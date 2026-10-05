"""Unit tests for the Agent Plan Store (Slice C — Claims Agent).

Pure plan assembly + approval + persistence. Headless: temp CSV/JSON
files only, no GUI, no HBSys, no DB.

Run from the project root:

    python -m unittest tests.test_agent_plan_store
    python tests/test_agent_plan_store.py
"""

from __future__ import annotations

import csv
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import agent_plan_store as store  # noqa: E402
from core.agent import fees_actions as actions  # noqa: E402

HEADERS = [
    "Patient Folder",
    "Status",
    "ADM Match",
    "DIS Match",
    "Prof Fee Sign Date (hprofserv.pdoctorsigndate)",
    "Consent Date (hpatcon1.consentdate)",
    "Auth Sign Date (hpatcon1.authsigndate)",
    "Ready to Generate XML",
]


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def ready_row(folder):
    return {
        "Patient Folder": folder,
        "Status": "MATCH",
        "ADM Match": "YES",
        "DIS Match": "YES",
        "Prof Fee Sign Date (hprofserv.pdoctorsigndate)": "2026/09/05",
        "Consent Date (hpatcon1.consentdate)": "2026/09/05",
        "Auth Sign Date (hpatcon1.authsigndate)": "2026/09/05",
        "Ready to Generate XML": "YES",
    }



class BuildPlanFromCsvTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_missing_csv_explains_why_empty(self):
        items, summary, note = store.build_plan_from_csv(
            self.root / "nope.csv"
        )
        self.assertEqual(items, [])
        self.assertEqual(summary["date_fill"], 0)
        self.assertIn("Fees Check", note)

    def test_routes_each_row(self):
        csv_path = self.root / "fees.csv"
        write_csv(csv_path, [
            ready_row("READY PATIENT"),
            dict(ready_row("FILL PATIENT"),
                 **{"Consent Date (hpatcon1.consentdate)": "",
                    "Auth Sign Date (hpatcon1.authsigndate)": "",
                    "Ready to Generate XML": "NO"}),
            dict(ready_row("BILL PATIENT"),
                 **{"Status": "NO FINAL BILL",
                    "Ready to Generate XML": "NO"}),
        ])
        items, summary, note = store.build_plan_from_csv(csv_path)
        self.assertEqual(note, "")
        self.assertEqual(
            [item["action"] for item in items],
            [
                actions.ACTION_XML_CLICKER,
                actions.ACTION_DATE_FILL,
                actions.ACTION_FINAL_BILL,
            ],
        )
        self.assertTrue(
            all(item["status"] == store.STATUS_PENDING for item in items)
        )
        self.assertEqual(summary["xml_clicker"], 1)
        self.assertEqual(summary["date_fill"], 1)
        self.assertEqual(summary["final_bill"], 1)


class ApprovalTests(unittest.TestCase):
    def _items(self):
        return [
            {"patient_folder": "A", "action": "date_fill",
             "status": store.STATUS_PENDING},
            {"patient_folder": "B", "action": "final_bill",
             "status": store.STATUS_PENDING},
        ]

    def test_apply_approval_marks_rest_skipped(self):
        items, approved, skipped = store.apply_approval(self._items(), {0})
        self.assertEqual((approved, skipped), (1, 1))
        self.assertEqual(items[0]["status"], store.STATUS_APPROVED)
        self.assertEqual(items[1]["status"], store.STATUS_SKIPPED)

    def test_unknown_indexes_never_approved(self):
        items, approved, skipped = store.apply_approval(self._items(), {9})
        self.assertEqual((approved, skipped), (0, 2))
        self.assertEqual(
            store.approved_items(items), []
        )

    def test_approved_items_returns_slice_e_set(self):
        items, _, _ = store.apply_approval(self._items(), {0, 1})
        approved = store.approved_items(items)
        self.assertEqual(len(approved), 2)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_save_and_load_round_trip(self):
        items = [{"patient_folder": "A", "action": "date_fill",
                  "status": store.STATUS_APPROVED}]
        path = store.save_approved_plan(items, plan_dir=self.root)
        self.assertTrue(path.name.startswith(store.PLAN_PREFIX))
        loaded = store.load_plan(path)
        self.assertEqual(loaded["total_rows"], 1)
        self.assertEqual(loaded["items"][0]["patient_folder"], "A")

    def test_load_unreadable_returns_empty(self):
        self.assertEqual(store.load_plan(self.root / "nope.json"), {})


class OutputXmlGatePlanTests(unittest.TestCase):
    """Slice F / D1-A: completed-XML rows are dropped from the plan + noted."""

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output_root = self.root / "output"
        self.output_root.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def _complete(self, folder):
        target = self.output_root / folder
        target.mkdir()
        for suffix in ("_CF4.xml", "_CF5.xml", "_ESOA.xml"):
            (target / ("X" + suffix)).write_text("<x/>", encoding="utf-8")

    def test_completed_xml_row_dropped_with_note(self):
        self._complete("DONE PATIENT")
        csv_path = self.root / "fees.csv"
        write_csv(csv_path, [
            ready_row("DONE PATIENT"),
            ready_row("PENDING PATIENT"),
        ])
        items, summary, note = store.build_plan_from_csv(
            csv_path, output_root=self.output_root
        )
        self.assertEqual(
            [item["patient_folder"] for item in items], ["PENDING PATIENT"]
        )
        self.assertEqual(summary["xml_clicker"], 1)
        self.assertIn("1 row hindi isinama", note)
        self.assertIn("CF4+CF5+ESOA", note)

    def test_partial_and_absent_folders_stay_in_plan(self):
        partial = self.output_root / "PARTIAL PATIENT"
        partial.mkdir()
        (partial / "X_CF4.xml").write_text("<x/>", encoding="utf-8")
        csv_path = self.root / "fees.csv"
        write_csv(csv_path, [
            ready_row("PARTIAL PATIENT"),
            ready_row("ABSENT PATIENT"),
        ])
        items, summary, note = store.build_plan_from_csv(
            csv_path, output_root=self.output_root
        )
        self.assertEqual(len(items), 2)
        self.assertEqual(summary["xml_clicker"], 2)
        self.assertEqual(note, "")


