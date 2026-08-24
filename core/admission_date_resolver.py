"""Fallback admission/discharge date resolver.

Used when OCR misses confinement dates but the batch/backup folder name already
contains reliable ADMYYYYMMDD_DISYYYYMMDD metadata.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


ADM_DIS_PATTERN = re.compile(
    r"ADM(?P<adm>\d{8})[_\-\s]*DIS(?P<dis>\d{8})",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AdmissionDateResolution:
    admission_date: str = ""
    discharge_date: str = ""
    source: str = ""
    evidence: str = ""

    @property
    def found(self) -> bool:
        return bool(self.admission_date and self.discharge_date)


def resolve_admission_dates_from_metadata(
    *,
    hospital_no: str = "",
    candidate_paths: Iterable[str | os.PathLike[str]] = (),
    backup_root: str | os.PathLike[str] = "",
) -> AdmissionDateResolution:
    """Resolve ADM/DIS dates from paths, then matching backup folder names."""
    for path in candidate_paths:
        resolution = extract_admission_dates_from_text(str(path))
        if resolution.found:
            return resolution

    hospital_no_digits = _digits(hospital_no)
    if not hospital_no_digits or not backup_root:
        return AdmissionDateResolution()

    root = Path(backup_root)
    if not root.is_dir():
        return AdmissionDateResolution()

    matches: list[Path] = []
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        folder_text = folder.name
        if hospital_no_digits not in _digits(folder_text):
            continue
        if ADM_DIS_PATTERN.search(folder_text):
            matches.append(folder)

    if not matches:
        return AdmissionDateResolution()

    matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    newest = matches[0]
    resolution = extract_admission_dates_from_text(newest.name)
    if not resolution.found:
        return AdmissionDateResolution()

    return AdmissionDateResolution(
        admission_date=resolution.admission_date,
        discharge_date=resolution.discharge_date,
        source="BACKUP_FOLDER_NAME",
        evidence=str(newest),
    )


def extract_admission_dates_from_text(value: str) -> AdmissionDateResolution:
    match = ADM_DIS_PATTERN.search(str(value or ""))
    if not match:
        return AdmissionDateResolution()
    return AdmissionDateResolution(
        admission_date=match.group("adm"),
        discharge_date=match.group("dis"),
        source="PATH_METADATA",
        evidence=str(value),
    )


def _digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


if __name__ == "__main__":
    result = extract_admission_dates_from_text(
        r"BENZAL - 000000000000622 - ADM20260701_DIS20260704"
    )
    assert result.admission_date == "20260701"
    assert result.discharge_date == "20260704"
    print("Admission date resolver standalone test passed")
