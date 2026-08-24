"""Privacy-preserving visual learning for reviewed claim documents.

The module stores derived, non-readable layout features in SQLite. It never
copies patient PDFs or images, never displays a GUI, and never replaces the
existing OCR/document rules. Automatic classification is opt-in and guarded
by class maturity, confidence, margin, visual-consensus, and negative feedback.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Callable

from core.activity_logger import logger


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = Path(os.getenv("CLAIMS_SQLITE_DB", str(BASE_DIR / "claims.db")))
FEATURE_VERSION = 1
AUTO_SETTING_KEY = "visual_learning_auto_enabled"

LEARNABLE_TYPES = frozenset(
    {
        "SOA1", "SOA2", "SOA2_page1", "SOA2_page2", "DTR",
        "MRF_page1", "MRF_page2", "COE", "MMC", "CSF", "CF2",
        "CF2_page1", "CF2_page2", "PBC_page1", "PBC_page2", "OPR", "ANR",
    }
)
SENSITIVE_TYPES = frozenset(
    {
        "SOA2_page1", "SOA2_page2", "MRF_page1", "MRF_page2",
        "CF2", "CF2_page1", "CF2_page2", "DTR",
    }
)


def normalize_doc_type(value: object) -> str:
    raw = str(value or "").strip()
    for item in LEARNABLE_TYPES:
        if raw.upper() == item.upper():
            return item
    return raw


@dataclass(frozen=True, slots=True)
class VisualFeatures:
    file_hash: str
    page_count: int
    width: int
    height: int
    phash: str
    dhash: str
    edge_grid: bytes
    horizontal_projection: bytes
    vertical_projection: bytes
    orb_descriptors: bytes
    orb_count: int
    quality: float
    orientation_uncertain: bool
    feature_version: int = FEATURE_VERSION


@dataclass(frozen=True, slots=True)
class VisualCandidate:
    doc_type: str
    confidence: float
    raw_score: float
    sample_count: int
    visual_votes: int
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VisualPrediction:
    predicted_type: str = ""
    confidence: float = 0.0
    margin: float = 0.0
    sample_count: int = 0
    visual_votes: int = 0
    auto_eligible: bool = False
    auto_applied: bool = False
    blocked_reason: str = ""
    evidence: tuple[str, ...] = ()
    candidates: tuple[VisualCandidate, ...] = ()
    file_hash: str = ""
    model_revision: int = 0
    prediction_id: int | None = None

    @property
    def has_suggestion(self) -> bool:
        return bool(self.predicted_type)

    def summary(self) -> str:
        if not self.predicted_type:
            return self.blocked_reason or "No trusted visual suggestion"
        mode = "AUTO READY" if self.auto_eligible else "SUGGESTION ONLY"
        return (
            f"{self.predicted_type} | {self.confidence:.1%} | "
            f"margin {self.margin:.1%} | {self.sample_count} sample(s) | {mode}"
        )


@dataclass(frozen=True, slots=True)
class TrainingResult:
    saved: bool
    duplicate: bool
    sample_id: int | None
    message: str


class VisualDocumentLearner:
    """Extract, persist, compare, and audit visual document fingerprints."""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB,
        log_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self._lock = RLock()
        self._log_callback = log_callback
        self._initialize_database()

    def _log(self, level: str, message: str) -> None:
        if self._log_callback:
            self._log_callback(message)
            return
        getattr(logger, level, logger.info)(message)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        with self._lock, self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS visual_learning_settings(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS visual_document_samples(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_hash TEXT NOT NULL UNIQUE,
                    doc_type TEXT NOT NULL,
                    feature_version INTEGER NOT NULL,
                    page_count INTEGER NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    phash TEXT NOT NULL,
                    dhash TEXT NOT NULL,
                    edge_grid BLOB NOT NULL,
                    horizontal_projection BLOB NOT NULL,
                    vertical_projection BLOB NOT NULL,
                    orb_descriptors BLOB NOT NULL,
                    orb_count INTEGER NOT NULL,
                    quality REAL NOT NULL,
                    orientation_uncertain INTEGER NOT NULL DEFAULT 0,
                    source_name TEXT NOT NULL DEFAULT '',
                    source_path_hash TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    confirmed_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_visual_samples_type_enabled
                ON visual_document_samples(doc_type, enabled);

                CREATE TABLE IF NOT EXISTS visual_predictions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prediction_key TEXT NOT NULL UNIQUE,
                    file_hash TEXT NOT NULL,
                    model_revision INTEGER NOT NULL,
                    predicted_type TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    margin REAL NOT NULL DEFAULT 0,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    auto_eligible INTEGER NOT NULL DEFAULT 0,
                    auto_applied INTEGER NOT NULL DEFAULT 0,
                    final_type TEXT NOT NULL DEFAULT '',
                    correct INTEGER,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    feedback_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_visual_predictions_type
                ON visual_predictions(predicted_type, feedback_at);

                CREATE TABLE IF NOT EXISTS visual_negative_feedback(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_hash TEXT NOT NULL,
                    rejected_type TEXT NOT NULL,
                    actual_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(file_hash, rejected_type, actual_type)
                );
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO visual_learning_settings(key, value)
                VALUES(?, '0')
                """,
                (AUTO_SETTING_KEY,),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO visual_learning_settings(key, value)
                VALUES('model_revision', '0')
                """
            )

    @staticmethod
    def _dependencies():
        try:
            import cv2
            import fitz
            import numpy as np
        except Exception as exc:
            raise RuntimeError(f"Visual learning dependencies unavailable: {exc}") from exc
        return cv2, fitz, np

    @staticmethod
    def _read_source(source: str | Path | bytes) -> tuple[bytes, str]:
        if isinstance(source, bytes):
            return source, "memory.pdf"
        path = Path(source)
        return path.read_bytes(), path.name

    @classmethod
    def extract_features(cls, source: str | Path | bytes) -> VisualFeatures:
        cv2, fitz, np = cls._dependencies()
        payload, _ = cls._read_source(source)
        file_hash = hashlib.sha256(payload).hexdigest()
        if not payload:
            raise ValueError("PDF is empty")

        try:
            document = fitz.open(stream=payload, filetype="pdf")
        except Exception as exc:
            raise ValueError(f"Unreadable PDF: {exc}") from exc
        try:
            if document.page_count < 1:
                raise ValueError("PDF has no pages")
            page = document.load_page(0)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )
            if pixmap.n >= 3:
                gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_RGB2GRAY)
            else:
                gray = image[:, :, 0]
            normalized, orientation_uncertain = cls._normalize_page(gray, cv2, np)
            return cls._features_from_image(
                normalized,
                file_hash=file_hash,
                page_count=document.page_count,
                orientation_uncertain=orientation_uncertain,
                cv2=cv2,
                np=np,
            )
        finally:
            document.close()

    @staticmethod
    def _normalize_page(gray, cv2, np):
        if gray is None or gray.size < 100:
            raise ValueError("Rendered page is blank or too small")
        gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        binary = cv2.threshold(
            blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )[1]

        points = cv2.findNonZero(binary)
        if points is None or len(points) < 80:
            raise ValueError("Rendered page has insufficient visual content")
        x, y, width, height = cv2.boundingRect(points)
        margin = max(8, int(min(gray.shape) * 0.015))
        x0, y0 = max(0, x - margin), max(0, y - margin)
        x1 = min(gray.shape[1], x + width + margin)
        y1 = min(gray.shape[0], y + height + margin)
        cropped = gray[y0:y1, x0:x1]

        lines = cv2.HoughLinesP(
            cv2.Canny(cropped, 60, 160), 1, np.pi / 180, 80,
            minLineLength=max(60, cropped.shape[1] // 5), maxLineGap=15,
        )
        angles: list[float] = []
        if lines is not None:
            for line in lines[:100]:
                x_start, y_start, x_end, y_end = line[0]
                angle = math.degrees(math.atan2(y_end - y_start, x_end - x_start))
                if -12 <= angle <= 12:
                    angles.append(angle)
        skew = float(np.median(angles)) if angles else 0.0
        orientation_uncertain = abs(skew) > 8 or cropped.shape[0] < cropped.shape[1] * 0.75
        if 0.2 < abs(skew) <= 8:
            center = (cropped.shape[1] / 2, cropped.shape[0] / 2)
            matrix = cv2.getRotationMatrix2D(center, skew, 1.0)
            cropped = cv2.warpAffine(
                cropped, matrix, (cropped.shape[1], cropped.shape[0]),
                flags=cv2.INTER_LINEAR, borderValue=255,
            )

        canvas = np.full((1024, 768), 255, dtype=np.uint8)
        scale = min(768 / cropped.shape[1], 1024 / cropped.shape[0])
        resized = cv2.resize(
            cropped,
            (max(1, int(cropped.shape[1] * scale)), max(1, int(cropped.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
        top = (1024 - resized.shape[0]) // 2
        left = (768 - resized.shape[1]) // 2
        canvas[top:top + resized.shape[0], left:left + resized.shape[1]] = resized
        return canvas, orientation_uncertain

    @staticmethod
    def _hash_bits(image, cv2, np, *, difference=False) -> str:
        if difference:
            small = cv2.resize(image, (9, 8), interpolation=cv2.INTER_AREA)
            bits = (small[:, 1:] > small[:, :-1]).flatten()
        else:
            small = cv2.resize(image, (32, 32), interpolation=cv2.INTER_AREA)
            transform = cv2.dct(np.float32(small))[:8, :8]
            median = np.median(transform[1:, :])
            bits = (transform > median).flatten()
        value = 0
        for bit in bits:
            value = (value << 1) | int(bool(bit))
        return f"{value:016x}"

    @classmethod
    def _features_from_image(
        cls, image, *, file_hash, page_count, orientation_uncertain, cv2, np
    ) -> VisualFeatures:
        edges = cv2.Canny(image, 60, 160)
        edge_grid = cv2.resize(edges, (16, 16), interpolation=cv2.INTER_AREA).astype(np.float32)
        edge_grid /= 255.0
        horizontal = cv2.resize(edges.mean(axis=1).reshape(-1, 1), (1, 64)).astype(np.float32)
        vertical = cv2.resize(edges.mean(axis=0).reshape(1, -1), (64, 1)).astype(np.float32)
        horizontal /= max(1.0, float(horizontal.max()))
        vertical /= max(1.0, float(vertical.max()))

        orb = cv2.ORB_create(nfeatures=450, fastThreshold=12)
        _, descriptors = orb.detectAndCompute(image, None)
        if descriptors is None:
            descriptors = np.empty((0, 32), dtype=np.uint8)
        ink_ratio = float((image < 230).mean())
        feature_density = min(1.0, descriptors.shape[0] / 180.0)
        ink_quality = min(1.0, ink_ratio / 0.08) if ink_ratio > 0 else 0.0
        quality = round((feature_density * 0.65) + (ink_quality * 0.35), 4)

        return VisualFeatures(
            file_hash=file_hash,
            page_count=int(page_count),
            width=int(image.shape[1]),
            height=int(image.shape[0]),
            phash=cls._hash_bits(image, cv2, np),
            dhash=cls._hash_bits(image, cv2, np, difference=True),
            edge_grid=zlib.compress(edge_grid.tobytes()),
            horizontal_projection=zlib.compress(horizontal.tobytes()),
            vertical_projection=zlib.compress(vertical.tobytes()),
            orb_descriptors=zlib.compress(descriptors.tobytes()),
            orb_count=int(descriptors.shape[0]),
            quality=quality,
            orientation_uncertain=bool(orientation_uncertain),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _source_path_hash(source_path: str | Path | None) -> str:
        if not source_path:
            return ""
        normalized = os.path.normcase(os.path.abspath(str(source_path)))
        return hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()

    def confirm_sample(
        self,
        features: VisualFeatures,
        doc_type: str,
        *,
        source_name: str = "",
        source_path: str | Path | None = None,
    ) -> TrainingResult:
        normalized_type = normalize_doc_type(doc_type)
        if normalized_type not in LEARNABLE_TYPES:
            return TrainingResult(False, False, None, f"{doc_type} is not learnable")
        if features.feature_version != FEATURE_VERSION:
            raise ValueError("Unsupported visual feature version")

        now = self._now()
        with self._lock, self._connection() as connection:
            existing = connection.execute(
                "SELECT id, doc_type FROM visual_document_samples WHERE file_hash = ?",
                (features.file_hash,),
            ).fetchone()
            if existing and existing["doc_type"] == normalized_type:
                return TrainingResult(
                    False, True, int(existing["id"]),
                    "Duplicate visual sample ignored; maturity count unchanged",
                )

            values = (
                normalized_type, features.feature_version, features.page_count,
                features.width, features.height, features.phash, features.dhash,
                features.edge_grid, features.horizontal_projection,
                features.vertical_projection, features.orb_descriptors,
                features.orb_count, features.quality,
                int(features.orientation_uncertain), Path(source_name).name,
                self._source_path_hash(source_path), now, now, features.file_hash,
            )
            if existing:
                connection.execute(
                    """
                    UPDATE visual_document_samples SET
                        doc_type=?, feature_version=?, page_count=?, width=?, height=?,
                        phash=?, dhash=?, edge_grid=?, horizontal_projection=?,
                        vertical_projection=?, orb_descriptors=?, orb_count=?, quality=?,
                        orientation_uncertain=?, source_name=?, source_path_hash=?,
                        enabled=1, confirmed_at=?, updated_at=? WHERE file_hash=?
                    """,
                    values,
                )
                sample_id = int(existing["id"])
                message = f"Visual sample corrected to {normalized_type}"
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO visual_document_samples(
                        doc_type, feature_version, page_count, width, height, phash,
                        dhash, edge_grid, horizontal_projection, vertical_projection,
                        orb_descriptors, orb_count, quality, orientation_uncertain,
                        source_name, source_path_hash, confirmed_at, updated_at, file_hash
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    values,
                )
                sample_id = int(cursor.lastrowid)
                message = f"Visual sample confirmed as {normalized_type}"
            self._increment_revision(connection)
        self._log("success", message)
        return TrainingResult(True, False, sample_id, message)

    @staticmethod
    def _increment_revision(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT value FROM visual_learning_settings WHERE key='model_revision'"
        ).fetchone()
        revision = int(row[0] if row else 0) + 1
        connection.execute(
            """
            INSERT INTO visual_learning_settings(key, value) VALUES('model_revision', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (str(revision),),
        )
        return revision

    def model_revision(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM visual_learning_settings WHERE key='model_revision'"
            ).fetchone()
        return int(row[0] if row else 0)

    def auto_enabled(self) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM visual_learning_settings WHERE key=?",
                (AUTO_SETTING_KEY,),
            ).fetchone()
        return str(row[0] if row else "0").strip().lower() in {"1", "true", "yes", "on"}

    def set_auto_enabled(self, enabled: bool) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO visual_learning_settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (AUTO_SETTING_KEY, "1" if enabled else "0"),
            )
        self._log("info", f"Visual auto-classification {'enabled' if enabled else 'disabled'}")

    @staticmethod
    def _hamming(left: str, right: str) -> float:
        try:
            differing = (int(left, 16) ^ int(right, 16)).bit_count()
            return max(0.0, 1.0 - (differing / 64.0))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _float_array(blob: bytes, np):
        return np.frombuffer(zlib.decompress(blob), dtype=np.float32)

    @staticmethod
    def _cosine(left, right, np) -> float:
        if left.size != right.size or left.size == 0:
            return 0.0
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        if denominator <= 1e-9:
            return 0.0
        return max(0.0, min(1.0, float(np.dot(left, right) / denominator)))

    @staticmethod
    def _orb_array(blob: bytes, count: int, np):
        if not blob or count <= 0:
            return np.empty((0, 32), dtype=np.uint8)
        raw = np.frombuffer(zlib.decompress(blob), dtype=np.uint8)
        if raw.size != count * 32:
            return np.empty((0, 32), dtype=np.uint8)
        return raw.reshape(count, 32)

    @classmethod
    def _similarity(cls, query: VisualFeatures, sample: sqlite3.Row):
        cv2, _, np = cls._dependencies()
        phash = cls._hamming(query.phash, sample["phash"])
        dhash = cls._hamming(query.dhash, sample["dhash"])
        edge = cls._cosine(
            cls._float_array(query.edge_grid, np),
            cls._float_array(sample["edge_grid"], np), np,
        )
        horizontal = cls._cosine(
            cls._float_array(query.horizontal_projection, np),
            cls._float_array(sample["horizontal_projection"], np), np,
        )
        vertical = cls._cosine(
            cls._float_array(query.vertical_projection, np),
            cls._float_array(sample["vertical_projection"], np), np,
        )
        projection = (horizontal + vertical) / 2.0

        query_orb = cls._orb_array(query.orb_descriptors, query.orb_count, np)
        sample_orb = cls._orb_array(sample["orb_descriptors"], sample["orb_count"], np)
        orb_score = 0.0
        if len(query_orb) >= 8 and len(sample_orb) >= 8:
            matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
            matches = matcher.knnMatch(query_orb, sample_orb, k=2)
            good = [pair[0] for pair in matches if len(pair) == 2 and pair[0].distance < 0.72 * pair[1].distance]
            orb_score = min(1.0, len(good) / max(20.0, min(len(query_orb), len(sample_orb)) * 0.35))

        raw = (
            phash * 0.22 + dhash * 0.08 + edge * 0.27
            + projection * 0.23 + orb_score * 0.20
        )
        votes = sum(
            (phash >= 0.84, dhash >= 0.82, edge >= 0.90,
             projection >= 0.90, orb_score >= 0.45)
        )
        components = {
            "pHash": phash, "dHash": dhash, "layout": edge,
            "lines": projection, "ORB": orb_score,
        }
        return raw, votes, components

    @staticmethod
    def _calibrated_confidence(raw_score: float, votes: int) -> float:
        base = max(0.0, min(1.0, (raw_score - 0.50) / 0.45))
        consensus = min(1.0, votes / 4.0)
        return max(0.0, min(0.999, base * 0.80 + consensus * 0.20))

    def _enabled_samples(self) -> list[sqlite3.Row]:
        with self._connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT * FROM visual_document_samples
                    WHERE enabled=1 AND feature_version=? ORDER BY id
                    """,
                    (FEATURE_VERSION,),
                ).fetchall()
            )

    def predict_features(
        self,
        features: VisualFeatures,
        *,
        deterministic_type: str = "UNKNOWN",
        record: bool = True,
    ) -> VisualPrediction:
        if deterministic_type and deterministic_type.upper() != "UNKNOWN":
            return VisualPrediction(
                blocked_reason=f"Deterministic classifier already selected {deterministic_type}",
                file_hash=features.file_hash,
                model_revision=self.model_revision(),
            )
        if features.quality < 0.35:
            return VisualPrediction(
                blocked_reason="Visual quality is too low",
                file_hash=features.file_hash,
                model_revision=self.model_revision(),
            )
        if features.orientation_uncertain:
            return VisualPrediction(
                blocked_reason="Page orientation/layout is uncertain",
                file_hash=features.file_hash,
                model_revision=self.model_revision(),
            )

        samples = self._enabled_samples()
        revision = self.model_revision()
        if not samples:
            return VisualPrediction(
                blocked_reason="No confirmed visual samples yet",
                file_hash=features.file_hash,
                model_revision=revision,
            )

        type_counts: dict[str, int] = {}
        best_by_type: dict[str, tuple[float, int, dict[str, float], sqlite3.Row]] = {}
        for sample in samples:
            doc_type = sample["doc_type"]
            type_counts[doc_type] = type_counts.get(doc_type, 0) + 1
            raw, votes, components = self._similarity(features, sample)
            previous = best_by_type.get(doc_type)
            if previous is None or raw > previous[0]:
                best_by_type[doc_type] = (raw, votes, components, sample)

        candidates: list[VisualCandidate] = []
        for doc_type, (raw, votes, components, _) in best_by_type.items():
            confidence = self._calibrated_confidence(raw, votes)
            evidence = tuple(
                f"{name}={score:.0%}" for name, score in components.items()
            )
            candidates.append(
                VisualCandidate(
                    doc_type, confidence, raw, type_counts[doc_type], votes, evidence
                )
            )
        candidates.sort(key=lambda item: (item.confidence, item.raw_score), reverse=True)
        top = candidates[0]
        measurement_names = ("pHash", "dHash", "layout", "lines", "ORB")
        measurement_winners = {
            measurement: max(
                best_by_type,
                key=lambda doc_type: best_by_type[doc_type][2][measurement],
            )
            for measurement in measurement_names
        }
        consensus_votes = sum(
            winner == top.doc_type for winner in measurement_winners.values()
        )
        top = replace(
            top,
            visual_votes=consensus_votes,
            evidence=top.evidence
            + (f"measurement_consensus={consensus_votes}/{len(measurement_names)}",),
        )
        candidates[0] = top
        second_confidence = candidates[1].confidence if len(candidates) > 1 else 0.0
        margin = max(0.0, top.confidence - second_confidence)
        min_samples = 5 if top.doc_type in SENSITIVE_TYPES else 3
        min_confidence = 0.97 if top.doc_type in SENSITIVE_TYPES else 0.95

        blocked: list[str] = []
        if len(candidates) < 2:
            blocked.append("needs examples from at least two document types")
        if top.sample_count < min_samples:
            blocked.append(f"needs {min_samples} distinct samples")
        if top.confidence < min_confidence:
            blocked.append(f"confidence below {min_confidence:.0%}")
        if margin < 0.15:
            blocked.append("top-two margin below 15%")
        if top.visual_votes < 3:
            blocked.append("visual measurements do not agree")
        if self._negative_match_blocks(features, top.doc_type):
            blocked.append("similar prior prediction was corrected")

        eligible = not blocked
        auto_applied = eligible and self.auto_enabled()
        prediction = VisualPrediction(
            predicted_type=top.doc_type,
            confidence=top.confidence,
            margin=margin,
            sample_count=top.sample_count,
            visual_votes=top.visual_votes,
            auto_eligible=eligible,
            auto_applied=auto_applied,
            blocked_reason="; ".join(blocked),
            evidence=top.evidence,
            candidates=tuple(candidates[:3]),
            file_hash=features.file_hash,
            model_revision=revision,
        )
        if record:
            prediction_id = self.record_prediction(prediction)
            prediction = replace(prediction, prediction_id=prediction_id)
        candidate_summary = ", ".join(
            f"{item.doc_type}:{item.confidence:.1%}" for item in prediction.candidates
        ) or "none"
        outcome = (
            "AUTO"
            if prediction.auto_applied
            else "AUTO-ELIGIBLE SHADOW"
            if prediction.auto_eligible
            else "SUGGESTION"
        )
        self._log(
            "info",
            "Visual decision "
            f"source=UNKNOWN candidate={prediction.predicted_type or 'NONE'} "
            f"confidence={prediction.confidence:.1%} margin={prediction.margin:.1%} "
            f"outcome={outcome} candidates=[{candidate_summary}] "
            f"guard={prediction.blocked_reason or 'passed'}",
        )
        return prediction

    def predict(
        self,
        source: str | Path | bytes,
        *,
        deterministic_type: str = "UNKNOWN",
        record: bool = True,
    ) -> tuple[VisualFeatures | None, VisualPrediction]:
        try:
            features = self.extract_features(source)
            return features, self.predict_features(
                features, deterministic_type=deterministic_type, record=record
            )
        except Exception as exc:
            self._log("warning", f"Visual learning deferred: {exc}")
            return None, VisualPrediction(blocked_reason=str(exc))

    def _negative_match_blocks(self, features: VisualFeatures, candidate_type: str) -> bool:
        with self._connection() as connection:
            rows = list(
                connection.execute(
                    """
                    SELECT s.* FROM visual_negative_feedback n
                    JOIN visual_document_samples s ON s.file_hash=n.file_hash
                    WHERE n.rejected_type=? AND s.enabled=1
                    """,
                    (candidate_type,),
                ).fetchall()
            )
        for row in rows:
            raw, votes, _ = self._similarity(features, row)
            if raw >= 0.88 and votes >= 3:
                return True
        return False

    def record_prediction(self, prediction: VisualPrediction) -> int | None:
        if not prediction.file_hash:
            return None
        key_source = f"{prediction.file_hash}|{prediction.model_revision}"
        prediction_key = hashlib.sha256(key_source.encode()).hexdigest()
        now = self._now()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO visual_predictions(
                    prediction_key, file_hash, model_revision, predicted_type,
                    confidence, margin, sample_count, auto_eligible, auto_applied,
                    evidence_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    prediction_key, prediction.file_hash, prediction.model_revision,
                    prediction.predicted_type, prediction.confidence, prediction.margin,
                    prediction.sample_count, int(prediction.auto_eligible),
                    int(prediction.auto_applied), json.dumps(
                        {
                            "top_evidence": prediction.evidence,
                            "candidates": [
                                {
                                    "doc_type": candidate.doc_type,
                                    "confidence": candidate.confidence,
                                    "raw_score": candidate.raw_score,
                                    "sample_count": candidate.sample_count,
                                    "visual_votes": candidate.visual_votes,
                                    "evidence": candidate.evidence,
                                }
                                for candidate in prediction.candidates
                            ],
                            "blocked_reason": prediction.blocked_reason,
                        }
                    ), now,
                ),
            )
            row = connection.execute(
                "SELECT id FROM visual_predictions WHERE prediction_key=?",
                (prediction_key,),
            ).fetchone()
        return int(row["id"]) if row else None

    def record_feedback(self, prediction_id: int | None, final_type: str) -> None:
        if not prediction_id:
            return
        actual = normalize_doc_type(final_type)
        now = self._now()
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT file_hash, predicted_type FROM visual_predictions WHERE id=?",
                (prediction_id,),
            ).fetchone()
            if row is None:
                return
            predicted = row["predicted_type"] or ""
            correct = int(bool(predicted) and predicted == actual)
            connection.execute(
                """
                UPDATE visual_predictions SET final_type=?, correct=?, feedback_at=?
                WHERE id=?
                """,
                (actual, correct, now, prediction_id),
            )
            if predicted and not correct and actual in LEARNABLE_TYPES:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO visual_negative_feedback(
                        file_hash, rejected_type, actual_type, created_at
                    ) VALUES(?,?,?,?)
                    """,
                    (row["file_hash"], predicted, actual, now),
                )
        if predicted:
            if correct:
                self._log(
                    "success",
                    f"Visual feedback confirmed prediction={predicted} final={actual}",
                )
            else:
                self._log(
                    "warning",
                    f"Visual feedback corrected prediction={predicted} final={actual}; "
                    "similar automatic predictions are now blocked",
                )

    def class_statistics(self) -> list[dict[str, object]]:
        with self._connection() as connection:
            sample_rows = connection.execute(
                """
                SELECT doc_type,
                       SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) AS active_samples,
                       SUM(CASE WHEN enabled=0 THEN 1 ELSE 0 END) AS disabled_samples,
                       MAX(updated_at) AS last_updated
                FROM visual_document_samples GROUP BY doc_type
                """
            ).fetchall()
            prediction_rows = connection.execute(
                """
                SELECT predicted_type, COUNT(*) AS reviewed_predictions,
                       SUM(CASE WHEN correct=1 THEN 1 ELSE 0 END) AS correct_predictions
                FROM visual_predictions WHERE feedback_at IS NOT NULL
                GROUP BY predicted_type
                """
            ).fetchall()
            negative_rows = connection.execute(
                """
                SELECT rejected_type, COUNT(*) AS negative_count
                FROM visual_negative_feedback GROUP BY rejected_type
                """
            ).fetchall()
        predictions = {row["predicted_type"]: dict(row) for row in prediction_rows}
        negatives = {row["rejected_type"]: int(row["negative_count"]) for row in negative_rows}
        result = []
        for row in sample_rows:
            doc_type = row["doc_type"]
            active = int(row["active_samples"] or 0)
            reviewed = int(predictions.get(doc_type, {}).get("reviewed_predictions", 0))
            correct = int(predictions.get(doc_type, {}).get("correct_predictions", 0))
            required = 5 if doc_type in SENSITIVE_TYPES else 3
            result.append(
                {
                    "doc_type": doc_type,
                    "active_samples": active,
                    "disabled_samples": int(row["disabled_samples"] or 0),
                    "negative_count": negatives.get(doc_type, 0),
                    "reviewed_predictions": reviewed,
                    "correct_predictions": correct,
                    "accuracy": (correct / reviewed) if reviewed else None,
                    "auto_ready": active >= required,
                    "last_updated": row["last_updated"] or "",
                }
            )
        return sorted(result, key=lambda item: str(item["doc_type"]))

    def list_samples(self, doc_type: str | None = None) -> list[dict[str, object]]:
        query = """
            SELECT id, file_hash, doc_type, source_name, quality,
                   orientation_uncertain, enabled, confirmed_at, updated_at
            FROM visual_document_samples
        """
        params: tuple[object, ...] = ()
        if doc_type:
            query += " WHERE doc_type=?"
            params = (normalize_doc_type(doc_type),)
        query += " ORDER BY updated_at DESC, id DESC"
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def confusion_statistics(self) -> list[dict[str, object]]:
        """Return reviewed visual predictions grouped by predicted and final type."""
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT predicted_type, final_type, COUNT(*) AS decision_count,
                       SUM(CASE WHEN correct=1 THEN 1 ELSE 0 END) AS correct_count
                FROM visual_predictions
                WHERE feedback_at IS NOT NULL AND predicted_type <> ''
                GROUP BY predicted_type, final_type
                ORDER BY predicted_type, decision_count DESC, final_type
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def set_sample_enabled(self, sample_id: int, enabled: bool) -> bool:
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "UPDATE visual_document_samples SET enabled=?, updated_at=? WHERE id=?",
                (int(enabled), self._now(), int(sample_id)),
            )
            if cursor.rowcount:
                self._increment_revision(connection)
        return bool(cursor.rowcount)


if __name__ == "__main__":
    learner = VisualDocumentLearner()
    logger.info(
        f"Visual learner ready: revision={learner.model_revision()}, "
        f"auto_enabled={learner.auto_enabled()}"
    )
