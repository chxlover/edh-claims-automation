"""Tests for the Final Bill coordinate editor (gui/final_bill_map_editor.py).

The save helpers run headless: temp click-map files and INJECTED anchor
lookups - these tests never touch HBSys. One withdrawn-Tk smoke test covers
the window itself.

Run from the project root:

    python -m unittest tests.test_gui_final_bill_map_editor
    python tests/test_gui_final_bill_map_editor.py
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
from gui import final_bill_map_editor as editor  # noqa: E402


def no_dialog(marker):
    """Fake live-dialog lookup: walang HBSys sa tests."""
    return None


def seed_map(path, **points) -> Path:
    """Write a click map with the given ClickPoint field dicts."""
    click_map_obj = click_map.ClickMap()
    for name, fields in points.items():
        target = click_map.TARGETS_BY_NAME[name]
        click_map_obj.set(
            click_map.ClickPoint(
                name=name, label=target.label, anchor=target.anchor, **fields
            )
        )
    click_map_obj.save(path)
    return path


class ParseXyTests(unittest.TestCase):
    def test_plain_x_comma_y(self):
        self.assertEqual(editor.parse_xy("1010,630"), (1010, 630))

    def test_space_separators_are_accepted(self):
        self.assertEqual(editor.parse_xy("1010 630"), (1010, 630))
        self.assertEqual(editor.parse_xy(" 1010 , 630 "), (1010, 630))

    def test_blank_is_none(self):
        self.assertIsNone(editor.parse_xy(""))
        self.assertIsNone(editor.parse_xy("   "))
        self.assertIsNone(editor.parse_xy(None))

    def test_missing_y_is_rejected(self):
        with self.assertRaises(ValueError):
            editor.parse_xy("1010,")

    def test_non_number_is_rejected(self):
        with self.assertRaises(ValueError):
            editor.parse_xy("abc,630")

    def test_negative_is_rejected(self):
        with self.assertRaises(ValueError):
            editor.parse_xy("-10,630")

    def test_too_many_parts_are_rejected(self):
        with self.assertRaises(ValueError):
            editor.parse_xy("1010,630,999")


class RowTextTests(unittest.TestCase):
    def test_both_blank_means_skip_the_row(self):
        self.assertEqual(editor.row_text("", "  "), "")

    def test_both_filled_becomes_one_pair(self):
        self.assertEqual(editor.row_text("1010", "630"), "1010,630")

    def test_only_x_keeps_the_comma_so_validation_rejects_it(self):
        self.assertEqual(editor.row_text("1010", ""), "1010,")


class SaveCoordinatesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.path = Path(self.temporary.name) / "click_map.json"

    def tearDown(self):
        self.temporary.cleanup()

    def raw(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def test_blank_rows_keep_stored_points_and_new_rows_are_written(self):
        seed_map(
            self.path,
            save_ok=dict(x=950, y=700, dx=90, dy=60,
                         anchor_rect=(860, 640, 1120, 700)),
        )
        saved = editor.save_coordinates(
            {"save_ok": "", "admin_no": "700,560"},
            path=self.path,
            anchor_rect_fn=no_dialog,
        )

        self.assertEqual(set(saved), {"admin_no"})
        click_map_obj = click_map.ClickMap.load(self.path)
        kept = click_map_obj.get("save_ok")
        self.assertEqual((kept.x, kept.y, kept.dx, kept.dy),
                         (950, 700, 90, 60))
        fresh = click_map_obj.get("admin_no")
        self.assertEqual((fresh.x, fresh.y), (700, 560))
        # Walang kilalang rect -> absolute (pareho ng --set name=x,y).
        self.assertFalse(fresh.anchored)
        self.assertEqual(
            fresh.label, click_map.TARGETS_BY_NAME["admin_no"].label
        )

    def test_one_invalid_row_blocks_the_whole_save(self):
        # Walang partial save: kahit isa lang ang mali, WALA talagang isinulat.
        seed_map(self.path, save_ok=dict(x=950, y=700))
        before = self.raw()

        with self.assertRaises(ValueError) as ctx:
            editor.save_coordinates(
                {"save_ok": "10,20", "admin_no": "abc,10"},
                path=self.path,
                anchor_rect_fn=no_dialog,
            )

        self.assertIn("admin_no", str(ctx.exception))
        self.assertEqual(self.raw(), before)

    def test_edit_keeps_following_the_popup_via_the_old_anchor_rect(self):
        # Ang bagong X,Y ay binabase pa rin sa kilalang anchor rect, kaya
        # sinusundan ng click ang popup gaya ng bago ang edit.
        seed_map(
            self.path,
            print_options_ok=dict(x=400, y=300, dx=160, dy=120,
                                  anchor_rect=(240, 180, 700, 520)),
        )
        saved = editor.save_coordinates(
            {"print_options_ok": "300,260"},
            path=self.path,
            anchor_rect_fn=no_dialog,
        )

        point = saved["print_options_ok"]
        self.assertEqual((point.dx, point.dy), (60, 80))
        self.assertEqual(point.anchor_rect, (240, 180, 700, 520))
        self.assertEqual(point.point_for((240, 180, 700, 520)), (300, 260))

    def test_live_dialog_rect_wins_over_the_old_anchor(self):
        # Kapag BUKAS ang dialog habang nagse-save, doon base ang dx/dy.
        seed_map(
            self.path,
            save_ok=dict(x=950, y=700, dx=90, dy=60,
                         anchor_rect=(860, 640, 1120, 700)),
        )

        def live_rect(marker):
            return (500, 400, 800, 600) if marker == "File save" else None

        saved = editor.save_coordinates(
            {"save_ok": "550,450"}, path=self.path, anchor_rect_fn=live_rect
        )

        point = saved["save_ok"]
        self.assertEqual(point.anchor_rect, (500, 400, 800, 600))
        self.assertEqual((point.dx, point.dy), (50, 50))
        self.assertEqual(point.point_for((500, 400, 800, 600)), (550, 450))

    def test_nothing_typed_leaves_the_file_untouched(self):
        seed_map(self.path, save_ok=dict(x=950, y=700))
        before = self.raw()

        saved = editor.save_coordinates(
            {"save_ok": "", "admin_no": ""},
            path=self.path,
            anchor_rect_fn=no_dialog,
        )

        self.assertEqual(saved, {})
        self.assertEqual(self.raw(), before)


class DeletePointsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.path = Path(self.temporary.name) / "click_map.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_delete_reports_only_points_that_existed(self):
        seed_map(self.path, save_ok=dict(x=1, y=2), admin_no=dict(x=3, y=4))

        removed = editor.delete_points(
            ["save_ok", "never_mapped"], path=self.path
        )

        self.assertEqual(removed, ("save_ok",))
        click_map_obj = click_map.ClickMap.load(self.path)
        self.assertIsNone(click_map_obj.get("save_ok"))
        self.assertIsNotNone(click_map_obj.get("admin_no"))

    def test_delete_on_a_missing_file_is_harmless(self):
        removed = editor.delete_points(["save_ok"], path=self.path)

        self.assertEqual(removed, ())
        self.assertFalse(self.path.exists())


class EditorWindowTests(unittest.TestCase):
    """Withdrawn-Tk smoke test: prefill, save, at delete sa buong window."""

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.path = Path(self.temporary.name) / "click_map.json"

    def tearDown(self):
        self.temporary.cleanup()

    def make_window(self):
        window = editor.FinalBillMapEditor(
            map_path=self.path, anchor_rect_fn=no_dialog
        )
        window.withdraw()
        self.addCleanup(window.destroy)
        window.update_idletasks()
        return window

    def test_entries_prefill_from_the_json_and_save_writes_back(self):
        seed_map(self.path, admin_no=dict(x=700, y=560))
        window = self.make_window()

        entry_x, entry_y = window.entries["admin_no"]
        self.assertEqual((entry_x.get(), entry_y.get()), ("700", "560"))
        blank_x, blank_y = window.entries["save_ok"]
        self.assertEqual((blank_x.get(), blank_y.get()), ("", ""))
        self.assertIn("WALA PA", window.status_vars["save_ok"].get())

        entry_x.delete(0, "end")
        entry_x.insert(0, "720")
        with mock.patch.object(
            editor.messagebox, "showinfo"
        ) as info, mock.patch.object(editor.messagebox, "showerror") as error:
            window.save()

        error.assert_not_called()
        info.assert_called_once()
        click_map_obj = click_map.ClickMap.load(self.path)
        admin_no = click_map_obj.get("admin_no")
        self.assertEqual((admin_no.x, admin_no.y), (720, 560))
        # Hindi hinawakan ang hindi binagong row.
        self.assertIsNone(click_map_obj.get("save_ok"))
        self.assertEqual(
            (entry_x.get(), entry_y.get()), ("720", "560")
        )

    def test_invalid_entry_shows_an_error_and_writes_nothing(self):
        seed_map(self.path, admin_no=dict(x=700, y=560))
        window = self.make_window()
        entry_x, _entry_y = window.entries["admin_no"]
        entry_x.delete(0, "end")
        entry_x.insert(0, "abc")
        before = self.path.read_text(encoding="utf-8")

        with mock.patch.object(
            editor.messagebox, "showinfo"
        ) as info, mock.patch.object(editor.messagebox, "showerror") as error:
            window.save()

        info.assert_not_called()
        error.assert_called_once()
        self.assertIn(
            "admin_no", error.call_args.args[1]
        )
        self.assertEqual(
            self.path.read_text(encoding="utf-8"), before
        )

    def test_delete_one_asks_first_then_removes(self):
        seed_map(self.path, admin_no=dict(x=700, y=560))
        window = self.make_window()

        with mock.patch.object(
            editor.messagebox, "askyesno", return_value=False
        ):
            window.delete_one("admin_no")
        self.assertIsNotNone(
            click_map.ClickMap.load(self.path).get("admin_no")
        )

        with mock.patch.object(
            editor.messagebox, "askyesno", return_value=True
        ):
            window.delete_one("admin_no")

        self.assertIsNone(
            click_map.ClickMap.load(self.path).get("admin_no")
        )
        entry_x, entry_y = window.entries["admin_no"]
        self.assertEqual((entry_x.get(), entry_y.get()), ("", ""))
        self.assertIn("Na-bura", window.message_var.get())

    def test_save_with_everything_blank_writes_nothing(self):
        window = self.make_window()
        before = (
            self.path.read_text(encoding="utf-8")
            if self.path.exists()
            else None
        )

        with mock.patch.object(
            editor.messagebox, "showinfo"
        ) as info, mock.patch.object(editor.messagebox, "showerror") as error:
            window.save()

        info.assert_not_called()
        error.assert_not_called()
        self.assertIn("Walang binagong field", window.message_var.get())
        after = (
            self.path.read_text(encoding="utf-8")
            if self.path.exists()
            else None
        )
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
