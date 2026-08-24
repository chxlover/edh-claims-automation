from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

import pytesseract
from PIL import Image, ImageEnhance, ImageOps

from hbsys_read_admission_history import OcrItem


NAME_COLUMN = (68, 421)
HOSPITAL_COLUMN = (422, 605)
ADMISSION_COLUMN = (1034, 1144)
DISCHARGE_COLUMN = (1145, 1273)


@dataclass(frozen=True)
class Cf4GridRow:
    y: float
    patient_name: str
    hospital_no: str
    admission_date: str
    discharge_date: str


def normalize_name(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", (value or "").upper()).strip()


def compact_name(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def normalize_date(value: str) -> str:
    match = re.search(r"(\d{1,2})\D+(\d{1,2})\D+(\d{4})", value or "")
    if not match:
        return ""
    month, day, year = match.groups()
    return f"{int(month):02d}/{int(day):02d}/{year}"


def detect_highlighted_row_center(image: Image.Image) -> float | None:
    rgb = image.convert("RGB")
    width, height = rgb.size
    blue_rows: list[int] = []
    for y in range(180, min(height, 1025)):
        blue_count = 0
        for x in range(68, min(width, 1273), 8):
            red, green, blue = rgb.getpixel((x, y))
            if blue >= 145 and 60 <= green <= 190 and red <= 90:
                blue_count += 1
        if blue_count >= 20:
            blue_rows.append(y)

    if not blue_rows:
        return None

    groups: list[list[int]] = []
    for y in blue_rows:
        if groups and y - groups[-1][-1] <= 1:
            groups[-1].append(y)
        else:
            groups.append([y])

    best_group = max(groups, key=len)
    if len(best_group) < 3:
        return None
    return (best_group[0] + best_group[-1]) / 2


def _name_candidate_rows(
    item_variants: list[list[OcrItem]],
    expected_patient_name: str,
) -> list[float]:
    expected_tokens = [
        token for token in normalize_name(expected_patient_name).split() if len(token) >= 3
    ]
    if not expected_tokens:
        return []

    candidates: list[float] = []
    for items in item_variants:
        name_items = [
            item
            for item in items
            if 180 <= item.y <= 1020
            and NAME_COLUMN[0] <= item.x <= NAME_COLUMN[1]
            and item.confidence >= 0.10
            and item.text
        ]
        name_items.sort(key=lambda item: item.y)

        rows: list[list[OcrItem]] = []
        for item in name_items:
            for row in rows:
                if abs(row[0].y - item.y) <= 9:
                    row.append(item)
                    break
            else:
                rows.append([item])

        for row in rows:
            row_text = " ".join(item.text for item in sorted(row, key=lambda item: item.x))
            compact_row = compact_name(row_text)
            name_score = sum(
                1 for token in expected_tokens if compact_name(token) in compact_row
            )
            required_score = 1 if len(expected_tokens) == 1 else 2
            if name_score >= required_score:
                candidates.append(sum(item.y for item in row) / len(row))

    return candidates


def _deduplicate_row_centers(values: list[float]) -> list[float]:
    clusters: list[list[float]] = []
    for value in sorted(values):
        for cluster in clusters:
            if abs((sum(cluster) / len(cluster)) - value) <= 9:
                cluster.append(value)
                break
        else:
            clusters.append([value])
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _read_cell_candidates(
    image: Image.Image,
    x_bounds: tuple[int, int],
    row_y: float,
    *,
    whitelist: str = "",
) -> list[str]:
    left, right = x_bounds
    center_y = int(round(row_y))
    crop = image.crop((left, max(0, center_y - 10), right, center_y + 10))
    gray = ImageOps.grayscale(crop)
    enhanced = ImageEnhance.Sharpness(
        ImageEnhance.Contrast(gray).enhance(2.0)
    ).enhance(2.0)

    candidates: list[str] = []
    for scale in (2, 3, 4):
        resized = enhanced.resize(
            (enhanced.width * scale, enhanced.height * scale),
            Image.Resampling.LANCZOS,
        )
        config = "--psm 7"
        if whitelist:
            config += f" -c tessedit_char_whitelist={whitelist}"
        value = pytesseract.image_to_string(resized, config=config).strip()
        if value and value not in candidates:
            candidates.append(value)
    return candidates


def _best_name(candidates: list[str], expected: str) -> str:
    expected_compact = compact_name(expected)
    expected_tokens = [compact_name(token) for token in normalize_name(expected).split()]

    def score(candidate: str) -> tuple[int, int]:
        compact = compact_name(candidate)
        exact = int(bool(expected_compact) and compact == expected_compact)
        token_score = sum(1 for token in expected_tokens if token and token in compact)
        return exact, token_score

    return max(candidates, key=score, default="")


def _best_hospital_no(candidates: list[str], expected: str) -> str:
    expected_digits = re.sub(r"\D", "", expected)
    values = [re.sub(r"\D", "", candidate) for candidate in candidates]
    if expected_digits in values:
        return expected_digits
    return max(values, key=len, default="")


def _best_date(candidates: list[str], expected: str) -> str:
    expected_date = normalize_date(expected)
    values = [normalize_date(candidate) for candidate in candidates]
    if expected_date in values:
        return expected_date
    return next((value for value in values if value), "")


def read_cf4_grid_row(
    image: Image.Image,
    row_y: float,
    *,
    expected_patient_name: str,
    expected_hospital_no: str,
    expected_admission: str,
    expected_discharge: str,
) -> Cf4GridRow:
    name_candidates = _read_cell_candidates(image, NAME_COLUMN, row_y)
    hospital_candidates = _read_cell_candidates(
        image,
        HOSPITAL_COLUMN,
        row_y,
        whitelist="0123456789",
    )
    admission_candidates = _read_cell_candidates(
        image,
        ADMISSION_COLUMN,
        row_y,
        whitelist="0123456789-/",
    )
    discharge_candidates = _read_cell_candidates(
        image,
        DISCHARGE_COLUMN,
        row_y,
        whitelist="0123456789-/",
    )
    return Cf4GridRow(
        y=row_y,
        patient_name=_best_name(name_candidates, expected_patient_name),
        hospital_no=_best_hospital_no(hospital_candidates, expected_hospital_no),
        admission_date=_best_date(admission_candidates, expected_admission),
        discharge_date=_best_date(discharge_candidates, expected_discharge),
    )


def row_matches_folder(
    row: Cf4GridRow,
    *,
    expected_patient_name: str,
    expected_hospital_no: str,
    expected_admission: str,
    expected_discharge: str,
) -> bool:
    expected_tokens = [
        token for token in normalize_name(expected_patient_name).split() if len(token) >= 3
    ]
    row_compact = compact_name(row.patient_name)
    name_score = sum(1 for token in expected_tokens if compact_name(token) in row_compact)
    required_name_score = 1 if len(expected_tokens) == 1 else 2
    name_ok = name_score >= required_name_score

    expected_hospital = re.sub(r"\D", "", expected_hospital_no)
    hospital_ok = bool(expected_hospital) and row.hospital_no == expected_hospital
    admission_ok = row.admission_date == normalize_date(expected_admission)
    discharge_ok = row.discharge_date == normalize_date(expected_discharge)

    # Folder confinement dates are mandatory. Patient identity must also be
    # supported by either the exact hospital number or a strong name match.
    return admission_ok and discharge_ok and (hospital_ok or name_ok)


def find_matching_cf4_grid_row(
    image_path: Path,
    item_variants: list[list[OcrItem]],
    *,
    expected_patient_name: str,
    expected_hospital_no: str,
    expected_admission: str,
    expected_discharge: str,
) -> Cf4GridRow | None:
    image = Image.open(image_path).convert("RGB")
    highlighted_y = detect_highlighted_row_center(image)
    candidate_values = _name_candidate_rows(item_variants, expected_patient_name)
    if highlighted_y is not None:
        candidate_values.insert(0, highlighted_y)

    matches: list[Cf4GridRow] = []
    for row_y in _deduplicate_row_centers(candidate_values):
        row = read_cf4_grid_row(
            image,
            row_y,
            expected_patient_name=expected_patient_name,
            expected_hospital_no=expected_hospital_no,
            expected_admission=expected_admission,
            expected_discharge=expected_discharge,
        )
        if row_matches_folder(
            row,
            expected_patient_name=expected_patient_name,
            expected_hospital_no=expected_hospital_no,
            expected_admission=expected_admission,
            expected_discharge=expected_discharge,
        ):
            matches.append(row)

    if len(matches) != 1:
        return None
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Read and validate a CF4 result-grid row.")
    parser.add_argument("image", type=Path)
    parser.add_argument("patient_name")
    parser.add_argument("hospital_no")
    parser.add_argument("admission")
    parser.add_argument("discharge")
    args = parser.parse_args()

    from hbsys_read_admission_history import read_ocr_item_variants

    result = find_matching_cf4_grid_row(
        args.image,
        read_ocr_item_variants(args.image),
        expected_patient_name=args.patient_name,
        expected_hospital_no=args.hospital_no,
        expected_admission=args.admission,
        expected_discharge=args.discharge,
    )
    print(result or "NO MATCH")
    return 0 if result else 1


if __name__ == "__main__":
    raise SystemExit(main())
