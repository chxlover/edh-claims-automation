"""Regression tests: agent-run Date Fill confinement selection must behave like
the proven Date Fill ABTC/Regular tool (the reference the owner asked to copy).

Evidence being locked down (logs + screenshots, 2026-09-26/28):
* ``_confinement_row`` dropped ``rect.top`` from the click point, so the
  double-click landed on the toolbar (y=98 instead of y=218), the Admission
  History popup never closed, and the PHIC step ran on the wrong screen;
* a single OCR pass dropped one of two grid rows and stopped the claim;
* the PHIC row click was blind (x=46, no proof) instead of the ABTC/Regular
  x=260/520 clicks with blue-highlight proof and 2-pass consensus.

Hermetic: captures are mocked or written to temp dirs — the real ``logs/``
tree is never touched.
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
    import hbsys_fill_dates as hdf
except Exception as exc:  # pragma: no cover - optional GUI/OCR deps
    raise unittest.SkipTest(f"Date Fill not importable: {exc}")


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


def _parsed(admission: str, discharge: str, y: float, encounter: str = "ADMIT"):
    return SimpleNamespace(
        row=SimpleNamespace(
            admission_date=admission,
            discharge_date=discharge,
            normalized_encounter_type=encounter,
        ),
        y=float(y),
    )


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


def _operator(live: bool = True):
    op = hdf.HbsysOperator(live=live, pause=0.0, confirm_each=False)
    op.log_action = lambda message: None
    op.guard_blind_input = lambda action: True
    return op


class ConfinementPointTests(unittest.TestCase):
    """The 2026-09-26/28 bug: y lacked rect.top, so the pick hit the toolbar."""

    def test_point_is_screen_absolute(self):
        op = _operator()
        window = _Window(_Rect(left=116, top=120))
        row = op._confinement_row(window, _parsed("09/04/2026", "09/09/2026", 98))
        self.assertEqual(row.point, (188, 218))  # left+72, top+98


class MergedOcrPassTests(unittest.TestCase):
    """One weak OCR pass must not hide the row the folder needs."""

    def test_rows_from_every_pass_are_merged_without_duplicates(self):
        op = _operator(live=False)
        op.capture_window = mock.Mock(
            return_value=Path(tempfile.gettempdir()) / "unused_capture.png"
        )
        wanted = _parsed("09/18/2026", "09/22/2026", 86)
        other = _parsed("02/06/2025", "02/09/2025", 113)
        duplicate = _parsed("09/18/2026", "09/22/2026", 88)
        window = _Window(_Rect(left=116, top=120))
        with mock.patch.object(
            hdf, "read_ocr_item_variants", return_value=[[other], [wanted]]
        ), mock.patch.object(
            hdf,
            "read_focused_admission_row_variants",
            return_value=[[duplicate]],
        ), mock.patch.object(
            hdf, "parse_rows_with_positions", side_effect=lambda items: list(items)
        ):
            rows = op._read_confinement_rows(window)
        pairs = [(row.admission_grid, row.discharge_grid) for row in rows]
        self.assertEqual(
            pairs,
            [("02/06/2025", "02/09/2025"), ("09/18/2026", "09/22/2026")],
        )
        self.assertEqual(rows[1].point, (116 + 72, 120 + 86))


class AdmissionHistoryCloseTests(unittest.TestCase):
    """The popup must consume the pick; otherwise PHIC runs on the wrong screen."""

    def _select(self, timeout: float, window_value):
        op = _operator()
        op.focus_hbsys = mock.Mock()
        op._hospital_number_visible = mock.Mock(return_value=True)
        claim = SimpleNamespace(
            admission_grid="09/04/2026",
            discharge_grid="09/09/2026",
            hospital_no="000000000022155",
        )
        with mock.patch.object(
            hdf, "select_confinement_row", return_value=True
        ), mock.patch.object(
            hdf, "find_admission_history_window", return_value=window_value
        ), mock.patch.object(hdf, "ADMIT_HISTORY_CLOSE_TIMEOUT", timeout):
            return op.select_admission_history_row(claim)

    def test_proceeds_when_the_popup_closed(self):
        self.assertTrue(self._select(0.05, None))

    def test_stops_when_the_popup_is_still_open(self):
        self.assertFalse(self._select(0.05, _Window(_Rect(left=1, top=2))))


class AdmissionHistoryReloadTests(unittest.TestCase):
    """2026-10-08: a stale-popup miss reloads the patient once.

    Live failure (workflow run 1): the Admit History popup opened
    against the PREVIOUS patient while the hospital-number load was
    still settling, so the folder match failed on a healthy patient.
    One reload + reopen heals it; a real mismatch still stops.

    2026-10-09 (CORTEZ, MENESES): the reload's re-typed hospital
    number never switched the loaded patient, so the second popup
    pass read the same wrong rows. The reload now verifies the
    popup closed, re-focuses HBSys, and requires the searched
    Hospital No. on the base screen before any row is trusted.
    """

    @staticmethod
    def _claim():
        return SimpleNamespace(
            admission_grid="09/04/2026",
            discharge_grid="09/09/2026",
            hospital_no="000000000022155",
        )

    def _reload_operator(self, select_side_effect):
        op = _operator()
        op.search_hospital_number = mock.Mock()
        op.focus_hbsys = mock.Mock()
        op._close_admission_history_popup = mock.Mock()
        op._hospital_number_visible = mock.Mock(return_value=True)
        return op, select_side_effect

    def test_stale_popup_self_heals_on_reload(self):
        op, _ = self._reload_operator([False, True])
        with mock.patch.object(
            hdf, "select_confinement_row", side_effect=[False, True]
        ) as select, mock.patch.object(
            hdf, "find_admission_history_window", return_value=None
        ), mock.patch.object(hdf, "sleep_short"):
            self.assertTrue(op.select_admission_history_row(self._claim()))
        self.assertEqual(select.call_count, 2)
        self.assertEqual(op.search_hospital_number.call_count, 1)

    def test_genuine_mismatch_stops_after_the_reload(self):
        op, _ = self._reload_operator([False, False])
        with mock.patch.object(
            hdf, "select_confinement_row", return_value=False
        ) as select, mock.patch.object(
            hdf, "find_admission_history_window", return_value=None
        ), mock.patch.object(hdf, "sleep_short"):
            self.assertFalse(op.select_admission_history_row(self._claim()))
        self.assertEqual(select.call_count, 2)
        self.assertEqual(op.search_hospital_number.call_count, 1)

    def test_popup_never_opening_is_retried_not_raised(self):
        op, _ = self._reload_operator(None)

        def _raise(*args, **kwargs):
            raise RuntimeError("Admission History popup did not open.")

        with mock.patch.object(
            hdf, "select_confinement_row", side_effect=_raise
        ) as select, mock.patch.object(
            hdf, "find_admission_history_window", return_value=None
        ), mock.patch.object(hdf, "sleep_short"):
            self.assertFalse(op.select_admission_history_row(self._claim()))
        self.assertEqual(select.call_count, 2)

    def test_reload_stops_when_the_popup_will_not_close(self):
        # 2026-10-09: typing the hospital number into a
        # still-open popup never switches the patient.
        op, _ = self._reload_operator([False])
        op._close_admission_history_popup = mock.Mock(return_value=False)
        with mock.patch.object(
            hdf, "select_confinement_row", return_value=False
        ) as select, mock.patch.object(
            hdf, "find_admission_history_window", return_value=None
        ), mock.patch.object(hdf, "sleep_short"):
            self.assertFalse(op.select_admission_history_row(self._claim()))
        self.assertEqual(select.call_count, 1)
        self.assertEqual(op.search_hospital_number.call_count, 0)

    def test_reload_stops_when_the_patient_never_loads(self):
        op, _ = self._reload_operator([False])
        op._hospital_number_visible = mock.Mock(
            side_effect=[True, False]
        )
        with mock.patch.object(
            hdf, "select_confinement_row", return_value=False
        ) as select, mock.patch.object(
            hdf, "find_admission_history_window", return_value=None
        ), mock.patch.object(hdf, "sleep_short"):
            self.assertFalse(op.select_admission_history_row(self._claim()))
        self.assertEqual(select.call_count, 1)
        self.assertEqual(op.search_hospital_number.call_count, 1)

    def test_first_pass_researches_when_the_number_is_not_visible(self):
        op, _ = self._reload_operator([True])
        op._hospital_number_visible = mock.Mock(
            side_effect=[False, True]
        )
        with mock.patch.object(
            hdf, "select_confinement_row", return_value=True
        ) as select, mock.patch.object(
            hdf, "find_admission_history_window", return_value=None
        ), mock.patch.object(hdf, "sleep_short"):
            self.assertTrue(op.select_admission_history_row(self._claim()))
        self.assertEqual(select.call_count, 1)
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
        self.assertTrue(op._hospital_number_visible(self._claim()))

    def test_hospital_number_visible_tolerates_ocr_digits(self):
        op = self._visible_operator(
            "Hospital No.: OOOOOOOOOO22I55 CORTEZ"
        )
        self.assertTrue(op._hospital_number_visible(self._claim()))

    def test_hospital_number_visible_rejects_another_patient(self):
        # 2026-10-09: BULAN's record was still loaded at
        # CORTEZ's stop; the check must reject it.
        op = self._visible_operator(
            "Hospital No.: 000000000022197 BULAN, JULIANA"
        )
        self.assertFalse(op._hospital_number_visible(self._claim()))

    def test_hospital_number_not_visible_without_an_hbsys_window(self):
        op = _operator()
        op.hbsys_window = None
        self.assertFalse(op._hospital_number_visible(self._claim()))

    def test_close_helper_closes_a_leftover_popup(self):
        op = _operator()
        window = _Window(_Rect(left=1, top=2))
        window.close = mock.Mock()
        with mock.patch.object(
            hdf, "find_admission_history_window",
            side_effect=[window, None],
        ), mock.patch.object(hdf, "sleep_short"), mock.patch.object(
            hdf.pyautogui, "press"
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
            hdf, "find_admission_history_window",
            side_effect=[window, None],
        ), mock.patch.object(hdf, "sleep_short"), mock.patch.object(
            hdf.pyautogui, "press"
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
            hdf, "find_admission_history_window", return_value=window
        ), mock.patch.object(hdf, "sleep_short"), mock.patch.object(
            hdf.pyautogui, "press"
        ):
            self.assertFalse(op._close_admission_history_popup())


class HospitalNumberEditTests(unittest.TestCase):
    """2026-10-09 live failures (COLOBONG + DAYAG) — production twin.

    COLOBONG: the patient HAD loaded into the Billing form, but the
    whole-window OCR read the hospital number 000000000010920 as
    ...10820, so the exact check said "not visible" and the flow stopped
    for review on a patient already on screen. DAYAG: the flow had ended
    on the Patient Record Form, which has no Hospital No. field, so the
    search typed nowhere and the probe kept showing the previous patient.
    The Edit control's own text now decides, and a missing field is
    detected so the screen can be restored before anything is typed.
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
        with mock.patch.object(hdf.pyautogui, "hotkey") as hotkey:
            self.assertTrue(op.ensure_hospital_number_screen("test"))
        hotkey.assert_not_called()

    def test_ensure_screen_closes_the_form_until_the_field_is_back(self):
        edit = _EditControl(text="000000000010920")
        op = _operator()
        window = _Window(_Rect(left=0, top=0))
        state = {"with_field": False}
        window.children = lambda: [edit] if state["with_field"] else []
        op.hbsys_window = window

        def _focus():
            state["with_field"] = True

        op.focus_hbsys = mock.Mock(side_effect=_focus)
        with mock.patch.object(hdf.pyautogui, "hotkey") as hotkey, \
                mock.patch.object(hdf, "sleep_short"):
            self.assertTrue(op.ensure_hospital_number_screen("test"))
        hotkey.assert_called_once_with("ctrl", "f4")

    def test_ensure_screen_gives_up_after_the_rounds(self):
        op = self._op_with_edit(children=[])
        op.focus_hbsys = mock.Mock()
        with mock.patch.object(hdf.pyautogui, "hotkey") as hotkey, \
                mock.patch.object(hdf, "sleep_short"):
            self.assertFalse(op.ensure_hospital_number_screen("test"))
        self.assertEqual(
            hotkey.call_count, hdf.P.SCREEN_RESTORE_ROUNDS - 1
        )

    def test_ensure_screen_never_types_and_stops_the_search(self):
        op = self._op_with_edit(children=[])
        op.focus_hbsys = mock.Mock()
        with mock.patch.object(hdf.pyautogui, "hotkey"), \
                mock.patch.object(hdf, "sleep_short"), \
                mock.patch.object(hdf.pyautogui, "doubleClick") as dclick, \
                mock.patch.object(hdf.pyautogui, "write") as write:
            op.search_hospital_number(self._claim_no())
        dclick.assert_not_called()
        write.assert_not_called()

    def test_search_types_once_the_field_is_available(self):
        op = self._op_with_edit(text="")
        with mock.patch.object(hdf.pyautogui, "doubleClick") as dclick, \
                mock.patch.object(hdf.pyautogui, "hotkey"), \
                mock.patch.object(hdf.pyautogui, "write") as write, \
                mock.patch.object(hdf.pyautogui, "press"), \
                mock.patch.object(hdf, "sleep_short"):
            op.search_hospital_number(self._claim_no())
        dclick.assert_called_once_with(
            hdf.P.HOSPITAL_NO.x, hdf.P.HOSPITAL_NO.y
        )
        write.assert_called_once()
        self.assertEqual(write.call_args.args[0], "000000000010920")

    def test_ensure_screen_is_a_no_op_in_dry_run(self):
        op = _operator(live=False)
        op.hbsys_window = _Window(_Rect(left=0, top=0))
        with mock.patch.object(hdf.pyautogui, "hotkey") as hotkey:
            self.assertTrue(op.ensure_hospital_number_screen("test"))
        hotkey.assert_not_called()


