"""GUI tests for the Patients Without XML panel, the Check Missing button
auto-disable, the 2-second auto-refresh, and the Live-mode defaults in the
Add Claims Upload / Claim Attachments tabs.

Instantiates the real widgets with the window withdrawn so nothing flashes.

Run from the project root:

    python -m unittest tests.test_gui_no_xml_panel
    python tests/test_gui_no_xml_panel.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import edh_claims_gui_XML_COPY_BUTTON as gui_module  # noqa: E402
from core import diagnostics  # noqa: E402
from gui.add_claims_upload_tab import AddClaimsUploadFrame  # noqa: E402
from gui.claim_attachments_tab import ClaimAttachmentsFrame  # noqa: E402

_DIAG_LOGS = None


def setUpModule():
    """Point core.diagnostics at a temp folder (2026-09-28).

    EDHClaimsGUI.__init__ installs the crash/lifecycle logging; without the
    redirect every GUI test run would append START/EXIT lines to the real
    logs/gui_lifecycle.log.
    """
    global _DIAG_LOGS
    diagnostics.reset_for_tests()
    _DIAG_LOGS = mkdtemp(prefix="edh_gui_test_diag_")
    os.environ["CLAIMS_DIAG_LOG_DIR"] = _DIAG_LOGS


def tearDownModule():
    os.environ.pop("CLAIMS_DIAG_LOG_DIR", None)
    diagnostics.reset_for_tests()  # closes the open gui_crash.log stream
    if _DIAG_LOGS:
        shutil.rmtree(_DIAG_LOGS, ignore_errors=True)


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
        """Legacy helper: with_xml=True writes ONLY CF4 (1 of the 3)."""
        kinds = ["CF4"] if with_xml else []
        return self.make_patient_with(name, kinds)

    def make_patient_with(self, name: str, kinds) -> Path:
        folder = self.output_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        for kind in kinds:
            (folder / f"{name}-260924150960_{kind}.xml").write_text(
                "{}", encoding="utf-8"
            )
        return folder

    def test_output_panel_shows_contents_badges(self):
        # Operator request 2026-10-15: the Output Folders rows carry
        # each folder's PDF/XML badge — no folder needs opening.
        folder = self.make_patient_with("ALPHA, JANE", ["CF4"])
        (folder / "CSF.pdf").write_text("x", encoding="utf-8")
        (folder / "DTR.pdf").write_text("x", encoding="utf-8")

        self.app.refresh_folder_lists()

        rows = list(self.app.output_folders_list.get(0, "end"))
        self.assertEqual(len(rows), 1)
        self.assertIn("ALPHA, JANE", rows[0])
        self.assertIn("[CSF DTR | XML 1/3]", rows[0])
        self.assertEqual(
            self.app.output_folders_card.cget("text"), "Output Folders (1)"
        )

    def test_other_panels_have_no_badges(self):
        # Only the Output panel carries badges (operator scope):
        # with_contents=False (the default) keeps plain folder names.
        folder = self.output_dir / "ALPHA, JANE"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "CSF.pdf").write_text("x", encoding="utf-8")

        self.app.populate_folder_list(
            self.app.ready_folders_card,
            self.app.ready_folders_list,
            "Ready Folders",
            str(self.output_dir),
        )

        rows = list(self.app.ready_folders_list.get(0, "end"))
        self.assertEqual(rows, ["ALPHA, JANE"])

    def test_panel_lists_folder_with_zero_of_three_xml(self):
        self.make_patient_with("ALPHA, JANE", [])

        self.app.populate_no_xml_list()

        self.assertEqual(
            list(self.app.no_xml_list.get(0, "end")), ["ALPHA, JANE  (0/3)"]
        )
        self.assertEqual(
            self.app.no_xml_card.cget("text"), "Patients Without XML (1)"
        )

    def test_panel_keeps_folder_with_one_or_two_xml_and_shows_the_count(self):
        # Operator rule 2026-10-12: only 3/3 removes a patient.
        self.make_patient_with("ONE, PATIENT", ["CF4"])
        self.make_patient_with("TWO, PATIENTS", ["CF4", "CF5"])

        self.app.populate_no_xml_list()

        self.assertEqual(
            sorted(self.app.no_xml_list.get(0, "end")),
            ["ONE, PATIENT  (1/3)", "TWO, PATIENTS  (2/3)"],
        )
        self.assertEqual(
            self.app.no_xml_card.cget("text"), "Patients Without XML (2)"
        )

    def test_folder_with_all_three_xml_is_removed_from_the_panel(self):
        self.make_patient_with("COMPLETE, PATIENT", ["CF4", "CF5", "ESOA"])

        self.app.populate_no_xml_list()

        self.assertEqual(list(self.app.no_xml_list.get(0, "end")), ["(none)"])
        self.assertEqual(
            self.app.no_xml_card.cget("text"), "Patients Without XML (0)"
        )

    def test_panel_shows_none_placeholder_when_all_have_xml(self):
        self.make_patient_with("BRAVO, JOHN", ["CF4", "CF5", "ESOA"])

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
        self.make_patient_with("ALPHA, JANE", [])

        self.app.refresh_folder_lists()

        self.assertEqual(
            list(self.app.no_xml_list.get(0, "end")), ["ALPHA, JANE  (0/3)"]
        )
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


class FolderColumnsLayoutTests(unittest.TestCase):
    """Operator request 2026-10-15: resizable folder columns + zebra rows.

    Uses the same app fixture (temp output dir): three patient folders
    exercise stripes, and the draggable sash between the Output/Ready
    column and the Incomplete/No-XML column is asserted directly.
    """

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
        for name in ("BRAVO, JOHN", "ALPHA, JANE", "CHARLIE, JUAN"):
            folder = self.output_dir / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "CSF.pdf").write_text("x", encoding="utf-8")
        self.app.reset_folder_split()

    def tearDown(self):
        self.app.settings["output_folder"] = self.original_output
        self.temporary.cleanup()

    def test_folder_columns_are_a_resizable_paned_window(self):
        import tkinter as tk

        sash = getattr(self.app, "folder_sash", None)
        self.assertIsNotNone(sash)
        self.assertIsInstance(sash, tk.Frame)
        # The sash sits in the divider column of the split.
        self.assertIn(sash, self.app.folder_split_body.grid_slaves(column=2))
        self.assertEqual(
            int(self.app.folder_split_body.columnconfigure(1)["minsize"]),
            gui_module.FOLDER_SPLIT_DEFAULT)

    def _sash_event(self, x_root):
        class _Event:
            pass

        event = _Event()
        event.x_root = x_root
        return event

    def test_dragging_sash_resizes_the_output_column(self):
        body = self.app.folder_split_body
        before = int(body.columnconfigure(1)["minsize"])
        self.app._on_folder_sash_press(self._sash_event(500))
        self.app._on_folder_sash_drag(self._sash_event(600))
        after = int(body.columnconfigure(1)["minsize"])
        self.assertEqual(after, before + 100)

    def test_dragging_sash_is_clamped_to_the_minimum(self):
        body = self.app.folder_split_body
        self.app._on_folder_sash_press(self._sash_event(500))
        self.app._on_folder_sash_drag(self._sash_event(-5000))
        self.assertEqual(
            int(body.columnconfigure(1)["minsize"]),
            gui_module.FOLDER_SPLIT_MIN)

    def test_reset_restores_the_default_split(self):
        body = self.app.folder_split_body
        body.columnconfigure(1, minsize=700)
        self.app.reset_folder_split()
        self.assertEqual(
            int(body.columnconfigure(1)["minsize"]),
            gui_module.FOLDER_SPLIT_DEFAULT)

    def test_output_rows_alternate_backgrounds(self):
        self.app.refresh_folder_lists()
        rows = list(self.app.output_folders_list.get(0, "end"))
        self.assertEqual(len(rows), 3)
        panel = self.app.colors["panel"]
        bg = self.app.colors["bg"]
        self.assertNotEqual(panel, bg)
        for index in range(3):
            got = str(self.app.output_folders_list.itemcget(index, "background"))
            self.assertEqual(got, panel if index % 2 == 0 else bg)

    def test_no_xml_rows_alternate_backgrounds(self):
        self.app.refresh_folder_lists()
        size = self.app.no_xml_list.size()
        self.assertGreater(size, 0)
        panel = self.app.colors["panel"]
        bg = self.app.colors["bg"]
        for index in range(size):
            got = str(self.app.no_xml_list.itemcget(index, "background"))
            self.assertEqual(got, panel if index % 2 == 0 else bg)

    def test_stripes_follow_theme_change(self):
        self.app.refresh_folder_lists()
        self.app.settings["theme"] = "Slate Dark"
        self.app.apply_theme()
        try:
            dark_bg = self.app.colors["bg"]
            self.assertEqual(dark_bg, "#111827")
            got = str(self.app.output_folders_list.itemcget(1, "background"))
            self.assertEqual(got, dark_bg)
        finally:
            self.app.settings["theme"] = "Light Blue"
            self.app.apply_theme()


if __name__ == "__main__":
    unittest.main()

