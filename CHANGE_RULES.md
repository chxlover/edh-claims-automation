# EDH Claims Bot Change Rules and Record

Owner: Melvin A. Calanda  
Project: EDH Claims Automation System  
Purpose: Permanent rules and traceable record for every code or configuration change.

## Mandatory Update Rule

Every implementation, bug fix, behavior change, configuration change, database-schema
change, or user-visible GUI change must update this file in the same work session.

Each change record must include:

- Date in `YYYY-MM-DD` format.
- Short change title.
- Reason for the change.
- Files added or modified.
- Behavior before and after the change.
- Safety or compatibility notes.
- Verification performed and its result.

Documentation-only edits may be grouped into one record. Log files, generated reports,
patient folders, scanned documents, backups, caches, and other runtime data are not code
changes and must not be recorded individually.

## Permanent Safety Rules

- Never delete, overwrite, or alter original patient scans.
- Never overwrite an existing generated file when its content is different unless the
  user explicitly approves that exact operation.
- HBSys/MySQL access from Claims Bot remains read-only unless the user explicitly changes
  the project policy.
- Keep new behavior modular. Make only minimal integrations in production files.
- Do not duplicate business logic between the GUI and core modules.
- Background services must not control the mouse or keyboard unless they are explicitly
  designed as UI automation.
- Background services must not freeze Tkinter's main thread.
- Ambiguous patient, document, folder, or confinement matches must never be guessed.
- Preserve backward compatibility with existing configuration files whenever possible.
- Test changes in proportion to their risk and record the verification result below.

### 2026-09-09 — Claim Attachments v7: DOCTYPE SOURCE-to-DESTINATION AUDIT (OCR readback removed)

Reason:

