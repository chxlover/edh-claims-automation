# EDH Claims Automation System

**PhilHealth Claims Automation System — Echague District Hospital (Isabela)**

| | |
|---|---|
| **Owner** | Melvin A. Calanda |
| **Hospital** | Echague District Hospital, Isabela |
| **Language** | Python 3.14+ |
| **GUI** | Tkinter (PySide6 optional in the future) |
| **Databases** | MySQL (HBSys, **read-only**) · SQLite (local metadata) |
| **Status** | Production — Single/Multiple Patient Mode ✔ |

---

## Table of Contents

1. [What This System Does](#what-this-system-does)
2. [Golden Rules](#golden-rules)
3. [Architecture](#architecture)
4. [Technology Stack](#technology-stack)
5. [Project Layout](#project-layout)
6. [Document Types](#document-types)
7. [Processing Flow](#processing-flow)
8. [Patient Identity & Folder Naming](#patient-identity--folder-naming)
9. [Operation Modes](#operation-modes)
10. [Review Queue](#review-queue)
11. [Generated Outputs](#generated-outputs)
12. [Database Policy](#database-policy)
13. [Core Modules](#core-modules)
14. [HBSys Automation](#hbsys-automation)
15. [Claims Agent Roadmap](#claims-agent-roadmap)
16. [GUI Overview](#gui-overview)
17. [Setup & Installation](#setup--installation)
18. [Updates (NAS)](#updates-nas)
19. [Testing](#testing)
20. [Development Rules](#development-rules)
21. [Change Documentation](#change-documentation)
22. [Roadmap & Current Status](#roadmap--current-status)
23. [Quick Command Reference](#quick-command-reference)

---

## What This System Does

Automatically processes scanned PhilHealth claim documents:

1. Detects **patient boundaries** in scanned batches.
2. Extracts the **Hospital Number** (OCR + database verification).
3. **Validates patient information** against HBSys (read-only).
4. Runs the claims pipeline: required documents → dates → **CF4 / CF5 / eSOA XML** → Fees Checker → Claims Checker.
5. Sends only problematic patients to the **Review Queue** for human intervention.

> **Goal:** Automation reduces manual work, but never reduces correctness.
> Human review happens only when necessary.

---

## Golden Rules

| # | Rule |
|---|------|
| 1 | **Never lose patient documents.** |
| 2 | **Never overwrite original scans.** Always back up before processing. |
| 3 | Every generated output must be **reproducible**. |
| 4 | Every patient must be **traceable**. |
| 5 | Every manual correction must be **recorded**. |
| 6 | Database lookups are **READ ONLY** (HBSys/MySQL must never be modified). |
| 7 | **Do not rewrite** the working processor, OCR, signing engine, XML generators, or Claims Checker — only *extend* with independent modules. |
| 8 | Ambiguous patient/document/confinement matches are **never guessed** — send to review. |
| 9 | One patient failing must **never terminate the batch**. |
| 10 | Processing must be **resumable** (power loss ≠ full restart). |
| 11 | OCR is never the final authority — **database verification wins**. |
| 12 | No temporary fixes, no hardcoded names/hospital numbers, no fake data. |

---

## Architecture

```
                         GUI (Tkinter)
                              │
                              ▼
                   Processing Engine
                   (production processor)
                              │
              ┌───────────────┴───────────────┐
              │                               │
        OCR Engine                      Patient Engine
              │                               │
              ▼                               ▼
        OCR Reader                    Patient Validator
              │                               │
              ▼                               ▼
        Group Builder                  Review Queue
              │                               │
              └───────────────┬───────────────┘
                              ▼
                       Claims Engine
                              │
                              ▼
                        XML Generator
                       (CF4 · CF5 · eSOA)
                              │
                              ▼
                        Claims Checker
```

Future target (per [CLAIMS_AGENT_PLAN.md](CLAIMS_AGENT_PLAN.md)) — a deterministic **Claims Agent orchestrator**:

```
GUI → CLAIMS AGENT / ORCHESTRATOR
        ├── Patient Engine
        ├── Document Engine
        ├── Claims Engine
        ├── HBSys State Controller
        ├── Recovery Engine
        ├── Verification Engine
        ├── Review Queue
        └── Resume Manager
```

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.14+ |
| GUI | Tkinter (`edh_claims_gui_XML_COPY_BUTTON.py`) |
| PDF | PyMuPDF, PyPDF2, pdf2image, reportlab, Pillow, Ghostscript (PDF/A) |
| OCR | Tesseract (`pytesseract`) + OpenCV |
| HBSys DB (read-only) | PyMySQL / mysql-connector-python |
| Local DB | SQLite (`claims.db`) |
| UI Automation | pywinauto, PyAutoGUI |
| Fuzzy matching | rapidfuzz |
| Reports | openpyxl (XLSX) |
| Notifications | python-telegram-bot (optional) |

---

## Project Layout

| Path | Purpose |
|---|---|
| `bot_unknown_trainer_DEFERRED_OUTPUT_REVIEW_PATSUFFIX_ADM_DIS.py` | **Production processor** (minimal modifications only) |
| `edh_claims_gui_XML_COPY_BUTTON.py` | Main GUI |
| `core/` | Modular services (DB, validation, review queue, resume, XML auto-copy, PDF compressor, visual learning…) |
| `date_fill_hbsys/` | HBSys Date Fill automation + verifier + window detection |
| `gui/` | Reusable Tkinter widgets (PDF Splitter, PDF Compressor tab, Visual Learning Manager) |
| `webscanner/` | CamScanner-like phone-browser scanner (Flask) |
| `tests/` | Unit tests (unittest) |
| `scans/` | Input scans (never modified) |
| `output/` | Per-patient output folders (source of truth for Date Fill) |
| `backup_originals/` | Backups of original scans |
| `review_staging/` | Staging for review queue patients |
| `claims_checker_results/` | Claims Checker results (`READY`, `INCOMPLETE`, …) |
| `signatures/` | Signature images for auto-signing |
| `merge_input/` `merge_output/` `pdfs/` | PDF merge workflow |
| `logs/` | Activity logs + Date Fill run logs (CSV) |
| `reports/` | Generated reports |

---

## Document Types

| Code | Document |
|---|---|
| CSF | Claim Signature Form — **primary patient boundary** |
| SOA1 | Statement of Account 1 — **primary source of Hospital Number** |
| SOA2 | Statement of Account 2 |
| COE | Certificate of Employment |
| MRF | Medical Referral Form |
| PBC | Philippine Birth Certificate |
| CF2 | Claim Form 2 |
| DTR | Daily Time Record |
| ANR | Anesthesia Record |
| OPR | Operating Room Record / **Delivery Room Record** |
| MMC | Marriage Certificate (Municipal Form No. 97) |

Other types may be added later. Every group = one patient; no document belongs to two patients.

---

## Processing Flow

```
START
  │
  ▼
Read Input Folder ──► Split Patient Groups ──► Identify Patient
                                                  │
                                                  ▼
                                          Hospital Number (OCR → DB verify)
                                                  │
                                                  ▼
                                           Patient Validator
                                          ┌──────────┴──────────┐
                                          │                     │
                                         PASS                 FAIL
                                          │                     │
                                          ▼                     ▼
                                   Claims Processing      Review Queue
                                          │                     │
                                          ▼                     ▼
                                        Output              Manual Fix
                                                                │
                                                                ▼
                                                          Resume Processing
                                                                │
                                                                ▼
                                                               END
```

Per-patient pipeline (full claim lifecycle):

```
RECEIVED → GROUPED → IDENTIFIED → VERIFIED → DOCUMENT_CHECK
  → CLAIM_PROCESSING → DATE_FILL → CF4_XML → CF5_XML → ESOA_XML
  → FEES_CHECK → CLAIMS_CHECK → FINAL_VERIFICATION → READY_TO_TRANSMIT
```

---

## Patient Identity & Folder Naming

**Identity priority:** Hospital Number (SOA1) → Patient Name (Database) → Patient Name (COE) → `UNKNOWN_PATIENT`

**Folder name format** (always uses *verified* information):

```
LASTNAME, FIRSTNAME MIDDLENAME - HPERCODE - ADMYYYYMMDD - DISYYYYMMDD
```

Example:

```
DELA CRUZ, JUAN S - 900000001234567 - ADM20260115 - DIS20260118
```

---

## Operation Modes

### Single Patient Mode
For development / testing / verification:
```
Select Single Patient → Detect Hospital Number → Patient Verification
  → YES → Continue
  → NO  → Correct Hospital Number → Search Again → Continue
```

### Multiple Patient Mode
For production:
- Automatically processes **every** patient, no per-patient confirmation.
- Only failed patients are sent to the Review Queue.

---

## Review Queue

| Code | Reason |
|---|---|
| H001 | Hospital Number Not Found |
| H002 | OCR Failed |
| H003 | Multiple Admissions |
| H004 | Patient Name Mismatch |
| H005 | Missing SOA1 |
| H006 | Missing COE |
| H007 | Admission Not Selected |
| H008 | Manual Override |
| H009 | Duplicate Hospital Number |
| H010 | Duplicate Encounter |

**Two review flavors (per the Claims Agent plan):**

- **PATIENT REVIEW** — patient identified but cannot be safely completed (multiple admissions, mismatch, duplicate encounter, recovery failed…). *User decides.*
- **UNKNOWN REVIEW** — document cannot be reliably identified/classified (unknown type, OCR failure, ambiguous layout). Uses the existing `unknown_review_manager` mechanism.

---

## Generated Outputs

- Per-patient PDFs: `CSF.pdf`, `SOA1.pdf`, `SOA2.pdf`, `CF2.pdf`, `COE.pdf`, `MRF.pdf`, `PBC.pdf`, `DTR.pdf`, `ANR.pdf`
- XML: **CF4**, **CF5**, **eSOA** (all three mandatory per claim)
- Unknown Review outputs
- Claims Checker CSV/Excel reports
- Fees Checker CSV/Excel reports

**PDF rules:** outputs are PDF/A, read-only, under the configured size limit; originals are always backed up.

---

## Database Policy

| Database | Role | Access |
|---|---|---|
| **MySQL (HBSys)** | Patient / admission / doctor / claim lookup | **READ ONLY — never modify** |
| **SQLite (`claims.db`)** | Local metadata: review queue, processing logs, patient history, settings, batch sessions, OCR learning | Read/Write (local only) |

**Key constraints:**
- Database functions must never display GUI.
- GUI must never perform SQL directly.
- Validation logic lives only inside the Patient Validator.
- Review Queue must never perform OCR.

---

## Core Modules

| Module | Responsibility |
|---|---|
| `claims_database.py` | SQLite local metadata access |
| `hbsys_connection.py` | Read-only HBSys/MySQL connection factory |
| `hospital_patient_lookup.py` / `hospital_number_resolver.py` / `hospital_number_extractor.py` | Patient lookup + Hospital Number handling |
| `patient_validator.py` | All validation logic |
| `patient_review_queue.py` | Review queue storage/management |
| `patient_identity_resolver.py` / `admission_lookup.py` / `admission_date_resolver.py` | Identity & admission resolution |
| `resume_manager.py` / `batch_tracker.py` | Resumable batch processing |
| `activity_logger.py` | Structured logging (INFO/SUCCESS/WARNING/ERROR/DEBUG) |
| `patient_folder_naming.py` | Verified folder naming |
| `soa2_resolver.py` / `document_type_decisions.py` / `claims_requirement_rules.py` | Document classification & requirements |
| `xml_auto_copy.py` | Background auto-copy of stable XML files |
| `pdf_compressor.py` | Safe PDF/A compression |
| `attachments_state.py` / `claim_attachments_uploader.py` | Claim Attachments automation (HBSys attach workflow, resumable batch state) |
| `claim_attachments_doc_type.py` | Doc Type assignment for attached files (3-pass OCR: normal + inverted + blue-band; folder-driven matching; never-guess ABORT policy) |
| `visual_document_learner.py` | Privacy-preserving visual layout learning (shadow mode) |
| `verification_panel_service.py` / `patient_correction_service.py` / `review_staging.py` | Verification & correction support |
| `backup_auditor.py` / `not_transmitted_batches.py` | Audit & batch completeness |

---

## HBSys Automation

Located in `date_fill_hbsys/`:

| File | Purpose |
|---|---|
| `hbsys_window.py` | Shared HBSys window detection (**excludes browser/Chrome windows**) |
| `hbsys_fill_dates.py` | Production Date Fill (PHIC Claim Form 2: Date Signed, Consent, Auth Sign) |
| `hbsys_fill_dates_testing.py` | Tested/safer Date Fill variant (used by the GUI "Date Fill ABTC/Regular") |
| `hbsys_date_fill_verifier.py` + `test_hbsys_date_fill_verifier.py` | Post-save verification (read-only) |
| `hbsys_ready_claims.py` | Lists claims from `output/` ready for Date Fill |
| `hbsys_read_admission_history.py` | Reads the open Admission History popup |
| `hbsys_find_zero_dates.py` | Finds `00-00-0000` date fields |
| `hbsys_inspect.py` | UI inspector (which elements are exposed vs pixel-painted) |
| `xml_generator_clicker.py` | CF4/CF5/eSOA XML clicker automation |
| `hbsys_rules.py` | Rule constants for row selection (ADMIT preference, date matching…) |

**Critical HBSys requirements:** HBSys open & maximized · 1920×1080 at 100% scaling · do not touch mouse/keyboard during live automation · start on the Billing screen with Hospital No. input visible.

**Date Fill behavior:** for each output folder → enter hospital number → Admit History → select matching confinement (ADMIT row preferred) → PHIC Beneficiaries → Claim Form 2 → Professional Fees **first row** → Consent → fill dates with **discharge date** → save → verify via read-only DB check.

---

## Claim Attachments Upload

GUI tab "Claim Attachments" (or CLI: `python -m core.claim_attachments_uploader [--live] [--confirm-each] [--limit N] [--resume]`).

Per patient in `claims_checker_results/READY/`: search patient → click "attach..." on the highlighted row → attach all folder files (PDFs first) → attach XMLs (Files of type: XML) → **assign Doc Type per grid row** → Upload → OK (Enter) → Close → next patient.

**Doc Type step (v5.2 — live tested):** for patients with more files than visible rows, the grid is processed as a **view loop**: each visible view is OCR'd (4 passes: normal, colour-inverted, blue-row targeted, per-row-band), matched 1:1 to unprocessed patient-folder files via three tiers (strict stem+extension, loose stem, 1-edit mutated stem), typed row-by-row, then the grid is scrolled and re-matched (already-processed rows reappearing in scroll overlap are skipped by text identity). When the last file is typed (e.g. eSOA/ESA), **Upload → Enter (OK) → Close** runs immediately — no post-type verification (the v5.1 cell-OCR verification was removed after live runs showed short values like DTR/CF4 are unreliable to OCR-read and blocked correct uploads; the duplicate-row guard remains in the per-view 1:1 matching during typing). Mapping: `\COE.pdf`→COE, `\CSF.pdf`→CSF, `\DTR.pdf`→DTR, `\SOA1.pdf`/`\SOA2.pdf`→SOA, `\MRF.pdf`→MRF, `\PBC.pdf`→PBC, `\MMC.pdf`→MMC, `\OPR.pdf`→OPR, `\ANR.pdf`→ANR, `\CF3.pdf`→CF3, `\CF2.pdf`→CF2, `_CF4.xml`→CF4, `_CF5.xml`→CF5, `_eSOA.xml`→ESA.

**Never-guess policy:** any unmatched/ambiguous/duplicate row, doc-cell verification mismatch, or un-scrollable remaining files → ABORT before Upload (patient marked FAILED, nothing wrong is typed into HBSys). A `logs/debug_doc_grid_*.png` screenshot is saved every view for diagnosis; the standalone test suite replays the latest debug crops as regression cases.

---

## Claims Agent Roadmap

Per `CLAIMS_AGENT_PLAN.md` — transform the system into a **rule-based autonomous claims agent** (deterministic orchestrator, **NOT AI/LLM**):

```
DETECT → DECIDE BY RULES → NAVIGATE → ACT → VERIFY → RECOVER → CONTINUE → FINALIZE
```

**Development order (phases):**

| Phase | Deliverable |
|---|---|
| 1 | `core/agent/hbsys_state_controller.py` + `hbsys_states.py` — HBSys navigation by **state** (HOSPITAL_NUMBER_ENTRY, CF4_XML, CF5_XML, ESOA_XML), verify every transition |
| 2 | Wrap existing Date Fill & XML tools as callable Agent tools |
| 3 | Mandatory **CF4 → CF5 → eSOA** pipeline for every claim |
| 4 | Verification after every major action |
| 5 | Deterministic recovery rules (e.g. Date Fill recovery for missing dates) |
| 6 | Claims Agent orchestration |
| 7 | Persistent Agent state / resume |
| 8 | Connect Patient Review + Unknown Review |
| 9 | Final dashboard / output summary |

**Final user-facing statuses:** 🟢 READY TO TRANSMIT · 🟡 INCOMPLETE · 🔴 PATIENT REVIEW · 🟣 UNKNOWN REVIEW

---

## GUI Overview

Main file: `edh_claims_gui_XML_COPY_BUTTON.py` (start via `Claim.bat` or `start_claims_gui.py`).

- **Dashboard / Quick Actions:** PDF E-Sign, Date Fill ABTC/Regular, XML Clicker, Copy XML, Check Missing, Fees Check, Recheck INC, PDF Compress, PDF Split
- **HBSys status indicator** in the header (green OPEN / red CLOSED, auto-refresh 3s) — Date Fill & XML Clicker are **disabled while HBSys is closed**
- **Notebook tabs:** Auto Process, Claims Processor, Review Queue, Missing Date Fill Batches, PDF Compress, PDF Split, Preferences, About
- **Preferences:** output folder, XML folder, HBSys source, Enable Auto Copy XML, etc.

---

## Setup & Installation

### New PC (one-click)

1. Copy the app to `C:\claims_bot`.
2. Connect to the hospital network.
3. Run `INSTALL_ON_NEW_PC.bat` → wait for `[VERIFICATION PASSED]`.
4. Start with `Claim.bat`.

The installer creates an isolated `.venv`, installs `requirements.txt`, and sets up: Microsoft Visual C++ Runtime, Tesseract OCR, Ghostscript, Poppler (via `winget` when available).

### Verify installation (non-destructive)

```powershell
C:\claims_bot\.venv\Scripts\python.exe C:\claims_bot\verify_installation.py --check-db
```

Only executes `SELECT 1 AS ok` against HBSys.

### Operational requirements

- 64-bit Python 3.14+, Windows with `winget` recommended
- HBSys client installed + hospital network access
- 1920×1080 @ 100% scaling, HBSys maximized during automation
- GUI folder paths configured in Preferences; signatures in `C:\claims_bot\signatures`
- Never copy real patient PDFs merely to test installation

---

## Updates (NAS)

- Update source configured in `update_config.json` (e.g. `\\192.168.1.193\Echague District Hospital Files\melvin\AIBOT(BACKUP)\claims_bot`).
- Manifest-based (`manifest.json`): **code + docs only** — patient data folders (`scans`, `output`, `backup_originals`, `review_staging`, `claims_checker_results`, `merge_*`, `pdfs`, `signatures`, `.venv`, `logs`, SQLite DBs, reports) are excluded and never overwritten.
- **Publish** (main PC): `python setup_nas_folders.py` → `python publish_update.py`
- **Apply** (other PCs): GUI → About → Check for Updates → Update Now → restart

---

## Testing

Project convention: run each test file directly or with `python -m unittest <module>` (no `tests/__init__.py`, so `discover` does not work).

```powershell
# Examples
python -m unittest tests.test_hbsys_window tests.test_gui_hbsys_status
python -m unittest test_hbsys_date_fill_verifier        # run from date_fill_hbsys/
python tests/test_gui_verify_panel_removal.py
python -m unittest tests.test_xml_auto_copy tests.test_pdf_compressor
```

Every module must support `if __name__ == "__main__":` for standalone testing. Integration happens only after module testing.

---

## Development Rules

- **Modularity:** every new feature is an independent module; never duplicate code; only minimal integrations in production files.
- **Coding style:** PEP 8, type hints preferred, small single-responsibility functions, no spaghetti code.
- **Logging:** use `core.activity_logger` (INFO / SUCCESS / WARNING / ERROR / DEBUG); avoid `print()` in new modules.
- **Stability:** production-ready code only — no prototypes, no temporary fixes; backward compatible.
- **Safety:** background services must not control mouse/keyboard unless designed as UI automation; must not freeze Tkinter's main thread.

---

## Change Documentation

**Every change** (implementation, bug fix, behavior/config change, schema change, GUI change) **must update `CHANGE_RULES.md` in the same work session**, with: date, title, reason, files, before/after behavior, safety notes, and verification results.

A change is not complete until it is documented there.

---

## Roadmap & Current Status

**Completed**

- ✔ Single Patient Mode · Multiple Patient Mode · Patient Confirmation
- ✔ Production Processor · OCR · PDF merge · Auto Signing · XML generators · Claims Checker
- ✔ SQLite foundation · Patient Review Queue · Resume Processing
- ✔ Date Fill (ABTC/Regular) + verifier · HBSys window detection · XML Auto Copy
- ✔ Fees Checker · PDF Compressor · PDF Splitter · Visual Document Learning · WebScanner

**Next milestones**

1. **Claims Agent — Phase 1:** `core/agent/hbsys_state_controller.py` + `hbsys_states.py`
2. Mandatory CF4 → CF5 → eSOA pipeline as Agent steps
3. Verification & deterministic recovery
4. Final dashboard (READY / INCOMPLETE / PATIENT REVIEW / UNKNOWN REVIEW)

---

## Quick Command Reference

```powershell
# Start the app
C:\claims_bot\Claim.bat
# or
python start_claims_gui.py

# Install on a new PC
INSTALL_ON_NEW_PC.bat

# Verify installation (read-only DB test)
.venv\Scripts\python.exe verify_installation.py --check-db

# Date Fill
cd date_fill_hbsys
python hbsys_ready_claims.py                          # list ready claims
python hbsys_fill_dates.py --limit 1                  # dry-run preview
python hbsys_fill_dates.py --live --limit 1 --confirm-each   # first live test
python hbsys_fill_dates.py --live                     # full live run

# Fees Checker / PDF compression / etc.
python fees_checker.py
python -m py_compile fees_checker.py                  # syntax check

# Publish an update (main PC)
python setup_nas_folders.py
python publish_update.py
```

---

*Maintained per the Living Change Documentation rule — see `CHANGE_RULES.md` for the full change record.*
