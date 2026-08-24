"""Read-only HBSys admission lookup and deterministic admission matching."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Iterable


class AdmissionLookupError(RuntimeError):
    """Raised when HBSys admissions cannot be read safely."""


@dataclass(frozen=True, slots=True)
class AdmissionRecord:
    encounter_no: str
    admission_date: str
    discharge_date: str


@dataclass(frozen=True, slots=True)
class AdmissionMatch:
    admissions: tuple[AdmissionRecord, ...]
    selected: AdmissionRecord | None

    @property
    def requires_selection(self) -> bool:
        return bool(self.admissions) and self.selected is None


class MySQLAdmissionLookup:
    """Query only ``hadmlog`` using a caller-supplied MySQL connection."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self.connection_factory = connection_factory

    def find_by_hospital_no(self, hospital_no: str) -> tuple[AdmissionRecord, ...]:
        if not hospital_no.strip():
            return ()

        connection = None
        try:
            connection = self.connection_factory()
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT enccode, admdate, disdate
                    FROM hadmlog
                    WHERE hpercode = %s
                    ORDER BY admdate DESC
                    """,
                    (hospital_no,),
                )
                rows = cursor.fetchall()
        except Exception as exc:
            raise AdmissionLookupError(
                f"Unable to read admissions for Hospital Number {hospital_no}"
            ) from exc
        finally:
            if connection is not None:
                connection.close()

        return tuple(
            AdmissionRecord(
                encounter_no=str(row.get("enccode") or "").strip(),
                admission_date=_format_date(row.get("admdate")),
                discharge_date=_format_date(row.get("disdate")),
            )
            for row in rows
        )

    def match(
        self,
        hospital_no: str,
        admission_date: str = "",
        discharge_date: str = "",
    ) -> AdmissionMatch:
        admissions = self.find_by_hospital_no(hospital_no)
        return match_admission(admissions, admission_date, discharge_date)


def match_admission(
    admissions: Iterable[AdmissionRecord],
    admission_date: str = "",
    discharge_date: str = "",
) -> AdmissionMatch:
    """Auto-select one admission or an unambiguous OCR date match."""
    records = tuple(admissions)
    if len(records) == 1:
        return AdmissionMatch(records, records[0])
    if not records:
        return AdmissionMatch(records, None)

    admission_date = admission_date.strip()
    discharge_date = discharge_date.strip()
    matches = tuple(
        record
        for record in records
        if (not admission_date or record.admission_date == admission_date)
        and (not discharge_date or record.discharge_date == discharge_date)
    )
    # At least one OCR date is required; otherwise every row would match.
    selected = (
        matches[0]
        if (admission_date or discharge_date) and len(matches) == 1
        else None
    )
    return AdmissionMatch(records, selected)


def _format_date(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], pattern).strftime("%Y%m%d")
        except ValueError:
            continue
    return ""


if __name__ == "__main__":
    from core.activity_logger import logger

    examples = (
        AdmissionRecord("ENC-1", "20260101", "20260103"),
        AdmissionRecord("ENC-2", "20260201", "20260204"),
    )
    result = match_admission(examples, "20260201", "20260204")
    assert result.selected == examples[1]
    assert match_admission(examples).selected is None
    logger.success("Admission matching standalone test passed")
