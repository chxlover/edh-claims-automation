# PROJECT_RULES.md

# EDH CLAIMS AUTOMATION SYSTEM V2

Version: 2.0

Owner:
Melvin A. Calanda

Hospital:
Echague District Hospital

Province:
Isabela

------------------------------------------------------------
PROJECT OBJECTIVE
------------------------------------------------------------

This project automates PhilHealth Claims processing.

The goal is to reduce manual work while keeping human
verification only when required.

The processor must always prioritize correctness over speed.

------------------------------------------------------------
GENERAL PRINCIPLES
------------------------------------------------------------

1.

Never lose patient documents.

2.

Never overwrite original scans.

3.

Every generated output must be reproducible.

4.

Every patient must be traceable.

5.

Every manual correction must be recorded.

6.

Database lookups are READ ONLY.

7.

Existing production features must never be broken.

------------------------------------------------------------
DOCUMENT TYPES
------------------------------------------------------------

Supported documents

CSF

SOA1

SOA2

COE

MRF

PBC

CF2

DTR

ANR

Other document types may be added later.

------------------------------------------------------------
PATIENT GROUPING
------------------------------------------------------------

The processor groups PDFs into one patient.

CSF is the primary patient boundary.

Every group represents one patient.

No document may belong to two patients.

------------------------------------------------------------
PATIENT IDENTITY
------------------------------------------------------------

Priority

1.

Hospital Number from SOA1

2.

Patient Name from Database

3.

Patient Name from COE

4.

UNKNOWN_PATIENT

Database information always has higher priority than OCR.

------------------------------------------------------------
PATIENT FOLDER NAME
------------------------------------------------------------

Format

LASTNAME, FIRSTNAME MIDDLENAME

-

HPERCODE

-

ADMYYYYMMDD

-

DISYYYYMMDD

Example

DELA CRUZ, JUAN S

-

900000001234567

-

ADM20260115

-

DIS20260118

Folder names must always use verified patient information.

------------------------------------------------------------
DATABASE
------------------------------------------------------------

Hospital Database

MySQL

Purpose

Patient lookup

Admission lookup

Doctor lookup

Claim lookup

The automation system must never modify HBSys data.

------------------------------------------------------------
LOCAL DATABASE
------------------------------------------------------------

SQLite

Purpose

Review Queue

Logs

History

Resume Sessions

Settings

Learning

SQLite is only for local metadata.

------------------------------------------------------------
SINGLE PATIENT MODE
------------------------------------------------------------

Purpose

Development

Testing

Verification

Workflow

Select Single Patient

↓

Detect Hospital Number

↓

Display Patient Verification

↓

YES

↓

Continue

NO

↓

Allow Hospital Number Correction

↓

Search Again

↓

Continue

------------------------------------------------------------
MULTIPLE PATIENT MODE
------------------------------------------------------------

Purpose

Production

Workflow

Automatically process every patient.

Do not interrupt processing.

Patients with errors must be added to Review Queue.

------------------------------------------------------------
PATIENT VERIFICATION
------------------------------------------------------------

Verification must display

Hospital Number

Patient Name

Admission Date

Discharge Date

Detected Documents

User chooses

PROCESS

CORRECT

SKIP

CANCEL

------------------------------------------------------------
REVIEW QUEUE
------------------------------------------------------------

Only problematic patients appear here.

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

------------------------------------------------------------
OCR RULES
------------------------------------------------------------

OCR is allowed to extract

Hospital Number

Patient Name

Doctor Name

Dates

Document Type

OCR is never the final authority.

Database verification always wins.

------------------------------------------------------------
DATABASE MATCHING
------------------------------------------------------------

Priority

Hospital Number

↓

Patient Name

↓

Admission

↓

Discharge

↓

Doctor

If confidence is low

Send to Review Queue.

------------------------------------------------------------
SIGNATURE RULES
------------------------------------------------------------

Auto signing is allowed only on supported forms.

Never remove an existing signature.

Never overwrite signed PDFs without backup.

------------------------------------------------------------
PDF RULES
------------------------------------------------------------

Every output PDF

PDF/A

Read Only

Target size below configured limit

Original scan must always be backed up.

------------------------------------------------------------
OUTPUT RULES
------------------------------------------------------------

Generated files

CSF.pdf

SOA1.pdf

SOA2.pdf

CF2.pdf

COE.pdf

MRF.pdf

PBC.pdf

DTR.pdf

ANR.pdf

XML

Unknown Review

Claims Checker CSV

------------------------------------------------------------
LOGGING
------------------------------------------------------------

Every important action must be logged.

Examples

Patient Detected

Hospital Number Verified

Admission Selected

Review Queue Added

XML Generated

Patient Finished

------------------------------------------------------------
RESUME PROCESSING
------------------------------------------------------------

Processing must be resumable.

Power interruption must not require restarting
the entire batch.

Already completed patients must not be processed again.

------------------------------------------------------------
BACKUP RULES
------------------------------------------------------------

Original scans

Never modify.

Always copy before processing.

Generated files

May be recreated.

------------------------------------------------------------
ERROR HANDLING
------------------------------------------------------------

Never terminate the entire batch because of one patient.

Continue processing.

Problem patients go to Review Queue.

------------------------------------------------------------
DEVELOPMENT RULES
------------------------------------------------------------

Never duplicate logic.

Never place SQL inside GUI.

Never place GUI inside database layer.

Keep modules independent.

Every module must support standalone testing.

------------------------------------------------------------
PRODUCTION RULE
------------------------------------------------------------

No temporary fixes.

No hardcoded patient names.

No hardcoded Hospital Numbers.

No fake data.

Every feature must work for every patient.

------------------------------------------------------------
PROJECT PHILOSOPHY
------------------------------------------------------------

Automation should reduce manual work.

Automation should never reduce correctness.

Human review should occur only when necessary.

Every decision should be explainable.

Every patient should be traceable.

The system must remain maintainable,
modular,
and production-ready.

------------------------------------------------------------
CURRENT ROADMAP
------------------------------------------------------------

Completed

✔ Existing OCR Engine

✔ Existing Claims Processor

✔ Existing Claims Checker

✔ Existing XML Generator

✔ Existing Auto Signing

✔ SQLite Foundation

Current Development

Patient Review Queue

Next

Resume Processing

Next

Verification GUI

Next

Dashboard

Next

AI Learning

------------------------------------------------------------
END OF PROJECT RULES
------------------------------------------------------------