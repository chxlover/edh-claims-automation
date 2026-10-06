"""Claims Agent Orchestrator (Slice E — Claims Agent, Phase 1 core).

Executes an approved Agent Plan deterministically: one row at a time, in
plan order, dispatched by the Slice B action verb to a per-action executor.

Dispatch (the row's stored action verb decides — no scoring, no guessing):

    date_fill     -> existing tool date_fill_hbsys/hbsys_fill_dates.py
                     run as `--live --hospital-no <N>` (exactly one patient)
    xml_clicker   -> existing tool date_fill_hbsys/xml_generator_clicker.py
                     run as `--live --hospital-no <N>` (exactly one patient)
    final_bill    -> core/agent/final_bill_actions.FinalBillRunner, driven end
                     to end per row: load the patient through the verified
                     Hospital No. lookup (double-click, CTRL+A, type, ENTER)
                     after closing a stale Billing form, pick the folder's
                     confinement from Admit History (exact-then-fuzzy, the
                     recipe Date Fill uses), then Final Bill -> "Final" box ->
                     OK -> No (or Save); the Billing form then STAYS OPEN and
                     the next patient's loader types the next Hospital No.
                     Any step that cannot be verified BLOCKs the row instead
                     of guessing.
    manual_review -> never executed; recorded as QUEUED for the human queue

Guarantees:

    * the hospital number comes from the patient folder through the SAME
      regex the tools use (hbsys_ready_claims.CLAIM_FOLDER_RE); an
      unparseable folder BLOCKS the row — never guessed.
    * only APPROVED rows execute; anything else is recorded SKIPPED.
    * continue-on-error: a failing row never stops the batch.
    * multi-patient final_bill rows reload the patient through the verified
      Hospital No. lookup and never type over the previous patient: the loader
      only reports success once a new "Billing (<patient>)" form is on screen.
    * every row gets a RowOutcome and the run saves
      logs/agent_run_YYYYMMDD_HHMMSS.json (JSON audit trail).
    * every executor is injectable — the module is fully headless-testable.

No Tkinter here: the Agent Plan panel calls run_approved_plan() from a
background thread and marshals its own UI updates.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.agent import agent_plan_store as plan_store
from core.agent import fees_actions as actions
from core.agent import plan_steps as step_plan
from core.agent.hbsys_screens import expected_prompt_screen
from date_fill_hbsys.hbsys_ready_claims import (
    CLAIM_FOLDER_RE,
    load_ready_claims,
    resolve_default_ready_dir,
)

BASE_DIR = Path(r"C:\claims_bot")
TOOLS_DIR = BASE_DIR / "date_fill_hbsys"
DATE_FILL_TOOL = TOOLS_DIR / "hbsys_fill_dates.py"
XML_CLICKER_TOOL = TOOLS_DIR / "xml_generator_clicker.py"
RUN_DIR = BASE_DIR / "logs"
RUN_PREFIX = "agent_run_"

# Row outcome vocabulary (stable — the Plan Panel + reports route on these).
OUTCOME_OK = "OK"
OUTCOME_BLOCKED = "BLOCKED"      # precondition not met — nothing attempted
OUTCOME_FAILED = "FAILED"        # attempted; the tool/runner reported failure
OUTCOME_QUEUED = "QUEUED"        # manual_review — human queue only
OUTCOME_SKIPPED = "SKIPPED"      # not part of the approved set

ALL_OUTCOMES = (
    OUTCOME_OK,
    OUTCOME_BLOCKED,
    OUTCOME_FAILED,
    OUTCOME_QUEUED,
    OUTCOME_SKIPPED,
)

# The outcomes that mean "this patient still needs a human". A row in one of
# these is skipped and the run moves on to the next selected patient, so the
# end-of-run list of them IS the operator's to-do list.
PROBLEM_OUTCOMES = (OUTCOME_BLOCKED, OUTCOME_FAILED)

DEFAULT_TOOL_TIMEOUT = 600  # seconds allowed per tool subprocess (one patient)
# With CLAIMS_AGENT_QUIET=1 in the tool environment, completion popups and
# auto-opened CSV run logs become plain stdout lines. The tools keep their
# existing popup behavior whenever the variable is unset.
QUIET_ENV = "CLAIMS_AGENT_QUIET"

# Every tool subprocess writes its full stdout/stderr here (one file per
# patient), so a failed row can always be explained from the report alone.
TOOL_LOG_PREFIX = "agent_tool_"


# -- Folder parsing (same regex the tools use) --------------------------------

def folder_match(folder: str):
    """CLAIM_FOLDER_RE match for one patient folder name, or None."""
    return CLAIM_FOLDER_RE.match(str(folder or "").strip())


def hospital_number_from_folder(folder: str) -> str:
    """Exact hospital number from the folder name; '' when unparseable."""
    match = folder_match(folder)
    return match.group("hospital_no") if match else ""


def patient_name_from_folder(folder: str) -> str:
    """Patient name from the folder name; '' when unparseable."""
    match = folder_match(folder)
    return match.group("name").strip() if match else ""


def confinement_dates_from_folder(folder: str) -> tuple:
    """(admission, discharge) as 'YYYYMMDD' strings from the folder name.

    Both are '' when the folder does not carry the ADM..._DIS... dates - the
    caller then skips confinement selection instead of guessing.
    """
    match = folder_match(folder)
    if not match:
        return "", ""
    return match.group("admission"), match.group("discharge")


def _one_line(text, limit: int = 300) -> str:
    """Collapse whitespace and truncate — for log lines, not for detail."""
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 3] + "..."



# -- Run report ---------------------------------------------------------------

@dataclass
class RowOutcome:
    """What happened to one approved plan row."""

    patient_folder: str
    action: str
    hospital_no: str = ""
    status: str = OUTCOME_SKIPPED
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "patient_folder": self.patient_folder,
            "action": self.action,
            "hospital_no": self.hospital_no,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class RunReport:
    """Outcome of one run_approved_plan() call (feeds logs + JSON audit)."""

    outcomes: list = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    saved_path: str = ""

    @property
    def counts(self) -> dict:
        counts = {status: 0 for status in ALL_OUTCOMES}
        for outcome in self.outcomes:
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
        return counts

    @property
    def summary_line(self) -> str:
        counts = self.counts
        return (
            f"{len(self.outcomes)} row(s): "
            f"OK {counts[OUTCOME_OK]} | "
            f"BLOCKED {counts[OUTCOME_BLOCKED]} | "
            f"FAILED {counts[OUTCOME_FAILED]} | "
            f"QUEUED {counts[OUTCOME_QUEUED]} | "
            f"SKIPPED {counts[OUTCOME_SKIPPED]}"
        )

    @property
    def problem_rows(self) -> list:
        """The rows that did NOT finish, in plan order (the operator's list).

        A blocked/failed row is skipped and the run continues to the next
        patient, so the counts alone never say WHICH patients still need
        attention. This is that list: hospital no + folder + one-line reason,
        so the operator does not have to open the JSON to find them.
        """
        return [
            outcome
            for outcome in self.outcomes
            if outcome.status in PROBLEM_OUTCOMES
        ]

    def problem_lines(self) -> list:
        """One log line per problem row, numbered, ready to print at the end."""
        lines = []
        for index, outcome in enumerate(self.problem_rows, start=1):
            identity = outcome.hospital_no or outcome.patient_folder
            lines.append(
                f"  {index}. {outcome.status} {identity} "
                f"[{outcome.patient_folder}] — "
                + _one_line(outcome.detail, limit=200)
            )
        return lines

    def save(self, run_dir=RUN_DIR) -> Path:
        """Persist the run as a JSON audit trail. Returns the path."""
        directory = Path(run_dir)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = directory / f"{RUN_PREFIX}{stamp}.json"
        payload = {
            "created_at": datetime.now().isoformat(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": self.counts,
            "problem_count": len(self.problem_rows),
            "problems": [
                {
                    "hospital_no": outcome.hospital_no,
                    "patient_folder": outcome.patient_folder,
                    "status": outcome.status,
                    "detail": outcome.detail,
                }
                for outcome in self.problem_rows
            ],
            "outcomes": [outcome.as_dict() for outcome in self.outcomes],
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self.saved_path = str(path)
        return path



# -- Default executors (the real tools; every dependency injectable) ----------

def _ready_queue() -> tuple:
    """Hospital numbers currently staged for the tools + the source dir."""
    source = resolve_default_ready_dir()
    try:
        claims = load_ready_claims(source)
    except OSError:
        return frozenset(), source
    return frozenset(claim.hospital_no for claim in claims), source


def _tool_output_lines(output: str) -> list:
    """Non-blank lines of one tool run (raw order kept for tail evidence)."""
    return [line.strip() for line in str(output or "").splitlines() if line.strip()]


def _tool_reason(output: str, limit: int = 400) -> str:
    """The one line of a tool run that says WHERE it went wrong.

    Preference order, so a failed row is diagnosable from the run report alone:
      1. `[STOP] ...`  - the tool's machine-readable stop record (patient, stage,
         reason, safe-reset, CSV path).
      2. the `Reason:` section of a `[POPUP]` message - the message is
         multi-line, so it is collected instead of cut at the first newline.
      3. the last non-blank line.
    """
    lines = _tool_output_lines(output)
    for line in lines:
        if line.startswith("[STOP]"):
            return _one_line(line, limit)
    reason = _popup_reason(lines)
    if reason:
        return _one_line(reason, limit)
    return _one_line(lines[-1], limit) if lines else ""


def _popup_reason(lines) -> str:
    """The 'Reason:' text inside a tool's [POPUP] message, collapsed to a line."""
    for index, line in enumerate(lines):
        if not line.startswith("[POPUP]"):
            continue
        collected = []
        for follow in lines[index + 1:]:
            if follow.startswith(
                ("[POPUP", "[STOP]", "[LIVE]", "[DRY]", "[OCR", "[EVIDENCE]", "Run log:")
            ):
                break
            collected.append(follow)
        text = " ".join(collected)
        if "Reason:" in text:
            text = text.split("Reason:", 1)[1]
        for trailer in ("CSV log saved here:", "Please review"):
            if trailer in text:
                text = text.split(trailer, 1)[0]
        text = " ".join(text.split())
        if text:
            return text
    return ""


