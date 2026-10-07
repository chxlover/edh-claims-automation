import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATE_FILL_DIR = ROOT / "date_fill_hbsys"
if str(DATE_FILL_DIR) not in sys.path:
    sys.path.insert(0, str(DATE_FILL_DIR))

import date_fill_headless as d  # noqa: E402  (pure std-lib; after sys.path tweak)


class HeadlessFlagTests(unittest.TestCase):
    KEY = "CLAIMS_HEADLESS"

    def setUp(self) -> None:
        os.environ.pop(self.KEY, None)

    def tearDown(self) -> None:
        os.environ.pop(self.KEY, None)

    def test_unset_means_interactive(self) -> None:
        self.assertFalse(d.headless())
        self.assertTrue(d.ui_enabled())

    def test_headless_one_disables_ui(self) -> None:
        os.environ[self.KEY] = "1"
        self.assertTrue(d.headless())
        self.assertFalse(d.ui_enabled())

    def test_headless_zero_keeps_ui(self) -> None:
        os.environ[self.KEY] = "0"
        self.assertFalse(d.headless())
        self.assertTrue(d.ui_enabled())

    def test_empty_keeps_ui(self) -> None:
        os.environ[self.KEY] = ""
        self.assertFalse(d.headless())
        self.assertTrue(d.ui_enabled())


if __name__ == "__main__":
    unittest.main()
