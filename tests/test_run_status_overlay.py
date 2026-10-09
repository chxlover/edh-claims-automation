"""Tests for the robot run-status overlay (gui/run_status_overlay.py).

Instantiates the real widget with the window withdrawn so nothing
flashes on the operator's screen.

Run from the project root:

    python -m unittest tests.test_run_status_overlay
    python tests/test_run_status_overlay.py
"""

from __future__ import annotations

import sys
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gui import run_status_overlay as overlay_module  # noqa: E402
from gui.run_status_overlay import RunStatusOverlay  # noqa: E402


class OverlayPatientCountTests(unittest.TestCase):
    """The node's remaining-patient count (2026-10-09).

    The count has its OWN line under the status text: the operator asked for
    it to appear ALONGSIDE "Workflow running..." / "Script running...", not to
    replace it. So the status label must keep saying "Workflow running" while
    the count label reads "Date Fill 2 left".
    """

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

    def test_count_does_not_replace_the_status_line(self):
        # The reported bug: the count was shown INSTEAD of the running text.
        self.overlay.set_running("Workflow running")
        self.overlay.set_count("Date Fill 2 left")
        self.assertTrue(
            self.overlay.status_label.cget("text")
            .startswith("Workflow running")
        )
        self.assertEqual(self.overlay.count_label.cget("text"), "Date Fill 2 left")

    def test_count_line_survives_the_dot_animation(self):
        # The dots animate the STATUS line only; the count must stay steady.
        self.overlay.set_running("Workflow running")
        self.overlay.set_count("Final Bill 1 left")
        self.overlay._phase = 2
        self.overlay._blink_on = True
        self.overlay._apply_state()
        self.assertEqual(
            self.overlay.status_label.cget("text"), "Workflow running..."
        )
        self.assertEqual(
            self.overlay.count_label.cget("text"), "Final Bill 1 left"
        )

    def test_two_digit_counts_fit(self):
        self.overlay.set_running("Workflow running")
        self.overlay.set_count("Date Fill 10 left")
        self.assertEqual(
            self.overlay.count_label.cget("text"), "Date Fill 10 left"
        )

    def test_empty_count_hides_the_line(self):
        self.overlay.set_running("Workflow running")
        self.overlay.set_count("")
        self.assertEqual(self.overlay.count_label.cget("text"), "")
        self.assertEqual(self.overlay.count, "")

    def test_count_is_truncated_not_wrapped(self):
        self.overlay.set_count("D" * 80)
        self.assertEqual(
            len(self.overlay.count_label.cget("text")),
            overlay_module.MAX_STATUS_CHARS,
        )

    def test_set_count_accepts_none(self):
        self.overlay.set_running("Workflow running")
        self.overlay.set_count("Date Fill 2 left")
        self.overlay.set_count(None)
        self.assertEqual(self.overlay.count, "")

    def test_set_idle_clears_the_count(self):
        self.overlay.set_running("Workflow running")
        self.overlay.set_count("Date Fill 2 left")
        self.overlay.set_idle()
        self.assertEqual(self.overlay.count_label.cget("text"), "")
        self.assertEqual(self.overlay.status_label.cget("text"), "Idle")

    def test_truncation_limit_covers_the_counts(self):
        self.assertGreaterEqual(
            overlay_module.MAX_STATUS_CHARS, len("Final Bill 999 left") + 3
        )

    def test_window_has_room_for_the_count_row(self):
        # The overlay grew by one row; the extra height must be reserved so
        # the count line is never clipped.
        self.assertGreaterEqual(
            RunStatusOverlay.HEIGHT,
            52 + RunStatusOverlay.COUNT_ROW_HEIGHT,
        )


