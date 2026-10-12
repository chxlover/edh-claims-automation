"""Tests for the Claims Agent pipeline (task sections 4-11).

Headless: every HBSys-touching stage is injected as a fake; no HBSys, no
live DB writes (SQLite state lives in a TemporaryDirectory), no PhilHealth
transmission. Real on-disk checks are used only for XML validation
(scenarios 9-16) with dummy files.

Run from the project root:
    python -m unittest tests.test_claims_agent
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.claims_agent import ClaimsAgent  # noqa: E402
from core.agent.pipeline_config import PipelineConfig  # noqa: E402
from core.agent.pipeline_state import PipelineStore  # noqa: E402
from core.agent.xml_validation import validate_patient_xmls  # noqa: E402

FOLDER_A = "DELA CRUZ, JUAN - 123456789012345 - ADM20260901_DIS20260903"
FOLDER_B = "SANTOS, MARIA - 000000000021401 - ADM20260906_DIS20260912"
SERIES = "260924150960"

READY_ROW = {
    "Status": "READY", "Missing": "", "Warnings": "",
    "Found": "CSF; CF4 XML; CF5 XML; eSOA XML",
}


def _write_xml(folder: Path, series: str, kind: str, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"NAME-{series}_{kind}.xml").write_text(body, encoding="utf-8")


def _write_good_xmls(folder: Path, series: str = SERIES) -> None:
    for kind in ("CF4", "CF5", "ESOA"):
        _write_xml(folder, series, kind, "<root><a>1</a></root>")


def _valid_verdict():
    from core.agent.xml_validation import XmlFileResult, XmlValidationResult

    verdict = XmlValidationResult(
        cross_kind_match=True, overall_valid=True, missing_kinds=()
    )
    for label in ("CF4", "CF5", "eSOA"):
        verdict.files[label] = XmlFileResult(
            kind=label, path=f"/x/{label}.xml", exists=True, non_empty=True,
            well_formed=True, series=(SERIES,), claim_match="MATCH", valid=True,
        )
    return verdict


class AgentHarness:
    """Build a ClaimsAgent with tmp dirs and success-by-default fakes."""

    def __init__(self, tmp: str, **overrides):
        self.tmp = Path(tmp)
        self.scan = self.tmp / "scans"
        self.output = self.tmp / "output"
        self.reports = self.tmp / "reports"
        self.scan.mkdir()
        self.output.mkdir()
        self.calls: list[tuple] = []

        def rec(name):
            def _fn(*args, **kwargs):
                self.calls.append((name, args))
                return _fn.result
            _fn.result = None
            return _fn

        self.config = PipelineConfig(
            scan_folder=self.scan,
            output_folder=self.output,
            report_dir=self.reports,
            state_db=self.tmp / "pipeline.db",
            live_hbsys=True,
            auto_open_report=False,
            resume=True,
        )
        for key, value in overrides.get("config", {}).items():
            setattr(self.config, key, value)
        self.store = PipelineStore(self.config.state_db)
        self.logs: list[str] = []

        fakes = dict(
            claim_processor_fn=self._ok_processor(),
            fees_snapshot_fn=lambda out: ({}, ""),
            fees_verdict_fn=lambda rows, folder: ("YES", ""),
            date_fill_fn=self._ok3("date_fill"),
            final_bill_fn=self._ok3("final_bill"),
            xml_clicker_fn=self._ok3("xml_clicker"),
            claims_check_fn=lambda folder: ("READY", dict(READY_ROW)),
            xml_validate_fn=lambda folder: _valid_verdict(),
            add_claims_fn=self._ok3("add_claims"),
            finalize_add_claims_fn=self._ok_finalize(),
            attachments_fn=self._ok_attachments(),
            verify_fn=lambda series, records, root: (
                True, "verified: MATCH=3", {"counts": {"MATCH": 3}}),
        )
        fakes.update(overrides.get("fakes", {}))
        self.agent = ClaimsAgent(
            config=self.config, store=self.store, log_fn=self.logs.append,
            **fakes,
        )

    def _ok_processor(self):
        def _fn(scan, output, log):
            self.calls.append(("processor", ()))
            return True, "ok", {"new_folders": []}
        return _fn

    def _ok3(self, name):
        def _fn(*args, **kwargs):
            self.calls.append((name, args))
            return True, "ok", {}
        return _fn

    def _ok_finalize(self):
        def _fn(log):
            self.calls.append(("finalize", ()))
            return True, "finalized"
        return _fn

    def _ok_attachments(self):
        def _fn(folder_path, patient_name, log):
            self.calls.append(("attachments", (folder_path,)))
            return True, "attached", {
                "claim_series": SERIES, "source_records": []}
        return _fn

    def add_folder(self, name: str) -> Path:
        folder = self.output / name
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def calls_named(self, name: str) -> list:
        return [c for c in self.calls if c[0] == name]

    def record(self, batch: str, key: str):
        return self.store.get_claim(batch, key)


class ClaimsAgentTests(unittest.TestCase):
    # 1. Complete successful pipeline.
    def test_01_full_success_completes(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(tmp)
            h.add_folder(FOLDER_A)
            summary = h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "COMPLETED")
            self.assertEqual(rec.last_completed_stage, "COMPLETED")
            self.assertEqual(summary.completed, 1)
            self.assertEqual(summary.total, 1)
            reports = list(h.reports.glob("*.xlsx"))
            self.assertEqual(len(reports), 1)

    # 2. Claim Processor failure still processes on-disk folders.
    def test_02_processor_failure_continues_with_disk_folders(self):
        with TemporaryDirectory() as tmp:
            def boom(scan, output, log):
                return False, "engine exploded", {}
            h = AgentHarness(tmp, fakes={"claim_processor_fn": boom})
            h.add_folder(FOLDER_A)
            summary = h.agent.run_batch("B1")
            self.assertEqual(h.record("B1", FOLDER_A).outcome, "COMPLETED")
            self.assertEqual(summary.completed, 1)

    # 3. First Fees Checker NO still proceeds (existing business order).
    def test_03_first_fees_no_still_proceeds(self):
        with TemporaryDirectory() as tmp:
            calls = []

            def verdict(rows, folder):
                calls.append(folder)
                return ("NO", "charges differ") if len(calls) == 1 else ("YES", "")
            h = AgentHarness(tmp, fakes={"fees_verdict_fn": verdict})
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "COMPLETED")
            self.assertIn("not ready (pass 1)", rec.stage_status["FEES_CHECK_1"]["detail"])

    # 4. Second Fees NO then YES after one recovery cycle.
    def test_04_second_fees_no_then_yes(self):
        with TemporaryDirectory() as tmp:
            state = {"n": 0}

            def verdict(rows, folder):
                state["n"] += 1
                if folder != FOLDER_A:
                    return ("YES", "")
                return ("YES", "") if state["n"] >= 3 else ("NO", "dates differ")
            h = AgentHarness(tmp, fakes={"fees_verdict_fn": verdict})
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "COMPLETED")
            self.assertEqual(rec.stage_status["FEES_CHECK_2"]["evaluations"], 2)
            self.assertEqual(len(h.calls_named("date_fill")), 2)  # initial + 1 cycle

    # 5. Second Fees NO until the retry limit -> INCOMPLETE, no XML.
    def test_05_second_fees_no_until_limit_is_incomplete(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(
                tmp,
                config={"max_fee_cycle_retries": 1},
                fakes={"fees_verdict_fn": lambda r, f: ("NO", "still off")},
            )
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "INCOMPLETE")
            self.assertEqual(rec.stage_status["FEES_CHECK_2"]["evaluations"], 2)
            self.assertNotIn("XML_GENERATION", rec.stage_status)
            self.assertTrue(rec.manual_action)

    # 5b. Retry semantics: 0 retries means exactly one evaluation.
    def test_05b_zero_retries_single_evaluation(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(
                tmp,
                config={"max_fee_cycle_retries": 0},
                fakes={"fees_verdict_fn": lambda r, f: ("NO", "off")},
            )
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.stage_status["FEES_CHECK_2"]["evaluations"], 1)
            self.assertEqual(len(h.calls_named("date_fill")), 1)

    # 6. Date Fill failure stops the claim before Final Bill.
    def test_06_date_fill_failure(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(
                tmp,
                fakes={"date_fill_fn": lambda f, log: (False, "no encounter", {})},
            )
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "FAILED")
            self.assertEqual(h.calls_named("final_bill"), [])
            self.assertTrue(rec.manual_action)

    # 7. Final Bill failure stops the claim.
    def test_07_final_bill_failure(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(
                tmp,
                fakes={"final_bill_fn": lambda f, log: (False, "BLOCKED: x", {})},
            )
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "FAILED")
            self.assertNotIn("FEES_CHECK_2", rec.stage_status)

    # 8. XML Clicker failure (retries exhausted) blocks attachments.
    def test_08_xml_clicker_failure(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(
                tmp,
                config={"xml_max_retries": 1},
                fakes={"xml_clicker_fn": lambda f, k, log: (False, "popup stuck", {})},
            )
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "FAILED")
            self.assertNotIn("XML_VALIDATION", rec.stage_status)
            self.assertEqual(h.calls_named("attachments"), [])

    # 9/10/11. Each missing XML kind -> INCOMPLETE (real validation).
    def test_09_10_11_each_missing_xml_kind_is_incomplete(self):
        # Labels are the repo's own (claims_checker "eSOA XML", KIND_LABELS).
        for missing_file, missing_label in (
            ("CF4", "CF4"), ("CF5", "CF5"), ("ESOA", "eSOA")):
            with TemporaryDirectory() as tmp:
                h = AgentHarness(
                    tmp, fakes={"xml_validate_fn": validate_patient_xmls})
                folder = h.add_folder(FOLDER_A)
                for kind in ("CF4", "CF5", "ESOA"):
                    if kind != missing_file:
                        _write_xml(folder, SERIES, kind, "<root/>")
                h.agent.run_batch("B1")
                rec = h.record("B1", FOLDER_A)
                self.assertEqual(rec.outcome, "INCOMPLETE", missing_file)
                self.assertIn(
                    missing_label, rec.stage_status["XML_VALIDATION"]["detail"]) 

    # 12. Empty XML file -> INCOMPLETE.
    def test_12_empty_xml_is_incomplete(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(tmp, fakes={"xml_validate_fn": validate_patient_xmls})
            folder = h.add_folder(FOLDER_A)
            _write_good_xmls(folder)
            (folder / f"NAME-{SERIES}_CF4.xml").write_text("", encoding="utf-8")
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "INCOMPLETE")
            self.assertFalse(rec.xml_result["files"]["CF4"]["non_empty"])

    # 13. Malformed XML is recorded honestly (encrypted-envelope rule).
    def test_13_malformed_xml_recorded_not_invented(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(tmp, fakes={"xml_validate_fn": validate_patient_xmls})
            folder = h.add_folder(FOLDER_A)
            _write_good_xmls(folder)
            (folder / f"NAME-{SERIES}_CF5.xml").write_text("<root>", encoding="utf-8")
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            files = rec.xml_result["files"]
            self.assertFalse(files["CF5"]["well_formed"])
            self.assertIn("encrypted", files["CF5"]["content_note"])
            # Filename-series rule still governs validity (documented).
            self.assertTrue(files["CF5"]["valid"])

    # 14/15. Series mismatch across kinds -> INCOMPLETE with MISMATCH.
    def test_14_15_series_mismatch_is_incomplete(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(tmp, fakes={"xml_validate_fn": validate_patient_xmls})
            folder = h.add_folder(FOLDER_A)
            _write_xml(folder, "111111111111", "CF4", "<root/>")
            _write_xml(folder, SERIES, "CF5", "<root/>")
            _write_xml(folder, SERIES, "ESOA", "<root/>")
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "INCOMPLETE")
            self.assertEqual(rec.xml_result["files"]["CF4"]["claim_match"], "MISMATCH")
            self.assertFalse(rec.xml_result["cross_kind_match"])

    # 16. All three valid XMLs pass (real validation).
    def test_16_all_three_valid_xmls(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(tmp, fakes={"xml_validate_fn": validate_patient_xmls})
            folder = h.add_folder(FOLDER_A)
            _write_good_xmls(folder)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertTrue(rec.xml_result["overall_valid"])
            self.assertEqual(rec.outcome, "COMPLETED")

    # 17/18/19. Checker statuses preserved; only READY proceeds.
    def test_17_ready_proceeds_to_attachments(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(tmp)
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            self.assertEqual(h.record("B1", FOLDER_A).checker_status, "READY")
            self.assertEqual(len(h.calls_named("attachments")), 1)

    def test_18_incomplete_holds_without_attachments(self):
        with TemporaryDirectory() as tmp:
            row = dict(READY_ROW, Status="INCOMPLETE", Missing="CF2")
            h = AgentHarness(
                tmp, fakes={"claims_check_fn": lambda f: ("INCOMPLETE", row)})
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "INCOMPLETE")
            self.assertEqual(h.calls_named("attachments"), [])

    def test_19_ready_with_review_preserved_exactly(self):
        with TemporaryDirectory() as tmp:
            row = dict(READY_ROW, Status="READY WITH REVIEW", Warnings="sig?")
            h = AgentHarness(
                tmp,
                fakes={"claims_check_fn": lambda f: ("READY WITH REVIEW", row)},
            )
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.checker_status, "READY WITH REVIEW")  # spaces kept
            self.assertEqual(rec.outcome, "HELD_FOR_REVIEW")
            self.assertEqual(h.calls_named("attachments"), [])

    # 20. Only eligible READY claims proceed (mixed batch).
    def test_20_mixed_batch_only_ready_attaches(self):
        with TemporaryDirectory() as tmp:
            def check(folder):
                if FOLDER_A in str(folder):
                    return "READY", dict(READY_ROW)
                return "INCOMPLETE", dict(READY_ROW, Status="INCOMPLETE",
                                          Missing="CF2")
            h = AgentHarness(tmp, fakes={"claims_check_fn": check})
            h.add_folder(FOLDER_A)
            h.add_folder(FOLDER_B)
            summary = h.agent.run_batch("B1")
            self.assertEqual(h.record("B1", FOLDER_A).outcome, "COMPLETED")
            self.assertEqual(h.record("B1", FOLDER_B).outcome, "INCOMPLETE")
            self.assertEqual(len(h.calls_named("attachments")), 1)
            self.assertEqual(summary.completed, 1)
            self.assertEqual(summary.incomplete, 1)

    # 21. Add Claims failure holds the claim.
    def test_21_add_claims_failure(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(
                tmp,
                fakes={"add_claims_fn": lambda n, p, log: (False, "no row", {})},
            )
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "FAILED")
            self.assertEqual(h.calls_named("finalize"), [])
            self.assertEqual(h.calls_named("attachments"), [])

    # 22. Attachments failure holds the claim.
    def test_22_attachments_failure(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(
                tmp,
                fakes={"attachments_fn": lambda p, n, log: (
                    False, "upload failed", {"uncertain": True})},
            )
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "FAILED")
            self.assertIn("uncertain", rec.hold_reason)

    # 23. Verification failure is not completion.
    def test_23_verification_failure_not_completed(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(
                tmp,
                fakes={"verify_fn": lambda s, r, d: (
                    False, "not verified: MISMATCH=1", {"counts": {"MISMATCH": 1}})},
            )
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "FAILED")
            self.assertFalse(rec.verification["verified"])

    # 24. Resume continues after the last completed stage.
    def test_24_resume_skips_completed_stages(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(tmp)
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            self.assertEqual(h.record("B1", FOLDER_A).outcome, "COMPLETED")
            second_calls: list = []
            real_date_fill = h.agent.date_fill_fn
            real_processor = h.agent.claim_processor_fn

            def counting_date_fill(folder, log):
                second_calls.append(str(folder))
                return real_date_fill(folder, log)

            h.agent.date_fill_fn = counting_date_fill
            h.agent.claim_processor_fn = lambda s, o, log: (True, "ok", {})
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "COMPLETED")
            self.assertEqual(second_calls, [])

    # 25. Resume never repeats OK non-idempotent HBSys actions.
    def test_25_resume_keeps_verified_hbsys_work(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(tmp)
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "COMPLETED")
            n_add = len(h.calls_named("add_claims"))
            n_att = len(h.calls_named("attachments"))
            # Second run of the same batch: terminal claims are skipped.
            h.agent.run_batch("B1")
            self.assertEqual(len(h.calls_named("add_claims")), n_add)
            self.assertEqual(len(h.calls_named("attachments")), n_att)

    # 26. One claim fails, another completes.
    def test_26_isolation_between_claims(self):
        with TemporaryDirectory() as tmp:
            def date_fill(folder, log):
                if FOLDER_B in str(folder):
                    return False, "no encounter", {}
                return True, "ok", {}
            h = AgentHarness(tmp, fakes={"date_fill_fn": date_fill})
            h.add_folder(FOLDER_A)
            h.add_folder(FOLDER_B)
            summary = h.agent.run_batch("B1")
            self.assertEqual(h.record("B1", FOLDER_A).outcome, "COMPLETED")
            self.assertEqual(h.record("B1", FOLDER_B).outcome, "FAILED")
            self.assertEqual(summary.completed, 1)
            self.assertEqual(summary.failed, 1)

    # 27. Mixed outcomes appear correctly in the Excel report.
    def test_27_mixed_report_contents(self):
        with TemporaryDirectory() as tmp:
            def check(folder):
                name = str(folder)
                if "DELA CRUZ, JUAN" in name:
                    return "READY", dict(READY_ROW)
                if "SANTOS" in name:
                    return "INCOMPLETE", dict(READY_ROW, Status="INCOMPLETE",
                                              Missing="CF2")
                return "READY WITH REVIEW", dict(
                    READY_ROW, Status="READY WITH REVIEW", Warnings="w")
            h = AgentHarness(tmp, fakes={"claims_check_fn": check})
            h.add_folder(FOLDER_A)
            h.add_folder(FOLDER_B)
            h.add_folder("REYES, ANA - 000000000099999 - ADM20260901_DIS20260902")
            summary = h.agent.run_batch("B1")
            self.assertEqual(
                (summary.completed, summary.incomplete, summary.held_for_review),
                (1, 1, 1))
            from openpyxl import load_workbook
            book = load_workbook(list(h.reports.glob("*.xlsx"))[0])
            claims = book["Claims"]
            outcomes = [claims.cell(row=r, column=21).value for r in (2, 3, 4)]
            self.assertEqual(
                sorted(outcomes), ["COMPLETED", "HELD_FOR_REVIEW", "INCOMPLETE"])
            checker_col = [claims.cell(row=r, column=15).value for r in (2, 3, 4)]
            self.assertIn("READY WITH REVIEW", checker_col)  # spaces preserved

    # 28. Report-generation failure keeps claim progress.
    def test_28_report_failure_keeps_state(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(tmp)
            h.add_folder(FOLDER_A)
            import core.agent.claims_report as report_mod
            real = report_mod.write_batch_report
            report_mod.write_batch_report = lambda *a, **k: (_ for _ in ()).throw(
                OSError("disk full"))
            try:
                summary = h.agent.run_batch("B1")
            finally:
                report_mod.write_batch_report = real
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "COMPLETED")
            self.assertEqual(summary.completed, 1)
            self.assertTrue(any("REPORT FAILURE" in line for line in h.logs))

    # 29. Live HBSys off holds HBSys stages honestly.
    def test_29_live_off_holds_hbsys_stages(self):
        with TemporaryDirectory() as tmp:
            h = AgentHarness(tmp, config={"live_hbsys": False})
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "HELD_FOR_REVIEW")
            self.assertEqual(rec.stage_status["DATE_FILL"]["status"], "SKIPPED")
            self.assertEqual(h.calls_named("date_fill"), [])

    # 30. Stage functions get a single-arg log callable (adapter contract).    def test_30_stage_log_accepts_single_arg(self):
        with TemporaryDirectory() as tmp:
            def date_fill(folder, log):
                log("filling dates...")  # real adapters call log(message)
                return True, "ok", {}
            h = AgentHarness(tmp, fakes={"date_fill_fn": date_fill})
            h.add_folder(FOLDER_A)
            h.agent.run_batch("B1")
            rec = h.record("B1", FOLDER_A)
            self.assertEqual(rec.outcome, "COMPLETED")
            self.assertTrue(
                any("[agent][B1]" in line and "filling dates" in line
                    for line in h.logs),
                h.logs)

    # 31. Claim processor streams engine output live (no silent minutes).
    def test_31_processor_streams_live(self):
        import core.agent.pipeline_adapters as adapters

        with TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "fake_engine.py").write_text(
                "print('engine line one')\nprint('engine line two')\n",
                encoding="utf-8",
            )
            out = tmpdir / "out"
            out.mkdir()
            old_base, old_mod = adapters.BASE_DIR, adapters.ENGINE_MODULE
            adapters.BASE_DIR, adapters.ENGINE_MODULE = tmpdir, "fake_engine.py"
            try:
                lines: list[str] = []
                ok, detail, extra = adapters.run_claim_processor_once(
                    tmpdir / "scans", out, lines.append)
            finally:
                adapters.BASE_DIR, adapters.ENGINE_MODULE = old_base, old_mod
            self.assertTrue(ok, detail)
            self.assertTrue(any("starting" in line for line in lines), lines)
            self.assertTrue(any("engine line one" in line for line in lines),
                            lines)
            self.assertTrue(any("done (exit=0)" in line for line in lines),
                            lines)


if __name__ == "__main__":
    unittest.main()
