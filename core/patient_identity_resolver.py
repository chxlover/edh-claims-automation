"""Resolve patient identity from document text when Hospital No OCR fails.

Primary use case: SOA1 Hospital Number is unreadable, but SOA2 contains a
patient name plus admission/discharge dates.  This module performs read-only
HBSys lookup and returns an explainable confidence result.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from rapidfuzz import fuzz

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


ConnectionFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class IdentityCandidate:
    hospital_no: str
    patient_name: str
    encounter_no: str
    admission_date: str
    discharge_date: str
    score: int
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    hospital_no: str = ""
    patient_name: str = ""
    encounter_no: str = ""
    admission_date: str = ""
    discharge_date: str = ""
    confidence: int = 0
    source: str = ""
    evidence: tuple[str, ...] = ()
    should_auto_accept: bool = False
    candidates: tuple[IdentityCandidate, ...] = field(default_factory=tuple)

    @property
    def has_suggestion(self) -> bool:
        return bool(self.hospital_no)

    def summary(self) -> str:
        if not self.hospital_no:
            return "SOA2 identity fallback: no safe candidate found"
        status = "auto-accepted" if self.should_auto_accept else "suggested only"
        evidence = "; ".join(self.evidence)
        return (
            f"SOA2 identity fallback ({status}): {self.hospital_no} "
            f"patient={self.patient_name} confidence={self.confidence} "
            f"source={self.source}"
            + (f" evidence=[{evidence}]" if evidence else "")
        )


def extract_patient_name_from_soa_text(text: str) -> str:
    """Extract likely SOA patient name from OCR text."""
    raw = str(text or "")
    if not raw.strip():
        return ""

    patterns = (
        r"(?:PRINT\s*NAME|PATIENT\s*NAME|NAME)\s*[:\-]?\s*([A-ZÑ,\-. ]{5,80})",
    )
    for pattern in patterns:
        match = re.search(pattern, raw, re.IGNORECASE)
        if not match:
            continue
        candidate = _clean_name(match.group(1))
        if candidate:
            return candidate

    for line in raw.splitlines():
        clean = _clean_name(line)
        if "," in clean and 8 <= len(clean) <= 70 and not _looks_like_non_name(clean):
            return clean

    return ""


def resolve_identity_from_soa2(
    *,
    soa_patient_name: str,
    admission_date: str = "",
    discharge_date: str = "",
    connection_factory: ConnectionFactory,
    min_auto_score: int = 92,
) -> IdentityResolution:
    """Find one clear HBSys patient/admission using SOA name and dates."""
    soa_patient_name = _clean_name(soa_patient_name)
    admission_date = str(admission_date or "").strip()
    discharge_date = str(discharge_date or "").strip()

    if not soa_patient_name or not (admission_date or discharge_date):
        return IdentityResolution()

    candidates = _query_admissions(
        connection_factory,
        admission_date=admission_date,
        discharge_date=discharge_date,
    )
    if not candidates:
        return IdentityResolution()

    scored: list[IdentityCandidate] = []
    for row in candidates:
        db_name = _build_patient_name(row)
        name_score = int(
            max(
                fuzz.token_set_ratio(soa_patient_name, db_name),
                fuzz.partial_ratio(soa_patient_name, db_name),
            )
        )
        evidence = [f"SOA2 name '{soa_patient_name}' vs HBSys '{db_name}' = {name_score}%"]
        score = name_score

        if admission_date and row["admission_date"] == admission_date:
            score += 4
            evidence.append("Admission date matched")
        if discharge_date and row["discharge_date"] == discharge_date:
            score += 4
            evidence.append("Discharge date matched")

        scored.append(
            IdentityCandidate(
                hospital_no=row["hospital_no"],
                patient_name=db_name,
                encounter_no=row["encounter_no"],
                admission_date=row["admission_date"],
                discharge_date=row["discharge_date"],
                score=max(0, min(100, score)),
                evidence=tuple(evidence),
            )
        )

    scored.sort(key=lambda item: item.score, reverse=True)
    best = scored[0]
    second_score = scored[1].score if len(scored) > 1 else 0
    clear_winner = len(scored) == 1 or best.score - second_score >= 8
    has_both_dates = bool(admission_date and discharge_date)
    should_auto_accept = (
        best.score >= min_auto_score
        and clear_winner
        and (has_both_dates or best.score >= 97)
    )

    evidence = list(best.evidence)
    evidence.append(f"HBSys candidates on date filter: {len(scored)}")
    if not clear_winner:
        evidence.append(f"Not auto-used: next candidate score is {second_score}%")

    return IdentityResolution(
        hospital_no=best.hospital_no,
        patient_name=best.patient_name,
        encounter_no=best.encounter_no,
        admission_date=best.admission_date,
        discharge_date=best.discharge_date,
        confidence=best.score,
        source="SOA2_NAME_ADMISSION_DISCHARGE",
        evidence=tuple(evidence),
        should_auto_accept=should_auto_accept,
        candidates=tuple(scored[:5]),
    )


def _query_admissions(
    connection_factory: ConnectionFactory,
    *,
    admission_date: str,
    discharge_date: str,
) -> list[dict[str, str]]:
    connection = None
    try:
        connection = connection_factory()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    p.hpercode,
                    p.patlast,
                    p.patfirst,
                    p.patsuffix,
                    p.patmiddle,
                    h.enccode,
                    DATE_FORMAT(h.admdate, '%%Y%%m%%d') AS admission_date,
                    DATE_FORMAT(h.disdate, '%%Y%%m%%d') AS discharge_date
                FROM hadmlog h
                INNER JOIN hperson p ON p.hpercode = h.hpercode
                WHERE (%s = '' OR DATE_FORMAT(h.admdate, '%%Y%%m%%d') = %s)
                  AND (%s = '' OR DATE_FORMAT(h.disdate, '%%Y%%m%%d') = %s)
                ORDER BY h.admdate DESC
                LIMIT 300
                """,
                (admission_date, admission_date, discharge_date, discharge_date),
            )
            rows = cursor.fetchall()
    finally:
        if connection is not None:
            connection.close()

    result: list[dict[str, str]] = []
    for row in rows or ():
        result.append(
            {
                "hospital_no": _normalize_hospital_no(row.get("hpercode")),
                "patient_name": _build_patient_name(row),
                "encounter_no": str(row.get("enccode") or "").strip(),
                "admission_date": str(row.get("admission_date") or "").strip(),
                "discharge_date": str(row.get("discharge_date") or "").strip(),
                "patlast": str(row.get("patlast") or ""),
                "patfirst": str(row.get("patfirst") or ""),
                "patsuffix": str(row.get("patsuffix") or ""),
                "patmiddle": str(row.get("patmiddle") or ""),
            }
        )
    return result


