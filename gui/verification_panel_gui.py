"""Tkinter GUI Verification Panel.

This window is read-only. It displays production status using service modules.
"""

from __future__ import annotations

import os
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from core.activity_logger import logger
from core.verification_panel_service import (
    VerificationPanelService,
    verification_panel_service,
)


class VerificationPanelWindow:
    """Read-only production verification dashboard."""

    def __init__(
        self,
        root: tk.Tk,
        service: VerificationPanelService = verification_panel_service,
    ) -> None:
        self.root = root
        self.service = service
        self.scan_folder = Path(os.getenv("CLAIMS_SCAN_FOLDER", "C:/claims_bot/scans"))
        self.output_folder = Path(os.getenv("CLAIMS_OUTPUT_FOLDER", "C:/claims_bot/output"))
        self.backup_folder = Path(
            os.getenv("CLAIMS_BACKUP_FOLDER", "C:/claims_bot/backup_originals")
        )
        self.summary_vars = {
            key: tk.StringVar(value="0")
            for key in (
                "scan_pdfs",
                "output_folders",
                "output_pdfs",
                "backup_folders",
                "pending",
                "in_review",
                "resolved",
                "completed",
            )
        }
        self.last_refresh_var = tk.StringVar(value="Not refreshed yet")
        self._configure_window()
        self._build_ui()
        self.refresh()

    def _configure_window(self) -> None:
        self.root.title("EDH Claims — GUI Verification Panel")
        self.root.geometry("1120x700")
        self.root.minsize(980, 620)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(
            header,
            text="GUI VERIFICATION PANEL",
            font=("Segoe UI", 16, "bold"),
        ).pack(side="left")
        ttk.Label(header, textvariable=self.last_refresh_var).pack(side="left", padx=18)
        ttk.Button(header, text="Refresh", command=self.refresh).pack(side="right")

        cards = ttk.Frame(outer)
        cards.pack(fill="x", pady=(0, 12))
        for label, key in (
            ("Scan PDFs", "scan_pdfs"),
            ("Output Folders", "output_folders"),
            ("Output PDFs", "output_pdfs"),
            ("Backup Folders", "backup_folders"),
            ("Pending", "pending"),
            ("In Review", "in_review"),
            ("Ready to Resume", "resolved"),
            ("Completed", "completed"),
        ):
            card = ttk.LabelFrame(cards, text=label, padding=10)
            card.pack(side="left", fill="x", expand=True, padx=4)
            ttk.Label(
                card,
                textvariable=self.summary_vars[key],
                font=("Segoe UI", 18, "bold"),
            ).pack()

        paned = ttk.Panedwindow(outer, orient="horizontal")
        paned.pack(fill="both", expand=True)
        left = ttk.Frame(paned)
        right = ttk.Frame(paned, padding=(12, 0, 0, 0))
        paned.add(left, weight=3)
        paned.add(right, weight=2)

        self._build_review_tables(left)
        self._build_output_table(right)
        self._build_actions(outer)

    def _build_review_tables(self, parent: ttk.Frame) -> None:
        active_frame = ttk.LabelFrame(parent, text="Active Review Queue", padding=8)
        active_frame.pack(fill="both", expand=True, pady=(0, 8))
        self.active_table = self._table(
            active_frame,
            columns=("id", "status", "reason", "hospital_no", "patient_name", "files"),
            headings={
                "id": "ID",
                "status": "Status",
                "reason": "Reason",
                "hospital_no": "Hospital No.",
                "patient_name": "Patient",
                "files": "Files",
            },
            widths={
                "id": 55,
                "status": 95,
                "reason": 70,
                "hospital_no": 130,
                "patient_name": 230,
                "files": 55,
            },
        )

        ready_frame = ttk.LabelFrame(parent, text="Ready to Resume", padding=8)
        ready_frame.pack(fill="both", expand=True)
        self.ready_table = self._table(
            ready_frame,
            columns=("id", "reason", "hospital_no", "patient_name", "admission"),
            headings={
                "id": "ID",
                "reason": "Reason",
                "hospital_no": "Hospital No.",
                "patient_name": "Patient",
                "admission": "Admission",
            },
            widths={
                "id": 55,
                "reason": 70,
                "hospital_no": 130,
                "patient_name": 250,
                "admission": 120,
            },
        )

    def _build_output_table(self, parent: ttk.Frame) -> None:
        output_frame = ttk.LabelFrame(parent, text="Recent Output Folders", padding=8)
        output_frame.pack(fill="both", expand=True)
        self.output_table = self._table(
            output_frame,
            columns=("name", "pdfs", "modified"),
            headings={"name": "Folder", "pdfs": "PDFs", "modified": "Modified"},
            widths={"name": 260, "pdfs": 55, "modified": 165},
        )

    @staticmethod
    def _table(
        parent: ttk.Frame,
        *,
        columns: tuple[str, ...],
        headings: dict[str, str],
        widths: dict[str, int],
    ) -> ttk.Treeview:
        table = ttk.Treeview(parent, columns=columns, show="headings", height=8)
        for column in columns:
            table.heading(column, text=headings[column])
            table.column(column, width=widths[column], anchor="w")
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scrollbar.set)
        table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return table

    def _build_actions(self, parent: ttk.Frame) -> None:
        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Open Scans", command=lambda: self._open(self.scan_folder)).pack(
            side="left"
        )
        ttk.Button(
            actions,
            text="Open Output",
            command=lambda: self._open(self.output_folder),
        ).pack(side="left", padx=6)
        ttk.Button(
            actions,
            text="Open Backup",
            command=lambda: self._open(self.backup_folder),
        ).pack(side="left")
        ttk.Label(
            actions,
            text="Read-only panel: use Patient Review Queue to fix or resume patients.",
            foreground="#555555",
        ).pack(side="right")

    def refresh(self) -> None:
        try:
            snapshot = self.service.snapshot(
                scan_folder=self.scan_folder,
                output_folder=self.output_folder,
                backup_folder=self.backup_folder,
            )
        except Exception as exc:
            logger.error(f"Verification Panel refresh failed: {exc}")
            messagebox.showerror("Verification Panel", str(exc), parent=self.root)
            return

        self.summary_vars["scan_pdfs"].set(str(snapshot.scan.pdf_count))
        self.summary_vars["output_folders"].set(str(snapshot.output.folder_count))
        self.summary_vars["output_pdfs"].set(str(snapshot.output.pdf_count))
        self.summary_vars["backup_folders"].set(str(snapshot.backup.folder_count))
        self.summary_vars["pending"].set(str(snapshot.review_counts.get("PENDING", 0)))
        self.summary_vars["in_review"].set(str(snapshot.review_counts.get("IN_REVIEW", 0)))
        self.summary_vars["resolved"].set(str(snapshot.review_counts.get("RESOLVED", 0)))
        self.summary_vars["completed"].set(str(snapshot.review_counts.get("COMPLETED", 0)))
        self.last_refresh_var.set(
            f"Last refresh: {datetime.now().strftime('%I:%M:%S %p')}"
        )

        self._replace_rows(
            self.active_table,
            (
                (
                    item.id,
                    item.status,
                    item.reason,
                    item.hospital_no,
                    item.patient_name,
                    len(item.documents),
                )
                for item in snapshot.active_reviews
            ),
        )
        self._replace_rows(
            self.ready_table,
            (
                (
                    item.id,
                    item.reason,
                    item.hospital_no,
                    item.patient_name,
                    item.admission_date,
                )
                for item in snapshot.ready_to_resume
            ),
        )
        self._replace_rows(
            self.output_table,
            (
                (item.name, item.pdf_count, item.modified)
                for item in snapshot.recent_outputs
            ),
        )

    @staticmethod
    def _replace_rows(table: ttk.Treeview, rows) -> None:
        table.delete(*table.get_children())
        for row in rows:
            table.insert("", "end", values=row)

    @staticmethod
    def _open(path: Path) -> None:
        if not path.is_dir():
            messagebox.showerror("Verification Panel", f"Folder unavailable:\n\n{path}")
            return
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("Verification Panel", str(exc))


def main() -> None:
    root = tk.Tk()
    VerificationPanelWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
