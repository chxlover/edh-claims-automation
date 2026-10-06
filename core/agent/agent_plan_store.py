"""Agent Plan Store (Slice C — Claims Agent, Phase 1 core).

Pure plan assembly + persistence for the Agent Plan Panel. Reads the Fees
Check CSV report, routes every row through the Slice B action table
(core.agent.fees_actions), and optionally saves the approved plan as a
JSON audit trail under logs/ (filesystem-as-memory, same pattern as
core/add_claims_state.py).

No subprocess, no HBSys, no DB access — pure functions of files + dicts.
The panel GUI calls this module only; it contains no Tkinter code.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from core.agent import fees_actions as actions
from core.agent import plan_steps as step_plan

BASE_DIR = Path(r"C:\claims_bot")
DEFAULT_FEES_CSV = BASE_DIR / "fees_checker_report.csv"
PLAN_DIR = BASE_DIR / "logs"
PLAN_PREFIX = "agent_plan_"
# Persistent ledger of (patient_folder, action) rows already executed OK.
COMPLETED_LEDGER = PLAN_DIR / "agent_completed_actions.json"

# Status shown in the panel for one planned patient row.
STATUS_PENDING = "PENDING"      # decided, not yet approved
STATUS_APPROVED = "APPROVED"    # approved, awaiting Slice E execution
STATUS_SKIPPED = "SKIPPED"      # excluded by the user in the panel



# -- Plan assembly (pure) -----------------------------------------------------

def read_fees_rows(csv_path: str | Path) -> list[dict]:
    """Read Fees Check CSV rows. Returns [] when the file is missing."""
    path = Path(csv_path)
    if not path.is_file():
        return []
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except OSError:
        return []


# -- Latest Fees Check CSV (plan base = pinaka-huling Fees check) --------------

def latest_fees_csv(base_dir=None) -> Path:
    """Newest fees_checker_report*.csv — the fixed report name or the
    timestamped fallback fees_checker writes when the fixed file is locked.
    Falls back to DEFAULT_FEES_CSV when no report exists yet."""
    base = Path(base_dir) if base_dir is not None else BASE_DIR
    newest = None
    newest_mtime = -1.0
    try:
        for candidate in base.glob("fees_checker_report*.csv"):
            try:
                mtime = candidate.stat().st_mtime
            except OSError:
                continue
            if mtime > newest_mtime:
                newest, newest_mtime = candidate, mtime
    except OSError:
        return DEFAULT_FEES_CSV
    return newest if newest is not None else DEFAULT_FEES_CSV


def resolve_fees_csv(value, base_dir=None) -> Path:
    """Pick the CSV the plan is built from.

    Blank or the canonical report name (fees_checker_report.csv) resolves
    to the NEWEST Fees Check CSV, so the plan always follows the latest
    run; any other explicit path (Browse/typed) is respected as-is.
    """
    raw = str(value or "").strip()
    if raw and Path(raw).name != DEFAULT_FEES_CSV.name:
        return Path(raw)
    return latest_fees_csv(base_dir)


# -- Completion ledger (tapos nang mga row — hindi na uulit) -------------------

def load_completed_actions(path=None) -> set:
    """(patient_folder, action) pairs already executed OK in a past run.

    A missing or corrupt ledger reads as empty — the plan then simply shows
    every row, never an error.
    """
    ledger_path = Path(path) if path is not None else COMPLETED_LEDGER
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        return set()
    completed = set()
    for folder, action_map in entries.items():
        if isinstance(action_map, dict):
            for action in action_map:
                completed.add((str(folder), str(action)))
    return completed


def record_completed_actions(pairs, path=None) -> int:
    """Merge (patient_folder, action) pairs into the ledger (atomic write).

    Returns how many were newly added; 0 leaves the file untouched. An
    unreadable existing ledger starts fresh rather than raising.
    """
    ledger_path = Path(path) if path is not None else COMPLETED_LEDGER
    entries: dict = {}
    try:
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("entries"), dict):
            entries = payload["entries"]
    except (OSError, ValueError):
        entries = {}
    stamp = datetime.now().isoformat(timespec="seconds")
    added = 0
    for raw_folder, raw_action in pairs or ():
        folder = str(raw_folder).strip()
        action = str(raw_action)
        if not folder:
            continue
        bucket = entries.get(folder)
        if not isinstance(bucket, dict):
            bucket = {}
            entries[folder] = bucket
        if action in bucket:
            continue
        bucket[action] = stamp
        added += 1
    if added:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = ledger_path.parent / (ledger_path.name + ".tmp")
        tmp_path.write_text(
            json.dumps({"version": 1, "entries": entries}, indent=2,
                       ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_path.replace(ledger_path)
    return added


def drop_completed_actions(decisions, completed) -> tuple:
    """Drop rows already executed OK — same shape as drop_completed_xml().

    Returns (kept, dropped_count).
    """
    allowed = completed or set()
    kept = []
    dropped = 0
    for decision in decisions or []:
        key = (str(decision.patient_folder).strip(), decision.action)
        if key in allowed:
            dropped += 1
        else:
            kept.append(decision)
    return kept, dropped


def to_plan_items(decisions: list) -> list[dict]:
    """Convert Slice B decisions to panel rows (all PENDING)."""
    items: list[dict] = []
    for decision in decisions or []:
        items.append(
            {
                "patient_folder": decision.patient_folder,
                "action": decision.action,
                "reason": decision.reason,
                "review_code": decision.review_code,
                "tool_available": decision.tool_available,
                "ready_verdict": decision.ready_verdict,
                "status": STATUS_PENDING,
            }
        )
    return items


def build_plan_from_csv(
    csv_path: str | Path,
    output_root=None,
    completed=None,
    fan_out: bool = True,
) -> tuple:
    """Read CSV -> route rows -> panel items + summary + source note.

    Returns (items, summary_dict, note). `note` explains an empty plan
    (missing file vs no rows) so the panel can show WHY it is empty, and
    rows dropped by the Slice F gates (XML output / completed runs).

    `output_root=None` resolves CLAIMS_OUTPUT_FOLDER (default
    C:\\claims_bot\\output) — the project-wide convention.

    `completed=None` reads the persistent completion ledger
    (logs/agent_completed_actions.json): rows already executed OK drop out
    of the plan so finished work is never repeated. Pass a set of
    (patient_folder, action) pairs to control it explicitly.

    `fan_out` (Slice H, default True) turns one fees row that needs BOTH a
    Final Bill and dates into TWO adjacent panel rows — FINAL BILL first, then
    DATE FILL for the same folder. The completion ledger still keys on
    (folder, action), so a Final Bill finished in an earlier run leaves only
    the DATE FILL row. Set fan_out=False for the old one-row-per-fees-row plan.
    """
    path = Path(csv_path)
    if not path.is_file():
        return [], actions.summarize_plan([]).as_dict(), (
            f"Fees Check CSV not found: {path}. "
            "Click Fees Check first, then reopen this tab."
        )
    rows = read_fees_rows(path)
    if not rows:
        return [], actions.summarize_plan([]).as_dict(), (
            f"No patient rows in {path}. "
            "Click Fees Check first, then reopen this tab."
        )
    if output_root is None:
        output_root = actions.default_output_root()
    if completed is None:
        completed = load_completed_actions()
    if fan_out:
        # Slice H: NO FINAL BILL + missing dates -> FINAL BILL row, then a
        # DATE FILL row right below it for the same patient.
        decisions = step_plan.build_step_plan(rows, output_root=output_root)
    else:
        decisions = actions.build_plan(rows, output_root=output_root)
    decisions, xml_done = actions.drop_completed_xml(decisions)
    decisions, run_done = drop_completed_actions(decisions, completed)
    decisions = step_plan.order_plan_items(decisions)
    summary = actions.summarize_plan(decisions)
    note_parts = []
    if xml_done:
        note_parts.append(
            f"{xml_done} row hindi isinama sa plan: kumpleto na ang XML "
            "(CF4+CF5+ESOA) sa output folder."
        )
    if run_done:
        note_parts.append(
            f"{run_done} row tapos na sa nakaraang run — hindi na inuulit."
        )
    return to_plan_items(decisions), summary.as_dict(), " | ".join(note_parts)



# -- Approval + persistence ---------------------------------------------------

def apply_approval(items, selected_indexes):
    """Mark selected rows APPROVED, the rest SKIPPED.

    Returns (updated_items, approved_count, skipped_count). Unknown indexes
    are ignored (never guessed into the approved set).
    """
    approved = 0
    skipped = 0
    valid = set(selected_indexes or set())
    for index, item in enumerate(items or []):
        if index in valid:
            item["status"] = STATUS_APPROVED
            approved += 1
        else:
            item["status"] = STATUS_SKIPPED
            skipped += 1
    return list(items or []), approved, skipped


def approved_items(items):
    """Rows the user approved (the Slice E execution set)."""
    return [item for item in (items or []) if item.get("status") == STATUS_APPROVED]


def save_approved_plan(items, plan_dir=PLAN_DIR):
    """Persist APPROVED rows as a JSON audit trail. Returns the path."""
    directory = Path(plan_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = directory / f"{PLAN_PREFIX}{stamp}.json"
    payload = {
        "created_at": datetime.now().isoformat(),
        "total_rows": len(items or []),
        "items": [dict(item) for item in (items or [])],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def load_plan(path):
    """Read a saved plan file back. Returns {} when unreadable."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
