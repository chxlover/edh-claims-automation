"""Move Review Queue source PDFs out of the live scans folder.

The Review Queue stores exact source document paths. When a patient group is
deferred, those PDFs must not remain in the live scans folder because future
auto-processing could accidentally read them again with newly scanned patients.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable

from core.activity_logger import ActivityLogger, logger
from core.patient_review_queue import PatientReviewQueue, review_queue


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REVIEW_STAGING = BASE_DIR / "review_staging"


def unique_destination(folder: Path, filename: str) -> Path:
    destination = folder / filename
    if not destination.exists():
        return destination

    stem = destination.stem
    suffix = destination.suffix
    counter = 1
    while True:
        candidate = folder / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


class ReviewStagingManager:
    def __init__(
        self,
        queue: PatientReviewQueue = review_queue,
        activity_logger: ActivityLogger = logger,
        staging_root: str | Path | None = None,
    ) -> None:
        self.queue = queue
        self.logger = activity_logger
        root = staging_root or os.getenv(
            "CLAIMS_REVIEW_STAGING_FOLDER",
            str(DEFAULT_REVIEW_STAGING),
        )
        self.staging_root = Path(root)

    def stage_documents(
        self,
        review_id: int,
        documents: Iterable[str | Path],
    ) -> tuple[Path, ...]:
        target_folder = self.staging_root / f"RQ-{review_id:06d}"
        target_folder.mkdir(parents=True, exist_ok=True)

        staged: list[Path] = []
        for document in documents:
            source = Path(document)
            if not source.is_file():
                raise FileNotFoundError(f"Review source document is missing: {source}")

            try:
                if source.resolve().parent == target_folder.resolve():
                    staged.append(source)
                    continue
            except OSError:
                pass

            destination = unique_destination(target_folder, source.name)
            shutil.move(str(source), str(destination))
            staged.append(destination)

        result = tuple(staged)
        self.queue.replace_review_location(review_id, target_folder, result)
        self.logger.warning(
            f"Review sources staged: review_id={review_id} "
            f"folder={target_folder} files={len(result)}"
        )
        return result


review_staging = ReviewStagingManager()


if __name__ == "__main__":
    print(f"Review staging root: {ReviewStagingManager().staging_root}")
