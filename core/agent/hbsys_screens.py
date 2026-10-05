"""HBSys Screen Detector (Slice A1 — Claims Agent, Phase 1 core).

Deterministic screen detection for HBSys. Answers "nasaan ang HBSys
ngayon?" WITHOUT clicking or typing anything — pure observation.

Contract:
    from core.agent.hbsys_screens import detect_screen
    result = detect_screen()
    result.screen   # one of the SCREEN_* constants below
    result.titles   # desktop window titles seen (debug)
    result.detail   # extra evidence (e.g. matched title)

Design notes:
    - Every detector is a pure function of observable desktop state
      (window titles + optional OCR text). No mouse, no keyboard.
    - Priority order matters: the most specific screens are checked first
      (popups/dialogs before the main app screens they sit on top of).
    - Unknown tool modules must never guess: UNKNOWN means "safe to do
      nothing yet, route to the Navigator's go_home()".
    - Window listing is injected (list_windows) so the whole detector is
      unit-testable without HBSys or a display.

Screen vocabulary (stable; the Navigator routes on these values):
    HBSYS_CLOSED        — no HBSys/HOMIS window on the desktop
    ORDER_TRANSACTIONS  — PHIC/HBSys main screen (toolbar row with
                          the eClaims button at the top)
    ECLAIMS_DASHBOARD   — eClaims screen is open (toolbar with
                          Upload Att / Add Claims sits under it)
    UPLOAD_CLAIMS       — Upload Claims popup is open
    UPLOAD_CLAIM_ATTACHMENTS — Upload Claim Attachments screen/popup
    ADMISSION_HISTORY   — Admission History popup is open
    PHIC_BENEFICIARIES  — PhilHealth Beneficiaries screen is open
    CLAIM_FORM_2        — Claim Form 2 screen is open
    CLAIM_FORM_4        — Claim Form 4 view is open
    FINAL_BILL_OPTIONS  — the Final Bill "Print Options" popup is open
                          (checkbox beside "Final" + OK button)
    FINAL_BILL_CONFIRM  — the "Call Administrator" Yes/No prompt that
                          HBSys shows while finalizing the bill
    FILE_SAVE           — the "File save" prompt shown on the other
                          Final Bill branch (OK -> then Close Form)
    DIALOG              — a blocking dialog/popup is on top
                          (OK/Yes/No buttons, Select Encounter,
                          PHIC save message, PHIC Details)
    UNKNOWN             — HBSys is open but the current screen is
                          not recognized
"""

from __future__ import annotations

from dataclasses import dataclass

# -- Screen vocabulary --------------------------------------------------------

SCREEN_HBSYS_CLOSED = "HBSYS_CLOSED"
SCREEN_ORDER_TRANSACTIONS = "ORDER_TRANSACTIONS"
SCREEN_ECLAIMS_DASHBOARD = "ECLAIMS_DASHBOARD"
SCREEN_UPLOAD_CLAIMS = "UPLOAD_CLAIMS"
SCREEN_ATTACHMENTS = "UPLOAD_CLAIM_ATTACHMENTS"
SCREEN_ADMISSION_HISTORY = "ADMISSION_HISTORY"
SCREEN_PHIC_BENEFICIARIES = "PHIC_BENEFICIARIES"
SCREEN_CLAIM_FORM_2 = "CLAIM_FORM_2"
SCREEN_CLAIM_FORM_4 = "CLAIM_FORM_4"
# Final Bill flow (Slice D — verified live on 2026-09-25):
#   Billing -> Final Bill  ->  PRINT OPTIONS (FNWNS370)
#   check "Final" + OK     ->  CALL ADMINISTRATOR (#32770, Yes/No)
#   No                      ->  popup closes, bill finalized
#   (other branch) File save popup -> OK -> Close Form toolbar button
SCREEN_FINAL_BILL_OPTIONS = "FINAL_BILL_OPTIONS"
SCREEN_FINAL_BILL_CONFIRM = "FINAL_BILL_CONFIRM"
SCREEN_FILE_SAVE = "FILE_SAVE"
SCREEN_DIALOG = "DIALOG"
SCREEN_UNKNOWN = "UNKNOWN"

