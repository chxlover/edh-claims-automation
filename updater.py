# -*- coding: utf-8 -*-
"""
EDH Claims updater and requirements helper.

Update detection is manifest-based and code-only. Patient/user data folders are
never used as update signals, so new patient folders will not trigger a false
update notification.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


LOCAL_ROOT = Path(__file__).resolve().parent
LOCAL_VERSION_FILE = LOCAL_ROOT / "version.txt"
LOCAL_CONFIG_FILE = LOCAL_ROOT / "update_config.json"
LOCAL_REQUIREMENTS_FILE = LOCAL_ROOT / "requirements.txt"

DEFAULT_NETWORK_SOURCE = (
    r"\\192.168.1.193\Echague District Hospital Files\melvin\AIBOT(BACKUP)\claims_bot"
)

PYTHON_REQUIREMENTS = [
    "PyMuPDF",
    "numpy",
    "opencv-python",
    "pytesseract",
    "pdf2image",
    "Pillow",
    "PyPDF2",
    "reportlab",
    "rapidfuzz",
    "pymysql",
    "mysql-connector-python",
    "pywinauto",
    "PyAutoGUI",
    "python-telegram-bot",
    "openpyxl",
]

EXTERNAL_REQUIREMENTS = [
    {
        "name": "Microsoft Visual C++ Runtime",
        "winget_id": "Microsoft.VCRedist.2015+.x64",
        "checker": "vcredist",
        "manual_url": "https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist",
    },
    {
        "name": "Ghostscript",
        "winget_id": "ArtifexSoftware.GhostScript",
        "checker": "ghostscript",
        "manual_url": "https://ghostscript.com/releases/gsdnld.html",
    },
    {
        "name": "Tesseract OCR",
        "winget_id": "UB-Mannheim.TesseractOCR",
        "checker": "tesseract",
        "manual_url": "https://github.com/UB-Mannheim/tesseract/wiki",
    },
    {
        "name": "Poppler",
        "winget_id": "oschwartz10612.Poppler",
        "checker": "poppler",
        "manual_url": "https://github.com/oschwartz10612/poppler-windows/releases",
    },
]


def get_network_source() -> str:
    env_value = os.getenv("CLAIMS_UPDATE_SOURCE", "").strip()
    if env_value:
        return env_value
    try:
        if LOCAL_CONFIG_FILE.exists():
            data = json.loads(LOCAL_CONFIG_FILE.read_text(encoding="utf-8"))
            value = str(data.get("network_source", "")).strip()
            if value:
                return value
    except Exception:
        pass
    return DEFAULT_NETWORK_SOURCE


def _network_root() -> Path:
    return Path(get_network_source())


def _read_version(path: Path) -> float:
    try:
        return float(path.read_text(encoding="utf-8").strip())
    except Exception:
        return 0.0


def _write_version(path: Path, version: float) -> None:
    path.write_text(str(version), encoding="utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_network_manifest() -> dict | None:
    manifest_file = _network_root() / "manifest.json"
    try:
        if not manifest_file.exists():
            return None
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("files"), list):
            return data
    except Exception:
        pass
    return None


def _changed_files_from_manifest(manifest: dict) -> list[str]:
    changed: list[str] = []
    for item in manifest.get("files", []):
        rel_path = str(item.get("path", "")).strip()
        if not rel_path:
            continue
        local_path = LOCAL_ROOT / rel_path
        if not local_path.exists():
            changed.append(rel_path)
            continue
        try:
            expected_size = int(item.get("size", -1))
            if local_path.stat().st_size != expected_size:
                changed.append(rel_path)
                continue
            if _file_sha256(local_path) != item.get("sha256"):
                changed.append(rel_path)
        except Exception:
            changed.append(rel_path)
    return changed


def get_local_version() -> float:
    return _read_version(LOCAL_VERSION_FILE)


def get_network_version() -> float:
    try:
        nas = _network_root()
        if not nas.exists():
            return -1.0
        manifest = _load_network_manifest()
        if manifest:
            return float(manifest.get("version", 0.0))
        return _read_version(nas / "version.txt")
    except Exception:
        return -1.0


def check_update_available() -> dict:
    local_ver = get_local_version()
    network_ver = get_network_version()
    nas_ok = network_ver >= 0
    changed_files: list[str] = []

    if nas_ok:
        manifest = _load_network_manifest()
        if manifest:
            changed_files = _changed_files_from_manifest(manifest)

    return {
        "available": nas_ok and (network_ver > local_ver or bool(changed_files)),
        "local_version": local_ver,
        "network_version": network_ver if nas_ok else 0.0,
        "nas_reachable": nas_ok,
        "changed_files": changed_files,
        "network_source": get_network_source(),
    }


def apply_update() -> tuple[bool, str]:
    try:
        nas = _network_root()
        if not nas.exists():
            return False, "Cannot access NAS update source. Check network connection."

        manifest = _load_network_manifest()
        if manifest:
            network_ver = float(manifest.get("version", get_network_version()))
            rel_paths = _changed_files_from_manifest(manifest)
            if not rel_paths:
                _write_version(LOCAL_VERSION_FILE, network_ver)
                return True, f"Already up to date at v{network_ver}."
        else:
            return False, "NAS manifest.json was not found. Publish an update first."

        errors: list[str] = []
        copied = 0
        for rel_path in rel_paths:
            src = nas / rel_path
            dest = LOCAL_ROOT / rel_path
            if not src.exists():
                errors.append(f"{rel_path}: missing on NAS")
                continue
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                copied += 1
            except Exception as exc:
                errors.append(f"{rel_path}: {exc}")

        _write_version(LOCAL_VERSION_FILE, network_ver)

        if errors:
            return True, (
                f"Updated to v{network_ver}; copied {copied} file(s), "
                f"with {len(errors)} file error(s). Restart the app."
            )
        return True, (
            f"Successfully updated to v{network_ver}; copied {copied} changed file(s). "
            "Restart the app to apply changes."
        )
    except Exception as exc:
        return False, f"Update failed: {exc}"


def restart_app() -> None:
    subprocess.Popen([sys.executable] + sys.argv, cwd=LOCAL_ROOT)
    sys.exit(0)


def _parse_requirement_name(line: str) -> str | None:
    value = line.strip()
    if not value or value.startswith("#"):
        return None
    for marker in ("==", ">=", "<=", "~=", ">", "<", "["):
        if marker in value:
            value = value.split(marker, 1)[0]
            break
    return value.strip()


def _load_requirements() -> list[str]:
    if not LOCAL_REQUIREMENTS_FILE.exists():
        return PYTHON_REQUIREMENTS
    requirements: list[str] = []
    for line in LOCAL_REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and not value.startswith("#") and _parse_requirement_name(value):
            requirements.append(value)
    return requirements or PYTHON_REQUIREMENTS


def _is_python_requirement_satisfied(requirement: str) -> bool:
    package_name = _parse_requirement_name(requirement)
    if not package_name:
        return True
    try:
        installed_version = importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return False
    if "==" in requirement:
        expected = requirement.split("==", 1)[1].strip()
        return installed_version == expected
    return True


def _install_python_package(requirement: str) -> bool:
    command = [sys.executable, "-m", "pip", "install", requirement]
    return subprocess.call(command, cwd=LOCAL_ROOT) == 0


def _find_ghostscript() -> str | None:
    for executable in ("gswin64c.exe", "gswin32c.exe", "gs"):
        found = shutil.which(executable)
        if found:
            return found
    standard = Path(r"C:\Program Files\gs")
    if standard.exists():
        matches = sorted(standard.glob(r"gs*\bin\gswin64c.exe"), reverse=True)
        if matches:
            return str(matches[0])
    return None


def _find_tesseract() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    candidates = [
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _find_poppler() -> str | None:
    for executable in ("pdftoppm.exe", "pdftoppm"):
        found = shutil.which(executable)
        if found:
            return found
    winget_root = (
        Path(os.getenv("LOCALAPPDATA", ""))
        / "Microsoft"
        / "WinGet"
        / "Packages"
    )
    if winget_root.exists():
        matches: list[Path] = []
        for package_root in winget_root.glob("oschwartz10612.Poppler_*"):
            matches.extend(package_root.rglob("pdftoppm.exe"))
        matches.sort(reverse=True)
        if matches:
            return str(matches[0])
    return None


def _find_vcredist() -> str | None:
    system_root = Path(os.getenv("SystemRoot", r"C:\Windows"))
    for candidate in (
        system_root / "System32" / "vcruntime140.dll",
        system_root / "SysWOW64" / "vcruntime140.dll",
    ):
        if candidate.exists():
            return str(candidate)
    return None


def _external_tool_available(checker: str) -> bool:
    if checker == "ghostscript":
        return bool(_find_ghostscript())
    if checker == "tesseract":
        return bool(_find_tesseract())
    if checker == "poppler":
        return bool(_find_poppler())
    if checker == "vcredist":
        return bool(_find_vcredist())
    return False


def _install_winget_package(package_id: str) -> bool:
    winget = shutil.which("winget")
    if not winget:
        return False
    command = [
        winget,
        "install",
        "--id",
        package_id,
        "--exact",
        "--silent",
        "--accept-package-agreements",
        "--accept-source-agreements",
    ]
    return subprocess.call(command) == 0


def ensure_external_requirements(
    status_callback=None,
    *,
    install_external: bool = False,
) -> tuple[list[str], list[str]]:
    """Check/install non-Python runtime dependencies."""
    installed: list[str] = []
    missing: list[str] = []

    def report(message: str) -> None:
        print(message)
        if status_callback:
            status_callback(message)

    for tool in EXTERNAL_REQUIREMENTS:
        if _external_tool_available(tool["checker"]):
            report(f"[External] OK: {tool['name']}")
            continue
        if install_external:
            report(f"[External] Installing with winget: {tool['name']}")
            if _install_winget_package(tool["winget_id"]):
                installed.append(tool["name"])
                report(f"[External] Install command completed: {tool['name']}")
                continue
        missing.append(f"{tool['name']} ({tool['manual_url']})")

    if missing:
        report("[External] Manual install may be required: " + "; ".join(missing))
    return installed, missing


def get_external_requirements_status() -> dict[str, bool]:
    """Return a stable diagnostic mapping for the setup verifier."""
    return {
        str(tool["name"]): _external_tool_available(str(tool["checker"]))
        for tool in EXTERNAL_REQUIREMENTS
    }


def ensure_requirements(status_callback=None, install_external: bool = False) -> list[str]:
    """
    Check Python libraries and install missing packages.

    External tools are checked and reported. They are installed with winget only
    when install_external=True.
    """
    installed: list[str] = []

    def report(message: str) -> None:
        print(message)
        if status_callback:
            status_callback(message)

    for requirement in _load_requirements():
        if _is_python_requirement_satisfied(requirement):
            report(f"[Requirements] OK: {requirement}")
            continue
        report(f"[Requirements] Installing: {requirement}")
        if _install_python_package(requirement):
            installed.append(requirement)
            report(f"[Requirements] Installed: {requirement}")
        else:
            report(f"[Requirements] FAILED: {requirement}")

    external_installed, _missing = ensure_external_requirements(
        status_callback,
        install_external=install_external,
    )
    installed.extend(external_installed)

    return installed


if __name__ == "__main__":
    ensure_requirements(install_external=False)
