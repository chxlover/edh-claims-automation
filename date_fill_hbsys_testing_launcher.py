from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
TESTING_SCRIPT = BASE_DIR / "date_fill_hbsys" / "hbsys_fill_dates_testing.py"


def choose_claim_type() -> str | None:
    """Ask which isolated testing workflow to run."""
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    answer = messagebox.askyesnocancel(
        "Date Fill Testing",
        "Choose the testing claim type:\n\n"
        "YES  = ABTC (uses Admission Date)\n"
        "NO   = Regular (uses Discharge Date)\n"
        "CANCEL = Do not run",
        parent=root,
    )
    root.destroy()
    if answer is None:
        return None
    return "ABTC" if answer else "REGULAR"


def main() -> int:
    if not TESTING_SCRIPT.is_file():
        print(f"Date Fill testing script was not found: {TESTING_SCRIPT}")
        return 1

    claim_type = choose_claim_type()
    if claim_type is None:
        print("[DATE FILL TESTING] Cancelled.")
        return 0

    command = [
        sys.executable,
        str(TESTING_SCRIPT),
        "--live",
        "--claim-type",
        claim_type,
    ]
    if claim_type == "ABTC":
        command.append("--enable-abtc")
    print(f"[DATE FILL TESTING] Launching: {TESTING_SCRIPT}")
    print(f"[DATE FILL TESTING] Claim Type: {claim_type}")
    print("[DATE FILL TESTING] Production Date Fill is not being used.")
    process = subprocess.Popen(command, cwd=str(TESTING_SCRIPT.parent))
    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
