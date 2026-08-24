import csv
import datetime as dt
import json
import os
import re
from pathlib import Path
from PIL import Image

from core.claims_requirement_rules import BASE_REQUIREMENTS, evaluate_requirements

try:
    import cv2
    import numpy as np
except Exception:
    cv2 = None
    np = None

try:
    import fitz
except Exception:
    fitz = None

try:
    import pytesseract
    from pdf2image import convert_from_path
except Exception:
    pytesseract = None
    convert_from_path = None


BASE_DIR = Path(r"C:\claims_bot")
OUTPUT_DIR = BASE_DIR / "output"
REPORT_CSV = BASE_DIR / "claims_checker_report.csv"
REPORT_LOG = BASE_DIR / "claims_checker_report.log"
CLAIMS_RESULTS_DIR = BASE_DIR / "claims_checker_results"
READY_DIR = CLAIMS_RESULTS_DIR / "READY"
READY_REVIEW_DIR = CLAIMS_RESULTS_DIR / "READY_WITH_REVIEW"
INCOMPLETE_DIR = CLAIMS_RESULTS_DIR / "INCOMPLETE"
SIGNATURE_CHECK_CONFIG = BASE_DIR / "signature_check_config.json"
POPPLER_PATH = r"C:\poppler\Library\bin"
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

ALWAYS_REQUIRED = list(BASE_REQUIREMENTS)
BIRTH_CERT_KEYWORDS = ["birth certificate", "certificate of live birth"]

DEFAULT_SIGNATURE_CHECKS = [
    {"key": "CSF_MEMBER", "label": "CSF - Signature over printed name of Member", "doc": "CSF", "page": "1", "x": "", "y": "", "w": "", "h": "", "enabled": False, "group": "CSF_MEMBER_OR_REP", "group_label": "CSF - Member or representative signature", "require_one": True},
    {"key": "CSF_REPRESENTATIVE", "label": "CSF - Signature over printed name of Member's Representative", "doc": "CSF", "page": "1", "x": "", "y": "", "w": "", "h": "", "enabled": False, "group": "CSF_MEMBER_OR_REP", "group_label": "CSF - Member or representative signature", "require_one": True},
    {"key": "CSF_PART_III_MEMBER_REP", "label": "CSF Part III - Member/representative signature", "doc": "CSF", "page": "1", "x": "", "y": "", "w": "", "h": "", "enabled": False},
    {"key": "CSF_DOCTOR_PART_IV", "label": "CSF Part IV - Doctor signature", "doc": "CSF", "page": "1", "x": "", "y": "", "w": "", "h": "", "enabled": False},
    {"key": "CSF_HCI_PART_V", "label": "CSF Part V - HCI/Chief signature", "doc": "CSF", "page": "1", "x": "", "y": "", "w": "", "h": "", "enabled": False},
    {"key": "CF2_DOCTOR", "label": "CF2 Page 2 - Doctor signature", "doc": "CF2", "page": "2", "x": "", "y": "", "w": "", "h": "", "enabled": False},
    {"key": "CF2_HCI", "label": "CF2 Page 2 - HCI representative signature", "doc": "CF2", "page": "2", "x": "", "y": "", "w": "", "h": "", "enabled": False},
    {"key": "CF2_MEMBER_CONSENT", "label": "CF2 Page 2 - Member/representative consent signature", "doc": "CF2", "page": "2", "x": "", "y": "", "w": "", "h": "", "enabled": False},
    {"key": "SOA1_CERTIFIED_CORRECTED_BY", "label": "SOA1 - Certified corrected by", "doc": "SOA1", "page": "1", "x": "", "y": "", "w": "", "h": "", "enabled": False},
    {"key": "SOA1_MEMBER_REP", "label": "SOA1 - Member/representative signature", "doc": "SOA1", "page": "1", "x": "", "y": "", "w": "", "h": "", "enabled": False},
    {"key": "SOA2_PREPARED_BY", "label": "SOA2 - Prepared by signature", "doc": "SOA2", "page": "any", "x": "", "y": "", "w": "", "h": "", "enabled": False},
    {"key": "SOA2_CONFORME", "label": "SOA2 - Conforme signature", "doc": "SOA2", "page": "any", "x": "", "y": "", "w": "", "h": "", "enabled": False},
    {"key": "MRF_PAGE2_MEMBER", "label": "MRF Page 2 - Member signature", "doc": "MRF", "page": "2", "x": "", "y": "", "w": "", "h": "", "enabled": False},
]


def normalize_name(value):
    return str(value or "").strip().lower()


