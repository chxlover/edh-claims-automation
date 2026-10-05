"""Temp: dump Admission History OCR variants for a captured PNG."""
import sys
from pathlib import Path

from PIL import Image  # noqa: E402
import pytesseract  # noqa: E402

sys.path.insert(0, "C:/claims_bot/date_fill_hbsys")

import hbsys_read_admission_history as reader  # noqa: E402

path = Path(sys.argv[1])
image = Image.open(path)
for index, (variant, scale, config) in enumerate(reader.build_ocr_variants(image)):
    data = pytesseract.image_to_data(
        variant, output_type=pytesseract.Output.DICT, config=config
    )
    items = reader.ocr_data_to_items(data, scale=scale)
    dates = [i.text for i in items if reader.DATE_RE.search(i.text)]
    types = [i.text for i in items if i.text in reader.TYPE_WORDS]
    score = reader.score_ocr_items_for_admission_history(items)
    print(f"--- variant {index} scale={scale} config={config!r} score={score}")
    print(f"    dates={dates}")
    print(f"    types={types}")
    for item in sorted(items, key=lambda i: (i.y, i.x)):
        if item.y > 90:
            print(
                f"    x={item.x:7.1f} y={item.y:6.1f} conf={item.confidence:.2f} "
                f"text={item.text!r}"
            )

print("=== chosen pass ===")
best = reader.read_ocr_items(path)
print(f"dates={[i.text for i in best if reader.DATE_RE.search(i.text)]}")
