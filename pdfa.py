import os
import re
import stat
import subprocess

# =========================
# CONFIG
# =========================

GHOSTSCRIPT_PATH = r"C:\Program Files\gs\gs10.07.0\bin\gswin64c.exe"

PDF_FOLDER = r"C:\claims_bot\pdfs"

MAX_SIZE_KB = 1000

# =========================
# UTIL
# =========================

def file_size_kb(path):
    return os.path.getsize(path) / 1024


def make_writable(path):
    if os.path.exists(path):
        os.chmod(path, stat.S_IWRITE)


def find_pdfs_recursive(root_folder):
    """
    Recursively find all PDFs inside root_folder, including subfolders
    (e.g. per-patient folders). Skips leftover temp conversion files
    like '..._pdfa_300.pdf' in case a previous run was interrupted.
    """
    pdf_paths = []

    for current_root, dirs, files in os.walk(root_folder):
        for file in files:
            if not file.lower().endswith(".pdf"):
                continue

            # Skip leftover temp files from a previous interrupted run.
            if re.search(r"_pdfa_\d+\.pdf$", file, flags=re.IGNORECASE):
                continue

            pdf_paths.append(os.path.join(current_root, file))

    return pdf_paths


# =========================
# PDF/A CONVERTER
# =========================

def convert_to_pdfa(pdf_path):

    if not os.path.exists(GHOSTSCRIPT_PATH):
        print("[!] Ghostscript not found")
        return

    make_writable(pdf_path)

    resolutions = [300, 250, 220, 200, 180, 150, 120]

    for res in resolutions:

        temp_pdf = pdf_path.replace(
            ".pdf",
            f"_pdfa_{res}.pdf"
        )

        cmd = [
            GHOSTSCRIPT_PATH,

            "-dPDFA",
            "-dBATCH",
            "-dNOPAUSE",

            "-sDEVICE=pdfwrite",

            "-dPreserveAnnots=true",
            "-dPrinted=true",

            "-dPDFACompatibilityPolicy=1",

            "-dDownsampleColorImages=true",
            f"-dColorImageResolution={res}",

            "-dDownsampleGrayImages=true",
            f"-dGrayImageResolution={res}",

            "-dDownsampleMonoImages=true",
            f"-dMonoImageResolution={res}",

            "-dAutoRotatePages=/None",

            f"-sOutputFile={temp_pdf}",

            pdf_path
        ]

        print(
            f"[TEST] {os.path.basename(pdf_path)} "
            f"{res} DPI"
        )

        subprocess.run(cmd, check=True)

        size = file_size_kb(temp_pdf)

        print(
            f"      Size: {size:.0f} KB"
        )

        if size <= MAX_SIZE_KB:

            os.remove(pdf_path)

            os.rename(temp_pdf, pdf_path)

            # READ ONLY
            os.chmod(pdf_path, stat.S_IREAD)

            print(
                f"[+] FINAL PDF/A: "
                f"{os.path.basename(pdf_path)} "
                f"({size:.0f} KB)"
            )

            return

        os.remove(temp_pdf)

    print(
        "[!] Converted best effort "
        "(did not reach target size)"
    )


# =========================
# MAIN
# =========================

def process():

    if not os.path.exists(PDF_FOLDER):
        print(f"[!] Folder not found: {PDF_FOLDER}")
        return

    pdf_paths = find_pdfs_recursive(PDF_FOLDER)

    if not pdf_paths:
        print(f"[!] No PDF files found under: {PDF_FOLDER}")
        return

    print(f"[+] Found {len(pdf_paths)} PDF file(s) under {PDF_FOLDER} (including subfolders)")

    for pdf_path in pdf_paths:

        try:

            convert_to_pdfa(pdf_path)

        except Exception as e:

            print(
                "[ERROR]",
                pdf_path,
                str(e)
            )


if __name__ == "__main__":
    process()

    if os.environ.get("CLAIMS_GUI_MODE") != "1":
        input("\nDone. Press ENTER to exit...")