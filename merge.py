# ============================================================
# MERGE PDF + CONVERT TO PDF/A (READ ONLY)
# ============================================================
# REQUIREMENTS:
# pip install PyPDF2
#
# Ghostscript:
# Download and install:
# https://www.ghostscript.com/releases/gsdnld.html
#
# Update GS_PATH below if needed
# ============================================================

import os
import subprocess
from PyPDF2 import PdfMerger

# ============================================================
# CONFIG
# ============================================================

INPUT_FOLDER = r"C:\claims_bot\merge_input"
OUTPUT_FOLDER = r"C:\claims_bot\merge_output"

# Ghostscript executable
GS_PATH = r"C:\Program Files\gs\gs10.07.0\bin\gswin64c.exe"

# PDF/A output quality
DPI = 150

# ============================================================
# CREATE OUTPUT FOLDER
# ============================================================

os.makedirs(OUTPUT_FOLDER, exist_ok=True)

# ============================================================
# GET PDF FILES
# ============================================================

pdf_files = [
    f for f in os.listdir(INPUT_FOLDER)
    if f.lower().endswith(".pdf")
]

pdf_files.sort()

if not pdf_files:
    print("No PDF files found.")
    exit()

# ============================================================
# MERGE PDF
# ============================================================

merged_temp_pdf = os.path.join(OUTPUT_FOLDER, "merged_temp.pdf")

merger = PdfMerger()

print("Merging PDFs...")

for pdf in pdf_files:
    pdf_path = os.path.join(INPUT_FOLDER, pdf)

    print(f"Adding: {pdf}")

    merger.append(pdf_path)

with open(merged_temp_pdf, "wb") as f:
    merger.write(f)

merger.close()

print("Merge completed.")

# ============================================================
# CONVERT TO PDF/A READ ONLY
# ============================================================

final_pdfa = os.path.join(OUTPUT_FOLDER, "merged_PDFA_READONLY.pdf")

print("Converting to PDF/A...")

gs_command = [
    GS_PATH,
    "-dPDFA=2",
    "-dBATCH",
    "-dNOPAUSE",
    "-dNOOUTERSAVE",
    "-sProcessColorModel=DeviceRGB",
    "-sDEVICE=pdfwrite",
    "-dCompatibilityLevel=1.4",

    # Compress images
    "-dDownsampleColorImages=true",
    f"-dColorImageResolution={DPI}",

    "-dDownsampleGrayImages=true",
    f"-dGrayImageResolution={DPI}",

    "-dDownsampleMonoImages=true",
    f"-dMonoImageResolution={DPI}",

    # Embed fonts
    "-dEmbedAllFonts=true",
    "-dSubsetFonts=true",

    # Make output harder to edit
    "-dPrinted=false",
    "-dModifyAnnotations=false",

    f"-sOutputFile={final_pdfa}",
    merged_temp_pdf
]

try:
    subprocess.run(gs_command, check=True)

    print("======================================")
    print("PDF/A READ ONLY CREATED SUCCESSFULLY")
    print(final_pdfa)
    print("======================================")

except subprocess.CalledProcessError as e:
    print("Ghostscript conversion failed.")
    print(e)

# ============================================================
# DELETE TEMP FILE
# ============================================================

if os.path.exists(merged_temp_pdf):
    os.remove(merged_temp_pdf)

print("Done.")