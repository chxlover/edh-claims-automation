"""Unit tests for the Fees Action Table (Slice B — Claims Agent).

decide_action() is a pure function of one fees row dict, so every test
runs headless — no HBSys, no DB, no clicks.

Run from the project root:

    python -m unittest tests.test_agent_fees_actions
    python tests/test_agent_fees_actions.py
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import fees_actions as actions  # noqa: E402

FOLDER = "DELA CRUZ, JUAN - 123456789012345 - ADM20260901_DIS20260903"


def make_row(**overrides):
    row = {
        "Patient Folder": FOLDER,
        "Status": "MATCH",
        "ADM Match": "YES",
        "DIS Match": "YES",
        "Prof Fee Sign Date (hprofserv.pdoctorsigndate)": "2026/09/05",
        "Consent Date (hpatcon1.consentdate)": "2026/09/05",
        "Auth Sign Date (hpatcon1.authsigndate)": "2026/09/05",
        "Ready to Generate XML": "YES",
    }
    row.update(overrides)
    return row


class DecideActionTests(unittest.TestCase):
    def test_ready_row_goes_to_xml_clicker(self):
        decision = actions.decide_action(make_row())
        self.assertEqual(decision.action, actions.ACTION_XML_CLICKER)
        self.assertTrue(decision.tool_available)
        self.assertEqual(decision.ready_verdict, "YES")

    def test_blank_prof_fee_date_goes_to_date_fill(self):
        row = make_row(
            **{"Prof Fee Sign Date (hprofserv.pdoctorsigndate)": "",
               "Ready to Generate XML": "NO"}
        )
        decision = actions.decide_action(row)
        self.assertEqual(decision.action, actions.ACTION_DATE_FILL)
        self.assertIn("Prof Fee Sign Date BLANK", decision.reason)

    def test_blank_consent_date_goes_to_date_fill(self):
        row = make_row(
            **{"Consent Date (hpatcon1.consentdate)": "",
               "Ready to Generate XML": "NO"}
        )
        decision = actions.decide_action(row)
        self.assertEqual(decision.action, actions.ACTION_DATE_FILL)

    def test_blank_auth_date_means_consent_path_date_fill(self):
        # User-confirmed: Auth Sign Date IS Consent — same Date Fill action.
        row = make_row(
            **{"Consent Date (hpatcon1.consentdate)": "",
               "Auth Sign Date (hpatcon1.authsigndate)": "",
               "Ready to Generate XML": "NO"}
        )
        decision = actions.decide_action(row)
        self.assertEqual(decision.action, actions.ACTION_DATE_FILL)
        self.assertIn("Auth Sign Date BLANK (= Consent)", decision.reason)


    def test_no_final_bill_routes_to_available_final_bill_tool(self):
        # Slice E: the FinalBillRunner exists, so the row is routable.
        row = make_row(Status="NO FINAL BILL", **{"Ready to Generate XML": "NO"})
        decision = actions.decide_action(row)
        self.assertEqual(decision.action, actions.ACTION_FINAL_BILL)
        self.assertTrue(decision.tool_available)
        self.assertEqual(decision.review_code, "")
        # The review code stays in the vocabulary for a withdrawn tool.
        self.assertTrue(hasattr(actions, "REVIEW_FINAL_BILL_TOOL_MISSING"))

    def test_final_bill_beats_blank_dates(self):
        # Priority: NO FINAL BILL is checked before signed dates.
        row = make_row(
            Status="NO FINAL BILL",
            **{"Consent Date (hpatcon1.consentdate)": "",
               "Ready to Generate XML": "NO"}
        )
        decision = actions.decide_action(row)
        self.assertEqual(decision.action, actions.ACTION_FINAL_BILL)

    def test_mismatch_goes_to_manual_review(self):
        # Operator decision 2026-10-07: the Final Bill automation
        # only touches bills that were never finalized. MISMATCH
        # totals need an operator decision -> manual review.
        row = make_row(Status="MISMATCH", **{"Ready to Generate XML": "NO"})
        decision = actions.decide_action(row)
        self.assertEqual(decision.action, actions.ACTION_MANUAL_REVIEW)
        self.assertEqual(decision.review_code, actions.REVIEW_MISMATCH)
        self.assertIn("MISMATCH", decision.reason)
        self.assertIn("manual review", decision.reason)

    def test_mismatch_beats_date_fill(self):
        # MISMATCH outranks DATE_FILL: totals are an operator
        # decision, not an automation target (2026-10-07).
        row = make_row(
            Status="MISMATCH",
            **{"Consent Date (hpatcon1.consentdate)": "",
               "Auth Sign Date (hpatcon1.authsigndate)": "",
               "Ready to Generate XML": "NO"},
        )
        decision = actions.decide_action(row)
        self.assertEqual(decision.action, actions.ACTION_MANUAL_REVIEW)
        self.assertEqual(decision.review_code, actions.REVIEW_MISMATCH)

    def test_adm_mismatch_goes_to_manual_review(self):
        row = make_row(**{"ADM Match": "NO", "Ready to Generate XML": "NO"})
        decision = actions.decide_action(row)
        self.assertEqual(decision.action, actions.ACTION_MANUAL_REVIEW)
        self.assertEqual(decision.review_code, actions.REVIEW_ADM_DIS_MISMATCH)

    def test_no_record_goes_to_manual_review(self):
        row = make_row(Status="NO RECORD", **{"Ready to Generate XML": "NO"})
        decision = actions.decide_action(row)
        self.assertEqual(decision.action, actions.ACTION_MANUAL_REVIEW)
        self.assertEqual(decision.review_code, actions.REVIEW_NO_RECORD)

    def test_unreadable_row_never_guessed(self):
        decision = actions.decide_action({})
        self.assertEqual(decision.action, actions.ACTION_MANUAL_REVIEW)
        self.assertEqual(decision.review_code, actions.REVIEW_UNREADABLE_ROW)

        decision = actions.decide_action(None)
        self.assertEqual(decision.action, actions.ACTION_MANUAL_REVIEW)

    def test_gate_no_without_known_cause_is_review(self):
        row = make_row(**{"Ready to Generate XML": "NO"})
        decision = actions.decide_action(row)
        self.assertEqual(decision.action, actions.ACTION_MANUAL_REVIEW)
        self.assertEqual(
            decision.review_code, actions.REVIEW_NOT_READY_OTHER
        )


class PlanBatchTests(unittest.TestCase):
    def test_build_plan_preserves_order(self):
        rows = [
            make_row(),
            make_row(Status="NO FINAL BILL",
                     **{"Ready to Generate XML": "NO"}),
            make_row(**{"Consent Date (hpatcon1.consentdate)": "",
                        "Ready to Generate XML": "NO"}),
            make_row(Status="MISMATCH", **{"Ready to Generate XML": "NO"}),
        ]
        plan = actions.build_plan(rows)
        self.assertEqual(
            [d.action for d in plan],
            [
                actions.ACTION_XML_CLICKER,
                actions.ACTION_FINAL_BILL,
                actions.ACTION_DATE_FILL,
                actions.ACTION_MANUAL_REVIEW,  # MISMATCH -> review (2026-10-07)
            ],
        )

    def test_summarize_plan_counts(self):
        plan = actions.build_plan([
            make_row(),
            make_row(Status="NO FINAL BILL",
                     **{"Ready to Generate XML": "NO"}),
            make_row(**{"Consent Date (hpatcon1.consentdate)": "",
                        "Ready to Generate XML": "NO"}),
        ])
        summary = actions.summarize_plan(plan)
        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.xml_clicker, 1)
        self.assertEqual(summary.final_bill, 1)
        self.assertEqual(summary.date_fill, 1)
        self.assertEqual(summary.manual_review, 0)

    def test_describe_decision_has_no_missing_marker_when_tool_present(self):
        decision = actions.decide_action(
            make_row(Status="NO FINAL BILL", **{"Ready to Generate XML": "NO"})
        )
        text = actions.describe_decision(decision)
        self.assertIn("FINAL_BILL", text)
        self.assertNotIn("[TOOL MISSING]", text)
        self.assertIn(FOLDER, text)

    def test_missing_tool_branch_still_reports_review_code(self):
        # Pin the withdrawn-tool behavior: if TOOL_AVAILABLE is ever flipped
        # back to False, the row must report the review code, never run.
        row = make_row(Status="NO FINAL BILL", **{"Ready to Generate XML": "NO"})
        with mock.patch.dict(
            actions.TOOL_AVAILABLE, {actions.ACTION_FINAL_BILL: False}
        ):
            decision = actions.decide_action(row)
        self.assertFalse(decision.tool_available)
        self.assertEqual(
            decision.review_code, actions.REVIEW_FINAL_BILL_TOOL_MISSING
        )
        self.assertIn("[TOOL MISSING]", actions.describe_decision(decision))


class OutputFolderXmlGateTests(unittest.TestCase):
    """Slice F plan-time gate: read-only CF4/CF5/ESOA scan via output_root."""

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.folder = self.root / FOLDER

    def tearDown(self):
        self.temporary.cleanup()

    def _write_xmls(self, folder, *suffixes):
        folder.mkdir(parents=True, exist_ok=True)
        for suffix in suffixes:
            (folder / ("PATIENT" + suffix)).write_text("<x/>", encoding="utf-8")

    def test_complete_folder_marks_xml_complete(self):
        self._write_xmls(self.folder, "_CF4.xml", "_CF5.xml", "_ESOA.xml")
        decision = actions.decide_action(make_row(), output_root=self.root)
        self.assertEqual(decision.action, actions.ACTION_XML_CLICKER)
        self.assertTrue(decision.xml_complete)
        self.assertIn("CF4+CF5+ESOA", decision.reason)

    def test_partial_folder_stays_in_plan_and_names_missing(self):
        self._write_xmls(self.folder, "_CF4.xml")
        decision = actions.decide_action(make_row(), output_root=self.root)
        self.assertEqual(decision.action, actions.ACTION_XML_CLICKER)
        self.assertFalse(decision.xml_complete)
        self.assertIn("kulang ng XML", decision.reason)
        self.assertIn("CF5+ESOA", decision.reason)

    def test_absent_folder_keeps_legacy_path(self):
        decision = actions.decide_action(make_row(), output_root=self.root)
        self.assertFalse(decision.xml_complete)
        self.assertIn("Ready to Generate XML = YES", decision.reason)

    def test_no_output_root_is_legacy_behavior(self):
        self._write_xmls(self.folder, "_CF4.xml", "_CF5.xml", "_ESOA.xml")
        decision = actions.decide_action(make_row())
        self.assertFalse(decision.xml_complete)
        self.assertNotIn("kulang ng XML", decision.reason)

    def test_absolute_folder_value_used_as_is(self):
        target = self.root / "elsewhere"
        self._write_xmls(target, "_CF4.xml", "_CF5.xml", "_ESOA.xml")
        row = make_row(**{"Patient Folder": str(target)})
        decision = actions.decide_action(
            row, output_root=self.root / "ignored_root"
        )
        self.assertTrue(decision.xml_complete)

    def test_build_plan_threads_output_root(self):
        self._write_xmls(self.folder, "_CF4.xml", "_CF5.xml", "_ESOA.xml")
        gated = actions.build_plan([make_row()], output_root=self.root)
        legacy = actions.build_plan([make_row()])
        self.assertTrue(gated[0].xml_complete)
        self.assertFalse(legacy[0].xml_complete)

    def test_non_ready_row_never_scanned(self):
        self._write_xmls(self.folder, "_CF4.xml", "_CF5.xml", "_ESOA.xml")
        row = make_row(**{
            "Consent Date (hpatcon1.consentdate)": "",
            "Ready to Generate XML": "NO",
        })
        decision = actions.decide_action(row, output_root=self.root)
        self.assertEqual(decision.action, actions.ACTION_DATE_FILL)
        self.assertFalse(decision.xml_complete)

    def test_drop_completed_xml_partitions_and_keeps_order(self):
        done = actions.ActionDecision(
            patient_folder="A", action=actions.ACTION_XML_CLICKER,
            reason="done", xml_complete=True,
        )
        keep_date = actions.ActionDecision(
            patient_folder="B", action=actions.ACTION_DATE_FILL, reason="x",
        )
        keep_xml = actions.ActionDecision(
            patient_folder="C", action=actions.ACTION_XML_CLICKER, reason="y",
        )
        kept, excluded = actions.drop_completed_xml(
            [done, keep_date, keep_xml]
        )
        self.assertEqual(excluded, 1)
        self.assertEqual([d.patient_folder for d in kept], ["B", "C"])

    def test_default_output_root_reads_env(self):
        with mock.patch.dict(
            os.environ, {"CLAIMS_OUTPUT_FOLDER": r"D:\tmp\out"}
        ):
            self.assertEqual(
                actions.default_output_root(), Path(r"D:\tmp\out")
            )
        with mock.patch.dict(os.environ):
            os.environ.pop("CLAIMS_OUTPUT_FOLDER", None)
            self.assertEqual(
                actions.default_output_root(), Path(r"C:\claims_bot\output")
            )


if __name__ == "__main__":
    unittest.main()