# -- Slice F part 2: latest CSV base + completion ledger ------------------------

class LatestFeesCsvTests(unittest.TestCase):
    """The plan base is always the newest fees_checker_report*.csv."""

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _write(self, name, mtime):
        path = self.root / name
        path.write_text("Patient Folder\n", encoding="utf-8")
        os.utime(path, (mtime, mtime))
        return path

    def test_picks_newest_by_mtime(self):
        self._write("fees_checker_report.csv", 1_000_000)
        stamped = self._write("fees_checker_report_20260925_161255.csv", 2_000_000)
        self.assertEqual(store.latest_fees_csv(self.root), stamped)

    def test_fixed_report_can_be_newest(self):
        self._write("fees_checker_report_20260101_000000.csv", 1_000_000)
        fixed = self._write("fees_checker_report.csv", 2_000_000)
        self.assertEqual(store.latest_fees_csv(self.root), fixed)

    def test_falls_back_to_default_when_no_report(self):
        self.assertEqual(store.latest_fees_csv(self.root), store.DEFAULT_FEES_CSV)

    def test_resolve_blank_and_canonical_follow_latest(self):
        stamped = self._write("fees_checker_report_20260925_161255.csv", 2_000_000)
        self._write("fees_checker_report.csv", 1_000_000)
        self.assertEqual(store.resolve_fees_csv("", base_dir=self.root), stamped)
        self.assertEqual(
            store.resolve_fees_csv(str(store.DEFAULT_FEES_CSV), base_dir=self.root),
            stamped,
        )

    def test_resolve_explicit_custom_path_is_respected(self):
        custom = self.root / "my_custom.csv"
        custom.write_text("Patient Folder\n", encoding="utf-8")
        self.assertEqual(
            store.resolve_fees_csv(str(custom), base_dir=self.root), custom
        )
        # An explicit timestamped fallback file is respected too.
        stamped = self._write("fees_checker_report_20260101_000000.csv", 9)
        self.assertEqual(
            store.resolve_fees_csv(str(stamped), base_dir=self.root), stamped
        )


class CompletedLedgerTests(unittest.TestCase):
    """logs/agent_completed_actions.json — finished rows never repeat."""

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.ledger = self.root / "agent_completed_actions.json"
        self.csv_path = self.root / "fees.csv"
        write_csv(self.csv_path, [ready_row("READY PATIENT")])

    def tearDown(self):
        self.temporary.cleanup()

    def test_record_then_load_roundtrip(self):
        pair = ("READY PATIENT", actions.ACTION_XML_CLICKER)
        self.assertEqual(store.record_completed_actions([pair], path=self.ledger), 1)
        # Idempotent — recording the same pair again adds nothing.
        self.assertEqual(store.record_completed_actions([pair], path=self.ledger), 0)
        self.assertEqual(store.load_completed_actions(self.ledger), {pair})

    def test_missing_and_corrupt_ledger_read_empty(self):
        self.assertEqual(store.load_completed_actions(self.ledger), set())
        self.ledger.write_text("{not json", encoding="utf-8")
        self.assertEqual(store.load_completed_actions(self.ledger), set())

    def test_drop_completed_actions_partitions(self):
        decisions = actions.build_plan([ready_row("PATIENT A"), ready_row("PATIENT B")])
        kept, dropped = store.drop_completed_actions(
            decisions, {("PATIENT A", actions.ACTION_XML_CLICKER)}
        )
        self.assertEqual(dropped, 1)
        self.assertEqual([d.patient_folder for d in kept], ["PATIENT B"])

    def test_build_plan_drops_ledger_completed_rows(self):
        items, summary, note = store.build_plan_from_csv(
            self.csv_path,
            output_root=self.root / "output",
            completed={("READY PATIENT", actions.ACTION_XML_CLICKER)},
        )
        self.assertEqual(items, [])
        self.assertEqual(summary.get(actions.ACTION_XML_CLICKER), 0)
        self.assertIn("tapos na sa nakaraang run", note)
        self.assertIn("hindi na inuulit", note)

    def test_build_plan_keeps_rows_not_in_completed(self):
        items, summary, note = store.build_plan_from_csv(
            self.csv_path,
            output_root=self.root / "output",
            completed={("OTHER PATIENT", actions.ACTION_DATE_FILL)},
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(note, "")

    def test_build_plan_default_reads_completion_ledger(self):
        store.record_completed_actions(
            [("READY PATIENT", actions.ACTION_XML_CLICKER)], path=self.ledger
        )
        with mock.patch.object(store, "COMPLETED_LEDGER", self.ledger):
            items, summary, note = store.build_plan_from_csv(
                self.csv_path, output_root=self.root / "output"
            )
        self.assertEqual(items, [])
        self.assertIn("hindi na inuulit", note)


if __name__ == "__main__":
    unittest.main()
