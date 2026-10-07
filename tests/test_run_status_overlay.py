"""Tests for the mini robot run-status overlay (gui/run_status_overlay.py).

Instantiates the real widget with the window withdrawn so nothing flashes on
the operator's screen.

Run from the project root:

    python -m unittest tests.test_run_status_overlay
    python tests/test_run_status_overlay.py
"""

from __future__ import annotations

import sys
import tkinter as tk
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gui import run_status_overlay as overlay_module  # noqa: E402
from gui.run_status_overlay import RunStatusOverlay  # noqa: E402


class RunStatusOverlayTests(unittest.TestCase):
    """State machine, lamp pulse, robot eyes, position, lifecycle."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.root.destroy()
        except tk.TclError:
            pass

    def setUp(self) -> None:
        self.overlay = RunStatusOverlay(self.root)
        self.addCleanup(self.overlay.destroy)

    # -- state ---------------------------------------------------------------

    def test_starts_idle(self):
        self.assertFalse(self.overlay.is_running)
        self.assertEqual(self.overlay.status_label.cget("text"), "Idle")

    def test_set_running_shows_label(self):
        self.overlay.set_running("Script running")
        self.assertTrue(self.overlay.is_running)
        self.assertEqual(
            self.overlay.status_label.cget("text"), "Script running"
        )

    def test_blank_running_label_defaults_to_running(self):
        self.overlay.set_running("   ")
        self.assertEqual(self.overlay.status_label.cget("text"), "Running")

    def test_long_running_label_is_truncated(self):
        self.overlay.set_running("W" * 60)
        text = self.overlay.status_label.cget("text")
        self.assertEqual(len(text), overlay_module.MAX_STATUS_CHARS)

    def test_set_idle_restores_idle(self):
        self.overlay.set_running("Script running")
        self.overlay.set_idle()
        self.assertFalse(self.overlay.is_running)
        self.assertEqual(self.overlay.status_label.cget("text"), "Idle")

    def test_running_text_is_green_idle_is_gray(self):
        self.overlay.set_running("Script running")
        run_fg = str(self.overlay.status_label.cget("fg"))
        self.overlay.set_idle()
        idle_fg = str(self.overlay.status_label.cget("fg"))
        self.assertEqual(run_fg, overlay_module.COL_TEXT_RUN)
        self.assertEqual(idle_fg, overlay_module.COL_TEXT_IDLE)
        self.assertNotEqual(run_fg, idle_fg)

    # -- lamp pulse ----------------------------------------------------------

    def _lamp_fill(self) -> str:
        return str(
            self.overlay.robot_canvas.itemcget(self.overlay._lamp_id, "fill")
        )

    def test_tick_pulses_lamp_while_running(self):
        self.overlay.set_running("Script running")
        self.overlay.tick()
        first = self._lamp_fill()
        self.overlay.tick()
        second = self._lamp_fill()
        pulse = (overlay_module.COL_LAMP_RUN_A, overlay_module.COL_LAMP_RUN_B)
        self.assertIn(first, pulse)
        self.assertIn(second, pulse)
        self.assertNotEqual(first, second)

    def test_tick_keeps_lamp_gray_while_idle(self):
        self.overlay.set_idle()
        self.overlay.tick()
        self.assertEqual(self._lamp_fill(), overlay_module.COL_LAMP_IDLE)

    # -- robot eyes ----------------------------------------------------------

    def _eye_state(self, item_id) -> str:
        return str(self.overlay.robot_canvas.itemcget(item_id, "state"))

    def test_robot_is_asleep_when_idle(self):
        self.overlay.set_idle()
        self.assertEqual(self._eye_state(self.overlay._eye_l_open), "hidden")
        self.assertEqual(self._eye_state(self.overlay._eye_r_open), "hidden")
        self.assertEqual(self._eye_state(self.overlay._eye_l_closed), "normal")
        self.assertEqual(self._eye_state(self.overlay._eye_r_closed), "normal")

    def test_robot_is_awake_when_running(self):
        self.overlay.set_running("Script running")
        self.assertEqual(self._eye_state(self.overlay._eye_l_open), "normal")
        self.assertEqual(self._eye_state(self.overlay._eye_r_open), "normal")
        self.assertEqual(self._eye_state(self.overlay._eye_l_closed), "hidden")
        self.assertEqual(self._eye_state(self.overlay._eye_r_closed), "hidden")

    # -- position / lifecycle ------------------------------------------------

    def test_position_is_lower_left_above_taskbar(self):
        x, y = self.overlay._target_xy()
        self.assertEqual(x, RunStatusOverlay.MARGIN_X)
        expected_y = max(
            0,
            self.root.winfo_screenheight()
            - RunStatusOverlay.HEIGHT
            - RunStatusOverlay.TASKBAR_GAP,
        )
        self.assertEqual(y, expected_y)

    def test_hidden_while_owner_withdrawn(self):
        self.overlay.refresh_visibility()
        self.root.update_idletasks()
        self.assertFalse(self.overlay.window.winfo_ismapped())
        self.assertFalse(self.overlay._owner_visible())

    def test_destroy_is_safe_to_call_twice_and_state_changes_after(self):
        self.overlay.destroy()
        self.overlay.destroy()  # no exception
        self.overlay.set_idle()  # no exception once the window is gone
        self.overlay.set_running("Script running")
        self.overlay.tick()
        self.overlay.refresh_visibility()


if __name__ == "__main__":
    unittest.main()