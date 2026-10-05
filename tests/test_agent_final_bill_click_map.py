"""Unit tests for the Final Bill click map (operator-recorded click points).

The map is the operator's own "unang OK", "File save OK", "Call Administrator
No" and "Final" checkbox, recorded once with F8 (or --set) and handed to
final_bill_actions. Every seam is injected — position, key, anchor rectangle,
print — so a whole mapping session runs headless: no HBSys, no desktop, no
clicks. The wiring tests prove the mapped point wins, that it is applied
RELATIVE to its dialog, and that a stale mapping falls back to the verified
control lookup instead of failing the row.

Run from the project root:

    python -m unittest tests.test_agent_final_bill_click_map
    python tests/test_agent_final_bill_click_map.py
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import final_bill_actions as fb  # noqa: E402
from core.agent import final_bill_click_map as click_map  # noqa: E402


class FakeDialog:
    """Enough window for the prompt answers: title, rect and visibility."""

    def __init__(self, title="File save", rect=(860, 660, 1100, 720), visible=True):
        self._title = title
        self._rect = rect
        self._visible = visible

    def window_text(self):
        return self._title

    def rectangle(self):
        return mock.Mock(
            left=self._rect[0],
            top=self._rect[1],
            right=self._rect[2],
            bottom=self._rect[3],
        )

    def is_visible(self):
        return self._visible


def write_map(path, **points):
    """A click map file with the given `name=(x, y[, dx, dy])` points."""
    data = {"version": 1, "screen": [1920, 1080], "mapped_at": "t", "points": {}}
    for name, values in points.items():
        x, y = values[0], values[1]
        data["points"][name] = {
            "label": name,
            "anchor": "dialog",
            "x": x,
            "y": y,
            "dx": values[2] if len(values) > 2 else None,
            "dy": values[3] if len(values) > 3 else None,
        }
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    return Path(path)


class ClickPointTests(unittest.TestCase):
    """The stored point is an offset INSIDE its dialog (that is the anchor)."""

    def test_anchored_point_follows_the_live_dialog_rectangle(self):
        point = click_map.ClickPoint(
            name="save_ok", anchor="File save", x=950, y=700, dx=90, dy=40
        )
        self.assertTrue(point.anchored)
        # Dialog moved 200 px left / 60 px up: the click moves with it.
        self.assertEqual(point.point_for((660, 620, 900, 680)), (750, 660))

    def test_a_point_without_an_offset_stays_absolute(self):
        point = click_map.ClickPoint(name="admin_no", x=700, y=560)
        self.assertFalse(point.anchored)
        self.assertEqual(point.point_for((1, 2, 3, 4)), (700, 560))
        self.assertEqual(point.point_for(None), (700, 560))

    def test_a_broken_rectangle_falls_back_to_the_absolute_point(self):
        point = click_map.ClickPoint(
            name="save_ok", anchor="File save", x=950, y=700, dx=90, dy=40
        )
        self.assertEqual(point.point_for((10, 20)), (950, 700))


class ClickMapFileTests(unittest.TestCase):
    """Saving, reading back, and reading a broken file (never fatal)."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "map.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_round_trip_keeps_points_offsets_and_screen(self):
        click_map.ClickMap(
            screen=(1920, 1080),
            points={
                "save_ok": click_map.ClickPoint(
                    name="save_ok", label="File save OK", anchor="File save",
                    x=950, y=700, dx=90, dy=40, anchor_rect=(860, 660, 1100, 720),
                )
            },
        ).save(self.path)

        loaded = click_map.ClickMap.load(self.path)
        self.assertEqual(loaded.screen, (1920, 1080))
        point = loaded.get("save_ok")
        self.assertIsNotNone(point)
        self.assertEqual((point.x, point.y), (950, 700))
        self.assertEqual((point.dx, point.dy), (90, 40))
        self.assertEqual(point.anchor_rect, (860, 660, 1100, 720))
        self.assertTrue(point.anchored)

    def test_a_missing_file_is_an_empty_map(self):
        self.assertIsNone(click_map.mapped_point("save_ok", path=self.path))
        self.assertEqual(click_map.ClickMap.load(self.path).points, {})

    def test_a_corrupt_file_is_an_empty_map_not_a_crash(self):
        self.path.write_text("{ this is not json", encoding="utf-8")
        self.assertEqual(click_map.ClickMap.load(self.path).points, {})

    def test_junk_points_are_skipped_but_good_ones_survive(self):
        self.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "points": {
                        "save_ok": {"x": 10, "y": 20},
                        "broken": "not-a-dict",
                        "half": {"x": "oops", "y": 1},
                    },
                }
            ),
            encoding="utf-8",
        )
        loaded = click_map.ClickMap.load(self.path)
        self.assertIsNotNone(loaded.get("save_ok"))
        self.assertIsNone(loaded.get("broken"))
        self.assertIsNone(loaded.get("half"))

    def test_mapped_point_resolves_against_the_dialog_rect(self):
        write_map(self.path, admin_no=(700, 560, 40, 20))
        self.assertEqual(
            click_map.mapped_point("admin_no", (660, 540, 1065, 590), self.path),
            (700, 560),
        )
        # No live rectangle: the absolute point taken during the session.
        self.assertEqual(click_map.mapped_point("admin_no", None, self.path), (700, 560))
        self.assertIsNone(click_map.mapped_point("save_ok", None, self.path))

    def test_show_and_check_text_report_what_is_mapped(self):
        write_map(self.path, save_ok=(950, 700, 90, 40))
        shown = click_map.show_text(self.path)
        self.assertIn("[x] save_ok", shown)
        self.assertIn("[ ] admin_no", shown)
        self.assertIn("WALA PA", shown)
        self.assertIn("admin_pair=", shown)

        checked = click_map.check_text(
            self.path, anchor_rect_fn=lambda marker: (860, 660, 1100, 720)
        )
        self.assertIn("mapped=oo", checked)
        self.assertIn("dialog 'File save': nakita", checked)
        self.assertIn("click ngayon kapag bukas: (950, 700)", checked)

    def test_clear_map_removes_the_file_and_is_safe_twice(self):
        write_map(self.path, save_ok=(1, 2))
        self.assertTrue(click_map.clear_map(self.path))
        self.assertFalse(self.path.exists())
        self.assertTrue(click_map.clear_map(self.path))


