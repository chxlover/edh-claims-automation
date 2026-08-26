from __future__ import annotations

import argparse
import ctypes
import csv
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pyautogui
from PIL import Image
from pywinauto import Desktop

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.xml_output_checker import (  # noqa: E402
    REQUIRED_XML_KINDS,
    find_existing_xml_kinds,
    format_kinds,
    missing_xml_kinds,
)
from hbsys_cf4_grid_reader import find_matching_cf4_grid_row  # noqa: E402
from hbsys_read_admission_history import (  # noqa: E402
    DATE_RE,
    OcrItem,
    find_admission_history_window,
    parse_rows_with_positions,
    read_ocr_item_variants,
    read_ocr_items,
)
from hbsys_ready_claims import DEFAULT_READY_DIR, ReadyClaim, load_ready_claims  # noqa: E402
from hbsys_window import is_hbsys_window, window_title  # noqa: E402


LOG_DIR = Path("logs")
FTPURL_DIR = Path(r"C:\Shared Folder\FTPURL")
CF4_DATE_RE = re.compile(r"\d{2}[-/]\d{2}[-/]\d{4}")


@dataclass(frozen=True)
class Point:
    x: int
    y: int


class P:
    CF4_XML = Point(486, 58)
    CF5_XML = Point(536, 58)
    ESOA_XML = Point(586, 58)

    CF4_PATIENT_NAME = Point(244, 138)
    CF4_SEARCH = Point(424, 138)
    CF4_PROCESS = Point(36, 96)
    CF4_GRID_CHECKBOX_X = 42

    HOSPITAL_NO = Point(253, 105)
    SEARCH = Point(399, 105)
    VALIDATE = Point(399, 132)
    GENERATE_XML = Point(399, 161)


def sleep_short(seconds: float = 0.35) -> None:
    time.sleep(seconds)


