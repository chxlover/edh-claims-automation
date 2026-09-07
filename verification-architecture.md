# Implementation Plan — Add Attachments Verification Strengthening + Reusable Verification Architecture

Task source: EDH Claims Automation spec (2026-09-07). Planning mode; no files modified yet.

## 1. Inspection findings (verified from actual source code)

### Current Add Attachments flow and its verification gaps

`core/claim_attachments_uploader.py` (1,594 lines) — `AttachmentsOperator` + `run_attachments_loop`:
- **Patient search → highlighted row click**: pixel-based blue band detection ONLY (`detect_highlighted_row_y`). It never verifies the highlighted row belongs to the CORRECT patient. (Note: `core/add_claims_uploader.py` DOES verify via `add_claims_verifier.extract_dates_from_ocr_text` — the attachments flow has no such check.)
- **Attachment popup found** (`find_attachment_popup`, line 547): pywinauto title check "ATTACHMENTS FOR" + "FOR" — the title actually contains `Attachments for {PATIENT NAME} - {CLAIM NO}` (live logs). **This is the most reliable patient-identity evidence in the whole flow (pywinauto window title, hierarchy level 1-2) and it is currently NOT compared against the expected patient.**
- **File dialog** (`wait_for_file_dialog`): new-handle #32770 detection — reliable, keep.
- **Doc-type view loop** (`assign_doc_types_and_upload`, v5.2): deterministic 1:1 `match_files_to_lines` with ambiguous→ABORT, not_found→ABORT — solid LEVEL 1/2 logic, but returns plain `bool` and logs string reasons; results are not structured/persisted.
- **Upload → Enter → Close** (lines 1040-1046): **blind Enter 2s after Upload click** — no detection of the OK confirmation dialog, no error-dialog detection, no check whether the popup closed instead.
- **`close_attachment_popup`** (line 681): popup-gone verify has 3 escape retries, but failure is only `WARNING: popup may still be open` — then the caller marks the patient **PROCESSED**. This is the "silently continue after verification failure" violation (Golden Rule 13).
- **`run_attachments_loop` Step 5.5/6**: `mark_processed` after `assign_doc_types_and_upload` bool; no verification record anywhere in state.

`gui/claim_attachments_tab.py` `_upload_worker` (line 338): parallel copy of the loop; statuses DONE/FAILED only.

`core/attachments_state.py`: JSON state with `failed`/`failed_reasons` — no verification fields, but its load-filter pattern (`k in cls.__dataclass_fields__`) is backward/forward compatible with added fields.

`core/claim_attachments_doc_type.py`: deterministic matcher returning `(matched, not_found, ambiguous)` — already structured; needs wrapping, NOT rewriting. GridLine carries screen coords.

`core/add_claims_verifier.py`: `FolderDates` (patient identity) + OCR date extraction — reused as-is for context.

`core/verification_panel_service.py`: read-only GUI data provider (folder stats/review queue) — NOT a verification engine; no overlap, no reuse conflict. Name collision is cosmetic; new package lives under `core/verification/`.

`core/patient_review_queue.py`: has `ReviewStatus` enum (PENDING/IN_REVIEW/RESOLVED) for patient review — different domain (queue workflow, not check results). Not reusable directly; the new `VerificationStatus` is a separate concept.

Other reusable precedents: `date_fill_hbsys/hbsys_date_fill_precheck.py` `PrecheckResult` (decision+reason) and `hbsys_date_fill_verifier.evaluate_post_save` — same "structured verdict, never guess" philosophy, standalone modules.

### HBSys UI reality (from live logs 2026-09-03/04 and existing code)
- The app is Clarion/FoxPro (`FNWNS3115`, `FNWND370`); the attachment grid is pixel-painted — pywinauto can read window titles and top-level buttons, but NOT grid contents. Reliable evidence hierarchy for this app: **window title/handles (pywinauto) > filesystem (folder/files) > internal deterministic state > pixel band detection > OCR**.
- The v5.1 history proved short-cell OCR (DTR/CF4/ESA reads) is unreliable → must NOT be the verification mechanism (task §5 confirms).
- After Upload, HBSys shows an OK confirmation dialog (owner spec: "click upload... then press OK (enter)"); popup then closes. Persistence inside HBSys is NOT inspectable without reopening the popup and OCRing the grid — OCR-only ⇒ per task §17, do NOT build a mandatory persistence check. Document as limitation.

## 2. Design

### New package: `core/verification/`

