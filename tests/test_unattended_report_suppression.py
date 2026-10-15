"""Tests for the unattended-run report suppression and the XML settle
delay (operator request 2026-10-12).

Two behaviours, both keyed on environment variables so the MAIN DASHBOARD
is completely unaffected:

1. ``CLAIMS_SUPPRESS_REPORT_OPEN=1`` — set by the workflow adapter for the
   checker nodes — stops ``fees_checker.open_report()`` and
   ``claims_checker.main()`` from opening Excel/CSV windows mid-run.
2. ``CLAIMS_CHECKER_XML_SETTLE_SECONDS`` (default 12) — the Claims Checker
   waits for the XML auto-copy before scanning, so a folder is never
   judged "NO XML" while the copy is still in flight.

Nothing here runs a real check, opens a file, or touches HBSys: the
checkers' main bodies are patched out and only the guard logic runs.

Run from the project root:
    python -m unittest tests.test_unattended_report_suppression
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import claims_checker  # noqa: E402
import fees_checker  # noqa: E402
from core.workflow_adapters import (  # noqa: E402
    SUPPRESS_REPORT_OPEN_NODES,
    build_env,
)
from core.workflow_registry import get_node_spec  # noqa: E402


class BuildEnvSuppressionTests(unittest.TestCase):
    """The adapter sets the flag for checker nodes only."""

    def test_checker_nodes_are_suppressed(self):
        for key in ("fees_checker", "claims_checker", "claims_checker_recheck"):
            self.assertIn(key, SUPPRESS_REPORT_OPEN_NODES)
            spec = get_node_spec(key)
            env = build_env(spec, {}, base_env={})
            self.assertEqual(env.get("CLAIMS_SUPPRESS_REPORT_OPEN"), "1", key)

    def test_non_checker_nodes_are_not_flagged(self):
        for key in ("date_fill_regular", "final_bill", "xml_clicker",
                    "claim_attachments", "add_claims_upload"):
            spec = get_node_spec(key)
            env = build_env(spec, {}, base_env={})
            self.assertNotIn("CLAIMS_SUPPRESS_REPORT_OPEN", env, key)


class BuildEnvQuietTests(unittest.TestCase):
    """CLAIMS_AGENT_QUIET silences the XML Clicker's modal popup."""

    def test_xml_clicker_is_quiet(self):
        spec = get_node_spec("xml_clicker")
        env = build_env(spec, {}, base_env={})
        self.assertEqual(env.get("CLAIMS_AGENT_QUIET"), "1")

    def test_other_nodes_are_not_quiet(self):
        for key in ("fees_checker", "date_fill_regular", "final_bill",
                    "claims_checker", "claim_attachments",
                    "add_claims_upload"):
            spec = get_node_spec(key)
            env = build_env(spec, {}, base_env={})
            self.assertNotIn("CLAIMS_AGENT_QUIET", env, key)


class XmlClickerPopupTests(unittest.TestCase):
    """The tool prints instead of popping up when quiet."""

    def setUp(self):
        sys.path.insert(0, str(PROJECT_ROOT / "date_fill_hbsys"))
        import xml_generator_clicker as tool

        self.tool = tool

    def test_popup_suppressed_when_quiet(self):
        import os

        with mock.patch.dict(os.environ, {"CLAIMS_AGENT_QUIET": "1"}), \
                mock.patch.object(self.tool.ctypes, "windll") as windll:
            self.tool.show_completion_popup("done")
        windll.user32.MessageBoxW.assert_not_called()

    def test_popup_still_shown_without_the_flag(self):
        import os

        with mock.patch.dict(os.environ, {"CLAIMS_AGENT_QUIET": ""}), \
                mock.patch.object(self.tool.ctypes, "windll") as windll:
            self.tool.show_completion_popup("done")
        windll.user32.MessageBoxW.assert_called_once()

    def test_run_log_not_opened_when_quiet(self):
        import os

        with mock.patch.dict(os.environ, {"CLAIMS_AGENT_QUIET": "1"}), \
                mock.patch.object(self.tool.os, "startfile") as startfile:
            self.tool.open_run_log(Path("run.csv"))
        startfile.assert_not_called()

    def test_run_log_still_opened_without_the_flag(self):
        import os

        with mock.patch.dict(os.environ, {"CLAIMS_AGENT_QUIET": ""}), \
                mock.patch.object(self.tool.os, "startfile") as startfile:
            self.tool.open_run_log(Path("run.csv"))
        startfile.assert_called_once()

    def test_every_registry_node_builds_env_without_error(self):
        for key in get_node_spec.__globals__["NODE_REGISTRY"]:
            build_env(get_node_spec(key), {}, base_env={})


