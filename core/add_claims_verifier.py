"""Add Claims Upload — Confinement period verifier.

Compares the admission/discharge dates encoded in the READY folder name
against the dates OCR'd from the highlighted search result row in the
eClaims Upload Claims popup.

Folder naming convention (from hbsys_ready_claims.py):
    PATIENT NAME - HOSPITAL_NO - ADMYYYYMMDD_DISYYYYMMDD

Example:
    JUAN DELA CRUZ - 12345 - ADM20260101_DIS20260105
    → admission = 2026-01-01, discharge = 2026-01-05
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


# -- folder name parsing -------------------------------------------------

CLAIM_FOLDER_RE = re.compile(
    r"^(?P<name>.+?)\s+-\s+(?P<hospital_no>\d+)\s+-\s+"
    r"ADM(?P<admission>\d{8})_DIS(?P<discharge>\d{8})$"
)


@dataclass(frozen=True)
class FolderDates:
    """Parsed dates from a READY folder name."""

    patient_name: str
    hospital_no: str
    admission: datetime
    discharge: datetime

    @property
    def admission_str(self) -> str:
        """MM/DD/YYYY format for eClaims grid comparison."""
        return self.admission.strftime("%m/%d/%Y")

    @property
    def discharge_str(self) -> str:
        """MM/DD/YYYY format for eClaims grid comparison."""
        return self.discharge.strftime("%m/%d/%Y")

    @property
    def admission_short(self) -> str:
        """MM-DD-YYYY format for HBSys comparison."""
        return self.admission.strftime("%m-%d-%Y")

    @property
    def discharge_short(self) -> str:
        """MM-DD-YYYY format for HBSys comparison."""
        return self.discharge.strftime("%m-%d-%Y")


def parse_folder_name(folder_name: str) -> Optional[FolderDates]:
    """Extract patient name, hospital number, and confinement dates from folder name.

    Returns None if the folder name does not match the expected pattern.
    """
    match = CLAIM_FOLDER_RE.match(folder_name.strip())
    if not match:
        return None

    try:
        admission = datetime.strptime(match.group("admission"), "%Y%m%d")
        discharge = datetime.strptime(match.group("discharge"), "%Y%m%d")
    except ValueError:
        return None

    return FolderDates(
        patient_name=match.group("name").strip(),
        hospital_no=match.group("hospital_no"),
        admission=admission,
        discharge=discharge,
    )


# -- OCR row parsing -----------------------------------------------------

# Dates in eClaims grids typically appear as MM/DD/YYYY or MM-DD-YYYY
DATE_PATTERN = re.compile(r"\d{1,2}[/\-]\d{1,2}[/\-]\d{4}")


@dataclass(frozen=True)
class OcrRowDates:
    """Dates extracted from an OCR'd row in the eClaims search results."""

    admission: str  # MM/DD/YYYY
    discharge: str  # MM/DD/YYYY
    raw_text: str   # full OCR text of the row


def extract_dates_from_ocr_text(text: str) -> Optional[OcrRowDates]:
    """Extract admission and discharge dates from OCR text of a highlighted row.

    Assumes dates appear in chronological order: admission first, discharge second.
    Returns None if fewer than 2 dates are found.
    """
    dates = DATE_PATTERN.findall(text)
    if len(dates) < 2:
        return None

    # Normalise separators to MM/DD/YYYY for consistent comparison
    normalised = [_normalise_date(d) for d in dates[:2]]
    return OcrRowDates(
        admission=normalised[0],
        discharge=normalised[1],
        raw_text=text,
    )


def _normalise_date(value: str) -> str:
    """Convert MM/DD/YYYY or MM-DD-YYYY to MM/DD/YYYY."""
    return value.replace("-", "/")


# -- verification --------------------------------------------------------

