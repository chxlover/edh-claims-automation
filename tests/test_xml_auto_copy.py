from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.xml_auto_copy import (  # noqa: E402
    XmlAutoCopyService,
    extract_patient_name_from_xml_filename,
    normalize_patient_match_key,
)


class XmlAutoCopyServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.output = self.root / "output"
        self.incomplete = self.root / "incomplete"
        self.source.mkdir()
        self.output.mkdir()
        self.incomplete.mkdir()
        self.service = XmlAutoCopyService(
            self.source,
            self.output,
            self.incomplete,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def patient_folder(self, name="DELA CRUZ, JUAN S", *, incomplete=False):
        root = self.incomplete if incomplete else self.output
        folder = root / f"{name} - 000000000012345 - ADM20260701_DIS20260703"
        folder.mkdir()
        return folder

    def xml_file(self, doc_type="CF4", content=b"generated-xml"):
        path = self.source / f"DELA CRUZ, JUAN, S-260728123456_{doc_type}.xml"
        path.write_bytes(content)
        return path

    def test_extracts_supported_hbsys_patient_name(self):
        name = extract_patient_name_from_xml_filename(
            "DELA CRUZ, JUAN, S-260728123456_eSOA.xml"
        )
        self.assertEqual(name, "DELA CRUZ, JUAN S")

    def test_unicode_enye_matches_plain_n_folder_name(self):
        self.assertEqual(
            normalize_patient_match_key("PEÑA, JUAN"),
            normalize_patient_match_key("PENA, JUAN"),
        )

    def test_requires_two_unchanged_polls_before_copy(self):
        folder = self.patient_folder()
        source = self.xml_file()

        first = self.service.scan_once()
        self.assertEqual(first.waiting_count, 1)
        self.assertFalse((folder / source.name).exists())

        second = self.service.scan_once()
        self.assertEqual(second.copied_count, 1)
        self.assertEqual((folder / source.name).read_bytes(), b"generated-xml")
        self.assertTrue(source.exists())

    def test_changed_source_waits_for_another_unchanged_poll(self):
        folder = self.patient_folder()
        source = self.xml_file()
        self.service.scan_once()
        source.write_bytes(b"changed-generated-xml")

        changed = self.service.scan_once()
        self.assertEqual(changed.waiting_count, 1)
        self.assertFalse((folder / source.name).exists())
        self.assertEqual(self.service.scan_once().copied_count, 1)

    def test_cf4_cf5_and_esoa_are_each_copied_once(self):
        folder = self.patient_folder()
        files = [self.xml_file(doc, doc.encode()) for doc in ("CF4", "CF5", "eSOA")]
        self.service.scan_once()
        result = self.service.scan_once()
        self.assertEqual(result.copied_count, 3)
        self.assertTrue(all((folder / item.name).exists() for item in files))

    def test_unsupported_xml_is_ignored(self):
        self.patient_folder()
        (self.source / "DELA CRUZ, JUAN, S-123_CF3.xml").write_bytes(b"cf3")
        result = self.service.scan_once(require_observed_stable=False)
        self.assertEqual(result.events, ())

    def test_multiple_output_confinements_are_ambiguous(self):
        self.patient_folder()
        second = self.output / "DELA CRUZ, JUAN S - 000000000012345 - ADM20260710_DIS20260712"
        second.mkdir()
        self.xml_file()
        result = self.service.scan_once(require_observed_stable=False)
        self.assertEqual(result.count("AMBIGUOUS"), 1)
        self.assertFalse(
            any(any(folder.glob("*.xml")) for folder in self.output.iterdir())
        )

    def test_output_has_priority_over_incomplete(self):
        output_folder = self.patient_folder()
        incomplete_folder = self.patient_folder(incomplete=True)
        source = self.xml_file()
        result = self.service.scan_once(require_observed_stable=False)
        self.assertEqual(result.copied_count, 1)
        self.assertTrue((output_folder / source.name).exists())
        self.assertFalse((incomplete_folder / source.name).exists())

    def test_incomplete_is_used_when_output_has_no_match(self):
        folder = self.patient_folder(incomplete=True)
        source = self.xml_file()
        result = self.service.scan_once(require_observed_stable=False)
        self.assertEqual(result.copied_count, 1)
        self.assertTrue((folder / source.name).exists())

    def test_multiple_incomplete_confinements_are_ambiguous(self):
        self.patient_folder(incomplete=True)
        second = self.incomplete / (
            "DELA CRUZ, JUAN S - 000000000012345 - ADM20260710_DIS20260712"
        )
        second.mkdir()
        self.xml_file()
        result = self.service.scan_once(require_observed_stable=False)
        self.assertEqual(result.count("AMBIGUOUS"), 1)
        self.assertFalse(
            any(any(folder.glob("*.xml")) for folder in self.incomplete.iterdir())
        )

    def test_unmatched_retries_after_patient_folder_is_created(self):
        source = self.xml_file()
        first = self.service.scan_once(require_observed_stable=False)
        self.assertEqual(first.count("UNMATCHED"), 1)
        folder = self.patient_folder()
        second = self.service.scan_once(require_observed_stable=False)
        self.assertEqual(second.copied_count, 1)
        self.assertTrue((folder / source.name).exists())

    def test_identical_destination_is_not_overwritten(self):
        folder = self.patient_folder()
        source = self.xml_file()
        destination = folder / source.name
        destination.write_bytes(source.read_bytes())
        original_mtime = destination.stat().st_mtime_ns
        result = self.service.scan_once(require_observed_stable=False)
        self.assertEqual(result.count("IDENTICAL_SKIP"), 1)
        self.assertEqual(destination.stat().st_mtime_ns, original_mtime)

    def test_different_destination_is_preserved_as_conflict(self):
        folder = self.patient_folder()
        source = self.xml_file()
        destination = folder / source.name
        destination.write_bytes(b"existing-different-content")
        result = self.service.scan_once(require_observed_stable=False)
        self.assertEqual(result.count("CONFLICT"), 1)
        self.assertEqual(destination.read_bytes(), b"existing-different-content")

    def test_missing_source_is_nonfatal_and_logged_once_per_state(self):
        missing = self.root / "missing"
        service = XmlAutoCopyService(missing, self.output, self.incomplete)
        first = service.scan_once()
        second = service.scan_once()
        self.assertEqual(first.count("SOURCE_UNAVAILABLE"), 1)
        self.assertTrue(first.events[0].reportable)
        self.assertFalse(second.events[0].reportable)

    def test_empty_source_configuration_does_not_scan_working_directory(self):
        service = XmlAutoCopyService("", self.output, self.incomplete)
        result = service.scan_once()
        self.assertEqual(result.count("SOURCE_UNAVAILABLE"), 1)


if __name__ == "__main__":
    unittest.main()
