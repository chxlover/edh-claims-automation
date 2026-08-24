"""One-time, isolated installer for an EDH Claims workstation."""

from __future__ import annotations

import argparse
import subprocess
import sys
import venv
from pathlib import Path

from updater import ensure_external_requirements


ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
REQUIREMENTS = ROOT / "requirements.txt"

REQUIRED_FOLDERS = (
    "scans",
    "output",
    "backup_originals",
    "review_staging",
    "claims_checker_results",
    "claims_checker_results/READY",
    "claims_checker_results/READY_WITH_REVIEW",
    "claims_checker_results/INCOMPLETE",
    "claims_checker_results/READY_ARCHIVED",
    "merge_input",
    "merge_output",
    "pdfs",
    "signatures",
    "reports",
    "date_fill_hbsys/logs",
    "date_fill_hbsys/logs/testing",
)


def ensure_folders() -> None:
    for relative in REQUIRED_FOLDERS:
        path = ROOT / relative
        path.mkdir(parents=True, exist_ok=True)
        print(f"[Folders] OK: {path}")


def run_checked(command: list[str]) -> None:
    print("[Setup] " + " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def ensure_supported_python() -> None:
    if sys.version_info < (3, 14):
        raise RuntimeError(
            "Python 3.14 or newer is required. "
            f"Current version: {sys.version.split()[0]}"
        )
    if sys.maxsize <= 2**32:
        raise RuntimeError("64-bit Python is required.")
    print(f"[Python] OK: {sys.version.split()[0]} 64-bit")


def ensure_virtual_environment() -> None:
    if VENV_PYTHON.is_file():
        print(f"[Virtual environment] OK: {VENV_DIR}")
        return
    print(f"[Virtual environment] Creating: {VENV_DIR}")
    venv.EnvBuilder(with_pip=True, clear=False).create(VENV_DIR)
    if not VENV_PYTHON.is_file():
        raise RuntimeError("Virtual environment creation did not produce python.exe")


def install_python_requirements() -> None:
    if not REQUIREMENTS.is_file():
        raise FileNotFoundError(f"Requirements file not found: {REQUIREMENTS}")
    run_checked(
        [
            str(VENV_PYTHON),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "pip",
            "setuptools",
            "wheel",
        ]
    )
    # Installing the complete file makes pip enforce version constraints and
    # dependencies, unlike checking package names one at a time.
    run_checked(
        [
            str(VENV_PYTHON),
            "-m",
            "pip",
            "install",
            "--upgrade",
            "-r",
            str(REQUIREMENTS),
        ]
    )


def verify_installation(*, check_database: bool = False) -> None:
    command = [str(VENV_PYTHON), str(ROOT / "verify_installation.py")]
    if check_database:
        command.append("--check-db")
    run_checked(command)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install all EDH Claims dependencies on this PC."
    )
    parser.add_argument(
        "--skip-external",
        action="store_true",
        help="Do not use winget for Tesseract, Ghostscript, Poppler, and VC++.",
    )
    parser.add_argument(
        "--check-db",
        action="store_true",
        help="Also verify a read-only SELECT 1 connection to HBSys.",
    )
    parser.add_argument("--pause", action="store_true")
    arguments = parser.parse_args()

    print("=" * 72)
    print("EDH Claims Automation System - New PC Installer")
    print("=" * 72)
    try:
        ensure_supported_python()
        ensure_folders()
        ensure_virtual_environment()
        install_python_requirements()
        ensure_external_requirements(
            install_external=not arguments.skip_external
        )
        verify_installation(check_database=arguments.check_db)
    except Exception as exc:
        print(f"\n[INSTALL FAILED] {exc}")
        if arguments.pause:
            input("Press Enter to close...")
        return 1

    print("\n[INSTALL COMPLETE]")
    print(f"Start the system using: {ROOT / 'Claim.bat'}")
    if arguments.pause:
        input("Press Enter to close...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
