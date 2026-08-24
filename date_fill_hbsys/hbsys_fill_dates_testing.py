from __future__ import annotations

import argparse
import csv
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pyautogui
from PIL import Image
from pywinauto import Desktop

from hbsys_read_admission_history_testing import (
    DATE_RE,
    OcrItem,
    find_admission_history_window,
    parse_rows_with_positions,
    read_focused_admission_row_variants,
    read_ocr_item_variants,
    read_ocr_items,
)
from hbsys_ready_claims import (
    DEFAULT_READY_DIR,
    ReadyClaim,
    load_ready_claims,
    parse_ready_claim_folder,
)
from hbsys_date_fill_verifier import (
    ABTC_ACCREDITATION_NO,
    HbsysDateFillVerifier,
    PatientDateSnapshot,
    VerificationError,
)
from hbsys_window import find_hbsys_window

LOG_DIR = Path("logs") / "testing"
RUN_LABEL = "Date Fill Testing"
RUN_LOG_PREFIX = "hbsys_fill_testing_run"

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
    CLOSE_FORM_BENEFICIARIES_WITH_DETAILS = Point(485, 58)
    CLOSE_FORM_BENEFICIARIES_LEGACY = Point(435, 58)
    TAB_PROF_FEES = Point(619, 132)
    TAB_CONSENT = Point(872, 132)
    PROF_DATE_SIGNED_CELL = Point(940, 179)
    CONSENT_DATE_SIGNED = Point(153, 300)
    CONSENT_CERT_DATE = Point(770, 598)


def sleep_short(seconds: float = 0.35) -> None:
    time.sleep(seconds)