def normalize_name(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", (value or "").upper()).strip()


def name_tokens(value: str) -> list[str]:
    return [token for token in normalize_name(value).split() if len(token) >= 3]


def normalize_date(value: str) -> str:
    return (value or "").replace("-", "/").strip()


def collect_ftpurl_xml_snapshot() -> dict[str, tuple[int, int]]:
    if not FTPURL_DIR.exists():
        return {}

    snapshot: dict[str, tuple[int, int]] = {}
    for path in FTPURL_DIR.glob("*.xml"):
        try:
            stat_result = path.stat()
        except OSError:
            continue
        snapshot[str(path)] = (stat_result.st_mtime_ns, stat_result.st_size)
    return snapshot


def changed_ftpurl_xml_files(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
) -> list[str]:
    changed: list[str] = []
    for path, signature in after.items():
        if before.get(path) != signature:
            changed.append(path)
    return sorted(
        changed,
        key=lambda value: after.get(value, (0, 0))[0],
        reverse=True,
    )


def format_changed_files(paths: list[str], limit: int = 12) -> str:
    if not paths:
        return ""
    labels = [str(Path(path).relative_to(FTPURL_DIR)) for path in paths[:limit]]
    if len(paths) > limit:
        labels.append(f"... +{len(paths) - limit} more")
    return " | ".join(labels)


def detected_xml_kinds(paths: list[str]) -> set[str]:
    kinds: set[str] = set()
    for path in paths:
        name = Path(path).name.upper()
        if "_CF4" in name:
            kinds.add("CF4")
        if "_CF5" in name:
            kinds.add("CF5")
        if "_ESOA" in name:
            kinds.add("ESOA")
    return kinds


class XmlGeneratorOperator:
    def __init__(self, live: bool, pause: float, confirm_each: bool):
        self.live = live
        self.pause = pause
        self.confirm_each = confirm_each
        self.hbsys_window = None

    def log_action(self, message: str) -> None:
        prefix = "LIVE" if self.live else "DRY"
        print(f"[{prefix}] {message}")

    def maybe_wait(self) -> None:
        time.sleep(self.pause)

    def click(self, point: Point, label: str) -> None:
        self.log_action(f"click {label} at ({point.x}, {point.y})")
        if self.live:
            pyautogui.click(point.x, point.y)
        self.maybe_wait()

    def double_click(self, point: Point, label: str) -> None:
        self.log_action(f"double-click {label} at ({point.x}, {point.y})")
        if self.live:
            pyautogui.doubleClick(point.x, point.y)
        self.maybe_wait()

    def press(self, key: str) -> None:
        self.log_action(f"press {key}")
        if self.live:
            pyautogui.press(key)
        self.maybe_wait()

    def hotkey(self, *keys: str) -> None:
        self.log_action(f"hotkey {'+'.join(keys)}")
        if self.live:
            pyautogui.hotkey(*keys)
        self.maybe_wait()

    def write(self, text: str) -> None:
        self.log_action(f"type {text!r}")
        if self.live:
            pyautogui.write(text, interval=0.01)
        self.maybe_wait()

    def focus_hbsys(self) -> None:
        windows = [
            window
            for window in Desktop(backend="win32").windows()
            if is_hbsys_window(window) or "HOSPITAL" in window_title(window)
        ]
        windows = sorted(
            windows,
            key=lambda window: (
                not window_title(window).startswith("HOSPITAL"),
                "HBSys" not in window_title(window),
            ),
        )
        for window in windows:
            title = window_title(window)
            self.log_action(f"focus HBSys/eClaims window: {title!r}")
            if self.live:
                try:
                    window.maximize()
                    window.set_focus()
                except Exception:
                    pass
            self.hbsys_window = window
            self.maybe_wait()
            self.verify_screen_layout()
            return
        raise RuntimeError("HBSys/eClaims window not found.")

    def refresh_generator_window(self, title_part: str, timeout: float = 3.0) -> None:
        if not self.live:
            return

        deadline = time.time() + timeout
        while time.time() < deadline:
            for window in Desktop(backend="win32").windows():
                title = window.window_text()
                if "HOSPITAL" not in title or title_part not in title:
                    continue
                rect = window.rectangle()
                if rect.right <= 0 or rect.bottom <= 0:
                    continue
                self.log_action(f"active generator window: {title!r}")
                try:
                    window.set_focus()
                except Exception:
                    pass
                self.hbsys_window = window
                self.maybe_wait()
                return
            time.sleep(0.15)

    def close_stale_select_encounter(self) -> None:
        if not self.live:
            return
        for window in Desktop(backend="win32").windows():
            if window.window_text().strip() != "Select Encounter":
                continue
            self.log_action("close stale Select Encounter popup before switching screens")
            try:
                window.set_focus()
            except Exception:
                pass
            try:
                window.close()
            except Exception:
                rect = window.rectangle()
                pyautogui.click(rect.right - 14, rect.top + 12)
            sleep_short(0.4)
            return

    def verify_screen_layout(self) -> None:
        width, height = pyautogui.size()
        self.log_action(f"screen size detected: {width}x{height}")
        if self.live and (width, height) != (1920, 1080):
            raise RuntimeError(
                "Screen must be 1920x1080 for this coordinate-based XML script."
            )

    def capture_window(self, prefix: str) -> Path:
        if self.hbsys_window is None:
            raise RuntimeError("HBSys/eClaims window is not focused.")
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.png"
        image = self.hbsys_window.capture_as_image()
        image.save(path)
        self.log_action(f"captured {path}")
        return path

    def dismiss_dialog(self, context: str, timeout: float = 30.0) -> bool:
        self.log_action(f"wait for OK dialog after {context}")
        if not self.live:
            self.log_action("would click OK if a dialog appears")
            return True

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                windows = Desktop(backend="win32").windows()
            except Exception:
                time.sleep(0.15)
                continue

            for window in windows:
                try:
                    title = window.window_text().strip()
                    class_name = window.class_name()
                except Exception:
                    continue
                if class_name != "#32770":
                    continue
                if not title or title in {"Select Encounter"}:
                    continue

                text_blob = ""
                try:
                    descendants = window.descendants()
                except Exception:
                    descendants = []

                for child in descendants:
                    try:
                        text_blob = f"{text_blob} {child.window_text()}"
                    except Exception:
                        continue

                if not any(
                    token in f"{title} {text_blob}".upper()
                    for token in (
                        "XML",
                        "VALIDATION",
                        "PROCESSING COMPLETED",
                        "SUCCESS",
                        "GENERATED",
                        "DATA IS CLEAN",
                    )
                ):
                    continue

                self.log_action(f"dismiss dialog {title!r}: {text_blob!r}")
                try:
                    window.set_focus()
                except Exception:
                    pass
                try:
                    pyautogui.press("enter")
                except Exception:
                    pass
                self.maybe_wait()
                sleep_short(0.4)
                return True

            time.sleep(0.15)

        self.log_action(f"no OK dialog detected after {context}")
        return False

    def dismiss_possible_dialogs(self, context: str, count: int) -> None:
        for index in range(count):
            if not self.dismiss_dialog(f"{context} dialog {index + 1}", timeout=90.0):
                break

    def open_generator(self, point: Point, label: str) -> None:
        if label == "CF4 XML":
            self.close_stale_select_encounter()
        self.focus_hbsys()
        self.click(point, label)
        sleep_short(1.5)
        self.refresh_generator_window(label)

    def search_cf4_patient(self, claim: ReadyClaim) -> None:
        self.double_click(P.CF4_PATIENT_NAME, "CF4 Patient Name")
        self.hotkey("ctrl", "a")
        self.write(claim.patient_name)
        self.click(P.CF4_SEARCH, "CF4 Search")
        sleep_short(1.8)

    def find_cf4_result_row_y(self, items: list[OcrItem], claim: ReadyClaim) -> float | None:
        relevant_items = [
            item
            for item in items
            if 170 <= item.y <= 1020 and item.confidence >= 0.20 and item.text
        ]
        relevant_items.sort(key=lambda item: item.y)

        rows: list[list[OcrItem]] = []
        for item in relevant_items:
            for row in rows:
                if abs(row[0].y - item.y) <= 10:
                    row.append(item)
                    break
            else:
                rows.append([item])

        tokens = name_tokens(claim.patient_name)
        expected_admission = normalize_date(claim.admission_grid)
        expected_discharge = normalize_date(claim.discharge_grid)
        expected_hospital_suffix = claim.hospital_no.lstrip("0")
        candidates: list[tuple[int, float, str]] = []
        for row in rows:
            row.sort(key=lambda item: item.x)
            row_text = " ".join(item.text for item in row)
            normalized = normalize_name(row_text)
            dates = [
                normalize_date(match.group(0))
                for item in row
                for match in CF4_DATE_RE.finditer(item.text)
            ]
            row_digits = re.sub(r"\D", "", row_text)

            admission_ok = expected_admission in dates
            discharge_ok = expected_discharge in dates
            hospital_ok = claim.hospital_no in row_digits or (
                bool(expected_hospital_suffix)
                and expected_hospital_suffix in row_digits.lstrip("0")
            )
            name_score = sum(1 for token in tokens if token in normalized)

            date_score = int(admission_ok) + int(discharge_ok)
            is_match = (
                hospital_ok and (date_score >= 1 or name_score >= 1)
            ) or (
                date_score == 2 and name_score >= 2
            )

            if is_match:
                row_y = sum(item.y for item in row) / len(row)
                score = name_score + date_score * 2 + (4 if hospital_ok else 0)
                candidates.append((score, row_y, row_text))

        if not candidates:
            self.log_action(
                "no CF4 candidate matched expected "
                f"name={claim.patient_name!r}, admission={expected_admission}, "
                f"discharge={expected_discharge}, hospital_no={claim.hospital_no}"
            )
            return None

        candidates.sort(key=lambda item: (-item[0], item[1]))
        best_score, best_y, best_text = candidates[0]
        if len(candidates) > 1 and candidates[1][0] == best_score:
            self.log_action("CF4 result row is ambiguous; matching rows:")
            for score, row_y, text in candidates:
                self.log_action(f"candidate score={score} y={row_y:.1f} text={text!r}")
            return None

        self.log_action(f"selected CF4 row y={best_y:.1f}: {best_text!r}")
        return best_y

    def find_cf4_highlight_row_y(
        self,
        image_path: Path,
        preferred_y: float | None,
    ) -> float | None:
        image = Image.open(image_path).convert("RGB")
        width, height = image.size

        search_ranges: list[tuple[int, int]] = []
        if preferred_y is not None:
            center = int(preferred_y)
            search_ranges.append((max(170, center - 22), min(height - 1, center + 22)))
        search_ranges.append((170, min(height - 1, 1020)))

        best_y: int | None = None
        best_count = 0
        for start_y, end_y in search_ranges:
            for y in range(start_y, end_y + 1):
                count = 0
                for x in range(55, min(width, 1270), 8):
                    red, green, blue = image.getpixel((x, y))
                    if blue >= 145 and 70 <= green <= 180 and red <= 80:
                        count += 1
                if count > best_count:
                    best_count = count
                    best_y = y
            if best_y is not None and best_count >= 20:
                self.log_action(
                    f"detected CF4 highlighted row y={best_y} using blue band"
                )
                return float(best_y)

        return None

    @staticmethod
    def cf4_checkbox_image_looks_checked(image: Image.Image) -> tuple[bool, int, int]:
        """Recognize both HBSys checkbox styles: dark tick or blue tick."""
        dark_inside = 0
        blue_inside = 0
        for x in range(4, 11):
            for y in range(4, 11):
                red, green, blue = image.getpixel((x, y))[:3]
                if red < 80 and green < 80 and blue < 80:
                    dark_inside += 1
                if (
                    blue >= 140
                    and 60 <= green <= 200
                    and red <= 160
                    and blue - red >= 40
                ):
                    blue_inside += 1
        checked = dark_inside >= 5 or blue_inside >= 5
        return checked, dark_inside, blue_inside

    def cf4_checkbox_looks_checked(self, screen_x: int, screen_y: int) -> bool:
        image = pyautogui.screenshot(region=(screen_x - 7, screen_y - 7, 15, 15))
        checked, dark_inside, blue_inside = self.cf4_checkbox_image_looks_checked(image)
        self.log_action(
            f"CF4 checkbox state at screen ({screen_x}, {screen_y}): "
            f"dark_inside={dark_inside}, blue_inside={blue_inside}, checked={checked}"
        )
        return checked

    def find_cf4_checkbox_center_on_screen(self) -> tuple[int, int] | None:
        image = pyautogui.screenshot()
        width, height = image.size

        blue_rows: list[int] = []
        for y in range(165, min(height, 1030)):
            blue_count = 0
            for x in range(50, min(width, 1300), 8):
                red, green, blue = image.getpixel((x, y))[:3]
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

        # The selected result row is the first wide blue band under the grid header.
        row_group = max(groups, key=len)
        row_top = min(row_group)
        row_bottom = max(row_group)

        dark_points: list[tuple[int, int]] = []
        for y in range(max(0, row_top - 4), min(height, row_bottom + 5)):
            for x in range(25, 65):
                red, green, blue = image.getpixel((x, y))[:3]
                if red < 90 and green < 90 and blue < 90:
                    dark_points.append((x, y))

        if dark_points:
            min_x = min(point[0] for point in dark_points)
            max_x = max(point[0] for point in dark_points)
            min_y = min(point[1] for point in dark_points)
            max_y = max(point[1] for point in dark_points)
            center = ((min_x + max_x) // 2, (min_y + max_y) // 2)
            self.log_action(
                "detected CF4 checkbox on screen "
                f"bbox=({min_x},{min_y})-({max_x},{max_y}) center={center}"
            )
            return center

        center_y = (row_top + row_bottom) // 2
        fallback = (P.CF4_GRID_CHECKBOX_X, center_y)
        self.log_action(f"using CF4 checkbox fallback center={fallback}")
        return fallback

    def select_cf4_matching_row(self, claim: ReadyClaim) -> bool:
        if not self.live:
            self.log_action(
                "would OCR CF4 grid and select row matching "
                f"{claim.patient_name} | {claim.admission_grid}-{claim.discharge_grid}"
            )
            return True

        image_path = self.capture_window("cf4_result_grid")
        # Normal CF4 Search already highlights the matching patient. Validate
        # that row directly first so a slow/noisy full-grid OCR pass cannot
        # prevent the required checkbox click.
        row = find_matching_cf4_grid_row(
            image_path,
            [],
            expected_patient_name=claim.patient_name,
            expected_hospital_no=claim.hospital_no,
            expected_admission=claim.admission_grid,
            expected_discharge=claim.discharge_grid,
        )
        if row is None:
            self.log_action(
                "highlighted CF4 row did not match folder confinement; "
                "scan other visible result rows"
            )
            row = find_matching_cf4_grid_row(
                image_path,
                read_ocr_item_variants(image_path),
                expected_patient_name=claim.patient_name,
                expected_hospital_no=claim.hospital_no,
                expected_admission=claim.admission_grid,
                expected_discharge=claim.discharge_grid,
            )
        if row is None:
            self.log_action("matching CF4 result row not found; stopping for review")
            return False

        self.log_action(
            "validated CF4 row from output-folder identity: "
            f"name={row.patient_name!r}, hospital_no={row.hospital_no}, "
            f"admission={row.admission_date}, discharge={row.discharge_date}, "
            f"y={row.y:.1f}"
        )
        if self.hbsys_window is None:
            raise RuntimeError("CF4 generator window is not focused.")

        # row.y and the checkbox X are coordinates inside capture_as_image().
        # A maximized Win32 window normally starts at (-8, -8), so using them
        # directly as screen coordinates misses the checkbox by eight pixels.
        window_rect = self.hbsys_window.rectangle()
        click_x = window_rect.left + P.CF4_GRID_CHECKBOX_X
        click_y = window_rect.top + int(round(row.y))
        self.log_action(
            "click CF4 include checkbox "
            f"local=({P.CF4_GRID_CHECKBOX_X}, {row.y:.1f}) "
            f"window_origin=({window_rect.left}, {window_rect.top}) "
            f"screen=({click_x}, {click_y})"
        )
        if self.cf4_checkbox_looks_checked(click_x, click_y):
            self.log_action("CF4 checkbox already checked; no click needed")
            return True
        pyautogui.moveTo(click_x, click_y, duration=0.08)
        pyautogui.click(click_x, click_y, clicks=1)
        sleep_short(0.5)
        self.capture_window("cf4_checkbox_after_single_click")
        if self.cf4_checkbox_looks_checked(click_x, click_y):
            self.log_action("CF4 include checkbox confirmed checked")
            return True

        self.log_action(
            "CF4 include checkbox was clicked once but is not confirmed checked; "
            "stopping safely without clicking Process"
        )
        return False

    def process_cf4(self, claim: ReadyClaim) -> str:
        self.open_generator(P.CF4_XML, "CF4 XML")
        self.search_cf4_patient(claim)
        if not self.select_cf4_matching_row(claim):
            return "needs_review_cf4_result"
        self.click(P.CF4_PROCESS, "CF4 Process")
        if not self.dismiss_dialog("CF4 Process", timeout=60.0):
            self.log_action(
                "CF4 Process did not show a completion/OK dialog; "
                "do not continue to CF5"
            )
            return "needs_review_cf4_process_dialog"
        return "done"

    def search_hospital_number(self, claim: ReadyClaim) -> None:
        self.double_click(P.HOSPITAL_NO, "Patient Health Record No.")
        self.hotkey("ctrl", "a")
        self.write(claim.hospital_no)
        self.click(P.SEARCH, "Search")
        sleep_short(1.2)

    def select_encounter_if_present(self, claim: ReadyClaim, context: str) -> bool:
        if not self.live:
            self.log_action(
                f"would select {context} encounter if popup appears: "
                f"{claim.admission_grid}-{claim.discharge_grid}"
            )
            return True

        deadline = time.time() + 4.0
        window = None
        while time.time() < deadline:
            window = find_admission_history_window()
            if window is None:
                for candidate in Desktop(backend="win32").windows():
                    if candidate.window_text().strip() == "Select Encounter":
                        window = candidate
                        break
            if window is not None:
                break
            time.sleep(0.15)

        if window is None:
            self.log_action(f"no {context} multiple-confinement popup; continue")
            return True

        image_path = self.capture_popup(window, f"{context.lower()}_select_encounter")
        ocr_items = read_ocr_items(image_path)
        rows = parse_rows_with_positions(ocr_items)
        for parsed in rows:
            row = parsed.row
            if (
                row.admission_date == claim.admission_grid
                and row.discharge_date == claim.discharge_grid
            ):
                rect = window.rectangle()
                click_x = rect.left + 72
                click_y = rect.top + int(parsed.y)
                self.log_action(
                    f"select {context} encounter "
                    f"{row.admission_date}-{row.discharge_date} at ({click_x}, {click_y})"
                )
                pyautogui.doubleClick(click_x, click_y)
                sleep_short(1.0)
                return True

        fallback_y = self.find_encounter_row_y_from_ocr(ocr_items, claim)
        if fallback_y is not None:
            rect = window.rectangle()
            click_x = rect.left + 72
            click_y = rect.top + int(fallback_y)
            self.log_action(
                f"select {context} encounter by OCR fallback at ({click_x}, {click_y})"
            )
            pyautogui.doubleClick(click_x, click_y)
            sleep_short(1.0)
            return True

        self.log_action(f"matching {context} encounter not found; stopping for review")
        return False

    def capture_popup(self, window, prefix: str) -> Path:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.png"
        image = window.capture_as_image()
        image.save(path)
        self.log_action(f"captured {path}")
        return path

    def find_encounter_row_y_from_ocr(
        self,
        items: list[OcrItem],
        claim: ReadyClaim,
    ) -> float | None:
        row_items = [item for item in items if item.y > 75 and item.confidence >= 0.20]
        row_items.sort(key=lambda item: item.y)

        rows: list[list[OcrItem]] = []
        for item in row_items:
            for row in rows:
                if abs(row[0].y - item.y) <= 12:
                    row.append(item)
                    break
            else:
                rows.append([item])

        expected_admission = normalize_date(claim.admission_grid)
        expected_discharge = normalize_date(claim.discharge_grid)
        candidates: list[tuple[int, float, str]] = []

        for row in rows:
            row.sort(key=lambda item: item.x)
            row_text = " ".join(item.text for item in row)
            normalized_text = normalize_date(row_text)
            compact_text = re.sub(r"[^0-9/]", "", normalized_text)
            admission_ok = expected_admission in normalized_text
            discharge_ok = expected_discharge in normalized_text
            discharge_partial_ok = expected_discharge[:5] in compact_text
            if admission_ok:
                score = 2 + int(discharge_ok) + int(discharge_partial_ok)
                row_y = sum(item.y for item in row) / len(row)
                candidates.append((score, row_y, row_text))

        if not candidates:
            return None

        candidates.sort(key=lambda item: (-item[0], item[1]))
        best_score, best_y, best_text = candidates[0]
        if len(candidates) > 1 and candidates[1][0] == best_score:
            self.log_action("encounter row is ambiguous; matching rows:")
            for score, row_y, text in candidates:
                self.log_action(f"candidate score={score} y={row_y:.1f} text={text!r}")
            return None

        self.log_action(f"selected encounter row y={best_y:.1f}: {best_text!r}")
        return best_y

    def validate_and_generate(self, label: str) -> None:
        self.click(P.VALIDATE, f"{label} Validate")
        self.dismiss_dialog(f"{label} Validate", timeout=60.0)
        self.click(P.GENERATE_XML, f"{label} Generate XML")
        self.dismiss_possible_dialogs(f"{label} Generate XML", count=2)

    def process_cf5(self, claim: ReadyClaim) -> str:
        self.open_generator(P.CF5_XML, "CF5 XML")
        self.search_hospital_number(claim)
        if not self.select_encounter_if_present(claim, "CF5"):
            return "needs_review_cf5_encounter"
        self.validate_and_generate("CF5")
        return "done"

    def process_esoa(self, claim: ReadyClaim) -> str:
        self.open_generator(P.ESOA_XML, "eSOA XML")
        self.search_hospital_number(claim)
        if not self.select_encounter_if_present(claim, "eSOA"):
            return "needs_review_esoa_encounter"
        self.validate_and_generate("eSOA")
        return "done"

    def process_claim(
        self,
        claim: ReadyClaim,
        kinds_to_process: set[str] | None = None,
    ) -> str:
        """Generate the requested XML kinds (default: all three, CF4 first)."""
        self.log_action(
            f"processing XML for {claim.patient_name} | {claim.hospital_no} | "
            f"ADM {claim.admission_grid} DIS {claim.discharge_grid} | "
            f"kinds={format_kinds(kinds_to_process or REQUIRED_XML_KINDS)}"
        )

        if self.confirm_each:
            input("Press Enter to process this claim, or Ctrl+C to stop...")

        requested = kinds_to_process or REQUIRED_XML_KINDS
        for kind_label, action in (
            ("cf4", self.process_cf4),
            ("cf5", self.process_cf5),
            ("esoa", self.process_esoa),
        ):
            if kind_label.upper() not in requested:
                continue
            status = action(claim)
            if status != "done":
                return f"{kind_label}_{status}"
        return "done"


def write_run_log(rows: list[dict[str, str]]) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"xml_generator_run_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "patient_name",
                "hospital_no",
                "admission",
                "discharge",
                "output_folder",
                "existing_xml",
                "missing_xml",
                "ftpurl_xml_count",
                "ftpurl_xml_files",
                "status",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def open_run_log(path: Path) -> None:
    try:
        os.startfile(path.resolve())  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - opening CSV is convenience only.
        print(f"Could not open run log automatically: {exc}")


def show_completion_popup(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
            0,
            message,
            "XML Generator Clicker",
            0x40 | 0x10000,
        )
    except Exception as exc:  # noqa: BLE001 - popup is convenience only.
        print(f"Could not show completion popup: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate CF4, CF5, and eSOA XML in HBSys for output folders."
    )
    parser.add_argument("--ready-dir", type=Path, default=DEFAULT_READY_DIR)
    parser.add_argument("--live", action="store_true", help="Actually click/type in HBSys.")
    parser.add_argument("--limit", type=int, help="Limit number of claims to process.")
    parser.add_argument("--hospital-no", help="Process one exact hospital number.")
    parser.add_argument("--pause", type=float, default=0.35)
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop after the first patient that is not done.",
    )
    parser.add_argument(
        "--confirm-each",
        action="store_true",
        help="Ask before every patient. Recommended for first live run.",
    )
    args = parser.parse_args()

    claims = load_ready_claims(args.ready_dir)
    if args.hospital_no:
        claims = [claim for claim in claims if claim.hospital_no == args.hospital_no]
    if args.limit:
        claims = claims[: args.limit]

    if not claims:
        print("No output folders to process.")
        return 0

    operator = XmlGeneratorOperator(
        live=args.live,
        pause=args.pause,
        confirm_each=args.confirm_each,
    )
    results: list[dict[str, str]] = []

    print(f"Mode: {'LIVE' if args.live else 'DRY-RUN'}")
    print(f"Claims: {len(claims)}")
    if not args.live:
        print("Dry-run only. Add --live to actually click/type in HBSys.")

    skipped_count = sum(
        1
        for claim in claims
        if not missing_xml_kinds(find_existing_xml_kinds(claim.folder))
    )
    print(f"Already complete (will be skipped): {skipped_count}")
    print(f"To process: {len(claims) - skipped_count}")

    for claim in claims:
        existing_kinds = find_existing_xml_kinds(claim.folder)
        pending_kinds = missing_xml_kinds(existing_kinds)

        if not pending_kinds:
            operator.log_action(
                f"SKIPPED {claim.patient_name}: all required XML already exist in "
                f"the output folder ({format_kinds(existing_kinds)})"
            )
            results.append(
                {
                    "patient_name": claim.patient_name,
                    "hospital_no": claim.hospital_no,
                    "admission": claim.admission_hbsys,
                    "discharge": claim.discharge_hbsys,
                    "output_folder": str(claim.folder),
                    "existing_xml": format_kinds(existing_kinds),
                    "missing_xml": "",
                    "ftpurl_xml_count": "0",
                    "ftpurl_xml_files": "",
                    "status": "skipped_complete_xml",
                }
            )
            continue

        operator.log_action(
            f"XML pre-check {claim.patient_name}: existing="
            f"{format_kinds(existing_kinds) or 'NONE'} | "
            f"to generate={format_kinds(pending_kinds)}"
        )

        ftpurl_before = collect_ftpurl_xml_snapshot()
        changed_xml_files: list[str] = []
        try:
            status = operator.process_claim(claim, kinds_to_process=pending_kinds)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - operator log should continue.
            status = f"error: {exc}"
            operator.log_action(status)
        finally:
            ftpurl_after = collect_ftpurl_xml_snapshot()
            changed_xml_files = changed_ftpurl_xml_files(ftpurl_before, ftpurl_after)

        if status.startswith("error:") and changed_xml_files:
            xml_kinds = detected_xml_kinds(changed_xml_files)
            still_missing = set(pending_kinds) - xml_kinds
            if not still_missing:
                status = "done_with_warning"
                operator.log_action(
                    "A dialog/window warning occurred, but all requested XML outputs were detected."
                )
        elif status == "done":
            xml_kinds = detected_xml_kinds(changed_xml_files)
            still_missing = set(pending_kinds) - xml_kinds
            if still_missing:
                status = "missing_xml_outputs:" + ",".join(sorted(still_missing))
                operator.log_action(
                    "XML workflow ended but requested FTPURL output is missing: "
                    + ", ".join(sorted(still_missing))
                )
        if status == "done" and not changed_xml_files:
            status = "done_no_ftpurl_xml_detected"
            operator.log_action(
                "XML dialogs completed but no new/updated XML was detected in "
                f"{FTPURL_DIR}"
            )
        elif changed_xml_files:
            operator.log_action(
                f"FTPURL XML changed/new files detected: {len(changed_xml_files)}"
            )

        results.append(
            {
                "patient_name": claim.patient_name,
                "hospital_no": claim.hospital_no,
                "admission": claim.admission_hbsys,
                "discharge": claim.discharge_hbsys,
                "output_folder": str(claim.folder),
                "existing_xml": format_kinds(existing_kinds),
                "missing_xml": format_kinds(pending_kinds),
                "ftpurl_xml_count": str(len(changed_xml_files)),
                "ftpurl_xml_files": format_changed_files(changed_xml_files),
                "status": status,
            }
        )

        if status != "done" and args.stop_on_error:
            break

    log_path = write_run_log(results)
    print(f"Run log: {log_path.resolve()}")
    if args.live:
        done_count = sum(
            1 for row in results if row.get("status", "").startswith("done")
        )
        skipped_count = sum(
            1 for row in results if row.get("status") == "skipped_complete_xml"
        )
        show_completion_popup(
            "XML Generator Clicker finished.\n\n"
            f"Claims checked: {len(results)}\n"
            f"Skipped (already complete): {skipped_count}\n"
            f"Completed: {done_count}\n"
            f"Needs review/errors: {len(results) - done_count - skipped_count}\n\n"
            f"Run log:\n{log_path.resolve()}"
        )
    else:
        open_run_log(log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