class ManualEntryTests(unittest.TestCase):
    """`--set name=x,y` is the fallback when a prompt cannot be shown on demand."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "map.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_set_manual_records_an_absolute_point_with_the_target_label(self):
        point = click_map.set_manual(
            "admin_no", 700, 560, path=self.path, note="manual"
        )
        self.assertEqual(point.label, "'Call Administrator' - No")
        self.assertEqual(point.anchor, "Call Administrator")
        self.assertFalse(point.anchored)
        self.assertEqual(
            click_map.mapped_point("admin_no", None, self.path), (700, 560)
        )

    def test_parse_manual_accepts_spaces_and_rejects_junk(self):
        self.assertEqual(click_map._parse_manual("save_ok=1010, 630"),
                         ("save_ok", 1010, 630))
        with self.assertRaises(ValueError):
            click_map._parse_manual("save_ok=1010")
        with self.assertRaises(ValueError):
            click_map._parse_manual("=10,20")
        with self.assertRaises(ValueError):
            click_map._parse_manual("save_ok=a,b")

    def test_main_set_show_and_clear_round_trip(self):
        code = click_map.main(["--set", "save_ok=1010,630", "--path", str(self.path)])
        self.assertEqual(code, 0)
        self.assertEqual(
            click_map.mapped_point("save_ok", None, self.path), (1010, 630)
        )
        self.assertEqual(click_map.main(["--show", "--path", str(self.path)]), 0)
        with mock.patch.object(
            click_map, "_default_anchor_rect", return_value=None
        ), mock.patch.object(
            click_map, "wait_for_capture_key", return_value="skip"
        ):
            self.assertEqual(
                click_map.main(["--check", "--path", str(self.path)]), 0
            )
            # --branch must NEVER wait on the real keyboard in tests: the two
            # popup targets are ESC'd and there is no prompt within 0 seconds.
            self.assertEqual(
                click_map.main(["--branch", "--branch-timeout", "0",
                                "--path", str(self.path)]),
                0,
            )
        self.assertEqual(click_map.main(["--clear", "--path", str(self.path)]), 0)
        self.assertFalse(self.path.exists())


class CaptureKeyTests(unittest.TestCase):
    """F8 = i-record, ESC = laktawan, walang key sa loob ng timeout = ''."""

    def test_f8_records_and_esc_skips(self):
        self.assertEqual(
            click_map.wait_for_capture_key(
                key_fn=lambda vk: vk == click_map.KEYS["f8"]
            ),
            "capture",
        )
        self.assertEqual(
            click_map.wait_for_capture_key(
                key_fn=lambda vk: vk == click_map.KEYS["esc"]
            ),
            "skip",
        )

    def test_timeout_returns_empty(self):
        self.assertEqual(
            click_map.wait_for_capture_key(key_fn=lambda vk: False, timeout=0), ""
        )

    def test_expand_names_understands_groups_and_keeps_order(self):
        self.assertEqual(
            click_map.expand_names(None), click_map.GROUPS["all"]
        )
        self.assertEqual(
            click_map.expand_names(["admin_pair"]),
            ("print_options_ok", "admin_no"),
        )
        # The default order is the LIVE order: checkbox, first OK, branches.
        self.assertEqual(
            click_map.GROUPS["all"],
            ("final_checkbox", "print_options_ok", "save_ok", "admin_no"),
        )
        # A group + a repeated member must not be recorded twice.
        self.assertEqual(
            click_map.expand_names(["dialog_oks", "print_options_ok", "who"]),
            ("print_options_ok", "save_ok", "who"),
        )


class CaptureSessionTests(unittest.TestCase):
    """A whole mapping session, headless: position/key/anchor are injected."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "map.json"
        self.printed: list = []
        self.rects = {
            "Print Options": (890, 330, 1130, 660),
            "File save": (860, 660, 1100, 720),
            "Call Administrator": (660, 540, 1065, 590),
        }

    def tearDown(self):
        self._tmp.cleanup()

    def _key_fn(self, presses):
        """Simulate the operator pressing `presses` (F8/ESC names), once each."""
        pending = list(presses)

        def key_fn(vk):
            if not pending:
                return False
            if vk == click_map.KEYS[pending[0]]:
                pending.pop(0)
                return True
            return False

        return key_fn

    def _session(self, names, positions, presses, **kwargs):
        queue = list(positions)
        return click_map.capture_session(
            names,
            map_path=self.path,
            position_fn=lambda: queue.pop(0),
            key_fn=self._key_fn(presses),
            anchor_rect_fn=lambda marker: self.rects.get(marker),
            print_fn=self.printed.append,
            **kwargs,
        )

    def test_admin_pair_records_both_clicks_with_their_offsets(self):
        # "Isang option": unang OK, tapos No - both in one session.
        session = self._session(
            "admin_pair", [(1010, 630), (700, 560)], ("f8", "f8")
        )

        self.assertEqual(sorted(session.points), ["admin_no", "print_options_ok"])
        first = session.get("print_options_ok")
        self.assertEqual((first.x, first.y), (1010, 630))
        self.assertEqual((first.dx, first.dy), (120, 300))
        # Anchored: the popup moves, the click follows it.
        self.assertEqual(first.point_for((940, 340, 1180, 670)), (1060, 640))

        no = session.get("admin_no")
        self.assertEqual((no.x, no.y), (700, 560))
        self.assertEqual((no.dx, no.dy), (40, 20))
        self.assertEqual(no.label, "'Call Administrator' - No")

        self.assertEqual(
            click_map.mapped_point("admin_no", None, self.path), (700, 560)
        )
        self.assertTrue(any("NAITALA" in line for line in self.printed))

    def test_countdown_mode_records_without_any_key(self):
        queue = [(950, 700)]
        with mock.patch("time.sleep") as sleep:
            session = click_map.capture_session(
                ["save_ok"],
                map_path=self.path,
                position_fn=lambda: queue.pop(0),
                anchor_rect_fn=lambda marker: self.rects.get(marker),
                print_fn=self.printed.append,
                countdown=5,
            )

        sleep.assert_called_once_with(5.0)
        point = session.get("save_ok")
        self.assertEqual((point.x, point.y), (950, 700))
        self.assertEqual((point.dx, point.dy), (90, 40))

    def test_esc_skips_a_target_and_keeps_what_was_mapped_before(self):
        write_map(self.path, admin_no=(1, 1, 2, 2))
        queue = [(999, 999)]
        session = click_map.capture_session(
            ["admin_no"],
            map_path=self.path,
            position_fn=lambda: queue.pop(0),
            key_fn=self._key_fn(("esc",)),
            anchor_rect_fn=lambda marker: None,
            print_fn=self.printed.append,
        )

        point = session.get("admin_no")
        self.assertEqual((point.x, point.y), (1, 1))
        self.assertTrue(any("LINALAKTAWAN" in line for line in self.printed))
        # the skipped point was never re-saved from the mouse position
        self.assertEqual(
            click_map.mapped_point("admin_no", None, self.path), (1, 1)
        )

    def test_timeout_records_nothing_and_says_so(self):
        with mock.patch("time.sleep"):
            session = click_map.capture_session(
                ["save_ok"],
                map_path=self.path,
                position_fn=lambda: (1, 2),
                key_fn=lambda vk: False,
                anchor_rect_fn=lambda marker: None,
                print_fn=self.printed.append,
                timeout=0,
            )

        self.assertEqual(session.points, {})
        self.assertTrue(any("TIMEOUT" in line for line in self.printed))

    def test_unknown_target_is_reported_and_skipped(self):
        session = click_map.capture_session(
            ["hindi_ko_alam"],
            map_path=self.path,
            position_fn=lambda: (1, 2),
            key_fn=lambda vk: False,
            anchor_rect_fn=lambda marker: None,
            print_fn=self.printed.append,
        )

        self.assertEqual(session.points, {})
        self.assertTrue(any("HINDI KILALA" in line for line in self.printed))

    def test_a_screen_sized_anchor_is_refused_and_the_point_stays_absolute(self):
        # Live 2026-10-02: "save_ok" anchored to [8, 33, 1920, 1073] - the whole
        # desktop, not the Note dialog. Tracking that rect makes the click drift
        # with the main window, so the offset is dropped instead.
        self.assertFalse(
            click_map._anchor_is_usable((8, 33, 1920, 1073), (1920, 1080))
        )
        self.assertFalse(click_map._anchor_is_usable(None, (1920, 1080)))
        self.assertFalse(click_map._anchor_is_usable((10, 10, 5, 5), (1920, 1080)))
        self.assertTrue(click_map._anchor_is_usable((761, 471, 1165, 620), (1920, 1080)))
        # No screen recorded (headless) - trust whatever the lookup returned.
        self.assertTrue(click_map._anchor_is_usable((8, 33, 1920, 1073), None))

        session = click_map.capture_session(
            names=["save_ok"],
            map_path=self.path,
            position_fn=lambda: (971, 593),
            # wait_for_capture_key() asks about a specific key (F8), so the
            # stub takes the key it is asked about - a 0-arg lambda raises here.
            key_fn=self._key_fn(("f8",)),
            anchor_rect_fn=lambda marker: (8, 33, 1920, 1073),
            print_fn=lambda message: None,
        )
        # capture_session() hands back the saved ClickMap, so the recorded
        # point is read back the same way the sibling tests read it.
        point = session.get("save_ok")
        self.assertEqual((point.x, point.y), (971, 593))
        self.assertIsNone(point.dx)
        self.assertIsNone(point.dy)
        self.assertIsNone(point.anchor_rect)

    def test_a_target_without_a_visible_dialog_is_stored_as_absolute(self):
        # Anchor dialog not on screen: the point is still recorded, absolute -
        # the operator can see from the map file that it is not anchored.
        self.rects = {}
        session = self._session(["admin_no"], [(700, 560)], ("f8",))

        point = session.get("admin_no")
        self.assertFalse(point.anchored)
        self.assertIsNone(point.anchor_rect)
        self.assertEqual(point.point_for((0, 0, 10, 10)), (700, 560))


