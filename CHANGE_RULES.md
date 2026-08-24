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

- `git init` — OK; `git add -A` — 123 files staged.
- Full staged-file review: WALANG nakitang patient data, `.db`, `.csv`,
  `.xlsx`, `.log`, `hbsys_bot.py`, certs, o temp images.
- Ang natitirang "suspicious" matches ay false positives: `.gitkeep`
  placeholders at `patient_*.py` code modules.
- Push sa GitHub (private) — dapat i-verify pagkatapos ng push.

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