def _tool_steps(output: str, count: int = 4) -> str:
    """The last few [LIVE]/[DRY] step lines - how far the tool actually got."""
    steps = [
        line
        for line in _tool_output_lines(output)
        if line.startswith(("[LIVE]", "[DRY]"))
    ]
    if not steps:
        return ""
    return " -> ".join(steps[-count:])


def save_tool_output(
    name: str, hospital_no: str, text: str, log_dir=None
) -> Path:
    """Persist one tool run's FULL stdout/stderr; returns the log file path.

    The run report detail only carries the reason + the tail, so the full log
    is where the operator (or the next run-review) reads the whole story.
    """
    directory = Path(log_dir or RUN_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_no = "".join(char for char in str(hospital_no) if char.isdigit())
    path = directory / (
        f"{TOOL_LOG_PREFIX}{Path(name).stem}_{safe_no or 'unknown'}_{stamp}.log"
    )
    path.write_text(str(text or ""), encoding="utf-8", errors="replace")
    return path


def _tool_detail(name: str, code, output: str, log_path) -> str:
    """One row detail that answers: which tool, which exit, WHY, how far, where.

    `reason` is the tool's own verdict (the [STOP] record, else the popup's
    Reason: text); `last steps` is the tail of its click/type trace, so the exact
    failing step is visible without opening the full log.
    """
    if code is None:
        head = f"{name} timed out"
    else:
        head = f"{name} exit {code}"
    reason = _tool_reason(output)
    steps = _tool_steps(output)
    parts = [head]
    if reason:
        parts.append(f"reason: {reason}")
    if steps:
        parts.append(f"last steps: {steps}")
    parts.append(f"full output: {log_path}")
    return " | ".join(parts)


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _run_tool(
    tool: Path, hospital_no: str, log_fn, timeout: int, log_dir=None
) -> tuple:
    """Run one existing tool for exactly one patient. Returns (status, detail).

    The FULL stdout/stderr of every run is written to
    logs/agent_tool_<tool>_<hospital>_<stamp>.log and the file is logged on
    its own line; the detail carries the tool's status line + the tail. A
    failed row is therefore diagnosable from the run report alone, without
    the operator re-running the patient to see what happened.
    """
    name = Path(tool).name
    log_fn(f"launch {name} --live --hospital-no {hospital_no} (cwd {Path(tool).parent})")
    env = dict(os.environ)
    env[QUIET_ENV] = "1"  # no modal popups / auto-opened CSVs in batch runs
    started = datetime.now()
    try:
        process = subprocess.run(
            [sys.executable, str(tool), "--live", "--hospital-no", hospital_no],
            cwd=str(tool.parent),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            ),
        )
    except subprocess.TimeoutExpired as exc:
        output = _as_text(getattr(exc, "output", ""))
        path = save_tool_output(name, hospital_no, output, log_dir=log_dir)
        log_fn(f"{name} timed out after {timeout}s — full output: {path}")
        return OUTCOME_FAILED, (
            f"{name} timed out after {timeout}s — review the HBSys screen "
            f"| reason: {_tool_reason(output) or 'no output yet'} "
            f"| full output: {path}"
        )
    except OSError as exc:
        return OUTCOME_FAILED, f"{name} could not start: {exc}"
    output = process.stdout or ""
    path = save_tool_output(name, hospital_no, output, log_dir=log_dir)
    seconds = int((datetime.now() - started).total_seconds())
    reason = _tool_reason(output) or "ok"
    if process.returncode != 0:
        log_fn(f"{name} exit {process.returncode} after {seconds}s — {reason}")
        log_fn(f"tool output saved: {path}")
        return OUTCOME_FAILED, _tool_detail(name, process.returncode, output, path)
    log_fn(f"{name} finished in {seconds}s — {reason}")
    log_fn(f"tool output saved: {path}")
    return OUTCOME_OK, _tool_detail(name, process.returncode, output, path)


