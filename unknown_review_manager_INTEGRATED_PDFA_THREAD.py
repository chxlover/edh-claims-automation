import os
import json
import re
import shutil
import sys
import subprocess
import threading
import tempfile
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime

try:
    import fitz  # PyMuPDF
    from PIL import Image, ImageTk
except Exception:
    fitz = None
    Image = None
    ImageTk = None

try:
    from PyPDF2 import PdfReader, PdfMerger
except Exception:
    PdfReader = None
    PdfMerger = None

try:
    from core.soa2_resolver import resolve_soa2_text
except Exception:
    resolve_soa2_text = None

try:
    from core.visual_document_learner import (
        LEARNABLE_TYPES,
        VisualDocumentLearner,
    )
    from gui.visual_learning_manager import VisualLearningManager
except Exception:
    LEARNABLE_TYPES = frozenset()
    VisualDocumentLearner = None
    VisualLearningManager = None


# ============================================================
# EDH UNKNOWN REVIEW MANAGER
# ============================================================
# Purpose:
#   Run AFTER normal processing is finished.
#
# It reviews suspicious/unknown output PDFs and lets you identify them.
#
# Rules:
#   DTR:
#       if DTR.pdf exists -> merge selected unknown into DTR.pdf
#       else -> rename/copy as DTR.pdf
#
#   MRF_page2:
#       merge with MRF_page1.pdf ONLY IF MRF_page1.pdf has exactly 1 page
#       MRF_page1 + selected MRF_page2 -> MRF.pdf
#
#   SOA2_page2:
#       merge with SOA2_page1.pdf ONLY IF SOA2_page1.pdf has exactly 1 page
#       SOA2_page1 + selected SOA2_page2 -> SOA2.pdf
#
#   Otherwise:
#       save/rename using selected type.
#
# Training:
#   saves decisions to C:\claims_bot\unknown_training_data.json
#
# Recommended location:
#   C:\claims_bot\unknown_review_manager.py
#
# Run:
#   python unknown_review_manager.py
# ============================================================

BASE_DIR = r"C:\claims_bot"
OUTPUT_FOLDER = os.path.join(BASE_DIR, "output")
TRAINING_FILE = os.path.join(BASE_DIR, "unknown_training_data.json")
REVIEW_LOG_FILE = os.path.join(BASE_DIR, "unknown_review_log.json")
PDFA_SCRIPT = os.path.join(BASE_DIR, "pdfa.py")  # kept for compatibility, not used by integrated converter
GHOSTSCRIPT_EXE = r"C:\Program Files\gs\gs10.07.0\bin\gswin64c.exe"
TARGET_PDFA_KB = 1000

REVIEW_KEYWORDS = [
    "UNKNOWN",
    "SOA2_page1",
    "SOA2_page2",
    "SOA2_page2_1",
    "MRF_page1",
    "MRF_page2",
    "MRF_page2_1",
]

DOC_TYPES = [
    "SOA1",
    "SOA2",
    "DTR",
    "SOA2_page1",
    "SOA2_page2",
    "MRF_page1",
    "MRF_page2",
    "COE",
    "MMC",
    "CSF",
    "CF2",
    "CF2_page1",
    "CF2_page2",
    "PBC_page1",
    "PBC_page2",
    "OPR",
    "ANR",
    "OTHER",
    "SKIP",
]

COMMON_TRAINING_PHRASE_BLOCKLIST = {
    "ECHAGUE DISTRICT HOSPITAL",
    "PHILIPPINE HEALTH INSURANCE CORPORATION",
    "PHILHEALTH",
    "PATIENT NAME",
    "LAST NAME",
    "FIRST NAME",
    "MIDDLE NAME",
    "HOSPITAL NO",
    "HOSPITAL NUMBER",
    "PATIENT HEALTH RECORD NO",
    "ADMISSION DATE",
    "DISCHARGE DATE",
    "DATE ADMITTED",
    "DATE DISCHARGED",
    "SIGNATURE",
    "PAGE",
}


def ensure_folder(path):
    os.makedirs(path, exist_ok=True)


