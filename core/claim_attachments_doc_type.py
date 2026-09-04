"""Claim Attachments — Doc Type Detection.

Maps filename suffixes from the HBSys "Attachments" grid to doc type values
that get typed into the per-row Doc Type combo box.

Mapping (owner spec, 2026-09-02):

    PDF rows (backslash-separated):
        \\COE.pdf -> COE      \\CSF.pdf -> CSF      \\DTR.pdf -> DTR
        \\SOA1.pdf -> SOA     \\SOA2.pdf -> SOA     \\MRF.pdf -> MRF
        \\PBC.pdf -> PBC      \\MMC.pdf -> MMC       \\OPR.pdf -> OPR
        \\ANR.pdf -> ANR      \\CF3.pdf -> CF3       \\CF2.pdf -> CF2

    XML rows (underscore-separated):
        _CF4.xml -> CF4       _CF5.xml -> CF5        _eSOA.xml -> ESA

OCR tolerance: the same normalisation is applied to BOTH the OCR text and
the known suffixes, so look-alike confusions (O/0, S/5, I/L/1, B/8, Z/2)
cancel out. ".xmi" is accepted as a common OCR misread of ".xml".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# Suffix stem -> HBSys doc type value
PDF_SUFFIXES = {
    "COE": "COE", "CSF": "CSF", "DTR": "DTR",
    "SOA1": "SOA", "SOA2": "SOA",
    "MRF": "MRF", "PBC": "PBC", "MMC": "MMC",
    "OPR": "OPR", "ANR": "ANR", "CF3": "CF3", "CF2": "CF2",
}
XML_SUFFIXES = {
    "CF4": "CF4", "CF5": "CF5", "ESOA": "ESA",
}

# Filename patterns as they appear in the grid rows.
# PDF:  "...ADM20260817_DIS20260822\\CSF.pdf"  (OCR may read "SOA1" as "SOAL")
# XML:  "NAME-CLAIMNO_CF4.xml"  (OCR often reads ".xml" as ".xmi")
_PDF_RE = re.compile(r"\\([A-Za-z0-9]{2,4})\.p", re.IGNORECASE)
_XML_RE = re.compile(r"_([A-Za-z0-9]{2,4})\.x", re.IGNORECASE)

# Map look-alike letters to digits. Applied to both the OCR text and the
# known suffix stems so the confusion pairs cancel out either way:
# "ESOA"->"E50A" and OCR "e50A"->"E50A" both normalise to the same value.
_LETTER_TO_DIGIT = str.maketrans("OSILBZ", "051182")


def _norm(text: str) -> str:
    """Uppercase and map look-alike letters to digits."""
    return text.upper().translate(_LETTER_TO_DIGIT)


# Pre-normalised lookup tables (normalised suffix -> doc type)
_PDF_LOOKUP = {_norm(stem): doc for stem, doc in PDF_SUFFIXES.items()}
_XML_LOOKUP = {_norm(stem): doc for stem, doc in XML_SUFFIXES.items()}

# All stems, longest first, for joined-text fallback matching
_ALL_STEMS_NORM: list[tuple[str, str]] = sorted(
    {_norm(stem): doc for stem, doc in {**PDF_SUFFIXES, **XML_SUFFIXES}.items()}.items(),
    key=lambda kv: -len(kv[0]),
)


def detect_doc_type(word: str) -> Optional[str]:
    """Return the HBSys doc type for a grid filename word, or None.

    Accepts forms such as:
        "ADM20260817_D1S20260822\\CSF.pdf"     -> CSF
        "ADM20260817_D1S20260822\\SOAL.pdfF"    -> SOA   (SOA1 with OCR noise)
        "GUZMAN-260901105854_CF4.xml"          -> CF4
        "GUZMAN-260901105854_e50A.xmi"         -> ESA   (eSOA with OCR noise)
    """
    match = _PDF_RE.search(word)
    if match:
        return _PDF_LOOKUP.get(_norm(match.group(1)))
    match = _XML_RE.search(word)
    if match:
        return _XML_LOOKUP.get(_norm(match.group(1)))
    return None


def detect_doc_type_from_words(words: list[str]) -> Optional[str]:
    """Detect doc type from ALL OCR words of ONE grid row band.

    Strategy:
        1. Per-word regex match (fast path — handles whole tokens like
           "ADM20260817_DIS20260822\\CSF.pdf" or "NAME-CLAIM_CF4.xml").
        2. Joined fallback: concatenate every word (spaces removed, OCR
           normalised) and search for a known stem — handles OCR-split
           tokens such as "SOA1" separated from ".pdf", or "D" + "TR.pdf".
    """
    for word in words:
        doc = detect_doc_type(word)
        if doc is not None:
            return doc
    if words:
        joined = _norm("".join(words))
        for stem_norm, doc in _ALL_STEMS_NORM:
            if stem_norm in joined:
                return doc
    return None


def doc_types_from_folder_files(filenames: list[str]) -> list[str]:
    """Build the expected doc-type sequence from the patient folder listing.

    This is the deterministic source of truth: the grid rows are created
    by attaching these files, so one doc type is expected per file.
    Non-PDF/XML files and XMLs that carry no known suffix are skipped
    (they cannot be classified and the caller must handle the mismatch).
    """
    docs: list[str] = []
    for name in filenames:
        if name.lower().endswith(".pdf"):
            doc = detect_doc_type("\\" + name)
        elif name.lower().endswith(".xml"):
            doc = detect_doc_type("_" + name)
        else:
            doc = None
        if doc is not None:
            docs.append(doc)
    return docs


def _file_stem(name: str) -> str:
    """Extract the doc-type stem from a patient folder filename.

        "COE.pdf"                          -> "COE"
        "NAME-CLAIMNO_CF4.xml"            -> "CF4"
        "NAME-CLAIMNO_eSOA.xml"           -> "eSOA"
    """
    base = name.rsplit(".", 1)[0]
    if "_" in base:
        base = base.rsplit("_", 1)[-1]
    return base


def match_row_to_file(
    row_words: list[str], unmatched_files: list[str]
) -> Optional[str]:
    """Match ONE grid row band to ONE unmatched folder file by doc type.

    For every unmatched file, compute its doc type; then check whether the
    tail of the row's OCR text (last 24 chars, normalised) contains that
    file's stem. Pure-letter stems of 3+ chars (DTR, MRF, ...) also allow
    subsequence matching to handle OCR-split tokens ("\\D" + "TR.pdf").
    Stems containing digits (SOA1, CF4, ...) require an exact substring
    match to avoid false positives on digit-heavy path text.

    Returns the doc type of the single matching file, or:
        None  -> no file matched this row (caller decides: empty row or error)
        "?"   -> multiple files matched (ambiguous — caller must abort)
    """
    joined = _norm("".join(row_words))
    tail = joined[-24:] if len(joined) > 24 else joined
    matches: list[str] = []
    for name in unmatched_files:
        if name.lower().endswith(".pdf"):
            doc = detect_doc_type("\\" + name)
        elif name.lower().endswith(".xml"):
            doc = detect_doc_type("_" + name)
        else:
            continue
        if doc is None:
            continue
        stem_norm = _norm(_file_stem(name))
        has_digit = any(ch.isdigit() for ch in stem_norm)
        if stem_norm in tail:
            matches.append(doc)
        elif not has_digit and len(stem_norm) >= 3 and _is_subsequence(stem_norm, tail):
            matches.append(doc)
    if not matches:
        return None
    if len(set(matches)) == 1:
        return matches[0]
    return "?"


def _is_subsequence(needle: str, haystack: str) -> bool:
    """True if all chars of needle appear in haystack in order."""
    it = iter(haystack)
    return all(ch in it for ch in needle)


# -- grid line extraction & folder-driven line matching (v4) -----------------
#
# The 2026-09-03 14:51 live run proved the pixel-separator band approach
# fragile: the grid's "File Name" column shows the FULL LOCAL PATH, which
# wraps to 2-3 text lines inside ONE row, so separator bands can split a
# row's text across bands (a band holding only "C:\claims_bot\...READY\
# PASCUA, VIOLETA " has no filename and gets skipped -> its file never
# matches -> ABORT). Also the popup's currently-selected row is drawn
# white-on-blue, which a single normal OCR pass can miss entirely.
#
# v4 replaces band matching with TEXT-LINE matching:
#   - words are grouped by tesseract's own (block, par, line) ids, which
#     are stable across wrapped rows;
#   - two OCR passes (normal + colour-inverted) cover the blue row;
#   - each folder file must match exactly ONE line (strict stem+extension,
#     loose stem fallback) and lines are consumed 1:1, so duplicate rows
#     or twin stems (SOA1/SOA2) can never share a row;
#   - anything unresolved is ABORT — never guessed.

@dataclass
class GridLine:
    """One OCR text line from the attachments popup (screen coords)."""

    words: list[str]
    left: int
    top: int
    right: int
    bottom: int
    norm_text: str = ""

    def __post_init__(self) -> None:
        if not self.norm_text:
            self.norm_text = _norm("".join(self.words))

    @property
    def center_y(self) -> int:
        """Vertical center — the y used to click this row's Doc Type cell."""
        return (self.top + self.bottom) // 2