class PhicProofSelectionTests(unittest.TestCase):
    """x=260/520 clicks with blue-highlight proof, copied from ABTC/Regular."""

    @staticmethod
    def _claim():
        return SimpleNamespace(
            patient_name="TOLENTINO, JUDEA PRAGATA",
            admission_grid="09/04/2026",
            discharge_grid="09/09/2026",
        )

    def test_two_attempts_without_proof_stop(self):
        op = _operator()
        op.hbsys_window = _Window(_Rect(left=10, top=20))
        op.dismiss_claim_form4_after_phic_if_visible = mock.Mock(return_value=True)
        op.capture_window = mock.Mock(
            side_effect=lambda *a, **k: Path(f"{a[-1]}.png")
        )
        op.find_phic_beneficiary_row_y_from_variants = mock.Mock(
            side_effect=lambda *a, **k: 200.0
        )
        op.is_blue_highlighted_row = mock.Mock(return_value=False)
        with mock.patch.object(hdf, "read_ocr_item_variants", return_value=[[]]), \
                mock.patch.object(hdf, "read_ocr_items", return_value=[]), \
                mock.patch.object(hdf, "sleep_short"), \
                mock.patch.object(hdf.pyautogui, "click") as click:
            result = op.click_phic_and_select_claim(self._claim())
        self.assertFalse(result)
        self.assertEqual(
            [call.args for call in click.call_args_list],
            [
                (hdf.P.PHIC.x, hdf.P.PHIC.y),
                (270, 220),  # left+260, top+200
                (400, 220),  # left+390 middle (Option A, 2026-10-06)
                (530, 220),  # left+520 retry
            ],
        )

    def test_first_highlighted_attempt_wins(self):
        op = _operator()
        op.hbsys_window = _Window(_Rect(left=10, top=20))
        op.dismiss_claim_form4_after_phic_if_visible = mock.Mock(return_value=True)
        op.capture_window = mock.Mock(
            side_effect=[Path("select.png"), Path("proof1.png")]
        )
        op.find_phic_beneficiary_row_y_from_variants = mock.Mock(
            side_effect=[200.0, 200.0]
        )
        op.is_blue_highlighted_row = mock.Mock(return_value=True)
        with mock.patch.object(hdf, "read_ocr_item_variants", return_value=[[]]), \
                mock.patch.object(hdf, "read_ocr_items", return_value=[]), \
                mock.patch.object(hdf, "sleep_short"), \
                mock.patch.object(hdf.pyautogui, "click") as click:
            result = op.click_phic_and_select_claim(self._claim())
        self.assertTrue(result)
        # The 2026-10-07 rule: when the claim row is ALREADY
        # highlighted, no row click may happen (clicking moved
        # the highlight OFF the correct row in the live failure).
        self.assertEqual(len(click.call_args_list), 1)  # PHIC only


