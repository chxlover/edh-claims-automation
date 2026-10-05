"""Crash + lifecycle diagnostics for the EDH Claims GUI (2026-09-28).

Why this module exists: on 2026-09-28 the GUI window closed with no
traceback on screen and no Windows crash event (no WER AppCrash / AppHang),
and Windows cannot tell "closed normally" apart from "terminated by
somebody else". Everything here is a small file trail so the next
occurrence explains itself:

    logs/gui_lifecycle.log   START / CLOSE (WM_DELETE_WINDOW) / EXIT (atexit)
    logs/gui_crash.log       faulthandler dump on a FATAL native error
                             (access violation, stack overflow, abort) with
                             the stacks of every thread
    logs/gui_errors.log      unhandled Python exceptions: main thread,
                             worker threads (threading.excepthook) and
                             Tkinter callbacks (report_callback_exception)

How to read the trail:

    CLOSE + EXIT             -> the window was closed normally (X / Alt+F4)
    EXIT without CLOSE       -> the process ended without the window handler
    no EXIT line at all      -> the process was killed from outside (Task
                                Manager, the console window was closed, a
                                security tool) or crashed before atexit ran
    gui_crash.log non-empty  -> fatal native crash, with a thread dump

    The folder is logs/ unless the CLAIMS_DIAG_LOG_DIR environment variable
    is set (tests point it at a temp folder so the production logs stay
    clean); an explicit argument always wins over both.

Contract:

    - every public call is best-effort and NEVER raises;
    - installing twice is a no-op (idempotent);
    - nothing changes application behavior: the Tk close handler still
      destroys the window exactly like Tk's default (it only adds a log
      line), and the original Tk callback-exception reporting still runs.

Standalone test:

    python core/diagnostics.py
"""

from __future__ import annotations

import atexit
import faulthandler
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(r"C:\claims_bot")
LOG_DIR = BASE_DIR / "logs"

LIFECYCLE_FILE = "gui_lifecycle.log"
CRASH_FILE = "gui_crash.log"
ERROR_FILE = "gui_errors.log"

APP_NAME = "EDH Claims Automation System"

#: Module state: keeps the faulthandler stream alive and tracks the install.
_state: dict = {
    "installed": False,
    "log_dir": None,
    "crash_stream": None,
    "started_at": None,
    "thread_excepthook": None,
}


# -- Paths --------------------------------------------------------------------

def log_dir(log_dir_override=None) -> Path:
    """Directory the three diagnostic files live in.

    Order: explicit override, then the CLAIMS_DIAG_LOG_DIR environment
    variable (tests redirect diagnostics into a temp folder), then the real
    logs folder.
    """
    if log_dir_override:
        return Path(log_dir_override)
    env_dir = os.environ.get("CLAIMS_DIAG_LOG_DIR")
    if env_dir:
        return Path(env_dir)
    return LOG_DIR


def lifecycle_log_path(log_dir_override=None) -> Path:
    return log_dir(log_dir_override) / LIFECYCLE_FILE


def crash_log_path(log_dir_override=None) -> Path:
    return log_dir(log_dir_override) / CRASH_FILE


def error_log_path(log_dir_override=None) -> Path:
    return log_dir(log_dir_override) / ERROR_FILE


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _append(path: Path, text: str) -> str:
    """Append *text* to *path*; return the line, or '' when it failed.

    Never raises: a diagnostics write must never break the application.
    """
    line = f"[{_timestamp()}] [pid {os.getpid()}] {text}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(line)
        return line
    except OSError:
        return ""


# -- Lifecycle ----------------------------------------------------------------

def write_lifecycle(event: str, detail: str = "", log_dir_override=None) -> str:
    """Append one START / CLOSE / EXIT line. Returns the line written."""
    text = str(event or "").strip().upper()
    line = f"{text} {detail}".rstrip()
    return _append(lifecycle_log_path(log_dir_override), line)


