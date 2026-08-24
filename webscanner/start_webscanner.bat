@echo off
REM WebScanner launcher - CamScanner-like web app for phone browsers.
cd /d "%~dp0"
echo Starting WebScanner...
echo Open https://<LAN-IP>:8443 on your phone (same Wi-Fi).
echo Camera needs HTTPS - tap Advanced - Proceed on first visit.
..\.venv\Scripts\python.exe app.py
pause
