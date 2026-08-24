from __future__ import annotations

from pathlib import Path

from updater import get_network_source


NAS_APP_FOLDERS = [
    "",
]

def ensure_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    print(f"[NAS] OK: {path}")


def main() -> int:
    print("=" * 70)
    print("EDH Claims Automation System - NAS Folder Setup")
    print("=" * 70)

    app_root = Path(get_network_source())

    print(f"NAS app update source : {app_root}")
    print("=" * 70)

    try:
        for rel in NAS_APP_FOLDERS:
            ensure_folder(app_root / rel)
    except Exception as exc:
        print(f"\nERROR: Could not create NAS folders: {exc}")
        print("\nCheck network access/permissions to the NAS shared folder.")
        input("Press Enter to close...")
        return 1

    print("=" * 70)
    print("NAS update folder is ready.")
    input("\nPress Enter to close...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
