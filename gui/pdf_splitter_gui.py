"""Tkinter PDF Splitter — split a PDF into page ranges (iLovePDF-style).

Features (mirrors the reference screenshot):
  - Pick a PDF file; the page count is shown.
  - Add multiple ranges, each "from page X to page Y".
  - Optional "Merge all ranges in one PDF file" checkbox.
  - Choose the output folder; split creates one PDF per range, or a
    single merged PDF when the checkbox is ticked.

Can be embedded as a notebook tab (`PdfSplitterFrame`) or run standalone
(`PdfSplitterWindow` / `python -m gui.pdf_splitter_gui`).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import fitz  # PyMuPDF
except Exception as exc:  # pragma: no cover - dependency guard
    fitz = None
    _FITZ_IMPORT_ERROR = exc
else:
    _FITZ_IMPORT_ERROR = None

from core.activity_logger import logger
from core.pdf_compressor import find_ghostscript

DEFAULT_OUTPUT = Path(os.getenv("CLAIMS_OUTPUT_FOLDER", r"C:\claims_bot\output"))
DEFAULT_SCANS = Path(os.getenv("CLAIMS_SCAN_FOLDER", r"C:\claims_bot\scans"))


class PdfSplitterFrame(ttk.Frame):
    """PDF Splitter widget — usable as a notebook tab or standalone frame."""

    def __init__(self, parent, log_callback=None) -> None:
        super().__init__(parent, padding=12)
        self.log_callback = log_callback or (lambda _message: None)
        self.pdf_path: Path | None = None
        self.page_count = 0
        self.ranges: list[tuple[int, int]] = []  # 1-based (from, to)
        self._build_ui()

    # ------------------------------------------------------------ ui

    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(
            header, text="SPLIT PDF", font=("Segoe UI", 16, "bold")
        ).pack(side="left")
        self.page_label = ttk.Label(header, text="", foreground="#555555")
        self.page_label.pack(side="left", padx=12)

        # --- file picker -------------------------------------------
        file_frame = ttk.LabelFrame(self, text="1. Piliin ang PDF", padding=8)
        file_frame.pack(fill="x", pady=(0, 8))
        row = ttk.Frame(file_frame)
        row.pack(fill="x")
        self.file_var = tk.StringVar(value="")
        ttk.Entry(row, textvariable=self.file_var, state="readonly").pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(row, text="Browse...", command=self._pick_file).pack(
            side="left", padx=(6, 0)
        )

        # --- ranges ------------------------------------------------
        range_frame = ttk.LabelFrame(self, text="2. Mga Range (from page X to Y)", padding=8)
        range_frame.pack(fill="both", expand=True, pady=(0, 8))
        range_frame.columnconfigure(0, weight=1)

        self.range_tree = ttk.Treeview(
            range_frame, columns=("range", "pages", "count"), show="headings", height=5
        )
        self.range_tree.heading("range", text="Range")
        self.range_tree.heading("pages", text="Pages")
        self.range_tree.heading("count", text="# Pages")
        self.range_tree.column("range", width=80, anchor="w")
        self.range_tree.column("pages", width=130, anchor="w")
        self.range_tree.column("count", width=70, anchor="center")
        self.range_tree.grid(row=0, column=0, sticky="nsew", pady=(0, 6))

        controls = ttk.Frame(range_frame)
        controls.grid(row=1, column=0, sticky="w")
        ttk.Label(controls, text="From page:").pack(side="left")
        self.from_spin = ttk.Spinbox(controls, from_=1, to=9999, width=6)
        self.from_spin.set(1)
        self.from_spin.pack(side="left", padx=(4, 8))
        ttk.Label(controls, text="to page:").pack(side="left")
        self.to_spin = ttk.Spinbox(controls, from_=1, to=9999, width=6)
        self.to_spin.set(1)
        self.to_spin.pack(side="left", padx=(4, 8))
        ttk.Button(controls, text="+ Add Range", command=self._add_range).pack(
            side="left", padx=(8, 4)
        )
        ttk.Button(controls, text="Remove Selected", command=self._remove_range).pack(
            side="left"
        )

        # --- preview line ------------------------------------------
        self.preview_var = tk.StringVar(value="Wala pang range na naidagdag.")
        ttk.Label(
            range_frame,
            textvariable=self.preview_var,
            foreground="#0a7a2f",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=2, column=0, sticky="w", pady=(2, 0))

        # --- merge checkbox ----------------------------------------
        self.merge_var = tk.BooleanVar(value=False)
        self.merge_var.trace_add("write", lambda *_: self._update_preview())
        ttk.Checkbutton(
            self,
            text="Merge all ranges in one PDF file",
            variable=self.merge_var,
        ).pack(anchor="w", pady=(0, 8))

        # --- PDF/A read-only checkbox ------------------------------
        self.pdfa_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self,
            text="Output as PDF/A read-only",
            variable=self.pdfa_var,
        ).pack(anchor="w", pady=(0, 8))

        # --- output folder -----------------------------------------
        out_frame = ttk.LabelFrame(self, text="3. Output Folder", padding=8)
        out_frame.pack(fill="x", pady=(0, 10))
        out_row = ttk.Frame(out_frame)
        out_row.pack(fill="x")
        self.out_var = tk.StringVar(value=str(DEFAULT_OUTPUT))
        ttk.Entry(out_row, textvariable=self.out_var).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(out_row, text="Browse...", command=self._pick_output).pack(
            side="left", padx=(6, 0)
        )

        # --- split button + status --------------------------------
        self.split_btn = ttk.Button(
            self, text="SPLIT PDF", command=self._split, style="Accent.TButton"
        )
        self.split_btn.pack(fill="x", pady=(0, 6))
        self.status_var = tk.StringVar(value="Pumili ng PDF file para magsimula.")
        ttk.Label(self, textvariable=self.status_var, foreground="#555555").pack(
            anchor="w"
        )

    # ------------------------------------------------------------ actions

    def _pick_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Piliin ang PDF",
            initialdir=str(DEFAULT_SCANS),
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not path:
            return
        self._load_pdf(Path(path))

    def _load_pdf(self, path: Path) -> None:
        try:
            with fitz.open(path) as doc:
                self.page_count = doc.page_count
        except Exception as exc:
            messagebox.showerror("PDF Error", f"Hindi mabuksan ang PDF:\n\n{exc}")
            return
        self.pdf_path = path
        self.file_var.set(str(path))
        self.page_label.config(
            text=f"{self.page_count} pages"
            if self.page_count != 1
            else "1 page"
        )
        self.to_spin.configure(to=max(1, self.page_count))
        self.to_spin.set(min(2, self.page_count))
        self.range_tree.delete(*self.range_tree.get_children())
        self.ranges.clear()
        self.status_var.set(f"Na-load: {path.name} ({self.page_count} pages)")

    def _add_range(self) -> None:
        if self.pdf_path is None:
            messagebox.showwarning("Split PDF", "Pumili muna ng PDF file.")
            return
        try:
            start = int(self.from_spin.get())
            end = int(self.to_spin.get())
        except ValueError:
            messagebox.showwarning("Split PDF", "Ilagay ang valid page numbers.")
            return
        if start < 1 or end > self.page_count or start > end:
            messagebox.showwarning(
                "Split PDF",
                f"Invalid range. Ang PDF ay may {self.page_count} pages "
                f"(1-{self.page_count}).",
            )
            return
        self.ranges.append((start, end))
        self.range_tree.insert(
            "", "end",
            values=(
                f"Range {len(self.ranges)}",
                f"pages {start} to {end}",
                f"{end - start + 1}",
            ),
        )
        self._update_preview()
        self.status_var.set(f"Na-add: pages {start} to {end}")

    def _remove_range(self) -> None:
        selected = self.range_tree.selection()
        if not selected:
            messagebox.showwarning("Split PDF", "Pumili ng range na tatanggalin.")
            return
        for item in selected:
            idx = self.range_tree.index(item)
            self.range_tree.delete(item)
            del self.ranges[idx]
        self._renumber_ranges()
        self._update_preview()

    def _renumber_ranges(self) -> None:
        for i, item in enumerate(self.range_tree.get_children(), start=1):
            values = self.range_tree.item(item, "values")
            self.range_tree.item(item, values=(f"Range {i}", values[1], values[2]))

    def _update_preview(self) -> None:
        """Update the preview line: how many splits and total pages."""
        if not self.ranges:
            self.preview_var.set("Wala pang range na naidagdag.")
            return
        total_pages = sum(end - start + 1 for start, end in self.ranges)
        splits = 1 if self.merge_var.get() else len(self.ranges)
        mode = "1 PDF file (merged)" if self.merge_var.get() else f"{len(self.ranges)} PDF files"
        self.preview_var.set(
            f"Preview: {len(self.ranges)} range(s) · {total_pages} pages · "
            f"{mode} ang gagawin"
        )

    def _pick_output(self) -> None:
        folder = filedialog.askdirectory(
            title="Piliin ang output folder", initialdir=str(DEFAULT_OUTPUT)
        )
        if folder:
            self.out_var.set(folder)

    # ------------------------------------------------------------ split

    def _split(self) -> None:
        if self.pdf_path is None:
            messagebox.showwarning("Split PDF", "Pumili muna ng PDF file.")
            return
        if not self.ranges:
            messagebox.showwarning("Split PDF", "Magdagdag ng kahit isang range.")
            return
        out_dir = Path(self.out_var.get().strip() or str(DEFAULT_OUTPUT))
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Split PDF", f"Hindi magawa ang folder:\n\n{exc}")
            return

        stem = self.pdf_path.stem
        self.split_btn.config(state="disabled")
        self.status_var.set("Sinisplit...")
        self.update_idletasks()
        try:
            created = self._do_split(out_dir, stem)
        except Exception as exc:
            logger.error(f"PDF split failed: {exc}")
            messagebox.showerror("Split PDF", f"Error habang nag-split:\n\n{exc}")
            created = []
        finally:
            self.split_btn.config(state="normal")

        if created:
            self.status_var.set(
                f"Tapos! {len(created)} file(s) na-save sa {out_dir}"
            )
            logger.success(f"PDF split: {len(created)} file(s) -> {out_dir}")
            self.log_callback(f"PDF split: {len(created)} file(s) -> {out_dir}")
            try:
                os.startfile(out_dir)  # type: ignore[attr-defined]
            except OSError:
                pass
            messagebox.showinfo(
                "Split PDF",
                f"{len(created)} file(s) na-save sa:\n{out_dir}",
            )

    def _do_split(self, out_dir: Path, stem: str) -> list[Path]:
        """Create the split PDFs. Returns the list of created files."""
        pdfa = self.pdfa_var.get()
        if self.merge_var.get():
            # single output: all selected pages, in range order
            pages: list[int] = []
            for start, end in self.ranges:
                pages.extend(range(start - 1, end))
            pages = sorted(set(pages))
            return [self._write_selection(out_dir / f"{stem}_split.pdf", pages, pdfa)]

        created: list[Path] = []
        for i, (start, end) in enumerate(self.ranges, start=1):
            pages = list(range(start - 1, end))
            name = f"{stem}_range{i}_p{start}-{end}.pdf"
            created.append(self._write_selection(out_dir / name, pages, pdfa))
        return created

    def _write_selection(self, target: Path, pages: list[int],
                         pdfa: bool = True) -> Path:
        """Write pages (0-based) of self.pdf_path into `target`.

        When `pdfa` is True the raw split is converted to a PDF/A
        read-only file via Ghostscript (falling back to plain PDF when
        Ghostscript is unavailable).
        """
        if not pages:
            raise ValueError("Empty page selection")
        with fitz.open(self.pdf_path) as doc:
            new_doc = fitz.open()
            try:
                new_doc.insert_pdf(doc, from_page=pages[0], to_page=pages[0])
                for p in pages[1:]:
                    new_doc.insert_pdf(doc, from_page=p, to_page=p)
                if not pdfa:
                    new_doc.save(target, deflate=True, garbage=3)
                    return target
                # write raw split to a temp file, then convert to PDF/A
                fd, tmp_name = tempfile.mkstemp(
                    prefix=f".{target.stem}_", suffix=".tmp.pdf",
                    dir=target.parent,
                )
                os.close(fd)
                tmp_path = Path(tmp_name)
                tmp_path.unlink(missing_ok=True)
                new_doc.save(tmp_path, deflate=True, garbage=3)
            finally:
                new_doc.close()
            self._convert_to_pdfa(tmp_path, target)
            tmp_path.unlink(missing_ok=True)
        return target

    def _convert_to_pdfa(self, source: Path, output: Path) -> None:
        """Convert `source` to a PDF/A-2 read-only file at `output`.

        Ghostscript 10.x requires (a) a customized PDFA_def.ps pointing
        at the srgb.icc profile and (b) --permit-file-read for that
        profile when SAFER mode is on.
        """
        ghostscript = find_ghostscript()
        if not ghostscript:
            logger.warning(
                "Ghostscript not found; PDF/A output skipped, plain PDF saved."
            )
            shutil.copy2(source, output)
            return

        gs_root = Path(ghostscript).resolve().parents[1]  # ...\gs\gs10.07.0
        lib_dir = gs_root / "lib"
        icc_dir = gs_root / "iccprofiles"
        srgb = icc_dir / "srgb.icc"
        pdfa_def_src = lib_dir / "PDFA_def.ps"

        if not pdfa_def_src.is_file() or not srgb.is_file():
            # fall back to plain PDF copy instead of a broken conversion
            logger.warning(
                "Ghostscript PDF/A files missing (PDFA_def.ps/srgb.icc); "
                "plain PDF saved."
            )
            shutil.copy2(source, output)
            return

        # customize PDFA_def.ps with the absolute srgb.icc path
        tmp_dir = Path(tempfile.mkdtemp(prefix="webscan_pdfa_"))
        try:
            content = pdfa_def_src.read_text(encoding="latin-1")
            content = re.sub(
                r"/ICCProfile \(.*?\)",
                "/ICCProfile (" + str(srgb).replace("\\", "/") + ")",
                content,
                count=1,
            )
            custom_def = tmp_dir / "PDFA_def.ps"
            custom_def.write_text(content, encoding="latin-1")

            command = [
                ghostscript,
                "-dPDFA=2",
                "-dBATCH",
                "-dNOPAUSE",
                "-dSAFER",
                "-dQUIET",
                "-I" + str(lib_dir),
                "-sICCProfilesDir=" + str(icc_dir),
                f"--permit-file-read={srgb}",
                "-sDEVICE=pdfwrite",
                "-dPDFACompatibilityPolicy=1",
                "-sColorConversionStrategy=RGB",
                "-sProcessColorModel=DeviceRGB",
                "-dEmbedAllFonts=true",
                "-dSubsetFonts=true",
                "-dAutoRotatePages=/None",
                "-dDetectDuplicateImages=true",
                "-dCompressFonts=true",
                "-dPrinted=false",
                "-dModifyAnnotations=false",
                f"-sOutputFile={output}",
                str(custom_def),
                str(source),
            ]
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                ),
            )
            combined = (completed.stdout or "") + (completed.stderr or "")
            if completed.returncode != 0 or not output.is_file():
                details = combined.strip() or "Unknown Ghostscript error"
                raise RuntimeError(f"PDF/A conversion failed: {details}")
            if "Failed to open the supplied ICCProfile" in combined:
                raise RuntimeError(
                    "PDF/A conversion failed: cannot read the ICC profile."
                )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # make the output read-only
        try:
            os.chmod(output, 0o444)
        except OSError:
            pass


class PdfSplitterWindow(tk.Tk):
    """Standalone window wrapper around PdfSplitterFrame."""

    def __init__(self) -> None:
        super().__init__()
        if fitz is None:
            messagebox.showerror(
                "Missing Dependency",
                f"PyMuPDF (fitz) is required.\n\n{_FITZ_IMPORT_ERROR}",
            )
            self.destroy()
            return
        self.title("EDH Claims — Split PDF")
        self.geometry("640x560")
        self.minsize(560, 500)
        self.frame = PdfSplitterFrame(self)
        self.frame.pack(fill="both", expand=True)


def main() -> None:
    window = PdfSplitterWindow()
    if window.winfo_exists():
        try:
            style = ttk.Style(window)
            style.configure("Accent.TButton", font=("Segoe UI", 11, "bold"))
        except tk.TclError:
            pass
        window.mainloop()


if __name__ == "__main__":
    main()
