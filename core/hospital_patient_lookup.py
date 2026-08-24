"""Read-only HBSys patient lookup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class PatientLookupError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HospitalPatient:
    hospital_no: str
    patient_name: str


class MySQLPatientLookup:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self.connection_factory = connection_factory

    def find(self, hospital_no: str) -> HospitalPatient | None:
        hospital_no = "".join(character for character in hospital_no if character.isdigit())
        if not hospital_no:
            return None
        hospital_no = hospital_no.zfill(15)
        connection = None
        try:
            connection = self.connection_factory()
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT patlast, patfirst, patsuffix, patmiddle, hpercode
                    FROM hperson WHERE hpercode = %s LIMIT 1
                    """,
                    (hospital_no,),
                )
                row = cursor.fetchone()
        except Exception as exc:
            raise PatientLookupError(
                f"Unable to read patient for Hospital Number {hospital_no}"
            ) from exc
        finally:
            if connection is not None:
                connection.close()
        if not row:
            return None
        given = " ".join(
            str(row.get(field) or "").strip().upper()
            for field in ("patfirst", "patsuffix", "patmiddle")
            if str(row.get(field) or "").strip()
        )
        last = str(row.get("patlast") or "").strip().upper()
        name = f"{last}, {given}".strip(" ,")
        return HospitalPatient(
            hospital_no="".join(
                character for character in str(row.get("hpercode") or hospital_no)
                if character.isdigit()
            ).zfill(15),
            patient_name=" ".join(name.split()),
        )
