# Upload Flow Fix v5.1 — doc-cell verify "read None" blocking Upload

Status: PLANNED (2026-09-04, after 13:17 GUIUO live run)

## Live evidence (13:17 GUIUO run — lahat ng iba ay GUMAGANA)

- View 1: 9 rows typed (COE, CSF, DTR, MRF, PBC, SOA, SOA, CF4, CF5) — OK
- Scroll down — OK
- View 2: eSOA typed as ESA (row 10/10) — OK (ang v5 scroll ay GUMANA)
- Pre-Upload verification: COE ✓, CSF ✓, tapos —
  `VERIFY FAIL: DTR.pdf expected 'DTR', doc cell read None` (2 reads,
  kasama ang retry)
- Result: nag-close ang popup imbes na magpatuloy sa Upload → OK → Close

## Root cause

`_ocr_doc_cell()` ay gumagamit ng fixed ±11px window sa paligid ng matched
OCR LINE's center_y. Pero:

- Ang file path column ay nagwa-wrap sa 2-3 text lines kada row. Ang
  filename line (kung saan nakasabit ang stem na ginamit ng matching) ay
  nasa IBA'T IBANG taas loob ng row band:
  - COE filename line: y=406 (band 383..413 — gitna)
  - DTR filename line: y=467 (band 443..473 — ILALIM, dahil wrapped)
- Ang combo cell text ay vertically centered sa ROW BAND (hal. ~458 para
  sa DTR), hindi sa filename line.
- Kaya ang DTR window (456..478) ay naka-clip sa itaas na kalahati ng
  glyphs → binarized PSM 7 ay walang mabasa → None → ABORT.

Hindi ito case-specific: kahit anong row na may 2+ wrapped path lines ay
pwedeng magbigay ng line center_y na malayo sa combo cell center.

## Fix (dalawang bahagi)

### 1. `_ocr_doc_cell()` rework — BAND-based window (hindi line-based)

- Kunin ang row separators ng current view (may
  `find_row_separator_lines()` na sa doc_type module) at hanapin ang band
  na naglalaman ng matched line's y (screen coords: band + crop offset).
- Ang strip window: FULL band height (band_top+2 .. band_bottom-2), x
  strip 466..528 (Doc column borders 470..524 + padding).
- Fallback kung walang separators: ±14px sa line center_y.
- Multi-variant reads, first non-empty wins (binarize@150, binarize@190,
  plain grayscale; PSM 7 tapos PSM 8; 4x upscale) — tinatanggal ang
  single-threshold fragility.
- I-pass ang verify view's existing screenshot (hindi per-row full-screen
  capture) — mas mabilis at consistent.

### 2. `_verify_doc_column_values()` policy rework — None ≠ mismatch

- MISMATCH (may nabasang text na != expected) → ABORT pa rin — ito ang
  tunay na evidence ng mali/duplicate/leftover row. (Hindi nababago.)
- NONE (unreadable after retry) → WARNING lang, magpatuloy:
  "doc cell unreadable — row matched 1:1, proceeding".
  - Justification: ang duplicate-row threat (ang dahilan ng verify) ay
    nahuhuli na ng per-view `match_files_to_lines` ambiguous check —
    isang file na tumutugma sa 2 rows = ABORT bago pa ang cell reads.
    Ang cell read ay value-confirmation layer lang; hindi ito dapat
    mag-block sa OCR flakiness.
- Nitong mababawasan ang mga spurious ABORT habang nakapreserve pa rin
  ang never-guess sa mga tunay na mismatch.

### 3. Upload/OK/Close sequence — WALANG binago

Ayon sa user (at existing code): pagkatapos ng huling row (eSOA) → click
Upload (598,730) → popup na may OK → press Enter → click Close (1408,734)
→ popup-gone verify. Ito na mismo ang step 3 ng `assign_doc_types_and_upload()`.

## Testing plan

- Unit (static, idadagdag sa claim_attachments_doc_type.py tests):
  - Band-selection helper: given separators + line y, i-expect ang band
    na naglalaman nito (synthetic separators).
  - None-policy: mismatch → abort-path flag; None → proceed-path flag.
- Rerun buong standalone suite (5 live regression crops dapat hindi
  magbago ang expectations).
- Live: `--live --confirm-each --limit 1` sa 10-file patient (GUIUO) —
  inaasahang log: 10/10 typed → verify (COE..ESA, posibleng WARNING sa
  ilang cell) → Upload → Enter (OK) → Close → popup closed.

## Files na babaguin

1. `core/claim_attachments_uploader.py` — `_ocr_doc_cell()`,
   `_verify_doc_column_values()` (band window + None policy + pass
   screenshot)
2. `core/claim_attachments_doc_type.py` — unit tests lang (band selection,
   None policy); OCR/matching pipeline HINDI ginagalaw
3. `CHANGE_RULES.md` + `README.md` + `DOC_TYPE_ASSIGNMENT_PLAN.md` —
   same-session documentation (SKILL.md step 8)

## Version history context

| Version | Petsa | Result |
|---|---|---|
| v4.3 | 2026-09-04 | MATTERIG 8/8, LIVE PASSED (9-file patients) |
| v5 | 2026-09-04 | Scroll + eSOA typing GUMANA (10/10 sa 13:17 run) |
| v5.1 (ito) | 2026-09-04 | Ayusin ang verify cell read para magpatuloy sa Upload → OK → Close |
