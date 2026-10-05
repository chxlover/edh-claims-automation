"""HBSys Navigator (Slice A2 â€” Claims Agent, Phase 1 core).

Deterministic navigation between HBSys screens. Answers "paano pumunta
sa DATE_FILL / XML clicker?" by driving the CURRENT screen (from the
Detector) to the TARGET screen, one verified hop at a time.

Design (same spirit as the existing workflow_engine):
    - STRICTLY SEQUENTIAL hops, re-detecting after every hop.
    - VERIFY AFTER EVERY HOP: a hop only counts when detect_screen()
      reports the expected screen afterwards.
    - FAIL-SAFE: never continue a chain after an error; blockers route
      to Patient Review instead of being guessed past.
    - NO production tool logic is duplicated here: the Navigator only
      clicks the small set of toolbar buttons the existing tools already
      click, then hands control back so the tool itself runs.

Coordinate source of truth (DO NOT invent new coordinates):
    - eclaims / upload_att / add_claims:
      core/add_claims_uploader.py CalibratedPointsData (1920x1080,
      overridable via calibration file) â€” same values the Add Claims
      Upload tool clicks in Steps 1-3.
    - PHIC toolbar button + Claim Form 2 toolbar button:
      date_fill_hbsys/hbsys_fill_dates.py class P (P.PHIC, P.CLAIM_FORM_2).
    - The XML generator tabs (CF4/CF5/eSOA) live inside the eClaims
      screen: date_fill_hbsys/xml_generator_clicker.py class P
      (P.CF4_XML / P.CF5_XML / P.ESOA_XML).

Coordinate provider is injected (get_point) so navigation is unit-
testable without a display; the default provider reads the real
modules above.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.agent import hbsys_screens as screens

# -- Navigation targets -------------------------------------------------------

TARGET_DATE_FILL = "DATE_FILL"          # Claim Form 2 date entry ready
TARGET_XML_CF4 = "XML_CF4"              # CF4 XML generator tab open
TARGET_XML_CF5 = "XML_CF5"              # CF5 XML generator tab open
TARGET_XML_ESOA = "XML_ESOA"            # eSOA XML generator tab open
TARGET_UPLOAD_CLAIMS = "UPLOAD_CLAIMS"  # Upload Claims popup open
TARGET_ATTACHMENTS = "UPLOAD_CLAIM_ATTACHMENTS"
# Final Bill (Slice D — verified live 2026-09-25): Billing -> Final Bill
# opens the "Print Options" popup where the operator ticks the checkbox
# beside "Final" and presses OK.
TARGET_FINAL_BILL = "FINAL_BILL_OPTIONS"

ALL_TARGETS = (
    TARGET_DATE_FILL,
    TARGET_XML_CF4,
    TARGET_XML_CF5,
    TARGET_XML_ESOA,
    TARGET_UPLOAD_CLAIMS,
    TARGET_ATTACHMENTS,
    TARGET_FINAL_BILL,
)

# Hops are small (from_screen, click_name) -> expected_screen steps.
# Declared per target so routing stays data, not nested ifs.
HOP_PLANS: dict = {}



# -- Hop outcome vocabulary ---------------------------------------------------

HOP_OK = "OK"                # hop clicked + expected screen verified
HOP_BLOCKED = "BLOCKED"      # HBSys closed or a dialog blocks the chain
HOP_VERIFY_FAILED = "VERIFY_FAILED"   # click done but screen did not change
HOP_UNKNOWN_SCREEN = "UNKNOWN_SCREEN"  # current screen not recognized
HOP_UNKNOWN_TARGET = "UNKNOWN_TARGET"  # target is not in ALL_TARGETS


@dataclass
class HopRecord:
    """One attempted navigation hop (for logs / Patient Review evidence)."""

    hop_index: int
    from_screen: str
    click: str
    expected_screen: str
    outcome: str = HOP_OK
    observed_screen: str = ""
    note: str = ""


@dataclass
class NavResult:
    """Outcome of one navigate() call."""

    target: str
    success: bool
    hops: list = field(default_factory=list)
    final_screen: str = screens.SCREEN_UNKNOWN
    reason: str = ""


# -- Coordinate provider ------------------------------------------------------
#
# Default reads the SAME constants the production tools click. The mapping
# is click_name -> (x, y) in absolute 1920x1080 screen coordinates.

def _default_points() -> dict:
    points: dict = {}
    try:
        from core.add_claims_uploader import _calibrated
    except Exception:
        _calibrated = None
    if _calibrated is not None:
        points["eclaims"] = tuple(_calibrated.eclaims)
        points["upload_att"] = tuple(_calibrated.upload_att)
        points["add_claims"] = tuple(_calibrated.add_claims)
    try:
        from date_fill_hbsys.hbsys_fill_dates import P as DateFillP
    except Exception:
        DateFillP = None
    if DateFillP is not None:
        points["phic"] = (DateFillP.PHIC.x, DateFillP.PHIC.y)
        points["claim_form_2"] = (
            DateFillP.CLAIM_FORM_2.x, DateFillP.CLAIM_FORM_2.y,
        )
    try:
        from date_fill_hbsys.xml_generator_clicker import P as XmlP
    except Exception:
        XmlP = None
    if XmlP is not None:
        points["cf4_xml"] = (XmlP.CF4_XML.x, XmlP.CF4_XML.y)
        points["cf5_xml"] = (XmlP.CF5_XML.x, XmlP.CF5_XML.y)
        points["esoa_xml"] = (XmlP.ESOA_XML.x, XmlP.ESOA_XML.y)
    return points


# Declared hop plans: (click_name, expected_screen) per step, keyed by
# (current_screen, target). A hop is followed by re-detection; the chain
# continues from the OBSERVED screen, so recovery paths fall out of the
# same table (e.g. a stray dialog routes to go_home first).
HOP_TABLE: dict = {
    # -- Date Fill: main toolbar -> PHIC Beneficiaries -> Claim Form 2 ----
    (screens.SCREEN_ORDER_TRANSACTIONS, TARGET_DATE_FILL): [
        ("phic", screens.SCREEN_PHIC_BENEFICIARIES),
    ],
    (screens.SCREEN_PHIC_BENEFICIARIES, TARGET_DATE_FILL): [
        ("claim_form_2", screens.SCREEN_CLAIM_FORM_2),
    ],
    # -- XML generator tabs: eClaims toolbar -> CF4/CF5/eSOA ---------------
    (screens.SCREEN_ORDER_TRANSACTIONS, TARGET_XML_CF4): [
        ("eclaims", screens.SCREEN_ECLAIMS_DASHBOARD),
    ],
    (screens.SCREEN_ECLAIMS_DASHBOARD, TARGET_XML_CF4): [
        ("cf4_xml", screens.SCREEN_ECLAIMS_DASHBOARD),
    ],
    (screens.SCREEN_ORDER_TRANSACTIONS, TARGET_XML_CF5): [
        ("eclaims", screens.SCREEN_ECLAIMS_DASHBOARD),
    ],
    (screens.SCREEN_ECLAIMS_DASHBOARD, TARGET_XML_CF5): [
        ("cf5_xml", screens.SCREEN_ECLAIMS_DASHBOARD),
    ],
    (screens.SCREEN_ORDER_TRANSACTIONS, TARGET_XML_ESOA): [
        ("eclaims", screens.SCREEN_ECLAIMS_DASHBOARD),
    ],
    (screens.SCREEN_ECLAIMS_DASHBOARD, TARGET_XML_ESOA): [
        ("esoa_xml", screens.SCREEN_ECLAIMS_DASHBOARD),
    ],
    # -- Upload Claims popup: eClaims toolbar -> Upload Att -> Add Claims --
    (screens.SCREEN_ORDER_TRANSACTIONS, TARGET_UPLOAD_CLAIMS): [
        ("eclaims", screens.SCREEN_ECLAIMS_DASHBOARD),
    ],
    (screens.SCREEN_ECLAIMS_DASHBOARD, TARGET_UPLOAD_CLAIMS): [
        ("upload_att", screens.SCREEN_ECLAIMS_DASHBOARD),
        ("add_claims", screens.SCREEN_UPLOAD_CLAIMS),
    ],
}
# Attachments rides the same eClaims path; its screen is detected by title
# once the operator opens it, so the Navigator hands off at the dashboard.
HOP_TABLE[(screens.SCREEN_ORDER_TRANSACTIONS, TARGET_ATTACHMENTS)] = [
    ("eclaims", screens.SCREEN_ECLAIMS_DASHBOARD),
]

# -- Menu hop table -----------------------------------------------------------
# Final Bill is NOT a toolbar coordinate: it is a menu item on the HBSys menu
# bar ("Billing -> Final Bill"), which was verified live with pynput/pywinauto
# menu_select. Menu hops are declared separately so no coordinate is invented
# for something that has none. Entries: (menu_path, expected_screen).
MENU_HOP_TABLE: dict = {
    (screens.SCREEN_ORDER_TRANSACTIONS, TARGET_FINAL_BILL): [
        ("Billing -> Final Bill", screens.SCREEN_FINAL_BILL_OPTIONS),
    ],
}
# Final Bill screen -> the Print Options popup (this is the target itself).
MENU_HOP_TABLE[(screens.SCREEN_FINAL_BILL_OPTIONS, TARGET_FINAL_BILL)] = [
    ("Billing -> Final Bill", screens.SCREEN_FINAL_BILL_OPTIONS),
]

# Target -> screen that counts as "already there" (navigate short-circuits).
TARGET_SATISFIED_BY = {
    TARGET_DATE_FILL: (screens.SCREEN_CLAIM_FORM_2,),
    TARGET_XML_CF4: (screens.SCREEN_ECLAIMS_DASHBOARD,),
    TARGET_XML_CF5: (screens.SCREEN_ECLAIMS_DASHBOARD,),
    TARGET_XML_ESOA: (screens.SCREEN_ECLAIMS_DASHBOARD,),
    TARGET_UPLOAD_CLAIMS: (screens.SCREEN_UPLOAD_CLAIMS,),
    TARGET_ATTACHMENTS: (
        screens.SCREEN_ATTACHMENTS, screens.SCREEN_ECLAIMS_DASHBOARD,
    ),
    TARGET_FINAL_BILL: (screens.SCREEN_FINAL_BILL_OPTIONS,),
}

# Screens that must be cleared before any chain continues. DIALOG goes to
# go_home (safe recovery); popups that already match a target satisfy it.
BLOCKER_SCREENS = (
    screens.SCREEN_HBSYS_CLOSED,
    screens.SCREEN_DIALOG,
    screens.SCREEN_UNKNOWN,
)

# Maximum hops per navigate() call (fail-safe against loops).
MAX_HOPS = 8


# -- Navigator ----------------------------------------------------------------

class Navigator:
    """Drive HBSys from its CURRENT screen to a TARGET screen.

    All desktop interaction is injected (detect_fn, click_fn, ocr_fn) so
    navigation is fully unit-testable. The default wiring uses the real
    Detector + pyautogui clicks + no OCR.
    """

    def __init__(
        self,
        detect_fn=None,
        click_fn=None,
        ocr_fn=None,
        points=None,
        log_fn=None,
        menu_fn=None,
    ):
        self.detect_fn = detect_fn or screens.detect_screen
        self.click_fn = click_fn or _real_click
        self.menu_fn = menu_fn or _real_menu_select
        self.ocr_fn = ocr_fn or (lambda: "")
        self.points = dict(points) if points is not None else _default_points()
        self.log_fn = log_fn or (lambda message: None)

    # -- public API ------------------------------------------------------

    def where_am_i(self) -> screens.ScreenResult:
        """Detect the current screen (pure observation, no clicks)."""
        return self._detect()

    def navigate(self, target: str) -> NavResult:
        """Drive HBSys to *target*, verifying every hop. Never loops."""
        if target not in ALL_TARGETS:
            return NavResult(
                target=target, success=False,
                reason=f"unknown navigation target: {target!r}",
            )

        result = NavResult(target=target, success=False)
        for _ in range(MAX_HOPS):
            current = self._detect()
            result.final_screen = current.screen

            if current.screen in TARGET_SATISFIED_BY[target]:
                result.success = True
                result.reason = f"already at target ({current.screen})"
                return result

            if current.screen in BLOCKER_SCREENS:
                if current.screen == screens.SCREEN_HBSYS_CLOSED:
                    result.reason = (
                        "HBSys is CLOSED â€” open HBSys and try again"
                    )
                    result.hops.append(HopRecord(
                        hop_index=len(result.hops),
                        from_screen=current.screen,
                        click="(none)",
                        expected_screen=current.screen,
                        outcome=HOP_BLOCKED,
                        observed_screen=current.screen,
                        note=result.reason,
                    ))
                    return result
                recovered = self._recover_blocker(result)
                if not recovered:
                    return result
                continue

            menu_hops = MENU_HOP_TABLE.get((current.screen, target))
            if menu_hops:
                done = self._run_hops(result, menu_hops, self.menu_fn, "menu")
                if done:
                    return result
                continue

            hops = HOP_TABLE.get((current.screen, target))
            if not hops:
                result.reason = (
                    f"no hop plan from {current.screen} to {target}; "
                    "routing to Patient Review instead of guessing"
                )
                result.hops.append(HopRecord(
                    hop_index=len(result.hops),
                    from_screen=current.screen,
                    click="(none)",
                    expected_screen=current.screen,
                    outcome=HOP_UNKNOWN_SCREEN,
                    observed_screen=current.screen,
                    note=result.reason,
                ))
                return result

            done = self._run_hops(result, hops)
            if done:
                return result

        result.reason = (
            f"did not reach {target} within {MAX_HOPS} hops "
            f"(stuck at {result.final_screen})"
        )
        return result

    def go_home(self) -> NavResult:
        """Safe recovery: clear blockers back to the main toolbar screen.

        Presses Escape (dismisses dialogs/popups one level at a time) and
        re-detects until ORDER_TRANSACTIONS is observed or hops run out.
        Never clicks toolbar buttons â€” dismissal only.
        """
        result = NavResult(
            target=screens.SCREEN_ORDER_TRANSACTIONS, success=False,
        )
        for _ in range(MAX_HOPS):
            current = self._detect()
            result.final_screen = current.screen
            if current.screen == screens.SCREEN_ORDER_TRANSACTIONS:
                result.success = True
                result.reason = "recovered to the main toolbar screen"
                return result
            if current.screen == screens.SCREEN_HBSYS_CLOSED:
                result.reason = "HBSys is CLOSED â€” open HBSys and try again"
                return result
            record = HopRecord(
                hop_index=len(result.hops),
                from_screen=current.screen,
                click="escape",
                expected_screen=screens.SCREEN_ORDER_TRANSACTIONS,
                observed_screen=current.screen,
            )
            try:
                self._press_escape()
            except Exception as exc:
                record.outcome = HOP_BLOCKED
                record.note = f"escape failed: {exc}"
                result.hops.append(record)
                result.reason = record.note
                return result
            observed = self._detect()
            record.observed_screen = observed.screen
            if observed.screen == screens.SCREEN_ORDER_TRANSACTIONS:
                record.outcome = HOP_OK
                record.note = "dialog/popup dismissed"
                result.hops.append(record)
                result.success = True
                result.final_screen = observed.screen
                result.reason = "recovered to the main toolbar screen"
                return result
            record.outcome = HOP_VERIFY_FAILED
            record.note = f"still at {observed.screen} after Escape"
            result.hops.append(record)
            result.final_screen = observed.screen
        result.reason = (
            f"could not recover to the main screen within {MAX_HOPS} hops "
            f"(stuck at {result.final_screen})"
        )
        return result

    # -- internals ---------------------------------------------------------

    def _detect(self) -> screens.ScreenResult:
        try:
            ocr = self.ocr_fn()
        except Exception:
            ocr = ""
        try:
            return self.detect_fn(ocr_text=ocr)
        except TypeError:
            return self.detect_fn()

    def _run_hops(self, result: NavResult, hops: list, act_fn=None, kind: str = "click") -> bool:
        """Run one hop-plan step list. True when navigate() should stop.

        ``act_fn`` is the primitive used for each hop (coordinate click or
        menu selection); hop names therefore stay the same vocabulary in
        both plans, only the primitive differs.
        """
        act_fn = act_fn or self.click_fn
        for click_name, expected in hops:
            current = self._detect()
            result.final_screen = current.screen
            if current.screen in TARGET_SATISFIED_BY[result.target]:
                result.success = True
                result.reason = f"reached target ({current.screen})"
                return True
            record = HopRecord(
                hop_index=len(result.hops),
                from_screen=current.screen,
                click=click_name,
                expected_screen=expected,
            )
            if kind == "menu":
                self.log_fn(
                    f"nav menu hop {record.hop_index}: {click_name} -> {expected}"
                )
                try:
                    act_fn(click_name)
                except Exception as exc:
                    record.outcome = HOP_BLOCKED
                    record.observed_screen = current.screen
                    record.note = f"menu selection failed: {exc}"
                    result.hops.append(record)
                    result.reason = record.note
                    return True
                observed = self._detect()
                record.observed_screen = observed.screen
                result.final_screen = observed.screen
                if observed.screen == expected:
                    record.outcome = HOP_OK
                    record.note = f"verified {observed.screen} (menu selection)"
                    result.hops.append(record)
                    if observed.screen in TARGET_SATISFIED_BY[result.target]:
                        result.success = True
                        result.reason = f"reached target ({observed.screen})"
                        return True
                    continue
                record.outcome = HOP_VERIFY_FAILED
                record.note = (
                    f"expected {expected} but observed {observed.screen}; "
                    "re-routing from the observed screen"
                )
                result.hops.append(record)
                return False
            point = self.points.get(click_name)
            if point is None:
                record.outcome = HOP_VERIFY_FAILED
                record.observed_screen = current.screen
                record.note = (
                    f"no coordinates for click {click_name!r}; "
                    "calibration missing â€” route to Patient Review"
                )
                result.hops.append(record)
                result.reason = record.note
                return True
            self.log_fn(f"nav hop {record.hop_index}: {click_name} -> {expected}")
            try:
                act_fn(point[0], point[1])
            except Exception as exc:
                record.outcome = HOP_BLOCKED
                record.observed_screen = current.screen
                record.note = f"click failed: {exc}"
                result.hops.append(record)
                result.reason = record.note
                return True
            observed = self._detect()
            record.observed_screen = observed.screen
            result.final_screen = observed.screen
            if observed.screen == expected:
                record.outcome = HOP_OK
                record.note = f"verified {observed.screen}"
                result.hops.append(record)
                if observed.screen in TARGET_SATISFIED_BY[result.target]:
                    result.success = True
                    result.reason = f"reached target ({observed.screen})"
                    return True
                continue
            record.outcome = HOP_VERIFY_FAILED
            record.note = (
                f"expected {expected} but observed {observed.screen}; "
                "re-routing from the observed screen"
            )
            result.hops.append(record)
            return False
        return False

    def _recover_blocker(self, result: NavResult) -> bool:
        """Clear a DIALOG/UNKNOWN blocker via go_home. True when cleared."""
        home = self.go_home()
        result.hops.extend(home.hops)
        result.final_screen = home.final_screen
        if not home.success:
            result.reason = (
                f"blocked at {result.final_screen} and recovery failed: "
                f"{home.reason}; routing to Patient Review"
            )
            return False
        self.log_fn(f"nav recovery: {home.reason}")
        return True

    def _press_escape(self) -> None:
        try:
            import pyautogui
        except Exception as exc:
            raise RuntimeError(f"pyautogui unavailable: {exc}")
        pyautogui.press("esc")


def _real_click(x: int, y: int) -> None:
    try:
        import pyautogui
    except Exception as exc:
        raise RuntimeError(f"pyautogui unavailable: {exc}")
    pyautogui.click(x, y)


def find_main_window():
    """HBSys main frame (class FNWND370, title contains HOMIS/HBSys).

    Verified live 2026-09-25: the HBSys frame is class FNWND370 and its title
    is "HBSys - Hospital Operations and Management Information System (HOMIS)
    Billing System", so both markers are matched.
    """
    try:
        from pywinauto import Desktop
    except Exception as exc:
        raise RuntimeError(f"pywinauto unavailable: {exc}")
    fallback = None
    for window in Desktop(backend="win32").windows():
        try:
            title = (window.window_text() or "").upper()
            if window.class_name() != "FNWND370":
                continue
            if "HOMIS" in title:
                return window
            if "HBSYS" in title and "(HOMIS" not in title:
                if fallback is None:
                    fallback = window
        except Exception:
            continue
    return fallback


def _real_menu_select(menu_path: str) -> None:
    """Select a HBSys menu item by PATH (e.g. 'Billing -> Final Bill').

    Verified live 2026-09-25: pywinauto's menu_select drives the real PowerBuilder
    menu without coordinates, which is why the Final Bill hop uses a menu path
    instead of a fabricated toolbar coordinate.
    """
    window = find_main_window()
    if window is None:
        raise RuntimeError("HBSys main window not found for menu selection")
    try:
        window.set_focus()
    except Exception:
        pass
    window.menu_select(menu_path)


def navigate_to(target: str, **kwargs) -> NavResult:
    """One-shot convenience: Navigator(**kwargs).navigate(target)."""
    return Navigator(**kwargs).navigate(target)