@dataclass(frozen=True)
class VerifyResult:
    """Result of comparing folder dates against OCR row dates."""

    match: bool
    folder_dates: FolderDates
    ocr_dates: Optional[OcrRowDates]
    reason: str = ""

    @property
    def summary(self) -> str:
        if self.match:
            return (
                f"MATCH: {self.folder_dates.patient_name} | "
                f"ADM {self.folder_dates.admission_str} DIS {self.folder_dates.discharge_str}"
            )
        if self.ocr_dates is None:
            return f"NO MATCH: Could not extract dates from OCR text"
        return (
            f"NO MATCH: folder ADM {self.folder_dates.admission_str} "
            f"DIS {self.folder_dates.discharge_str} vs "
            f"OCR ADM {self.ocr_dates.admission} DIS {self.ocr_dates.discharge}"
        )


def verify_confinement(
    folder_dates: FolderDates,
    ocr_text: str,
) -> VerifyResult:
    """Verify that the OCR'd row's confinement matches the folder name.

    This is the core verification function called each loop cycle.
    It compares admission AND discharge dates.
    """
    ocr_dates = extract_dates_from_ocr_text(ocr_text)

    if ocr_dates is None:
        return VerifyResult(
            match=False,
            folder_dates=folder_dates,
            ocr_dates=ocr_dates,
            reason="OCR text contains fewer than 2 dates",
        )

    admission_match = _normalise_date(folder_dates.admission_str) == ocr_dates.admission
    discharge_match = _normalise_date(folder_dates.discharge_str) == ocr_dates.discharge

    if admission_match and discharge_match:
        return VerifyResult(
            match=True,
            folder_dates=folder_dates,
            ocr_dates=ocr_dates,
        )

    reasons = []
    if not admission_match:
        reasons.append(f"admission: folder={folder_dates.admission_str} ocr={ocr_dates.admission}")
    if not discharge_match:
        reasons.append(f"discharge: folder={folder_dates.discharge_str} ocr={ocr_dates.discharge}")

    return VerifyResult(
        match=False,
        folder_dates=folder_dates,
        ocr_dates=ocr_dates,
        reason="; ".join(reasons),
    )


# -- standalone test ----------------------------------------------------

if __name__ == "__main__":
    # Test folder name parsing
    test_folders = [
        "JUAN DELA CRUZ - 12345 - ADM20260101_DIS20260105",
        "MARIA SANTOS - 67890 - ADM20260315_DIS20260320",
        "INVALID FORMAT",
    ]

    print("=== Folder Name Parsing ===")
    for name in test_folders:
        result = parse_folder_name(name)
        if result:
            print(f"  OK: {result.patient_name} | {result.hospital_no} | "
                  f"ADM {result.admission_str} DIS {result.discharge_str}")
        else:
            print(f"  FAIL: Could not parse '{name}'")

    # Test OCR date extraction
    print("\n=== OCR Date Extraction ===")
    test_ocr_texts = [
        "JUAN DELA CRUZ 12345 01/01/2026 01/05/2026 CLAIM",
        "MARIA SANTOS 03/15/2026 03/20/2026",
        "NO DATES HERE",
        "SINGLE 01/01/2026",
    ]
    for text in test_ocr_texts:
        result = extract_dates_from_ocr_text(text)
        if result:
            print(f"  OK: ADM={result.admission} DIS={result.discharge}")
        else:
            print(f"  FAIL: '{text}'")

    # Test verification
    print("\n=== Confinement Verification ===")
    folder = parse_folder_name("JUAN DELA CRUZ - 12345 - ADM20260101_DIS20260105")
    if folder:
        # Match
        vr = verify_confinement(folder, "JUAN DELA CRUZ 12345 01/01/2026 01/05/2026")
        print(f"  Test 1: {vr.summary}")

        # Mismatch
        vr2 = verify_confinement(folder, "JUAN DELA CRUZ 12345 01/02/2026 01/06/2026")
        print(f"  Test 2: {vr2.summary}")

        # No dates
        vr3 = verify_confinement(folder, "JUAN DELA CRUZ 12345 NO DATES")
        print(f"  Test 3: {vr3.summary}")
