"""GUI tests for the remaining-patient count on the run-status overlay.

Instantiates the real EDHClaimsGUI (window withdrawn so it does not flash) and
drives `refresh_run_status_overlay()` with the workflow state faked, so the
outcome never depends on a real HBSys or a real workflow run.

The chain exercised here is the real 2026-10-09 one: Date Fill prints
"Claims: 3" + one "[LIVE] processing" line per patient, Final Bill prints
"N patient folder(s) ... N pending (NO FINAL BILL)" + one "(i/N)" line.

Run from the project root:

    python -m unittest tests.test_gui_workflow_progress_overlay
    python tests/test_gui_workflow_progress_overlay.py
"""

from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import mkdtemp

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import edh_claims_gui_XML_COPY_BUTTON as gui_module  # noqa: E402
from core import diagnostics  # noqa: E402
from core.workflow_progress import WorkflowProgress  # noqa: E402

_DIAG_LOGS = None


def setUpModule():
    """Keep GUI lifecycle logging out of the real logs/ folder."""
    global _DIAG_LOGS
    diagnostics.reset_for_tests()
    _DIAG_LOGS = mkdtemp(prefix="edh_gui_progress_diag_")
    os.environ["CLAIMS_DIAG_LOG_DIR"] = _DIAG_LOGS


def tearDownModule():
    os.environ.pop("CLAIMS_DIAG_LOG_DIR", None)
    diagnostics.reset_for_tests()
    if _DIAG_LOGS:
        shutil.rmtree(_DIAG_LOGS, ignore_errors=True)


