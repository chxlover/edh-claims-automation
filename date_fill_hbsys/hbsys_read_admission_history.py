from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps
from pywinauto import Desktop

from hbsys_rules import AdmissionRow, recommend_discharge_date


if __package__ in (None, ""):
    # Legacy production tooling runs this as a top-level script
    # (``cd date_fill_hbsys`` then ``python hbsys_fill_dates.py``), so keep
    # its old relative "logs" default there.
    LOG_DIR = Path("logs")
else:
    # The agent flow imports it as the ``date_fill_hbsys`` package, so
    # captures land under the project logs instead of wherever cwd is.
    LOG_DIR = Path(__file__).resolve().parents[1] / "logs"

DATE_RE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
TIME_RE = re.compile(r"\b\d{1,2}[.:]\d{2}\s*(?:AM|PM)?\b", re.I)
TYPE_WORDS = {"ADMIT", "OPD", "ER", "EMERGENCY"}

# A grid date is 10 glyphs in a fixed-width column, so a per-cell re-read can
# safely crop around the token. Verified live on 2026-09-26: a whole-grid pass
# read '09/2212026' (the second '/' smeared into a '1') and dropped the row,
# while every one of 12 whitelist re-reads of that same cell returned
# '09/22/2026'.
DATE_CELL_HALF_WIDTH = 52
DATE_CELL_HALF_HEIGHT = 12
DATE_CELL_CONFIG = "--psm 7 -c tessedit_char_whitelist=0123456789/"
# Several whitelist configurations per cell: the value is only accepted when
# two of them agree, because a single pass can invent a plausible-but-wrong
# date (verified live: '06/31/2026' where the cell really holds 08/31/2026).
DATE_CELL_CONFIGS = (
    DATE_CELL_CONFIG,
    "--psm 8 -c tessedit_char_whitelist=0123456789/",
    "--psm 13 -c tessedit_char_whitelist=0123456789/",
    "--psm 7 -c tessedit_char_whitelist=0123456789/-",
)


def date_token_needs_reread(text: str) -> bool:
    """Should this grid date token be re-read on its own?

    True for a smeared token the strict DATE_RE rejected, and ALSO for a token
    that parses as MM/DD/YYYY but is not a real calendar date ('06/31/2026') -
    that is always a misread, and re-reading is the only way to get the true
    date. Never true for a real date: those are already good.
    """
    value = " ".join(str(text or "").strip().split())
    if not value or "/" not in value:
        return False
    if not repair_date_text(value):
        return True
    return False


def looks_like_smeared_date(text: str) -> bool:
    """True for a date-ish token the strict DATE_RE rejected.

    Catches the shapes seen in the Admission History grid: '09/2212026',
    'O9/22/2026', '9/22/26'. Deliberately loose — it only decides that a cell
    is worth re-reading, never which date it is.
    """
    text = str(text or "").strip()
    if not text or DATE_RE.search(text):
        return False
    if "/" not in text:
        return False
    return sum(char.isdigit() for char in text) >= 6


