"""Read-only audit of historical patient backup folders."""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PyPDF2 import PdfReader

from core.activity_logger import ActivityLogger, logger


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_BACKUP_ROOT = BASE_DIR / "backup_originals"
DEFAULT_REPORT_DIR = BASE_DIR / "reports"
HOSPITAL_PATTERN = re.compile(r"(?<!\d)(\d{15})(?!\d)")
ADMISSION_PATTERN = re.compile(r"ADM(\d{8})", re.IGNORECASE)
DISCHARGE_PATTERN = re.compile(r"DIS(\d{8})", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class BackupAuditRecord:
    folder: str
    patient_name: str
    hospital_no: str
    admission_date: str
    discharge_date: str
    pdf_count: int
    total_bytes: int
    unreadable_pdfs: int
    issues: tuple[str, ...]

    @property
    def needs_review(self) -> bool:
        return bool(self.issues)


class BackupAuditor:
    """Inspect metadata and PDF structure without modifying source files."""

    def __init__(
        self,
        backup_root: str | Path = DEFAULT_BACKUP_ROOT,
        activity_logger: ActivityLogger = logger,
        minimum_document_count: int = 3,
    ) -> None:
        self.backup_root = Path(backup_root)
        self.logger = activity_logger
        self.minimum_document_count = minimum_document_count

    def audit(self) -> list[BackupAuditRecord]:
        if not self.backup_root.is_dir():
            raise FileNotFoundError(f"Backup folder not found: {self.backup_root}")
        records = [
            self._inspect_folder(folder)
            for folder in sorted(self.backup_root.iterdir(), key=lambda path: path.name)
            if folder.is_dir() and not folder.name.startswith("working_reference_")
        ]
        records = self._add_duplicate_issues(records)
        self.logger.info(
            f"Backup audit completed: folders={len(records)} "
            f"review={sum(record.needs_review for record in records)}"
        )
        return records

    def write_csv(
        self,
        records: Iterable[BackupAuditRecord],
        report_path: str | Path | None = None,
    ) -> Path:
        if report_path is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = DEFAULT_REPORT_DIR / f"backup_audit_{stamp}.csv"
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                (
                    "needs_review", "issues", "patient_name", "hospital_no",
                    "admission_date", "discharge_date", "pdf_count",
                    "unreadable_pdfs", "total_bytes", "folder",
                )
            )
            for record in records:
                writer.writerow(
                    (
                        "YES" if record.needs_review else "NO",
                        "; ".join(record.issues), record.patient_name,
                        record.hospital_no, record.admission_date,
                        record.discharge_date, record.pdf_count,
                        record.unreadable_pdfs, record.total_bytes, record.folder,
                    )
                )
        self.logger.success(f"Backup audit report created: {path}")
        return path

    def _inspect_folder(self, folder: Path) -> BackupAuditRecord:
        hospital_match = HOSPITAL_PATTERN.search(folder.name)
        admission_match = ADMISSION_PATTERN.search(folder.name)
        discharge_match = DISCHARGE_PATTERN.search(folder.name)
        hospital_no = hospital_match.group(1) if hospital_match else ""
        patient_name = folder.name.split(" - ", 1)[0].strip()
        pdfs = tuple(sorted(folder.glob("*.pdf")))
        issues: list[str] = []
        if not hospital_no:
            issues.append("MISSING_OR_INVALID_HOSPITAL_NO")
        if not patient_name or patient_name.upper().startswith("UNKNOWN"):
            issues.append("MISSING_OR_UNKNOWN_PATIENT_NAME")
        if not admission_match or not discharge_match:
            issues.append("MISSING_ADMISSION_OR_DISCHARGE_DATE")
        if not pdfs:
            issues.append("NO_PDF_DOCUMENTS")
        elif len(pdfs) < self.minimum_document_count:
            issues.append("LOW_DOCUMENT_COUNT")
        unreadable = sum(not self._is_readable_pdf(pdf) for pdf in pdfs)
        if unreadable:
            issues.append("UNREADABLE_PDF")
        return BackupAuditRecord(
            folder=str(folder), patient_name=patient_name,
            hospital_no=hospital_no,
            admission_date=admission_match.group(1) if admission_match else "",
            discharge_date=discharge_match.group(1) if discharge_match else "",
            pdf_count=len(pdfs), total_bytes=sum(pdf.stat().st_size for pdf in pdfs),
            unreadable_pdfs=unreadable, issues=tuple(issues),
        )

    @staticmethod
    def _is_readable_pdf(path: Path) -> bool:
        try:
            reader = PdfReader(path, strict=False)
            return len(reader.pages) > 0
        except Exception:
            return False

    @staticmethod
    def _add_duplicate_issues(
        records: list[BackupAuditRecord],
    ) -> list[BackupAuditRecord]:
        hospital_counts = Counter(
            record.hospital_no for record in records if record.hospital_no
        )
        encounter_counts = Counter(
            (record.hospital_no, record.admission_date, record.discharge_date)
            for record in records
            if record.hospital_no and record.admission_date and record.discharge_date
        )
        updated: list[BackupAuditRecord] = []
        for record in records:
            issues = list(record.issues)
            if record.hospital_no and hospital_counts[record.hospital_no] > 1:
                issues.append("DUPLICATE_HOSPITAL_NUMBER")
            encounter = (
                record.hospital_no, record.admission_date, record.discharge_date
            )
            if all(encounter) and encounter_counts[encounter] > 1:
                issues.append("DUPLICATE_ENCOUNTER")
            updated.append(replace(record, issues=tuple(issues)))
        return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit backup patient folders")
    parser.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    parser.add_argument("--report")
    arguments = parser.parse_args()
    auditor = BackupAuditor(arguments.backup_root)
    records = auditor.audit()
    report = auditor.write_csv(records, arguments.report)
    logger.info(f"Review candidates: {sum(item.needs_review for item in records)}")
    logger.info(f"Report: {report}")


if __name__ == "__main__":
    main()
