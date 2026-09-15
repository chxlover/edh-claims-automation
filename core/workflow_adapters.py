"""Workflow — Thin Adapters (v1).

`ScriptNodeAdapter` turns one registry `NodeSpec` into a runnable,
stoppable subprocess node — WITHOUT duplicating any business logic.

It reproduces exactly what the main GUI already does for scripts
(`edh_claims_gui_XML_COPY_BUTTON.py:_run_script_thread`):

    * [sys.executable, <entry point>] launched at PROJECT_ROOT
    * stdout/stderr piped, streamed line-by-line to a callback
    * CLAIMS_* env bridge (settings + node extra_env)
    * stop via Popen.terminate()

Differences from the GUI runner (deliberate, minimal):
    * NO window focus / Tk messagebox handling — the adapter is a
      headless worker used by the workflow engine.
    * Entry points are invoked DIRECTLY (never the Tk-prompting
      launchers), so an unattended workflow never blocks on a dialog
      and terminate() reaches the real tool process.
    * The adapter returns an exit code instead of touching GUI state.

Module-path convention (mirrors `core/workflow_registry.py`):
    "core.add_claims_uploader"          → python -m core.add_claims_uploader
    "date_fill_hbsys.hbsys_fill_dates_testing"
                                        → python -m date_fill_hbsys.hbsys_fill_dates_testing
    "fees_checker.py"                   → python fees_checker.py
    "bot_..._ADM_DIS.py"                → python bot_..._ADM_DIS.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from core.workflow_registry import NodeSpec, get_node_spec

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Default CLAIMS_* environment bridge — same keys/values the main GUI
# passes to every script run (see _run_script_thread). The values are
# taken from *settings* by the caller (engine/GUI) and merged with the
# node's own extra_env here.
DEFAULT_CLAIMS_ENV_KEYS = (
    "CLAIMS_ENABLE_AUTO_SIGN",
    "CLAIMS_ENABLE_AUTO_SIGN_CSF",
    "CLAIMS_ENABLE_AUTO_SIGN_CF2",
    "CLAIMS_ENABLE_DATE_SIGNED",
    "CLAIMS_ENABLE_BACKUP",
    "CLAIMS_SHOW_DEBUG_LOGS",
    "CLAIMS_SCAN_FOLDER",
    "CLAIMS_OUTPUT_FOLDER",
    "CLAIMS_BACKUP_FOLDER",
    "CLAIMS_REVIEW_STAGING_FOLDER",
    "CLAIMS_SIGNATURE_FOLDER",
    "CLAIMS_SQLITE_DB",
    "CLAIMS_GUI_MODE",
)

# Statuses an adapter run can report (exact vocabulary)
STATUS_RUNNING = "RUNNING"
STATUS_DONE = "DONE"
STATUS_FAILED = "FAILED"
STATUS_STOPPED = "STOPPED"


def build_command(spec: NodeSpec, *, live: bool) -> list[str]:
    """Full argv for one node — deterministic, registry-driven.

    `live=True` appends the spec's live_args (existing --live flags);
    `live=False` omits them, which for supports_dry entry points is the
    tool's own dry-run mode (never a guess — the tools themselves
    decide what no---live means).
    """
    parts = [sys.executable]
    if spec.module.endswith(".py"):
        parts.append(spec.module)
    else:
        parts.extend(("-m", spec.module))
    parts.extend(spec.args)
    if live:
        parts.extend(spec.live_args)
    return parts


def build_env(
    spec: NodeSpec,
    settings: Optional[dict],
    base_env: Optional[dict] = None,
) -> dict:
    """Environment for one node run.

    *base_env* (usually os.environ) is copied, the CLAIMS_* bridge is
    applied from *settings* (only keys present in settings — same
    values the GUI would set), then the node's extra_env wins last
    (it mirrors per-script envs the GUI already hard-codes, e.g.
    CLAIMS_PROCESS_MODE for the claims processor).
    """
    env = dict(base_env if base_env is not None else os.environ)
    if settings:
        mapping = {
            "CLAIMS_ENABLE_AUTO_SIGN": (
                "1" if settings.get("enable_auto_sign", True) else "0"
            ),
            "CLAIMS_ENABLE_AUTO_SIGN_CSF": (
                "1" if settings.get("enable_auto_sign_csf", True) else "0"
            ),
            "CLAIMS_ENABLE_AUTO_SIGN_CF2": (
                "1" if settings.get("enable_auto_sign_cf2", True) else "0"
            ),
            "CLAIMS_ENABLE_DATE_SIGNED": (
                "1" if settings.get("enable_date_signed", True) else "0"
            ),
            "CLAIMS_ENABLE_BACKUP": (
                "1" if settings.get("enable_backup", True) else "0"
            ),
            "CLAIMS_SHOW_DEBUG_LOGS": (
                "1" if settings.get("show_debug_logs", True) else "0"
            ),
            "CLAIMS_SCAN_FOLDER": settings.get("scan_folder"),
            "CLAIMS_OUTPUT_FOLDER": settings.get("output_folder"),
            "CLAIMS_BACKUP_FOLDER": settings.get("backup_folder"),
            "CLAIMS_REVIEW_STAGING_FOLDER": settings.get("review_staging_folder"),
            "CLAIMS_SIGNATURE_FOLDER": settings.get("signature_folder"),
            "CLAIMS_SQLITE_DB": settings.get("sqlite_db"),
            "CLAIMS_GUI_MODE": "1",
        }
        for key, value in mapping.items():
            if value is not None:
                env[key] = str(value)
    env.update(spec.extra_env)
    return env


class ScriptNodeAdapter:
    """One running workflow node (a subprocess wrapper).

    Usage by the engine:

        adapter = ScriptNodeAdapter(spec, settings, on_output=log)
        code = adapter.run(live=True)          # blocking; streams output
        # — or, to stop mid-run from another thread:
        adapter.stop()                          # terminate() + status

    Thread-safety: `run()` blocks the calling (worker) thread;
    `stop()` may be called from any thread. Not re-usable after run().
    """

    def __init__(
        self,
        spec: NodeSpec,
        settings: Optional[dict] = None,
        on_output: Optional[Callable[[str], None]] = None,
    ) -> None:
        if get_node_spec(spec.key) is None:
            raise ValueError(f"unknown node type: {spec.key!r}")
        self.spec = spec
        self.settings = settings
        self.on_output = on_output or (lambda line: None)
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._stopped = False

    # -- execution --------------------------------------------------------

    def run(self, *, live: bool = True) -> str:
        """Run the node to completion; returns final status string.

        Statuses: DONE (exit 0) | FAILED (non-zero exit) |
        STOPPED (terminate() was requested first).
        """
        command = build_command(self.spec, live=live)
        env = build_env(self.spec, self.settings)
        self._emit(f"[workflow] {self.spec.label}: "
                   + " ".join(command))

        with self._lock:
            self._process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=env,
                # no new console window when launched from pythonw/GUI
                creationflags=subprocess.CREATE_NO_WINDOW
                if os.name == "nt" else 0,
            )
        try:
            assert self._process.stdout is not None
            for line in self._process.stdout:
                self._emit(line.rstrip("\r\n"))
            code = self._process.wait()
        finally:
            with self._lock:
                self._process = None

        if self._stopped:
            return STATUS_STOPPED
        return STATUS_DONE if code == 0 else STATUS_FAILED

    # -- control ----------------------------------------------------------

    def stop(self) -> None:
        """Terminate the node's subprocess (cooperative stop).

        Mirrors the main GUI's Stop Process: terminate() the current
        script; the tool keeps its own resume state (batch JSONs,
        recheck, prechecks) exactly as with the GUI's stop button.
        """
        self._stopped = True
        with self._lock:
            process = self._process
        if process is not None:
            try:
                process.terminate()
            except Exception:
                pass

    # -- internals ----------------------------------------------------------

    def _emit(self, line: str) -> None:
        try:
            self.on_output(line)
        except Exception:
            pass  # logging must never break the workflow


# -- standalone test (AGENTS.md: every module must support __main__) -------

if __name__ == "__main__":
    import time

    failures = 0

    def check(label: str, ok: bool) -> None:
        global failures
        if not ok:
            failures += 1
        print(f"{'OK  ' if ok else 'FAIL'} {label}")

    # build_command: registry-driven argv, dry vs live
    spec = get_node_spec("add_claims_upload")
    dry_cmd = build_command(spec, live=False)
    live_cmd = build_command(spec, live=True)
    check(
        "command: module path -> python -m core.add_claims_uploader",
        dry_cmd == [sys.executable, "-m", "core.add_claims_uploader"]
        and live_cmd[-1] == "--live",
    )
    spec_df = get_node_spec("date_fill_regular")
    df_cmd = build_command(spec_df, live=True)
    check(
        "command: date_fill direct argv (no prompting launcher)",
        df_cmd == [
            sys.executable, "-m", "date_fill_hbsys.hbsys_fill_dates_testing",
            "--production-mode", "--claim-type", "REGULAR", "--live",
        ],
    )
    spec_fee = get_node_spec("fees_checker")
    check(
        "command: root script file -> python fees_checker.py",
        build_command(spec_fee, live=True)
        == [sys.executable, "fees_checker.py"],
    )
    spec_cp = get_node_spec("claims_processor")
    check(
        "command: claims_processor runs bare (env-driven)",
        build_command(spec_cp, live=True)
        == [sys.executable,
            "bot_unknown_trainer_DEFERRED_OUTPUT_REVIEW_PATSUFFIX_ADM_DIS.py"],
    )

    # build_env: settings bridge + node extra_env precedence
    settings = {
        "output_folder": r"C:\claims_bot\output",
        "enable_backup": False,
        "scan_folder": r"C:\claims_bot\scans",
    }
    env = build_env(spec_cp, settings, base_env={"PATH": "x"})
    check(
        "env: settings bridge applied",
        env["CLAIMS_OUTPUT_FOLDER"] == r"C:\claims_bot\output"
        and env["CLAIMS_ENABLE_BACKUP"] == "0"
        and env["CLAIMS_GUI_MODE"] == "1"
        and env["PATH"] == "x",
    )
    check(
        "env: node extra_env wins (process mode)",
        env["CLAIMS_PROCESS_MODE"] == "multiple"
        and env["CLAIMS_CONFIRM_PATIENT"] == "0",
    )
    env2 = build_env(spec_fee, None, base_env={"PATH": "x"})
    check(
        "env: no settings -> only base env + extra_env",
        env2 == {"PATH": "x"},
    )

    # adapter: unknown node type refused (never guessed)
    try:
        ScriptNodeAdapter(NodeSpec(
            key="nope", label="x", category="x", module="x.py",
        ))
        check("adapter: unknown node type refused", False)
    except ValueError:
        check("adapter: unknown node type refused", True)

    # adapter: run a real echo subprocess -> DONE + streamed output.
    # The echo spec is registered in the REAL registry for the duration
    # of the test (then removed) so the adapter's registry guard — the
    # same guard the engine relies on — is exercised on the true path.
    echo_spec = NodeSpec(
        key="echo_test", label="Echo", category="Test",
        module="workflow_echo_test.py",  # created below in PROJECT_ROOT
        args=(), live_args=(), supports_dry=True,
    )
    from core.workflow_registry import NODE_REGISTRY
    NODE_REGISTRY[echo_spec.key] = echo_spec
    echo_file = PROJECT_ROOT / "workflow_echo_test.py"
    echo_file.write_text(
        "import sys\nprint('adapter-line-1')\nprint('adapter-line-2')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    try:
        lines: list[str] = []
        adapter = ScriptNodeAdapter(echo_spec, on_output=lines.append)
        status = adapter.run(live=True)
        check(
            "adapter: success run -> DONE + streamed lines",
            status == STATUS_DONE
            and "adapter-line-1" in lines
            and "adapter-line-2" in lines
            and any("workflow_echo_test.py" in ln for ln in lines),
        )

        # adapter: failing exit code -> FAILED
        echo_file.write_text(
            "import sys\nprint('boom')\nsys.exit(3)\n", encoding="utf-8"
        )
        adapter2 = ScriptNodeAdapter(echo_spec, on_output=lambda _l: None)
        check(
            "adapter: non-zero exit -> FAILED",
            adapter2.run(live=True) == STATUS_FAILED,
        )

        # adapter: stop() terminates a long-running subprocess
        echo_file.write_text(
            "import time, sys\nprint('starting')\nsys.stdout.flush()\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        adapter3 = ScriptNodeAdapter(echo_spec, on_output=lambda _l: None)
        result_box: dict[str, str] = {}

        def runner() -> None:
            result_box["status"] = adapter3.run(live=True)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        time.sleep(1.5)  # let the child start and reach the sleep
        adapter3.stop()
        thread.join(timeout=10)
        check(
            "adapter: stop() -> STOPPED (terminate reaches the tool)",
            result_box.get("status") == STATUS_STOPPED
            and not thread.is_alive(),
        )
    finally:
        echo_file.unlink(missing_ok=True)
        NODE_REGISTRY.pop("echo_test", None)

    print("RESULT:", "PASSED" if failures == 0 else f"{failures} FAILURE(S)")
    raise SystemExit(0 if failures == 0 else 1)
