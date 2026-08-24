import fitz
import numpy as np
import cv2
import os
import re
import stat
import subprocess
import shutil
import json
import time
import pytesseract
from PIL import Image
from pdf2image import convert_from_path
from PyPDF2 import PdfMerger, PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from rapidfuzz import fuzz

from core.admission_date_resolver import resolve_admission_dates_from_metadata
from core.admission_lookup import AdmissionLookupError, MySQLAdmissionLookup
from core.batch_tracker import BatchPatientTracker
from core.hospital_number_extractor import extract_soa1_hospital_number
from core.hospital_number_resolver import HospitalNumberResolution, resolve_hospital_number
from core.hbsys_connection import create_hbsys_connection
from core.patient_identity_resolver import (
    IdentityResolution,
    extract_patient_name_from_soa_text,
    resolve_identity_from_soa2,
)
from core.patient_folder_naming import (
    build_patient_folder_name as build_canonical_patient_folder_name,
    sanitize_folder_component as sanitize_canonical_folder_component,
)
from core.patient_review_queue import review_queue
from core.patient_validator import PatientValidationContext, patient_validator
from core.review_staging import ReviewStagingManager
from core.soa2_resolver import resolve_soa2_text

try:
    from core.visual_document_learner import VisualDocumentLearner
except Exception:
    VisualDocumentLearner = None

try:
    import tkinter as tk
    from tkinter import messagebox, simpledialog, ttk
except Exception:
    tk = None
    messagebox = None
    simpledialog = None
    ttk = None

try:
    import pymysql
except ImportError:
    pymysql = None

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
POPPLER_PATH = r"C:\poppler\Library\bin"
GHOSTSCRIPT_PATH = r"C:\Program Files\gs\gs10.07.0\bin\gswin64c.exe"

SCAN_FOLDER = os.environ.get("CLAIMS_SCAN_FOLDER", r"C:\claims_bot\scans")
OUTPUT_FOLDER = os.environ.get("CLAIMS_OUTPUT_FOLDER", r"C:\claims_bot\output")
SIGNATURE_FOLDER = os.environ.get(
    "CLAIMS_SIGNATURE_FOLDER", r"C:\claims_bot\signatures"
)
BACKUP_FOLDER = os.environ.get(
    "CLAIMS_BACKUP_FOLDER", r"C:\claims_bot\backup_originals"
)
REVIEW_STAGING_FOLDER = os.environ.get(
    "CLAIMS_REVIEW_STAGING_FOLDER", r"C:\claims_bot\review_staging"
)

# ==================================================
# DYNAMIC DOCTOR MANAGER CONFIG
# ==================================================
# GUI Doctor Manager saves here.
# Bot will read this automatically.
DOCTORS_CONFIG_PATH = r"C:\claims_bot\doctors_config.json"
UNKNOWN_TRAINING_PATH = r"C:\claims_bot\unknown_training_data.json"
CLAIMS_GUI_CONFIG_PATH = r"C:\claims_bot\claims_gui_config.json"

# ==================================================
# DATABASE CONFIG - READ ONLY MYSQL USER
# ==================================================
# IMPORTANT:
# Gumamit ng MySQL user na SELECT lang ang permission.
# Palitan ang DB_NAME / DB_USER / DB_PASSWORD ayon sa actual setup mo.
DB_HOST = "192.168.1.2"
DB_PORT = 3306
DB_USER = "root"
DB_PASSWORD = "root"
DB_NAME = "hbsys_edh"

MAX_SIZE_KB = 1000

current_patient = None
current_patient_base_name = None
current_hospital_no = None
batch_patient_tracker = BatchPatientTracker()
review_staging_manager = ReviewStagingManager(
    review_queue,
    staging_root=REVIEW_STAGING_FOLDER,
)
visual_document_learner = None
if VisualDocumentLearner is not None:
    try:
        visual_document_learner = VisualDocumentLearner()
    except Exception as visual_init_error:
        print("[VISUAL LEARNING] Unavailable:", visual_init_error)

# ============================================
# PROCESS MODE
# ============================================

PROCESS_MODE = "single"     # single / multiple
CONFIRM_PATIENT = True

DOCTOR_SETTINGS_LOADED_FROM_JSON_ONCE = False




soa2_pages = {}
mrf_pages = {}
pbc_pages = {}
cf2_pages = {}
cf2_page_texts = {}

DOC_KEYWORDS = {
    "CSF": ["claim signature form", "csf"],
    # CF2 must be page-specific. A generic "cf2" fallback is unsafe because
    # "Use additional CF2 if necessary" appears on both pages.
    "CF2": [],
    "MRF": ["member registration"],
    "OPR": ["operating room record", "delivery room record"],
    "ANR": [
        "anesthesia record",
        "anaesthesia record",
        "anesthetic agent",
        "anaesthetic agent",
        "detailed technique",
        "induction",
        "maintenance",
        "emergence",
        "fluid summary",
        "urine output in o.r",
        "condition of patient on departure"
    ],
    "MMC": [
        "certificate of marriage",
        "marriage certificate",
        "municipal form no. 97",
        "office of the civil registrar",
        "local civil registrar",
    ],
    "COE": [
        "hci portal reference no",
        "hci portal reference",
        "philhealth benefit eligibility",
        "philhealth benefit eligibility form",
        "teamphilhealth"
    ],
}

DTR_KEYWORDS = [
    "x-ray", "xray", "ct scan", "ultrasound",
    "hematology", "urinalysis", "diagnostic",
    "examination", "mila amor", "electrocardiogram", "electro", "ecg", "normal sinus rhythm",
    "laboratory result", "clinical chemistry",
    "cross-matching", "parasitology", "2d echocardiography", "2-d echocardiogram",
    "2d echocardiogram", "echocardiography", "echocardiogram",
    "blood bank result", "laboratory department", "blood typing", "blood chemistry", "ultrasound", "serology", "radiographic report"
]

MULTI_PAGE = ["DTR"]

DOCTOR_SETTINGS = {
    "GARCIA_MICHELLE": {
        "aliases": ["GARCIA, MICHELLE", "GARCIA MICHELLE", "MICHELLE GARCIA"],
        "file": "garcia_michelle.png"
    },
    "GARCIA_JOSE_NARCISO": {
        "aliases": ["GARCIA, JOSE NARCISO", "GARCIA JOSE NARCISO", "JOSE NARCISO GARCIA"],
        "file": "garcia_jose_narciso.png",
        "x": 240,
        "y": 180,
        "max_w": 120,
        "max_h": 50
    },
    "AY_AYEN_OLIVIA": {
        "aliases": [
            "OLIVIA G AY-AYEN",
            "OLIVIA G AY AYEN",
            "OLIVIA AY-AYEN",
            "OLIVIA AY AYEN",
            "AY-AYEN OLIVIA",
            "AY AYEN OLIVIA",
            "AY-AYEN, OLIVIA",
            "OLIVIA G AY-AYEN MD",
            "OLIVIA G AY AYEN MD"
        ],
        "file": "ay_ayen_olivia.png",
        "x": 270,
        "y": 190,
        "max_w": 95,
        "max_h": 30
    },
    "ESPIRITU": {
        "aliases": [
            "MARIA CRISTINA DEL ROSARIO ESPIRITU",
            "MARIA CRISTINA ESPIRITU",
            "ESPIRITU MARIA CRISTINA",
            "DR MARIA CRISTINA ESPIRITU"
        ],
        "file": "espiritu.png",
        "max_w": 130,
        "max_h": 35
    },
    "BALBOA": {
        "aliases": ["BALBOA"],
        "file": "balboa.png"
    },
    "GAFFUD_RHODA_JACQUELINE": {
        "aliases": [
            "RHODA JACQUELINE P GAFFUD",
            "RHODA JACQUELINE GAFFUD",
            "GAFFUD RHODA JACQUELINE",
            "GAFFUD, RHODA JACQUELINE",
            "RHODA GAFFUD"
        ],
        "file": "gaffud_rhoda_jacqueline.png"
    },
    "GAFFUD_YMMANDAH": {
        "aliases": ["GAFFUD, YMMANDAH", "GAFFUD YMMANDAH", "YMMANDAH GAFFUD"],
        "file": "gaffud_ymmandah.png"
    },
    "SUMAWANG_RIVERA": {
        "aliases": ["SUMAWANG-RIVERA", "SUMAWANG RIVERA", "SUMAWANG", "RIVERA"],
        "file": "sumawang_rivera.png"
    },
    "AYESHA_BEA_FEDERIZO": {
        "aliases": ["FEDERIZO, AYESHA BEA", "AYESHA BEA", "FEDERIZO"],
        "file": "ayesha_bea_federizo.png",
        "x": 250,
        "y": 155,
        "max_w": 150,
        "max_h": 65,

        # Separate signature-check box for CSF Part IV.
        # This avoids false SKIP from printed doctor name/date/underlines.
        "check_x": 250,
        "check_y": 155,
        "check_max_w": 150,
        "check_max_h": 65
    },
    "MARIA_ELAINE_TUPONG": {
        "aliases": ["MARIA ELAINE, TUPONG", "MARIA ELAINE"],
        "file": "maria_elaine_tupong.png",
        "x": 310,
        "y": 175,
        "max_w": 95,
        "max_h": 30,
        
        # CF2 PAGE 2 SIGNATURE CONFIG
        "cf2_x": 120,
        "cf2_y": 740,
        "cf2_max_w": 120,
        "cf2_max_h": 25
       
    }
}


# ==================================================
# LOAD DOCTORS FROM GUI JSON
# ==================================================
def parse_aliases(value):
    """
    Accept aliases saved by GUI as:
        "ESPIRITU | MARIA CRISTINA ESPIRITU"
    or list:
        ["ESPIRITU", "MARIA CRISTINA ESPIRITU"]
    """
    if value is None:
        return []

    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]

    value = str(value).strip()

    if not value:
        return []

    parts = re.split(r"[|,;\n]+", value)

    return [p.strip() for p in parts if p.strip()]



GENERIC_DOCTOR_ALIASES = {
    "MD", "M D", "DR", "DRA", "DOC", "DOCTOR",
    "PHYSICIAN", "LICENSE", "LIC", "PRC"
}


def is_valid_doctor_alias(alias):
    """
    Prevent false doctor detection from generic aliases like:
        MD, DR, DOCTOR

    This was the cause of wrong signature:
        ALLAN_P_TUPONG_MD matched alias "MD" with score 100
        even though Part IV doctor was EMY-ANN SUMAWANG-RIVERA.
    """
    alias_clean = normalize_text(str(alias or ""))

    if not alias_clean:
        return False

    if alias_clean in GENERIC_DOCTOR_ALIASES:
        return False

    # Very short aliases are dangerous for fuzzy matching.
    if len(alias_clean) < 4:
        return False

    # Must contain at least 4 letters total.
    letters = re.sub(r"[^A-Z]", "", alias_clean)
    if len(letters) < 4:
        return False

    return True


def safe_int(value, default=None):
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def doctor_name_to_key(name):
    name = str(name or "").upper()
    name = re.sub(r"[^A-Z0-9]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")

    if not name:
        name = "DOCTOR"

    return name


