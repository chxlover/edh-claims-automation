"""GUI tests for PDF Preview zoom (operator request 2026-10-15).

Zoom In / Zoom Out on the contact-sheet viewer, with the level carrying
over to the next sheet (and across refreshes) so the operator does not
re-zoom every page.

Instantiates the real panel with a fake sheet service and temp PNGs,
window withdrawn so nothing flashes.

Run from the project root:
    python -m unittest tests.test_gui_pdf_preview_zoom
"""

from __future__ import annotations

import os
import shutil
import sys
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from tkinter import ttk

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PIL import Image  # noqa: E402

from core import diagnostics  # noqa: E402
from gui.pdf_preview_panel import PdfPreviewPanel, ZOOM_MAX, ZOOM_MIN  # noqa: E402

_DIAG_LOGS = None


def setUpModule():
    global _DIAG_LOGS
    diagnostics.reset_for_tests()
    _DIAG_LOGS = mkdtemp(prefix="edh_gui_test_diag_")
    os.environ["CLAIMS_DIAG_LOG_DIR"] = _DIAG_LOGS


def tearDownModule():
    os.environ.pop("CLAIMS_DIAG_LOG_DIR", None)
    diagnostics.reset_for_tests()
    if _DIAG_LOGS:
        shutil.rmtree(_DIAG_LOGS, ignore_errors=True)


class FakePreviewService:
    def __init__(self, sheets):
        self._sheets = list(sheets)

    def build_sheets(self, doc_type="CSF"):
        return list(self._sheets)

    def list_patients(self, doc_type="CSF"):
        return ["patient"]


class PdfPreviewZoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:  # noqa: BLE001 - cleanup must not mask failures.
            pass

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.sheets = []
        for index in range(3):
            path = Path(self.temporary.name) / f"sheet_{index}.png"
            Image.new("RGB", (400, 600), color=(index * 40, 0, 0)).save(path)
            self.sheets.append(path)
        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True)
        self.frame = frame
        self.panel = PdfPreviewPanel(
            frame, service=FakePreviewService(self.sheets))
        self.root.update_idletasks()
        self.panel._on_sheets_ready("CSF", list(self.sheets))

    def tearDown(self):
        self.frame.destroy()
        self.temporary.cleanup()

    def test_default_zoom_is_100_percent(self):
        self.assertEqual(self.panel._zoom, 1.0)
        self.assertEqual(self.panel.zoom_label_var.get(), "Zoom: 100%")

    def test_zoom_in_and_out_change_the_level(self):
        self.panel.zoom_in()
        self.assertGreater(self.panel._zoom, 1.0)
        first = self.panel.zoom_label_var.get()
        self.assertNotEqual(first, "Zoom: 100%")
        self.panel.zoom_out()
        self.panel.zoom_out()
        self.assertLess(self.panel._zoom, 1.0)

    def test_zoom_carries_over_to_the_next_sheet(self):
        # Operator request: zoom in, Next sheet keeps the zoom.
        self.panel.zoom_in()
        self.panel.zoom_in()
        level = self.panel._zoom
        label = self.panel.zoom_label_var.get()
        self.panel._next_sheet()
        self.assertEqual(self.panel.sheet_label_var.get(), "Sheet 2 of 3")
        self.assertEqual(self.panel._zoom, level)
        self.assertEqual(self.panel.zoom_label_var.get(), label)
        self.panel._previous_sheet()
        self.assertEqual(self.panel._zoom, level)

    def test_zoom_survives_refresh_with_new_sheets(self):
        self.panel.zoom_in()
        level = self.panel._zoom
        self.panel._on_sheets_ready("COE", list(self.sheets))
        self.assertEqual(self.panel._zoom, level)

    def test_zoom_clamps_at_limits(self):
        for _ in range(20):
            self.panel.zoom_in()
        self.assertLessEqual(self.panel._zoom, ZOOM_MAX)
        for _ in range(40):
            self.panel.zoom_out()
        self.assertGreaterEqual(self.panel._zoom, ZOOM_MIN)

    def test_scrollbars_and_wheel_scroll_exist(self):
        self.assertIsNotNone(self.panel.v_scroll)
        self.assertIsNotNone(self.panel.h_scroll)
        # Wheel scroll must not raise, even zoomed in.
        self.panel.zoom_in()
        self.panel.zoom_in()
        self.root.update_idletasks()
        event = type("E", (), {"delta": -120})()
        self.panel._on_mousewheel(event)


if __name__ == "__main__":
    unittest.main()