class HbsysOperator:
    def __init__(
        self,
        live: bool,
        pause: float,
        confirm_each: bool,
        claim_type: str = "REGULAR",
        enable_abtc: bool = False,
    ):
        self.live = live
        self.pause = pause
        self.confirm_each = confirm_each
        self.claim_type = str(claim_type or "REGULAR").strip().upper()
        if self.claim_type not in {"REGULAR", "ABTC"}:
            raise ValueError("Claim Type must be Regular or ABTC")
        if self.claim_type == "ABTC" and not enable_abtc:
            raise ValueError("ABTC is enabled only in Date Fill Testing")
        self.enable_abtc = enable_abtc
        self.hbsys_window = None
        self.verifier = HbsysDateFillVerifier()
        self.before_snapshot: PatientDateSnapshot | None = None
        self.audit: dict[str, str] = {}
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

    def dismiss_rate_validation_message(self, timeout: float = 2.5) -> bool:
        """Dismiss only the known informational dialog after encounter selection."""
        if not self.live:
            return True
        deadline = time.time() + timeout
        while time.time() < deadline:
            for window in Desktop(backend="win32").windows():
                try:
                    if window.window_text().strip() != "Rate validation":
                        continue
                    text_blob = " ".join(
                        child.window_text() for child in window.descendants()
                    )
                except Exception:
                    continue
                if "Rates no longer exist" not in text_blob:
                    self.log_action(
                        f"unknown Rate validation dialog was not dismissed: {text_blob!r}"
                    )
                    return False
                self.log_action("dismiss known Rate validation information dialog")
                try:
                    window.set_focus()
                    window.child_window(title="OK", class_name="Button").click()
                except Exception:
                    pyautogui.press("enter")
                sleep_short(0.4)
                return True
            time.sleep(0.1)
        return True

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

    def select_admission_history_row(
        self,
        claim: ReadyClaim,
        expected_encounter_types: set[str] | None = None,
    ) -> bool:
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
        item_variants = read_ocr_item_variants(image_path)
        focused_variants = read_focused_admission_row_variants(image_path)
        item_variants.extend(focused_variants)
        if focused_variants:
            self.log_action(
                f"Admission History added {len(focused_variants)} focused row OCR passes"
            )
        parsed_variants = [
            parse_rows_with_positions(items) for items in item_variants
        ]
        rows = max(parsed_variants, key=len, default=[])
        if rows:
            detected = "; ".join(
                f"{parsed.row.admission_date}-{parsed.row.discharge_date}"
                for parsed in rows
            )
            self.log_action(f"Admission History rows detected: {detected}")
        else:
            self.log_action("Admission History OCR found no date rows.")
        self.audit["admission_history_screenshot"] = str(image_path.resolve())

        row_y = self.find_exact_admission_history_row_y(
            item_variants,
            claim.admission_grid,
            claim.discharge_grid,
            expected_encounter_types=expected_encounter_types,
            match_admission_only=self.claim_type == "ABTC",
        )
        if row_y is not None:
            selected_row = next(
                (
                    parsed.row
                    for parsed in rows
                    if self._admission_history_row_matches(
                        parsed,
                        claim.admission_grid,
                        claim.discharge_grid,
                        expected_encounter_types,
                        match_admission_only=self.claim_type == "ABTC",
                    )
                    and abs(parsed.y - row_y) <= 12
                ),
                None,
            )
            rect = window.rectangle()
            click_x = rect.left + 72
            click_y = rect.top + int(row_y)
            self.log_action(
                "select database-confirmed Admission History row "
                f"{selected_row.admission_date if selected_row else claim.admission_grid}-"
                f"{selected_row.discharge_date if selected_row else claim.discharge_grid} "
                f"at ({click_x}, {click_y})"
            )
            pyautogui.doubleClick(click_x, click_y)
            sleep_short(0.8)
            if not self.dismiss_rate_validation_message():
                self.log_action("Admission History selection left an unknown dialog")
                return False
            self.audit["selected_admission"] = (
                selected_row.admission_date
                if selected_row
                else claim.admission_grid
            )
            self.audit["selected_discharge"] = (
                selected_row.discharge_date
                if selected_row
                else (
                    claim.admission_grid
                    if self.claim_type == "ABTC"
                    else claim.discharge_grid
                )
            )
            self.audit["admission_history_match"] = "MATCH"
            self.audit["admission_history_screenshot"] = str(image_path.resolve())
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

    def find_exact_admission_history_row_y(
        self,
        item_variants: list[list[OcrItem]],
        admission_date: str,
        discharge_date: str,
        expected_encounter_types: set[str] | None = None,
        *,
        match_admission_only: bool = False,
    ) -> float | None:
        """Require a unique exact confinement row across multiple OCR passes."""
        allowed_types = {
            value.upper()
            for value in (expected_encounter_types or {"ADMIT"})
        }
        matches: list[float] = []
        for variant_number, items in enumerate(item_variants, start=1):
            rows = parse_rows_with_positions(items)
            exact_admit_rows = [
                parsed
                for parsed in rows
                if self._admission_history_row_matches(
                    parsed,
                    admission_date,
                    discharge_date,
                    allowed_types,
                    match_admission_only=match_admission_only,
                )
            ]
            if len(exact_admit_rows) != 1:
                continue
            row_y = exact_admit_rows[0].y
            matches.append(row_y)
            self.log_action(
                f"Admission History OCR variant {variant_number} matched "
                f"exact row y={row_y:.1f}"
            )

        clusters: list[list[float]] = []
        for row_y in sorted(matches):
            for cluster in clusters:
                if abs(sum(cluster) / len(cluster) - row_y) <= 12:
                    cluster.append(row_y)
                    break
            else:
                clusters.append([row_y])
        clusters.sort(key=lambda values: (-len(values), sum(values) / len(values)))
        if not clusters or len(clusters[0]) < 2:
            self.log_action(
                "Admission History needs at least two OCR passes agreeing on "
                "the exact confinement row and encounter type"
            )
            return None
        if len(clusters) > 1 and len(clusters[0]) == len(clusters[1]):
            self.log_action("Admission History OCR row position is ambiguous")
            return None
        return sum(clusters[0]) / len(clusters[0])

    @staticmethod
    def _admission_history_row_matches(
        parsed,
        admission_date: str,
        discharge_date: str,
        expected_encounter_types: set[str] | None,
        *,
        match_admission_only: bool,
    ) -> bool:
        """Match the HBSys grid without confusing OPD time with claim period.

        HBSys ABTC screens display an OPD consultation date that can differ
        from the folder's claim discharge date. The exact ABTC encounter is
        already resolved read-only before UI selection, so the visual row uses
        admission + OPD/OPDAD; PHIC additionally requires patient name and the
        ABTC accreditation number.
        """
        allowed_types = {
            value.upper()
            for value in (expected_encounter_types or {"ADMIT"})
        }
        row = parsed.row
        discharge_matches = (
            True
            if match_admission_only
            else row.discharge_date == discharge_date
        )
        return (
            row.admission_date == admission_date
            and discharge_matches
            and row.normalized_encounter_type in allowed_types
        )

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
        abtc_mode = self.claim_type == "ABTC"
        row_y = self.find_phic_beneficiary_row_y_from_variants(
            read_ocr_item_variants(image_path),
            claim.admission_grid,
            claim.discharge_grid,
            claim.patient_name,
            strict=abtc_mode,
            minimum_consensus=2,
            match_admission_only=abtc_mode,
            required_accreditation=(
                ABTC_ACCREDITATION_NO if abtc_mode else ""
            ),
        )
        rect = self.hbsys_window.rectangle()

        if row_y is not None:
            click_y = rect.top + int(round(row_y))
            for attempt, x_offset in enumerate((260, 520), start=1):
                click_x = rect.left + x_offset
                self.log_action(
                    "single-click PhilHealth Beneficiaries row "
                    f"{claim.admission_grid}-{claim.discharge_grid} "
                    f"attempt {attempt} at ({click_x}, {click_y})"
                )
                pyautogui.click(click_x, click_y)
                sleep_short(0.6)
                proof_path = self.capture_window(
                    self.hbsys_window,
                    f"phic_beneficiaries_selected_proof_{attempt}",
                )
                proof_y = self.find_phic_beneficiary_row_y_from_variants(
                    read_ocr_item_variants(proof_path),
                    claim.admission_grid,
                    claim.discharge_grid,
                    claim.patient_name,
                    strict=abtc_mode,
                    minimum_consensus=2,
                    match_admission_only=abtc_mode,
                    required_accreditation=(
                        ABTC_ACCREDITATION_NO if abtc_mode else ""
                    ),
                )
                highlighted = (
                    proof_y is not None
                    and self.is_blue_highlighted_row(proof_path, proof_y)
                )
                self.audit["phic_selected_admission"] = (
                    claim.admission_grid if proof_y else ""
                )
                self.audit["phic_selected_discharge"] = (
                    ("IGNORED_ABTC" if abtc_mode else claim.discharge_grid)
                    if proof_y
                    else ""
                )
                self.audit["phic_highlighted_row_match"] = (
                    "MATCH" if highlighted else "NOT MATCH"
                )
                self.audit["phic_selection_screenshot"] = str(proof_path.resolve())
                if highlighted:
                    return True
            return False

        self.log_action(
            "matching PhilHealth Beneficiaries row not found; stopping for review"
        )
        return False

    def find_phic_beneficiary_row_y_from_variants(
        self,
        item_variants: list[list[OcrItem]],
        admission_date: str,
        discharge_date: str,
        patient_name: str = "",
        strict: bool = False,
        minimum_consensus: int = 1,
        match_admission_only: bool = False,
        required_accreditation: str = "",
    ) -> float | None:
        """Select a PHIC row using all OCR passes and row-position consensus."""
        independent_abtc_proof = bool(required_accreditation)
        row_matches: list[float] = []
        for variant_number, items in enumerate(item_variants, start=1):
            row_y = self.find_phic_beneficiary_row_y(
                items,
                admission_date,
                discharge_date,
                "" if independent_abtc_proof else patient_name,
                strict=False if independent_abtc_proof else strict,
                match_admission_only=match_admission_only,
                # Date/name and accreditation often become legible in
                # different OCR passes. Prove them independently below, then
                # require both proofs to point to the same physical row.
                required_accreditation="",
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
        date_consensus_required = (
            1 if independent_abtc_proof else minimum_consensus
        )
        if len(best_cluster) < date_consensus_required:
            self.log_action(
                "PHIC OCR did not reach the required row-position consensus: "
                f"{len(best_cluster)}/{date_consensus_required} pass(es)"
            )
            return None
        if len(clusters) > 1 and len(best_cluster) == len(clusters[1]):
            self.log_action(
                "PHIC OCR variants matched different rows with equal confidence; "
                "stopping for review"
            )
            return None

        selected_y = sum(best_cluster) / len(best_cluster)
        if required_accreditation:
            name_matches = [
                row_y
                for items in item_variants
                if (
                    row_y := self.find_phic_name_row_y(
                        items,
                        patient_name,
                    )
                )
                is not None
            ]
            name_y = self.consensus_row_position(
                name_matches,
                minimum_consensus,
            )
            if name_y is None:
                self.log_action(
                    "PHIC patient name did not reach the required "
                    "row-position consensus"
                )
                return None
            if abs(name_y - selected_y) > 12:
                self.log_action(
                    "PHIC admission and patient-name proofs point to "
                    "different rows; stopping for review"
                )
                return None

            accreditation_matches = [
                row_y
                for items in item_variants
                if (
                    row_y := self.find_phic_accreditation_row_y(
                        items,
                        required_accreditation,
                    )
                )
                is not None
            ]
            accreditation_y = self.consensus_row_position(
                accreditation_matches,
                minimum_consensus,
            )
            if accreditation_y is None:
                self.log_action(
                    "PHIC ABTC accreditation did not reach the required "
                    f"row-position consensus for {required_accreditation}"
                )
                return None
            if abs(accreditation_y - selected_y) > 12:
                self.log_action(
                    "PHIC admission/name and ABTC accreditation proofs point "
                    "to different rows; stopping for review"
                )
                return None
            self.log_action(
                f"PHIC ABTC accreditation {required_accreditation} verified "
                f"on the same row y={accreditation_y:.1f}"
            )
        self.log_action(
            "PHIC OCR consensus selected row "
            f"y={selected_y:.1f} from {len(best_cluster)} matching pass(es)"
        )
        return selected_y

    def find_phic_name_row_y(
        self,
        items: list[OcrItem],
        patient_name: str,
    ) -> float | None:
        """Find one row carrying the patient's name."""
        if not self.name_match_tokens(patient_name):
            return None
        relevant_items = [
            item
            for item in items
            if 130 <= item.y <= 380
            and item.confidence >= 0.15
            and item.text
        ]
        rows: list[list[OcrItem]] = []
        for item in sorted(relevant_items, key=lambda value: value.y):
            for row in rows:
                if abs(row[0].y - item.y) <= 10:
                    row.append(item)
                    break
            else:
                rows.append([item])
        matches: list[float] = []
        for row in rows:
            row_text = " ".join(item.text for item in row)
            if any(
                token in self.normalize_for_name_match(row_text)
                for token in self.name_match_tokens(patient_name)
            ):
                matches.append(sum(item.y for item in row) / len(row))
        if len(matches) != 1:
            return None
        return matches[0]

    @staticmethod
    def consensus_row_position(
        positions: list[float],
        minimum_consensus: int,
    ) -> float | None:
        """Return one unambiguous row center supported by enough OCR passes."""
        clusters: list[list[float]] = []
        for row_y in sorted(positions):
            for cluster in clusters:
                if abs(sum(cluster) / len(cluster) - row_y) <= 12:
                    cluster.append(row_y)
                    break
            else:
                clusters.append([row_y])
        clusters.sort(
            key=lambda cluster: (
                -len(cluster),
                sum(cluster) / len(cluster),
            )
        )
        if not clusters or len(clusters[0]) < minimum_consensus:
            return None
        if len(clusters) > 1 and len(clusters[0]) == len(clusters[1]):
            return None
        return sum(clusters[0]) / len(clusters[0])

    @staticmethod
    def find_phic_accreditation_row_y(
        items: list[OcrItem],
        accreditation: str,
    ) -> float | None:
        """Find one exact accreditation token without trusting fuzzy OCR."""
        expected = re.sub(r"[^A-Z0-9]", "", accreditation.upper())
        matches = [
            item.y
            for item in items
            if 130 <= item.y <= 380
            and item.confidence >= 0.15
            and expected
            in re.sub(r"[^A-Z0-9]", "", item.text.upper())
        ]
        if not matches:
            return None
        clusters: list[list[float]] = []
        for row_y in sorted(matches):
            for cluster in clusters:
                if abs(sum(cluster) / len(cluster) - row_y) <= 10:
                    cluster.append(row_y)
                    break
            else:
                clusters.append([row_y])
        if len(clusters) != 1:
            return None
        return sum(clusters[0]) / len(clusters[0])

    def find_phic_beneficiary_row_y(
        self,
        items: list[OcrItem],
        admission_date: str,
        discharge_date: str,
        patient_name: str = "",
        strict: bool = False,
        match_admission_only: bool = False,
        required_accreditation: str = "",
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
        normalized_accreditation = re.sub(
            r"[^A-Z0-9]",
            "",
            required_accreditation.upper(),
        )
        for row in rows:
            row.sort(key=lambda item: item.x)
            dates = [match.group(0) for item in row for match in DATE_RE.finditer(item.text)]
            row_text = " ".join(item.text for item in row)
            row_y = sum(item.y for item in row) / len(row)
            compact_row_text = re.sub(r"[^A-Z0-9]", "", row_text.upper())
            accreditation_matches = (
                not normalized_accreditation
                or normalized_accreditation in compact_row_text
            )
            discharge_matches = (
                True if match_admission_only else discharge_date in dates
            )
            if (
                admission_date in dates
                and discharge_matches
                and accreditation_matches
            ):
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
            if strict:
                return None
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
        required_name_tokens = min(2, len(name_tokens))

        if best_score < required_name_tokens or best_score == second_score:
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
    def is_blue_highlighted_row(image_path: Path, row_y: float) -> bool:
        """Confirm that the exact OCR row is the Windows blue selected row."""
        with Image.open(image_path).convert("RGB") as image:
            y1 = max(0, int(row_y) - 7)
            y2 = min(image.height, int(row_y) + 8)
            x2 = min(image.width, 1580)
            pixels = list(image.crop((5, y1, x2, y2)).getdata())
        if not pixels:
            return False
        blue_pixels = sum(
            1
            for red, green, blue in pixels
            if blue >= 120 and blue > red * 1.25 and blue > green * 1.15
        )
        return blue_pixels / len(pixels) >= 0.08

    @staticmethod
    def normalize_for_name_match(value: str) -> str:
        return re.sub(r"[^A-Z0-9]+", " ", value.upper()).strip()

    def name_match_tokens(self, patient_name: str) -> list[str]:
        normalized = self.normalize_for_name_match(patient_name)
        tokens = [token for token in normalized.split() if len(token) >= 3]
        return tokens

    def open_claim_form_2(self) -> None:
        self.click(P.CLAIM_FORM_2, "Claim Form 2")
        sleep_short(1.2)

    def fill_professional_fee_date(self, fill_date_hbsys: str) -> bool:
        self.click(P.TAB_PROF_FEES, "Professional Fees / Charges tab")
        self.click(P.EDIT, "Edit")
        self.click(P.PROF_DATE_SIGNED_CELL, "Professional Fee Date Signed")
        self.hotkey("ctrl", "a")
        self.write(fill_date_hbsys)
        self.press("enter")
        self.click(P.SAVE, "Save Professional Fee")
        saved = self.dismiss_phic_message("Professional Fee save")
        self.audit["professional_fee_save_confirmed"] = "YES" if saved else "NO"
        sleep_short(0.8)
        return saved

    def fill_consent_dates(self, fill_date_hbsys: str) -> bool:
        self.click(P.TAB_CONSENT, "Consent tab")
        self.click(P.EDIT, "Edit Consent")
        self.click(P.CONSENT_DATE_SIGNED, "Consent Date Signed")
        self.hotkey("ctrl", "a")
        self.write(fill_date_hbsys)
        self.click(P.CONSENT_CERT_DATE, "Certification Date")
        self.hotkey("ctrl", "a")
        self.write(fill_date_hbsys)
        self.click(P.SAVE, "Save Consent")
        saved = self.dismiss_phic_message("Consent save")
        self.audit["consent_save_confirmed"] = "YES" if saved else "NO"
        sleep_short(0.8)
        return saved

    def close_claim_forms(self) -> None:
        self.click(P.CLOSE_FORM_CF2, "Close Claim Form 2")
        sleep_short(0.8)
        self.close_phic_beneficiaries("Close PhilHealth Beneficiaries")
        sleep_short(0.8)
        self.screen_stage = "base"

    def beneficiaries_close_point(self) -> Point:
        """Close Form toolbar slot for the current layout that includes Details."""
        return P.CLOSE_FORM_BENEFICIARIES_WITH_DETAILS

    def beneficiaries_alternate_close_point(self) -> Point:
        """Legacy Close Form slot used when the toolbar has no Details button."""
        return P.CLOSE_FORM_BENEFICIARIES_LEGACY

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
        self.audit["phic_details_dismissed"] = "YES"
        return True

    def close_phic_beneficiaries(self, label: str) -> None:
        self.cancel_pending_beneficiary_edit_if_visible()
        for point, slot_label in (
            (self.beneficiaries_close_point(), "with Details"),
            (self.beneficiaries_alternate_close_point(), "legacy"),
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
        self.audit["phic_pending_edit_cancelled"] = "YES"
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
                self.audit["phic_claim_form4_dismissed"] = "YES"
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

        self.audit["phic_claim_form4_dismissed"] = "NO"
        self.log_action(
            "PHIC Beneficiaries still shows a Cancel button after dismissal; "
            "stopping for review"
        )
        return False

    def reset_to_safe_start(self) -> bool:
        """Best-effort close of the known Date Fill screens, with verification."""
        if not self.live:
            self.audit["safe_reset_confirmed"] = "YES"
            self.screen_stage = "base"
            return True
        if self.screen_stage == "base":
            self.audit["safe_reset_confirmed"] = "YES"
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

            if not self.dismiss_rate_validation_message(timeout=1.5):
                self.audit["safe_reset_confirmed"] = "NO"
                return False

            if self.screen_stage == "cf2":
                self.click(P.CLOSE_FORM_CF2, "Close Claim Form 2 after failure")
                self.screen_stage = "beneficiary"
            if self.screen_stage == "beneficiary":
                self.close_phic_beneficiaries(
                    "Close PhilHealth Beneficiaries after failure"
                )
            sleep_short(0.5)
            if self.hbsys_window is None:
                self.audit["safe_reset_confirmed"] = "NO"
                return False
            proof_path = self.capture_window(self.hbsys_window, "safe_reset_proof")
            proof_text = " ".join(
                item.text.upper() for item in read_ocr_items(proof_path)
            )
            safe_text = self.is_safe_reset_text(proof_text)
            safe = (
                find_admission_history_window() is None
                and safe_text
            )
            self.audit["safe_reset_screenshot"] = str(proof_path.resolve())
            self.screen_stage = "base" if safe else self.screen_stage
            self.audit["safe_reset_confirmed"] = "YES" if safe else "NO"
            return safe
        except Exception as exc:  # noqa: BLE001 - reset failure must be audited.
            self.log_action(f"safe reset failed: {exc}")
            self.audit["safe_reset_confirmed"] = "NO"
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

    @staticmethod
    def claim_dates(claim: ReadyClaim) -> tuple[date, date]:
        return (
            datetime.strptime(claim.admission_hbsys, "%m-%d-%Y").date(),
            datetime.strptime(claim.discharge_hbsys, "%m-%d-%Y").date(),
        )

    def verify_database_after_save(self, expected_fill_date: date):
        if self.before_snapshot is None:
            raise RuntimeError("Pre-save HBSys snapshot is missing.")
        proof = None
        for attempt in range(8):
            proof = self.verifier.verify_after_save(
                expected_fill_date,
                self.before_snapshot,
            )
            if proof.verified or proof.critical_wrong_encounter:
                break
            if attempt < 7:
                time.sleep(1.0)
        assert proof is not None
        self.audit["professional_fee_db_date"] = proof.professional_date
        self.audit["consent_db_date"] = proof.consent_date
        self.audit["authorization_db_date"] = proof.authorization_date
        self.audit["other_encounter_changed"] = (
            "YES" if proof.other_encounter_changed else "NO"
        )
        self.audit["changed_other_enccodes"] = "|".join(
            proof.changed_other_enccodes
        )
        self.audit["database_verification_reason"] = proof.reason
        return proof

    def process_claim(self, claim: ReadyClaim) -> str:
        self.audit = {}
        self.before_snapshot = None
        self.screen_stage = "base"
        self.log_action(
            f"processing {claim.patient_name} | {claim.hospital_no} | "
            f"ADM {claim.admission_hbsys} DIS {claim.discharge_hbsys}"
        )

        if self.confirm_each:
            input("Press Enter to process this claim, or Ctrl+C to stop...")

        admission, discharge = self.claim_dates(claim)
        try:
            identity = self.verifier.resolve_exact_encounter(
                claim.hospital_no,
                admission,
                discharge,
                self.claim_type,
            )
            self.before_snapshot = self.verifier.capture_patient_snapshot(
                claim.hospital_no,
                identity.enccode,
                identity.claim_type,
            )
        except VerificationError as exc:
            self.audit["database_verification_reason"] = str(exc)
            return "SKIPPED_NO_EXACT_ENCOUNTER"
        self.audit["matched_enccode"] = identity.enccode
        self.audit["claim_type"] = identity.claim_type
        self.audit["encounter_type"] = identity.encounter_type
        self.audit["date_basis"] = identity.date_basis
        self.audit["target_fill_date"] = identity.target_date.isoformat()
        expected_before = self.before_snapshot.encounters.get(identity.enccode)
        self.audit["expected_encounter_before"] = (
            expected_before.as_text() if expected_before else "NO RECORDS"
        )

        if not self.live:
            self.log_action("dry-run input encounter verified; no dates were written")
            return "DRY_RUN_VERIFIED_INPUT"

        self.focus_hbsys()
        self.search_hospital_number(claim)
        expected_types = (
            {"OPD", "OPDAD"}
            if identity.claim_type == "ABTC"
            else {"ADMIT"}
        )
        if not self.select_admission_history_row(claim, expected_types):
            return "SKIPPED_ADMISSION_HISTORY_MISMATCH"

        if not self.click_phic_and_select_claim(claim):
            if self.audit.get("phic_claim_form4_dismissed") == "NO":
                return "SKIPPED_PHIC_CANCEL_STUCK"
            return "SKIPPED_SELECTED_ROW_MISMATCH"
        self.open_claim_form_2()
        self.screen_stage = "cf2"
        fill_date_hbsys = identity.target_date.strftime("%m-%d-%Y")
        if not self.fill_professional_fee_date(fill_date_hbsys):
            return "SKIPPED_PROFESSIONAL_SAVE_UNCONFIRMED"
        if not self.fill_consent_dates(fill_date_hbsys):
            return "SKIPPED_CONSENT_SAVE_UNCONFIRMED"

        proof = self.verify_database_after_save(identity.target_date)
        if proof.critical_wrong_encounter:
            return "CRITICAL_WRONG_ENCOUNTER"
        if not proof.verified:
            return "SKIPPED_POST_SAVE_DATABASE_MISMATCH"
        self.close_claim_forms()
        return "VERIFIED"


def write_run_log(rows: list[dict[str, str]]) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{RUN_LOG_PREFIX}_{datetime.now():%Y%m%d_%H%M%S}.csv"
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "patient_name",
                "hospital_no",
                "admission",
                "discharge",
                "claim_type",
                "encounter_type",
                "date_basis",
                "target_fill_date",
                "output_folder_name",
                "output_folder_patient_name",
                "selected_patient_name",
                "patient_name_match",
                "output_folder_discharge",
                "entered_discharge",
                "discharge_date_match",
                "matched_enccode",
                "expected_encounter_before",
                "selected_admission",
                "selected_discharge",
                "admission_history_match",
                "phic_selected_admission",
                "phic_selected_discharge",
                "phic_highlighted_row_match",
                "professional_fee_save_confirmed",
                "consent_save_confirmed",
                "professional_fee_db_date",
                "consent_db_date",
                "authorization_db_date",
                "other_encounter_changed",
                "changed_other_enccodes",
                "database_verification_reason",
                "admission_history_screenshot",
                "phic_selection_screenshot",
                "phic_pending_edit_cancelled",
                "phic_claim_form4_dismissed",
                "safe_reset_screenshot",
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


def partition_claims_for_type(
    claims: list[ReadyClaim],
    verifier: HbsysDateFillVerifier,
    claim_type: str,
) -> tuple[list[ReadyClaim], list[ReadyClaim]]:
    """Keep requested-type folders and silently separate the opposite type."""
    requested = claim_type.upper()
    opposite = "ABTC" if requested == "REGULAR" else "REGULAR"
    selected: list[ReadyClaim] = []
    ignored: list[ReadyClaim] = []
    for claim in claims:
        admission = claim.admission_date.date()
        discharge = claim.discharge_date.date()
        try:
            verifier.resolve_exact_encounter(
                claim.hospital_no,
                admission,
                discharge,
                requested,
            )
            selected.append(claim)
            continue
        except VerificationError:
            pass
        try:
            verifier.resolve_exact_encounter(
                claim.hospital_no,
                admission,
                discharge,
                opposite,
            )
            ignored.append(claim)
        except VerificationError:
            # Keep unresolved folders in the requested run so they receive a
            # visible audited failure instead of disappearing silently.
            selected.append(claim)
    return selected, ignored


def describe_stop_status(status: str) -> str:
    mapping = {
        "SKIPPED_ADMISSION_HISTORY_MISMATCH": (
            "Could not select the correct Admission History confinement period."
        ),
        "SKIPPED_SELECTED_ROW_MISMATCH": (
            "The exact PhilHealth Beneficiaries row was not confirmed as selected."
        ),
        "SKIPPED_NO_EXACT_ENCOUNTER": "No unique exact HBSys encounter was found.",
        "SKIPPED_PROFESSIONAL_SAVE_UNCONFIRMED": (
            "Professional Fee save confirmation was not detected."
        ),
        "SKIPPED_CONSENT_SAVE_UNCONFIRMED": (
            "Consent save confirmation was not detected."
        ),
        "SKIPPED_POST_SAVE_DATABASE_MISMATCH": (
            "Saved HBSys dates did not match the expected encounter."
        ),
        "CRITICAL_WRONG_ENCOUNTER": "Another confinement record changed.",
    }
    if status.startswith("error:"):
        return status
    return mapping.get(status, status)


def main() -> int:
    global LOG_DIR, RUN_LABEL, RUN_LOG_PREFIX

    parser = argparse.ArgumentParser(description="Fill HBSys CF2 dates from selected claim folders.")
    parser.add_argument("--ready-dir", type=Path, default=None)
    parser.add_argument("--live", action="store_true", help="Actually click/type in HBSys.")
    parser.add_argument("--limit", type=int, help="Limit number of claims to process.")
    parser.add_argument("--hospital-no", help="Process one exact hospital number.")
    parser.add_argument("--pause", type=float, default=0.35)
    parser.add_argument(
        "--production-mode",
        action="store_true",
        help="Use production Date Fill labels and production log folder.",
    )
    parser.add_argument(
        "--claim-type",
        choices=("REGULAR", "ABTC"),
        default="REGULAR",
        help="Process only the selected HBSys claim type.",
    )
    parser.add_argument(
        "--enable-abtc",
        action="store_true",
        help="Explicit testing-only gate required for ABTC processing.",
    )
    parser.add_argument(
        "--confirm-each",
        action="store_true",
        help="Ask before every patient. Recommended for first live run.",
    )
    args = parser.parse_args()
    if args.production_mode:
        LOG_DIR = Path("logs")
        RUN_LABEL = "Date Fill"
        RUN_LOG_PREFIX = "hbsys_fill_run"
    if args.claim_type == "ABTC" and not args.enable_abtc:
        message = "ABTC Date Fill is available only through Date Fill Testing."
        print(message)
        show_popup(RUN_LABEL, message, error=True)
        return 2

    source_dir = args.ready_dir or DEFAULT_READY_DIR
    claims = load_ready_claims(source_dir)
    if args.hospital_no:
        claims = [claim for claim in claims if claim.hospital_no == args.hospital_no]
    if args.limit:
        claims = claims[: args.limit]

    classifier = HbsysDateFillVerifier()
    claims, ignored_claims = partition_claims_for_type(
        claims,
        classifier,
        args.claim_type,
    )

    if not claims:
        message = f"No claims to process in source folder:\n{source_dir}"
        print(message)
        show_popup(RUN_LABEL, message, error=False)
        return 0

    operator = HbsysOperator(
        live=args.live,
        pause=args.pause,
        confirm_each=args.confirm_each,
        claim_type=args.claim_type,
        enable_abtc=args.enable_abtc,
    )
    results: list[dict[str, str]] = []
    batch_stopped = False

    print(f"Mode: {'LIVE' if args.live else 'DRY-RUN'}")
    print(f"Source folder: {source_dir}")
    print(f"Claim Type: {args.claim_type}")
    print(f"Claims: {len(claims)}")
    print(f"Opposite-type folders ignored: {len(ignored_claims)}")
    if not args.live:
        print("Dry-run only. Add --live to actually click/type in HBSys.")

    for claim in claims:
        operator.audit = {}
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
                **operator.audit,
                "status": status,
            }
        )

        if status != "VERIFIED":
            safe_reset = operator.reset_to_safe_start()
            results[-1]["safe_reset_confirmed"] = operator.audit.get(
                "safe_reset_confirmed", "NO"
            )
            if status == "CRITICAL_WRONG_ENCOUNTER" or not safe_reset:
                batch_stopped = True
                break
            continue

    log_path = write_run_log(results)
    print(f"Run log: {log_path.resolve()}")
    failed_rows = [
        row
        for row in results
        if row.get("status") != "VERIFIED"
    ]
    if batch_stopped:
        failed = results[-1]
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
        show_popup(f"{RUN_LABEL} Stopped", stop_message, error=True)
        return 1

    open_run_log(log_path)
    if failed_rows:
        message = (
            "Date Fill finished, but some patients were safely skipped.\n\n"
            f"Verified: {len(results) - len(failed_rows)}\n"
            f"Skipped: {len(failed_rows)}\n\n"
            f"CSV log:\n{log_path.resolve()}"
        )
        show_popup(
            f"{RUN_LABEL} Complete with Skipped Patients",
            message,
            error=True,
        )
        return 1

    show_popup(
        f"{RUN_LABEL} Complete",
        f"Date Fill completed successfully.\n\nCSV log:\n{log_path.resolve()}",
        error=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
