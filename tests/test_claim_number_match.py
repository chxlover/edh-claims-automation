from __future__ import annotations
import csv
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import claims_checker  # noqa: E402


def make_patient_folder(root: Path, name: str, xml_names: list[str]) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    for xml_name in xml_names:
        (folder / xml_name).write_text("{}", encoding="utf-8")
    return folder


class ExtractClaimSeriesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_extracts_series_per_kind(self):
        folder = make_patient_folder(
            self.root,
            "DOE, JUAN CRUZ - 000000000012345 - ADM20260901_DIS20260903",
            [
                "DOE, JUAN CRUZ-260924150960_CF4.xml",
                "DOE, JUAN, CRUZ-260924150960_CF5.xml",
                "DOE, JUAN, CRUZ-260924150960_eSOA.xml",
            ],
        )
        series = claims_checker.extract_claim_series(folder)
        self.assertEqual(series["cf4"], {"260924150960"})
        self.assertEqual(series["cf5"], {"260924150960"})
        self.assertEqual(series["esoa"], {"260924150960"})

    def test_ignores_non_xml_and_non_matching_names(self):
        folder = make_patient_folder(
            self.root,
            "DOE, JUAN CRUZ",
            ["DOE, JUAN CRUZ-260924150960_CF4.xml"],
        )
        (folder / "CSF.pdf").write_text("x", encoding="utf-8")
        (folder / "DOE, JUAN CRUZ_no_number_CF4.xml").write_text(
            "{}", encoding="utf-8"
        )
        series = claims_checker.extract_claim_series(folder)
        self.assertEqual(series["cf4"], {"260924150960"})
        self.assertEqual(series["cf5"], set())
        self.assertEqual(series["esoa"], set())

    def test_detects_duplicate_kind_with_different_numbers(self):
        folder = make_patient_folder(
            self.root,
            "DOE, JUAN CRUZ",
            [
                "DOE, JUAN CRUZ-260924150960_CF4.xml",
                "DOE, JUAN CRUZ-260924199999_CF4.xml",
            ],
        )
        series = claims_checker.extract_claim_series(folder)
        self.assertEqual(series["cf4"], {"260924150960", "260924199999"})


class BuildClaimNumberRowsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def build(self, name: str, xml_names: list[str]) -> dict:
        folder = make_patient_folder(self.root, name, xml_names)
        rows = claims_checker.build_claim_number_rows([folder])
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_all_match(self):
        row = self.build(
            "ACLIB, GRACHEL-ANNE BANGNGAD",
            [
                "ACLIB, GRACHEL-ANNE BANGNGAD-260924150960_CF4.xml",
                "ACLIB, GRACHEL-ANNE, BANGNGAD-260924150960_CF5.xml",
                "ACLIB, GRACHEL-ANNE, BANGNGAD-260924150960_eSOA.xml",
            ],
        )
        self.assertEqual(row["Status"], "CLAIM NO. MATCH")
        self.assertIn("CF4=260924150960", row["Found"])
        self.assertIn("CF5=260924150960", row["Found"])
        self.assertIn("ESOA=260924150960", row["Found"])
        self.assertIn("match", row["Notes"])

    def test_one_number_differs(self):
        row = self.build(
            "DELA CRUZ, MARISSA DOBLA",
            [
                "DELA CRUZ, MARISSA DOBLA-260924152817_CF4.xml",
                "DELA CRUZ, MARISSA, DOBLA-260924152899_CF5.xml",
                "DELA CRUZ, MARISSA, DOBLA-260924152817_eSOA.xml",
            ],
        )
        self.assertEqual(row["Status"], "CLAIM NO. MISMATCH")
        self.assertIn("differ", row["Notes"])

    def test_missing_one_kind(self):
        row = self.build(
            "OLEGARIO, CRISTINA ISAGUIRRE",
            [
                "OLEGARIO, CRISTINA ISAGUIRRE-260924153728_CF4.xml",
                "OLEGARIO, CRISTINA, ISAGUIRRE-260924153728_CF5.xml",
            ],
        )
        self.assertEqual(row["Status"], "CLAIM NO. INCOMPLETE")
        self.assertIn("ESOA", row["Notes"])
        self.assertIn("ESOA=(none)", row["Found"])

    def test_duplicate_cf4_with_different_numbers_is_mismatch(self):
        row = self.build(
            "REYES, MARIA SANTOS",
            [
                "REYES, MARIA SANTOS-111111111111_CF4.xml",
                "REYES, MARIA SANTOS-222222222222_CF4.xml",
                "REYES, MARIA, SANTOS-111111111111_CF5.xml",
                "REYES, MARIA, SANTOS-111111111111_eSOA.xml",
            ],
        )
        self.assertEqual(row["Status"], "CLAIM NO. MISMATCH")
        self.assertIn("111111111111", row["Found"])
        self.assertIn("222222222222", row["Found"])

    def test_no_xml(self):
        row = self.build("GARCIA, PEDRO REYES", [])
        self.assertEqual(row["Status"], "NO XML")


