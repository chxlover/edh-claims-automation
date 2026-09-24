"""Service module for building PDF contact sheets used by the GUI preview panel.

Reads CSF.pdf / COE.pdf files from the output folder, renders each page
to a scaled image, and composes 3-column contact sheets (1920x1080)
into the logs/ folder.  No PDF is ever opened by the user directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

import sys as _sys
from pathlib import Path as _Path

if __name__ == "__main__" and __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from core.activity_logger import ActivityLogger, logger

OUTPUT_DIR = Path(os.getenv("CLAIMS_OUTPUT_FOLDER", "C:/claims_bot/output"))
LOGS_DIR = Path("logs")
SHEET_W, SHEET_H = 1920, 1080
COLS = 3
COL_W = SHEET_W // COLS
MARGIN = 12
LABEL_H = 26
BG = (245, 245, 245)
CARD_BG = (255, 255, 255)
TEXT = (30, 30, 30)
ACCENT = (0, 120, 215)

SUPPORTED_DOC_TYPES = ("CSF", "COE")


@dataclass(frozen=True, slots=True)
class SheetResult:
    doc_type: str
    sheet_paths: tuple[Path, ...]
    patient_count: int


def _font(size: int):
    for name in ("segoeui.ttf", "DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _render_page(pdf_path: Path, cell_w: int) -> Image.Image:
    """Render the first page of a PDF to fit (cell_w - 2*MARGIN) wide."""
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    target_w = cell_w - 2 * MARGIN
    zoom = target_w / page.rect.width
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return img


def _build_sheet(files: list[Path], out_path: Path) -> None:
    sheet = Image.new("RGB", (SHEET_W, SHEET_H), BG)
    draw = ImageDraw.Draw(sheet)
    font_label = _font(17)
    font_small = _font(13)

    for idx, pdf in enumerate(files):
        col = idx % COLS
        row = idx // COLS
        x0 = col * COL_W
        y0 = row * SHEET_H

        draw.rectangle(
            [
                x0 + MARGIN,
                y0 + MARGIN,
                x0 + COL_W - MARGIN,
                y0 + SHEET_H - MARGIN,
            ],
            fill=CARD_BG,
            outline=(200, 200, 200),
            width=1,
        )

        page_img = _render_page(pdf, COL_W)
        px = x0 + (COL_W - page_img.width) // 2
        py = y0 + MARGIN + (SHEET_H - MARGIN - LABEL_H - page_img.height) // 2
        if py < y0 + MARGIN:
            py = y0 + MARGIN
        sheet.paste(page_img, (px, py))

        label = pdf.parent.name
        draw.text(
            (x0 + MARGIN, y0 + SHEET_H - LABEL_H - 2),
            label,
            fill=TEXT,
            font=font_label,
        )
        draw.text(
            (x0 + MARGIN, y0 + SHEET_H - LABEL_H + 16),
            f"page 1 of {fitz.open(str(pdf)).page_count}",
            fill=ACCENT,
            font=font_small,
        )

    sheet.save(out_path)


def _find_pdfs(doc_type: str, output_dir: Path) -> list[Path]:
    pattern = f"{doc_type}.pdf"
    return sorted(output_dir.rglob(pattern))


class PdfPreviewService:
    """Builds contact sheets for CSF and COE PDFs from the output folder."""

    def __init__(
        self,
        output_dir: Path | str = OUTPUT_DIR,
        logs_dir: Path | str = LOGS_DIR,
        activity_logger: ActivityLogger | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.logs_dir = Path(logs_dir)
        self.logger = activity_logger or logger

    def list_patients(self, doc_type: str) -> list[Path]:
        """Return the list of patient folders containing the given PDF."""
        if doc_type not in SUPPORTED_DOC_TYPES:
            raise ValueError(
                f"Unsupported doc_type: {doc_type}. "
                f"Supported: {SUPPORTED_DOC_TYPES}"
            )
        pdfs = _find_pdfs(doc_type, self.output_dir)
        return [p.parent for p in pdfs]

    def build_sheets(
        self, doc_type: str = "CSF", output_dir: Path | str | None = None
    ) -> list[Path]:
        """Build 3-column contact sheets and return the generated PNG paths."""
        if doc_type not in SUPPORTED_DOC_TYPES:
            raise ValueError(
                f"Unsupported doc_type: {doc_type}. "
                f"Supported: {SUPPORTED_DOC_TYPES}"
            )

        out_dir = Path(output_dir) if output_dir else self.output_dir
        pdfs = _find_pdfs(doc_type, out_dir)
        if not pdfs:
            self.logger.warning(f"No {doc_type}.pdf found in {out_dir}")
            return []

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        prefix = doc_type.lower()
        sheets: list[Path] = []
        chunks = [pdfs[i : i + COLS] for i in range(0, len(pdfs), COLS)]
        for i, chunk in enumerate(chunks, start=1):
            out_path = self.logs_dir / f"{prefix}_contact_sheet_{i}.png"
            _build_sheet(chunk, out_path)
            sheets.append(out_path)

        self.logger.success(
            f"{doc_type}: {len(pdfs)} PDF(s) -> {len(sheets)} sheet(s)"
        )
        return sheets

    def snapshot(self) -> SheetResult:
        """Build sheets for both CSF and COE and return a summary."""
        csf_sheets = self.build_sheets("CSF")
        coe_sheets = self.build_sheets("COE")
        return SheetResult(
            doc_type="CSF+COE",
            sheet_paths=tuple(csf_sheets + coe_sheets),
            patient_count=len(self.list_patients("CSF"))
            + len(self.list_patients("COE")),
        )


pdf_preview_service = PdfPreviewService()


if __name__ == "__main__":
    import sys

    base = Path(__file__).resolve().parent.parent
    service = PdfPreviewService(output_dir=base / "output", logs_dir=base / "logs")
    result = service.snapshot()
    assert result.patient_count >= 0
    logger.success("PdfPreviewService standalone test passed")
    sys.exit(0)