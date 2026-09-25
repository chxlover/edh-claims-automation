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
