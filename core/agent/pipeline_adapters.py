"""Minimal adapters around the existing working modules.

Each adapter reuses the real entry point (no logic duplicated) and returns
``(ok, detail, extra)`` so the orchestrator can record stage results
without knowing module internals. Heavy GUI modules are imported LAZILY
inside the adapter functions, so unit tests and dry runs never touch
HBSys, pyautogui, or the live database.

Real contracts reused (verified against source):
    * Claim Processor — subprocess ``bot_unknown_trainer_..._ADM_DIS.py``
      with ``CLAIMS_PROCESS_MODE=multiple`` + ``CLAIMS_CONFIRM_PATIENT=0``
      (mirrors ``core/workflow_registry.py`` claims_processor node).
    * Fees — ``fees_checker.run_check(write_reports=False)`` then
      ``ready_to_generate_xml(row)`` -> ("YES"/"NO", problems).
    * Date Fill — ``HbsysOperator.process_claim(claim)`` -> "VERIFIED" on
      success, "SKIPPED_*"/"CRITICAL_*" otherwise.
    * Final Bill — ``orchestrator.default_final_bill`` -> (OUTCOME_*, detail).
    * XML Clicker — ``XmlGeneratorOperator.process_claim`` -> "done".
    * Claims check — ``claims_checker.check_patient_folder`` (read-only;
      NEVER ``organize_claim_folders``, which moves folders).
    * Add Claims — ``AddClaimsOperator`` per-patient methods
      (search -> read_and_verify -> click checkbox); batch finalize
      click_add/click_ok/click_close runs once via ``finalize_add_claims``.
    * Attachments — ``AttachmentsOperator`` per-patient methods
      (search -> click_attach_on_highlighted_row ->
      assign_doc_types_and_upload); verification below.
    * Attachment Verification — ``audit_claim_series`` (READ-ONLY scan of
      the eClaimsDoc destination; the module itself is never modified).

Standalone test:
    python core/agent/pipeline_adapters.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time as _time
from pathlib import Path

BASE_DIR = Path(r"C:\claims_bot")
ENGINE_MODULE = "bot_unknown_trainer_DEFERRED_OUTPUT_REVIEW_PATSUFFIX_ADM_DIS.py"

DATE_FILL_OK = "VERIFIED"
XML_CLICKER_OK = "done"


def _log_nop(message: str) -> None:
    pass


# -- Claim Processor (batch subprocess) ------------------------------------


def run_claim_processor_once(
    scan_folder: Path,
    output_folder: Path,
    log_fn=None,
) -> tuple[bool, str, dict]:
    """Run the production engine once; report which output folders are new.

    The engine (OCR over scans) can run for many minutes, so its output
    is STREAMED line-by-line through ``log_fn`` instead of only reported
    at the end — otherwise the batch looks dead while the engine works.
    """
    log = log_fn or _log_nop
    before = {p.name for p in output_folder.iterdir() if p.is_dir()} \
        if output_folder.is_dir() else set()
    env = dict(os.environ)
    env["CLAIMS_PROCESS_MODE"] = "multiple"
    env["CLAIMS_CONFIRM_PATIENT"] = "0"
    log(f"claim processor starting: {ENGINE_MODULE} "
        f"(scans={scan_folder}, timeout=60min)")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(BASE_DIR / ENGINE_MODULE)],
            cwd=str(BASE_DIR),
            env=env,
            stdin=subprocess.DEVNULL,  # never wait on a prompt silently:
            stdout=subprocess.PIPE,    # an input() inside fails fast instead
            stderr=subprocess.STDOUT,  # of hanging the batch forever
            text=True,
        )
    except OSError as exc:
        return False, f"claim processor could not start: {exc}", {}
    collected: list[str] = []
    state = {"last_output": _time.monotonic(), "done": False}
    stop_watch = threading.Event()

    def _watchdog() -> None:
        """Heartbeat while the engine is quiet: proves the batch is alive
        and shows whether the engine itself went silent (stuck prompt vs
        slow OCR). Daemon; stops when the run ends."""
        while not stop_watch.wait(120):
            if state["done"]:
                return
            quiet = _time.monotonic() - state["last_output"]
            log(f"claim processor | ... still running "
                f"(no engine output for {int(quiet)}s — "
                f"{'likely slow OCR/PDF work' if quiet < 600 else 'POSSIBLY STUCK: check CPU of the engine process'})")

    watch = threading.Thread(target=_watchdog, daemon=True)
    watch.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            text = line.rstrip()
            if text:
                collected.append(text)
                state["last_output"] = _time.monotonic()
                log(f"claim processor | {text}"[:300])
        proc.stdout.close()
        code = proc.wait(timeout=3600)
    except subprocess.SubprocessError as exc:
        try:
            proc.kill()
        except Exception:
            pass
        return False, f"claim processor failed: {exc}", {}
    except Exception as exc:  # noqa: BLE001 - never hang the batch silently
        try:
            proc.kill()
        except Exception:
            pass
        return False, f"claim processor error: {exc}", {}
    finally:
        state["done"] = True
        stop_watch.set()
    after = {p.name for p in output_folder.iterdir() if p.is_dir()} \
        if output_folder.is_dir() else set()
    new_folders = sorted(after - before)
    tail = "\n".join(collected[-8:])[-500:]
    if code != 0:
        detail = tail or "unknown engine error"
        return False, f"claim processor exit={code}: {detail}", {
            "new_folders": new_folders,
        }
    log(f"claim processor done (exit=0); {len(new_folders)} new folder(s)")
    return True, tail or f"{len(new_folders)} new folder(s)", {
        "new_folders": new_folders,
    }


def discover_claim_folders(output_folder: Path) -> list[Path]:
    """Patient folders currently in the output folder (sorted, stable)."""
    if not output_folder.is_dir():
        return []
    return sorted(
        (p for p in output_folder.iterdir() if p.is_dir()),
        key=lambda p: p.name.upper(),
    )


# -- Fees Checker (batch, read-only) ----------------------------------------


def fees_snapshot(output_folder: Path) -> tuple[dict[str, dict], str]:
    """Run the existing Fees Checker; index rows by patient folder name.

    Returns (rows_by_folder, error). ``error`` is "" on success — even when
    individual rows say NO (a business verdict, not an execution error).
    """
    sys.path.insert(0, str(BASE_DIR))
    try:
        import fees_checker  # noqa: E402
    except Exception as exc:  # noqa: BLE001 - import must not kill the batch
        return {}, f"fees checker import failed: {exc}"
    try:
        rows, _, _ = fees_checker.run_check(write_reports=False)
    except Exception as exc:  # noqa: BLE001 - technical execution error
        return {}, f"fees checker execution error: {exc}"
    indexed: dict[str, dict] = {}
    for row in rows:
        name = str(row.get("Patient Folder", ""))
        if name:
            indexed[name] = row
    return indexed, ""


def fees_ready_for_folder(
    rows_by_folder: dict[str, dict], folder_name: str
) -> tuple[str, str]:
    """Exact YES/NO verdict for one folder (never guessed).

    Returns ("YES"/"NO", problems) via the real ``ready_to_generate_xml``,
    or ("ERROR", reason) when the folder has no fees row (technical gap,
    distinct from a legitimate NO).
    """
    sys.path.insert(0, str(BASE_DIR))
    try:
        from fees_checker import ready_to_generate_xml  # noqa: E402
    except Exception as exc:  # noqa: BLE001
        return "ERROR", f"fees checker import failed: {exc}"
    row = rows_by_folder.get(folder_name)
    if row is None:
        return "ERROR", "no fees checker row for this folder"
    try:
        verdict, problems = ready_to_generate_xml(row)
    except Exception as exc:  # noqa: BLE001
        return "ERROR", f"fees verdict error: {exc}"
    return verdict, problems


# -- Date Fill / Final Bill / XML Clicker (per-claim, HBSys-touching) -------


def _ready_claim_for(folder: Path):
    sys.path.insert(0, str(BASE_DIR / "date_fill_hbsys"))
    sys.path.insert(0, str(BASE_DIR))
    from hbsys_ready_claims import parse_ready_claim_folder  # noqa: E402

    return parse_ready_claim_folder(folder)


def date_fill_for_patient(
    folder: Path, log_fn=None, claim_type: str = "REGULAR"
) -> tuple[bool, str, dict]:
    """Run existing Date Fill for one folder. Success == "VERIFIED"."""
    claim = _ready_claim_for(folder)
    if claim is None:
        return False, "folder name is not a parseable patient folder", {}
    from date_fill_hbsys.hbsys_fill_dates_testing import (  # noqa: E402
        HbsysOperator,
    )

    operator = HbsysOperator(live=True, pause=0.35, confirm_each=False)
    if claim_type.upper() == "ABTC":
        operator.claim_type = "ABTC"
    try:
        status = operator.process_claim(claim)
    except Exception as exc:  # noqa: BLE001 - GUI/OCR failure
        return False, f"date fill raised: {exc}", {}
    if status == DATE_FILL_OK:
        return True, status, {}
    return False, status, {}


def final_bill_for_patient(
    folder: Path, log_fn=None
) -> tuple[bool, str, dict]:
    """Run existing Final Bill flow. Success == orchestrator OUTCOME_OK."""
    from core.agent import orchestrator  # noqa: E402

    log = log_fn or _log_nop
    try:
        outcome, detail = orchestrator.default_final_bill(
            str(folder), "", log, 600
        )
    except Exception as exc:  # noqa: BLE001 - GUI failure
        return False, f"final bill raised: {exc}", {}
    if outcome == orchestrator.OUTCOME_OK:
        return True, detail, {}
    return False, f"{outcome}: {detail}", {"outcome": outcome}


def xml_clicker_for_patient(
    folder: Path, kinds: set[str] | None = None, log_fn=None
) -> tuple[bool, str, dict]:
    """Run existing XML Clicker for one folder. Success == "done".

    A "done" click is NOT proof files exist — the agent verifies with
    ``find_existing_xml_kinds`` afterwards (see claims_agent).
    """
    claim = _ready_claim_for(folder)
    if claim is None:
        return False, "folder name is not a parseable patient folder", {}
    from date_fill_hbsys.xml_generator_clicker import (  # noqa: E402
        XmlGeneratorOperator,
    )

    operator = XmlGeneratorOperator(live=True, pause=0.35, confirm_each=False)
    try:
        status = operator.process_claim(claim, kinds_to_process=kinds)
    except Exception as exc:  # noqa: BLE001 - GUI failure
        return False, f"xml clicker raised: {exc}", {}
    if status == XML_CLICKER_OK:
        return True, status, {}
    return False, status, {}


# -- Claims check (read-only) ------------------------------------------------


def claims_check_for_folder(folder: Path) -> tuple[str, dict]:
    """Run the existing Claims Checker on one folder (no moves).

    Returns (status, row) where status is exactly one of
    "READY" | "READY WITH REVIEW" | "INCOMPLETE".
    """
    sys.path.insert(0, str(BASE_DIR))
    import claims_checker  # noqa: E402

    row = claims_checker.check_patient_folder(folder)
    return str(row.get("Status", "INCOMPLETE")), row


# -- HBSys Add Claims / Attachments (per-patient operator reuse) ------------


def add_claims_for_patient(
    folder_name: str, patient_name: str, log_fn=None
) -> tuple[bool, str, dict]:
    """Search + verify + check one patient in Upload Claims.

    Mirrors the per-patient body of ``run_upload_loop`` (same methods, same
    order). Batch finalize (Add/OK/Close) is separate: ``finalize_add_claims``.
    """
    sys.path.insert(0, str(BASE_DIR))
    from core.add_claims_uploader import (  # noqa: E402
        AddClaimsOperator,
        load_patients,
    )
    from core.add_claims_state import UploadState  # noqa: E402

    _ = (load_patients, UploadState)  # reused indirectly; kept explicit
    operator = AddClaimsOperator(live=True, pause=0.35, confirm_each=False)
    try:
        operator.search_patient(patient_name)
        verify = operator.read_and_verify_highlighted_row(patient_name)
        checked = operator.click_checkbox_of_highlighted_row(patient_name)
        operator.clear_search_box()
    except Exception as exc:  # noqa: BLE001 - GUI/OCR failure
        return False, f"add claims raised: {exc}", {"uncertain": True}
    if not checked:
        return False, "checkbox click failed", {"uncertain": False}
    if not verify.match:
        return True, f"checked (verify informational: {verify.reason})", {
            "verify_match": False
        }
    return True, "checked + verified", {"verify_match": True}


def finalize_add_claims(log_fn=None) -> tuple[bool, str]:
    """Batch finalize: click Add -> OK -> Close (once per batch)."""
    sys.path.insert(0, str(BASE_DIR))
    from core.add_claims_uploader import AddClaimsOperator  # noqa: E402

    operator = AddClaimsOperator(live=True, pause=0.35, confirm_each=False)
    try:
        operator.click_add()
        ok_clicked = operator.click_ok()
        operator.click_close()
    except Exception as exc:  # noqa: BLE001 - GUI failure
        return False, f"finalize raised: {exc}"
    if not ok_clicked:
        return False, "OK confirmation not confirmed"
    return True, "Add/OK/Close finalized"


def attachments_for_patient(
    folder_path: str, patient_name: str, log_fn=None
) -> tuple[bool, str, dict]:
    """Attach one patient's files + assign doc types (existing operator).

    Mirrors the per-patient body of ``run_attachments_loop`` (same methods,
    same order). Returns (ok, detail, {"claim_series": ...}).
    """
    sys.path.insert(0, str(BASE_DIR))
    from core.claim_attachments_uploader import (  # noqa: E402
        AttachmentsOperator,
    )

    operator = AttachmentsOperator(live=True, pause=0.35, confirm_each=False)
    try:
        if not operator.search_patient(patient_name):
            return False, "patient search failed", {"uncertain": False}
        if not operator.click_attach_on_highlighted_row():
            return False, "attach dialog did not open", {"uncertain": True}
        uploaded = operator.assign_doc_types_and_upload(folder_path)
        series = operator._current_claim_series()
        source_records = list(getattr(operator.source_audit, "records", []))
    except Exception as exc:  # noqa: BLE001 - GUI failure
        return False, f"attachments raised: {exc}", {"uncertain": True}
    if not uploaded:
        return False, "doc-type assign/upload reported failure", {"uncertain": True}
    return True, "attached + doc types assigned", {
        "claim_series": series,
        "source_records": source_records,
    }


# -- Attachment Verification (existing interface only) -----------------------


def verify_attachments_for_series(
    claim_series: str,
    source_records: list | None = None,
    dest_root: Path | None = None,
) -> tuple[bool, str, dict]:
    """Invoke the EXISTING Attachment Verification (read-only).

    ``audit_claim_series`` scans the eClaimsDoc destination and compares
    with source records. Verified == folder present AND every row MATCH.
    The verification module itself is never modified here.
    """
    sys.path.insert(0, str(BASE_DIR))
    from core.claim_attachments_doctype_audit import (  # noqa: E402
        ECLAIMS_DOC_ROOT,
        audit_claim_series,
    )

    root = dest_root or ECLAIMS_DOC_ROOT
    try:
        audit = audit_claim_series(source_records or [], claim_series, dest_root=root)
    except Exception as exc:  # noqa: BLE001
        return False, f"verification error: {exc}", {"uncertain": True}
    if audit.folder_missing:
        return False, "destination folder not found", {"uncertain": True}
    counts: dict[str, int] = {}
    for row in audit.results:
        counts[row.status] = counts.get(row.status, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no rows"
    if audit.results and all(row.status == "MATCH" for row in audit.results):
        return True, f"verified: {summary}", {"counts": counts}
    return False, f"not verified: {summary}", {"counts": counts}


if __name__ == "__main__":
    assert DATE_FILL_OK == "VERIFIED"
    assert XML_CLICKER_OK == "done"
    print("pipeline_adapters standalone check passed")
