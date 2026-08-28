"""Add Claims Upload — Coordinate Calibration Utility.

Helps calibrate the screen coordinates for the eClaims Upload Claims popup.
Since the popup position can vary based on window placement, this utility:

1. Captures the current screen
2. Detects the Upload Claims popup window
3. Allows interactive coordinate calibration
4. Saves calibrated coordinates for use by the uploader

Usage:
    # Interactive calibration mode
    python -m core.add_claims_calibration

    # Detect popup position only
    python -m core.add_claims_calibration --detect

    # Load saved calibration
    python -m core.add_claims_calibration --load
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pyautogui
from PIL import Image, ImageDraw, ImageFont
from pywinauto import Desktop


LOG_DIR = Path("logs")
CALIBRATION_FILE = LOG_DIR / "add_claims_calibration.json"


@dataclass
class CalibratedPoints:
    """Calibrated coordinates for the eClaims Upload Claims popup."""

    # Main HBSys toolbar (absolute screen coordinates)
    eclaims: tuple[int, int] = (86, 59)
    upload_att: tuple[int, int] = (136, 101)
    add_claims: tuple[int, int] = (83, 101)

    # Upload Claims popup (LOCAL coordinates relative to popup window origin)
    search_box: tuple[int, int] = (562, 271)
    search_button: tuple[int, int] = (1392, 269)
    grid_checkbox_x: int = 10
    add_button: tuple[int, int] = (972, 477)
    ok_button: tuple[int, int] = (554, 376)

    # Close button (absolute screen coordinates)
    close_x: int = 1456
    close_y: int = 236

    # Metadata
    screen_width: int = 1920
    screen_height: int = 1080
    calibrated_at: str = ""
    popup_title: str = ""

    def save(self, path: Path | None = None) -> Path:
        target = path or CALIBRATION_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["calibrated_at"] = datetime.now().isoformat()
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: Path | None = None) -> "CalibratedPoints":
        target = path or CALIBRATION_FILE
        if not target.exists():
            return cls()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            # Convert lists back to tuples
            for key in ("eclaims", "upload_att", "add_claims", "search_box",
                        "search_button", "add_button", "ok_button"):
                if key in data and isinstance(data[key], list):
                    data[key] = tuple(data[key])
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            return cls()


def find_upload_claims_popup() -> Optional[object]:
    """Find the eClaims Upload Claims popup window."""
    desktop = Desktop(backend="win32")
    for window in desktop.windows():
        try:
            title = window.window_text().strip()
        except Exception:
            continue
        title_upper = title.upper()
        if "UPLOAD" in title_upper and "CLAIM" in title_upper:
            return window
        if "ADD CLAIMS" in title_upper:
            return window
    return None


def capture_screen_with_annotation(
    popup_rect: Optional[tuple[int, int, int, int]] = None,
    points: Optional[CalibratedPoints] = None,
) -> Path:
    """Capture the screen and annotate it with calibration points.

    Returns the path to the annotated screenshot.
    """
    screenshot = pyautogui.screenshot()
    draw = ImageDraw.Draw(screenshot)

    # Draw popup rectangle if available
    if popup_rect:
        left, top, right, bottom = popup_rect
        draw.rectangle([left, top, right, bottom], outline="red", width=3)
        draw.text((left, top - 20), "Upload Claims Popup", fill="red")

    # Draw calibration points if provided
    if points:
        # HBSys toolbar points (absolute)
        for name, pos in [
            ("eClaims", points.eclaims),
            ("Upload Att", points.upload_att),
            ("Add Claims", points.add_claims),
        ]:
            x, y = pos
            draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill="green")
            draw.text((x + 10, y - 5), name, fill="green")

        # Popup-relative points (if popup_rect is known)
        if popup_rect:
            left, top, _, _ = popup_rect
            for name, pos in [
                ("Search Box", points.search_box),
                ("Search Button", points.search_button),
                ("Add Button", points.add_button),
                ("OK Button", points.ok_button),
            ]:
                x, y = pos
                screen_x = left + x
                screen_y = top + y
                draw.ellipse([screen_x - 5, screen_y - 5, screen_x + 5, screen_y + 5], fill="blue")
                draw.text((screen_x + 10, screen_y - 5), f"{name} (local)", fill="blue")

            # Checkbox X line
            checkbox_x = left + points.grid_checkbox_x
            draw.line([(checkbox_x, top + 120), (checkbox_x, top + 400)], fill="yellow", width=2)
            draw.text((checkbox_x + 5, top + 120), "Checkbox X", fill="yellow")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"calibration_{datetime.now():%Y%m%d_%H%M%S}.png"
    screenshot.save(path)
    return path


def interactive_calibration() -> CalibratedPoints:
    """Interactive calibration mode.

    Guides the user through calibrating each coordinate by:
    1. Detecting the popup window
    2. Taking a screenshot
    3. Asking the user to verify/adjust coordinates
    """
    print("=" * 60)
    print("  ADD CLAIMS UPLOAD - COORDINATE CALIBRATION")
    print("=" * 60)
    print()

    # Check screen size
    width, height = pyautogui.size()
    print(f"Screen size: {width}x{height}")

    if (width, height) != (1920, 1080):
        print(f"WARNING: Expected 1920x1080, got {width}x{height}")
        print("Coordinates may need adjustment for this resolution.")
    print()

    # Try to find the popup
    popup = find_upload_claims_popup()
    popup_rect = None
    popup_title = ""

    if popup:
        try:
            popup_rect_obj = popup.rectangle()
            popup_rect = (popup_rect_obj.left, popup_rect_obj.top,
                         popup_rect_obj.right, popup_rect_obj.bottom)
            popup_title = popup.window_text()
            print(f"Popup detected: {popup_title}")
            print(f"Popup position: ({popup_rect[0]}, {popup_rect[1]}) - "
                  f"({popup_rect[2]}, {popup_rect[3]})")
            print(f"Popup size: {popup_rect[2] - popup_rect[0]}x{popup_rect[3] - popup_rect[1]}")
        except Exception as exc:
            print(f"Could not read popup position: {exc}")
    else:
        print("No Upload Claims popup detected.")
        print("Please open the popup first (Steps 1-3), then run calibration again.")
    print()

    # Load existing calibration
    points = CalibratedPoints.load()
    print("Current calibration values:")
    print(f"  eClaims toolbar: {points.eclaims}")
    print(f"  Upload Att toolbar: {points.upload_att}")
    print(f"  Add Claims toolbar: {points.add_claims}")
    print(f"  Search Box (local): {points.search_box}")
    print(f"  Search Button (local): {points.search_button}")
    print(f"  Grid Checkbox X: {points.grid_checkbox_x}")
    print(f"  Add Button (local): {points.add_button}")
    print(f"  OK Button (local): {points.ok_button}")
    print()

    # Capture annotated screenshot
    path = capture_screen_with_annotation(popup_rect, points)
    print(f"Annotated screenshot saved to: {path}")
    print("Review the screenshot to verify coordinate positions.")
    print()

    # Ask user if they want to adjust
    response = input("Do you want to adjust coordinates? (y/n): ").strip().lower()
    if response != "y":
        print("Keeping current calibration.")
        return points

    # Interactive adjustment
    print()
    print("Enter new coordinates (press Enter to keep current value):")
    print()

    def ask_point(name: str, current: tuple[int, int]) -> tuple[int, int]:
        val = input(f"  {name} (x,y) [{current[0]},{current[1]}]: ").strip()
        if not val:
            return current
        try:
            parts = val.replace(" ", "").split(",")
            return (int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            print(f"    Invalid format, keeping {current}")
            return current

    def ask_int(name: str, current: int) -> int:
        val = input(f"  {name} [{current}]: ").strip()
        if not val:
            return current
        try:
            return int(val)
        except ValueError:
            print(f"    Invalid number, keeping {current}")
            return current

    print("--- HBSys Toolbar (absolute screen coordinates) ---")
    points.eclaims = ask_point("eClaims", points.eclaims)
    points.upload_att = ask_point("Upload Att", points.upload_att)
    points.add_claims = ask_point("Add Claims", points.add_claims)

    print()
    print("--- Upload Claims Popup (LOCAL coordinates relative to popup) ---")
    points.search_box = ask_point("Search Box", points.search_box)
    points.search_button = ask_point("Search Button", points.search_button)
    points.grid_checkbox_x = ask_int("Grid Checkbox X", points.grid_checkbox_x)
    points.add_button = ask_point("Add Button", points.add_button)
    points.ok_button = ask_point("OK Button", points.ok_button)

    print()
    print("--- Close Button Offsets (relative to popup edges) ---")
    points.close_x_offset = ask_int("Close X offset", points.close_x_offset)
    points.close_y_offset = ask_int("Close Y offset", points.close_y_offset)

    points.screen_width = width
    points.screen_height = height
    points.popup_title = popup_title

    # Save
    path = points.save()
    print()
    print(f"Calibration saved to: {path}")

    # Capture new annotated screenshot
    path = capture_screen_with_annotation(popup_rect, points)
    print(f"Updated screenshot saved to: {path}")

    return points


def detect_popup_position() -> None:
    """Detect and display the Upload Claims popup position."""
    print("Searching for Upload Claims popup...")

    popup = find_upload_claims_popup()
    if popup is None:
        print("No Upload Claims popup found.")
        print("Please open the popup first (Steps 1-3 in eClaims).")
        return

    try:
        rect = popup.rectangle()
        title = popup.window_text()
        print(f"\nPopup found: {title}")
        print(f"  Position: ({rect.left}, {rect.top}) - ({rect.right}, {rect.bottom})")
        print(f"  Size: {rect.right - rect.left}x{rect.bottom - rect.top}")
        print(f"  Center: ({(rect.left + rect.right) // 2}, {(rect.top + rect.bottom) // 2})")

        # Show where the calibrated points would be
        points = CalibratedPoints.load()
        print(f"\nCalibrated points on this popup:")
        for name, local_pos in [
            ("Search Box", points.search_box),
            ("Search Button", points.search_button),
            ("Add Button", points.add_button),
            ("OK Button", points.ok_button),
        ]:
            screen_x = rect.left + local_pos[0]
            screen_y = rect.top + local_pos[1]
            print(f"  {name}: local={local_pos} -> screen=({screen_x}, {screen_y})")

        checkbox_x = rect.left + points.grid_checkbox_x
        print(f"  Checkbox X line: screen x={checkbox_x}")

        # Capture screenshot
        path = capture_screen_with_annotation(
            (rect.left, rect.top, rect.right, rect.bottom),
            points,
        )
        print(f"\nAnnotated screenshot: {path}")

    except Exception as exc:
        print(f"Error reading popup: {exc}")


def load_calibration() -> CalibratedPoints:
    """Load and display the current calibration."""
    points = CalibratedPoints.load()
    print("Current calibration:")
    print(json.dumps(asdict(points), indent=2))
    return points


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Add Claims Upload - Coordinate Calibration"
    )
    parser.add_argument(
        "--detect",
        action="store_true",
        help="Detect popup position only",
    )
    parser.add_argument(
        "--load",
        action="store_true",
        help="Load and display saved calibration",
    )
    args = parser.parse_args()

    if args.detect:
        detect_popup_position()
    elif args.load:
        load_calibration()
    else:
        interactive_calibration()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
