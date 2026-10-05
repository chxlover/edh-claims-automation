"""GUI tests for the Agent Plan Panel (Slice C — Claims Agent).

Instantiates the real AgentPlanFrame (window withdrawn so it does not
flash) with a temp Fees CSV. NEVER touches HBSys — the panel is
read-only by contract; messagebox calls are stubbed.

Run from the project root:

    python -m unittest tests.test_gui_agent_plan
    python tests/test_gui_agent_plan.py
"""

from __future__ import annotations

import csv
import json
import sys
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import agent_plan_store as plan_store  # noqa: E402
from core.agent import orchestrator  # noqa: E402
from gui import agent_plan_tab  # noqa: E402
from gui.agent_plan_tab import AgentPlanFrame  # noqa: E402

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



class AgentPlanPanelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.csv_path = self.root / "fees.csv"
        with open(self.csv_path, "w", newline="",
                  encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADERS)
            writer.writeheader()
            writer.writerow(ready_row("READY PATIENT"))
            writer.writerow(dict(
                ready_row("FILL PATIENT"),
                **{"Consent Date (hpatcon1.consentdate)": "",
                   "Auth Sign Date (hpatcon1.authsigndate)": "",
                   "Ready to Generate XML": "NO"},
            ))

        self.logs_before = set(
            Path("C:/claims_bot/logs").glob("agent_plan_*.json")
        )
        self.tk_root = tk.Tk()
        self.tk_root.withdraw()
        self.frame = AgentPlanFrame(
            self.tk_root,
            settings_getter=lambda: {},
            log_callback=lambda msg: None,
            background=False,  # synchronous execution so tests are deterministic
        )
        self.frame.pack(fill="both", expand=True)
        self.tk_root.update_idletasks()

        self._real_showinfo = agent_plan_tab.messagebox.showinfo
        agent_plan_tab.messagebox.showinfo = (
            lambda *args, **kwargs: None
        )
        # Slice E: approve_selected asks for confirmation before executing.
        # Default to NO so the approval tests never trigger a run.
        self._real_askyesno = agent_plan_tab.messagebox.askyesno
        agent_plan_tab.messagebox.askyesno = (
            lambda *args, **kwargs: False
        )
        # Fees Check preflight failure path uses showerror.
        self._real_showerror = agent_plan_tab.messagebox.showerror
        agent_plan_tab.messagebox.showerror = (
            lambda *args, **kwargs: None
        )
        # The unfinished-run warning reads a heartbeat file: point it at the
        # temp folder so no test depends on the real logs/ directory.
        self.frame.state_file = self.root / "no_heartbeat.json"

    def tearDown(self):
        agent_plan_tab.messagebox.showinfo = self._real_showinfo
        agent_plan_tab.messagebox.askyesno = self._real_askyesno
        agent_plan_tab.messagebox.showerror = self._real_showerror
        try:
            self.tk_root.destroy()
        except Exception:  # noqa: BLE001 - cleanup must not mask failures.
            pass
        # Remove any plan files this test saved (audit trail stays clean).
        for plan_file in Path("C:/claims_bot/logs").glob("agent_plan_*.json"):
            if plan_file not in self.logs_before:
                try:
                    plan_file.unlink()
                except OSError:
                    pass
        self.temporary.cleanup()

    def test_load_plan_renders_rows_and_summary(self):
        self.frame.fees_csv_var.set(str(self.csv_path))
        self.frame.load_plan()

        self.assertEqual(len(self.frame.items), 2)
        self.assertEqual(len(self.frame.plan_tree.get_children()), 2)
        self.assertIn("XML Clicker: 1", self.frame.summary_var.get())
        self.assertIn("Date Fill: 1", self.frame.summary_var.get())
        self.assertEqual(
            str(self.frame.approve_btn.cget("state")), "normal"
        )

    def test_missing_csv_disables_approve(self):
        self.frame.fees_csv_var.set(str(self.root / "nope.csv"))
        self.frame.load_plan()

        self.assertEqual(self.frame.items, [])
        self.assertIn("Fees Check", self.frame.note_var.get())
        self.assertEqual(
            str(self.frame.approve_btn.cget("state")), "disabled"
        )

    def test_approve_selected_persists_only(self):
        self.frame.fees_csv_var.set(str(self.csv_path))
        self.frame.load_plan()
        children = self.frame.plan_tree.get_children()
        self.frame.plan_tree.selection_set(children[0])
        self.frame.approve_selected()

        approved = [
            item for item in self.frame.items
            if item["status"] == "APPROVED"
        ]
        skipped = [
            item for item in self.frame.items
            if item["status"] == "SKIPPED"
        ]
        self.assertEqual(len(approved), 1)
        self.assertEqual(len(skipped), 1)
        # Status column in the tree reflects the approval.
        statuses = [
            self.frame.plan_tree.item(child, "values")[3]
            for child in self.frame.plan_tree.get_children()
        ]
        self.assertEqual(statuses, ["APPROVED", "SKIPPED"])
        # A JSON audit trail was saved (execution needs askyesno=YES here).
        logs_dir = Path("C:/claims_bot/logs")
        new_plans = [
            plan_file for plan_file in logs_dir.glob("agent_plan_*.json")
            if plan_file not in self.logs_before
        ]
        self.assertEqual(len(new_plans), 1)
        # askyesno defaulted to NO -> nothing executed.
        self.assertNotIn("Run finished:", self.frame.log_text.get("1.0", "end"))

    def test_approve_and_run_executes_approved_rows(self):
        agent_plan_tab.messagebox.askyesno = lambda *args, **kwargs: True
        calls = {}

        def fake_run(rows, *, log_fn=None, **kwargs):
            calls["rows"] = list(rows)
            log_fn("fake orchestrator line")
            return orchestrator.RunReport(
                outcomes=[
                    orchestrator.RowOutcome(
                        rows[0].get("patient_folder", ""),
                        rows[0].get("action", ""),
                        status=orchestrator.OUTCOME_OK,
                        detail="fake done",
                    )
                ]
            )

        self.frame.run_plan_fn = fake_run
        self.frame.fees_csv_var.set(str(self.csv_path))
        self.frame.load_plan()
        children = self.frame.plan_tree.get_children()
        self.frame.plan_tree.selection_set(children[0])
        self.frame.approve_selected()

        # The approved rows reached the orchestrator, in approval shape.
        self.assertEqual(len(calls["rows"]), 1)
        self.assertEqual(calls["rows"][0]["status"], "APPROVED")
        # Progress + summary landed in the panel log.
        log_text = self.frame.log_text.get("1.0", "end")
        self.assertIn("fake orchestrator line", log_text)
        self.assertIn("Run finished:", log_text)
        self.assertIn("OK 1", log_text)
        # Approve button re-enabled after the run.
        self.assertEqual(
            str(self.frame.approve_btn.cget("state")), "normal"
        )

    def test_load_plan_drops_rows_completed_in_ledger(self):
        ledger = self.root / "agent_completed_actions.json"
        plan_store.record_completed_actions(
            [("READY PATIENT", agent_plan_tab.actions.ACTION_XML_CLICKER)],
            path=ledger,
        )
        self.frame.fees_csv_var.set(str(self.csv_path))
        with mock.patch.object(plan_store, "COMPLETED_LEDGER", ledger):
            self.frame.load_plan()

        self.assertEqual(len(self.frame.items), 1)
        self.assertEqual(self.frame.items[0]["patient_folder"], "FILL PATIENT")
        self.assertIn("hindi na inuulit", self.frame.note_var.get())

    def test_run_finished_reloads_and_drops_completed(self):
        agent_plan_tab.messagebox.askyesno = lambda *args, **kwargs: True
        ledger = self.root / "agent_completed_actions.json"

        def fake_run(rows, *, log_fn=None, **kwargs):
            # Mimic the real orchestrator: OK rows go to the completion ledger.
            for row in rows:
                plan_store.record_completed_actions(
                    [(row.get("patient_folder", ""), row.get("action", ""))],
                    path=ledger,
                )
            return orchestrator.RunReport(
                outcomes=[
                    orchestrator.RowOutcome(
                        rows[0].get("patient_folder", ""),
                        rows[0].get("action", ""),
                        status=orchestrator.OUTCOME_OK,
                        detail="fake done",
                    )
                ]
            )

        self.frame.run_plan_fn = fake_run
        self.frame.fees_csv_var.set(str(self.csv_path))
        self.frame.load_plan()  # both rows visible before the run
        self.assertEqual(len(self.frame.items), 2)
        children = self.frame.plan_tree.get_children()
        self.frame.plan_tree.selection_set(children[0])  # READY PATIENT

        with mock.patch.object(plan_store, "COMPLETED_LEDGER", ledger):
            self.frame.approve_selected()  # sync run -> _run_finished reloads

        # READY PATIENT ran OK -> recorded -> dropped by the reload.
        self.assertEqual(len(self.frame.items), 1)
        self.assertEqual(self.frame.items[0]["patient_folder"], "FILL PATIENT")
        self.assertIn("hindi na inuulit", self.frame.note_var.get())
        self.assertEqual(
            str(self.frame.approve_btn.cget("state")), "normal"
        )

    # -- End-of-run report of the patients that were skipped (2026-09-28) ---
    # Operator rule: a problem row never stops the run, but the run must END by
    # naming the patients that were not finalized, so the operator does not
    # have to open the JSON to find out who was skipped.

    def test_run_finished_warns_and_lists_the_skipped_patients(self):
        agent_plan_tab.messagebox.askyesno = lambda *args, **kwargs: True
        shown = []
        agent_plan_tab.messagebox.showwarning = (
            lambda *args, **kwargs: shown.append(args)
        )
        agent_plan_tab.messagebox.showinfo = lambda *a, **k: shown.append(a)

        def fake_run(rows, *, log_fn=None, **kwargs):
            return orchestrator.RunReport(
                outcomes=[
                    orchestrator.RowOutcome(
                        "READY PATIENT", "final_bill", hospital_no="111",
                        status=orchestrator.OUTCOME_OK, detail="finalized",
                    ),
                    orchestrator.RowOutcome(
                        "FILL PATIENT", "final_bill", hospital_no="222",
                        status=orchestrator.OUTCOME_BLOCKED,
                        detail="Billing form is not open",
                    ),
                ],
                saved_path="logs/agent_run_X.json",
            )

        self.frame.run_plan_fn = fake_run
        self.frame.fees_csv_var.set(str(self.csv_path))
        self.frame.load_plan()
        self.frame.plan_tree.selection_set(self.frame.plan_tree.get_children())
        self.frame.approve_selected()

        # A WARNING (not the plain "finished" info box) names the patient.
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0][0], "Agent Plan — may hindi na-finalize")
        body = shown[0][1]
        self.assertIn("222", body)
        self.assertIn("FILL PATIENT", body)
        self.assertIn("Billing form is not open", body)
        # ...and the same names are in the panel log.
        log_text = self.frame.log_text.get("1.0", "end")
        self.assertIn("NOT FINALIZED 222", log_text)
        self.assertIn("Billing form is not open", log_text)

    def test_run_finished_confirms_when_every_patient_was_finalized(self):
        agent_plan_tab.messagebox.askyesno = lambda *args, **kwargs: True
        infos, warns = [], []
        agent_plan_tab.messagebox.showinfo = lambda *a, **k: infos.append(a)
        agent_plan_tab.messagebox.showwarning = lambda *a, **k: warns.append(a)

        def fake_run(rows, *, log_fn=None, **kwargs):
            return orchestrator.RunReport(
                outcomes=[
                    orchestrator.RowOutcome(
                        "READY PATIENT", "final_bill", hospital_no="111",
                        status=orchestrator.OUTCOME_OK, detail="finalized",
                    )
                ]
            )

        self.frame.run_plan_fn = fake_run
        self.frame.fees_csv_var.set(str(self.csv_path))
        self.frame.load_plan()
        self.frame.plan_tree.selection_set(self.frame.plan_tree.get_children())
        self.frame.approve_selected()

        self.assertEqual(warns, [])
        self.assertEqual(len(infos), 1)
        self.assertIn("Lahat ng napiling patient ay na-finalize", infos[0][1])

    # -- Load Plan preflight: Fees Check first (user request 2026-09-26) ---

    def _write_extra_csv(self, name, folders):
        path = self.root / name
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=HEADERS)
            writer.writeheader()
            for folder in folders:
                writer.writerow(ready_row(folder))
        return path

    def test_on_load_plan_preflight_runs_fees_check_first(self):
        calls = []
        fresh = self._write_extra_csv("fresh_report.csv", ["FRESH PATIENT"])

        def fake_check():
            calls.append(1)
            return [], fresh, Path("fresh.xlsx")

        self.frame.fees_check_fn = fake_check
        self.frame.fees_csv_var.set(str(self.csv_path))
        self.frame.on_load_plan()  # background=False -> synchronous

        self.assertEqual(len(calls), 1)
        self.assertEqual(self.frame.fees_csv_var.get(), str(fresh))
        self.assertEqual(len(self.frame.items), 1)
        self.assertEqual(
            self.frame.items[0]["patient_folder"], "FRESH PATIENT"
        )
        self.assertEqual(
            str(self.frame.load_btn.cget("state")), "normal"
        )

    def test_on_load_plan_preflight_unchecked_skips_fees_check(self):
        calls = []
        self.frame.fees_check_fn = lambda: calls.append(1)
        self.frame.preflight_var.set(False)
        self.frame.fees_csv_var.set(str(self.csv_path))
        self.frame.on_load_plan()

        self.assertEqual(calls, [])
        self.assertEqual(len(self.frame.items), 2)

    def test_on_load_plan_preflight_failure_reports_and_keeps_plan(self):
        errors = []
        agent_plan_tab.messagebox.showerror = (
            lambda *args, **kwargs: errors.append(args)
        )

        def boom():
            raise RuntimeError("HBSys unreachable")

        self.frame.fees_check_fn = boom
        self.frame.on_load_plan()

        self.assertEqual(len(errors), 1)
        log_text = self.frame.log_text.get("1.0", "end")
        self.assertIn("HBSys unreachable", log_text)
        self.assertIn("FAILED", log_text)
        self.assertEqual(self.frame.items, [])  # plan never loaded
        self.assertEqual(str(self.frame.load_btn.cget("state")), "normal")

    def test_run_finished_reload_skips_fees_check(self):
        # The after-run reload uses load_plan() directly — no second
        # Fees Check per run.
        calls = []
        self.frame.fees_check_fn = lambda: calls.append(1) or ([], self.csv_path, None)
        agent_plan_tab.messagebox.askyesno = lambda *args, **kwargs: True

        def fake_run(rows, *, log_fn=None, **kwargs):
            return orchestrator.RunReport(
                outcomes=[
                    orchestrator.RowOutcome(
                        rows[0].get("patient_folder", ""),
                        rows[0].get("action", ""),
                        status=orchestrator.OUTCOME_BLOCKED,
                        detail="queued only",
                    )
                ]
            )

        self.frame.run_plan_fn = fake_run
        self.frame.fees_csv_var.set(str(self.csv_path))
        self.frame.on_load_plan()  # preflight runs the fake check once
        self.assertEqual(len(calls), 1)

        children = self.frame.plan_tree.get_children()
        self.frame.plan_tree.selection_set(children[0])
        self.frame.approve_selected()  # sync run -> _run_finished reloads

        self.assertEqual(len(calls), 1)  # reload did NOT re-run the check
        self.assertEqual(len(self.frame.items), 2)  # plan re-rendered


    def test_load_plan_warns_about_an_unfinished_previous_run(self):
        # A heartbeat still marked RUNNING means the previous run's process
        # ended without finishing: the panel must name the row it stopped on.
        heartbeat = self.root / "agent_current_run.json"
        heartbeat.write_text(
            json.dumps(
                {
                    "state": "RUNNING",
                    "rows_done": 2,
                    "total_rows": 5,
                    "last_action": "final_bill",
                    "last_patient_folder": (
                        "GUMPAL, KING SPENCER LUYUN - 000000000021426 - "
                        "ADM20260907_DIS20260911"
                    ),
                    "updated_at": "2026-09-28T09:24:30",
                }
            ),
            encoding="utf-8",
        )
        self.frame.state_file = heartbeat
        self.frame.fees_csv_var.set(str(self.csv_path))
        self.frame.load_plan()

        log_text = self.frame.log_text.get("1.0", "end")
        self.assertIn("hindi natapos ang nakaraang run", log_text)
        self.assertIn("row 2/5", log_text)
        self.assertIn("final_bill", log_text)
        self.assertIn("gui_lifecycle.log", log_text)

    def test_load_plan_has_no_warning_after_a_finished_run(self):
        heartbeat = self.root / "agent_current_run.json"
        heartbeat.write_text(
            json.dumps({"state": "FINISHED", "rows_done": 5, "total_rows": 5}),
            encoding="utf-8",
        )
        self.frame.state_file = heartbeat
        self.frame.fees_csv_var.set(str(self.csv_path))
        self.frame.load_plan()

        self.assertNotIn(
            "hindi natapos", self.frame.log_text.get("1.0", "end")
        )

    def test_missing_heartbeat_is_not_a_warning(self):
        self.frame.state_file = self.root / "does_not_exist.json"
        self.frame.fees_csv_var.set(str(self.csv_path))
        self.frame.load_plan()

        self.assertNotIn(
            "hindi natapos", self.frame.log_text.get("1.0", "end")
        )

    def test_map_final_bill_clicks_opens_the_mapper_in_its_own_console(self):
        # The button must launch the click mapper for the WHOLE flow (unang OK,
        # File save OK, Call Administrator No, 'Final' checkbox) in a separate
        # console, so F8/ESC reach it while HBSys keeps the mouse.
        with mock.patch("subprocess.Popen") as popen:
            self.frame.map_final_bill_clicks()

        command = popen.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertIn("core.agent.final_bill_click_map", command)
        self.assertIn("--map", command)
        self.assertEqual(popen.call_args.kwargs["cwd"], str(PROJECT_ROOT))
        self.assertIn(
            "click mapper", self.frame.log_text.get("1.0", "end").lower()
        )

    def test_map_final_bill_clicks_reports_a_launch_failure(self):
        # A mapper that cannot start must be a log line + a dialog, never a
        # crashed panel (the claims GUI keeps running either way).
        with mock.patch(
            "subprocess.Popen", side_effect=OSError("boom")
        ), mock.patch.object(
            agent_plan_tab.messagebox, "showerror"
        ) as error:
            self.frame.map_final_bill_clicks()

        error.assert_called_once()
        self.assertIn("boom", self.frame.log_text.get("1.0", "end"))

    def test_edit_final_bill_coordinates_opens_the_editor(self):
        # The coordinate editor is the no-F8 alternative: type X,Y per button
        # in a GUI and save to the same click-map .json, so it must launch
        # the gui.final_bill_map_editor module from the project root.
        with mock.patch("subprocess.Popen") as popen:
            self.frame.edit_final_bill_coordinates()

        command = popen.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertIn("gui.final_bill_map_editor", command)
        self.assertEqual(popen.call_args.kwargs["cwd"], str(PROJECT_ROOT))
        self.assertIn(
            "coordinate editor", self.frame.log_text.get("1.0", "end").lower()
        )

    def test_edit_final_bill_coordinates_reports_a_launch_failure(self):
        # A GUI that cannot start must be a log line + a dialog, never a
        # crashed panel (same contract as the F8 mapper button).
        with mock.patch(
            "subprocess.Popen", side_effect=OSError("boom")
        ), mock.patch.object(
            agent_plan_tab.messagebox, "showerror"
        ) as error:
            self.frame.edit_final_bill_coordinates()

        error.assert_called_once()
        self.assertIn("boom", self.frame.log_text.get("1.0", "end"))


    def test_get_final_bill_coordinates_opens_the_getter(self):
        # The getter is the point-and-click alternative: live cursor X,Y,
        # F8/countdown capture, saved to the same click-map .json - so it
        # must launch the gui.final_bill_coordinate_getter module.
        with mock.patch("subprocess.Popen") as popen:
            self.frame.get_final_bill_coordinates()

        command = popen.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertIn("gui.final_bill_coordinate_getter", command)
        self.assertEqual(popen.call_args.kwargs["cwd"], str(PROJECT_ROOT))
        self.assertIn(
            "coordinate getter", self.frame.log_text.get("1.0", "end").lower()
        )

    def test_get_final_bill_coordinates_reports_a_launch_failure(self):
        # A GUI that cannot start must be a log line + a dialog, never a
        # crashed panel (same contract as the mapper/editor buttons).
        with mock.patch(
            "subprocess.Popen", side_effect=OSError("boom")
        ), mock.patch.object(
            agent_plan_tab.messagebox, "showerror"
        ) as error:
            self.frame.get_final_bill_coordinates()

        error.assert_called_once()
        self.assertIn("boom", self.frame.log_text.get("1.0", "end"))


if __name__ == "__main__":
    unittest.main()