def _word_min_conf(word: str) -> int:
    """Confidence threshold for one OCR word.

    Extension-bearing words are critical for file matching, so they get a
    lower threshold (the folder-driven matcher guards against false
    positives anyway). Mirrors the threshold proven in the 2026-09-03
    live tests.
    """
    low = word.lower()
    if ".pdf" in low or ".xmi" in low or ".xml" in low:
        return 10
    return 20


def extract_grid_lines(
    ocr_data: dict, offset_x: int = 0, offset_y: int = 0
) -> list[GridLine]:
    """Group pytesseract image_to_data words into text lines.

    Words are grouped by tesseract's (block, paragraph, line) ids — this
    survives wrapped path rows where pixel-separator bands fail. Words
    below their confidence threshold are dropped. Coordinates are shifted
    by (offset_x, offset_y) to convert crop-relative positions to screen
    coordinates.
    """
    groups: dict[
        tuple[int, int, int], list[tuple[int, int, int, int, str]]
    ] = {}
    for i in range(len(ocr_data["text"])):
        text = ocr_data["text"][i].strip()
        if not text:
            continue
        raw_conf = ocr_data["conf"][i]
        conf = int(raw_conf) if str(raw_conf).isdigit() else -1
        if conf <= _word_min_conf(text):
            continue
        key = (
            ocr_data["block_num"][i],
            ocr_data["par_num"][i],
            ocr_data["line_num"][i],
        )
        groups.setdefault(key, []).append(
            (
                ocr_data["left"][i] + offset_x,
                ocr_data["top"][i] + offset_y,
                ocr_data["width"][i],
                ocr_data["height"][i],
                text,
            )
        )
    lines: list[GridLine] = []
    for words in groups.values():
        words.sort(key=lambda w: w[0])
        lines.append(
            GridLine(
                words=[w[4] for w in words],
                left=min(w[0] for w in words),
                top=min(w[1] for w in words),
                right=max(w[0] + w[2] for w in words),
                bottom=max(w[1] + w[3] for w in words),
            )
        )
    lines.sort(key=lambda ln: (ln.top, ln.left))
    return lines


def _line_quality(line: GridLine) -> tuple[bool, bool, int]:
    """Ranking key for choosing between two OCR readings of one line.

    The matcher needs the file stem (strict/loose tier) and, ideally, the
    extension marker (strict tier). A reading that contains a known stem
    beats one that does not; then an extension marker beats none; then the
    longer alphanumeric reading wins (more complete).
    """
    text = line.norm_text
    has_stem = any(stem in text for stem, _doc in _ALL_STEMS_NORM)
    has_ext = ".P" in text or ".X" in text
    alnum = sum(ch.isalnum() for ch in text)
    return (has_stem, has_ext, alnum)


def _has_stem(line: GridLine) -> bool:
    """True when the line's normalised text contains a known file stem."""
    return any(stem in line.norm_text for stem, _doc in _ALL_STEMS_NORM)


