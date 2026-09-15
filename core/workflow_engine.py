"""Workflow — Engine + Persistence (v1).

Sequential workflow executor over the node registry
(`core/workflow_registry.py`) via thin subprocess adapters
(`core/workflow_adapters.py`). Existing business logic is NEVER
duplicated — the engine only decides WHAT runs, WHEN, and in WHICH
order, exactly mirroring the manual operator pipeline that today is
enforced by folder contracts (scans\\ → output\\ → READY\\ → eClaimsDoc).

Data model (minimum, per task spec):

    WorkflowConfig { version, nodes:[NodeInstance], connections:[Connection] }
    NodeInstance   { id, node_type, enabled, order, position, params }
    Connection     { source_id, target_id, enabled }

Execution rules (v1 — deliberately simple, matching reality):
    * STRICTLY SEQUENTIAL by `order` over enabled nodes. All HBSys
      automations share one window/mouse — no parallelism exists today
      and none is introduced.
    * Connections are stored/rendered metadata; they visualize the chain
      and are kept consistent with node order. Graph traversal and
      branching/conditions are OUT OF SCOPE for v1 (the current
      pipeline has no branching).
    * Failure policy: a FAILED node stops the workflow by default
      (fail-safe: never continue an HBSys chain after an error) unless
      the node's params set continue_on_fail=True.
    * Stop: terminate() the current node's subprocess; remaining nodes
      are skipped (SKIPPED_STOPPED). Each tool keeps its own resume
      behavior (batch state JSONs, recheck, prechecks) exactly as with
      the main GUI's Stop button.
    * HBSys gate: before any hbsys_touching node, verify the HBSys
      window exists (`date_fill_hbsys.hbsys_window.find_hbsys_window`)
      — the same pre-check the main GUI already applies to Date Fill /
      XML Clicker buttons. Missing HBSys -> node is skipped with reason.
    * Unknown registry keys, missing/invalid config files: REFUSED —
      never guessed (Restore Default is the caller's remedy).

Persistence: `C:\\claims_bot\\workflow_config.json` — same JSON style as
the existing `claims_gui_config.json` (load_json/save_json pattern).
A missing or corrupt file never runs; the GUI offers Restore Default.

DEFAULT_WORKFLOW reproduces the current manual happy-path pipeline
(README pipeline order):
    claims_processor → date_fill_regular → xml_clicker → copy_xml →
    fees_checker → claims_checker → add_claims_upload →
    claim_attachments
(archive stays a manual GUI action in v1; INCOMPLETE/review outcomes
remain handled by the existing manual tools, unchanged.)
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.workflow_adapters import ScriptNodeAdapter
from core.workflow_registry import NODE_REGISTRY, NodeSpec, get_node_spec

CONFIG_VERSION = 1
WORKFLOW_CONFIG_FILE = Path(__file__).resolve().parent.parent / "workflow_config.json"

# Node execution statuses (exact vocabulary)
NODE_STATUS_PENDING = "PENDING"
NODE_STATUS_RUNNING = "RUNNING"
NODE_STATUS_DONE = "DONE"
NODE_STATUS_FAILED = "FAILED"
NODE_STATUS_STOPPED = "STOPPED"
NODE_STATUS_SKIPPED = "SKIPPED"
NODE_STATUS_SKIPPED_STOPPED = "SKIPPED_STOPPED"

# Workflow-level results
WORKFLOW_COMPLETED = "COMPLETED"
WORKFLOW_FAILED = "FAILED"
WORKFLOW_STOPPED = "STOPPED"
WORKFLOW_REFUSED = "REFUSED"


# -- data model ------------------------------------------------------------


@dataclass
class NodeInstance:
    """One node placed in a workflow (an instance of a registry spec)."""

    id: str
    node_type: str                    # registry key — validated on load
    enabled: bool = True
    order: int = 0                    # 1..N execution sequence
    position: dict = field(default_factory=dict)   # {"x": int, "y": int}
    params: dict = field(default_factory=dict)     # {"continue_on_fail": bool, ...}

    def spec(self) -> Optional[NodeSpec]:
        return get_node_spec(self.node_type)


@dataclass
class Connection:
    """A visual connection between two node instances."""

    source_id: str
    target_id: str
    enabled: bool = True


@dataclass
class WorkflowConfig:
    """A whole saved workflow (what the JSON file contains)."""

    version: int = CONFIG_VERSION
    nodes: list[NodeInstance] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)


@dataclass
class NodeRunResult:
    """Outcome of one node inside a workflow run (for reporting/GUI)."""

    node_id: str
    node_type: str
    label: str
    status: str
    reason: str = ""


# -- default workflow -------------------------------------------------------


def default_workflow() -> WorkflowConfig:
    """The DEFAULT WORKFLOW — the current manual pipeline order.

    Deep-copied fresh on every call so restoring the default can never
    be affected by a mutated instance. Positions form a simple left-to-
    right canvas layout; connections chain the enabled happy path.
    """
    keys = [
        "claims_processor",
        "date_fill_regular",
        "xml_clicker",
        "copy_xml",
        "fees_checker",
        "claims_checker",
        "add_claims_upload",
        "claim_attachments",
    ]
    nodes = [
        NodeInstance(
            id=f"n{index}",
            node_type=key,
            enabled=True,
            order=index,
            position={"x": 40 + (index - 1) * 180, "y": 60},
        )
        for index, key in enumerate(keys, start=1)
    ]
    connections = [
        Connection(source_id=nodes[i].id, target_id=nodes[i + 1].id)
        for i in range(len(nodes) - 1)
    ]
    return WorkflowConfig(nodes=nodes, connections=connections)


# -- persistence -------------------------------------------------------------


class WorkflowConfigError(Exception):
    """Raised when a config file is missing/corrupt/invalid — never guessed."""


def save_workflow(config: WorkflowConfig, path: Path = WORKFLOW_CONFIG_FILE) -> Path:
    """Persist the workflow to JSON (same style as claims_gui_config.json)."""
    validate_config(config)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": config.version,
        "nodes": [asdict(node) for node in config.nodes],
        "connections": [asdict(conn) for conn in config.connections],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def load_workflow(path: Path = WORKFLOW_CONFIG_FILE) -> WorkflowConfig:
    """Load + validate a saved workflow.

    Raises WorkflowConfigError for: missing file, invalid JSON, wrong
    schema, unknown node types, duplicate node ids, bad orders. The
    caller (GUI) offers Restore Default — the engine never guesses.
    """
    path = Path(path)
    if not path.is_file():
        raise WorkflowConfigError(f"workflow config not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise WorkflowConfigError(f"workflow config unreadable: {exc}") from exc

    if not isinstance(raw, dict) or "nodes" not in raw or "connections" not in raw:
        raise WorkflowConfigError("workflow config schema invalid")
    if int(raw.get("version", 0)) != CONFIG_VERSION:
        raise WorkflowConfigError(
            f"workflow config version {raw.get('version')!r} unsupported"
        )

    nodes: list[NodeInstance] = []
    seen_ids: set[str] = set()
    for item in raw["nodes"]:
        if not isinstance(item, dict) or "id" not in item or "node_type" not in item:
            raise WorkflowConfigError(f"workflow node schema invalid: {item!r}")
        node = NodeInstance(
            id=str(item["id"]),
            node_type=str(item["node_type"]),
            enabled=bool(item.get("enabled", True)),
            order=int(item.get("order", 0)),
            position=dict(item.get("position") or {}),
            params=dict(item.get("params") or {}),
        )
        if node.id in seen_ids:
            raise WorkflowConfigError(f"duplicate node id: {node.id}")
        if get_node_spec(node.node_type) is None:
            raise WorkflowConfigError(
                f"unknown node type: {node.node_type!r} "
                f"(node {node.id}) — Restore Default to fix"
            )
        seen_ids.add(node.id)
        nodes.append(node)

    node_ids = seen_ids
    connections: list[Connection] = []
    for item in raw["connections"]:
        if not isinstance(item, dict) or "source_id" not in item or "target_id" not in item:
            raise WorkflowConfigError(f"workflow connection schema invalid: {item!r}")
        conn = Connection(
            source_id=str(item["source_id"]),
            target_id=str(item["target_id"]),
            enabled=bool(item.get("enabled", True)),
        )
        if conn.source_id not in node_ids or conn.target_id not in node_ids:
            raise WorkflowConfigError(
                f"connection references unknown node: "
                f"{conn.source_id} -> {conn.target_id}"
            )
        connections.append(conn)

    config = WorkflowConfig(
        version=CONFIG_VERSION, nodes=nodes, connections=connections
    )
    validate_config(config)
    return config


def validate_config(config: WorkflowConfig) -> None:
    """In-memory sanity validation (used by save AND load)."""
    seen_ids: set[str] = set()
    for node in config.nodes:
        if node.id in seen_ids:
            raise WorkflowConfigError(f"duplicate node id: {node.id}")
        seen_ids.add(node.id)
        if get_node_spec(node.node_type) is None:
            raise WorkflowConfigError(
                f"unknown node type: {node.node_type!r} (node {node.id})"
            )


# -- engine -------------------------------------------------------------------


@dataclass
class WorkflowRunReport:
    """Final outcome of one run — statuses per node in execution order."""

    result: str                       # COMPLETED | FAILED | STOPPED | REFUSED
    reason: str
    node_results: list[NodeRunResult] = field(default_factory=list)


class WorkflowEngine:
    """Sequential workflow runner — one run at a time (single-run guard).

    Callbacks (all optional; GUI passes Tk-marshalled lambdas):
        on_node_status(node: NodeInstance, status: str, reason: str)
        on_output(line: str)                       — streamed child stdout
        on_finished(report: WorkflowRunReport)

    Usage:

        engine = WorkflowEngine(settings_getter)
        report = engine.run(config, live=True)      # blocking, worker thread
        engine.stop()                               # from another thread
    """

    def __init__(
        self,
        settings_getter: Optional[Callable[[], dict]] = None,
        on_node_status: Optional[
            Callable[[NodeInstance, str, str], None]
        ] = None,
        on_output: Optional[Callable[[str], None]] = None,
        on_finished: Optional[
            Callable[[WorkflowRunReport], None]
        ] = None,
    ) -> None:
        self.settings_getter = settings_getter or (lambda: {})
        self.on_node_status = on_node_status or (lambda *_: None)
        self.on_output = on_output or (lambda _line: None)
        self.on_finished = on_finished or (lambda _report: None)
        self._current_adapter: Optional[ScriptNodeAdapter] = None
        self._adapter_lock = threading.Lock()
        self._running = False
        self._stop_requested = False

    # -- state ----------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    def stop(self) -> None:
        """Request stop: terminate the current node, skip the rest."""
        self._stop_requested = True
        with self._adapter_lock:
            adapter = self._current_adapter
        if adapter is not None:
            adapter.stop()

    # -- execution --------------------------------------------------------

    def run(self, config: WorkflowConfig, *, live: bool = True) -> WorkflowRunReport:
        """Execute the workflow sequentially by node order.

        Returns a WorkflowRunReport; also invokes on_finished. Runs are
        serialized (a second run while running is REFUSED, never raced).
        """
        if self._running:
            report = WorkflowRunReport(
                result=WORKFLOW_REFUSED,
                reason="a workflow is already running",
            )
            self.on_finished(report)
            return report

        # Validate BEFORE running — never guess unknown nodes.
        try:
            validate_config(config)
        except WorkflowConfigError as exc:
            report = WorkflowRunReport(result=WORKFLOW_REFUSED, reason=str(exc))
            self.on_finished(report)
            return report

        if not any(node.enabled for node in config.nodes):
            report = WorkflowRunReport(
                result=WORKFLOW_REFUSED, reason="no enabled nodes in workflow"
            )
            self.on_finished(report)
            return report

        settings = dict(self.settings_getter() or {})
        ordered = sorted(
            (node for node in config.nodes if node.enabled),
            key=lambda node: node.order,
        )
        report = WorkflowRunReport(result=WORKFLOW_COMPLETED, reason="")
        self._running = True
        self._stop_requested = False
        try:
            for node in ordered:
                if self._stop_requested:
                    self._record(
                        report, node, NODE_STATUS_SKIPPED_STOPPED, "workflow stopped"
                    )
                    continue

                spec = node.spec()
                if spec is None:  # validated above; defensive only
                    self._record(report, node, NODE_STATUS_SKIPPED, "unknown node")
                    report.result = WORKFLOW_REFUSED
                    report.reason = f"unknown node type {node.node_type!r}"
                    break

                # HBSys gate for UI-automation nodes (same pre-check the
                # main GUI applies to Date Fill / XML Clicker).
                if spec.hbsys_touching and not self._hbsys_available():
                    self._record(
                        report,
                        node,
                        NODE_STATUS_SKIPPED,
                        "HBSys window not found — open HBSys first",
                    )
                    continue

                self._record(report, node, NODE_STATUS_RUNNING, "")
                adapter = ScriptNodeAdapter(
                    spec, settings, on_output=self.on_output
                )
                with self._adapter_lock:
                    self._current_adapter = adapter
                try:
                    status = adapter.run(live=live)
                finally:
                    with self._adapter_lock:
                        self._current_adapter = None

                if status == "DONE":
                    self._record(report, node, NODE_STATUS_DONE, "")
                    continue
                if status == "STOPPED":
                    self._record(report, node, NODE_STATUS_STOPPED, "stopped by user")
                    report.result = WORKFLOW_STOPPED
                    report.reason = "stopped by user"
                    # mark the remaining nodes skipped-by-stop
                    for rest in ordered[ordered.index(node) + 1:]:
                        self._record(
                            report, rest, NODE_STATUS_SKIPPED_STOPPED,
                            "workflow stopped",
                        )
                    break
                # FAILED
                reason = f"exit code non-zero ({spec.label})"
                self._record(report, node, NODE_STATUS_FAILED, reason)
                if bool(node.params.get("continue_on_fail", False)):
                    self.on_output(
                        f"[workflow] {spec.label} failed — continuing "
                        f"(continue_on_fail)"
                    )
                    continue
                report.result = WORKFLOW_FAILED
                report.reason = f"node failed: {spec.label}"
                for rest in ordered[ordered.index(node) + 1:]:
                    self._record(
                        report, rest, NODE_STATUS_SKIPPED,
                        f"skipped after failure: {spec.label}",
                    )
                break
        finally:
            self._running = False

        if report.result == WORKFLOW_COMPLETED:
            done = sum(
                1 for r in report.node_results if r.status == NODE_STATUS_DONE
            )
            report.reason = f"{done}/{len(ordered)} nodes completed"
        self.on_finished(report)
        return report

    # -- internals -----------------------------------------------------------

    def _record(
        self,
        report: WorkflowRunReport,
        node: NodeInstance,
        status: str,
        reason: str,
    ) -> None:
        """Append a node status to the report + notify the callback."""
        label = (
            node.spec().label if node.spec() is not None else node.node_type
        )
        report.node_results.append(
            NodeRunResult(
                node_id=node.id,
                node_type=node.node_type,
                label=label,
                status=status,
                reason=reason,
            )
        )
        try:
            self.on_node_status(node, status, reason)
        except Exception:
            pass  # GUI callbacks must never break the run

    def _hbsys_available(self) -> bool:
        """True when the HBSys window exists (same check as main GUI)."""
        try:
            from date_fill_hbsys.hbsys_window import find_hbsys_window

            return find_hbsys_window() is not None
        except Exception:
            return False


# -- standalone test (AGENTS.md: every module must support __main__) -------

if __name__ == "__main__":
    import tempfile
    import time

    failures = 0

    def _raises(fn) -> bool:
        try:
            fn()
        except WorkflowConfigError:
            return True
        return False

    def check(label: str, ok: bool) -> None:
        global failures
        if not ok:
            failures += 1
        print(f"{'OK  ' if ok else 'FAIL'} {label}")

    # -- default workflow ---------------------------------------------------
    default = default_workflow()
    check(
        "default: 8 nodes in current manual pipeline order",
        [n.node_type for n in default.nodes] == [
            "claims_processor", "date_fill_regular", "xml_clicker", "copy_xml",
            "fees_checker", "claims_checker", "add_claims_upload",
            "claim_attachments",
        ]
        and [n.order for n in default.nodes] == list(range(1, 9)),
    )
    check(
        "default: chained connections, all enabled",
        len(default.connections) == 7
        and all(c.enabled for c in default.connections)
        and default.connections[0].source_id == "n1"
        and default.connections[-1].target_id == "n8",
    )
    d2 = default_workflow()
    d2.nodes[0].enabled = False
    check(
        "default: factory returns fresh copies (no shared mutation)",
        default_workflow().nodes[0].enabled is True,
    )

    # -- persistence round-trip ----------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "workflow_config.json"
        save_workflow(default, cfg_path)
        loaded = load_workflow(cfg_path)
        check(
            "persistence: save/load round-trip identical",
            [asdict(n) for n in loaded.nodes]
            == [asdict(n) for n in default.nodes]
            and [asdict(c) for c in loaded.connections]
            == [asdict(c) for c in default.connections]
            and loaded.version == CONFIG_VERSION,
        )

        # corrupt / missing / schema failures — never guessed
        check(
            "persistence: missing file -> WorkflowConfigError",
            _raises(lambda: load_workflow(Path(tmp) / "nope.json")),
        )
        cfg_path.write_text("{ not json", encoding="utf-8")
        check(
            "persistence: corrupt JSON -> WorkflowConfigError",
            _raises(lambda: load_workflow(cfg_path)),
        )
        cfg_path.write_text(
            json.dumps({"version": 99, "nodes": [], "connections": []}),
            encoding="utf-8",
        )
        check(
            "persistence: wrong version -> WorkflowConfigError",
            _raises(lambda: load_workflow(cfg_path)),
        )
        bad_nodes = {
            "version": 1,
            "nodes": [{"id": "x", "node_type": "not_a_real_node"}],
            "connections": [],
        }
        cfg_path.write_text(json.dumps(bad_nodes), encoding="utf-8")
        check(
            "persistence: unknown node type -> WorkflowConfigError",
            _raises(lambda: load_workflow(cfg_path)),
        )
        bad_conn = {
            "version": 1,
            "nodes": [],
            "connections": [
                {"source_id": "ghost", "target_id": "n1", "enabled": True}
            ],
        }
        cfg_path.write_text(json.dumps(bad_conn), encoding="utf-8")
        check(
            "persistence: dangling connection -> WorkflowConfigError",
            _raises(lambda: load_workflow(cfg_path)),
        )
        dup = {
            "version": 1,
            "nodes": [
                {"id": "n1", "node_type": "copy_xml"},
                {"id": "n1", "node_type": "copy_xml"},
            ],
            "connections": [],
        }
        cfg_path.write_text(json.dumps(dup), encoding="utf-8")
        check(
            "persistence: duplicate ids -> WorkflowConfigError",
            _raises(lambda: load_workflow(cfg_path)),
        )

    # -- engine: registry-driven fake execution ------------------------------
    # The engine is exercised through REAL ScriptNodeAdapters on echo
    # scripts (registered temporarily), so ordering, stop, failure and
    # skip semantics are tested on the true execution path.
    from core.workflow_registry import NODE_REGISTRY, NodeSpec

    def make_echo_node(key: str, exit_code: int, sleep: float = 0.0) -> None:
        NODE_REGISTRY[key] = NodeSpec(
            key=key, label=f"Echo {key}", category="Test",
            module=f"workflow_{key}.py", args=(), live_args=(),
            supports_dry=True,
        )
        (Path(__file__).resolve().parent.parent / f"workflow_{key}.py").write_text(
            "import sys, time\n"
            f"print('run {key}')\n"
            f"time.sleep({sleep})\n"
            f"sys.exit({exit_code})\n",
            encoding="utf-8",
        )

    try:
        for key, code, sleep in (
            ("wf_ok1", 0, 0.0), ("wf_ok2", 0, 0.0), ("wf_fail", 3, 0.0),
            ("wf_slow", 0, 60.0), ("wf_after", 0, 0.0),
        ):
            make_echo_node(key, code, sleep)

        def build(nodes_spec):
            return WorkflowConfig(
                nodes=[
                    NodeInstance(id=nid, node_type=ntype, enabled=enb, order=i)
                    for i, (nid, ntype, enb) in enumerate(nodes_spec, start=1)
                ],
                connections=[],
            )

        # happy path: 2 nodes complete in order
        outputs: list[str] = []
        statuses: list[tuple[str, str]] = []

        def on_node_status(node, status, reason):
            statuses.append((node.id, status))

        engine = WorkflowEngine(
            on_output=outputs.append, on_node_status=on_node_status
        )
        report = engine.run(build([("a", "wf_ok1", True), ("b", "wf_ok2", True)]))
        check(
            "engine: 2 nodes complete in order -> COMPLETED",
            report.result == WORKFLOW_COMPLETED
            and [r.status for r in report.node_results]
            == [NODE_STATUS_RUNNING, NODE_STATUS_DONE,
                NODE_STATUS_RUNNING, NODE_STATUS_DONE]
            and [r.node_id for r in report.node_results if r.status == "DONE"]
            == ["a", "b"]
            and "run wf_ok1" in "\n".join(outputs),
        )
        check(
            "engine: summary reason reports 2/2",
            report.reason == "2/2 nodes completed",
        )

        # failure stops the workflow; remaining nodes skipped
        statuses.clear()
        engine2 = WorkflowEngine(
            on_output=lambda _l: None, on_node_status=on_node_status
        )
        report2 = engine2.run(
            build([("a", "wf_ok1", True), ("b", "wf_fail", True),
                   ("c", "wf_after", True)])
        )
        check(
            "engine: failed node -> FAILED + remaining skipped",
            report2.result == WORKFLOW_FAILED
            and [r.status for r in report2.node_results]
            == [NODE_STATUS_RUNNING, NODE_STATUS_DONE,
                NODE_STATUS_RUNNING, NODE_STATUS_FAILED,
                NODE_STATUS_SKIPPED]
            and "wf_after" not in "\n".join(outputs),
        )

        # continue_on_fail lets the chain continue
        cfg = build([("a", "wf_fail", True), ("b", "wf_after", True)])
        cfg.nodes[0].params["continue_on_fail"] = True
        engine3 = WorkflowEngine()
        report3 = engine3.run(cfg)
        check(
            "engine: continue_on_fail proceeds to next node",
            report3.result == WORKFLOW_COMPLETED
            and [r.status for r in report3.node_results]
            == [NODE_STATUS_RUNNING, NODE_STATUS_FAILED,
                NODE_STATUS_RUNNING, NODE_STATUS_DONE],
        )

        # disabled nodes never run
        cfg = build([("a", "wf_ok1", False), ("b", "wf_ok2", True)])
        engine4 = WorkflowEngine()
        report4 = engine4.run(cfg)
        check(
            "engine: disabled node skipped by ordering (not executed)",
            report4.result == WORKFLOW_COMPLETED
            and report4.node_results[0].node_type == "wf_ok2"
            and "wf_ok1" not in [r.node_type for r in report4.node_results],
        )

        # stop() terminates the current node; rest skipped-by-stop
        cfg = build([("a", "wf_slow", True), ("b", "wf_after", True)])

        def on_node_status_collect(node, status, reason):
            statuses.append((node.id, status))

        statuses.clear()
        engine5 = WorkflowEngine(
            on_output=lambda _l: None, on_node_status=on_node_status_collect
        )
        box: dict[str, WorkflowRunReport] = {}

        def runner() -> None:
            box["report"] = engine5.run(cfg)

        import threading as _threading

        thread = _threading.Thread(target=runner, daemon=True)
        thread.start()

        # wait until the slow node is RUNNING, then stop
        deadline = time.time() + 10
        while time.time() < deadline:
            if any(s == "RUNNING" for _id, s in statuses):
                break
            time.sleep(0.05)
        engine5.stop()
        thread.join(timeout=15)
        rep = box.get("report")
        check(
            "engine: stop() -> STOPPED + rest SKIPPED_STOPPED",
            rep is not None
            and rep.result == WORKFLOW_STOPPED
            and [r.status for r in rep.node_results]
            == [NODE_STATUS_RUNNING, NODE_STATUS_STOPPED,
                NODE_STATUS_SKIPPED_STOPPED],
        )
        check(
            "engine: running flag cleared after stop",
            engine5.running is False,
        )

        # single-run guard
        engine6 = WorkflowEngine()
        cfg = build([("a", "wf_slow", True), ("b", "wf_after", True)])
        t6 = _threading.Thread(
            target=lambda: box.setdefault("r6", engine6.run(cfg)), daemon=True
        )
        t6.start()
        deadline = time.time() + 10
        while time.time() < deadline:
            if engine6.running:
                break
            time.sleep(0.05)
        rep6 = engine6.run(cfg)  # second run while running -> REFUSED
        engine6.stop()
        t6.join(timeout=15)
        check(
            "engine: second run while running -> REFUSED",
            rep6.result == WORKFLOW_REFUSED
            and "already running" in rep6.reason,
        )

        # all-disabled / empty workflow refused
        engine7 = WorkflowEngine()
        rep7 = engine7.run(build([("a", "wf_ok1", False)]))
        check(
            "engine: no enabled nodes -> REFUSED",
            rep7.result == WORKFLOW_REFUSED
            and "no enabled nodes" in rep7.reason,
        )

        # unknown node type refused before running anything
        bad_cfg = build([("a", "not_a_node", True)])
        rep8 = WorkflowEngine().run(bad_cfg)
        check(
            "engine: unknown node type -> REFUSED (never guessed)",
            rep8.result == WORKFLOW_REFUSED
            and "unknown node type" in rep8.reason,
        )

        # HBSys gate: hbsys_touching fake node with no HBSys -> SKIPPED
        NODE_REGISTRY["wf_hb"] = NodeSpec(
            key="wf_hb", label="Fake HBSys", category="Test",
            module="workflow_wf_hb.py", args=(), live_args=(),
            hbsys_touching=True,
        )
        (Path(__file__).resolve().parent.parent / "workflow_wf_hb.py").write_text(
            "print('should never run')\n", encoding="utf-8"
        )
        rep9 = WorkflowEngine().run(build([("a", "wf_hb", True)]))
        check(
            "engine: HBSys gate skips UI node when HBSys closed",
            rep9.result == WORKFLOW_COMPLETED
            and rep9.node_results[-1].status == NODE_STATUS_SKIPPED
            and "HBSys window not found" in rep9.node_results[-1].reason
            and "should never run" not in "\n".join(outputs),
        )
    finally:
        for key in ("wf_ok1", "wf_ok2", "wf_fail", "wf_slow", "wf_after", "wf_hb"):
            NODE_REGISTRY.pop(key, None)
            (Path(__file__).resolve().parent.parent / f"workflow_{key}.py").unlink(
                missing_ok=True
            )

    print("RESULT:", "PASSED" if failures == 0 else f"{failures} FAILURE(S)")
    raise SystemExit(0 if failures == 0 else 1)
