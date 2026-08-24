"""Tkinter PDF Compressor tab with safe preview and background compression."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import fitz
from PIL import Image, ImageTk

from core.pdf_compressor import (
    COMPRESSION_PRESETS,
    compress_pdf,
    default_compressed_path,
)


def format_size(byte_count: int) -> str:
    size = float(byte_count)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return f"{byte_count} B"


class PDFCompressorFrame(ttk.Frame):
    def __init__(self, parent, log_callback=None) -> None:
        super().__init__(parent, padding=10)
        self.log_callback = log_callback or (lambda _message: None)
        self.source_path: Path | None = None
        self.output_path: Path | None = None
        self.document: fitz.Document | None = None
        self.page_index = 0
        self.preview_photo = None
        self.worker_running = False

        self.source_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.preset_var = tk.StringVar(value="Readable Claims")
        self.target_mb_var = tk.StringVar(value="1.4")
        self.file_info_var = tk.StringVar(value="Open a PDF to preview and compress it.")
        self.page_var = tk.StringVar(value="Page 0 of 0")
        self.status_var = tk.StringVar(value="Ready")
        self.result_var = tk.StringVar(value="No compressed output yet.")
        self._build_ui()

    def destroy(self):
        self._close_document()
        super().destroy()

    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(
            header, text="PDF COMPRESSOR", font=("Segoe UI", 17, "bold")
        ).pack(side="left")
        ttk.Label(
            header,
            text="Creates a separate compressed PDF/A read-only copy. Original stays unchanged.",
        ).pack(side="left", padx=(14, 0))

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)
        controls = ttk.Frame(body, padding=8, width=390)
        preview = ttk.Frame(body, padding=8)
        body.add(controls, weight=0)
        body.add(preview, weight=1)

        source_box = ttk.LabelFrame(controls, text="Source PDF", padding=10)
        source_box.pack(fill="x")
        ttk.Entry(source_box, textvariable=self.source_var, state="readonly").pack(
            fill="x", pady=(0, 8)
        )
        source_actions = ttk.Frame(source_box)
        source_actions.pack(fill="x")
        ttk.Button(
            source_actions, text="Open PDF", command=self.choose_source
        ).pack(side="left", fill="x", expand=True)
        self.open_source_button = ttk.Button(
            source_actions, text="Open in Viewer", command=self.open_source_external,
            state="disabled",
        )
        self.open_source_button.pack(side="left", fill="x", expand=True, padx=(6, 0))
        ttk.Label(
            source_box, textvariable=self.file_info_var, wraplength=340
        ).pack(anchor="w", pady=(10, 0))

        settings_box = ttk.LabelFrame(controls, text="Compression Settings", padding=10)
        settings_box.pack(fill="x", pady=(10, 0))
        ttk.Label(settings_box, text="Compression level").pack(anchor="w")
        preset = ttk.Combobox(
            settings_box,
            textvariable=self.preset_var,
            values=tuple(COMPRESSION_PRESETS),
            state="readonly",
        )
        preset.pack(fill="x", pady=(4, 4))
        target_row = ttk.Frame(settings_box)
        target_row.pack(fill="x", pady=(6, 4))
        ttk.Label(target_row, text="Maximum file size (MB)").pack(side="left")
        ttk.Spinbox(
            target_row,
            from_=0.1,
            to=100.0,
            increment=0.1,
            textvariable=self.target_mb_var,
            width=8,
        ).pack(side="right")
        ttk.Label(
            settings_box,
            text=(
                "Readable Claims: recommended; grayscale, minimum 200 DPI\n"
                "High Quality: clearer images, larger file\n"
                "Balanced: smaller color copy; moderate resolution\n"
                "Strong: smallest copy, lower image resolution\n"
                "Readable Claims will not lower quality past its safe minimum."
            ),
        ).pack(anchor="w")

        output_box = ttk.LabelFrame(controls, text="Compressed Output", padding=10)
        output_box.pack(fill="x", pady=(10, 0))
        ttk.Entry(output_box, textvariable=self.output_var, state="readonly").pack(
            fill="x", pady=(0, 8)
        )
        ttk.Button(
            output_box, text="Choose Save Location", command=self.choose_output
        ).pack(fill="x")
        self.compress_button = ttk.Button(
            output_box,
            text="Compress and Save Copy",
            command=self.start_compression,
            state="disabled",
        )
        self.compress_button.pack(fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(output_box, mode="indeterminate")
        self.progress.pack(fill="x", pady=(8, 0))
        ttk.Label(output_box, textvariable=self.status_var).pack(anchor="w", pady=(6, 0))

        result_box = ttk.LabelFrame(controls, text="Result", padding=10)
        result_box.pack(fill="both", expand=True, pady=(10, 0))
        ttk.Label(result_box, textvariable=self.result_var, wraplength=340).pack(anchor="w")
        self.open_result_button = ttk.Button(
            result_box,
            text="Open Compressed PDF",
            command=self.open_result_external,
            state="disabled",
        )
        self.open_result_button.pack(fill="x", side="bottom")

        preview_box = ttk.LabelFrame(preview, text="PDF Preview", padding=8)
        preview_box.pack(fill="both", expand=True)
        navigation = ttk.Frame(preview_box)
        navigation.pack(fill="x", pady=(0, 6))
        self.previous_button = ttk.Button(
            navigation, text="Previous", command=lambda: self.change_page(-1), state="disabled"
        )
        self.previous_button.pack(side="left")
        ttk.Label(navigation, textvariable=self.page_var).pack(side="left", padx=12)
        self.next_button = ttk.Button(
            navigation, text="Next", command=lambda: self.change_page(1), state="disabled"
        )
        self.next_button.pack(side="left")
        self.canvas = tk.Canvas(
            preview_box,
            background="#3B4252",
            highlightthickness=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.render_page())

    def choose_source(self) -> None:
        selected = filedialog.askopenfilename(
            title="Open PDF to Compress",
            filetypes=[("PDF files", "*.pdf")],
        )
        if selected:
            self.load_source(selected)

    def load_source(self, selected: str | Path) -> None:
        path = Path(selected).resolve()
        try:
            document = fitz.open(path)
            if document.page_count < 1:
                document.close()
                raise ValueError("The selected PDF has no pages")
        except Exception as exc:
            messagebox.showerror("PDF Compressor", f"Unable to open PDF:\n\n{exc}")
            return
        self._close_document()
        self.document = document
        self.source_path = path
        self.output_path = default_compressed_path(path)
        self.source_var.set(str(path))
        self.output_var.set(str(self.output_path))
        self.page_index = 0
        self.file_info_var.set(
            f"{document.page_count} page(s) • Original size: {format_size(path.stat().st_size)}"
        )
        self.result_var.set("No compressed output yet.")
        self.status_var.set("Ready to compress")
        self.open_source_button.configure(state="normal")
        self.open_result_button.configure(state="disabled")
        self.compress_button.configure(state="normal")
        self.render_page()

    def _close_document(self) -> None:
        if self.document is not None:
            try:
                self.document.close()
            except Exception:
                pass
        self.document = None

    def choose_output(self) -> None:
        if not self.source_path:
            messagebox.showinfo("PDF Compressor", "Open a PDF first.")
            return
        selected = filedialog.asksaveasfilename(
            title="Save Compressed PDF/A Copy",
            initialdir=str(self.source_path.parent),
            initialfile=default_compressed_path(self.source_path).name,
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
        )
        if selected:
            self.output_path = Path(selected).resolve()
            self.output_var.set(str(self.output_path))

    def render_page(self) -> None:
        if self.document is None or not self.winfo_exists():
            return
        try:
            page = self.document.load_page(self.page_index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            available_width = max(240, self.canvas.winfo_width() - 20)
            available_height = max(300, self.canvas.winfo_height() - 20)
            image.thumbnail((available_width, available_height), Image.Resampling.LANCZOS)
            self.preview_photo = ImageTk.PhotoImage(image)
            self.canvas.delete("all")
            self.canvas.create_image(
                max(10, self.canvas.winfo_width() // 2),
                max(10, self.canvas.winfo_height() // 2),
                image=self.preview_photo,
                anchor="center",
            )
            self.page_var.set(f"Page {self.page_index + 1} of {self.document.page_count}")
            self.previous_button.configure(
                state="normal" if self.page_index > 0 else "disabled"
            )
            self.next_button.configure(
                state="normal" if self.page_index + 1 < self.document.page_count else "disabled"
            )
        except Exception as exc:
            self.status_var.set(f"Preview unavailable: {exc}")

    def change_page(self, offset: int) -> None:
        if self.document is None:
            return
        target = self.page_index + offset
        if 0 <= target < self.document.page_count:
            self.page_index = target
            self.render_page()

    def start_compression(self) -> None:
        if self.worker_running or not self.source_path or not self.output_path:
            return
        if self.output_path.exists():
            messagebox.showwarning(
                "PDF Compressor",
                "The selected output already exists. Choose another filename; existing files are never overwritten.",
            )
            return
        source = self.source_path
        output = self.output_path
        preset = self.preset_var.get()
        try:
            target_mb = float(self.target_mb_var.get().strip())
            if not 0.1 <= target_mb <= 100.0:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "PDF Compressor",
                "Maximum file size must be a number from 0.1 MB to 100 MB.",
            )
            return
        self.worker_running = True
        self.compress_button.configure(state="disabled")
        self.progress.start(12)
        self.status_var.set("Compressing in background...")
        self.result_var.set("Please wait. The original PDF is not being modified.")

        def worker() -> None:
            try:
                result = compress_pdf(
                    source,
                    output,
                    preset=preset,
                    target_mb=target_mb,
                )
                self.after(0, lambda: self.finish_compression(result=result))
            except Exception as exc:
                error = str(exc)
                self.after(0, lambda: self.finish_compression(error=error))

        threading.Thread(target=worker, name="pdf-compressor", daemon=True).start()

    def finish_compression(self, *, result=None, error: str = "") -> None:
        self.worker_running = False
        self.progress.stop()
        self.compress_button.configure(state="normal" if self.source_path else "disabled")
        if error:
            self.status_var.set("Compression failed")
            self.result_var.set(error)
            self.log_callback(f"[PDF COMPRESSOR] ERROR: {error}")
            messagebox.showerror("PDF Compressor", error)
            return
        if result.saved:
            self.output_path = result.output
            self.output_var.set(str(result.output))
            self.status_var.set("Compressed copy saved")
            self.result_var.set(
                f"Original: {format_size(result.original_bytes)}\n"
                f"Compressed: {format_size(result.compressed_bytes)}\n"
                f"Maximum selected: {format_size(result.target_bytes or 0)}\n"
                f"Space saved: {result.savings_percent:.1f}%\n\n{result.output}"
            )
            self.open_result_button.configure(state="normal")
            self.log_callback(
                f"[PDF COMPRESSOR] Saved {result.output} ({result.savings_percent:.1f}% smaller)"
            )
            messagebox.showinfo(
                "PDF Compressor",
                f"Compressed PDF/A read-only copy saved.\n\n"
                f"Maximum selected: {self.target_mb_var.get()} MB\n"
                f"Space saved: {result.savings_percent:.1f}%\n\n{result.output}",
            )
        else:
            self.status_var.set("No smaller copy created")
            self.result_var.set(result.message)
            self.open_result_button.configure(state="disabled")
            self.log_callback(f"[PDF COMPRESSOR] {result.message}")
            messagebox.showinfo("PDF Compressor", result.message)

    def open_source_external(self) -> None:
        if self.source_path and self.source_path.exists():
            os.startfile(self.source_path)

    def open_result_external(self) -> None:
        if self.output_path and self.output_path.exists():
            os.startfile(self.output_path)


if __name__ == "__main__":
    root = tk.Tk()
    root.title("EDH PDF Compressor")
    root.geometry("1200x760")
    PDFCompressorFrame(root).pack(fill="both", expand=True)
    root.mainloop()
