"""Run-finished notifier: taskbar flash + toast, so the operator KNOWS it ended.

The Final Bill run takes minutes per patient and the operator has to look away
(the run must not be interrupted, and HBSys must stay in the foreground), so
the only reliable end-of-run signal is OUTSIDE the HBSys window: the taskbar
button starts flashing and a toast pops up (Windows 10/11 action centre).

SAFETY (this is the whole point of the module):
    Nothing here may steal the foreground. The agent is still driving HBSys with
    real mouse and keyboard events; a notifier that raised its own window would
    make the NEXT click/keystroke land in the wrong window - exactly the failure
    that made the earlier runs type into the claims GUI instead of the Hospital
    No. field. So:
      - the taskbar flash is done with FlashWindowEx(FLASHW_TRAY), which only
        flashes the existing taskbar button and never activates the window,
      - the Windows toast (WinRT) is preferred when available because it needs
        no window of ours at all,
      - the Tk fallback toast is a non-activating toplevel.

Every entry point is fail-soft: a broken notifier must never fail a run, so
each call is wrapped and only ever logs.

Standalone testing:
    python -m gui.run_notifier            # shows a sample toast + flash
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional

LOGGER = logging.getLogger(__name__)

# How long the taskbar button keeps flashing, in milliseconds.
FLASH_DURATION_MS = 6000

# Toast geometry (screen coords of the bottom-right corner) and timing.
TOAST_MARGIN = 16
TOAST_WIDTH = 340
TOAST_HEIGHT = 110
TOAST_DURATION_MS = 12000


def _windows_available() -> bool:
    """True only on Windows (every feature here is Windows-only)."""
    return os.name == "nt"


def _as_hwnd(value: int):
    """Coerce an int into a ctypes HWND (plain int when ctypes is absent)."""
    try:
        from ctypes import wintypes

        return wintypes.HWND(value)
    except Exception:  # noqa: BLE001
        return value


# -- taskbar flash -----------------------------------------------------------

def _flash_taskbar(hwnd, duration_ms: int = FLASH_DURATION_MS) -> bool:
    """Flash an existing window's TASKBAR button without activating it.

    Returns True when the flash was requested. FlashWindowEx is the only
    end-of-run signal that does not touch the foreground, which is mandatory
    while the agent is mid-click on HBSys.
    """
    if not _windows_available() or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001 - non-Windows or locked-down env
        return False

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hwnd", wintypes.HWND),
            ("dwFlags", wintypes.DWORD),
            ("uCount", wintypes.UINT),
            ("dwTimeout", wintypes.DWORD),
        ]

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        # FLASHW_TRAY (0x02) | FLASHW_TIMER (0x04): uCount 0 = use dwTimeout.
        flags = 0x00000002 | 0x00000004
        info = FLASHWINFO(
            ctypes.sizeof(FLASHWINFO),
            _as_hwnd(hwnd),
            flags,
            0,
            int(duration_ms),
        )
        return bool(user32.FlashWindowEx(ctypes.byref(info)))
    except Exception:  # noqa: BLE001 - never break a run over a flash
        LOGGER.debug("FlashWindowEx failed", exc_info=True)
        return False


def flash_taskbar_for(widget, duration_ms: int = FLASH_DURATION_MS) -> bool:
    """Flash the taskbar button of the widget's toplevel window."""
    try:
        import ctypes

        # Tk's winfo_id is the client window; the top-level frame is the one
        # that owns the taskbar button.
        hwnd = int(widget.winfo_id())
        parent = ctypes.windll.user32.GetParent(_as_hwnd(hwnd))
        hwnd = int(parent) if parent else hwnd
    except Exception:  # noqa: BLE001
        try:
            hwnd = int(widget.winfo_id())
        except Exception:  # noqa: BLE001
            return False
    return _flash_taskbar(hwnd, duration_ms)



# -- Windows toast (preferred: needs no window of ours) ---------------------

def _windows_toast(title: str, message: str) -> bool:
    """Post a real Windows notification. False when unavailable or failed."""
    if not _windows_available():
        return False
    try:
        from winotify import Notification  # type: ignore

        Notification(
            app_id="EDH Claims Automation",
            title=title,
            msg=message,
            duration="long",
        ).show()
        return True
    except Exception:  # noqa: BLE001 - module absent or WinRT blocked
        LOGGER.debug("Windows toast unavailable", exc_info=True)
        return False


# -- Tk toast (fallback: never activates) ------------------------------------

