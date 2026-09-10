"""Claim Attachments Upload — Main loop controller.

Automates the HBSys UPLOAD CLAIM ATTACHMENTS workflow for attaching
supporting documents (PDFs + XMLs) to patient claims.

Workflow per patient:
    1. Type patient name in search box
    2. Click Search
    3. Patient row gets highlighted (blue band)
    4. Click "attach..." on the highlighted row
    5. Attachment popup opens ("Attachments for PATIENT - CLAIM_NO")
    6. Click "Attach..." button in popup → file dialog opens
    7. Type patient folder path in file dialog
    8. Ctrl+A → Open  (attaches all files from patient folder)
    9. Click "Attach..." again → file dialog opens
   10. Change "Files of type" to XML files
   11. Ctrl+A → Open  (attaches all XML files)
   12. Assign doc type per grid row (folder-driven OCR line matching),
       then Upload → OK
   13. Close attachment popup
   14. Clear search box → next patient

Design follows loop-engineering principles:
    - Goal-based loop with machine-checkable stop condition
    - State persisted to filesystem (resumable)
    - Verification before every irreversible action
    - Escalation on consecutive failures

Coordinates:
    All coordinates are ABSOLUTE screen coordinates for 1920x1080.
    The main window must be maximized.

Usage:
    # Dry-run (no clicks)
    python -m core.claim_attachments_uploader

    # Live mode
    python -m core.claim_attachments_uploader --live

    # Live with confirmation before each patient
    python -m core.claim_attachments_uploader --live --confirm-each

    # Resume a failed batch
    python -m core.claim_attachments_uploader --live --resume
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pyautogui
from PIL import Image, ImageDraw
from pywinauto import Desktop

from core.add_claims_verifier import FolderDates, parse_folder_name
from core.attachments_state import AttachmentsState
from core.claim_attachments_doctype_audit import (
    ECLAIMS_DOC_ROOT,
    SourceAuditCollector,
    audit_claim_series,
    extract_claim_series_from_title,
    finalize_doctype_audit,
)
from core.claim_attachments_doc_type import (
    detect_doc_type,
    find_highlight_band,
    match_copied_path_to_file,
)


LOG_DIR = Path("logs")
DEFAULT_READY_DIR = Path(r"C:\claims_bot\claims_checker_results\READY")

# Maximum consecutive failures before the loop stops (Principle 4 — guardrail)
MAX_CONSECUTIVE_FAILURES = 3

# Maximum doc-type grid rows processed per patient (guardrail against
# runaway TAB loops if the grid layout is misdetected)
MAX_DOC_ROWS = 40


# -- auto-reload (watch mode) ---------------------------------------------

def _get_source_mtimes() -> dict[str, float]:
    """Record modification times of all .py files in the project."""
    project_root = Path(__file__).resolve().parent.parent
    mtimes: dict[str, float] = {}
    for py_file in project_root.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        try:
            mtimes[str(py_file)] = py_file.stat().st_mtime
        except OSError:
            pass
    return mtimes


def check_for_code_changes(stored_mtimes: dict[str, float]) -> list[str]:
    """Check if any .py file has changed since stored_mtimes.

    Returns list of changed file paths, or empty list if no changes.
    """
    project_root = Path(__file__).resolve().parent.parent
    changed: list[str] = []
    for py_file in project_root.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        try:
            current_mtime = py_file.stat().st_mtime
            old_mtime = stored_mtimes.get(str(py_file), 0)
            if current_mtime > old_mtime:
                changed.append(str(py_file))
        except OSError:
            pass
    return changed


def restart_script() -> None:
    """Restart the current Python script (preserving all CLI args)."""
    print("\n" + "=" * 60)
    print("  CODE CHANGED — restarting script...")
    print("=" * 60)
    os.execv(sys.executable, [sys.executable] + sys.argv)


# -- Coordinate points (absolute screen coords for 1920x1080) -----------


@dataclass(frozen=True)
class Point:
    x: int
    y: int


# -- Doc-type grid coordinates (from pixel recon of SS_choose_doc_type.png) --

DOC_FIELD_X = 497          # X of the Doc Type combo field (column x=470..524)
DOC_ARROW_X = 519          # X of the combo dropdown arrow (right side of cell)

# Upload/Close button coordinates in the attachments popup (user-provided)
DOC_UPLOAD_BUTTON = (598, 730)
DOC_CLOSE_BUTTON = (1408, 734)

# v6 clipboard copy: X inside the File Name / path column of the popup
# grid (x≈526..1010), safely right of the Doc Type combo column (x≤524)
ROW_COPY_X = 700

# Popup grid area for row separator scanning (left, top, right, bottom).
# The popup rect in the live test was (464,322,1457,758); the grid header
# band sits at y≈350..384 and the first data separator at y≈413.
DOC_GRID_BOUNDS = (470, 390, 1440, 700)


@dataclass
class CalibratedPoints:
    """Calibrated coordinates for the Claim Attachments workflow.

    All coordinates are ABSOLUTE screen coordinates.
    The main HBSys window should be maximized.
    """

    # --- Main UPLOAD CLAIM ATTACHMENTS window ---
    # Search box and button (absolute screen coords)
    search_box: tuple[int, int] = (110, 136)
    search_button: tuple[int, int] = (410, 139)

    # "attach..." column X position (absolute screen coords)
    # Y is detected dynamically from the blue highlighted band
    attach_column_x: int = 1340

    # --- Attachment popup ---
    # "Attach..." button in the attachment popup
    popup_attach_button: tuple[int, int] = (513, 732)

    # --- File dialog (Windows standard Open dialog) ---
    file_dialog_path_field: tuple[int, int] = (559, 561)
    file_dialog_files_type: tuple[int, int] = (582, 593)
    file_dialog_xml_option: tuple[int, int] = (745, 613)
    file_dialog_open: tuple[int, int] = (838, 564)

    # Doc-type grid (calibrated values)
    doc_type_field_x: int = 505
    doc_type_arrow_x: int = 537
    doc_grid_left: int = 460
    doc_grid_top: int = 400
    doc_grid_right: int = 1010
    doc_grid_bottom: int = 700
    doc_column_right: int = 524
    doc_upload_button: tuple[int, int] = (598, 730)
    doc_close_button: tuple[int, int] = (1408, 734)

    def save(self, path: Path | None = None) -> Path:
        import json
        from dataclasses import asdict
        target = path or (LOG_DIR / "claim_attachments_calibration.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        # Remove internal calibration fields before writing
        for key in ("doc_type_field_x", "doc_type_arrow_x", "doc_grid_left",
                     "doc_grid_top", "doc_grid_right", "doc_column_right",
                     "doc_upload_button", "doc_close_button"):
            data.pop(key, None)
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: Path | None = None) -> "CalibratedPoints":
        import json
        target = path or (LOG_DIR / "claim_attachments_calibration.json")
        if not target.exists():
            return cls()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            for key in ("search_box", "search_button", "popup_attach_button",
                        "file_dialog_path_field", "file_dialog_files_type",
                        "file_dialog_xml_option", "file_dialog_open"):
                if key in data and isinstance(data[key], list):
                    data[key] = tuple(data[key])
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            return cls()


# Load calibrated coordinates at module import time
_calibrated = CalibratedPoints.load()


class P:
    """Named screen coordinates for the Claim Attachments workflow.

    All coordinates are ABSOLUTE screen coordinates.
    """

    SEARCH_BOX = Point(*_calibrated.search_box)
    SEARCH_BUTTON = Point(*_calibrated.search_button)
    ATTACH_COLUMN_X = _calibrated.attach_column_x

    POPUP_ATTACH_BUTTON = Point(*_calibrated.popup_attach_button)

    FILE_DIALOG_PATH = Point(*_calibrated.file_dialog_path_field)
    FILE_DIALOG_TYPE = Point(*_calibrated.file_dialog_files_type)
    FILE_DIALOG_XML = Point(*_calibrated.file_dialog_xml_option)
    FILE_DIALOG_OPEN = Point(*_calibrated.file_dialog_open)
    FILE_LIST_FOCUS = Point(595, 509)  # click inside file list to focus before Ctrl+A


def sleep_short(seconds: float = 0.35) -> None:
    time.sleep(seconds)


# -- blue band detection ------------------------------------------------

def _is_blue_band(pixel: tuple[int, ...]) -> bool:
    """Check if a pixel is part of the HBSys blue highlight band."""
    if len(pixel) < 3:
        return False
    red, green, blue = pixel[:3]
    return blue >= 140 and 60 <= green <= 200 and red <= 100 and (blue - red) >= 40


def detect_highlighted_row_y(
    image: Image.Image,
    scan_start_y: int = 150,
    scan_end_y: int = 400,
    scan_start_x: int = 50,
    scan_end_x: int = 1300,
) -> float | None:
    """Detect the vertical centre of the blue highlight band in a screenshot.

    Returns the screen Y coordinate of the row centre, or None.
    """
    width, height = image.size

    blue_rows: list[int] = []
    for y in range(scan_start_y, min(height, scan_end_y)):
        blue_count = 0
        for x in range(scan_start_x, min(width, scan_end_x), 6):
            if _is_blue_band(image.getpixel((x, y))):
                blue_count += 1
        if blue_count >= 15:
            blue_rows.append(y)

    if not blue_rows:
        return None

    # Group contiguous blue rows into bands
    bands: list[list[int]] = []
    for y in blue_rows:
        if bands and y - bands[-1][-1] <= 2:
            bands[-1].append(y)
        else:
            bands.append([y])

    if not bands:
        return None

    # The highlighted result row is the widest blue band
    best_band = max(bands, key=len)
    return (min(best_band) + max(best_band)) / 2.0


# -- operator class -----------------------------------------------------

class AttachmentsOperator:
    """UI automation operator for the Claim Attachments workflow.

    Supports dry-run mode (no clicks) for safe testing.
    """

    def __init__(self, live: bool, pause: float, confirm_each: bool):
        self.live = live
        self.pause = pause
        self.confirm_each = confirm_each
        self.hbsys_window: Optional[object] = None
        self.attachment_popup: Optional[object] = None
        self._pre_attach_handles: set[int] = set()
        # v7 source-to-destination audit: source records are collected
        # in memory in ACTUAL processing order (per claim series); the
        # destination comparison runs once per claim series after its
        # upload; the Excel report is built once after the batch.
        self.source_audit = SourceAuditCollector()
        self.doctype_audit_results: list = []

    def log_action(self, message: str) -> None:
        prefix = "LIVE" if self.live else "DRY"
        print(f"[{prefix}] {message}")

    def maybe_wait(self) -> None:
        time.sleep(self.pause)

    def click(self, point: Point, label: str) -> None:
        """Click an absolute screen coordinate."""
        self.log_action(f"click {label} at ({point.x}, {point.y})")
        if self.live:
            pyautogui.click(point.x, point.y)
        self.maybe_wait()

    def press(self, key: str) -> None:
        self.log_action(f"press {key}")
        if self.live:
            pyautogui.press(key)
        self.maybe_wait()

    def hotkey(self, *keys: str) -> None:
        self.log_action(f"hotkey {'+'.join(keys)}")
        if self.live:
            pyautogui.hotkey(*keys)
        self.maybe_wait()

    def write(self, text: str) -> None:
        self.log_action(f"type {text!r}")
        if self.live:
            pyautogui.write(text, interval=0.01)
        self.maybe_wait()

    def verify_screen_layout(self) -> None:
        width, height = pyautogui.size()
        self.log_action(f"screen size detected: {width}x{height}")
        if self.live and (width, height) != (1920, 1080):
            raise RuntimeError(
                "Screen must be 1920x1080 for this coordinate-based script."
            )

    # -- navigation -------------------------------------------------------

    def focus_hbsys(self) -> None:
        """Find and focus the main HBSys UPLOAD CLAIM ATTACHMENTS window."""
        desktop = Desktop(backend="win32")
        for window in desktop.windows():
            try:
                title = window.window_text().strip()
            except Exception:
                continue
            title_upper = title.upper()
            if "UPLOAD" in title_upper and "CLAIM" in title_upper and "ATTACHMENT" in title_upper:
                self.log_action(f"focus HBSys window: {title!r}")
                if self.live:
                    try:
                        window.maximize()
                        window.set_focus()
                    except Exception:
                        pass
                self.hbsys_window = window
                self.maybe_wait()
                self.verify_screen_layout()
                return
        raise RuntimeError(
            "HBSys UPLOAD CLAIM ATTACHMENTS window not found. "
            "Open HBSys and navigate to Upload Claim Attachments first."
        )

    # -- patient search ---------------------------------------------------

    def search_patient(self, patient_name: str) -> bool:
        """Type patient name in search box and click Search.

        Returns True if search was executed successfully.
        """
        self.log_action(f"searching for patient: {patient_name}")

        # Focus main window — retry up to 3 times
        for attempt in range(3):
            if self.hbsys_window is not None:
                try:
                    self.hbsys_window.set_focus()
                    self.log_action(f"main window focused (attempt {attempt + 1})")
                    sleep_short(0.5)
                    break
                except Exception as exc:
                    self.log_action(f"WARNING: could not focus main window: {exc}")
                    sleep_short(0.5)

        # Click search box — click twice to ensure focus
        self.log_action(f"clicking search box at ({P.SEARCH_BOX.x}, {P.SEARCH_BOX.y})")
        pyautogui.click(P.SEARCH_BOX.x, P.SEARCH_BOX.y)
        sleep_short(0.15)
        pyautogui.click(P.SEARCH_BOX.x, P.SEARCH_BOX.y)
        sleep_short(0.15)

        # Select all existing text and delete
        self.log_action("selecting all text in search box (Ctrl+A)")
        pyautogui.hotkey("ctrl", "a")
        sleep_short(0.15)
        pyautogui.press("delete")
        sleep_short(0.2)

        # Type patient name
        self.log_action(f"typing patient name: {patient_name!r}")
        pyautogui.write(patient_name, interval=0.02)
        sleep_short(0.5)

        # Click Search button — double-click to ensure it registers
        self.log_action(f"double-clicking Search button at ({P.SEARCH_BUTTON.x}, {P.SEARCH_BUTTON.y})")
        pyautogui.doubleClick(P.SEARCH_BUTTON.x, P.SEARCH_BUTTON.y)
        sleep_short(1.0)

        # Also press Enter as fallback (some search boxes need it)
        self.log_action("pressing Enter as fallback search trigger")
        pyautogui.press("enter")
        sleep_short(3.0)  # wait for search results to load

        # Take screenshot to verify highlight moved
        if self.live:
            screenshot = pyautogui.screenshot()
            row_y = detect_highlighted_row_y(screenshot)
            if row_y is not None:
                self.log_action(f"after search: highlighted row detected at y={row_y:.0f}")
            else:
                self.log_action("after search: NO highlighted row detected!")
                debug_path = LOG_DIR / f"debug_search_no_highlight_{datetime.now():%Y%m%d_%H%M%S}.png"
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                screenshot.save(debug_path)
                self.log_action(f"DEBUG screenshot saved: {debug_path}")

        return True

    def click_attach_on_highlighted_row(self) -> bool:
        """Detect the blue highlighted row and click 'attach...' on it.

        Returns True if successful.
        """
        if not self.live:
            self.log_action("would click 'attach...' on highlighted row")
            return True

        # Capture full screen to detect the blue band
        screenshot = pyautogui.screenshot()
        width, height = screenshot.size
        self.log_action(f"screenshot: {width}x{height}")

        # Detect blue band Y position
        row_y = detect_highlighted_row_y(
            screenshot,
            scan_start_y=150,
            scan_end_y=400,
            scan_start_x=50,
            scan_end_x=1300,
        )

        if row_y is None:
            self.log_action("no blue highlighted row detected after search")
            return False

        self.log_action(f"highlighted row center y={row_y:.0f}")

        # Click "attach..." at (ATTACH_COLUMN_X, row_y)
        click_x = P.ATTACH_COLUMN_X
        click_y = int(round(row_y))

        self.log_action(f"clicking 'attach...' at screen ({click_x}, {click_y})")

        # Save debug screenshot
        self._save_debug_screenshot(screenshot, click_x, click_y, row_y, "attach_row")

        pyautogui.moveTo(click_x, click_y, duration=0.08)
        pyautogui.click(click_x, click_y, clicks=1)
        sleep_short(1.5)

        return True

    def _save_debug_screenshot(
        self, screenshot: Image.Image, click_x: int, click_y: int,
        row_y: float, label: str,
    ) -> None:
        """Save annotated screenshot showing where the click will happen."""
        debug = screenshot.copy()
        draw = ImageDraw.Draw(debug)

        # Red crosshair at click position
        cross_size = 30
        draw.line(
            [(click_x - cross_size, click_y), (click_x + cross_size, click_y)],
            fill="red", width=3,
        )
        draw.line(
            [(click_x, click_y - cross_size), (click_x, click_y + cross_size)],
            fill="red", width=3,
        )

        # Red circle
        r = 15
        draw.ellipse(
            [click_x - r, click_y - r, click_x + r, click_y + r],
            outline="red", width=3,
        )

        # Blue line at highlighted row Y
        draw.line(
            [(0, int(row_y)), (screenshot.width, int(row_y))],
            fill="blue", width=2,
        )

        # Text labels
        draw.text(
            (click_x + 20, click_y - 10),
            f"CLICK ({click_x},{click_y})",
            fill="red",
        )
        draw.text(
            (10, int(row_y) - 20),
            f"Blue band y={row_y:.0f}",
            fill="blue",
        )

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"debug_attachments_{label}_{datetime.now():%Y%m%d_%H%M%S}.png"
        debug.save(path)
        self.log_action(f"DEBUG screenshot saved: {path}")

    # -- attachment popup --------------------------------------------------

    def find_attachment_popup(self, timeout: float = 8.0) -> bool:
        """Wait for the attachment popup to appear ("Attachments for ...").

        Returns True if found.
        """
        if not self.live:
            self.log_action("would wait for attachment popup")
            return True

        deadline = time.time() + timeout
        while time.time() < deadline:
            popup = self._find_popup_window()
            if popup is not None:
                self.attachment_popup = popup
                try:
                    title = popup.window_text().strip()
                    rect = popup.rectangle()
                    self.log_action(
                        f"Attachment popup found: title={title!r} "
                        f"rect=({rect.left},{rect.top},{rect.right},{rect.bottom})"
                    )
                    popup.set_focus()
                except Exception as exc:
                    self.log_action(f"Popup found but error reading: {exc}")
                return True
            time.sleep(0.3)

        self.log_action("Attachment popup NOT found within timeout")
        return False

    def _find_popup_window(self) -> Optional[object]:
        """Find the attachment popup window by title."""
        desktop = Desktop(backend="win32")
        for window in desktop.windows():
            try:
                title = window.window_text().strip()
            except Exception:
                continue
            title_upper = title.upper()
            if "ATTACHMENTS" in title_upper and "FOR" in title_upper:
                return window
        return None

    def click_popup_attach_button(self) -> bool:
        """Click the 'Attach...' button in the attachment popup.

        Records window handles BEFORE clicking so that wait_for_file_dialog()
        can detect the NEW file dialog window that appears.

        Strategy:
        1. pywinauto Application connect → child_window click
        2. Find #32770 dialog → connect → child_window click
        3. Calculate from popup rect
        4. Absolute screen coordinates

        Returns True if successful.
        """
        self.log_action("clicking 'Attach...' button in popup")

        # --- Record ALL current window handles BEFORE clicking ---
        # This lets wait_for_file_dialog() detect the NEW file dialog
        self._pre_attach_handles = set()
        try:
            desktop = Desktop(backend="win32")
            for window in desktop.windows():
                try:
                    self._pre_attach_handles.add(window.handle)
                except Exception:
                    pass
        except Exception:
            pass
        self.log_action(f"recorded {len(self._pre_attach_handles)} pre-attach handles")

        if self.attachment_popup is not None:
            try:
                self.attachment_popup.set_focus()
                sleep_short(0.3)
            except Exception:
                pass

            # Strategy 1: pywinauto Application connect → child_window
            try:
                from pywinauto import Application
                popup_handle = self.attachment_popup.handle
                app = Application(backend="win32").connect(handle=popup_handle)
                dlg = app.window(handle=popup_handle)
                btn = dlg.child_window(title="Attach...", class_name="Button")
                self.log_action("pywinauto: found Attach... button, clicking...")
                btn.click_input()
                self.log_action("pywinauto: Attach... button clicked")
                sleep_short(2.5)
                return True
            except Exception as exc:
                self.log_action(f"pywinauto strategy1 failed: {exc}")

            # Strategy 2: Find #32770 'Attachments' dialog → connect → click
            try:
                from pywinauto import Application
                desktop = Desktop(backend="win32")
                for win in desktop.windows():
                    try:
                        cls = win.class_name()
                        title = win.window_text().strip()
                    except Exception:
                        continue
                    if cls == "#32770" and "ATTACH" in title.upper():
                        self.log_action(f"found #32770 dialog: {title!r}")
                        app2 = Application(backend="win32").connect(handle=win.handle)
                        dlg2 = app2.window(handle=win.handle)
                        btn2 = dlg2.child_window(title="Attach...", class_name="Button")
                        btn2.click_input()
                        self.log_action("pywinauto: Attach... clicked via #32770")
                        sleep_short(2.5)
                        return True
            except Exception as exc:
                self.log_action(f"pywinauto strategy2 failed: {exc}")

            # Strategy 3: Calculate from popup rect
            try:
                rect = self.attachment_popup.rectangle()
                click_x = rect.left + 49
                click_y = rect.bottom - 26
                self.log_action(f"rect-based click at ({click_x}, {click_y})")
                pyautogui.click(click_x, click_y)
                sleep_short(2.5)
                return True
            except Exception as exc:
                self.log_action(f"rect-based click failed: {exc}")

        # Strategy 4: absolute coordinates
        self.click(P.POPUP_ATTACH_BUTTON, "Popup Attach button (absolute)")
        sleep_short(2.5)
        return True

    def close_attachment_popup(self) -> None:
        """Close the attachment popup by clicking Close or pressing Escape.

        Verifies the popup is actually gone before returning.
        """
        self.log_action("closing attachment popup")

        if self.attachment_popup is not None:
            # Strategy 1: pywinauto Close button
            try:
                from pywinauto import Application
                handle = self.attachment_popup.handle
                app = Application(backend="win32").connect(handle=handle)
                dlg = app.window(handle=handle)
                close_btn = dlg.child_window(
                    title="Close", class_name="Button"
                )
                close_btn.click_input()
                self.log_action("Close button clicked via pywinauto")
                sleep_short(1.0)
            except Exception as exc:
                self.log_action(f"pywinauto Close failed: {exc}")

                # Strategy 2: Coordinate-based Close button
                try:
                    rect = self.attachment_popup.rectangle()
                    close_x = rect.right - 60
                    close_y = rect.bottom - 26
                    self.log_action(f"clicking Close at ({close_x}, {close_y})")
                    pyautogui.click(close_x, close_y)
                    sleep_short(1.0)
                except Exception:
                    # Strategy 3: Escape key
                    try:
                        self.attachment_popup.set_focus()
                    except Exception:
                        pass
                    pyautogui.press("escape")
                    self.log_action("pressed Escape to close popup")
                    sleep_short(1.0)

        # Verify popup is gone (retry up to 3 times)
        for attempt in range(3):
            if self._find_popup_window() is None:
                self.log_action("popup confirmed closed")
                self.attachment_popup = None
                return
            self.log_action(f"popup still open, attempt {attempt + 1}/3...")
            pyautogui.press("escape")
            sleep_short(1.0)

        self.log_action("WARNING: popup may still be open")
        self.attachment_popup = None

    # -- file dialog ------------------------------------------------------

    def close_attachment_popup(self) -> None:
        """Close the attachment popup by clicking Close or pressing Escape.

        Verifies the popup is actually gone before returning.
        """
        self.log_action("closing attachment popup")

        if self.attachment_popup is not None:
            # Strategy 1: pywinauto Close button
            try:
                from pywinauto import Application
                handle = self.attachment_popup.handle
                app = Application(backend="win32").connect(handle=handle)
                dlg = app.window(handle=handle)
                close_btn = dlg.child_window(
                    title="Close", class_name="Button"
                )
                close_btn.click_input()
                self.log_action("Close button clicked via pywinauto")
                sleep_short(1.0)
            except Exception as exc:
                self.log_action(f"pywinauto Close failed: {exc}")

                # Strategy 2: Coordinate-based Close button
                try:
                    rect = self.attachment_popup.rectangle()
                    close_x = rect.right - 60
                    close_y = rect.bottom - 26
                    self.log_action(f"clicking Close at ({close_x}, {close_y})")
                    pyautogui.click(close_x, close_y)
                    sleep_short(1.0)
                except Exception:
                    # Strategy 3: Escape key
                    try:
                        self.attachment_popup.set_focus()
                    except Exception:
                        pass
                    pyautogui.press("escape")
                    self.log_action("pressed Escape to close popup")
                    sleep_short(1.0)

        # Verify popup is gone (retry up to 3 times)
        for attempt in range(3):
            if self._find_popup_window() is None:
                self.log_action("popup confirmed closed")
                self.attachment_popup = None
                return
            self.log_action(f"popup still open, attempt {attempt + 1}/3...")
            pyautogui.press("escape")
            sleep_short(1.0)

        self.log_action("WARNING: popup may still be open")
        self.attachment_popup = None

    # -- doc type assignment ----------------------------------------------

    def _popup_crop_box(
        self, screenshot: Image.Image
    ) -> tuple[int, int, int, int]:
        """Screen-coordinate crop box covering the attachment popup.

        Uses the live popup rectangle when available (robust to the popup
        opening at a different position); falls back to the fixed
        DOC_GRID_BOUNDS region.
        """
        if self.attachment_popup is not None:
            try:
                rect = self.attachment_popup.rectangle()
                left = max(0, int(rect.left))
                top = max(0, int(rect.top))
                right = min(screenshot.width, int(rect.right))
                bottom = min(screenshot.height, int(rect.bottom))
                if right - left > 200 and bottom - top > 200:
                    return (left, top, right, bottom)
            except Exception as exc:
                self.log_action(
                    f"WARNING: popup rect unavailable ({exc}); "
                    f"using fixed grid bounds"
                )
        return DOC_GRID_BOUNDS

    def _save_doc_grid_debug(
        self, crop: Image.Image, name: str | None = None
    ) -> None:
        """Persist the popup-grid OCR crop for failure diagnosis."""
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            if name:
                stem = f"{name}_{datetime.now():%Y%m%d_%H%M%S}"
            else:
                stem = f"debug_doc_grid_{datetime.now():%Y%m%d_%H%M%S}"
            path = LOG_DIR / f"{stem}.png"
            crop.save(path)
            self.log_action(f"DEBUG screenshot saved: {path}")
        except Exception as exc:
            self.log_action(f"WARNING: could not save doc grid debug: {exc}")

    # -- view fingerprint (offline tests) ------------------------------------

    @staticmethod
    def _view_fingerprint(lines: list) -> str:
        """Stable text fingerprint of one OCR view (no-progress guard).

        Retained for the doc_type module's offline test suite. The live
        v6.1 path no longer scrolls the grid (TAB moves the focus and
        HBSys auto-scrolls the focused row into view), so no live caller
        remains.
        """
        import hashlib

        parts = [ln.norm_text for ln in sorted(lines, key=lambda l: l.top)]
        return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()

    # -- v6 clipboard helpers ------------------------------------------------

    def _copy_row_path(self, row_y: int) -> Optional[str]:
        """Right-click a grid row, click "copy", and read the clipboard.

        Owner-verified HBSys behavior (2026-09-07): right-clicking a row
        opens a context menu with three items — "view", "copy", "open
        location". Clicking "copy" (single click; the menu closes itself)
        puts the row's FULL ABSOLUTE PATH (path + filename + extension)
        on the clipboard.

        Strategy 1: locate the #32768 menu window, fetch its item rects
        via MN_GETHMENU/GetMenuItemRect, and click the item whose text
        starts with "copy".
        Strategy 2 (fallback, only when the menu window IS confirmed
        open but item rects failed): keyboard — Down then Enter selects
        the 2nd item ("copy" per the owner-verified 3-item order: view,
        copy, open location). Never used when no menu was detected, so
        stray keypresses can never hit the grid.
        Cleanup: Escape closes any leftover menu; returns None on any
        failure — the caller falls back to OCR for that row (never
        guesses).
        """
        import pyperclip

        try:
            before = pyperclip.paste()
        except Exception:
            before = ""

        self.log_action(f"right-clicking row at ({ROW_COPY_X}, {row_y})")
        pyautogui.rightClick(ROW_COPY_X, row_y)
        sleep_short(0.5)

        menu_hwnd = self._find_context_menu()

        if menu_hwnd is None:
            # No menu opened — nothing to click. Clean up (in case the
            # menu is drawn but not enumerable) and let OCR handle row.
            self.log_action("no context menu appeared — pressing Escape")
            pyautogui.press("escape")
            sleep_short(0.3)
            return None

        item_rect = self._context_menu_copy_rect(menu_hwnd)
        if item_rect is not None:
            cx = (item_rect[0] + item_rect[2]) // 2
            cy = (item_rect[1] + item_rect[3]) // 2
            self.log_action(f"clicking 'copy' menu item at ({cx}, {cy})")
            pyautogui.click(cx, cy)
            return self._poll_clipboard_change(before)

        # Menu confirmed open but rects unavailable — keyboard route:
        # first item is highlighted by default, Down moves to "copy",
        # Enter triggers it.
        self.log_action(
            "menu open, item rect unavailable — keyboard: down + enter"
        )
        pyautogui.press("down")
        sleep_short(0.2)
        pyautogui.press("enter")
        result = self._poll_clipboard_change(before)
        if result is None:
            pyautogui.press("escape")
            sleep_short(0.3)
        return result

    def _find_context_menu(self, timeout: float = 2.0) -> Optional[int]:
        """Find the top-level #32768 context-menu window handle, if any.

        Returns the hwnd, or None when no context menu is open.
        """
        import ctypes

        user32 = ctypes.windll.user32
        deadline = time.time() + timeout
        while time.time() < deadline:
            found: Optional[int] = None
            try:
                desktop = Desktop(backend="win32")
                for window in desktop.windows():
                    try:
                        if window.class_name() == "#32768":
                            found = window.handle
                            break
                    except Exception:
                        continue
            except Exception:
                pass
            if found is not None:
                # Verify the menu is still on screen (GetMenuState fails
                # for dead handles, guarding against stale hwnds).
                if user32.IsWindow(found):
                    return found
            sleep_short(0.1)
        return None

    def _context_menu_copy_rect(
        self, menu_hwnd: int
    ) -> Optional[tuple[int, int, int, int]]:
        """Screen rect of the context-menu "copy" item, via Win32 menus.

        MN_GETHMENU retrieves the HMENU of a #32768 menu window;
        GetMenuItemCount/GetMenuString/GetMenuItemRect then locate the
        item whose text starts with "copy". Returns None on any failure.
        """
        import ctypes

        MN_GETHMENU = 0x01E1
        user32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        hmenu = user32.SendMessageW(menu_hwnd, MN_GETHMENU, 0, 0)
        if not hmenu:
            self.log_action("MN_GETHMENU returned no HMENU")
            return None

        count = user32.GetMenuItemCount(hmenu)
        if count <= 0:
            self.log_action(f"GetMenuItemCount returned {count}")
            return None

        buf = ctypes.create_unicode_buffer(256)
        for index in range(count):
            user32.GetMenuStringW(hmenu, index, buf, 256, 0x400)  # MF_BYPOSITION
            text = buf.value.strip().lower()
            if text.startswith("copy"):
                rect = RECT()
                if user32.GetMenuItemRect(menu_hwnd, hmenu, index, ctypes.byref(rect)):
                    return (rect.left, rect.top, rect.right, rect.bottom)
                self.log_action("GetMenuItemRect failed for 'copy' item")
                return None

        self.log_action(
            f"context menu has no 'copy' item ({count} items scanned)"
        )
        return None

    def _poll_clipboard_change(
        self, before: str, timeout: float = 1.0
    ) -> Optional[str]:
        """Wait until the clipboard differs from *before* and holds text.

        Returns the new clipboard text, or None when it never changed.
        """
        import pyperclip

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                current = pyperclip.paste()
            except Exception:
                current = None
            if current and current != before:
                self.log_action(f"row copy: {current}")
                return current
            sleep_short(0.1)
        self.log_action("clipboard did not change after copy attempt")
        return None

    def _type_row_doc(self, doc: str, row_y: int, idx: int, total: int) -> None:
        """Click field, type doc type, click arrow, press TAB (one row)."""
        self.log_action(f"row {idx}/{total}: doc type {doc} (y={row_y})")
        self.click(Point(DOC_FIELD_X, row_y), f"doc field row {idx}")
        sleep_short(0.3)
        self.write(doc)
        sleep_short(0.3)
        self.click(Point(DOC_ARROW_X, row_y), f"doc arrow row {idx}")
        sleep_short(0.5)
        self.press("tab")
        sleep_short(0.3)

    def assign_doc_types_and_upload(self, folder_path: str) -> bool:
        """Assign doc types to attached files and perform Upload/OK/Close.

        Runs after the 2nd Open (XML files) in the attachment popup.
        v6.1 TAB-WALK (owner directive, 2026-09-08 — "after mapili ng
        doc type TAB lang pindutin, wag na scroll, hanggang ESA"):

            1. Determine the doc-type sequence from the patient folder
               (deterministic, same as always).
            2. Walk the grid with the KEYBOARD TAB flow, ONE row at a
               time, starting at the auto-selected first row:
                 a. locate the focused row via its blue highlight band
                    (`find_highlight_band` — pixel scan, no OCR);
                 b. right-click the focused row → context menu "copy" →
                    clipboard holds the row's FULL ABSOLUTE PATH;
                 c. match the path to a patient-folder file (exact
                    basename + parent folder);
                 d. type that file's doc type into the Doc Type combo →
                    click arrow → press TAB  (TAB moves the grid focus to
                    the NEXT row — HBSys auto-scrolls the focused row
                    into view, so the grid handles the >10-row case by
                    itself; no mouse-wheel scroll is ever performed);
                 e. repeat until the last file (eSOA -> ESA).
            3. Upload (598,730) → Enter (OK) → Close (1408,734)
               (v5.2, unchanged).

        Row identity is the COPIED PATH (exact), never screen position,
        row number, or OCR text. The screen Y is only used to locate the
        currently focused blue row.

        Guardrails (loop-engineering Principle 4):
            - Never-guess: no focused row, copy failure, foreign path, or
              duplicate focus → ABORT before any Upload click, with the
              full copied path logged.
            - Copied path OUTSIDE the patient folder (foreign row —
              e.g. the VILLARUEL XMLs found in the 15:03 grids) → ABORT.
            - Duplicate focus (focus returned to an already-typed row)
              → ABORT (needs human review).
            - MAX_DOC_ROWS file cap; Upload only after EVERY expected
              file has been typed.

        Returns True on success; False on failure.
        """
        self.log_action("assigning doc types for attached files (v6.1 tab-walk)")

        # --- 0. Folder-driven expected doc types (deterministic) -----------
        folder = Path(folder_path)
        if not folder.is_dir():
            self.log_action(f"ABORT: patient folder not found: {folder_path}")
            return False
        folder_files = sorted(
            f.name for f in folder.iterdir()
            if f.suffix.lower() in (".pdf", ".xml")
        )
        if not folder_files:
            self.log_action("ABORT: no PDF/XML files in the patient folder")
            return False
        if len(folder_files) > MAX_DOC_ROWS:
            self.log_action(
                f"ABORT: {len(folder_files)} PDF/XML files exceed "
                f"MAX_DOC_ROWS ({MAX_DOC_ROWS}) — refusing to loop"
            )
            return False

        file_docs: dict[str, str] = {}
        for name in folder_files:
            prefix = "\\" if name.lower().endswith(".pdf") else "_"
            doc = detect_doc_type(prefix + name)
            if doc is None:
                self.log_action(
                    f"ABORT: cannot classify file (unknown stem): {name}"
                )
                return False
            file_docs[name] = doc

        expected_parent = folder.name

        if not self.live:
            for name in folder_files:
                self.log_action(f"  would set doc type {file_docs[name]!r} for {name}")
            self.log_action("would right-click the focused row, copy, type, TAB — until ESA")
            self.log_action("would click Upload -> Enter (OK) -> Close")
            return True

        # --- 1. TAB-WALK (clipboard identity + TAB focus walk) ---------------
        sleep_short(1.5)  # let the grid settle after the 2nd Open
        processed: set[str] = set()
        typed_rows = 0
        last_focus_y: Optional[int] = None

        # v7 audit: source sequence numbering is per claim series (the
        # destination files are numbered per claim series) and starts
        # at 01 for this patient's first copied source path.
        claim_series = self._current_claim_series()
        self.source_audit.start_series(claim_series)

        # One debug crop of the initial state (diagnosis only — no OCR).
        screenshot = pyautogui.screenshot()
        crop_box = self._popup_crop_box(screenshot)
        self._last_crop_box = crop_box
        self._save_doc_grid_debug(screenshot.crop(crop_box))

        while len(processed) < len(folder_files):
            row_y = self._focused_row_y()
            if row_y is None:
                self.log_action(
                    "ABORT: no focused (blue) row detected — grid state unknown"
                )
                return False
            if last_focus_y is not None and row_y != last_focus_y:
                self.log_action(
                    f"note: focus moved from y={last_focus_y} to y={row_y}"
                )

            copied = self._copy_row_path(row_y)
            if copied is None:
                self.log_action(
                    f"ABORT: copy failed on focused row at y={row_y}"
                )
                return False
            matched_file = match_copied_path_to_file(
                copied, folder_files, expected_parent
            )
            if matched_file is None:
                self.log_action(
                    f"ABORT: copied row path is not a patient file: {copied}"
                )
                return False
            if matched_file in processed:
                self.log_action(
                    f"ABORT: duplicate focus on {matched_file} "
                    f"(already typed) — needs human review"
                )
                return False

            typed_rows += 1
            self._type_row_doc(
                file_docs[matched_file], row_y, typed_rows, len(folder_files)
            )
            # v7 audit: record the source document IN PROCESSING ORDER
            # immediately after the successful copy + existing DOCTYPE
            # extraction (the sequence is captured here — never
            # re-derived from filenames or Explorer ordering). The
            # destination side is verified later from the eClaimsDoc
            # filenames; no OCR is involved anywhere.
            self._audit_source_document(
                source_path=copied,
                extracted_doctype=file_docs[matched_file],
            )
            processed.add(matched_file)
            last_focus_y = row_y
            # _type_row_doc ends with TAB: HBSys moves the focus to the
            # next row and auto-scrolls it into view.

        # --- 2. Upload, OK, Close (immediately after the last typed row) ----
        self.click(Point(*DOC_UPLOAD_BUTTON), "Upload")
        sleep_short(2.0)  # wait for the OK confirmation dialog
        self.press("enter")  # OK
        sleep_short(1.0)
        self.click(Point(*DOC_CLOSE_BUTTON), "Close")
        sleep_short(1.0)

        # v7 audit: ONE destination scan for this claim series now that
        # all of its documents have been processed and uploaded (single
        # folder listing — see audit_claim_series).
        self._audit_destination_for_series(claim_series)

        self.log_action(
            f"doc types assigned ({typed_rows} rows, tab-walk) and uploaded"
        )
        return True

    def _focused_row_y(self) -> Optional[int]:
        """Screen Y of the focused (blue-highlighted) grid row, or None.

        Screenshots the popup crop and locates the blue selection band
        via `find_highlight_band` (pixel scan, no OCR). Bands whose top
        is within 50px of the popup top are ignored (title bar / grid
        header); the band must be at least 8px tall. Returns the band
        center in screen coordinates. Retries up to 2 seconds — the band
        may take a moment to render after a TAB.
        """
        deadline = time.time() + 2.0
        while time.time() < deadline:
            screenshot = pyautogui.screenshot()
            crop_box = self._popup_crop_box(screenshot)
            self._last_crop_box = crop_box
            crop = screenshot.crop(crop_box)
            band = find_highlight_band(crop)
            if band is not None:
                top, bottom = band
                if top >= 50 and bottom - top >= 8:
                    return crop_box[1] + (top + bottom) // 2
            sleep_short(0.15)
        return None

    def _current_claim_series(self) -> str:
        """Claim series number of the CURRENT attachment popup (existing value).

        Reuses `extract_claim_series_from_title` on the popup the
        workflow already holds — no second extraction mechanism.
        Returns '' when the popup/title is unavailable.
        """
        if self.attachment_popup is None:
            return ""
        try:
            return extract_claim_series_from_title(
                self.attachment_popup.window_text()
            )
        except Exception:
            return ""

    def _audit_source_document(
        self, source_path: str, extracted_doctype: str
    ) -> None:
        """v7 audit: record one processed source document (in memory).

        Called immediately after the existing tab-walk successfully
        copied the source full path and matched it to a patient file
        (the EXISTING extracted doc type is consumed as-is — extraction
        itself is untouched). The collector assigns the per-claim-series
        source number in ACTUAL processing order. Non-blocking.
        """
        record = self.source_audit.add(
            source_full_path=source_path,
            extracted_doctype=extracted_doctype,
        )
        self.log_action(
            f"doctype audit: source {record.source_number} -> "
            f"{extracted_doctype} ({record.source_filename})"
        )

    def _audit_destination_for_series(self, claim_series: str) -> None:
        """v7 audit: compare this claim series' sources with eClaimsDoc.

        Runs ONCE per claim series AFTER all of its documents have been
        processed and uploaded. Scans
            C:\\Shared Folder\\eClaimsDoc\\<CLAIM_SERIES>\\
        (single listing) and compares each source record's number +
        extracted DOCTYPE with the destination filename's
        '...-RAW-<DOCTYPE>-<NUMBER>' tokens. Never blocks the workflow —
        missing folders/parse failures become REVIEW records.
        """
        series_records = [
            r
            for r in self.source_audit.records
            if r.claim_series_number == claim_series
        ]
        if not series_records:
            return
        audit = audit_claim_series(
            series_records, claim_series, dest_root=ECLAIMS_DOC_ROOT
        )
        self.doctype_audit_results.extend(audit.results)
        counts: dict[str, int] = {}
        for result in audit.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        summary = ", ".join(
            f"{status}={count}" for status, count in sorted(counts.items())
        )
        self.log_action(
            f"doctype audit: claim series {claim_series} vs eClaimsDoc "
            f"destination — {summary}"
        )

    def wait_for_file_dialog(self, timeout: float = 8.0) -> bool:
        """Wait for the Windows file dialog (2nd popup) to appear.

        Detection strategy:
            Before clicking Attach..., we recorded all existing window handles
            in self._pre_attach_handles.  After clicking, any NEW top-level
            window that wasn't in that set is the file dialog.

            The file dialog has class '#32770' and title 'Attachments'
            (same as the dialog inside the popup), so we MUST detect it
            by its NEW handle, not by title/class alone.

        Returns True if found.
        """
        if not self.live:
            self.log_action("would wait for file dialog")
            return True

        pre_handles = getattr(self, "_pre_attach_handles", set())
        self.log_action(
            f"waiting for file dialog "
            f"(excluding {len(pre_handles)} pre-existing handles)"
        )

        # Classes to ignore (system windows that are always present)
        ignore_classes = {
            "Shell_TrayWnd", "Progman", "WorkerW", "tooltips_class32",
            "ForegroundStaging", "Auto-Suggest Dropdown",
            "NotifyIconOverflowWindow", "TaskListThumbnailWnd",
            "SystemTray_Main", "_SearchEditBoxFakeWindow",
            "ComboLBox", "MSCTFIME UI", "IME",
            "TabletModeCoverWindow", "DummyDWMListenerWindow",
            "EdgeUiInputTopWndClass", "CicLoaderWndClass",
            "Dwm", "PushNotificationsPowerManagement",
        }

        deadline = time.time() + timeout
        while time.time() < deadline:
            desktop = Desktop(backend="win32")
            for window in desktop.windows():
                try:
                    handle = window.handle
                    cls = window.class_name()
                    title = window.window_text().strip()
                except Exception:
                    continue

                # Skip pre-existing windows
                if handle in pre_handles:
                    continue

                # Skip known system windows
                if cls in ignore_classes:
                    continue

                title_upper = title.upper()

                # NEW #32770 window = file dialog
                if cls == "#32770":
                    self.log_action(
                        f"File dialog found (new #32770): "
                        f"title={title!r}"
                    )
                    return True

                # Standard Open/Save dialog titles
                if any(kw in title_upper for kw in ("OPEN", "SAVE", "BROWSE")):
                    self.log_action(
                        f"File dialog found (title match): "
                        f"title={title!r}"
                    )
                    return True

                # Dialog-like class patterns
                if any(p in cls for p in ("Dialog", "FileDialog")):
                    self.log_action(
                        f"File dialog found (class pattern): "
                        f"cls={cls!r} title={title!r}"
                    )
                    return True

            time.sleep(0.4)

        # Debug: log all NEW windows found at timeout
        self.log_action("File dialog NOT found — listing NEW windows:")
        desktop = Desktop(backend="win32")
        for window in desktop.windows():
            try:
                handle = window.handle
                if handle in pre_handles:
                    continue
                cls = window.class_name()
                title = window.window_text().strip()
                if cls not in ignore_classes:
                    self.log_action(
                        f"  NEW window: class={cls!r} title={title!r}"
                    )
            except Exception:
                pass

        return False

    def type_folder_path_and_open(self, folder_path: str) -> bool:
        """Type the folder path in the file dialog (2nd popup) and click Open.

        1. Click the path/filename field
        2. Type the full path
        3. Press Enter to navigate to the folder
        4. Click inside file list area to focus it
        5. Ctrl+A (select all files in the folder)
        6. Click Open

        Returns True if successful.
        """
        self.log_action(f"typing folder path: {folder_path}")

        if not self.live:
            self.log_action("would type path and click Open")
            return True

        # Click the path/filename field
        self.log_action(f"clicking path field at ({P.FILE_DIALOG_PATH.x}, {P.FILE_DIALOG_PATH.y})")
        pyautogui.click(P.FILE_DIALOG_PATH.x, P.FILE_DIALOG_PATH.y)
        sleep_short(0.3)

        # Clear and type the full path
        pyautogui.hotkey("ctrl", "a")
        sleep_short(0.1)
        pyautogui.press("delete")
        sleep_short(0.1)
        pyautogui.write(folder_path, interval=0.01)
        sleep_short(0.5)

        # Press Enter to navigate to the folder
        pyautogui.press("enter")
        sleep_short(1.5)

        # Click inside file list area to focus it (otherwise Ctrl+A selects filename text)
        self.log_action(f"clicking file list area at ({P.FILE_LIST_FOCUS.x}, {P.FILE_LIST_FOCUS.y})")
        pyautogui.click(P.FILE_LIST_FOCUS.x, P.FILE_LIST_FOCUS.y)
        sleep_short(0.5)

        # Ctrl+A to select all files in the folder
        pyautogui.hotkey("ctrl", "a")
        sleep_short(0.5)

        # Click Open
        self.log_action(f"clicking Open at ({P.FILE_DIALOG_OPEN.x}, {P.FILE_DIALOG_OPEN.y})")
        pyautogui.click(P.FILE_DIALOG_OPEN.x, P.FILE_DIALOG_OPEN.y)
        sleep_short(2.0)  # wait for files to be processed

        return True

    def select_xml_type_and_open(self) -> bool:
        """Change 'Files of type' to XML in the file dialog, select all, and click Open.

        1. Click "Files of type" dropdown (2nd popup)
        2. Press arrow down to navigate to "XML files" option
        3. Press Enter to select it
        4. Click inside file list area to focus it
        5. Ctrl+A to select all XML files
        6. Click Open

        Returns True if successful.
        """
        self.log_action("selecting XML file type in file dialog")

        if not self.live:
            self.log_action("would select XML type and click Open")
            return True

        # Click "Files of type" dropdown
        self.log_action(f"clicking Files of type at ({P.FILE_DIALOG_TYPE.x}, {P.FILE_DIALOG_TYPE.y})")
        pyautogui.click(P.FILE_DIALOG_TYPE.x, P.FILE_DIALOG_TYPE.y)
        sleep_short(0.8)  # wait for dropdown to open

        # Press arrow down to navigate to "XML files" option, then Enter to select
        self.log_action("pressing arrow down + Enter to select XML files")
        pyautogui.press("down")
        sleep_short(0.3)
        pyautogui.press("enter")
        sleep_short(0.8)  # wait for filter to apply and dropdown to close

        # Click inside file list area to focus it (otherwise Ctrl+A selects filename text)
        self.log_action(f"clicking file list area at ({P.FILE_LIST_FOCUS.x}, {P.FILE_LIST_FOCUS.y})")
        pyautogui.click(P.FILE_LIST_FOCUS.x, P.FILE_LIST_FOCUS.y)
        sleep_short(0.5)

        # Ctrl+A to select all XML files
        pyautogui.hotkey("ctrl", "a")
        sleep_short(0.5)

        # Click Open
        self.log_action(f"clicking Open at ({P.FILE_DIALOG_OPEN.x}, {P.FILE_DIALOG_OPEN.y})")
        pyautogui.click(P.FILE_DIALOG_OPEN.x, P.FILE_DIALOG_OPEN.y)
        sleep_short(2.0)  # wait for files to be processed

        return True

    # -- search box management --------------------------------------------

    def clear_search_box(self) -> None:
        """Clear the search box for the next patient."""
        self.log_action("clearing search box")

        # Focus main window first
        if self.hbsys_window is not None:
            try:
                self.hbsys_window.set_focus()
                sleep_short(0.3)
            except Exception:
                pass

        self.log_action(f"clicking search box at ({P.SEARCH_BOX.x}, {P.SEARCH_BOX.y})")
        pyautogui.click(P.SEARCH_BOX.x, P.SEARCH_BOX.y)
        sleep_short(0.15)
        self.log_action("selecting all text (Ctrl+A)")
        pyautogui.hotkey("ctrl", "a")
        sleep_short(0.1)
        self.log_action("deleting selected text")
        pyautogui.press("delete")
        sleep_short(0.3)


# -- main loop -----------------------------------------------------------

def load_patients(ready_dir: Path) -> list[FolderDates]:
    """Load and parse all patient folders from the READY directory."""
    patients: list[FolderDates] = []
    if not ready_dir.exists():
        return patients

    for folder in sorted(ready_dir.iterdir(), key=lambda p: p.name.upper()):
        if not folder.is_dir():
            continue
        parsed = parse_folder_name(folder.name)
        if parsed is not None:
            patients.append(parsed)

    return patients


def build_folder_path(ready_dir: Path, patient: FolderDates) -> str:
    """Build the path to the patient's folder in READY directory.

    Uses the full folder name with hospital number and confinement period.
    Example: C:\\claims_bot\\claims_checker_results\\READY\\
             ECHANES, PAUL GEORGE DE GUZMAN - 000000000020743 - ADM20260817_DIS20260822
    """
    folder_name = (
        f"{patient.patient_name} - {patient.hospital_no} - "
        f"ADM{patient.admission.strftime('%Y%m%d')}_"
        f"DIS{patient.discharge.strftime('%Y%m%d')}"
    )
    return str(ready_dir / folder_name)


def run_attachments_loop(
    operator: AttachmentsOperator,
    state: AttachmentsState,
    patients: list[FolderDates],
    ready_dir: Path,
    watch_mtimes: dict[str, float] | None = None,
) -> int:
    """Main attachments loop.  Returns 0 on success, 1 on failure.

    Each iteration:
        1. Type patient name and search
        2. Detect highlighted row
        3. Click "attach..." on the row
        4. In attachment popup, attach all files from patient folder
        5. Attach XML files specifically
        6. Close popup and clear search
    """
    consecutive_failures = 0

    for idx, patient in enumerate(patients):
        if idx < state.processed:
            # Already processed in a previous run — skip
            continue

        # --- auto-reload check ---
        if watch_mtimes is not None:
            changed = check_for_code_changes(watch_mtimes)
            if changed:
                print(f"\n  Code changed: {', '.join(Path(c).name for c in changed[:5])}")
                state.save()
                restart_script()  # does not return

        patient_label = (
            f"{patient.patient_name} - {patient.hospital_no} - "
            f"ADM{patient.admission.strftime('%Y%m%d')}_"
            f"DIS{patient.discharge.strftime('%Y%m%d')}"
        )
        state.current_patient = patient_label

        print(f"\n{'='*60}")
        print(f"  Patient {idx + 1}/{len(patients)}: {patient.patient_name}")
        print(f"  Hospital No: {patient.hospital_no}")
        print(f"  ADM: {patient.admission_str}  DIS: {patient.discharge_str}")
        print(f"  Folder: {build_folder_path(ready_dir, patient)}")
        print(f"{'='*60}")

        if operator.confirm_each:
            input("Press Enter to process this patient, or Ctrl+C to stop...")

        folder_path = build_folder_path(ready_dir, patient)

        # Step 1: Type patient name and search
        operator.search_patient(patient.patient_name)

        # Step 2: Click "attach..." on the highlighted row
        if not operator.click_attach_on_highlighted_row():
            consecutive_failures += 1
            state.mark_failed(patient.patient_name, "highlighted row not found")
            state.save()
            print(f"  [FAIL] highlighted row not detected")
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                state.mark_failed_batch()
                state.save()
                return 1
            operator.clear_search_box()
            continue

        # Step 3: Wait for attachment popup
        if not operator.find_attachment_popup():
            consecutive_failures += 1
            state.mark_failed(patient.patient_name, "attachment popup not found")
            state.save()
            print(f"  [FAIL] attachment popup not found")
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                state.mark_failed_batch()
                state.save()
                return 1
            operator.clear_search_box()
            continue

        # Step 4: Click "Attach..." → file dialog → type path → Open
        operator.click_popup_attach_button()

        if not operator.wait_for_file_dialog():
            consecutive_failures += 1
            state.mark_failed(patient.patient_name, "file dialog not found (all files)")
            state.save()
            print(f"  [FAIL] file dialog not found for all files")
            operator.close_attachment_popup()
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                state.mark_failed_batch()
                state.save()
                return 1
            operator.clear_search_box()
            continue

        operator.type_folder_path_and_open(folder_path)
        print(f"  [OK] All files attached from: {folder_path}")

        # Step 5: Click "Attach..." again → file dialog → XML → Open
        sleep_short(1.0)
        operator.click_popup_attach_button()

        if not operator.wait_for_file_dialog():
            consecutive_failures += 1
            state.mark_failed(patient.patient_name, "file dialog not found (XML)")
            state.save()
            print(f"  [FAIL] file dialog not found for XML files")
            operator.close_attachment_popup()
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                state.mark_failed_batch()
                state.save()
                return 1
            operator.clear_search_box()
            continue

        operator.select_xml_type_and_open()
        print(f"  [OK] XML files attached")

        # Step 5.5: Assign doc types per row, then Upload → OK → Close
        if not operator.assign_doc_types_and_upload(folder_path):
            consecutive_failures += 1
            state.mark_failed(patient.patient_name, "doc type assignment failed")
            state.save()
            print(f"  [FAIL] doc type assignment failed")
            operator.close_attachment_popup()
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                state.mark_failed_batch()
                state.save()
                return 1
            operator.clear_search_box()
            continue

        # Step 6: Close attachment popup and verify it's gone
        operator.close_attachment_popup()
        sleep_short(2.0)  # extra wait for popup to fully close

        # Re-focus main window after popup closes
        if operator.hbsys_window is not None:
            try:
                operator.hbsys_window.set_focus()
                print(f"  main window re-focused after popup close")
            except Exception:
                pass
        sleep_short(1.0)

        # Success — reset consecutive failure counter
        consecutive_failures = 0
        state.mark_processed(patient.patient_name)
        state.save()
        print(f"  [OK] ATTACHED: {patient.patient_name}")

        # Clear search box for next patient
        operator.clear_search_box()

    # All patients processed
    print(f"\n{'='*60}")
    print("  All patients processed.")
    print(f"{'='*60}")

    # v7 audit: generate the final DOCTYPE source-to-destination Excel
    # report ONCE, after the entire batch has finished (in-memory results
    # only until now), then open it with the default Windows Excel
    # behavior.
    if operator.doctype_audit_results:
        report_path = finalize_doctype_audit(
            operator.doctype_audit_results, LOG_DIR / "doctype_audit"
        )
        if report_path is not None:
            print(f"  DOCTYPE audit report: {report_path}")

    state.mark_completed()
    state.save()
    return 0


# -- CLI entry point -----------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Claim Attachments Upload — automate document attachments for READY patients."
    )
    parser.add_argument(
        "--ready-dir",
        type=Path,
        default=None,
        help=f"READY folder path (default: {DEFAULT_READY_DIR})",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually click/type in HBSys (default: dry-run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of patients to process",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=0.35,
        help="Pause between actions in seconds (default: 0.35)",
    )
    parser.add_argument(
        "--confirm-each",
        action="store_true",
        help="Ask before processing each patient (recommended for first live run)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a previously failed/interrupted batch",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Auto-restart when code files change (no need to re-run manually)",
    )
    args = parser.parse_args()

    ready_dir = args.ready_dir or DEFAULT_READY_DIR
    patients = load_patients(ready_dir)

    if args.limit:
        patients = patients[:args.limit]

    if not patients:
        message = f"No patient folders found in:\n{ready_dir}"
        print(message)
        return 0

    # Load or create state
    if args.resume:
        state = AttachmentsState.load()
        if state.status not in ("in_progress", "failed"):
            print("No interrupted batch to resume. Starting fresh.")
            state = AttachmentsState()
    else:
        state = AttachmentsState()

    if state.status != "in_progress":
        state.mark_started(len(patients))

    operator = AttachmentsOperator(
        live=args.live,
        pause=args.pause,
        confirm_each=args.confirm_each,
    )

    print(f"Mode: {'LIVE' if args.live else 'DRY-RUN'}")
    print(f"Ready folder: {ready_dir}")
    print(f"Total patients: {state.total_patients}")
    print(f"Already processed: {state.processed}")
    if args.watch:
        print("Auto-reload: ON (script will restart when code changes)")
    if not args.live:
        print("Dry-run only. Add --live to actually click/type in HBSys.")

    try:
        # Focus HBSys window
        operator.focus_hbsys()

        # Record file mtimes for auto-reload
        source_mtimes = _get_source_mtimes() if args.watch else {}
        if args.watch:
            print(f"Watching {len(source_mtimes)} .py files for changes...")

        # Process each patient
        result = run_attachments_loop(
            operator, state, patients, ready_dir,
            watch_mtimes=source_mtimes if args.watch else None,
        )

    except KeyboardInterrupt:
        print("\n\nInterrupted by user. State saved.")
        state.save()
        return 1
    except Exception as exc:
        print(f"\nERROR: {exc}")
        state.mark_failed_batch()
        state.save()
        return 1

    # Print summary
    print(f"\n{'='*60}")
    print("  BATCH SUMMARY")
    print(f"{'='*60}")
    print(state.summary)
    print(f"{'='*60}")

    return result


if __name__ == "__main__":
    raise SystemExit(main())
