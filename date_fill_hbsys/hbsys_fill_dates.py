from __future__ import annotations

import argparse
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

from hbsys_read_admission_history import (
    ConfinementRow,
    DATE_RE,
    OcrItem,
    find_admission_history_window,
    parse_rows_with_positions,
    read_focused_admission_row_variants,
    read_ocr_item_variants,
    read_ocr_items,
    select_confinement_row,
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

# 2026-09-28: foreground guard for the BLIND pyautogui input
# (core/agent/window_guard.py). The project root is APPENDED, not prepended,
# so this tool's sibling modules keep import priority. The import is optional
# on purpose: without it the tool behaves exactly as it did before.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))
try:
    from core.agent.window_guard import guard_input as _guard_input
except Exception:  # noqa: BLE001 - the guard is diagnostics only
    _guard_input = None


LOG_DIR = Path("logs")

# How long select_admission_history_row waits for the Admission History popup
# to consume the double-click. The popup closes on a successful pick; while it
# stays open the confinement was never loaded and the next step (PHIC) would
# run on the wrong screen — seen live 2026-09-26/28 as the double-click
# landing on the toolbar and every later step reading the wrong screen.
ADMIT_HISTORY_CLOSE_TIMEOUT = 3.0


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
    # End-of-run cleanup slot (operator rule 2026-10-08): the
    # Close Form toolbar slot read from the live HBSys toolbar
    # (X:434 Y:60). Same context rule as the alt slot — it is
    # the open form's Close Form button while a form window is
    # up, and the Admit History toolbar button on the bare
    # hospital-search screen (hence the not-already-safe guard
    # in close_open_form_at_run_end).
    CLOSE_FORM_END_OF_RUN = Point(434, 60)
    # Control id of the Hospital No. Edit on the Billing / hospital-search
    # form, recorded live by the Final Bill probe (final_bill_actions.py:
    # "Hospital No. currently loaded (Edit 1004 on the Billing form)") and
    # reused by the Date Fill load check (live failure 2026-10-09,
    # COLOBONG: OCR read the loaded number ...10920 as ...10820 and the
    # flow stopped for review on a patient that had actually loaded).
    HOSPITAL_NO_EDIT_ID = 1004
    # How many times the screen-restore routine may close the active form
    # while looking for the Hospital No. field (operator rule 2026-10-09).
    SCREEN_RESTORE_ROUNDS = 3
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
        self._expected_admission_grid = ""
        self._expected_discharge_grid = ""
        self._admission_history_image_path = None
        self._admission_history_window = None
        self._admission_history_item_passes: list = []

    def log_action(self, message: str) -> None:
        prefix = "LIVE" if self.live else "DRY"
        print(f"[{prefix}] {message}")

    def guard_blind_input(self, action: str) -> bool:
        """Check the foreground window before sending blind input.

        2026-09-28: pyautogui sends clicks/keys to WHATEVER window has focus,
        so when HBSys is not focused (or is hung - seen live 2026-09-26 as
        "focus HBSys window: '... (Not Responding)'") the input lands on
        another application, e.g. the EDH Claims GUI. Default mode WARNS
        through log_action and then proceeds (behavior unchanged); set
        CLAIMS_AGENT_FOCUS_GUARD=block to refuse the input instead.

        Fail-open: any guard problem returns True, so this check can never
        stop a production run.
        """
        if _guard_input is None:
            return True
        try:
            return bool(_guard_input(action, log_fn=self.log_action))
        except Exception:  # noqa: BLE001 - never break a run
            return True

    def maybe_wait(self) -> None:
        time.sleep(self.pause)

    def click(self, point: Point, label: str) -> None:
        action = f"click {label} at ({point.x}, {point.y})"
        self.log_action(action)
        if self.live:
            if not self.guard_blind_input(action):
                return
            pyautogui.click(point.x, point.y)
        self.maybe_wait()

    def double_click(self, point: Point, label: str) -> None:
        action = f"double-click {label} at ({point.x}, {point.y})"
        self.log_action(action)
        if self.live:
            if not self.guard_blind_input(action):
                return
            pyautogui.doubleClick(point.x, point.y)
        self.maybe_wait()

    def press(self, key: str) -> None:
        action = f"press {key}"
        self.log_action(action)
        if self.live:
            if not self.guard_blind_input(action):
                return
            pyautogui.press(key)
        self.maybe_wait()

    def hotkey(self, *keys: str) -> None:
        action = f"hotkey {'+'.join(keys)}"
        self.log_action(action)
        if self.live:
            if not self.guard_blind_input(action):
                return
            pyautogui.hotkey(*keys)
        self.maybe_wait()

    def write(self, text: str) -> None:
        action = f"type {text!r}"
        self.log_action(action)
        if self.live:
            if not self.guard_blind_input(action):
                return
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
        """Dismiss only the known informational dialog after encounter selection.

        HBSys pops a "Rate validation" dialog carrying "Rates no longer exist"
        when a confinement row is chosen. It is informational: the encounter
        loads fine once OK is pressed. An *unknown* "Rate validation" dialog is
        left alone (returns False) so a real rate error stops the run for
        review instead of being silently dismissed.
        """
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
                    self.press("enter")
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
        # Only a form that actually has the Hospital No. field can load a
        # patient (live failure 2026-10-09, DAYAG: the search ran while the
        # Patient Record Form was still up, the double-click hit a label, the
        # typed number went nowhere and the probe kept showing the previous
        # patient). Restore the Billing / hospital-search form first; if it
        # cannot be restored, do NOT type into an unknown form — the caller's
        # load verification stops this patient safely for review instead.
        if not self.ensure_hospital_number_screen("before the patient search"):
            self.log_action(
                "no screen with a Hospital No. field — not typing the hospital "
                "number into an unknown form"
            )
            return
        self.double_click(P.HOSPITAL_NO, "Hospital No.")
        self.hotkey("ctrl", "a")
        self.write(claim.hospital_no)
        self.press("enter")
        sleep_short(1.5)

    def select_admission_history_row(self, claim: ReadyClaim) -> bool:
        """Click Admit History, then double-click the row for this admission.

        Shared recipe with the Final Bill agent (select_confinement_row from
        hbsys_read_admission_history): exact folder-to-grid matching first,
        then OCR-tolerant fuzzy matching, and nothing else guesses — a
        mismatch stops for review. The grid dates stay in MM/DD/YYYY shape
        after comparing raw values still OCR-misread (``O1/0l/2026`` etc.),
        so dates are compared by canonical key, not raw string equality.

        Live failure 2026-10-08 (workflow run 1: FLORES, GANNABAN,
        MENESES, TAGUBA): the popup can open against the PREVIOUS
        patient while the hospital-number load is still settling, so
        every OCR pass reads the wrong patient's confinements and the
        folder match fails. One automatic reload + reopen heals it (the
        follow-up run matched the same patients); a patient whose real
        history still does not match the folder (MENESES) stays a safe
        stop for review — never guessed.

        Live failure 2026-10-09 (CORTEZ, MENESES): the reload's
        re-typed hospital number never switched the loaded patient
        (the safe-reset proof still showed the PREVIOUS patient's
        record), so the second popup pass read the same wrong rows.
        The reload now closes the popup with verification, re-focuses
        HBSys, and requires the searched Hospital No. on the base
        screen before any popup row is trusted.
        """
        self._expected_admission_grid = claim.admission_grid
        self._expected_discharge_grid = claim.discharge_grid
        picked = False
        for load_attempt in (1, 2):
            if load_attempt == 2:
                self.log_action(
                    "Admission History did not match the folder on the "
                    "first pass; reloading the patient and retrying"
                )
                if not self._close_admission_history_popup():
                    self.log_action(
                        "Admission History popup did not close after "
                        "the folder mismatch; stopping for review"
                    )
                    return False
                self.focus_hbsys()
                self.search_hospital_number(claim)
                sleep_short(1.0)
            if not self._hospital_number_visible(claim):
                if load_attempt == 1:
                    self.log_action(
                        "Hospital No. not visible on the base "
                        "screen; re-searching the patient"
                    )
                    self.focus_hbsys()
                    self.search_hospital_number(claim)
                    sleep_short(1.0)
                else:
                    self.log_action(
                        f"Hospital No. {claim.hospital_no} not "
                        "visible after the patient reload; "
                        "stopping for review"
                    )
                    return False
                if not self._hospital_number_visible(claim):
                    self.log_action(
                        f"Hospital No. {claim.hospital_no} still "
                        "not visible; stopping for review"
                    )
                    return False
            try:
                picked = select_confinement_row(
                    open_popup_fn=self._open_admission_history,
                    live=self.live,
                    log_fn=self.log_action,
                    wait_fn=sleep_short,
                    read_rows_fn=self._read_confinement_rows,
                    double_click_fn=self._double_click_row,
                    rate_dialog_fn=self.dismiss_rate_validation_message,
                    expected_admission=claim.admission_grid,
                    expected_discharge=claim.discharge_grid,
                    fuzzy_rows_fn=self._read_fuzzy_confinement_row,
                    on_selected_row_fn=self._set_form_stage_base,
                    context="Date Fill",
                )
            except RuntimeError:
                # The shared reader raises when the popup never opens
                # (HBSys still settling); the reload pass below retries
                # instead of aborting the batch.
                picked = False
            if picked:
                break
        if not picked:
            return False
        if not self._wait_admission_history_closed():
            self.log_action(
                "Admission History popup is still open after selecting the "
                "row; the confinement was not loaded — stopping for review"
            )
            return False
        return True

    def _close_admission_history_popup(self) -> bool:
        """Close a leftover Admission History popup, verified.

        The popup covers the Hospital No. field, so it must
        be gone before a patient reload can re-type the
        hospital number (live failure 2026-10-09: the reload
        typed into the still-open popup and the patient
        never switched).
        """
        for _close_round in (1, 2):
            window = find_admission_history_window()
            if window is None:
                return True
            try:
                window.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup.
                try:
                    window.set_focus()
                    pyautogui.press("esc")
                except Exception:  # noqa: BLE001 - best-effort.
                    pass
            sleep_short(0.5)
        return find_admission_history_window() is None

    def hospital_no_edit(self):
        """The visible Hospital No. Edit control, or None when it is not on screen.

        pywinauto reads the control's OWN text, so the check never depends on
        OCR: live failure 2026-10-09 (COLOBONG) — the patient HAD loaded into
        the Billing form, but the whole-window OCR read the number 10920 as
        10820, so the flow reported "not visible" and stopped for review on a
        patient that was already loaded. None whenever the field is absent (a
        Patient Record Form has no Hospital No. field), which is what the
        screen-restore routine below keys off.
        """
        if self.hbsys_window is None:
            return None
        try:
            for child in self.hbsys_window.children():
                try:
                    if child.class_name() != "Edit":
                        continue
                    if child.control_id() != P.HOSPITAL_NO_EDIT_ID:
                        continue
                    if not child.is_visible():
                        continue
                    return child
                except Exception:
                    continue
        except Exception:
            return None
        return None

    def hospital_no_edit_text(self) -> str:
        """Exact text of the Hospital No. Edit control ('' when not readable)."""
        edit = self.hospital_no_edit()
        if edit is None:
            return ""
        try:
            return (edit.window_text() or "").strip()
        except Exception:
            return ""

    def ensure_hospital_number_screen(self, context: str) -> bool:
        """True when a form with the Hospital No. field is the current screen.

        Live failure 2026-10-09 (DAYAG): a Date Fill flow can end on the
        Patient Record Form, which has NO Hospital No. field, so the next
        search double-clicked a label, the typed number went nowhere, the
        probe still showed the PREVIOUS patient's record, and the safe reset
        failed on the same screen (its proof needs the Hospital No. marker)
        — which then stopped the whole batch. When the field is missing the
        active form is closed (ctrl+F4 = standard MDI child close) until the
        Billing / hospital-search form is back; nothing is typed and no
        patient is guessed. False means the caller must stop that patient
        safely for review.
        """
        if not self.live:
            return True
        for round_no in range(1, P.SCREEN_RESTORE_ROUNDS + 1):
            if self.hospital_no_edit() is not None:
                return True
            if round_no > 1:
                self.log_action(
                    f"{context}: no Hospital No. field on screen; closing "
                    f"the active form (round {round_no}/"
                    f"{P.SCREEN_RESTORE_ROUNDS})"
                )
                try:
                    self.focus_hbsys()
                    pyautogui.hotkey("ctrl", "f4")
                except Exception as exc:  # noqa: BLE001 - best-effort restore.
                    self.log_action(f"{context}: close attempt failed: {exc}")
                sleep_short(0.8)
        if self.hospital_no_edit() is None:
            self.log_action(
                f"{context}: no screen with a Hospital No. field after "
                f"{P.SCREEN_RESTORE_ROUNDS} close attempts"
            )
            return False
        return True

    def _hospital_number_visible(self, claim) -> bool:
        """Require the searched hospital number on the base screen.

        The Patient Record Form header shows the loaded
        patient's hospital number. A popup that still shows
        the previous patient means the search never switched
        the patient (live failure 2026-10-09, CORTEZ/MENESES).
        """
        if self.hbsys_window is None:
            return False
        wanted = re.sub(r"\D+", "", str(claim.hospital_no))
        # Live failure 2026-10-09 (COLOBONG, workflow runs 1 and 2):
        # the patient loaded into the Billing form, but the whole-window
        # OCR read the loaded number 000000000010920 as ...10820, so the
        # exact check failed and the patient stopped for review while
        # already on screen. The Edit control's own text decides when it
        # is readable — OCR can never misread a digit it does not parse.
        edit = self.hospital_no_edit()
        if edit is not None:
            shown = re.sub(r"\D+", "", self.hospital_no_edit_text())
            return bool(shown) and shown == wanted
        text = self.current_hbsys_text("patient_load_probe")
        squashed = re.sub(r"[^A-Z0-9]+", "", text)
        wanted_text = re.sub(r"[^A-Z0-9]+", "", str(claim.hospital_no).upper())
        if wanted_text in squashed:
            return True
        # OCR digit tolerances: O/D/Q read as 0, L/I as 1,
        # Z as 2, S as 5, B as 8.
        normalized = (
            squashed.replace("O", "0")
            .replace("D", "0")
            .replace("Q", "0")
            .replace("L", "1")
            .replace("I", "1")
            .replace("Z", "2")
            .replace("S", "5")
            .replace("B", "8")
        )
        return wanted_text in normalized

    def _wait_admission_history_closed(self) -> bool:
        """The popup must consume the pick before the next step may run.

        Without this check a double-click that misses the row (wrong
        coordinates, lost focus) leaves the popup open and every later step
        silently operates on the wrong screen instead of stopping.
        """
        deadline = time.time() + ADMIT_HISTORY_CLOSE_TIMEOUT
        while time.time() < deadline:
            if find_admission_history_window() is None:
                return True
            time.sleep(0.2)
        return False

    def _set_form_stage_base(self) -> None:
        self.screen_stage = "base"

    def _open_admission_history(self, purpose: str):
        """Click the toolbar slot, or return the open popup when verifying."""
        if purpose == "verify open":
            return find_admission_history_window()
        self.click(P.ADMIT_HISTORY, "Admit History")
        self.screen_stage = "admission_popup"
        sleep_short(0.8)
        return None

    def _read_confinement_rows(self, window) -> list:
        """Admission History rows from EVERY OCR pass, merged.

        Copied from the proven Date Fill ABTC/Regular tool: full-window
        variants plus focused per-row crops, so one weak pass cannot hide
        the row the folder needs (live 2026-09-26: the single best pass read
        only the OPD row and stopped the claim). Raw grid strings ride along
        untouched for the shared exact/fuzzy matcher.
        """
        image_path = self.capture_window(window, "admission_history_select")
        self._admission_history_image_path = image_path
        self._admission_history_window = window
        variants = list(read_ocr_item_variants(image_path))
        focused = read_focused_admission_row_variants(image_path)
        if focused:
            self.log_action(
                f"Admission History added {len(focused)} focused row OCR passes"
            )
        self._admission_history_item_passes = variants + focused
        parsed_rows = self._merge_parsed_rows(self._admission_history_item_passes)
        return [self._confinement_row(window, parsed) for parsed in parsed_rows]

    @staticmethod
    def _merge_parsed_rows(item_passes: list) -> list:
        """Every distinct grid row any OCR pass parsed (first pass wins)."""
        merged: list = []
        seen: set = set()
        for items in item_passes:
            for parsed in parse_rows_with_positions(items):
                key = (parsed.row.admission_date, parsed.row.discharge_date)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(parsed)
        return merged

    def _confinement_row(self, window, parsed):
        """One shared ConfinementRow for the exact and fuzzy passes.

        BOTH coordinates are screen-absolute: x from the window's left edge,
        y from the window's top edge — the same ``rect.top + row_y`` the
        proven Date Fill ABTC/Regular tool clicks. Without the top offset the
        double-click landed on the toolbar above the popup (live 2026-09-26/28:
        click at y=98 while the row sat at y=218), the popup never closed,
        and every later step ran on the wrong screen.
        """
        rect = window.rectangle()
        point = (rect.left + 72, rect.top + int(parsed.y))
        return ConfinementRow(
            admission_grid=parsed.row.admission_date,
            discharge_grid=parsed.row.discharge_date,
            point=point,
            encounter_type=parsed.row.normalized_encounter_type,
        )

    def _read_fuzzy_confinement_row(self):
        """Re-score the OCR-tolerant row over every pass of the grid image.

        The repaired single best pass runs first (its date-cell re-read
        rescues smeared dates like ``09/2212026``), then the same merged
        variants/focused passes the exact match used.
        """
        item_passes = [read_ocr_items(self._admission_history_image_path)]
        item_passes.extend(self._admission_history_item_passes)
        parsed_rows = self._merge_parsed_rows(item_passes)
        fuzzy = self.best_fuzzy_admission_history_row(
            parsed_rows,
            self._expected_admission_grid,
            self._expected_discharge_grid,
        )
        if fuzzy is None:
            return None
        return self._confinement_row(self._admission_history_window, fuzzy)

    def _double_click_row(self, click_x: int, click_y: int) -> None:
        self.double_click(Point(click_x, click_y), "Admission History row")

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
        """Single-click the beneficiary row and PROVE it got highlighted.

        Copied from the proven Date Fill ABTC/Regular tool: click inside the
        row (x=260, retry x=520), recapture, and only accept the pick when the
        same row OCRs back as the Windows blue selected row. The old blind
        single click at x=46 sent no proof, so a miss either stopped for
        review on the wrong screen or silently carried the fill onward.
        """
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
        if self.beneficiaries_window_shows_other_patient(image_path, claim):
            # Stale window from the previous patient (live failure
            # 2026-10-08, TUTAAN): HBSys refocused SIBALON's still-open
            # Beneficiaries window instead of loading this claim's rows.
            # Close it and reopen a fresh window for the current patient
            # before any row is trusted. A window that is STILL stale
            # after the reopen falls through to the row scan, whose
            # first-name guard remains the final never-guess gate.
            self.close_phic_beneficiaries(
                "Close stale PhilHealth Beneficiaries window"
            )
            self.click(
                P.PHIC,
                "PHIC (reopen after stale Beneficiaries window)",
            )
            self.screen_stage = "beneficiary"
            sleep_short(1.0)
            if not self.dismiss_claim_form4_after_phic_if_visible():
                return False
            image_path = self.capture_window(
                self.hbsys_window,
                "phic_beneficiaries_select_retry",
            )
        ocr_variants = read_ocr_item_variants(image_path)
        row_y = self.find_phic_beneficiary_row_y_from_variants(
            ocr_variants,
            claim.admission_grid,
            claim.discharge_grid,
            claim.patient_name,
            minimum_consensus=2,
        )
        if row_y is None:
            # Single-pass rescue (Change Record 2026-10-07): the selected
            # (blue) target row OCRs poorly — its year misreads as 2028 — so
            # often only ONE pass carries the claim's own confinement
            # month/day. Trust that locate only together with the blue proof
            # below or the proof-gated click loop.
            row_y = self.find_phic_beneficiary_row_y_from_variants(
                ocr_variants,
                claim.admission_grid,
                claim.discharge_grid,
                claim.patient_name,
                minimum_consensus=1,
                require_confinement_evidence=True,
            )
            if row_y is not None:
                self.log_action(
                    f"PHIC tentative single-pass row locate y={row_y:.1f} "
                    "carrying the claim confinement dates"
                )
        if row_y is None:
            self.log_action(
                "matching PhilHealth Beneficiaries row not found; stopping for review"
            )
            return False

        # HBSys can open Beneficiaries with the claim's row ALREADY selected.
        # Clicking before checking moved the highlight OFF the correct row
        # (live failure 2026-10-07), so confirm first.
        if self.is_blue_highlighted_row(image_path, row_y):
            self.log_action(
                f"PhilHealth Beneficiaries row {claim.admission_grid}-"
                f"{claim.discharge_grid} already highlighted; selection "
                "confirmed without clicking"
            )
            return True

        rect = self.hbsys_window.rectangle()
        click_y = rect.top + int(round(row_y))
        # Middle x-offset (390) is the row's text area; the flanking offsets
        # (260/520) cover the date columns. All three keep the blue-highlight
        # proof requirement (Option A, 2026-10-06).
        for attempt, x_offset in enumerate((260, 390, 520), start=1):
                click_x = rect.left + x_offset
                self.click(
                    Point(click_x, click_y),
                    f"PhilHealth Beneficiaries row {claim.admission_grid}-"
                    f"{claim.discharge_grid} attempt {attempt}",
                )
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
                    minimum_consensus=2,
                )
                if proof_y is not None and self.is_blue_highlighted_row(
                    proof_path, proof_y
                ):
                    self.log_action(
                        "PhilHealth Beneficiaries row highlighted and verified "
                        f"on attempt {attempt}"
                    )
                    return True
                self.log_action(
                    f"PhilHealth Beneficiaries attempt {attempt}: clicked row is "
                    "not the highlighted match"
                )
                # Self-correction: if the proof OCR locates the claim row at
                # a different y than the row we clicked, the next attempt
                # must click the proof location, not repeat the stale one
                # (live failure 2026-10-07 clicked y=281 three times while
                # the proof kept reporting y≈207).
                if proof_y is not None:
                    proof_click_y = rect.top + int(round(proof_y))
                    if abs(proof_click_y - click_y) > 4:
                        self.log_action(
                            f"re-target next PHIC click to y={proof_click_y}: "
                            "proof OCR located the claim row elsewhere"
                        )
                        click_y = proof_click_y
        self.log_action(
            "PhilHealth Beneficiaries row never highlighted as selected; "
            "stopping for review"
        )
        return False

    @staticmethod
    def _parse_mdy(value: str) -> tuple[int, int] | None:
        """Parse MM/DD/YYYY into (month, day); None when OCR garbled it."""
        match = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", value or "")
        if not match:
            return None
        month, day = int(match.group(1)), int(match.group(2))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        return month, day

    def phic_row_date_evidence(
        self,
        items: list[OcrItem],
        row_y: float,
        admission_date: str,
        discharge_date: str,
    ) -> str:
        """Classify the confinement-column dates OCR read on one grid row.

        Returns:
            "confine" — a date matches the claim's month/day (year OCR
                        misreads such as 2026 -> 2028 are tolerated).
            "refute"  — the dates are readable but belong to a different
                        confinement of the same patient (for example the
                        07/11/2025 stay when the claim is 09/16/2026). Such
                        a vote must never help elect the clicked row.
            "unknown" — no usable date evidence (garbled or missing).

        Only the first two readable dates count: ADMISSION DATE and
        DISCHARGE DATE are the first two date columns, while BIRTHDAY and
        similar later columns must not carry the decision.
        """
        target_days = {
            parsed
            for expected in (admission_date, discharge_date)
            if (parsed := self._parse_mdy(expected)) is not None
        }
        target_months = {month for month, _ in target_days}
        row_dates: list[tuple[int, int]] = []
        for item in sorted(
            (value for value in items if abs(value.y - row_y) <= 10),
            key=lambda value: value.x,
        ):
            for match in DATE_RE.finditer(item.text):
                parsed = self._parse_mdy(match.group(0))
                if parsed is not None:
                    row_dates.append(parsed)
        confinement = row_dates[:2]
        if not confinement:
            return "unknown"
        if any(date in target_days for date in confinement):
            return "confine"
        if any(month in target_months for month, _ in confinement):
            return "unknown"
        return "refute"

    def find_phic_beneficiary_row_y_from_variants(
        self,
        item_variants: list[list[OcrItem]],
        admission_date: str,
        discharge_date: str,
        patient_name: str = "",
        minimum_consensus: int = 1,
        require_confinement_evidence: bool = False,
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
            evidence = self.phic_row_date_evidence(
                items, row_y, admission_date, discharge_date
            )
            if evidence == "refute":
                # Live failure 2026-10-07: four passes read the patient's
                # OTHER confinement (07/11/2025) because the blue target row
                # OCRs poorly, and that wrong majority elected the clicked
                # row. A row whose dates clearly belong to another stay must
                # abstain instead of voting.
                self.log_action(
                    f"PHIC OCR variant {variant_number} abstains: row y="
                    f"{row_y:.1f} reads a different confinement than the claim"
                )
                continue
            if require_confinement_evidence and evidence != "confine":
                self.log_action(
                    f"PHIC OCR variant {variant_number} ignored for the "
                    f"tentative locate: no claim confinement dates at y={row_y:.1f}"
                )
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
        if len(best_cluster) < minimum_consensus:
            self.log_action(
                "PHIC OCR did not reach the required row-position consensus: "
                f"{len(best_cluster)}/{minimum_consensus} pass(es)"
            )
            return None
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
        parts = self.name_parts(patient_name)
        first_name_token = parts["first"]
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
                # The shared last name ('BALUNSAT') must NOT carry the fallback
                # decision either. Require the FIRST NAME token; otherwise this
                # row is the wrong sibling and must not be a fallback pick.
                if first_name_token and first_name_token not in self.normalize_for_name_match(
                    row_text
                ):
                    continue
                dated_name_candidates.append((row_y, row_text, row))

        if len(candidates) == 1:
            row_y, row_text, _row = candidates[0]
            # A single confinement match is NOT proof when a sibling shares the
            # same last name (BALUNSAT, KATE ARIANE vs AMARA MARCELINE). Require
            # the first-name token to be present in the row text.
            if first_name_token and first_name_token not in self.normalize_for_name_match(
                row_text
            ):
                self.log_action(
                    "PHIC single confinement candidate rejected: first name "
                    f"{first_name_token!r} not found in {row_text!r}"
                )
                return None
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
                    # Same guard as the dated-name fallback: the shared last
                    # name ('BALUNSAT') must not carry the admission+name
                    # fallback decision. Require the FIRST NAME token.
                    if first_name_token and first_name_token not in normalized_row_text:
                        continue
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
            # The shared last name ('GONZALES') must NOT carry the decision.
            # Require the FIRST NAME token; a row matching only the last name
            # is the wrong sibling and gets score 0 (excluded).
            if first_name_token and first_name_token not in normalized_row_text:
                self.log_action(
                    f"PHIC candidate rejected: first name {first_name_token!r} "
                    f"not in {row_text!r}"
                )
                scored.append((0, row_y, row_text))
                continue
            score = sum(1 for token in name_tokens if token in normalized_row_text)
            scored.append((score, row_y, row_text))

        scored.sort(key=lambda item: (-item[0], item[1]))
        best_score, best_y, best_text = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else -1
        # Copied from the proven Date Fill ABTC/Regular tool: one matching
        # name token is not proof when several rows share the confinement.
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
        """Confirm that the exact OCR row is the Windows blue selected row.

        Copied from the proven Date Fill ABTC/Regular tool: the click alone
        proves nothing, only the highlighted row does.
        """
        with Image.open(image_path).convert("RGB") as image:
            y1 = max(0, int(row_y) - 7)
            y2 = min(image.height, int(row_y) + 8)
            x2 = min(image.width, 1580)
            # tobytes() instead of the deprecated Image.getdata() (Pillow 14).
            raw = image.crop((5, y1, x2, y2)).tobytes()
        pixel_count = len(raw) // 3
        if not pixel_count:
            return False
        blue_pixels = 0
        for index in range(0, len(raw), 3):
            red, green, blue = raw[index], raw[index + 1], raw[index + 2]
            if blue >= 120 and blue > red * 1.25 and blue > green * 1.15:
                blue_pixels += 1
        return blue_pixels / pixel_count >= 0.08

    @staticmethod
    def normalize_for_name_match(value: str) -> str:
        return re.sub(r"[^A-Z0-9]+", " ", value.upper()).strip()

    def name_match_tokens(self, patient_name: str) -> list[str]:
        normalized = self.normalize_for_name_match(patient_name)
        tokens = [token for token in normalized.split() if len(token) >= 3]
        return tokens

    @staticmethod
    def name_parts(patient_name: str) -> dict[str, str | list[str]]:
        """Split 'LAST, FIRST MIDDLE EXTRA' into role-tagged parts.

        Used by the PHIC Beneficiaries disambiguator: when several rows share
        the same confinement and the same last name, the FIRST NAME is the only
        discriminating token. Matching only the shared last name ('GONZALES')
        selected the wrong sibling (BALUNSAT, KATE ARIANE instead of AMARA
        MARCELINE) -- Option: require the first-name token to be present.
        """
        normalized = re.sub(r"[^A-Z0-9]+", " ", (patient_name or "").upper()).strip()
        if "," in normalized:
            last, rest = normalized.split(",", 1)
            last = last.strip()
            rest_tokens = [t for t in rest.split() if len(t) >= 3]
        else:
            tokens = normalized.split()
            last = tokens[0] if tokens else ""
            rest_tokens = tokens[1:]
        first = rest_tokens[0] if rest_tokens else ""
        return {"last": last, "first": first, "rest": rest_tokens[1:]}

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

    def close_open_form_at_run_end(self) -> None:
        """End-of-run Close Form cleanup (operator rule 2026-10-08).

        After the LAST patient, click the Close Form slot
        (X:434 Y:60) — but ONLY when the screen is not already
        at the hospital-number base state — so a form left open
        after the final patient (e.g. a Beneficiaries window
        that survived its close slot) cannot block the next
        workflow node. Final Bill follows Date Fill, and its
        first HBSys step needs a clean screen ready for
        hospital-number entry. On an already-clean screen the
        same slot is the Admit History toolbar button, so the
        click must NOT happen there (it would open the popup
        instead of closing a form).
        """
        if not self.live or self.hbsys_window is None:
            return
        # A form with no Hospital No. field (the Patient Record Form,
        # live failure 2026-10-09 DAYAG) puts the slot at (434, 60) on a
        # DIFFERENT button — it would open another Claim Form instead of
        # closing one, leaving the next workflow node a worse screen than
        # it found. Restore the Billing / hospital-search form first; the
        # slot is only needed once that screen is back and a form is up.
        self.ensure_hospital_number_screen("at the end of the Date Fill run")
        end_text = self.current_hbsys_text("date_fill_end_probe")
        if self.is_safe_reset_text(end_text):
            return
        self.click(
            P.CLOSE_FORM_END_OF_RUN,
            "Close Form (end of Date Fill run)",
        )
        sleep_short(0.6)

    def current_hbsys_text(self, prefix: str) -> str:
        if self.hbsys_window is None:
            return ""
        path = self.capture_window(self.hbsys_window, prefix)
        return " ".join(item.text.upper() for item in read_ocr_items(path))

    @staticmethod
    def is_phic_beneficiaries_text(text: str) -> bool:
        squashed = re.sub(r"[^A-Z0-9]+", "", text.upper())
        if "PHILHEALTHBENEFICIARIES" in squashed:
            return True
        # OCR often splits the title into "PHIL HEALTH
        # BENEFICIARIES" (space instead of the joined token)
        # or misreads the first word (PHILAEALTH / HEAITH).
        # The old exact-token check let a still-open
        # Beneficiaries window pass for closed (live failure
        # 2026-10-08: SIBALON's window survived the close
        # slot and poisoned the next patient's PHIC step),
        # so the squashed text only needs PHIL +
        # BENEFICIARIES to count as the window being open.
        return "BENEFICIARIES" in squashed and "PHIL" in squashed

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

    def beneficiaries_window_shows_other_patient(
        self,
        image_path: Path,
        claim: ReadyClaim,
    ) -> bool:
        """True when the open Beneficiaries window belongs to another patient.

        HBSys sometimes leaves the previous patient's Beneficiaries
        window open; the PHIC toolbar click then only refocuses that
        window instead of loading the current patient (live failure
        2026-10-08, TUTAAN: the grid still showed SIBALON, MYRNA
        DULAY, whose confinement dates were identical to the claim's,
        so the date-based row scan nearly picked the wrong row and
        only the first-name guard stopped it). The window title
        carries its own patient name, so a capture that shows a
        Beneficiaries window without the claim's first name -- and
        without any other name token -- is stale and must be closed
        and reopened before row selection.
        """
        text = " ".join(
            item.text.upper() for item in read_ocr_items(image_path)
        )
        squashed = re.sub(r"[^A-Z0-9]+", "", text)
        if "BENEFICIARIES" not in squashed:
            # No Beneficiaries window detected; the row scan
            # handles a missing / empty grid as a safe stop.
            return False
        parts = self.name_parts(claim.patient_name)
        first = re.sub(
            r"[^A-Z0-9]+", "", (parts.get("first") or "").upper()
        )
        if first and first in squashed:
            return False
        return not any(
            token in squashed
            for token in self.name_match_tokens(claim.patient_name)
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

            if not self.dismiss_rate_validation_message(timeout=1.5):
                self.log_action(
                    "safe reset stopped: unknown Rate validation dialog is open"
                )
                return False

            if self.screen_stage == "cf2":
                self.click(P.CLOSE_FORM_CF2, "Close Claim Form 2 after failure")
                self.screen_stage = "beneficiary"
            # Two close rounds: a Beneficiaries window that
            # survived both Close Form slots must not terminate
            # the batch (Golden Rule 9) when another round can
            # still clear it (live failure 2026-10-08: the
            # failed reset stopped the whole run at TUTAAN).
            safe = False
            for close_round in (1, 2):
                if (
                    self.screen_stage == "beneficiary"
                    or self.is_phic_beneficiaries_current_screen()
                ):
                    self.close_phic_beneficiaries(
                        "Close PhilHealth Beneficiaries after failure"
                        if close_round == 1
                        else "Second Close PhilHealth Beneficiaries attempt"
                    )
                sleep_short(0.5)
                if self.hbsys_window is None:
                    return False
                proof_text = self.current_hbsys_text("safe_reset_proof")
                safe = (
                    find_admission_history_window() is None
                    and self.is_safe_reset_text(proof_text)
                )
                if safe:
                    break
                if close_round == 1:
                    self.log_action(
                        "safe reset proof still shows an open "
                        "window; retrying the close"
                    )
            if not safe and self.ensure_hospital_number_screen(
                "during the safe reset"
            ):
                # A form with no Hospital No. field (the Patient Record
                # Form) used to fail this proof and stop the WHOLE batch
                # (live failure 2026-10-09, DAYAG: the previous patient's
                # form was still up, the proof read no hospital marker and
                # the run ended on a patient that merely needed the screen
                # restored). Closing it brings the Billing form back, so
                # prove the base state again.
                sleep_short(0.5)
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
        squashed = re.sub(r"[^A-Z0-9]+", "", proof_text.upper())
        hospital_search_visible = any(
            marker in squashed
            for marker in ("HOSPITALNO", "HOSPITALN0", "HOSPITALNUMBER")
        )
        # Same OCR tolerance as is_phic_beneficiaries_text: a
        # spaced "PHIL HEALTH BENEFICIARIES" title must still
        # count as the window being open, otherwise the reset
        # reports success while the previous patient's window
        # is still on screen (live failure 2026-10-08).
        beneficiary_still_open = (
            "PHILHEALTHBENEFICIARIES" in squashed
            or ("BENEFICIARIES" in squashed and "PHIL" in squashed)
        )
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
    if os.environ.get("CLAIMS_AGENT_QUIET") == "1":
        # Slice E Claims Agent: never open the CSV from an unattended run.
        print(f"Run log: {path.resolve()}")
        return
    try:
        os.startfile(path.resolve())  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - opening CSV is convenience only.
        print(f"Could not open run log automatically: {exc}")


def show_popup(title: str, message: str, *, error: bool = False) -> None:
    """Show a small topmost Windows popup for important Date Fill status."""
    if os.environ.get("CLAIMS_AGENT_QUIET") == "1":
        # Slice E Claims Agent runs the tool unattended: print instead of
        # opening a modal dialog. Env unset = existing behavior, unchanged.
        print(f"[POPUP] {title}: {message}")
        return
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

    # End-of-run cleanup (operator rule 2026-10-08): make sure
    # no form is left open after the last patient, so the next
    # workflow node (Final Bill follows Date Fill) starts from a
    # screen ready for hospital-number entry.
    operator.close_open_form_at_run_end()

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
        stop_reason = describe_stop_status(failed.get("status", ""))
        # One machine-readable line so the agent's run report says WHERE it
        # stopped (which patient, which stage, why, and where the evidence is)
        # instead of the first 500 characters of stdout.
        print(
            "[STOP] "
            f"patient={failed.get('patient_name', '')} | "
            f"hospital_no={failed.get('hospital_no', '')} | "
            f"expected ADM {failed.get('admission', '')} - "
            f"DIS {failed.get('discharge', '')} | "
            f"status={failed.get('status', '')} | "
            f"reason={' '.join(stop_reason.split())} | "
            f"screen_stage={operator.screen_stage} | "
            f"safe_reset={failed.get('safe_reset_confirmed', '') or 'not reached'} | "
            f"csv={log_path.resolve()}"
        )
        for image in sorted(
            LOG_DIR.glob("*.png"), key=lambda item: item.stat().st_mtime
        )[-3:]:
            print(f"[EVIDENCE] screenshot {image.resolve()}")
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
