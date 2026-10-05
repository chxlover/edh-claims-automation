"""_probe_final_bill.py - Slice D discovery probe for the HBSys Final Bill flow.

DEV-ONLY helper (same spirit as the other `_verify_*.py` / `_analyze_*.py`
scripts at the repo root). It performs ONE manual-equivalent step of the
Final Bill workflow and records exact evidence (window titles, class names,
control ids, absolute rectangles, screenshots) so the real implementation in
`core/agent/*` never has to guess coordinates.

Manual sequence being mapped (operator description):
    1. type Hospital No. then press Enter
    2. click Admit History
    3. pick the right confinement period
    4. double click the right confinement
    5. click Final Bill  -> "Print Options" popup opens
    6. check the checkbox beside the word "Final", then click OK
    7. sometimes a "call administrator / Yes-No" popup appears -> click No
       sometimes a "File save" popup appears -> click OK, then Close Form

Usage (each flag is one observable step; nothing is clicked silently):
    python _probe_final_bill.py --open                # Billing -> Final Bill
    python _probe_final_bill.py --snapshot            # dump popup tree only
    python _probe_final_bill.py --click-final         # toggle the Final box
    python _probe_final_bill.py --click-ok            # press OK on the popup
    python _probe_final_bill.py --open --click-final --click-ok --watch 20

Evidence goes to:
    logs/final_bill_probe_<timestamp>.json
    screenshots/final_bill_probe_<timestamp>_<stage>.png
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
SHOT_DIR = ROOT / "screenshots"

MAIN_TITLE_MARKER = "HOMIS"
MAIN_CLASS = "FNWND370"
POPUP_CLASS = "FNWNS370"
BTN_FINAL_ID = 1001
BTN_OK_ID = 1002
BTN_PRINT_ID = 1003

_LOG_LINES: list = []


def log(message: str) -> None:
    """Print + keep for the JSON evidence file."""
    stamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    _LOG_LINES.append(line)


def _desktop():
    import pywinauto

    return pywinauto.Desktop(backend="win32")


def find_main_window():
    """HBSys main frame (class FNWND370, title contains HOMIS)."""
    for win in _desktop().windows():
        try:
            title = win.window_text()
        except Exception:
            continue
        if (
            win.class_name() == MAIN_CLASS
            and MAIN_TITLE_MARKER in title.upper()
            and win.is_visible()
        ):
            return win
    return None


def _has_final_button(win) -> bool:
    for ctrl in win.children():
        try:
            if not ctrl.is_visible():
                continue
            if ctrl.class_name() != "Button":
                continue
            if ctrl.control_id() == BTN_FINAL_ID and "FINAL" in ctrl.window_text().upper():
                return True
        except Exception:
            continue
    return False


def find_print_options():
    """The Print Options popup (FNWNS370 carrying a 'Final' button)."""
    for win in _desktop().windows():
        try:
            if win.class_name() != POPUP_CLASS:
                continue
            if not win.is_visible():
                continue
            if _has_final_button(win):
                return win
        except Exception:
            continue
    return None


def child(win, control_id: int, class_name: str = ""):
    """First VISIBLE child with this id (falls back to the first match).

    HBSys PowerBuilder forms reuse small ids (1000-1010) for both the real
    buttons and hidden datawindows, so visibility + class must be checked.
    """
    fallback = None
    for ctrl in win.children():
        try:
            if ctrl.control_id() != control_id:
                continue
            if class_name and ctrl.class_name() != class_name:
                continue
            if ctrl.is_visible():
                return ctrl
            if fallback is None:
                fallback = ctrl
        except Exception:
            continue
    return fallback

def rect_of(ctrl) -> list:
    r = ctrl.rectangle()
    return [r.left, r.top, r.right, r.bottom]




def dump_window(win) -> dict:
    """Full evidence dump for one window: rects + control ids + titles."""
    try:
        title = win.window_text()
    except Exception:
        title = ""
    data = {
        "handle": hex(int(win.handle)),
        "title": title,
        "class_name": win.class_name(),
        "process_id": win.process_id(),
        "rectangle": rect_of(win),
        "visible_children": [],
        "all_children_count": 0,
    }
    try:
        children = win.children()
    except Exception:
        return data
    data["all_children_count"] = len(children)
    for ctrl in children:
        try:
            if not ctrl.is_visible():
                continue
            data["visible_children"].append(
                {
                    "text": ctrl.window_text(),
                    "class_name": ctrl.class_name(),
                    "control_id": ctrl.control_id(),
                    "rectangle": rect_of(ctrl),
                }
            )
        except Exception:
            continue
    return data


def dump_desktop_hbsys() -> list:
    """Every visible window owned by the HBSys process (for popup discovery)."""
    windows = []
    main = find_main_window()
    pid = main.process_id() if main is not None else None
    for win in _desktop().windows():
        try:
            if pid is not None and win.process_id() != pid:
                continue
            if not win.is_visible():
                continue
        except Exception:
            continue
        try:
            windows.append(
                {
                    "handle": hex(int(win.handle)),
                    "title": win.window_text(),
                    "class_name": win.class_name(),
                    "rectangle": rect_of(win),
                }
            )
        except Exception:
            continue
    return windows


def _window_by_handle(handle: str):
    for win in _desktop().windows():
        try:
            if hex(int(win.handle)) == handle:
                return win
        except Exception:
            continue
    return None


def _grab(box):
    """Screen grab helper (kept tiny so callers stay readable)."""
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=box)


def screenshot(win, stage: str) -> str:
    """Save a PNG of the window (or the whole screen when win is None)."""
    try:
        from PIL import ImageGrab
    except Exception as exc:  # pragma: no cover - dev tool
        log(f"screenshot skipped (PIL unavailable): {exc}")
        return ""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SHOT_DIR / f"final_bill_probe_{stamp}_{stage}.png"
    box = tuple(rect_of(win)) if win is not None else None
    try:
        img = ImageGrab.grab(bbox=box)
        img.save(path)
        log(f"screenshot saved: {path.name}")
        return str(path)
    except Exception as exc:  # pragma: no cover - dev tool
        log(f"screenshot failed: {exc}")
        return ""


# -- checkbox / button helpers ------------------------------------------------


def _checkbox_glyph(ctrl):
    """Grab the checkbox glyph pixels of the 'Final' button (grayscale)."""
    from PIL import ImageGrab
    import numpy as np

    r = ctrl.rectangle()
    # Measured glyph area: the box sits in the top-left ~14x18 px.
    img = ImageGrab.grab(bbox=(r.left + 1, r.top + 6, r.left + 14, r.top + 18))
    return np.array(img.convert("L"))


def _checkbox_dark_ratio(ctrl) -> float:
    """Dark-pixel ratio inside the checkbox glyph of the 'Final' button."""
    arr = _checkbox_glyph(ctrl)
    return float((arr < 128).mean())


def render_checkbox_ascii(ctrl) -> str:
    """ASCII rendering of the 'Final' checkbox glyph (visual evidence)."""
    arr = _checkbox_glyph(ctrl)
    lines = []
    for row in arr:
        lines.append("".join("#" if value < 128 else "." for value in row))
    return "\n".join(lines)


def toggle_final_checkbox(win) -> bool:
    """Check the checkbox beside the word 'Final' (idempotent).

    Verification: the glyph's dark-pixel ratio must INCREASE after clicking
    (an unchecked box is a thin empty square; a checked box has a tick).
    If the click instead DECREASED the ratio the box was already checked,
    so it is clicked a second time to restore "checked".
    """
    import pywinauto

    btn = child(win, BTN_FINAL_ID, "Button")
    if btn is None:
        log("Final button (id 1001) not found on the popup")
        return False
    r = btn.rectangle()
    before = _checkbox_dark_ratio(btn)
    click_x = r.left + 7
    click_y = r.top + 12
    log(
        f"Final checkbox before: dark_ratio={before:.3f} "
        f"button_rect=[{r.left},{r.top},{r.right},{r.bottom}]"
    )
    log("glyph before:\n" + render_checkbox_ascii(btn))
    log(f"clicking checkbox at ({click_x}, {click_y})")
    force_foreground(win)
    pywinauto.mouse.click(coords=(click_x, click_y))
    time.sleep(0.4)
    after = _checkbox_dark_ratio(btn)
    log(f"Final checkbox after click 1: dark_ratio={after:.3f}")
    if after <= before:
        log("ratio did not increase - box was already checked; restoring")
        force_foreground(win)
        pywinauto.mouse.click(coords=(click_x, click_y))
        time.sleep(0.4)
        after = _checkbox_dark_ratio(btn)
        log(f"Final checkbox after click 2: dark_ratio={after:.3f}")
    checked = after > before
    log(f"checkbox checked: {checked}")
    log("glyph now:\n" + render_checkbox_ascii(btn))
    return checked


def click_ok(win) -> bool:
    """Press OK on the popup (id 1002)."""
    btn = child(win, BTN_OK_ID, "Button")
    if btn is None:
        log("OK button (id 1002) not found on the popup")
        return False
    force_foreground(win)
    r = btn.rectangle()
    import pywinauto

    pywinauto.mouse.click(coords=((r.left + r.right) // 2, (r.top + r.bottom) // 2))
    log(f"clicked OK at {rect_of(btn)} (real mouse)")
    time.sleep(0.4)
    return True


def click_button_by_text(win, labels) -> str:
    """Click the first visible button whose text matches one of labels."""
    wanted = [label.lower() for label in labels]
    for ctrl in win.children():
        try:
            if not ctrl.is_visible():
                continue
            text = ctrl.window_text().strip().lower()
        except Exception:
            continue
        if text in wanted:
            log(f"clicking button {ctrl.window_text()!r} (id {ctrl.control_id()})")
            ctrl.click_input()
            return ctrl.window_text()
    return ""


BM_CLICK = 0x00F5


def force_foreground(win) -> bool:
    """Bring a window to the front so real mouse clicks land on it.

    Needed because the IDE/terminal window can sit on top of HBSys dialogs;
    a pyautogui/click_input click would then hit the wrong window.
    """
    try:
        import win32gui
    except Exception as exc:  # pragma: no cover - dev tool
        log(f"foreground check skipped (win32gui unavailable): {exc}")
        return False
    handle = int(win.handle)
    try:
        win.set_focus()
    except Exception:
        pass
    for attempt in range(3):
        try:
            win32gui.SetForegroundWindow(handle)
        except Exception as exc:
            log(f"SetForegroundWindow attempt {attempt + 1} failed: {exc}")
        time.sleep(0.25)
        if win32gui.GetForegroundWindow() == handle:
            return True
    log("could not force window to foreground")
    return False


def dialog_button(win, labels):
    """First visible Button child whose text matches one of labels."""
    wanted = [label.lower() for label in labels]
    for ctrl in win.children():
        try:
            if not ctrl.is_visible():
                continue
            if ctrl.class_name() != "Button":
                continue
            text = ctrl.window_text().strip().lower()
        except Exception:
            continue
        if text in wanted:
            return ctrl
    return None


def press_dialog_button(win, labels) -> str:
    """Click a standard Win32 dialog button via BM_CLICK (no mouse needed).

    Used for the HBSys '#32770' message boxes (Call Administrator,
    File save, ...) because those are ordinary Windows buttons and BM_CLICK
    works even when another application owns the foreground.
    """
    ctrl = dialog_button(win, labels)
    if ctrl is None:
        log(f"no dialog button matching {labels}")
        return ""
    text = ctrl.window_text()
    log(
        f"clicking dialog button {text!r} (id {ctrl.control_id()}) "
        f"rect={rect_of(ctrl)} via BM_CLICK"
    )
    ctrl.send_message(BM_CLICK, 0, 0)
    time.sleep(0.4)
    return text


def raise_to_top(win) -> bool:
    """Temporarily float a window above everything (it may hide behind the IDE)."""
    try:
        import win32con
        import win32gui
    except Exception as exc:  # pragma: no cover - dev tool
        log(f"raise skipped (win32gui unavailable): {exc}")
        return False
    handle = int(win.handle)
    try:
        win32gui.SetWindowPos(
            handle,
            win32con.HWND_TOPMOST,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
        )
        return True
    except Exception as exc:  # pragma: no cover - dev tool
        log(f"raise failed: {exc}")
        return False


def click_dialog_button_mouse(win, labels) -> str:
    """Click a dialog button with a REAL mouse click (after raising the dialog).

    This is the path that works for HBSys message boxes: BM_CLICK is ignored
    by the PowerBuilder modal loop, and a plain mouse click misses the button
    when another application (the IDE) owns the foreground/top z-order.
    """
    try:
        import pywinauto
        import win32gui
    except Exception as exc:  # pragma: no cover - dev tool
        log(f"mouse click unavailable: {exc}")
        return ""
    ctrl = dialog_button(win, labels)
    if ctrl is None:
        log(f"no dialog button matching {labels}")
        return ""
    r = ctrl.rectangle()
    cx = (r.left + r.right) // 2
    cy = (r.top + r.bottom) // 2
    text = ctrl.window_text()
    raise_to_top(win)
    time.sleep(0.3)
    at_point = win32gui.WindowFromPoint((cx, cy))
    log(
        f"window at the {text!r} button centre ({cx}, {cy}) is "
        f"{hex(at_point)} {win32gui.GetWindowText(at_point)!r} "
        f"{win32gui.GetClassName(at_point)!r}"
    )
    pywinauto.mouse.click(coords=(cx, cy))
    time.sleep(0.5)
    return text


def _tooltip_rect():
    """Bounding box of the visible PowerBuilder tooltip window, if any."""
    for win in _desktop().windows():
        try:
            if not win.is_visible():
                continue
            if win.class_name() != "PBTooltips16_70":
                continue
            r = win.rectangle()
            if r.width() > 10 and r.height() > 5 and r.top > 0:
                return (r.left, r.top, r.right, r.bottom)
        except Exception:
            continue
    return None


def toolbar_tooltip_label(x: int, y: int, settle: float = 1.4) -> str:
    """Hover a toolbar slot and OCR the PowerBuilder tooltip -> button name.

    HBSys toolbars are PowerBuilder canvases with NO child controls and the
    tooltip window carries no window text (it is custom drawn), so the label
    is read from the tooltip pixels. This is how the agent resolves a button
    such as 'Close Form' without hardcoding a coordinate guess.
    """
    import pywinauto
    from PIL import Image

    try:
        import pytesseract
    except Exception as exc:  # pragma: no cover - dev tool
        return f"<no OCR: {exc}>"
    pywinauto.mouse.move(coords=(x, y))
    time.sleep(settle)
    box = _tooltip_rect()
    if box is None:
        return ""
    img = _grab(box).convert("L")
    big = img.resize((img.width * 3, img.height * 3), Image.LANCZOS)
    return pytesseract.image_to_string(big, config="--psm 7").strip()


def click_toolbar_button(x: int, y: int) -> str:
    """Click a toolbar slot after confirming its tooltip label by OCR."""
    import pywinauto

    label = toolbar_tooltip_label(x, y)
    log(f"toolbar slot ({x}, {y}) tooltip OCR: {label!r}")
    pywinauto.mouse.click(coords=(x, y))
    time.sleep(0.6)
    return label


def list_open_forms() -> list:
    """Titles of the MDI child forms currently open in HBSys."""
    main = find_main_window()
    if main is None:
        return []
    titles = []
    for ctrl in main.children():
        try:
            if ctrl.class_name() != MAIN_CLASS:
                continue
            if not ctrl.is_visible():
                continue
            titles.append(ctrl.window_text())
        except Exception:
            continue
    return titles


def find_dialog(*title_markers):
    """Top-level visible window whose title contains one of title_markers."""
    wanted = [marker.lower() for marker in title_markers]
    for win in _desktop().windows():
        try:
            if not win.is_visible():
                continue
            title = win.window_text()
        except Exception:
            continue
        if not title:
            continue
        lower = title.lower()
        if any(marker in lower for marker in wanted):
            return win
    return None


def describe_dialog(win) -> str:
    """Read the static text of a dialog so we know exactly what it asks."""
    parts = []
    for ctrl in win.children():
        try:
            if not ctrl.is_visible():
                continue
            if ctrl.class_name() != "Static":
                continue
            text = ctrl.window_text().strip()
            if text:
                parts.append(text)
        except Exception:
            continue
    return " | ".join(parts)


def watch_new_windows(seconds: float, known_handles) -> list:
    """Poll the desktop and record every newly appeared HBSys window."""
    seen = []
    deadline = time.time() + seconds
    logged = set()
    while time.time() < deadline:
        for info in dump_desktop_hbsys():
            handle = info["handle"]
            if handle in known_handles or handle in logged:
                continue
            logged.add(handle)
            log(
                f"NEW WINDOW {handle} title={info['title']!r} "
                f"class={info['class_name']!r} rect={info['rectangle']}"
            )
            win = _window_by_handle(handle)
            dump = dump_window(win) if win is not None else info
            seen.append(dump)
            for ctrl in dump.get("visible_children", []):
                log(
                    f"    child text={ctrl['text']!r} class={ctrl['class_name']!r} "
                    f"id={ctrl['control_id']} rect={ctrl['rectangle']}"
                )
            if win is not None:
                screenshot(win, f"new_{handle}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="HBSys Final Bill probe")
    parser.add_argument("--open", action="store_true", help="Billing -> Final Bill")
    parser.add_argument("--snapshot", action="store_true", help="dump the popup tree")
    parser.add_argument("--click-final", action="store_true")
    parser.add_argument("--click-ok", action="store_true")
    parser.add_argument(
        "--watch", type=float, default=6.0, help="seconds to watch for popups"
    )
    parser.add_argument(
        "--answer-no", action="store_true", help="click No on the open dialog"
    )
    parser.add_argument(
        "--answer-ok", action="store_true", help="click OK on the open dialog"
    )
    parser.add_argument("--list", action="store_true", help="list HBSys windows")
    parser.add_argument("--forms", action="store_true", help="list open MDI forms")
    parser.add_argument(
        "--toolbar-tooltip",
        metavar="X,Y",
        help="hover a toolbar slot and OCR its tooltip label",
    )
    parser.add_argument(
        "--click-toolbar",
        metavar="X,Y",
        help="confirm a toolbar slot by tooltip OCR, then click it",
    )
    args = parser.parse_args(argv)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    evidence = {"started": datetime.now().isoformat()}

    main_win = find_main_window()
    if main_win is None:
        log("HBSys main window (FNWND370 / HOMIS) not found - is it running?")
        return 2
    log(
        f"HBSys main: {hex(int(main_win.handle))} title={main_win.window_text()!r} "
        f"pid={main_win.process_id()}"
    )
    evidence["main_window"] = dump_window(main_win)

    # Hospital No. currently loaded (Edit 1004 on the Billing form).
    for ctrl in main_win.children():
        try:
            if ctrl.control_id() == 1004 and ctrl.class_name() == "Edit":
                log(f"Hospital No. field = {ctrl.window_text()!r}")
                evidence["hospital_no"] = ctrl.window_text()
        except Exception:
            continue

    known = {info["handle"] for info in dump_desktop_hbsys()}
    log(f"open MDI forms before: {list_open_forms()}")
    evidence["forms_before"] = list_open_forms()

    if args.forms:
        for title in list_open_forms():
            log(f"form: {title!r}")

    if args.toolbar_tooltip:
        x, y = (int(v) for v in args.toolbar_tooltip.split(","))
        label = toolbar_tooltip_label(x, y)
        log(f"toolbar slot ({x}, {y}) tooltip OCR: {label!r}")
        evidence["toolbar_tooltip"] = {"x": x, "y": y, "label": label}

    if args.click_toolbar:
        x, y = (int(v) for v in args.click_toolbar.split(","))
        label = click_toolbar_button(x, y)
        evidence["toolbar_click"] = {"x": x, "y": y, "label": label}
        evidence["followups"] = watch_new_windows(args.watch, known)
        evidence["forms_after"] = list_open_forms()
        log(f"open MDI forms after: {evidence['forms_after']}")

    if args.list:
        for info in dump_desktop_hbsys():
            log(
                f"window {info['handle']} title={info['title']!r} "
                f"class={info['class_name']!r} rect={info['rectangle']}"
            )

    if args.answer_no or args.answer_ok:
        dialog = find_dialog("call administrator", "file save", "save", "error")
        if dialog is None:
            log("no blocking dialog found to answer")
        else:
            log(
                f"dialog: {hex(int(dialog.handle))} title={dialog.window_text()!r} "
                f"class={dialog.class_name()!r} rect={rect_of(dialog)}"
            )
            log(f"dialog says: {describe_dialog(dialog)!r}")
            evidence["dialog"] = dump_window(dialog)
            screenshot(dialog, "dialog")
            label = "No" if args.answer_no else "OK"
            clicked = click_dialog_button_mouse(dialog, (label, f"&{label}"))
            log(f"clicked {clicked!r} on the dialog")
            evidence["dialog_answer"] = clicked
            evidence["followups"] = watch_new_windows(args.watch, known)

    popup = find_print_options()
    if args.open and popup is None:
        main_win.set_focus()
        time.sleep(0.3)
        log("selecting menu Billing -> Final Bill")
        main_win.menu_select("Billing -> Final Bill")
        for _ in range(25):
            time.sleep(0.2)
            popup = find_print_options()
            if popup is not None:
                break
    if popup is None:
        log("Print Options popup is not open")
    else:
        log(f"Print Options popup: {hex(int(popup.handle))} rect={rect_of(popup)}")
        evidence["print_options"] = dump_window(popup)
        screenshot(popup, "print_options")
        if args.snapshot:
            for ctrl in evidence["print_options"]["visible_children"]:
                log(
                    f"    child text={ctrl['text']!r} class={ctrl['class_name']!r} "
                    f"id={ctrl['control_id']} rect={ctrl['rectangle']}"
                )
        if args.click_final:
            evidence["final_checked"] = toggle_final_checkbox(popup)
            screenshot(popup, "after_click_final")
        if args.click_ok:
            evidence["ok_clicked"] = click_ok(popup)
            log("OK clicked - watching for follow-up popups")
            evidence["followups"] = watch_new_windows(args.watch, known)

    evidence["log"] = _LOG_LINES
    out = LOG_DIR / f"final_bill_probe_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    log(f"evidence written: {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
