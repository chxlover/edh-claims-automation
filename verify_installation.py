"""Non-destructive workstation verification for EDH Claims."""

from __future__ import annotations

import argparse
import importlib
import platform
import sys
from pathlib import Path

from install_requirements import REQUIRED_FOLDERS, ROOT
from updater import get_external_requirements_status


IMPORT_CHECKS = {
    "fitz": "PyMuPDF",
    "numpy": "numpy",
    "cv2": "opencv-python",
    "pytesseract": "pytesseract",
    "pdf2image": "pdf2image",
    "PIL": "Pillow",
    "PyPDF2": "PyPDF2",
    "reportlab": "reportlab",
    "rapidfuzz": "rapidfuzz",
    "pymysql": "pymysql",
    "mysql.connector": "mysql-connector-python",
    "pywinauto": "pywinauto",
    "pyautogui": "PyAutoGUI",
    "telegram": "python-telegram-bot",
    "openpyxl": "openpyxl",
    "tkinter": "Tkinter (Python standard library)",
}


def check_python_modules() -> list[str]:
    failures: list[str] = []
    for module_name, package_name in IMPORT_CHECKS.items():
        try:
            importlib.import_module(module_name)
            print(f"[Python library] OK: {package_name}")
        except Exception as exc:
            failures.append(f"{package_name}: {exc}")
            print(f"[Python library] FAILED: {package_name}: {exc}")
    return failures


def check_external_tools() -> list[str]:
    failures: list[str] = []
    for name, available in get_external_requirements_status().items():
        print(f"[External] {'OK' if available else 'MISSING'}: {name}")
        if not available:
            failures.append(name)
    return failures


def check_folders() -> list[str]:
    failures: list[str] = []
    for relative in REQUIRED_FOLDERS:
        path = ROOT / relative
        print(f"[Folder] {'OK' if path.is_dir() else 'MISSING'}: {path}")
        if not path.is_dir():
            failures.append(str(path))
    return failures


def check_database_read_only() -> list[str]:
    try:
        from core.hbsys_connection import create_hbsys_connection

        connection = create_hbsys_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok")
                row = cursor.fetchone()
                if not row or int(row.get("ok", 0)) != 1:
                    raise RuntimeError("SELECT 1 returned an unexpected result")
        finally:
            connection.close()
        print("[HBSys] OK: read-only connection test")
        return []
    except Exception as exc:
        print(f"[HBSys] FAILED: {exc}")
        return [str(exc)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify this EDH Claims PC.")
    parser.add_argument("--check-db", action="store_true")
    arguments = parser.parse_args()

    print(f"[Python] {platform.python_version()} ({platform.architecture()[0]})")
    failures = []
    if sys.version_info < (3, 14):
        failures.append("Python 3.14 or newer is required")
    failures.extend(check_python_modules())
    failures.extend(check_external_tools())
    failures.extend(check_folders())
    if arguments.check_db:
        failures.extend(check_database_read_only())

    if failures:
        print(f"\n[VERIFICATION FAILED] {len(failures)} issue(s)")
        return 1
    print("\n[VERIFICATION PASSED] This PC has all checked requirements.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