def read_date_cell(image: Image.Image, box: tuple) -> str:
    """Re-read one grid date cell with a digits-and-slash whitelist.

    `box` is the token's (left, top, right, bottom) inside `image`. Returns the
    canonical MM/DD/YYYY text, or '' when the cell cannot be read — the caller
    then keeps the original token and lets the row be skipped, so a smear can
    never turn into a guessed confinement.

    A whole-grid pass can also return a date that LOOKS valid but is a bad read
    ('06/31/2026' - June has 30 days, really 08/31/2026). So the cell is read
    several ways and the value is only accepted when the passes AGREE on a real
    date: no single lucky configuration is trusted on its own.
    """
    try:
        import pytesseract
    except Exception:
        return ""

    left, top, right, bottom = (int(value) for value in box)
    center_x = (left + right) // 2
    center_y = (top + bottom) // 2
    crop_box = (
        center_x - DATE_CELL_HALF_WIDTH,
        center_y - DATE_CELL_HALF_HEIGHT,
        center_x + DATE_CELL_HALF_WIDTH,
        center_y + DATE_CELL_HALF_HEIGHT,
    )
    width, height = image.size
    if (
        crop_box[0] < 0
        or crop_box[1] < 0
        or crop_box[2] > width
        or crop_box[3] > height
        or crop_box[2] <= crop_box[0]
        or crop_box[3] <= crop_box[1]
    ):
        return ""
    try:
        cell = ImageOps.grayscale(image.crop(crop_box))
        sharp = ImageEnhance.Sharpness(
            ImageEnhance.Contrast(cell).enhance(2.5)
        ).enhance(2.0)
    except Exception:
        return ""
    reads: dict = {}
    for source, tag in ((cell, "g"), (sharp, "s")):
        for scale in (2, 3, 4):
            try:
                scaled = source.resize(
                    (source.width * scale, source.height * scale), Image.LANCZOS
                )
            except Exception:
                continue
            for config in DATE_CELL_CONFIGS:
                try:
                    raw = pytesseract.image_to_string(scaled, config=config)
                except Exception:
                    continue
                value = repair_date_text(raw)
                if value:
                    reads.setdefault(value, set()).add(f"{tag}{scale}x")
    if not reads:
        return ""
    best = max(reads, key=lambda value: (len(reads[value]), value))
    # One agreeing configuration is not evidence: require at least two
    # independent reads (different sharpening and/or scale) to say 's').
    return best if len(reads[best]) >= 2 else ""


def repair_date_items(image: Image.Image, items: list["OcrItem"]) -> list["OcrItem"]:
    """Replace smeared date tokens with a whitelist re-read of their own cell.

    Only tokens that already look like a date are re-read, and only a re-read
    that yields a real MM/DD/YYYY replaces anything — otherwise the token is
    left exactly as the grid pass read it.
    """
    try:
        from dataclasses import replace
    except Exception:  # pragma: no cover - dataclasses is stdlib
        return items
    repaired: list = []
    for item in items:
        if date_token_needs_reread(item.text):
            left = int(item.x - item.width / 2)
            top = int(item.y - item.height / 2)
            cell = read_date_cell(
                image, (left, top, left + int(item.width), top + int(item.height))
            )
            if cell:
                repaired.append(replace(item, text=cell))
                continue
        repaired.append(item)
    return repaired


@dataclass(frozen=True)
class ConfinementRow:
    """One Admission History row: raw grid dates plus its row click point."""

    admission_grid: str
    discharge_grid: str
    point: tuple
    encounter_type: str = ""


def admission_grid_key(value: str) -> str:
    """Canonical key for an MM/DD/YYYY grid date ('' when unparseable)."""
    try:
        return datetime.strptime(str(value or "").strip(), "%m/%d/%Y").strftime(
            "%Y%m%d"
        )
    except ValueError:
        return ""


