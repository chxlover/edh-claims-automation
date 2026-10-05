"""Unit tests for core/diagnostics.py (crash + lifecycle trail, 2026-09-28).

Everything installs into a temp folder; the crash stream is closed again via
reset_for_tests() so Windows can delete the temp directory.

Run from the project root:

    python -m unittest tests.test_core_diagnostics
    python tests/test_core_diagnostics.py
"""

from __future__ import annotations

import sys
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import mkdtemp
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import diagnostics  # noqa: E402


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        diagnostics.reset_for_tests()
        self.logs = Path(mkdtemp(prefix="edh_diag_test_"))

    def tearDown(self):
        diagnostics.reset_for_tests()

    def test_install_writes_start_and_is_idempotent(self):
        self.assertTrue(diagnostics.install_crash_logging(self.logs))
        self.assertFalse(diagnostics.install_crash_logging(self.logs))
        lifecycle = diagnostics.lifecycle_log_path(self.logs).read_text(
            encoding="utf-8"
        )
        self.assertEqual(lifecycle.count("START"), 1)
        self.assertIn("EDH Claims Automation System", lifecycle)
        self.assertTrue(diagnostics.crash_log_path(self.logs).is_file())
        self.assertIn(
            "faulthandler armed",
            diagnostics.crash_log_path(self.logs).read_text(encoding="utf-8"),
        )

    def test_write_lifecycle_appends_lines(self):
        diagnostics.install_crash_logging(self.logs)
        line = diagnostics.write_lifecycle("TEST", "hello", self.logs)
        self.assertIn("TEST hello", line)
        self.assertIn("[pid ", line)
        self.assertIn(
            "TEST hello",
            diagnostics.lifecycle_log_path(self.logs).read_text(encoding="utf-8"),
        )

    def test_log_exception_writes_traceback(self):
        try:
            raise ValueError("boom")
        except ValueError as exc:
            diagnostics.log_exception("unit", ValueError, exc, exc.__traceback__,
                                      self.logs)
        text = diagnostics.error_log_path(self.logs).read_text(encoding="utf-8")
        self.assertIn("UNHANDLED EXCEPTION (unit)", text)
        self.assertIn("ValueError: boom", text)

    def test_excepthook_logs_and_keeps_default_reporting(self):
        diagnostics.install_crash_logging(self.logs)
        printed: list = []
        original = sys.__excepthook__
        try:
            sys.__excepthook__ = lambda *args: printed.append(args)
            try:
                raise RuntimeError("hooked")
            except RuntimeError as exc:
                sys.excepthook(RuntimeError, exc, exc.__traceback__)
        finally:
            sys.__excepthook__ = original
        self.assertEqual(len(printed), 1)  # default reporting preserved
        self.assertIn(
            "hooked",
            diagnostics.error_log_path(self.logs).read_text(encoding="utf-8"),
        )

    def test_configured_log_dir_is_where_the_calls_write(self):
        diagnostics.install_crash_logging(self.logs)
        # The directory recorded at install time is used by the hooks.
        self.assertEqual(diagnostics._state["log_dir"], self.logs)


class TkCloseLoggingTests(unittest.TestCase):
    def setUp(self):
        diagnostics.reset_for_tests()
        self.logs = Path(mkdtemp(prefix="edh_diag_tk_"))
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001 - already destroyed by the test
            pass
        diagnostics.reset_for_tests()

    def test_close_writes_line_and_destroys_the_window(self):
        self.assertTrue(
            diagnostics.install_tk_close_logging(self.root, self.logs)
        )
        self.assertFalse(
            diagnostics.install_tk_close_logging(self.root, self.logs)
        )
        # The WM runs the registered Tcl command; destroy() alone skips it.
        self.root.tk.call(self.root.protocol("WM_DELETE_WINDOW"))
        lifecycle = diagnostics.lifecycle_log_path(self.logs).read_text(
            encoding="utf-8"
        )
        self.assertIn("CLOSE", lifecycle)
        self.assertIn("WM_DELETE_WINDOW", lifecycle)

    def test_tk_callback_exception_is_logged(self):
        diagnostics.install_tk_close_logging(self.root, self.logs)

        def boom():
            raise RuntimeError("callback boom")

        self.root.after(0, boom)
        self.root.update()  # runs the callback -> report_callback_exception
        text = diagnostics.error_log_path(self.logs).read_text(encoding="utf-8")
        self.assertIn("callback boom", text)
        self.assertIn("tkinter callback", text)

    def test_install_is_safe_without_a_root(self):
        self.assertFalse(diagnostics.install_tk_close_logging(None))


class ResetSafetyTests(unittest.TestCase):
    """After reset_for_tests() nothing may write to the production logs.

    Regression guard (2026-09-28): the EXIT atexit hook used to resolve
    _state["log_dir"] at exit time, so install/reset cycles inside one test
    process ended with stale EXIT lines in the real logs/gui_lifecycle.log.
    """

    def setUp(self):
        diagnostics.reset_for_tests()
        self.logs = Path(mkdtemp(prefix="edh_diag_bind_"))
        self.never = Path(mkdtemp(prefix="edh_diag_nowhere_")) / "logs"

    def tearDown(self):
        diagnostics.reset_for_tests()

    def test_exit_hook_keeps_the_directory_it_was_registered_with(self):
        diagnostics.install_crash_logging(self.logs)
        diagnostics.reset_for_tests()  # clears _state["log_dir"]
        diagnostics._on_exit(self.logs)  # the directory atexit kept
        lifecycle = diagnostics.lifecycle_log_path(self.logs).read_text(
            encoding="utf-8"
        )
        self.assertIn("EXIT", lifecycle)

    def test_exit_hook_without_a_directory_writes_nowhere(self):
        with mock.patch.object(diagnostics, "LOG_DIR", self.never):
            diagnostics._on_exit()
        self.assertFalse(self.never.exists())

    def test_excepthook_after_reset_writes_nowhere(self):
        original = sys.__excepthook__
        printed: list = []
        try:
            sys.__excepthook__ = lambda *args: printed.append(args)
            with mock.patch.object(diagnostics, "LOG_DIR", self.never):
                diagnostics._excepthook(ValueError, ValueError("x"), None)
        finally:
            sys.__excepthook__ = original
        self.assertEqual(len(printed), 1)  # default reporting still runs
        self.assertFalse(self.never.exists())

    def test_reset_unregisters_the_exit_hook(self):
        diagnostics.install_crash_logging(self.logs)
        with mock.patch.object(diagnostics.atexit, "unregister") as unreg:
            diagnostics.reset_for_tests()
        unreg.assert_called_with(diagnostics._on_exit)


if __name__ == "__main__":
    unittest.main()
