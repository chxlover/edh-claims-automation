"""Unit tests for the HBSys Screen Detector (Slice A1 — Claims Agent).

The detector is a pure function of injected window titles (+ optional OCR
text), so every test runs headless — no HBSys, no display, no clicks.

Run from the project root:

    python -m unittest tests.test_agent_hbsys_screens
    python tests/test_agent_hbsys_screens.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import hbsys_screens as screens  # noqa: E402


class FakeWindow:
    """Minimal stand-in for a pywinauto window wrapper."""

    def __init__(self, text="", broken=False, class_name="FNWND370"):
        self._text = text
        self._broken = broken
        self._class_name = class_name

    def window_text(self):
        if self._broken:
            raise RuntimeError("window is gone")
        return self._text

    def class_name(self):
        if self._broken:
            raise RuntimeError("window is gone")
        return self._class_name


def listing(*titles, broken=(), classes=None):
    classes = classes or {}
    windows = [
        FakeWindow(
            text=title,
            broken=(title in broken),
            class_name=classes.get(title, "FNWND370"),
        )
        for title in titles
    ]
    return lambda: windows


def listing_with_desktop(real_titles, **kwargs):
    """Desktop where only `real_titles` have real (non-stub) windows.

    dialog_body_fn MUST ignore anything it cannot see here - mirroring the
    live rule that body text may only come from a window that is really open.
    Also provides dialog_marker_fn: the live-window proof that admits the
    "Note" into the detector (mirrors the action module's find_dialog_exact).
    """
    open_windows = set(real_titles)

    def dialog_body_fn(title):
        if title in open_windows:
            return kwargs.get("body_text", "")
        return ""

    def dialog_marker_fn(markers):
        if isinstance(markers, str):
            markers = (markers,)
        wanted = {str(marker).strip().lower() for marker in markers}
        for title in open_windows:
            if str(title).strip().lower() in wanted:
                return title
        return None

    return dialog_body_fn, dialog_marker_fn


class ClosedTests(unittest.TestCase):
    def test_no_windows_means_closed(self):
        result = screens.detect_screen(list_windows=lambda: [])
        self.assertEqual(result.screen, screens.SCREEN_HBSYS_CLOSED)

    def test_non_hbsys_windows_means_closed(self):
        result = screens.detect_screen(
            list_windows=listing("Google Chrome", "Calculator")
        )
        self.assertEqual(result.screen, screens.SCREEN_HBSYS_CLOSED)

    def test_browser_tab_with_hbsys_title_is_not_hbsys(self):
        # A Chrome tab titled "Claude HBSys Automation" is not HBSys even
        # when only the title is considered: Chrome top-level windows use
        # the Chrome_WidgetWin_* class and their titles end with the
        # browser name, while the real HBSys title starts with "HBSys".
        result = screens.detect_screen(
            list_windows=listing(
                "Claude HBSys Automation - Google Chrome",
                classes={
                    "Claude HBSys Automation - Google Chrome": "Chrome_WidgetWin_1"
                },
            )
        )
        self.assertEqual(result.screen, screens.SCREEN_HBSYS_CLOSED)

    def test_broken_windows_are_ignored(self):
        result = screens.detect_screen(
            list_windows=listing("HBSys", broken=("HBSys",))
        )
        self.assertEqual(result.screen, screens.SCREEN_HBSYS_CLOSED)


class PopupPriorityTests(unittest.TestCase):
    def test_upload_claims_popup_wins_over_main(self):
        result = screens.detect_screen(
            list_windows=listing("HBSys - Hospital Operations", "Upload Claims")
        )
        self.assertEqual(result.screen, screens.SCREEN_UPLOAD_CLAIMS)

    def test_admission_history_popup(self):
        result = screens.detect_screen(
            list_windows=listing("HBSys", "Admission History")
        )
        self.assertEqual(result.screen, screens.SCREEN_ADMISSION_HISTORY)

    def test_select_encounter_is_dialog(self):
        result = screens.detect_screen(
            list_windows=listing("HBSys", "Select Encounter")
        )
        self.assertEqual(result.screen, screens.SCREEN_DIALOG)

    def test_attachments_screen(self):
        result = screens.detect_screen(
            list_windows=listing("HBSys", "Upload Claim Attachments")
        )
        self.assertEqual(result.screen, screens.SCREEN_ATTACHMENTS)

    def test_generator_result_window_is_dialog(self):
        result = screens.detect_screen(
            list_windows=listing("HOSPITAL - CF4 XML Generator")
        )
        self.assertEqual(result.screen, screens.SCREEN_DIALOG)

    def test_file_save_note_window_is_detected(self):
        # Live 2026-10-02: HBSys titled the prompt "Note" and put "File Save"
        # in the body, so the flow never reached the OK the operator wants.
        # The runner's screen OCR never reads this popup; body text comes
        # from the dialog itself (dialog_body_fn), which may only confirm a
        # window that is really open (dialog_marker_fn, the live-window proof).
        body_fn, marker_fn = listing_with_desktop(
            ("HBSys", "Note"), body_text="File Save"
        )
        result = screens.detect_screen(
            list_windows=listing("HBSys", "Note"),
            dialog_body_fn=body_fn, dialog_marker_fn=marker_fn,
        )
        self.assertEqual(result.screen, screens.SCREEN_FILE_SAVE)

    def test_a_plain_note_window_is_not_the_file_save_prompt(self):
        # "Note" is far too generic to trust on its own: only the body text
        # may promote it to the File Save prompt.
        result = screens.detect_screen(
            list_windows=listing("HBSys", "Note"), ocr_text="Attendance list"
        )
        self.assertNotEqual(result.screen, screens.SCREEN_FILE_SAVE)

    def test_note_without_body_confirmation_is_never_file_save(self):
        # Even WITH a File Save body, a "Note" that is not really open must
        # not be promoted: the dialog lookup must see a live window.
        body_fn, marker_fn = listing_with_desktop(
            ("HBSys",), body_text="File Save"
        )
        result = screens.detect_screen(
            list_windows=listing("HBSys", "Note"),
            dialog_body_fn=body_fn, dialog_marker_fn=marker_fn,
        )
        self.assertNotEqual(result.screen, screens.SCREEN_FILE_SAVE)

    def test_prompt_beats_print_options_even_when_both_are_open(self):
        # Live 2026-10-02 15:03 (patient #21853): OK raised the File save prompt
        # while Print Options stayed open behind it. The prompt must be the
        # screen, never FINAL_BILL_OPTIONS - otherwise the runner clicked
        # nothing and BLOCKed with "Print Options still open".
        body_fn, marker_fn = listing_with_desktop(
            ("HBSys", "Note"), body_text="File Save"
        )
        result = screens.detect_screen(
            list_windows=listing(
                "HBSys",
                "Print Options",
                "Note",
            ),
            dialog_body_fn=body_fn, dialog_marker_fn=marker_fn,
        )
        self.assertEqual(result.screen, screens.SCREEN_FILE_SAVE)

    def test_file_save_title_still_detected_without_ocr(self):
        result = screens.detect_screen(list_windows=listing("HBSys", "File save"))
        self.assertEqual(result.screen, screens.SCREEN_FILE_SAVE)


class FullScreenStateTests(unittest.TestCase):
    def test_main_title_without_ocr_defaults_to_order_transactions(self):
        result = screens.detect_screen(list_windows=listing("HBSys"))
        self.assertEqual(result.screen, screens.SCREEN_ORDER_TRANSACTIONS)

    def test_beneficiaries_ocr(self):
        result = screens.detect_screen(
            list_windows=listing("HBSys"),
            ocr_text="PHILHEALTH BENEFICIARIES grid Cancel",
        )
        self.assertEqual(result.screen, screens.SCREEN_PHIC_BENEFICIARIES)

    def test_claim_form_2_ocr(self):
        result = screens.detect_screen(
            list_windows=listing("HBSys"),
            ocr_text="Claim Form 2 professional fee date",
        )
        self.assertEqual(result.screen, screens.SCREEN_CLAIM_FORM_2)

    def test_eclaims_dashboard_ocr(self):
        result = screens.detect_screen(
            list_windows=listing("HBSys"),
            ocr_text="eClaims Upload Att Add Claims toolbar",
        )
        self.assertEqual(result.screen, screens.SCREEN_ECLAIMS_DASHBOARD)


class ConvenienceTests(unittest.TestCase):
    def test_is_hbsys_open(self):
        self.assertFalse(screens.is_hbsys_open(list_windows=lambda: []))
        self.assertTrue(screens.is_hbsys_open(list_windows=listing("HBSys")))

    def test_describe_closed(self):
        result = screens.detect_screen(list_windows=lambda: [])
        self.assertIn("CLOSED", screens.describe(result))

    def test_expected_prompt_screen_reads_the_plan_reason(self):
        # The two reasons in logs/agent_plan_*.json raise two different
        # prompts, and only one of them is ever expected for a row.
        self.assertEqual(
            screens.expected_prompt_screen(
                "NO FINAL BILL sa HBSys \u2014 kailangan i-final bill muna."
            ),
            screens.SCREEN_FILE_SAVE,
        )
        self.assertEqual(
            screens.expected_prompt_screen(
                "MISMATCH ang itemized vs grouped charges \u2014 "
                "kailangan i-final bill muna."
            ),
            screens.SCREEN_FINAL_BILL_CONFIRM,
        )

    def test_expected_prompt_screen_is_empty_for_other_reasons(self):
        self.assertEqual(screens.expected_prompt_screen(""), "")
        self.assertEqual(screens.expected_prompt_screen(None), "")
        self.assertEqual(screens.expected_prompt_screen("missing SOA1"), "")


if __name__ == "__main__":
    unittest.main()
