from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The requirement-rule integration tests do not render images.  Keep them
# runnable in a lightweight developer interpreter where Pillow is absent.
try:
    from PIL import Image as _pillow_image  # noqa: F401
except ModuleNotFoundError:
    pil_module = types.ModuleType("PIL")
    pil_module.Image = types.ModuleType("PIL.Image")
    sys.modules["PIL"] = pil_module
    sys.modules["PIL.Image"] = pil_module.Image

import claims_checker  # noqa: E402
from core.claims_requirement_rules import (  # noqa: E402
    BASE_REQUIREMENTS,
    evaluate_requirements,
)


class ClaimsRequirementRuleTests(unittest.TestCase):
    def setUp(self):
        self.base = set(BASE_REQUIREMENTS)

    def test_complete_regular_claim_is_ready(self):
        result = evaluate_requirements(self.base)
        self.assertEqual(result.classification, "REGULAR")
        self.assertEqual(result.missing, ())

    def test_dtr_is_a_hard_requirement(self):
        result = evaluate_requirements(self.base - {"DTR"})
        self.assertEqual(result.missing, ("DTR",))

    def test_cf3_does_not_waive_cf4_xml(self):
        result = evaluate_requirements((self.base - {"CF4 XML"}) | {"CF3"})
        self.assertIn("CF4 XML", result.missing)

    def test_mrf_accepts_pbc_or_mmc(self):
        self.assertNotIn(
            "PBC or MMC",
            evaluate_requirements(self.base | {"MRF", "PBC"}).missing,
        )
        self.assertNotIn(
            "PBC or MMC",
            evaluate_requirements(self.base | {"MRF", "MMC"}).missing,
        )

    def test_mrf_without_companion_reports_alternative(self):
        result = evaluate_requirements(self.base | {"MRF"})
        self.assertEqual(result.missing, ("PBC or MMC",))

    def test_coe_no_requires_mrf_and_birth_certificate_companion(self):
        result = evaluate_requirements(self.base, coe_eligibility_no=True)
        self.assertEqual(result.missing, ("MRF", "PBC or MMC"))

    def test_coe_no_accepts_mrf_and_pbc(self):
        result = evaluate_requirements(
            self.base | {"MRF", "PBC"},
            coe_eligibility_no=True,
        )
        self.assertEqual(result.missing, ())

    def test_coe_no_accepts_mrf_and_mmc(self):
        result = evaluate_requirements(
            self.base | {"MRF", "MMC"},
            coe_eligibility_no=True,
        )
        self.assertEqual(result.missing, ())

    def test_coe_no_with_companion_but_without_mrf_still_requires_mrf(self):
        result = evaluate_requirements(
            self.base | {"MMC"},
            coe_eligibility_no=True,
        )
        self.assertEqual(result.missing, ("MRF",))

    def test_anr_requires_opr_and_cf3(self):
        result = evaluate_requirements(self.base | {"ANR"})
        self.assertEqual(result.classification, "CS/ANR")
        self.assertEqual(result.missing, ("OPR", "CF3"))

    def test_nsd_requires_opr_and_cf3(self):
        result = evaluate_requirements(self.base, nsd01_detected=True)
        self.assertEqual(result.classification, "NSD")
        self.assertEqual(result.missing, ("OPR", "CF3"))

    def test_cf2_classifies_newborn_without_extra_requirement(self):
        result = evaluate_requirements(self.base | {"CF2"})
        self.assertEqual(result.classification, "NEWBORN")
        self.assertEqual(result.missing, ())

    def test_combined_rules_are_cumulative_without_duplicates(self):
        result = evaluate_requirements(
            self.base | {"ANR", "MRF"},
            nsd01_detected=True,
        )
        self.assertEqual(result.missing, ("OPR", "CF3", "PBC or MMC"))

    def test_opr_and_companion_files_do_not_reverse_trigger(self):
        result = evaluate_requirements(self.base | {"OPR", "PBC", "MMC"})
        self.assertEqual(result.missing, ())
        self.assertEqual(result.classification, "REGULAR")


class ClaimsCheckerIntegrationTests(unittest.TestCase):
    @staticmethod
    def _touch(folder: Path, filename: str) -> None:
        (folder / filename).write_bytes(b"test")

    def test_lowercase_mmc_opr_and_cf2_are_detected(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for filename in (
                "CSF.pdf",
                "COE.pdf",
                "SOA1.pdf",
                "SOA2.pdf",
                "DTR.pdf",
                "MRF.pdf",
                "mmc.pdf",
                "ANR.pdf",
                "opr.pdf",
                "CF3.pdf",
                "cf2.pdf",
                "patient_CF4.xml",
                "patient_CF5.xml",
                "patient_eSOA.xml",
            ):
                self._touch(folder, filename)

            with patch.object(claims_checker, "read_pdf_text", return_value=""):
                row = claims_checker.check_patient_folder(folder)

            self.assertEqual(row["Status"], "READY")
            self.assertEqual(row["Missing"], "")
            self.assertIn("MMC", row["Found"])
            self.assertIn("NEWBORN + CS/ANR", row["Notes"])

    def test_nsd_missing_documents_make_folder_incomplete(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            for filename in (
                "CSF.pdf",
                "COE.pdf",
                "SOA1.pdf",
                "SOA2.pdf",
                "DTR.pdf",
                "patient_CF4.xml",
                "patient_CF5.xml",
                "patient_eSOA.xml",
            ):
                self._touch(folder, filename)

            with patch.object(claims_checker, "read_pdf_text", return_value="NSD01"):
                row = claims_checker.check_patient_folder(folder)

            self.assertEqual(row["Status"], "INCOMPLETE")
            self.assertEqual(row["Missing"], "OPR; CF3")


if __name__ == "__main__":
    unittest.main()
