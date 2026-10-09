#!/usr/bin/env bash
# Build one standalone Gate executable. Linux and macOS.
# Windows uses build/make-gate.ps1; PyInstaller cannot cross-compile, so the
# .exe must be built on a Windows machine.
set -euo pipefail
cd "$(dirname "$0")/.."

VENV=build/.venv
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet pyinstaller cryptography

"$VENV/bin/pyinstaller" \
  --onefile --clean --noconfirm \
  --name celephais-gate \
  --paths bin \
  --hidden-import celephais_vault \
  --distpath build/dist --workpath build/work --specpath build \
  bin/celephais-gate

echo
echo "built: build/dist/celephais-gate"
ls -lh build/dist/celephais-gate
