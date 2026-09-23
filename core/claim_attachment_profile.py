"""Claim Attachment Profile - single source of truth for claim attachments.

One checkbox per document (Preferences -> Claim Attachment Checklist...):

    CHECKED   -> the document is required by the Claims Checker AND is
                 uploaded to HBSys by the Claim Attachments uploader.
    UNCHECKED -> the document is NOT required AND its files are moved out
                 of the patient folder into the backup folder
                 claims_checker_results\\_upload_backup\\<patient>\\
                 BEFORE the uploader attaches anything, so they are never
                 uploaded.  ("Not required and not uploaded" - owner
                 decision 2026-09-23.)

The profile also owns the filename-suffix -> HBSys doc type mapping that
core/claim_attachments_doc_type.py types into the HBSys attachments grid,
so a PhilHealth requirement change (drop a document, rename a doc type,
add a new document) needs ZERO code edits.

Storage:
    claim_attachment_profile.json (project root) - local configuration,
    same policy as document_detection_rules.json.  The file only stores
    deltas: for built-in documents the editable fields (enabled,
    hbsys_doctype); custom documents are stored as full entries.  Deleting
    the file (or "Restore Defaults") restores the EXACT behavior of the
    hardcoded maps (PDF_SUFFIXES / XML_SUFFIXES / BASE_REQUIREMENTS).

Safety:
    - A corrupt/unreadable profile NEVER crashes processing: every reader
      falls back to the built-in defaults.
    - The HBSys doc type is typed verbatim into HBSys by keyboard
      automation, so it is validated strictly (uppercase alphanumerics
      only).  Duplicate doc types are allowed on purpose (SOA1 and SOA2
      both map to "SOA" in HBSys).
    - Excluded files are MOVED, never deleted, and a per-patient
      _excluded_manifest.json records every move for the Restore button.
    - The backup folder is a SIBLING of READY with a "_" prefix, so the
      claims checker, the uploader and the archive/transmit job all
      ignore it.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_FILE = BASE_DIR / "claim_attachment_profile.json"
SCHEMA_VERSION = 1

CLAIMS_RESULTS_DIR = BASE_DIR / "claims_checker_results"
DEFAULT_BACKUP_ROOT = CLAIMS_RESULTS_DIR / "_upload_backup"
DEFAULT_SEARCH_ROOTS = (
    CLAIMS_RESULTS_DIR / "READY",
    CLAIMS_RESULTS_DIR / "READY_ARCHIVED",
)

MANIFEST_NAME = "_excluded_manifest.json"

_DOCTYPE_RE = re.compile(r"^[A-Z0-9]{2,12}$")
_CODE_RE = re.compile(r"^[A-Za-z0-9]{2,12}$")
_SUFFIX_RE = re.compile(r"^[A-Za-z0-9]{2,8}$")

# ---------------------------------------------------------------
# DEFAULT DOCUMENTS - the exact equivalent of the previously
# hardcoded tables:
#   * BASE_REQUIREMENTS in core/claims_requirement_rules.py
#     (conditional=False documents, in this order)
#   * PDF_SUFFIXES / XML_SUFFIXES in core/claim_attachments_doc_type.py
#     (suffix -> hbsys_doctype)
#   * the found-document scan order of claims_checker.get_found_items
#     (dict order below)
# "conditional" documents are never part of the base requirement list;
# they become required only through the conditional rules in
# core/claims_requirement_rules.py (or, for CF2, only classify the claim).
# ---------------------------------------------------------------
DEFAULT_DOCS: Dict[str, Dict[str, Any]] = {
    "CSF":  {"kind": "pdf", "suffix": "CSF",  "hbsys_doctype": "CSF", "conditional": False},
    "COE":  {"kind": "pdf", "suffix": "COE",  "hbsys_doctype": "COE", "conditional": False},
    "SOA1": {"kind": "pdf", "suffix": "SOA1", "hbsys_doctype": "SOA", "conditional": False},
    "SOA2": {"kind": "pdf", "suffix": "SOA2", "hbsys_doctype": "SOA", "conditional": False},
    "CF2":  {"kind": "pdf", "suffix": "CF2",  "hbsys_doctype": "CF2", "conditional": True},
    "CF3":  {"kind": "pdf", "suffix": "CF3",  "hbsys_doctype": "CF3", "conditional": True},
    "MRF":  {"kind": "pdf", "suffix": "MRF",  "hbsys_doctype": "MRF", "conditional": True},
    "PBC":  {"kind": "pdf", "suffix": "PBC",  "hbsys_doctype": "PBC", "conditional": True},
    "MMC":  {"kind": "pdf", "suffix": "MMC",  "hbsys_doctype": "MMC", "conditional": True},
    "ANR":  {"kind": "pdf", "suffix": "ANR",  "hbsys_doctype": "ANR", "conditional": True},
    "OPR":  {"kind": "pdf", "suffix": "OPR",  "hbsys_doctype": "OPR", "conditional": True},
    "DTR":  {"kind": "pdf", "suffix": "DTR",  "hbsys_doctype": "DTR", "conditional": False},
    "CF4":  {"kind": "xml", "suffix": "CF4",  "hbsys_doctype": "CF4", "conditional": False},
    "CF5":  {"kind": "xml", "suffix": "CF5",  "hbsys_doctype": "CF5", "conditional": False},
    "eSOA": {"kind": "xml", "suffix": "ESOA", "hbsys_doctype": "ESA", "conditional": False},
}

_SAVE_COUNTER = 0
_PROFILE_CACHE: Dict[str, Any] = {"mtime": None, "data": None, "merged": None, "error": ""}


# ---------------------------------------------------------------
# Validation
# ---------------------------------------------------------------

def validate_doctype(value: Any) -> str:
    """Return '' when the HBSys doc type is valid, else an error message.

    Strict on purpose: the value is typed verbatim into the HBSys grid by
    keyboard automation, so a typo would fail silently during upload.
    """
    text = str(value or "").strip().upper()
    if not _DOCTYPE_RE.match(text):
        return (
            f"HBSys doc type {value!r} is invalid: use 2-12 uppercase "
            f"letters/digits only (A-Z, 0-9)"
        )
    return ""


def _validate_custom_entry(code: str, raw: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    """Validate one custom document entry from the JSON file."""
    if not _CODE_RE.match(code):
        return None, f"custom doc {code!r}: invalid code (skipped)"
    if code.upper() in {c.upper() for c in DEFAULT_DOCS}:
        return None, f"custom doc {code!r}: collides with a built-in document (skipped)"
    if not isinstance(raw, dict):
        return None, f"custom doc {code}: expected an object (skipped)"
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in ("pdf", "xml"):
        return None, f"custom doc {code}: kind must be 'pdf' or 'xml' (skipped)"
    suffix = str(raw.get("suffix") or "").strip().upper()
    if not _SUFFIX_RE.match(suffix):
        return None, f"custom doc {code}: invalid suffix {suffix!r} (skipped)"
    doctype = str(raw.get("hbsys_doctype") or "").strip().upper()
    error = validate_doctype(doctype)
    if error:
        return None, f"custom doc {code}: {error} (skipped)"
    entry = {
        "kind": kind,
        "suffix": suffix,
        "hbsys_doctype": doctype,
        "conditional": bool(raw.get("conditional", False)),
        "enabled": bool(raw.get("enabled", True)),
        "custom": True,
    }
    return entry, ""


def validate_overrides(data: Any) -> Tuple[Dict[str, Any], str]:
    """Validate a raw overrides dict. Returns (cleaned, error_message)."""
    errors: List[str] = []
    cleaned: Dict[str, Any] = {"docs": {}, "custom_docs": {}}
    if data is None:
        return cleaned, ""
    if not isinstance(data, dict):
        return cleaned, "profile overrides: expected a JSON object"

    docs_raw = data.get("docs") or {}
    if not isinstance(docs_raw, dict):
        errors.append("docs: expected an object")
        docs_raw = {}
    for code, raw in docs_raw.items():
        if code not in DEFAULT_DOCS:
            errors.append(f"docs.{code!r}: unknown built-in document (skipped)")
            continue
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            errors.append(f"docs.{code}: expected an object (skipped)")
            continue
        entry: Dict[str, Any] = {}
        if "enabled" in raw:
            entry["enabled"] = bool(raw["enabled"])
        if "hbsys_doctype" in raw:
            doctype = str(raw.get("hbsys_doctype") or "").strip().upper()
            error = validate_doctype(doctype)
            if error:
                errors.append(f"docs.{code}: {error} (field skipped)")
            else:
                entry["hbsys_doctype"] = doctype
        cleaned["docs"][code] = entry

    custom_raw = data.get("custom_docs") or {}
    if not isinstance(custom_raw, dict):
        errors.append("custom_docs: expected an object")
        custom_raw = {}
    used_suffixes = {d["suffix"].upper() for d in DEFAULT_DOCS.values()}
    for code, raw in custom_raw.items():
        code_key = str(code or "").strip()
        entry, error = _validate_custom_entry(code_key, raw)
        if entry is None:
            errors.append(error)
            continue
        if entry["suffix"].upper() in used_suffixes:
            errors.append(f"custom doc {code_key}: suffix {entry['suffix']!r} already used (skipped)")
            continue
        used_suffixes.add(entry["suffix"].upper())
        cleaned["custom_docs"][code_key] = entry

    return cleaned, "; ".join(errors)



# ---------------------------------------------------------------
# Persistence (load / save / restore) with mtime cache
# ---------------------------------------------------------------

def load_overrides(path: Optional[Path] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """Load raw overrides from disk. Returns (data_or_None, error).

    None + empty error -> file missing (first run; caller uses defaults).
    None + error text  -> corrupt/unreadable; callers MUST fall back to the
    built-in defaults (processing must never crash on a bad config file).
    """
    path = Path(path) if path is not None else Path(PROFILE_FILE)
    if not path.is_file():
        return None, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"claim_attachment_profile.json is unreadable/corrupt: {exc}"
    if not isinstance(data, dict):
        return None, "claim_attachment_profile.json: expected a JSON object"
    if data.get("schema_version") != SCHEMA_VERSION:
        return None, (
            f"claim_attachment_profile.json: unsupported schema_version "
            f"{data.get('schema_version')!r} (expected {SCHEMA_VERSION})"
        )
    cleaned, errors = validate_overrides(data)
    return cleaned, errors


def save_overrides(data: Dict[str, Any], path: Optional[Path] = None) -> str:
    """Validate + persist overrides. Returns an error message ('' on success)."""
    global _SAVE_COUNTER
    cleaned, message = validate_overrides(data)
    try:
        path = Path(path) if path is not None else Path(PROFILE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"schema_version": SCHEMA_VERSION, **cleaned},
                indent=4,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        return (message + "; " if message else "") + f"could not save profile: {exc}"
    _SAVE_COUNTER += 1
    _invalidate_cache()
    return message


def restore_defaults(path: Optional[Path] = None) -> str:
    """Delete the overrides file: the built-in defaults become active again.

    This is the documented rollback path - with no JSON file the effective
    profile is byte-for-byte the previously hardcoded behavior.
    """
    global _SAVE_COUNTER
    try:
        path = Path(path) if path is not None else Path(PROFILE_FILE)
        if path.is_file():
            path.unlink()
    except OSError as exc:
        return f"could not remove profile file: {exc}"
    _SAVE_COUNTER += 1
    _invalidate_cache()
    return ""


def _invalidate_cache() -> None:
    _PROFILE_CACHE["mtime"] = None
    _PROFILE_CACHE["data"] = None
    _PROFILE_CACHE["merged"] = None
    _PROFILE_CACHE["error"] = ""


def _merge_profile(data: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Merge validated overrides over the built-in defaults."""
    merged: Dict[str, Dict[str, Any]] = {}
    overrides = (data or {}).get("docs") or {}
    for code, default in DEFAULT_DOCS.items():
        entry = dict(default)
        entry["enabled"] = True
        entry["custom"] = False
        raw = overrides.get(code)
        if isinstance(raw, dict):
            if "enabled" in raw:
                entry["enabled"] = bool(raw["enabled"])
            if raw.get("hbsys_doctype"):
                entry["hbsys_doctype"] = str(raw["hbsys_doctype"]).strip().upper()
        merged[code] = entry
    for code, raw in ((data or {}).get("custom_docs") or {}).items():
        if isinstance(raw, dict):
            merged[code] = dict(raw)
    return merged