def file_stem_key(path):
    return normalize_name(Path(path).stem)


def is_patient_folder(path):
    if not path.is_dir():
        return False

    names = {p.name.lower() for p in path.iterdir() if p.is_file()}

    pdf_hits = any(name.endswith(".pdf") for name in names)
    xml_hits = any(name.endswith(".xml") for name in names)

    return pdf_hits or xml_hits


def iter_patient_folders(output_dir):
    if not output_dir.exists():
        return

    for root, dirs, files in os.walk(output_dir):
        path = Path(root)

        if path.name.startswith("_"):
            dirs[:] = []
            continue

        if is_patient_folder(path):
            yield path
            dirs[:] = []


def has_pdf(patient_folder, doc_name):
    target = doc_name.lower()

    for file in patient_folder.iterdir():
        if not file.is_file() or file.suffix.lower() != ".pdf":
            continue

        stem = file_stem_key(file)

        if stem == target:
            return True

        if target == "soa2" and stem.startswith("soa2"):
            return True

    return False


def has_xml(patient_folder, xml_type):
    xml_type = xml_type.lower()

    for file in patient_folder.iterdir():
        if not file.is_file() or file.suffix.lower() != ".xml":
            continue

        name = file.name.lower()

        if xml_type == "cf4" and re.search(r"(^|[_\-\s])cf4(\.xml$|[_\-\s])", name):
            return True

        if xml_type == "cf5" and re.search(r"(^|[_\-\s])cf5(\.xml$|[_\-\s])", name):
            return True

        if xml_type == "esoa" and "esoa" in name:
            return True

    return False


def read_pdf_text(pdf_path):
    text = ""

    if fitz is None or not pdf_path.exists():
        return ocr_pdf_text(pdf_path)

    try:
        doc = fitz.open(str(pdf_path))
        text = "\n".join(page.get_text() for page in doc)
    except Exception:
        text = ""

    if text.strip():
        return text

    return ocr_pdf_text(pdf_path)


def ocr_pdf_text(pdf_path):
    if pytesseract is None or convert_from_path is None or not pdf_path.exists():
        return ""

    if Path(TESSERACT_CMD).exists():
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

    try:
        images = convert_from_path(
            str(pdf_path),
            first_page=1,
            last_page=1,
            poppler_path=POPPLER_PATH if Path(POPPLER_PATH).exists() else None,
        )

        if not images:
            return ""

        return pytesseract.image_to_string(images[0])

    except Exception:
        return ""


def find_pdf(patient_folder, doc_name):
    target = doc_name.lower()

    for file in patient_folder.iterdir():
        if not file.is_file() or file.suffix.lower() != ".pdf":
            continue

        stem = file_stem_key(file)

        if stem == target:
            return file

        if target == "soa2" and stem.startswith("soa2"):
            return file

    return None


