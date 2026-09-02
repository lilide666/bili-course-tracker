@echo off
REM ====================================================================
REM  Install dependencies: run ONCE on a new computer
REM  Installs pywebview (desktop window) and Pillow (icon generation)
REM ====================================================================
cd /d "%~dp0"
echo.
echo ==========================================================
echo   Installing dependencies (first-time setup only)
echo ==========================================================
echo.
echo   Upgrading pip ...
py -3 -m pip install --upgrade pip
echo.
echo   Installing pywebview ...
py -3 -m pip install pywebview
echo.
echo   Installing Pillow ...
py -3 -m pip install Pillow
echo.
echo   Installing PyInstaller (for build.py) ...
py -3 -m pip install pyinstaller
echo.
echo ==========================================================
echo   Done! Daily use: double-click the exe in the app folder.
echo   Dev mode: double-click start.bat
echo ==========================================================
echo.
pause
