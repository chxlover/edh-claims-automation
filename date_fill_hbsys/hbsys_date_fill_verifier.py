"""Read-only proof that Date Fill targeted and updated the right encounter."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.hbsys_connection import create_hbsys_connection  # noqa: E402
from core.not_transmitted_batches import ABTC_ACCREDITATION_NO  # noqa: E402


LOGGER = logging.getLogger(__name__)
ConnectionFactory = Callable[[], Any]


class VerificationError(RuntimeError):
    """Raised when HBSys cannot provide one safe, exact encounter."""


@dataclass(frozen=True)
class EncounterIdentity:
    enccode: str
    hpercode: str
    admission: date
    discharge: date
    claim_type: str = "REGULAR"
    encounter_type: str = "ADMIT"

    @property
    def date_basis(self) -> str:
        return "ADMISSION" if self.claim_type == "ABTC" else "DISCHARGE"

    @property
    def target_date(self) -> date:
        return self.admission if self.claim_type == "ABTC" else self.discharge


@dataclass(frozen=True)
class EncounterDateState:
    professional_dates: tuple[str, ...] = ()
    consent_dates: tuple[str, ...] = ()
    authorization_dates: tuple[str, ...] = ()

    def as_text(self) -> str:
        return (
            f"professional={self.professional_dates};"
            f"consent={self.consent_dates};"
            f"authorization={self.authorization_dates}"
        )


@dataclass(frozen=True)
class PatientDateSnapshot:
    expected_enccode: str
    encounters: dict[str, EncounterDateState] = field(default_factory=dict)


@dataclass(frozen=True)
class PostSaveProof:
    verified: bool
    critical_wrong_encounter: bool
    professional_date: str
    consent_date: str
    authorization_date: str
    other_encounter_changed: bool
    changed_other_enccodes: tuple[str, ...]
    reason: str


def _date_only(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text[:10] if text else ""


def _summarize(values: tuple[str, ...]) -> str:
    unique = sorted({value for value in values if value})
    return "|".join(unique)


def encounter_field_checks(
    expected: str,
    state: EncounterDateState,
) -> dict[str, bool]:
    """Single source of truth for per-field completeness semantics.

    A patient can have more than one professional fee row. Some rows are left
    blank by design (the hospital does not date them), so empty professional
    rows are acceptable; every non-empty row must equal ``expected``. Consent
    and authorization must exist and match exactly.
    """

    professional_ok = (
        any(state.professional_dates)
        and all(
            value == expected or not value
            for value in state.professional_dates
        )
    )
    consent_ok = bool(state.consent_dates) and all(
        value == expected for value in state.consent_dates
    )
    authorization_ok = bool(state.authorization_dates) and all(
        value == expected for value in state.authorization_dates
    )
    return {
        "professional": professional_ok,
        "consent": consent_ok,
        "authorization": authorization_ok,
    }


def evaluate_post_save(
    expected_fill_date: date,
    before: PatientDateSnapshot,
    after: PatientDateSnapshot,
) -> PostSaveProof:
    """Compare immutable read-only snapshots and produce an explainable proof."""
    expected = expected_fill_date.isoformat()
    expected_state = after.encounters.get(after.expected_enccode)
    if expected_state is None:
        return PostSaveProof(
            False, False, "", "", "", False, (),
            "Expected encounter has no post-save HBSys records.",
        )

    changed_other = tuple(
        sorted(
            enccode
            for enccode in set(before.encounters) | set(after.encounters)
            if enccode != before.expected_enccode
            and before.encounters.get(enccode) != after.encounters.get(enccode)
        )
    )
    field_checks = encounter_field_checks(expected, expected_state)
    professional_ok = field_checks["professional"]
    consent_ok = field_checks["consent"]
    authorization_ok = field_checks["authorization"]

    if changed_other:
        reason = "Another confinement record changed: " + ", ".join(changed_other)
    elif not professional_ok:
        reason = "Professional Fee date was not saved to the expected encounter."
    elif not consent_ok:
        reason = "Consent date was not saved to the expected encounter."
    elif not authorization_ok:
        reason = "Certification/authorization date was not saved to the expected encounter."
    else:
        reason = "All expected HBSys date fields match the required fill date."

    return PostSaveProof(
        verified=professional_ok and consent_ok and authorization_ok and not changed_other,
        critical_wrong_encounter=bool(changed_other),
        professional_date=_summarize(expected_state.professional_dates),
        consent_date=_summarize(expected_state.consent_dates),
        authorization_date=_summarize(expected_state.authorization_dates),
        other_encounter_changed=bool(changed_other),
        changed_other_enccodes=changed_other,
        reason=reason,
    )


class HbsysDateFillVerifier:
    """HBSys repository restricted to SELECT statements for Date Fill proof."""

    def __init__(self, connection_factory: ConnectionFactory = create_hbsys_connection):
        self._connection_factory = connection_factory

    def resolve_exact_encounter(
        self,
        hospital_no: str,
        admission: date,
        discharge: date,
        claim_type: str = "REGULAR",
    ) -> EncounterIdentity:
        normalized_type = str(claim_type or "REGULAR").strip().upper()
        if normalized_type not in {"REGULAR", "ABTC"}:
            raise VerificationError("Claim Type must be Regular or ABTC.")
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                if normalized_type == "ABTC":
                    cursor.execute(
                        """
                        SELECT hpatcon.enccode,
                               hpatcon.hpercode,
                               DATE(hpatcon1.padmissiondatetime) AS admission,
                               DATE(hpatcon1.pdischargedatetime) AS discharge,
                               COALESCE(henctr.toecode, 'OPD') AS encounter_type
                        FROM hpatcon
                        INNER JOIN hpatcon1
                            ON hpatcon1.enccode = hpatcon.enccode
                        LEFT JOIN henctr
                            ON henctr.enccode = hpatcon.enccode
                        WHERE hpatcon.hpercode = %s
                          AND hpatcon.accreno = %s
                          AND DATE(hpatcon1.padmissiondatetime) = %s
                          AND DATE(hpatcon1.pdischargedatetime) = %s
                        """,
                        (
                            hospital_no,
                            ABTC_ACCREDITATION_NO,
                            admission,
                            discharge,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        SELECT enccode, hpercode, DATE(admdate) AS admission,
                               DATE(disdate) AS discharge,
                               'ADMIT' AS encounter_type
                        FROM hadmlog
                        WHERE hpercode = %s
                          AND DATE(admdate) = %s
                          AND DATE(disdate) = %s
                        """,
                        (hospital_no, admission, discharge),
                    )
                rows = cursor.fetchall()
        finally:
            connection.close()

        if len(rows) != 1:
            raise VerificationError(
                "Expected exactly one HBSys encounter for folder confinement; "
                f"found {len(rows)}."
            )
        row = rows[0]
        return EncounterIdentity(
            enccode=str(row["enccode"]),
            hpercode=str(row["hpercode"]),
            admission=row["admission"],
            discharge=row["discharge"],
            claim_type=normalized_type,
            encounter_type=str(row.get("encounter_type") or "").upper(),
        )

    def capture_patient_snapshot(
        self,
        hospital_no: str,
        expected_enccode: str,
        claim_type: str = "REGULAR",
    ) -> PatientDateSnapshot:
        normalized_type = str(claim_type or "REGULAR").strip().upper()
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                if normalized_type == "ABTC":
                    cursor.execute(
                        """
                        SELECT enccode
                        FROM hpatcon
                        WHERE hpercode = %s
                          AND accreno = %s
                        """,
                        (hospital_no, ABTC_ACCREDITATION_NO),
                    )
                else:
                    cursor.execute(
                        "SELECT enccode FROM hadmlog WHERE hpercode = %s",
                        (hospital_no,),
                    )
                enccodes = [str(row["enccode"]) for row in cursor.fetchall()]
                states = {
                    enccode: self._read_encounter_state(cursor, enccode)
                    for enccode in enccodes
                }
        finally:
            connection.close()
        return PatientDateSnapshot(expected_enccode, states)

    @staticmethod
    def _read_encounter_state(cursor: Any, enccode: str) -> EncounterDateState:
        cursor.execute(
            "SELECT pdoctorsigndate FROM hprofserv WHERE enccode = %s",
            (enccode,),
        )
        professional = tuple(
            sorted(
                _date_only(row.get("pdoctorsigndate"))
                for row in cursor.fetchall()
            )
        )
        cursor.execute(
            "SELECT consentdate, authsigndate FROM hpatcon1 WHERE enccode = %s",
            (enccode,),
        )
        rows = cursor.fetchall()
        consent = tuple(sorted(_date_only(row.get("consentdate")) for row in rows))
        authorization = tuple(
            sorted(_date_only(row.get("authsigndate")) for row in rows)
        )
        return EncounterDateState(professional, consent, authorization)

    def verify_after_save(
        self,
        expected_fill_date: date,
        before: PatientDateSnapshot,
    ) -> PostSaveProof:
        after = self.capture_encounters(
            before.expected_enccode,
            tuple(before.encounters),
        )
        return evaluate_post_save(expected_fill_date, before, after)

    def capture_encounters(
        self,
        expected_enccode: str,
        enccodes: tuple[str, ...],
    ) -> PatientDateSnapshot:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                states = {
                    enccode: self._read_encounter_state(cursor, enccode)
                    for enccode in enccodes
                }
        finally:
            connection.close()
        return PatientDateSnapshot(expected_enccode, states)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Date Fill encounter verifier")
    parser.add_argument("hospital_no")
    parser.add_argument("admission", type=date.fromisoformat)
    parser.add_argument("discharge", type=date.fromisoformat)
    parser.add_argument(
        "--claim-type",
        choices=("REGULAR", "ABTC"),
        default="REGULAR",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    verifier = HbsysDateFillVerifier()
    identity = verifier.resolve_exact_encounter(
        args.hospital_no, args.admission, args.discharge, args.claim_type
    )
    snapshot = verifier.capture_patient_snapshot(
        args.hospital_no,
        identity.enccode,
        identity.claim_type,
    )
    LOGGER.info("Matched enccode: %s", identity.enccode)
    LOGGER.info("Current state: %s", snapshot.encounters[identity.enccode].as_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
