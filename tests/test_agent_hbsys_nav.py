"""Unit tests for the HBSys Navigator (Slice A2 — Claims Agent).

Desktop interaction is injected (scripted screen reads + recorded clicks),
so every test runs headless — no HBSys, no display, no real clicks.

Run from the project root:

    python -m unittest tests.test_agent_hbsys_nav
    python tests/test_agent_hbsys_nav.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import hbsys_nav as nav  # noqa: E402
from core.agent import hbsys_screens as screens  # noqa: E402

POINTS = {
    "phic": (486, 58),
    "claim_form_2": (84, 58),
    "eclaims": (86, 59),
    "upload_att": (136, 101),
    "add_claims": (83, 101),
    "cf4_xml": (486, 58),
    "cf5_xml": (536, 58),
    "esoa_xml": (586, 58),
}


class ScriptedDesktop:
    """Fake desktop: scripted reads; each click jumps per the clicks map."""

    def __init__(self, script):
        self._reads = list(script.get("reads", []))
        self._on_click = dict(script.get("clicks", {}))
        self.clicks = []
        self.escapes = 0
        self.current = script.get("start", screens.SCREEN_ORDER_TRANSACTIONS)

    def detect(self, ocr_text=""):
        if self._reads:
            self.current = self._reads.pop(0)
        return screens.ScreenResult(screen=self.current, titles=(), detail="")

    def click(self, x, y):
        self.clicks.append((x, y))
        for name, point in POINTS.items():
            if (x, y) == point and name in self._on_click:
                self.current = self._on_click[name]
                return
        # Unknown click: screen does not change (verify will fail).

    def press_escape(self):
        self.escapes += 1
        if self.current in (
            screens.SCREEN_DIALOG,
            screens.SCREEN_UPLOAD_CLAIMS,
            screens.SCREEN_ADMISSION_HISTORY,
        ):
            self.current = screens.SCREEN_ORDER_TRANSACTIONS


def make_nav(desktop):
    navigator = nav.Navigator(
        detect_fn=desktop.detect,
        click_fn=desktop.click,
        points=dict(POINTS),
    )
    navigator._press_escape = desktop.press_escape
    return navigator


class NavigateTests(unittest.TestCase):
    def test_unknown_target_refused(self):
        desktop = ScriptedDesktop({})
        result = make_nav(desktop).navigate("FINAL_BILL")
        self.assertFalse(result.success)
        self.assertEqual(desktop.clicks, [])
        self.assertIn("unknown navigation target", result.reason)

    def test_already_there_short_circuits_without_clicks(self):
        desktop = ScriptedDesktop({"start": screens.SCREEN_CLAIM_FORM_2})
        result = make_nav(desktop).navigate(nav.TARGET_DATE_FILL)
        self.assertTrue(result.success)
        self.assertEqual(desktop.clicks, [])

    def test_closed_hbsys_blocks_with_reason(self):
        desktop = ScriptedDesktop({"start": screens.SCREEN_HBSYS_CLOSED})
        result = make_nav(desktop).navigate(nav.TARGET_DATE_FILL)
        self.assertFalse(result.success)
        self.assertEqual(desktop.clicks, [])
        self.assertIn("CLOSED", result.reason)

    def test_date_fill_two_hops_verified(self):
        desktop = ScriptedDesktop({
            "start": screens.SCREEN_ORDER_TRANSACTIONS,
            "clicks": {
                "phic": screens.SCREEN_PHIC_BENEFICIARIES,
                "claim_form_2": screens.SCREEN_CLAIM_FORM_2,
            },
        })
        result = make_nav(desktop).navigate(nav.TARGET_DATE_FILL)
        self.assertTrue(result.success)
        self.assertEqual(desktop.clicks, [POINTS["phic"], POINTS["claim_form_2"]])
        self.assertEqual(result.final_screen, screens.SCREEN_CLAIM_FORM_2)
        self.assertTrue(all(h.outcome == nav.HOP_OK for h in result.hops))

    def test_verify_failure_reroutes_and_stops(self):
        # phic click does nothing (screen stuck): the hop is VERIFY_FAILED
        # and the fail-safe caps retries at MAX_HOPS before giving up.
        desktop = ScriptedDesktop({"start": screens.SCREEN_ORDER_TRANSACTIONS})
        result = make_nav(desktop).navigate(nav.TARGET_DATE_FILL)
        self.assertFalse(result.success)
        self.assertEqual(result.hops[0].outcome, nav.HOP_VERIFY_FAILED)
        self.assertLessEqual(len(result.hops), nav.MAX_HOPS + 1)

    def test_missing_coordinates_route_to_review(self):
        desktop = ScriptedDesktop({"start": screens.SCREEN_ORDER_TRANSACTIONS})
        navigator = make_nav(desktop)
        navigator.points = {}
        result = navigator.navigate(nav.TARGET_DATE_FILL)
        self.assertFalse(result.success)
        self.assertIn("no coordinates", result.reason)
        self.assertEqual(desktop.clicks, [])

    def test_upload_claims_popup_chain(self):
        desktop = ScriptedDesktop({
            "start": screens.SCREEN_ECLAIMS_DASHBOARD,
            "clicks": {
                "upload_att": screens.SCREEN_ECLAIMS_DASHBOARD,
                "add_claims": screens.SCREEN_UPLOAD_CLAIMS,
            },
        })
        result = make_nav(desktop).navigate(nav.TARGET_UPLOAD_CLAIMS)
        self.assertTrue(result.success)
        self.assertEqual(result.final_screen, screens.SCREEN_UPLOAD_CLAIMS)


class GoHomeTests(unittest.TestCase):
    def test_already_home(self):
        desktop = ScriptedDesktop({"start": screens.SCREEN_ORDER_TRANSACTIONS})
        result = make_nav(desktop).go_home()
        self.assertTrue(result.success)
        self.assertEqual(desktop.escapes, 0)

    def test_dialog_dismissed_by_escape(self):
        desktop = ScriptedDesktop({"start": screens.SCREEN_DIALOG})
        result = make_nav(desktop).go_home()
        self.assertTrue(result.success)
        self.assertEqual(desktop.escapes, 1)

    def test_closed_hbsys_cannot_recover(self):
        desktop = ScriptedDesktop({"start": screens.SCREEN_HBSYS_CLOSED})
        result = make_nav(desktop).go_home()
        self.assertFalse(result.success)
        self.assertEqual(desktop.escapes, 0)

    def test_navigate_recovers_blocker_then_continues(self):
        desktop = ScriptedDesktop({
            "start": screens.SCREEN_DIALOG,
            "reads": [
                screens.SCREEN_DIALOG,            # navigate sees blocker
                screens.SCREEN_DIALOG,            # go_home sees blocker
                screens.SCREEN_ORDER_TRANSACTIONS,  # after Escape
                screens.SCREEN_ORDER_TRANSACTIONS,  # chain continues
                screens.SCREEN_PHIC_BENEFICIARIES,  # phic verified
                screens.SCREEN_CLAIM_FORM_2,      # claim_form_2 verified
            ],
            "clicks": {
                "phic": screens.SCREEN_PHIC_BENEFICIARIES,
                "claim_form_2": screens.SCREEN_CLAIM_FORM_2,
            },
        })
        result = make_nav(desktop).navigate(nav.TARGET_DATE_FILL)
        self.assertTrue(result.success)
        self.assertEqual(desktop.escapes, 1)


if __name__ == "__main__":
    unittest.main()