def _get_data() -> Tuple[Dict[str, Any], str]:
    """Load overrides once per file-change (mtime-checked, never per call).

    "data is None" forces a reload: the cache starts empty and a missing
    profile file also yields mtime=None, so without this guard the very
    first call could cache an empty profile (same collision class as the
    2026-09-23 document_detection_rules bug).
    """
    try:
        mtime = PROFILE_FILE.stat().st_mtime if PROFILE_FILE.is_file() else None
    except OSError:
        mtime = None
    if _PROFILE_CACHE["mtime"] != mtime or _PROFILE_CACHE["data"] is None:
        if mtime is None:
            data, error = {"docs": {}, "custom_docs": {}}, ""
        else:
            loaded, error = load_overrides()
            data = loaded if loaded is not None else {"docs": {}, "custom_docs": {}}
        _PROFILE_CACHE["mtime"] = mtime
        _PROFILE_CACHE["data"] = data
        _PROFILE_CACHE["merged"] = None
        _PROFILE_CACHE["error"] = error
    return _PROFILE_CACHE["data"], _PROFILE_CACHE["error"]


def get_profile() -> Dict[str, Dict[str, Any]]:
    """Effective document profile (defaults + overrides), ordered dict."""
    data, _error = _get_data()
    if _PROFILE_CACHE["merged"] is None:
        _PROFILE_CACHE["merged"] = _merge_profile(data)
    return _PROFILE_CACHE["merged"]


