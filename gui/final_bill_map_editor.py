"""Final Bill click map - coordinate editor (manu-manong pag-type ng points).

Bakit may GUI na ito
---------------------
Ang F8 mapper (core/agent/final_bill_click_map.py, --map / --branch) ang
pinakamadaling paraan para maitala ang totoong click points. Pero kapag ayaw
o hindi mapindot ang F8, puwedeng i-type ang coordinates nang direkta dito:

    * bawat field ay MAY LABEL kung aling button ang nilalagyan -
      final_checkbox, print_options_ok, save_ok, admin_no;
    * i-type ang X at Y (pixels), pindutin ang I-SAVE, at mabubuo ang
      logs/final_bill_click_map.json - eksaktong parehong file na binabasa
      ng Final Bill flow (walang bagong format, walang duplicated writer);
    * BLANGKONG field = hindi babaguhin ang naka-save na point;
    * ang bawat Burahin ay nagtatanggal ng point (bumabalik ang button sa
      verified control lookup ng flow).

Anchor rule kapag nagse-save:
    1. kapag BUKAS ang dialog ng point habang nagse-save, doon base ang
       dx/dy offset - sinusundan pa rin ng click ang popup;
    2. kundi, sa huling kilalang anchor rect ng point;
    3. kung wala talaga, absolute ang point (pareho ng --set name=x,y).

Tinatanggap ang format na "1010,630" o "1010 630" (buong numero, hindi
negatibo). Pagbasa at pagsulat lang ng click map file ang ginagawa nito -
hindi ito humahawak o humahanap sa HBSys maliban sa opsyonal na pagtingin sa
bukas na dialog rectangle para sa anchoring.

Run:   python -m gui.final_bill_map_editor   (puwede ring doble-tap)
Test:  python -m unittest tests.test_gui_final_bill_map_editor
"""

from __future__ import annotations

import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.agent import final_bill_click_map as click_map  # noqa: E402

# Absolute kahit saang cwd nabuksan ang script - laging itong .json ang
# babasahin at susulatin ng Final Bill flow.
DEFAULT_MAP_PATH = PROJECT_ROOT / click_map.CLICK_MAP_PATH

NOTE_MANUAL = "manual entry (coordinate editor)"


def parse_xy(text) -> tuple | None:
    """'1010,630' (o '1010 630') -> (1010, 630); blank -> None.

    ValueError (Filipino) kapag hindi X,Y na buong numero - hindi ito
    nagsasara ng GUI, ang tawag lang ang nagpapakita nito sa operator.
    """
    raw = "" if text is None else str(text).strip()
    if not raw:
        return None
    parts = [
        part.strip()
        for part in raw.replace(";", ",").replace(" ", ",").split(",")
        if part.strip()
    ]
    if len(parts) != 2:
        raise ValueError(
            f"kailangan ng format na X,Y (hal. 1010,630) - nakuha: {raw!r}"
        )
    try:
        x = int(parts[0])
        y = int(parts[1])
    except ValueError as exc:
        raise ValueError(
            f"buong numero ang X at Y (hal. 1010,630) - nakuha: {raw!r}"
        ) from exc
    if x < 0 or y < 0:
        raise ValueError(
            f"hindi pwedeng negatibo ang coordinate - nakuha: ({x}, {y})"
        )
    return x, y


def row_text(x_text, y_text) -> str:
    """Dalawang entry field -> isang 'X,Y' teksto ('' kapag parehong blank).

    Kapag isa lang ang napuno, nagiging "X," ito - na sinasadyang i-reject
    ni parse_xy (kailangan parehong X at Y, o blanks pareho).
    """
    x = "" if x_text is None else str(x_text).strip()
    y = "" if y_text is None else str(y_text).strip()
    if not x and not y:
        return ""
    return f"{x},{y}"


def build_point(target, x: int, y: int, *, anchor_rect=None, note=NOTE_MANUAL):
    """ClickPoint para sa target; naka-anchor kapag may kilalang dialog rect."""
    rect = None
    dx = dy = None
    if anchor_rect and len(anchor_rect) == 4:
        rect = tuple(int(value) for value in anchor_rect)
        dx = int(x) - rect[0]
        dy = int(y) - rect[1]
    return click_map.ClickPoint(
        name=target.name,
        label=target.label,
        anchor=target.anchor,
        x=int(x),
        y=int(y),
        dx=dx,
        dy=dy,
        anchor_rect=rect,
        mapped_at=datetime.now().isoformat(),
        note=note,
    )


