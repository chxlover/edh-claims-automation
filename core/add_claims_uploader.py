"""Add Claims Upload — Main loop controller.

Automates the eClaims Upload Claims workflow:

    1. Click eClaims
    2. Click Upload Att
    3. Click Add Claims
    4. For each patient in READY folder:
       a. Type patient name in search box
       b. Click Search
       c. OCR the highlighted row
       d. Verify confinement period matches folder name
       e. If match → click checkbox of highlighted row
       f. Clear search box for next patient
    5. Click Add
    6. Click OK
    7. Click X (close)

Design follows loop-engineering principles:
    - Goal-based loop with machine-checkable stop condition
    - State persisted to filesystem (resumable)
    - Verification before every irreversible action
    - Escalation on consecutive failures

Usage:
    # Dry-run (no clicks)
    python -m core.add_claims_uploader

    # Live mode
    python -m core.add_claims_uploader --live

    # Live with confirmation before each patient
    python -m core.add_claims_uploader --live --confirm-each

    # Resume a failed batch
    python -m core.add_claims_uploader --live --resume
"""

from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pyautogui
from PIL import Image
from pywinauto import Desktop

from core.add_claims_ocr import (
    HighlightedRow,
    capture_popup,
    find_upload_claims_popup,
    read_highlighted_row,
    read_highlighted_row_text,
)
from core.add_claims_state import UploadState
from core.add_claims_verifier import (
    FolderDates,
    VerifyResult,
    parse_folder_name,
    verify_confinement,
)


LOG_DIR = Path("logs")
DEFAULT_READY_DIR = Path(r"C:\claims_bot\claims_checker_results\READY")

# Maximum consecutive failures before the loop stops (Principle 4 — guardrail)
MAX_CONSECUTIVE_FAILURES = 3


# -- coordinate points (for 1920x1080 screen) ---------------------------

@dataclass(frozen=True)
class Point:
    x: int
    y: int


def _load_calibrated_points() -> "CalibratedPointsData":
    """Load calibrated coordinates from calibration file, or use defaults."""
    try:
        from core.add_claims_calibration import CalibratedPoints
        return CalibratedPoints.load()
    except Exception:
        # Fallback to default values
        return CalibratedPointsData()


@dataclass
class CalibratedPointsData:
    """Calibrated coordinates with defaults for 1920x1080 screen."""
    eclaims: tuple[int, int] = (86, 59)
    upload_att: tuple[int, int] = (136, 101)
    add_claims: tuple[int, int] = (83, 101)
    search_box: tuple[int, int] = (562, 271)
    search_button: tuple[int, int] = (1392, 269)
    grid_checkbox_x: int = 10
    add_button: tuple[int, int] = (972, 477)
    ok_button: tuple[int, int] = (554, 376)
    close_x: int = 1456
    close_y: int = 236


# Load calibrated coordinates at module import time
_calibrated = _load_calibrated_points()


class P:
    """Named screen coordinates for the eClaims Upload Claims popup.

    These are LOCAL coordinates relative to the popup window origin.
    When clicking, we add the window rectangle offset to convert to
    absolute screen coordinates.

    Coordinates are loaded from calibration file if available,
    otherwise defaults for 1920x1080 screen are used.
    """
    # Main HBSys toolbar (absolute coordinates)
    ECLAIMS = Point(*_calibrated.eclaims)
    UPLOAD_ATT = Point(*_calibrated.upload_att)
    ADD_CLAIMS = Point(*_calibrated.add_claims)

    # Upload Claims popup (LOCAL coordinates relative to popup window)
    SEARCH_BOX = Point(*_calibrated.search_box)
    SEARCH_BUTTON = Point(*_calibrated.search_button)
    GRID_CHECKBOX_X = _calibrated.grid_checkbox_x
    ADD_BUTTON = Point(*_calibrated.add_button)
    OK_BUTTON = Point(*_calibrated.ok_button)
    CLOSE_BUTTON_X = _calibrated.close_x
    CLOSE_BUTTON_Y = _calibrated.close_y


def sleep_short(seconds: float = 0.35) -> None:
    time.sleep(seconds)


# -- operator class ------------------------------------------------------

