"""Safe background copying of generated HBSys XML files.

The service is filesystem-only.  It does not use Tkinter, control the mouse or
keyboard, start subprocesses, or modify source XML files.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SUPPORTED_XML_PATTERN = re.compile(r"_(CF4|CF5|ESOA)\.xml$", re.IGNORECASE)


def normalize_patient_match_name(value: object) -> str:
    """Normalize HBSys XML names and Claims Bot folder names consistently."""

    name = str(value or "").strip().upper().replace("Ã‘", "N").replace("Ñ", "N")
    # Support valid Unicode names (for example Ñ) as well as the legacy
    # mojibake replacements above. The comparison remains exact after accents
    # are reduced to their base characters.
    name = "".join(
        character
        for character in unicodedata.normalize("NFKD", name)
        if not unicodedata.combining(character)
    )

    if " - " in name:
        name = name.split(" - ", 1)[0].strip()
    if "," in name:
        first, rest = name.split(",", 1)
        name = first.strip() + ", " + rest.replace(",", " ").strip()
    name = re.sub(r"\s+", " ", name)
    return name.strip(" ,.-")


def normalize_patient_match_key(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", normalize_patient_match_name(value))


def extract_patient_name_from_xml_filename(filename: object) -> str:
    name = Path(str(filename or "")).stem.strip()
    name = re.sub(r"-\d+_(CF4|CF5|ESOA)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"-\d+$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"_(CF4|CF5|ESOA)$", "", name, flags=re.IGNORECASE)
    return normalize_patient_match_name(name)


@dataclass(frozen=True)
class XmlCopyEvent:
    status: str
    source: Path | None = None
    patient_name: str = ""
    destination: Path | None = None
    reason: str = ""
    reportable: bool = True


@dataclass(frozen=True)
class XmlCopySummary:
    events: tuple[XmlCopyEvent, ...]

    def count(self, status: str) -> int:
        return sum(event.status == status for event in self.events)

    @property
    def copied_count(self) -> int:
        return self.count("COPIED")

    @property
    def waiting_count(self) -> int:
        return self.count("WAITING_STABLE")

    @property
    def issue_count(self) -> int:
        return self.count("AMBIGUOUS") + self.count("CONFLICT") + self.count("COPY_ERROR")


class XmlAutoCopyService:
    """Stateful scanner with stability, ambiguity, and overwrite protection."""

    def __init__(
        self,
        source_folder: str | Path,
        output_folder: str | Path,
        incomplete_folder: str | Path,
    ) -> None:
        self._source_configured = bool(str(source_folder or "").strip())
        self._output_configured = bool(str(output_folder or "").strip())
        self.source_folder = Path(source_folder or ".")
        self.output_folder = Path(output_folder or ".")
        self.incomplete_folder = Path(incomplete_folder or ".")
        self._observations: dict[Path, tuple[tuple[int, int], int]] = {}
        self._last_outcomes: dict[Path, tuple[object, ...]] = {}
        self._service_last_outcome: tuple[object, ...] | None = None

    @property
    def configuration_key(self) -> tuple[str, str, str]:
        return tuple(
            os.path.normcase(os.path.abspath(str(path)))
            for path in (self.source_folder, self.output_folder, self.incomplete_folder)
        )

    @staticmethod
    def _signature(path: Path) -> tuple[int, int]:
        stat_result = path.stat()
        return stat_result.st_size, stat_result.st_mtime_ns

    @staticmethod
    def _folder_index(root: Path) -> dict[str, list[Path]]:
        index: dict[str, list[Path]] = defaultdict(list)
        if not root.is_dir():
            return index
        try:
            folders = list(root.iterdir())
        except OSError:
            return index
        for folder in folders:
            if not folder.is_dir():
                continue
            key = normalize_patient_match_key(folder.name)
            if key:
                index[key].append(folder)
        for paths in index.values():
            paths.sort(key=lambda item: item.name.upper())
        return index

    @staticmethod
    def _inventory_signature(*indexes: dict[str, list[Path]]) -> tuple[str, ...]:
        folders = []
        for index in indexes:
            for paths in index.values():
                folders.extend(os.path.normcase(os.path.abspath(str(path))) for path in paths)
        return tuple(sorted(folders))

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @classmethod
    def _identical(cls, source: Path, destination: Path) -> bool:
        try:
            if source.stat().st_size != destination.stat().st_size:
                return False
            return cls._sha256(source) == cls._sha256(destination)
        except OSError:
            return False

    def _event(
        self,
        status: str,
        source: Path | None,
        patient_name: str = "",
        destination: Path | None = None,
        reason: str = "",
        outcome_key: tuple[object, ...] | None = None,
    ) -> XmlCopyEvent:
        reportable = True
        if source is not None and outcome_key is not None:
            previous = self._last_outcomes.get(source)
            reportable = previous != outcome_key
            self._last_outcomes[source] = outcome_key
        return XmlCopyEvent(
            status=status,
            source=source,
            patient_name=patient_name,
            destination=destination,
            reason=reason,
            reportable=reportable,
        )

    def _choose_destination(
        self,
        key: str,
        output_index: dict[str, list[Path]],
        incomplete_index: dict[str, list[Path]],
    ) -> tuple[Path | None, tuple[Path, ...]]:
        output_matches = tuple(output_index.get(key, ()))
        if output_matches:
            return (output_matches[0], ()) if len(output_matches) == 1 else (None, output_matches)
        incomplete_matches = tuple(incomplete_index.get(key, ()))
        if incomplete_matches:
            return (
                (incomplete_matches[0], ())
                if len(incomplete_matches) == 1
                else (None, incomplete_matches)
            )
        return None, ()

    def _atomic_copy(
        self,
        source: Path,
        destination: Path,
        source_signature: tuple[int, int],
        patient_name: str,
    ) -> XmlCopyEvent:
        if destination.exists():
            if self._identical(source, destination):
                destination_signature = self._signature(destination)
                return self._event(
                    "IDENTICAL_SKIP",
                    source,
                    patient_name,
                    destination,
                    "Destination already contains identical XML",
                    ("IDENTICAL_SKIP", source_signature, destination, destination_signature),
                )
            destination_signature = self._signature(destination)
            return self._event(
                "CONFLICT",
                source,
                patient_name,
                destination,
                "Existing XML has different content; original preserved",
                ("CONFLICT", source_signature, destination, destination_signature),
            )

        temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
        try:
            shutil.copy2(source, temporary)
            if self._signature(source) != source_signature:
                temporary.unlink(missing_ok=True)
                return self._event(
                    "WAITING_STABLE",
                    source,
                    patient_name,
                    destination,
                    "Source changed while being copied",
                    ("WAITING_STABLE", self._signature(source)),
                )
            if not self._identical(source, temporary):
                temporary.unlink(missing_ok=True)
                raise OSError("Temporary copy verification failed")
            if destination.exists():
                temporary.unlink(missing_ok=True)
                return self._atomic_copy(source, destination, source_signature, patient_name)
            os.rename(temporary, destination)
            destination_signature = self._signature(destination)
            return self._event(
                "COPIED",
                source,
                patient_name,
                destination,
                "Stable XML copied successfully",
                ("COPIED", source_signature, destination, destination_signature),
            )
        except Exception as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return self._event(
                "COPY_ERROR",
                source,
                patient_name,
                destination,
                str(exc),
                ("COPY_ERROR", source_signature, destination, str(exc)),
            )

    def scan_once(self, *, require_observed_stable: bool = True) -> XmlCopySummary:
        if not self._source_configured or not self.source_folder.is_dir():
            reason = (
                "XML source folder is not configured"
                if not self._source_configured
                else f"Source folder not found: {self.source_folder}"
            )
            outcome = ("SOURCE_UNAVAILABLE", reason)
            event = XmlCopyEvent(
                "SOURCE_UNAVAILABLE",
                reason=reason,
                reportable=self._service_last_outcome != outcome,
            )
            self._service_last_outcome = outcome
            return XmlCopySummary((event,))

        output_index = (
            self._folder_index(self.output_folder)
            if self._output_configured
            else {}
        )
        incomplete_index = self._folder_index(self.incomplete_folder)
        inventory_signature = self._inventory_signature(output_index, incomplete_index)
        events: list[XmlCopyEvent] = []
        current_sources: set[Path] = set()

        try:
            source_files = sorted(self.source_folder.iterdir(), key=lambda item: item.name.upper())
        except OSError as exc:
            outcome = ("SOURCE_UNAVAILABLE", str(exc))
            event = XmlCopyEvent(
                "SOURCE_UNAVAILABLE",
                reason=str(exc),
                reportable=self._service_last_outcome != outcome,
            )
            self._service_last_outcome = outcome
            return XmlCopySummary((event,))

        self._service_last_outcome = None

        for source in source_files:
            if not source.is_file() or not SUPPORTED_XML_PATTERN.search(source.name):
                continue
            current_sources.add(source)
            patient_name = extract_patient_name_from_xml_filename(source.name)
            patient_key = normalize_patient_match_key(patient_name)

            try:
                signature = self._signature(source)
                with source.open("rb") as stream:
                    stream.read(1)
            except OSError as exc:
                events.append(
                    self._event(
                        "WAITING_STABLE",
                        source,
                        patient_name,
                        reason=f"Source is locked or unreadable: {exc}",
                        outcome_key=("WAITING_STABLE", "UNREADABLE", str(exc)),
                    )
                )
                continue

            previous_signature, previous_count = self._observations.get(source, ((-1, -1), 0))
            stable_count = previous_count + 1 if previous_signature == signature else 1
            self._observations[source] = (signature, stable_count)

            if signature[0] <= 0:
                events.append(
                    self._event(
                        "WAITING_STABLE",
                        source,
                        patient_name,
                        reason="Source XML is empty",
                        outcome_key=("WAITING_STABLE", signature),
                    )
                )
                continue

            if require_observed_stable and stable_count < 2:
                events.append(
                    self._event(
                        "WAITING_STABLE",
                        source,
                        patient_name,
                        reason="Waiting for a second unchanged polling cycle",
                        outcome_key=("WAITING_STABLE", signature),
                    )
                )
                continue

            patient_folder, ambiguous = self._choose_destination(
                patient_key,
                output_index,
                incomplete_index,
            )
            if ambiguous:
                names = "; ".join(path.name for path in ambiguous)
                events.append(
                    self._event(
                        "AMBIGUOUS",
                        source,
                        patient_name,
                        reason=f"Multiple patient folders match: {names}",
                        outcome_key=("AMBIGUOUS", signature, inventory_signature, names),
                    )
                )
                continue
            if patient_folder is None:
                events.append(
                    self._event(
                        "UNMATCHED",
                        source,
                        patient_name,
                        reason="No matching patient folder",
                        outcome_key=("UNMATCHED", signature, inventory_signature),
                    )
                )
                continue

            destination = patient_folder / source.name
            events.append(
                self._atomic_copy(source, destination, signature, patient_name)
            )

        removed = set(self._observations) - current_sources
        for source in removed:
            self._observations.pop(source, None)
            self._last_outcomes.pop(source, None)

        return XmlCopySummary(tuple(events))


def format_event(event: XmlCopyEvent) -> str:
    source = event.source.name if event.source else ""
    parts = [f"[AUTO XML] {event.status}"]
    if source:
        parts.append(f"file={source}")
    if event.patient_name:
        parts.append(f"patient={event.patient_name}")
    if event.destination:
        parts.append(f"destination={event.destination}")
    if event.reason:
        parts.append(f"reason={event.reason}")
    return " | ".join(parts)


def status_counts(events: Iterable[XmlCopyEvent]) -> Counter[str]:
    return Counter(event.status for event in events)


if __name__ == "__main__":
    service = XmlAutoCopyService(
        r"C:\Shared Folder\FTPURL",
        r"C:\claims_bot\output",
        r"C:\claims_bot\claims_checker_results\INCOMPLETE",
    )
    summary = service.scan_once(require_observed_stable=False)
    for item in summary.events:
        print(format_event(item))
