"""Fees Action Table (Slice B — Claims Agent, Phase 1 core).

Deterministic per-patient routing from a Fees Check row to the next
action: DATE FILL, FINAL BILL, XML CLICKER, or MANUAL REVIEW.

Single source of truth: fees_checker.ready_to_generate_xml() decides the
YES/NO gate. This module only maps the WHY (the problem list / Status)
to the WHAT (the action). No HBSys interaction, no clicks, no DB access —
pure function of one fees row dict. Slice F adds an OPTIONAL read-only
scan of the patient's output folder (only when output_root is passed) so
rows whose CF4+CF5+ESOA XMLs already exist are marked xml_complete and
can be dropped from the plan at plan time.

Action vocabulary (stable; the Agent Plan Panel + engine route on these):
    ACTION_DATE_FILL        — blank Prof Fee / Consent / Auth Sign Date
                              (user-confirmed: Auth Sign Date IS Consent,
                              both filled by the existing Date Fill tool)
    ACTION_FINAL_BILL       — NO FINAL BILL (itemized total zero/null) or
                              MISMATCH totals were also routed
                              here per the 2026-09-26 decision —
                              REVERSED 2026-10-07: the Final Bill
                              automation only touches bills that
                              were NEVER finalized. MISMATCH
                              totals are an operator decision,
                              so they queue for MANUAL_REVIEW.)
                              Runs the verified Final Bill flow (Slice D
                              final_bill_actions.py); the Slice E
                              orchestrator executes it.
    ACTION_XML_CLICKER      — Ready to Generate XML = YES (all gates pass)
    ACTION_MANUAL_REVIEW    — anything else (ADM/DIS mismatch,
                              NO RECORD, unreadable row, ...). Never guessed.

Priority (first match wins — deterministic, no scoring):
    1. Row unusable (unparseable / NO RECORD) .... MANUAL_REVIEW
    2. Status NO FINAL BILL ...................... FINAL_BILL
    3. Any signed date blank ..................... DATE_FILL
    4. Ready to Generate XML = YES ............... XML_CLICKER
    5. Anything else (incl. MISMATCH totals) ..... MANUAL_REVIEW
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from core import xml_output_checker as xml_check

# -- Action vocabulary --------------------------------------------------------

ACTION_DATE_FILL = "date_fill"
ACTION_FINAL_BILL = "final_bill"
ACTION_XML_CLICKER = "xml_clicker"
ACTION_MANUAL_REVIEW = "manual_review"

ALL_ACTIONS = (
    ACTION_DATE_FILL,
    ACTION_FINAL_BILL,
    ACTION_XML_CLICKER,
    ACTION_MANUAL_REVIEW,
)

# -- Tool availability --------------------------------------------------------
#
# FINAL BILL has a verified automation module since Slice D
# (core/agent/final_bill_actions.py) and is dispatched by the Slice E
# orchestrator. Flip a value to False ONLY if a tool is withdrawn:
# decide_action() then reports tool_available=False together with
# REVIEW_FINAL_BILL_TOOL_MISSING so callers queue the row for review
# instead of attempting it.

TOOL_AVAILABLE = {
    ACTION_DATE_FILL: True,     # date_fill_hbsys/hbsys_fill_dates.py
    ACTION_FINAL_BILL: True,    # core/agent/final_bill_actions.py (Slice D)
    ACTION_XML_CLICKER: True,   # date_fill_hbsys/xml_generator_clicker.py
    ACTION_MANUAL_REVIEW: True,  # review queue (no tool needed)
}

# Manual-review reason codes (stable; shown in the Plan Panel + reports).
REVIEW_NO_RECORD = "NO_RECORD"
REVIEW_MISMATCH = "MISMATCH"  # totals mismatch -> manual review (2026-10-07)
REVIEW_ADM_DIS_MISMATCH = "ADM_DIS_MISMATCH"
REVIEW_NOT_READY_OTHER = "NOT_READY_OTHER"
REVIEW_FINAL_BILL_TOOL_MISSING = "FINAL_BILL_TOOL_MISSING"
REVIEW_UNREADABLE_ROW = "UNREADABLE_ROW"

# Fees row keys read by the table (mirrors fees_checker.HEADERS).
KEY_STATUS = "Status"
KEY_ADM_MATCH = "ADM Match"
KEY_DIS_MATCH = "DIS Match"
KEY_PROF_DATE = "Prof Fee Sign Date (hprofserv.pdoctorsigndate)"
KEY_CONSENT_DATE = "Consent Date (hpatcon1.consentdate)"
KEY_AUTH_DATE = "Auth Sign Date (hpatcon1.authsigndate)"
KEY_READY = "Ready to Generate XML"
KEY_FOLDER = "Patient Folder"


@dataclass(frozen=True)
class ActionDecision:
    """One routed decision for a single fees row."""

    patient_folder: str
    action: str
    reason: str                        # human-readable WHY (Tagalog ok)
    review_code: str = ""              # set for manual_review / missing tool
    tool_available: bool = True        # False when TOOL_AVAILABLE says missing
    ready_verdict: str = ""            # raw "Ready to Generate XML" value
    problems: tuple = field(default_factory=tuple)
    xml_complete: bool = False         # Slice F: output folder already has
                                       # CF4+CF5+ESOA -> excluded from plan



# -- Decision table -----------------------------------------------------------

def _blank(value) -> bool:
    return not str(value or "").strip()


def _missing_signed_dates(row: dict) -> list:
    """Signed-date columns (Consent/Auth share the Date Fill path)."""
    missing = []
    if _blank(row.get(KEY_PROF_DATE)):
        missing.append("Prof Fee Sign Date BLANK")
    if _blank(row.get(KEY_CONSENT_DATE)):
        missing.append("Consent Date BLANK")
    # User-confirmed: Auth Sign Date IS Consent — same Date Fill action.
    if _blank(row.get(KEY_AUTH_DATE)):
        missing.append("Auth Sign Date BLANK (= Consent)")
    return missing


# -- Slice F: plan-time output-folder XML gate --------------------------------

DEFAULT_OUTPUT_ROOT = r"C:\claims_bot\output"


def default_output_root() -> Path:
    """Output root from CLAIMS_OUTPUT_FOLDER (same convention as
    pdf_preview_service / workflow_adapters). Read at call time so tests
    can patch the environment."""
    return Path(os.getenv("CLAIMS_OUTPUT_FOLDER", DEFAULT_OUTPUT_ROOT))


def resolve_output_folder(folder_value, output_root) -> Path | None:
    """Locate one patient's output folder; None disables the scan.

    fees_checker writes a bare folder NAME into "Patient Folder", so it is
    joined under output_root; an absolute value is used as-is.
    """
    raw = str(folder_value or "").strip()
    if not raw or output_root is None:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    return Path(output_root) / candidate


def decide_action(row: dict, *, output_root=None) -> ActionDecision:
    """Route one fees row to its next action. Pure function, never guesses.

    Args:
        row: one fees_checker row dict (HEADERS keys). Missing keys are
            treated as blank/unknown — never defaulted to a good value.
        output_root: optional root for the Slice F plan-time XML gate
            (read-only scan for CF4/CF5/ESOA). ``None`` keeps the legacy
            behavior — no filesystem access at all.
    """
    folder = str((row or {}).get(KEY_FOLDER) or "").strip()
    if not isinstance(row, dict) or not folder:
        return ActionDecision(
            patient_folder=folder or "(unknown)",
            action=ACTION_MANUAL_REVIEW,
            reason="Hindi mabasa ang fees row — manual review.",
            review_code=REVIEW_UNREADABLE_ROW,
            ready_verdict=str((row or {}).get(KEY_READY) or ""),
        )

    status = str(row.get(KEY_STATUS) or "").strip()
    ready = str(row.get(KEY_READY) or "").strip()

    # 1. No HBSys record at all — nothing downstream can run.
    if status == "NO RECORD":
        return ActionDecision(
            patient_folder=folder,
            action=ACTION_MANUAL_REVIEW,
            reason="Walang hpatcon1 record sa HBSys — manual review.",
            review_code=REVIEW_NO_RECORD,
            ready_verdict=ready,
        )

    # 2. Bill not finalized — the Final Bill tool (Slice D) handles it.
    if status == "NO FINAL BILL":
        tool_ready = TOOL_AVAILABLE[ACTION_FINAL_BILL]
        return ActionDecision(
            patient_folder=folder,
            action=ACTION_FINAL_BILL,
            reason="NO FINAL BILL sa HBSys — kailangan i-final bill muna.",
            review_code="" if tool_ready else REVIEW_FINAL_BILL_TOOL_MISSING,
            tool_available=tool_ready,
            ready_verdict=ready,
        )

    # 2b. Charge totals mismatch — operator decision 2026-10-07: the
    #     Final Bill automation only touches bills that were NEVER
    #     finalized (NO FINAL BILL). MISMATCH (itemized vs grouped
    #     charges) needs an operator decision, so it queues for
    #     manual review instead of auto-re-finaling the bill.
    #     (Was FINAL_BILL per the 2026-09-26 decision — reversed.)
    if status == "MISMATCH":
        return ActionDecision(
            patient_folder=folder,
            action=ACTION_MANUAL_REVIEW,
            reason=(
                "MISMATCH ang itemized vs grouped charges — "
                "manual review (hindi na auto-final bill)."
            ),
            review_code=REVIEW_MISMATCH,
            ready_verdict=ready,
        )

    # 3. Any signed date blank — Date Fill (Consent/Auth share the path).
    missing_dates = _missing_signed_dates(row)
    if missing_dates:
        return ActionDecision(
            patient_folder=folder,
            action=ACTION_DATE_FILL,
            reason="Kailangan ng Date Fill: " + "; ".join(missing_dates) + ".",
            ready_verdict=ready,
            problems=tuple(missing_dates),
        )

    # 4. Gate says YES — straight to the XML Clicker. Slice F: when an
    #    output root is given, scan the patient folder read-only — a fully
    #    generated XML set (CF4+CF5+ESOA) marks the row xml_complete so the
    #    plan can drop it; a partial set stays but names what is missing.
    if ready == "YES":
        target = resolve_output_folder(folder, output_root)
        if target is not None and target.is_dir():
            existing = xml_check.find_existing_xml_kinds(target)
            if xml_check.is_xml_complete(existing):
                return ActionDecision(
                    patient_folder=folder,
                    action=ACTION_XML_CLICKER,
                    reason=("Kumpleto na ang XML sa output folder "
                            "(CF4+CF5+ESOA) — hindi na kailangan."),
                    ready_verdict=ready,
                    xml_complete=True,
                )
            missing = xml_check.missing_xml_kinds(existing)
            if missing:
                return ActionDecision(
                    patient_folder=folder,
                    action=ACTION_XML_CLICKER,
                    reason=("Ready to Generate XML = YES — kulang ng XML: "
                            + xml_check.format_kinds(missing)
                            + " — tuloy sa XML Clicker."),
                    ready_verdict=ready,
                )
        return ActionDecision(
            patient_folder=folder,
            action=ACTION_XML_CLICKER,
            reason="Ready to Generate XML = YES — diretso sa XML Clicker.",
            ready_verdict=ready,
        )

    # 5. Anything else (ADM/DIS mismatch, unexpected Status, gate NO
    #    without a known cause) — manual review, never guessed.
    #    (MISMATCH routed to FINAL_BILL at step 2b, so it never reaches here.)
    problems = []
    if row.get(KEY_ADM_MATCH) != "YES":
        problems.append("ADM date mismatch")
    if row.get(KEY_DIS_MATCH) != "YES":
        problems.append("DIS date mismatch")
    if status not in ("MATCH", "MISMATCH", "NO FINAL BILL", "NO RECORD"):
        problems.append(f"status is {status or 'unknown'}")
    if not problems:
        problems.append("Ready to Generate XML = NO (unknown cause)")
    return ActionDecision(
        patient_folder=folder,
        action=ACTION_MANUAL_REVIEW,
        reason="Manual review: " + "; ".join(problems) + ".",
        review_code=(
            REVIEW_ADM_DIS_MISMATCH
            if (row.get(KEY_ADM_MATCH) != "YES"
                or row.get(KEY_DIS_MATCH) != "YES")
            else REVIEW_NOT_READY_OTHER
        ),
        ready_verdict=ready,
        problems=tuple(problems),
    )



# -- Batch + summary ----------------------------------------------------------

@dataclass(frozen=True)
class PlanSummary:
    """Counts per action for one batch of fees rows."""

    total: int = 0
    date_fill: int = 0
    final_bill: int = 0
    xml_clicker: int = 0
    manual_review: int = 0

    def as_dict(self) -> dict:
        return {
            ACTION_DATE_FILL: self.date_fill,
            ACTION_FINAL_BILL: self.final_bill,
            ACTION_XML_CLICKER: self.xml_clicker,
            ACTION_MANUAL_REVIEW: self.manual_review,
        }


def build_plan(rows: list, output_root=None) -> list:
    """Route every fees row. Order preserved (input order = plan order).

    ``output_root=None`` keeps the legacy behavior (no folder scan).
    """
    return [decide_action(row, output_root=output_root) for row in (rows or [])]


def drop_completed_xml(decisions) -> tuple:
    """Slice F / D1-A: split off rows whose XML output is already complete.

    Returns (kept_decisions, excluded_count); plan order preserved.
    """
    kept = []
    excluded = 0
    for decision in decisions or []:
        if getattr(decision, "xml_complete", False):
            excluded += 1
        else:
            kept.append(decision)
    return kept, excluded


def summarize_plan(decisions: list) -> PlanSummary:
    """Count decisions per action."""
    counts = {action: 0 for action in ALL_ACTIONS}
    for decision in decisions or []:
        if decision.action in counts:
            counts[decision.action] += 1
    return PlanSummary(
        total=len(decisions or []),
        date_fill=counts[ACTION_DATE_FILL],
        final_bill=counts[ACTION_FINAL_BILL],
        xml_clicker=counts[ACTION_XML_CLICKER],
        manual_review=counts[ACTION_MANUAL_REVIEW],
    )


def describe_decision(decision: ActionDecision) -> str:
    """One-line human summary (for logs / the Plan Panel)."""
    tool = "" if decision.tool_available else " [TOOL MISSING]"
    code = f" [{decision.review_code}]" if decision.review_code else ""
    return (
        f"{decision.patient_folder}: {decision.action.upper()}{tool}{code} "
        f"— {decision.reason}"
    )