def _anchor_rect_for(target, anchor_rect_fn, old_point) -> tuple | None:
    """Live rect ngayon > huling kilalang anchor rect > None (absolute)."""
    rect = click_map._anchor_rect(anchor_rect_fn, target.anchor)
    if rect:
        return rect
    if old_point is not None and old_point.anchor_rect:
        rect = tuple(int(value) for value in old_point.anchor_rect)
        return rect if len(rect) == 4 else None
    return None


def save_coordinates(rows: dict, *, path=None, anchor_rect_fn=None) -> dict:
    """I-save ang mga bagong coordinates sa click map (.json).

    rows: {target_name: 'X,Y'} - blank/wala = hindi babaguhin ang stored
    point. LAHAT ng field ay ni-validate MUNA (walang partial save) bago
    isulat ang file nang isang beses.

    anchor_rect_fn: live-dialog lookup (marker -> rect) para sa anchoring;
    kapag None ang binigay, ginagamit ang default na HBSys window finder -
    kaya laging nagpapasa ng fake ang tests (hindi nila hinahawakan ang
    HBSys).

    Returns {name: ClickPoint} na na-save. Raises ValueError (may listahan
    ng maling field) o OSError kapag hindi masulat ang file.
    """
    parsed: dict = {}
    errors: list = []
    for target in click_map.TARGETS:
        try:
            xy = parse_xy((rows or {}).get(target.name, ""))
        except ValueError as exc:
            errors.append(f"{target.name} ({target.label}): {exc}")
            continue
        if xy is not None:
            parsed[target.name] = xy
    if errors:
        raise ValueError("Hindi ma-save ang mga field:\n" + "\n".join(errors))
    if not parsed:
        return {}
    if anchor_rect_fn is None:
        anchor_rect_fn = click_map._default_anchor_rect
    click_map_obj = click_map.ClickMap.load(path)
    saved: dict = {}
    for name, (x, y) in parsed.items():
        target = click_map.TARGETS_BY_NAME.get(name)
        if target is None:
            continue
        old = click_map_obj.get(name)
        point = build_point(
            target,
            x,
            y,
            anchor_rect=_anchor_rect_for(target, anchor_rect_fn, old),
        )
        click_map_obj.set(point)
        saved[name] = point
    if not click_map_obj.screen:
        click_map_obj.screen = tuple(click_map._screen_size())
    click_map_obj.mapped_at = datetime.now().isoformat()
    click_map_obj.save(path)
    click_map._log(
        "Final Bill click map edited (GUI): "
        + ", ".join(f"{name}={point.summary()}" for name, point in saved.items())
    )
    return saved


def delete_points(names, *, path=None) -> tuple:
    """Tanggalin ang mga point sa file; returns ang mga nabura talaga."""
    click_map_obj = click_map.ClickMap.load(path)
    removed = []
    for name in names or ():
        key = str(name).strip()
        if key and key in click_map_obj.points:
            del click_map_obj.points[key]
            removed.append(key)
    if removed:
        click_map_obj.mapped_at = datetime.now().isoformat()
        click_map_obj.save(path)
        click_map._log(
            f"Final Bill click map cleared (GUI): {', '.join(removed)}"
        )
    return tuple(removed)


