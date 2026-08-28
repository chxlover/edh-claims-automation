"""Add Claims Upload — OCR reader for the highlighted search result row.

Captures the eClaims Upload Claims popup window, identifies the
highlighted (blue-band) row, and returns its OCR text so the
verifier can compare confinement dates against the folder name.

Follows the same OCR pattern as hbsys_read_admission_history.py:
pytesseract + PIL with multiple scale variants for reliability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps
from pywinauto import Desktop


LOG_DIR = Path("logs")


@dataclass(frozen=True)
class HighlightedRow:
    """A detected highlighted row from the eClaims search results."""

    text: str
    y: float           # vertical centre of the highlighted band (local coords)
    x_start: int       # left edge of the grid content area
    x_end: int         # right edge of the grid content area


# -- window discovery ----------------------------------------------------
def find_upload_claims_popup() -> Optional[object]:
    """Find the eClaims Upload Claims popup dialog.

    The popup is a separate dialog titled 'Claims (Tagged Final from Billing)'
    that appears OVER the main HBSys window.  It is NOT the main HBSys window
    (which has 'HOSPITAL' in its title and class 'FNWND3115').

    Returns the pywinauto window handle or None.
    """
    desktop = Desktop(backend="win32")

    # Strategy 1: Walk main HBSys window descendants (popup may be a child)
    hbsys_window = None
    for window in desktop.windows():
        try:
            title = window.window_text().strip()
        except Exception:
            continue
        if "HOSPITAL" in title.upper():
            hbsys_window = window
            break

    if hbsys_window is not None:
        try:
            for ctrl in hbsys_window.descendants():
                try:
                    ctrl_title = ctrl.window_text().strip().upper()
                    if "CLAIMS" in ctrl_title and ("TAGGED" in ctrl_title or "FINAL" in ctrl_title):
                        return ctrl
                except Exception:
                    continue
        except Exception:
            pass

    # Strategy 2: Search top-level windows (popup may be separate)
    for window in desktop.windows():
        try:
            title = window.window_text().strip()
        except Exception:
            continue
        title_upper = title.upper()

        # Skip main HBSys windows
        if "HOSPITAL" in title_upper:
            continue
        if "ELECTRONIC" in title_upper and "CLAIMS" in title_upper:
            continue

        # Match the popup dialog
        if "CLAIMS" in title_upper and ("TAGGED" in title_upper or "FINAL" in title_upper):
            return window

    return None


# -- screenshot ----------------------------------------------------------
def capture_popup(window: object, prefix: str = "upload_claims") -> Optional[Path]:
    """Capture the popup window as a PNG.  Returns the file path."""
    try:
        window.set_focus()  # type: ignore[union-attr]
    except Exception:
        pass

    try:
        image: Image.Image = window.capture_as_image()  # type: ignore[union-attr]
    except Exception as exc:
        print(f"[OCR] Could not capture popup: {exc}")
        return None

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.png"
    image.save(path)
    return path


# -- highlighted row detection -------------------------------------------
def _is_blue_band(pixel: tuple[int, ...]) -> bool:
    """Check if a pixel is part of the HBSys blue highlight band."""
    if len(pixel) < 3:
        return False
    red, green, blue = pixel[:3]
    return blue >= 140 and 60 <= green <= 200 and red <= 100 and (blue - red) >= 40


def detect_highlighted_row_y(image: Image.Image) -> Optional[float]:
    """Detect the vertical centre of the blue highlight band in the image.

    Returns local y-coordinate or None if no highlight is found.
    The blue highlight is on data columns (x=468+), NOT on Include column.
    """
    width, height = image.size

    # Scan rows for blue pixels — the highlight band is a horizontal stripe
    # Scan x=400+ to find the blue band on data columns
    blue_rows: list[int] = []
    for y in range(150, min(height, 500)):  # grid area
        blue_count = 0
        for x in range(400, min(width, 1500), 6):  # data columns area
            if _is_blue_band(image.getpixel((x, y))):
                blue_count += 1
        if blue_count >= 10:
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


def extract_row_text(
    image: Image.Image,
    row_y: float,
    margin: int = 12,
) -> str:
    """Crop the horizontal band around row_y and OCR it to extract text."""
    width, height = image.size
    y_top = max(0, int(row_y) - margin)
    y_bottom = min(height, int(row_y) + margin)

    crop = image.crop((0, y_top, width, y_bottom))
    crop = ImageOps.grayscale(crop)
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)

    try:
        import pytesseract
        text = pytesseract.image_to_string(crop, config="--psm 6")
        return text.strip()
    except ImportError:
        print("[OCR] pytesseract not installed")
        return ""
    except Exception as exc:
        print(f"[OCR] Tesseract failed: {exc}")
        return ""


# -- public API ----------------------------------------------------------
def read_highlighted_row(
    window: Optional[object] = None,
    popup_path: Optional[Path] = None,
) -> Optional[HighlightedRow]:
    """Read the highlighted search result row from the Upload Claims popup.

    If *window* is provided, captures a fresh screenshot.
    If *popup_path* is provided, reads from an existing screenshot.
    Returns None when no highlight is detected.
    """
    if popup_path is not None:
        image = Image.open(popup_path)
    elif window is not None:
        path = capture_popup(window)
        if path is None:
            return None
        image = Image.open(path)
    else:
        return None

    row_y = detect_highlighted_row_y(image)
    if row_y is None:
        return None

    text = extract_row_text(image, row_y)
    if not text:
        return None

    return HighlightedRow(
        text=text,
        y=row_y,
        x_start=0,
        x_end=image.size[0],
    )


def read_highlighted_row_text(window: object) -> str:
    """Convenience wrapper: return just the OCR text of the highlighted row."""
    row = read_highlighted_row(window=window)
    return row.text if row else ""


# -- standalone test ----------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        print(f"Reading highlighted row from: {path}")
        row = read_highlighted_row(popup_path=path)
        if row:
            print(f"  Row y: {row.y:.1f}")
            print(f"  Text: {row.text!r}")
        else:
            print("  No highlighted row detected.")
    else:
        print("Usage: python add_claims_ocr.py <screenshot.png>")
        print("Or call read_highlighted_row() with a live window handle.")