class PostOkBranchTests(unittest.TestCase):
    """HBSys shows ONE prompt after the first OK - the tool waits for THAT one."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "map.json"
        self.printed: list = []
        self.rects: dict = {}
        self.positions: list = []

    def tearDown(self):
        self._tmp.cleanup()

    def _window(self, name=None, rect=(0, 0, 10, 10)):
        """Anchor lookup: only `name`'s dialog is on screen (the other waits)."""
        markers = {
            "save_ok": "File save",
            "admin_no": "Call Administrator",
            "print_options_ok": "Print Options",
            "final_checkbox": "Print Options",
        }

        def anchor_rect_fn(marker):
            return rect if name is not None and markers[name] == marker else None

        return anchor_rect_fn

    def _key_fn(self, presses):
        pending = list(presses)

        def key_fn(vk):
            if not pending:
                return False
            if vk == click_map.KEYS[pending[0]]:
                pending.pop(0)
                return True
            return False

        return key_fn

    def test_the_save_prompt_wins_the_branch_without_touching_no(self):
        chosen, rect = click_map.wait_for_post_ok_branch(
            key_fn=lambda vk: False,
            anchor_rect_fn=self._window("save_ok", (860, 660, 1100, 720)),
            timeout=0.1,
        )
        self.assertEqual(chosen, "save_ok")
        self.assertEqual(rect, (860, 660, 1100, 720))

    def test_the_admin_dialog_wins_the_branch_without_touching_save(self):
        chosen, rect = click_map.wait_for_post_ok_branch(
            key_fn=lambda vk: False,
            anchor_rect_fn=self._window("admin_no", (660, 540, 1065, 590)),
            timeout=0.1,
        )
        self.assertEqual(chosen, "admin_no")
        self.assertEqual(rect, (660, 540, 1065, 590))

    def test_timeout_or_esc_map_nothing(self):
        chosen, rect = click_map.wait_for_post_ok_branch(
            key_fn=lambda vk: False,
            anchor_rect_fn=self._window(None),
            timeout=0,
        )
        self.assertEqual((chosen, rect), ("", None))

        chosen, rect = click_map.wait_for_post_ok_branch(
            key_fn=self._key_fn(("esc",)),
            anchor_rect_fn=self._window("save_ok"),
        )
        self.assertEqual((chosen, rect), ("", None))

    def test_the_save_prompt_is_found_by_its_plain_save_title_too(self):
        # Ibang build: "Save" lang ang title (tulad ng _front_save_prompt).
        def anchor_rect_fn(marker):
            return (860, 660, 1100, 720) if marker == "Save" else None

        chosen, rect = click_map.wait_for_post_ok_branch(
            key_fn=lambda vk: False,
            anchor_rect_fn=anchor_rect_fn,
            timeout=0.1,
        )
        self.assertEqual(chosen, "save_ok")
        self.assertEqual(rect, (860, 660, 1100, 720))

    def test_f8_during_the_wait_records_even_when_no_title_matches(self):
        # Dating bug: detection-only ang loop, kaya F8 habang hinihintay ay
        # walang nangyayari. Ngayon: F8 -> itatanong kung alin ang lumabas.
        asked = []

        def choose():
            asked.append(True)
            return "save_ok"

        chosen, rect = click_map.wait_for_post_ok_branch(
            key_fn=self._key_fn(("f8",)),
            anchor_rect_fn=self._window(None),
            f8_grace=0,
            choose_fn=choose,
        )
        # rect None = walang nabasang title -> ABSOLUTE ang record.
        self.assertEqual((chosen, rect), ("save_ok", None))
        self.assertEqual(len(asked), 1)

    def test_a_full_branch_session_maps_checkbox_ok_and_only_the_branch(self):
        self.positions = [(390, 337), (1010, 630), (950, 700)]
        session = click_map.branch_session(
            map_path=self.path,
            position_fn=lambda: self.positions.pop(0),
            key_fn=self._key_fn(("f8", "f8", "f8")),
            anchor_rect_fn=self._window("save_ok", (860, 660, 1100, 720)),
            print_fn=self.printed.append,
            branch_timeout=5,
        )

        self.assertEqual(
            sorted(session.points),
            ["final_checkbox", "print_options_ok", "save_ok"],
        )
        self.assertIsNone(session.get("admin_no"))
        save = session.get("save_ok")
        self.assertEqual((save.x, save.y), (950, 700))
        self.assertEqual((save.dx, save.dy), (90, 40))
        self.assertEqual(
            click_map.mapped_point("save_ok", None, self.path), (950, 700)
        )

    def test_a_branch_session_with_no_prompt_maps_only_the_popup(self):
        session = click_map.branch_session(
            map_path=self.path,
            position_fn=lambda: (1010, 630),
            key_fn=self._key_fn(("esc", "f8")),
            anchor_rect_fn=self._window(None),
            print_fn=self.printed.append,
            branch_timeout=0,
        )

        self.assertEqual(sorted(session.points), ["print_options_ok"])
        self.assertTrue(any("Walang prompt" in line for line in self.printed))

    def test_full_session_records_the_branch_on_the_f8_that_asked(self):
        # Prompt appears but its title cannot be read: the operator hovers and
        # presses F8 once - the session must record immediately (no extra F8).
        positions = [(390, 337), (1010, 630), (950, 700)]
        session = click_map.branch_session(
            map_path=self.path,
            position_fn=lambda: positions.pop(0),
            key_fn=self._key_fn(("f8", "f8", "f8")),
            anchor_rect_fn=self._window(None),
            print_fn=self.printed.append,
            branch_timeout=5,
            f8_grace=0,
            choose_fn=lambda: "save_ok",
        )

        self.assertEqual(
            sorted(session.points),
            ["final_checkbox", "print_options_ok", "save_ok"],
        )
        save = session.get("save_ok")
        self.assertEqual((save.x, save.y), (950, 700))
        self.assertIsNone(save.dx)  # walang nabasang title -> absolute
        self.assertTrue(any("NAITALA (F8)" in line for line in self.printed))


