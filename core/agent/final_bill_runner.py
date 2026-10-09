"""Final Bill — Workflow Tab node CLI (folder contract, Option B).

Runs the VERIFIED Final Bill flow (the same `_default_final_bill` the Agent
Plan uses) once per patient folder under the claims output root.

Usage:

    python -m core.agent.final_bill_runner                 # DRY: list pending folders
    python -m core.agent.final_bill_runner --live          # LIVE: bill every pending folder
    python -m core.agent.final_bill_runner --live --limit 1
    python -m core.agent.final_bill_runner --live --force  # re-run marked folders

Folder contract (same regex as the uploaders / Agent Plan, never guessed):

    <output_root>/<PATIENT NAME> - <HOSPITAL NO> - ADMYYYYMMDD_DISYYYYMMDD
    -> hospital no + confinement come from the FOLDER NAME via
       core.add_claims_verifier.parse_folder_name(); entries that do not
       parse are skipped (not patient folders) and counted in the log.

Resume contract (why re-running is cheap and safe):

    <folder>\\.final_bill_ok   written ONLY when the patient's outcome is OK;
    later runs skip that folder unless --force. A BLOCKED/FAILED folder never
    gets a marker, so the next run retries it. The marker body is JSON
    (hospital no, outcome, detail, timestamp) for the operator's audit.

Status gate (operator rule 2026-10-08): before ANY Final Bill, this node
runs the Fees Check silently (read-only, ``write_reports=False`` — this
node never writes fees_checker_report.csv/.xlsx; the reports only come
from the main dashboard Fees Check). Only patients whose fees Status is
NO FINAL BILL are final-billed. MATCH (already final-billed in HBSys),
MISMATCH (itemized vs grouped), NO RECORD, ERROR and any folder without
a fees row are SKIPPED with a [SKIP] reason — the automation must never
re-final-bill a patient whose bill was already finalized, and MISMATCH
queues for manual review instead of auto-re-finaling. A failed Fees
Check stops the node (exit 1): a status is never guessed. --force
overrides the resume marker but NEVER this status gate.

Date Fill policy (operator rule 2026-10-09): the engine
continues to this node after a Date Fill review stop (the
Date Fill nodes run with continue_on_fail) and this node
does NOT gate on the Date Fill run log — the operator's
workflow final-bills a patient even when Date Fill stopped
on it (final billing does not require the CF2 date fill).
The Date Fill failures stay visible in the workflow report
(the node still shows FAILED); the Final Bill flow's own
confinement-row matching (never-guess) and per-patient
continue-on-error remain the safety boundary.

Exit codes (the workflow engine's fail-safe contract):

    0  every processed folder OK, or nothing to do (all marked / no folders)
    1  at least one BLOCKED/FAILED folder, an unusable output root, or a
       marker that could not be written -> the engine STOPS the chain after
       this node (opt out per node with continue_on_fail=True). Within the
       batch the runner keeps going (continue-on-error, same rule as the
       Agent Plan), so one bad patient never hides the others' results.

HBSys: live runs need the HBSys Billing screen. The registry marks this node
hbsys_touching=True and the engine pre-checks find_hbsys_window() before
launching it (same gate as Date Fill / XML Clicker). This module never
imports pywinauto itself — the UI automation lives behind the orchestrator
and loads only when a LIVE patient is actually processed.

Logging: stdout IS the log channel of this subprocess node — the workflow
adapter streams it line-by-line into the Workflow tab (same contract as
core/add_claims_uploader.py). print() with flush here is the interface, not
ad-hoc debugging.

Standalone test: `python -m core.agent.final_bill_runner` (DRY listing) plus
`python -m unittest tests.test_workflow_final_bill_node` for the unit suite.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from core.add_claims_verifier import parse_folder_name
from core.agent.fees_actions import default_output_root
from core.agent.orchestrator import (
    DEFAULT_TOOL_TIMEOUT,
    OUTCOME_BLOCKED,
    OUTCOME_FAILED,
    OUTCOME_OK,
    default_final_bill,
)

# Resume marker written inside a patient folder AFTER an OK Final Bill run.
MARKER_NAME = ".final_bill_ok"

# Exit codes (engine contract: 1 stops the chain, 0 continues).
EXIT_OK = 0
EXIT_NOT_OK = 1


def log(message: str) -> None:
    """One stdout line — the workflow adapter streams it into the GUI log."""
    print(message, flush=True)


def marker_path(folder: Path) -> Path:
    """Resume marker for one patient folder."""
    return Path(folder) / MARKER_NAME


def iter_patient_folders(output_root: Path) -> list[Path]:
    """Patient folders under *output_root*, sorted ASCENDING by name.

    Only directories whose name parses under the folder contract count as
    patient folders; everything else (stray dirs, files) is ignored, never
    guessed at. One folder = one patient, processed contiguously — the same
    patient grouping rule the Agent Plan uses.
    """
    root = Path(output_root)
    if not root.is_dir():
        return []
    folders: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        if parse_folder_name(child.name) is None:
            continue  # not a patient folder under the contract
        folders.append(child)
    return folders


def needs_final_bill(folder: Path, *, force: bool = False) -> bool:
    """True when this folder should run: no OK-marker yet, or --force."""
    if force:
        return True
    return not marker_path(folder).exists()


# ---------------------------------------------------------------- fees gate


def run_fees_check(output_root: Path) -> list[dict]:
    """Fees Check preflight: read-only rows for *output_root*, no reports.

    Operator rule 2026-10-08: the Workflow Final Bill node first
    runs the Fees Check SILENTLY (``write_reports=False`` — this node
    never writes fees_checker_report.csv/.xlsx; the reports only come
    from the main dashboard Fees Check) so the node can bill ONLY the
    patients whose fees Status is NO FINAL BILL. The check scans the
    node's own ``--output-root`` — the fees checker defaults to the
    project output folder, so its OUTPUT_DIR is pointed at this root
    for the call and restored afterwards.
    """
    import fees_checker  # local import: node stays importable without it

    previous_root = fees_checker.OUTPUT_DIR
    fees_checker.OUTPUT_DIR = Path(output_root)
    try:
        rows, _csv_path, _xlsx_path = fees_checker.run_check(
            write_reports=False
        )
    finally:
        fees_checker.OUTPUT_DIR = previous_root
    return rows


def fees_status_map(rows: list[dict]) -> dict[str, str]:
    """Map patient folder name -> fees Status (pure, never guesses)."""
    status_map: dict[str, str] = {}
    for row in rows or []:
        folder = str((row or {}).get("Patient Folder") or "").strip()
        if folder:
            status_map[folder] = str((row or {}).get("Status") or "").strip()
    return status_map


def final_bill_skip_reason(status: str) -> str:
    """Why a folder is NOT final-billed (shown in the workflow log)."""
    if status == "MATCH":
        return (
            "MATCH na ang itemized vs grouped charges — final bill "
            "na sa HBSys, hindi na kailangan."
        )
    if status == "MISMATCH":
        return (
            "MISMATCH ang itemized vs grouped charges — manual review "
            "(hindi na auto-final bill)."
        )
    if status == "NO RECORD":
        return "walang hpatcon1 record sa HBSys — manual review."
    if not status:
        return "walang fees check row para sa folder na ito — manual review."
    return f"fees status '{status}' ay hindi NO FINAL BILL — manual review."


def select_final_bill_folders(
    candidates: list[Path],
    status_map: dict[str, str],
) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Split marker-cleared folders into (pending, skipped).

    Only a fees Status of exactly NO FINAL BILL may run the Final
    Bill flow (operator rule 2026-10-08: the automation must not
    touch patients whose bill was already finalized, and MISMATCH
    rows queue for manual review instead of auto-re-finaling).
    Every other status — and any folder without a fees row — is
    skipped together with its status so the operator sees it in
    the workflow log.
    """
    pending: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    for folder in candidates:
        status = status_map.get(folder.name, "")
        if status == "NO FINAL BILL":
            pending.append(folder)
        else:
            skipped.append((folder, status))
    return pending, skipped


