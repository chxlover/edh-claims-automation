"""Tests for the Final Bill coordinate getter (gui/final_bill_coordinate_getter.py).

Headless: INJECTED position/key/anchor lookups at temp click-map files - these
tests never touch HBSys or the real keyboard/mouse. The window itself runs as
a withdrawn Tk; F8/ESC are scripted through key_fn.

Run from the project root:

    python -m unittest tests.test_gui_final_bill_coordinate_getter
    python tests/test_gui_final_bill_coordinate_getter.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import final_bill_click_map as click_map  # noqa: E402
from gui import final_bill_coordinate_getter as getter  # noqa: E402


def no_dialog(marker):
    """Fake live-dialog lookup: walang HBSys sa tests."""
    return None


class ScriptedKeys:
    """key_fn fake: bawat tawag ay nagbabalik ng halaga sa pagkakasunod."""

    def __init__(self, *sequence):
        self.sequence = list(sequence)
        self.calls = 0

    def __call__(self, vk):
        self.calls += 1
        if self.sequence:
            return self.sequence.pop(0)
        return False


class FormatXyTests(unittest.TestCase):
    def test_pair_becomes_comma_text(self):
        self.assertEqual(getter.format_xy(1010, 630), "1010,630")

    def test_values_are_truncated_to_ints(self):
        self.assertEqual(getter.format_xy(1010.0, 630.9), "1010,630")


class StatusTests(unittest.TestCase):
    def test_missing_point_reads_wala_pa(self):
        self.assertIn("WALA PA", getter.status_for(None))

    def test_saved_point_shows_its_summary(self):
        point = click_map.ClickPoint(
            name="save_ok", x=950, y=700, dx=90, dy=60,
            anchor_rect=(860, 640, 1120, 700),
        )
        self.assertEqual(getter.status_for(point), point.summary())

    def test_mapped_count_only_counts_saved_targets(self):
        click_map_obj = click_map.ClickMap()
        click_map_obj.set(click_map.ClickPoint(name="save_ok", x=1, y=2))
        self.assertEqual(getter.mapped_count(click_map_obj), 1)
        self.assertEqual(getter.mapped_count(click_map.ClickMap()), 0)

class GetterWindowTests(unittest.TestCase):
    """Withdrawn-Tk: capture (F8/countdown), save, at refresh - walang HBSys."""

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.path = Path(self.temporary.name) / "click_map.json"

    def tearDown(self):
        self.temporary.cleanup()

    def make_window(
        self,
        position=(1010, 630),
        key_fn=None,
        anchor=no_dialog,
        countdown=0.0,
    ):
        window = getter.FinalBillCoordinateGetter(
            map_path=self.path,
            position_fn=lambda: position,
            key_fn=key_fn if key_fn is not None else ScriptedKeys(),
            anchor_rect_fn=anchor,
            countdown=countdown,
        )
        window.withdraw()
        self.addCleanup(window.destroy)
        window.update_idletasks()
        return window

    def test_default_target_is_the_first_one(self):
        window = self.make_window()
        self.assertEqual(window.target_box.current(), 0)
        self.assertEqual(
            window._selected_target().name, click_map.TARGETS[0].name
        )

    def test_capture_fills_the_x_and_y_fields(self):
        window = self.make_window(position=(1010, 630))

        window.capture()

        self.assertEqual(window.entry_x.get(), "1010")
        self.assertEqual(window.entry_y.get(), "630")
        self.assertIn("1010,630", window.message_var.get())

    def test_negative_cursor_position_is_reported_not_filled(self):
        window = self.make_window(position=(-5, 630))

        window.capture()

        self.assertEqual(
            (window.entry_x.get(), window.entry_y.get()), ("", "")
        )
        self.assertIn("Hindi magamit", window.message_var.get())

    def test_armed_f8_records_then_esc_disarms(self):
        keys = ScriptedKeys(False, True)  # poll 1: ESC walang, F8 meron
        window = self.make_window(position=(720, 560), key_fn=keys)

        window.toggle_arm()
        self.assertTrue(window.armed)
        window._poll_key()

        # Naitala ang posisyon at nananatiling armed para sa susunod na F8.
        self.assertEqual(
            (window.entry_x.get(), window.entry_y.get()), ("720", "560")
        )
        self.assertTrue(window.armed)
        self.assertIn("F8", window.message_var.get())

        keys.sequence = [True, False]  # poll 2: ESC
        window._poll_key()

        self.assertFalse(window.armed)
        self.assertIn("ESC", window.message_var.get())
        self.assertIn("Hindi naka-arm", window.arm_var.get())

    def test_countdown_ticks_then_captures(self):
        window = self.make_window(position=(550, 450), countdown=2.0)

        window.start_countdown()
        self.assertTrue(window._counting)
        self.assertIn("2s", window.message_var.get())
        window._countdown_tick()
        self.assertIn("1s", window.message_var.get())

        window._countdown_tick()  # left hits 0 -> capture

        self.assertEqual(window.entry_x.get(), "550")
        self.assertEqual(window.entry_y.get(), "450")
        self.assertFalse(window._counting)

    def test_countdown_is_blocked_while_armed(self):
        window = self.make_window(key_fn=ScriptedKeys(False, False))

        window.toggle_arm()
        window.start_countdown()

        self.assertFalse(window._counting)
        self.assertIn("Naka-arm", window.message_var.get())

    def test_save_writes_the_selected_target_anchored(self):
        def live_rect(marker):
            return (1000, 600, 1300, 700) if marker == "Print Options" else None

        window = self.make_window(position=(1010, 630), anchor=live_rect)
        window.capture()

        with mock.patch.object(
            getter.messagebox, "showinfo"
        ) as info, mock.patch.object(getter.messagebox, "showerror") as error:
            window.save()

        info.assert_not_called()
        error.assert_not_called()
        point = click_map.ClickMap.load(self.path).get("final_checkbox")
        self.assertEqual((point.x, point.y), (1010, 630))
        # Live dialog rect -> anchored sa popup (dx/dy), hindi lang absolute.
        self.assertEqual((point.dx, point.dy), (10, 30))
        self.assertEqual(point.anchor_rect, (1000, 600, 1300, 700))
        self.assertIn("NA-SAVE", window.message_var.get())
        self.assertIn("1/4", window.count_var.get())

    def test_save_without_dialog_is_absolute(self):
        window = self.make_window(position=(700, 560), anchor=no_dialog)
        window.capture()

        with mock.patch.object(getter.messagebox, "showerror") as error:
            window.save()

        error.assert_not_called()
        point = click_map.ClickMap.load(self.path).get("final_checkbox")
        self.assertEqual((point.x, point.y), (700, 560))
        self.assertFalse(point.anchored)

    def test_invalid_typed_value_blocks_save_and_writes_nothing(self):
        window = self.make_window()
        window.entry_x.insert(0, "abc")
        window.entry_y.insert(0, "630")

        with mock.patch.object(getter.messagebox, "showerror") as error:
            window.save()

        error.assert_called_once()
        self.assertIn("final_checkbox", error.call_args.args[1])
        self.assertFalse(self.path.exists())

    def test_blank_fields_do_not_write(self):
        window = self.make_window()

        with mock.patch.object(
            getter.messagebox, "showinfo"
        ) as info, mock.patch.object(getter.messagebox, "showerror") as error:
            window.save()

        info.assert_not_called()
        error.assert_not_called()
        self.assertIn("Walang laman", window.message_var.get())
        self.assertFalse(self.path.exists())

    def test_refresh_shows_saved_status_for_the_selected_target(self):
        click_map_obj = click_map.ClickMap()
        click_map_obj.set(
            click_map.ClickPoint(name="admin_no", x=700, y=560)
        )
        click_map_obj.save(self.path)
        window = self.make_window()

        window.target_box.current(3)  # admin_no
        window.refresh()

        self.assertIn("700", window.status_var.get())
        self.assertIn("1/4", window.count_var.get())

    def test_refresh_of_an_unsaved_target_reads_wala_pa(self):
        window = self.make_window()

        window.refresh()

        self.assertIn("WALA PA", window.status_var.get())
        self.assertIn("0/4", window.count_var.get())


if __name__ == "__main__":
    unittest.main()