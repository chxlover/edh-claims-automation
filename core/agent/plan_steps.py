"""Plan Steps (Slice H — Claims Agent, per-patient step fan-out).

One Fees Check row can need MORE THAN ONE action for the same patient, and
the plan shows them as SEPARATE ROWS: FINAL BILL first, then DATE FILL for
the same folder, next to each other.

    DELA CRUZ, JUAN - ADM..DIS    FINAL BILL
    DELA CRUZ, JUAN - ADM..DIS    DATE FILL

Why this module exists: decide_action() is first-match-wins and returns ONE
action per row. That stays untouched (decide_rows() delegates to it), so the
existing panel, the completion ledger and all existing tests keep working.
This module only ADDS the second step and puts the rows in a safe order.

Ordering is the real safety net, not a gate: FINAL BILL runs before DATE FILL
for the same patient, so the plan never fills dates on an encounter that has
not been finalized. DATE FILL still runs when FINAL BILL blocks or fails —
the operator's decision 2026-10-05, because the Hospital No. is the reference
either way. That is reported as a NOTE, never as a silent skip.

XML CLICKER deliberately stays OUT of the chain: it drives a different
window, and it was not connected yet.

Pure functions only — no HBSys, no subprocess, no GUI, no DB.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Sequence

from core.agent.fees_actions import (
    ACTION_DATE_FILL,
    ACTION_FINAL_BILL,
    ACTION_MANUAL_REVIEW,
    ACTION_XML_CLICKER,
    _missing_signed_dates,
    decide_action,
)

# Order of the steps of ONE patient. Only these two are connected; XML
# CLICKER is listed for completeness but is never fanned out.
STEP_ORDER = (
    ACTION_FINAL_BILL,
    ACTION_DATE_FILL,
    ACTION_XML_CLICKER,
    ACTION_MANUAL_REVIEW,
)

# -- Fan-out -------------------------------------------------------------------

def decide_rows(row: dict, *, output_root=None) -> tuple:
    """Every action one fees row needs, already in STEP_ORDER.

    The FIRST element is always exactly what decide_action() returned, so a
    caller that only wants the next action keeps the existing behavior.
    """
    primary = decide_action(row, output_root=output_root)
    steps = [primary]
    if primary.action != ACTION_FINAL_BILL:
        return tuple(steps)
    # FINAL BILL is the primary action — does this patient also need dates?
    missing = _missing_signed_dates(row or {})
    if missing:
        steps.append(
            replace(
                primary,
                action=ACTION_DATE_FILL,
                reason=(
                    "Pagkatapos ng Final Bill: kailangan ng Date Fill — "
                    + "; ".join(missing)
                    + "."
                ),
                review_code="",
                problems=tuple(missing),
            )
        )
    return tuple(steps)


def build_step_plan(rows: list, output_root=None) -> list:
    """decide_rows() for every fees row, flattened. Order preserved."""
    plan = []
    for row in rows or []:
        plan.extend(decide_rows(row, output_root=output_root))
    return plan


# -- Ordering ------------------------------------------------------------------

def step_rank(action: str) -> int:
    """Sort key for one action; unknown actions sort last, stably."""
    try:
        return STEP_ORDER.index(str(action or ""))
    except ValueError:
        return len(STEP_ORDER)
def order_plan_items(items) -> list:
    """Group rows by patient, FINAL BILL before DATE FILL, CSV order kept.

    Works on plan items (dicts with 'patient_folder'/'action') or on
    ActionDecision objects — both are read through _field(). The sort is
    STABLE: inside one action the original plan order never changes, so the
    operator still sees the Fees Check sequence.
    """
    grouped: dict = {}
    order: list = []
    for index, item in enumerate(items or []):
        folder = _field(item, "patient_folder")
        if folder not in grouped:
            grouped[folder] = []
            order.append(folder)
        grouped[folder].append((step_rank(_field(item, "action")), index, item))
    result = []
    for folder in order:
        for _rank, _index, item in sorted(
            grouped[folder], key=lambda entry: (entry[0], entry[1])
        ):
            result.append(item)
    return result


# -- Patient chains (grouping for the panel + the run loop) --------------------

@dataclass
class PatientChain:
    """Every step row belonging to one patient, in execution order."""

    patient_folder: str
    hospital_no: str = ""
    steps: tuple = field(default_factory=tuple)   # ActionDecision objects
    index: int = 0                                # position in the plan

    @property
    def actions(self) -> tuple:
        return tuple(step.action for step in self.steps)

    @property
    def has_final_bill(self) -> bool:
        return ACTION_FINAL_BILL in self.actions

    def step_for(self, action: str):
        """The one step with this action, or None."""
        for step in self.steps:
            if step.action == action:
                return step
        return None

    def as_dict(self) -> dict:
        return {
            "patient_folder": self.patient_folder,
            "hospital_no": self.hospital_no,
            "index": self.index,
            "actions": list(self.actions),
            "steps": [
                {
                    "action": step.action,
                    "reason": step.reason,
                    "review_code": step.review_code,
                    "tool_available": step.tool_available,
                }
                for step in self.steps
            ],
        }


def group_by_patient(decisions: Sequence, *, hospital_no_fn=None) -> list:
    """Group decided steps into PatientChains, ordered by first appearance.

    `hospital_no_fn(folder) -> str` is injected so this module never has to
    import the orchestrator's folder regex (no circular import).
    """
    chains: dict = {}
    order: list = []
    for decision in decisions or []:
        folder = getattr(decision, "patient_folder", "") or ""
        if folder not in chains:
            chains[folder] = PatientChain(patient_folder=folder, index=len(order))
            order.append(folder)
        chains[folder].steps = tuple(chains[folder].steps) + (decision,)
    for chain in chains.values():
        chain.steps = tuple(
            sorted(chain.steps, key=lambda step: step_rank(step.action))
        )
    if hospital_no_fn is not None:
        for folder in order:
            chains[folder].hospital_no = str(hospital_no_fn(folder) or "")
    return [chains[folder] for folder in order]


def prerequisite_note(action: str, chain) -> str:
    """Advisory note for a step whose predecessor sits in the same chain.

    Returns '' when the step has no predecessor in this chain (the common
    case: DATE FILL alone, because FINAL BILL finished in a past run and the
    ledger already dropped its row). Never blocks execution — the Hospital No.
    is the reference, so DATE FILL runs anyway (operator decision
    2026-10-05) and the operator is told the Final Bill is still open.
    """
    if str(action or "") != ACTION_DATE_FILL or chain is None:
        return ""
    if chain.step_for(ACTION_FINAL_BILL) is None:
        return ""
    return (
        "FINAL BILL ay nasa plan din pero hindi pa natatapos para sa "
        "pasyenteng ito — DATE FILL ang tumatakbo (based sa Hospital No.)."
    )


def prerequisite_note_for_plan(action: str, item, items) -> str:
    """prerequisite_note() for the plan-row loop (dicts, not ActionDecisions).

    The run loop walks raw plan rows, so this reads the same question off the
    row list: is this patient's FINAL BILL row somewhere in this plan?
    Advisory only — the row still executes.
    """
    if str(action or "") != ACTION_DATE_FILL:
        return ""
    folder = _field(item, "patient_folder")
    if not folder:
        return ""
    for other in items or []:
        if other is item:
            continue
        if _field(other, "patient_folder") == folder and _field(
            other, "action"
        ) == ACTION_FINAL_BILL:
            return prerequisite_note(action, _chain_of(item, items))
    return ""


def _chain_of(item, items):
    """A minimal PatientChain holding this item's patient, for the note text."""
    folder = _field(item, "patient_folder")
    steps = tuple(
        _RowView(other)
        for other in items or []
        if _field(other, "patient_folder") == folder
        and _field(other, "action") in STEP_ORDER
    )
    return PatientChain(patient_folder=folder, steps=steps)


