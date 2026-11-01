@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m offshore_metocean gui
) else (
  echo Run setup.ps1 first, or install with: python -m pip install -e .
  pause
)
