"""GUI tests for the per-node run-condition control (Phase 4, 2026-10-12).

Instantiates the real WorkflowFrame with the window withdrawn and drives
the `when` combo directly. NEVER runs a workflow, spawns a subprocess,
or touches HBSys — only the editor's display/save of `params["when"]`.

Run from the project root:
    python -m unittest tests.test_gui_workflow_tab_conditions
"""

from __future__ import annotations

import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.workflow_conditions import (  # noqa: E402
    CONDITION_ALWAYS,
    CONDITION_CHECKER_READY,
    CONDITION_FEES_ALLOWS_XML,
    CONDITION_LABELS,
)
from core.workflow_engine import (  # noqa: E402
    load_workflow,
    save_workflow,
    default_workflow,
)
from gui.workflow_tab import WorkflowFrame  # noqa: E402


class WorkflowTabConditionTests(unittest.TestCase):
    def setUp(self):
        self.tk_root = tk.Tk()
        self.tk_root.withdraw()
        self.addCleanup(self.tk_root.destroy)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = Path(self.tmp.name) / "workflow_config.json"
        # Start from the shipped default so the test mirrors a real load.
        save_workflow(default_workflow(), self.config_path)
        self.frame = WorkflowFrame(
            self.tk_root,
            settings_getter=lambda: {},
            log_callback=lambda _line: None,
            config_path=self.config_path,
        )
        self.frame.pack()

    # -- helpers ----------------------------------------------------------

    def _select(self, node_type: str, occurrence: int = 0):
        ordered = sorted(self.frame.config.nodes, key=lambda n: n.order)
        matches = [n for n in ordered if n.node_type == node_type]
        node = matches[occurrence]
        self.frame._selected_node = node.id
        self.frame._refresh_order_list()
        return node

    def _rows(self) -> list[str]:
        return [
            self.frame.order_list.get(i)
            for i in range(self.frame.order_list.size())
        ]

    # -- tests ------------------------------------------------------------

    def test_combo_is_disabled_until_a_node_is_selected(self):
        self.frame._selected_node = None
        self.frame._refresh_selection_info()
        self.assertEqual(
            str(self.frame.when_combo.cget("state")), "disabled")

    def test_combo_enables_and_shows_the_nodes_condition(self):
        node = self._select("xml_clicker")
        self.assertEqual(str(self.frame.when_combo.cget("state")), "readonly")
        self.assertEqual(node.params.get("when"), CONDITION_FEES_ALLOWS_XML)
        self.assertEqual(
            self.frame.when_var.get(), CONDITION_LABELS[CONDITION_FEES_ALLOWS_XML])

    def test_selecting_a_condition_updates_the_node(self):
        node = self._select("claims_checker")
        self.assertEqual(node.params.get("when"), CONDITION_ALWAYS)
        self.frame.when_var.set(CONDITION_LABELS[CONDITION_CHECKER_READY])
        self.frame._on_when_selected()
        self.assertEqual(node.params.get("when"), CONDITION_CHECKER_READY)

    def test_choosing_always_writes_the_explicit_id(self):
        node = self._select("add_claims_upload")
        self.frame.when_var.set(CONDITION_LABELS[CONDITION_ALWAYS])
        self.frame._on_when_selected()
        self.assertEqual(node.params.get("when"), CONDITION_ALWAYS)

    def test_label_to_id_mapping_round_trips(self):
        for condition_id, label in CONDITION_LABELS.items():
            self.assertEqual(
                self.frame._condition_id_from_label(label), condition_id)

    def test_unknown_label_falls_back_to_always(self):
        self.assertEqual(
            self.frame._condition_id_from_label("garbage"), CONDITION_ALWAYS)

    def test_no_selection_does_not_write_to_any_node(self):
        node = self._select("xml_clicker")
        before = dict(node.params)
        self.frame._selected_node = None
        self.frame.when_var.set(CONDITION_LABELS[CONDITION_CHECKER_READY])
        self.frame._on_when_selected()
        self.assertEqual(node.params, before)

    def test_list_rows_show_a_gate_marker_only_when_conditional(self):
        rows = self._rows()
        joined = "\n".join(rows)
        self.assertIn(f"[{CONDITION_FEES_ALLOWS_XML}]", joined)
        # Unconditional nodes must not carry a marker.
        fees_row = next(r for r in rows if "Fees Check" in r)
        self.assertNotIn("[", fees_row)
        self.assertNotIn("disabled", fees_row)

    def test_disabled_node_shows_both_markers(self):
        node = self._select("xml_clicker")
        node.enabled = False
        self.frame._refresh_order_list()
        row = next(r for r in self._rows() if "XML" in r)
        self.assertIn("[disabled]", row)
        self.assertIn(f"[{CONDITION_FEES_ALLOWS_XML}]", row)

    def test_new_node_defaults_to_always(self):
        before = len(self.frame.config.nodes)
        self.frame.palette_var.set(
            f"{__import__('core.workflow_registry', fromlist=['x']).NODE_REGISTRY['fees_checker'].label}"
            f"  (Checks)"
        )
        self.frame.add_node()
        self.assertEqual(len(self.frame.config.nodes), before + 1)
        added = sorted(self.frame.config.nodes, key=lambda n: n.order)[-1]
        self.assertEqual(added.params.get("when"), CONDITION_ALWAYS)

    def test_condition_survives_save_and_reload(self):
        node = self._select("final_bill")
        self.frame.when_var.set(CONDITION_LABELS[CONDITION_FEES_ALLOWS_XML])
        self.frame._on_when_selected()
        save_workflow(self.frame.config, self.config_path)
        reloaded = load_workflow(self.config_path)
        matched = [n for n in reloaded.nodes if n.id == node.id]
        self.assertEqual(len(matched), 1)
        self.assertEqual(
            matched[0].params.get("when"), CONDITION_FEES_ALLOWS_XML)

    def test_deleted_node_also_loses_its_condition(self):
        node = self._select("xml_clicker")
        self.frame._selected_node = node.id
        self.frame.order_list.selection_clear(0, "end")
        # select the row so delete uses the list selection
        ordered = sorted(self.frame.config.nodes, key=lambda n: n.order)
        index = [n.id for n in ordered].index(node.id)
        self.frame.order_list.selection_set(index)
        self.frame.delete_node()
        self.assertNotIn(node.id, [n.id for n in self.frame.config.nodes])


if __name__ == "__main__":
    unittest.main()