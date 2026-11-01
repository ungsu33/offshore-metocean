param([string]$PythonExe = "python")
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        & $PythonExe -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required. Pass -PythonExe with its full path.' }
    }
    & '.\.venv\Scripts\python.exe' -m pip install -e '.[dev]'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    Write-Host 'Ready. Double-click launch_gui.bat or run .venv\Scripts\offshore-metocean-gui.exe'
} finally { Pop-Location }
