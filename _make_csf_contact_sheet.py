"""Build a 3-column contact sheet of all CSF.pdf files in output/.

Layout (1920x1080 per image, 3 columns = 640px each):
  Image 1: files 1-3
  Image 2: files 4-5 + 1 blank slot
Each PDF page is rendered to fit its 640x1080 cell (aspect preserved,
label drawn below the page).  No PDF is opened by hand by the user.
"""
from __future__ import annotations

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

OUTPUT = "output"
SHEET_W, SHEET_H = 1920, 1080
COLS = 3
COL_W = SHEET_W // COLS  # 640
CELL_H = SHEET_H
MARGIN = 12
LABEL_H = 26
BG = (245, 245, 245)
CARD_BG = (255, 255, 255)
TEXT = (30, 30, 30)
ACCENT = (0, 120, 215)


def _font(size: int):
    for name in ("segoeui.ttf", "DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def render_page(pdf_path: str, cell_w: int, cell_h: int) -> Image.Image:
    """Render the first page of a PDF to fit (cell_w - 2*MARGIN) wide."""
    doc = fitz.open(pdf_path)
    page = doc[0]
    target_w = cell_w - 2 * MARGIN
    # Scale so the page fits width; if taller than available height, scale down
    zoom = target_w / page.rect.width
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return img


def build_sheet(files: list[str], out_path: str) -> None:
    from pathlib import Path

    sheet = Image.new("RGB", (SHEET_W, SHEET_H), BG)
    draw = ImageDraw.Draw(sheet)
    font_label = _font(17)
    font_small = _font(13)

    for idx, pdf in enumerate(files):
        pdf_path = Path(pdf)
        col = idx % COLS
        row = idx // COLS
        x0 = col * COL_W
        y0 = row * CELL_H

        # Card background
        draw.rectangle(
            [x0 + MARGIN, y0 + MARGIN, x0 + COL_W - MARGIN, y0 + CELL_H - MARGIN],
            fill=CARD_BG,
            outline=(200, 200, 200), width=1,
        )

        page_img = render_page(pdf, COL_W, CELL_H)
        # Center the page inside the card, above the label
        px = x0 + (COL_W - page_img.width) // 2
        py = y0 + MARGIN + (CELL_H - MARGIN - LABEL_H - page_img.height) // 2
        if py < y0 + MARGIN:
            py = y0 + MARGIN
        sheet.paste(page_img, (px, py))

        # Label: patient folder name
        label = pdf_path.parent.name
        draw.text((x0 + MARGIN, y0 + CELL_H - LABEL_H - 2), label,
                  fill=TEXT, font=font_label)
        draw.text((x0 + MARGIN, y0 + CELL_H - LABEL_H + 16),
                  f"page 1 of {fitz.open(pdf).page_count}",
                  fill=ACCENT, font=font_small)

    sheet.save(out_path)
    print(f"saved {out_path} ({len(files)} files)")


def main() -> int:
    from pathlib import Path

    pdfs = sorted(Path(OUTPUT).rglob("CSF.pdf"))
    if not pdfs:
        print("no CSF.pdf found")
        return 1

    print(f"found {len(pdfs)} CSF.pdf files")
    # 3 columns per sheet
    sheets = [pdfs[i:i + COLS] for i in range(0, len(pdfs), COLS)]
    for i, chunk in enumerate(sheets, start=1):
        build_sheet(
            [str(p) for p in chunk],
            f"logs\\csf_contact_sheet_{i}.png",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())