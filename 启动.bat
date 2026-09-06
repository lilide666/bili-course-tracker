@echo off
REM ====================================================================
REM  Dev launcher: start the app from source (needs Python + deps).
REM  For daily use, double-click the exe inside the app folder instead.
REM ====================================================================
cd /d "%~dp0"
echo.
echo   Starting Bilibili Course Tracker (dev mode) ...
echo.
start "" pyw -3 src\app.py
exit
