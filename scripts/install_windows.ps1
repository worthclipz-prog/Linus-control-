# Install Pilot on Windows. Run from the repo root in PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python not found. Install Python 3.10+ from https://www.python.org/downloads/ (tick 'Add to PATH'), then re-run."
    exit 1
}

python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e .
& .\.venv\Scripts\python.exe -m playwright install chromium firefox

if (-not (Test-Path config.toml)) { Copy-Item config.example.toml config.toml }
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

Write-Host ""
Write-Host "Installed. Next:"
Write-Host "  1. Put your OpenAI API key in .env (OPENAI_API_KEY=...)"
Write-Host "  2. .\.venv\Scripts\Activate.ps1; pilot --doctor"
Write-Host "  3. pilot --gui    or    pilot `"open youtube and search for lofi music`""
