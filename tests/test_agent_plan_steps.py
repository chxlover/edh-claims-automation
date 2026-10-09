"""Unit tests for the Plan Steps fan-out (Slice H — Claims Agent).

decide_rows() / order_plan_items() / group_by_patient() are pure functions of
fees row dicts, so every test runs headless — no HBSys, no subprocess, no DB.

Run from the project root:

    python -m unittest tests.test_agent_plan_steps
    python tests/test_agent_plan_steps.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import agent_plan_store as store  # noqa: E402
from core.agent import fees_actions as actions  # noqa: E402
from core.agent import plan_steps as steps  # noqa: E402

JUAN = "DELA CRUZ, JUAN - 123456789012345 - ADM20260901_DIS20260903"
PEDRO = "DELA CRUZ, PEDRO - 123456789012346 - ADM20260901_DIS20260903"
PETER = "DELA CRUZ, PETER - 123456789012347 - ADM20260901_DIS20260903"


def make_row(folder=JUAN, **overrides):
    row = {
        "Patient Folder": folder,
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


def no_dates(folder=JUAN, **overrides):
    return make_row(
        folder,
        **{
            "Prof Fee Sign Date (hprofserv.pdoctorsigndate)": "",
            "Consent Date (hpatcon1.consentdate)": "",
            "Auth Sign Date (hpatcon1.authsigndate)": "",
            "Ready to Generate XML": "NO",
            **overrides,
        }
    )


class DecideRowsTests(unittest.TestCase):
    def test_final_bill_with_missing_dates_fans_out_to_two_steps(self):
        result = steps.decide_rows(no_dates(Status="NO FINAL BILL"))
        self.assertEqual(
            [step.action for step in result],
            [actions.ACTION_FINAL_BILL, actions.ACTION_DATE_FILL],
        )
        self.assertIn("Prof Fee Sign Date BLANK", result[1].reason)

    def test_mismatch_is_manual_review_only(self):
        # 2026-10-07: MISMATCH no longer fans out — it is an
        # operator decision (manual review), never auto-final-billed.
        result = steps.decide_rows(no_dates(Status="MISMATCH"))
        self.assertEqual(
            [step.action for step in result],
            [actions.ACTION_MANUAL_REVIEW],
        )

    def test_final_bill_without_missing_dates_stays_one_step(self):
        result = steps.decide_rows(make_row(Status="NO FINAL BILL"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].action, actions.ACTION_FINAL_BILL)

    def test_match_with_missing_dates_is_date_fill_only(self):
        result = steps.decide_rows(no_dates())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].action, actions.ACTION_DATE_FILL)

    def test_ready_row_is_xml_clicker_only(self):
        result = steps.decide_rows(make_row())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].action, actions.ACTION_XML_CLICKER)

    def test_no_record_never_mixes_with_automation(self):
        result = steps.decide_rows(no_dates(Status="NO RECORD"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].action, actions.ACTION_MANUAL_REVIEW)

    def test_unreadable_row_is_manual_review_only(self):
        result = steps.decide_rows({"Patient Folder": ""})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].action, actions.ACTION_MANUAL_REVIEW)

    def test_first_step_always_matches_decide_action(self):
        """Backward compatibility: row 1 IS the old single-action result."""
        for row in (
            make_row(),
            no_dates(),
            no_dates(Status="NO FINAL BILL"),
            no_dates(Status="MISMATCH"),
            no_dates(Status="NO RECORD"),
        ):
            with self.subTest(status=row.get("Status")):
                self.assertEqual(
                    steps.decide_rows(row)[0].action,
                    actions.decide_action(row).action,
                )

    def test_build_step_plan_flattens_and_preserves_order(self):
        plan = steps.build_step_plan([
            no_dates(JUAN, Status="NO FINAL BILL"),
            make_row(PEDRO),
            no_dates(PETER),
        ])
        self.assertEqual(
            [step.action for step in plan],
            [
                actions.ACTION_FINAL_BILL,
                actions.ACTION_DATE_FILL,
                actions.ACTION_XML_CLICKER,
                actions.ACTION_DATE_FILL,
            ],
        )
class OrderPlanItemsTests(unittest.TestCase):
    def test_final_bill_is_placed_before_date_fill_of_same_patient(self):
        items = [
            {"patient_folder": JUAN, "action": actions.ACTION_DATE_FILL},
            {"patient_folder": PEDRO, "action": actions.ACTION_FINAL_BILL},
            {"patient_folder": JUAN, "action": actions.ACTION_FINAL_BILL},
        ]
        ordered = steps.order_plan_items(items)
        self.assertEqual(
            [(item["patient_folder"], item["action"]) for item in ordered],
            [
                (JUAN, actions.ACTION_FINAL_BILL),
                (JUAN, actions.ACTION_DATE_FILL),
                (PEDRO, actions.ACTION_FINAL_BILL),
            ],
        )

    def test_sort_is_stable_inside_one_action(self):
        first = "A, PATIENT - 1 - ADM20260901_DIS20260903"
        second = "B, PATIENT - 2 - ADM20260901_DIS20260903"
        third = "C, PATIENT - 3 - ADM20260901_DIS20260903"
        items = [
            {"patient_folder": first, "action": actions.ACTION_XML_CLICKER},
            {"patient_folder": second, "action": actions.ACTION_XML_CLICKER},
            {"patient_folder": third, "action": actions.ACTION_XML_CLICKER},
        ]
        ordered = steps.order_plan_items(items)
        self.assertEqual(
            [item["patient_folder"] for item in ordered],
            [first, second, third],
        )

    def test_single_step_rows_keep_their_original_order(self):
        """Regression: a plan with no patient needing two steps is unchanged."""
        items = [
            {"patient_folder": JUAN, "action": actions.ACTION_DATE_FILL},
            {"patient_folder": PEDRO, "action": actions.ACTION_XML_CLICKER},
            {"patient_folder": PETER, "action": actions.ACTION_DATE_FILL},
        ]
        self.assertEqual(steps.order_plan_items(items), items)

    def test_unknown_action_sorts_last_without_losing_rows(self):
        items = [
            {"patient_folder": JUAN, "action": "something_new"},
            {"patient_folder": JUAN, "action": actions.ACTION_FINAL_BILL},
        ]
        ordered = steps.order_plan_items(items)
        self.assertEqual(ordered[0]["action"], actions.ACTION_FINAL_BILL)
        self.assertEqual(len(ordered), 2)

    def test_works_on_action_decision_objects(self):
        plan = steps.build_step_plan([no_dates(JUAN, Status="NO FINAL BILL")])
        ordered = steps.order_plan_items(plan)
        self.assertEqual(
            [step.action for step in ordered],
            [actions.ACTION_FINAL_BILL, actions.ACTION_DATE_FILL],
        )


class GroupByPatientTests(unittest.TestCase):
    def setUp(self):
        self.plan = steps.build_step_plan([
            no_dates(JUAN, Status="NO FINAL BILL"),
            make_row(PEDRO),
            no_dates(PETER),
        ])
        self.chains = steps.group_by_patient(self.plan)

    def test_one_chain_per_patient_in_first_appearance_order(self):
        self.assertEqual(
            [chain.patient_folder for chain in self.chains],
            [JUAN, PEDRO, PETER],
        )

    def test_juan_chain_has_both_steps_in_order(self):
        juan = self.chains[0]
        self.assertEqual(
            juan.actions, (actions.ACTION_FINAL_BILL, actions.ACTION_DATE_FILL)
        )
        self.assertTrue(juan.has_final_bill)
        self.assertIsNotNone(juan.step_for(actions.ACTION_FINAL_BILL))
        self.assertIsNone(juan.step_for(actions.ACTION_XML_CLICKER))

    def test_patient_without_final_bill_is_single_step(self):
        pedro = self.chains[1]
        self.assertEqual(pedro.actions, (actions.ACTION_XML_CLICKER,))
        self.assertFalse(pedro.has_final_bill)

    def test_hospital_no_is_injected_only_when_asked(self):
        self.assertEqual(self.chains[0].hospital_no, "")
        chained = steps.group_by_patient(
            self.plan, hospital_no_fn=lambda folder: "123456789012345"
        )
        self.assertEqual(chained[0].hospital_no, "123456789012345")

    def test_as_dict_is_json_friendly(self):
        payload = self.chains[0].as_dict()
        self.assertEqual(payload["patient_folder"], JUAN)
        self.assertEqual(payload["index"], 0)
        self.assertEqual(
            payload["actions"],
            [actions.ACTION_FINAL_BILL, actions.ACTION_DATE_FILL],
        )
        self.assertEqual(len(payload["steps"]), 2)


class PrerequisiteNoteTests(unittest.TestCase):
    def test_note_present_when_final_bill_is_in_the_same_chain(self):
        chain = steps.group_by_patient(
            steps.decide_rows(no_dates(Status="NO FINAL BILL"))
        )[0]
        note = steps.prerequisite_note(actions.ACTION_DATE_FILL, chain)
        self.assertTrue(note)
        self.assertIn("FINAL BILL", note)

    def test_no_note_when_final_bill_is_absent(self):
        """The common case: Final Bill finished in a past run."""
        chain = steps.group_by_patient(steps.decide_rows(no_dates()))[0]
        self.assertEqual(
            steps.prerequisite_note(actions.ACTION_DATE_FILL, chain), ""
        )

    def test_no_note_for_other_actions_or_no_chain(self):
        chain = steps.group_by_patient(
            steps.decide_rows(no_dates(Status="NO FINAL BILL"))
        )[0]
        self.assertEqual(
            steps.prerequisite_note(actions.ACTION_FINAL_BILL, chain), ""
        )
        self.assertEqual(
            steps.prerequisite_note(actions.ACTION_DATE_FILL, None), ""
        )


class PlanStoreFanOutTests(unittest.TestCase):
    """Slice H: the CSV -> panel path produces the two-step rows."""

    HEADER = (
        "Patient Folder,Status,ADM Match,DIS Match,"
        "Prof Fee Sign Date (hprofserv.pdoctorsigndate),"
        "Consent Date (hpatcon1.consentdate),"
        "Auth Sign Date (hpatcon1.authsigndate),Ready to Generate XML\n"
    )

    def _csv(self, folder, status, dates, ready):
        # Quoted: patient folders contain commas ("DELA CRUZ, JUAN ..."), so an
        # unquoted field would shift every column and mis-read the status.
        return (
            f'{self.HEADER}"{folder}",{status},YES,YES,'
            f"{dates},{dates},{dates},{ready}\n"
        )

    def _write(self, text):
        handle = TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        path = Path(handle.name) / "fees.csv"
        path.write_text(text, encoding="utf-8")
        return path

    def test_no_final_bill_with_blank_dates_gives_two_ordered_rows(self):
        path = self._write(self._csv(JUAN, "NO FINAL BILL", "", "NO"))
        items, _summary, _note = store.build_plan_from_csv(
            path, output_root=Path(self._write("").parent), completed=set()
        )
        self.assertEqual(
            [(item["patient_folder"], item["action"]) for item in items],
            [
                (JUAN, actions.ACTION_FINAL_BILL),
                (JUAN, actions.ACTION_DATE_FILL),
            ],
        )

    def test_fan_out_false_keeps_one_row_per_fees_row(self):
        path = self._write(self._csv(JUAN, "NO FINAL BILL", "", "NO"))
        items, _summary, _note = store.build_plan_from_csv(
            path,
            output_root=Path(self._write("").parent),
            completed=set(),
            fan_out=False,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["action"], actions.ACTION_FINAL_BILL)

    def test_completed_final_bill_leaves_only_the_date_fill_row(self):
        """The feedback loop: Final Bill done in a past run -> one row left."""
        path = self._write(self._csv(JUAN, "NO FINAL BILL", "", "NO"))
        items, _summary, note = store.build_plan_from_csv(
            path,
            output_root=Path(self._write("").parent),
            completed={(JUAN, actions.ACTION_FINAL_BILL)},
        )
        self.assertEqual(
            [(item["patient_folder"], item["action"]) for item in items],
            [(JUAN, actions.ACTION_DATE_FILL)],
        )
        self.assertIn("1 row", note)

    def test_ledger_keeps_both_steps_keyed_separately(self):
        """(folder, action) is the ledger key, so two steps never collide."""
        path = self._write(self._csv(JUAN, "NO FINAL BILL", "", "NO"))
        output_root = Path(self._write("").parent)
        items, _summary, _note = store.build_plan_from_csv(
            path, output_root=output_root, completed=set()
        )
        completed = {(item["patient_folder"], item["action"]) for item in items}
        self.assertEqual(
            completed,
            {
                (JUAN, actions.ACTION_FINAL_BILL),
                (JUAN, actions.ACTION_DATE_FILL),
            },
        )
        again, _summary, _note = store.build_plan_from_csv(
            path, output_root=output_root, completed=completed
        )
        self.assertEqual(again, [])


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()