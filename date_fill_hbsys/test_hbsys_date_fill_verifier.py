from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from PIL import Image, ImageDraw

from hbsys_fill_dates_testing import HbsysOperator, P
from hbsys_read_admission_history_testing import OcrItem
from hbsys_date_fill_verifier import (
    EncounterIdentity,
    EncounterDateState,
    PatientDateSnapshot,
    evaluate_post_save,
)


EXPECTED = "ENC-EXPECTED"
OTHER = "ENC-OTHER"
DISCHARGE = date(2026, 6, 25)


def state(value: str) -> EncounterDateState:
    return EncounterDateState((value,), (value,), (value,))


class PostSaveProofTests(unittest.TestCase):
    def test_regular_and_abtc_use_the_correct_target_date(self) -> None:
        regular = EncounterIdentity(
            EXPECTED,
            "0001",
            date(2026, 6, 22),
            DISCHARGE,
            claim_type="REGULAR",
        )
        abtc = EncounterIdentity(
            EXPECTED,
            "0001",
            date(2026, 6, 22),
            DISCHARGE,
            claim_type="ABTC",
            encounter_type="OPD",
        )

        self.assertEqual(regular.date_basis, "DISCHARGE")
        self.assertEqual(regular.target_date, DISCHARGE)
        self.assertEqual(abtc.date_basis, "ADMISSION")
        self.assertEqual(abtc.target_date, date(2026, 6, 22))

    def test_all_expected_fields_match(self) -> None:
        before = PatientDateSnapshot(EXPECTED, {EXPECTED: state("")})
        after = PatientDateSnapshot(EXPECTED, {EXPECTED: state("2026-06-25")})

        proof = evaluate_post_save(DISCHARGE, before, after)

        self.assertTrue(proof.verified)
        self.assertFalse(proof.critical_wrong_encounter)
        self.assertEqual(proof.professional_date, "2026-06-25")

    def test_missing_professional_date_is_not_verified(self) -> None:
        before = PatientDateSnapshot(EXPECTED, {EXPECTED: state("")})
        after = PatientDateSnapshot(
            EXPECTED,
            {
                EXPECTED: EncounterDateState(
                    ("",), ("2026-06-25",), ("2026-06-25",)
                )
            },
        )

        proof = evaluate_post_save(DISCHARGE, before, after)

        self.assertFalse(proof.verified)
        self.assertIn("Professional Fee", proof.reason)

    def test_other_encounter_change_is_critical(self) -> None:
        before = PatientDateSnapshot(
            EXPECTED,
            {EXPECTED: state(""), OTHER: state("")},
        )
        after = PatientDateSnapshot(
            EXPECTED,
            {
                EXPECTED: state("2026-06-25"),
                OTHER: state("2026-06-25"),
            },
        )

        proof = evaluate_post_save(DISCHARGE, before, after)

        self.assertFalse(proof.verified)
        self.assertTrue(proof.critical_wrong_encounter)
        self.assertEqual(proof.changed_other_enccodes, (OTHER,))

    def test_missing_expected_records_is_not_verified(self) -> None:
        before = PatientDateSnapshot(EXPECTED, {EXPECTED: state("")})
        after = PatientDateSnapshot(EXPECTED, {})

        proof = evaluate_post_save(DISCHARGE, before, after)

        self.assertFalse(proof.verified)
        self.assertIn("no post-save", proof.reason)

    def test_one_wrong_consent_row_fails_all_rows_rule(self) -> None:
        before = PatientDateSnapshot(EXPECTED, {EXPECTED: state("")})
        after = PatientDateSnapshot(
            EXPECTED,
            {
                EXPECTED: EncounterDateState(
                    ("2026-06-25",),
                    ("2026-06-25", "2026-06-24"),
                    ("2026-06-25", "2026-06-25"),
                )
            },
        )

        proof = evaluate_post_save(DISCHARGE, before, after)

        self.assertFalse(proof.verified)
        self.assertIn("Consent", proof.reason)


