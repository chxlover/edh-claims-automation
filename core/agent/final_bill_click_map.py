"""Final Bill click map - ang mga eksaktong PININDOT ng operator.

Why this exists
---------------
The Final Bill flow answers HBSys prompts with the MOUSE:

    "Print Options"        -> click the UNANG OK        -> print_options_ok
    "File save"            -> click the susunod na OK   -> save_ok
    "Call Administrator"   -> click "No"                -> admin_no
    "Print Options"        -> the 'Final' checkbox      -> final_checkbox

Today those buttons are found by PowerBuilder control id / caption / geometry
(verified 2026-09-25). That is fragile on a build whose ids or captions shift.
This module lets the operator record the REAL point once, per button, and hands
it back to the flow:

    * a point is stored with the dialog it belongs to, as an OFFSET inside that
      dialog, so a popup that opens somewhere else on screen still gets its
      click on the same button;
    * mapping is OPTIONAL: an unmapped button keeps the existing control
      lookup, and a mapped point that misses is retried with that lookup before
      a row is allowed to fail (see final_bill_actions._click_prompt_answer).

How to map (live, HBSys on screen)
----------------------------------
    python -m core.agent.final_bill_click_map --map
        One session, in the order the live flow reaches them:
            1. Print Options       -> 'Final' checkbox (opsyonal, ESC muna)
            2. Print Options       -> UNANG OK, hover tapos F8,
                                      tapos i-click mo para lumabas ang prompt
            3. isa lang sa dalawa ang lalabas - i-map ang lumabas:
               "File save"         -> hover its OK, press F8
               "Call Administrator"-> hover its "No", press F8
        ESC skips a target. F8 records the point under the mouse.

    python -m core.agent.final_bill_click_map --map admin_pair
        "Isang option": records the admin path only - unang OK, tapos No.

    python -m core.agent.final_bill_click_map --map dialog_oks --in 8
        Same, but instead of F8 each target has 8 seconds: hover now, it
        records itself (works even when the console is not focused).

    python -m core.agent.final_bill_click_map --branch --in 12
        Para sa tamang order: checkbox, unang OK, tapos hinihintay ng tool ang
        prompt na TALAGANG lumabas - isa lang ang ni-record (File save OK o
        Call Administrator No). Isang command, walang session na naghihintay
        sa hindi lalabas na dialog. F8 rin ang pindot kahit habang hinihintay
        pa lang ang prompt: kapag hindi nababasa ng tool ang title nito,
        itatanong nito kung alin ang lumabas bago mag-record - hindi
        namamatay ang F8 sa step na ito.

    python -m core.agent.final_bill_click_map --show   # ano ang naka-map na
    python -m core.agent.final_bill_click_map --check  # bukas ba ang dialog?
    python -m core.agent.final_bill_click_map --clear  # ulitin mula umpisa
    python -m core.agent.final_bill_click_map --set save_ok=1010,630
    python -m gui.final_bill_map_editor   # GUI: labels + X,Y bawat button,
                                          # i-save sa parehong .json (walang F8)

Standalone test: python -m core.agent.final_bill_click_map --show
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Absolute (not cwd-relative): the Final Bill flow and every launcher must find
# the operator's map no matter which directory the claims GUI was started from
# (integration check 2026-10-02 - a cwd outside C:\claims_bot silently returned
# an empty map). Same file as before when run from the project root.
LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
CLICK_MAP_PATH = LOG_DIR / "final_bill_click_map.json"
MAP_VERSION = 1

# Keys the Final Bill flow looks up (final_bill_actions._mapped_click_point).
POINT_PRINT_OPTIONS_OK = "print_options_ok"
POINT_SAVE_OK = "save_ok"
POINT_ADMIN_NO = "admin_no"
POINT_FINAL_CHECKBOX = "final_checkbox"


@dataclass(frozen=True)
class ClickTarget:
    """One button the operator can map."""

    name: str
    label: str            # shown in the session and inside the map file
    anchor: str           # title marker of its dialog ("" = no anchor)
    steps: tuple          # Tagalog + English instructions for the session


TARGETS = (
    ClickTarget(
        POINT_FINAL_CHECKBOX,
        "Print Options - 'Final' checkbox",
        "Print Options",
        (
            "Buksan ang HBSys: Billing -> Final Bill para lumabas ang",
            "'Print Options' popup.",
            "I-hover ang mouse sa 'Final' checkbox (tabi ng salitang Final).",
            "Pindutin ang F8 para i-record. (ESC = laktawan, opsyonal ito)",
        ),
    ),
    ClickTarget(
        POINT_PRINT_OPTIONS_OK,
        "Print Options - UNANG OK",
        "Print Options",
        (
            "Nasa 'Print Options' ka pa rin.",
            "I-hover ang mouse SA GITNA ng UNANG OK (huwag munang i-click).",
            "Pindutin ang F8 - TAPOS i-click ang OK para lumabas ang prompt.",
        ),
    ),
    ClickTarget(
        POINT_SAVE_OK,
        "'File save' - OK",
        "File save",
        (
            "Tapos na ang unang OK: lumabas na ang 'File save' prompt.",
            "I-hover ang mouse sa OK nito (ang nasa ibabang button row).",
            "Pindutin ang F8 para i-record. (ESC = laktawan)",
            "Kapag 'Call Administrator' ang lumabas sa halip nito,",
            "pindutin ang ESC at i-map ito sa ibang session.",
        ),
    ),
    ClickTarget(
        POINT_ADMIN_NO,
        "'Call Administrator' - No",
        "Call Administrator",
        (
            "I-click muna ang unang OK para lumabas ang dialog na ito.",
            "I-hover ang mouse sa 'No' - HUWAG sa Yes.",
            "Pindutin ang F8 para i-record. (ESC = laktawan)",
        ),
    ),
)
TARGETS_BY_NAME = {target.name: target for target in TARGETS}

# "Isang option" na sunod-sunod na mapping session: --map admin_pair
GROUPS = {
    "all": tuple(target.name for target in TARGETS),
    "dialog_oks": (POINT_PRINT_OPTIONS_OK, POINT_SAVE_OK),
    "checkbox_first_ok": (POINT_FINAL_CHECKBOX, POINT_PRINT_OPTIONS_OK),
    "admin_pair": (POINT_PRINT_OPTIONS_OK, POINT_ADMIN_NO),
}

# Virtual-key codes for the capture keys (F8 records, ESC skips).
KEYS = {"f8": 0x77, "esc": 0x1B}


@dataclass
class ClickPoint:
    """One recorded click: absolute point + (when possible) dialog offset.

    `dx`/`dy` are the point's offset inside its anchor dialog's rectangle. They
    are what makes the mapping survive a popup that opens at another spot: the
    flow resolves the point against the dialog's LIVE rectangle.
    """

    name: str
    label: str = ""
    anchor: str = ""
    x: int = 0
    y: int = 0
    dx: int | None = None
    dy: int | None = None
    anchor_rect: tuple | None = None
    mapped_at: str = ""
    note: str = ""

    def point_for(self, dialog_rect=None) -> tuple:
        """Absolute (x, y) to click: dialog-relative when the anchor is known."""
        if dialog_rect is not None and self.dx is not None and self.dy is not None:
            rect = tuple(int(value) for value in dialog_rect)
            if len(rect) == 4:
                return (rect[0] + int(self.dx), rect[1] + int(self.dy))
        return (int(self.x), int(self.y))

    @property
    def anchored(self) -> bool:
        return bool(self.anchor) and self.dx is not None and self.dy is not None

    def summary(self) -> str:
        base = f"({int(self.x)}, {int(self.y)})"
        if self.anchored:
            return (
                f"{base} | nakaangkla sa '{self.anchor}' "
                f"+({int(self.dx)}, {int(self.dy)})"
            )
        return f"{base} | absolute (walang anchor)"

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "anchor": self.anchor,
            "x": int(self.x),
            "y": int(self.y),
            "dx": None if self.dx is None else int(self.dx),
            "dy": None if self.dy is None else int(self.dy),
            "anchor_rect": (
                [int(value) for value in self.anchor_rect]
                if self.anchor_rect
                else None
            ),
            "mapped_at": self.mapped_at,
            "note": self.note,
        }


@dataclass
class ClickMap:
    """Every recorded point, keyed by target name (`print_options_ok`, ...)."""

    version: int = MAP_VERSION
    screen: tuple = ()
    mapped_at: str = ""
    points: dict = field(default_factory=dict)

    def get(self, name: str) -> ClickPoint | None:
        point = self.points.get(str(name))
        return point if isinstance(point, ClickPoint) else None

    def set(self, point: ClickPoint) -> None:
        self.points[point.name] = point

    def is_mapped(self, name: str) -> bool:
        return self.get(name) is not None

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "screen": [int(value) for value in (self.screen or ())],
            "mapped_at": self.mapped_at or datetime.now().isoformat(),
            "points": {
                name: point.to_dict() for name, point in self.points.items()
            },
        }

    def save(self, path: Path | str | None = None) -> Path:
        """Write the map (logs/final_bill_click_map.json by default)."""
        target = Path(path) if path else CLICK_MAP_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        self.mapped_at = self.mapped_at or datetime.now().isoformat()
        target.write_text(
            json.dumps(self.to_dict(), indent=2), encoding="utf-8"
        )
        return target

    @classmethod
    def load(cls, path: Path | str | None = None) -> "ClickMap":
        """Read the map; a missing or unreadable file is an EMPTY map.

        Never raises: an unreadable map must not stop a live run - the flow
        then simply keeps its verified control lookup.
        """
        target = Path(path) if path else CLICK_MAP_PATH
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        if not isinstance(raw, dict):
            return cls()
        points = {}
        for name, data in (raw.get("points") or {}).items():
            if not isinstance(data, dict):
                continue
            try:
                rect = data.get("anchor_rect")
                points[str(name)] = ClickPoint(
                    name=str(name),
                    label=str(data.get("label") or ""),
                    anchor=str(data.get("anchor") or ""),
                    x=int(data.get("x") or 0),
                    y=int(data.get("y") or 0),
                    dx=None if data.get("dx") is None else int(data["dx"]),
                    dy=None if data.get("dy") is None else int(data["dy"]),
                    anchor_rect=tuple(int(v) for v in rect) if rect else None,
                    mapped_at=str(data.get("mapped_at") or ""),
                    note=str(data.get("note") or ""),
                )
            except Exception:
                continue
        try:
            screen = tuple(int(value) for value in (raw.get("screen") or ()))
        except Exception:
            screen = ()
        return cls(
            version=int(raw.get("version") or MAP_VERSION),
            screen=screen,
            mapped_at=str(raw.get("mapped_at") or ""),
            points=points,
        )


def load_click_map(path: Path | str | None = None) -> ClickMap:
    """The click map as saved (empty when nothing was mapped yet)."""
    return ClickMap.load(path)


def mapped_point(
    name: str,
    dialog_rect=None,
    path: Path | str | None = None,
) -> tuple | None:
    """(x, y) recorded for `name` - or None when it was never mapped.

    `dialog_rect` is the LIVE rectangle of the dialog the point belongs to; it
    is what turns the recorded offset back into an absolute point, so the click
    follows its dialog around the screen.
    """
    point = load_click_map(path).get(name)
    if point is None:
        return None
    return point.point_for(dialog_rect)


def expand_names(names=None) -> tuple:
    """Target names, in order; group names ("admin_pair", "all") expand.

    A plain string is ONE name, not a sequence of characters - `--map` with no
    argument arrives as an empty list, and a programmatic call may pass
    "admin_pair" directly.
    """
    if not names:
        return GROUPS["all"]
    if isinstance(names, str):
        names = [names]
    expanded: list = []
    for name in names:
        key = str(name).strip()
        if not key:
            continue
        for member in GROUPS.get(key) or (key,):
            if member not in expanded:
                expanded.append(member)
    return tuple(expanded)


# -- capture session -----------------------------------------------------------

def _keys_available() -> bool:
    try:
        import win32api  # noqa: F401  (ships with pywin32/pywinauto)
    except Exception:
        return False
    return True


def key_down(vk: int) -> bool:
    """True while the virtual key `vk` is held down (False if unreadable)."""
    try:
        import win32api
    except Exception:
        return False
    try:
        return bool(win32api.GetAsyncKeyState(int(vk)) & 0x8000)
    except Exception:
        return False


def wait_for_capture_key(
    *,
    key_fn=None,
    timeout=None,
    poll: float = 0.05,
    print_fn=print,
) -> str:
    """Wait for F8 ('capture') or ESC ('skip'); '' when `timeout` runs out.

    F8 records the point under the mouse, so the operator never has to move the
    mouse off the button they are marking. `key_fn` is injectable for tests.
    """
    if key_fn is None:
        if not _keys_available():
            return console_capture_key(print_fn)
        key_fn = key_down
    deadline = None if timeout is None else time.time() + float(timeout)
    sleep_for = max(float(poll), 0.01)
    while deadline is None or time.time() < deadline:
        if key_fn(KEYS["f8"]):
            return "capture"
        if key_fn(KEYS["esc"]):
            return "skip"
        time.sleep(sleep_for)
    return ""


def console_capture_key(print_fn=print) -> str:
    """Fallback when win32api is missing: ENTER records, 's' skips.

    In this mode the point is read when ENTER is pressed, so the mouse must
    already be on the button (use `--in SECONDS` or `--set name=x,y` instead).
    """
    try:
        print_fn("      [ENTER = i-record ang kasalukuyang mouse position,"
                 " 's' = laktawan]")
        answer = input("      > ")
    except (EOFError, KeyboardInterrupt):
        return "skip"
    return "skip" if answer.strip().lower().startswith("s") else "capture"


def _default_position() -> tuple:
    """(x, y) of the mouse right now."""
    import pyautogui

    x, y = pyautogui.position()
    return int(x), int(y)


def _screen_size() -> tuple:
    try:
        import pyautogui

        return tuple(int(value) for value in pyautogui.size())
    except Exception:
        return ()


def _default_anchor_rect(marker: str):
    """Live rectangle of the dialog whose title contains `marker` (None = wala).

    Lazy import: this module must stay importable (and testable) without the
    GUI stack, and final_bill_actions is the module that owns the finders.
    """
    from core.agent import final_bill_actions as fb

    if marker.lower().startswith("print options"):
        window = fb.find_print_options_window()
    elif marker.lower().startswith("file save"):
        # Live 2026-10-02: HBSys raises this prompt titled "Note" with "File
        # Save" in the body, so a plain find_dialog("File save") misses it and
        # the point could never be anchored (dx/dy stayed None -> absolute).
        # _front_save_prompt() already knows both titles and body-confirms.
        window = fb.find_dialog_exact(*fb.screens.TITLE_FILE_SAVE_ALIASES)
        if window is None:
            window, _label = fb._front_save_prompt()
    else:
        window = fb.find_dialog(marker)
    if window is None:
        return None
    return fb.rect_of(window)


# An anchor has to be the dialog itself. A screen-sized rectangle is not one -
# on 2026-10-02 "save_ok" anchored to [8, 33, 1920, 1073] (1912x1040, the whole
# desktop) which silently made the point absolute while claiming to be tracked.
MAX_ANCHOR_AREA_FRACTION = 0.5


def _anchor_is_usable(rect, screen) -> bool:
    """False when `rect` cannot be a dialog (absent, inverted or screen-sized)."""
    if not rect:
        return False
    try:
        left, top, right, bottom = (int(value) for value in rect)
    except Exception:
        return False
    if right <= left or bottom <= top:
        return False
    try:
        screen_w, screen_h = int(screen[0]), int(screen[1])
    except Exception:
        return True
    if screen_w <= 0 or screen_h <= 0:
        return True
    area = (right - left) * (bottom - top)
    return area <= MAX_ANCHOR_AREA_FRACTION * screen_w * screen_h


def _anchor_rect(anchor_rect_fn, marker: str):
    """Normalised (left, top, right, bottom) from an injected anchor lookup."""
    if not marker or anchor_rect_fn is None:
        return None
    try:
        rect = anchor_rect_fn(marker)
    except Exception:
        return None
    if not rect:
        return None
    try:
        rect = tuple(int(value) for value in rect)
    except Exception:
        return None
    return rect if len(rect) == 4 else None


def capture_session(
    names=None,
    *,
    map_path: Path | str | None = None,
    position_fn=None,
    key_fn=None,
    anchor_rect_fn=None,
    print_fn=print,
    timeout=None,
    countdown=None,
) -> ClickMap:
    """Record one click point per target, in the order the live flow needs them.

    F8 records the point under the mouse, ESC skips that target. With
    `countdown=N` there is no key at all: each target says "hover now" and
    records itself after N seconds (works even when the console is not focused,
    so it can be started from another window).

    `position_fn()`, `key_fn(vk)` and `anchor_rect_fn(marker)` are injectable so
    the whole session can be tested without a desktop.
    """
    names = expand_names(names)
    position_fn = position_fn or _default_position
    anchor_rect_fn = anchor_rect_fn or _default_anchor_rect
    click_map = ClickMap.load(map_path)
    if not click_map.screen:
        click_map.screen = _screen_size()

    print_fn("")
    print_fn("=" * 66)
    print_fn("  FINAL BILL CLICK MAP - i-record ang mga pinipindot mo")
    print_fn("=" * 66)
    for index, name in enumerate(names, start=1):
        target = TARGETS_BY_NAME.get(name)
        if target is None:
            print_fn(f"  [{index}/{len(names)}] {name}: HINDI KILALA - laktawan")
            continue
        print_fn("")
        print_fn("-" * 66)
        print_fn(f"  [{index}/{len(names)}] {target.label}")
        for line in target.steps:
            print_fn(f"      {line}")
        if target.anchor:
            print_fn(
                f"      (anchor: window na may '{target.anchor}' - susundan ng"
                " bot ang button kahit lumipat ang popup)"
            )
        if countdown:
            print_fn(f"      >>> {float(countdown):g} segundo: i-hover NA ang mouse!")
            time.sleep(float(countdown))
            action = "capture"
        else:
            action = wait_for_capture_key(
                key_fn=key_fn, timeout=timeout, print_fn=print_fn
            )
        if action == "skip":
            print_fn("      LINALAKTAWAN (ESC).")
            continue
        if action != "capture":
            print_fn("      TIMEOUT - hindi na naghintay pa ng key.")
            break
        x, y = position_fn()
        rect = _anchor_rect(anchor_rect_fn, target.anchor)
        if not _anchor_is_usable(rect, click_map.screen):
            # A screen-sized rect is not the dialog (2026-10-02: "save_ok"
            # anchored to 1912x1040, the whole desktop). Keep the absolute
            # point instead of pretending it tracks the wrong window.
            rect = None
        point = ClickPoint(
            name=target.name,
            label=target.label,
            anchor=target.anchor,
            x=int(x),
            y=int(y),
            dx=(int(x) - rect[0]) if rect else None,
            dy=(int(y) - rect[1]) if rect else None,
            anchor_rect=rect,
            mapped_at=datetime.now().isoformat(),
        )
        click_map.set(point)
        print_fn(f"      NAITALA: {target.label} = {point.summary()}")

    click_map.mapped_at = datetime.now().isoformat()
    path = click_map.save(map_path)
    print_fn("")
    print_fn(f"  Na-save ang click map: {path}")
    print_fn(f"  Naka-map: {', '.join(click_map.points) or 'wala'}")
    print_fn("  Tingnan ulit: python -m core.agent.final_bill_click_map --show")
    _log(f"Final Bill click map saved: {path} | mapped={sorted(click_map.points)}")
    return click_map


# -- post-OK branch (isa lang ang lalabas) -------------------------------------

BRANCH_CANDIDATES = (POINT_SAVE_OK, POINT_ADMIN_NO)

# Detection: parehong title na tumatry ng MISMONG flow (final_bill_actions:
# DIALOG_TITLE_FILE_SAVE / DIALOG_TITLE_CONFIRM at ang _front_save_prompt
# fallbacks - may build na "Save" lang ang title, hindi "File save").
# Dito lang nakaharaya ang mga string para walang GUI import sa module load.
# "Call Administrator" muna bago ang save: ang HBSys-only na title ay walang
# false positive, samantalang ang "Save" ay tumatagpo rin ng ibang window.
BRANCH_MARKERS = {
    POINT_ADMIN_NO: ("Call Administrator",),
    POINT_SAVE_OK: ("File save", "Save", "Save As"),
}
BRANCH_DETECT_ORDER = (POINT_ADMIN_NO, POINT_SAVE_OK)


def console_choose_branch() -> str:
    """Kapag pindot ang F8 pero hindi nababasa ang title ng prompt: alin ang lumabas?

    Operator input (tulad ng console_capture_key): walang tanong kapag hindi
    naman pinindot ang F8, at walang exception kahit sarado ang stdin.
    """
    try:
        print("      Hindi nabasa ang title ng prompt na lumabas.")
        print("        [1] 'File save' - OK     [2] 'Call Administrator' - No")
        answer = input("      Alin ang lumabas? (1/2, ENTER = 1) > ")
    except (EOFError, KeyboardInterrupt):
        return ""
    key = answer.strip().lower()
    if key in ("", "1", "f", "file", "save", "s"):
        return POINT_SAVE_OK
    if key in ("2", "a", "admin", "administrator", "c"):
        return POINT_ADMIN_NO
    return ""


def _detect_branch_prompt(anchor_rect_fn, candidates) -> tuple:
    """(name, rect) ng pinakabukas na prompt mula sa `candidates`, o ("", None)."""
    ordered = [name for name in BRANCH_DETECT_ORDER if name in candidates]
    ordered += [name for name in candidates if name not in BRANCH_DETECT_ORDER]
    for name in ordered:
        markers = BRANCH_MARKERS.get(name)
        if not markers:
            target = TARGETS_BY_NAME.get(name)
            markers = (target.anchor,) if target and target.anchor else ()
        for marker in markers:
            rect = _anchor_rect(anchor_rect_fn, marker)
            if rect:
                return name, rect
    return "", None


def wait_for_post_ok_branch(
    candidates=BRANCH_CANDIDATES,
    *,
    anchor_rect_fn=None,
    key_fn=None,
    timeout=None,
    poll: float = 0.2,
    f8_grace: float = 1.0,
    print_fn=None,
    choose_fn=None,
) -> tuple:
    """Hintayin ang prompt na TALAGANG lumabas pagkatapos ng unang OK.

    Polls the anchor dialog of each candidate until one appears (or ESC /
    timeout). F8 is honored too - kapag pindot ang F8 pero hindi nababasa ang
    title ng prompt, itatanong ng `choose_fn` kung alin ang lumabas bago mag-
    record, kaya HINDI KAILANMAN namamatay ang F8 sa step na ito (dating bug:
    detection-only ang loop, kaya isang maling title = walang nangyayari).

    `f8_grace` (default 1 segundo) ay para hindi masalo ang F8 na ginamit pa sa
    [2/2] - doon kanina pa bago pa man magsimulang maghintay ang step na ito.

    Returns (target_name, dialog_rect):

        * detection  -> tunay na rect ng prompt
        * F8 + choose -> (name, None): walang nabasang title, ABSOLUTE ang record
        * ESC / timeout / skip -> ("", None)

    Walang click ang function na ito - puro hintay lang. `key_fn(vk)`,
    `anchor_rect_fn(marker)`, `choose_fn()` at `print_fn` are injectable so the
    whole wait runs headless in tests (position is NOT read here - only in
    branch_session after a choice was made).
    """
    candidates = tuple(candidates or ())
    anchor_rect_fn = anchor_rect_fn or _default_anchor_rect
    key_fn = key_fn or (key_down if _keys_available() else None)
    choose_fn = choose_fn or console_choose_branch
    deadline = None if timeout is None else time.time() + float(timeout)
    sleep_for = max(float(poll), 0.05)
    started = time.time()
    if print_fn is not None:
        print_fn(
            "      Hinihintay ang prompt: F8 kapag lumabas (pag hindi nababasa"
            " ang title, itatanong kung alin) - ESC = wala."
        )
    while deadline is None or time.time() < deadline:
        if key_fn is not None:
            try:
                if key_fn(KEYS["esc"]):
                    return "", None
            except Exception:
                pass
        name, rect = _detect_branch_prompt(anchor_rect_fn, candidates)
        if name:
            return name, rect
        if key_fn is not None and time.time() - started >= float(f8_grace):
            try:
                pressed = key_fn(KEYS["f8"])
            except Exception:
                pressed = False
            if pressed:
                chosen = choose_fn()
                return (chosen, None) if chosen else ("", None)
        time.sleep(sleep_for)
    return "", None


def branch_session(
    *,
    map_path: Path | str | None = None,
    position_fn=None,
    key_fn=None,
    anchor_rect_fn=None,
    print_fn=print,
    timeout=None,
    countdown=None,
    branch_timeout=None,
    f8_grace: float = 1.0,
    choose_fn=None,
) -> ClickMap:
    """Mapping session para sa TAMANG pagkasunod-sunod ng final bill prompts.

    Isa lang sa dalawa ang lalabas pagkatapos ng unang OK ("File save" OK o
    "Call Administrator" No), kaya ganito ang order:

        [1/2] Print Options - 'Final' checkbox (opsyonal, pwedeng ESC)
        [2/2] Print Options - UNANG OK (F8, TAPOS i-click ng operator)
        [3]   alin ang LUMABAS: F8 sa point na i-click, isang beses lang

    hindi hinihintay ang prompt na hindi lalabas - hinihintay ng tool kung ANO
    ang talagang lumabas, tapos iyon lang ang ni-record. Pagkatapos ng branch,
    Tapos na ang HBSys (bumabalik sa main screen), kaya tapos na rin ang session.
    """
    names = (POINT_FINAL_CHECKBOX, POINT_PRINT_OPTIONS_OK)
    position_fn = position_fn or _default_position
    anchor_rect_fn = anchor_rect_fn or _default_anchor_rect

    print_fn("")
    print_fn("=" * 66)
    print_fn("  FINAL BILL CLICK MAP - branch session (isa lang ang lalabas)")
    print_fn("=" * 66)

    click_map = ClickMap.load(map_path)
    if not click_map.screen:
        click_map.screen = _screen_size()

    def record(target, rect_override=None):
        shown = rect_override or _anchor_rect(anchor_rect_fn, target.anchor)
        if not _anchor_is_usable(shown, click_map.screen):
            # Keep the absolute point instead of tracking the wrong window.
            shown = None
        x, y = position_fn()
        point = ClickPoint(
            name=target.name,
            label=target.label,
            anchor=target.anchor,
            x=int(x),
            y=int(y),
            dx=(int(x) - shown[0]) if shown else None,
            dy=(int(y) - shown[1]) if shown else None,
            anchor_rect=shown,
            mapped_at=datetime.now().isoformat(),
        )
        click_map.set(point)
        return point

    def wait_key():
        if countdown:
            print_fn(f"      >>> {float(countdown):g} segundo: i-hover NA ang mouse!")
            time.sleep(float(countdown))
            return "capture"
        return wait_for_capture_key(
            key_fn=key_fn, timeout=timeout, print_fn=print_fn
        )

    for index, name in enumerate(names, start=1):
        target = TARGETS_BY_NAME[name]
        print_fn("")
        print_fn("-" * 66)
        print_fn(f"  [{index}/2] {target.label}")
        for line in target.steps:
            print_fn(f"      {line}")
        if target.anchor:
            print_fn(
                f"      (anchor: window na may '{target.anchor}' - susundan ng"
                " bot ang button kahit lumipat ang popup)"
            )
        action = wait_key()
        if action == "skip":
            print_fn("      LINALAKTAWAN (ESC).")
            continue
        if action != "capture":
            print_fn("      TIMEOUT - hindi na naghintay pa ng key.")
            break
        point = record(target)
        print_fn(f"      NAITALA: {target.label} = {point.summary()}")

    # -- [3]: the ONE prompt HBSys really shows ---------------------------------
    print_fn("")
    print_fn("-" * 66)
    print_fn("  [3] alin ang LUMABAS pagkatapos ng unang OK?")
    print_fn("      I-click mo na ang UNANG OK para lumabas ang prompt.")
    print_fn("      Paglabas: i-hover ang point AT pindutin ang F8 - isang beses.")
    print_fn("      (F8 kahit habang hinihintay ng tool - pag hindi nababasa ang")
    print_fn("       title ng prompt, itatanong nito kung alin ang lumabas.)")
    if branch_timeout:
        print_fn(f"      (hihinto sa {float(branch_timeout):g} segundo pag walang lumabas)")
    chosen, rect = wait_for_post_ok_branch(
        BRANCH_CANDIDATES,
        anchor_rect_fn=anchor_rect_fn,
        key_fn=key_fn,
        timeout=branch_timeout,
        f8_grace=f8_grace,
        print_fn=print_fn,
        choose_fn=choose_fn,
    )
    if not chosen:
        print_fn("      Walang prompt na lumabas (o ESC). Walang ni-record.")
    elif rect is None:
        # F8 ang nanalo sa branch (hindi nabasa ang title ng prompt): ang
        # record ang mismo ang hiniling ng operator - i-record kaagad, walang
        # pangalawang F8 na hinihintay.
        target = TARGETS_BY_NAME[chosen]
        point = record(target)
        print_fn(f"      NAITALA (F8): {target.label} = {point.summary()}")
    else:
        target = TARGETS_BY_NAME[chosen]
        print_fn(f"      Lumabas: {target.label} - i-hover ang point, pindutin ang F8.")
        action = wait_key()
        if action == "skip":
            print_fn("      LINALAKTAWAN (ESC).")
        elif action == "capture":
            point = record(target, rect_override=rect)
            print_fn(f"      NAITALA: {target.label} = {point.summary()}")

    click_map.mapped_at = datetime.now().isoformat()
    path = click_map.save(map_path)
    print_fn("")
    print_fn(f"  Na-save ang click map: {path}")
    print_fn(f"  Naka-map: {', '.join(click_map.points) or 'wala'}")
    print_fn("  Tingnan ulit: python -m core.agent.final_bill_click_map --show")
    _log(f"Final Bill click map saved (branch): {path} | "
         f"mapped={sorted(click_map.points)}")
    return click_map


def _log(message: str) -> None:
    """Activity-logger line (never fatal) - no prints in library code."""
    try:
        from core.activity_logger import logger

        logger.info(message)
    except Exception:
        pass


# -- inspect / maintain --------------------------------------------------------

def show_text(path=None) -> str:
    """What is mapped right now (used by --show)."""
    click_map = ClickMap.load(path)
    file_path = Path(path) if path else CLICK_MAP_PATH
    lines = [f"Click map: {file_path}"]
    if click_map.screen:
        width, height = (list(click_map.screen) + [0, 0])[:2]
        lines.append(f"  screen: {width}x{height}")
    else:
        lines.append("  screen: (hindi pa naitala)")
    lines.append(f"  huling mapping: {click_map.mapped_at or 'wala pa'}")
    lines.append("")
    for target in TARGETS:
        point = click_map.get(target.name)
        if point is None:
            lines.append(f"  [ ] {target.name:<18} {target.label}")
            lines.append(
                "      WALA PA - verified control lookup ang gagamitin."
            )
            continue
        lines.append(f"  [x] {target.name:<18} {target.label}")
        lines.append(f"      {point.summary()}")
    lines.append("")
    lines.append(
        "  Groups: "
        + " | ".join(
            f"{name}={','.join(members)}" for name, members in GROUPS.items()
        )
    )
    return "\n".join(lines)


def check_text(path=None, *, anchor_rect_fn=None) -> str:
    """Per target: mapped? and is its dialog open NOW (live HBSys check)."""
    click_map = ClickMap.load(path)
    anchor_rect_fn = anchor_rect_fn or _default_anchor_rect
    lines = [show_text(path), "", "Live check:"]
    for target in TARGETS:
        point = click_map.get(target.name)
        rect = _anchor_rect(anchor_rect_fn, target.anchor)
        state = "nakita" if rect else "hindi nakita"
        lines.append(
            f"  {target.name:<18} mapped={'oo' if point else 'hindi'}"
            f" | dialog '{target.anchor}': {state}"
        )
        if point and rect:
            lines.append(
                f"      click ngayon kapag bukas: {point.point_for(rect)}"
                f" (anchor {rect})"
            )
    return "\n".join(lines)


def set_manual(
    name: str,
    x: int,
    y: int,
    *,
    path=None,
    note: str = "manual entry (--set)",
) -> ClickPoint:
    """Record a point by hand (`--set save_ok=1010,630`), without a session."""
    target = TARGETS_BY_NAME.get(str(name))
    click_map = ClickMap.load(path)
    point = ClickPoint(
        name=str(name),
        label=target.label if target else str(name),
        anchor=target.anchor if target else "",
        x=int(x),
        y=int(y),
        mapped_at=datetime.now().isoformat(),
        note=note,
    )
    click_map.set(point)
    click_map.mapped_at = datetime.now().isoformat()
    click_map.save(path)
    return point


def clear_map(path=None) -> bool:
    """Delete the map file; True when it is gone (never raises)."""
    target = Path(path) if path else CLICK_MAP_PATH
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return True
    except Exception:
        return False


def _parse_manual(value: str):
    """'save_ok=1010,630' -> ('save_ok', 1010, 630)."""
    name, _, coords = str(value).partition("=")
    parts = [part for part in coords.replace(" ", "").split(",") if part]
    if not name.strip() or len(parts) < 2:
        raise ValueError(f"format: name=x,y (natanggap: {value!r})")
    return name.strip(), int(parts[0]), int(parts[1])


# -- CLI -----------------------------------------------------------------------

def main(argv=None) -> int:
    """`python -m core.agent.final_bill_click_map --help` for the full list."""
    parser = argparse.ArgumentParser(
        prog="python -m core.agent.final_bill_click_map",
        description=(
            "I-record ang click points ng Final Bill prompts: unang OK"
            " (Print Options), OK (File save), No (Call Administrator) at ang"
            " 'Final' checkbox. F8 = i-record, ESC = laktawan."
        ),
    )
    parser.add_argument(
        "--map",
        nargs="*",
        metavar="NAME",
        help=(
            "magsimula ng mapping session; walang pangalan = tamang order "
            f"({' | '.join(GROUPS)})"
        ),
    )
    parser.add_argument(
        "--branch",
        action="store_true",
        help=(
            "branch session para sa TAMANG order: checkbox, unang OK, tapos"
            " isa lang sa File save/Call Administrator ang hihintayin"
        ),
    )
    parser.add_argument(
        "--branch-timeout",
        dest="branch_timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="hihinto ang branch wait pag walang prompt sa N segundo (hal. 60)",
    )
    parser.add_argument(
        "--in",
        dest="countdown",
        type=float,
        metavar="SECONDS",
        help="sa halip na F8: i-hover na lang, auto-record pagkatapos ng N segundo",
    )
    parser.add_argument("--show", action="store_true", help="ipakita ang click map")
    parser.add_argument(
        "--check",
        action="store_true",
        help="ipakita kung bukas ngayon ang mga dialog na naka-map",
    )
    parser.add_argument("--clear", action="store_true", help="burahin ang click map")
    parser.add_argument(
        "--set",
        dest="manual",
        action="append",
        metavar="NAME=X,Y",
        help="manu-manong point, hal. --set save_ok=1010,630",
    )
    parser.add_argument("--path", default=None, help="ibang click map file")
    args = parser.parse_args(argv)

    if args.clear:
        if clear_map(args.path):
            print("Click map deleted.")
            return 0
        print("Could not delete the click map.")
        return 1

    if args.manual:
        for value in args.manual:
            try:
                name, x, y = _parse_manual(value)
            except (ValueError, TypeError) as exc:
                print(f"  {exc}")
                return 2
            point = set_manual(name, x, y, path=args.path)
            print(f"  NAITALA (manual): {point.name} = {point.summary()}")
        _log(f"Final Bill click map set manually: {args.manual}")

    if args.check:
        print(check_text(args.path))
        return 0

    if args.show:
        print(show_text(args.path))
        return 0

    if args.map is not None:
        capture_session(
            args.map, map_path=args.path, countdown=args.countdown
        )
        print("")
        print(show_text(args.path))
        return 0

    if args.branch:
        branch_session(
            map_path=args.path,
            countdown=args.countdown,
            branch_timeout=args.branch_timeout,
        )
        print("")
        print(show_text(args.path))
        return 0

    parser.print_help()
    print("")
    print(show_text(args.path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
