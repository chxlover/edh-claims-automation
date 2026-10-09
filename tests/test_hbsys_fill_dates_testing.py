"""Regression tests for the agent-run Date Fill (hbsys_fill_dates_testing).

Live failure 2026-10-08 (TUTAAN, ZENAIDA CASIMINA, 000000000011453):

* SIBALON's PhilHealth Beneficiaries window survived the Close Form
  slot, but the close verification OCR read the title as the spaced
  "PHIL HEALTH BENEFICIARIES" and the exact-token check missed it,
  so the window passed for closed.
* The next patient's PHIC toolbar click only refocused that stale
  window (its confinement dates were identical to the claim's), and
  only the first-name row guard stopped the wrong pick.
* The safe reset failed the same way, which terminated the whole
  batch (Golden Rule 9: one patient failing must never do that).

Fixes locked down here: space-tolerant window detection, stale-window
close-and-reopen in the PHIC step, and a second close round in the
safe reset. Hermetic: captures are mocked or written to temp dirs.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DATE_FILL_DIR = ROOT / "date_fill_hbsys"
if str(DATE_FILL_DIR) not in sys.path:
    sys.path.insert(0, str(DATE_FILL_DIR))

try:
    import hbsys_fill_dates_testing as hdft
except Exception as exc:  # pragma: no cover - optional GUI/OCR deps
    raise unittest.SkipTest(f"Date Fill testing not importable: {exc}")


class _Rect:
    def __init__(self, left: int, top: int, right: int = 1930, bottom: int = 1060):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _Window:
    def __init__(self, rect: _Rect):
        self._rect = rect

    def rectangle(self) -> _Rect:
        return self._rect


def _operator(live: bool = True):
    op = hdft.HbsysOperator(live=live, pause=0.0, confirm_each=False)
    op.log_action = lambda message: None
    op.guard_blind_input = lambda action: True
    return op


class _EditControl:
    """A fake HBSys Edit control (the Hospital No. field)."""

    def __init__(self, control_id: int = 1004, text: str = "",
                 class_name: str = "Edit", visible: bool = True):
        self._control_id = control_id
        self._text = text
        self._class_name = class_name
        self._visible = visible

    def class_name(self) -> str:
        return self._class_name

    def control_id(self) -> int:
        return self._control_id

    def is_visible(self) -> bool:
        return self._visible

    def window_text(self) -> str:
        return self._text


def _claim():
    return SimpleNamespace(
        patient_name="TOLENTINO, JUDEA PRAGATA",
        admission_grid="09/04/2026",
        discharge_grid="09/09/2026",
    )


class BeneficiariesWindowTextTests(unittest.TestCase):
    """The close check must see a spaced OCR title as the window."""

    def test_joined_title_is_detected(self):
        self.assertTrue(
            hdft.HbsysOperator.is_phic_beneficiaries_text(
                "PhilHealth Beneficiaries of GONZALES, AMARA"
            )
        )

    def test_spaced_title_is_detected(self):
        # The exact live-failure reading from the 2026-10-08 run.
        self.assertTrue(
            hdft.HbsysOperator.is_phic_beneficiaries_text(
                "PHIL HEALTH BENEFICIARIES OF SBALON, MYRNA DULY"
            )
        )

    def test_misread_first_word_is_detected(self):
        self.assertTrue(
            hdft.HbsysOperator.is_phic_beneficiaries_text(
                "PHILAEALTH BENEFICIARIES OF GONZALES, AMARA"
            )
        )

    def test_plain_hbsys_screen_is_not_the_window(self):
        self.assertFalse(
            hdft.HbsysOperator.is_phic_beneficiaries_text(
                "HBSys - Hospital Operations and Management "
                "Information System (HOMIS) Billing System "
                "Patient Record Form Window Quit"
            )
        )


class SafeResetTextTests(unittest.TestCase):
    """The reset proof must count a spaced title as the window."""

    def test_search_screen_without_window_is_safe(self):
        self.assertTrue(
            hdft.HbsysOperator.is_safe_reset_text(
                "HBSys Billing System Hospital No. Patient Record Form"
            )
        )

    def test_spaced_window_title_is_still_open(self):
        self.assertFalse(
            hdft.HbsysOperator.is_safe_reset_text(
                "HBSys Billing System Hospital No. "
                "PHIL HEALTH BENEFICIARIES OF SBALON, MYRNA DULY"
            )
        )

    def test_window_without_search_field_is_not_safe(self):
        self.assertFalse(
            hdft.HbsysOperator.is_safe_reset_text(
                "PHIL HEALTH BENEFICIARIES OF GONZALES, AMARA"
            )
        )


class StaleBeneficiariesWindowTests(unittest.TestCase):
    """A Beneficiaries window from the previous patient must be spotted."""

    @staticmethod
    def _items(text: str):
        return [hdft.OcrItem(text, 0.9, 10, 10)]

    def _shows_other(self, text: str, patient_name: str):
        op = _operator()
        with mock.patch.object(
            hdft, "read_ocr_items", return_value=self._items(text)
        ):
            return op.beneficiaries_window_shows_other_patient(
                Path("capture.png"),
                SimpleNamespace(patient_name=patient_name),
            )

    def test_previous_patient_window_is_stale(self):
        # TUTAAN's claim against SIBALON's still-open window:
        # identical confinement dates, different patient.
        self.assertTrue(
            self._shows_other(
                "PHIL HEALTH BENEFICIARIES OF SIBALON, MYRNA DULAY "
                "SIBALON MYRNA DULAY TOTAL DAYS CLAIMED",
                "TUTAAN, ZENAIDA CASIMINA",
            )
        )

    def test_current_patient_window_is_not_stale(self):
        self.assertFalse(
            self._shows_other(
                "PHIL HEALTH BENEFICIARIES OF TOLENTINO, JUDEA "
                "PRAGATA TOLENTINO JUDEA PRAGATA",
                "TOLENTINO, JUDEA PRAGATA",
            )
        )

    def test_screen_without_the_window_is_not_stale(self):
        self.assertFalse(
            self._shows_other(
                "HBSys Billing System Hospital No. Patient Record Form",
                "TUTAAN, ZENAIDA CASIMINA",
            )
        )


class StaleWindowSelfHealTests(unittest.TestCase):
    """The PHIC step must close a stale window and reopen it once."""

    def _op(self):
        op = _operator()
        op.hbsys_window = _Window(_Rect(left=10, top=20))
        op.dismiss_claim_form4_after_phic_if_visible = mock.Mock(
            return_value=True
        )
        op.close_phic_beneficiaries = mock.Mock()
        op.find_phic_beneficiary_row_y_from_variants = mock.Mock(
            return_value=200.0
        )
        op.is_blue_highlighted_row = mock.Mock(return_value=True)
        return op

    def test_stale_window_is_closed_and_reopened(self):
        op = self._op()
        op.capture_window = mock.Mock(
            side_effect=[Path("select.png"), Path("select_retry.png")]
        )
        stale = [
            hdft.OcrItem(
                "PHIL HEALTH BENEFICIARIES OF SIBALON, MYRNA DULAY",
                0.9,
                10,
                10,
            )
        ]
        with mock.patch.object(
            hdft, "read_ocr_items", return_value=stale
        ), mock.patch.object(
            hdft, "read_ocr_item_variants", return_value=[[]]
        ), mock.patch.object(
            hdft, "sleep_short"
        ), mock.patch.object(
            hdft.pyautogui, "click"
        ) as click:
            result = op.click_phic_and_select_claim(_claim())
        self.assertTrue(result)
        op.close_phic_beneficiaries.assert_called_once_with(
            "Close stale PhilHealth Beneficiaries window"
        )
        # PHIC clicked twice: the initial open and the reopen.
        self.assertEqual(
            [call.args for call in click.call_args_list],
            [
                (hdft.P.PHIC.x, hdft.P.PHIC.y),
                (hdft.P.PHIC.x, hdft.P.PHIC.y),
            ],
        )
        self.assertEqual(op.capture_window.call_count, 2)

    def test_current_window_is_not_reopened(self):
        op = self._op()
        op.capture_window = mock.Mock(side_effect=[Path("select.png")])
        current = [
            hdft.OcrItem(
                "PHIL HEALTH BENEFICIARIES OF TOLENTINO, JUDEA PRAGATA",
                0.9,
                10,
                10,
            )
        ]
        with mock.patch.object(
            hdft, "read_ocr_items", return_value=current
        ), mock.patch.object(
            hdft, "read_ocr_item_variants", return_value=[[]]
        ), mock.patch.object(
            hdft, "sleep_short"
        ), mock.patch.object(
            hdft.pyautogui, "click"
        ) as click:
            result = op.click_phic_and_select_claim(_claim())
        self.assertTrue(result)
        op.close_phic_beneficiaries.assert_not_called()
        # PHIC only: the row was already highlighted on the first,
        # current-patient capture.
        self.assertEqual(len(click.call_args_list), 1)


class SafeResetCloseRoundsTests(unittest.TestCase):
    """The reset retries the close instead of failing the batch."""

    def _reset(self, safe_results):
        op = _operator()
        op.screen_stage = "beneficiary"
        op.hbsys_window = _Window(_Rect(left=10, top=20))
        op.close_phic_beneficiaries = mock.Mock()
        op.dismiss_rate_validation_message = mock.Mock(return_value=True)
        op.is_safe_reset_text = mock.Mock(side_effect=safe_results)
        op.capture_window = mock.Mock(
            side_effect=[
                Path(f"safe_reset_proof_{index}.png")
                for index in range(len(safe_results))
            ]
        )
        with mock.patch.object(
            hdft, "find_admission_history_window", return_value=None
        ), mock.patch.object(
            hdft, "read_ocr_items", return_value=[]
        ), mock.patch.object(hdft, "sleep_short"), mock.patch.object(
            hdft, "Desktop"
        ) as desktop:
            desktop.return_value.windows.return_value = []
            result = op.reset_to_safe_start()
        return op, result

    def test_first_safe_proof_closes_once(self):
        op, result = self._reset([True])
        self.assertTrue(result)
        self.assertEqual(op.close_phic_beneficiaries.call_count, 1)
        self.assertEqual(op.audit["safe_reset_confirmed"], "YES")
        self.assertEqual(op.screen_stage, "base")

    def test_unsafe_first_proof_retries_the_close(self):
        # The 2026-10-08 case: the first proof still showed the
        # stale window. The reset must try the close again before
        # giving up, so one patient cannot terminate the batch.
        op, result = self._reset([False, True])
        self.assertTrue(result)
        self.assertEqual(op.close_phic_beneficiaries.call_count, 2)
        self.assertEqual(op.audit["safe_reset_confirmed"], "YES")

    def test_reset_fails_only_after_both_close_rounds(self):
        op, result = self._reset([False, False])
        self.assertFalse(result)
        self.assertEqual(op.close_phic_beneficiaries.call_count, 2)
        self.assertEqual(op.audit["safe_reset_confirmed"], "NO")
        self.assertEqual(op.screen_stage, "beneficiary")


class HospitalNumberEditTests(unittest.TestCase):
    """2026-10-09 live failure (COLOBONG, workflow runs 1 and 2).

    The patient HAD loaded into the Billing form, but the whole-window
    OCR read the hospital number 000000000010920 as ...10820, so the
    exact check reported "not visible" and the flow stopped for review on
    a patient that was already on screen. The Edit control's own text
    (pywinauto, no OCR) now decides, and a screen with no such field
    (the Patient Record Form, live failure DAYAG) is detected so the
    screen can be restored before anything is typed.
    """

    def _op_with_edit(self, text="", control_id=1004, class_name="Edit",
                      visible=True, children=None):
        op = _operator()
        window = _Window(_Rect(left=0, top=0))
        if children is None:
            children = [
                _EditControl(control_id=control_id, text=text,
                             class_name=class_name, visible=visible)
            ]
        window.children = lambda: children
        op.hbsys_window = window
        op.current_hbsys_text = mock.Mock(return_value="")
        return op

    def _claim_no(self, hospital_no="000000000010920"):
        return SimpleNamespace(hospital_no=hospital_no)

    def test_edit_text_accepts_the_exact_loaded_number(self):
        op = self._op_with_edit(text="000000000010920")
        self.assertTrue(op._hospital_number_visible(self._claim_no()))

    def test_edit_text_ignores_ocr_misreads(self):
        # The live case: OCR said ...10820 while the control reads the
        # true number. The control decides, so the patient is loaded.
        op = self._op_with_edit(text="000000000010920")
        op.current_hbsys_text = mock.Mock(
            return_value="Hospital No.: 000000000010820 COLOBONG"
        )
        self.assertTrue(op._hospital_number_visible(self._claim_no()))

    def test_edit_text_rejects_a_different_patient(self):
        # The stale-load guard still holds with the exact check: the
        # previous patient's number in the field must NOT pass.
        op = self._op_with_edit(text="000000000022197")
        self.assertFalse(op._hospital_number_visible(self._claim_no()))

    def test_empty_field_is_not_visible(self):
        op = self._op_with_edit(text="")
        self.assertFalse(op._hospital_number_visible(self._claim_no()))

    def test_other_edits_are_ignored(self):
        op = self._op_with_edit(children=[_EditControl(control_id=2002,
                                                       text="PAY")])
        self.assertIsNone(op.hospital_no_edit())
        self.assertEqual(op.hospital_no_edit_text(), "")

    def test_hidden_field_is_ignored(self):
        op = self._op_with_edit(text="000000000010920", visible=False)
        self.assertIsNone(op.hospital_no_edit())

    def test_other_class_is_ignored(self):
        op = self._op_with_edit(text="000000000010920",
                                class_name="Button")
        self.assertIsNone(op.hospital_no_edit())

    def test_falls_back_to_ocr_when_there_is_no_edit(self):
        # A build that hides the Edit control keeps the proven OCR path.
        op = self._op_with_edit(children=[])
        op.current_hbsys_text = mock.Mock(
            return_value="Hospital No.: 000000000010920 COLOBONG"
        )
        self.assertTrue(op._hospital_number_visible(self._claim_no()))

    def test_no_window_means_not_visible(self):
        op = _operator()
        op.hbsys_window = None
        self.assertFalse(op._hospital_number_visible(self._claim_no()))

    def test_ensure_screen_returns_true_when_the_field_is_present(self):
        op = self._op_with_edit(text="")
        with mock.patch.object(hdft.pyautogui, "hotkey") as hotkey:
            self.assertTrue(op.ensure_hospital_number_screen("test"))
        hotkey.assert_not_called()

    def test_ensure_screen_closes_the_form_until_the_field_is_back(self):
        # Patient Record Form -> ctrl+F4 restores the Billing form.
        edit = _EditControl(text="000000000010920")
        op = _operator()
        window = _Window(_Rect(left=0, top=0))
        state = {"with_field": False}
        window.children = lambda: [edit] if state["with_field"] else []
        op.hbsys_window = window
        def _focus():
            state["with_field"] = True
        op.focus_hbsys = mock.Mock(side_effect=_focus)
        with mock.patch.object(hdft.pyautogui, "hotkey") as hotkey, \
                mock.patch.object(hdft, "sleep_short"):
            self.assertTrue(op.ensure_hospital_number_screen("test"))
        hotkey.assert_called_once_with("ctrl", "f4")

    def test_ensure_screen_gives_up_after_the_rounds(self):
        op = self._op_with_edit(children=[])
        op.focus_hbsys = mock.Mock()
        with mock.patch.object(hdft.pyautogui, "hotkey") as hotkey, \
                mock.patch.object(hdft, "sleep_short"):
            self.assertFalse(op.ensure_hospital_number_screen("test"))
        self.assertEqual(
            hotkey.call_count, hdft.P.SCREEN_RESTORE_ROUNDS - 1
        )

    def test_ensure_screen_never_types_and_stops_the_search(self):
        # DAYAG: no form with the field can be restored, so the search
        # must NOT type into an unknown form; the caller stops safely.
        op = self._op_with_edit(children=[])
        op.focus_hbsys = mock.Mock()
        claim = self._claim_no()
        with mock.patch.object(hdft.pyautogui, "hotkey"), \
                mock.patch.object(hdft, "sleep_short"), \
                mock.patch.object(hdft.pyautogui, "doubleClick") as dclick, \
                mock.patch.object(hdft.pyautogui, "write") as write:
            op.search_hospital_number(claim)
        dclick.assert_not_called()
        write.assert_not_called()

    def test_search_types_once_the_field_is_available(self):
        op = self._op_with_edit(text="")
        with mock.patch.object(hdft.pyautogui, "doubleClick") as dclick, \
                mock.patch.object(hdft.pyautogui, "hotkey"), \
                mock.patch.object(hdft.pyautogui, "write") as write, \
                mock.patch.object(hdft.pyautogui, "press"), \
                mock.patch.object(hdft, "sleep_short"):
            op.search_hospital_number(self._claim_no())
        dclick.assert_called_once_with(
            hdft.P.HOSPITAL_NO.x, hdft.P.HOSPITAL_NO.y
        )
        write.assert_called_once()
        self.assertEqual(write.call_args.args[0], "000000000010920")

    def test_ensure_screen_is_a_no_op_in_dry_run(self):
        op = _operator(live=False)
        op.hbsys_window = _Window(_Rect(left=0, top=0))
        with mock.patch.object(hdft.pyautogui, "hotkey") as hotkey:
            self.assertTrue(op.ensure_hospital_number_screen("test"))
        hotkey.assert_not_called()


class EndOfRunCloseFormTests(unittest.TestCase):
    """The run ends with a clean HBSys screen for the next node.

    Operator rule 2026-10-08: after the last Date Fill patient,
    click the Close Form slot (X:434 Y:60) so a form left open
    cannot block the next workflow node (Final Bill follows Date
    Fill). On an already-clean screen the slot is the Admit
    History toolbar button, so the click must NOT happen there.
    """

    def _op(self):
        op = _operator()
        op.hbsys_window = _Window(_Rect(left=0, top=0))
        return op

    def test_clicks_close_form_when_a_form_is_still_open(self):
        op = self._op()
        op.current_hbsys_text = mock.Mock(
            return_value=(
                "HBSys Billing System "
                "PHIL HEALTH BENEFICIARIES OF GONZALES, AMARA"
            )
        )
        with mock.patch.object(hdft, "sleep_short"), \
                mock.patch.object(hdft.pyautogui, "click") as click:
            op.close_open_form_at_run_end()
        self.assertEqual(
            (
                hdft.P.CLOSE_FORM_END_OF_RUN.x,
                hdft.P.CLOSE_FORM_END_OF_RUN.y,
            ),
            (434, 60),
        )
        self.assertEqual(
            [call.args for call in click.call_args_list],
            [(434, 60)],
        )

    def test_no_click_when_the_screen_is_already_base(self):
        # The slot is the Admit History toolbar button on the
        # bare screen — clicking it would open the popup.
        op = self._op()
        op.current_hbsys_text = mock.Mock(
            return_value=(
                "HBSys Billing System Hospital No. Patient Record Form"
            )
        )
        with mock.patch.object(hdft, "sleep_short"), \
                mock.patch.object(hdft.pyautogui, "click") as click:
            op.close_open_form_at_run_end()
        click.assert_not_called()

    def test_never_clicks_in_dry_run(self):
        op = _operator(live=False)
        op.hbsys_window = _Window(_Rect(left=0, top=0))
        op.current_hbsys_text = mock.Mock(
            return_value="PHIL HEALTH BENEFICIARIES OF GONZALES, AMARA"
        )
        with mock.patch.object(hdft.pyautogui, "click") as click:
            op.close_open_form_at_run_end()
        click.assert_not_called()

    def test_no_click_without_an_hbsys_window(self):
        op = self._op()
        op.hbsys_window = None
        with mock.patch.object(hdft.pyautogui, "click") as click:
            op.close_open_form_at_run_end()
        click.assert_not_called()


class AdmissionHistoryReloadTests(unittest.TestCase):
    """2026-10-08: a stale-popup miss reloads the patient once.

    Live failure (workflow run 1): the Admit History popup opened
    against the PREVIOUS patient while the hospital-number load was
    still settling, so the folder match failed on a healthy patient.
    One reload + reopen heals it; a real mismatch still stops.
    """

    @staticmethod
    def _claim():
        return SimpleNamespace(
            patient_name="TOLENTINO, JUDEA PRAGATA",
            admission_grid="09/04/2026",
            discharge_grid="09/09/2026",
            hospital_no="000000000022155",
        )

    def _operator_with_mocks(self, row_y_side_effect):
        op = _operator()
        op.click = mock.Mock()
        op.focus_hbsys = mock.Mock()
        op.search_hospital_number = mock.Mock()
        op._close_admission_history_popup = mock.Mock()
        op._hospital_number_visible = mock.Mock(return_value=True)
        op.capture_window = mock.Mock(
            side_effect=lambda *a, **k: Path(f"{a[-1]}.png")
        )
        op.find_exact_admission_history_row_y = mock.Mock(
            side_effect=row_y_side_effect
        )
        op.dismiss_rate_validation_message = mock.Mock(return_value=True)
        return op

    def test_stale_popup_self_heals_on_reload(self):
        op = self._operator_with_mocks([None, 200.0])
        with mock.patch.object(
            hdft, "find_admission_history_window",
            return_value=_Window(_Rect(left=116, top=120)),
        ), mock.patch.object(
            hdft, "read_ocr_item_variants", return_value=[[]]
        ), mock.patch.object(
            hdft, "read_focused_admission_row_variants", return_value=[]
        ), mock.patch.object(
            hdft, "parse_rows_with_positions", return_value=[]
        ), mock.patch.object(
            hdft, "sleep_short"
        ), mock.patch.object(
            hdft.pyautogui, "doubleClick"
        ) as double_click:
            self.assertTrue(op.select_admission_history_row(self._claim()))
        self.assertEqual(op.search_hospital_number.call_count, 1)
        double_click.assert_called_once_with(188, 320)

    def test_genuine_mismatch_stops_after_the_reload(self):
        op = self._operator_with_mocks([None, None])
        with mock.patch.object(
            hdft, "find_admission_history_window",
            return_value=_Window(_Rect(left=116, top=120)),
        ), mock.patch.object(
            hdft, "read_ocr_item_variants", return_value=[[]]
        ), mock.patch.object(
            hdft, "read_focused_admission_row_variants", return_value=[]
        ), mock.patch.object(
            hdft, "parse_rows_with_positions", return_value=[]
        ), mock.patch.object(
            hdft, "sleep_short"
        ):
            self.assertFalse(op.select_admission_history_row(self._claim()))
        self.assertEqual(op.search_hospital_number.call_count, 1)

    def test_popup_that_never_opens_is_reloaded_and_retried(self):
        op = self._operator_with_mocks([200.0])
        windows = iter([None, None, None, _Window(_Rect(left=116, top=120))])
        with mock.patch.object(
            hdft, "find_admission_history_window",
            side_effect=lambda: next(windows),
        ), mock.patch.object(
            hdft, "read_ocr_item_variants", return_value=[[]]
        ), mock.patch.object(
            hdft, "read_focused_admission_row_variants", return_value=[]
        ), mock.patch.object(
            hdft, "parse_rows_with_positions", return_value=[]
        ), mock.patch.object(
            hdft, "sleep_short"
        ), mock.patch.object(
            hdft.pyautogui, "press"
        ), mock.patch.object(
            hdft.pyautogui, "doubleClick"
        ):
            self.assertTrue(op.select_admission_history_row(self._claim()))
        self.assertEqual(op.search_hospital_number.call_count, 1)

    def test_reload_stops_when_the_popup_will_not_close(self):
        # 2026-10-09: typing the hospital number into a
        # still-open popup never switches the patient.
        op = self._operator_with_mocks([None])
        op._close_admission_history_popup = mock.Mock(return_value=False)
        with mock.patch.object(
            hdft, "find_admission_history_window",
            return_value=_Window(_Rect(left=116, top=120)),
        ), mock.patch.object(
            hdft, "read_ocr_item_variants", return_value=[[]]
        ), mock.patch.object(
            hdft, "read_focused_admission_row_variants", return_value=[]
        ), mock.patch.object(
            hdft, "parse_rows_with_positions", return_value=[]
        ), mock.patch.object(
            hdft, "sleep_short"
        ):
            self.assertFalse(op.select_admission_history_row(self._claim()))
        self.assertEqual(op.search_hospital_number.call_count, 0)

    def test_reload_stops_when_the_patient_never_loads(self):
        op = self._operator_with_mocks([None])
        op._hospital_number_visible = mock.Mock(
            side_effect=[True, False]
        )
        with mock.patch.object(
            hdft, "find_admission_history_window",
            return_value=_Window(_Rect(left=116, top=120)),
        ), mock.patch.object(
            hdft, "read_ocr_item_variants", return_value=[[]]
        ), mock.patch.object(
            hdft, "read_focused_admission_row_variants", return_value=[]
        ), mock.patch.object(
            hdft, "parse_rows_with_positions", return_value=[]
        ), mock.patch.object(
            hdft, "sleep_short"
        ):
            self.assertFalse(op.select_admission_history_row(self._claim()))
        self.assertEqual(op.search_hospital_number.call_count, 1)

    def test_first_pass_researches_when_the_number_is_not_visible(self):
        op = self._operator_with_mocks([200.0])
        op._hospital_number_visible = mock.Mock(
            side_effect=[False, True]
        )
        with mock.patch.object(
            hdft, "find_admission_history_window",
            return_value=_Window(_Rect(left=116, top=120)),
        ), mock.patch.object(
            hdft, "read_ocr_item_variants", return_value=[[]]
        ), mock.patch.object(
            hdft, "read_focused_admission_row_variants", return_value=[]
        ), mock.patch.object(
            hdft, "parse_rows_with_positions", return_value=[]
        ), mock.patch.object(
            hdft, "sleep_short"
        ), mock.patch.object(
            hdft.pyautogui, "doubleClick"
        ):
            self.assertTrue(op.select_admission_history_row(self._claim()))
        self.assertEqual(op.search_hospital_number.call_count, 1)

    def _visible_operator(self, text):
        op = _operator()
        op.hbsys_window = _Window(_Rect(left=0, top=0))
        op.current_hbsys_text = mock.Mock(return_value=text)
        return op

    def test_hospital_number_visible_accepts_the_claim_number(self):
        op = self._visible_operator(
            "HBSys Hospital No.: 000000000022155 CORTEZ, MARRY ANN"
        )
        claim = SimpleNamespace(hospital_no="000000000022155")
        self.assertTrue(op._hospital_number_visible(claim))

    def test_hospital_number_visible_tolerates_ocr_digits(self):
        op = self._visible_operator(
            "Hospital No.: OOOOOOOOOO22I55 CORTEZ"
        )
        claim = SimpleNamespace(hospital_no="000000000022155")
        self.assertTrue(op._hospital_number_visible(claim))

    def test_hospital_number_visible_rejects_another_patient(self):
        # 2026-10-09: BULAN's record was still loaded at
        # CORTEZ's stop; the check must reject it.
        op = self._visible_operator(
            "Hospital No.: 000000000022197 BULAN, JULIANA"
        )
        claim = SimpleNamespace(hospital_no="000000000022155")
        self.assertFalse(op._hospital_number_visible(claim))

    def test_hospital_number_not_visible_without_an_hbsys_window(self):
        op = _operator()
        op.hbsys_window = None
        claim = SimpleNamespace(hospital_no="000000000022155")
        self.assertFalse(op._hospital_number_visible(claim))

    def test_close_helper_closes_a_leftover_popup(self):
        op = _operator()
        window = _Window(_Rect(left=1, top=2))
        window.close = mock.Mock()
        with mock.patch.object(
            hdft, "find_admission_history_window",
            side_effect=[window, None],
        ), mock.patch.object(hdft, "sleep_short"), mock.patch.object(
            hdft.pyautogui, "press"
        ) as press:
            self.assertTrue(op._close_admission_history_popup())
        window.close.assert_called_once_with()
        press.assert_not_called()

    def test_close_helper_falls_back_to_esc(self):
        op = _operator()
        window = _Window(_Rect(left=1, top=2))
        window.set_focus = mock.Mock()

        def _boom():
            raise RuntimeError("close failed")

        window.close = _boom
        with mock.patch.object(
            hdft, "find_admission_history_window",
            side_effect=[window, None],
        ), mock.patch.object(hdft, "sleep_short"), mock.patch.object(
            hdft.pyautogui, "press"
        ) as press:
            self.assertTrue(op._close_admission_history_popup())
        window.set_focus.assert_called_once_with()
        press.assert_called_once_with("esc")

    def test_close_helper_reports_a_popup_that_will_not_close(self):
        op = _operator()
        window = _Window(_Rect(left=1, top=2))
        window.set_focus = mock.Mock()

        def _boom():
            raise RuntimeError("close failed")

        window.close = _boom
        with mock.patch.object(
            hdft, "find_admission_history_window", return_value=window
        ), mock.patch.object(hdft, "sleep_short"), mock.patch.object(
            hdft.pyautogui, "press"
        ):
            self.assertFalse(op._close_admission_history_popup())


if __name__ == "__main__":
    unittest.main(verbosity=2)
