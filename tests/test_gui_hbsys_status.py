"""GUI tests for the HBSys status indicator and Date Fill/XML Clicker blocking.

Instantiates the real EDHClaimsGUI (window withdrawn so it does not flash)
and drives `update_hbsys_status()` with a mocked `find_hbsys_window`, so the
outcome does not depend on whether HBSys is actually open on the machine.

Run from the project root:

    python -m unittest tests.test_gui_hbsys_status
    python tests/test_gui_hbsys_status.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import edh_claims_gui_XML_COPY_BUTTON as gui_module  # noqa: E402


def button_state(button) -> str:
    # ttk.Button.cget("state") returns a Tcl_Obj, not a plain str.
    return str(button.cget("state"))


class HbsysStatusBlockingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = gui_module.EDHClaimsGUI()
        cls.app.withdraw()
        cls.app.update_idletasks()
        cls._real_find = gui_module.find_hbsys_window

    @classmethod
    def tearDownClass(cls):
        gui_module.find_hbsys_window = cls._real_find
        try:
            cls.app.destroy()
        except Exception:  # noqa: BLE001 - cleanup must not mask failures.
            pass

    def setUp(self):
        self.app.date_fill_btn.configure(state="normal")
        self.app.xml_clicker_btn.configure(state="normal")

    def set_finder(self, result=None, error=None):
        def fake():
            if error is not None:
                raise error
            return result

        gui_module.find_hbsys_window = fake

    def test_open_enables_buttons_and_clears_warning(self):
        self.set_finder(result=object())
        self.app.update_hbsys_status()
        self.assertEqual(self.app.hbsys_status_var.get(), "● HBSys: OPEN")
        self.assertEqual(button_state(self.app.date_fill_btn), "normal")
        self.assertEqual(button_state(self.app.xml_clicker_btn), "normal")
        self.assertEqual(self.app.hbsys_warning_label.cget("text"), "")

    def test_closed_disables_buttons_and_shows_warning(self):
        self.set_finder(result=None)
        self.app.update_hbsys_status()
        self.assertEqual(self.app.hbsys_status_var.get(), "● HBSys: CLOSED")
        self.assertEqual(button_state(self.app.date_fill_btn), "disabled")
        self.assertEqual(button_state(self.app.xml_clicker_btn), "disabled")
        self.assertIn("CLOSED", self.app.hbsys_warning_label.cget("text"))

    def test_detection_error_disables_buttons(self):
        self.set_finder(error=RuntimeError("boom"))
        self.app.update_hbsys_status()
        self.assertEqual(self.app.hbsys_status_var.get(), "● HBSys: Error")
        self.assertEqual(button_state(self.app.date_fill_btn), "disabled")
        self.assertEqual(button_state(self.app.xml_clicker_btn), "disabled")

    def test_reopen_reenables_buttons_and_clears_warning(self):
        self.set_finder(result=None)
        self.app.update_hbsys_status()
        self.assertEqual(button_state(self.app.date_fill_btn), "disabled")

        self.set_finder(result=object())
        self.app.update_hbsys_status()
        self.assertEqual(button_state(self.app.date_fill_btn), "normal")
        self.assertEqual(button_state(self.app.xml_clicker_btn), "normal")
        self.assertEqual(self.app.hbsys_warning_label.cget("text"), "")

    def test_apply_hbsys_block_direct(self):
        self.app._apply_hbsys_block(blocked=True)
        self.assertEqual(button_state(self.app.date_fill_btn), "disabled")
        self.assertIn("CLOSED", self.app.hbsys_warning_label.cget("text"))

        self.app._apply_hbsys_block(blocked=False)
        self.assertEqual(button_state(self.app.date_fill_btn), "normal")
        self.assertEqual(button_state(self.app.xml_clicker_btn), "normal")
        self.assertEqual(self.app.hbsys_warning_label.cget("text"), "")

    def test_schedule_hbsys_status_check_registers_after_job(self):
        self.app.schedule_hbsys_status_check()
        self.assertIsNotNone(self.app.hbsys_status_job)
        self.app.after_cancel(self.app.hbsys_status_job)
        self.app.hbsys_status_job = None

    def test_check_hbsys_status_now_updates_and_reschedules(self):
        self.set_finder(result=object())
        self.app.check_hbsys_status_now()
        self.assertEqual(self.app.hbsys_status_var.get(), "● HBSys: OPEN")
        self.assertIsNotNone(self.app.hbsys_status_job)
        if self.app.hbsys_status_job is not None:
            self.app.after_cancel(self.app.hbsys_status_job)
            self.app.hbsys_status_job = None


if __name__ == "__main__":
    unittest.main()
