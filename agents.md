# AGENTS.md

# EDH Claims Automation System v2

## Project Owner

Melvin A. Calanda

Hospital:
Echague District Hospital

Language:
Python 3.14+

GUI:
Tkinter (existing)
Future: PySide6 (optional)

Database:
MySQL (Hospital Database)
SQLite (Local Metadata / Review Queue)

---

# PROJECT GOAL

Build a production-grade PhilHealth Claims Automation System.

The system shall:

- Automatically process scanned claim documents.
- Detect patient boundaries.
- Extract Hospital Number.
- Validate patient information.
- Generate claim outputs.
- Require human intervention ONLY when necessary.

The existing processor is already working.

DO NOT rewrite it.

Improve it using modular architecture.

---

# IMPORTANT RULE

DO NOT redesign the entire processor.

DO NOT replace existing OCR.

DO NOT replace existing signing engine.

DO NOT replace existing XML generator.

DO NOT replace Claims Checker.

Everything new must be implemented as independent modules.

---

# CURRENT PROCESSOR

Main Engine

bot_unknown_trainer_DEFERRED_OUTPUT_REVIEW_PATSUFFIX_ADM_DIS.py

This file is the production engine.

Only minimal modifications are allowed.

---

# ARCHITECTURE

                GUI
                 │
                 ▼
        Processing Engine
                 │
    ┌────────────┴────────────┐
    │                         │
 OCR Engine              Patient Engine
    │                         │
    ▼                         ▼
 OCR Reader           Patient Validator
    │                         │
    ▼                         ▼
 Group Builder         Review Queue
    │                         │
    └────────────┬────────────┘
                 ▼
          Claims Engine
                 │
                 ▼
          XML Generator
                 │
                 ▼
          Claims Checker

---

# CORE MODULES

Location

core/

Modules

claims_database.py

activity_logger.py

patient_context.py

patient_validator.py

patient_review_queue.py

resume_manager.py

history_manager.py

Future modules may be added.

---

# DATABASE

SQLite

Purpose

Only local metadata.

Never replace HBSys MySQL.

Tables

review_queue

processing_logs

patient_history

settings

batch_sessions

ocr_learning

---

# MYSQL

Read-only.

Never modify HBSys.

Purpose

Patient lookup

Admission lookup

Doctor lookup

Claim lookup

---

# PROCESSING FLOW

START

↓

Read Input Folder

↓

Split Patient Groups

↓

Identify Patient

↓

Hospital Number

↓

Patient Validator

↓

PASS

↓

Claims Processing

↓

Output

OR

↓

Review Queue

↓

Manual Fix

↓

Resume Processing

↓

END

---

# SINGLE PATIENT MODE

Purpose

Development

Testing

Workflow

Select Single Patient

↓

Hospital Number

↓

Patient Verification

↓

YES

↓

Continue

NO

↓

Correct Hospital Number

↓

Search Again

↓

Continue

---

# MULTIPLE PATIENT MODE

Purpose

Production

Workflow

Automatically process every patient.

No confirmation.

Only failed patients are sent to Review Queue.

---

# REVIEW QUEUE

Reasons

H001

Hospital Number Not Found

H002

OCR Failed

H003

Multiple Admissions

H004

Patient Name Mismatch

H005

Missing SOA1

H006

Missing COE

H007

Admission Not Selected

H008

Manual Override

H009

Duplicate Hospital Number

H010

Duplicate Encounter

---

# DEVELOPMENT RULES

Every new feature must be modular.

Never duplicate code.

Database functions must never display GUI.

GUI must never perform SQL.

Validation logic belongs only inside Patient Validator.

Review Queue must never perform OCR.

---

# CODING STYLE

Python

PEP8

Type hints preferred

Small functions

Single responsibility

No spaghetti code.

No global logic inside utility modules.

---

# TESTING RULE

Every module must support:

if __name__ == "__main__":

for standalone testing.

Integration happens only after module testing.

---

# LOGGING

Use Activity Logger.

Avoid print() in new modules.

Supported levels

INFO

SUCCESS

WARNING

ERROR

DEBUG

---

# FUTURE FEATURES

Resume Processing

Dashboard

Statistics

OCR Learning

Patient Learning

Review Queue GUI

Batch History

AI Suggestions

---

# DO NOT CHANGE

Working OCR

Working PDF Merge

Working Auto Sign

Working XML Generator

Working Claims Checker

Existing processing flow

Only extend functionality.

---

# DEVELOPMENT PHILOSOPHY

Stability first.

Backward compatibility.

Production-ready code only.

No prototypes.

No temporary fixes.

Every feature must be reusable.

Every module must be independently testable.

---

# CURRENT STATUS

✔ Single Patient Mode

✔ Multiple Patient Mode

✔ Patient Confirmation

✔ SQLite Foundation

✔ Production Processor

Next milestone

Patient Review Queue

After Review Queue

Resume Processing

After Resume

GUI Verification Panel

---

# LIVING CHANGE DOCUMENTATION

Read `CHANGE_RULES.md` before modifying project code or configuration.

Every implementation, bug fix, behavior change, configuration change, database-schema
change, or user-visible GUI change must update the Change Record in `CHANGE_RULES.md` in
the same work session.

A change is not complete until its affected files, behavior, safety notes, and verification
results are documented there.
