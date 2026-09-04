"""Claim Attachments Upload — State persistence.

Stores batch progress on the filesystem so the loop is resumable
after interruption.  Each cycle re-reads this file from disk rather
than trusting in-memory state (Principle 6 — filesystem as memory).

Pattern follows core/add_claims_state.py.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


LOG_DIR = Path("logs")
STATE_FILE = LOG_DIR / "claim_attachments_state.json"


@dataclass
class AttachmentsState:
    """Persistent state for one Claim Attachments upload batch."""

    batch_id: str = ""
    total_patients: int = 0
    processed: int = 0
    current_patient: str = ""
    status: str = "pending"  # pending | in_progress | completed | failed
    failed: list[str] = field(default_factory=list)
    failed_reasons: dict[str, str] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""

    # -- serialisation ---------------------------------------------------

    def save(self, path: Path | None = None) -> Path:
        """Write state to disk.  Returns the path written."""
        target = path or STATE_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return target

    @classmethod
    def load(cls, path: Path | None = None) -> "AttachmentsState":
        """Read state from disk.  Returns a fresh state when file is missing."""
        target = path or STATE_FILE
        if not target.exists():
            return cls()
        try:
            data: dict[str, Any] = json.loads(
                target.read_text(encoding="utf-8")
            )
            return cls(
                **{k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            )
        except Exception:
            return cls()

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def new_batch_id() -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def mark_started(self, total: int) -> None:
        self.batch_id = self.new_batch_id()
        self.total_patients = total
        self.processed = 0
        self.status = "in_progress"
        self.started_at = datetime.now().isoformat()
        self.failed.clear()
        self.failed_reasons.clear()

    def mark_processed(self, patient_name: str) -> None:
        self.current_patient = patient_name
        self.processed += 1

    def mark_failed(self, patient_name: str, reason: str) -> None:
        self.failed.append(patient_name)
        self.failed_reasons[patient_name] = reason

    def mark_completed(self) -> None:
        self.status = "completed"
        self.finished_at = datetime.now().isoformat()
        self.current_patient = ""

    def mark_failed_batch(self) -> None:
        self.status = "failed"
        self.finished_at = datetime.now().isoformat()

    @property
    def is_done(self) -> bool:
        return self.processed >= self.total_patients

    @property
    def summary(self) -> str:
        lines = [
            f"Batch: {self.batch_id}",
            f"Status: {self.status}",
            f"Processed: {self.processed}/{self.total_patients}",
        ]
        if self.failed:
            lines.append(f"Failed: {len(self.failed)}")
            for name in self.failed:
                reason = self.failed_reasons.get(name, "unknown")
                lines.append(f"  - {name}: {reason}")
        return "\n".join(lines)


# -- standalone test ----------------------------------------------------

if __name__ == "__main__":
    state = AttachmentsState()
    state.mark_started(3)
    state.mark_processed("PATIENT ONE")
    state.mark_failed("PATIENT TWO", "ATTACH_FAILED")
    state.mark_completed()

    path = state.save()
    print(f"State saved to: {path}")
    print(state.summary)

    loaded = AttachmentsState.load()
    print("\nReloaded state:")
    print(loaded.summary)
