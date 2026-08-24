"""Safe PDF compression service used by the Claims Bot GUI.

The service writes a separate PDF/A read-only copy. It never changes the
source PDF and never overwrites an existing destination file.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.activity_logger import logger


COMPRESSION_PRESETS = {
    "Readable Claims": {
        "color_dpi": 300, "gray_dpi": 300, "mono_dpi": 400,
        "jpegq": 90, "grayscale": True,
    },
    "High Quality": {
        "color_dpi": 240, "gray_dpi": 240, "mono_dpi": 400,
        "jpegq": 90, "grayscale": False,
    },
    "Balanced": {
        "color_dpi": 150, "gray_dpi": 150, "mono_dpi": 300,
        "jpegq": 78, "grayscale": False,
    },
    "Strong": {
        "color_dpi": 110, "gray_dpi": 110, "mono_dpi": 240,
        "jpegq": 62, "grayscale": False,
    },
}

TARGET_DPI_LADDERS = {
    "Readable Claims": (300, 270, 240, 220, 200),
    "High Quality": (240, 220, 200, 180, 160, 140, 120),
    "Balanced": (150, 135, 120, 105, 90, 75, 60, 50),
    "Strong": (110, 96, 84, 72, 60, 50, 42),
}


@dataclass(frozen=True, slots=True)
class CompressionResult:
    status: str
    source: Path
    output: Path | None
    original_bytes: int
    compressed_bytes: int
    message: str
    target_bytes: int | None = None
    attempts: int = 1

    @property
    def saved(self) -> bool:
        return self.status == "SAVED" and self.output is not None

    @property
    def savings_percent(self) -> float:
        if self.original_bytes <= 0:
            return 0.0
        return max(
            0.0,
            (self.original_bytes - self.compressed_bytes) / self.original_bytes * 100.0,
        )


def find_ghostscript() -> str | None:
    """Find Ghostscript without depending on a single installed version."""
    configured = os.getenv("CLAIMS_GHOSTSCRIPT", "").strip()
    candidates = [
        configured,
        r"C:\Program Files\gs\gs10.07.0\bin\gswin64c.exe",
        r"C:\Program Files\gs\gs10.06.0\bin\gswin64c.exe",
        r"C:\Program Files\gs\gs10.05.1\bin\gswin64c.exe",
        r"C:\Program Files\gs\gs10.04.0\bin\gswin64c.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    return shutil.which("gswin64c") or shutil.which("gswin32c") or shutil.which("gs")


def default_compressed_path(source: str | Path) -> Path:
    path = Path(source)
    return path.with_name(f"{path.stem}_COMPRESSED_PDFA.pdf")


def _validate_pdf(path: Path) -> int:
    try:
        import fitz

        with fitz.open(path) as document:
            if document.page_count < 1:
                raise ValueError("Compressed PDF has no pages")
            return int(document.page_count)
    except Exception as exc:
        raise RuntimeError(f"Compressed output is not a readable PDF: {exc}") from exc


def _command(
    ghostscript: str,
    source: Path,
    output: Path,
    settings: dict[str, int],
) -> list[str]:
    grayscale = bool(settings.get("grayscale", False))
    color_strategy = "Gray" if grayscale else "RGB"
    process_color_model = "DeviceGray" if grayscale else "DeviceRGB"
    return [
        ghostscript,
        "-dPDFA=2",
        "-dBATCH",
        "-dNOPAUSE",
        "-dSAFER",
        "-dQUIET",
        "-sDEVICE=pdfwrite",
        "-dPDFACompatibilityPolicy=1",
        f"-sColorConversionStrategy={color_strategy}",
        f"-sProcessColorModel={process_color_model}",
        "-dEmbedAllFonts=true",
        "-dSubsetFonts=true",
        "-dDetectDuplicateImages=true",
        "-dCompressFonts=true",
        "-dAutoRotatePages=/None",
        "-dDownsampleColorImages=true",
        "-dColorImageDownsampleType=/Bicubic",
        f"-dColorImageResolution={settings['color_dpi']}",
        "-dDownsampleGrayImages=true",
        "-dGrayImageDownsampleType=/Bicubic",
        f"-dGrayImageResolution={settings['gray_dpi']}",
        "-dDownsampleMonoImages=true",
        "-dMonoImageDownsampleType=/Subsample",
        f"-dMonoImageResolution={settings['mono_dpi']}",
        f"-dJPEGQ={settings['jpegq']}",
        f"-sOutputFile={output}",
        str(source),
    ]


def compress_pdf(
    source: str | Path,
    destination: str | Path,
    *,
    preset: str = "Readable Claims",
    target_mb: float | None = None,
    ghostscript: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> CompressionResult:
    """Create a smaller PDF/A read-only copy without altering the source."""
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if source_path.suffix.lower() != ".pdf" or not source_path.is_file():
        raise FileNotFoundError(f"Source PDF was not found: {source_path}")
    if destination_path.suffix.lower() != ".pdf":
        raise ValueError("Destination must use the .pdf extension")
    if source_path == destination_path:
        raise ValueError("Save the compressed PDF as a separate copy")
    if destination_path.exists():
        raise FileExistsError(f"Destination already exists: {destination_path}")
    if preset not in COMPRESSION_PRESETS:
        raise ValueError(f"Unknown compression preset: {preset}")
    if target_mb is not None and not 0.1 <= float(target_mb) <= 100.0:
        raise ValueError("Target file size must be from 0.1 MB to 100 MB")

    executable = ghostscript or find_ghostscript()
    if not executable:
        raise RuntimeError("Ghostscript was not found. Run the Claims Bot requirements setup.")

    original_bytes = source_path.stat().st_size
    if original_bytes <= 0:
        raise ValueError("Source PDF is empty")
    source_pages = _validate_pdf(source_path)
    target_bytes = int(float(target_mb) * 1024 * 1024) if target_mb is not None else None
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    file_descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{destination_path.stem}_",
        suffix=".tmp.pdf",
        dir=destination_path.parent,
    )
    os.close(file_descriptor)
    temp_path = Path(temp_name)
    temp_path.unlink(missing_ok=True)
    try:
        base_settings = COMPRESSION_PRESETS[preset]
        dpi_values = (
            TARGET_DPI_LADDERS[preset]
            if target_bytes is not None
            else (int(base_settings["color_dpi"]),)
        )
        best_size: int | None = None
        attempts = 0
        accepted_size: int | None = None

        for dpi in dpi_values:
            attempts += 1
            temp_path.unlink(missing_ok=True)
            settings = {
                "color_dpi": dpi,
                "gray_dpi": dpi,
                "mono_dpi": max(150, min(int(base_settings["mono_dpi"]), dpi * 2)),
                "jpegq": max(38, int(base_settings["jpegq"]) - max(0, int(base_settings["color_dpi"]) - dpi) // 4),
                "grayscale": bool(base_settings.get("grayscale", False)),
            }
            command = _command(executable, source_path, temp_path, settings)
            completed = runner(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if completed.returncode != 0 or not temp_path.is_file():
                details = (
                    completed.stderr or completed.stdout or "Unknown Ghostscript error"
                ).strip()
                raise RuntimeError(f"PDF compression failed: {details}")

            output_pages = _validate_pdf(temp_path)
            if output_pages != source_pages:
                raise RuntimeError(
                    f"Page-count verification failed: source={source_pages}, output={output_pages}"
                )
            compressed_bytes = temp_path.stat().st_size
            best_size = compressed_bytes if best_size is None else min(best_size, compressed_bytes)
            if compressed_bytes < original_bytes and (
                target_bytes is None or compressed_bytes <= target_bytes
            ):
                accepted_size = compressed_bytes
                break

        if accepted_size is None and (best_size is None or best_size >= original_bytes):
            message = (
                "The PDF is already optimized; compression did not create a smaller file. "
                "The original was left unchanged."
            )
            logger.info(f"PDF compression no reduction: {source_path.name} preset={preset}")
            return CompressionResult(
                "NO_REDUCTION", source_path, None, original_bytes,
                best_size or original_bytes, message, target_bytes, attempts,
            )

        if accepted_size is None:
            readability_note = (
                " Readable Claims mode will not go below 200 DPI; increase the maximum "
                "size rather than sacrificing text clarity."
                if preset == "Readable Claims"
                else ""
            )
            message = (
                f"The selected {target_mb:g} MB limit could not be reached safely. "
                f"Smallest verified result was {best_size / (1024 * 1024):.2f} MB. "
                "No over-limit output was saved; the original was left unchanged."
                + readability_note
            )
            logger.warning(
                f"PDF compression target not met: {source_path.name} target_mb={target_mb:g} "
                f"best_bytes={best_size} attempts={attempts}"
            )
            return CompressionResult(
                "TARGET_NOT_MET", source_path, None, original_bytes,
                int(best_size or 0), message, target_bytes, attempts,
            )

        os.replace(temp_path, destination_path)
        os.chmod(destination_path, stat.S_IREAD)
        result = CompressionResult(
            "SAVED",
            source_path,
            destination_path,
            original_bytes,
            accepted_size,
            "Compressed PDF/A read-only copy saved successfully.",
            target_bytes,
            attempts,
        )
        logger.success(
            f"PDF compressed: {source_path.name} -> {destination_path.name} "
            f"preset={preset} target_mb={target_mb if target_mb is not None else 'none'} "
            f"attempts={attempts} savings={result.savings_percent:.1f}%"
        )
        return result
    finally:
        temp_path.unlink(missing_ok=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Create a compressed PDF/A read-only copy")
    parser.add_argument("source")
    parser.add_argument("destination", nargs="?")
    parser.add_argument(
        "--preset", choices=tuple(COMPRESSION_PRESETS), default="Readable Claims"
    )
    parser.add_argument("--target-mb", type=float, default=1.4)
    arguments = parser.parse_args()
    result = compress_pdf(
        arguments.source,
        arguments.destination or default_compressed_path(arguments.source),
        preset=arguments.preset,
        target_mb=arguments.target_mb,
    )
    logger.info(result.message)