def _default_date_fill(
    hospital_no, folder, log_fn, timeout, *, queue_fn=None, run_tool_fn=None
):
    """Existing Date Fill tool for one patient. Returns (status, detail)."""
    queue_fn = queue_fn or _ready_queue
    run_tool_fn = run_tool_fn or (
        lambda tool, hosp: _run_tool(tool, hosp, log_fn, timeout)
    )
    queued, source = queue_fn()
    if hospital_no not in queued:
        return OUTCOME_BLOCKED, (
            f"not in the Date Fill ready queue ({source}) — "
            "i-stage muna ang claim folder bago i-run"
        )
    status, detail = run_tool_fn(DATE_FILL_TOOL, hospital_no)
    if status == OUTCOME_OK and "No claims to process in source folder" in detail:
        return OUTCOME_BLOCKED, (
            "left the Date Fill queue before launch — re-stage, then re-run"
        )
    return status, detail


def _default_xml_clicker(
    hospital_no, folder, log_fn, timeout, *, queue_fn=None, run_tool_fn=None
):
    """Existing XML Clicker tool for one patient. Returns (status, detail)."""
    queue_fn = queue_fn or _ready_queue
    run_tool_fn = run_tool_fn or (
        lambda tool, hosp: _run_tool(tool, hosp, log_fn, timeout)
    )
    queued, source = queue_fn()
    if hospital_no not in queued:
        return OUTCOME_BLOCKED, (
            f"not in the XML Clicker ready queue ({source}) — "
            "i-stage muna ang claim folder bago i-run"
        )
    status, detail = run_tool_fn(XML_CLICKER_TOOL, hospital_no)
    if status == OUTCOME_OK and "No output folders to process" in detail:
        return OUTCOME_BLOCKED, (
            "left the XML queue before launch — re-stage, then re-run"
        )
    return status, detail


