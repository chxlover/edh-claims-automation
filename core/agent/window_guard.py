"""Foreground-window guard for the BLIND automation input (2026-09-28).

The Date Fill and XML Clicker tools drive HBSys with pyautogui: clicks at
absolute screen coordinates and ``hotkey()`` / ``press()`` / ``write()`` calls
that go to WHATEVER WINDOW CURRENTLY HAS FOCUS. pyautogui has no window
targeting, so when HBSys is not focused (or is hung - the live runs of
2026-09-26 logged ``focus HBSys window: '... (Not Responding)'``) those keys
and clicks land on the EDH Claims GUI or on any other foreground app.

Measured with a real Tk window on 2026-09-28: Ctrl+F4 (used by Date Fill's
``dismiss_phic_details_if_open``), Ctrl+W, ESC and Ctrl+A do NOT close the
Tk window, and only Alt+F4 does (never sent by this code base) - so a stray
hotkey cannot explain the GUI closing. The remaining risk is real anyway:
stray input can press GUI controls, close a dialog, or type into the wrong
place. This guard makes that visible (and optionally impossible) instead of
silent.

Modes (env var ``CLAIMS_AGENT_FOCUS_GUARD``):

    warn   (default) log a loud warning, then send the input as before
    block            log the warning and REFUSE the input (returns False)
    off              no checking at all

Fail-open by design: if the foreground window cannot be read (no pywin32,
headless session), the guard returns True - a diagnostics check must never
stop a production run. ``guard_input()`` is injectable (``title_fn``) so the
whole decision path is unit-testable.

Standalone test:

    python core/agent/window_guard.py
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Callable

#: Environment variable that selects the guard mode.
GUARD_ENV = "CLAIMS_AGENT_FOCUS_GUARD"

MODE_WARN = "warn"
MODE_BLOCK = "block"
MODE_OFF = "off"
ALL_MODES = (MODE_WARN, MODE_BLOCK, MODE_OFF)

#: Title words that identify the HBSys / HOMIS window (same rule as
#: core.agent.hbsys_screens.is_hbsys_title).
HBSYS_TITLE_WORDS = ("HBSYS", "HOMIS", "HOSPITAL")

_LOG_FILE = "agent_input_guard.log"


# -- Detection ----------------------------------------------------------------

def is_hbsys_title(title: str) -> bool:
    """True when a window title belongs to HBSys / HOMIS."""
    upper = str(title or "").upper()
    return any(word in upper for word in HBSYS_TITLE_WORDS)


def foreground_window_title() -> str:
    """Title of the current foreground window ('' when unreadable)."""
    try:
        import win32gui
    except Exception:  # noqa: BLE001 - pywin32 may be missing
        return ""
    try:
        handle = win32gui.GetForegroundWindow()
        if not handle:
            return ""
        return win32gui.GetWindowText(handle) or ""
    except Exception:  # noqa: BLE001 - a dead window is not our problem
        return ""


def foreground_is_hbsys(title_fn: Callable[[], str] | None = None) -> bool:
    """True when HBSys owns the foreground (False when it cannot be read)."""
    probe = title_fn or foreground_window_title
    try:
        return is_hbsys_title(probe())
    except Exception:  # noqa: BLE001 - fail-open
        return False


# -- Mode ---------------------------------------------------------------------

def guard_mode(env=None) -> str:
    """Guard mode from the environment (default 'warn'; unknown = 'warn')."""
    source = os.environ if env is None else env
    value = str(source.get(GUARD_ENV, "") or "").strip().lower()
    return value if value in ALL_MODES else MODE_WARN


# -- Guard --------------------------------------------------------------------

def guard_input(
    action: str,
    *,
    log_fn: Callable[[str], None] | None = None,
    title_fn: Callable[[], str] | None = None,
    mode: str | None = None,
    log_dir_override=None,
) -> bool:
    """Check the foreground window before sending blind input.

    Args:
        action: human-readable description of the input about to be sent
            (e.g. ``"hotkey ctrl+a"`` or ``"click Close Form at (548, 97)"``).
        log_fn: called with the warning line (the tools pass ``log_action``),
            so the warning also lands in the captured agent tool log.
        title_fn: injectable foreground-title probe (tests).
        mode: override the environment mode.
        log_dir_override: override the directory of ``agent_input_guard.log``.

    Returns:
        True when the caller may send the input; False only in 'block' mode
        while a non-HBSys window owns the foreground.
    """
    selected = str(mode or guard_mode()).strip().lower()
    if selected == MODE_OFF:
        return True

    probe = title_fn or foreground_window_title
    try:
        title = str(probe() or "")
    except Exception:  # noqa: BLE001 - fail-open
        return True

    if title and is_hbsys_title(title):
        return True  # the input is going where it belongs

    shown = title or "<unreadable / no foreground window>"
    allowed = selected != MODE_BLOCK
    verdict = "sending anyway (warn mode)" if allowed else "input REFUSED"
    message = (
        f"blind input guard: {action} while the foreground window is {shown!r} "
        f"- not HBSys; {verdict}"
    )
    _log(message, log_dir_override)
    if callable(log_fn):
        try:
            log_fn(message)
        except Exception:  # noqa: BLE001 - logging must not break the run
            pass
    try:
        from core.activity_logger import logger

        logger.warning(message)
    except Exception:  # noqa: BLE001 - the activity logger is optional
        pass
    return allowed


def _log(message: str, log_dir_override=None) -> None:
    """Best-effort append of one guard line (never raises)."""
    try:
        if log_dir_override:
            directory = Path(log_dir_override)
        else:
            # Only needed for the default folder: keeps this module usable
            # when the tool runs with its own directory as sys.path[0].
            from core.diagnostics import log_dir as default_log_dir

            directory = default_log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n"
        with open(
            directory / _LOG_FILE, "a", encoding="utf-8", errors="replace"
        ) as handle:
            handle.write(line)
    except Exception:  # noqa: BLE001 - diagnostics only
        pass


# -- standalone test ----------------------------------------------------------

if __name__ == "__main__":
    from tempfile import TemporaryDirectory

    assert is_hbsys_title(
        "HBSys - Hospital Operations and Management Information System "
        "(HOMIS) Billing System"
    )
    assert not is_hbsys_title("EDH Claims Automation System")
    assert guard_mode({}) == MODE_WARN
    assert guard_mode({GUARD_ENV: "BLOCK"}) == MODE_BLOCK
    assert guard_mode({GUARD_ENV: "nonsense"}) == MODE_WARN

    with TemporaryDirectory() as tmp:
        lines: list = []
        assert guard_input(
            "hotkey ctrl+a", title_fn=lambda: "EDH Claims",
            mode=MODE_WARN, log_fn=lines.append, log_dir_override=tmp,
        ) is True
        assert guard_input(
            "hotkey ctrl+a", title_fn=lambda: "EDH Claims",
            mode=MODE_BLOCK, log_fn=lines.append, log_dir_override=tmp,
        ) is False
        assert guard_input(
            "hotkey ctrl+a", title_fn=lambda: "HBSys - HOMIS",
            mode=MODE_BLOCK, log_fn=lines.append, log_dir_override=tmp,
        ) is True
        assert len(lines) == 2, lines
        assert lines[0].startswith("blind input guard:")
        assert (Path(tmp) / _LOG_FILE).is_file()
    print("core.agent.window_guard standalone test passed")
