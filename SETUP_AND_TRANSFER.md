# EDH Claims Automation System - Setup and Transfer Guide

This guide is for copying and running the program on another PC.

## 1. Main Rule: Code Updates vs Patient Data

The updater does **not** scan all folders to decide if there is an update.

It checks only `manifest.json` from the NAS. That manifest contains hashes for code and documentation files only.

These patient/user data folders are excluded from update detection and will not be overwritten by updates:

- `scans`
- `output`
- `backup_originals`
- `review_staging`
- `claims_checker_results`
- `merge_input`
- `merge_output`
- `pdfs`
- `signatures`
- `.venv` (recreated locally by the installer)
- logs
- SQLite databases
- generated CSV/report files

So if new patient folders are added, the app will **not** think there is a code update.

## 2. Recommended Folder

Copy the program to:

```text
C:\claims_bot
```

Recommended required folders:

```text
C:\claims_bot\scans
C:\claims_bot\output
C:\claims_bot\backup_originals
C:\claims_bot\review_staging
C:\claims_bot\claims_checker_results
C:\claims_bot\claims_checker_results\READY
C:\claims_bot\claims_checker_results\READY_WITH_REVIEW
C:\claims_bot\claims_checker_results\INCOMPLETE
C:\claims_bot\claims_checker_results\READY_ARCHIVED
C:\claims_bot\merge_input
C:\claims_bot\merge_output
C:\claims_bot\pdfs
C:\claims_bot\signatures
```

The installer/launcher creates these folders automatically if missing.

## 3. First Run on a New PC (Recommended)

1. Copy the application to:

```text
C:\claims_bot
```

2. Double-click:

```text
C:\claims_bot\INSTALL_ON_NEW_PC.bat
```

The one-time installer will:

1. Detect 64-bit Python 3.14.
2. Offer to install Python 3.14 with `winget` if Python is missing.
3. Create an isolated `C:\claims_bot\.venv`.
4. Install/upgrade every package in the root `requirements.txt`.
5. Install/check Microsoft Visual C++ Runtime, Tesseract OCR,
   Ghostscript, and Poppler.
6. Create all required work folders.
7. Import-test every Python library.
8. Test the HBSys connection using read-only `SELECT 1`.

Using `.venv` prevents unrelated packages installed on the computer from
causing errors in Claims Bot.

Command-line alternative:

```powershell
cd C:\claims_bot
python .\install_requirements.py --check-db
```

To verify again without reinstalling:

```powershell
C:\claims_bot\.venv\Scripts\python.exe C:\claims_bot\verify_installation.py --check-db
```

## 4. External Tools

Some tools are not normal Python libraries.

The installer attempts to install these automatically through `winget`:

- Microsoft Visual C++ Runtime
- Ghostscript
- Tesseract OCR
- Poppler

If `winget` is unavailable, the installer prints which external tool must be
installed manually.

## 5. Start the Program

Use:

```text
C:\claims_bot\Claim.bat
```

or:

```powershell
cd C:\claims_bot
python .\start_claims_gui.py
```

`Claim.bat` uses the isolated `.venv`. If `.venv` is missing, it automatically
starts the one-time setup before opening the GUI.

## 5.1 New-PC Operational Requirements

- HBSys client must already be installed and working on that PC.
- The PC must be connected to the hospital network.
- Recommended Windows display: `1920x1080`, scaling `100%`.
- HBSys must be maximized for Date Fill/XML click automation.
- Do not move the mouse or keyboard while live HBSys automation is running.
- Confirm the configured folders in the GUI Preferences tab.
- Copy required signature image files separately into `C:\claims_bot\signatures`.
- Never copy real patient scans merely to test installation.

## 6. About and Version

Open the GUI, then go to:

```text
About
```

You can see:

- Installed version
- NAS update source
- Update status
- Check for Updates button
- Update Now button

## 7. NAS Update Source

The default NAS update source is configured in:

```text
C:\claims_bot\update_config.json
```

Example:

```json
{
    "network_source": "\\\\192.168.1.193\\Echague District Hospital Files\\melvin\\AIBOT(BACKUP)\\claims_bot"
}
```

If the NAS folder changes, edit only `update_config.json`.

## 8. Publishing an Update from the Main PC

On the main/developer PC:

```powershell
cd C:\claims_bot
python .\setup_nas_folders.py
python .\publish_update.py
```

This will:

1. Create/check the NAS update folder.
2. Ask for the new version.
3. Build a code-only `manifest.json`.
4. Copy changed code/documentation files to NAS.
5. Exclude patient/user data folders.

## 9. Applying an Update on Other PCs

On the other PC:

1. Open GUI.
2. Go to `About`.
3. Click `Check for Updates`.
4. If available, click `Update Now`.
5. Restart the app after update.

## 10. Important Safety Notes

- Do not store patient source PDFs inside the NAS update folder.
- Do not publish from a PC that has untested code.
- Local settings such as `claims_gui_config.json` and `doctors_config.json` are not included in updates by default.
- Patient output folders are treated as work data, not app code.
