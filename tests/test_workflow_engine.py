"""Unit tests for the EXISTING Workflow Engine (Phase 1 safety net).

Purpose: lock in current engine behaviour BEFORE any change (conditions,
new default order, checkpoints). Nothing here runs a real subprocess,
touches HBSys, or writes a production file — `ScriptNodeAdapter` is
patched module-locally so no child process is ever started, and
`_hbsys_available` is stubbed like `tests/test_workflow_final_bill_node.py`
does.

Run from the project root:
    python -m unittest tests.test_workflow_engine
"""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import workflow_engine as we  # noqa: E402
from core.workflow_engine import (  # noqa: E402
    NODE_STATUS_DONE,
    NODE_STATUS_FAILED,
    NODE_STATUS_RUNNING,
    NODE_STATUS_SKIPPED,
    NODE_STATUS_SKIPPED_STOPPED,
    NODE_STATUS_STOPPED,
    WORKFLOW_COMPLETED,
    WORKFLOW_FAILED,
    WORKFLOW_REFUSED,
    WORKFLOW_STOPPED,
    Connection,
    NodeInstance,
    WorkflowConfig,
    WorkflowConfigError,
    WorkflowEngine,
    default_workflow,
    load_workflow,
    save_workflow,
)
from core.workflow_registry import NODE_REGISTRY, get_node_spec  # noqa: E402


def _node(node_id: str, node_type: str, order: int, **kwargs) -> NodeInstance:
    return NodeInstance(id=node_id, node_type=node_type, order=order, **kwargs)


class _RecordingAdapterFactory:
    """Stand-in for ScriptNodeAdapter: records calls, returns canned status."""

    def __init__(self, statuses: dict | None = None, default: str = "DONE"):
        self.statuses = statuses or {}
        self.default = default
        self.calls: list[str] = []
        self.specs: list[str] = []

    def install(self):
        factory = self

        class FakeAdapter:
            def __init__(self, spec, settings, on_output=None):
                factory.specs.append(spec.key)
                self.spec = spec

            def run(self, *, live: bool = True) -> str:
                factory.calls.append(self.spec.key)
                status = factory.statuses.get(self.spec.key, factory.default)
                if callable(status):
                    return status()
                return status

            def stop(self) -> None:
                factory.calls.append("STOP:" + self.spec.key)

        return mock.patch.object(we, "ScriptNodeAdapter", FakeAdapter)


def _config(*nodes, connections=()) -> WorkflowConfig:
    return WorkflowConfig(nodes=list(nodes), connections=list(connections))


class DefaultWorkflowTests(unittest.TestCase):
    """The shipped default workflow (current order)."""

    def test_default_order_is_the_operator_pipeline(self):
        config = default_workflow()
        self.assertEqual(
            [n.node_type for n in config.nodes],
            [
                "fees_checker", "date_fill_regular", "final_bill",
                "fees_checker", "xml_clicker", "copy_xml",
                "claims_checker", "add_claims_upload", "claim_attachments",
            ],
        )
        self.assertEqual([n.order for n in config.nodes], list(range(1, 10)))

    def test_default_excludes_claims_processor_but_copies_xml(self):
        # Operator direction 2026-10-12: the production Claim Processor is
        # run manually (signatures/dates are checked by hand). Operator rule
        # 2026-10-13: copy_xml IS in the default, right after xml_clicker —
        # the background auto-copy service pauses while a workflow runs, so
        # the workflow must copy the generated XMLs itself. Both remain in
        # the registry and can still be added by hand.
        keys = [n.node_type for n in default_workflow().nodes]
        self.assertNotIn("claims_processor", keys)
        self.assertIsNotNone(get_node_spec("claims_processor"))
        self.assertIsNotNone(get_node_spec("copy_xml"))
        self.assertLess(keys.index("xml_clicker"), keys.index("copy_xml"))
        self.assertLess(keys.index("copy_xml"), keys.index("claims_checker"))

    def test_default_runs_fees_twice_as_the_gate(self):
        # fees_checker first pass routes the patient; second pass re-checks
        # after Date Fill + Final Bill before XML generation.
        keys = [n.node_type for n in default_workflow().nodes]
        self.assertEqual(keys.count("fees_checker"), 2)
        self.assertLess(
            keys.index("fees_checker"), keys.index("date_fill_regular"))
        self.assertLess(
            keys.index("final_bill"), keys.index("xml_clicker"))
        second = [n.node_type for n in default_workflow().nodes][3]
        self.assertEqual(second, "fees_checker")

    def test_default_node_ids_are_unique_with_two_fees_nodes(self):
        ids = [n.id for n in default_workflow().nodes]
        self.assertEqual(len(ids), len(set(ids)), ids)

    def test_default_nodes_are_enabled_and_chained(self):
        config = default_workflow()
        self.assertTrue(all(n.enabled for n in config.nodes))
        self.assertEqual(len(config.connections), len(config.nodes) - 1)
        self.assertEqual(config.connections[0].source_id, config.nodes[0].id)
        self.assertEqual(
            config.connections[-1].target_id, config.nodes[-1].id)

    def test_default_is_a_fresh_copy_each_call(self):
        # Mutating one instance must never affect the factory default
        # (Restore Default depends on this).
        first = default_workflow()
        first.nodes[0].enabled = False
        first.nodes.append(_node("extra", "fees_checker", 99))
        second = default_workflow()
        self.assertTrue(second.nodes[0].enabled)
        self.assertEqual(len(second.nodes), 9)

    def test_date_fill_nodes_continue_on_fail(self):
        params = {
            n.node_type: n.params
            for n in default_workflow().nodes
            if n.node_type.startswith("date_fill")
        }
        self.assertTrue(params)
        for node_params in params.values():
            self.assertTrue(node_params.get("continue_on_fail"))


