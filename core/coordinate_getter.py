"""Interactive Coordinate Getter Tool.

Capture screen coordinates by clicking on elements.
Useful for calibrating UI automation scripts.

Usage:
    # Run standalone
    python -m core.coordinate_getter

    # Run and save to calibration file
    python -m core.coordinate_getter --save-calibration
"""

from __future__ import annotations

import argparse
import json
import sys
import tkinter as tk
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import pyautogui
from PIL import Image, ImageTk


LOG_DIR = Path("logs")
COORDS_FILE = LOG_DIR / "captured_coordinates.json"


@dataclass
class CapturedPoint:
    """A captured coordinate with a label."""
    label: str
    x: int
    y: int
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


class CoordinateGetter:
    """Interactive GUI for capturing screen coordinates."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Coordinate Getter - Click to Capture")
        self.root.geometry("1200x800")
        self.root.configure(bg="#1a1a2e")

        self.captured_points: list[CapturedPoint] = []
        self.current_label = tk.StringVar(value="point_1")
        self.screenshot_photo: Optional[ImageTk.PhotoImage] = None
        self.scale_factor = 1.0
        self.offset_x = 0
        self.offset_y = 0

        self.build_ui()
        self.take_screenshot()

    def build_ui(self):
        # Top controls
        controls = tk.Frame(self.root, bg="#16213e", pady=8)
        controls.pack(fill="x")

        tk.Label(
            controls,
            text="COORDINATE GETTER",
            bg="#16213e",
            fg="#e94560",
            font=("Segoe UI", 14, "bold"),
        ).pack(side="left", padx=10)

        tk.Label(
            controls,
            text="Click on the screenshot to capture coordinates",
            bg="#16213e",
            fg="#a0a0a0",
            font=("Segoe UI", 10),
        ).pack(side="left", padx=10)

        # Label input
        tk.Label(controls, text="Label:", bg="#16213e", fg="white").pack(
            side="left", padx=(20, 5)
        )
        tk.Entry(
            controls,
            textvariable=self.current_label,
            width=20,
            font=("Segoe UI", 10),
        ).pack(side="left")

        # Buttons
        tk.Button(
            controls,
            text="New Screenshot",
            command=self.take_screenshot,
            bg="#0f3460",
            fg="white",
            font=("Segoe UI", 9),
            padx=10,
        ).pack(side="left", padx=10)

        tk.Button(
            controls,
            text="Clear All",
            command=self.clear_points,
            bg="#e94560",
            fg="white",
            font=("Segoe UI", 9),
            padx=10,
        ).pack(side="left", padx=5)

        tk.Button(
            controls,
            text="Save Coordinates",
            command=self.save_coordinates,
            bg="#16a085",
            fg="white",
            font=("Segoe UI", 9),
            padx=10,
        ).pack(side="left", padx=5)

        tk.Button(
            controls,
            text="Copy Last",
            command=self.copy_last_point,
            bg="#f39c12",
            fg="white",
            font=("Segoe UI", 9),
            padx=10,
        ).pack(side="left", padx=5)

        # Main area
        main_frame = tk.Frame(self.root, bg="#1a1a2e")
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Screenshot canvas
        canvas_frame = tk.Frame(main_frame, bg="#333")
        canvas_frame.pack(side="left", fill="both", expand=True)

        self.canvas = tk.Canvas(canvas_frame, bg="#222", cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self.on_canvas_click)

        # Points list
        list_frame = tk.Frame(main_frame, bg="#16213e", width=300)
        list_frame.pack(side="right", fill="y", padx=(10, 0))
        list_frame.pack_propagate(False)

        tk.Label(
            list_frame,
            text="Captured Points",
            bg="#16213e",
            fg="white",
            font=("Segoe UI", 11, "bold"),
        ).pack(pady=5)

        # Scrollable list
        list_canvas = tk.Canvas(list_frame, bg="#16213e", highlightthickness=0)
        list_scroll = tk.Scrollbar(list_frame, orient="vertical", command=list_canvas.yview)
        self.points_frame = tk.Frame(list_canvas, bg="#16213e")

        self.points_frame.bind(
            "<Configure>",
            lambda e: list_canvas.configure(scrollregion=list_canvas.bbox("all")),
        )

        list_canvas.create_window((0, 0), window=self.points_frame, anchor="nw")
        list_canvas.configure(yscrollcommand=list_scroll.set)

        list_canvas.pack(side="left", fill="both", expand=True)
        list_scroll.pack(side="right", fill="y")

        # Status bar
        self.status_var = tk.StringVar(value="Ready - Click on screenshot to capture coordinates")
        status_bar = tk.Label(
            self.root,
            textvariable=self.status_var,
            bg="#0f3460",
            fg="white",
            font=("Segoe UI", 9),
            anchor="w",
            padx=10,
        )
        status_bar.pack(fill="x", side="bottom")

    def take_screenshot(self):
        """Capture the screen and display it."""
        self.status_var.set("Taking screenshot...")
        self.root.update()

        # Hide this window temporarily
        self.root.withdraw()
        self.root.after(200, self._capture_and_show)

    def _capture_and_show(self):
        """Capture screen and show in window."""
        try:
            screenshot = pyautogui.screenshot()
            self.screenshot_image = screenshot

            # Calculate scale to fit in canvas
            self.root.update_idletasks()
            canvas_w = max(400, self.canvas.winfo_width())
            canvas_h = max(300, self.canvas.winfo_height())

            scale_w = canvas_w / screenshot.width
            scale_h = canvas_h / screenshot.height
            self.scale_factor = min(scale_w, scale_h, 1.0)

            new_w = int(screenshot.width * self.scale_factor)
            new_h = int(screenshot.height * self.scale_factor)

            resized = screenshot.resize((new_w, new_h), Image.LANCZOS)
            self.screenshot_photo = ImageTk.PhotoImage(resized)

            # Center the image
            self.offset_x = max(0, (canvas_w - new_w) // 2)
            self.offset_y = max(0, (canvas_h - new_h) // 2)

            self.canvas.delete("all")
            self.canvas.create_image(
                self.offset_x,
                self.offset_y,
                image=self.screenshot_photo,
                anchor="nw",
            )

            # Redraw existing points
            self.redraw_points()

            self.root.deiconify()
            self.status_var.set(
                f"Screenshot captured ({screenshot.width}x{screenshot.height}) - "
                f"Click to capture coordinates"
            )

        except Exception as exc:
            self.root.deiconify()
            self.status_var.set(f"Error: {exc}")

    def on_canvas_click(self, event):
        """Handle click on canvas to capture coordinates."""
        # Convert canvas coordinates to screen coordinates
        canvas_x = event.x - self.offset_x
        canvas_y = event.y - self.offset_y

        # Convert to original screenshot coordinates
        orig_x = int(canvas_x / self.scale_factor)
        orig_y = int(canvas_y / self.scale_factor)

        # Validate bounds
        if not self.screenshot_image:
            return
        if orig_x < 0 or orig_x >= self.screenshot_image.width:
            return
        if orig_y < 0 or orig_y >= self.screenshot_image.height:
            return

        # Get label
        label = self.current_label.get().strip()
        if not label:
            label = f"point_{len(self.captured_points) + 1}"

        # Capture point
        point = CapturedPoint(label=label, x=orig_x, y=orig_y)
        self.captured_points.append(point)

        # Update label for next point
        self.current_label.set(f"point_{len(self.captured_points) + 1}")

        # Draw marker on canvas
        screen_x = event.x
        screen_y = event.y
        self.canvas.create_oval(
            screen_x - 6, screen_y - 6, screen_x + 6, screen_y + 6,
            outline="#e94560", width=2,
        )
        self.canvas.create_text(
            screen_x + 10, screen_y - 10,
            text=label,
            fill="#e94560",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )

        # Add to points list
        self.add_point_to_list(point)

        # Update status
        self.status_var.set(
            f"Captured: {label} = ({orig_x}, {orig_y}) | "
            f"Total: {len(self.captured_points)} points"
        )

    def add_point_to_list(self, point: CapturedPoint):
        """Add a point to the scrollable points list."""
        row = tk.Frame(self.points_frame, bg="#16213e")
        row.pack(fill="x", padx=5, pady=2)

        # Number
        num = len(self.captured_points)
        tk.Label(
            row,
            text=f"{num}.",
            bg="#16213e",
            fg="#a0a0a0",
            font=("Segoe UI", 9),
            width=3,
        ).pack(side="left")

        # Label
        tk.Label(
            row,
            text=point.label,
            bg="#16213e",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            width=12,
            anchor="w",
        ).pack(side="left")

        # Coordinates
        tk.Label(
            row,
            text=f"({point.x}, {point.y})",
            bg="#16213e",
            fg="#16a085",
            font=("Cascadia Mono", 9),
        ).pack(side="left")

        # Delete button
        tk.Button(
            row,
            text="X",
            bg="#e94560",
            fg="white",
            font=("Segoe UI", 8),
            width=2,
            command=lambda: self.delete_point(num - 1, row),
        ).pack(side="right", padx=2)

    def delete_point(self, index: int, row_widget: tk.Frame):
        """Delete a captured point."""
        if 0 <= index < len(self.captured_points):
            del self.captured_points[index]
            row_widget.destroy()
            self.redraw_points_list()
            self.redraw_points()

    def clear_points(self):
        """Clear all captured points."""
        self.captured_points.clear()
        self.redraw_points_list()
        self.redraw_points()
        self.status_var.set("All points cleared")

    def redraw_points_list(self):
        """Redraw the points list."""
        for widget in self.points_frame.winfo_children():
            widget.destroy()

        for idx, point in enumerate(self.captured_points):
            self.add_point_to_list(point)

    def redraw_points(self):
        """Redraw all point markers on the canvas."""
        # Keep the screenshot, remove old markers
        self.canvas.delete("marker")

        for point in self.captured_points:
            # Convert original coords to canvas coords
            canvas_x = point.x * self.scale_factor + self.offset_x
            canvas_y = point.y * self.scale_factor + self.offset_y

            self.canvas.create_oval(
                canvas_x - 6, canvas_y - 6, canvas_x + 6, canvas_y + 6,
                outline="#e94560", width=2, tags="marker",
            )
            self.canvas.create_text(
                canvas_x + 10, canvas_y - 10,
                text=point.label,
                fill="#e94560",
                font=("Segoe UI", 9, "bold"),
                anchor="w",
                tags="marker",
            )

    def copy_last_point(self):
        """Copy the last captured point to clipboard."""
        if not self.captured_points:
            self.status_var.set("No points to copy")
            return

        point = self.captured_points[-1]
        coord_str = f"Point({point.x}, {point.y})"
        self.root.clipboard_clear()
        self.root.clipboard_append(coord_str)
        self.status_var.set(f"Copied: {coord_str}")

    def save_coordinates(self):
        """Save all captured coordinates to file."""
        if not self.captured_points:
            self.status_var.set("No points to save")
            return

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "captured_at": datetime.now().isoformat(),
            "screen_width": pyautogui.size()[0],
            "screen_height": pyautogui.size()[1],
            "points": [asdict(p) for p in self.captured_points],
        }

        path = COORDS_FILE
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.status_var.set(f"Saved {len(self.captured_points)} points to {path}")

    def run(self):
        """Start the coordinate getter."""
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Interactive Coordinate Getter - click to capture screen coordinates"
    )
    parser.add_argument(
        "--save-calibration",
        action="store_true",
        help="Save coordinates to calibration file format",
    )
    args = parser.parse_args()

    getter = CoordinateGetter()
    getter.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