def final_bill_block_reason(folder: str, open_forms) -> str:
    """Pure precondition check for one Final Bill row. Returns '' only when
    the Billing form for THIS patient is open; otherwise the BLOCKED reason.

    The patient/confinement setup (operator manual steps 1-4) is never
    guessed — the row BLOCKs until the operator has done it.
    """
    from core.agent import final_bill_actions as final_bill  # lazy (pywinauto)

    name = patient_name_from_folder(folder)
    if not name:
        return "folder name has no patient name — cannot verify the Billing form"
    titles = [str(title).strip() for title in (open_forms or [])]
    billing = [
        title
        for title in titles
        if final_bill.is_billing_title(title)
    ]
    if not billing:
        return (
            "no Billing form open — search the Hospital No. and select the "
            "confinement period first (manual steps 1-4)"
        )
    wanted = final_bill.billing_form_for_patient(name)
    wanted_key = final_bill.billing_title_key(wanted)
    if wanted_key not in {final_bill.billing_title_key(title) for title in billing}:
        return (
            "Billing form is open for a different patient: "
            + ", ".join(billing)
            + f" — plan needs {wanted}"
        )
    return ""


def _default_final_bill(
    folder, hospital_no, log_fn, timeout, *, forms_fn=None, runner_fn=None,
    loader_fn=None, confinement_fn=None, expected_screen="", always_reload=False,
):
    """Verified FinalBillRunner for one patient. Returns (status, detail).

    The whole workflow runs, not just the popups, whenever this patient's
    Billing form is not the one on screen:

        loader_fn(hospital_no)    -> double-click the Hospital No. field,
                                     CTRL+A, type the number, ENTER, and
                                     verify a new Billing form appeared
        confinement_fn(adm, dis)  -> Admit History, exact-then-fuzzy row match
                                     (the same recipe Date Fill runs), then
                                     double-click that confinement
        runner_fn(...)            -> Final Bill -> "Final" box -> OK ->
                                     the post-OK prompt (File save: click the
                                     bottom OK; Call Administrator: click "No")

    Every step must report success; otherwise the row BLOCKs instead of
    billing whatever patient/confinement happened to be loaded. Re-running the
    loader per row is what makes a multi-patient batch safe - consecutive
    final_bill rows never type over each other.

    The Billing form is left OPEN after the post-OK prompt and the next
    patient's loader only types the next Hospital No. into that same form;
    the last patient simply leaves the form on screen.
    """
    from core.agent import final_bill_actions as final_bill  # lazy (pywinauto)

    forms_fn = forms_fn or final_bill.list_open_forms
    runner_fn = runner_fn or final_bill.run_final_bill

    load_notes: list = []

    def note_loader(message: str) -> None:
        # The loader explains itself ("stale form ...", "tooltip ...",
        # "no new Billing form ..."); the notes feed both the live log and
        # the BLOCKED detail, so the run report names the failing step.
        load_notes.append(message)
        log_fn(f"  load: {message}")

    if loader_fn is None:
        # Slice H: always_reload=True re-enters the Hospital No. even when this
        # patient's Billing form is already open (the operator's rule: type the
        # Hospital No. again before every step - it is the reference). The
        # loader then uses "relink" verification, because re-entering the SAME
        # Hospital No. may refresh that window instead of opening a second one.
        # `wanted` is read at CALL time (defined below), not at build time.
        # The new kwargs are passed ONLY in relink mode, so the default path
        # keeps the exact call signature it always had.
        def _loader(hosp, _wanted=lambda: wanted, _reload=always_reload):
            if _reload:
                return final_bill.load_patient_by_hospital_no(
                    hosp,
                    log_fn=note_loader,
                    verify_mode=final_bill.LOAD_VERIFY_RELINK,
                    expect_title=_wanted(),
                )
            return final_bill.load_patient_by_hospital_no(
                hosp, log_fn=note_loader
            )

        loader_fn = _loader

    confinement_notes: list = []

    def note_confinement(message: str) -> None:
        confinement_notes.append(message)
        log_fn(f"  admit history: {message}")

    if confinement_fn is None:
        # The real matcher logs which Admission History rows it saw and which
        # one it picked, so the run report carries the confinement evidence.
        confinement_fn = lambda admission, discharge: final_bill.select_confinement(  # noqa: E731
            admission, discharge, log_fn=note_confinement
        )

    name = patient_name_from_folder(folder)
    wanted = final_bill.billing_form_for_patient(name)
    admission, discharge = confinement_dates_from_folder(folder)

    # Load this patient whenever their Billing form is not already open. The
    # loader's verification is what proves the right patient is on screen.
    # always_reload (Slice H) types the Hospital No. again even when this
    # patient's form is already open — the operator's rule: the Hospital No. is
    # the reference, so it is entered before EVERY step.
    current_forms = [str(title).strip() for title in (forms_fn() or [])]
    # Titles are matched by normalized key, never byte-for-byte: HBSys can title
    # this same patient's form "Billing (VALENTINO, NIKKI )" (an empty middle
    # name leaves a space before ")") while the folder name carries none.
    current_keys = {final_bill.billing_title_key(title) for title in current_forms}
    already_open = bool(wanted) and final_bill.billing_title_key(wanted) in current_keys
    load_attempted = False
    load_ok = True
    if wanted and (always_reload or not already_open):
        if already_open:
            log_fn(
                f"re-entering hospital no {hospital_no}: {wanted} is already "
                "open — typing it again to confirm the patient on screen"
            )
        else:
            log_fn(
                f"loading patient in Billing form: hospital no {hospital_no} "
                f"({wanted})"
            )
        load_attempted = True
        try:
            load_ok = bool(loader_fn(hospital_no))
        except Exception as exc:
            load_ok = False
            note_loader(
                f"patient load raised: {type(exc).__name__}: {exc}"
            )
        if not load_ok:
            log_fn(
                "patient load not verified — the Billing form for this patient "
                "did not appear (the row blocks instead of guessing)"
            )
            # The loader already logged WHY; snapshot the screen once more so
            # the BLOCKED detail below is self-contained in the run report.
            try:
                for line in final_bill.diagnose_screen("final bill load").splitlines():
                    log_fn(f"  {line}")
            except Exception as exc:
                note_loader(f"screen diagnostics unavailable: {exc}")

    reason = final_bill_block_reason(folder, forms_fn())
    if reason:
        if load_attempted and not load_ok and load_notes:
            reason = f"{reason} | load: {' ; '.join(load_notes[-5:])}"
        return OUTCOME_BLOCKED, _one_line(reason, limit=1200)

    if admission and discharge:
        log_fn(f"selecting confinement period {admission} - {discharge}")
        try:
            picked = confinement_fn(admission, discharge)
        except Exception as exc:
            detail = f"confinement selection failed: {exc}"
            if confinement_notes:
                detail += f" | matcher: {' ; '.join(confinement_notes[-5:])}"
            return OUTCOME_BLOCKED, _one_line(detail, limit=1200)
        if not picked:
            detail = (
                f"confinement period {admission}-{discharge} not found in the "
                "Admit History list — pick it manually"
            )
            if confinement_notes:
                detail += f" | matcher: {' ; '.join(confinement_notes[-5:])}"
            return OUTCOME_BLOCKED, _one_line(detail, limit=1200)

    log_fn(f"final bill for {wanted} (hospital no {hospital_no})")
    result = runner_fn(
        # Keyed comparison, never byte-for-byte: HBSys titles this same
        # patient's form "Billing (VALENTINO, NIKKI )" when the middle name is
        # empty, and a raw "in" test would read that as "form not open".
        forms_open_fn=lambda: final_bill.billing_title_key(wanted)
        in {final_bill.billing_title_key(title) for title in forms_fn()},
        expected_screen=expected_screen,
        log_fn=log_fn,
    )
    steps = " -> ".join(str(step) for step in getattr(result, "actions", []) or [])
    if steps:
        log_fn(f"final bill steps: {steps}")
    detail = _one_line(getattr(result, "reason", "") or "", limit=400)
    if getattr(result, "success", False):
        return OUTCOME_OK, detail or "final bill committed"
    # A row that stopped keeps its own evidence: the HBSys windows that were
    # open when it stopped (the popup that swallowed the click, or the prompt
    # that never appeared). The step trail is already inside `reason`, so the
    # FAILED row in agent_run_*.json is diagnosable without re-running the
    # patient.
    try:
        windows = final_bill.hbsys_window_titles()
    except Exception:
        windows = []
    if windows:
        detail = _one_line(
            f"{detail} | windows: {'; '.join(windows)}", limit=1200
        )
    # ...plus the PNG of the screen the run stopped on: the only way to tell
    # "the prompt never appeared" from "the prompt is there and the click
    # missed it" without re-running the patient. Tests stub save_screenshot
    # to "", so no file is written outside a real session.
    try:
        shot = final_bill.save_screenshot("final_bill_stopped")
    except Exception:
        shot = ""
    if shot:
        detail = _one_line(f"{detail} | screenshot: {shot}", limit=1400)
    if getattr(result, "final_step", "") == final_bill.STEP_BLOCKED:
        return OUTCOME_BLOCKED, detail or "final bill flow blocked"
    return OUTCOME_FAILED, detail or "final bill run failed"


