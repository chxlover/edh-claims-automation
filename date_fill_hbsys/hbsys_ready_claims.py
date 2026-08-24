from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = Path(r"C:\claims_bot\output")


def _path_from_env(name: str) -> Path | None:
    value = os.getenv(name, "").strip().strip('"')
    if not value:
        return None
    return Path(value)


def resolve_default_ready_dir() -> Path:
    """
    Resolve the Date Fill source folder.

    Date Fill is intentionally local/simple again: it reads the configured
    claims output folder only, with C:\\claims_bot\\output as fallback.
    """
    gui_output = _path_from_env("CLAIMS_OUTPUT_FOLDER")
    if gui_output:
        return gui_output

    return DEFAULT_OUTPUT_DIR


DEFAULT_READY_DIR = resolve_default_ready_dir()
CLAIM_FOLDER_RE = re.compile(
    r"^(?P<name>.+?)\s+-\s+(?P<hospital_no>\d+)\s+-\s+"
    r"ADM(?P<admission>\d{8})_DIS(?P<discharge>\d{8})$"
)


@dataclass(frozen=True)
class ReadyClaim:
    folder: Path
    patient_name: str
    hospital_no: str
    admission_date: datetime
    discharge_date: datetime

    @property
    def admission_hbsys(self) -> str:
        return self.admission_date.strftime("%m-%d-%Y")

    @property
    def discharge_hbsys(self) -> str:
        return self.discharge_date.strftime("%m-%d-%Y")

    @property
    def admission_grid(self) -> str:
        return self.admission_date.strftime("%m/%d/%Y")

    @property
    def discharge_grid(self) -> str:
        return self.discharge_date.strftime("%m/%d/%Y")


def parse_yyyymmdd(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d")


def parse_ready_claim_folder(folder: Path) -> ReadyClaim | None:
    match = CLAIM_FOLDER_RE.match(folder.name)
    if not match:
        return None

    return ReadyClaim(
        folder=folder,
        patient_name=match.group("name").strip(),
        hospital_no=match.group("hospital_no"),
        admission_date=parse_yyyymmdd(match.group("admission")),
        discharge_date=parse_yyyymmdd(match.group("discharge")),
    )


def load_ready_claims(ready_dir: Path = DEFAULT_READY_DIR) -> list[ReadyClaim]:
    claims: list[ReadyClaim] = []
    if not ready_dir.exists():
        return claims

    for folder in sorted(ready_dir.iterdir(), key=lambda path: path.name.upper()):
        if not folder.is_dir():
            continue
        claim = parse_ready_claim_folder(folder)
        if claim:
            claims.append(claim)
    return claims


def main() -> int:
    parser = argparse.ArgumentParser(description="List HBSys claims from the local output folder.")
    parser.add_argument("--ready-dir", type=Path, default=None)
    parser.add_argument("--hospital-no", help="Filter by exact hospital number.")
    args = parser.parse_args()

    source_dir = args.ready_dir or DEFAULT_READY_DIR
    claims = load_ready_claims(source_dir)
    if args.hospital_no:
        claims = [claim for claim in claims if claim.hospital_no == args.hospital_no]

    if not claims:
        print(f"No claims found in source folder: {source_dir}")
        return 0

    print(f"Source folder: {source_dir}")
    print(f"Claims: {len(claims)}")
    for index, claim in enumerate(claims, start=1):
        print(
            f"{index}. {claim.patient_name} | hospital_no={claim.hospital_no} | "
            f"admission={claim.admission_hbsys} | discharge={claim.discharge_hbsys} | "
            f"folder={claim.folder}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