def profile_revision() -> Tuple[Any, ...]:
    """Cheap change token for downstream caches (doc type lookup tables)."""
    try:
        stat = PROFILE_FILE.stat()
        stamp: Tuple[Any, ...] = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = (None, None)
    return stamp + (_SAVE_COUNTER,)


# ---------------------------------------------------------------
# Query API (used by claims_requirement_rules / claim_attachments_doc_type
# / claim_attachments_uploader / claims_checker / the checklist dialog)
# ---------------------------------------------------------------

def requirement_name(code: str, entry: Optional[Dict[str, Any]] = None) -> str:
    """Canonical requirement name, e.g. 'SOA1', 'CF4 XML', 'eSOA XML'."""
    if entry is None:
        entry = get_profile().get(code) or {}
    return f"{code} XML" if entry.get("kind") == "xml" else code


def is_enabled(code: str) -> bool:
    entry = get_profile().get(code)
    return bool(entry and entry.get("enabled", True))


def get_required_base_docs() -> List[str]:
    """Enabled non-conditional documents, as canonical requirement names."""
    return [
        requirement_name(code, entry)
        for code, entry in get_profile().items()
        if entry.get("enabled", True) and not entry.get("conditional", False)
    ]


def get_enabled_requirement_names() -> set:
    """Canonical requirement names of every enabled document."""
    return {
        requirement_name(code, entry)
        for code, entry in get_profile().items()
        if entry.get("enabled", True)
    }


