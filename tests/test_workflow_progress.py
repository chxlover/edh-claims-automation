"""Tests for the remaining-patient counter (core/workflow_progress.py).

The tracker is DISPLAY-ONLY: it parses workflow log lines into "N left" text
for the run-status overlay. These tests use trimmed REAL log lines from the
2026-10-09 workflow runs (Date Fill "Claims: 3" / "[LIVE] processing ...",
Final Bill "3 patient folder(s) ... 1 pending" / "[LIVE] (1/1) ...").

Run from the project root:

    python -m unittest tests.test_workflow_progress
    python tests/test_workflow_progress.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.workflow_progress import (  # noqa: E402
    DATE_FILL,
    FINAL_BILL,
    WorkflowProgress,
    node_kind,
)


def _feed_all(tracker: WorkflowProgress, lines) -> None:
    for line in lines:
        tracker.feed(line)


class NodeKindTests(unittest.TestCase):
    """The `[RUNNING] <label>` anchor decides what is being counted."""

    def test_registry_labels_are_recognised(self):
        self.assertEqual(
            node_kind("Date Fill (REGULAR, discharge date)"), DATE_FILL
        )
        self.assertEqual(
            node_kind("Date Fill (ABTC, admission date)"), DATE_FILL
        )
        self.assertEqual(
            node_kind("Final Bill (HBSys Billing -> Final)"), FINAL_BILL
        )

    def test_unrelated_nodes_are_not_counted(self):
        for label in ("Claims Processor", "XML Clicker", "Fees Checker", ""):
            with self.subTest(label=label):
                self.assertIsNone(node_kind(label))

    def test_none_and_junk_are_not_counted(self):
        self.assertIsNone(node_kind(None))
        self.assertIsNone(node_kind("   "))


class DateFillCountingTests(unittest.TestCase):
    """Date Fill prints `Claims: N` then one `[LIVE] processing` per patient."""

    def test_counts_down_from_the_batch_size(self):
        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Mode: LIVE",
            "Claims: 3",
            "[LIVE] processing COLOBONG, JOSEPH DAVE GONZALES | 000000000010920",
        ])
        self.assertEqual(tracker.kind, DATE_FILL)
        self.assertEqual(tracker.remaining, 2)
        self.assertEqual(tracker.label, "Date Fill 2 left")

    def test_no_count_before_the_batch_size_is_known(self):
        # Before `Claims: N` the overlay must keep its generic text rather
        # than invent a total.
        tracker = WorkflowProgress()
        tracker.feed("[RUNNING] Date Fill (REGULAR, discharge date)")
        self.assertEqual(tracker.label, "")
        tracker.feed("Claims: 3")
        self.assertEqual(tracker.label, "Date Fill 3 left")

    def test_last_patient_reaches_zero(self):
        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Claims: 1",
            "[LIVE] processing DAYAG, VIC ERNESTO JR TAYABAN | 000000000001450",
        ])
        self.assertEqual(tracker.remaining, 0)
        self.assertEqual(tracker.label, "Date Fill 0 left")

    def test_abtc_node_is_counted_too(self):
        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "[RUNNING] Date Fill (ABTC, admission date)",
            "Claims: 2",
            "[LIVE] processing USBAL, JAN CARLO DAPPANAN | 000000000022158",
        ])
        self.assertEqual(tracker.kind, DATE_FILL)
        self.assertEqual(tracker.label, "Date Fill 1 left")

    def test_lines_outside_a_counted_node_are_ignored(self):
        # No [RUNNING] yet -> a stray "Claims: 9" must not start counting.
        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "Claims: 9",
            "[LIVE] processing GHOST, PATIENT | 000000000000001",
        ])
        self.assertEqual(tracker.kind, "")
        self.assertEqual(tracker.label, "")


class FinalBillCountingTests(unittest.TestCase):
    """Final Bill prints the pending count then one `(n/total)` line per patient."""

    def test_counts_down_from_the_pending_count(self):
        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "[RUNNING] Final Bill (HBSys Billing -> Final)",
            "[LIVE] 3 patient folder(s) under C:\\claims_bot\\output, "
            "2 pending (NO FINAL BILL), 1 skipped",
        ])
        self.assertEqual(tracker.kind, FINAL_BILL)
        self.assertEqual(tracker.label, "Final Bill 2 left")

    def test_item_line_decrements_the_count(self):
        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "[RUNNING] Final Bill (HBSys Billing -> Final)",
            "[LIVE] 3 patient folder(s) under C:\\claims_bot\\output, "
            "2 pending (NO FINAL BILL), 1 skipped",
            "[LIVE] (1/2) DELA CRUZ, JUAN - 000000000012345 - "
            "ADM20260901_DIS20260903 | hospital no 000000000012345",
        ])
        self.assertEqual(tracker.label, "Final Bill 1 left")

    def test_skipped_folders_are_not_counted_as_remaining(self):
        # The node processes only the NO FINAL BILL folders, so "3 folders"
        # must never show as 3 remaining when only 1 is pending.
        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "[RUNNING] Final Bill (HBSys Billing -> Final)",
            "[LIVE] 3 patient folder(s) under C:\\claims_bot\\output, "
            "1 pending (NO FINAL BILL), 2 skipped",
        ])
        self.assertEqual(tracker.remaining, 1)
        self.assertEqual(tracker.label, "Final Bill 1 left")

    def test_zero_pending_reports_no_remaining(self):
        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "[RUNNING] Final Bill (HBSys Billing -> Final)",
            "[LIVE] 3 patient folder(s) under C:\\claims_bot\\output, "
            "0 pending (NO FINAL BILL), 3 skipped",
        ])
        # total 0 -> nothing worth showing; the node itself logs "nothing to do".
        self.assertEqual(tracker.label, "")


class NodeSwitchTests(unittest.TestCase):
    """A finished or restarted node must never keep the previous count."""

    def test_failed_date_fill_clears_its_count(self):
        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Claims: 3",
            "[LIVE] processing COLOBONG, JOSEPH DAVE GONZALES | 000000000010920",
            "[FAILED] Date Fill (REGULAR, discharge date) - exit code non-zero",
        ])
        self.assertEqual(tracker.kind, "")
        self.assertEqual(tracker.label, "")

    def test_continue_on_fail_line_does_not_resurrect_the_count(self):
        # The engine logs this right after the FAILED line; it is not a
        # [RUNNING] line, so the count must stay cleared.
        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Claims: 3",
            "[LIVE] processing COLOBONG, JOSEPH DAVE GONZALES | 000000000010920",
            "[FAILED] Date Fill (REGULAR, discharge date) - exit code non-zero",
            "[workflow] Date Fill (REGULAR, discharge date) failed - continuing "
            "(continue_on_fail)",
        ])
        self.assertEqual(tracker.label, "")

    def test_done_and_stopped_clear_the_count(self):
        for ending in (
            "[DONE] Final Bill (HBSys Billing -> Final)",
            "[STOPPED] Final Bill (HBSys Billing -> Final)",
            "[SKIPPED] Final Bill (HBSys Billing -> Final)",
        ):
            with self.subTest(ending=ending):
                tracker = WorkflowProgress()
                _feed_all(tracker, [
                    "[RUNNING] Final Bill (HBSys Billing -> Final)",
                    "[LIVE] 3 patient folder(s) under C:\\claims_bot\\output, "
                    "1 pending (NO FINAL BILL), 2 skipped",
                    ending,
                ])
                self.assertEqual(tracker.label, "")

    def test_next_node_starts_from_its_own_count(self):
        # The real 2026-10-09 chain: Date Fill (FAILED, continue_on_fail)
        # then Final Bill.
        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Claims: 3",
            "[LIVE] processing COLOBONG, JOSEPH DAVE GONZALES | 000000000010920",
            "[LIVE] processing CORMINAL, SAMANTHA BLANCQUERA | 000000000002842",
            "[LIVE] processing DAYAG, VIC ERNESTO JR TAYABAN | 000000000001450",
            "[FAILED] Date Fill (REGULAR, discharge date) - exit code non-zero",
            "[workflow] Date Fill (REGULAR, discharge date) failed - continuing "
            "(continue_on_fail)",
            "[RUNNING] Final Bill (HBSys Billing -> Final)",
        ])
        self.assertEqual(tracker.kind, FINAL_BILL)
        self.assertEqual(tracker.label, "")
        _feed_all(tracker, [
            "[LIVE] 3 patient folder(s) under C:\\claims_bot\\output, "
            "1 pending (NO FINAL BILL), 2 skipped",
        ])
        self.assertEqual(tracker.label, "Final Bill 1 left")
        _feed_all(tracker, [
            "[LIVE] (1/1) DAYAG, VIC ERNESTO JR TAYABAN - 000000000001450",
        ])
        self.assertEqual(tracker.label, "Final Bill 0 left")

    def test_an_unrelated_node_clears_the_count(self):
        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Claims: 3",
            "[RUNNING] XML Clicker",
        ])
        self.assertEqual(tracker.kind, "")
        self.assertEqual(tracker.label, "")


class RobustnessTests(unittest.TestCase):
    """Display-only: junk may cost a count, never raise."""

    def test_blank_and_none_lines_are_safe(self):
        tracker = WorkflowProgress()
        for line in ("", "   ", None):
            tracker.feed(line)
        self.assertEqual(tracker.label, "")

    def test_unknown_lines_never_change_the_count(self):
        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Claims: 4",
            "[LIVE] Hospital No. not visible on the base screen",
            "[LIVE] captured logs\\patient_load_probe_20261009_104223.png",
            "Run log: C:\\claims_bot\\logs\\hbsys_fill_run_20261009_104004.csv",
            "[workflow] Date Fill (REGULAR, discharge date): python -m x",
        ])
        self.assertEqual(tracker.remaining, 4)

    def test_remaining_never_goes_negative(self):
        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Claims: 1",
            "[LIVE] processing A | 1",
            "[LIVE] processing B | 2",
            "[LIVE] processing C | 3",
        ])
        self.assertEqual(tracker.remaining, 0)
        self.assertEqual(tracker.label, "Date Fill 0 left")

    def test_reset_clears_everything(self):
        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Claims: 5",
            "[LIVE] processing A | 1",
        ])
        self.assertEqual(tracker.remaining, 4)
        tracker.reset()
        self.assertEqual(tracker.kind, "")
        self.assertEqual(tracker.remaining, 0)
        self.assertEqual(tracker.label, "")

    def test_label_fits_the_overlay_width(self):
        # gui.run_status_overlay truncates longer text; these must fit whole
        # (label + the 3-dot animation) inside MAX_STATUS_CHARS.
        from gui.run_status_overlay import MAX_STATUS_CHARS

        tracker = WorkflowProgress()
        _feed_all(tracker, [
            "[RUNNING] Date Fill (REGULAR, discharge date)",
            "Claims: 999",
        ])
        self.assertLessEqual(len(tracker.label) + 3, MAX_STATUS_CHARS)
        _feed_all(tracker, [
            "[RUNNING] Final Bill (HBSys Billing -> Final)",
            "[LIVE] 999 patient folder(s) under C:\\out, "
            "999 pending (NO FINAL BILL), 0 skipped",
        ])
        self.assertLessEqual(len(tracker.label) + 3, MAX_STATUS_CHARS)


if __name__ == "__main__":
    unittest.main(verbosity=2)