ALL_SCREENS = (
    SCREEN_HBSYS_CLOSED,
    SCREEN_ORDER_TRANSACTIONS,
    SCREEN_ECLAIMS_DASHBOARD,
    SCREEN_UPLOAD_CLAIMS,
    SCREEN_ATTACHMENTS,
    SCREEN_ADMISSION_HISTORY,
    SCREEN_PHIC_BENEFICIARIES,
    SCREEN_CLAIM_FORM_2,
    SCREEN_CLAIM_FORM_4,
    SCREEN_FINAL_BILL_OPTIONS,
    SCREEN_FINAL_BILL_CONFIRM,
    SCREEN_FILE_SAVE,
    SCREEN_DIALOG,
    SCREEN_UNKNOWN,
)

# -- Window-title evidence ----------------------------------------------------
#
# Mirrors the titles the existing tools already key on:
#   * Upload Claims popup ....... "Upload Claims" (core/add_claims_uploader.py)
#   * Admission History popup ... "Admission History"
#       (date_fill_hbsys/hbsys_read_admission_history.py)
#   * Select Encounter popup .... "Select Encounter"
#       (date_fill_hbsys/xml_generator_clicker.py)
#   * Generator result windows .. "HOSPITAL..." + CF4/CF5/eSOA XML label
#       (date_fill_hbsys/xml_generator_clicker.py)
#   * Main HBSys window ......... "HBSys" / "HOMIS" title markers
#       (date_fill_hbsys/hbsys_window.py)

TITLE_UPLOAD_CLAIMS = "Upload Claims"
TITLE_ATTACHMENTS = "Upload Claim Attachments"
TITLE_ADMISSION_HISTORY = "Admission History"
TITLE_SELECT_ENCOUNTER = "Select Encounter"
TITLE_PHIC_DETAILS = "PHIC Details"
# Final Bill flow titles (verified live 2026-09-25):
#   "Print Options"     .... FNWNS370 popup holding Button id 1001 "Final"
#                            (checkbox) + Button id 1002 "OK"
#   "Call Administrator" ... #32770 Yes/No prompt with the text
#                            "File exist in Patient Charges Detail.. do you
#                             want to replace the Computation?"
#   "File save"         .... the other branch's save prompt (OK -> Close Form)
TITLE_PRINT_OPTIONS = "Print Options"
TITLE_FINAL_BILL_CONFIRM = "Call Administrator"
TITLE_FILE_SAVE = "File save"
# Live 2026-10-02: HBSys raises the "File save" note with the window
# title "Note" and the words "File Save" as its body text, so the title
# alone never matched and the flow never reached the prompt's OK button.
TITLE_FILE_SAVE_ALIASES = ("File save", "File Save", "Note")

# The Agent Plan's own "reason" says WHY the row needs a Final Bill, and the
# two reasons raise two DIFFERENT prompts (live 2026-10-02):
#   "MISMATCH ang itemized vs grouped charges" -> "Call Administrator" (Yes/No)
#   "NO FINAL BILL sa HBSys"                   -> "Note" / "File Save" (OK)
# Only ONE prompt is ever expected for a row; the other must not be clicked.
REASON_MARKERS_FILE_SAVE = ("no final bill", "file save")
REASON_MARKERS_CONFIRM_NO = ("mismatch", "itemized", "grouped")


def expected_prompt_screen(reason: str) -> str:
    """Pure: the prompt this plan reason predicts, or "" when it says neither.

    Used for reporting and cross-checking only. Detection stays authoritative:
    a row whose reason disagrees with the screen is still handled by what is
    actually on screen, never by what the plan hoped would appear.
    """
    text = str(reason or "").casefold()
    if not text.strip():
        return ""
    if any(marker in text for marker in REASON_MARKERS_FILE_SAVE):
        return SCREEN_FILE_SAVE
    if any(marker in text for marker in REASON_MARKERS_CONFIRM_NO):
        return SCREEN_FINAL_BILL_CONFIRM
    return ""

GENERATOR_LABELS = ("CF4 XML", "CF5 XML", "eSOA XML", "CF4", "CF5", "eSOA")