# Public alias: the Workflow Tab final_bill node (core/agent/final_bill_runner.py)
# runs this SAME implementation — one verified flow, two entry points, no
# duplicated logic (AGENTS.md: never duplicate code between GUI and core).
default_final_bill = _default_final_bill


# -- Orchestration ------------------------------------------------------------

def _record_completed(report, log, completed_path=None) -> None:
    """Write OK rows to the completion ledger (Slice F repeat guard).

    Never raises — a ledger problem must not fail a finished run.
    """
    pairs = [
        (outcome.patient_folder, outcome.action)
        for outcome in report.outcomes
        if outcome.status == OUTCOME_OK
    ]
    if not pairs:
        return
    try:
        added = plan_store.record_completed_actions(pairs, path=completed_path)
    except Exception as exc:  # noqa: BLE001 - audit must not break the run
        log(f"completed ledger update failed: {type(exc).__name__}: {exc}")
        return
    if added:
        log(
            f"completed ledger: +{added} row tapos na — "
            "hindi na uulitin sa susunod na plan load"
        )


# -- Run heartbeat (2026-09-28) ------------------------------------------------
# One heartbeat file, rewritten after every row, so a run that ends because the
# application closed (or was killed) still says exactly where it stopped. The
# JSON audit trail (agent_run_*.json) is written only at the END of a run.
#
# The name deliberately does NOT start with "agent_run_": the audit trail is
# read back with the glob agent_run_*.json, and a heartbeat must never be
# mistaken for a finished run report.

