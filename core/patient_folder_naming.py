"""Canonical EDH patient output-folder naming helpers."""

from __future__ import annotations

import re
from datetime import date, datetime


def sanitize_folder_component(value: object) -> str:
    """Return a Windows-safe folder-name component."""
    if value is None:
        return ""
    cleaned = str(value).strip()
    cleaned = re.sub(r'[<>:"/\\|?*\r\n\t]+', "-", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned.strip(" -")


def normalize_folder_date(value: object) -> str:
    """Normalize a date-like value to YYYYMMDD, or return an empty string."""
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    digits = "".join(character for character in str(value) if character.isdigit())
    if len(digits) >= 8:
        candidate = digits[:8]
        try:
            datetime.strptime(candidate, "%Y%m%d")
            return candidate
        except ValueError:
            pass
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(str(value).strip()[:10], pattern).strftime("%Y%m%d")
        except ValueError:
            continue
    return ""


def normalize_hpercode(value: object) -> str:
    """Normalize an HBSys patient code to its 15-digit display form."""
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits.zfill(15) if digits else ""


def build_patient_folder_name(
    patient_name: object,
    hospital_no: object = None,
    admdate: object = None,
    disdate: object = None,
) -> str:
    """Build ``NAME - HPERCODE - ADMYYYYMMDD_DISYYYYMMDD`` when complete."""
    name = sanitize_folder_component(patient_name)
    hospital = sanitize_folder_component(hospital_no)
    admission = normalize_folder_date(admdate)
    discharge = normalize_folder_date(disdate)
    parts = [name]
    if hospital:
        parts.append(hospital)
    if admission and discharge:
        parts.append(f"ADM{admission}_DIS{discharge}")
    return " - ".join(part for part in parts if part)


if __name__ == "__main__":
    sample = build_patient_folder_name(
        "DELA CRUZ, JUAN S",
        "1234",
        "2026-01-15",
        "2026-01-18",
    )
    assert sample == "DELA CRUZ, JUAN S - 1234 - ADM20260115_DIS20260118"
    print(sample)