class PhicConsensusTests(unittest.TestCase):
    """Row-position consensus needs two agreeing OCR passes (ABTC/Regular rule)."""

    def _match(self, side_effect, **kwargs):
        op = _operator()
        op.find_phic_beneficiary_row_y = mock.Mock(side_effect=side_effect)
        return op.find_phic_beneficiary_row_y_from_variants(
            [[], [], []],
            "09/04/2026",
            "09/09/2026",
            "TOLENTINO, JUDEA PRAGATA",
            **kwargs,
        )

    def test_single_pass_is_not_enough_with_minimum_consensus_two(self):
        self.assertIsNone(self._match([200.0, None, None], minimum_consensus=2))

    def test_two_agreeing_passes_select_the_row(self):
        self.assertAlmostEqual(
            self._match([200.0, 204.0, None], minimum_consensus=2), 202.0
        )

    def test_default_still_accepts_a_single_pass(self):
        self.assertAlmostEqual(self._match([200.0, None, None]), 200.0)


class BlueHighlightProofTests(unittest.TestCase):
    """The clicked row only counts when the proof screenshot shows it blue."""

    @staticmethod
    def _image(band_color):
        from PIL import Image

        image = Image.new("RGB", (400, 240), (245, 245, 245))
        for y in range(100, 116):
            for x in range(399):
                image.putpixel((x, y), band_color)
        return image

    def test_blue_band_is_the_highlighted_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proof_blue.png"
            self._image((30, 60, 200)).save(path)
            self.assertTrue(hdf.HbsysOperator.is_blue_highlighted_row(path, 107.0))

    def test_gray_band_is_not_highlighted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proof_gray.png"
            self._image((128, 128, 128)).save(path)
            self.assertFalse(hdf.HbsysOperator.is_blue_highlighted_row(path, 107.0))


