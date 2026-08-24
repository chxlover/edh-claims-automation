"""Launch isolated processing for one corrected Review Queue patient."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from core.admission_date_resolver import resolve_admission_dates_from_metadata
from core.admission_lookup import MySQLAdmissionLookup
from core.activity_logger import ActivityLogger, logger
from core.hbsys_connection import create_hbsys_connection
from core.patient_review_queue import PatientReviewQueue, ReviewItem, review_queue


BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSOR = BASE_DIR / "bot_unknown_trainer_DEFERRED_OUTPUT_REVIEW_PATSUFFIX_ADM_DIS.py"


class ResumeManager:
    def __init__(
        self,
        queue: PatientReviewQueue = review_queue,
        activity_logger: ActivityLogger = logger,
        processor: Path = PROCESSOR,
    ) -> None:
        self.queue = queue
        self.logger = activity_logger
        self.processor = processor

    def validate(self, review_id: int) -> tuple[Path, ...]:
        item = self.queue.get(review_id)
        if item is None:
            raise LookupError(f"Review item {review_id} does not exist")
        if item.status != "RESOLVED":
            raise ValueError("Only RESOLVED review items can be resumed")
        item = self._auto_heal_missing_admission(review_id, item)
        if not item.hospital_no or not item.patient_name or not item.encounter_no:
            raise ValueError("Corrected patient identity and admission are required")
        documents = tuple(Path(document) for document in item.documents)
        if not documents:
            raise ValueError("Review item has no source documents")
        missing = tuple(path for path in documents if not path.is_file())
        if missing:
            documents = self._recover_from_backup(review_id, item, documents)
        if not self.processor.is_file():
            raise FileNotFoundError(f"Claims processor is unavailable: {self.processor}")
        return documents

    def _recover_from_backup(
        self,
        review_id: int,
        item: ReviewItem,
        documents: tuple[Path, ...],
    ) -> tuple[Path, ...]:
        backup_root = Path(
            os.getenv("CLAIMS_BACKUP_FOLDER", str(BASE_DIR / "backup_originals"))
        )
        staging_root = Path(
            os.getenv("CLAIMS_RESUME_STAGING", str(BASE_DIR / "resume_staging"))
        ) / f"review_{review_id}"
        recovered: list[Path] = []
        for document in documents:
            if document.is_file():
                source = document
            else:
                candidates = tuple(backup_root.rglob(document.name))
                patient_candidates = tuple(
                    candidate
                    for candidate in candidates
                    if item.hospital_no in str(candidate.parent)
                    or item.patient_name.upper() in str(candidate.parent).upper()
                )
                if patient_candidates:
                    candidates = patient_candidates
                if len(candidates) != 1:
                    raise FileNotFoundError(
                        f"Source document {document.name} is unavailable and "
                        f"backup match count is {len(candidates)}"
                    )
                source = candidates[0]
            staging_root.mkdir(parents=True, exist_ok=True)
            destination = staging_root / document.name
            if destination.exists():
                destination = staging_root / f"{document.stem}_copy{document.suffix}"
            shutil.copy2(source, destination)
            recovered.append(destination)
        result = tuple(recovered)
        self.queue.replace_document_paths(review_id, result)
        self.logger.warning(
            f"Resume sources restored from backup copies: review_id={review_id}"
        )
        return result

    def resume(self, review_id: int) -> subprocess.Popen[str]:
        self.validate(review_id)
        if not self.queue.start_resume(review_id):
            raise RuntimeError("Could not reserve review item for resume")
        environment = os.environ.copy()
        environment["CLAIMS_REVIEW_ID"] = str(review_id)
        environment["CLAIMS_GUI_MODE"] = "1"
        try:
            process = subprocess.Popen(
                [sys.executable, str(self.processor)],
                cwd=BASE_DIR,
                env=environment,
                stdin=subprocess.DEVNULL,
                text=True,
            )
        except Exception as exc:
            self.queue.fail_resume(review_id, f"Resume launch failed: {exc}")
            raise
        self.logger.info(
            f"Resume Processing launched: review_id={review_id} pid={process.pid}"
        )
        return process

    def _auto_heal_missing_admission(
        self, review_id: int, item: ReviewItem
    ) -> ReviewItem:
        """Fill old RESOLVED H003/H007 items that lack admission fields.

        Older GUI versions could mark an H003 item RESOLVED without encounter
        details. Before failing Resume Processing, try the same safe metadata
        fallback used by the main processor.
        """
        if item.encounter_no and item.admission_date and item.discharge_date:
            return item
        reason_codes = set(re.findall(r"H\d{3}", item.reason_detail))
        reason_codes.add(item.reason)
        if not reason_codes.intersection({"H003", "H007"}) or not item.hospital_no:
            return item

        metadata_dates = resolve_admission_dates_from_metadata(
            hospital_no=item.hospital_no,
            candidate_paths=(item.folder, *item.documents),
            backup_root=os.getenv("CLAIMS_BACKUP_FOLDER", str(BASE_DIR / "backup_originals")),
        )
        if not metadata_dates.found:
            return item

        admission_match = MySQLAdmissionLookup(create_hbsys_connection).match(
            item.hospital_no,
            metadata_dates.admission_date,
            metadata_dates.discharge_date,
        )
        if admission_match.selected is None:
            return item

        note = (
            "Auto-filled missing admission before resume using "
            f"{metadata_dates.source}: "
            f"ADM{metadata_dates.admission_date}_DIS{metadata_dates.discharge_date}"
        )
        changed = self.queue.apply_patient_correction(
            review_id,
            hospital_no=item.hospital_no,
            patient_name=item.patient_name,
            encounter_no=admission_match.selected.encounter_no,
            admission_date=admission_match.selected.admission_date,
            discharge_date=admission_match.selected.discharge_date,
            corrected_by="AUTO_RESUME_ADMISSION_HEAL",
            note=note,
            remaining_detail="",
        )
        if changed:
            self.logger.info(f"Review Queue admission auto-healed: id={review_id}")
            healed = self.queue.get(review_id)
            return healed if healed is not None else item
        return item


resume_manager = ResumeManager()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Resume one corrected claim patient")
    parser.add_argument("review_id", type=int)
    arguments = parser.parse_args()
    resume_manager.resume(arguments.review_id)
