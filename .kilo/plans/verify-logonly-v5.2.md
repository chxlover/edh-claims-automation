# Upload Flow Fix v5.2 — TANGGALIN ang doc-cell verification walk

Status: PLANNED (2026-09-04, after the 14:xx GUIUO live run)

## Live evidence

- v5.1 gumawa lahat ng mechanics: 10/10 rows typed (kabilang eSOA sa
  view 2), verified scroll-top, band window, arrow exclusion.
- PERO dalawang beses na nag-VERIFY FAIL ang cell OCR sa maikling
  values: DTR (kanina, read None) at CF4 (read 'F' lang) — kahit TAMA
  lahat sa visual (kumpirmasyon ng may-ari). Ang cell OCR ng 2-3 char
  values ay hindi kapani-paniwala.
- Desisyon ng may-ari: "pwede kayang hindi nalang mag verify after ng
  ESOA doc type click upload na" — tanggalin ang verify, diretso Upload.

## Fix (v5.2 final)

1. SA `assign_doc_types_and_upload()`: pagkatapos ng view loop
   (lahat ng files PROCESSED, kasama ang eSOA/ESA na huling tina-type) —
   **diretso sa Upload (598,730) → wait OK dialog → press Enter →
   click Close (1408,734)**. Walang verify walk.
2. Ang `_verify_doc_column_values()` at `_ocr_doc_cell()` calls ay
   tatanggaling sa flow (methods mananatili sa file para sa future
   diagnostic use, o buburahin — buburahin na para walang dead code;
   simple ang mas mahusay).
3. Ang duplicate-row guard ay NANANATILI sa tamang lugar: per-view 1:1
   matching habang nagta-type (1 file = 2+ rows sa isang view = ABORT
   bago mag-type). Ito ang tunay na guard; hindi kailangan ng cell OCR.
4. Guardrails na nananatili: ambiguous → ABORT, MAX_SCROLL_ITERATIONS,
   no-progress scroll detection, never-guess policy — lahat buo pa rin.

## Files na babaguhin

1. `core/claim_attachments_uploader.py`:
   - `assign_doc_types_and_upload()`: tanggalin ang verify call; docstring
     update (step 4 = Upload agad).
   - Burahin: `_verify_doc_column_values()`, `_ocr_doc_cell()`,
     `_doc_value_matches()`, DOC_COLUMN_PAD constant (dead code na).
   - `_scroll_grid_top()`: mananatili (gumagamit pa ang view loop? HINDI —
     scroll-top ay verify lang gumagamit. Burahin din kung wala nang
     caller; i-check muna).
2. `core/claim_attachments_doc_type.py` — tests:
   - Tanggalin ang v5.1 band-window tests at policy tests (naka-depend
     sa mga buradong methods).
   - Mananatili: lahat ng matching/fingerprint/live-crop tests.
3. `CHANGE_RULES.md` + `README.md` + `DOC_TYPE_ASSIGNMENT_PLAN.md` —
   same-session documentation.

## Testing

- Buong standalone suite rerun (dapat lahat ng retained tests PASSED).
- py_compile + GUI import chain.
- Live: GUIUO ulit — inaasahang log: view 1 (9 rows) → scroll → view 2
  (ESA) → **Upload → OK (Enter) → Close** → "doc types assigned (10 rows
  across views) and uploaded". Walang verify lines.

## Version history context

| Version | Petsa | Result |
|---|---|---|
| v5 | 2026-09-04 | Scroll + eSOA typing GUMANA (10/10) |
| v5.1 | 2026-09-04 | Band window + verified scroll-top; pero cell OCR flaky (DTR None, CF4 'F') — verify pa rin ang blocker |
| v5.2 (ito) | 2026-09-04 | VERIFY TANGGAL — after eSOA, direktong Upload → OK (Enter) → Close |
