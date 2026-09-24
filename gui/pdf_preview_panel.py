"""Tkinter GUI PDF Preview Panel.

Builds 3-column contact sheets for CSF and COE PDFs inside the main GUI,
then previews them with Previous / Next navigation -- no external viewer needed.
"""

from __future__ import annotations

import threading
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox

from PIL import Image, ImageTk

from core.activity_logger import logger
from core.pdf_preview_service import (
    PdfPreviewService,
    pdf_preview_service,
)


class PdfPreviewPanel:
    """Embedded preview panel with prev/next buttons and per-sheet navigation."""

    def __init__(
        self,
        parent: tk.Frame,
        service: PdfPreviewService = pdf_preview_service,
    ) -> None:
        self.parent = parent
        self.service = service
        self._sheets: list[Path] = []
        self._sheet_index = 0
        self._tk_image: ImageTk.PhotoImage | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self.parent)
        toolbar.pack(fill="x", pady=(0, 6))

        self.doc_type_var = tk.StringVar(value="CSF")
        ttk.Label(toolbar, text="Document:").pack(side="left")
        for doc in ("CSF", "COE"):
            ttk.Radiobutton(
                toolbar,
                text=doc,
                variable=self.doc_type_var,
                value=doc,
                command=self._refresh,
            ).pack(side="left", padx=(4, 12))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(
            toolbar,
            textvariable=self.status_var,
            foreground="#555555",
        ).pack(side="left", padx=8)

        self.sheet_label_var = tk.StringVar(value="Sheet 0 of 0")
        ttk.Label(
            toolbar,
            textvariable=self.sheet_label_var,
            foreground="#555555",
        ).pack(side="right", padx=8)

        nav = ttk.Frame(self.parent)
        nav.pack(fill="x", pady=(0, 6))
        self.prev_btn = ttk.Button(
            nav, text="Previous", command=self._previous_sheet
        )
        self.prev_btn.pack(side="left", padx=(0, 4))
        self.next_btn = ttk.Button(
            nav, text="Next", command=self._next_sheet
        )
        self.next_btn.pack(side="left")

        refresh_btn = ttk.Button(
            nav, text="Regenerate Sheets", command=self._refresh
        )
        refresh_btn.pack(side="right", padx=(8, 0))

        self.canvas = tk.Canvas(
            self.parent, bg="#111111", highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)
        self._canvas_image_id: int | None = None

    def refresh(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        doc_type = self.doc_type_var.get()
        self.status_var.set(f"Generating {doc_type} sheets...")
        self.parent.update_idletasks()

        def _work():
            try:
                sheets = self.service.build_sheets(doc_type=doc_type)
                self.parent.after(
                    0, lambda: self._on_sheets_ready(doc_type, sheets)
                )
            except Exception as exc:
                logger.error(f"PDF preview refresh failed: {exc}")
                self.parent.after(
                    0,
                    lambda: self._on_error(
                        f"Failed to build {doc_type} sheets:\n{exc}"
                    ),
                )

        threading.Thread(target=_work, daemon=True).start()

    def _on_sheets_ready(self, doc_type: str, sheets: list[Path]) -> None:
        self._sheets = sheets
        self._sheet_index = 0
        self._render_current()
        self.status_var.set(
            f"{doc_type}: {len(sheets)} sheet(s) for "
            f"{len(self.service.list_patients(doc_type))} patient(s)"
        )

    def _on_error(self, message: str) -> None:
        self._sheets = []
        self.status_var.set("Error")
        messagebox.showerror("PDF Preview", message, parent=self.parent)

    def _render_current(self) -> None:
        if not self._sheets:
            self._clear_canvas()
            self.sheet_label_var.set("Sheet 0 of 0")
            self._set_nav_state()
            return

        sheet_path = self._sheets[self._sheet_index]
        try:
            img = Image.open(sheet_path)
        except OSError as exc:
            self._on_error(f"Cannot open sheet:\n{sheet_path}\n\n{exc}")
            return

        self.parent.update_idletasks()
        cw = max(300, self.canvas.winfo_width() - 20)
        ch = max(200, self.canvas.winfo_height() - 20)
        img.thumbnail((cw, ch), Image.LANCZOS)
        self._tk_image = ImageTk.PhotoImage(img)

        if self._canvas_image_id is not None:
            self.canvas.itemconfig(
                self._canvas_image_id, image=self._tk_image
            )
        else:
            self._canvas_image_id = self.canvas.create_image(
                cw // 2, ch // 2, anchor="center", image=self._tk_image
            )

        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.sheet_label_var.set(
            f"Sheet {self._sheet_index + 1} of {len(self._sheets)}"
        )
        self._set_nav_state()

    def _clear_canvas(self) -> None:
        if self._canvas_image_id is not None:
            self.canvas.delete(self._canvas_image_id)
            self._canvas_image_id = None
        self._tk_image = None

    def _set_nav_state(self) -> None:
        self.prev_btn.configure(
            state="normal" if self._sheet_index > 0 else "disabled"
        )
        self.next_btn.configure(
            state="normal"
            if self._sheet_index < len(self._sheets) - 1
            else "disabled"
        )

    def _previous_sheet(self) -> None:
        if self._sheet_index > 0:
            self._sheet_index -= 1
            self._render_current()

    def _next_sheet(self) -> None:
        if self._sheet_index < len(self._sheets) - 1:
            self._sheet_index += 1
            self._render_current()


def main() -> None:
    root = tk.Tk()
    root.title("EDH Claims - PDF Preview")
    root.geometry("1100x700")
    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)
    PdfPreviewPanel(frame)
    root.mainloop()


if __name__ == "__main__":
    main()