"""Shared HBSys window detection for the Date Fill / XML automation tools.

Single source of truth for deciding which desktop windows belong to HBSys so
that browser tabs are never mistaken for the real HBSys application window
(a Chrome tab titled "Claude HBSys Automation" contains "HBSys" but is not
HBSys). Browser windows are always excluded.

Standalone test:
    python hbsys_window.py
"""

from __future__ import annotations

from pywinauto import Desktop

# Class names used by the HBSys (Clarion/FoxPro) application windows.
HBSYS_CLASS_NAMES = frozenset({"FNWND370", "FNWND230"})

# Title fragments that identify real HBSys/HOMIS windows.
HBSYS_TITLE_MARKERS = ("HBSys", "HOMIS")


def window_title(window) -> str:
    """Return the window title, or "" when the window is gone/unreadable."""
    try:
        return window.window_text() or ""
    except Exception:
        return ""


def window_class(window) -> str:
    """Return the window class name, or "" when unreadable."""
    try:
        return window.class_name() or ""
    except Exception:
        return ""


def is_browser_window(window) -> bool:
    """True for Chromium-based browser windows (Chrome/Edge tabs).

    Chrome and Edge top-level windows use the ``Chrome_WidgetWin_*`` class;
    their tab titles can contain "HBSys" and must never be treated as HBSys.
    """
    return window_class(window).startswith("Chrome_WidgetWin_")


def is_hbsys_window(window) -> bool:
    """True when the window is the real HBSys/HOMIS application.

    Matches by HBSys window class or by HBSys/HOMIS title markers, and always
    excludes browser windows even when their tab title contains "HBSys".
    """
    if is_browser_window(window):
        return False
    if window_class(window) in HBSYS_CLASS_NAMES:
        return True
    title = window_title(window)
    return any(marker in title for marker in HBSYS_TITLE_MARKERS)


def find_hbsys_windows(backend: str = "win32") -> list:
    """Return every desktop window that belongs to HBSys (browsers excluded)."""
    return [
        window
        for window in Desktop(backend=backend).windows()
        if is_hbsys_window(window)
    ]


def find_hbsys_window(backend: str = "win32"):
    """Return the first HBSys window, or None when HBSys is not open."""
    windows = find_hbsys_windows(backend)
    return windows[0] if windows else None


def find_hbsys_window_or_raise(backend: str = "win32"):
    """Return the first HBSys window or raise RuntimeError."""
    window = find_hbsys_window(backend)
    if window is None:
        raise RuntimeError("HBSys window not found. Open HBSys and try again.")
    return window


if __name__ == "__main__":
    for backend in ("win32", "uia"):
        found = find_hbsys_windows(backend)
        print(f"{backend}: {len(found)} HBSys window(s)")
        for window in found:
            print(
                f"  - {window_title(window)!r} "
                f"[{window_class(window)}] handle={window.handle}"
            )
        if not found:
            print("  (none - open HBSys and run again)")
