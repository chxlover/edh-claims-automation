from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import fitz  # noqa: E402

from core.pdf_compressor import (  # noqa: E402
    compress_pdf,
    default_compressed_path,
)


def make_pdf(path: Path, pages: int = 2, trailing_bytes: int = 100_000) -> bytes:
    document = fitz.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((72, 72), f"TEST CLAIM PAGE {index + 1}", fontsize=18)
        for row in range(12):
            page.draw_line((72, 120 + row * 35), (520, 120 + row * 35))
    payload = document.tobytes()
    document.close()
    payload += b"X" * trailing_bytes
    path.write_bytes(payload)
    return payload


def write_small_pdf(path: Path, pages: int = 2, trailing_bytes: int = 0) -> None:
    document = fitz.open()
    for _index in range(pages):
        document.new_page()
    document.save(path)
    document.close()
    if trailing_bytes:
        with path.open("ab") as stream:
            stream.write(b"P" * trailing_bytes)


class PDFCompressorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "claim.pdf"
        self.original = make_pdf(self.source)

    def tearDown(self):
        for path in self.root.rglob("*"):
            if path.is_file():
                try:
                    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
        self.temporary.cleanup()

    def test_default_output_is_a_separate_copy(self):
        self.assertEqual(
            default_compressed_path(self.source).name,
            "claim_COMPRESSED_PDFA.pdf",
        )

    def test_readable_claims_uses_grayscale_and_high_resolution(self):
        output = self.root / "readable.pdf"
        captured = []

        def runner(command, **_kwargs):
            captured.append(command)
            output_argument = next(item for item in command if item.startswith("-sOutputFile="))
            write_small_pdf(Path(output_argument.split("=", 1)[1]))
            return subprocess.CompletedProcess(command, 0, "", "")

        result = compress_pdf(
            self.source,
            output,
            preset="Readable Claims",
            ghostscript="fake-gs.exe",
            runner=runner,
        )
        self.assertTrue(result.saved)
        self.assertIn("-sColorConversionStrategy=Gray", captured[0])
        self.assertIn("-dColorImageResolution=300", captured[0])

    def test_compresses_to_verified_read_only_copy_without_changing_source(self):
        output = self.root / "compressed.pdf"
        captured = []

        def runner(command, **_kwargs):
            captured.append(command)
            output_argument = next(item for item in command if item.startswith("-sOutputFile="))
            write_small_pdf(Path(output_argument.split("=", 1)[1]))
            return subprocess.CompletedProcess(command, 0, "", "")

        result = compress_pdf(
            self.source,
            output,
            preset="Balanced",
            ghostscript="fake-gs.exe",
            runner=runner,
        )
        self.assertTrue(result.saved)
        self.assertTrue(output.exists())
        self.assertGreater(result.savings_percent, 0)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertIn("-dColorImageResolution=150", captured[0])
        with fitz.open(output) as document:
            self.assertEqual(document.page_count, 2)

    def test_existing_destination_is_never_overwritten(self):
        output = self.root / "existing.pdf"
        output.write_bytes(b"KEEP")
        with self.assertRaises(FileExistsError):
            compress_pdf(
                self.source,
                output,
                ghostscript="fake-gs.exe",
                runner=lambda *_args, **_kwargs: None,
            )
        self.assertEqual(output.read_bytes(), b"KEEP")

    def test_no_output_is_kept_when_compression_is_not_smaller(self):
        output = self.root / "not_smaller.pdf"

        def runner(command, **_kwargs):
            output_argument = next(item for item in command if item.startswith("-sOutputFile="))
            shutil.copy2(self.source, Path(output_argument.split("=", 1)[1]))
            return subprocess.CompletedProcess(command, 0, "", "")

        result = compress_pdf(
            self.source,
            output,
            preset="High Quality",
            ghostscript="fake-gs.exe",
            runner=runner,
        )
        self.assertEqual(result.status, "NO_REDUCTION")
        self.assertFalse(output.exists())
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_source_cannot_be_used_as_destination(self):
        with self.assertRaises(ValueError):
            compress_pdf(
                self.source,
                self.source,
                ghostscript="fake-gs.exe",
                runner=lambda *_args, **_kwargs: None,
            )

    def test_target_size_retries_until_a_verified_result_fits(self):
        self.original = make_pdf(self.source, trailing_bytes=500_000)
        output = self.root / "targeted.pdf"
        attempted_dpi = []

        def runner(command, **_kwargs):
            dpi = int(
                next(item for item in command if item.startswith("-dColorImageResolution="))
                .split("=", 1)[1]
            )
            attempted_dpi.append(dpi)
            output_argument = next(item for item in command if item.startswith("-sOutputFile="))
            padding = 80_000 if dpi <= 120 else 140_000
            write_small_pdf(Path(output_argument.split("=", 1)[1]), trailing_bytes=padding)
            return subprocess.CompletedProcess(command, 0, "", "")

        result = compress_pdf(
            self.source,
            output,
            preset="Balanced",
            target_mb=0.1,
            ghostscript="fake-gs.exe",
            runner=runner,
        )
        self.assertTrue(result.saved)
        self.assertLessEqual(result.compressed_bytes, int(0.1 * 1024 * 1024))
        self.assertEqual(attempted_dpi, [150, 135, 120])
        self.assertEqual(result.attempts, 3)

    def test_target_not_met_does_not_save_an_over_limit_output(self):
        self.original = make_pdf(self.source, trailing_bytes=500_000)
        output = self.root / "over_limit.pdf"

        def runner(command, **_kwargs):
            output_argument = next(item for item in command if item.startswith("-sOutputFile="))
            write_small_pdf(
                Path(output_argument.split("=", 1)[1]),
                trailing_bytes=150_000,
            )
            return subprocess.CompletedProcess(command, 0, "", "")

        result = compress_pdf(
            self.source,
            output,
            preset="Strong",
            target_mb=0.1,
            ghostscript="fake-gs.exe",
            runner=runner,
        )
        self.assertEqual(result.status, "TARGET_NOT_MET")
        self.assertFalse(output.exists())
        self.assertGreater(result.compressed_bytes, result.target_bytes)
        self.assertEqual(self.source.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
