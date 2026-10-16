"""Unicode-safe text entry for the pyautogui-driven HBSys tools.

``pyautogui.write()`` can only send characters that exist in
``pyautogui.KEYBOARD_KEYS`` (lowercase letters, digits, symbols, named
keys). Any other character -- the enye in ``DOÑA``, accents such as
``á é í ó ú`` -- is silently DROPPED. A patient name typed into an
HBSys search box therefore comes out wrong (``DOÑA`` -> ``DOA``) and
the search finds nothing.

Operator report 2026-10-16: "ang script natin di nya kaya mag copy at
paste ng Ñ dinidisregard nya."

Why not a clipboard paste (Ctrl+V)?
    HBSys screens (window class ``FNWND370``) are custom-drawn 4GL
    windows: their entry fields are not standard Windows edit controls
    and they do not process ``Ctrl+V``. Sending ``Ctrl+V`` therefore
    leaves the field empty. What HBSys *does* accept is an ordinary
    keystroke -- which is exactly how ``pyautogui.write`` types the
    ASCII characters today.

So the fallback sends each non-ASCII character as a real **Unicode
keystroke** through the Win32 ``SendInput`` API with
``KEYEVENTF_UNICODE`` (``VK_PACKET``). That is the same input channel
``pyautogui`` uses, so the field receives the character the same way it
receives letters. If ``SendInput`` itself is unavailable, the module
falls back to a clipboard paste as a last resort.

No new dependency: ``ctypes`` is in the standard library.

Callers (``core/add_claims_uploader``,
``core/claim_attachments_uploader``,
``date_fill_hbsys/xml_generator_clicker``) keep their own logging and
(where present) input-guard behaviour and simply route the actual
keystrokes through :func:`write_text`.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class _INPUTUnion(ctypes.Union):
    _fields_ = (
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    )


class INPUT(ctypes.Structure):
    _fields_ = (
        ("type", wintypes.DWORD),
        ("union", _INPUTUnion),
    )


_USER32 = None


def _user32():
    """Return the cached user32 DLL handle configured for SendInput."""
    global _USER32
    if _USER32 is None:
        user32 = ctypes.windll.user32
        user32.SendInput.argtypes = (
            wintypes.UINT,
            ctypes.POINTER(INPUT),
            ctypes.c_int,
        )
        user32.SendInput.restype = wintypes.UINT
        _USER32 = user32
    return _USER32


def can_type(text: str) -> bool:
    """True when ``pyautogui.write`` can send every character of *text*.

    pyautogui's ``KEYBOARD_KEYS`` lists only lowercase letters plus
    digits/symbols/named keys, yet ``write`` happily produces uppercase
    and the rest of printable ASCII via Shift. So a character is
    typeable when it is either in that list or plain printable ASCII
    (0x20-0x7E). Anything else -- the enye, accents, smart quotes -- is
    dropped and must go through :func:`send_unicode_text` or
    :func:`paste_text`.
    """
    import pyautogui

    keys = set(pyautogui.KEYBOARD_KEYS)
    for character in text:
        if character in keys:
            continue
        if 0x20 <= ord(character) <= 0x7E:
            continue
        return False
    return True


def _key_events_for(text: str):
    """Yield the UTF-16 code units of *text* (handles surrogate pairs)."""
    encoded = text.encode("utf-16-le")
    return [
        encoded[index] | (encoded[index + 1] << 8)
        for index in range(0, len(encoded), 2)
    ]


def send_unicode_text(text: str, *, interval: float = 0.01) -> None:
    """Type *text* as real Unicode keystrokes via ``SendInput``.

    Each character is injected as a ``KEYEVENTF_UNICODE`` down/up pair,
    i.e. the same input channel ``pyautogui.write`` uses. This works in
    custom-drawn HBSys fields that ignore ``Ctrl+V``. Raises ``OSError``
    when the Win32 call does not deliver every event.
    """
    user32 = _user32()
    size = ctypes.sizeof(INPUT)
    for code in _key_events_for(text):
        events = (INPUT * 2)()
        events[0].type = INPUT_KEYBOARD
        events[0].union.ki = KEYBDINPUT(
            0, code, KEYEVENTF_UNICODE, 0, None
        )
        events[1].type = INPUT_KEYBOARD
        events[1].union.ki = KEYBDINPUT(
            0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None
        )
        sent = user32.SendInput(2, events, size)
        if sent != 2:
            raise OSError(
                f"SendInput delivered {sent}/2 events for U+{code:04X}"
            )
        if interval:
            time.sleep(interval)


def paste_text(text: str) -> None:
    """Copy *text* to the clipboard and paste it with Ctrl+V.

    Last-resort fallback only (Unicode-safe on Windows). Many HBSys
    fields are custom-drawn and ignore Ctrl+V, so
    :func:`send_unicode_text` is tried first.
    """
    import pyautogui
    import pyperclip

    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")


def write_text(text: str, *, interval: float = 0.01) -> bool:
    """Type *text*, keeping the pyautogui path for typeable text.

    Pure-ASCII text takes the unchanged ``pyautogui.write`` path. Text
    with a character pyautogui cannot send is injected as Unicode
    keystrokes (falling back to a clipboard paste only if ``SendInput``
    fails).

    Returns ``True`` when the non-ASCII fallback was used, ``False``
    when the normal ``pyautogui.write`` path ran.
    """
    import pyautogui

    if can_type(text):
        pyautogui.write(text, interval=interval)
        return False
    try:
        send_unicode_text(text, interval=interval)
    except Exception:
        paste_text(text)
    return True
