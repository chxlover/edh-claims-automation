"""Regression tests for the OCR result cache.

The same screenshot is often read several times in one Date Fill step
(row locate, proof, re-probe). Caching by (path, mtime, size) avoids running
9 tesseract variants repeatedly, while a rewritten file must never return a
stale result. Headless: uses a tiny synthetic image, no HBSys / tesseract.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DATE_FILL_DIR = ROOT / "date_fill_hbsys"
if str(DATE_FILL_DIR) not in sys.path:
    sys.path.insert(0, str(DATE_FILL_DIR))

try:
    import hbsys_read_admission_history_testing as reader
except Exception as exc:  # pragma: no cover - optional deps
    raise unittest.SkipTest(f"reader not importable: {exc}")


class OcrCacheTests(unittest.TestCase):
    def setUp(self):
        reader.clear_ocr_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "capture.png"
        from PIL import Image

        Image.new("RGB", (40, 20), "white").save(self.path)

    def test_same_file_is_read_once(self):
        fake = [[reader.OcrItem("X", 1.0, 0.0, 0.0)]]
        with mock.patch.object(
            reader, "build_ocr_variants", return_value=[("img", 1.0, "--psm 6")]
        ), mock.patch(
            "pytesseract.image_to_data", return_value={"text": []}
        ) as tess, mock.patch.object(
            reader, "ocr_data_to_items", return_value=fake[0]
        ):
            first = reader.read_ocr_item_variants_tesseract(self.path)
            second = reader.read_ocr_item_variants_tesseract(self.path)
        self.assertEqual(first, second)
        self.assertEqual(tess.call_count, 1, "second read must hit the cache")

    def test_rewritten_file_is_not_cached(self):
        fake = [[reader.OcrItem("X", 1.0, 0.0, 0.0)]]
        with mock.patch.object(
            reader, "build_ocr_variants", return_value=[("img", 1.0, "--psm 6")]
        ), mock.patch(
            "pytesseract.image_to_data", return_value={"text": []}
        ) as tess, mock.patch.object(
            reader, "ocr_data_to_items", return_value=fake[0]
        ):
            reader.read_ocr_item_variants_tesseract(self.path)
            # Rewrite the file with different content -> new mtime/size -> miss.
            from PIL import Image

            Image.new("RGB", (80, 40), "black").save(self.path)
            reader.read_ocr_item_variants_tesseract(self.path)
        self.assertEqual(tess.call_count, 2, "rewritten file must re-run OCR")

    def test_clear_cache_forces_reread(self):
        fake = [[reader.OcrItem("X", 1.0, 0.0, 0.0)]]
        with mock.patch.object(
            reader, "build_ocr_variants", return_value=[("img", 1.0, "--psm 6")]
        ), mock.patch(
            "pytesseract.image_to_data", return_value={"text": []}
        ) as tess, mock.patch.object(
            reader, "ocr_data_to_items", return_value=fake[0]
        ):
            reader.read_ocr_item_variants_tesseract(self.path)
            reader.clear_ocr_cache()
            reader.read_ocr_item_variants_tesseract(self.path)
        self.assertEqual(tess.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