class RunStatusOverlayTests(unittest.TestCase):
    """State machine, lamp blink, shine/dots, eyes, drag, lifecycle."""

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
        self.assertTrue(
            self.overlay.status_label.cget("text").startswith("Script running")
        )

    def test_blank_running_label_defaults_to_running(self):
        self.overlay.set_running("   ")
        self.assertTrue(
            self.overlay.status_label.cget("text").startswith("Running")
        )

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
            self.overlay.lamp_canvas.itemcget(self.overlay._lamp_id, "fill")
        )

    def test_lamp_blinks_while_running(self):
        self.overlay.set_running("Script running")
        first = self._lamp_fill()
        self.overlay.tick()
        second = self._lamp_fill()
        self.overlay.tick()
        third = self._lamp_fill()
        blink = (overlay_module.COL_LAMP_RUN_A, overlay_module.COL_LAMP_RUN_B)
        self.assertEqual(first, overlay_module.COL_LAMP_RUN_A)
        self.assertIn(second, blink)
        self.assertNotEqual(first, second)
        self.assertEqual(third, first)

    def test_lamp_is_gray_while_idle(self):
        self.overlay.set_idle()
        self.overlay.tick()
        self.assertEqual(self._lamp_fill(), overlay_module.COL_LAMP_IDLE)

    # -- status text shine + on-process dots -------------------------

    def test_running_text_shines(self):
        self.overlay.set_running("Script running")
        first = str(self.overlay.status_label.cget("fg"))
        self.overlay.tick()
        second = str(self.overlay.status_label.cget("fg"))
        self.assertEqual(first, overlay_module.COL_TEXT_RUN)
        self.assertEqual(second, overlay_module.COL_TEXT_SHINE)

    def test_running_dots_cycle_one_two_three(self):
        self.overlay.set_running("Script running")
        seen = []
        for _ in range(3):
            self.overlay.tick()
            seen.append(self.overlay.status_label.cget("text"))
        self.assertEqual(
            seen,
            ["Script running..", "Script running...", "Script running."],
        )

    def test_idle_text_has_no_dots(self):
        self.overlay.set_running("Script running")
        self.overlay.tick()
        self.overlay.set_idle()
        self.assertEqual(self.overlay.status_label.cget("text"), "Idle")

    # -- robot eyes ----------------------------------------------------------

    def _eye_state(self, item_id: int) -> str:
        return str(self.overlay.robot_canvas.itemcget(item_id, "state"))

    def _eye_core_fill(self) -> str:
        halo_id, core_id = self.overlay._eye_ids[0]
        return str(self.overlay.robot_canvas.itemcget(core_id, "fill"))

    def test_eyes_exist_for_both_eyes(self):
        self.assertEqual(len(self.overlay._eye_ids), 2)

    def test_eyes_glow_while_running(self):
        self.overlay.set_running("Script running")
        for halo_id, core_id in self.overlay._eye_ids:
            self.assertEqual(self._eye_state(halo_id), "normal")
            self.assertEqual(self._eye_state(core_id), "normal")
        self.assertEqual(self._eye_core_fill(), overlay_module.COL_EYE_RUN)

    def test_eyes_hidden_while_idle(self):
        self.overlay.set_idle()
        for halo_id, core_id in self.overlay._eye_ids:
            self.assertEqual(self._eye_state(halo_id), "hidden")
            self.assertEqual(self._eye_state(core_id), "hidden")

    def test_tick_keeps_eyes_in_sync(self):
        self.overlay.set_running("Script running")
        self.overlay.tick()
        for _halo_id, core_id in self.overlay._eye_ids:
            self.assertEqual(self._eye_state(core_id), "normal")
        self.overlay.set_idle()
        self.overlay.tick()
        for _halo_id, core_id in self.overlay._eye_ids:
            self.assertEqual(self._eye_state(core_id), "hidden")

    # -- robot icon ----------------------------------------------------------

    def test_robot_icon_file_exists(self):
        self.assertTrue(overlay_module.ICON_PATH.is_file())

    def test_robot_icon_is_rendered(self):
        self.assertIsNotNone(self.overlay._icon_photo)
        self.assertTrue(self.overlay.robot_canvas.find_withtag("icon"))

    # -- dragging ------------------------------------------------------------

    def _press(self, x: int, y: int) -> None:
        self.overlay._on_press(SimpleNamespace(x_root=x, y_root=y))

    def _motion(self, x: int, y: int) -> None:
        self.overlay._on_motion(SimpleNamespace(x_root=x, y_root=y))

    def _release(self, x: int, y: int) -> None:
        self.overlay._on_release(SimpleNamespace(x_root=x, y_root=y))

    def _xy(self) -> tuple[int, int]:
        geometry = self.overlay.window.geometry()   # "288x52+X+Y"
        _, pos = geometry.split("+", 1)
        x, y = pos.split("+")
        return int(x), int(y)

    def test_drag_moves_window_and_position_sticks(self):
        start_x, start_y = self._xy()
        self._press(100, 100)
        self._motion(140, 100)   # 40 px to the right
        self._release(140, 100)
        self.assertTrue(self.overlay._user_moved)
        moved_x, moved_y = self._xy()
        self.assertEqual(moved_x - start_x, 40)
        self.assertEqual(moved_y, start_y)
        # the poll loop must not snap the overlay back to the corner
        self.overlay._position()
        self.assertEqual(self._xy(), (moved_x, moved_y))

    def test_small_jiggle_is_a_click_not_a_drag(self):
        self._press(100, 100)
        self._motion(102, 100)   # below DRAG_THRESHOLD
        with mock.patch.object(self.overlay.owner, "deiconify") as deiconify:
            self._release(102, 100)
        deiconify.assert_called_once()
        self.assertFalse(self.overlay._user_moved)

    def test_click_without_drag_focuses_owner(self):
        self._press(100, 100)
        with mock.patch.object(self.overlay.owner, "deiconify") as deiconify:
            self._release(100, 100)
        deiconify.assert_called_once()

    def test_drag_does_not_focus_owner(self):
        self._press(100, 100)
        self._motion(160, 100)
        with mock.patch.object(self.overlay.owner, "deiconify") as deiconify:
            self._release(160, 100)
        deiconify.assert_not_called()
        self.assertTrue(self.overlay._user_moved)

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