```
core/verification/
    __init__.py        # public API re-exports
    result.py          # VerificationStatus, ReasonCode, RetryPolicy, VerificationResult
    evidence.py        # EvidenceSource, EvidenceType, VerificationEvidence
    context.py         # VerificationContext
    base.py            # BaseVerifier (ABC) + helpers
    service.py         # VerificationService (registry + aggregation)
    attachments.py     # AttachmentsVerificationService (stage verifiers)
    exceptions.py      # VerificationError hierarchy
```

**result.py**
- `VerificationStatus(Enum)`: `PASS`, `REVIEW`, `FAIL`
- `ReasonCode(Enum)`: `NONE, PATIENT_MISMATCH, FOLDER_NOT_FOUND, FILE_NOT_FOUND, FILE_NOT_READABLE, DOCUMENT_AMBIGUOUS, DOCUMENT_NOT_MATCHED, ATTACHMENT_WINDOW_NOT_FOUND, UPLOAD_ACTION_FAILED, UNEXPECTED_DIALOG, CONFIRMATION_NOT_DETECTED, POPUP_NOT_CLOSED, UNEXPECTED_UI_STATE, PERSISTENCE_UNCERTAIN, TIMEOUT, SCROLL_FAILED, CANNOT_SCROLL`
- `RetryPolicy(Enum)`: `SAFE_RETRY` (pre-action detection misses), `NO_RETRY` (known FAIL, safe to re-run patient later), `MANUAL_ONLY` (upload-state uncertain — NEVER auto re-upload)
- `VerificationResult` dataclass: `status, check_name, patient_id, patient_name, step, message, reason: ReasonCode, evidence: list[VerificationEvidence], timestamp, duration, recoverable, retryable, retry_policy` + `to_dict()/from_dict()` + `is_pass/is_review/is_fail` properties + `review_summary()` producing the actionable WHAT/EXPECTED/OBSERVED/UNCERTAIN/RECOMMENDED text (task §29).

**evidence.py**
- `EvidenceSource(Enum)`: `PYWINAUTO, FILESYSTEM, INTERNAL_STATE, PIXEL, OCR, ACTION_LOG`
- `EvidenceType(Enum)`: `WINDOW_TITLE, WINDOW_EXISTENCE, BUTTON_STATE, FILE_EXISTENCE, FILE_LIST, MATCHED_ROWS, STATE_FILE, SCREENSHOT_PATH, ERROR_MESSAGE, ACTION_EXECUTED`
- `VerificationEvidence` dataclass: `source, type, description, value` (value is str/bool/int/list — must be JSON-serializable) + `to_dict()`.

**context.py**
- `VerificationContext` dataclass: `patient: FolderDates | None` (reuse existing object — no duplication), `patient_name, hospital_no, folder_path, step, expected_files: list[str], actual_files: list[str], popup_title: str, match_result: tuple | None, extra: dict`. Plain data holder passed to verifiers.

**base.py**
- `BaseVerifier` ABC: `check_name: str`, `verify(self, context) -> VerificationResult` (template: record start time → call `_check(context)` → wrap exceptions as FAIL/`UNEXPECTED_UI_STATE` with evidence).
- Constructor helpers: `self._pass()`, `self._review(reason, message)`, `self._fail(reason, message)` — subclass fills evidence.

**service.py**
- `VerificationService`: `register(verifier)`, `run(check_name, context)`, keeps last results per check. Simple, no event bus (task §30).

