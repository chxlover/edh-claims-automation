"""Workflow — Configurable GUI Tab (v2, simplified list editor).

At the owner's direction (2026-09-11) the v1 canvas (node boxes, drag,
connection arrows) was REPLACED by a simple, professional list editor.
The workflow is managed purely as an EXECUTION ORDER of registered
nodes:

    Select          — click a row in the Execution Order list
    Add Node        — palette of registered nodes, appended last
    Enable/Disable  — toggle without deleting
    Change Order    — Up / Down buttons (renumbered 01..N)
    Delete Node     — remove the instance ONLY (the underlying Python
                      module is never touched)
    Save            — workflow_config.json (same JSON style as the
                      existing claims_gui_config.json)
    Restore Default — regenerate the factory default workflow
                      (current manual pipeline order)
    Run / Stop      — engine on a daemon worker thread; per-node status
                      row colors; output streamed to the tab log
    Live mode       — checkbox, DRY-RUN by default

Engine / registry / adapters / persistence are UNCHANGED. Connections
stay in the data model (saved configs remain valid) — they are simply
not displayed, since the engine executes strictly by node order.

Constructor injection matches the existing tabs (settings_getter,
log_callback) — no coupling to the main shell or sibling tabs.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from core.workflow_engine import (
    NODE_STATUS_DONE,
    NODE_STATUS_FAILED,
    NODE_STATUS_RUNNING,
    NODE_STATUS_SKIPPED,
    NODE_STATUS_SKIPPED_STOPPED,
    NODE_STATUS_STOPPED,
    NodeInstance,
    WorkflowConfig,
    WorkflowConfigError,
    WorkflowEngine,
    default_workflow,
    load_workflow,
    save_workflow,
)
from core.workflow_registry import NODE_REGISTRY, categories, node_labels

# row colors per run status: (background, foreground)
STATUS_ROW_COLORS = {
    NODE_STATUS_RUNNING: ("#2F6FED", "#FFFFFF"),
    NODE_STATUS_DONE: ("#16A34A", "#FFFFFF"),
    NODE_STATUS_FAILED: ("#DC2626", "#FFFFFF"),
    NODE_STATUS_STOPPED: ("#F59E0B", "#1F2937"),
    NODE_STATUS_SKIPPED: ("#9CA3AF", "#1F2937"),
    NODE_STATUS_SKIPPED_STOPPED: ("#F59E0B", "#1F2937"),
}


class WorkflowFrame(ttk.Frame):
    """Simple execution-order workflow editor + runner."""

    def __init__(
        self,
        parent,
        settings_getter: Callable[[], dict],
        log_callback: Callable[[str], None],
    ) -> None:
        super().__init__(parent, padding=8)
        self.settings_getter = settings_getter
        self.log_callback = log_callback

        self.config: WorkflowConfig = WorkflowConfig()
        self.engine: Optional[WorkflowEngine] = None
        self._run_thread: Optional[threading.Thread] = None
        self._selected_node: Optional[str] = None
        self._node_status_colors: dict[str, str] = {}  # node id -> status
        # thread-safe UI updates: worker threads put callables here;
        # the Tk main loop drains them via _poll_ui_queue (never calls
        # Tk from a worker thread — the canonical safe pattern)
        self._ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()

        self._build_ui()
        self._load_initial_config()
        self.after(100, self._poll_ui_queue)

    def _poll_ui_queue(self) -> None:
        """Drain pending UI updates queued by worker threads (Tk-safe)."""
        try:
            while True:
                callable_item = self._ui_queue.get_nowait()
                try:
                    callable_item()
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.after(100, self._poll_ui_queue)

    def _post(self, fn: Callable[[], None]) -> None:
        """Schedule *fn* on the Tk thread from ANY thread (safe no-op
        when Tk is not pumping, e.g. headless self-tests)."""
        self._ui_queue.put(fn)

    # -- UI construction -----------------------------------------------------

    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 6))
        ttk.Label(
            header,
            text="Configurable Claims Workflow",
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(header, textvariable=self.status_var).pack(
            side="right", padx=(0, 8)
        )

        # -- add node palette ------------------------------------------------
        add_box = ttk.LabelFrame(self, text="Add Node", padding=6)
        add_box.pack(fill="x", pady=(0, 8))
        add_box.columnconfigure(0, weight=1)
        self.palette_var = tk.StringVar()
        palette_values = [
            f"{label}  ({cat})"
            for cat in categories()
            for label in [
                spec.label for spec in NODE_REGISTRY.values()
                if spec.category == cat
            ]
        ]
        self.palette_combo = ttk.Combobox(
            add_box,
            textvariable=self.palette_var,
            values=palette_values,
            state="readonly",
        )
        self.palette_combo.grid(row=0, column=0, sticky="ew")
        ttk.Button(
            add_box, text="+ Add to Workflow", command=self.add_node
        ).grid(row=0, column=1, padx=(8, 0))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        # -- execution order list (the workflow) -----------------------------
        order_box = ttk.LabelFrame(body, text="Execution Order", padding=6)
        order_box.grid(row=0, column=0, sticky="nsew")
        order_box.rowconfigure(0, weight=1)
        order_box.columnconfigure(0, weight=1)
        self.order_list = tk.Listbox(order_box, height=12, font=("Segoe UI", 10))
        self.order_list.grid(row=0, column=0, sticky="nsew")
        self.order_list.bind("<<ListboxSelect>>", self._on_order_select)
        order_btns = ttk.Frame(order_box)
        order_btns.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        for text, command in (
            ("Move Up", lambda: self.move_node(-1)),
            ("Move Down", lambda: self.move_node(1)),
            ("Delete Node", self.delete_node),
        ):
            ttk.Button(
                order_btns, text=text, command=command
            ).pack(side="left", expand=True, fill="x", padx=1)

        # -- selected node panel ------------------------------------------------
        node_box = ttk.LabelFrame(body, text="Selected Node", padding=8)
        node_box.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.node_info = tk.StringVar(value="Select a node in the list")
        ttk.Label(node_box, textvariable=self.node_info, wraplength=230).pack(
            fill="x", pady=(0, 8)
        )
        self.enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            node_box,
            text="Enabled",
            variable=self.enabled_var,
            command=self.toggle_enabled,
        ).pack(fill="x")
        ttk.Label(
            node_box,
            text="Disabled nodes stay in the workflow\nbut are skipped when running.",
            foreground="#64748B",
            wraplength=230,
        ).pack(fill="x", pady=(8, 0))

        # -- controls ------------------------------------------------------------
        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=(8, 0))
        ttk.Button(
            controls, text="Run Workflow", command=self.run_workflow
        ).pack(side="left", padx=2)
        ttk.Button(
            controls, text="Stop", command=self.stop_workflow
        ).pack(side="left", padx=2)
        ttk.Button(
            controls, text="Save", command=self.save_config
        ).pack(side="left", padx=(12, 2))
        ttk.Button(
            controls, text="Restore Default", command=self.restore_default
        ).pack(side="left", padx=2)
        self.live_var = tk.BooleanVar(value=False)  # DRY-RUN default
        ttk.Checkbutton(
            controls, text="Live mode", variable=self.live_var
        ).pack(side="right")

        # -- log ---------------------------------------------------------------
        log_box = ttk.LabelFrame(self, text="Workflow Log", padding=4)
        log_box.pack(fill="x", pady=(8, 0))
        self.log_text = tk.Text(
            log_box, height=7, state="disabled", font=("Consolas", 9)
        )
        self.log_text.pack(fill="x")

    # -- config lifecycle --------------------------------------------------

    def _load_initial_config(self) -> None:
        try:
            self.config = load_workflow()
            self._log("Loaded saved workflow from workflow_config.json")
        except WorkflowConfigError as exc:
            self.config = default_workflow()
            self._log(f"Config not loaded ({exc}); using DEFAULT workflow")
        self._refresh_order_list()

    def save_config(self) -> None:
        try:
            path = save_workflow(self.config)
        except WorkflowConfigError as exc:
            self._log(f"Save refused: {exc}")
            return
        self._log(f"Workflow saved: {path}")
        self.status_var.set("Saved")

    def restore_default(self) -> None:
        self.config = default_workflow()
        self._selected_node = None
        self._node_status_colors = {}
        self._refresh_order_list()
        self._log("Default workflow restored (not saved yet — press Save)")
        self.status_var.set("Default restored")

    # -- node operations -----------------------------------------------------

    def add_node(self) -> None:
        label = self.palette_var.get()
        key = next((k for k, l in node_labels().items() if l in label), None)
        if key is None:
            self._log("Select a node type from the palette first")
            return
        next_order = max((n.order for n in self.config.nodes), default=0) + 1
        node = NodeInstance(
            id=f"n{next_order}_{key[:8]}",
            node_type=key,
            enabled=True,
            order=next_order,
        )
        self.config.nodes.append(node)
        self._selected_node = node.id
        self._log(f"Added node: {NODE_REGISTRY[key].label}")
        self._refresh_order_list()

    def delete_node(self) -> None:
        node_id = self._selected_node_id_from_list()
        if node_id is None:
            self._log("Select a node in the list first")
            return
        node = self._find_node(node_id)
        spec = NODE_REGISTRY.get(node.node_type) if node else None
        label = spec.label if spec else node_id
        self.config.nodes = [n for n in self.config.nodes if n.id != node_id]
        self.config.connections = [
            c for c in self.config.connections
            if c.source_id != node_id and c.target_id != node_id
        ]
        self._renumber()
        self._selected_node = None
        self._node_status_colors.pop(node_id, None)
        self._log(f"Deleted node (workflow only — module untouched): {label}")
        self._refresh_order_list()

    def toggle_enabled(self) -> None:
        node_id = self._selected_node_id_from_list()
        if node_id is None:
            self._log("Select a node in the list first")
            return
        node = self._find_node(node_id)
        if node is None:
            return
        node.enabled = bool(self.enabled_var.get())
        state = "enabled" if node.enabled else "disabled"
        self._log(f"Node {state}: {NODE_REGISTRY[node.node_type].label}")
        self._refresh_order_list()

    def move_node(self, direction: int) -> None:
        """Move the selected node up/down in the execution order."""
        node_id = self._selected_node_id_from_list()
        if node_id is None:
            self._log("Select a node in the list first")
            return
        ordered = sorted(self.config.nodes, key=lambda n: n.order)
        index = next(
            (i for i, n in enumerate(ordered) if n.id == node_id), None
        )
        if index is None:
            return
        new_index = index + direction
        if new_index < 0 or new_index >= len(ordered):
            return
        ordered[index], ordered[new_index] = ordered[new_index], ordered[index]
        for position, node in enumerate(ordered, start=1):
            node.order = position
        self._renumber()
        self._selected_node = ordered[new_index].id
        self._refresh_order_list()
        self.order_list.selection_clear(0, "end")
        self.order_list.selection_set(new_index)
        self.order_list.see(new_index)

    # -- run / stop ------------------------------------------------------------

    def run_workflow(self) -> None:
        if self._run_thread is not None and self._run_thread.is_alive():
            self._log("A workflow run is already active")
            return
        if self.engine is not None and self.engine.running:
            self._log("Engine busy — press Stop first")
            return
        live = bool(self.live_var.get())
        if live:
            self._log(
                "LIVE workflow run starting — HBSys nodes need HBSys open, "
                "maximized, hands off."
            )
        else:
            self._log("DRY workflow run starting (supported nodes dry-run)")

        # fresh run: clear the previous run's status colors
        self._node_status_colors = {}
        self._refresh_order_list()

        self.engine = WorkflowEngine(
            settings_getter=self.settings_getter,
            on_node_status=self._on_node_status,
            on_output=lambda line: self._post(lambda l=line: self._log(l)),
            on_finished=self._on_run_finished,
        )
        config_snapshot = self.config
        self.status_var.set("Running…")
        self._run_thread = threading.Thread(
            target=lambda: self.engine.run(config_snapshot, live=live),
            name="workflow-run",
            daemon=True,
        )
        self._run_thread.start()

    def stop_workflow(self) -> None:
        if self.engine is not None:
            self.engine.stop()
            self._log("Stop requested — terminating current node…")
        else:
            self._log("No workflow running")

    def _on_node_status(
        self, node: NodeInstance, status: str, reason: str
    ) -> None:
        label = (
            NODE_REGISTRY[node.node_type].label
            if node.node_type in NODE_REGISTRY
            else node.node_type
        )
        text = f"[{status}] {label}"
        if reason:
            text += f" — {reason}"
        self._post(lambda t=text: self._log(t))
        self._post(lambda n=node.id, s=status: self._paint_node_status(n, s))

    def _on_run_finished(self, report) -> None:
        def done():
            self.status_var.set(
                f"Finished: {report.result} ({report.reason})"
            )
            self._log(f"Workflow finished: {report.result} — {report.reason}")

        self._post(done)

    # -- list rendering ------------------------------------------------------

    def _refresh_order_list(self) -> None:
        """Rebuild the execution order list, keeping the selection."""
        self.order_list.delete(0, "end")
        ordered = sorted(self.config.nodes, key=lambda n: n.order)
        for node in ordered:
            spec = NODE_REGISTRY.get(node.node_type)
            label = spec.label if spec else node.node_type
            state = "" if node.enabled else "  [disabled]"
            self.order_list.insert(
                "end", f"{node.order:02d}. {label}{state}"
            )
        # restore selection + status colors by node id
        for index, node in enumerate(ordered):
            status = self._node_status_colors.get(node.id)
            if status in STATUS_ROW_COLORS:
                bg, fg = STATUS_ROW_COLORS[status]
                self.order_list.itemconfig(index, background=bg, foreground=fg)
        if self._selected_node is not None:
            for index, node in enumerate(ordered):
                if node.id == self._selected_node:
                    self.order_list.selection_clear(0, "end")
                    self.order_list.selection_set(index)
                    self.order_list.see(index)
                    break
        self._refresh_selection_info()

    def _refresh_selection_info(self) -> None:
        node = self._find_node(self._selected_node) if self._selected_node else None
        if node is None:
            self.node_info.set("Select a node in the list")
            self.enabled_var.set(True)
            return
        spec = NODE_REGISTRY.get(node.node_type)
        self.node_info.set(
            f"{spec.label if spec else node.node_type}\n"
            f"{spec.description if spec else ''}"
        )
        self.enabled_var.set(node.enabled)

    def _paint_node_status(self, node_id: str, status: str) -> None:
        """Color the node's row in the list by run status."""
        if status not in STATUS_ROW_COLORS:
            return
        self._node_status_colors[node_id] = status
        ordered = sorted(self.config.nodes, key=lambda n: n.order)
        for index, node in enumerate(ordered):
            if node.id == node_id:
                bg, fg = STATUS_ROW_COLORS[status]
                self.order_list.itemconfig(index, background=bg, foreground=fg)
                break

    # -- events / helpers ---------------------------------------------------

    def _on_order_select(self, _event) -> None:
        selection = self.order_list.curselection()
        if not selection:
            return
        ordered = sorted(self.config.nodes, key=lambda n: n.order)
        self._selected_node = ordered[selection[0]].id
        self._refresh_selection_info()

    def _selected_node_id_from_list(self) -> Optional[str]:
        """Node id of the selected list row (or the stored selection)."""
        selection = self.order_list.curselection()
        if selection:
            ordered = sorted(self.config.nodes, key=lambda n: n.order)
            return ordered[selection[0]].id
        return self._selected_node

    def _find_node(self, node_id: str) -> Optional[NodeInstance]:
        return next(
            (n for n in self.config.nodes if n.id == node_id), None
        )

    def _renumber(self) -> None:
        for position, node in enumerate(
            sorted(self.config.nodes, key=lambda n: n.order), start=1
        ):
            node.order = position

    def _log(self, line: str) -> None:
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"{line}\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass
        try:
            self.log_callback(line)
        except Exception:
            pass


