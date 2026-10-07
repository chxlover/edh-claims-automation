"""Small always-on-top run-status indicator for the Claims GUI.

A mini robot mascot docked at the lower-left screen corner (near the Windows
Start button) so the operator can see at a glance whether the Claims Bot is
working:

- Idle    -> robot asleep (closed eyes), gray antenna lamp + chest LED.
- Running -> robot awake (blinking), pulsing green lamp + LED + status text.

Display-only by design: it never takes focus (safe while HBSys mouse/keyboard
automation is live), and it hides together with the main window.

Standalone demo / visual check:

    python gui/run_status_overlay.py
"""

from __future__ import annotations

import tkinter as tk

# -- palette (dark "automation" style) ---------------------------------------
COL_BG = "#0F172A"          # panel background
COL_BORDER = "#334155"      # panel border
COL_ACCENT = "#38BDF8"      # cyan robot outline / eyes (automation look)
COL_BODY = "#1E293B"        # robot head / body fill
COL_TITLE = "#E5E7EB"       # title text
COL_TEXT_RUN = "#BBF7D0"    # status text while running
COL_TEXT_IDLE = "#CBD5E1"   # status text while idle
COL_LAMP_IDLE = "#64748B"   # gray antenna lamp while idle
COL_LAMP_RUN_A = "#22C55E"  # green lamp pulse (on)
COL_LAMP_RUN_B = "#86EFAC"  # green lamp pulse (off)
COL_LED_OFF = "#334155"     # chest LED off (idle)
COL_MOUTH = "#64748B"       # mouth grille

BLINK_EVERY_TICKS = 8       # blink once every 8 poll ticks (~6 s at 750 ms)
MAX_STATUS_CHARS = 24