def get_doctype_maps() -> Tuple[Dict[str, str], Dict[str, str]]:
    """(pdf_map, xml_map): suffix stem -> HBSys doc type, ENABLED docs only.

    Disabled documents are deliberately absent: their files were moved to
    the backup folder before upload, so if one still shows up in the HBSys
    grid the uploader's never-guess ABORT must fire instead of typing a
    doc type for a document PhilHealth no longer wants.
    """
    pdf_map: Dict[str, str] = {}
    xml_map: Dict[str, str] = {}
    for _code, entry in get_profile().items():
        if not entry.get("enabled", True):
            continue
        target = pdf_map if entry.get("kind") == "pdf" else xml_map
        target[str(entry["suffix"]).upper()] = str(entry["hbsys_doctype"]).upper()
    return pdf_map, xml_map


def get_excluded_docs() -> Dict[str, Dict[str, Any]]:
    """Disabled documents (the ones whose files are moved to backup)."""
    return {
        code: entry
        for code, entry in get_profile().items()
        if not entry.get("enabled", True)
    }


def doc_matches_file(entry: Dict[str, Any], file: Path) -> bool:
    """True when *file* belongs to the document described by *entry*.

    Mirrors the matching rules of claims_checker.has_pdf / has_xml so the
    checker, the exclusion step and the uploader always agree.
    """
    name = file.name.lower()
    suffix = str(entry.get("suffix") or "").strip().lower()
    if not suffix:
        return False
    if entry.get("kind") == "pdf":
        if file.suffix.lower() != ".pdf":
            return False
        stem = file.stem.strip().lower()
        if stem == suffix:
            return True
        # Legacy special case from claims_checker.has_pdf: merged SOA2
        # files may carry extra text after the stem ("SOA2_page2.pdf").
        if suffix == "soa2" and stem.startswith("soa2"):
            return True
        return False
    if file.suffix.lower() != ".xml":
        return False
    # Legacy special case from claims_checker.has_xml.
    if suffix == "esoa":
        return "esoa" in name
    pattern = r"(^|[_\-\s])" + re.escape(suffix) + r"(\.xml$|[_\-\s])"
    return re.search(pattern, name) is not None


