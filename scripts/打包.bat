@echo off
REM ====================================================================
REM  Build the packaged app (creates / updates the app folder).
REM  Same as running: py -3 build.py
REM ====================================================================
REM  Script lives in scripts\; switch to project root before building.
cd /d "%~dp0.."
echo.
echo   Building app. This may take 1-3 minutes ...
echo.
py -3 -B scripts\build.py
if errorlevel 1 pause
