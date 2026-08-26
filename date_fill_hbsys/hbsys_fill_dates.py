from __future__ import annotations

import argparse
import csv
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pyautogui
from pywinauto import Desktop

from hbsys_read_admission_history import (
    DATE_RE,
    OcrItem,
    find_admission_history_window,
    parse_rows_with_positions,
    read_ocr_item_variants,
    read_ocr_items,
)
from hbsys_date_fill_precheck import (
    PRECHECK_PROCESS,
    PrecheckResult,
    precheck_claim,
)
from hbsys_date_fill_verifier import HbsysDateFillVerifier
from hbsys_ready_claims import (
    DEFAULT_READY_DIR,
    ReadyClaim,
    load_ready_claims,
    parse_ready_claim_folder,
)
from hbsys_window import find_hbsys_window


LOG_DIR = Path("logs")


@dataclass(frozen=True)
class Point:
    x: int
    y: int


class P:
    HOSPITAL_NO = Point(166, 174)
    ADMIT_HISTORY = Point(435, 58)
    PHIC = Point(486, 58)
    CLAIM_FORM_2 = Point(84, 58)
    EDIT = Point(36, 58)
    SAVE = Point(36, 58)
    CANCEL_BENEFICIARIES = Point(185, 58)
    CLOSE_FORM_CF2 = Point(84, 58)
    # Current HBSys Beneficiaries toolbar always shows a Details button, so the
    # Close Form button sits at x=485. x=435 is the legacy slot for toolbars
    # that have no Details button.
    CLOSE_FORM_BENEFICIARIES = Point(485, 58)
    CLOSE_FORM_BENEFICIARIES_ALT = Point(435, 58)
    TAB_PROF_FEES = Point(619, 132)
    TAB_CONSENT = Point(872, 132)
    PROF_DATE_SIGNED_CELL = Point(940, 179)
    CONSENT_DATE_SIGNED = Point(153, 300)
    CONSENT_CERT_DATE = Point(770, 598)


def sleep_short(seconds: float = 0.35) -> None:
    time.sleep(seconds)


