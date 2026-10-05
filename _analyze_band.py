"""One-off diagnostics: attach-button bounds vs bot click in debug screenshot.

Replicates the bot's exact blue-band scan, finds the red crosshair centre,
and measures the attach... button rectangle in the attach column.
"""

from collections import Counter

from PIL import Image

IMG = r"C:\claims_bot\logs\debug_attachments_attach_row_20260923_153916.png"
img = Image.open(IMG).convert("RGB")


def cls(px):
    r, g, b = px
    if r > 170 and g < 90 and b < 90:
        return "R"  # red crosshair / label
    if b >= 140 and 60 <= g <= 200 and r <= 100 and (b - r) >= 40:
        return "B"  # blue band (same rule as _is_blue_band)
    if r > 235 and g > 235 and b > 235:
        return "W"  # white
    if abs(r - g) < 20 and abs(g - b) < 20:
        return "G"  # gray (button face / borders)
    return "."


# 1) exact crosshair centre: mode of red pixels
reds = [
    (x, y)
    for y in range(150, 230)
    for x in range(1290, 1400)
    if cls(img.getpixel((x, y))) == "R"
]
xs = sorted(p[0] for p in reds)
ys = sorted(p[1] for p in reds)
cx = Counter(p[0] for p in reds).most_common(1)[0][0]
cy = Counter(p[1] for p in reds).most_common(1)[0][0]
print(
    f"crosshair centre (mode): x={cx} y={cy}  (red px={len(reds)}, "
    f"x range={xs[0]}..{xs[-1]}, y range={ys[0]}..{ys[-1]})"
)

# 2) replicate the bot's band scan exactly (x 50..1300 step 6, >=15 blue)
blue_rows = []
for y in range(150, 400):
    n = sum(
        1
        for x in range(50, min(img.width, 1300), 6)
        if cls(img.getpixel((x, y))) == "B"
    )
    if n >= 15:
        blue_rows.append(y)
if blue_rows:
    bands = []
    for y in blue_rows:
        if bands and y - bands[-1][-1] <= 2:
            bands[-1].append(y)
        else:
            bands.append([y])
    for i, b in enumerate(bands):
        print(
            f"band[{i}]: {min(b)}..{max(b)} height={max(b) - min(b) + 1} "
            f"center={(min(b) + max(b)) / 2}"
        )
    best = max(bands, key=len)
    top, bot = min(best), max(best)
    print(f"bot would click y={(top + bot) / 2}")
else:
    print("bot band: NONE DETECTED")

# 3) classify the attach cell (x 1300..1384) row by row
print(
    "\n  y : pattern x=1300..1384 step 2  "
    "(B=blue G=gray W=white R=red .=other)"
)
for y in range(166, 216):
    pat = "".join(cls(img.getpixel((x, y))) for x in range(1300, 1385, 2))
    print(f"{y:4d}: {pat}")

# 4) button rectangle: rows/cols dominated by gray+white inside the cell
btn_rows = []
for y in range(160, 220):
    row = [cls(img.getpixel((x, y))) for x in range(1305, 1382)]
    light = sum(1 for t in row if t in "WG")
    if light >= 12:
        btn_rows.append(y)
if btn_rows:
    mid = btn_rows[len(btn_rows) // 2]
    btn_cols = [
        x for x in range(1295, 1395) if cls(img.getpixel((x, mid))) in "WG"
    ]
    print(
        f"\nbutton rows: {min(btn_rows)}..{max(btn_rows)} "
        f"center={(min(btn_rows) + max(btn_rows)) / 2} "
        f"cols: {min(btn_cols)}..{max(btn_cols)} "
        f"center_x={(min(btn_cols) + max(btn_cols)) / 2}"
    )
else:
    print("\nbutton: no gray/white block found")

# 5) zoom crop for visual verification
crop = img.crop((1280, 155, 1410, 225)).resize((130 * 8, 70 * 8), Image.NEAREST)
crop.save(r"C:\claims_bot\logs\_zoom_attach_cell.png")
print("saved C:\\claims_bot\\logs\\_zoom_attach_cell.png")