**attachments.py — `AttachmentsVerificationService`** (the Add Attachments specialist; composed of BaseVerifier subclasses, all pure-deterministic):
1. `PatientInputsVerifier` (LEVEL 1, check `attachments_inputs`): patient parsed; folder exists (`FOLDER_NOT_FOUND`); expected files exist & readable (`FILE_NOT_FOUND`/`FILE_NOT_READABLE`); every file classifiable by `detect_doc_type` (`DOCUMENT_NOT_MATCHED` for unknown stems). Evidence: filesystem listings, FolderDates parse.
2. `PopupWindowVerifier` (LEVEL 2, check `attachments_popup`): popup title captured via pywinauto must contain the expected patient name (normalized) → else `PATIENT_MISMATCH` FAIL **before any Attach click**; missing popup → `ATTACHMENT_WINDOW_NOT_FOUND` FAIL. Evidence: window title + rect.
3. `DocumentMappingVerifier` (LEVEL 1.5, check `attachments_mapping`): wraps `match_files_to_lines` output — all matched 1:1 → PASS with per-file mapping evidence; any `ambiguous` → **REVIEW** (`DOCUMENT_AMBIGUOUS`, never PASS, UPLOAD blocked); any `not_found` → FAIL (`DOCUMENT_NOT_MATCHED`) or REVIEW when scrolled-out cases remain (policy: not_found = FAIL; scroll-exhaustion = FAIL `CANNOT_SCROLL`). (Keeps existing ABORT semantics; now structured.)
4. `UploadActionVerifier` (LEVEL 2, check `attachments_upload`): evidence that Upload was clicked (action log) + confirmation dialog detected and Enter processed (`CONFIRMATION_NOT_DETECTED` → REVIEW + MANUAL_ONLY when absent but popup state unclear) + no NEW unexpected #32770 error dialog (`UNEXPECTED_DIALOG` → FAIL).
5. `PopupClosedVerifier` (LEVEL 3, check `attachments_popup_closed`): popup gone within retries → PASS; still open → **FAIL `POPUP_NOT_CLOSED`** (fixes the current WARNING-then-continue bug). Evidence: window existence checks.
6. `FinalStateVerifier` (LEVEL 3, check `attachments_final_state`): main HBSys window focused again + no attachment/file dialogs left over; returns overall per-patient verdict. Level 4 (`attachments_persistence`) is **declared but not enabled** — documented limitation: HBSys persistence is not inspectable without unreliable OCR; PASS claims stay "UI FLOW VERIFIED" with `PERSISTENCE_UNCERTAIN` noted in REVIEW only when the confirmation dialog was genuinely undetectable.

### Upload idempotency / duplicate protection (task §24-25)
New operator helper `_await_upload_confirmation()` replacing the blind Enter:
1. After Upload click, poll up to 3s for: (a) NEW #32770 dialog (reuse `wait_for_file_dialog` pre-handle technique), or (b) attachment popup gone, or (c) neither.
2. (a) dialog → press Enter (OK) → proceed to Close. 
3. (b) popup gone without dialog → PASS (documented HBSys variant: upload commits directly) — Enter is skipped, avoiding stray keystrokes.
4. (c) neither → `_probe_grid_state()`: OCR grid for file-path lines (long path text = reliable OCR, unlike short cells). 0 file rows ⇒ grid cleared ⇒ upload already committed → just Close. ≥1 rows ⇒ still pre-upload state ⇒ retry Upload **once**. OCR indeterminate ⇒ **REVIEW + MANUAL_ONLY** — popup closed via Escape, patient marked REVIEW, never re-uploaded blindly.

### Result → batch policy (never guess, never stop the batch)
- FAIL → `state.mark_failed(patient, reason_code.value)`; loop `continue`s (existing `MAX_CONSECUTIVE_FAILURES=3` guardrail preserved — task: one patient failure must not kill batch).
- REVIEW → `state.mark_processed` + verification record REVIEW; GUI shows orange.
- PASS → mark_processed as today.

### State integration (`core/attachments_state.py`)
Add one field: `verifications: dict[str, dict] = field(default_factory=dict)` + `mark_verification(patient_name, result: VerificationResult)` (stores status/step/reason/message/evidence_count/timestamp). Load pattern already filters unknown keys → old JSONs load fine; new JSONs under old code drop the key safely. No second state system.

### Uploader integration (`core/claim_attachments_uploader.py`) — minimal, no rewrite
- Import the service; operator gains `self.last_verification: VerificationResult | None`.
- `find_attachment_popup()` — unchanged detection, but return/title now flows into `PopupWindowVerifier` (title-vs-patient check) at the loop level.
- `assign_doc_types_and_upload(folder_path)` — signature **unchanged (bool)** for GUI/CLI compat; internals: mapping ABORTs now produce structured results (same semantics), then `_await_upload_confirmation()`, then Close with **FAIL** (not WARNING) when popup won't close; sets `self.last_verification`. Dry-run: builds PASS mapping result from folder listing (no clicks).
- `close_attachment_popup()` — add `strict: bool = False` param (default preserves old lenient behavior for legacy callers); the attachments flow passes `strict=True` and surfaces FAIL.
- `run_attachments_loop` / GUI `_upload_worker`: after each patient, `state.mark_verification(...)`; GUI status text "DONE"→green / "REVIEW"→orange / "FAILED"→red (minimal change — the status setter already takes text+color).

### GUI integration (minimal, task §28)
- `gui/claim_attachments_tab.py`: log line `Verification: PASS/REVIEW/FAIL — {reason}`; tree status cell shows `REVIEW` orange for review results. No redesign, no new panels.

## 3. Files to create / modify

