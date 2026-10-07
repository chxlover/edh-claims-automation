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
        claim = SimpleNamespace(
            admission_grid="09/04/2026", discharge_grid="09/09/2026"
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
                mock.patch.object(hdf, "sleep_short"), \
                mock.patch.object(hdf.pyautogui, "click") as click:
            result = op.click_phic_and_select_claim(self._claim())
        self.assertTrue(result)
        self.assertEqual(len(click.call_args_list), 2)  # PHIC + one row attempt


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


if __name__ == "__main__":
    unittest.main(verbosity=2)