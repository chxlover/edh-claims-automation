"""Operator report 2026-10-16: pyautogui.write() silently DROPS the
enye (and other non-ASCII characters), so a patient name like DOÑA is
typed as DOA and the HBSys search finds nothing.

These tests pin the Unicode-safe fallback (core.unicode_typing) and
that the operators route their text entry through it. All key sends
are mocked — no live input, no HBSys needed.
"""

from __future__ import annotations

import ctypes
import sys
import unittest
from pathlib import Path
from unittest import mock

from core import unicode_typing

DATE_FILL_DIR = Path(__file__).resolve().parent.parent / "date_fill_hbsys"
if str(DATE_FILL_DIR) not in sys.path:
    sys.path.insert(0, str(DATE_FILL_DIR))


class CanTypeTests(unittest.TestCase):
    def test_ascii_text_is_typeable(self):
        self.assertTrue(unicode_typing.can_type("DOA, JENNIFER"))

    def test_enye_is_not_typeable(self):
        self.assertFalse(unicode_typing.can_type("DOÑA, JENNIFER"))

    def test_enye_and_accents_are_not_typeable(self):
        for text in ("doña", "MARÍA", "PEÑA", "JOSÉ", "NÚÑEZ"):
            with self.subTest(text=text):
                self.assertFalse(unicode_typing.can_type(text))


class FakeUser32:
    """Captures SendInput calls instead of injecting real keystrokes."""

    def __init__(self, accept: bool = True):
        self.accept = accept
        self.calls: list = []

    def SendInput(self, count, events, size):
        self.calls.append((count, size, list(events)))
        return count if self.accept else 0


class SendUnicodeTextTests(unittest.TestCase):
    def test_sends_down_and_up_unicode_events(self):
        fake = FakeUser32()
        with mock.patch.object(unicode_typing, "_user32", return_value=fake), \
                mock.patch.object(unicode_typing.time, "sleep"):
            unicode_typing.send_unicode_text("Ñ", interval=0)

        self.assertEqual(len(fake.calls), 1)
        count, size, events = fake.calls[0]
        self.assertEqual(count, 2)
        self.assertEqual(size, ctypes.sizeof(unicode_typing.INPUT))
        # scan code carries the real character (U+00D1), not a vkey
        self.assertEqual(events[0].union.ki.wScan, 0x00D1)
        self.assertEqual(events[0].union.ki.dwFlags, 0x0004)
        self.assertEqual(events[1].union.ki.dwFlags, 0x0004 | 0x0002)

    def test_raises_when_sendinput_delivers_nothing(self):
        fake = FakeUser32(accept=False)
        with mock.patch.object(unicode_typing, "_user32", return_value=fake), \
                mock.patch.object(unicode_typing.time, "sleep"):
            with self.assertRaises(OSError):
                unicode_typing.send_unicode_text("Ñ", interval=0)

    def test_input_struct_is_win32_sized(self):
        # 28 bytes on 32-bit, 40 bytes on 64-bit; a wrong size makes
        # SendInput fail with ERROR_INVALID_PARAMETER.
        self.assertIn(ctypes.sizeof(unicode_typing.INPUT), (28, 40))


class WriteTextTests(unittest.TestCase):
    def test_ascii_uses_pyautogui_write(self):
        with mock.patch("pyautogui.write") as fake_write, \
                mock.patch.object(
                    unicode_typing, "send_unicode_text") as fake_uni, \
                mock.patch.object(
                    unicode_typing, "paste_text") as fake_paste:
            used_fallback = unicode_typing.write_text("DOA", interval=0.03)
        self.assertFalse(used_fallback)
        fake_write.assert_called_once_with("DOA", interval=0.03)
        fake_uni.assert_not_called()
        fake_paste.assert_not_called()

    def test_enye_uses_unicode_keystrokes(self):
        with mock.patch("pyautogui.write") as fake_write, \
                mock.patch.object(
                    unicode_typing, "send_unicode_text") as fake_uni, \
                mock.patch.object(
                    unicode_typing, "paste_text") as fake_paste:
            used_fallback = unicode_typing.write_text("DOÑA", interval=0.02)
        self.assertTrue(used_fallback)
        fake_write.assert_not_called()
        fake_uni.assert_called_once_with("DOÑA", interval=0.02)
        fake_paste.assert_not_called()

    def test_unicode_failure_falls_back_to_clipboard(self):
        with mock.patch.object(
                unicode_typing, "send_unicode_text",
                side_effect=OSError("SendInput failed")), \
                mock.patch.object(
                    unicode_typing, "paste_text") as fake_paste:
            used_fallback = unicode_typing.write_text("DOÑA")
        self.assertTrue(used_fallback)
        fake_paste.assert_called_once_with("DOÑA")


class PasteTextTests(unittest.TestCase):
    def test_paste_copies_then_sends_ctrl_v(self):
        with mock.patch("pyperclip.copy") as fake_copy, \
                mock.patch("pyautogui.hotkey") as fake_hotkey:
            unicode_typing.paste_text("DOÑA, JENNIFER")
        fake_copy.assert_called_once_with("DOÑA, JENNIFER")
        fake_hotkey.assert_called_once_with("ctrl", "v")


class OperatorRoutingTests(unittest.TestCase):
    """Every operator's write() must go through the helper (live mode)."""

    def _assert_routes_through_helper(self, operator):
        with mock.patch.object(
                unicode_typing, "write_text") as fake_write:
            operator.write("DOÑA")
        fake_write.assert_called_once()
        self.assertEqual(fake_write.call_args.args[0], "DOÑA")

    def test_add_claims_operator_routes_through_helper(self):
        from core.add_claims_uploader import AddClaimsOperator

        operator = AddClaimsOperator(live=True, pause=0, confirm_each=False)
        self._assert_routes_through_helper(operator)

    def test_attachments_operator_routes_through_helper(self):
        from core.claim_attachments_uploader import AttachmentsOperator

        operator = AttachmentsOperator(live=True, pause=0, confirm_each=False)
        self._assert_routes_through_helper(operator)

    def test_xml_generator_operator_routes_through_helper(self):
        from date_fill_hbsys.xml_generator_clicker import XmlGeneratorOperator

        operator = XmlGeneratorOperator(live=True, pause=0, confirm_each=False)
        self._assert_routes_through_helper(operator)

    def test_dry_run_never_touches_the_keyboard(self):
        from core.add_claims_uploader import AddClaimsOperator

        operator = AddClaimsOperator(live=False, pause=0, confirm_each=False)
        with mock.patch.object(
                unicode_typing, "write_text") as fake_write:
            operator.write("DOÑA")
        fake_write.assert_not_called()

    def test_folder_path_typing_uses_helper(self):
        """The file-dialog folder path also carries the patient name."""
        from core.claim_attachments_uploader import AttachmentsOperator

        operator = AttachmentsOperator(live=True, pause=0, confirm_each=False)
        path = r"C:\claims_bot\output\DOÑA, JENNIFER - 000000000021698"
        with mock.patch("pyautogui.click"), \
                mock.patch("pyautogui.hotkey"), \
                mock.patch("pyautogui.press"), \
                mock.patch("core.claim_attachments_uploader.sleep_short"), \
                mock.patch.object(
                    unicode_typing, "write_text") as fake_write:
            operator.type_folder_path_and_open(path)
        fake_write.assert_called_once_with(path, interval=0.01)


if __name__ == "__main__":
    unittest.main()
