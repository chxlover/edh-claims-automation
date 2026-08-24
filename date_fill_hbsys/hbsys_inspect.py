from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from pywinauto import Desktop

from hbsys_rules import sample_recommendation
from hbsys_window import is_hbsys_window


LOG_DIR = Path("logs")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def safe_part(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in value.strip())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned[:48] or "window"


def dump_window(wrapper, backend: str, timestamp: str, restore: bool, sequence: int) -> Path:
    if restore:
        try:
            wrapper.restore()
            wrapper.set_focus()
        except Exception:
            pass

    title = wrapper.window_text()
    path = LOG_DIR / (
        f"hbsys_{backend}_{timestamp}_{sequence:03d}_"
        f"{safe_part(title)}_{wrapper.handle}.txt"
    )
    lines = [
        f"Backend: {backend}",
        f"Window text: {title}",
        f"Class name: {wrapper.class_name()}",
        f"Handle: {wrapper.handle}",
        "",
        "Descendants:",
    ]

    try:
        descendants = wrapper.descendants()
    except Exception as exc:  # noqa: BLE001 - inspection must keep going.
        descendants = []
        lines.append(f"Could not list descendants: {exc!r}")

    for index, child in enumerate(descendants, start=1):
        try:
            rectangle = child.rectangle()
        except Exception:
            rectangle = ""

        try:
            text = child.window_text()
        except Exception:
            text = ""

        try:
            class_name = child.class_name()
        except Exception:
            class_name = ""

        try:
            control_type = child.element_info.control_type
        except Exception:
            control_type = ""

        lines.append(
            f"{index:04d} | class={class_name!r} | type={control_type!r} | "
            f"text={text!r} | rect={rectangle}"
        )

    write_text(path, "\n".join(lines))
    return path


def find_hbsys_windows(backend: str, include_all: bool):
    desktop = Desktop(backend=backend)
    windows = []
    for window in desktop.windows():
        title = ""
        try:
            title = window.window_text()
        except Exception:
            pass

        if include_all:
            windows.append(window)
        elif (
            is_hbsys_window(window)
            or "PHIC CLAIM FORM" in title
            or "Admission History" in title
        ):
            windows.append(window)
    return windows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect visible HBSys windows and export their controls."
    )
    parser.add_argument(
        "--backend",
        choices=["uia", "win32", "both"],
        default="both",
        help="Windows automation backend to use.",
    )
    parser.add_argument(
        "--no-restore",
        action="store_true",
        help="Do not try to restore/focus HBSys windows before inspection.",
    )
    parser.add_argument(
        "--all-windows",
        action="store_true",
        help="Inspect every top-level desktop window, useful for popups/dialogs.",
    )
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backends = ["uia", "win32"] if args.backend == "both" else [args.backend]
    generated: list[Path] = []

    for backend in backends:
        try:
            windows = find_hbsys_windows(backend, include_all=args.all_windows)
        except Exception as exc:  # noqa: BLE001 - report backend failures.
            path = LOG_DIR / f"hbsys_{backend}_{timestamp}_error.txt"
            write_text(path, f"Backend {backend} failed: {exc!r}\n")
            generated.append(path)
            continue

        if not windows:
            path = LOG_DIR / f"hbsys_{backend}_{timestamp}_not_found.txt"
            write_text(path, "No HBSys/HOMIS window found. Open HBSys and try again.\n")
            generated.append(path)
            continue

        for sequence, window in enumerate(windows, start=1):
            generated.append(
                dump_window(
                    window,
                    backend,
                    timestamp,
                    restore=not args.no_restore,
                    sequence=sequence,
                )
            )

    sample = sample_recommendation()
    sample_path = LOG_DIR / f"sample_recommendation_{timestamp}.txt"
    write_text(
        sample_path,
        "\n".join(
            [
                f"Status: {sample.status}",
                f"Date to fill: {sample.date_to_fill}",
                f"Reason: {sample.reason}",
                f"Selected row: {sample.row}",
            ]
        ),
    )
    generated.append(sample_path)

    print("Generated inspection files:")
    for path in generated:
        print(f"- {path.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
