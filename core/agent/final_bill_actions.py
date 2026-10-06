"""HBSys Final Bill Actions (Slice D — Claims Agent, Phase 1 core).

Deterministic Final Bill workflow: the exact controls, dialogs and click
mechanics that were verified LIVE against HBSys on 2026-09-25 (HBSys PID
16640 / 22008, patient Hospital No. 000000000021401, Billing form open).

Manual sequence given by the operator (translated 1:1 into steps):

    1. type the Hospital No. in the Billing form lookup, press Enter
    2. Admit History  -> pick the right confinement period
    3. double click the right confinement (loads it into the form)
    4. Billing -> Final Bill            [STEP_OPEN_FINAL_BILL]
       -> "Print Options" popup (FNWNS370) opens
    5. tick the checkbox beside the word "Final"  [STEP_CHECK_FINAL]
    6. click OK                          [STEP_CLICK_OK]
    7. answer the prompt that popped up (ONE of the two, by screen):
         "File save"            -> click "OK"   (the one at the BOTTOM)
                                        [STEP_SAVE_CLICK_OK]
         "Call Administrator"   -> click "No"   (never Yes)
                                        [STEP_CONFIRM_NO]
       -> the bill IS FINAL
    8. -> STEP_DONE
       -> operator instruction: after the prompt the Billing form STAYS OPEN.
          The next patient's loader only types the next Hospital No. into it
          and verifies the new "Billing (<patient>)" window.

    Step 7 is decided by which prompt is on screen, and each is answered with
    the MOUSE, the way the operator answers it (2026-09-28): "OK" is clicked on
    the save prompt (the one in the button row at the BOTTOM of the dialog) and
    "No" is clicked on the "Call Administrator" dialog. Nothing here depends on
    tab order or on ENTER accepting whatever happens to be focused. Each dialog
    is raised to the foreground before its answer is sent, so a prompt that is
    not in front can never be answered by accident, and each answer is verified
    to have closed its own dialog.

Verified control evidence (absolute 1920x1080 coordinates, HBSys maximized):

    Print Options popup        FNWNS370  rect (256, 166, 525, 409)
      Button "Final"  id 1001  rect (333, 325, 421, 350)
          the checkbox glyph is drawn at the button's LEFT edge; the
          toggle point used live is (left + 7, top + 12) = (340, 337)
      Button "OK"     id 1002  rect (331, 357, 410, 384)
    Call Administrator prompt  #32770   rect (761, 471, 1165, 620)
      Static text: "File exist in Patient Charges Detail.. do you want
                    to replace the Computation?"
      Button "&Yes"   id 6     rect (988, 585, 1063, 608)
      Button "&No"    id 7     rect (1072, 585, 1147, 608)   <-- click this
    Close Form toolbar slot    FNFIXEDBAR70, row 2, (548, 97)
      resolved by OCR-ing the PowerBuilder tooltip: "Close Form"
      Evidence / manual probe only - the Final Bill flow never clicks it.

Mechanics that mattered live (do not "simplify" these away):

    * BM_CLICK does NOT dismiss the PowerBuilder modal, and a real mouse
      click misses it whenever another app (IDE/terminal) is on top.
      The working recipe is: raise the window (SetWindowPos HWND_TOPMOST,
      SWP_NOACTIVATE) -> verify WindowFromPoint is the button -> real click.
    * The "Final" box is NOT a Windows checkbox (style has no BS_CHECKBOX
      bit and BM_GETCHECK always returns 0). Its state is verified from the
      glyph pixels: dark-pixel ratio increases when ticked, and the tick
      pattern is logged as ASCII evidence.
    * The toolbar has no child controls: a slot is identified by hovering it
      and OCR-ing the PBTooltips16_70 tooltip window.

Multi-patient batch (one plan row per patient — verified live 2026-09-26):

    Final Bill rows run back-to-back inside the SAME HBSys session, so the
    transition between patients has to be explicit instead of "type the next
    hospital number and hope":

    1. `load_patient_by_hospital_no(hospital_no)` — the operator's steps 1-2:
       the Hospital No. lookup is DOUBLE-CLICKED at HOSPITAL_NO_POINT — the same
       verified (166, 174) slot Date Fill uses (hbsys_fill_dates.P.HOSPITAL_NO) —
       CTRL+A to clear, the number is typed, ENTER, then it is VERIFIED that a
       new "Billing (...)" form appeared. An unverified load returns False and
       the row BLOCKS; the flow never carries on into whatever patient happened
       to be on screen.
    2. `select_confinement(admission, discharge)` — the operator's step 3:
       Admit History -> exact grid-date match, else the OCR-tolerant fuzzy row
       (the same scoring Date Fill uses: best_fuzzy_admission_history_row),
       then double-click the row and answer the Rate validation dialog.
    3. `FinalBillRunner.run()` — the operator's steps 4-7: Final Bill ->
       "Final" checkbox -> OK -> answer the prompt (click OK on File
       save, click No on Call Administrator). The form then STAYS OPEN;
       the NEXT patient's loader just types that patient's hospital number
       into the same form.

    A leftover Billing form is therefore left on screen, and the next load is
    verified by requiring a Billing form that was NOT open before, so a
    patient that cannot be loaded blocks the row instead of guessing whose
    bill is being run.

This module contains the mechanics + a pure step PLANNER. The planner is a
pure function of the detected screen, so the whole decision path is
unit-testable headless (see tests/test_agent_final_bill.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.agent import hbsys_screens as screens

# -- Verified control evidence -------------------------------------------------

POPUP_TITLE_PRINT_OPTIONS = "Print Options"
POPUP_CLASS_PRINT_OPTIONS = "FNWNS370"
DIALOG_TITLE_CONFIRM = "Call Administrator"
DIALOG_CLASS_CONFIRM = "#32770"
DIALOG_TITLE_FILE_SAVE = "File save"
TOOLBAR_CLASS = "FNFIXEDBAR70"
TOOLTIP_CLASS = "PBTooltips16_70"

BTN_FINAL_ID = 1001          # "Final" checkbox button on Print Options
BTN_FINAL_OK_ID = 1002       # "OK" button on Print Options
BTN_PRINT_ID = 1003          # "PRINT" button on Print Options
BTN_CONFIRM_YES_ID = 6       # "&Yes" on the Call Administrator prompt
BTN_CONFIRM_NO_ID = 7        # "&No"  on the Call Administrator prompt

# Click offset INSIDE the "Final" button that lands on its checkbox glyph.
FINAL_CHECKBOX_OFFSET = (7, 12)

# Toolbar slot for "Close Form" (verified by tooltip OCR, row 2).
# Probe / manual-recovery only: the Final Bill flow never clicks it, so
# _probe_live.py can still close a form by hand.
CLOSE_FORM_POINT = (548, 97)
CLOSE_FORM_TOOLTIP = "Close Form"
# Toolbar rows inside FNFIXEDBAR70 (the band is roughly y 43..121).
TOOLBAR_ROW_2_TOP = 86
TOOLBAR_ROW_2_BOTTOM = 118

# Text the confirmation prompt shows (matched case-insensitively).
CONFIRM_PROMPT_TEXT = (
    "File exist in Patient Charges Detail.. do you want to replace "
    "the Computation?"
)
CONFIRM_PROMPT_MARKERS = ("replace the computation",)

# -- Patient lookup (multi-patient transitions) --------------------------------
# The Hospital No. lookup on the HBSys main screen. The coordinate is NOT
# invented here: it is Date Fill's verified P.HOSPITAL_NO
# (date_fill_hbsys/hbsys_fill_dates.py: Point(166, 174)), the slot that
# search_hospital_number() double-clicks, clears with CTRL+A, types into and
# confirms with ENTER. Keeping one source of truth is what makes Final Bill
# follow the exact Date Fill workflow for every patient in a batch.
HOSPITAL_NO_POINT = (166, 174)
HOSPITAL_NO_LABEL = "Hospital No."
# Verified live control id of the Hospital No. Edit on the Billing form
# (_probe_final_bill.py, 2026-09-26: "Edit 1004 on the Billing form").
HOSPITAL_NO_EDIT_ID = 1004
# The patient's form is an MDI child titled "Billing (<PATIENT NAME>)".
BILLING_FORM_PREFIX = "Billing ("
# Live value Date Fill waits after ENTER (hbsys_fill_dates.search_hospital_number
# uses sleep_short(1.5) before touching Admit History).
# Seconds to wait for a NEW "Billing (...)" form after ENTER. Date Fill sleeps
# 1.5s blindly; the patient load is a server round-trip and the live run on
# 2026-09-26 blocked a row because 1.5s was not enough to be sure, so the
# verified wait is longer and every second of it is logged.
LOAD_WAIT_SECONDS = 8.0
CLOSE_FORM_WAIT_SECONDS = 3.0

# Encounter type words the Admission History grid prints (Date Fill's TYPE_WORDS
# in hbsys_read_admission_history.py). Only ADMIT gets the fuzzy bonus.
ENCOUNTER_WORDS = ("ADMIT", "OPD", "ER", "EMERGENCY")


# -- Step vocabulary ----------------------------------------------------------

STEP_OPEN_FINAL_BILL = "open_final_bill"     # Billing -> Final Bill (menu)
STEP_CHECK_FINAL = "check_final_box"         # tick the checkbox beside "Final"
STEP_CLICK_OK = "click_ok"                   # OK on the Print Options popup
STEP_SAVE_CLICK_OK = "save_click_ok"             # "File save" -> click "OK"
STEP_CONFIRM_NO = "answer_confirm_no"            # "Call Administrator" -> click "No"
STEP_DONE = "done"                           # bill final (form stays open)
STEP_BLOCKED = "blocked"                     # needs a human / wrong screen

ALL_STEPS = (
    STEP_OPEN_FINAL_BILL,
    STEP_CHECK_FINAL,
    STEP_CLICK_OK,
    STEP_SAVE_CLICK_OK,
    STEP_CONFIRM_NO,
    STEP_DONE,
    STEP_BLOCKED,
)


@dataclass(frozen=True)
class StepDecision:
    """What to do next, and why (shown in logs / the Plan Panel evidence)."""

    step: str
    reason: str
    screen: str = screens.SCREEN_UNKNOWN
    final_checked: bool = False
    bill_finalized: bool = False
    ok_clicked: bool = False
    prompt_answered: bool = False

    @property
    def terminal(self) -> bool:
        return self.step in (STEP_DONE, STEP_BLOCKED)


# -- Planner ------------------------------------------------------------------

def _expected_note(expected_screen: str, screen: str) -> str:
    """Empty when the screen matches the plan's reason, else a mismatch note.

    The reason is a prediction, not an instruction: detection stays
    authoritative. The note only makes the disagreement visible in the run
    report instead of silently acting on the prompt nobody expected.
    """
    if not expected_screen or expected_screen == screen:
        return ""
    return (
        f" [note: the plan reason predicted {expected_screen}, but "
        f"{screen} is what is on screen]"
    )


def plan_step(
    screen: str,
    *,
    expected_screen: str = "",
    final_checked: bool = False,
    bill_finalized: bool = False,
    ok_clicked: bool = False,
    prompt_answered: bool = False,
    forms_open: bool = True,
) -> StepDecision:
    """Return the next deterministic step for the observed HBSys state.

    The operator's tail of the flow (2026-09-28) is:

        Final Bill -> tick "Final" -> click OK -> answer the prompt

    The prompt is answered TWO different ways, exactly as the operator does,
    and both with the MOUSE (2026-09-28):

        "File save"            -> CLICK the "OK" in the button row at the
                                  BOTTOM of the dialog (never TAB, never ENTER)
        "Call Administrator"   -> CLICK "No" (never Yes - it would replace
                                  the computed charges)

    Nothing here depends on tab order or on ENTER accepting whatever happens
    to be focused, and each answer is verified to have closed its own dialog.

    Args:
        screen: current screen from the Detector (screens.SCREEN_*).
        final_checked: True once the "Final" checkbox glyph reads as ticked.
        ok_clicked: True once STEP_CLICK_OK ran (the popup is committed and
            the post-OK prompt has to be answered).
        prompt_answered: True once the post-OK prompt was answered — that is
            the authoritative "bill is final" signal, not the screen.
        bill_finalized: True once the Final Bill itself is committed.
        forms_open: True while the Billing form is still open. The form is
            NEVER closed by this flow: after OK/No the bill is final and the
            next patient's loader closes the leftover form while typing the
            next Hospital No. (operator instruction, 2026-10-02).
    """
    # Answering the prompt is what commits the bill, so the answer itself is
    # the authoritative "finalized" signal - not the screen. Without this the
    # planner would re-plan the answer forever on a screen that still reports
    # the (now answered) prompt.
    if prompt_answered:
        bill_finalized = True

    if bill_finalized:
        # Done. The Close Form toolbar click was REMOVED on operator
        # instruction (2026-10-02): the flow ends here and the next patient's
        # loader closes the leftover form while typing the next Hospital No.
        return StepDecision(
            step=STEP_DONE,
            reason=(
                "Final Bill committed"
                + (
                    " and the Billing form is closed"
                    if not forms_open
                    else " - the form stays open until every selected patient "
                         "is done (the next loader closes it)"
                )
            ),
            screen=screen,
            final_checked=final_checked,
            bill_finalized=True,
            ok_clicked=ok_clicked,
            prompt_answered=True,
        )

    if screen == screens.SCREEN_FINAL_BILL_OPTIONS:
        if not final_checked:
            return StepDecision(
                step=STEP_CHECK_FINAL,
                reason=(
                    "Print Options popup is open: tick the checkbox beside "
                    "the word 'Final'"
                ),
                screen=screen,
            )
        if ok_clicked:
            # OK was already pressed for this popup and it is STILL on screen.
            # HBSys leaves Print Options open BEHIND the prompt it raises
            # ("Note"/"File save" or "Call Administrator") - the operator
            # answers the prompt next, never a second OK.
            #
            # Live 2026-10-02 15:03 (patient #21853): OK raised the File save prompt
            # while Print Options stayed open behind it - and the detector_read
            # raw titles only, because its screen OCR never saw the prompt.
            # The plan's own predicted prompt (Agent Plan reason) says the
            # prompt that is up in that case, so the mapped button is clicked
            # once (File save OK 971,593 / +(963,560), or No on the Call
            # Administrator dialog) - never a second OK, never a blind click.
            # Only a stale read with NO prompt and NO expectation blocks.
            if (
                not prompt_answered
                and expected_screen == screens.SCREEN_FILE_SAVE
            ):
                return StepDecision(
                    step=STEP_SAVE_CLICK_OK,
                    reason=(
                        "Print Options stayed open behind the raised prompt and "
                        "the plan reason predicted FILE_SAVE, so the File save "
                        "OK (971,593 / +(963,560), operator-mapped) is clicked "
                        "once - OK on Print Options is never clicked twice"
                    ),
                    screen=screen,
                    final_checked=True,
                    ok_clicked=True,
                )
            if (
                not prompt_answered
                and expected_screen == screens.SCREEN_FINAL_BILL_CONFIRM
            ):
                return StepDecision(
                    step=STEP_CONFIRM_NO,
                    reason=(
                        "Print Options stayed open behind the raised prompt and "
                        "the plan reason predicted the Call Administrator "
                        "dialog, so No is clicked once - OK on Print Options "
                        "is never clicked twice"
                    ),
                    screen=screen,
                    final_checked=True,
                    ok_clicked=True,
                )
            return StepDecision(
                step=STEP_BLOCKED,
                reason=(
                    "the Print Options popup is still on screen after OK was "
                    "clicked once - OK is never clicked twice; close the popup "
                    "by hand and finish this patient"
                ),
                screen=screen,
                final_checked=True,
                ok_clicked=True,
            )
        return StepDecision(
            step=STEP_CLICK_OK,
            reason="'Final' is ticked: click OK on the Print Options popup",
            screen=screen,
            final_checked=True,
        )

    # The "File save" prompt: click its OK button. No TAB and no keystroke —
    # the operator clicks OK, and a click cannot depend on the dialog's tab
    # order the way a keyboard answer does.
    if screen == screens.SCREEN_FILE_SAVE:
        return StepDecision(
            step=STEP_SAVE_CLICK_OK,
            reason=(
                "'File save' prompt is up: click OK to accept it (the "
                "operator's answer)" + _expected_note(expected_screen, screen)
            ),
            screen=screen,
            final_checked=final_checked,
            ok_clicked=ok_clicked,
        )

    # The "Call Administrator" Yes/No prompt: click "No". The operator answers
    # this one with the mouse (2026-09-28) - "Yes" would replace the computed
    # charges, so "No" is clicked by name and never by tab order.
    if screen == screens.SCREEN_FINAL_BILL_CONFIRM:
        return StepDecision(
            step=STEP_CONFIRM_NO,
            reason=(
                "HBSys asks 'do you want to replace the Computation?' - click "
                "No (never Yes: it would replace the computed charges)"
                + _expected_note(expected_screen, screen)
            ),
            screen=screen,
            final_checked=final_checked,
            ok_clicked=ok_clicked,
        )

    # OK was clicked but no prompt is on screen yet (or the build raises none):
    # the operator still waits for the prompt rather than clicking blindly, so
    # this is reported as a human step instead of a guess.
    if ok_clicked and not prompt_answered:
        return StepDecision(
            step=STEP_BLOCKED,
            reason=(
                "OK was clicked but neither the 'File save' prompt nor the "
                "'Call Administrator' dialog could be read - check the screen "
                "and finish this patient by hand"
            ),
            screen=screen,
            final_checked=final_checked,
            ok_clicked=True,
        )

    if screen == screens.SCREEN_ORDER_TRANSACTIONS:
        if forms_open:
            return StepDecision(
                step=STEP_OPEN_FINAL_BILL,
                reason=(
                    "Billing form is open: open Billing -> Final Bill "
                    "(patient + confinement must already be selected)"
                ),
                screen=screen,
            )
        return StepDecision(
            step=STEP_BLOCKED,
            reason=(
                "no Billing form open: search the Hospital No. and select "
                "the confinement period before Final Bill (human step)"
            ),
            screen=screen,
        )

    if screen == screens.SCREEN_HBSYS_CLOSED:
        return StepDecision(
            step=STEP_BLOCKED,
            reason="HBSys is CLOSED - open HBSys and try again",
            screen=screen,
        )

    return StepDecision(
        step=STEP_BLOCKED,
        reason=(
            f"screen {screen} is not part of the Final Bill flow; "
            "route to Patient Review instead of guessing"
        ),
        screen=screen,
        final_checked=final_checked,
    )



# -- Control evidence table ----------------------------------------------------
# Kept as data (not prose) so tests/reports can assert on the exact values that
# were verified live, and so future calibration changes are one-line edits.

@dataclass(frozen=True)
class ControlEvidence:
    """One verified control: window + control identity + observed rectangle."""

    window_title: str
    window_class: str
    control_text: str
    control_id: int
    rect: tuple          # (left, top, right, bottom) as observed live
    note: str = ""


FINAL_BILL_EVIDENCE = (
    ControlEvidence(
        POPUP_TITLE_PRINT_OPTIONS,
        POPUP_CLASS_PRINT_OPTIONS,
        "Final",
        BTN_FINAL_ID,
        (333, 325, 421, 350),
        "checkbox beside the word 'Final'; toggle point = left+7, top+12",
    ),
    ControlEvidence(
        POPUP_TITLE_PRINT_OPTIONS,
        POPUP_CLASS_PRINT_OPTIONS,
        "OK",
        BTN_FINAL_OK_ID,
        (331, 357, 410, 384),
        "commit the Final Bill options",
    ),
    ControlEvidence(
        POPUP_TITLE_PRINT_OPTIONS,
        POPUP_CLASS_PRINT_OPTIONS,
        "PRINT",
        BTN_PRINT_ID,
        (278, 202, 497, 317),
        "never clicked by the Final Bill flow (printing is not wanted)",
    ),
    ControlEvidence(
        DIALOG_TITLE_CONFIRM,
        DIALOG_CLASS_CONFIRM,
        "&Yes",
        BTN_CONFIRM_YES_ID,
        (988, 585, 1063, 608),
        "DO NOT click: Yes would replace the existing computation",
    ),
    ControlEvidence(
        DIALOG_TITLE_CONFIRM,
        DIALOG_CLASS_CONFIRM,
        "&No",
        BTN_CONFIRM_NO_ID,
        (1072, 585, 1147, 608),
        "the operator's answer - keeps the existing computation",
    ),
)


def evidence_for(control_id: int) -> ControlEvidence:
    """Look up one verified control by its Windows control id."""
    for item in FINAL_BILL_EVIDENCE:
        if item.control_id == control_id:
            return item
    raise KeyError(f"no verified evidence for control id {control_id}")


# -- GUI mechanics (verified live 2026-09-25) ----------------------------------

def _desktop():
    from pywinauto import Desktop

    return Desktop(backend="win32")


def _safe_visible(window) -> bool:
    """True when a desktop window is visible (never raises)."""
    try:
        return bool(window.is_visible())
    except Exception:
        return False


def find_hbsys_main():
    """HBSys main frame (FNWND370 / HOMIS) - same lookup as the Navigator."""
    from core.agent import hbsys_nav

    return hbsys_nav.find_main_window()


def find_print_options_window():
    """The Final Bill 'Print Options' popup, or None.

    Identified by class FNWNS370 + a VISIBLE 'Final' Button (id 1001).
    PowerBuilder reuses id 1001/1002 for hidden datawindows too, so both the
    class and the visibility must be checked (a real live bug we hit).
    """
    for window in _desktop().windows():
        try:
            if not window.is_visible():
                continue
            if window.class_name() != POPUP_CLASS_PRINT_OPTIONS:
                continue
            if _visible_button(window, BTN_FINAL_ID) is not None:
                return window
        except Exception:
            continue
    return None


def find_dialog(*title_markers):
    """Visible top-level window whose title contains one of the markers."""
    wanted = [marker.lower() for marker in title_markers]
    for window in _desktop().windows():
        try:
            if not window.is_visible():
                continue
            title = (window.window_text() or "").strip()
        except Exception:
            continue
        if title and any(marker in title.lower() for marker in wanted):
            return window
    return None


def find_dialog_exact(*titles):
    """Visible top-level window whose title matches one of `titles` EXACTLY.

    find_dialog() matches on a substring, so the "Save" and "Note" markers can
    land on an unrelated window - one of them measured 1912x1040, i.e. the whole
    desktop. Anchoring a click point has to be sure it is the real dialog, so
    matching is exact here (only case and surrounding blanks are forgiven).
    """
    wanted = {str(title).strip().lower() for title in titles if title}
    if not wanted:
        return None
    for window in _desktop().windows():
        try:
            if not window.is_visible():
                continue
            title = (window.window_text() or "").strip()
        except Exception:
            continue
        if title and title.lower() in wanted:
            return window
    return None


def _live_note_evidence():
    """(body_fn, marker_fn) proving the live "Note" window is the real prompt.

    body_fn("Note") returns that dialog's body text ONLY when a window titled
    exactly "Note" is live on the desktop - nothing is read from a window that
    is not really open. marker_fn(("Note",)) returns the live window (the
    existence proof the detector needs before admitting it).
    """
    def body_fn(title):
        if str(title or "").strip().lower() != "note":
            return ""
        try:
            dialog = find_dialog_exact("Note")
        except Exception:
            return ""
        if dialog is None:
            return ""
        try:
            return dialog_text(dialog)
        except Exception:
            return ""

    def marker_fn(markers):
        if isinstance(markers, str):
            markers = (markers,)
        wanted = {
            str(marker).strip().lower() for marker in (markers or ()) if marker
        }
        if "note" not in wanted:
            return None
        try:
            return find_dialog_exact("Note")
        except Exception:
            return None

    return body_fn, marker_fn


def _children(window):
    try:
        return list(window.children())
    except Exception:
        return []


def _visible_button(window, control_id: int, text: str = ""):
    """First VISIBLE Button child with this control id (and optional text)."""
    for child in _children(window):
        try:
            if child.class_name() != "Button":
                continue
            if child.control_id() != control_id:
                continue
            if not child.is_visible():
                continue
            if text and text.upper() not in (child.window_text() or "").upper():
                continue
            return child
        except Exception:
            continue
    return None


def rect_of(control) -> tuple:
    """(left, top, right, bottom) of a pywinauto control."""
    r = control.rectangle()
    return (r.left, r.top, r.right, r.bottom)


def _grab(box):
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=box)


def checkbox_glyph(control):
    """Grayscale pixels of the checkbox glyph inside the 'Final' button.

    The glyph is drawn at the button's left edge; the measured window is
    left+1 .. left+14, top+6 .. top+18 (offset matches
    FINAL_CHECKBOX_OFFSET and was confirmed with pixel dumps).
    """
    import numpy as np

    left, top, _, _ = rect_of(control)
    image = _grab((left + 1, top + 6, left + 14, top + 18)).convert("L")
    return np.array(image)


def checkbox_dark_ratio(control) -> float:
    """Ratio of dark pixels in the glyph - rises when the box is ticked."""
    return float((checkbox_glyph(control) < 128).mean())


def checkbox_ascii(control) -> str:
    """ASCII dump of the glyph (log evidence, like the live session used)."""
    return "\n".join(
        "".join("#" if value < 128 else "." for value in row)
        for row in checkbox_glyph(control)
    )


def raise_to_top(window, topmost: bool = True) -> bool:
    """Bring a window to the interactive foreground so clicks land on it.

    Mirrors the working probes: ``window.set_focus()`` + ``ShowWindow``
    (restore when minimized) + ``SetForegroundWindow`` until Windows reports
    it as the foreground window, then keep it topmost with ``SetWindowPos``.
    The old HWND_TOPMOST-only recipe raised z-order without focus, so the
    HBSys typing went to the GUI/IDE instead. Returns True when the window
    is the foreground window afterwards.
    """
    try:
        import win32con
        import win32gui
    except Exception:
        return False
    handle = None
    try:
        handle = int(window.handle)
    except Exception:
        return False
    if not handle:
        return False
    try:
        try:
            window.set_focus()
        except Exception:
            pass
        try:
            if win32gui.IsIconic(handle):
                win32gui.ShowWindow(handle, win32con.SW_RESTORE)
        except Exception:
            pass
        for _ in range(3):
            try:
                win32gui.SetForegroundWindow(handle)
            except Exception:
                pass
            try:
                import time as _time

                _time.sleep(0.25)
                if win32gui.GetForegroundWindow() == handle:
                    break
            except Exception:
                break
        win32gui.SetWindowPos(
            handle,
            win32con.HWND_TOPMOST if topmost else win32con.HWND_NOTOPMOST,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
        )
        try:
            return win32gui.GetForegroundWindow() == handle
        except Exception:
            return True
    except Exception:
        return False


def ensure_com_thread() -> None:
    """Init COM on this thread so pywinauto clicks never crash the worker.

    The Agent Plan worker is a plain background thread; pywinauto's mouse
    input (`pywinauto.mouse.double_click`) died there with
    RPC_E_DISCONNECTED (0x80010108, logged in ``logs/gui_crash.log``) because
    COM was never initialised. CoInitializeEx is idempotent, so calling it
    from the GUI thread too is harmless. Returns nothing.
    """
    try:
        import pythoncom  # type: ignore

        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        except Exception:
            # S_FALSE / RPC_E_CHANGED_MODE: already initialised — fine.
            pass
    except Exception:
        pass


def real_click(x: int, y: int) -> None:
    """Real mouse click at absolute screen coordinates (pywinauto/pyauto)."""
    import pywinauto

    pywinauto.mouse.click(coords=(x, y))


def click_button(control, window=None) -> bool:
    """Real click on a control's centre, after raising its window."""
    if window is not None:
        raise_to_top(window)
    left, top, right, bottom = rect_of(control)
    real_click((left + right) // 2, (top + bottom) // 2)
    return True


def click_point(rect, window=None) -> bool:
    """Real click at the centre of a (l, t, r, b) rectangle.

    Used when the target is located by geometry only, with no clickable
    control wrapper to hand to `click_button`.
    """
    if window is not None:
        raise_to_top(window)
    left, top, right, bottom = rect
    real_click((left + right) // 2, (top + bottom) // 2)
    return True


def _mapped_click_point(name: str, window) -> tuple | None:
    """Operator-recorded click point for `name` (Final Bill click map), or None.

    The operator can record the real buttons once
    (``python -m core.agent.final_bill_click_map --map``); this hands that point
    to the flow. The point was stored as an offset inside `window`'s own
    rectangle, so it is resolved against the LIVE rectangle and follows a popup
    that opens somewhere else on screen.

    Returns None when the target was never mapped, when the map cannot be read
    or when `window` has no usable rectangle - the caller then keeps its
    verified control lookup, which is why the mapping stays optional.
    """
    try:
        from core.agent import final_bill_click_map as click_map

        rect = rect_of(window) if window is not None else None
        if rect is not None:
            rect = tuple(int(value) for value in rect)
        return click_map.mapped_point(name, rect)
    except Exception:
        return None


# PowerBuilder repaints the 'Final' checkbox glyph AFTER the click lands.
# Reading the pixels straight away sees the old glyph, the step then reports
# "not ticked", clicks a SECOND time (unticking the box again) and re-plans
# forever - the suspected cause of the 2026-09-29 12:43 run that burned all
# 12 steps without ever reaching the prompt.
CHECKBOX_REPAINT_SETTLE_SECONDS = 0.35


def _final_checkbox_offset(button_rect, window) -> tuple:
    """Click offset for the 'Final' checkbox: mapped point, else the verified one.

    A mapped `final_checkbox` point (click map) is resolved against the popup
    and converted back to a BUTTON-relative offset, because the idempotent
    toggle below clicks the same spot twice (tick, then the corrective un-tick).
    """
    left, top = int(button_rect[0]), int(button_rect[1])
    mapped = _mapped_click_point("final_checkbox", window)
    if mapped is not None:
        return mapped[0] - left, mapped[1] - top
    return FINAL_CHECKBOX_OFFSET


def toggle_final_checkbox(window) -> bool:
    """Tick the checkbox beside the word 'Final' (idempotent).

    Returns True when the glyph ends up ticked. If the first click DECREASED
    the dark ratio the box was already ticked, so it is clicked again (this
    keeps the step idempotent when a previous run left it checked).

    The glyph is re-read only after CHECKBOX_REPAINT_SETTLE_SECONDS, so a
    repaint still in flight can never be mistaken for "the box did not tick".
    """
    import time

    button = _visible_button(window, BTN_FINAL_ID, "Final")
    if button is None:
        raise RuntimeError("'Final' button (id 1001) not found on the popup")
    button_rect = rect_of(button)
    left, top = int(button_rect[0]), int(button_rect[1])
    offset_x, offset_y = _final_checkbox_offset(button_rect, window)
    before = checkbox_dark_ratio(button)
    raise_to_top(window)
    real_click(left + offset_x, top + offset_y)
    time.sleep(CHECKBOX_REPAINT_SETTLE_SECONDS)
    after = checkbox_dark_ratio(button)
    if after <= before:
        raise_to_top(window)
        real_click(left + offset_x, top + offset_y)
        time.sleep(CHECKBOX_REPAINT_SETTLE_SECONDS)
        after = checkbox_dark_ratio(button)
    return after > before


# How long the Print Options popup is given to disappear after the one OK
# click, and how often that is re-checked.
POPUP_CLOSE_TIMEOUT_SECONDS = 3.0
POPUP_CLOSE_POLL_SECONDS = 0.15


def _popup_gone(window, timeout: float = POPUP_CLOSE_TIMEOUT_SECONDS) -> bool:
    """True once the Print Options popup is no longer on screen.

    A PowerBuilder popup can report itself still alive for a moment after the
    click that closed it, so this polls the window instead of trusting a single
    read. It reports the outcome only - the caller decides what it means.
    """
    import time

    try:
        is_visible = getattr(window, "is_visible", None)
        if is_visible is None:
            return True
        deadline = time.time() + max(0.0, float(timeout))
        while True:
            try:
                if not is_visible():
                    return True
            except Exception:
                # The handle is gone: the popup closed.
                return True
            if time.time() >= deadline:
                return False
            time.sleep(POPUP_CLOSE_POLL_SECONDS)
    except Exception:
        return True


def click_print_options_ok(window) -> bool:
    """Click OK (id 1002) on the Print Options popup with the mouse.

    A point the operator recorded (click map `print_options_ok`) is preferred:
    it is what really gets pressed in the live flow, and it is applied relative
    to the popup, so it survives a popup that opens elsewhere. Without a
    mapping the live-verified control id is used, exactly as before.
    """
    mapped = _mapped_click_point("print_options_ok", window)
    if mapped is not None:
        raise_to_top(window)
        real_click(*mapped)
    else:
        button = _visible_button(window, BTN_FINAL_OK_ID, "OK")
        if button is None:
            raise RuntimeError("OK button (id 1002) not found on the popup")
        click_button(button, window)
    # One click, then confirm the popup is on its way out. Without this the
    # caller cannot tell a real click from one the popup swallowed, and the
    # planner would press OK again on the unchanged screen.
    return _popup_gone(window)


# Settle time before clicking OK on the "File save" prompt, how long we wait
# for the prompt to actually disappear, and the settle after it is gone.
SAVE_CLICK_SETTLE_SECONDS = 0.8
SAVE_VERIFY_TIMEOUT_SECONDS = 3.0
SAVE_VERIFY_SETTLE_SECONDS = 0.8


def answer_save_prompt_with_ok() -> str:
    """Answer the "File save" prompt by CLICKING its "OK" button.

    The operator clicks OK on this prompt (2026-09-28), so the button is
    clicked by id/caption like every other control in this flow - no TAB, no
    ENTER, no SPACE. That also removes the focus-order guesswork a keyboard
    answer depends on: the click lands on the button itself, whatever the
    dialog's tab order is.

    When the operator has mapped this button (click map `save_ok`), that exact
    point is clicked first; if the prompt does not close, the verified control
    lookup runs right after (see _click_prompt_answer), so a stale mapping can
    never break the row.

    The prompt is raised to the foreground first, otherwise the click would
    land on the claims GUI / IDE that is on top - the exact failure that made
    earlier runs of this flow type into the wrong window.

    Returns a short label for the log. Raises when the prompt or its OK
    button cannot be found, so the row fails with a readable reason instead
    of guessing.
    """
    import time

    ensure_com_thread()

    dialog, label = _front_save_prompt()
    if dialog is None:
        raise RuntimeError(
            f"{DIALOG_TITLE_FILE_SAVE!r} prompt not found - cannot click OK"
        )
    raise_to_top(dialog)
    time.sleep(SAVE_CLICK_SETTLE_SECONDS)
    if not _click_prompt_answer(dialog, "save_ok", answer_dialog_ok):
        raise RuntimeError(
            "clicked OK on the 'File save' prompt but it stayed open - "
            "finish this patient by hand"
        )
    time.sleep(SAVE_VERIFY_SETTLE_SECONDS)
    return label


def _front_save_prompt():
    """The "File save" prompt window: (window, label); (None, "") when absent.

    "Save"/"Save As" are included because the same branch on another build
    shows a plain save dialog instead of the PowerBuilder one. The confirm
    dialog is NOT a candidate here - it is answered by clicking "No", so a
    plain "OK" click would answer the wrong prompt.
    """
    for title in (DIALOG_TITLE_FILE_SAVE, "Save", "Save As"):
        dialog = find_dialog(title)
        if dialog is not None:
            return dialog, (dialog.window_text() or title).strip()
    # Live 2026-10-02: HBSys titled this prompt "Note" and put "File Save" in
    # the body. Only accepted when the body confirms it - a plain "Note" window
    # is somebody else's dialog and is never clicked here.
    dialog = find_dialog("Note")
    if dialog is not None and "file save" in str(dialog_text(dialog) or "").lower():
        return dialog, (dialog.window_text() or "Note").strip()
    return None, ""


# Settle before clicking "No" on the "Call Administrator" dialog, and how long
# we wait afterwards for the dialog to actually close.
CONFIRM_NO_SETTLE_SECONDS = 0.8
CONFIRM_NO_WAIT_SECONDS = 2.0


def answer_confirm_prompt_with_no() -> str:
    """Answer the "Call Administrator" dialog by CLICKING "No".

    The operator clicks No on this Yes/No prompt (2026-09-28). Clicking the
    button by name is deliberate: "Yes" would replace the computed charges, so
    this must never depend on tab order or on ENTER accepting whatever is
    focused. The dialog is raised to the foreground first, otherwise the click
    would land on the claims GUI / IDE that happens to be on top - the exact
    failure that made earlier runs of this flow type into the wrong window.

    Returns a short label for the log. Raises when the dialog or its "No"
    button cannot be found, so the row fails with a readable reason instead
    of guessing.
    """
    import time

    ensure_com_thread()

    dialog = find_dialog(DIALOG_TITLE_CONFIRM)
    if dialog is None:
        raise RuntimeError(
            f"{DIALOG_TITLE_CONFIRM!r} prompt not found - cannot click No"
        )
    label = (dialog.window_text() or DIALOG_TITLE_CONFIRM).strip()
    raise_to_top(dialog)
    time.sleep(CONFIRM_NO_SETTLE_SECONDS)
    if not _click_prompt_answer(
        dialog, "admin_no", answer_dialog_no, CONFIRM_NO_WAIT_SECONDS
    ):
        raise RuntimeError(
            f"clicked No on the {label!r} dialog but it stayed open - "
            "finish this patient by hand"
        )
    time.sleep(SAVE_VERIFY_SETTLE_SECONDS)
    return label


def _wait_prompt_gone(dialog, timeout: float = SAVE_VERIFY_TIMEOUT_SECONDS) -> bool:
    """True once `dialog` is no longer visible (False when it survived)."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if not dialog.is_visible():
                return True
        except Exception:
            # The window handle died with the dialog: that is the goal.
            return True
        time.sleep(0.1)
    return False


def _click_prompt_answer(
    dialog,
    map_name: str,
    control_click_fn,
    timeout: float = SAVE_VERIFY_TIMEOUT_SECONDS,
) -> bool:
    """Click a prompt's answer button: mapped point first, control as retry.

    Returns True when the dialog is gone afterwards. The operator's own point
    (click map `map_name`) is tried first because that is what really gets
    pressed live; when it does not close the dialog the verified control lookup
    happens right after and the dialog is re-checked. A stale or wrong mapping
    therefore degrades to the pre-2026-09-30 behaviour instead of failing the
    row - and a mapped point NEVER answers a different prompt, because its
    dialog was already identified by the caller.
    """
    mapped = _mapped_click_point(map_name, dialog)
    if mapped is not None:
        raise_to_top(dialog)
        real_click(*mapped)
        if _wait_prompt_gone(dialog, timeout):
            return True
    try:
        control_click_fn(dialog)
    except Exception:
        if mapped is None:
            raise
    return _wait_prompt_gone(dialog, timeout)


def _visible_static_texts(window) -> list:
    texts = []
    for child in _children(window):
        try:
            if child.class_name() != "Static" or not child.is_visible():
                continue
            text = (child.window_text() or "").strip()
        except Exception:
            continue
        if text:
            texts.append(text)
    return texts


def dialog_text(window) -> str:
    """Static text of a dialog (what the prompt actually asks)."""
    return " | ".join(_visible_static_texts(window))


def is_replace_computation_prompt(window) -> bool:
    """True when this dialog is the 'replace the Computation?' Yes/No prompt."""
    blob = dialog_text(window).lower()
    if not blob:
        blob = (window.window_text() or "").lower()
    return any(marker in blob for marker in CONFIRM_PROMPT_MARKERS)


def _visible_buttons_by_text(window, *labels) -> list:
    """Every visible Button child whose text matches one of `labels`.

    Text is compared with the PowerBuilder ampersand stripped and case
    ignored ("&No" -> "no"), because the id/rect of a prompt can shift between
    HBSys builds while the caption does not.
    """
    wanted = [str(label).upper().replace("&", "") for label in labels]
    found = []
    for child in _children(window):
        try:
            if child.class_name() != "Button" or not child.is_visible():
                continue
            text = (child.window_text() or "").strip().upper().replace("&", "")
        except Exception:
            continue
        if text in wanted:
            found.append(child)
    return found


def _visible_button_by_text(window, *labels):
    """First visible Button child whose text matches one of `labels`."""
    matches = _visible_buttons_by_text(window, *labels)
    return matches[0] if matches else None


def _bottom_most_button(buttons: list):
    """The lowest button on screen (max rectangle().top), or the first one.

    The "File save" prompt can carry more than one control captioned "OK"; the
    operator clicks the one in the BUTTON ROW AT THE BOTTOM of the dialog, not
    one that belongs to the file list above it. Ties (same row) keep document
    order, so a single "OK" is returned unchanged.
    """
    def top_of(button) -> int:
        try:
            return int(button.rectangle().top)
        except Exception:
            return 0

    best = buttons[0]
    for button in buttons[1:]:
        if top_of(button) > top_of(best):
            best = button
    return best


# Captions that must NEVER be pressed on the "File save" prompt, whatever
# their position: they cancel or re-ask instead of keeping the file.
CANCEL_CAPTIONS = ("CANCEL", "&CANCEL", "NO", "&NO", "CLOSE", "&CLOSE")


def _button_caption(button) -> str:
    try:
        return (button.window_text() or "").strip().upper().replace("&", "")
    except Exception:
        return ""


def _accept_buttons(window) -> list:
    """Every visible Button that means "keep this file" (OK / Save / Yes).

    Caption-independent on purpose. The HBSys "File save" prompt is a
    PowerBuilder dialog: on some builds the button is a real `Button` child
    captioned "OK", on others it is an owner-drawn control whose caption OCR
    never reads, and on the live screen the arrow the operator drew pointed at
    a control whose text Tesseract could not resolve at all. Looking only for
    the literal caption "OK" is therefore the wrong test - this asks which
    buttons are NOT cancel-ish, and the geometry decides between them.
    """
    found = []
    for child in _children(window):
        try:
            if child.class_name() != "Button" or not child.is_visible():
                continue
        except Exception:
            continue
        caption = _button_caption(child)
        if caption in CANCEL_CAPTIONS:
            continue
        if caption and caption not in ("OK", "SAVE", "YES", "OPEN"):
            # A named non-accept button (Help, Details, Browse, ...): it is not
            # the one the operator presses for the default answer.
            continue
        found.append(child)
    return found


def answer_dialog_no(window) -> str:
    """Answer the confirmation prompt with No (id 7, else the 'No' caption).

    The id-7 button is the live-verified target; the caption lookup is the
    fallback for a repainted prompt so the batch never answers Yes by
    accident (Yes would replace the computed charges).
    """
    button = _visible_button(window, BTN_CONFIRM_NO_ID, "No")
    if button is None:
        button = _visible_button_by_text(window, "No", "NO")
    if button is None:
        raise RuntimeError("'No' button (id 7) not found on the prompt")
    click_button(button, window)
    return button.window_text()


def answer_dialog_ok(window) -> str:
    """Answer the 'File save' prompt with the OK at the BOTTOM of the dialog.

    The operator clicks the OK in the button row at the bottom of the prompt
    (2026-09-28, confirmed with a marked-up screenshot), so when the dialog
    carries more than one accepting control the LOWEST one is taken - an
    accept control belonging to the file list above it is not what the
    operator presses.

    Three passes, most reliable first:
      1. every visible Button captioned OK / Save (the usual PowerBuilder case)
      2. every visible Button that is NOT a cancel-ish caption - covers the
         owner-drawn button whose caption OCR never reads
      3. the last enabled child in the dialog's own bottom button row, by
         geometry alone - covers a prompt whose controls are not Buttons at all

    Cancel / No / Close are excluded in every pass, so this can never answer
    the prompt with a cancel.
    """
    # Pass 1 + 2: real Button children, accepting caption or no caption.
    candidates = _accept_buttons(window)
    if candidates:
        button = _bottom_most_button(candidates)
        click_button(button, window)
        return _button_caption(button) or "<unlabelled accept button>"

    # Pass 3: geometry. Click inside the dialog's own bottom band, at the
    # position of the last non-cancel control - no class, no caption, no id.
    fallback = _bottom_row_control(window)
    if fallback is not None:
        control, rect = fallback
        click_point(rect)
        return f"<bottom-row control at {rect}>"
    raise RuntimeError("OK button not found on the save prompt")


# How far up from the dialog's bottom edge the button row sits, and how tall
# that band is. PowerBuilder puts its button row just above the bottom border.
BOTTOM_ROW_BAND_PX = 72
MIN_BOTTOM_ROW_BUTTON_W = 40
MIN_BOTTOM_ROW_BUTTON_H = 16


def _bottom_row_control(window):
    """(control, (l, t, r, b)) of the last control in the dialog's bottom row.

    Used only when no accepting Button could be found by caption or class.
    The dialog's own rectangle defines the band, so the click is always inside
    the prompt and never lands on the HBSys form behind it.
    """
    try:
        dialog = window.rectangle()
    except Exception:
        return None
    band_top = dialog.bottom - BOTTOM_ROW_BAND_PX
    best = None
    for child in _children(window):
        try:
            if not child.is_visible():
                continue
            r = child.rectangle()
        except Exception:
            continue
        width = r.right - r.left
        height = r.bottom - r.top
        if width < MIN_BOTTOM_ROW_BUTTON_W or height < MIN_BOTTOM_ROW_BUTTON_H:
            continue
        if r.top < band_top or r.bottom > dialog.bottom:
            continue
        if _button_caption(child) in CANCEL_CAPTIONS:
            continue
        if best is None or r.left > best[1][0]:
            best = (child, (r.left, r.top, r.right, r.bottom))
    return best


def toolbar_tooltip_label(x: int, y: int, settle: float = 1.4) -> str:
    """Hover a toolbar slot and OCR the PowerBuilder tooltip -> button name.

    The HBSys toolbar is a PowerBuilder canvas with no child controls and its
    tooltip window (PBTooltips16_70) carries no window text, so the label is
    read from the tooltip pixels. Verified live for 'Close Form'.
    """
    import time

    import pywinauto
    from PIL import Image

    try:
        import pytesseract
    except Exception as exc:  # pragma: no cover - tess missing on new PCs
        return f"<no OCR: {exc}>"
    pywinauto.mouse.move(coords=(x, y))
    time.sleep(settle)
    box = _tooltip_rect()
    if box is None:
        return ""
    image = _grab(box).convert("L")
    big = image.resize((image.width * 3, image.height * 3), Image.LANCZOS)
    return pytesseract.image_to_string(big, config="--psm 7").strip()


def _tooltip_rect():
    for window in _desktop().windows():
        try:
            if not window.is_visible():
                continue
            if window.class_name() != TOOLTIP_CLASS:
                continue
            rect = window.rectangle()
            if rect.width() > 10 and rect.height() > 5 and rect.top > 0:
                return (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:
            continue
    return None


def _tooltip_matches_close_form(label: str) -> bool:
    """True when an OCR'd tooltip reads as the 'Close Form' button.

    Tolerant of OCR noise (case, stray whitespace) but strict enough that a
    shifted layout produces a mismatch and the click is refused.
    """
    words = (label or "").lower().split()
    return "close" in words and "form" in words


def click_close_form(point=None, verify: bool = True) -> str:
    """Click the 'Close Form' toolbar slot (verified (548, 97)).

    When verify is True the slot is confirmed by OCR-ing its tooltip first, so
    a layout shift fails loudly instead of clicking a random icon.
    """
    x, y = point or CLOSE_FORM_POINT
    label = toolbar_tooltip_label(x, y) if verify else ""
    if verify and label and not _tooltip_matches_close_form(label):
        raise RuntimeError(
            f"toolbar slot ({x}, {y}) is {label!r}, not 'Close Form' - "
            "layout changed, refusing to click"
        )
    real_click(x, y)
    return label or CLOSE_FORM_TOOLTIP


def close_billing_form(
    patient_name: str = "",
    timeout: float = CLOSE_FORM_WAIT_SECONDS,
    *,
    verify: bool = True,
    log_fn=None,
) -> bool:
    """Close the OPEN Billing form with the verified Close Form slot.

    Probe / manual-recovery only: no automated Final Bill path calls this (the
    form is left open after the prompt and between patients). It stays here for
    `_probe_live.py` and for the guards its unit tests cover (no Billing form
    open = no click).

    The multi-patient guard: the toolbar band is live on the main screen too,
    so clicking "Close Form" when no Billing form is open is exactly how the
    wrong window used to get closed. This refuses to click unless a Billing
    form is really open, then verifies the form is gone. Every decision is
    logged through `log_fn` so the run report says WHY a close failed (no
    form / tooltip mismatch / form survived) instead of just False.

    Args:
        patient_name: when given, only that patient's form may be closed; a
            different patient's form is left alone (returns False).
        timeout: seconds to wait for the form to disappear after the click.
        verify: OCR-verify the toolbar tooltip reads "Close Form" first.

    Returns:
        True only when no Billing form is left open afterwards.
    """
    import time

    log = log_fn or (lambda message: None)
    try:
        open_forms = billing_form_titles(list_open_forms())
    except Exception as exc:
        log(f"cannot list open forms: {type(exc).__name__}: {exc}")
        return False
    if not open_forms:
        log("Close Form: no Billing form open — refusing to click the toolbar")
        return False
    if patient_name:
        wanted = billing_form_for_patient(patient_name)
        open_keys = {billing_title_key(title) for title in open_forms}
        if billing_title_key(wanted) not in open_keys:
            log(
                f"Close Form: refusing — the open form is not this patient's "
                f"(open: {', '.join(open_forms)}, wanted: {wanted})"
            )
            return False

    log(f"Close Form: open forms before click: {', '.join(open_forms)}")
    try:
        label = click_close_form(verify=verify)
    except Exception as exc:
        log(f"Close Form: click refused — {exc}")
        return False
    if label:
        log(f"Close Form: toolbar slot reads {label!r} — clicked")

    deadline = time.time() + max(0.0, float(timeout))
    while True:
        try:
            still_open = billing_form_titles(list_open_forms())
        except Exception as exc:
            log(f"Close Form: cannot verify after the click: {exc}")
            return False
        if not still_open:
            log("Close Form: the Billing form is closed")
            return True
        if time.time() >= deadline:
            log(
                f"Close Form: the form survived the click "
                f"(still open: {', '.join(still_open)})"
            )
            return False
        time.sleep(0.2)


def list_open_forms() -> list:
    """Titles of the MDI child forms currently open in HBSys.

    A Billing form belongs to a patient, so only titles of the shape
    "Billing (<PATIENT NAME>)" count for the agent (see
    `billing_form_titles`). A bare "Billing" title is a workspace marker
    and never blocks a patient row.
    """
    main = find_hbsys_main()
    if main is None:
        return []
    titles = []
    for child in _children(main):
        try:
            if child.class_name() != "FNWND370" or not child.is_visible():
                continue
            title = child.window_text()
            if (title or "").strip() in ("", BILLING_FORM_PREFIX.rstrip(" (")):
                continue
            titles.append(title)
        except Exception:
            continue
    return titles


def billing_form_titles(titles) -> list:
    """Pure: the MDI form titles that belong to a patient's Billing form.

    HBSys titles the per-patient form "Billing (<PATIENT NAME>)"; anything
    else ("User Menu", "PhilHealth Beneficiaries", ...) is not a Billing form
    and is never treated as one.
    """
    return [
        str(title).strip()
        for title in (titles or [])
        if str(title).strip().startswith(BILLING_FORM_PREFIX)
    ]


def billing_form_for_patient(name: str) -> str:
    """Pure: the exact Billing form title for one patient name ('' when none)."""
    clean = str(name or "").strip()
    return f"{BILLING_FORM_PREFIX}{clean})" if clean else ""


_TITLE_PAD_RE = re.compile(r"\s+([(),])")


def normalize_billing_title(title) -> str:
    """Pure: a Billing form title reduced to one canonical spacing.

    HBSys builds the per-patient title as "Billing (<surname>, <given> <middle>)".
    A patient with no middle name leaves a stray space before the closing ")",
    so the real title is "Billing (VALENTINO, NIKKI )" while the folder-derived
    name is "VALENTINO, NIKKI" - the same patient, different bytes. This makes
    both sides canonical so they can never drift apart.

    Collapsing whitespace runs alone is NOT enough: "NIKKI )" splits into the
    tokens "NIKKI" and ")", so rejoining restores that very space. The pad is
    therefore dropped beside the brackets and the comma, which also removes the
    pad after "(" - compare titles with billing_title_key(), never with ==.
    """
    text = " ".join(str(title or "").split())
    # A collapsed run still leaves "NIKKI )": that space is the token separator
    # between the name and ")", so it must be dropped beside the punctuation.
    return _TITLE_PAD_RE.sub(r"\1", text)


def billing_title_key(title) -> str:
    """Pure: comparison key for a Billing title (whitespace + case folded).

    Every "is this form the patient's?" question goes through this key; the raw
    title is kept for logs and for window lookups.
    """
    return normalize_billing_title(title).casefold()


def is_billing_title(title) -> bool:
    """Pure: True when a window title is a per-patient Billing form.

    Both sides are keyed, because normalize_billing_title() also removes the
    pad after "(" - so a normalized title reads "Billing(VALENTINO, NIKKI)"
    and would never match the raw "Billing (" prefix.
    """
    return billing_title_key(title).startswith(billing_title_key(BILLING_FORM_PREFIX))


def find_billing_hospital_no_edit():
    """Find the visible Hospital No. Edit control on the Billing form.

    Preferred match is the verified control id (HOSPITAL_NO_EDIT_ID) recorded
    live by the Slice D probe (_probe_final_bill.py: "Hospital No. currently
    loaded (Edit 1004 on the Billing form)"). The geometric search (top-left
    band) is the fallback for a build that renumbers the control — the lookup
    is always double-clicked through HOSPITAL_NO_POINT if neither matches.
    """
    main = find_hbsys_main()
    if main is None:
        return None
    fallback = None
    for child in _children(main):
        try:
            if child.class_name() != "Edit" or not child.is_visible():
                continue
            if child.control_id() == HOSPITAL_NO_EDIT_ID:
                return child
        except Exception:
            continue
        if fallback is None:
            try:
                left, top, right, bottom = rect_of(child)
                if top < 300 and left < 300:
                    fallback = child
            except Exception:
                continue
    return fallback


def hospital_no_click_point() -> tuple:
    """The point to double-click for the Hospital No. lookup.

    When the verified Edit (id HOSPITAL_NO_EDIT_ID) is on screen its centre is
    used — control-relative, so HBSys does not have to be maximized. Otherwise
    the verified Date Fill slot (HOSPITAL_NO_POINT) is used, which is the only
    option while no Billing form is open.
    """
    try:
        edit = find_billing_hospital_no_edit()
    except Exception:
        edit = None
    if edit is not None:
        try:
            left, top, right, bottom = rect_of(edit)
            centre = ((left + right) // 2, (top + bottom) // 2)
            if edit.control_id() == HOSPITAL_NO_EDIT_ID:
                return centre
            if abs(centre[0] - HOSPITAL_NO_POINT[0]) <= 120 and (
                abs(centre[1] - HOSPITAL_NO_POINT[1]) <= 120
            ):
                return centre
        except Exception:
            pass
    return HOSPITAL_NO_POINT


# -- Screen diagnostics (what was on screen when a step refused) ---------------
# A blocked row is only actionable when the log says WHAT was on screen at the
# moment it blocked: which Billing form, which dialog/popup stole the click, and
# whether the Hospital No. field even received the typed number. These helpers
# answer exactly that and drop a screenshot in logs\, so the next review reads
# the run report instead of re-running the patient against a live HBSys.

DIAG_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"   # claims_bot/logs
DIAG_WINDOW_CLASSES = ("FNWND370", "FNWNS370", "FNFIXEDBAR70", "#32770")
DIAG_TITLE_MARKERS = (
    "hbsys", "homis", "billing", "print options", "call administrator",
    "admission history", "rate validation", "file save", "select encounter",
    "phic",
)


def hbsys_window_titles() -> list:
    """The HBSys windows, popups and dialogs currently on screen.

    Only HBSys-shaped windows are reported (class or title marker) so the line
    stays readable, but a modal `#32770` blocker always shows up — that is
    exactly the case where "nothing happened" used to be reported.
    """
    titles: list = []
    try:
        windows = _desktop().windows()
    except Exception:
        return titles
    for window in windows:
        try:
            if not window.is_visible():
                continue
            title = (window.window_text() or "").strip()
            class_name = window.class_name() or ""
        except Exception:
            continue
        low = title.lower()
        if class_name not in DIAG_WINDOW_CLASSES and not any(
            marker in low for marker in DIAG_TITLE_MARKERS
        ):
            continue
        label = title or "<untitled>"
        entry = f"{label} [{class_name}]"
        if entry not in titles:
            titles.append(entry)
    return titles


def _ocr_text(box) -> str:
    """OCR one screen region to text ('' when pytesseract/region is unusable)."""
    try:
        import pytesseract
    except Exception:
        return ""
    if box[2] <= box[0] or box[3] <= box[1]:
        return ""
    try:
        return " ".join(pytesseract.image_to_string(_grab(box)).split())
    except Exception:
        return ""


def hospital_no_field_text() -> str:
    """What the Hospital No. field shows RIGHT NOW.

    Prefers the Edit control's own text (HOSPITAL_NO_EDIT_ID) and falls back to
    OCR of the verified lookup band around HOSPITAL_NO_POINT, so a log can prove
    whether the click and the keystrokes actually landed on the field.
    """
    edit = None
    try:
        edit = find_billing_hospital_no_edit()
    except Exception:
        edit = None
    if edit is not None:
        try:
            text = (edit.window_text() or "").strip()
        except Exception:
            text = ""
        if text:
            return text
        try:
            left, top, right, bottom = rect_of(edit)
        except Exception:
            left = top = right = bottom = 0
        if right > left:
            return _ocr_text((left, top, right, bottom))
    x, y = HOSPITAL_NO_POINT
    return _ocr_text((x - 110, y - 14, x + 110, y + 14))


def save_screenshot(prefix: str = "agent_diag") -> str:
    """Save a full-screen PNG under logs\\ and return its path ('' on failure)."""
    try:
        from PIL import ImageGrab

        DIAG_LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = DIAG_LOG_DIR / f"{prefix}_{stamp}.png"
        ImageGrab.grab().save(path)
        return str(path)
    except Exception:
        return ""


def diagnose_screen(context: str = "screen", *, screenshot: bool = True) -> str:
    """A multi-line snapshot of the screen, for logs and run-report details.

    Names the open Billing forms, every HBSys window/dialog that could be
    swallowing clicks, what the Hospital No. field received, and where the
    screenshot was saved — the four things needed to tell "the patient did not
    load" apart from "a popup ate the click" and "the typing never landed".
    """
    lines = [f"{context}: diagnostics"]
    try:
        forms = billing_form_titles(list_open_forms())
        lines.append("  billing forms: " + (", ".join(forms) if forms else "none"))
    except Exception as exc:
        lines.append(f"  billing forms: unreadable ({type(exc).__name__}: {exc})")
    windows = hbsys_window_titles()
    lines.append("  windows: " + ("; ".join(windows) if windows else "none found"))
    field = hospital_no_field_text()
    lines.append("  hospital no field: " + (field if field else "<empty/unreadable>"))
    if screenshot:
        path = save_screenshot("agent_diag")
        if path:
            lines.append(f"  screenshot: {path}")
    return "\n".join(lines)


def _rate_validation_open() -> bool:
    """True when a visible 'Rate validation' window is on screen."""
    try:
        return any(
            (window.window_text() or "").strip() == "Rate validation"
            for window in _desktop().windows()
            if _safe_visible(window)
        )
    except Exception:
        return False


def clear_blocking_popups(log_fn=None) -> bool:
    """Close leftover HBSys popups that would swallow the next patient's click.

    Seen live 2026-09-26: a failed Date Fill row leaves its "Admission
    History" popup (and sometimes the informational "Rates no longer exist"
    dialog) open; a modal popup then eats the click/typing instead of letting
    the Hospital No. lookup land, so the next patient's load comes back
    unverified. This closes exactly those two known-transient popups and only
    answers the known rate message. Any OTHER blocker is left alone and
    reported, so the row fails loudly with its name in the log instead of
    guessing.
    """
    log = log_fn or (lambda message: None)

    popup = find_admission_history()
    if popup is not None:
        log("a leftover Admit History popup is open — closing it first")
        try:
            popup.close()
        except Exception:
            try:
                popup.set_focus()
            except Exception:
                pass
            try:
                from pywinauto.keyboard import send_keys

                send_keys("{ESC}")
            except Exception:
                pass

    # The Rate dialog only exists AFTER a confinement row is picked, so only
    # answer it when it is actually on screen. Dismissing blindly used to
    # print "an unknown dialog is open" on clean screens and BLOCK every
    # patient even when no window was open at all.
    rate_open = _rate_validation_open()
    if rate_open:
        if _dismiss_rate_validation_dialog(timeout=1.5):
            log("Rate validation dialog answered (Rates no longer exist)")
        else:
            log("an unknown dialog is open — stopping instead of clicking blindly")
            # Name it: "nothing happened" is not an actionable reason.
            for line in diagnose_screen("load: blocker", screenshot=False).splitlines():
                log(line)
            return False
    else:
        log("no Rate validation dialog on screen — nothing to dismiss")
    return True


LOAD_VERIFY_NEW_FORM = "new_form"
LOAD_VERIFY_RELINK = "relink"


def load_patient_by_hospital_no(
    hospital_no: str,
    timeout: float = LOAD_WAIT_SECONDS,
    *,
    close_stale_form: bool = True,
    log_fn=None,
    verify_mode: str = LOAD_VERIFY_NEW_FORM,
    expect_title: str = "",
) -> bool:
    """Load one patient into the Billing form - the operator's steps 1-2.

    Exactly Date Fill's `search_hospital_number`, plus the verification Final
    Bill needs for a multi-patient batch:

        0. leftover popups (Admit History / Rate validation) from a previous
           failure are cleared first, because a modal popup makes the click
           and the typing land on the wrong window;
        1. the Hospital No. field is DOUBLE-CLICKED, cleared with CTRL+A,
           filled with `hospital_no` and confirmed with ENTER;
        2. the load is VERIFIED: a "Billing (...)" form that was not open
           before must appear (Date Fill waits the same 1.5s here). Nothing is
           closed in between, so this check is what proves the patient about
           to be billed is really the one on screen - it is mandatory, not
           best-effort.

    Returns True only when that new Billing form is on screen. False means the
    caller must BLOCK the row - never type over whatever patient is showing.
    `close_stale_form` stays in the signature for call compatibility and is
    ignored.

    Every decision is logged through `log_fn` (the orchestrator prefixes
    these with "load:"), so the run report names the exact failing step for
    the next review: which popup blocked, which forms were on screen, and
    whether the Hospital No. field received the typed number.

    verify_mode (Slice H, 2026-10-05):
      "new_form" (DEFAULT, unchanged behaviour) waits for a Billing form that
         was not open before this call. Used when moving to a NEW patient.
      "relink" additionally accepts the ALREADY-OPEN form of the SAME patient,
         matched by billing_title_key against `expect_title`. Needed when the
         operator's rule is "type the Hospital No. again before every step":
         re-entering the same Hospital No. may refresh the same window instead
         of spawning a second one, and that is still a correct load. A window
         belonging to a DIFFERENT patient never satisfies this mode - the row
         blocks instead.
    """
    import time

    from pywinauto import mouse
    from pywinauto.keyboard import send_keys

    log = log_fn or (lambda message: None)
    clean_no = str(hospital_no or "").strip()
    if not clean_no or not clean_no.isdigit():
        log(f"load: refusing hospital number {hospital_no!r} (not digits)")
        return False

    main = find_hbsys_main()
    if main is None:
        log("load: HBSys main window not found")
        return False
    ensure_com_thread()
    if not raise_to_top(main):
        log("load: could not bring HBSys to the foreground — the GUI may cover it")
    try:
        main.set_focus()
    except Exception:
        pass
    time.sleep(0.5)

    if not clear_blocking_popups(log_fn=log):
        return False

    forms_before = set(billing_form_titles(list_open_forms()))

    click_x, click_y = hospital_no_click_point()
    log(f"load: double-clicking the Hospital No. field at ({click_x}, {click_y})")
    try:
        raise_to_top(main)
        try:
            main.set_focus()
        except Exception:
            pass
        mouse.double_click(coords=(click_x, click_y), button="left")
    except Exception as exc:
        log(f"load: double-click crashed ({type(exc).__name__}: {exc}) — COM/focus")
        return False
    time.sleep(0.4)
    send_keys("^a")
    time.sleep(0.1)
    # Type FIRST, read the field back, THEN press ENTER. If the double-click did
    # not put the caret in the field (another window had focus), the number goes
    # nowhere and the log has to say so instead of "the patient did not load".
    send_keys(clean_no)
    time.sleep(0.3)
    received = hospital_no_field_text()
    if not received:
        log("load: could not read the Hospital No. field back (no text, no OCR)")
    elif clean_no in received.replace(" ", ""):
        log(f"load: the Hospital No. field now reads {received!r} — typing landed")
    else:
        log(
            f"load: the field reads {received!r}, NOT the typed {clean_no!r} — "
            "the click or the keystrokes did not land (focus was elsewhere)"
        )
    log("load: pressing ENTER to load the patient")
    send_keys("{ENTER}")

    started = time.time()
    deadline = started + max(0.0, float(timeout))
    reported: set = set()
    want_key = billing_title_key(expect_title) if expect_title else ""
    relink = str(verify_mode or LOAD_VERIFY_NEW_FORM) == LOAD_VERIFY_RELINK
    if relink:
        log(
            f"load: relink mode - a Billing form already showing this patient "
            f"({expect_title!r}) counts as loaded, not only a brand new window"
        )
    while time.time() < deadline:
        opened = set(billing_form_titles(list_open_forms()))
        fresh = opened - forms_before
        if fresh:
            log(
                "load: verified after "
                f"{round(time.time() - started, 1)}s — "
                f"{', '.join(sorted(fresh))} is now open"
            )
            return True
        if relink and want_key:
            # Same patient's window is open AND nothing else took its place:
            # re-entering the same Hospital No. can refresh that window instead
            # of opening a second one. Compared by normalized key, never
            # byte-for-byte (HBSys titles can carry an empty middle name).
            keys = {billing_title_key(title) for title in opened}
            if want_key in keys and (not keys - {want_key}):
                log(
                    "load: verified after "
                    f"{round(time.time() - started, 1)}s — "
                    f"{', '.join(sorted(opened))} already showed this patient and "
                    "was refreshed by the Hospital No. (relink)"
                )
                return True
        elapsed = int(time.time() - started)
        if elapsed not in reported:
            reported.add(elapsed)
            log(f"load: still waiting for the Billing form ({elapsed}s elapsed)")
        time.sleep(0.2)
    log(
        f"load: not verified after {round(time.time() - started, 1)}s — no new "
        "Billing form appeared "
        f"(still open: {', '.join(sorted(billing_form_titles(list_open_forms()))) or 'none'}); "
        "the row blocks instead of guessing"
    )
    for line in diagnose_screen("load: after ENTER").splitlines():
        log(line)
    return False


# -- Admit History (confinement period selection) ------------------------------
# Shared recipe with Date Fill (select_confinement_row from
# date_fill_hbsys.hbsys_read_admission_history): open the popup, OCR the rows
# through this module, exact-match the folder's YYYYMMDD dates, double-click
# the row, then dismiss the "Rate validation" dialog. A mismatch stops for
# review — nothing else ever guesses which confinement to load.

ADMIT_HISTORY_POINT = (434, 61)          # toolbar row 1, "Admit History" slot
ADMIT_HISTORY_TOOLTIP = "Admit History"
ADMISSION_HISTORY_TITLE = "Admission History"
ADMISSION_HISTORY_CLASS = "FNWNS370"
# Popup geometry verified LIVE 2026-09-26 (HBSys maximized, 1920x1080):
#   window rect (116, 109, 770, 488); the 3 header lines end ~top+92; data
#   rows are ~27px tall; row 1 text centre is (300, 211) and a double-click
#   there loads that confinement into the Billing form and closes the popup.
ADMISSION_HISTORY_PAD = 12
ADMISSION_HISTORY_HEADER_OFFSET = 92
ADMISSION_LINE_Y_TOLERANCE = 10          # px, groups OCR words into one row


def _tooltip_matches_admit_history(label: str) -> bool:
    """True when an OCR'd tooltip reads as the 'Admit History' button.

    Tolerant of the OCR noise seen live ('Sdmit Histor'), but strict enough
    that a shifted layout produces a mismatch and the click is refused.
    """
    low = (label or "").lower()
    return "hist" in low and ("adm" in low or "dmit" in low or "smit" in low)


def find_admission_history(timeout: float = 0.0):
    """The visible 'Admission History' popup, or None (polls up to `timeout`)."""
    import time

    deadline = time.time() + max(0.0, float(timeout))
    while True:
        try:
            for window in _desktop().windows():
                try:
                    if window.class_name() != ADMISSION_HISTORY_CLASS:
                        continue
                    if not window.is_visible():
                        continue
                    if (window.window_text() or "").strip() == ADMISSION_HISTORY_TITLE:
                        return window
                except Exception:
                    continue
        except Exception:
            return None
        if time.time() >= deadline:
            return None
        time.sleep(0.2)


def open_admission_history(verify: bool = True, timeout: float = 4.0):
    """Click the 'Admit History' toolbar slot and return the popup (or None).

    The slot is confirmed by OCR-ing its PowerBuilder tooltip first, so a
    layout shift fails loudly instead of clicking a random icon (same recipe
    as `click_close_form`).
    """
    import time

    existing = find_admission_history()
    if existing is not None:
        return existing
    main = find_hbsys_main()
    if main is None:
        return None
    ensure_com_thread()
    raise_to_top(main)
    try:
        main.set_focus()
    except Exception:
        pass
    time.sleep(0.5)
    x, y = ADMIT_HISTORY_POINT
    label = toolbar_tooltip_label(x, y) if verify else ""
    if verify and label and not _tooltip_matches_admit_history(label):
        raise RuntimeError(
            f"toolbar slot ({x}, {y}) is {label!r}, not 'Admit History' - "
            "layout changed, refusing to click"
        )
    real_click(x, y)
    return find_admission_history(timeout=timeout)


def _is_date_token(word: str) -> bool:
    parts = str(word or "").split("/")
    if len(parts) != 3:
        return False
    return all(part.isdigit() for part in parts) and len(parts[2]) == 4


def _date_to_yyyymmdd(word: str) -> str:
    """"09/05/2026" -> "20260905" ('' when unparseable)."""
    if not _is_date_token(word):
        return ""
    mm, dd, yyyy = word.split("/")
    return f"{yyyy}{int(mm):02d}{int(dd):02d}"


def date_token_needs_reread(word: str) -> bool:
    """Should this grid date token be re-read on its own?

    Delegates to Date Fill's reader so both workflows agree on what is worth
    re-reading: a smeared token, and also a token that looks like a date but is
    not a real one ('06/31/2026'). Falls back to the same shape rules when the
    shared package is unavailable.
    """
    if _ensure_date_fill_imports():
        try:
            from hbsys_read_admission_history import (
                date_token_needs_reread as shared_needs,
            )

            return shared_needs(word)
        except Exception:
            pass
    text = str(word or "").strip()
    if not text or "/" not in text:
        return False
    try:
        datetime.strptime(text, "%m/%d/%Y")
        return False
    except ValueError:
        return True


def read_date_cell(image, box: tuple) -> str:
    """Re-read one Admission History date cell with a digits-only whitelist.

    A whole-grid OCR pass can read '09/22/2026' as '09/2212026'; the strict
    date matcher then drops the row that the plan actually needs. Re-reading
    that single cell fixes it. Returns '' when the cell stays unreadable, so an
    ambiguous cell is skipped rather than guessed.
    """
    if _ensure_date_fill_imports():
        try:
            from hbsys_read_admission_history import read_date_cell as shared_read

            return shared_read(image, box)
        except Exception:
            return ""
    return ""


def _ocr_grid_lines(window) -> list:
    """OCR the grid of the Admission History popup into word-lines.

    Each line is a list of (y_center, x_center, word) in ABSOLUTE screen
    coordinates. Only the data area below the 3-line header is captured.
    """
    from PIL import Image

    try:
        import pytesseract
    except Exception:
        return []

    left, top, right, bottom = rect_of(window)
    box = (
        left + ADMISSION_HISTORY_PAD,
        top + ADMISSION_HISTORY_HEADER_OFFSET,
        right - ADMISSION_HISTORY_PAD,
        bottom - ADMISSION_HISTORY_PAD,
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        return []
    image = _grab(box).convert("L")
    big = image.resize((image.width * 2, image.height * 2), Image.LANCZOS)
    data = pytesseract.image_to_data(
        big, config="--psm 6", output_type=pytesseract.Output.DICT
    )
    tokens = []
    smeared: list = []
    for index, word in enumerate(data["text"]):
        word = (word or "").strip()
        if not word:
            continue
        width = int(data["width"][index])
        height = int(data["height"][index])
        if width < 4 or height < 6:
            continue
        x = box[0] + (int(data["left"][index]) + width / 2) / 2
        y = box[1] + (int(data["top"][index]) + height / 2) / 2
        tokens.append((y, x, word))
        # A grid date whose '/' was read as a digit ('09/2212026') fails
        # _is_date_token and silently costs us the whole row. Re-read that one
        # cell with a digits-only whitelist before the line is parsed. The
        # re-read crops `image` (1x, box-relative) while the token coordinates
        # come from `big` (2x, screen-absolute), so convert both.
        if smeared_date_token(word):
            smeared.append(
                (
                    x,
                    y,
                    (left - box[0] + width / 2) / 2,
                    (top - box[1] + height / 2) / 2,
                    width / 4,
                    height / 4,
                    word,
                )
            )
    for x, y, cell_x, cell_y, half_w, half_h, word in smeared:
        fixed = read_date_cell(
            image,
            (
                int(cell_x - half_w),
                int(cell_y - half_h),
                int(cell_x + half_w),
                int(cell_y + half_h),
            ),
        )
        if fixed:
            for position, (ty, tx, tw) in enumerate(tokens):
                if tw == word and abs(ty - y) <= ADMISSION_LINE_Y_TOLERANCE:
                    tokens[position] = (ty, tx, fixed)
                    break
    lines: list = []
    for token in sorted(tokens):
        if lines and abs(lines[-1][0][0] - token[0]) <= ADMISSION_LINE_Y_TOLERANCE:
            lines[-1].append(token)
        else:
            lines.append([token])
    return lines


def admission_history_rows(window) -> list:
    """Confinement rows of the Admission History popup, parsed from its pixels.

    The grid is a PowerBuilder DataWindow with no child controls, so the rows
    are OCR'd. Each entry is::

        {
            "admission": "YYYYMMDD", "discharge": "YYYYMMDD",     # canonical
            "admission_grid": "MM/DD/YYYY",                       # raw OCR text
            "discharge_grid": "MM/DD/YYYY",                       # raw OCR text
            "encounter": "ADMIT" | "OPD" | "ER" | "EMERGENCY" | "",
            "point": (x, y),
        }

    `point` is the centre of the row's first (admission) date cell - the exact
    spot double-clicked live to load the confinement into the Billing form.
    Column order verified live: admission date, admission time, discharge
    date, discharge time, encounter type.

    The raw grid text is kept beside the canonical YYYYMMDD value because the
    matching recipe shared with Date Fill compares the RAW grid string
    (admission_grid_key / ocr_date_match_score) - an OCR smear like
    "O1/0l/2026" then fails the match instead of loading the wrong
    confinement.
    """
    rows: list = []
    for line in _ocr_grid_lines(window):
        dates = [word for _, _, word in line if _is_date_token(word)]
        if len(dates) < 2:
            continue
        admission = _date_to_yyyymmdd(dates[0])
        discharge = _date_to_yyyymmdd(dates[1])
        if not admission or not discharge:
            continue
        left, top, right, _ = rect_of(window)
        first_x = next(
            (x for _, x, word in line if word == dates[0]),
            (left + right) // 2,
        )
        rows.append(
            {
                "admission": admission,
                "discharge": discharge,
                "admission_grid": dates[0],
                "discharge_grid": dates[1],
                "encounter": _encounter_type_from_line(line),
                "point": (int(first_x), int(line[0][0])),
            }
        )
    return rows


def _encounter_type_from_line(line) -> str:
    """Encounter type word of one OCR'd grid line ('' when absent)."""
    for _, _, word in line:
        upper = str(word or "").strip().upper()
        if upper in ENCOUNTER_WORDS:
            return upper
    return ""


def ocr_date_match_score(ocr_value: str, expected_value: str) -> int:
    """Score one OCR'd grid date against the folder's grid date.

    Verbatim port of Date Fill's scoring
    (HbsysOperator.ocr_date_match_score in date_fill_hbsys/hbsys_fill_dates.py)
    so Final Bill and Date Fill accept and reject exactly the same rows:

        100  identical text
         80  same month AND day (the year was misread)
      35-65  same month AND year, day off by <= 10
         25  same month AND year, day off by more than 10
         20  same month only
          0  anything else
    """
    if ocr_value == expected_value:
        return 100
    try:
        ocr_date = datetime.strptime(str(ocr_value or "").strip(), "%m/%d/%Y")
        expected_date = datetime.strptime(
            str(expected_value or "").strip(), "%m/%d/%Y"
        )
    except ValueError:
        return 0
    if ocr_date.month == expected_date.month and (
        ocr_date.day == expected_date.day
    ):
        return 80
    if ocr_date.month == expected_date.month and (
        ocr_date.year == expected_date.year
    ):
        day_difference = abs(ocr_date.day - expected_date.day)
        if day_difference <= 10:
            return max(35, 65 - day_difference)
        return 25
    if ocr_date.month == expected_date.month:
        return 20
    return 0


# Same thresholds Date Fill uses (best_fuzzy_admission_history_row): a row has
# to clear the bar on its own AND beat the runner-up by a clear margin, else
# nothing is picked and the row BLOCKs for a human.
FUZZY_ROW_MIN_SCORE = 115
FUZZY_ROW_MIN_MARGIN = 25
FUZZY_ENCOUNTER_BONUS = 10


def fuzzy_confinement_row(
    rows,
    expected_admission: str,
    expected_discharge: str,
    *,
    min_score: int = FUZZY_ROW_MIN_SCORE,
    min_margin: int = FUZZY_ROW_MIN_MARGIN,
):
    """Best OCR-tolerant confinement row for the folder's two grid dates.

    Pure port of Date Fill's `best_fuzzy_admission_history_row`: each row is
    scored as the two date scores plus a bonus for an ADMIT encounter, the
    winner must reach `min_score` and beat the runner-up by `min_margin`, else
    None is returned and the caller stops for review.

    `rows` are anything carrying ``admission_grid`` / ``discharge_grid`` /
    ``encounter_type`` / ``point`` (the shared ConfinementRow), so the exact
    and fuzzy passes talk about the same rows.
    """
    scored = []
    for row in rows or []:
        score = ocr_date_match_score(
            getattr(row, "admission_grid", ""), expected_admission
        ) + ocr_date_match_score(
            getattr(row, "discharge_grid", ""), expected_discharge
        )
        if str(getattr(row, "encounter_type", "") or "").upper() == "ADMIT":
            score += FUZZY_ENCOUNTER_BONUS
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or scored[0][0] < min_score:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < min_margin:
        return None
    return scored[0][1]


def _yyyymmdd_to_grid(value: str) -> str:
    """"20260905" -> "09/05/2026" (the MM/DD/YYYY shape grid rows carry)."""
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        return ""
    return f"{text[4:6]}/{text[6:8]}/{text[:4]}"


def _dismiss_rate_validation_dialog(timeout: float = 2.5) -> bool:
    """Dismiss the known "Rates no longer exist" dialog after row selection.

    Shared cleanup the Final Bill agent answers itself because the agent flow
    never imports Date Fill's HbsysOperator: an HBSys "Rate validation" popup
    carrying "Rates no longer exist" is informational (the confinement loads
    fine once OK is pressed). An unknown Rate validation dialog is left open
    (returns False) so a real rate error stops the run for review.
    """
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        for window in _desktop().windows():
            try:
                if (window.window_text() or "").strip() != "Rate validation":
                    continue
                text_blob = " ".join(
                    child.window_text() for child in window.descendants()
                )
            except Exception:
                continue
            if "Rates no longer exist" not in text_blob:
                return False
            try:
                window.set_focus()
                window.child_window(title="OK", class_name="Button").click()
            except Exception:
                # Fallback stays a MOUSE click on a resolved button, never a
                # keystroke: a bare ENTER can be delivered to whatever window
                # is in front, which is how the agent used to type into the
                # claims GUI. Give up quietly if no OK can be resolved.
                try:
                    button = _visible_button_by_text(window, "OK", "&OK")
                    if button is not None:
                        click_button(button, window)
                except Exception:
                    pass
            time.sleep(0.4)
            return True
        time.sleep(0.1)
    return True


# Name of the Date Fill package beside core/ (used to make its legacy
# top-level imports work from the agent flow).
DATE_FILL_PACKAGE_DIR = "date_fill_hbsys"


def _ensure_date_fill_imports() -> bool:
    """Make the Date Fill package importable the way the tool runs it.

    `date_fill_hbsys/hbsys_read_admission_history.py` does a top-level
    ``from hbsys_rules import ...`` because the tool is normally started with
    date_fill_hbsys as the working directory. Importing it from core/agent
    therefore needs both the project root and that package directory on
    sys.path; without this the shared row matcher silently fails to import
    (the old `select_confinement` swallowed that and returned False).
    """
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    tools = root / DATE_FILL_PACKAGE_DIR
    for entry in (str(tools), str(root)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    try:
        import hbsys_read_admission_history  # noqa: F401
        import hbsys_rules  # noqa: F401
    except Exception:
        return False
    return True


def _confinement_row_class():
    """The shared ConfinementRow dataclass from Date Fill (None when missing)."""
    if not _ensure_date_fill_imports():
        return None
    try:
        from date_fill_hbsys.hbsys_read_admission_history import ConfinementRow
    except Exception:
        return None
    return ConfinementRow


def _capture_popup_image(popup, prefix: str = "final_bill_admission_history"):
    """Capture one Admission History popup via capture_as_image (None on failure).

    The Final Bill agent must OCR exactly what Date Fill OCRs: the popup
    pixels, not a full-screen grab. Full-screen grabs shrink the small popup
    grid past what Tesseract can read (live 2026-09-28: PERA 09/09-09/12 sat
    in the grid while the single-pass screen-grab OCR read zero rows).
    Returns (image, saved_path) so rows can be parsed and the PNG kept as
    run evidence under logs/.
    """
    try:
        image = popup.capture_as_image()
    except Exception:
        return None, ""
    try:
        from datetime import datetime as _dt

        DIAG_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = DIAG_LOG_DIR / (
            f"{prefix}_{_dt.now().strftime('%Y%m%d_%H%M%S')}.png"
        )
        try:
            image.save(path)
            return image, str(path)
        except Exception:
            return image, ""
    except Exception:
        return image, ""


def _merge_parsed_rows(item_passes: list) -> list:
    """Every distinct grid row any OCR pass parsed (first pass wins).

    Port of Date Fill's HbsysOperator._merge_parsed_rows: full-window
    variants plus focused per-row crops are merged on the
    (admission_date, discharge_date) key, so one weak pass cannot hide the
    row the folder needs.
    """
    if not _ensure_date_fill_imports():
        return []
    try:
        from date_fill_hbsys.hbsys_read_admission_history import (
            parse_rows_with_positions,
        )
    except Exception:
        return []
    merged: list = []
    seen: set = set()
    for items in item_passes or []:
        try:
            parsed_list = parse_rows_with_positions(items)
        except Exception:
            continue
        for parsed in parsed_list:
            try:
                key = (parsed.row.admission_date, parsed.row.discharge_date)
            except Exception:
                continue
            if key in seen:
                continue
            seen.add(key)
            merged.append(parsed)
    return merged


def _confinement_row_from_parsed(popup, parsed):
    """One shared ConfinementRow with a screen-absolute click point.

    Port of Date Fill's HbsysOperator._confinement_row: x from the popup's
    left edge, y from the popup's TOP edge (rect.top + row_y). Without the
    top offset the double-click lands on the toolbar above the popup and the
    popup never closes.
    """
    row_class = _confinement_row_class()
    if row_class is None:
        return None
    try:
        rect = rect_of(popup)
        point = (rect[0] + 72, rect[1] + int(parsed.y))
        return row_class(
            admission_grid=parsed.row.admission_date,
            discharge_grid=parsed.row.discharge_date,
            point=point,
            encounter_type=parsed.row.normalized_encounter_type,
        )
    except Exception:
        return None


def _read_confinement_rows_multi_pass(popup, *, log_fn=None) -> tuple:
    """Admission History rows from EVERY OCR pass, merged (Date Fill recipe).

    Captures the popup image (capture_as_image), runs the full-window
    variants plus the focused per-row crops from
    date_fill_hbsys.hbsys_read_admission_history, and merges every distinct
    parsed row. Returns (rows, image_path) where rows are ConfinementRow
    objects with screen-absolute click points. On total OCR failure returns
    ([], image_path) so the caller logs the evidence path and BLOCKs.
    """
    log = log_fn or (lambda message: None)
    if not _ensure_date_fill_imports():
        return [], ""
    try:
        from date_fill_hbsys.hbsys_read_admission_history import (
            read_focused_admission_row_variants,
            read_ocr_item_variants,
        )
    except Exception:
        return [], ""
    image, image_path = _capture_popup_image(popup)
    if image is None:
        log("Admit History: popup capture failed (capture_as_image)")
        return [], ""
    try:
        variants = list(read_ocr_item_variants(image_path)) if image_path else []
    except Exception:
        variants = []
    try:
        from pathlib import Path as _Path

        focused = (
            read_focused_admission_row_variants(_Path(image_path))
            if image_path
            else []
        )
    except Exception:
        focused = []
    if focused:
        log(f"Admission History added {len(focused)} focused row OCR passes")
    if image_path:
        log(f"Admission History capture: {image_path}")
    merged = _merge_parsed_rows(list(variants or []) + list(focused or []))
    rows = []
    for parsed in merged:
        row = _confinement_row_from_parsed(popup, parsed)
        if row is not None:
            rows.append(row)
    return rows, image_path


def confinement_rows_for_matching(popup, *, log_fn=None) -> list:
    """ConfinementRow objects (the shape Date Fill matches) for one popup.

    Date Fill recipe: the popup is captured once via capture_as_image, then
    EVERY full-window OCR variant plus the focused per-row crops are merged,
    so one weak pass cannot hide the row the folder needs (live 2026-09-28:
    PERA 09/09-09/12 was on screen while the old single-pass screen-grab OCR
    read zero rows and BLOCKed). Raw grid text rides along untouched for the
    shared exact/fuzzy matcher.
    """
    log = log_fn or (lambda message: None)
    # Multi-pass Date Fill path first — the only path that can see the small
    # popup grid reliably.
    try:
        rows, _ = _read_confinement_rows_multi_pass(popup, log_fn=log)
    except Exception:
        rows = []
    if rows:
        return rows
    # Fallback: the legacy single-pass screen-grab OCR (kept so a capture
    # failure still yields whatever the live pixels read).
    row_class = _confinement_row_class()
    if row_class is None:
        return []
    rows: list = []
    for parsed in admission_history_rows(popup):
        rows.append(
            row_class(
                admission_grid=parsed["admission_grid"],
                discharge_grid=parsed["discharge_grid"],
                point=parsed["point"],
                encounter_type=parsed.get("encounter", ""),
            )
        )
    return rows


def select_confinement(
    admission: str,
    discharge: str,
    timeout: float = 4.0,
    *,
    log_fn=None,
) -> bool:
    """Open Admit History and double-click the row for this confinement period.

    `admission`/`discharge` are YYYYMMDD strings (the shape the folder name
    carries). Returns True when the row was found AND the popup consumed the
    pick (it closes on success, verified live). On False the popup is left
    open so the operator can see what the list actually contains.

    The exact script Date Fill runs (select_confinement_row from
    date_fill_hbsys.hbsys_read_admission_history): open the popup, read the
    rows, exact-match on the canonical grid date, otherwise retry with the
    OCR-tolerant fuzzy row (best_fuzzy_admission_history_row scoring via
    `fuzzy_confinement_row`), then dismiss the Rate validation dialog. A
    mismatch stops for review - nothing else ever guesses which confinement to
    load, which is what keeps a multi-patient batch from billing the wrong
    stay.
    """
    import time

    from pywinauto import mouse

    log = log_fn or (lambda message: None)
    want_admission = str(admission or "").strip()
    want_discharge = str(discharge or "").strip()
    if len(want_admission) != 8 or not want_admission.isdigit():
        return False
    if len(want_discharge) != 8 or not want_discharge.isdigit():
        return False

    if not _ensure_date_fill_imports():
        log("Admit History: Date Fill matcher unavailable (import failed)")
        return False
    try:
        from date_fill_hbsys.hbsys_read_admission_history import (
            select_confinement_row,
        )
    except Exception:
        return False

    window = open_admission_history()
    if window is None:
        log("Admit History popup did not open")
        return False
    raise_to_top(window)
    time.sleep(0.3)

    want_admission_grid = _yyyymmdd_to_grid(want_admission)
    want_discharge_grid = _yyyymmdd_to_grid(want_discharge)
    seen_rows: list = []

    def read_rows_fn(popup) -> list:
        rows = confinement_rows_for_matching(popup)
        seen_rows[:] = rows
        # Say what the grid ACTUALLY showed: a "not found" verdict is only
        # useful when the rows it compared against are in the log.
        described = "; ".join(
            f"{row.admission_grid}-{row.discharge_grid}"
            + (f" {row.encounter_type}" if getattr(row, "encounter_type", "") else "")
            for row in rows
        )
        log(
            f"Admit History: OCR read {len(rows)} row(s) from the grid: "
            f"{described or 'none'}"
        )
        return rows

    def fuzzy_rows_fn():
        rows = confinement_rows_for_matching(window)
        fuzzy = fuzzy_confinement_row(
            rows, want_admission_grid, want_discharge_grid
        )
        if fuzzy is None:
            return None
        log(
            "Admit History: OCR-tolerant row "
            f"{fuzzy.admission_grid}-{fuzzy.discharge_grid} for folder "
            f"{want_admission_grid}-{want_discharge_grid}"
        )
        return fuzzy

    def double_click_fn(click_x: int, click_y: int) -> None:
        mouse.double_click(coords=(click_x, click_y), button="left")

    picked = select_confinement_row(
        open_popup_fn=lambda purpose: window if purpose == "verify open" else None,
        live=True,
        log_fn=log,
        wait_fn=time.sleep,
        read_rows_fn=read_rows_fn,
        double_click_fn=double_click_fn,
        rate_dialog_fn=_dismiss_rate_validation_dialog,
        expected_admission=want_admission_grid,
        expected_discharge=want_discharge_grid,
        fuzzy_rows_fn=fuzzy_rows_fn,
        on_selected_row_fn=None,
        context="Final Bill",
    )
    if not picked:
        if not seen_rows:
            log("Admit History: the grid returned NO rows at all (popup empty or OCR failed)")
        log(
            f"Admit History: no row matched {want_admission_grid}-{want_discharge_grid} "
            "— picking manually"
        )
        for line in diagnose_screen("admit history: nothing selected").splitlines():
            log(line)
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if find_admission_history() is None:
            return True
        time.sleep(0.2)
    return False


# -- Runner --------------------------------------------------------------------

@dataclass
class RunResult:
    """Outcome of one run() call (feeds logs / the Plan Panel evidence)."""

    success: bool = False
    steps: list = field(default_factory=list)      # StepDecision history
    reason: str = ""
    screens_seen: list = field(default_factory=list)
    actions: list = field(default_factory=list)    # human log lines

    @property
    def final_step(self) -> str:
        return self.steps[-1].step if self.steps else ""


# How many times the SAME non-terminal step may be planned in a row before the
# run stops naming that step. Three = the initial attempt plus two retries; a
# screen that did not change after three attempts will not change on the
# fourth either, so the whole step budget is not wasted on it.
SAME_STEP_REPEAT_LIMIT = 3


def trace_of(actions) -> str:
    """Compact step trail for the run report: 'open -> check x2 -> ok'.

    Consecutive repeats collapse to 'xN', so even a full 12-step stall stays
    short enough for the FAILED detail line in agent_run_*.json.
    """
    runs: list = []
    for step in actions:
        if runs and runs[-1][0] == step:
            runs[-1] = (step, runs[-1][1] + 1)
        else:
            runs.append((step, 1))
    return " -> ".join(
        step if count == 1 else f"{step} x{count}" for step, count in runs
    )


class FinalBillRunner:
    """Drive the verified Final Bill sequence, one planned step at a time.

    Every primitive is injected so the runner is headless-testable:
        detect_fn()          -> current screens.SCREEN_* (str)
        menu_fn(path)        -> select a menu item, e.g. "Billing -> Final Bill"
        forms_open_fn()      -> True while the Billing form is open
        check_final_fn()     -> tick the 'Final' box, True when ticked
        click_ok_fn()        -> click OK on the Print Options popup
        save_click_ok_fn()   -> "File save": click "OK"
        confirm_no_fn()      -> "Call Administrator": click "No"

    The defaults use the real GUI mechanics above. There is NO close step: the
    flow ends on the answered prompt and the Billing form is left open for the
    next patient's loader (operator instruction, 2026-10-02).

    Both post-OK prompts are answered with the MOUSE, the way the operator
    answers them (2026-09-28): "OK" is clicked on the "File save" prompt (the
    one in the button row at the bottom of the dialog) and "No" is clicked on
    the "Call Administrator" dialog. Nothing here depends on tab order or on
    ENTER accepting whatever happens to be focused. Each dialog is raised to
    the foreground before its own answer is sent, so a prompt that is not in
    front can never be answered by accident, and each answer is verified to
    have closed its own dialog.
    """

    def __init__(
        self,
        detect_fn=None,
        menu_fn=None,
        forms_open_fn=None,
        check_final_fn=None,
        click_ok_fn=None,
        save_click_ok_fn=None,
        confirm_no_fn=None,
        log_fn=None,
        max_steps: int = 12,
        initial_final_checked: bool = False,
        initial_bill_finalized: bool = False,
        expected_screen: str = "",
    ):
        self.detect_fn = detect_fn or self._detect_screen
        self.menu_fn = menu_fn or self._menu_select
        self.forms_open_fn = forms_open_fn or list_open_forms
        self.check_final_fn = check_final_fn or self._check_final
        self.click_ok_fn = click_ok_fn or self._click_ok
        self.save_click_ok_fn = save_click_ok_fn or self._save_click_ok
        self.confirm_no_fn = confirm_no_fn or self._confirm_no
        # No close_form_fn and no close_form_at_end: the Close Form toolbar
        # click is not part of this flow.
        self.log_fn = log_fn or (lambda message: None)
        self.max_steps = int(max_steps)
        # Resume support: a previous run may already have ticked the box or
        # committed the bill right before the agent was restarted.
        self.initial_final_checked = bool(initial_final_checked)
        self.initial_bill_finalized = bool(initial_bill_finalized)
        # What the Agent Plan's "reason" predicts (SCREEN_FILE_SAVE or
        # SCREEN_FINAL_BILL_CONFIRM). Reporting only - never a click trigger.
        self.expected_screen = str(expected_screen or "")

    # -- public API ------------------------------------------------------

    def run(self) -> RunResult:
        """Advance the workflow until DONE or BLOCKED (never loops forever)."""
        result = RunResult()
        final_checked = self.initial_final_checked
        bill_finalized = self.initial_bill_finalized
        ok_clicked = False
        prompt_answered = bool(self.initial_bill_finalized)
        # No-progress guard: consecutive plans of the SAME step mean the screen
        # is not changing under us (see SAME_STEP_REPEAT_LIMIT).
        repeat_step = ""
        repeat_count = 0
        for _ in range(self.max_steps):
            screen = self.detect_fn()
            result.screens_seen.append(screen)
            decision = plan_step(
                screen,
                expected_screen=self.expected_screen,
                final_checked=final_checked,
                bill_finalized=bill_finalized,
                ok_clicked=ok_clicked,
                prompt_answered=prompt_answered,
                forms_open=bool(self.forms_open_fn()),
            )
            result.steps.append(decision)
            self.log_fn(f"final bill step: {decision.step} ({decision.reason})")
            result.actions.append(decision.step)
            if decision.step == STEP_DONE:
                result.success = True
                result.reason = decision.reason
                return result
            if decision.step == STEP_BLOCKED:
                result.success = False
                result.reason = decision.reason
                return result
            if decision.step == repeat_step:
                repeat_count += 1
            else:
                repeat_step, repeat_count = decision.step, 1
            if repeat_count >= SAME_STEP_REPEAT_LIMIT:
                # The screen never changed under this step: stop NOW, naming
                # it, instead of burning the rest of the budget and reporting
                # only "did not finish within N steps".
                result.success = False
                result.reason = (
                    f"{decision.step} ran {repeat_count} times in a row on "
                    f"screen {screen} without changing it - check the HBSys "
                    f"display and finish this patient by hand | steps: "
                    f"{trace_of(result.actions)}"
                )
                self.log_fn(f"final bill failure: {result.reason}")
                return result
            try:
                (
                    bill_finalized,
                    final_checked,
                    ok_clicked,
                    prompt_answered,
                ) = self._perform(
                    decision.step, bill_finalized, final_checked,
                    ok_clicked, prompt_answered,
                )
            except Exception as exc:  # noqa: BLE001 - report, never continue
                result.success = False
                result.reason = f"{decision.step} failed: {exc}"
                self.log_fn(f"final bill failure: {result.reason}")
                return result
        result.success = False
        result.reason = (
            f"final bill flow did not finish within {self.max_steps} steps "
            f"| steps: {trace_of(result.actions)}"
        )
        self.log_fn(f"final bill failure: {result.reason}")
        return result

    # -- step execution --------------------------------------------------

    def _perform(
        self,
        step: str,
        bill_finalized: bool,
        final_checked: bool,
        ok_clicked: bool = False,
        prompt_answered: bool = False,
    ):
        """Run one planned step.

        Returns the updated
        ``(bill_finalized, final_checked, ok_clicked, prompt_answered)``.
        """
        if step == STEP_OPEN_FINAL_BILL:
            self.menu_fn("Billing -> Final Bill")
            return bill_finalized, final_checked, ok_clicked, prompt_answered
        if step == STEP_CHECK_FINAL:
            return (
                bill_finalized,
                bool(self.check_final_fn()),
                ok_clicked,
                prompt_answered,
            )
        if step == STEP_CLICK_OK:
            self.click_ok_fn()
            # The popup is committed; whatever prompt it hands over next is
            # answered by the next step - both are button clicks ("OK" on
            # "File save", "No" on "Call Administrator"), never a keystroke.
            return bill_finalized, True, True, prompt_answered
        if step == STEP_SAVE_CLICK_OK:
            self.save_click_ok_fn()
            # Accepting the File save prompt commits the Final Bill.
            return True, True, True, True
        if step == STEP_CONFIRM_NO:
            self.confirm_no_fn()
            # Answering the Call Administrator dialog commits the Final Bill.
            return True, True, True, True
        return bill_finalized, final_checked, ok_clicked, prompt_answered

    # -- default primitives (real GUI, used only when nothing injected) --

    @staticmethod
    def _detect_screen() -> str:
        # The detector needs dialog-body text to admit the "Note"-titled File
        # Save prompt (its own screen OCR never reads this popup): a window
        # titled exactly "Note" is real iff find_dialog_exact sees it live,
        # and dialog_text reads the body that confirms it is the real prompt.
        # Headless/GUI-less callers (pywinauto missing) get None windows here,
        # and then the Note provably cannot be admitted - never promoted.
        body_fn, marker_fn = _live_note_evidence()
        return screens.detect_screen(
            dialog_body_fn=body_fn, dialog_marker_fn=marker_fn,
        ).screen

    @staticmethod
    def _menu_select(path: str) -> None:
        from core.agent import hbsys_nav

        hbsys_nav._real_menu_select(path)

    @staticmethod
    def _check_final() -> bool:
        window = find_print_options_window()
        if window is None:
            raise RuntimeError("Print Options popup not found")
        return toggle_final_checkbox(window)

    @staticmethod
    def _click_ok() -> None:
        window = find_print_options_window()
        if window is None:
            raise RuntimeError("Print Options popup not found")
        click_print_options_ok(window)

    @staticmethod
    def _save_click_ok() -> None:
        """Answer the "File save" prompt by CLICKING its "OK" button.

        The operator clicks OK here (2026-09-28), so nothing is typed and no
        TAB is pressed - a click lands on the button itself and cannot be
        thrown off by the dialog's tab order. The prompt is raised to the
        foreground first, otherwise the click would land on the claims GUI /
        IDE that happens to be on top.
        """
        answer_save_prompt_with_ok()

    @staticmethod
    def _confirm_no() -> None:
        """Answer the "Call Administrator" prompt by CLICKING "No".

        The operator clicks No on this Yes/No prompt (2026-09-28), so the
        button is looked up by name and clicked with a real mouse click - no
        TAB, no keystroke. Clicking is deliberate here: "Yes" would replace the
        computed charges, so the answer must never depend on tab order or on
        ENTER accepting whatever happens to be focused. The dialog is raised
        to the foreground first, otherwise the click would land on the claims
        GUI / IDE that happens to be on top.
        """
        answer_confirm_prompt_with_no()


def run_final_bill(**kwargs) -> RunResult:
    """One-shot convenience: FinalBillRunner(**kwargs).run()."""
    return FinalBillRunner(**kwargs).run()