@dataclass(frozen=True)
class ScreenResult:
    """Outcome of one detect_screen() call."""

    screen: str
    titles: tuple = ()
    detail: str = ""
    # Titles owned by HBSys (subset of `titles`); empty when HBSys is closed.
    hbsys_titles: tuple = ()


def _upper(text: str) -> str:
    return (text or "").upper()



# -- Detector -----------------------------------------------------------------

def detect_screen(
    list_windows=None, ocr_text: str = "", dialog_body_fn=None,
    dialog_marker_fn=None,
) -> ScreenResult:
    """Detect the current HBSys screen. Never clicks or types.

    Args:
        list_windows: zero-arg callable returning an iterable of objects
            with ``window_text()`` (pywinauto window wrappers). When None,
            the real desktop is enumerated (requires pywinauto + display).
        ocr_text: optional OCR text of the focused HBSys window, used to
            disambiguate full-screen states (Beneficiaries / CF2 / CF4 /
            eClaims dashboard) whose titles are all the main HBSys title.
        dialog_body_fn: optional callable taking a dialog title and returning
            that dialog's body text (e.g. the real action module's
            ``dialog_text(find_dialog_exact(title))``). Used ONLY to confirm
            the "Note"-titled File Save prompt - the runner feeds dialog-body
            text because its own screen OCR never reads this popup. Headless
            callers leave it None; a "Note" window is then never treated as
            the File save prompt.
        dialog_marker_fn: optional callable taking marker(s) and returning a
            live window (e.g. the action module's ``find_dialog_exact``). Used
            ONLY to prove the "Note" window is really open - a body text hit
            without a live window never admits the Note. Headless callers
            leave it None (then even a confirming body never admits it).
    """
    titles = _collect_titles(list_windows)

    hbsys_titles = tuple(
        title for title in titles if _is_hbsys_title(title)
    )
    popup_titles = tuple(title for title in titles if _is_popup_title(title))
    if not hbsys_titles and not popup_titles:
        return ScreenResult(
            screen=SCREEN_HBSYS_CLOSED,
            titles=titles,
            detail="no HBSys/HOMIS window on the desktop",
        )

    # Most-specific popups first: they sit on top of the main screens.
    # Popup titles are matched even when the main HBSys window title is
    # temporarily hidden (minimized behind the popup).
    #
    # "Note" is not a known popup title, so it is filtered out above. HBSys
    # still raises the File Save prompt under that title (live 2026-10-02):
    #dialog_body_fn must return body text CONFIRMING "File save" - title-only
    # admission would let anyone else's "Note" dialog hijack this screen.
    # The note candidates are resolved in _match_popup by title ORDER:
    # dialog_marker_fn(("Note",)) proves the window is really open, so the
    # "Note" rides along for the match and note_ok confirms it is the real
    # prompt. Only ONE "Note" is ever genuinely open on the desktop, and a
    # body text hit without a live window never admits it (see
    # test_note_without_body_confirmation_is_never_file_save).
    if dialog_marker_fn is not None and dialog_marker_fn(("Note",)):
        note_titles = ("Note",)
    else:
        note_titles = ()
    match = _match_popup(
        hbsys_titles + popup_titles + note_titles, ocr_text,
        dialog_body_fn=dialog_body_fn,
    )
    if match is not None:
        return ScreenResult(
            screen=match[0], titles=titles,
            detail=match[1], hbsys_titles=hbsys_titles,
        )

    # Full-screen states share the main HBSys title; OCR text decides.
    screen, detail = _match_full_screen_state(ocr_text)
    return ScreenResult(
        screen=screen, titles=titles,
        detail=detail, hbsys_titles=hbsys_titles,
    )


def _collect_titles(list_windows) -> tuple:
    if list_windows is None:
        try:
            from pywinauto import Desktop
        except Exception:
            return ()
        try:
            windows = Desktop(backend="win32").windows()
        except Exception:
            return ()
        return _read_titles(windows)
    try:
        return _read_titles(list_windows())
    except Exception:
        return ()


