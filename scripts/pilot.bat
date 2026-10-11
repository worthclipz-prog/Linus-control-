@echo off
rem Double-click to open the Pilot window on Windows (after running install_windows.ps1).
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
pilot --gui
