"""Final Bill click map - coordinate GETTER (kuhanin ang X,Y mula sa screen).

Bakit may GUI na ito
---------------------
Tatlong paraan na ngayon para maitala ang click points ng Final Bill:
ang F8 mapper (live na session), ang coordinate editor (manu-manong type),
at ang GETTER na ito - para sa operator na nakatingin sa totoong button
sa HBSys at gusto ang mismong coordinate nito nang hindi hinahanap ang numero:

    * LIVE na X,Y ng cursor - nakikita habang inii-hover ang target button;
    * pindutin ang F8 habang NAKA-ARM (o ang countdown na "Kumuha sa 3s")
      - mapupunta ang coordinate sa X/Y field nang hindi iniiwan ang
      button sa screen;
    * piliin ang target (final_checkbox, print_options_ok, save_ok,
      admin_no) at I-SAVE - parehong logs/final_bill_click_map.json ang
      isinusulat (walang bagong format, walang duplicated writer);
    * Kopyahin - ang "X,Y" ay mapupunta sa clipboard para idikit sa
      coordinate editor kung doon mas sanay ang operator.

Hindi ito humahawak o nagpapadala ng click/keystroke sa HBSys (basa lang
ng pyautogui.position at GetAsyncKeyState). Ang tanging posibleng kontak
ay ang default na anchor lookup (buksang dialog rectangle) kapag nagse-save
- kapareho ng editor. Ang F8 ay BINABATAYAN lamang kapag naka-arm ang
getter; ESC (o ang Tigil button) ang nagdidisarm.

Anchor rule kapag nagse-save (galing sa coordinate editor):
    1. kapag BUKAS ang dialog ng point habang nagse-save, doon base ang
       dx/dy offset - sinusundan pa rin ng click ang popup;
    2. kundi, sa huling kilalang anchor rect ng point;
    3. kung wala talaga, absolute ang point (pareho ng --set name=x,y).

Run:   python -m gui.final_bill_coordinate_getter   (puwede ring doble-tap)
Test:  python -m unittest tests.test_gui_final_bill_coordinate_getter
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import final_bill_click_map as click_map  # noqa: E402
from gui.final_bill_map_editor import (  # noqa: E402
    DEFAULT_MAP_PATH,
    parse_xy,
    row_text,
    save_coordinates,
)

# Gaano kadalas i-refresh ang live na X,Y / binabantayan ang F8 (ms).
LIVE_POLL_MS = 100
KEY_POLL_MS = 50
# Default na countdown ng "Kumuha sa ...s" (segundo).
DEFAULT_COUNTDOWN = 3.0


def format_xy(x, y) -> str:
    """(1010, 630) -> '1010,630' (teksto ng X/Y field at clipboard)."""
    return f"{int(x)},{int(y)}"


def status_for(point) -> str:
    """Status label ng selected target ('WALA PA' kapag walang point)."""
    if point is None:
        return "WALA PA - control lookup ang gagamitin"
    return point.summary()


def mapped_count(click_map_obj) -> int:
    """Ilan sa mga target ang may point na sa click map."""
    return sum(
        1
        for target in click_map.TARGETS
        if click_map_obj.get(target.name) is not None
    )


class FinalBillCoordinateGetter(tk.Tk):
    """Mini window (laging naka-on-top): live X,Y + F8/countdown capture.

    Ang tatlong injected na ito ang paraan ng tests na hindi mahawakan ang
    tunay na desktop: `position_fn()` -> (x, y), `key_fn(vk)` -> bool, at
    `anchor_rect_fn(marker)` -> rect o None (kapareho ng capture_session ng
    F8 mapper).
    """

    def __init__(
        self,
        *,
        map_path=None,
        position_fn=None,
        key_fn=None,
        anchor_rect_fn=None,
        countdown=DEFAULT_COUNTDOWN,
    ):
        super().__init__()
        self.title("Final Bill Click Map - Coordinate Getter")
        self.resizable(False, False)
        try:
            # Laging naka-on-top: makita pa rin ang live X,Y kahit nakaharang
            # ang HBSys sa window ng getter.
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.map_path = Path(map_path) if map_path else DEFAULT_MAP_PATH
        self.position_fn = position_fn or click_map._default_position
        self._injected_key_fn = key_fn
        self.anchor_rect_fn = anchor_rect_fn
        self.countdown_seconds = float(countdown)
        self.targets = list(click_map.TARGETS)
        self.armed = False
        self._counting = False
        self._countdown_left = 0.0
        self.message_var = tk.StringVar(value="")
        self.live_var = tk.StringVar(value="X: ____   Y: ____")
        self.arm_var = tk.StringVar(value="Hindi naka-arm")
        self.count_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="")
        self._build_layout()
        self.refresh()
        self.after(LIVE_POLL_MS, self._poll_live)

    # -- layout -------------------------------------------------------------

    def _build_layout(self) -> None:
        header = ttk.Frame(self, padding=(10, 8, 10, 2))
        header.pack(fill="x")
        ttk.Label(
            header,
            text="I-hover ang cursor sa target, tapos kunin ang coordinate.",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "Live ang X,Y sa ibaba. Ang F8 (kapag naka-arm) o ang "
                "countdown ang naglalagay sa field - hindi ito nagki-click "
                "ng kahit ano sa HBSys."
            ),
            foreground="#555555",
        ).pack(anchor="w")

        target_frame = ttk.LabelFrame(self, text="  Target  ", padding=(8, 4))
        target_frame.pack(fill="x", padx=10, pady=3)
        self.target_box = ttk.Combobox(
            target_frame,
            state="readonly",
            width=46,
            values=[
                f"{target.name} - {target.label}" for target in self.targets
            ],
        )
        self.target_box.grid(row=0, column=0, sticky="w")
        self.target_box.current(0)
        self.target_box.bind(
            "<<ComboboxSelected>>", lambda _event: self.refresh()
        )
        ttk.Label(
            target_frame, textvariable=self.count_var, foreground="#1e8e3e"
        ).grid(row=0, column=1, padx=(10, 0))
        ttk.Label(
            target_frame,
            textvariable=self.status_var,
            foreground="#1e8e3e",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))

        live_frame = ttk.LabelFrame(
            self, text="  Live na posisyon ng cursor  ", padding=(8, 4)
        )
        live_frame.pack(fill="x", padx=10, pady=3)
        ttk.Label(
            live_frame,
            textvariable=self.live_var,
            font=("Consolas", 14, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            live_frame,
            textvariable=self.arm_var,
            foreground="#b06000",
        ).pack(anchor="w")

        entry_frame = ttk.LabelFrame(
            self, text="  Nakuha (maaaring i-type rin)  ", padding=(8, 4)
        )
        entry_frame.pack(fill="x", padx=10, pady=3)
        ttk.Label(entry_frame, text="X:").grid(row=0, column=0, padx=(0, 3))
        self.entry_x = ttk.Entry(entry_frame, width=10)
        self.entry_x.grid(row=0, column=1)
        ttk.Label(entry_frame, text="Y:").grid(row=0, column=2, padx=(8, 3))
        self.entry_y = ttk.Entry(entry_frame, width=10)
        self.entry_y.grid(row=0, column=3)
        ttk.Button(
            entry_frame, text="Kopyahin", command=self.copy_xy
        ).grid(row=0, column=4, padx=(12, 0))

        actions = ttk.Frame(self, padding=(10, 6))
        actions.pack(fill="x")
        self.arm_btn = ttk.Button(
            actions, text="I-arm / Tigil ang F8", command=self.toggle_arm
        )
        self.arm_btn.pack(side="left")
        self.countdown_btn = ttk.Button(
            actions,
            text=f"Kumuha sa {self.countdown_seconds:g}s",
            command=self.start_countdown,
        )
        self.countdown_btn.pack(side="left", padx=(8, 0))
        ttk.Button(
            actions, text="I-save sa .json", command=self.save
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            actions, text="I-refresh mula sa file", command=self.refresh
        ).pack(side="left", padx=(8, 0))

        status = ttk.Frame(self, padding=(10, 0, 10, 8))
        status.pack(fill="x")
        ttk.Label(
            status,
            textvariable=self.message_var,
            relief="sunken",
            anchor="w",
            padding=4,
        ).pack(fill="x")

    # -- capture ------------------------------------------------------------

    def _resolve_key_fn(self):
        """key_fn: na-inject > tunay na GetAsyncKeyState > None (walang win32)."""
        if self._injected_key_fn is not None:
            return self._injected_key_fn
        if click_map._keys_available():
            return click_map.key_down
        return None

    def _alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    def toggle_arm(self) -> None:
        if self.armed:
            self.disarm("Tinigil ang arm - hindi na binabantayan ang F8.")
            return
        if self._resolve_key_fn() is None:
            self._set_message(
                "Hindi mabasa ang F8 dito (kulang ang win32api) - gamitin "
                "ang countdown sa tabi."
            )
            return
        if self._counting:
            self._set_message("May countdown pa - hintayin munang matapos.")
            return
        self.armed = True
        self.arm_var.set(
            "Naka-ARM: i-hover ang button, pindutin ang F8 (ESC = itigil)"
        )
        self.after(KEY_POLL_MS, self._poll_key)

    def disarm(self, reason: str = "") -> None:
        self.armed = False
        self.arm_var.set("Hindi naka-arm")
        if reason:
            self._set_message(reason)

    def _poll_key(self) -> None:
        if not self.armed or not self._alive():
            return
        key_fn = self._resolve_key_fn()
        if key_fn is None:
            self.disarm("Nawala ang F8 reader - tinigil ang arm.")
            return
        try:
            skip = key_fn(click_map.KEYS["esc"])
            record = key_fn(click_map.KEYS["f8"])
        except Exception:  # noqa: BLE001 - unreadable key state is just 'no'
            skip = record = False
        if skip:
            self.disarm("ESC - tinigil ang arm.")
            return
        if record:
            self._capture("F8")
        self.after(KEY_POLL_MS, self._poll_key)

    def capture(self) -> None:
        """Public: basahin ang kasalukuyang cursor papunta sa X/Y field."""
        self._capture("cursor")

    def _capture(self, source: str) -> None:
        try:
            x, y = self.position_fn()
            x, y = int(x), int(y)
            # Negatibo = Filipino error; HINDI mapupunta sa field ang mali.
            parse_xy(format_xy(x, y))
        except ValueError as exc:
            self._set_message(f"Hindi magamit ang cursor: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - report, never crash the GUI
            self._set_message(f"Hindi mabasa ang cursor: {exc}")
            return
        self.entry_x.delete(0, "end")
        self.entry_x.insert(0, str(x))
        self.entry_y.delete(0, "end")
        self.entry_y.insert(0, str(y))
        self._set_message(
            f"Nakuha mula sa {source}: {format_xy(x, y)} - piliin ang target "
            "sa itaas, tapos I-SAVE (hindi pa ito naka-save)."
        )

    def start_countdown(self) -> None:
        if self._counting:
            return
        if self.armed:
            self._set_message(
                "Naka-arm ang F8 - itigil muna bago gamitin ang countdown."
            )
            return
        self._counting = True
        self._countdown_left = self.countdown_seconds
        self.countdown_btn.configure(state="disabled")
        self._countdown_tick()

    def _countdown_tick(self) -> None:
        if not self._counting or not self._alive():
            return
        if self._countdown_left <= 0:
            self._counting = False
            self.countdown_btn.configure(state="normal")
            self._capture("countdown")
            return
        self._set_message(
            f"Kukuha sa {self._countdown_left:g}s - i-hover NA ang mouse sa "
            "target!"
        )
        self._countdown_left -= 1.0
        self.after(1000, self._countdown_tick)

    # -- save / copy / refresh ---------------------------------------------

    def _selected_target(self):
        index = self.target_box.current()
        if index is None or not 0 <= index < len(self.targets):
            return self.targets[0]
        return self.targets[index]

    def save(self) -> None:
        """I-save ang field ng TARGET na iisa sa .json (walang partial write)."""
        target = self._selected_target()
        value = row_text(self.entry_x.get(), self.entry_y.get())
        if not value:
            self._set_message(
                "Walang laman ang X/Y - mag-capture o mag-type muna."
            )
            return
        try:
            saved = save_coordinates(
                {target.name: value},
                path=self.map_path,
                anchor_rect_fn=self.anchor_rect_fn,
            )
        except ValueError as exc:
            messagebox.showerror("Coordinate Getter", str(exc), parent=self)
            return
        except Exception as exc:  # noqa: BLE001 - report, never crash the GUI
            messagebox.showerror(
                "Coordinate Getter",
                f"Hindi ma-save ang .json:\n{exc}",
                parent=self,
            )
            return
        self.refresh()
        if saved:
            point = next(iter(saved.values()))
            self._set_message(f"NA-SAVE: {target.name} = {point.summary()}")
        else:
            self._set_message("Walang nabago sa .json.")

    def copy_xy(self) -> None:
        value = row_text(self.entry_x.get(), self.entry_y.get())
        if not value:
            self._set_message("Walang laman ang X/Y para kopyahin.")
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self._set_message(f"Kinopya: {value} - puwedeng idikit sa editor.")

    def refresh(self) -> None:
        """Basahin ang .json: status ng target + bilang ng naka-save."""
        click_map_obj = click_map.ClickMap.load(self.map_path)
        point = click_map_obj.get(self._selected_target().name)
        self.status_var.set(status_for(point))
        self.count_var.set(
            f"{mapped_count(click_map_obj)}/{len(self.targets)} ang naka-save"
        )
        self._set_message(
            f"{self.map_path} | huling update: "
            f"{click_map_obj.mapped_at or 'wala pa'}"
        )

    def _poll_live(self) -> None:
        if not self._alive():
            return
        try:
            x, y = self.position_fn()
            self.live_var.set(f"X: {int(x)}   Y: {int(y)}")
        except Exception:  # noqa: BLE001 - unreadable cursor is just '----'
            self.live_var.set("X: ----   Y: ---- (hindi mabasa ang cursor)")
        self.after(LIVE_POLL_MS, self._poll_live)

    def _set_message(self, text: str) -> None:
        self.message_var.set(str(text))


def main() -> int:
    getter = FinalBillCoordinateGetter()
    getter.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())