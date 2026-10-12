"""GUI tests for the Claims Agent tab.

Instantiates the real ClaimsAgentFrame (window withdrawn) with a fake
agent factory. NEVER touches HBSys — the factory returns canned
summaries/records; messagebox calls are stubbed.

Run from the project root:
    python -m unittest tests.test_gui_claims_agent
"""

from __future__ import annotations

import sys
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent.pipeline_state import ClaimRecord, PipelineStore  # noqa: E402
from gui.claims_agent_tab import ClaimsAgentFrame  # noqa: E402


def _summary(batch_id="B1", **counts):
    data = {"batch_id": batch_id, "total_claims": 0, "completed": 0,
            "incomplete": 0, "held_for_review": 0, "failed": 0,
            "pending": 0}
    data.update(counts)
    summary = SimpleNamespace(batch_id=batch_id)
    summary.as_dict = lambda: data
    return summary


class ClaimsAgentTabTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "scans").mkdir()
        (self.root / "output").mkdir()
        self.settings = {"scan_folder": str(self.root / "scans"),
                         "output_folder": str(self.root / "output")}
        self.logs: list[str] = []
        self.tk_root = tk.Tk()
        self.tk_root.withdraw()
        self.addCleanup(self.tk_root.destroy)
        self.addCleanup(self.temporary.cleanup)

    def _frame(self, agent_factory=None, background=False):
        return ClaimsAgentFrame(
            self.tk_root,
            settings_getter=lambda: self.settings,
            log_callback=self.logs.append,
            agent_factory=agent_factory,
            background=background,
        )

    def test_start_runs_batch_and_shows_summary(self):
        seen = {}

        def factory(config):
            seen["live"] = config.live_hbsys
            seen["retries"] = config.max_fee_cycle_retries

            class FakeAgent:
                def run_batch(self, batch_id):
                    seen["batch"] = batch_id or "B1"
                    return _summary(seen["batch"], total_claims=2, completed=2)
            return FakeAgent()

        frame = self._frame(agent_factory=factory)
        frame.batch_var.set("B9")
        with mock.patch("gui.claims_agent_tab.messagebox.askyesno",
                        return_value=True):
            frame.on_start()
        self.tk_root.update()  # flush after() callbacks
        self.assertIn("B9", frame.summary_var.get())
        self.assertIn("completed=2", frame.summary_var.get())
        self.assertFalse(frame.is_running)
        self.assertEqual(seen["batch"], "B9")
        self.assertFalse(seen["live"])  # safe default: live OFF

    def test_decline_confirmation_runs_nothing(self):
        started = []
        frame = self._frame(agent_factory=lambda c: started.append(c))
        with mock.patch("gui.claims_agent_tab.messagebox.askyesno",
                        return_value=False):
            frame.on_start()
        self.assertEqual(started, [])
        self.assertFalse(frame.is_running)

    def test_refresh_reads_store_without_running(self):
        db = self.root / "pipeline.db"
        store = PipelineStore(db)
        store.open_batch("B7")
        rec = ClaimRecord(batch_id="B7", claim_key="K1", outcome="COMPLETED",
                          last_completed_stage="COMPLETED",
                          hold_reason="", manual_action="")
        store.upsert_claim(rec)
        rec2 = ClaimRecord(batch_id="B7", claim_key="K2", outcome="FAILED",
                           last_completed_stage="DATE_FILL",
                           hold_reason="no encounter",
                           manual_action="check HBSys")
        store.upsert_claim(rec2)
        store.close()

        real_load = __import__("core.agent.pipeline_config",
                               fromlist=["load_config"]).load_config

        def settings():
            return dict(self.settings)

        frame = self._frame()
        frame.batch_var.set("B7")
        # Point the frame's store at our db by overriding state_db.
        orig_build = frame._build_config

        def build():
            cfg = orig_build()
            cfg.state_db = db
            return cfg
        frame._build_config = build
        frame.refresh_table()
        rows = frame.tree.get_children()
        self.assertEqual(len(rows), 2)
        values = [frame.tree.item(r)["values"] for r in rows]
        outcomes = sorted(v[2] for v in values)
        self.assertEqual(outcomes, ["COMPLETED", "FAILED"])
        failed = [v for v in values if v[2] == "FAILED"][0]
        self.assertIn("check HBSys", failed[3])

    def test_config_problem_reported_not_run(self):
        frame = self._frame(agent_factory=lambda c: (_ for _ in ()).throw(
            AssertionError("must not run")))
        frame.batch_var.set("B1")
        bad = dict(self.settings, scan_folder=str(self.root / "nope"))
        frame.settings_getter = lambda: bad
        with mock.patch("gui.claims_agent_tab.messagebox.askyesno",
                        return_value=True):
            frame.on_start()
        self.tk_root.update()
        self.assertIn("Config invalid", frame.summary_var.get())
        self.assertFalse(frame.is_running)

    def test_double_start_refused(self):
        frame = self._frame()
        frame._running = True
        with mock.patch("gui.claims_agent_tab.messagebox.showinfo") as info:
            frame.on_start()
        info.assert_called_once()

    def test_background_thread_delivers_logs_and_finish(self):
        # Regression test for the reported bug: the worker thread called
        # after() directly, so no log and no finish ever reached the GUI
        # (overlay stuck on "running"). Now the worker only enqueues.
        def factory(config):
            class FakeAgent:
                def run_batch(self, batch_id):
                    frame_ref._thread_log("[agent][B][K][STAGE] hello")
                    return _summary("BG", total_claims=1, completed=1)
            return FakeAgent()

        frame_ref = None
        frame = self._frame(agent_factory=factory, background=True)
        frame_ref = frame
        with mock.patch("gui.claims_agent_tab.messagebox.askyesno",
                        return_value=True):
            frame.on_start()
        frame._run_thread.join(timeout=15)
        frame._drain_queue()
        self.tk_root.update()
        try:
            self.assertFalse(frame.is_running)
            self.assertIn("completed=1", frame.summary_var.get())
            box = frame.log_text.get("1.0", "end")
            self.assertIn("batch starting", box)
            self.assertIn("hello", box)
            self.assertTrue(any("hello" in line for line in self.logs))
        finally:
            if frame._poll_job is not None:
                try:
                    frame.after_cancel(frame._poll_job)
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()