def safe_int(value, default=None):
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def load_signature_check_config():
    if SIGNATURE_CHECK_CONFIG.exists():
        try:
            with open(SIGNATURE_CHECK_CONFIG, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                return data
        except Exception:
            pass

    return DEFAULT_SIGNATURE_CHECKS


def has_signature_ink_in_box(pdf_path, page_index, x, y, w, h):
    if fitz is None or not pdf_path.exists():
        return None

    try:
        doc = fitz.open(str(pdf_path))

        if page_index < 0 or page_index >= doc.page_count:
            return None

        page = doc[page_index]
        zoom = 2
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")

        page_height = float(page.rect.height)
        left = int(max(0, x * zoom))
        top = int(max(0, (page_height - y - h) * zoom))
        right = int(min(image.width, (x + w) * zoom))
        bottom = int(min(image.height, (page_height - y) * zoom))

        if right <= left or bottom <= top:
            return None

        crop = image.crop((left, top, right, bottom))
        pixels = list(crop.getdata())

        if not pixels:
            return None

        dark_pixels = sum(1 for px in pixels if px < 115)
        dark_ratio = dark_pixels / float(len(pixels))

        return dark_pixels >= 35 and dark_ratio >= 0.008

    except Exception:
        return None


def pdf_page_to_image(pdf_path, page_index):
    if fitz is None or not pdf_path.exists():
        return None, None
    try:
        doc = fitz.open(str(pdf_path))
        if page_index < 0 or page_index >= doc.page_count:
            return None, None
        page = doc[page_index]
        zoom = 2
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
        return image, page.rect.height
    except Exception:
        return None, None

def has_handwritten_signature_in_area(pdf_path, page_index, x, y, w, h,
                                      dark_threshold=180,
                                      min_dark_pixels=120,
                                      min_ratio=0.010):
    image, page_height = pdf_page_to_image(pdf_path, page_index)
    if image is None:
        return None
    zoom = 2
    left = int(max(0, x * zoom))
    top = int(max(0, (page_height - y - h) * zoom))
    right = int(min(image.width, (x + w) * zoom))
    bottom = int(min(image.height, (page_height - y) * zoom))
    if right <= left or bottom <= top:
        return None
    crop = image.crop((left, top, right, bottom))
    pixels = list(crop.getdata())
    if not pixels:
        return None
    dark_pixels = sum(1 for px in pixels if px < dark_threshold)
    ratio = dark_pixels / float(len(pixels))
    return dark_pixels >= min_dark_pixels and ratio >= min_ratio

def has_signature_dark_ratio(pdf_path, page_index, x, y, w, h, threshold=0.18):
    image, page_height = pdf_page_to_image(pdf_path, page_index)
    if image is None:
        return None
    zoom = 2
    left = int(max(0, x * zoom))
    top = int(max(0, (page_height - y - h) * zoom))
    right = int(min(image.width, (x + w) * zoom))
    bottom = int(min(image.height, (page_height - y) * zoom))
    if right <= left or bottom <= top:
        return None
    crop = image.crop((left, top, right, bottom))
    pixels = list(crop.getdata())
    if not pixels:
        return None
    dark_pixels = sum(1 for px in pixels if px < 180)
    dark_ratio = dark_pixels / float(len(pixels))
    return dark_ratio >= threshold



def has_signature_contours(pdf_path, page_index, x, y, w, h,
                           min_area=20,
                           min_contours=3,
                           min_total_area=250):
    image, page_height = pdf_page_to_image(pdf_path, page_index)
    if image is None or cv2 is None or np is None:
        return None

    zoom = 2
    left = int(max(0, x * zoom))
    top = int(max(0, (page_height - y - h) * zoom))
    right = int(min(image.width, (x + w) * zoom))
    bottom = int(min(image.height, (page_height - y) * zoom))

    if right <= left or bottom <= top:
        return None

    crop = image.crop((left, top, right, bottom))
    gray = np.array(crop)

    _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)

    kernel = np.ones((2, 2), np.uint8)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(
        thresh,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE
    )

    valid_count = 0
    total_area = 0

    for c in contours:
        area = cv2.contourArea(c)
        if area >= min_area:
            valid_count += 1
            total_area += area

    return (valid_count >= min_contours) or (total_area >= min_total_area)


def evaluate_signature_slot(patient_folder, slot):
    doc_name = str(slot.get("doc") or "").strip()

    if not doc_name:
        return None

    pdf_path = find_pdf(patient_folder, doc_name)

    if not pdf_path:
        return None

    x = safe_int(slot.get("x"))
    y = safe_int(slot.get("y"))
    w = safe_int(slot.get("w"))
    h = safe_int(slot.get("h"))

    if x is None or y is None or not w or not h:
        return None

    page_value = str(slot.get("page") or "1").strip().lower()

    try:
        doc = fitz.open(str(pdf_path)) if fitz is not None else None
        page_count = doc.page_count if doc is not None else 0
        if doc is not None:
            doc.close()
    except Exception:
        page_count = 0

    if page_value == "any":
        page_indexes = range(page_count)
    else:
        page_indexes = [max(0, safe_int(page_value, 1) - 1)]

    found = False
    unknown = False

    for page_index in page_indexes:
        slot_key = str(slot.get("key") or "").strip().upper()

        if slot_key == "CSF_DOCTOR_PART_IV":
            result = has_signature_contours(pdf_path, page_index, x, y, w, h, min_area=20, min_contours=3, min_total_area=250)
        elif slot_key == "CSF_HCI_PART_V":
            result = has_signature_contours(pdf_path, page_index, x, y, w, h, min_area=20, min_contours=5, min_total_area=350)
        else:
            result = has_signature_ink_in_box(pdf_path, page_index, x, y, w, h)

        if result is True:
            found = True
            break

        if result is None:
            unknown = True

    try:
        print(f"[SIGCHECK] {patient_folder.name} | {slot.get('key')} | found={found} unknown={unknown}")
    except Exception:
        pass

    if found:
        return "found"

    if unknown:
        return "unknown"

    return "missing"


