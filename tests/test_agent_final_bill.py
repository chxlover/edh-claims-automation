"""Unit tests for the Final Bill Actions (Slice D — Claims Agent).

plan_step() is a pure function of (screen, final_checked, bill_finalized,
forms_open) and FinalBillRunner takes every GUI primitive as an injection, so
the whole decision path — including the two live-verified branches (confirm ->
No, and File save -> OK) — runs headless: no HBSys, no DB, no clicks.

Run from the project root:

    python -m unittest tests.test_agent_final_bill
    python tests/test_agent_final_bill.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import final_bill_actions as fb  # noqa: E402
from core.agent import final_bill_click_map as click_map  # noqa: E402
from core.agent import hbsys_screens as screens  # noqa: E402


# -- test hygiene (2026-09-30) -------------------------------------------------
# After a mapping session the operator's machine carries a real click map
# (logs/final_bill_click_map.json) with the live "unang OK" / "File save" /
# "Call Administrator" points. These tests cover the CONTROL-ID path, so the
# map path is pointed at a temp file that never exists. The click-map wiring
# itself is covered by tests/test_agent_final_bill_click_map.py.

_CLICK_MAP_PATH = click_map.CLICK_MAP_PATH


def setUpModule():
    click_map.CLICK_MAP_PATH = (
        Path(tempfile.mkdtemp(prefix="no_click_map_"))
        / "final_bill_click_map.json"
    )


def tearDownModule():
    click_map.CLICK_MAP_PATH = _CLICK_MAP_PATH


class FakeHbsys:
    """Scripted stand-in for the live HBSys window.

    Each injected primitive mutates the fake state the same way the real GUI
    does, so detect() always reports the screen that action produced — the
    runner therefore walks the exact sequence observed live on 2026-09-25.
    """

    def __init__(self, popup: str | None = None, form_open: bool = True,
                 broken_step: str = ""):
        self.popup = popup            # None | "options" | "confirm" | "save"
        self.form_open = form_open
        self.final_checked = False
        self.bill_finalized = False
        self.broken_step = broken_step
        self.calls: list[str] = []

    # -- injected primitives ----------------------------------------

    def detect(self) -> str:
        if self.popup == "options":
            return screens.SCREEN_FINAL_BILL_OPTIONS
        if self.popup == "confirm":
            return screens.SCREEN_FINAL_BILL_CONFIRM
        if self.popup == "save":
            return screens.SCREEN_FILE_SAVE
        if not self.form_open:
            # The bare User Menu is not in the detector vocabulary yet; the
            # planner short-circuits on bill_finalized before the screen
            # matters, so any non-flow screen is a faithful stand-in.
            return screens.SCREEN_UNKNOWN
        return screens.SCREEN_ORDER_TRANSACTIONS

    def menu(self, path: str) -> None:
        if self.broken_step == fb.STEP_OPEN_FINAL_BILL:
            raise RuntimeError("menu boom")
        self.calls.append(f"menu:{path}")
        self.popup = "options"

    def check_final(self) -> bool:
        if self.broken_step == fb.STEP_CHECK_FINAL:
            raise RuntimeError("glyph boom")
        self.calls.append("check_final")
        self.final_checked = True
        return True

    def click_ok(self) -> None:
        if self.broken_step == fb.STEP_CLICK_OK:
            raise RuntimeError("ok boom")
        self.calls.append("click_ok")
        # Live 2026-10-02: OK leaves Print Options open BEHIND the prompt it
        # raises ("Note"/"File save" or "Call Administrator"); the prompt is
        # answered next, and Print Options may still read as open behind it.
        self.popup = "confirm" if self.popup == "options" else None

    def save_click_ok(self) -> None:
        if self.broken_step == fb.STEP_SAVE_CLICK_OK:
            raise RuntimeError("save ok boom")
        self.calls.append("save_click_ok")
        self.popup = None
        self.bill_finalized = True

    def confirm_no(self) -> None:
        if self.broken_step == fb.STEP_CONFIRM_NO:
            raise RuntimeError("no boom")
        self.calls.append("confirm_no")
        self.popup = None
        self.bill_finalized = True

    def forms_open(self) -> bool:
        return self.form_open


def make_runner(fake: FakeHbsys, **kwargs) -> fb.FinalBillRunner:
    # Close Form was removed from the end of the flow (2026-10-02): the runner
    # has no close_form_fn and no close_form_at_end opt-in, so the Billing form
    # is left open for the next patient's loader to replace.
    return fb.FinalBillRunner(
        detect_fn=fake.detect,
        menu_fn=fake.menu,
        forms_open_fn=fake.forms_open,
        check_final_fn=fake.check_final,
        click_ok_fn=fake.click_ok,
        save_click_ok_fn=fake.save_click_ok,
        confirm_no_fn=fake.confirm_no,
        **kwargs,
    )



class PlanStepTests(unittest.TestCase):
    """Every screen/state branch of the pure planner."""

    def test_options_without_tick_plans_check_first(self):
        decision = fb.plan_step(screens.SCREEN_FINAL_BILL_OPTIONS)
        self.assertEqual(decision.step, fb.STEP_CHECK_FINAL)
        self.assertFalse(decision.final_checked)
        self.assertFalse(decision.terminal)

    def test_options_after_tick_plans_ok(self):
        decision = fb.plan_step(
            screens.SCREEN_FINAL_BILL_OPTIONS, final_checked=True
        )
        self.assertEqual(decision.step, fb.STEP_CLICK_OK)
        self.assertTrue(decision.final_checked)

    def test_confirm_prompt_plans_click_no(self):
        # "Call Administrator" Yes/No: the operator clicks "No". Clicking is
        # deliberate - "Yes" would replace the computed charges, so the answer
        # must never depend on tab order or on ENTER accepting the focus.
        decision = fb.plan_step(screens.SCREEN_FINAL_BILL_CONFIRM)
        self.assertEqual(decision.step, fb.STEP_CONFIRM_NO)
        self.assertIn("No", decision.reason)
        self.assertNotIn("TAB", decision.reason)

    def test_file_save_prompt_plans_click_ok(self):
        # The "File save" prompt is answered by clicking "OK" - no keystroke.
        decision = fb.plan_step(screens.SCREEN_FILE_SAVE)
        self.assertEqual(decision.step, fb.STEP_SAVE_CLICK_OK)
        self.assertIn("OK", decision.reason)
        self.assertNotIn("TAB", decision.reason)

    def test_print_options_after_ok_blocks_instead_of_double_clicking(self):
        # Live 2026-10-02: OK stays pressed ONCE even when Print Options is
        # still on screen. Its stale read alone (no prompt, no expected
        # prompt) must never trigger a second OK click - it blocks for a
        # human instead of re-clicking blindly.
        decision = fb.plan_step(
            screens.SCREEN_FINAL_BILL_OPTIONS, final_checked=True,
            ok_clicked=True,
        )
        self.assertEqual(decision.step, fb.STEP_BLOCKED)
        self.assertIn("never clicked twice", decision.reason)

    def test_print_options_with_expected_save_prompt_clicks_save(self):
        # Live 2026-10-02 15:03 (patient #21853): OK raised the File save prompt
        # while Print Options stayed open behind it - and the detector reads
        # only raw titles when OCR never sees the prompt. The plan's own
        # predicted prompt (from the Agent Plan reason) says the prompt that
        # is up, so the mapped File save OK (971,593 / +(963,560)) is clicked
        # once - never a second OK, never a blind click.
        decision = fb.plan_step(
            screens.SCREEN_FINAL_BILL_OPTIONS, final_checked=True,
            ok_clicked=True,
            expected_screen=screens.SCREEN_FILE_SAVE,
        )
        self.assertEqual(decision.step, fb.STEP_SAVE_CLICK_OK)
        self.assertIn("971,593", decision.reason)
        answered = fb.plan_step(
            screens.SCREEN_FINAL_BILL_OPTIONS, final_checked=True,
            ok_clicked=True, prompt_answered=True,
            expected_screen=screens.SCREEN_FILE_SAVE,
        )
        self.assertEqual(answered.step, fb.STEP_DONE)

    def test_print_options_with_expected_confirm_clicks_no(self):
        # Same stale read, but the plan predicted the "Call Administrator"
        # branch (MISMATCH reason): answer No, never a second OK.
        decision = fb.plan_step(
            screens.SCREEN_FINAL_BILL_OPTIONS, final_checked=True,
            ok_clicked=True,
            expected_screen=screens.SCREEN_FINAL_BILL_CONFIRM,
        )
        self.assertEqual(decision.step, fb.STEP_CONFIRM_NO)
        answered = fb.plan_step(
            screens.SCREEN_FINAL_BILL_OPTIONS, final_checked=True,
            ok_clicked=True, prompt_answered=True,
            expected_screen=screens.SCREEN_FINAL_BILL_CONFIRM,
        )
        self.assertEqual(answered.step, fb.STEP_DONE)

    def test_prompt_is_answered_exactly_once_after_ok(self):
        # OK hands over to a prompt; the answer runs once and the bill is final.
        decision = fb.plan_step(
            screens.SCREEN_FINAL_BILL_CONFIRM, ok_clicked=True
        )
        self.assertEqual(decision.step, fb.STEP_CONFIRM_NO)
        self.assertTrue(decision.ok_clicked)
        # Already answered -> the flow moves on. Close Form is only planned
        # when the caller says the batch is over (see the next test).
        after = fb.plan_step(
            screens.SCREEN_FINAL_BILL_CONFIRM,
            final_checked=True,
            ok_clicked=True,
            prompt_answered=True,
        )
        self.assertEqual(after.step, fb.STEP_DONE)
        self.assertTrue(after.terminal)

    def test_the_flow_never_plans_a_close_form(self):
        # Removed 2026-10-02: the Final Bill ends right after the post-OK
        # prompt is answered. There is no last-patient branch any more - the
        # Billing form stays open and the next patient's loader replaces it.
        answered = fb.plan_step(
            screens.SCREEN_FILE_SAVE,
            final_checked=True,
            ok_clicked=True,
            prompt_answered=True,
        )
        self.assertEqual(answered.step, fb.STEP_DONE)
        self.assertTrue(answered.terminal)
        self.assertNotIn("close", answered.step)
        self.assertNotIn("Close Form", answered.reason)

    def test_ok_without_a_readable_prompt_blocks_instead_of_guessing(self):
        # The two prompt screens are the only things the agent will answer; a
        # build that shows neither must stop for a human, not blind-press.
        decision = fb.plan_step(
            screens.SCREEN_ORDER_TRANSACTIONS, ok_clicked=True
        )
        self.assertEqual(decision.step, fb.STEP_BLOCKED)
        self.assertIn("File save", decision.reason)

    def test_billing_form_plans_open_menu(self):
        decision = fb.plan_step(screens.SCREEN_ORDER_TRANSACTIONS)
        self.assertEqual(decision.step, fb.STEP_OPEN_FINAL_BILL)
        self.assertIn("Billing -> Final Bill", decision.reason)

    def test_billing_form_missing_blocks(self):
        decision = fb.plan_step(
            screens.SCREEN_ORDER_TRANSACTIONS, forms_open=False
        )
        self.assertEqual(decision.step, fb.STEP_BLOCKED)
        self.assertTrue(decision.terminal)
        self.assertIn("human step", decision.reason)

    def test_closed_hbsys_blocks(self):
        decision = fb.plan_step(screens.SCREEN_HBSYS_CLOSED)
        self.assertEqual(decision.step, fb.STEP_BLOCKED)
        self.assertIn("CLOSED", decision.reason)

    def test_foreign_screen_blocks_and_routes_to_patient_review(self):
        decision = fb.plan_step(screens.SCREEN_ECLAIMS_DASHBOARD)
        self.assertEqual(decision.step, fb.STEP_BLOCKED)
        self.assertIn("Patient Review", decision.reason)
        self.assertEqual(decision.screen, screens.SCREEN_ECLAIMS_DASHBOARD)

    def test_finalized_with_open_form_is_done_without_closing(self):
        # The bill is final and the form is still open: that is a normal
        # finish now, not a reason to click Close Form.
        decision = fb.plan_step(
            screens.SCREEN_ORDER_TRANSACTIONS,
            bill_finalized=True,
        )
        self.assertEqual(decision.step, fb.STEP_DONE)
        self.assertTrue(decision.bill_finalized)
        self.assertNotIn("Close Form", decision.reason)

    def test_finalized_patient_does_not_close_the_form(self):
        # Every patient leaves the Billing form open - the next patient's
        # loader closes it anyway, so a Close Form click would be wasted.
        decision = fb.plan_step(
            screens.SCREEN_ORDER_TRANSACTIONS,
            bill_finalized=True,
        )
        self.assertEqual(decision.step, fb.STEP_DONE)
        self.assertIn("stays open", decision.reason)

    def test_prompt_answer_never_implies_a_close_form(self):
        # Even right after the prompt is answered, no Close Form may follow.
        decision = fb.plan_step(
            screens.SCREEN_FINAL_BILL_CONFIRM,
            prompt_answered=True,
        )
        self.assertEqual(decision.step, fb.STEP_DONE)
        self.assertNotIn("Close Form", decision.reason)

    def test_finalized_with_closed_form_is_done(self):
        decision = fb.plan_step(
            screens.SCREEN_UNKNOWN,
            bill_finalized=True,
            forms_open=False,
        )
        self.assertEqual(decision.step, fb.STEP_DONE)
        self.assertTrue(decision.terminal)

    def test_every_planned_step_is_in_the_vocabulary(self):
        cases = [
            fb.plan_step(screens.SCREEN_FINAL_BILL_OPTIONS),
            fb.plan_step(screens.SCREEN_FINAL_BILL_CONFIRM),
            fb.plan_step(screens.SCREEN_FILE_SAVE),
            fb.plan_step(screens.SCREEN_ORDER_TRANSACTIONS),
            fb.plan_step(screens.SCREEN_HBSYS_CLOSED),
            fb.plan_step(screens.SCREEN_UNKNOWN),
        ]
        for decision in cases:
            self.assertIn(decision.step, fb.ALL_STEPS)
        # 7 steps: menu, checkbox, OK, save click-OK, confirm click-No, done
        # (+ blocked). Close Form was removed from the end of the flow.
        self.assertEqual(len(fb.ALL_STEPS), 7)
        self.assertIn(fb.STEP_SAVE_CLICK_OK, fb.ALL_STEPS)
        self.assertIn(fb.STEP_CONFIRM_NO, fb.ALL_STEPS)

    def test_post_ok_prompts_use_the_operators_own_answers(self):
        """"File save" -> click "OK"; "Call Administrator" -> click "No".

        Operator instruction, 2026-09-28. Both prompts are answered with the
        MOUSE, and differently on purpose: a click on the save prompt's OK,
        and a click on "No" (never "Yes", which would replace the computed
        charges). No keystroke is involved, so no tab-order guesswork.
        """
        save = fb.plan_step(screens.SCREEN_FILE_SAVE, final_checked=True)
        self.assertEqual(save.step, fb.STEP_SAVE_CLICK_OK)
        self.assertIn("OK", save.reason)
        self.assertNotIn("TAB", save.reason)

        confirm = fb.plan_step(
            screens.SCREEN_FINAL_BILL_CONFIRM, final_checked=True
        )
        self.assertEqual(confirm.step, fb.STEP_CONFIRM_NO)
        self.assertIn("No", confirm.reason)
        self.assertNotIn("TAB", confirm.reason)


class FinalCheckboxRepaintTests(unittest.TestCase):
    """The 'Final' glyph is re-read only AFTER the click has repainted.

    2026-09-29 12:43: the step read the pixels straight after the click, saw
    the old (unticked) glyph, clicked a second time - unticking the box it had
    just ticked - and the flow looped until the step budget ran out, never
    reaching the "File save" / "Call Administrator" prompts at all.
    """

    BUTTON_RECT = (333, 325, 447, 349)

    def _toggle(self, ratios):
        """Run toggle_final_checkbox with the GUI stubbed out.

        Returns (ticked, events, sleeps) where `events` interleaves every
        glyph read and every repaint settle in the order they happened.
        """
        events: list = []
        reads = iter(ratios)
        button = mock.Mock()

        def read_glyph(_control):
            events.append("read")
            return next(reads)

        def settle(seconds):
            events.append("settle")
            events[-1] = f"settle:{seconds}"

        with mock.patch.object(
            fb, "_visible_button", return_value=button
        ), mock.patch.object(
            fb, "rect_of", return_value=self.BUTTON_RECT
        ), mock.patch.object(
            fb, "checkbox_dark_ratio", side_effect=read_glyph
        ), mock.patch.object(
            fb, "raise_to_top"
        ), mock.patch.object(
            fb, "real_click"
        ), mock.patch("time.sleep", side_effect=settle) as sleep:
            ticked = fb.toggle_final_checkbox(mock.Mock())
        return ticked, events, sleep

    def test_a_single_click_settles_before_the_glyph_is_re_read(self):
        ticked, events, sleep = self._toggle([0.10, 0.55])

        self.assertTrue(ticked)
        # before-read, repaint settle, after-read - never read-then-read.
        self.assertEqual(
            events,
            [
                "read",
                f"settle:{fb.CHECKBOX_REPAINT_SETTLE_SECONDS}",
                "read",
            ],
        )
        sleep.assert_called_once_with(fb.CHECKBOX_REPAINT_SETTLE_SECONDS)

    def test_the_second_click_also_settles_before_its_re_read(self):
        # first read 0.50 (ticked already), click -> still 0.50 in flight, so
        # the second click runs - and IT settles too before the final read.
        ticked, events, sleep = self._toggle([0.50, 0.50, 0.85])

        self.assertTrue(ticked)
        self.assertEqual(
            events,
            [
                "read",
                f"settle:{fb.CHECKBOX_REPAINT_SETTLE_SECONDS}",
                "read",
                f"settle:{fb.CHECKBOX_REPAINT_SETTLE_SECONDS}",
                "read",
            ],
        )
        self.assertEqual(sleep.call_count, 2)


class EvidenceTests(unittest.TestCase):
    """The verified control rectangles / ids stay pinned by tests."""

    def test_five_verified_controls(self):
        self.assertEqual(len(fb.FINAL_BILL_EVIDENCE), 5)

    def test_final_checkbox_offset_lands_inside_the_rect(self):
        evidence = fb.evidence_for(fb.BTN_FINAL_ID)
        left, top, right, bottom = evidence.rect
        offset_x, offset_y = fb.FINAL_CHECKBOX_OFFSET
        self.assertEqual(evidence.control_text, "Final")
        # The live toggle point (left+7, top+12) = (340, 337) is inside.
        self.assertTrue(left + offset_x < right)
        self.assertTrue(top + offset_y < bottom)
        self.assertEqual((left + offset_x, top + offset_y), (340, 337))

    def test_confirm_no_is_the_click_target_and_yes_is_not(self):
        no = fb.evidence_for(fb.BTN_CONFIRM_NO_ID)
        yes = fb.evidence_for(fb.BTN_CONFIRM_YES_ID)
        self.assertEqual(no.control_text, "&No")
        self.assertIn("operator's answer", no.note.lower())
        self.assertIn("do not click", yes.note.lower())

    def test_unknown_control_id_raises(self):
        with self.assertRaises(KeyError):
            fb.evidence_for(4242)


class TooltipLabelTests(unittest.TestCase):
    def test_close_form_label_matches_tolerantly(self):
        self.assertTrue(fb._tooltip_matches_close_form("Close Form"))
        self.assertTrue(fb._tooltip_matches_close_form("Close  FORM"))

    def test_other_labels_do_not_match(self):
        self.assertFalse(fb._tooltip_matches_close_form("Print"))
        self.assertFalse(fb._tooltip_matches_close_form(""))
        self.assertFalse(fb._tooltip_matches_close_form("Close"))


class FileSaveOkClickTests(unittest.TestCase):
    """'File save' -> click the OK in the BOTTOM button row, by any means.

    The operator's marked-up screenshot showed the OK is the control in the
    button row at the bottom of the prompt. On the live PowerBuilder dialog
    that control's caption is not always readable, so the click must not
    depend on the caption - only on the button NOT being a cancel.
    """

    class FakeControl:
        def __init__(self, class_name, text, rect, visible=True):
            self._class = class_name
            self._text = text
            self._rect = rect
            self._visible = visible

        def class_name(self):
            return self._class

        def window_text(self):
            return self._text

        def rectangle(self):
            return mock.Mock(
                left=self._rect[0], top=self._rect[1],
                right=self._rect[2], bottom=self._rect[3],
            )

        def is_visible(self):
            return self._visible

    class FakeWindow:
        def __init__(self, controls, rect=(0, 0, 400, 300)):
            self._controls = controls
            self._rect = rect

        def children(self):
            return list(self._controls)

        def rectangle(self):
            return mock.Mock(
                left=self._rect[0], top=self._rect[1],
                right=self._rect[2], bottom=self._rect[3],
            )

    def _clicked(self, window):
        """Run answer_dialog_ok with the click stubbed; return the click point."""
        with mock.patch.object(fb, "real_click") as click:
            fb.answer_dialog_ok(window)
            # real_click(x, y) is called positionally.
            return click.call_args.args if click.call_args else None

    def test_lowest_ok_is_clicked_not_the_upper_one(self):
        window = self.FakeWindow([
            self.FakeControl("Button", "OK", (100, 40, 180, 65)),
            self.FakeControl("Button", "OK", (100, 250, 180, 275)),
        ])
        self.assertEqual(self._clicked(window), (140, 262))

    def test_cancel_is_never_clicked_even_when_it_is_lowest(self):
        window = self.FakeWindow([
            self.FakeControl("Button", "OK", (100, 250, 180, 275)),
            self.FakeControl("Button", "Cancel", (200, 250, 280, 275)),
        ])
        self.assertEqual(self._clicked(window), (140, 262))

    def test_unlabelled_owner_drawn_button_is_still_clicked(self):
        # The live case: a Button whose caption OCR never reads. It is the
        # only non-cancel button, so it is the OK.
        window = self.FakeWindow([
            self.FakeControl("Button", "", (100, 250, 180, 275)),
            self.FakeControl("Button", "Cancel", (200, 250, 280, 275)),
        ])
        self.assertEqual(self._clicked(window), (140, 262))

    def test_named_non_accept_button_is_skipped(self):
        window = self.FakeWindow([
            self.FakeControl("Button", "OK", (100, 250, 180, 275)),
            self.FakeControl("Button", "Browse", (200, 250, 280, 275)),
        ])
        self.assertEqual(self._clicked(window), (140, 262))

    def test_non_button_controls_fall_back_to_the_bottom_row_geometry(self):
        # No Button children at all (some HBSys builds). The right-most
        # control in the dialog's own bottom band is pressed instead.
        window = self.FakeWindow([
            self.FakeControl("Static", "File save", (20, 30, 380, 50)),
            self.FakeControl("fntext", "", (240, 250, 320, 275)),
            self.FakeControl("Button", "Cancel", (140, 250, 220, 275)),
        ], rect=(0, 0, 400, 300))
        self.assertEqual(self._clicked(window), (280, 262))

    def test_control_above_the_bottom_band_is_not_pressed(self):
        # The file list sits above the button row; a wide control there must
        # never be treated as the OK.
        window = self.FakeWindow([
            self.FakeControl("Static", "listing", (20, 100, 380, 200)),
        ], rect=(0, 0, 400, 300))
        with self.assertRaises(RuntimeError):
            fb.answer_dialog_ok(window)

    def test_invisible_controls_are_ignored(self):
        window = self.FakeWindow([
            self.FakeControl("Button", "OK", (100, 250, 180, 275)),
            self.FakeControl("Button", "OK", (100, 100, 180, 125), False),
        ])
        self.assertEqual(self._clicked(window), (140, 262))


class RunnerTests(unittest.TestCase):
    """FinalBillRunner drives the whole sequence with injected primitives."""

    def test_happy_path_matches_the_live_2026_09_28_sequence(self):
        # Live operator sequence: Final Bill -> tick "Final" -> click OK ->
        # answer the post-OK prompt -> done. This branch shows the
        # "Call Administrator" Yes/No dialog, which is answered by CLICKING No.
        # Close Form was removed from the end of the flow (2026-10-02).
        fake = FakeHbsys()                      # Billing form open
        logs: list[str] = []
        result = make_runner(fake, log_fn=logs.append).run()

        self.assertTrue(result.success, result.reason)
        self.assertEqual(
            result.actions,
            [
                fb.STEP_OPEN_FINAL_BILL,
                fb.STEP_CHECK_FINAL,
                fb.STEP_CLICK_OK,
                fb.STEP_CONFIRM_NO,
                fb.STEP_DONE,
            ],
        )
        self.assertEqual(
            fake.calls,
            [
                "menu:Billing -> Final Bill",
                "check_final",
                "click_ok",
                "confirm_no",
            ],
        )
        self.assertTrue(fake.bill_finalized)
        # The Billing form is deliberately left open for the next patient.
        self.assertTrue(fake.form_open)
        self.assertEqual(result.final_step, fb.STEP_DONE)
        self.assertEqual(len(logs), len(result.actions))
        # "No" is clicked once on the confirm dialog, and the save prompt's
        # OK is never clicked on this branch.
        self.assertEqual(fake.calls.count("confirm_no"), 1)
        self.assertNotIn("save_click_ok", fake.calls)

    def test_save_prompt_branch_clicks_ok(self):
        # click_ok resolves to popup="save" instead of the confirm prompt,
        # exactly the alternate branch in the operator's manual flow. That
        # prompt is answered by CLICKING "OK" - no TAB, no ENTER, no SPACE,
        # so nothing can be mis-delivered to a window that is not in front.
        fake = FakeHbsys()
        original_click_ok = fake.click_ok

        def click_ok_save_branch() -> None:
            original_click_ok()
            fake.popup = "save"

        runner = make_runner(fake)
        runner.click_ok_fn = click_ok_save_branch
        result = runner.run()

        self.assertTrue(result.success, result.reason)
        self.assertIn(fb.STEP_SAVE_CLICK_OK, result.actions)
        self.assertNotIn(fb.STEP_CONFIRM_NO, result.actions)
        self.assertEqual(fake.calls[-1], "save_click_ok")
        self.assertTrue(fake.form_open)

    def test_already_finalized_with_open_form_finishes_immediately(self):
        # Resume case: a previous run committed the bill. The form is still
        # open and the run simply finishes - Close Form no longer exists.
        fake = FakeHbsys()
        result = make_runner(fake, initial_bill_finalized=True).run()

        self.assertTrue(result.success, result.reason)
        self.assertEqual(result.actions, [fb.STEP_DONE])
        self.assertEqual(fake.calls, [])
        self.assertTrue(fake.form_open)

    def test_the_runner_never_clicks_close_form(self):
        # Removed 2026-10-02: no patient gets a Close Form click, so the form
        # is left open for the next patient's loader to replace.
        fake = FakeHbsys()
        result = make_runner(fake, max_steps=8).run()

        self.assertTrue(result.success, result.reason)
        self.assertNotIn("close_form", fake.calls)
        self.assertEqual(result.final_step, fb.STEP_DONE)
        self.assertTrue(fake.form_open)

    def test_hbsys_closed_blocks_without_touching_the_gui(self):
        fake = FakeHbsys(form_open=False)
        # Force the closed-HBSys screen regardless of fake state.
        fake.detect = lambda: screens.SCREEN_HBSYS_CLOSED
        result = make_runner(fake).run()

        self.assertFalse(result.success)
        self.assertEqual(result.actions, [fb.STEP_BLOCKED])
        self.assertEqual(fake.calls, [])
        self.assertIn("CLOSED", result.reason)

    def test_missing_billing_form_blocks_as_human_step(self):
        fake = FakeHbsys(form_open=False)
        fake.detect = lambda: screens.SCREEN_ORDER_TRANSACTIONS
        result = make_runner(fake).run()

        self.assertFalse(result.success)
        self.assertEqual(result.actions, [fb.STEP_BLOCKED])
        self.assertIn("human step", result.reason)

    def test_primitive_failure_is_reported_not_retried(self):
        fake = FakeHbsys(broken_step=fb.STEP_CHECK_FINAL)
        result = make_runner(fake).run()

        self.assertFalse(result.success)
        self.assertIn("check_final_box failed", result.reason)
        self.assertIn("glyph boom", result.reason)
        # The failing step was recorded after the successful open, then stop.
        self.assertEqual(
            result.actions, [fb.STEP_OPEN_FINAL_BILL, fb.STEP_CHECK_FINAL]
        )
        self.assertNotIn("click_ok", fake.calls)

    def test_default_runner_binds_real_gui_primitives(self):
        # Construct-only (headless-safe): the no-injection path must resolve
        # every default primitive — this broke once when an edit dropped the
        # static methods, so it is pinned here.
        runner = fb.FinalBillRunner()
        for name in (
            "_detect_screen",
            "_menu_select",
            "_check_final",
            "_click_ok",
            "_save_click_ok",
            "_confirm_no",
        ):
            self.assertTrue(callable(getattr(runner, name)), name)
        # The retired keyboard-based answers must not come back: the save
        # prompt is a click, and the confirm prompt is a single TAB - not
        # TAB-then-ENTER and not a blind ENTER.
        self.assertFalse(hasattr(runner, "_answer_no"))
        self.assertFalse(hasattr(runner, "_answer_ok"))
        self.assertFalse(hasattr(runner, "_press_enter"))
        self.assertFalse(hasattr(runner, "_save_tab_enter"))
        self.assertIs(runner.detect_fn, fb.FinalBillRunner._detect_screen)
        # Removed 2026-10-02: the runner must not carry a close_form hook at
        # all, so no code path can reach the shared Close Form toolbar band.
        self.assertFalse(hasattr(runner, "close_form_fn"))

    def test_a_stuck_step_fails_fast_and_names_itself(self):
        # 2026-09-29 12:43 run: a step that never changed the screen burned the
        # whole step budget and the report only said "did not finish within 12
        # steps". The no-progress guard now stops on the 3rd repeat and names
        # the step, the screen it was stuck on, and the step trail - all of it
        # inside the FAILED detail the run report keeps.
        fake = FakeHbsys()
        logs: list = []

        def stuck_menu(path: str) -> None:
            fake.calls.append(f"menu:{path}")   # never opens the popup

        runner = make_runner(fake, max_steps=5, log_fn=logs.append)
        runner.menu_fn = stuck_menu
        result = runner.run()

        self.assertFalse(result.success)
        self.assertIn(fb.STEP_OPEN_FINAL_BILL, result.reason)
        self.assertIn("without changing it", result.reason)
        self.assertIn(
            f"{fb.SAME_STEP_REPEAT_LIMIT} times in a row", result.reason
        )
        self.assertIn(
            f"steps: {fb.STEP_OPEN_FINAL_BILL} x{fb.SAME_STEP_REPEAT_LIMIT}",
            result.reason,
        )
        # it stops early - the remaining steps are not wasted on a screen that
        # is never going to change
        self.assertEqual(len(result.steps), fb.SAME_STEP_REPEAT_LIMIT)
        self.assertTrue(
            any(line.startswith("final bill failure:") for line in logs),
            logs,
        )

    def test_step_cap_stops_a_flow_whose_steps_keep_changing(self):
        # Alternating steps never trip the no-progress guard, so the step cap
        # stays the backstop - and its reason now carries the trail too, which
        # is what makes a "did not finish" row diagnosable after the fact.
        fake = FakeHbsys()
        tick = {"n": 0}

        def alternating_detect() -> str:
            tick["n"] += 1
            return (
                screens.SCREEN_ORDER_TRANSACTIONS
                if tick["n"] % 2
                else screens.SCREEN_FINAL_BILL_OPTIONS
            )

        fake.detect = alternating_detect
        runner = make_runner(fake, max_steps=6)
        runner.check_final_fn = lambda: False  # the box never reads as ticked
        result = runner.run()

        self.assertFalse(result.success)
        self.assertIn("did not finish within 6 steps", result.reason)
        self.assertIn(fb.STEP_OPEN_FINAL_BILL, result.reason)
        self.assertIn(fb.STEP_CHECK_FINAL, result.reason)
        self.assertEqual(len(result.steps), 6)

    def test_trace_of_collapses_consecutive_repeats(self):
        self.assertEqual(
            fb.trace_of(
                [fb.STEP_OPEN_FINAL_BILL, fb.STEP_OPEN_FINAL_BILL,
                 fb.STEP_OPEN_FINAL_BILL, fb.STEP_CHECK_FINAL]
            ),
            f"{fb.STEP_OPEN_FINAL_BILL} x3 -> {fb.STEP_CHECK_FINAL}",
        )
        self.assertEqual(fb.trace_of([]), "")

    def test_a_stuck_bill_form_cannot_fail_the_row_anymore(self):
        # Removed 2026-10-02: the row no longer has a Close Form step, so a
        # Billing form that stays open is a normal finish, not a failure.
        fake = FakeHbsys()
        runner = make_runner(fake, initial_bill_finalized=True)
        result = runner.run()

        self.assertTrue(result.success, result.reason)
        self.assertEqual(result.final_step, fb.STEP_DONE)
        self.assertTrue(fake.form_open)


class AdmitHistoryTests(unittest.TestCase):
    """Admit History popup helpers - pure parsing is tested headless."""

    def test_date_token_recognises_the_grid_format(self):
        self.assertTrue(fb._is_date_token("09/05/2026"))
        self.assertFalse(fb._is_date_token("09:55"))
        self.assertFalse(fb._is_date_token("ADMIT"))
        self.assertFalse(fb._is_date_token("09/05/26"))

    def test_date_to_yyyymmdd_matches_the_folder_shape(self):
        # the folder name carries ADMYYYYMMDD_DISYYYYMMDD, the grid MM/DD/YYYY
        self.assertEqual(fb._date_to_yyyymmdd("09/05/2026"), "20260905")
        self.assertEqual(fb._date_to_yyyymmdd("12/31/2026"), "20261231")
        self.assertEqual(fb._date_to_yyyymmdd("garbage"), "")

    def test_yyyymmdd_to_grid_matches_the_datefill_shape(self):
        # select_confinement converts the folder YYYYMMDD back to the
        # MM/DD/YYYY grid shape Date Fill matches (admission_grid_key)
        self.assertEqual(fb._yyyymmdd_to_grid("20260905"), "09/05/2026")
        self.assertEqual(fb._yyyymmdd_to_grid("20261231"), "12/31/2026")
        self.assertEqual(fb._yyyymmdd_to_grid("garbage"), "")
        self.assertEqual(fb._yyyymmdd_to_grid(""), "")

    def test_admit_history_tooltip_matches_the_live_ocr_noise(self):
        self.assertTrue(fb._tooltip_matches_admit_history("Admit History"))
        self.assertTrue(fb._tooltip_matches_admit_history("Sdmit Histor"))
        self.assertFalse(fb._tooltip_matches_admit_history("Close Form"))
        self.assertFalse(fb._tooltip_matches_admit_history(""))
        self.assertFalse(fb._tooltip_matches_admit_history("Adjustment"))

    def test_select_confinement_rejects_bad_dates_before_touching_gui(self):
        # no HBSys here - the guard must fire before any window is opened
        self.assertFalse(fb.select_confinement("", ""))
        self.assertFalse(fb.select_confinement("20260901", "260901"))
        self.assertFalse(fb.select_confinement("2026-09-01", "2026-09-03"))


class ConfinementRowMergeTests(unittest.TestCase):
    """Multi-pass Admission History OCR -> ConfinementRow (pure, headless).

    Live 2026-09-28 13:40: the popup really held PERA 09/09/2026-09/12/2026
    (agent_diag_20260928_134039.png) yet the single full-screen grab read zero
    rows and BLOCKed. These lock the two pieces the multi-pass reader depends
    on: a misreading pass must not hide the correct row, and the click point
    must be measured from the popup's TOP-left (the old full-screen crop put
    it on the toolbar, so the double-click never selected anything).
    """

    @classmethod
    def setUpClass(cls):
        # the Date Fill tool lives in date_fill_hbsys/ and is only importable
        # after final_bill_actions puts that directory on sys.path
        if not fb._ensure_date_fill_imports():
            raise unittest.SkipTest("Date Fill admission-history tool unavailable")

    @staticmethod
    def _ocr_items(admission: str, discharge: str, y: float = 101.0):
        """One grid row worth of OCR items: admission, discharge, ADMIT."""
        from hbsys_read_admission_history import OcrItem

        return [
            OcrItem(admission, 90.0, 60.0, y, width=52.0, height=12.0),
            OcrItem(discharge, 90.0, 320.0, y, width=52.0, height=12.0),
            OcrItem("ADMIT", 90.0, 560.0, y, width=40.0, height=12.0),
        ]

    def test_a_misreading_pass_cannot_hide_the_correct_row(self):
        # pass 0 invented a '2' for the '6'; pass 1 read the real date. Both
        # keys are distinct, so the merge must keep BOTH and let the exact
        # matcher pick the right one (verified live on the 13:40 screenshot).
        merged = fb._merge_parsed_rows(
            [
                self._ocr_items("09/09/2028", "09/12/2026"),
                self._ocr_items("09/09/2026", "09/12/2026"),
            ]
        )
        pairs = [(p.row.admission_date, p.row.discharge_date) for p in merged]
        self.assertIn(("09/09/2028", "09/12/2026"), pairs)
        self.assertIn(("09/09/2026", "09/12/2026"), pairs)

    def test_identical_rows_from_many_passes_are_merged_once(self):
        # 4 focused per-row passes all read the same stay - they must not
        # produce four rows (the old code double-clicked the same cell twice).
        merged = fb._merge_parsed_rows(
            [self._ocr_items("09/09/2026", "09/12/2026")] * 4
        )
        self.assertEqual(len(merged), 1)

    def test_row_keeps_the_grid_text_and_encounter_type(self):
        merged = fb._merge_parsed_rows(
            [self._ocr_items("09/09/2026", "09/12/2026")]
        )
        self.assertEqual(merged[0].row.admission_date, "09/09/2026")
        self.assertEqual(merged[0].row.discharge_date, "09/12/2026")
        self.assertEqual(merged[0].row.normalized_encounter_type, "ADMIT")

    def test_click_point_is_measured_from_the_popups_top_left_corner(self):
        # popup window at (122, 110); grid row 101px below its top edge and
        # the first date column centred ~72px from its left edge.
        class _Rect:
            left, top, right, bottom = 122, 110, 767, 491

        class _Popup:
            def rectangle(self):
                return _Rect()

        merged = fb._merge_parsed_rows(
            [self._ocr_items("09/09/2026", "09/12/2026", y=101.0)]
        )
        row = fb._confinement_row_from_parsed(_Popup(), merged[0])
        self.assertIsNotNone(row)
        self.assertEqual(row.point, (194, 211))
        self.assertEqual(row.admission_grid, "09/09/2026")
        self.assertEqual(row.discharge_grid, "09/12/2026")

    def test_unreadable_ocr_passes_yield_no_rows_instead_of_raising(self):
        # a pass that lost both dates must not crash the merge
        from hbsys_read_admission_history import OcrItem

        self.assertEqual(
            fb._merge_parsed_rows([[OcrItem("ADMIT", 40.0, 60.0, 101.0)]]), []
        )

    def test_merge_survives_a_broken_datefill_import(self):
        with mock.patch.object(fb, "_ensure_date_fill_imports", return_value=False):
            self.assertEqual(fb._merge_parsed_rows([[]]), [])


class PatientLookupTests(unittest.TestCase):
    """Multi-patient transition helpers (Hospital No. lookup + guarded close)."""

    def test_hospital_no_point_is_date_fills_verified_slot(self):
        # One source of truth: date_fill_hbsys P.HOSPITAL_NO is the slot the
        # Date Fill tool double-clicks for every patient in its batch.
        self.assertEqual(fb.HOSPITAL_NO_POINT, (166, 174))
        try:
            from date_fill_hbsys.hbsys_fill_dates import P
        except Exception as exc:  # pragma: no cover - pyautogui missing
            self.skipTest(f"Date Fill not importable: {exc}")
        self.assertEqual((P.HOSPITAL_NO.x, P.HOSPITAL_NO.y), fb.HOSPITAL_NO_POINT)

    def test_hospital_no_edit_id_is_the_probed_control(self):
        # Verified live by the Slice D probe: Edit 1004 on the Billing form.
        self.assertEqual(fb.HOSPITAL_NO_EDIT_ID, 1004)

    def test_billing_form_titles_only_keeps_the_patient_form(self):
        titles = ["Billing (DELA CRUZ, JUAN)", "User Menu", "PhilHealth"]
        self.assertEqual(
            fb.billing_form_titles(titles), ["Billing (DELA CRUZ, JUAN)"]
        )
        self.assertEqual(fb.billing_form_titles(["User Menu"]), [])
        self.assertEqual(fb.billing_form_titles(None), [])

    def test_billing_form_title_for_patient(self):
        self.assertEqual(
            fb.billing_form_for_patient("DELA CRUZ, JUAN"),
            "Billing (DELA CRUZ, JUAN)",
        )
        self.assertEqual(fb.billing_form_for_patient(""), "")
        self.assertEqual(fb.billing_form_for_patient(None), "")

    def test_print_options_ok_is_never_pressed_twice(self):
        # Live 2026-10-02: the Print Options window stayed on screen behind the
        # "File save" note, so the screen never changed and OK was pressed 3x
        # before SAME_STEP_REPEAT_LIMIT failed the row.
        decision = fb.plan_step(
            fb.screens.SCREEN_FINAL_BILL_OPTIONS,
            final_checked=True,
            bill_finalized=False,
            ok_clicked=True,
            prompt_answered=False,
        )
        self.assertEqual(decision.step, fb.STEP_BLOCKED)
        self.assertIn("never clicked twice", decision.reason)

    def test_print_options_ok_still_clicked_when_not_clicked_yet(self):
        decision = fb.plan_step(
            fb.screens.SCREEN_FINAL_BILL_OPTIONS,
            final_checked=True,
            bill_finalized=False,
            ok_clicked=False,
            prompt_answered=False,
        )
        self.assertEqual(decision.step, fb.STEP_CLICK_OK)

    def test_click_print_options_ok_verifies_the_popup_left(self):
        # The one click must be confirmed, otherwise a swallowed click looks
        # identical to a successful one and the planner presses OK again.
        clicks = []

        class FakePopup:
            def __init__(self, stays_open):
                self.stays_open = stays_open

            def is_visible(self):
                return self.stays_open

        with mock.patch.object(
            fb, "_mapped_click_point", return_value=(10, 20)
        ), mock.patch.object(
            fb, "raise_to_top"
        ), mock.patch.object(
            fb, "real_click", side_effect=lambda x, y: clicks.append((x, y))
        ), mock.patch.object(
            fb, "POPUP_CLOSE_TIMEOUT_SECONDS", 0.2
        ), mock.patch.object(
            fb, "POPUP_CLOSE_POLL_SECONDS", 0.01
        ):
            gone = fb.click_print_options_ok(FakePopup(False))
            stuck = fb.click_print_options_ok(FakePopup(True))
        self.assertTrue(gone)
        self.assertFalse(stuck)
        self.assertEqual(len(clicks), 2)

    def test_plan_reason_mismatch_is_reported_but_the_screen_wins(self):
        # The reason PREDICTS the prompt; detection decides. A disagreement is
        # surfaced in the run report, never silently acted on.
        expected = fb.screens.SCREEN_FILE_SAVE
        same = fb.plan_step(
            fb.screens.SCREEN_FILE_SAVE,
            expected_screen=expected,
            final_checked=True,
            ok_clicked=True,
        )
        self.assertNotIn("note:", same.reason)
        self.assertEqual(same.step, fb.STEP_SAVE_CLICK_OK)

        other = fb.plan_step(
            fb.screens.SCREEN_FINAL_BILL_CONFIRM,
            expected_screen=expected,
            final_checked=True,
            ok_clicked=True,
        )
        self.assertEqual(other.step, fb.STEP_CONFIRM_NO)
        self.assertIn("note:", other.reason)
        self.assertIn(fb.screens.SCREEN_FILE_SAVE, other.reason)

    def test_runner_carries_the_expected_prompt_from_the_plan(self):
        # _default_final_bill must hand the plan's predicted prompt to the
        # runner instead of guessing which prompt HBSys will raise.
        from types import SimpleNamespace

        from core.agent import orchestrator as orch

        calls = []

        def capture(**kwargs):
            calls.append(kwargs.get("expected_screen"))
            return SimpleNamespace(
                success=True, reason="done", final_step="done"
            )

        status, _detail = orch._default_final_bill(
            "DELA CRUZ, JUAN - 123456789012345 - ADM20260901_DIS20260903",
            "123456789012345",
            lambda message: None,
            30,
            forms_fn=lambda: ["Billing (DELA CRUZ, JUAN)", "User Menu"],
            runner_fn=capture,
            confinement_fn=lambda admission, discharge: True,
            expected_screen=fb.screens.SCREEN_FILE_SAVE,
        )
        self.assertEqual(status, orch.OUTCOME_OK)
        self.assertEqual(calls, [fb.screens.SCREEN_FILE_SAVE])

    def test_normalize_billing_title_collapses_inner_whitespace(self):
        # The pad is dropped on BOTH sides of the brackets, so the canonical
        # form is "Billing(NAME)" - compare titles with billing_title_key().
        self.assertEqual(
            fb.normalize_billing_title("Billing (VALENTINO, NIKKI )"),
            "Billing(VALENTINO, NIKKI)",
        )
        self.assertEqual(
            fb.normalize_billing_title("  Billing (DELA CRUZ,   JUAN) "),
            "Billing(DELA CRUZ, JUAN)",
        )
        self.assertEqual(fb.normalize_billing_title(""), "")
        self.assertEqual(fb.normalize_billing_title(None), "")

    def test_is_billing_title_matches_both_padded_and_clean(self):
        # "User Menu"/"PhilHealth" must stay excluded, and the pad on "(" must
        # not stop the per-patient form from being recognized.
        for title in (
            "Billing (VALENTINO, NIKKI )",
            "Billing (VALENTINO, NIKKI)",
            "  Billing(VALENTINO, NIKKI)  ",
        ):
            self.assertTrue(fb.is_billing_title(title), title)
        for title in ("User Menu", "PhilHealth", "Billing", ""):
            self.assertFalse(fb.is_billing_title(title), title)
        self.assertFalse(fb.is_billing_title(None))

    def test_billing_title_key_ignores_case_and_spacing(self):
        self.assertEqual(
            fb.billing_title_key("Billing (VALENTINO, NIKKI )"),
            fb.billing_title_key("billing (valentino,  nikki)"),
        )
        self.assertEqual(
            fb.billing_title_key(fb.billing_form_for_patient("VALENTINO, NIKKI ")),
            fb.billing_title_key("Billing (VALENTINO, NIKKI )"),
        )
        self.assertNotEqual(
            fb.billing_title_key("Billing (VALENTINO, NIKKI )"),
            fb.billing_title_key("Billing (SANTOS, MARIA)"),
        )

    def test_close_billing_form_accepts_the_trailing_space_title(self):
        # It must not refuse the patient's OWN form just because HBSys wrote
        # "Billing (VALENTINO, NIKKI )" with a space before the ")".
        responses = [
            ["Billing (VALENTINO, NIKKI )", "User Menu"],   # before the click
            [],                                             # after the click
        ]
        calls = {"n": 0}

        def fake_open():
            index = min(calls["n"], len(responses) - 1)
            calls["n"] += 1
            return list(responses[index])

        with mock.patch.object(
            fb, "list_open_forms", side_effect=fake_open
        ), mock.patch.object(
            fb, "click_close_form", return_value="Close Form"
        ) as clicker:
            closed = fb.close_billing_form(
                "VALENTINO, NIKKI", 0.1, verify=False, log_fn=None
            )
        self.assertTrue(closed)
        self.assertEqual(clicker.call_count, 1)

    def test_close_billing_form_still_refuses_another_patient(self):
        responses = [["Billing (SANTOS, MARIA )", "User Menu"]]

        def fake_open():
            return list(responses[0])

        with mock.patch.object(
            fb, "list_open_forms", side_effect=fake_open
        ), mock.patch.object(fb, "click_close_form") as clicker:
            closed = fb.close_billing_form(
                "VALENTINO, NIKKI", 0.1, verify=False, log_fn=None
            )
        self.assertFalse(closed)
        clicker.assert_not_called()

    def test_load_rejects_bad_hospital_numbers_before_touching_the_gui(self):
        # No HBSys here: the guard must fire first (never type garbage).
        notes = []
        log = notes.append
        self.assertFalse(fb.load_patient_by_hospital_no("", log_fn=log))
        self.assertFalse(fb.load_patient_by_hospital_no("   ", log_fn=log))
        self.assertFalse(fb.load_patient_by_hospital_no("ABC123", log_fn=log))
        self.assertTrue(all("load:" in line for line in notes))

    def test_load_never_clicks_close_form(self):
        # The Final Bill flow never clicks Close Form. The loader was the last
        # place that could do it, so it is pinned here: a Billing form that was
        # already open is left alone, and the load is only accepted when a
        # Billing form that was NOT open before appears.
        calls = {"n": 0}

        def fake_forms():
            calls["n"] += 1
            if calls["n"] == 1:
                return ["Billing (SANTOS, MARIA )"]
            return ["Billing (SANTOS, MARIA )", "Billing (VALENTINO, NIKKI)"]

        with mock.patch.object(
            fb, "list_open_forms", side_effect=fake_forms
        ), mock.patch.object(
            fb, "find_hbsys_main", return_value=object()
        ), mock.patch.object(
            fb, "raise_to_top", return_value=True
        ), mock.patch.object(
            fb, "clear_blocking_popups", return_value=True
        ), mock.patch.object(
            fb, "hospital_no_click_point", return_value=(166, 174)
        ), mock.patch.object(
            fb, "hospital_no_field_text", return_value="000000000123456"
        ), mock.patch.object(
            fb, "close_billing_form"
        ) as closer, mock.patch.object(
            fb, "click_close_form"
        ) as clicker, mock.patch(
            "pywinauto.mouse.double_click"
        ), mock.patch(
            "pywinauto.keyboard.send_keys"
        ):
            loaded = fb.load_patient_by_hospital_no(
                "000000000123456", timeout=1.0
            )

        self.assertTrue(loaded)
        closer.assert_not_called()
        clicker.assert_not_called()
        closer.assert_not_called()
        clicker.assert_not_called()

    # -- Slice H: relink verification ------------------------------------

    def _run_load(self, forms_sequence, **kwargs):
        """Drive the loader with a scripted list_open_forms sequence."""
        calls = {"n": 0}

        def fake_forms():
            calls["n"] += 1
            index = min(calls["n"] - 1, len(forms_sequence) - 1)
            return list(forms_sequence[index])

        patches = [
            mock.patch.object(fb, "list_open_forms", side_effect=fake_forms),
            mock.patch.object(fb, "find_hbsys_main", return_value=object()),
            mock.patch.object(fb, "raise_to_top", return_value=True),
            mock.patch.object(fb, "clear_blocking_popups", return_value=True),
            mock.patch.object(fb, "hospital_no_click_point", return_value=(166, 174)),
            mock.patch.object(fb, "hospital_no_field_text", return_value="000000000123456"),
            mock.patch.object(fb, "close_billing_form"),
            mock.patch.object(fb, "click_close_form"),
            mock.patch.object(fb, "diagnose_screen", return_value=""),
            mock.patch("pywinauto.mouse.double_click"),
            mock.patch("pywinauto.keyboard.send_keys"),
        ]
        for patch in patches:
            patch.start()
        try:
            return fb.load_patient_by_hospital_no("000000000123456", **kwargs)
        finally:
            for patch in reversed(patches):
                patch.stop()

    def test_new_form_mode_still_refuses_when_no_window_appears(self):
        """Regression: the default mode is unchanged - only a NEW form counts."""
        self.assertFalse(self._run_load([["User Menu"]], timeout=0.4))

    def test_relink_mode_accepts_the_already_open_form_of_this_patient(self):
        # Re-typing the same Hospital No. can REFRESH the same window instead of
        # opening a second one. That is still a correct load, and in "new_form"
        # mode it would time out and block a row that is actually fine.
        notes = []
        loaded = self._run_load(
            [["Billing (SANTOS, MARIA )"], ["Billing (SANTOS, MARIA )"]],
            timeout=0.4,
            verify_mode=fb.LOAD_VERIFY_RELINK,
            expect_title="Billing (SANTOS, MARIA)",
            log_fn=notes.append,
        )
        self.assertTrue(loaded)
        self.assertTrue(any("relink" in line for line in notes))

    def test_relink_mode_refuses_a_form_belonging_to_another_patient(self):
        # Safety net: another patient's window never satisfies this mode.
        self.assertFalse(
            self._run_load(
                [["Billing (VALENTINO, NIKKI)"], ["Billing (VALENTINO, NIKKI)"]],
                timeout=0.4,
                verify_mode=fb.LOAD_VERIFY_RELINK,
                expect_title="Billing (SANTOS, MARIA)",
            )
        )

    def test_relink_mode_refuses_when_another_form_is_open_beside_it(self):
        # Two windows open and only one is this patient = ambiguous, block.
        self.assertFalse(
            self._run_load(
                [
                    ["Billing (SANTOS, MARIA )", "Billing (VALENTINO, NIKKI)"],
                    ["Billing (SANTOS, MARIA )", "Billing (VALENTINO, NIKKI)"],
                ],
                timeout=0.4,
                verify_mode=fb.LOAD_VERIFY_RELINK,
                expect_title="Billing (SANTOS, MARIA)",
            )
        )

    def test_relink_matches_the_title_with_the_trailing_space(self):
        # HBSys titles the missing-middle-name form "Billing (SANTOS, MARIA )".
        loaded = self._run_load(
            [
                ["Billing (SANTOS, MARIA )"],
                ["Billing (SANTOS, MARIA )"],
            ],
            timeout=0.4,
            verify_mode=fb.LOAD_VERIFY_RELINK,
            expect_title="Billing (SANTOS, MARIA )",
        )
        self.assertTrue(loaded)

    def test_relink_mode_without_expect_title_falls_back_to_new_form(self):
        # No title to match: never guess, wait for a genuinely new window.
        self.assertFalse(
            self._run_load(
                [["Billing (SANTOS, MARIA )"], ["Billing (SANTOS, MARIA )"]],
                timeout=0.4,
                verify_mode=fb.LOAD_VERIFY_RELINK,
                expect_title="",
            )
        )

    def test_guarded_close_refuses_when_no_billing_form_is_open(self):
        # The shared toolbar band is live on the main screen too; with no
        # Billing form open the close must be a no-op (False), never a click.
        # ("Billing" alone is a workspace marker, not a patient form.)
        # The form list is stubbed so the verdict does not depend on whichever
        # patient the live HBSys session happens to have open.
        notes = []
        log = notes.append
        original = fb.list_open_forms
        try:
            fb.list_open_forms = lambda: ["User Menu"]
            self.assertFalse(fb.close_billing_form(log_fn=log))
            fb.list_open_forms = lambda: ["Billing (OTHER, PATIENT)"]
            self.assertFalse(fb.close_billing_form("DELA CRUZ, JUAN", log_fn=log))
        finally:
            fb.list_open_forms = original
        self.assertEqual(fb.billing_form_titles(["Billing", "User Menu"]), [])
        self.assertTrue(any("refusing to click the toolbar" in line for line in notes))

    def test_popup_clear_is_harmless_without_a_display(self):
        # No HBSys here: clearing must be a no-op with evidence, not a click.
        # The two screen probes are stubbed so the result does not depend on
        # whether an HBSys session happens to be open on this desktop.
        notes = []
        log = notes.append
        originals = (fb.find_admission_history, fb._dismiss_rate_validation_dialog)
        fb.find_admission_history = lambda *args, **kwargs: None
        fb._dismiss_rate_validation_dialog = lambda *args, **kwargs: True
        try:
            self.assertTrue(fb.clear_blocking_popups(log_fn=log))
        finally:
            fb.find_admission_history, fb._dismiss_rate_validation_dialog = originals
        self.assertTrue(any("dialog" in line.lower() for line in notes))

    def test_unknown_dialog_refuses_and_names_the_windows(self):
        # An unknown modal blocker must stop the row AND say what was on
        # screen - "nothing happened" is not a diagnosis.
        notes = []
        log = notes.append
        originals = (
            fb.find_admission_history,
            fb._dismiss_rate_validation_dialog,
            fb._rate_validation_open,
            fb.diagnose_screen,
        )
        fb.find_admission_history = lambda *args, **kwargs: None
        fb._dismiss_rate_validation_dialog = lambda *args, **kwargs: False
        fb._rate_validation_open = lambda *args, **kwargs: True
        fb.diagnose_screen = lambda context, **kwargs: (
            f"{context}: diagnostics\n  windows: Rate validation [#32770]"
        )
        try:
            self.assertFalse(fb.clear_blocking_popups(log_fn=log))
        finally:
            (
                fb.find_admission_history,
                fb._dismiss_rate_validation_dialog,
                fb._rate_validation_open,
                fb.diagnose_screen,
            ) = originals
        self.assertTrue(any("unknown dialog" in line for line in notes))
        self.assertTrue(any("Rate validation" in line for line in notes))


class ScreenDiagnosticsTests(unittest.TestCase):
    """`diagnose_screen` answers "what was on screen" without clicking."""

    def test_diagnose_screen_reports_forms_windows_field_and_shot(self):
        originals = (
            fb.list_open_forms,
            fb.billing_form_titles,
            fb.hbsys_window_titles,
            fb.hospital_no_field_text,
            fb.save_screenshot,
        )
        fb.list_open_forms = lambda: ["Billing (DELA CRUZ, JUAN)"]
        fb.billing_form_titles = lambda titles: ["Billing (DELA CRUZ, JUAN)"]
        fb.hbsys_window_titles = lambda: ["Rate validation [#32770]"]
        fb.hospital_no_field_text = lambda: "000000000008144"
        fb.save_screenshot = lambda prefix="agent_diag": "logs/agent_diag_x.png"
        try:
            report = fb.diagnose_screen("load: after ENTER")
        finally:
            (
                fb.list_open_forms,
                fb.billing_form_titles,
                fb.hbsys_window_titles,
                fb.hospital_no_field_text,
                fb.save_screenshot,
            ) = originals
        self.assertIn("Billing (DELA CRUZ, JUAN)", report)
        self.assertIn("Rate validation", report)
        self.assertIn("000000000008144", report)
        self.assertIn("logs/agent_diag_x.png", report)

    def test_diagnose_screen_survives_a_broken_screen_probe(self):
        def boom():
            raise RuntimeError("no desktop")

        originals = (
            fb.list_open_forms,
            fb.hbsys_window_titles,
            fb.hospital_no_field_text,
        )
        fb.list_open_forms = boom
        fb.hbsys_window_titles = lambda: []
        fb.hospital_no_field_text = lambda: ""
        try:
            report = fb.diagnose_screen("load: after ENTER", screenshot=False)
        finally:
            (
                fb.list_open_forms,
                fb.hbsys_window_titles,
                fb.hospital_no_field_text,
            ) = originals
        self.assertIn("unreadable", report)
        self.assertIn("<empty/unreadable>", report)
        self.assertNotIn("screenshot:", report)


class FuzzyConfinementTests(unittest.TestCase):
    """The OCR-tolerant confinement match Date Fill and Final Bill share."""

    @classmethod
    def setUpClass(cls):
        cls.row_class = fb._confinement_row_class()
        if cls.row_class is None:  # pragma: no cover - package missing
            raise unittest.SkipTest("date_fill_hbsys matcher unavailable")

    def make_row(self, admission, discharge, encounter="ADMIT", point=(300, 211)):
        return self.row_class(admission, discharge, point, encounter)

    def test_ocr_date_match_score_thresholds(self):
        score = fb.ocr_date_match_score
        self.assertEqual(score("09/05/2026", "09/05/2026"), 100)
        self.assertEqual(score("09/05/2025", "09/05/2026"), 80)
        self.assertEqual(score("09/08/2026", "09/05/2026"), 62)
        self.assertEqual(score("09/25/2026", "09/05/2026"), 25)
        self.assertEqual(score("09/20/2019", "09/05/2026"), 20)
        self.assertEqual(score("01/05/2026", "09/05/2026"), 0)
        self.assertEqual(score("O1/0l/2026", "09/05/2026"), 0)
        self.assertEqual(score("", "09/05/2026"), 0)

    def test_fuzzy_pick_prefers_the_exact_row(self):
        rows = [
            self.make_row("10/01/2026", "10/03/2026", "OPD", (300, 238)),
            self.make_row("09/05/2026", "09/07/2026", "ADMIT", (300, 211)),
        ]
        picked = fb.fuzzy_confinement_row(rows, "09/05/2026", "09/07/2026")
        self.assertIsNotNone(picked)
        self.assertEqual(picked.point, (300, 211))

    def test_fuzzy_pick_tolerates_a_misread_day(self):
        rows = [self.make_row("09/06/2026", "09/08/2026", "ADMIT")]
        picked = fb.fuzzy_confinement_row(rows, "09/05/2026", "09/07/2026")
        self.assertIsNotNone(picked)
        self.assertEqual(picked.admission_grid, "09/06/2026")

    def test_fuzzy_gives_up_when_rows_are_too_close_to_call(self):
        rows = [
            self.make_row("09/05/2026", "09/07/2026", "ADMIT", (300, 211)),
            self.make_row("09/05/2026", "09/07/2026", "ADMIT", (300, 238)),
        ]
        self.assertIsNone(
            fb.fuzzy_confinement_row(rows, "09/05/2026", "09/07/2026")
        )

    def test_fuzzy_gives_up_below_the_score_floor(self):
        rows = [self.make_row("01/02/2019", "01/04/2019", "OPD")]
        self.assertIsNone(
            fb.fuzzy_confinement_row(rows, "09/05/2026", "09/07/2026")
        )
        self.assertIsNone(fb.fuzzy_confinement_row([], "09/05/2026", "09/07/2026"))

    def test_admit_encounter_bonus_can_cross_the_score_floor(self):
        # 80 (same month/day, misread year) + 25 (same month/year, day off by
        # 15) = 105: below the floor on its own, while the ADMIT bonus Date
        # Fill grants lands it exactly on 115 - the line is the same in both
        # tools, so the same row is accepted and rejected identically.
        admit = [self.make_row("09/05/2025", "09/22/2026", "ADMIT")]
        opd = [self.make_row("09/05/2025", "09/22/2026", "OPD")]
        picked = fb.fuzzy_confinement_row(admit, "09/05/2026", "09/07/2026")
        self.assertIsNotNone(picked)
        self.assertEqual(picked.encounter_type, "ADMIT")
        self.assertIsNone(fb.fuzzy_confinement_row(opd, "09/05/2026", "09/07/2026"))
        self.assertEqual(fb.FUZZY_ROW_MIN_SCORE, 115)
        self.assertEqual(fb.FUZZY_ROW_MIN_MARGIN, 25)


if __name__ == "__main__":
    unittest.main(verbosity=2)
