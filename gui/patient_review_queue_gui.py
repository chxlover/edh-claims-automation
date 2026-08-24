"""Tkinter Patient Review Queue interface.

The GUI calls the queue service only. It contains no SQL or OCR logic.
"""

from __future__ import annotations

import os
import subprocess
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Callable

from core.activity_logger import logger
from core.admission_lookup import MySQLAdmissionLookup
from core.hbsys_connection import create_hbsys_connection
from core.hospital_patient_lookup import MySQLPatientLookup
from core.patient_correction_service import CorrectionCandidate, PatientCorrectionService
from core.patient_review_queue import (
    PatientReviewQueue,
    ReviewItem,
    ReviewStatus,
    review_queue,
)
from core.resume_manager import ResumeManager


STATUS_OPTIONS = ("ACTIVE", "ALL", *(status.value for status in ReviewStatus))
AUTO_REVIEW_USER = "EDH_REVIEW"


class PatientReviewQueueWindow:
    """Review queue presentation; persistence stays in the service layer."""

    def __init__(
        self,
        root: tk.Tk,
        queue_service: PatientReviewQueue = review_queue,
    ) -> None:
        self.root = root
        self.queue = queue_service
        self.corrections = PatientCorrectionService(
            queue_service,
            MySQLPatientLookup(create_hbsys_connection),
            MySQLAdmissionLookup(create_hbsys_connection),
        )
        self.resume_manager = ResumeManager(queue_service)
        self.items_by_id: dict[int, ReviewItem] = {}
        self.document_paths: list[Path] = []
        self.status_var = tk.StringVar(value="ACTIVE")
        self.summary_var = tk.StringVar(value="0 review items")
        self.last_refresh_var = tk.StringVar(value="Not refreshed yet")
        self.auto_refresh_var = tk.BooleanVar(value=True)
        self.file_summary_var = tk.StringVar(value="0 files under review")
        self.issue_scope_var = tk.StringVar(value="")
        self.resume_queue_var = tk.StringVar(value="Auto Resume: idle")
        self._auto_refresh_after_id: str | None = None
        self._resume_monitor_after_id: str | None = None
        self._resume_waiting_ids: list[int] = []
        self._resume_active_id: int | None = None
        self._resume_active_process: subprocess.Popen[str] | None = None
        self.detail_vars = {
            name: tk.StringVar(value="")
            for name in (
                "id", "status", "reason", "hospital_no", "patient_name",
                "encounter_no", "admission", "discharge", "confidence",
                "batch_id", "created_at", "resolved_by", "resolved_at",
            )
        }
        self._configure_window()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        self._schedule_auto_refresh()

    def _configure_window(self) -> None:
        self.root.title("EDH Claims — Patient Review Queue")
        self.root.geometry("1180x720")
        self.root.minsize(980, 620)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(
            header,
            text="PATIENT REVIEW QUEUE",
            font=("Segoe UI", 16, "bold"),
        ).pack(side="left")
        ttk.Label(header, textvariable=self.summary_var).pack(side="left", padx=18)
        ttk.Label(header, textvariable=self.last_refresh_var).pack(side="left")
        ttk.Button(header, text="Refresh", command=self.refresh).pack(side="right")
        ttk.Checkbutton(
            header,
            text="Auto-refresh",
            variable=self.auto_refresh_var,
            command=self._schedule_auto_refresh,
        ).pack(side="right", padx=(0, 8))
        ttk.Combobox(
            header,
            textvariable=self.status_var,
            values=STATUS_OPTIONS,
            state="readonly",
            width=14,
        ).pack(side="right", padx=8)
        ttk.Label(header, text="Status:").pack(side="right")
        self.status_var.trace_add("write", lambda *_: self.refresh())

        paned = ttk.Panedwindow(outer, orient="horizontal")
        paned.pack(fill="both", expand=True)
        list_frame = ttk.Frame(paned)
        detail_frame = ttk.Frame(paned, padding=(12, 0, 0, 0))
        paned.add(list_frame, weight=3)
        paned.add(detail_frame, weight=2)

        self._build_table(list_frame)
        self._build_details(detail_frame)
        self._build_actions(outer)

    def _build_table(self, parent: ttk.Frame) -> None:
        columns = (
            "id", "status", "reason", "hospital_no", "patient_name",
            "files", "admission", "created_at",
        )
        self.table = ttk.Treeview(parent, columns=columns, show="headings")
        headings = {
            "id": "ID", "status": "Status", "reason": "Reason",
            "hospital_no": "Hospital No.", "patient_name": "Patient",
            "files": "Files", "admission": "Admission", "created_at": "Created",
        }
        widths = {
            "id": 55, "status": 95, "reason": 65, "hospital_no": 135,
            "patient_name": 230, "admission": 100, "created_at": 145,
            "files": 55,
        }
        for column in columns:
            self.table.heading(column, text=headings[column])
            self.table.column(column, width=widths[column], anchor="w")
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.table.bind("<<TreeviewSelect>>", self._on_select)
        self.table.bind("<Double-1>", self._on_double_click)

    def _build_details(self, parent: ttk.Frame) -> None:
        info = ttk.LabelFrame(parent, text="Patient and Review Details", padding=10)
        info.pack(fill="x")
        labels = (
            ("id", "Queue ID"), ("status", "Status"), ("reason", "Reason"),
            ("hospital_no", "Hospital No."), ("patient_name", "Patient"),
            ("encounter_no", "Encounter"), ("admission", "Admission"),
            ("discharge", "Discharge"), ("confidence", "Confidence"),
            ("batch_id", "Batch"), ("created_at", "Created"),
            ("resolved_by", "Resolved by"), ("resolved_at", "Resolved at"),
        )
        for row, (key, label) in enumerate(labels):
            ttk.Label(info, text=f"{label}:").grid(row=row, column=0, sticky="nw", pady=2)
            ttk.Label(
                info, textvariable=self.detail_vars[key], wraplength=350
            ).grid(row=row, column=1, sticky="nw", padx=(10, 0), pady=2)
        info.columnconfigure(1, weight=1)

        ttk.Label(parent, text="Issue details:").pack(anchor="w", pady=(10, 2))
        self.issue_text = self._readonly_text(parent, height=5)
        ttk.Label(parent, text="Resolution note:").pack(anchor="w", pady=(10, 2))
        self.resolution_text = self._readonly_text(parent, height=4)

        docs_frame = ttk.LabelFrame(parent, text="Files Under Review", padding=6)
        docs_frame.pack(fill="both", expand=True, pady=(10, 0))
        ttk.Label(
            docs_frame,
            textvariable=self.file_summary_var,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            docs_frame,
            textvariable=self.issue_scope_var,
            wraplength=380,
            foreground="#8A4B08",
        ).pack(anchor="w", pady=(2, 6))
        self.documents = tk.Listbox(docs_frame, height=7)
        self.documents.pack(fill="both", expand=True)
        self.documents.bind("<Double-1>", lambda _event: self.open_document())

    @staticmethod
    def _readonly_text(parent: ttk.Frame, height: int) -> tk.Text:
        widget = tk.Text(parent, height=height, wrap="word", state="disabled")
        widget.pack(fill="x")
        return widget

    def _build_actions(self, parent: ttk.Frame) -> None:
        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Start Review", command=self.start_review).pack(side="left")
        ttk.Button(
            actions,
            text="Correct Hospital No.",
            command=self.correct_hospital_no,
        ).pack(side="left", padx=6)
        ttk.Button(actions, text="Resolve", command=self.resolve).pack(side="left", padx=6)
        ttk.Button(actions, text="Skip", command=self.skip).pack(side="left")
        ttk.Button(actions, text="Reopen", command=self.reopen).pack(side="left", padx=6)
        ttk.Button(actions, text="Resume Processing", command=self.resume_processing).pack(
            side="left"
        )
        ttk.Button(actions, text="Open Folder", command=self.open_folder).pack(side="right")
        ttk.Button(actions, text="Open Document", command=self.open_document).pack(
            side="right", padx=6
        )
        ttk.Label(
            parent,
            textvariable=self.resume_queue_var,
            foreground="#1D4ED8",
        ).pack(anchor="w", pady=(6, 0))

    def refresh(self, select_id: int | None = None) -> None:
        try:
            items = self.queue.list_items(self._selected_statuses())
        except Exception as exc:
            logger.error(f"Review Queue refresh failed: {exc}")
            messagebox.showerror("Review Queue", f"Could not load queue:\n\n{exc}")
            return
        self.items_by_id = {item.id: item for item in items}
        self.table.delete(*self.table.get_children())
        for item in items:
            self.table.insert(
                "", "end", iid=str(item.id),
                values=(
                    item.id, item.status, item.reason, item.hospital_no,
                    item.patient_name, len(item.documents), item.admission_date,
                    item.created_at,
                ),
            )
        self.summary_var.set(f"{len(items)} review item{'s' if len(items) != 1 else ''}")
        self.last_refresh_var.set(
            f"Last refresh: {datetime.now().strftime('%I:%M:%S %p')}"
        )
        target = select_id if select_id in self.items_by_id else None
        if target is None and items:
            target = items[0].id
        if target is not None:
            self.table.selection_set(str(target))
            self.table.focus(str(target))
            self.table.see(str(target))
            self._show_item(self.items_by_id[target])
        else:
            self._clear_details()

    def _schedule_auto_refresh(self) -> None:
        if self._auto_refresh_after_id is not None:
            self.root.after_cancel(self._auto_refresh_after_id)
            self._auto_refresh_after_id = None
        if self.auto_refresh_var.get():
            self._auto_refresh_after_id = self.root.after(
                10000, self._auto_refresh_tick
            )

    def _auto_refresh_tick(self) -> None:
        selected = self._selected_item(show_error=False)
        self.refresh(select_id=selected.id if selected else None)
        self._schedule_auto_refresh()

    def close(self) -> None:
        if self._auto_refresh_after_id is not None:
            self.root.after_cancel(self._auto_refresh_after_id)
            self._auto_refresh_after_id = None
        if self._resume_monitor_after_id is not None:
            self.root.after_cancel(self._resume_monitor_after_id)
            self._resume_monitor_after_id = None
        self.root.destroy()

    def _selected_statuses(self) -> tuple[str, ...] | None:
        selected = self.status_var.get()
        if selected == "ALL":
            return None
        if selected == "ACTIVE":
            return (ReviewStatus.PENDING.value, ReviewStatus.IN_REVIEW.value)
        return (selected,)

    def _on_select(self, _event: tk.Event) -> None:
        item = self._selected_item(show_error=False)
        if item:
            self._show_item(item)

    def _on_double_click(self, _event: tk.Event) -> None:
        """Start only pending items; active items remain selected for actions."""
        item = self._selected_item(show_error=False)
        if item and item.status == ReviewStatus.PENDING.value:
            self.start_review()

    def _selected_item(self, show_error: bool = True) -> ReviewItem | None:
        selection = self.table.selection()
        if not selection:
            if show_error:
                messagebox.showwarning("Review Queue", "Select a review item first.")
            return None
        return self.items_by_id.get(int(selection[0]))

    def _show_item(self, item: ReviewItem) -> None:
        values = {
            "id": str(item.id), "status": item.status, "reason": item.reason,
            "hospital_no": item.hospital_no, "patient_name": item.patient_name,
            "encounter_no": item.encounter_no, "admission": item.admission_date,
            "discharge": item.discharge_date,
            "confidence": f"{item.confidence}%", "batch_id": item.batch_id,
            "created_at": item.created_at, "resolved_by": item.resolved_by,
            "resolved_at": item.resolved_at or "",
        }
        for key, value in values.items():
            self.detail_vars[key].set(value)
        self._set_text(self.issue_text, item.reason_detail)
        self._set_text(self.resolution_text, item.resolution_note)
        self.documents.delete(0, tk.END)
        self.document_paths = [Path(document) for document in item.documents]
        count = len(self.document_paths)
        self.file_summary_var.set(
            f"{count} file{'s' if count != 1 else ''} under review"
        )
        self.issue_scope_var.set(self._issue_scope(item.reason))
        for index, document in enumerate(self.document_paths, start=1):
            self.documents.insert(tk.END, f"{index:02d}. {document.name}")
        if self.document_paths:
            self.documents.selection_set(0)

    def _clear_details(self) -> None:
        for variable in self.detail_vars.values():
            variable.set("")
        self._set_text(self.issue_text, "")
        self._set_text(self.resolution_text, "")
        self.documents.delete(0, tk.END)
        self.document_paths = []
        self.file_summary_var.set("0 files under review")
        self.issue_scope_var.set("")

    @staticmethod
    def _issue_scope(reason: str) -> str:
        guidance = {
            "H001": "Check the SOA1 Hospital Number against HBSys.",
            "H002": "OCR/document detection failed. Use Unknown Review Manager for document-type correction.",
            "H003": "The whole patient group is valid, but admission selection is required.",
            "H004": "Compare the COE patient name with the verified HBSys name.",
            "H005": "SOA1 is missing from this patient group.",
            "H006": "COE is missing from this patient group.",
            "H007": "Select the correct admission for this patient group.",
            "H008": "Review the manually overridden patient identity.",
            "H009": "Compare this entire group with the other matching Hospital Number.",
            "H010": "Compare this entire group with the duplicate encounter.",
        }
        return guidance.get(reason, "Inspect the listed patient documents.")

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def start_review(self) -> None:
        self._run_status_action(
            lambda item: self.queue.start_review(item.id),
            allowed=(ReviewStatus.PENDING.value,),
            success="Review started.",
        )

    def resolve(self) -> None:
        item = self._selected_item()
        if not item:
            return
        if item.status not in (ReviewStatus.PENDING.value, ReviewStatus.IN_REVIEW.value):
            messagebox.showwarning("Review Queue", "Only active items can be resolved.")
            return
        self._execute(
            lambda: self.queue.resolve(
                item.id,
                "Resolved from Patient Review Queue.",
                AUTO_REVIEW_USER,
            ),
            item.id,
            "Review resolved.",
        )

    def correct_hospital_no(self) -> None:
        item = self._selected_item()
        if not item:
            return
        hospital_no = simpledialog.askstring(
            "Correct Hospital Number",
            "Enter the correct Hospital Number:",
            initialvalue=item.hospital_no,
            parent=self.root,
        )
        if not hospital_no or not hospital_no.strip():
            return
        try:
            candidate = self.corrections.search(item.id, hospital_no)
        except Exception as exc:
            logger.error(f"Patient correction search failed: {exc}")
            messagebox.showerror("Patient Search", str(exc), parent=self.root)
            return
        self._show_correction_candidate(candidate)

    def _show_correction_candidate(
        self,
        candidate: CorrectionCandidate,
        *,
        resume_after_selection: bool = False,
    ) -> None:
        window = tk.Toplevel(self.root)
        window.title("Verify Corrected Patient")
        window.geometry("650x430")
        window.transient(self.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="VERIFIED HBSYS PATIENT",
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                f"Hospital No.: {candidate.patient.hospital_no}\n"
                f"Patient: {candidate.patient.patient_name}\n"
                f"Name match score: {candidate.name_match_score}%"
            ),
        ).pack(anchor="w", pady=(6, 12))
        ttk.Label(frame, text="Select admission:").pack(anchor="w")
        admissions = ttk.Treeview(
            frame,
            columns=("encounter", "admission", "discharge"),
            show="headings",
            height=8,
        )
        for key, label, width in (
            ("encounter", "Encounter", 260),
            ("admission", "Admission", 120),
            ("discharge", "Discharge", 120),
        ):
            admissions.heading(key, text=label)
            admissions.column(key, width=width, anchor="w")
        for index, admission in enumerate(candidate.admissions):
            admissions.insert(
                "", "end", iid=str(index),
                values=(
                    admission.encounter_no,
                    admission.admission_date,
                    admission.discharge_date,
                ),
            )
        admissions.pack(fill="both", expand=True, pady=6)
        if len(candidate.admissions) == 1:
            admissions.selection_set("0")

        def apply() -> None:
            selection = admissions.selection()
            if not selection:
                messagebox.showwarning(
                    "Patient Correction", "Select an admission first.", parent=window
                )
                return
            selected = candidate.admissions[int(selection[0])]
            try:
                changed, remaining = self.corrections.apply(
                    candidate,
                    encounter_no=selected.encounter_no,
                    corrected_by=AUTO_REVIEW_USER,
                    note=(
                        "Verified admission selected before Resume Processing."
                        if resume_after_selection
                        else "Hospital Number corrected from Patient Review Queue."
                    ),
                    preserve_resolved_status=resume_after_selection,
                )
            except Exception as exc:
                logger.error(f"Patient correction failed: {exc}")
                messagebox.showerror("Patient Correction", str(exc), parent=window)
                return
            if changed:
                window.destroy()
                self.refresh(select_id=candidate.review_id)
                if resume_after_selection:
                    self._enqueue_resume(
                        candidate.review_id,
                        source_message=(
                            "Verified confinement selected. Resume Processing "
                            "will start automatically."
                        ),
                    )
                elif remaining:
                    message = (
                        "Patient identity corrected. Remaining review issues: "
                        + ", ".join(remaining)
                    )
                    messagebox.showinfo("Patient Correction", message, parent=self.root)
                else:
                    self._enqueue_resume(
                        candidate.review_id,
                        source_message="Patient corrected. Resume Processing will start automatically.",
                    )

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(10, 0))
        ttk.Button(controls, text="Cancel", command=window.destroy).pack(side="right")
        ttk.Button(controls, text="Apply Correction", command=apply).pack(
            side="right", padx=8
        )

    def skip(self) -> None:
        item = self._selected_item()
        if not item:
            return
        if item.status not in (ReviewStatus.PENDING.value, ReviewStatus.IN_REVIEW.value):
            messagebox.showwarning("Review Queue", "Only active items can be skipped.")
            return
        self._execute(
            lambda: self.queue.skip(
                item.id,
                "Skipped from Patient Review Queue.",
                AUTO_REVIEW_USER,
            ),
            item.id,
            "Review skipped.",
        )

    def reopen(self) -> None:
        self._run_status_action(
            lambda item: self.queue.reopen(item.id),
            allowed=(ReviewStatus.RESOLVED.value, ReviewStatus.SKIPPED.value),
            success="Review reopened.",
        )

    def resume_processing(self) -> None:
        item = self._selected_item()
        if not item:
            return
        if item.status != ReviewStatus.RESOLVED.value:
            messagebox.showwarning(
                "Resume Processing",
                "Only successfully resolved items can be resumed.",
            )
            return
        if not item.hospital_no or not item.patient_name:
            messagebox.showwarning(
                "Resume Processing",
                "A verified Hospital Number and patient identity are required. "
                "Use Correct Hospital No. first.",
                parent=self.root,
            )
            return
        if not item.encounter_no or not item.admission_date or not item.discharge_date:
            try:
                candidate = self.corrections.search(item.id, item.hospital_no)
            except Exception as exc:
                logger.error(f"Admission selection search failed: {exc}")
                messagebox.showerror("Resume Processing", str(exc), parent=self.root)
                return
            if not candidate.admissions:
                messagebox.showerror(
                    "Resume Processing",
                    "No HBSys admission was found for this patient.",
                    parent=self.root,
                )
                return
            self._show_correction_candidate(
                candidate,
                resume_after_selection=True,
            )
            return
        if not messagebox.askyesno(
            "Resume Processing",
            f"Resume claims processing for:\n\n{item.patient_name}?",
            parent=self.root,
        ):
            return
        self._enqueue_resume(item.id, source_message="Resume Processing queued.")

    def _enqueue_resume(self, review_id: int, source_message: str = "") -> None:
        if review_id == self._resume_active_id or review_id in self._resume_waiting_ids:
            self.resume_queue_var.set(
                f"Auto Resume: review #{review_id} is already queued/running"
            )
            return

        item = self.queue.get(review_id)
        if item is None:
            messagebox.showerror("Auto Resume", f"Review item #{review_id} was not found.")
            return
        if item.status != ReviewStatus.RESOLVED.value:
            messagebox.showwarning(
                "Auto Resume",
                f"Review #{review_id} is {item.status}; only RESOLVED items can resume.",
                parent=self.root,
            )
            self.refresh(select_id=review_id)
            return

        self._resume_waiting_ids.append(review_id)
        waiting_count = len(self._resume_waiting_ids)
        self.resume_queue_var.set(
            f"Auto Resume: queued #{review_id}"
            + (f" ({waiting_count} waiting)" if waiting_count > 1 else "")
        )
        if source_message:
            messagebox.showinfo("Auto Resume", source_message, parent=self.root)
        self._pump_resume_queue()

    def _pump_resume_queue(self) -> None:
        if self._resume_active_process is not None:
            if self._resume_active_process.poll() is None:
                self._schedule_resume_monitor()
                return
            finished_id = self._resume_active_id
            self._resume_active_process = None
            self._resume_active_id = None
            if finished_id is not None:
                self.refresh(select_id=finished_id)

        try:
            currently_resuming = self.queue.list_items((ReviewStatus.RESUMING.value,))
        except Exception as exc:
            logger.error(f"Auto Resume status check failed: {exc}")
            self.resume_queue_var.set(f"Auto Resume: waiting, status check failed: {exc}")
            self._schedule_resume_monitor()
            return

        if currently_resuming:
            ids = ", ".join(f"#{item.id}" for item in currently_resuming)
            waiting = len(self._resume_waiting_ids)
            self.resume_queue_var.set(
                f"Auto Resume: waiting for active resume {ids}"
                + (f" ({waiting} queued)" if waiting else "")
            )
            self._schedule_resume_monitor()
            return

        while self._resume_waiting_ids:
            review_id = self._resume_waiting_ids.pop(0)
            item = self.queue.get(review_id)
            if item is None or item.status != ReviewStatus.RESOLVED.value:
                continue
            try:
                process = self.resume_manager.resume(review_id)
            except Exception as exc:
                logger.error(f"Auto Resume failed to launch for id={review_id}: {exc}")
                messagebox.showerror("Auto Resume", str(exc), parent=self.root)
                self.refresh(select_id=review_id)
                continue

            self._resume_active_id = review_id
            self._resume_active_process = process
            self.resume_queue_var.set(
                f"Auto Resume: processing #{review_id} (PID {process.pid})"
            )
            self.refresh(select_id=review_id)
            self._schedule_resume_monitor()
            return

        self.resume_queue_var.set("Auto Resume: idle")

    def _schedule_resume_monitor(self) -> None:
        if self._resume_monitor_after_id is not None:
            self.root.after_cancel(self._resume_monitor_after_id)
        self._resume_monitor_after_id = self.root.after(3000, self._resume_monitor_tick)

    def _resume_monitor_tick(self) -> None:
        self._resume_monitor_after_id = None
        self._pump_resume_queue()

    def _run_status_action(
        self,
        action: Callable[[ReviewItem], bool],
        *,
        allowed: tuple[str, ...],
        success: str,
    ) -> None:
        item = self._selected_item()
        if not item:
            return
        if item.status not in allowed:
            messagebox.showwarning(
                "Review Queue", f"Action is not allowed for status {item.status}."
            )
            return
        self._execute(lambda: action(item), item.id, success)

    def _execute(self, action: Callable[[], bool], item_id: int, success: str) -> None:
        try:
            changed = action()
        except Exception as exc:
            logger.error(f"Review Queue action failed for id={item_id}: {exc}")
            messagebox.showerror("Review Queue", str(exc))
            return
        if changed:
            self.refresh(select_id=item_id)
            messagebox.showinfo("Review Queue", success)

    def open_folder(self) -> None:
        item = self._selected_item()
        if item:
            self._open_path(Path(item.folder), expect_directory=True)

    def open_document(self) -> None:
        selection = self.documents.curselection()
        if not selection:
            messagebox.showwarning("Review Queue", "Select a source document first.")
            return
        index = int(selection[0])
        if index >= len(self.document_paths):
            messagebox.showerror("Review Queue", "Document selection is unavailable.")
            return
        self._open_path(self.document_paths[index])

    @staticmethod
    def _open_path(path: Path, expect_directory: bool = False) -> None:
        valid = path.is_dir() if expect_directory else path.is_file()
        if not valid:
            messagebox.showerror("Review Queue", f"Path is unavailable:\n\n{path}")
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("Review Queue", f"Could not open path:\n\n{exc}")


def main() -> None:
    root = tk.Tk()
    PatientReviewQueueWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
