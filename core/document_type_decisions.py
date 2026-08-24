"""Manual document type decisions for deferred document review.

This module writes small local JSON rules used by the existing UNKNOWN trainer.
It does not OCR, move files, or touch HBSys.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.activity_logger import logger


BASE_DIR = Path(__file__).resolve().parent.parent
TRAINING_FILE = BASE_DIR / "unknown_training_data.json"

DOCUMENT_TYPES = (
    "SOA1",
    "SOA2_page1",
    "SOA2_page2",
    "DTR",
    "MRF_page1",
    "MRF_page2",
    "COE",
    "MMC",
    "CSF",
    "CF2_page1",
    "CF2_page2",
    "PBC_page1",
    "PBC_page2",
    "OPR",
    "ANR",
    "OTHER",
)


@dataclass(frozen=True, slots=True)
class DocumentTypeDecision:
    source_path: str
    doc_type: str
    decided_by: str
    note: str = ""


class DocumentTypeDecisionService:
    def __init__(self, training_file: str | Path = TRAINING_FILE) -> None:
        self.training_file = Path(training_file)

    def save_decision(self, decision: DocumentTypeDecision) -> None:
        doc_type = decision.doc_type.strip()
        if doc_type not in DOCUMENT_TYPES:
            raise ValueError(f"Unsupported document type: {doc_type}")
        if not decision.decided_by.strip():
            raise ValueError("decided_by is required")

        path = Path(decision.source_path)
        if not path.name:
            raise ValueError("source_path is required")

        data = self._load()
        data.append(
            {
                "doc_type": doc_type,
                "source_path": str(path.resolve()),
                "filename_contains": path.name.lower(),
                "keywords": [],
                "decided_by": decision.decided_by.strip(),
                "note": decision.note.strip(),
            }
        )
        self.training_file.parent.mkdir(parents=True, exist_ok=True)
        self.training_file.write_text(
            json.dumps(data, indent=4, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(
            f"Manual document type decision saved: {path.name} -> {doc_type}"
        )

    def _load(self) -> list[dict]:
        if not self.training_file.exists():
            return []
        try:
            data = json.loads(self.training_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []


document_type_decisions = DocumentTypeDecisionService()


if __name__ == "__main__":
    assert "MMC" in DOCUMENT_TYPES
    logger.success("Document Type Decision service standalone test passed")
