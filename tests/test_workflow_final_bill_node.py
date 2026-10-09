"""Unit tests for the final_bill Workflow Tab node (folder contract, Option B).

Covers: registry entry, default workflow placement, folder contract +
resume marker semantics, DRY safety (no pywinauto, no markers), exit codes
(fail-safe chain stop), --limit, output-root resolution, and the engine's
HBSys gate for the new node.

The LIVE path is never exercised against a real HBSys: `default_final_bill`
/ `run_patient` are mocked, and the engine test patches
`WorkflowEngine._hbsys_available` — so every test is headless + hermetic.

Run from the project root:

    python -m unittest tests.test_workflow_final_bill_node
    python tests/test_workflow_final_bill_node.py
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import final_bill_runner as runner  # noqa: E402
from core.workflow_adapters import build_command  # noqa: E402
from core.workflow_engine import (  # noqa: E402
    DEFAULT_NODE_PARAMS,
    NODE_STATUS_DONE,
    NODE_STATUS_SKIPPED,
    WORKFLOW_COMPLETED,
    NodeInstance,
    WorkflowConfig,
    WorkflowEngine,
    default_workflow,
)
from core.workflow_registry import (  # noqa: E402
    NODE_REGISTRY,
    get_node_spec,
    validate_registry,
)

# Folder names under the SAME contract the uploaders parse (never guessed).
FOLDER_JUAN = "DELA CRUZ, JUAN - 123456789012345 - ADM20260901_DIS20260903"
FOLDER_MARIA = "SANTOS, MARIA - 000000000021401 - ADM20260906_DIS20260912"
FOLDER_B = "BAQUIRAN, JAYDE - 000000000010049 - ADM20260921_DIS20260926"
NOT_A_PATIENT = "random_subdir"


def make_root(*names: str) -> tuple[TemporaryDirectory, Path]:
    """Temp output root containing the given sub-entries (created in order)."""
    tmp = TemporaryDirectory()
    root = Path(tmp.name)
    for name in names:
        (root / name).mkdir()
    return tmp, root


def pending_fees_rows(*folder_names: str) -> list[dict]:
    """Fees rows marking the given folders as NO FINAL BILL."""
    return [
        {"Patient Folder": name, "Status": "NO FINAL BILL"}
        for name in folder_names
    ]


def write_date_fill_log(
    path: Path, *rows: tuple[str, str, str]
) -> None:
    """Write a Date Fill run log in the REAL csv format.

    Folder names contain commas, so the fields must be
    csv-quoted exactly like the tool's write_run_log —
    an unquoted hand-written row would split the folder
    name at its comma and the gate would never match.
    """
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["patient_name", "output_folder_name", "status"])
        for row in rows:
            writer.writerow(row)


class RegistryTests(unittest.TestCase):
    """The catalog entry drives adapters/engine/GUI — its shape is the contract."""

    def test_final_bill_spec_fields(self):
        spec = get_node_spec("final_bill")
        self.assertIsNotNone(spec, "final_bill must be registered")
        self.assertEqual(spec.category, "HBSys")
        self.assertEqual(spec.module, "core.agent.final_bill_runner")
        self.assertEqual(spec.args, ())
        self.assertEqual(spec.live_args, ("--live",))
        self.assertTrue(spec.hbsys_touching, "drives the HBSys Billing screen")
        self.assertTrue(spec.supports_dry, "DRY listing must be safe")
        self.assertNotEqual(spec.label, "")

    def test_registry_has_13_entries_and_validates(self):
        self.assertEqual(len(NODE_REGISTRY), 13)
        self.assertEqual(validate_registry(), [], "entry points must exist on disk")

    def test_registry_hbsys_set_includes_final_bill(self):
        hbsys = {k for k, s in NODE_REGISTRY.items() if s.hbsys_touching}
        self.assertIn("final_bill", hbsys)

    def test_build_command_dry_and_live(self):
        spec = get_node_spec("final_bill")
        dry = build_command(spec, live=False)
        live = build_command(spec, live=True)
        self.assertEqual(dry, [sys.executable, "-m", "core.agent.final_bill_runner"])
        self.assertEqual(live, dry + ["--live"])


class DefaultWorkflowTests(unittest.TestCase):
    """The default order mirrors the manual pipeline: Final Bill BEFORE Date Fill."""

    def test_default_order_places_final_bill_second(self):
        config = default_workflow()
        types = [n.node_type for n in config.nodes]
        self.assertEqual(types, [
            "claims_processor",
            "final_bill",
            "date_fill_regular",
            "xml_clicker",
            "copy_xml",
            "fees_checker",
            "claims_checker",
            "add_claims_upload",
            "claim_attachments",
        ])
        self.assertEqual([n.order for n in config.nodes], list(range(1, 10)))

    def test_default_connections_chain_nine_nodes(self):
        config = default_workflow()
        self.assertEqual(len(config.connections), 8)
        self.assertTrue(all(c.enabled for c in config.connections))
        self.assertEqual(config.connections[0].source_id, "n1")
        self.assertEqual(config.connections[-1].target_id, "n9")

    def test_date_fill_nodes_continue_on_fail(self):
        # 2026-10-09: a Date Fill review stop (exit 1) must
        # not stop the chain — Final Bill follows Date Fill
        # per the workflow. The Final Bill node's own Date
        # Fill gate keeps unverified folders out of billing.
        config = default_workflow()
        params = {n.node_type: n.params for n in config.nodes}
        self.assertTrue(params["date_fill_regular"]["continue_on_fail"])
        self.assertEqual(params["final_bill"], {})
        # The ABTC twin carries the same default even though
        # it is not part of the default chain.
        self.assertTrue(
            DEFAULT_NODE_PARAMS["date_fill_abtc"]["continue_on_fail"]
        )


class FolderContractTests(unittest.TestCase):
    """Folder name is the ONLY hospital-no source (Option B, never guessed)."""

    def test_iter_patient_folders_sorted_and_filtered(self):
        # Created OUT of order + one non-contract dir + one file: the result
        # must be contract folders only, ascending by name.
        tmp, root = make_root(FOLDER_MARIA, NOT_A_PATIENT, FOLDER_JUAN)
        try:
            (root / "stray_file.txt").write_text("x", encoding="utf-8")
            folders = runner.iter_patient_folders(root)
            self.assertEqual([f.name for f in folders], [FOLDER_JUAN, FOLDER_MARIA])
        finally:
            tmp.cleanup()

    def test_folder_without_adm_dis_is_excluded(self):
        # No ADM..._DIS... dates -> parse_folder_name returns None -> skipped.
        tmp, root = make_root("DELA CRUZ, JUAN - 123456789012345")
        try:
            self.assertEqual(runner.iter_patient_folders(root), [])
        finally:
            tmp.cleanup()

    def test_missing_output_root_is_empty_not_an_error(self):
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope"
            self.assertEqual(runner.iter_patient_folders(missing), [])

    def test_hospital_no_parsed_from_folder_name(self):
        from core.add_claims_verifier import parse_folder_name

        parsed = parse_folder_name(FOLDER_JUAN)
        self.assertEqual(parsed.hospital_no, "123456789012345")
        self.assertEqual(parsed.admission.strftime("%Y%m%d"), "20260901")
        self.assertEqual(parsed.discharge.strftime("%Y%m%d"), "20260903")


class MarkerTests(unittest.TestCase):
    """.final_bill_ok = resume gate: written ONLY on OK, --force overrides."""

    def test_marker_written_only_on_ok(self):
        tmp, root = make_root(FOLDER_JUAN)
        try:
            folder = root / FOLDER_JUAN
            # BLOCKED/FAILED never write a marker (next run must retry).
            self.assertFalse(runner.write_marker(folder, "123", "BLOCKED", "hbsys closed"))
            self.assertFalse(runner.marker_path(folder).exists())
            self.assertFalse(runner.write_marker(folder, "123", "FAILED", "click missed"))
            self.assertFalse(runner.marker_path(folder).exists())
            # OK writes a JSON marker carrying the audit trail.
            self.assertTrue(runner.write_marker(folder, "123", "OK", "final bill committed"))
            payload = json.loads(runner.marker_path(folder).read_text(encoding="utf-8"))
            self.assertEqual(payload["hospital_no"], "123")
            self.assertEqual(payload["outcome"], "OK")
            self.assertIn("at", payload)
        finally:
            tmp.cleanup()

    def test_needs_final_bill_gate_and_force(self):
        tmp, root = make_root(FOLDER_JUAN, FOLDER_MARIA)
        try:
            folder = root / FOLDER_JUAN
            self.assertTrue(runner.needs_final_bill(folder), "no marker -> pending")
            runner.write_marker(folder, "123", "OK", "done")
            self.assertFalse(runner.needs_final_bill(folder), "marked -> skipped")
            self.assertTrue(
                runner.needs_final_bill(folder, force=True),
                "--force re-runs even marked folders",
            )
            # The OTHER folder keeps its own gate (markers are per-folder).
            self.assertTrue(runner.needs_final_bill(root / FOLDER_MARIA))
        finally:
            tmp.cleanup()


class DryRunTests(unittest.TestCase):
    """DRY = listing only: no markers, no desktop, no pywinauto import."""

    def test_dry_lists_pending_and_writes_no_marker(self):
        tmp, root = make_root(FOLDER_JUAN, FOLDER_MARIA)
        try:
            runner.write_marker(root / FOLDER_MARIA, "999", "OK", "already billed")
            out = io.StringIO()
            with mock.patch.object(
                runner,
                "run_fees_check",
                return_value=pending_fees_rows(FOLDER_JUAN, FOLDER_MARIA),
            ):
                with redirect_stdout(out):
                    code = runner.main(["--output-root", str(root)])
            self.assertEqual(code, runner.EXIT_OK)
            text = out.getvalue()
            self.assertIn("[DRY]", text)
            self.assertIn(FOLDER_JUAN, text, "pending folder must be listed")
            self.assertNotIn(FOLDER_MARIA, text, "marked folder must be skipped")
            self.assertIn("1 pending", text)
            self.assertFalse(
                runner.marker_path(root / FOLDER_JUAN).exists(),
                "DRY must never write markers",
            )
        finally:
            tmp.cleanup()

    def test_dry_never_imports_pywinauto(self):
        # Subprocess = hermetic import check: other suite modules may already
        # have pywinauto in THIS interpreter's sys.modules.
        with TemporaryDirectory() as tmp:
            script = (
                "import sys; "
                "from core.agent import final_bill_runner as r; "
                f"code = r.main(['--output-root', {tmp!r}]); "
                "print('PYWINATO_LOADED' if 'pywinauto' in sys.modules else 'CLEAN'); "
                f"sys.exit(code)"
            )
            proc = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True, text=True, timeout=120,
                cwd=str(PROJECT_ROOT),
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("CLEAN", proc.stdout)


class ExitCodeTests(unittest.TestCase):
    """Exit 1 = engine stops the chain (fail-safe); exit 0 = all OK / nothing."""

    def test_all_ok_exits_zero_and_marks_every_folder(self):
        tmp, root = make_root(FOLDER_B, FOLDER_JUAN, FOLDER_MARIA)
        try:
            with mock.patch.object(
                runner, "run_patient", return_value=("OK", "final bill committed")
            ) as fake, mock.patch.object(
                runner,
                "run_fees_check",
                return_value=pending_fees_rows(FOLDER_B, FOLDER_JUAN, FOLDER_MARIA),
            ):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = runner.main(["--live", "--output-root", str(root)])
            self.assertEqual(code, runner.EXIT_OK)
            self.assertEqual(fake.call_count, 3)
            for name in (FOLDER_B, FOLDER_JUAN, FOLDER_MARIA):
                self.assertTrue(
                    runner.marker_path(root / name).exists(),
                    f"OK must mark {name}",
                )
            self.assertIn("all 3 patient(s) OK", out.getvalue())
        finally:
            tmp.cleanup()

    def test_one_blocked_of_three_exits_one_and_skips_its_marker(self):
        tmp, root = make_root(FOLDER_B, FOLDER_JUAN, FOLDER_MARIA)
        try:
            # Ascending order: B -> JUAN -> MARIA; the MIDDLE patient blocks.
            with mock.patch.object(
                runner,
                "run_patient",
                side_effect=[("OK", "done"), ("BLOCKED", "hbsys closed"), ("OK", "done")],
            ), mock.patch.object(
                runner,
                "run_fees_check",
                return_value=pending_fees_rows(FOLDER_B, FOLDER_JUAN, FOLDER_MARIA),
            ):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = runner.main(["--live", "--output-root", str(root)])
            self.assertEqual(code, runner.EXIT_NOT_OK, "any BLOCKED must stop the chain")
            self.assertTrue(runner.marker_path(root / FOLDER_B).exists())
            self.assertFalse(
                runner.marker_path(root / FOLDER_JUAN).exists(),
                "BLOCKED must NOT be marked (next run retries)",
            )
            self.assertTrue(runner.marker_path(root / FOLDER_MARIA).exists())
            # Continue-on-error: the patient AFTER the blocked one still ran.
            self.assertIn("1 of 3 patient(s) need attention", out.getvalue())
        finally:
            tmp.cleanup()

    def test_system_exit_does_not_halt_the_batch(self):
        # A SystemExit (e.g. a step calling sys.exit() on a confinement mismatch)
        # must NOT halt the --live batch: it becomes a FAILED row and the NEXT
        # patient still runs. Modeled on
        # test_one_blocked_of_three_exits_one_and_skips_its_marker.
        tmp, root = make_root(FOLDER_B, FOLDER_JUAN)
        try:
            with mock.patch.object(
                runner,
                "run_patient",
                side_effect=[
                    SystemExit("confinement mismatch: ADM-DIS not selectable"),
                    ("OK", "done"),
                ],
            ), mock.patch.object(
                runner,
                "run_fees_check",
                return_value=pending_fees_rows(FOLDER_B, FOLDER_JUAN),
            ):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = runner.main(["--live", "--output-root", str(root)])
            self.assertEqual(
                code, runner.EXIT_NOT_OK,
                "a FAILED row still stops the chain (fail-safe exit 1)",
            )
            self.assertFalse(
                runner.marker_path(root / FOLDER_B).exists(),
                "SystemExit must NOT produce an OK marker (next run retries it)",
            )
            self.assertTrue(
                runner.marker_path(root / FOLDER_JUAN).exists(),
                "the patient AFTER a SystemExit must still run (continue-on-error)",
            )
            # The full traceback reaches stdout so the exact mismatch is visible.
            self.assertIn("Traceback", out.getvalue())
        finally:
            tmp.cleanup()

    def test_all_marked_is_nothing_to_do_exit_zero(self):
        tmp, root = make_root(FOLDER_JUAN)
        try:
            runner.write_marker(root / FOLDER_JUAN, "123", "OK", "already billed")
            with mock.patch.object(runner, "run_patient") as fake:
                out = io.StringIO()
                with redirect_stdout(out):
                    code = runner.main(["--live", "--output-root", str(root)])
            self.assertEqual(code, runner.EXIT_OK)
            fake.assert_not_called(), "marked folders must never re-run"
            self.assertIn("nothing to do", out.getvalue())
        finally:
            tmp.cleanup()


class LimitTests(unittest.TestCase):
    """--limit caps the pending slice (safe first-live-run rehearsal)."""

    def test_limit_processes_only_n_pending(self):
        tmp, root = make_root(FOLDER_B, FOLDER_JUAN, FOLDER_MARIA)
        try:
            with mock.patch.object(
                runner, "run_patient", return_value=("OK", "done")
            ) as fake, mock.patch.object(
                runner,
                "run_fees_check",
                return_value=pending_fees_rows(FOLDER_B, FOLDER_JUAN, FOLDER_MARIA),
            ):
                with redirect_stdout(io.StringIO()):
                    code = runner.main(
                        ["--live", "--limit", "2", "--output-root", str(root)]
                    )
            self.assertEqual(code, runner.EXIT_OK)
            self.assertEqual(fake.call_count, 2, "--limit 2 must run exactly 2")
            # Ascending order -> the first two folders were picked.
            run_folders = [c.args[0].name for c in fake.call_args_list]
            self.assertEqual(run_folders, [FOLDER_B, FOLDER_JUAN])
        finally:
            tmp.cleanup()


class FeesGateTests(unittest.TestCase):
    """2026-10-08: only NO FINAL BILL folders may be final-billed.

    Before this gate the node billed EVERY unmarked folder, so
    patients already final-billed in HBSys (MATCH) and MISMATCH
    rows were re-final-billed. The node now runs the Fees Check
    silently first (no CSV/XLSX) and bills only NO FINAL BILL.
    """

    @staticmethod
    def _row(folder: str, status: str) -> dict:
        return {"Patient Folder": folder, "Status": status}

    def test_status_map_keys_on_patient_folder_name(self):
        rows = [
            self._row(FOLDER_JUAN, "NO FINAL BILL"),
            self._row(FOLDER_MARIA, "MATCH"),
            {"Patient Folder": "", "Status": "MATCH"},
        ]
        self.assertEqual(
            runner.fees_status_map(rows),
            {FOLDER_JUAN: "NO FINAL BILL", FOLDER_MARIA: "MATCH"},
        )

    def test_only_no_final_bill_is_pending(self):
        tmp, root = make_root(FOLDER_JUAN, FOLDER_MARIA, FOLDER_B)
        try:
            folders = runner.iter_patient_folders(root)
            status_map = {
                FOLDER_JUAN: "NO FINAL BILL",
                FOLDER_MARIA: "MATCH",      # already final-billed
                FOLDER_B: "MISMATCH",       # operator review
            }
            pending, skipped = runner.select_final_bill_folders(
                folders, status_map
            )
            self.assertEqual([f.name for f in pending], [FOLDER_JUAN])
            self.assertEqual(
                [(f.name, s) for f, s in skipped],
                [(FOLDER_B, "MISMATCH"), (FOLDER_MARIA, "MATCH")],
            )
        finally:
            tmp.cleanup()

    def test_folder_without_a_fees_row_is_skipped_not_guessed(self):
        folder = Path("out") / FOLDER_JUAN
        pending, skipped = runner.select_final_bill_folders([folder], {})
        self.assertEqual(pending, [])
        self.assertEqual(skipped, [(folder, "")])
        self.assertIn(
            "walang fees check row", runner.final_bill_skip_reason("")
        )

    def test_force_overrides_the_marker_but_never_the_status_gate(self):
        tmp, root = make_root(FOLDER_MARIA)
        try:
            folder = root / FOLDER_MARIA
            runner.write_marker(folder, "000000000021401", "OK", "done")
            self.assertTrue(runner.needs_final_bill(folder, force=True))
            pending, _skipped = runner.select_final_bill_folders(
                [folder], {FOLDER_MARIA: "MATCH"}
            )
            self.assertEqual(
                pending, [], "--force must not re-bill an already-billed patient"
            )
        finally:
            tmp.cleanup()

    def test_main_dry_skips_already_billed_and_mismatch_patients(self):
        tmp, root = make_root(FOLDER_JUAN, FOLDER_MARIA, FOLDER_B)
        try:
            rows = [
                self._row(FOLDER_JUAN, "NO FINAL BILL"),
                self._row(FOLDER_MARIA, "MATCH"),
                self._row(FOLDER_B, "MISMATCH"),
            ]
            out = io.StringIO()
            with mock.patch.object(
                runner, "run_fees_check", return_value=rows
            ), redirect_stdout(out):
                code = runner.main(["--output-root", str(root)])
            self.assertEqual(code, runner.EXIT_OK)
            text = out.getvalue()
            # The NO FINAL BILL patient is the only one listed to run.
            self.assertIn(f"[DRY] {FOLDER_JUAN}", text)
            self.assertNotIn(f"[DRY] {FOLDER_MARIA}", text)
            self.assertNotIn(f"[DRY] {FOLDER_B}", text)
            # The other two are skipped with status + reason.
            self.assertIn("[SKIP]", text)
            self.assertIn("MISMATCH", text)
            self.assertIn("manual review", text)
            self.assertIn("final bill na sa HBSys", text)
            self.assertFalse(runner.marker_path(root / FOLDER_JUAN).exists())
        finally:
            tmp.cleanup()

    def test_main_live_runs_only_the_no_final_bill_patient(self):
        tmp, root = make_root(FOLDER_JUAN, FOLDER_MARIA)
        try:
            rows = [
                self._row(FOLDER_JUAN, "NO FINAL BILL"),
                self._row(FOLDER_MARIA, "MATCH"),
            ]
            out = io.StringIO()
            with mock.patch.object(
                runner, "run_patient", return_value=("OK", "done")
            ) as fake, mock.patch.object(
                runner, "run_fees_check", return_value=rows
            ):
                with redirect_stdout(out):
                    code = runner.main(["--live", "--output-root", str(root)])
            self.assertEqual(code, runner.EXIT_OK)
            # Only JUAN reached the Final Bill flow; MARIA (already
            # final-billed) was never touched.
            self.assertEqual(fake.call_count, 1)
            self.assertEqual(fake.call_args.args[0].name, FOLDER_JUAN)
            self.assertTrue(runner.marker_path(root / FOLDER_JUAN).exists())
            self.assertFalse(runner.marker_path(root / FOLDER_MARIA).exists())
        finally:
            tmp.cleanup()

    def test_main_stops_when_the_fees_check_fails(self):
        tmp, root = make_root(FOLDER_JUAN)
        try:
            with mock.patch.object(
                runner,
                "run_fees_check",
                side_effect=RuntimeError("HBSys unreachable"),
            ):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = runner.main(["--live", "--output-root", str(root)])
            self.assertEqual(code, runner.EXIT_NOT_OK)
            self.assertIn("Fees Check preflight failed", out.getvalue())
            self.assertFalse(runner.marker_path(root / FOLDER_JUAN).exists())
        finally:
            tmp.cleanup()

    def test_run_fees_check_is_silent_and_scans_the_node_root(self):
        import fees_checker

        tmp, root = make_root(FOLDER_JUAN)
        try:
            original_root = fees_checker.OUTPUT_DIR
            seen_roots: list[Path] = []

            def fake_run_check(**kwargs):
                seen_roots.append(fees_checker.OUTPUT_DIR)
                # The preflight must stay silent: no reports.
                self.assertFalse(kwargs.get("write_reports", True))
                return [], None, None

            with mock.patch.object(
                fees_checker, "run_check", side_effect=fake_run_check
            ) as fake_check:
                rows = runner.run_fees_check(root)
            self.assertEqual(rows, [])
            fake_check.assert_called_once_with(write_reports=False)
            # The node's root was scanned ...
            self.assertEqual(seen_roots, [Path(root)])
            # ... and the fees checker default was restored.
            self.assertEqual(fees_checker.OUTPUT_DIR, original_root)
        finally:
            tmp.cleanup()


class DateFillPolicyTests(unittest.TestCase):
    """2026-10-09 (operator decision): Final Bill follows
    Date Fill UNCONDITIONALLY — a folder Date Fill did NOT
    verify is still final-billed (final billing does not
    require the CF2 date fill; the engine reaches this node
    via continue_on_fail). This test pins the policy: an
    unverified status in the newest Date Fill run log must
    NOT stop the folder from flowing to the Final Bill flow."""

    def test_unverified_in_date_fill_log_still_flows(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / FOLDER_JUAN).mkdir()
            logs = root / "logs"
            logs.mkdir()
            write_date_fill_log(
                logs / "hbsys_fill_run_20261009_110000.csv",
                (
                    "JUAN",
                    FOLDER_JUAN,
                    "SKIPPED_ADMISSION_HISTORY_MISMATCH",
                ),
            )
            out = io.StringIO()
            with mock.patch.object(
                runner, "run_fees_check",
                return_value=pending_fees_rows(FOLDER_JUAN),
            ), redirect_stdout(out):
                code = runner.main(["--output-root", str(root)])
            self.assertEqual(code, runner.EXIT_OK)
            self.assertIn(
                f"[DRY] {FOLDER_JUAN} | hospital no", out.getvalue()
            )

class EnvTests(unittest.TestCase):
    """Output root: --output-root flag > CLAIMS_OUTPUT_FOLDER env > default."""

    def test_output_root_flag_overrides_env(self):
        tmp_a, root_a = make_root(FOLDER_JUAN)
        tmp_b, root_b = make_root(FOLDER_MARIA)
        try:
            with mock.patch.dict(os.environ, {"CLAIMS_OUTPUT_FOLDER": str(root_a)}), \
                    mock.patch.object(
                        runner,
                        "run_fees_check",
                        return_value=pending_fees_rows(FOLDER_MARIA),
                    ):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = runner.main(["--output-root", str(root_b)])
            self.assertEqual(code, runner.EXIT_OK)
            self.assertIn(FOLDER_MARIA, out.getvalue())
            self.assertNotIn(FOLDER_JUAN, out.getvalue())
        finally:
            tmp_a.cleanup()
            tmp_b.cleanup()

    def test_env_output_root_is_honoured(self):
        tmp, root = make_root(FOLDER_JUAN)
        try:
            with mock.patch.dict(os.environ, {"CLAIMS_OUTPUT_FOLDER": str(root)}), \
                    mock.patch.object(
                        runner,
                        "run_fees_check",
                        return_value=pending_fees_rows(FOLDER_JUAN),
                    ):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = runner.main([])
            self.assertEqual(code, runner.EXIT_OK)
            self.assertIn(FOLDER_JUAN, out.getvalue())
        finally:
            tmp.cleanup()

    def test_missing_output_root_exits_not_ok(self):
        with TemporaryDirectory() as tmp:
            out = io.StringIO()
            with redirect_stdout(out):
                code = runner.main(["--output-root", str(Path(tmp) / "nope")])
            self.assertEqual(code, runner.EXIT_NOT_OK, "bad root must stop the chain")
            self.assertIn("output root not found", out.getvalue())


class EngineTests(unittest.TestCase):
    """The engine's HBSys gate applies to final_bill like any UI node."""

    @staticmethod
    def _single_node_config() -> WorkflowConfig:
        return WorkflowConfig(
            nodes=[NodeInstance(id="n1", node_type="final_bill", order=1)],
            connections=[],
        )

    def test_engine_skips_final_bill_when_hbsys_unavailable(self):
        lines: list[str] = []
        engine = WorkflowEngine(on_output=lines.append)
        with mock.patch.object(WorkflowEngine, "_hbsys_available", lambda self: False):
            report = engine.run(self._single_node_config(), live=True)
        self.assertEqual(report.result, WORKFLOW_COMPLETED)
        node = report.node_results[0]
        self.assertEqual(node.status, NODE_STATUS_SKIPPED)
        self.assertIn("HBSys window not found", node.reason)
        self.assertEqual(lines, [], "skipped node must never launch a subprocess")

    def test_engine_dry_run_completes_final_bill_node(self):
        tmp, root = make_root(FOLDER_JUAN)
        try:
            lines: list[str] = []
            engine = WorkflowEngine(
                settings_getter=lambda: {"output_folder": str(root)},
                on_output=lines.append,
            )
            with mock.patch.object(WorkflowEngine, "_hbsys_available", lambda self: True):
                report = engine.run(self._single_node_config(), live=False)
            self.assertEqual(report.result, WORKFLOW_COMPLETED)
            node = report.node_results[-1]  # RUNNING is recorded first
            self.assertEqual(node.node_type, "final_bill")
            self.assertEqual(node.status, NODE_STATUS_DONE, node.reason)
            self.assertTrue(
                any("[DRY]" in line for line in lines),
                f"runner stdout must stream into the engine log: {lines}",
            )
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