class DuplicateConfinementNameTests(unittest.TestCase):
    """One name token must not disambiguate two same-confinement rows."""

    @staticmethod
    def _row_items(y: float, name_texts: list[str]):
        items = [
            hdf.OcrItem("09/04/2026", 0.99, 60, y),
            hdf.OcrItem("09/09/2026", 0.99, 160, y),
        ]
        x = 260.0
        for text in name_texts:
            items.append(hdf.OcrItem(text, 0.99, x, y))
            x += 90
        return items

    def test_one_token_is_ambiguous(self):
        op = _operator()
        items = self._row_items(150, ["TOLENTINO"]) + self._row_items(
            170, ["DELA", "CRUZ"]
        )
        result = op.find_phic_beneficiary_row_y(
            items, "09/04/2026", "09/09/2026", "TOLENTINO, JUDEA PRAGATA"
        )
        self.assertIsNone(result)

    def test_two_tokens_pick_the_named_row(self):
        op = _operator()
        items = self._row_items(150, ["TOLENTINO", "JUDEA"]) + self._row_items(
            170, ["DELA", "CRUZ"]
        )
        result = op.find_phic_beneficiary_row_y(
            items, "09/04/2026", "09/09/2026", "TOLENTINO, JUDEA PRAGATA"
        )
        self.assertAlmostEqual(result, 150.0)


