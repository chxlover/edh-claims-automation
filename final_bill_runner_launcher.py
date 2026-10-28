"""Launcher for the HBSys Final Bill runner (workflow node, dashboard button).

This keeps the GUI integration separate from the production processor and
from the actual HBSys Final Bill automation.

Runs the folder-contract Final Bill flow (core.agent.final_bill_runner) in
LIVE mode over every pending patient folder under CLAIMS_OUTPUT_FOLDER.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
TOOL = BASE_DIR / "core" / "agent" / "final_bill_runner.py"


def main() -> int:
    if not TOOL.is_file():
        raise FileNotFoundError(f"Could not find Final Bill runner: {TOOL}")

    print("[FINAL BILL] Launching:", TOOL)
    command = [sys.executable, "-m", "core.agent.final_bill_runner", "--live"]
    process = subprocess.Popen(command, cwd=str(BASE_DIR))
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())