# CLAIMS AGENT — DEVELOPMENT HANDOFF

## 1. PROJECT CONTEXT

This is the Echague District Hospital (EDH) PhilHealth Claims Automation System.

The existing system already has working components for:

- OCR
- Patient/document grouping
- Patient identification
- Claims processing
- Date Fill
- CF4 XML generation
- CF5 XML generation
- eSOA XML generation
- XML copying
- Fees Checker
- Claims Checker
- Auto signing
- Unknown Review
- Patient Review Queue
- SQLite metadata/history
- MySQL read-only hospital database access

IMPORTANT:

DO NOT rewrite the existing production processor.

DO NOT replace the existing OCR engine.

DO NOT replace the existing signing engine.

DO NOT replace the existing XML generators.

DO NOT replace the existing Claims Checker.

New functionality must be implemented as independent modules around the existing working system.

The existing PROJECT_RULES.md and AGENTS.md remain authoritative.

---

# 2. MAIN GOAL

Transform the existing claims automation system into a:

# RULE-BASED AUTONOMOUS CLAIMS AGENT

This is NOT an AI system.

Do NOT use:

- LLM
- Ollama
- OpenAI API
- AI decision making
- machine-learning decision making

The Agent is a deterministic orchestrator.

Its job is:

DETECT → DECIDE BY RULES → NAVIGATE → ACT → VERIFY → RECOVER → CONTINUE → FINALIZE

The user should only need to:

1. Scan the claim documents.
2. Start the process.
3. Wait for the Agent.
4. Review the final queues.

The user should NOT manually operate every HBSys step.

---

# 3. DESIRED USER EXPERIENCE

User workflow:

SCAN
↓
START PROCESS
↓
Agent processes all patients automatically
↓
Agent navigates HBSys as needed
↓
Agent executes existing tools
↓
Agent verifies every important action
↓
Agent attempts safe recovery when possible
↓
Agent continues processing
↓
Final dashboard

The user primarily wants to see:

GREEN:
READY TO TRANSMIT

YELLOW:
INCOMPLETE

RED:
PATIENT REVIEW

PURPLE:
UNKNOWN REVIEW

---

# 4. IMPORTANT HBSys REQUIREMENT

Some existing automation tools require HBSys to be on a specific screen/tab.

Examples:

DATE FILL:
- Requires the HBSys screen/tab where the Hospital Number can be entered.

CF4 XML:
- Requires the appropriate CF4 XML screen/tab.

CF5 XML:
- Requires the appropriate CF5 XML screen/tab.

eSOA XML:
- Requires the appropriate eSOA XML screen/tab.

The user is willing to keep HBSys open and let the Agent navigate/click whatever screen is necessary.

Therefore:

DO NOT assume HBSys starts on a specific tab.

The Agent must be able to determine the required HBSys state and navigate to it.

---

# 5. HBSys STATE CONTROLLER

This is the FIRST development milestone.

Create an independent module:

core/agent/hbsys_state_controller.py

The purpose is to abstract HBSys navigation away from the individual tools.

The Agent should think in terms of STATES, not raw coordinates.

Initial target states:

HOSPITAL_NUMBER_ENTRY
CF4_XML
CF5_XML
ESOA_XML

Later states may include:

ADMISSION_HISTORY
CF2
PHIC_BENEFICIARIES

The controller should provide concepts equivalent to:

detect_current_state()

go_to(state)

verify_state(state)

The exact implementation must use the existing HBSys automation/inspection mechanisms already present in the project.

Do not blindly click based on coordinates if a reliable UI element/state detection method is available.

Preferred order:

1. Direct UI automation / control detection
2. Existing inspection mechanisms
3. Coordinate automation only when necessary
4. OCR/pixel detection as fallback

Every navigation action should be verified.

Example:

Agent needs CF4 XML

→ State Controller navigates to CF4 XML
→ verify CF4 XML screen
→ only then execute CF4 XML generator

Never:

click → assume correct → continue

Instead:

ACTION
↓
VERIFY
↓
PASS → CONTINUE
FAIL → RECOVERY / REVIEW

---

# 6. CLAIMS AGENT ARCHITECTURE

The high-level architecture should become:

GUI
↓
CLAIMS AGENT / ORCHESTRATOR
↓
Patient Engine
Document Engine
Claims Engine
HBSys State Controller
Recovery Engine
Verification Engine
Review Queue
Resume Manager

The Agent should orchestrate existing components.

It should NOT duplicate their internal logic.

---

# 7. PROPOSED MODULES

Potential new modules:

core/
    agent/
        claims_agent.py
        agent_state.py
        agent_recovery.py
        agent_verifier.py
        hbsys_state_controller.py
        hbsys_states.py

Use the existing project's modular architecture.

Do not create unnecessary modules until their responsibilities are clear.

Each module should be independently testable.

---

# 8. PATIENT PROCESSING FLOW

For every patient:

