"""In-memory duplicate identity tracking for one processing batch."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True, slots=True)
class DuplicateResult:
    duplicate_hospital_no: bool = False
    duplicate_encounter: bool = False


class BatchPatientTracker:
    def __init__(self) -> None:
        self._hospital_numbers: set[str] = set()
        self._encounters: set[str] = set()
        self._lock = Lock()

    def check_and_register(
        self, hospital_no: str = "", encounter_no: str = ""
    ) -> DuplicateResult:
        hospital_no = hospital_no.strip()
        encounter_no = encounter_no.strip()
        with self._lock:
            result = DuplicateResult(
                duplicate_hospital_no=(
                    bool(hospital_no) and hospital_no in self._hospital_numbers
                ),
                duplicate_encounter=(
                    bool(encounter_no) and encounter_no in self._encounters
                ),
            )
            if hospital_no:
                self._hospital_numbers.add(hospital_no)
            if encounter_no:
                self._encounters.add(encounter_no)
            return result

    def clear(self) -> None:
        with self._lock:
            self._hospital_numbers.clear()
            self._encounters.clear()


if __name__ == "__main__":
    from core.activity_logger import logger

    tracker = BatchPatientTracker()
    assert not tracker.check_and_register("H1", "E1").duplicate_hospital_no
    duplicate = tracker.check_and_register("H1", "E1")
    assert duplicate.duplicate_hospital_no and duplicate.duplicate_encounter
    logger.success("Batch Patient Tracker standalone test passed")