def write_marker(folder: Path, hospital_no: str, outcome: str, detail: str = "") -> bool:
    """Persist `.final_bill_ok` — ONLY for an OK outcome.

    Returns True when a marker was written. A BLOCKED/FAILED outcome never
    writes one (so the next run retries that patient) and never clobbers an
    existing marker. Writing errors propagate: a marker that cannot be
    written must surface as a failure, not silently skip a patient forever.
    """
    if outcome != OUTCOME_OK:
        return False
    payload = {
        "hospital_no": str(hospital_no),
        "outcome": outcome,
        "detail": str(detail or ""),
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    marker_path(folder).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return True


def run_patient(folder: Path, *, log_fn) -> tuple[str, str]:
    """Live: run the verified Final Bill flow for ONE patient folder.

    Returns (outcome, detail) — OUTCOME_OK / OUTCOME_BLOCKED / OUTCOME_FAILED
    exactly as the orchestrator reports them. The desktop automation loads
    inside this call path only, so a DRY run never touches the screen.
    """
    parsed = parse_folder_name(folder.name)
    if parsed is None:  # defensive: iter_patient_folders already filtered
        return OUTCOME_BLOCKED, "folder name does not match the patient folder contract"

    def step_log(message: str) -> None:
        log_fn(f"    {message}")

    return default_final_bill(
        folder.name,
        parsed.hospital_no,
        step_log,
        DEFAULT_TOOL_TIMEOUT,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry: parse args, pick pending folders, DRY-list or LIVE-run."""
    parser = argparse.ArgumentParser(
        prog="python -m core.agent.final_bill_runner",
        description="Final Bill every pending patient folder (Workflow Tab node).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually drive HBSys (default: DRY listing, no clicks)",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Patient output root (default: CLAIMS_OUTPUT_FOLDER, else C:\\claims_bot\\output)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N pending folders (0 = no limit)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            f"Re-run folders that already carry {MARKER_NAME} "
            "(the NO FINAL BILL status gate still applies)"
        ),
    )
    args = parser.parse_args(argv)

    output_root = Path(args.output_root) if args.output_root else default_output_root()
    if not output_root.is_dir():
        log(f"[ERROR] output root not found: {output_root} — check CLAIMS_OUTPUT_FOLDER")
        return EXIT_NOT_OK

    folders = iter_patient_folders(output_root)
    candidates = [
        f for f in folders if needs_final_bill(f, force=args.force)
    ]

    # Fees Check preflight (operator rule 2026-10-08): run the
    # check FIRST, silently (no CSV/XLSX — reports only come
    # from the main dashboard Fees Check), and bill ONLY the
    # patients whose fees Status is NO FINAL BILL. Already
    # final-billed (MATCH) and MISMATCH folders never reach
    # the Final Bill flow again. The check runs only when an
    # unmarked folder exists; a failed check STOPS the node —
    # a status is never guessed.
    status_map: dict[str, str] = {}
    if candidates:
        try:
            fees_rows = run_fees_check(output_root)
        except Exception as exc:  # noqa: BLE001 - stop, never guess.
            log(
                f"[ERROR] Fees Check preflight failed — cannot tell "
                f"which patients are NO FINAL BILL: {exc}"
            )
            return EXIT_NOT_OK
        status_map = fees_status_map(fees_rows)

    pending, skipped = select_final_bill_folders(candidates, status_map)
    if args.limit > 0:
        pending = pending[: args.limit]

    mode = "[LIVE]" if args.live else "[DRY]"
    log(
        f"{mode} {len(folders)} patient folder(s) under {output_root}, "
        f"{len(pending)} pending (NO FINAL BILL), {len(skipped)} skipped"
    )
    for folder, status in skipped:
        log(
            f"[SKIP] {folder.name} | fees status: "
            f"{status or '(walang row)'}"
            f" | {final_bill_skip_reason(status)}"
        )

    if not pending:
        log(
            "nothing to do — every patient folder either already "
            f"carries {MARKER_NAME} or its fees status is not "
            "NO FINAL BILL"
        )
        return EXIT_OK

    if not args.live:
        for folder in pending:
            parsed = parse_folder_name(folder.name)
            log(f"[DRY] {folder.name} | hospital no {parsed.hospital_no} | would run Final Bill")
        return EXIT_OK

    # LIVE: continue-on-error per folder (Agent Plan rule); fail-safe exit at
    # the end so the engine stops the chain when ANY patient needs attention.
    failures = 0
    for index, folder in enumerate(pending, start=1):
        parsed = parse_folder_name(folder.name)
        log(f"[LIVE] ({index}/{len(pending)}) {folder.name} | hospital no {parsed.hospital_no}")
        try:
            status, detail = run_patient(folder, log_fn=log)
        except KeyboardInterrupt:
            log("[LIVE] stopped by operator — finished folders stay marked, rest retried next run")
            return EXIT_NOT_OK
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — one patient must not kill the batch
            # SystemExit may come from a step that calls sys.exit()/raises
            # SystemExit (e.g. a confinement-mismatch surfacing via HBSys).
            # Log the full traceback so the exact mismatch is visible, then
            # continue to the NEXT patient instead of killing the whole
            # --live batch. (KeyboardInterrupt is handled above -> operator abort.)
            log(
                f"ERROR in {folder.name} ({type(exc).__name__}): {exc}\n"
                f"{traceback.format_exc()}"
            )
            status, detail = OUTCOME_FAILED, f"runner raised {type(exc).__name__}: {exc}"

        if status == OUTCOME_OK:
            log(f"[LIVE] OK | {detail or 'final bill committed'}")
            try:
                write_marker(folder, parsed.hospital_no, status, detail)
                log(f"[LIVE] marker written: {MARKER_NAME}")
            except OSError as exc:
                # The bill DID run — but without a marker the next run would
                # re-run it. Surface as failure so the operator decides.
                failures += 1
                log(f"[LIVE] FAILED to write marker ({exc}) — patient will re-run next time")
        else:
            failures += 1
            log(f"[LIVE] {status} | {detail}")

    if failures:
        log(f"[LIVE] {failures} of {len(pending)} patient(s) need attention")
        return EXIT_NOT_OK
    log(f"[LIVE] all {len(pending)} patient(s) OK")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

