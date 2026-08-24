"""Strict Hospital Number extraction from SOA1 OCR text."""

from __future__ import annotations

import re


OCR_DIGIT_TRANSLATION = str.maketrans(
    {"O": "0", "Q": "0", "I": "1", "L": "1", "S": "5", "B": "8"}
)
LABEL_PATTERN = re.compile(
    r"(?:HOSPITAL|HOSP)\s*(?:NO|NUMBER|#)\.?\s*[:\-]?",
    re.IGNORECASE,
)
DIGIT_LIKE_PATTERN = re.compile(
    r"(?<![A-Z0-9])[0-9OQILSB][0-9OQILSB\s\-/.]{3,28}(?![A-Z0-9])"
)


def extract_soa1_hospital_number(text: str) -> str | None:
    """Return a normalized 15-digit number only from credible SOA1 evidence."""
    if not text or not text.strip():
        return None

    lines = [line.strip().upper() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        label = LABEL_PATTERN.search(line)
        if not label:
            continue
        nearby = [line[label.end():], *lines[index + 1:index + 3]]
        for fragment in nearby:
            candidate = _numeric_candidate(fragment)
            if candidate:
                return candidate

    # An uninterrupted 15-digit value is a safe fallback. Short unlabeled
    # values are rejected because dates/account numbers could match.
    match = re.search(r"(?<![A-Z0-9])\d{15}(?![A-Z0-9])", text, re.IGNORECASE)
    return match.group(0) if match else None


def _numeric_candidate(fragment: str) -> str | None:
    match = DIGIT_LIKE_PATTERN.search(fragment.upper())
    if not match:
        return None
    translated = match.group(0).translate(OCR_DIGIT_TRANSLATION)
    digits = re.sub(r"\D", "", translated)
    if not 5 <= len(digits) <= 15:
        return None
    if len(digits) == 8 and _looks_like_date(digits):
        return None
    return digits.zfill(15)


def _looks_like_date(digits: str) -> bool:
    return (
        1900 <= int(digits[:4]) <= 2100
        or 1900 <= int(digits[-4:]) <= 2100
    )


if __name__ == "__main__":
    from core.activity_logger import logger

    assert extract_soa1_hospital_number(
        "Statement of Account\nHospital No: 000000000015308"
    ) == "000000000015308"
    assert extract_soa1_hospital_number(
        "Hospital No: OOOOOOOOOO153O8"
    ) == "000000000015308"
    assert extract_soa1_hospital_number(
        "Hospital No: DATE/TIME ADMITTED 30 APR 2026"
    ) is None
    logger.success("SOA1 Hospital Number extractor standalone test passed")
