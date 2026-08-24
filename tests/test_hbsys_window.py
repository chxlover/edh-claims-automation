"""Unit tests for the shared HBSys window detection module.

Covers the browser-window exclusion that prevents a Chrome tab whose title
contains "HBSys" from being mistaken for the real HBSys application.

Run from the project root:

    python -m unittest tests.test_hbsys_window
    python tests/test_hbsys_window.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from date_fill_hbsys import hbsys_window  # noqa: E402


class FakeWindow:
    """Minimal stand-in for a pywinauto window wrapper."""

    def __init__(self, text: str = "", class_name: str = "", handle: int = 0):
        self._text = text
        self._class = class_name
        self.handle = handle

    def window_text(self) -> str:
        if self._text is None:
            raise RuntimeError("window is gone")
        return self._text

    def class_name(self) -> str:
        if self._class is None:
            raise RuntimeError("window is gone")
        return self._class


class FakeDesktop:
    def __init__(self, windows, **kwargs):
        self._windows = windows
        self.kwargs = kwargs

    def windows(self):
        return self._windows


def desktop_factory(windows):
    """Return a Desktop factory that records the backend it was built with."""
    calls = []

    def factory(backend="win32", **kwargs):
        calls.append(backend)
        return FakeDesktop(windows, **kwargs)

    factory.calls = calls
    return factory


class WindowTitleAndClassTests(unittest.TestCase):
    def test_window_title_returns_text(self):
        self.assertEqual(hbsys_window.window_title(FakeWindow(text="HBSys")), "HBSys")

    def test_window_title_safe_on_broken_window(self):
        self.assertEqual(hbsys_window.window_title(FakeWindow(text=None)), "")

    def test_window_class_returns_class(self):
        self.assertEqual(
            hbsys_window.window_class(FakeWindow(class_name="FNWND370")),
            "FNWND370",
        )

    def test_window_class_safe_on_broken_window(self):
        self.assertEqual(hbsys_window.window_class(FakeWindow(class_name=None)), "")


class IsBrowserWindowTests(unittest.TestCase):
    def test_chrome_widget_win_1_is_browser(self):
        self.assertTrue(
            hbsys_window.is_browser_window(FakeWindow(class_name="Chrome_WidgetWin_1"))
        )

    def test_chrome_widget_win_0_is_browser(self):
        self.assertTrue(
            hbsys_window.is_browser_window(FakeWindow(class_name="Chrome_WidgetWin_0"))
        )

    def test_hbsys_class_is_not_browser(self):
        self.assertFalse(
            hbsys_window.is_browser_window(FakeWindow(class_name="FNWND370"))
        )

    def test_unrelated_class_is_not_browser(self):
        self.assertFalse(
            hbsys_window.is_browser_window(FakeWindow(class_name="#32770"))
        )


class IsHbsysWindowTests(unittest.TestCase):
    def test_fnwnd370_class_counts(self):
        self.assertTrue(
            hbsys_window.is_hbsys_window(FakeWindow(text="", class_name="FNWND370"))
        )

    def test_fnwnd230_class_counts(self):
        self.assertTrue(
            hbsys_window.is_hbsys_window(FakeWindow(text="", class_name="FNWND230"))
        )

    def test_hbsys_title_counts(self):
        self.assertTrue(
            hbsys_window.is_hbsys_window(
                FakeWindow(
                    text="HBSys - Hospital Operations and Management "
                    "Information System (HOMIS) Billing System"
                )
            )
        )

    def test_homis_title_counts(self):
        self.assertTrue(
            hbsys_window.is_hbsys_window(FakeWindow(text="HOMIS Billing"))
        )

    def test_chrome_tab_with_hbsys_title_is_excluded(self):
        # The regression this module was created for: a Chrome tab whose
        # title contains "HBSys" must never be treated as the HBSys app.
        chrome = FakeWindow(
            text="Claude HBSys Automation - Google Chrome",
            class_name="Chrome_WidgetWin_1",
        )
        self.assertFalse(hbsys_window.is_hbsys_window(chrome))

    def test_chrome_with_hbsys_class_name_is_excluded(self):
        # Even if a Chromium window somehow reports an HBSys-like title
        # without the browser class, the title markers still count; this
        # guards the class-only shortcut from ever matching a browser.
        edge = FakeWindow(
            text="HOMIS Billing",
            class_name="Chrome_WidgetWin_0",
        )
        self.assertFalse(hbsys_window.is_hbsys_window(edge))

    def test_unrelated_window_is_not_hbsys(self):
        self.assertFalse(
            hbsys_window.is_hbsys_window(FakeWindow(text="Notepad", class_name="Notepad"))
        )


class FindHbsysWindowsTests(unittest.TestCase):
    def setUp(self):
        self.hbsys_main = FakeWindow(
            text="HBSys - HOMIS Billing",
            class_name="FNWND370",
            handle=1,
        )
        self.hbsys_popup = FakeWindow(
            text="Admission History",
            class_name="FNWND230",
            handle=2,
        )
        self.chrome = FakeWindow(
            text="Claude HBSys Automation - Google Chrome",
            class_name="Chrome_WidgetWin_1",
            handle=3,
        )
        self.other = FakeWindow(text="Notepad", class_name="Notepad", handle=4)

    def test_find_hbsys_windows_excludes_browsers(self):
        factory = desktop_factory(
            [self.hbsys_main, self.chrome, self.hbsys_popup, self.other]
        )
        with mock.patch("date_fill_hbsys.hbsys_window.Desktop", factory):
            found = hbsys_window.find_hbsys_windows()
        self.assertEqual([w.handle for w in found], [1, 2])

    def test_find_hbsys_window_skips_chrome_and_returns_hbsys(self):
        factory = desktop_factory([self.chrome, self.hbsys_main, self.other])
        with mock.patch("date_fill_hbsys.hbsys_window.Desktop", factory):
            found = hbsys_window.find_hbsys_window()
        self.assertEqual(found.handle, 1)

    def test_find_hbsys_window_returns_none_when_only_browsers_match(self):
        factory = desktop_factory([self.chrome, self.other])
        with mock.patch("date_fill_hbsys.hbsys_window.Desktop", factory):
            self.assertIsNone(hbsys_window.find_hbsys_window())

    def test_find_hbsys_window_or_raise_returns_window(self):
        factory = desktop_factory([self.hbsys_main])
        with mock.patch("date_fill_hbsys.hbsys_window.Desktop", factory):
            self.assertEqual(hbsys_window.find_hbsys_window_or_raise().handle, 1)

    def test_find_hbsys_window_or_raise_raises_when_closed(self):
        factory = desktop_factory([self.chrome])
        with mock.patch("date_fill_hbsys.hbsys_window.Desktop", factory):
            with self.assertRaisesRegex(RuntimeError, "HBSys window not found"):
                hbsys_window.find_hbsys_window_or_raise()

    def test_backend_is_forwarded_to_desktop(self):
        factory = desktop_factory([self.hbsys_main])
        with mock.patch("date_fill_hbsys.hbsys_window.Desktop", factory):
            hbsys_window.find_hbsys_windows(backend="uia")
        self.assertEqual(factory.calls, ["uia"])

    def test_default_backend_is_win32(self):
        factory = desktop_factory([self.hbsys_main])
        with mock.patch("date_fill_hbsys.hbsys_window.Desktop", factory):
            hbsys_window.find_hbsys_windows()
        self.assertEqual(factory.calls, ["win32"])


if __name__ == "__main__":
    unittest.main()
