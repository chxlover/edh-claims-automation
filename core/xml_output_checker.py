"""Check which HBSys XML outputs (CF4/CF5/eSOA) already exist in a patient folder.

Filesystem-only, read-only module used by the XML generator clicker to skip
patients whose three XML outputs were already generated.

Matching rule: a file counts as XML kind CF4/CF5/ESOA when its name ends with
``_CF4.xml``, ``_CF5.xml``, or ``_ESOA.xml`` (case-insensitive) — the same
pattern the background auto-copy service accepts.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path


REQUIRED_XML_KINDS: frozenset[str] = frozenset({"CF4", "CF5", "ESOA"})

XML_KIND_PATTERN = re.compile(r"_([A-Za-z0-9]+)\.xml$", re.IGNORECASE)


def xml_kind_from_filename(filename: object) -> str | None:
    """Return 'CF4'/'CF5'/'ESOA' when the filename is a supported XML output."""
    match = XML_KIND_PATTERN.search(Path(str(filename or "")).name)
    if not match:
        return None
    kind = match.group(1).upper()
    return kind if kind in REQUIRED_XML_KINDS else None


def find_existing_xml_kinds(folder: Path) -> set[str]:
    """Scan one patient output folder and return the XML kinds present."""
    kinds: set[str] = set()
    if not folder.is_dir():
        return kinds

    for path in sorted(folder.iterdir(), key=lambda item: item.name.upper()):
        if not path.is_file():
            continue
        kind = xml_kind_from_filename(path.name)
        if kind:
            kinds.add(kind)
    return kinds


def missing_xml_kinds(existing: set[str]) -> set[str]:
    """Return the required XML kinds that are NOT in ``existing``."""
    return REQUIRED_XML_KINDS - existing


def is_xml_complete(existing: set[str]) -> bool:
    """True only when all three required XML kinds are present."""
    return not missing_xml_kinds(existing)


def format_kinds(kinds: set[str]) -> str:
    """Stable display string like 'CF4+CF5' (or '' when empty)."""
    return "+".join(sorted(kinds))


def _run_self_test() -> int:
    """Standalone test with fake patient folders containing 0-3 XML files."""
    failures: list[str] = []

    def check(label: str, actual: object, expected: object) -> None:
        ok = actual == expected
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {actual!r} (expected {expected!r})")
        if not ok:
            failures.append(label)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        # Case 1: empty folder.
        empty = base / "empty"
        empty.mkdir()
        check("empty folder kinds", find_existing_xml_kinds(empty), set())
        check("empty folder complete", is_xml_complete(find_existing_xml_kinds(empty)), False)

        # Case 2: all three XMLs present (mixed case names).
        full = base / "full"
        full.mkdir()
        (full / "DELA CRUZ, JUAN S - 900000001234567_CF4.xml").write_text("<cf4/>")
        (full / "DELA CRUZ, JUAN S - 900000001234567_cf5.XML").write_text("<cf5/>")
        (full / "DELA CRUZ, JUAN S - 900000001234567_Esoa.xml").write_text("<esoa/>")
        existing_full = find_existing_xml_kinds(full)
        check("full folder kinds", existing_full, {"CF4", "CF5", "ESOA"})
        check("full folder missing", missing_xml_kinds(existing_full), set())
        check("full folder complete", is_xml_complete(existing_full), True)

        # Case 3: only CF4 exists.
        partial = base / "partial"
        partial.mkdir()
        (partial / "sample_CF4.xml").write_text("<cf4/>")
        existing_partial = find_existing_xml_kinds(partial)
        check("partial folder kinds", existing_partial, {"CF4"})
        check("partial folder missing", missing_xml_kinds(existing_partial), {"CF5", "ESOA"})

        # Case 4: non-XML files and unrelated XMLs are ignored; subfolders skipped.
        noisy = base / "noisy"
        noisy.mkdir()
        (noisy / "CSF.pdf").write_text("pdf")
        (noisy / "notes.txt").write_text("text")
        (noisy / "OTHER.xml").write_text("<other/>")
        (noisy / "subdir_ESOA.xml").mkdir()  # directory named like an XML file
        check("noisy folder kinds", find_existing_xml_kinds(noisy), set())

        # Case 5: non-existent folder is safe.
        check(
            "missing folder kinds",
            find_existing_xml_kinds(base / "does_not_exist"),
            set(),
        )

        # Case 6: helpers.
        check("format empty", format_kinds(set()), "")
        check("format two", format_kinds({"ESOA", "CF4"}), "CF4+ESOA")
        check("kind from name", xml_kind_from_filename("X_CF4.xml"), "CF4")
        check("kind from junk", xml_kind_from_filename("report.pdf"), None)

    if failures:
        print(f"\nSELF TEST FAILED ({len(failures)}): {failures}")
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Scan a real folder when given as argument (read-only inspection).
        target = Path(sys.argv[1])
        kinds = find_existing_xml_kinds(target)
        print(f"{target}: existing={format_kinds(kinds) or 'NONE'}")
        print(f"missing={format_kinds(missing_xml_kinds(kinds)) or 'NONE'}")
        print(f"complete={is_xml_complete(kinds)}")
        raise SystemExit(0)
    raise SystemExit(_run_self_test())