def merge_dual_pass_lines(
    normal_lines: list[GridLine], inverted_lines: list[GridLine]
) -> list[GridLine]:
    """Merge the two OCR passes; the better-quality reading wins on overlap.

    The popup's selected row is drawn white-on-blue: the normal pass often
    returns a partial/garbled line for it while the inverted pass reads it
    fully. The old rule (discard ANY overlapping inverted line) threw away
    the good inverted reading whenever the normal pass produced even a
    fragment — so COE.pdf (always the auto-selected first row) went
    "not found" in the 2026-09-03 15:39/15:40 live runs.

    Rule per incoming line:
        - no overlap            -> append (a row only this pass saw)
        - exactly one overlap   -> keep the higher _line_quality reading
        - several overlaps      -> stem-gain (v4.3): when the incoming
          reading carries a known file stem and NONE of the overlapping
          lines does, the stem-less fragments are garbage for matching
          purposes — replace them all with the incoming line. Otherwise
          keep the normal readings (conservative: the incoming pass
          merged rows, and dropping normal rows could lose a stem).
    """
    lines = list(normal_lines)
    for inv_line in inverted_lines:
        overlap_idx = [
            i for i, ln in enumerate(lines)
            if not (inv_line.bottom < ln.top or inv_line.top > ln.bottom)
            and not (inv_line.right < ln.left or inv_line.left > ln.right)
        ]
        if not overlap_idx:
            lines.append(inv_line)
        elif len(overlap_idx) == 1:
            i = overlap_idx[0]
            if _line_quality(inv_line) > _line_quality(lines[i]):
                lines[i] = inv_line
        else:
            # v4.3 stem-gain: live-proven in the 2026-09-04 MATTERIG run —
            # the isolated band reading '...\COE.pdf' overlapped TWO garbage
            # fragments ('5E0EEE' + '1CARRE0N-000...') and the conservative
            # rule discarded the ONLY good reading. A stem-less line can
            # never match a file, so replacing stem-less fragments loses
            # nothing; the stem is pure information gain.
            if _has_stem(inv_line) and not any(
                _has_stem(lines[i]) for i in overlap_idx
            ):
                for i in sorted(overlap_idx, reverse=True):
                    del lines[i]
                lines.append(inv_line)
    lines.sort(key=lambda ln: (ln.top, ln.left))
    return lines


def find_highlight_band(image: "Image.Image") -> Optional[tuple[int, int]]:
    """Y-range of the blue selection highlight row in *image*, or None.

    The attachments popup auto-selects a row and draws it as white text
    on a solid blue band. The band is located by counting blue-dominant
    pixels per pixel row (blue clearly above both red and green) and
    taking the largest run of rows that are mostly blue. Returns None
    when no such band exists (nothing selected).
    """
    width, height = image.size
    pixels = image.load()
    is_blue_row: list[bool] = []
    for y in range(height):
        blue = 0
        sampled = 0
        for x in range(10, width - 10, 4):
            r, g, b = pixels[x, y][:3]
            sampled += 1
            if b > 120 and (b - max(r, g)) > 40:
                blue += 1
        is_blue_row.append(sampled > 0 and blue / sampled >= 0.5)
    best: Optional[tuple[int, int]] = None
    start: Optional[int] = None
    for y, flag in enumerate(is_blue_row + [False]):
        if flag and start is None:
            start = y
        elif not flag and start is not None:
            if best is None or (y - start) > (best[1] - best[0]):
                best = (start, y)
            start = None
    return best


def ocr_highlight_band(
    image: "Image.Image",
    band: tuple[int, int],
    offset_x: int = 0,
    offset_y: int = 0,
) -> list[GridLine]:
    """OCR the selected row band via manual binarization.

    White-on-blue text defeats tesseract's auto-threshold on the full
    crop: in the 2026-09-03 15:39/15:40 live runs BOTH passes returned
    only garbage for the selected row ('5NEEE') while every normal row
    was read fine. Inside the band the white pixels ARE the text, so a
    fixed threshold (luminance > 170 -> text) yields clean black-on-
    white glyphs that OCR reads reliably (validated on the real debug
    crops: '\\C0E.NDF' with the COE stem present).
    """
    import pytesseract

    top, bottom = band
    band_img = image.crop((0, top, image.width, bottom))
    black_white = band_img.convert("L").point(lambda v: 0 if v > 170 else 255)
    data = pytesseract.image_to_data(
        black_white, config="--psm 6", output_type=pytesseract.Output.DICT
    )
    return extract_grid_lines(data, offset_x, offset_y + top)


def find_row_separator_lines(
    image: "Image.Image", min_separators: int = 3
) -> list[int]:
    """Y positions of the light-gray full-width grid separator lines.

    Each grid row is delimited by a light-gray horizontal line (~80% of
    the row's pixels in 225..246 luminance, low saturation). Rows that
    pass the threshold are clustered (adjacent lines within 3px merged).
    Returns [] when fewer than *min_separators* lines exist (not a grid).
    """
    width, height = image.size
    pixels = image.load()
    x_start = max(10, int(width * 0.2))
    x_end = max(x_start + 10, width - 40)
    step = 3
    separators: list[int] = []
    for y in range(max(1, int(height * 0.12)), height - 2):
        gray = 0
        total = 0
        for x in range(x_start, x_end, step):
            r, g, b = pixels[x, y][:3]
            avg = (r + g + b) // 3
            total += 1
            if 225 <= avg < 247 and (max(r, g, b) - min(r, g, b)) < 20:
                gray += 1
        if total > 0 and gray / total >= 0.75:
            if not separators or y - separators[-1] > 3:
                separators.append(y)
    return separators if len(separators) >= min_separators else []


def ocr_row_bands(
    image: "Image.Image",
    bands: list[tuple[int, int]],
    offset_x: int = 0,
    offset_y: int = 0,
) -> list[GridLine]:
    """OCR each grid row band separately (PSM 6, plain) and return lines.

    Why: on the 2026-09-03 17:20 live crop (SASPA), the full-crop passes
    missed CSF.pdf and MRF.pdf entirely and lost the .xml suffixes of the
    CF4/CF5 rows, while per-band PSM 6 crops read EVERY row perfectly —
    wrapped paths included. Isolating each band removes the surrounding
    rows that confuse tesseract's line segmentation.
    """
    import pytesseract

    lines: list[GridLine] = []
    for top, bottom in bands:
        if bottom - top < 18:
            continue  # sliver between wrapped text lines, not a row band
        band_img = image.crop((0, top, image.width, bottom))
        try:
            data = pytesseract.image_to_data(
                band_img, config="--psm 6", output_type=pytesseract.Output.DICT
            )
        except Exception:
            continue
        lines.extend(
            extract_grid_lines(data, offset_x, offset_y + top)
        )
    return lines


