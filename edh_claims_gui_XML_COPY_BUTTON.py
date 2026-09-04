import os
import sys
import json
import shutil
import re
import sqlite3
import stat
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageTk
import fitz
from PyPDF2 import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from core.xml_auto_copy import (
    XmlAutoCopyService,
    XmlCopySummary,
    format_event as format_xml_copy_event,
)
from gui.not_transmitted_batches_tab import NotTransmittedBatchesFrame
from gui.pdf_compressor_tab import PDFCompressorFrame
from gui.pdf_splitter_gui import PdfSplitterFrame
from gui.add_claims_upload_tab import AddClaimsUploadFrame
from gui.claim_attachments_tab import ClaimAttachmentsFrame
from date_fill_hbsys.hbsys_window import find_hbsys_window

BASE_DIR = r"C:\claims_bot"
CONFIG_FILE = os.path.join(BASE_DIR, "claims_gui_config.json")
DOCTORS_FILE = os.path.join(BASE_DIR, "doctors_config.json")
GHOSTSCRIPT_PATH = r"C:\Program Files\gs\gs10.07.0\bin\gswin64c.exe"
MANUAL_SIGN_MAX_SIZE_KB = 1000

SCRIPT_CONFIG = {
    "merge_pdf": "merge.py",
    "convert_pdfa": "pdfa.py",
    "claims_processor": "bot_unknown_trainer_DEFERRED_OUTPUT_REVIEW_PATSUFFIX_ADM_DIS.py", 
    "unknown_review_manager": "unknown_review_manager_INTEGRATED_PDFA_THREAD.py",
    "patient_review_queue": "patient_review_queue_gui.py",
    "date_fill_hbsys_testing": "date_fill_hbsys_testing_launcher.py",
    "xml_generator_clicker": "xml_generator_clicker_launcher.py",
    "claims_checker": "claims_checker.py",
    "fees_checker": "fees_checker.py",
    "pdf_splitter": "pdf_splitter_gui.py",
}

REVIEW_KEYWORDS = [
    "UNKNOWN",
    "SOA2_PAGE1",
    "SOA2_PAGE2",
    "SOA2_PAGE2_1",
    "MRF_PAGE1",
    "MRF_PAGE2",
    "MRF_PAGE2_1",
]

DEFAULT_SETTINGS = {
    "base_dir": BASE_DIR,
    "scan_folder": os.path.join(BASE_DIR, "scans"),
    "output_folder": os.path.join(BASE_DIR, "output"),
    "backup_folder": os.path.join(BASE_DIR, "backup_originals"),
    "review_staging_folder": os.path.join(BASE_DIR, "review_staging"),
    "signature_folder": os.path.join(BASE_DIR, "signatures"),
    "sqlite_db": os.path.join(BASE_DIR, "claims.db"),
    "xml_source_folder": r"C:\Shared Folder\FTPURL",
    "enable_auto_sign": True,
    "enable_auto_sign_csf": True,
    "enable_auto_sign_cf2": True,
    "enable_date_signed": True,
    "enable_backup": True,
    "show_debug_logs": True,
    "enable_auto_process": False,
    "enable_auto_copy_xml": True,
    "theme": "Light Blue",
}

THEMES = {
    "Light Blue": {
        "bg": "#F4F7FB", "panel": "#FFFFFF", "ink": "#1F2937",
        "muted": "#64748B", "primary": "#2F6FED", "primary_dark": "#2457C5",
        "primary_soft": "#EAF1FF", "success": "#16A34A",
        "warning": "#F59E0B", "danger": "#DC2626", "line": "#D8E1EC",
        "log_bg": "#111827", "log_fg": "#DDE7F3",
    },
    "Soft Green": {
        "bg": "#F3F8F4", "panel": "#FFFFFF", "ink": "#1F2937",
        "muted": "#64748B", "primary": "#2F8F5B", "primary_dark": "#25764A",
        "primary_soft": "#E9F7EF", "success": "#16A34A",
        "warning": "#DFA000", "danger": "#DC2626", "line": "#D6E7DA",
        "log_bg": "#102019", "log_fg": "#E2F4E9",
    },
    "Warm Sand": {
        "bg": "#FAF6EF", "panel": "#FFFFFF", "ink": "#2B2118",
        "muted": "#7A6B5E", "primary": "#C26A2E", "primary_dark": "#9F5424",
        "primary_soft": "#FFF0E3", "success": "#15803D",
        "warning": "#D97706", "danger": "#B91C1C", "line": "#E9DCCB",
        "log_bg": "#241A12", "log_fg": "#F5EBDD",
    },
    "Slate Dark": {
        "bg": "#111827", "panel": "#1F2937", "ink": "#F8FAFC",
        "muted": "#CBD5E1", "primary": "#60A5FA", "primary_dark": "#3B82F6",
        "primary_soft": "#253449", "success": "#22C55E",
        "warning": "#FBBF24", "danger": "#F87171", "line": "#334155",
        "log_bg": "#020617", "log_fg": "#E2E8F0",
    },
}

DEFAULT_DOCTORS = [
    {
        "name": "RHODA JACQUELINE P. GAFFUD, MD",
        "aliases": "RHODA GAFFUD | GAFFUD, RHODA JACQUELINE",
        "signature_file": "gaffud_rhoda_jacqueline.png",
        "part_iv_x": "315",
        "part_iv_y": "145",
        "part_v_x": "460",
        "part_v_y": "62",
        "enabled": True,
    }
]


def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def normalize_windows_path(value):
    path = str(value or "").strip()
    if path.startswith("//"):
        return "\\\\" + path[2:].replace("/", "\\")
    return path.replace("/", "\\")




# ============================================================
# PDF PREVIEW + DRAG COORDINATES
# ============================================================
class DoctorPreviewFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)

        self.pdf_path = None
        self.original_pil = None
        self.preview_photo = None

        # Live signature preview
        self.signature_path = None
        self.signature_pil = None
        self.signature_photo = None
        self.signature_item = None

        self.zoom_percent = 70
        self.current_scale = 0.70
        self.image_offset_x = 20
        self.image_offset_y = 20

        # PDF coordinate system used by the actual bot/reportlab:
        # x/y are PDF points, origin is BOTTOM-LEFT.
        # Preview image coordinates are pixels, origin is TOP-LEFT.
        self.render_zoom = 2
        self.page_width = None
        self.page_height = None

        self.box = None
        self.text = None

        self.dragging = False
        self.offset_x = 0
        self.offset_y = 0

        self.build_ui()

    def build_ui(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x")

        ttk.Button(toolbar, text="Open PDF", command=self.load_pdf).pack(side="left", padx=5, pady=5)
        ttk.Button(toolbar, text="Fit Page", command=self.fit_page).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Zoom -", command=self.zoom_out).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Zoom +", command=self.zoom_in).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Reset Box", command=self.reset_box).pack(side="left", padx=5)
        ttk.Button(toolbar, text="Copy PDF Coords", command=self.copy_true_coords).pack(side="left", padx=5)

        self.zoom_var = tk.StringVar(value="Zoom: 70%")
        ttk.Label(toolbar, textvariable=self.zoom_var, font=("Segoe UI", 10, "bold")).pack(side="left", padx=15)

        self.coord_var = tk.StringVar(value="PDF X: 0 | PDF Y: 0 | W: 0 | H: 0")
        ttk.Label(toolbar, textvariable=self.coord_var, font=("Segoe UI", 10, "bold")).pack(side="right", padx=10)

        # Editable width/height in PDF coordinates.
        self.box_w_var = tk.StringVar(value="95")
        self.box_h_var = tk.StringVar(value="30")

        ttk.Label(toolbar, text="W:").pack(side="left", padx=(12, 2))
        ttk.Entry(toolbar, textvariable=self.box_w_var, width=6).pack(side="left")

        ttk.Label(toolbar, text="H:").pack(side="left", padx=(8, 2))
        ttk.Entry(toolbar, textvariable=self.box_h_var, width=6).pack(side="left")

        ttk.Button(
            toolbar,
            text="Apply W/H",
            command=self.apply_box_size
        ).pack(side="left", padx=5)

        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(container, bg="gray20")

        self.v_scroll = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.h_scroll = ttk.Scrollbar(container, orient="horizontal", command=self.canvas.xview)

        self.canvas.configure(
            yscrollcommand=self.v_scroll.set,
            xscrollcommand=self.h_scroll.set
        )

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.v_scroll.grid(row=0, column=1, sticky="ns")
        self.h_scroll.grid(row=1, column=0, sticky="ew")

        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        self.canvas.bind("<ButtonPress-1>", self.start_drag)
        self.canvas.bind("<B1-Motion>", self.do_drag)
        self.canvas.bind("<ButtonRelease-1>", self.stop_drag)

        self.canvas.bind("<MouseWheel>", self.mousewheel_scroll)

    def load_pdf(self):
        path = filedialog.askopenfilename(
            title="Select PDF",
            filetypes=[("PDF Files", "*.pdf")]
        )

        if not path:
            return

        self.load_pdf_path(path, page_index=0)

    def load_pdf_path(self, path, page_index=0):
        self.pdf_path = path

        try:
            doc = fitz.open(path)
            if doc.page_count == 0:
                messagebox.showerror("PDF Preview Error", "PDF has no pages.")
                return

            page_index = max(0, min(int(page_index), doc.page_count - 1))
            page = doc[page_index]

            self.page_width = float(page.rect.width)
            self.page_height = float(page.rect.height)

            pix = page.get_pixmap(
                matrix=fitz.Matrix(self.render_zoom, self.render_zoom),
                alpha=False
            )

            temp_img = "_preview_temp.png"
            pix.save(temp_img)

            self.original_pil = Image.open(temp_img).convert("RGB")

            self.fit_page()

        except Exception as e:
            messagebox.showerror("PDF Preview Error", str(e))


    def render_preview(self):
        if self.original_pil is None:
            return

        self.current_scale = self.zoom_percent / 100.0

        new_w = max(1, int(self.original_pil.width * self.current_scale))
        new_h = max(1, int(self.original_pil.height * self.current_scale))

        pil = self.original_pil.resize((new_w, new_h), Image.LANCZOS)

        self.preview_photo = ImageTk.PhotoImage(pil)

        # Preserve current PDF coordinates before redraw.
        pdf_coords = None
        if self.box:
            pdf_coords = self.get_pdf_box_coordinates()

        self.canvas.delete("all")

        self.canvas.create_image(
            self.image_offset_x,
            self.image_offset_y,
            image=self.preview_photo,
            anchor="nw"
        )

        self.canvas.config(
            scrollregion=(0, 0, new_w + 50, new_h + 50)
        )

        if pdf_coords:
            self.create_drag_box_from_pdf_coords(pdf_coords)
        else:
            self.create_drag_box()

        self.zoom_var.set(f"Zoom: {self.zoom_percent}%")


    def fit_page(self):
        if self.original_pil is None:
            return

        self.update_idletasks()

        canvas_w = max(200, self.canvas.winfo_width() - 60)
        canvas_h = max(200, self.canvas.winfo_height() - 60)

        scale_w = canvas_w / self.original_pil.width
        scale_h = canvas_h / self.original_pil.height

        scale = min(scale_w, scale_h)

        self.zoom_percent = max(20, int(scale * 100))

        self.render_preview()

    def zoom_in(self):
        if self.original_pil is None:
            return

        self.zoom_percent = min(200, self.zoom_percent + 10)
        self.render_preview()

    def zoom_out(self):
        if self.original_pil is None:
            return

        self.zoom_percent = max(20, self.zoom_percent - 10)
        self.render_preview()

    def create_drag_box(self):
        # Default PDF coordinates close to CSF Part IV signature area.
        # These are the SAME coordinate type used by your bot:
        # "x", "y", "max_w", "max_h"
        pdf_x = 270
        pdf_y = 190
        pdf_w = 95
        pdf_h = 30

        self.create_drag_box_from_pdf_coords((pdf_x, pdf_y, pdf_w, pdf_h))


    def pdf_box_to_screen_box(self, pdf_coords):
        """
        Convert bot/reportlab PDF coordinates to preview screen coordinates.

        Bot/reportlab:
            origin = bottom-left
            x, y = bottom-left of signature box

        Preview image:
            origin = top-left

        Canvas:
            scaled image + offset
        """
        if self.page_height is None:
            return None

        pdf_x, pdf_y, pdf_w, pdf_h = pdf_coords

        img_x = pdf_x * self.render_zoom
        img_y = (self.page_height - pdf_y - pdf_h) * self.render_zoom
        img_w = pdf_w * self.render_zoom
        img_h = pdf_h * self.render_zoom

        screen_x1 = self.image_offset_x + (img_x * self.current_scale)
        screen_y1 = self.image_offset_y + (img_y * self.current_scale)
        screen_x2 = screen_x1 + (img_w * self.current_scale)
        screen_y2 = screen_y1 + (img_h * self.current_scale)

        return screen_x1, screen_y1, screen_x2, screen_y2


    def screen_box_to_pdf_box(self, screen_coords):
        """
        Convert preview screen box to bot/reportlab PDF coordinates.
        This is the IMPORTANT conversion.

        Output is directly usable in your bot:
            "x": PDF_X
            "y": PDF_Y
            "max_w": PDF_W
            "max_h": PDF_H
        """
        if self.page_height is None:
            return None

        sx1, sy1, sx2, sy2 = screen_coords

        img_x = (sx1 - self.image_offset_x) / self.current_scale
        img_y = (sy1 - self.image_offset_y) / self.current_scale
        img_w = (sx2 - sx1) / self.current_scale
        img_h = (sy2 - sy1) / self.current_scale

        pdf_x = img_x / self.render_zoom
        pdf_w = img_w / self.render_zoom
        pdf_h = img_h / self.render_zoom

        # Convert TOP-LEFT image y to BOTTOM-LEFT PDF y.
        pdf_y = self.page_height - (img_y / self.render_zoom) - pdf_h

        return (
            int(round(pdf_x)),
            int(round(pdf_y)),
            int(round(pdf_w)),
            int(round(pdf_h))
        )


    def create_drag_box_from_pdf_coords(self, pdf_coords):
        screen_box = self.pdf_box_to_screen_box(pdf_coords)

        if not screen_box:
            return

        x1, y1, x2, y2 = screen_box

        # Live signature preview should appear under the red border.
        self.draw_signature_preview_in_box(x1, y1, x2, y2)

        self.box = self.canvas.create_rectangle(
            x1, y1, x2, y2,
            outline="red",
            width=3
        )

        self.text = self.canvas.create_text(
            x1 + 5,
            y1 - 15,
            anchor="w",
            fill="yellow",
            font=("Segoe UI", 10, "bold"),
            text="DRAG THIS AREA"
        )

        self.keep_signature_preview_visible()
        self.update_coordinates()

    def keep_signature_preview_visible(self):
        """
        Keep live signature preview above the PDF image but below the red box.
        """
        if self.signature_item:
            self.canvas.tag_raise(self.signature_item)

        if self.box:
            self.canvas.tag_raise(self.box)

        if self.text:
            self.canvas.tag_raise(self.text)


    def set_signature_preview(self, signature_path):
        """
        Load signature PNG for live preview.
        Called by Doctor Manager when selecting/browsing a doctor's signature.
        """
        self.signature_path = signature_path
        self.signature_pil = None
        self.signature_photo = None

        if signature_path and os.path.exists(signature_path):
            try:
                self.signature_pil = Image.open(signature_path).convert("RGBA")
            except Exception as e:
                messagebox.showerror("Signature Preview Error", str(e))
                self.signature_pil = None

        self.refresh_signature_preview()


    def refresh_signature_preview(self):
        """
        Redraw current red box and signature preview without changing coordinates.
        """
        if not self.box:
            return

        coords = self.get_pdf_box_coordinates()

        if not coords:
            return

        # Delete old box/text/signature then recreate using same PDF coords.
        if self.signature_item:
            self.canvas.delete(self.signature_item)
            self.signature_item = None

        if self.box:
            self.canvas.delete(self.box)
            self.box = None

        if self.text:
            self.canvas.delete(self.text)
            self.text = None

        self.create_drag_box_from_pdf_coords(coords)


    def draw_signature_preview_in_box(self, x1, y1, x2, y2):
        """
        Draw actual signature PNG inside the red box.
        This is preview only. It does not modify the PDF.
        """
        self.signature_item = None

        if self.signature_pil is None:
            return

        box_w = max(1, int(x2 - x1))
        box_h = max(1, int(y2 - y1))

        sig_w, sig_h = self.signature_pil.size

        if sig_w <= 0 or sig_h <= 0:
            return

        scale = min(
            box_w / float(sig_w),
            box_h / float(sig_h)
        )

        new_w = max(1, int(sig_w * scale))
        new_h = max(1, int(sig_h * scale))

        resized = self.signature_pil.resize(
            (new_w, new_h),
            Image.LANCZOS
        )

        self.signature_photo = ImageTk.PhotoImage(resized)

        offset_x = x1 + ((box_w - new_w) / 2)
        offset_y = y1 + ((box_h - new_h) / 2)

        self.signature_item = self.canvas.create_image(
            offset_x,
            offset_y,
            image=self.signature_photo,
            anchor="nw"
        )


    def reset_box(self):
        if not self.box:
            return

        self.canvas.delete(self.box)

        if self.signature_item:
            self.canvas.delete(self.signature_item)
            self.signature_item = None

        if self.text:
            self.canvas.delete(self.text)

        self.create_drag_box_from_pdf_coords((270, 190, 95, 30))


    def canvas_xy(self, event):
        return (
            self.canvas.canvasx(event.x),
            self.canvas.canvasy(event.y)
        )

    def start_drag(self, event):
        if not self.box:
            return

        ex, ey = self.canvas_xy(event)

        coords = self.canvas.coords(self.box)

        if coords[0] <= ex <= coords[2] and coords[1] <= ey <= coords[3]:
            self.dragging = True
            self.offset_x = ex - coords[0]
            self.offset_y = ey - coords[1]

    def do_drag(self, event):
        if not self.dragging:
            return

        ex, ey = self.canvas_xy(event)

        coords = self.canvas.coords(self.box)

        width = coords[2] - coords[0]
        height = coords[3] - coords[1]

        x1 = ex - self.offset_x
        y1 = ey - self.offset_y

        x2 = x1 + width
        y2 = y1 + height

        self.canvas.coords(self.box, x1, y1, x2, y2)
        self.canvas.coords(self.text, x1 + 5, y1 - 15)

        # Move live signature preview with the box.
        if self.signature_item:
            self.canvas.delete(self.signature_item)
            self.signature_item = None

        self.draw_signature_preview_in_box(x1, y1, x2, y2)

        self.keep_signature_preview_visible()

        self.update_coordinates()

    def stop_drag(self, event):
        self.dragging = False


    def get_pdf_box_coordinates(self):
        """
        Return stable PDF coordinates used by the actual bot.
        These DO NOT change when zoom changes.
        """
        if not self.box:
            return None

        coords = self.canvas.coords(self.box)
        return self.screen_box_to_pdf_box(coords)


    def update_coordinates(self):
        pdf_coords = self.get_pdf_box_coordinates()

        if not pdf_coords:
            return

        pdf_x, pdf_y, pdf_w, pdf_h = pdf_coords

        self.coord_var.set(
            f"PDF X: {pdf_x} | PDF Y: {pdf_y} | W: {pdf_w} | H: {pdf_h}"
        )

        # Keep editable W/H fields updated while dragging/resizing.
        self.box_w_var.set(str(pdf_w))
        self.box_h_var.set(str(pdf_h))


    def copy_true_coords(self):
        coords = self.get_pdf_box_coordinates()

        if not coords:
            return

        x, y, w, h = coords

        # Directly matches your bot settings:
        # "x": x, "y": y, "max_w": w, "max_h": h
        value = f'"x": {x},\\n"y": {y},\\n"max_w": {w},\\n"max_h": {h}'

        self.clipboard_clear()
        self.clipboard_append(value)

        messagebox.showinfo(
            "Copied",
            "PDF coordinates copied. Paste directly into doctor config:\\n\\n" + value
        )


    def apply_box_size(self):
        """
        Apply editable W/H while keeping current PDF X/Y.
        W/H are PDF coordinate values, same as max_w/max_h in bot.
        """
        coords = self.get_pdf_box_coordinates()

        if not coords:
            return

        x, y, old_w, old_h = coords

        try:
            new_w = int(float(self.box_w_var.get()))
            new_h = int(float(self.box_h_var.get()))
        except Exception:
            messagebox.showerror("Invalid Size", "Width and Height must be numbers.")
            return

        if new_w <= 0 or new_h <= 0:
            messagebox.showerror("Invalid Size", "Width and Height must be greater than zero.")
            return

        if self.signature_item:
            self.canvas.delete(self.signature_item)
            self.signature_item = None

        if self.box:
            self.canvas.delete(self.box)

        if self.text:
            self.canvas.delete(self.text)

        self.create_drag_box_from_pdf_coords((x, y, new_w, new_h))


    def mousewheel_scroll(self, event):
        self.canvas.yview_scroll(
            int(-1 * (event.delta / 120)),
            "units"
        )



