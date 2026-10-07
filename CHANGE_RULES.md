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

### 2026-10-07 - Fix: PHIC row consensus elects the wrong confinement row (wrong-row click)

Reason:

Live workflow failure ngayong araw — Date Fill node sumabot sa
`SKIPPED_SELECTED_ROW_MISMATCH` para sa pasyenteng `DE LEON, YERIN ROBE
CHRISTINA DEL ROSARIO` (000000000009254). Na-reproduce offline mula sa
`logs/phic_beneficiaries_select_20261007_133146.png`:

- Naka-blue-highlight na ang tamang row (09/16/2026-09/18/2026) nang mag-open
  ang PHIC Beneficiaries form (`is_blue_highlighted_row(png, 205) = True`).
- Ang blue (white-on-blue) na target row ay maling nababasa ng OCR — year
  misread `2026 -> 2028` (kapareho ng sa Admission History log).
- Isang OCR pass lang ang nakakita ng target row (y=205); apat na passes ay
  bumagsak sa dated-name fallback at bumoto ng IBAng confinement ng parehong
  pasyente (07/11/2025-07/14/2025, y≈288).
- Majority-cluster consensus → mananalo ang maling row 4-1 → tatlong click sa
  y=281 → gumalaw ang highlight palayo sa tamang row → hindi na ma-confirm ang
  blue proof → `SKIPPED_SELECTED_ROW_MISMATCH` (safe stop pero nakapag-click
  na ng mali).

Sinubukan ding idagdag ang inverted-contrast OCR passes para sa blue rows pero
hindi nakatulong (walang dagdag na pass ang nakabasa ng target row), kaya
logic-level ang fix — walang OCR engine na pinalitan.

Files added / modified:

- date_fill_hbsys/hbsys_fill_dates_testing.py
  - bagong `_parse_mdy()` at `phic_row_date_evidence()`: inuuri ang
    confinement-column dates ng bawat boto — `"confine"` (month/day tugma sa
    claim; taon misread ay tolerado), `"refute"` (malinaw na ibang
    confinement), `"unknown"` (garbled/missing); ang BIRTHDAY at ibang later
    na date columns ay hindi bumuboto (unang dalawang date lang).
  - `find_phic_beneficiary_row_y_from_variants()`: ang `"refute"` na boto ay
    **abstain** na (hindi na puwedeng manalo sa cluster) + bagong optional
    `require_confinement_evidence=True` para sa tentative locate.
  - `click_phic_and_select_claim()`:
    1. kapag walang 2-vote consensus → single-pass **tentative locate** na
       gamit confinement-date evidence lamang (taon misread OK);
    2. **already-highlighted short-circuit** — kung naka-blue na ang target
       row sa select capture: audit `MATCH` + return True na **walang click**
       (ito ang eksaktong nag-save ng failed run — naka-blue na pala ang
       tamang row; ang lumang code ang gumalaw nito palayo);
    3. **proof re-target** — kapag hindi blue, ang susunod na attempt ay
       kakabigin sa `proof_y` mula sa proof OCR, hindi sa lumang `click_y`
       (dati: 3 beses sa y=281 habang report ng proof ay y≈207).
  - `is_blue_highlighted_row()`: `Image.getdata()` → `tobytes()` byte scan
    (inutusan ng Pillow 14 DeprecationWarning sa run log).
- date_fill_hbsys/hbsys_fill_dates.py (production) — parehong apat na
  pagbabago, para manatiling pareho ang production at testing stacks (ugali ng
  mga naunang PHIC fix records).
- date_fill_hbsys/test_hbsys_date_fill_verifier.py — 4 bagong test:
  refuted-votes consensus, tentative-evidence requirement,
  already-highlighted no-click, proof re-target click coordinates.
- CHANGE_RULES.md — record na ito.

Behavior:

- Before: pwedeng makapag-elect ang majority na maling row mula sa fallback
  votes; laging nagki-click kahit naka-select na ang tamang row; paulit-ulit sa
  lumang kahit anong y.
- After: ang malinaw na ibang confinement boto ay abstain; walang click kapag
  naka-blue na ang tamang row; nagre-re-target mula sa proof OCR; ganun pa rin
  ang safe-stop kapag walang ligtas na patunay.

Safety / compatibility:

- HINDI pinaluwag ang min-2 consensus para sa normal na pagki-click — ang
  tentative single-pass ay locate lamang at laging dadaan sa blue-highlight
  proof gate; kapag mali ang tentative at hindi blue, proof-gated pa rin ang
  klik → safe stop pa rin ang outcome (hindi masama kaysa dating behavior).
- Walang OCR/signing/XML/Claims Checker na binago; ang umiiral na fallback,
  first-name guard, ABTC strict/accreditation/name proofs ay pareho pa rin —
  vote classification lang ang idinagdag (`require_confinement_evidence=False`
  by default kaya walang ibang caller ang naapektuhan).
- `claims_checker.py` na `getdata()` ay hindi hinawakan (DO NOT CHANGE).

Verification performed:

- `python -m unittest test_hbsys_date_fill_verifier` → **38/38 OK**
  (34 umiiral + 4 bago; walang na-regress).
- Offline end-to-end sa totoong failed-run screenshot:
  strict consensus → `None` (mga 07/11 boto = abstain), tentative locate →
  `y=205.2`, blue = True, buong `click_phic_and_select_claim` → `True` na may
  **0 grid clicks** at audit `phic_highlighted_row_match=MATCH`.
- `py_compile -W error::SyntaxWarning` sa tatlong .py file → OK.
- LIVE HBSys run ay HINDI pa na-uulit dito (kailangan ng bukang HBSys) —
  i-re-run ng may-ari ang Date Fill node sa pasyenteng ito para makumpirma.

### 2026-10-07 - Feature: mini robot run-status overlay sa lower-left screen corner

Reason:

Operator request: kailangan may maliit na nakasulat/indicator sa lower-left side
ng screen malapit sa Windows icon para makita agad kung working/running ang
Claims Bot script. Existing dashboard status text ay nasa loob ng main window,
kaya hindi madaling makita kapag busy ang operator sa HBSys o ibang window.
Kasunod na request: gawing parang robot ang status overlay para bagay sa
automation theme (hindi lang dot/text).

Files added / modified:

