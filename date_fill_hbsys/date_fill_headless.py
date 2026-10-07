"""Headless-mode helpers for the Date Fill entry points.

The Workflow engine launches Date Fill as a *headless* subprocess
(`core/workflow_adapters.ScriptNodeAdapter`), so its end-of-run Tk popup
("Date Fill Complete" / "Date Fill Stopped ...") and its auto-open of the
run-log CSV in Excel would block or distract an unattended Final Bill batch.

Marking (see core/workflow_registry.py): `CLAIMS_HEADLESS=1` lives in the
`extra_env` of the `date_fill_regular` / `date_fill_abtc` nodes. It is NOT set
by the main-dashboard Date Fill button (whose launcher inherits the GUI
`_run_script_thread` environment) nor by manual `python hbsys_fill_dates*.py`
runs, so those keep their original popup + Excel behaviour.

The Date Fill entry points call `ui_enabled()` before showing any popup /
opening Excel, so:

* Workflow tab   (CLAIMS_HEADLESS=1) -> ui_enabled() False -> silent; the
  Workflow tab still receives the run-log path via stdout.
* Main dashboard / manual run        -> CLAIMS_HEADLESS unset ->
  ui_enabled() True  -> popup + Excel (unchanged original behaviour).

Pure std-lib only, so it imports & unit-tests anywhere -- notably from inside
the pywinauto-dependent `hbsys_fill_dates_testing.py`, which already puts
`date_fill_hbsys/` on sys.path (PYTHONPATH in the adapter; script dir in the
dashboard launcher).
"""

from __future__ import annotations

import os


def headless() -> bool:
    """True when launched by the headless Workflow engine (CLAIMS_HEADLESS=1)."""
    return os.environ.get("CLAIMS_HEADLESS") == "1"


def ui_enabled() -> bool:
    """True when a desktop popup + Excel-open is desirable (interactive run)."""
    return not headless()


if __name__ == "__main__":
    # Standalone sanity check (AGENTS.md: every module must support __main__).
    import sys

    print(f"CLAIMS_HEADLESS={os.environ.get('CLAIMS_HEADLESS')!r}")
    print(f"headless()={headless()} ui_enabled()={ui_enabled()}")
    sys.exit(0)
