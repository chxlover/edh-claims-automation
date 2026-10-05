"""Temp: measure the Admission History grid geometry (borders, columns, rows)."""
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, "C:/claims_bot/date_fill_hbsys")

path = Path(sys.argv[1])
image = Image.open(path).convert("L")
width, height = image.size
print(f"image size: {width}x{height}")
pixels = image.load()

# Vertical border columns: many dark pixels across the grid area.
print("--- vertical lines (count of dark pixels, y in 40..height) ---")
for x in range(width):
    dark = sum(1 for y in range(40, height) if pixels[x, y] < 120)
    if dark > (height - 40) * 0.5:
        print(f"  x={x} dark={dark}")

print("--- horizontal lines (count of dark pixels, x in 15..width-10) ---")
for y in range(height):
    dark = sum(1 for x in range(15, width - 10) if pixels[x, y] < 120)
    if dark > (width - 25) * 0.6:
        print(f"  y={y} dark={dark}")