class FirstNameDisambiguationTests(unittest.TestCase):
    """Same last name + same confinement: the FIRST NAME must decide.

    Live case (2026-10-07): BALUNSAT, AMARA MARCELINE GONZALES (folder)
    vs BALUNSAT, KATE ARIANE GONZALES (PHIC row) -- the script picked the
    wrong sibling because the shared last name 'GONZALES' carried the match.
    """

    @staticmethod
    def _row_items(y: float, name_texts: list[str]):
        items = [
            hdf.OcrItem("09/23/2026", 0.99, 60, y),
            hdf.OcrItem("09/29/2026", 0.99, 160, y),
        ]
        x = 260.0
        for text in name_texts:
            items.append(hdf.OcrItem(text, 0.99, x, y))
            x += 90
        return items

    def test_wrong_first_name_is_rejected(self):
        op = _operator()
        # Both rows share confinement AND last name; only the first name differs.
        items = self._row_items(150, ["BALUNSAT", "KATE"]) + self._row_items(
            170, ["BALUNSAT", "AMARA"]
        )
        result = op.find_phic_beneficiary_row_y(
            items,
            "09/23/2026",
            "09/29/2026",
            "BALUNSAT, AMARA MARCELINE GONZALES",
        )
        # The wrong sibling (KATE) must NOT be selected.
        self.assertNotEqual(result, 150.0)

    def test_first_name_disambiguates_two_same_last_name(self):
        op = _operator()
        items = self._row_items(150, ["BALUNSAT", "KATE"]) + self._row_items(
            170, ["BALUNSAT", "AMARA"]
        )
        result = op.find_phic_beneficiary_row_y(
            items,
            "09/23/2026",
            "09/29/2026",
            "BALUNSAT, AMARA MARCELINE GONZALES",
        )
        self.assertAlmostEqual(result, 170.0)

    def test_missing_first_name_stops_for_review(self):
        op = _operator()
        # OCR reads only the shared last name on both rows -- no first name.
        items = self._row_items(150, ["BALUNSAT"]) + self._row_items(
            170, ["BALUNSAT"]
        )
        result = op.find_phic_beneficiary_row_y(
            items,
            "09/23/2026",
            "09/29/2026",
            "BALUNSAT, AMARA MARCELINE GONZALES",
        )
        self.assertIsNone(result)

    def test_fallback_does_not_select_wrong_sibling(self):
        """Admission+name fallback must not pick the wrong sibling.

        Live case: AMARA's discharge OCR misreads 09/29/2026 as 09/26/2026,
        so AMARA drops out of `candidates`. The fallback then sees KATE's row
        (admission 09/23/2026 + shared last name 'BALUNSAT') and -- before this
        fix -- returned KATE. The shared last name must NOT carry the fallback.
        """
        op = _operator()
        # KATE row: correct admission, wrong discharge, shared last name only.
        kate = self._row_items(150, ["BALUNSAT", "KATE"])
        kate[1] = hdf.OcrItem("09/26/2026", 0.99, 160, 150)  # discharge differs
        # AMARA row: correct admission, discharge MISREAD, first name readable.
        amara = self._row_items(170, ["BALUNSAT", "AMARA"])
        amara[1] = hdf.OcrItem("09/26/2026", 0.99, 160, 170)  # misread discharge
        result = op.find_phic_beneficiary_row_y(
            kate + amara,
            "09/23/2026",
            "09/29/2026",
            "BALUNSAT, AMARA MARCELINE GONZALES",
        )
        # KATE (y=150) must NOT be selected -- the wrong sibling.
        self.assertNotEqual(result, 150.0)

    def test_fallback_selects_correct_when_first_name_readable(self):
        """Same misread-discharge scenario, but AMARA's first name IS readable.

        The admission+name fallback should then pick AMARA (y=170).
        """
        op = _operator()
        kate = self._row_items(150, ["BALUNSAT", "KATE"])
        kate[1] = hdf.OcrItem("09/26/2026", 0.99, 160, 150)
        amara = self._row_items(170, ["BALUNSAT", "AMARA"])
        amara[1] = hdf.OcrItem("09/26/2026", 0.99, 160, 170)
        result = op.find_phic_beneficiary_row_y(
            kate + amara,
            "09/23/2026",
            "09/29/2026",
            "BALUNSAT, AMARA MARCELINE GONZALES",
        )
        self.assertAlmostEqual(result, 170.0)


