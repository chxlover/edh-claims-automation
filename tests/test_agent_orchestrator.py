"""Unit tests for the Claims Agent Orchestrator (Slice E).

run_approved_plan() is a pure dispatcher over injected executors, so every
test runs headless — no HBSys, no DB, no subprocess, no clicks. The default
executors are tested through their injectable queue/runner hooks.

Run from the project root:

    python -m unittest tests.test_agent_orchestrator
    python tests/test_agent_orchestrator.py
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import agent_plan_store as plan_store  # noqa: E402
from core.agent import fees_actions as actions  # noqa: E402
from core.agent import final_bill_actions as final_bill  # noqa: E402
from core.agent import orchestrator  # noqa: E402

FOLDER = "DELA CRUZ, JUAN - 123456789012345 - ADM20260901_DIS20260903"
OTHER_FOLDER = "SANTOS, MARIA - 000000000021401 - ADM20260906_DIS20260912"
BAD_FOLDER = "NOT A PATIENT FOLDER"
THIRD_FOLDER = "DELA CRUZ, PEDRO - 123456789012346 - ADM20260906_DIS20260912"


# -- test hygiene (2026-09-28) -------------------------------------------------
# The _default_final_bill tests exercise the REAL load path, which calls
# diagnose_screen() -> save_screenshot() when a load cannot be verified. That
# used to write a real desktop screenshot (logs/agent_diag_*.png) on every
# test run, i.e. whatever patient data happened to be on the operator's screen.
# Patching both at module scope keeps the tests hermetic.

_REAL_DIAGNOSE_SCREEN = final_bill.diagnose_screen
_REAL_SAVE_SCREENSHOT = final_bill.save_screenshot


def setUpModule():
    final_bill.diagnose_screen = (
        lambda context="screen", **kwargs: f"{context}: diagnostics (test stub)"
    )
    final_bill.save_screenshot = lambda prefix="agent_diag": ""


def tearDownModule():
    final_bill.diagnose_screen = _REAL_DIAGNOSE_SCREEN
    final_bill.save_screenshot = _REAL_SAVE_SCREENSHOT



def make_item(action, folder=FOLDER, status=None, **overrides):
    """One plan item shaped like agent_plan_store.to_plan_items() rows."""
    item = {
        "patient_folder": folder,
        "action": action,
        "reason": "test row",
        "review_code": "",
        "tool_available": True,
        "ready_verdict": "YES",
        "status": plan_store.STATUS_APPROVED if status is None else status,
    }
    item.update(overrides)
    return item


class RecordingExecutors:
    """Stand-in executors that record calls and return scripted results."""

    def __init__(self):
        self.calls = []       # (action, hospital_no, folder) per invocation
        self.results = {}     # action -> (status, detail)
        self.raise_on = {}    # action -> exception instance to raise

    def make(self, action):
        def executor(hospital_no, folder):
            self.calls.append((action, hospital_no, folder))
            if action in self.raise_on:
                raise self.raise_on[action]
            return self.results.get(action, (orchestrator.OUTCOME_OK, "done"))
        return executor


class FolderParsingTests(unittest.TestCase):
    def test_hospital_number_parsed_from_folder(self):
        self.assertEqual(
            orchestrator.hospital_number_from_folder(FOLDER), "123456789012345"
        )
        self.assertEqual(
            orchestrator.hospital_number_from_folder(OTHER_FOLDER),
            "000000000021401",
        )

    def test_patient_name_parsed_from_folder(self):
        self.assertEqual(
            orchestrator.patient_name_from_folder(FOLDER), "DELA CRUZ, JUAN"
        )

    def test_unparseable_folder_never_guessed(self):
        self.assertEqual(orchestrator.hospital_number_from_folder(BAD_FOLDER), "")
        self.assertEqual(orchestrator.hospital_number_from_folder(""), "")
        self.assertEqual(orchestrator.hospital_number_from_folder(None), "")
        self.assertEqual(orchestrator.patient_name_from_folder(BAD_FOLDER), "")


class FinalBillPreconditionTests(unittest.TestCase):
    """final_bill_block_reason() — pure, evidence-based, never guesses."""

    def test_blocked_when_no_billing_form_open(self):
        reason = orchestrator.final_bill_block_reason(FOLDER, ["User Menu"])
        self.assertIn("no Billing form open", reason)
        self.assertIn("manual steps 1-4", reason)

    def test_blocked_when_billing_form_for_other_patient(self):
        reason = orchestrator.final_bill_block_reason(
            FOLDER, ["Billing (SANTOS, MARIA)", "User Menu"]
        )
        self.assertIn("different patient", reason)
        self.assertIn("Billing (SANTOS, MARIA)", reason)
        self.assertIn("Billing (DELA CRUZ, JUAN)", reason)

    def test_empty_reason_when_billing_form_matches_patient(self):
        reason = orchestrator.final_bill_block_reason(
            FOLDER, ["Billing (DELA CRUZ, JUAN)", "User Menu"]
        )
        self.assertEqual(reason, "")

    def test_form_with_trailing_space_is_not_a_different_patient(self):
        # Live 2026-10-02: HBSys titled the form "Billing (VALENTINO, NIKKI )"
        # - an empty middle name leaves a space before ")" - and the row BLOCKed
        # as "a different patient". Same patient, different bytes.
        folder = "VALENTINO, NIKKI - 000000000021842 - ADM20260922_DIS20260924"
        reason = orchestrator.final_bill_block_reason(
            folder, ["Billing (VALENTINO, NIKKI )", "User Menu"]
        )
        self.assertEqual(reason, "")

    def test_repeated_inner_spaces_still_match_the_folder_name(self):
        reason = orchestrator.final_bill_block_reason(
            FOLDER, ["Billing (DELA  CRUZ,   JUAN )", "User Menu"]
        )
        self.assertEqual(reason, "")

    def test_a_truly_different_patient_still_blocks(self):
        reason = orchestrator.final_bill_block_reason(
            FOLDER, ["Billing (SANTOS, MARIA)", "User Menu"]
        )
        self.assertIn("different patient", reason)
        self.assertIn("Billing (SANTOS, MARIA)", reason)
        self.assertIn("Billing (DELA CRUZ, JUAN)", reason)

    def test_blocked_when_folder_name_unparseable(self):
        reason = orchestrator.final_bill_block_reason(BAD_FOLDER, [])
        self.assertIn("no patient name", reason)


class FinalBillSuffixToleranceTests(unittest.TestCase):
    """Operator rule 2026-10-09 — the live DAYAG case.

    Folder "DAYAG, VIC ERNESTO JR TAYABAN" against the loaded HBSys form
    "Billing (DAYAG, VIC ERNESTO TAYABAN)" used to BLOCK a correctly loaded
    patient ("Billing form is open for a different patient"). The
    generational suffix (III, JR, SR, II, IV, I) is now ignored for the
    comparison — but only when the match is UNIQUE, so a father and a son
    whose names differ by nothing but the suffix still BLOCK.
    """

    FOLDER = (
        "DAYAG, VIC ERNESTO JR TAYABAN - 000000000001450 - "
        "ADM20261004_DIS20261008"
    )

    def test_live_dayag_case_no_longer_blocks(self):
        reason = orchestrator.final_bill_block_reason(
            self.FOLDER, ["Billing (DAYAG, VIC ERNESTO TAYABAN)", "User Menu"]
        )
        self.assertEqual(reason, "")

    def test_each_operator_suffix_matches(self):
        for suffix in ("III", "JR", "SR", "II", "IV", "I"):
            with self.subTest(suffix=suffix):
                folder = (
                    f"SMITH, JOHN {suffix} - 000000000001450 - "
                    "ADM20261004_DIS20261008"
                )
                self.assertEqual(
                    orchestrator.final_bill_block_reason(
                        folder, ["Billing (SMITH, JOHN)", "User Menu"]
                    ),
                    "",
                )

    def test_ambiguous_suffix_forms_still_block(self):
        reason = orchestrator.final_bill_block_reason(
            self.FOLDER,
            [
                "Billing (DAYAG, VIC ERNESTO SR TAYABAN)",
                "Billing (DAYAG, VIC ERNESTO TAYABAN)",
            ],
        )
        self.assertIn("different patient", reason)

    def test_a_truly_different_patient_still_blocks(self):
        reason = orchestrator.final_bill_block_reason(
            self.FOLDER, ["Billing (SANTOS, MARIA)", "User Menu"]
        )
        self.assertIn("different patient", reason)
        self.assertIn("Billing (DAYAG, VIC ERNESTO JR TAYABAN)", reason)

    def test_suffix_inside_a_name_is_not_stripped(self):
        folder = "CRUZ, MARIA - 000000000001450 - ADM20261004_DIS20261008"
        self.assertIn(
            "different patient",
            orchestrator.final_bill_block_reason(
                folder, ["Billing (CRUZ, MARIA IRA)", "User Menu"]
            ),
        )



class DispatchTests(unittest.TestCase):
    """run_approved_plan() over injected executors — order + guarantees."""

    def run_plan(self, items, executors=None, **kwargs):
        executors = executors or RecordingExecutors()
        logs = []
        # Hermetic: without run_dir the heartbeat (agent_current_run.json)
        # would be written into the real logs folder of the project.
        kwargs.setdefault("run_dir", self.enterContext(TemporaryDirectory()))
        report = orchestrator.run_approved_plan(
            items,
            date_fill_fn=executors.make(actions.ACTION_DATE_FILL),
            xml_clicker_fn=executors.make(actions.ACTION_XML_CLICKER),
            final_bill_fn=executors.make(actions.ACTION_FINAL_BILL),
            log_fn=logs.append,
            save=False,
            **kwargs,
        )
        return report, executors, logs

    def test_rows_run_in_plan_order(self):
        """Slice H: rows of the SAME patient are grouped, FINAL BILL first.

        The raw plan had xml_clicker(OTHER) before final_bill(OTHER) because
        both landed in the same folder. Ordering is what the operator asked
        for on 2026-10-05: a patient's FINAL BILL runs before anything else
        for that patient. Pass order_steps=False for the old raw plan order.
        """
        items = [
            make_item(actions.ACTION_XML_CLICKER, folder=OTHER_FOLDER),
            make_item(actions.ACTION_DATE_FILL),
            make_item(actions.ACTION_FINAL_BILL, folder=OTHER_FOLDER),
        ]
        report, executors, _ = self.run_plan(items)

        self.assertEqual(
            [call[0] for call in executors.calls],
            [
                actions.ACTION_FINAL_BILL,
                actions.ACTION_XML_CLICKER,
                actions.ACTION_DATE_FILL,
            ],
        )
        # Each row still used ITS OWN patient's hospital number.
        self.assertEqual(
            [call[1] for call in executors.calls],
            ["000000000021401", "000000000021401", "123456789012345"],
        )
        self.assertEqual(
            [outcome.action for outcome in report.outcomes],
            [
                actions.ACTION_FINAL_BILL,
                actions.ACTION_XML_CLICKER,
                actions.ACTION_DATE_FILL,
            ],
        )
        self.assertEqual(
            [outcome.status for outcome in report.outcomes],
            [orchestrator.OUTCOME_OK] * 3,
        )

    def test_order_steps_false_keeps_the_raw_plan_order(self):
        items = [
            make_item(actions.ACTION_XML_CLICKER, folder=OTHER_FOLDER),
            make_item(actions.ACTION_DATE_FILL),
            make_item(actions.ACTION_FINAL_BILL, folder=OTHER_FOLDER),
        ]
        _report, executors, _logs = self.run_plan(items, order_steps=False)
        self.assertEqual(
            [call[0] for call in executors.calls],
            [
                actions.ACTION_XML_CLICKER,
                actions.ACTION_DATE_FILL,
                actions.ACTION_FINAL_BILL,
            ],
        )

    def test_one_step_per_patient_keeps_the_plan_order(self):
        """Regression: three DIFFERENT patients — nothing to regroup."""
        items = [
            make_item(actions.ACTION_DATE_FILL, folder=OTHER_FOLDER),
            make_item(actions.ACTION_XML_CLICKER, folder=THIRD_FOLDER),
            make_item(actions.ACTION_DATE_FILL),
        ]
        _report, executors, _logs = self.run_plan(items)
        self.assertEqual(
            [call[0] for call in executors.calls],
            [
                actions.ACTION_DATE_FILL,
                actions.ACTION_XML_CLICKER,
                actions.ACTION_DATE_FILL,
            ],
        )

    def test_date_fill_runs_even_when_final_bill_blocked(self):
        """The operator's decision 2026-10-05: the Hospital No. is the reference.

        DATE FILL must NOT be skipped when the FINAL BILL step of the same
        patient blocks or fails — a note is logged instead, and the row still
        runs. The precondition is ORDER (FINAL BILL first), not a gate.
        """
        executors = RecordingExecutors()
        executors.results[actions.ACTION_FINAL_BILL] = (
            orchestrator.OUTCOME_BLOCKED,
            "no Billing form open",
        )
        items = [
            make_item(actions.ACTION_FINAL_BILL),
            make_item(actions.ACTION_DATE_FILL),
        ]
        report, executors, logs = self.run_plan(items, executors)

        self.assertEqual(
            [call[0] for call in executors.calls],
            [actions.ACTION_FINAL_BILL, actions.ACTION_DATE_FILL],
        )
        self.assertEqual(
            [outcome.status for outcome in report.outcomes],
            [orchestrator.OUTCOME_BLOCKED, orchestrator.OUTCOME_OK],
        )
        # The operator is told out loud why Date Fill still ran.
        notes = [line for line in logs if "note:" in line]
        self.assertEqual(len(notes), 1)
        self.assertIn("FINAL BILL", notes[0])

    def test_no_note_when_date_fill_is_the_only_step_of_its_patient(self):
        """Normal case: Final Bill finished in a past run → nothing to say."""
        items = [
            make_item(actions.ACTION_FINAL_BILL, folder=OTHER_FOLDER),
            make_item(actions.ACTION_DATE_FILL),
        ]
        _report, _executors, logs = self.run_plan(items)
        self.assertEqual([line for line in logs if "note:" in line], [])

    def test_final_bill_step_never_gets_a_note(self):
        items = [
            make_item(actions.ACTION_FINAL_BILL),
            make_item(actions.ACTION_DATE_FILL),
        ]
        _report, _executors, logs = self.run_plan(items)
        for line in logs:
            if "note:" in line:
                self.assertNotIn(
                    orchestrator.hospital_number_from_folder(FOLDER), line
                )
        # exactly one note, and it belongs to the DATE FILL row only
        self.assertEqual(len([l for l in logs if "note:" in l]), 1)

    def test_date_fill_note_is_logged_when_final_bill_is_in_the_plan(self):
        """Advisory only: DATE FILL still RUNS (Hospital No. is the reference)."""
        items = [
            make_item(actions.ACTION_FINAL_BILL),
            make_item(actions.ACTION_DATE_FILL),
        ]
        report, executors, logs = self.run_plan(items)
        self.assertEqual(
            [call[0] for call in executors.calls],
            [actions.ACTION_FINAL_BILL, actions.ACTION_DATE_FILL],
        )
        self.assertTrue(
            any("FINAL BILL ay nasa plan din" in line for line in logs),
            logs,
        )
        date_row = report.outcomes[1]
        self.assertEqual(date_row.status, orchestrator.OUTCOME_OK)
        self.assertIn("FINAL BILL ay nasa plan din", date_row.detail)

    def test_no_note_when_patient_has_only_date_fill(self):
        items = [make_item(actions.ACTION_DATE_FILL)]
        report, _executors, logs = self.run_plan(items)
        self.assertFalse(
            any("FINAL BILL ay nasa plan din" in line for line in logs), logs
        )
        self.assertNotIn("FINAL BILL ay nasa plan din", report.outcomes[0].detail)

    def test_manual_review_is_queued_never_executed(self):
        item = make_item(
            actions.ACTION_MANUAL_REVIEW,
            status=plan_store.STATUS_APPROVED,
            reason="fees MISMATCH",
        )
        report, executors, _ = self.run_plan([item])

        self.assertEqual(executors.calls, [])
        self.assertEqual(report.outcomes[0].status, orchestrator.OUTCOME_QUEUED)
        self.assertIn("fees MISMATCH", report.outcomes[0].detail)
        self.assertIn("para sa tao", report.outcomes[0].detail)

    def test_unapproved_rows_are_skipped(self):
        item = make_item(
            actions.ACTION_XML_CLICKER, status=plan_store.STATUS_SKIPPED
        )
        report, executors, _ = self.run_plan([item])

        self.assertEqual(executors.calls, [])
        self.assertEqual(report.outcomes[0].status, orchestrator.OUTCOME_SKIPPED)

    def test_missing_status_is_never_executed(self):
        item = make_item(actions.ACTION_XML_CLICKER, status="PENDING")
        report, executors, _ = self.run_plan([item])

        self.assertEqual(executors.calls, [])
        self.assertEqual(report.outcomes[0].status, orchestrator.OUTCOME_SKIPPED)

    def test_missing_tool_blocks_row(self):
        item = make_item(actions.ACTION_FINAL_BILL, tool_available=False)
        report, executors, _ = self.run_plan([item])

        self.assertEqual(executors.calls, [])
        self.assertEqual(report.outcomes[0].status, orchestrator.OUTCOME_BLOCKED)
        self.assertIn("tool unavailable", report.outcomes[0].detail)

    def test_unknown_action_blocks_row(self):
        item = make_item("do_something_wild")
        report, executors, _ = self.run_plan([item])

        self.assertEqual(executors.calls, [])
        self.assertEqual(report.outcomes[0].status, orchestrator.OUTCOME_BLOCKED)
        self.assertIn("never guessed", report.outcomes[0].detail)

    def test_folder_without_hospital_number_blocks_row(self):
        item = make_item(actions.ACTION_DATE_FILL, folder=BAD_FOLDER)
        report, executors, _ = self.run_plan([item])

        self.assertEqual(executors.calls, [])
        self.assertEqual(report.outcomes[0].status, orchestrator.OUTCOME_BLOCKED)
        self.assertIn("hospital number", report.outcomes[0].detail)

    def test_executor_exception_marks_failed_and_batch_continues(self):
        executors = RecordingExecutors()
        executors.raise_on[actions.ACTION_DATE_FILL] = RuntimeError("boom")
        items = [
            make_item(actions.ACTION_DATE_FILL),
            make_item(actions.ACTION_XML_CLICKER),
        ]
        report, executors, logs = self.run_plan(items, executors)

        self.assertEqual(
            [outcome.status for outcome in report.outcomes],
            [orchestrator.OUTCOME_FAILED, orchestrator.OUTCOME_OK],
        )
        self.assertIn("RuntimeError: boom", report.outcomes[0].detail)
        self.assertEqual(len(executors.calls), 2)

    def test_system_exit_marks_failed_and_batch_continues(self):
        # A SystemExit (e.g. a step calling sys.exit()/raising SystemExit on a
        # confinement mismatch) must NOT halt the Agent Plan: it becomes a
        # FAILED row and the next item still runs. Mirrors
        # test_executor_exception_marks_failed_and_batch_continues, but for the
        # BaseException-escape that `except Exception` previously let through.
        executors = RecordingExecutors()
        executors.raise_on[actions.ACTION_DATE_FILL] = SystemExit("stop the batch")
        items = [
            make_item(actions.ACTION_DATE_FILL),
            make_item(actions.ACTION_XML_CLICKER),
        ]
        report, executors, logs = self.run_plan(items, executors)

        self.assertEqual(
            [outcome.status for outcome in report.outcomes],
            [orchestrator.OUTCOME_FAILED, orchestrator.OUTCOME_OK],
        )
        self.assertIn("SystemExit: stop the batch", report.outcomes[0].detail)
        self.assertEqual(len(executors.calls), 2, "SystemExit must not halt the batch")
        # The full traceback reaches the run log so the exact mismatch is visible.
        self.assertIn("Traceback", "\n".join(logs))

    def test_executor_blocked_result_passes_through(self):
        executors = RecordingExecutors()
        executors.results[actions.ACTION_XML_CLICKER] = (
            orchestrator.OUTCOME_BLOCKED,
            "not in the ready queue",
        )
        report, executors, _ = self.run_plan(
            [make_item(actions.ACTION_XML_CLICKER)], executors
        )

        self.assertEqual(report.outcomes[0].status, orchestrator.OUTCOME_BLOCKED)
        self.assertEqual(report.outcomes[0].detail, "not in the ready queue")

    def test_log_lines_include_progress_and_summary(self):
        report, _, logs = self.run_plan([make_item(actions.ACTION_DATE_FILL)])

        joined = "\n".join(logs)
        self.assertIn(f"run {actions.ACTION_DATE_FILL}", joined)
        self.assertIn("OK:", joined)
        self.assertIn(report.summary_line, joined)

    def test_one_outcome_per_input_row_including_empty_input(self):
        report, executors, _ = self.run_plan([])
        self.assertEqual(report.outcomes, [])
        self.assertEqual(executors.calls, [])



class ToolLogTests(unittest.TestCase):
    """_run_tool() keeps full per-patient tool logs + clear reasons.

    This is the user-visible fix for "hindi malaman kung saan
    nagkakaproblema": every tool run lands in
    logs/agent_tool_<tool>_<hospital>_<stamp>.log and the row detail says
    which exit, WHY (the tool's own status line), and where to read.
    """

    def write_fake_tool(self, tmp, stdout, code):
        tool = Path(tmp) / "faketoolt.py"
        tool.write_text(
            "import sys\n"
            f"sys.stdout.write({stdout!r})\n"
            f"raise SystemExit({code})\n",
            encoding="utf-8",
        )
        return tool

    def test_failure_keeps_the_reason_and_a_full_log_file(self):
        popup = (
            "[POPUP] Date Fill Stopped: Date Fill stopped before completion. "
            "Reason: Could not select the correct Admission History "
            "confinement period."
        )
        output = (
            "Mode: LIVE Source folder: X Claims: 1 "
            "[LIVE] processing PAT | 123 | ADM 09-18-2026 DIS 09-22-2026 "
            "[LIVE] click Admit History at (435, 58) "
            "[LIVE] Admission History OCR found no date rows.\n" + popup + "\n"
        )
        with TemporaryDirectory() as tmp:
            tool = self.write_fake_tool(tmp, output, 1)
            logs = []
            status, detail = orchestrator._run_tool(
                tool, "123", logs.append, 30, log_dir=tmp
            )

            self.assertEqual(status, orchestrator.OUTCOME_FAILED)
            self.assertIn("exit 1", detail)
            self.assertIn("Could not select the correct Admission History", detail)
            self.assertIn("full output:", detail)
            self.assertTrue(any("tool output saved" in line for line in logs))

            files = list(Path(tmp).glob("agent_tool_faketoolt_123_*.log"))
            self.assertEqual(len(files), 1)
            saved = files[0].read_text(encoding="utf-8")
            self.assertEqual(saved, output)

    def test_success_still_carries_the_status_line(self):
        output = "Mode: LIVE Claims: 1 [LIVE] done [POPUP] done\n"
        with TemporaryDirectory() as tmp:
            tool = self.write_fake_tool(tmp, output, 0)
            status, detail = orchestrator._run_tool(
                tool, "123", lambda message: None, 30, log_dir=tmp
            )

            self.assertEqual(status, orchestrator.OUTCOME_OK)
            self.assertIn("exit 0", detail)
            self.assertIn("done", detail)

    def test_timeout_reports_and_saves_partial_output(self):
        script = "import sys, time\nsys.stdout.write('[LIVE] stuck\\n')\n"
        script += "sys.stdout.flush()\ntime.sleep(60)\n"
        with TemporaryDirectory() as tmp:
            tool = Path(tmp) / "hangtool.py"
            tool.write_text(script, encoding="utf-8")
            logs = []
            status, detail = orchestrator._run_tool(
                tool, "123", logs.append, 1, log_dir=tmp
            )

            self.assertEqual(status, orchestrator.OUTCOME_FAILED)
            self.assertIn("timed out", detail)
            self.assertIn("full output:", detail)
            self.assertTrue(list(Path(tmp).glob("agent_tool_hangtool_123_*.log")))

    def test_tool_reason_prefers_the_stop_record(self):
        # The tool prints a machine-readable [STOP] line naming the stage and
        # the reason; that must win over the tail of stdout.
        output = (
            "Mode: LIVE\n"
            "[LIVE] click Admit History at (435, 58)\n"
            "[STOP] patient=DELA CRUZ | hospital_no=123 | "
            "status=needs_review_admission_history | "
            "reason=Could not select the correct Admission History confinement. | "
            "csv=logs/run.csv\n"
        )
        reason = orchestrator._tool_reason(output)
        self.assertIn("[STOP]", reason)
        self.assertIn("needs_review_admission_history", reason)
        self.assertIn("Could not select the correct Admission History", reason)

    def test_tool_reason_reads_the_popup_reason_block(self):
        # A [POPUP] message is multi-line: only the "Reason:" text is the
        # verdict, and it must survive instead of being cut at the newline.
        output = (
            "Mode: LIVE\n"
            "[POPUP] Date Fill Stopped: Date Fill stopped before completion.\n"
            "Patient: DELA CRUZ, JUAN\n"
            "Hospital No.: 000000000008144\n"
            "Reason:\nCould not select the correct Admission History period.\n"
            "CSV log saved here:\nlogs\\run.csv\n"
            "Please review the current HBSys screen before running Date Fill again.\n"
        )
        reason = orchestrator._tool_reason(output)
        self.assertEqual(
            reason, "Could not select the correct Admission History period."
        )

    def test_tool_reason_falls_back_to_the_last_line(self):
        self.assertEqual(orchestrator._tool_reason("only\n"), "only")
        self.assertEqual(orchestrator._tool_reason(""), "")

    def test_tool_detail_carries_the_last_steps(self):
        output = (
            "[LIVE] type '123'\n"
            "[LIVE] press enter\n"
            "[LIVE] click Admit History at (435, 58)\n"
            "[LIVE] Admission History OCR found no date rows.\n"
            "[STOP] patient=DELA CRUZ | status=needs_review_admission_history\n"
        )
        detail = orchestrator._tool_detail("tool.py", 1, output, "logs/tool.log")
        self.assertIn("reason: [STOP]", detail)
        self.assertIn("last steps:", detail)
        self.assertIn("click Admit History", detail)
        self.assertIn("full output: logs/tool.log", detail)


class FinalBillNoteTests(unittest.TestCase):
    """BLOCKED final_bill rows carry the loader/matcher evidence."""

    def test_blocked_detail_carries_the_loader_note(self):
        loaded = []

        def loader_fn(hospital_no):
            loaded.append(hospital_no)
            return False  # e.g. tooltip mismatch / form survived

        status, detail = orchestrator._default_final_bill(
            FOLDER,
            "123456789012345",
            lambda message: None,
            30,
            forms_fn=lambda: ["Billing (OTHER, PATIENT)", "User Menu"],
            runner_fn=lambda **kwargs: SimpleNamespace(
                success=True, reason="x", final_step="done"
            ),
            loader_fn=loader_fn,
            confinement_fn=lambda admission, discharge: True,
        )
        self.assertEqual(status, orchestrator.OUTCOME_BLOCKED)
        self.assertEqual(loaded, ["123456789012345"])

    def test_default_loader_and_matcher_stream_notes_to_the_log(self):
        from core.agent import final_bill_actions as final_bill

        seen = {}
        forms = ["User Menu"]

        def fake_load(hospital_no, log_fn=None):
            seen["load_log"] = log_fn
            forms.append("Billing (DELA CRUZ, JUAN)")
            return True

        def fake_match(admission, discharge, log_fn=None):
            seen["match_log"] = log_fn
            return True

        real_load = final_bill.load_patient_by_hospital_no
        real_match = final_bill.select_confinement
        final_bill.load_patient_by_hospital_no = fake_load
        final_bill.select_confinement = fake_match
        try:
            logs = []
            status, detail = orchestrator._default_final_bill(
                FOLDER,
                "123456789012345",
                logs.append,
                30,
                forms_fn=lambda: list(forms),
                runner_fn=lambda **kwargs: SimpleNamespace(
                    success=True, reason="final bill committed", final_step="done"
                ),
            )
        finally:
            final_bill.load_patient_by_hospital_no = real_load
            final_bill.select_confinement = real_match

        self.assertEqual(status, orchestrator.OUTCOME_OK)
        self.assertIsNotNone(seen.get("load_log"))
        self.assertIsNotNone(seen.get("match_log"))


class ReportTests(unittest.TestCase):
    def test_counts_and_summary_line(self):
        report = orchestrator.RunReport(
            outcomes=[
                orchestrator.RowOutcome(
                    "A", actions.ACTION_DATE_FILL,
                    status=orchestrator.OUTCOME_OK,
                ),
                orchestrator.RowOutcome(
                    "B", actions.ACTION_MANUAL_REVIEW,
                    status=orchestrator.OUTCOME_QUEUED,
                ),
                orchestrator.RowOutcome(
                    "C", actions.ACTION_XML_CLICKER,
                    status=orchestrator.OUTCOME_FAILED,
                ),
            ]
        )
        counts = report.counts
        self.assertEqual(counts[orchestrator.OUTCOME_OK], 1)
        self.assertEqual(counts[orchestrator.OUTCOME_QUEUED], 1)
        self.assertEqual(counts[orchestrator.OUTCOME_FAILED], 1)
        self.assertEqual(counts[orchestrator.OUTCOME_BLOCKED], 0)
        self.assertIn("3 row(s)", report.summary_line)
        self.assertIn("FAILED 1", report.summary_line)
        self.assertIn("QUEUED 1", report.summary_line)

    def test_save_writes_json_audit_trail(self):
        with TemporaryDirectory() as tmp:
            report = orchestrator.RunReport(
                outcomes=[
                    orchestrator.RowOutcome(
                        FOLDER,
                        actions.ACTION_DATE_FILL,
                        hospital_no="123456789012345",
                        status=orchestrator.OUTCOME_OK,
                        detail="fine",
                    )
                ]
            )
            path = report.save(tmp)

            self.assertEqual(report.saved_path, str(path))
            self.assertTrue(Path(path).name.startswith(orchestrator.RUN_PREFIX))
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"][orchestrator.OUTCOME_OK], 1)
            self.assertEqual(payload["outcomes"][0]["patient_folder"], FOLDER)
            self.assertEqual(
                payload["outcomes"][0]["hospital_no"], "123456789012345"
            )

    def test_run_approved_plan_saves_by_default(self):
        executors = RecordingExecutors()
        with TemporaryDirectory() as tmp:
            report = orchestrator.run_approved_plan(
                [make_item(actions.ACTION_XML_CLICKER)],
                date_fill_fn=executors.make(actions.ACTION_DATE_FILL),
                xml_clicker_fn=executors.make(actions.ACTION_XML_CLICKER),
                final_bill_fn=executors.make(actions.ACTION_FINAL_BILL),
                save=True,
                run_dir=tmp,
                completed_path=Path(tmp) / "agent_completed_actions.json",
            )
            files = list(Path(tmp).glob("agent_run_*.json"))
            self.assertEqual(len(files), 1)
            self.assertEqual(report.saved_path, str(files[0]))

    def make_mixed_report(self):
        return orchestrator.RunReport(
            outcomes=[
                orchestrator.RowOutcome(
                    "A", actions.ACTION_FINAL_BILL,
                    hospital_no="111", status=orchestrator.OUTCOME_OK,
                ),
                orchestrator.RowOutcome(
                    "B", actions.ACTION_FINAL_BILL,
                    hospital_no="222", status=orchestrator.OUTCOME_BLOCKED,
                    detail="Billing form for B is not open",
                ),
                orchestrator.RowOutcome(
                    "C", actions.ACTION_FINAL_BILL,
                    hospital_no="333", status=orchestrator.OUTCOME_FAILED,
                    detail="close_form failed: RuntimeError",
                ),
                orchestrator.RowOutcome(
                    "D", actions.ACTION_MANUAL_REVIEW,
                    status=orchestrator.OUTCOME_QUEUED,
                ),
            ]
        )

    def test_problem_rows_name_only_blocked_and_failed(self):
        report = self.make_mixed_report()
        # OK and QUEUED are finished / human-queued, not "skipped by the run".
        self.assertEqual(
            [outcome.hospital_no for outcome in report.problem_rows],
            ["222", "333"],
        )

    def test_problem_lines_carry_hospital_no_folder_and_reason(self):
        lines = self.make_mixed_report().problem_lines()
        self.assertEqual(len(lines), 2)
        self.assertIn("1. BLOCKED 222", lines[0])
        self.assertIn("[B]", lines[0])
        self.assertIn("Billing form for B is not open", lines[0])
        self.assertIn("2. FAILED 333", lines[1])
        self.assertIn("close_form failed", lines[1])

    def test_problem_lines_fall_back_to_folder_without_hospital_no(self):
        report = orchestrator.RunReport(
            outcomes=[
                orchestrator.RowOutcome(
                    "ADM1", actions.ACTION_FINAL_BILL,
                    status=orchestrator.OUTCOME_BLOCKED, detail="no HBSys",
                )
            ]
        )
        self.assertIn("1. BLOCKED ADM1 [ADM1]", report.problem_lines()[0])

    def test_save_json_lists_the_skipped_patients(self):
        with TemporaryDirectory() as tmp:
            path = self.make_mixed_report().save(tmp)
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            # The counts never said WHICH patients; the problems block does.
            self.assertEqual(payload["problem_count"], 2)
            self.assertEqual(
                [entry["hospital_no"] for entry in payload["problems"]],
                ["222", "333"],
            )
            self.assertEqual(payload["problems"][0]["patient_folder"], "B")
            self.assertIn("close_form failed", payload["problems"][1]["detail"])
            # The full per-row trail is still there unchanged.
            self.assertEqual(len(payload["outcomes"]), 4)

    def test_run_ends_by_listing_the_skipped_patients(self):
        lines = []
        with TemporaryDirectory() as tmp:
            def blocked_fn(hospital_no, folder, is_last=False):
                return orchestrator.OUTCOME_BLOCKED, "B was not loaded"

            orchestrator.run_approved_plan(
                [make_item(actions.ACTION_FINAL_BILL)],
                date_fill_fn=lambda *a, **k: (orchestrator.OUTCOME_OK, "ok"),
                xml_clicker_fn=lambda *a, **k: (orchestrator.OUTCOME_OK, "ok"),
                final_bill_fn=blocked_fn,
                save=False,
                run_dir=tmp,
                log_fn=lines.append,
            )
        joined = "\n".join(lines)
        # Continue-on-error is unchanged (one row still ran), but the run now
        # ENDS by naming the patient instead of only counting it.
        self.assertIn("NOT FINALIZED - 1 patient(s) skipped", joined)
        self.assertIn("1. BLOCKED", joined)
        self.assertIn("B was not loaded", joined)

    def test_clean_run_says_every_patient_was_finalized(self):
        lines = []
        with TemporaryDirectory() as tmp:
            orchestrator.run_approved_plan(
                [make_item(actions.ACTION_XML_CLICKER)],
                date_fill_fn=lambda *a, **k: (orchestrator.OUTCOME_OK, "ok"),
                xml_clicker_fn=lambda *a, **k: (orchestrator.OUTCOME_OK, "ok"),
                final_bill_fn=lambda *a, **k: (orchestrator.OUTCOME_OK, "ok"),
                save=False,
                run_dir=tmp,
                log_fn=lines.append,
            )
        joined = "\n".join(lines)
        self.assertIn("all selected patients were finalized", joined)
        self.assertNotIn("NOT FINALIZED", joined)



class HeartbeatTests(unittest.TestCase):
    """Run heartbeat (2026-09-28): where a run stopped when the app died.

    The audit trail (agent_run_*.json) is written only when a run finishes, so
    a run that ends with the window closing leaves no report at all. The
    heartbeat is rewritten before and after every row and always carries the
    state, so the panel can say which row a dead run stopped on.
    """

    def run_plan(self, items, tmp, **kwargs):
        executors = kwargs.pop("executors", None) or RecordingExecutors()
        # Default executors, overridable per test (setdefault, not overwrite).
        kwargs.setdefault(
            "date_fill_fn", executors.make(actions.ACTION_DATE_FILL)
        )
        kwargs.setdefault(
            "xml_clicker_fn", executors.make(actions.ACTION_XML_CLICKER)
        )
        kwargs.setdefault(
            "final_bill_fn", executors.make(actions.ACTION_FINAL_BILL)
        )
        report = orchestrator.run_approved_plan(
            items,
            run_dir=tmp,
            completed_path=Path(tmp) / "agent_completed_actions.json",
            **kwargs,
        )
        return report, executors

    def state_of(self, tmp):
        return orchestrator.last_run_state(
            Path(tmp) / orchestrator.RUN_STATE_FILE
        )

    def test_finished_heartbeat_after_a_run(self):
        with TemporaryDirectory() as tmp:
            report, _ = self.run_plan(
                [make_item(actions.ACTION_XML_CLICKER)], tmp
            )
            state = self.state_of(tmp)
            self.assertEqual(state["state"], "FINISHED")
            self.assertEqual(state["rows_done"], 1)
            self.assertEqual(state["total_rows"], 1)
            self.assertEqual(state["last_action"], actions.ACTION_XML_CLICKER)
            self.assertEqual(state["last_status"], orchestrator.OUTCOME_OK)
            self.assertEqual(state["last_hospital_no"], "123456789012345")
            self.assertEqual(state["saved_path"], report.saved_path)
            self.assertEqual(state["pid"], os.getpid())
            self.assertIn("updated_at", state)
            self.assertIn("counts", state)

    def test_heartbeat_names_the_row_being_attempted(self):
        seen = {}
        with TemporaryDirectory() as tmp:
            heartbeat = Path(tmp) / orchestrator.RUN_STATE_FILE

            def spy(hospital_no, folder):
                # Runs INSIDE the row: the heartbeat must already point at it.
                seen.update(orchestrator.last_run_state(heartbeat))
                return orchestrator.OUTCOME_BLOCKED, "blocked for the test"

            self.run_plan(
                [make_item(actions.ACTION_DATE_FILL)],
                tmp,
                date_fill_fn=spy,
            )
            self.assertEqual(seen["state"], "RUNNING")
            self.assertEqual(seen["rows_done"], 0)
            self.assertEqual(seen["current_index"], 1)
            self.assertEqual(seen["current_action"], actions.ACTION_DATE_FILL)
            self.assertEqual(seen["current_patient_folder"], FOLDER)
            self.assertEqual(seen["current_hospital_no"], "123456789012345")

    def test_heartbeat_is_rewritten_after_every_row(self):
        progress = []
        with TemporaryDirectory() as tmp:
            original = orchestrator.write_run_heartbeat

            def spy_write(payload, path=None, run_dir=orchestrator.RUN_DIR):
                progress.append((payload.get("state"), payload.get("rows_done")))
                return original(payload, path=path, run_dir=run_dir)

            with mock.patch.object(orchestrator, "write_run_heartbeat",
                                   spy_write):
                self.run_plan(
                    [
                        make_item(actions.ACTION_XML_CLICKER),
                        make_item(actions.ACTION_DATE_FILL,
                                  folder=OTHER_FOLDER),
                    ],
                    tmp,
                )
        # RUNNING before row 1, RUNNING after row 1, RUNNING before row 2,
        # RUNNING after row 2, FINISHED at the end.
        self.assertEqual(
            progress,
            [
                ("RUNNING", 0),
                ("RUNNING", 0),
                ("RUNNING", 1),
                ("RUNNING", 1),
                ("RUNNING", 2),
                ("FINISHED", 2),
            ],
        )

    def test_heartbeat_never_matches_the_audit_trail_glob(self):
        self.assertFalse(
            Path(orchestrator.RUN_STATE_FILE).match("agent_run_*.json")
        )
        with TemporaryDirectory() as tmp:
            self.run_plan([make_item(actions.ACTION_XML_CLICKER)], tmp)
            runs = list(Path(tmp).glob("agent_run_*.json"))
            self.assertEqual(len(runs), 1)  # the heartbeat is not one of them
            self.assertTrue(
                (Path(tmp) / orchestrator.RUN_STATE_FILE).is_file()
            )

    def test_last_run_state_defaults_to_empty(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(self.state_of(tmp), {})
            broken = Path(tmp) / orchestrator.RUN_STATE_FILE
            broken.write_text("{not json", encoding="utf-8")
            self.assertEqual(self.state_of(tmp), {})

    def test_heartbeat_failure_never_breaks_the_run(self):
        with TemporaryDirectory() as tmp:
            report, _ = self.run_plan(
                [make_item(actions.ACTION_XML_CLICKER)],
                tmp,
                state_path=Path(tmp) / "bad:name" / "state.json",
            )
            self.assertEqual(report.counts[orchestrator.OUTCOME_OK], 1)

    def test_heartbeat_is_skipped_cleanly_when_saving_is_disabled(self):
        with TemporaryDirectory() as tmp:
            report, _ = self.run_plan(
                [make_item(actions.ACTION_XML_CLICKER)], tmp, save=False
            )
            self.assertEqual(report.saved_path, "")
            self.assertEqual(self.state_of(tmp)["state"], "FINISHED")


class CompletedLedgerTests(unittest.TestCase):
    """OK rows land in the completion ledger; failures never do.

    save=False never touches the ledger (tests stay hermetic).
    """

    def run_with_ledger(self, items, executors=None, ledger=None, **kwargs):
        executors = executors or RecordingExecutors()
        with TemporaryDirectory() as tmp:
            if ledger is None:
                ledger = Path(tmp) / "agent_completed_actions.json"
            report = orchestrator.run_approved_plan(
                items,
                date_fill_fn=executors.make(actions.ACTION_DATE_FILL),
                xml_clicker_fn=executors.make(actions.ACTION_XML_CLICKER),
                final_bill_fn=executors.make(actions.ACTION_FINAL_BILL),
                save=kwargs.pop("save", True),
                run_dir=Path(tmp) / "runs",
                completed_path=ledger,
                **kwargs,
            )
            completed = plan_store.load_completed_actions(ledger)
        return report, completed

    def test_ok_rows_recorded_in_ledger(self):
        report, completed = self.run_with_ledger(
            [
                make_item(actions.ACTION_XML_CLICKER),
                make_item(actions.ACTION_DATE_FILL, folder=OTHER_FOLDER),
            ]
        )
        self.assertEqual(report.counts[orchestrator.OUTCOME_OK], 2)
        self.assertEqual(
            completed,
            {
                (FOLDER, actions.ACTION_XML_CLICKER),
                (OTHER_FOLDER, actions.ACTION_DATE_FILL),
            },
        )

    def test_failed_rows_never_recorded(self):
        executors = RecordingExecutors()
        executors.results[actions.ACTION_XML_CLICKER] = (
            orchestrator.OUTCOME_FAILED,
            "boom",
        )
        report, completed = self.run_with_ledger(
            [make_item(actions.ACTION_XML_CLICKER)], executors=executors
        )
        self.assertEqual(report.counts[orchestrator.OUTCOME_FAILED], 1)
        self.assertEqual(completed, set())

    def test_save_false_never_touches_ledger(self):
        executors = RecordingExecutors()
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "agent_completed_actions.json"
            orchestrator.run_approved_plan(
                [make_item(actions.ACTION_XML_CLICKER)],
                xml_clicker_fn=executors.make(actions.ACTION_XML_CLICKER),
                save=False,
                completed_path=ledger,
                run_dir=Path(tmp) / "runs",
            )
            self.assertFalse(ledger.exists())


class SharedConfinementRecipeTests(unittest.TestCase):
    """select_confinement_row is the one recipe Date Fill and Final Bill share.

    Date Fill runs it as a module-level script (``cd date_fill_hbsys``), so
    its imports are done lazily here the way _default_final_bill does it —
    the tests only pin the pure contract both callers depend on.
    """

    def _reader(self):
        # Date Fill runs its reader as a plain module-level script
        # (``cd date_fill_hbsys``), its sibling ``hbsys_rules`` imports the
        # same way — mirror that here so the shared recipe is tested exactly
        # as the production tooling executes it.
        import importlib
        import sys

        module_dir_text = str(PROJECT_ROOT / "date_fill_hbsys")
        added = module_dir_text not in sys.path
        if added:
            sys.path.insert(0, module_dir_text)
        try:
            for stale in (
                "hbsys_read_admission_history",
                "shared_admission_reader",
            ):
                sys.modules.pop(stale, None)
            return importlib.import_module("hbsys_read_admission_history")
        finally:
            if added:
                sys.path.remove(module_dir_text)

    def test_grid_key_needs_a_full_mm_dd_yyyy_date(self):
        reader = self._reader()
        self.assertEqual(reader.admission_grid_key("09/05/2026"), "20260905")
        self.assertEqual(reader.admission_grid_key("9/5/2026"), "20260905")
        self.assertEqual(reader.admission_grid_key("O1/0l/2026"), "")
        self.assertEqual(reader.admission_grid_key(""), "")

    def test_dry_run_never_opens_or_reads_the_popup(self):
        reader = self._reader()
        calls: list[str] = []

        def open_popup_fn(purpose: str):
            calls.append(purpose)
            return None

        def read_rows_fn(window):
            raise AssertionError("dry run must not OCR the grid")

        picked = reader.select_confinement_row(
            open_popup_fn=open_popup_fn,
            live=False,
            log_fn=lambda message: None,
            wait_fn=lambda seconds: calls.append(f"wait {seconds}"),
            read_rows_fn=read_rows_fn,
            double_click_fn=lambda x, y: calls.append("click"),
            rate_dialog_fn=lambda: (_ for _ in ()).throw(
                AssertionError("dry run must not touch dialogs")
            ),
            expected_admission="09/05/2026",
            expected_discharge="09/10/2026",
            context="Date Fill",
        )
        self.assertTrue(picked)
        self.assertEqual(calls, ["Admit History"])
    def test_exact_row_is_double_clicked_then_closed_dialog(self):
        reader = self._reader()
        events: list[tuple] = []

        row = reader.ConfinementRow(
            admission_grid="09/05/2026",
            discharge_grid="09/10/2026",
            point=(300, 211),
        )

        picked = reader.select_confinement_row(
            open_popup_fn=lambda purpose: (
                "popup" if purpose == "verify open" else events.append("open")
            ),
            live=True,
            log_fn=lambda message: events.append(("log", message)),
            wait_fn=lambda seconds: events.append(("wait", seconds)),
            read_rows_fn=lambda window: [row],
            double_click_fn=lambda x, y: events.append(("click", x, y)),
            rate_dialog_fn=lambda: events.append("rates") or True,
            expected_admission="09/05/2026",
            expected_discharge="09/10/2026",
            context="Date Fill",
        )
        self.assertTrue(picked)
        self.assertIn(("click", 300, 211), events)
        self.assertIn("rates", events)

    def test_mismatch_never_clicks_and_stops_for_review(self):
        reader = self._reader()
        events: list[tuple] = []

        row = reader.ConfinementRow(
            admission_grid="01/01/2026",
            discharge_grid="01/05/2026",
            point=(300, 238),
        )

        picked = reader.select_confinement_row(
            open_popup_fn=lambda purpose: (
                "popup" if purpose == "verify open" else None
            ),
            live=True,
            log_fn=lambda message: events.append(("log", message)),
            wait_fn=lambda seconds: None,
            read_rows_fn=lambda window: [row],
            double_click_fn=lambda x, y: events.append(("click", x, y)),
            rate_dialog_fn=lambda: True,
            expected_admission="09/05/2026",
            expected_discharge="09/10/2026",
            context="Final Bill",
        )
        self.assertFalse(picked)
        self.assertNotIn(("click", 300, 238), events)
        self.assertTrue(
            any("Only one Admission History row" in text for _, text in events)
        )

    def test_ocr_smear_cannot_match_the_folder_dates(self):
        reader = self._reader()
        events: list[tuple] = []

        row = reader.ConfinementRow(
            admission_grid="O9/05/2026",
            discharge_grid="09/10/2026",
            point=(300, 211),
        )

        picked = reader.select_confinement_row(
            open_popup_fn=lambda purpose: (
                "popup" if purpose == "verify open" else None
            ),
            live=True,
            log_fn=lambda message: events.append(("log", message)),
            wait_fn=lambda seconds: None,
            read_rows_fn=lambda window: [row],
            double_click_fn=lambda x, y: events.append(("click", x, y)),
            rate_dialog_fn=lambda: True,
            expected_admission="09/05/2026",
            expected_discharge="09/10/2026",
            context="Final Bill",
        )
        self.assertFalse(picked)
        self.assertNotIn(("click", 300, 211), events)

    def test_fuzzy_row_is_the_retry_after_exact_miss(self):
        reader = self._reader()
        events: list[tuple] = []

        fuzzy = reader.ConfinementRow(
            admission_grid="09/06/2026",
            discharge_grid="09/10/2026",
            point=(300, 211),
        )

        picked = reader.select_confinement_row(
            open_popup_fn=lambda purpose: (
                "popup" if purpose == "verify open" else None
            ),
            live=True,
            log_fn=lambda message: None,
            wait_fn=lambda seconds: None,
            read_rows_fn=lambda window: [
                reader.ConfinementRow(
                    admission_grid="01/01/2026",
                    discharge_grid="01/05/2026",
                    point=(300, 238),
                )
            ],
            double_click_fn=lambda x, y: events.append(("click", x, y)),
            rate_dialog_fn=lambda: True,
            expected_admission="09/05/2026",
            expected_discharge="09/10/2026",
            fuzzy_rows_fn=lambda: fuzzy,
            context="Date Fill",
        )
        self.assertTrue(picked)
        self.assertEqual(events, [("click", 300, 211)])


class DefaultExecutorTests(unittest.TestCase):
    """The real executors through their injectable hooks — no subprocess."""

    def test_date_fill_blocked_when_patient_not_in_queue(self):
        def run_tool_fn(tool, hospital_no):
            raise AssertionError("subprocess must not launch when unqueued")

        status, detail = orchestrator._default_date_fill(
            "123456789012345",
            FOLDER,
            lambda message: None,
            30,
            queue_fn=lambda: (frozenset(), Path("C:/claims_bot/output")),
            run_tool_fn=run_tool_fn,
        )
        self.assertEqual(status, orchestrator.OUTCOME_BLOCKED)
        self.assertIn("ready queue", detail)

    def test_date_fill_runs_tool_when_queued(self):
        seen = {}

        def run_tool_fn(tool, hospital_no):
            seen["tool"] = tool
            seen["hospital_no"] = hospital_no
            return orchestrator.OUTCOME_OK, "processed 1 claim"

        status, detail = orchestrator._default_date_fill(
            "123456789012345",
            FOLDER,
            lambda message: None,
            30,
            queue_fn=lambda: (frozenset({"123456789012345"}), Path("X")),
            run_tool_fn=run_tool_fn,
        )
        self.assertEqual(status, orchestrator.OUTCOME_OK)
        self.assertEqual(seen["tool"], orchestrator.DATE_FILL_TOOL)
        self.assertEqual(seen["hospital_no"], "123456789012345")
        self.assertIn("processed", detail)

    def test_date_fill_empty_queue_marker_blocks(self):
        status, detail = orchestrator._default_date_fill(
            "123456789012345",
            FOLDER,
            lambda message: None,
            30,
            queue_fn=lambda: (frozenset({"123456789012345"}), Path("X")),
            run_tool_fn=lambda tool, hospital_no: (
                orchestrator.OUTCOME_OK,
                "No claims to process in source folder:\nX",
            ),
        )
        self.assertEqual(status, orchestrator.OUTCOME_BLOCKED)
        self.assertIn("re-stage", detail)

    def test_date_fill_tool_failure_passes_through(self):
        status, detail = orchestrator._default_date_fill(
            "123456789012345",
            FOLDER,
            lambda message: None,
            30,
            queue_fn=lambda: (frozenset({"123456789012345"}), Path("X")),
            run_tool_fn=lambda tool, hospital_no: (
                orchestrator.OUTCOME_FAILED,
                "hbsys_fill_dates.py exit 1: stopped for review",
            ),
        )
        self.assertEqual(status, orchestrator.OUTCOME_FAILED)
        self.assertIn("stopped for review", detail)

    def test_xml_clicker_blocked_when_patient_not_in_queue(self):
        def run_tool_fn(tool, hospital_no):
            raise AssertionError("subprocess must not launch when unqueued")

        status, detail = orchestrator._default_xml_clicker(
            "000000000021401",
            OTHER_FOLDER,
            lambda message: None,
            30,
            queue_fn=lambda: (frozenset(), Path("C:/claims_bot/output")),
            run_tool_fn=run_tool_fn,
        )
        self.assertEqual(status, orchestrator.OUTCOME_BLOCKED)
        self.assertIn("ready queue", detail)

    def test_xml_clicker_empty_queue_marker_blocks(self):
        status, detail = orchestrator._default_xml_clicker(
            "000000000021401",
            OTHER_FOLDER,
            lambda message: None,
            30,
            queue_fn=lambda: (frozenset({"000000000021401"}), Path("X")),
            run_tool_fn=lambda tool, hospital_no: (
                orchestrator.OUTCOME_OK,
                "No output folders to process.",
            ),
        )
        self.assertEqual(status, orchestrator.OUTCOME_BLOCKED)
        self.assertIn("re-stage", detail)

    def test_final_bill_blocked_without_open_billing_form(self):
        def runner_fn(**kwargs):
            raise AssertionError("runner must not start when no form is open")

        status, detail = orchestrator._default_final_bill(
            FOLDER,
            "123456789012345",
            lambda message: None,
            30,
            forms_fn=lambda: ["User Menu"],
            runner_fn=runner_fn,
            loader_fn=lambda hospital_no: False,
            confinement_fn=lambda admission, discharge: True,
        )
        self.assertEqual(status, orchestrator.OUTCOME_BLOCKED)
        self.assertIn("no Billing form open", detail)

    def test_final_bill_attempts_auto_load_when_different_or_no_patient(self):
        loaded_hosp = []
        picked_periods = []
        forms_state = [["Billing (OTHER, PATIENT)", "User Menu"]]

        def fake_loader(hosp):
            loaded_hosp.append(hosp)
            forms_state[0] = ["Billing (DELA CRUZ, JUAN)", "User Menu"]
            return True

        def fake_confinement(admission, discharge):
            picked_periods.append((admission, discharge))
            return True

        def runner_fn(**kwargs):
            return SimpleNamespace(
                success=True,
                reason="Final Bill committed; form closed",
                final_step="done",
            )

        status, detail = orchestrator._default_final_bill(
            FOLDER,
            "123456789012345",
            lambda message: None,
            30,
            forms_fn=lambda: forms_state[0],
            runner_fn=runner_fn,
            loader_fn=fake_loader,
            confinement_fn=fake_confinement,
        )
        self.assertEqual(status, orchestrator.OUTCOME_OK)
        self.assertEqual(loaded_hosp, ["123456789012345"])
        self.assertEqual(picked_periods, [("20260901", "20260903")])
        self.assertIn("committed", detail)

    def test_final_bill_does_not_reload_when_the_form_is_already_open(self):
        # The trailing space HBSys adds for a missing middle name must not make
        # the row reload this same patient over its own open form.
        loaded_hosp = []
        runner_called = []

        def fake_loader(hospital_no):
            loaded_hosp.append(hospital_no)
            return True

        def runner_fn(**kwargs):
            runner_called.append(True)
            return SimpleNamespace(
                success=True,
                reason="Final Bill committed",
                final_step="done",
            )

        status, _detail = orchestrator._default_final_bill(
            FOLDER,
            "123456789012345",
            lambda message: None,
            30,
            forms_fn=lambda: ["Billing (DELA CRUZ, JUAN )", "User Menu"],
            runner_fn=runner_fn,
            loader_fn=fake_loader,
            confinement_fn=lambda admission, discharge: True,
        )
        self.assertEqual(status, orchestrator.OUTCOME_OK)
        self.assertEqual(loaded_hosp, [])
        self.assertEqual(len(runner_called), 1)

    def test_final_bill_reloads_when_always_reload_is_on(self):
        # Slice H: the operator's rule is "type the Hospital No. again before
        # every step" — the Hospital No. is the reference. Default off, so the
        # test above pins the old behaviour; this pins the opt-in.
        loaded_hosp = []

        def fake_loader(hospital_no):
            loaded_hosp.append(hospital_no)
            return True

        status, _detail = orchestrator._default_final_bill(
            FOLDER,
            "123456789012345",
            lambda message: None,
            30,
            forms_fn=lambda: ["Billing (DELA CRUZ, JUAN )", "User Menu"],
            runner_fn=lambda **kwargs: SimpleNamespace(
                success=True, reason="Final Bill committed", final_step="done"
            ),
            loader_fn=fake_loader,
            confinement_fn=lambda admission, discharge: True,
            always_reload=True,
        )
        self.assertEqual(status, orchestrator.OUTCOME_OK)
        self.assertEqual(loaded_hosp, ["123456789012345"])

    def test_always_reload_uses_relink_verification(self):
        # The real loader (not a stub) must be called with relink mode and this
        # patient's Billing title, so re-typing the same Hospital No. counts.
        from core.agent import final_bill_actions as final_bill

        seen = {}

        def fake_load(hospital_no, log_fn=None, verify_mode=None, expect_title=""):
            seen["verify_mode"] = verify_mode
            seen["expect_title"] = expect_title
            return True

        real_load = final_bill.load_patient_by_hospital_no
        final_bill.load_patient_by_hospital_no = fake_load
        try:
            status, _detail = orchestrator._default_final_bill(
                FOLDER,
                "123456789012345",
                lambda message: None,
                30,
                forms_fn=lambda: ["Billing (DELA CRUZ, JUAN )", "User Menu"],
                runner_fn=lambda **kwargs: SimpleNamespace(
                    success=True, reason="ok", final_step="done"
                ),
                confinement_fn=lambda admission, discharge: True,
                always_reload=True,
            )
        finally:
            final_bill.load_patient_by_hospital_no = real_load

        self.assertEqual(status, orchestrator.OUTCOME_OK)
        self.assertEqual(seen["verify_mode"], final_bill.LOAD_VERIFY_RELINK)
        # The title comes from billing_form_for_patient(folder name); the
        # trailing space HBSys shows for a missing middle name is normalized
        # away by billing_title_key inside the loader, so the raw folder-derived
        # title is exactly what belongs here.
        self.assertEqual(
            seen["expect_title"],
            final_bill.billing_form_for_patient(
                orchestrator.patient_name_from_folder(FOLDER)
            ),
        )
        self.assertEqual(
            final_bill.billing_title_key(seen["expect_title"]),
            final_bill.billing_title_key("Billing (DELA CRUZ, JUAN )"),
        )

    def test_default_path_keeps_the_original_loader_call_signature(self):
        # Regression: with always_reload off the loader is called WITHOUT the
        # new kwargs, so existing injectors/doubles keep working unchanged.
        from core.agent import final_bill_actions as final_bill

        seen = {}

        def fake_load(hospital_no, log_fn=None):
            seen["called"] = hospital_no
            forms_state[0] = ["Billing (DELA CRUZ, JUAN )", "User Menu"]
            return True

        forms_state = [["User Menu"]]
        real_load = final_bill.load_patient_by_hospital_no
        final_bill.load_patient_by_hospital_no = fake_load
        try:
            status, _detail = orchestrator._default_final_bill(
                FOLDER,
                "123456789012345",
                lambda message: None,
                30,
                forms_fn=lambda: forms_state[0],
                runner_fn=lambda **kwargs: SimpleNamespace(
                    success=True, reason="ok", final_step="done"
                ),
                confinement_fn=lambda admission, discharge: True,
            )
        finally:
            final_bill.load_patient_by_hospital_no = real_load

        self.assertEqual(status, orchestrator.OUTCOME_OK)
        self.assertEqual(seen["called"], "123456789012345")

    def test_final_bill_blocked_when_confinement_not_in_admit_history(self):
        runner_called = []

        def runner_fn(**kwargs):
            runner_called.append(True)
            return SimpleNamespace(success=True, reason="", final_step="done")

        status, detail = orchestrator._default_final_bill(
            FOLDER,
            "123456789012345",
            lambda message: None,
            30,
            forms_fn=lambda: ["Billing (DELA CRUZ, JUAN)"],
            runner_fn=runner_fn,
            confinement_fn=lambda admission, discharge: False,
        )
        self.assertEqual(status, orchestrator.OUTCOME_BLOCKED)
        self.assertIn("20260901-20260903", detail)
        self.assertIn("Admit History", detail)
        self.assertEqual(runner_called, [])

    def test_final_bill_blocked_when_confinement_selection_raises(self):
        def boom(admission, discharge):
            raise RuntimeError("layout changed, refusing to click")

        status, detail = orchestrator._default_final_bill(
            FOLDER,
            "123456789012345",
            lambda message: None,
            30,
            forms_fn=lambda: ["Billing (DELA CRUZ, JUAN)"],
            runner_fn=lambda **kwargs: SimpleNamespace(success=True),
            confinement_fn=boom,
        )
        self.assertEqual(status, orchestrator.OUTCOME_BLOCKED)
        self.assertIn("confinement selection failed", detail)
        self.assertIn("layout changed", detail)

    def test_confinement_is_never_selected_when_the_form_is_not_ready(self):
        picked = []

        def runner_fn(**kwargs):
            raise AssertionError("runner must not start when blocked")

        status, _ = orchestrator._default_final_bill(
            FOLDER,
            "123456789012345",
            lambda message: None,
            30,
            forms_fn=lambda: ["User Menu"],
            runner_fn=runner_fn,
            loader_fn=lambda hospital_no: False,
            confinement_fn=lambda admission, discharge: picked.append(
                (admission, discharge)
            ),
        )
        self.assertEqual(status, orchestrator.OUTCOME_BLOCKED)
        self.assertEqual(picked, [])

    def test_confinement_dates_from_folder(self):
        self.assertEqual(
            orchestrator.confinement_dates_from_folder(FOLDER),
            ("20260901", "20260903"),
        )
        self.assertEqual(
            orchestrator.confinement_dates_from_folder("DELA CRUZ, JUAN - 123"),
            ("", ""),
        )


    def test_final_bill_runs_for_open_patient(self):
        seen = {}

        def runner_fn(**kwargs):
            seen["forms_open"] = kwargs["forms_open_fn"]()
            seen["log_fn"] = kwargs["log_fn"]
            return SimpleNamespace(
                success=True,
                reason="Final Bill committed; form closed",
                final_step="done",
            )

        status, detail = orchestrator._default_final_bill(
            FOLDER,
            "123456789012345",
            lambda message: None,
            30,
            forms_fn=lambda: ["Billing (DELA CRUZ, JUAN)", "User Menu"],
            runner_fn=runner_fn,
            confinement_fn=lambda admission, discharge: True,
        )
        self.assertEqual(status, orchestrator.OUTCOME_OK)
        self.assertTrue(seen["forms_open"])
        self.assertIn("committed", detail)
        self.assertIsNotNone(seen["log_fn"])

    def test_final_bill_runner_blocked_maps_to_blocked(self):
        def runner_fn(**kwargs):
            return SimpleNamespace(
                success=False,
                reason="screen X is not part of the Final Bill flow",
                final_step="blocked",
            )

        status, detail = orchestrator._default_final_bill(
            FOLDER,
            "123456789012345",
            lambda message: None,
            30,
            forms_fn=lambda: ["Billing (DELA CRUZ, JUAN)"],
            runner_fn=runner_fn,
            confinement_fn=lambda admission, discharge: True,
        )
        self.assertEqual(status, orchestrator.OUTCOME_BLOCKED)
        self.assertIn("not part of the Final Bill flow", detail)

    def test_final_bill_runner_failure_maps_to_failed(self):
        def runner_fn(**kwargs):
            return SimpleNamespace(
                success=False,
                reason="click_ok_fn failed: popup vanished",
                final_step="click_ok",
            )

        status, detail = orchestrator._default_final_bill(
            FOLDER,
            "123456789012345",
            lambda message: None,
            30,
            forms_fn=lambda: ["Billing (DELA CRUZ, JUAN)"],
            runner_fn=runner_fn,
            confinement_fn=lambda admission, discharge: True,
        )
        self.assertEqual(status, orchestrator.OUTCOME_FAILED)
        self.assertIn("popup vanished", detail)

    def test_failed_final_bill_row_keeps_screen_evidence(self):
        # A stopped row must say WHICH prompt was on screen, so the operator
        # can tell "the prompt never appeared" from "it is up and the click
        # missed it" - 2026-09-29 12:43 had neither in the run report.
        def runner_fn(**kwargs):
            return SimpleNamespace(
                success=False,
                reason=(
                    "confirm_no ran 3 times in a row on screen save_prompt "
                    "without changing it | steps: confirm_no x3"
                ),
                final_step="confirm_no",
            )

        # capture whatever the module hygiene stubs replaced, then restore
        # THOSE (never the real functions) when the test is done.
        stub_shot = final_bill.save_screenshot
        stub_diagnose = final_bill.diagnose_screen
        real_windows = final_bill.hbsys_window_titles
        final_bill.hbsys_window_titles = lambda: [
            "File save [#32770]",
            "Call Administrator [#32770]",
        ]
        final_bill.save_screenshot = (
            lambda prefix="agent_diag": "logs\\final_bill_stopped_test.png"
        )
        try:
            status, detail = orchestrator._default_final_bill(
                FOLDER,
                "123456789012345",
                lambda message: None,
                30,
                forms_fn=lambda: ["Billing (DELA CRUZ, JUAN)"],
                runner_fn=runner_fn,
                confinement_fn=lambda admission, discharge: True,
            )
        finally:
            final_bill.save_screenshot = stub_shot
            final_bill.diagnose_screen = stub_diagnose
            final_bill.hbsys_window_titles = real_windows

        self.assertEqual(status, orchestrator.OUTCOME_FAILED)
        self.assertIn("confirm_no x3", detail)
        self.assertIn("File save [#32770]", detail)
        self.assertIn("Call Administrator [#32770]", detail)
        self.assertIn("final_bill_stopped_test.png", detail)


class FakeBillingSession:
    """One HBSys session across several patients.

    Mirrors the real mechanics the Final Bill executor relies on: a verified
    load (type the hospital number and open the new form, closing nothing) and
    a runner that leaves the Billing form open on screen.
    """

    def __init__(self, names):
        self.names = names                  # hospital_no -> patient name
        self.forms = ["User Menu"]          # MDI child titles
        self.loaded = []                    # hospital numbers typed
        self.replaced = []                  # forms the loader retyped over
        self.stale_closes = []              # forms closed while loading (never)
        self.runner_closes = []             # forms closed by the runner (never)
        self.runs = []                      # (form title, forms_open) per run
        self.picked = []                    # (admission, discharge) selected

    def forms_fn(self):
        return list(self.forms)

    def loader(self, hospital_no):
        # The loader never closes anything: it types the hospital number and
        # HBSys retypes the Billing form it already had open.
        for title in [t for t in self.forms if t.startswith("Billing (")]:
            self.forms.remove(title)
            self.replaced.append(title)
        self.loaded.append(hospital_no)
        self.forms.append(f"Billing ({self.names[hospital_no]})")
        return True

    def confinement(self, admission, discharge):
        self.picked.append((admission, discharge))
        return True

    def runner(self, **kwargs):
        open_forms = [t for t in self.forms if t.startswith("Billing (")]
        title = open_forms[0] if open_forms else ""
        self.runs.append((title, kwargs["forms_open_fn"]()))
        # The Billing form stays open after the post-OK prompt.
        return SimpleNamespace(
            success=True,
            reason="Final Bill committed; form left open",
            final_step="done",
        )


class MultiPatientFinalBillTests(unittest.TestCase):
    """Consecutive final_bill rows in one HBSys session, nothing guessed."""

    def setUp(self):
        self.session = FakeBillingSession(
            {"123456789012345": "DELA CRUZ, JUAN", "000000000021401": "SANTOS, MARIA"}
        )

    def run_row(self, folder, logs=None):
        return orchestrator._default_final_bill(
            folder,
            orchestrator.hospital_number_from_folder(folder),
            (logs if logs is not None else []).append,
            30,
            forms_fn=self.session.forms_fn,
            runner_fn=self.session.runner,
            loader_fn=self.session.loader,
            confinement_fn=self.session.confinement,
        )

    def test_second_patient_is_loaded_after_the_first_form_stays_open(self):
        status_one, _ = self.run_row(FOLDER)
        status_two, _ = self.run_row(OTHER_FOLDER)

        self.assertEqual(status_one, orchestrator.OUTCOME_OK)
        self.assertEqual(status_two, orchestrator.OUTCOME_OK)
        # Both patients went through the verified Hospital No. lookup, in plan
        # order, and the runner only ever ran against the right patient's form.
        self.assertEqual(
            self.session.loaded, ["123456789012345", "000000000021401"]
        )
        self.assertEqual(
            self.session.runs,
            [("Billing (DELA CRUZ, JUAN)", True), ("Billing (SANTOS, MARIA)", True)],
        )
        # Nothing is ever closed: the loader types over the open form and the
        # runner leaves it open after OK/No.
        self.assertEqual(self.session.stale_closes, [])
        self.assertEqual(self.session.runner_closes, [])
        self.assertEqual(
            self.session.replaced, ["Billing (DELA CRUZ, JUAN)"],
        )
        self.assertEqual(
            self.session.picked,
            [("20260901", "20260903"), ("20260906", "20260912")],
        )

    def test_stale_form_from_another_patient_is_retyped_not_closed(self):
        # Crash/resume case: the previous patient's form is still open. The
        # loader must type over it (never close it) and then bill this patient.
        self.session.forms = ["Billing (SANTOS, MARIA)", "User Menu"]
        logs = []

        status, _ = self.run_row(FOLDER, logs)

        self.assertEqual(status, orchestrator.OUTCOME_OK)
        self.assertEqual(self.session.stale_closes, [])
        self.assertEqual(self.session.replaced, ["Billing (SANTOS, MARIA)"])
        self.assertEqual(self.session.loaded, ["123456789012345"])
        self.assertEqual(
            self.session.runs, [("Billing (DELA CRUZ, JUAN)", True)],
        )
        self.assertTrue(any("loading patient in Billing form" in line for line in logs))

    def test_same_patient_row_reuses_the_form_the_tools_left_open(self):
        # Date Fill for this patient just ran, so the Billing form is open and
        # the Final Bill row must NOT re-type the hospital number.
        self.session.forms = ["Billing (DELA CRUZ, JUAN)", "User Menu"]

        status, _ = self.run_row(FOLDER)

        self.assertEqual(status, orchestrator.OUTCOME_OK)
        self.assertEqual(self.session.loaded, [])
        self.assertEqual(self.session.stale_closes, [])
        self.assertEqual(self.session.picked, [("20260901", "20260903")])

    def test_batch_runs_two_final_bill_rows_in_one_session(self):
        items = [
            make_item(actions.ACTION_FINAL_BILL),
            make_item(actions.ACTION_FINAL_BILL, folder=OTHER_FOLDER),
        ]
        logs = []
        report = orchestrator.run_approved_plan(
            items,
            final_bill_fn=lambda hospital_no, folder: self.run_row(
                folder, logs
            ),
            log_fn=logs.append,
            save=False,
            run_dir=self.enterContext(TemporaryDirectory()),
        )

        self.assertEqual(report.counts[orchestrator.OUTCOME_OK], 2)
        self.assertEqual(
            self.session.loaded, ["123456789012345", "000000000021401"]
        )
        # Two rows, two patients, and nothing closed in between.
        self.assertEqual(self.session.runner_closes, [])
        self.assertEqual(self.session.stale_closes, [])
        self.assertIn("run final_bill: " + FOLDER, logs)


class StepChainOrderTests(unittest.TestCase):
    """Slice H: one patient's FINAL BILL runs before its DATE FILL."""

    def setUp(self):
        self.calls = []
        self.run_dir = self.enterContext(TemporaryDirectory())

    def _run(self, items, **kwargs):
        def recorder(action):
            def executor(hospital_no, folder):
                self.calls.append((action, folder))
                return orchestrator.OUTCOME_OK, "done"
            return executor
        return orchestrator.run_approved_plan(
            items,
            date_fill_fn=recorder(actions.ACTION_DATE_FILL),
            final_bill_fn=recorder(actions.ACTION_FINAL_BILL),
            xml_clicker_fn=recorder(actions.ACTION_XML_CLICKER),
            log_fn=lambda message: None,
            save=False,
            run_dir=self.run_dir,
            **kwargs,
        )

    def test_final_bill_runs_before_the_same_patient_date_fill(self):
        items = [
            make_item(actions.ACTION_DATE_FILL),
            make_item(actions.ACTION_FINAL_BILL),
        ]
        self._run(items)

        self.assertEqual(
            self.calls,
            [
                (actions.ACTION_FINAL_BILL, FOLDER),
                (actions.ACTION_DATE_FILL, FOLDER),
            ],
        )

    def test_order_steps_false_keeps_the_raw_plan_order(self):
        items = [
            make_item(actions.ACTION_DATE_FILL),
            make_item(actions.ACTION_FINAL_BILL),
        ]
        self._run(items, order_steps=False)

        self.assertEqual(
            self.calls,
            [
                (actions.ACTION_DATE_FILL, FOLDER),
                (actions.ACTION_FINAL_BILL, FOLDER),
            ],
        )

    def test_patient_groups_are_not_split_by_another_patient(self):
        items = [
            make_item(actions.ACTION_DATE_FILL),
            make_item(actions.ACTION_FINAL_BILL, folder=OTHER_FOLDER),
            make_item(actions.ACTION_FINAL_BILL),
        ]
        self._run(items)

        self.assertEqual(
            self.calls,
            [
                (actions.ACTION_FINAL_BILL, FOLDER),
                (actions.ACTION_DATE_FILL, FOLDER),
                (actions.ACTION_FINAL_BILL, OTHER_FOLDER),
            ],
        )

    def test_single_step_plan_keeps_its_original_order(self):
        """Regression: nothing to chain means nothing to reorder."""
        items = [
            make_item(actions.ACTION_DATE_FILL, folder=FOLDER),
            make_item(actions.ACTION_XML_CLICKER, folder=OTHER_FOLDER),
            make_item(actions.ACTION_DATE_FILL, folder=THIRD_FOLDER),
        ]
        self._run(items)

        self.assertEqual(
            self.calls,
            [
                (actions.ACTION_DATE_FILL, FOLDER),
                (actions.ACTION_XML_CLICKER, OTHER_FOLDER),
                (actions.ACTION_DATE_FILL, THIRD_FOLDER),
            ],
        )

    def test_date_fill_note_is_advisory_and_never_blocks(self):
        # FINAL BILL for this patient is also in the plan. The note must appear
        # in the log and in the row detail, but the row still executes.
        items = [
            make_item(actions.ACTION_DATE_FILL),
            make_item(actions.ACTION_FINAL_BILL),
        ]
        logs = []

        def executor(hospital_no, folder):
            self.calls.append((actions.ACTION_DATE_FILL, folder))
            return orchestrator.OUTCOME_OK, "dates filled"
        report = orchestrator.run_approved_plan(
            items,
            date_fill_fn=executor,
            final_bill_fn=lambda hospital_no, folder: (
                orchestrator.OUTCOME_OK, "billed"
            ),
            log_fn=logs.append,
            save=False,
            run_dir=self.run_dir,
        )

        self.assertEqual(report.counts[orchestrator.OUTCOME_OK], 2)
        self.assertEqual(len(self.calls), 1)   # date_fill really ran
        note_lines = [line for line in logs if line.strip().startswith("note:")]
        self.assertTrue(note_lines, "the advisory note must be logged")
        self.assertIn("FINAL BILL", note_lines[0])
        date_fill_row = next(
            outcome for outcome in report.outcomes
            if outcome.action == actions.ACTION_DATE_FILL
        )
        self.assertEqual(date_fill_row.status, orchestrator.OUTCOME_OK)
        self.assertIn("FINAL BILL", date_fill_row.detail)
        self.assertIn("dates filled", date_fill_row.detail)

    def test_no_note_when_the_patient_has_no_final_bill_row(self):
        items = [make_item(actions.ACTION_DATE_FILL)]
        logs = []
        self._run(items)
        report = orchestrator.run_approved_plan(
            items,
            date_fill_fn=lambda hospital_no, folder: (
                orchestrator.OUTCOME_OK, "dates filled"
            ),
            log_fn=logs.append,
            save=False,
            run_dir=self.run_dir,
        )
        self.assertEqual(report.outcomes[0].detail, "dates filled")


if __name__ == "__main__":
    unittest.main()