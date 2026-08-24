@echo off
cd /d "C:\claims_bot"
if not exist ".venv\Scripts\python.exe" (
    echo EDH Claims isolated environment is not installed.
    echo Starting the one-time workstation setup...
    echo.
    call INSTALL_ON_NEW_PC.bat
    if not exist ".venv\Scripts\python.exe" (
        echo Setup is incomplete. The program cannot start safely.
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" start_claims_gui.py
pause
