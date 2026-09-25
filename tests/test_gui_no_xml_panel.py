"""GUI tests for the Patients Without XML panel, the Check Missing button
auto-disable, the 2-second auto-refresh, and the Live-mode defaults in the
Add Claims Upload / Claim Attachments tabs.

Instantiates the real widgets with the window withdrawn so nothing flashes.

Run from the project root:

    python -m unittest tests.test_gui_no_xml_panel
    python tests/test_gui_no_xml_panel.py
"""

from __future__ import annotations

import sys
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import edh_claims_gui_XML_COPY_BUTTON as gui_module  # noqa: E402
from gui.add_claims_upload_tab import AddClaimsUploadFrame  # noqa: E402
from gui.claim_attachments_tab import ClaimAttachmentsFrame  # noqa: E402


def button_state(button) -> str:
    # ttk.Button.cget("state") returns a Tcl_Obj, not a plain str.
    return str(button.cget("state"))


class NoXmlPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = gui_module.EDHClaimsGUI()
        cls.app.withdraw()
        cls.app.update_idletasks()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.destroy()
        except Exception:  # noqa: BLE001 - cleanup must not mask failures.
            pass

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.output_dir = Path(self.temporary.name)
        self.original_output = self.app.settings.get("output_folder", "")
        self.app.settings["output_folder"] = str(self.output_dir)
        self.app.check_missing_button.configure(state="normal")

    def tearDown(self):
        self.app.settings["output_folder"] = self.original_output
        self.app.check_missing_button.configure(state="normal")
        self.temporary.cleanup()

    def make_patient(self, name: str, with_xml: bool) -> Path:
        folder = self.output_dir / name
        folder.mkdir(parents=True)
        if with_xml:
            (folder / f"{name}-260924150960_CF4.xml").write_text(
                "{}", encoding="utf-8"
            )
        return folder

    def test_panel_lists_only_folders_without_xml(self):
        self.make_patient("ALPHA, JANE", with_xml=False)
        self.make_patient("BRAVO, JOHN", with_xml=True)

        self.app.populate_no_xml_list()

        self.assertEqual(list(self.app.no_xml_list.get(0, "end")), ["ALPHA, JANE"])
        self.assertEqual(
            self.app.no_xml_card.cget("text"), "Patients Without XML (1)"
        )

    def test_panel_shows_none_placeholder_when_all_have_xml(self):
        self.make_patient("BRAVO, JOHN", with_xml=True)

        self.app.populate_no_xml_list()

        self.assertEqual(list(self.app.no_xml_list.get(0, "end")), ["(none)"])
        self.assertEqual(
            self.app.no_xml_card.cget("text"), "Patients Without XML (0)"
        )

    def test_button_enabled_when_xml_present(self):
        self.make_patient("BRAVO, JOHN", with_xml=True)

        self.app.refresh_check_missing_button_state()

        self.assertEqual(button_state(self.app.check_missing_button), "normal")

    def test_button_disabled_when_no_xml_anywhere(self):
        self.make_patient("ALPHA, JANE", with_xml=False)

        self.app.refresh_check_missing_button_state()

        self.assertEqual(button_state(self.app.check_missing_button), "disabled")

    def test_button_disabled_when_output_dir_missing(self):
        # A missing output folder means there is no XML to check.
        self.app.settings["output_folder"] = str(self.output_dir / "MISSING")

        self.app.refresh_check_missing_button_state()

        self.assertEqual(button_state(self.app.check_missing_button), "disabled")

    def test_button_fail_open_on_scan_error(self):
        real_helper = gui_module.output_dir_has_xml

        def boom(_path):
            raise RuntimeError("scan exploded")

        gui_module.output_dir_has_xml = boom
        try:
            self.app.refresh_check_missing_button_state()
        finally:
            gui_module.output_dir_has_xml = real_helper

        self.assertEqual(button_state(self.app.check_missing_button), "normal")

    def test_refresh_folder_lists_drives_panel_and_button(self):
        self.make_patient("ALPHA, JANE", with_xml=False)

        self.app.refresh_folder_lists()

        self.assertEqual(list(self.app.no_xml_list.get(0, "end")), ["ALPHA, JANE"])
        self.assertEqual(button_state(self.app.check_missing_button), "disabled")

    def test_dashboard_auto_refresh_interval_is_two_seconds(self):
        recorded = []
        real_after = self.app.after

        def spy(ms, *args, **kwargs):
            recorded.append(ms)
            return real_after(ms, *args, **kwargs)

        self.app.after = spy
        try:
            self.app.schedule_dashboard_auto_refresh()
        finally:
            self.app.after = real_after
        self.assertIn(2000, recorded)
        if self.app.dashboard_auto_refresh_job is not None:
            self.app.after_cancel(self.app.dashboard_auto_refresh_job)
            self.app.dashboard_auto_refresh_job = None


class TabAutoRefreshTests(unittest.TestCase):
    """The Add Claims Upload and Claim Attachments tabs share the same
    structure; run the same assertions against both frame classes."""

    def build_frame(self, frame_class):
        root = tk.Tk()
        root.withdraw()
        frame = frame_class(
            root, settings_getter=lambda: {}, log_callback=lambda m: None
        )
        root.update_idletasks()
        return root, frame

    def test_live_is_default_mode(self):
        for frame_class in (AddClaimsUploadFrame, ClaimAttachmentsFrame):
            with self.subTest(frame=frame_class.__name__):
                root, frame = self.build_frame(frame_class)
                try:
                    self.assertEqual(frame.mode_var.get(), "live")
                finally:
                    root.destroy()

    def test_auto_refresh_job_scheduled(self):
        for frame_class in (AddClaimsUploadFrame, ClaimAttachmentsFrame):
            with self.subTest(frame=frame_class.__name__):
                root, frame = self.build_frame(frame_class)
                try:
                    self.assertIsNotNone(frame._auto_refresh_job)
                finally:
                    root.destroy()

    def test_quiet_refresh_does_not_log(self):
        root, frame = self.build_frame(AddClaimsUploadFrame)
        try:
            frame.log_text.delete("1.0", "end")
            frame.refresh_patient_list(verbose=False)
            self.assertEqual(frame.log_text.get("1.0", "end-1c"), "")
        finally:
            root.destroy()

    def test_tick_skips_refresh_while_upload_running(self):
        root, frame = self.build_frame(AddClaimsUploadFrame)
        try:
            calls = []
            frame.refresh_patient_list = lambda verbose=True: calls.append(verbose)

            class _FakeThread:
                def __init__(self, alive):
                    self._alive = alive

                def is_alive(self):
                    return self._alive

            frame.upload_thread = _FakeThread(alive=True)
            frame._auto_refresh_tick()
            self.assertEqual(calls, [])

            frame.upload_thread = _FakeThread(alive=False)
            frame._auto_refresh_tick()
            self.assertEqual(calls, [False])
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()

