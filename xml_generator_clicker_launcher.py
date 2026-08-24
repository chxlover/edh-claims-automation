"""Launcher for the HBSys XML generator clicker.

This keeps the GUI integration separate from the production processor and from
the actual HBSys XML generator.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
TOOL = BASE_DIR / "date_fill_hbsys" / "xml_generator_clicker.py"


def main() -> int:
    if not TOOL.is_file():
        raise FileNotFoundError(f"Could not find XML generator clicker: {TOOL}")

    print(f"[XML GENERATOR CLICKER] Launching: {TOOL}")
    command = [sys.executable, str(TOOL), "--live"]
    process = subprocess.Popen(command, cwd=TOOL.parent)
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
