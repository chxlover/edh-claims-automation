"""Tests for per-node workflow conditions (Phase 3, 2026-10-12).

Pure evaluator + engine integration, both hermetic:
    * no subprocess (ScriptNodeAdapter is patched),
    * no HBSys (`_hbsys_available` stubbed),
    * no real Fees CSV / READY folder (the context is injected or the
      reader is patched).

Run from the project root:
    python -m unittest tests.test_workflow_conditions
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import workflow_conditions as wc  # noqa: E402
from core import workflow_engine as we  # noqa: E402
from core.workflow_conditions import (  # noqa: E402
    CONDITION_ALWAYS,
    CONDITION_CHECKER_READY,
    CONDITION_FEES_ALLOWS_XML,
    CONDITION_FEES_READY_XML,
    CONDITION_FEES_SAYS_DATE_FILL,
    CONDITION_FEES_SAYS_FINAL_BILL,
    evaluate,
    is_known,
    node_condition,
)
from core.workflow_engine import (  # noqa: E402
    NODE_STATUS_DONE,
    NODE_STATUS_SKIPPED,
    WorkflowConfig,
    WorkflowEngine,
    NodeInstance,
    default_workflow,
)

# A fees row that is fully ready for XML (the decide_action happy path).
ROW_READY = {
    "Patient Folder": "A - 000000000000001 - ADM20260101_DIS20260103",
    "Status": "MATCH",
    "Adm Match": "YES",
    "Dis Match": "YES",
    "Prof Fee Sign Date (hprofserv.pdoctorsigndate)": "2026/09/05",
    "Consent Date (hpatcon1.consentdate)": "2026/09/05",
    "Auth Sign Date (hpatcon1.authsigndate)": "2026/09/05",
    "Ready to Generate XML": "YES",
}
# Same row but the consent date is blank -> Date Fill is needed.
ROW_NO_DATES = dict(ROW_READY, **{"Consent Date (hpatcon1.consentdate)": ""})
# Bill not finalized -> Final Bill is needed.
ROW_NO_BILL = dict(ROW_READY, Status="NO FINAL BILL")
# Same but with its own folder and a blank consent date -> needs
# Date Fill AND Final Bill (operator rule 2026-10-13).
ROW_NO_BILL_NO_DATES = dict(
    ROW_READY,
    Status="NO FINAL BILL",
    **{"Patient Folder":
       "C - 000000000000003 - ADM20260101_DIS20260103",
       "Consent Date (hpatcon1.consentdate)": ""},
)
# Charges differ but itemized > 0 -> the bill IS already finalized.
ROW_MISMATCH = dict(
    ROW_READY,
    Status="MISMATCH",
    **{"Patient Folder":
       "B - 000000000000002 - ADM20260101_DIS20260103"},
)
# Fees row that errored: the real checker leaves the readiness column
# blank for these, so it must never reach XML.
ROW_ERROR = dict(ROW_READY, Status="ERROR",
                 **{"Ready to Generate XML": ""})
ROW_NO_RECORD = dict(ROW_READY, Status="NO RECORD",
                     **{"Ready to Generate XML": ""})


def _node(node_id, node_type, order, **params):
    return NodeInstance(id=node_id, node_type=node_type, order=order,
                        params=dict(params))


class ConditionEvaluatorTests(unittest.TestCase):
    """The pure evaluator."""

    def test_always_runs_without_context(self):
        self.assertEqual(evaluate(CONDITION_ALWAYS), (True, ""))
        self.assertEqual(evaluate(""), (True, ""))
        self.assertEqual(evaluate(None), (True, ""))

    def test_unknown_condition_skips_and_never_guesses(self):
        ok, reason = evaluate("typo_condition")
        self.assertFalse(ok)
        self.assertIn("never guessed", reason)
        self.assertFalse(is_known("typo_condition"))
        for known in wc.ALL_CONDITIONS:
            self.assertTrue(is_known(known), known)

    def test_fees_condition_without_fees_rows_skips(self):
        for condition in (CONDITION_FEES_SAYS_DATE_FILL,
                          CONDITION_FEES_SAYS_FINAL_BILL,
                          CONDITION_FEES_READY_XML):
            ok, reason = evaluate(condition, {})
            self.assertFalse(ok, condition)
            self.assertIn("no Fees Check result", reason)

    def test_date_fill_condition_follows_existing_fees_decision(self):
        # Delegates to core.agent.fees_actions.decide_action — no new rules.
        ok, _ = evaluate(CONDITION_FEES_SAYS_DATE_FILL,
                         {"fees_rows": [ROW_NO_DATES]})
        self.assertTrue(ok)
        ok, reason = evaluate(CONDITION_FEES_SAYS_DATE_FILL,
                              {"fees_rows": [ROW_READY]})
        self.assertFalse(ok)
        self.assertIn("no patient needs Date Fill", reason)

    def test_final_bill_condition_follows_no_final_bill_status(self):
        ok, _ = evaluate(CONDITION_FEES_SAYS_FINAL_BILL,
                         {"fees_rows": [ROW_NO_BILL]})
        self.assertTrue(ok)
        ok, _ = evaluate(CONDITION_FEES_SAYS_FINAL_BILL,
                         {"fees_rows": [ROW_READY]})
        self.assertFalse(ok)

    def test_xml_condition_requires_ready_verdict(self):
        ok, _ = evaluate(CONDITION_FEES_READY_XML, {"fees_rows": [ROW_READY]})
        self.assertTrue(ok)
        ok, reason = evaluate(CONDITION_FEES_READY_XML,
                              {"fees_rows": [ROW_NO_BILL]})
        self.assertFalse(ok)
        self.assertIn("no patient is ready for XML", reason)

    def test_checker_condition_uses_ready_folders(self):
        ok, _ = evaluate(CONDITION_CHECKER_READY, {"ready_folders": ["A"]})
        self.assertTrue(ok)
        ok, reason = evaluate(CONDITION_CHECKER_READY, {"ready_folders": []})
        self.assertFalse(ok)
        self.assertIn("no READY folders", reason)

    def test_checker_condition_does_not_require_fees_rows(self):
        # Regression: an early draft checked fees rows first and wrongly
        # skipped this condition.
        ok, _ = evaluate(CONDITION_CHECKER_READY,
                         {"ready_folders": ["A"], "fees_rows": []})
        self.assertTrue(ok)

    def test_mixed_batch_runs_node_when_any_patient_needs_it(self):
        context = {"fees_rows": [ROW_READY, ROW_NO_DATES]}
        ok, _ = evaluate(CONDITION_FEES_SAYS_DATE_FILL, context)
        self.assertTrue(ok)

    def test_malformed_row_is_skipped_not_guessed(self):
        context = {"fees_rows": [{"Status": "MATCH"}, "not-a-row", None]}
        ok, reason = evaluate(CONDITION_FEES_SAYS_DATE_FILL, context)
        self.assertFalse(ok)
        self.assertIsInstance(reason, str)

    def test_node_condition_reads_params_when(self):
        self.assertEqual(
            node_condition(_node("a", "copy_xml", 1, when="fees_ready_xml")),
            "fees_ready_xml",
        )
        self.assertEqual(node_condition(_node("a", "copy_xml", 1)),
                         CONDITION_ALWAYS)

    def test_every_condition_has_a_ui_label(self):
        for condition in wc.ALL_CONDITIONS:
            self.assertIn(condition, wc.CONDITION_LABELS)


class MismatchRoutingTests(unittest.TestCase):
    """Operator rule 2026-10-12: MISMATCH goes to XML, not to Final Bill.

    Verified basis: a fees row is NO FINAL BILL when itemized <= 0.01,
    so a MISMATCH row has itemized > 0 — the bill is ALREADY finalized
    in HBSys and does not need Final Bill again.
    """

    def test_mismatch_is_not_final_billed(self):
        # Unchanged 2026-10-07 rule: MISMATCH never routes to Final Bill.
        ok, reason = evaluate(CONDITION_FEES_SAYS_FINAL_BILL,
                              {"fees_rows": [ROW_MISMATCH]})
        self.assertFalse(ok)
        self.assertIn("no patient needs Final Bill", reason)

    def test_mismatch_allows_xml(self):
        ok, _ = evaluate(CONDITION_FEES_ALLOWS_XML,
                         {"fees_rows": [ROW_MISMATCH]})
        self.assertTrue(ok)

    def test_strict_ready_xml_still_rejects_mismatch(self):
        # fees_ready_xml is kept for a stricter workflow; it must not
        # silently start including MISMATCH.
        ok, _ = evaluate(CONDITION_FEES_READY_XML,
                         {"fees_rows": [ROW_MISMATCH]})
        self.assertFalse(ok)

    def test_mismatch_with_missing_dates_needs_date_fill_first(self):
        row = dict(ROW_MISMATCH, **{"Auth Sign Date (hpatcon1.authsigndate)": ""})
        context = {"fees_rows": [row]}
        ok, _ = evaluate(CONDITION_FEES_SAYS_DATE_FILL, context)
        self.assertTrue(ok)
        ok, reason = evaluate(CONDITION_FEES_ALLOWS_XML, context)
        self.assertFalse(ok)
        self.assertIn("no patient allows XML", reason)

    def test_mismatch_with_all_dates_goes_straight_to_xml(self):
        context = {"fees_rows": [ROW_MISMATCH]}
        ok, _ = evaluate(CONDITION_FEES_SAYS_DATE_FILL, context)
        self.assertFalse(ok)
        ok, _ = evaluate(CONDITION_FEES_ALLOWS_XML, context)
        self.assertTrue(ok)

    def test_mixed_batch_allows_xml_when_any_one_qualifies(self):
        context = {"fees_rows": [ROW_READY, ROW_MISMATCH, ROW_NO_BILL]}
        ok, _ = evaluate(CONDITION_FEES_ALLOWS_XML, context)
        self.assertTrue(ok)

    def test_folder_counted_once_across_both_xml_routes(self):
        context = {"fees_rows": [ROW_READY, ROW_MISMATCH]}
        routed = wc._folders_needing(context, "xml_clicker") + \
            wc.mismatch_folders_allowing_xml(context)
        self.assertEqual(len(routed), 2)   # different folders
        self.assertEqual(len(wc._unique(routed)), 2)
        # A folder that qualifies through BOTH routes is counted once.
        both = wc._folders_needing(context, "xml_clicker")
        both = both + wc._folders_needing(context, "xml_clicker")
        self.assertEqual(len(wc._unique(both)), 1)

    def test_only_mismatch_is_special_cased(self):
        # MISMATCH is the ONLY status that may reach XML without
        # "Ready to Generate XML" = YES.
        for row, expected in (
            (ROW_READY, True),          # MATCH + ready YES
            (ROW_MISMATCH, True),       # MISMATCH, bill already finalized
            (ROW_NO_BILL, False),       # needs Final Bill first
            (ROW_NO_RECORD, False),     # nothing downstream can run
            (ROW_ERROR, False),         # technical error, ready is blank
        ):
            ok, _ = evaluate(CONDITION_FEES_ALLOWS_XML, {"fees_rows": [row]})
            self.assertEqual(ok, expected, row["Status"])

    def test_default_workflow_uses_the_allows_xml_condition(self):
        config = default_workflow()
        xml_node = next(n for n in config.nodes if n.node_type == "xml_clicker")
        self.assertEqual(xml_node.params.get("when"), CONDITION_FEES_ALLOWS_XML)
        self.assertNotEqual(xml_node.params.get("when"), CONDITION_FEES_READY_XML)


class NoFinalBillDateFillTests(unittest.TestCase):
    """Operator rule 2026-10-13: NO FINAL BILL + blank date -> Date Fill
    first, then Final Bill; XML only when Ready to Generate = YES.

    The EXISTING Fees routing (``decide_action`` step 2) sends every
    NO FINAL BILL row to Final Bill BEFORE reaching its date check
    (step 3) — so without the workflow-level helper the Date Fill node
    would be skipped for an unbilled patient with blank signed dates,
    while the Final Bill node's own Date Fill gate (operator rule
    2026-10-09) refuses to bill unverified folders. The patient would
    be stuck forever and never reach XML.
    """

    def test_no_bill_with_blank_date_needs_date_fill(self):
        ok, _ = evaluate(CONDITION_FEES_SAYS_DATE_FILL,
                         {"fees_rows": [ROW_NO_BILL_NO_DATES]})
        self.assertTrue(ok)

    def test_no_bill_with_blank_date_still_needs_final_bill(self):
        # BOTH nodes run, in workflow order (date_fill node 2 runs
        # before the final_bill node 3): Date Fill fills the dates,
        # Final Bill bills afterwards.
        context = {"fees_rows": [ROW_NO_BILL_NO_DATES]}
        ok, _ = evaluate(CONDITION_FEES_SAYS_DATE_FILL, context)
        self.assertTrue(ok)
        ok, _ = evaluate(CONDITION_FEES_SAYS_FINAL_BILL, context)
        self.assertTrue(ok)

    def test_no_bill_with_complete_dates_skips_date_fill(self):
        ok, reason = evaluate(CONDITION_FEES_SAYS_DATE_FILL,
                              {"fees_rows": [ROW_NO_BILL]})
        self.assertFalse(ok)
        self.assertIn("no patient needs Date Fill", reason)

    def test_any_single_blank_signed_date_triggers_date_fill(self):
        # Either alin sa tatlo (prof/consent/auth) — isa lang na blank,
        # Date Fill na.
        for column in (
            "Prof Fee Sign Date (hprofserv.pdoctorsigndate)",
            "Consent Date (hpatcon1.consentdate)",
            "Auth Sign Date (hpatcon1.authsigndate)",
        ):
            row = dict(ROW_NO_BILL, **{column: ""})
            ok, _ = evaluate(CONDITION_FEES_SAYS_DATE_FILL,
                             {"fees_rows": [row]})
            self.assertTrue(ok, column)

    def test_no_bill_never_allows_xml_until_ready(self):
        # With or without dates, an unbilled patient must not reach
        # the XML Clicker — saka lang kapag Ready to Generate = YES
        # (which a NO FINAL BILL row can never satisfy).
        for row in (ROW_NO_BILL, ROW_NO_BILL_NO_DATES):
            ok, reason = evaluate(CONDITION_FEES_ALLOWS_XML,
                                  {"fees_rows": [row]})
            self.assertFalse(ok, row["Status"])
            self.assertIn("no patient allows XML", reason)
            ok, _ = evaluate(CONDITION_FEES_READY_XML,
                             {"fees_rows": [row]})
            self.assertFalse(ok, row["Status"])

    def test_helpers_are_pure(self):
        context = {"fees_rows": [ROW_NO_BILL_NO_DATES, ROW_NO_BILL]}
        self.assertEqual(wc.no_final_bill_rows(context),
                         [ROW_NO_BILL_NO_DATES, ROW_NO_BILL])
        self.assertEqual(wc.no_final_bill_folders_missing_dates(context),
                         [ROW_NO_BILL_NO_DATES["Patient Folder"]])

    def test_no_bill_patient_progresses_across_passes_without_looping(self):
        # Operator scenario 2026-10-13, end to end for ONE patient:
        # NO FINAL BILL + blank consent date. Each snapshot is what
        # the next fees CSV contains after the previous node did its
        # job. The chain must move FORWARD every pass (date fill ->
        # final bill -> xml) and never revisit a node — the engine
        # visits each node exactly once, strictly sequentially.
        folder = ROW_NO_BILL_NO_DATES["Patient Folder"]

        # Pass 1 (fees check 1st): unbilled + blank date ->
        # Date Fill AND Final Bill run, XML waits.
        ctx1 = {"fees_rows": [ROW_NO_BILL_NO_DATES]}
        self.assertTrue(evaluate(CONDITION_FEES_SAYS_DATE_FILL, ctx1)[0])
        self.assertTrue(evaluate(CONDITION_FEES_SAYS_FINAL_BILL, ctx1)[0])
        self.assertFalse(evaluate(CONDITION_FEES_ALLOWS_XML, ctx1)[0])

        # After Date Fill: dates complete, still unbilled ->
        # Date Fill drops out, Final Bill stays.
        filled = dict(ROW_NO_BILL_NO_DATES,
                      **{"Consent Date (hpatcon1.consentdate)": "2026/09/05"})
        ctx2 = {"fees_rows": [filled]}
        self.assertFalse(evaluate(CONDITION_FEES_SAYS_DATE_FILL, ctx2)[0])
        self.assertTrue(evaluate(CONDITION_FEES_SAYS_FINAL_BILL, ctx2)[0])
        self.assertFalse(evaluate(CONDITION_FEES_ALLOWS_XML, ctx2)[0])

        # After Final Bill + fees re-check: MATCH + Ready YES ->
        # both fixers drop out, XML runs. Terminal state: nothing
        # left to do, so no loop is possible.
        ready = dict(filled, Status="MATCH",
                     **{"Ready to Generate XML": "YES"})
        self.assertEqual(ready["Patient Folder"], folder)
        ctx3 = {"fees_rows": [ready]}
        self.assertFalse(evaluate(CONDITION_FEES_SAYS_DATE_FILL, ctx3)[0])
        self.assertFalse(evaluate(CONDITION_FEES_SAYS_FINAL_BILL, ctx3)[0])
        self.assertTrue(evaluate(CONDITION_FEES_ALLOWS_XML, ctx3)[0])

        # And the default workflow visits the fixers in that order.
        config = default_workflow()
        order = {n.node_type: n.order for n in config.nodes}
        self.assertLess(order["date_fill_regular"], order["final_bill"])
        self.assertLess(order["final_bill"], order["xml_clicker"])

    def test_mismatch_helpers_are_pure(self):
        context = {"fees_rows": [ROW_MISMATCH, ROW_READY]}
        self.assertEqual(wc.mismatch_rows(context),
                         [ROW_MISMATCH])
        self.assertEqual(wc.mismatch_folders_allowing_xml(context),
                         [ROW_MISMATCH["Patient Folder"]])
        self.assertEqual(wc.mismatch_folders_missing_dates(context), [])


class DefaultWorkflowConditionTests(unittest.TestCase):
    """The shipped default carries the operator's conditions."""

    def test_default_conditions_match_the_operator_pipeline(self):
        config = default_workflow()
        got = {n.node_type: n.params.get("when") for n in config.nodes}
        self.assertEqual(got["fees_checker"], CONDITION_ALWAYS)
        self.assertEqual(got["date_fill_regular"], CONDITION_FEES_SAYS_DATE_FILL)
        self.assertEqual(got["final_bill"], CONDITION_FEES_SAYS_FINAL_BILL)
        self.assertEqual(got["copy_xml"], CONDITION_ALWAYS)
        self.assertEqual(got["claims_checker"], CONDITION_ALWAYS)
        self.assertEqual(got["add_claims_upload"], CONDITION_CHECKER_READY)
        self.assertEqual(got["claim_attachments"], CONDITION_CHECKER_READY)

    def test_default_conditions_are_all_known_ids(self):
        for node in default_workflow().nodes:
            when = node_condition(node)
            self.assertTrue(is_known(when), (node.node_type, when))

    def test_date_fill_keeps_continue_on_fail_with_condition(self):
        node = next(n for n in default_workflow().nodes
                    if n.node_type == "date_fill_regular")
        self.assertTrue(node.params.get("continue_on_fail"))
        self.assertEqual(node.params.get("when"), CONDITION_FEES_SAYS_DATE_FILL)

    def test_conditions_survive_save_load_round_trip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wf.json"
            we.save_workflow(default_workflow(), path)
            loaded = we.load_workflow(path)
            got = {n.node_type: n.params.get("when") for n in loaded.nodes}
            self.assertEqual(got["xml_clicker"], CONDITION_FEES_ALLOWS_XML)