class ExecutionOrderTests(unittest.TestCase):
    """Sequential-by-order execution and enable/disable."""

    def _three_nodes(self):
        return _config(
            _node("a", "fees_checker", 3),
            _node("b", "date_fill_regular", 1),
            _node("c", "copy_xml", 2),
        )

    def test_runs_in_order_not_list_order(self):
        engine = WorkflowEngine()
        fake = _RecordingAdapterFactory()
        with fake.install(), mock.patch.object(
            WorkflowEngine, "_hbsys_available", lambda self: True
        ):
            report = engine.run(self._three_nodes(), live=False)
        self.assertEqual(fake.calls, ["date_fill_regular", "copy_xml", "fees_checker"])
        self.assertEqual(report.result, WORKFLOW_COMPLETED)

    def test_disabled_nodes_are_never_executed(self):
        config = _config(
            _node("a", "fees_checker", 1),
            _node("b", "date_fill_regular", 2, enabled=False),
            _node("c", "copy_xml", 3),
        )
        engine = WorkflowEngine()
        fake = _RecordingAdapterFactory()
        with fake.install(), mock.patch.object(
            WorkflowEngine, "_hbsys_available", lambda self: True
        ):
            report = engine.run(config, live=False)
        self.assertNotIn("date_fill_regular", fake.calls)
        statuses = {r.node_id: r.status for r in report.node_results}
        self.assertNotIn("b", statuses)  # disabled -> not even a result row
        self.assertEqual(report.result, WORKFLOW_COMPLETED)

    def test_node_status_callback_receives_running_then_done(self):
        seen: list[tuple[str, str]] = []
        engine = WorkflowEngine(
            on_node_status=lambda node, status, reason: seen.append(
                (node.id, status))
        )
        with _RecordingAdapterFactory().install(), mock.patch.object(
            WorkflowEngine, "_hbsys_available", lambda self: True
        ):
            engine.run(_config(_node("a", "copy_xml", 1)), live=False)
        self.assertEqual(
            seen, [("a", NODE_STATUS_RUNNING), ("a", NODE_STATUS_DONE)])


class FailurePolicyTests(unittest.TestCase):
    """A FAILED node stops the chain unless continue_on_fail is set."""

    def _two(self, continue_on_fail: bool):
        # NOTE: continue_on_fail is read from the node that FAILED
        # (workflow_engine reads node.params of the failing node), so it
        # must be set on "a" (the failing copy_xml), not on the next one.
        return _config(
            _node("a", "copy_xml", 1,
                  params={"continue_on_fail": continue_on_fail}),
            _node("b", "fees_checker", 2),
        )

    def test_failure_stops_remaining_nodes(self):
        engine = WorkflowEngine()
        fake = _RecordingAdapterFactory(
            statuses={"copy_xml": "FAILED"}, default="DONE")
        with fake.install(), mock.patch.object(
            WorkflowEngine, "_hbsys_available", lambda self: True
        ):
            report = engine.run(self._two(False), live=False)
        self.assertEqual(fake.calls, ["copy_xml"])
        self.assertEqual(report.result, WORKFLOW_FAILED)
        statuses = {r.node_id: r.status for r in report.node_results}
        self.assertEqual(statuses["a"], NODE_STATUS_FAILED)
        self.assertEqual(statuses["b"], NODE_STATUS_SKIPPED)

    def test_continue_on_fail_proceeds(self):
        engine = WorkflowEngine()
        fake = _RecordingAdapterFactory(
            statuses={"copy_xml": "FAILED"}, default="DONE")
        with fake.install(), mock.patch.object(
            WorkflowEngine, "_hbsys_available", lambda self: True
        ):
            report = engine.run(self._two(True), live=False)
        self.assertEqual(fake.calls, ["copy_xml", "fees_checker"])
        self.assertEqual(report.result, WORKFLOW_COMPLETED)
        statuses = {r.node_id: r.status for r in report.node_results}
        self.assertEqual(statuses["a"], NODE_STATUS_FAILED)
        self.assertEqual(statuses["b"], NODE_STATUS_DONE)

    def test_on_output_reports_the_continue_on_fail_reason(self):
        lines: list[str] = []
        engine = WorkflowEngine(on_output=lines.append)
        fake = _RecordingAdapterFactory(
            statuses={"copy_xml": "FAILED"}, default="DONE")
        with fake.install(), mock.patch.object(
            WorkflowEngine, "_hbsys_available", lambda self: True
        ):
            engine.run(self._two(True), live=False)
        self.assertTrue(any("continue_on_fail" in line for line in lines), lines)