class BeneficiariesWindowTextTests(unittest.TestCase):
    """The close check must see a spaced OCR title as the window.

    Live failure 2026-10-08 (TUTAAN): the probe OCR read the title
    as "PHIL HEALTH BENEFICIARIES" (spaced), the exact-token check
    missed it, and SIBALON's still-open window passed for closed.
    """

    def test_joined_title_is_detected(self):
        self.assertTrue(
            hdf.HbsysOperator.is_phic_beneficiaries_text(
                "PhilHealth Beneficiaries of GONZALES, AMARA"
            )
        )

    def test_spaced_title_is_detected(self):
        self.assertTrue(
            hdf.HbsysOperator.is_phic_beneficiaries_text(
                "PHIL HEALTH BENEFICIARIES OF SBALON, MYRNA DULY"
            )
        )

    def test_misread_first_word_is_detected(self):
        self.assertTrue(
            hdf.HbsysOperator.is_phic_beneficiaries_text(
                "PHILAEALTH BENEFICIARIES OF GONZALES, AMARA"
            )
        )

    def test_plain_hbsys_screen_is_not_the_window(self):
        self.assertFalse(
            hdf.HbsysOperator.is_phic_beneficiaries_text(
                "HBSys - Hospital Operations and Management "
                "Information System (HOMIS) Billing System "
                "Patient Record Form Window Quit"
            )
        )


