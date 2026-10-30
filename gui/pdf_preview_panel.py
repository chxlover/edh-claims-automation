"""Tkinter GUI PDF Preview Panel.

Builds 3-column contact sheets for CSF and COE PDFs inside the main GUI,
then previews them with Previous / Next navigation -- no external viewer needed.

Zoom (+/-) is persistent: once set, it carries over to the next sheet
(and across refreshes), so the operator does not re-zoom every page.
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

#: Zoom step per click (x1.25 / /1.25) and hard limits, as fractions of
#: the fit-to-canvas size. 100% == fit, exactly like before zoom existed.
ZOOM_STEP = 1.25
ZOOM_MIN = 0.5
ZOOM_MAX = 3.0


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
        self._page_index = 0
        self._max_pages = 1
        self._tk_image: ImageTk.PhotoImage | None = None
        # Persistent zoom (fraction of fit size). Never reset on sheet
        # change or refresh — operator request: zoom carries over to the
        # next page.
        self._zoom = 1.0
        self._build_ui()

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self.parent)
        toolbar.pack(fill="x", pady=(0, 6))

        self.doc_type_var = tk.StringVar(value="CSF")
        ttk.Label(toolbar, text="Document:").pack(side="left")
        # Radio buttons are built dynamically (see _rebuild_doc_type_buttons)
        # so only document types that actually have PDFs are shown.
        self._doc_type_frame = ttk.Frame(toolbar)
        self._doc_type_frame.pack(side="left")
        self._doc_type_cache: list[str] = []
        self._rebuild_doc_type_buttons()

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

        self.prev_page_btn = ttk.Button(
            nav, text="Prev Page", command=self._previous_page
        )
        self.prev_page_btn.pack(side="left", padx=(12, 4))
        self.next_page_btn = ttk.Button(
            nav, text="Next Page", command=self._next_page
        )
        self.next_page_btn.pack(side="left")

        self.page_label_var = tk.StringVar(value="Page 1 of 1")
        ttk.Label(nav, textvariable=self.page_label_var).pack(
            side="left", padx=8
        )

        self.zoom_out_btn = ttk.Button(
            nav, text="Zoom -", command=self.zoom_out
        )
        self.zoom_out_btn.pack(side="left", padx=(12, 4))
        self.zoom_in_btn = ttk.Button(
            nav, text="Zoom +", command=self.zoom_in
        )
        self.zoom_in_btn.pack(side="left")

        self.zoom_label_var = tk.StringVar(value="Zoom: 100%")
        ttk.Label(nav, textvariable=self.zoom_label_var).pack(
            side="left", padx=8
        )

        refresh_btn = ttk.Button(
            nav, text="Regenerate Sheets", command=self._refresh
        )
        refresh_btn.pack(side="right", padx=(8, 0))

        container = ttk.Frame(self.parent)
        container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            container, bg="#111111", highlightthickness=0
        )
        self.v_scroll = ttk.Scrollbar(
            container, orient="vertical", command=self.canvas.yview
        )
        self.h_scroll = ttk.Scrollbar(
            container, orient="horizontal", command=self.canvas.xview
        )
        self.canvas.configure(
            yscrollcommand=self.v_scroll.set,
            xscrollcommand=self.h_scroll.set,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.v_scroll.grid(row=0, column=1, sticky="ns")
        self.h_scroll.grid(row=1, column=0, sticky="ew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        self._canvas_image_id: int | None = None

        self.canvas.bind("<MouseWheel>", self._on_mousewheel)

    def _available_doc_types(self) -> list[str]:
        service = self.service
        if hasattr(service, "available_doc_types"):
            try:
                return list(service.available_doc_types())
            except Exception:  # noqa: BLE001 - fall back below.
                pass
        # Fallback: probe list_patients for each known type.
        try:
            from core.pdf_preview_service import ALL_DOC_TYPES
        except Exception:  # noqa: BLE001
            ALL_DOC_TYPES = ("CSF", "COE")
        result: list[str] = []
        for doc in ALL_DOC_TYPES:
            try:
                if service.list_patients(doc):
                    result.append(doc)
            except Exception:  # noqa: BLE001
                pass
        if not result:
            result = ["CSF", "COE"]
        return result

    def _rebuild_doc_type_buttons(self) -> None:
        available = self._available_doc_types()
        if available == self._doc_type_cache and self._doc_type_frame.winfo_children():
            # Nothing changed; keep the existing buttons.
            if self.doc_type_var.get() not in available and available:
                self.doc_type_var.set(available[0])
            return
        for widget in self._doc_type_frame.winfo_children():
            widget.destroy()
        self._doc_type_cache = list(available)
        if not available:
            ttk.Label(
                self._doc_type_frame, text="No documents found"
            ).pack(side="left", padx=(4, 12))
            return
        if self.doc_type_var.get() not in available:
            self.doc_type_var.set(available[0])
        for doc in available:
            ttk.Radiobutton(
                self._doc_type_frame,
                text=doc,
                variable=self.doc_type_var,
                value=doc,
                command=self._refresh,
            ).pack(side="left", padx=(4, 12))

    def refresh(self) -> None:
        self._refresh()

    def _refresh(self, reset_sheet: bool = True) -> None:
        self._rebuild_doc_type_buttons()
        doc_type = self.doc_type_var.get()
        if not doc_type:
            self.status_var.set("No documents found")
            return
        self.status_var.set(f"Generating {doc_type} sheets...")
        self.parent.update_idletasks()

        def _work():
            try:
                sheets = self.service.build_sheets(
                    doc_type=doc_type, page_index=self._page_index
                )
                max_pages = getattr(self.service, "last_max_pages", 1)
                self.parent.after(
                    0,
                    lambda: self._on_sheets_ready(
                        doc_type, sheets, max_pages, reset_sheet
                    ),
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

    def _on_sheets_ready(
        self, doc_type: str, sheets: list[Path], max_pages: int = 1,
        reset_sheet: bool = True,
    ) -> None:
        self._sheets = sheets
        if reset_sheet or self._sheet_index >= len(sheets):
            self._sheet_index = 0
        if self._sheet_index < 0:
            self._sheet_index = 0
        self._max_pages = max(1, int(max_pages or 1))
        self._render_current()
        self.status_var.set(
            f"{doc_type}: {len(sheets)} sheet(s) for "
            f"{len(self.service.list_patients(doc_type))} patient(s)"
        )
        self._set_page_state()

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
        fit = min(cw / img.width, ch / img.height)
        target_w = max(1, int(img.width * fit * self._zoom))
        target_h = max(1, int(img.height * fit * self._zoom))
        if (target_w, target_h) != img.size:
            img = img.resize((target_w, target_h), Image.LANCZOS)
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
        self._set_page_state()

    def _set_page_state(self) -> None:
        try:
            self.prev_page_btn.configure(
                state="normal" if self._page_index > 0 else "disabled"
            )
            self.next_page_btn.configure(
                state="normal"
                if self._page_index < self._max_pages - 1
                else "disabled"
            )
            self.page_label_var.set(
                f"Page {self._page_index + 1} of {self._max_pages}"
            )
        except Exception:  # noqa: BLE001 - widgets may not exist yet.
            pass

    def _previous_sheet(self) -> None:
        if self._sheet_index > 0:
            self._sheet_index -= 1
            if self._page_index != 0:
                # Moving to a different set of patients: restart at page 1.
                self._page_index = 0
                self._refresh(reset_sheet=False)
            else:
                self._render_current()

    def _next_sheet(self) -> None:
        if self._sheet_index < len(self._sheets) - 1:
            self._sheet_index += 1
            if self._page_index != 0:
                # Moving to a different set of patients: restart at page 1.
                self._page_index = 0
                self._refresh(reset_sheet=False)
            else:
                self._render_current()

    def _previous_page(self) -> None:
        if self._page_index > 0:
            self._page_index -= 1
            self._refresh(reset_sheet=False)

    def _next_page(self) -> None:
        if self._page_index < self._max_pages - 1:
            self._page_index += 1
            self._refresh(reset_sheet=False)

    # -- zoom (persistent across sheets) -----------------------------------

    def zoom_in(self) -> None:
        """Zoom one step in; the level carries over to the next sheet."""
        self._set_zoom(self._zoom * ZOOM_STEP)

    def zoom_out(self) -> None:
        """Zoom one step out; the level carries over to the next sheet."""
        self._set_zoom(self._zoom / ZOOM_STEP)

    def _set_zoom(self, value: float) -> None:
        self._zoom = min(ZOOM_MAX, max(ZOOM_MIN, value))
        self.zoom_label_var.set(f"Zoom: {int(round(self._zoom * 100))}%")
        self._render_current()

    def _on_mousewheel(self, event) -> None:
        self.canvas.yview_scroll(-1 * (event.delta // 120), "units")


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