class HbsysOperator:
    def __init__(self, live: bool, pause: float, confirm_each: bool):
        self.live = live
        self.pause = pause
        self.confirm_each = confirm_each
        self.hbsys_window = None
        self.screen_stage = "base"

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
        window = find_hbsys_window()
        if window is None:
            raise RuntimeError("HBSys window not found. Open HBSys and try again.")
        self.log_action(f"focus HBSys window: {window.window_text()!r}")
        if self.live:
            try:
                window.maximize()
                window.set_focus()
            except Exception:
                pass
        self.hbsys_window = window
        self.maybe_wait()
        self.verify_screen_layout()

    def dismiss_phic_message(self, context: str, timeout: float = 4.0) -> bool:
        self.log_action(f"wait for PHIC save message after {context}")
        if not self.live:
            self.log_action("would click OK if PHIC save message appears")
            return True

        deadline = time.time() + timeout
        while time.time() < deadline:
            for window in Desktop(backend="win32").windows():
                try:
                    title = window.window_text().strip()
                    class_name = window.class_name()
                except Exception:
                    continue

                if title != "PHIC" or class_name != "#32770":
                    continue

                try:
                    text_blob = " ".join(
                        child.window_text() for child in window.descendants()
                    )
                except Exception:
                    text_blob = ""

                if "Record" not in text_blob and "saved" not in text_blob.lower():
                    continue

                self.log_action(f"dismiss PHIC message: {text_blob!r}")
                try:
                    window.set_focus()
                    ok_button = window.child_window(title="OK", class_name="Button")
                    ok_button.click()
                except Exception:
                    self.press("enter")
                sleep_short(0.4)
                return True

            time.sleep(0.15)

        self.log_action(f"no PHIC save message detected after {context}")
        return False

    def verify_screen_layout(self) -> None:
        width, height = pyautogui.size()
        self.log_action(f"screen size detected: {width}x{height}")
        if self.live and (width, height) != (1920, 1080):
            raise RuntimeError(
                "Screen must be 1920x1080 for this coordinate-based script."
            )

    def search_hospital_number(self, claim: ReadyClaim) -> None:
        self.double_click(P.HOSPITAL_NO, "Hospital No.")
        self.hotkey("ctrl", "a")
        self.write(claim.hospital_no)
        self.press("enter")
        sleep_short(1.5)

    def select_admission_history_row(self, claim: ReadyClaim) -> bool:
        self.click(P.ADMIT_HISTORY, "Admit History")
        self.screen_stage = "admission_popup"
        sleep_short(0.8)

        if not self.live:
            self.log_action(
                "would OCR Admission History and select row matching "
                f"{claim.admission_grid} - {claim.discharge_grid}"
            )
            return True

        window = find_admission_history_window()
        if window is None:
            raise RuntimeError("Admission History popup did not open.")

        image_path = self.capture_window(window, "admission_history_select")
        rows = parse_rows_with_positions(read_ocr_items(image_path))
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
                    "select exact Admission History row "
                    f"{claim.admission_grid}-{claim.discharge_grid} "
                    f"at ({click_x}, {click_y})"
                )
                pyautogui.doubleClick(click_x, click_y)
                sleep_short(0.8)
                self.screen_stage = "base"
                return True

        fuzzy = self.best_fuzzy_admission_history_row(
            rows,
            claim.admission_grid,
            claim.discharge_grid,
        )
        if fuzzy is not None:
            rect = window.rectangle()
            click_x = rect.left + 72
            click_y = rect.top + int(fuzzy.y)
            self.log_action(
                "select OCR-tolerant Admission History row "
                f"{fuzzy.row.admission_date}-{fuzzy.row.discharge_date} "
                f"for folder {claim.admission_grid}-{claim.discharge_grid} "
                f"at ({click_x}, {click_y})"
            )
            pyautogui.doubleClick(click_x, click_y)
            sleep_short(0.8)
            self.screen_stage = "base"
            return True

        if len(rows) == 1:
            parsed = rows[0]
            row = parsed.row
            self.log_action(
                "Only one Admission History row was detected, but it did not "
                "safely match the folder admission/discharge dates: "
                f"HBSys OCR {row.admission_date}-{row.discharge_date}, "
                f"folder {claim.admission_grid}-{claim.discharge_grid}. Stopping."
            )
            return False

        self.log_action(
            "matching Admission History row not found for "
            f"{claim.admission_grid}-{claim.discharge_grid}; stopping for review"
        )
        return False

    def best_fuzzy_admission_history_row(self, rows, admission_date, discharge_date):
        scored = []
        for parsed in rows:
            score = self.ocr_date_match_score(
                parsed.row.admission_date, admission_date
            ) + self.ocr_date_match_score(parsed.row.discharge_date, discharge_date)
            if parsed.row.normalized_encounter_type == "ADMIT":
                score += 10
            scored.append((score, parsed))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored or scored[0][0] < 115:
            return None
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 25:
            return None
        return scored[0][1]

    @staticmethod
    def ocr_date_match_score(ocr_value: str, expected_value: str) -> int:
        if ocr_value == expected_value:
            return 100
        try:
            ocr_date = datetime.strptime(ocr_value, "%m/%d/%Y")
            expected_date = datetime.strptime(expected_value, "%m/%d/%Y")
        except ValueError:
            return 0
        if ocr_date.month == expected_date.month and ocr_date.day == expected_date.day:
            return 80
        if ocr_date.month == expected_date.month and ocr_date.year == expected_date.year:
            day_difference = abs(ocr_date.day - expected_date.day)
            if day_difference <= 10:
                return max(35, 65 - day_difference)
            return 25
        if ocr_date.month == expected_date.month:
            return 20
        return 0

    def capture_window(self, window, prefix: str) -> Path:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.png"
        image = window.capture_as_image()
        image.save(path)
        self.log_action(f"captured {path}")
        return path

    def click_phic_and_select_claim(self, claim: ReadyClaim) -> bool:
        self.click(P.PHIC, "PHIC")
        self.screen_stage = "beneficiary"
        sleep_short(1.0)
        if not self.dismiss_claim_form4_after_phic_if_visible():
            return False
        if not self.live:
            self.log_action(
                "would OCR PhilHealth Beneficiaries and select row matching "
                f"{claim.admission_grid} - {claim.discharge_grid}"
            )
            return True

        if self.hbsys_window is None:
            raise RuntimeError("HBSys window is not focused.")

        image_path = self.capture_window(self.hbsys_window, "phic_beneficiaries_select")
        row_y = self.find_phic_beneficiary_row_y_from_variants(
            read_ocr_item_variants(image_path),
            claim.admission_grid,
            claim.discharge_grid,
            claim.patient_name,
        )
        if row_y is None:
            self.log_action(
                "matching PhilHealth Beneficiaries row not found; stopping for review"
            )
            return False

        rect = self.hbsys_window.rectangle()
        click_x = rect.left + 46
        click_y = rect.top + int(row_y)
        self.log_action(
            "select PhilHealth Beneficiaries row "
            f"{claim.admission_grid}-{claim.discharge_grid} at ({click_x}, {click_y})"
        )
        pyautogui.click(click_x, click_y)
        sleep_short(0.5)
        return True

    def find_phic_beneficiary_row_y_from_variants(
        self,
        item_variants: list[list[OcrItem]],
        admission_date: str,
        discharge_date: str,
        patient_name: str = "",
    ) -> float | None:
        """Select a PHIC row using all OCR passes and row-position consensus."""
        row_matches: list[float] = []
        for variant_number, items in enumerate(item_variants, start=1):
            row_y = self.find_phic_beneficiary_row_y(
                items,
                admission_date,
                discharge_date,
                patient_name,
            )
            if row_y is None:
                continue
            row_matches.append(row_y)
            self.log_action(
                f"PHIC OCR variant {variant_number} matched row y={row_y:.1f}"
            )

        if not row_matches:
            return None

        # Different OCR scales may report slightly different centers for the
        # same grid row. Group them, then use the strongest position cluster.
        clusters: list[list[float]] = []
        for row_y in sorted(row_matches):
            for cluster in clusters:
                cluster_center = sum(cluster) / len(cluster)
                if abs(cluster_center - row_y) <= 12:
                    cluster.append(row_y)
                    break
            else:
                clusters.append([row_y])

        clusters.sort(key=lambda cluster: (-len(cluster), sum(cluster) / len(cluster)))
        best_cluster = clusters[0]
        if len(clusters) > 1 and len(best_cluster) == len(clusters[1]):
            self.log_action(
                "PHIC OCR variants matched different rows with equal confidence; "
                "stopping for review"
            )
            return None

        selected_y = sum(best_cluster) / len(best_cluster)
        self.log_action(
            "PHIC OCR consensus selected row "
            f"y={selected_y:.1f} from {len(best_cluster)} matching pass(es)"
        )
        return selected_y

    def find_phic_beneficiary_row_y(
        self,
        items: list[OcrItem],
        admission_date: str,
        discharge_date: str,
        patient_name: str = "",
    ) -> float | None:
        relevant_items = [
            item
            for item in items
            if 130 <= item.y <= 380
            and item.confidence >= 0.25
            and item.text
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

        candidates: list[tuple[float, str, list[OcrItem]]] = []
        admission_only_candidates: list[tuple[float, str, list[OcrItem]]] = []
        dated_name_candidates: list[tuple[float, str, list[OcrItem]]] = []
        name_tokens = self.name_match_tokens(patient_name)
        for row in rows:
            row.sort(key=lambda item: item.x)
            dates = [match.group(0) for item in row for match in DATE_RE.finditer(item.text)]
            row_text = " ".join(item.text for item in row)
            row_y = sum(item.y for item in row) / len(row)
            if admission_date in dates and discharge_date in dates:
                candidates.append((row_y, row_text, row))
            if admission_date in dates:
                admission_only_candidates.append((row_y, row_text, row))
            if dates and name_tokens and any(
                token in self.normalize_for_name_match(row_text)
                for token in name_tokens
            ):
                dated_name_candidates.append((row_y, row_text, row))

        if len(candidates) == 1:
            row_y, row_text, _row = candidates[0]
            return row_y
        if not candidates:
            # Safe OCR fallback: the PHIC grid sometimes misreads the discharge
            # year on the highlighted row (example: 07/01/2026 -> 07/01/2028).
            # If the admission date and patient name point to one row only,
            # accept that row instead of stopping.
            named_admission_candidates: list[tuple[float, str, list[OcrItem]]] = []
            for row_y, row_text, row in admission_only_candidates:
                normalized_row_text = self.normalize_for_name_match(row_text)
                if name_tokens and any(
                    token in normalized_row_text for token in name_tokens
                ):
                    named_admission_candidates.append((row_y, row_text, row))
            if len(named_admission_candidates) == 1:
                row_y, row_text, _row = named_admission_candidates[0]
                self.log_action(
                    "PHIC OCR fallback selected row by admission date + patient name "
                    f"because discharge OCR did not match exactly: {row_text!r}"
                )
                return row_y
            if len(dated_name_candidates) == 1:
                row_y, row_text, _row = dated_name_candidates[0]
                self.log_action(
                    "PHIC OCR fallback selected the only dated row matching patient name: "
                    f"{row_text!r}"
                )
                return row_y
            return None

        if not name_tokens:
            self.log_action(
                "multiple PHIC rows have same confinement and no name tokens available"
            )
            return None

        scored: list[tuple[int, float, str]] = []
        for row_y, row_text, _row in candidates:
            normalized_row_text = self.normalize_for_name_match(row_text)
            score = sum(1 for token in name_tokens if token in normalized_row_text)
            scored.append((score, row_y, row_text))

        scored.sort(key=lambda item: (-item[0], item[1]))
        best_score, best_y, best_text = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else -1
        if best_score == 0 or best_score == second_score:
            self.log_action(
                "multiple PHIC rows share same confinement but name match is ambiguous"
            )
            for score, row_y, row_text in scored:
                self.log_action(f"candidate score={score} y={row_y:.1f} text={row_text!r}")
            return None

        self.log_action(
            f"selected duplicate confinement by patient name score={best_score}: {best_text!r}"
        )
        return best_y

    @staticmethod
    def normalize_for_name_match(value: str) -> str:
        return re.sub(r"[^A-Z0-9]+", " ", value.upper()).strip()

    def name_match_tokens(self, patient_name: str) -> list[str]:
        normalized = self.normalize_for_name_match(patient_name)
        tokens = [token for token in normalized.split() if len(token) >= 3]
        return tokens

    def open_claim_form_2(self) -> None:
        self.click(P.CLAIM_FORM_2, "Claim Form 2")
        self.screen_stage = "cf2"
        sleep_short(1.2)

    def fill_professional_fee_date(self, claim: ReadyClaim) -> None:
        self.click(P.TAB_PROF_FEES, "Professional Fees / Charges tab")
        self.click(P.EDIT, "Edit")
        self.click(P.PROF_DATE_SIGNED_CELL, "Professional Fee Date Signed")
        self.hotkey("ctrl", "a")
        self.write(claim.discharge_hbsys)
        self.press("enter")
        self.click(P.SAVE, "Save Professional Fee")
        self.dismiss_phic_message("Professional Fee save")
        sleep_short(0.8)

    def fill_consent_dates(self, claim: ReadyClaim) -> None:
        self.click(P.TAB_CONSENT, "Consent tab")
        self.click(P.EDIT, "Edit Consent")
        self.click(P.CONSENT_DATE_SIGNED, "Consent Date Signed")
        self.hotkey("ctrl", "a")
        self.write(claim.discharge_hbsys)
        self.click(P.CONSENT_CERT_DATE, "Certification Date")
        self.hotkey("ctrl", "a")
        self.write(claim.discharge_hbsys)
        self.click(P.SAVE, "Save Consent")
        self.dismiss_phic_message("Consent save")
        sleep_short(0.8)

    def close_claim_forms(self) -> None:
        self.click(P.CLOSE_FORM_CF2, "Close Claim Form 2")
        sleep_short(0.8)
        self.close_phic_beneficiaries("Close PhilHealth Beneficiaries")
        sleep_short(0.8)
        self.screen_stage = "base"

    def current_hbsys_text(self, prefix: str) -> str:
        if self.hbsys_window is None:
            return ""
        path = self.capture_window(self.hbsys_window, prefix)
        return " ".join(item.text.upper() for item in read_ocr_items(path))

    @staticmethod
    def is_phic_beneficiaries_text(text: str) -> bool:
        normalized = re.sub(r"[^A-Z0-9]+", " ", text.upper())
        return "PHILHEALTH BENEFICIARIES" in normalized

    @staticmethod
    def is_beneficiary_cancel_visible_text(text: str) -> bool:
        normalized = re.sub(r"[^A-Z0-9]+", " ", text.upper())
        # BENEFICIARIES (not the full PHILHEALTH BENEFICIARIES) because OCR
        # often misreads PHILHEALTH as PHILAEALTH / HEAITH on the title bar.
        return "BENEFICIARIES" in normalized and "CANCEL" in normalized

    def phic_cancel_visible(self) -> bool:
        """Return True when OCR evidence shows the PHIC Beneficiaries + Cancel state.

        Uses every OCR variant plus a focused toolbar-strip pass because the
        small Cancel toolbar label is often missed by a single full-window OCR
        pass. When the Claim Form 4 view stays up, the grid cannot highlight
        rows, so this detection must be reliable.
        """
        if not self.live or self.hbsys_window is None:
            return False
        path = self.capture_window(
            self.hbsys_window, "phic_beneficiaries_cancel_probe"
        )
        for variant in read_ocr_item_variants(path):
            text = " ".join(item.text.upper() for item in variant)
            if self.is_beneficiary_cancel_visible_text(text):
                return True
        try:
            import pytesseract
            from PIL import Image, ImageOps

            image = Image.open(path)
            crop = image.crop((0, 40, 700, 105))
            crop = ImageOps.grayscale(crop)
            crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
            text = pytesseract.image_to_string(crop, config="--psm 6")
            return self.is_beneficiary_cancel_visible_text(text)
        except Exception:  # noqa: BLE001 - toolbar probe is best-effort only.
            return False

    def is_phic_beneficiaries_current_screen(self) -> bool:
        if not self.live:
            return False
        return self.is_phic_beneficiaries_text(
            self.current_hbsys_text("phic_beneficiaries_close_probe")
        )

    def cancel_pending_beneficiary_edit_if_visible(self) -> bool:
        """Cancel a visible PHIC Beneficiaries Cancel state.

        Covers both a pending edit row before closing the form and the Claim
        Form 4 tab view that HBSys sometimes opens right after the PHIC click.
        """
        if not self.live or self.hbsys_window is None:
            return False
        if not self.phic_cancel_visible():
            return False
        self.log_action(
            "PhilHealth Beneficiaries Cancel button detected; cancelling "
            "pending edit / Claim Form 4 view"
        )
        self.click(P.CANCEL_BENEFICIARIES, "Cancel pending PhilHealth Beneficiaries edit")
        sleep_short(0.8)
        return True

    def dismiss_claim_form4_after_phic_if_visible(self) -> bool:
        """Dismiss the Claim Form 4 view that sometimes opens after the PHIC click.

        HBSys can open the PhilHealth Beneficiaries window on the Claim Form 4
        tab with a visible Cancel button instead of the regular Beneficiaries
        grid toolbar. When that happens, click Cancel first so the confinement
        selection flow reads the correct grid.

        Returns False only when Cancel was visible but did not clear after
        repeated probes, meaning the screen must be reviewed instead of clicked
        blindly.
        """
        if not self.live:
            self.log_action(
                "would click Cancel if PHIC Beneficiaries opens on the "
                "Claim Form 4 tab"
            )
            return True

        if not self.cancel_pending_beneficiary_edit_if_visible():
            return True  # regular Beneficiaries grid; nothing to cancel

        for attempt in range(3):
            if not self.phic_cancel_visible():
                self.log_action(
                    "PHIC Beneficiaries Claim Form 4 view dismissed; continuing "
                    "with confinement selection"
                )
                return True
            self.log_action(
                "PHIC Beneficiaries Cancel still visible after dismissal "
                f"(verify attempt {attempt + 1})"
            )
            sleep_short(1.0)

        self.log_action(
            "PHIC Beneficiaries still shows a Cancel button after dismissal; "
            "stopping for review"
        )
        return False

    def dismiss_phic_details_if_open(self) -> bool:
        """Close the PHIC Details child window that opens when a toolbar click
        lands on the Details button instead of the Close Form slot."""
        if not self.live or self.hbsys_window is None:
            return False
        text = self.current_hbsys_text("phic_details_probe")
        if "PHIC DETAILS" not in re.sub(r"[^A-Z0-9]+", " ", text.upper()):
            return False
        self.log_action("PHIC Details window detected; closing it")
        self.hotkey("ctrl", "f4")
        sleep_short(0.8)
        return True

    def close_phic_beneficiaries(self, label: str) -> None:
        self.cancel_pending_beneficiary_edit_if_visible()
        for point, slot_label in (
            (P.CLOSE_FORM_BENEFICIARIES, "with Details"),
            (P.CLOSE_FORM_BENEFICIARIES_ALT, "legacy"),
        ):
            self.click(point, f"{label} - Close Form slot ({slot_label})")
            sleep_short(0.6)
            self.dismiss_phic_details_if_open()
            if not self.is_phic_beneficiaries_current_screen():
                return
            self.log_action(
                "PhilHealth Beneficiaries still open after Close Form slot "
                f"({slot_label}); trying the next slot"
            )
        self.log_action(
            "PhilHealth Beneficiaries still open after both Close Form slots"
        )

    def reset_to_safe_start(self) -> bool:
        """Close PHIC/edit screens so the next hospital number can be entered."""
        if not self.live:
            self.screen_stage = "base"
            return True
        try:
            for window in Desktop(backend="win32").windows():
                try:
                    if window.class_name() == "#32770":
                        window.set_focus()
                        pyautogui.press("esc")
                        sleep_short(0.2)
                except Exception:
                    continue

            admission_window = find_admission_history_window()
            if admission_window is not None:
                try:
                    admission_window.close()
                except Exception:
                    admission_window.set_focus()
                    pyautogui.press("esc")
                sleep_short(0.5)

            if self.screen_stage == "cf2":
                self.click(P.CLOSE_FORM_CF2, "Close Claim Form 2 after failure")
                self.screen_stage = "beneficiary"
            if self.screen_stage == "beneficiary" or self.is_phic_beneficiaries_current_screen():
                self.close_phic_beneficiaries(
                    "Close PhilHealth Beneficiaries after failure"
                )
            sleep_short(0.5)
            if self.hbsys_window is None:
                return False
            proof_text = self.current_hbsys_text("safe_reset_proof")
            safe = (
                find_admission_history_window() is None
                and self.is_safe_reset_text(proof_text)
            )
            self.screen_stage = "base" if safe else self.screen_stage
            return safe
        except Exception as exc:  # noqa: BLE001 - reset must be conservative.
            self.log_action(f"safe reset failed: {exc}")
            return False

    @staticmethod
    def is_safe_reset_text(proof_text: str) -> bool:
        normalized = re.sub(r"[^A-Z0-9]+", " ", proof_text.upper())
        hospital_search_visible = any(
            marker in normalized
            for marker in ("HOSPITAL NO", "HOSPITAL N0", "HOSPITAL NUMBER")
        )
        beneficiary_still_open = "PHILHEALTH BENEFICIARIES" in normalized
        return hospital_search_visible and not beneficiary_still_open

    def process_claim(self, claim: ReadyClaim) -> str:
        self.screen_stage = "base"
        self.log_action(
            f"processing {claim.patient_name} | {claim.hospital_no} | "
            f"ADM {claim.admission_hbsys} DIS {claim.discharge_hbsys}"
        )

        if self.confirm_each:
            input("Press Enter to process this claim, or Ctrl+C to stop...")

        self.focus_hbsys()
        self.search_hospital_number(claim)
        if not self.select_admission_history_row(claim):
            return "needs_review_admission_history"

        if not self.click_phic_and_select_claim(claim):
            return "needs_review_phic_beneficiaries"
        self.open_claim_form_2()
        self.fill_professional_fee_date(claim)
        self.fill_consent_dates(claim)
        self.close_claim_forms()
        return "done"


def write_run_log(rows: list[dict[str, str]]) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"hbsys_fill_run_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "patient_name",
                "hospital_no",
                "admission",
                "discharge",
                "output_folder_name",
                "output_folder_patient_name",
                "selected_patient_name",
                "patient_name_match",
                "output_folder_discharge",
                "entered_discharge",
                "discharge_date_match",
                "precheck",
                "precheck_missing",
                "precheck_reason",
                "safe_reset_confirmed",
                "status",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def normalize_name_for_match(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def match_label(left: str, right: str) -> str:
    return "MATCH" if left == right and left else "NOT MATCH"


def build_cross_check_fields(claim: ReadyClaim) -> dict[str, str]:
    parsed = parse_ready_claim_folder(claim.folder)
    if parsed is None:
        return {
            "output_folder_name": claim.folder.name,
            "output_folder_patient_name": "",
            "selected_patient_name": claim.patient_name,
            "patient_name_match": "NOT MATCH",
            "output_folder_discharge": "",
            "entered_discharge": claim.discharge_hbsys,
            "discharge_date_match": "NOT MATCH",
        }

    output_patient_name = parsed.patient_name
    selected_patient_name = claim.patient_name
    output_discharge = parsed.discharge_hbsys
    entered_discharge = claim.discharge_hbsys

    return {
        "output_folder_name": claim.folder.name,
        "output_folder_patient_name": output_patient_name,
        "selected_patient_name": selected_patient_name,
        "patient_name_match": match_label(
            normalize_name_for_match(output_patient_name),
            normalize_name_for_match(selected_patient_name),
        ),
        "output_folder_discharge": output_discharge,
        "entered_discharge": entered_discharge,
        "discharge_date_match": match_label(output_discharge, entered_discharge),
    }


def open_run_log(path: Path) -> None:
    try:
        os.startfile(path.resolve())  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - opening CSV is convenience only.
        print(f"Could not open run log automatically: {exc}")


def show_popup(title: str, message: str, *, error: bool = False) -> None:
    """Show a small topmost Windows popup for important Date Fill status."""
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        if error:
            messagebox.showerror(title, message, parent=root)
        else:
            messagebox.showinfo(title, message, parent=root)
        root.destroy()
    except Exception as exc:  # noqa: BLE001 - popup is best-effort only.
        print(f"[POPUP ERROR] {exc}")


def describe_stop_status(status: str) -> str:
    mapping = {
        "needs_review_admission_history": (
            "Could not select the correct Admission History confinement period."
        ),
        "needs_review_phic_beneficiaries": (
            "Could not select the correct PhilHealth Beneficiaries confinement period."
        ),
        "SKIPPED_DATES_COMPLETE": (
            "All required HBSys dates already match the expected fill date."
        ),
    }
    if status.startswith("error:"):
        return status
    return mapping.get(status, status)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill HBSys CF2 dates from selected claim folders.")
    parser.add_argument("--ready-dir", type=Path, default=None)
    parser.add_argument("--live", action="store_true", help="Actually click/type in HBSys.")
    parser.add_argument("--limit", type=int, help="Limit number of claims to process.")
    parser.add_argument("--hospital-no", help="Process one exact hospital number.")
    parser.add_argument("--pause", type=float, default=0.35)
    parser.add_argument(
        "--confirm-each",
        action="store_true",
        help="Ask before every patient. Recommended for first live run.",
    )
    parser.add_argument(
        "--no-precheck",
        action="store_true",
        help=(
            "Disable the read-only skip-if-dates-complete pre-check and "
            "process every claim as before."
        ),
    )
    args = parser.parse_args()

    source_dir = args.ready_dir or DEFAULT_READY_DIR
    claims = load_ready_claims(source_dir)
    if args.hospital_no:
        claims = [claim for claim in claims if claim.hospital_no == args.hospital_no]
    if args.limit:
        claims = claims[: args.limit]

    if not claims:
        message = f"No claims to process in source folder:\n{source_dir}"
        print(message)
        show_popup("Date Fill", message, error=False)
        return 0

    operator = HbsysOperator(live=args.live, pause=args.pause, confirm_each=args.confirm_each)
    verifier = HbsysDateFillVerifier()
    results: list[dict[str, str]] = []

    print(f"Mode: {'LIVE' if args.live else 'DRY-RUN'}")
    print(f"Source folder: {source_dir}")
    print(f"Claims: {len(claims)}")
    if not args.live:
        print("Dry-run only. Add --live to actually click/type in HBSys.")

    for claim in claims:
        precheck_fields: dict[str, str] = {}

        if not args.no_precheck:
            pre_result: PrecheckResult | None
            try:
                pre_result = precheck_claim(
                    verifier,
                    claim.hospital_no,
                    datetime.strptime(claim.admission_hbsys, "%m-%d-%Y").date(),
                    datetime.strptime(claim.discharge_hbsys, "%m-%d-%Y").date(),
                    "REGULAR",
                )
            except Exception as exc:  # noqa: BLE001 - fall back to normal flow.
                pre_result = PrecheckResult(
                    PRECHECK_PROCESS,
                    reason=f"precheck error, falling back to full flow: {exc}",
                )
            precheck_fields["precheck"] = pre_result.decision
            precheck_fields["precheck_missing"] = "|".join(pre_result.missing_fields)
            precheck_fields["precheck_reason"] = pre_result.reason

            if pre_result.skipped:
                status = "SKIPPED_DATES_COMPLETE"
                print(
                    f"[SKIP] {claim.patient_name}: dates already complete — "
                    "no clicks needed"
                )
                results.append(
                    {
                        "patient_name": claim.patient_name,
                        "hospital_no": claim.hospital_no,
                        "admission": claim.admission_hbsys,
                        "discharge": claim.discharge_hbsys,
                        **build_cross_check_fields(claim),
                        **precheck_fields,
                        "status": status,
                        "safe_reset_confirmed": "",
                    }
                )
                continue
        else:
            precheck_fields["precheck"] = "DISABLED"

        try:
            status = operator.process_claim(claim)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - operator log should continue.
            status = f"error: {exc}"
            operator.log_action(status)

        results.append(
            {
                "patient_name": claim.patient_name,
                "hospital_no": claim.hospital_no,
                "admission": claim.admission_hbsys,
                "discharge": claim.discharge_hbsys,
                **build_cross_check_fields(claim),
                **precheck_fields,
                "status": status,
                "safe_reset_confirmed": "",
            }
        )

        if status != "done":
            safe_reset = operator.reset_to_safe_start()
            results[-1]["safe_reset_confirmed"] = "YES" if safe_reset else "NO"
            if not safe_reset:
                break
            continue

    log_path = write_run_log(results)
    print(f"Run log: {log_path.resolve()}")
    skipped_complete_count = sum(
        1 for row in results if row.get("status") == "SKIPPED_DATES_COMPLETE"
    )
    failed_rows = [
        row
        for row in results
        if row.get("status") not in ("done", "SKIPPED_DATES_COMPLETE")
    ]
    if failed_rows:
        failed = failed_rows[-1]
        stop_message = (
            "Date Fill stopped before completion.\n\n"
            f"Patient: {failed.get('patient_name', '')}\n"
            f"Hospital No.: {failed.get('hospital_no', '')}\n"
            f"Admission: {failed.get('admission', '')}\n"
            f"Discharge: {failed.get('discharge', '')}\n\n"
            f"Reason:\n{describe_stop_status(failed.get('status', ''))}\n\n"
            f"CSV log saved here:\n{log_path.resolve()}\n\n"
            "Please review the current HBSys screen before running Date Fill again."
        )
        print(
            "Date Fill stopped before completion. CSV was saved but not opened "
            "automatically so you can review HBSys first."
        )
        show_popup("Date Fill Stopped", stop_message, error=True)
        return 1

    open_run_log(log_path)
    show_popup(
        "Date Fill Complete",
        (
            "Date Fill completed successfully.\n\n"
            f"Processed: {len(results) - skipped_complete_count}\n"
            f"Skipped (dates already complete): {skipped_complete_count}\n\n"
            f"CSV log:\n{log_path.resolve()}"
        ),
        error=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