class SafeResetTextTests(unittest.TestCase):
    """The reset proof must count a spaced title as the window."""

    def test_search_screen_without_window_is_safe(self):
        self.assertTrue(
            hdf.HbsysOperator.is_safe_reset_text(
                "HBSys Billing System Hospital No. Patient Record Form"
            )
        )

    def test_spaced_window_title_is_still_open(self):
        self.assertFalse(
            hdf.HbsysOperator.is_safe_reset_text(
                "HBSys Billing System Hospital No. "
                "PHIL HEALTH BENEFICIARIES OF SBALON, MYRNA DULY"
            )
        )

    def test_joined_window_title_is_still_open(self):
        self.assertFalse(
            hdf.HbsysOperator.is_safe_reset_text(
                "HBSys Billing System Hospital No. "
                "PhilHealth Beneficiaries of GONZALES, AMARA"
            )
        )

    def test_window_without_search_field_is_not_safe(self):
        self.assertFalse(
            hdf.HbsysOperator.is_safe_reset_text(
                "PHIL HEALTH BENEFICIARIES OF GONZALES, AMARA"
            )
        )

    def test_ocr_n0_variant_of_the_search_field_counts(self):
        self.assertTrue(
            hdf.HbsysOperator.is_safe_reset_text(
                "HBSys Billing System HOSPITAL N0 Patient Record Form"
            )
        )


class StaleBeneficiariesWindowTests(unittest.TestCase):
    """A Beneficiaries window from the previous patient must be spotted."""

    @staticmethod
    def _items(text: str):
        return [hdf.OcrItem(text, 0.9, 10, 10)]

    def _shows_other(self, text: str, patient_name: str):
        op = _operator()
        with mock.patch.object(
            hdf, "read_ocr_items", return_value=self._items(text)
        ):
            return op.beneficiaries_window_shows_other_patient(
                Path("capture.png"),
                SimpleNamespace(patient_name=patient_name),
            )

    def test_previous_patient_window_is_stale(self):
        # TUTAAN's claim against SIBALON's still-open window
        # (identical confinement dates, different patient).
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

    def test_first_name_alone_proves_the_window_is_current(self):
        # OCR may garble the last name; a readable first name
        # still proves the window belongs to the claim patient.
        self.assertFalse(
            self._shows_other(
                "PHIL HEALTH BENEFICIARIES OF T0LENTINO, JUDEA "
                "PRAGATA",
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

    @staticmethod
    def _claim():
        return SimpleNamespace(
            patient_name="TOLENTINO, JUDEA PRAGATA",
            admission_grid="09/04/2026",
            discharge_grid="09/09/2026",
        )

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
            hdf.OcrItem(
                "PHIL HEALTH BENEFICIARIES OF SIBALON, MYRNA DULAY",
                0.9,
                10,
                10,
            )
        ]
        with mock.patch.object(
            hdf, "read_ocr_items", return_value=stale
        ), mock.patch.object(
            hdf, "read_ocr_item_variants", return_value=[[]]
        ), mock.patch.object(
            hdf, "sleep_short"
        ), mock.patch.object(
            hdf.pyautogui, "click"
        ) as click:
            result = op.click_phic_and_select_claim(self._claim())
        self.assertTrue(result)
        op.close_phic_beneficiaries.assert_called_once_with(
            "Close stale PhilHealth Beneficiaries window"
        )
        # PHIC clicked twice: the initial open and the reopen.
        self.assertEqual(
            [call.args for call in click.call_args_list],
            [
                (hdf.P.PHIC.x, hdf.P.PHIC.y),
                (hdf.P.PHIC.x, hdf.P.PHIC.y),
            ],
        )
        # The stale capture and the reopened capture were both read.
        self.assertEqual(op.capture_window.call_count, 2)

    def test_current_window_is_not_reopened(self):
        op = self._op()
        op.capture_window = mock.Mock(
            side_effect=[Path("select.png")]
        )
        current = [
            hdf.OcrItem(
                "PHIL HEALTH BENEFICIARIES OF TOLENTINO, JUDEA PRAGATA",
                0.9,
                10,
                10,
            )
        ]
        with mock.patch.object(
            hdf, "read_ocr_items", return_value=current
        ), mock.patch.object(
            hdf, "read_ocr_item_variants", return_value=[[]]
        ), mock.patch.object(
            hdf, "sleep_short"
        ), mock.patch.object(
            hdf.pyautogui, "click"
        ) as click:
            result = op.click_phic_and_select_claim(self._claim())
        self.assertTrue(result)
        op.close_phic_beneficiaries.assert_not_called()
        # PHIC only: the row was already highlighted on the first,
        # current-patient capture.
        self.assertEqual(len(click.call_args_list), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)