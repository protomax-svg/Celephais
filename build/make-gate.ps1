# Build the Gate for Windows. Run on Windows, in PowerShell, from the repo root:
#   .\build\make-gate.ps1
# Needs Python 3 from python.org with "Add python.exe to PATH" ticked.
#
# Two executables out of the same script, because one cannot be both:
#
#   Gate.exe            windowed, for the double-click. It has no console at
#                       all, so nothing a subprocess says can land behind the
#                       overlay. The overlay is fullscreen and topmost, and it
#                       used to cover a console that tailscaled was still
#                       writing into - the output showed through anyway.
#   celephais-gate.exe  console, for the command line, where the output is the
#                       whole point.
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
# Absolute: --specpath makes relative data paths resolve against the spec dir.
$ca = (Resolve-Path build\cacert.pem).Path

foreach ($variant in @(@{ Name = "Gate";           Window = "--noconsole" },
                       @{ Name = "celephais-gate"; Window = "--console" })) {
  & "$venv\Scripts\pyinstaller.exe" `
    --onefile --clean --noconfirm `
    --name $variant.Name `
    $variant.Window `
    --paths bin `
    --hidden-import celephais_vault `
    --hidden-import celephais_net `
    --hidden-import celephais_ui `
    @tsArgs `
    --add-data "$ca;." `
    --distpath build\dist --workpath build\work --specpath build `
    bin\celephais-gate
}

Write-Host ""
Write-Host "built: build\dist\Gate.exe            (double-click)"
Write-Host "built: build\dist\celephais-gate.exe  (command line)"
