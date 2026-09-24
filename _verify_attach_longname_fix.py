"""Offline verification for the long-name attach-click fix.

Runs detect_highlighted_row_band() + the click-y decision logic from
click_attach_on_highlighted_row() against:
  1. The REAL user screenshot (long name, double-height row).
  2. A synthetic single-line row (short name behaviour must be unchanged).
  3. A synthetic double-line row (long name -> first-line click).
"""
from __future__ import annotations

from PIL import Image

from core.claim_attachments_uploader import (
    FIRST_LINE_TEXT_OFFSET,
    MULTILINE_ROW_MIN_HEIGHT,
    detect_highlighted_row_band,
    detect_highlighted_row_y,
)

SCREENSHOT = r"C:\claims_bot\screenshots\claim_attachments_mahaba_ang_pangalan.png"
BLUE = (0, 120, 215)  # HBSys highlight blue (passes _is_blue_band)


def decide_click_y(band: tuple[int, int]) -> int:
    """Mirror of the decision logic in click_attach_on_highlighted_row()."""
    top, bottom = band
    height = bottom - top + 1
    if height > MULTILINE_ROW_MIN_HEIGHT:
        return top + FIRST_LINE_TEXT_OFFSET
    return int(round((top + bottom) / 2.0))


def synthetic_image(band_top: int, band_bottom: int) -> Image.Image:
    img = Image.new("RGB", (1920, 1080), (255, 255, 255))
    for y in range(band_top, band_bottom + 1):
        for x in range(0, 1400):
            img.putpixel((x, y), BLUE)
    return img


def main() -> int:
    failures = 0

    # 1. Real user screenshot: long name wrapped to 2 lines.
    real = Image.open(SCREENSHOT).convert("RGB")
    band = detect_highlighted_row_band(real)
    assert band is not None, "no blue band detected in real screenshot"
    top, bottom = band
    height = bottom - top + 1
    click_y = decide_click_y(band)
    print(f"[REAL] band=({top},{bottom}) height={height}px -> click_y={click_y}")
    # The 'attach...' text in this screenshot sits at y~180-190.
    if not (height > MULTILINE_ROW_MIN_HEIGHT and 178 <= click_y <= 192):
        print("[REAL] FAIL: expected tall band and first-line click ~185")
        failures += 1
    else:
        print("[REAL] PASS: tall band detected, click lands on first text line")

    # 2. Synthetic single-line row (short name): behaviour unchanged.
    img1 = synthetic_image(185, 201)  # 17px tall
    band1 = detect_highlighted_row_band(img1)
    assert band1 is not None
    click1 = decide_click_y(band1)
    center1 = detect_highlighted_row_y(img1)
    print(f"[SHORT] band={band1} -> click_y={click1} (center={center1})")
    if click1 != int(round(center1)):
        print("[SHORT] FAIL: short-name click must stay at band centre")
        failures += 1
    else:
        print("[SHORT] PASS: single-line row still clicks band centre")

    # 3. Synthetic double-line row (long name): first-line click.
    img2 = synthetic_image(176, 207)  # 32px tall
    band2 = detect_highlighted_row_band(img2)
    assert band2 is not None
    click2 = decide_click_y(band2)
    print(f"[LONG] band={band2} -> click_y={click2} (old centre would be 191)")
    if click2 != 176 + FIRST_LINE_TEXT_OFFSET:
        print("[LONG] FAIL: expected first-line click at top+9")
        failures += 1
    else:
        print("[LONG] PASS: tall row clicks first text line")

    # 4. No band -> None (unchanged).
    blank = Image.new("RGB", (1920, 1080), (255, 255, 255))
    assert detect_highlighted_row_band(blank) is None
    assert detect_highlighted_row_y(blank) is None
    print("[NONE] PASS: no band -> None")

    print()
    if failures:
        print(f"RESULT: FAILED ({failures} case/s)")
        return 1
    print("RESULT: PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
