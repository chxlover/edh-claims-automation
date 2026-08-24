"""Hospital Number correction and revalidation workflow."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from core.admission_lookup import AdmissionRecord, MySQLAdmissionLookup
from core.hospital_patient_lookup import HospitalPatient, MySQLPatientLookup
from core.patient_review_queue import PatientReviewQueue, ReviewStatus


@dataclass(frozen=True, slots=True)
class CorrectionCandidate:
    review_id: int
    patient: HospitalPatient
    admissions: tuple[AdmissionRecord, ...]
    name_match_score: int


class PatientCorrectionService:
    RESOLVED_BY_IDENTITY = {"H001", "H003", "H007"}

    def __init__(
        self,
        queue: PatientReviewQueue,
        patients: MySQLPatientLookup,
        admissions: MySQLAdmissionLookup,
        name_threshold: int = 78,
    ) -> None:
        self.queue = queue
        self.patients = patients
        self.admissions = admissions
        self.name_threshold = name_threshold

    def search(self, review_id: int, hospital_no: str) -> CorrectionCandidate:
        item = self.queue.get(review_id)
        if item is None:
            raise LookupError(f"Review item {review_id} does not exist")
        patient = self.patients.find(hospital_no)
        if patient is None:
            raise LookupError("Hospital Number was not found in HBSys")
        admissions = self.admissions.find_by_hospital_no(patient.hospital_no)
        score = _name_score(item.patient_name, patient.patient_name)
        return CorrectionCandidate(review_id, patient, admissions, score)

    def apply(
        self,
        candidate: CorrectionCandidate,
        *,
        encounter_no: str,
        corrected_by: str,
        note: str,
        preserve_resolved_status: bool = False,
    ) -> tuple[bool, tuple[str, ...]]:
        selected = self._select_admission(candidate.admissions, encounter_no)
        item = self.queue.get(candidate.review_id)
        if item is None:
            raise LookupError(f"Review item {candidate.review_id} does not exist")

        existing_codes = set(re.findall(r"H\d{3}", item.reason_detail))
        existing_codes.add(item.reason)
        resolved_codes = set(self.RESOLVED_BY_IDENTITY)
        if candidate.name_match_score >= self.name_threshold:
            resolved_codes.add("H004")
        remaining = tuple(sorted(existing_codes - resolved_codes))
        remaining_detail = _remaining_detail(item.reason_detail, set(remaining))

        changed = self.queue.apply_patient_correction(
            candidate.review_id,
            hospital_no=candidate.patient.hospital_no,
            patient_name=candidate.patient.patient_name,
            encounter_no=selected.encounter_no,
            admission_date=selected.admission_date,
            discharge_date=selected.discharge_date,
            corrected_by=corrected_by,
            note=note,
            remaining_detail=remaining_detail,
        )
        if not changed:
            return False, remaining

        if preserve_resolved_status and item.status == ReviewStatus.RESOLVED.value:
            # A reviewer may already have accepted a non-identity issue (for
            # example H005) before Resume discovers that an admission is still
            # missing.  Applying the verified HBSys admission must not reopen
            # that manually resolved issue.
            return True, remaining
        if remaining:
            self.queue.database.update_review_status(
                candidate.review_id, ReviewStatus.PENDING.value
            )
        else:
            self.queue.database.update_review_status(
                candidate.review_id,
                ReviewStatus.RESOLVED.value,
                note,
                corrected_by,
            )
        return True, remaining

    @staticmethod
    def _select_admission(
        admissions: tuple[AdmissionRecord, ...], encounter_no: str
    ) -> AdmissionRecord:
        if not admissions:
            raise ValueError("No admission was found for this patient")
        if len(admissions) == 1 and not encounter_no:
            return admissions[0]
        for admission in admissions:
            if admission.encounter_no == encounter_no:
                return admission
        raise ValueError("Select a valid admission")


def _name_score(left: str, right: str) -> int:
    if not left.strip() or not right.strip():
        return 0
    return int(max(fuzz.token_set_ratio(left, right), fuzz.partial_ratio(left, right)))


def _remaining_detail(detail: str, remaining: set[str]) -> str:
    parts = [part.strip() for part in detail.split(";") if part.strip()]
    return "; ".join(
        part for part in parts if any(code in part for code in remaining)
    )