class EngineConditionTests(unittest.TestCase):
    """A false condition SKIPS the node and the chain continues."""

    def setUp(self):
        _FakeAdapter.calls = []

    def _run(self, config, context):
        engine = WorkflowEngine()
        with mock.patch.object(we, "ScriptNodeAdapter", _FakeAdapter), \
                mock.patch.object(WorkflowEngine, "_hbsys_available",
                                  lambda self: True), \
                mock.patch.object(WorkflowEngine, "_build_context",
                                  lambda self, settings: dict(context)), \
                mock.patch.object(WorkflowEngine, "_refresh_condition_context",
                                  lambda self, ctx: None):
            return engine.run(config, live=False), _FakeAdapter.calls

    def test_false_condition_skips_and_chain_continues(self):
        config = WorkflowConfig(nodes=[
            _node("a", "copy_xml", 1, when=CONDITION_FEES_READY_XML),
            _node("b", "fees_checker", 2),
        ])
        report, calls = self._run(config, {"fees_rows": [ROW_NO_BILL],
                                           "ready_folders": []})
        self.assertNotIn("copy_xml", calls)
        self.assertIn("fees_checker", calls)
        by_node: dict[str, list[str]] = {}
        for row in report.node_results:
            by_node.setdefault(row.node_id, []).append(row.status)
        self.assertEqual(by_node["a"][-1], NODE_STATUS_SKIPPED)
        self.assertEqual(by_node["b"][-1], NODE_STATUS_DONE)
        self.assertEqual(report.result, we.WORKFLOW_COMPLETED)

    def test_skip_reason_names_the_condition(self):
        config = WorkflowConfig(nodes=[
            _node("a", "xml_clicker", 1, when=CONDITION_FEES_READY_XML),
        ])
        report, _ = self._run(config, {"fees_rows": [ROW_NO_BILL]})
        reason = report.node_results[0].reason
        self.assertIn(CONDITION_FEES_READY_XML, reason)
        self.assertIn("no patient is ready for XML", reason)

    def test_true_condition_runs_the_node(self):
        config = WorkflowConfig(nodes=[
            _node("a", "xml_clicker", 1, when=CONDITION_FEES_READY_XML),
        ])
        report, calls = self._run(config, {"fees_rows": [ROW_READY]})
        self.assertIn("xml_clicker", calls)
        # The engine records RUNNING then DONE for the same node, so the
        # final status is the last entry for that node id.
        self.assertEqual(
            [r.status for r in report.node_results if r.node_id == "a"][-1],
            NODE_STATUS_DONE,
        )

    def test_node_without_when_always_runs(self):
        # Pre-Phase-3 behaviour must be unchanged.
        config = WorkflowConfig(nodes=[
            _node("a", "xml_clicker", 1),
        ])
        _, calls = self._run(config, {"fees_rows": []})
        self.assertIn("xml_clicker", calls)

    def test_unknown_condition_skips_the_node(self):
        config = WorkflowConfig(nodes=[
            _node("a", "xml_clicker", 1, when="bogus"),
            _node("b", "claims_checker", 2),
        ])
        report, calls = self._run(config, {"fees_rows": [ROW_READY]})
        self.assertNotIn("xml_clicker", calls)
        self.assertIn("claims_checker", calls)
        self.assertIn("never guessed", report.node_results[0].reason)

    def test_condition_skip_does_not_stop_the_workflow(self):
        # A SKIPPED-by-condition node must NOT trigger the failure policy.
        config = WorkflowConfig(nodes=[
            _node("a", "copy_xml", 1, when="bogus"),
            _node("b", "add_claims_upload", 2, when=CONDITION_CHECKER_READY),
            # Unconditional: proves a condition-skip never stops the chain.
            _node("c", "claim_attachments", 3),
        ])
        report, calls = self._run(config, {"fees_rows": [], "ready_folders": []})
        self.assertNotIn("copy_xml", calls)
        self.assertNotIn("add_claims_upload", calls)
        self.assertIn("claim_attachments", calls)
        self.assertEqual(report.result, we.WORKFLOW_COMPLETED)

    def test_skip_is_logged_to_the_output_stream(self):
        lines: list[str] = []
        engine = WorkflowEngine(on_output=lines.append)
        config = WorkflowConfig(nodes=[
            _node("a", "xml_clicker", 1, when=CONDITION_FEES_READY_XML),
        ])
        with mock.patch.object(we, "ScriptNodeAdapter", _FakeAdapter), \
                mock.patch.object(WorkflowEngine, "_hbsys_available",
                                  lambda self: True), \
                mock.patch.object(WorkflowEngine, "_build_context",
                                  lambda self, settings: {
                                      "fees_rows": [ROW_NO_BILL]}):
            engine.run(config, live=False)
        self.assertTrue(any("skipped" in line for line in lines), lines)


class _FakeAdapter:
    """Never spawns a process; records the node key it was asked to run."""

    calls: list[str] = []

    def __init__(self, spec, settings, on_output=None):
        self.spec = spec

    def run(self, *, live: bool = True) -> str:
        _FakeAdapter.calls.append(self.spec.key)
        return "DONE"

    def stop(self) -> None:
        pass


def setUpModule():
    _FakeAdapter.calls = []


if __name__ == "__main__":
    unittest.main()