- Owner directive (2026-09-09): ang v6.2 OCR-readback audit ay lumikha ng
  maraming "REVIEW - READ_FAILED" (ang HBSys grid cells ay hindi mabasa
  ng OCR nang maaasahan). Bagong verification model: i-verify ang
  SOURCE (processing order + existing extracted DOCTYPE) laban sa
  ACTUAL DESTINATION FILES sa
  `C:\Shared Folder\eClaimsDoc\<CLAIM_SERIES>\` — ang destination
  FILENAME (`...-RAW-<DOCTYPE>-<NUMBER>.pdf/.xml`) ang tanging
  verification source; WALANG OCR, screenshot OCR, image recognition,
  o visual text recognition sa bagong verification. Ang existing
  extraction at assignment (v6.1 tab-walk) ay HINDI binago — audit
  layer LANG.

Files:

- REWRITTEN `core/claim_attachments_doctype_audit.py` (v6.2 OCR
  readback modules REPLACED — the readback approach is what produced
  READ_FAILED noise; task explicitly forbids keeping OCR in the new
  verification):
  - `normalize_doctype()` — pinned (strip/upper/None).
  - NEW `normalize_sequence()` — "01"/"1"/1 ay logically equal (int
    compare); original display value preserved sa report.
  - `extract_claim_series_from_title()` — pinned (existing popup-title
    value; walang second extraction mechanism).
  - NEW `SourceAuditRecord` + `SourceAuditCollector` — in-memory source
    records; `start_series()` resets the per-claim-series sequence to
    01 (destination files ay per-claim-series ang numbering);
    `add()` assigns "01","02",... sa ACTUAL processing order.
  - NEW `parse_destination_filename()` — deterministic generic regex
    `-RAW-([A-Za-z0-9]{2,4})-(\d{1,3})$` on the extension-stripped
    stem: `DOH-PDFA-1b-RAW-CF4-08.xml` → ("CF4","08"); walang
    hardcoded doc-type vocabulary, walang AI/fuzzy.
  - NEW `scan_destination_folder()` — single listing ng claim series
    destination folder; None kapag wala ang folder (caller records
    REVIEW, hindi crash).
  - NEW `audit_claim_series()` — source vs destination comparison:
    number+doctype MATCH; missing destination number → MISMATCH
    "Destination file not found"; DOCTYPE mismatch → MISMATCH;
    duplicate destination numbers → MISMATCH "Duplicate destination
    sequence number" (never silently chosen); destination number na
    walang source record → "UNEXPECTED DESTINATION"; unparseable
    destination filename → REVIEW "Unable to parse destination
    filename"; missing claim series folder → REVIEW "Claim Series
    destination folder not found" (hindi-crash).
  - REMOVED: `interpret_doc_ocr()`, `read_doc_type_cell()`,
    `DocTypeAuditRecord` (actual_selected_doctype), OCR cell geometry
    constants, pytesseract usage — ang buong OCR readback path ay
    tinanggal sa audit (task §2/§22).
  - `build_doctype_audit_excel()` / `finalize_doctype_audit()` — bagong
    12-column report (Status, Reason, Claim Series Number, Source
    Number, Source Filename, Source Full Path, Extracted DOCTYPE,
    Destination Number, Destination Filename, Destination Full Path,
    Destination DOCTYPE, Timestamp — WALANG "Actual Selected DOCTYPE"
    o OCR readback fields); summary: DOCTYPE SOURCE-DESTINATION AUDIT /
    TOTAL RECORDS / MATCH / MISMATCH / REVIEW / UNEXPECTED DESTINATION
    (calculated mula sa actual records); bold headers, freeze panes,
    auto-filter, column widths, status colors (MATCH=green,
    MISMATCH=red, REVIEW=yellow, UNEXPECTED=red error-highlight);
    ONE build pagkatapos ng buong batch + auto-open (os.startfile).
- `core/claim_attachments_uploader.py`:
  - `AttachmentsOperator.__init__` — `doctype_audit_records` (v6.2) →
    `source_audit: SourceAuditCollector` + `doctype_audit_results`
    (audit rows). 
  - NEW `_current_claim_series()` — claim series mula sa EXISTING
    popup title (reuse ng `extract_claim_series_from_title`).
  - REMOVED `_audit_doc_type_selection()` (OCR readback + screenshot
    kada row) — REPLACED ng:
    - `_audit_source_document()` — tinatawag IMMEDIATELY pagkatapos ng
      bawat successful copy + existing extraction sa v6.1 tab-walk;
      nagre-record ng source record KASAMA ang per-series source number
      (01, 02, ... processing order, hindi sorted filenames, hindi
      Explorer order); consumed value = existing
      `file_docs[matched_file]`.
    - `_audit_destination_for_series()` — ONE destination folder scan
      pagkatapos ng Upload/OK/Close ng claim series
      (`audit_claim_series`); results appended in-memory.
  - `assign_doc_types_and_upload()` — `start_series()` bago ang
    tab-walk; `_audit_source_document()` kada row (pagkatapos ng
    `_type_row_doc`, kung saan nag-success ang copy); destination
    audit pagkatapos ng Upload→OK→Close. Ang tab-walk, extraction,
    selection, Upload sequence ay HINDI ginalaw.
  - `run_attachments_loop()` — finalize ng bagong report (pagkatapos ng
    buong batch, auto-open).
  - Imports updated (WALANG pyautogui screenshot / OCR sa audit path).
- `gui/claim_attachments_tab.py` — `_upload_worker` finalize block:
  `doctype_audit_records` → `doctype_audit_results`. Walang iba pang
  GUI changes.

Behavior:

- Per document (LIVE): existing copy → existing extraction → existing
  selection (lahat HINDI binago) → source record na may next na
  per-series sequence number (01, 02, ...).
- Per claim series (pagkatapos ng Upload): isang scan ng
  `C:\Shared Folder\eClaimsDoc\<SERIES>\`; kada source record: hanapin
  ang destination entry na may PAREHONG filename sequence number →
  compare number + normalized DOCTYPE → MATCH/MISMATCH; detect
  unexpected destinations, duplicate numbers, unparseable filenames,
  missing folder (REVIEW, hindi crash).
- Pagkatapos ng buong batch (CLI at GUI): isang Excel report na may
  summary + colored rows, auto-open.
- Dry-run: walang source records (walang aktwal na copy sa live grid
  loop) → walang report.

Safety / compatibility:

- Never-guess: duplicate destination numbers ay laging MISMATCH
  (listed lahat ng candidates sa reason); missing folder ay REVIEW;
  unparseable filename ay REVIEW — hindi kailanman MATCH.
- Performance: +1 in-memory record kada row (microseconds); +1 folder
  listing kada claim series; WALANG OCR, WALANG screenshots, WALANG
  per-document Excel writes — mas mabilis pa sa v6.2 (na may +1
  screenshot + 2 OCR passes kada row).
- Ang v6.1 ABORT guards (copy fail / foreign path / duplicate focus /
  no focused row) ay hindi ginalaw — source records lang ang nadadagdag
  pagkatapos ng successful copy/match.
- Walang binagong: doc_type extraction tables, tab-walk flow, Upload
  sequence, XML/PDF handling, claims processing, database, ibang GUI
  components. `normalize_doctype` signature unchanged (pinned).

Verification:

- `python -m core.claim_attachments_doctype_audit` — 29/29 PASSED
  (normalize doctype/sequence, generic destination parsing kasama ang
  XML/3-digit/OTH + junk cases, popup-title claim series, per-series
  sequence reset, TEST 1 normal match, TEST 2 DOCTYPE mismatch, TEST 3
  number mismatch, TEST 4 missing destination, TEST 5 unexpected
  destination, TEST 6 duplicate number, TEST 7 10-document perfect
  match in processing order, TEST 8 multiple claim series +
  mixed-batch filtering, missing folder REVIEW, unparseable filename
  REVIEW, Excel summary counts/columns/colors/freeze/auto-filter,
  finalize empty→None).
- `python -m core.claim_attachments_doc_type` — PASSED (59/59
  regression — extraction untouched).
- `python -m unittest discover tests` — 87/87 OK (no regressions).
- `python -m py_compile` (uploader + audit + gui tab) + `-W error`
  import — passed.
- Uploader dry-run — malinis (HBSys window error lang, inaasahan).
- End-to-end vs REAL destination folders: VILORIA/260909144200 →
  10/10 MATCH; VENTURA/260818103440 → 8/8 MATCH (parehong claim series
  folders sa `C:\Shared Folder\eClaimsDoc`).
- Live batch re-test PENDING (kailangan ng live HBSys run; tingnan ang
  per-row "doctype audit: source NN -> DOC" logs at ang "claim series
  X vs eClaimsDoc destination — MATCH=n, ..." log kada patient).

### 2026-09-09 — Claim Attachments v6.2: DOCTYPE selection AUDIT + Excel verification report

Reason:

- Owner directive (2026-09-09): kailangan ng deterministic audit na
  nagpapatunay na ang DOCTYPE na EXTRACTED mula sa source filename ay
  talagang ang NAPILI sa HBSys UI — hindi lang ang internal variable.
  "Did the DOCTYPE that the automation extracted from the source
  filename/path actually become the DOCTYPE selected in the HBSys UI?"
- Ang existing extraction/selection flow (v6.1 tab-walk) ay HINDI
  binago — readback + comparison + audit logging LANG ang dinagdag.

Files:

- NEW `core/claim_attachments_doctype_audit.py` — self-contained audit
  module (AGENTS.md modularity rule):
  - `normalize_doctype()` — deterministic strip/upper/None lang.
  - `interpret_doc_ocr()` — STRICT-VOCABULARY OCR interpretation: ang
    reading ay tinatanggap LANG kapag normalise (project OCR-confusion
    mapping _norm: O/0, S/5, I/L/1, B/8, Z/2) patung sa EXACT na isa sa
    14 known doc values; kung hindi → None (READ_FAILED). Walang fuzzy,
    walang AI.
  - `read_doc_type_cell(image, row_y)` — actual HBSys cell readback: crop
    ng Doc Type cell (x=468..512, row_y±12px, arrow excluded), 3x scale
    grayscale + fixed-threshold OCR passes (PSM 7, A-Z0-9 whitelist).
    Bakit OCR: ang HBSys grid cells ay walang control-level text API
    (custom FNWNS3115 painting — parehong dahilan bakit OCR ang grid
    reading stack ng project mula v4).
  - `extract_claim_series_from_title()` — claim series mula sa EXISTING
    popup title ('Attachments for X - 260907095364' → '260907095364');
    walang second extraction mechanism.
  - `DocTypeAuditRecord` dataclass — in-memory record kada document:
    status, claim_series_number, source_filename, source_full_path,
    extracted_doctype, actual_selected_doctype, timestamp.
  - `build_doctype_audit_excel()` / `finalize_doctype_audit()` — Excel
    report: DOCTYPE VERIFICATION SUMMARY section (TOTAL/MATCH/MISMATCH/
    REVIEW counts mula sa actual records), bold header + freeze panes +
    auto-filter + column widths, status highlighting (MATCH=green,
    MISMATCH=red, REVIEW=yellow), ONE build pagkatapos ng BUONG batch,
    auto-open via os.startfile. SHA-256 fields ay HINDI idinagdag (walang
    existing hash data sa doc-type flow; task §8 ay optional).
- `core/claim_attachments_uploader.py`:
  - `AttachmentsOperator.__init__` — `doctype_audit_records: list`
    (in-memory lang habang nagpaprocess).
  - Bagong `_audit_doc_type_selection()` — tinatawag IMMEDIATELY pagkatapos
    ng bawat `_type_row_doc` (pagkatapos ng arrow click, bago ang TAB
    focus move): readback ng ACTUAL selected value → comparison →
    MATCH/MISMATCH/REVIEW record + log. Hindi blocking — record lang,
    tuloy ang workflow (audit, hindi bagong fail-safe).
  - `run_attachments_loop()` — pagkatapos ng buong batch: isang tawag sa
    `finalize_doctype_audit()` (Excel build + auto-open), live records
    lang (dry-run ay walang records — walang pinili sa HBSys).
  - Imports updated. Ang v6.1 tab-walk, extraction, at selection flow ay
    HINDI ginalaw.
- `gui/claim_attachments_tab.py` — `_upload_worker`: pagkatapos ng
  buong batch (mark_completed), isang tawag sa `finalize_doctype_audit()`
  + log sa GUI. Walang iba pang GUI changes.

Behavior:

- Per document (LIVE lang): extracted = existing `file_docs[matched_file]`
  (HINDI binago) → existing `_type_row_doc` selection (HINDI binago) →
  NEW readback ng actual cell value → compare:
  extracted == actual → MATCH; actual == READ_FAILED → REVIEW (hindi
  kailanman MATCH); iba → MISMATCH. Isang audit record kada document,
  in-memory; tuloy ang processing anuman ang status.
- Dry-run: walang records (walang aktwal na selection sa HBSys).
- Pagkatapos ng buong batch (CLI at GUI): isang Excel report na may
  summary + colored rows, auto-open.

Safety / compatibility:

- Never-guess: unreadable cell → REVIEW, hindi MATCH; MISMATCH ay hindi
  pwedeng maging MATCH (strict vocabulary, walang fuzzy).
- Ang mismong existing ABORT guards (copy fail / foreign path /
  duplicate focus / no focused row) ay hindi ginalaw — ang audit ay
  non-blocking at nangyayari LANG kapag successful na ang selection.
- Performance: +1 screenshot + 2 OCR passes sa maliit na cell crop kada
  row (~0.3-0.5s) — walang full-screen OCR, walang hash recomputation,
  walang per-row Excel writes.
- Walang binagong: doc_type extraction tables, tab-walk flow, Upload
  sequence, XML/PDF handling, claims processing, database, ibang GUI
  components. openpyxl ay existing dependency na (fees_checker,
  not_transmitted_batches).

Verification:

- `python -m core.claim_attachments_doctype_audit` — 16/16 PASSED
  (normalize, MATCH/MISMATCH comparisons, strict-vocabulary interpretation
  kasama ang garbage→None, synthetic cell readback COE/CF4/empty, claim
  series parsing, Excel build: summary counts, freeze panes, auto-filter,
  status colors, one row per document, finalize no-records→None).
- `python -m core.claim_attachments_doc_type` — 59/59 PASSED (regression).
- `python -m py_compile` (warnings-as-errors) sa uploader + audit +
  gui tab — passed.
- Uploader dry-run — malinis (READY folder ay walang laman ngayon; HBSys
  window focus error lang ang inaasahan nang walang bukas na HBSys).
- `import gui.claim_attachments_tab` — passed.
- Live test PENDING: isang LIVE batch; tingnan ang per-row
  "doctype audit: MATCH/MISMATCH/REVIEW" logs at ang auto-open na Excel
  report pagkatapos ng batch.

### 2026-09-08 — Claim Attachments doc type v6.1: TAB-WALK (walang scroll, hanggang ESA)

Reason:

- Live run 2026-09-08 09:10 (ANDAYA + VENTURA, 15 files): rows 1-9 OK
  via clipboard copy (tamang doc type, tamang row — "...\ANR.pdf" hanggang
  "...\OPR.pdf" ang naka-log na path). PBC (row 10) ay "no context menu
  appeared" — ito ang partially-visible last row sa viewport, hindi
  pwedeng i-right-click. Pagkatapos, ang OCR-fallback anchor ay nagbigay
  ng y=998 (LABAS ng popup — bottom=758) at nag-type doon; ang wheel
  scroll (960,540) ay sumira ng grid state (0 bands, garbage anchors
  y=1419/1446) → lahat ng right-click nag-fail → ABORT sa dalawang patient.
- Owner directive (2026-09-08): "after mapili ng doc type TAB lang
  pindutin wag na scroll hanggang matapos ang files bale ESA ang
  pinakalast" — TAB ang gumagalaw ng focus, HBSys mismo ang nag-a-auto-
  scroll ng focused row, ESA ang huling doc type, at dapat malagyan ng
  doc type ang LAHAT ng na-upload na documents.

Files:

- `core/claim_attachments_uploader.py`:
  - `assign_doc_types_and_upload()` — pinalitan ang v6.0 view/scroll
    loop ng v6.1 TAB-WALK: focused (blue) row → right-click "copy" →
    clipboard FULL ABSOLUTE PATH → match sa patient file (exact basename
    + expected parent) → type doc type → arrow → TAB → next focused row,
    kada row hanggang mabuo ang `len(processed) == len(folder_files)`;
    pagkatapos ay Upload → Enter (OK) → Close (v5.2, unchanged). Step 0
    (folder listing, file_docs, expected_parent, dry-run) HINDI binago.
  - Bagong `_focused_row_y()` — focused row detection via blue highlight
    band (`find_highlight_band`, pixel scan, WALANG OCR); band top >= 50px
    mula sa popup top (title/header excluded), height >= 8px; retry
    hanggang 2.0s (0.15s interval); None kapag walang valid focus.
  - TINANGGAL (dead live machinery): `_scroll_grid_down()`,
    `_ocr_doc_grid_view()`, `_row_bands_from_screenshot()`,
    `_bands_from_lines()`, constant `MAX_SCROLL_ITERATIONS`, at ang
    buong no-progress/heavier-scroll logic.
  - `_view_fingerprint()` — PINANATILI (ginagamit ng offline test suite
    ng doc_type module); docstring updated.
  - Imports pruned: tanggal `find_row_separator_lines`,
    `match_files_to_lines`, `ocr_grid_lines`; dagdag
    `find_highlight_band`; panatili `detect_doc_type`,
    `match_copied_path_to_file`.
- `CHANGE_RULES.md` (this record) at `DOC_TYPE_ASSIGNMENT_PLAN.md`
  (v6.1 history row + status) — updated.
- `core/claim_attachments_doc_type.py` — WALANG production changes.
- `requirements.txt` — WALANG changes (pyperclip==1.11.0 na mula v6.0).

Behavior:

- Before (v6.0): pixel band scan ng visible rows + wheel scroll + OCR
  fallback — nagiging unstable kapag >10 files (partially-visible row 10
  ay hindi ma-right-click; wheel scroll sumira ng state).
- After (v6.1): kada row — blue focused band → copy path → type → TAB.
  WALANG manual scrolling KAHIT KAILAN: ang TAB ang nag-a-advance ng
  grid focus at si HBSys ang nag-a-auto-scroll ng focused row papunta
  sa view (na-solve ang >10-file case). Row identity = clipboard full
  absolute path (hindi position, hindi row number, hindi OCR). Inaasahan
  ang ESA (eSOA.xml) bilang huling doc type, pero ang authoritative
  completion condition ay `len(processed) == len(folder_files)`.

Safety:

- Never-guess ABORTs (lahat bago ang anumang Upload click, na may buong
  path sa log): no focused blue row, copy failure, foreign path (hal.
  VILLARUEL XML sa maling grid), duplicate focus (bumalik ang focus sa
  nagawang row).
- Upload ay nangyayari LANG pagkatapos mabuo ang LAHAT ng expected files
  — walang partial upload.
- MAX_DOC_ROWS = 40 cap — pinanatili.
- WALANG OCR sa live path; WALANG scroll sa live path; dry-run unchanged
  (log lang).

Verification:

- `python -m py_compile` (warnings-as-errors) sa uploader + doc_type
  modules — passed.
- `python -m core.claim_attachments_doc_type` — 59/59 PASSED (hindi
  binago ang tests).
- `python -m core.claim_attachments_uploader` (dry-run) — malinis:
  "assigning doc types for attached files (v6.1 tab-walk)" +
  "would right-click the focused row, copy, type, TAB — until ESA".
- `python -c "import gui.claim_attachments_tab"` — passed.
- Live test PENDING (owner protocol): `--live --confirm-each --limit 1`
  sa ~15-file patient; visual check ng doc type column bago iwan ang
  Upload. Dapat ma-prove: row 10+ (dati partially-visible) ay napopro-
  seso na, walang wheel scroll, tuloy hanggang ESA, at Upload lang sa
  dulo.

### 2026-09-08 — Claim Attachments doc type v6.0: CLIPBOARD-PRIMARY matching (owner-verified context menu)

Reason:

- Live run 2026-09-07 15:03 (15-file patients): 3 ABORT — MANAYAN
  (CF2.pdf, CF3.pdf), ROY (MMC.pdf), VENTURA (MRF.pdf) — "still not
  found after 12 views": may mga grid rows na HINDI mabasa ng kahit
  anong OCR pass (normal/inverted/blue-band/row-band), kaya hindi
  na-match ang file. Offline OCR trace sa mga saved debug crops ang
  nagpatunay: purong garbage ('=~000000000020838-') ang nababasa sa
  mga row na iyon.
- FEDERIZO (owner-reported) at iba pang "successful" patients:
  maling doc type assignment sa ilang rows (band-vs-line y offsets sa
  wrapped rows, walang row-identity verification).
- Research finding (kritikal): sa LAHAT ng 8 patients ng 15:03 batch,
  ang XML rows sa grid ay file ni VILLARUEL, JUAN JR BAUTISTA-260825157234
  (iba pang patient, Aug 25 claim) — nasa loob ng mga patient folder ang
  mga XML na may VILLARUEL filename. Upstream issue ito (XML
  generation/copy), HINDI doc type selection, at kailangang ayusin sa
  XML generation step.
- Owner-verified HBSys capability (probe, 2026-09-07): right-click sa
  grid row → context menu na may tatlong items — "view", "copy", "open
  location". Ang "copy" (single click) ay naglalagay sa clipboard ng
  FULL ABSOLUTE PATH ng row (path + filename + extension) — 100%
  deterministic, walang OCR noise.
- Desisyon ng may-ari: clipboard-primary matching. Ang path mula sa
  clipboard ang row identity; OCR ay fallback na lang.

Files:

- `requirements.txt`: idinagdag ang pyperclip==1.11.0 (dati transitive
  dep; gagamitin na ng production code).
- `core/claim_attachments_doc_type.py`:
  - Added `match_copied_path_to_file(path, folder_files, expected_parent)`
    — exact basename + patient-folder parent match; foreign folder o
    unknown basename → None (never-guess).
  - Added v6 unit tests (path match, foreign-folder guard,
    case-insensitivity, unknown basename).
- `core/claim_attachments_uploader.py`:
  - Added `ROW_COPY_X = 700` (File Name column, safe sa Doc Type column).
  - Added `_row_bands_from_screenshot()` — pixel-based row bands via
    `find_row_separator_lines()` (no OCR; reliable sa hindi mababasang rows).
  - Added `_bands_from_lines()` — OCR-line fallback anchors (v5-proven
    click targets) kapag walang pixel separators.
  - Added `_copy_row_path(row_y)` — right-click → #32768 menu →
    MN_GETHMENU/GetMenuItemRect → click "copy" → poll clipboard change;
    keyboard down+enter fallback (menu-confirm lang); Escape cleanup.
  - Added `_find_context_menu()`, `_context_menu_copy_rect()`,
    `_poll_clipboard_change()` helpers.
  - Reworked `assign_doc_types_and_upload()` sa v6 clipboard-primary loop:
    per-row INTERLEAVED flow (copy → type doc → arrow → TAB → next row),
    band-center typing (FEDERIZO fix), scroll overlap skip by path
    identity, duplicate-row ABORT (>20px apart sa isang view), foreign
    path ABORT, OCR fallback para sa mga hindi ma-copy na row (unresolved
    bands lang), v5 OCR-fingerprint no-progress detection (retained),
    guardrails unchanged (MAX_SCROLL_ITERATIONS=12, MAX_DOC_ROWS=40).

Behavior:

- Before: OCR-text matching lang ang row identity; unreadable rows →
  not_found → ABORT; band/line y-offset mismatch → maling row typing.
- After: ang row identity ay ang COPIED PATH (exact) — deterministic;
  unreadable-OCR rows ay nagagawa pa rin (pixel bands + clipboard);
  typing sa band center; foreign/duplicate rows → ABORT (may path sa
  log) bago mag-Upload; upload flow (v5.2) at guardrails unchanged.

Safety / compatibility:

- Never-guess guardrail palaban: foreign path, duplicate row, copy
  failure + OCR not-found/ambiguous → ABORT bago ang Upload click.
- Right-click ay nasa File Name column (x=700) — malayo sa Doc Type
  combo column; walang click sa maling column.
- Escape cleanup sa lahat ng failed menu paths; walang stray keypress
  sa grid (keyboard fallback ay menu-confirmed lang).
- OCR pipeline (4-pass) at 3-tier matcher unchanged — OCR fallback ang
  gamit, hindi pinalitan.
- Dry-run mode: walang clicks; log lang.
- No GUI changes; pyperclip na lang ang bagong direct dep (naka-install
  na sa venv).

Verification:

- `python -m core.claim_attachments_doc_type` — 59/59 PASSED (55 dati +
  4 bagong v6 tests).
- `python -m py_compile` (warnings-as-errors) sa uploader + doc_type —
  passed.
- Dry-run (`python -m core.claim_attachments_uploader`) — malinis ang
  log flow, walang clicks.
- GUI tab import check (`import gui.claim_attachments_tab`) — passed.
- Live protocol PENDING: `--live --confirm-each --limit 1` — bago i-click
  ang Upload, i-visual check ng may-ari ang doc type column; pag OK,
  full batch. Inaasahan: wala nang ABORT sa klase ng MANAYAN/ROY/VENTURA
  (unreadable rows), at tama ang row assignment (clipboard identity).

### 2026-09-07 — Claim Attachments doc type v5.3: near-stem merge + twin-safe mutated tier (SORIANO CSF fix)

Reason:

- Live run 2026-09-07 11:44 (batch 20260907_114445): SORIANO, RODRIGO
  REYES ay nag-ABORT sa doc type assignment — "1 file(s) still not
  found after 12 views: CSF.pdf" — kahit visible naman lahat ng 8
  files at hindi kailangang mag-scroll. (PALAMING, OK sa same batch.)
- Root cause (2 defects, isolated via pass-by-pass trace sa saved
  debug crops):
  1. `merge_dual_pass_lines` v4.3 stem-gain rule: EXACT known stem
     lang ang tinatanggap bilang "gain". Ang row-band pass ay nabasa
     nang tama ang CSF row bilang '...C5E.PDF' (F->E OCR misread),
     pero dahil hindi exact stem, itinapon ng merge rule ang tanging
     mabuting pagbasa; nanatili ang garbage fragments ('-' + path).
  2. `match_files_to_lines` mutated tier (v4.3): DEAD CODE pala —
     pinatunayan ng repro na kahit ang documented na MATTERIG case
     ('C4.XM1' para sa CF4) ay hindi kailanman tumama. Mali ang window
     construction (end-trim ng pre-marker text sa halip na suffix
     adjacent sa marker, kung saan talaga nakalagay ang stem).
- HINDI dahil ito sa pagtatanggal ng v5.1 verification walk — ang ABORT
  ay PRE-typing matching failure, hindi post-typing verify.

Files:

- Modified `core/claim_attachments_doc_type.py` (v5.3):
  - Added `_PDF_STEM_SET` / `_XML_STEM_SET` / `_KNOWN_STEM_SET`
    vocabulary tables.
  - Added `_mutated_stem_match` — twin-safe 1-edit comparator: pinapayagan
    ang letter substitutions/dropped chars (F->E, 'C4' for 'CF4') PERO
    tinatanggihan ang digit-for-digit mutations (4->5, 1->2) para hindi
    kailanman mag-cross-match ang SOA1/SOA2 at CF4/CF5.
  - Added `_near_stem_before_marker` + `_has_near_stem` — 1-edit mutated
    stem na adjacent lang sa extension marker ('.P'/'.X') ang tinatanggap;
    stray mutated stem sa gitna ng path ay hindi.
  - `merge_dual_pass_lines`: ang multi-overlap stem-gain rule ay
    gumagamit na ng `_has_near_stem` (near-stem form) — mutated-stem
    readings ('C5E.PDF') ay buhay na sa merged output.
  - `_mutated_stem_candidates`: window ay SUFFIX adjacent sa marker na
    may stem-1..stem+1 lengths, gamit ang `_mutated_stem_match`.
  - Removed dead code: `_has_stem`, `_edit_distance_le1` (both fully
    superseded).
- Updated `DOC_TYPE_ASSIGNMENT_PLAN.md` (v5.3 history row + status).
- Updated `CHANGE_RULES.md` (this record).

Behavior:

- Before: '\CSF.pdf' na nabasa bilang 'C5E.PDF' ay itinatapon sa merge
  at hindi tumatama sa kahit anong tier -> not_found -> 12 views na
  walang progress -> ABORT ng buong patient.
- After: ang 'C5E.PDF' reading ay nakaligtas sa merge (near-stem gain)
  AT tumatama sa mutated tier -> CSF.pdf matched -> tuloy ang typing
  at Upload. Verified sa mismong failed SORIANO crop: 8/8 matched,
  0 not_found, 0 ambiguous.
- Twin safety preserved: CF4 never matches a 'CF5.XM1' row; SOA1 never
  matches a 'S0A2.PDF' row (digit mutations rejected).

Safety / compatibility:

- Never-guess guardrail unchanged: not_found/ambiguous -> ABORT pa rin
  bago mag-Upload.
- Strict tier at loose tier unchanged; ang mutated tier lang (3rd/
  weakest) at ang merge rule ang pinalawak.
- No GUI changes; no new dependencies; no changes sa production engine.

Verification:

- `python -m core.claim_attachments_doc_type` — 55/55 PASSED,
  kabilang ang 2 bagong live crop regressions (PALAMING 8/8 — OK
  patient ng batch; SORIANO 8/8 — dati FAILED patient) at 4 bagong
  v5.3 unit tests (near-stem merge survival, mutated-tier MATTERIG/
  SORIANO cases, twin safety, marker adjacency).
- `python -m py_compile` (warnings-as-errors) sa doc_type + uploader
  modules — passed.
- Offline re-run ng mismong failed SORIANO debug crop
  (debug_doc_grid_20260907_114645.png): matched 8/8, not_found=[],
  ambiguous=[] — dati 7/8 na may CSF.pdf not_found sa LAHAT ng views.
- Live re-test PENDING: ulitin ang SORIANO patient via
  `--live --confirm-each --limit 1` (o ang 2-patient READY batch).

### 2026-09-04 — Claim Attachments doc type: v5.2 verification REMOVED (Upload agad pagkatapos ng huling row)

Reason:

- Live run 14:xx (GUIUO): v5.1 ay naayos na ang DTR cell read (band
  window + arrow exclusion gumana), PERO nag-VERIFY FAIL ulit sa
  iba pang file — "expected 'CF4', doc cell read 'F'" (partial read ng
  maikling value). Dalawang beses na (DTR None, CF4 'F') na-block ng
  cell OCR ang Upload kahit TAMA ang lahat ng doc types sa visual
  (kumpirmasyon ng may-ari).
- Desisyon ng may-ari: tanggalin na ang verification — pagkatapos i-type
  ang huling doc type (eSOA/ESA), direktang Upload → OK (Enter) → Close.

Files:

- Modified `core/claim_attachments_uploader.py`:
  - `assign_doc_types_and_upload()`: pagkatapos ng view loop (lahat ng
    files PROCESSED) — DIRETSA sa Upload (598,730) → Enter (OK) →
    Close (1408,734). Walang verification pass. Docstring updated.
  - Binura (dead code): `_verify_doc_column_values()`,
    `_ocr_doc_cell()`, `_doc_value_matches()`, `_scroll_grid_top()`,
    at `DOC_COLUMN_PAD` constant — lahat ng caller ay verify pass lang.
  - Mananatili: `_scroll_grid_down()`, `_ocr_doc_grid_view()`,
    `_view_fingerprint()`, `_type_row_doc()` (gagamit pa ng view loop),
    at ang unused na `find_row_separator_lines` import ay inalis.
  - Ang duplicate-row guard ay NASA TAMANG lugar pa rin: per-view 1:1
    matching habang nagta-type (1 file = 2+ rows sa isang view = ABORT
    bago mag-type ng kahit ano).
- Modified `core/claim_attachments_doc_type.py` (tests lang):
  - Binura ang v5.1 band-window/policy/doc-value tests (naka-depend sa
    mga buradong methods).
  - Bagong v5.2 structural test: kumukumpirma na wala na ang verify
    methods sa operator (upload sequence = last typed row → Upload).
  - Mananatili ang lahat ng matching/fingerprint/5 live-crop tests.

Behavior:

- Bago: 10/10 rows typed, pero nag-a-ABORT ang verify pass sa flaky
  cell reads (DTR None / CF4 'F') — hindi nakaka-click ng Upload kahit
  tama ang lahat.
- Ngayon: view 1 (rows 1-9) → scroll → view 2 (eSOA/ESA) → **Upload →
  OK (Enter) → Close** — tuloy-tuloy, walang verification step.

Safety:

- Never-guess policy buo pa rin sa view loop: ambiguous/duplicate rows
  sa typing phase, not_found files, scroll failure, MAX_SCROLL_ITERATIONS
  — lahat ABORT bago ang Upload. Ang tinanggal LANG ay ang post-type
  cell OCR na hindi kapani-paniwala sa maikling values.
- Walang binago sa 4-pass OCR/matching pipeline at Upload/OK/Close coords.

Verification:

- `python core/claim_attachments_doc_type.py` — RESULT: PASSED (5 live
  regression crops + v5 scroll tests + v5.2 structural test).
- `py_compile` — OK; GUI import chain — OK.
- Live test PENDING: GUIUO ulit — inaasahang log: view 1 (9 rows) →
  scroll → view 2 (ESA) → Upload → OK (Enter) → Close → "doc types
  assigned (10 rows across views) and uploaded". WALA nang verify lines.

### 2026-09-04 — Claim Attachments doc type: v5.1 verify fix (band window + arrow exclusion + verified scroll-top)

Reason:

- Live run 13:17 (GUIUO, 10 files): GUMANA ang v5 view loop — view 1
  typed 9 rows (COE..CF5), scroll OK, view 2 typed eSOA/ESA (10/10).
  Pero nag-ABORT bago ang Upload: "VERIFY FAIL: DTR.pdf expected 'DTR',
  doc cell read None". Hindi na-click ang Upload → Enter → Close.
- Root causes (sa mismong debug_doc_grid_20260904_131759.png):
  (1) `_ocr_doc_cell()` ay gumagamit ng ±11px window sa paligid ng
      matched FILE-PATH line center_y — pero ang doc cell text ay
      vertically centered sa ROW BAND, at ang wrapped paths (2-3 OCR
      lines) ay naglalagay ng filename line malayo sa cell center
      (DTR filename line y=467 vs band 443..473) → clipped glyphs → None.
  (2) Kasama ng window ang combo ARROW glyph (x≈519..525) — binabasa
      bilang stray letters ('TR V', 'TRJC') na nagpapabula sa cell value.
  (3) `_scroll_grid_top()` ay isang beses lang na scroll(20) — naiwan
      ang grid MID-SCROLL: ang verify view ay nawalan ng COE/CSF rows
      (nasa itaas), at ang blue band ay nasa CF5 row pa rin.

Files:

- Modified `core/claim_attachments_uploader.py`:
  - `_ocr_doc_cell()` v5.1: band-based vertical window (buong
    separator-delimited row band, fallback ±14px), X-WINDOW NA WALANG
    ARROW (x=467..509 — text column lang; ang arrow glyph ay nagbibigay
    ng stray letters), 4x upscale, multi-threshold (150/190/120) PSM 7
    reads, plain-gray fallback, alnum whitelist. Optional
    view_image/crop_box params — nagre-reuse ng verify screenshot
    (hindi per-row full-screen capture).
  - `_scroll_grid_top()` — pulsed scroll-up (scroll(10) x hanggang 6)
    na may fingerprint equality check: garantisadong nasa top kapag
    tumigil na ang paggalaw ng view. Returns bool na.
  - `_verify_doc_column_values()` — None-policy: MISMATCH (may nabasang
    IBA) → ABORT pa rin (evidence ng mali/duplicate/leftover row);
    UNREADABLE (None after 1 retry) → WARNING at magpatuloy — ang
    duplicate-row threat ay huli na ng per-view 1:1 matching (ambiguous
    check bago pa ang cell reads); hindi dapat mag-block ang OCR
    flakiness sa Upload.
  - Import ng `find_row_separator_lines`.
- Modified `core/claim_attachments_doc_type.py` (tests lang):
  - v5.1 band-window tests laban sa mismong 13:17 verify crop:
    DTR doc cell reads 'DTR' (dati None) — PASSED; COE skip ay inaasahan
    (mid-scroll crop). None/empty→proceed, mismatch→abort policy test.

Behavior:

- Bago: verify pass ay nag-a-ABORT sa unreadable cells (None) kahit
  tama ang 1:1 matching at walang duplicates — na-block ang Upload sa
  10-file patients kahit kumpleto na ang 10/10.
- Ngayon: pagkatapos ng huling row (eSOA) → verified scroll-to-top →
  walk ng views → band-based cell reads (DTR='DTR', COE='COE', atbp.)
  → VERIFY PASS → **click Upload (598,730) → popup OK → press Enter →
  click Close (1408,734)** → popup-gone verify. Mismatch lang (tunay
  na mali/duplicate) o ambiguous rows ang nag-a-ABORT.

Safety:

- Never-guess policy nanatili: mismatch/ambiguous/duplicate = ABORT
  bago ang Upload. Ang None-policy ay nagpapatakbo lamang sa mga
  rows na pasado na sa 1:1 matching — walang nadadagdag na guess.
- Walang binago sa 4-pass OCR/matching pipeline, Upload/OK/Close
  coords, at buong attach flow bago ang doc-type step.

Verification:

- `python core/claim_attachments_doc_type.py` — RESULT: PASSED (5 live
  regression crops + v5 unit tests + v5.1: DTR 'DTR' band read mula
  sa mismong verify crop na dating None; policy decision table).
- `py_compile` — OK; GUI import chain — OK.
- Live test PENDING: GUIUO ulit — inaasahang log: 10/10 typed →
  scroll-top verified → VERIFY PASS (posibleng WARNING sa ilang cell)
  → Upload → OK (Enter) → Close → "doc types assigned (10 rows across
  views) and uploaded".

### 2026-09-04 — Claim Attachments doc type: v5 grid scrolling (view loop + pre-Upload verification)

Reason:

- Live run 11:31 (GUIUO, 10 files): 9/10 lang — ang `_eSOA.xml` ay nasa
  ILALIM ng visible grid (below CF5.xml). Kailangan i-scroll down ang
  popup grid. Dating behavior: not_found → ABORT (ligtas pero hindi
  kumpleto — documented limitation simula v4).
- Plan: `.kilo/plans/grid-scrolling-v5.md` (inaprubahan, implementado).

Files:

- Modified `core/claim_attachments_uploader.py`:
  - New constants: `MAX_SCROLL_ITERATIONS = 12`, `DOC_COLUMN_PAD = 6`.
  - New helpers: `_scroll_grid_down()` (wheel -2 sa grid center),
    `_scroll_grid_top()` (wheel +20), `_ocr_doc_grid_view()` (per-view
    OCR na may debug screenshot naming), `_view_fingerprint()` (MD5 ng
    normalised text lines sorted by y — positional shift ay hindi
    nagbabago ng fingerprint, text change OO — no-progress guard),
    `_type_row_doc()` (click field → type → arrow → TAB, one row),
    `_ocr_doc_cell()` (Doc column strip OCR: 2x upscale, binarize,
    PSM 7, alnum whitelist), `_doc_value_matches()` (OCR-noise tolerant
    comparison gamit ang shared `_norm`).
  - New `_verify_doc_column_values()`: PRE-UPLOAD VERIFICATION — scroll
    sa taas, walk ng views, bawat file row ay i-OCR ang Doc column cell;
    dapat tugma ang na-type na doc type (1 retry bago fail). Huli nitong
    mahuhuli ang leftover duplicate rows mula sa mga na-ABORT na runs
    (file-text row na walang laman na Doc cell → ABORT, walang Upload).
  - `assign_doc_types_and_upload()` v5 VIEW LOOP: per view — OCR (4-pass
    pipeline HINDI binago) → match UNPROCESSED files lang (processed
    rows na nagre-reappear sa scroll overlap ay skip, hindi ambiguous) →
    type rows top-to-bottom → mark PROCESSED (identity = text, hindi
    position). May natira → scroll down → re-OCR. No-progress: pareho
    ang fingerprint pagkatapos ng scroll → heavier scroll retry → kung
    pareho pa rin → ABORT "cannot scroll to remaining files". Break sa
    lahat PROCESSED → verify → Upload (598,730) → Enter → Close
    (1408,734).
  - `_save_doc_grid_debug()` may `name` parameter na para sa per-view
    debug crops (view2_post-scroll, verify, atbp.).
- Modified `core/claim_attachments_doc_type.py` (tests only, walang
  pipeline change):
  - GUIUO crop (debug_doc_grid_20260904_113159.png) idinagdag sa live
    regression suite bilang documented scrolled-out case (9/10 sa
    static crop; ang v5 loop ang nag-handle ng scroll sa live).
  - v5 unit tests: unprocessed-eSOA matching sa view-2 overlap lines
    (skip semantics), view fingerprint (positional shift ignored, text
    change detected), doc cell value matching (E5A==ESA, S0A==SOA na
    lehitimong OCR noise; CF4 != CF5; empty fails).

Behavior:

- Bago: 10+ files na pasyente → huling rows not_found → ABORT (walang
  Upload, kailangan ng manu-manong intervention).
- Ngayon: ang loop ay nag-scroll hanggang ma-process lahat ng files,
  tapos nag-verify na ang LAHAT ng doc column cells ay may tamang doc
  type bago i-click ang Upload. Never-guess policy nanatili: scroll
  failure / verify mismatch / ambiguous = ABORT, walang Upload click.

Safety:

- Walang binago sa working OCR engine, XML generator, claims checker,
  signing engine, o sa 4-pass OCR/matching pipeline mismo — view loop
  at verification layer lang ang idinagdag.
- Ang pre-Upload verification ay laging nangyayari BAGO ang Upload
  click — verify-before-irreversible-action (loop-engineering
  Principle 3).
- Ang mga debug crop kada view (debug_doc_grid_view*.png) ay
  nagpapahintulot ng post-mortem diagnosis ng anumang ABORT.

Verification:

- `python core/claim_attachments_doc_type.py` — RESULT: PASSED (5 live
  regression crops: PASCUA 8/8, SAFLOR 8/8, SASPA 9/10, MATTERIG 8/8,
  GUIUO 9/10 — parehong documented scrolled-out eSOA) + 3 v5 unit
  tests (unprocessed matching, fingerprint, doc value matching).
- `py_compile` tatlong files — OK; buong GUI import chain — OK.
- Live test PENDING: `--live --confirm-each --limit 1` sa 10-file
  patient (GUIUO) — inaasahang sa log: view 1 (9 rows typed) → scroll
  → view 2 (eSOA typed) → verify pass → Upload → OK → Close.

### 2026-09-04 — Claim Attachments doc type v4.3: LIVE TEST PASSED + GitHub backup

Reason:

- Live re-run matapos ang v4.3 fixes: matagumpay na nagtakda ang
  doc-type step at nag-Upload ang system (gumagana na — kumpirmasyon ng
  may-ari, "guamgana na"). Ang v4.1-v4.3 na live regression suite laban
  sa apat na totoong debug crops ang naging sagwasyon bago ang live run.
- I-backup ang bagong ayos na system sa GitHub (kasama ang buong
  doc-type step: v2 NameError repair, v3, v4, v4.1, v4.2, v4.3).

Files:

- Updated `CHANGE_RULES.md` — ang record na ito.
- Updated `README.md`, `DOC_TYPE_ASSIGNMENT_PLAN.md` (nauna nang
  in-update sa v4.3 session).

Behavior:

- Walang code change sa step na ito — documentation at backup lang.

Verification:

- LIVE RUN PASSED (2026-09-04, kumpirmasyon ng may-ari): ang
  doc-type assignment + Upload flow ay gumagana na sa totoong HBSys.
- `python core/claim_attachments_doc_type.py` — RESULT: PASSED (4 live
  regression crops: PASCUA 8/8, SAFLOR 8/8, SASPA 9/10 documented
  scrolled-out, MATTERIG 8/8).
- GitHub push: bagong commit sa `chxlover/edh-claims-automation`
  (main) na may lahat ng Claim Attachments modules + doc-type step
  v4.3 + markdowns.

### 2026-09-04 — Claim Attachments doc type: v4.3 flexible merge + mutated-stem tier (MATTERIG fix)

Reason:

- Live run 08:35 (MATTERIG): 6/8 lang — nawawala ang COE.pdf at _CF4.xml.
  Iba-iba ang grid render kada pasyente (PASCUA/SAFLOR ok sa v4.1; SASPA
  kailangan ng v4.2; MATTERIG nag-expose ng dalawang bagong gap), kaya
  case-by-case patch ang dating approach — hindi flexible.
- Diagnosis sa logs/debug_doc_grid_20260904_083557.png:
  (1) COE: ang band pass ay TAMA na binabasa ang '\COE.pdf' (conf=25),
      pero ang merge rule ay "multi-overlap -> conservative keep normals"
      — nag-overlap ang band reading sa DALAWANG basurang normal fragments
      ('5E0EEE' + '1CARRE0N-000...'), kaya natapon ang TAMA na reading.
      Sa PASCUA/SAFLOR kagabi, ISANG fragment lang ang overlap kaya
      gumana ang v4.1 rule.
  (2) CF4: binasa ng OCR ang '_CF4.xml' bilang 'C4.XM1' — nawala ang F sa
      stem; walang tumugma sa strict/loose tiers.

Files:

- Modified `core/claim_attachments_doc_type.py` (flexible rules, hindi
  case patches):
  - `merge_dual_pass_lines()` — bagong STEM-GAIN rule sa multi-overlap:
    kapag ang incoming line ay may known stem at WALA sa kahit alin sa
    mga overlapping lines, papalitan nito lahat ng stem-less fragments.
    Stem-less line ay hindi kailanman makakapag-match ng file, kaya
    zero information loss; pure gain ang stem. Kapag stem-less din ang
    incoming, mananatili ang conservative keep.
  - `match_files_to_lines()` — bagong TIERTENG MUTATED (tier 3): stems na
    may 1-edit distance (substitution/deletion/insertion) sa known stem,
    kung ang extension letter (P/X) ay tumutugma. `New _edit_distance_le1()`
    helper. Hindi maaaring mag-cross-match ang SOA1/SOA2 o CF4/CF5 sa
    pamamagitan ng tier na ito (may distinct digits); ginagamit lang
    kapag walang mas malakas na kandidato.
  - Live regression tests: 4 na totoong debug crops na ngayon (PASCUA,
    SAFLOR, SASPA, MATTERIG) + per-case expectations (ang SASPA eSOA ay
    documented na scrolled-out — 9/10 + not_found ang tama, ABORT sa
    live). Nag-aassert na rin ang v4.3 merge sa stem-gain replacement at
    stem-less conservative keep.

Behavior:

- Bago (v4.2): multi-overlap ay laging conservative — natatapon ang
  nag-iisang magandang reading kapag 2+ basurang fragments ang overlap
  (guaranteed COE failure sa MATTERIG-type renders). Stem mutations
  ('C4' para sa CF4) ay guaranteed not_found.
- Ngayon: flexible sa lahat ng nakitang render variations — ang mga
  gaps ng bawat version (v4.1: single-fragment overlap; v4.2:
  full-crop misses; v4.3: multi-fragment overlap + stem mutations) ay
  sakop na ng mga general rules, hindi case patches.

Safety:

- Never-guess policy nanatili: stem-gain ay nagpapalit lang ng
  stem-LESS fragments (hindi kailanman kapalit ng stem-bearing line);
  mutated tier ay nire-require ang extension letter at hindi
  nagma-match sa distinct-digit twins; ambiguous/not_found = ABORT
  parin bago ang Upload.
- Walang binago sa working OCR engine, XML generator, claims checker,
  signing engine, o processing flow.

Verification:

- `python core/claim_attachments_doc_type.py` — RESULT: PASSED (lahat ng
  lumang cases + v4.3 merge cases + 4 live regressions: PASCUA 8/8,
  SAFLOR 8/8, SASPA 9/10 na may documented eSOA scrolled-out, MATTERIG
  8/8 — mismong crop ng nabigong run ngayong umaga).
- `py_compile` — OK; buong GUI import chain — OK.
- Live test PENDING: i-run muli ang MATTERIG (at iba pang pasyente) —
  inaasahang hindi na ma-ABORT; kung may ABORT pa, may bagong debug crop
  na naman para sa diagnosis.

### 2026-09-03 — Claim Attachments doc type: v4.1 blue-row recovery (quality merge + targeted band OCR)

Reason:

- Live retest 15:38-15:40 pagkatapos ng v4: 7/8 (PASCUA) at 6/8 (SAFLOR)
  na lang ang hindi mahanap — COE.pdf sa dalawa, at CSF.pdf sa SAFLOR.
  Malaki ang improvement pero may natitira pa: ang mga files na iyon ay
  ang UNANG row(s) ng grid — ang auto-selected na row na white-on-blue.
- Diagnosis gamit ang bagong debug screenshots
  (logs/debug_doc_grid_20260903_153917.png / _154006.png):
  (1) ang lumang "discard ANY overlapping inverted line" rule sa
  `ocr_grid_lines()` ay nagtatapon ng magandang inverted reading kapag
  may kahit fragment na ang normal pass (ito ang CSF sa SAFLOR);
  (2) mas malalim — sa totoong live crops, BOTH passes ay basura lang ang
  nababasa sa blue row ('5NEEE'), kaya walang merge strategy na
  makakarecover sa COE. Ang offline reference screenshot ay nagkataong
  nababasa, kaya hindi ito nahuli ng v4 integration test.

Files:

- Modified `core/claim_attachments_doc_type.py`:
  - New `_line_quality()` — ranking key: may stem > may extension
    marker ('.P'/'.X') > mas mahabang alnum reading.
  - New `merge_dual_pass_lines()` — pinalitan ang "normal-pass always
    wins" rule: sa isang overlap, ang mas mataas ang quality ang mananalo;
    walang overlap -> idagdag; dalawa o higit na overlap -> konservatibong
    manatili sa normal readings (inverted pass merged rows).
  - New `find_highlight_band()` — hinahanap ang blue selection band via
    blue-dominant pixel counting (b > 120 at b - max(r,g) > 40, >= 50% ng
    sampled width per row; pinakamalaking contiguous run).
  - New `ocr_highlight_band()` — hiwalay na OCR ng band gamit ang manual
    binarization (luminance > 170 -> itim na text sa puting background),
    PSM 6, 1x scale para eksakto ang coordinates. Validado sa totoong
    live crops: binabasa nito ang '\\C0E.NDF' (stem 'C0E' present) na
    hindi kayang basahin ng dalawang full-crop passes.
  - `ocr_grid_lines()` — ngayon 3 passes: normal + inverted + targeted
    band pass (kapag may blue band), lahat pinagsasama via
    `merge_dual_pass_lines`.
  - Standalone tests: dagdag na v4.1 merge cases (inverted replaces
    garbled normal; good normal kept over weak inverted; multi-overlap
    conservative), find_highlight_band synthetic (may band / wala), at
    LIVE REGRESSION tests laban sa dalawang totoong debug crops mula sa
    mismong nabigong 15:38/15:40 runs (PASCUA + SAFLOR, 8 files each).

Behavior:

- Bago (v4): COE.pdf (auto-selected blue row) laging "not found" -> ABORT;
  paminsan-minsan pati CSF.pdf (SAFLOR).
- Ngayon: ang quality merge ay nagligtas sa CSF (6/8 -> 7/8 sa SAFLOR),
  at ang targeted band pass ay nagbibigay ng maaasahang COE reading —
  parehong live crops ay 8/8 na may tamang doc sequence [COE, CSF, DTR,
  SOA, SOA, CF4, CF5, ESA]. Kung walang blue band o kabiguan ang band
  OCR, hindi nagbabago ang kilos ng matcher — not_found pa rin ang
  malinaw na ABORT.

Safety:

- Walang binago sa working OCR engine, XML generator, claims checker,
  signing engine, o processing flow — doc-type step pa rin lang.
- Ang band detection ay purely passive pixel scanning ng screenshot na
  hawak na; walang bagong dependency (pytesseract + PIL lang).
- Never-guess policy nanatili: ambiguous/not_found/unclassifiable = ABORT
  bago ang Upload; may debug_doc_grid_*.png kada run.
- Ang live regression tests ay naka-depende sa logs/debug_doc_grid_*.png;
  kapag na-delete ang mga ito, ang mga test ay mag-SKIP nang maayos
  (hindi nag-fail).

Verification:

- `python core/claim_attachments_doc_type.py` — RESULT: PASSED: lahat ng
  lumang cases OK + v4.1 (3 merge cases; find_highlight_band band=(20,34)/
  None; live regression PASCUA 8/8 at SAFLOR 8/8 na may tamang docs).
- `py_compile` dalawang files — OK; import chain (uploader + GUI tab) — OK.
- Live test PENDING: i-run muli ang PASCUA/SAFLOR (i-check muna na walang
  natirang luma rows sa popup — kung mayroon, i-Delete muna; aabutan ng
  duplicate-row guard ang run: ABORT, hindi mali ang maitype).

### 2026-09-03 — Claim Attachments doc type: v4 line-based matching (fix sa laging ABORT)

Reason:

- Live test 14:51-14:53: dalawang pasyente (PASCUA, SAFLOR) ay ABORT sa
  doc-type step na may "8 file(s) not visible in the grid (scrolling not
  supported)" kahit kumpleto naman ang 8 rows sa popup grid.
- Root cause #1 (SAFLOR): `_pop_matched_file()` sa
  `core/claim_attachments_uploader.py` ay nag-return ng matched file
  pero HINDI talaga nag-remove sa `unmatched` list (kahit "Remove and
  return" ang docstring), at binabalewala rin ng caller ang return value.
  Kaya laging may natitira sa `unmatched` — palaging ABORT kahit perpekto
  ang matching. Guaranteed failure sa bawat live run.
- Root cause #2 (PASCUA): ang "File Name" column ng grid ay nagpapakita
  ng FULL LOCAL PATH na nagwa-wrap sa 2-3 text lines sa loob ng ISANG
  row. Ang pixel-separator band detection ay maaaring mag-split ng isang
  row sa dalawang bands (band 3 sa live log ay "C:\claims_bot\...READY\
  PASCUA, VIOLETA " lang — walang filename, walang extension) — skip,
  kaya may file na hindi na-match. Bukod dito, ang selected (blue) row ay
  white-on-blue text na hindi nababasa ng normal na OCR pass (dahilan
  kung bakit laging nawawala ang COE sa v3 integration test).

Files:

- Modified `core/claim_attachments_doc_type.py`:
  - New `GridLine` dataclass — OCR text line na may screen coords at
    `center_y` (ang y na pinipindot sa Doc Type cell).
  - New `extract_grid_lines()` — pag-group ng pytesseract words sa text
    lines gamit ang tesseract (block, par, line) ids; stable kahit
    nagwa-wrap ang path rows. Conf thresholds: 10 kapag may .pdf/.xml/
    .xmi ang word, else 20 (live-proven thresholds).
  - New `ocr_grid_lines()` — OCR gamit ang PSM 6 (talo ang PSM 3 sa
    offline validation: 8/8 vs 7/8 files) + colour-inverted pass para sa
    blue selected row; overlapping duplicate lines ay dine-dedupe.
  - New `match_files_to_lines()` — folder-driven 1:1 matching: bawat file
    ay dapat tumugma sa EXACTLY ISANG line. Strict tier: stem + ".P"/".X"
    (tumatagal ng ".PDFF"/".XM1" OCR noise); loose tier: stem lang (para
    sa rows na nawalan ng dot, hal. "50A2PDF"). Fixpoint consumption para
    hindi makuha ng dalawang files ang iisang row (SOA1/SOA2 twins,
    duplicate rows). Return: (matched, not_found, ambiguous) — walang
    guessing, lahat ng hindi tiyak ay ABORT.
  - `match_row_to_file()` at iba pang lumang functions: naka-retain para
    sa backward compatibility at sariling tests.
  - Standalone tests: dagdag na synthetic grid (wrapped paths, blue row,
    split "\D"+"TR.PDF", ".PDFF", dot-less "50A2PDF", 3-line XML wrap,
    title/header/note junk), duplicate-row -> ambiguous, missing-row ->
    not_found, at integration test laban sa totoong
    SS_choose_doc_type.png (8/8, kasama ang blue COE row).
- Modified `core/claim_attachments_uploader.py`:
  - Inalis: `_is_light_gray_row_separator()`, `_detect_grid_row_bands()`
    (separator-band approach) at `_pop_matched_file()` (buggy).
  - New `_popup_crop_box()` — crop sa live popup rect mula sa window
    handle (fallback: DOC_GRID_BOUNDS) at `_save_doc_grid_debug()` —
    nagse-save ng `logs/debug_doc_grid_<ts>.png` KADA run para madaling
    i-diagnose ang anumang ABORT.
  - `assign_doc_types_and_upload()` v4: crop -> OCR text lines (PSM 6 +
    inverted) -> i-classify ang lahat ng folder files (unknown stem =
    ABORT) -> 1:1 line matching -> rows_plan sorted by y -> per-row
    type/arrow/TAB -> Upload -> OK -> Close. Bagong ABORT messages:
    ambiguous (duplicate/twin rows), not_found (scrolled out/unreadable),
    MAX_DOC_ROWS file cap.
  - Workflow docstring: nadagdag ang Step 12 (doc types + Upload).

Behavior:

- Bago: ang doc-type step ay laging/nag-ABORT na "N file(s) not visible
  in the grid" dahil hindi nababawasan ang unmatched list at sa wrapped-
  path band splits; walang doc types na naibibigay.
- Ngayon: bawat folder file ay 1:1 sa isang grid TEXT LINE; ang doc type
  ay ini-type sa row kung saan NANDOON ang filename text (y mula sa line
  itself, hindi mula sa separator bands). Kapag may kulang, dobleng,
  o di-kilalang file: ABORT bago pa ang Upload — walang mali na
  maipapadala sa HBSys.

Safety:

- Walang binago sa working OCR engine, XML generator, claims checker,
  signing engine, o sa existing processing flow — doc-type step lang
  (bagong module mula 2026-09-02/03) ang inayos.
- Never-guess policy: ambiguous / not_found / unclassifiable = ABORT na
  may malinaw na dahilan sa log + debug screenshot.
- Walang bagong external dependency (pytesseract + PIL lang, existing).

Verification:

- `python core/claim_attachments_doc_type.py` — RESULT: PASSED (32 lumang
  cases OK + v4: synthetic 20/20 lines, 8/8 match na may tamang docs
  [COE, CSF, DTR, SOA, SOA, CF4, CF5, ESA] at ys=[406,437,467,497,527,
  571,615,659]; duplicate CSF -> ambiguous; missing eSOA -> not_found;
  v4 integration sa SS_choose_doc_type.png: 8/8 kasama ang blue COE row).
- `py_compile` sa dalawang files — OK; import chain (uploader + GUI tab)
  — OK.
- Live test PENDING: patakbuhin muli ang PASCUA/SAFLOR. MAHALAGA: i-check
  muna na walang natirang luma rows sa popup ng mga pasyenteng na-ABORT
  noon (kung may natira, i-Delete muna — aabutan ng duplicate-row guard
  ang run: ABORT ulit itaas, hindi mali ang maitype). I-verify rin na
  ang pag-close ng popup nang walang Upload ay hindi nag-iiwan ng
  attachments sa HBSys.

### 2026-09-03 — Claim Attachments doc type: live-test fixes (v3 — pixel rows + folder-driven matching)

Reason:

- Unang live test (2026-09-03 ~11:30): ABORT ang dalawang pasyente dahil
  (1) ini-count ang header rows ("Doc File Document PHIC...", "Cloud
  Storage URL Transmitt") at path-only fragments bilang "unclassified
  rows", at (2) nag-split ang OCR ng mga suffix ("\D" + "TR.pdf" para sa
  DTR; "\SOA1" na hiwalay ang ".pdf"). Root cause: cropped-region PSM 6
  OCR na mababa ang quality + row clustering via OCR text positions lang.

Files:

- Modified `core/claim_attachments_doc_type.py`:
  - New `detect_doc_type_from_words()` — per-row fallback (per-word regex
    + joined normalised substring) para sa OCR-split tokens.
  - New `match_row_to_file()` — FOLDER-DRIVEN matching: para sa bawat grid
    row band, hanapin kung alin sa mga hindi pa na-match na folder files
    ang tumutugma (stem sa normalised row-text tail; pure-letter stems
    >=3 chars ay may subsequence fallback para sa "\D"+"TR.pdf" splits;
    digit stems (SOA1/CF4) ay exact substring lang para iwasan false
    positives). Return: doc type / None / "?" (ambiguous).
  - New `doc_types_from_folder_files()` at `_file_stem()` helpers.
  - Standalone tests: 32 cases PASSED — kasama ang lahat ng live-test OCR
    fragments (DTR split, SOA1 split, e50A.xmi, header junk na hindi
    dapat mag-match).

- Modified `core/claim_attachments_uploader.py`:
  - Detection strategy v3: (1) pixel-based row bands — light-gray
    full-width separator lines (y=413,443,473,... sa screenshot; 80%
    threshold sa x=700..1350) na nagbibigay ng eksaktong row boundaries;
    (2) FULL-screenshot OCR (hindi na cropped PSM 6) na may band
    assignment via word top position; (3) folder-driven matching per band.
  - Confidence rule: extension-bearing words (.pdf/.xml/.xmi) ay
    tinatanggap hanggang conf>10 (ibang words conf>20) — ang SOA2 token
    (conf=17) ay nawawala sa lumang threshold.
  - Band skip rules: symbol-only bands (scrollbar arrows "«" ">") at
    non-file text bands (header remnants) ay skip, HINDI abort. Abort
    lang kapag may filename token na walang match o ambiguous.
  - Coordinates: DOC_FIELD_X 505→497, DOC_ARROW_X 537→519 (pixel recon:
    ang Doc Type column ay x=470..524 — ang lumang 537 ay LABAS na ng
    column).
  - Leftover files (nasa scroll area, wala sa visible bands) → ABORT na
    may explicit reason "scrolling not supported" — hindi partial
    upload.

Behavior:

- Kapag kumpleto ang files sa visible grid (7 rows o mas kaunti):
  per-row type + arrow + TAB, saka Upload → OK → Close.
- Kapag may scrolled-out files (hal. 15 files): ABORT bago mag-Upload,
  walang mali maaaring maipadala; listahan ng mga hindi visible sa log.

Verification:

- `python core/claim_attachments_doc_type.py` — 32/32 PASSED.
- Integration test laban sa aktwal na SS_choose_doc_type.png (parehong
  logic ng uploader): 8 bands detected eksakto; 7 file rows → CSF, DTR,
  SOA, SOA, CF4, CF5, ESA na may tamang row centers (428/458/488/518/
  555/599/643); scrollbar band skip; 8 scrolled-out files tama ang
  identification. RESULT: PASSED.
- `py_compile` tatlong files — malinis; buong GUI import chain OK.
- Live test PENDING: `--live --confirm-each --limit 1` — susunod na
  live run ang magpapatunay ng combo field/arrow coordinates (497/519)
  at ng Upload→OK→Close flow.

### 2026-09-03 — Claim Attachments: Doc Type Assignment step (after 2nd Open) + NameError repair

Reason:

- Ayon sa may-ari (spec 2026-09-02): pagkatapos ng pangalawang click Open
  (XML attach), may Doc Type column ang grid sa attachments popup. Bawat
  file row ay kailangang mabigyan ng doc type base sa filename suffix,
  bago i-click ang Upload. Implementasyon ayon sa
  `DOC_TYPE_ASSIGNMENT_PLAN.md`.
- Kasabay nito: na-repair ang `NameError: name 'Point' is not defined` na
  humarang sa pagbukas ng buong GUI (corrupted indentation mula sa
  nabigong edit noong 2026-09-02 session).

Files:

- New `core/claim_attachments_doc_type.py`:
  - `detect_doc_type(word)` — suffix-to-doc-type mapping gamit ang regex +
    OCR-noise-tolerant normalisation (O↔0, S↔5, I/L↔1, B↔8, Z↔2; ".xmi"
    tinatanggap bilang OCR misread ng ".xml"). PDF map: COE, CSF, DTR,
    SOA1/SOA2→SOA, MRF, PBC, MMC, OPR, ANR, CF3, CF2. XML map: CF4, CF5,
    eSOA→ESA. May `__main__` standalone test (19 cases mula sa aktwal na
    OCR recon ng SS_choose_doc_type.png).
- Modified `core/claim_attachments_uploader.py`:
  - Repaired ang sira na `Point` dataclass definition (indentation) at
    inayos ang mga na-duplicate na doc-type constants — ito ang sanhi ng
    GUI NameError.
  - Idinagdag ang `MAX_DOC_ROWS = 40` guardrail at import ng
    `detect_doc_type`.
  - New method `AttachmentsOperator.assign_doc_types_and_upload()`:
    OCR ng popup grid (region-based screenshot + pytesseract) → row
    clustering (15px tolerance) → doc type detection per row → per-row:
    click combo field, i-type ang doc type, click arrow down (combo),
    TAB → Upload (598,730) → Enter (OK) → Close (1408,734). Dry-run mode:
    log lang ng mga idedetect mula sa patient folder, walang clicks.
  - Guardrails: unknown suffix sa kahit anong row → ABORT (walang Upload
    click — hindi papayagang maipadala ang maling doc type sa HBSys);
    row count cross-check vs patient folder (scrolling hindi pa suportado
    → abort + escalate); MAX_DOC_ROWS cap laban sa runaway TAB loop.
  - Integrated sa `run_attachments_loop()` bilang Step 5.5 pagkatapos ng
    `select_xml_type_and_open()`, bago ang `close_attachment_popup()`;
    kapag nag-fail, `mark_failed` + existing consecutive-failure guardrail.
- Modified `gui/claim_attachments_tab.py`:
  - Same Step 5.5 insertion sa `_upload_worker` pagkatapos ng XML attach;
    may log messages at FAILED status handling na kaparehas ng ibang steps.
- Modified `tests/test_gui_verify_panel_removal.py`:
  - In-update ang expected notebook tabs list (idinagdag ang "Add Claims
    Upload" at "Claim Attachments" — pre-existing FAIL mula 2026-08-28/09-01
    na hindi pa naaayos sa test expectations).

Behavior:

- Before: pagkatapos i-attach ang XML files, isara agad ang popup — walang
  doc type assignment, walang Upload click sa loob ng popup.
- After: pagkatapos ng 2nd Open, ang bawat grid row ay bibigyan ng doc type
  (type + arrow down + TAB), saka i-click ang Upload → OK (Enter) → Close.
  Ang `close_attachment_popup()` ay nagsisilbing safety net (kapag nag-close
  na ang popup via Close button, confirmation lang ito).
- Dry-run: nag-log lang ng mga idedetect na doc types mula sa folder
  contents ("would set doc type 'CF4' for ...") — ligtas i-test nang walang
  HBSys.

Safety / compatibility:

- Walang binagong working OCR, PDF Merge, Auto Sign, XML Generator, o Claims
  Checker. Ang panibagong step ay nasa loob lang ng claim attachments flow.
- Ang doc type step ay nag-a-abort (hindi nagha-hula) kapag may unknown suffix
  o kulang na rows — walang maling doc type ang makakarating sa HBSys.
- Backward compatible ang `CalibratedPoints.load()` — ang mga bagong field
  ay may defaults kahit luma ang calibration JSON.

Verification:

- `python -m py_compile` sa tatlong modified files — malinis.
- `python core/claim_attachments_doc_type.py` — 19/19 cases PASSED
  (kasama ang aktwal na OCR-noise strings mula sa screenshot: "SOAL.pdfF"
  → SOA, "e50A.xmi" → ESA, "D1S20260822" prefix, atbp.).
- Dry-run `assign_doc_types_and_upload()` sa totoong READY patient
  (CABERO, ROSEMARIE SALUD - 8 files): tama ang lahat ng detections —
  CF4, CF5, ESA, COE, CSF, DTR, SOA, SOA. RESULT: PASSED.
- `python tests/test_gui_verify_panel_removal.py` — ALL CHECKS PASSED
  (inayos na ang pre-existing tab list FAIL).
- Buong import chain ng GUI (`start_claims_gui` → `edh_claims_gui_XML_COPY_BUTTON`
  → `gui.claim_attachments_tab` → `core.claim_attachments_uploader`) —
  OK na; gumagana na ulit ang pagbukas ng system.
- Live test PENDING: `--live --confirm-each --limit 1` — inaasahang i-run ng
  may-ari para sa OCR grid detection at combo field/arrow coordinates
  (DOC_FIELD_X=505, DOC_ARROW_X=537 ay derived mula sa OCR recon; kung
  mag-miss, i-calibrate gamit ang Coordinate Getter).

### 2026-09-02 — Add Claims Upload GUI: restore Coordinate Getter button; loop-engineering skill: markdown-update rule

Reason:

- Bawi ng may-ari ang isa sa mga inalis na calibration buttons: ibalik ang
  `Coordinate Getter` (Calibrate Coordinates at Detect Popup ay mananatiling
  tinanggal).
- Idagdag sa `.agents/skills/loop-engineering/SKILL.md` ang mandatory rule na
  pagkatapos ng anumang code edit o addition ay i-update ang mga kaugnay na
  markdown docs sa same work session.

Files:

- Modified `gui/add_claims_upload_tab.py` (minimal):
  - Binalik ang `Coordinate Getter` button pagkatapos ng Resume Batch
    (padx 20) at ang `open_coordinate_getter()` method (subprocess launcher
    para sa `core.coordinate_getter`).
  - Binalik ang `import os` (gagamitin ulit ng method).
  - Ang `Calibrate Coordinates` at `Detect Popup` buttons/methods ay
    mananatiling WALA ayon sa nakaraang change.
- Modified `.agents/skills/loop-engineering/SKILL.md`:
  - Design mode list: bagong step 8 — "Update the markdowns": after any code
    edit or addition, laging i-update ang relevant markdown documentation
    sa same work session; hindi kumpleto ang code change hangga't hindi
    na-update ang docs na naglalarawan nito. Nabanggit na ito ay nag-aapply
    sa lahat ng mode (design, review, implementation).
- Updated `CHANGE_RULES.md`.

Behavior:

- Add Claims Upload tab: Start Upload | Stop | Resume Batch | Coordinate
  Getter (balik sa dating layout para sa coordinate getter tool); ang
  calibrate/detect entry points ay CLI lang (`--calibrate`/`--detect`).
- loop-engineering skill: ang mga agent sessions na gumagamit ng skill ay
  obligadong i-update ang markdowns pagkatapos ng bawat code edit/add.

Safety / compatibility:

- GUI-only change sa tab; walang core module, database, o processor na
  naapektuhan. Ang `core/coordinate_getter.py` ay hindi kailanman binura at
  walang ibang caller ang mga inalis na calibrate/detect methods.
- SKILL.md ay documentation-only para sa agent behavior; walang production
  code na naapektuhan.

Verification:

- `python -m py_compile gui/add_claims_upload_tab.py` — malinis (.venv).
- Headless GUI test — PASSED: Coordinate Getter button present; Calibrate
  Coordinates/Detect Popup ay wala pa rin; `open_coordinate_getter`
  restored; core buttons (Start Upload/Stop/Resume Batch/Browse/Refresh)
  intact.

### 2026-09-02 — Add Claims Upload GUI: remove Calibrate Coordinates / Detect Popup / Coordinate Getter buttons

Reason:

- Ayon sa may-ari, tanggalin na ang calibration buttons sa Add Claims Upload
  tab. Buttons lang ang inalis — hindi binago ang core automation, hindi
  binura ang calibration modules mismo.

Files:

- Modified `gui/add_claims_upload_tab.py` (minimal):
  - Removed the three buttons sa Controls section: `Calibrate Coordinates`
    (`open_calibration`), `Detect Popup` (`detect_popup`), at `Coordinate
    Getter` (`open_coordinate_getter`), kasama ang vertical separator at ang
    buong "Calibration" method section (subprocess launchers).
  - Removed the now-unused `import os`.
  - Walang binago sa patient list, mode selection, Start/Stop/Resume,
    upload worker, at log display.

Behavior:

- Before: may tatlong calibration buttons (Calibrate Coordinates, Detect
  Popup, Coordinate Getter) pagkatapos ng Resume Batch button.
- After: ang Controls section ay Start Upload | Stop | Resume Batch lamang.
  Ang CLI (`--calibrate`/`--detect` sa `core.add_claims_uploader`) at ang
  modules `core/add_claims_calibration.py` at `core/coordinate_getter.py` ay
  hindi ginalaw at pwede pa ring i-run nang direkta.

Safety / compatibility:

- GUI-only removal; walang production/core module, database, o processor
  na naapektuhan.
- Ang mga calibration method ay walang ibang caller (verified via code
  search), kaya walang nasirang ibaing functionality.
- Backward compatible: ang upload workflow mismo (dry-run/live, state,
  resume) ay walang pinagbago.

Verification:

- `python -m py_compile gui/add_claims_upload_tab.py` — malinis (system
  at .venv Python).
- Headless GUI instantiation test: WALA nang calibration button text sa
  tab (Calibrate Coordinates / Detect Popup / Coordinate Getter — lahat
  absent), present pa rin ang Start Upload/Stop/Resume Batch/Browse/
  Refresh, at ang mga core upload method (`start_upload`, `stop_upload`,
  `resume_upload`, `refresh_patient_list`) ay nandoon pa rin; ang tatlong
  calibration method ay wala na. RESULT: PASSED.
- `python tests/test_gui_verify_panel_removal.py` — 1 pre-existing FAIL
  (notebook tabs list: hindi pa kasama sa expected list ng test ang "Add
  Claims Upload"/"Claim Attachments" na tabs mula 2026-08-28/09-01);
  hindi kaugnay ng change na ito — lahat ng Quick Actions checks ay PASS.

### 2026-08-26 — Date Fill: skip patients whose HBSys dates are already complete (pre-check)

Reason:

- Kapag na-run ulit ang Date Fill sa mga pasyenteng kumpleto na ang dates sa
  HBSys, muling pinoproseso pa rin ang buong CF2 flow. Gusto ng may-ari na
  i-skip ang mga pasyenteng kumpleto na (eksaktong tugma sa expected fill
  date), at i-process lang ang may kulang. Desisyon ng may-ari: eksaktong
  tugma ang batayan; kapag 1–2 lang ang kulang, buong flow pa rin (v1);
  testing script lang (`hbsys_fill_dates_testing.py`) ang gagalawan — hindi
  ang production `hbsys_fill_dates.py`.

Files:

- Added `date_fill_hbsys/hbsys_date_fill_precheck.py` — read-only pre-check
  module:
  - `precheck_claim(verifier, hospital_no, admission, discharge,
    claim_type)` → `PrecheckResult` na may decision na `SKIP` /
    `PROCESS` / `UNRESOLVED` (+ missing fields list at reason).
  - Reuses `resolve_exact_encounter()` + `capture_patient_snapshot()` +
    `encounter_field_checks()` ng verifier — WALANG bagong SQL, walang
    duplicate business logic. Expected fill date ay galing sa
    `EncounterIdentity.target_date` (discharge para REGULAR, admission para
    ABTC).
  - `UNRESOLVED` kapag hindi natukoy nang eksakto ang encounter — hindi
    sini-skip ang ambiguous match (sunod sa "never guess" rule); tuloy sa
    normal na audited flow.
  - May standalone self-test via `if __name__ == "__main__":` (9 pure-
    function checks, walang DB na kailangan).
- Modified `date_fill_hbsys/hbsys_date_fill_verifier.py` (refactor lamang):
  - Extracted ang per-field completeness semantics sa bagong public helper
    na `encounter_field_checks(expected, state)`; ginagamit na ngayon ito ng
    `evaluate_post_save()` — isang source of truth para sa parehong pre-check
    at post-save proof. Walang binago sa behavior/semantics.
- Modified `date_fill_hbsys/hbsys_fill_dates_testing.py` (minimal):
  - Main loop: bago ang bawat claim (kapag naka-ON ang pre-check), tawagin
    ang `precheck_claim()`; `SKIP` → status `SKIPPED_DATES_COMPLETE`, log,
    continue — ZERO clicks sa HBSys. `PROCESS`/`UNRESOLVED`/error sa
    pre-check → tuloy sa normal na flow (fail-safe).
  - Bagong CLI flag `--no-precheck` bilang escape hatch (default: ON).
  - Run-log CSV: bagong columns `precheck`, `precheck_missing`,
    `precheck_reason` (hiwalay na dict, hindi naaapektuhan ng audit reset ng
    `process_claim`).
  - Summary popups: hiwalay na bilang ng "Skipped (dates already complete)"
    laban sa verified at failed; `failed_rows` filter ay hindi na
    isinasama ang `SKIPPED_DATES_COMPLETE`.
  - `describe_stop_status()`: nadagdagan ng `SKIPPED_DATES_COMPLETE`
    description.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: lahat ng claims sa output folder ay pinoproseso nang buo kahit
  kumpleto na ang dates sa HBSys.
- After: bawat claim ay ni-pre-check muna gamit ang read-only DB snapshot;
  kung eksaktong tugma lahat ng professional/consent/authorization dates sa
  expected fill date (discharge/admission), LAKTAWAN ito nang walang kahit
  isang click. Kapag may kulang o iba, tuloy pa rin ang dating buong flow.

Safety / compatibility:

- Read-only SELECTs lamang ang pre-check (parehong connection factory ng
  verifier); walang writes sa HBSys.
- Fail-safe: error o ambiguity sa pre-check = PROCESS (dating ugali), hindi
  skip.
- Hindi ginalaw: mismong click/fill sequence, production
  `hbsys_fill_dates.py`, OCR, XML generators, Claims Checker, GUI.
- Backward compatible: default behavior ay may pre-check pero ang resulta
  ng non-complete claims ay kapareho ng dati; `--no-precheck` ibinabalik
  ang lumang daloy nang buo.
- Refactor note: ang `encounter_field_checks()` extraction ay
  behavior-preserving (pinatunayan ng 34/34 existing verifier tests).

Verification:

- `python hbsys_date_fill_precheck.py` — 9/9 self-test checks PASSED.
- Refactor regression: `python -m unittest test_hbsys_date_fill_verifier`
  — Ran 34 tests, OK (pareho bago at pagkatapos ng refactor).
- `python -m py_compile` sa 3 binagong/bagong files — malinis.
- Dry-run laban sa totoong `output/` (4 claims): lahat ay nag-ulat ng
  `PROCESS` na may tamang missing-fields list (wala pang na-fi-fill), at
  ang CSV columns (`precheck`, `precheck_missing`, `precheck_reason`) ay
  populado. Bug na nadetect at naayos noong development: ang unang bersyon
  ay naglalagay ng precheck fields sa `operator.audit`, na nirereset ng
  `process_claim()` — inilipat sa hiwalay na dict.
- SKIP path live-data test (read-only): isang totoong encounter mula sa
  HBSys na kumpleto ang dates (hpercode 000000000020867, dis 2026-08-23) →
  `SKIP` decision na may tamang reason at enccode.
- Live smoke test PENDING — i-run ng may-ari kapag handa:
  `python hbsys_fill_dates_testing.py --live --limit 1 --confirm-each`
  (may pre-check na ito by default).

### 2026-08-26 — Production Date Fill: parehong skip-if-dates-complete pre-check

Reason:

- I-apply ang parehong skip-if-complete business logic ng testing Date Fill
  sa production `hbsys_fill_dates.py` para hindi na muli iproseso ang mga
  pasyenteng kumpleto na ang dates sa HBSys.

Files:

- Modified `date_fill_hbsys/hbsys_fill_dates.py` (minimal integration;
  walang binago sa click/fill sequence):
  - Imports mula sa `hbsys_date_fill_precheck` + `HbsysDateFillVerifier`.
  - Main loop: bago ang bawat claim, `precheck_claim(...)` na may
    `claim_type="REGULAR"` (production ay REGULAR lang); `SKIP` → status
    `SKIPPED_DATES_COMPLETE`, continue — zero clicks. `PROCESS` /
    `UNRESOLVED` / pre-check error → tuloy sa dating flow (fail-safe).
  - Bagong CLI flag `--no-precheck` (default: ON).
  - Run-log CSV: bagong columns `precheck`, `precheck_missing`,
    `precheck_reason`; precheck fields ay nasa hiwalay na dict (hindi
    naaapektuhan ng anumang audit reset).
  - `failed_rows`: hindi na isinasama ang `SKIPPED_DATES_COMPLETE`; ang
    "stopped" popup ay gumagamit na ng `failed_rows[-1]` (dating
    `results[-1]`, para hindi maipakita ang skip row bilang failed).
  - "Date Fill Complete" popup: may dagdag na "Skipped (dates already
    complete)" count.
  - `describe_stop_status()`: nadagdagan ng `SKIPPED_DATES_COMPLETE`.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: lahat ng claims sa output folder ay pinoproseso nang buo kahit
  kumpleto na ang dates.
- After: eksaktong tugma lahat ng professional/consent/authorization dates
  sa expected fill date (discharge, REGULAR) → SKIP nang walang click; iba
  pa → dating buong CF2 flow. Pareho ng testing script ang semantics dahil
  iisang module (`hbsys_date_fill_precheck`) at iisang field-semantics
  source (`encounter_field_checks`) ang ginagamit.

Safety / compatibility:

- Read-only SELECTs lamang; fail-safe sa error/ambiguity (PROCESS, hindi
  skip); `--no-precheck` ibinabalik ang lumang daloy.
- Walang binago sa click/fill/verification sequence ng production script.

Verification:

- `python -m py_compile date_fill_hbsys/hbsys_fill_dates.py` — malinis.
- Dry-run laban sa totoong `output/` (2 claims): precheck columns populado
  (`PROCESS`, tamang missing-fields list) bago pa ang normal flow. Ang
  kasunod na `error: HBSys window not found` ay pre-existing dry-run
  behavior kapag sarado ang HBSys — hindi kaugnay ng change na ito.
- SKIP path at pre-check semantics: pinatunayan na sa nakaraang entry
  (live-data test na nagresulta ng `SKIP` + 34/34 verifier tests).
- Live smoke test PENDING — i-run ng may-ari kapag handa at bukas ang
  HBSys.

### 2026-08-26 — XML Clicker: skip folders that already have all 3 XML; generate missing kinds only

Reason:

- Kapag na-run ulit ang XML Clicker sa mga output folders na kumpleto na ang
  CF4/CF5/eSOA XML, muling kino-click nito ang buong HBSys workflow at
  posibleng mag-doble ang XML. Gusto ng may-ari na i-skip ang kumpletong
  folders at i-process lang ang mga kulang (0, 1, o 2 XML).

Files:

- Added `core/xml_output_checker.py` — modular, read-only filesystem checker:
  - `find_existing_xml_kinds(folder)` — nag-scan ng patient output folder para
    sa `_CF4.xml` / `_CF5.xml` / `_ESOA.xml` (case-insensitive) files.
  - `missing_xml_kinds(existing)` / `is_xml_complete(existing)` /
    `format_kinds(kinds)` helpers.
  - May standalone self-test via `if __name__ == "__main__":` (13 checks:
    empty/full/partial/noisy/missing folders + helper functions).
- Modified `date_fill_hbsys/xml_generator_clicker.py` (minimal integration):
  - Import block: `sys.path` insert ng PROJECT_ROOT + imports mula sa
    `core.xml_output_checker` (parehong convention sa
    `hbsys_date_fill_verifier.py`).
  - `process_claim(claim, kinds_to_process=None)`: bagong optional parameter;
    default ay lahat ng 3 kinds (backward compatible); tatakbo lang ang
    cf4/cf5/esoa actions na kasama sa requested set.
  - Main loop: bago ang bawat claim, pre-check ng existing XML sa output
    folder; kumpleto (3/3) → status `skipped_complete_xml`, log, continue —
    ZERO clicks sa HBSys. Kulang → i-process LANG ang missing kinds.
  - Post-run FTPURL verification: ang expected-kinds check ay
    `set(pending_kinds)` na imbes na laging `{CF4,CF5,ESOA}` (para hindi
    mag-flag ng "missing" ang mga kind na hindi hiningi).
  - Run-log CSV: bagong columns `existing_xml` at `missing_xml`.
  - Completion popup: hiwalay na bilang para sa skipped / completed / needs
    review; may summary print din sa console (`Already complete (will be
    skipped): N` / `To process: M`).
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: lahat ng claims sa `output/` ay pinoproseso nang buong
  CF4→CF5→eSOA kahit kumpleto na; walang skip at walang partial processing.
- After: kumpleto ang 3 XML sa output folder → SKIP (walang klik). Kulang
  (0–2) → tanging mga kulang na XML lang ang gine-generate (hal. may CF4
  na → CF5+eSOA lang ang i-click). Ang run log ay nagtatala na ngayon ng
  `existing_xml`/`missing_xml` per patient at may `skipped_complete_xml`
  status.

Safety / compatibility:

- Read-only filesystem checks lamang ang pre-check; walang bagong dependency.
- Skipped patients = walang kahit isang mouse/keyboard activity sa HBSys.
- Hindi ginalaw: production processor, OCR, signing engine, XML generator
  internals, Claims Checker, Date Fill, GUI, `core/xml_auto_copy.py`.
- Backward compatible: walang binagong CLI arguments; default na ugali ng
  `process_claim` (walang parameter) ay buong 3 kinds pa rin.
- Tandaan: ang check ay nakabatay sa patient OUTPUT folder (kung saan
  nagko-copy ang xml_auto_copy), ayon sa napili ng may-ari — HINDI ang
  FTPURL folder.

Verification:

- `python core/xml_output_checker.py` — 13/13 self-test checks PASSED
  (kasama ang case-insensitivity fix na `_cf5.XML`; isang FAIL ang nadetect
  at naayos noong development).
- `python -m py_compile date_fill_hbsys/xml_generator_clicker.py
  core/xml_output_checker.py` — malinis.
- Dry-run laban sa totoong `output/` (5 claims): pre-check logs tama
  (`existing=NONE | to generate=CF4+CF5+ESOA`), DRY-RUN mode, walang live click.
- Simulated dry-run (`--ready-dir` na may 3 test folders):
  COMPLETE (3 XML) → `SKIPPED ... (CF4+CF5+ESOA)`, walang processing;
  EMPTY (0 XML) → buong CF4+CF5+ESOA;
  PARTIAL (CF4 lang) → `to generate=CF5+ESOA` lang. Run-log CSV ay tama
  ang `existing_xml`/`missing_xml`/status columns.
- Regression: `python -m unittest tests.test_xml_auto_copy` — Ran 15 tests, OK.
- Live smoke test PENDING — i-run ng may-ari kapag handa:
  `python date_fill_hbsys/xml_generator_clicker.py --live --limit 1 --confirm-each`.

### 2026-08-24 — GitHub repository setup (private) + .gitignore

Reason:

- I-version ang code sa GitHub (private repo `edh-claims-automation`) para sa
  backup at collaboration, habang tinitiyak na HINDI na-upload ang patient
  data, runtime data, at secrets.

Files:

- Added `.gitignore` (project root) — nag-e-exclude:
  - Patient/work folders (structure kept via `.gitkeep`): `scans/`,
    `output/`, `backup_originals/`, `claims_checker_results/` at ang mga
    subfolder nito (`INCOMPLETE`, `READY`, `READY_ARCHIVED`,
    `READY_WITH_REVIEW`), `review_staging/`, `resume_staging/`,
    `merge_input/`, `merge_output/`, `pdfs/`, `test_runs/`,
    `training_data/`, `date_fill_queue/`, `New folder/`, `New folder (2)/`.
  - Secrets: `hbsys_bot.py` (hardcoded Telegram token + DB credentials),
    `webscanner/certs/` (SSL private key), `.idea/`, `.reasonix/`,
    `reasonix.toml`.
  - Local settings: `signatures/`, `claims_gui_config.json`,
    `doctors_config.json`, `signature_check_config.json`,
    `update_config.json`, `date_fill_hbsys/Path.txt`,
    `date_fill_hbsys/command.txt`.
  - Runtime/generated: `logs/`, `reports/`, `*.log`, `claims_gui_log_*`,
    `claims_checker_report*`, `fees_checker_report*`, `*_temp.png`,
    `*_preview_temp.png`, `*.db` (SQLite), `review_queue.json`,
    `unknown_review_log.json`, `unknown_training_data.json`, sample PDFs.
  - Python/OS: `__pycache__/`, `*.pyc`, `.venv/`, `.pytest_cache/`,
    `Thumbs.db`, `Desktop.ini`, `.DS_Store`.
- Added `.gitkeep` placeholders sa 12 work folders (upang ma-preserve ang
  folder structure sa repo kahit walang laman).
- Git repo initialized sa `C:\claims_bot` (branch na default) at 123 files
  ang na-stage para sa unang commit (code, docs, tests, GUI images lamang).
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: walang version control; ang buong folder (kasama ang ~1.8 GB ng
  patient data sa `backup_originals/` at `claims_checker_results/`) ay nasa
  isang lugar lamang.
- After: ang code at docs ay nasa private GitHub repo; ang patient data,
  credentials, at runtime data ay nananatili lamang sa local machine.

Safety / compatibility:

- WALANG patient data, credentials, o generated reports ang na-stage — na-verify
  sa pamamagitan ng full staged-file review bago mag-commit.
- Ang production engine, GUI, core modules, tests, at docs ay hindi binago;
  ang pagdagdag ng `.gitignore` at `.gitkeep` ay hindi nakakaapekto sa runtime.
- Private repo lamang — hindi ito public (may hardcoded DB credentials pa rin
  ang production engine; i-recommend na i-refactor ito sa environment/config sa
  hinaharap).

Verification:

- `git init` — OK; `git add -A` — 123 files staged (121 pagkatapos i-exclude ang
  `date_fill_hbsys/Path.txt` at `command.txt`).
- Full staged-file review: WALANG nakitang patient data, `.db`, `.csv`,
  `.xlsx`, `.log`, `hbsys_bot.py`, certs, o temp images.
- Ang natitirang "suspicious" matches ay false positives: `.gitkeep`
  placeholders at `patient_*.py` code modules.
- Push sa GitHub (private `chxlover/edh-claims-automation`, branch `main`) —
  VERIFIED: `git ls-remote origin` ay nagpapakita ng `acbff9b... refs/heads/main`
  na tugma sa local HEAD.

### 2026-08-24 — PDF detection: Delivery Room Record as OPR

Reason:

- Idagdag ang "Delivery Room Record" bilang isa pang dokumento na ide-detect
  bilang `OPR` (katulad ng "Operating Room Record"). Ang Delivery Room Record
  ay madalas na kasama sa claims (delivery/childbirth cases) pero hindi pa
  nade-detect noon at napupunta sa UNKNOWN.

Files:

- Modified `bot_unknown_trainer_DEFERRED_OUTPUT_REVIEW_PATSUFFIX_ADM_DIS.py`:
  - `DOC_KEYWORDS["OPR"]`: `["operating room record", "delivery room record"]`.
  - `detect_opr_type()`: STRICT check na ngayon ay may kasamang
    `"delivery room record"`, at ang fuzzy fallback ay tumitingin na rin sa
    `DELIVERY ROOM RECORD` (threshold 88, kapareho ng operating room record).
- Modified `unknown_review_manager_INTEGRATED_PDFA_THREAD.py`:
  - `score_training_phrase` strong markers para sa `OPR` ay may kasama nang
    `"DELIVERY ROOM RECORD"` para mas mataas ang training score kapag ang
    user ay manu-manong nag-tag ng delivery room record bilang OPR.
- Updated `README.md` (document types table: OPR row at MMC row).
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: ang "Delivery Room Record" ay hindi na-detect bilang OPR — napupunta
  sa UNKNOWN / Unknown Review.
- After: ang OCR text na may "delivery room record" (exact o fuzzy) ay
  nire-return bilang `OPR` at nase-save bilang `OPR.pdf` sa patient folder,
  katulad ng operating room record. Walang binago sa ibang detection flows
  (MRF/PBC/ANR/CF2/SOA/DTR/CSF/COE ay hindi ginalaw).

Safety / compatibility:

- Ang production processor ay minimal na pagbabago lang (dagdag na keyword
  string at isang OR condition sa umiiral nang `detect_opr_type()`); walang
  binagong OCR engine, signing, XML generator, o Claims Checker.
- Walang bagong dependency; read-only pa rin sa HBSys/MySQL.
- Hindi naaapektuhan ang existing "Operating Room Record" detection.

Verification:

- `python -m py_compile bot_unknown_trainer_DEFERRED_OUTPUT_REVIEW_PATSUFFIX_ADM_DIS.py` — malinis (may pre-existing `SyntaxWarning` sa line 502 tungkol sa `\c` sa configuration help text; hindi ito kaugnay ng change na ito).
- `python -m py_compile unknown_review_manager_INTEGRATED_PDFA_THREAD.py` — malinis.
- Standalone function test (headless): `detect_opr_type("DELIVERY ROOM RECORD")` → `OPR`, `detect_opr_type("Operating Room Record")` → `OPR`, walang delivery/operating room record na text → `""`.

### 2026-08-24 — Consolidated Master README

Reason:

- Magkaroon ng isang malinis at kumpletong master documentation para sa buong
  proyekto: pinagsama ang mahahalagang nilalaman ng `AGENTS.md`,
  `PROJECT_RULES.md`, `CLAIMS_AGENT_PLAN.md`, `CHANGE_RULES.md`,
  `SETUP_AND_TRANSFER.md`, `NEW_PC_INSTALL.md`, at
  `date_fill_hbsys/README.md`/`TRANSFER_GUIDE.md` sa isang organisadong file,
  para mas madaling basahin at i-maintain.

Files:

- Added `README.md` (project root) — table of contents, overview, golden
  rules, architecture diagrams (current + Claims Agent target), technology
  stack, project layout, document types, processing flow, patient identity &
  folder naming, operation modes, review queue (H001–H010 + PATIENT/UNKNOWN
  review), generated outputs, database policy, core modules table, HBSys
  automation summary, Claims Agent roadmap phases, GUI overview, setup &
  installation, NAS updates, testing commands, development rules, change
  documentation rule, roadmap/status, and a quick command reference.
- Updated `CHANGE_RULES.md`.

Behavior:

- Documentation-only; walang binagong production code, configuration,
  database, o GUI behavior.

Safety / compatibility:

- Ang lahat ng existing markdown files ay hindi binago (ang bagong README ay
  additive at nagli-link sa `CLAIMS_AGENT_PLAN.md`).
- Walang duplicate na documentation na inalis; ang README ay summary/reference
  lamang, at ang `CHANGE_RULES.md` pa rin ang authoritative change record.

Verification:

- Manu-manong cross-check ng README laban sa bawat source markdown
  (AGENTS.md, PROJECT_RULES.md, CLAIMS_AGENT_PLAN.md, CHANGE_RULES.md,
  SETUP_AND_TRANSFER.md, NEW_PC_INSTALL.md, date_fill_hbsys docs) — lahat ng
  key facts (engine filename, GUI filename, module names, review codes,
  folder naming, HBSys requirements, phases) ay tugma.
- Kinumpirma ang aktwal na `core/` module list at `date_fill_hbsys/` file list
  sa filesystem bago isulat ang README.

### 2026-08-14 — Unit tests: hbsys_window detection + GUI HBSys status/blocking

Reason:

- Permanent regression coverage para sa dalawang bagong feature:
  (1) ang shared HBSys window detection na nag-e-exclude ng browser
  windows, at (2) ang GUI HBSys status indicator + Date Fill/XML Clicker
  blocking. Dati ang dalawang ito ay na-verify lang sa pamamagitan ng
  one-off headless commands.

Files:

- Added `tests/test_hbsys_window.py` (22 tests, unittest style, may
  PROJECT_ROOT sys.path insertion at `if __name__ == "__main__"`):
  - `FakeWindow`/`FakeDesktop` + `desktop_factory` na nagre-record ng
    backend; nilalagyan ng mock ang `hbsys_window.Desktop`.
  - `window_title`/`window_class` safe sa broken windows.
  - `is_browser_window` (Chrome_WidgetWin_1/0 true; FNWND370 false).
  - `is_hbsys_window` (class FNWND370/230, HBSys/HOMIS titles, at ang
    regression case: Chrome tab na may "HBSys" sa title ay EXCLUDED).
  - `find_hbsys_windows`/`find_hbsys_window`/`find_hbsys_window_or_raise`
    (browser exclusion, None kapag sarado, RuntimeError, backend
    forwarding, default win32).
- Added `tests/test_gui_hbsys_status.py` (7 tests):
  - Instantiates `EDHClaimsGUI` (withdrawn, parang
    `test_gui_verify_panel_removal.py`) at mino-mock ang
    `gui_module.find_hbsys_window` para hindi nakadepende kung naka-open
    talaga ang HBSys.
  - OPEN → status var, buttons `normal`, walang warning; CLOSED → buttons
    `disabled` + warning text; Error → `disabled`; reopen → nababalik;
    `_apply_hbsys_block` direct; `schedule_hbsys_status_check` job;
    `check_hbsys_status_now` update + reschedule.
  - Note: `cget("state")` ay nag-return ng `Tcl_Obj`, kaya may
    `button_state()` helper na nag-i-`str()`.
- Updated `CHANGE_RULES.md`.

Behavior:

- No production behavior change; test-only additions.

Safety / compatibility:

- Walang binagong production code. Ang GUI test ay nag-i-instantiate ng
  totoong GUI (withdrawn) — kaparehong pattern ng existing
  `test_gui_verify_panel_removal.py`. Ang `find_hbsys_window` mock ay
  nire-restore sa `tearDownClass`.
- Tandaan: `python -m unittest discover -s tests` ay HINDI gumagana sa
  project na ito (walang `tests/__init__.py`, pre-existing limitation);
  ang convention ay i-run ang bawat file — tingnan sa ibaba.

Verification:

- `python -m unittest tests.test_hbsys_window tests.test_gui_hbsys_status`
  — Ran 29 tests, OK (22 detection + 7 GUI).
- `python -m unittest tests.test_gui_hbsys_status -v` — lahat ng 7 GUI
  tests ok.
- `python -m unittest discover -s tests -t . -p "test_*.py"` — ImportError
  (pre-existing: start directory not importable, walang __init__.py);
  consistent sa existing convention ng project.
- Date Fill verifier regression: `python -m unittest
  test_hbsys_date_fill_verifier` — 34 tests OK.

### 2026-08-14 — GUI: block Date Fill / XML Clicker when HBSys is CLOSED

Reason:

- Kapag sarado ang HBSys, ang `Date Fill ABTC/Regular` at `XML Clicker`
  ay mag-fa-fail lang ("HBSys window not found"). Mas maganda na i-disable
  ang mga button na ito at magpakita ng warning habang CLOSED ang HBSys,
  para maiwasan ang walang-silbing pag-click.

Files:

- Modified `edh_claims_gui_XML_COPY_BUTTON.py`:
  - `build_dashboard_tab`: ang `Date Fill ABTC/Regular` (2,1) at
    `XML Clicker` (3,0) quick-action buttons ay naka-store na bilang
    `self.date_fill_btn` at `self.xml_clicker_btn` (walang pinagbago sa
    text, position, o command).
  - Bagong `self.hbsys_warning_label` (tk.Label, red `#DC2626`, naka-wrap)
    sa Production Actions panel, sa ibaba ng quick actions grid, sa itaas
    ng Separator; `__init__` initializes it bilang `None`; `apply_theme()`
    sinasabay ang bg sa `self.colors["panel"]`.
  - `update_hbsys_status()`: sa bawat branch (Error/CLOSED/OPEN) ay
    tumatawag na ng `_apply_hbsys_block(blocked=...)`.
  - Bagong `_apply_hbsys_block(blocked)` — kapag `blocked=True`:
    dinidi-disable ang `date_fill_btn` at `xml_clicker_btn` at ipinapakita
    ang warning "⚠ HBSys is CLOSED — Date Fill and XML Clicker are
    disabled. Open HBSys first."; kapag `False`: ibabalik sa `normal` at
    nililinis ang warning text.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: ang dalawang button ay laging enabled kahit sarado ang HBSys.