def load_doctor_settings_from_json():
    """
    Load doctor settings from C:\\claims_bot\\doctors_config.json.

    GUI fields supported:
        name
        aliases
        signature_file
        part_iv_x
        part_iv_y
        part_v_x
        part_v_y
        cf2_x
        cf2_y
        cf2_max_w
        cf2_max_h
        cf2_hci_x
        cf2_hci_y
        cf2_hci_max_w
        cf2_hci_max_h
        enabled
    """
    if not os.path.exists(DOCTORS_CONFIG_PATH):
        print("[DOCTOR CONFIG] doctors_config.json not found. Using hardcoded DOCTOR_SETTINGS.")
        return None

    try:
        with open(DOCTORS_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            print("[DOCTOR CONFIG] Invalid doctors_config.json format. Expected list.")
            return None

        loaded = {}

        for item in data:
            if not isinstance(item, dict):
                continue

            if not item.get("enabled", True):
                continue

            name = str(item.get("name") or "").strip()
            signature_file = str(item.get("signature_file") or item.get("file") or "").strip()

            aliases = parse_aliases(item.get("aliases"))

            if name and is_valid_doctor_alias(name):
                aliases.append(name)

            # Final safety filter. This prevents aliases like "MD" from matching everyone.
            aliases = list(dict.fromkeys([a for a in aliases if is_valid_doctor_alias(a)]))

            if not name and not aliases:
                continue

            if not signature_file:
                print("[DOCTOR CONFIG] Skipped doctor without signature file:", name)
                continue

            key = item.get("key") or doctor_name_to_key(name or aliases[0])

            part_iv_x = safe_int(item.get("part_iv_x"), safe_int(item.get("x"), 250))
            part_iv_y = safe_int(item.get("part_iv_y"), safe_int(item.get("y"), 190))

            max_w = safe_int(item.get("max_w"), 130)
            max_h = safe_int(item.get("max_h"), 35)

            cf2_x = safe_int(item.get("cf2_x"), None)
            cf2_y = safe_int(item.get("cf2_y"), None)
            cf2_max_w = safe_int(item.get("cf2_max_w"), None)
            cf2_max_h = safe_int(item.get("cf2_max_h"), None)

            loaded[key] = {
                "aliases": aliases,
                "file": signature_file,

                # Part IV placement used by existing signing logic
                "x": part_iv_x,
                "y": part_iv_y,
                "max_w": max_w,
                "max_h": max_h,

                # Part IV signature-check ROI
                "check_x": safe_int(item.get("check_x"), part_iv_x),
                "check_y": safe_int(item.get("check_y"), part_iv_y),
                "check_max_w": safe_int(item.get("check_max_w"), max_w),
                "check_max_h": safe_int(item.get("check_max_h"), max_h),

                # Reserved for future Part V per-doctor config
                "part_v_x": safe_int(item.get("part_v_x"), None),
                "part_v_y": safe_int(item.get("part_v_y"), None),

                # CF2 doctor/professional signature placement
                "cf2_x": cf2_x,
                "cf2_y": cf2_y,
                "cf2_max_w": cf2_max_w,
                "cf2_max_h": cf2_max_h,

                # CF2 Authorized HCI Representative placement.
                # Usually configured only on the Chief of Hospital/Rhoda row.
                "cf2_hci_x": safe_int(item.get("cf2_hci_x"), None),
                "cf2_hci_y": safe_int(item.get("cf2_hci_y"), None),
                "cf2_hci_max_w": safe_int(item.get("cf2_hci_max_w"), None),
                "cf2_hci_max_h": safe_int(item.get("cf2_hci_max_h"), None),
            }

        if not loaded:
            print("[DOCTOR CONFIG] No enabled doctors loaded. Using hardcoded DOCTOR_SETTINGS.")
            return None

        print("[DOCTOR CONFIG] Loaded doctors from GUI JSON:", len(loaded))
        print("[DOCTOR CONFIG] Doctors:", list(loaded.keys()))

        return loaded

    except Exception as e:
        print("[DOCTOR CONFIG ERROR]", str(e))
        print("[DOCTOR CONFIG] Using hardcoded DOCTOR_SETTINGS.")
        return None


def reload_dynamic_doctor_settings():
    """
    Load latest doctors_config.json into DOCTOR_SETTINGS.
    """
    global DOCTOR_SETTINGS

    loaded = load_doctor_settings_from_json()

    if loaded:
        DOCTOR_SETTINGS = loaded

    return DOCTOR_SETTINGS




# ==================================================
# GUI FEATURE TOGGLES
# ==================================================
def _bool_from_env_or_config(env_name, config_key, default=True):
    """
    Feature toggle source priority:
    1. Environment variable from GUI subprocess
    2. C:\claims_bot\claims_gui_config.json
    3. default
    """
    val = os.environ.get(env_name)

    if val is not None:
        return str(val).strip().lower() in ("1", "true", "yes", "on")

    try:
        if os.path.exists(CLAIMS_GUI_CONFIG_PATH):
            with open(CLAIMS_GUI_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            if config_key in cfg:
                return bool(cfg.get(config_key))
    except Exception as e:
        print("[GUI CONFIG ERROR]", str(e))

    return default


def is_auto_sign_enabled():
    return _bool_from_env_or_config("CLAIMS_ENABLE_AUTO_SIGN", "enable_auto_sign", True)


def _bool_from_env_or_config_with_fallback(env_name, config_key, fallback_config_key, default=True):
    val = os.environ.get(env_name)

    if val is not None:
        return str(val).strip().lower() in ("1", "true", "yes", "on")

    try:
        if os.path.exists(CLAIMS_GUI_CONFIG_PATH):
            with open(CLAIMS_GUI_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            if config_key in cfg:
                return bool(cfg.get(config_key))

            if fallback_config_key in cfg:
                return bool(cfg.get(fallback_config_key))
    except Exception as e:
        print("[GUI CONFIG ERROR]", str(e))

    return default


def is_auto_sign_csf_enabled():
    return _bool_from_env_or_config_with_fallback(
        "CLAIMS_ENABLE_AUTO_SIGN_CSF",
        "enable_auto_sign_csf",
        "enable_auto_sign",
        True
    )


def is_auto_sign_cf2_enabled():
    return _bool_from_env_or_config_with_fallback(
        "CLAIMS_ENABLE_AUTO_SIGN_CF2",
        "enable_auto_sign_cf2",
        "enable_auto_sign",
        True
    )


def is_date_signed_enabled():
    return _bool_from_env_or_config("CLAIMS_ENABLE_DATE_SIGNED", "enable_date_signed", True)


def is_backup_enabled():
    return _bool_from_env_or_config("CLAIMS_ENABLE_BACKUP", "enable_backup", True)



# ==================================================
# UNKNOWN TRAINER
# ==================================================
VALID_UNKNOWN_TRAINER_TYPES = [
    "CSF",
    "SOA1",
    "SOA2_page1",
    "SOA2_page2",
    "SOA2_page2_1",
    "MRF_page2_1",
    "DTR",
    "COE",
    "CF2_page1",
    "CF2_page2",
    "MRF_page1",
    "MRF_page2",
    "PBC_page1",
    "PBC_page2",
    "OPR",
    "ANR",
    "MMC",
    "OTHER",
    "UNKNOWN"
]


def load_unknown_training_data():
    try:
        if os.path.exists(UNKNOWN_TRAINING_PATH):
            with open(UNKNOWN_TRAINING_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                return data
    except Exception as e:
        print("[UNKNOWN TRAINER] Load error:", str(e))

    return []


def save_unknown_training_record(record):
    data = load_unknown_training_data()
    data.append(record)

    try:
        with open(UNKNOWN_TRAINING_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        print("[UNKNOWN TRAINER] Saved training rule ->", UNKNOWN_TRAINING_PATH)
    except Exception as e:
        print("[UNKNOWN TRAINER] Save error:", str(e))


def split_training_keywords(value):
    if not value:
        return []

    if isinstance(value, list):
        return [normalize_text(str(v)) for v in value if normalize_text(str(v))]

    parts = re.split(r"[,\n;|]+", str(value))
    return [normalize_text(p) for p in parts if normalize_text(p)]


def compact_training_text(value):
    return re.sub(r"[^A-Z0-9]+", "", normalize_text(value or ""))


def detect_doc_from_unknown_training(pdf_path, text):
    """
    Check user-trained UNKNOWN rules before asking again.

    Rule match:
    - if any saved keyword/phrase appears in OCR text
    - or if filename contains saved filename hint
    """
    data = load_unknown_training_data()

    if not data:
        return None

    clean = normalize_text(text or "")
    filename = os.path.basename(pdf_path or "").lower()
    source_path = os.path.abspath(pdf_path or "").lower()

    matched_types = set()

    for rule in data:
        if not isinstance(rule, dict):
            continue

        doc_type = rule.get("doc_type") or rule.get("selected_type")

        if not doc_type or doc_type in {"UNKNOWN", "SKIP", "OTHER"}:
            continue

        keywords = split_training_keywords(rule.get("keywords", []))
        filename_hint = str(rule.get("filename_contains") or "").strip().lower()
        source_path_hint = str(rule.get("source_path") or "").strip().lower()

        matched = False

        compact_clean = compact_training_text(clean)
        for kw in keywords:
            compact_kw = compact_training_text(kw)
            if kw and (kw in clean or (compact_kw and compact_kw in compact_clean)):
                matched = True
                break

        if filename_hint and filename_hint in filename:
            matched = True

        if source_path_hint and os.path.abspath(source_path_hint).lower() == source_path:
            matched = True

        if matched:
            matched_types.add(doc_type)

    if len(matched_types) == 1:
        matched_type = next(iter(matched_types))
        print("[UNKNOWN TRAINER] Matched trained rule:", matched_type)
        return matched_type

    if len(matched_types) > 1:
        print(
            "[UNKNOWN TRAINER] Conflicting trained rules; suggestion deferred:",
            ", ".join(sorted(matched_types)),
        )

    return None


def ask_unknown_doc_type(pdf_path, text):
    """
    Manual review popup for UNKNOWN PDF.
    User selects document type and optional keywords for future detection.
    """
    filename = os.path.basename(pdf_path)

    preview = (text or "").strip()
    preview = preview[:1800]

    # Console fallback
    if tk is None or ttk is None:
        print("\n[UNKNOWN TRAINER] UNKNOWN PDF:", filename)
        print("[UNKNOWN TRAINER] OCR preview:")
        print(preview[:800])
        print("[UNKNOWN TRAINER] Valid types:", ", ".join(VALID_UNKNOWN_TRAINER_TYPES))

        choice = input("Enter document type or press ENTER to keep UNKNOWN: ").strip()

        if not choice:
            return "UNKNOWN"

        choice_upper = choice.upper()

        for item in VALID_UNKNOWN_TRAINER_TYPES:
            if choice_upper == item.upper():
                return item

        print("[UNKNOWN TRAINER] Invalid type. Keeping UNKNOWN.")
        return "UNKNOWN"

    result = {
        "doc_type": "UNKNOWN",
        "keywords": ""
    }

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    win = tk.Toplevel(root)
    win.title("UNKNOWN Trainer")
    win.geometry("780x620")
    win.attributes("-topmost", True)
    win.grab_set()

    frm = ttk.Frame(win, padding=12)
    frm.pack(fill="both", expand=True)

    ttk.Label(
        frm,
        text="UNKNOWN PDF detected",
        font=("Segoe UI", 14, "bold")
    ).pack(anchor="w")

    ttk.Label(
        frm,
        text=f"File: {filename}",
        font=("Segoe UI", 10)
    ).pack(anchor="w", pady=(4, 10))

    ttk.Label(frm, text="OCR Preview:").pack(anchor="w")

    txt = tk.Text(frm, height=18, wrap="word", font=("Consolas", 9))
    txt.pack(fill="both", expand=True, pady=(4, 10))
    txt.insert("1.0", preview)
    txt.config(state="disabled")

    row = ttk.Frame(frm)
    row.pack(fill="x", pady=(4, 6))

    ttk.Label(row, text="Correct Document Type:", width=22).pack(side="left")

    doc_var = tk.StringVar(value="DTR")
    combo = ttk.Combobox(
        row,
        textvariable=doc_var,
        values=VALID_UNKNOWN_TRAINER_TYPES,
        state="readonly",
        width=22
    )
    combo.pack(side="left")

    ttk.Label(
        frm,
        text="Optional future keywords/phrases, separated by comma. Example: laboratory result, xray report, operating room record"
    ).pack(anchor="w", pady=(8, 2))

    keywords_var = tk.StringVar()
    keywords_entry = ttk.Entry(frm, textvariable=keywords_var)
    keywords_entry.pack(fill="x")

    button_row = ttk.Frame(frm)
    button_row.pack(fill="x", pady=(12, 0))

    def save_and_use():
        result["doc_type"] = doc_var.get().strip() or "UNKNOWN"
        result["keywords"] = keywords_var.get().strip()
        win.destroy()

    def keep_unknown():
        result["doc_type"] = "UNKNOWN"
        result["keywords"] = ""
        win.destroy()

    ttk.Button(button_row, text="Use Selected Type", command=save_and_use).pack(side="left")
    ttk.Button(button_row, text="Keep UNKNOWN", command=keep_unknown).pack(side="left", padx=(8, 0))

    win.wait_window()

    try:
        root.destroy()
    except Exception:
        pass

    doc_type = result["doc_type"]

    if doc_type and doc_type != "UNKNOWN":
        keywords = split_training_keywords(result.get("keywords"))

        record = {
            "doc_type": doc_type,
            "keywords": keywords,
            "filename_contains": "",
            "sample_file": filename
        }

        save_unknown_training_record(record)

    return doc_type or "UNKNOWN"


def make_writable(path):
    if os.path.exists(path):
        os.chmod(path, stat.S_IWRITE)


def safe_delete(path, retries=5, delay=0.5):
    """
    Safely delete a file even if it is read-only or temporarily locked.

    Fixes:
        PermissionError: [WinError 5] Access is denied

    Common causes:
        - PDF is read-only
        - PDF is still open in viewer
        - scanner/Windows is still holding the file briefly
    """
    if not path or not os.path.exists(path):
        return True

    last_error = None

    for attempt in range(1, retries + 1):
        try:
            make_writable(path)
            os.remove(path)
            return True

        except PermissionError as e:
            last_error = e
            print(f"[SAFE DELETE] Permission denied attempt {attempt}/{retries}: {path}")
            time.sleep(delay)

        except Exception as e:
            last_error = e
            print(f"[SAFE DELETE] Delete failed attempt {attempt}/{retries}: {path} -> {e}")
            time.sleep(delay)

    print("[SAFE DELETE WARNING] Could not delete file. It may be open/locked:")
    print("   ", path)
    print("   ", last_error)
    return False


def file_size_kb(path):
    return os.path.getsize(path) / 1024


def safe_move(src, dst):
    base, ext = os.path.splitext(dst)
    counter = 1
    new_dst = dst

    while os.path.exists(new_dst):
        new_dst = f"{base}_{counter}{ext}"
        counter += 1

    os.rename(src, new_dst)
    return new_dst


def sanitize_folder_component(value):
    return sanitize_canonical_folder_component(value)


def build_patient_folder_name(patient_name, hospital_no=None, admdate=None, disdate=None):
    return build_canonical_patient_folder_name(
        patient_name,
        hospital_no,
        admdate,
        disdate,
    )


def reset_patient_page_trackers():
    global soa2_pages, mrf_pages, pbc_pages, cf2_pages, cf2_page_texts

    soa2_pages = {}
    mrf_pages = {}
    pbc_pages = {}
    cf2_pages = {}
    cf2_page_texts = {}


def name_match_score(left, right):
    left = normalize_text(left or "")
    right = normalize_text(right or "")

    if not left or not right:
        return 0

    return max(
        fuzz.token_set_ratio(left, right),
        fuzz.partial_ratio(left, right)
    )


def names_probably_match(left, right, threshold=78):
    return name_match_score(left, right) >= threshold


def format_db_date(value):
    if not value:
        return None

    try:
        return value.strftime("%Y%m%d")
    except Exception:
        pass

    value = str(value).strip()

    patterns = [
        (r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", ("y", "m", "d")),
        (r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})", ("m", "d", "y")),
    ]

    for pattern, order in patterns:
        match = re.search(pattern, value)

        if not match:
            continue

        parts = dict(zip(order, match.groups()))

        try:
            return f"{int(parts['y']):04d}{int(parts['m']):02d}{int(parts['d']):02d}"
        except Exception:
            return None

    return None


def normalize_ocr_date_to_yyyymmdd(value):
    if not value:
        return None

    raw = str(value).upper()
    raw = raw.replace("O", "0")
    raw = re.sub(r"[,]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()

    month_map = {
        "JAN": 1, "JANUARY": 1,
        "FEB": 2, "FEBRUARY": 2,
        "MAR": 3, "MARCH": 3,
        "APR": 4, "APRIL": 4,
        "MAY": 5,
        "JUN": 6, "JUNE": 6,
        "JUL": 7, "JULY": 7,
        "AUG": 8, "AUGUST": 8,
        "SEP": 9, "SEPT": 9, "SEPTEMBER": 9,
        "OCT": 10, "OCTOBER": 10,
        "NOV": 11, "NOVEMBER": 11,
        "DEC": 12, "DECEMBER": 12,
    }

    numeric_patterns = [
        (r"\b(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})\b", ("y", "m", "d")),
        (r"\b(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4})\b", ("m", "d", "y")),
    ]

    for pattern, order in numeric_patterns:
        match = re.search(pattern, raw)

        if not match:
            continue

        parts = dict(zip(order, match.groups()))

        try:
            return f"{int(parts['y']):04d}{int(parts['m']):02d}{int(parts['d']):02d}"
        except Exception:
            pass

    word_patterns = [
        r"\b(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|SEPT|OCTOBER|NOVEMBER|DECEMBER|JAN|FEB|MAR|APR|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2})\s+(\d{4})\b",
        r"\b(\d{1,2})\s+(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|SEPT|OCTOBER|NOVEMBER|DECEMBER|JAN|FEB|MAR|APR|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{4})\b",
    ]

    for idx, pattern in enumerate(word_patterns):
        match = re.search(pattern, raw)

        if not match:
            continue

        try:
            if idx == 0:
                month, day, year = match.groups()
            else:
                day, month, year = match.groups()

            return f"{int(year):04d}{month_map[month]:02d}{int(day):02d}"
        except Exception:
            pass

    return None


def extract_adm_dis_from_soa_text(text):
    """
    Get confinement dates from SOA2 OCR.
    Prefer explicit Admission/Discharge labels, with numeric and word-date support.
    """
    if not text:
        return None, None

    clean = normalize_text(text)
    raw = str(text or "")

    admdate = None
    disdate = None

    label_patterns = [
        ("adm", r"(?:ADMISSION|ADMITTED|DATE\s+ADMITTED|ADMISSION\s+DATE)\D{0,25}([A-Z0-9,./\- ]{6,30})"),
        ("dis", r"(?:DISCHARGE|DISCHARGED|DATE\s+DISCHARGED|DISCHARGE\s+DATE)\D{0,25}([A-Z0-9,./\- ]{6,30})"),
    ]

    for kind, pattern in label_patterns:
        match = re.search(pattern, raw, re.IGNORECASE)

        if not match:
            match = re.search(pattern, clean, re.IGNORECASE)

        if match:
            parsed = normalize_ocr_date_to_yyyymmdd(match.group(1))

            if kind == "adm":
                admdate = parsed
            else:
                disdate = parsed

    if admdate and disdate:
        return admdate, disdate

    # Common SOA wording has both dates on one confinement/admission period line.
    period_patterns = [
        r"(?:CONFINEMENT|ADMISSION|ADMITTED|PERIOD)\D{0,30}([A-Z0-9,./\- ]{6,25})\s*(?:TO|UNTIL|\-)\s*([A-Z0-9,./\- ]{6,25})",
        r"\bFROM\D{0,12}([A-Z0-9,./\- ]{6,25})\s*(?:TO|UNTIL|\-)\s*([A-Z0-9,./\- ]{6,25})",
    ]

    for pattern in period_patterns:
        match = re.search(pattern, raw, re.IGNORECASE)

        if not match:
            match = re.search(pattern, clean, re.IGNORECASE)

        if match:
            first = normalize_ocr_date_to_yyyymmdd(match.group(1))
            second = normalize_ocr_date_to_yyyymmdd(match.group(2))

            if first and second:
                return first, second

    return admdate, disdate


def get_latest_admission_dates(hpercode):
    hpercode = normalize_hospital_no(hpercode)

    if not hpercode or pymysql is None:
        return None, None

    try:
        conn = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
            charset="utf8"
        )

        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT admdate, disdate
                FROM hadmlog
                WHERE hpercode=%s
                ORDER BY disdate DESC
                LIMIT 1
                """,
                (hpercode,)
            )
            row = cursor.fetchone()

        conn.close()

        if not row:
            return None, None

        return (
            format_db_date(row.get("admdate")),
            format_db_date(row.get("disdate"))
        )

    except Exception as e:
        print("[HADMLOG ERROR]", str(e))
        return None, None


def create_admission_lookup():
    """Build the modular read-only admission repository."""
    if pymysql is None:
        raise AdmissionLookupError("pymysql is not installed")

    def connection_factory():
        return pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
            read_timeout=10,
            write_timeout=10,
            autocommit=True,
            charset="utf8",
        )

    return MySQLAdmissionLookup(connection_factory)


def normalize_text(text):
    text = text.upper()
    text = text.replace("Ñ", "N")
    text = re.sub(r"[^A-Z0-9\- ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_soa_label_line(line):
    """
    OCR cleanup para sa SOA labels.
    Example:
    S0A -> SOA
    5OA -> SOA
    S O A -> SOA
    """
    line = normalize_text(line)
    line = line.replace("S0A", "SOA")
    line = line.replace("5OA", "SOA")
    line = re.sub(r"\bS\s+O\s+A\b", "SOA", line)
    return line


def has_strong_soa_identity(text):
    """
    Strong SOA markers only.
    Ginagamit ito para hindi mapagkamalang SOA2 ang DTR/lab forms
    na may 'reference range' or 'prepared by'.
    """
    text_lower = text.lower()
    clean = normalize_text(text)

    strong_exact = [
        "please pay at the cashier",
        "soa reference no",
        "soa reference #",
        "soa ref no",
        "soa ref #",
        "statement of account",
        "patient's statement of account",
        "summary of fees",
        "summary of charges",
        "itemized charges"
    ]

    if any(k in text_lower for k in strong_exact):
        return True

    strong_clean = [
        "PLEASE PAY AT THE CASHIER",
        "SOA REFERENCE NO",
        "SOA REFERENCE",
        "SOA REF NO",
        "STATEMENT OF ACCOUNT",
        "PATIENT S STATEMENT OF ACCOUNT",
        "SUMMARY OF FEES",
        "SUMMARY OF CHARGES",
        "ITEMIZED CHARGES"
    ]

    if any(k in clean for k in strong_clean):
        return True

    return False


def is_dtr_text(text):
    """
    DTR / diagnostic / lab guard.
    Ito ang pumipigil na maging SOA2 ang laboratory, ECG, xray, etc.
    """
    text_lower = text.lower()
    clean = normalize_text(text)

    if any(k in text_lower for k in DTR_KEYWORDS):
        return True

    dtr_extra_clean = [
        "REFERENCE RANGE",
        "REF RANGE",
        "HEMATOLOGY",
        "URINALYSIS",
        "CLINICAL CHEMISTRY",
        "CREATININE",
        "HEMOGLOBIN",
        "PLATELET",
        "WBC",
        "RBC",
        "BLOOD TYPE",
        "CROSS MATCHING",
        "ELECTROCARDIOGRAM",
        "NORMAL SINUS RHYTHM",
        "VENTRICULAR RATE",
        "PR INTERVAL",
        "QRS DURATION",
        "QT QTC"
    ]

    if any(k in clean for k in dtr_extra_clean):
        return True

    return False


def is_probably_dtr_not_soa(text):
    """
    True kapag mukhang DTR/lab/diagnostic siya at wala namang strong SOA marker.
    """
    return is_dtr_text(text) and not has_strong_soa_identity(text)



def normalize_hospital_no(value):
    """
    Normalize Hospital No.

    User may enter:
        12345

    Bot converts it to:
        000000000012345

    Final format: 15 digits.
    """
    if value is None:
        return None

    value = str(value)
    value = re.sub(r"\D", "", value)

    if not value:
        return None

    return value.zfill(15)

def build_patient_name_from_db(row):
    """
    Folder format:
        PATLAST, PATFIRST PATSUFFIX PATMIDDLE

    Example:
        BERMUDEZ, ELINO JR DELA CRUZ

    Notes:
        patsuffix is placed after patfirst because hospital naming preference is:
        patlast, patfirst patsuffix patmiddle
    """
    patlast = str(row.get("patlast") or "").strip().upper()
    patfirst = str(row.get("patfirst") or "").strip().upper()
    patsuffix = str(row.get("patsuffix") or "").strip().upper()
    patmiddle = str(row.get("patmiddle") or "").strip().upper()

    name_parts = []

    if patfirst:
        name_parts.append(patfirst)

    if patsuffix:
        name_parts.append(patsuffix)

    if patmiddle:
        name_parts.append(patmiddle)

    given_part = " ".join(name_parts).strip()

    if patlast and given_part:
        full_name = f"{patlast}, {given_part}"
    elif patlast:
        full_name = patlast
    else:
        full_name = given_part

    full_name = re.sub(r"\s+", " ", full_name).strip(" ,")

    return full_name

def get_patient_from_db_by_hpercode(hpercode):
    """
    READ ONLY database lookup.
    Source of truth for patient folder name.

    Query:
    SELECT patlast, patfirst, patsuffix, patmiddle, hpercode
    FROM hperson
    WHERE hpercode=%s
    LIMIT 1
    """
    hpercode = normalize_hospital_no(hpercode)

    if not hpercode:
        print("[DB] Invalid Hospital No:", hpercode)
        return None

    if pymysql is None:
        print("[DB ERROR] pymysql is not installed. Run: pip install pymysql")
        return None

    try:
        conn = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
            read_timeout=10,
            write_timeout=10,
            autocommit=True,
            charset="utf8"
        )

        with conn.cursor() as cursor:
            sql = """
                SELECT
                    patlast,
                    patfirst,
                    patsuffix,
                    patmiddle,
                    hpercode
                FROM hperson
                WHERE hpercode = %s
                LIMIT 1
            """

            cursor.execute(sql, (hpercode,))
            row = cursor.fetchone()

        conn.close()

        if not row:
            print("[DB] No patient found for hpercode:", hpercode)
            return None

        row["hpercode"] = normalize_hospital_no(row.get("hpercode"))

        print(
            "[DB] Patient found:",
            build_patient_name_from_db(row),
            "-",
            row["hpercode"]
        )

        return row

    except Exception as e:
        print("[DB ERROR]", str(e))
        return None


def confirm_patient_from_db_by_hpercode(initial_hospital_no):

    print("========== ENTERED confirm_patient_from_db_by_hpercode ==========")
    print("Hospital No:", initial_hospital_no)

    hospital_no = normalize_hospital_no(initial_hospital_no)



def confirm_patient_from_db_by_hpercode(initial_hospital_no):
    """
    Human confirmation layer.

    Flow:
    1. Use OCR Hospital No. from SOA1.
    2. Query hperson using SELECT only.
    3. Show popup with Hospital No. + patient name.
    4. YES = accept and continue.
    5. NO = ask user to enter correct Hospital No., then query again.
    6. Cancel = stop processing safely.
    """
    hospital_no = normalize_hospital_no(initial_hospital_no)

    # If Tkinter is not available, fallback to old behavior.
    if tk is None or messagebox is None or simpledialog is None:
        print("[CONFIRM] Tkinter not available. Using DB result without popup.")
        return get_patient_from_db_by_hpercode(hospital_no)

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    try:
        while True:
            if not hospital_no:
                hospital_no = simpledialog.askstring(
                    "Enter Hospital No.",
                    "Hospital No. was not detected.\n\nEnter correct Hospital No:",
                    parent=root
                )
                hospital_no = normalize_hospital_no(hospital_no)

                if not hospital_no:
                    print("[CONFIRM] Cancelled. No Hospital No. entered.")
                    return None

            row = get_patient_from_db_by_hpercode(hospital_no)

            if not row:
                retry = messagebox.askyesno(
                    "Patient Not Found",
                    f"No patient found for Hospital No.:\n\n{hospital_no}\n\nDo you want to enter the correct Hospital No.?",
                    parent=root
                )

                if not retry:
                    print("[CONFIRM] Cancelled. Patient not found.")
                    return None

                hospital_no = simpledialog.askstring(
                    "Correct Hospital No.",
                    "Enter correct Hospital No:",
                    parent=root
                )
                hospital_no = normalize_hospital_no(hospital_no)
                continue

            patient_name = build_patient_name_from_db(row)
            db_hpercode = normalize_hospital_no(row.get("hpercode"))

            ok = messagebox.askyesno(
                "Confirm Patient",
                "Please confirm patient before processing.\n\n"
                f"Hospital No.: {db_hpercode}\n\n"
                f"Patient Name:\n{patient_name}\n\n"
                "Is this the correct patient?",
                parent=root
            )

            if ok:
                print("[CONFIRM] Patient confirmed:", patient_name, "-", db_hpercode)
                return row

            hospital_no = simpledialog.askstring(
                "Correct Hospital No.",
                "Enter correct Hospital No:",
                parent=root
            )
            hospital_no = normalize_hospital_no(hospital_no)

            if not hospital_no:
                print("[CONFIRM] Cancelled. No corrected Hospital No. entered.")
                return None

    finally:
        try:
            root.destroy()
        except Exception:
            pass


def safe_copy(src, dst):
    """
    Copy file without overwriting existing backup files.
    """
    base, ext = os.path.splitext(dst)
    counter = 1
    new_dst = dst

    while os.path.exists(new_dst):
        new_dst = f"{base}_{counter}{ext}"
        counter += 1

    shutil.copy2(src, new_dst)
    return new_dst


def backup_original_scans(files):
    if not is_backup_enabled():
        print("[BACKUP] Disabled from GUI settings. Backup skipped.")
        return

    """
    Copy untouched original scanned PDFs to:
    C:\\claims_bot\\backup_originals\\PATIENT NAME - HOSPITAL NO\\

    This runs AFTER patient confirmation and BEFORE processing/moving files.
    """
    if not current_patient:
        print("[BACKUP] No confirmed patient. Backup skipped.")
        return

    backup_patient_folder = os.path.join(BACKUP_FOLDER, current_patient)
    os.makedirs(backup_patient_folder, exist_ok=True)

    print("\n[+] BACKUP: Copying original scanned PDFs before processing...\n")

    count = 0

    for file in files:
        if not file.lower().endswith(".pdf"):
            continue

        src = os.path.join(SCAN_FOLDER, file)

        if not os.path.exists(src):
            continue

        dst = os.path.join(backup_patient_folder, file)
        copied = safe_copy(src, dst)
        count += 1

        print("[BACKUP] Original scan copied ->", copied)

    print("[BACKUP] Completed. Total original PDFs backed up:", count)


def backup_original_paths(paths):
    """Back up exact source paths used by isolated Resume Processing."""
    if not is_backup_enabled():
        raise RuntimeError("Backup must be enabled for Resume Processing")
    if not current_patient:
        raise RuntimeError("Verified patient context is required before backup")
    backup_patient_folder = os.path.join(BACKUP_FOLDER, current_patient)
    os.makedirs(backup_patient_folder, exist_ok=True)
    for path in paths:
        source = os.path.abspath(path)
        if not os.path.isfile(source):
            raise FileNotFoundError(source)
        destination = os.path.join(
            backup_patient_folder, os.path.basename(source)
        )
        copied = safe_copy(source, destination)
        print("[RESUME BACKUP] Original scan copied ->", copied)

def choose_admission_dates(hpercode):

    hpercode = normalize_hospital_no(hpercode)

    if not hpercode or pymysql is None:
        return None, None

    try:

        conn = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
            charset="utf8"
        )

        with conn.cursor() as cursor:

            cursor.execute("""
                SELECT admdate, disdate
                FROM hadmlog
                WHERE hpercode=%s
                ORDER BY admdate DESC
            """, (hpercode,))

            rows = cursor.fetchall()

        conn.close()

        # No admission found
        if not rows:
            return None, None

        # Single admission only = AUTO SELECT
        if len(rows) == 1:

            row = rows[0]

            admdate = format_db_date(row["admdate"])
            disdate = format_db_date(row["disdate"])

            print(
                f"[HADMLOG] Single admission found: "
                f"ADM{admdate}_DIS{disdate}"
            )

            return admdate, disdate

        # No tkinter available
        if tk is None:

            row = rows[0]

            return (
                format_db_date(row["admdate"]),
                format_db_date(row["disdate"])
            )

        print(
            f"[HADMLOG] Multiple admissions found "
            f"({len(rows)}) - user selection required"
        )

        choice = {}

        root = tk.Tk()
        root.withdraw()

        win = tk.Toplevel(root)
        win.title("Select Admission")
        win.geometry("600x400")
        win.grab_set()

        tk.Label(
            win,
            text=f"Multiple admissions found for HPERCODE {hpercode}\nSelect the correct confinement:",
            font=("Arial", 10, "bold")
        ).pack(pady=(10, 5))

        lb = tk.Listbox(win, width=80, height=12)

        for r in rows:

            adm = format_db_date(r["admdate"])
            dis = format_db_date(r["disdate"])

            lb.insert(
                tk.END,
                f"ADM {adm}   DIS {dis}"
            )

        lb.pack(fill="both", expand=True, padx=10, pady=10)

        lb.selection_set(0)

        def use_selected():

            idx = lb.curselection()

            if not idx:
                return

            choice["row"] = rows[idx[0]]
            win.destroy()

        tk.Button(
            win,
            text="Use Selected Admission",
            command=use_selected
        ).pack(pady=10)

        win.wait_window()

        root.destroy()

        if "row" not in choice:
            row = rows[0]
        else:
            row = choice["row"]

        admdate = format_db_date(row["admdate"])
        disdate = format_db_date(row["disdate"])

        print(
            f"[HADMLOG] Selected: "
            f"ADM{admdate}_DIS{disdate}"
        )

        return admdate, disdate

    except Exception as e:

        print("[HADMLOG PICK ERROR]", str(e))

        return None, None


def set_patient_context_from_db_hospital_no(hospital_no):
    """
    Creates/updates current patient context using:
    SOA1 OCR Hospital No -> DB hperson -> exact patient name.

    Final folder format:
    PATLAST, PATFIRST PATMIDDLE - HPERCODE
    """
    global current_patient, current_patient_base_name, current_hospital_no
    global soa2_pages, mrf_pages, pbc_pages, cf2_pages, cf2_page_texts

    hospital_no = normalize_hospital_no(hospital_no)

    if not hospital_no:
        return False

    print(
        "[DEBUG]",
        "PROCESS_MODE =", PROCESS_MODE,
        "CONFIRM_PATIENT =", CONFIRM_PATIENT
    )

    row = get_patient_with_confirmation(hospital_no)

    if not row:
        return False

    db_patient_name = build_patient_name_from_db(row)
    db_hpercode = normalize_hospital_no(row.get("hpercode"))

    if not db_patient_name or not db_hpercode:
        print("[DB] Incomplete patient data:", row)
        return False

    old_folder_name = current_patient

    current_patient_base_name = db_patient_name
    current_hospital_no = db_hpercode

    admdate, disdate = choose_admission_dates(db_hpercode)

    current_patient = build_patient_folder_name(
        current_patient_base_name,
        current_hospital_no,
        admdate,
        disdate
    )

    # Reset temporary page trackers when new verified patient is set.
    reset_patient_page_trackers()

    print("[+] VERIFIED PATIENT FROM DB:", current_patient)

    # If an old temporary folder already exists, move contents to verified DB folder.
    if old_folder_name and old_folder_name != current_patient:
        old_folder = os.path.join(OUTPUT_FOLDER, old_folder_name)
        new_folder = os.path.join(OUTPUT_FOLDER, current_patient)

        if os.path.exists(old_folder):
            if os.path.exists(new_folder):
                move_folder_contents(old_folder, new_folder)
            else:
                os.rename(old_folder, new_folder)

            print("[+] Folder renamed using DB patient name:")
            print("    OLD:", old_folder_name)
            print("    NEW:", current_patient)

    return True


def extract_hospital_no_from_soa1(text):
    hospital_no = extract_soa1_hospital_number(text or "")
    if hospital_no:
        print("[+] Hospital No. extracted from SOA1:", hospital_no)
    else:
        print("[!] Valid numeric Hospital No. not found in SOA1")
    return hospital_no

def move_folder_contents(src_folder, dst_folder):
    os.makedirs(dst_folder, exist_ok=True)

    for item in os.listdir(src_folder):
        src_path = os.path.join(src_folder, item)
        dst_path = os.path.join(dst_folder, item)

        if os.path.isdir(src_path):
            if os.path.exists(dst_path):
                move_folder_contents(src_path, dst_path)
                try:
                    os.rmdir(src_path)
                except Exception:
                    pass
            else:
                os.rename(src_path, dst_path)
        else:
            safe_move(src_path, dst_path)

    try:
        os.rmdir(src_folder)
    except Exception:
        pass


def update_current_patient_folder_with_hospital_no(hospital_no):
    """
    OLD behavior: use OCR patient name + OCR hospital no.
    NEW behavior: use SOA1 OCR hospital no only as key,
    then query hperson table for exact patient name and hpercode.

    This makes folder name more accurate:
    PATLAST, PATFIRST PATMIDDLE - HPERCODE
    """
    hospital_no = normalize_hospital_no(hospital_no)

    if not hospital_no:
        print("[!] Empty Hospital No, cannot update folder")
        return

    ok = set_patient_context_from_db_hospital_no(hospital_no)

    if not ok:
        print("[!] DB verification failed. Folder not renamed from OCR Hospital No.")

def ocr_pdf(pdf_path):
    images = convert_from_path(
        pdf_path,
        first_page=1,
        last_page=1,
        poppler_path=POPPLER_PATH
    )
    return pytesseract.image_to_string(images[0]).lower()


def convert_to_pdfa_target_size(pdf_path):
    if not os.path.exists(GHOSTSCRIPT_PATH):
        print("[!] Ghostscript not found:", GHOSTSCRIPT_PATH)
        return

    make_writable(pdf_path)
    resolutions = [300, 250, 220, 200, 180, 150, 120]
    best_temp = None
    best_size = None
    temp_candidates = []

    for res in resolutions:
        temp_pdf = pdf_path.replace(".pdf", f"_tmp_{res}.pdf")
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
            pdf_path
        ]

        subprocess.run(cmd, check=True)
        size = file_size_kb(temp_pdf)

        print(f"[TEST] {os.path.basename(pdf_path)} {res} DPI -> {size:.0f} KB")

        if best_size is None or size < best_size:
            if best_temp and os.path.exists(best_temp):
                os.remove(best_temp)
            best_temp = temp_pdf
            best_size = size
        else:
            os.remove(temp_pdf)

        if size <= MAX_SIZE_KB:
            os.remove(pdf_path)
            os.rename(best_temp, pdf_path)
            os.chmod(pdf_path, stat.S_IREAD)
            print(f"[+] PDF/A + Read-only FINAL: {os.path.basename(pdf_path)} -> {size:.0f} KB")
            return

    if best_temp and os.path.exists(best_temp):
        os.remove(pdf_path)
        os.rename(best_temp, pdf_path)
        os.chmod(pdf_path, stat.S_IREAD)
        print(
            "[+] PDF/A + Read-only BEST EFFORT:",
            os.path.basename(pdf_path),
            f"-> {best_size:.0f} KB",
        )
        return

    for temp_pdf in temp_candidates:
        if os.path.exists(temp_pdf):
            os.remove(temp_pdf)

    print("[!] Hindi umabot sa target size pero converted best effort.")


def finalize_all_pdfs():
    print("\n[+] Converting ALL final PDFs to PDF/A + Read-only target 1000KB...\n")

    for root, dirs, files in os.walk(OUTPUT_FOLDER):
        for file in files:
            if not file.lower().endswith(".pdf"):
                continue

            if "_overlay" in file or "_signed" in file or ".tmp" in file or "_tmp_" in file:
                continue

            pdf_path = os.path.join(root, file)
            convert_to_pdfa_target_size(pdf_path)


def extract_patient_name(text):
    text = text.upper()
    lines = text.split("\n")

    stop_words = {
        "LAST", "NAME", "FIRST", "MIDDLE", "EXTENSION",
        "JR", "SR", "III", "IV",
        "CHILD", "PARENT", "SPOUSE",
        "RELATIONSHIP", "MEMBER", "DATE", "BIRTH",
        "MONTH", "DAY", "YEAR", "PATIENT"
    }

    for i, line in enumerate(lines):
        if "NAME OF PATIENT" in line or "5. NAME OF PATIENT" in line:
            block = " ".join(lines[i + 1:i + 9])
            block = re.sub(r"[^A-Z\s]", " ", block)
            block = re.sub(r"\s+", " ", block).strip()

            words = [w for w in block.split() if w not in stop_words and len(w) > 1]

            clean = []
            for w in words:
                if w in {"TUA", "PARENT", "SPOUSE", "CHILD", "SIGNATURE", "DATE"}:
                    break
                clean.append(w)

            if len(clean) >= 3:
                return f"{clean[0]}, {clean[1]} {clean[2]}".title()

            if len(clean) >= 2:
                return f"{clean[0]}, {clean[1]}".title()

    return None


def clean_extracted_patient_name(value):
    if not value:
        return None

    value = str(value).upper()
    value = value.replace("Ã‘", "N")
    value = re.sub(r"[^A-ZÑ,\-.\s]", " ", value)
    value = re.sub(r"\b(PATIENT|NAME|MEMBER|PHILHEALTH|ELIGIBILITY|BENEFIT|FORM|DATE|BIRTH|PIN|HOSPITAL|NO)\b", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" ,.-")

    if len(value) < 5:
        return None

    words = [w for w in re.split(r"[\s,]+", value) if w]

    if len(words) < 2:
        return None

    return value.title()


def extract_patient_name_from_coe(text):
    """
    COE fallback and verification source.
    Tries line labels first, then nearby text after common PhilHealth fields.
    """
    if not text:
        return None

    lines = [line.strip() for line in str(text).splitlines() if line.strip()]

    label_patterns = [
        r"(?:NAME\s+OF\s+PATIENT|PATIENT\s+NAME|MEMBER\s+NAME|NAME)\s*[:\-]?\s*(.+)",
    ]

    for line in lines:
        clean_line = re.sub(r"\s+", " ", line).strip()

        for pattern in label_patterns:
            match = re.search(pattern, clean_line, re.IGNORECASE)

            if not match:
                continue

            candidate = clean_extracted_patient_name(match.group(1))

            if candidate:
                return candidate

    for idx, line in enumerate(lines):
        clean_line = normalize_text(line)

        if (
            "NAME OF PATIENT" in clean_line
            or "PATIENT NAME" in clean_line
            or "MEMBER NAME" in clean_line
        ):
            for next_line in lines[idx + 1:idx + 5]:
                candidate = clean_extracted_patient_name(next_line)

                if candidate:
                    return candidate

    block = " ".join(lines)
    block = re.sub(r"\s+", " ", block)

    inline_patterns = [
        r"(?:NAME\s+OF\s+PATIENT|PATIENT\s+NAME|MEMBER\s+NAME)\s*[:\-]?\s*([A-ZÑ ,.\-]{8,80})",
    ]

    for pattern in inline_patterns:
        match = re.search(pattern, block, re.IGNORECASE)

        if match:
            candidate = clean_extracted_patient_name(match.group(1))

            if candidate:
                return candidate

    return None


def extract_part_iv_text(text):
    clean = text.upper()

    start_markers = [
        "PART IV",
        "PART  IV",
        "PARTIV",
        "PART |V",
        "PART !V",
        "HEALTH CARE PROFESSIONAL INFORMATION",
        "CERTIFICATION OF HEALTH CARE PROFESSIONAL",
        "ACCREDITATION NO"
    ]

    end_markers = [
        "PART V",
        "PART  V",
        "PARTV",
        "PROVIDER INFORMATION AND CERTIFICATION"
    ]

    start = -1

    for marker in start_markers:
        pos = clean.find(marker)
        if pos != -1:
            start = pos
            break

    if start != -1:
        end = len(clean)

        for marker in end_markers:
            pos = clean.find(marker, start + 20)
            if pos != -1:
                end = pos
                break

        part_text = clean[start:end]
    else:
        part_text = ""

    if len(part_text.strip()) < 150:
        lines = clean.split("\n")
        part_text = "\n".join(lines[-60:])

    print("\n[DEBUG PART IV TEXT]")
    print(part_text[:1000])
    print("[END DEBUG PART IV TEXT]\n")

    return part_text


def has_fuzzy_keyword(text, phrases, threshold=82):
    clean = normalize_text(text)

    for phrase in phrases:
        phrase_clean = normalize_text(phrase)
        score = fuzz.partial_ratio(phrase_clean, clean)

        if score >= threshold:
            print("[FUZZ DOC]", phrase, "score =", score)
            return True

    return False


def line_fuzzy_match(text, phrases, threshold=72):
    lines = [normalize_text(line) for line in text.splitlines()]
    lines = [line for line in lines if line]

    for line in lines:
        for phrase in phrases:
            phrase_clean = normalize_text(phrase)
            score = fuzz.partial_ratio(phrase_clean, line)

            if score >= threshold:
                print("[FUZZ LINE]", phrase, "score =", score, "line =", line[:100])
                return True

    return False


def is_soa1_text(text):
    text_lower = text.lower()

    if "please pay at the cashier" in text_lower:
        return True

    return line_fuzzy_match(
        text,
        ["PLEASE PAY AT THE CASHIER"],
        threshold=82
    )


def is_soa2_page1_text(text):
    """
    SOA2 page 1 strict rule.

    IMPORTANT FIX:
    Hindi na siya basta nag-fuzzy sa 'SOA REFERENCE',
    kasi ang DTR/lab forms may 'REFERENCE RANGE' at nagiging false SOA2_page1.

    New rule:
    - Same line dapat may SOA
    - Same line dapat may REF / REFERENCE / NO
    - Skip kung REFERENCE RANGE / REF RANGE
    """
    soa2_resolution = resolve_soa2_text(text or "")
    if soa2_resolution.doc_type in ("SOA2", "SOA2_page1"):
        return True

    text_lower = text.lower()

    exact_markers = [
        "soa reference no",
        "soa reference #",
        "soa ref no",
        "soa ref #"
    ]

    if any(k in text_lower for k in exact_markers):
        return True

    lines = [normalize_soa_label_line(line) for line in text.splitlines()]
    lines = [line for line in lines if line]

    for line in lines:

        if "REFERENCE RANGE" in line or "REF RANGE" in line:
            continue

        has_soa = "SOA" in line
        has_ref = (
            "REFERENCE" in line
            or "REF" in line
        )
        has_no = (
            " NO" in line
            or "NO " in line
            or line.endswith("NO")
            or "#" in line
        )

        if has_soa and has_ref and has_no:
            print("[SOA2 PAGE1 STRICT LINE]", line[:120])
            return True

        if has_soa and has_ref:
            score1 = fuzz.partial_ratio("SOA REFERENCE NO", line)
            score2 = fuzz.partial_ratio("SOA REF NO", line)
            best_score = max(score1, score2)

            if best_score >= 82:
                print("[SOA2 PAGE1 FUZZ SAFE] score =", best_score, "line =", line[:120])
                return True

    return False

def is_coe_text(text):
    """
    COE / PhilHealth Benefit Eligibility guard.

    COE can contain labels that look like SOA2 page 2, so this must run before
    SOA2 detection and image fallback.
    """
    text_lower = str(text or "").lower()
    clean = normalize_text(text or "")

    if (
        "hci portal reference no" in text_lower
        or "hci portal reference" in text_lower
        or "philhealth benefit eligibility" in text_lower
        or "philhealth benefit eligibility form" in text_lower
        or "teamphilhealth" in text_lower
        or "team philhealth" in text_lower
    ):
        return True

    benefit_identity = (
        "BENEFIT" in clean
        and (
            "ELIGIBILITY" in clean
            or fuzz.partial_ratio("ELIGIBILITY", clean) >= 84
        )
    )

    portal_identity = (
        "HCI" in clean
        and "PORTAL" in clean
        and (
            "REFERENCE" in clean
            or fuzz.partial_ratio("REFERENCE", clean) >= 84
        )
    )

    team_identity = (
        "TEAM" in clean
        and "PHILHEALTH" in clean
    )

    if benefit_identity or portal_identity or team_identity:
        print("[COE IDENTITY]", clean[:120])
        return True

    return False


def is_soa2_page2_text(text):
    """
    SOA2 page 2 rule.

    IMPORTANT FIX:
    Kapag mukhang DTR/lab/diagnostic siya at walang strong SOA marker,
    huwag siyang gawing SOA2_page2 kahit may 'prepared by'.
    """
    soa2_resolution = resolve_soa2_text(text or "")
    if soa2_resolution.guarded:
        return False
    if soa2_resolution.doc_type in ("SOA2", "SOA2_page2"):
        return True

    if detect_anr_type(text):
        print("[ANR GUARD] Blocked false SOA2_page2 detection")
        return False

    if is_probably_dtr_not_soa(text):
        print("[DTR GUARD] Blocked false SOA2_page2 detection")
        return False

    if is_coe_text(text):
        print("[COE GUARD] Blocked false SOA2_page2 detection")
        return False

    text_lower = text.lower()

    if (
        "prepared by" in text_lower
        or "prepared by:" in text_lower
        or "conforme" in text_lower
        or "conforme:" in text_lower
    ):
        return True

    secondary_exact = [
        "relationship of representative",
        "patient / representative",
        "patient/representative",
        "signature over printed name",
        "administrative officer"
    ]

    if any(k in text_lower for k in secondary_exact):
        return True

    fuzzy_main_markers = [
        "PREPARED BY",
        "CONFORME"
    ]

    if line_fuzzy_match(text, fuzzy_main_markers, threshold=68):
        return True

    fuzzy_secondary_markers = [
        "RELATIONSHIP OF REPRESENTATIVE",
        "PATIENT REPRESENTATIVE",
        "SIGNATURE OVER PRINTED NAME",
        "ADMINISTRATIVE OFFICER"
    ]

    if line_fuzzy_match(text, fuzzy_secondary_markers, threshold=76):
        return True

    return False



def detect_mrf_type(text):
    """
    Detect MRF page 1 and page 2 before CSF/SOA logic.

    FINAL RULE:
    MRF_page1 keywords:
      - PhilHealth Member Registration Form
      - UHC, but only with page 1 context
      - PHILSYS

    MRF_page2 keywords:
      - For PhilHealth Use Only
      - amendment / updating-amendment, but not amendment alone
        unless there are other page 2 indicators.

    IMPORTANT:
    PMRF was removed because it can also appear on MRF_page2.
    """
    text_lower = text.lower()
    clean = normalize_text(text)

    # CF2 page 1 can contain generic PhilHealth/MRF-looking labels.
    # If CF2 identity is present, let detect_cf2_type handle it later.
    if (
        "claim form 2" in text_lower
        or "cf2" in text_lower
        or (
            "type of accommodation" in text_lower
            and "z-benefit package code" in text_lower
        )
    ):
        print("[CF2 GUARD] Blocked false MRF detection")
        return ""

    # -------------------------
    # MRF PAGE 1 - strong markers first
    # -------------------------
    page1_strong_exact = [
        "philhealth member registration form",
        "member registration form",
        "philsys id number",
        "philsys"
    ]

    if any(k in text_lower for k in page1_strong_exact):
        return "MRF_page1"

    page1_strong_fuzzy = [
        "PHILHEALTH MEMBER REGISTRATION FORM",
        "MEMBER REGISTRATION FORM",
        "PHILSYS ID NUMBER",
        "PHILSYS"
    ]

    for phrase in page1_strong_fuzzy:
        score = fuzz.partial_ratio(phrase, clean)
        if score >= 84:
            print("[MRF PAGE1 FUZZ]", phrase, "score =", score)
            return "MRF_page1"

    # UHC is allowed as page1 keyword, pero huwag standalone lang.
    # Page2 can also contain PhilHealth/header text, kaya kailangan may page1 context.
    has_uhc = (
        "uhc" in text_lower
        or "UHC" in clean
        or fuzz.partial_ratio("UHC", clean) >= 95
    )

    page1_context = [
        "personal details",
        "philhealth identification number",
        "pin is your unique",
        "purpose:",
        "registration",
        "preferred konsulta provider",
        "maiden name"
    ]

    if has_uhc and any(k in text_lower for k in page1_context):
        return "MRF_page1"

    # -------------------------
    # MRF PAGE 2
    # -------------------------
    # Strong page2 marker: automatic page2.
    if (
        "for philhealth use only" in text_lower
        or "for philhealth use" in text_lower
        or fuzz.partial_ratio("FOR PHILHEALTH USE ONLY", clean) >= 82
    ):
        return "MRF_page2"

    # Amendment alone is weak because MRF_page1 may also contain registration/updating/amendment.
    # Require at least 2 page2 indicators OR very strong specific text.
    page2_indicators = [
        "updating/amendment",
        "updating amendment",
        "v. updating/amendment",
        "v updating amendment",
        "change/correction of name",
        "correction of date of birth",
        "correction of sex",
        "change of civil status",
        "updating of personal information",
        "under penalty of law",
        "documents i have attached",
        "full name:"
    ]

    page2_hits = 0
    for marker in page2_indicators:
        if marker in text_lower:
            page2_hits += 1

    if page2_hits >= 2:
        return "MRF_page2"

    page2_fuzzy = [
        "CHANGE CORRECTION OF NAME",
        "CORRECTION OF DATE OF BIRTH",
        "CHANGE OF CIVIL STATUS",
        "UPDATING OF PERSONAL INFORMATION",
        "UNDER PENALTY OF LAW"
    ]

    fuzzy_hits = 0
    for phrase in page2_fuzzy:
        score = fuzz.partial_ratio(phrase, clean)
        if score >= 82:
            print("[MRF PAGE2 FUZZ]", phrase, "score =", score)
            fuzzy_hits += 1

    if fuzzy_hits >= 1 and page2_hits >= 1:
        return "MRF_page2"

    return ""


def detect_pbc_type(text):
    """
    Detect PBC page 1 and page 2 before CSF/SOA/DTR logic.

    PBC_page1:
      - Certificate of Live Birth
      - Office of the Civil Registrar General
      - Municipal Form No. 102
      - Registry No.
      - Registered at the Office of the Civil Registrar

    PBC_page2:
      - Affidavit of Acknowledgment/Admission of Paternity
      - Admission of Paternity
      - Affidavit for Delayed Registration of Birth
      - Subscribed and Sworn
    """
    text_lower = text.lower()
    clean = normalize_text(text)

    # -------------------------
    # PBC PAGE 1
    # -------------------------
    page1_exact = [
        "certificate of live birth",
        "office of the civil registrar general",
        "municipal form no. 102",
        "municipal form no 102",
        "registry no",
        "registered at the office of the civil registrar"
    ]

    if any(k in text_lower for k in page1_exact):
        return "PBC_page1"

    page1_fuzzy = [
        "CERTIFICATE OF LIVE BIRTH",
        "OFFICE OF THE CIVIL REGISTRAR GENERAL",
        "MUNICIPAL FORM NO 102",
        "REGISTERED AT THE OFFICE OF THE CIVIL REGISTRAR"
    ]

    for phrase in page1_fuzzy:
        score = fuzz.partial_ratio(phrase, clean)
        if score >= 84:
            print("[PBC PAGE1 FUZZ]", phrase, "score =", score)
            return "PBC_page1"

    # -------------------------
    # PBC PAGE 2
    # -------------------------
    page2_exact = [
        "affidavit of acknowledgment",
        "admission of paternity",
        "affidavit for delayed registration of birth",
        "subscribed and sworn"
    ]

    if any(k in text_lower for k in page2_exact):
        return "PBC_page2"

    page2_fuzzy = [
        "AFFIDAVIT OF ACKNOWLEDGMENT",
        "ADMISSION OF PATERNITY",
        "AFFIDAVIT FOR DELAYED REGISTRATION OF BIRTH",
        "SUBSCRIBED AND SWORN"
    ]

    for phrase in page2_fuzzy:
        score = fuzz.partial_ratio(phrase, clean)
        if score >= 82:
            print("[PBC PAGE2 FUZZ]", phrase, "score =", score)
            return "PBC_page2"

    return ""



def detect_opr_type(text):

    text_lower = text.lower()
    clean = normalize_text(text)

    # STRICT OPR ONLY
    # OPR covers both the Operating Room Record and the Delivery Room Record.
    if "operating room record" in text_lower or "delivery room record" in text_lower:
        return "OPR"

    if (
        fuzz.partial_ratio(
            "OPERATING ROOM RECORD",
            clean
        ) >= 88
        or fuzz.partial_ratio(
            "DELIVERY ROOM RECORD",
            clean
        ) >= 88
    ):

        print("[OPR FUZZ] OPERATING/DELIVERY ROOM RECORD")

        return "OPR"

    return ""


def detect_anr_type(text):
    """
    Detect ANR / Anesthesia Record using OCR text.
    Added as a safe guard so ANR will not be renamed as SOA2_page2.
    """
    text_lower = text.lower()
    clean = normalize_text(text)

    strong_exact = [
        "anesthesia record",
        "anaesthesia record"
    ]

    if any(k in text_lower for k in strong_exact):
        return "ANR"

    secondary_markers = [
        "premedication",
        "proposed operation",
        "anesthetic agent",
        "anaesthetic agent",
        "detailed technique",
        "induction",
        "maintenance",
        "emergence",
        "fluid summary",
        "urine output in o.r",
        "urine output in o r",
        "condition of patient on departure"
    ]

    secondary_hits = 0
    for marker in secondary_markers:
        if marker in text_lower:
            secondary_hits += 1

    if secondary_hits >= 2:
        return "ANR"

    fuzzy_markers = [
        "ANESTHESIA RECORD",
        "ANAESTHESIA RECORD",
        "PREMEDICATION DOSE ROUTE TIME",
        "PROPOSED OPERATION",
        "ANESTHETIC AGENT",
        "DETAILED TECHNIQUE",
        "INDUCTION MAINTENANCE EMERGENCE",
        "FLUID SUMMARY",
        "URINE OUTPUT IN O R",
        "CONDITION OF PATIENT ON DEPARTURE"
    ]

    fuzzy_hits = 0
    for phrase in fuzzy_markers:
        score = fuzz.partial_ratio(phrase, clean)
        if score >= 78:
            print("[ANR FUZZ]", phrase, "score =", score)
            fuzzy_hits += 1

    if fuzzy_hits >= 1 and secondary_hits >= 1:
        return "ANR"

    return ""

def detect_cf2_type(text):

    text_lower = text.lower()

    # CF2 page 1 has unique accommodation/newborn-care fields.
    # "Use additional CF2 if necessary" appears on both CF2 pages, so it must
    # never be used as a standalone page 2 detector.
    if (
        "type of accommodation" in text_lower
        or "for essential newborn care" in text_lower
        or "essential newborn care" in text_lower
    ):
        return "CF2_page1"

    # -------------------------
    # CF2 PAGE 2
    # -------------------------
    page2_keywords = [
        "certification of consumption",
        "no co-pay on top",
        "with co-pay on top",
        "name of accredited health care professional",
        "authorized hci representative",
        "accreditation number"
    ]

    page2_hits = 0

    for k in page2_keywords:
        if k in text_lower:
            page2_hits += 1

    if page2_hits >= 2:
        return "CF2_page2"

    # -------------------------
    # CF2 PAGE 1
    # -------------------------
    page1_keywords = [
        "claim form 2",
        "type of accommodation",
        "z-benefit package code",
        "for essential newborn care",
        "essential newborn care"
    ]

    page1_hits = 0

    for k in page1_keywords:
        if k in text_lower:
            page1_hits += 1

    if "claim form 2" in text_lower:
        return "CF2_page1"

    if page1_hits >= 2:
        return "CF2_page1"

    return ""

def detect_doc(text):
    text = text.lower()
    clean_text = normalize_text(text)

    # MRF must be detected first.
    # This prevents MRF page 1 from becoming CSF and MRF page 2 from becoming SOA2_page2.
    mrf_type = detect_mrf_type(text)
    if mrf_type:
        return mrf_type

    pbc_type = detect_pbc_type(text)
    if pbc_type:
        return pbc_type

    opr_type = detect_opr_type(text)
    if opr_type:
        return opr_type

    anr_type = detect_anr_type(text)
    if anr_type:
        return anr_type

    cf2_type = detect_cf2_type(text)

    if cf2_type:
        return cf2_type

    if (
        "CLAIM SIGNATURE FORM" in clean_text
        or "CL SIGNATURE FORM" in clean_text
        or "THIS FORM MAY BE REPRODUCED" in clean_text
        or "CSF" in clean_text
    ):
        return "CSF"

    if (
        "hci portal reference no" in text
        or "hci portal reference" in text
        or "philhealth benefit eligibility" in text
        or "philhealth benefit eligibility form" in text
        or "teamphilhealth" in text
        or "team philhealth" in text
        or is_coe_text(text)
    ):
        return "COE"

    # IMPORTANT FIX:
    # DTR guard muna bago SOA.
    # Para ang lab/ECG/xray na may "reference range" or "prepared by"
    # ay hindi maging SOA2_page1/page2.
    if is_probably_dtr_not_soa(text):
        return "DTR"

    if (
        is_soa1_text(text)
        or is_soa2_page1_text(text)
        or is_soa2_page2_text(text)
    ):
        return "SOA"

    if any(k in text for k in [
        "statement of account",
        "patient's statement of account",
        "summary of fees",
        "itemized charges",
        "summary of charges",
        "professional fees",
        "print name"
    ]):
        return "SOA"

    if has_fuzzy_keyword(text, [
        "STATEMENT OF ACCOUNT",
        "SUMMARY OF FEES",
        "ITEMIZED CHARGES"
    ], threshold=84):
        return "SOA"

    for doc, keywords in DOC_KEYWORDS.items():
        if doc in ["CSF", "MRF"]:
            continue

        if any(k in text for k in keywords):
            return doc

    if is_dtr_text(text):
        return "DTR"

    return "UNKNOWN"
    
def normalize_ocr_for_doc(text):

    text = text.upper()

    replacements = {

        "0": "O",
        "1": "I",
        "5": "S",

        "PREP RED": "PREPARED",
        "PREP RED BY": "PREPARED BY",

        "PREPARED 8Y": "PREPARED BY",
        "PREPARED BV": "PREPARED BY",

        "CONFARNE": "CONFORME",
        "CONFORRNE": "CONFORME",
        "CONF0RME": "CONFORME",

        "SIGNATURE EK ER": "SIGNATURE OVER",
        "VER/PRINTED": "OVER PRINTED"

    }

    for wrong, correct in replacements.items():
        text = text.replace(wrong, correct)

    return text

def detect_soa_type(text):

    text_lower = text.lower()
    clean = normalize_ocr_for_doc(text)

    # -------------------------
    # SOA1
    # -------------------------
    if "please pay at the cashier" in text_lower:
        return "SOA1"

    soa2_resolution = resolve_soa2_text(text or "")
    if soa2_resolution.is_soa2:
        print(
            "[SOA2 RESOLVER]",
            soa2_resolution.doc_type,
            f"page1={soa2_resolution.page1_score}",
            f"completion={soa2_resolution.completion_score}",
        )
        return soa2_resolution.doc_type

    # -------------------------
    # SOA2 PAGE1
    # -------------------------
    soa_page1_keywords = [
        "soa reference no",
        "soa reference #",
        "soa ref no"
    ]

    if any(k in text_lower for k in soa_page1_keywords):
        return "SOA2_page1"

    # -------------------------
    # SOA2 PAGE2
    # -------------------------
    page2_keywords = [
        "prepared by",
        "conforme",
        "administrative aide",
        "administrative officer",
        "signature over printed name",
        "patient / representative",
        "patient representative"
    ]

    # exact
    for k in page2_keywords:
        if k in text_lower:
            return "SOA2_page2"

    # fuzzy per line
    for line in clean.splitlines():

        line = line.strip()

        if not line:
            continue

        for keyword in page2_keywords:

            score = fuzz.partial_ratio(
                normalize_ocr_for_doc(keyword),
                normalize_ocr_for_doc(line)
            )

            print(
                "[FUZZ LINE]",
                keyword.upper(),
                "score =",
                score,
                "line =",
                line
            )

            if score >= 80:
                return "SOA2_page2"

    # default
    return "SOA1"


def detect_doctors(text):
    global DOCTOR_SETTINGS_LOADED_FROM_JSON_ONCE

    # Load latest Doctor Manager config once per run.
    if not DOCTOR_SETTINGS_LOADED_FROM_JSON_ONCE:
        reload_dynamic_doctor_settings()
        DOCTOR_SETTINGS_LOADED_FROM_JSON_ONCE = True

    part_iv_text = extract_part_iv_text(text)
    clean = normalize_text(part_iv_text)

    detected = []

    for doctor_key, setting in DOCTOR_SETTINGS.items():
        best_score = 0
        best_alias = ""

        for alias in setting["aliases"]:
            if not is_valid_doctor_alias(alias):
                continue

            alias_clean = normalize_text(alias)

            # Extra guard: never use generic medical suffixes as doctor identity.
            if alias_clean in GENERIC_DOCTOR_ALIASES:
                continue

            score = fuzz.partial_ratio(alias_clean, clean)

            if score > best_score:
                best_score = score
                best_alias = alias

        print("[FUZZ PART IV]", doctor_key, "score =", best_score, "alias =", best_alias)

        if best_score >= 85 and is_valid_doctor_alias(best_alias):
            detected.append((doctor_key, setting, best_score, len(normalize_text(best_alias))))

    # If two doctors both score 100, choose the one with the longer/more specific alias,
    # not the one that matched a short generic word.
    detected.sort(key=lambda x: (x[2], x[3]), reverse=True)

    return [(doctor_key, setting, score) for doctor_key, setting, score, alias_len in detected]


def choose_part_iv_doctor(detected_doctors):
    if not detected_doctors:
        return None

    doctor_key, setting, score = detected_doctors[0]

    if score >= 85:
        return doctor_key, setting

    return None


def draw_signature_auto(c, img_path, x, y, max_w, max_h):
    img = Image.open(img_path)

    w, h = img.size

    scale = min(
        max_w / float(w),
        max_h / float(h)
    )

    new_w = w * scale
    new_h = h * scale

    offset_x = x + (max_w - new_w) / 2
    offset_y = y + (max_h - new_h) / 2

    c.drawImage(
        img_path,
        offset_x,
        offset_y,
        width=new_w,
        height=new_h,
        mask="auto"
    )


def pdf_page_to_image(pdf_path, page_no=0, zoom=2):
    doc = fitz.open(pdf_path)

    page = doc[page_no]

    pix = page.get_pixmap(
        matrix=fitz.Matrix(zoom, zoom),
        alpha=False
    )

    img = np.frombuffer(
        pix.samples,
        dtype=np.uint8
    )

    img = img.reshape(
        pix.height,
        pix.width,
        pix.n
    )

    doc.close()

    return cv2.cvtColor(
        img,
        cv2.COLOR_RGB2GRAY
    ), zoom


def has_signature_in_area(pdf_path, x, y, max_w, max_h, threshold=0.35):
    gray, zoom = pdf_page_to_image(pdf_path)

    h, w = gray.shape

    page_doc = fitz.open(pdf_path)
    page_height = float(page_doc[0].rect.height)
    page_doc.close()

    margin_x = 5
    margin_y = 3

    x1 = int((x + margin_x) * zoom)
    x2 = int((x + max_w - margin_x) * zoom)

    y_top_pdf = page_height - (y + max_h - margin_y)
    y_bottom_pdf = page_height - (y + margin_y)

    y1 = int(y_top_pdf * zoom)
    y2 = int(y_bottom_pdf * zoom)

    x1 = max(0, min(x1, w - 1))
    x2 = max(0, min(x2, w - 1))
    y1 = max(0, min(y1, h - 1))
    y2 = max(0, min(y2, h - 1))

    roi = gray[y1:y2, x1:x2]

    if roi.size == 0:
        return False

    # Threshold image
    _, thresh = cv2.threshold(roi, 200, 255, cv2.THRESH_BINARY_INV)

    # Find contours
    contours, _ = cv2.findContours(
        thresh,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    # Count meaningful contours
    large_contours = 0
    total_area = 0

    for cnt in contours:
        area = cv2.contourArea(cnt)

        if area >= 12:
            large_contours += 1
            total_area += area

    print(
        "[SIGN CHECK]",
        "contours =", large_contours,
        "area =", total_area
    )

    # Decision
    if large_contours >= 2 or total_area >= 120:
        return True

    return False

def has_signature_dark_ratio(pdf_path, x, y, max_w, max_h, threshold=0.18):
    gray, zoom = pdf_page_to_image(pdf_path)

    h, w = gray.shape

    page_doc = fitz.open(pdf_path)
    page_height = float(page_doc[0].rect.height)
    page_doc.close()

    margin_x = 20
    margin_y = 8

    x1 = int((x + margin_x) * zoom)
    x2 = int((x + max_w - margin_x) * zoom)

    y_top_pdf = page_height - (y + max_h - margin_y)
    y_bottom_pdf = page_height - (y + margin_y)

    y1 = int(y_top_pdf * zoom)
    y2 = int(y_bottom_pdf * zoom)

    x1 = max(0, min(x1, w - 1))
    x2 = max(0, min(x2, w - 1))
    y1 = max(0, min(y1, h - 1))
    y2 = max(0, min(y2, h - 1))

    roi = gray[y1:y2, x1:x2]

    if roi.size == 0:
        return False

    dark_pixels = np.sum(roi < 80)
    ratio = dark_pixels / float(roi.size)

    print(
        "[PART V DARK CHECK]",
        "ratio =", ratio,
        "threshold =", threshold,
        "x =", x,
        "y =", y,
        "w =", max_w,
        "h =", max_h
    )

    return ratio >= threshold



def has_handwritten_signature_in_area(
    pdf_path,
    x,
    y,
    max_w,
    max_h,
    min_contours=1,
    min_area=80,
    debug_label="PART_IV"
):
    """
    Handwritten signature checker for CSF Part IV.

    Why separate from has_signature_in_area():
    - Part IV contains printed doctor name and horizontal lines.
    - Normal contour counting sees printed text as "signature".
    - This function removes horizontal lines and ignores small printed letters.

    It detects only handwriting-like strokes:
    - taller than printed letters
    - wider than normal letters
    - meaningful contour area
    """

    gray, zoom = pdf_page_to_image(pdf_path)
    h, w = gray.shape

    page_doc = fitz.open(pdf_path)
    page_height = float(page_doc[0].rect.height)
    page_doc.close()

    margin_x = 3
    margin_y = 2

    x1 = int((x + margin_x) * zoom)
    x2 = int((x + max_w - margin_x) * zoom)

    y_top_pdf = page_height - (y + max_h - margin_y)
    y_bottom_pdf = page_height - (y + margin_y)

    y1 = int(y_top_pdf * zoom)
    y2 = int(y_bottom_pdf * zoom)

    x1 = max(0, min(x1, w - 1))
    x2 = max(0, min(x2, w - 1))
    y1 = max(0, min(y1, h - 1))
    y2 = max(0, min(y2, h - 1))

    roi = gray[y1:y2, x1:x2]

    if roi.size == 0:
        print("[HAND SIGN CHECK] Empty ROI", debug_label)
        return False

    # Convert dark ink/text into white foreground.
    _, thresh = cv2.threshold(
        roi,
        200,
        255,
        cv2.THRESH_BINARY_INV
    )

    # Remove horizontal form lines/underlines because they are not signatures.
    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (45, 1)
    )
    horizontal_lines = cv2.morphologyEx(
        thresh,
        cv2.MORPH_OPEN,
        horizontal_kernel
    )
    cleaned = cv2.subtract(thresh, horizontal_lines)

    contours, _ = cv2.findContours(
        cleaned,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    handwriting_contours = 0
    handwriting_area = 0

    for cnt in contours:
        bx, by, bw, bh = cv2.boundingRect(cnt)
        area = cv2.contourArea(cnt)
        aspect = bw / float(bh or 1)

        # Ignore small printed letters.
        if bh < 18:
            continue

        # Ignore tiny noise.
        if bw < 25 or area < 40:
            continue

        # Ignore remaining very long thin horizontal marks.
        if aspect > 10 and bh < 22:
            continue

        handwriting_contours += 1
        handwriting_area += area

    print(
        "[HAND SIGN CHECK]",
        debug_label,
        "hand_contours =", handwriting_contours,
        "hand_area =", handwriting_area,
        "x =", x,
        "y =", y,
        "w =", max_w,
        "h =", max_h
    )

    return handwriting_contours >= min_contours or handwriting_area >= min_area

def add_signatures_to_csf(
    input_pdf,
    output_pdf,
    detected_doctors
):

    reader = PdfReader(input_pdf)
    writer = PdfWriter()

    page = reader.pages[0]

    page_width = float(page.mediabox.width)
    page_height = float(page.mediabox.height)

    overlay_path = input_pdf + "_overlay.pdf"

    c = canvas.Canvas(
        overlay_path,
        pagesize=(page_width, page_height)
    )

    part_iv_position = (250, 190)
    part_v_position = (50, 75)

    max_w = 130
    max_h = 35

    signed_count = 0

    part_iv_doctor = choose_part_iv_doctor(detected_doctors)

    if part_iv_doctor:
        doctor_key, setting = part_iv_doctor
        sig_path = os.path.join(SIGNATURE_FOLDER, setting["file"])

        if os.path.exists(sig_path):
            doctor_x = setting.get("x", part_iv_position[0])
            doctor_y = setting.get("y", part_iv_position[1])

            doctor_max_w = setting.get("max_w", max_w)
            doctor_max_h = setting.get("max_h", max_h)

            # Use a separate handwriting checker for CSF Part IV.
            # This prevents printed doctor name/date/lines from being treated as a signature.
            check_x = setting.get("check_x", doctor_x)
            check_y = setting.get("check_y", doctor_y)
            check_max_w = setting.get("check_max_w", doctor_max_w)
            check_max_h = setting.get("check_max_h", doctor_max_h)

            if has_handwritten_signature_in_area(
                input_pdf,
                check_x,
                check_y,
                check_max_w,
                check_max_h,
                min_contours=1,
                min_area=80,
                debug_label="CSF PART IV " + doctor_key
            ):
                print("[SKIP] Existing signature detected in PART IV")
            else:
                draw_signature_auto(
                    c,
                    sig_path,
                    doctor_x,
                    doctor_y,
                    doctor_max_w,
                    doctor_max_h
                )
                print("[+] Signed PART IV:", doctor_key)
                signed_count += 1
        else:
            print("[!] Missing PART IV signature:", sig_path)

    else:
        print("[INFO] No valid PART IV doctor detected")

    rhoda_file = os.path.join(
        SIGNATURE_FOLDER,
        "gaffud_rhoda_jacqueline.png"
    )

    if os.path.exists(rhoda_file):

        x, y = part_v_position

        if has_signature_dark_ratio(
            input_pdf,
            x,
            y,
            max_w,
            max_h,
            threshold=0.18
        ):

            print(
                "[SKIP] Existing signature "
                "detected in PART V"
            )

        else:

            draw_signature_auto(
                c,
                rhoda_file,
                x,
                y,
                max_w,
                max_h
            )

            print(
                "[+] Signed PART V: "
                "RHODA JACQUELINE P. GAFFUD"
            )

            signed_count += 1

    else:

        print(
            "[!] Missing PART V signature:",
            rhoda_file
        )

    c.save()

    if signed_count == 0:

        os.remove(overlay_path)

        return False

    overlay = PdfReader(overlay_path)

    page.merge_page(
        overlay.pages[0]
    )

    writer.add_page(page)

    for i in range(1, len(reader.pages)):
        writer.add_page(reader.pages[i])

    with open(output_pdf, "wb") as f:
        writer.write(f)

    os.remove(overlay_path)

    return True


def auto_sign_csf(pdf_path, text):
    if not is_auto_sign_csf_enabled():
        print("[AUTO SIGN] Disabled from GUI settings. CSF auto-sign skipped.")
        return False

    make_writable(pdf_path)

    detected_doctors = detect_doctors(text)

    print(
        "[DEBUG] Doctors detected:",
        [d[0] for d in detected_doctors]
    )

    temp_signed = pdf_path.replace(
        ".pdf",
        "_signed.pdf"
    )

    signed = add_signatures_to_csf(
        pdf_path,
        temp_signed,
        detected_doctors
    )

    if signed:

        os.remove(pdf_path)

        os.rename(
            temp_signed,
            pdf_path
        )

        print(
            "[+] CSF signing complete"
        )

        return True

    if os.path.exists(temp_signed):
        os.remove(temp_signed)

    return False


def merge_pdf(existing, new_file):
    make_writable(existing)

    temp_output = existing + ".tmp.pdf"

    merger = PdfMerger()

    merger.append(existing)
    merger.append(new_file)

    merger.write(temp_output)

    merger.close()

    os.remove(existing)
    safe_delete(new_file)

    os.rename(temp_output, existing)


def merge_two(p1, p2, output):
    if os.path.exists(output):

        make_writable(output)

        os.remove(output)

    merger = PdfMerger()

    merger.append(p1)
    merger.append(p2)

    merger.write(output)

    merger.close()


def is_soa2_page1_by_image(pdf_path):
    """
    Image/OCR fallback for SOA2 page 1.
    Ginagamit kapag hindi nadetect ng main OCR.
    """
    try:
        resolution = get_soa2_resolution_by_image(pdf_path)

        if resolution.doc_type in ("SOA2", "SOA2_page1"):
            print("[SOA2 IMAGE CHECK]", resolution.doc_type, "detected by image OCR")
            return True

        return False

    except Exception as e:
        print("[SOA2 PAGE1 IMAGE CHECK ERROR]", str(e))
        return False


def is_soa2_page2_by_image(pdf_path):
    """
    Image/OCR fallback for SOA2 page 2.
    Ginagamit kapag hindi nadetect ng main OCR.
    """
    try:
        resolution = get_soa2_resolution_by_image(pdf_path)

        if resolution.doc_type in ("SOA2", "SOA2_page2"):
            print("[SOA2 IMAGE CHECK]", resolution.doc_type, "detected by image OCR")
            return True

        return False

    except Exception as e:
        print("[SOA2 IMAGE CHECK ERROR]", str(e))
        return False


def get_soa2_resolution_by_image(pdf_path):
    """
    OCR full page plus lower-page crop for SOA2 completion markers.
    This helps one-page SOA2 where the conforme/signature is near the bottom.
    """
    images = convert_from_path(
        pdf_path,
        first_page=1,
        last_page=1,
        poppler_path=POPPLER_PATH
    )
    pil_img = images[0].convert("L")
    img = np.array(pil_img)
    img = cv2.resize(img, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    _, img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    processed = Image.fromarray(img)
    full_text = pytesseract.image_to_string(processed)
    width, height = processed.size
    lower_crop = processed.crop((0, int(height * 0.55), width, height))
    lower_text = pytesseract.image_to_string(lower_crop)
    resolution = resolve_soa2_text(full_text, lower_text=lower_text)

    if resolution.guarded:
        print("[SOA2 IMAGE GUARD]", "; ".join(resolution.evidence))

    return resolution



def is_anr_by_image(pdf_path):
    """
    Strong image-based fallback for ANR / Anesthesia Record.

    Why this is needed:
    - ANR scans are usually image-only.
    - Normal OCR sometimes misses "ANESTHESIA RECORD".
    - The same page can be falsely routed as SOA2_page2 because OCR sees form labels.

    This function uses:
    1. Full-page OCR
    2. Cropped top-right OCR where "ANESTHESIA RECORD" usually appears
    3. Cropped lower-section OCR for ANR-specific labels
    4. Grid/layout fallback ONLY for ANR-style anesthesia chart
    """
    try:
        images = convert_from_path(
            pdf_path,
            first_page=1,
            last_page=1,
            poppler_path=POPPLER_PATH
        )

        pil_img = images[0].convert("L")

        # Upscale for better OCR
        big = pil_img.resize(
            (pil_img.width * 2, pil_img.height * 2),
            Image.Resampling.LANCZOS
        )

        def prep_for_ocr(pil_part):
            arr = np.array(pil_part)
            arr = cv2.GaussianBlur(arr, (3, 3), 0)
            _, arr = cv2.threshold(
                arr,
                0,
                255,
                cv2.THRESH_BINARY + cv2.THRESH_OTSU
            )
            return Image.fromarray(arr)

        ocr_chunks = []

        # Full page OCR
        try:
            ocr_chunks.append(
                pytesseract.image_to_string(
                    prep_for_ocr(big),
                    config="--psm 6"
                )
            )
            ocr_chunks.append(
                pytesseract.image_to_string(
                    prep_for_ocr(big),
                    config="--psm 11"
                )
            )
        except Exception:
            pass

        bw, bh = big.size

        # ANR title is usually top-right
        crop_title = big.crop((int(bw * 0.58), 0, bw, int(bh * 0.23)))

        # ANR lower fields: detailed technique / induction / maintenance / emergence / fluid summary
        crop_bottom = big.crop((0, int(bh * 0.70), bw, bh))

        # Left side labels: hours / agents / fluids / pulse / BP etc.
        crop_left = big.crop((0, int(bh * 0.18), int(bw * 0.28), int(bh * 0.78)))

        for crop in [crop_title, crop_bottom, crop_left]:
            try:
                ocr_chunks.append(
                    pytesseract.image_to_string(
                        prep_for_ocr(crop),
                        config="--psm 6"
                    )
                )
                ocr_chunks.append(
                    pytesseract.image_to_string(
                        prep_for_ocr(crop),
                        config="--psm 11"
                    )
                )
            except Exception:
                pass

        image_text = "\n".join(ocr_chunks).lower()
        clean = normalize_text(image_text)

        anr_exact_keywords = [
            "anesthesia record",
            "anaesthesia record",
            "premedication",
            "proposed operation",
            "anesthetic agent",
            "anaesthetic agent",
            "detailed technique",
            "induction",
            "maintenance",
            "emergence",
            "fluid summary",
            "urine output in o.r",
            "urine output in o r",
            "condition of patient on departure",
            "anesthesiologist",
            "anaesthesiologist"
        ]

        exact_hits = 0
        for k in anr_exact_keywords:
            if k in image_text:
                exact_hits += 1

        if "anesthesia record" in image_text or "anaesthesia record" in image_text:
            print("[ANR IMAGE CHECK] ANR title keyword found")
            return True

        if exact_hits >= 2:
            print("[ANR IMAGE CHECK] ANR exact hits =", exact_hits)
            return True

        fuzzy_markers = [
            "ANESTHESIA RECORD",
            "ANAESTHESIA RECORD",
            "PREMEDICATION DOSE ROUTE TIME",
            "PROPOSED OPERATION",
            "ANESTHETIC AGENT",
            "ANAESTHETIC AGENT",
            "DETAILED TECHNIQUE",
            "INDUCTION MAINTENANCE EMERGENCE",
            "FLUID SUMMARY",
            "URINE OUTPUT IN O R",
            "CONDITION OF PATIENT ON DEPARTURE",
            "ANESTHESIOLOGIST"
        ]

        fuzzy_hits = 0
        for marker in fuzzy_markers:
            score = fuzz.partial_ratio(marker, clean)
            if score >= 68:
                print("[ANR IMAGE FUZZ]", marker, "score =", score)
                fuzzy_hits += 1

        if fuzzy_hits >= 2:
            print("[ANR IMAGE CHECK] ANR fuzzy hits =", fuzzy_hits)
            return True

        # Layout fallback for ANR anesthesia chart.
        # This is allowed only inside this function; caller guards against strong SOA1.
        img = np.array(pil_img)
        img = cv2.resize(img, (900, 1200))
        blur = cv2.GaussianBlur(img, (3, 3), 0)
        edges = cv2.Canny(blur, 30, 120)

        # ANR has dense center graph/grid.
        center = edges[230:930, 120:830]

        horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (24, 1))
        vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 24))

        horizontal = cv2.morphologyEx(center, cv2.MORPH_OPEN, horizontal_kernel)
        vertical = cv2.morphologyEx(center, cv2.MORPH_OPEN, vertical_kernel)

        h_score = int(np.sum(horizontal > 0))
        v_score = int(np.sum(vertical > 0))
        edge_score = int(np.sum(center > 0))
        dark_pixels = int(np.sum(img < 120))

        print(
            "[ANR IMAGE GRID]",
            "h_score =", h_score,
            "v_score =", v_score,
            "edge_score =", edge_score,
            "dark =", dark_pixels,
            "ocr_hits =", exact_hits,
            "fuzzy_hits =", fuzzy_hits
        )

        # Conservative: require both grid shape and at least weak ANR OCR hint.
        weak_anr_hint = (
            fuzz.partial_ratio("ANESTHESIA RECORD", clean) >= 58
            or fuzz.partial_ratio("DETAILED TECHNIQUE", clean) >= 58
            or fuzz.partial_ratio("FLUID SUMMARY", clean) >= 58
            or "anest" in image_text
            or "anaest" in image_text
        )

        if weak_anr_hint and h_score > 6500 and v_score > 4500 and edge_score > 18000:
            print("[ANR IMAGE CHECK] ANR grid/layout detected")
            return True

        return False

    except Exception as e:
        print("[ANR IMAGE CHECK ERROR]", str(e))
        return False

def is_ecg_by_image(pdf_path):
    """
    Image-based fallback for ECG forms.
    Ginagamit lang ito kapag OCR doc_type is UNKNOWN.
    Hindi nito binabago ang existing OCR keywords; dagdag fallback lang ito.
    """
    try:
        images = convert_from_path(
            pdf_path,
            first_page=1,
            last_page=1,
            poppler_path=POPPLER_PATH
        )

        pil_img = images[0].convert("L")

        try:
            image_text = pytesseract.image_to_string(pil_img).lower()
        except Exception:
            image_text = ""

        ecg_text_keywords = [
            "electrocardiogram",
            "ecg",
            "normal sinus rhythm",
            "sinus rhythm",
            "ventricular rate",
            "pr interval",
            "qrs duration",
            "qt/qtc",
            "borderline abnormal ecg"
        ]

        if any(k in image_text for k in ecg_text_keywords):
            print("[ECG IMAGE CHECK] ECG text keyword found")
            return True

        img = np.array(pil_img)

        img = cv2.resize(img, (900, 1200))

        blur = cv2.GaussianBlur(img, (3, 3), 0)

        edges = cv2.Canny(blur, 30, 120)

        horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 1))
        vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 30))

        horizontal = cv2.morphologyEx(edges, cv2.MORPH_OPEN, horizontal_kernel)
        vertical = cv2.morphologyEx(edges, cv2.MORPH_OPEN, vertical_kernel)

        h_score = int(np.sum(horizontal > 0))
        v_score = int(np.sum(vertical > 0))
        edge_score = int(np.sum(edges > 0))
        dark_pixels = int(np.sum(img < 120))

        line_score = h_score + v_score

        print(
            "[ECG IMAGE CHECK]",
            "h_score =", h_score,
            "v_score =", v_score,
            "edge_score =", edge_score,
            "dark =", dark_pixels,
            "line_score =", line_score
        )

        if line_score > 2500 and edge_score > 12000 and dark_pixels > 12000:
            return True

        if h_score > 1800 and dark_pixels > 18000:
            return True

        return False

    except Exception as e:
        print("[ECG IMAGE CHECK ERROR]", str(e))
        return False

def extract_cf2_doctor_section(text):
    """
    Extract only CF2 Page 2 doctor/professional fee section.

    Target area:
    - 10. Accreditation Number
    - Name of Accredited Health Care Professional
    - No co-pay on top
    - With co-pay on top

    Purpose:
    Para hindi madamay ang RHODA sa lower Chief of Hospital II section.
    """

    upper_text = text.upper()

    start_markers = [
        "10. ACCREDITATION NUMBER",
        "10 ACCREDITATION NUMBER",
        "ACCREDITATION NUMBER",
        "ACCREDITATION NO",
        "NAME OF ACCREDITED HEALTH CARE PROFESSIONAL"
    ]

    end_markers = [
        "PART III",
        "CERTIFICATION OF CONSUMPTION",
        "PART IV",
        "AUTHORIZED HCI REPRESENTATIVE",
        "CHIEF OF HOSPITAL"
    ]

    start = -1

    for marker in start_markers:
        pos = upper_text.find(marker)
        if pos != -1:
            start = pos
            break

    if start == -1:
        print("[!] CF2 doctor section start not found, using first half of OCR text")
        return upper_text[:len(upper_text) // 2]

    end = len(upper_text)

    for marker in end_markers:
        pos = upper_text.find(marker, start + 20)
        if pos != -1:
            end = pos
            break

    section = upper_text[start:end]

    print("\n[DEBUG CF2 DOCTOR SECTION]")
    print(section[:1200])
    print("[END DEBUG CF2 DOCTOR SECTION]\n")

    return section

def get_cf2_hci_representative():
    """
    Return the single CF2 Authorized HCI Representative signature config.

    The hospital currently uses RHODA / Chief of Hospital for this lower-left
    CF2 signature. Keeping this tied to the doctor config lets the GUI control
    the signature file and coordinates without another JSON file.
    """
    fallback = {
        "file": "gaffud_rhoda_jacqueline.png",
        "cf2_hci_x": 45,
        "cf2_hci_y": 25,
        "cf2_hci_max_w": 135,
        "cf2_hci_max_h": 30,
    }

    for doctor_key, setting in DOCTOR_SETTINGS.items():
        aliases = " ".join(setting.get("aliases", []))
        haystack = normalize_text(doctor_key + " " + aliases + " " + setting.get("file", ""))

        if (
            "RHODA" in haystack
            or "GAFFUD RHODA" in haystack
            or "GAFFUD_RHODA" in doctor_key
            or setting.get("file") == fallback["file"]
        ):
            rep = fallback.copy()
            rep["file"] = setting.get("file") or fallback["file"]
            rep["cf2_hci_x"] = setting.get("cf2_hci_x") or fallback["cf2_hci_x"]
            rep["cf2_hci_y"] = setting.get("cf2_hci_y") or fallback["cf2_hci_y"]
            rep["cf2_hci_max_w"] = setting.get("cf2_hci_max_w") or fallback["cf2_hci_max_w"]
            rep["cf2_hci_max_h"] = setting.get("cf2_hci_max_h") or fallback["cf2_hci_max_h"]
            return rep

    return fallback

def auto_sign_cf2_page2(pdf_path, text):
    """
    Auto-sign CF2 page 2 only.

    Signature 1:
    - Doctor name is below Accreditation No.
    - Detect doctor using DOCTOR_SETTINGS aliases.

    Signature 2:
    - RHODA JACQUELINE P. GAFFUD
    - Left side / before Chief of Hospital II area.

    NOTE:
    Coordinates may need adjustment depending on your scan layout.
    """

    if not is_auto_sign_cf2_enabled():
        print("[AUTO SIGN] Disabled from GUI settings. CF2 auto-sign skipped.")
        return False

    make_writable(pdf_path)
    reload_dynamic_doctor_settings()

    cf2_doctor_section = extract_cf2_doctor_section(text)
    clean = normalize_text(cf2_doctor_section)

    detected_doctor = None

    for doctor_key, setting in DOCTOR_SETTINGS.items():
        best_score = 0
        best_alias = ""

        for alias in setting["aliases"]:
            score = fuzz.partial_ratio(
                normalize_text(alias),
                clean
            )

            if score > best_score:
                best_score = score
                best_alias = alias

        print("[CF2 DOCTOR FUZZ]", doctor_key, "score =", best_score, "alias =", best_alias)

        if best_score >= 82:
            detected_doctor = (doctor_key, setting)
            break

    reader = PdfReader(pdf_path)
    writer = PdfWriter()

    page = reader.pages[0]
    page_width = float(page.mediabox.width)
    page_height = float(page.mediabox.height)

    overlay_path = pdf_path + "_cf2_overlay.pdf"

    c = canvas.Canvas(
        overlay_path,
        pagesize=(page_width, page_height)
    )

    signed_count = 0
    
    
    #page_width = float(page.mediabox.width)
    #page_height = float(page.mediabox.height)

    #print("[CF2 PAGE SIZE] width =", page_width, "height =", page_height)

    # =========================
    # MANUAL COORDINATES AREA
    # =========================
    # Adjust mo ito kapag mataas/mababa ang pirma.
    # x = left/right
    # y = taas/baba
    # max_w/max_h = laki ng signature

    # =========================
    # MANUAL COORDINATES AREA
    # =========================

    # Default CF2 doctor position
    # gagamitin lang ito kapag walang cf2_x/cf2_y config ang doctor
    default_cf2_doctor_x = 95
    default_cf2_doctor_y = 705
    default_cf2_doctor_max_w = 120
    default_cf2_doctor_max_h = 25

    cf2_hci_rep = get_cf2_hci_representative()
    rhoda_x = cf2_hci_rep["cf2_hci_x"]
    rhoda_y = cf2_hci_rep["cf2_hci_y"]
    rhoda_max_w = cf2_hci_rep["cf2_hci_max_w"]
    rhoda_max_h = cf2_hci_rep["cf2_hci_max_h"]

    # =========================
    # SIGN DOCTOR
    # =========================
    if detected_doctor:

        doctor_key, setting = detected_doctor

        sig_path = os.path.join(
            SIGNATURE_FOLDER,
            setting["file"]
        )

        if os.path.exists(sig_path):

            # PER-DOCTOR CF2 CONFIG
            doctor_x = setting.get("cf2_x") or default_cf2_doctor_x
            doctor_y = setting.get("cf2_y") or default_cf2_doctor_y
            doctor_max_w = setting.get("cf2_max_w") or default_cf2_doctor_max_w
            doctor_max_h = setting.get("cf2_max_h") or default_cf2_doctor_max_h

            # CF2 boxes contain printed text/lines, so use the handwriting
            # checker to avoid false SKIP from form labels.
            if has_handwritten_signature_in_area(
                pdf_path,
                doctor_x,
                doctor_y,
                doctor_max_w,
                doctor_max_h,
                min_contours=1,
                min_area=60,
                debug_label="CF2 DOCTOR " + doctor_key
            ):

                print(
                    "[SKIP] Existing CF2 doctor signature detected"
                )

            else:

                draw_signature_auto(
                    c,
                    sig_path,
                    doctor_x,
                    doctor_y,
                    doctor_max_w,
                    doctor_max_h
                )

                print(
                    "[+] Signed CF2 doctor:",
                    doctor_key
                )

                signed_count += 1

        else:

            print(
                "[!] Missing CF2 doctor signature file:",
                sig_path
            )

    else:

        print("[INFO] No CF2 doctor detected")

    # =========================
    # SIGN RHODA / HCI REPRESENTATIVE
    # =========================
    rhoda_file = os.path.join(
        SIGNATURE_FOLDER,
        cf2_hci_rep["file"]
    )

    if os.path.exists(rhoda_file):
        if has_handwritten_signature_in_area(
            pdf_path,
            rhoda_x,
            rhoda_y,
            rhoda_max_w,
            rhoda_max_h,
            min_contours=1,
            min_area=60,
            debug_label="CF2 HCI REPRESENTATIVE"
        ):
            print("[SKIP] Existing CF2 Rhoda signature detected")
        else:
            draw_signature_auto(
                c,
                rhoda_file,
                rhoda_x,
                rhoda_y,
                rhoda_max_w,
                rhoda_max_h
            )

            print("[+] Signed CF2 Rhoda / HCI Representative")
            signed_count += 1
    else:
        print("[!] Missing Rhoda signature file:", rhoda_file)

    c.save()

    if signed_count == 0:
        os.remove(overlay_path)
        return False

    overlay = PdfReader(overlay_path)

    page.merge_page(overlay.pages[0])
    writer.add_page(page)

    for i in range(1, len(reader.pages)):
        writer.add_page(reader.pages[i])

    temp_signed = pdf_path.replace(".pdf", "_cf2_signed.pdf")

    with open(temp_signed, "wb") as f:
        writer.write(f)

    os.remove(overlay_path)
    os.remove(pdf_path)
    os.rename(temp_signed, pdf_path)

    print("[+] CF2 page 2 signing complete")
    return True

def resolve_soa_doc_type(path, text, doc_type):
    """
    Shared SOA correction logic.
    Converts broad SOA into SOA1 / SOA2_page1 / SOA2_page2.
    Also protects ANR false positive.
    """
    if doc_type in ("SOA", "SOA2_page1", "SOA2_page2", "UNKNOWN") and is_coe_text(text):
        print("[+] Corrected COE before SOA merge")
        return "COE"

    if doc_type == "SOA":
        if (not is_soa1_text(text)) and (not is_soa2_page1_text(text)) and is_anr_by_image(path):
            doc_type = "ANR"
            print("[+] Corrected SOA false positive -> ANR")
        else:
            doc_type = detect_soa_type(text)

        if doc_type == "SOA1" and not is_soa1_text(text):
            try:
                image_resolution = get_soa2_resolution_by_image(path)
            except Exception as exc:
                print("[SOA2 IMAGE FALLBACK ERROR]", str(exc))
                image_resolution = None
            if image_resolution and image_resolution.is_soa2:
                doc_type = image_resolution.doc_type
                print("[+] SOA fallback corrected by image:", doc_type)
            else:
                print("[INFO] SOA broad fallback saved as SOA1")

    if doc_type == "SOA2_page2" and (not is_soa1_text(text)) and (not is_soa2_page1_text(text)):
        if is_anr_by_image(path):
            doc_type = "ANR"
            print("[+] Final correction: SOA2_page2 -> ANR")

    return doc_type



# ==================================================
# DEFERRED UNKNOWN REVIEW
# ==================================================
DEFERRED_UNKNOWN_TYPES = [
    "SOA2_page2_1",
    "MRF_page2_1",
    "MRF_page2",
    "MRF_page1",
    "SOA2_page2",
    "SOA2_page1",
    "UNKNOWN"
]


def handle_deferred_unknown_review(output_pdf_path, doc_type, ocr_text):
    """
    Trainer popup only AFTER PDF already saved to OUTPUT folder.
    """

    if doc_type not in DEFERRED_UNKNOWN_TYPES:
        return doc_type

    print("[DEFERRED UNKNOWN TRAINER]")
    print("Saved to output first ->", doc_type)

    selected = ask_unknown_doc_type(output_pdf_path, ocr_text)

    print("[DEFERRED UNKNOWN TRAINER] User selected:", selected)

    return selected


def classify_pdf_for_processing(path, text):
    doc_type = detect_doc(text)

    if doc_type == "UNKNOWN":
        trained_type = detect_doc_from_unknown_training(path, text)

        if trained_type:
            doc_type = trained_type
            print("[+] UNKNOWN Trainer detection:", doc_type)

    if doc_type == "UNKNOWN":
        if (not is_soa1_text(text)) and (not is_soa2_page1_text(text)) and is_anr_by_image(path):
            doc_type = "ANR"
            print("[+] Image-based detection: ANR")
        elif is_ecg_by_image(path):
            doc_type = "DTR"
            print("[+] Image-based detection: ECG -> DTR")
        elif is_soa2_page1_by_image(path):
            doc_type = "SOA2_page1"
            print("[+] Image-based detection: SOA2_page1")
        elif is_soa2_page2_by_image(path):
            doc_type = "SOA2_page2"
            print("[+] Image-based detection: SOA2_page2")

    if doc_type == "UNKNOWN" and visual_document_learner is not None:
        _, visual_prediction = visual_document_learner.predict(
            path,
            deterministic_type=doc_type,
            record=True,
        )
        if visual_prediction.has_suggestion:
            print("[VISUAL LEARNING]", visual_prediction.summary())
            if visual_prediction.candidates:
                print(
                    "[VISUAL LEARNING] Candidate scores:",
                    ", ".join(
                        f"{candidate.doc_type}={candidate.confidence:.1%}"
                        for candidate in visual_prediction.candidates
                    ),
                )
            if visual_prediction.evidence:
                print(
                    "[VISUAL LEARNING] Evidence:",
                    ", ".join(visual_prediction.evidence),
                )
            if visual_prediction.blocked_reason:
                print("[VISUAL LEARNING] Guard:", visual_prediction.blocked_reason)
            if visual_prediction.auto_applied:
                doc_type = visual_prediction.predicted_type
                print("[VISUAL LEARNING] Final source=VISUAL_AUTO type=", doc_type)
            else:
                print("[VISUAL LEARNING] Final source=MANUAL_REVIEW type=UNKNOWN")
        elif visual_prediction.blocked_reason:
            print("[VISUAL LEARNING] Deferred:", visual_prediction.blocked_reason)

    if doc_type == "UNKNOWN" and "SOA2_page1" in soa2_pages:
        print("[DEFERRED UNKNOWN TRAINER] Pending SOA2 merge detected. No popup during processing.")

    elif doc_type == "UNKNOWN" and "MRF_page1" in mrf_pages:
        print("[DEFERRED UNKNOWN TRAINER] Pending MRF merge detected. No popup during processing.")

    elif doc_type == "UNKNOWN":
        print("[DEFERRED UNKNOWN TRAINER] Popup delayed until AFTER output save.")

    if doc_type == "SOA2_page2_1":
        doc_type = "UNKNOWN"
        print("[UNKNOWN TRAINER] SOA2_page2_1 treated as UNKNOWN for manual review")

    if doc_type == "MRF_page2_1":
        doc_type = "UNKNOWN"
        print("[UNKNOWN TRAINER] MRF_page2_1 treated as UNKNOWN for manual review")

    if doc_type == "CSF":
        print("[INFO] CSF detected. Patient name will use SOA1 DB or COE fallback, not CSF OCR.")

    return resolve_soa_doc_type(path, text, doc_type)


def build_patient_groups(files, ocr_cache):
    groups = []
    current_group = []

    for file in files:
        if not file.lower().endswith(".pdf"):
            continue

        path = os.path.join(SCAN_FOLDER, file)

        if not os.path.exists(path):
            continue

        try:
            text = ocr_cache.get(path)

            if text is None:
                text = ocr_pdf(path)
                ocr_cache[path] = text

            doc_type = classify_pdf_for_processing(path, text)
        except Exception as e:
            print("[OCR ERROR]", file, str(e))
            text = ""
            doc_type = "UNKNOWN"

        if doc_type == "CSF" and current_group:
            groups.append(current_group)
            current_group = []
            print("[BATCH] New CSF detected. Starting next patient group.")

        current_group.append({
            "file": file,
            "path": path,
            "text": text,
            "initial_doc_type": doc_type
        })

    if current_group:
        groups.append(current_group)

    return groups


def find_group_identity(group):
    hospital_no = None
    coe_patient_name = None
    admdate = None
    disdate = None
    soa2_page1_dates = (None, None)
    soa2_patient_name = ""
    hospital_no_resolution = HospitalNumberResolution()
    identity_resolution = IdentityResolution()

    for item in group:
        doc_type = item.get("initial_doc_type")
        text = item.get("text") or ""

        if doc_type == "SOA1" and not hospital_no:
            hospital_no = extract_hospital_no_from_soa1(text)

        if doc_type == "COE" and not coe_patient_name:
            coe_patient_name = extract_patient_name_from_coe(text)

        if doc_type in ("SOA2", "SOA2_page2") and not (admdate and disdate):
            admdate, disdate = extract_adm_dis_from_soa_text(text)

        if doc_type in ("SOA2", "SOA2_page1"):
            soa2_page1_dates = extract_adm_dis_from_soa_text(text)

        if doc_type in ("SOA2", "SOA2_page1", "SOA2_page2") and not soa2_patient_name:
            soa2_patient_name = extract_patient_name_from_soa_text(text)

    if not (admdate and disdate):
        admdate, disdate = soa2_page1_dates

    if not hospital_no:
        hospital_no_resolution = resolve_hospital_number(
            group,
            patient_lookup=get_patient_from_db_by_hpercode,
            poppler_path=POPPLER_PATH,
            tesseract_cmd=pytesseract.pytesseract.tesseract_cmd,
        )
        if hospital_no_resolution.has_suggestion:
            print("[HOSPITAL NO FALLBACK]", hospital_no_resolution.summary())
        if hospital_no_resolution.should_auto_accept:
            hospital_no = hospital_no_resolution.hospital_no
            print(
                "[HOSPITAL NO FALLBACK] Auto-accepted:",
                hospital_no,
                f"confidence={hospital_no_resolution.confidence}",
            )

    if not hospital_no:
        fallback_name = soa2_patient_name or coe_patient_name or ""
        try:
            identity_resolution = resolve_identity_from_soa2(
                soa_patient_name=fallback_name,
                admission_date=admdate or "",
                discharge_date=disdate or "",
                connection_factory=create_hbsys_connection,
            )
        except Exception as exc:
            print("[SOA2 IDENTITY FALLBACK ERROR]", str(exc))
        else:
            if identity_resolution.has_suggestion:
                print("[SOA2 IDENTITY FALLBACK]", identity_resolution.summary())
            if identity_resolution.should_auto_accept:
                hospital_no = identity_resolution.hospital_no
                if not coe_patient_name:
                    coe_patient_name = identity_resolution.patient_name
                admdate = identity_resolution.admission_date or admdate
                disdate = identity_resolution.discharge_date or disdate
                print(
                    "[SOA2 IDENTITY FALLBACK] Auto-accepted:",
                    hospital_no,
                    f"confidence={identity_resolution.confidence}",
                )

    return (
        hospital_no,
        coe_patient_name,
        admdate,
        disdate,
        hospital_no_resolution,
        identity_resolution,
    )


def set_patient_context_for_group(group, group_index):
    global current_patient, current_patient_base_name, current_hospital_no

    (
        hospital_no,
        coe_patient_name,
        admdate,
        disdate,
        hospital_no_resolution,
        identity_resolution,
    ) = find_group_identity(group)
    row = None

    current_patient = None
    current_patient_base_name = None
    current_hospital_no = None
    reset_patient_page_trackers()

    if hospital_no:

        # -------------------------------
        # SINGLE PATIENT MODE
        # -------------------------------
        if PROCESS_MODE == "single" and CONFIRM_PATIENT:

            row = confirm_patient_from_db_by_hpercode(hospital_no)

        # -------------------------------
        # MULTIPLE PATIENT MODE
        # -------------------------------
        else:

            row = get_patient_from_db_by_hpercode(hospital_no)

        if not row:
            fallback_name = ""
            for item in group:
                if item.get("initial_doc_type") in ("SOA2", "SOA2_page1", "SOA2_page2"):
                    fallback_name = extract_patient_name_from_soa_text(item.get("text") or "")
                    if fallback_name:
                        break
            if not fallback_name:
                fallback_name = coe_patient_name or ""

            try:
                db_identity_resolution = resolve_identity_from_soa2(
                    soa_patient_name=fallback_name,
                    admission_date=admdate or "",
                    discharge_date=disdate or "",
                    connection_factory=create_hbsys_connection,
                )
            except Exception as exc:
                print("[SOA2 IDENTITY FALLBACK ERROR]", str(exc))
            else:
                if db_identity_resolution.has_suggestion:
                    identity_resolution = db_identity_resolution
                    print("[SOA2 IDENTITY FALLBACK]", identity_resolution.summary())
                if db_identity_resolution.should_auto_accept:
                    hospital_no = db_identity_resolution.hospital_no
                    admdate = db_identity_resolution.admission_date or admdate
                    disdate = db_identity_resolution.discharge_date or disdate
                    row = get_patient_from_db_by_hpercode(hospital_no)
                    if row:
                        print(
                            "[SOA2 IDENTITY FALLBACK] Replaced invalid Hospital No with:",
                            hospital_no,
                            f"confidence={db_identity_resolution.confidence}",
                        )

        if row:
            current_patient_base_name = build_patient_name_from_db(row)
            current_hospital_no = normalize_hospital_no(row.get("hpercode"))

    # Multiple-patient processing must not stop for a problematic patient.
    # Gather facts here; all decisions remain inside PatientValidator.
    if PROCESS_MODE == "multiple":
        detected_types = {
            item.get("initial_doc_type") for item in group
        }
        score = None
        database_name = ""
        admission_count = 0
        admission_selected = False
        encounter_no = ""
        admission_lookup_failed = False

        if row:
            database_name = build_patient_name_from_db(row)

            if not (admdate and disdate):
                metadata_dates = resolve_admission_dates_from_metadata(
                    hospital_no=hospital_no or "",
                    candidate_paths=(
                        item.get("path") or item.get("file") or ""
                        for item in group
                    ),
                    backup_root=BACKUP_FOLDER,
                )
                if metadata_dates.found:
                    admdate = metadata_dates.admission_date
                    disdate = metadata_dates.discharge_date
                    print(
                        "[ADMISSION DATE FALLBACK]",
                        f"ADM{admdate}_DIS{disdate}",
                        f"source={metadata_dates.source}",
                        metadata_dates.evidence,
                    )

            try:
                admission_match = create_admission_lookup().match(
                    hospital_no,
                    admdate or "",
                    disdate or "",
                )
                admission_count = len(admission_match.admissions)
                if admission_match.selected:
                    admission_selected = True
                    encounter_no = admission_match.selected.encounter_no
                    admdate = admission_match.selected.admission_date or admdate
                    disdate = admission_match.selected.discharge_date or disdate
            except AdmissionLookupError as exc:
                admission_lookup_failed = True
                print("[ADMISSION LOOKUP ERROR]", str(exc))

        duplicates = batch_patient_tracker.check_and_register(
            hospital_no or "", encounter_no
        )

        validation = patient_validator.validate(
            PatientValidationContext(
                hospital_no=hospital_no or "",
                database_patient_found=row is not None,
                database_patient_name=database_name,
                detected_patient_name=coe_patient_name or "",
                name_match_score=score,
                has_soa1="SOA1" in detected_types,
                has_coe="COE" in detected_types,
                ocr_failed=all(
                    not (item.get("text") or "").strip() for item in group
                ),
                admission_count=admission_count,
                admission_required=row is not None,
                admission_selected=admission_selected,
                duplicate_hospital_no=duplicates.duplicate_hospital_no,
                duplicate_encounter=duplicates.duplicate_encounter,
            )
        )

        if not validation.passed:
            primary = validation.primary_issue
            all_issues = "; ".join(
                f"{issue.reason.value}: {issue.message}"
                for issue in validation.issues
            )
            if admission_lookup_failed:
                all_issues += "; MySQL admission lookup was unavailable"
            if hospital_no_resolution.has_suggestion:
                all_issues += "; " + hospital_no_resolution.summary()
            if identity_resolution.has_suggestion:
                all_issues += "; " + identity_resolution.summary()
            review_documents = tuple(item["path"] for item in group)
            review_id = review_queue.add_patient(
                primary.reason,
                hospital_no=hospital_no or "",
                patient_name=database_name or coe_patient_name or "",
                admission_date=admdate or "",
                discharge_date=disdate or "",
                encounter_no=encounter_no,
                confidence=(
                    score
                    or hospital_no_resolution.confidence
                    or identity_resolution.confidence
                    or 0
                ),
                folder=SCAN_FOLDER,
                documents=review_documents,
                reason_detail=all_issues,
                batch_id=os.path.basename(os.path.normpath(SCAN_FOLDER)),
            )
            try:
                staged_documents = review_staging_manager.stage_documents(
                    review_id,
                    review_documents,
                )
                print(
                    "[REVIEW STAGING] Patient group moved out of scans:",
                    f"review_id={review_id}",
                    f"files={len(staged_documents)}",
                )
            except Exception as exc:
                print("[REVIEW STAGING ERROR]", str(exc))
                print(
                    "[REVIEW STAGING ERROR] Patient group remains deferred, "
                    "but source files could not be moved out of scans."
                )
                raise RuntimeError(
                    "Review staging failed; stop processing to avoid mixing "
                    "review PDFs with new scans."
                ) from exc
            print(
                "[REVIEW QUEUE] Patient group deferred:",
                ", ".join(validation.reason_codes),
            )
            return False

    if not current_patient_base_name and coe_patient_name:
        current_patient_base_name = coe_patient_name
        current_hospital_no = normalize_hospital_no(hospital_no)
        print("[COE FALLBACK] Using patient name from COE OCR:", current_patient_base_name)

    if not current_patient_base_name:
        current_patient_base_name = f"UNKNOWN_PATIENT_GROUP_{group_index:03d}"
        current_hospital_no = normalize_hospital_no(hospital_no)
        print("[PATIENT WARNING] No SOA1 DB name or COE name. Using:", current_patient_base_name)

    current_patient = build_patient_folder_name(
        current_patient_base_name,
        current_hospital_no,
        admdate,
        disdate
    )

    print("[+] PATIENT GROUP READY:", current_patient)

    return True


def set_patient_context_from_review(review_item):
    """Use the identity already corrected and revalidated in Review Queue."""
    global current_patient, current_patient_base_name, current_hospital_no

    current_patient_base_name = review_item.patient_name
    current_hospital_no = review_item.hospital_no
    reset_patient_page_trackers()
    current_patient = build_patient_folder_name(
        review_item.patient_name,
        review_item.hospital_no,
        review_item.admission_date,
        review_item.discharge_date,
    )
    print("[RESUME] VERIFIED PATIENT CONTEXT:", current_patient)
    return True


def process_patient_group(group, group_index, review_item=None):
    global soa2_pages, mrf_pages, pbc_pages, cf2_pages, cf2_page_texts

    context_ready = (
        set_patient_context_from_review(review_item)
        if review_item is not None
        else set_patient_context_for_group(group, group_index)
    )
    if not context_ready:
        return False

    if review_item is not None:
        backup_original_paths([item["path"] for item in group])
    else:
        backup_original_scans([item["file"] for item in group])

    print(f"\n[+] PROCESSING PATIENT GROUP {group_index}: {current_patient}\n")

    for item in group:
        file = item["file"]
        path = item["path"]

        if not os.path.exists(path):
            continue

        text = item.get("text") or ""

        print("\n--- OCR ---")
        print(text[:800])

        doc_type = classify_pdf_for_processing(path, text)

        if doc_type == "SOA1":
            hospital_no = extract_hospital_no_from_soa1(text)

            if hospital_no:
                print("[INFO] SOA1 Hospital No seen during processing:", hospital_no)
                print("[INFO] Confirmed patient folder remains:", current_patient)

        folder = os.path.join(OUTPUT_FOLDER, current_patient)
        os.makedirs(folder, exist_ok=True)

        # -------------------------
        # CF2 PAGE MERGE
        # -------------------------
        if doc_type.startswith("CF2_page"):
            saved_page = safe_move(
                path,
                os.path.join(folder, doc_type + ".pdf")
            )

            cf2_pages[doc_type] = saved_page
            cf2_page_texts[doc_type] = text

            print("[+] Saved CF2 page:", doc_type, "->", saved_page)

            if "CF2_page1" in cf2_pages and "CF2_page2" in cf2_pages:
                out = os.path.join(folder, "CF2.pdf")

                auto_sign_cf2_page2(
                    cf2_pages["CF2_page2"],
                    cf2_page_texts.get("CF2_page2", text)
                )

                merge_two(
                    cf2_pages["CF2_page1"],
                    cf2_pages["CF2_page2"],
                    out
                )

                if os.path.exists(cf2_pages["CF2_page1"]):
                    os.remove(cf2_pages["CF2_page1"])

                if os.path.exists(cf2_pages["CF2_page2"]):
                    os.remove(cf2_pages["CF2_page2"])

                cf2_pages = {}
                cf2_page_texts = {}

                print("[+] CF2 merged")

            continue

        # -------------------------
        # PBC PAGE MERGE
        # -------------------------
        if doc_type.startswith("PBC_page"):
            saved_page = safe_move(
                path,
                os.path.join(folder, doc_type + ".pdf")
            )

            pbc_pages[doc_type] = saved_page

            print("[+] Saved PBC page:", doc_type, "->", saved_page)

            if "PBC_page1" in pbc_pages and "PBC_page2" in pbc_pages:
                page1 = pbc_pages["PBC_page1"]
                page2 = pbc_pages["PBC_page2"]

                if not os.path.exists(page1):
                    print("[!] PBC_page1 file missing, cannot merge:", page1)
                    continue

                if not os.path.exists(page2):
                    print("[!] PBC_page2 file missing, cannot merge:", page2)
                    continue

                out = os.path.join(folder, "PBC.pdf")

                merge_two(page1, page2, out)

                if os.path.exists(page1):
                    os.remove(page1)

                if os.path.exists(page2):
                    os.remove(page2)

                pbc_pages = {}

                print("[+] PBC merged")

            continue

        # -------------------------
        # MRF PAGE MERGE
        # -------------------------
        if doc_type.startswith("MRF_page"):
            saved_page = safe_move(
                path,
                os.path.join(folder, doc_type + ".pdf")
            )

            mrf_pages[doc_type] = saved_page

            print("[+] Saved MRF page:", doc_type, "->", saved_page)

            if "MRF_page1" in mrf_pages and "MRF_page2" in mrf_pages:
                page1 = mrf_pages["MRF_page1"]
                page2 = mrf_pages["MRF_page2"]

                if not os.path.exists(page1):
                    print("[!] MRF_page1 file missing, cannot merge:", page1)
                    continue

                if not os.path.exists(page2):
                    print("[!] MRF_page2 file missing, cannot merge:", page2)
                    continue

                out = os.path.join(folder, "MRF.pdf")

                merge_two(page1, page2, out)

                if os.path.exists(page1):
                    os.remove(page1)

                if os.path.exists(page2):
                    os.remove(page2)

                mrf_pages = {}

                print("[+] MRF merged")

            continue

        # -------------------------
        # SOA2 PAGE MERGE
        # -------------------------
        if doc_type.startswith("SOA2"):
            target_name = doc_type + ".pdf"
            if doc_type == "SOA2":
                target_name = "SOA2.pdf"
                if os.path.exists(os.path.join(folder, target_name)):
                    target_name = "UNKNOWN_SOA2_DUPLICATE.pdf"
                    print("[SOA2 DUPLICATE] Existing SOA2.pdf kept; extra SOA2 sent to Unknown Review.")

            saved_page = safe_move(
                path,
                os.path.join(folder, target_name)
            )

            soa2_pages[doc_type] = saved_page

            if doc_type == "SOA2":
                print("[+] SOA2 complete single-page saved:", saved_page)
                continue

            if "SOA2_page1" in soa2_pages and "SOA2_page2" in soa2_pages:
                out = os.path.join(folder, "SOA2.pdf")

                if os.path.exists(out):
                    out = os.path.join(folder, "UNKNOWN_SOA2_DUPLICATE.pdf")
                    print("[SOA2 DUPLICATE] Existing SOA2.pdf kept; merged duplicate sent to Unknown Review.")

                merge_two(
                    soa2_pages["SOA2_page1"],
                    soa2_pages["SOA2_page2"],
                    out
                )

                if os.path.exists(soa2_pages["SOA2_page1"]):
                    os.remove(soa2_pages["SOA2_page1"])

                if os.path.exists(soa2_pages["SOA2_page2"]):
                    os.remove(soa2_pages["SOA2_page2"])

                soa2_pages = {}

                print("[+] SOA2 merged ->", os.path.basename(out))

            continue

        # -------------------------
        # MULTI-PAGE APPEND DOCS
        # DTR only. CF2 uses CF2_page1/page2 logic above.
        # -------------------------
        if doc_type in MULTI_PAGE:
            out_file = os.path.join(folder, doc_type + ".pdf")

            if os.path.exists(out_file):
                merge_pdf(out_file, path)
                print("[+] Merged ->", doc_type)
            else:
                safe_move(path, out_file)
                print("[+] Created ->", doc_type)

            continue

        # -------------------------
        # NORMAL SINGLE DOCS
        # -------------------------
        out_file = os.path.join(folder, doc_type + ".pdf")

        if doc_type == "ANR" and os.path.exists(out_file):
            make_writable(out_file)
            os.remove(out_file)

        saved = safe_move(path, out_file)

        if doc_type == "CSF":
            auto_sign_csf(saved, text)

        print("[+] Saved ->", doc_type)

    return True


def choose_processing_mode():
    """
    Startup dialog.

    Returns:
        "single"
        "multiple"
    """

    global PROCESS_MODE
    global CONFIRM_PATIENT

    env_mode = os.environ.get("CLAIMS_PROCESS_MODE", "").strip().lower()
    if env_mode in {"single", "multiple"}:
        PROCESS_MODE = env_mode
        CONFIRM_PATIENT = os.environ.get(
            "CLAIMS_CONFIRM_PATIENT", "1"
        ).strip().lower() in {"1", "true", "yes", "y", "on"}
        print("PROCESS_MODE =", PROCESS_MODE)
        print("CONFIRM_PATIENT =", CONFIRM_PATIENT)
        return

    if tk is None:
        PROCESS_MODE = "single"
        CONFIRM_PATIENT = True
        return

    root = tk.Tk()
    root.title("Claims Bot")

    root.geometry("420x230")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    mode = tk.StringVar(value="single")
    confirm = tk.BooleanVar(value=True)

    tk.Label(
        root,
        text="CLAIMS BOT PROCESS MODE",
        font=("Segoe UI", 13, "bold")
    ).pack(pady=12)

    tk.Radiobutton(
        root,
        text="Single Patient Process",
        variable=mode,
        value="single",
        font=("Segoe UI",10)
    ).pack(anchor="w", padx=30)

    tk.Radiobutton(
        root,
        text="Multiple Patient Process",
        variable=mode,
        value="multiple",
        font=("Segoe UI",10)
    ).pack(anchor="w", padx=30)

    tk.Checkbutton(
        root,
        text="Confirm patient before processing",
        variable=confirm
    ).pack(anchor="w", padx=30, pady=(10,5))

    def start():

        global PROCESS_MODE
        global CONFIRM_PATIENT

        PROCESS_MODE = mode.get()
        CONFIRM_PATIENT = confirm.get()

        print("PROCESS_MODE =", PROCESS_MODE)
        print("CONFIRM_PATIENT =", CONFIRM_PATIENT)

        root.destroy()

    tk.Button(
        root,
        text="START",
        width=18,
        command=start
    ).pack(pady=18)

    root.mainloop()



def process():
    batch_patient_tracker.clear()
    files = sorted(os.listdir(SCAN_FOLDER))
    ocr_cache = {}

    print("\n[+] BATCH PASS: Splitting scans by CSF patient boundary...\n")

    groups = build_patient_groups(files, ocr_cache)

    if not groups:
        print("[!] No PDF files found for processing.")
        finalize_all_pdfs()
        return

    print("[+] Patient groups found:", len(groups))

    for index, group in enumerate(groups, start=1):
        process_patient_group(group, index)

    finalize_all_pdfs()


def process_review_queue_item(review_id):
    """Resume exactly one corrected patient without scanning the entire batch."""
    review_item = review_queue.get(review_id)
    if review_item is None:
        raise LookupError(f"Review item {review_id} does not exist")
    if review_item.status != "RESUMING":
        raise ValueError("Review item is not reserved for resume processing")

    group = []
    try:
        for document in review_item.documents:
            path = os.path.abspath(document)
            if not os.path.isfile(path):
                raise FileNotFoundError(path)
            text = ocr_pdf(path)
            doc_type = classify_pdf_for_processing(path, text)
            group.append(
                {
                    "file": os.path.basename(path),
                    "path": path,
                    "text": text,
                    "initial_doc_type": doc_type,
                }
            )
        processed = process_patient_group(group, review_id, review_item=review_item)
        if not processed:
            raise RuntimeError(
                "Document classification is still uncertain. "
                "Please review the listed source document(s), resolve again, then resume."
            )
        finalize_all_pdfs()
        review_queue.complete_resume(review_id)
        print("[RESUME] Patient processing completed:", review_id)
    except Exception as exc:
        review_queue.fail_resume(review_id, f"Resume processing failed: {exc}")
        raise
    
if __name__ == "__main__":
    resume_review_id = os.environ.get("CLAIMS_REVIEW_ID", "").strip()

    if resume_review_id:
        process_review_queue_item(int(resume_review_id))
    else:
        choose_processing_mode()
        process()

    if os.environ.get("CLAIMS_GUI_MODE") != "1":
        input("\nDone. Press ENTER to exit...")