def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json(path, data):
    ensure_folder(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def safe_path(path):
    return os.path.normpath(path)


def normalize_training_text(value):
    text = str(value or "").upper()
    text = text.replace("Ã‘", "N")
    text = re.sub(r"[^A-Z0-9\- /]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def compact_training_text(value):
    return re.sub(r"[^A-Z0-9]+", "", normalize_training_text(value))


def is_safe_training_phrase(phrase):
    clean = normalize_training_text(phrase)

    if len(clean) < 4 or len(clean) > 70:
        return False

    if clean in COMMON_TRAINING_PHRASE_BLOCKLIST:
        return False

    if any(blocked in clean for blocked in COMMON_TRAINING_PHRASE_BLOCKLIST):
        return False

    letters = sum(1 for c in clean if c.isalpha())
    digits = sum(1 for c in clean if c.isdigit())

    if letters < 4:
        return False

    if digits > letters:
        return False

    # Avoid patient-specific names/dates/amounts as training rules.
    if re.search(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", clean):
        return False

    if re.search(r"\b\d{8,}\b", clean):
        return False

    return True


def phrase_exists_in_text(phrase, text):
    clean_phrase = normalize_training_text(phrase)
    clean_text = normalize_training_text(text)

    if clean_phrase and clean_phrase in clean_text:
        return True

    compact_phrase = compact_training_text(phrase)
    compact_text = compact_training_text(text)

    return bool(compact_phrase and compact_phrase in compact_text)


def get_existing_training_keyword_types():
    keyword_types = {}

    for record in load_json(TRAINING_FILE, []):
        if not isinstance(record, dict):
            continue

        selected_type = record.get("doc_type") or record.get("selected_type")
        if not selected_type:
            continue

        keywords = record.get("keywords") or []
        if isinstance(keywords, str):
            keywords = re.split(r"[,\n;|]+", keywords)

        for keyword in keywords:
            clean = normalize_training_text(keyword)
            if not clean:
                continue
            keyword_types.setdefault(clean, set()).add(str(selected_type))

    return keyword_types


def unique_path(path):
    if not os.path.exists(path):
        return path

    base, ext = os.path.splitext(path)
    counter = 1

    while True:
        candidate = f"{base}_{counter}{ext}"
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def get_pdf_page_count(path):
    if PdfReader is None:
        return None

    try:
        reader = PdfReader(path)
        return len(reader.pages)
    except Exception:
        return None


def merge_pdfs(output_path, input_paths):
    if PdfMerger is None:
        raise RuntimeError("PyPDF2 is not installed. Run: pip install PyPDF2")

    temp_path = output_path + ".tmp_merge.pdf"

    merger = PdfMerger()

    try:
        for p in input_paths:
            merger.append(p)

        with open(temp_path, "wb") as f:
            merger.write(f)

    finally:
        try:
            merger.close()
        except Exception:
            pass

    if os.path.exists(output_path):
        backup = unique_path(output_path.replace(".pdf", "_backup.pdf"))
        shutil.move(output_path, backup)

    shutil.move(temp_path, output_path)


def find_ghostscript():
    """
    Find Ghostscript executable.
    """
    candidates = [
        GHOSTSCRIPT_EXE,
        r"C:\Program Files\gs\gs10.07.0\bin\gswin64c.exe",
        r"C:\Program Files\gs\gs10.06.0\bin\gswin64c.exe",
        r"C:\Program Files\gs\gs10.05.1\bin\gswin64c.exe",
        r"C:\Program Files\gs\gs10.04.0\bin\gswin64c.exe",
        r"C:\Program Files\gs\gs10.03.1\bin\gswin64c.exe",
    ]

    for p in candidates:
        if os.path.exists(p):
            return p

    return shutil.which("gswin64c") or shutil.which("gs")


def convert_pdf_to_pdfa_readonly_inplace(pdf_path, target_kb=TARGET_PDFA_KB):
    """
    Integrated PDF/A read-only conversion.
    Does NOT call pdfa.py.

    Uses Ghostscript directly on the selected/affected PDF only.

    Output:
        same filename overwritten safely
    """
    gs = find_ghostscript()

    if not gs:
        return False, "Ghostscript not found. Install Ghostscript or check GHOSTSCRIPT_EXE path."

    if not os.path.exists(pdf_path):
        return False, f"PDF not found: {pdf_path}"

    # DPI attempts: start quality high, lower if file too large.
    dpi_attempts = [300, 250, 220, 200, 180, 150, 120]

    best_temp = None
    best_size = None

    temp_dir = tempfile.mkdtemp(prefix="unknown_pdfa_")

    try:
        for dpi in dpi_attempts:
            temp_out = os.path.join(temp_dir, f"pdfa_{dpi}.pdf")

            cmd = [
                gs,
                "-dPDFA=2",
                "-dBATCH",
                "-dNOPAUSE",
                "-dNOOUTERSAVE",
                "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.7",
                "-dAutoRotatePages=/None",
                "-dDetectDuplicateImages=true",
                "-dCompressFonts=true",
                "-dSubsetFonts=true",
                "-dDownsampleColorImages=true",
                "-dDownsampleGrayImages=true",
                "-dDownsampleMonoImages=true",
                f"-dColorImageResolution={dpi}",
                f"-dGrayImageResolution={dpi}",
                f"-dMonoImageResolution={dpi}",
                "-sColorConversionStrategy=RGB",
                "-sProcessColorModel=DeviceRGB",
                "-sOutputFile=" + temp_out,
                pdf_path,
            ]

            result = subprocess.run(
                cmd,
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            )

            if result.returncode != 0 or not os.path.exists(temp_out):
                continue

            size_kb = os.path.getsize(temp_out) // 1024

            if best_size is None or size_kb < best_size:
                best_size = size_kb
                best_temp = temp_out

            if size_kb <= target_kb:
                best_temp = temp_out
                best_size = size_kb
                break

        if not best_temp:
            return False, "Ghostscript failed to create PDF/A output."

        backup = unique_path(pdf_path.replace(".pdf", "_before_pdfa.pdf"))
        shutil.move(pdf_path, backup)
        shutil.copy2(best_temp, pdf_path)

        # Read-only
        try:
            os.chmod(pdf_path, 0o444)
        except Exception:
            pass

        return True, f"PDF/A read-only done: {os.path.basename(pdf_path)} ({best_size} KB)"

    except Exception as e:
        return False, str(e)

    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def run_pdfa_converter_after_review(target_paths=None):
    """
    Integrated PDF/A converter.
    Converts only affected final PDF(s), not the whole output folder.
    """
    if not target_paths:
        return True, "No target PDF/A conversion needed."

    messages = []
    all_ok = True

    for p in target_paths:
        ok, msg = convert_pdf_to_pdfa_readonly_inplace(p)
        messages.append(msg)

        if not ok:
            all_ok = False

    return all_ok, "\n".join(messages)


def move_to_reviewed_folder(path):
    folder = os.path.join(os.path.dirname(path), "_reviewed_unknowns")
    ensure_folder(folder)

    dst = unique_path(os.path.join(folder, os.path.basename(path)))
    shutil.move(path, dst)
    return dst


def save_training_record(patient_folder, source_file, selected_type, action, keywords=""):
    data = load_json(TRAINING_FILE, [])

    record = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "patient_folder": os.path.basename(patient_folder),
        "sample_file": os.path.basename(source_file),
        "selected_type": selected_type,
        "action": action,
        "keywords": [k.strip() for k in keywords.split(",") if k.strip()],
    }

    data.append(record)
    save_json(TRAINING_FILE, data)

    logs = load_json(REVIEW_LOG_FILE, [])
    logs.append(record)
    save_json(REVIEW_LOG_FILE, logs)


class UnknownReviewManager(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("EDH Unknown Review Manager")
        self.geometry("1350x850")
        self.minsize(1200, 760)

        self.output_folder_var = tk.StringVar(value=OUTPUT_FOLDER)
        self.review_items = []
        self.current_index = None
        self.current_pdf_image = None
        self.current_photo = None
        self.zoom_percent = 60

        self.selected_type_var = tk.StringVar(value="DTR")
        self.keywords_var = tk.StringVar()
        self.suggested_keywords_var = tk.StringVar(value="Suggested keywords: none")
        self.current_suggested_keywords = []
        self.use_visual_training_var = tk.BooleanVar(value=True)
        self.visual_suggestion_var = tk.StringVar(
            value="Visual learning: waiting for a file"
        )
        self.visual_analysis_token = 0
        self.visual_learner = None
        if VisualDocumentLearner is not None:
            try:
                self.visual_learner = VisualDocumentLearner()
            except Exception as exc:
                self.visual_suggestion_var.set(f"Visual learning unavailable: {exc}")

        self.build_ui()
        self.scan_review_files()

    # ======================================================
    # UI
    # ======================================================
    def build_ui(self):
        main = ttk.Frame(self, padding=12)
        main.pack(fill="both", expand=True)

        top = ttk.Frame(main)
        top.pack(fill="x")

        ttk.Label(
            top,
            text="EDH Unknown Review Manager",
            font=("Segoe UI", 18, "bold")
        ).pack(side="left")

        ttk.Button(top, text="Scan Output Folder", command=self.scan_review_files).pack(side="right")
        ttk.Button(top, text="Browse Output", command=self.browse_output_folder).pack(side="right", padx=(0, 8))
        ttk.Button(
            top, text="Visual Learning Manager", command=self.open_visual_learning_manager
        ).pack(side="right", padx=(0, 8))

        folder_row = ttk.Frame(main)
        folder_row.pack(fill="x", pady=(8, 12))

        ttk.Label(folder_row, text="Output Folder:", width=14).pack(side="left")
        ttk.Entry(folder_row, textvariable=self.output_folder_var).pack(side="left", fill="x", expand=True)

        body = ttk.Frame(main)
        body.pack(fill="both", expand=True)

        left = ttk.LabelFrame(body, text="Review Queue", padding=8)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.config(width=410)
        left.pack_propagate(False)

        self.queue_listbox = tk.Listbox(left, font=("Segoe UI", 9), height=28)
        self.queue_listbox.pack(fill="both", expand=True)
        self.queue_listbox.bind("<<ListboxSelect>>", self.on_select_item)

        left_buttons = ttk.Frame(left)
        left_buttons.pack(fill="x", pady=(8, 0))

        ttk.Button(left_buttons, text="Open Patient Folder", command=self.open_patient_folder).pack(fill="x", pady=3)
        ttk.Button(left_buttons, text="Open PDF External", command=self.open_pdf_external).pack(fill="x", pady=3)

        right = ttk.Frame(body)
        right.pack(side="right", fill="both", expand=True)

        info = ttk.LabelFrame(right, text="Selected File", padding=8)
        info.pack(fill="x")

        self.info_var = tk.StringVar(value="No file selected")
        ttk.Label(info, textvariable=self.info_var, font=("Segoe UI", 10, "bold")).pack(anchor="w")

        action = ttk.LabelFrame(right, text="Identify and Apply", padding=8)
        action.pack(fill="x", pady=(8, 8))

        row1 = ttk.Frame(action)
        row1.pack(fill="x")

        ttk.Label(row1, text="Correct Type:", width=14).pack(side="left")
        self.type_combo = ttk.Combobox(
            row1,
            textvariable=self.selected_type_var,
            values=DOC_TYPES,
            state="readonly",
            width=22
        )
        self.type_combo.pack(side="left")
        self.type_combo.bind("<<ComboboxSelected>>", self.refresh_keyword_suggestions)

        ttk.Label(row1, text="Training keywords:", width=18).pack(side="left", padx=(18, 0))
        ttk.Entry(row1, textvariable=self.keywords_var).pack(side="left", fill="x", expand=True)

        row2 = ttk.Frame(action)
        row2.pack(fill="x", pady=(8, 0))

        ttk.Button(row2, text="Apply Selected Type", command=self.apply_selected_type).pack(side="left")
        ttk.Button(row2, text="Skip", command=self.skip_current).pack(side="left", padx=(8, 0))
        ttk.Button(row2, text="Delete from Queue Only", command=self.remove_from_queue_only).pack(side="left", padx=(8, 0))
        ttk.Button(row2, text="Use Suggested Keywords", command=self.use_suggested_keywords).pack(side="left", padx=(8, 0))

        row3 = ttk.Frame(action)
        row3.pack(fill="x", pady=(8, 0))
        self.visual_training_check = ttk.Checkbutton(
            row3,
            text="Use successful correction as visual training sample",
            variable=self.use_visual_training_var,
        )
        self.visual_training_check.pack(side="left")

        ttk.Label(action, textvariable=self.suggested_keywords_var, foreground="#5B677A").pack(anchor="w", pady=(8, 0))
        ttk.Label(
            action,
            textvariable=self.visual_suggestion_var,
            foreground="#315A8A",
            wraplength=820,
        ).pack(anchor="w", pady=(5, 0))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(action, textvariable=self.status_var, foreground="blue").pack(anchor="w", pady=(8, 0))

        preview_card = ttk.LabelFrame(right, text="PDF Preview", padding=8)
        preview_card.pack(fill="both", expand=True)

        toolbar = ttk.Frame(preview_card)
        toolbar.pack(fill="x")

        ttk.Button(toolbar, text="Zoom -", command=self.zoom_out).pack(side="left")
        ttk.Button(toolbar, text="Zoom +", command=self.zoom_in).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="Fit Width", command=self.fit_width).pack(side="left", padx=(6, 0))

        self.zoom_var = tk.StringVar(value=f"Zoom: {self.zoom_percent}%")
        ttk.Label(toolbar, textvariable=self.zoom_var).pack(side="left", padx=(12, 0))

        canvas_frame = ttk.Frame(preview_card)
        canvas_frame.pack(fill="both", expand=True, pady=(8, 0))

        self.canvas = tk.Canvas(canvas_frame, bg="gray25")
        self.v_scroll = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        self.h_scroll = ttk.Scrollbar(canvas_frame, orient="horizontal", command=self.canvas.xview)

        self.canvas.configure(yscrollcommand=self.v_scroll.set, xscrollcommand=self.h_scroll.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.v_scroll.grid(row=0, column=1, sticky="ns")
        self.h_scroll.grid(row=1, column=0, sticky="ew")

        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        self.canvas.bind("<MouseWheel>", self.mousewheel_scroll)

    # ======================================================
    # SCAN QUEUE
    # ======================================================
    def browse_output_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output_folder_var.get() or "C:\\")
        if folder:
            self.output_folder_var.set(folder)
            self.scan_review_files()

    def scan_review_files(self):
        output_folder = safe_path(self.output_folder_var.get())

        self.review_items = []

        if not os.path.exists(output_folder):
            messagebox.showerror("Output Folder Not Found", output_folder)
            return

        for root, dirs, files in os.walk(output_folder):
            # Skip backup/review folders
            if "_reviewed_unknowns" in root:
                continue

            for file in files:
                if not file.lower().endswith(".pdf"):
                    continue

                name_upper = file.upper()

                matched = False
                for kw in REVIEW_KEYWORDS:
                    if kw.upper() in name_upper:
                        matched = True
                        break

                if not matched:
                    continue

                path = os.path.join(root, file)

                self.review_items.append({
                    "path": path,
                    "patient_folder": root,
                    "filename": file,
                    "page_count": get_pdf_page_count(path),
                })

        self.queue_listbox.delete(0, "end")

        for item in self.review_items:
            patient = os.path.basename(item["patient_folder"])
            pages = item["page_count"] if item["page_count"] is not None else "?"
            self.queue_listbox.insert("end", f"{patient} | {item['filename']} | {pages} page(s)")

        self.status_var.set(f"Found {len(self.review_items)} file(s) for review.")

        if self.review_items:
            self.queue_listbox.selection_set(0)
            self.on_select_item()

    def on_select_item(self, event=None):
        sel = self.queue_listbox.curselection()

        if not sel:
            return

        self.current_index = sel[0]
        item = self.review_items[self.current_index]

        path = item["path"]
        pages = item["page_count"] if item["page_count"] is not None else "?"

        self.info_var.set(
            f"Patient: {os.path.basename(item['patient_folder'])}\n"
            f"File: {item['filename']}\n"
            f"Pages: {pages}\n"
            f"Path: {path}"
        )

        self.keywords_var.set("")
        self.selected_type_var.set(self.guess_default_type(item))
        self.refresh_keyword_suggestions()

        self.load_pdf_preview(path)
        self.start_visual_analysis(item)

    def guess_default_type(self, item):
        filename = item["filename"]
        f = filename.upper()
        text_hint = self.extract_pdf_text_hint(item["path"]).upper()

        if resolve_soa2_text is not None:
            soa2_resolution = resolve_soa2_text(text_hint)
            if soa2_resolution.doc_type in ("SOA2", "SOA2_page1", "SOA2_page2"):
                return soa2_resolution.doc_type

        if self.looks_like_soa1_text(text_hint):
            return "SOA1"

        if "SOA2_PAGE1" in f:
            return "SOA2_page1"

        if "SOA2_PAGE2" in f:
            return "SOA2_page2"

        if "MRF" in f:
            return "MRF_page2"

        if "DTR" in f:
            return "DTR"

        if "MMC" in f or "MARRIAGE" in text_hint:
            return "MMC"

        if "SOA1" in f:
            return "SOA1"

        return "SKIP"

    @staticmethod
    def extract_pdf_text_hint(path, max_chars=8000, max_pages=2):
        if fitz is None:
            return ""
        try:
            with fitz.open(path) as doc:
                if doc.page_count == 0:
                    return ""
                chunks = []
                for page_index in range(min(doc.page_count, max_pages)):
                    chunks.append(doc[page_index].get_text("text"))
                return "\n".join(chunks)[:max_chars]
        except Exception:
            return ""

    def open_visual_learning_manager(self):
        if self.visual_learner is None or VisualLearningManager is None:
            messagebox.showwarning(
                "Visual Learning",
                "Visual learning is unavailable. Check the Live Processing Logs and installed requirements.",
            )
            return
        VisualLearningManager(self, self.visual_learner)

    def start_visual_analysis(self, item):
        self.visual_analysis_token += 1
        token = self.visual_analysis_token
        item.pop("visual_features", None)
        item.pop("visual_prediction", None)

        if self.visual_learner is None:
            self.visual_suggestion_var.set("Visual learning unavailable")
            return

        path = item.get("path", "")
        self.visual_suggestion_var.set("Visual learning: analyzing layout in background...")

        def worker():
            features, prediction = self.visual_learner.predict(
                path,
                deterministic_type="UNKNOWN",
                record=True,
            )
            try:
                self.after(
                    0,
                    lambda: self.finish_visual_analysis(
                        token, path, features, prediction
                    ),
                )
            except Exception:
                pass

        threading.Thread(
            target=worker,
            name="unknown-visual-analysis",
            daemon=True,
        ).start()

    def finish_visual_analysis(self, token, path, features, prediction):
        if token != self.visual_analysis_token:
            return
        item = self.get_current_item()
        if not item or os.path.abspath(item.get("path", "")) != os.path.abspath(path):
            return
        item["visual_features"] = features
        item["visual_prediction"] = prediction

        if prediction.has_suggestion:
            evidence = ", ".join(prediction.evidence)
            blocked = f" | Guard: {prediction.blocked_reason}" if prediction.blocked_reason else ""
            self.visual_suggestion_var.set(
                f"Visual suggestion: {prediction.summary()} | {evidence}{blocked}"
            )
            if self.selected_type_var.get().strip() == "SKIP":
                self.selected_type_var.set(prediction.predicted_type)
                self.refresh_keyword_suggestions()
        else:
            self.visual_suggestion_var.set(
                f"Visual learning: {prediction.blocked_reason or 'no trusted suggestion'}"
            )

    def refresh_keyword_suggestions(self, event=None):
        item = self.get_current_item()

        if not item:
            self.current_suggested_keywords = []
            self.suggested_keywords_var.set("Suggested keywords: none")
            return

        selected_type = self.selected_type_var.get().strip()
        self.current_suggested_keywords = self.suggest_training_keywords(item, selected_type)

        if self.current_suggested_keywords:
            suggested = ", ".join(self.current_suggested_keywords)
            self.suggested_keywords_var.set(f"Suggested keywords: {suggested}")
            if not self.keywords_var.get().strip():
                self.keywords_var.set(suggested)
        else:
            self.suggested_keywords_var.set(
                "Suggested keywords: none safe found. Add a unique phrase manually if needed."
            )

    def use_suggested_keywords(self):
        if not self.current_suggested_keywords:
            messagebox.showinfo(
                "No Suggestions",
                "No safe unique keyword suggestion was found for this file."
            )
            return

        self.keywords_var.set(", ".join(self.current_suggested_keywords))

    def suggest_training_keywords(self, item, selected_type, limit=5):
        text = item.get("text_hint")
        if text is None:
            text = self.extract_pdf_text_hint(item["path"])
            item["text_hint"] = text

        if not text:
            return []

        other_texts = []
        for other in self.review_items:
            if other is item:
                continue

            other_text = other.get("text_hint")
            if other_text is None:
                other_text = self.extract_pdf_text_hint(other["path"], max_chars=4000, max_pages=1)
                other["text_hint"] = other_text

            if other_text:
                other_texts.append(other_text)

        existing_keyword_types = get_existing_training_keyword_types()
        candidates = self.extract_training_phrase_candidates(text)
        scored = []
        seen = set()

        for phrase in candidates:
            clean = normalize_training_text(phrase)
            if clean in seen:
                continue
            seen.add(clean)

            if not is_safe_training_phrase(clean):
                continue

            # Safety guard: skip phrases already used for a different document type.
            trained_types = existing_keyword_types.get(clean, set())
            if trained_types and selected_type not in trained_types:
                continue

            # Safety guard: skip phrases that also appear in other review files.
            if any(phrase_exists_in_text(clean, other_text) for other_text in other_texts):
                continue

            scored.append((self.score_training_phrase(clean, selected_type), clean))

        scored.sort(reverse=True)
        return [phrase for _, phrase in scored[:limit]]

    @staticmethod
    def extract_training_phrase_candidates(text):
        clean_lines = []

        for raw_line in str(text or "").splitlines():
            line = normalize_training_text(raw_line)
            if line:
                clean_lines.append(line)

        candidates = []

        for line in clean_lines:
            if 4 <= len(line) <= 70:
                candidates.append(line)

            words = [
                word for word in re.findall(r"[A-Z][A-Z0-9\-]+", line)
                if len(word) >= 3
            ]

            for size in range(2, min(6, len(words)) + 1):
                for start in range(0, len(words) - size + 1):
                    candidates.append(" ".join(words[start:start + size]))

        return candidates

    @staticmethod
    def score_training_phrase(phrase, selected_type):
        score = 0
        words = phrase.split()

        if 2 <= len(words) <= 5:
            score += 10

        if 8 <= len(phrase) <= 45:
            score += 8

        strong_markers = {
            "MMC": ["CERTIFICATE OF MARRIAGE", "MARRIAGE CERTIFICATE", "CIVIL REGISTRAR"],
            "DTR": ["ECG", "ELECTROCARDIOGRAM", "LABORATORY", "HEMATOLOGY", "URINALYSIS", "X RAY", "XRAY"],
            "ANR": ["ANESTHESIA RECORD", "ANAESTHESIA RECORD", "ANESTHETIC", "INDUCTION"],
            "OPR": ["OPERATING ROOM RECORD", "OPERATION RECORD", "DELIVERY ROOM RECORD"],
            "COE": ["BENEFIT ELIGIBILITY", "HCI PORTAL", "TEAMPHILHEALTH"],
            "SOA1": ["PLEASE PAY AT THE CASHIER"],
            "SOA2": ["SOA REFERENCE", "STATEMENT OF ACCOUNT", "CONFORME", "PREPARED BY", "SIGNATURE OVER PRINTED NAME"],
            "SOA2_page1": ["SOA REFERENCE", "STATEMENT OF ACCOUNT"],
            "SOA2_page2": ["CONFORME", "PREPARED BY", "SIGNATURE OVER PRINTED NAME", "PATIENT REPRESENTATIVE"],
            "MRF_page1": ["MEMBER REGISTRATION"],
            "MRF_page2": ["MEMBER REGISTRATION"],
        }

        for marker in strong_markers.get(selected_type, []):
            if marker in phrase:
                score += 25

        if any(char.isdigit() for char in phrase):
            score -= 3

        return score

    @staticmethod
    def looks_like_soa1_text(text):
        if not text:
            return False
        has_soa = (
            "STATEMENT OF ACCOUNT" in text
            or "SUMMARY OF ACCOUNT" in text
            or "SOA" in text
        )
        has_patient_billing = (
            "HOSPITAL NO" in text
            or "HOSPITAL NUMBER" in text
            or "PATIENT NAME" in text
            or "PHILHEALTH" in text
        )
        page2_markers = (
            "PROFESSIONAL FEE" in text
            or "DRUGS AND MEDICINES" in text
            or "LABORATORY" in text
            or "ROOM AND BOARD" in text
        )
        return has_soa and has_patient_billing and not page2_markers

    # ======================================================
    # PDF PREVIEW
    # ======================================================
    def load_pdf_preview(self, path):
        self.canvas.delete("all")
        self.current_pdf_image = None
        self.current_photo = None

        if fitz is None or ImageTk is None:
            self.canvas.create_text(
                30,
                30,
                text="PyMuPDF/Pillow not installed.\nRun: pip install pymupdf pillow",
                anchor="nw",
                fill="white"
            )
            return

        try:
            doc = fitz.open(path)
            page = doc[0]

            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            temp = os.path.join(BASE_DIR, "_unknown_preview_temp.png")
            pix.save(temp)

            self.current_pdf_image = Image.open(temp).convert("RGB")
            self.render_preview()

        except Exception as e:
            self.canvas.create_text(30, 30, text=str(e), anchor="nw", fill="white")

    def render_preview(self):
        if self.current_pdf_image is None:
            return

        scale = self.zoom_percent / 100.0

        w = max(1, int(self.current_pdf_image.width * scale))
        h = max(1, int(self.current_pdf_image.height * scale))

        img = self.current_pdf_image.resize((w, h), Image.LANCZOS)
        self.current_photo = ImageTk.PhotoImage(img)

        self.canvas.delete("all")
        self.canvas.create_image(20, 20, image=self.current_photo, anchor="nw")
        self.canvas.config(scrollregion=(0, 0, w + 60, h + 60))
        self.zoom_var.set(f"Zoom: {self.zoom_percent}%")

    def zoom_in(self):
        self.zoom_percent = min(200, self.zoom_percent + 10)
        self.render_preview()

    def zoom_out(self):
        self.zoom_percent = max(20, self.zoom_percent - 10)
        self.render_preview()

    def fit_width(self):
        if self.current_pdf_image is None:
            return

        self.update_idletasks()
        canvas_w = max(200, self.canvas.winfo_width() - 80)
        self.zoom_percent = max(20, int((canvas_w / self.current_pdf_image.width) * 100))
        self.render_preview()

    def mousewheel_scroll(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ======================================================
    # ACTIONS
    # ======================================================
    def get_current_item(self):
        if self.current_index is None:
            return None

        if self.current_index < 0 or self.current_index >= len(self.review_items):
            return None

        return self.review_items[self.current_index]

    def open_patient_folder(self):
        item = self.get_current_item()

        if not item:
            return

        os.startfile(item["patient_folder"])

    def open_pdf_external(self, silent=False):
        item = self.get_current_item()

        if not item:
            return

        try:
            os.startfile(item["path"])
        except Exception as e:
            if not silent:
                messagebox.showerror("Open PDF Error", str(e))

    def apply_selected_type(self):
        item = self.get_current_item()

        if not item:
            messagebox.showinfo("No Selection", "Select a file first.")
            return

        selected_type = self.selected_type_var.get().strip()

        if selected_type == "SKIP":
            self.skip_current()
            return

        source_path = item["path"]
        patient_folder = item["patient_folder"]
        visual_features = item.get("visual_features")
        visual_payload = None
        visual_prediction = item.get("visual_prediction")
        prediction_id = getattr(visual_prediction, "prediction_id", None)

        if (
            self.visual_learner is not None
            and self.use_visual_training_var.get()
            and selected_type in LEARNABLE_TYPES
            and visual_features is None
        ):
            try:
                with open(source_path, "rb") as source_stream:
                    visual_payload = source_stream.read()
            except Exception as exc:
                self.status_var.set(f"Review will continue; visual sample unavailable: {exc}")

        try:
            self.last_pdfa_targets = []
            action = self.apply_type_action(patient_folder, source_path, selected_type)

            # After merge/rename, automatically reconvert final PDFs to PDF/A read-only.
            #
            # IMPORTANT FIX:
            # Before:
            #   subprocess.run() was executed in GUI thread.
            #   If pdfa.py takes long, tkinter becomes "Not Responding".
            #
            # Now:
            #   run PDF/A conversion AFTER GUI returns to event loop.
            #
            self.status_var.set("Applying PDF/A read-only conversion...")
            self.update_idletasks()

            self.after(
                100,
                lambda: self.run_pdfa_after_apply(
                    action,
                    patient_folder,
                    source_path,
                    selected_type,
                    visual_features=visual_features,
                    visual_payload=visual_payload,
                    prediction_id=prediction_id,
                    visual_training_enabled=bool(self.use_visual_training_var.get()),
                    training_keywords=self.keywords_var.get(),
                ),
            )

            return

        except Exception as e:
            messagebox.showerror("Apply Error", str(e))



    def run_pdfa_after_apply(
        self,
        action,
        patient_folder,
        source_path,
        selected_type,
        *,
        visual_features=None,
        visual_payload=None,
        prediction_id=None,
        visual_training_enabled=False,
        training_keywords="",
    ):
        """
        Run integrated PDF/A conversion in a background thread.
        Prevents Not Responding.
        """
        targets = getattr(self, "last_pdfa_targets", [])

        def worker():
            try:
                pdfa_ok, pdfa_msg = run_pdfa_converter_after_review(targets)
                final_action = action + "\n\n[PDF/A]\n" + pdfa_msg

                visual_message = ""
                if self.visual_learner is not None:
                    try:
                        self.visual_learner.record_feedback(prediction_id, selected_type)
                        if visual_training_enabled and selected_type in LEARNABLE_TYPES:
                            features = visual_features
                            if features is None and visual_payload:
                                features = self.visual_learner.extract_features(visual_payload)
                            if features is not None:
                                training = self.visual_learner.confirm_sample(
                                    features,
                                    selected_type,
                                    source_name=os.path.basename(source_path),
                                    source_path=source_path,
                                )
                                visual_message = training.message
                            else:
                                visual_message = "Visual sample was not available; review action was still completed"
                    except Exception as visual_error:
                        visual_message = (
                            "Review completed, but visual learning was deferred: "
                            + str(visual_error)
                        )

                if visual_message:
                    final_action += "\n\n[VISUAL LEARNING]\n" + visual_message

                save_training_record(
                    patient_folder=patient_folder,
                    source_file=source_path,
                    selected_type=selected_type,
                    action=final_action,
                    keywords=training_keywords
                )

                def done_ui():
                    if pdfa_ok:
                        messagebox.showinfo("Applied", final_action)
                    else:
                        messagebox.showwarning("Applied with PDF/A Warning", final_action)

                    self.scan_review_files()
                    self.status_var.set("Ready")

                self.after(0, done_ui)

            except Exception as e:
                error_message = str(e)
                self.after(0, lambda: messagebox.showerror("PDF/A Error", error_message))
                self.after(0, lambda: self.status_var.set("PDF/A failed"))

        threading.Thread(target=worker, daemon=True).start()



    def apply_type_action(self, patient_folder, source_path, selected_type):
        selected_type = selected_type.strip()

        if selected_type == "DTR":
            return self.apply_dtr(patient_folder, source_path)

        if selected_type == "MRF_page2":
            return self.apply_mrf_page2(patient_folder, source_path)

        if selected_type == "MRF_page1":
            return self.apply_single_rename(patient_folder, source_path, "MRF_page1.pdf")

        if selected_type == "SOA2_page2":
            return self.apply_soa2_page2(patient_folder, source_path)

        if selected_type == "SOA2":
            return self.apply_single_rename(patient_folder, source_path, "SOA2.pdf")

        if selected_type == "SOA2_page1":
            return self.apply_single_rename(patient_folder, source_path, "SOA2_page1.pdf")

        if selected_type == "OTHER":
            return self.apply_single_rename(patient_folder, source_path, "OTHER.pdf")

        return self.apply_single_rename(patient_folder, source_path, f"{selected_type}.pdf")

    def apply_dtr(self, patient_folder, source_path):
        dtr_path = os.path.join(patient_folder, "DTR.pdf")

        if os.path.abspath(source_path) == os.path.abspath(dtr_path):
            return "Already DTR.pdf. No action needed."

        if os.path.exists(dtr_path):
            merge_pdfs(dtr_path, [dtr_path, source_path])
            self.last_pdfa_targets = [dtr_path]
            moved = move_to_reviewed_folder(source_path)
            return f"Merged into existing DTR.pdf\nMoved reviewed source to:\n{moved}"

        dst = unique_path(dtr_path)
        shutil.move(source_path, dst)
        self.last_pdfa_targets = [dst]
        return f"Saved as DTR.pdf:\n{dst}"

    def apply_mrf_page2(self, patient_folder, source_path):
        page1 = os.path.join(patient_folder, "MRF_page1.pdf")
        final_mrf = os.path.join(patient_folder, "MRF.pdf")

        if not os.path.exists(page1):
            return self.apply_single_rename(patient_folder, source_path, "MRF_page2.pdf")

        page_count = get_pdf_page_count(page1)

        if page_count != 1:
            raise RuntimeError(
                "MRF_page1 exists but is not exactly 1 page.\n"
                f"Detected pages: {page_count}\n\n"
                "Safety rule: merge only if MRF_page1 is exactly 1 page."
            )

        merge_pdfs(final_mrf, [page1, source_path])
        self.last_pdfa_targets = [final_mrf]

        reviewed1 = move_to_reviewed_folder(page1)
        reviewed2 = move_to_reviewed_folder(source_path)

        return (
            "Merged MRF_page1 + selected MRF_page2 -> MRF.pdf\n"
            f"Reviewed files moved:\n{reviewed1}\n{reviewed2}"
        )

    def apply_soa2_page2(self, patient_folder, source_path):
        page1 = os.path.join(patient_folder, "SOA2_page1.pdf")
        final_soa2 = os.path.join(patient_folder, "SOA2.pdf")

        if not os.path.exists(page1):
            return self.apply_single_rename(patient_folder, source_path, "SOA2_page2.pdf")

        page_count = get_pdf_page_count(page1)

        if page_count != 1:
            raise RuntimeError(
                "SOA2_page1 exists but is not exactly 1 page.\n"
                f"Detected pages: {page_count}\n\n"
                "Safety rule: merge only if SOA2_page1 is exactly 1 page."
            )

        merge_pdfs(final_soa2, [page1, source_path])
        self.last_pdfa_targets = [final_soa2]

        reviewed1 = move_to_reviewed_folder(page1)
        reviewed2 = move_to_reviewed_folder(source_path)

        return (
            "Merged SOA2_page1 + selected SOA2_page2 -> SOA2.pdf\n"
            f"Reviewed files moved:\n{reviewed1}\n{reviewed2}"
        )

    def apply_single_rename(self, patient_folder, source_path, target_filename):
        target = os.path.join(patient_folder, target_filename)

        if os.path.abspath(source_path) == os.path.abspath(target):
            return f"Already named {target_filename}. No action needed."

        if os.path.exists(target):
            target = unique_path(target)

        shutil.move(source_path, target)
        self.last_pdfa_targets = [target]

        return f"Saved as:\n{target}"

    def skip_current(self):
        item = self.get_current_item()

        if not item:
            return

        save_training_record(
            patient_folder=item["patient_folder"],
            source_file=item["path"],
            selected_type="SKIP",
            action="Skipped review",
            keywords=""
        )

        self.remove_from_queue_only()

    def remove_from_queue_only(self):
        if self.current_index is None:
            return

        self.review_items.pop(self.current_index)

        self.queue_listbox.delete(0, "end")

        for item in self.review_items:
            patient = os.path.basename(item["patient_folder"])
            pages = item["page_count"] if item["page_count"] is not None else "?"
            self.queue_listbox.insert("end", f"{patient} | {item['filename']} | {pages} page(s)")

        self.current_index = None
        self.canvas.delete("all")
        self.info_var.set("No file selected")

        if self.review_items:
            self.queue_listbox.selection_set(0)
            self.on_select_item()


if __name__ == "__main__":
    app = UnknownReviewManager()
    app.mainloop()