RUN_STATE_FILE = "agent_current_run.json"


def heartbeat_path(run_dir=RUN_DIR) -> Path:
    """Path of the current-run heartbeat file."""
    return Path(run_dir) / RUN_STATE_FILE


def write_run_heartbeat(payload: dict, path=None, run_dir=RUN_DIR) -> str:
    """Persist the current run state. Never raises (diagnostics only).

    Returns the path written, or '' when nothing could be written.
    """
    target = Path(path) if path else heartbeat_path(run_dir)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        body = dict(payload or {})
        body["updated_at"] = datetime.now().isoformat()
        body["pid"] = os.getpid()
        target.write_text(
            json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return str(target)
    except Exception:  # noqa: BLE001 - a heartbeat must not break a run
        return ""


def last_run_state(path=None, run_dir=RUN_DIR) -> dict:
    """Last heartbeat payload ({} when it is missing or unreadable)."""
    target = Path(path) if path else heartbeat_path(run_dir)
    try:
        return json.loads(target.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - a missing heartbeat is normal
        return {}


def run_approved_plan(
    items,
    *,
    date_fill_fn=None,
    xml_clicker_fn=None,
    final_bill_fn=None,
    log_fn=None,
    save: bool = True,
    run_dir=RUN_DIR,
    timeout: int = DEFAULT_TOOL_TIMEOUT,
    completed_path=None,
    state_path=None,
    order_steps: bool = True,
    always_reload: bool = False,
) -> RunReport:
    """Execute the approved plan rows in order; never stop at a bad row.

    Args:
        items: plan rows (agent_plan_store items). Only rows whose status is
            APPROVED execute; every other row is recorded SKIPPED.
        date_fill_fn / xml_clicker_fn / final_bill_fn: injectable executors,
            each called as fn(hospital_no, folder) -> (status, detail) where
            status is OUTCOME_OK / OUTCOME_BLOCKED / OUTCOME_FAILED.
            Missing ones default to the real tools (Date Fill, XML Clicker)
            and the verified FinalBillRunner — the final_bill executor runs
            in-process, so `timeout` does not apply to it.
        log_fn: called with human-readable progress lines.
        save / run_dir: persist the JSON audit trail (agent_run_*.json);
            when saving, OK rows also go to the completion ledger so the
            next plan load skips them (finished work never repeats).
        timeout: seconds allowed per tool subprocess (one patient).
        completed_path: completion ledger file (None =
            logs/agent_completed_actions.json).
        state_path: heartbeat file rewritten after every row (None =
            logs/agent_current_run.json). It names the row the run stopped on
            when the process ends before the run finishes.
        order_steps: group rows by patient and run FINAL BILL before DATE FILL
            for the same patient (Slice H). Ordering is STABLE, so a plan in
            which no patient needs two steps runs in exactly the same order as
            before. Set False to use the raw plan order.
        always_reload: type the Hospital No. again before EVERY step, even when
            this patient's Billing form is already open (Slice H; the operator's
            rule — the Hospital No. is the reference). Default False = the old
            behaviour, which skips the loader when the form is already open.

    Returns:
        A RunReport with one RowOutcome per input row, in execution order.
    """
    log = log_fn or (lambda message: None)
    # Slice H: same patient's FINAL BILL runs before its DATE FILL. Stable
    # sort — a plan where no patient needs two steps is byte-for-byte the same
    # execution order as before.
    if order_steps:
        items = step_plan.order_plan_items(items)
    date_fill_fn = date_fill_fn or (
        lambda hosp, folder: _default_date_fill(hosp, folder, log, timeout)
    )
    xml_clicker_fn = xml_clicker_fn or (
        lambda hosp, folder: _default_xml_clicker(hosp, folder, log, timeout)
    )
    # The plan's own "reason" names which prompt HBSys will raise for this row.
    # It travels through this holder so the executor contract stays
    # (hospital_no, folder) and every injected 2-arg executor keeps working.
    expected_screen = {"value": ""}
    final_bill_fn = final_bill_fn or (
        lambda hosp, folder: _default_final_bill(
            folder, hosp, log, timeout,
            expected_screen=expected_screen["value"],
            always_reload=always_reload,
        )
    )
    executors = {
        actions.ACTION_DATE_FILL: date_fill_fn,
        actions.ACTION_XML_CLICKER: xml_clicker_fn,
        actions.ACTION_FINAL_BILL: final_bill_fn,
    }

    # After OK/No the Billing form stays open; the next patient's loader types
    # the next Hospital No. into that same form and verifies the new
    # "Billing (<patient>)" window.

    report = RunReport(started_at=datetime.now().isoformat())
    state_file = Path(state_path) if state_path else heartbeat_path(run_dir)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    def _heartbeat(
        rows_done: int, outcome=None, state: str = "RUNNING", current=None,
        extra=None,
    ) -> None:
        payload = {
            "state": state,
            "run_id": run_id,
            "started_at": report.started_at,
            "rows_done": rows_done,
            "total_rows": len(items or []),
        }
        if outcome is not None:
            payload.update(
                {
                    "last_action": outcome.action,
                    "last_patient_folder": outcome.patient_folder,
                    "last_hospital_no": outcome.hospital_no,
                    "last_status": outcome.status,
                    "last_detail": _one_line(outcome.detail, limit=300),
                    "counts": report.counts,
                }
            )
        if current is not None:
            payload.update(current)
        if extra is not None:
            payload.update(extra)
        write_run_heartbeat(payload, path=state_file)

    _heartbeat(0)  # RUNNING, before the first row is touched
    for row_index, item in enumerate(items or []):
        item = item or {}
        folder = str(item.get("patient_folder") or "").strip()
        action = str(item.get("action") or "")
        hospital_no = hospital_number_from_folder(folder)
        outcome = RowOutcome(
            patient_folder=folder or "(unknown)",
            action=action,
            hospital_no=hospital_no,
        )

        if item.get("status") != plan_store.STATUS_APPROVED:
            # Approval is explicit — everything else never runs.
            outcome.status = OUTCOME_SKIPPED
            outcome.detail = "hindi kasama sa approved set — hindi tina-try"
        elif action == actions.ACTION_MANUAL_REVIEW:
            outcome.status = OUTCOME_QUEUED
            outcome.detail = (
                "manual review — para sa tao, hindi awtomatiko. "
                + str(item.get("reason") or "")
            ).strip()
        elif not item.get("tool_available", True):
            outcome.status = OUTCOME_BLOCKED
            outcome.detail = "tool unavailable — queue for review"
        elif action not in executors:
            outcome.status = OUTCOME_BLOCKED
            outcome.detail = f"unknown action {action!r} — never guessed"
        elif not hospital_no:
            outcome.status = OUTCOME_BLOCKED
            outcome.detail = (
                "folder name does not contain a hospital number — never guessed"
            )
        else:
            log(f"run {action}: {outcome.patient_folder}")
            # Slice H: advisory only — never blocks. Says out loud when this
            # patient's FINAL BILL row is in the same plan, so the operator
            # can see why DATE FILL ran anyway (Hospital No. is the reference).
            chain_note = step_plan.prerequisite_note_for_plan(
                action, item, items
            )
            if chain_note:
                log("  note: " + _one_line(chain_note))
            # Name the row being ATTEMPTED, so a process that dies inside the
            # executor still says which patient it was working on.
            _heartbeat(
                len(report.outcomes),
                current={
                    "current_index": len(report.outcomes) + 1,
                    "current_action": action,
                    "current_patient_folder": outcome.patient_folder,
                    "current_hospital_no": hospital_no,
                },
            )
            try:
                if action == actions.ACTION_FINAL_BILL:
                    expected_screen["value"] = expected_prompt_screen(
                        item.get("reason")
                    )
                    if expected_screen["value"]:
                        log(
                            f"  plan reason predicts the "
                            f"{expected_screen['value']} prompt: "
                            f"{_one_line(str(item.get('reason') or ''), limit=90)}"
                        )
                    # After OK/No the Billing form stays open; the next patient's
                    # loader types the next Hospital No. into that same form.
                    log(
                        "  final bill: form stays open after OK/No - the next "
                        "selected patient's loader types the Hospital No."
                    )
                status, detail = executors[action](hospital_no, folder)
            except (Exception, SystemExit) as exc:  # noqa: BLE001 - report, keep going
                # Full traceback so a confinement-mismatch / sys.exit() that
                # escapes a step is diagnosable in the run log (the one-liner
                # below still travels to agent_run_*.json as the row detail).
                log(
                    f"ERROR in {action} for {outcome.patient_folder} "
                    f"({type(exc).__name__}): {exc}\n{traceback.format_exc()}"
                )
                status, detail = OUTCOME_FAILED, f"{type(exc).__name__}: {exc}"
            outcome.status = (
                status
                if status in (OUTCOME_OK, OUTCOME_BLOCKED, OUTCOME_FAILED)
                else OUTCOME_FAILED
            )
            outcome.detail = str(detail or "")
            if chain_note:
                # Keep the advisory in the JSON audit trail too, not only the log.
                outcome.detail = f"{outcome.detail} | {chain_note}".strip(" |")
            log(
                f"{outcome.status}: {outcome.patient_folder} — "
                + _one_line(outcome.detail)
            )

        report.outcomes.append(outcome)
        # Heartbeat after every row: if the app disappears mid-run, this file
        # names the row it stopped on (the JSON audit trail is end-of-run only).
        _heartbeat(len(report.outcomes), outcome)

    report.finished_at = datetime.now().isoformat()
    log(report.summary_line)
    # Operator rule: a problem row never stops the run, but the run must END
    # with the names of the patients that still need a human. Without this the
    # counts ("BLOCKED 2 | FAILED 1") never say WHICH patients, and the
    # operator has to open the JSON to find out who was skipped.
    problems = report.problem_rows
    if problems:
        log(
            f"NOT FINALIZED - {len(problems)} patient(s) skipped, "
            "fix these by hand then re-run them:"
        )
        for line in report.problem_lines():
            log(line)
    else:
        log("all selected patients were finalized")
    if save:
        log(f"run report saved: {report.save(run_dir)}")
        _record_completed(report, log, completed_path)
    _heartbeat(
        len(report.outcomes),
        report.outcomes[-1] if report.outcomes else None,
        state="FINISHED",
        extra={
            "finished_at": report.finished_at,
            "saved_path": report.saved_path,
            "summary": report.counts,
        },
    )
    return report