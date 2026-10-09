# Build one standalone Gate.exe. Run on Windows, in PowerShell, from the repo root:
#   .\build\make-gate.ps1
# Needs Python 3 from python.org with "Add python.exe to PATH" ticked.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$venv = "build\.venv"
if (-not (Test-Path $venv)) { python -m venv $venv }
& "$venv\Scripts\pip.exe" install --quiet --upgrade pip
& "$venv\Scripts\pip.exe" install --quiet pyinstaller cryptography

& "$venv\Scripts\pyinstaller.exe" `
  --onefile --clean --noconfirm `
  --name Gate `
  --paths bin `
  --hidden-import celephais_vault `
  --distpath build\dist --workpath build\work --specpath build `
  bin\celephais-gate

Write-Host ""
Write-Host "built: build\dist\Gate.exe"
