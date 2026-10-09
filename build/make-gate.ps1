# Build one standalone Gate.exe. Run on Windows, in PowerShell, from the repo root:
#   .\build\make-gate.ps1
# Needs Python 3 from python.org with "Add python.exe to PATH" ticked.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$venv = "build\.venv"
if (-not (Test-Path $venv)) { python -m venv $venv }
& "$venv\Scripts\pip.exe" install --quiet --upgrade pip
& "$venv\Scripts\pip.exe" install --quiet pyinstaller cryptography certifi

# Carry Tailscale inside the exe so a clean Windows host needs nothing
# installed. Install Tailscale once on the BUILD machine, or unpack the
# official zip, so these two files exist.
$tsArgs = @()
foreach ($name in @("tailscaled.exe", "tailscale.exe")) {
  $found = (Get-Command $name -ErrorAction SilentlyContinue).Source
  if (-not $found) {
    $guess = Join-Path $env:ProgramFiles "Tailscale\$name"
    if (Test-Path $guess) { $found = $guess }
  }
  if ($found) { $tsArgs += @("--add-binary", "$found;.") }
  else { Write-Warning "$name not found; the build will fall back to host networking" }
}

# Windows has its own certificate store, but carry the bundle anyway so the
# behaviour matches the Linux build.
$ca = & "$venv\Scripts\python.exe" -c "import certifi;print(certifi.where())"
Copy-Item $ca build\cacert.pem -Force
$ca = "build\cacert.pem"

& "$venv\Scripts\pyinstaller.exe" `
  --onefile --clean --noconfirm `
  --name Gate `
  --paths bin `
  --hidden-import celephais_vault `
  --hidden-import celephais_net `
  @tsArgs `
  --add-data "$ca;." `
  --distpath build\dist --workpath build\work --specpath build `
  bin\celephais-gate

Write-Host ""
Write-Host "built: build\dist\Gate.exe"