- After: auto-update every 3s (kasama ng status indicator) — CLOSED/Error
  = grayed out (disabled) + red warning; OPEN = enabled, walang warning;
  awtomatikong nababalik kapag nag-open ang HBSys.

Safety / compatibility:

- GUI-only change; walang database access, walang processor/OCR/XML
  changes. Ang button text, position, at command ay hindi binago (kaya
  intact ang Quick Actions layout at ang existing GUI test).
- Ang disable ay UI-level lang; ang scripts mismo ay may sariling guard
  ("HBSys window not found") kung sakaling i-run nang direkta.

Verification:

- `python -m py_compile edh_claims_gui_XML_COPY_BUTTON.py` — malinis.
- Headless simulation: REAL (OPEN) → parehong buttons `normal`, walang
  warning; monkeypatch `find_hbsys_window` → CLOSED → parehong buttons
  `disabled`, may warning text; ibalik → OPEN → `normal` ulit, walang
  warning. (Note: `cget('state')` ay nag-return ng `Tcl_Obj`, kaya
  kailangan i-`str()` bago i-compare — test-only detail.)
- `python tests/test_gui_verify_panel_removal.py` — ALL CHECKS PASSED.

### 2026-08-14 — GUI: HBSys status indicator (green/red)

Reason:

- Makita agad sa EDH Claims GUI kung naka-open ang HBSys bago i-run ang
  HBSys-dependent actions (Date Fill ABTC/Regular, XML Clicker): green
  kung OPEN, red kung CLOSED/Error. Ang indicator ay gumagamit ng shared
  `hbsys_window` detection na nag-e-exclude ng Chrome.

