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


GHOST_WINDOW_CLASSES = frozenset({"Ghost"})
GHOST_TITLE_MARKERS = ("[Ghost]",)


def is_ghost_window(window) -> bool:
    """True for a phantom HBSys window that must be ignored."""
    if window_class(window) in GHOST_WINDOW_CLASSES:
        return True
    title = window_title(window)
    return any(marker in title for marker in GHOST_TITLE_MARKERS)


def is_hbsys_window(window) -> bool:
    """True when the window is the real HBSys/HOMIS application.

    Matches by HBSys window class or by HBSys/HOMIS title markers, and always
    excludes browser windows and ghost (phantom) windows even when their tab
    title contains "HBSys".
    """
    if is_browser_window(window):
        return False
    if is_ghost_window(window):
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


# -- Shared, robust focus helpers (used by every HBSys-touching tool) -------
#
# These used to live inline in xml_generator_clicker.py only. They are now
# shared so Date Fill, XML Clicker, Add Claims Upload and Claim Attachments
# all focus HBSys the same robust way: close leftover popups/forms, recover
# from a blocker screen via Escape-only go_home(), raise the application via
# its pinned taskbar icon (image recognition, more reliable than set_focus
# on custom-drawn 4GL windows), then maximize + set_focus + verify layout.


def close_stale_windows(log_fn=None, live: bool = True) -> None:
    """Close leftover popups / Billing forms that would steal the click.

    Transient popups (Select Encounter, Admission/Admit History, Rate
    Validation, PhilHealth Beneficiaries) and leftover ``Billing (...)``
    forms are closed. Everything is best-effort and logged. Safe no-op
    when *live* is False.
    """
    if not live:
        return
    try:
        import pyautogui
        from pywinauto import Desktop
    except Exception:  # noqa: BLE001 - helpers must never crash the caller
        return

    stale_popup_titles = (
        "Select Encounter",
        "Admission History",
        "Admit History",
        "Rate Validation",
        "PhilHealth Beneficiaries",
    )

    def _log(message: str) -> None:
        if callable(log_fn):
            try:
                log_fn(message)
            except Exception:  # noqa: BLE001
                pass

    try:
        windows = list(Desktop(backend="win32").windows())
    except Exception:  # noqa: BLE001
        return

    for window in windows:
        try:
            title = window.window_text() or ""
        except Exception:  # noqa: BLE001
            title = ""
        is_stale_popup = title.strip() in stale_popup_titles
        is_billing_form = title.strip().startswith("Billing (")
        if not (is_stale_popup or is_billing_form):
            continue
        _log(f"closing stale window before HBSys focus: {title!r}")
        try:
            window.set_focus()
        except Exception:  # noqa: BLE001
            pass
        try:
            window.close()
        except Exception:  # noqa: BLE001
            try:
                rect = window.rectangle()
                pyautogui.click(rect.right - 14, rect.top + 12)
            except Exception:  # noqa: BLE001
                pass
        try:
            import time as _time
            _time.sleep(0.3)
        except Exception:  # noqa: BLE001
            pass


def focus_via_taskbar_icon(
    icon_path=None, log_fn=None, live: bool = True,
) -> bool:
    """Bring HBSys to the foreground by clicking its taskbar icon.

    Uses image recognition (pyautogui.locateOnScreen) on the pinned taskbar
    icon, which is more reliable than set_focus() for custom-drawn 4GL
    windows. Returns True when the icon was found and clicked. Safe no-op
    in dry-run mode.
    """
    if not live:
        return False
    try:
        import pyautogui
        from pathlib import Path
    except Exception:  # noqa: BLE001
        return False

    if icon_path is None:
        candidates = [
            Path(r"C:\\claims_bot\\eclaims_icon.png"),
            Path(__file__).resolve().parent.parent / "eclaims_icon.png",
        ]
        for candidate in candidates:
            if candidate.is_file():
                icon_path = candidate
                break
    if icon_path is None or not Path(icon_path).is_file():
        if callable(log_fn):
            try:
                log_fn("taskbar icon image not found - falling back to set_focus()")
            except Exception:  # noqa: BLE001
                pass
        return False

    if callable(log_fn):
        try:
            log_fn(f"locating taskbar icon: {icon_path}")
        except Exception:  # noqa: BLE001
            pass

    for attempt in range(5):
        try:
            location = pyautogui.locateOnScreen(
                str(icon_path), confidence=0.8, grayscale=True
            )
        except Exception as exc:  # noqa: BLE001
            if callable(log_fn):
                try:
                    log_fn(f"locateOnScreen failed: {exc}")
                except Exception:  # noqa: BLE001
                    pass
            location = None
        if location is not None:
            try:
                center = pyautogui.center(location)
                if callable(log_fn):
                    try:
                        log_fn(f"taskbar icon found at {center} - clicking")
                    except Exception:  # noqa: BLE001
                        pass
                pyautogui.click(center)
                try:
                    import time as _time
                    _time.sleep(1.0)
                except Exception:  # noqa: BLE001
                    pass
                return True
            except Exception as exc:  # noqa: BLE001
                if callable(log_fn):
                    try:
                        log_fn(f"taskbar click failed: {exc}")
                    except Exception:  # noqa: BLE001
                        pass
                return False
        try:
            import time as _time
            _time.sleep(0.4)
        except Exception:  # noqa: BLE001
            pass
    if callable(log_fn):
        try:
            log_fn("taskbar icon not found on screen")
        except Exception:  # noqa: BLE001
            pass
    return False


def detect_screen_and_recover(log_fn=None, live: bool = True) -> str:
    """Best-effort screen detection + Escape-only go_home() recovery.

    Returns the screen name observed on entry (or 'unavailable').
    Never raises.
    """
    try:
        from core.agent import hbsys_nav, hbsys_screens
    except Exception:  # noqa: BLE001
        return "unavailable"
    try:
        result = hbsys_screens.detect_screen()
        screen = result.screen
    except Exception:  # noqa: BLE001
        return "unavailable"
    if callable(log_fn):
        try:
            log_fn(f"HBSys screen on entry: {screen}")
        except Exception:  # noqa: BLE001
            pass
    blocker_screens = (
        hbsys_screens.SCREEN_HBSYS_CLOSED,
        hbsys_screens.SCREEN_DIALOG,
        hbsys_screens.SCREEN_UNKNOWN,
    )
    if screen in blocker_screens and live:
        if callable(log_fn):
            try:
                log_fn("HBSys not on a usable screen - attempting go_home()")
            except Exception:  # noqa: BLE001
                pass
        try:
            nav = hbsys_nav.Navigator(log_fn=log_fn or (lambda _m: None))
            nav.go_home()
            after = hbsys_screens.detect_screen()
            screen = after.screen
            if callable(log_fn):
                try:
                    log_fn(f"HBSys screen after go_home: {screen}")
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            if callable(log_fn):
                try:
                    log_fn(f"screen recovery skipped: {exc}")
                except Exception:  # noqa: BLE001
                    pass
    return screen


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