def ocr_grid_lines(
    image: "Image.Image", offset_x: int = 0, offset_y: int = 0
) -> list[GridLine]:
    """OCR *image* and return text lines, readable on dark rows too.

    Four passes, merged by quality (see `merge_dual_pass_lines`):

        1. normal pass (PSM 6) — reads every white-background row;
        2. colour-inverted pass — covers rows the normal pass misses;
        3. targeted band pass — when a blue selection band exists, the
           band is binarized manually and OCR'd separately, because both
           full-crop passes can return garbage for the white-on-blue
           selected row;
        4. per-row-band pass (v4.2) — the grid's light-gray separator
           lines split the image into row bands; each band is OCR'd in
           isolation (PSM 6). On the 2026-09-03 17:20 SASPA live crop
           this pass read rows (CSF, MRF, and the wrapped _CF4.xml/_CF5.xml
           rows) that ALL other passes missed.

    Uses PSM 6 (uniform text block), which clearly beat the default
    PSM 3 on the real popup grid (8/8 vs 7/8 files matched in the
    2026-09-03 offline validation).
    """
    import pytesseract
    from PIL import ImageOps

    lines = extract_grid_lines(
        pytesseract.image_to_data(
            image, config="--psm 6", output_type=pytesseract.Output.DICT
        ),
        offset_x,
        offset_y,
    )
    try:
        inverted = extract_grid_lines(
            pytesseract.image_to_data(
                ImageOps.invert(image),
                config="--psm 6",
                output_type=pytesseract.Output.DICT,
            ),
            offset_x,
            offset_y,
        )
    except Exception:
        inverted = []
    merged = merge_dual_pass_lines(lines, inverted)
    band = find_highlight_band(image)
    if band is not None:
        # v4.3: the full-crop passes only ever return garbage fragments for
        # the white-on-blue selected row (live-proven: '5NEEE',
        # '1CARRE0N-000000000015817\ADM20260821,'). When the isolated band
        # pass later produces a GOOD multi-line reading, the merge rule
        # sees a multi-overlap and conservatively discards it — exactly
        # what dropped the COE reading in the 2026-09-04 MATTERIG live run.
        # Fix: drop stem-less lines FULLY INSIDE the band BEFORE the band
        # pass, so its readings merge without competition. Lines that
        # contain a known stem are always kept (safety against a false
        # band detection).
        b_top, b_bottom = band
        merged = [
            ln for ln in merged
            if not (b_top <= ln.top and ln.bottom <= b_bottom)
            or any(stem in ln.norm_text for stem, _doc in _ALL_STEMS_NORM)
        ]
        try:
            band_lines = ocr_highlight_band(image, band, offset_x, offset_y)
        except Exception:
            band_lines = []
        merged = merge_dual_pass_lines(merged, band_lines)
    # v4.2: per-row-band pass on the separator-delimited row bands
    separators = find_row_separator_lines(image)
    if separators:
        bands = [
            (separators[i], separators[i + 1])
            for i in range(len(separators) - 1)
            if separators[i + 1] - separators[i] >= 18
        ]
        if bands:
            try:
                band_lines = ocr_row_bands(image, bands, offset_x, offset_y)
            except Exception:
                band_lines = []
            merged = merge_dual_pass_lines(merged, band_lines)
    return merged


def _file_ext_letter(name: str) -> str:
    """Extension letter used in the strict match tier (P=.pdf, X=.xml)."""
    return "P" if name.lower().endswith(".pdf") else "X"


def _edit_distance_le1(a: str, b: str) -> bool:
    """True when the edit distance between *a* and *b* is at most 1.

    Covers the OCR stem mutations seen in live runs (a dropped character
    such as 'C4' for 'CF4'). Lengths differing by more than 1 fail fast;
    equal lengths require at most one substituted character.
    """
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b) if x != y) <= 1
    # length differs by exactly 1: single insertion or deletion
    if len(a) > len(b):
        a, b = b, a
    # a is shorter; try deleting each char of b in turn
    for i in range(len(b)):
        if b[:i] + b[i + 1:] == a:
            return True
    return False


def match_files_to_lines(
    files: list[str], lines: list[GridLine]
) -> tuple[list[tuple[str, GridLine]], list[str], list[tuple[str, int]]]:
    """Folder-driven matching: pair every folder file with ONE grid line.

    For each file, candidate lines are found in the line's normalised
    text, in three tiers (strongest first):
        strict tier — normalised stem + "." + extension letter
                      ("C0E.P", "CF4.X" — tolerates ".PDFF"/".XM1" noise)
        loose tier  — normalised stem anywhere ("50A1" in "50A1PDF")
        mutated tier (v4.3) — the stem lost/gained ONE character to OCR
                      ("C4.X" for CF4, "S0A1" variants...) AND the line
                      still carries the right extension letter. Covers
                      the 2026-09-04 MATTERIG live case where tesseract
                      read '_CF4.xml' as 'C4.XM1'. Edit distance is
                      computed on a short window before the extension
                      marker; digit-vs-letter confusions (4/CF4) do NOT
                      match, so SOA1/SOA2 twins and CF4/CF5 can never
                      cross-match through this tier.

    The stronger tier wins whenever it has candidates. Lines are consumed
    1:1 via a fixpoint pass (one line per file, one file per line), so
    duplicate grid rows or twin stems (SOA1/SOA2) can never both grab the
    same row.

    Returns (matched, not_found, ambiguous):
        matched   — (file, line) pairs, one per resolved file
        not_found — files with no free candidate line left
        ambiguous — (file, free candidate count) for contested files
    """

    def _mutated_stem_candidates(
        stem_norm: str, ext: str, lines_list: list[GridLine]
    ) -> list[GridLine]:
        """Lines whose text contains a 1-edit mutation of *stem_norm*
        directly followed by the '.' + extension marker."""
        results: list[GridLine] = []
        for ln in lines_list:
            text = ln.norm_text
            # find the extension marker occurrences
            marker = "." + ext
            pos = text.find(marker)
            while pos != -1:
                window_start = max(0, pos - (len(stem_norm) + 2))
                window = text[window_start:pos]
                # try all suffixes of the window as candidate stems
                for cut in range(0, min(2, len(window)) + 1):
                    cand = window[: len(window) - cut] if cut else window
                    if not cand:
                        continue
                    if _edit_distance_le1(cand, stem_norm):
                        results.append(ln)
                        break
                pos = text.find(marker, pos + 1)
        return results

    candidates: dict[str, list[list[GridLine]]] = {}
    for name in files:
        stem_norm = _norm(_file_stem(name))
        ext = _file_ext_letter(name)
        strict = [ln for ln in lines if stem_norm + "." + ext in ln.norm_text]
        loose = [ln for ln in lines if stem_norm in ln.norm_text]
        mutated = _mutated_stem_candidates(stem_norm, ext, lines)
        candidates[name] = [strict, loose, mutated]

    consumed: set[int] = set()
    matched: dict[str, GridLine] = {}
    changed = True
    while changed:
        changed = False
        for name in files:
            if name in matched:
                continue
            tiers = candidates[name]
            tier = next((t for t in tiers if t), [])
            free = [ln for ln in tier if id(ln) not in consumed]
            if len(free) == 1:
                matched[name] = free[0]
                consumed.add(id(free[0]))
                changed = True

    not_found: list[str] = []
    ambiguous: list[tuple[str, int]] = []
    for name in files:
        if name in matched:
            continue
        tiers = candidates[name]
        tier = next((t for t in tiers if t), [])
        free = [ln for ln in tier if id(ln) not in consumed]
        if len(free) > 1:
            ambiguous.append((name, len(free)))
        else:
            not_found.append(name)

    ordered = [(name, matched[name]) for name in files if name in matched]
    return ordered, not_found, ambiguous