def mm_to_points(value):
    return float(value) * 72 / 25.4


class PDFESignFrame(ttk.Frame):
    """PDF e-sign tab: click/drag signatures, then save signed PDF/A read-only."""

    def __init__(self, parent, settings_getter, log_callback=None):
        super().__init__(parent)
        self.settings_getter = settings_getter
        self.log_callback = log_callback or (lambda message: None)
        self.pdf_path = None
        self.signature_path = None
        self.document = None
        self.current_page = 0
        self.zoom = 1.5
        self.placements = []
        self.selected_index = None
        self.drag_offset = None
        self.page_photo = None
        self.signature_pil = None
        self.signature_preview_refs = []
        self.signature_cache = {}
        self.pdf_label_var = tk.StringVar(value="No PDF selected")
        self.signature_label_var = tk.StringVar(value="No e-sign selected")
        self.favorite_signature_var = tk.StringVar(value="")
        self.favorite_signatures = {}
        self.page_var = tk.IntVar(value=1)
        self.page_count_var = tk.StringVar(value="/ 0")
        self.zoom_var = tk.IntVar(value=150)
        self.signature_width_mm = tk.IntVar(value=35)
        self.placement_var = tk.StringVar(value="Placements: 0")
        self.status_var = tk.StringVar(value="Ready")
        self.build_ui()

    def build_ui(self):
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="PDF E-SIGN", font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Label(
            header,
            text="Choose an e-sign, click to place it. Change e-sign anytime; each placed signature keeps its own image.",
        ).pack(side="left", padx=16)

        file_box = ttk.LabelFrame(self, text="Files", padding=8)
        file_box.pack(fill="x")
        ttk.Button(file_box, text="Open PDF", command=self.open_pdf).grid(row=0, column=0, padx=4, pady=3)
        ttk.Label(file_box, textvariable=self.pdf_label_var).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(file_box, text="Choose E-Sign PNG/JPG", command=self.open_signature).grid(row=0, column=2, padx=4)
        ttk.Label(file_box, textvariable=self.signature_label_var).grid(row=0, column=3, sticky="w", padx=8)
        ttk.Label(file_box, text="Favorite:").grid(row=1, column=0, sticky="w", padx=4, pady=(4, 0))
        self.favorite_combo = ttk.Combobox(
            file_box,
            textvariable=self.favorite_signature_var,
            values=(),
            state="readonly",
        )
        self.favorite_combo.grid(row=1, column=1, columnspan=2, sticky="ew", padx=8, pady=(4, 0))
        self.favorite_combo.bind("<<ComboboxSelected>>", lambda _event: self.select_favorite_signature())
        ttk.Button(file_box, text="Refresh Favorites", command=self.refresh_signature_favorites).grid(row=1, column=3, sticky="w", padx=8, pady=(4, 0))
        file_box.columnconfigure(1, weight=1)
        file_box.columnconfigure(3, weight=1)
        self.refresh_signature_favorites()

        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=8)
        ttk.Button(controls, text="Previous", command=self.previous_page).pack(side="left")
        ttk.Label(controls, text="Page:").pack(side="left", padx=(10, 2))
        self.page_spin = ttk.Spinbox(
            controls,
            from_=1,
            to=1,
            textvariable=self.page_var,
            width=5,
            command=self.page_spin_changed,
        )
        self.page_spin.pack(side="left")
        self.page_spin.bind("<Return>", lambda _event: self.page_spin_changed())
        ttk.Label(controls, textvariable=self.page_count_var).pack(side="left", padx=(4, 14))
        ttk.Button(controls, text="Next", command=self.next_page).pack(side="left")
        ttk.Label(controls, text="Zoom").pack(side="left", padx=(22, 4))
        ttk.Button(controls, text="Fit Width", command=self.fit_width).pack(side="left", padx=(0, 6))
        ttk.Button(controls, text="Zoom -", command=lambda: self.bump_zoom(-10)).pack(side="left", padx=(0, 3))
        ttk.Button(controls, text="Zoom +", command=lambda: self.bump_zoom(10)).pack(side="left", padx=(0, 6))
        ttk.Scale(controls, from_=75, to=250, variable=self.zoom_var, command=self.zoom_changed, length=170).pack(side="left")
        ttk.Label(controls, text="Signature width").pack(side="left", padx=(22, 4))
        ttk.Scale(
            controls,
            from_=15,
            to=90,
            variable=self.signature_width_mm,
            command=self.signature_width_changed,
            length=170,
        ).pack(side="left")

        canvas_frame = ttk.Frame(self)
        canvas_frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(canvas_frame, bg="#8f8f8f", cursor="crosshair")
        v_scroll = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        h_scroll = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        self.canvas.bind("<ButtonPress-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)

        action_row = ttk.Frame(self)
        action_row.pack(fill="x", pady=(8, 0))
        ttk.Label(action_row, textvariable=self.placement_var).pack(side="left")
        ttk.Label(action_row, textvariable=self.status_var, foreground="blue").pack(side="left", padx=18)
        ttk.Button(action_row, text="Delete Selected Signature", command=self.delete_selected).pack(side="right")
        ttk.Button(
            action_row,
            text="Save Signed PDF/A Read-only Copy",
            command=self.save_signed_pdfa,
        ).pack(side="right", padx=8)

    def settings(self):
        return self.settings_getter() or {}

    def open_pdf(self):
        filename = filedialog.askopenfilename(
            title="Open PDF",
            initialdir=self.settings().get("output_folder", BASE_DIR),
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not filename:
            return
        try:
            document = fitz.open(filename)
        except Exception as exc:
            messagebox.showerror("PDF E-Sign", f"Unable to open PDF:\n\n{exc}")
            return
        if document.page_count == 0:
            document.close()
            messagebox.showwarning("PDF E-Sign", "The selected PDF has no pages.")
            return
        if self.document:
            self.document.close()
        self.document = document
        self.pdf_path = filename
        self.current_page = 0
        self.placements = []
        self.selected_index = None
        self.pdf_label_var.set(os.path.basename(filename))
        self.page_spin.config(to=document.page_count)
        self.page_var.set(1)
        self.page_count_var.set(f"/ {document.page_count}")
        self.render_page()

    def open_signature(self):
        filename = filedialog.askopenfilename(
            title="Choose E-Sign PNG/JPG",
            initialdir=self.settings().get("signature_folder", BASE_DIR),
            filetypes=[("Images", "*.png *.jpg *.jpeg"), ("All files", "*.*")],
        )
        if not filename:
            return
        try:
            self.signature_pil = Image.open(filename).convert("RGBA")
        except Exception as exc:
            messagebox.showerror("PDF E-Sign", f"Unable to load e-sign image:\n\n{exc}")
            return
        self.signature_path = filename
        self.signature_label_var.set(os.path.basename(filename))
        self.signature_cache[filename] = self.signature_pil
        self.render_page()

    def refresh_signature_favorites(self):
        folder = self.settings().get("signature_folder", "")
        favorites = {}
        if os.path.isdir(folder):
            for filename in sorted(os.listdir(folder)):
                if not filename.lower().endswith((".png", ".jpg", ".jpeg")):
                    continue
                label = os.path.splitext(filename)[0]
                favorites[label] = os.path.join(folder, filename)
        self.favorite_signatures = favorites
        values = list(favorites.keys())
        self.favorite_combo.config(values=values)
        if values and not self.favorite_signature_var.get():
            self.favorite_signature_var.set(values[0])

    def select_favorite_signature(self):
        label = self.favorite_signature_var.get()
        path = self.favorite_signatures.get(label)
        if not path:
            return
        try:
            self.signature_pil = Image.open(path).convert("RGBA")
        except Exception as exc:
            messagebox.showerror("PDF E-Sign", f"Unable to load favorite e-sign:\n\n{exc}")
            return
        self.signature_path = path
        self.signature_cache[path] = self.signature_pil
        self.signature_label_var.set(os.path.basename(path))
        self.render_page()

    def render_page(self):
        self.canvas.delete("all")
        self.signature_preview_refs = []
        self.page_photo = None
        if not self.document:
            self.canvas.create_text(24, 24, text="Open a PDF to start.", anchor="nw", fill="white")
            return
        page = self.document[self.current_page]
        self.zoom = float(self.zoom_var.get()) / 100
        pix = page.get_pixmap(matrix=fitz.Matrix(self.zoom, self.zoom), alpha=False)
        page_image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        self.page_photo = ImageTk.PhotoImage(page_image)
        self.canvas.create_image(0, 0, image=self.page_photo, anchor="nw")
        self.canvas.config(scrollregion=(0, 0, pix.width, pix.height))
        self.draw_placements()
        self.placement_var.set(f"Placements: {len(self.placements)}")

    def draw_placements(self):
        for index, placement in enumerate(self.placements):
            if placement["page"] != self.current_page:
                continue
            signature_pil = self.get_signature_image(placement.get("signature_path"))
            if signature_pil is None:
                continue
            x1, y1, x2, y2 = self.canvas_rect_for_placement(placement)
            width = max(1, int(x2 - x1))
            height = max(1, int(y2 - y1))
            preview = signature_pil.resize((width, height), Image.LANCZOS)
            photo = ImageTk.PhotoImage(preview)
            self.signature_preview_refs.append(photo)
            self.canvas.create_image(x1, y1, image=photo, anchor="nw")
            color = "#0066ff" if index == self.selected_index else "#444444"
            dash = (4, 2) if index == self.selected_index else (2, 3)
            self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=2, dash=dash)

    def signature_size_points(self):
        width_pt = mm_to_points(self.signature_width_mm.get())
        if not self.signature_pil:
            return width_pt, width_pt * 0.35
        ratio = self.signature_pil.height / max(self.signature_pil.width, 1)
        return width_pt, width_pt * ratio

    def get_signature_image(self, signature_path):
        if not signature_path:
            return None
        if signature_path not in self.signature_cache:
            try:
                self.signature_cache[signature_path] = Image.open(signature_path).convert("RGBA")
            except Exception:
                return None
        return self.signature_cache[signature_path]

    def canvas_rect_for_placement(self, placement):
        return (
            placement["x"] * self.zoom,
            placement["y"] * self.zoom,
            (placement["x"] + placement["w"]) * self.zoom,
            (placement["y"] + placement["h"]) * self.zoom,
        )

    def placement_at(self, canvas_x, canvas_y):
        for index in range(len(self.placements) - 1, -1, -1):
            placement = self.placements[index]
            if placement["page"] != self.current_page:
                continue
            x1, y1, x2, y2 = self.canvas_rect_for_placement(placement)
            if x1 <= canvas_x <= x2 and y1 <= canvas_y <= y2:
                return index
        return None

    def on_canvas_press(self, event):
        if not self.document:
            return
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        index = self.placement_at(canvas_x, canvas_y)
        if index is not None:
            self.selected_index = index
            placement = self.placements[index]
            self.drag_offset = (canvas_x / self.zoom - placement["x"], canvas_y / self.zoom - placement["y"])
            self.render_page()
            return
        if not self.signature_pil or not self.signature_path:
            messagebox.showinfo("PDF E-Sign", "Choose an e-sign image first.")
            return
        w, h = self.signature_size_points()
        placement = {
            "page": self.current_page,
            "x": canvas_x / self.zoom - w / 2,
            "y": canvas_y / self.zoom - h / 2,
            "w": w,
            "h": h,
            "signature_path": self.signature_path,
        }
        self.placements.append(placement)
        self.selected_index = len(self.placements) - 1
        self.clamp_placement(placement)
        self.render_page()

    def on_canvas_drag(self, event):
        if self.selected_index is None or self.drag_offset is None:
            return
        canvas_x = self.canvas.canvasx(event.x)
        canvas_y = self.canvas.canvasy(event.y)
        placement = self.placements[self.selected_index]
        placement["x"] = canvas_x / self.zoom - self.drag_offset[0]
        placement["y"] = canvas_y / self.zoom - self.drag_offset[1]
        self.clamp_placement(placement)
        self.render_page()

    def on_canvas_release(self, _event):
        self.drag_offset = None

    def clamp_placement(self, placement):
        page = self.document[placement["page"]]
        max_x = max(float(page.rect.width) - placement["w"], 0)
        max_y = max(float(page.rect.height) - placement["h"], 0)
        placement["x"] = min(max(placement["x"], 0), max_x)
        placement["y"] = min(max(placement["y"], 0), max_y)

    def delete_selected(self):
        if self.selected_index is None:
            return
        if 0 <= self.selected_index < len(self.placements):
            del self.placements[self.selected_index]
        self.selected_index = None
        self.render_page()

    def previous_page(self):
        if self.document and self.current_page > 0:
            self.current_page -= 1
            self.page_var.set(self.current_page + 1)
            self.render_page()

    def next_page(self):
        if self.document and self.current_page < self.document.page_count - 1:
            self.current_page += 1
            self.page_var.set(self.current_page + 1)
            self.render_page()

    def page_spin_changed(self):
        if not self.document:
            return
        page = max(1, min(int(self.page_var.get() or 1), self.document.page_count))
        self.current_page = page - 1
        self.page_var.set(page)
        self.render_page()

    def zoom_changed(self, _value=None):
        self.render_page()

    def bump_zoom(self, delta):
        value = max(75, min(250, int(self.zoom_var.get()) + int(delta)))
        self.zoom_var.set(value)
        self.render_page()

    def fit_width(self):
        if not self.document:
            return
        self.update_idletasks()
        page = self.document[self.current_page]
        available = max(300, self.canvas.winfo_width() - 30)
        zoom_percent = int((available / max(float(page.rect.width), 1)) * 100)
        self.zoom_var.set(max(75, min(250, zoom_percent)))
        self.render_page()

    def signature_width_changed(self, _value=None):
        if self.selected_index is not None:
            placement = self.placements[self.selected_index]
            signature_pil = self.get_signature_image(placement.get("signature_path"))
            if signature_pil is None:
                return
            center_x = placement["x"] + placement["w"] / 2
            center_y = placement["y"] + placement["h"] / 2
            width_pt = mm_to_points(self.signature_width_mm.get())
            ratio = signature_pil.height / max(signature_pil.width, 1)
            placement["w"], placement["h"] = width_pt, width_pt * ratio
            placement["x"] = center_x - placement["w"] / 2
            placement["y"] = center_y - placement["h"] / 2
            self.clamp_placement(placement)
        self.render_page()

    def on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def default_output_path(self):
        if not self.pdf_path:
            return ""
        root, ext = os.path.splitext(os.path.abspath(self.pdf_path))
        return f"{root}_SIGNED_PDFA{ext}"

    def write_signed_temp_pdf(self, temp_path):
        if not self.pdf_path:
            raise ValueError("Select a PDF first.")
        if not self.placements:
            raise ValueError("Click the PDF page to place at least one signature.")
        doc = fitz.open(self.pdf_path)
        try:
            for placement in self.placements:
                signature_path = placement.get("signature_path")
                if not signature_path or not os.path.exists(signature_path):
                    raise FileNotFoundError(f"Signature image unavailable: {signature_path}")
                rect = fitz.Rect(
                    placement["x"],
                    placement["y"],
                    placement["x"] + placement["w"],
                    placement["y"] + placement["h"],
                )
                doc[placement["page"]].insert_image(rect, filename=signature_path, overlay=True, keep_proportion=True)
            doc.save(temp_path, garbage=4, deflate=True)
        finally:
            doc.close()

    def convert_to_pdfa_readonly(self, source, destination):
        if not os.path.exists(GHOSTSCRIPT_PATH):
            raise RuntimeError(f"Ghostscript not found: {GHOSTSCRIPT_PATH}")
        if os.path.exists(destination):
            os.chmod(destination, stat.S_IWRITE | stat.S_IREAD)
        cmd = [
            GHOSTSCRIPT_PATH,
            "-dPDFA=2",
            "-dBATCH",
            "-dNOPAUSE",
            "-dSAFER",
            "-dPDFACompatibilityPolicy=1",
            "-sDEVICE=pdfwrite",
            "-sColorConversionStrategy=RGB",
            "-dEmbedAllFonts=true",
            "-dSubsetFonts=true",
            "-dAutoRotatePages=/None",
            f"-sOutputFile={destination}",
            source,
        ]
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode != 0 or not os.path.exists(destination):
            message = (result.stderr or result.stdout or "Unknown Ghostscript error").strip()
            raise RuntimeError(f"PDF/A conversion failed:\n{message}")
        os.chmod(destination, stat.S_IREAD)

    def save_signed_pdfa(self):
        if not self.pdf_path:
            messagebox.showinfo("PDF E-Sign", "Open a PDF first.")
            return
        output = filedialog.asksaveasfilename(
            title="Save Signed PDF/A Read-only Copy",
            initialfile=os.path.basename(self.default_output_path()),
            initialdir=os.path.dirname(self.pdf_path),
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
        )
        if not output:
            return
        if not output.lower().endswith(".pdf"):
            output += ".pdf"
        if os.path.abspath(output) == os.path.abspath(self.pdf_path):
            messagebox.showwarning(
                "PDF E-Sign",
                "Please save as a separate PDF/A copy, not over the original PDF.",
            )
            return
        output_root, output_ext = os.path.splitext(output)
        temp_signed = f"{output_root}_tmp_signed{output_ext or '.pdf'}"
        try:
            self.status_var.set("Writing signed temporary PDF...")
            self.update_idletasks()
            self.write_signed_temp_pdf(temp_signed)
            self.status_var.set("Converting to PDF/A read-only...")
            self.update_idletasks()
            self.convert_to_pdfa_readonly(temp_signed, output)
            self.status_var.set("Saved signed PDF/A read-only copy.")
            self.log_callback(f"[PDF E-SIGN] Saved signed PDF/A read-only: {output}")
            messagebox.showinfo("PDF E-Sign", f"Signed PDF/A read-only copy saved:\n\n{output}")
        except Exception as exc:
            self.status_var.set("Save failed.")
            messagebox.showerror("PDF E-Sign", str(exc))
            self.log_callback(f"[PDF E-SIGN ERROR] {exc}")
        finally:
            if os.path.exists(temp_signed):
                try:
                    os.remove(temp_signed)
                except Exception:
                    pass


class EDHClaimsGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("EDH Claims Automation System")
        self.geometry("1450x900")
        self.minsize(1280, 800)
        try:
            self.state("zoomed")
        except Exception:
            pass

        self.settings = load_json(CONFIG_FILE, DEFAULT_SETTINGS.copy())
        self.doctors = load_json(DOCTORS_FILE, DEFAULT_DOCTORS.copy())

        self.running_process = None
        self.start_time = None
        self.selected_doctor_index = None
        self.dashboard_auto_refresh_job = None
        self.auto_process_var = tk.BooleanVar(
            value=bool(self.settings.get("enable_auto_process", False))
        )
        self.auto_process_status_var = tk.StringVar(value="Auto Process: Off")
        self.auto_process_job = None
        self.auto_last_signature = None
        self.auto_stable_since = None
        self.auto_stable_seconds = 30
        self.auto_poll_ms = 5000
        self.auto_copy_xml_var = tk.BooleanVar(
            value=bool(self.settings.get("enable_auto_copy_xml", True))
        )
        self.auto_copy_xml_status_var = tk.StringVar(
            value=(
                "Auto Copy XML: Idle"
                if self.auto_copy_xml_var.get()
                else "Auto Copy XML: Off"
            )
        )
        self.auto_copy_xml_job = None
        self.auto_copy_xml_poll_ms = 10000
        self.auto_copy_xml_lock = threading.Lock()
        self.xml_auto_copy_service = self._create_xml_auto_copy_service()

        self.hbsys_status_var = tk.StringVar(value="● HBSys: Checking...")
        self.hbsys_status_label = None
        self.hbsys_status_job = None
        self.hbsys_status_poll_ms = 3000
        self.hbsys_warning_label = None

        self.setup_style()
        self.build_ui()
        self.refresh_dashboard_counts()
        self.schedule_dashboard_auto_refresh()
        self.schedule_auto_process_watcher()
        self.schedule_auto_copy_xml_watcher()
        self.check_hbsys_status_now()
        self.refresh_doctor_list()

    def setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        theme_name = self.settings.get("theme", "Light Blue")
        self.colors = THEMES.get(theme_name, THEMES["Light Blue"]).copy()
        self.configure(bg=self.colors["bg"])
        style.configure(".", font=("Segoe UI", 10), background=self.colors["bg"], foreground=self.colors["ink"])
        style.configure("TFrame", background=self.colors["bg"])
        style.configure("TNotebook", background=self.colors["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", font=("Segoe UI", 10, "bold"), padding=(18, 9), background="#E6EDF6", foreground=self.colors["muted"])
        style.map(
            "TNotebook.Tab",
            background=[("selected", self.colors["panel"]), ("active", self.colors["primary_soft"])],
            foreground=[("selected", self.colors["primary"]), ("active", self.colors["ink"])],
        )
        style.configure("TLabelframe", background=self.colors["panel"], bordercolor=self.colors["line"], relief="solid")
        style.configure("TLabelframe.Label", background=self.colors["panel"], foreground=self.colors["ink"], font=("Segoe UI", 10, "bold"))
        style.configure("Panel.TFrame", background=self.colors["panel"])
        style.configure("Header.TFrame", background=self.colors["bg"])
        style.configure("Title.TLabel", font=("Segoe UI", 21, "bold"), background=self.colors["bg"], foreground=self.colors["ink"])
        style.configure("Muted.TLabel", background=self.colors["bg"], foreground=self.colors["muted"])
        style.configure("PanelMuted.TLabel", background=self.colors["panel"], foreground=self.colors["muted"])
        style.configure("Alert.TLabel", background="#FFF7ED", foreground="#9A3412", font=("Segoe UI", 11, "bold"), padding=10)
        style.configure("StatValue.TLabel", background=self.colors["panel"], foreground=self.colors["primary"], font=("Segoe UI", 22, "bold"))
        style.configure("Big.TButton", font=("Segoe UI", 10, "bold"), padding=(8, 7), background="#E8EEF7", foreground=self.colors["ink"])
        style.map("Big.TButton", background=[("active", "#DCE8F8")])
        style.configure("Compact.TButton", font=("Segoe UI", 9, "bold"), padding=(5, 5), background="#E8EEF7", foreground=self.colors["ink"])
        style.map("Compact.TButton", background=[("active", "#DCE8F8")])
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(8, 7), foreground="#FFFFFF", background=self.colors["primary"])
        style.map("Primary.TButton", background=[("active", self.colors["primary_dark"])])
        style.configure("Warning.TButton", font=("Segoe UI", 10, "bold"), padding=(8, 7), foreground="#111827", background=self.colors["warning"])
        style.map("Warning.TButton", background=[("active", "#FBBF24")])
        style.configure("Danger.TButton", font=("Segoe UI", 10, "bold"), padding=9, foreground="#FFFFFF", background=self.colors["danger"])
        style.configure("Horizontal.TProgressbar", troughcolor="#E2E8F0", background=self.colors["primary"], bordercolor=self.colors["line"])

    def apply_theme(self):
        self.setup_style()
        try:
            self.configure(bg=self.colors["bg"])
        except Exception:
            pass
        if hasattr(self, "log_text"):
            self.log_text.configure(
                bg=self.colors["log_bg"],
                fg=self.colors["log_fg"],
                insertbackground="#FFFFFF",
            )
        if self.hbsys_status_label is not None:
            self.hbsys_status_label.configure(bg=self.colors["bg"])
        if self.hbsys_warning_label is not None:
            self.hbsys_warning_label.configure(bg=self.colors["panel"])

    def build_ui(self):
        main = ttk.Frame(self, padding=14)
        main.pack(fill="both", expand=True)

        header = ttk.Frame(main, style="Header.TFrame")
        header.pack(fill="x")

        title_block = ttk.Frame(header, style="Header.TFrame")
        title_block.pack(side="left")
        ttk.Label(title_block, text="EDH Claims Automation System", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            title_block,
            text="Production claims processing • review queue • PDF e-sign",
            style="Muted.TLabel",
        ).pack(anchor="w")
        self.hbsys_status_label = tk.Label(
            header,
            textvariable=self.hbsys_status_var,
            bg=self.colors["bg"],
            fg="#9CA3AF",
            font=("Segoe UI", 10, "bold"),
            cursor="hand2",
        )
        self.hbsys_status_label.pack(side="right", padx=(0, 12))
        self.hbsys_status_label.bind(
            "<Button-1>", lambda _event: self.check_hbsys_status_now()
        )
        ttk.Button(header, text="Save Settings", command=self.save_all).pack(side="right")
        ttk.Button(header, text="Refresh", command=self.refresh_dashboard_counts).pack(side="right", padx=(0, 8))

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True, pady=(14, 0))

        self.dashboard_tab = ttk.Frame(self.notebook, padding=12)
        self.folders_tab = ttk.Frame(self.notebook, padding=12)
        self.not_transmitted_tab = ttk.Frame(self.notebook, padding=4)
        self.doctor_tab = ttk.Frame(self.notebook, padding=12)
        self.esign_tab = ttk.Frame(self.notebook, padding=4)
        self.pdf_compressor_tab = ttk.Frame(self.notebook, padding=4)
        self.pdf_splitter_tab = ttk.Frame(self.notebook, padding=4)
        self.settings_tab = ttk.Frame(self.notebook, padding=12)
        self.about_tab = ttk.Frame(self.notebook, padding=18)

        self.notebook.add(self.dashboard_tab, text="Main Dashboard")
        self.notebook.add(self.folders_tab, text="Folders")
        self.notebook.add(self.not_transmitted_tab, text="Missing Date Fill Batches")
        self.notebook.add(self.doctor_tab, text="Doctor Manager")
        self.notebook.add(self.esign_tab, text="PDF E-Sign")
        self.notebook.add(self.pdf_compressor_tab, text="PDF Compress")
        self.notebook.add(self.pdf_splitter_tab, text="PDF Split")
        self.add_claims_upload_tab = ttk.Frame(self.notebook, padding=4)
        self.notebook.add(self.add_claims_upload_tab, text="Add Claims Upload")
        self.claim_attachments_tab = ttk.Frame(self.notebook, padding=4)
        self.notebook.add(self.claim_attachments_tab, text="Claim Attachments")
        self.notebook.add(self.settings_tab, text="Preferences")
        self.notebook.add(self.about_tab, text="About")

        self.build_dashboard_tab()
        self.build_folders_tab()
        self.build_not_transmitted_tab()
        self.build_doctor_tab()
        self.build_esign_tab()
        self.build_pdf_compressor_tab()
        self.build_pdf_splitter_tab()
        self.build_add_claims_upload_tab()
        self.build_claim_attachments_tab()
        self.build_settings_tab()
        self.build_about_tab()

    def build_dashboard_tab(self):
        top = ttk.Frame(self.dashboard_tab)
        top.pack(fill="x")

        self.stat_total_pdfs = tk.StringVar(value="0")
        self.stat_unknown = tk.StringVar(value="0")
        self.stat_patients = tk.StringVar(value="0")
        self.stat_failed_ocr = tk.StringVar(value="0")
        self.stat_review_needed = tk.StringVar(value="0")
        self.stat_patient_queue = tk.StringVar(value="0")
        self.stat_ready = tk.StringVar(value="0")
        self.stat_ready_review = tk.StringVar(value="0")
        self.stat_incomplete = tk.StringVar(value="0")
        self.stat_archived = tk.StringVar(value="0")
        self.review_alert_var = tk.StringVar(value="✅ No active patient review items.")

        stat_cards = [
            ("Total PDFs", self.stat_total_pdfs, lambda: self.open_folder(self.settings.get("scan_folder", ""))),
            ("Unknown Files", self.stat_unknown, lambda: self.open_folder(self.settings.get("scan_folder", ""))),
            ("Patient Folders", self.stat_patients, lambda: self.open_folder(self.settings.get("output_folder", ""))),
            ("Failed OCR", self.stat_failed_ocr, lambda: self.run_script("unknown_review_manager")),
            ("Need Review", self.stat_review_needed, lambda: self.run_script("unknown_review_manager")),
            ("Patient Queue", self.stat_patient_queue, self.open_patient_review_queue),
            ("READY", self.stat_ready, lambda: self.open_folder(os.path.join(BASE_DIR, "claims_checker_results", "READY"))),
            ("REVIEW", self.stat_ready_review, lambda: self.open_folder(os.path.join(BASE_DIR, "claims_checker_results", "READY_WITH_REVIEW"))),
            ("INCOMPLETE", self.stat_incomplete, lambda: self.open_folder(os.path.join(BASE_DIR, "claims_checker_results", "INCOMPLETE"))),
            ("ARCHIVED", self.stat_archived, lambda: self.open_folder(os.path.join(BASE_DIR, "claims_checker_results", "READY_ARCHIVED"))),
        ]
        for label, var, action in stat_cards:
            card = ttk.LabelFrame(top, text=label, padding=12)
            card.pack(side="left", fill="x", expand=True, padx=4)
            value_label = ttk.Label(card, textvariable=var, style="StatValue.TLabel")
            value_label.pack()
            self.make_stat_card_clickable(card, value_label, label, var, action)

        self.review_alert_label = ttk.Label(
            self.dashboard_tab,
            textvariable=self.review_alert_var,
            style="Alert.TLabel",
            anchor="w",
        )
        self.review_alert_label.pack(fill="x", pady=(10, 0))

        body = ttk.Frame(self.dashboard_tab)
        body.pack(fill="both", expand=True, pady=(12, 0))

        left = ttk.Frame(body, width=430)
        left.pack(side="left", fill="y", padx=(0, 12))
        left.pack_propagate(False)
        left_canvas = tk.Canvas(
            left,
            bg=self.colors["bg"],
            highlightthickness=0,
            borderwidth=0,
        )
        left_scroll = ttk.Scrollbar(left, orient="vertical", command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left_canvas.pack(side="left", fill="both", expand=True)
        left_scroll.pack(side="right", fill="y")
        left_content = ttk.Frame(left_canvas)
        left_window = left_canvas.create_window((0, 0), window=left_content, anchor="nw")

        def _sync_left_scroll(_event=None):
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))
            left_canvas.itemconfigure(left_window, width=left_canvas.winfo_width())

        left_content.bind("<Configure>", _sync_left_scroll)
        left_canvas.bind("<Configure>", _sync_left_scroll)
        left_canvas.bind(
            "<MouseWheel>",
            lambda event: left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units"),
        )

        right = ttk.Frame(body)
        right.pack(side="right", fill="both", expand=True)

        actions = ttk.LabelFrame(left_content, text="Production Actions", padding=10)
        actions.pack(fill="x", padx=(0, 4))

        def add_full_button(text, command, style="Big.TButton"):
            ttk.Button(
                actions,
                text=text,
                style=style,
                command=command,
            ).pack(fill="x", pady=2)

        def add_grid_button(parent, text, command, row, column):
            button = ttk.Button(
                parent,
                text=text,
                style="Compact.TButton",
                command=command,
            )
            button.grid(row=row, column=column, sticky="ew", padx=2, pady=2)
            return button

        ttk.Button(actions, text="Start Full Claims Processor", style="Primary.TButton",
                   command=lambda: self.run_script("claims_processor")).pack(fill="x", pady=2)
        add_full_button("Merge PDF", lambda: self.run_script("merge_pdf"))
        add_full_button("PDF/A Read Only", lambda: self.run_script("convert_pdfa"))

        auto_box = ttk.Frame(actions)
        auto_box.pack(fill="x", pady=(4, 2))
        ttk.Checkbutton(
            auto_box,
            text="Auto Process Scans",
            variable=self.auto_process_var,
            command=self.toggle_auto_process,
        ).pack(anchor="w")
        ttk.Label(
            auto_box,
            textvariable=self.auto_process_status_var,
            style="PanelMuted.TLabel",
            wraplength=365,
        ).pack(anchor="w", pady=(2, 0))
        ttk.Label(
            auto_box,
            textvariable=self.auto_copy_xml_status_var,
            style="PanelMuted.TLabel",
            wraplength=365,
        ).pack(anchor="w", pady=(2, 0))

        quick_actions = ttk.Frame(actions)
        quick_actions.pack(fill="x", pady=(4, 0))
        quick_actions.columnconfigure(0, weight=1, uniform="action_cols")
        quick_actions.columnconfigure(1, weight=1, uniform="action_cols")

        self.unknown_review_btn = ttk.Button(
            quick_actions,
            text="Unknown Review Manager (0)",
            style="Big.TButton",
            command=lambda: self.run_script("unknown_review_manager")
        )
        self.unknown_review_btn.grid(row=0, column=0, columnspan=2, sticky="ew", padx=2, pady=2)

        self.patient_review_queue_btn = ttk.Button(
            quick_actions,
            text="Patient Review Queue (0)",
            style="Big.TButton",
            command=self.open_patient_review_queue,
        )
        self.patient_review_queue_btn.grid(row=1, column=0, columnspan=2, sticky="ew", padx=2, pady=2)

        add_grid_button(quick_actions, "PDF E-Sign", lambda: self.notebook.select(self.esign_tab), 2, 0)
        self.date_fill_btn = add_grid_button(
            quick_actions,
            "Date Fill ABTC/Regular",
            lambda: self.run_script("date_fill_hbsys_testing"),
            2,
            1,
        )
        self.xml_clicker_btn = add_grid_button(
            quick_actions,
            "XML Clicker",
            lambda: self.run_script("xml_generator_clicker"),
            3,
            0,
        )
        add_grid_button(quick_actions, "Copy XML", self.copy_xml_button_clicked, 3, 1)
        add_grid_button(quick_actions, "Check Missing", lambda: self.run_script("claims_checker"), 4, 0)
        add_grid_button(quick_actions, "Fees Check", lambda: self.run_script("fees_checker"), 4, 1)
        add_grid_button(quick_actions, "Recheck INC", self.recheck_incomplete_claims, 5, 0)
        add_grid_button(
            quick_actions,
            "PDF Compress",
            lambda: self.notebook.select(self.pdf_compressor_tab),
            5,
            1,
        )
        add_grid_button(
            quick_actions,
            "PDF Split",
            lambda: self.notebook.select(self.pdf_splitter_tab),
            6,
            0,
        )
        add_grid_button(
            quick_actions,
            "Add Claims Upload",
            lambda: self.notebook.select(self.add_claims_upload_tab),
            6,
            1,
        )
        add_grid_button(
            quick_actions,
            "Claim Attachments",
            lambda: self.notebook.select(self.claim_attachments_tab),
            7,
            0,
        )

        self.hbsys_warning_label = tk.Label(
            actions,
            text="",
            bg=self.colors["panel"],
            fg="#DC2626",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
            justify="left",
            wraplength=380,
        )
        self.hbsys_warning_label.pack(fill="x", pady=(6, 0))

        ttk.Separator(actions).pack(fill="x", pady=6)

        bottom_actions = ttk.Frame(actions)
        bottom_actions.pack(fill="x")
        bottom_actions.columnconfigure(0, weight=1, uniform="bottom_action_cols")
        bottom_actions.columnconfigure(1, weight=1, uniform="bottom_action_cols")
        add_grid_button(bottom_actions, "Stop Process", self.stop_process, 0, 0)
        add_grid_button(bottom_actions, "Archive Claims", self.archive_transmitted_claims, 0, 1)

        
        
        note = ttk.LabelFrame(left_content, text="Work Tip", padding=10)
        note.pack(fill="x", padx=(0, 4), pady=(10, 0))
        ttk.Label(
            note,
            text="Copy PDFs into scans folder, run processor, then watch Patient Queue and latest output here.",
            wraplength=365,
            style="PanelMuted.TLabel",
        ).pack(anchor="w")

        log_card = ttk.LabelFrame(right, text="Live Processing Logs", padding=12)
        log_card.pack(fill="both", expand=True)

        self.progress = ttk.Progressbar(log_card, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 8))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(log_card, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).pack(anchor="w")

        log_frame = ttk.Frame(log_card)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))

        self.log_text = tk.Text(
            log_frame,
            bg=self.colors["log_bg"],
            fg=self.colors["log_fg"],
            insertbackground="#ffffff",
            font=("Cascadia Mono", 10),
            wrap="word",
            relief="flat",
            padx=12,
            pady=10,
        )
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=scroll.set)

        log_buttons = ttk.Frame(right)
        log_buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(log_buttons, text="Clear Logs", command=self.clear_logs).pack(side="left")
        ttk.Button(log_buttons, text="Save Logs", command=self.save_logs).pack(side="left", padx=(8, 0))

        self.log("EDH Claims GUI ready.")

    def make_stat_card_clickable(self, card, value_label, label, variable, action):
        def on_click(_event=None):
            self.open_dashboard_stat(label, variable, action)

        for widget in (card, value_label):
            widget.bind("<Button-1>", on_click)
            try:
                widget.configure(cursor="hand2")
            except Exception:
                pass

    def open_dashboard_stat(self, label, variable, action):
        try:
            count = int(str(variable.get()).strip() or "0")
        except ValueError:
            count = 0
        if count <= 0:
            self.status_var.set(f"{label}: 0 item(s)")
            return
        self.log(f"[DASHBOARD] Opening {label} ({count})")
        action()


    def build_folders_tab(self):
        left = ttk.Frame(self.folders_tab)
        left.pack(fill="both", expand=True)

        claims = ttk.LabelFrame(left, text="Claims Folders", padding=12)
        claims.pack(fill="x", pady=8)

        ttk.Button(claims, text="Open READY",
                   command=lambda: self.open_folder(os.path.join(BASE_DIR, "claims_checker_results", "READY"))).pack(fill="x", pady=4)

        ttk.Button(claims, text="Open READY WITH REVIEW",
                   command=lambda: self.open_folder(os.path.join(BASE_DIR, "claims_checker_results", "READY_WITH_REVIEW"))).pack(fill="x", pady=4)

        ttk.Button(claims, text="Open INCOMPLETE",
                   command=lambda: self.open_folder(os.path.join(BASE_DIR, "claims_checker_results", "INCOMPLETE"))).pack(fill="x", pady=4)

        ttk.Button(claims, text="Open ARCHIVED",
                   command=lambda: self.open_folder(os.path.join(BASE_DIR, "claims_checker_results", "READY_ARCHIVED"))).pack(fill="x", pady=4)

        processing = ttk.LabelFrame(left, text="Processing Folders", padding=12)
        processing.pack(fill="x", pady=8)

        ttk.Button(processing, text="Open Scans Folder",
                   command=lambda: self.open_folder(self.settings["scan_folder"])).pack(fill="x", pady=4)
        ttk.Button(processing, text="Open Output Folder",
                   command=lambda: self.open_folder(self.settings["output_folder"])).pack(fill="x", pady=4)
        ttk.Button(processing, text="Open Backup Originals",
                   command=lambda: self.open_folder(self.settings["backup_folder"])).pack(fill="x", pady=4)
        ttk.Button(processing, text="Open Signatures Folder",
                   command=lambda: self.open_folder(self.settings["signature_folder"])).pack(fill="x", pady=4)

        reports = ttk.LabelFrame(left, text="Reports", padding=12)
        reports.pack(fill="x", pady=8)

        ttk.Button(reports, text="Open Claims Checker CSV",
                   command=lambda: self.open_file(os.path.join(BASE_DIR, "claims_checker_report.csv"))).pack(fill="x", pady=4)

        ttk.Button(reports, text="Open Claims Checker Log",
                   command=lambda: self.open_file(os.path.join(BASE_DIR, "claims_checker_report.log"))).pack(fill="x", pady=4)


    def build_not_transmitted_tab(self):
        frame = NotTransmittedBatchesFrame(
            self.not_transmitted_tab,
            settings_getter=lambda: self.settings,
            log_callback=self.log,
        )
        frame.pack(fill="both", expand=True)


    def build_add_claims_upload_tab(self):
        frame = AddClaimsUploadFrame(
            self.add_claims_upload_tab,
            settings_getter=lambda: self.settings,
            log_callback=self.log,
        )
        frame.pack(fill="both", expand=True)


    def build_claim_attachments_tab(self):
        frame = ClaimAttachmentsFrame(
            self.claim_attachments_tab,
            settings_getter=lambda: self.settings,
            log_callback=self.log,
        )
        frame.pack(fill="both", expand=True)


    def build_doctor_tab(self):
        container = ttk.Frame(self.doctor_tab)
        container.pack(fill="both", expand=True)

        left = ttk.LabelFrame(container, text="Doctors List", padding=10, width=360)
        left.pack(side="left", fill="y", padx=(0, 12))
        left.pack_propagate(False)

        self.doctor_listbox = tk.Listbox(left, height=8, font=("Segoe UI", 10))
        self.doctor_listbox.pack(fill="both", expand=True)
        self.doctor_listbox.bind("<<ListboxSelect>>", self.on_doctor_select)

        doc_buttons = ttk.Frame(left)
        doc_buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(doc_buttons, text="+ Add", command=self.add_doctor).pack(side="left", fill="x", expand=True, padx=2)
        ttk.Button(doc_buttons, text="Delete", command=self.delete_doctor).pack(side="left", fill="x", expand=True, padx=2)

        right = ttk.Frame(container)
        right.pack(side="right", fill="both", expand=True)

        form = ttk.LabelFrame(left, text="Doctor Configuration", padding=12)
        form.pack(fill="x", pady=(12, 0))

        self.doc_name = tk.StringVar()
        self.doc_aliases = tk.StringVar()
        self.doc_signature = tk.StringVar()
        self.doc_enabled = tk.BooleanVar(value=True)
        self.doc_part_iv_x = tk.StringVar()
        self.doc_part_iv_y = tk.StringVar()
        self.doc_part_v_x = tk.StringVar()
        self.doc_part_v_y = tk.StringVar()
        self.doc_max_w = tk.StringVar()
        self.doc_max_h = tk.StringVar()
        self.doc_cf2_x = tk.StringVar()
        self.doc_cf2_y = tk.StringVar()
        self.doc_cf2_max_w = tk.StringVar()
        self.doc_cf2_max_h = tk.StringVar()
        self.doc_cf2_hci_x = tk.StringVar()
        self.doc_cf2_hci_y = tk.StringVar()
        self.doc_cf2_hci_max_w = tk.StringVar()
        self.doc_cf2_hci_max_h = tk.StringVar()

        self.form_entry(form, "Doctor Name", self.doc_name, 0)
        self.form_entry(form, "Aliases", self.doc_aliases, 1)

        sig_row = ttk.Frame(form)
        sig_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=6)
        sig_row.columnconfigure(1, weight=1)
        ttk.Label(sig_row, text="Signature PNG", width=18).grid(row=0, column=0, sticky="w")
        ttk.Entry(sig_row, textvariable=self.doc_signature).grid(row=0, column=1, sticky="ew")
        ttk.Button(sig_row, text="Browse", command=self.browse_signature).grid(row=0, column=2, padx=(8, 0))

        coord = ttk.LabelFrame(left, text="CSF Signature Position", padding=12)
        coord.pack(fill="x", pady=(12, 0))
        ttk.Label(coord, text="Part IV X").grid(row=0, column=0, padx=4, pady=4)
        ttk.Entry(coord, textvariable=self.doc_part_iv_x, width=12).grid(row=0, column=1, padx=4, pady=4)
        ttk.Label(coord, text="Part IV Y").grid(row=0, column=2, padx=4, pady=4)
        ttk.Entry(coord, textvariable=self.doc_part_iv_y, width=12).grid(row=0, column=3, padx=4, pady=4)
        ttk.Label(coord, text="Part V X").grid(row=1, column=0, padx=4, pady=4)
        ttk.Entry(coord, textvariable=self.doc_part_v_x, width=12).grid(row=1, column=1, padx=4, pady=4)
        ttk.Label(coord, text="Part V Y").grid(row=1, column=2, padx=4, pady=4)
        ttk.Entry(coord, textvariable=self.doc_part_v_y, width=12).grid(row=1, column=3, padx=4, pady=4)

        ttk.Label(coord, text="Max W").grid(row=2, column=0, padx=4, pady=4)
        ttk.Entry(coord, textvariable=self.doc_max_w, width=12).grid(row=2, column=1, padx=4, pady=4)
        ttk.Label(coord, text="Max H").grid(row=2, column=2, padx=4, pady=4)
        ttk.Entry(coord, textvariable=self.doc_max_h, width=12).grid(row=2, column=3, padx=4, pady=4)

        ttk.Checkbutton(coord, text="Enabled", variable=self.doc_enabled).grid(row=3, column=0, sticky="w", pady=(8, 0))

        cf2_coord = ttk.LabelFrame(left, text="CF2 Signature Position", padding=12)
        cf2_coord.pack(fill="x", pady=(12, 0))

        ttk.Label(cf2_coord, text="CF2 Doctor X").grid(row=0, column=0, padx=4, pady=4)
        ttk.Entry(cf2_coord, textvariable=self.doc_cf2_x, width=12).grid(row=0, column=1, padx=4, pady=4)
        ttk.Label(cf2_coord, text="CF2 Doctor Y").grid(row=0, column=2, padx=4, pady=4)
        ttk.Entry(cf2_coord, textvariable=self.doc_cf2_y, width=12).grid(row=0, column=3, padx=4, pady=4)

        ttk.Label(cf2_coord, text="CF2 Doctor W").grid(row=1, column=0, padx=4, pady=4)
        ttk.Entry(cf2_coord, textvariable=self.doc_cf2_max_w, width=12).grid(row=1, column=1, padx=4, pady=4)
        ttk.Label(cf2_coord, text="CF2 Doctor H").grid(row=1, column=2, padx=4, pady=4)
        ttk.Entry(cf2_coord, textvariable=self.doc_cf2_max_h, width=12).grid(row=1, column=3, padx=4, pady=4)

        ttk.Label(cf2_coord, text="CF2 HCI X").grid(row=2, column=0, padx=4, pady=4)
        ttk.Entry(cf2_coord, textvariable=self.doc_cf2_hci_x, width=12).grid(row=2, column=1, padx=4, pady=4)
        ttk.Label(cf2_coord, text="CF2 HCI Y").grid(row=2, column=2, padx=4, pady=4)
        ttk.Entry(cf2_coord, textvariable=self.doc_cf2_hci_y, width=12).grid(row=2, column=3, padx=4, pady=4)

        ttk.Label(cf2_coord, text="CF2 HCI W").grid(row=3, column=0, padx=4, pady=4)
        ttk.Entry(cf2_coord, textvariable=self.doc_cf2_hci_max_w, width=12).grid(row=3, column=1, padx=4, pady=4)
        ttk.Label(cf2_coord, text="CF2 HCI H").grid(row=3, column=2, padx=4, pady=4)
        ttk.Entry(cf2_coord, textvariable=self.doc_cf2_hci_max_h, width=12).grid(row=3, column=3, padx=4, pady=4)

        actions = ttk.LabelFrame(right, text="Actions", padding=8)

        save_row = ttk.Frame(actions)
        save_row.pack(fill="x")
        ttk.Button(save_row, text="Save Doctor", command=self.save_current_doctor).pack(side="left")
        ttk.Button(save_row, text="Save Doctors JSON", command=self.save_doctors).pack(side="left", padx=(8, 0))
        ttk.Button(save_row, text="Refresh Signature Preview", command=self.update_live_signature_preview).pack(side="left", padx=(8, 0))

        coord_row = ttk.Frame(actions)
        coord_row.pack(fill="x", pady=(6, 0))
        ttk.Button(coord_row, text="Use Preview Coords for Part IV", command=self.use_preview_coords_for_part_iv).pack(side="left")
        ttk.Button(coord_row, text="Use Preview for CF2 Doctor", command=self.use_preview_coords_for_cf2_doctor).pack(side="left", padx=(8, 0))
        ttk.Button(coord_row, text="Use Preview for CF2 HCI", command=self.use_preview_coords_for_cf2_hci).pack(side="left", padx=(8, 0))
        ttk.Button(coord_row, text="Test Signature Overlay", command=self.test_signature_overlay).pack(side="left", padx=(8, 0))

        preview = ttk.LabelFrame(right, text="PDF Preview + Drag Coordinates", padding=12)
        preview.pack(fill="both", expand=True, pady=(12, 0))

        self.preview_frame = DoctorPreviewFrame(preview)
        self.preview_frame.pack(fill="both", expand=True)

        actions.pack(fill="x", pady=(10, 0))

    def build_esign_tab(self):
        frame = PDFESignFrame(
            self.esign_tab,
            settings_getter=lambda: self.settings,
            log_callback=self.log,
        )
        frame.pack(fill="both", expand=True)

    def build_pdf_compressor_tab(self):
        frame = PDFCompressorFrame(
            self.pdf_compressor_tab,
            log_callback=self.log,
        )
        frame.pack(fill="both", expand=True)

    def build_pdf_splitter_tab(self):
        frame = PdfSplitterFrame(
            self.pdf_splitter_tab,
            log_callback=self.log,
        )
        frame.pack(fill="both", expand=True)

    def form_entry(self, parent, label, variable, row):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label, width=18).grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)

    def build_settings_tab(self):
        form = ttk.LabelFrame(self.settings_tab, text="Preferences", padding=12)
        form.pack(fill="x", pady=(12, 0))

        self.var_base_dir = tk.StringVar(value=self.settings["base_dir"])
        self.var_scan_folder = tk.StringVar(value=self.settings["scan_folder"])
        self.var_output_folder = tk.StringVar(value=self.settings["output_folder"])
        self.var_backup_folder = tk.StringVar(value=self.settings["backup_folder"])
        self.var_review_staging_folder = tk.StringVar(
            value=self.settings.get(
                "review_staging_folder",
                os.path.join(BASE_DIR, "review_staging"),
            )
        )
        self.var_signature_folder = tk.StringVar(value=self.settings["signature_folder"])
        self.var_xml_source_folder = tk.StringVar(value=self.settings.get("xml_source_folder", r"C:\Shared Folder\FTPURL"))
        self.var_theme = tk.StringVar(value=self.settings.get("theme", "Light Blue"))

        folder_rows = [
            ("Base Folder", self.var_base_dir),
            ("Scan Folder", self.var_scan_folder),
            ("Output Folder", self.var_output_folder),
            ("Backup Folder", self.var_backup_folder),
            ("Review Staging", self.var_review_staging_folder),
            ("Signatures Folder", self.var_signature_folder),
            ("XML Source Folder", self.var_xml_source_folder),
        ]
        for row, (label, var) in enumerate(folder_rows):
            self.setting_folder_row(form, label, var, row)

        theme_row = len(folder_rows)
        ttk.Label(form, text="Theme", width=18).grid(row=theme_row, column=0, sticky="w", pady=6)
        ttk.Combobox(
            form,
            textvariable=self.var_theme,
            values=tuple(THEMES.keys()),
            state="readonly",
            width=24,
        ).grid(row=theme_row, column=1, sticky="w", pady=6)
        ttk.Button(
            form,
            text="Apply Theme",
            command=self.preview_selected_theme,
        ).grid(row=theme_row, column=2, padx=(8, 0), pady=6)

        checks = ttk.LabelFrame(self.settings_tab, text="Feature Toggles", padding=12)
        checks.pack(fill="x", pady=(12, 0))
        auto_default = bool(self.settings.get("enable_auto_sign", True))
        self.var_auto_sign_csf = tk.BooleanVar(value=bool(self.settings.get("enable_auto_sign_csf", auto_default)))
        self.var_auto_sign_cf2 = tk.BooleanVar(value=bool(self.settings.get("enable_auto_sign_cf2", auto_default)))
        self.var_date_signed = tk.BooleanVar(value=self.settings["enable_date_signed"])
        self.var_backup = tk.BooleanVar(value=self.settings["enable_backup"])
        self.var_debug_logs = tk.BooleanVar(value=self.settings["show_debug_logs"])
        self.var_auto_process = tk.BooleanVar(
            value=bool(self.settings.get("enable_auto_process", False))
        )
        self.var_auto_copy_xml = tk.BooleanVar(
            value=bool(self.settings.get("enable_auto_copy_xml", True))
        )
        ttk.Checkbutton(checks, text="Enable Auto Sign CSF", variable=self.var_auto_sign_csf).pack(anchor="w")
        ttk.Checkbutton(checks, text="Enable Auto Sign CF2", variable=self.var_auto_sign_cf2).pack(anchor="w")
        ttk.Checkbutton(checks, text="Enable Date Signed", variable=self.var_date_signed).pack(anchor="w")
        ttk.Checkbutton(checks, text="Backup Original Scans", variable=self.var_backup).pack(anchor="w")
        ttk.Checkbutton(checks, text="Show Debug Logs", variable=self.var_debug_logs).pack(anchor="w")
        ttk.Checkbutton(checks, text="Auto Process Scans", variable=self.var_auto_process).pack(anchor="w")
        ttk.Checkbutton(
            checks,
            text="Enable Auto Copy XML",
            variable=self.var_auto_copy_xml,
        ).pack(anchor="w")
        ttk.Button(self.settings_tab, text="Save Preferences", command=self.save_settings).pack(anchor="e", pady=(12, 0))

    def build_about_tab(self):
        container = ttk.Frame(self.about_tab)
        container.pack(fill="both", expand=True)

        title = ttk.Label(
            container,
            text="EDH Claims Automation System",
            font=("Segoe UI", 20, "bold"),
        )
        title.pack(anchor="w", pady=(0, 4))

        ttk.Label(
            container,
            text="Production claims processing, review queue, PDF e-sign, XML tools, and HBSys helpers.",
            style="Muted.TLabel",
            wraplength=900,
        ).pack(anchor="w", pady=(0, 14))

        info = ttk.LabelFrame(container, text="System Information", padding=12)
        info.pack(fill="x", pady=(0, 12))

        self.about_version_var = tk.StringVar(value=self.get_app_version_label())
        self.about_nas_var = tk.StringVar(value="Checking when requested...")
        self.about_update_status_var = tk.StringVar(
            value="Click Check for Updates to compare this PC with the NAS code manifest."
        )

        self._about_info_row(info, "Installed Version", self.about_version_var, 0)
        self._about_info_row(info, "Update Source", self.about_nas_var, 1)

        update_box = ttk.LabelFrame(container, text="Updates", padding=12)
        update_box.pack(fill="x", pady=(0, 12))

        ttk.Label(
            update_box,
            textvariable=self.about_update_status_var,
            wraplength=1000,
            justify="left",
        ).pack(anchor="w", fill="x", pady=(0, 10))

        button_row = ttk.Frame(update_box)
        button_row.pack(anchor="w")
        ttk.Button(
            button_row,
            text="Check for Updates",
            command=self.check_for_updates_clicked,
        ).pack(side="left", padx=(0, 8))
        self.about_update_btn = ttk.Button(
            button_row,
            text="Update Now",
            command=self.apply_update_clicked,
            state="disabled",
        )
        self.about_update_btn.pack(side="left", padx=(0, 8))
        ttk.Button(
            button_row,
            text="Open Setup Guide",
            command=lambda: self.open_file(os.path.join(BASE_DIR, "SETUP_AND_TRANSFER.md")),
        ).pack(side="left")

        note = ttk.LabelFrame(container, text="Important Update Rule", padding=12)
        note.pack(fill="x")
        ttk.Label(
            note,
            text=(
                "Updates are detected from manifest.json code hashes only. "
                "Patient/user folders such as scans, output, backup_originals, review_staging, "
                "claims_checker_results, merge_input, merge_output, pdfs, "
                "signatures, logs, and databases "
                "are excluded so new patient folders will not trigger false updates."
            ),
            wraplength=1000,
            justify="left",
        ).pack(anchor="w")

    def _about_info_row(self, parent, label, variable, row):
        ttk.Label(parent, text=label, width=20).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Label(parent, textvariable=variable, wraplength=900).grid(
            row=row, column=1, sticky="w", pady=4
        )
        parent.columnconfigure(1, weight=1)

    def get_app_version_label(self):
        version_path = os.path.join(BASE_DIR, "version.txt")
        try:
            with open(version_path, "r", encoding="utf-8") as handle:
                return f"v{handle.read().strip() or '0.0'}"
        except OSError:
            return "v0.0"

    def check_for_updates_clicked(self):
        self.about_update_btn.configure(state="disabled")
        self.about_update_status_var.set("Checking NAS update manifest...")

        def worker():
            try:
                from updater import check_update_available

                result = check_update_available()
            except Exception as exc:  # noqa: BLE001 - status display only.
                result = {
                    "available": False,
                    "nas_reachable": False,
                    "error": str(exc),
                    "local_version": 0.0,
                    "network_version": 0.0,
                    "changed_files": [],
                    "network_source": "",
                }
            self.after(0, lambda: self.on_update_check_done(result))

        threading.Thread(target=worker, daemon=True).start()

    def on_update_check_done(self, result):
        source = result.get("network_source", "")
        if source:
            self.about_nas_var.set(source)

        if not result.get("nas_reachable", False):
            self.about_update_status_var.set(
                "NAS update source is not reachable. Check network connection or update_config.json."
            )
            return

        local_version = result.get("local_version", 0.0)
        network_version = result.get("network_version", 0.0)
        changed_files = result.get("changed_files", [])

        if result.get("available", False):
            self.about_update_btn.configure(state="normal")
            self.about_update_status_var.set(
                f"Update available. Installed: v{local_version} | NAS: v{network_version} | "
                f"Changed code/doc files: {len(changed_files)}"
            )
        else:
            self.about_update_status_var.set(f"Up to date. Installed: v{local_version}")

    def apply_update_clicked(self):
        if not messagebox.askyesno(
            "Confirm Update",
            "Update this program from the NAS code manifest?\n\n"
            "Patient folders and local settings will not be overwritten.",
        ):
            return

        self.about_update_btn.configure(state="disabled")
        self.about_update_status_var.set("Applying update from NAS...")

        def worker():
            try:
                from updater import apply_update

                success, message = apply_update()
            except Exception as exc:  # noqa: BLE001 - status display only.
                success, message = False, str(exc)
            self.after(0, lambda: self.on_apply_update_done(success, message))

        threading.Thread(target=worker, daemon=True).start()

    def on_apply_update_done(self, success, message):
        self.about_update_status_var.set(message)
        if not success:
            messagebox.showwarning("Update Failed", message)
            return
        if messagebox.askyesno("Update Complete", f"{message}\n\nRestart the app now?"):
            try:
                from updater import restart_app

                restart_app()
            except Exception as exc:  # noqa: BLE001
                messagebox.showwarning("Restart Failed", str(exc))

    def setting_folder_row(self, parent, label, variable, row):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text=label, width=18).grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Button(parent, text="Browse", command=lambda: self.browse_folder(variable)).grid(row=row, column=2, padx=(8, 0))

    def refresh_doctor_list(self):
        self.doctor_listbox.delete(0, "end")
        for doc in self.doctors:
            status = "ON" if doc.get("enabled") else "OFF"
            self.doctor_listbox.insert("end", f"[{status}] {doc.get('name', 'Unnamed Doctor')}")

    def update_live_signature_preview(self):
        """
        Load selected doctor's signature into the PDF preview area.
        """
        if not hasattr(self, "preview_frame"):
            return

        sig_name = self.doc_signature.get().strip()

        if not sig_name:
            self.preview_frame.set_signature_preview(None)
            return

        # If full path was saved, use it. Otherwise use signatures folder.
        if os.path.isabs(sig_name):
            sig_path = sig_name
        else:
            sig_path = os.path.join(self.settings.get("signature_folder", ""), sig_name)

        self.preview_frame.set_signature_preview(sig_path)


    def on_doctor_select(self, event=None):
        sel = self.doctor_listbox.curselection()
        if not sel:
            return
        self.selected_doctor_index = sel[0]
        doc = self.doctors[self.selected_doctor_index]
        self.doc_name.set(doc.get("name", ""))
        self.doc_aliases.set(doc.get("aliases", ""))
        self.doc_signature.set(doc.get("signature_file", ""))
        self.doc_part_iv_x.set(doc.get("part_iv_x", ""))
        self.doc_part_iv_y.set(doc.get("part_iv_y", ""))
        self.doc_part_v_x.set(doc.get("part_v_x", ""))
        self.doc_part_v_y.set(doc.get("part_v_y", ""))
        self.doc_max_w.set(doc.get("max_w", "130"))
        self.doc_max_h.set(doc.get("max_h", "35"))
        self.doc_cf2_x.set(doc.get("cf2_x", ""))
        self.doc_cf2_y.set(doc.get("cf2_y", ""))
        self.doc_cf2_max_w.set(doc.get("cf2_max_w", "120"))
        self.doc_cf2_max_h.set(doc.get("cf2_max_h", "25"))
        self.doc_cf2_hci_x.set(doc.get("cf2_hci_x", ""))
        self.doc_cf2_hci_y.set(doc.get("cf2_hci_y", ""))
        self.doc_cf2_hci_max_w.set(doc.get("cf2_hci_max_w", "135"))
        self.doc_cf2_hci_max_h.set(doc.get("cf2_hci_max_h", "30"))
        self.doc_enabled.set(bool(doc.get("enabled", True)))

        self.update_live_signature_preview()

    def add_doctor(self):
        self.doctors.append({
            "name": "NEW DOCTOR",
            "aliases": "",
            "signature_file": "",
            "part_iv_x": "0",
            "part_iv_y": "0",
            "part_v_x": "0",
            "part_v_y": "0",
            "max_w": "130",
            "max_h": "35",
            "cf2_x": "",
            "cf2_y": "",
            "cf2_max_w": "120",
            "cf2_max_h": "25",
            "cf2_hci_x": "",
            "cf2_hci_y": "",
            "cf2_hci_max_w": "135",
            "cf2_hci_max_h": "30",
            "enabled": True,
        })
        self.refresh_doctor_list()
        self.doctor_listbox.selection_clear(0, "end")
        self.doctor_listbox.selection_set(len(self.doctors) - 1)
        self.on_doctor_select()

    def delete_doctor(self):
        if self.selected_doctor_index is None:
            messagebox.showinfo("Delete Doctor", "Select a doctor first.")
            return
        doc = self.doctors[self.selected_doctor_index]
        if messagebox.askyesno("Delete Doctor", f"Delete doctor?\n\n{doc.get('name')}"):
            self.doctors.pop(self.selected_doctor_index)
            self.selected_doctor_index = None
            self.refresh_doctor_list()
            self.doc_name.set("")
            self.doc_aliases.set("")
            self.doc_signature.set("")
            self.doc_max_w.set("")
            self.doc_max_h.set("")
            self.doc_cf2_x.set("")
            self.doc_cf2_y.set("")
            self.doc_cf2_max_w.set("")
            self.doc_cf2_max_h.set("")
            self.doc_cf2_hci_x.set("")
            self.doc_cf2_hci_y.set("")
            self.doc_cf2_hci_max_w.set("")
            self.doc_cf2_hci_max_h.set("")

    def save_current_doctor(self):
        if self.selected_doctor_index is None:
            messagebox.showinfo("Save Doctor", "Select or add a doctor first.")
            return
        self.doctors[self.selected_doctor_index] = {
            "name": self.doc_name.get().strip(),
            "aliases": self.doc_aliases.get().strip(),
            "signature_file": self.doc_signature.get().strip(),
            "part_iv_x": self.doc_part_iv_x.get().strip(),
            "part_iv_y": self.doc_part_iv_y.get().strip(),
            "part_v_x": self.doc_part_v_x.get().strip(),
            "part_v_y": self.doc_part_v_y.get().strip(),
            "max_w": self.doc_max_w.get().strip(),
            "max_h": self.doc_max_h.get().strip(),
            "cf2_x": self.doc_cf2_x.get().strip(),
            "cf2_y": self.doc_cf2_y.get().strip(),
            "cf2_max_w": self.doc_cf2_max_w.get().strip(),
            "cf2_max_h": self.doc_cf2_max_h.get().strip(),
            "cf2_hci_x": self.doc_cf2_hci_x.get().strip(),
            "cf2_hci_y": self.doc_cf2_hci_y.get().strip(),
            "cf2_hci_max_w": self.doc_cf2_hci_max_w.get().strip(),
            "cf2_hci_max_h": self.doc_cf2_hci_max_h.get().strip(),
            "enabled": bool(self.doc_enabled.get()),
        }
        self.save_doctors()
        self.refresh_doctor_list()
        messagebox.showinfo("Save Doctor", "Doctor saved.")

    def browse_signature(self):
        path = filedialog.askopenfilename(title="Select Signature PNG",
                                          filetypes=[("PNG files", "*.png"), ("All files", "*.*")])
        if not path:
            return
        sig_folder = self.settings["signature_folder"]
        os.makedirs(sig_folder, exist_ok=True)
        filename = os.path.basename(path)
        dst = os.path.join(sig_folder, filename)
        if os.path.abspath(path) != os.path.abspath(dst):
            shutil.copy2(path, dst)
        self.doc_signature.set(filename)
        self.update_live_signature_preview()
        messagebox.showinfo("Signature", f"Signature copied to:\n{dst}")

    def use_preview_coords_for_part_iv(self):
        """
        Copy current preview PDF coordinates into selected doctor's Part IV fields.
        PDF X/Y/W/H are the same coordinate system used by the bot.
        """
        if not hasattr(self, "preview_frame"):
            messagebox.showerror("Preview", "Preview frame not available.")
            return

        coords = self.preview_frame.get_pdf_box_coordinates()

        if not coords:
            messagebox.showinfo("Preview", "Open a PDF and position the red box first.")
            return

        x, y, w, h = coords

        self.doc_part_iv_x.set(str(x))
        self.doc_part_iv_y.set(str(y))
        self.doc_max_w.set(str(w))
        self.doc_max_h.set(str(h))

        messagebox.showinfo(
            "Preview Coordinates Applied",
            f"Applied to Part IV:\\n\\nX: {x}\\nY: {y}\\nMax W: {w}\\nMax H: {h}\\n\\nClick Save Doctor to save."
        )

    def use_preview_coords_for_cf2_doctor(self):
        """
        Copy preview coordinates into the CF2 doctor/professional signature fields.
        This is the upper-right CF2 signature below accreditation/name.
        """
        if not hasattr(self, "preview_frame"):
            messagebox.showerror("Preview", "Preview frame not available.")
            return

        coords = self.preview_frame.get_pdf_box_coordinates()

        if not coords:
            messagebox.showinfo("Preview", "Open a PDF and position the red box first.")
            return

        x, y, w, h = coords

        self.doc_cf2_x.set(str(x))
        self.doc_cf2_y.set(str(y))
        self.doc_cf2_max_w.set(str(w))
        self.doc_cf2_max_h.set(str(h))

        messagebox.showinfo(
            "Preview Coordinates Applied",
            f"Applied to CF2 Doctor:\\n\\nX: {x}\\nY: {y}\\nMax W: {w}\\nMax H: {h}\\n\\nClick Save Doctor to save."
        )

    def use_preview_coords_for_cf2_hci(self):
        """
        Copy preview coordinates into the CF2 Authorized HCI Representative fields.
        Configure this on the Chief of Hospital/Rhoda doctor row.
        """
        if not hasattr(self, "preview_frame"):
            messagebox.showerror("Preview", "Preview frame not available.")
            return

        coords = self.preview_frame.get_pdf_box_coordinates()

        if not coords:
            messagebox.showinfo("Preview", "Open a PDF and position the red box first.")
            return

        x, y, w, h = coords

        self.doc_cf2_hci_x.set(str(x))
        self.doc_cf2_hci_y.set(str(y))
        self.doc_cf2_hci_max_w.set(str(w))
        self.doc_cf2_hci_max_h.set(str(h))

        messagebox.showinfo(
            "Preview Coordinates Applied",
            f"Applied to CF2 HCI Representative:\\n\\nX: {x}\\nY: {y}\\nMax W: {w}\\nMax H: {h}\\n\\nClick Save Doctor to save."
        )


    def test_signature_overlay(self):
        messagebox.showinfo("Test Signature Overlay", "Placeholder muna.\nNext: actual test overlay sa sample CSF PDF.")

    def browse_folder(self, variable):
        folder = filedialog.askdirectory(initialdir=variable.get() or "C:\\")
        if folder:
            variable.set(folder)

    def preview_selected_theme(self):
        self.settings["theme"] = self.var_theme.get().strip() or "Light Blue"
        self.apply_theme()

    def save_settings(self, show_message=True):
        auto_process_enabled = bool(
            self.auto_process_var.get()
            if hasattr(self, "auto_process_var")
            else getattr(self, "var_auto_process", tk.BooleanVar(value=False)).get()
        )
        auto_copy_xml_enabled = bool(
            self.var_auto_copy_xml.get()
            if hasattr(self, "var_auto_copy_xml")
            else self.auto_copy_xml_var.get()
        )
        self.settings = {
            "base_dir": normalize_windows_path(self.var_base_dir.get()),
            "scan_folder": normalize_windows_path(self.var_scan_folder.get()),
            "output_folder": normalize_windows_path(self.var_output_folder.get()),
            "backup_folder": normalize_windows_path(self.var_backup_folder.get()),
            "review_staging_folder": normalize_windows_path(self.var_review_staging_folder.get()),
            "signature_folder": normalize_windows_path(self.var_signature_folder.get()),
            "sqlite_db": self.settings.get(
                "sqlite_db", os.path.join(BASE_DIR, "claims.db")
            ),
            "xml_source_folder": normalize_windows_path(self.var_xml_source_folder.get()),
            "enable_auto_sign": bool(self.var_auto_sign_csf.get() or self.var_auto_sign_cf2.get()),
            "enable_auto_sign_csf": bool(self.var_auto_sign_csf.get()),
            "enable_auto_sign_cf2": bool(self.var_auto_sign_cf2.get()),
            "enable_date_signed": bool(self.var_date_signed.get()),
            "enable_backup": bool(self.var_backup.get()),
            "show_debug_logs": bool(self.var_debug_logs.get()),
            "enable_auto_process": auto_process_enabled,
            "enable_auto_copy_xml": auto_copy_xml_enabled,
            "theme": self.var_theme.get().strip() or "Light Blue",
        }
        self.auto_process_var.set(bool(self.settings.get("enable_auto_process", False)))
        if hasattr(self, "var_auto_process"):
            self.var_auto_process.set(bool(self.settings.get("enable_auto_process", False)))
        self.auto_copy_xml_var.set(
            bool(self.settings.get("enable_auto_copy_xml", True))
        )
        if hasattr(self, "var_auto_copy_xml"):
            self.var_auto_copy_xml.set(self.auto_copy_xml_var.get())
        self._ensure_xml_auto_copy_service()
        self.auto_copy_xml_status_var.set(
            "Auto Copy XML: Idle"
            if self.auto_copy_xml_var.get()
            else "Auto Copy XML: Off"
        )
        save_json(CONFIG_FILE, self.settings)
        self.apply_theme()
        if show_message:
            messagebox.showinfo(
                "Settings",
                "Preferences saved.\n\nTheme applied. Reopen the GUI if any old panel color remains.",
            )
        self.refresh_dashboard_counts()

    def save_doctors(self):
        save_json(DOCTORS_FILE, self.doctors)
        self.log("[GUI] Doctors configuration saved.")

    def save_all(self):
        self.save_settings()
        self.save_doctors()

    def build_signature_choice_map(self):
        choices = {}
        used_paths = set()

        for doc in self.doctors:
            sig_name = str(doc.get("signature_file") or "").strip()
            name = str(doc.get("name") or "Unnamed").strip()

            if not sig_name:
                continue

            if os.path.isabs(sig_name):
                sig_path = sig_name
            else:
                sig_path = os.path.join(self.settings.get("signature_folder", ""), sig_name)

            label = f"{name} ({os.path.basename(sig_name)})"
            choices[label] = sig_path
            used_paths.add(os.path.normcase(os.path.abspath(sig_path)))

        signature_folder = self.settings.get("signature_folder", "")
        if os.path.isdir(signature_folder):
            for filename in sorted(os.listdir(signature_folder)):
                if not filename.lower().endswith((".png", ".jpg", ".jpeg")):
                    continue
                sig_path = os.path.join(signature_folder, filename)
                normalized = os.path.normcase(os.path.abspath(sig_path))
                if normalized in used_paths:
                    continue
                choices[f"E-Sign: {filename}"] = sig_path
                used_paths.add(normalized)

        return choices

    def draw_signature_on_reportlab_canvas(self, c, sig_path, x, y, max_w, max_h):
        img = Image.open(sig_path).convert("RGBA")
        sig_w, sig_h = img.size

        if sig_w <= 0 or sig_h <= 0:
            raise ValueError("Invalid signature image size.")

        scale = min(max_w / float(sig_w), max_h / float(sig_h))
        draw_w = max(1, sig_w * scale)
        draw_h = max(1, sig_h * scale)
        draw_x = x + ((max_w - draw_w) / 2)
        draw_y = y + ((max_h - draw_h) / 2)

        c.drawImage(
            sig_path,
            draw_x,
            draw_y,
            width=draw_w,
            height=draw_h,
            mask="auto"
        )

    def apply_manual_signature_to_pdf(self, pdf_path, sig_path, page_index, coords):
        x, y, max_w, max_h = coords

        if not os.path.exists(pdf_path):
            raise FileNotFoundError(pdf_path)

        if not os.path.exists(sig_path):
            raise FileNotFoundError(sig_path)

        reader = PdfReader(pdf_path)

        if not reader.pages:
            raise ValueError("PDF has no pages.")

        page_index = max(0, min(int(page_index), len(reader.pages) - 1))
        page = reader.pages[page_index]
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)

        backup_dir = os.path.join(os.path.dirname(pdf_path), "_manual_sign_backup")
        os.makedirs(backup_dir, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(
            backup_dir,
            f"{os.path.splitext(os.path.basename(pdf_path))[0]}_{stamp}.pdf"
        )
        shutil.copy2(pdf_path, backup_path)

        overlay_path = pdf_path + "_manual_overlay.pdf"
        temp_path = pdf_path + "_manual_signed.pdf"

        c = canvas.Canvas(overlay_path, pagesize=(page_width, page_height))
        self.draw_signature_on_reportlab_canvas(c, sig_path, x, y, max_w, max_h)
        c.save()

        overlay_reader = PdfReader(overlay_path)
        writer = PdfWriter()

        for idx, src_page in enumerate(reader.pages):
            if idx == page_index:
                src_page.merge_page(overlay_reader.pages[0])
            writer.add_page(src_page)

        with open(temp_path, "wb") as f:
            writer.write(f)

        os.remove(overlay_path)
        try:
            os.chmod(pdf_path, 0o666)
        except Exception:
            pass
        os.replace(temp_path, pdf_path)

        return backup_path

    def convert_manual_signed_pdfa(self, pdf_path):
        if not os.path.exists(GHOSTSCRIPT_PATH):
            self.log("[MANUAL SIGN] PDF/A skipped. Ghostscript not found.")
            return False

        temp_candidates = []
        best_temp = None
        best_size_kb = None

        try:
            try:
                os.chmod(pdf_path, 0o666)
            except Exception:
                pass

            resolutions = [300, 250, 220, 200, 180, 150, 120]

            for res in resolutions:
                temp_pdf = pdf_path.replace(".pdf", f"_manual_pdfa_{res}.pdf")
                temp_candidates.append(temp_pdf)

                cmd = [
                    GHOSTSCRIPT_PATH,
                    "-dPDFA",
                    "-dBATCH",
                    "-dNOPAUSE",
                    "-sDEVICE=pdfwrite",
                    "-dPreserveAnnots=true",
                    "-dPrinted=true",
                    "-dPDFACompatibilityPolicy=1",
                    "-dDownsampleColorImages=true",
                    f"-dColorImageResolution={res}",
                    "-dDownsampleGrayImages=true",
                    f"-dGrayImageResolution={res}",
                    "-dDownsampleMonoImages=true",
                    f"-dMonoImageResolution={res}",
                    "-dAutoRotatePages=/None",
                    f"-sOutputFile={temp_pdf}",
                    pdf_path,
                ]

                self.log(f"[MANUAL SIGN] PDF/A test {res} DPI: {os.path.basename(pdf_path)}")
                subprocess.run(cmd, check=True)

                size_kb = os.path.getsize(temp_pdf) / 1024
                self.log(f"[MANUAL SIGN] PDF/A size: {size_kb:.0f} KB")

                if best_size_kb is None or size_kb < best_size_kb:
                    if best_temp and os.path.exists(best_temp):
                        os.remove(best_temp)
                    best_temp = temp_pdf
                    best_size_kb = size_kb
                else:
                    os.remove(temp_pdf)

                if size_kb <= MANUAL_SIGN_MAX_SIZE_KB:
                    os.replace(best_temp, pdf_path)
                    os.chmod(pdf_path, 0o444)
                    self.log("[MANUAL SIGN] PDF/A read-only conversion done.")
                    return True

            if best_temp and os.path.exists(best_temp):
                os.replace(best_temp, pdf_path)
                os.chmod(pdf_path, 0o444)
                self.log(
                    "[MANUAL SIGN] PDF/A read-only best effort done; "
                    f"size still above target ({best_size_kb:.0f} KB)."
                )
                return True

            return False

        except Exception as e:
            self.log(f"[MANUAL SIGN] PDF/A conversion failed: {e}")
            return False
        finally:
            for temp_pdf in temp_candidates:
                if os.path.exists(temp_pdf):
                    try:
                        os.remove(temp_pdf)
                    except Exception:
                        pass

    def open_manual_sign_window(self):
        win = tk.Toplevel(self)
        win.title("Manual Sign Document")
        win.geometry("1180x820")
        win.transient(self)

        state = {
            "sig_map": self.build_signature_choice_map()
        }

        pdf_var = tk.StringVar()
        sig_var = tk.StringVar()
        page_var = tk.StringVar(value="1")

        controls = ttk.LabelFrame(win, text="Manual Signature", padding=10)
        controls.pack(fill="x", padx=12, pady=12)
        controls.columnconfigure(1, weight=1)

        ttk.Label(controls, text="PDF").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(controls, textvariable=pdf_var).grid(row=0, column=1, sticky="ew", padx=4, pady=4)

        ttk.Label(controls, text="Signature").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        sig_combo = ttk.Combobox(
            controls,
            textvariable=sig_var,
            values=list(state["sig_map"].keys()),
            state="readonly"
        )
        sig_combo.grid(row=1, column=1, sticky="ew", padx=4, pady=4)

        ttk.Label(controls, text="Page").grid(row=1, column=2, sticky="e", padx=4, pady=4)
        ttk.Entry(controls, textvariable=page_var, width=6).grid(row=1, column=3, sticky="w", padx=4, pady=4)

        preview_card = ttk.LabelFrame(win, text="Preview + Drag Signature Box", padding=10)
        preview_card.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        preview = DoctorPreviewFrame(preview_card)
        preview.pack(fill="both", expand=True)

        def selected_signature_path():
            label = sig_var.get().strip()
            return state["sig_map"].get(label, label)

        def refresh_signature_preview(*_):
            sig_path = selected_signature_path()
            preview.set_signature_preview(sig_path if sig_path else None)

        def load_preview():
            path = pdf_var.get().strip()

            if not path:
                return

            try:
                page_no = max(1, int(float(page_var.get() or "1")))
            except Exception:
                page_no = 1
                page_var.set("1")

            preview.load_pdf_path(path, page_index=page_no - 1)
            refresh_signature_preview()

        def browse_pdf():
            path = filedialog.askopenfilename(
                title="Select CSF/CF2 PDF",
                initialdir=self.settings.get("output_folder", BASE_DIR),
                filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
            )

            if not path:
                return

            pdf_var.set(path)

            try:
                reader = PdfReader(path)
                page_count = len(reader.pages)
            except Exception:
                page_count = 1

            name = os.path.basename(path).lower()

            if "cf2" in name and page_count >= 2:
                page_var.set("2")
            else:
                page_var.set("1")

            load_preview()

        def browse_signature():
            path = filedialog.askopenfilename(
                title="Select Signature PNG",
                initialdir=self.settings.get("signature_folder", BASE_DIR),
                filetypes=[("PNG files", "*.png"), ("All files", "*.*")]
            )

            if not path:
                return

            label = "Custom: " + os.path.basename(path)
            state["sig_map"][label] = path
            sig_combo.config(values=list(state["sig_map"].keys()))
            sig_var.set(label)
            refresh_signature_preview()

        def save_manual_signature():
            pdf_path = pdf_var.get().strip()
            sig_path = selected_signature_path()
            coords = preview.get_pdf_box_coordinates()

            if not pdf_path:
                messagebox.showinfo("Manual Sign", "Select a PDF first.")
                return

            if not sig_path:
                messagebox.showinfo("Manual Sign", "Select a signature first.")
                return

            if not coords:
                messagebox.showinfo("Manual Sign", "Open the PDF and position the red box first.")
                return

            try:
                page_no = max(1, int(float(page_var.get() or "1")))
                backup_path = self.apply_manual_signature_to_pdf(
                    pdf_path,
                    sig_path,
                    page_no - 1,
                    coords
                )
                pdfa_done = self.convert_manual_signed_pdfa(pdf_path)
                if not pdfa_done:
                    try:
                        shutil.copy2(backup_path, pdf_path)
                    except Exception as restore_error:
                        self.log(f"[MANUAL SIGN] Restore failed: {restore_error}")
                    raise RuntimeError(
                        "PDF/A read-only conversion failed. "
                        "The original PDF was restored from backup."
                    )
                self.log(f"[MANUAL SIGN] Signed: {pdf_path}")
                self.log(f"[MANUAL SIGN] Backup: {backup_path}")
                messagebox.showinfo(
                    "Manual Sign",
                    "Signed PDF/A read-only saved.\n\n"
                    + "Backup:\n"
                    + backup_path
                )
                load_preview()
            except Exception as e:
                messagebox.showerror("Manual Sign Error", str(e))
                self.log(f"[MANUAL SIGN ERROR] {e}")

        ttk.Button(controls, text="Browse PDF", command=browse_pdf).grid(row=0, column=2, padx=4, pady=4)
        ttk.Button(controls, text="Load Preview", command=load_preview).grid(row=0, column=3, padx=4, pady=4)
        ttk.Button(controls, text="Browse Signature", command=browse_signature).grid(row=1, column=4, padx=4, pady=4)
        ttk.Button(controls, text="Save Signed PDF/A Read-only", command=save_manual_signature).grid(row=0, column=4, padx=4, pady=4)

        sig_combo.bind("<<ComboboxSelected>>", refresh_signature_preview)

        if state["sig_map"]:
            first_label = next(iter(state["sig_map"].keys()))
            sig_var.set(first_label)
            refresh_signature_preview()

    def run_script(self, key):
        # Save latest checkbox/preferences before running the bot
        try:
            self.save_settings(show_message=False)
        except TypeError:
            self.save_settings()

        if self.running_process is not None:
            messagebox.showwarning("Process Running", "May running process pa.")
            return
        script_name = SCRIPT_CONFIG.get(key)
        base_dir = self.settings["base_dir"]
        script_path = os.path.join(base_dir, script_name)
        if not os.path.exists(script_path):
            messagebox.showerror("Script Not Found", f"Hindi makita ang script:\n\n{script_path}")
            return

        self.clear_logs()
        self.progress.start(10)
        self.status_var.set(f"Running: {script_name}")
        self.start_time = None
        self.log("=" * 70)
        self.log(f"Running script: {script_path}")
        self.log("=" * 70)
        threading.Thread(target=self._run_script_thread, args=(script_path, base_dir), daemon=True).start()

    def open_patient_review_queue(self):
        """Open the review queue independently from long-running processors."""
        self._open_independent_gui("patient_review_queue", "Patient Review Queue")

    def open_pdf_splitter(self):
        """Open the PDF Splitter tool independently."""
        self._open_independent_gui("pdf_splitter", "PDF Splitter")

    def _open_independent_gui(self, key, title):
        base_dir = self.settings["base_dir"]
        script_name = SCRIPT_CONFIG[key]
        script_path = os.path.join(base_dir, script_name)
        if not os.path.exists(script_path):
            messagebox.showerror(
                "Script Not Found",
                f"Hindi makita ang script:\n\n{script_path}",
            )
            return
        try:
            environment = os.environ.copy()
            environment["CLAIMS_SQLITE_DB"] = self.settings.get(
                "sqlite_db", os.path.join(base_dir, "claims.db")
            )
            environment["CLAIMS_SCAN_FOLDER"] = self.settings["scan_folder"]
            environment["CLAIMS_OUTPUT_FOLDER"] = self.settings["output_folder"]
            environment["CLAIMS_BACKUP_FOLDER"] = self.settings["backup_folder"]
            environment["CLAIMS_REVIEW_STAGING_FOLDER"] = self.settings.get(
                "review_staging_folder",
                os.path.join(base_dir, "review_staging"),
            )
            environment["CLAIMS_SIGNATURE_FOLDER"] = self.settings[
                "signature_folder"
            ]
            subprocess.Popen(
                [sys.executable, script_path],
                cwd=base_dir,
                stdin=subprocess.DEVNULL,
                env=environment,
            )
            self.log(f"[GUI] {title} opened.")
        except OSError as exc:
            messagebox.showerror(
                title,
                f"Hindi mabuksan ang {title}:\n\n{exc}",
            )

    def _run_script_thread(self, script_path, cwd):
        try:
            env = os.environ.copy()
            auto_default = bool(self.settings.get("enable_auto_sign", True))
            env["CLAIMS_ENABLE_AUTO_SIGN"] = "1" if auto_default else "0"
            env["CLAIMS_ENABLE_AUTO_SIGN_CSF"] = "1" if self.settings.get("enable_auto_sign_csf", auto_default) else "0"
            env["CLAIMS_ENABLE_AUTO_SIGN_CF2"] = "1" if self.settings.get("enable_auto_sign_cf2", auto_default) else "0"
            env["CLAIMS_ENABLE_DATE_SIGNED"] = "1" if self.settings.get("enable_date_signed", True) else "0"
            env["CLAIMS_ENABLE_BACKUP"] = "1" if self.settings.get("enable_backup", True) else "0"
            env["CLAIMS_SHOW_DEBUG_LOGS"] = "1" if self.settings.get("show_debug_logs", True) else "0"
            env["CLAIMS_SCAN_FOLDER"] = self.settings["scan_folder"]
            env["CLAIMS_OUTPUT_FOLDER"] = self.settings["output_folder"]
            env["CLAIMS_BACKUP_FOLDER"] = self.settings["backup_folder"]
            env["CLAIMS_REVIEW_STAGING_FOLDER"] = self.settings.get(
                "review_staging_folder",
                os.path.join(cwd, "review_staging"),
            )
            env["CLAIMS_SIGNATURE_FOLDER"] = self.settings["signature_folder"]
            env["CLAIMS_SQLITE_DB"] = self.settings.get(
                "sqlite_db", os.path.join(cwd, "claims.db")
            )
            env["CLAIMS_GUI_MODE"] = "1"
            if os.path.basename(script_path) == SCRIPT_CONFIG["claims_processor"]:
                env["CLAIMS_PROCESS_MODE"] = "multiple"
                env["CLAIMS_CONFIRM_PATIENT"] = "0"

            self.running_process = subprocess.Popen(
                [sys.executable, script_path],
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=env
            )
            for line in self.running_process.stdout:
                self.log(line.rstrip())
            code = self.running_process.wait()

            if code == 0:
                self.status_var.set("Done")
                self.log("\n[GUI] Process completed successfully.")
            else:
                self.status_var.set(f"Error/Stopped code {code}")
                self.log(f"\n[GUI] Process exited with code {code}")
                self.log("[GUI] Check logs above for errors.")
                script_name = os.path.basename(script_path)
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        "Script Stopped",
                        (
                            f"{script_name} stopped or encountered an error.\n\n"
                            f"Exit code: {code}\n\n"
                            "Check the Live Processing Logs and any CSV/log file "
                            "created by the script."
                        ),
                    ),
                )

            # IMPORTANT FIX
            self.running_process = None

            # stop progress bar
            self.progress.stop()

            # refresh dashboard stats
            self.refresh_dashboard_counts()

            review_needed = self.count_review_needed_files()
            patient_queue_counts = self.count_patient_review_queue()
            patient_queue_active = (
                patient_queue_counts.get("PENDING", 0)
                + patient_queue_counts.get("IN_REVIEW", 0)
            )
            patient_queue_ready = patient_queue_counts.get("RESOLVED", 0)
            latest_output = self.get_latest_output_folder()
            self.log("[SUMMARY] Dashboard refreshed.")
            self.log(f"[SUMMARY] Patient Queue active: {patient_queue_active}")
            self.log(f"[SUMMARY] Ready to resume: {patient_queue_ready}")
            if latest_output:
                self.log(f"[SUMMARY] Latest output: {latest_output}")
            if review_needed > 0:
                self.log(f"[REVIEW] {review_needed} file(s) need Unknown Review.")
            else:
                self.log("[REVIEW] No unknown/review files found.")

            # timer removed
            self.start_time = None
        except Exception as e:
            self.status_var.set("Error")
            self.log(f"[GUI ERROR] {e}")
        finally:
            self.progress.stop()
            self.refresh_dashboard_counts()

    def toggle_auto_process(self):
        enabled = bool(self.auto_process_var.get())
        self.settings["enable_auto_process"] = enabled
        if hasattr(self, "var_auto_process"):
            self.var_auto_process.set(enabled)
        save_json(CONFIG_FILE, self.settings)
        self.auto_last_signature = None
        self.auto_stable_since = None
        self.auto_process_status_var.set(
            "Auto Process: Waiting for PDFs" if enabled else "Auto Process: Off"
        )
        self.log(f"[AUTO] Auto Process Scans {'enabled' if enabled else 'disabled'}.")

    def schedule_auto_process_watcher(self):
        if self.auto_process_job is not None:
            try:
                self.after_cancel(self.auto_process_job)
            except Exception:
                pass
        self.auto_process_job = self.after(self.auto_poll_ms, self.auto_process_tick)

    def get_scan_pdf_signature(self):
        scan_folder = self.settings.get("scan_folder", "")
        if not scan_folder or not os.path.isdir(scan_folder):
            return ()
        signature = []
        for name in sorted(os.listdir(scan_folder)):
            if not name.lower().endswith(".pdf"):
                continue
            path = os.path.join(scan_folder, name)
            if not os.path.isfile(path):
                continue
            try:
                stat_result = os.stat(path)
            except OSError:
                continue
            signature.append((name, stat_result.st_size, stat_result.st_mtime_ns))
        return tuple(signature)

    def auto_process_tick(self):
        try:
            self._auto_process_tick()
        except Exception as exc:
            self.auto_process_status_var.set(f"Auto Process: Error - {exc}")
            self.log(f"[AUTO ERROR] {exc}")
        finally:
            self.schedule_auto_process_watcher()

    def _auto_process_tick(self):
        if not bool(self.auto_process_var.get()):
            self.auto_process_status_var.set("Auto Process: Off")
            return

        signature = self.get_scan_pdf_signature()
        pdf_count = len(signature)
        queue_counts = self.count_patient_review_queue()
        review_active = queue_counts.get("PENDING", 0) + queue_counts.get("IN_REVIEW", 0)
        review_note = (
            f" | Review items waiting: {review_active}"
            if review_active
            else ""
        )

        if self.running_process is not None:
            status = "Processing"
            if pdf_count:
                status = f"Pending next run ({pdf_count} PDF)"
            self.auto_process_status_var.set(f"Auto Process: {status}{review_note}")
            return

        if not signature:
            self.auto_last_signature = None
            self.auto_stable_since = None
            self.auto_process_status_var.set(f"Auto Process: Idle{review_note}")
            return

        now = time.time()
        if signature != self.auto_last_signature:
            self.auto_last_signature = signature
            self.auto_stable_since = now
            self.auto_process_status_var.set(
                f"Auto Process: Waiting for stable PDFs ({pdf_count}){review_note}"
            )
            return

        stable_for = now - (self.auto_stable_since or now)
        if stable_for < self.auto_stable_seconds:
            remaining = max(1, int(self.auto_stable_seconds - stable_for))
            self.auto_process_status_var.set(
                f"Auto Process: Waiting {remaining}s ({pdf_count} PDF){review_note}"
            )
            return

        self.auto_process_status_var.set(
            f"Auto Process: Starting processor ({pdf_count} PDF){review_note}"
        )
        self.log(f"[AUTO] Stable PDFs detected: {pdf_count}. Starting processor.")
        self.auto_last_signature = None
        self.auto_stable_since = None
        self.run_script("claims_processor")

    def _create_xml_auto_copy_service(self):
        base_dir = self.settings.get("base_dir", BASE_DIR) or BASE_DIR
        return XmlAutoCopyService(
            source_folder=self.settings.get(
                "xml_source_folder", r"C:\Shared Folder\FTPURL"
            ),
            output_folder=self.settings.get("output_folder", ""),
            incomplete_folder=os.path.join(
                base_dir,
                "claims_checker_results",
                "INCOMPLETE",
            ),
        )

    def _ensure_xml_auto_copy_service(self):
        candidate = self._create_xml_auto_copy_service()
        current = getattr(self, "xml_auto_copy_service", None)
        if current is None or current.configuration_key != candidate.configuration_key:
            self.xml_auto_copy_service = candidate
        return self.xml_auto_copy_service

    def schedule_auto_copy_xml_watcher(self):
        if self.auto_copy_xml_job is not None:
            try:
                self.after_cancel(self.auto_copy_xml_job)
            except Exception:
                pass
        self.auto_copy_xml_job = self.after(
            self.auto_copy_xml_poll_ms,
            self.auto_copy_xml_tick,
        )

    def auto_copy_xml_tick(self):
        try:
            if not bool(self.auto_copy_xml_var.get()):
                self.auto_copy_xml_status_var.set("Auto Copy XML: Off")
                return
            self._start_xml_copy_cycle(manual=False)
        except Exception as exc:
            self.auto_copy_xml_status_var.set(f"Auto Copy XML: Error - {exc}")
            self.log(f"[AUTO XML] COPY_ERROR | reason={exc}")
        finally:
            self.schedule_auto_copy_xml_watcher()

    def _start_xml_copy_cycle(self, *, manual=False):
        if not self.auto_copy_xml_lock.acquire(blocking=False):
            self.auto_copy_xml_status_var.set("Auto Copy XML: Copying")
            if manual:
                messagebox.showinfo(
                    "Copy XML",
                    "Auto Copy XML is already running. Please check the status shortly.",
                )
            return False

        service = self._ensure_xml_auto_copy_service()
        self.auto_copy_xml_status_var.set("Auto Copy XML: Copying")

        def worker():
            try:
                summary = service.scan_once(
                    require_observed_stable=not manual,
                )
            except Exception as exc:
                summary = XmlCopySummary(())
                error = str(exc)
            else:
                error = ""
            finally:
                self.auto_copy_xml_lock.release()

            try:
                self.after(
                    0,
                    lambda: self._finish_xml_copy_cycle(
                        summary,
                        manual=manual,
                        error=error,
                    ),
                )
            except Exception:
                pass

        threading.Thread(
            target=worker,
            name="xml-auto-copy",
            daemon=True,
        ).start()
        return True

    def _finish_xml_copy_cycle(self, summary, *, manual=False, error=""):
        if error:
            self.auto_copy_xml_status_var.set("Auto Copy XML: Error")
            self.log(f"[AUTO XML] COPY_ERROR | reason={error}")
            if manual:
                messagebox.showerror("Copy XML Error", error)
            return

        for event in summary.events:
            if event.reportable:
                self.log(format_xml_copy_event(event))

        source_unavailable = summary.count("SOURCE_UNAVAILABLE")
        copied = summary.copied_count
        waiting = summary.waiting_count
        issues = summary.issue_count
        unmatched = summary.count("UNMATCHED")

        if source_unavailable:
            status = "Auto Copy XML: Source unavailable"
        elif copied and issues:
            status = f"Auto Copy XML: Copied {copied} | Issues {issues}"
        elif copied:
            status = f"Auto Copy XML: Copied: {copied}"
        elif issues:
            status = f"Auto Copy XML: Ambiguous/Conflict: {issues}"
        elif waiting:
            status = f"Auto Copy XML: Waiting for stable XML ({waiting})"
        elif unmatched:
            status = f"Auto Copy XML: Idle | Unmatched: {unmatched}"
        else:
            status = "Auto Copy XML: Idle"
        self.auto_copy_xml_status_var.set(status)

        if copied:
            self.refresh_dashboard_counts()

        if manual:
            identical = summary.count("IDENTICAL_SKIP")
            messagebox.showinfo(
                "Copy XML",
                (
                    "XML copy check completed.\n\n"
                    f"Copied: {copied}\n"
                    f"Already identical: {identical}\n"
                    f"Waiting: {waiting}\n"
                    f"Unmatched: {unmatched}\n"
                    f"Ambiguous/Conflict/Error: {issues}\n\n"
                    "Check Live Processing Logs for details."
                ),
            )

    def copy_xml_button_clicked(self):
        """
        Manual XML copy workflow:
            Scan -> Process -> Generate XML -> Click this button
        """
        try:
            self.save_settings(show_message=False)
        except TypeError:
            self.save_settings()

        self.log("=" * 70)
        self.log("[AUTO XML] Manual safe copy check started...")
        self.log(f"[XML] Source: {self.settings.get('xml_source_folder', r'C:\Shared Folder\FTPURL')}")
        self.log(f"[XML] Output: {self.settings.get('output_folder', '')}")
        self.log("=" * 70)

        self._start_xml_copy_cycle(manual=True)


    def stop_process(self):
        if self.running_process is None:
            self.log("No running process.")
            return
        if messagebox.askyesno("Stop Process", "Stop running process?"):
            self.running_process.terminate()
            self.log("[GUI] Stop signal sent.")
            self.status_var.set("Stopping...")

    def count_review_needed_files(self):
        """
        Count output PDFs that may need Unknown Review Manager.

        Looks for filenames containing:
        UNKNOWN, SOA2_page1/page2, MRF_page1/page2, etc.
        """
        output = self.settings.get("output_folder", "")
        count = 0

        if not os.path.exists(output):
            return 0

        for root, dirs, files in os.walk(output):
            if "_reviewed_unknowns" in root:
                continue

            for f in files:
                if not f.lower().endswith(".pdf"):
                    continue

                name_upper = f.upper()

                for kw in REVIEW_KEYWORDS:
                    if kw in name_upper:
                        count += 1
                        break

        return count


    def refresh_dashboard_counts(self):
        scan = self.settings.get("scan_folder", "")
        output = self.settings.get("output_folder", "")
        total = 0
        unknown = 0
        patients = 0
        if os.path.exists(scan):
            for f in os.listdir(scan):
                if f.lower().endswith(".pdf"):
                    total += 1
                if "unknown" in f.lower():
                    unknown += 1
        if os.path.exists(output):
            for f in os.listdir(output):
                if os.path.isdir(os.path.join(output, f)):
                    patients += 1
        review_needed = self.count_review_needed_files()

        self.stat_total_pdfs.set(str(total))
        self.stat_unknown.set(str(unknown))
        self.stat_patients.set(str(patients))
        self.stat_failed_ocr.set("0")
        self.stat_review_needed.set(str(review_needed))
        patient_queue_counts = self.count_patient_review_queue()
        patient_queue_active = (
            patient_queue_counts.get("PENDING", 0)
            + patient_queue_counts.get("IN_REVIEW", 0)
        )
        patient_queue_ready = patient_queue_counts.get("RESOLVED", 0)
        self.stat_patient_queue.set(str(patient_queue_active))

        claims_root = os.path.join(BASE_DIR, "claims_checker_results")

        def count_folders(name):
            p = os.path.join(claims_root, name)
            if not os.path.exists(p):
                return 0
            return sum(1 for f in os.listdir(p) if os.path.isdir(os.path.join(p, f)))

        ready_count = count_folders("READY")
        review_count = count_folders("READY_WITH_REVIEW")
        incomplete_count = count_folders("INCOMPLETE")
        archived_count = count_folders("READY_ARCHIVED")

        self.stat_ready.set(str(ready_count))
        self.stat_ready_review.set(str(review_count))
        self.stat_incomplete.set(str(incomplete_count))
        self.stat_archived.set(str(archived_count))

        if hasattr(self, "unknown_review_btn"):
            if review_needed > 0:
                self.unknown_review_btn.config(
                    text=f"Unknown Review Manager ({review_needed})",
                    style="Warning.TButton",
                )
            else:
                self.unknown_review_btn.config(
                    text="Unknown Review Manager (0)",
                    style="Big.TButton",
                )

        if hasattr(self, "patient_review_queue_btn"):
            if patient_queue_active:
                self.patient_review_queue_btn.config(
                    text=f"⚠ Patient Review Queue ({patient_queue_active} for review)",
                    style="Warning.TButton",
                )
                self.review_alert_var.set(
                    f"⚠ {patient_queue_active} patient(s) need review. Open Patient Review Queue."
                )
            elif patient_queue_ready:
                self.patient_review_queue_btn.config(
                    text=f"Patient Review Queue ({patient_queue_ready} ready to resume)",
                    style="Big.TButton",
                )
                self.review_alert_var.set(
                    f"✅ No active review. {patient_queue_ready} patient(s) ready to resume."
                )
            else:
                self.patient_review_queue_btn.config(
                    text="Patient Review Queue (0)",
                    style="Big.TButton",
                )
                self.review_alert_var.set("✅ No active patient review items.")

    def schedule_dashboard_auto_refresh(self):
        if self.dashboard_auto_refresh_job is not None:
            try:
                self.after_cancel(self.dashboard_auto_refresh_job)
            except Exception:
                pass
        self.dashboard_auto_refresh_job = self.after(15000, self.dashboard_auto_refresh_tick)

    def dashboard_auto_refresh_tick(self):
        try:
            self.refresh_dashboard_counts()
        finally:
            self.schedule_dashboard_auto_refresh()

    def schedule_hbsys_status_check(self):
        if self.hbsys_status_job is not None:
            try:
                self.after_cancel(self.hbsys_status_job)
            except Exception:
                pass
        self.hbsys_status_job = self.after(
            self.hbsys_status_poll_ms,
            self.hbsys_status_tick,
        )

    def hbsys_status_tick(self):
        try:
            self.update_hbsys_status()
        except Exception as exc:  # noqa: BLE001 - status display only.
            self.log(f"[HBSYS STATUS] {exc}")
        finally:
            self.schedule_hbsys_status_check()

    def check_hbsys_status_now(self):
        if self.hbsys_status_job is not None:
            try:
                self.after_cancel(self.hbsys_status_job)
            except Exception:
                pass
        self.update_hbsys_status()
        self.schedule_hbsys_status_check()

    def update_hbsys_status(self):
        try:
            window = find_hbsys_window()
        except Exception as exc:  # noqa: BLE001 - status display only.
            self.hbsys_status_var.set("● HBSys: Error")
            if self.hbsys_status_label is not None:
                self.hbsys_status_label.configure(fg="#DC2626")
            self.log(f"[HBSYS STATUS] detection error: {exc}")
            self._apply_hbsys_block(blocked=True)
            return
        if window is None:
            self.hbsys_status_var.set("● HBSys: CLOSED")
            if self.hbsys_status_label is not None:
                self.hbsys_status_label.configure(fg="#DC2626")
            self._apply_hbsys_block(blocked=True)
        else:
            self.hbsys_status_var.set("● HBSys: OPEN")
            if self.hbsys_status_label is not None:
                self.hbsys_status_label.configure(fg="#16A34A")
            self._apply_hbsys_block(blocked=False)

    def _apply_hbsys_block(self, blocked: bool) -> None:
        """Disable/enable HBSys-dependent actions and show a warning when closed."""
        state = "disabled" if blocked else "normal"
        for button in (getattr(self, "date_fill_btn", None), getattr(self, "xml_clicker_btn", None)):
            if button is not None:
                button.configure(state=state)
        if self.hbsys_warning_label is not None:
            if blocked:
                self.hbsys_warning_label.configure(
                    text=(
                        "⚠ HBSys is CLOSED — Date Fill and XML Clicker are "
                        "disabled. Open HBSys first."
                    ),
                    fg="#DC2626",
                )
            else:
                self.hbsys_warning_label.configure(text="")

    def get_latest_output_folder(self):
        output = self.settings.get("output_folder", "")
        if not output or not os.path.isdir(output):
            return None
        folders = [
            os.path.join(output, item)
            for item in os.listdir(output)
            if os.path.isdir(os.path.join(output, item))
        ]
        if not folders:
            return None
        return max(folders, key=lambda path: os.path.getmtime(path))

    def open_latest_output_folder(self):
        latest = self.get_latest_output_folder()
        if not latest:
            messagebox.showinfo(
                "Open Latest Output",
                "No output patient folder found yet.",
            )
            return
        self.open_folder(latest)

    def count_patient_review_queue(self):
        db_path = self.settings.get(
            "sqlite_db",
            os.path.join(self.settings.get("base_dir", BASE_DIR), "claims.db"),
        )
        if not db_path or not os.path.exists(db_path):
            return {}
        try:
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT status, COUNT(*) AS total
                    FROM review_queue
                    GROUP BY status
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            self.log(f"[GUI] Could not read Patient Review Queue count: {exc}")
            return {}
        return {str(status): int(total) for status, total in rows}

    def update_timer(self):
        return




    def recheck_incomplete_claims(self):
        incomplete_dir = os.path.join(BASE_DIR, "claims_checker_results", "INCOMPLETE")

        if not os.path.exists(incomplete_dir):
            messagebox.showinfo(
                "Recheck INCOMPLETE",
                f"Folder not found:\n\n{incomplete_dir}"
            )
            return

        folders = [
            f for f in os.listdir(incomplete_dir)
            if os.path.isdir(os.path.join(incomplete_dir, f))
        ]

        if not folders:
            messagebox.showinfo(
                "Recheck INCOMPLETE",
                "No INCOMPLETE claims found."
            )
            return

        claims_checker = os.path.join(
            self.settings.get("base_dir", BASE_DIR),
            "claims_checker.py"
        )

        if not os.path.exists(claims_checker):
            messagebox.showerror(
                "Recheck INCOMPLETE",
                f"claims_checker.py not found:\n\n{claims_checker}"
            )
            return

        if not messagebox.askyesno(
            "Recheck INCOMPLETE",
            f"Recheck {len(folders)} INCOMPLETE claim folders?"
        ):
            return

        self.log("=" * 70)
        self.log("[RECHECK] Starting INCOMPLETE recheck...")
        self.log(f"[RECHECK] Folder count: {len(folders)}")
        self.log("=" * 70)

        try:
            self.save_settings(show_message=False)
        except TypeError:
            self.save_settings()

        script_path = os.path.join(
            self.settings.get("base_dir", BASE_DIR),
            "claims_checker.py"
        )

        env = os.environ.copy()
        env["CLAIMS_RECHECK_INCOMPLETE"] = "1"

        self.clear_logs()
        self.progress.start(10)
        self.status_var.set("Running: Recheck INCOMPLETE")

        def run_recheck():
            try:
                self.running_process = subprocess.Popen(
                    [sys.executable, script_path],
                    cwd=self.settings.get("base_dir", BASE_DIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env
                )

                for line in self.running_process.stdout:
                    self.log(line.rstrip())

                self.running_process.wait()
                self.log("[RECHECK] Complete.")
            finally:
                self.running_process = None
                self.progress.stop()
                self.refresh_dashboard_counts()

        threading.Thread(target=run_recheck, daemon=True).start()

    def archive_transmitted_claims(self):
        import shutil

        source = os.path.join(BASE_DIR, "claims_checker_results", "READY")

        dest1 = r"C:\Shared Folder\FTPURL\TRANSMITTED\scan via bot"
        dest2 = r"\\192.168.1.193\Echague District Hospital Files\melvin\SHAREDFOLDER\TRANSMITTED\MELVIN\scan via bot"
        archived_root = os.path.join(BASE_DIR, "claims_checker_results", "READY_ARCHIVED")

        if not os.path.exists(source):
            messagebox.showinfo("Archive", f"READY folder not found:\n\n{source}")
            return

        folders = [f for f in os.listdir(source) if os.path.isdir(os.path.join(source, f))]

        if not folders:
            messagebox.showinfo("Archive", "No READY claims found.")
            return

        if not messagebox.askyesno(
            "Archive Transmitted Claims",
            f"Move {len(folders)} READY claim folders to TRANSMITTED archives?"
        ):
            return

        os.makedirs(dest1, exist_ok=True)
        os.makedirs(dest2, exist_ok=True)
        os.makedirs(archived_root, exist_ok=True)

        moved = 0
        failed = 0

        for folder in folders:
            src_folder = os.path.join(source, folder)
            dst1 = os.path.join(dest1, folder)
            dst2 = os.path.join(dest2, folder)

            try:
                if os.path.exists(dst1):
                    shutil.rmtree(dst1, ignore_errors=True)

                if os.path.exists(dst2):
                    shutil.rmtree(dst2, ignore_errors=True)

                shutil.copytree(src_folder, dst1)
                shutil.copytree(src_folder, dst2)

                if os.path.exists(dst1) and os.path.exists(dst2):
                    try:
                        archived_folder = os.path.join(archived_root, folder)

                        if os.path.exists(archived_folder):
                            shutil.rmtree(archived_folder, ignore_errors=True)

                        shutil.move(src_folder, archived_folder)

                        moved += 1
                        self.log(f"[ARCHIVE] {folder} moved to READY_ARCHIVED")

                    except Exception as move_error:
                        failed += 1
                        self.log(f"[ARCHIVE MOVE ERROR] {folder}: {move_error}")
                else:
                    failed += 1

            except Exception as e:
                failed += 1
                self.log(f"[ARCHIVE ERROR] {folder}: {e}")

        messagebox.showinfo(
            "Archive Complete",
            f"Archived: {moved}\nFailed: {failed}"
        )

    def open_folder(self, path):
        path = normalize_windows_path(path)
        if not os.path.exists(path):
            if messagebox.askyesno("Folder Not Found", f"Create folder?\n\n{path}"):
                os.makedirs(path, exist_ok=True)
            else:
                return
        os.startfile(path)

    def open_file(self, path):
        if not os.path.exists(path):
            messagebox.showinfo(
                "Report Not Found",
                "Run Check Missing Requirements first.\n\n" + path
            )
            return

        os.startfile(path)

    def log(self, message):
        def append():
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
        self.after(0, append)

    def clear_logs(self):
        self.log_text.delete("1.0", "end")

    def save_logs(self):
        logs = self.log_text.get("1.0", "end").strip()
        if not logs:
            messagebox.showinfo("Save Logs", "No logs to save.")
            return
        filename = f"claims_gui_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile=filename,
                                            filetypes=[("Text files", "*.txt")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(logs)
            messagebox.showinfo("Save Logs", f"Saved:\n{path}")


if __name__ == "__main__":
    app = EDHClaimsGUI()
    app.mainloop()