def _read_titles(windows) -> tuple:
    titles = []
    for window in windows or ():
        try:
            cls = window.class_name() if hasattr(window, "class_name") else ""
        except Exception:
            cls = ""
        # Never let a browser tab pose as HBSys (mirrors
        # date_fill_hbsys/hbsys_window.py: Chrome/Edge top-level windows use
        # the Chrome_WidgetWin_* class even when the tab title says HBSys).
        if isinstance(cls, str) and cls.startswith("Chrome_WidgetWin_"):
            continue
        try:
            title = (window.window_text() or "").strip()
        except Exception:
            continue
        if title:
            titles.append(title)
    return tuple(titles)


# Browser tab titles end with the browser name ("... - Google Chrome") while
# the real HBSys title starts with it ("HBSys - Hospital Operations...").
BROWSER_TAB_MARKERS = (
    " - GOOGLE CHROME",
    " - MICROSOFT EDGE",
    " - MOZILLA FIREFOX",
    "CHROME_WIDGETWIN",
)


def _looks_like_browser_tab(title: str) -> bool:
    upper = _upper(title)
    return any(marker in upper for marker in BROWSER_TAB_MARKERS)


def _is_hbsys_title(title: str) -> bool:
    if _looks_like_browser_tab(title):
        return False
    upper = _upper(title)
    return "HBSYS" in upper or "HOMIS" in upper or "HOSPITAL" in upper


POPUP_TITLES = (
    TITLE_UPLOAD_CLAIMS,
    TITLE_ADMISSION_HISTORY,
    TITLE_SELECT_ENCOUNTER,
    TITLE_PHIC_DETAILS,
    TITLE_PRINT_OPTIONS,
    TITLE_FINAL_BILL_CONFIRM,
    TITLE_FILE_SAVE,
)


def _is_popup_title(title: str) -> bool:
    """True for known HBSys tool popups by exact title or attachment label."""
    if _looks_like_browser_tab(title):
        return False
    stripped = (title or "").strip()
    if stripped in POPUP_TITLES:
        return True
    upper = _upper(title)
    if TITLE_ATTACHMENTS in upper or ("ATTACHMENT" in upper and "UPLOAD" in upper):
        return True
    return any(label in title and "HOSPITAL" in upper for label in GENERATOR_LABELS)


def _match_popup(titles: tuple, ocr_text: str = "", dialog_body_fn=None):
    """Match popup/dialog screens. Returns (screen, detail) or None.

    Post-OK prompts win BEFORE Print Options (live 2026-10-02): after OK is
    clicked HBSys leaves Print Options open BEHIND the prompt it raises
    ("Note"/"File save" or "Call Administrator"). The prompt is what the
    operator answers next, so it is resolved in its own pass ahead of Print
    Options - the old per-title order depended on window enumeration order,
    and when Print Options happened to come first the runner kept reading
    FINAL_BILL_OPTIONS and BLOCKed as "Print Options still open", even
    though the File save OK (971,593 / +(963,560)) was already on screen
    waiting to be clicked.
    """
    file_save_keys = {
        alias.lower() for alias in TITLE_FILE_SAVE_ALIASES if alias != "Note"
    }
    note_ok = (
        callable(dialog_body_fn)
        and "file save" in str(dialog_body_fn("Note") or "").lower()
    ) or ("file save" in str(ocr_text or "").lower())
    # -- Pass 1: the post-OK prompts ("Call Administrator", the File save
    #    prompt incl. its "Note" title) anywhere among the titles.
    #    "Note" can only be admitted when it is provably the File save
    #    prompt: the body text (or screen OCR) says "File save". Live the
    #    detector's dialog-body reader refuses a window that is not really
    #    open (find_dialog_exact), so anyone else's "Note" can never pass.
    #    Headless dialog_body_fn MUST enforce the same rule (see
    #    listing_with_desktop in tests/test_agent_hbsys_screens.py).
    for title in titles:
        upper = _upper(title)
        if title.strip() == TITLE_FINAL_BILL_CONFIRM:
            return (
                SCREEN_FINAL_BILL_CONFIRM,
                f"Final Bill confirmation prompt: {title!r} (Yes/No -> No)",
            )
        if title.strip().lower() in file_save_keys or (
            title.strip().lower() == "note" and note_ok
        ):
            return (
                SCREEN_FILE_SAVE,
                f"Final Bill file save prompt: {title!r} (OK -> Close Form)",
            )
    # -- Pass 2: Print Options, then everything generic (unchanged below). --
    for title in titles:
        upper = _upper(title)
        if title.strip() == TITLE_ADMISSION_HISTORY:
            return SCREEN_ADMISSION_HISTORY, f"popup open: {title!r}"
        if title.strip() == TITLE_PRINT_OPTIONS:
            return (
                SCREEN_FINAL_BILL_OPTIONS,
                f"Final Bill print options popup: {title!r} "
                "(Final checkbox + OK)",
            )
        if TITLE_UPLOAD_CLAIMS in title and "ATTACHMENT" not in upper:
            return SCREEN_UPLOAD_CLAIMS, f"popup open: {title!r}"
        if TITLE_ATTACHMENTS in upper or (
            "ATTACHMENT" in upper and "UPLOAD" in upper
        ):
            return SCREEN_ATTACHMENTS, f"screen open: {title!r}"
        if title.strip() == TITLE_SELECT_ENCOUNTER:
            return (
                SCREEN_DIALOG,
                f"blocking dialog: {title!r} (needs close/dismiss)",
            )
        if TITLE_PHIC_DETAILS in upper:
            return (
                SCREEN_DIALOG,
                f"blocking child window: {title!r} (needs close)",
            )
        for label in GENERATOR_LABELS:
            if label in title and "HOSPITAL" in upper:
                return SCREEN_DIALOG, f"generator result window: {title!r}"
    for title in titles:
        upper = _upper(title)
        if title.strip().upper() == "OK" or "PHIC SAVE" in upper:
            return SCREEN_DIALOG, f"blocking dialog: {title!r}"
    return None


