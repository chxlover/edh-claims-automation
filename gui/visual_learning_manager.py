"""Tkinter management window for visual document-learning metadata."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from core.visual_document_learner import VisualDocumentLearner


class VisualLearningManager(tk.Toplevel):
    def __init__(self, master, learner: VisualDocumentLearner) -> None:
        super().__init__(master)
        self.learner = learner
        self.title("EDH Visual Learning Manager")
        self.geometry("1120x700")
        self.minsize(900, 580)
        self.auto_var = tk.BooleanVar(value=self.learner.auto_enabled())
        self.status_var = tk.StringVar(value="Ready")
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.pack(fill="both", expand=True)

        top = ttk.Frame(main)
        top.pack(fill="x")
        ttk.Label(
            top, text="VISUAL DOCUMENT LEARNING", font=("Segoe UI", 16, "bold")
        ).pack(side="left")
        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="right")

        ttk.Checkbutton(
            main,
            text="Enable qualified automatic classification",
            variable=self.auto_var,
            command=self.toggle_auto,
        ).pack(anchor="w", pady=(10, 2))
        ttk.Label(
            main,
            text=(
                "OFF means shadow/suggestion mode. Enabling this does not bypass sample, "
                "confidence, margin, consensus, or negative-feedback safety rules."
            ),
            foreground="#5B677A",
        ).pack(anchor="w", pady=(0, 10))

        notebook = ttk.Notebook(main)
        notebook.pack(fill="both", expand=True)
        class_tab = ttk.Frame(notebook, padding=8)
        sample_tab = ttk.Frame(notebook, padding=8)
        confusion_tab = ttk.Frame(notebook, padding=8)
        notebook.add(class_tab, text="Class Readiness")
        notebook.add(sample_tab, text="Training Samples")
        notebook.add(confusion_tab, text="Confusion Audit")

        class_columns = (
            "type", "active", "disabled", "negative", "reviewed", "accuracy",
            "ready", "updated",
        )
        self.class_tree = ttk.Treeview(
            class_tab, columns=class_columns, show="headings", selectmode="browse"
        )
        class_headings = {
            "type": "Document Type", "active": "Active", "disabled": "Disabled",
            "negative": "Corrections", "reviewed": "Reviewed Predictions",
            "accuracy": "Accuracy", "ready": "Sample Ready", "updated": "Last Updated",
        }
        for column in class_columns:
            self.class_tree.heading(column, text=class_headings[column])
            self.class_tree.column(column, width=130, anchor="center")
        self.class_tree.column("type", width=155, anchor="w")
        self.class_tree.column("updated", width=175, anchor="w")
        class_scroll = ttk.Scrollbar(class_tab, command=self.class_tree.yview)
        self.class_tree.configure(yscrollcommand=class_scroll.set)
        self.class_tree.pack(side="left", fill="both", expand=True)
        class_scroll.pack(side="right", fill="y")

        sample_columns = (
            "id", "type", "source", "hash", "quality", "orientation", "enabled", "confirmed",
        )
        self.sample_tree = ttk.Treeview(
            sample_tab, columns=sample_columns, show="headings", selectmode="browse"
        )
        sample_headings = {
            "id": "ID", "type": "Document Type", "source": "Source Name",
            "hash": "File Hash", "quality": "Quality", "orientation": "Orientation",
            "enabled": "Enabled", "confirmed": "Confirmed At",
        }
        widths = {
            "id": 60, "type": 135, "source": 185, "hash": 150, "quality": 80,
            "orientation": 100, "enabled": 80, "confirmed": 165,
        }
        for column in sample_columns:
            self.sample_tree.heading(column, text=sample_headings[column])
            self.sample_tree.column(column, width=widths[column], anchor="center")
        self.sample_tree.column("source", anchor="w")
        sample_scroll = ttk.Scrollbar(sample_tab, command=self.sample_tree.yview)
        self.sample_tree.configure(yscrollcommand=sample_scroll.set)
        self.sample_tree.pack(side="left", fill="both", expand=True)
        sample_scroll.pack(side="right", fill="y")

        confusion_columns = ("predicted", "final", "count", "result")
        self.confusion_tree = ttk.Treeview(
            confusion_tab,
            columns=confusion_columns,
            show="headings",
            selectmode="browse",
        )
        confusion_headings = {
            "predicted": "Visual Prediction",
            "final": "Manually Confirmed Type",
            "count": "Reviews",
            "result": "Result",
        }
        for column in confusion_columns:
            self.confusion_tree.heading(column, text=confusion_headings[column])
            self.confusion_tree.column(column, width=210, anchor="center")
        self.confusion_tree.pack(fill="both", expand=True)

        actions = ttk.Frame(main)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(
            actions, text="Disable Selected Sample", command=lambda: self.set_selected(False)
        ).pack(side="left")
        ttk.Button(
            actions, text="Enable Selected Sample", command=lambda: self.set_selected(True)
        ).pack(side="left", padx=(8, 0))
        ttk.Label(actions, textvariable=self.status_var).pack(side="right")

    def toggle_auto(self) -> None:
        try:
            self.learner.set_auto_enabled(self.auto_var.get())
            self.status_var.set(
                "Automatic classification enabled"
                if self.auto_var.get() else "Shadow/suggestion mode enabled"
            )
        except Exception as exc:
            self.auto_var.set(self.learner.auto_enabled())
            messagebox.showerror("Visual Learning", str(exc), parent=self)

    def refresh(self) -> None:
        for item in self.class_tree.get_children():
            self.class_tree.delete(item)
        for row in self.learner.class_statistics():
            accuracy = "—" if row["accuracy"] is None else f"{row['accuracy']:.1%}"
            self.class_tree.insert(
                "", "end", values=(
                    row["doc_type"], row["active_samples"], row["disabled_samples"],
                    row["negative_count"], row["reviewed_predictions"], accuracy,
                    "YES" if row["auto_ready"] else "NO", row["last_updated"],
                )
            )

        for item in self.sample_tree.get_children():
            self.sample_tree.delete(item)
        samples = self.learner.list_samples()
        for row in samples:
            self.sample_tree.insert(
                "", "end", iid=str(row["id"]), values=(
                    row["id"], row["doc_type"], row["source_name"],
                    str(row["file_hash"])[:16] + "…", f"{float(row['quality']):.0%}",
                    "UNCERTAIN" if row["orientation_uncertain"] else "OK",
                    "YES" if row["enabled"] else "NO", row["confirmed_at"],
                )
            )
        for item in self.confusion_tree.get_children():
            self.confusion_tree.delete(item)
        for row in self.learner.confusion_statistics():
            matched = row["predicted_type"] == row["final_type"]
            self.confusion_tree.insert(
                "",
                "end",
                values=(
                    row["predicted_type"],
                    row["final_type"],
                    row["decision_count"],
                    "CORRECT" if matched else "CORRECTED",
                ),
            )
        self.auto_var.set(self.learner.auto_enabled())
        self.status_var.set(f"{len(samples)} stored feature sample(s)")

    def set_selected(self, enabled: bool) -> None:
        selection = self.sample_tree.selection()
        if not selection:
            messagebox.showinfo(
                "Visual Learning", "Select a training sample first.", parent=self
            )
            return
        sample_id = int(selection[0])
        if not enabled and not messagebox.askyesno(
            "Disable Sample",
            "Disable this visual sample? The original patient document will not be changed.",
            parent=self,
        ):
            return
        try:
            if not self.learner.set_sample_enabled(sample_id, enabled):
                raise ValueError("Training sample was not found")
            self.refresh()
        except Exception as exc:
            messagebox.showerror("Visual Learning", str(exc), parent=self)


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    VisualLearningManager(root, VisualDocumentLearner())
    root.mainloop()
