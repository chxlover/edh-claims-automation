"""Unit tests for core/agent/window_guard.py (blind-input guard, 2026-09-28).

Headless: the foreground-title probe is injected, so nothing touches the
real desktop and no input is ever sent.

Run from the project root:

    python -m unittest tests.test_agent_window_guard
    python tests/test_agent_window_guard.py
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import window_guard  # noqa: E402

HBSYS_TITLE = (
    "HBSys - Hospital Operations and Management Information System "
    "(HOMIS) Billing System"
)


class TitleTests(unittest.TestCase):
    def test_hbsys_titles_recognized(self):
        for title in (HBSYS_TITLE, "HOMIS Billing", "Hospital Manager"):
            self.assertTrue(window_guard.is_hbsys_title(title), title)

    def test_other_titles_are_not_hbsys(self):
        for title in ("EDH Claims Automation System", "", None, "Excel"):
            self.assertFalse(window_guard.is_hbsys_title(title), title)

    def test_foreground_is_hbsys_uses_the_injected_probe(self):
        self.assertTrue(
            window_guard.foreground_is_hbsys(lambda: HBSYS_TITLE)
        )
        self.assertFalse(
            window_guard.foreground_is_hbsys(lambda: "EDH Claims")
        )

    def test_probe_errors_are_fail_open(self):
        def boom():
            raise RuntimeError("no win32")

        self.assertFalse(window_guard.foreground_is_hbsys(boom))


class ModeTests(unittest.TestCase):
    def test_default_is_warn(self):
        self.assertEqual(window_guard.guard_mode({}), window_guard.MODE_WARN)

    def test_modes_are_case_insensitive(self):
        self.assertEqual(
            window_guard.guard_mode({window_guard.GUARD_ENV: " BLOCK "}),
            window_guard.MODE_BLOCK,
        )
        self.assertEqual(
            window_guard.guard_mode({window_guard.GUARD_ENV: "off"}),
            window_guard.MODE_OFF,
        )

    def test_unknown_mode_falls_back_to_warn(self):
        self.assertEqual(
            window_guard.guard_mode({window_guard.GUARD_ENV: "explode"}),
            window_guard.MODE_WARN,
        )


class GuardInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.logs = Path(self.tmp.name)
        self.lines: list = []

    def tearDown(self):
        self.tmp.cleanup()

    def guard(self, title, mode, action="hotkey ctrl+a"):
        return window_guard.guard_input(
            action,
            title_fn=lambda: title,
            mode=mode,
            log_fn=self.lines.append,
            log_dir_override=self.logs,
        )

    def test_hbsys_foreground_is_always_allowed_and_never_logged(self):
        for mode in window_guard.ALL_MODES:
            self.assertTrue(self.guard(HBSYS_TITLE, mode))
        self.assertEqual(self.lines, [])
        self.assertFalse(
            (self.logs / window_guard._LOG_FILE).is_file()
        )

    def test_warn_mode_allows_the_input_but_logs_it(self):
        self.assertTrue(self.guard("EDH Claims Automation System",
                                   window_guard.MODE_WARN))
        self.assertEqual(len(self.lines), 1)
        self.assertIn("blind input guard: hotkey ctrl+a", self.lines[0])
        self.assertIn("sending anyway", self.lines[0])
        self.assertIn(
            "blind input guard",
            (self.logs / window_guard._LOG_FILE).read_text(encoding="utf-8"),
        )

    def test_block_mode_refuses_the_input(self):
        self.assertFalse(self.guard("EDH Claims Automation System",
                                    window_guard.MODE_BLOCK))
        self.assertIn("input REFUSED", self.lines[0])

    def test_off_mode_checks_nothing(self):
        self.assertTrue(self.guard("EDH Claims Automation System",
                                   window_guard.MODE_OFF))
        self.assertEqual(self.lines, [])

    def test_unreadable_foreground_is_reported(self):
        self.assertTrue(self.guard("", window_guard.MODE_WARN))
        self.assertIn("unreadable", self.lines[0])

    def test_probe_error_is_fail_open(self):
        def boom():
            raise OSError("no window")

        self.assertTrue(
            window_guard.guard_input(
                "click Close Form", title_fn=boom,
                mode=window_guard.MODE_BLOCK,
                log_fn=self.lines.append, log_dir_override=self.logs,
            )
        )
        self.assertEqual(self.lines, [])

    def test_broken_log_callback_still_returns_the_decision(self):
        def boom(message):
            raise RuntimeError("log failed")

        self.assertFalse(
            window_guard.guard_input(
                "press enter", title_fn=lambda: "Excel",
                mode=window_guard.MODE_BLOCK,
                log_fn=boom, log_dir_override=self.logs,
            )
        )

    def test_env_var_selects_block_mode_end_to_end(self):
        with mock.patch.dict(os.environ,
                             {window_guard.GUARD_ENV: "block"}):
            self.assertFalse(
                window_guard.guard_input(
                    "hotkey ctrl+f4", title_fn=lambda: "EDH Claims",
                    log_fn=self.lines.append, log_dir_override=self.logs,
                )
            )


if __name__ == "__main__":
    unittest.main()
