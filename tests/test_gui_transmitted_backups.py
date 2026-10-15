"""GUI tests for the TRANSMITTED Backups button (operator request 2026-10-15).

One click opens the local and the network TRANSMITTED archive, each
independently: an unreachable folder never blocks the other and is
reported once at the end (no create-prompt — the archives are created
by Archive Transmitted Claims itself).

Instantiates the real widgets with the window withdrawn so nothing flashes.

Run from the project root:
    python -m unittest tests.test_gui_transmitted_backups
"""

from __future__ import annotations

import os
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import edh_claims_gui_XML_COPY_BUTTON as gui_module  # noqa: E402
from core import diagnostics  # noqa: E402

_DIAG_LOGS = None


def setUpModule():
    global _DIAG_LOGS
    diagnostics.reset_for_tests()
    _DIAG_LOGS = mkdtemp(prefix="edh_gui_test_diag_")
    os.environ["CLAIMS_DIAG_LOG_DIR"] = _DIAG_LOGS


def tearDownModule():
    os.environ.pop("CLAIMS_DIAG_LOG_DIR", None)
    diagnostics.reset_for_tests()
    if _DIAG_LOGS:
        shutil.rmtree(_DIAG_LOGS, ignore_errors=True)


def find_button(widget, text):
    if widget.winfo_class() in ("Button", "TButton"):
        try:
            if str(widget.cget("text")) == text:
                return widget
        except Exception:
            pass
    for child in widget.winfo_children():
        found = find_button(child, text)
        if found is not None:
            return found
    return None


class TransmittedBackupsButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = gui_module.EDHClaimsGUI()
        cls.app.withdraw()
        cls.app.update_idletasks()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.destroy()
        except Exception:  # noqa: BLE001 - cleanup must not mask failures.
            pass

    def test_button_exists_beside_archived(self):
        button = find_button(self.app, "Open TRANSMITTED Backups")
        self.assertIsNotNone(button)

    def test_paths_are_the_archive_destinations(self):
        self.assertEqual(
            list(gui_module.TRANSMITTED_BACKUP_FOLDERS),
            [
                r"C:\Shared Folder\FTPURL\TRANSMITTED\scan via bot",
                r"\\192.168.1.193\Echague District Hospital Files"
                r"\melvin\SHAREDFOLDER\TRANSMITTED\MELVIN\scan via bot",
            ],
        )

    def test_one_click_opens_both_folders(self):
        opened = []
        with mock.patch.object(gui_module.os, "startfile",
                               side_effect=lambda p: opened.append(p)), \
             mock.patch.object(gui_module.os.path, "exists",
                               return_value=True), \
             mock.patch.object(gui_module.messagebox, "showinfo") as info:
            self.app.open_transmitted_backups()
        self.assertEqual(len(opened), 2)
        for path in gui_module.TRANSMITTED_BACKUP_FOLDERS:
            self.assertIn(gui_module.normalize_windows_path(path), opened)
        info.assert_not_called()

    def test_unreachable_folder_does_not_block_the_other(self):
        opened = []
        real_exists = os.path.exists

        def exists(path):
            if "192.168.1.193" in str(path):
                return False
            return real_exists(path)

        with TemporaryDirectory() as tmp:
            local = str(Path(tmp) / "scan via bot")
            os.makedirs(local)
            with mock.patch.object(gui_module, "TRANSMITTED_BACKUP_FOLDERS",
                                   (local,
                                    r"\\192.168.1.193\unreachable\share")), \
                 mock.patch.object(gui_module.os, "startfile",
                                   side_effect=lambda p: opened.append(p)), \
                 mock.patch.object(gui_module.messagebox, "showinfo") as info:
                self.app.open_transmitted_backups()
        self.assertEqual(opened, [gui_module.normalize_windows_path(local)])
        info.assert_called_once()

    def test_button_invokes_the_opener(self):
        button = find_button(self.app, "Open TRANSMITTED Backups")
        self.assertIsNotNone(button)
        opened = []
        with mock.patch.object(gui_module.os, "startfile",
                               side_effect=lambda p: opened.append(p)), \
             mock.patch.object(gui_module.os.path, "exists",
                               return_value=True), \
             mock.patch.object(gui_module.messagebox, "showinfo"):
            button.invoke()
            self.app.update_idletasks()
        self.assertEqual(len(opened), 2)


if __name__ == "__main__":
    unittest.main()