class FinalBillWiringTests(unittest.TestCase):
    """final_bill_actions: the mapped point wins, and a stale map degrades."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "map.json"
        self._real_path = click_map.CLICK_MAP_PATH
        click_map.CLICK_MAP_PATH = self.path

    def tearDown(self):
        click_map.CLICK_MAP_PATH = self._real_path
        self._tmp.cleanup()

    def test_print_options_ok_clicks_the_point_relative_to_the_popup(self):
        write_map(self.path, print_options_ok=(1010, 630, 120, 300))
        # The operator recorded it on a popup at (890, 330); this run the popup
        # is at (700, 200) - the click must follow the popup, not the old spot.
        window = FakeDialog("Print Options", rect=(700, 200, 940, 530))
        # The click closes the popup, so the one click is reported as taken.
        def close_it(x, y):
            window._visible = False

        with mock.patch.object(
            fb, "real_click", side_effect=close_it
        ) as click, mock.patch.object(fb, "raise_to_top"):
            self.assertTrue(fb.click_print_options_ok(window))

        self.assertEqual(click.call_args.args, (820, 500))

    def test_unmapped_print_options_ok_keeps_the_verified_control(self):
        window = FakeDialog("Print Options")
        button = mock.Mock()
        with mock.patch.object(
            fb, "_visible_button", return_value=button
        ) as finder, mock.patch.object(
            fb,
            "click_button",
            side_effect=lambda btn, win: setattr(window, "_visible", False),
        ) as click:
            self.assertTrue(fb.click_print_options_ok(window))

        finder.assert_called_once()
        click.assert_called_once_with(button, window)

    def test_ok_is_clicked_once_even_when_the_popup_survives(self):
        # A swallowed click must not be retried into a double press: the one
        # click is reported as done and the planner's ok_clicked guard takes
        # over from there.
        window = FakeDialog("Print Options", visible=True)
        calls = []
        with mock.patch.object(
            fb, "_mapped_click_point", return_value=(10, 20)
        ), mock.patch.object(
            fb, "raise_to_top"
        ), mock.patch.object(
            fb, "real_click", side_effect=lambda x, y: calls.append((x, y))
        ), mock.patch.object(fb, "POPUP_CLOSE_TIMEOUT_SECONDS", 0.05):
            self.assertFalse(fb.click_print_options_ok(window))
        self.assertEqual(len(calls), 1)

    def test_unmapped_and_missing_ok_raises_the_readable_error(self):
        with mock.patch.object(fb, "_visible_button", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                fb.click_print_options_ok(FakeDialog("Print Options"))
        self.assertIn("OK button (id 1002) not found", str(ctx.exception))

    def test_a_corrupt_map_never_breaks_the_flow(self):
        self.path.write_text("{ broken", encoding="utf-8")
        window = FakeDialog("Print Options")
        with mock.patch.object(
            fb, "click_button", side_effect=lambda b, w: setattr(window, "_visible", False)
        ) as click, mock.patch.object(fb, "_visible_button", return_value=mock.Mock()):
            self.assertTrue(fb.click_print_options_ok(window))
        click.assert_called_once()

    def test_file_save_prompt_ok_clicks_the_mapped_point(self):
        write_map(self.path, save_ok=(950, 700, 90, 40))
        dialog = FakeDialog("File save", rect=(860, 660, 1100, 720))
        with mock.patch.object(
            fb, "_front_save_prompt", return_value=(dialog, "File save")
        ), mock.patch.object(fb, "raise_to_top"), mock.patch.object(
            fb, "real_click"
        ) as click, mock.patch.object(
            fb, "_wait_prompt_gone", return_value=True
        ), mock.patch.object(
            fb, "answer_dialog_ok"
        ) as control_click, mock.patch("time.sleep"):
            label = fb.answer_save_prompt_with_ok()

        self.assertEqual(label, "File save")
        self.assertEqual(click.call_args.args, (950, 700))
        control_click.assert_not_called()

    def test_call_administrator_no_clicks_the_mapped_point(self):
        write_map(self.path, admin_no=(700, 560, 40, 20))
        dialog = FakeDialog("Call Administrator", rect=(660, 540, 1065, 590))
        with mock.patch.object(
            fb, "find_dialog", return_value=dialog
        ), mock.patch.object(fb, "raise_to_top"), mock.patch.object(
            fb, "real_click"
        ) as click, mock.patch.object(
            fb, "_wait_prompt_gone", return_value=True
        ), mock.patch.object(
            fb, "answer_dialog_no"
        ) as control_click, mock.patch("time.sleep"):
            label = fb.answer_confirm_prompt_with_no()

        self.assertEqual(label, "Call Administrator")
        self.assertEqual(click.call_args.args, (700, 560))
        control_click.assert_not_called()

    def test_a_stale_mapping_falls_back_to_the_verified_control(self):
        # Recorded point misses (dialog moved): the old lookup runs and the row
        # is saved - a stale map must never fail a patient.
        write_map(self.path, save_ok=(1, 1, 2, 2))
        dialog = FakeDialog("File save")
        with mock.patch.object(
            fb, "_front_save_prompt", return_value=(dialog, "File save")
        ), mock.patch.object(fb, "raise_to_top"), mock.patch.object(
            fb, "real_click"
        ), mock.patch.object(
            fb, "_wait_prompt_gone", side_effect=[False, True]
        ), mock.patch.object(
            fb, "answer_dialog_ok"
        ) as control_click, mock.patch("time.sleep"):
            label = fb.answer_save_prompt_with_ok()

        self.assertEqual(label, "File save")
        control_click.assert_called_once_with(dialog)

    def test_when_both_the_map_and_the_control_miss_the_row_fails_readably(self):
        write_map(self.path, save_ok=(1, 1, 2, 2))
        dialog = FakeDialog("File save")
        with mock.patch.object(
            fb, "_front_save_prompt", return_value=(dialog, "File save")
        ), mock.patch.object(fb, "raise_to_top"), mock.patch.object(
            fb, "real_click"
        ), mock.patch.object(
            fb, "_wait_prompt_gone", return_value=False
        ), mock.patch.object(
            fb, "answer_dialog_ok"
        ), mock.patch("time.sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                fb.answer_save_prompt_with_ok()

        self.assertIn("stayed open", str(ctx.exception))
        self.assertIn("File save", str(ctx.exception))

    def test_the_final_checkbox_uses_the_mapped_point(self):
        write_map(self.path, final_checkbox=(390, 337, 10, 12))
        window = FakeDialog("Print Options", rect=(333, 315, 447, 349))
        button = mock.Mock()

        def rect_of(control):
            return (
                (333, 315, 447, 349)
                if control is window
                else (333, 325, 447, 349)
            )

        with mock.patch.object(
            fb, "_visible_button", return_value=button
        ), mock.patch.object(
            fb, "rect_of", side_effect=rect_of
        ), mock.patch.object(
            fb, "checkbox_dark_ratio", side_effect=[0.1, 0.6]
        ), mock.patch.object(fb, "raise_to_top"), mock.patch.object(
            fb, "real_click"
        ) as click, mock.patch("time.sleep"):
            self.assertTrue(fb.toggle_final_checkbox(window))

        # anchored to the popup: (333 + 10, 315 + 12)
        self.assertEqual(click.call_args.args, (343, 327))

    def test_the_final_checkbox_without_a_map_keeps_the_verified_offset(self):
        window = FakeDialog("Print Options", rect=(333, 315, 447, 349))
        button = mock.Mock()
        with mock.patch.object(
            fb, "_visible_button", return_value=button
        ), mock.patch.object(
            fb, "rect_of", return_value=(333, 325, 447, 349)
        ), mock.patch.object(
            fb, "checkbox_dark_ratio", side_effect=[0.1, 0.6]
        ), mock.patch.object(fb, "raise_to_top"), mock.patch.object(
            fb, "real_click"
        ) as click, mock.patch("time.sleep"):
            self.assertTrue(fb.toggle_final_checkbox(window))

        offset_x, offset_y = fb.FINAL_CHECKBOX_OFFSET
        self.assertEqual(click.call_args.args, (333 + offset_x, 325 + offset_y))


if __name__ == "__main__":
    unittest.main()