class BuildClaimNumberRowsCF3Tests(unittest.TestCase):
    """Operator rule 2026-10-15: a CF3 (NSD/nanganak) folder needs
    CF5 + eSOA only — CF4 is waived in the claim-number rows too."""

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def build(self, name: str, files: list[str]) -> dict:
        folder = make_patient_folder(self.root, name, files)
        rows = claims_checker.build_claim_number_rows([folder])
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_cf3_with_cf5_and_esoa_match(self):
        row = self.build(
            "SANTOS, MARIA CLARA",
            [
                "CF3.pdf",
                "SANTOS, MARIA, CLARA-260924150960_CF5.xml",
                "SANTOS, MARIA, CLARA-260924150960_eSOA.xml",
            ],
        )
        self.assertEqual(row["Status"], "CLAIM NO. MATCH")
        self.assertIn("both", row["Notes"])

    def test_cf3_with_only_cf5_is_incomplete(self):
        row = self.build(
            "SANTOS, MARIA CLARA",
            [
                "CF3.pdf",
                "SANTOS, MARIA, CLARA-260924150960_CF5.xml",
            ],
        )
        self.assertEqual(row["Status"], "CLAIM NO. INCOMPLETE")
        self.assertIn("ESOA", row["Notes"])

    def test_cf3_with_differing_numbers_is_mismatch(self):
        row = self.build(
            "SANTOS, MARIA CLARA",
            [
                "CF3.pdf",
                "SANTOS, MARIA, CLARA-260924150960_CF5.xml",
                "SANTOS, MARIA, CLARA-260924199999_eSOA.xml",
            ],
        )
        self.assertEqual(row["Status"], "CLAIM NO. MISMATCH")

    def test_cf3_with_no_xml_is_still_no_xml(self):
        row = self.build("SANTOS, MARIA CLARA", ["CF3.pdf"])
        self.assertEqual(row["Status"], "NO XML")

    def test_no_cf3_still_needs_all_three(self):
        # Same XMLs without CF3.pdf -> INCOMPLETE (CF4 missing).
        row = self.build(
            "SANTOS, MARIA CLARA",
            [
                "SANTOS, MARIA, CLARA-260924150960_CF5.xml",
                "SANTOS, MARIA, CLARA-260924150960_eSOA.xml",
            ],
        )
        self.assertEqual(row["Status"], "CLAIM NO. INCOMPLETE")
        self.assertIn("CF4", row["Notes"])



class WriteReportsWithClaimRowsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.original_csv = claims_checker.REPORT_CSV
        self.original_log = claims_checker.REPORT_LOG
        claims_checker.REPORT_CSV = self.root / "report.csv"
        claims_checker.REPORT_LOG = self.root / "report.log"

    def tearDown(self):
        claims_checker.REPORT_CSV = self.original_csv
        claims_checker.REPORT_LOG = self.original_log
        self.temporary.cleanup()

    def test_claim_rows_appended_below_requirement_rows(self):
        folder = make_patient_folder(
            self.root,
            "DOE, JUAN CRUZ",
            [
                "DOE, JUAN CRUZ-260924150960_CF4.xml",
                "DOE, JUAN, CRUZ-260924150960_CF5.xml",
                "DOE, JUAN, CRUZ-260924150960_eSOA.xml",
            ],
        )
        main_rows = [
            {
                "Patient Folder": "DOE, JUAN CRUZ",
                "Status": "READY",
                "Found": "CF4 XML; CF5 XML; eSOA XML",
                "Missing": "",
                "Warnings": "",
                "Signature Warnings": "",
                "Signature Debug": "",
                "Eligibility": "YES",
                "Reason": "",
                "Notes": "",
            }
        ]
        claim_rows = claims_checker.build_claim_number_rows([folder])

        csv_path, log_path = claims_checker.write_reports(main_rows, claim_rows)

        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            parsed = list(csv.DictReader(f))

        # Main requirement row is first and unchanged.
        self.assertEqual(parsed[0]["Patient Folder"], "DOE, JUAN CRUZ")
        self.assertEqual(parsed[0]["Status"], "READY")

        # Blank separator row comes right after the requirement rows.
        self.assertEqual(parsed[1]["Patient Folder"], "")

        # Section header separates the claim-number block.
        self.assertEqual(
            parsed[2]["Patient Folder"], "=== CLAIM NUMBER MATCH CHECK ==="
        )

        # Claim-number row is appended below.
        self.assertEqual(parsed[3]["Patient Folder"], "DOE, JUAN CRUZ")
        self.assertEqual(parsed[3]["Status"], "CLAIM NO. MATCH")

        log_text = Path(log_path).read_text(encoding="utf-8")
        self.assertIn("CLAIM NO. MATCH = 1", log_text)

    def test_write_reports_without_claim_rows_stays_compatible(self):
        csv_path, log_path = claims_checker.write_reports(
            [
                {
                    "Patient Folder": "DOE, JUAN CRUZ",
                    "Status": "READY",
                    "Found": "",
                    "Missing": "",
                    "Warnings": "",
                    "Signature Warnings": "",
                    "Signature Debug": "",
                    "Eligibility": "",
                    "Reason": "",
                    "Notes": "",
                }
            ]
        )
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            parsed = list(csv.DictReader(f))
        self.assertEqual(len(parsed), 1)
        log_text = Path(log_path).read_text(encoding="utf-8")
        self.assertNotIn("Claim Number Match", log_text)


class FolderHasAnyXmlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_true_when_xml_present(self):
        folder = make_patient_folder(
            self.root, "DOE, JUAN", ["DOE, JUAN-260924150960_CF4.xml"]
        )
        self.assertTrue(claims_checker.folder_has_any_xml(folder))

    def test_false_when_only_non_xml(self):
        folder = make_patient_folder(self.root, "DOE, JUAN", [])
        (folder / "CSF.pdf").write_text("x", encoding="utf-8")
        self.assertFalse(claims_checker.folder_has_any_xml(folder))

    def test_false_when_folder_missing(self):
        self.assertFalse(
            claims_checker.folder_has_any_xml(self.root / "MISSING")
        )


class ListFoldersWithoutXmlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_returns_sorted_names_and_total(self):
        make_patient_folder(
            self.root, "ZETA, ANNE", ["ZETA, ANNE-260924150960_CF4.xml"]
        )
        make_patient_folder(self.root, "bravo, john", [])
        make_patient_folder(self.root, "Alpha, Jane", [])
        (self.root / "stray.pdf").write_text("x", encoding="utf-8")

        names, total = claims_checker.list_folders_without_xml(self.root)

        self.assertEqual(names, ["Alpha, Jane", "bravo, john"])
        self.assertEqual(total, 2)

    def test_missing_root_returns_empty(self):
        names, total = claims_checker.list_folders_without_xml(
            self.root / "MISSING"
        )
        self.assertEqual((names, total), ([], 0))

    def test_blank_root_returns_empty(self):
        self.assertEqual(claims_checker.list_folders_without_xml(""), ([], 0))