class FeesOpenReportTests(unittest.TestCase):
    """fees_checker.open_report honours the flag."""

    def test_suppressed_flag_never_opens(self):
        with mock.patch.dict("os.environ",
                             {"CLAIMS_SUPPRESS_REPORT_OPEN": "1"}), \
                mock.patch.object(fees_checker.os, "startfile") as startfile:
            fees_checker.open_report(Path("report.xlsx"))
        startfile.assert_not_called()

    def test_without_the_flag_it_still_opens(self):
        # Main dashboard behavior must be unchanged.
        env = {"PATH": "", "SystemRoot": ""}
        with mock.patch.dict("os.environ", env, clear=True), \
                mock.patch.object(
                    fees_checker.os, "startfile",
                    create=True) as startfile, \
                mock.patch.object(fees_checker.sys, "platform", "win32"):
            fees_checker.open_report(Path("report.xlsx"))
        startfile.assert_called_once()


class ClaimsCheckerSettleDelayTests(unittest.TestCase):
    """The XML settle delay: default 12s, configurable, never negative."""

    def setUp(self):
        self._saved = {
            k: v for k, v in __import__("os").environ.items()
            if k == "CLAIMS_CHECKER_XML_SETTLE_SECONDS"
        }

    def tearDown(self):
        import os

        os.environ.pop("CLAIMS_CHECKER_XML_SETTLE_SECONDS", None)
        os.environ.update(self._saved)

    def test_default_is_twelve_seconds(self):
        import os

        os.environ.pop("CLAIMS_CHECKER_XML_SETTLE_SECONDS", None)
        self.assertEqual(claims_checker.DEFAULT_XML_SETTLE_SECONDS, 12.0)
        self.assertEqual(claims_checker.xml_settle_seconds(), 12.0)

    def test_env_override_is_honoured(self):
        import os

        os.environ["CLAIMS_CHECKER_XML_SETTLE_SECONDS"] = "2.5"
        self.assertEqual(claims_checker.xml_settle_seconds(), 2.5)

    def test_zero_disables_the_wait(self):
        import os

        os.environ["CLAIMS_CHECKER_XML_SETTLE_SECONDS"] = "0"
        self.assertEqual(claims_checker.xml_settle_seconds(), 0.0)

    def test_negative_clamps_to_zero(self):
        import os

        os.environ["CLAIMS_CHECKER_XML_SETTLE_SECONDS"] = "-5"
        self.assertEqual(claims_checker.xml_settle_seconds(), 0.0)

    def test_bad_value_falls_back_to_default(self):
        import os

        os.environ["CLAIMS_CHECKER_XML_SETTLE_SECONDS"] = "soon"
        self.assertEqual(
            claims_checker.xml_settle_seconds(),
            claims_checker.DEFAULT_XML_SETTLE_SECONDS,
        )


class ClaimsCheckerMainGuardTests(unittest.TestCase):
    """main() waits, then skips the report windows when suppressed."""

    def _run_main(self, env):
        import os

        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(claims_checker.time, "sleep") as sleep, \
                mock.patch.object(claims_checker, "iter_patient_folders",
                                  return_value=[]), \
                mock.patch.object(claims_checker, "organize_claim_folders"), \
                mock.patch.object(claims_checker, "write_reports",
                                  return_value=(Path("a.csv"), Path("b.log"))), \
                mock.patch.object(claims_checker.os, "startfile") as startfile:
            claims_checker.main()
        return sleep, startfile

    def test_waits_eight_seconds_by_default(self):
        sleep, startfile = self._run_main({})
        sleep.assert_called_once_with(
            claims_checker.DEFAULT_XML_SETTLE_SECONDS)
        # Dashboard (no flag) still opens its reports.
        self.assertEqual(startfile.call_count, 2)

    def test_workflow_run_waits_and_opens_nothing(self):
        sleep, startfile = self._run_main({"CLAIMS_SUPPRESS_REPORT_OPEN": "1"})
        sleep.assert_called_once_with(
            claims_checker.DEFAULT_XML_SETTLE_SECONDS)
        startfile.assert_not_called()

    def test_settle_zero_means_no_sleep(self):
        sleep, _ = self._run_main(
            {"CLAIMS_CHECKER_XML_SETTLE_SECONDS": "0"})
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()