def check_signature_slots(patient_folder):
    warnings = []
    grouped = {}

    for slot in load_signature_check_config():
        if not isinstance(slot, dict) or not slot.get("enabled"):
            continue

        group = str(slot.get("group") or "").strip()

        if group and slot.get("require_one"):
            grouped.setdefault(group, []).append(slot)
            continue

        label = str(slot.get("label") or slot.get("key") or "Signature").strip()
        result = evaluate_signature_slot(patient_folder, slot)

        if result in (None, "found"):
            continue

        if result == "unknown":
            warnings.append(f"{label} signature check unreadable")
        elif result == "missing":
            warnings.append(f"{label} signature not found")

    for group, slots in grouped.items():
        label = str(slots[0].get("group_label") or group or "Signature group").strip()
        results = [evaluate_signature_slot(patient_folder, slot) for slot in slots]

        if "found" in results:
            continue

        if all(result is None for result in results):
            continue

        if any(result == "unknown" for result in results):
            warnings.append(f"{label} signature check unreadable")
        else:
            warnings.append(f"{label} signature not found")

    return warnings


def get_found_items(patient_folder):
    found = []

    for doc in ["CSF", "COE", "SOA1", "SOA2", "CF2", "CF3", "MRF", "PBC", "MMC", "ANR", "OPR", "DTR"]:
        if has_pdf(patient_folder, doc):
            found.append(doc)

    for xml_type, label in [("cf4", "CF4 XML"), ("cf5", "CF5 XML"), ("esoa", "eSOA XML")]:
        if has_xml(patient_folder, xml_type):
            found.append(label)

    return found


def section_between(text, start_pattern, end_patterns):
    match = re.search(start_pattern, text, flags=re.IGNORECASE)

    if not match:
        return ""

    start = match.end()
    end = len(text)

    for pattern in end_patterns:
        next_match = re.search(pattern, text[start:], flags=re.IGNORECASE)
        if next_match:
            end = min(end, start + next_match.start())

    return text[start:end].strip()


def analyze_coe(patient_folder):
    coe = find_pdf(patient_folder, "COE")

    result = {
        "eligibility": "UNKNOWN",
        "reason": "",
        "attached_documents": "",
        "requires_mrf_pbc": False,
        "note": "",
    }

    if not coe:
        result["note"] = "COE missing; eligibility not checked"
        return result

    text = read_pdf_text(coe)

    if not text.strip():
        result["note"] = "COE text unreadable; eligibility not checked"
        return result

    compact = re.sub(r"\s+", " ", text).strip()

    eligibility_match = re.search(
        r"ELIGIBLE\s+TO\s+AVAIL\s+PHILHEALTH\s+BENEFITS\??\s*[:=]?\s*(YES|NO)\b",
        compact,
        flags=re.IGNORECASE,
    )

    if eligibility_match:
        result["eligibility"] = eligibility_match.group(1).upper()
    elif re.search(r"\bNOT\s+ELIGIBLE\b|\bNOT\s+QUALIFIED\b", compact, flags=re.IGNORECASE):
        result["eligibility"] = "NO"

    result["reason"] = section_between(
        text,
        r"Reason/s?\s*:?",
        [r"Attached\s+Documents?\s*:?", r"Important\s+Reminders?\s*:?", r"Note\s*:?", r"$"],
    )

    result["attached_documents"] = section_between(
        text,
        r"Attached\s+Documents?\s*:?",
        [r"Important\s+Reminders?\s*:?", r"Note\s*:?", r"$"],
    )

    result["requires_mrf_pbc"] = result["eligibility"] == "NO"

    return result


def compact_note(value, max_len=180):
    value = re.sub(r"\s+", " ", str(value or "")).strip()

    if len(value) <= max_len:
        return value

    return value[: max_len - 3].rstrip() + "..."



def csf_contains_nsd01(patient_folder):
    csf = find_pdf(patient_folder, "CSF")
    if not csf:
        return False

    text = read_pdf_text(csf)
    if not text:
        return False

    return "NSD01" in text.upper()

def check_patient_folder(patient_folder):
    warnings = []
    signature_warnings = []
    notes = []
    found = get_found_items(patient_folder)
    coe = analyze_coe(patient_folder)

    rule_result = evaluate_requirements(
        found,
        nsd01_detected=csf_contains_nsd01(patient_folder),
        coe_eligibility_no=coe["eligibility"] == "NO",
    )
    missing = list(rule_result.missing)
    notes.append(f"Claim classification: {rule_result.classification}")
    notes.extend(rule_result.notes)

    if coe["note"]:
        notes.append(coe["note"])

    #signature_warnings = check_signature_slots(patient_folder)

    if missing:
        status = "INCOMPLETE"
    elif warnings or signature_warnings:
        status = "READY WITH REVIEW"
    else:
        status = "READY"

    return {
        "Patient Folder": patient_folder.name,
        "Status": status,
        "Found": "; ".join(found),
        "Missing": "; ".join(missing),
        "Warnings": "; ".join(warnings),
        "Signature Warnings": "; ".join(signature_warnings),
        "Signature Debug": "; ".join(signature_warnings),
        "Eligibility": coe["eligibility"],
        "Reason": compact_note(coe["reason"]),
        "Notes": "; ".join(notes),
    }