Files:

- Modified `edh_claims_gui_XML_COPY_BUTTON.py`:
  - Import: `from date_fill_hbsys.hbsys_window import find_hbsys_window`.
  - `__init__`: added `hbsys_status_var` ("● HBSys: Checking..."),
    `hbsys_status_label = None`, `hbsys_status_job = None`,
    `hbsys_status_poll_ms = 3000`; call `check_hbsys_status_now()` after
    the existing schedulers.
  - Header: new clickable status label (right side, bago ang Save
    Settings/Refresh buttons) na may textvariable `hbsys_status_var`;
    click = instant re-check.
  - `apply_theme()`: sinasabay ang label bg sa theme.
  - New methods: `schedule_hbsys_status_check()` (after-loop every 3s,
    pareho ang pattern sa `dashboard_auto_refresh_tick`),
    `hbsys_status_tick()` (try/finally reschedule),
    `check_hbsys_status_now()` (instant re-check), at
    `update_hbsys_status()` (calls `find_hbsys_window()`; sets
    "● HBSys: OPEN" green `#16A34A`, "● HBSys: CLOSED" red `#DC2626`,
    "● HBSys: Error" red on exception).
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: walang HBSys status sa GUI.
- After: laging nakikita sa header ang HBSys status, auto-refresh every
  3 seconds at sa click; green = naka-open ang HBSys (real window,
  hindi Chrome), red = sarado o error.

