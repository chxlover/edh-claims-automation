"""Read-only Date Fill pre-check: skip patients whose dates are already complete.

Business rule (single source of truth is ``hbsys_date_fill_verifier``):

1. Resolve exactly one HBSys encounter from hospital_no + ADM/DIS dates.
2. Snapshot the professional / consent / authorization dates (read-only).
3. Compare against the expected fill date (discharge for REGULAR, admission
   for ABTC) using the SAME field semantics as the post-save verification.

Decisions:
- SKIP      : all required date fields already match the expected fill date.
- PROCESS   : at least one required field is missing or different.
- UNRESOLVED: the encounter could not be uniquely resolved — never skip on an
  ambiguous match; let the normal Date Fill flow handle it audited.

This module never clicks and never writes to HBSys. Database access is
read-only through ``HbsysDateFillVerifier``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from hbsys_date_fill_verifier import (  # noqa: E402
    EncounterDateState,
    HbsysDateFillVerifier,
    VerificationError,
    encounter_field_checks,
)


PRECHECK_SKIP = "SKIP"
PRECHECK_PROCESS = "PROCESS"
PRECHECK_UNRESOLVED = "UNRESOLVED"

FIELD_LABELS = {
    "professional": "Professional Fee",
    "consent": "Consent",
    "authorization": "Authorization/Certification",
}


@dataclass(frozen=True)
class PrecheckResult:
    decision: str
    reason: str = ""
    missing_fields: tuple[str, ...] = ()

    @property
    def skipped(self) -> bool:
        return self.decision == PRECHECK_SKIP


def precheck_claim(
    verifier: HbsysDateFillVerifier,
    hospital_no: str,
    admission: date,
    discharge: date,
    claim_type: str = "REGULAR",
) -> PrecheckResult:
    """Decide whether Date Fill can safely skip this patient (read-only)."""
    try:
        identity = verifier.resolve_exact_encounter(
            hospital_no,
            admission,
            discharge,
            claim_type,
        )
    except VerificationError as exc:
        return PrecheckResult(
            PRECHECK_UNRESOLVED,
            reason=f"Encounter not uniquely resolved ({exc}).",
        )

    snapshot = verifier.capture_patient_snapshot(
        hospital_no,
        identity.enccode,
        identity.claim_type,
    )
    state = snapshot.encounters.get(snapshot.expected_enccode)
    if state is None:
        return PrecheckResult(
            PRECHECK_PROCESS,
            reason="Expected encounter has no HBSys date records yet.",
        )

    expected = identity.target_date.isoformat()
    checks = encounter_field_checks(expected, state)
    missing = tuple(name for name, ok in checks.items() if not ok)

    if not missing:
        return PrecheckResult(
            PRECHECK_SKIP,
            reason=(
                "All required dates already match the expected fill date "
                f"{expected} (enccode={identity.enccode})."
            ),
        )
    labels = ", ".join(FIELD_LABELS.get(name, name) for name in missing)
    return PrecheckResult(
        PRECHECK_PROCESS,
        reason=f"Incomplete or mismatched dates: {labels}.",
        missing_fields=missing,
    )


def _run_self_test() -> int:
    """Pure-function tests of the completeness semantics (no DB needed)."""
    from hbsys_date_fill_verifier import evaluate_post_save, PatientDateSnapshot

    failures: list[str] = []

    def check(label: str, actual: object, expected: object) -> None:
        ok = actual == expected
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {actual!r} (expected {expected!r})")
        if not ok:
            failures.append(label)

    def snapshot_with(state: EncounterDateState) -> PatientDateSnapshot:
        return PatientDateSnapshot("ENC1", {"ENC1": state})

    def proof_for(state: EncounterDateState, expected: str):
        snap = snapshot_with(state)
        # Reuse trick: before == after means no other-encounter changes and
        # the field checks are evaluated against a single stable snapshot.
        return evaluate_post_save(
            date.fromisoformat(expected),
            snap,
            PatientDateSnapshot("ENC1", {"ENC1": state}),
        )

    # Case 1: all three fields match exactly -> complete.
    complete = EncounterDateState(("2026-08-11",), ("2026-08-11",), ("2026-08-11",))
    checks = encounter_field_checks("2026-08-11", complete)
    check("complete all-ok", all(checks.values()), True)
    check("complete proof verified", proof_for(complete, "2026-08-11").verified, True)

    # Case 2: everything empty -> incomplete.
    empty = EncounterDateState((), (), ())
    checks_empty = encounter_field_checks("2026-08-11", empty)
    check(
        "empty missing fields",
        tuple(name for name, ok in checks_empty.items() if not ok),
        ("professional", "consent", "authorization"),
    )
    check("empty not verified", proof_for(empty, "2026-08-11").verified, False)

    # Case 3: multiple professional rows, one blank by design -> acceptable.
    partial_rows = EncounterDateState(("2026-08-11", ""), ("2026-08-11",), ("2026-08-11",))
    check(
        "blank prof row acceptable",
        encounter_field_checks("2026-08-11", partial_rows)["professional"],
        True,
    )

    # Case 4: wrong value in consent -> incomplete.
    wrong_consent = EncounterDateState(("2026-08-11",), ("2026-08-10",), ("2026-08-11",))
    checks_wrong = encounter_field_checks("2026-08-11", wrong_consent)
    check(
        "wrong consent detected",
        ("consent",) == tuple(name for name, ok in checks_wrong.items() if not ok),
        True,
    )

    # Case 5: UNRESOLVED decision constant sanity + result helpers.
    result_skip = PrecheckResult(PRECHECK_SKIP, reason="done")
    result_process = PrecheckResult(
        PRECHECK_PROCESS,
        reason="incomplete",
        missing_fields=("consent",),
    )
    check("skip flagged", result_skip.skipped, True)
    check("process not flagged", result_process.skipped, False)
    check("field label lookup", FIELD_LABELS["authorization"], "Authorization/Certification")

    if failures:
        print(f"\nSELF TEST FAILED ({len(failures)}): {failures}")
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_self_test())