class RefusalTests(unittest.TestCase):
    """REFUSED cases: never guessed, never partially executed."""

    def test_unknown_node_type_is_refused_before_execution(self):
        engine = WorkflowEngine()
        fake = _RecordingAdapterFactory()
        config = _config(
            NodeInstance(id="x", node_type="not_a_real_node", order=1))
        with fake.install():
            report = engine.run(config, live=False)
        self.assertEqual(report.result, WORKFLOW_REFUSED)
        self.assertEqual(fake.calls, [])

    def test_duplicate_node_ids_are_refused(self):
        engine = WorkflowEngine()
        config = _config(
            _node("same", "copy_xml", 1), _node("same", "fees_checker", 2))
        with _RecordingAdapterFactory().install():
            report = engine.run(config, live=False)
        self.assertEqual(report.result, WORKFLOW_REFUSED)

    def test_workflow_with_no_enabled_nodes_is_refused(self):
        engine = WorkflowEngine()
        config = _config(_node("a", "copy_xml", 1, enabled=False))
        with _RecordingAdapterFactory().install():
            report = engine.run(config, live=False)
        self.assertEqual(report.result, WORKFLOW_REFUSED)
        self.assertIn("no enabled nodes", report.reason)

    def test_second_concurrent_run_is_refused(self):
        engine = WorkflowEngine()
        release = threading.Event()
        entered = threading.Event()

        def blocking():
            entered.set()
            release.wait(10)
            return "DONE"

        config = _config(_node("a", "copy_xml", 1))
        fake = _RecordingAdapterFactory(statuses={"copy_xml": blocking})

        def worker():
            with fake.install(), mock.patch.object(
                WorkflowEngine, "_hbsys_available", lambda self: True
            ):
                engine.run(config, live=False)

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(entered.wait(10), "adapter never ran")
        try:
            second = engine.run(config, live=False)
        finally:
            release.set()
            thread.join(10)
        self.assertEqual(second.result, WORKFLOW_REFUSED)
        self.assertIn("already running", second.reason)
        self.assertFalse(engine.running)

    def test_on_fired_fires_for_refusals_too(self):
        seen = []
        engine = WorkflowEngine(on_finished=seen.append)
        with _RecordingAdapterFactory().install():
            engine.run(_config(_node("a", "copy_xml", 1, enabled=False)),
                       live=False)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].result, WORKFLOW_REFUSED)


class HbsysGateTests(unittest.TestCase):
    """hbsys_touching nodes are skipped when HBSys is not open."""

    def test_hbsys_node_skipped_when_hbsys_missing(self):
        engine = WorkflowEngine()
        fake = _RecordingAdapterFactory()
        config = _config(
            _node("a", "date_fill_regular", 1),
            _node("b", "fees_checker", 2),
        )
        with fake.install(), mock.patch.object(
            WorkflowEngine, "_hbsys_available", lambda self: False
        ):
            report = engine.run(config, live=False)
        self.assertEqual(fake.calls, ["fees_checker"])
        statuses = {r.node_id: r.status for r in report.node_results}
        self.assertEqual(statuses["a"], NODE_STATUS_SKIPPED)
        reason = {r.node_id: r.reason for r in report.node_results}["a"]
        self.assertIn("HBSys window not found", reason)

    def test_non_hbsys_node_runs_even_when_hbsys_missing(self):
        self.assertFalse(get_node_spec("fees_checker").hbsys_touching)
        self.assertTrue(get_node_spec("date_fill_regular").hbsys_touching)
        self.assertTrue(get_node_spec("final_bill").hbsys_touching)


