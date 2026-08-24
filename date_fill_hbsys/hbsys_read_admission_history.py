from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps
from pywinauto import Desktop

from hbsys_rules import AdmissionRow, recommend_discharge_date


LOG_DIR = Path("logs")
DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
TIME_RE = re.compile(r"\b\d{1,2}[.:]\d{2}\s*(?:AM|PM)?\b", re.IGNORECASE)
TYPE_WORDS = {"ADMIT", "OPD", "ER", "EMERGENCY"}


@dataclass(frozen=True)
class OcrItem:
    text: str
    confidence: float
    x: float
    y: float


@dataclass(frozen=True)
class ParsedAdmissionRow:
    row: AdmissionRow
    y: float


def find_admission_history_window():
    desktop = Desktop(backend="win32")
    for window in desktop.windows():
        if window.window_text().strip() == "Admission History":
            return window
    return None


def screenshot_admission_history() -> Path:
    window = find_admission_history_window()
    if window is None:
        raise RuntimeError("Admission History popup not found. Open it first.")

    try:
        window.set_focus()
    except Exception:
        pass

    image: Image.Image = window.capture_as_image()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"admission_history_{datetime.now():%Y%m%d_%H%M%S}.png"
    image.save(path)
    return path


def normalize_text(value: str) -> str:
    value = value.strip().upper()
    value = value.replace("O.", "0.")
    value = value.replace("C0", "00")
    value = value.replace("CO", "00")
    value = value.replace(".", ":")
    value = re.sub(r"\s+", " ", value)
    return value


def normalize_date_text(value: str) -> str:
    match = DATE_RE.search(value)
    if not match:
        return value.strip()
    month, day, year = match.group(0).split("/")
    return f"{int(month):02d}/{int(day):02d}/{year}"


def read_ocr_items(image_path: Path) -> list[OcrItem]:
    try:
        return read_ocr_items_tesseract(image_path)
    except Exception as exc:  # noqa: BLE001 - Tesseract can be missing on new PCs.
        print(f"[OCR] Tesseract unavailable: {exc}")
        return []


def read_ocr_item_variants(image_path: Path) -> list[list[OcrItem]]:
    """Return every OCR interpretation of a captured HBSys window.

    Admission History generally benefits from choosing one highest-scoring OCR
    pass.  PHIC Beneficiaries is different: one pass can read the dates while
    another reads the patient name.  Exposing all passes lets that workflow
    make a safe, row-level decision without changing the existing OCR engine.
    """
    try:
        return read_ocr_item_variants_tesseract(image_path)
    except Exception as exc:  # noqa: BLE001 - Tesseract can be missing.
        print(f"[OCR] Tesseract unavailable: {exc}")
        return []


def read_ocr_items_tesseract(image_path: Path) -> list[OcrItem]:
    item_variants = read_ocr_item_variants_tesseract(image_path)
    best_items: list[OcrItem] = []
    best_score = -1
    for items in item_variants:
        score = score_ocr_items_for_admission_history(items)
        if score > best_score:
            best_score = score
            best_items = items
    return best_items


def read_ocr_item_variants_tesseract(image_path: Path) -> list[list[OcrItem]]:
    import pytesseract

    image = Image.open(image_path)
    item_variants: list[list[OcrItem]] = []
    for variant, scale, config in build_ocr_variants(image):
        data = pytesseract.image_to_data(
            variant,
            output_type=pytesseract.Output.DICT,
            config=config,
        )
        item_variants.append(ocr_data_to_items(data, scale=scale))
    return item_variants


def build_ocr_variants(image: Image.Image):
    gray = ImageOps.grayscale(image)
    contrast = ImageEnhance.Contrast(gray).enhance(2.5)
    sharp = ImageEnhance.Sharpness(contrast).enhance(2.0)

    variants = [
        (image, 1.0, "--psm 6"),
        (gray, 1.0, "--psm 6"),
        (sharp, 1.0, "--psm 6"),
    ]

    for scale in (2.0, 3.0):
        resized = sharp.resize(
            (int(sharp.width * scale), int(sharp.height * scale)),
            Image.Resampling.LANCZOS,
        )
        variants.append((resized, scale, "--psm 6"))
        variants.append((resized, scale, "--psm 11"))

    return variants


