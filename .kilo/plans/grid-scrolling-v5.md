# Claim Attachments — Grid Scrolling Plan (v5)

Status: PLANNED (2026-09-04) — pending approval, hindi pa naka-code.

Owner: Melvin A. Calanda — Echague District Hospital

## Problem (live evidence)

GUIUO live run (2026-09-04 11:31): 10 files sa folder (COE, CSF, DTR,
MRF, PBC, SOA1, SOA2 + CF4.xml, CF5.xml, eSOA.xml). Ang 9 ay na-match at
na-detect; ang `_eSOA.xml` ay NOT FOUND — nasa ILALIM ng visible grid
(below CF5.xml). Kailangan i-scroll down ang popup grid para makita.
Kasalukuyan: not_found → ABORT (ligtas pero hindi kumpleto).

Ang popup rect sa live: (464,322)-(1457,758), grid area ~436px, ~9-10 rows
lang ang kasya. Kapag 10+ files, pumapasok sa scroll area ang huli.

## Solution overview — VIEW LOOP

Hindi solusyon ang "i-scroll tapos hanapin lang" — kailangan ng
iterative loop na may row identity, no-progress detection, at
pre-Upload verification. (skill:loop-engineering: machine-checkable
done condition, guardrails, verify before irreversible Upload.)

```
per view:
    OCR grid (existing 4-pass pipeline — HINDI binabago)
    match UNPROCESSED folder files sa visible lines (1:1, 3-tier — HINDI binabago)
    per bagong match (top→bottom): click field (497,y) → type doc → arrow (519,y) → TAB
    mark files PROCESSED (identity = filename stem+ext sa norm_text)
    kung lahat PROCESSED → pre-Upload verify
    kung may natira → scroll down → next view
```

## 1. Scroll mechanics

- Primary: hover sa gitna ng grid (live popup rect center, hal. 960,540),
  `pyautogui.scroll(-2)` (2 notches ≈ 2-6 rows), wait 0.8s, re-OCR.
  Bagong debug screenshot kada view: `debug_doc_grid_view{N}_*.png`.
- Fallback: kung walang epekto ang wheel (view hash pareho), i-click ang
  scrollbar down arrow sa popup right edge (OCR recon: '«' '>' glyphs sa
  x≈1390, y≈690 area) at re-OCR.
- Scroll-to-top para sa verification: `pyautogui.scroll(+20)`.

## 2. Row identity across views (TEXT, hindi position)

- Pagkatapos ng scroll, iba ang Y positions pero pareho ang text.
- Identity = filename stem+extension sa normalised line text.
- Row na PROCESSED na at nakikita ulit (scroll overlap) → SKIP, hindi
  na i-te-type ulit, HINDI rin considered ambiguous.
- WITHIN one view: isang file tumugma sa 2+ lines → ABORT (duplicate
  rows — existing rule, hindi nababago).

## 3. Guardrails (Principle 4)

| Guardrail | Kapag trigger |
|---|---|
| No-progress: view hash pareho 2x sunod (hindi nag-scroll) | fallback scrollbar click → kung pareho pa rin: ABORT "cannot scroll to remaining files" |
| MAX_SCROLL_ITERATIONS = 12 | ABORT |
| Unknown/unclassified file row | ABORT (existing) |
| Within-view ambiguous | ABORT (existing) |
| Verify mismatch sa pre-Upload pass (after 1 OCR retry) | ABORT — WALANG Upload click |

## 4. Pre-Upload verification pass (bago ang irreversible Upload)

Kapag lahat ng files PROCESSED na:

1. Scroll pabalik sa taas, wait 0.8s.
2. I-walk ang mga views (scroll down ulit hanggang dulo).
3. Sa BAWAT file row: i-OCR ang DOC COLUMN (x≈470..524) — dapat andoon
   ang na-type na doc type (COE, CSF, DTR, SOA, MRF, PBC, CF4, CF5, ESA).
   Maikli at malinis ang mga values sa puting cell — mababasa ng OCR.
4. Empty doc cell o mismatch → 1 OCR retry → kung ganoon pa rin → ABORT.

**Bakit ito ang pinakamahalagang bahagi:** huli nitong mahuhuli ang mga
LEFTOVER DUPLICATE ROWS mula sa mga na-ABORT na runs (real na operational
hazard — may memo na dapat i-Delete muna bago mag-run). Kapag may natirang
duplicate row, may file-text row na WALANG doc type → ABORT bago ang
Upload, imbesang maipadala ang hindi kumpletong claim sa HBSys.
Ito ang verify-before-irreversible-action (Principle 3).

## 5. Testing

- Unit (static, walang HBSys): synthetic before/after scroll views —
  skip logic (processed row reappears → skip, hindi ambiguous), no-progress
  hash detection, iteration cap. I-add sa `claim_attachments_doc_type.py`
  standalone tests.
- Ang GUIUO crop (`logs/debug_doc_grid_20260904_113159.png`) ay idagdag sa
  live regression suite bilang documented scrolled-out case (9/10 ang
  expected sa STATIC crop — hindi nito matetest ang scrolling mismo).
- Live protocol: `--live --confirm-each --limit 1` sa pasyenteng may 10
  files (GUIUO). Sana makita sa log: view 1 rows → scroll → view 2 rows →
  verify pass → Upload → OK → Close.