class AddClaimsOperator:
    """UI automation operator for the eClaims Upload Claims workflow.

    Supports dry-run mode (no clicks) for safe testing.
    """

    def __init__(self, live: bool, pause: float, confirm_each: bool):
        self.live = live
        self.pause = pause
        self.confirm_each = confirm_each
        self.hbsys_window: Optional[object] = None
        self.popup_window: Optional[object] = None

    def log_action(self, message: str) -> None:
        prefix = "LIVE" if self.live else "DRY"
        print(f"[{prefix}] {message}")

    def maybe_wait(self) -> None:
        time.sleep(self.pause)

    def click(self, point: Point, label: str, *, local: bool = False) -> None:
        """Click a point.  If local=True, converts from popup-local to screen coords."""
        if local and self.popup_window is not None:
            try:
                rect = self.popup_window.rectangle()
                screen_x = rect.left + point.x
                screen_y = rect.top + point.y
            except Exception:
                screen_x, screen_y = point.x, point.y
        else:
            screen_x, screen_y = point.x, point.y

        self.log_action(f"click {label} at ({screen_x}, {screen_y})")
        if self.live:
            pyautogui.click(screen_x, screen_y)
        self.maybe_wait()

    def double_click(self, point: Point, label: str, *, local: bool = False) -> None:
        if local and self.popup_window is not None:
            try:
                rect = self.popup_window.rectangle()
                screen_x = rect.left + point.x
                screen_y = rect.top + point.y
            except Exception:
                screen_x, screen_y = point.x, point.y
        else:
            screen_x, screen_y = point.x, point.y

        self.log_action(f"double-click {label} at ({screen_x}, {screen_y})")
        if self.live:
            pyautogui.doubleClick(screen_x, screen_y)
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
        """Find and focus the main HBSys window."""
        desktop = Desktop(backend="win32")
        for window in desktop.windows():
            try:
                title = window.window_text().strip()
            except Exception:
                continue
            if "HOSPITAL" in title.upper() or "HBSYS" in title.upper():
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
        raise RuntimeError("HBSys window not found. Open HBSys and try again.")

    def click_eclaims(self) -> None:
        """Step 1: Click eClaims on the HBSys toolbar."""
        self.log_action("Step 1: Click eClaims")
        self.click(P.ECLAIMS, "eClaims")
        sleep_short(1.5)

    def click_upload_att(self) -> None:
        """Step 2: Click Upload Att on the eClaims toolbar."""
        self.log_action("Step 2: Click Upload Att")
        self.click(P.UPLOAD_ATT, "Upload Att")
        sleep_short(1.5)

    def click_add_claims(self) -> None:
        """Step 3: Click Add Claims to open the popup."""
        self.log_action("Step 3: Click Add Claims")
        self.click(P.ADD_CLAIMS, "Add Claims")
        sleep_short(2.0)

    def find_popup(self, timeout: float = 5.0) -> bool:
        """Wait for the Upload Claims popup to appear."""
        if not self.live:
            self.log_action("would wait for Upload Claims popup")
            return True

        deadline = time.time() + timeout
        while time.time() < deadline:
            popup = find_upload_claims_popup()
            if popup is not None:
                self.popup_window = popup
                try:
                    title = popup.window_text().strip()
                    rect = popup.rectangle()
                    cls = popup.class_name()
                    self.log_action(
                        f"Popup found: title={title!r} class={cls!r} "
                        f"rect=({rect.left},{rect.top},{rect.right},{rect.bottom}) "
                        f"size={rect.right-rect.left}x{rect.bottom-rect.top}"
                    )
                    popup.set_focus()
                except Exception as exc:
                    self.log_action(f"Popup found but error reading: {exc}")
                return True
            time.sleep(0.3)

        self.log_action("Upload Claims popup NOT found within timeout")
        return False

    # -- patient search loop ----------------------------------------------

    def search_patient(self, patient_name: str) -> bool:
        """Type patient name in search box and click Search.

        Returns True if search was executed successfully.
        """
        self.log_action(f"searching for patient: {patient_name}")

        # Ensure popup has focus before typing
        if self.popup_window is not None:
            try:
                self.popup_window.set_focus()
                self.log_action("popup window focused")
            except Exception:
                pass

        # Clear search box and type patient name
        self.double_click(P.SEARCH_BOX, "Search Box", local=True)
        self.hotkey("ctrl", "a")
        self.write(patient_name)
        self.click(P.SEARCH_BUTTON, "Search Button", local=True)
        sleep_short(2.0)  # wait for search results to load

        return True

    def read_and_verify_highlighted_row(
        self,
        folder_dates: FolderDates,
    ) -> VerifyResult:
        """OCR the highlighted row and verify confinement dates.

        Returns the verification result.
        """
        if not self.live:
            self.log_action(
                f"would OCR highlighted row and verify "
                f"ADM {folder_dates.admission_str} DIS {folder_dates.discharge_str}"
            )
            # Return a synthetic match for dry-run
            return VerifyResult(
                match=True,
                folder_dates=folder_dates,
                ocr_dates=None,
                reason="dry-run: skipped OCR verification",
            )

        # Read the highlighted row via OCR
        row = read_highlighted_row(window=self.popup_window)
        if row is None:
            self.log_action("no highlighted row detected after search")
            return VerifyResult(
                match=False,
                folder_dates=folder_dates,
                ocr_dates=None,
                reason="no highlighted row detected",
            )

        self.log_action(f"highlighted row OCR text: {row.text!r}")

        # Verify confinement dates
        result = verify_confinement(folder_dates, row.text)
        self.log_action(f"verification result: {result.summary}")
        return result

    def click_checkbox_of_highlighted_row(self) -> bool:
        """Click the checkbox of the highlighted (selected) search result row.

        Strategy ("Include" text + blue band):
        1. Capture full-screen screenshot via pyautogui
        2. Find "Include" header text → X position of checkbox column
        3. Find blue highlighted row → Y position of row
        4. Click at (include_center_x, blue_band_center_y)
        """
        if not self.live:
            self.log_action("would click checkbox of highlighted row")
            return True

        # Step 1: Capture full screen
        self.log_action("capturing full screen for checkbox detection...")
        screenshot = pyautogui.screenshot()
        width, height = screenshot.size
        self.log_action(f"screenshot: {width}x{height}")

        # Step 2: Find "Include" header text → column X
        include_x = self._find_include_column_x(screenshot)
        if include_x is None:
            self.log_action("could not find Include column header")
            return False
        self.log_action(f"checkbox column center x={include_x}")

        # Step 3: Find blue highlighted row → row Y
        row_y = self._find_highlighted_row_y(screenshot)
        if row_y is None:
            self.log_action("could not detect highlighted row via blue band")
            return False
        self.log_action(f"highlighted row center y={row_y:.0f}")

        # Step 4: Click at (include_x, row_y)
        click_x = include_x
        click_y = int(round(row_y))
        self.log_action(f"clicking checkbox at screen ({click_x}, {click_y})")

        # DEBUG: Save annotated screenshot BEFORE clicking
        self._save_debug_screenshot(screenshot, click_x, click_y, row_y, "before")

        pyautogui.moveTo(click_x, click_y, duration=0.08)
        pyautogui.click(click_x, click_y, clicks=1)
        sleep_short(0.5)

        # DEBUG: Save annotated screenshot AFTER clicking
        after = pyautogui.screenshot()
        self._save_debug_screenshot(after, click_x, click_y, row_y, "after")

        self.log_action("checkbox clicked")
        return True

    def _save_debug_screenshot(
        self, screenshot: Image.Image, click_x: int, click_y: int,
        row_y: float, label: str
    ) -> None:
        """Save annotated screenshot showing where the click will happen."""
        from PIL import ImageDraw, ImageFont

        debug = screenshot.copy()
        draw = ImageDraw.Draw(debug)

        # Draw red crosshair at click position
        cross_size = 30
        draw.line(
            [(click_x - cross_size, click_y), (click_x + cross_size, click_y)],
            fill="red", width=3,
        )
        draw.line(
            [(click_x, click_y - cross_size), (click_x, click_y + cross_size)],
            fill="red", width=3,
        )

        # Draw red circle around click point
        r = 15
        draw.ellipse(
            [click_x - r, click_y - r, click_x + r, click_y + r],
            outline="red", width=3,
        )

        # Draw blue line at highlighted row Y
        draw.line(
            [(0, int(row_y)), (screenshot.width, int(row_y))],
            fill="blue", width=2,
        )

        # Add text label
        draw.text(
            (click_x + 20, click_y - 10),
            f"CLICK HERE ({click_x},{click_y})",
            fill="red",
        )
        draw.text(
            (10, int(row_y) - 20),
            f"Blue band row y={row_y:.0f}",
            fill="blue",
        )

        # Crop to popup area for easier viewing (full width, popup height)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"debug_checkbox_{label}_{datetime.now():%Y%m%d_%H%M%S}.png"
        debug.save(path)
        self.log_action(f"DEBUG screenshot saved: {path}")

    def _find_highlighted_row_y(self, image: Image.Image) -> float | None:
        """Find Y center of blue highlighted row (CF4 logic)."""
        width, height = image.size

        blue_rows: list[int] = []
        for y in range(150, min(height, 1030)):
            blue_count = 0
            for x in range(50, min(width, 1300), 8):
                red, green, blue = image.getpixel((x, y))[:3]
                if blue >= 145 and 60 <= green <= 190 and red <= 90:
                    blue_count += 1
            if blue_count >= 20:
                blue_rows.append(y)

        if not blue_rows:
            return None

        # Group into bands
        groups: list[list[int]] = []
        for y in blue_rows:
            if groups and y - groups[-1][-1] <= 1:
                groups[-1].append(y)
            else:
                groups.append([y])

        if not groups:
            return None

        # Widest band is the highlighted row
        row_group = max(groups, key=len)
        return (min(row_group) + max(row_group)) / 2.0

    def _find_include_column_x(self, image: Image.Image) -> int | None:
        """Find the X center of the 'Include' column header text in the POPUP.

        Crops the screenshot to the popup window area, then scans the
        header row for dark text pixels in the leftmost column.
        Returns the SCREEN X coordinate of the Include column center.
        """
        # Get popup window rect to crop the screenshot
        popup_left = 0
        popup_top = 0
        if self.popup_window is not None:
            try:
                rect = self.popup_window.rectangle()
                popup_left = rect.left
                popup_top = rect.top
            except Exception:
                pass

        # Crop screenshot to popup area
        popup_crop = image.crop((
            max(0, popup_left),
            max(0, popup_top),
            min(image.width, popup_left + 600),  # Include column is in first 600px
            min(image.height, popup_top + 200),   # Header area is in first 200px
        ))
        self.log_action(
            f"cropped to popup area: ({popup_left},{popup_top}) "
            f"size={popup_crop.width}x{popup_crop.height}"
        )

        # Scan header area for checkbox border pixels (x=30-80)
        # Checkbox is to the RIGHT of the 'Include' text, not under it
        checkbox_dark_xs: list[int] = []
        for y in range(0, min(popup_crop.height, 80)):
            for x in range(30, min(popup_crop.width, 80)):
                red, green, blue = popup_crop.getpixel((x, y))[:3]
                # Dark pixels = checkbox border (gray/dark)
                if red < 100 and green < 100 and blue < 100:
                    checkbox_dark_xs.append(x)

        if not checkbox_dark_xs:
            self.log_action("no checkbox border pixels found in popup")
            return None

        # Find the center of dark pixel cluster
        min_x = min(checkbox_dark_xs)
        max_x = max(checkbox_dark_xs)
        crop_center_x = (min_x + max_x) // 2

        # Convert back to screen coordinates
        screen_x = popup_left + crop_center_x

        self.log_action(
            f"checkbox in popup: crop_x={min_x}-{max_x}, "
            f"crop_center={crop_center_x}, screen_x={screen_x}"
        )
        return screen_x

    def clear_search_box(self) -> None:
        """Clear the search box for the next patient."""
        self.double_click(P.SEARCH_BOX, "Search Box (clear)", local=True)
        self.hotkey("ctrl", "a")
        self.press("delete")
        sleep_short(0.3)

    # -- finalize ---------------------------------------------------------

    def click_add(self) -> None:
        """Step 5: Click Add to submit all checked patients."""
        self.log_action("Step 5: Click Add")
        self.click(P.ADD_BUTTON, "Add Button", local=True)
        sleep_short(2.0)

    def click_ok(self) -> bool:
        """Step 6: Click OK on the confirmation dialog."""
        self.log_action("Step 6: Click OK")

        if not self.live:
            self.log_action("would click OK on confirmation dialog")
            return True

        # Wait for OK dialog
        deadline = time.time() + 10.0
        while time.time() < deadline:
            desktop = Desktop(backend="win32")
            for window in desktop.windows():
                try:
                    title = window.window_text().strip()
                    class_name = window.class_name()
                except Exception:
                    continue

                if class_name != "#32770":
                    continue

                # Look for OK button in dialog
                try:
                    ok_button = window.child_window(title="OK", class_name="Button")
                    ok_button.click()
                    self.log_action("OK button clicked")
                    sleep_short(0.5)
                    return True
                except Exception:
                    continue

            time.sleep(0.3)

        # Fallback: press Enter
        self.log_action("no OK dialog found; pressing Enter as fallback")
        self.press("enter")
        sleep_short(0.5)
        return True

    def click_close(self) -> None:
        """Step 7: Click X to close the popup."""
        self.log_action("Step 7: Click Close (X)")

        # Use calibrated absolute coordinates for close button
        close_x = P.CLOSE_BUTTON_X
        close_y = P.CLOSE_BUTTON_Y

        self.log_action(f"click close at screen ({close_x}, {close_y})")
        if self.live:
            pyautogui.click(close_x, close_y)
        sleep_short(0.5)


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