1. Detect patient group.
2. Identify patient.
3. Verify Hospital Number.
4. Verify patient information.
5. Verify admission/confinement.
6. Check required documents.
7. Check required dates.
8. Attempt safe recovery for known problems.
9. Generate CF4 XML.
10. Generate CF5 XML.
11. Generate eSOA XML.
12. Verify all three XML outputs.
13. Run Fees Checker.
14. Run Claims Checker.
15. Perform final verification.
16. Determine final status.

The process must continue automatically without user confirmation during normal batch processing.

If one patient fails, do NOT terminate the entire batch.

Send the problematic patient to the appropriate review queue and continue processing the remaining patients.

---

# 9. MANDATORY XML PIPELINE

Every claim must go through:

CF4 XML
↓
CF5 XML
↓
eSOA XML

These are mandatory for every claim.

This is NOT an optional recovery step.

The Agent must make sure all three are generated and verified.

If any required XML fails:

Do not mark the claim READY TO TRANSMIT.

Attempt known safe recovery if one exists.

If recovery fails:

PATIENT REVIEW.

---

# 10. DATE FILL RECOVERY

One known recovery workflow is Date Fill.

Example:

Problem:

Date Signed is missing.

Agent should:

1. Detect missing date.
2. Determine that Date Fill is an available recovery.
3. Ask HBSys State Controller to navigate to HOSPITAL_NUMBER_ENTRY.
4. Verify that the correct screen is open.
5. Execute existing Date Fill functionality.
6. Verify the date was actually filled.
7. If successful, continue processing.
8. If unsuccessful, send the patient to PATIENT REVIEW.

Do not simply trust a tool's "success" message.

Always verify the actual result.

---

# 11. RECOVERY ENGINE

The Agent should support deterministic recovery rules.

General pattern:

PROBLEM
↓
CAN THIS BE SAFELY FIXED?
↓
YES
↓
RUN KNOWN RECOVERY
↓
VERIFY
↓
SUCCESS → CONTINUE
FAIL → REVIEW

NO
↓
REVIEW

Examples of known recoverable problems may include:

- Missing dates that can be obtained from verified Admission History
- Missing/generated files that have a deterministic generation process
- XML files that can be regenerated
- Known navigation/state problems

Do NOT invent recovery actions.

Only use explicitly defined and verified recovery procedures.

---

# 12. REVIEW TYPES

There must be a distinction between:

## PATIENT REVIEW

The patient is identified, but the system cannot safely complete processing.

Examples:

- Multiple admissions
- Conflicting patient information
- Hospital number mismatch
- Duplicate encounter
- Required information unavailable
- Recovery failed
- Ambiguous confinement

The user must make the decision.

## UNKNOWN REVIEW

The document itself cannot be reliably identified/classified.

Examples:

- Unknown document type
- OCR/classification failure
- Ambiguous document
- Unknown layout

Use the existing Unknown Review mechanism.

---

# 13. READY TO TRANSMIT

A claim may only become:

READY_TO_TRANSMIT

when all required checks pass.

Conceptually:

Patient verified
AND
Admission verified
AND
Required documents complete
AND
Required dates complete
AND
CF4 XML generated
AND
CF5 XML generated
AND
eSOA XML generated
AND
XML outputs verified
AND
Fees Checker passed
AND
Claims Checker passed
AND
Final verification passed
AND
No unresolved review condition

Only then:

READY_TO_TRANSMIT

Do not use "looks okay" logic.

---

# 14. AGENT STATE

Processing must be resumable.

Each patient should have a persistent processing state.

Example:

RECEIVED
↓
GROUPED
↓
IDENTIFIED
↓
VERIFIED
↓
DOCUMENT_CHECK
↓
CLAIM_PROCESSING
↓
DATE_FILL
↓
CF4_XML
↓
CF5_XML
↓
ESOA_XML
↓
FEES_CHECK
↓
CLAIMS_CHECK
↓
FINAL_VERIFICATION
↓
READY_TO_TRANSMIT

If a problem occurs:

CURRENT_STAGE
↓
RECOVERY
↓
VERIFY
↓
CONTINUE

If the application/PC crashes:

Resume from the last successfully verified state.

Do not reprocess already completed work unnecessarily.

---

# 15. AGENT ACTION LOG

Every important Agent action should be recorded.

Example:

Patient:
JUAN DELA CRUZ

Action:
DATE_FILL

Reason:
DATE_SIGNED_MISSING

HBSys State:
HOSPITAL_NUMBER_ENTRY

Result:
SUCCESS

Verification:
PASSED

Next:
CF4_XML

Another example:

Action:
CF5_XML

Result:
FAILED

Recovery:
ATTEMPTED

Recovery Result:
FAILED

Final:
PATIENT_REVIEW

The system must always be able to explain:

WHAT happened?
WHY did it happen?
WHAT did the Agent do?
DID it succeed?
WHAT was verified?
WHAT happened next?

---

# 16. BATCH PROCESSING

The Agent must support:

SCAN
↓
START
↓
PROCESS ALL PATIENTS

No confirmation for every patient.

If one patient fails:

CONTINUE NEXT PATIENT

Do not stop the entire batch.

At the end:

Show summary.

Example:

READY TO TRANSMIT: 127
INCOMPLETE: 4
PATIENT REVIEW: 7
UNKNOWN REVIEW: 2

---

# 17. INCOMPLETE STATUS

The desired behavior is:

INCOMPLETE
↓
Agent checks whether the problem has a known safe recovery
↓
If yes:
    attempt recovery
    verify
    continue if successful

If recovery is not possible:

send to appropriate review queue.

The objective is to minimize manual work.

The user should not manually repair problems that the deterministic Agent can safely repair.

---

# 18. SAFETY RULES

The Agent must:

- Never modify original scanned documents.
- Never lose patient documents.
- Never overwrite original scans.
- Keep database lookups read-only.
- Never guess when patient identity or admission is ambiguous.
- Never blindly click through unknown UI states.
- Verify important UI transitions.
- Verify important generated outputs.
- Record important actions.
- Continue other patients when one patient fails.
- Keep processing resumable.
- Preserve existing working functionality.

If the Agent encounters an unknown HBSys screen/state:

STOP CURRENT PATIENT
↓
PATIENT REVIEW

Do not blindly continue.

---

# 19. IMPORTANT DESIGN PRINCIPLE

The Agent is NOT a replacement for the existing system.

The Agent is the ORCHESTRATOR.

Existing tools are workers.

Example:

Agent
↓
"Need Date Fill"
↓
HBSys State Controller
↓
HOSPITAL_NUMBER_ENTRY
↓
Date Fill tool
↓
Verifier
↓
continue

Another:

Agent
↓
"Need CF4 XML"
↓
HBSys State Controller
↓
CF4_XML
↓
Existing CF4 XML generator
↓
Verifier
↓
continue

The Agent decides WHAT needs to happen.

Existing modules perform HOW it happens.

The State Controller handles WHERE HBSys needs to be.

The Verifier confirms WHETHER it succeeded.

---

# 20. DEVELOPMENT ORDER

DO NOT implement the entire Agent in one step.

Implement in this order:

PHASE 1
HBSys State Controller

Test:

1. Navigate to HOSPITAL_NUMBER_ENTRY
2. Verify state
3. Navigate to CF4_XML
4. Verify state
5. Navigate to CF5_XML
6. Verify state
7. Navigate to ESOA_XML
8. Verify state

PHASE 2
Wrap existing Date Fill and XML tools as callable Agent tools.

PHASE 3
Implement mandatory CF4 → CF5 → eSOA pipeline.

PHASE 4
Implement verification after every major action.

PHASE 5
Implement deterministic recovery rules.

PHASE 6
Implement Claims Agent orchestration.

PHASE 7
Implement persistent Agent state / resume.

PHASE 8
Connect Patient Review and Unknown Review.

PHASE 9
Implement final dashboard/output summary.

---

# 21. FIRST TASK FOR CODEX

DO NOT start by rewriting the existing processor.

DO NOT start by implementing the complete Agent.

FIRST:

Inspect the existing project and identify:

1. Current HBSys automation files.
2. Date Fill implementation.
3. CF4 XML implementation.
4. CF5 XML implementation.
5. eSOA XML implementation.
6. Existing UI inspection utilities.
7. Existing pywinauto/coordinate/OCR automation.
8. Existing Claims Checker.
9. Existing Review Queue.
10. Existing logging/state infrastructure.

Then report:

- Which file currently performs each task.
- What HBSys screen/tab each task requires.
- How that screen can be reliably detected.
- How navigation currently works.
- What can be reused.
- What new module should wrap it.

DO NOT modify production code during this inspection phase.

After inspection, propose the exact implementation of:

core/agent/hbsys_state_controller.py

and

core/agent/hbsys_states.py

Only implement after the existing code and UI behavior have been understood.

---

# 22. SUCCESS CRITERIA

The final system should eventually allow this workflow:

USER:
Scan documents.

USER:
Start Process.

AGENT:
Processes every patient automatically.

AGENT:
Navigates HBSys automatically.

AGENT:
Runs Date Fill when required.

AGENT:
Generates CF4 XML for every claim.

AGENT:
Generates CF5 XML for every claim.

AGENT:
Generates eSOA XML for every claim.

AGENT:
Runs existing checks.

AGENT:
Automatically fixes known safe problems.

AGENT:
Verifies every important action.

AGENT:
Continues processing even if one patient fails.

AGENT:
Sends unresolved cases to Patient Review or Unknown Review.

USER:
Only reviews the final queues.

FINAL USER OUTPUT:

READY TO TRANSMIT
INCOMPLETE
PATIENT REVIEW
UNKNOWN REVIEW

The goal is to minimize manual interaction while maintaining correctness, traceability, safety, and resumability.

END OF HANDOFF