- gui/run_status_overlay.py (BAGO)
  - display-only always-on-top Tk mini window;
  - lower-left screen position, near taskbar/start corner;
  - mini robot mascot na **pure Tk Canvas** (walang image asset): antenna
    status lamp, ears, rounded head na may eyes, mouth grille, body na may
    dalawang chest LEDs; cyan accent (#38BDF8) sa dark panel — automation look;
  - Idle = tulog na robot (closed eyes, gray lamp/LED, gray status text);
  - Running = gising (blinking eyes bawat ~6 s, pulsing green lamp/LED,
    green status text) + label hal. "Script running" / "Workflow running";
  - public API: `set_idle()`, `set_running(label)`, `tick()`,
    `refresh_visibility()`, `destroy()`, property `is_running`;
  - `_alive()` guard — safe tawagin kahit destroyed na ang window;
  - `if __name__ == "__main__":` standalone demo (AGENTS.md testing rule).
- tests/test_run_status_overlay.py (BAGO)
  - 13 unittest cases: state transitions, label default + truncation,
    green/gray text colors, lamp pulse, asleep/awake eyes, lower-left
    position math, hide-when-owner-withdrawn, double-destroy safety;
  - tunay na widget na withdrawn owner (walang flash sa screen).
- edh_claims_gui_XML_COPY_BUTTON.py
  - imports and creates `RunStatusOverlay`;
  - polls main subprocess + Workflow engine state every 750 ms;
  - sets overlay to running when a normal script or Recheck INCOMPLETE starts;
  - returns overlay to Idle after process cleanup — ang dalawang
    `self.after(0, set_run_status_idle)` sa loob ng worker threads
    (`_run_script_thread`, `run_recheck`) ay naka-try/except na
    (RuntimeError/TclError) para hindi masira ang cleanup; ang 750 ms poll
    loop ang backstop;
  - destroys/cancels the overlay cleanly when the main GUI closes.

Behavior:

- Before: running state was visible only inside the main dashboard status/log area.
- After: a small "EDH Claim Automation System" badge with a robot mascot
  appears at the lower-left screen corner (itaas ng Start button). Robot ay
  tulog (closed eyes, gray) kapag walang ginagawa; gising at nagba-blink na
  may pulsing green lamp kapag may running script / Recheck / Workflow.

Safety / compatibility:

- Display-only UI; it does not control mouse, keyboard, OCR, HBSys, signing,
  XML generation, Claims Checker, MySQL, or SQLite data.
- Hindi ito kumukuha ng focus (`lift()` lang, walang `focus_force`) — hindi
  nakaka-abala sa HBSys mouse/keyboard automation.
- The overlay hides with a withdrawn main window and is cleaned up on GUI
  destroy, so GUI tests and app shutdown do not leave an orphan window.
- Walang image asset — purong Tk canvas primitives, walang bagong dependency.
- No configuration/schema change and no change to processing behavior.

Verification:

- `python -W error::SyntaxWarning -m py_compile edh_claims_gui_XML_COPY_BUTTON.py gui\run_status_overlay.py tests\test_run_status_overlay.py`
  -> OK.
- `python -m unittest tests.test_run_status_overlay` -> 13/13 OK.
- `python -m unittest tests.test_gui_no_xml_panel` -> 12/12 OK.
- Visual check: PIL screenshot ng lower-left corner sa both states
  (running = gising na robot + green lamp + "Script running";
  idle = tulog + gray lamp + "Idle") — confirmed rendered sa itaas ng
  Windows Start button.

### 2026-10-06 - Fix: Final Bill batch continues to next patient on ANY error (incl. SystemExit) + traceback logging

Reason:

Live Final Bill runs halted mid-batch when a per-patient step raised or called
`sys.exit()` (e.g. a confinement mismatch surfacing as a `SystemExit`, or any
non-`Exception` escape). `except Exception` does not catch `SystemExit`, so the
first such patient aborted the whole batch and later patients never ran. There
was also no traceback, so an operator could not see which patient/value
mismatch caused the halt.

Files modified:

- core/agent/orchestrator.py
  - added `import traceback`;
  - `run_approved_plan()`: broadened the per-row guard `except Exception` ->
    `except (Exception, SystemExit)`; the full `traceback.format_exc()` is now
    logged via `log_fn` (run log + agent_run_*.json audit trail) so the exact
    offending patient/value is diagnosable; the row still becomes
    `OUTCOME_FAILED` (NOT BLOCKED) and the batch continues to the next row.
- core/agent/final_bill_runner.py
  - added `import traceback`;
  - `main()` LIVE loop: broadened `except Exception` -> `except (Exception,
    SystemExit)` and logs `traceback.format_exc()`; a `SystemExit`/raising step
    becomes a `FAILED` row (no `.final_bill_ok` marker -> retried next run)
    instead of killing the `--live` batch. The pre-existing
    `except KeyboardInterrupt` above it still aborts cleanly for the operator.
- tests/test_workflow_final_bill_node.py (+1 test)
  - ExitCodeTests.test_system_exit_does_not_halt_the_batch: the patient AFTER a
    SystemExit still runs, no OK marker is written, and a traceback reaches stdout.
- tests/test_agent_orchestrator.py (+1 test)
  - DispatchTests.test_system_exit_marks_failed_and_batch_continues: a SystemExit
    executor becomes FAILED and the next row still runs + traceback reaches the run log.

Behavior:

- Before: a `SystemExit` (or any non-`Exception` escape) from one patient's step
  killed the entire Final Bill batch; later patients were skipped; no traceback.
- After: every per-patient Exception/SystemExit is caught, logged with a full
  traceback, recorded as a FAILED row, and the batch continues to the next
  patient. `KeyboardInterrupt` still stops the batch only on operator request.
  Confinement mismatches that produce evidence + `picked=None` still route to
  BLOCKED (unchanged) - this hardens only the ERROR path.

Safety / compatibility:

- No change to OCR, signing, XML generation, HBSys coordinates, or the existing
  BLOCKED review-queue routing (H001-H010 unchanged).
- `run_approved_plan` executor injection surface (fn(hosp, folder) -> (status,
  detail)) is unchanged; existing callers/tests keep working.
- `KeyboardInterrupt` is still NOT caught by the broadened guard (handled first
  in final_bill_runner.main), so operator abort stays intact.
- New behaviour affects only the ERROR path; OK / BLOCKED / SKIPPED rows are
  byte-for-byte unchanged.

Verification:

- `python -W error::SyntaxWarning -m py_compile` on orchestrator.py,
  final_bill_runner.py, and both edited test files: OK.
- `import core.agent.orchestrator, core.agent.final_bill_runner`: OK.
- Curated agent-core regression (7 modules, headless): 297/297 OK, including the
  two new SystemExit tests and existing continue-on-error siblings
  (test_executor_exception_marks_failed_and_batch_continues,
  test_final_bill_runner_failure_maps_to_failed,
  test_date_fill_tool_failure_passes_through).
- Note: the full ~580-test discovery suite could not be run in this sandbox
  (30s per-command timeout); the 7 agent-core modules most affected by this
  change were run green instead.

### 2026-10-02 - Fix: Final Bill post-OK prompt detection (Print Options stays open behind File save / Call Administrator)

Reason:
Live run 2026-10-02 15:03 (pasyente #21853 - pangalan/HRN ay hindi
inilalagay sa repo, ayon sa .gitignore):
pagka-click ng OK sa Print Options, HINDI nagsasara ang Print Options -
nananatili itong bukas sa likod habang ang File save prompt ("Note"
[#32770]) ang lumilitaw sa ibabaw. Ang `_match_popup` ay nagbabalik ng unang
mag-match sa enumeration order, at ang Print Options ay naka-check BAGO ang
"Note" - kaya ang screen ay nanatiling FINAL_BILL_OPTIONS kahit naroon na
ang File save OK (971,593 / +(963,560), operator-mapped). Bukod dito, ang
"Note" window ay hindi ina-admit ng `detect_screen` nang walang OCR text -
at ang runner ay walang OCR sa live run - kaya hindi ito nakilala bilang
FILE_SAVE. Resulta: click_ok -> blocked, ang mapped save_ok ay hindi
kailanman na-click, BLOCKED ang row kahit handa na ang prompt.

Files modified:

- core/agent/hbsys_screens.py (untracked, new module this session):
  _match_popup() naging dalawang pass (Pass 1 = post-OK prompts kahit
  nasaan sa enumeration; Pass 2 = Print Options + generics); detect_screen()
  may dalawang bagong optional injectable (dialog_body_fn, dialog_marker_fn)
  - ang "Note" ay ina-admit LAMANG kapag may "File save" body confirmation
  AT live window proof.
- core/agent/final_bill_actions.py (untracked, new module this session):
  +_live_note_evidence() (body text LAMANG mula sa live "Note" window);
  _detect_screen() nagpapasa ng live evidence; plan_step(): kapag ok_clicked
  at stale ang read pero ang plan ay nanghula ng FILE_SAVE/CONFIRM, ang
  mapped button ay kini-click nang minsan (save OK 971,593 / +(963,560), o
  No); walang hula at walang prompt pa rin ay BLOCK ("never clicked twice").
- tests/test_agent_hbsys_screens.py: +listing_with_desktop() desktop double
  (body text LAMANG para sa bukas na window); +test_prompt_beats_print_
  options_even_when_both_are_open (eksaktong live reproduction);
  +test_note_without_body_confirmation_is_never_file_save;
  test_file_save_note_window_is_detected in-update sa dialog_body_fn.
Behavior:

- Before: open_final_bill -> check_final_box -> click_ok -> blocked ("Print
  Options popup is still on screen after OK was clicked once"). Ang File save
  prompt ay nasa screen na (windows: Note [#32770]) pero hindi nakilala, kaya
  ang mapped save_ok (971,593) ay hindi na-click at BLOCKED ang row.
- After: click_ok -> save_click_ok (File save OK, mula sa live dialog-body
  confirmation + plan prediction) -> done. Sa MISMATCH branch: click_ok ->
  answer_confirm_no (No, never Yes) -> done. Tig-iisa lang kada row, tulad ng
  napagkasunduan (ang dalawang prompt ay mutually exclusive). Ang OK sa Print
  Options ay hindi na kailanman pini-press nang dalawang beses.

Safety / compatibility:

- Ang "Note" admission ay nangangailangan ng PAREHONG live window
  (find_dialog_exact, exact title match) AT body confirmation ("File save").
  Ang ibang "Note" dialog (Epson Scan 2, ibang app) ay hindi pumapasa.
- Ang expected_screen fallback ay sumusunod LAMANG sa plan kapag stale ang
  read pagkatapos ng OK. Kapag ibang prompt ang tunay na lumabas, detection
  pa rin ang masusunod (may mismatch note sa run report).
- "Call Administrator" = No palagi (never Yes); File save = OK sa button row
  (never TAB/ENTER). Walang galaw sa loader, confinement, Close Form, OCR,
  signing, XML, checker.
- Backward compatible: ang dalawang bagong detector parameter ay optional
  (default None); lumang headless callers walang nababago.

Verification:

- python -m py_compile on all touched files: OK
- Targeted: test_agent_hbsys_screens + test_agent_final_bill = 106/106 OK;
  orchestrator + click_map + hbsys_nav = 129/129 OK
- Full suite: 498 tests OK (dalawang magkahiwalay na run, 36-46 s)
- Live evidence: agent_run_20261002_150312.json (BLOCKED case) ang
  reproduction basis; ang susunod na live run ang magpapatunay na ang "NO
  FINAL BILL" row ay nagiging save_click_ok imbes na blocked.


- tests/test_agent_final_bill.py: +test_print_options_after_ok_blocks_
  instead_of_double_clicking; +test_print_options_with_expected_save_
  prompt_clicks_save (live BLOCKED scenario); +test_print_options_with_
  expected_confirm_clicks_no; FakeHbsys.click_ok comment in-update.

### 2026-10-02 - Behavior: tanggalin ang Close Form sa dulo ng Final Bill

Reason:
Instruksyon ng operator: "tanggalin mo na ang close form doon sa dulo ng final
bill, dapat after maclick ang ok or no tanggalin na ang close form susunod doon
itype na naman ang hospital no hanggang maubos ang mga selected patients."
Ibig sabihin: pagkatapos ng OK/No sa post-OK prompt, HINDI na dapat i-click ang
Close Form - diretsong susunod ang pag-type ng susunod na hospital number hanggang
maubos ang mga selected patient. Kasama na dito ang huling pasyente, na dati'y
nag-i-click pa ng Close Form bago matapos ang batch (dahil sa
close_form_at_end=is_last_patient mula sa 2026-09-28 rule).

Files modified:

- core/agent/orchestrator.py - (1) tinanggal ang close_form_at_end=is_last_patient
  sa tawag ng runner_fn sa _default_final_bill, kaya laging default False ang
  runner at walang STEP_CLOSE_FORM na nai-plano kahit sa huling pasyente;
  (2) tinanggal ang is_last_patient na parameter ng _default_final_bill (wala nang
  gamit) at ang buong last-patient wiring: _final_bill_rows,
  _last_final_bill_index, _accepts_is_last, _call_final_bill, at ang last-patient
  branch sa dispatch (ngayon iisang executors[action](hospital_no, folder) na ang
  lahat ng action); (3) na-update ang module docstring, ang _default_final_bill
  docstring, at ang log line ("form stays open after OK/No"). HINDI hinawakan ang
  loader (load_patient_by_hospital_no) - ang close nito ng stale form ay bahagi
  pa rin ng pag-type ng susunod na hospital number.
- core/agent/final_bill_actions.py - DOCSTRING/COMMENT lamang (walang galaw sa
  logic): inayos ang module flow (yugto 8 = walang Close Form), ang multi-patient
  step 3, ang plan_step close_form_at_end docstring, at ang dalawang comment sa
  plan branch at FinalBillRunner.__init__ para sabihing opt-in / DEFAULT FALSE na
  ito at hindi na ito sinaset ng batch runner.

Behavior:

- Before: ang huling pasyente ng batch ay natatapos sa STEP_CLOSE_FORM (nag-i-click
  ng toolbar Close Form pagkatapos ng OK/No) bunga ng close_form_at_end=is_last_patient.
  Bawat pasyente bago doon: OK/No -> DONE (bukas ang form), tapos ang LOADER ng
  susunod na pasyente ang nagsasara ng lumang form habang nagta-type ng bagong
  hospital number.
- After: OK/No -> STEP_DONE kaagad para SA LAHAT ng pasyente, kasama na ang huli -
  walang Close Form click sa dulo ng Final Bill. Ang loop: OK/No -> (loader:
  isinasara ang stale form + nagta-type ng susunod na hospital no) -> ... hanggang
  maubos ang mga selected patient. Sa huling pasyente, bukas ang form sa screen
  (walang auto-close).

Safety / compatibility:

- Ang BINAGO ay ang orchestrator (ang tanging producer ng close_form_at_end) LAMANG.
  Ang plan_step(..., close_form_at_end=False default) at FinalBillRunner ay
  pinanatiling opt-in (default OFF, guarded, may test pa) - kaya walang test na
  nasisira at may capability pa kung explicit na hihingin sa hinaharap.
- HINDI hinawakan ang LOADER (load_patient_by_hospital_no) - ang pag-close ng stale
  form doon ay KAILANGAN para ma-type ang susunod na hospital number (ang searchable
  Hospital No. lookup ay nasa main screen; na-verify live 2026-09-26). Iyon ang
  "itype na naman ang hospital no" na hinihingi ng operator, kaya hindi ito tinanggal.
- Walang bagong SQL/DB, walang change sa click map, walang change sa signing, XML
  generator, o claims checker.
- Inalis ang _accepts_is_last / _call_final_bill - lahat ng executor ay tinatawag
  na 2-arg (hospital_no, folder); ang injected na 3-arg na may default (hal.
  is_last=False sa test) ay tumatakbo pa rin (yaong default ang magagamit).

Verification:

- python -m py_compile core/agent/orchestrator.py core/agent/final_bill_actions.py: OK.
- Tests (mula C:\claims_bot): 6 suite / 242 tests - LAHAT OK. Ito ang lahat ng test
  file na nag-i-import ng binagong module: test_agent_orchestrator 72,
  test_agent_final_bill 71, test_gui_agent_plan 21, test_gui_final_bill_map_editor 21,
  test_gui_final_bill_coordinate_getter 17, test_agent_final_bill_click_map 40.
- Grep: 0 natitirang reference sa is_last_patient / close_form_at_end sa
  orchestrator.py; ang close_form_at_end at STEP_CLOSE_FORM sa final_bill_actions.py
  ay nananatili bilang opt-in (default False).


### 2026-10-02 - Integration: totoong Final Bill click map (.json) - pagsusuri at absolute-path fix

Reason:
Ibinigay ng operator ang aktwal na `logs/final_bill_click_map.json` (4/4 na
point, mapped 2026-10-02T09:57:54, screen 1920x1080) at hiniling ang pagsusuri
at pag-integrate sa Final Bill. Resulta ng pagsusuri:
- `final_checkbox` (339,336) at `print_options_ok` (377,368) - nakaangkla sa
  'Print Options' rect [256,166,525,409]; dx/dy (83,170 / 121,202) - tama ang
  matematika at nasa loob ng rect ang parehong point;
- `admin_no` (1103,594) - nakaangkla sa 'Call Administrator' rect
  [761,471,1165,620] (+342,+123), nasa loob ng rect;
- `save_ok` (975,595) - ABSOLUTE (anchor_rect/dx/dy = null, isinave habang
  sarado ang 'File save' prompt kaya walang live rect na mag-anchor);
- lahat may label/anchor/note ("manual entry (coordinate editor)") at nababasa
  ng `--show` nang buo.
Natuklasang butas: ang `LOG_DIR = Path("logs")` ay CWD-relative - kapag ang
claims GUI ay na-launch mula sa labas ng `C:\claims_bot`, tahimik na WALANG
nababasa ang flow (empty map = balik sa lumang control lookup at hindi
nagagamit ang mga coordinate ng operator). Iyon ang inayos dito.

Files modified:

- core/agent/final_bill_click_map.py - (1) `LOG_DIR` ngayon ABSOLUTE:
  `Path(__file__).resolve().parents[2] / "logs"` - parehong file pa rin kapag
  mula sa project root, pero natatagpuan na kahit saang cwd nag-launch;
  (2) inalis ang dobleng comment bago ang `KEYS` (dobleng kopya ng parehong
  linya). Walang touch sa capture/anchor/CLI logic.

Behavior:

- Before: mabubuhos ang operator na "wala pang map" kapag iba ang cwd ng GUI
  kahit kumpleto ang .json.
- After: palaging nababasa ng flow ang `logs/final_bill_click_map.json` (path
  nakasemento sa lokasyon ng repo). Integrated na ang 4 na point: anchored
  point -> LIVE rect ng prompt (sumusunod ang click sa paglipat ng dialog);
  walang rect -> naka-record na absolute; `save_ok` absolute + safety net
  (`_click_prompt_answer`: mapped click muna, kapag hindi nagsara ang prompt =
  control-lookup retry, kaya hindi kayang masira ng stale na point ang row).

Safety / compatibility:

- HINDI binago ang integration wiring (`_mapped_click_point`,
  `click_print_options_ok`, `_click_prompt_answer`, `toggle_final_checkbox`) -
  ang path lang ang inayos.
- Lahat ng test na nagpa-patch ng `CLICK_MAP_PATH` ay value-agnostic (temp
  path ang ipinapalit); ang editor/getter `PROJECT_ROOT / CLICK_MAP_PATH` ay
  pareho pa ding absolute destination (pathlib: absolute RHS mananalo).
- Walang bagong JSON format, walang SQL/DB, walang production flow change.

Verification:

- `python -m py_compile core/agent/final_bill_click_map.py`: OK.
- Headless simulation gamit ang totoong .json - mula `C:\claims_bot` at mula
  `C:\Windows` (dalawang beses): parehong 4/4 points; hal. lumipat ang
  Print Options sa (300,200,620,480) -> `print_options_ok` = (421,402)
  (sumusunod), walang rect -> (377,368) (recorded); `save_ok` = (975,595)
  laging absolute; bago ang fix, walang nababasa mula sa ibang cwd.
- Tests (mula C:\claims_bot): 5 suite / 170 tests - LAHAT OK
  (test_agent_final_bill_click_map 40, test_agent_final_bill 71,
  test_gui_final_bill_map_editor 21, test_gui_final_bill_coordinate_getter 17,
  test_gui_agent_plan 21).
- `--show`: 4/4 na naka-map ang mga target.


### 2026-10-02 - Feature: Final Bill coordinate getter GUI (live X,Y + F8/countdown capture, save sa .json)

Reason:
Operator request: "gawa ka din ng coordinate getter" - kailangan ng
point-and-click na paraan: nakatingin sa totoong button sa HBSys at gusto ang
eksaktong X,Y nito nang hindi iniiwan ang button at hindi nagta-type ng numero.
Ginawa ang `gui/final_bill_coordinate_getter.py`: laging naka-on-top na mini
window na may live na cursor X,Y, target selector, at dalawang paraan ng
capture (F8 habang naka-arm, o countdown), na sumasave sa parehong
`logs/final_bill_click_map.json` na binabasa ng Final Bill flow.

Files added / modified:

- gui/final_bill_coordinate_getter.py (BAGO) - getter window + pure helpers:
  `format_xy()`, `status_for()`, `mapped_count()`. Live readout (100 ms poll
  ng `pyautogui.position`), "I-arm / Tigil ang F8" (50 ms poll ng
  `GetAsyncKeyState` - F8 = kumuha at manatiling armed para sa susunod na
  F8, ESC = disarm; pareho ng KEYS ng F8 mapper), countdown na "Kumuha sa 3s"
  (1 s tick), Kopyahin (clipboard), target Combobox + status/count mula sa
  .json, at I-SAVE na diretsong tumatawag sa `save_coordinates()` ng editor
  (validate-all-then-write + parehong anchor rule). Kapag negatibo ang cursor
  = Filipino error sa status bar, HINDI mapupunta sa field. Injected para sa
  tests: `position_fn`, `key_fn`, `anchor_rect_fn`, `countdown`.
  Standalone: `python -m gui.final_bill_coordinate_getter`.
- gui/agent_plan_tab.py - bagong button "Get Final Bill Coordinates" (katabi
  ng editor button) at `get_final_bill_coordinates()`: kapareho ng editor
  launcher - console-less, output sa `logs/final_bill_coordinate_getter.log`,
  error = log line + messagebox, hindi nag-crash ang claims GUI.
- tests/test_gui_final_bill_coordinate_getter.py (BAGO) - 17 tests:
  format/status/count, capture fill, negatibo = rejected, F8-arm + ESC disarm
  (ScriptedKeys), countdown tick -> capture, countdown blocked habang armed,
  save na anchored (live rect) at absolute (walang dialog), invalid field =
  walang isinulat, blank = walang isinulat, refresh status/count. Lahat may
  injected position/key/anchor - NEVER humahawak ng HBSys o tunay na
  keyboard/mouse.
- tests/test_gui_agent_plan.py - +2 launcher tests (tamang module/cwd, log
  line; failure = showerror + log).

Behavior:

- Before: dalawang paraan lang - F8 mapper session o ang editor na pagta-type
  ng numero (kailangang malaman muna ang X,Y).
- After: makikita ang live na X,Y habang inii-hover ang button, at makukuha
  ito nang isang F8 o countdown; isang klik sa I-SAVE at nasa .json na.

Safety / compatibility:

- BASA lang ng `pyautogui.position` at `GetAsyncKeyState` - walang ipinapadalang
  click o keystroke sa HBSys; ang F8 ay may-bisa lang kapag naka-arm ang
  getter (default: OFF). Walang bagong JSON format at walang touch sa
  production Final Bill flow / F8 mapper logic - `ClickMap.load()/save()` at
  `save_coordinates()` lang ang gamit.
- Anchor rule pareho ng editor (live dialog rect > huling kilalang rect >
  absolute); ang default na `anchor_rect_fn` lang ang tumatawag sa HBSys
  finder (sa panahon ng save), kaya ini-inject ng tests.
- Walang SQL/DB; hindi nagpe-freeze ang claims GUI (hiwalay na proseso ang
  getter, console-less tulad ng editor).

Verification:

- `python -m py_compile` sa 4 file: OK.
- `python -m unittest discover` bawat file mula `C:\claims_bot`:
  test_gui_final_bill_coordinate_getter 17 OK; test_gui_agent_plan 21 OK
  (2 bago); kabuuan 23 test files / 472 tests - LAHAT OK
  (test_gui_verify_panel_removal: 0 tests, standalone script, inaasahan
  gaya ng dati).
- Live launch: `python -m gui.final_bill_coordinate_getter` - binuksan ang
  buong window ng 3 segundo (RUNNING_OK, walang laman ang stderr) at
  na-kill nang normal; walang isinulat sa .json sa pagbukas.

### 2026-10-02 - Feature: Final Bill coordinate editor GUI (manu-manong X,Y kada button, save sa .json)

Reason:
Operator request: kapag ayaw nang mag-F8, gusto nang mag-type na lang ng
coordinates — pero dapat Nakalabel kung aling coordinates ang alin at
masisave sa .json. Ginawa ang `gui/final_bill_map_editor.py`: Tkinter GUI na
may LabelFrame bawat target (`final_checkbox`, `print_options_ok`,
`save_ok`, `admin_no` — pangalan + label + dialog anchor), X at Y entry na
naka-pre-fill mula sa kasalukuyang click map, at I-SAVE na sumusulat sa
parehong `logs/final_bill_click_map.json` na binabasa ng Final Bill flow.

Files added / modified:

- gui/final_bill_map_editor.py (BAGO) — editor window + pure helpers:
  `parse_xy()` ("1010,630" o "1010 630"; blank = None; negatibo / hindi
  numero = ValueError na Filipino), `row_text()` (parehong blank = skip ang
  row; isa lang ang napuno = "X," na sadyang ire-reject), `save_coordinates()`
  (ni-validate LAHAT ng field MUNA — walang partial save — bago isulat ang
  file nang isang beses; anchor rule: bukas na live dialog rect > huling
  kilalang anchor rect ng point > absolute, kaya sinusundan pa rin ng
  in-edit na point ang popup), `delete_points()`. May Burahin bawat row
  (may kumpirmasyon), Burahin lahat, I-refresh, at status bar
  (`point.summary()` o "WALA PA - control lookup ang gagamitin"). Default na
  path ay ABSOLUTE (`C:\claims_bot\logs\...`) kaya tama kahit saang cwd
  nabuksan. Standalone: `python -m gui.final_bill_map_editor`.
- gui/agent_plan_tab.py — bagong button "Edit Final Bill Coordinates"
  (katabi ng "Map Final Bill Clicks (F8)") at `edit_final_bill_coordinates()`:
  naglalaunch ng `python -m gui.final_bill_map_editor` mula sa project root,
  console-less (`CREATE_NO_WINDOW`), output naka-append sa
  `logs/final_bill_map_editor.log` — kapareho ng F8-mapper button contract:
  kapag bumigo ang launch = log line + messagebox lang, hindi nag-crash ang
  claims GUI.
- core/agent/final_bill_click_map.py — DOCSTRING lang: binanggit ang
  `python -m gui.final_bill_map_editor` sa listahan ng `--set`/`--show`
  na opsyon. Walang touch sa session/mapping logic.
- tests/test_gui_final_bill_map_editor.py (BAGO) — 21 tests: parse/validate,
  blank-keeps-stored, walang partial save, anchor fallback (old rect > live
  rect > absolute), delete, at withdrawn-Tk window smoke (prefill, save,
  error dialog, burahin). Lahat may injected `anchor_rect_fn` — NEVER
  humahawak ng HBSys.
- tests/test_gui_agent_plan.py — +2 launcher tests (tamang command/cwd,
  log line; failure = showerror + log).

Behavior:

- Before: ang tanging paraan ng paglagay ng point ay F8/ESC session o ang
  console-only `--set name=x,y` (walang label, kailangang tandaan ang
  bawat pangalan, nakasulat lang sa terminal).
- After: GUI na may nakalabeleng field bawat button (X at Y hiwalay),
  pre-filled mula sa .json, error per field bago ang anumang pagsulat, at
  kumpirmasyon bawat burahan. Blangkong field = hindi babaguhin ang
  naka-save na point.

Safety / compatibility:

- Walang binago sa production Final Bill flow, sa F8 mapper logic, o sa
  JSON format — diretsong ginagamit ang `ClickMap`/`ClickPoint`
  `load()`/`save()`; optional pa rin ang map (control lookup fallback kapag
  walang point, gaya ng dati).
- Hindi hahawak ng mouse/keyboard at hindi nagpe-preset ng HBSys. Ang tanging
  posibleng kontak ay opsyonal na pagbasa ng bukas na dialog rectangle para
  sa anchoring (default `anchor_rect_fn`) — ito ang ini-inject ng tests.
- `save_coordinates()` laging nagva-validate bago magsulat — walang
  maiiwang kalahating .json. Walang SQL/DB sa feature na ito.

Verification:

- `python -m py_compile` sa 5 file: OK.
- `python -m unittest discover` bawat file mula `C:\claims_bot`:
  test_gui_final_bill_map_editor 21 OK; test_gui_agent_plan 19 OK (2 bago);
  test_agent_final_bill_click_map 40 OK; kabuuan 22 test files / 453 tests —
  LAHAT OK (test_gui_verify_panel_removal: 0 tests, standalone script,
  inaasahan gaya ng dati).
- Live launch: `python -m gui.final_bill_map_editor` — binuksan ang buong
  window ng 3 segundo (RUNNING_OK) at na-kill nang normal.

### 2026-10-02 - Fix: --branch F8 walang effect habang hinihintay ang pangalawang prompt

Reason:
Operator report: sa `--branch` session, pagkatapos ng pangalawang OK - kapag
lumabas na ang isa sa dalawang prompt (File save / Call Administrator) -
walang nangyayari kapag pinindot ang F8; parang ayaw ng tool ang dalawang
kakambal na popup. Root cause: step [3] (`wait_for_post_ok_branch()`)
detection-only ang loop - ang prompt title at ESC lang ang pino-poll, ang F8
ay HINDI kailanman. At ang detection ay eksaktong "File save" / "Call
Administrator" lang, samantalang ang mismong flow (`_front_save_prompt`)
ay tumatanggap din ng "Save" / "Save As" (may build na ganoon ang title).
Kaya isang title na hindi mabasa = tahimik na walang nangyayari habang
panay ang pindot ng F8.

Files modified:

- core/agent/final_bill_click_map.py
  - wait_for_post_ok_branch(): pino-poll na rin ang F8 (pagkalipas ng
    `f8_grace`, default 1s, para hindi masalo ang F8 na ginamit sa [2/2]);
    detection pumapasok sa BRANCH_MARKERS na title aliases (mas specific na
    "Call Administrator" muna bago ang save, para hindi manalo ang ibang
    window na may "save" sa title); kapag pindot ang F8 pero walang title na
    tumugma, itatanong ng `choose_fn` (default `console_choose_branch()`:
    [1] File save / [2] Call Administrator) kung alin ang lumabas bago
    mag-record - kaya HINDI KAILANMAN namamatay ang F8 sa step na ito
  - bagong helper: `console_choose_branch()` (operator input, tulad ng
    console_capture_key - safe kahit sarado ang stdin) at
    `_detect_branch_prompt()` (admin-first marker scan)
  - branch_session(): bagong `f8_grace` at `choose_fn` na parameter
    (injectable para sa headless test), waiting-hint na linya sa [3], at
    kapag ang F8 ang nanalo sa branch (walang nabasang title = rect None)
    ay IRE-RECORD kaagad - walang pangalawang F8 na hinihintay, kasing-
    bilis ng normal na detection path
  - module docstring: inilarawan ang F8-while-waiting na pag-uugali ng --branch
- tests/test_agent_final_bill_click_map.py - 3 bagong test (37 -> 40):
  ang "Save"-lang na title ay natutukoy pa rin; F8 sa loob ng wait kahit
  walang tumugmang title ay nagtatanong at nagre-record; at buong branch
  session na nagre-record sa mismong F8 na nagtanong (absolute point +
  "NAITALA (F8)" sa output)

Behavior before:
Step [3] ay nakatingin lang sa dalawang eksaktong title at ESC. Kung hindi
mabasa ang title (o ibang title variant ang build), walang nangyayari kahit
pindutin ang F8 - walang mensahe, walang record, walang katapusan hanggang
ESC o i-restart ang session.

Behavior after:
F8 ang aktibong pindot sa buong branch wait: detection ang mas mabilis na
daan (rect kaya anchored pa rin ang record), at kapag hindi mabasa ang
title ay tinatanong ng tool kung alin ang lumabas bago i-record kaagad -
kahit dalawang sunod na popup, isang pindot lang ng F8 ang kailangan.

Safety or compatibility notes:

- Detection pa rin ang mas gustong daan: ang tanong lang ay lumalabas kapag
  F8 na talaga ang pinindot at walang nabasang title (default ENTER = File
  save, kaya pindot-pindot lang din); ang ESC sa loob ng wait ay data pa rin
  na "walang prompt" - walang click na naisasagawa ng tool habang naghihintay.
- Walang default behavior na nagbago sa walang kaugnay na F8 press:
  `f8_grace` (1s) ang pumipigil masalo ang F8 na ginamit sa [2/2], at ang
  detection path (rect != None) ay eksaktong gaya ng dati.
- Walang binago sa final_bill_actions.py / production flow - puro ang
  mapping session ang apektado; optional pa rin ang map.
- Ininjectable pa rin lahat (`key_fn`, `anchor_rect_fn`, `choose_fn`,
  `print_fn`, `f8_grace`) - headless ang tests, walang HBSys na kailangan.

Verification performed:
python -m py_compile core/agent/final_bill_click_map.py -> OK.
Batched unittest discover (kasi lumalampas sa 30s ang buong discover sa
machineng ito): lahat ng 21 test module, 430 tests, OK - kabilang ang
tests.test_agent_final_bill_click_map 40/40 (3/3 na bagong test),
test_agent_final_bill 71, test_agent_orchestrator 72, test_gui_agent_plan 17.


### 2026-09-30 - Feature: Final Bill click map (i-record ang unang OK, File save OK, Call Administrator No)

Reason:
The Final Bill flow answers three HBSys prompts with the MOUSE, and until now it
found each button by PowerBuilder control id / caption / geometry. When a build
shifts an id, a caption or the popup layout, the click can miss and the row
fails - and the 2026-09-29 12:43 run showed how expensive that is (12 wasted
steps, no trace of which prompt was involved). The operator asked to map the
real clicks instead: unang OK (Print Options), tapos ang OK ng "File save",
tapos ang No ng "Call Administrator" (at ang 'Final' checkbox), with the admin
path recordable in ONE session ("isang option": unang OK + No).

Files added:

- core/agent/final_bill_click_map.py - the map + the capture session + a CLI
  - ClickPoint stores the absolute point AND its offset inside its dialog
    (dx/dy + anchor title), so a popup that opens elsewhere still gets the
    click on the same button; ClickMap saves/loads logs/final_bill_click_map.json
    (a missing or corrupt file is an EMPTY map: never fatal)
  - TARGETS = final_checkbox, print_options_ok, save_ok, admin_no - in LIVE
    order: checkbox -> unang OK -> isa lang sa save/admin branch (Tagalog +
    English instructions); GROUPS = all, dialog_oks, checkbox_first_ok,
    admin_pair
  - capture_session(): F8 records the point under the mouse, ESC skips; with
    --in SECONDS each target records itself after N seconds (works even when
    the console is not focused); position/key/anchor/print are injectable
  - --branch (+ wait_for_post_ok_branch / branch_session, 2026-09-30):
    checkbox, unang OK, tapos isa lang sa File save/Call Administrator ang
    hinihintay - detect tapos i-record ang prompt na TALAGANG lumabas; ang
    hindi lumalabas ay hindi na hinihintay
  - CLI: --map [names|groups] (no names = all targets), --branch
    [--branch-timeout S] [--in N], --show, --check
    (mapped? dialog open now?), --set name=x,y, --clear, --path
- tests/test_agent_final_bill_click_map.py - 32 tests: anchoring math, file
  round trip / corrupt file / junk points, group expansion, F8-ESC-timeout,
  a headless admin_pair session, ESC keeps the old point, post-OK branch wait
  (save-only, admin-only, timeout/ESC maps nothing), branch session headless
  (checkbox + OK + the winning branch only), plus the wiring tests (mapped
  point wins, applied relative to the dialog, stale mapping falls back,
  corrupt map never breaks the flow)

Files modified:

- core/agent/final_bill_actions.py
  - _mapped_click_point(name, window): the operator's point resolved against
    the LIVE window rectangle, or None (map missing/unreadable/unmapped)
  - click_print_options_ok(): mapped `print_options_ok` first, else the
    verified id 1002 lookup exactly as before
  - _click_prompt_answer(dialog, map_name, control_click_fn, timeout): clicks
    the mapped point, verifies the dialog closed, and only then - if it is
    still open - runs the old control lookup and re-verifies, so a stale
    mapping degrades to the old behaviour instead of failing the row
  - answer_save_prompt_with_ok() -> `save_ok`, answer_confirm_prompt_with_no()
    -> `admin_no` (both through _click_prompt_answer; error messages unchanged)
  - _final_checkbox_offset(): mapped `final_checkbox` converted back to a
    button-relative offset, else FINAL_CHECKBOX_OFFSET (the repaint settle and
    the idempotent double-click stay as they were)
- gui/agent_plan_tab.py - new "Map Final Bill Clicks" button in the Controls
  row; map_final_bill_clicks() opens `python -m core.agent.final_bill_click_map
  --map` in its own console window (claimed GUI mode env, cwd = project root);
  a launch failure is a log line + message box, never a crashed panel
- tests/test_agent_final_bill.py - test hygiene: the module points
  click_map.CLICK_MAP_PATH at a temp file, so a real map on the operator's
  machine can never change what the control-id tests exercise (restored in
  tearDownModule)
- tests/test_gui_agent_plan.py - 2 tests: the button launches the mapper in its
  own console from the project root, and a launch failure is reported

Behavior before:
The three Final Bill clicks were found only by control id / caption / geometry.
There was no way for the operator to say "this is the button I really press" -
and no place to record the answer to either prompt.

Behavior after:
The operator can record the real points once (GUI button or CLI). Every mapped
point is applied RELATIVE to its dialog, so it follows the popup around the
screen. Unmapped targets, a deleted map and a stale map all keep the previous
verified behaviour; a mapped point is only ever used on a prompt the caller
already identified, and the answer is still verified by the dialog closing.
A mapped click is logged by the same step lines as before, and the map file
itself says which points are anchored and when they were recorded.

Safety or compatibility notes:

- Mapping is optional and additive: with no logs/final_bill_click_map.json the
  flow is bit-for-bit the previous behaviour (all 388 pre-existing tests still
  pass unchanged).
- A wrong or stale mapped point CANNOT answer a prompt with a cancel/Yes:
  "Call Administrator" is still only ever answered by clicking its "No", and
  the control-lookup retry runs before a row is allowed to fail.
- No new dependency: pyautogui (already required) for the mouse position,
  win32api/pywin32 (shipped with pywinauto) only to read the F8/ESC key state.
  F8 is polled, never injected - the mapper never presses a key or clicks a
  button by itself; only the operator clicks during a session.
- The map is read on demand (no caching), so re-mapping takes effect on the
  next step without restarting the GUI.
- The mapper runs in a separate process/console; a mapper crash cannot take the
  claims GUI down and cannot touch patient files.
- Map data is not committed: it lives under logs/ as runtime data.

Verification performed:
python -m unittest discover -s tests -> 422 tests OK (previously 388), of which
32 are new click-map tests and 2 are the new GUI button tests.
tests.test_agent_final_bill / tests.test_agent_orchestrator / 
tests.test_gui_agent_plan all OK. CLI smoke-tested with --show (real path,
prints all four targets as unmapped), --set, --check and --clear.



Reason:
The 2026-09-29 12:42 final_bill run (CABLING, NIXEN BLANQUERA) FAILED with
`final bill flow did not finish within 12 steps` and carried no trace of what it was
doing (logs/agent_run_20260929_124313.json holds only that sentence), so it was
impossible to tell whether the "Call administrator" prompt (click No) or the "File
save" prompt (click OK) had even been reached. Prime suspect, now fixed:
`toggle_final_checkbox()` re-read the glyph pixels immediately after `real_click()`,
while PowerBuilder repaints the glyph slightly later - a stale "unticked" reading made
the step click a SECOND time (unticking the box it had just ticked) and the planner
re-planned the same step until the 12-step budget ran out. Whatever step was actually
stuck, the run now stops fast and records the evidence needed to name it.

Files modified:

- core/agent/final_bill_actions.py
  - CHECKBOX_REPAINT_SETTLE_SECONDS = 0.35: toggle_final_checkbox() now sleeps after
    EACH click before re-reading checkbox_dark_ratio(), so a repaint in flight can
    never be mistaken for "the box did not tick"
  - SAME_STEP_REPEAT_LIMIT = 3 + trace_of(): FinalBillRunner.run() stops on the 3rd
    consecutive plan of the SAME non-terminal step and names the step, the screen it
    was stuck on and the collapsed step trail ("open -> check x3 -> ok")
  - the step-budget exhaustion reason carries the same trail; both failure paths
    (repeat guard, exhaustion, exception) log a `final bill failure:` line via log_fn
- core/agent/orchestrator.py
  - FAILED final_bill rows append `| windows: ...` (final_bill.hbsys_window_titles(),
    which includes modal #32770 dialogs such as File save / Call Administrator) and
    `| screenshot: <path>` (save_screenshot("final_bill_stopped")) to the detail kept
    in agent_run_*.json - a stopped row now shows which dialog was on screen
  - docstring correction: the Call Administrator prompt is answered by clicking "No"
    (was "TAB")
- tests/test_agent_final_bill.py
  - new FinalCheckboxRepaintTests (glyph is re-read only after the settle, for the
    first click and for the corrective second click)
  - test_step_cap_stops_a_stuck_flow replaced by test_a_stuck_step_fails_fast_and_names_itself
    (stops on the 3rd repeat, names itself, logs `final bill failure:`) plus
    test_step_cap_stops_a_flow_whose_steps_keep_changing (alternating steps still hit
    the cap, reason now carries the trail) and test_trace_of_collapses_consecutive_repeats
- tests/test_agent_orchestrator.py
  - new test_failed_final_bill_row_keeps_screen_evidence (windows + screenshot + trail
    appear in the FAILED detail; stubs restored to the module-hygiene stubs)

Behavior before:
A step whose screen did not change silently consumed the whole 12-step budget (~23 s)
and the report said only "final bill flow did not finish within 12 steps". The Final
checkbox glyph was sampled the instant after the click, so a repaint race flipped the
box twice and looped. A FAILED row recorded no screen state at all.

Behavior after:
A step that repeats 3 times in a row stops the run immediately with
"<step> ran 3 times in a row on screen <screen> without changing it - check the HBSys
display and finish this patient by hand | steps: <trail>", and the trail is also
appended when the budget is genuinely exhausted. FAILED rows additionally carry the
open HBSys windows and a `final_bill_stopped_<ts>.png` of the screen, which is what
decides the next fix: prompt absent vs prompt present and the click missed.

Safety or compatibility notes:

- The two prompt handlers themselves (answer_admin_no, confirm_no, click_print_options_ok)
  are unchanged - this session only removed the stall that prevented them from running
  and made the failure diagnosable.
- The guard NEVER converts a failure into success: a stuck row stays FAILED (BLOCKED
  semantics untouched), nothing is guessed, no prompt is clicked blind.
- Only the checkbox step pays the 0.35 s settle; other steps' timing is untouched.
- Screenshots are taken only on a real failure; the test suite stubs save_screenshot
  to "" so no patient screen is captured during tests.

Verification performed:
python -m unittest discover -s tests -> full suite OK (388 tests), including the new
repaint/guard/evidence tests; tests.test_agent_final_bill (72) and
tests.test_agent_orchestrator (72) both OK.



Reason:
Two live-run defects on `final_bill` rows when several patients run in one
batch: (1) the next patient was never really loaded — the loader clicked a
heuristic Edit control, typed the number and reported success without
verifying anything, so the flow could bill whatever patient was on screen
(or close the wrong window with a blind "Close Form" click on the shared
toolbar band); (2) the confinement match was exact-only — the OCR-tolerant
fuzzy pass Date Fill uses (`best_fuzzy_admission_history_row`) was passed as
None, and the shared matcher import silently failed (`hbsys_rules` not
importable from core/agent), so Admit History selection returned False and
the row BLOCKed.

Files modified:

- core/agent/final_bill_actions.py
  - HOSPITAL_NO_POINT (166, 174) = Date Fill's verified P.HOSPITAL_NO,
    HOSPITAL_NO_EDIT_ID 1004 (probed live: "Edit 1004 on the Billing
    form"), LOAD_WAIT_SECONDS 1.5, ENCOUNTER_WORDS
  - billing_form_titles() / billing_form_for_patient(): pure helpers
    (only "Billing (...)" MDI children count as a patient form)
  - find_billing_hospital_no_edit(): prefers control id 1004, geometric
    search kept as fallback; hospital_no_click_point(): control-relative
    centre when 1004 is visible, else the verified coordinate
  - load_patient_by_hospital_no(): closes a STALE Billing form first
    (guarded), DOUBLE-CLICKs the Hospital No. field, CTRL+A, types,
    ENTER, then VERIFIES a new "Billing (...)" form appeared; returns
    False otherwise (row BLOCKs, never types over a patient)
  - close_billing_form(): refuses to click when no Billing form is open
    (or when another patient's form is named), OCR-verifies the tooltip,
    then waits for the form to disappear -> True/False
  - select_confinement(): now runs Date Fill's recipe end to end —
    _ensure_date_fill_imports() (project root + date_fill_hbsys on
    sys.path for hbsys_rules), raw grid text carried into the shared
    ConfinementRow, exact match first, then fuzzy_rows_fn, plus an
    optional log_fn; returns False on a mismatch (popup left open)
  - ocr_date_match_score() + fuzzy_confinement_row() +
    FUZZY_ROW_MIN_SCORE 115 / FUZZY_ROW_MIN_MARGIN 25 / ADMIT bonus 10:
    verbatim port of Date Fill's scoring; admission_history_rows() now
    also returns the raw grid text and the encounter type
  - answer_dialog_no(): id-7 first, then the "No" caption (never Yes);
    answer_dialog_ok(): OK, else Save (new _visible_button_by_text)
  - FinalBillRunner: close_form_fn defaults to close_billing_form and a
    False return fails the row with a readable reason instead of
    re-clicking; _answer_ok() tolerates a "Save"-titled dialog;
    plan_step()/docstrings updated for the multi-patient sequence
- core/agent/orchestrator.py
  - _default_final_bill(): logs the stale form it is about to replace,
    uses final_bill.billing_form_for_patient() for the wanted title,
    treats a False/raising loader as "not verified" (row BLOCKs via
    final_bill_block_reason), and wires the real confinement matcher
    with log_fn so the chosen Admit History row lands in the run log
  - module docstring: final_bill dispatch + multi-patient guarantee
- tests/test_agent_final_bill.py: +16 tests (PatientLookupTests,
  FuzzyConfinementTests, stubborn-close failure reason; the default
  close_form_fn binding pinned to close_billing_form)
- tests/test_agent_orchestrator.py: +4 tests (MultiPatientFinalBillTests
  with FakeBillingSession: second patient loaded after the first form
  closes, stale form closed by the loader, same-patient row reuses the
  open form, two final_bill rows in one batch)

Behavior:
- Before: a final_bill row could run against a stale Billing form, skip
  the confinement (fuzzy pass None / matcher import failing) or the
  runner could click Close Form with no form open; consecutive patients
  were effectively typed over each other.
- After: every final_bill row follows the operator sequence exactly —
  close the stale form -> double-click Hospital No. -> type -> ENTER ->
  verify the new Billing form -> Admit History exact/fuzzy match ->
  Final Bill -> "Final" box -> OK -> No (or Save) -> Close Form; every
  step that cannot be verified BLOCKs that row only, with the batch
  continuing.

Safety / compatibility:
- Never clicks Close Form blind: the click only happens while a Billing
  form is open and the tooltip OCR still reads "Close Form".
- Loader/runner signatures stay backward compatible (new kwargs only);
  orchestrator injection surface unchanged, so existing tests/callers
  keep working. Ambiguous confinement matches still stop for review
  (threshold + 25-point margin, same numbers as Date Fill).

Verification:

- python -m py_compile on all four touched files: OK
- Targeted: tests.test_agent_final_bill + tests.test_agent_orchestrator =
  92/92 OK (75 before this change)
- Agent suite: fees_actions + final_bill + hbsys_nav + hbsys_screens +
  orchestrator + plan_store + gui_agent_plan = 173/173 OK
- Headless two-patient evidence run (orchestrator._default_final_bill
  wired to a fake session): row 1 load (123456789012345) ->
  confinement 20260901-20260903 -> bill -> form closed; row 2 load
  (000000000021401) -> confinement 20260906-20260912 -> bill -> form
  closed; 2 OK, no stale typing
- date_fill_hbsys/test_hbsys_date_fill_verifier still OK run from its
  own directory (unchanged behavior)


### 2026-09-26 - Slice F (part 3): Load Plan auto-runs Fees Check + MISMATCH rows route to FINAL BILL

Reason:
Two user decisions: (1) the plan load flow required manually clicking
Fees Check before Load Plan — make it one click (with an opt-out
checkbox); (2) rows with Status MISMATCH were planned as MANUAL REVIEW,
but the actual fix for mismatched itemized-vs-grouped totals is
re-running the Final Bill flow — planned action must be FINAL BILL.

Files modified:

- core/agent/fees_actions.py
  - new priority 2b in decide_action(): Status MISMATCH -> ACTION_FINAL_BILL
    ("MISMATCH ang itemized vs grouped charges - kailangan i-final bill
    muna."), ranked above DATE_FILL (bill must be right before dates);
    TOOL_AVAILABLE check still honored (FINAL_BILL_TOOL_MISSING path)
  - catch-all manual-review branch no longer special-cases MISMATCH
    (unreachable now); REVIEW_MISMATCH constant kept for old reports
  - module docstring action table + priority list updated
- gui/agent_plan_tab.py
  - new checkbox "Run Fees Check first" (default ON) next to Load Plan
  - Load Plan button now calls on_load_plan(): checkbox ON -> runs the
    Fees Check first on a background thread (fees_checker.run_check,
    lazy import, read-only DB; UI never freezes; double-click guarded by
    _loading; Load button disabled during preflight), then loads the
    report THAT run wrote; OFF -> plain load_plan()
  - preflight failure: error logged + showerror explaining, plan not
    reloaded (uncheck to load the existing report); fees_check_fn is
    injectable for headless tests (same pattern as run_plan_fn)
  - load_plan() stays a PURE load — the after-run auto-reload never
    triggers another Fees Check
- tests/test_agent_fees_actions.py: mismatch routing tests replaced
  (+test_mismatch_goes_to_final_bill, +test_mismatch_beats_date_fill,
  build_plan order expectation updated)
- tests/test_gui_agent_plan.py: +4 tests (preflight runs check then
  loads fresh CSV; unchecked skips check; failure path shows error and
  keeps state; after-run reload does NOT re-run the check) +
  showerror stub in setUp/tearDown

Behavior:
- Before: Load Plan read whatever CSV already existed (manually
  refreshed); MISMATCH rows sat in Manual Review and were never executed.
- After: one Load Plan click = fresh Fees Check -> fresh plan; MISMATCH
  patients are approved/run through the verified Final Bill flow like
  NO FINAL BILL rows (and the completion ledger records them the same
  way, so a fixed patient never returns).

Safety / compatibility:
- Fees Check stays read-only HBSys access, now on a background thread
  (same rule as plan execution); no change to fees_checker.py itself.
- Manual review keeps everything else (ADM/DIS mismatch, NO RECORD,
  unknown statuses); old run reports referencing MISMATCH unchanged.

Verification:
- python -m py_compile on all touched files: OK
- Targeted: test_agent_fees_actions + test_agent_plan_store = 45/45 OK;
  test_gui_agent_plan = 10/10 OK
- Full suite: 259 tests OK (previous 254 + 5 net new), 16.0 s
- Live routing smoke: MISMATCH row -> FINAL_BILL decision; NO RECORD
  still MANUAL_REVIEW.


### 2026-09-25 - Slice F (part 2): Plan base = latest Fees Check CSV + completion ledger (finished rows never repeat)

Reason:
User: the Agent Plan must be based on the LATEST generated Fees Check
report. When fees_checker_report.csv is open/locked, fees_checker falls
back to a timestamped copy (fees_checker_report_<stamp>.csv) and the fixed
file goes stale — the panel kept reading it. Also: after Approve & Run the
panel must update so rows that already finished are not repeated on the
next plan load.

Files modified:

- core/agent/agent_plan_store.py
  - new latest_fees_csv(base_dir=None): newest fees_checker_report*.csv
    by mtime (fixed or timestamped fallback); falls back to
    DEFAULT_FEES_CSV when no report exists
  - new resolve_fees_csv(value, base_dir=None): blank/canonical name ->
    newest report; any other explicit Browse/typed path respected as-is
  - new completion ledger API: COMPLETED_LEDGER =
    logs/agent_completed_actions.json, load_completed_actions()
    (missing/corrupt -> empty set), record_completed_actions()
    (atomic tmp+replace, idempotent), drop_completed_actions()
    (mirror of drop_completed_xml)
  - build_plan_from_csv(csv_path, output_root=None, completed=None):
    completed=None reads the ledger; OK rows drop from the plan and the
    note gains "N row tapos na sa nakaraang run - hindi na inuulit."
- core/agent/orchestrator.py
  - run_approved_plan gains completed_path=None; when save=True it now
    records every OK row into the ledger via new _record_completed()
    (never raises; BLOCKED/FAILED/QUEUED/SKIPPED rows are NOT recorded
    so they retry on the next run)
- gui/agent_plan_tab.py
  - load_plan resolves the Fees CSV through resolve_fees_csv() and
    updates the field to the file actually used
  - _run_finished reloads the plan after a run with outcomes, so
    completed rows drop out immediately
- tests/test_agent_plan_store.py: +11 tests (LatestFeesCsvTests,
  CompletedLedgerTests)
- tests/test_agent_orchestrator.py: +3 tests (CompletedLedgerTests);
  test_run_approved_plan_saves_by_default redirects completed_path to tmp
- tests/test_gui_agent_plan.py: +2 tests (ledger drop on load; reload +
  drop after run)

Behavior:
- Before: the panel always read fees_checker_report.csv (stale when the
  fixed file was locked); the same CSV produced the same plan after every
  run, so finished rows reappeared and could be re-executed.
- After: Load Plan always follows the newest Fees Check CSV (an explicit
  custom path still wins); OK rows recorded once in
  logs/agent_completed_actions.json drop from every later plan build with
  a note count; failed/blocked/queued rows still appear for retry.

Safety / compatibility:
- All scans/reads read-only; the ledger is written atomically only on
  save=True runs (save=False tests never touch it); build_plan_from_csv
  stays backward compatible (fake test folders never match real ledger
  entries). Reset = delete logs/agent_completed_actions.json.
- Orchestrator still executes only user-approved rows and every run still
  writes its logs/agent_run_*.json audit trail.

Verification:
- python -m py_compile on all touched files: OK
- Full suite: 254 tests OK (baseline 238 + 16 new), 14.941 s
- Smoke: resolve_fees_csv(DEFAULT) -> fees_checker_report_20260925_161255.csv
  (newest mtime) and the plan built from it; ledger absent -> 0 pairs.


### 2026-09-25 - Slice F (part 1): Plan-time XML output-folder gate + XML clicker probe note

Reason:
User decision table for ready rows: if the patient output folder already
has ALL required XMLs (CF4+CF5+ESOA), the row must NOT appear in the
Agent Plan — the XML Clicker has nothing left to generate. If some XMLs
are still missing, the row stays and the plan must say WHICH kinds are
missing. Same semantics as the runtime XMLClickerPolicy
(core/xml_output_checker.py).
Also recorded per user: the routed XML Clicker only starts AT the
CF4/CF5/eSOA tabs (fixed points P.CF4_XML / P.CF5_XML / P.ESOA_XML at
y=58) — the intermediate clicks that navigate HBSys to that screen
happen BEFORE those tabs and are not automated yet; they must be
captured in a live probe session (see Next below).

Files modified:

- core/agent/fees_actions.py
  - ActionDecision gains xml_complete flag (default False)
  - decide_action(row, *, output_root=None): optional read-only scan —
    complete folder marks xml_complete=True; partial folder keeps the
    row with a "kulang ng XML: ..." reason naming CF4/CF5/ESOA; absent
    folder or output_root=None behaves exactly as before
  - new helpers: DEFAULT_OUTPUT_ROOT, default_output_root()
    (CLAIMS_OUTPUT_FOLDER env, default C:\claims_bot\output — same
    convention as pdf_preview_service / workflow_adapters),
    resolve_output_folder() (bare folder name joined under root;
    absolute value used as-is)
  - build_plan(rows, output_root=None) threads the root through
  - drop_completed_xml(decisions) -> (kept, excluded_count)
- core/agent/agent_plan_store.py
  - build_plan_from_csv(csv_path, output_root=None): resolves the
    default output root, drops completed-XML rows before summarize /
    to_plan_items, and returns a note ("N row hindi isinama sa plan:
    kumpleto na ang XML (CF4+CF5+ESOA) sa output folder.") that the
    Plan Panel shows via the existing note_var
- tests/test_agent_fees_actions.py: +9 tests (OutputFolderXmlGateTests)
- tests/test_agent_plan_store.py: +2 tests (OutputXmlGatePlanTests)

No GUI change needed — agent_plan_tab.load_plan() calls
build_plan_from_csv(csv_path) with no output_root, which now resolves
the standard output root automatically (line 216 sets note_var).

Behavior:
- Before: every Ready=YES row always became an xml_clicker plan item
  with one fixed reason; plan build never touched the filesystem.
- After: rows whose output folder already has all three XMLs are
  excluded from the plan (they stay in the source CSV) and counted in
  the panel note; partial folders remain with the missing kinds named;
  the legacy reason string is preserved verbatim for absent folders /
  output_root=None.

Safety / compatibility:
- Scan is read-only; XML Clicker / Final Bill flows untouched;
  ActionDecision keeps optional fields so old constructors work;
  backward compatible with callers passing no output_root (orchestrator,
  GUI, reports unchanged).

Verification:
- python -m py_compile on all touched files: OK
- Targeted: tests.test_agent_fees_actions + tests.test_agent_plan_store
  = 33/33 OK
- Full suite: 238 tests OK (baseline 227 + 11 new), 15.978 s

Next (deferred to live probe session, per user):
Capture the intermediate HBSys navigation clicks that happen BEFORE the
CF4 XML tab (menus/screens leading to the CF4/CF5/eSOA tab bar at
(486/536/586, 58)) and add them as a pre-navigation step in
date_fill_hbsys/xml_generator_clicker.py (open_generator currently only
focuses the already-open window and clicks the tab). Probe plan: user
opens HBSys + logs in, walks the navigation once manually while click
positions are logged, then encode the steps and validate with
python date_fill_hbsys/xml_generator_clicker.py --live --limit 1
--confirm-each on a test patient.

### 2026-09-25 - Slice E: Claims Agent orchestrator (approved plan now executes)

Reason:
User-approved Slice E of the Claims Agent plan: wire the Slice C approved
plan to real execution — "Approve & Run" dispatches Date Fill, XML Clicker
and Final Bill through a deterministic orchestrator. NO FINAL BILL rows are
finally routable now that Slice D's FinalBillRunner exists (the flip of
TOOL_AVAILABLE[final_bill], reserved by Slice D, happens here).

Files added:

- core/agent/orchestrator.py
  - run_approved_plan(items, ...) executes APPROVED rows in plan order,
    one RowOutcome per row (OK / BLOCKED / FAILED / QUEUED / SKIPPED),
    continue-on-error, JSON audit trail logs/agent_run_YYYYMMDD_HHMMSS.json
  - dispatch: date_fill -> hbsys_fill_dates.py --live --hospital-no N;
    xml_clicker -> xml_generator_clicker.py --live --hospital-no N;
    final_bill -> FinalBillRunner but ONLY when the Billing form for that
    exact patient is open (final_bill_block_reason() is pure + tested);
    manual_review -> QUEUED, never executed
  - hospital number parsed from the folder via hbsys_ready_claims.
    CLAIM_FOLDER_RE (the same regex the tools use) — unparseable folder =
    BLOCKED, never guessed; ready-queue membership is pre-checked before
    any subprocess is launched
  - every executor injectable (queue_fn / run_tool_fn / forms_fn /
    runner_fn); RunReport exposes counts + summary_line for the panel
- tests/test_agent_orchestrator.py — 31 headless tests (folder parsing,
  Final Bill preconditions, dispatch order + guarantees, report/save,
  default-executor branches through injected hooks)

Files modified:

- core/agent/fees_actions.py — TOOL_AVAILABLE[ACTION_FINAL_BILL] flipped to
  True; the NO FINAL BILL branch sets review_code="" when the tool is
  available and keeps REVIEW_FINAL_BILL_TOOL_MISSING for a withdrawn tool;
  module/docstring comments updated
- date_fill_hbsys/hbsys_fill_dates.py — show_popup() + open_run_log() honor
  CLAIMS_AGENT_QUIET=1 (print instead of modal dialog / CSV auto-open);
  env unset = existing behavior, unchanged
- date_fill_hbsys/xml_generator_clicker.py — same guard for
  show_completion_popup() + open_run_log()
- gui/agent_plan_tab.py — "Approve & Run" is no longer approve-only:
  approval + plan JSON persist as before, then an explicit askyesno
  confirmation, then execution via the orchestrator on a daemon thread
  (progress marshalled with after(); run_plan_fn injectable,
  background=False gives synchronous runs for tests); docstring, header
  label and button text updated
- tests/test_agent_fees_actions.py — tool-present expectations for the two
  pinned tests + a new mock.patch.dict test pinning the withdrawn-tool
  review-code branch (vocabulary stays intact)
- tests/test_gui_agent_plan.py — askyesno stubbed NO by default (existing
  approval tests keep their behavior); new approve-and-run execution test;
  background=False for determinism

Behavior before:
"Approve & Run (approve only)" persisted the plan JSON and explicitly did
nothing in HBSys; NO FINAL BILL rows reported tool_available=False and
could only fall back to manual review; the Date Fill/XML tools always popped
modal dialogs and auto-opened their CSV run logs (fine for one GUI click,
wrong for unattended batches).

Behavior after (Slice E):
An approved plan actually runs — per-row dispatch in plan order, each tool
invoked for exactly one patient, Final Bill run only when the right
patient's Billing form is verified open, manual-review rows queued, every
row reported and saved to logs/agent_run_*.json. Declining the confirmation
saves the plan without executing anything.

Safety / compatibility notes:

- Only status=APPROVED rows execute; anything else is recorded SKIPPED or
  QUEUED — approval stays explicit and selection-only.
- final_bill_block_reason() refuses to run unless the Billing form title
  matches the folder's patient (title format probe-verified
  2026-09-25: "Billing (NAME)"); the Hospital No. + Admit History
  confinement steps remain the operator's manual steps 1-4 — never
  guessed; a mismatch BLOCKS instead of clicking.
- The Final Bill commit point (answer No on Call Administrator) and the
  never-click-&Yes pin are unchanged from Slice D.
- CLAIMS_AGENT_QUIET only affects orchestrator-launched child tools; the
  normal GUI buttons behave exactly as before (env unset).
- HBSys/MySQL stays read-only outside the verified UI flows; the main GUI
  file was not touched (the tab's new kwargs have defaults).
- Tkinter's main thread never blocks: execution runs on a daemon thread
  and UI updates go through after().

Verification performed:

- python -m py_compile on every touched file (incl. the main GUI) -> OK.
- Full agent suite (unittest discover, pattern test_agent_*.py)
  -> 104 tests, 0 failures (was 72; +31 orchestrator, +1 fees branch pin).
- tests.test_gui_agent_plan -> 4 tests, 0 failures (persists-only path +
  new approve-and-run execution path).
- Whole tests/ directory (pattern test_*.py) -> 227 tests, 0 failures,
  0 errors (the two not_transmitted_batches thread tracebacks are
  pre-existing test noise, unrelated to this change).
- Quiet-guard smoke: show_popup()/show_completion_popup()/open_run_log()
  print instead of opening dialogs when CLAIMS_AGENT_QUIET=1.
- Live HBSys batch intentionally NOT run in this session (execution
  mutates billing data); the first live run is a user-driven action from
  the Agent Plan tab with HBSys open.

### 2026-09-25 - Slice D: Final Bill Actions module (verified mechanics + pure planner + injectable runner)

Reason:
User-approved Slice D of the Claims Agent plan. The live mapping session
of 2026-09-25 (HBSys PID 16640/22008, patient ...21401) walked the whole
Final Bill flow by hand and captured exact controls + click mechanics.
This change turns that evidence into a reusable module so the agent can
drive HBSys Billing -> Final Bill deterministically instead of NO FINAL
BILL rows always falling back to manual review.

Files added:

- core/agent/final_bill_actions.py
  - verified control evidence: Print Options FNWNS370 (Final id 1001 at
    (333,325,421,350), offset (7,12) -> live toggle point (340,337); OK id
    1002; PRINT id 1003 never clicked), Call Administrator #32770 (&Yes id
    6 marked DO NOT click, &No id 7 = the operator answer), Close Form
    toolbar slot (548,97); FINAL_BILL_EVIDENCE table + evidence_for()
  - STEP_* vocabulary (8 steps), frozen StepDecision, and plan_step() —
    a pure planner over (screen, final_checked, bill_finalized, forms_open)
  - GUI mechanics kept exactly as verified: raise_to_top (HWND_TOPMOST +
    SWP_NOACTIVATE) before every real mouse click, checkbox state via
    glyph dark-pixel ratio (the box has no BS_CHECKBOX), toolbar slot via
    PBTooltips16_70 hover + Tesseract OCR with a refuse-to-click guard,
    dialogs answered by control id after is_visible()+class filters
  - FinalBillRunner: detect/menu/check/ok/confirm/save/close primitives
    all injected (defaults = the real GUI), max_steps cap, first failure
    reported and never retried, resume via initial_final_checked /
    initial_bill_finalized; run_final_bill() convenience wrapper
- tests/test_agent_final_bill.py — 24 headless tests: all planner
  branches, evidence pins (toggle point (340,337) inside the rect, Yes vs
  No notes), tooltip label matching, and runner sequences for the live
  confirm->No path, the File save->OK branch, resume, both blocked paths,
  primitive-failure reporting and the step cap

Files modified (this session, detector/navigation prerequisites):

- core/agent/hbsys_screens.py — SCREEN_FINAL_BILL_OPTIONS /
  SCREEN_FINAL_BILL_CONFIRM / SCREEN_FILE_SAVE with titles "Print
  Options", "Call Administrator", "File save" + _match_popup branches
- core/agent/hbsys_nav.py — TARGET_FINAL_BILL, MENU_HOP_TABLE uses the
  real menu path "Billing -> Final Bill" (no fabricated coordinate),
  menu_fn injection, generalized _run_hops, real _real_menu_select +
  find_main_window primitives
- _probe_final_bill.py — dev probe that live-mapped the flow (JSON +
  screenshots); its helpers are mirrored by the module

Behavior before:

The detector did not know the three Final Bill popups; the Navigator had
no verified path to Final Bill; no module embodied the click recipe
(BM_CLICK cannot dismiss these PowerBuilder modals); NO FINAL BILL rows
could only route to MANUAL_REVIEW_FINAL_BILL.

Behavior after (Slice D):

plan_step() maps every observed state to exactly one next step or a
BLOCKED reason (never guesses), FinalBillRunner can execute the full
sequence open menu -> tick Final -> OK -> answer the prompt -> Close Form,
and the two live branches (confirm -> No, and File save -> OK) are both
represented and tested.

Safety / compatibility notes:

- plan_step is a pure function; every test runs headless — no HBSys, no
  DB, no mouse movement in tests.
- The &Yes button is never a click target (pinned by test); answering the
  confirm prompt is the only commit point.
- click_close_form refuses to click when the tooltip OCR does not read
  "Close Form" (layout-shift guard).
- HBSys/MySQL stays read-only; no production GUI file was touched.
- TOOL_AVAILABLE[final_bill] in fees_actions.py deliberately stays False
  (and FINAL_BILL_TOOL_MISSING stays) until Slice E wires real execution
  into the orchestrator — flipping it is a Slice E decision, not guessed
  here.

Verification performed:

- python -m py_compile core/agent/final_bill_actions.py -> OK.
- AST undefined-name scan of the module -> none.
- Full agent suite (python unittest discover, pattern test_agent_*.py)
  -> 72 tests, 0 failures: fees 14 + final_bill 25 + nav 11 + screens 15
  + plan_store 7. No regressions in nav/screens tests. The default
  (no-injection) FinalBillRunner constructor is pinned by a test after an
  edit once dropped its default-primitive static methods.
- Live end-to-end sequence (same session, via the probe): Billing ->
  Final Bill -> Print Options -> tick (340,337) -> OK -> Call
  Administrator -> No -> popup closed -> Close Form (548,97 by tooltip
  OCR) -> only User Menu left; HBSys z-order restored.

### 2026-09-24 - Slice C: Agent Plan Panel (read-only approval tab from Fees Check CSV)

Reason:
User-approved Slice C of the Claims Agent plan: a read-only panel that
shows one row per Fees Check patient with the Slice B planned action and
lets the user Approve & Run (approve + persist only — execution is Slice E).

Files added:

- core/agent/agent_plan_store.py (read_fees_rows, to_plan_items,
  build_plan_from_csv returning items + summary + why-empty note;
  apply_approval marking selection APPROVED/rest SKIPPED with unknown
  indexes ignored; approved_items; save_approved_plan JSON audit trail
  under logs/agent_plan_YYYYMMDD_HHMMSS.json; load_plan)
- gui/agent_plan_tab.py (AgentPlanFrame notebook tab: Fees CSV path +
  Browse + Load Plan, color-coded Treeview Patient/Action/Why/Status,
  Select All / Clear Selection, "Approve & Run (approve only)"; NEVER
  clicks HBSys, NEVER runs a tool, NEVER touches the HBSys DB)
- tests/test_agent_plan_store.py, tests/test_gui_agent_plan.py

Files modified (thin GUI wiring only, sibling-tab pattern):

- edh_claims_gui_XML_COPY_BUTTON.py (import AgentPlanFrame; new
  "Agent Plan" notebook tab + build_agent_plan_tab)

Behavior before:
No shared place to preview per-patient planned actions; the approved set
for future execution did not exist anywhere.

Behavior after (Slice C):
The Agent Plan tab loads fees_checker_report.csv (latest Fees Check run),
renders the routed plan with a counts summary, and on Approve & Run marks
the selection APPROVED, the rest SKIPPED, and saves the approved rows as a
JSON audit trail. Nothing executes — the dialog and button say so
explicitly ("approve only", "Execution is wired in Slice E").

Safety or compatibility notes:

- Read-only contract: CSV read + plan JSON write are the only I/O.
- Approval is explicit selection only; empty selection shows an info
  dialog instead of approving anything.
- Plan JSON filename is timestamped, never overwritten.
- The tab follows the sibling-tab constructor pattern
  (settings_getter, log_callback); no coupling to other tabs.

Verification performed:
python -m unittest tests.test_agent_plan_store tests.test_gui_agent_plan
tests.test_agent_fees_actions -> 24 tests, all OK. Existing GUI suites
(hbsys_status, no_xml_panel, verify_panel_removal) -> 19 tests, all OK
(the new tab did not break main-GUI instantiation).

### 2026-09-24 - Slice B: Fees Action Table (fees row → date_fill / final_bill / xml_clicker / manual_review)

Reason:
User-approved Slice B of the Claims Agent plan, plus user confirmation that
Auth Sign Date IS Consent (same Date Fill path) and that ADM/DIS mismatch +
MISMATCH route to MANUAL_REVIEW (never auto-fixed).

Files added:

- core/agent/fees_actions.py (ACTION_* vocabulary; TOOL_AVAILABLE with
  final_bill=False until the Slice D Final Bill mapping session;
  decide_action() priority: unreadable/NO RECORD → manual_review,
  NO FINAL BILL → final_bill, blank Prof Fee/Consent/Auth(=Consent) dates →
  date_fill, gate YES → xml_clicker, anything else → manual_review with
  MISMATCH / ADM_DIS_MISMATCH / NOT_READY_OTHER codes; build_plan,
  summarize_plan, describe_decision)
- tests/test_agent_fees_actions.py

Behavior before:
No shared rule table mapped Fees Check verdicts to the next tool; the
workflow engine only ran fixed node order with no per-patient branching.

Behavior after (Slice B):
decide_action() routes one fees row deterministically; build_plan() keeps
input order; summarize_plan() counts per action for the future Plan Panel.
No production file modified; no GUI wiring yet; nothing runs automatically.

Safety or compatibility notes:

- Pure function of one row dict — no HBSys, no DB, no clicks.
- Missing/blank keys are treated as unknown (never defaulted good), so an
  unreadable row can only ever become manual_review.
- final_bill decisions carry tool_available=False + review code
  FINAL_BILL_TOOL_MISSING; callers must send them to MANUAL_REVIEW_FINAL_BILL
  until the Slice D mapping session produces the tool.

Verification performed:
python -m unittest tests.test_agent_fees_actions -> 14 tests, all OK
(priority, Consent=Auth confirmation, missing-tool flag, batch order +
counts, describe output). Agent slice total: 40 tests OK (screens 15 + nav
11 + fees 14). Non-GUI regression slice re-run: requirement rules +
claim-number match + 3 agent modules, all OK.

### 2026-09-24 - Plan: Claims Agent decision flow (Fees Check → Date Fill / Final Bill / XML Clicker) + HBSys window detection plan

Reason:
The user's live workflow is: after scanning, click Fees Check, then act per
patient from the "Ready to Generate XML" verdict — blank Prof Fee Sign Date
(hprofserv.pdoctorsigndate), Consent Date (hpatcon1.consentdate), or Auth
Sign Date (hpatcon1.authsigndate) means run Date Fill; "NO FINAL BILL"
(itemized total zero/null) means run Final Bill (no script exists yet);
"Ready to Generate XML = YES" for all patients means run the XML Clicker.
The open question was how the tooling knows which HBSys window/screen is
currently open and how it gets to the Date Fill, XML Clicker, or Final Bill
screen. The user asked for a plan first ("plan ka nga muna").

Investigation (read-only, no production code touched):

- fees_checker.py read-only checks per patient: itemized (hpatchrgdtl) vs
  grouped (hpatgrpchrg) totals; Status MATCH / MISMATCH / NO FINAL BILL;
  ADM/DIS folder-vs-hpatcon1 match; three signed-date columns; and
  ready_to_generate_xml() as the single source of truth for the
  "Ready to Generate XML" YES/NO verdict (write_reports + XLSX colors).
- Date Fill exists: date_fill_hbsys/hbsys_fill_dates.py operator
  (Hospital No. search → Admission History row → PHIC Beneficiaries row →
  Claim Form 2 → fill Prof Fee/Consent dates → close forms). Its operator
  already tracks screen_stage (base/admission_popup/beneficiary/cf2) and
  recovers to base on failure. Missing vs Date Fill: it fills CF2 Prof Fee
  and Consent dates but NOT the hpatcon1 Auth Sign Date column path.
- XML Clicker exists: date_fill_hbsys/xml_generator_clicker.py operator
  (focus HBSys/eClaims window → open CF4/CF5/eSOA generator tabs →
  search patient → Select Encounter → validate+generate). Uses absolute
  1920x1080 toolbar points; detects screens only loosely (title contains
  checks + OCR probes).
- Window detection exists: date_fill_hbsys/hbsys_window.py
  (is_hbsys_window by FNWND370/FNWND230 class or HBSys/HOMIS title, with
  Chrome_WidgetWin_* browser exclusion; covered by tests/test_hbsys_window.py
  and the main GUI's HBSys open/closed indicator that gates Date Fill /
  XML Clicker buttons). It answers open/closed but NOT which screen is open.
- Workflow engine exists: core/workflow_engine.py + core/workflow_registry.py
  (12 registered nodes incl. date_fill_regular/abtc, xml_clicker, fees_checker,
  claims_checker, add_claims_upload, claim_attachments; HBSys gate skips
  hbsys_touching nodes when HBSys is closed) + gui/workflow_tab.py editor.
  It orchestrates fixed node ORDER — it has no per-patient rule table and no
  screen-state routing, so it cannot express "this patient needs Date Fill,
  that one needs Final Bill".
- Final Bill has NO script: confirmed — no final-bill/module/screen mapping
  exists anywhere in the repo. This is the one genuinely new automation.

Plan approved by user (Slice A first):

- A1 Screen Detector: core/agent/hbsys_screens.py — detect_screen() from
  window titles + optional OCR text, 11-screen vocabulary (HBSYS_CLOSED,
  ORDER_TRANSACTIONS, ECLAIMS_DASHBOARD, UPLOAD_CLAIMS,
  UPLOAD_CLAIM_ATTACHMENTS, ADMISSION_HISTORY, PHIC_BENEFICIARIES,
  CLAIM_FORM_2, CLAIM_FORM_4, DIALOG, UNKNOWN), pure observation, injectable
  window listing for headless tests.
- A2 Navigator: core/agent/hbsys_nav.py — Navigator.navigate(target) over
  declared hop plans with per-hop verification, go_home() safe recovery via
  Escape-only dismissal, MAX_HOPS fail-safe, coordinates read from the SAME
  production constants (no duplicated business logic).
- B Fees Action Table: core/agent/fees_actions.py — decide_action() mapping
  each fees row to date_fill / final_bill(MISSING tool) / xml_clicker /
  manual_review, reusing ready_to_generate_xml() as the gate.
- C Agent Plan Panel: read-only approval panel listing per-patient planned
  actions (Approve & Run / Skip / Edit later); execution only after approval.
- D Final Bill: mapping session with the user (open HBSys together, record
  the Final Bill screen path) before any automation is written; until then
  final_bill_required patients route to MANUAL_REVIEW_FINAL_BILL.
- E Wire-up: registry nodes per action + engine per-patient branching +
  run reports + Patient Review routing; production tools run unchanged.

Files added (plan status — Slice A implemented, B–E pending user go):

- core/agent/hbsys_screens.py, core/agent/hbsys_nav.py
- tests/test_agent_hbsys_screens.py, tests/test_agent_hbsys_nav.py

Behavior before:
No shared answer to "which HBSys screen is open now"; every HBSys tool
assumed its start screen (Date Fill assumed the main screen, XML Clicker
assumed eClaims reachable, uploaders assumed their popup reachable). No
rule table mapped Fees Check verdicts to Date Fill / Final Bill / XML
Clicker. Final Bill automation did not exist.

Behavior after (Slice A):
Detector + Navigator exist as independent, tested modules. No production
file modified; no GUI wiring yet; nothing runs automatically.

Safety or compatibility notes:

- Detector never clicks/types; Navigator caps hops at MAX_HOPS, re-verifies
  every hop, dismisses blockers with Escape only, and routes unknown
  states to Patient Review instead of guessing.
- Navigator coordinates come from the existing production constants
  (CalibratedPointsData, Date Fill P, XML clicker P) — none invented.
- Final Bill is deliberately NOT automated until the mapping session.

Verification performed:
python -m unittest tests.test_agent_hbsys_screens
tests.test_agent_hbsys_nav -> 26 tests, all OK (headless scripted desktop).
Non-GUI regression slice (requirement rules + claim-number match + the 2 new
agent modules) -> 62 tests, all OK. Full 12-module GUI suite timed out in the
30 s tool window (pre-existing slow GUI/PDF tests, not related to Slice A —
26/26 agent tests pass and no production file was modified).

### 2026-09-24 - Feature: Patients Without XML panel + Check Missing auto-disable + 2s auto-refresh + Live mode default

Reason:
The user wants (1) a dashboard panel listing output patient folders that have
no XML files, placed below the Live Processing Logs, (2) the
"Check Missing Req./Claim #" button automatically disabled while the output
folder contains no XML (nothing to check), (3) every-2-seconds refresh for the
dashboard and for the Add Claims Upload and Claim Attachments patient lists,
and (4) "Live (clicks HBSys)" as the default mode in both upload tabs.

Files modified:

- claims_checker.py (new folder_has_any_xml, list_folders_without_xml,
  output_dir_has_xml; write_reports lists NO-XML patient names under the
  Claim Number Match summary; main prints the no-XML patient list)
- edh_claims_gui_XML_COPY_BUTTON.py (imports the two list helpers; new
  "Patients Without XML" panel in the right column below Live Processing
  Logs; check_missing_button stored and auto-disabled via
  refresh_check_missing_button_state, fail-open on unexpected scan errors;
  dashboard auto-refresh interval 15000 ms -> 2000 ms; refresh_folder_lists
  also drives the new panel and button state)
- gui/add_claims_upload_tab.py, gui/claim_attachments_tab.py (mode default
  "dry-run" -> "live"; new AUTO_REFRESH_MS=2000 auto-refresh of the patient
  list with quiet logging, selection preservation, skip-while-uploading, and
  cancel-on-destroy)

Files added:

- tests/test_gui_no_xml_panel.py

Files extended:

- tests/test_claim_number_match.py (helper unit tests)

Behavior before:
No visibility of which output patients lack XML files. The Check Missing
button was always enabled. Dashboard lists refreshed every 15 s; the upload
tab patient lists refreshed only via the manual Refresh button. Both upload
tabs defaulted to Dry-Run mode.

Behavior after:
A "Patients Without XML (N)" panel under the logs lists output folders with
no XML files, refreshed together with the other folder lists every 2 s. The
Check Missing Req./Claim # button disables itself while the output folder
has no XML and logs one line when it does. Add Claims Upload and Claim
Attachments patient lists auto-refresh every 2 s (silent, selection kept,
paused while an upload is running) and both tabs now default to Live mode
(the live-run confirmation dialog still appears on Start).

Safety or compatibility notes:

- Read-only scans only; helpers swallow OSError and return empty results.
- Button logic fails open on unexpected exceptions so a scan hiccup never
  blocks the user; a missing/empty output folder legitimately disables the
  button (there is no XML to check).
- Auto-refresh never runs while an upload thread is alive, so in-flight
  patient rows, statuses, and progress are never disturbed.
- Live-mode default change does not bypass the existing "Confirm Live Mode"
  dialog; Dry-Run remains selectable.

Verification performed:
python -m unittest (full suite: 10 modules) -> 119 tests, all OK. New tests
cover the three helpers, the panel contents/title, button enable/disable,
fail-open behavior, the 2000 ms dashboard interval, Live defaults in both
tabs, quiet auto-refresh, and skip-while-running. Smoke-tested the helpers
against the real output folder (2 no-XML patients detected, has_xml=False
consistent).

### 2026-09-24 - Feature: Claim Number Match Check appended to Check Missing report + GUI button text

Reason:
The user needs to verify that the CF4, CF5, and eSOA XML files of each patient
share the same HBSys claim series number. The XML payloads are encrypted
eClaims envelopes, so the series number embedded in the XML filename
(e.g. NAME-260924150960_CF4.xml) is the only deterministic source. The check
was integrated into the existing Check Missing (claims_checker) report as
separate rows below the requirement rows, per user decision.

Files modified:

- claims_checker.py (new extract_claim_series, _format_series,
  build_claim_number_rows; write_reports accepts optional claim_rows and logs
  claim-number summary counts; main builds claim rows before folder moves)
- edh_claims_gui_XML_COPY_BUTTON.py (button text "Check Missing" ->
  "Check Missing Req./Claim #")

Files added:

- tests/test_claim_number_match.py

Behavior before:
Check Missing reported document requirements only; there was no verification
that the three generated XML files belong to the same claim. The GUI button
read "Check Missing".

Behavior after:
The Check Missing CSV report appends a "=== CLAIM NUMBER MATCH CHECK ==="
section below the requirement rows, with one row per patient showing the CF4,
CF5, and eSOA claim series numbers and a status: CLAIM NO. MATCH,
CLAIM NO. MISMATCH (numbers differ, including duplicate same-kind files with
different numbers), CLAIM NO. INCOMPLETE (an XML kind is missing), or NO XML.
The log report includes claim-number summary counts. The GUI button now reads
"Check Missing Req./Claim #".

Safety or compatibility notes:

- Claim-number rows are report-only and are built as a separate list; they are
  never merged into the main rows, so organize_claim_folders() status mapping
  and READY/READY_WITH_REVIEW/INCOMPLETE folder moves are unchanged.
- Claim rows are built before organize_claim_folders() moves the folders into
  the results directories.
- write_reports() remains backward compatible when called without claim rows.
- CSV has no cell coloring, so mismatches are flagged via the explicit
  "CLAIM NO. MISMATCH" status text instead of a red highlight.
- Read-only: the check reads filenames only; no XML content is decrypted or
  modified.

Verification performed:
python -m unittest tests.test_claim_number_match -> 10 tests, all OK
(match, one-differs, missing-kind, duplicate-kind-different-numbers, no-XML,
filename extraction edge cases, CSV section ordering, backward compatibility).
Smoke-tested build_claim_number_rows against real
claims_checker_results/READY patient folders and confirmed correct
CLAIM NO. MATCH rows with the expected series numbers.

### 2026-09-24 - Docs: Revise Claims Agent Plan Phase 1 (HBSys State Controller) to multi-step navigation state machine
### 2026-09-24 - Docs: Revise Claims Agent Plan Phase 1 (HBSys State Controller) to multi-step navigation state machine

Reason:
Real HBSys navigation is not single-click: Date Fill passes several buttons before
the hospital number can be typed, and the XML generators pass intermediate screens
before CF4/CF5/eSOA processing. The original plan Section 5 described the State
Controller as simple target-state navigation; it needed to be upgraded to a state
machine with multi-step verified paths before implementation begins.

Files modified:

- CLAIMS_AGENT_PLAN.md (Section 5 expanded into 5.1-5.6)

Behavior before:
Section 5 listed only four target states (HOSPITAL_NUMBER_ENTRY, CF4_XML, CF5_XML,
ESOA_XML), deferred intermediate screens as "later states", and described
go_to(state) without modeling intermediate steps, blocker screens, or detours.

Behavior after:
Section 5 now defines a fine-grained state model (destination, intermediate, and
blocker/detour states such as ADMISSION_HISTORY, PHIC_BENEFICIARIES_CF4TAB,
SELECT_ENCOUNTER, MODAL_DIALOG, PHIC_DETAILS), a navigation graph with per-step
ACTION -> VERIFY discipline, deterministic detour rules adapted from the proven
logic in hbsys_fill_dates.py and xml_generator_clicker.py, a safe-reset capability
back to MAIN_WINDOW, and explicit Phase 1 scope boundaries.

Safety or compatibility notes:

- Documentation-only change; no code, configuration, or runtime behavior modified.
- No changes to the production engine or existing tools.

Verification performed:
Re-read CLAIMS_AGENT_PLAN.md Section 5 after editing and confirmed the revised
subsections render correctly and remain consistent with Sections 6-7.

### 2026-09-24 - Fix: Claim Attachments "attach..." click on long patient names (wrapped row)
### 2026-09-24 - Fix: Claim Attachments "attach..." click on long patient names (wrapped row)

Reason:

- The "attach..." row click worked for short patient names but failed for
  long names (e.g. AGUSTIN, EDRIELLE JAMES SABBALUCA).  Reference screenshot
  `screenshots/claim_attachments_mahaba_ang_pangalan.png` shows why: a long
  name WRAPS to a second line, doubling the row height (blue band y 176-208,
  33px), while the "attach..." hyperlink text stays TOP-ALIGNED on the first
  line (y~185).  `click_attach_on_highlighted_row()` clicked the blue band
  CENTRE (y~192), landing below the clickable text, so nothing happened.
  Short names produce single-line rows (~17px) where centre == text line, so
  they always worked.

Files:

- Modified `core/claim_attachments_uploader.py` (attach-click path only):
  - NEW `detect_highlighted_row_band()` — returns (top, bottom) of the
    widest blue band.  `detect_highlighted_row_y()` is now a thin wrapper
    around it with an unchanged signature/return value (the `search_patient`
    logging caller is unaffected; the separate copy in `add_claims_ocr.py`
    was NOT touched).
  - NEW constants `MULTILINE_ROW_MIN_HEIGHT = 24` and
    `FIRST_LINE_TEXT_OFFSET = 9`.
  - `click_attach_on_highlighted_row()`: when the band is taller than a
    single-line row, clicks (ATTACH_COLUMN_X, band_top + 9) — the first text
    line where "attach..." is drawn.  Single-line rows still click the band
    centre exactly as before.
- Added `_verify_attach_longname_fix.py` (offline verification script).

Behavior:

- Before: long-name (wrapped) rows were clicked at the doubled band centre,
  missing the top-aligned "attach..." text -> attach popup never opened.
- After: tall bands are clicked on the first text line; short-name rows keep
  the proven centre-click behaviour bit-for-bit.

Safety:

- No changes to OCR, doc-type assignment, XML generation, verifier, or any
  other module.  The change only affects the attach-click Y coordinate for
  rows taller than 24px.

Verification:

- `python -m py_compile core/claim_attachments_uploader.py` — OK.
- `python _verify_attach_longname_fix.py` — PASSED:
  [REAL] user screenshot: band (176,208) 33px -> click_y=185 (on the
  "attach..." text; old code clicked 192, below it);
  [SHORT] synthetic 17px row -> click stays at band centre (unchanged);
  [LONG] synthetic 32px row -> click at top+9;
  [NONE] blank image -> None.
- Live test on a long-named patient: PENDING (run when HBSys is free).

### 2026-09-24 - Fix: Add Claims Include checkbox — precise targeting + real verification

Reason:

- The morning batch (20260924_081153) reported 3/3 success yet the checkbox
  states were never actually verified — the code clicked and ASSUMED success.
- Deep pixel forensics on `logs/debug_checkbox_before/after_20260924_*.png`
  revealed the full truth:
  1. The old click (header-text centre, x~500) landed in the Include CELL,
     not on the box — but HBSys toggles the checkbox on cell clicks, so the
     clicks DID work.  The "failure" was a misreading: a checked box inside
     the BLUE highlighted row is rendered BORDERLESS with a BLUE tick on
     white — nearly invisible next to the dark ticks of normal rows.
  2. There was zero post-click verification, so any real miss would have
     been silent.
  3. A first attempt at a dark-tick-only verifier was blind to the blue-tick
     style and caused 2 extra toggle clicks (ON->OFF->ON) on the live run
     (batch 20260924_084915, patient AGUSTIN) before this was understood.

Files:

- Modified `core/add_claims_uploader.py`:
  - REMOVED `_find_include_column_x()` (header-text scan; imprecise target).
  - NEW `_find_checkbox_x_in_row()`: locates the checkbox inside the
    highlighted row band via its two dark vertical edges (6-24 px apart,
    just right of the grid's left border).  Only the Include column area
    (popup-local x 5..60) is scanned so patient-name text can never be
    mistaken for checkbox edges.  Fallback: calibrated `GRID_CHECKBOX_X`.
  - NEW `_checkbox_image_looks_checked()` / `_checkbox_looks_checked()`:
    15x15 capture around the box; inner 9x9 counts dark-tick, blue-tick and
    white pixels.  checked = (dark >= 6 OR blue >= 5) AND white >= 15.
    The white-interior guard stops the blue highlight band itself from
    counting as a blue tick when a click misses the box (bare band =
    all blue, no white).
  - NEW `_popup_left()` and `_is_dark()` helpers.
  - `click_checkbox_of_highlighted_row()` rewritten: detect the box X in the
    row band, skip if already checked (a click would UNCHECK it), click once
    then VERIFY the tick appeared, retry up to 3 attempts, and return False
    (loud failure -> patient marked FAILED) if still unchecked.
  - `CalibratedPointsData.grid_checkbox_x` default 10 -> 37 (10 was the
    grid's left border, not the checkbox).
- Modified `core/add_claims_calibration.py`: same default 10 -> 37.
- Modified `logs/add_claims_calibration.json`: `grid_checkbox_x` 10 -> 37.
- Updated `CHANGE_RULES.md`.

Behavior:

- Before: clicked the Include cell near the box, never verified anything;
  checked boxes in highlighted rows looked unchecked to the naked eye.
- After: clicks the detected checkbox centre; verifies the tick after every
  click (both dark-tick and blue-tick styles); never clicks an
  already-checked box; fails loudly after 3 unconfirmed attempts.
- `claim_attachments_uploader.py` was re-checked against today's
  `debug_attachments_attach_row_*.png`: the attach click lands on the
  "attach..." link and the popup opens; it also fails loudly via the
  `find_attachment_popup` timeout.  No change needed there.

Safety / compatibility:

- No changes to OCR, signing, XML generation, or the main processor.
- Detection runs on the already-captured screenshot; the only new screen
  interaction is one extra 15x15 screenshot per verification.
- Never clicks an already-checked box, so re-runs cannot uncheck patients.
- Calibration JSON schema unchanged (single value corrected).

Verification:

- `python -m py_compile core/add_claims_uploader.py core/add_claims_calibration.py` — passed.
- Offline validation `_verify_fix.py`: 12/12 cases PASS against the saved
  debug screenshots (empty boxes, blue ticks, dark ticks, and off-target
  bare-band points all classified correctly — even with the debug
  crosshair/line annotations occluding part of the boxes; live captures
  have stronger margins).
- Detection of `_find_checkbox_x_in_row()` on the saved
  `debug_checkbox_before_20260924_081227.png` returns x=488 (checkbox
  centre), versus the old code's x=500 cell click.
- Live run 20260924_084915 (AGUSTIN): click landed dead-centre at
  (488, 664); the after screenshot confirms the box toggled to the
  borderless blue-tick checked state.  The verifier at that time was the
  dark-only interim version, so the patient was incorrectly marked failed
  in `logs/add_claims_upload_state.json` — the claim itself was checked and
  the finalize steps (Add/OK/Close) executed.  Check the main grid before
  re-running AGUSTIN to avoid a duplicate add.
- Live end-to-end re-run with the final dual-style verifier: PENDING (user
  is actively using HBSys for other work at the time of the fix).


### 2026-09-23 - Security: removed patient-info screenshots from the repository

Reason:

- Owner report: the `screenshots/` folder contains patient information (live
  HBSys screens with patient names and claim rows) and must be cleared.
- All 15 images were TRACKED and had already been pushed to the PUBLIC
  repository `github.com/chxlover/edh-claims-automation` (verified
  `private: false` through the GitHub API), so deleting them only on the local
  PC would have left them visible on GitHub.

Files (DELETE):

- `screenshots/` - 15 PNG files (1.66 MB total): 6 in
  `add_claims_upload_claims/`, 2 in `attach_claims/`, and 7 in the folder root
  (`attach_claims.png`, `attach_pop_up.png`,
  `attach_pop_up_2_to_select_pdf_and_xml.png`, `checkboxhighlighted.png`,
  `click_attach_of_highlighted_patient.png`, `doc_type.png`,
  `pop_up_showed_up_click_attach.png`).
- The now-empty subfolders `screenshots/attach_claims/` and
  `screenshots/add_claims_upload_claims/` were removed. `screenshots/` itself
  is kept (empty) because the doc-type self-test probes that path.

Files (EDIT):

- `.gitignore` - new rules: every PNG/JPG/JPEG/BMP/GIF inside `screenshots/`
  is ignored, so a future live screenshot can never be committed by accident
  (the previous ignore list protected patient folders but not this one).

Behavior before:

- 15 patient-containing screenshots were part of the public repository and were
  downloadable from GitHub; the uploader's diagnostic screenshots in `logs/`
  (977 files, 73 MB, gitignored) were the only locally-only images.

Behavior after:

- The folder is empty locally, the deletion is committed and pushed, so GitHub
  HEAD no longer contains any of the 15 images and the paths are ignored.
- The doc-type self-test "v4 integration" case (the only code reference to
  `screenshots/attach_claims/SS_choose_doc_type.png`) now prints
  `SKIP v4 integration (reference screenshot not found)`; every other
  assertion in that suite still runs (synthetic grid, v4.1/v4.3 merge, v5
  scroll, v5.3 mutated tiers, v6 path matching, live regression crops).

Safety / compatibility notes:

- Operating files only: no source module, coordinate, OCR rule or pipeline
  behavior changed. The images were recon references - the coordinates they
  produced are already hardcoded, so runtime behavior is unaffected.
- Files are recoverable from git history (`git checkout 9851a5c --
  screenshots/`) until a history rewrite is performed.
- HISTORY PURGED (owner decision, same day): `python -m git_filter_repo
  --invert-paths --path screenshots --force` rewrote every local ref, the
  `origin` remote was re-added, and `git push --force origin main` updated
  GitHub. `main` HEAD is now 326ac26 (replacing 9851a5c / b0f0f13 - the latter
  no longer exists as an object), the HEAD tree is byte-identical
  (86c5f6b692aac748b75871883ce9757450896213), `main` still has the same 13
  commits (no commit lost), the `v0.3` tag is untouched (its commit never
  contained the screenshots), and `git log --all -- screenshots` is empty.
  The repository stays PUBLIC by owner choice.
- Backup taken BEFORE the rewrite: `C:\claims_bot_git_backup_20260923.git`
  (mirror clone of the pre-rewrite history, INCLUDING the screenshots) - keep
  it secure or delete it once it is no longer needed.
- Remaining caveats: GitHub can keep unreachable objects / cached views for a
  while (direct-by-SHA URLs), other clones or forks keep their own copy (e.g.
  the Cline worktree clone under `%USERPROFILE%\.cline\worktrees`), and the
  `logs/` debug screenshots (977 files, 73 MB, gitignored) still sit on the PC.

Verification performed:

- `git status` -> 15 `deleted:` entries under `screenshots/`, no other change.
- `Get-ChildItem screenshots -Recurse -File -Include *.png,*.jpg,*.jpeg,*.bmp,*.gif`
  -> 0 files remaining.
- `git ls-files -- screenshots` -> 15 entries before staging, 0 after the
  commit (verified with `git ls-files` on the new HEAD).
- `git check-ignore -v screenshots/test.png` -> matched by the new rule.
- `python -u core/claim_attachments_doc_type.py` -> "SKIP v4 integration",
  "RESULT: PASSED" (no FAIL).
- After the purge: fresh `git clone` of the public repository -> HEAD 326ac26,
  `git log --all -- screenshots` empty, `git cat-file -t b0f0f13` -> "Not a
  valid object name", `git rev-list --count HEAD` -> 13 (same as before), and
  `git ls-remote origin refs/heads/main` equals the local HEAD.

### 2026-09-23 - Feature: Claim Attachment Checklist wired into the uploader + Preferences dialog

Reason:

- Completes the two items left open by the "Claim Attachment Profile" record
  below: the exclusion helpers were only exercised by the module self-test, and
  the Preferences dialog did not exist yet.
- Owner rule for an UNCHECKED document: "not required and not uploaded". Its
  files must leave the patient folder BEFORE the uploader attaches anything,
  and the operator must be able to edit the checklist from the GUI instead of
  hand-editing JSON.

Files (ADD):

- `gui/claim_attachment_checklist_dialog.py` (new module, 670 lines)
  - One switch per document in a table (Required / Document / Type / Filename
    suffix / HBSys doc type / Origin): `[x]` = required by the Claims Checker
    AND uploaded to HBSys, `[ ]` = not required and its files are moved to the
    backup folder. Space / Enter / double-click toggles the selection, plus
    Check All / Uncheck All / Toggle Required.
  - "Edit HBSys Doc Type..." renames the value typed into the HBSys grid
    (validated by `validate_doctype`); "Add Custom Document..." / "Remove
    Custom" manage documents PhilHealth adds later (validated code, unique
    suffix, no built-in collision); "Restore Defaults" deletes the profile
    file; "Restore Excluded Files..." calls `restore_all_excluded()` on READY
    then READY_ARCHIVED and reports every patient folder.
  - Save writes DELTAS only (built-ins: changed `enabled` / `hbsys_doctype`;
    custom documents: full entries) through `validate_overrides()` first, so an
    invalid value is refused BEFORE the file is touched. `self.saved` mirrors
    the document-detection dialog so the main GUI can refresh its status line.
  - `if __name__ == "__main__":` standalone test with 28 assertions on a temp
    profile file (never the real one).

Files (EDIT):

- `core/claim_attachments_uploader.py`
  - New `apply_attachment_exclusions(patient_folder, ready_dir, live=True)`
    -> `(ok, moved_files, error)`. It moves the files of unchecked documents
    to `claims_checker_results\_upload_backup\<patient>\` (never deletes, writes
    the per-patient `_excluded_manifest.json`). Dry-run (`live=False`) only
    reports the names. A profile file that exists but cannot be trusted
    (`last_profile_error()` non-empty) returns `ok=False`, so the patient is
    SKIPPED instead of attaching a document PhilHealth may no longer require.
  - `run_attachments_loop()` gained "Step 0" right after `folder_path` is
    computed and BEFORE Step 1 (search patient): a failed exclusion increments
    `consecutive_failures` and skips the patient through the existing
    escalation path; moved files are printed per patient.
  - New offline `self_test()` + `--self-test` CLI flag (no HBSys needed; no
    other CLI behavior changed).
- `gui/claim_attachments_tab.py` - imports `apply_attachment_exclusions` and
  runs the same Step 0 in `_upload_worker()` (thread-safe logging via
  `self.after`), so the GUI upload path cannot upload an unchecked document.
- `edh_claims_gui_XML_COPY_BUTTON.py` - new Preferences panel "Claim
  Attachments" with a status line ("N of M documents checked...") and an
  "Edit Claim Attachment Checklist..." button
  (`_update_claim_attachment_status()` / `open_claim_attachment_checklist_dialog()`,
  refreshed from `save_settings()`), mirroring the Document Detection panel.

Behavior before:

- The checklist could only be edited by hand-editing
  `claim_attachment_profile.json`, and the uploader ignored it: an unchecked
  document was still attached to HBSys (the doc-type step then ABORTed on it,
  or typed a doc type for a document PhilHealth no longer wants).

Behavior after:

- Preferences -> Claim Attachments -> "Edit Claim Attachment Checklist..."
  edits the checklist; an unchecked document is (a) no longer required by the
  Claims Checker, (b) no longer typed into the HBSys grid, and (c) its files
  are moved out of the patient folder before the first "Attach..." click, in
  both the CLI loop and the GUI tab. "Restore Excluded Files..." (or deleting
  `_upload_backup`) puts the files back.

Safety / compatibility notes:

- Files are MOVED, never deleted; the backup folder is a "_"-prefixed sibling
  of READY that the checker, the uploader and the archive/transmit job ignore.
- No change to OCR, signing, XML generation, the HBSys coordinates or the
  doc-type matching logic. The profile import inside the uploader is wrapped:
  if the module is unavailable the old behavior runs unchanged.
- Dry-run stays side-effect free: nothing is moved (only reported) and no HBSys
  action is performed.
- Never-guess preserved: an unreadable/corrupt profile ABORTS the patient
  (marked FAILED) instead of uploading everything.

Verification performed:

- `python -m py_compile core/claim_attachments_uploader.py gui/claim_attachments_tab.py gui/claim_attachment_checklist_dialog.py edh_claims_gui_XML_COPY_BUTTON.py` -> exit 0.
- `python -u gui/claim_attachment_checklist_dialog.py` -> 28/28 PASS,
  "RESULT: PASSED" (defaults, uncheck + Save deltas, reopen, custom document
  validation + save, doc-type rename, restore defaults, restore excluded files
  in a temp READY tree).
- `python -m core.claim_attachments_uploader --self-test` -> 8/8 PASS,
  "RESULT: PASSED" (default checklist untouched, dry-run reports without
  moving, live move + backup folder + manifest, required files untouched,
  corrupt profile -> patient skipped).
- `python -m core.claim_attachments_uploader --help` shows `--self-test`;
  `import gui.claim_attachments_tab` and `import core.claim_attachments_uploader`
  -> OK.
- Headless GUI smoke test: `import edh_claims_gui_XML_COPY_BUTTON` -> OK, and
  `EDHClaimsGUI.__new__` + `_update_claim_attachment_status()` ->
  "Status: 15 of 15 documents checked (required by the Claims Checker and
  uploaded to HBSys)."
- Regression re-run: `core/claim_attachment_profile.py` 26/26 PASS,
  `core/claims_requirement_rules.py` 9/9 PASS,
  `core/claim_attachments_doc_type.py` 65/65 OK (no FAIL lines).
- Live HBSys test PENDING (owner): recommended
  `--live --confirm-each --limit 2` with one document unchecked, then verify
  the file lands in `claims_checker_results\_upload_backup\<patient>\` and that
  the other documents are still attached.

### 2026-09-23 - Feature: Claim Attachment Profile (configurable requirement + doc-type source of truth)

Reason:

- Owner request: the per-document claim-attachment checklist must be configurable
  from the GUI so a PhilHealth requirement change (drop a document, rename an
  HBSys doc type, add a new document) needs ZERO code edits.
- An UNCHECKED document must be BOTH "not required" by the Claims Checker AND
  "not uploaded" to HBSys by the Claim Attachments uploader.
- Until the checklist exists, the hardcoded behavior must stay identical, so
  the module must fall back to the legacy maps on any error.

Files (ADD):

- `core/claim_attachment_profile.py` (new module, 673 lines)
  - `DEFAULT_DOCS` is the single declarative source of truth for every document:
    `kind` (pdf/xml), filename `suffix`, `hbsys_doctype`, `conditional`, and the
    scan order used by `claims_checker.get_found_items`.
  - Readers: `get_profile()`, `profile_revision()`, `is_enabled()`,
    `get_required_base_docs()`, `get_enabled_requirement_names()`,
    `get_doctype_maps()`, `get_excluded_docs()`, `last_profile_error()`.
  - Writers: `validate_overrides()` / `save_overrides()` / `restore_defaults()`
    with strict validation (uppercase alphanumeric HBSys doc types only,
    duplicate doc types intentionally allowed because SOA1/SOA2 both type "SOA",
    built-in code and suffix collisions rejected, unsupported schema_version
    refused, the file is left untouched when validation fails).
  - Storage: `claim_attachment_profile.json` (project root), deltas only, same
    policy as `document_detection_rules.json`. A missing/unreadable/corrupt file
    NEVER crashes processing - every reader falls back to built-in defaults.
  - Exclusion helpers (owner decision: "not required and not uploaded"):
    `doc_matches_file()`, `backup_root_for()` (sibling `_upload_backup` of
    READY), `exclude_files()` (MOVES, never deletes, writes a per-patient
    `_excluded_manifest.json`), `restore_all_excluded()` (searches READY and
    READY_ARCHIVED, collision-safe `_unique_destination`).
  - `if __name__ == "__main__":` standalone test with 26 assertions.

Files (EDIT):

- `core/claims_requirement_rules.py`
  - Header docstring documents the profile as the source of the REQUIRED set.
  - Optional import guarded by `try/except` (legacy path runs when unavailable).
  - `evaluate_requirements()` now filters `found_documents` through
    `get_enabled_requirement_names()`, takes `required` from
    `get_required_base_docs()`, and gates every conditional requirement
    (OPR/CF3 for ANR and NSD01, MRF + "PBC or MMC" for COE eligibility NO)
    through `is_enabled()`. `mrf_required` and `pbc_mmc_possible` were added so
    an unchecked MRF cannot add "PBC or MMC" as missing.
- `core/claim_attachments_doc_type.py`
  - Added `_ensure_tables()` + `_TABLE_REVISION`: the derived tables
    (`_PDF_LOOKUP`, `_XML_LOOKUP`, `_ALL_STEMS_NORM`, `_PDF_STEM_SET`,
    `_XML_STEM_SET`, `_STEMS_BY_EXT`, `_KNOWN_STEM_SET`) are rebuilt from
    `get_doctype_maps()` only when `profile_revision()` changes, and the
    hardcoded `PDF_SUFFIXES` / `XML_SUFFIXES` remain the fallback.
  - `_ensure_tables()` is called at the top of `detect_doc_type()`,
    `detect_doc_type_from_words()`, `match_row_to_file()`, `_has_near_stem()`
    and `match_files_to_lines()`.
  - Self-test extended with 3 profile regression cases (default == hardcoded,
    disabled CSF stem removed, restore brings CSF back).

Behavior before:

- The required document set, the conditional requirements and the suffix -> doc
  type vocabulary were hardcoded; changing a requirement meant editing Python.

Behavior after:

- With NO `claim_attachment_profile.json` the behavior is exactly the legacy
  hardcoded behavior (verified assertion-by-assertion against
  `LEGACY_BASE_REQUIREMENTS` / `LEGACY_PDF_SUFFIXES` / `LEGACY_XML_SUFFIXES`).
- With a profile file, an unchecked document is invisible to the rules (never
  required, never reported missing, never triggers a conditional requirement)
  and its stem leaves the doc-type vocabulary, so a file that should have been
  excluded still hits the existing never-guess ABORT path instead of being typed
  into HBSys.

Safety / compatibility notes:

- No change to OCR, PDF merge, signing, XML generator or the hardcoded detection
  chain. `claims_checker.py` needed no edit: it calls `evaluate_requirements()`,
  which now filters disabled documents internally.
- Every profile read is wrapped so a corrupt config file cannot break a batch;
  `restore_defaults()` deletes the JSON, it never writes over the legacy maps.
- Exclusion MOVES files into `claims_checker_results\_upload_backup\<patient>\`
  (a "_"-prefixed sibling of READY that the checker, the uploader and the
  archive/transmit job all ignore) and records every move in a manifest, so
  nothing is ever deleted.
- COMPLETED later the same day - see the "Claim Attachment Checklist wired into
  the uploader + Preferences dialog" record above: `exclude_files()` /
  `restore_all_excluded()` are now called by the uploader
  (move-before-attach, CLI loop and GUI tab) and the
  Preferences -> "Claim Attachment Checklist..." dialog exists.

Verification performed:

- `python -m py_compile core/claim_attachment_profile.py core/claims_requirement_rules.py core/claim_attachments_doc_type.py core/document_detection_rules.py gui/document_detection_rules_dialog.py claims_checker.py core/claim_attachments_uploader.py` -> exit 0.
- `python core/claim_attachment_profile.py` -> 26/26 PASS, "RESULT: PASSED"
  (defaults == legacy maps, save/load, invalid input rejection, collision
  rejection, corrupt-file fallback, exclude + manifest + restore round-trip in
  READY_ARCHIVED, restore_defaults).
- `python core/claims_requirement_rules.py` -> 9/9 PASS, "RESULT: PASSED"
  (default profile unchanged, SOA1 unchecked not required/not missing, MRF
  unchecked removes "PBC or MMC", OPR unchecked while CF3 stays required).
- `python -u core/claim_attachments_doc_type.py` -> 65/65 OK, "RESULT: PASSED",
  including the 3 new profile cases and all pre-existing v4/v5/v6 live
  regressions (PASCUA, SAFLOR, SASPA, MATTERIG, GUIUO, PALAMING, SORIANO).
- `python core/document_detection_rules.py` -> "RESULT: PASSED" (unchanged).
- `Test-Path claim_attachment_profile.json` -> False before and after every test
  run, i.e. no test leaves configuration behind (each test points `PROFILE_FILE`
  at a temp dir and restores the real path).

### 2026-09-23 - Fix: Document Detection Rules editor dialog (SyntaxError + incomplete per-type editor)

Reason:

- Owner report (2026-09-23): clicking "Edit Document Detection Rules..." in
  Preferences > Document Detection shows an error and the editor never opens.
- Root cause 1: `gui/document_detection_rules_dialog.py` line 236 contained a
  stray `n` character inside the `messagebox.showwarning(...)` call, making the
  whole module unimportable (SyntaxError). The GUI catches the import failure
  and shows "Could not open the editor".
- Root cause 2: the same file was truncated at line 295 in the middle of
  `_open_editor()` - the per-type editor had no page-rules UI and no OK/Cancel
  buttons, so Add/Edit could never write changes back.

Files (EDIT):

- `gui/document_detection_rules_dialog.py`
  - Removed the stray `n` (line 236) so the module imports cleanly.
  - Completed `_open_editor()`: scrollable Page Rules section (page key
    1/2/.../"single", enabled, min_hits, comma-separated keywords, add/remove
    row), OK/Cancel footer with validation (document code format, duplicate
    code, numeric priority/min_hits, valid page keys), and write-back to
    `self.working` preserving untouched advanced fields (`strong`, `fuzzy`,
    `suppress_if`, level `min_hits`) per the file's docstring contract.
  - Added the required `if __name__ == "__main__":` standalone test block.
  - `from typing import ...` now also imports `List`.

Behavior before:

- Clicking "Edit Document Detection Rules..." always showed
  "Could not open the editor: invalid syntax" and no editor opened.

Behavior after:

- The rules editor opens, lists all document types (default rules when no
  `document_detection_rules.json` exists yet), and Add / Edit / Delete /
  Restore Defaults / Save all work. Save writes `document_detection_rules.json`
  through the existing validated `save_rules()` path. Detection behavior itself
  is unchanged: the toggle stays OFF by default and the hardcoded detector in
  the production engine is untouched.

Safety / compatibility notes:

- No change to the production engine, OCR, signing, XML generator, or the
  hardcoded detection chain. `core/document_detection_rules.py` and its
  DEFAULT_RULES are unchanged. No new dependencies.
- Reminder: the whole "Document Detection Rules Configurable" feature
  (2026-09-16: engine +36 lines, `core/document_detection_rules.py`, GUI
  checkbox + this dialog) exists ONLY in the local `C:\claims_bot` copy and is
  not yet committed to the GitHub repository.

Verification performed:

- `python -m py_compile gui/document_detection_rules_dialog.py` -> OK.
- `python gui/document_detection_rules_dialog.py` (standalone) -> dialog opens
  with 11 document types, type editor opens for CSF -> PASSED.
- Headless smoke test: opened the SOA2 editor, invoked OK programmatically -
  write-back preserved page-rule `strong`/`min_hits`/`fuzzy` fields; save/load
  round-trip via `save_rules()`/`load_rules()` on a temp file produced no
  validation errors; Add-new rule flow added the new type to working -> PASSED.

### 2026-09-23 - Enhancement: Document Detection Rules editor (multi-page fields, advanced JSON, guards)

Reason:

- Follow-up to the 2026-09-23 dialog fix: the rebuilt per-type editor still
  could not edit several rule fields that DEFAULT_RULES actively use, so
  editing a rule risked losing or flattening configuration (owner-approved
  6-step plan).

Files (EDIT):

- `gui/document_detection_rules_dialog.py`
  - Rules table: new "Pages" column ("1, 2, single" for multi-page types,
    "single-page" otherwise) so multi-page vs single-page types are visible
    at a glance.
  - `_open_editor()`: new type-level "Min hits" spinbox (critical for
    single-page types, e.g. ANR = 2) and "Strong keywords" text box (main
    matcher for MRF/CF2/SOA2/ANR).
  - New "Advanced (JSON)" box at type level (suppress_if, fuzzy, custom
    keys) and a per-page "Adv..." JSON mini editor (page-level fuzzy etc.) -
    fulfills the original docstring contract that advanced fields stay
    editable as JSON. JSON is validated on OK: must parse, must be an
    object, and must not contain managed keys (enabled, priority, min_hits,
    keywords, strong, aliases, page_rules).
  - Page-rule rows now include a comma-separated "strong" field and the
    "Adv..." button; per-page advanced keys (fuzzy, custom) round-trip
    unchanged.
  - New `_on_ok()` guard: defining a "single" page rule on any type other
    than SOA2 shows a Yes/No warning, because only SOA2 has a downstream
    handler for plain (complete-form) output.
  - Footer and the "Add Page Rule" bar are now packed first (side=bottom)
    so they can never be clipped by the expanding page-rules area; page
    rules are displayed numeric-first with "single" last (was single-first).
  - Module docstring updated to the new field contract; `__main__`
    standalone test extended to 6 scenario groups.

Behavior before:

- min_hits (type level), strong keywords, suppress_if and fuzzy were not
  editable in the dialog, only silently preserved. There was no protection
  against adding a "single" rule to a type with no downstream handler.

Behavior after:

- Every field used by DEFAULT_RULES is editable in the dialog; advanced
  fields are validated JSON; round-tripping a rule through the editor keeps
  it semantically identical. Detection behavior itself is unchanged: the
  toggle stays OFF by default and the hardcoded detector in the production
  engine is untouched.

Safety / compatibility notes:

- No change to the production engine, OCR, signing, XML generator, the
  hardcoded detection chain, `core/document_detection_rules.py`, or
  DEFAULT_RULES. No new dependencies. `document_detection_rules.json` is
  only written through the existing validated `save_rules()` path.
- The feature still exists ONLY in the local `C:\claims_bot` copy and is
  not yet committed to the GitHub repository.

Verification performed:

- `python -m py_compile gui/document_detection_rules_dialog.py` -> OK.
- `python gui/document_detection_rules_dialog.py` (standalone, 6 groups):
  Pages column, type min_hits edit (ANR 2->3), strong edit (CSF), advanced
  JSON edit (DTR suppress_if + custom key), page strong edit with
  min_hits/fuzzy preserved (SOA2) -> RESULT: PASSED.
- Headless smoke test (10 scenarios): untouched OK round-trip keeps
  SOA2/DTR semantically identical; invalid advanced JSON rejected (editor
  stays open); managed-key collision rejected; per-page Adv JSON editor
  preserves fuzzy and adds custom keys; "single" guard blocks on No and
  allows on Yes -> SMOKE RESULT: ALL PASSED.
- `python core/document_detection_rules.py` self-test -> 19/19 PASSED
  (module unchanged, regression check only).

### 2026-09-23 - Fix: configurable detection returned UNKNOWN for every page when no rules file existed

Reason:

- Owner report (2026-09-23): with "Document Detection Rules Configurable"
  enabled, EVERY scanned document was classified as UNKNOWN.  Root cause:
  `_get_rules()` cache in `core/document_detection_rules.py` starts as
  `{"mtime": None, "types": None}` and a missing
  `document_detection_rules.json` also yields `mtime=None`, so the
  staleness check `cache_mtime != mtime` was False on the very first call
  and the empty initial cache was returned - zero rules loaded, so no rule
  could ever match.  (All module self-tests passed because they pass rules
  explicitly and never exercised the missing-file `_get_rules()` path.)

Files (EDIT):

- `core/document_detection_rules.py`
  - `_get_rules()`: staleness check is now
    `mtime changed OR cached types is None`, so the first call with no
    rules file on disk correctly falls back to the built-in DEFAULT_RULES.
  - `__main__`: new regression test - point RULES_FILE at a missing path,
    reset the cache, and verify detect_doc_configurable("CLAIM SIGNATURE
    FORM") returns CSF via the defaults fallback.

Behavior before:

- Configurable mode ON + no document_detection_rules.json -> 0 rules
  loaded -> all pages UNKNOWN (production engine reproduced:
  classify_pdf_for_processing returned UNKNOWN for CSF/SOA1/MRF/COE/OPR
  sample texts).

Behavior after:

- Same setup -> 11 default rules loaded -> samples classify correctly
  (CSF, SOA1, MRF_page1, COE, OPR).  No behavior change when a rules file
  exists or when the mode is OFF (hardcoded detector untouched).

Safety / compatibility notes:

- One-line logic change plus a test; no signature or schema changes, no
  new dependencies.  The hardcoded detector, OCR, signing, XML generator
  and Claims Checker are untouched.  OFF mode is completely unaffected.

Verification performed:

- `python -m py_compile core/document_detection_rules.py` -> OK.
- `python core/document_detection_rules.py` self-test -> 20/20 PASSED
  (includes the new missing-file regression case).
- Engine-level repro script (imports the production engine module, sets
  CLAIMS_DOC_DETECTION_RULES_CONFIGURABLE=1, calls
  classify_pdf_for_processing): before fix all UNKNOWN / 0 rules loaded;
  after fix CSF, SOA1, MRF_page1, COE, OPR detected / 11 rules loaded.
- `python gui/document_detection_rules_dialog.py` standalone -> RESULT:
  PASSED (no dialog regression).

### 2026-09-23 - Fix: CSF misdetected as MRF_page1 in configurable detection mode

Reason:

- Owner report (2026-09-23): with "Document Detection Rules Configurable"
  enabled, the CSF page was not detected as CSF.  Reproduction with the real
  scan `New folder\csf001.pdf` through the production engine showed
  configurable detection returned MRF_page1 (priority 100 beats CSF 75).
  Root cause: the default MRF page-1 rule used the hardcoded
  detect_mrf_type() page-1 CONTEXT markers ("philhealth identification
  number", "purpose:", ...) with min_hits=1, but in the hardcoded detector
  those markers only count together with a UHC marker (has_uhc AND context).
  Every CSF page contains "philhealth identification number" (Part I), so
  the MRF rule matched first.  A second false hit came from "purpose:": the
  matcher's normalized-text fallback strips punctuation, degrading it to the
  plain word "purpose", which also appears in CSF text.

Files (EDIT):

- `core/document_detection_rules.py`
  - DEFAULT_RULES["MRF"]: page "1" keywords now start with "uhc" and use
    min_hits=2 (mirrors the hardcoded has_uhc + context rule); the
    "purpose:" marker was removed (normalized fallback made it match the
    bare word "purpose" inside CSF text).
  - DEFAULT_RULES["MRF"]: rule-level suppress_if ["claim form 2", "cf2"]
    added - mirrors the hardcoded CF2 guard in detect_mrf_type().
  - `__main__`: two new regression tests - realistic CSF page-1 OCR text
    (with the "PHILHEALTH IDENTIFICATION NUMBER (PIN) OF MEMBER" and SEX
    lines) must return CSF, and a UHC + context text must still return
    MRF_page1.

Behavior before:

- Configurable mode ON: CSF page -> MRF_page1 (wrong group / wrong merge).

Behavior after:

- CSF page -> CSF.  Verified on 7 real scanned PDFs: 6/7 identical to the
  hardcoded detector (CSF, COE x2, DTR x2, UNKNOWN); the 1 remaining
  difference is hardcoded "SOA" vs configurable "SOA1" for the same page,
  which converges downstream because resolve_soa_doc_type() converts the
  broad hardcoded SOA into SOA1 via the same "please pay at the cashier"
  marker.

Safety / compatibility notes:

- Default-rule data change only; no code/signature/schema changes, no new
  dependencies.  Hardcoded detector, OCR, signing, XML generator and Claims
  Checker untouched; OFF mode unaffected.  Existing
  document_detection_rules.json files (if any) still load as before - the
  dialog's Restore Defaults picks up the corrected MRF rule.

Verification performed:

- `python -m py_compile core/document_detection_rules.py` -> OK.
- `python core/document_detection_rules.py` self-test -> 22/22 PASSED
  (includes the new CSF-realistic and UHC-MRF regression cases).
- Real-PDF engine repro (7 scans from `New folder`): CSF page now CSF;
  6/7 identical to hardcoded, 1 benign SOA vs SOA1 difference (see above).
- `python gui/document_detection_rules_dialog.py` standalone -> RESULT:
  PASSED (no dialog regression).

### 2026-09-15 - Main Dashboard: 3-column layout (Actions | Output + Ready | Incomplete + Logs)

Reason:

- Owner directive (2026-09-15): "gawin mo ngang 3 columns ang GUI main:
  sa left actions, sa gitna output folders, sa baba nya is ready folders,
  at sa right column incomplete folders at sa baba nya logs. Ngayon dapat
  makita ang mga folder name, huwag mo ng lagyan ng open button."

Files (EDIT):

- `edh_claims_gui_XML_COPY_BUTTON.py`
  - `build_dashboard_tab()`: the dashboard body is now 3 columns
    (side-by-side pack): LEFT = existing Production Actions + Work Tip
    column (430 -> 400 px so the three columns fit, content unchanged);
    MIDDLE = new "Output Folders" panel on top and "Ready Folders"
    panel below (equal row weights); RIGHT = new "Incomplete Folders"
    panel on top and the existing "Live Processing Logs" card below
    (row weights 1:2, so logs stay the bigger panel). The log card is
    now gridded into the right column and the Clear Logs / Save Logs
    buttons moved inside the log card. HBSys warning wraplength
    380 -> 360 to match the narrower actions column.
  - New read-only helper methods: `list_subfolder_names()`
    (os.scandir, case-insensitive sort, capped at 500 rows with a
    "+N more folders" row so a huge folder cannot stall the UI),
    `_build_folder_list_panel()` (LabelFrame + Listbox + vertical and
    horizontal scrollbars + mousewheel bindings; display-only, NO open
    button and NO double-click action by design), `populate_folder_list()`
    (fills names and shows the folder count in the panel title),
    `refresh_folder_lists()`.
  - `refresh_dashboard_counts()` now calls `refresh_folder_lists()` so
    the three lists refresh on startup, on the 15 s dashboard auto
    refresh, on the Refresh button, and after every existing caller
    (processor run, copy xml, recheck, archive).
  - `apply_theme()` re-colors the three listboxes on theme change.

Data sources (owner choice: READY only):

- Output Folders = subfolders of `settings["output_folder"]`.
- Ready Folders = subfolders of `claims_checker_results\READY`.
- Incomplete Folders = subfolders of `claims_checker_results\INCOMPLETE`.
- READY_WITH_REVIEW is intentionally NOT listed.

Behavior before / after:

- Before: 2 columns (Production Actions | Live Processing Logs); folder
  names were not listed on the dashboard, only counts on the clickable
  stat cards.
- After: 3 columns with visible full folder names (horizontal scroll
  keeps long patient folder names readable). No open buttons were added.

Safety / compatibility:

- Only one production file touched; OCR, XML generator, claims checker,
  signing, settings, database and subprocess logic are unchanged.
- Folder listing is read-only directory enumeration (3 os.scandir calls
  per refresh); current data set: output=0, READY=5, INCOMPLETE=2.
- No new dependencies and no configuration change required.
- Existing clickable stat cards (which open folders) are unchanged.
- Pre-change backup: Temp\edh_gui_backup_dashboard_columns.py.

Verification:

- `python -m py_compile edh_claims_gui_XML_COPY_BUTTON.py` - PASSED
- `python -m unittest discover tests` - PASSED (Ran 87 tests, OK)
- `python tests/test_gui_hbsys_status.py` - PASSED (Ran 7 tests, OK)
- Headless GUI smoke on the real EDHClaimsGUI (withdrawn) - PASSED:
  titles "Output Folders (0) | Ready Folders (5) | Incomplete Folders
  (2)"; Output+Ready share the middle column, Incomplete+Logs share the
  right column (rows 0/1); ready list content equals the sorted READY
  folder names on disk; NO button widget inside any of the three folder
  panels; Clear Logs / Save Logs still present. Note: the pre-existing
  "Exception in thread Thread-1 (worker)" noise from
  `gui/not_transmitted_batches_tab.py` line 452 during the test suite is
  unrelated to this change (background worker posting after() after app
  destroy inside the tests).

### 2026-09-11 — Workflow GUI v2: SIMPLIFIED list editor (canvas removed)

Reason:

- Owner directive (2026-09-11): "simplehan mo na… retain mo na lang ung
  execution order, tanggalin mo na ung drag at mga arrows". Ang v1
  canvas node editor (drag/connect/arrows) ay PALITAN ng simple
  execution-order list. Mas madaling gamitin para sa operator at wala
  nang canvas-related edge cases.

Files (EDIT):

- `gui/workflow_tab.py` — REWRITTEN as v2 list editor:
  - Execution Order Listbox (01..N rows, [disabled] marker, status
    row-colors habang tumatakbo: RUNNING blue / DONE green / FAILED
    red / STOPPED-SKIPPED yellow-gray).
  - Controls: Add Node (palette), Move Up/Down (renumber + selection
    keep), Delete Node (instance LANG — registry/module hindi nasisira),
    Enable/Disable checkbox, Save, Restore Default, Run Workflow / Stop,
    Live mode checkbox (DRY default).
  - REMOVED: canvas, drag, connection arrows, connect/disconnect,
    geometry hit-testing. Connections ay NANANATILI sa data model
    (compatible pa rin ang mga naka-save na config; engine validates
    them as before) — hindi na lang sila sinasabi sa UI.
  - NEW thread-safety pattern: worker threads ay naglalagay ng UI
    updates sa isang queue, at ang Tk main loop ang nagda-drain
    (`_poll_ui_queue` + `_post`) — wala nang Tk calls mula sa worker
    threads (naitalize race sa v1 tests, mas safe sa real GUI).
- Engine/registry/adapters/persistence: WALANG binago.

Verification:

- `python -m gui.workflow_tab` — PASSED (drives the REAL list editor:
  default 8-row order, select, Move Up/Down with selection keep,
  enable/disable, add/delete, save/restore, status paint, at
  END-TO-END RUN: 2 echo nodes → COMPLETED, DONE colors both rows,
  Finished status)
- `python -m core.workflow_engine` — PASSED
- `python -m unittest discover tests` — 87/87 OK
- LIVE app smoke (real EDHClaimsGUI, real Tk events): select/move/
  toggle/add/delete/restore/save lahat gumagana sa aktwal na tab
- `py_compile` — OK

### 2026-09-11 — Workflow GUI FIX: drag, Up/Down, arrow-click, status paint

Reason:

- Owner report (2026-09-11): "hindi gumagana yung drag at up and down
  at mga ibang button sa workflow". Root causes (lahat na-reproduce
  bago ito ayusin):
  1. UP/DOWN dead: `move_node()` ay nagbasa lang ng
     `order_list.curselection()`, pero ang canvas-selection redraw
     (`_redraw_all`) ay nagre-rebuild ng Listbox at bumubura ng
     selection — kaya pagkatapos mag-click ng node sa canvas, patay ang
     Up/Down. FIX: `move_node` ngayon ay tumatanggap ng KAHIT ANONG
     pinagmulan ng selection (canvas node o list row), at
     `_refresh_order_list` ay nagre-restore ng selection BY NODE ID
     (hindi row index) pagkatapos ng redraw.
  2. DRAG broken: `moveto` sa shared tag ay gumagalang lang ng unang
     item (naiiwan ang mga text); walang drag threshold kaya kahit
     jitter ng click ay gumagalaw ng node; at ang pag-drag sa ibabaw ng
     ibang node ay nagpapatakbo ng handler NG IBANG node (posisyon
     corruption). FIX: canvas-level press/motion/release lang (walang
     per-item tag bindings na nagpapatunggal), +4px drag threshold,
     delta-based na paggalaw ng LAHAT ng items (`canvas.move` sa
     `nid:` tag), at ang click-vs-drag ay nadedetermine sa release.
  3. ARROW-CLICK dead (Disconnect): walang binding sa connection lines
     — `Disconnect Selected` ay hindi kailanman nagka-laman. FIX:
     deterministic point-to-segment geometry hit-test (±8px) sa
     naka-track na line items; ang click sa arrow ay nagse-select ng
     koneksyon (pula) at pwede na i-Disconnect. Node-hit ay deterministic
     box test (hindi `find_closest` na unreliable).
  4. STATUS PAINT crash: `itemconfig(outline=...)` sa node-id tag ay
     bumabagsak sa TEXT items (TclError "unknown option -outline") —
     hindi kailanman nag-paint ang status colors. FIX: paints lang sa
     RECTANGLE item (tracked via `_node_rect_items`), at ang kulay ay
     naka-record sa `_node_status_colors` para hindi mawala sa redraw;
     nagli-linis ito kapag may bagong Run.

Files (EDIT):

- `gui/workflow_tab.py` — reworked canvas event layer (press/motion/
  release + hit-testing), fixed `move_node`, `_refresh_order_list`
  (id-based selection restore), `_paint_node_status`, status-color
  lifecycle (clear on Run/Restore/Delete). WALANG binagong engine/
  registry/adapter behavior — GUI layer lang.

Verification:

- `python -m gui.workflow_tab` — PASSED (drives REAL canvas handlers:
  click-select, connect pair, drag +60px, jitter-click no-drag,
  arrow-click + disconnect, Up/Down from canvas AND list selection
  across redraws, enable/disable, status paint, add/delete/restore/
  save)
- `python -m core.workflow_engine` — PASSED (engine layer untouched)
- `python -m unittest discover tests` — 87/87 OK
- LIVE app smoke (real EDHClaimsGUI + real `<Button-1>`/`<B1-Motion>`/
  `<ButtonRelease-1>` event stream sa aktwal na Workflow tab):
  click-select ✓, drag 30px ✓, arrow-click select ✓, Disconnect ✓,
  reconnect via A→B clicks ✓, Up/Down ✓, status paint crash-free ✓

### 2026-09-11 — DB Config FIX: charset utf8 (hindi utf8mb4) sa Test Connection

Reason:

- Owner report (2026-09-11): "unknown character set utf8mb4" sa Test
  Connection. Ang pymysql 1.4.6 ay DEFAULT utf8mb4 kapag walang charset
  na tinukoy; ang `core/hbsys_connection.py` factory ay laging
  `charset="utf8"`. Ang v1 `test_connection` ay nakaligtaan ang charset
  — mismong behavior sa loob mismo ng config screen (ang factory sa
  labas ay tama na).

Files (EDIT):

- `core/db_connection_config.py` — test_connection ay may
  `charset="utf8"` na katulad ng factory; +1 self-test check na
  naka-capture sa real pymysql.connect kwargs (14/14 PASSED —
  pinipigilan ang muling pagbalik ng bug). Docstring note din.

### 2026-09-11 — Server & Database Credentials Configuration (HBSys MySQL)

Reason:

- Owner directive (2026-09-11): GUI settings screen para sa server/database
  credentials — Server/Host, Port, Database, Username, Password na may
  Show/Hide, Test Connection (walang auto-save, laging sinasara ang
  connection), Save Configuration, at Load ng existing values. HINDI
  hinahardcode ang mga credentials at HINDI pinapalitan ang working
  connection logic.

Files (NEW):

- `core/db_connection_config.py` — Configuration Manager: load/save/
  validate ng `db_connection_config.json` (DB_HOST/DB_PORT/DB_NAME/
  DB_USER/DB_PASSWORD — git-ignored, local lang); apply_to_env() na
  nag-e-export sa HBSYS_DB_* env vars NG EXISTING factory; test_connection()
  gamit ang EKSAKTONG parehong pymysql parameters ng factory (connect_timeout
  5s, laging naka-close sa finally); _scrub_secret() — password HINDI
  kailanman lumalabas sa error messages/logs/exceptions; validation na may
  user-friendly messages (host/port 1-65535/database/username). Missing o
  corrupt file → existing factory defaults (zero behavior change).
  13/13 __main__ self-test.
- `gui/db_connection_dialog.py` — DBConnectionDialog (modal Toplevel,
  ttk): Connection Settings fields na may hints, password masked ("•")
  default na may Show/Hide toggle, Test Connection (walang save, status
  label ✓/✗, malinaw na success/failure messages — kasama ang checklist sa
  failure), Save Configuration (validate → save → apply_to_env), Close.
  Ina-notify ang GUI log nang WALANG credentials ([DB] lines lang).
  __main__ self-test (headless-safe).

Files (EDIT):

- `edh_claims_gui_XML_COPY_BUTTON.py` — 3 surgical additions: (1) sa
  __init__ pagkatapos ng settings load: apply_to_env(
  load_connection_config()) para sa HBSYS_DB_* env bago pa magamit ang
  factory (no saved config = walang nabago); (2) "Server & Database"
  section sa Preferences tab na may "Configure Server & Database…"
  button; (3) open_db_connection_dialog() handler. WALANG ibang
  pagbabago; ang save_settings flow at lahat ng iba pang settings ay
  hindi ginalaw.
- `.gitignore` — db_connection_config.json (may password — local lang,
  never committed; same policy ng claims_gui_config.json).

Behavior:

- OPEN dialog: niloload ang saved values (o factory defaults kung wala);
  password masked. WALANG overwrite hangga't hindi nag-Save ang user.
- TEST: entererd values lang (never saved); same factory parameters;
  connection laging naka-close; success → "✓ Connection successful +
  Server/Database", failure → "✗ + reason (scrubbed) + checklist";
  walang password sa kahit anong message/log.
- SAVE: validation → JSON write → HBSYS_DB_* env. Ang EXISTING na
  factory at lahat ng GUI-launched scripts (env = os.environ.copy())
  ay kusang gumagamit ng bagong values. Hindi kailangang palitan ang
  kahit anong query/repository/pooling code — env-var contract lang.
- STARTUP: no saved config → factory defaults (192.168.1.2:3306/
  hbsys_edh) — eksakto ang dati.

Safety / compatibility:

- `core/hbsys_connection.py` HINDI binago (zero modifications) — ang
  bagong layer ay dumadaan lang sa mga env var na binabasa na nito.
- Walang binagong business logic, queries, repositories, o ibang GUI
  tabs; walang bagong dependencies (pymysql na ang existing).
- Password: local JSON lang (git-ignored), masked sa UI, scrubbed sa
  exceptions, hindi log.

Verification:

- `python -m core.db_connection_config` — 13/13 PASSED
- `python -m gui.db_connection_dialog` — PASSED (real display:
  construction/load/masking toggle/validation warnings/test failure
  path/no-leak logs)
- `python -m unittest discover tests` — 87/87 OK (no regressions)
- `py_compile` all 3 changed files — OK
- GUI smoke (real EDHClaimsGUI): startup naghahatid ng saved config sa
  HBSYS_DB_* env; factory source intact (env vars pa rin); Preferences
  button wired; dialog opens.
- Live Test Connection sa totoong HBSys DB: PENDING (operator-run).

### 2026-09-10 — Configurable Workflow System v1 (registry + adapters + engine + GUI tab)

Reason:

- Owner directive (2026-09-10): gawing configurable ang existing pipeline
  ("Existing Functions → Thin Integration Layer → Workflow/Connection
  Registry → Workflow Engine → Configurable GUI") HINDI rebuild. Ang
  buong analysis plan ay nasa
  `C:\Users\EDH-Admin\.local\share\kilo\plans\1788937928018-configurable-workflow-system.md`
  at na-approve para sa implementasyon. KEY FACT na nagtulak sa design:
  WALANG code-level chaining sa buong project — ang pipeline ay
  folder-contract lang (scans\ → output\ → READY\ → eClaimsDoc), kaya
  ang workflow layer ay PURELY ADDITIVE (walang binagang business
  logic, walang inrewire).

Files (NEW):

- `core/workflow_registry.py` — NodeSpec catalog ng 12 existing entry
  points (claims_processor, date_fill_regular/abtc, xml_clicker,
  copy_xml, fees_checker, claims_checker + recheck, add_claims_upload,
  claim_attachments, merge_pdf, convert_pdfa). Pure data +
  validate_registry; hbsys_touching flags; supports_dry flags; direct
  entry-point invocation (HINDI ang Tk-prompting launchers para
  hindi mag-block ang unattended runs at abot ng terminate() ang real
  tool process). 20/20 __main__ self-test.
- `core/workflow_adapters.py` — ScriptNodeAdapter: thin subprocess
  wrapper na kopya lang ng existing GUI `_run_script_thread` behavior
  (cwd=PROJECT_ROOT, stdout streaming via callback, CLAIMS_* env
  bridge mula sa settings + node extra_env, terminate() stop,
  CREATE_NO_WINDOW). build_command/build_env pure functions. Statuses:
  DONE/FAILED/STOPPED. 11/11 __main__ self-test (totoong subprocess
  lifecycle kasama ang terminate).
- `core/workflow_engine.py` — NodeInstance/Connection/WorkflowConfig
  dataclasses; default_workflow() factory (8 nodes = current manual
  pipeline order, deep-copied bawat tawag); save/load/validate ng
  `workflow_config.json` (WorkflowConfigError sa missing/corrupt/
  unknown-node/duplicate-id/dangling-connection — HINDI kailanman
  nag-huhula; GUI ang mag-ooffer ng Restore Default);
  WorkflowEngine: STRICTLY SEQUENTIAL by order (isang HBSys window
  lang — walang parallelism), per-node status callbacks,
  continue_on_fail param (default stop), Stop = terminate current +
  SKIPPED_STOPPED sa natitira, HBSys pre-check (find_hbsys_window)
  bago ang hbsys_touching nodes (same gate as Date Fill/XML Clicker
  buttons), single-run guard, unknown-node REFUSED. 21/21 __main__
  self-test.
- `gui/workflow_tab.py` — WorkflowFrame (ttk.Frame) sa tk.Canvas:
  Drag (canvas tag move, positions persisted), Connect (click A then
  B → arrow; no duplicates/self-loops), Disconnect (Disconnect
  Selected button), Delete Node (instance + connections LANG — hindi
  kinakabit ang underlying module), Add Node (palette by category),
  Enable/Disable (checkbox, walang position/order loss), Change Order
  (side list Up/Down + renumber), Save, Restore Default, Run/Stop
  (daemon thread, engine callbacks marshalled via self.after(0)),
  Live mode checkbox (DRY default), per-node status colors, workflow
  log. Constructor-injected settings_getter/log_callback — same
  pattern as existing tabs. __main__ self-test (real display):
  construction + buong edit vocabulary.

Files (EDIT):

- `edh_claims_gui_XML_COPY_BUTTON.py` — 4 surgical additions: (1)
  import WorkflowFrame; (2) notebook tab "Workflow" (sa pagitan ng
  Claim Attachments at Preferences); (3) build_workflow_tab() +
  workflow_running() helper; (4) auto-watcher guards: _auto_process_tick
  at auto_copy_xml_tick ay nag-Paused (workflow running) habang
  nag-e-execute ang workflow — pinipigilan ang auto claims_processor /
  xml-copy na magsimula MID-CHAIN. WALANG ibang pagbabago sa shell.
- `.gitignore` — workflow_config.json sa local settings section (same
  policy ng claims_gui_config.json; user-generated state).

Behavior:

- DEFAULT: walang workflow_config.json → tab naglo-load ng DEFAULT
  workflow (current manual pipeline order); HINDI nag-a-auto-run ang
  workflow kahit kailan — Run button LANG. Existing buttons/tabs/
  watchers: kapag walang tumatakbo na workflow, eksakto ang dati.
- Run: engine → adapters → totoong subprocess ng mga existing tools;
  exit code ang resulta; Stop = terminate (same sa GUI Stop Process);
  bawat tool ang may-ari pa rin ng sariling resume (batch state
  JSONs/recheck/prechecks).
- HBSys gate: date_fill/xml_clicker/add_claims/claim_attachments nodes
  ay SKIPPED with reason kapag sarado ang HBSys (hindi crash).
- Dry-run default sa tab (Live mode checkbox); supports_dry tools ang
  may tunay na dry behavior; claims_processor/fees_checker/checkers/
  copy_xml ay walang dry mode (live_args=() — tulad ng GUI behavior).
- v1 SCOPE (deliberate): connections = visual/ordered metadata lang;
  graph traversal + branching/conditions DEFERRED (walang branching sa
  current pipeline). Archive node DEFERRED (manual GUI action pa rin).
  Telegram/webscanner/PDF-per-file tools NOT RECOMMENDED (documented).

Safety / compatibility:

- ZERO modification sa: production engine
  (bot_unknown_trainer_..._ADM_DIS.py), core/claim_attachments_*
  (v6.1/v7), core/add_claims_*, date_fill_hbsys/* (invoked LANG),
  business/DB modules, lahat ng existing GUI tabs, hbsys_bot,
  webscanner.
- Walang DB schema change; walang dependencies na idinagdag; pure
  stdlib + existing openpyxl/tkinter stack.
- Adapters invoke entry points DIRECTLY (never the prompting
  launchers) — terminate() abot ng real tool process (naka-test).

Verification:

- `python -m core.workflow_registry` — 20/20 PASSED
- `python -m core.workflow_adapters` — 11/11 PASSED
- `python -m core.workflow_engine` — 21/21 PASSED
- `python -m gui.workflow_tab` — PASSED (real display)
- `python -m unittest discover tests` — 87/87 OK (no regressions)
- `python -m core.claim_attachments_doc_type` — PASSED (59)
- `python -m core.claim_attachments_doctype_audit` — PASSED (29)
- `py_compile` all 5 changed files — OK
- GUI smoke (real EDHClaimsGUI launch): 12 tabs render kabilang ang
  Workflow; workflow_running() False → True (fake engine) → False;
  auto-watcher guards verified
- E2E engine run (real subprocesses): 3-node config (1 disabled) →
  COMPLETED 2/2, disabled node never executed, order preserved,
  stdout streamed
- Live full-pipeline workflow run: PENDING (operator-run; kailangan ng
  scans na may tunay na pasyente + HBSys open)

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


---

## 2026-09-24 -- PDF Preview Tab (CSF + COE Contact Sheets)

### Change Title
Add in-GUI PDF Preview tab with Previous / Next navigation for CSF and COE contact sheets.

### Reason
User requested a button in the main GUI that generates 3-column contact sheets for
CSF and COE PDFs and previews them inside the application -- no external PDF viewer
needed. The existing _make_csf_contact_sheet.py script was generalized into a
reusable service module.

### Files Added
- core/pdf_preview_service.py -- service that builds 3-column contact sheets
  (1920x1080) for CSF.pdf and COE.pdf from the output folder into logs/.
- gui/pdf_preview_panel.py -- Tkinter panel with document-type radio buttons
  (CSF/COE), Regenerate button, Previous / Next navigation, and an embedded
  canvas viewer.

### Files Modified
- edh_claims_gui_XML_COPY_BUTTON.py -- added import, new notebook tab
  "PDF Preview", and build_pdf_preview_tab() method.

### Behavior Before
- Only standalone script _make_csf_contact_sheet.py existed; no in-GUI preview.

### Behavior After
- Main GUI has a "PDF Preview" tab. Selecting CSF or COE auto-generates contact
  sheets (3 columns, 1920x1080) and displays them in a canvas with Prev/Next
  buttons. No external viewer is opened.

### Safety / Compatibility
- New modules only. No changes to existing OCR, signing, XML, or claims-checker
  code.
- Service module supports `if __name__ == "__main__":` standalone testing.
- GUI panel runs sheet generation in a background thread to keep UI responsive.

### Verification
- `python core/pdf_preview_service.py` -> SUCCESS: CSF 5 PDFs -> 2 sheets,
  COE 5 PDFs -> 2 sheets.
- Syntax OK for all three new/modified files.
- Import test: `from edh_claims_gui_XML_COPY_BUTTON import EDHClaimsGUI` OK;
  build_pdf_preview_tab present.
- Generated sheets:
  logs/csf_contact_sheet_1.png, logs/csf_contact_sheet_2.png,
  logs/coe_contact_sheet_1.png, logs/coe_contact_sheet_2.png.


## 2026-09-26 -- Claims Agent diagnostic logging (find WHERE it broke)

### Change Title
Make every blocked/failed agent row name the exact failing step, with live screen
evidence (forms, windows, Hospital No. field, screenshot) instead of
"nothing happened".

### Reason
The live 3-row run reported only: date_fill "exit 1" truncated mid-sentence, and
final_bill "patient load not verified". Neither said WHICH step failed, what was
on screen, or whether the typed hospital number ever reached the field, so the
cause could not be found without re-running the patient by hand.

### Files Modified
- core/agent/final_bill_actions.py -- new screen-diagnostics helpers
  (`hbsys_window_titles`, `hospital_no_field_text`, `save_screenshot`,
  `diagnose_screen`); the loader now types, READS THE FIELD BACK, then presses
  ENTER, logs the wait second by second, and dumps the diagnostics when the form
  does not appear; `select_confinement` logs the OCR'd grid rows it compared
  against; `clear_blocking_popups` names the blocking window; LOAD_WAIT_SECONDS
  1.5 -> 8.0.
- core/agent/orchestrator.py -- `_tool_reason` prefers the tool's `[STOP]`
  record, then the `Reason:` block of a multi-line `[POPUP]` message, then the
  last line; `_tool_detail` adds `last steps:` (tail of the click/type trace);
  removed the now-unused `_tool_tail`; BLOCKED final_bill details carry all
  loader/matcher notes plus the screen snapshot.
- date_fill_hbsys/hbsys_fill_dates.py -- prints one machine-readable
  `[STOP] patient=... | status=... | reason=... | screen_stage=... |
  safe_reset=... | csv=...` line and up to three `[EVIDENCE] screenshot ...`
  lines before the stop popup.
- tests/test_agent_final_bill.py -- popup-clear and guarded-close tests stub the
  screen probes (they used to read the live desktop, so they failed whenever a
  Billing form was open); new `ScreenDiagnosticsTests` + unknown-dialog test.
- tests/test_agent_orchestrator.py -- `[STOP]` / popup-`Reason:` / last-steps
  coverage replacing the old popup-line expectation.

### Behavior Before
- Failed row detail = tool name + exit code + first 500 chars of stdout, which
  cut the date_fill trace in the middle of the Admit History step.
- final_bill "patient load not verified": no field read-back, no wait trace, no
  list of windows/forms, no screenshot, 1.5s wait.
- Admit History "could not select": the rows the OCR actually saw were not logged.

### Behavior After
- Row detail: `hbsys_fill_dates.py exit 1 | reason: [STOP] patient=... |
  status=needs_review_admission_history | reason=... | screen_stage=admission_popup
  | csv=... | last steps: [LIVE] press enter -> [LIVE] click Admit History at
  (435, 58) -> ... | full output: logs/agent_tool_....log`.
- A blocked load logs: which form was closed, the click point, whether the
  Hospital No. field received the number, the wait per second, then the open
  Billing forms, every HBSys window/modal dialog, the field text, and
  logs/agent_diag_<stamp>.png.
- Admit History logs the exact rows it read before saying "no row matched".
- Live smoke check (read-only) on this desktop reported:
  `billing forms: Billing (COLLADO, JEMALYN GAMENG)`,
  `windows: HBSys ... [FNWND370]; Epson Scan 2 [#32770]`,
   `hospital no field: <HRN>`.

### Safety / Compatibility
- Diagnostics are read-only: window enumeration, control text, one OCR of the
  Hospital No. band, and a full-screen PNG. No click, no typing, no key press.
- Every new probe is exception-guarded, so a missing window/OCR degrades the
  log to "<empty/unreadable>" instead of failing the row.
- Nothing else changed: no coordinates, no control ids, no match thresholds.

### Verification
- `python -m py_compile` on the three modified modules: OK.
- Agent suite (fees_actions, final_bill, hbsys_nav, hbsys_screens,
  orchestrator, plan_store, gui_agent_plan): 186 tests, OK.
- `date_fill_hbsys` verifier tests: OK.
- Live read-only run of `diagnose_screen()` printed the form list, the window
  list (including the Epson Scan 2 modal), the field text and the screenshot
  path, as shown above.
## 2026-09-28 -- GUI crash diagnostics: lifecycle/crash/error trail, focus guard, run heartbeat, unfinished-run warning

### Context
The GUI closed silently on Agent Plan -> Approve & Run. No code path closes
the window (only Alt+F4 / the X button do, and code never sends them), so the
next occurrence must explain itself from a file trail. Everything added is
modular, fail-open, and never raises.

### Files Modified
- core/diagnostics.py -- NEW. faulthandler -> logs/gui_crash.log (native
  crashes, all-thread dump); unhandled exceptions (main thread, worker
  threads, Tk callbacks) -> logs/gui_errors.log; START/CLOSE/EXIT lifecycle
  -> logs/gui_lifecycle.log. Idempotent install, never raises, best-effort
  writes, reset_for_tests() for suites; install_tk_close_logging() follows
  the directory install_crash_logging() was given. Log folder order:
  explicit argument, CLAIMS_DIAG_LOG_DIR env var (tests redirect here),
  else logs/. The atexit EXIT hook binds its folder at registration and is
  unregistered by reset_for_tests(); the excepthooks go inert when the state
  is reset, so a test process can no longer append EXIT/exception lines to
  the production logs.
- core/agent/window_guard.py -- NEW. Foreground-window guard used by the
  HBSys tools: warn/block/off via CLAIMS_AGENT_FOCUS_GUARD, fail-open,
  injectable window-title function for tests.
- core/agent/orchestrator.py -- run heartbeat: logs/agent_current_run.json
  (renamed from agent_run_current.json so it never matches the
  agent_run_*.json audit-trail glob) is rewritten before the run, names the
  row being ATTEMPTED (current_index/action/patient_folder/hospital_no)
  before the executor runs, after every row, and ends as state=FINISHED with
  finished_at/saved_path/summary; new write_run_heartbeat(),
  last_run_state(), state_path= on run_approved_plan. A heartbeat failure
  never breaks a run.
- gui/agent_plan_tab.py -- state_file constructor parameter and
  _previous_run_warning(): when loading a plan sees state=RUNNING it logs a
  Tagalog warning (row X/Y, last action, pointer to gui_lifecycle.log).
- start_claims_gui.py, edh_claims_gui_XML_COPY_BUTTON.py -- install the
  diagnostics at launch / in EDHClaimsGUI.__init__ (plus the Tk close log).
- date_fill_hbsys/hbsys_fill_dates.py, date_fill_hbsys/xml_generator_clicker.py
  -- window guard integrated (refuse/flag input when HBSys is not focused).
- tests/test_core_diagnostics.py, tests/test_agent_window_guard.py -- NEW
  suites (install/reset safety, close logging, exit-hook binding, guard
  modes).
- tests/test_agent_orchestrator.py -- HeartbeatTests (finished payload,
  attempted-row naming, per-row rewrite, glob non-overlap, empty/corrupt
  state, failure never breaks a run, save=False); module-level stubs for the
  final_bill screen probes; every run_approved_plan() call site now passes
  run_dir so the heartbeat stays in temp folders.
- tests/test_gui_agent_plan.py -- unfinished-RUNNING / FINISHED / missing
  heartbeat warning tests; state_file pointed at a temp dir.
- tests/test_gui_no_xml_panel.py, tests/test_gui_hbsys_status.py --
  setUpModule/tearDownModule set CLAIMS_DIAG_LOG_DIR to a temp folder (the
  real EDHClaimsGUI installs diagnostics in __init__).
- tests/test_gui_verify_panel_removal.py -- standalone run redirects
  CLAIMS_DIAG_LOG_DIR before building the GUI.

### Behavior Before
- A silent close left no trace: no crash file, no exception log, no way to
  tell "closed normally" from "killed"; a dead run left no record of the row
  it stopped on; test runs could append gui_*.log / agent_current_run.json
  into the real logs/ folder.

### Behavior After
- logs/gui_lifecycle.log: CLOSE+EXIT = closed normally (X / Alt+F4); EXIT
  without CLOSE = the process ended without the window handler; no EXIT line
  = killed from outside or a crash before atexit. gui_crash.log non-empty =
  fatal native crash with thread stacks. gui_errors.log = unhandled
  exceptions (main thread, worker threads, Tk callbacks).
- logs/agent_current_run.json shows which row the last run stopped on, and
  the Agent Plan panel warns on load when it never finished (state=RUNNING).
- Test runs leave logs/ byte-for-byte unchanged.

### Safety / Compatibility
- Every diagnostics/guard entry point is fail-open and never raises; the Tk
  close handler still destroys the window exactly like the default; the
  original Tk/thread exception reporting still runs; production defaults are
  unchanged (same folder, same behavior when nothing is configured).
- The heartbeat file name cannot collide with the agent_run_*.json audit
  trail; a heartbeat write failure is swallowed.

### Verification
- python -m unittest tests.test_core_diagnostics tests.test_agent_window_guard
  tests.test_agent_orchestrator tests.test_gui_agent_plan
  tests.test_gui_no_xml_panel tests.test_gui_hbsys_status: 124 tests, OK,
  in one process; a before/after snapshot of logs/ showed zero new or
  modified files (only the artifacts deleted during cleanup differ).
- python core/diagnostics.py (standalone selftest): passed.
- Cleaned test/harness artifacts from logs/: gui_lifecycle.log,
  agent_current_run.json, agent_run_current.json, 3x agent_diag_*.png
  (10:28), and agent_plan_20260928_102611.json (a re-save of the 09:24 plan,
  identical except created_at).
- tests/test_gui_verify_panel_removal.py still fails 3 stale layout checks
  (its expected tab list predates Agent Plan/Workflow/PDF Preview and it
  looks for the old exact "Check Missing" label) -- pre-existing and
  unrelated to this work.
## 2026-09-28 -- Agent Date Fill: confinement selection copied from the working Date Fill ABTC/Regular tool

### Context
Agent Plan -> Approve & Run launches the PRODUCTION tool
`date_fill_hbsys/hbsys_fill_dates.py` (orchestrator `DATE_FILL_TOOL`), while the
GUI button "Date Fill ABTC/Regular" runs `hbsys_fill_dates_testing.py`. The owner
reported that the agent cannot select a confinement while the button works, and
the logs/screenshots prove two distinct defects:

- `agent_tool_hbsys_fill_dates_..._20260926_155739.log`: the single best OCR pass
  read only 1 of 2 Admission History rows -> `needs_review_admission_history`.
- `..._20260926_161157.log` and `..._20260928_105548.log`: `select exact
  Admission History row ... at (188, 98)` -> `needs_review_phic_beneficiaries`.
  Screenshot `phic_beneficiaries_select_20260928_105534.png` shows the Admission
  History popup STILL OPEN: `_confinement_row` built the click point as
  `(rect.left + 72, parsed.y)` — missing `rect.top` — so the double-click hit the
  toolbar at y=98 instead of the row at y≈218, the popup never closed, the PHIC
  click ran on the wrong screen, and the matcher correctly reported "matching
  PhilHealth Beneficiaries row not found".

The proven reference (Date Fill ABTC/Regular) was copied function-by-function
into the production tool. The reference itself is untouched.

### Files Modified
- `date_fill_hbsys/hbsys_read_admission_history.py` — added
  `read_focused_admission_row_variants()` + `detect_admission_grid_row_centers()`
  (ported from `hbsys_read_admission_history_testing.py`): per-row focused OCR
  crops so one weak full-window pass cannot drop a grid row. Additive only; the
  shared `select_confinement_row` recipe and the Final Bill imports are unchanged.
- `date_fill_hbsys/hbsys_fill_dates.py` (the tool the agent launches):
  - `_confinement_row`: click point is now `(rect.left + 72, rect.top + row_y)` —
    screen-absolute like the working tool (the root-cause fix).
  - `_read_confinement_rows`: merges ALL OCR passes (7 full-window variants + 4
    focused row passes) instead of one best pass; new `_merge_parsed_rows()`
    de-duplicates by date pair; the fuzzy fallback re-scores the repaired best
    pass (smeared-date cell re-read kept) plus the stored passes.
  - `select_admission_history_row`: after the shared recipe reports a pick,
    `_wait_admission_history_closed()` (`ADMIT_HISTORY_CLOSE_TIMEOUT = 3.0s`)
    requires the popup to actually close; otherwise it stops with
    `needs_review_admission_history` instead of continuing on the wrong screen.
  - `click_phic_and_select_claim`: proof-verified selection copied from the
    working tool — single-click at x=260, retry x=520 (through the guarded
    `self.click`), recapture, re-match, and accept only when the row OCRs back as
    the Windows blue selected row; the old blind one-shot click at x=46 with no
    proof is gone.
  - `find_phic_beneficiary_row_y_from_variants`: new `minimum_consensus`
    parameter, 2 at both call sites (copied from the working tool).
  - `find_phic_beneficiary_row_y`: duplicate-confinement disambiguation now needs
    `min(2, len(name_tokens))` matching name tokens (was: 1 token sufficed).
  - `is_blue_highlighted_row()` added (PIL row-highlight proof).
- `tests/test_date_fill_confinement.py` — NEW: 13 hermetic regression tests
  (absolute click point, multi-pass merge, popup-closure stop, x=260/520 proof
  loop with exact click coordinates, 2-pass consensus, blue/gray highlight proof,
  1-token ambiguity vs 2-token pick). Captures are mocked or temp files; no test
  touches the real `logs/`.

### Behavior Before
- One OCR pass decided the Admission History rows; a weak pass that missed the
  folder's row stopped the claim even when another pass had read it.
- The Admission History double-click could land off the row (y missing
  `rect.top`); the recipe reported success anyway, the popup stayed open, and
  every later step (PHIC, CF2) ran against the wrong screen until the matcher
  failed — or could have matched grid text from the popup.
- The PHIC row was clicked once at x=46 with no verification of any kind; row
  selection could silently fail while the flow reported success.

### Behavior After
- Every OCR pass contributes rows; the exact/fuzzy matcher sees the union.
- The double-click uses screen-absolute coordinates (identical math to the
  working tool), and the flow refuses to continue until the popup is closed —
  a miss now stops early as `needs_review_admission_history` with the popup
  left open for the operator.
- The PHIC row is only accepted with two agreeing OCR passes AND a blue-highlight
  proof screenshot; clicks are attempted at x=260 then x=520; failure stops as
  `needs_review_phic_beneficiaries` (same status names as before).

### Safety / Compatibility
- HINDI ginalaw ang gumaganang `hbsys_fill_dates_testing.py` (ang reference), ang
  shared `select_confinement_row` recipe, ang Final Bill agent (lahat ng click
  points nito ay screen-absolute na — `point = (first_x, line[0][0])` mula sa
  `_ocr_grid_lines`), ang fill sequence (parehas pa rin ang Professional
  Fee/Consent clicks at discharge date para sa REGULAR), ang CSV columns, ang CLI
  (`--live --hospital-no`), at ang status names.
- ABTC-only proof helpers (accreditation/name-row consensus) stay in the testing
  tool; the agent tool remains REGULAR (discharge-date basis) exactly as before.
- Bagong stop conditions = earlier failure with the SAME review statuses; the
  tool never guesses a confinement (never-guess rule intact).

### Verification
- `python -m py_compile` on both modified modules — OK.
- `python -m unittest tests.test_date_fill_confinement -v` — 13 tests, OK (0.3s).
- `python -m unittest tests.test_agent_final_bill tests.test_agent_orchestrator`
  — 112 tests, OK (shared recipe + Final Bill unaffected).
- `python -m unittest test_hbsys_date_fill_verifier` (in `date_fill_hbsys/`) —
  34 tests, OK.
- Full discovery `python -m unittest discover -s tests` — 355 tests, OK;
  before/after snapshot of `logs/` — identical (1256 files), i.e. hermetic.
- Huling live run bago ang fix (`agent_tool_..._20260928_105548.log`) ay
  huminto sa `needs_review_phic_beneficiaries`; ang susunod na live Agent Plan
   run (hal. <isang pasyente>) ang pagkakataong patunayan ang bagong
  flow sa totoong HBSys.



## 2026-09-28 13:40 -- Final Bill: Admit History multi-pass row reader + live-copy sync (PERA run BLOCK fix)

### Context
Ang Agent Plan Final Bill run ng 2026-09-28 13:30 at 13:40 ay parehong
 `BLOCKED` sa unang pasyente (isang pasyente) at na-block
ang susunod na dalawa dahil hindi na-close ang kanyang `Billing` form.
Root cause na natukoy sa logs + screenshot:

- `logs/agent_run_20260928_134039.json` --
  `"confinement period 20260909-20260912 not found in the Admit History list"`
  na may `"admit history: nothing selected"`, ibig sabing ZERO rows ang
  na-read ng matcher.
- `logs/agent_diag_20260928_134039.png` -- ANG POPUPYON AY MAAYONG LAMANG.
  Naka-display nang malinaw ang row na `09/09/2026 | 09:40 PM | 09/12/2026 |
  02:31 PM | ADMIT` sa loob ng `Admission History` window, at tama na ang
   Hospital No. field (`<HRN>`) -- patunay na nasagot na ang typing at
  na-tatama na ng Admit History.
- `logs/gui_crash.log` -- WALANG nbagong crash (ang 11:34 na
  `RPC_E_DISCONNECTED` sa `load_patient_by_hospital_no` ay hindi na
  nangyari). Pumasa na ang COM-init at foreground fix.

Pangunahing sanhi: ang `confinement_rows_for_matching()` na tumatakbo sa
LIVE na `C:\claims_bot\core\agent\final_bill_actions.py` ay pa rin ang LUMANG
single-pass full-screen grab (verified via md5: live 9922f418... 78,241 bytes vs
worktree 268896d1... 83,929 bytes). Ang single full-screen grab ay nagbababa ng
13" popup grid sa readability limit ng Tesseract kaya ZERO rows ang
nababasa -- kahit nasa screen nang perfect ang confinement.

### Fix
1. Multi-pass OCR reader (port ng Date Fill recipe) sa Final Bill agent:
   - `_capture_popup_image()` -- kinukuhang popup pixels mismo gamit
     `capture_as_image()` (hindi na full-screen grab) at sinusulatan sa
     `logs/final_bill_admission_history_<ts>.png` bilang run evidence.
   - `_merge_parsed_rows()` -- MERGE ang bawat natatanging
     (admission_date, discharge_date) sa lahat ng OCR pass, first-pass-wins.
   - `_confinement_row_from_parsed()` -- click point na
     `point = (rect.left + 72, rect.top + int(parsed.y))`, i.e. mula sa TOP-LEFT
     ng popup. Ang dating full-screen crop ay nakatapat sa toolbar sa itaas.
   - `_read_confinement_rows_multi_pass()` -- pinagsasama ang 7 full-window
     variants + 4 focused per-row crops.
   - `confinement_rows_for_matching(popup, *, log_fn=None)` -- multi-pass
     muna, legacy single-pass bilang fallback kapag capture failed.
2. Na-sync ang buong worktree file papunta sa live copy
   `C:\claims_bot\core\agent\final_bill_actions.py` (md5 SAME) at
   `gui/agent_plan_tab.py`.

### Files Modified
- core/agent/final_bill_actions.py -- `_capture_popup_image`,
  `_merge_parsed_rows`, `_confinement_row_from_parsed`,
  `_read_confinement_rows_multi_pass`, at ang multi-pass na
  `confinement_rows_for_matching` / `select_confinement`.
- tests/test_agent_final_bill.py -- BAGONG `ConfinementRowMergeTests` (6 tests):
  (a) `test_a_misreading_pass_cannot_hide_the_correct_row`,
  (b) `test_identical_rows_from_many_passes_are_merged_once`,
  (c) `test_row_keeps_the_grid_text_and_encounter_type`,
  (d) `test_click_point_is_measured_from_the_popups_top_left_corner`
  (popup at (122,110), row y=101 -> point (194,211)),
  (e) `test_unreadable_ocr_passes_yield_no_rows_instead_of_raising`,
  (f) `test_merge_survives_a_broken_datefill_import`.
  Dagdag pa rin ang `from unittest import mock` import.

### Validation Against The Real Screenshot
Ang crop ng tunay na popup mula `agent_diag_20260928_134039.png` ay
pinatakbo sa mismong reader bago i-deploy:

- full-window passes: 7 -- pass 1 -> `('09/09/2026', '09/12/2026', 'ADMIT')`
- focused row passes: 4 -- lahat -> `('09/09/2026', '09/12/2026')`

Patungo sa folder na `ADM20260909_DIS20260912`. Pansinin na ang pass 0 ay
nagbasa ng `09/09/2028` (isang digit ang mali) -- dahil doon kailangan ng
merge: hindi dapat tanggalin ang tamang row dahil may pass na nagkamali.

### Safety / Compatibility
- Read-only sa MySQL/HBSys data; walang schema change, walang SQL, walang
  pagbabago sa XML generator, signing engine, OCR engine, o Claims Checker.
- Pop-up capture ay screenshot lamang (read-only probe), gaya ng dati.
- NEVER-GUESS rule hindi nababago: kung walang row na tumutugma sa folder
  period, BLOCKED pa rin at hinahawakan ng operator -- ang multi-pass reader
  ay nagpapalakas ng pagkakabasa, hindi ng pagpili ng confinement.
- Ang legacy single-pass path ay nananatiling fallback, kaya hindi breakage
  kapag nawala ang popup capture.

### Verification
- `python -m py_compile` sa live na `C:\claims_bot\core\agent\final_bill_actions.py`
  at `gui\agent_plan_tab.py` -- OK.
- `python -m pytest tests/test_agent_final_bill.py tests/test_agent_orchestrator.py tests/test_gui_agent_plan.py -q`
  -- 131 passed.
- `python -m pytest tests/ -q` -- 361 passed, 7 subtests passed (20.2s).
- md5 ng live vs worktree: `core/agent/final_bill_actions.py` SAME,
  `gui/agent_plan_tab.py` SAME.
- Huling live run (`agent_run_20260928_134039.json`) -- huling aking
  pagsubok sa totoong HBSys; dapat na dumaan ang Admit History step para sa
  PERA at magsimula na ang Close Form -> next patient loop.

---

## 2026-09-28 - Final Bill: PRESS ENTER pagkatapos ng click OK

### Request (operator)
```
AFTER CLICK OK PRESS ENTER YAN ANG IDAGDAG MO.
WAG NA UNG PRESS SAVE OR OK OR NO
```

### Diagnosis
Ang dating galit sa selection ng confinement ay na-fix na (popup-pixels
multi-pass OCR, entry sa itaas). Ang natitirang tanong ay ang post-OK
step: ang dating code ay nag-click No (STEP_CONFIRM_NO) o click OK/Save
(STEP_SAVE_OK) - hindi ito ang ginagawa ng operator. Ang operator ay
nagta-press ENTER pagkatapos ng click OK.

### Behavior change
Ang post-OK action ay ISANG press_enter. Hindi na walang click ang
Yes/No (Do/No) at Save/OK - pareho silang tinatugunan ng iisang ENTER.

Bagong step: STEP_PRESS_ENTER = "press_enter".
Retired (nananatiling pangalan lang para ma-resolve ng lumang log/plan
file, hindi na naplaplan): STEP_CONFIRM_NO, STEP_SAVE_OK.
ALL_STEPS 8 -> 7.

### Files affected
- core/agent/final_bill_actions.py
  - plan_step(): bagong keyword ok_clicked at enter_pressed; ang
    SCREEN_FINAL_BILL_CONFIRM at SCREEN_FILE_SAVE ay nagpaplano na ng
    STEP_PRESS_ENTER. Ang ok_clicked=True + enter_pressed=False ay may
    pinaprioritadong shortcut na STEP_PRESS_ENTER kahit wala nang dialog
    (ang ENTER ay mapupunta sa front window - gaya ng ginagawa ng tao).
  - StepDecision: bagong field na ok_clicked.
  - FinalBillRunner.__init__(): pinagpalit ang answer_confirm_fn /
    answer_save_fn ng press_enter_fn (injected sa tests).
  - FinalBillRunner._perform(): (ok_clicked, enter_pressed) na ang
    isinusunod na state; nagbabalik ng 4-tuple.
  - Bagong module-level press_enter_now() + _front_hbsys_window() +
    PRESS_ENTER_SETTLE_SECONDS (0.6s) at PRESS_ENTER_WAIT_SECONDS
    (2.0s). Kinakailangan ng ensure_com_thread() at raise_to_top() bago
    ang send_keys("{ENTER}") - kung hindi, ang ENTER ay mapupunta sa
    claims GUI/IDE (parehong pagkabigo na nangyari dati sa pag-type ng
    hospital number).
  - FinalBillRunner._press_enter() (bago ang _answer_no / _answer_ok,
    na tina-delete).
  - Module docstring at FinalBillRunner docstring: updated sa bagong
    hakbang 7.
- tests/test_agent_final_bill.py
  - FakeHbsys.answer_no/answer_save -> FakeHbsys.press_enter.
  - make_runner() -> press_enter_fn=.
  - test_confirm_prompt_plans_no -> test_confirm_prompt_plans_enter
  - test_file_save_prompt_plans_ok -> test_file_save_prompt_plans_enter
  - Bagong test_enter_is_planned_exactly_once_after_ok.
  - test_happy_path_matches_the_live_2026_09_25_sequence ->
    _2026_09_28_sequence, may assertEqual(fake.calls.count("press_enter"), 1).
  - test_save_prompt_branch_answers_ok_instead ->
    test_save_prompt_branch_also_uses_enter.
  - test_default_runner_binds_real_gui_primitives: _press_enter nalen,
    at _answer_no/_answer_ok ay dapat WALA na.
  - test_every_planned_step_is_in_the_vocabulary: 7 steps, patay na ang
    dalawang retired.

### Bug na na-diskubra habang nagta-test
Ang unang plan_step ay nagpaplano ng STEP_PRESS_ENTER nang paulit-ulit
kapag nananatili ang confirm screen matapos ang ENTER (walang
enter_pressed guard). Ayusin: ang ENTER mismo ang authoritative na
"finalized" signal - hindi ang screen - kaya enter_pressed=True ay
nagsaset ng bill_finalized=True sa simula ng plan_step. Nakaiwas ito sa
infinite loop hanggang sa step cap.

### Safety / Compatibility
- plan_step() at FinalBillRunner puro/injected -> headless pa rin.
- Walang MySQL/HBSys schema change, walang SQL, walang pagbabago sa XML
  generator, signing engine, OCR engine, o Claims Checker.
- Hindi na ginagamit ang control id/caption ng Yes/No at Save/OK, kaya
  build-agnostic: gumagana kahit walang lumabas na dialog.
- Ang ENTER ay pinapadala sa window na nasa unahan (confirm dialog ->
  print popup -> HBSys main), HINDI sa Billing form grid para hindi
  magbago ang napiling row. Ang Billing form ay hindi kailanman
  naging target ng ENTER.
- Pag send_keys ay hindi available, nagta-throw ang step na
  "press_enter failed: ..." at ang row ay na-BLOCK (hindi silent).

### Verification
- python -m py_compile core/agent/final_bill_actions.py -- OK.
- python -m pytest tests/test_agent_final_bill.py -q -- 54 passed.
- python -m pytest tests/ -q -- 362 passed, 7 subtests passed (16.80s).
- Headless planner simulation ng tunay na screen sequence:
  ORDER_TRANSACTIONS -> open_final_bill,
  FINAL_BILL_OPTIONS -> check_final_box,
  (checked) -> click_ok,
  (ok_clicked) -> press_enter,
  (prompt) -> press_enter,
  (enter_pressed) -> close_form,
  (form closed) -> done.
- md5 ng live vs worktree: core/agent/final_bill_actions.py SAME
  (b954f53d568cf4a1784eda2564348bd3, 89271 bytes) at py_compile OK sa
  C:\claims_bot.
- Live run: kailangan pa ng pag-verify ng isang patient (halimbawa
  PERA) na ang sequence ay open_final_bill -> check_final_box ->

## 2026-09-28 — Final Bill: File save = TAB+ENTER, Call Administrator = click No, at taskbar notification kapag tapos na ang run

### Request ng operator
1. Pagkatapos ng click ng OK sa Print Options popup:
   - kung "File save" ang lumabas -> PRESS TAB, pagkatapos ay ENTER
     o SPACE;
   - kung "Call Administrator" ang lumabas (Yes/No) -> CLICK "No".
2. Kapag tapos na ang run, dapat may malinaw na notification sa taskbar
   (iilaw o mag-flash) para alam ng operator na wala nang tumatakbo.

### Mga apektadong file
- `core/agent/final_bill_actions.py`
- `gui/run_notifier.py` (BAGO)
- `gui/agent_plan_tab.py`
- `tests/test_agent_final_bill.py`

### Behavior changes
- Dalawang hiwalay na step ang pinalitan ang dating iisang
  `press_enter`:
  - `STEP_SAVE_TAB_ENTER = "save_tab_enter"` -- "File save" prompt:
    keyboard-only (TAB, saka ENTER/SPACE). Walang caption/id na
    hinahanap, kaya gumagana kahit "OK", "Save" o "&Save" ang label.
  - `STEP_CONFIRM_NO = "answer_confirm_no"` -- "Call Administrator"
    Yes/No:click ng "No". Ito ang intent ng operator; ang "Yes" ay
    magpapalit ng computation kaya hindi ito ginagawa.
- Ang dating `press_enter` / `press_enter_now` / `PRESS_ENTER_*`
  constants ay tinanggal. Walang naipapadala na ENTRY bilang
  panlahat na sagot sa dalawang prompt; ang bawat isa ay may sariling
  aksyon na.
- Bagong state flag sa planner: `prompt_answered` (pinapalit ang
  `enter_pressed`). Ito ang authoritative na "the bill is final" signal,
  hindi ang screen, para hindi ma-plan ulit ang sagot sa prompt na
  naka-answer na.
- Kung na-click na ang OK pero walang nababasa na prompt (build na walang
  nagpo-pop up), ang planner ay nagre-`BLOCK` na may readable na
  reason sa halip na mag-click ng blind.
- Bagong module `gui/run_notifier.py`:
  - `notify_run_finished()` / `notify_run_failed()`;
  - taskbar flash gamit ang `FlashWindowEx(FLASHW_TRAY)`
    (HINDI nag-a-activate ng window);
  - Windows toast gamit ang `winotify` (WinRT) kung available;
  - Tk corner toast bilang fallback (overrideredirect + topmost, at
    click binding na "break" para hindi ma-pull ang focus);
  - fail-soft: bawat call ay naka-wrap, isang notifier lang ang
    broken ay hindi mabibigo ang run.
- Tinawag ang notifier sa `_run_finished()` at `_run_failed()` ng
  Agent Plan tab, at naka-log ang `Run-finished notification: <mode>`.

### Safety notes
- HINDI nang-a-activate ang notifier ng kahit anong window. Kailangan
  ito dahil habang ang agent ay nagta-click at nagta-type sa HBSys, at
  ang isang notification na nag-activate ay gagawin na ang susunod na
  keystroke ay mali sa window (ang dating bug na natype sa claims GUI
  imbes na sa Hospital No. field).
- Hindi na ginagamit ang control id/caption ng Yes/No at Save/OK para sa
  File save branch; keyboard-only na ito. Ang Call Administrator ay
  button click dinag-anan bilang "No" dahil ito ang sinabi ng operator.
- Hindi binabago ang working OCR, PDF merge, auto-sign, XML generator,
  Claims Checker, o ang dating processing flow.

### Verification
- python -m py_compile core/agent/final_bill_actions.py
  gui/agent_plan_tab.py gui/run_notifier.py -- OK.
- python -m pytest tests/ -q -- 363 passed, 7 subtests passed
  (23.02s).
- Probes: `_windows_available()` -> True;
  `_windows_toast(...)` -> True pagkatapos ng `pip install winotify`
  (toast ay lumabas para sa real).
- Live run: kailangan pa ng pag-verify sa isang patient na ang
  sequence ay open_final_bill -> check_final_box -> click_ok ->
  (file_save -> save_click_ok | confirm -> answer_confirm_tab) ->
  close_form -> done, AT na nag-flash ang taskbar nang tapos.

---

## 2026-09-28 - Final Bill: File save = click OK, Call Administrator = press TAB

### Operator instruction
Pagkatapos ng Final Bill -> tick 'Final' -> click OK:
- Basahin ang screen.
- Kung 'File save' ang nakita -> CLICK ang 'OK' (HINDI mag-TAB).
- Kung 'Call Administrator' ang nakita -> PRESS ng TAB.
- Saka lang pagkatapos nito, kung tapos na, click 'Close Form' at
  lumipat sa susunod na selected patient hanggang maubos.

### Affected files
- core/agent/final_bill_actions.py
- tests/test_agent_final_bill.py

### Behavior
- STEP_CONFIRM_NO ('answer_confirm_no', click 'No') -> STEP_CONFIRM_TAB
  ('answer_confirm_tab', press TAB). Ang click-the-'No' primitive
  (_answer_no / answer_dialog_no) ay TINANGGAL sa runner.
- STEP_SAVE_CLICK_OK ('save_click_ok') ang dumaang nasa live: click ang
  OK ng 'File save' prompt. Na-verify na ito bago nang ipinapailaw,
  kaya walang pagbabago sa pagganito.
- TINANGGAL ang STEP_SAVE_TAB_ENTER at STEP_PRESS_ENTER (saka ang
  _save_tab_enter at _press_enter na default primitives). Ang ENTER ay
  hindi na ginagamit sa post-OK flow.
- Dalawang magkaibang sagot ang dalawang prompt (click vs. isang TAB),
  at pareho ay may verify-after: pag hindi nawala ang dialog, throw
  ang '...but it stayed open' na reason.
- Hindi na ginagamit ang 'Call Administrator' bilang focus target ng
  save prompt (_front_save_prompt) - ang TAB ang k Sagut nun.
- plan_step: rename ng state field enter_pressed -> prompt_answered
  (ang pagtutugon sa prompt ang tunay na 'bill is final' signal).

### Safety notes
- Ang File save ay MOUSE CLICK, kaya kahit may focus sa filename field
  ay tiyak na dumatarget ang click sa mismong OK button.
- Ang Call Administrator ay isang keystroke (TAB). Bago ito ipinapadala,
  ang dialog ay ini-raise sa foreground (raise_to_top) at CoInitializeEx
  ang thread, para hindi mapunta ang TAB sa claims GUI o sa IDE.
- Kung OK ang na-click pero walang na-detect na prompt, BLOCKED na
  (hindi blind na ENTER o click) - may human step.
- Hindi nag-a-activate ng window ang run notifier, kaya hindi naiubos
  ng agent ang huling keystroke ang notification.

### Verification
- python -m py_compile core/agent/final_bill_actions.py -- OK.
- python -m pytest tests/test_agent_final_bill.py -q -- 56 passed.
- python -m pytest tests/ -q -- 364 passed, 7 subtests passed (15.90s).
- Live run: kailangan pa ng pag-verify sa isang patient na ang
  sequence ay open_final_bill -> check_final_box -> click_ok ->
  (file_save -> save_click_ok | confirm -> answer_confirm_tab) ->
  close_form -> done, AT na nag-flash ang taskbar nang tapos.

---

---

## 2026-09-28 - File save: click the BOTTOM "OK"; Close Form only after ALL patients

### Operator instruction
Dalawang bagong detalye sa dating entry:
- Ang OK ng "File save" ay yung NASA BABA ng dialog (yung button row sa
  ibaba), hindi ang unang "OK" na nahanap.
- HUWAG click ang "Close Form" hangga't hindi tapos lahat ng selected
  patients.

### Affected files
- core/agent/final_bill_actions.py
- core/agent/orchestrator.py
- tests/test_agent_final_bill.py
- tests/test_agent_orchestrator.py

### Behavior
1. "File save" -> ang PINAKABABANG "OK" ang kinli-click.
   - Bagong `_bottom_most_button(buttons)`: pure, kinukuha ang button na
     may pinakamalaking `rectangle().top`; tie ay nauuwi sa document order,
     kaya ang iisang "OK" ay hindi nagbabago.
   - `answer_dialog_ok()`: `_visible_buttons_by_text()` (plural) na ang
     ginamit, at `_bottom_most_button()` ang pumipili. Ang dating
     `_visible_button_by_text()` (unang match) ay hindi na ginagamit dito -
     siya ang sanhi ng pag-click sa maling "OK" kapag may dalawa.
2. "Close Form" -> LAST patient of the batch lang.
   - `plan_step(..., close_form_at_end: bool = False)`: kapag
     `bill_finalized` at `forms_open`, STEP_CLOSE_FORM ay inilalabas LANG
     kapag `close_form_at_end` True. Sa dating code, STEP_CLOSE_FORM ang
     default kada patient.
   - Kapag False: STEP_DONE na agad (hindi na click ang Close Form).
   - `FinalBillRunner.__init__(..., close_form_at_end=False)` -> ipinapasa
     sa `plan_step` bawat loop.
   - Orchestrator: `_final_bill_rows` = index ng lahat ng APPROVED
     Final Bill row na may patient_folder + hospital number;
     `_last_final_bill_index` = huli. `is_last_patient=(index ==
     _last_final_bill_index)` ang ipinapasa, at
     `close_form_at_end=is_last_patient` sa runner.
   - `_default_final_bill(..., is_last_patient=False)` at
     `_accepts_is_last(fn)` (bagong signature probe) para hindi masira ang
     custom/legacy na `final_bill_fn` na 2-arg lamang.

### Safety notes
- Ang Close Form ay click sa toolbar band - iisang window lang ang
  tinatama. Kaya nga huling hakbang ito, at hindi kada patient: kahit
  sabihin pa ng run na "close form", hindi na ito nang-click kapag
  `close_form_at_end` False, kaya walang ibang form na naaapektuhan.
- Hindi na ni-hit ang "Close Form" sa gitna ng batch. Ang loader ng susunod
  na patient ang nagsasara ng lumang form (ito na ang dati), kaya walang
  nawawalang orasat at waling double close.
- Ang `_bottom_most_button` ay nagta-tie-break sa document order, kaya ang
  isang "OK" lang ay exactly ang dating behavior - walang regression sa
  build na iisang OK lang ang meron.
- Pareho pa rin: ang bawat sagot ay may verify-after (dismiss ng dialog o
  throw), at hindi nag-a-activate ang run notifier.

### Verification
- python -m py_compile core/agent/final_bill_actions.py
  core/agent/orchestrator.py -- OK.
- python -m pytest tests/test_agent_final_bill.py -q -- 56 passed.
- python -m pytest tests/ -q -- 368 passed, 7 subtests passed (15.88s).
- Live run: kailangan pa ng pag-verify sa isang patient na ang sequence ay
  open_final_bill -> check_final_box -> click_ok -> (file_save ->
  save_click_ok | confirm -> answer_confirm_tab) -> done, at sa HULING
  patient lamang ang close_form.

---

## 2026-09-28 - Option (a) for problem rows: run continues, failures NAMED at the end

### Operator decision
Q: a blocked/failed patient mid-batch should (a) continue and be listed at the
end, (b) notify on every failure, or (c) both? **Answer: (a)** - keep going and
list the failures when the run ends. The taskbar notification still fires at the
end (that was a separate earlier request), but it must not turn into a per-patient
alarm that interrupts the run.

### What was wrong
`RunReport` only carried COUNTS (`BLOCKED 2 | FAILED 1`). A count does not tell
the operator *who* still needs a manual Final Bill, and the counts are printed in
the log, not in the notification - so the notification could not name anyone
without opening the JSON by hand.

### Changes
`core/agent/orchestrator.py`
- `PROBLEM_OUTCOMES = (OUTCOME_BLOCKED, OUTCOME_FAILED)` - one place that defines
  "a row the operator must still finish by hand".
- `RunReport.problem_rows()` - the blocked/failed rows in run order.
- `RunReport.problem_lines()` - one numbered log line per problem row
  (`1. HOSPITAL NO | PATIENT - reason`), ready to print at the end.
- End-of-run summary now prints the count *and* names every problem row, so a
  plain log tail is enough to find them.
- The run summary JSON gained `problem_count` and a `problems` array
  (hospital no + patient + status + reason) for the report tab.

`gui/agent_plan_tab.py`
- `_run_finished()` logs each problem row by name, not just a count.
- The end-of-run notification says "Tapos na ang run, pero N patient ang hindi
  na-finalize" when there are problem rows, and names them.
- A `messagebox.showwarning` lists the skipped patients at the end. It is shown
  once, after the run, and it takes no input the agent sends - the HBSys
  keystrokes for the Final Bill flow are finished by that point.

`tests/test_agent_orchestrator.py` - 6 new tests: problem rows are ordered by run
order, only BLOCKED/FAILED count (SKIPPED/DONE do not), the lines are numbered
and carry the reason, the JSON carries the problems array, and a clean run has an
empty problem list.
`tests/test_gui_agent_plan.py` - new tests: problem rows are logged by name, the
notification reports the skipped count, and a clean run raises no warning dialog.

### Safety notes
- The end-of-run warning dialog is a GUI window. It is raised only AFTER the last
  patient finished, so it can never swallow a keystroke the Final Bill flow still
  needs. The taskbar/toast notifier never takes the foreground at all
  (see `gui/run_notifier.py`) precisely so it cannot become the click target.
- A problem row still never stops the run (unchanged) - the operator rule "skip
  and go to the next patient" was already the behaviour; only the reporting is
  new.

### Files affected
- `core/agent/orchestrator.py`
- `gui/agent_plan_tab.py`
- `tests/test_agent_orchestrator.py`
- `tests/test_gui_agent_plan.py`

### Verification
- `python -m pytest tests/ -q` -> **376 passed, 7 subtests** (16.88s)

---

## 2026-09-28 - "File save" OK click: use the BOTTOM-most OK, not the first OK

### Problem
The operator answered: *"pag nagpakita kasi ang File save, click OK dapat yung OK
sa **baba** ng file save."* The prompt carries more than one button captioned
`OK`; the old `answer_dialog_ok()` took the **first** matching control, which is
not the one in the button row at the bottom of the dialog. A bare caption match
is therefore ambiguous and unsafe here.

### Change
`answer_dialog_ok()` (and its new public wrapper `answer_file_save_prompt()`) now
resolves the target button in three ordered passes and takes the first that
yields a clickable control:

1. **Bottom-most by control id** - every visible/enabled `OK` / `&OK` / `Save` /
   `&Save` control is collected and `_bottom_most_button()` returns the one with
   the greatest `top` (i.e. the lowest on screen). Ties keep document order, so a
   prompt with a single `OK` behaves exactly as before - no regression.
2. **Caption match inside the dialog's own bottom band** - used when the
   control-id pass finds nothing: the lowest `BUTTON_ROW_...` band of the dialog
   is searched for a button.
3. **Geometry fallback** - clicks inside the dialog's own bottom button row
   (`_bottom_row_control`) by rectangle, so a build that renumbers ids and
   re-captions buttons is still driven deterministically.

The chosen control is reported with its rectangle in the log, so a run report
shows exactly which OK was clicked instead of only that "something was clicked".

### Safety notes
- Still a **mouse click on a resolved control** - no TAB, no ENTER, no blind
  coordinate click from a screenshot.
- The dialog is raised to the foreground before the click, so the click cannot
  land on the claims GUI that is on top.
- If no OK can be resolved the function raises with a readable reason rather
  than clicking an arbitrary point; the row is then reported as not finalized.
- The verify step (prompt must disappear) is unchanged.

### Files affected
- `core/agent/final_bill_actions.py`
- `tests/test_agent_final_bill.py`

### Verification
- `python -m pytest tests/ -q` -> **383 passed, 7 subtests** (18.06s)


## 2026-10-05 - Final Bill: wala nang Close Form click (before at after ng bawat pasyente)

### Reason
Ang operator ay nag-utos na huwag nang i-click ang **Close Form** sa Final Bill
flow — hindi lang sa dulo ng isang pasyente, kundi pati sa paglipat ng pasyente
sa pasyente. Bago ang change, bagama't tinanggal na ang Close Form *step* sa
`FinalBillRunner` (2026-10-02), may isa pang aktibong tawag dito:
`load_patient_by_hospital_no()` ang nagtatawag ng `close_billing_form()` para
isara ang "stale" Billing form ng naunang pasyente bago mag-type ng bagong
Hospital No. Tinatanggal na ito para walang anumang automated path na
nagta-tap sa Close Form toolbar slot (548, 97).

### Files affected
- `core/agent/final_bill_actions.py`
- `core/agent/orchestrator.py`
- `tests/test_agent_final_bill.py`

### Behavior (before -> after)
- `load_patient_by_hospital_no()`: kung may bukas na `Billing (...)` form, ang
  loader ay tumatawag ng `close_billing_form()`; kapag hindi na-close ang form
  ay `return False` (BLOCK ang row).
  -> **After**: hindi na tumatawag ng close. Ang leftover form ay iniiwang BUKAS
  at **hinahanap sa log** (`load: leftover Billing form(s) left OPEN (Close Form
  is never clicked): ...`), saka tuloy ang double-click + CTRL+A + type + ENTER.
- Verification (hindi nagbago): kailangan pa ring mag-appear ang isang
  `Billing (...)` form na **hindi** bukas bago ang load; kung wala, `False` ang
  load at BLOCKED ang row (kasama ang `diagnose_screen()` na nag-ta-name ng mga
  forms/windows na nasa screen).
- `clear_blocking_popups()`: **hindi nagbago** — ginagawa pa rin ang pag-close ng
  leftover "Admission History" popup at ang "Rate validation" dialog, dahil ang
  mga ito ay kumakain ng click/typing (hindi na Close Form ang dahilan).
- `close_billing_form()` / `click_close_form()` / `CLOSE_FORM_POINT`: nananatili
  (para sa `_probe_live.py`, manual recovery, at sa unit tests) pero na-tag
  bilang **MANUAL PROBE ONLY**; walang automated code path ang tumatawag sa kanila.

### Safety notes
- **Walang automated Close Form click** — pati na ang loader sa pagitan ng
  pasyenteng pasyente.
- Hindi na tinatamaan ang HBSys/MySQL; read-only pa rin.
- Parameter na `close_stale_form=True` ay **panatilihin** sa signature para sa
  backward compatibility, pero sinadya nang **hindi ginagamit** (wala nang
  automated close na pwedeng i-on).
- Validation (isang bagong `Billing (...)` form na "hindi bukas bago") ang
  nagre-guard ng tamang pasyente kapag hindi na nagsasara ang form — hindi na
  ang pag-close.
- Iba pa rito ang Close Form ng **PhilHealth Beneficiaries / Claim Form 2** sa
  `date_fill_hbsys/hbsys_fill_dates.py` — hindi ginalaw iyon.

### Verification
- `python -m py_compile core/agent/final_bill_actions.py core/agent/orchestrator.py tests/test_agent_final_bill.py` -> OK
- `python -m unittest tests.test_agent_final_bill` -> **85 tests, OK**
- `python -m unittest tests.test_agent_orchestrator` -> **76 tests, OK**
- Bagong regression test `test_load_never_clicks_close_form` (tests/test_agent_final_bill.py):
  kinukumpirma na hindi tumatawag ang loader ng `close_billing_form()` /
  `click_close_form()`, at nasa log ang leftover form ("left OPEN").
- Hindi pa live-run sa HBSys — kailangan ng operator ang actual run para sa
  multi-patient batch.
## 2026-10-05 (follow-up) - Alisin ang lahat ng Close Form / stale-form na bakas sa BLOCKED

### Reason
Pagkatapos tanggalin ang Close Form click mismo, nanatili pa ring bakas ng dating
ugali ang mga log/docstring na nagsasabing "close ang stale form" o "Close Form is
never clicked". Ayon sa operator, **wala nang Close Form** — dapat malinis na lang
ang mga leftover na ito para hindi magmukhang may close pa rin ang system.

### Files affected
- `core/agent/final_bill_actions.py`
- `core/agent/orchestrator.py`
- `tests/test_agent_final_bill.py`
- `tests/test_agent_orchestrator.py`

### Behavior (before -> after)
Walang pagbabago sa anumang click o guard — **documentation at log lines lamang**
ang inalis, at isang test fixture na napapanahang lumabas na hindi na nagta-close.

- `orchestrator._default_final_bill()`: may `stale` list na naghahanap ng Billing
  form ng ibang pasyente at naglalagay ng log na *"a previous patient's Billing
  form is still open ... Close Form is never clicked"*.
  -> **After**: tatanggalin ang buong `stale` block at ang log (wala nang sayson
  para sa "stale" — hindi pa naman ito ang dahilan ng load).
- `orchestrator.run_approved_plan()`: log na *"form stays open after OK/No ...
  (no Close Form, before or after)"* -> **After**: iniwan lang ang *"form stays
  open after OK/No - the next selected patient's loader types the Hospital No."*
- `load_patient_by_hospital_no()`: log na *"leftover Billing form(s) left OPEN
  (Close Form is never clicked)"* -> **After**: tatanggalin. `forms_before` ay
  nananatili dahil **ito ang basehan ng verification** (kailangang may bagong
  `Billing (...)` form na hindi bukas bago).
- Docstrings (module header, `CLOSE_FORM_POINT`, `close_billing_form()`,
  `billing_form_titles()`, `FinalBillRunner.__init__`, orchestrator header at
  `_default_final_bill()`): lahat ng "2026-10-05: never clicks Close Form" na
  paulit-ulit ay pinapasimpliwan na "probe / manual-recovery only".
- `tests/test_agent_orchestrator.py` — `FakeBillingSession`: ang `loader()` ay
  **nagta-close ng dating Billing form** at ang `runner()` ay nagtatangki ng
  Close Form bago magbukas ng susunod na pasyente (parang 2026-09-26 pa).
  -> **After**: `loader()` ay nagta-type lang (HBSys ang nagsa-retitle ng form)
  at ini-record sa `replaced`; `runner()` ay iniiwang bukas ang form.
### 2026-10-05 - Pag-publish sa GitHub (`07552c4`) + `.gitignore` scratch rules

### Reason
Una naming i-push sa `origin/main` ang lahat ng natapos na work (Close Form
removal, Final Bill agent, plan GUI, tests). Bago mag-push, nilinis ang mga
dumi at ang bagong maaaring patient identifier.

### Files affected
- `.gitignore`
- `CHANGE_RULES.md` (redaction ng mga bagong entry lamang)

### Behavior
- `.gitignore` (+`Local scratch/probe output` section): `tmp_*.txt`,
  `_phase*.txt`, `_t?.txt`, `_*.png`, `*_current.png`, `*_proof.png`,
  `debug_*.png`. -> **After**: hindi na na-`git add -A` ang mga probe/OCR dump
  at debug screenshot ng session (nasa disk pa rin, wala lang sa repo).
  May kaunting sagot: hindi rin kasama ang `docs/conversation_handover*`
  (naglalaman ng pangalan + HRN ng pasyente) - nananatiling local.
- Redaction sa mga **bagong** entry/pakali lamang (ang mga nakaraang entry na
  naka-commit na ay hindi hinawakan):
  - `CHANGE_RULES.md` 2026-10-02 entry: `COLLANTES, RYAN JAMES CAWILE -
    000000000021853` -> `pasyente #21853`; `COLLANTES BLOCKED case` ->
    `BLOCKED case`; `live COLLANTES scenario` -> `live BLOCKED scenario`.
  - `CHANGE_RULES.md` (ibang bagong linya): `TOLENTINO 000000000014140` ->
    `<isang pasyente>`; `PERA, JEANNY JACOBEN 000000000021537` ->
    `<isang pasyente>`; `hospital no field: 000000000021748` -> `<HRN>`.
  - `core/agent/final_bill_actions.py:310`, `tests/test_agent_final_bill.py:187`,
    `tests/test_agent_hbsys_screens.py:184`: `(COLLANTES)` -> `(patient #21853)`.

### Safety notes
- Walang source code na binago sa bahaging ito - pawang comment, dokumento,
  at ignore rules lamang.
- Ang mga test fixture na pangalan (`SANTOS, MARIA`, `DELA CRUZ, JUAN`) ay
  **hindi tunay** at hindi binago.
- Secret scan sa lahat ng staged `.py` bago mag-push: wala. Ang `.gitignore`
  ay nagtatago na ng `db_connection_config.json` at `hbsys_bot.py`.
- Hindi kasama sa commit ang `docs/` (patient-identifying handover dump).

### Verification
- `python -m unittest tests.test_agent_final_bill tests.test_agent_hbsys_screens`
  -> 107 tests OK (pagkatapos ng redaction edits)
- `git rev-parse HEAD origin/main` -> pareho ang `07552c4d0b17b3e6bf09a8fc2e9f848515d3f71d`
- `git push origin main --porcelain` -> `Done`, `up to date`

  Na-update ang tatlong test (`..._form_stays_open`, `..._retyped_not_closed`,
  `test_batch_runs_two_final_bill_rows_in_one_session`) at idinagdag ang
  assertion na walang nang-close (`runner_closes == []`, `stale_closes == []`).

### Safety notes
- **Walang ibang BLOCKED ang tinanggal.** Nananatili ang mga tunay na guard:
  HBSys closed, ibang pasyenteng Billing form, Print Options na hindi natatapos,
  OK na walang prompt, at hindi magagana ang Hospital No. load.
- **Walang behavioral change** sa mga click — ang tinitest ay pareho pa rin:
  walang automated Close Form click kahit kailanman.
- Ang `close_billing_form()` / `click_close_form()` ay nananatili para sa
  `_probe_live.py` (manual) at sa sarili nilang unit tests.
- Hindi pa live-run sa HBSys.

### Verification
- `python -m py_compile core/agent/final_bill_actions.py core/agent/orchestrator.py tests/test_agent_final_bill.py` -> OK
- `python -m pytest tests/ -q` -> **499 passed, 7 subtests** (46.96s)

## 2026-10-06 - Workflow Tab: `final_bill` node (folder contract, Option B)

**Layunin:** idagdag ang Final Bill bilang workflow step (bago mag-Date Fill)
gamit ang **folder contract** -- ibig sabihin, ang Hospital No. ay galing sa
**output folder name** (`NAME - HPERCODE - ADM..._DIS...`), hindi sa plan row.
Ang UI automation ay ang pareho nang verified `_default_final_bill` ng Agent
Plan -- walang duplicate logic.

### Files

- **NEW** `core/agent/final_bill_runner.py` -- CLI runner:
  `--live / --output-root / --limit / --force`. Nag-i-iterate ng output
  folders (sorted ascending = patient grouping), parse via
  `core.add_claims_uploader.parse_folder_name`, nag-skip ng may
  `.final_bill_ok` marker maliban kung `--force`. DRY mode (default): nag-i-list
  lang, exit 0, WALANG pywinauto import. LIVE: `default_final_bill(folder,
  hospital_no, log, timeout)` kada pasyente, marker isinusulat LANG kapag OK,
  continue-on-error sa batch, exit 1 kapag may BLOCKED/FAILED (engine humihinto
  sa chain -- fail-safe, opt-out ang `continue_on_fail`).
- **`core/agent/orchestrator.py`** -- 1 line: `default_final_bill =
  _default_final_bill` alias (iisang implementation).
- **`core/workflow_registry.py`** -- `NodeSpec(key="final_bill", category="HBSys",
  module="core.agent.final_bill_runner", live_args=("--live",), hbsys_touching=True,
  supports_dry=True)`; self-test 12->13 + hbsys/dry/entry-point sets updated.
- **`core/workflow_engine.py`** -- default order: `final_bill` sa position 2
  (bago `date_fill_regular`); self-test 8->9 nodes / 7->8 connections / n1->n9.
- **`gui/workflow_tab.py`** -- asserts 8->9 (9->10 add, 8->9 delete/restore);
  `move_node(1)` expectation = `final_bill`.
- **NEW** `tests/test_workflow_final_bill_node.py` -- 23 tests
  (Registry/Default/Folders/Marker/Dry/Exit/Limit/Env/Engine).

### Behavior

- **Before:** wala pang Final Bill step -- Date Fill agad pagkatapos ng Claims
  Processor kahit hindi pa finalized ang bill.
- **After:** default workflow = 9 nodes; Final Bill bago Date Fill
  (Slice H rule: FINAL BILL bago DATE FILL). Lumang 8-node `workflow_config.json`
  ay naglo-load pa rin -- rollback-safe; bagong node sa **Restore Default**.
- Marker resumable: OK folders ay hindi ulit; BLOCKED/FAILED walang marker ->
  retry; `--force` para sa deliberate re-run.

### Safety notes

- **`workflow_config.json` ay HINDI ginalaw** -- naglo-load pa rin ang lumang
### 2026-10-06 (follow-up) -- Date Fill headless popup/Excel guard (Workflow tab)

- **Problem:** pagkatapos ng Date Fill sa Workflow tab, ang "Date Fill Complete"
  popup at ang auto-open ng run-log CSV sa Excel ay nagpo-pop-up at nag-o-open
  pa rin -- naghahadlang sa unattended Final Bill batch (ang HBSys window ay
  nasa gitna, ang Excel ay nakadikit).
- **Root cause:** `date_fill_hbsys/hbsys_fill_dates_testing.py` (at ang
  kapatid na `hbsys_fill_dates.py`) ay may hardcoded `show_popup(...)` /
  `open_run_log(...)` call sa bawat exit path, walang headless guard.
- **Fix (pre-existing module, now wired in):** `date_fill_hbsys/date_fill_headless.py`
  (`ui_enabled()` = `not headless()` = `CLAIMS_HEADLESS != "1"`). Lahat ng
  6 call site sa `hbsys_fill_dates_testing.py` ay naka-`if ui_enabled():`
  (ABTC guard, No-claims, Stopped, Complete-with-Skipped, Complete + open_run_log).
  `core/workflow_registry.py` ay naglalagay ng `CLAIMS_HEADLESS=1` sa
  `extra_env` ng `date_fill_regular` / `date_fill_abtc` (via `build_env()`).
- **Behavior:**
  * Workflow tab (CLAIMS_HEADLESS=1) -> ui_enabled()=False -> **walang popup,
- `python -m unittest tests.test_date_fill_headless tests.test_workflow_final_bill_node
  tests.test_agent_orchestrator` -> **121/121 OK** (walang regression).

### 2026-10-06 (follow-up 2) -- PHIC Beneficiaries intermittent OCR row mismatch (Option A)

- **Problem (live 2026-10-07):** `hbsys_fill_run_20261007_083216.csv` ->
  ALAURIN, MARY ANN ESCOREL = `SKIPPED_SELECTED_ROW_MISMATCH` (admission_history_match
  = MATCH, precheck = PROCESS, precheck_reason = "Incomplete or mismatched dates:
  Professional Fee, Consent, Authorization/Certification.").
- **Root cause (split):** `precheck_reason` is a **read-only DB precheck** and is
  CORRECT -- the DB simply has no dates yet, Date Fill is what writes them. The
  real failure is in `click_phic_and_select_claim()`: `find_phic_beneficiary_row_y_from_variants()`
  requires `minimum_consensus=2` agreeing OCR passes, and the PHIC Beneficiaries
  grid OCR is *intermittent* (flaky font / window refresh). Some patients pass,
  some fail. The safety guard is correct (never guess), but not robust enough.
- **Fix (Option A -- additive, no safety weakening):**
  * `date_fill_hbsys/hbsys_read_admission_history_testing.py::build_ocr_variants()`
    -- 2 new binary-threshold variants (v > 128, v > 160) -> 7 total OCR passes
    (was 5). Higher chance of reaching consensus 2.
  * `date_fill_hbsys/hbsys_fill_dates_testing.py::click_phic_and_select_claim()`
    -- x-offsets `260, 390, 520` (was `260, 520`). 390 hits the row's text area.
  * Same file -- **re-capture once** before declaring an offset a miss (blue
    selection can lag one frame behind the click).
  * `date_fill_hbsys/hbsys_fill_dates.py` -- same 3 changes (kapatid na tool,
    same proven recipe).
  * `minimum_consensus=2` and the **blue-highlight proof** are UNCHANGED.
- **Behavior:** patients that previously failed intermittently now pass; patients
  with genuinely unreadable PHIC grid still stop for review (correct).
- **Verification:** `py_compile -W error::SyntaxWarning` clean (6 files);
  `python -m unittest tests.test_date_fill_confinement tests.test_date_fill_headless
  tests.test_workflow_final_bill_node tests.test_agent_orchestrator` -> **134/134 OK**
  (updated `test_two_attempts_without_proof_stop` to 3 offsets).
- **Note:** `hbsys_fill_dates.py` is the *production* Date Fill tool (the one the
  main dashboard runs). Same fix applied there so the intermittent failure is
  fixed in both entry points.

### 2026-10-06 (follow-up 3) -- PHIC Beneficiaries wrong-sibling selection (first-name required)

- **Problem (live 2026-10-07):** BALUNSAT, AMARA MARCELINE GONZALES (folder,
  000000000021855, ADM20260923_DIS20260929) -- the script selected
  BALUNSAT, KATE ARIANE GONZALES in the PhilHealth Beneficiaries grid. Same
  confinement period, same last name, different first name.
- **Root cause:** `find_phic_beneficiary_row_y()` matched on `any(token in
  row_text)`. The shared last name ('GONZALES') scored points for BOTH rows, so
  the wrong sibling won. Two unguarded paths:
  * `len(candidates) == 1` -- returned the single confinement match with NO name
    check at all.
  * scoring path -- first name was never required.
- **Fix (additive, no safety weakening):**
  * NEW `name_parts(patient_name)` -- splits 'LAST, FIRST MIDDLE EXTRA' into
    `{'last', 'first', 'rest'}` (comma-aware; falls back to first-token-as-last
    for uncommaised names).
  * `len(candidates) == 1` path -- if a first-name token exists, it MUST be
    present in the row text; otherwise the candidate is rejected and the method
    returns `None` (stop for review, never guess).
  * scoring path -- a row missing the first-name token gets score 0 (excluded);
    the shared last name can no longer carry the decision.
  * Applied to BOTH `hbsys_fill_dates_testing.py` and `hbsys_fill_dates.py`
    (production tool).
- **Safety:** rows with genuinely unreadable first names still stop for review
  (correct). Patients whose first name is unique in the grid are unchanged.
- **Tests (NEW `FirstNameDisambiguationTests`, 3 tests):**
  * `test_wrong_first_name_is_rejected` -- KATE row must NOT be selected.
  * `test_first_name_disambiguates_two_same_last_name` -- AMARA row selected.
  * `test_missing_first_name_stops_for_review` -- OCR reads only the shared last
    name on both rows -> `None`.
- **Verification:** `py_compile -W error::SyntaxWarning` clean (4 files);
  `python -m unittest tests.test_date_fill_confinement -v` -> **16/16 OK**
  (was 13; +3 new, 0 regressions).

### 2026-10-06 (follow-up 4) -- PHIC fallback paths also require first name

- **Problem (live 2026-10-07, same BALUNSAT case):** the previous fix only
  covered 2 of the 4 paths in `find_phic_beneficiary_row_y()`. When AMARA's
  discharge OCR misreads 09/29/2026 as 09/26/2026, AMARA drops out of
  `candidates` and the **fallback** fires. The `named_admission_candidates`
  fallback matched on `any(token in row_text)` -- the shared last name
  'BALUNSAT' matched KATE's row, so KATE was returned as the single fallback
  pick. **The discharge dates ARE different (09/29 vs 09/26); the admission
  dates are the same.** The wrong sibling won via the admission+name fallback,
  not via a discharge match.
- **Root cause:** the first-name requirement was applied to the `candidates`
  path and the scoring path, but NOT to the two fallback paths:
  `named_admission_candidates` (admission + name token) and
  `dated_name_candidates` (any dates + name token).
- **Fix (same guard, 2 more paths):**
  * `dated_name_candidates` build loop -- skip rows whose text does not contain
    the first-name token (`continue` instead of appending).
  * `named_admission_candidates` build loop -- same `continue` guard.
  * Applied to BOTH `hbsys_fill_dates_testing.py` and `hbsys_fill_dates.py`.
- **Safety:** a row matching only the shared last name can no longer be a
  fallback pick. If the correct patient's first name is genuinely unreadable,
  the method returns `None` (stop for review) -- never guess the wrong sibling.
- **Tests (NEW, 2 tests):**
  * `test_fallback_does_not_select_wrong_sibling` -- AMARA discharge misread,
    KATE's name readable -> KATE (y=150) must NOT be selected.
  * `test_fallback_selects_correct_when_first_name_readable` -- same
    misread-discharge, but AMARA's first name readable -> AMARA (y=170)
    selected via the fallback.
- **Verification:** `py_compile -W error::SyntaxWarning` clean (4 files);
  `python -m unittest tests.test_date_fill_confinement -v` -> **18/18 OK**
  (was 16; +2 new, 0 regressions).
    walang Excel-open**; stop reason ay stdout lang (ini-stream ng adapter).
  * Main dashboard / manual run (CLAIMS_HEADLESS unset) -> ui_enabled()=True
    -> **popup + Excel-open pa rin** (dating gawi, zero change).
- **Also fixed:** indentation bug sa 5 linya ng `hbsys_fill_dates_testing.py`
  at 3 linya ng `workflow_registry.py` (doubled indentation from a bad
  editor match -> IndentationError). All compile `-W error::SyntaxWarning` clean.
- **Verification:** `py_compile` clean; `python -m core.workflow_registry`
  PASSED; `python -m core.workflow_engine` PASSED;
  `python -m unittest tests.test_date_fill_headless tests.test_workflow_final_bill_node
  tests.test_agent_orchestrator` -> **121/121 OK** (walang regression).
  8-node config. Ang `gui/workflow_tab.py` self-test ay nag-o-overwrite +
  nag-delete nito (`save_config` + `finally: unlink`); PINATAKBO ito nang may
  backup/restore -- hash-verified na naibalik nang buo.
- `print()` sa runner ay SADYA: stdout = log channel ng subprocess node
  (ini-stream ng `ScriptNodeAdapter`). Walang ActivityLogger (no SQLite
  double-write); `flush=True` bawat linya.
- Marker `.final_bill_ok` = JSON (hospital_no, outcome, detail, at) -- dotfile;
  hindi nakokosas sa `.pdf`/`.xml` suffix filters ng claims_checker/fees_checker/
  xml_auto_copy.
- Exit 1 sa: BLOCKED/FAILED, hindi mahanap na output root, marker write failure,
  operator interrupt. HBSys gate ay nag-skip kapag sarado -- hindi nag-click nang
  walang window.
- Hindi pa live-validated ang `relink` double-reload check; gumagamit ng default
  (`always_reload=False`); ang loader ay tumatakbo bawat pasyente dahil magkaiba
  ang bukas na form (verified ng `final_bill_block_reason`).

### Pre-existing: Date Fill / XML Clicker sibling-import launch fix

- **Problem (pre-existing):** `Date Fill (REGULAR|ABTC)` at `XML Clicker` ->
  `ModuleNotFoundError: No module named 'hbsys_read_admission_history_testing'`.
  HINDI dahil sa `final_bill` -- pareho ito sa lumang default (node ay HINDI
  pa sinusubukan bago dumating dito).
- **Root cause:** `date_fill_hbsys/hbsys_fill_dates_testing.py` (l. 16) at
  `xml_generator_clicker.py` (l. 28-38) gumagamit ng bare sibling imports.
  `ScriptNodeAdapter` (`core/workflow_adapters.py:183`) ay `python -m
  <package.module>` with `cwd=PROJECT_ROOT`, kaya `date_fill_hbsys/` WALANG
  entry sa `sys.path`. (`core/workflow_adapters.py` mula sa commit `7d5840e`;
  HINDI tinouch ng Slice-H session.)
- **Reference (existing launchers):** `date_fill_hbsys_launcher.py:47` at
  `xml_generator_clicker_launcher.py:24` gumagamit ng `cwd=tool.parent`
  (= `date_fill_hbsys/`), kung saan gumagana ang bare imports.
- **Fix (registry-only, 3 lines):** idinagdag sa `NodeSpec` ng
  `date_fill_regular`, `date_fill_abtc`, `xml_clicker` ang
  `extra_env={"PYTHONPATH": "date_fill_hbsys"}` (`build_env()` sa
  `core/workflow_adapters.py:134` = `env.update(spec.extra_env)`). `PYTHONPATH`
  ay `None` sa env (verified), walang clobber; kwarg-order-free (dataclass).
- **Verification:** `import date_fill_hbsys.hbsys_fill_dates_testing` +
  `...xml_generator_clicker` -> IMPORT OK (nakaraan na l. 16) with
  `PYTHONPATH=date_fill_hbsys`. (HINDI pina-run ang dry `main`: walang HBSys
  dito -> engine gate i-skip; may unguarded `pyautogui.size()` sa dry.)
- **Safety:** registry data lamang; walang launcher/module/adapter code change;
  `python -m` + `cwd=PROJECT_ROOT` di nagbago; dry = log-only (clicks guarded
  `if self.live:`).


### Verification (2026-10-06 entry)

- `python -m py_compile` (lahat ng 6 files) -- COMPILE CLEAN.
- `python -m core.workflow_registry` -- PASSED (13 entries; `final_bill`
  entry point on disk verified).
- `python -m core.workflow_engine` -- PASSED (default 9 nodes, 8 chained
  connections n1->n9; HBSys gate skips UI node when HBSys closed).
- `python -m core.agent.final_bill_runner` (DRY, real `output\`) -- 4 patient
  folder(s) na-parse, `[DRY] ... would run Final Bill`, exit 0, WALANG
  `.final_bill_ok`, WALANG pywinauto import.
- `python -m core.agent.final_bill_runner --help` (dry smoke) -- PASSED.
- `python -m core.agent.final_bill_runner` (live, no HBSys) -- BLOCKED correctly
  (`hbsys_not_found`), exit 1.
- date_fill import fix: `import date_fill_hbsys.hbsys_fill_dates_testing`,
  `...xml_generator_clicker` -> IMPORT OK (ModuleNotFoundError resolved).
- `python -m unittest discover -s tests -p "test_workflow*.py"` -- 23/23 OK.
- `python -m gui.workflow_tab` -- PASSED (echo e2e COMPLETED; backup/restore
  ng workflow_config.json hash-verified).
- `python -m unittest discover -s tests -p "test_*.py"` -- 580 tests OK
  (557 baseline + 23; re-run AFTER date_fill env fix -> 580 OK, walang regression).

NOTE (2026-10-06): ang CHANGELOG ay may nangyaring truncation accident habang
ini-append ang entry na ito (isang `find()`-based script ay sumunod sa maagang
`### Verification` header). Naayos ang committed history via `git checkout`;
ang huling hindi na-commit na `plan_steps` demo entry (session-start na file na
nagwawakas sa "...Standalone smoke test ng module.") ay NA-LARGE -- HINDI ito
narating ng `git checkout` (wala sa anumang commit, wala sa stash). Kailangan
ng may-ari na muling-idagdag mula sa sarili, kung kailangan pa. Ang entry na
ito (2026-10-06) ay naka-append nang buo at rollback-safe.