## 6. Files na babaguin (after approval)

1. `core/claim_attachments_uploader.py` — `assign_doc_types_and_upload()`
   ay gawing view loop; new helpers: `_scroll_grid()`, `_ocr_view()`,
   `_verify_doc_column()`. Upload/OK/Close coords unchanged.
2. `core/claim_attachments_doc_type.py` — processed-row skip helper +
   synthetic scroll tests. OCR/matching pipeline HINDI binabago.
3. `CHANGE_RULES.md` + `README.md` + `DOC_TYPE_ASSIGNMENT_PLAN.md` —
   same-session documentation (SKILL.md step 8 rule).

## 7. Ang PROMPT (copy-paste ready)

---

PROMPT: Claim Attachments — Grid Scrolling (v5) para sa 10+ files

Konteksto: Gumagana na ang doc type assignment (v4.3) sa
`core/claim_attachments_uploader.py` + `core/claim_attachments_doc_type.py`
para sa mga rows na VISIBLE sa attachment grid. Problema: kapag 10+ files
ang pasyente, ang huling rows (hal. _eSOA.xml na nasa ilalim ng _CF5.xml)
ay hindi visible — nasa scroll area. Kasalukuyan: not_found → ABORT.

Gawin (v5 — scroll loop):

1. VIEW LOOP — pagkatapos ng 2nd Open (XML attach):
   a. I-OCR ang visible grid (existing 4-pass pipeline — huwag baguhin).
   b. I-match ang mga UNPROCESSED folder files sa visible lines (1:1,
      3-tier — huwag baguhin).
   c. Per bagong match (top→bottom): click doc field (DOC_FIELD_X=497),
      i-type ang doc type, click arrow (DOC_ARROW_X=519), press TAB.
   d. Markahin ang file na PROCESSED (identity = filename stem+ext sa
      normalised line text — hindi position).
2. KUNG MAY NATIRANG files:
   a. I-scroll down: hover sa gitna ng grid (live popup rect center, hal.
      960,540), pyautogui.scroll(-2), wait 0.8s.
   b. Bagong debug screenshot kada view (logs/debug_doc_grid_viewN_*.png).
   c. Mga PROCESSED rows na nakikita ulit (scroll overlap) → SKIP — huwag
      i-type ulit, hindi ambiguous.
   d. I-match lang ang unprocessed files sa mga bagong lines. Within-view:
      1 file = 2+ lines → ABORT (duplicates — never guess).
   e. Ulitin hanggang lahat ng files ay PROCESSED.
3. NO-PROGRESS GUARDRAILS:
   - Kung walang bagong match pagkatapos ng scroll at view hash ay pareho
     ng nakaraan: i-try ang scrollbar down arrow click (popup right edge,
     x≈1390, y≈690) bilang fallback; kung pareho pa rin ang view →
     ABORT: "cannot scroll to remaining files".
   - MAX_SCROLL_ITERATIONS = 12 → ABORT.
4. PRE-UPLOAD VERIFICATION (bago ang Upload click):
   a. Scroll pabalik sa taas (pyautogui.scroll(+20)), wait 0.8s.
   b. I-walk ulit ang views; sa BAWAT file row, i-OCR ang DOC COLUMN
      (x≈470..524) — dapat may na-type nang doc type (COE/CSF/DTR/SOA/
      MRF/PBC/MMC/OPR/ANR/CF2/CF3/CF4/CF5/ESA).
   c. Empty cell o mismatch (after 1 OCR retry) → ABORT — WALANG Upload.
      Huli nitong mahuhuli ang leftover duplicate rows mula sa mga
      na-ABORT na runs.
5. PAG VERIFIED LAHAT: click Upload (598,730) → Enter (OK) → click Close
   (1408,734) → i-verify na mawala ang popup (existing).

Mga patakaran:
- Never-guess policy: ambiguous / not_found / verify mismatch / scroll
  failure = ABORT, walang Upload click.
- Huwag baguhin: working OCR engine, XML generator, claims checker,
  signing engine, buong OCR/matching pipeline ng doc-type step.
- Plan → code → tests. Gamitin ang skill:loop-engineering principles.
- Testing: synthetic before/after scroll views (unit tests sa
  claim_attachments_doc_type.py) + idugtog ang GUIUO crop
  (logs/debug_doc_grid_20260904_113159.png) sa live regression suite bilang
  documented scrolled-out case (9/10 expected sa static crop) + live test
  protocol na --live --confirm-each --limit 1 sa 10-file patient.
- Pagkatapos ng lahat: i-update ang CHANGE_RULES.md, README.md, at
  DOC_TYPE_ASSIGNMENT_PLAN.md sa same work session.

---

## 8. Buong version history (context)

| Version | Petsa | Strategy | Result |
|---|---|---|---|
| v4.1 | 2026-09-03 | 3-pass OCR + quality merge (blue row) | PASCUA/SAFLOR 8/8 |
| v4.2 | 2026-09-03 | + per-row-band pass | SASPA rows nababasa |
| v4.3 | 2026-09-04 | + stem-gain merge + mutated-stem tier | MATTERIG 8/8, LIVE PASSED |
| v5 (ito) | 2026-09-04 (planned) | + scroll loop + pre-Upload verify | para sa 10+ file patients (GUIUO case) |
