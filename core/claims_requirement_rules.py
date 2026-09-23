"""Pure requirement rules for the Claims Checker.

This module does not inspect patient files, display GUI elements, or access a
database.  The caller supplies the document types that were already detected.

The REQUIRED/EXCLUDED document set comes from the Claim Attachment Profile
(core/claim_attachment_profile.py, edited via Preferences -> Claim Attachment
Checklist...): a document whose checkbox is unchecked is neither required nor
considered "found" by the rules below.  When the profile module is unavailable
the legacy hardcoded behavior below runs unchanged.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from core.claim_attachment_profile import (
        get_enabled_requirement_names,
        get_required_base_docs,
        is_enabled,
    )
except Exception:  # pragma: no cover - legacy fallback keeps checker alive
    get_enabled_requirement_names = None
    get_required_base_docs = None
    is_enabled = None


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

    With the Claim Attachment Profile active, disabled documents are filtered
    out of ``found_documents`` first: an unchecked document is invisible to
    every rule below (never required, never triggers conditional rules).
    """

    found = {_canonical_document(item) for item in found_documents}
    if get_enabled_requirement_names is not None:
        found &= get_enabled_requirement_names()
        required = list(get_required_base_docs())
        doc_enabled = lambda code: is_enabled(code)  # noqa: E731
    else:
        required = list(BASE_REQUIREMENTS)
        doc_enabled = lambda _code: True  # noqa: E731
    missing: list[str] = []
    notes: list[str] = []
    classifications: list[str] = []

    if "CF2" in found:
        classifications.append("NEWBORN")
        notes.append("CF2 present - Newborn claim")

    if "ANR" in found:
        classifications.append("CS/ANR")
        notes.append("ANR present - OPR and CF3 required")
        if doc_enabled("OPR"):
            _append_unique(required, "OPR")
        if doc_enabled("CF3"):
            _append_unique(required, "CF3")

    if nsd01_detected:
        classifications.append("NSD")
        notes.append("NSD01 detected in CSF - OPR and CF3 required")
        if doc_enabled("OPR"):
            _append_unique(required, "OPR")
        if doc_enabled("CF3"):
            _append_unique(required, "CF3")

    if "MRF" in found:
        notes.append("MRF present - PBC or MMC required")

    mrf_required = coe_eligibility_no and doc_enabled("MRF")
    if coe_eligibility_no:
        notes.append("COE eligibility NO - MRF and PBC or MMC required")
        if mrf_required:
            _append_unique(required, "MRF")

    if not classifications:
        classifications.append("REGULAR")

    for document in required:
        if _canonical_document(document) not in found:
            _append_unique(missing, document)

    pbc_mmc_possible = doc_enabled("PBC") or doc_enabled("MMC")
    if (
        ("MRF" in found or mrf_required)
        and pbc_mmc_possible
        and not ({"PBC", "MMC"} & found)
    ):
        _append_unique(missing, "PBC or MMC")

    return RequirementResult(
        classification=" + ".join(classifications),
        required=tuple(required),
        missing=tuple(missing),
        notes=tuple(notes),
    )


def main() -> int:
    """Small standalone smoke test required by the project architecture."""

    failures = 0

    def check(label: str, ok: bool) -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")

    complete_regular = set(BASE_REQUIREMENTS)
    result = evaluate_requirements(complete_regular)
    print(f"Classification: {result.classification}")
    print(f"Missing: {', '.join(result.missing) or 'None'}")
    check("complete regular claim has no missing docs", not result.missing)

    if get_required_base_docs is not None:
        import tempfile

        from core import claim_attachment_profile as profile

        real_profile_file = profile.PROFILE_FILE
        profile.PROFILE_FILE = Path(tempfile.mkdtemp()) / "missing_profile.json"
        profile._invalidate_cache()

        # Default profile == legacy hardcoded behavior.
        legacy = evaluate_requirements(complete_regular)
        check("default profile: base requirements unchanged",
              list(legacy.required) == list(BASE_REQUIREMENTS))
        legacy_mrf = evaluate_requirements(complete_regular | {"MRF"})
        check("default profile: MRF triggers PBC or MMC",
              "PBC or MMC" in legacy_mrf.missing)

        # SOA1 unchecked -> neither required nor reported missing.
        profile.save_overrides({"docs": {"SOA1": {"enabled": False}}, "custom_docs": {}})
        without_soa = evaluate_requirements(complete_regular)
        check("SOA1 unchecked: not required", "SOA1" not in without_soa.required)
        check("SOA1 unchecked: not missing", "SOA1" not in without_soa.missing)

        # MRF unchecked -> COE eligibility NO no longer requires MRF/PBC/MMC.
        profile.save_overrides({"docs": {"MRF": {"enabled": False}}, "custom_docs": {}})
        no_mrf = evaluate_requirements(complete_regular, coe_eligibility_no=True)
        check("MRF unchecked: MRF not required", "MRF" not in no_mrf.required)
        check("MRF unchecked: no PBC or MMC", "PBC or MMC" not in no_mrf.missing)

        # ANR conditional honors an unchecked OPR but still requires CF3.
        profile.save_overrides({"docs": {"OPR": {"enabled": False}}, "custom_docs": {}})
        anr = evaluate_requirements(complete_regular | {"ANR"})
        check("OPR unchecked: not required via ANR", "OPR" not in anr.required)
        check("OPR unchecked: CF3 still required", "CF3" in anr.required)

        profile.PROFILE_FILE = real_profile_file
        profile._invalidate_cache()

    print("RESULT:", "PASSED" if failures == 0 else f"{failures} FAILURE(S)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