# -- standalone test -------------------------------------------------------

if __name__ == "__main__":
    cases = [
        # PDF rows (from OCR recon of SS_choose_doc_type.png)
        ("ADM20260817_D1S20260822\\CSF.pdf", "CSF"),
        ("ADM20260817_D1S20260822\\DTR.pdf", "DTR"),
        ("ADM20260817_D1S20260822\\SOAL.pdfF", "SOA"),
        ("ADM20260817_DIS20260822\\SOA2.pdf", "SOA"),
        ("\\COE.pdf", "COE"),
        ("\\MRF.pdf", "MRF"),
        ("\\PBC.pdf", "PBC"),
        ("\\MMC.pdf", "MMC"),
        ("\\OPR.pdf", "OPR"),
        ("\\ANR.pdf", "ANR"),
        ("\\CF3.pdf", "CF3"),
        ("\\CF2.pdf", "CF2"),
        # XML rows
        ("GUZMAN-260901105854_CF4.xml", "CF4"),
        ("GUZMAN-260901105854_CF5.xmi", "CF5"),
        ("GUZMAN-260901105854_e50A.xmi", "ESA"),
        ("_eSOA.XML", "ESA"),
        # Non-matching words (paths, names)
        ("C:\\claims_bot\\claims_checker_results\\READY\\ECHANES,", None),
        ("PAUL GEORGE DE GUZMAN", None),
        ("000000000020743", None),
    ]
    failures = 0
    for text, expected in cases:
        got = detect_doc_type(text)
        ok = got == expected
        if not ok:
            failures += 1
        print(f"{'OK  ' if ok else 'FAIL'} {text!r} -> {got!r} (expected {expected!r})")
    # Row-word lists observed in the 2026-09-03 live test (OCR-split rows).
    # NOTE: heavily split rows like '\\D' + 'TR.pdf' are NOT solvable from
    # text alone — they are handled by match_row_to_file (folder-driven).
    word_row_cases = [
        # CANTA live row: 'SOA1' separated from '.pdf'
        (["GIANN HERMOSO pdf - ADM20260824_D1S20260827\\SOA1", "000000000021009"], "SOA"),
        # XML row split into name stem + extension fragment
        (["GUZMAN-260901105854_CF4", "xmi"], "CF4"),
        (["GUZMAN-260901105854_e50A", "xmi"], "ESA"),
        # path fragments only (must NOT match)
        (["o", "C:\\daims_bot\\claims_checker_results\\READY\\CANTA,", "GIANN HERMOSO - ADM20260824_01520260827\\CANTA,"], None),
        # header junk (must NOT match)
        (["Doc", "File", "Document", "PHIC", "Type", "Name"], None),
        (["Cloud", "Storage", "URL", "Transmitt", "en"], None),
    ]
    for words, expected in word_row_cases:
        got = detect_doc_type_from_words(words)
        ok = got == expected
        if not ok:
            failures += 1
        print(f"{'OK  ' if ok else 'FAIL'} row-words {words} -> {got!r} (expected {expected!r})")
    # Row-to-file matching (folder-driven, from 2026-09-03 live test)
    folder_files = [
        "COE.pdf", "CSF.pdf", "DTR.pdf", "SOA1.pdf", "SOA2.pdf",
        "SAFLOR, ORLANDO EUGENIO-260901108515_CF4.xml",
        "SAFLOR, ORLANDO EUGENIO-260901108515_CF5.xml",
        "SAFLOR, ORLANDO EUGENIO-260901108515_eSOA.xml",
    ]
    match_cases = [
        # Live SAFLOR row: OCR split '\\D' + 'TR.pdf' — matches DTR.pdf
        (["- ADM20260815_01520260819\\D", "ORLANDO EUGENIO TR.pdf"], "DTR"),
        # Live CANTA row: 'SOA1' without '.pdf' tail visible
        (["GIANN HERMOSO pdf - ADM20260824_D1S20260827\\SOA1", "000000000021009"], "SOA"),
        # Normal row: whole token present
        (["- ADM20260815_D1S20260819\\COE.pdf"], "COE"),
        # XML row split into stem + extension fragment
        (["SAFLOR, ORLANDO EUGENIO-260901108515_CF4", "xmi"], "CF4"),
        (["SAFLOR, ORLANDO EUGENIO-260901108515_e50A", "xmi"], "ESA"),
        # Path-only row: no file matches
        (["o", "C:\\daims_bot\\claims_checker_results\\READY\\SAFLOR,"], None),
        # Header junk: no match
        (["Doc", "File", "Document", "PHIC", "Type", "Name"], None),
    ]
    for words, expected in match_cases:
        got = match_row_to_file(words, folder_files)
        ok = got == expected
        if not ok:
            failures += 1
        print(f"{'OK  ' if ok else 'FAIL'} match_row {words[:2]}... -> {got!r} (expected {expected!r})")
    # Folder sequence helper
    seq = doc_types_from_folder_files(folder_files)
    expected_seq = ["COE", "CSF", "DTR", "SOA", "SOA", "CF4", "CF5", "ESA"]
    ok = seq == expected_seq
    if not ok:
        failures += 1
    print(f"{'OK  ' if ok else 'FAIL'} folder sequence {seq} (expected {expected_seq})")

    # -- v4: line extraction + folder-driven LINE matching -----------------
    def _ocr_dict(lines_words: list[list[tuple[str, int, int, int, int]]]) -> dict:
        """Build a pytesseract-style DICT from lines of words.

        Each inner list is ONE tesseract line (same block/par/line ids).
        Word tuple: (text, left, top, width, height); confidence fixed at
        90; coordinates crop-relative.
        """
        out: dict = {
            key: []
            for key in (
                "text", "conf", "left", "top", "width", "height",
                "block_num", "par_num", "line_num",
            )
        }
        for block, words in enumerate(lines_words, start=1):
            for text, left, top, width, height in words:
                out["text"].append(text)
                out["conf"].append(90)
                out["left"].append(left)
                out["top"].append(top)
                out["width"].append(width)
                out["height"].append(height)
                out["block_num"].append(block)
                out["par_num"].append(1)
                out["line_num"].append(1)
        return out

    head = "‘C:\\C1A1M5_80T\\C1A1M5_CHECKER_RE5U1T5\\READY\\SAF10R,0R1AND0EUGEN10"
    hosp = "SAF10R-000000000020680-ADM20260815_D1520260819"
    synth = [
        # popup title + grid header junk (must NOT match)
        [("‘ATTACHMENT5F0RSAF10R,0R1AND0EUGEN10-260901108515", 470, 330, 380, 16)],
        [("D0CF111ED0CUMENTPH1C", 484, 357, 300, 14)],
        # COE row (selected/blue): head of path unreadable, tail readable
        [("--000000000020680-ADM20260815_D1520260819\\C0E.PDF", 526, 399, 400, 14)],
        # CSF row (wrapped path: stem on the 2nd line)
        [(head, 526, 416, 400, 14)],
        [(hosp + "\\C5F.PDF", 526, 430, 395, 14)],
        # DTR row: OCR split '\\D' + 'TR.PDF' WITHIN one line (live case)
        [(head, 526, 446, 400, 14)],
        [(hosp + "\\D", 526, 460, 200, 14), ("TR.PDF", 730, 460, 60, 14)],
        # SOA1 row: '.PDFF' OCR noise (strict tier must still hit)
        [(head, 526, 476, 400, 14)],
        [(hosp + "\\50A1.PDFF", 526, 490, 395, 14)],
        # SOA2 row: extension dot lost by OCR (loose tier fallback)
        [(head, 526, 506, 400, 14)],
        [(hosp + "\\50A2PDF", 526, 520, 390, 14)],
        # CF4 row: 3-line wrap, stem on the LAST line
        [(head, 526, 536, 400, 14)],
        [(hosp + "\\SAF10R,", 526, 550, 395, 14)],
        [("0R1AND0EUGEN10-260901108515_CF4.XM1", 526, 564, 300, 14)],
        # CF5 row
        [(head, 526, 580, 400, 14)],
        [("0R1AND0EUGEN10-260901108515_CF5.XM1", 526, 608, 300, 14)],
        # eSOA row ('.XM1' misread)
        [(head, 526, 624, 400, 14)],
        [("0R1AND0EUGEN10-260901108515_E50A.XM1", 526, 652, 300, 14)],
        # note + buttons junk (must NOT match)
        [("N0TE:0RG1NA11E5W118EDE1ETEDANDM0VEDT0F1E5ERVER", 470, 708, 250, 14)],
        [("ATTACH...UP10ADDE1ETE", 490, 733, 230, 14)],
    ]
    grid_lines = extract_grid_lines(_ocr_dict(synth))
    ok = len(grid_lines) == len(synth) and any(
        "DTR.PDF" in ln.norm_text for ln in grid_lines
    )
    if not ok:
        failures += 1
    print(
        f"{'OK  ' if ok else 'FAIL'} extract_grid_lines: {len(grid_lines)}/"
        f"{len(synth)} lines, split-token join works: "
        f"{any('DTR.PDF' in ln.norm_text for ln in grid_lines)}"
    )
    matched, nf, amb = match_files_to_lines(folder_files, grid_lines)
    v4_docs = [
        detect_doc_type("\\" + f if f.lower().endswith(".pdf") else "_" + f)
        for f, _ in matched
    ]
    v4_ys = [ln.center_y for _, ln in matched]
    ok = (
        not nf and not amb and len(matched) == 8
        and v4_docs == ["COE", "CSF", "DTR", "SOA", "SOA", "CF4", "CF5", "ESA"]
        and v4_ys == sorted(v4_ys)
    )
    if not ok:
        failures += 1
    print(
        f"{'OK  ' if ok else 'FAIL'} match_files_to_lines synthetic: "
        f"{len(matched)}/8, docs={v4_docs}, ys={v4_ys}, nf={nf}, amb={amb}"
    )

    # Duplicate row -> ambiguous, never guessed
    dup_lines = grid_lines + [
        GridLine(words=[hosp + "\\C5F.PDF"], left=526, top=700, right=920, bottom=714)
    ]
    m2, nf2, amb2 = match_files_to_lines(folder_files, dup_lines)
    ok = len(m2) == 7 and not nf2 and amb2 == [("CSF.pdf", 2)]
    if not ok:
        failures += 1
    print(f"{'OK  ' if ok else 'FAIL'} duplicate CSF row -> ambiguous: m={len(m2)}, nf={nf2}, amb={amb2}")

    # Missing row (scrolled out / unreadable) -> not_found, never guessed
    fewer = [ln for ln in grid_lines if "E50A.XM1" not in ln.norm_text]
    m3, nf3, amb3 = match_files_to_lines(folder_files, fewer)
    ok = (
        len(m3) == 7 and not amb3
        and nf3 == ["SAFLOR, ORLANDO EUGENIO-260901108515_eSOA.xml"]
    )
    if not ok:
        failures += 1
    print(f"{'OK  ' if ok else 'FAIL'} missing eSOA row -> not_found: m={len(m3)}, nf={nf3}, amb={amb3}")

    # -- v4 integration: the real reference screenshot ----------------------
    from pathlib import Path as _Path

    ref = (
        _Path(__file__).resolve().parent.parent
        / "screenshots" / "attach_claims" / "SS_choose_doc_type.png"
    )
    if ref.is_file():
        try:
            from PIL import Image

            img = Image.open(ref).convert("RGB")
            box = (464, 322, 1460, 760)
            ref_lines = ocr_grid_lines(img.crop(box), box[0], box[1])
            ref_files = [
                "COE.pdf", "CSF.pdf", "DTR.pdf", "SOA1.pdf", "SOA2.pdf",
                "ECHANES, PAUL GEORGE DE GUZMAN-260901105854_CF4.xml",
                "ECHANES, PAUL GEORGE DE GUZMAN-260901105854_CF5.xml",
                "ECHANES, PAUL GEORGE DE GUZMAN-260901105854_eSOA.xml",
            ]
            rm, rnf, ramb = match_files_to_lines(ref_files, ref_lines)
            rdocs = [
                detect_doc_type("\\" + f if f.lower().endswith(".pdf") else "_" + f)
                for f, _ in rm
            ]
            ok = (
                not rnf and not ramb and len(rm) == 8
                and rdocs == ["COE", "CSF", "DTR", "SOA", "SOA", "CF4", "CF5", "ESA"]
            )
            if not ok:
                failures += 1
            print(
                f"{'OK  ' if ok else 'FAIL'} v4 integration {ref.name}: "
                f"{len(rm)}/8, docs={rdocs}, nf={rnf}, amb={ramb}"
            )
        except Exception as exc:
            failures += 1
            print(f"FAIL v4 integration raised: {exc}")
    else:
        print("SKIP v4 integration (reference screenshot not found)")

    # -- v4.1: quality-based dual-pass merge --------------------------------
    # Live bug (2026-09-03 15:39/15:40): the popup's auto-selected first
    # row is white-on-blue; the normal pass read it only partially (no
    # stem) and the old "discard ANY overlapping inverted line" rule threw
    # away the good inverted reading -> COE.pdf (and once CSF.pdf)
    # "not found".
    norm_blue = [
        GridLine(
            words=["000000000020680-ADM20260815_D1520260819"],
            left=526, top=399, right=900, bottom=413,
        )
    ]
    inv_blue = [
        GridLine(
            words=["--000000000020680-ADM20260815_D1520260819\\C0E.PDF"],
            left=526, top=399, right=940, bottom=413,
        )
    ]
    merged = merge_dual_pass_lines(norm_blue, inv_blue)
    ok = len(merged) == 1 and "C0E.PDF" in merged[0].norm_text
    if not ok:
        failures += 1
    print(
        f"{'OK  ' if ok else 'FAIL'} v4.1 merge: inverted reading replaces "
        f"garbled normal on the blue row ({merged[0].norm_text!r})"
    )

    # A good normal reading must survive a weaker inverted reading.
    norm_good = [
        GridLine(
            words=["ADM20260815_D1520260819\\C0E.PDF"],
            left=526, top=399, right=900, bottom=413,
        )
    ]
    inv_weak = [
        GridLine(words=["C0E"], left=526, top=399, right=600, bottom=413)
    ]
    merged_rev = merge_dual_pass_lines(norm_good, inv_weak)
    ok = len(merged_rev) == 1 and "C0E.PDF" in merged_rev[0].norm_text
    if not ok:
        failures += 1
    print("OK   v4.1 merge: good normal reading kept over weak inverted"
          if ok else "FAIL v4.1 merge: normal reading was wrongly replaced")

    # Inverted line spanning TWO normal lines — v4.3 stem-gain: the incoming
    # line carries the COE stem and both normals are stem-less fragments, so
    # the fragments are replaced by the good reading (MATTERIG live case).
    norm_split = [
        GridLine(words=["ADM20260815"], left=526, top=395, right=700, bottom=407),
        GridLine(words=["D1520260819"], left=526, top=409, right=700, bottom=421),
    ]
    inv_span = [
        GridLine(
            words=["ADM20260815_D1520260819\\C0E.PDF"],
            left=526, top=395, right=940, bottom=421,
        )
    ]
    merged_multi = merge_dual_pass_lines(norm_split, inv_span)
    ok = (
        len(merged_multi) == 1
        and "C0E.PDF" in merged_multi[0].norm_text
    )
    if not ok:
        failures += 1
    print(
        f"{'OK  ' if ok else 'FAIL'} v4.3 merge: multi-overlap stem-gain "
        f"replaces stem-less fragments ({len(merged_multi)} line kept)"
    )

    # Inverted line spanning TWO normal lines, incoming has NO stem —
    # conservative keep (no information to gain, could lose line splits)
    norm_split2 = [
        GridLine(words=["ADM20260815\\C0E.PDF"], left=526, top=395, right=700, bottom=407),
        GridLine(words=["D1520260819\\CSF.PDF"], left=526, top=409, right=700, bottom=421),
    ]
    inv_span2 = [
        GridLine(
            words=["ADM20260815_D1520260819"],
            left=526, top=395, right=940, bottom=421,
        )
    ]
    merged_multi2 = merge_dual_pass_lines(norm_split2, inv_span2)
    ok = len(merged_multi2) == 2
    if not ok:
        failures += 1
    print(
        f"{'OK  ' if ok else 'FAIL'} v4.3 merge: multi-overlap stem-less "
        f"incoming discarded conservatively ({len(merged_multi2)} lines kept)"
    )

    # find_highlight_band: synthetic blue band + no-band case
    from PIL import Image as _Img2

    _syn = _Img2.new("RGB", (100, 60), (255, 255, 255))
    for _yy in range(20, 34):
        for _xx in range(5, 95):
            _syn.putpixel((_xx, _yy), (0, 120, 215))
    _plain = _Img2.new("RGB", (100, 60), (255, 255, 255))
    ok = find_highlight_band(_syn) == (20, 34) and find_highlight_band(_plain) is None
    if not ok:
        failures += 1
    print(
        f"{'OK  ' if ok else 'FAIL'} find_highlight_band: "
        f"band={find_highlight_band(_syn)}, plain={find_highlight_band(_plain)}"
    )

    # -- v4.1 regression: the two REAL failed live debug crops --------------
    live_cases = [
        ("PASCUA", "debug_doc_grid_20260903_153917.png", [
            "COE.pdf", "CSF.pdf", "DTR.pdf", "SOA1.pdf", "SOA2.pdf",
            "PASCUA, VIOLETA AGUSTIN-260903109443_CF4.xml",
            "PASCUA, VIOLETA, AGUSTIN-260903109443_CF5.xml",
            "PASCUA, VIOLETA, AGUSTIN-260903109443_eSOA.xml",
        ]),
        ("SAFLOR", "debug_doc_grid_20260903_154006.png", [
            "COE.pdf", "CSF.pdf", "DTR.pdf", "SOA1.pdf", "SOA2.pdf",
            "SAFLOR, ORLANDO EUGENIO-260903108515_CF4.xml",
            "SAFLOR, ORLANDO, EUGENIO-260903108515_CF5.xml",
            "SAFLOR, ORLANDO, EUGENIO-260903108515_eSOA.xml",
        ]),
        # v4.2 regression: SASPA — rows only the per-band pass could read.
        # NOTE: the 10th file (_eSOA.xml) is BELOW the visible crop area
        # (scrolled out) — the expected outcome is 9/10 with eSOA in
        # not_found. This documents the scroll limitation: in live runs
        # this correctly ABORTs instead of guessing.
        ("SASPA", "debug_doc_grid_20260903_172021.png", [
            "COE.pdf", "CSF.pdf", "DTR.pdf", "MRF.pdf", "PBC.pdf",
            "SOA1.pdf", "SOA2.pdf",
            "SASPA, JHON ROY MANAYAN-260903136574_CF4.xml",
            "SASPA, JHON ROY, MANAYAN-260903136574_CF5.xml",
            "SASPA, JHON ROY, MANAYAN-260903136574_eSOA.xml",
        ]),
        # v4.3 regression: MATTERIG — blue-row multi-overlap + 'C4.XM1' stem loss
        ("MATTERIG", "debug_doc_grid_20260904_083557.png", [
            "COE.pdf", "CSF.pdf", "DTR.pdf", "SOA1.pdf", "SOA2.pdf",
            "MATTERIG, REYMUNDO CARREON-260904082247_CF4.xml",
            "MATTERIG, REYMUNDO, CARREON-260904082247_CF5.xml",
            "MATTERIG, REYMUNDO, CARREON-260904082247_eSOA.xml",
        ]),
        # v5 regression: GUIUO — 10-file patient; eSOA.xml scrolled out
        # below CF5.xml (live 2026-09-04 11:31). Static crop documents the
        # 9/10 baseline; the v5 view loop handles the scroll in live runs.
        ("GUIUO", "debug_doc_grid_20260904_113159.png", [
            "COE.pdf", "CSF.pdf", "DTR.pdf", "MRF.pdf", "PBC.pdf",
            "SOA1.pdf", "SOA2.pdf",
            "GUIUO, REYNALDO PARALLAG-260904085021_CF4.xml",
            "GUIUO, REYNALDO, PARALLAG-260904085021_CF5.xml",
            "GUIUO, REYNALDO, PARALLAG-260904085021_eSOA.xml",
        ]),
    ]
    for label, fname, files in live_cases:
        crop_path = (
            _Path(__file__).resolve().parent.parent / "logs" / fname
        )
        if not crop_path.is_file():
            print(f"SKIP v4.1 live {label} ({fname} not found)")
            continue
        try:
            from PIL import Image as _Img

            crop_img = _Img.open(crop_path).convert("RGB")
            live_lines = ocr_grid_lines(crop_img)
            lm, lnf, lamb = match_files_to_lines(files, live_lines)
            ldocs = [
                detect_doc_type("\\" + f if f.lower().endswith(".pdf") else "_" + f)
                for f, _ in lm
            ]
            # Expected outcomes are per-case: every crop must match every
            # file EXCEPT files physically below the crop area (scrolled
            # out) — SASPA's and GUIUO's eSOA are the documented cases.
            scrolled_out = {
                "SASPA": ["SASPA, JHON ROY, MANAYAN-260903136574_eSOA.xml"],
                "GUIUO": ["GUIUO, REYNALDO, PARALLAG-260904085021_eSOA.xml"],
            }
            expect_nf = scrolled_out.get(label, [])
            pdf_docs = sorted(
                d for d in ldocs if d not in ("CF4", "CF5", "ESA")
            )
            xml_docs = [d for d in ldocs if d in ("CF4", "CF5", "ESA")]
            expect_xml = sorted(
                d for d in ("CF4", "CF5", "ESA")
                if d != "ESA" or label not in ("SASPA", "GUIUO")
            )
            ok = (
                not lamb
                and sorted(lnf) == sorted(expect_nf)
                and len(lm) == len(files) - len(expect_nf)
                # the three XML doc types present exactly once each,
                # minus any scrolled-out one
                and sorted(xml_docs) == expect_xml
            )
            if not ok:
                failures += 1
            print(
                f"{'OK  ' if ok else 'FAIL'} live regression {label}: "
                f"{len(lm)}/{len(files)}, docs={ldocs}, nf={lnf}, amb={lamb}"
            )
        except Exception as exc:
            failures += 1
            print(f"FAIL live regression {label} raised: {exc}")

    # -- v5 unit tests: view-loop scroll semantics (static, no HBSys) ------
    # 1. Processed-skip matching: after view 1 processed COE..SOA2, view 2
    #    (post-scroll, overlapping rows) must match ONLY the eSOA row —
    #    reappearing processed rows are skipped, not ambiguous.
    view1_lines = [
        GridLine(words=["ADM20260823_D1S20260826\\C0E.PDF"], left=526, top=84, right=940, bottom=98),
        GridLine(words=["ADM20260823_D1S20260826\\C5F.PDF"], left=526, top=115, right=940, bottom=129),
    ]
    view2_lines = [
        # scroll overlap: CSF row still visible at new y
        GridLine(words=["ADM20260823_D1S20260826\\C5F.PDF"], left=526, top=60, right=940, bottom=74),
        # the previously-scrolled-out row is now visible
        GridLine(words=["PARA11AG-260904085021_E50A.XM1"], left=526, top=115, right=940, bottom=129),
    ]
    guiuo_files = [
        "COE.pdf", "CSF.pdf",
        "GUIUO, REYNALDO, PARALLAG-260904085021_eSOA.xml",
    ]
    m2, nf2, amb2 = match_files_to_lines(
        ["GUIUO, REYNALDO, PARALLAG-260904085021_eSOA.xml"], view2_lines
    )
    ok = len(m2) == 1 and not nf2 and not amb2
    if not ok:
        failures += 1
    print(
        f"{'OK  ' if ok else 'FAIL'} v5 scroll: unprocessed eSOA matched in "
        f"view 2 (m={len(m2)}, nf={nf2}, amb={amb2})"
    )

    # 2. View fingerprint: same lines at different y => same fingerprint;
    #    different text => different fingerprint (no-progress guard).
    import sys as _sys
    _root = _Path(__file__).resolve().parent.parent
    if str(_root) not in _sys.path:
        _sys.path.insert(0, str(_root))
    from core.claim_attachments_uploader import AttachmentsOperator

    fp_a = AttachmentsOperator._view_fingerprint(view1_lines)
    shifted = [
        GridLine(words=["ADM20260823_D1S20260826\\C0E.PDF"], left=526, top=40, right=940, bottom=54),
        GridLine(words=["ADM20260823_D1S20260826\\C5F.PDF"], left=526, top=71, right=940, bottom=85),
    ]
    fp_b = AttachmentsOperator._view_fingerprint(shifted)
    fp_c = AttachmentsOperator._view_fingerprint(view2_lines)
    ok = fp_a == fp_b and fp_a != fp_c
    if not ok:
        failures += 1
    print(
        f"{'OK  ' if ok else 'FAIL'} v5 scroll: view fingerprint "
        f"(positional shift ignored, text change detected)"
    )

    # v5.2 (2026-09-04 14:xx GUIUO live run): the doc-cell verification
    # walk was REMOVED — cell OCR of short values (DTR read None, CF4 read
    # 'F') blocked Uploads of visually correct assignments twice. The
    # upload sequence is now: last typed row -> Upload -> Enter -> Close.
    # Structural check: the verify methods must not exist anymore.
    ok = not any(
        hasattr(AttachmentsOperator, m)
        for m in (
            "_verify_doc_column_values",
            "_ocr_doc_cell",
            "_doc_value_matches",
            "_scroll_grid_top",
        )
    )
    if not ok:
        failures += 1
    print(
        f"{'OK  ' if ok else 'FAIL'} v5.2: verification walk removed — "
        f"Upload follows the last typed row directly"
    )

    print("RESULT:", "PASSED" if failures == 0 else f"{failures} FAILURE(S)")
    raise SystemExit(0 if failures == 0 else 1)
