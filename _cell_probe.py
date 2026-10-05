"""Temp: can a per-cell re-OCR resolve the '09/2212026' smear?"""
import sys
from pathlib import Path

import pytesseract
from PIL import Image, ImageEnhance, ImageOps

path = Path(sys.argv[1])
image = Image.open(path).convert("L")
print(f"image {image.size}")

# Row 1 discharge cell: OCR token centre was x=328, y=98 in window coords.
centres = [(76, 98, "row1 ADMISSION"), (328, 98, "row1 DISCHARGE"),
           (76, 125, "row2 ADMISSION"), (330, 125, "row2 DISCHARGE")]

for cx, cy, label in centres:
    print(f"\n=== {label}  centre=({cx},{cy})")
    for half_w, half_h in ((52, 12), (60, 14), (44, 11)):
        box = (cx - half_w, cy - half_h, cx + half_w, cy + half_h)
        if box[0] < 0 or box[1] < 0 or box[2] > image.width or box[3] > image.height:
            print(f"  box {box} out of bounds, skipped")
            continue
        crop = image.crop(box)
        gray = ImageOps.grayscale(crop)
        sharp = ImageEnhance.Sharpness(
            ImageEnhance.Contrast(gray).enhance(2.5)
        ).enhance(2.0)
        for tag, img, scale in (
            ("gray1x", gray, 1),
            ("sharp1x", sharp, 1),
            ("sharp4x", sharp, 4),
            ("sharp6x", sharp, 6),
        ):
            scaled = img.resize(
                (img.width * scale, img.height * scale), Image.LANCZOS
            )
            for config in (
                "--psm 7 -c tessedit_char_whitelist=0123456789/",
                "--psm 7 -c tessedit_char_whitelist=0123456789/-",
                "--psm 8 -c tessedit_char_whitelist=0123456789/",
                "--psm 13 -c tessedit_char_whitelist=0123456789/",
            ):
                text = pytesseract.image_to_string(scaled, config=config).strip()
                if text:
                    print(
                        f"  hw={half_w} {tag:8s} {config:52s} -> {text!r}"
                    )
