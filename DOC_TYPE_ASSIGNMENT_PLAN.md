# Claim Attachments — Doc Type Assignment Step

Status: **LIVE TEST PASSED (v4.3, 2026-09-04)** — validated sa totoong HBSys;
4 live regression crops (PASCUA, SAFLOR, SASPA, MATTERIG) ang sinalalabak.

Owner: Melvin A. Calanda — Echague District Hospital

## Implementation history

| Version | Petsa | Strategy | Result |
|---|---|---|---|
| v1 (plan) | 2026-09-02 | OCR-based suffix detection sa cropped grid (PSM 6) | Plan lang — naka-save para sa implementation |
| v2 (initial) | 2026-09-03 AM | Cropped-region OCR + word clustering | ABORT lahat — header rows at OCR splits misread |
| v3 | 2026-09-03 ~13:00 | Pixel row separators + full-screenshot OCR + folder-driven matching | ABORT pa rin — `_pop_matched_file` bug at wrapped paths |
| v4 | 2026-09-03 ~15:38 | Line-based matching (`GridLine` + `match_files_to_lines`), debug screenshots | 7/8 PASCUA, 6/8 SAFLOR — blue COE row hindi mabasa |
| v4.1 | 2026-09-03 ~16:00 | **3-pass OCR: normal + inverted + targeted blue-band** na may quality merge | **8/8 PASCUA at SAFLOR — PASSED laban sa mismong live crops** |
| v4.2 | 2026-09-03 gabi | **Per-row-band pass** (separator-delimited bands, PSM 6 isolation) | SASPA rows (CSF, MRF, wrapped XMLs) nababasa na |
| **v4.3** | 2026-09-04 | **Stem-gain merge rule + mutated-stem tier** (flexible sa iba't ibang grid renders) | **MATTERIG 8/8 — mismong crop ng nabigong run; 4 live regression crops lahat PASSED** |

Known limitation: scrolling — kapag higit sa visible grid area ang files ng
pasyente (hal. 10+), ang mga nasa ibaba ay not_found → ABORT (tama at
ligtas; hindi hulaan). Ang SASPA live case ang nag-document nito.

Ang buong change history (reason/files/behavior/verification) ay nasa
`CHANGE_RULES.md` sa ilalim ng "Claim Attachments doc type" entries
(2026-09-03: v2 NameError repair, v3, v4, v4.1).

## Goal (orihinal na spec, 2026-09-02)

Idagdag ang Doc Type Assignment step sa Claim Attachments Uploader
(`core/claim_attachments_uploader.py` + `gui/claim_attachments_tab.py`),
pagkatapos ng pangalawang click Open (XML attach) at bago mag-close ng
attachment popup.

Reference screenshot:
`C:\claims_bot\screenshots\attach_claims\SS_choose_doc_type.png`
(OCR-verified: grid rows mula y~417, ~30px per row; "Doc" header ~(488, 357);
Upload button (598, 730); Close (1408, 734); Open (838, 564)).

## Workflow (per user spec)

Bawat file row sa "Attachments for PATIENT" popup grid:

1. OCR ang grid (pyautogui screenshot + pytesseract) → basahin ang filename
   suffix ng bawat row.
2. I-map ang suffix sa doc type (table sa baba).
3. Sa unang row: i-type ang doc type sa doc type field, i-click ang combo box
   arrow down, tapos pindutin ang TAB → focus lumipat sa next row doc type field.
4. Ulitin hanggang sa huling row.
5. Pagkatapos ng lahat: click **Upload (598, 730)** → press **Enter** (OK
   dialog) → click **Close (1408, 734)**.
6. I-dugtong ito pagkatapos ng pangalawang click Open — open coordinates =
   (838, 564) — bale dalawang click Open ang may ganoong coords.

## Doc type mapping (suffix → itype)

| Suffix (detect)  | Itype sa Doc Type |
|------------------|-------------------|
| \COE.pdf         | COE               |
| \CSF.pdf         | CSF               |
| \DTR.pdf         | DTR               |
| \SOA1.pdf        | SOA               |
| \SOA2.pdf        | SOA               |
| _CF4.xml         | CF4               |
| _CF5.xml         | CF5               |
| _eSOA.xml        | ESA               |
| \MRF.pdf         | MRF               |
| \PBC.pdf         | PBC               |
| \MMC.pdf         | MMC               |
| \OPR.pdf         | OPR               |
| \ANR.pdf         | ANR               |
| \CF3.pdf         | CF3               |
| \CF2.pdf         | CF2               |

Detection ay runtime OCR sa actual grid (hindi folder listing) para match ang
aktwal na order sa screen. May OCR-noise normalization (0↔O, 1↔I/L, 5↔S)
dahil known vocabulary lang ang hinahanap.

## Loop-engineering design (Principles)

### Whiteboard (Principle 0)

1. Trigger: per patient, sa loob ng existing loop — pagkatapos ng 2nd Open
   (XML attach), bago mag-close ng popup.
2. Check: OCR grid → filename suffix per row → map sa doc type.
3. Action: per row: type doc type → click combo arrow down → TAB.
4. Done (machine-checkable): lahat ng detected rows na-type at na-commit;
   pre-Upload re-scan: walang empty doc type row bago i-click ang Upload.
5. Escalate: unknown suffix o OCR failure → HINDI i-click ang Upload → mark
   patient failed sa state → next patient (existing pattern).

### Exits at guardrails (Principle 4)

- Success: all rows committed → Upload → OK (Enter) → Close → popup-gone
  verify (existing).
- Failure: 0 rows detected / unknown suffix → abort patient (walang Upload —
  hindi pwedeng mali ang doc type sa claim), `mark_failed`,
  `MAX_CONSECUTIVE_FAILURES=3` pa-stop ng buong batch (existing).
- Budget: row loop cap (max 40 rows) laban sa infinite TAB kapag mali ang
  layout detection.
- Escalation: failed patient → state JSON → titingnan ng tao via GUI log.

### Verification (Principle 3)

- OCR recon ng screenshot: DONE (2026-09-02) — positions verified.
- Dry-run mode: log lang, walang clicks.
- Live protocol: `--live --confirm-each --limit 1` bago full batch.

## Unknowns / risks (i-resolve sa live test)

1. Combo arrow X position — wala sa spec; derive mula sa "Doc" header OCR
   (~x≈510, configurable sa calibration JSON). Kung mag-miss sa live, kunin
   exact coord gamit ang Coordinate Getter (existing tool).
2. Grid scrolling — 9 rows lang ang visible sa screenshot; kapag 15 files ang
   patient, posibleng mag-scroll. V1: visible rows lang; kung mas maraming
   files kaysa visible rows → abort + escalate.
3. Abort path — kapag nag-abort mid-typing, isara ang popup via existing
   `close_attachment_popup()`; kung mananatili ba ang mga nai-typang rows sa
   HBSys ay makikita sa first live test.
4. OK dialog — after Upload, hintayin ang bagong #32770 window bago pindutin
   ang Enter; kapag walang dialog sa ~3s, lalaktawan (logged).

## Files na binago ( Implemented na)

1. `core/claim_attachments_uploader.py` — suffix map + OCR detector +
   `assign_doc_types_and_upload()` + Upload/OK/Close coordinates +
   integration sa `run_attachments_loop()` (after 2nd Open) — **DONE**.
2. `gui/claim_attachments_tab.py` — same insertion sa `_upload_worker` — **DONE**.
3. `core/claim_attachments_doc_type.py` (bagong module) — suffix map,
   line extraction, 3-pass OCR (normal + inverted + blue-band), quality
   merge, folder-driven matching (`match_files_to_lines`) — **DONE**.
4. `CHANGE_RULES.md` — mandatory change record kada version — **DONE**
   (v2, v3, v4, v4.1 entries).
5. version.txt — hindi pa binabump (0.3 pa rin; susundin ang owner).

## Recon data (OCR, 2026-09-02, para sa bukas)

- Popup title: "Attachments for ECHANES, PAUL GEORGE DE GUZMAN - 260901105854"
- "Doc" header: top=357, left=488
- File rows (visible): y≈417 (CSF.pdf), 447 (DTR.pdf), 477 (SOA1.pdf),
  507, 537, 567 (CF4.xml), 597, 627 (CF5.xml), 657 (eSOA.xml)
- Full paths sa left column: "...READY\ECHANES, PAUL GEORGE DE GUZMAN -
  000000000020743 - ADM20260817_DIS20260822\<FILE>"
- Buttons: Attach... ~(490,727), Upload (598,730), Close (1408,734)
- Note text sa dialog: "Note: Original files will be deleted and moved to
  server"
