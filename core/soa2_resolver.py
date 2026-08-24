"""SOA2 one-page vs two-page resolver.

SOA2 can be a complete single page or a split two-page document.  This module
keeps that decision deterministic and reusable without changing the OCR engine.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from rapidfuzz import fuzz
except Exception:  # pragma: no cover - production has rapidfuzz
    fuzz = None

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@dataclass(frozen=True, slots=True)
class SOA2Resolution:
    doc_type: str
    page1_score: int
    completion_score: int
    guarded: bool = False
    evidence: tuple[str, ...] = ()

    @property
    def is_soa2(self) -> bool:
        return self.doc_type in {"SOA2", "SOA2_page1", "SOA2_page2"}


PAGE1_MARKERS = (
    "SOA REFERENCE NO",
    "SOA REFERENCE #",
    "SOA REF NO",
    "SOA REF #",
    "STATEMENT OF ACCOUNT",
)

PAGE1_SUPPORT_MARKERS = (
    "PRINT NAME",
    "PATIENT NAME",
    "DATE AND TIME ADMITTED",
    "DATE AND TIME DISCHARGED",
    "FINAL DIAGNOSIS",
    "SUMMARY OF FEES",
    "ITEMIZED CHARGES",
)

COMPLETION_STRONG_MARKERS = (
    "CONFORME",
    "PREPARED BY",
)

COMPLETION_SUPPORT_MARKERS = (
    "SIGNATURE OVER PRINTED NAME",
    "PATIENT / REPRESENTATIVE",
    "PATIENT/REPRESENTATIVE",
    "PATIENT REPRESENTATIVE",
    "RELATIONSHIP OF REPRESENTATIVE",
    "ADMINISTRATIVE OFFICER",
    "ADMINISTRATIVE AIDE",
)

DTR_GUARDS = (
    "REFERENCE RANGE",
    "REF RANGE",
    "HEMATOLOGY",
    "URINALYSIS",
    "CLINICAL CHEMISTRY",
    "ELECTROCARDIOGRAM",
    "NORMAL SINUS RHYTHM",
    "LABORATORY RESULT",
    "X RAY",
    "XRAY",
)

COE_GUARDS = (
    "HCI PORTAL REFERENCE",
    "PHILHEALTH BENEFIT ELIGIBILITY",
    "TEAM PHILHEALTH",
    "TEAMPHILHEALTH",
)

ANR_GUARDS = (
    "ANESTHESIA RECORD",
    "ANAESTHESIA RECORD",
    "ANESTHETIC AGENT",
    "INDUCTION",
    "MAINTENANCE",
    "EMERGENCE",
)


def resolve_soa2_text(text: str, *, lower_text: str = "") -> SOA2Resolution:
    clean = _normalize(text)
    lower_clean = _normalize(lower_text)
    evidence: list[str] = []

    if _has_any(clean, COE_GUARDS):
        return SOA2Resolution("UNKNOWN", 0, 0, True, ("COE guard matched",))
    if _has_any(clean, ANR_GUARDS):
        return SOA2Resolution("UNKNOWN", 0, 0, True, ("ANR guard matched",))
    if _has_any(clean, DTR_GUARDS) and not _has_any(clean, ("STATEMENT OF ACCOUNT", "SOA REFERENCE")):
        return SOA2Resolution("UNKNOWN", 0, 0, True, ("DTR/lab guard matched",))

    page1_score = 0
    completion_score = 0

    for marker in PAGE1_MARKERS:
        if marker in clean:
            page1_score += 35
            evidence.append(f"page1:{marker}")
            break

    support_hits = sum(1 for marker in PAGE1_SUPPORT_MARKERS if marker in clean)
    if support_hits:
        page1_score += min(35, support_hits * 8)
        evidence.append(f"page1_support:{support_hits}")

    for marker in COMPLETION_STRONG_MARKERS:
        if marker in clean:
            completion_score += 35
            evidence.append(f"completion:{marker}")

    support_completion_hits = sum(
        1 for marker in COMPLETION_SUPPORT_MARKERS if marker in clean
    )
    if support_completion_hits:
        completion_score += min(35, support_completion_hits * 10)
        evidence.append(f"completion_support:{support_completion_hits}")

    if lower_clean:
        lower_hits = sum(
            1
            for marker in (*COMPLETION_STRONG_MARKERS, *COMPLETION_SUPPORT_MARKERS)
            if marker in lower_clean
        )
        if lower_hits:
            completion_score += min(20, lower_hits * 8)
            evidence.append(f"lower_completion:{lower_hits}")

    if completion_score < 35 and fuzz is not None:
        for line in clean.splitlines():
            for marker in (*COMPLETION_STRONG_MARKERS, *COMPLETION_SUPPORT_MARKERS):
                if fuzz.partial_ratio(marker, line) >= 84:
                    completion_score += 28
                    evidence.append(f"fuzzy_completion:{marker}")
                    break
            if completion_score >= 35:
                break

    page1_score = min(100, page1_score)
    completion_score = min(100, completion_score)

    has_page1 = page1_score >= 35
    has_completion = completion_score >= 35

    if has_page1 and has_completion:
        return SOA2Resolution("SOA2", page1_score, completion_score, False, tuple(evidence))
    if has_page1:
        return SOA2Resolution("SOA2_page1", page1_score, completion_score, False, tuple(evidence))
    if has_completion:
        return SOA2Resolution("SOA2_page2", page1_score, completion_score, False, tuple(evidence))
    return SOA2Resolution("UNKNOWN", page1_score, completion_score, False, tuple(evidence))


def _normalize(value: str) -> str:
    text = str(value or "").upper()
    replacements = {
        "S0A": "SOA",
        "5OA": "SOA",
        "CONF0RME": "CONFORME",
        "CONFARNE": "CONFORME",
        "CONFORRNE": "CONFORME",
    }
    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)
    text = re.sub(r"[^A-Z0-9#:/,\- ]+", " ", text)
    text = re.sub(r"\bS\s+O\s+A\b", "SOA", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


if __name__ == "__main__":
    one_page = "Statement of Account SOA Reference No 2026-1 Print Name JUAN Conforme Signature over Printed Name"
    split_page1 = "Statement of Account SOA Reference No 2026-1 Print Name JUAN Summary of Fees"
    split_page2 = "Prepared by Administrative Officer Conforme Patient / Representative"
    lab = "Laboratory Result Reference Range Hematology Prepared by"
    assert resolve_soa2_text(one_page).doc_type == "SOA2"
    assert resolve_soa2_text(split_page1).doc_type == "SOA2_page1"
    assert resolve_soa2_text(split_page2).doc_type == "SOA2_page2"
    assert resolve_soa2_text(lab).doc_type == "UNKNOWN"
    print("SOA2 resolver standalone test passed")