class StopTests(unittest.TestCase):
    """Stop terminates the current node and skips the rest."""

    def test_stop_marks_remaining_nodes_skipped_stopped(self):
        engine = WorkflowEngine()
        fake = _RecordingAdapterFactory()
        config = _config(
            _node("a", "copy_xml", 1),
            _node("b", "fees_checker", 2),
            _node("c", "claims_checker", 3),
        )

        def stopping_first():
            engine.stop()
            return "STOPPED"

        fake.statuses["copy_xml"] = stopping_first
        with fake.install(), mock.patch.object(
            WorkflowEngine, "_hbsys_available", lambda self: True
        ):
            report = engine.run(config, live=False)
        self.assertEqual(report.result, WORKFLOW_STOPPED)
        statuses = {r.node_id: r.status for r in report.node_results}
        self.assertEqual(statuses["a"], NODE_STATUS_STOPPED)
        self.assertEqual(statuses["b"], NODE_STATUS_SKIPPED_STOPPED)
        self.assertEqual(statuses["c"], NODE_STATUS_SKIPPED_STOPPED)
        self.assertNotIn("fees_checker", fake.calls)


class PersistenceTests(unittest.TestCase):
    """Save/load/validate round-trip and refusal cases."""

    def test_save_then_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wf.json"
            original = default_workflow()
            save_workflow(original, path)
            loaded = load_workflow(path)
            self.assertEqual(
                [n.node_type for n in loaded.nodes],
                [n.node_type for n in original.nodes],
            )
            self.assertEqual(len(loaded.connections), len(original.connections))
            self.assertEqual(loaded.version, original.version)

    def test_node_params_and_positions_survive_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wf.json"
            config = _config(
                NodeInstance(id="a", node_type="date_fill_regular", order=1,
                             position={"x": 10, "y": 20},
                             params={"continue_on_fail": True}),
            )
            save_workflow(config, path)
            loaded = load_workflow(path)
            self.assertEqual(loaded.nodes[0].position, {"x": 10, "y": 20})
            self.assertTrue(loaded.nodes[0].params["continue_on_fail"])

    def test_load_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(WorkflowConfigError):
                load_workflow(Path(tmp) / "nope.json")

    def test_load_corrupt_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wf.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(WorkflowConfigError):
                load_workflow(path)

    def test_load_wrong_version_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wf.json"
            path.write_text(
                '{"version": 99, "nodes": [], "connections": []}',
                encoding="utf-8",
            )
            with self.assertRaises(WorkflowConfigError):
                load_workflow(path)

    def test_load_unknown_node_type_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wf.json"
            path.write_text(
                '{"version": 1, "nodes": [{"id": "a", '
                '"node_type": "nope"}], "connections": []}',
                encoding="utf-8",
            )
            with self.assertRaises(WorkflowConfigError):
                load_workflow(path)

    def test_load_duplicate_node_ids_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wf.json"
            path.write_text(
                '{"version": 1, "nodes": [{"id": "a", "node_type": "copy_xml"},'
                ' {"id": "a", "node_type": "fees_checker"}], '
                '"connections": []}',
                encoding="utf-8",
            )
            with self.assertRaises(WorkflowConfigError):
                load_workflow(path)

    def test_dangling_connection_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wf.json"
            path.write_text(
                '{"version": 1, "nodes": [{"id": "a", "node_type": "copy_xml"}],'
                ' "connections": [{"source_id": "a", "target_id": "ghost"}]}',
                encoding="utf-8",
            )
            with self.assertRaises(WorkflowConfigError):
                load_workflow(path)

    def test_save_validates_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wf.json"
            bad = _config(NodeInstance(id="a", node_type="nope", order=1))
            with self.assertRaises(WorkflowConfigError):
                save_workflow(bad, path)
            self.assertFalse(path.exists())

    def test_connection_to_same_node_is_allowed_by_schema(self):
        # Documented current behaviour: validate_config only checks ids and
        # node types, not self-loops. Locked so any future change is visible.
        we.validate_config(
            _config(_node("a", "copy_xml", 1),
                    connections=[Connection("a", "a")]))


class RegistryTests(unittest.TestCase):
    """Registry resolution used by the engine and the list editor."""

    def test_every_node_type_resolves(self):
        for key in NODE_REGISTRY:
            self.assertIsNotNone(get_node_spec(key), key)

    def test_unknown_key_returns_none(self):
        self.assertIsNone(get_node_spec("definitely_not_a_node"))

    def test_duplicate_node_instances_are_schema_valid(self):
        # Required for a fees(1)/fees(2) workflow: same node_type twice,
        # different ids.
        config = _config(
            _node("f1", "fees_checker", 1),
            _node("f2", "fees_checker", 2),
            connections=[Connection("f1", "f2")],
        )
        we.validate_config(config)  # must not raise
        self.assertEqual(len(config.nodes), 2)


if __name__ == "__main__":
    unittest.main()