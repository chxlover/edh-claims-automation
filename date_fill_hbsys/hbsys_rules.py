from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


DATE_INPUT_FORMATS = ("%m/%d/%Y", "%m-%d-%Y")
DATE_OUTPUT_FORMAT = "%m-%d-%Y"


@dataclass(frozen=True)
class AdmissionRow:
    admission_date: str
    admission_time: str = ""
    discharge_date: str = ""
    discharge_time: str = ""
    encounter_type: str = ""

    @property
    def normalized_encounter_type(self) -> str:
        return self.encounter_type.strip().upper()


@dataclass(frozen=True)
class Recommendation:
    status: str
    row: AdmissionRow | None
    date_to_fill: str | None
    reason: str


def parse_date(value: str) -> datetime | None:
    value = value.strip()
    for fmt in DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def format_for_hbsys(value: str) -> str | None:
    parsed = parse_date(value)
    if not parsed:
        return None
    return parsed.strftime(DATE_OUTPUT_FORMAT)


def recommend_discharge_date(rows: Iterable[AdmissionRow]) -> Recommendation:
    usable = [row for row in rows if format_for_hbsys(row.discharge_date)]
    if not usable:
        return Recommendation(
            status="no_match",
            row=None,
            date_to_fill=None,
            reason="No admission row has a valid discharge date.",
        )

    admit_rows = [row for row in usable if row.normalized_encounter_type == "ADMIT"]
    if len(admit_rows) == 1:
        row = admit_rows[0]
        return Recommendation(
            status="auto",
            row=row,
            date_to_fill=format_for_hbsys(row.discharge_date),
            reason="Only one ADMIT confinement row was found.",
        )

    if len(usable) == 1:
        row = usable[0]
        return Recommendation(
            status="auto",
            row=row,
            date_to_fill=format_for_hbsys(row.discharge_date),
            reason="Only one row has a valid discharge date.",
        )

    return Recommendation(
        status="needs_review",
        row=None,
        date_to_fill=None,
        reason="Multiple possible confinement rows were found.",
    )


def sample_recommendation() -> Recommendation:
    rows = [
        AdmissionRow("06/11/2026", "08:30 AM", "06/11/2026", "10:00 AM", "OPD"),
        AdmissionRow("06/05/2026", "04:30 AM", "06/09/2026", "01:12 PM", "ADMIT"),
        AdmissionRow("10/29/2025", "10:01 AM", "10/29/2025", "12:00 PM", "OPD"),
    ]
    return recommend_discharge_date(rows)