def _start_detail() -> str:
    try:
        argv = " ".join(sys.argv)
    except Exception:  # noqa: BLE001 - argv is best-effort in frozen apps
        argv = "<unavailable>"
    return (
        f"app={APP_NAME!r} python={sys.version.split()[0]} "
        f"executable={sys.executable!r} cwd={os.getcwd()!r} argv={argv!r}"
    )


def _on_exit(directory=None) -> None:
    """atexit hook: prove the interpreter ended on its own.

    *directory* is bound when the hook is registered, so a hook left behind
    by an earlier install can never fall back to the production logs folder
    after reset_for_tests() cleared the module state.
    """
    where = directory if directory is not None else _state.get("log_dir")
    if where is None:
        return  # state was reset (tests): there is no folder to write to
    started = _state.get("started_at")
    uptime = ""
    if started is not None:
        uptime = f"uptime={round(datetime.now().timestamp() - started, 1)}s "
    write_lifecycle(
        "EXIT",
        f"{uptime}(atexit - the interpreter ended normally)",
        where,
    )


def _thread_excepthook(args) -> None:
    """Log an unhandled worker-thread exception, then keep default output."""
    where = "thread"
    try:
        if getattr(args, "thread", None) is not None:
            where = f"thread {args.thread.name}"
    except Exception:  # noqa: BLE001 - naming the thread is best-effort
        pass
    directory = _state.get("log_dir")
    if directory is not None:
        log_exception(
            where, args.exc_type, args.exc_value, args.exc_traceback, directory
        )
    original = _state.get("thread_excepthook")
    if callable(original):
        try:
            original(args)
        except Exception:  # noqa: BLE001 - never raise from a hook
            pass


def _excepthook(exc_type, exc, tb) -> None:
    """Log an unhandled main-thread exception, then keep default output."""
    directory = _state.get("log_dir")
    if directory is not None:
        log_exception("main thread", exc_type, exc, tb, directory)
    try:
        sys.__excepthook__(exc_type, exc, tb)
    except Exception:  # noqa: BLE001
        pass


def log_exception(context, exc_type, exc, tb, log_dir_override=None) -> str:
    """Append one traceback to gui_errors.log. Returns the block written."""
    try:
        body = "".join(traceback.format_exception(exc_type, exc, tb))
    except Exception:  # noqa: BLE001 - a broken traceback must not break us
        body = f"{exc_type}: {exc}\n"
    text = (
        f"[{_timestamp()}] [pid {os.getpid()}] UNHANDLED EXCEPTION "
        f"({context})\n{body}"
    )
    try:
        path = error_log_path(log_dir_override)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(text)
        return text
    except OSError:
        return ""


# -- Install ------------------------------------------------------------------

def install_crash_logging(log_dir_override=None) -> bool:
    """Enable faulthandler + exception hooks + the atexit EXIT line.

    Returns True when this call armed the crash file, False when everything
    was already installed (idempotent) or when the crash file could not be
    opened (the hooks and the atexit line are installed either way).
    """
    if _state["installed"]:
        return False

    directory = log_dir(log_dir_override)
    _state["log_dir"] = directory
    _state["started_at"] = datetime.now().timestamp()

    armed = False
    try:
        directory.mkdir(parents=True, exist_ok=True)
        stream = open(
            directory / CRASH_FILE,
            "a",
            buffering=1,
            encoding="utf-8",
            errors="replace",
        )
        stream.write(
            f"\n[{_timestamp()}] [pid {os.getpid()}] --- faulthandler armed "
            f"({_start_detail()}) ---\n"
        )
        # faulthandler holds its own reference, so the stream stays open.
        faulthandler.enable(file=stream, all_threads=True)
        _state["crash_stream"] = stream
        armed = True
    except Exception:  # noqa: BLE001 - diagnostics are best-effort only
        _state["crash_stream"] = None

    _state["thread_excepthook"] = threading.excepthook
    threading.excepthook = _thread_excepthook
    sys.excepthook = _excepthook
    # Bind the folder into the hook: a later reset must not redirect an
    # already-registered handler to the default (production) logs folder.
    atexit.register(_on_exit, directory)
    _state["installed"] = True

    write_lifecycle("START", _start_detail(), directory)
    return armed