def last_profile_error() -> str:
    """Validation/load error of the active profile file ('' when clean)."""
    _get_data()
    return _PROFILE_CACHE["error"]



# ---------------------------------------------------------------
# Exclusion (per-patient move to the _upload_backup sibling folder)
# ---------------------------------------------------------------

def backup_root_for(ready_dir: Path) -> Path:
    """Backup root next to the READY dir (invisible to checker/uploader)."""
    return Path(ready_dir).parent / "_upload_backup"


def _unique_destination(target: Path) -> Path:
    if not target.exists():
        return target
    for index in range(2, 100):
        candidate = target.with_name(f"{target.stem} ({index}){target.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"could not find a free backup name for {target.name}")


def exclude_files(patient_folder: Path, backup_root: Path) -> List[str]:
    """Move files of DISABLED documents out of *patient_folder*.

    Returns the list of moved file names.  A per-patient
    _excluded_manifest.json is (re)written in the backup folder so the
    Restore button can put everything back.  Nothing is ever deleted.
    """
    patient_folder = Path(patient_folder)
    backup_root = Path(backup_root)
    excluded = get_excluded_docs()
    if not excluded or not patient_folder.is_dir():
        return []

    moved: List[str] = []
    dest_dir = backup_root / patient_folder.name
    for file in sorted(patient_folder.iterdir()):
        if not file.is_file():
            continue
        for _code, entry in excluded.items():
            if doc_matches_file(entry, file):
                dest_dir.mkdir(parents=True, exist_ok=True)
                target = _unique_destination(dest_dir / file.name)
                shutil.move(str(file), str(target))
                moved.append(file.name)
                break

    if moved:
        manifest = {
            "patient_folder": patient_folder.name,
            "moved_at": datetime.now().isoformat(timespec="seconds"),
            "files": sorted(moved),
        }
        try:
            (dest_dir / MANIFEST_NAME).write_text(
                json.dumps(manifest, indent=4, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass
    return moved


def restore_all_excluded(
    backup_root: Path = DEFAULT_BACKUP_ROOT,
    search_roots: Optional[Tuple[Path, ...]] = None,
) -> Dict[str, List[str]]:
    """Move every backed-up file back into its patient folder.

    The patient folder is searched in *search_roots* (READY first, then
    READY_ARCHIVED) so restore also works after archiving.  Patients whose
    folder no longer exists are left untouched and reported as SKIPPED.
    Returns {patient_folder_name: [results...]}.
    """
    backup_root = Path(backup_root)
    roots = tuple(Path(r) for r in (search_roots or DEFAULT_SEARCH_ROOTS))
    results: Dict[str, List[str]] = {}
    if not backup_root.is_dir():
        return results

    for patient_backup in sorted(backup_root.iterdir()):
        if not patient_backup.is_dir():
            continue
        target_dir: Optional[Path] = None
        for root in roots:
            candidate = root / patient_backup.name
            if candidate.is_dir():
                target_dir = candidate
                break
        messages: List[str] = []
        if target_dir is None:
            messages.append("SKIPPED: patient folder not found in READY/READY_ARCHIVED")
            results[patient_backup.name] = messages
            continue
        for file in sorted(patient_backup.iterdir()):
            if not file.is_file() or file.name == MANIFEST_NAME:
                continue
            target = _unique_destination(target_dir / file.name)
            shutil.move(str(file), str(target))
            messages.append(file.name)
        manifest = patient_backup / MANIFEST_NAME
        try:
            if manifest.is_file():
                manifest.unlink()
            patient_backup.rmdir()  # only succeeds when empty
        except OSError:
            pass
        results[patient_backup.name] = messages
    return results


# ---------------------------------------------------------------
# Standalone self-tests (project rule: every core module has one)
# ---------------------------------------------------------------

LEGACY_PDF_SUFFIXES = {
    "COE": "COE", "CSF": "CSF", "DTR": "DTR", "SOA1": "SOA", "SOA2": "SOA",
    "MRF": "MRF", "PBC": "PBC", "MMC": "MMC", "OPR": "OPR", "ANR": "ANR",
    "CF3": "CF3", "CF2": "CF2",
}
LEGACY_XML_SUFFIXES = {"CF4": "CF4", "CF5": "CF5", "ESOA": "ESA"}
LEGACY_BASE_REQUIREMENTS = [
    "CSF", "COE", "SOA1", "SOA2", "DTR", "CF4 XML", "CF5 XML", "eSOA XML",
]


def main() -> int:
    global PROFILE_FILE
    import tempfile

    failures = 0

    def check(label: str, ok: bool) -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")

    real_profile_file = PROFILE_FILE

    # 1. Missing file -> defaults identical to the legacy hardcoded maps.
    PROFILE_FILE = Path(tempfile.mkdtemp()) / "missing_profile.json"
    _invalidate_cache()
    check(
        "default base requirements == legacy BASE_REQUIREMENTS",
        get_required_base_docs() == LEGACY_BASE_REQUIREMENTS,
    )
    pdf_map, xml_map = get_doctype_maps()
    check("default pdf map == legacy PDF_SUFFIXES", pdf_map == LEGACY_PDF_SUFFIXES)
    check("default xml map == legacy XML_SUFFIXES", xml_map == LEGACY_XML_SUFFIXES)

    # 2. Save/load overrides: disable SOA1, add a custom document.
    tmp = Path(tempfile.mkdtemp())
    PROFILE_FILE = tmp / "claim_attachment_profile.json"
    _invalidate_cache()
    error = save_overrides({
        "docs": {"SOA1": {"enabled": False}},
        "custom_docs": {
            "XRAY": {"kind": "pdf", "suffix": "XRAY", "hbsys_doctype": "XRAY",
                     "conditional": False, "enabled": True},
        },
    })
    check("save_overrides clean", error == "")
    check("SOA1 disabled", not is_enabled("SOA1"))
    check("SOA1 not required", "SOA1" not in get_required_base_docs())
    check("SOA1 absent from doctype map", "SOA1" not in get_doctype_maps()[0])
    check("custom XRAY in doctype map", get_doctype_maps()[0].get("XRAY") == "XRAY")
    check("custom XRAY required", "XRAY" in get_required_base_docs())

    # 3. Invalid inputs are rejected without breaking the active profile.
    error = save_overrides({"docs": {"COE": {"hbsys_doctype": "bad type!"}}, "custom_docs": {}})
    check("invalid doctype rejected", "invalid" in error.lower())
    check("invalid doctype field skipped", get_doctype_maps()[0].get("COE") == "COE")
    error = save_overrides({"docs": {}, "custom_docs": {
        "CSF": {"kind": "pdf", "suffix": "CSF2", "hbsys_doctype": "CSF"}}})
    check("custom code colliding with built-in rejected", "collides" in error)
    error = save_overrides({"docs": {}, "custom_docs": {
        "DUP": {"kind": "pdf", "suffix": "SOA1", "hbsys_doctype": "DUP"}}})
    check("custom suffix collision rejected", "already used" in error)

    # 4. Corrupt file -> defaults fallback + error reported.
    PROFILE_FILE.write_text("{ not json", encoding="utf-8")
    _invalidate_cache()
    check("corrupt file -> default maps", get_doctype_maps()[0] == LEGACY_PDF_SUFFIXES)
    check("corrupt file -> error reported", "corrupt" in last_profile_error())

    # 5. Exclusion + restore round-trip in a temp tree.
    save_overrides({"docs": {"SOA1": {"enabled": False}, "eSOA": {"enabled": False}},
                    "custom_docs": {}})
    root = tmp / "results"
    ready = root / "READY"
    patient = ready / "DELA CRUZ, JUAN - 000000000000001 - ADM20260801_DIS20260805"
    patient.mkdir(parents=True)
    for name in ["CSF.pdf", "COE.pdf", "SOA1.pdf", "SOA2.pdf", "DTR.pdf",
                 "DELACRUZ-123_CF4.xml", "DELACRUZ-123_CF5.xml", "DELACRUZ-123_eSOA.xml"]:
        (patient / name).write_text("x", encoding="utf-8")
    backup_root = backup_root_for(ready)
    check("backup root is _upload_backup sibling", backup_root.name == "_upload_backup")
    moved = exclude_files(patient, backup_root)
    check("exclude moved SOA1 + eSOA only",
          sorted(moved) == ["DELACRUZ-123_eSOA.xml", "SOA1.pdf"])
    check("SOA1.pdf gone from patient folder", not (patient / "SOA1.pdf").exists())
    check("backup copy exists", (backup_root / patient.name / "SOA1.pdf").is_file())
    check("manifest written", (backup_root / patient.name / MANIFEST_NAME).is_file())
    check("SOA2.pdf untouched", (patient / "SOA2.pdf").is_file())

    archived = root / "READY_ARCHIVED"
    archived.mkdir()
    shutil.move(str(patient), str(archived / patient.name))
    results = restore_all_excluded(backup_root, (ready, archived))
    check("restore found patient in READY_ARCHIVED",
          sorted(results.get(patient.name, [])) == ["DELACRUZ-123_eSOA.xml", "SOA1.pdf"])
    check("SOA1.pdf restored", (archived / patient.name / "SOA1.pdf").is_file())
    check("backup folder removed", not (backup_root / patient.name).exists())

    # 6. Restore defaults -> file gone, defaults active again.
    error = restore_defaults()
    check("restore_defaults clean", error == "" and not PROFILE_FILE.is_file())
    check("defaults active again", get_doctype_maps()[1] == LEGACY_XML_SUFFIXES)

    PROFILE_FILE = real_profile_file
    _invalidate_cache()
    print("RESULT:", "PASSED" if failures == 0 else f"{failures} FAILURE(S)")
    raise SystemExit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()