def run_upload_loop(
    operator: AddClaimsOperator,
    state: UploadState,
    patients: list[FolderDates],
) -> int:
    """Main upload loop.  Returns 0 on success, 1 on failure.

    Each iteration:
        1. Type patient name
        2. Search
        3. OCR highlighted row
        4. Verify confinement
        5. If match → click checkbox
        6. Update state
    """
    consecutive_failures = 0

    for idx, patient in enumerate(patients):
        if idx < state.processed:
            # Already processed in a previous run — skip
            continue

        patient_label = (
            f"{patient.patient_name} - {patient.hospital_no} - "
            f"ADM{patient.admission.strftime('%Y%m%d')}_DIS{patient.discharge.strftime('%Y%m%d')}"
        )
        state.current_patient = patient_label

        print(f"\n{'='*60}")
        print(f"  Patient {idx + 1}/{len(patients)}: {patient.patient_name}")
        print(f"  Hospital No: {patient.hospital_no}")
        print(f"  ADM: {patient.admission_str}  DIS: {patient.discharge_str}")
        print(f"{'='*60}")

        if operator.confirm_each:
            input("Press Enter to process this patient, or Ctrl+C to stop...")

        # Step 4a: Type patient name and search
        operator.search_patient(patient.patient_name)

        # Step 4b: OCR highlighted row and verify confinement (informational only)
        verify_result = operator.read_and_verify_highlighted_row(patient)
        if verify_result.match:
            print(f"  [INFO] verification: MATCH")
        else:
            print(f"  [INFO] verification: {verify_result.reason or 'mismatch'} (clicking anyway)")

        # Step 4c: Click checkbox of highlighted row (always, regardless of verification)
        if not operator.click_checkbox_of_highlighted_row():
            consecutive_failures += 1
            state.mark_failed(patient.patient_name, "checkbox click failed")
            state.save()
            print(f"  [FAIL] checkbox click failed")
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                state.mark_failed_batch()
                state.save()
                return 1
            operator.clear_search_box()
            continue

        # Success — reset consecutive failure counter
        consecutive_failures = 0
        state.mark_processed(patient.patient_name)
        state.save()
        print(f"  [OK] CHECKED: {patient.patient_name}")

        # Clear search box for next patient
        operator.clear_search_box()

    # All patients processed — finalize
    print(f"\n{'='*60}")
    print("  All patients processed. Finalizing...")
    print(f"{'='*60}")

    # Step 5: Click Add
    operator.click_add()

    # Step 6: Click OK
    operator.click_ok()

    # Step 7: Click Close
    operator.click_close()

    state.mark_completed()
    state.save()
    return 0