```
Created:
  core/verification/__init__.py
  core/verification/result.py
  core/verification/evidence.py
  core/verification/context.py
  core/verification/base.py
  core/verification/service.py
  core/verification/attachments.py
  core/verification/exceptions.py
  tests/test_verification_architecture.py
Modified (minimal):
  core/attachments_state.py          (verifications dict + mark_verification)
  core/claim_attachments_uploader.py (verification wiring, confirmation await, strict close, last_verification)
  gui/claim_attachments_tab.py       (status/log lines only)
  CHANGE_RULES.md, README.md         (same-session docs per project rule)
NOT modified:
  core/claim_attachments_doc_type.py (matcher untouched — wrapped only), 
  production engine, OCR pipeline, XML generator, Claims Checker, patient matching,
  Date Fill, Add Claims flow, verification_panel_service.py
```

## 4. Testing plan

`tests/test_verification_architecture.py` (unittest; per-file run convention — no `tests/__init__.py`):
- Result semantics: PASS/REVIEW/FAIL enum; uncertain never becomes PASS (mapper converts ambiguity→REVIEW); to_dict/from_dict roundtrip.
- Evidence: serialization; explainable output (review_summary has expected/observed/uncertain/recommended).
- InputsVerifier: missing folder→FAIL FOLDER_NOT_FOUND; missing file→FAIL FILE_NOT_FOUND; unreadable file→FAIL FILE_NOT_READABLE; all-good→PASS.
- PopupWindowVerifier: title contains patient→PASS; wrong name→FAIL PATIENT_MISMATCH; no popup→FAIL ATTACHMENT_WINDOW_NOT_FOUND.
- DocumentMappingVerifier: ambiguous→REVIEW (blocked upload); not_found→FAIL; full match→PASS with mapping evidence.
- Upload idempotency: `_await_upload_confirmation` decision table (dialog→Enter; popup-gone→skip; grid rows>0→one retry; indeterminate→REVIEW MANUAL_ONLY, NO second Upload) — tested via mocked probe.
- Retry policy: FAIL POPUP_NOT_CLOSED is strict; upload-uncertain policy is MANUAL_ONLY.
- State: mark_verification → save/load roundtrip; backward compat (old state file without verifications loads).
- Operator dry-run: `last_verification` set; bool return preserved.

Regression (must all pass before completion):
- `python core/claim_attachments_doc_type.py` (existing 5-crop live regression suite)
- `python tests/test_gui_verify_panel_removal.py`
- `py_compile` on all touched files; GUI import chain (`start_claims_gui` → main GUI → attachments tab → uploader)
- Live protocol (owner-run, documented as pending): `--live --confirm-each --limit 1` — expected log: popup title verified vs patient → mapping PASS → Upload → dialog detected → Enter → Close verified → `Verification: PASS`.

## 5. Acceptance criteria mapping (task §36)

- Stronger deterministic verification: title-based patient verify + structured mapping gate + confirmation await + strict popup close. ☑ plan
- No short-cell OCR restore: cell OCR remains deleted; confirmation uses dialog handles; grid probe only reads long path lines. ☑
- PASS/REVIEW/FAIL + structured evidence + reason codes: result.py/evidence.py. ☑
- Traceable: every result timestamped, evidence-listed, mirrored into `attachments_state.verifications` + activity log. ☑
- Unsafe duplicate uploads prevented: MANUAL_ONLY on upload uncertainty; single bounded retry only after grid-rows probe. ☑
- Ambiguity never PASS: mapping ambiguous → REVIEW, upload blocked. ☑
- Batch survives patient failure: existing continue + consecutive-failure cap retained. ☑
- Resume intact: state file unchanged in shape (additive field). ☑
- No AI/LLM, no workflow builder, no unrelated refactors. ☑

## 6. Known limitations (to state honestly at delivery)

1. **Persistence (LEVEL 4) is not verified** — HBSys gives no non-OCR way to inspect stored attachments; per spec §16/17 we do not invent one. Post-upload PASS means "UI flow verified" (upload action + confirmation + popup closed), with the claim types documented. A future optional reopen-probe can layer on `attachments_persistence` without API change.
2. Grid-state probe (`_probe_grid_state`) is OCR-based (long path lines — the reliable OCR class) and only used for retry decisions; indeterminate → REVIEW, never guessed.
3. Patient identity before the popup opens relies on the search highlight (pixel band); the definitive identity check is the popup title (enforced before any Attach click).
4. Live HBSys confirmation dialog texts (error wording) are not exhaustively catalogued; unexpected-dialog detection keys on NEW #32770 windows not matching the expected OK dialog, conservative → REVIEW/FAIL.