def score_ocr_items_for_admission_history(items: list[OcrItem]) -> int:
    date_count = sum(1 for item in items if DATE_RE.search(item.text))
    type_count = sum(1 for item in items if item.text in TYPE_WORDS)
    return date_count * 10 + type_count


def ocr_data_to_items(data, *, scale: float) -> list[OcrItem]:
    items: list[OcrItem] = []
    count = len(data.get("text", []))
    for index in range(count):
        text = str(data["text"][index]).strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
        except Exception:
            confidence = 0.0
        if confidence < 0:
            confidence = 0.0
        left = float(data["left"][index]) / scale
        top = float(data["top"][index]) / scale
        width = float(data["width"][index]) / scale
        height = float(data["height"][index]) / scale
        items.append(
            OcrItem(
                text=normalize_text(text),
                confidence=confidence / 100.0,
                x=left + width / 2,
                y=top + height / 2,
            )
        )
    return items


def group_items_by_row(items: list[OcrItem]) -> list[list[OcrItem]]:
    row_items = [
        item
        for item in items
        if item.y > 75
        and (
            DATE_RE.search(item.text)
            or TIME_RE.search(item.text)
            or item.text in TYPE_WORDS
        )
    ]
    row_items.sort(key=lambda item: item.y)

    rows: list[list[OcrItem]] = []
    for item in row_items:
        for row in rows:
            if abs(row[0].y - item.y) <= 12:
                row.append(item)
                break
        else:
            rows.append([item])

    for row in rows:
        row.sort(key=lambda item: item.x)
    return rows


def parse_rows(items: list[OcrItem]) -> list[AdmissionRow]:
    return [parsed.row for parsed in parse_rows_with_positions(items)]


def parse_rows_with_positions(items: list[OcrItem]) -> list[ParsedAdmissionRow]:
    parsed: list[ParsedAdmissionRow] = []

    for ocr_row in group_items_by_row(items):
        dates = [
            normalize_date_text(match.group(0))
            for item in ocr_row
            for match in DATE_RE.finditer(item.text)
        ]
        times = [match.group(0) for item in ocr_row for match in TIME_RE.finditer(item.text)]
        encounter = next((item.text for item in ocr_row if item.text in TYPE_WORDS), "")

        if len(dates) < 2:
            continue

        parsed.append(
            ParsedAdmissionRow(
                row=AdmissionRow(
                    admission_date=dates[0],
                    admission_time=times[0] if len(times) > 0 else "",
                    discharge_date=dates[1],
                    discharge_time=times[1] if len(times) > 1 else "",
                    encounter_type=encounter,
                ),
                y=sum(item.y for item in ocr_row) / len(ocr_row),
            )
        )

    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read Admission History rows from screenshot/OCR."
    )
    parser.add_argument(
        "--image",
        type=Path,
        help="Use an existing screenshot instead of capturing the live popup.",
    )
    args = parser.parse_args()

    image_path = args.image or screenshot_admission_history()
    items = read_ocr_items(image_path)
    rows = parse_rows(items)
    recommendation = recommend_discharge_date(rows)

    print(f"Image: {image_path.resolve()}")
    print(f"Rows found: {len(rows)}")
    for index, row in enumerate(rows, start=1):
        print(
            f"{index}. admission={row.admission_date} {row.admission_time} | "
            f"discharge={row.discharge_date} {row.discharge_time} | "
            f"type={row.encounter_type}"
        )

    print(f"Recommendation: {recommendation.status}")
    print(f"Date to fill: {recommendation.date_to_fill or ''}")
    print(f"Reason: {recommendation.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
