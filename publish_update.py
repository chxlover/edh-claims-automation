# -*- coding: utf-8 -*-
"""
Publish EDH Claims Automation updates to NAS.

Run this on the main/developer PC only. It creates manifest.json from code and
documentation files only. Patient/user data folders are intentionally excluded.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from updater import get_network_source


LOCAL_ROOT = Path(__file__).resolve().parent
NAS_PATH = Path(get_network_source())
VERSION_FILE = NAS_PATH / "version.txt"
MANIFEST_FILE = NAS_PATH / "manifest.json"

INCLUDE_EXTENSIONS = {".py", ".txt", ".bat", ".md"}
INCLUDE_NAMES = {"requirements.txt"}

EXCLUDE_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "backup_originals",
    "claims_checker_results",
    "date_fill_hbsys/logs",
    "logs",
    "merge_input",
    "merge_output",
    "output",
    "pdfs",
    "review_staging",
    "scans",
    "signatures",
    "unknown_training_backup",
}

EXCLUDE_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pyc",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".csv",
    ".log",
    ".xlsx",
    ".xlsm",
    ".tmp",
}

EXCLUDE_NAMES = {
    "claims_gui_config.json",
    "doctors_config.json",
    "signature_check_config.json",
    "unknown_review_log.json",
    "unknown_training_data.json",
    "update_config.json",
    "review_queue.json",
    "manifest.json",
}


def _normalized_parts(path: Path) -> set[str]:
    rel = path.relative_to(LOCAL_ROOT).as_posix()
    parts = set(Path(rel).parts)
    for excluded in EXCLUDE_DIRS:
        if "/" in excluded and rel.startswith(excluded + "/"):
            parts.add(excluded)
    return parts


def should_publish(path: Path) -> bool:
    if not path.is_file():
        return False
    rel = path.relative_to(LOCAL_ROOT).as_posix()
    parts = _normalized_parts(path)
    if parts & EXCLUDE_DIRS:
        return False
    if path.name in EXCLUDE_NAMES:
        return False
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return False
    if path.name in INCLUDE_NAMES:
        return True
    if path.suffix.lower() in INCLUDE_EXTENSIONS:
        return True
    if rel.startswith("core/") or rel.startswith("gui/") or rel.startswith("date_fill_hbsys/"):
        return path.suffix.lower() in INCLUDE_EXTENSIONS
    return False


def get_version(path: Path) -> float:
    try:
        return float(path.read_text(encoding="utf-8").strip())
    except Exception:
        return 0.0


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(version: float) -> dict:
    files = []
    for path in sorted(LOCAL_ROOT.rglob("*")):
        if not should_publish(path):
            continue
        rel = path.relative_to(LOCAL_ROOT).as_posix()
        stat = path.stat()
        files.append(
            {
                "path": rel,
                "size": stat.st_size,
                "sha256": file_sha256(path),
            }
        )
    return {
        "version": version,
        "published_at": datetime.now().isoformat(timespec="seconds"),
        "files": files,
        "excluded_data_folders": sorted(EXCLUDE_DIRS),
    }


def main() -> int:
    print("=" * 70)
    print("EDH Claims Automation System - Publish Update to NAS")
    print("=" * 70)
    print(f"NAS source: {NAS_PATH}")

    if not NAS_PATH.exists():
        print("\nERROR: Cannot access NAS update folder.")
        print("Check update_config.json or network access.")
        input("\nPress Enter to exit.")
        return 1

    current = get_version(VERSION_FILE)
    suggested = round(current + 0.1, 1)
    raw = input(f"\nNew version (Enter = v{suggested}): ").strip()
    new_version = float(raw) if raw else suggested

    if new_version <= current:
        confirm = input(f"Warning: v{new_version} <= v{current}. Continue? (y/n): ").strip().lower()
        if confirm != "y":
            return 0

    manifest = build_manifest(new_version)
    ok = 0
    errors = 0

    print(f"\nPublishing v{new_version} with {len(manifest['files'])} code/doc file(s)...\n")
    for item in manifest["files"]:
        rel = item["path"]
        src = LOCAL_ROOT / rel
        dest = NAS_PATH / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            print(f"OK     {rel}")
            ok += 1
        except Exception as exc:
            print(f"ERROR  {rel}: {exc}")
            errors += 1

    VERSION_FILE.write_text(str(new_version), encoding="utf-8")
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\nVersion: v{new_version}")
    print(f"Copied : {ok}")
    print(f"Errors : {errors}")
    print("\nPatient folders/data were excluded from the manifest.")
    input("\nPress Enter to exit.")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
