@echo off
setlocal
cd /d "%~dp0"
title EDH Claims Automation System - New PC Setup

echo ========================================================================
echo EDH Claims Automation System - New PC Setup
echo ========================================================================
echo.

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 (
    py -3.14 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=py -3.14"
)

if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo Python was not found. Attempting to install Python 3.14 with winget...
    where winget >nul 2>nul
    if errorlevel 1 (
        echo.
        echo INSTALL FAILED: winget is unavailable.
        echo Install 64-bit Python 3.14, then run this file again.
        pause
        exit /b 1
    )
    winget install --id Python.Python.3.14 --exact --silent ^
        --accept-package-agreements --accept-source-agreements
    if exist "%LocalAppData%\Programs\Python\Python314\python.exe" (
        set PYTHON_CMD="%LocalAppData%\Programs\Python\Python314\python.exe"
    )
    if not defined PYTHON_CMD if exist "%LocalAppData%\Programs\Python\Launcher\py.exe" (
        set PYTHON_CMD="%LocalAppData%\Programs\Python\Launcher\py.exe" -3.14
    )
    if not defined PYTHON_CMD (
        echo.
        echo Python was installed but this window cannot locate it yet.
        echo Close this window and run INSTALL_ON_NEW_PC.bat again.
        pause
        exit /b 1
    )
)

%PYTHON_CMD% install_requirements.py --check-db
if errorlevel 1 (
    echo.
    echo Setup did not complete. Review the FAILED line above.
    pause
    exit /b 1
)

echo.
echo Setup completed. You may now run Claim.bat.
pause
exit /b 0
