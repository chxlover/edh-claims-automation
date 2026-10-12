"""Claims Agent GUI Tab — full pipeline runner.

Runs the deterministic Claims Agent (`core/agent/claims_agent.py`) on a
background thread so Tkinter's main thread never freezes; progress comes
back via `after()`. Follows the constructor pattern of the sibling tabs
(settings_getter, log_callback) and the AgentPlanFrame threading contract.

Safety:
    * "Live HBSys" is OFF by default — with it off the agent SKIPS every
      HBSys-touching stage and holds claims (nothing clicks, nothing types).
    * The panel never clicks HBSys itself; all work goes through the agent.
    * Starting a batch asks for explicit confirmation (like Agent Plan).

Integrated into the main EDH Claims GUI as a notebook tab.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable


class ClaimsAgentFrame(ttk.Frame):
    """GUI frame for the Claims Agent batch runner."""

    def __init__(
        self,
        parent: ttk.Frame,
        settings_getter: Callable[[], dict],
        log_callback: Callable[[str], None] | None = None,
        *,
        agent_factory: Callable | None = None,
        background: bool = True,
    ) -> None:
        super().__init__(parent)
        self.settings_getter = settings_getter
        self.log_callback = log_callback or (lambda msg: None)
        # agent_factory(config) -> ClaimsAgent; injectable for headless tests.
        self.agent_factory = agent_factory
        self.background = background
        self._running = False
        self._run_thread = None
        self.batch_var = tk.StringVar(value="")
        self.live_var = tk.BooleanVar(value=False)
        self.retries_var = tk.StringVar(value="2")
        self.summary_var = tk.StringVar(value="No batch run yet.")
        self.report_var = tk.StringVar(value="")
        # Thread-safe bridge (2026-10-09 fix): Tkinter is NOT thread-safe,
        # so the worker thread must NEVER call after()/widget methods
        # directly (that silently dropped every log + the finish signal,
        # leaving the UI stuck on "running"). The worker only puts
        # ("log"/"done") messages here; the main thread drains them.
        self._ui_queue: queue.Queue = queue.Queue()
        self._poll_job = None
        self.build_ui()

    @property
    def is_running(self) -> bool:
        return self._running

    # -- UI construction --------------------------------------------------

    def build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="Claims Agent",
                  font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Label(header, text="Full pipeline: scan → fees → date fill → final bill → "
                  "XML → check → attachments → report",
                  style="PanelMuted.TLabel").pack(side="left", padx=16)

        controls = ttk.LabelFrame(self, text="Controls", padding=10)
        controls.pack(fill="x", pady=(0, 8))

        row1 = ttk.Frame(controls)
        row1.pack(fill="x", pady=(0, 6))
        ttk.Label(row1, text="Batch ID (empty = new/resume):").pack(side="left")
        ttk.Entry(row1, textvariable=self.batch_var, width=24).pack(
            side="left", padx=(6, 16))
        ttk.Label(row1, text="Fee retries:").pack(side="left")
        ttk.Spinbox(row1, from_=0, to=10, width=4,
                    textvariable=self.retries_var).pack(side="left", padx=(6, 16))
        ttk.Checkbutton(row1, text="Live HBSys (OFF = safe dry run)",
                        variable=self.live_var).pack(side="left")

        row2 = ttk.Frame(controls)
        row2.pack(fill="x")
        self.start_button = ttk.Button(row2, text="Start / Resume Batch",
                                       command=self.on_start)
        self.start_button.pack(side="left", padx=(0, 8))
        ttk.Button(row2, text="Refresh Status",
                   command=self.refresh_table).pack(side="left", padx=(0, 8))
        ttk.Button(row2, text="Open Reports Folder",
                   command=self.on_open_reports).pack(side="left")

        ttk.Label(self, textvariable=self.summary_var,
                  font=("Segoe UI", 10, "bold")).pack(fill="x", pady=(4, 0))
        ttk.Label(self, textvariable=self.report_var,
                  style="PanelMuted.TLabel").pack(fill="x")

        table_frame = ttk.LabelFrame(self, text="Claims", padding=6)
        table_frame.pack(fill="both", expand=True, pady=(6, 0))
        columns = ("claim", "stage", "outcome", "reason")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings",
                                 height=12)
        for col, title, width in (("claim", "Claim", 320),
                                  ("stage", "Last stage", 180),
                                  ("outcome", "Outcome", 140),
                                  ("reason", "Reason / manual action", 420)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, stretch=(col in ("claim", "reason")))
        scroll = ttk.Scrollbar(table_frame, orient="vertical",
                               command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # -- Live agent log (2026-10-09): the batch's own lines, visible
        # right inside the tab (read-only). Same lines also go to the
        # main GUI log via log_callback.
        log_frame = ttk.LabelFrame(self, text="Agent Log", padding=6)
        log_frame.pack(fill="x", pady=(6, 0))
        self.log_text = tk.Text(log_frame, height=7, wrap="word",
                                state="disabled",
                                font=("Consolas", 9))
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical",
                                   command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="x", expand=True)
        log_scroll.pack(side="right", fill="y")

    # -- actions ----------------------------------------------------------

    def _build_config(self):
        from core.agent.pipeline_config import load_config

        settings = self.settings_getter() or {}
        try:
            retries = max(0, int(self.retries_var.get()))
        except ValueError:
            retries = 2
        return load_config({
            "scan_folder": settings.get("scan_folder", ""),
            "output_folder": settings.get("output_folder", ""),
            "max_fee_cycle_retries": retries,
            "live_hbsys": bool(self.live_var.get()),
            "batch_id": self.batch_var.get().strip(),
        })

    def on_start(self) -> None:
        if self._running:
            messagebox.showinfo("Claims Agent", "A batch is already running.")
            return
        live = bool(self.live_var.get())
        question = ("Run the full claims pipeline?\n\n"
                    f"Live HBSys: {'ON — the agent WILL click/type in HBSys' if live else 'OFF — safe mode, HBSys stages are skipped'}")
        if not messagebox.askyesno("Claims Agent", question):
            return
        self._running = True
        self.start_button.configure(state="disabled")
        self.summary_var.set("Batch running… (live log below)")
        self._append_log("[claims-agent] batch starting…")
        # Capture Tk state HERE (main thread): the worker must never call
        # widget/StringVar methods (same thread-violation class as after()).
        batch_id = self.batch_var.get().strip()
        try:
            config = self._build_config()
        except Exception as exc:  # noqa: BLE001
            self._running = False
            self.start_button.configure(state="normal")
            self.summary_var.set(f"Batch error: {exc}")
            return
        if self.background:
            self._run_thread = threading.Thread(
                target=self._run_batch, args=(config, batch_id), daemon=True)
            self._run_thread.start()
            self._poll_queue()
        else:
            self._run_batch(config, batch_id)
            self._drain_queue()

    def _thread_log(self, message: str) -> None:
        """Worker-thread side: enqueue only, never touch Tk."""
        try:
            self._ui_queue.put_nowait(("log", message))
        except Exception:
            pass

    def _poll_queue(self) -> None:
        """Main-thread side: drain queued messages, then reschedule."""
        try:
            self._drain_queue()
        except Exception:  # noqa: BLE001 - polling must never kill the tab
            pass
        if self._running:
            try:
                self._poll_job = self.after(250, self._poll_queue)
            except Exception:  # noqa: BLE001 - widget destroyed
                self._poll_job = None

    def _drain_queue(self) -> None:
        while True:
            try:
                kind, payload = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            if kind == "log":
                self._append_log(payload)
            elif kind == "done":
                summary, error = payload
                self._finish(summary, error)

    def _append_log(self, message: str) -> None:
        """Main thread only: show in the tab log box + main GUI log."""
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            # Keep the box bounded (last ~2000 lines: the engine prints
            # thousands of OCR lines per patient).
            lines = int(self.log_text.index("end-1c").split(".")[0])
            if lines > 2000:
                self.log_text.delete("1.0", f"{lines - 2000}.0")
            self.log_text.configure(state="disabled")
        except Exception:
            pass
        try:
            self.log_callback(message)
        except Exception:
            pass

    def _make_agent(self, config):
        if self.agent_factory is not None:
            return self.agent_factory(config)
        from core.agent.claims_agent import ClaimsAgent

        return ClaimsAgent(config=config, log_fn=self._thread_log)

    def _run_batch(self, config, batch_id: str) -> None:
        """Worker thread: config/batch_id are plain data (no Tk calls)."""
        try:
            problems = config.problems()
            if problems:
                self._ui_queue.put_nowait(
                    ("done", (None, f"Config invalid: {'; '.join(problems)}")))
                return
            agent = self._make_agent(config)
            summary = agent.run_batch(batch_id)
            self._ui_queue.put_nowait(("done", (summary, "")))
        except Exception as exc:  # noqa: BLE001 - must always unlock the UI
            try:
                self._ui_queue.put_nowait(("done", (None, f"Batch error: {exc}")))
            except Exception:
                pass

    def _finish(self, summary, error: str) -> None:
        self._running = False
        self.start_button.configure(state="normal")
        if error:
            self.summary_var.set(error)
            self.log_callback(f"[claims-agent] {error}")
            return
        counts = summary.as_dict()
        self.summary_var.set(
            f"Batch {summary.batch_id}: total={counts['total_claims']} "
            f"completed={counts['completed']} "
            f"incomplete={counts['incomplete']} "
            f"review={counts['held_for_review']} "
            f"failed={counts['failed']}")
        self.log_callback(
            f"[claims-agent] batch {summary.batch_id} finished: {counts}")
        self.refresh_table()

    def refresh_table(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        try:
            config = self._build_config()
            from core.agent.pipeline_state import PipelineStore

            store = PipelineStore(config.state_db)
            try:
                batch_id = self.batch_var.get().strip() or store.latest_open_batch()
                records = store.list_claims(batch_id) if batch_id else []
            finally:
                store.close()
        except Exception as exc:  # noqa: BLE001 - refresh must not crash the tab
            self.summary_var.set(f"Refresh failed: {exc}")
            return
        for record in records:
            reason = record.hold_reason or record.last_error or ""
            if record.manual_action:
                reason = f"{reason} | Action: {record.manual_action}"
            self.tree.insert("", "end", values=(
                record.claim_key, record.last_completed_stage or record.stage,
                record.outcome, reason[:160]))
        if records and not self._running:
            self.summary_var.set(
                f"Batch {batch_id}: {len(records)} claim(s) — "
                f"{sum(1 for r in records if r.outcome == 'COMPLETED')} completed, "
                f"{sum(1 for r in records if r.outcome == 'FAILED')} failed, "
                f"{sum(1 for r in records if r.outcome == 'INCOMPLETE')} incomplete, "
                f"{sum(1 for r in records if r.outcome == 'HELD_FOR_REVIEW')} for review.")

    def on_open_reports(self) -> None:
        try:
            folder = Path(self._build_config().report_dir)
            folder.mkdir(parents=True, exist_ok=True)
            import os

            os.startfile(folder)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - convenience only
            messagebox.showinfo("Claims Agent", f"Cannot open folder: {exc}")
