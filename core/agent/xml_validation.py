"""XML-output validation for the Claims Agent (additive enhancement).

Reuses the existing project rules — never replaces them:
    * existence per kind: ``claims_checker.has_xml`` (cf4/cf5/esoa patterns);
    * kind catalogue: ``core.xml_output_checker.REQUIRED_XML_KINDS``;
    * claim-series source: ``claims_checker.extract_claim_series`` plus the
      ``CLAIM NO. MATCH`` rule from ``build_claim_number_rows``
      (one series number shared by all three files).

Ground truth about content checks (claims_checker.py docstring): the
generated files are ENCRYPTED eClaims envelopes, so required XML
elements/attributes cannot be read from the file bytes. This module
therefore ATTEMPTS a well-formedness parse and records the outcome, but
a parse failure on an encrypted envelope is reported as
``content_not_readable`` — never invented as a schema violation and never
the sole reason to fail a claim. Non-empty + filename-series +
cross-kind match remain the deterministic validity signal.

Standalone test:
    python core/agent/xml_validation.py
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from claims_checker import CLAIM_KINDS, extract_claim_series, has_xml  # noqa: E402
from core.xml_output_checker import REQUIRED_XML_KINDS  # noqa: E402

KIND_LABELS = {"cf4": "CF4", "cf5": "CF5", "esoa": "eSOA"}

CONTENT_NOT_READABLE = (
    "content checks not performable: HBSys XML is an encrypted eClaims "
    "envelope; filename series + cross-kind match is the validity signal"
)


@dataclass
class XmlFileResult:
    """Individual result for one required XML output (task section 6)."""

    kind: str  # CF4 | CF5 | eSOA
    path: str = ""
    exists: bool = False
    non_empty: bool = False
    well_formed: bool = False
    content_note: str = ""
    series: tuple[str, ...] = ()
    claim_match: str = ""  # MATCH | MISMATCH | NOT_APPLICABLE
    error: str = ""
    valid: bool = False


@dataclass
class XmlValidationResult:
    files: dict[str, XmlFileResult] = field(default_factory=dict)
    cross_kind_match: bool = False
    overall_valid: bool = False
    missing_kinds: tuple[str, ...] = ()


def _first_xml_path(folder: Path, kind: str) -> Path | None:
    """First file in the folder that the existing has_xml rule accepts."""
    lowered = kind.lower()
    try:
        names = sorted(
            p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".xml"
        )
    except OSError:
        return None
    import re

    for name in names:
        low = name.lower()
        if lowered == "cf4" and re.search(r"(^|[_\-\s])cf4(\.xml$|[_\-\s])", low):
            return folder / name
        if lowered == "cf5" and re.search(r"(^|[_\-\s])cf5(\.xml$|[_\-\s])", low):
            return folder / name
        if lowered == "esoa" and "esoa" in low:
            return folder / name
    return None


def validate_patient_xmls(folder: str | Path) -> XmlValidationResult:
    """Validate all three required XML outputs of one patient folder."""
    root = Path(folder)
    result = XmlValidationResult()
    try:
        series_map = extract_claim_series(root)
    except OSError as exc:
        for kind in CLAIM_KINDS:
            label = KIND_LABELS[kind]
            result.files[label] = XmlFileResult(
                kind=label, error=f"cannot list folder: {exc}"
            )
        result.missing_kinds = tuple(KIND_LABELS[k] for k in CLAIM_KINDS)
        return result

    all_series: set[str] = set()
    for kind in CLAIM_KINDS:
        label = KIND_LABELS[kind]
        assert label in REQUIRED_XML_KINDS or label.upper() in {
            k.upper() for k in REQUIRED_XML_KINDS
        }, label
        entry = XmlFileResult(kind=label)
        try:
            entry.exists = bool(has_xml(root, kind))
        except OSError as exc:
            entry.error = f"existence check failed: {exc}"
            result.files[label] = entry
            continue
        if entry.exists:
            found = _first_xml_path(root, kind)
            if found is not None:
                entry.path = str(found)
                try:
                    entry.non_empty = found.stat().st_size > 0
                except OSError as exc:
                    entry.error = f"size check failed: {exc}"
                if entry.non_empty:
                    try:
                        ET.parse(str(found))
                        entry.well_formed = True
                    except ET.ParseError:
                        entry.well_formed = False
                        entry.content_note = CONTENT_NOT_READABLE
                    except OSError as exc:
                        entry.error = f"read failed: {exc}"
        entry.series = tuple(sorted(series_map.get(kind, ())))
        all_series.update(entry.series)
        result.files[label] = entry

    # Cross-kind claim-identifier match: the existing CLAIM NO. MATCH rule —
    # every present kind carries exactly one series and all agree.
    present = [e for e in result.files.values() if e.exists and e.series]
    if present and len(all_series) == 1 and all(len(e.series) == 1 for e in present):
        result.cross_kind_match = True
    for entry in result.files.values():
        if not entry.exists or not entry.series:
            entry.claim_match = "NOT_APPLICABLE"
        else:
            entry.claim_match = "MATCH" if result.cross_kind_match else "MISMATCH"
            if entry.claim_match == "MISMATCH":
                entry.error = "; ".join(
                    x for x in (entry.error, "claim numbers differ across XML files") if x
                )
        entry.valid = bool(
            entry.exists
            and entry.non_empty
            and not entry.error
            and entry.series
            and entry.claim_match != "MISMATCH"
        )

    result.missing_kinds = tuple(
        label for label in (KIND_LABELS[k] for k in CLAIM_KINDS)
        if not result.files[label].valid
    )
    result.overall_valid = not result.missing_kinds and result.cross_kind_match
    return result


if __name__ == "__main__":
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmp:
        empty = Path(tmp) / "empty"
        empty.mkdir()
        verdict = validate_patient_xmls(empty)
        assert not verdict.overall_valid and len(verdict.missing_kinds) == 3, verdict

        good = Path(tmp) / "good"
        good.mkdir()
        for kind in ("CF4", "CF5", "ESOA"):
            (good / f"NAME-260924150960_{kind}.xml").write_text(
                "<root><a>1</a></root>", encoding="utf-8"
            )
        verdict = validate_patient_xmls(good)
        assert verdict.overall_valid, verdict
        assert all(f.well_formed for f in verdict.files.values()), verdict

        bad = Path(tmp) / "bad"
        bad.mkdir()
        (bad / "NAME-111_CF4.xml").write_text("<root>", encoding="utf-8")
        (bad / "NAME-222_CF5.xml").write_text("<root/>", encoding="utf-8")
        (bad / "NAME-222_ESOA.xml").write_text("", encoding="utf-8")
        verdict = validate_patient_xmls(bad)
        assert not verdict.overall_valid, verdict
        assert verdict.files["CF4"].claim_match == "MISMATCH", verdict
        assert not verdict.files["eSOA"].non_empty, verdict
    print("xml_validation standalone check passed")
