"""Pure requirement rules for the Claims Checker.

This module does not inspect files, display GUI elements, or access a database.
The caller supplies the document types that were already detected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


BASE_REQUIREMENTS = (
    "CSF",
    "COE",
    "SOA1",
    "SOA2",
    "DTR",
    "CF4 XML",
    "CF5 XML",
    "eSOA XML",
)


@dataclass(frozen=True)
class RequirementResult:
    """Explainable result returned by :func:`evaluate_requirements`."""

    classification: str
    required: tuple[str, ...]
    missing: tuple[str, ...]
    notes: tuple[str, ...]


def _canonical_document(value: object) -> str:
    compact = " ".join(str(value or "").strip().split()).upper()
    aliases = {
        "ESOA": "eSOA XML",
        "ESOA XML": "eSOA XML",
        "CF4": "CF4 XML",
        "CF5": "CF5 XML",
    }
    if compact in aliases:
        return aliases[compact]
    return compact


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def evaluate_requirements(
    found_documents: Iterable[str],
    *,
    nsd01_detected: bool = False,
    coe_eligibility_no: bool = False,
) -> RequirementResult:
    """Evaluate base and cumulative conditional claim requirements.

    ``PBC or MMC`` is an alternative requirement and is therefore represented
    as one missing item when an MRF exists but neither companion is present.
    """

    found = {_canonical_document(item) for item in found_documents}
    required = list(BASE_REQUIREMENTS)
    missing: list[str] = []
    notes: list[str] = []
    classifications: list[str] = []

    if "CF2" in found:
        classifications.append("NEWBORN")
        notes.append("CF2 present - Newborn claim")

    if "ANR" in found:
        classifications.append("CS/ANR")
        notes.append("ANR present - OPR and CF3 required")
        _append_unique(required, "OPR")
        _append_unique(required, "CF3")

    if nsd01_detected:
        classifications.append("NSD")
        notes.append("NSD01 detected in CSF - OPR and CF3 required")
        _append_unique(required, "OPR")
        _append_unique(required, "CF3")

    if "MRF" in found:
        notes.append("MRF present - PBC or MMC required")

    if coe_eligibility_no:
        notes.append("COE eligibility NO - MRF and PBC or MMC required")
        _append_unique(required, "MRF")

    if not classifications:
        classifications.append("REGULAR")

    for document in required:
        if _canonical_document(document) not in found:
            _append_unique(missing, document)

    if ("MRF" in found or coe_eligibility_no) and not ({"PBC", "MMC"} & found):
        _append_unique(missing, "PBC or MMC")

    return RequirementResult(
        classification=" + ".join(classifications),
        required=tuple(required),
        missing=tuple(missing),
        notes=tuple(notes),
    )


def main() -> int:
    """Small standalone smoke test required by the project architecture."""

    complete_regular = set(BASE_REQUIREMENTS)
    result = evaluate_requirements(complete_regular)
    print(f"Classification: {result.classification}")
    print(f"Missing: {', '.join(result.missing) or 'None'}")
    return 0 if not result.missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