class _RowView:
    """Read-only action/patient view over a plan row dict."""

    __slots__ = ("_row",)

    def __init__(self, row):
        self._row = row or {}

    @property
    def patient_folder(self):
        return _field(self._row, "patient_folder")

    @property
    def action(self):
        return _field(self._row, "action")

    @property
    def reason(self):
        return _field(self._row, "reason")


def _field(item, name: str):
    """Read one field from a plan item dict or an ActionDecision."""
    if isinstance(item, dict):
        return item.get(name, "")
    return getattr(item, name, "")


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import json

    from core.agent import agent_plan_store as store

    # Three patients, exactly the operator's 2026-10-05 example.
    juan = "DELA CRUZ, JUAN - 123456789012345 - ADM20260901_DIS20260903"
    pedro = "DELA CRUZ, PEDRO - 123456789012346 - ADM20260901_DIS20260901"
    peter = "DELA CRUZ, PETER - 123456789012347 - ADM20260901_DIS20260901"

    def row(folder, status, dates, ready):
        return {
            "Patient Folder": folder,
            "Status": status,
            "ADM Match": "YES",
            "DIS Match": "YES",
            "Prof Fee Sign Date (hprofserv.pdoctorsigndate)": dates,
            "Consent Date (hpatcon1.consentdate)": dates,
            "Auth Sign Date (hpatcon1.authsigndate)": dates,
            "Ready to Generate XML": ready,
        }

    fees_rows = [
        row(juan, "NO FINAL BILL", "", "NO"),   # two steps
        row(pedro, "MATCH", "", "NO"),          # one step
        row(peter, "MATCH", "2026/09/05", "YES"),  # one step
    ]

    items = store.to_plan_items(build_step_plan(fees_rows))
    print("--- plan as the panel shows it (ordered) ---")
    for item in order_plan_items(items):
        print(f"{item['action'].upper():13s} {item['patient_folder']}")
    print("--- what the run loop would execute, in order ---")
    for item in order_plan_items(items):
        note = prerequisite_note_for_plan(item["action"], item, items)
        print(f"{item['action'].upper():13s} {note or '(walang note)'}")
    print("--- one chain as JSON ---")
    print(json.dumps(
        group_by_patient(build_step_plan(fees_rows))[0].as_dict(),
        indent=2,
        ensure_ascii=False,
    ))