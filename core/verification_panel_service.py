"""Read-only data provider for the GUI Verification Panel."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.activity_logger import ActivityLogger, logger
from core.patient_review_queue import PatientReviewQueue, ReviewItem, ReviewStatus, review_queue


@dataclass(frozen=True, slots=True)
class FolderSnapshot:
    path: str
    pdf_count: int
    folder_count: int


@dataclass(frozen=True, slots=True)
class RecentFolder:
    name: str
    path: str
    modified: str
    pdf_count: int


@dataclass(frozen=True, slots=True)
class VerificationSnapshot:
    scan: FolderSnapshot
    output: FolderSnapshot
    backup: FolderSnapshot
    review_counts: dict[str, int]
    active_reviews: tuple[ReviewItem, ...]
    ready_to_resume: tuple[ReviewItem, ...]
    recent_outputs: tuple[RecentFolder, ...]


class VerificationPanelService:
    """Collect production verification facts without mutating files or databases."""

    def __init__(
        self,
        queue: PatientReviewQueue = review_queue,
        activity_logger: ActivityLogger = logger,
    ) -> None:
        self.queue = queue
        self.logger = activity_logger

    def snapshot(
        self,
        *,
        scan_folder: str | Path,
        output_folder: str | Path,
        backup_folder: str | Path,
        limit: int = 25,
    ) -> VerificationSnapshot:
        all_reviews = self.queue.list_items(limit=None)
        counts = {status.value: 0 for status in ReviewStatus}
        for item in all_reviews:
            counts[item.status] = counts.get(item.status, 0) + 1

        active = tuple(
            item
            for item in all_reviews
            if item.status in {ReviewStatus.PENDING.value, ReviewStatus.IN_REVIEW.value}
        )[:limit]
        ready = tuple(
            item for item in all_reviews if item.status == ReviewStatus.RESOLVED.value
        )[:limit]

        return VerificationSnapshot(
            scan=self._folder_snapshot(scan_folder),
            output=self._folder_snapshot(output_folder),
            backup=self._folder_snapshot(backup_folder),
            review_counts=counts,
            active_reviews=active,
            ready_to_resume=ready,
            recent_outputs=self._recent_folders(output_folder, limit=limit),
        )

    @staticmethod
    def _folder_snapshot(path: str | Path) -> FolderSnapshot:
        folder = Path(path)
        if not folder.is_dir():
            return FolderSnapshot(str(folder), 0, 0)
        pdf_count = sum(1 for item in folder.rglob("*.pdf") if item.is_file())
        folder_count = sum(1 for item in folder.iterdir() if item.is_dir())
        return FolderSnapshot(str(folder), pdf_count, folder_count)

    @staticmethod
    def _recent_folders(path: str | Path, limit: int) -> tuple[RecentFolder, ...]:
        folder = Path(path)
        if not folder.is_dir():
            return ()
        candidates = sorted(
            (item for item in folder.iterdir() if item.is_dir()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        recent: list[RecentFolder] = []
        for item in candidates[:limit]:
            modified = item.stat().st_mtime
            recent.append(
                RecentFolder(
                    name=item.name,
                    path=str(item),
                    modified=VerificationPanelService._format_mtime(modified),
                    pdf_count=sum(1 for pdf in item.rglob("*.pdf") if pdf.is_file()),
                )
            )
        return tuple(recent)

    @staticmethod
    def _format_mtime(value: float) -> str:
        from datetime import datetime

        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %I:%M:%S %p")


verification_panel_service = VerificationPanelService()


if __name__ == "__main__":
    base = Path(__file__).resolve().parent.parent
    snapshot = verification_panel_service.snapshot(
        scan_folder=base / "scans",
        output_folder=base / "output",
        backup_folder=base / "backup_originals",
        limit=5,
    )
    assert snapshot.scan.path
    logger.success("Verification Panel service standalone test passed")