class FinalBillMapEditor(tk.Tk):
    """Window: isang labeled row bawat target, X at Y field, I-SAVE sa .json."""

    def __init__(self, *, map_path=None, anchor_rect_fn=None):
        super().__init__()
        self.title("Final Bill Click Map - Coordinate Editor")
        self.resizable(False, False)
        self.map_path = Path(map_path) if map_path else DEFAULT_MAP_PATH
        # Live-dialog lookup (marker -> rect); na-inject ng tests para hindi
        # kailangang buksan ang HBSys.
        self.anchor_rect_fn = anchor_rect_fn
        self.entries: dict = {}
        self.status_vars: dict = {}
        self.message_var = tk.StringVar(value="")
        self._build_layout()
        self.refresh()

    def _build_layout(self) -> None:
        header = ttk.Frame(self, padding=(10, 8, 10, 2))
        header.pack(fill="x")
        ttk.Label(
            header,
            text="I-type ang X at Y (pixels) bawat button, tapos I-SAVE.",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            header,
            text=(
                "Blangkong field = hindi babaguhin ang naka-save. "
                "Ang .json na ito ang binabasa ng Final Bill flow."
            ),
            foreground="#555555",
        ).pack(anchor="w")

        for target in click_map.TARGETS:
            self._build_row(target)

        actions = ttk.Frame(self, padding=(10, 6))
        actions.pack(fill="x")
        ttk.Button(
            actions, text="I-SAVE lahat sa .json", command=self.save
        ).pack(side="left")
        ttk.Button(
            actions, text="I-refresh mula sa file", command=self.refresh
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            actions, text="Burahin lahat", command=self.delete_all
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

    def _build_row(self, target) -> None:
        frame = ttk.LabelFrame(self, text=f"  {target.name}  ", padding=(8, 4))
        frame.pack(fill="x", padx=10, pady=3)

        ttk.Label(
            frame, text=target.label, font=("Segoe UI", 9, "bold")
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            frame,
            text=f"Dialog: '{target.anchor}'",
            foreground="#555555",
        ).grid(row=1, column=0, sticky="w")

        ttk.Label(frame, text="X:").grid(row=0, column=1, padx=(16, 3))
        entry_x = ttk.Entry(frame, width=9)
        entry_x.grid(row=0, column=2)
        ttk.Label(frame, text="Y:").grid(row=0, column=3, padx=(8, 3))
        entry_y = ttk.Entry(frame, width=9)
        entry_y.grid(row=0, column=4)
        ttk.Button(
            frame,
            text="Burahin",
            command=lambda name=target.name: self.delete_one(name),
        ).grid(row=0, column=5, padx=(12, 0))

        status_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=status_var, foreground="#1e8e3e").grid(
            row=1, column=1, columnspan=4, sticky="w", padx=(16, 0)
        )
        self.entries[target.name] = (entry_x, entry_y)
        self.status_vars[target.name] = status_var

    def _set_message(self, text: str) -> None:
        self.message_var.set(text)

    def refresh(self) -> None:
        """Basahin ang .json: i-fill ang field at status ng bawat target."""
        click_map_obj = click_map.ClickMap.load(self.map_path)
        mapped = 0
        for target in click_map.TARGETS:
            entry_x, entry_y = self.entries[target.name]
            point = click_map_obj.get(target.name)
            entry_x.delete(0, "end")
            entry_y.delete(0, "end")
            if point is None:
                self.status_vars[target.name].set(
                    "WALA PA - control lookup ang gagamitin"
                )
                continue
            mapped += 1
            entry_x.insert(0, str(int(point.x)))
            entry_y.insert(0, str(int(point.y)))
            self.status_vars[target.name].set(point.summary())
        self._set_message(
            f"{self.map_path} | {mapped}/{len(click_map.TARGETS)} ang naka-save"
            f" | huling update: {click_map_obj.mapped_at or 'wala pa'}"
        )

    def save(self) -> None:
        """I-validate LAHAT ng field, tapos isulat ang .json nang isang beses."""
        rows = {}
        for target in click_map.TARGETS:
            entry_x, entry_y = self.entries[target.name]
            rows[target.name] = row_text(entry_x.get(), entry_y.get())
        if not any(rows.values()):
            self._set_message("Walang binagong field - walang isinulat.")
            return
        try:
            saved = save_coordinates(
                rows, path=self.map_path, anchor_rect_fn=self.anchor_rect_fn
            )
        except ValueError as exc:
            messagebox.showerror("Coordinate Editor", str(exc), parent=self)
            return
        except Exception as exc:  # noqa: BLE001 - report, never crash the GUI
            messagebox.showerror(
                "Coordinate Editor", f"Hindi ma-save ang .json:\n{exc}", parent=self
            )
            return
        self.refresh()
        lines = "\n".join(
            f"  {name} = {point.summary()}" for name, point in saved.items()
        )
        messagebox.showinfo(
            "Coordinate Editor",
            f"Na-save sa {self.map_path}:\n{lines}",
            parent=self,
        )

    def delete_one(self, name: str) -> None:
        if not messagebox.askyesno(
            "Burahin ang point",
            f"Burahin ang '{name}' sa click map?\n"
            "Babalik ito sa verified control lookup ng flow.",
            parent=self,
        ):
            return
        removed = delete_points([name], path=self.map_path)
        self.refresh()
        if removed:
            self._set_message(f"Na-bura: {', '.join(removed)}.")
        else:
            self._set_message(f"Wala namang naka-save na '{name}'.")

    def delete_all(self) -> None:
        if not messagebox.askyesno(
            "Burahin lahat",
            "Burahin lahat ng Final Bill click points?\n"
            "Lahat ng button ay babalik sa control lookup.",
            parent=self,
        ):
            return
        removed = delete_points(
            [target.name for target in click_map.TARGETS], path=self.map_path
        )
        self.refresh()
        if removed:
            self._set_message(f"Na-bura: {', '.join(removed)}.")
        else:
            self._set_message("Wala ring naka-save - walang nabura.")


def main() -> int:
    editor = FinalBillMapEditor()
    editor.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
