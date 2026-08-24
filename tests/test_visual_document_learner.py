from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import fitz  # noqa: E402

from core.visual_document_learner import (  # noqa: E402
    VisualDocumentLearner,
)


def build_form(form_type: str, patient: str, *, landscape: bool = False) -> bytes:
    document = fitz.open()
    width, height = ((792, 612) if landscape else (612, 792))
    page = document.new_page(width=width, height=height)
    page.insert_text((55, 55), form_type, fontsize=18)
    page.insert_text((60, 90), f"PATIENT: {patient}", fontsize=10)

    if form_type == "CLAIM SIGNATURE FORM":
        page.draw_rect((45, 110, width - 45, height - 60))
        for y in range(145, int(height - 80), 42):
            page.draw_line((60, y), (width - 60, y))
        page.insert_text((75, 180), "MEMBER CERTIFICATION", fontsize=11)
    elif form_type == "BENEFIT ELIGIBILITY":
        for x in range(70, int(width - 60), 85):
            page.draw_rect((x, 140, min(x + 55, width - 40), 205))
        for y in range(270, int(height - 70), 75):
            page.draw_circle((width / 2, y), 20)
            page.draw_line((90, y), (width - 90, y))
        page.insert_text((75, 235), "HCI PORTAL REFERENCE", fontsize=11)
    else:
        page.draw_rect((70, 130, width - 70, height - 80))
        page.draw_line((width / 2, 130), (width / 2, height - 80))
    payload = document.tobytes()
    document.close()
    return payload


class VisualDocumentLearnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.messages: list[str] = []
        self.learner = VisualDocumentLearner(
            self.root / "visual.db", log_callback=self.messages.append
        )

    def tearDown(self):
        self.temporary.cleanup()

    def train(self, doc_type: str, form_title: str, count: int) -> None:
        for index in range(count):
            payload = build_form(form_title, f"PATIENT {doc_type} {index}")
            features = self.learner.extract_features(payload)
            result = self.learner.confirm_sample(
                features,
                doc_type,
                source_name=f"sample_{index}.pdf",
                source_path=self.root / f"private_{index}.pdf",
            )
            self.assertTrue(result.saved)

    def test_starts_in_shadow_mode(self):
        self.assertFalse(self.learner.auto_enabled())
        self.assertEqual(self.learner.model_revision(), 0)

    def test_duplicate_sample_does_not_inflate_maturity(self):
        features = self.learner.extract_features(
            build_form("CLAIM SIGNATURE FORM", "DUPLICATE")
        )
        first = self.learner.confirm_sample(features, "CSF")
        second = self.learner.confirm_sample(features, "CSF")
        self.assertTrue(first.saved)
        self.assertTrue(second.duplicate)
        stats = self.learner.class_statistics()
        self.assertEqual(stats[0]["active_samples"], 1)

    def test_ordinary_type_requires_three_samples_and_competing_class(self):
        self.train("CSF", "CLAIM SIGNATURE FORM", 2)
        self.train("COE", "BENEFIT ELIGIBILITY", 3)
        _, early = self.learner.predict(
            build_form("CLAIM SIGNATURE FORM", "EARLY"), record=False
        )
        self.assertEqual(early.predicted_type, "CSF")
        self.assertFalse(early.auto_eligible)
        self.assertIn("needs 3 distinct samples", early.blocked_reason)

        self.train("CSF", "CLAIM SIGNATURE FORM", 1)
        _, mature = self.learner.predict(
            build_form("CLAIM SIGNATURE FORM", "MATURE"), record=False
        )
        self.assertTrue(mature.auto_eligible, mature.blocked_reason)
        self.assertFalse(mature.auto_applied)

        self.learner.set_auto_enabled(True)
        _, enabled = self.learner.predict(
            build_form("CLAIM SIGNATURE FORM", "AUTO"), record=False
        )
        self.assertTrue(enabled.auto_applied)

    def test_sensitive_type_requires_five_samples(self):
        self.train("DTR", "DIAGNOSTIC TEST RESULT", 4)
        self.train("COE", "BENEFIT ELIGIBILITY", 3)
        _, prediction = self.learner.predict(
            build_form("DIAGNOSTIC TEST RESULT", "FOUR"), record=False
        )
        self.assertEqual(prediction.predicted_type, "DTR")
        self.assertFalse(prediction.auto_eligible)
        self.assertIn("needs 5 distinct samples", prediction.blocked_reason)

    def test_one_class_cannot_auto_classify(self):
        self.train("CSF", "CLAIM SIGNATURE FORM", 3)
        _, prediction = self.learner.predict(
            build_form("CLAIM SIGNATURE FORM", "ONLY CLASS"), record=False
        )
        self.assertFalse(prediction.auto_eligible)
        self.assertIn("at least two document types", prediction.blocked_reason)

    def test_deterministic_result_is_never_replaced(self):
        self.train("CSF", "CLAIM SIGNATURE FORM", 3)
        _, prediction = self.learner.predict(
            build_form("CLAIM SIGNATURE FORM", "KNOWN"),
            deterministic_type="COE",
            record=False,
        )
        self.assertFalse(prediction.has_suggestion)
        self.assertIn("already selected COE", prediction.blocked_reason)

    def test_corrupt_and_landscape_documents_defer_safely(self):
        features, corrupt = self.learner.predict(b"not a pdf", record=False)
        self.assertIsNone(features)
        self.assertFalse(corrupt.has_suggestion)

        features = self.learner.extract_features(
            build_form("CLAIM SIGNATURE FORM", "LANDSCAPE", landscape=True)
        )
        self.assertTrue(features.orientation_uncertain)
        prediction = self.learner.predict_features(features, record=False)
        self.assertIn("orientation", prediction.blocked_reason.lower())

    def test_prediction_feedback_and_negative_memory_are_audited(self):
        self.train("CSF", "CLAIM SIGNATURE FORM", 3)
        self.train("COE", "BENEFIT ELIGIBILITY", 3)
        features, prediction = self.learner.predict(
            build_form("CLAIM SIGNATURE FORM", "CORRECTED"), record=True
        )
        self.assertIsNotNone(prediction.prediction_id)
        self.learner.record_feedback(prediction.prediction_id, "COE")

        confusion = self.learner.confusion_statistics()
        self.assertEqual(len(confusion), 1)
        self.assertEqual(confusion[0]["predicted_type"], "CSF")
        self.assertEqual(confusion[0]["final_type"], "COE")
        self.assertEqual(confusion[0]["decision_count"], 1)
        self.learner.confirm_sample(features, "COE")
        self.assertTrue(self.learner._negative_match_blocks(features, "CSF"))

    def test_only_derived_features_and_hashed_path_are_stored(self):
        source_path = self.root / "PATIENT PRIVATE NAME.pdf"
        features = self.learner.extract_features(
            build_form("CLAIM SIGNATURE FORM", "PRIVATE PATIENT")
        )
        self.learner.confirm_sample(
            features, "CSF", source_name="UNKNOWN.pdf", source_path=source_path
        )
        connection = sqlite3.connect(self.root / "visual.db")
        try:
            row = connection.execute(
                "SELECT * FROM visual_document_samples"
            ).fetchone()
            columns = [item[1] for item in connection.execute(
                "PRAGMA table_info(visual_document_samples)"
            )]
        finally:
            connection.close()
        record = dict(zip(columns, row))
        self.assertNotIn(str(source_path), record.values())
        self.assertEqual(len(record["source_path_hash"]), 64)
        for field in (
            "edge_grid", "horizontal_projection", "vertical_projection", "orb_descriptors"
        ):
            self.assertNotIn(b"%PDF", record[field])

    def test_sample_can_be_disabled_and_reenabled(self):
        features = self.learner.extract_features(
            build_form("CLAIM SIGNATURE FORM", "TOGGLE")
        )
        result = self.learner.confirm_sample(features, "CSF")
        self.assertTrue(self.learner.set_sample_enabled(result.sample_id, False))
        self.assertEqual(self.learner.class_statistics()[0]["active_samples"], 0)
        self.assertTrue(self.learner.set_sample_enabled(result.sample_id, True))
        self.assertEqual(self.learner.class_statistics()[0]["active_samples"], 1)


if __name__ == "__main__":
    unittest.main()
