from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from updater import ensure_requirements
from install_requirements import ensure_folders


ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def _running_in_project_venv() -> bool:
    try:
        return Path(sys.executable).resolve() == VENV_PYTHON.resolve()
    except OSError:
        return False


def main() -> int:
    if VENV_PYTHON.is_file() and not _running_in_project_venv():
        return subprocess.call(
            [str(VENV_PYTHON), str(Path(__file__).resolve())],
            cwd=ROOT,
        )
    ensure_folders()
    ensure_requirements()

    from edh_claims_gui_XML_COPY_BUTTON import EDHClaimsGUI

    app = EDHClaimsGUI()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