def select_confinement_row(
    *,
    open_popup_fn,
    live: bool,
    log_fn,
    wait_fn,
    read_rows_fn,
    double_click_fn,
    rate_dialog_fn,
    expected_admission: str,
    expected_discharge: str,
    fuzzy_rows_fn=None,
    on_selected_row_fn=None,
    context: str,
) -> bool:
    """Pick one confinement row from the Admission History popup.

    Shared recipe used by Date Fill (HbsysOperator.select_admission_history_row)
    and the Final Bill agent (final_bill_actions.select_confinement): the popup
    is opened, the grid is OCR'd through the caller, the folder grid dates
    (MM/DD/YYYY) must match the row exactly, then an OCR-tolerant fuzzy row
    is retried through the caller, and any Rate validation dialog left behind
    is answered by the caller. A mismatch stops for review — nothing else ever
    guesses which confinement to load.
    """
    open_popup_fn("Admit History")

    if not live:
        log_fn(
            "would OCR Admission History and select row matching "
            f"{expected_admission} - {expected_discharge}"
        )
        return True

    try:
        window = open_popup_fn("verify open")
    except TypeError:
        window = None
    if window is None:
        raise RuntimeError("Admission History popup did not open.")

    rows = read_rows_fn(window)
    if rows:
        detected = "; ".join(
            f"{row.admission_grid}-{row.discharge_grid}" for row in rows
        )
        log_fn(f"Admission History rows detected: {detected}")
    else:
        log_fn("Admission History OCR found no date rows.")

    expected_admission_key = admission_grid_key(expected_admission)
    expected_discharge_key = admission_grid_key(expected_discharge)
    picked = next(
        (
            row
            for row in rows
            if admission_grid_key(row.admission_grid)
            and admission_grid_key(row.admission_grid) == expected_admission_key
            and admission_grid_key(row.discharge_grid)
            and admission_grid_key(row.discharge_grid) == expected_discharge_key
        ),
        None,
    )
    if picked is None and fuzzy_rows_fn is not None:
        fuzzy = fuzzy_rows_fn()
        if fuzzy is not None:
            click_x, click_y = int(fuzzy.point[0]), int(fuzzy.point[1])
            log_fn(
                "select OCR-tolerant Admission History row "
                f"{fuzzy.admission_grid}-{fuzzy.discharge_grid} "
                f"for folder {expected_admission}-{expected_discharge} "
                f"at ({click_x}, {click_y})"
            )
            double_click_fn(click_x, click_y)
            wait_fn(0.8)
            if not rate_dialog_fn():
                log_fn(
                    f"{context} OCR-tolerant Admission History selection left "
                    "an unknown Rate validation dialog"
                )
                return False
            if on_selected_row_fn is not None:
                on_selected_row_fn()
            return True
    if picked is None:
        if len(rows) == 1:
            row = rows[0]
            log_fn(
                "Only one Admission History row was detected, but it did not "
                "safely match the folder admission/discharge dates: "
                f"HBSys OCR {row.admission_grid}-{row.discharge_grid}, "
                f"folder {expected_admission}-{expected_discharge}. Stopping."
            )
        else:
            log_fn(
                "matching Admission History row not found for "
                f"{expected_admission}-{expected_discharge}; stopping for review"
            )
        return False

    click_x, click_y = int(picked.point[0]), int(picked.point[1])
    log_fn(
        f"select exact Admission History row "
        f"{expected_admission}-{expected_discharge} "
        f"at ({click_x}, {click_y})"
    )
    double_click_fn(click_x, click_y)
    wait_fn(0.8)
    if not rate_dialog_fn():
        log_fn(
            f"{context} Admission History selection left an unknown Rate "
            "validation dialog"
        )
        return False
    if on_selected_row_fn is not None:
        on_selected_row_fn()
    return True


@dataclass(frozen=True)
class OcrItem:
    text: str
    confidence: float
    x: float
    y: float
    # Pixel size of the token's own box, so a smeared date can be re-read from
    # that cell alone (see repair_date_items). Defaults keep every existing
    # OcrItem(text, confidence, x, y) construction working.
    width: float = 0.0
    height: float = 0.0


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


def screenshot_admission_history(log_dir: Path | None = None) -> Path:
    window = find_admission_history_window()
    if window is None:
        raise RuntimeError("Admission History popup not found. Open it first.")

    try:
        window.set_focus()
    except Exception:
        pass

    image: Image.Image = window.capture_as_image()

    target = log_dir or LOG_DIR
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"admission_history_{datetime.now():%Y%m%d_%H%M%S}.png"
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


