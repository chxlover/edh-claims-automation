"""SQLite persistence for local claims metadata.

This database is deliberately separate from the read-only HBSys MySQL database.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any, Iterable


BASE_DIR = Path(__file__).resolve().parent.parent
DB_FILE = Path(os.getenv("CLAIMS_SQLITE_DB", str(BASE_DIR / "claims.db")))


class DatabaseManager:
    """Own SQLite connections and queries; this layer never displays a GUI."""

    def __init__(self, db_file: str | Path = DB_FILE) -> None:
        self.db_file = Path(db_file)
        self._lock = RLock()
        self.conn = sqlite3.connect(self.db_file, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.create_tables()

    def create_tables(self) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS review_queue(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hospital_no TEXT,
                    patient_name TEXT,
                    admission_date TEXT,
                    discharge_date TEXT,
                    encounter_no TEXT,
                    reason TEXT,
                    confidence INTEGER,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    folder TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processing_logs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    log_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                    level TEXT,
                    message TEXT
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings(
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS review_corrections(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    review_id INTEGER NOT NULL,
                    old_hospital_no TEXT NOT NULL DEFAULT '',
                    new_hospital_no TEXT NOT NULL DEFAULT '',
                    old_patient_name TEXT NOT NULL DEFAULT '',
                    new_patient_name TEXT NOT NULL DEFAULT '',
                    encounter_no TEXT NOT NULL DEFAULT '',
                    corrected_by TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    corrected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(review_id) REFERENCES review_queue(id)
                )
                """
            )
            self._migrate_review_queue()
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_review_queue_status_created
                ON review_queue(status, created_at, id)
                """
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_review_queue_encounter
                ON review_queue(encounter_no)
                """
            )

    def _migrate_review_queue(self) -> None:
        """Add v2 fields without replacing or deleting an existing queue."""
        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(review_queue)")
        }
        additions = {
            "reason_detail": "TEXT NOT NULL DEFAULT ''",
            "documents_json": "TEXT NOT NULL DEFAULT '[]'",
            "batch_id": "TEXT NOT NULL DEFAULT ''",
            "source_key": "TEXT NOT NULL DEFAULT ''",
            "resolution_note": "TEXT NOT NULL DEFAULT ''",
            "resolved_by": "TEXT NOT NULL DEFAULT ''",
            "resolved_at": "DATETIME",
            "updated_at": "DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP",
        }
        for name, definition in additions.items():
            if name not in columns:
                self.conn.execute(
                    f"ALTER TABLE review_queue ADD COLUMN {name} {definition}"
                )

    def add_review_patient(self, **values: Any) -> int:
        fields = (
            "hospital_no", "patient_name", "admission_date", "discharge_date",
            "encounter_no", "reason", "reason_detail", "confidence", "folder",
            "documents_json", "batch_id", "source_key",
        )
        payload = {field: values.get(field, "") for field in fields}
        payload["confidence"] = values.get("confidence", 0)
        with self._lock, self.conn:
            cursor = self.conn.execute(
                f"INSERT INTO review_queue ({', '.join(fields)}) "
                f"VALUES ({', '.join('?' for _ in fields)})",
                tuple(payload[field] for field in fields),
            )
            return int(cursor.lastrowid)

    def find_active_review(self, source_key: str) -> sqlite3.Row | None:
        if not source_key:
            return None
        return self.conn.execute(
            """
            SELECT * FROM review_queue
            WHERE source_key = ? AND status IN ('PENDING', 'IN_REVIEW')
            ORDER BY id DESC LIMIT 1
            """,
            (source_key,),
        ).fetchone()

    def get_review(self, review_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM review_queue WHERE id = ?", (review_id,)
        ).fetchone()

    def get_reviews(
        self, statuses: Iterable[str] | None = None, limit: int | None = None
    ) -> list[sqlite3.Row]:
        params: list[Any] = []
        query = "SELECT * FROM review_queue"
        if statuses:
            normalized = list(statuses)
            query += f" WHERE status IN ({', '.join('?' for _ in normalized)})"
            params.extend(normalized)
        query += " ORDER BY created_at, id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        return list(self.conn.execute(query, params).fetchall())

    def get_pending_reviews(self) -> list[sqlite3.Row]:
        return self.get_reviews(("PENDING",))

    def update_review_status(
        self,
        review_id: int,
        status: str,
        resolution_note: str = "",
        resolved_by: str = "",
    ) -> bool:
        resolved = status in {"RESOLVED", "SKIPPED", "COMPLETED"}
        with self._lock, self.conn:
            cursor = self.conn.execute(
                """
                UPDATE review_queue
                SET status = ?, resolution_note = ?, resolved_by = ?,
                    resolved_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, resolution_note, resolved_by, resolved, review_id),
            )
            return cursor.rowcount == 1

    def apply_patient_correction(
        self,
        review_id: int,
        *,
        hospital_no: str,
        patient_name: str,
        encounter_no: str,
        admission_date: str,
        discharge_date: str,
        corrected_by: str,
        note: str,
        remaining_detail: str,
    ) -> bool:
        """Update identity and append an immutable manual-correction audit row."""
        with self._lock, self.conn:
            current = self.conn.execute(
                "SELECT hospital_no, patient_name FROM review_queue WHERE id = ?",
                (review_id,),
            ).fetchone()
            if current is None:
                return False
            self.conn.execute(
                """
                INSERT INTO review_corrections(
                    review_id, old_hospital_no, new_hospital_no,
                    old_patient_name, new_patient_name, encounter_no,
                    corrected_by, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id, current["hospital_no"] or "", hospital_no,
                    current["patient_name"] or "", patient_name, encounter_no,
                    corrected_by, note,
                ),
            )
            cursor = self.conn.execute(
                """
                UPDATE review_queue
                SET hospital_no = ?, patient_name = ?, encounter_no = ?,
                    admission_date = ?, discharge_date = ?,
                    reason_detail = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    hospital_no, patient_name, encounter_no, admission_date,
                    discharge_date, remaining_detail, review_id,
                ),
            )
            return cursor.rowcount == 1

    def get_review_corrections(self, review_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT * FROM review_corrections
                WHERE review_id = ? ORDER BY corrected_at, id
                """,
                (review_id,),
            ).fetchall()
        )

    def update_review_documents(self, review_id: int, documents_json: str) -> bool:
        with self._lock, self.conn:
            cursor = self.conn.execute(
                """
                UPDATE review_queue
                SET documents_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (documents_json, review_id),
            )
            return cursor.rowcount == 1

    def update_review_location(
        self,
        review_id: int,
        folder: str,
        documents_json: str,
    ) -> bool:
        with self._lock, self.conn:
            cursor = self.conn.execute(
                """
                UPDATE review_queue
                SET folder = ?, documents_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (folder, documents_json, review_id),
            )
            return cursor.rowcount == 1

    def mark_review_completed(self, review_id: int) -> bool:
        return self.update_review_status(review_id, "COMPLETED")

    def log(self, level: str, message: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO processing_logs(level, message) VALUES(?, ?)",
                (level, message),
            )

    def info(self, message: str) -> None:
        self.log("INFO", message)

    def warning(self, message: str) -> None:
        self.log("WARNING", message)

    def error(self, message: str) -> None:
        self.log("ERROR", message)

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: object) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(value)),
            )

    def close(self) -> None:
        with self._lock:
            self.conn.close()


db = DatabaseManager()


if __name__ == "__main__":
    from core.activity_logger import logger

    logger.info(f"SQLite database ready: {DB_FILE}")
