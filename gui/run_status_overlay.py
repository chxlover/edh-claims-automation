"""Small always-on-top run-status indicator for the Claims GUI.

The robot mascot (images/robot-coding-3d-icon.png) is docked at the
lower-left screen corner (near the Windows Start button) so the
operator can see at a glance whether the Claims Bot is working:

- Idle    -> gray status lamp; the icon's own eyes show as-is.
- Running -> BLINKING green lamp, SHINING status text with an
             "on process" dot animation (1 -> 2 -> 3 dots) and
             the robot's eyes glow green on top of the icon.
- Count   -> when a workflow node reports how many patients are
             left, that shows on its OWN line under the status text
             ("Date Fill 2 left" / "Final Bill 1 left") so the
             status line keeps its meaning. Empty when there is no
             count. Purely display-only (core/workflow_progress.py).

The overlay is DRAGGABLE: press anywhere inside it and move the mouse
to park it wherever the operator wants. The new position sticks for
the rest of the session — the poll loop no longer snaps the overlay
back to the corner. A plain click (no drag) still brings the main
GUI window forward.

Display-only by design: it never takes focus (safe while HBSys
mouse/keyboard automation is live), and it hides together with the
main window.

Standalone demo / visual check:

    python gui/run_status_overlay.py
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path

# -- palette (dark "automation" style) ---------------------------------------
COL_BG = "#0F172A"          # panel background
COL_BORDER = "#334155"      # panel border
COL_ACCENT = "#38BDF8"      # cyan accent (automation look)
COL_BODY = "#1E293B"        # placeholder robot fill (icon fallback)
COL_TITLE = "#E5E7EB"       # title text
COL_LAMP_IDLE = "#64748B"   # gray status lamp while idle
COL_LAMP_RUN_A = "#22C55E"  # blinking green lamp (on)
COL_LAMP_RUN_B = "#86EFAC"  # blinking green lamp (off)
COL_TEXT_RUN = "#BBF7D0"    # status text while running
COL_TEXT_SHINE = "#F0FDF4"  # status text "shine" phase
COL_TEXT_IDLE = "#CBD5E1"   # status text while idle
COL_EYE_RUN = "#BBF7D0"     # glowing eye core while running
COL_EYE_HALO_RUN = "#4ADE80"  # glowing eye halo while running
COL_TEXT_COUNT = "#7DD3FC"  # remaining-patient count (cyan, always legible)

# Long enough for "Date Fill 10 left" / "Final Bill 12 left" plus the
# 3-dot animation; longer labels still truncate, never wrap or overflow.
MAX_STATUS_CHARS = 28

# Robot mascot icon — 450x450 RGBA PNG with a fully transparent
# background, resized to the ROBOT slot at runtime.
ICON_PATH = (
    Path(__file__).resolve().parent.parent
    / "images"
    / "robot-coding-3d-icon.png"
)
ICON_SIZE = 450

# Eye glow geometry, measured on the 450x450 icon (the two bright
# face spots), scaled to the ROBOT slot at runtime.
EYE_CENTERS_450 = (
    (180.0, 164.4),   # left eye
    (271.3, 164.4),   # right eye
)
EYE_RADIUS_450 = 16.5     # ~33 px eye diameter / 2
EYE_HALO_FACTOR = 2.1     # halo radius = eye radius * this


class RunStatusOverlay:
    """Display-only draggable robot status window near the taskbar start corner."""

    WIDTH = 288
    HEIGHT = 70              # 52 + the extra patient-count row
    COUNT_ROW_HEIGHT = 18    # px of the "Date Fill N left" line
    MARGIN_X = 10
    TASKBAR_GAP = 54         # sits just above the taskbar / Start corner
    ROBOT = 44               # robot icon size (px)
    LAMP = 12                # status lamp dot size (px)
    DRAG_THRESHOLD = 4       # px (Manhattan) before a press counts as a drag

    def __init__(self, owner: tk.Tk) -> None:
        self.owner = owner
        self.window = tk.Toplevel(owner)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        try:
            self.window.attributes("-toolwindow", True)
        except tk.TclError:
            pass

        self._running = False
        self._label = "Idle"
        self._count = ""      # remaining patients ("Date Fill 2 left" or "")
        self._icon_photo: tk.PhotoImage | None = None
        self._eye_ids: list[tuple[int, int]] = []

        # -- running animation state (blink / shine / dots) --------
        self._blink_on = False
        self._phase = 0          # 0,1,2 -> 1,2,3 status dots

        # -- drag state (the overlay is movable by the operator) ----------
        self._press_x: int | None = None
        self._press_y: int | None = None
        self._win_x = 0
        self._win_y = 0
        self._dragging = False
        self._user_moved = False   # True once the operator has dragged it

        self.frame = tk.Frame(
            self.window,
            bg=COL_BG,
            highlightbackground=COL_BORDER,
            highlightthickness=1,
            bd=0,
        )
        self.frame.pack(fill="both", expand=True)

        # -- robot mascot (icon image on the robot canvas) ----------------
        self.robot_canvas = tk.Canvas(
            self.frame,
            width=self.ROBOT,
            height=self.ROBOT,
            bg=COL_BG,
            highlightthickness=0,
            bd=0,
        )
        self.robot_canvas.grid(row=0, column=0, rowspan=3, padx=(10, 8), pady=4)
        if self._load_robot_icon():
            self._create_eye_glow()

        tk.Label(
            self.frame,
            text="EDH Claim Automation System",
            bg=COL_BG,
            fg=COL_TITLE,
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(6, 0))
        self.status_label = tk.Label(
            self.frame,
            text="Idle",
            bg=COL_BG,
            fg=COL_TEXT_IDLE,
            font=("Segoe UI", 8),
        )
        self.status_label.grid(row=1, column=1, sticky="w", padx=(0, 8), pady=(0, 0))

        # -- remaining-patient row (Date Fill / Final Bill, 2026-10-09) -----
        # Its OWN line directly under the status text, so the operator reads
        # "Workflow running..." AND how many patients are left instead of one
        # replacing the other. Empty (blank) whenever there is no count.
        self.count_label = tk.Label(
            self.frame,
            text="",
            bg=COL_BG,
            fg=COL_TEXT_COUNT,
            font=("Segoe UI", 8, "bold"),
        )
        self.count_label.grid(row=2, column=1, sticky="w", padx=(0, 8), pady=(0, 6))

        # -- status lamp dot (the static icon cannot blink) ---------------
        self.lamp_canvas = tk.Canvas(
            self.frame,
            width=self.LAMP,
            height=self.LAMP,
            bg=COL_BG,
            highlightthickness=0,
            bd=0,
        )
        self._lamp_id = self.lamp_canvas.create_oval(
            1, 1, self.LAMP - 1, self.LAMP - 1,
            fill=COL_LAMP_IDLE, outline="",
        )
        self.lamp_canvas.grid(row=0, column=2, rowspan=2, padx=(0, 10))

        self._apply_state()
        self._position()
        self._bind_drag()

    @property
    def is_running(self) -> bool:
        """True while a script/workflow run is being reported."""
        return self._running

    def set_idle(self) -> None:
        self._running = False
        self._label = "Idle"
        self._count = ""
        self._apply_state()

    def set_running(self, label: str = "Running") -> None:
        transitioning = not self._running
        self._running = True
        self._label = label.strip() or "Running"
        if transitioning:
            # restart the blink / shine / dots animation
            self._blink_on = True
            self._phase = 0
        self._apply_state()

    def set_count(self, count: str = "") -> None:
        """Remaining patients of the active node ('' clears it).

        Shown on its OWN line under the status text, so "Workflow running..."
        keeps its meaning while the operator also sees how many patients are
        left (operator request 2026-10-09). Display-only: it never decides
        which patient is processed.
        """
        text = str(count or "").strip()[:MAX_STATUS_CHARS]
        if text == self._count:
            return
        self._count = text
        self._apply_state()

    @property
    def count(self) -> str:
        """The current remaining-patient text ('' when there is none)."""
        return self._count

    def refresh_visibility(self) -> None:
        """Keep the overlay visible while the owner is, at its parked spot."""
        if not self._alive():
            return
        if not self._owner_visible():
            self.window.withdraw()
            return
        self._position()
        self.window.deiconify()
        self.window.lift()

    def tick(self) -> None:
        """Advance the running animation (called by the GUI poll loop).

        While running: the green lamp BLINKS, the status text
        SHINES, and the "on process" dots cycle 1 -> 2 -> 3.
        While idle: everything stays steady gray.
        """
        if not self._alive():
            return
        if self._running:
            self._blink_on = not self._blink_on
            self._phase = (self._phase + 1) % 3
        self._apply_state()

    def destroy(self) -> None:
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    # -- internals -----------------------------------------------------------

    def _create_eye_glow(self) -> None:
        """Draw the glowing eyes over the icon (measured positions)."""
        scale = self.ROBOT / ICON_SIZE
        r_eye = EYE_RADIUS_450 * scale
        r_halo = r_eye * EYE_HALO_FACTOR
        for cx450, cy450 in EYE_CENTERS_450:
            cx, cy = cx450 * scale, cy450 * scale
            halo = self.robot_canvas.create_oval(
                cx - r_halo, cy - r_halo,
                cx + r_halo, cy + r_halo,
                fill=COL_EYE_HALO_RUN, outline="",
                state="hidden", tags="eye",
            )
            core = self.robot_canvas.create_oval(
                cx - r_eye, cy - r_eye,
                cx + r_eye, cy + r_eye,
                fill=COL_EYE_RUN, outline="",
                state="hidden", tags="eye",
            )
            self._eye_ids.append((halo, core))

    def _load_robot_icon(self) -> bool:
        """Load and resize the robot icon onto the robot canvas."""
        try:
            from PIL import Image, ImageTk

            image = Image.open(ICON_PATH).convert("RGBA")
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            image = image.resize((self.ROBOT, self.ROBOT), resampling)
            self._icon_photo = ImageTk.PhotoImage(image)
            self.robot_canvas.create_image(
                self.ROBOT // 2, self.ROBOT // 2,
                image=self._icon_photo, anchor="center", tags="icon",
            )
            return True
        except Exception:
            # A missing/broken icon asset must never crash the GUI.
            self._draw_placeholder()
            return False

    def _draw_placeholder(self) -> None:
        """Minimal mascot fallback when the icon file cannot be loaded."""
        c = self.robot_canvas
        c.create_oval(
            6, 6, self.ROBOT - 6, self.ROBOT - 6,
            fill=COL_BODY, outline=COL_ACCENT,
        )
        c.create_text(
            self.ROBOT // 2, self.ROBOT // 2,
            text="EDH", fill=COL_TITLE, font=("Segoe UI", 8, "bold"),
        )

    def _bind_drag(self) -> None:
        """Make the whole overlay draggable; a plain click focuses the main GUI."""
        widgets = [self.window, *self._descendants(self.window)]
        for widget in widgets:
            widget.bind("<ButtonPress-1>", self._on_press)
            widget.bind("<B1-Motion>", self._on_motion)
            widget.bind("<ButtonRelease-1>", self._on_release)
        self.window.bind("<Double-Button-1>", lambda _event: self.owner.lift())

    @staticmethod
    def _descendants(widget: tk.Widget):
        for child in widget.winfo_children():
            yield child
            yield from RunStatusOverlay._descendants(child)

    def _on_press(self, event: tk.Event) -> None:
        self._press_x = event.x_root
        self._press_y = event.y_root
        self._win_x, self._win_y = self._current_xy()
        self._dragging = False

    def _on_motion(self, event: tk.Event) -> None:
        if self._press_x is None:
            return
        dx = event.x_root - self._press_x
        dy = event.y_root - self._press_y
        if not self._dragging and abs(dx) + abs(dy) > self.DRAG_THRESHOLD:
            self._dragging = True
        if self._dragging:
            self._user_moved = True
            self.window.geometry(f"+{self._win_x + dx}+{self._win_y + dy}")

    def _on_release(self, event: tk.Event) -> None:
        dragged = self._dragging
        self._press_x = None
        self._press_y = None
        self._dragging = False
        if not dragged:
            # Plain click: bring the main GUI forward. NEVER focus_force —
            # the overlay must not steal focus while HBSys automation runs.
            self.owner.deiconify()

    def _current_xy(self) -> tuple[int, int]:
        """Current window position from the geometry string (works mapped or withdrawn)."""
        try:
            _, pos = self.window.geometry().split("+", 1)
            x, y = pos.split("+")
            return int(x), int(y)
        except (ValueError, AttributeError):
            return self.window.winfo_x(), self.window.winfo_y()

    def _apply_state(self) -> None:
        if not self._alive():
            return
        if self._running:
            dots = "." * (self._phase + 1)     # "on process" dots
            self.status_label.configure(
                text=(self._label + dots)[:MAX_STATUS_CHARS],
                fg=COL_TEXT_RUN if self._blink_on else COL_TEXT_SHINE,
            )
            self.lamp_canvas.itemconfigure(
                self._lamp_id,
                fill=COL_LAMP_RUN_A if self._blink_on else COL_LAMP_RUN_B,
            )
        else:
            self.status_label.configure(
                text=self._label[:MAX_STATUS_CHARS],
                fg=COL_TEXT_IDLE,
            )
            self.lamp_canvas.itemconfigure(self._lamp_id, fill=COL_LAMP_IDLE)
        eye_state = "normal" if self._running else "hidden"
        for halo_id, core_id in self._eye_ids:
            self.robot_canvas.itemconfigure(halo_id, state=eye_state)
            self.robot_canvas.itemconfigure(core_id, state=eye_state)
        self.count_label.configure(
            text=self._count,
            fg=COL_TEXT_COUNT,
        )
        self.refresh_visibility()

    def _alive(self) -> bool:
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def _position(self) -> None:
        if self._user_moved:
            return  # the operator parked it somewhere — keep it there
        x, y = self._target_xy()
        self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

    def _target_xy(self) -> tuple[int, int]:
        screen_h = self.owner.winfo_screenheight()
        y = max(0, screen_h - self.HEIGHT - self.TASKBAR_GAP)
        return self.MARGIN_X, y

    def _owner_visible(self) -> bool:
        try:
            return self.owner.state() != "withdrawn" and self.owner.winfo_exists()
        except tk.TclError:
            return False


if __name__ == "__main__":
    # Standalone demo: robot wakes up, sleeps, wakes up with a workflow label.
    # Drag the overlay anywhere — it stays where you drop it.
    root = tk.Tk()
    root.title("RunStatusOverlay demo (owner window)")
    root.geometry("420x180+520+420")
    _overlay = RunStatusOverlay(root)

    def _demo(step: int = 0) -> None:
        if step == 0:
            _overlay.set_running("Script running")
        elif step == 10:
            _overlay.set_idle()
        elif step == 16:
            _overlay.set_running("Workflow running")
        elif step >= 26:
            root.destroy()
            return
        _overlay.tick()
        _overlay.refresh_visibility()
        root.after(500, _demo, step + 1)

    root.after(300, _demo, 0)
    root.mainloop()