def _build_patient_name(row: dict[str, Any]) -> str:
    last = str(row.get("patlast") or "").strip().upper()
    parts = [
        str(row.get(field) or "").strip().upper()
        for field in ("patfirst", "patsuffix", "patmiddle")
        if str(row.get(field) or "").strip()
    ]
    given = " ".join(parts)
    return " ".join(f"{last}, {given}".strip(" ,").split())


def _clean_name(value: str) -> str:
    text = str(value or "").upper().replace("Ã‘", "Ñ")
    text = re.split(
        r"\b(?:DATE|AGE|ADDRESS|FINAL|DIAGNOSIS|ADMISSION|DISCHARGE|HOSPITAL|SOA)\b",
        text,
        maxsplit=1,
    )[0]
    text = re.sub(r"[^A-ZÑ,\-. ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,.-")
    if _looks_like_non_name(text):
        return ""
    return text


def _looks_like_non_name(text: str) -> bool:
    clean = str(text or "").upper()
    blocked = (
        "STATEMENT OF ACCOUNT",
        "ECHAGUE DISTRICT",
        "PHILHEALTH",
        "SUMMARY",
        "ITEMIZED",
        "ROOM AND BOARD",
        "LABORATORY",
        "MEDICINE",
    )
    return any(item in clean for item in blocked)


def _normalize_hospital_no(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(15) if digits else ""


if __name__ == "__main__":
    sample = "Print Name: ALINDADA, JESUSA LACSON Date and Time Admitted:"
    assert extract_patient_name_from_soa_text(sample) == "ALINDADA, JESUSA LACSON"
    print("Patient identity resolver standalone test passed")
