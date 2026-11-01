@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  ".venv\Scripts\python.exe" -c "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('offshore_metocean') else 1)"
  if errorlevel 1 (
    echo Offshore Metocean is not installed. Run setup.ps1 first.
    pause
    exit /b 1
  )
  start "" ".venv\Scripts\pythonw.exe" -m offshore_metocean gui %*
) else (
  echo Run setup.ps1 first, or install with: python -m pip install -e .
  pause
)