def install_tk_close_logging(root, log_dir_override=None) -> bool:
    """Log the window close, keep Tk's default close behavior.

    Adds:
        * WM_DELETE_WINDOW -> a CLOSE line, then root.destroy() (exactly what
          Tk does by default: the app must close as it always did),
        * report_callback_exception -> Tkinter callback tracebacks land in
          gui_errors.log; the original reporting still runs afterwards.

    Returns True when the hooks were added, False when they already were.
    """
    if root is None or getattr(root, "_edh_diagnostics_installed", False):
        return False
    # Follow the directory install_crash_logging() was given, so both files
    # stay together even when a caller overrides the default folder.
    directory = log_dir(
        log_dir_override if log_dir_override else _state.get("log_dir")
    )
    original_report = getattr(root, "report_callback_exception", None)

    def _report(exc, val, tb):
        log_exception("tkinter callback", exc, val, tb, directory)
        if callable(original_report):
            try:
                original_report(exc, val, tb)
            except Exception:  # noqa: BLE001 - never raise from a hook
                pass

    def _on_close():
        write_lifecycle(
            "CLOSE", "WM_DELETE_WINDOW - closing the main window", directory
        )
        try:
            root.destroy()
        except Exception:  # noqa: BLE001 - the window may already be gone
            pass

    try:
        root.protocol("WM_DELETE_WINDOW", _on_close)
        root.report_callback_exception = _report
        root._edh_diagnostics_installed = True
        return True
    except Exception:  # noqa: BLE001 - diagnostics must not break startup
        return False


def reset_for_tests() -> None:
    """Drop the idempotency flag and close the crash stream (test-only).

    Closing the stream matters on Windows: a temp folder holding an open
    gui_crash.log cannot be deleted. The atexit EXIT hook registered by the
    install is unregistered as well, so repeated install/reset cycles in one
    test process cannot pile up handlers. It does not restore the hooks that
    were already installed (they are inert without a log directory).
    """
    atexit.unregister(_on_exit)
    stream = _state.get("crash_stream")
    if stream is not None:
        try:
            stream.close()
        except Exception:  # noqa: BLE001 - closing is best-effort
            pass
    _state["installed"] = False
    _state["log_dir"] = None
    _state["started_at"] = None
    _state["crash_stream"] = None


# -- standalone test ----------------------------------------------------------

if __name__ == "__main__":
    import tkinter as tk
    from tempfile import mkdtemp

    # mkdtemp, not TemporaryDirectory: the crash log stays open on purpose
    # (faulthandler needs it) until reset_for_tests() closes it.
    tmp = mkdtemp(prefix="edh_diag_selftest_")
    assert install_crash_logging(log_dir_override=tmp) is True
    assert install_crash_logging(log_dir_override=tmp) is False  # idempotent
    write_lifecycle("DEBUG", "manual line", tmp)
    log_exception("selftest", ValueError, ValueError("boom"), None, tmp)
    lifecycle = lifecycle_log_path(tmp).read_text(encoding="utf-8")
    assert lifecycle.count("START") == 1, lifecycle
    assert "DEBUG manual line" in lifecycle
    assert "boom" in error_log_path(tmp).read_text(encoding="utf-8")
    assert crash_log_path(tmp).is_file()

    frame_root = tk.Tk()
    frame_root.withdraw()
    assert install_tk_close_logging(frame_root, tmp) is True
    assert install_tk_close_logging(frame_root, tmp) is False
    # A real window close runs the WM_DELETE_WINDOW command; destroy() alone
    # bypasses it, so call the registered Tcl command the way the WM does.
    frame_root.tk.call(frame_root.protocol("WM_DELETE_WINDOW"))
    assert "CLOSE" in lifecycle_log_path(tmp).read_text(encoding="utf-8")
    reset_for_tests()
    print(f"core.diagnostics standalone test passed (logs: {tmp})")
