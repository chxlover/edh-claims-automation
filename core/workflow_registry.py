"""Workflow — Node Registry (v1).

Catalog of the EXISTING pipeline entry points that can become workflow
nodes. Pure data + validation only — no execution, no GUI, no business
logic. The registry is the single source of truth that the adapters
(`core/workflow_adapters.py`), the engine (`core/workflow_engine.py`)
and the GUI tab (`gui/workflow_tab.py`) all consult.

Design rules (AGENTS.md / task spec):
    - NEVER duplicate business logic: each node only records HOW to
      invoke an existing script/module exactly as a human operator or
      the existing GUI already invokes it.
    - Nodes invoke tool entry points DIRECTLY (never the Tk-prompting
      launchers) so unattended runs never block on a dialog and stop()
      reaches the real tool process.
    - `hbsys_touching=True` nodes need the HBSys window open, maximized,
      on the Billing start screen at 1920x1080 with hands off — the
      engine checks `find_hbsys_window()` before running them (same
      gate the main GUI already applies to Date Fill / XML Clicker).

All commands run with cwd=PROJECT_ROOT (C:\\claims_bot) and inherit the
CLAIMS_* environment the main GUI already builds (`_run_script_thread`).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class NodeSpec:
    """One workflow node type (a catalog entry, not an instance).

    Fields:
        key           — unique registry key (used in workflow_config.json)
        label         — human-readable name shown in the GUI
        category      — grouping label for the palette ("Claims", "HBSys", ...)
        module        — Python module path invoked as a subprocess
        args          — fixed CLI arguments (existing entry-point behavior)
        live_args     — extra arguments appended when running live
        hbsys_touching— node drives the HBSys UI (engine pre-checks HBSys)
        supports_dry  — entry point has a safe dry-run mode (no --live)
        extra_env    — constant environment variables (mirrors the env
                        bridge the main GUI already sets per script)
        description   — one-line purpose for tooltips/logs
    """

    key: str
    label: str
    category: str
    module: str
    args: tuple[str, ...] = ()
    live_args: tuple[str, ...] = ("--live",)
    hbsys_touching: bool = False
    supports_dry: bool = True
    extra_env: dict[str, str] = field(default_factory=dict)
    description: str = ""


# -- The registry ----------------------------------------------------------
#
# Args mirror the existing entry points exactly:
#   * claims_processor  — GUI already runs it with CLAIMS_PROCESS_MODE=multiple
#     and CLAIMS_CONFIRM_PATIENT=0 (edh_claims_gui_XML_COPY_BUTTON.py:2913-2915).
#   * date_fill         — direct invocation of hbsys_fill_dates_testing.py
#     (--live --production-mode --claim-type REGULAR|ABTC), bypassing the
#     Tk-prompting launcher; ABTC needs the --enable-abtc gate flag.
#   * xml_clicker       — direct xml_generator_clicker.py --live.
#   * copy_xml          — python -m core.xml_auto_copy (its __main__ runs a
#     real one-shot scan of FTPURL -> output/INCOMPLETE).
#   * uploaders         — CLI mains already support --live, --confirm-each,
#     --resume and persistent batch state (core/add_claims_state.py etc.).

NODE_REGISTRY: dict[str, NodeSpec] = {
    spec.key: spec
    for spec in (
        # -- Claims processing (subprocess, folder-contract pipeline) -------
        NodeSpec(
            key="claims_processor",
            label="Claims Processor (Scan → Patient Folders)",
            category="Claims",
            module="bot_unknown_trainer_DEFERRED_OUTPUT_REVIEW_PATSUFFIX_ADM_DIS.py",
            args=(),
            live_args=(),  # production engine runs by mode dialog/env
            hbsys_touching=False,
            supports_dry=False,
            extra_env={
                "CLAIMS_PROCESS_MODE": "multiple",
                "CLAIMS_CONFIRM_PATIENT": "0",
            },
            description="OCR scans → patient groups → signed PDF/A folders in output\\",
        ),
        NodeSpec(
            key="date_fill_regular",
            label="Date Fill (REGULAR, discharge date)",
            category="HBSys",
            module="date_fill_hbsys.hbsys_fill_dates_testing",
            args=("--production-mode", "--claim-type", "REGULAR"),
            live_args=("--live",),
            hbsys_touching=True,
            supports_dry=True,
            description="Fill CF2 date fields using the DISCHARGE date (regular claims)",
        ),
        NodeSpec(
            key="date_fill_abtc",
            label="Date Fill (ABTC, admission date)",
            category="HBSys",
            module="date_fill_hbsys.hbsys_fill_dates_testing",
            args=("--production-mode", "--claim-type", "ABTC", "--enable-abtc"),
            live_args=("--live",),
            hbsys_touching=True,
            supports_dry=True,
            description="Fill CF2 date fields using the ADMISSION date (ABTC claims)",
        ),
        NodeSpec(
            key="xml_clicker",
            label="XML Clicker (CF4 → CF5 → eSOA)",
            category="HBSys",
            module="date_fill_hbsys.xml_generator_clicker",
            args=(),
            live_args=("--live",),
            hbsys_touching=True,
            supports_dry=True,
            description="Generate the three HBSys eClaims XMLs per patient (CF4→CF5→eSOA)",
        ),
        NodeSpec(
            key="copy_xml",
            label="Copy XML (FTPURL → Patient Folders)",
            category="Claims",
            module="core.xml_auto_copy",
            args=(),
            live_args=(),  # filesystem service, no live/dry distinction
            hbsys_touching=False,
            supports_dry=False,
            description="Copy generated CF4/CF5/eSOA XMLs into patient folders",
        ),
        NodeSpec(
            key="fees_checker",
            label="Fees Check (charges vs HBSys DB)",
            category="Checks",
            module="fees_checker.py",
            args=(),
            live_args=(),  # read-only checker, always "live"
            hbsys_touching=False,
            supports_dry=False,
            description="Compare itemized vs grouped charges; report ready-to-XML verdicts",
        ),
        NodeSpec(
            key="claims_checker",
            label="Claims Checker (sort READY/INCOMPLETE)",
            category="Checks",
            module="claims_checker.py",
            args=(),
            live_args=(),
            hbsys_touching=False,
            supports_dry=False,
            description="Verify document completeness; move folders to READY / INCOMPLETE",
        ),
        NodeSpec(
            key="claims_checker_recheck",
            label="Recheck INCOMPLETE Folders",
            category="Checks",
            module="claims_checker.py",
            args=(),
            live_args=(),
            hbsys_touching=False,
            supports_dry=False,
            extra_env={"CLAIMS_RECHECK_INCOMPLETE": "1"},
            description="Re-run the claims checker over claims_checker_results\\INCOMPLETE",
        ),
        # -- HBSys eClaims upload automations (CLI mains, resumable) ---------
        NodeSpec(
            key="add_claims_upload",
            label="Add Claims Upload (tag claims in HBSys)",
            category="HBSys Upload",
            module="core.add_claims_uploader",
            args=(),
            live_args=("--live",),
            hbsys_touching=True,
            supports_dry=True,
            description="Search + tag READY patients in eClaims Upload Claims",
        ),
        NodeSpec(
            key="claim_attachments",
            label="Claim Attachments (attach docs + doc types)",
            category="HBSys Upload",
            module="core.claim_attachments_uploader",
            args=(),
            live_args=("--live",),
            hbsys_touching=True,
            supports_dry=True,
            description="Attach PDFs/XMLs per patient, assign doc types, run v7 audit",
        ),
        # -- Optional batch utilities ----------------------------------------
        NodeSpec(
            key="merge_pdf",
            label="Merge PDF (merge_input → PDF/A)",
            category="Utilities",
            module="merge.py",
            args=(),
            live_args=(),
            hbsys_touching=False,
            supports_dry=False,
            description="Merge all PDFs in merge_input\\ into one read-only PDF/A",
        ),
        NodeSpec(
            key="convert_pdfa",
            label="PDF/A Convert (pdfs folder)",
            category="Utilities",
            module="pdfa.py",
            args=(),
            live_args=(),
            hbsys_touching=False,
            supports_dry=False,
            extra_env={"CLAIMS_GUI_MODE": "1"},
            description="Bulk-convert pdfs\\ files to read-only PDF/A ≤1MB",
        ),
    )
}


def get_node_spec(key: str) -> Optional[NodeSpec]:
    """Registry entry for *key*, or None when unknown (never guessed)."""
    return NODE_REGISTRY.get(key)


def node_labels() -> dict[str, str]:
    """key → label mapping for GUI palettes."""
    return {key: spec.label for key, spec in NODE_REGISTRY.items()}


def categories() -> list[str]:
    """Distinct category names in registry order."""
    seen: list[str] = []
    for spec in NODE_REGISTRY.values():
        if spec.category not in seen:
            seen.append(spec.category)
    return seen


def validate_registry() -> list[str]:
    """Deterministic sanity checks for the registry itself.

    Returns a list of problems (empty = healthy). Checks that every
    module entry point actually exists on disk, that keys/labels are
    unique, and that `module` is either an importable package path or
    an existing .py file under PROJECT_ROOT.
    """
    problems: list[str] = []
    labels: set[str] = set()

    def entry_exists(module: str) -> bool:
        # Root script file: "fees_checker.py" / "merge.py" (dotted names
        # only for real packages — a name ending in .py is a file).
        if module.endswith(".py"):
            return (PROJECT_ROOT / module).is_file()
        # Package path: "core.add_claims_uploader" → core\add_claims_uploader.py
        candidate = PROJECT_ROOT / (module.replace(".", "\\") + ".py")
        if candidate.is_file():
            return True
        # package with __main__.py entry
        return (PROJECT_ROOT / module.replace(".", "\\") / "__main__.py").is_file()

    for spec in NODE_REGISTRY.values():
        if spec.label in labels:
            problems.append(f"duplicate label: {spec.label!r}")
        labels.add(spec.label)
        if not entry_exists(spec.module):
            problems.append(
                f"{spec.key}: entry point not found: {spec.module}"
            )
    if len(NODE_REGISTRY) != len({s.key for s in NODE_REGISTRY.values()}):
        problems.append("duplicate keys in registry")
    return problems


# -- standalone test (AGENTS.md: every module must support __main__) -------

if __name__ == "__main__":
    failures = 0

    def check(label: str, ok: bool) -> None:
        global failures
        if not ok:
            failures += 1
        print(f"{'OK  ' if ok else 'FAIL'} {label}")

    check(
        "registry: 12 catalog entries",
        len(NODE_REGISTRY) == 12,
    )
    check(
        "registry: keys are unique",
        len({s.key for s in NODE_REGISTRY.values()}) == len(NODE_REGISTRY),
    )
    check(
        "registry: labels are unique",
        len({s.label for s in NODE_REGISTRY.values()}) == len(NODE_REGISTRY),
    )
    check(
        "lookup: known key returns spec, unknown returns None",
        get_node_spec("claims_processor") is not None
        and get_node_spec("nope") is None,
    )
    check(
        "registry: HBSys-touching nodes flagged",
        {k for k, s in NODE_REGISTRY.items() if s.hbsys_touching}
        == {"date_fill_regular", "date_fill_abtc", "xml_clicker",
            "add_claims_upload", "claim_attachments"},
    )
    check(
        "registry: dry-run capable nodes flagged",
        all(
            NODE_REGISTRY[k].supports_dry
            for k in ("date_fill_regular", "xml_clicker",
                      "add_claims_upload", "claim_attachments")
        ),
    )
    check(
        "registry: claims_processor carries the env the GUI already sets",
        NODE_REGISTRY["claims_processor"].extra_env
        == {"CLAIMS_PROCESS_MODE": "multiple", "CLAIMS_CONFIRM_PATIENT": "0"},
    )
    check(
        "registry: date_fill args bypass the prompting launcher",
        NODE_REGISTRY["date_fill_regular"].args
        == ("--production-mode", "--claim-type", "REGULAR")
        and NODE_REGISTRY["date_fill_abtc"].args
        == ("--production-mode", "--claim-type", "ABTC", "--enable-abtc")
        and NODE_REGISTRY["date_fill_regular"].module
        == "date_fill_hbsys.hbsys_fill_dates_testing",
    )
    check(
        "registry: categories cover all nodes",
        set(categories())
        == {s.category for s in NODE_REGISTRY.values()},
    )
    check(
        "validation: registry is healthy (entry points exist)",
        validate_registry() == [],
    )
    # entry-point existence, verified explicitly for the report
    for key in ("claims_processor", "date_fill_regular", "xml_clicker",
                "copy_xml", "fees_checker", "claims_checker",
                "add_claims_upload", "claim_attachments",
                "merge_pdf", "convert_pdfa"):
        spec = NODE_REGISTRY[key]
        ok = (
            (PROJECT_ROOT / spec.module).is_file()
            if spec.module.endswith(".py")
            else (
                (PROJECT_ROOT / (spec.module.replace(".", "\\") + ".py")).is_file()
                or (
                    PROJECT_ROOT / spec.module.replace(".", "\\") / "__main__.py"
                ).is_file()
            )
        )
        check(f"entry point on disk: {key} -> {spec.module}", ok)

    print("RESULT:", "PASSED" if failures == 0 else f"{failures} FAILURE(S)")
    raise SystemExit(0 if failures == 0 else 1)
