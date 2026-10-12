"""Claims Agent — deterministic orchestrator for the full claims pipeline.

Coordinates the existing working modules (via ``pipeline_adapters``) through
the required sequence; never duplicates their business logic:

PENDING -> CLAIM_PROCESSING -> FEES_CHECK_1 -> DATE_FILL -> FINAL_BILL
  -> FEES_CHECK_2 --(NO, bounded loop)--> DATE_FILL ...
  -> XML_GENERATION -> XML_VALIDATION -> CLAIMS_CHECK
  -> HBSYS_ADD_CLAIMS -> ATTACHMENTS -> ATTACHMENT_VERIFICATION
  -> COMPLETED

Only exact checker status ``READY`` (``claims_checker.check_patient_folder``)
plus three valid XMLs may proceed to HBSys attachments. ``INCOMPLETE``
holds for correction; ``READY WITH REVIEW`` holds for review — both pass
through untouched, never converted.

Every stage function is injectable, so tests run headless with fakes and
production uses the real adapters. One failed claim never stops unrelated
claims. Checkpoints go to ``PipelineStore`` after every stage.

Standalone test:
    python core/agent/claims_agent.py
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.activity_logger import logger  # noqa: E402
from core.agent import pipeline_adapters as adapters  # noqa: E402
from core.agent.pipeline_config import PipelineConfig, load_config  # noqa: E402
from core.agent.pipeline_state import ClaimRecord, PipelineStore  # noqa: E402
from core.agent.xml_validation import (  # noqa: E402
    XmlValidationResult,
    validate_patient_xmls,
)

# -- stage order (resume compares by index) ----------------------------------

STAGES = (
    "PENDING",
    "CLAIM_PROCESSING",
    "FEES_CHECK_1",
    "DATE_FILL",
    "FINAL_BILL",
    "FEES_CHECK_2",
    "XML_GENERATION",
    "XML_VALIDATION",
    "CLAIMS_CHECK",
    "HBSYS_ADD_CLAIMS",
    "ATTACHMENTS",
    "ATTACHMENT_VERIFICATION",
    "COMPLETED",
)
_STAGE_INDEX = {name: i for i, name in enumerate(STAGES)}

# -- outcomes -----------------------------------------------------------------

OUTCOME_COMPLETED = "COMPLETED"
OUTCOME_INCOMPLETE = "INCOMPLETE"
OUTCOME_HELD_FOR_REVIEW = "HELD_FOR_REVIEW"
OUTCOME_FAILED = "FAILED"
OUTCOME_PENDING = "PENDING"

CHECKER_READY = "READY"
CHECKER_REVIEW = "READY WITH REVIEW"  # exact repo string (spaces, not underscores)
CHECKER_INCOMPLETE = "INCOMPLETE"

_TRANSIENT_WORDS = (
    "timeout",
    "timed out",
    "locked",
    "busy",
    "not responding",
    "connection",
    "retry",
    "temporarily",
)


def classify_failure(detail: str, business_no: bool = False) -> str:
    """Failure class for logging/next-action (retry budget is stage-owned).

    business-rule failure (a legitimate NO) | missing-or-invalid document |
    temporary execution failure | permanent/config error | uncertain outcome.
    """
    text = str(detail or "").lower()
    if "uncertain" in text or "verify in hbsys" in text:
        return "uncertain outcome requiring verification"
    if business_no:
        return "business-rule failure"
    if any(word in text for word in _TRANSIENT_WORDS):
        return "temporary execution failure"
    if "missing" in text or "not found" in text or "empty" in text:
        return "missing or invalid document"
    return "permanent configuration or input error"


@dataclass
class BatchSummary:
    batch_id: str
    total: int = 0
    completed: int = 0
    incomplete: int = 0
    held_for_review: int = 0
    failed: int = 0
    pending: int = 0
    xml_generation_failures: int = 0
    xml_validation_failures: int = 0
    attachment_failures: int = 0
    needs_manual_action: int = 0

    def as_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "total_claims": self.total,
            "completed": self.completed,
            "incomplete": self.incomplete,
            "held_for_review": self.held_for_review,
            "failed": self.failed,
            "pending": self.pending,
            "xml_generation_failures": self.xml_generation_failures,
            "xml_validation_failures": self.xml_validation_failures,
            "attachment_failures": self.attachment_failures,
            "needs_manual_action": self.needs_manual_action,
        }


class ClaimsAgent:
    """Deterministic per-claim pipeline runner with checkpoints and resume."""

    def __init__(
        self,
        config: PipelineConfig | None = None,
        store: PipelineStore | None = None,
        log_fn=None,
        # -- injectable stage functions (defaults = real adapters) ---------
        claim_processor_fn=None,
        fees_snapshot_fn=None,
        fees_verdict_fn=None,
        date_fill_fn=None,
        final_bill_fn=None,
        xml_clicker_fn=None,
        claims_check_fn=None,
        xml_validate_fn=None,
        add_claims_fn=None,
        finalize_add_claims_fn=None,
        attachments_fn=None,
        verify_fn=None,
    ) -> None:
        self.config = config or load_config()
        self.store = store or PipelineStore(self.config.state_db)
        self.log_fn = log_fn or logger.info
        self.claim_processor_fn = claim_processor_fn or adapters.run_claim_processor_once
        self.fees_snapshot_fn = fees_snapshot_fn or adapters.fees_snapshot
        self.fees_verdict_fn = fees_verdict_fn or adapters.fees_ready_for_folder
        self.date_fill_fn = date_fill_fn or adapters.date_fill_for_patient
        self.final_bill_fn = final_bill_fn or adapters.final_bill_for_patient
        self.xml_clicker_fn = xml_clicker_fn or adapters.xml_clicker_for_patient
        self.claims_check_fn = claims_check_fn or adapters.claims_check_for_folder
        self.xml_validate_fn = xml_validate_fn or validate_patient_xmls
        self.add_claims_fn = add_claims_fn or adapters.add_claims_for_patient
        self.finalize_add_claims_fn = (
            finalize_add_claims_fn or adapters.finalize_add_claims
        )
        self.attachments_fn = attachments_fn or adapters.attachments_for_patient
        self.verify_fn = verify_fn or adapters.verify_attachments_for_series
        self._fees_cache: dict[str, dict] = {}
        self._fees_error: str = ""
        self._added_claim_keys: list[str] = []

    # -- logging -----------------------------------------------------------

    def _log(self, batch_id: str, claim_key: str, stage: str, message: str) -> None:
        line = f"[agent][{batch_id}][{claim_key}][{stage}] {message}"
        try:
            self.log_fn(line)
        except Exception:
            pass

    def _stage_log(self, batch_id: str, claim_key: str, stage: str):
        """Single-argument log adapter: existing stage functions call
        ``log_fn(message)``, while the agent log needs batch/claim/stage."""
        return lambda message: self._log(
            batch_id, claim_key, stage, str(message))

    # -- checkpoint helpers -------------------------------------------------

    def _save(self, record: ClaimRecord) -> None:
        self.store.upsert_claim(record)

    def _record(
        self,
        record: ClaimRecord,
        stage: str,
        status: str,
        detail: str = "",
        completed: bool = False,
        extra: dict | None = None,
    ) -> None:
        record.attempts += 1
        entry: dict = {"status": status, "detail": detail[:500]}
        if extra:
            entry.update(extra)
        record.stage_status[stage] = entry
        if completed:
            record.last_completed_stage = stage
        record.stage = stage
        self._save(record)

    def _already_done(self, record: ClaimRecord, stage: str) -> bool:
        """True when a resume must NOT rerun this stage."""
        return _STAGE_INDEX.get(record.last_completed_stage, -1) >= _STAGE_INDEX[stage]

    # -- batch ---------------------------------------------------------------

    def run_batch(self, batch_id: str = "") -> BatchSummary:
        """Run (or resume) one batch. Never raises for per-claim failures."""
        resumed_from = ""
        if not batch_id:
            batch_id = self.config.batch_id
        # Resume rule: without an explicit batch id, continue the latest
        # open batch instead of starting a duplicate one.
        if not batch_id and self.config.resume:
            latest = self.store.latest_open_batch()
            if latest:
                batch_id = latest
                resumed_from = latest
        if not batch_id:
            batch_id = time.strftime("%Y%m%d_%H%M%S")
        self.store.open_batch(batch_id)
        self._log(batch_id, "-", "-", f"batch start (resume={self.config.resume})")
        if resumed_from:
            self._log(batch_id, "-", "-", f"resumed from open batch {resumed_from}")

        problems = self.config.problems()
        summary = BatchSummary(batch_id=batch_id)
        if problems:
            self._log(batch_id, "-", "-", f"config invalid: {'; '.join(problems)}")
            self.store.close_batch(batch_id, "config_error")
            return summary

        # Stage 1 (batch): claim processor, once. Skip on resume when the
        # batch already tracks claims AND no new PDFs wait in scans.
        known = self.store.claim_keys(batch_id)
        waiting_pdfs = (
            [p for p in self.config.scan_folder.glob("*.pdf") if p.is_file()]
            if self.config.scan_folder.is_dir()
            else []
        )
        if known and not waiting_pdfs:
            self._log(batch_id, "-", "CLAIM_PROCESSING",
                       f"skipped (resume): {len(known)} claim(s) already tracked")
        else:
            self._log(batch_id, "-", "CLAIM_PROCESSING",
                       f"starting engine: {len(known)} tracked, "
                       f"{len(waiting_pdfs)} waiting PDF(s) in scans")
            try:
                ok, detail, _extra = self.claim_processor_fn(
                    self.config.scan_folder, self.config.output_folder,
                    self._stage_log(batch_id, "-", "CLAIM_PROCESSING"),
                )
            except Exception as exc:  # noqa: BLE001 - adapter must not kill batch
                ok, detail = False, f"claim processor raised: {exc}"
            self._log(batch_id, "-", "CLAIM_PROCESSING",
                       f"{'OK' if ok else 'FAILED'}: {detail[:300]}")
            if not ok:
                self._log(batch_id, "-", "-",
                           "processor failed; continuing with folders on disk")

        folders = adapters.discover_claim_folders(self.config.output_folder)
        for folder in folders:
            key = folder.name
            record = self.store.get_claim(batch_id, key)
            if record is None:
                record = ClaimRecord(
                    batch_id=batch_id, claim_key=key, folder_name=key,
                    output_path=str(folder),
                )
                self._save(record)
        summary.total = len(folders)
        self._log(batch_id, "-", "-", f"{len(folders)} claim folder(s) discovered")

        # Batch-level fees snapshot (refreshed inside the fees-2 loop).
        self._refresh_fees(batch_id)

        for position, folder in enumerate(folders, start=1):
            record = self.store.get_claim(batch_id, folder.name)
            try:
                self._log(batch_id, record.claim_key, "-",
                           f"processing claim {position}/{len(folders)}")
                self._run_claim(batch_id, record, folder)
            except Exception as exc:  # noqa: BLE001 - isolate per-claim
                record.outcome = OUTCOME_FAILED
                record.last_error = f"unexpected pipeline error: {exc}"[:500]
                record.hold_reason = record.last_error
                record.manual_action = "inspect logs; fix cause; rerun batch to resume"
                self._save(record)
                self._log(batch_id, record.claim_key, "-", record.last_error)

        # Batch finalize for Add Claims (once) when at least one claim passed
        # the per-patient check and the run is live.
        if self._added_claim_keys and self.config.live_hbsys:
            batch_log = self._stage_log(batch_id, "-", "HBSYS_ADD_CLAIMS")
            try:
                ok, detail = self.finalize_add_claims_fn(batch_log)
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, f"finalize raised: {exc}"
            for key in self._added_claim_keys:
                record = self.store.get_claim(batch_id, key)
                if record is None:
                    continue
                record.hbsys["add_claims_finalize"] = {
                    "status": "OK" if ok else "FAILED", "detail": detail[:300],
                }
                if not ok:
                    record.outcome = OUTCOME_FAILED
                    record.last_error = f"add claims finalize failed: {detail}"[:500]
                    record.hold_reason = (
                        "batch finalize (Add/OK/Close) failed — verify in HBSys "
                        "before rerunning; do not blindly repeat"
                    )
                    record.manual_action = "verify tagged claims in HBSys, then resume"
                self._save(record)
            self._log(batch_id, "-", "HBSYS_ADD_CLAIMS",
                       f"batch finalize {'OK' if ok else 'FAILED'}: {detail[:300]}")

        # Final report (failure here must not erase claim progress).
        try:
            from core.agent import claims_report as report_mod  # noqa: E402

            path = report_mod.write_batch_report(
                self.config, batch_id, self.store.list_claims(batch_id),
                summary.as_dict(),
            )
            self._log(batch_id, "-", "-", f"final report: {path}")
        except Exception as exc:  # noqa: BLE001
            self._log(batch_id, "-", "-", f"REPORT FAILURE (state kept): {exc}")

        for record in self.store.list_claims(batch_id):
            if record.outcome == OUTCOME_COMPLETED:
                summary.completed += 1
            elif record.outcome == OUTCOME_INCOMPLETE:
                summary.incomplete += 1
            elif record.outcome == OUTCOME_HELD_FOR_REVIEW:
                summary.held_for_review += 1
            elif record.outcome == OUTCOME_FAILED:
                summary.failed += 1
            else:
                summary.pending += 1
            if record.stage_status.get("XML_GENERATION", {}).get("status") == "FAILED":
                summary.xml_generation_failures += 1
            if record.stage_status.get("XML_VALIDATION", {}).get("status") == "FAILED":
                summary.xml_validation_failures += 1
            if record.stage_status.get("ATTACHMENTS", {}).get("status") == "FAILED":
                summary.attachment_failures += 1
            if record.manual_action:
                summary.needs_manual_action += 1

        self.store.close_batch(batch_id, "finished")
        self._log(
            batch_id, "-", "-",
            f"batch end: total={summary.total} completed={summary.completed} "
            f"incomplete={summary.incomplete} review={summary.held_for_review} "
            f"failed={summary.failed}",
        )
        return summary

    # -- per-claim pipeline ----------------------------------------------------

    def _refresh_fees(self, batch_id: str) -> None:
        try:
            rows, error = self.fees_snapshot_fn(self.config.output_folder)
        except Exception as exc:  # noqa: BLE001
            rows, error = {}, f"fees snapshot raised: {exc}"
        self._fees_cache = rows
        self._fees_error = error
        if error:
            self._log(batch_id, "-", "FEES_CHECK", f"snapshot error: {error[:300]}")
        else:
            self._log(batch_id, "-", "FEES_CHECK",
                       f"snapshot: {len(rows)} row(s)")

    def _run_claim(self, batch_id: str, record: ClaimRecord, folder: Path) -> None:
        key = record.claim_key
        if record.outcome in (
            OUTCOME_COMPLETED, OUTCOME_INCOMPLETE,
            OUTCOME_HELD_FOR_REVIEW, OUTCOME_FAILED,
        ):
            self._log(batch_id, key, "-", f"skip (resume): outcome={record.outcome}")
            return

        # -- Stage 1 (per-claim): folder present in output --------------------
        if not self._already_done(record, "CLAIM_PROCESSING"):
            if folder.is_dir():
                self._record(record, "CLAIM_PROCESSING", "OK",
                             f"folder present: {folder.name}", completed=True)
            else:
                return self._hold_failed(
                    record, "CLAIM_PROCESSING", "folder missing from output",
                    "restore the patient folder, then resume")

        # -- Stage 2: Fees Checker first pass (informational gate) ------------
        if not self._already_done(record, "FEES_CHECK_1"):
            verdict, problems = self._fees_verdict(key)
            if verdict == "YES":
                self._record(record, "FEES_CHECK_1", "OK", "ready", completed=True)
            elif verdict == "NO":
                # Business NO on pass 1: recorded; the pipeline still runs
                # Date Fill + Final Bill (existing business order), pass 2 gates.
                self._record(record, "FEES_CHECK_1", "OK",
                             f"not ready (pass 1): {problems}", completed=True)
            else:
                self._record(record, "FEES_CHECK_1", "ERROR", problems)
                # Fees-1 is read-only; continue so date fill can still proceed.

        # -- Stage 3: Date Fill + Final Bill (each recorded separately) -------
        if not self._hbsys_live_or_hold(record, "DATE_FILL"):
            return
        if not self._already_done(record, "DATE_FILL"):
            if not self._run_hbsys_stage(
                record, "DATE_FILL", self.date_fill_fn, folder,
                hold_manual="check HBSys screen + admission match, then resume"):
                return
        if not self._already_done(record, "FINAL_BILL"):
            if not self._run_hbsys_stage(
                record, "FINAL_BILL", self.final_bill_fn, folder,
                hold_manual="check billing form state in HBSys, then resume"):
                return

        # -- Stage 4: Fees Checker second pass + bounded conditional loop -----
        if not self._already_done(record, "FEES_CHECK_2"):
            if not self._fees_gate_loop(batch_id, record, folder):
                return

        # -- Stage 5: XML Clicker (bounded retries; click != proof) -----------
        if not self._already_done(record, "XML_GENERATION"):
            if not self._xml_generation(record, folder):
                return

        # -- Stage 6: XML validation (all three kinds) ------------------------
        if not self._already_done(record, "XML_VALIDATION"):
            if not self._xml_validation(record, folder):
                return

        # -- Stage 7: Claims Checker status (preserved, never converted) ------
        if not self._already_done(record, "CLAIMS_CHECK"):
            if not self._claims_check(record, folder):
                return
        status = record.checker_status
        if status == CHECKER_INCOMPLETE:
            record.outcome = OUTCOME_INCOMPLETE
            self._save(record)
            return
        if status == CHECKER_REVIEW:
            record.outcome = OUTCOME_HELD_FOR_REVIEW
            self._save(record)
            return
        if status != CHECKER_READY:
            return self._hold_failed(
                record, "CLAIMS_CHECK", f"unknown checker status: {status!r}",
                "inspect claims checker output, then resume")

        # -- Eligibility: READY + three valid XMLs, nothing else --------------
        if not (record.xml_result.get("overall_valid")):
            # Re-verify from disk: outputs may have changed since validation.
            verdict = self._safe_xml_validate(folder)
            record.xml_result = self._xml_to_dict(verdict)
            if not verdict.overall_valid:
                return self._hold_incomplete(
                    record, "XML outputs no longer valid on disk",
                    "regenerate the missing XML, then resume")
            self._save(record)

        # -- Stage 8: HBSys Add Claims -> Attachments -> Verification ---------
        if not self._hbsys_live_or_hold(record, "HBSYS_ADD_CLAIMS"):
            return
        if not self._already_done(record, "HBSYS_ADD_CLAIMS"):
            if not self._run_hbsys_stage(
                record, "HBSYS_ADD_CLAIMS",
                lambda f, log: self.add_claims_fn(
                    record.folder_name, self._patient_name(folder), log),
                folder,
                hold_manual="verify tagged claims in HBSys, then resume"):
                return
            self._added_claim_keys.append(key)
            record.hbsys["add_claims"] = record.stage_status["HBSYS_ADD_CLAIMS"]
            self._save(record)
        if not self._already_done(record, "ATTACHMENTS"):
            if not self._run_hbsys_stage(
                record, "ATTACHMENTS",
                lambda f, log: self.attachments_fn(str(folder),
                                                   self._patient_name(folder), log),
                folder,
                hold_manual="verify attached files in HBSys, then resume"):
                return
            record.hbsys["attachments"] = record.stage_status["ATTACHMENTS"]
            record.hbsys["attachments_series"] = record.stage_status[
                "ATTACHMENTS"].get("extra_claim_series", "")
            record.hbsys["attachments_records"] = record.stage_status[
                "ATTACHMENTS"].get("extra_source_records", [])
            self._save(record)
        if not self._already_done(record, "ATTACHMENT_VERIFICATION"):
            if not self._attachment_verification(record):
                return

        record.outcome = OUTCOME_COMPLETED
        record.stage = "COMPLETED"
        record.last_completed_stage = "COMPLETED"
        self._save(record)
        self._log(batch_id, key, "-", "COMPLETED")

    # -- stage implementations --------------------------------------------------

    def _fees_verdict(self, folder_name: str) -> tuple[str, str]:
        if self._fees_error and not self._fees_cache:
            return "ERROR", self._fees_error
        try:
            return self.fees_verdict_fn(self._fees_cache, folder_name)
        except Exception as exc:  # noqa: BLE001
            return "ERROR", f"fees verdict raised: {exc}"

    def _fees_gate_loop(self, batch_id: str, record: ClaimRecord, folder: Path) -> bool:
        """Second-pass gate. Returns True when the claim may proceed to XML."""
        evaluations = 0
        attempts_log: list[str] = []
        while True:
            self._refresh_fees(batch_id)
            verdict, problems = self._fees_verdict(record.folder_name)
            evaluations += 1
            attempts_log.append(f"eval{evaluations}={verdict}:{problems}"[:200])
            if verdict == "YES":
                self._record(
                    record, "FEES_CHECK_2", "OK", "ready",
                    completed=True,
                    extra={"evaluations": evaluations, "attempts": attempts_log},
                )
                return True
            if verdict == "ERROR":
                self._record(
                    record, "FEES_CHECK_2", "ERROR", problems,
                    extra={"evaluations": evaluations, "attempts": attempts_log},
                )
                self._hold_failed(
                    record, "FEES_CHECK_2",
                    f"fees checker technical error: {problems}",
                    "fix fees checker/DB access, then resume")
                return False
            # Legitimate NO: bounded recovery loop (never unlimited).
            cycles_used = evaluations - 1
            if cycles_used >= self.config.max_fee_cycle_retries:
                record.retries = cycles_used
                self._record(
                    record, "FEES_CHECK_2", "NO",
                    f"not ready after {evaluations} evaluation(s): {problems}",
                    extra={"evaluations": evaluations, "attempts": attempts_log},
                )
                self._hold_incomplete(
                    record,
                    f"fees not ready after {evaluations} evaluation(s): {problems}",
                    "correct charges/dates in HBSys, then resume")
                return False
            record.retries = cycles_used + 1
            self._log(batch_id, record.claim_key, "FEES_CHECK_2",
                       f"NO (attempt {evaluations}) -> recovery cycle "
                       f"{cycles_used + 1}/{self.config.max_fee_cycle_retries}")
            self._record(record, "FEES_CHECK_2", "NO_RETRY",
                         f"cycle {cycles_used + 1}: {problems}")
            if not self._run_hbsys_stage(
                    record, "DATE_FILL", self.date_fill_fn, folder,
                    hold_manual="check HBSys screen + admission match, then resume"):
                return False
            if not self._run_hbsys_stage(
                    record, "FINAL_BILL", self.final_bill_fn, folder,
                    hold_manual="check billing form state in HBSys, then resume"):
                return False

    def _run_hbsys_stage(self, record, stage, fn, folder, hold_manual: str) -> bool:
        """Run one HBSys-touching stage with its retry budget (default 0)."""
        tries = 1 + max(0, self.config.hbsys_max_retries)
        last_detail = ""
        uncertain = False
        log = self._stage_log(record.batch_id, record.claim_key, stage)
        self._log(record.batch_id, record.claim_key, stage,
                   f"starting (up to {tries} attempt(s))")
        for attempt in range(1, tries + 1):
            try:
                ok, detail, extra = fn(folder, log)
            except Exception as exc:  # noqa: BLE001
                ok, detail, extra = False, f"{stage.lower()} raised: {exc}", {}
            last_detail = str(detail)
            uncertain = bool((extra or {}).get("uncertain"))
            flat = {f"extra_{k}": v for k, v in (extra or {}).items()
                    if isinstance(v, (str, int, float, bool, list, dict))}
            if ok:
                self._record(record, stage, "OK", last_detail, completed=True,
                             extra=flat or None)
                return True
            self._record(record, stage, "FAILED",
                         f"attempt {attempt}/{tries}: {last_detail}")
            if uncertain:
                break
        kind = classify_failure(last_detail)
        reason = (f"{stage} failed ({kind}): {last_detail}")
        if uncertain:
            reason += " — outcome uncertain; verify in HBSys, do NOT blindly rerun"
        self._hold_failed(record, stage, reason, hold_manual)
        return False

    def _xml_generation(self, record: ClaimRecord, folder: Path) -> bool:
        tries = 1 + max(0, self.config.xml_max_retries)
        last_detail = ""
        log = self._stage_log(record.batch_id, record.claim_key, "XML_GENERATION")
        self._log(record.batch_id, record.claim_key, "XML_GENERATION",
                   f"starting (up to {tries} attempt(s))")
        for attempt in range(1, tries + 1):
            try:
                ok, detail, _extra = self.xml_clicker_fn(folder, None, log)
            except Exception as exc:  # noqa: BLE001
                ok, detail = False, f"xml clicker raised: {exc}"
            last_detail = str(detail)
            if ok:
                self._record(record, "XML_GENERATION", "OK",
                             f"attempt {attempt}/{tries}: {last_detail}",
                             completed=True)
                return True
            self._record(record, "XML_GENERATION", "FAILED",
                         f"attempt {attempt}/{tries}: {last_detail}")
        self._hold_failed(
            record, "XML_GENERATION",
            f"xml generation failed ({classify_failure(last_detail)}): {last_detail}",
            "check HBSys generator screens, then resume")
        return False

    def _safe_xml_validate(self, folder: Path) -> XmlValidationResult:
        try:
            return self.xml_validate_fn(folder)
        except Exception as exc:  # noqa: BLE001
            empty = XmlValidationResult()
            empty.missing_kinds = ("CF4", "CF5", "eSOA")
            return empty

    @staticmethod
    def _xml_to_dict(verdict: XmlValidationResult) -> dict:
        return {
            "overall_valid": verdict.overall_valid,
            "cross_kind_match": verdict.cross_kind_match,
            "missing_kinds": list(verdict.missing_kinds),
            "files": {
                label: {
                    "path": f.path,
                    "exists": f.exists,
                    "non_empty": f.non_empty,
                    "well_formed": f.well_formed,
                    "content_note": f.content_note,
                    "series": list(f.series),
                    "claim_match": f.claim_match,
                    "error": f.error,
                    "valid": f.valid,
                }
                for label, f in verdict.files.items()
            },
        }

    def _xml_validation(self, record: ClaimRecord, folder: Path) -> bool:
        verdict = self._safe_xml_validate(folder)
        record.xml_result = self._xml_to_dict(verdict)
        if verdict.overall_valid:
            self._record(record, "XML_VALIDATION", "OK",
                         "CF4+CF5+eSOA present, non-empty, series match",
                         completed=True)
            return True
        self._record(record, "XML_VALIDATION", "FAILED",
                     f"missing/invalid: {', '.join(verdict.missing_kinds)}")
        self._hold_incomplete(
            record,
            f"mandatory XML missing/invalid: {', '.join(verdict.missing_kinds)}",
            "regenerate the missing XML, then resume")
        return False

    def _claims_check(self, record: ClaimRecord, folder: Path) -> bool:
        try:
            status, row = self.claims_check_fn(folder)
        except Exception as exc:  # noqa: BLE001
            self._hold_failed(
                record, "CLAIMS_CHECK", f"claims checker raised: {exc}",
                "inspect claims checker output, then resume")
            return False
        record.checker_status = str(status)
        detail = (f"status={status} missing={row.get('Missing', '')} "
                  f"warnings={row.get('Warnings', '')}"[:400])
        if status == CHECKER_READY:
            self._record(record, "CLAIMS_CHECK", "OK", detail, completed=True)
            return True
        if status == CHECKER_REVIEW:
            self._record(record, "CLAIMS_CHECK", "OK_REVIEW", detail, completed=True)
            record.hold_reason = f"held for review: {detail}"
            record.manual_action = "complete the existing review process"
            return True
        self._record(record, "CLAIMS_CHECK", "OK_INCOMPLETE", detail, completed=True)
        record.hold_reason = f"incomplete: {detail}"
        record.manual_action = "correct the missing documents, then resume"
        return True

    def _attachment_verification(self, record: ClaimRecord) -> bool:
        series = str(record.hbsys.get("attachments_series", ""))
        source_records = record.hbsys.get("attachments_records", [])
        if not series:
            self._hold_failed(
                record, "ATTACHMENT_VERIFICATION",
                "no claim series captured during attachments — outcome uncertain; "
                "verify in HBSys, do NOT blindly rerun",
                "verify attached files in HBSys, then resume")
            return False
        try:
            ok, detail, extra = self.verify_fn(series, source_records, None)
        except Exception as exc:  # noqa: BLE001
            ok, detail, extra = False, f"verification raised: {exc}", {
                "uncertain": True}
        record.verification = {
            "series": series,
            "verified": bool(ok),
            "detail": str(detail)[:500],
            "counts": (extra or {}).get("counts", {}),
        }
        if ok:
            self._record(record, "ATTACHMENT_VERIFICATION", "OK", str(detail),
                         completed=True)
            return True
        self._record(record, "ATTACHMENT_VERIFICATION", "FAILED", str(detail))
        self._hold_failed(
            record, "ATTACHMENT_VERIFICATION",
            f"attachment NOT verified: {detail}",
            "verify attached files in HBSys eClaimsDoc, then resume")
        return False

    # -- holds ---------------------------------------------------------------

    def _patient_name(self, folder: Path) -> str:
        return folder.name.split(" - ")[0].strip()

    def _hbsys_live_or_hold(self, record: ClaimRecord, stage: str) -> bool:
        if self.config.live_hbsys:
            return True
        self._record(record, stage, "SKIPPED",
                     "live HBSys disabled (CLAIMS_LIVE_HBSYS=0); "
                     "HBSys-touching stages never run dry")
        record.outcome = OUTCOME_HELD_FOR_REVIEW
        record.hold_reason = (
            f"{stage} skipped: automatic HBSys work is disabled; "
            "enable live HBSys and resume")
        record.manual_action = "enable live HBSys (or run the HBSys step manually)"
        record.stage = stage
        self._save(record)
        self._log(record.batch_id, record.claim_key, stage,
                   "held: live HBSys disabled")
        return False

    def _hold_failed(self, record, stage, reason: str, manual: str) -> None:
        record.last_error = reason[:500]
        record.hold_reason = reason[:500]
        record.manual_action = manual
        record.outcome = OUTCOME_FAILED
        record.stage = stage
        self._save(record)
        self._log(record.batch_id, record.claim_key, stage, f"FAILED: {reason[:300]}")

    def _hold_incomplete(self, record, reason: str, manual: str) -> None:
        record.last_error = reason[:500]
        record.hold_reason = reason[:500]
        record.manual_action = manual
        record.outcome = OUTCOME_INCOMPLETE
        self._save(record)
        self._log(record.batch_id, record.claim_key, record.stage,
                   f"INCOMPLETE: {reason[:300]}")


if __name__ == "__main__":
    agent = ClaimsAgent.__new__(ClaimsAgent)
    assert _STAGE_INDEX["COMPLETED"] == 12
    assert classify_failure("connection timeout") == "temporary execution failure"
    assert classify_failure("x", business_no=True) == "business-rule failure"
    assert classify_failure("verify in hbsys") == "uncertain outcome requiring verification"
    print("claims_agent standalone check passed")
