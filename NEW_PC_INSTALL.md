# EDH Claims — New PC Installation

## One-click installation

1. Copy the complete application code to `C:\claims_bot`.
2. Connect the PC to the hospital network.
3. Double-click `C:\claims_bot\INSTALL_ON_NEW_PC.bat`.
4. Allow Windows/winget installation prompts.
5. Wait for `[VERIFICATION PASSED]`.
6. Start the system using `C:\claims_bot\Claim.bat`.

The setup creates an isolated `.venv` and installs all Python requirements,
Microsoft Visual C++ Runtime, Tesseract OCR, Ghostscript, and Poppler.

## Verify installation

Run:

```powershell
C:\claims_bot\.venv\Scripts\python.exe C:\claims_bot\verify_installation.py --check-db
```

This is non-destructive. The database test executes only:

```sql
SELECT 1 AS ok
```

## Required workstation configuration

- 64-bit Python 3.14 or newer
- Windows with `winget` recommended
- HBSys client installed
- Hospital network/database access
- Screen resolution `1920x1080`, scaling `100%`
- HBSys maximized during Date Fill or XML automation

## Local data not supplied by Python installation

Check these before production use:

- GUI folder paths in Preferences
- `C:\Shared Folder\FTPURL` or the configured XML folder
- Signature images in `C:\claims_bot\signatures`
- Scanner destination
- Printer/scanner drivers
- HBSys login and access

Do not copy patient PDFs merely for installation testing.
