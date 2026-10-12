"""Minimal SQLite checkpoint store for the Claims Agent pipeline.

Separate file (``claims_pipeline.db``) from the review-queue database
(``claims.db``) so the pipeline never touches existing tables.

Persisted per claim (task section 6): stable claim key, input/output
paths, current + last-completed stage, per-stage status map, attempt
counts, XML validation JSON, checker status, HBSys JSON, verification
JSON, last error, final outcome, hold reason, manual action, retry
counts, start/last-update timestamps.

Resume contract:
    * stages at or below ``last_completed_stage`` are NOT rerun;
    * before any HBSys stage the agent re-verifies XML files on disk;
    * a non-idempotent HBSys stage recorded OK is skipped; recorded
      FAILED/uncertain is HELD (never blindly repeated).

Standalone test:
    python core/agent/pipeline_state.py
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    batch_id   TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'open',
    config_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS claims (
    batch_id            TEXT NOT NULL,
    claim_key           TEXT NOT NULL,
    folder_name         TEXT NOT NULL DEFAULT '',
    input_pdf           TEXT NOT NULL DEFAULT '',
    output_path         TEXT NOT NULL DEFAULT '',
    stage               TEXT NOT NULL DEFAULT 'PENDING',
    last_completed_stage TEXT NOT NULL DEFAULT '',
    stage_json          TEXT NOT NULL DEFAULT '{}',
    attempts            INTEGER NOT NULL DEFAULT 0,
    xml_json            TEXT NOT NULL DEFAULT '{}',
    checker_status      TEXT NOT NULL DEFAULT '',
    hbsys_json          TEXT NOT NULL DEFAULT '{}',
    verification_json   TEXT NOT NULL DEFAULT '{}',
    last_error          TEXT NOT NULL DEFAULT '',
    outcome             TEXT NOT NULL DEFAULT 'PENDING',
    hold_reason         TEXT NOT NULL DEFAULT '',
    manual_action       TEXT NOT NULL DEFAULT '',
    retries             INTEGER NOT NULL DEFAULT 0,
    started_at          TEXT NOT NULL DEFAULT '',
    updated_at          TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (batch_id, claim_key)
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class ClaimRecord:
    """One row of the claims table (JSON columns decoded to dicts)."""

    batch_id: str
    claim_key: str
    folder_name: str = ""
    input_pdf: str = ""
    output_path: str = ""
    stage: str = "PENDING"
    last_completed_stage: str = ""
    stage_status: dict = field(default_factory=dict)
    attempts: int = 0
    xml_result: dict = field(default_factory=dict)
    checker_status: str = ""
    hbsys: dict = field(default_factory=dict)
    verification: dict = field(default_factory=dict)
    last_error: str = ""
    outcome: str = "PENDING"
    hold_reason: str = ""
    manual_action: str = ""
    retries: int = 0
    started_at: str = ""
    updated_at: str = ""


class PipelineStore:
    """Tiny SQLite checkpoint store. No ORM, no framework.

    Opens a fresh connection per operation (and commits immediately) so
    checkpoints survive a crash and no file handle is held between calls.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _session(self):
        """One short-lived connection per operation (always closed)."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def close(self) -> None:
        """No persistent handle to close (kept for API compatibility)."""

    # -- batches ---------------------------------------------------------

    def open_batch(self, batch_id: str, config_json: str = "{}") -> None:
        with self._session() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO batches (batch_id, started_at, config_json)"
                " VALUES (?, ?, ?)",
                (batch_id, _now(), config_json),
            )

    def close_batch(self, batch_id: str, status: str) -> None:
        with self._session() as conn:
            conn.execute(
                "UPDATE batches SET finished_at = ?, status = ? WHERE batch_id = ?",
                (_now(), status, batch_id),
            )

    def latest_open_batch(self) -> str:
        with self._session() as conn:
            row = conn.execute(
                "SELECT batch_id FROM batches WHERE status = 'open'"
                " ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return row["batch_id"] if row else ""

    # -- claims ----------------------------------------------------------

    def upsert_claim(self, record: ClaimRecord) -> None:
        record.updated_at = _now()
        if not record.started_at:
            record.started_at = record.updated_at
        with self._session() as conn:
            conn.execute(
            """INSERT INTO claims
               (batch_id, claim_key, folder_name, input_pdf, output_path,
                stage, last_completed_stage, stage_json, attempts, xml_json,
                checker_status, hbsys_json, verification_json, last_error,
                outcome, hold_reason, manual_action, retries,
                started_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (batch_id, claim_key) DO UPDATE SET
                folder_name=excluded.folder_name,
                input_pdf=excluded.input_pdf,
                output_path=excluded.output_path,
                stage=excluded.stage,
                last_completed_stage=excluded.last_completed_stage,
                stage_json=excluded.stage_json,
                attempts=excluded.attempts,
                xml_json=excluded.xml_json,
                checker_status=excluded.checker_status,
                hbsys_json=excluded.hbsys_json,
                verification_json=excluded.verification_json,
                last_error=excluded.last_error,
                outcome=excluded.outcome,
                hold_reason=excluded.hold_reason,
                manual_action=excluded.manual_action,
                retries=excluded.retries,
                updated_at=excluded.updated_at""",
            (
                record.batch_id,
                record.claim_key,
                record.folder_name,
                record.input_pdf,
                record.output_path,
                record.stage,
                record.last_completed_stage,
                json.dumps(record.stage_status),
                record.attempts,
                json.dumps(record.xml_result),
                record.checker_status,
                json.dumps(record.hbsys),
                json.dumps(record.verification),
                record.last_error,
                record.outcome,
                record.hold_reason,
                record.manual_action,
                record.retries,
                record.started_at,
                record.updated_at,
            ),
        )

    def _row_to_record(self, row: sqlite3.Row) -> ClaimRecord:
        data = dict(row)
        for key in ("stage_status", "xml_result", "hbsys", "verification"):
            column = {
                "stage_status": "stage_json",
                "xml_result": "xml_json",
                "hbsys": "hbsys_json",
                "verification": "verification_json",
            }[key]
            try:
                data[key] = json.loads(data.pop(column) or "{}")
            except ValueError:
                data[key] = {}
        return ClaimRecord(**data)

    def get_claim(self, batch_id: str, claim_key: str) -> ClaimRecord | None:
        with self._session() as conn:
            row = conn.execute(
                "SELECT * FROM claims WHERE batch_id = ? AND claim_key = ?",
                (batch_id, claim_key),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list_claims(self, batch_id: str) -> list[ClaimRecord]:
        with self._session() as conn:
            rows = conn.execute(
                "SELECT * FROM claims WHERE batch_id = ? ORDER BY claim_key",
                (batch_id,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def claim_keys(self, batch_id: str) -> set[str]:
        with self._session() as conn:
            rows = conn.execute(
                "SELECT claim_key FROM claims WHERE batch_id = ?", (batch_id,)
            ).fetchall()
        return {row["claim_key"] for row in rows}


if __name__ == "__main__":
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmp:
        store = PipelineStore(Path(tmp) / "test.db")
        store.open_batch("B1")
        rec = ClaimRecord(batch_id="B1", claim_key="K1", stage="FEES_CHECK_1")
        store.upsert_claim(rec)
        assert store.get_claim("B1", "K1").stage == "FEES_CHECK_1"
        assert store.latest_open_batch() == "B1"
        store.close_batch("B1", "finished")
        assert store.latest_open_batch() == ""
        store.close()
    print("pipeline_state standalone check passed")
