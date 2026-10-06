"""Agent Plan Panel GUI Tab (Slice C approval + Slice E execution).

Shows one row per Fees Check patient with the Slice B planned action, lets
the user select rows, and on "Approve & Run":

    1. marks the selection APPROVED (rest SKIPPED) and saves the approved
       rows as a JSON audit trail (core/agent/agent_plan_store.py),
    2. asks for an explicit confirmation — nothing runs without YES,
    3. executes the approved rows through core/agent/orchestrator.py
       (Date Fill + XML Clicker = the existing tools, Final Bill = the
       verified FinalBillRunner; manual-review rows are only QUEUED).

CONTRACT (Slice E):
    - The panel itself NEVER clicks HBSys and NEVER touches the HBSys
      database; all execution happens through the orchestrator's
      injectable executors on a background thread, so Tkinter's main
      thread never freezes (progress lines come back via after()).
    - Rows never execute outside the approved set, and every run writes
      a logs/agent_run_*.json audit trail.

Diagnostics (2026-09-28): loading a plan also reads the orchestrator heartbeat
(logs/agent_current_run.json). A previous run still marked RUNNING means the
program ended before that run finished — the panel then says which row it
stopped on, next to the crash/lifecycle trail in logs/.

Integrated into the main EDH Claims GUI as a notebook tab, following the
constructor pattern of the sibling tabs (settings_getter, log_callback).
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from core.agent import agent_plan_store as plan_store
from core.agent import fees_actions as actions
from core.agent import orchestrator
from gui import run_notifier

ACTION_LABELS = {
    actions.ACTION_DATE_FILL: "DATE FILL",
    actions.ACTION_FINAL_BILL: "FINAL BILL",
    actions.ACTION_XML_CLICKER: "XML CLICKER",
    actions.ACTION_MANUAL_REVIEW: "MANUAL REVIEW",
}

ACTION_ROW_COLORS = {
    actions.ACTION_DATE_FILL: "#1a73e8",     # blue
    actions.ACTION_FINAL_BILL: "#b06000",    # amber/brown
    actions.ACTION_XML_CLICKER: "#1e8e3e",   # green
    actions.ACTION_MANUAL_REVIEW: "#c5221f",  # red
}



class AgentPlanFrame(ttk.Frame):
    """GUI frame for the Agent Plan approval + execution panel."""

    def __init__(
        self,
        parent: ttk.Frame,
        settings_getter: Callable[[], dict],
        log_callback: Callable[[str], None] | None = None,
        *,
        run_plan_fn: Callable | None = None,
        fees_check_fn: Callable | None = None,
        background: bool = True,
        state_file=None,
    ):
        super().__init__(parent)
        self.settings_getter = settings_getter
        self.log_callback = log_callback or (lambda msg: None)
        # Slice E execution: run_plan_fn defaults to
        # orchestrator.run_approved_plan (injectable for headless tests);
        # background=False runs it synchronously instead of on a thread.
        self.run_plan_fn = run_plan_fn
        # Load-Plan preflight: fees_check_fn defaults to
        # fees_checker.run_check (lazy import); injectable for tests.
        self.fees_check_fn = fees_check_fn
        self.background = background
        # Unfinished-run warning source: the orchestrator heartbeat file
        # (None = logs/agent_current_run.json). Injectable for tests.
        self.state_file = state_file
        self._running = False
        self._run_thread = None
        self._loading = False
        self.items: list[dict] = []
        self.summary_var = tk.StringVar(value="No plan loaded yet.")
        self.note_var = tk.StringVar(value="")
        self.build_ui()

    # -- UI construction --------------------------------------------------

    def build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 10))

        ttk.Label(
            header,
            text="Agent Plan",
            font=("Segoe UI", 16, "bold"),
        ).pack(side="left")

        ttk.Label(
            header,
            text="Plan from Fees Check — approve rows, then run them on HBSys",
            style="PanelMuted.TLabel",
        ).pack(side="left", padx=16)

        # -- Top controls -------------------------------------------------
        controls = ttk.LabelFrame(self, text="Controls", padding=10)
        controls.pack(fill="x", pady=(0, 8))

        row1 = ttk.Frame(controls)
        row1.pack(fill="x", pady=(0, 6))

        ttk.Label(row1, text="Fees CSV:").pack(side="left")
        self.fees_csv_var = tk.StringVar(
            value=str(plan_store.DEFAULT_FEES_CSV)
        )
        ttk.Entry(row1, textvariable=self.fees_csv_var, width=60).pack(
            side="left", padx=(6, 8), fill="x", expand=True
        )
        ttk.Button(row1, text="Browse", command=self.browse_fees_csv).pack(
            side="left"
        )
        # Preflight: refresh the Fees Check report before every Load Plan.
        self.preflight_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            row1,
            text="Run Fees Check first",
            variable=self.preflight_var,
        ).pack(side="left", padx=(10, 0))
        self.load_btn = ttk.Button(
            row1, text="Load Plan", command=self.on_load_plan
        )
        self.load_btn.pack(side="left", padx=(8, 0))

        row2 = ttk.Frame(controls)
        row2.pack(fill="x", pady=(0, 6))

        self.summary_label = ttk.Label(
            row2, textvariable=self.summary_var,
            font=("Segoe UI", 10, "bold"),
        )
        self.summary_label.pack(side="left")

        row3 = ttk.Frame(controls)
        row3.pack(fill="x")

        self.approve_btn = ttk.Button(
            row3,
            text="Approve & Run",
            style="Primary.TButton",
            command=self.approve_selected,
            state="disabled",
        )
        self.approve_btn.pack(side="left")
        ttk.Button(
            row3, text="Select All", command=self.select_all
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            row3, text="Clear Selection", command=self.clear_selection
        ).pack(side="left", padx=(8, 0))
        # Final Bill click mapper: the operator records the real "unang OK",
        # "File save" OK and "Call Administrator" No once (F8), and the Final
        # Bill flow clicks those points instead of guessing at control ids.
        ttk.Button(
            row3,
            text="Map Final Bill Clicks (F8)",
            command=self.map_final_bill_clicks,
        ).pack(side="left", padx=(8, 0))
        # Manual alternative: coordinate editor GUI - the operator TYPES the
        # X,Y per button (with labels) and saves straight to the same
        # click-map .json, for when F8 mapping is not convenient.
        ttk.Button(
            row3,
            text="Edit Final Bill Coordinates",
            command=self.edit_final_bill_coordinates,
        ).pack(side="left", padx=(8, 0))
        # Coordinate getter: the operator hovers the live HBSys button and
        # grabs its X,Y (F8 armed or countdown) straight into the same
        # click-map .json - point-and-click, no typing.
        ttk.Button(
            row3,
            text="Get Final Bill Coordinates",
            command=self.get_final_bill_coordinates,
        ).pack(side="left", padx=(8, 0))

        # Slice H: one patient can have TWO rows (FINAL BILL, then DATE FILL).
        # Both switches default ON — the operator's rule is that the Hospital
        # No. is the reference, so it is typed again before every step.
        self.fan_out_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            row3,
            text="2 steps (Final Bill + Date Fill)",
            variable=self.fan_out_var,
        ).pack(side="left", padx=(8, 0))
        self.always_reload_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            row3,
            text="Always re-enter Hospital No.",
            variable=self.always_reload_var,
        ).pack(side="left", padx=(8, 0))

        row4 = ttk.Frame(controls)
        row4.pack(fill="x")

        self.note_label = ttk.Label(
            row4, textvariable=self.note_var, foreground="#b06000"
        )
        self.note_label.pack(side="left")


        # -- Plan table ---------------------------------------------------
        table_frame = ttk.LabelFrame(self, text="Planned Actions", padding=10)
        table_frame.pack(fill="both", expand=True, pady=(0, 8))

        columns = ("patient", "action", "reason", "status")
        self.plan_tree = ttk.Treeview(
            table_frame, columns=columns, show="headings",
            selectmode="extended", height=12,
        )
        self.plan_tree.heading("patient", text="Patient Folder")
        self.plan_tree.heading("action", text="Planned Action")
        self.plan_tree.heading("reason", text="Why")
        self.plan_tree.heading("status", text="Status")
        self.plan_tree.column("patient", width=280)
        self.plan_tree.column("action", width=130)
        self.plan_tree.column("reason", width=420)
        self.plan_tree.column("status", width=100)
        self.plan_tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.plan_tree.yview
        )
        scrollbar.pack(side="right", fill="y")
        self.plan_tree.configure(yscrollcommand=scrollbar.set)

        for action, color in ACTION_ROW_COLORS.items():
            self.plan_tree.tag_configure(action, foreground=color)

        # -- Log ----------------------------------------------------------
        log_frame = ttk.LabelFrame(self, text="Plan Log", padding=4)
        log_frame.pack(fill="x")
        self.log_text = tk.Text(log_frame, height=6, state="disabled")
        self.log_text.pack(fill="x")


    # -- Actions ----------------------------------------------------------

    def browse_fees_csv(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askopenfilename(
            title="Select Fees Check CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if chosen:
            self.fees_csv_var.set(chosen)

    def on_load_plan(self) -> None:
        """Button entry: optionally run a fresh Fees Check, then load.

        With the "Run Fees Check first" checkbox ON (default) the plan
        base is always the report THIS click just wrote — fees check is
        read-only against HBSys and runs on a background thread so the
        UI never freezes. Unchecked (or driven programmatically through
        load_plan(), e.g. the after-run reload) loads the existing CSV
        without touching HBSys.
        """
        if self._loading:
            self.log("Fees Check paperatakbo pa — hintayin ang tapos.")
            return
        if not self.preflight_var.get():
            self.load_plan()
            return

        self._loading = True
        self.load_btn.configure(state="disabled")
        self.log("Running Fees Check first (read-only sa HBSys)...")

        def worker():
            try:
                check_fn = self.fees_check_fn or self._default_fees_check
                _rows, csv_path, _xlsx = check_fn()
            except Exception as exc:  # noqa: BLE001 - surface, never crash Tk
                self._ui(lambda: self._preflight_failed(exc))
                return
            self._ui(lambda: self._preflight_finished(str(csv_path)))

        if self.background:
            threading.Thread(target=worker, daemon=True).start()
        else:
            worker()

    @staticmethod
    def _default_fees_check():
        """Run the full Fees Check (import lazy: keeps GUI start light)."""
        import fees_checker

        return fees_checker.run_check()

    def _preflight_finished(self, csv_path: str) -> None:
        self._loading = False
        self.load_btn.configure(state="normal")
        self.log(f"Fees Check tapos — bagong report: {csv_path}")
        self.fees_csv_var.set(csv_path)
        self.load_plan()

    def _preflight_failed(self, exc: Exception) -> None:
        self._loading = False
        self.load_btn.configure(state="normal")
        self.log(f"Fees Check FAILED: {exc}")
        messagebox.showerror(
            "Fees Check",
            f"Hindi tumakbo ang Fees Check:\n{exc}\n\n"
            "Pwede i-uncheck ang 'Run Fees Check first' para i-load lang "
            "ang existing report.",
            parent=self,
        )

    def load_plan(self) -> None:
        """Read the LATEST Fees Check CSV and render one row per patient.

        Pure load (no Fees Check run) — used directly by the after-run
        auto-reload; the Load Plan button goes through on_load_plan().
        Read-only: the base is always the newest fees_checker_report*.csv
        (blank/canonical field), while an explicit Browse/typed path is
        respected as-is; rows recorded completed by a previous run drop
        out so finished work is never repeated.
        """
        raw_path = self.fees_csv_var.get().strip()
        csv_path = str(plan_store.resolve_fees_csv(raw_path))
        if csv_path != raw_path:
            self.fees_csv_var.set(csv_path)
        # Slice H: with the switch on, one patient can contribute TWO rows
        # (FINAL BILL, then DATE FILL), so the counts below are STEPS.
        fan_out = bool(self.fan_out_var.get())
        items, summary, note = plan_store.build_plan_from_csv(
            csv_path, fan_out=fan_out
        )
        self.items = items
        self._render_items()

        parts = [
            f"Date Fill: {summary.get(actions.ACTION_DATE_FILL, 0)}",
            f"Final Bill: {summary.get(actions.ACTION_FINAL_BILL, 0)}",
            f"XML Clicker: {summary.get(actions.ACTION_XML_CLICKER, 0)}",
            f"Manual Review: {summary.get(actions.ACTION_MANUAL_REVIEW, 0)}",
        ]
        total = sum(summary.values())
        patients = len({
            str(item.get("patient_folder") or "").strip()
            for item in items
            if str(item.get("patient_folder") or "").strip()
        })
        noun = "steps" if (fan_out and total > patients) else "patients"
        self.summary_var.set(
            f"Plan: {total} {noun} ({patients} patient(s)) — "
            + " | ".join(parts)
        )
        self.note_var.set(note)
        self.approve_btn.configure(state="normal" if items else "disabled")
        self.log(
            f"Loaded plan from {csv_path}: {total} {noun} "
            f"({patients} patient(s)). "
            + ("| ".join(parts) if items else note)
        )
        warning = self._previous_run_warning()
        if warning:
            self.log(warning)

    def _previous_run_warning(self) -> str:
        """Warn when the heartbeat says the last run never finished.

        The orchestrator rewrites logs/agent_current_run.json after every row.
        A state of RUNNING at load time means the previous run's process ended
        without finishing (the window was closed, or the process was killed) —
        the payload names the row it stopped on, and the crash/lifecycle trail
        lives in logs/gui_lifecycle.log and logs/gui_crash.log.
        """
        try:
            state = orchestrator.last_run_state(self.state_file)
        except Exception:  # noqa: BLE001 - a warning must never fail a load
            return ""
        if not state or state.get("state") != "RUNNING":
            return ""
        where = (
            f"{state.get('last_action') or 'unknown'}"
            f" {state.get('last_patient_folder') or ''}".strip()
        )
        return (
            "BABALA: hindi natapos ang nakaraang run "
            f"(row {state.get('rows_done', '?')}/{state.get('total_rows', '?')}"
            f" — {where} — {state.get('updated_at') or ''}). "
            "Kung kusang nagsara ang program, tingnan ang logs\\gui_lifecycle.log "
            "at logs\\gui_crash.log para sa dahilan."
        )

    def select_all(self) -> None:
        children = self.plan_tree.get_children()
        if children:
            self.plan_tree.selection_set(children)

    def clear_selection(self) -> None:
        self.plan_tree.selection_clear()

    def approve_selected(self) -> None:
        """Approve the selection, then execute it after an explicit YES."""
        if not self.items:
            return
        children = list(self.plan_tree.get_children())
        selected = {
            children.index(item_id)
            for item_id in self.plan_tree.selection()
            if item_id in children
        }
        if not selected:
            messagebox.showinfo(
                "Agent Plan", "Select at least one row to approve."
            )
            return
        items, approved_count, skipped_count = plan_store.apply_approval(
            self.items, selected
        )
        self.items = items
        self._render_items()
        approved_rows = plan_store.approved_items(items)
        saved_path = plan_store.save_approved_plan(approved_rows)
        self.log(
            f"Approved {approved_count} row(s), skipped {skipped_count}. "
            f"Saved approved plan: {saved_path}"
        )
        if self._running:
            messagebox.showinfo(
                "Agent Plan",
                "Plan saved. A run is still in progress — wait for it to "
                "finish before starting another.",
            )
            return
        counts = {action: 0 for action in actions.ALL_ACTIONS}
        for row in approved_rows:
            if row.get("action") in counts:
                counts[row.get("action")] += 1
        # Slice H: rows are now STEPS, so count the patients too — otherwise
        # "2 DATE FILL + 1 FINAL BILL" would read like three patients.
        patients = {
            str(row.get("patient_folder") or "").strip()
            for row in approved_rows
            if str(row.get("patient_folder") or "").strip()
        }
        confirmed = messagebox.askyesno(
            "Approve & Run — execute on HBSys?",
            f"Approved {approved_count} step(s) sa {len(patients)} "
            f"patient(s), skipped {skipped_count}.\n\n"
            f"Saved: {saved_path}\n\n"
            f"DATE FILL: {counts[actions.ACTION_DATE_FILL]} step(s)\n"
            f"FINAL BILL: {counts[actions.ACTION_FINAL_BILL]} step(s)\n"
            f"XML CLICKER: {counts[actions.ACTION_XML_CLICKER]} step(s)\n"
            f"MANUAL REVIEW: {counts[actions.ACTION_MANUAL_REVIEW]} "
            "(queued — hindi tatakbo)\n\n"
            "May pasyenteng dalawang hakbang (FINAL BILL, DATE FILL) — "
            "FINAL BILL ang muna.\n\n"
            "Tatakbo ang approved rows ngayon sa HBSys (background thread). "
            "Buksan muna ang HBSys bago mag-YES.\n\n"
            "Ipatakbo na?",
            parent=self,
        )
        if not confirmed:
            self.log("Execution declined — plan saved only, walang tinakbo.")
            messagebox.showinfo(
                "Agent Plan", "Plan saved. Walang tinakbo sa HBSys."
            )
            return
        self._start_execution(approved_rows)

    # -- Slice E execution ------------------------------------------------

    def _start_execution(self, approved_rows: list) -> None:
        """Run approved rows (thread; synchronous when background=False)."""
        self._running = True
        self.approve_btn.configure(state="disabled")

        def worker():
            try:
                try:
                    import pythoncom  # type: ignore

                    try:
                        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
                    except Exception:
                        pass
                except Exception:
                    pass
                run_fn = self.run_plan_fn or orchestrator.run_approved_plan
                # Slice H toggles. Read on the Tk thread BEFORE the worker
                # starts, so the worker never touches a StringVar off-thread.
                kwargs = {}
                if self.fan_out_var.get():
                    kwargs["order_steps"] = True
                else:
                    kwargs["order_steps"] = False
                kwargs["always_reload"] = bool(self.always_reload_var.get())
                report = run_fn(
                    approved_rows,
                    log_fn=lambda message: self._ui(lambda: self.log(message)),
                    **kwargs,
                )
            except Exception as exc:  # noqa: BLE001 - surface, never crash Tk
                self._ui(lambda: self._run_failed(exc))
                return
            self._ui(lambda: self._run_finished(report))

        if self.background:
            self._run_thread = threading.Thread(target=worker, daemon=True)
            self._run_thread.start()
        else:
            worker()

    def _run_finished(self, report) -> None:
        self._running = False
        self.approve_btn.configure(
            state="normal" if self.items else "disabled"
        )
        self.log(f"Run finished: {report.summary_line}")
        if report.saved_path:
            self.log(f"Run report: {report.saved_path}")
        # Slice H: after Final Bills finish, the next plan load is what shows
        # the Date Fill steps that were left. Say it once, no auto-run.
        finished_final_bills = sum(
            1
            for outcome in (getattr(report, "outcomes", None) or [])
            if outcome.action == actions.ACTION_FINAL_BILL
            and outcome.status == "OK"
        )
        if finished_final_bills:
            self.log(
                f"  {finished_final_bills} FINAL BILL step(s) ang natapos — "
                "buksan ang Plan Panel para makita ang na-update na DATE FILL."
            )
        # Problem rows (BLOCKED / FAILED) are named here, not just counted: the
        # operator's rule is "keep going, list them at the end" so the skipped
        # patients are visible without opening the JSON.
        problems = list(getattr(report, "problem_rows", []) or [])
        for outcome in problems:
            self.log(
                f"  NOT FINALIZED {outcome.hospital_no or outcome.patient_folder} "
                f"[{outcome.patient_folder}] — {outcome.detail}"
            )
        # Taskbar flash + toast: the operator may be away from the screen while
        # the run drives HBSys, so the end of the run is announced OUTSIDE the
        # HBSys window. The notifier never takes the foreground (see
        # gui/run_notifier.py), so HBSys stays the click/keyboard target.
        if problems:
            message = (
                f"Tapos na ang run, pero {len(problems)} patient ang hindi "
                f"na-finalize.\n{str(report.summary_line)}"
            )
            title = "Agent Plan - may hindi na-finalize"
        else:
            message = "Tapos na ang run.\n" + str(report.summary_line)
            title = "Agent Plan - run finished"
        used = run_notifier.notify_run_finished(self, message=message, title=title)
        self.log(f"Run-finished notification: {used}")
        # Reload so rows the orchestrator recorded as completed drop out
        # of the plan (finished work is never repeated).
        if getattr(report, "outcomes", None):
            self.load_plan()
        if problems:
            listed = "\n".join(
                f"  {index}. {outcome.hospital_no or outcome.patient_folder} "
                f"[{outcome.patient_folder}]\n     {outcome.detail}"
                for index, outcome in enumerate(problems, start=1)
            )
            messagebox.showwarning(
                "Agent Plan — may hindi na-finalize",
                f"{report.summary_line}\n\n"
                f"{len(problems)} patient(s) ang na-skip at hindi na-finalize:\n"
                f"{listed}\n\n"
                f"Ayusin ng kamay, tapos piliin ang mga ito at i-run ulit.\n"
                f"Report: {report.saved_path or '(not saved)'}",
                parent=self,
            )
            return
        messagebox.showinfo(
            "Agent Plan — run finished",
            f"{report.summary_line}\n\n"
            f"Lahat ng napiling patient ay na-finalize.\n"
            f"Report: {report.saved_path or '(not saved)'}",
            parent=self,
        )

    def _run_failed(self, exc: Exception) -> None:
        self._running = False
        self.approve_btn.configure(
            state="normal" if self.items else "disabled"
        )
        detail = f"{type(exc).__name__}: {exc}"
        self.log(f"Run failed: {detail}")
        try:
            run_notifier.notify_run_failed(
                self, message=f"May error sa run.\n{detail[:200]}"
            )
        except Exception:  # noqa: BLE001 - a notifier must never mask the error
            pass
        messagebox.showerror(
            "Agent Plan — run failed",
            detail,
            parent=self,
        )

    def _ui(self, update) -> None:
        """Marshal a UI update onto Tk's main thread."""
        if self.background:
            self.after(0, update)
        else:
            update()

    # -- Internals --------------------------------------------------------

    def _render_items(self) -> None:
        # Slice H: mark the rows that are a FOLLOW-UP step of the same patient.
        # The plan already lists them adjacently; the marker only says why.
        final_bill_folders = {
            item.get("patient_folder", "")
            for item in self.items
            if item.get("action") == actions.ACTION_FINAL_BILL
        }
        for item_id in self.plan_tree.get_children():
            self.plan_tree.delete(item_id)
        for item in self.items:
            action = item.get("action", "")
            label = ACTION_LABELS.get(action, action)
            if action == actions.ACTION_DATE_FILL and (
                item.get("patient_folder", "") in final_bill_folders
            ):
                label = "2nd step - " + label
            if not item.get("tool_available", True):
                label += " [TOOL MISSING]"
            self.plan_tree.insert(
                "",
                "end",
                values=(
                    item.get("patient_folder", ""),
                    label,
                    item.get("reason", ""),
                    item.get("status", ""),
                ),
                tags=(action,),
            )

    def map_final_bill_clicks(self) -> None:
        """Open the Final Bill click mapper in its own console (F8 = record).

        The mapper (core/agent/final_bill_click_map.py) walks the operator
        through the Final Bill flow in its LIVE order - the 'Final' checkbox,
        the UNANG OK of "Print Options", at alin lang sa "File save" OK o "Call
        Administrator" No ang lumabas - and stores them in
        logs/final_bill_click_map.json. It runs in a separate console window so
        F8/ESC reach the tool while HBSys keeps the mouse, and so a mapper
        crash can never take the claims GUI down with it.
        """
        import os
        import subprocess
        import sys

        project_root = Path(__file__).resolve().parents[1]
        self.log(
            "Opening Final Bill click mapper (F8 = i-record ang mouse, "
            "ESC = laktawan)..."
        )
        try:
            env = os.environ.copy()
            env["CLAIMS_GUI_MODE"] = "1"
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "core.agent.final_bill_click_map",
                    "--map",
                ],
                cwd=str(project_root),
                env=env,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            self.log(
                "Click mapper opened. I-hover ang button sa HBSys at pindutin "
                "ang F8; tapos i-click ito para lumipat sa susunod na prompt."
            )
        except Exception as exc:  # noqa: BLE001 - report, never crash the GUI
            self.log(f"Click mapper failed to open: {exc}")
            messagebox.showerror(
                "Map Final Bill Clicks",
                f"Could not open the click mapper:\n{exc}",
            )

    def edit_final_bill_coordinates(self) -> None:
        """Open the Final Bill coordinate editor GUI (manual X,Y entry).

        gui/final_bill_map_editor.py lets the operator TYPE the click points
        per button (final_checkbox, print_options_ok, save_ok, admin_no) with
        labels and save them to the same logs/final_bill_click_map.json the
        Final Bill flow reads - for when F8 mapping is not convenient. It is
        a Tk window launched without its own console, with output appended
        to logs/final_bill_map_editor.log, so a failed launch or a Tk crash
        can never take the claims GUI down.
        """
        import os
        import subprocess
        import sys

        project_root = Path(__file__).resolve().parents[1]
        self.log(
            "Opening Final Bill coordinate editor (i-type ang X, Y bawat "
            "button, tapos I-SAVE)..."
        )
        try:
            log_path = project_root / "logs" / "final_bill_map_editor.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["CLAIMS_GUI_MODE"] = "1"
            with open(log_path, "a", encoding="utf-8", errors="replace") as sink:
                subprocess.Popen(
                    [sys.executable, "-m", "gui.final_bill_map_editor"],
                    cwd=str(project_root),
                    env=env,
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            self.log(
                "Coordinate editor opened. I-type ang bawat field at "
                "pindutin ang I-SAVE para ma-save sa .json."
            )
        except Exception as exc:  # noqa: BLE001 - report, never crash the GUI
            self.log(f"Coordinate editor failed to open: {exc}")
            messagebox.showerror(
                "Edit Final Bill Coordinates",
                f"Could not open the coordinate editor:\n{exc}",
            )

    def get_final_bill_coordinates(self) -> None:
        """Open the Final Bill coordinate getter GUI (live X,Y capture).

        gui/final_bill_coordinate_getter.py shows the cursor's live X,Y while
        the operator hovers the real HBSys button, captures it with F8 (while
        armed) or a countdown, and saves the chosen target to the same
        logs/final_bill_click_map.json the Final Bill flow reads - the
        point-and-click complement to typing in the coordinate editor. It runs
        without its own console, output appended to
        logs/final_bill_coordinate_getter.log, so a failed launch or a Tk
        crash can never take the claims GUI down.
        """
        import os
        import subprocess
        import sys

        project_root = Path(__file__).resolve().parents[1]
        self.log(
            "Opening Final Bill coordinate getter (i-hover ang button, tapos "
            "F8 o countdown para makuha ang X,Y)..."
        )
        try:
            log_path = (
                project_root / "logs" / "final_bill_coordinate_getter.log"
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["CLAIMS_GUI_MODE"] = "1"
            with open(log_path, "a", encoding="utf-8", errors="replace") as sink:
                subprocess.Popen(
                    [sys.executable, "-m", "gui.final_bill_coordinate_getter"],
                    cwd=str(project_root),
                    env=env,
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            self.log(
                "Coordinate getter opened. Live ang X,Y; F8 kapag naka-arm, "
                "tapos I-SAVE sa .json."
            )
        except Exception as exc:  # noqa: BLE001 - report, never crash the GUI
            self.log(f"Coordinate getter failed to open: {exc}")
            messagebox.showerror(
                "Get Final Bill Coordinates",
                f"Could not open the coordinate getter:\n{exc}",
            )

    def log(self, message: str) -> None:
        """Append a message to the panel log."""
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.log_callback(message)


# -- standalone test ------------------------------------------------------

if __name__ == "__main__":
    root = tk.Tk()
    root.title("Agent Plan - Test")
    root.geometry("1100x700")

    frame = AgentPlanFrame(
        root,
        settings_getter=lambda: {},
        log_callback=print,
    )
    frame.pack(fill="both", expand=True, padx=10, pady=10)

    root.mainloop()