class RunStatusOverlay:
    """Display-only mini robot status window near the taskbar start corner."""

    WIDTH = 288
    HEIGHT = 52
    MARGIN_X = 10
    TASKBAR_GAP = 54         # sits just above the taskbar / Start corner
    ROBOT = 44               # robot canvas size (px)

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
        self._pulse_on = False
        self._blink_count = 0
        self._label = "Idle"

        self.frame = tk.Frame(
            self.window,
            bg=COL_BG,
            highlightbackground=COL_BORDER,
            highlightthickness=1,
            bd=0,
        )
        self.frame.pack(fill="both", expand=True)

        # -- robot mascot (pure Tk canvas, no image assets) -------------------
        self.robot_canvas = tk.Canvas(
            self.frame,
            width=self.ROBOT,
            height=self.ROBOT,
            bg=COL_BG,
            highlightthickness=0,
            bd=0,
        )
        self.robot_canvas.grid(row=0, column=0, rowspan=2, padx=(10, 8), pady=4)
        self._draw_robot()

        tk.Label(
            self.frame,
            text="EDH Claim Automation System",
            bg=COL_BG,
            fg=COL_TITLE,
            font=("Segoe UI", 9, "bold"),
        ).grid(row=0, column=1, sticky="w", padx=(0, 8), pady=(8, 0))
        self.status_label = tk.Label(
            self.frame,
            text="Idle",
            bg=COL_BG,
            fg=COL_TEXT_IDLE,
            font=("Segoe UI", 8),
        )
        self.status_label.grid(row=1, column=1, sticky="w", padx=(0, 8), pady=(0, 8))

        self._apply_state()
        self._position()
        # Click focuses the main window; NEVER focus_force here — the overlay
        # must not steal focus while HBSys automation is driving the mouse.
        self.window.bind("<Button-1>", lambda _event: self.owner.deiconify())
        self.window.bind("<Double-Button-1>", lambda _event: self.owner.lift())

    @property
    def is_running(self) -> bool:
        """True while a script/workflow run is being reported."""
        return self._running

    def set_idle(self) -> None:
        self._running = False
        self._label = "Idle"
        self._apply_state()

    def set_running(self, label: str = "Running") -> None:
        self._running = True
        self._label = label.strip() or "Running"
        self._apply_state()

    def refresh_visibility(self) -> None:
        """Keep the overlay near the taskbar and hide it with the main window."""
        if not self._alive():
            return
        if not self._owner_visible():
            self.window.withdraw()
            return
        self._position()
        self.window.deiconify()
        self.window.lift()

    def tick(self) -> None:
        """Pulse the lamp and blink the eyes (called by the GUI poll loop)."""
        if not self._alive():
            return
        if self._running:
            self._pulse_on = not self._pulse_on
            color = COL_LAMP_RUN_A if self._pulse_on else COL_LAMP_RUN_B
            self.robot_canvas.itemconfigure(self._lamp_id, fill=color)
            self.robot_canvas.itemconfigure(self._led_id, fill=color)
            self._blink_count += 1
            eyes_closed = self._blink_count % BLINK_EVERY_TICKS == 0
            self._set_eyes_open(not eyes_closed)
        else:
            self._set_eyes_open(False)

    def destroy(self) -> None:
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    # -- internals -----------------------------------------------------------

    def _draw_robot(self) -> None:
        """Draw the mini robot mascot (pure Tk canvas primitives)."""
        c = self.robot_canvas
        # antenna with status lamp
        self._lamp_id = c.create_oval(18, 0, 26, 8, fill=COL_LAMP_IDLE, outline="")
        c.create_line(22, 8, 22, 12, fill=COL_ACCENT, width=2)
        # ear bolts
        c.create_rectangle(5, 16, 8, 23, fill=COL_BODY, outline=COL_ACCENT)
        c.create_rectangle(36, 16, 39, 23, fill=COL_BODY, outline=COL_ACCENT)
        # head
        self._round_rect(c, 8, 12, 36, 28, 5, fill=COL_BODY, outline=COL_ACCENT, width=1)
        # eyes: open (awake) vs closed lines (asleep)
        self._eye_l_open = c.create_oval(13, 16, 19, 22, fill=COL_ACCENT, outline="")
        self._eye_r_open = c.create_oval(25, 16, 31, 22, fill=COL_ACCENT, outline="")
        self._eye_l_closed = c.create_line(13, 19, 19, 19, fill=COL_ACCENT, width=2)
        self._eye_r_closed = c.create_line(25, 19, 31, 19, fill=COL_ACCENT, width=2)
        # mouth grille
        for x in (19, 22, 25):
            c.create_line(x, 24, x, 26, fill=COL_MOUTH, width=1)
        # body with two chest LEDs
        self._round_rect(c, 10, 30, 34, 43, 4, fill=COL_BODY, outline=COL_ACCENT, width=1)
        self._led_id = c.create_oval(16, 34, 22, 40, fill=COL_LAMP_IDLE, outline="")
        self._led2_id = c.create_oval(24, 34, 30, 40, fill=COL_LED_OFF, outline="")

    @staticmethod
    def _round_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int,
                    radius: int, **kwargs) -> int:
        """Rounded rectangle as a smooth Tk polygon (no image assets)."""
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]
        return canvas.create_polygon(points, smooth=True, **kwargs)

    def _apply_state(self) -> None:
        if not self._alive():
            return
        self.status_label.configure(
            text=self._label[:MAX_STATUS_CHARS],
            fg=COL_TEXT_RUN if self._running else COL_TEXT_IDLE,
        )
        if self._running:
            self.robot_canvas.itemconfigure(self._lamp_id, fill=COL_LAMP_RUN_A)
            self.robot_canvas.itemconfigure(self._led_id, fill=COL_LAMP_RUN_A)
            self.robot_canvas.itemconfigure(self._led2_id, fill=COL_ACCENT)
            self._set_eyes_open(True)
        else:
            self.robot_canvas.itemconfigure(self._lamp_id, fill=COL_LAMP_IDLE)
            self.robot_canvas.itemconfigure(self._led_id, fill=COL_LAMP_IDLE)
            self.robot_canvas.itemconfigure(self._led2_id, fill=COL_LED_OFF)
            self._set_eyes_open(False)
        self.refresh_visibility()

    def _set_eyes_open(self, open_: bool) -> None:
        c = self.robot_canvas
        c.itemconfigure(self._eye_l_open, state="normal" if open_ else "hidden")
        c.itemconfigure(self._eye_r_open, state="normal" if open_ else "hidden")
        c.itemconfigure(self._eye_l_closed, state="hidden" if open_ else "normal")
        c.itemconfigure(self._eye_r_closed, state="hidden" if open_ else "normal")

    def _alive(self) -> bool:
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def _position(self) -> None:
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