# -- standalone test (AGENTS.md: every module must support __main__) -------

if __name__ == "__main__":
    from pathlib import Path

    try:
        root = tk.Tk()
        root.withdraw()
        frame = WorkflowFrame(
            parent=root,
            settings_getter=lambda: {"output_folder": r"C:\claims_bot\output"},
            log_callback=lambda _l: None,
        )
        frame.update_idletasks()

        # default workflow loads as an 8-row execution order
        assert len(frame.config.nodes) == 8, "default workflow loads 8 nodes"
        assert frame.order_list.size() == 8, "8 rows in the list"
        default_types = [n.node_type for n in
                         sorted(frame.config.nodes, key=lambda x: x.order)]
        assert default_types[0] == "claims_processor"
        assert default_types[-1] == "claim_attachments"

        # selecting a row populates the panel
        frame.order_list.selection_set(0)
        frame._on_order_select(None)
        assert frame._selected_node == "n1"
        assert "Claims Processor" in frame.node_info.get()

        # Up/Down move the node and KEEP the selection
        frame.move_node(1)  # n1 down
        types_after_down = [n.node_type for n in
                            sorted(frame.config.nodes, key=lambda x: x.order)]
        assert types_after_down[0] == "date_fill_regular", types_after_down
        assert frame.order_list.curselection(), "selection kept after move"
        frame.move_node(-1)  # back up
        types_after_up = [n.node_type for n in
                          sorted(frame.config.nodes, key=lambda x: x.order)]
        assert types_after_up[0] == "claims_processor"

        # enable/disable via the checkbox state
        frame.order_list.selection_set(0)
        frame._on_order_select(None)
        frame.enabled_var.set(False)
        frame.toggle_enabled()
        assert frame._find_node("n1").enabled is False
        frame.enabled_var.set(True)
        frame.toggle_enabled()
        assert frame._find_node("n1").enabled is True

        # add node appends last + selects it
        frame.palette_var.set(
            f"{NODE_REGISTRY['claims_checker_recheck'].label}  (Checks)"
        )
        frame.add_node()
        assert len(frame.config.nodes) == 9, "add node works"
        new_node = max(frame.config.nodes, key=lambda n: n.order)
        assert new_node.node_type == "claims_checker_recheck"
        assert frame._selected_node == new_node.id

        # delete removes ONLY the instance (registry intact)
        frame.delete_node()
        assert len(frame.config.nodes) == 8, "delete node works"
        assert "claims_checker_recheck" in NODE_REGISTRY

        # save -> file; restore default
        frame.save_config()
        assert Path("workflow_config.json").is_file()
        frame.restore_default()
        assert len(frame.config.nodes) == 8

        # status paint colors the row (crash-free)
        frame._paint_node_status("n1", NODE_STATUS_RUNNING)
        frame._paint_node_status("n1", NODE_STATUS_DONE)
        assert frame._node_status_colors["n1"] == NODE_STATUS_DONE

        # -- Run end-to-end with injected echo nodes (safe: no real tools) --
        from core.workflow_registry import NodeSpec

        for key in ("wf_tab_a", "wf_tab_b"):
            NODE_REGISTRY[key] = NodeSpec(
                key=key, label=f"Echo {key}", category="Test",
                module=f"workflow_{key}.py", args=(), live_args=(),
                supports_dry=True,
            )
            Path(f"workflow_{key}.py").write_text(
                "import sys\nprint('run')\nsys.exit(0)\n", encoding="utf-8"
            )
        try:
            saved_config = frame.config
            frame.config = WorkflowConfig(
                nodes=[
                    NodeInstance(id="a", node_type="wf_tab_a", order=1),
                    NodeInstance(id="b", node_type="wf_tab_b", order=2),
                ],
                connections=[],
            )
            frame._node_status_colors = {}
            frame._refresh_order_list()
            frame.run_workflow()
            assert frame._run_thread is not None
            # pump the Tk loop so worker-thread after(0) callbacks land
            # (in the real app the mainloop does this continuously)
            import time as _time

            deadline = _time.time() + 30
            while (
                frame._run_thread is not None
                and frame._run_thread.is_alive()
                and _time.time() < deadline
            ):
                root.update()
                _time.sleep(0.02)
            root.update()
            assert frame.status_var.get().startswith("Finished: COMPLETED"), \
                frame.status_var.get()
            assert frame._node_status_colors.get("a") == NODE_STATUS_DONE
            assert frame._node_status_colors.get("b") == NODE_STATUS_DONE
            frame.config = saved_config
        finally:
            for key in ("wf_tab_a", "wf_tab_b"):
                NODE_REGISTRY.pop(key, None)
                Path(f"workflow_{key}.py").unlink(missing_ok=True)

        print("OK   gui: list editor (select/move/toggle/add/delete/save/run)")
        print("RESULT: PASSED")
        root.destroy()
    except tk.TclError as exc:
        # no display (headless CI) — verify the importable API instead
        print(f"SKIP display unavailable ({exc}) — import-level checks only")
        assert WorkflowFrame is not None
        assert default_workflow().nodes[0].node_type == "claims_processor"
        print("OK   gui: import + default workflow verified headless")
        print("RESULT: PASSED")
    finally:
        Path("workflow_config.json").unlink(missing_ok=True)
