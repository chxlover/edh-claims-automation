"""Hospital Number fallback resolver.

This module is intentionally independent from the production processor.  It
collects possible Hospital Numbers from available group OCR text and, when
needed, from targeted SOA1 image crops.  It does not display GUI and it does
not write to any database.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.hospital_number_extractor import extract_soa1_hospital_number


PatientLookup = Callable[[str], Any | None]

OCR_DIGIT_TRANSLATION = str.maketrans(
    {"O": "0", "Q": "0", "I": "1", "L": "1", "S": "5", "B": "8"}
)

STRONG_LABEL_PATTERN = re.compile(
    r"(?:HOSPITAL|HOSP|PATIENT\s+HEALTH\s+RECORD|HEALTH\s+RECORD|HRN)"
    r"\s*(?:NO|NUMBER|#|CODE)?\.?\s*[:\-]?\s*"
    r"([0-9OQILSB][0-9OQILSB\s\-/.]{3,28})",
    re.IGNORECASE,
)

ANY_15_DIGIT_PATTERN = re.compile(r"(?<![A-Z0-9])\d{15}(?![A-Z0-9])", re.IGNORECASE)

RISKY_LABELS = (
    "SOA REFERENCE",
    "SOA REF",
    "PHILHEALTH",
    "PIN",
    "CLAIM",
    "CASE",
    "VOUCHER",
    "RECEIPT",
    "ACCREDITATION",
    "REFERENCE RANGE",
    "ROOM",
)


@dataclass(frozen=True, slots=True)
class HospitalNumberCandidate:
    hospital_no: str
    score: int
    source: str
    document: str = ""
    evidence: tuple[str, ...] = ()
    database_patient_found: bool = False


@dataclass(frozen=True, slots=True)
class HospitalNumberResolution:
    hospital_no: str = ""
    confidence: int = 0
    source: str = ""
    document: str = ""
    evidence: tuple[str, ...] = ()
    should_auto_accept: bool = False
    candidates: tuple[HospitalNumberCandidate, ...] = field(default_factory=tuple)

    @property
    def has_suggestion(self) -> bool:
        return bool(self.hospital_no)

    def summary(self) -> str:
        if not self.hospital_no:
            return "Hospital Number fallback: no safe candidate found"
        status = "auto-accepted" if self.should_auto_accept else "suggested only"
        evidence = "; ".join(self.evidence)
        return (
            "Hospital Number fallback "
            f"({status}): {self.hospital_no} "
            f"confidence={self.confidence} source={self.source}"
            + (f" document={os.path.basename(self.document)}" if self.document else "")
            + (f" evidence=[{evidence}]" if evidence else "")
        )


def resolve_hospital_number(
    group: Iterable[dict[str, Any]],
    *,
    patient_lookup: PatientLookup | None = None,
    poppler_path: str | None = None,
    tesseract_cmd: str | None = None,
    min_auto_confidence: int = 90,
) -> HospitalNumberResolution:
    """Return a confident fallback Hospital Number or an explainable suggestion.

    ``group`` is the processor's patient document group.  Each item may contain
    ``initial_doc_type``, ``text`` and ``path`` keys.
    """
    items = list(group)
    candidates: list[HospitalNumberCandidate] = []

    for item in items:
        text = str(item.get("text") or "")
        doc_type = str(item.get("initial_doc_type") or "UNKNOWN")
        path = str(item.get("path") or item.get("file") or "")
        candidates.extend(_extract_candidates_from_text(text, doc_type, path))

    if not candidates:
        for item in items:
            if str(item.get("initial_doc_type") or "").upper() != "SOA1":
                continue
            path = str(item.get("path") or "")
            if not path:
                continue
            for crop_text in _ocr_soa1_crops(
                path,
                poppler_path=poppler_path,
                tesseract_cmd=tesseract_cmd,
            ):
                candidates.extend(_extract_candidates_from_text(crop_text, "SOA1_CROP", path))

    candidates = _merge_and_validate_candidates(candidates, patient_lookup)
    if not candidates:
        return HospitalNumberResolution()

    candidates.sort(key=lambda item: item.score, reverse=True)
    best = candidates[0]
    second_score = candidates[1].score if len(candidates) > 1 else 0

    # Do not auto-accept close races. This is where false positives usually hide.
    has_clear_winner = best.score - second_score >= 12 or len(candidates) == 1
    db_ok_or_unavailable = best.database_patient_found or patient_lookup is None
    should_auto_accept = (
        best.score >= min_auto_confidence
        and has_clear_winner
        and db_ok_or_unavailable
    )

    return HospitalNumberResolution(
        hospital_no=best.hospital_no,
        confidence=max(0, min(100, best.score)),
        source=best.source,
        document=best.document,
        evidence=best.evidence,
        should_auto_accept=should_auto_accept,
        candidates=tuple(candidates[:5]),
    )


def _extract_candidates_from_text(
    text: str,
    doc_type: str,
    document: str,
) -> list[HospitalNumberCandidate]:
    if not text.strip():
        return []

    candidates: list[HospitalNumberCandidate] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = "\n".join(lines)

    strict_soa1 = extract_soa1_hospital_number(joined)
    if strict_soa1:
        candidates.append(
            HospitalNumberCandidate(
                hospital_no=strict_soa1,
                score=78 if doc_type == "SOA1" else 68,
                source=f"{doc_type}_STRICT_TEXT",
                document=document,
                evidence=("Strict labeled Hospital No extractor matched",),
            )
        )

    for index, line in enumerate(lines):
        context = " ".join(lines[max(0, index - 1): index + 2])
        if _has_risky_label(context):
            continue

        match = STRONG_LABEL_PATTERN.search(context)
        if match:
            hospital_no = _normalize_candidate(match.group(1))
            if hospital_no:
                score = 82
                if doc_type == "SOA1":
                    score += 6
                candidates.append(
                    HospitalNumberCandidate(
                        hospital_no=hospital_no,
                        score=score,
                        source=f"{doc_type}_LABEL_TEXT",
                        document=document,
                        evidence=("Found near Hospital/HRN label",),
                    )
                )

    # Last text fallback: an isolated 15-digit value. Safe only as a suggestion
    # unless HBSys validation raises the score later.
    for match in ANY_15_DIGIT_PATTERN.finditer(joined):
        context = joined[max(0, match.start() - 60): match.end() + 60]
        if _has_risky_label(context):
            continue
        candidates.append(
            HospitalNumberCandidate(
                hospital_no=match.group(0),
                score=54 if doc_type == "SOA1" else 45,
                source=f"{doc_type}_ISOLATED_15_DIGIT",
                document=document,
                evidence=("Isolated 15-digit candidate",),
            )
        )

    return candidates


def _merge_and_validate_candidates(
    candidates: list[HospitalNumberCandidate],
    patient_lookup: PatientLookup | None,
) -> list[HospitalNumberCandidate]:
    merged: dict[str, HospitalNumberCandidate] = {}
    counts: dict[str, int] = {}

    for candidate in candidates:
        counts[candidate.hospital_no] = counts.get(candidate.hospital_no, 0) + 1
        previous = merged.get(candidate.hospital_no)
        if previous is None or candidate.score > previous.score:
            merged[candidate.hospital_no] = candidate

    validated: list[HospitalNumberCandidate] = []
    for hospital_no, candidate in merged.items():
        score = candidate.score
        evidence = list(candidate.evidence)
        if counts[hospital_no] > 1:
            score += min(12, 4 * (counts[hospital_no] - 1))
            evidence.append(f"Seen {counts[hospital_no]} time(s)")

        database_patient_found = False
        if patient_lookup is not None:
            try:
                database_patient_found = patient_lookup(hospital_no) is not None
            except Exception as exc:
                evidence.append(f"HBSys lookup unavailable: {exc}")
            else:
                if database_patient_found:
                    score += 28
                    evidence.append("Hospital Number exists in HBSys")
                else:
                    score -= 22
                    evidence.append("Hospital Number not found in HBSys")

        validated.append(
            HospitalNumberCandidate(
                hospital_no=hospital_no,
                score=max(0, min(100, score)),
                source=candidate.source,
                document=candidate.document,
                evidence=tuple(evidence),
                database_patient_found=database_patient_found,
            )
        )

    return validated


def _ocr_soa1_crops(
    pdf_path: str,
    *,
    poppler_path: str | None,
    tesseract_cmd: str | None,
) -> list[str]:
    try:
        import pytesseract
        from PIL import ImageEnhance, ImageOps
        from pdf2image import convert_from_path
    except Exception:
        return []

    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    try:
        pages = convert_from_path(
            pdf_path,
            dpi=300,
            first_page=1,
            last_page=1,
            poppler_path=poppler_path,
        )
    except Exception:
        return []

    if not pages:
        return []

    page = pages[0]
    width, height = page.size
    zones = (
        (0.00, 0.00, 1.00, 0.42),
        (0.00, 0.00, 0.58, 0.48),
        (0.42, 0.00, 1.00, 0.48),
        (0.00, 0.14, 1.00, 0.58),
    )
    texts: list[str] = []

    for left, top, right, bottom in zones:
        crop = page.crop(
            (
                int(width * left),
                int(height * top),
                int(width * right),
                int(height * bottom),
            )
        )
        gray = crop.convert("L")
        enlarged = gray.resize((gray.width * 2, gray.height * 2))
        variants = [
            enlarged,
            ImageEnhance.Sharpness(enlarged).enhance(2.0),
            ImageEnhance.Contrast(enlarged).enhance(1.8),
            enlarged.point(lambda pixel: 255 if pixel > 170 else 0),
            ImageOps.invert(enlarged),
        ]
        for image in variants:
            try:
                text = pytesseract.image_to_string(image, config="--psm 6")
            except Exception:
                continue
            if text.strip():
                texts.append(text)

    return texts


def _normalize_candidate(value: str) -> str:
    translated = str(value or "").upper().translate(OCR_DIGIT_TRANSLATION)
    digits = re.sub(r"\D", "", translated)
    if not 5 <= len(digits) <= 15:
        return ""
    if len(digits) == 8 and _looks_like_date(digits):
        return ""
    return digits.zfill(15)


def _looks_like_date(digits: str) -> bool:
    return 1900 <= int(digits[:4]) <= 2100 or 1900 <= int(digits[-4:]) <= 2100


def _has_risky_label(text: str) -> bool:
    clean = re.sub(r"\s+", " ", str(text or "").upper())
    return any(label in clean for label in RISKY_LABELS)


if __name__ == "__main__":
    sample_group = [
        {
            "initial_doc_type": "SOA1",
            "text": "Statement of Account\nPatient Health Record No: OOOOOOOOOO181O6",
            "path": "SOA1.pdf",
        }
    ]
    result = resolve_hospital_number(sample_group, min_auto_confidence=80)
    assert result.hospital_no == "000000000018106"
    assert result.should_auto_accept
    print("Hospital Number resolver standalone test passed")
