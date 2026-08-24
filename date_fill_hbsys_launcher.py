"""Launcher for the HBSys date-fill helper.

This wrapper lets the main EDH Claims GUI launch the existing date-fill tool
without hard-coding it into the processor.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
LOCAL_DATE_FILL_DIR = BASE_DIR / "date_fill_hbsys"
EXTERNAL_DATE_FILL_DIR = Path(r"C:\Users\EDH-Admin\Documents\date_fill_hbsys")


def find_date_fill_tool() -> Path:
    candidates = (
        LOCAL_DATE_FILL_DIR / "hbsys_fill_dates_testing.py",
        LOCAL_DATE_FILL_DIR / "hbsys_fill_dates.py",
        BASE_DIR / "hbsys_fill_dates.py",
        EXTERNAL_DATE_FILL_DIR / "hbsys_fill_dates.py",
        BASE_DIR / "date_fill_hbsys.py",
        BASE_DIR / "filldate.py",
        BASE_DIR / "__pycache__" / "filldate.cpython-314.pyc",
        BASE_DIR / "__pycache__" / "filldate.cpython-313.pyc",
        BASE_DIR / "__pycache__" / "filldate.cpython-312.pyc",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "Could not find date_fill_hbsys.py, filldate.py, or compiled filldate.pyc."
    )


def main() -> int:
    tool = find_date_fill_tool()
    print(f"[DATE FILL HBSYS] Launching: {tool}")
    command = [sys.executable, str(tool)]
    if tool.name in {"hbsys_fill_dates.py", "hbsys_fill_dates_testing.py"}:
        command.append("--live")
    if tool.name == "hbsys_fill_dates_testing.py":
        command.extend(("--production-mode", "--claim-type", "REGULAR"))
    process = subprocess.Popen(command, cwd=tool.parent)
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
