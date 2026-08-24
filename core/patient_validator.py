"""Pure patient validation rules for claims processing.

The validator has no SQL, GUI, OCR, or filesystem side effects. Callers gather
facts and receive explainable validation issues suitable for the Review Queue.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.patient_review_queue import ReviewReason


@dataclass(frozen=True, slots=True)
class PatientValidationContext:
    hospital_no: str = ""
    database_patient_found: bool = False
    database_patient_name: str = ""
    detected_patient_name: str = ""
    name_match_score: int | None = None
    has_soa1: bool = False
    has_coe: bool = False
    ocr_failed: bool = False
    admission_count: int = 0
    admission_required: bool = False
    admission_selected: bool = False
    duplicate_hospital_no: bool = False
    duplicate_encounter: bool = False
    manual_override: bool = False


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    reason: ReviewReason
    message: str


@dataclass(frozen=True, slots=True)
class PatientValidationResult:
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.issues

    @property
    def primary_issue(self) -> ValidationIssue | None:
        return self.issues[0] if self.issues else None

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(issue.reason.value for issue in self.issues)


class PatientValidator:
    """Apply all patient rules in deterministic priority order."""

    def __init__(self, name_match_threshold: int = 78) -> None:
        if not 0 <= name_match_threshold <= 100:
            raise ValueError("name_match_threshold must be between 0 and 100")
        self.name_match_threshold = name_match_threshold

    def validate(
        self, context: PatientValidationContext
    ) -> PatientValidationResult:
        issues: list[ValidationIssue] = []

        if context.ocr_failed:
            issues.append(self._issue(ReviewReason.OCR_FAILED, "OCR produced no usable text"))
        if not context.has_soa1:
            issues.append(self._issue(ReviewReason.MISSING_SOA1, "SOA1 was not detected"))
        elif not context.hospital_no.strip():
            issues.append(
                self._issue(
                    ReviewReason.HOSPITAL_NUMBER_NOT_FOUND,
                    "Hospital Number was not extracted from SOA1",
                )
            )
        elif not context.database_patient_found:
            issues.append(
                self._issue(
                    ReviewReason.HOSPITAL_NUMBER_NOT_FOUND,
                    "Hospital Number was not found in the hospital database",
                )
            )

        if not context.has_coe:
            issues.append(self._issue(ReviewReason.MISSING_COE, "COE was not detected"))
        # COE OCR is not an identity authority. A valid SOA1 Hospital Number
        # resolved in HBSys establishes the database patient name.

        if context.admission_count > 1 and not context.admission_selected:
            issues.append(
                self._issue(
                    ReviewReason.MULTIPLE_ADMISSIONS,
                    f"{context.admission_count} admissions require selection",
                )
            )
        elif context.admission_required and not context.admission_selected:
            issues.append(
                self._issue(
                    ReviewReason.ADMISSION_NOT_SELECTED,
                    "A valid admission has not been selected",
                )
            )

        if context.duplicate_hospital_no:
            issues.append(
                self._issue(
                    ReviewReason.DUPLICATE_HOSPITAL_NUMBER,
                    "Hospital Number occurs more than once in the batch",
                )
            )
        if context.duplicate_encounter:
            issues.append(
                self._issue(
                    ReviewReason.DUPLICATE_ENCOUNTER,
                    "Encounter occurs more than once in the batch",
                )
            )
        if context.manual_override:
            issues.append(
                self._issue(
                    ReviewReason.MANUAL_OVERRIDE,
                    "Patient identity contains a manual override",
                )
            )

        return PatientValidationResult(tuple(issues))

    @staticmethod
    def _issue(reason: ReviewReason, message: str) -> ValidationIssue:
        return ValidationIssue(reason=reason, message=message)


patient_validator = PatientValidator()


if __name__ == "__main__":
    from core.activity_logger import logger

    validator = PatientValidator()
    passing = validator.validate(
        PatientValidationContext(
            hospital_no="000000000012345",
            database_patient_found=True,
            database_patient_name="DELA CRUZ, JUAN",
            detected_patient_name="DELA CRUZ, JUAN",
            name_match_score=100,
            has_soa1=True,
            has_coe=True,
        )
    )
    failing = validator.validate(PatientValidationContext(ocr_failed=True))
    assert passing.passed
    assert failing.reason_codes == ("H002", "H005", "H006")
    logger.success("Patient Validator standalone test passed")