def _match_full_screen_state(ocr_text: str):
    """Match states that share the main HBSys title using OCR evidence."""
    blob = _upper(ocr_text or "")
    if not blob:
        # No OCR evidence: the main toolbar screen is the safe assumption
        # because every HBSys flow passes through it.
        return (
            SCREEN_ORDER_TRANSACTIONS,
            "no OCR evidence; defaulting to main toolbar screen",
        )
    if "PHILHEALTH BENEFICIARIES" in blob or "PHIC BENEFICIARIES" in blob:
        if "CLAIM FORM 4" in blob or "CF4" in blob:
            return (
                SCREEN_CLAIM_FORM_4,
                "OCR shows Beneficiaries on the Claim Form 4 view",
            )
        return (
            SCREEN_PHIC_BENEFICIARIES,
            "OCR shows the PhilHealth Beneficiaries grid",
        )
    if "CLAIM FORM 2" in blob or "CF2" in blob:
        return SCREEN_CLAIM_FORM_2, "OCR shows Claim Form 2"
    if "CLAIM FORM 4" in blob or ("CF4" in blob and "XML" not in blob):
        return SCREEN_CLAIM_FORM_4, "OCR shows Claim Form 4"
    if "UPLOAD" in blob and "ATTACH" in blob:
        return (
            SCREEN_ECLAIMS_DASHBOARD,
            "OCR shows the eClaims toolbar (Upload Att visible)",
        )
    if "ECLAIMS" in blob:
        return SCREEN_ECLAIMS_DASHBOARD, "OCR shows the eClaims screen"
    return (
        SCREEN_ORDER_TRANSACTIONS,
        "OCR shows the main toolbar screen",
    )


# -- Convenience --------------------------------------------------------------

def is_hbsys_open(list_windows=None) -> bool:
    """True when any HBSys/HOMIS window is on the desktop."""
    return detect_screen(list_windows=list_windows).screen != SCREEN_HBSYS_CLOSED


def describe(result: ScreenResult) -> str:
    """One-line human summary of a ScreenResult (for logs/reports)."""
    if result.screen == SCREEN_HBSYS_CLOSED:
        return "HBSys is CLOSED (no HBSys/HOMIS window found)"
    return f"HBSys screen: {result.screen} ({result.detail})"