class ListFoldersMissingXmlTests(unittest.TestCase):
    """Operator rule 2026-10-12: 3/3 required to leave the list.

    The panel must not agree/disagree with the Claims Checker, so this
    reuses the checker's own has_xml() rule.
    """

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _folder(self, name, kinds, pdfs=()):
        folder = self.root / name
        folder.mkdir(parents=True, exist_ok=True)
        for kind in kinds:
            (folder / f"{name}-260924150960_{kind}.xml").write_text(
                "{}", encoding="utf-8")
        for pdf in pdfs:
            (folder / pdf).write_text("x", encoding="utf-8")
        return folder

    def test_three_of_three_is_not_listed(self):
        self._folder("COMPLETE", ["CF4", "CF5", "ESOA"])
        self.assertEqual(claims_checker.list_folders_missing_xml(self.root),
                         ([], 0))

    def test_cf3_folder_needs_two_of_two(self):
        # Operator rule 2026-10-15: CF3.pdf present -> CF5 + eSOA
        # only. 2/2 removes it; 1/2 stays listed as (1/2).
        self._folder("NSD_FULL", ["CF5", "ESOA"], pdfs=["CF3.pdf"])
        self._folder("NSD_HALF", ["CF5"], pdfs=["CF3.pdf"])
        rows, total = claims_checker.list_folders_missing_xml(self.root)
        self.assertEqual(rows, ["NSD_HALF  (1/2)"])
        self.assertEqual(total, 1)

    def test_cf3_folder_with_no_xml_shows_zero_of_two(self):
        self._folder("NSD_NONE", [], pdfs=["CF3.pdf"])
        rows, total = claims_checker.list_folders_missing_xml(self.root)
        self.assertEqual(rows, ["NSD_NONE  (0/2)"])
        self.assertEqual(total, 1)

    def test_two_of_three_is_listed_with_count(self):
        self._folder("TWO", ["CF4", "CF5"])
        rows, total = claims_checker.list_folders_missing_xml(self.root)
        self.assertEqual(rows, ["TWO  (2/3)"])
        self.assertEqual(total, 1)

    def test_one_of_three_is_listed_with_count(self):
        self._folder("ONE", ["ESOA"])
        rows, total = claims_checker.list_folders_missing_xml(self.root)
        self.assertEqual(rows, ["ONE  (1/3)"])
        self.assertEqual(total, 1)

    def test_zero_of_three_is_listed_with_zero_count(self):
        self._folder("NONE", [])
        rows, total = claims_checker.list_folders_missing_xml(self.root)
        self.assertEqual(rows, ["NONE  (0/3)"])
        self.assertEqual(total, 1)

    def test_sorts_case_insensitively(self):
        self._folder("zeta", [])
        self._folder("Alpha", [])
        self._folder("bravo", ["CF4"])
        rows, total = claims_checker.list_folders_missing_xml(self.root)
        self.assertEqual(rows, ["Alpha  (0/3)", "bravo  (1/3)", "zeta  (0/3)"])
        self.assertEqual(total, 3)

    def test_stray_files_are_ignored(self):
        self._folder("PATIENT", [])
        (self.root / "loose.pdf").write_text("x", encoding="utf-8")
        rows, _ = claims_checker.list_folders_missing_xml(self.root)
        self.assertEqual(rows, ["PATIENT  (0/3)"])

    def test_missing_or_blank_root_returns_empty(self):
        self.assertEqual(
            claims_checker.list_folders_missing_xml(self.root / "MISSING"),
            ([], 0),
        )
        self.assertEqual(claims_checker.list_folders_missing_xml(""), ([], 0))

    def test_matches_the_claim_kinds_the_checker_uses(self):
        # The three kinds are the checker's own CLAIM_KINDS (cf4/cf5/esoa).
        self.assertEqual(
            sorted(claims_checker.CLAIM_KINDS), ["cf4", "cf5", "esoa"])
        self._folder("PATIENT", ["CF4", "CF5"])
        rows, _ = claims_checker.list_folders_missing_xml(self.root)
        self.assertTrue(rows[0].endswith("(2/3)"))


class OutputDirHasXmlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_true_when_xml_inside_patient_folder(self):
        make_patient_folder(
            self.root, "DOE, JUAN", ["DOE, JUAN-260924150960_CF4.xml"]
        )
        self.assertTrue(claims_checker.output_dir_has_xml(self.root))

    def test_true_when_xml_directly_in_output(self):
        (self.root / "loose.xml").write_text("{}", encoding="utf-8")
        self.assertTrue(claims_checker.output_dir_has_xml(self.root))

    def test_false_when_no_xml_anywhere(self):
        make_patient_folder(self.root, "DOE, JUAN", [])
        (self.root / "readme.pdf").write_text("x", encoding="utf-8")
        self.assertFalse(claims_checker.output_dir_has_xml(self.root))

    def test_false_when_missing_or_blank(self):
        self.assertFalse(
            claims_checker.output_dir_has_xml(self.root / "MISSING")
        )
        self.assertFalse(claims_checker.output_dir_has_xml(""))


if __name__ == "__main__":
    unittest.main()