def write_reports(rows):
    REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)

    csv_path = REPORT_CSV

    try:
        f = open(csv_path, "w", newline="", encoding="utf-8-sig")
    except PermissionError:
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = BASE_DIR / f"claims_checker_report_{stamp}.csv"
        f = open(csv_path, "w", newline="", encoding="utf-8-sig")

    with f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "Patient Folder",
                "Status",
                "Found",
                "Missing",
                "Warnings",
                "Signature Warnings",
                "Signature Debug",
                "Eligibility",
                "Reason",
                "Notes",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    counts = {}
    for row in rows:
        counts[row["Status"]] = counts.get(row["Status"], 0) + 1

    missing_counts = {}
    for row in rows:
        for item in str(row.get("Missing","")).split(";"):
            item = item.strip()
            if item:
                missing_counts[item] = missing_counts.get(item, 0) + 1

    with open(REPORT_LOG, "w", encoding="utf-8") as f:
        f.write("=== CLAIMS CHECKER SUMMARY ===\n\n")
        f.write(f"Total Patients: {len(rows)}\n")
        f.write(f"Ready: {counts.get('READY',0)}\n")
        f.write(f"Needs Review: {counts.get('READY WITH REVIEW',0)}\n")
        f.write(f"Incomplete: {counts.get('INCOMPLETE',0)}\n\n")

        f.write("Missing Documents:\n")
        for k,v in sorted(missing_counts.items()):
            f.write(f"{k} = {v}\n")

        f.write("\n=============================\n\n")
        f.write("Claims Checker Report\n")
        f.write("Generated: " + dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "\n")
        f.write("Output folder: " + str(OUTPUT_DIR) + "\n")
        f.write("Total patient folders: " + str(len(rows)) + "\n")

        for status in ["READY", "READY WITH REVIEW", "INCOMPLETE"]:
            f.write(f"{status}: {counts.get(status, 0)}\n")

        f.write("\nRules:\n")
        f.write("Always required: " + ", ".join(ALWAYS_REQUIRED) + "\n")
        f.write("MRF present -> PBC or MMC required\n")
        f.write("COE eligibility NO -> MRF + PBC or MMC required\n")
        f.write("ANR present -> OPR + CF3 required\n")
        f.write("NSD01 in CSF -> OPR + CF3 required\n")
        f.write("CF2 present -> claim classified as Newborn\n")
        f.write("Conditional requirements are cumulative\n")
        f.write("Enabled signature boxes -> warning only\n")

    return csv_path, REPORT_LOG



def organize_claim_folders(rows, source_dir=OUTPUT_DIR):
    import shutil

    CLAIMS_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    READY_DIR.mkdir(exist_ok=True)
    READY_REVIEW_DIR.mkdir(exist_ok=True)
    INCOMPLETE_DIR.mkdir(exist_ok=True)

    status_map = {r["Patient Folder"]: r["Status"] for r in rows}

    for folder in list(iter_patient_folders(source_dir)):
        status = status_map.get(folder.name)
        if not status:
            continue

        if status == "READY":
            dest_root = READY_DIR
        elif status == "READY WITH REVIEW":
            dest_root = READY_REVIEW_DIR
        else:
            dest_root = INCOMPLETE_DIR

        dest = dest_root / folder.name

        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)

        shutil.move(str(folder), str(dest))

def main():
    recheck_mode = os.environ.get("CLAIMS_RECHECK_INCOMPLETE", "0") == "1"

    source_dir = INCOMPLETE_DIR if recheck_mode else OUTPUT_DIR

    rows = [check_patient_folder(folder) for folder in iter_patient_folders(source_dir)]
    rows.sort(key=lambda row: row["Patient Folder"].lower())
    organize_claim_folders(rows, source_dir)
    csv_path, log_path = write_reports(rows)

    print("Claims checker complete")
    print("CSV:", csv_path)
    print("Log:", log_path)
    print("Patient folders checked:", len(rows))

    try:
        os.startfile(str(csv_path))
    except Exception:
        pass

    try:
        os.startfile(str(log_path))
    except Exception:
        pass


if __name__ == "__main__":
    main()