class OverlayRemainingPatientsTests(unittest.TestCase):
    """The overlay text follows the active node's remaining count."""

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
        self.overlay = self.app.run_status_overlay
        self.tracker = WorkflowProgress()
        self.app.workflow_progress = self.tracker
        self.app.running_process = None
        # Fake "workflow running" without touching the real engine, but keep
        # the real frame so the log-callback wiring stays inspectable.
        self._real_workflow_frame = self.app.workflow_frame
        self.app.workflow_frame = type(
            "_Frame",
            (),
            {
                "engine": type("_E", (), {"running": True})(),
                "log_callback": self._real_workflow_frame.log_callback,
            },
        )()
        # The overlay is a per-app singleton: start every test from a clean
        # two-line state so no count leaks between tests.
        self.overlay.set_idle()
        self.addCleanup(self._stop_polling)
        self.addCleanup(self._restore_frame)

    def _restore_frame(self):
        self.app.workflow_frame = self._real_workflow_frame

    def _stop_polling(self):
        """The poll loop reschedules itself; stop it so tests stay headless."""
        job = getattr(self.app, "run_status_overlay_job", None)
        if job is not None:
            try:
                self.app.after_cancel(job)
            except Exception:  # noqa: BLE001 - already fired / destroyed.
                pass
            self.app.run_status_overlay_job = None

    def _refresh(self):
        self._stop_polling()
        self.app.refresh_run_status_overlay()
        return self.overlay.status_label.cget("text")

    def _feed(self, *lines):
        for line in lines:
            self.tracker.feed(line)

    def _texts(self):
        """The two overlay lines as they stand right now."""
        return (
            self.overlay.status_label.cget("text"),
            self.overlay.count_label.cget("text"),
        )

    def _refresh_texts(self):
        """Poll the overlay once (what the 750ms loop does), then read it."""
        self._refresh()
        return self._texts()

    def test_generic_text_before_any_count_is_known(self):
        self._feed("[RUNNING] Date Fill (REGULAR, discharge date)")
        status, count = self._refresh_texts()
        self.assertTrue(status.startswith("Workflow running"), status)
        self.assertEqual(count, "")

    def test_count_shows_under_the_running_text_not_instead_of_it(self):
        # The reported bug: the count replaced "Workflow running...".
        self._feed(
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Claims: 3",
            "[LIVE] processing COLOBONG, JOSEPH DAVE GONZALES | 000000000010920",
        )
        status, count = self._refresh_texts()
        self.assertTrue(status.startswith("Workflow running"), status)
        self.assertEqual(count, "Date Fill 2 left")

    def test_date_fill_counts_down(self):
        self._feed(
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Claims: 3",
            "[LIVE] processing COLOBONG, JOSEPH DAVE GONZALES | 000000000010920",
        )
        self.assertEqual(self._refresh_texts()[1], "Date Fill 2 left")
        self._feed(
            "[LIVE] processing CORMINAL, SAMANTHA BLANCQUERA | 000000000002842"
        )
        self.assertEqual(self._refresh_texts()[1], "Date Fill 1 left")

    def test_final_bill_counts_down_from_the_pending_count(self):
        self._feed(
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Claims: 3",
            "[FAILED] Date Fill (REGULAR, discharge date) - exit code non-zero",
            "[workflow] Date Fill (REGULAR, discharge date) failed - continuing "
            "(continue_on_fail)",
            "[RUNNING] Final Bill (HBSys Billing -> Final)",
            "[LIVE] 3 patient folder(s) under C:\\claims_bot\\output, "
            "2 pending (NO FINAL BILL), 1 skipped",
        )
        self.assertEqual(self._refresh_texts()[1], "Final Bill 2 left")
        self._feed(
            "[LIVE] (1/2) DELA CRUZ, JUAN - 000000000012345 | hospital no 123"
        )
        self.assertEqual(self._refresh_texts()[1], "Final Bill 1 left")

    def test_finished_node_clears_the_count_line(self):
        self._feed(
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Claims: 2",
            "[LIVE] processing A | 1",
        )
        self.assertEqual(self._refresh_texts()[1], "Date Fill 1 left")
        self._feed("[DONE] Date Fill (REGULAR, discharge date)")
        status, count = self._refresh_texts()
        self.assertTrue(status.startswith("Workflow running"), status)
        self.assertEqual(count, "")

    def test_idle_when_no_workflow_is_running(self):
        self._feed(
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Claims: 5",
        )
        self.app.workflow_frame = None
        status, count = self._refresh_texts()
        self.assertFalse(self.overlay.is_running)
        self.assertEqual(status, "Idle")
        self.assertEqual(count, "")

    def test_script_running_clears_the_count_line(self):
        # A dashboard script owns the overlay; a stale node count must not
        # linger next to "Script running".
        self._feed(
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Claims: 4",
        )
        self.assertEqual(self._refresh_texts()[1], "Date Fill 4 left")
        self.app.running_process = object()
        status, count = self._refresh_texts()
        self.assertTrue(status.startswith("Script running"), status)
        self.assertEqual(count, "")

    def test_workflow_tab_feeds_the_tracker(self):
        # The workflow log callback must reach the tracker (that is the only
        # wiring under test: build_workflow_tab routes every log line through
        # WorkflowProgress.feed before the main log()).
        # The real frame, captured before setUp swaps in the fake one.
        real_frame = self._real_workflow_frame
        self.assertIsInstance(self.app.workflow_progress, WorkflowProgress)
        seen = []
        original_log = self.app.log
        self.app.log = lambda message: seen.append(message)
        try:
            callback = real_frame.log_callback
            callback("[RUNNING] Date Fill (REGULAR, discharge date)")
            callback("Claims: 3")
            callback(
                "[LIVE] processing COLOBONG, JOSEPH DAVE GONZALES | 000000000010920"
            )
        finally:
            self.app.log = original_log
        self.assertEqual(self.app.workflow_progress.label, "Date Fill 2 left")
        # The original log still received every line, unfiltered.
        self.assertEqual(len(seen), 3)
        self.assertTrue(seen[0].startswith("[RUNNING]"))


if __name__ == "__main__":
    unittest.main(verbosity=2)