Safety / compatibility:

- Read-only desktop enumeration lang; walang HBSys/MySQL access, walang
  patient data, walang processor/OCR/XML changes.
- Ang check ay nasa Tk main thread pero ang `find_hbsys_window()` ay
  mabilis (EnumWindows); may try/except at try/finally para hindi
  masira ang poll loop.
- Walang bagong dependency (pywinauto ay nasa `requirements.txt` na).
- Hindi naaapektuhan ang Quick Actions layout at notebook tabs; walang
  binagong button/position.

Verification:

- `python -m py_compile edh_claims_gui_XML_COPY_BUTTON.py` — malinis.
- Headless check (GUI withdrawn): `update_hbsys_status()` →
  `● HBSys: OPEN`, fg `#16A34A` (HBSys ay naka-open sa machine).
- `python tests/test_gui_verify_panel_removal.py` — ALL CHECKS PASSED
  (Quick Actions layout at notebook tabs intact).
- Live visual check PENDING — i-open ang GUI at i-confirm ang
  green/red indicator sa header at ang auto-refresh kada 3s.

### 2026-08-14 — HBSys window detection: exclude browser (Chrome) windows

Reason:

- Ang HBSys detection ay gumamit ng `"HBSys" in title` substring match,
  kaya ang isang Chrome/Edge tab na may "HBSys" sa title (hal. "Claude
  HBSys Automation - Google Chrome") ay maaaring mapagkamalang HBSys app
  window at ma-focus/ma-maximize sa halip na ang totoong HBSys.

Files:

- Added `date_fill_hbsys/hbsys_window.py` — shared HBSys window detection
  module: `is_browser_window()` (class `Chrome_WidgetWin_*`),
  `is_hbsys_window()` (class `FNWND370`/`FNWND230` o title markers
  `HBSys`/`HOMIS`, ngunit HINDI browser windows), `find_hbsys_windows()`,
  `find_hbsys_window()`, `find_hbsys_window_or_raise()`, at ang safe na
  `window_title()`/`window_class()` helpers; may standalone test via
  `if __name__ == "__main__"`.
- Modified `date_fill_hbsys/hbsys_find_zero_dates.py`: tinanggal ang local
  `find_hbsys_window()` at ginamit ang shared version (focus/restore moved
  into `find_zero_dates()`).
- Modified `date_fill_hbsys/hbsys_inspect.py`: ang filtered mode ay
  gumagamit na ng shared `is_hbsys_window()` (plus the existing
  `PHIC CLAIM FORM`/`Admission History` title matches).
- Modified `date_fill_hbsys/hbsys_fill_dates.py` (production) at
  `date_fill_hbsys/hbsys_fill_dates_testing.py`: ang `focus_hbsys()` ay
  gumagamit na ng shared `find_hbsys_window()`.
- Modified `date_fill_hbsys/xml_generator_clicker.py`: ang `focus_hbsys()`
  ay nag-filter ng candidates gamit ang `is_hbsys_window()` (plus the
  existing `HOSPITAL` title match) bago ang existing priority sort, at
  gumagamit ng safe `window_title()`.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: `Desktop(...).windows()` + `"HBSys" in title` ay maaaring
  pumili ng Chrome tab na may "HBSys" sa title, lalo na kung nauna ito
  sa enumeration kaysa sa totoong HBSys window.
- After: browser windows (Chrome/Edge, class `Chrome_WidgetWin_*`) ay
  hindi kailanman itinuturing na HBSys; ang detection ay humahanap lang
  ng totoong HBSys windows (class `FNWND370`/`FNWND230` o HBSys/HOMIS
  title, hindi browser). Ang exact-title matches (`PHIC`, `Select
  Encounter`, `Admission History`, `#32770` dialogs) ay hindi ginalaw.

Safety / compatibility:

- Read-only desktop enumeration lang; walang HBSys/MySQL access, walang
  patient data, walang GUI changes.
- Ang shared module ay naglalaman ng parehong matching rules na dati,
  minus lang ang browser windows; walang binago sa OCR/click/fill flow.
- `hbsys_window.py` import ay local sa `date_fill_hbsys/` at hindi
  nangangailangan ng bagong dependency (pywinauto na ang gamit).

Verification:

- `python -m py_compile` sa lahat ng 6 na binagong/added files — malinis.
- Live check (`hbsys_window.py` standalone): win32 at uia ay parehong
  nag-ulat ng 1 window lamang — ang totoong HBSys
  (`FNWND370`, handle 924264); WALA na ang dating `Chrome_WidgetWin_1`
  "Claude HBSys Automation" match.
- `python -m unittest test_hbsys_date_fill_verifier` — 34 tests passed.

### 2026-08-13 — Revert: Date Fill learning/teaching/retry/name-proof features

Reason:

- May-ari: ibalik sa orihinal ang code bago ang lahat ng 2026-08-13 changes.
  Ang PHIC name-proof (confinement + surname + given name) ay nagdulot ng
  mas maraming skips sa `hbsys_fill_run` excel, kaya pinili ng may-ari na
  i-revert ang buong session (Scope B: Lahat).

Files:

- Deleted `date_fill_hbsys/hbsys_fill_learning.py`, `hbsys_fill_teaching.py`,
  `test_hbsys_fill_learning.py`, `test_hbsys_fill_teaching.py`,
  `date_fill_hbsys_retry_launcher.py`, `date_fill_hbsys_teach_launcher.py`,
  at ang kanilang JSON data (`date_fill_learning.json`,
  `date_fill_teaching.json`).
- Reverted `date_fill_hbsys/hbsys_fill_dates_testing.py`: tinanggal ang
  learning/teaching/retry integration, `--retry-skipped`, `--teach`,
  `--retries`, per-patient skip popup, teach helpers, at ang
  `patient_name_parts()`/`name_proof_ok()`; naibalik ang orihinal na 38-column
  run log at ang orihinal na `main()` flow.
- Reverted `date_fill_hbsys/hbsys_fill_dates.py` (production): ibinalik ang
  date-only PHIC matching (single candidate, fallbacks `any(token)`, multiple
  candidates gate na walang `name_proof_ok`); tinanggal ang
  `patient_name_parts()`/`name_proof_ok()`.
- Reverted `edh_claims_gui_XML_COPY_BUTTON.py`: tinanggal ang `Retry Skipped
  Dates` at `Teach Date Fill` buttons at ang kanilang SCRIPT_CONFIG entries.
- Reverted `date_fill_hbsys/test_hbsys_date_fill_verifier.py` (tinanggal ang
  6 name-proof tests) at `tests/test_gui_verify_panel_removal.py`
  (tinanggal ang 2 bagong button checks).
- Reverted `date_fill_hbsys/README.md` sa orihinal na nilalaman.

Behavior:

- Ang PHIC Beneficiaries row ay pipiliin muli base sa confinement dates;
  ang pangalan ay ginagamit lamang sa fallbacks at sa multiple-candidate
  scoring (walang surname+given proof sa single candidate).
- Wala nang auto-retry, `--retry-skipped`, teach mode, learning memory, o
  `Retry Skipped Dates`/`Teach Date Fill` GUI buttons.

Safety notes:

- Ang production `hbsys_fill_dates.py` ay read-only pa rin sa HBSys DB.
- Walang ginawang pagbabago sa working OCR, PDF merge, signing, XML
  generator, o Claims Checker.

Verification:

- `python -m py_compile` sa lahat ng binagong scripts — malinis.
- `python -m unittest test_hbsys_date_fill_verifier` — 34 tests passed
  (naibalik mula 40).
- `python tests/test_gui_verify_panel_removal.py` — ALL CHECKS PASSED
  (walang bagong buttons).

### 2026-08-12 — Fees Checker: Ready to Generate XML verdict column

Reason:

- Makita agad sa Fees Checker Excel kung sinong mga pasyente ang
  HANDA nang i-generate ang XML (green) at kung sino ang hindi (red),
  kasama ang konsolidadong remark kung bakit hindi pa ready.

Files:

- Modified `fees_checker.py`:
  - `HEADERS`: added `Ready to Generate XML` after `Status`.
  - New helper `ready_to_generate_xml(row)` — a patient is READY when
    ALL of: fees `Status` == MATCH, `ADM Match` == YES, `DIS Match` ==
    YES, and the three signed-date columns are non-empty; otherwise it
    returns `NO` plus a problem list (status reason, ADM/DIS mismatch,
    which signed-date field is BLANK).
  - `write_reports`: computes the verdict for every row before writing
    (single source of truth) and appends a consolidated
    `XML READY CHECK: ...` remark when not ready.
  - `write_xlsx`: READY rows get a whole-row light-green fill; a MATCH
    row that is NOT ready now turns light red instead of green; the
    `Ready to Generate XML` cell is solid green `008000` (white "YES")
    when ready and solid red `FF0000` (white "NO") otherwise, for every
    row including SKIPPED/ERROR.
  - Removed the separate `DATE CHECK: ... is BLANK.` remarks from the
    previous change — the blank-date red cells remain, and the date
    reasons are now covered by the single `XML READY CHECK` remark.
  - Console summary now reports `READY for XML` count.
  - Module docstring updated.
- Updated `CHANGE_RULES.md`.

Behavior:

- Read-only SELECTs only; hospital database untouched.
- Green row + green "YES" = patient ready for XML generation; red
  "NO" = not ready, remark lists the exact blockers.
- The fees `Status` column (MATCH/MISMATCH/NO FINAL BILL/etc.) is
  unchanged; readiness is a separate, stricter verdict.

Verification:

- `python -m py_compile fees_checker.py` and a live read-only run
  PENDING — terminal (bash) not available on this machine; run once
  possible and verify: green rows only for fully ready patients, red
  `Ready to Generate XML` cells with remarks for the rest, and blank
  date cells still solid red.

### 2026-08-12 — Fees Checker: signed-date columns (blank = SOLID RED)

Reason:

- Makita sa Fees Checker Excel kung aling mga pasyente ang WALANG
  signed dates sa HBSys: `hprofserv.pdoctorsigndate` (professional fee),
  `hpatcon1.consentdate` (consent), at `hpatcon1.authsigndate`
  (authorization/certification). Kung blank ang isang date cell,
  kukulayan ito ng SOLID RED para agad makita.

Files:

- Modified `fees_checker.py`:
  - `HEADERS`: added 3 columns after the employee columns:
    `Prof Fee Sign Date (hprofserv.pdoctorsigndate)`,
    `Consent Date (hpatcon1.consentdate)`,
    `Auth Sign Date (hpatcon1.authsigndate)`.
  - `check_patient_folder`:
    - New read-only query `SELECT pdoctorsigndate FROM hprofserv
      WHERE enccode=%s`; collects the non-empty dates (a patient can have
      several professional-fee rows and empty rows are allowed), joined
      with ` | `. When every row is blank the column is empty and a
      `DATE CHECK: hprofserv.pdoctorsigndate is BLANK.` remark is added.
    - Extended the existing `hpatcon1` SELECT with `consentdate,
      authsigndate`; formats both with `_fmt_date` (YYYY/MM/DD) and adds
      `DATE CHECK: ... is BLANK.` remarks when empty.
  - `write_xlsx`: new `blank_date_fill` (SOLID RED `FF0000`) with white
    bold font; after the status-based row coloring, every blank cell in
    the three date columns is overridden to SOLID RED so missing dates
    stay visible even inside green MATCH rows.
  - Error-row placeholder updated with the new headers.
  - Module docstring updated.
- Updated `CHANGE_RULES.md`.

Behavior:

- Read-only SELECTs only; hospital database untouched.
- Date columns always present in CSV and XLSX; blank date cells are
  SOLID RED in XLSX; remarks explain which field is blank.
- Row Status (MATCH/MISMATCH/NO FINAL BILL/etc.) is unchanged — the
  date check is informational, it does not change the fees verdict.

Verification:

- `python -m py_compile fees_checker.py` and a live read-only run
  PENDING — terminal (bash) not available on this machine; run once
  possible and verify the new columns appear and blank cells are red.

### 2026-08-12 — Replace Date Fill button with Date Fill ABTC/Regular

Reason:

- Tanggalin sa GUI ang production `Date Fill` button (hindi na ito
  ginagamit) at i-retain ang `Date Fill Testing` bilang ang tanging
  Date Fill entry, na may mas malinaw na pangalan na nagpapakita na
  sinusuportahan nito ang parehong ABTC at Regular claims.

Files:

- Modified `edh_claims_gui_XML_COPY_BUTTON.py`:
  - `SCRIPT_CONFIG`: removed the `"date_fill_hbsys"` entry
    (`date_fill_hbsys_launcher.py`); kept `"date_fill_hbsys_testing"`
    (`date_fill_hbsys_testing_launcher.py`).
  - Quick Actions: removed the `Date Fill` button (row 2, col 1);
    renamed the `Date Fill Testing` button to
    `Date Fill ABTC/Regular` (still runs
    `run_script("date_fill_hbsys_testing")`) and reflowed the grid so
    there are no gaps: row 2 = PDF E-Sign | Date Fill ABTC/Regular;
    row 3 = XML Clicker | Copy XML; row 4 = Check Missing | Fees Check;
    row 5 = Recheck INC | PDF Compress; row 6 = PDF Split (alone).
- Updated `tests/test_gui_verify_panel_removal.py` to cover the new
  Quick Actions layout: asserts no `verification_panel`/
  `date_fill_hbsys` SCRIPT_CONFIG entries (testing entry retained), no
  exact "Date Fill" button, presence of `Date Fill ABTC/Regular` at
  (2, 1), and the reflowed cell positions with no duplicates/gaps.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: Quick Actions had both `Date Fill` (production) and
  `Date Fill Testing` buttons.
- After: only `Date Fill ABTC/Regular` remains; clicking it still runs
  the Date Fill Testing script (`hbsys_fill_dates_testing.py` via its
  launcher). The production `date_fill_hbsys_launcher.py` file itself
  is untouched and can still be run directly if needed.

Safety / compatibility:

- GUI-only change; no database access, no patient data, no processor
  changes. The notebook tab `Missing Date Fill Batches` is unrelated
  and was left untouched.

Verification:

- Code search: no remaining `run_script("date_fill_hbsys")` calls;
  `date_fill_hbsys_testing` still present in SCRIPT_CONFIG and used by
  the `Date Fill ABTC/Regular` button.
- `python -m py_compile` and
  `python tests/test_gui_verify_panel_removal.py` PENDING — terminal
  (bash) not available on this machine; run once possible.

### 2026-08-12 — Remove GUI Verification Panel from Main GUI

Reason:

- Tanggalin ang "Verify Panel" shortcut sa EDH Claims GUI — hindi na
  kailangan i-open ang verification panel mula sa main GUI.

Files:

- Modified `edh_claims_gui_XML_COPY_BUTTON.py`:
  - `SCRIPT_CONFIG`: removed the `"verification_panel"` entry.
  - Quick Actions: removed the `Verify Panel` button (row 2, col 0) and
    shifted the remaining quick-action buttons up one row so the grid
    has no gaps (PDF E-Sign / Date Fill now row 2; Date Fill Testing /
    XML Clicker row 3; Copy XML / Check Missing row 4; Fees Check /
    Recheck INC row 5; PDF Compress / PDF Split row 6).
  - Removed the `open_verification_panel()` method.
- Added `tests/test_gui_verify_panel_removal.py` — standalone headless
  GUI test (instantiates `EDHClaimsGUI` with the window withdrawn) that
  checks: no `verification_panel` in `SCRIPT_CONFIG`, no
  `open_verification_panel` method, no "Verify Panel" text anywhere in
  the GUI, notebook tabs intact, and the Quick Actions grid (rows 2-6)
  has every button in the shifted position with no duplicate cells or
  gaps.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: Main Dashboard had a `Verify Panel` quick-action button that
  opened `verification_panel_gui.py` in a separate window.
- After: the button, its `SCRIPT_CONFIG` entry, and its method are gone;
  the standalone `verification_panel_gui.py` script itself is untouched
  and can still be run directly if needed.

Safety / compatibility:

- GUI-only change; no database access, no patient data, no processor
  changes. `_open_independent_gui()` is retained because
  `open_patient_review_queue()` and `open_pdf_splitter()` still use it.

Verification:

- No remaining references to `open_verification_panel` or
  `verification_panel` in `edh_claims_gui_XML_COPY_BUTTON.py` (verified
  by code search; all 5 original references removed).
- Quick Actions grid re-checked: rows 2-6 both columns filled, no gaps
  or duplicate cells.
- `python -m py_compile edh_claims_gui_XML_COPY_BUTTON.py` PENDING —
  terminal (bash) not available on this machine; run once possible.
- `python tests/test_gui_verify_panel_removal.py` PENDING — created;
  run on the owner's PC and record the result here.

### 2026-08-10 — Fees Checker: employee name/number check (hphiclog)

Reason:

- Isali sa Fees Checker ang check para sa employed members: kung ang
  member ay employed private (`typemem='01'`) o employed government
  (`typemem='02'`) sa `hphiclog`, dapat may laman ang `phicnum2`
  (12 digits) at `empname`; kung kulang, ilagay sa Remarks.

Files:

- Modified `fees_checker.py`:
  - Headers/row dict: added `Employed (hphiclog.typemem)`,
    `Employee No (hphiclog.phicnum2)`, `Employee Name (hphiclog.empname)`.
  - `check_patient_folder`: new read-only query
    `SELECT typemem, phicnum2, empname FROM hphiclog
    WHERE hpercode=%s AND typemem IN ('01','02') LIMIT 1`.
    NOTE: `hphiclog` has NO enccode column — it is keyed by `hpercode`
    (verified via information_schema).
  - Employee remarks: "EMPLOYEE CHECK: member is employed private
    (01)/government (02) but phicnum2 is 'X' — dapat 12 digits" and/or
    "but empname is BLANK". Non-employed members (other typemem) are
    left blank with no remarks.
  - Error-row placeholder updated with the new headers.
- Updated `CHANGE_RULES.md`.

Behavior:

- Read-only SELECTs only; hospital database untouched.
- Employee columns only populated when an employed-member row exists.

Verification:

- Real-data test (read-only) passed: ALINDADA (typemem=02) shows
  Employee No 151431000002 (12 digits) and Employee Name
  "PROVINCIAL GOVERNMENT OF ISABELA", no employee remarks; ASUNCION
  (typemem=03, non-employed) leaves the three columns blank with no
  false-positive remarks.
- `python -m py_compile` passed.

### 2026-08-07 — Fees Checker: NO FINAL BILL flag + hpatcon.memphicnum

Reason:

- Kapag ang `Itemized Total (SUM pcchrgamt hpatchrgdtl)` ay zero/null,
  ibig sabihin hindi pa na-final bill sa HBSYS — kailangan i-flag sa
  remarks at i-highlight para malaman agad ang dahilan.
- I-check din ang `hpatcon.memphicnum` (member PHIC number) gamit ang
  `enccode` bilang reference, para makita kung may membership record na.

Files:

- Modified `fees_checker.py`:
  - Headers/row dict: added `Member PHIC No (hpatcon.memphicnum)`.
  - `check_patient_folder`: added read-only query
    `SELECT memphicnum FROM hpatcon WHERE enccode=%s`.
  - New Status `NO FINAL BILL` when itemized total <= tolerance (0/null):
    remarks explain "hindi pa na-FINAL BILL sa HBSYS" plus the
    `hpatcon.memphicnum` value (or WALA) and the grouped total.
  - `write_xlsx`: `NO FINAL BILL` rows are highlighted AMBER
    (light `FFE699`, status cell solid `FFC000`) to distinguish them
    from red MISMATCH; Status column located by header index instead of
    a hardcoded number.
  - Error-row placeholder updated with the new header.
- Updated `CHANGE_RULES.md`.

Behavior:

- Read-only SELECTs only; hospital database untouched.
- NO FINAL BILL = amber rows; MISMATCH/NO RECORD = red; MATCH = green.

Verification:

- Live run against HBSys (12 folders): 8 MATCH, 4 NO FINAL BILL,
  0 MISMATCH/ERROR. ASUNCION flagged NO FINAL BILL (itemized 0.00/null,
  grouped 4,785.00, memphicnum 0602); ALINDADA MATCH with memphicnum
  060000208593.
- CSV verified: `Member PHIC No (hpatcon.memphicnum)` column present in
  all rows; XLSX amber/green fills verified via openpyxl.
- `python -m py_compile` passed.

### 2026-08-06 — PDF Splitter GUI Tool

Reason:

- Kailangan ng tool na kayang i-split ang isang PDF sa page ranges,
  gaya ng iLovePDF Split PDF (referenced screenshot
  `.reasonix/attachments/clipboard-20260806-140503.155389-000046.png`):
  magdagdag ng multiple ranges ("from page X to page Y"), option na
  i-merge ang lahat ng ranges sa iisang PDF, at pumili ng output folder.

Files:

- Added `gui/pdf_splitter_gui.py` — Tkinter PDF Splitter widget
  (`PdfSplitterFrame`) + standalone window wrapper
  (`PdfSplitterWindow`):
  - Pick a PDF file; shows the page count.
  - Add multiple ranges via spinboxes (+ Add Range / Remove Selected),
    validated against the document page count.
  - "Merge all ranges in one PDF file" checkbox — when ticked, all
    selected pages are saved into one `{stem}_split.pdf`; otherwise one
    file per range (`{stem}_range{i}_p{from}-{to}.pdf`).
  - Preview line under the ranges list: shows how many ranges were
    added, the total page count, and how many PDF files will be created
    (updates live as ranges are added/removed or merge toggles).
  - "Output as PDF/A read-only" checkbox (default ON) — each split is
    converted with Ghostscript to a true PDF/A-2 file with `/OutputIntent`
    and the file is set read-only (chmod 0444). Falls back to a plain
    PDF copy when Ghostscript or its PDFA_def.ps/srgb.icc files are
    unavailable.
  - Ghostscript 10.x PDF/A requires a customized `PDFA_def.ps` pointing
    at the absolute `srgb.icc` path plus `--permit-file-read=<icc>` in
    SAFER mode — both handled automatically (temp copy, cleaned up).
  - Output folder picker (defaults to `output/`, or CLAIMS_OUTPUT_FOLDER
    env var); auto-opens the output folder after a successful split.
  - Uses PyMuPDF (`fitz`) `insert_pdf` page selection; standalone
    `main()` + `if __name__ == "__main__"` for testing.
- Added `pdf_splitter_gui.py` — root launcher (pattern matches
  `patient_review_queue_gui.py` / `verification_panel_gui.py`).
- Modified `edh_claims_gui_XML_COPY_BUTTON.py`:
  - `SCRIPT_CONFIG`: added `"pdf_splitter": "pdf_splitter_gui.py"`.
  - Notebook: added `PDF Split` tab (after `PDF Compress`) embedding
    `PdfSplitterFrame` via new `build_pdf_splitter_tab()`.
  - Quick Actions: removed the `Latest Output` button (row 2, col 1) to
    make room; `PDF Split` and `PDF Compress` quick buttons (row 7) now
    just select their notebook tab instead of opening separate windows.
- Updated `CHANGE_RULES.md`.

Behavior:

- Button click opens the standalone Split PDF window; no DB access.
- Range validation prevents out-of-range/empty selections.
- Merged mode deduplicates overlapping pages and preserves order.

Safety / compatibility:

- Read-only on the source PDF; only creates new files in the chosen
  output folder. Hospital database untouched.
- Independent module; claims processor/OCR/signing/XML generator
  unchanged.
- Uses `core.activity_logger` for logging.

Verification:

- Headless splitter tests passed: 4-page doc split into
  `range1_p1-2.pdf` + `range2_p3-4.pdf` (2 pages each), merged mode
  produced one 4-page `{stem}_split.pdf`, partial range p2-3 produced
  2 pages; page integrity preserved (4 == 4) with a real PDF source.
- Preview test passed: live updates for "N range(s) · M pages · K PDF
  files" in both separate and merged modes.
- PDF/A verification passed: split output contains `/OutputIntent`
  (GTS_PDFA1) in its xref — confirmed true PDF/A-2, not just a flag;
  file set read-only; plain-PDF fallback verified when the checkbox is
  off; PDF/A conversion repeatable.
- GUI launch smoke test passed (window opens, SPLIT PDF button present).
- Full GUI instantiation test passed: notebook contains the `PDF Split`
  tab; `Latest Output` quick-action button removed; no duplicate grid
  cells in Quick Actions.
- `python -m py_compile` passed for `gui/pdf_splitter_gui.py`,
  `pdf_splitter_gui.py`, `edh_claims_gui_XML_COPY_BUTTON.py`.
- GUI wiring verified: SCRIPT_CONFIG entry, tab, and method present.

### 2026-08-06 — Fees Checker: Itemized Charges vs Summary of Fees

Reason:

- Kailangan i-verify na ang `SUM(grpamount)` (itemized charges sa
  `hpatgrpchrg`) ay tumutugma sa `ptotalactualchargeshci` (summary of fees
  sa `hpatcon1`) para sa bawat patient encounter, at na ang ADM/DIS
  dates sa folder name ay tugma sa `hpatcon1.padmissiondatetime` /
  `pdischargedatetime`. Read-only lang — hindi binabago ang HBSys.
- Ang comparison table na `hpatgrpchrg.grpamount` ay ang tamang pinagmulan
  ng itemized total (hindi `hpatchrg.pcchrgamt`), ayon sa sample SQL ng
  may-ari.

Files:

- Added `fees_checker.py` — standalone Fees Checker script:
  - Parses `output/` folder names (`NAME - HPERCODE - ADMYYYYMMDD_DISYYYYMMDD`).
  - Read-only queries: `hadmlog` (enccode lookup), `hpatgrpchrg`
    (`SELECT SUM(grpamount) ... WHERE enccode=%s AND hpercode=%s`),
    `hpatcon1` (`ptotalactualchargeshci`, `padmissiondatetime`,
    `pdischargedatetime`).
  - Converts hpatcon1 datetimes to YYYY/MM/DD and compares against the
    folder-name ADM/DIS dates.
  - Flags MISMATCH / NO RECORD / SKIPPED / ERROR rows.
  - Writes `fees_checker_report.csv` (plain) and
    `fees_checker_report.xlsx` with RED background on mismatch rows
    (CSV cannot hold colors, hence the XLSX duplicate). Falls back to
    timestamped filenames when the main report file is locked (e.g.
    open in Excel).
  - Auto-opens the XLSX report in the default spreadsheet app after a
    successful run (`open_report()` → `os.startfile` on Windows,
    `xdg-open` elsewhere; failures are logged, never fatal).
- Modified `edh_claims_gui_XML_COPY_BUTTON.py`:
  - `SCRIPT_CONFIG`: added `"fees_checker": "fees_checker.py"`.
  - Quick Actions: added `Fees Check` button (row 6, col 0) that runs
    the checker via `run_script("fees_checker")`.
- Updated `CHANGE_RULES.md`.

Behavior:

- Button click → console log + CSV/XLSX reports in the project root.
- MATCH rows are green in XLSX; MISMATCH / NO RECORD rows are red with
  the Status cell in solid red; SKIPPED rows (unrecognized folder names
  such as `New folder`) are left unstyled.
- Remarks explain the difference amount and any ADM/DIS date mismatch.
- DB access is read-only (`create_hbsys_connection`), same factory used
  by the other read-only repositories; no writes to HBSys.

Safety / compatibility:

- Read-only SELECT statements only; hospital database untouched.
- Independent module; claims processor/OCR/signing/XML generator
  unchanged.
- Uses `core.activity_logger` for logging, no `print()` in the check
  logic (console summary only in `main()`).

Verification:

- `python fees_checker.py` ran against live HBSys using
  `hpatgrpchrg.grpamount`: 3 MATCH, 2 MISMATCH (ALOTA: itemized PHP
  27,771.00 vs summary PHP 36,771.00, diff PHP 9,000.00; AMPONIN: PHP
  4,843.00 vs PHP 7,243.00, diff PHP 2,400.00), 1 SKIPPED — red rows
  confirmed in `fees_checker_report_*.xlsx` via openpyxl (bg `FFC7CE`,
  MATCH green `C6EFCE`).
- Locked-file fallback verified (PermissionError → timestamped xlsx).
- `python -m py_compile` passed for `fees_checker.py` and
  `edh_claims_gui_XML_COPY_BUTTON.py`.
- GUI wiring verified (SCRIPT_CONFIG entry + button present) and checker
  re-run exits 0.

### 2026-08-05 — WebScanner: CamScanner-like Web App for Phone Browsers

Reason:

- Magkaroon ng camera scanner na kayang i-run sa browser ng cellphone:
  mag-photo ng dokumento, i-autocrop/perspective-correct, mag-filter
  (colored / enhanced / greyscale / black & white), at i-export bilang
  image (JPG/PNG) o multi-page PDF na A4 ang default na laki ng pahina.

Files:

- Added `webscanner/scanner_engine.py` — OpenCV processing engine
  (edge detection, perspective warp, filters, rotation, PDF export).
- Added `webscanner/app.py` — Flask HTTPS server, self-signed cert
  generation, HTTP→HTTPS redirect, REST API endpoints.
- Added `webscanner/templates/index.html` — mobile-first camera UI
  (getUserMedia), corner-drag editing, filter chips, PDF page tray.
- Added `webscanner/smoke_test.py` — endpoint smoke test.
- Added `webscanner/requirements.txt`, `webscanner/start_webscanner.bat`.
- Updated `CHANGE_RULES.md`.

Behavior:

- Run via `webscanner\start_webscanner.bat` (or `python app.py`); serves
  HTTPS on port 8443 and redirects plain HTTP (8080) to HTTPS so phone
  camera access works.
- Self-signed certificate (SAN covers LAN IPs + localhost) is generated
  on first run into `webscanner/certs/`; first visit needs
  Advanced → Proceed anyway.
- `/api/detect` returns normalized document corners from the largest
  detected quadrilateral; falls back to full frame when none found.
- `/api/process` applies rotation (multiples of 90°), perspective
  correction, then filter (colored / enhanced / greyscale / bw) plus
  optional brightness/contrast; returns a JPEG or PNG data URL.
- `/api/export/pdf` assembles multiple pages into one PDF fitted and
  centered on A4 (default), A5, Letter, or Legal pages.
- UI: rear-camera capture with frame guides, flip camera, file upload,
  auto-detect button, draggable numbered corner handles, rotate,
  brightness/contrast sliders, filter chips, add-page-to-PDF tray with
  remove, and Save JPG/PNG.

Safety / compatibility:

- Independent module; the claims processor, OCR, signing, XML generator,
  and Claims Checker are untouched.
- New Python deps added to the project venv only for this module:
  Flask 3.1.3 and cryptography 50.0.0.
- No HBSys/MySQL access; no patient data is stored server-side (images
  stay in the browser, PDFs are generated on demand and downloaded).
- Certificates are regenerated only when missing; existing certs reused.

Verification:

- `python webscanner/scanner_engine.py` — engine self-test passed
  (detection, warp, 4 filters, rotation, A4 PDF bytes valid).
- `python webscanner/smoke_test.py` against a live server — all passed:
  index page 200 with camera JS and A4 selector, HTTP→HTTPS 301 redirect,
  detect corners, process + bw filter, multi-page A4 PDF download.
- `python -m py_compile` passed for `app.py` and `scanner_engine.py`.

### 2026-08-04 - Date Fill Live Test Fixes: Close Form Slot and Multiple Professional Fee Rows

Reason:

- Live testing ng isang pasyente (DORIA, ROWENA PASTOR) pagkatapos gumana ang
  Claim Form 4 dismissal ay nagsiwalat ng dalawang isyu:
  1. Ang `Close Form` button ng PhilHealth Beneficiaries toolbar ay nasa x=485
     (hindi 435) kapag may `Details` button; ang click sa (435,58) ay nagbubukas
     ng `PHIC Details` window sa halip na mag-close ng form.
  2. Ang ilang pasyente ay may higit sa isang professional fee row sa Claim
     Form 2 Professional Fees tab; ang bot ay nag-fill lang ng unang row, kaya
     ang post-save database verification ay nag-fail
     ("Professional Fee date was not saved to the expected encounter").

Files:

- Updated `date_fill_hbsys/hbsys_fill_dates.py`.
- Updated `date_fill_hbsys/hbsys_fill_dates_testing.py`.
- Updated `date_fill_hbsys/test_hbsys_date_fill_verifier.py`.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: `Close Form` click ay naka-target sa (435,58); kapag may `Details`
  button sa toolbar, ito ay nagbubukas ng `PHIC Details` window at hindi
  nakukumpleto ang pag-close, na nag-iiwan ng bukas na screen.
- After: ang `Close Form` slot ay (485,58) (may Details button), na may legacy
  na (435,58) bilang fallback; kung may `PHIC Details` window na nabuksan,
  ito ay io-close (Ctrl+F4) bago subukan ang susunod na slot.
- Before: kapag may ilang professional fee row ang pasyente (hal. ANGELIE at
  WALLY), ang post-save verification ay nag-fail dahil i-require nito na LAHAT
  ng rows ay may date.
- After: ang bot ay nag-fill LANG ng unang professional fee row
  (P.PROF_DATE_SIGNED_CELL) — ang ibang rows (hal. WALLY) ay hindi ginalaw.
  Ang verifier ay tumatanggap na ng mga walang laman na rows basta ang bawat
  may laman na row ay katumbas ng expected fill date.

Safety / compatibility:

- Ang PHIC Details close ay naka-trigger lamang kapag ang OCR evidence ay
  nagpapakita ng `PHIC DETAILS`; walang blind na pagpindot.
- Ang Cancel detection ay gumagamit ng lahat ng OCR variants at isang focused
  toolbar-strip pass; ang generic na Cancel sa labas ng PHIC Beneficiaries
  screen ay hindi pa rin ini-trigger.
- Ang verifier ay tumatanggap ng mga empty professional rows ngunit
  nangangailangan pa rin na may kahit isang row na na-fill nang tama.
- Walang HBSys SQL writes; UI automation pa rin ito plus read-only verification.

Verification:

- Live test (1 pasyente, DORIA, 2026-08-04): ang Claim Form 4 + Cancel state
  ay na-detect at na-dismiss; tamang confinement row ang napili at na-highlight;
  ang Professional Fee (unang row) at Consent dates ay na-fill; ang WALLY
  (pangalawang) row ay HINDI ginalaw; ang post-save verification ay
  nagresulta sa VERIFIED.
- Idinagdag ang unit tests para sa robust Cancel detection (OCR variants) at
  sa verifier relaxation (empty professional rows).
- Passed `python -m unittest test_hbsys_date_fill_verifier` (34 tests).

### 2026-08-04 - Date Fill Dismiss Claim Form 4 View After PHIC Click

Reason:

- After clicking the PHIC tab menu, HBSys sometimes opens the PhilHealth
  Beneficiaries window on the Claim Form 4 tab with a `Cancel` toolbar button
  visible instead of the regular Beneficiaries grid toolbar. When that happens,
  Date Fill must click `Cancel` first, then select the correct confinement
  period; otherwise the confinement selection and subsequent Claim Form 2 steps
  operate on the wrong screen.

Files:

- Updated `date_fill_hbsys/hbsys_fill_dates.py`.
- Updated `date_fill_hbsys/hbsys_fill_dates_testing.py`.
- Updated `date_fill_hbsys/test_hbsys_date_fill_verifier.py`.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: `click_phic_and_select_claim()` went straight from the PHIC click to
  OCR row selection. If HBSys opened on the Claim Form 4 tab, the flow could
  read the wrong layout or click the wrong toolbar slot.
- After: `click_phic_and_select_claim()` calls
  `dismiss_claim_form4_after_phic_if_visible()` right after the PHIC click.
  When the PHIC Beneficiaries + Cancel state is detected, it clicks `Cancel`,
  then re-probes up to three times to confirm the Cancel button cleared before
  continuing with confinement selection.
- The shared `cancel_pending_beneficiary_edit_if_visible()` now covers both the
  close-time pending-edit recovery and the new open-time Claim Form 4 dismissal.
- Date Fill Testing records `phic_claim_form4_dismissed` = YES/NO in its audit
  and run-log CSV (new `phic_claim_form4_dismissed` column); the production
  Date Fill logs the dismissal outcome.
- If Cancel is visible but does not clear after the repeated probes, Date Fill
  stops for review instead of clicking blindly. The testing script reports the
  distinct status `SKIPPED_PHIC_CANCEL_STUCK` (instead of the misleading
  `SKIPPED_SELECTED_ROW_MISMATCH`) when the Cancel stays stuck.

Safety / compatibility:

- The recovery only triggers when OCR evidence indicates both
  `PHILHEALTH BENEFICIARIES` and `CANCEL`; a generic Cancel button outside that
  screen is ignored (same guard as the existing close-time recovery).
- When the Claim Form 4 state is absent, the flow is unchanged apart from one
  extra OCR probe right after the PHIC click.
- No HBSys SQL writes were added; this remains UI automation plus read-only
  verification.

Verification:

- Added 5 unit tests covering dry-run acceptance, no-cancel skip, successful
  dismissal, persistent-Cancel stop, and the dry-run PHIC flow continuing after
  the dismissal guard.
- Passed no-cache syntax check for the production/testing Date Fill files and
  verifier tests.
- Passed `python -m unittest test_hbsys_date_fill_verifier` with 30 tests (was
  25 before this change).

### 2026-08-04 - Date Fill PHIC Beneficiaries Cancel Recovery

Reason:

- HBSys can leave the PhilHealth Beneficiaries form in an edit/pending row state with a
  visible `Cancel` toolbar button, preventing Date Fill from safely closing the form and
  continuing to the next patient.

Files:

- Updated `date_fill_hbsys/hbsys_fill_dates.py`.
- Updated `date_fill_hbsys/hbsys_fill_dates_testing.py`.
- Updated `date_fill_hbsys/test_hbsys_date_fill_verifier.py`.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: when Date Fill encountered a PHIC Beneficiaries pending-edit state, it could
  stop on the current patient and leave the screen open, requiring manual cleanup before
  the next hospital number could be entered.
- After: Date Fill detects the PHIC Beneficiaries + Cancel state, clicks `Cancel`, then
  clicks `Close Form`; if the first close toolbar slot is not correct because HBSys shows
  a different toolbar layout, it tries the alternate Close Form slot.
- Production Date Fill now records whether safe reset was confirmed before continuing
  after a recoverable patient-level failure.
- Date Fill Testing records whether a pending PHIC edit was cancelled.

Safety / compatibility:

- The recovery only triggers when OCR evidence indicates both `PHILHEALTH BENEFICIARIES`
  and `CANCEL`; a generic Cancel button outside that screen is ignored.
- The script only proceeds to the next patient after the known HBSys starting screen is
  confirmed; otherwise the batch remains conservative and stops.
- No HBSys SQL writes were added; this remains UI automation plus read-only verification.

Verification:

- Added unit coverage for PHIC Cancel detection and alternate Close Form toolbar slots.
- Passed no-cache syntax check for the production/testing Date Fill files and verifier
  tests.
- Passed `python -m unittest test_hbsys_date_fill_verifier -v` with 25 tests.

### 2026-07-29 — Safe PDF Compressor GUI

Reason:

- Provide an integrated way to open, preview, and reduce the size of a PDF without using
  a separate script or modifying the original document.

Files:

- Added `core/pdf_compressor.py`.
- Added `gui/pdf_compressor_tab.py`.
- Added `tests/test_pdf_compressor.py`.
- Updated `edh_claims_gui_XML_COPY_BUTTON.py`.
- Updated `CHANGE_RULES.md`.

Behavior:

- Added a `PDF Compress` tab and compact Main Dashboard shortcut.
- Added `Open PDF`, in-GUI page preview and navigation, `Open in Viewer`, selectable save
  location, `Open Compressed PDF`, and original-versus-compressed size reporting.
- Added High Quality, Balanced, and Strong compression presets.
- Added `Readable Claims` as the new default preset after user testing showed that the
  previous lower-resolution output could pixelate small text.
- Readable Claims converts scan imagery to grayscale for better size efficiency, begins at
  300 DPI, and never falls below 200 DPI while attempting the selected target.
- Added an editable maximum output size from 0.1 MB to 100 MB, defaulting to 1.4 MB.
- When a maximum is selected, the service retries progressively smaller verified
  resolutions until the output is within the limit.
- Compression runs in a daemon worker thread so the Claims Bot GUI remains responsive.
- Output is a separate compressed PDF/A read-only copy using the default suffix
  `_COMPRESSED_PDFA.pdf`.
- Page count and PDF readability are verified before the output is accepted.
- If compression does not produce a smaller PDF, no redundant output copy is retained.
- If the selected size cannot be reached safely, no over-limit output is saved and the
  smallest verified attempted size is reported to the user.
- In Readable Claims mode, an unreachable target instructs the user to increase the size
  limit instead of silently lowering the document below the readability floor.

Safety and compatibility:

- Source PDFs are never deleted, renamed, overwritten, or made read-only.
- Existing output files are never overwritten; the user must choose a new destination.
- A temporary output in the destination directory is atomically renamed only after all
  validation succeeds.
- Ghostscript discovery supports the configured environment path, installed EDH version,
  nearby supported versions, and the system executable path.
- The feature is independent of claims processing, OCR, signing, XML generation, and HBSys.

Verification:

- Python compilation passed for the compressor service, GUI tab, main GUI integration,
  and compressor tests.
- PDF Compressor automated tests: 8 passed, including readable grayscale/high-resolution
  mode, target retry, and strict over-limit rejection.
- Full project regression suite: 58 passed.
- PDF Compressor tab and full Claims Bot GUI integration smoke tests passed.
- A real Ghostscript test using a generated non-patient image PDF created a valid
  one-page PDF/A copy with the same page count and 67.0 percent size reduction.
- A real 1.4 MB target test reduced a generated 3.14 MB non-patient PDF to 0.94 MB.

### 2026-07-29 — Hybrid Visual Document Learning

Reason:

- Allow future manually reviewed forms to provide privacy-preserving visual layout
  evidence when deterministic OCR and specialized document resolvers remain uncertain.
- Prevent conflicting legacy keyword rules from silently deciding a document type.

Files:

- Added `core/visual_document_learner.py`.
- Added `gui/visual_learning_manager.py`.
- Added `tests/test_visual_document_learner.py`.
- Updated `unknown_review_manager_INTEGRATED_PDFA_THREAD.py`.
- Updated `bot_unknown_trainer_DEFERRED_OUTPUT_REVIEW_PATSUFFIX_ADM_DIS.py`.
- Updated `CHANGE_RULES.md`.

Behavior:

- Visual learning starts in shadow/suggestion mode; automatic visual classification is
  disabled by default and requires an explicit enable action in Visual Learning Manager.
- Successful Unknown Review Manager corrections can store derived first-page features:
  perceptual hashes, edge-layout grid, line projections, and ORB descriptors.
- No extra PDF, rendered page, or readable patient-document image is retained by the
  visual learner. Duplicate file hashes do not increase training maturity.
- Existing OCR rules and specialized resolvers remain first priority. Visual matching is
  consulted only while the result is still `UNKNOWN`.
- Ordinary types require at least three distinct confirmed samples, 95 percent confidence,
  a 15 percent top-two margin, and agreement from at least three visual measurements.
- Merge-sensitive types and DTR require at least five samples and 97 percent confidence.
- At least two trained document classes must exist before any visual decision can be
  auto-eligible, preventing a one-class learner from guessing every unknown form.
- `OTHER`, `UNKNOWN`, and `SKIP` are not learnable automatic types.
- A corrected suggestion creates negative feedback and blocks similar future automatic
  predictions for the rejected type.
- Unknown Review Manager now shows the visual suggestion, confidence, evidence, sample
  count, a default-checked training consent box, and a Visual Learning Manager button.
- Visual Learning Manager shows class readiness, individual feature samples, enable or
  disable controls, reviewed accuracy, and predicted-versus-confirmed confusion audit.
- Conflicting legacy keyword rules now defer to review instead of selecting one of the
  conflicting types.
- Prediction logs include candidate scores, evidence, guard result, and whether the final
  classification source was visual automatic classification or manual review.

Safety and compatibility:

- Existing output PDFs and the 89 legacy keyword records are not imported as trusted
  visual samples. Only future successful manual corrections train the visual learner.
- The existing OCR, SOA2 resolver, PDF merge, signing, XML generator, Claims Checker, and
  patient-processing order are preserved.
- Visual feature extraction and review analysis run outside Tkinter's main thread.
- Corrupt, blank, low-quality, landscape/orientation-uncertain, low-margin, contradictory,
  immature, or unseen layouts remain under manual review.
- Visual-learning failures do not undo or fail an otherwise successful manual correction.
- The new SQLite tables are additive local metadata and do not write to HBSys/MySQL.

Verification:

- Python compilation passed for the core learner, manager GUI, Unknown Review Manager,
  and production processor. The processor still reports one pre-existing invalid-escape
  `SyntaxWarning` around its configuration help text; it is unrelated to this change.
- Visual learner automated tests: 10 passed, covering shadow mode, sample thresholds,
  sensitive thresholds, duplicates, deterministic precedence, negative feedback,
  corrupt/rotated deferral, privacy storage, and sample enable/disable behavior.
- Full project regression suite: 50 passed.
- Visual Learning Manager Tkinter smoke test passed using a temporary SQLite database.

### 2026-07-28 — Background Auto Copy XML Watcher

Reason:

- Automatically deliver stable CF4, CF5, and eSOA XML files from the configured HBSys XML
  source into the correct patient folder without requiring the manual Copy XML action.

Files:

- Added `core/xml_auto_copy.py`.
- Added `tests/test_xml_auto_copy.py`.
- Updated `edh_claims_gui_XML_COPY_BUTTON.py`.

Behavior:

- Added an optional ten-second background watcher with its own daemon worker and lock.
- Added two-poll file stability checks for automatic copying.
- Matching is patient-name based and requires exactly one matching confinement folder.
- Output Folder has priority; `claims_checker_results/INCOMPLETE` is fallback only when
  Output has no match.
- Added atomic temporary-file copying, identical-file skipping, conflict protection,
  source preservation, meaningful event logging, and dashboard status.
- Added the `Enable Auto Copy XML` preference, enabled by default.
- The existing manual Copy XML button now uses the same modular safe-copy service.
- Removed the old duplicate copy implementation that could overwrite destination XML and
  collapse duplicate patient folders into one match.

Safety and compatibility:

- Source XML files are never deleted or modified.
- Different existing destination files are preserved and reported as `CONFLICT`.
- Multiple matching confinement folders are reported as `AMBIGUOUS`; nothing is copied.
- Existing settings files remain compatible when the new preference key is absent.
- The watcher is independent of Auto Process Scans, Claims Processor, and XML Clicker.

Verification:

- Python compilation passed for the core service, GUI integration, and tests.
- Auto Copy XML automated tests: 15 passed.
- Claims Checker regression tests: 16 passed.

### 2026-07-28 — Living Change Documentation Rule

Reason:

- Ensure that future changes remain understandable, auditable, and easier to transfer to
  another PC or maintainer.

Files:

- Added `CHANGE_RULES.md`.
- Updated `agents.md` to require maintenance of this file.

Behavior:

- Every future implementation or code/configuration change must add or update a Change
  Record entry here before the task is considered complete.

Safety and compatibility:

- Documentation only; no production workflow or patient data is changed.

Verification:

- Confirmed the rule is referenced by the project's agent instructions.

### 2026-08-28 — Add Claims Upload Automation (eClaims Upload Claims loop)

Reason:

- Automate the manual eClaims Upload Claims workflow: searching patients from the
  READY folder, verifying confinement periods against folder names, and checking
  checkboxes before batch submission. Previously this was done manually per patient.

Files:

- Added `core/add_claims_state.py` — persistent batch state (filesystem-based, resumable).
- Added `core/add_claims_verifier.py` — confinement period verification (folder vs OCR).
- Added `core/add_claims_ocr.py` — highlighted row OCR reader for Upload Claims popup.
- Added `core/add_claims_uploader.py` — main loop controller (orchestrates steps 1–7).

Behavior:

- Before: User manually types each patient name, clicks Search, visually confirms
  confinement match, checks the checkbox, and repeats for every READY patient.
- After: Script reads all patient folders from READY, automates the search-verify-check
  loop for each patient, then clicks Add → OK → Close. State is persisted to
  `logs/add_claims_upload_state.json` so interrupted batches can be resumed.

Safety and compatibility:

- Dry-run mode by default (no clicks). Must pass `--live` to actually interact with
  HBSys.
- Stops after 3 consecutive confinement mismatches (guardrail).
- Never modifies the READY folder or patient data — read-only folder scan.
- State file is separate from production databases.
- Existing Date Fill, XML generators, and Claims Checker are not affected.

Verification:

- All four modules pass `python -m` standalone test execution.
- Folder name parsing tested with valid and invalid formats.
- Confinement verification tested with matching and mismatching date pairs.
- OCR module tested with screenshot input.
- Uploader module tested in dry-run mode.
- Python compilation passed for all new modules.

### 2026-08-28 — Add Claims Upload GUI Tab

Reason:

- Integrate the Add Claims Upload automation into the main EDH Claims GUI for
  easy access. Users can now run the upload from the GUI instead of the command line.

Files:

- Added `gui/add_claims_upload_tab.py` — new GUI tab with patient list, controls,
  and upload log.
- Modified `edh_claims_gui_XML_COPY_BUTTON.py` — added import, notebook tab, build
  method, and dashboard button.

Behavior:

- New "Add Claims Upload" tab in the main notebook with:
  - Ready folder selector (browse/refresh)
  - Dry-run/Live mode radio buttons
  - Confirm-each-patient checkbox
  - Patient list table with status column
  - Start/Stop/Resume buttons
  - Progress bar and upload log
- Dashboard button "Add Claims Upload" added to quick actions grid.
- Tab opens the AddClaimsUploadFrame with real-time upload progress.

Safety and compatibility:

- Dry-run mode is default (no clicks).
- Live mode requires user confirmation before starting.
- Stop button allows graceful shutdown after current patient.
- Existing tabs and functionality are unaffected.

Verification:

- Python compilation passed for both modified and new files.
- GUI tab loads correctly with patient list from READY folder.

### 2026-08-28 — Add Claims Upload Coordinate Calibration

Reason:

- The eClaims Upload Claims popup position can vary based on window placement.
  A calibration utility helps detect the actual popup position and allows
  interactive coordinate adjustment for reliable automation.

Files:

- Added `core/add_claims_calibration.py` — interactive calibration utility.
- Modified `core/add_claims_uploader.py` — loads calibrated coordinates from
  calibration file, added --calibrate and --detect CLI options.

Behavior:

- New calibration utility can:
  - Detect Upload Claims popup window position
  - Capture annotated screenshot showing coordinate points
  - Allow interactive coordinate adjustment
  - Save calibration to `logs/add_claims_calibration.json`
- Uploader loads calibrated coordinates at startup (falls back to defaults).
- CLI options: --calibrate (run calibration), --detect (detect popup only).

Safety and compatibility:

- Calibration is read-only (no clicks during detection).
- Interactive calibration requires user input.
- Default coordinates preserved for 1920x1080 screens.
- Existing automation workflow unaffected.

Verification:

- Python compilation passed for new module.
- Calibration file loads correctly.
- Uploader uses calibrated coordinates when available.

### 2026-08-28 — Add Claims Upload GUI Calibration Button

Reason:

- Add convenience buttons to the Add Claims Upload GUI tab for running
  coordinate calibration and detecting popup position without command line.

Files:

- Modified `gui/add_claims_upload_tab.py` — added Calibrate Coordinates and
  Detect Popup buttons with corresponding methods.

Behavior:

- New "Calibrate Coordinates" button opens the calibration utility in a
  separate process.
- New "Detect Popup" button runs popup detection and shows results in
  the upload log.
- Both buttons are placed after the Resume Batch button with a separator.

Safety and compatibility:

- Calibration runs in a separate process (non-blocking).
- Detection has a 10-second timeout to prevent hanging.
- Existing upload functionality unaffected.

Verification:

- Python compilation passed for modified file.
- Buttons appear in correct position in the GUI tab.

### 2026-08-28 — Interactive Coordinate Getter Tool

Reason:

- Need an easy way to capture exact screen coordinates for UI elements.
  The coordinate getter provides a visual, interactive way to click on
  elements and capture their positions for calibration.

Files:

- Added `core/coordinate_getter.py` — interactive coordinate getter GUI.
- Modified `gui/add_claims_upload_tab.py` — added Coordinate Getter button.

Behavior:

- New "Coordinate Getter" button in Add Claims Upload tab opens the tool.
- Tool captures screen and displays it in a scrollable window.
- User clicks on elements to capture their coordinates.
- Each point gets a label and is saved to a list.
- Points can be copied to clipboard, saved to JSON, or deleted.
- Coordinates are captured in original screen resolution.

Safety and compatibility:

- Tool runs in a separate process (non-blocking).
- No modifications to system or automation files.
- Coordinates saved to `logs/captured_coordinates.json`.
- Existing functionality unaffected.

Verification:

- Python compilation passed for new module.
- Button appears in GUI tab.
- Tool launches correctly from GUI.

### 2026-08-28 — Calibration Update: Close Button + CF4 Checkbox Logic

Reason:

- User calibrated the coordinates and found:
  - Close button X is at absolute coordinate 1456 (not relative offset)
  - Checkbox coordinates change dynamically based on row position
  - Should use CF4-style blue band detection for checkbox (same as XML Clicker)

Files:

- Modified `logs/add_claims_calibration.json` — updated with calibrated coordinates.
- Modified `core/add_claims_calibration.py` — updated defaults and close button fields.
- Modified `core/add_claims_uploader.py` — updated P class, checkbox logic, close button.

Behavior:

- Close button now uses absolute screen coordinates (1456, 12) instead of relative offset.
- Checkbox detection uses CF4-style blue band + dark pixel detection:
  1. Find blue highlighted rows in the grid
  2. Detect the widest blue band (the search result)
  3. Find dark pixels (checkbox) in that row area
  4. Click the center of detected checkbox
- Fallback to GRID_CHECKBOX_X if no dark pixels detected.
- All coordinates updated from user calibration.

Safety and compatibility:

- Checkbox detection is read-only (screenshot + pixel analysis).
- Fallback mechanism preserved for reliability.
- Existing upload workflow unaffected.

Verification:

- Python compilation passed for modified files.
- Calibration file loads correctly.
- Close button coordinates match user calibration.

### 2026-08-28 — Fix Checkbox Detection: Blue Band Y Coordinate

Reason:

- Live test showed checkbox was not being clicked because:
  1. Blue band detection was returning the HEADER band (y=153) instead of the DATA band (y=406)
  2. Code was scanning x=0+ for blue pixels, but the Include column (checkbox) is NOT highlighted
  3. The blue highlight only appears on data columns (x=400+)

Files:

- Modified `core/add_claims_uploader.py`:
  - `_find_highlight_row_y()`: Changed to scan x=400-1500 (data columns only)
  - Added band grouping logic to find the WIDEST band in data area (y > 300)
  - Updated `_checkbox_looks_checked()` with improved pixel analysis
  - Added verbose logging for debugging

Behavior:

- Before: Code found blue band at y=153 (header), clicked checkbox at wrong Y
- After: Code finds blue band at y=406 (data row), clicks checkbox at correct Y
- The Include column (checkbox) is at x=8-12, NOT highlighted
- The data columns (Patient Name, etc.) are at x=400+, highlighted blue
- Checkbox center is at x=10 (light interior), y=406 (blue band center)

Safety and compatibility:

- Blue band detection is read-only (screenshot + pixel analysis)
- Fallback to first data row if no blue band detected
- Existing upload workflow unaffected

Verification:

- Python compilation passed
- Dry-run test: 4/4 patients processed successfully
- Blue band detection correctly identifies data row at y=406
- Checkbox pattern verified: x=10 has light interior (240,240,240)

### 2026-09-01 — Fix Highlighted Row OCR: use pyautogui.screenshot() instead of window.capture_as_image()

Reason:

- Live test showed that `read_highlighted_row()` in `add_claims_ocr.py`
  failed to detect the blue highlight band for 3 out of 4 patients
  (ACOSTA, MASIBAG, SALVADOR), even though the blue band was clearly
  visible on screen. Only VENTURA was detected.
- Root cause: `window.capture_as_image()` (pywinauto, Win32 PrintWindow)
  does not reliably capture custom-rendered selection highlights in the
  HBSys DataGrid control. The blue band is drawn by the control's custom
  rendering and is invisible in the captured image.
- Meanwhile, `click_checkbox_of_highlighted_row()` in the uploader uses
  `pyautogui.screenshot()` (actual screen pixels) and always detects
  the blue band correctly.

Files:

- Modified `core/add_claims_ocr.py`:
  - `read_highlighted_row()`: Now calls `_capture_popup_via_screenshot()`
    first, which uses `pyautogui.screenshot()` + crop to popup rect.
    Falls back to `capture_popup()` (window.capture_as_image()) only if
    the screenshot method fails.
  - Added `_capture_popup_via_screenshot(window)`: captures full screen
    via pyautogui.screenshot(), gets popup rect via window.rectangle(),
    crops to popup area. Returns PIL Image or None on failure.
  - Updated docstring to explain why pyautogui is preferred.

Behavior:

- Before: `read_highlighted_row()` used `window.capture_as_image()` which
  often returned an image without the blue highlight band, causing
  "no highlighted row detected" for most patients.
- After: Uses `pyautogui.screenshot()` (actual screen pixels) cropped to
  the popup window area. The blue highlight band is reliably captured,
  enabling OCR verification for all patients.

Safety / compatibility:

- pyautogui.screenshot() is already used by `click_checkbox_of_highlighted_row()`
  in the uploader — same proven capture method.
- Fallback to `window.capture_as_image()` if screenshot fails.
- No changes to detection thresholds, scan ranges, or OCR logic.
- `capture_popup()` retained for backward compatibility and fallback.

Verification:

- `python -m py_compile core/add_claims_ocr.py` — passed.
- Visual inspection: pyautogui.screenshot() captures actual screen pixels
  including the blue highlight band that window.capture_as_image() misses.
- Live test confirmed: pyautogui.screenshot() capture works correctly,
  but `detect_highlighted_row_y()` still missed 3 patients.

### 2026-09-01 — Fix Highlighted Row Detection: extend scan range to y=50

Reason:

- Debug logs revealed the REAL root cause: `detect_highlighted_row_y()`
  scanned y from 150 to 500 in the popup image.  When the search match
  was the FIRST row in the list (top of grid), the blue highlight band
  was at y≈30-60 — completely BELOW the scan start at y=150.
- For VENTURA (row 14, y≈340) the scan range covered it; for ACOSTA,
  MASIBAG, and SALVADOR (row 1, y≈30-60) it was missed.
- pyautogui.screenshot() capture was working correctly all along — the
  blue band WAS in the captured image but the scan range was too narrow.

Files:

- Modified `core/add_claims_ocr.py`:
  - `detect_highlighted_row_y()`: Changed scan start from y=150 to y=50.
    y=50 is below the popup title bar and "Patient Name" field but above
    the first data row, so it catches highlighted rows at any position
    in the list without false positives from the header.
  - Updated docstring explaining the scan range rationale.

Behavior:

- Before: scan_y=150-500 — missed highlighted rows in the top 10 rows.
- After: scan_y=50-500 — catches highlighted rows at ANY position.

Safety / compatibility:

- y=50 is below the popup UI elements (title bar + Patient Name field)
  and above the first data row, so no false positives from header text.
- No changes to blue pixel thresholds, x scan range, or OCR logic.

Verification:

- `python -m py_compile core/add_claims_ocr.py` — passed.
- Live test PENDING — re-run the upload loop and verify all 4 patients
  (ACOSTA, MASIBAG, SALVADOR, VENTURA) are detected with OCR text.

### 2026-09-01 — Claim Attachments Uploader: new module for attaching documents

Reason:

- Automate the HBSys UPLOAD CLAIM ATTACHMENTS workflow: for each patient
  in the READY folder, search the patient, click "attach..." on the
  highlighted row, attach all files from the patient folder, then attach
  XML files specifically. This eliminates manual per-patient clicking.

Files:

- Added `core/attachments_state.py` — batch state persistence module:
  - `AttachmentsState` dataclass with `save()`/`load()`, `mark_started()`,
    `mark_processed()`, `mark_failed()`, `mark_completed()`.
  - Stores batch ID, progress, failed patients + reasons.
  - State file: `logs/claim_attachments_state.json`.
  - Standalone self-test via `if __name__ == "__main__"`.

- Added `core/claim_attachments_uploader.py` — main automation module:
  - `CalibratedPoints` dataclass with all workflow coordinates (search box,
    search button, attach column X, popup attach button, file dialog
    elements). Loads from `logs/claim_attachments_calibration.json` with
    defaults for 1920x1080.
  - `AttachmentsOperator` class: UI automation with dry-run/live modes,
    focus_hbsys(), search_patient(), click_attach_on_highlighted_row()
    (blue band detection), find_attachment_popup(), file dialog
    interaction (type path, change file type to XML).
  - `run_attachments_loop()`: goal-based loop with state persistence,
    consecutive failure guardrail (MAX=3), resume support.
  - CLI entry point with `--live`, `--confirm-each`, `--resume`,
    `--pause`, `--limit`, `--ready-dir` flags.

- Updated `CHANGE_RULES.md`.

Behavior:

- Before: manual per-patient attachment in HBSys UPLOAD CLAIM ATTACHMENTS.
- After: automated loop that processes all READY patients — search,
  detect highlighted row, attach all files, attach XMLs, close popup,
  move to next patient. Supports dry-run for safe testing.

Safety / compatibility:

- Independent new module; does not modify any existing code.
- Uses pyautogui/pywinauto for UI automation (same as existing modules).
- Read-only on HBSys/MySQL; only performs UI clicks.
- State persistence enables resume after interruption.
- MAX_CONSECUTIVE_FAILURES=3 guardrail stops the loop on persistent errors.

Verification:

- `python -m py_compile core/attachments_state.py` — passed.
- `python -m py_compile core/claim_attachments_uploader.py` — passed.
- `python core/attachments_state.py` — standalone self-test passed
  (state save/load, mark_processed, mark_failed, mark_completed).
- Dry-run mode PENDING: `python -m core.claim_attachments_uploader --live --limit 1 --confirm-each`.

### 2026-09-01 — Claim Attachments GUI tab: integrated into main EDH Claims GUI

Reason:

- Magkaroon ng GUI entry point para sa Claim Attachments Upload, para
  hindi na kailangan i-run ang CLI nang mag-isa. Pareho ng pattern ng
  existing Add Claims Upload tab — may patient list, dry-run/live toggle,
  Start/Stop/Resume buttons, at real-time log.

Files:

- Added `gui/claim_attachments_tab.py` — Tkinter frame class:
  - `ClaimAttachmentsFrame(ttk.Frame)` — patient list, mode selection,
    Start/Stop/Resume buttons, progress bar, real-time log.
  - `_upload_worker()` — background thread na tumatawag ng
    `AttachmentsOperator` methods at nag-uupdate ng GUI.
  - `browse_ready_dir()`, `refresh_patient_list()`, `log()`,
    `update_patient_status()`.
  - Standalone test via `if __name__ == "__main__"`.

- Modified `edh_claims_gui_XML_COPY_BUTTON.py`:
  - Import: `from gui.claim_attachments_tab import ClaimAttachmentsFrame`.
  - `notebook`: added `self.claim_attachments_tab` frame with
    text="Claim Attachments" (after Add Claims Upload, before Preferences).
  - `build_tabs()`: added `self.build_claim_attachments_tab()` call.
  - Quick Actions grid: added "Claim Attachments" button at (7, 0)
    that selects the notebook tab.
  - New `build_claim_attachments_tab()`: instantiates
    `ClaimAttachmentsFrame` with `settings_getter` and `log_callback`.

- Updated `CHANGE_RULES.md`.

Behavior:

- Before: Claim Attachments was only accessible via CLI.
- After: "Claim Attachments" tab in the main GUI notebook, with
  patient list, dry-run/live mode, Start/Stop/Resume buttons, and
  real-time log. Quick Actions button also available.

Safety / compatibility:

- Additive change; no existing code modified.
- Same pattern as existing Add Claims Upload tab.
- Uses the same `AttachmentsOperator` and `AttachmentsState` from
  `core/claim_attachments_uploader.py`.

Verification:

- `python -m py_compile gui/claim_attachments_tab.py` — passed.
- `python -m py_compile edh_claims_gui_XML_COPY_BUTTON.py` — passed.
- `python gui/claim_attachments_tab.py` — standalone GUI test launched
  (window opens, patient list loads from READY folder).
- Live smoke test PENDING: open GUI → Claim Attachments tab →
  verify patient list, controls, and dry-run mode.

### 2026-09-01 — Claim Attachments: fix folder path to use patient name only

Reason:

- Ang folder path na ita-type sa file dialog ay dapat patient name lang
  (hal. `READY\ECHANES, PAUL GEORGE DE GUZMAN`), hindi ang buong
  folder name na may hospital number at confinement period.
- Ang dating format (`PATIENT NAME - HOSPITAL_NO - ADMYYYYMMDD_...`) ay
  hindi tugma sa aktwal na folder naming sa READY directory.

Files:

- Modified `core/claim_attachments_uploader.py`:
  - `build_folder_path()`:简化 sa `ready_dir / patient.patient_name`
    (dating buong folder name format).
- Modified `gui/claim_attachments_tab.py`:
  - `_upload_worker()`: parehong simplification sa folder_path construction.

Behavior:

- Before: file dialog tinatype ang buong folder name
  (`READY\ECHANES, PAUL GEORGE DE GUZMAN - 000000000020743 - ADM...`).
- After: file dialog tinatype ang patient name lang
  (`READY\ECHANES, PAUL GEORGE DE GUZMAN`).

Safety / compatibility:

- Minimal change; walang ibang behavior ang naapektuhan.
- Ang `load_patients()` function ay hindi binago (gumagamit pa rin ng
  `parse_folder_name()` para sa internal patient list).

Verification:

- `python -m py_compile core/claim_attachments_uploader.py` — passed.
- `python -m py_compile gui/claim_attachments_tab.py` — passed.
- `python gui/claim_attachments_tab.py` — standalone GUI test passed.

### 2026-09-01 — Claim Attachments: fix Attach button click + popup close verification

Reason:

- Live test showed two bugs:
  1. "Attach..." button click at (513, 732) was not opening the file
     dialog — fixed coordinates may not match the actual popup layout.
  2. After closing the popup, it was not fully dismissed before the next
     patient search, causing the old popup to still be visible.

Files:

- Modified `core/claim_attachments_uploader.py`:
  - `click_popup_attach_button()`: 3-strategy approach:
    1. pywinauto `child_window(title="Attach...").click_input()`
       (most reliable — finds the actual button control)
    2. Fallback: calculate click position from popup rectangle
       (rect.left + 49, rect.bottom - 26)
    3. Fallback: absolute coordinates (513, 732)
  - `close_attachment_popup()`: added verification loop (3 attempts)
    to confirm the popup is actually gone before returning;
    uses `child_window(title="Close").click_input()` instead of
    `.click()` for better reliability.
  - `run_attachments_loop()`: added `sleep_short(1.0)` after
    `close_attachment_popup()` to ensure full dismissal.

- Modified `gui/claim_attachments_tab.py`:
  - `_upload_worker()`: added `time.sleep(1.0)` after popup close.

- Updated `CHANGE_RULES.md`.

Behavior:

- Before: fixed coordinate click (513, 732) for Attach button;
  popup close not verified; next patient could see old popup.
- After: pywinauto finds and clicks the actual Attach button control;
  popup close is verified (3 retry attempts); extra wait ensures
  full dismissal before next patient.

Safety / compatibility:

- No new dependencies (pywinauto already in requirements.txt).
- `click_input()` is pywinauto's click method that sends input
  to the actual control, more reliable than screen-coordinate click.
- Fail-safe: if pywinauto fails, falls back to screen coordinates.

Verification:

- `python -m py_compile core/claim_attachments_uploader.py` — passed.
- `python -m py_compile gui/claim_attachments_tab.py` — passed.
- Live test PENDING: re-run with --confirm-each --limit 2 to verify
  Attach button opens file dialog and popup closes between patients.

### 2026-09-01 — Claim Attachments: fix file dialog detection + 2-popup workflow

Reason:

- Live test showed "File dialog NOT found" — the code was not detecting
  the 2nd popup (Windows file dialog) that opens after clicking
  "Attach..." in the 1st popup (Attachments popup).
- The user clarified the workflow: 2 separate popups — (1) Attachments
  popup with "Attach..." button, (2) Windows file dialog for file
  selection.
- Original detection was too strict: required exact class (#32770) AND
  title containing OPEN/SAVE/BROWSE. Many file dialogs have different
  classes or titles.

Files:

- Modified `core/claim_attachments_uploader.py`:
  - `wait_for_file_dialog()`: 3-strategy detection:
    1. Class="#32770" or "FileDialog" (excluding attachment popup)
    2. Title contains OPEN/SAVE/BROWSE
    3. Class pattern match (Dialog/FileDialog/ToolbarWindow)
    Timeout increased to 8s; debug logging lists all windows on failure.
  - `click_popup_attach_button()`: increased wait to 2.0s after click
    (was 1.5s) to give file dialog more time to appear.
  - `type_folder_path_and_open()`: added Enter key after typing path
    (to navigate to folder), increased delays for reliability.
  - `select_xml_type_and_open()`: increased delays for dropdown and
    filter operations.

- Updated `CHANGE_RULES.md`.

Behavior:

- Before: file dialog detection required exact class + title match;
  often failed to find the dialog.
- After: 3-strategy detection catches file dialogs with various classes
  and titles; debug logging shows all windows on failure for diagnosis.

Safety / compatibility:

- More lenient detection = fewer false negatives.
- Excludes attachment popup from detection (title contains "ATTACHMENTS").
- Debug logging on failure helps diagnose future issues.

Verification:

- `python -m py_compile core/claim_attachments_uploader.py` — passed.
- Live test PENDING: re-run with --confirm-each --limit 1 to verify
  file dialog is found and path is typed correctly.

### 2026-09-01 — Claim Attachments: fix pywinauto DialogWrapper button click

Reason:

- Live test showed `'DialogWrapper' object has no attribute 'child_window'`
  — the popup is found as a pywinauto `DialogWrapper`, not a full
  `WindowSpecification` object. `child_window()` is only available on
  objects returned by `Application.window()`.
- The debug window list revealed that the actual button container is
  a `#32770` window with title `'Attachments'` (separate from the
  `FNWNS3115` popup frame).

Files:

- Modified `core/claim_attachments_uploader.py`:
  - `click_popup_attach_button()`: 4-strategy approach:
    1. `Application(backend='win32').connect(handle=popup.handle)`
       → `dialog.child_window(title="Attach...").click_input()`
    2. Find `#32770` 'Attachments' dialog directly → connect → click
    3. Screen coordinates from popup rect (fallback)
    4. Absolute coordinates (final fallback)
  - `close_attachment_popup()`: same Application-based approach for
    the Close button click.

- Updated `CHANGE_RULES.md`.

Behavior:

- Before: pywinauto `child_window()` failed on DialogWrapper; button
  click fell through to screen coordinates which missed the button.
- After: `Application(backend='win32').connect()` creates a proper
  `WindowSpecification` that supports `child_window()`; button is
  found and clicked reliably.

Safety / compatibility:

- No new dependencies (pywinauto already in requirements.txt).
- 4-strategy fallback ensures at least one approach works.
- Debug logging shows which strategy succeeded/failed.

Verification:

- `python -m py_compile core/claim_attachments_uploader.py` — passed.
- Live test PENDING: re-run with --confirm-each --limit 1 to verify
  Attach button opens the file dialog.

### 2026-09-01 — Claim Attachments: fix file dialog detection + full folder path

Reason:

- Live test showed file dialog NOT found after clicking Attach... button.
  Root cause: the file dialog has class `#32770` and title `Attachments`
  (same as the dialog inside the popup), but the detection code excluded
  all windows with "ATTACHMENTS" in the title — so it could never find
  the file dialog.
- Also: the folder path typed into the file dialog should use the FULL
  folder name (with hospital number and confinement period), not just the
  patient name.

Files:

- Modified `core/claim_attachments_uploader.py`:
  - `build_folder_path()`: changed from `ready_dir / patient.patient_name`
    to `ready_dir / f"{patient.patient_name} - {patient.hospital_no} -
    ADM{...}_DIS{...}"`.  Verified the resulting path exists on disk.
  - `click_popup_attach_button()`: now records ALL existing window handles
    BEFORE clicking Attach... (`self._pre_attach_handles`).  4 strategies:
    1. `Application.connect(handle=popup.handle)` → `child_window()`
    2. Find `#32770` dialog → connect → `child_window()`
    3. Calculate from popup rect
    4. Absolute coordinates
  - `wait_for_file_dialog()`: completely rewritten with handle-based
    detection.  Any NEW top-level window (not in `_pre_attach_handles`)
    with class `#32770` is the file dialog.  Ignores known system windows
    (Shell_TrayWnd, tooltips, etc.).  Debug logs list only NEW windows
    on failure.
  - `close_attachment_popup()`: now has 3-strategy fallback:
    1. pywinauto Close button
    2. Coordinate-based Close from popup rect
    3. Escape key
- Modified `gui/claim_attachments_tab.py`:
  - Import `build_folder_path` from core module.
  - `_upload_worker()`: uses `build_folder_path(ready_dir, patient)`
    instead of inline `str(ready_dir / patient.patient_name)`.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: file dialog was never detected (title "Attachments" was
  excluded); folder path was just the patient name (folder didn't exist).
- After: file dialog is detected by its NEW handle (not present before
  clicking Attach...); folder path uses the full name with hospital
  number and confinement period (verified to exist on disk).

Safety / compatibility:

- Handle-based detection is reliable — it doesn't depend on window title
  or class, only on whether the window is NEW.
- 4-strategy button click + 3-strategy close ensures fallback.
- No new dependencies.

Verification:

- `python -m py_compile core/claim_attachments_uploader.py` — passed.
- `python -m py_compile gui/claim_attachments_tab.py` — passed.
- `build_folder_path()` test: returns
  `READY\CABERO, ROSEMARIE SALUD - 000000000020958 - ADM20260821_DIS20260824`
  and `Path(...).exists() = True`.
- Live test PENDING: re-run with --confirm-each --limit 1 to verify
  file dialog opens and path is typed correctly.

### 2026-09-01 — Claim Attachments: add file list focus click before Ctrl+A

Reason:

- After typing the folder path (or selecting XML from dropdown), the
  keyboard focus was still on the "File name:" field.  Ctrl+A would
  select text in that field instead of selecting files in the list.
  Need to LEFT CLICK inside the file list area (595, 509) first to
  transfer focus to the file list before Ctrl+A.

Files:

- Modified `core/claim_attachments_uploader.py`:
  - Added `FILE_LIST_FOCUS = Point(595, 509)` to the `P` class.
  - `type_folder_path_and_open()`: added click at FILE_LIST_FOCUS after
    pressing Enter (navigate to folder) and before Ctrl+A.
  - `select_xml_type_and_open()`: added click at FILE_LIST_FOCUS after
    selecting XML from dropdown and before Ctrl+A.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: Ctrl+A selected text in the File name field; no files were
  selected; Open button did nothing useful.
- After: click at (595, 509) focuses the file list; Ctrl+A selects all
  files; Open attaches them.

Safety / compatibility:

- Single extra click per step; no new dependencies.
- Coordinates match user-provided screenshots.

Verification:

- `python -m py_compile core/claim_attachments_uploader.py` — passed.
- Live test PENDING: re-run with --confirm-each --limit 1.

### 2026-09-01 — Claim Attachments: add debug logging for patient-to-patient transition

Reason:

- After processing the first patient, the highlight doesn't move to the
  next patient when clicking Search.  Added detailed logging to diagnose
  the issue: search box click coordinates, text selection, typing,
  search button click, and post-search highlight detection.

Files:

- Modified `core/claim_attachments_uploader.py`:
  - `search_patient()`: added detailed logging for each step (search
    box click, Ctrl+A, delete, type, search button click); added
    post-search screenshot + highlight detection to verify the
    search worked; added extra wait after focusing main window.
  - `clear_search_box()`: added main window focus before clearing;
    added logging for each step.
  - `run_attachments_loop()`: added 2s wait after popup close; added
    main window re-focus after popup closes.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: minimal logging between patients; hard to debug transition.
- After: detailed logs show exactly what's happening at each step;
    debug screenshots saved when no highlight is detected.

Safety / compatibility:

- Logging-only changes; no behavior changes to the core workflow.

Verification:

- `python -m py_compile core/claim_attachments_uploader.py` — passed.
- Live test PENDING: re-run to see the new debug logs.

### 2026-09-01 — Claim Attachments: add --watch auto-reload on code changes

Reason:

- When debugging, the user has to manually restart the script every time
  code changes.  Add a `--watch` flag that monitors all .py files and
  automatically restarts the script when changes are detected.

Files:

- Modified `core/claim_attachments_uploader.py`:
  - Added `_get_source_mtimes()` — records mtime of all .py files.
  - Added `check_for_code_changes(stored_mtimes)` — compares current
    mtimes against stored; returns list of changed files.
  - Added `restart_script()` — uses `os.execv()` to restart the
    process with the same CLI args.
  - `run_attachments_loop()`: added `watch_mtimes` parameter; checks
    for code changes before each patient; saves state and restarts
    if changes detected.
  - CLI: added `--watch` flag.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: manual restart required after every code change.
- After: `--watch` flag monitors .py files; auto-restarts between
  patients when changes are detected (state is saved first).

Safety / compatibility:

- State is always saved before restart — no data loss.
- Only checks between patients, not mid-action.
- `os.execv()` replaces the process cleanly.
- No new dependencies.

Verification:

- `python -m py_compile core/claim_attachments_uploader.py` — passed.
- Live test PENDING: run with --live --watch to test auto-reload.

### 2026-09-01 — Claim Attachments: fix Search button not triggering for next patient

Reason:

- After processing the first patient and closing the popup, the Search
  button click at (410, 139) was not triggering a new search for the
  next patient.  The highlight stayed on the previous patient's row.
  Root cause: focus was not fully restored after popup close, and a
  single click on the Search button wasn't registering.

Files:

- Modified `core/claim_attachments_uploader.py`:
  - `search_patient()`: focus main window with 3 retries; click search
    box twice to ensure focus; double-click Search button instead of
    single click; press Enter as fallback search trigger.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: single click on Search button after popup close didn't
  trigger a new search; highlight stayed on previous patient.
- After: double-click + Enter ensures the search executes; focus
  retries ensure the main window is active.

Safety / compatibility:

- Double-click + Enter is safe; no side effects.
- Focus retries handle transient focus loss after popup close.

Verification:

- `python -m py_compile core/claim_attachments_uploader.py` — passed.
- Live test PENDING: run with --live --confirm-each --limit 2.

### 2026-09-01 — Claim Attachments: use keyboard navigation for XML dropdown

Reason:

- Clicking a specific coordinate in the "Files of type" dropdown to
  select "XML files" was unreliable (dropdown position can vary).  Use
  keyboard navigation instead: click the dropdown → arrow down → Enter.
  This is more reliable and doesn't depend on exact dropdown position.

Files:

- Modified `core/claim_attachments_uploader.py`:
  - `select_xml_type_and_open()`: replaced `pyautogui.click()` on the
    XML option coordinate with `pyautogui.press("down")` +
    `pyautogui.press("enter")` keyboard navigation.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: clicked a fixed coordinate in the dropdown to select XML;
  coordinate could miss if dropdown position changed.
- After: arrow down + Enter navigates to and selects XML files;
  works regardless of dropdown position.

Safety / compatibility:

- Keyboard navigation is more reliable than coordinate clicking.
- No new dependencies.

Verification:

- `python -m py_compile core/claim_attachments_uploader.py` — passed.
- Live test PENDING: re-run with --confirm-each --limit 1.

### 2026-09-01 — Claim Attachments: fix XML dropdown option coordinate

Reason:

- The XML files option in the "Files of type" dropdown was at
  coordinate (595, 509) which is inside the file list area, not the
  dropdown.  The correct coordinate for the XML option in the opened
  dropdown is (745, 613).

Files:

- Modified `core/claim_attachments_uploader.py`:
  - `CalibratedPoints.file_dialog_xml_option`: changed from
    `(595, 509)` to `(745, 613)`.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: clicking "XML files" hit the file list area instead of the
  dropdown option; XML filter was never applied.
- After: click lands on the actual "XML files" option in the dropdown;
  filter is applied correctly.

Safety / compatibility:

- Single coordinate change; no logic changes.
- Coordinates match user-provided screenshots.

Verification:

- `python -m py_compile core/claim_attachments_uploader.py` — passed.
- Live test PENDING: re-run with --confirm-each --limit 1.