class VisualSelectionProofTests(unittest.TestCase):
    def setUp(self) -> None:
        self.operator = HbsysOperator(live=False, pause=0, confirm_each=False)

    def test_strict_phic_match_requires_both_dates_and_patient_name(self) -> None:
        items = [
            OcrItem("06/22/2026", 0.99, 40, 200),
            OcrItem("06/25/2026", 0.99, 120, 200),
            OcrItem("AGUSTIN KATHLEA", 0.99, 250, 200),
        ]

        row_y = self.operator.find_phic_beneficiary_row_y(
            items,
            "06/22/2026",
            "06/25/2026",
            "AGUSTIN, KATHLEA SOLEN",
            strict=True,
        )

        self.assertEqual(row_y, 200)

    def test_abtc_phic_requires_same_day_dates_name_and_accreditation(self) -> None:
        items = [
            OcrItem("06/15/2026", 0.99, 40, 200),
            OcrItem("06/15/2026", 0.99, 120, 200),
            OcrItem("BALDOMERO MARK VINZON PASCUA", 0.99, 350, 200),
            OcrItem("B02023200", 0.99, 1340, 200),
        ]

        row_y = self.operator.find_phic_beneficiary_row_y(
            items,
            "06/15/2026",
            "06/22/2026",
            "BALDOMERO, MARK VINZON PASCUA",
            strict=True,
            match_admission_only=True,
            required_accreditation="B02023200",
        )

        self.assertEqual(row_y, 200)

    def test_abtc_phic_rejects_wrong_accreditation(self) -> None:
        items = [
            OcrItem("06/15/2026", 0.99, 40, 200),
            OcrItem("06/15/2026", 0.99, 120, 200),
            OcrItem("BALDOMERO MARK VINZON PASCUA", 0.99, 350, 200),
            OcrItem("H02021954", 0.99, 1340, 200),
        ]

        row_y = self.operator.find_phic_beneficiary_row_y(
            items,
            "06/15/2026",
            "06/22/2026",
            "BALDOMERO, MARK VINZON PASCUA",
            strict=True,
            match_admission_only=True,
            required_accreditation="B02023200",
        )

        self.assertIsNone(row_y)

    def test_abtc_combines_date_name_and_accreditation_ocr_passes(self) -> None:
        date_and_name = [
            OcrItem("06/15/2026", 0.99, 40, 200),
            OcrItem("BALDOMERO MARK VINZON PASCUA", 0.99, 350, 200),
            OcrItem("1802023200", 0.80, 1340, 200),
        ]
        accreditation = [
            OcrItem("0615/2026", 0.30, 40, 202),
            OcrItem("BALDOMER0", 0.60, 350, 202),
            OcrItem("B02023200", 0.90, 1340, 202),
        ]

        row_y = self.operator.find_phic_beneficiary_row_y_from_variants(
            [date_and_name, date_and_name, accreditation, accreditation],
            "06/15/2026",
            "06/22/2026",
            "BALDOMERO, MARK VINZON PASCUA",
            strict=True,
            minimum_consensus=2,
            match_admission_only=True,
            required_accreditation="B02023200",
        )

        self.assertEqual(row_y, 200)

    def test_abtc_combines_three_independent_phic_proofs(self) -> None:
        date_only = [
            OcrItem("06/15/2026", 0.99, 40, 200),
            OcrItem("PATIENT", 0.99, 350, 200),
        ]
        name_only = [
            OcrItem("BALDOMERO MARK VINZON PASCUA", 0.99, 350, 202),
        ]
        accreditation_only = [
            OcrItem("B02023200", 0.99, 1340, 198),
        ]

        row_y = self.operator.find_phic_beneficiary_row_y_from_variants(
            [
                date_only,
                name_only,
                name_only,
                accreditation_only,
                accreditation_only,
            ],
            "06/15/2026",
            "06/22/2026",
            "BALDOMERO, MARK VINZON PASCUA",
            strict=True,
            minimum_consensus=2,
            match_admission_only=True,
            required_accreditation="B02023200",
        )

        self.assertEqual(row_y, 200)

    def test_admission_history_uses_exact_multi_pass_consensus(self) -> None:
        wrong = [
            OcrItem("07/01/2028", 0.99, 76, 100),
            OcrItem("07/01/2026", 0.99, 330, 100),
            OcrItem("ADMIT", 0.99, 564, 100),
        ]
        exact_one = [
            OcrItem("07/01/2026", 0.99, 76, 99),
            OcrItem("07/01/2026", 0.99, 330, 99),
            OcrItem("ADMIT", 0.99, 564, 99),
        ]
        exact_two = [
            OcrItem("07/01/2026", 0.99, 76, 101),
            OcrItem("07/01/2026", 0.99, 330, 101),
            OcrItem("ADMIT", 0.99, 564, 101),
        ]

        row_y = self.operator.find_exact_admission_history_row_y(
            [wrong, exact_one, exact_two],
            "07/01/2026",
            "07/01/2026",
        )

        self.assertEqual(row_y, 100)

    def test_admission_history_rejects_one_pass_only(self) -> None:
        exact = [
            OcrItem("07/01/2026", 0.99, 76, 100),
            OcrItem("07/01/2026", 0.99, 330, 100),
            OcrItem("ADMIT", 0.99, 564, 100),
        ]

        row_y = self.operator.find_exact_admission_history_row_y(
            [exact],
            "07/01/2026",
            "07/01/2026",
        )

        self.assertIsNone(row_y)

    def test_abtc_admission_history_accepts_exact_opd_row(self) -> None:
        first = [
            OcrItem("06/22/2026", 0.99, 76, 180),
            OcrItem("06/25/2026", 0.99, 330, 180),
            OcrItem("OPD", 0.99, 564, 180),
        ]
        second = [
            OcrItem("06/22/2026", 0.99, 76, 182),
            OcrItem("06/25/2026", 0.99, 330, 182),
            OcrItem("OPD", 0.99, 564, 182),
        ]

        row_y = self.operator.find_exact_admission_history_row_y(
            [first, second],
            "06/22/2026",
            "06/25/2026",
            {"OPD", "OPDAD"},
        )

        self.assertEqual(row_y, 181)

    def test_abtc_accepts_same_day_opd_consultation_row(self) -> None:
        first = [
            OcrItem("06/15/2026", 0.99, 76, 100),
            OcrItem("06/15/2026", 0.99, 330, 100),
            OcrItem("OPD", 0.99, 564, 100),
        ]
        second = [
            OcrItem("06/15/2026", 0.99, 76, 102),
            OcrItem("06/15/2026", 0.99, 330, 102),
            OcrItem("OPD", 0.99, 564, 102),
        ]

        row_y = self.operator.find_exact_admission_history_row_y(
            [first, second],
            "06/15/2026",
            "06/22/2026",
            {"OPD", "OPDAD"},
            match_admission_only=True,
        )

        self.assertEqual(row_y, 101)

    def test_abtc_requires_explicit_testing_gate(self) -> None:
        with self.assertRaises(ValueError):
            HbsysOperator(
                live=False,
                pause=0,
                confirm_each=False,
                claim_type="ABTC",
            )

        operator = HbsysOperator(
            live=False,
            pause=0,
            confirm_each=False,
            claim_type="ABTC",
            enable_abtc=True,
        )
        self.assertEqual(operator.claim_type, "ABTC")

    def test_beneficiaries_close_button_prefers_details_layout_slot(self) -> None:
        regular = HbsysOperator(
            live=False,
            pause=0,
            confirm_each=False,
        )
        abtc = HbsysOperator(
            live=False,
            pause=0,
            confirm_each=False,
            claim_type="ABTC",
            enable_abtc=True,
        )

        # Both claim types use the with-Details Close Form slot first because the
        # current HBSys Beneficiaries toolbar always shows a Details button.
        self.assertEqual(
            (regular.beneficiaries_close_point().x,
             regular.beneficiaries_close_point().y),
            (485, 58),
        )
        self.assertEqual(
            (abtc.beneficiaries_close_point().x,
             abtc.beneficiaries_close_point().y),
            (485, 58),
        )

    def test_phic_consensus_accepts_ocr_year_fallback_from_two_passes(self) -> None:
        first = [
            OcrItem("06/29/2026", 0.99, 40, 205),
            OcrItem("07/03/2028", 0.99, 120, 205),
            OcrItem("MARQUEZ JOLLY LAO ANG", 0.99, 250, 205),
        ]
        second = [
            OcrItem("06/29/2026", 0.99, 40, 207),
            OcrItem("07/03/2028", 0.99, 120, 207),
            OcrItem("MARQUEZ JOLLY LAO ANG", 0.99, 250, 207),
        ]

        row_y = self.operator.find_phic_beneficiary_row_y_from_variants(
            [first, second],
            "06/29/2026",
            "07/03/2026",
            "MARQUEZ, JOLLY LAO-ANG",
            strict=False,
            minimum_consensus=2,
        )

        self.assertEqual(row_y, 206)

    def test_phic_consensus_rejects_single_ocr_fallback(self) -> None:
        one_pass = [
            OcrItem("06/29/2026", 0.99, 40, 205),
            OcrItem("07/03/2028", 0.99, 120, 205),
            OcrItem("MARQUEZ JOLLY LAO ANG", 0.99, 250, 205),
        ]

        row_y = self.operator.find_phic_beneficiary_row_y_from_variants(
            [one_pass],
            "06/29/2026",
            "07/03/2026",
            "MARQUEZ, JOLLY LAO-ANG",
            strict=False,
            minimum_consensus=2,
        )

        self.assertIsNone(row_y)

    def test_strict_phic_match_rejects_admission_only_fallback(self) -> None:
        items = [
            OcrItem("06/22/2026", 0.99, 40, 200),
            OcrItem("06/26/2026", 0.99, 120, 200),
            OcrItem("AGUSTIN KATHLEA", 0.99, 250, 200),
        ]

        row_y = self.operator.find_phic_beneficiary_row_y(
            items,
            "06/22/2026",
            "06/25/2026",
            "AGUSTIN, KATHLEA SOLEN",
            strict=True,
        )

        self.assertIsNone(row_y)

    def test_blue_highlight_proof(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "selected.png"
            image = Image.new("RGB", (1600, 400), "white")
            ImageDraw.Draw(image).rectangle((5, 193, 1579, 207), fill=(0, 120, 215))
            image.save(path)

            self.assertTrue(self.operator.is_blue_highlighted_row(path, 200))
            self.assertFalse(self.operator.is_blue_highlighted_row(path, 250))

    def test_safe_reset_accepts_hospital_n0_ocr(self) -> None:
        proof_text = "HBSYS BILLING HOSPITAL N0: 000000000019272 CASE TYPE"

        self.assertTrue(self.operator.is_safe_reset_text(proof_text))

    def test_safe_reset_rejects_beneficiaries_screen(self) -> None:
        proof_text = "HOSPITAL NO PHILHEALTH BENEFICIARIES OF SAMPLE PATIENT"

        self.assertFalse(self.operator.is_safe_reset_text(proof_text))

    def test_detects_beneficiary_cancel_only_on_phic_screen(self) -> None:
        self.assertTrue(
            self.operator.is_beneficiary_cancel_visible_text(
                "PHILHEALTH BENEFICIARIES OF SAMPLE PATIENT CANCEL"
            )
        )
        # OCR often misreads PHILHEALTH; BENEFICIARIES is the reliable marker.
        self.assertTrue(
            self.operator.is_beneficiary_cancel_visible_text(
                "PHILAEALTH BENEFICIARIES OF SAMPLE PATIENT CANCEL"
            )
        )
        self.assertFalse(
            self.operator.is_beneficiary_cancel_visible_text(
                "BILLING SCREEN CANCEL"
            )
        )
        self.assertFalse(
            self.operator.is_beneficiary_cancel_visible_text(
                "PHILHEALTH BENEFICIARIES OF SAMPLE PATIENT"
            )
        )

    def test_beneficiaries_close_slot_has_alternate_fallback(self) -> None:
        regular = HbsysOperator(live=False, pause=0, confirm_each=False)
        abtc = HbsysOperator(
            live=False,
            pause=0,
            confirm_each=False,
            claim_type="ABTC",
            enable_abtc=True,
        )

        self.assertEqual(
            regular.beneficiaries_close_point(),
            P.CLOSE_FORM_BENEFICIARIES_WITH_DETAILS,
        )
        self.assertEqual(
            regular.beneficiaries_alternate_close_point(),
            P.CLOSE_FORM_BENEFICIARIES_LEGACY,
        )
        self.assertEqual(
            abtc.beneficiaries_close_point(),
            P.CLOSE_FORM_BENEFICIARIES_WITH_DETAILS,
        )
        self.assertEqual(
            abtc.beneficiaries_alternate_close_point(),
            P.CLOSE_FORM_BENEFICIARIES_LEGACY,
        )


class ClaimForm4DismissalTests(unittest.TestCase):
    """Dismissal of the Claim Form 4 view HBSys can open after the PHIC click."""

    def setUp(self) -> None:
        self.operator = HbsysOperator(live=False, pause=0, confirm_each=False)

    @mock.patch("hbsys_fill_dates_testing.sleep_short")
    def test_dry_run_accepts_without_cancel(self, _mock_sleep: mock.Mock) -> None:
        self.assertTrue(self.operator.dismiss_claim_form4_after_phic_if_visible())

    @mock.patch("hbsys_fill_dates_testing.sleep_short")
    def test_skips_when_no_cancel_visible(self, _mock_sleep: mock.Mock) -> None:
        self.operator.live = True
        self.operator.cancel_pending_beneficiary_edit_if_visible = lambda: False

        self.assertTrue(self.operator.dismiss_claim_form4_after_phic_if_visible())
        self.assertEqual(self.operator.audit.get("phic_claim_form4_dismissed"), None)

    @mock.patch("hbsys_fill_dates_testing.sleep_short")
    def test_returns_true_when_cancel_clears(self, _mock_sleep: mock.Mock) -> None:
        self.operator.live = True
        self.operator.cancel_pending_beneficiary_edit_if_visible = lambda: True
        self.operator.phic_cancel_visible = lambda: False  # Cancel cleared

        self.assertTrue(self.operator.dismiss_claim_form4_after_phic_if_visible())
        self.assertEqual(self.operator.audit["phic_claim_form4_dismissed"], "YES")

    @mock.patch("hbsys_fill_dates_testing.sleep_short")
    def test_returns_false_when_cancel_persists(self, _mock_sleep: mock.Mock) -> None:
        self.operator.live = True
        self.operator.cancel_pending_beneficiary_edit_if_visible = lambda: True
        self.operator.phic_cancel_visible = lambda: True  # Cancel stays visible

        self.assertFalse(self.operator.dismiss_claim_form4_after_phic_if_visible())
        self.assertEqual(self.operator.audit["phic_claim_form4_dismissed"], "NO")

    @mock.patch("hbsys_fill_dates_testing.sleep_short")
    def test_dry_run_phic_flow_continues_after_dismissal(
        self, _mock_sleep: mock.Mock
    ) -> None:
        fake_claim = SimpleNamespace(
            admission_grid="06/22/2026",
            discharge_grid="06/25/2026",
            patient_name="SAMPLE PATIENT",
        )

        self.assertTrue(self.operator.click_phic_and_select_claim(fake_claim))

    @mock.patch("hbsys_fill_dates_testing.read_ocr_item_variants")
    def test_phic_cancel_visible_detects_from_any_variant(
        self, mock_variants: mock.Mock
    ) -> None:
        self.operator.live = True
        self.operator.hbsys_window = object()
        self.operator.capture_window = lambda window, prefix: Path("fake.png")
        mock_variants.return_value = [
            [OcrItem("PHILHEALTH BENEFICIARIES OF DORIA", 0.99, 30, 100)],
            [OcrItem("BENEFICIARIES OF DORIA CANCEL", 0.99, 30, 100)],
        ]

        self.assertTrue(self.operator.phic_cancel_visible())

    @mock.patch("hbsys_fill_dates_testing.read_ocr_item_variants")
    def test_phic_cancel_visible_false_when_no_cancel_variant(
        self, mock_variants: mock.Mock
    ) -> None:
        self.operator.live = True
        self.operator.hbsys_window = object()
        self.operator.capture_window = lambda window, prefix: Path("fake.png")
        mock_variants.return_value = [
            [OcrItem("PHILHEALTH BENEFICIARIES OF DORIA", 0.99, 30, 100)],
            [OcrItem("PHILAEALTH BENEFICIARIES OF DORIA", 0.99, 30, 100)],
        ]

        self.assertFalse(self.operator.phic_cancel_visible())


class ProfessionalFeeRowsVerifierTests(unittest.TestCase):
    """Post-save proof tolerates empty professional fee rows left by design."""

    def test_empty_rows_plus_filled_row_are_verified(self) -> None:
        before = PatientDateSnapshot(EXPECTED, {EXPECTED: state("")})
        after = PatientDateSnapshot(
            EXPECTED,
            {
                EXPECTED: EncounterDateState(
                    ("", "2026-06-25"),  # second row intentionally blank
                    ("2026-06-25",),
                    ("2026-06-25",),
                )
            },
        )

        proof = evaluate_post_save(DISCHARGE, before, after)

        self.assertTrue(proof.verified)

    def test_all_rows_blank_is_not_verified(self) -> None:
        before = PatientDateSnapshot(EXPECTED, {EXPECTED: state("")})
        after = PatientDateSnapshot(
            EXPECTED,
            {
                EXPECTED: EncounterDateState(
                    ("", ""),
                    ("2026-06-25",),
                    ("2026-06-25",),
                )
            },
        )

        proof = evaluate_post_save(DISCHARGE, before, after)

        self.assertFalse(proof.verified)
        self.assertIn("Professional Fee", proof.reason)


if __name__ == "__main__":
    unittest.main()
