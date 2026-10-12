"""Configuration for the deterministic Claims Agent pipeline.

Reads the existing project configuration (``claims_gui_config.json``) and
applies environment overrides, then validates. No new config file format.

Retry semantics (documented once, here):
    * ``max_fee_cycle_retries`` — number of EXTRA
      date_fill -> final_bill -> fees_check cycles after the FIRST
      "NO / NOT READY" second-pass result. Total second-pass fees
      evaluations = 1 + max_fee_cycle_retries. Default 2.
    * ``xml_max_retries`` — extra XML Clicker attempts after the first
      failed generation. Default 1.
    * ``hbsys_max_retries`` — extra attempts for the non-idempotent
      HBSys stages (Add Claims / upload / attachments). Default 0:
      a failed or uncertain HBSys operation is HELD for verification
      instead of blindly repeated (avoids duplicate uploads).

Standalone test:
    python core/agent/pipeline_config.py
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(r"C:\claims_bot")
CONFIG_FILE = BASE_DIR / "claims_gui_config.json"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


@dataclass
class PipelineConfig:
    """All settings the Claims Agent needs. Sensible, documented defaults."""

    scan_folder: Path = field(default_factory=lambda: BASE_DIR / "scans")
    output_folder: Path = field(default_factory=lambda: BASE_DIR / "output")
    xml_source_folder: Path = field(
        default_factory=lambda: Path(r"C:\Shared Folder\FTPURL")
    )
    report_dir: Path = field(default_factory=lambda: BASE_DIR / "reports")
    state_db: Path = field(
        default_factory=lambda: BASE_DIR / "claims_pipeline.db"
    )
    max_fee_cycle_retries: int = 2
    xml_max_retries: int = 1
    hbsys_max_retries: int = 0
    live_hbsys: bool = False
    auto_open_report: bool = False
    resume: bool = True
    batch_id: str = ""

    def problems(self) -> list[str]:
        """Validation problems (empty = valid). Missing scan/output is fatal;
        an uncreatable report dir is fatal; everything else falls back."""
        issues: list[str] = []
        if not self.scan_folder.is_dir():
            issues.append(f"scan folder missing: {self.scan_folder}")
        if not self.output_folder.is_dir():
            issues.append(f"output folder missing: {self.output_folder}")
        try:
            self.report_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            issues.append(f"report dir not writable: {self.report_dir} ({exc})")
        for name in (
            "max_fee_cycle_retries",
            "xml_max_retries",
            "hbsys_max_retries",
        ):
            if getattr(self, name) < 0:
                issues.append(f"{name} must be >= 0")
        return issues


def load_config(overrides: dict | None = None) -> PipelineConfig:
    """Build a PipelineConfig from claims_gui_config.json + env + overrides.

    Precedence (lowest first): defaults < claims_gui_config.json
    < environment < explicit ``overrides`` dict.
    """
    file_values: dict = {}
    try:
        if CONFIG_FILE.is_file():
            file_values = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        file_values = {}

    def pick(key: str, env_name: str, default: Path) -> Path:
        for source in (
            (overrides or {}).get(key),
            os.getenv(env_name),
            file_values.get(key),
        ):
            if source:
                return Path(str(source))
        return default

    defaults = PipelineConfig()
    cfg = PipelineConfig(
        scan_folder=pick("scan_folder", "CLAIMS_SCAN_FOLDER", defaults.scan_folder),
        output_folder=pick(
            "output_folder", "CLAIMS_OUTPUT_FOLDER", defaults.output_folder
        ),
        xml_source_folder=pick(
            "xml_source_folder", "CLAIMS_XML_SOURCE_FOLDER", defaults.xml_source_folder
        ),
        report_dir=pick("report_dir", "CLAIMS_REPORT_DIR", defaults.report_dir),
        state_db=pick("state_db", "CLAIMS_PIPELINE_DB", defaults.state_db),
        max_fee_cycle_retries=_env_int(
            "CLAIMS_MAX_FEE_RETRIES", defaults.max_fee_cycle_retries
        ),
        xml_max_retries=_env_int("CLAIMS_XML_MAX_RETRIES", defaults.xml_max_retries),
        hbsys_max_retries=_env_int(
            "CLAIMS_HBSYS_MAX_RETRIES", defaults.hbsys_max_retries
        ),
        live_hbsys=_env_bool("CLAIMS_LIVE_HBSYS", defaults.live_hbsys),
        auto_open_report=_env_bool(
            "CLAIMS_AUTO_OPEN_REPORT", defaults.auto_open_report
        ),
        resume=_env_bool("CLAIMS_RESUME", defaults.resume),
        batch_id=str(
            (overrides or {}).get("batch_id", os.getenv("CLAIMS_BATCH_ID", ""))
        ),
    )
    if overrides:
        for key in (
            "max_fee_cycle_retries",
            "xml_max_retries",
            "hbsys_max_retries",
            "live_hbsys",
            "auto_open_report",
            "resume",
            "batch_id",
        ):
            if key in overrides and overrides[key] is not None:
                setattr(cfg, key, overrides[key])
    return cfg


if __name__ == "__main__":
    config = load_config()
    print("scan folder:  ", config.scan_folder)
    print("output folder:", config.output_folder)
    print("xml source:   ", config.xml_source_folder)
    print("report dir:   ", config.report_dir)
    print("state db:     ", config.state_db)
    print("fee retries:  ", config.max_fee_cycle_retries)
    print("xml retries:  ", config.xml_max_retries)
    print("hbsys retries:", config.hbsys_max_retries)
    print("live hbsys:   ", config.live_hbsys)
    problems = config.problems()
    print("pipeline_config standalone check:", "OK" if not problems else problems)