# -- CLI entry point -----------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add Claims Upload — automate eClaims upload for READY patients."
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
        "--calibrate",
        action="store_true",
        help="Run coordinate calibration before upload",
    )
    parser.add_argument(
        "--detect",
        action="store_true",
        help="Detect popup position and exit",
    )
    args = parser.parse_args()

    # Handle calibration/detect modes
    if args.calibrate or args.detect:
        from core.add_claims_calibration import main as calibration_main
        import sys
        sys.argv = [sys.argv[0]]
        if args.detect:
            sys.argv.append("--detect")
        return calibration_main()

    ready_dir = args.ready_dir or DEFAULT_READY_DIR
    patients = load_patients(ready_dir)

    if args.limit:
        patients = patients[: args.limit]

    if not patients:
        message = f"No patient folders found in:\n{ready_dir}"
        print(message)
        return 0

    # Load or create state
    if args.resume:
        state = UploadState.load()
        if state.status not in ("in_progress", "failed"):
            print("No interrupted batch to resume. Starting fresh.")
            state = UploadState()
    else:
        state = UploadState()

    if state.status != "in_progress":
        state.mark_started(len(patients))

    operator = AddClaimsOperator(
        live=args.live,
        pause=args.pause,
        confirm_each=args.confirm_each,
    )

    print(f"Mode: {'LIVE' if args.live else 'DRY-RUN'}")
    print(f"Ready folder: {ready_dir}")
    print(f"Total patients: {state.total_patients}")
    print(f"Already processed: {state.processed}")
    if not args.live:
        print("Dry-run only. Add --live to actually click/type in HBSys.")

    try:
        # Steps 1-3: Navigate to Add Claims popup
        operator.focus_hbsys()
        operator.click_eclaims()
        operator.click_upload_att()
        operator.click_add_claims()

        if not operator.find_popup():
            print("ERROR: Upload Claims popup did not appear.")
            return 1

        # Step 4: Process each patient
        result = run_upload_loop(operator, state, patients)

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
