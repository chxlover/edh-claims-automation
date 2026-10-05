"""Temp: how unanimous is the per-cell re-read, and does it beat the grid pass?"""
import sys
from pathlib import Path

import pytesseract
from PIL import Image, ImageEnhance, ImageOps

CONFIGS = (
    "--psm 7 -c tessedit_char_whitelist=0123456789/",
    "--psm 8 -c tessedit_char_whitelist=0123456789/",
    "--psm 13 -c tessedit_char_whitelist=0123456789/",
    "--psm 7 -c tessedit_char_whitelist=0123456789/-",
)

sys.path.insert(0, "C:/claims_bot/date_fill_hbsys")
import hbsys_read_admission_history as reader  # noqa: E402


def cell_reads(image, cx, cy):
    """Every distinct per-cell read of one date cell."""
    results = []
    for half_w, half_h in ((52, 12), (48, 12), (56, 12)):
        crop = ImageOps.grayscale(
            image.crop((cx - half_w, cy - half_h, cx + half_w, cy + half_h))
        )
        sharp = ImageEnhance.Sharpness(
            ImageEnhance.Contrast(crop).enhance(2.5)
        ).enhance(2.0)
        for img, tag in ((crop, "gray"), (sharp, "sharp")):
            for scale in (2, 3):
                scaled = img.resize(
                    (img.width * scale, img.height * scale), Image.LANCZOS
                )
                for config in CONFIGS:
                    text = " ".join(
                        pytesseract.image_to_string(scaled, config=config).split()
                    )
                    if reader.DATE_RE.search(text):
                        results.append(
                            (
                                reader.normalize_date_text(text),
                                f"hw{half_w} {tag}{scale}x "
                                f"{config.split('-c ')[1][:6]}",
                            )
                        )
    return results


for name in sys.argv[1:]:
    path = Path(name)
    image = Image.open(path).convert("L")
    print(f"\n########## {path.name}")
    items = reader.read_ocr_items(path)
    dates = [i for i in items if reader.DATE_RE.search(i.text)]
    for item in sorted(dates, key=lambda i: (i.y, i.x)):
        grid = reader.normalize_date_text(item.text)
        reads = cell_reads(image, int(item.x), int(item.y))
        tally = {}
        for value, _ in reads:
            tally[value] = tally.get(value, 0) + 1
        agree = len(tally) == 1
        top = max(tally, key=tally.get) if tally else ""
        verdict = "SAME" if top == grid else "DIFFERENT"
        print(
            f"  y={item.y:6.1f} x={item.x:6.1f} grid={grid!r} "
            f"cell={top!r} ({max(tally.values()) if tally else 0}"
            f"/{len(reads)}) unanimous={agree} -> {verdict}"
        )
        if verdict == "DIFFERENT":
            for value, tag in reads:
                if value != top:
                    print(f"        outlier {value!r} via {tag}")
