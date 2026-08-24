"""Patient Review Queue service.

This module accepts validation failures; it does not perform OCR or display GUI.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

from core.activity_logger import ActivityLogger, logger
from core.claims_database import DatabaseManager, db


class ReviewReason(StrEnum):
    HOSPITAL_NUMBER_NOT_FOUND = "H001"
    OCR_FAILED = "H002"
    MULTIPLE_ADMISSIONS = "H003"
    PATIENT_NAME_MISMATCH = "H004"
    MISSING_SOA1 = "H005"
    MISSING_COE = "H006"
    ADMISSION_NOT_SELECTED = "H007"
    MANUAL_OVERRIDE = "H008"
    DUPLICATE_HOSPITAL_NUMBER = "H009"
    DUPLICATE_ENCOUNTER = "H010"


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED = "RESOLVED"
    SKIPPED = "SKIPPED"
    RESUMING = "RESUMING"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True, slots=True)
class ReviewItem:
    id: int
    hospital_no: str
    patient_name: str
    admission_date: str
    discharge_date: str
    encounter_no: str
    reason: str
    reason_detail: str
    confidence: int
    status: str
    folder: str
    documents: tuple[str, ...]
    batch_id: str
    source_key: str
    resolution_note: str
    resolved_by: str
    created_at: str
    updated_at: str
    resolved_at: str | None


class PatientReviewQueue:
    """Coordinate review records while keeping SQL in the database layer."""

    def __init__(
        self,
        database: DatabaseManager = db,
        activity_logger: ActivityLogger = logger,
    ) -> None:
        self.database = database
        self.logger = activity_logger

    def add_patient(
        self,
        reason: ReviewReason | str,
        *,
        hospital_no: str = "",
        patient_name: str = "",
        admission_date: str = "",
        discharge_date: str = "",
        encounter_no: str = "",
        confidence: int = 0,
        folder: str | Path = "",
        documents: Iterable[str | Path] = (),
        reason_detail: str = "",
        batch_id: str = "",
        source_key: str = "",
    ) -> int:
        reason_code = self._reason_code(reason)
        confidence = max(0, min(100, int(confidence)))
        document_list = tuple(str(Path(item)) for item in documents)
        source_key = source_key.strip() or self._build_source_key(
            reason_code, encounter_no, hospital_no, admission_date, folder,
            document_list,
        )

        existing = self.database.find_active_review(source_key)
        if existing:
            self.logger.warning(
                f"Review Queue duplicate ignored: id={existing['id']} "
                f"reason={reason_code}"
            )
            return int(existing["id"])

        review_id = self.database.add_review_patient(
            hospital_no=hospital_no.strip(),
            patient_name=patient_name.strip(),
            admission_date=admission_date.strip(),
            discharge_date=discharge_date.strip(),
            encounter_no=encounter_no.strip(),
            reason=reason_code,
            reason_detail=reason_detail.strip(),
            confidence=confidence,
            folder=str(folder),
            documents_json=json.dumps(document_list, ensure_ascii=False),
            batch_id=batch_id.strip(),
            source_key=source_key,
        )
        self.logger.warning(
            f"Review Queue added: id={review_id} reason={reason_code} "
            f"hospital_no={hospital_no or 'UNKNOWN'}"
        )
        return review_id

    def get(self, review_id: int) -> ReviewItem | None:
        row = self.database.get_review(review_id)
        return self._to_item(row) if row else None

    def get_pending(self, limit: int | None = None) -> list[ReviewItem]:
        rows = self.database.get_reviews(
            (ReviewStatus.PENDING.value,), limit=limit
        )
        return [self._to_item(row) for row in rows]

    def list_items(
        self,
        statuses: Iterable[ReviewStatus | str] | None = None,
        limit: int | None = None,
    ) -> list[ReviewItem]:
        values = None if statuses is None else tuple(str(status) for status in statuses)
        return [
            self._to_item(row)
            for row in self.database.get_reviews(values, limit=limit)
        ]

    def start_review(self, review_id: int) -> bool:
        return self._change_status(
            review_id,
            ReviewStatus.IN_REVIEW,
            allowed_from=(ReviewStatus.PENDING,),
        )

    def resolve(
        self, review_id: int, resolution_note: str, resolved_by: str
    ) -> bool:
        if not resolution_note.strip():
            raise ValueError("resolution_note is required")
        if not resolved_by.strip():
            raise ValueError("resolved_by is required")
        return self._change_status(
            review_id,
            ReviewStatus.RESOLVED,
            resolution_note,
            resolved_by,
            allowed_from=(ReviewStatus.PENDING, ReviewStatus.IN_REVIEW),
        )

    def skip(self, review_id: int, note: str, resolved_by: str) -> bool:
        if not note.strip() or not resolved_by.strip():
            raise ValueError("note and resolved_by are required")
        return self._change_status(
            review_id,
            ReviewStatus.SKIPPED,
            note,
            resolved_by,
            allowed_from=(ReviewStatus.PENDING, ReviewStatus.IN_REVIEW),
        )

    def reopen(self, review_id: int) -> bool:
        return self._change_status(
            review_id,
            ReviewStatus.PENDING,
            allowed_from=(ReviewStatus.RESOLVED, ReviewStatus.SKIPPED),
        )

    def start_resume(self, review_id: int) -> bool:
        return self._change_status(
            review_id,
            ReviewStatus.RESUMING,
            allowed_from=(ReviewStatus.RESOLVED,),
        )

    def complete_resume(self, review_id: int) -> bool:
        return self._change_status(
            review_id,
            ReviewStatus.COMPLETED,
            allowed_from=(ReviewStatus.RESUMING,),
        )

    def fail_resume(self, review_id: int, message: str) -> bool:
        return self._change_status(
            review_id,
            ReviewStatus.RESOLVED,
            message,
            allowed_from=(ReviewStatus.RESUMING,),
        )

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
        if not corrected_by.strip() or not note.strip():
            raise ValueError("corrected_by and note are required")
        changed = self.database.apply_patient_correction(
            review_id,
            hospital_no=hospital_no,
            patient_name=patient_name,
            encounter_no=encounter_no,
            admission_date=admission_date,
            discharge_date=discharge_date,
            corrected_by=corrected_by.strip(),
            note=note.strip(),
            remaining_detail=remaining_detail,
        )
        if changed:
            self.logger.info(
                f"Review Queue patient corrected: id={review_id} "
                f"hospital_no={hospital_no}"
            )
        return changed

    def replace_document_paths(
        self, review_id: int, documents: Iterable[str | Path]
    ) -> bool:
        paths = tuple(str(Path(document)) for document in documents)
        if not paths:
            raise ValueError("At least one document path is required")
        changed = self.database.update_review_documents(
            review_id, json.dumps(paths, ensure_ascii=False)
        )
        if changed:
            self.logger.info(
                f"Review Queue document paths recovered: id={review_id} "
                f"files={len(paths)}"
            )
        return changed

    def replace_review_location(
        self,
        review_id: int,
        folder: str | Path,
        documents: Iterable[str | Path],
    ) -> bool:
        paths = tuple(str(Path(document)) for document in documents)
        if not paths:
            raise ValueError("At least one document path is required")
        changed = self.database.update_review_location(
            review_id,
            str(Path(folder)),
            json.dumps(paths, ensure_ascii=False),
        )
        if changed:
            self.logger.info(
                f"Review Queue source location updated: id={review_id} "
                f"folder={folder} files={len(paths)}"
            )
        return changed

    def _change_status(
        self,
        review_id: int,
        status: ReviewStatus,
        note: str = "",
        user: str = "",
        allowed_from: tuple[ReviewStatus, ...] = (),
    ) -> bool:
        row = self.database.get_review(review_id)
        if row is None:
            raise LookupError(f"Review item {review_id} does not exist")
        current_status = str(row["status"])
        if allowed_from and current_status not in {
            allowed.value for allowed in allowed_from
        }:
            raise ValueError(
                f"Cannot change review {review_id} from {current_status} "
                f"to {status.value}"
            )
        changed = self.database.update_review_status(
            review_id, status.value, note.strip(), user.strip()
        )
        if changed:
            self.logger.info(
                f"Review Queue status changed: id={review_id} status={status.value}"
            )
        return changed

    @staticmethod
    def _reason_code(reason: ReviewReason | str) -> str:
        try:
            return ReviewReason(str(reason)).value
        except ValueError as exc:
            valid = ", ".join(item.value for item in ReviewReason)
            raise ValueError(f"Unknown review reason. Expected one of: {valid}") from exc

    @staticmethod
    def _build_source_key(
        reason: str,
        encounter_no: str,
        hospital_no: str,
        admission_date: str,
        folder: str | Path,
        documents: tuple[str, ...],
    ) -> str:
        identity = "|".join(
            (reason, encounter_no.strip(), hospital_no.strip(),
             admission_date.strip(), str(folder), *documents)
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @staticmethod
    def _to_item(row: Any) -> ReviewItem:
        try:
            documents = tuple(json.loads(row["documents_json"] or "[]"))
        except (TypeError, json.JSONDecodeError):
            documents = ()
        return ReviewItem(
            id=int(row["id"]),
            hospital_no=row["hospital_no"] or "",
            patient_name=row["patient_name"] or "",
            admission_date=row["admission_date"] or "",
            discharge_date=row["discharge_date"] or "",
            encounter_no=row["encounter_no"] or "",
            reason=row["reason"] or "",
            reason_detail=row["reason_detail"] or "",
            confidence=int(row["confidence"] or 0),
            status=row["status"],
            folder=row["folder"] or "",
            documents=documents,
            batch_id=row["batch_id"] or "",
            source_key=row["source_key"] or "",
            resolution_note=row["resolution_note"] or "",
            resolved_by=row["resolved_by"] or "",
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            resolved_at=str(row["resolved_at"]) if row["resolved_at"] else None,
        )


review_queue = PatientReviewQueue()


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        test_db = DatabaseManager(Path(directory) / "review_queue_test.db")
        queue = PatientReviewQueue(test_db, logger)
        item_id = queue.add_patient(
            ReviewReason.OCR_FAILED,
            patient_name="STANDALONE TEST",
            documents=("sample.pdf",),
            reason_detail="Module self-test",
        )
        assert queue.get(item_id) is not None
        assert queue.start_review(item_id)
        assert queue.resolve(item_id, "Verified manually", "SELF_TEST")
        assert queue.get(item_id).status == ReviewStatus.RESOLVED
        test_db.close()
        logger.success("Patient Review Queue standalone test passed")
