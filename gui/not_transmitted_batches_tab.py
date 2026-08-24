"""Tkinter tab for Regular and ABTC Missing Date Fill batches."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from core.not_transmitted_batches import (
    ClaimBatchWorkbookService,
    ClaimFolderRecord,
    NotTransmittedClaimsRepository,
    WorkbookExportResult,
)


class NotTransmittedBatchesFrame(ttk.Frame):
    """Export configurable patient batches and create one selected folder batch."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        settings_getter: Callable[[], dict],
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent, padding=8)
        self.settings_getter = settings_getter
        self.log_callback = log_callback
        self.repository = NotTransmittedClaimsRepository()
        self.workbooks = ClaimBatchWorkbookService()
        self.claim_type_var = tk.StringVar(value="Regular")
        self.year_var = tk.StringVar(value=str(datetime.now().year))
        self.batch_size_var = tk.StringVar(value="5")
        self.workbook_var = tk.StringVar(value="")
        self.sheet_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Ready")
        self.count_var = tk.StringVar(value="No workbook loaded")
        self.current_records: tuple[ClaimFolderRecord, ...] = ()
        self._busy_buttons: list[ttk.Button] = []
        self._build_ui()
        self._load_available_years()

    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(
            header,
            text="REGULAR / ABTC MISSING DATE FILL BATCHES",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "Extract Regular or ABTC patients with at least one missing "
                "Date Fill field (read-only). Claims with a claim-map record "
                "are excluded."
            ),
        ).pack(anchor="w", pady=(3, 0))

        source = ttk.LabelFrame(
            self,
            text="1. Extract Patients with Missing Date Fill Fields",
            padding=12,
        )
        source.pack(fill="x", pady=(0, 10))
        ttk.Label(source, text="Claim Type:").grid(row=0, column=0, sticky="w")
        self.claim_type_combo = ttk.Combobox(
            source,
            textvariable=self.claim_type_var,
            values=("Regular", "ABTC"),
            state="readonly",
            width=10,
        )
        self.claim_type_combo.grid(row=0, column=1, sticky="w", padx=(8, 14))
        ttk.Label(source, text="Year:").grid(row=0, column=2, sticky="w")
        self.year_combo = ttk.Combobox(
            source,
            textvariable=self.year_var,
            values=(str(datetime.now().year),),
            state="readonly",
            width=12,
        )
        self.year_combo.grid(row=0, column=3, sticky="w", padx=(8, 14))
        ttk.Label(source, text="Patients per sheet:").grid(
            row=0, column=4, sticky="w"
        )
        self.batch_size_combo = ttk.Combobox(
            source,
            textvariable=self.batch_size_var,
            values=tuple(str(value) for value in range(1, 51)),
            state="readonly",
            width=7,
        )
        self.batch_size_combo.grid(row=0, column=5, sticky="w", padx=(8, 14))
        export_button = ttk.Button(
            source,
            text="Extract to Excel",
            command=self.extract_to_excel,
        )
        export_button.grid(row=0, column=6, sticky="w")
        self._busy_buttons.append(export_button)
        ttk.Label(
            source,
            text="Regular: discharge date | ABTC: admission date",
        ).grid(row=0, column=7, sticky="w", padx=(18, 0))
        source.columnconfigure(7, weight=1)

        workbook_box = ttk.LabelFrame(self, text="2. Select Workbook and Sheet", padding=12)
        workbook_box.pack(fill="x", pady=(0, 10))
        workbook_box.columnconfigure(1, weight=1)
        ttk.Label(workbook_box, text="Workbook:").grid(row=0, column=0, sticky="w")
        ttk.Entry(
            workbook_box,
            textvariable=self.workbook_var,
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", padx=8)
        browse_button = ttk.Button(
            workbook_box,
            text="Browse Workbook",
            command=self.browse_workbook,
        )
        browse_button.grid(row=0, column=2, padx=(0, 6))
        self._busy_buttons.append(browse_button)
        ttk.Button(
            workbook_box,
            text="Open Workbook",
            command=self.open_workbook,
        ).grid(row=0, column=3)

        ttk.Label(workbook_box, text="Batch Sheet:").grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        self.sheet_combo = ttk.Combobox(
            workbook_box,
            textvariable=self.sheet_var,
            values=(),
            state="readonly",
            width=22,
        )
        self.sheet_combo.grid(row=1, column=1, sticky="w", padx=8, pady=(10, 0))
        self.sheet_combo.bind("<<ComboboxSelected>>", self._sheet_changed)
        ttk.Label(workbook_box, textvariable=self.count_var).grid(
            row=1, column=2, columnspan=2, sticky="w", pady=(10, 0)
        )

        preview = ttk.LabelFrame(self, text="3. Selected Sheet Preview", padding=10)
        preview.pack(fill="both", expand=True, pady=(0, 10))
        columns = ("patient", "hpercode", "admission", "discharge", "folder")
        self.tree = ttk.Treeview(preview, columns=columns, show="headings", height=8)
        for key, label, width in (
            ("patient", "Patient Name", 270),
            ("hpercode", "HPERCODE", 145),
            ("admission", "Admission", 105),
            ("discharge", "Discharge", 105),
            ("folder", "Output Folder Name", 510),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=80, anchor="w")
        y_scroll = ttk.Scrollbar(preview, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(preview, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        preview.rowconfigure(0, weight=1)
        preview.columnconfigure(0, weight=1)

        actions = ttk.Frame(self)
        actions.pack(fill="x")
        create_button = ttk.Button(
            actions,
            text="Create Folders for Selected Sheet",
            command=self.create_selected_folders,
        )
        create_button.pack(side="left")
        self._busy_buttons.append(create_button)
        ttk.Button(
            actions,
            text="Open Output Folder",
            command=self.open_output_folder,
        ).pack(side="left", padx=8)
        ttk.Label(actions, textvariable=self.status_var).pack(side="right")

    def _settings(self) -> dict:
        return dict(self.settings_getter() or {})

    def _reports_folder(self) -> Path:
        base = self._settings().get("base_dir") or Path(__file__).resolve().parent.parent
        return Path(base) / "reports"

    def _load_available_years(self) -> None:
        current_year = datetime.now().year

        def success(years: tuple[int, ...]) -> None:
            values = tuple(str(year) for year in years) or (str(current_year),)
            self.year_combo.configure(values=values)
            if str(current_year) in values:
                self.year_var.set(str(current_year))
            else:
                self.year_var.set(values[0])
            self.status_var.set("Ready")

        self._run_background(
            self.repository.available_years,
            success,
            action="Loading available years",
            show_error=False,
        )

    def extract_to_excel(self) -> None:
        try:
            year = int(self.year_var.get())
            batch_size = int(self.batch_size_var.get())
            claim_type = self.claim_type_var.get().strip().upper()
            if claim_type not in {"REGULAR", "ABTC"}:
                raise ValueError
        except ValueError:
            messagebox.showwarning(
                "Missing Date Fill Batches",
                "Select a valid year and Patients per sheet value.",
            )
            return
        reports = self._reports_folder()
        reports.mkdir(parents=True, exist_ok=True)
        filename = (
            f"missing_date_fill_{claim_type.lower()}_{year}_"
            f"{datetime.now():%Y%m%d_%H%M%S}.xlsx"
        )
        destination = filedialog.asksaveasfilename(
            parent=self,
            title="Save Missing Date Fill Workbook",
            initialdir=reports,
            initialfile=filename,
            defaultextension=".xlsx",
            filetypes=(("Excel Workbook", "*.xlsx"),),
        )
        if not destination:
            return

        def task():
            extraction = self.repository.fetch(year, claim_type)
            summary = self.workbooks.export(
                extraction,
                year=year,
                destination=destination,
                batch_size=batch_size,
                claim_type=claim_type,
            )
            sheets = self.workbooks.list_batch_sheets(summary.path)
            return summary, sheets

        def success(payload: tuple[WorkbookExportResult, tuple[str, ...]]) -> None:
            summary, sheets = payload
            self.workbook_var.set(str(summary.path))
            self._set_sheets(sheets)
            self.status_var.set(
                f"Exported {summary.patient_count} patients in {summary.batch_count} sheets"
            )
            self._log(
                f"[MISSING DATE FILL] Exported {summary.patient_count} patient(s) "
                f"to {summary.path}"
            )
            message = (
                f"Workbook created successfully.\n\n"
                f"Patients: {summary.patient_count}\n"
                f"Claim Type: {claim_type}\n"
                f"Date Basis: "
                f"{'Admission' if claim_type == 'ABTC' else 'Discharge'}\n"
                f"Patients per sheet: {batch_size}\n"
                f"Batch sheets: {summary.batch_count}\n"
                f"Needs Review: {summary.invalid_count}\n\n"
                f"{summary.path}"
            )
            messagebox.showinfo("Export Complete", message, parent=self)

        self._run_background(task, success, action="Extracting HBSys patients")

    def browse_workbook(self) -> None:
        current = Path(self.workbook_var.get()) if self.workbook_var.get() else None
        initial = current.parent if current and current.parent.exists() else self._reports_folder()
        selected = filedialog.askopenfilename(
            parent=self,
            title="Open EDH Folder-Batch Workbook",
            initialdir=initial,
            filetypes=(("Excel Workbook", "*.xlsx"),),
        )
        if not selected:
            return

        def task():
            profile = self.workbooks.workbook_profile(selected)
            sheets = self.workbooks.list_batch_sheets(selected)
            return profile, sheets

        def success(
            payload: tuple[tuple[str, str], tuple[str, ...]]
        ) -> None:
            (claim_type, date_basis), sheets = payload
            if not sheets:
                raise ValueError("The workbook has no Batch sheets")
            self.claim_type_var.set(claim_type.title())
            self.workbook_var.set(selected)
            self._set_sheets(sheets)
            self.status_var.set(
                f"Loaded {len(sheets)} {claim_type} batch sheets "
                f"({date_basis.title()} date basis)"
            )

        self._run_background(task, success, action="Validating workbook")

    def _set_sheets(self, sheets: tuple[str, ...]) -> None:
        self.sheet_combo.configure(values=sheets)
        if not sheets:
            self.sheet_var.set("")
            self._populate_preview(())
            return
        self.sheet_var.set(sheets[0])
        self._load_selected_sheet()

    def _sheet_changed(self, _event=None) -> None:
        self._load_selected_sheet()

    def _load_selected_sheet(self) -> None:
        path = self.workbook_var.get().strip()
        sheet = self.sheet_var.get().strip()
        if not path or not sheet:
            self._populate_preview(())
            return

        def task():
            return self.workbooks.read_batch_sheet(path, sheet)

        def success(records: tuple[ClaimFolderRecord, ...]) -> None:
            self._populate_preview(records)
            self.status_var.set(f"{sheet}: {len(records)} patient(s)")

        self._run_background(task, success, action=f"Loading {sheet}")

    def _populate_preview(self, records: tuple[ClaimFolderRecord, ...]) -> None:
        self.current_records = records
        for item in self.tree.get_children():
            self.tree.delete(item)
        for record in records:
            self.tree.insert(
                "",
                "end",
                values=(
                    record.patient_name,
                    record.hpercode,
                    record.admission_date.isoformat(),
                    record.discharge_date.isoformat(),
                    record.folder_name,
                ),
            )
        self.count_var.set(
            f"{len(records)} patient(s) in selected sheet"
            if records
            else "No valid Batch sheet selected"
        )

    def create_selected_folders(self) -> None:
        workbook = self.workbook_var.get().strip()
        sheet = self.sheet_var.get().strip()
        if not workbook or not sheet or not self.current_records:
            messagebox.showwarning(
                "Create Patient Folders",
                "Load a valid workbook and Batch sheet first.",
                parent=self,
            )
            return
        settings = self._settings()
        output_folder = settings.get("output_folder", "").strip()
        if not output_folder:
            messagebox.showerror(
                "Create Patient Folders",
                "The configured Output Folder is empty.",
                parent=self,
            )
            return
        names = "\n".join(f"• {record.folder_name}" for record in self.current_records)
        if not messagebox.askyesno(
            "Create Patient Folders",
            f"Create these {len(self.current_records)} folder(s) in:\n"
            f"{output_folder}\n\n{names}\n\n"
            "Existing folders will be skipped and will not be changed.",
            parent=self,
        ):
            return

        def task():
            return self.workbooks.create_folders(
                workbook,
                sheet,
                output_folder=output_folder,
                reports_folder=self._reports_folder(),
            )

        def success(summary) -> None:
            self.status_var.set(
                f"Created {summary.created}; existing {summary.existing}; failed {summary.failed}"
            )
            self._log(
                f"[MISSING DATE FILL] {sheet}: created={summary.created}, "
                f"existing={summary.existing}, failed={summary.failed}"
            )
            messagebox.showinfo(
                "Folder Creation Complete",
                f"Created: {summary.created}\n"
                f"Already Existing: {summary.existing}\n"
                f"Failed: {summary.failed}\n\n"
                f"Audit CSV:\n{summary.audit_path}",
                parent=self,
            )

        self._run_background(task, success, action=f"Creating folders for {sheet}")

    def open_workbook(self) -> None:
        path = Path(self.workbook_var.get())
        if not path.is_file():
            messagebox.showinfo("Open Workbook", "No workbook is currently loaded.")
            return
        os.startfile(path)

    def open_output_folder(self) -> None:
        output = Path(self._settings().get("output_folder", ""))
        if not output.is_dir():
            messagebox.showerror("Output Folder", f"Folder not found:\n{output}")
            return
        os.startfile(output)

    def _run_background(
        self,
        task: Callable,
        on_success: Callable,
        *,
        action: str,
        show_error: bool = True,
    ) -> None:
        self._set_busy(True, action)

        def worker() -> None:
            try:
                result = task()
            except Exception as exc:
                self.after(0, lambda error=exc: self._background_failed(error, show_error))
                return
            self.after(0, lambda: self._background_succeeded(result, on_success))

        threading.Thread(target=worker, daemon=True).start()

    def _background_succeeded(self, result, callback: Callable) -> None:
        self._set_busy(False, "Ready")
        try:
            callback(result)
        except Exception as exc:
            self._background_failed(exc, True)

    def _background_failed(self, error: Exception, show_error: bool) -> None:
        self._set_busy(False, "Error")
        self._log(f"[MISSING DATE FILL ERROR] {error}")
        if show_error:
            messagebox.showerror("Missing Date Fill Batches", str(error), parent=self)

    def _set_busy(self, busy: bool, status: str) -> None:
        state = "disabled" if busy else "normal"
        for button in self._busy_buttons:
            button.configure(state=state)
        self.status_var.set(status)

    def _log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Missing Date Fill Batches")
    root.geometry("1200x700")
    base = Path(__file__).resolve().parent.parent
    frame = NotTransmittedBatchesFrame(
        root,
        settings_getter=lambda: {
            "base_dir": str(base),
            "output_folder": str(base / "output"),
        },
        log_callback=print,
    )
    frame.pack(fill="both", expand=True)
    root.mainloop()