def _tk_toast(master, title: str, message: str,
              duration_ms: int = TOAST_DURATION_MS):
    """A small always-on-top corner toast that does NOT take the foreground.

    -overrideredirect removes the title bar, -topmost keeps it visible over
    HBSys, and the click binding returns "break" so clicking the toast cannot
    pull focus away from HBSys mid-run.
    """
    import tkinter as tk

    try:
        top = tk.Toplevel(master)
    except Exception:  # noqa: BLE001
        return None
    top.title(title)
    try:
        top.attributes("-topmost", True)
        top.overrideredirect(True)
        top.resizable(False, False)
    except Exception:  # noqa: BLE001
        pass

    frame = tk.Frame(top, bg="#1b5e20", padx=10, pady=8)
    frame.pack(fill="both", expand=True)
    tk.Label(
        frame, text=title, bg="#1b5e20", fg="white",
        font=("Segoe UI", 11, "bold"), justify="left", anchor="w",
    ).pack(fill="x")
    tk.Label(
        frame, text=message, bg="#1b5e20", fg="#e8f5e9", font=("Segoe UI", 9),
        justify="left", anchor="w", wraplength=TOAST_WIDTH - 24,
    ).pack(fill="x")

    def _place() -> None:
        try:
            w = max(TOAST_WIDTH, top.winfo_reqwidth())
            h = max(TOAST_HEIGHT, top.winfo_reqheight())
            x = top.winfo_screenwidth() - w - TOAST_MARGIN
            y = top.winfo_screenheight() - h - 48 - TOAST_MARGIN
            top.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:  # noqa: BLE001
            pass

    try:
        top.update_idletasks()
        _place()
        top.bind("<Button-1>", lambda _event: "break", add="+")
        top.after(duration_ms, top.destroy)
    except Exception:  # noqa: BLE001
        try:
            top.destroy()
        except Exception:  # noqa: BLE001
            pass
        return None
    return top


# -- public API --------------------------------------------------------------

def notify_run_finished(
    widget=None,
    title: str = "Agent Plan - run finished",
    message: str = "Tapos na ang run.",
    *,
    on_done: Optional[Callable[[], None]] = None,
) -> str:
    """Tell the operator the run is over.

    Returns what was used: "win" (Windows toast), "taskbar" (flash only),
    "tk" (corner toast) or "none". The taskbar flash always runs, so the button
    lights up even on a machine with no toast mechanism at all.

    Args:
        widget: the Tk widget whose taskbar button should flash.
        title/message: the operator-facing toast text.
        on_done: called AFTER the toast is up, used to hand the foreground back
            to HBSys so the next keystroke cannot land in the toast.
    """
    used = "none"
    if widget is not None:
        try:
            if flash_taskbar_for(widget):
                used = "taskbar"
        except Exception:  # noqa: BLE001
            LOGGER.debug("taskbar flash failed", exc_info=True)
    try:
        if _windows_toast(title, message):
            used = "win"
    except Exception:  # noqa: BLE001
        pass
    if widget is not None and used != "win":
        try:
            if _tk_toast(widget, title, message) is not None:
                used = "tk"
        except Exception:  # noqa: BLE001
            LOGGER.debug("tk toast failed", exc_info=True)
    if on_done is not None:
        try:
            on_done()
        except Exception:  # noqa: BLE001
            LOGGER.debug("restore foreground failed", exc_info=True)
    LOGGER.info("run-finished notifier used=%s", used)
    return used


def notify_run_failed(
    widget=None,
    message: str = "May error sa run.",
    title: str = "Agent Plan - run failed",
) -> str:
    """The variant of `notify_run_finished` for a crashed run."""
    if _windows_toast(title, message):
        return "win"
    if widget is not None:
        try:
            if _tk_toast(widget, title, message) is not None:
                return "tk"
        except Exception:  # noqa: BLE001
            pass
    return "none"


if __name__ == "__main__":  # standalone test
    import tkinter as tk

    root = tk.Tk()
    root.title("run_notifier standalone test")
    root.geometry("420x170+80+80")
    tk.Label(root, text="Click a button to see the notifier.").pack(pady=10)
    tk.Button(
        root, text="Finished",
        command=lambda: notify_run_finished(root, message="Tapos na ang run."),
    ).pack(pady=4)
    tk.Button(
        root, text="Failed",
        command=lambda: notify_run_failed(root, message="May error sa run."),
    ).pack(pady=4)
    root.mainloop()
