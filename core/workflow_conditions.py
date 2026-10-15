"""Per-node conditions for the Workflow engine (Phase 3, 2026-10-12).

A node may declare ``params["when"]`` — a CONDITION ID evaluated BEFORE the
node runs. A false condition does NOT remove the node from the workflow:
the node is reported as ``SKIPPED`` with a human-readable reason and the
chain continues (the existing failure policy is untouched).

Every condition is PURE and DETERMINISTIC: it reads only the execution
context that earlier nodes produced (Fees rows, Claims Checker results).
No AI, no guessing — an unreadable/blank value never satisfies a
condition, it only skips the node with a reason.

The Fees routing itself is NOT re-implemented here: the real decision
logic already exists in :func:`core.agent.fees_actions.decide_action`
(pure). This module only AGGREGATES those existing decisions across the
batch, so a batch runs a node when at least one patient still needs it.

Standalone test:
    python core/workflow_conditions.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.agent import fees_actions  # noqa: E402

# -- condition ids ---------------------------------------------------------

CONDITION_ALWAYS = "always"
CONDITION_FEES_SAYS_DATE_FILL = "fees_says_date_fill"
CONDITION_FEES_SAYS_FINAL_BILL = "fees_says_final_bill"
CONDITION_FEES_READY_XML = "fees_ready_xml"
CONDITION_FEES_ALLOWS_XML = "fees_allows_xml"
CONDITION_CHECKER_READY = "checker_is_ready"

ALL_CONDITIONS = (
    CONDITION_ALWAYS,
    CONDITION_FEES_SAYS_DATE_FILL,
    CONDITION_FEES_SAYS_FINAL_BILL,
    CONDITION_FEES_READY_XML,
    CONDITION_FEES_ALLOWS_XML,
    CONDITION_CHECKER_READY,
)

#: Condition ids a node may use, with a one-line label for the list editor.
CONDITION_LABELS = {
    CONDITION_ALWAYS: "Always run",
    CONDITION_FEES_SAYS_DATE_FILL: "Run when Fees says Date Fill is needed",
    CONDITION_FEES_SAYS_FINAL_BILL: "Run when Fees says Final Bill is needed",
    CONDITION_FEES_READY_XML: "Run when Fees says ready for XML",
    CONDITION_FEES_ALLOWS_XML: (
        "Run when Fees allows XML (ready to generate, or MISMATCH totals)"
    ),
    CONDITION_CHECKER_READY: "Run when Claims Checker has READY folders",
}

#: The fees Status of a row whose itemized and grouped charges differ.
#: Verified in fees_checker: a MISMATCH row has itemized > 0, i.e. the
#: bill IS already finalized in HBSys (that is why NO FINAL BILL is the
#: separate "itemized is 0.00" status). Operator rule 2026-10-12: a
#: MISMATCH claim therefore does not need Final Bill again — it goes
#: straight to XML generation.
FEES_STATUS_MISMATCH = "MISMATCH"

#: Fees Status of a row whose bill was never finalized (itemized is 0.00).
#: Operator rule 2026-10-13: such a row needs BOTH Date Fill (when any
#: signed date is blank) AND Final Bill before XML generation is allowed.
FEES_STATUS_NO_FINAL_BILL = "NO FINAL BILL"

# -- helpers ---------------------------------------------------------------


def is_known(condition: str) -> bool:
    return str(condition or "").strip() in ALL_CONDITIONS


def _fees_rows(context: dict) -> list[dict]:
    rows = context.get("fees_rows")
    return list(rows) if isinstance(rows, list) else []


def _folders_needing(context: dict, action: str) -> list[str]:
    """Folders whose EXISTING fees decision is *action* (pure aggregation)."""
    output_root = context.get("output_root")
    folders: list[str] = []
    for row in _fees_rows(context):
        try:
            decision = fees_actions.decide_action(row, output_root=output_root)
        except Exception:  # noqa: BLE001 - never guess; skip the row
            continue
        if decision.action == action:
            folders.append(decision.patient_folder)
    return folders


def _short(folders: list[str], limit: int = 3) -> str:
    head = ", ".join(folders[:limit])
    more = f" (+{len(folders) - limit} more)" if len(folders) > limit else ""
    return head + more


# -- MISMATCH handling (operator rule 2026-10-12) --------------------------


def _status(row: dict) -> str:
    return str((row or {}).get(fees_actions.KEY_STATUS) or "").strip()


def _folder(row: dict) -> str:
    return str((row or {}).get(fees_actions.KEY_FOLDER) or "").strip()


def _signed_dates_missing(row: dict) -> bool:
    """True when a signed date is still blank (the EXISTING Fees rule).

    Reuses ``fees_actions._missing_signed_dates`` so this module never
    re-implements which dates matter.
    """
    try:
        return bool(fees_actions._missing_signed_dates(row))
    except Exception:  # noqa: BLE001 - unreadable row -> treat as missing
        return True


def mismatch_rows(context: dict) -> list[dict]:
    """Fees rows whose Status is MISMATCH (bill already finalized).

    Non-dict / unreadable rows are ignored here (the strict Fees routing
    already refuses them) so a malformed row can never satisfy a gate.
    """
    return [row for row in _fees_rows(context)
            if isinstance(row, dict)
            and _status(row) == FEES_STATUS_MISMATCH]


def mismatch_folders_missing_dates(context: dict) -> list[str]:
    """MISMATCH folders that still need Date Fill before XML generation."""
    folders: list[str] = []
    for row in mismatch_rows(context):
        if _signed_dates_missing(row):
            name = _folder(row)
            if name:
                folders.append(name)
    return folders


# -- NO FINAL BILL handling (operator rule 2026-10-13) ----------------------


def no_final_bill_rows(context: dict) -> list[dict]:
    """Fees rows whose Status is NO FINAL BILL (bill never finalized).

    Non-dict / unreadable rows are ignored here (the strict Fees routing
    already refuses them) so a malformed row can never satisfy a gate.
    """
    return [row for row in _fees_rows(context)
            if isinstance(row, dict)
            and _status(row) == FEES_STATUS_NO_FINAL_BILL]


def no_final_bill_folders_missing_dates(context: dict) -> list[str]:
    """NO FINAL BILL folders that still need Date Fill first.

    The EXISTING Fees routing (``decide_action`` step 2) sends every
    NO FINAL BILL row to Final Bill BEFORE reaching its date check
    (step 3) — so without this helper the Date Fill node would be
    skipped for an unbilled patient with blank signed dates, while the
    Final Bill node's own Date Fill gate (operator rule 2026-10-09)
    refuses to bill unverified folders. The patient would be stuck
    forever and never reach XML. This helper closes that gap: the
    patient gets Date Fill AND Final Bill, in workflow order.
    """
    folders: list[str] = []
    for row in no_final_bill_rows(context):
        if _signed_dates_missing(row):
            name = _folder(row)
            if name:
                folders.append(name)
    return folders


def mismatch_folders_allowing_xml(context: dict) -> list[str]:
    """MISMATCH folders whose signed dates are already complete.

    A MISMATCH claim is already billed (verified: itemized > 0), so it
    skips Final Bill and may go straight to XML — but only once its
    signed dates are filled, otherwise the generated XML would carry
    the very dates Date Fill exists to fix.
    """
    folders: list[str] = []
    for row in mismatch_rows(context):
        if not _signed_dates_missing(row):
            name = _folder(row)
            if name:
                folders.append(name)
    return folders


def _unique(folders: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in folders:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


# -- evaluation -------------------------------------------------------------


def evaluate(condition: str, context: dict | None = None) -> tuple[bool, str]:
    """Evaluate one condition.

    Returns ``(should_run, reason)``. ``reason`` is the skip explanation
    when ``should_run`` is False (empty string otherwise).
    """
    context = context or {}
    name = str(condition or "").strip() or CONDITION_ALWAYS

    if name == CONDITION_ALWAYS:
        return True, ""

    if not is_known(name):
        # Unknown condition -> skip, never guess. The engine surfaces the
        # reason so a typo in workflow_config.json is visible immediately.
        return False, f"unknown condition {name!r} — node skipped, never guessed"

    # The Fees rows are only required by the three Fees-based conditions.
    if name in (
        CONDITION_FEES_SAYS_DATE_FILL,
        CONDITION_FEES_SAYS_FINAL_BILL,
        CONDITION_FEES_READY_XML,
        CONDITION_FEES_ALLOWS_XML,
    ) and not _fees_rows(context):
        return False, "no Fees Check result available — node skipped"

    if name == CONDITION_FEES_SAYS_DATE_FILL:
        folders = _folders_needing(context, fees_actions.ACTION_DATE_FILL)
        folders += mismatch_folders_missing_dates(context)
        # Operator rule 2026-10-13: an unbilled patient with blank
        # signed dates needs Date Fill too (the Fees routing sends it
        # to Final Bill first, which would otherwise skip Date Fill).
        folders += no_final_bill_folders_missing_dates(context)
        folders = _unique(folders)
        if folders:
            return True, ""
        return False, "Fees: no patient needs Date Fill"

    if name == CONDITION_FEES_SAYS_FINAL_BILL:
        folders = _folders_needing(context, fees_actions.ACTION_FINAL_BILL)
        if folders:
            return True, ""
        return False, "Fees: no patient needs Final Bill"

    if name == CONDITION_FEES_READY_XML:
        folders = _folders_needing(context, fees_actions.ACTION_XML_CLICKER)
        if folders:
            return True, ""
        return False, "Fees: no patient is ready for XML generation"

    if name == CONDITION_FEES_ALLOWS_XML:
        # (a) the existing route: Fees says "Ready to Generate XML" = YES
        folders = _folders_needing(context, fees_actions.ACTION_XML_CLICKER)
        # (b) MISMATCH: the bill is already finalized, so Final Bill is
        #     skipped and XML generation may run (dates must be filled).
        folders += mismatch_folders_allowing_xml(context)
        folders = _unique(folders)
        if folders:
            return True, ""
        return False, "Fees: no patient allows XML generation"

    if name == CONDITION_CHECKER_READY:
        ready = context.get("ready_folders")
        if isinstance(ready, list) and ready:
            return True, ""
        return False, "Claims Checker: no READY folders"

    return False, f"condition {name!r} not handled"


def node_condition(node) -> str:
    """Condition id declared by a node (``params['when']``).

    Missing/blank means "always" — the current, unchanged behaviour.
    """
    params = getattr(node, "params", None)
    if not isinstance(params, dict):
        return CONDITION_ALWAYS
    return str(params.get("when", CONDITION_ALWAYS) or CONDITION_ALWAYS).strip() or (
        CONDITION_ALWAYS
    )


if __name__ == "__main__":
    rows_match = {
        "Patient Folder": "A",
        "Status": "MATCH",
        "Adm Match": "YES",
        "Dis Match": "YES",
        "Prof Fee Sign Date (hprofserv.pdoctorsigndate)": "2026/09/05",
        "Consent Date (hpatcon1.consentdate)": "2026/09/05",
        "Auth Sign Date (hpatcon1.authsigndate)": "2026/09/05",
        "Ready to Generate XML": "YES",
    }
    rows_no_dates = dict(
        rows_match, **{"Consent Date (hpatcon1.consentdate)": ""}
    )
    rows_no_bill = dict(rows_match, Status="NO FINAL BILL")
    # Charges differ, but itemized > 0 -> the bill IS already finalized.
    rows_mismatch = dict(rows_match, Status="MISMATCH")

    assert evaluate(CONDITION_ALWAYS, {})[0] is True
    assert evaluate("", {})[0] is True
    ok, reason = evaluate("nope", {})
    assert ok is False and "never guessed" in reason

    ctx = {"fees_rows": [rows_match]}
    assert evaluate(CONDITION_FEES_SAYS_DATE_FILL, ctx)[0] is False
    assert evaluate(CONDITION_FEES_READY_XML, ctx)[0] is True

    ctx = {"fees_rows": [rows_no_dates]}
    assert evaluate(CONDITION_FEES_SAYS_DATE_FILL, ctx)[0] is True

    ctx = {"fees_rows": [rows_no_bill]}
    assert evaluate(CONDITION_FEES_SAYS_FINAL_BILL, ctx)[0] is True

    # NO FINAL BILL + complete dates -> Final Bill only (no Date Fill).
    assert evaluate(CONDITION_FEES_SAYS_DATE_FILL, ctx)[0] is False
    assert evaluate(CONDITION_FEES_ALLOWS_XML, ctx)[0] is False

    # NO FINAL BILL + blank date -> Date Fill AND Final Bill (operator
    # rule 2026-10-13); XML stays skipped until Ready to Generate = YES.
    ctx = {"fees_rows": [dict(rows_no_bill,
                               **{"Consent Date (hpatcon1.consentdate)": ""})]}
    assert evaluate(CONDITION_FEES_SAYS_DATE_FILL, ctx)[0] is True
    assert evaluate(CONDITION_FEES_SAYS_FINAL_BILL, ctx)[0] is True
    assert evaluate(CONDITION_FEES_ALLOWS_XML, ctx)[0] is False
    assert evaluate(CONDITION_FEES_READY_XML, ctx)[0] is False

    # MISMATCH: already billed -> Date Fill only if dates missing, XML allowed
    assert evaluate(CONDITION_FEES_READY_XML, ctx)[0] is False
    ctx = {"fees_rows": [rows_mismatch]}
    assert evaluate(CONDITION_FEES_SAYS_FINAL_BILL, ctx)[0] is False
    assert evaluate(CONDITION_FEES_ALLOWS_XML, ctx)[0] is True
    assert evaluate(CONDITION_FEES_READY_XML, ctx)[0] is False  # strict: NO
    assert evaluate(CONDITION_FEES_SAYS_DATE_FILL, ctx)[0] is False
    ctx = {"fees_rows": [dict(rows_mismatch,
                               **{"Auth Sign Date (hpatcon1.authsigndate)": ""})]}
    assert evaluate(CONDITION_FEES_ALLOWS_XML, ctx)[0] is False
    assert evaluate(CONDITION_FEES_SAYS_DATE_FILL, ctx)[0] is True

    assert evaluate(CONDITION_FEES_READY_XML, {})[0] is False
    assert evaluate(CONDITION_CHECKER_READY, {"ready_folders": ["A"]})[0] is True
    assert evaluate(CONDITION_CHECKER_READY, {})[0] is False

    class _N:
        params = {"when": CONDITION_FEES_READY_XML}

    assert node_condition(_N()) == CONDITION_FEES_READY_XML
    assert node_condition(type("X", (), {"params": {}})()) == CONDITION_ALWAYS

    print("workflow_conditions standalone check passed")