def repair_date_text(text: str) -> str:
    """Return a REAL calendar date for a date-ish token, else ''.

    Repairs the smears seen in the Admission History grid ('09/2212026',
    '9/18/2026', '(06/05/2025') and - importantly - REJECTS impossible dates.
    The whole-grid pass read '06/31/2026' (June has 30 days); that is a bad read,
    not a date, so it is never handed to a matcher as if it were real.
    """
    value = " ".join(str(text or "").strip().split())
    if not value:
        return ""
    digits_only = re.sub(r"[^0-9/]", "", value)
    if not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", digits_only):
        return ""
    month, day, year = digits_only.split("/")
    try:
        parsed = datetime.strptime(f"{int(month)}/{int(day)}/{year}", "%m/%d/%Y")
    except ValueError:
        return ""
    return f"{parsed.month:02d}/{parsed.day:02d}/{parsed.year:04d}"


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
    # A smeared date ('09/2212026') is invisible to DATE_RE, so the best pass
    # can look healthy while the row that matters was dropped. Re-read those
    # cells before the caller groups the tokens into rows.
    if best_items:
        try:
            best_items = repair_date_items(Image.open(image_path), best_items)
        except Exception as exc:  # noqa: BLE001 - never fail a run over a repair
            print(f"[OCR] date cell repair skipped: {exc}")
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


def read_focused_admission_row_variants(image_path: Path) -> list[list[OcrItem]]:
    """OCR only the visible grid rows to avoid full-window year misreads.

    Ported from hbsys_read_admission_history_testing.py, the module behind the
    proven Date Fill ABTC/Regular button: a whole-window pass can drop a row
    entirely (live 2026-09-26 the single best pass read only the OPD row), so
    each detected gray data-row band is cropped and OCR'd on its own.
    """
    import pytesseract

    image = Image.open(image_path)
    gray = ImageOps.grayscale(image)
    row_centers = detect_admission_grid_row_centers(gray)
    if not row_centers:
        return []

    variant_specs = (
        (2.0, "gray", "--psm 6"),
        (2.0, "gray", "--psm 7"),
        (3.0, "gray", "--psm 6"),
        (3.0, "contrast", "--psm 7"),
    )
    item_variants: list[list[OcrItem]] = []
    right = min(image.width - 20, 630)
    for scale, style, config in variant_specs:
        items: list[OcrItem] = []
        for center in row_centers:
            top = max(0, int(center) - 13)
            bottom = min(image.height, int(center) + 14)
            crop = gray.crop((14, top, right, bottom))
            if style == "contrast":
                crop = ImageEnhance.Contrast(crop).enhance(2.5)
            crop = crop.resize(
                (int(crop.width * scale), int(crop.height * scale)),
                Image.Resampling.LANCZOS,
            )
            text = normalize_text(
                pytesseract.image_to_string(crop, config=config).strip()
            )
            dates = [normalize_date_text(match.group(0)) for match in DATE_RE.finditer(text)]
            encounter_type = next(
                (
                    value
                    for value in ("ADMIT", "OPDAD", "OPD")
                    if value in text
                ),
                "",
            )
            if len(dates) < 2 or not encounter_type:
                continue
            items.extend(
                (
                    OcrItem(dates[0], 1.0, 76.0, center),
                    OcrItem(dates[1], 1.0, 330.0, center),
                    OcrItem(encounter_type, 1.0, 564.0, center),
                )
            )
        item_variants.append(items)
    return item_variants


def detect_admission_grid_row_centers(gray: Image.Image) -> list[float]:
    """Find gray HBSys data-row bands without assuming the number of rows."""
    right = min(gray.width - 20, 630)
    if right <= 15:
        return []
    centers: list[float] = []
    band: list[int] = []
    for y in range(88, min(gray.height - 10, 350)):
        samples = [gray.getpixel((x, y)) for x in range(15, right, 3)]
        dark_ratio = sum(value < 230 for value in samples) / len(samples)
        if dark_ratio > 0.55:
            band.append(y)
        elif band:
            if len(band) >= 8:
                centers.append((band[0] + band[-1]) / 2)
            band = []
    if len(band) >= 8:
        centers.append((band[0] + band[-1]) / 2)
    return centers


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
                width=width,
                height=height,
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
