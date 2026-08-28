"""Add Claims Upload GUI Tab.

Provides a Tkinter frame for the eClaims Upload Claims automation.
Displays the patient list from READY, allows dry-run/live mode selection,
and shows real-time upload progress.

Integrated into the main EDH Claims GUI as a notebook tab.
"""

from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

# Ensure project root is on sys.path for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.add_claims_uploader import (
    AddClaimsOperator,
    load_patients,
    run_upload_loop,
)
from core.add_claims_state import UploadState
from core.add_claims_verifier import FolderDates


DEFAULT_READY_DIR = Path(r"C:\claims_bot\claims_checker_results\READY")


class AddClaimsUploadFrame(ttk.Frame):
    """GUI frame for the Add Claims Upload automation."""

    def __init__(
        self,
        parent: ttk.Frame,
        settings_getter: Callable[[], dict],
        log_callback: Callable[[str], None] | None = None,
    ):
        super().__init__(parent)
        self.settings_getter = settings_getter
        self.log_callback = log_callback or (lambda msg: None)
        self.patients: list[FolderDates] = []
        self.state = UploadState()
        self.upload_thread: Optional[threading.Thread] = None
        self.stop_requested = False

        self.build_ui()
        self.refresh_patient_list()

    # -- UI construction --------------------------------------------------

    def build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 10))

        ttk.Label(
            header,
            text="Add Claims Upload",
            font=("Segoe UI", 16, "bold"),
        ).pack(side="left")

        ttk.Label(
            header,
            text="Automate eClaims Upload Claims for READY patients",
            style="PanelMuted.TLabel",
        ).pack(side="left", padx=16)

        # -- Top controls -------------------------------------------------
        controls = ttk.LabelFrame(self, text="Controls", padding=10)
        controls.pack(fill="x", pady=(0, 8))

        row1 = ttk.Frame(controls)
        row1.pack(fill="x", pady=(0, 6))

        ttk.Label(row1, text="Ready Folder:").pack(side="left")
        self.ready_dir_var = tk.StringVar(value=str(DEFAULT_READY_DIR))
        ttk.Entry(row1, textvariable=self.ready_dir_var, width=60).pack(
            side="left", padx=(6, 8), fill="x", expand=True
        )
        ttk.Button(row1, text="Browse", command=self.browse_ready_dir).pack(
            side="left"
        )
        ttk.Button(row1, text="Refresh", command=self.refresh_patient_list).pack(
            side="left", padx=(8, 0)
        )

        row2 = ttk.Frame(controls)
        row2.pack(fill="x", pady=(0, 6))

        self.mode_var = tk.StringVar(value="dry-run")
        ttk.Radiobutton(
            row2, text="Dry-Run (safe)", variable=self.mode_var, value="dry-run"
        ).pack(side="left")
        ttk.Radiobutton(
            row2, text="Live (clicks HBSys)", variable=self.mode_var, value="live"
        ).pack(side="left", padx=(12, 0))

        self.confirm_each_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            row2, text="Confirm each patient", variable=self.confirm_each_var
        ).pack(side="left", padx=(20, 0))

        row3 = ttk.Frame(controls)
        row3.pack(fill="x")

        self.patient_count_var = tk.StringVar(value="Patients: 0")
        ttk.Label(row3, textvariable=self.patient_count_var, font=("Segoe UI", 10, "bold")).pack(
            side="left"
        )

        self.state_var = tk.StringVar(value="Idle")
        ttk.Label(row3, textvariable=self.state_var, foreground="blue").pack(
            side="left", padx=(20, 0)
        )

        # -- Buttons ------------------------------------------------------
        btn_row = ttk.Frame(controls)
        btn_row.pack(fill="x", pady=(8, 0))

        self.start_btn = ttk.Button(
            btn_row,
            text="Start Upload",
            style="Primary.TButton",
            command=self.start_upload,
        )
        self.start_btn.pack(side="left")

        self.stop_btn = ttk.Button(
            btn_row,
            text="Stop",
            style="Danger.TButton",
            command=self.stop_upload,
            state="disabled",
        )
        self.stop_btn.pack(side="left", padx=(8, 0))

        self.resume_btn = ttk.Button(
            btn_row,
            text="Resume Batch",
            command=self.resume_upload,
        )
        self.resume_btn.pack(side="left", padx=(8, 0))

        ttk.Separator(btn_row, orient="vertical").pack(side="left", fill="y", padx=(12, 8))

        self.calibrate_btn = ttk.Button(
            btn_row,
            text="Calibrate Coordinates",
            command=self.open_calibration,
        )
        self.calibrate_btn.pack(side="left")

        self.detect_btn = ttk.Button(
            btn_row,
            text="Detect Popup",
            command=self.detect_popup,
        )
        self.detect_btn.pack(side="left", padx=(8, 0))

        self.coord_getter_btn = ttk.Button(
            btn_row,
            text="Coordinate Getter",
            command=self.open_coordinate_getter,
        )
        self.coord_getter_btn.pack(side="left", padx=(8, 0))

        # -- Patient list --------------------------------------------------
        list_frame = ttk.LabelFrame(self, text="Patients from READY Folder", padding=8)
        list_frame.pack(fill="both", expand=True, pady=(8, 0))

        columns = ("no", "name", "hospital_no", "admission", "discharge", "status")
        self.patient_tree = ttk.Treeview(
            list_frame, columns=columns, show="headings", height=12
        )

        self.patient_tree.heading("no", text="#")
        self.patient_tree.heading("name", text="Patient Name")
        self.patient_tree.heading("hospital_no", text="Hospital No.")
        self.patient_tree.heading("admission", text="Admission")
        self.patient_tree.heading("discharge", text="Discharge")
        self.patient_tree.heading("status", text="Status")

        self.patient_tree.column("no", width=40, anchor="center")
        self.patient_tree.column("name", width=280)
        self.patient_tree.column("hospital_no", width=140)
        self.patient_tree.column("admission", width=100, anchor="center")
        self.patient_tree.column("discharge", width=100, anchor="center")
        self.patient_tree.column("status", width=100, anchor="center")

        tree_scroll = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.patient_tree.yview
        )
        self.patient_tree.configure(yscrollcommand=tree_scroll.set)

        self.patient_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        # -- Progress bar --------------------------------------------------
        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", pady=(8, 0))

        # -- Status log ----------------------------------------------------
        log_frame = ttk.LabelFrame(self, text="Upload Log", padding=8)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))

        self.log_text = tk.Text(
            log_frame,
            bg="#111827",
            fg="#DDE7F3",
            insertbackground="#FFFFFF",
            font=("Cascadia Mono", 9),
            wrap="word",
            relief="flat",
            padx=8,
            pady=6,
            height=8,
        )
        self.log_text.pack(side="left", fill="both", expand=True)

        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=log_scroll.set)

    # -- Actions ----------------------------------------------------------

    def browse_ready_dir(self) -> None:
        from tkinter import filedialog

        path = filedialog.askdirectory(
            title="Select READY Folder",
            initialdir=self.ready_dir_var.get(),
        )
        if path:
            self.ready_dir_var.set(path)
            self.refresh_patient_list()

    def refresh_patient_list(self) -> None:
        """Reload patient folders from the READY directory."""
        ready_dir = Path(self.ready_dir_var.get())
        self.patients = load_patients(ready_dir)

        # Clear treeview
        for item in self.patient_tree.get_children():
            self.patient_tree.delete(item)

        # Populate
        for idx, patient in enumerate(self.patients, start=1):
            self.patient_tree.insert(
                "",
                "end",
                values=(
                    idx,
                    patient.patient_name,
                    patient.hospital_no,
                    patient.admission_str,
                    patient.discharge_str,
                    "Pending",
                ),
            )

        self.patient_count_var.set(f"Patients: {len(self.patients)}")
        self.progress["maximum"] = max(len(self.patients), 1)
        self.progress["value"] = 0

        # Load existing state if available
        self.state = UploadState.load()
        if self.state.status == "in_progress":
            self.state_var.set(f"Resumable: {self.state.processed}/{self.state.total_patients}")
        else:
            self.state_var.set("Idle")

        self.log(f"Loaded {len(self.patients)} patients from {ready_dir}")

    def update_patient_status(self, index: int, status: str, color: str = "") -> None:
        """Update the status column of a patient row."""
        items = self.patient_tree.get_children()
        if index < len(items):
            values = list(self.patient_tree.item(items[index], "values"))
            values[5] = status
            self.patient_tree.item(items[index], values=values)
            if color:
                self.patient_tree.tag_configure(status, foreground=color)
                self.patient_tree.item(items[index], tags=(status,))

    def log(self, message: str) -> None:
        """Append a message to the upload log."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {message}\n")
        self.log_text.see("end")
        self.log_callback(message)

    # -- Calibration ------------------------------------------------------

    def open_calibration(self) -> None:
        """Open the coordinate calibration utility."""
        import subprocess
        import sys

        self.log("Opening coordinate calibration...")

        try:
            env = os.environ.copy()
            env["CLAIMS_GUI_MODE"] = "1"

            # Run calibration in a separate process
            subprocess.Popen(
                [sys.executable, "-m", "core.add_claims_calibration"],
                cwd=str(PROJECT_ROOT),
                env=env,
            )
            self.log("Calibration utility opened. Follow the prompts in the console.")
        except Exception as exc:
            self.log(f"ERROR: Could not open calibration: {exc}")
            messagebox.showerror(
                "Calibration Error",
                f"Could not open calibration utility:\n\n{exc}",
            )

    def detect_popup(self) -> None:
        """Detect the Upload Claims popup position."""
        import subprocess
        import sys

        self.log("Detecting Upload Claims popup...")

        try:
            env = os.environ.copy()
            env["CLAIMS_GUI_MODE"] = "1"

            # Run detection in a separate process
            result = subprocess.run(
                [sys.executable, "-m", "core.add_claims_calibration", "--detect"],
                cwd=str(PROJECT_ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

            # Show output in log
            if result.stdout:
                for line in result.stdout.strip().split("\n"):
                    self.log(f"  {line}")
            if result.stderr:
                for line in result.stderr.strip().split("\n"):
                    self.log(f"  {line}")

            if result.returncode == 0:
                self.log("Popup detection completed.")
            else:
                self.log("Popup detection failed. Make sure the popup is open.")
        except subprocess.TimeoutExpired:
            self.log("Popup detection timed out.")
        except Exception as exc:
            self.log(f"ERROR: Could not detect popup: {exc}")

    def open_coordinate_getter(self) -> None:
        """Open the interactive coordinate getter tool."""
        import subprocess
        import sys

        self.log("Opening Coordinate Getter...")

        try:
            env = os.environ.copy()
            env["CLAIMS_GUI_MODE"] = "1"

            # Run coordinate getter in a separate process
            subprocess.Popen(
                [sys.executable, "-m", "core.coordinate_getter"],
                cwd=str(PROJECT_ROOT),
                env=env,
            )
            self.log("Coordinate Getter opened. Click on elements to capture coordinates.")
        except Exception as exc:
            self.log(f"ERROR: Could not open Coordinate Getter: {exc}")
            messagebox.showerror(
                "Coordinate Getter Error",
                f"Could not open Coordinate Getter:\n\n{exc}",
            )

    # -- Upload control ---------------------------------------------------

    def start_upload(self) -> None:
        """Start the upload in a background thread."""
        if self.upload_thread is not None and self.upload_thread.is_alive():
            messagebox.showwarning("Add Claims Upload", "Upload is already running.")
            return

        if not self.patients:
            messagebox.showinfo(
                "Add Claims Upload", "No patients found in the READY folder."
            )
            return

        live_mode = self.mode_var.get() == "live"
        if live_mode:
            if not messagebox.askyesno(
                "Confirm Live Mode",
                "Live mode will click/type in HBSys.\n\n"
                "Make sure HBSys is open and visible.\n\n"
                "Continue?",
            ):
                return

        self.stop_requested = False
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        # Reset all patient statuses
        for item in self.patient_tree.get_children():
            values = list(self.patient_tree.item(item, "values"))
            values[5] = "Pending"
            self.patient_tree.item(item, values=values)

        self.progress["value"] = 0

        self.upload_thread = threading.Thread(
            target=self._upload_worker,
            args=(live_mode,),
            daemon=True,
        )
        self.upload_thread.start()

    def stop_upload(self) -> None:
        """Signal the upload to stop after the current patient."""
        self.stop_requested = True
        self.log("Stop requested. Will stop after current patient...")

    def resume_upload(self) -> None:
        """Resume a previously interrupted batch."""
        state = UploadState.load()
        if state.status not in ("in_progress", "failed"):
            messagebox.showinfo(
                "Add Claims Upload", "No interrupted batch to resume."
            )
            return

        self.log(f"Resuming batch {state.batch_id} from patient {state.processed + 1}...")
        self.start_upload()

    def _upload_worker(self, live_mode: bool) -> None:
        """Background worker for the upload loop."""
        try:
            ready_dir = Path(self.ready_dir_var.get())
            confirm_each = self.confirm_each_var.get()

            # Initialize state
            if self.state.status != "in_progress":
                self.state.mark_started(len(self.patients))

            operator = AddClaimsOperator(
                live=live_mode,
                pause=0.35,
                confirm_each=confirm_each,
            )

            self.after(0, lambda: self.log(f"Mode: {'LIVE' if live_mode else 'DRY-RUN'}"))
            self.after(0, lambda: self.log(f"Patients: {len(self.patients)}"))

            # Steps 1-3: Navigate to Add Claims popup
            self.after(0, lambda: self.log("Focusing HBSys window..."))
            operator.focus_hbsys()

            self.after(0, lambda: self.log("Step 1: Click eClaims"))
            operator.click_eclaims()

            self.after(0, lambda: self.log("Step 2: Click Upload Att"))
            operator.click_upload_att()

            self.after(0, lambda: self.log("Step 3: Click Add Claims"))
            operator.click_add_claims()

            if not operator.find_popup():
                self.after(0, lambda: self.log("ERROR: Upload Claims popup did not appear."))
                self.after(0, self._upload_finished)
                return

            # Step 4: Process each patient
            for idx, patient in enumerate(self.patients):
                if self.stop_requested:
                    self.after(0, lambda: self.log("Upload stopped by user."))
                    break

                if idx < self.state.processed:
                    # Already processed in a previous run
                    self.after(0, lambda i=idx: self.update_patient_status(i, "Skipped", "gray"))
                    continue

                patient_label = (
                    f"{patient.patient_name} - {patient.hospital_no} - "
                    f"ADM{patient.admission.strftime('%Y%m%d')}_DIS{patient.discharge.strftime('%Y%m%d')}"
                )
                self.state.current_patient = patient_label

                self.after(0, lambda i=idx, p=patient: self._update_processing(i, p))

                # Search patient
                operator.search_patient(patient.patient_name)

                # Verify confinement
                verify_result = operator.read_and_verify_highlighted_row(patient)

                if not verify_result.match:
                    reason = verify_result.reason or "confinement mismatch"
                    self.state.mark_failed(patient.patient_name, reason)
                    self.state.save()
                    self.after(
                        0,
                        lambda i=idx, r=reason: (
                            self.update_patient_status(i, "FAILED", "red"),
                            self.log(f"  FAIL: {r}"),
                        ),
                    )
                    operator.clear_search_box()
                    continue

                # Click checkbox
                if not operator.click_checkbox_of_highlighted_row():
                    self.state.mark_failed(patient.patient_name, "checkbox click failed")
                    self.state.save()
                    self.after(
                        0,
                        lambda i=idx: (
                            self.update_patient_status(i, "FAILED", "red"),
                            self.log("  FAIL: checkbox click failed"),
                        ),
                    )
                    operator.clear_search_box()
                    continue

                # Success
                self.state.mark_processed(patient.patient_name)
                self.state.save()
                self.after(
                    0,
                    lambda i=idx: (
                        self.update_patient_status(i, "DONE", "green"),
                        self.log(f"  OK: {patient.patient_name}"),
                    ),
                )
                operator.clear_search_box()

            # Finalize
            self.after(0, lambda: self.log("Finalizing: Click Add..."))
            operator.click_add()

            self.after(0, lambda: self.log("Finalizing: Click OK..."))
            operator.click_ok()

            self.after(0, lambda: self.log("Finalizing: Click Close..."))
            operator.click_close()

            self.state.mark_completed()
            self.state.save()

        except Exception as exc:
            self.after(0, lambda e=exc: self.log(f"ERROR: {e}"))
            self.state.mark_failed_batch()
            self.state.save()
        finally:
            self.after(0, self._upload_finished)

    def _update_processing(self, index: int, patient: FolderDates) -> None:
        """Update UI to show which patient is being processed."""
        self.update_patient_status(index, "Processing...", "orange")
        self.progress["value"] = index
        self.log(f"Processing {index + 1}/{len(self.patients)}: {patient.patient_name}")

    def _upload_finished(self) -> None:
        """Called when the upload thread completes."""
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.progress["value"] = len(self.patients)

        # Print summary
        self.log("=" * 50)
        self.log("BATCH SUMMARY")
        self.log(self.state.summary)
        self.log("=" * 50)

        self.state_var.set(
            f"Completed: {self.state.processed}/{self.state.total_patients}"
        )

        if self.state.failed:
            messagebox.showwarning(
                "Add Claims Upload",
                f"Upload completed with {len(self.state.failed)} failure(s).\n\n"
                f"Failed patients:\n" + "\n".join(f"  - {name}" for name in self.state.failed),
            )
        else:
            messagebox.showinfo(
                "Add Claims Upload",
                f"Upload completed successfully!\n\n"
                f"Processed: {self.state.processed}/{self.state.total_patients}",
            )


# -- standalone test -----------------------------------------------------

if __name__ == "__main__":
    root = tk.Tk()
    root.title("Add Claims Upload - Test")
    root.geometry("900x700")

    frame = AddClaimsUploadFrame(
        root,
        settings_getter=lambda: {},
        log_callback=print,
    )
    frame.pack(fill="both", expand=True, padx=10, pady=10)

    root.mainloop()
