from __future__ import annotations

from dataclasses import dataclass

from hbsys_window import find_hbsys_window


ZERO_DATE_MARKERS = ("00-00-0000", "00/00/0000")


@dataclass(frozen=True)
class ZeroDateField:
    index: int
    class_name: str
    text: str
    rectangle: str


def find_zero_dates() -> list[ZeroDateField]:
    window = find_hbsys_window()
    if window is None:
        raise RuntimeError("No HBSys/HOMIS window found. Open HBSys and try again.")
    try:
        window.restore()
        window.set_focus()
    except Exception:
        pass

    results: list[ZeroDateField] = []
    for index, child in enumerate(window.descendants(), start=1):
        try:
            text = child.window_text()
        except Exception:
            continue

        if not any(marker in text for marker in ZERO_DATE_MARKERS):
            continue

        try:
            class_name = child.class_name()
        except Exception:
            class_name = ""

        try:
            rectangle = str(child.rectangle())
        except Exception:
            rectangle = ""

        results.append(
            ZeroDateField(
                index=index,
                class_name=class_name,
                text=text,
                rectangle=rectangle,
            )
        )

    return results


def main() -> int:
    fields = find_zero_dates()
    if not fields:
        print("No visible/exposed zero-date fields found.")
        return 0

    print(f"Found {len(fields)} exposed zero-date field(s):")
    for field in fields:
        print(
            f"{field.index:04d} | class={field.class_name!r} | "
            f"text={field.text!r} | rect={field.rectangle}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

