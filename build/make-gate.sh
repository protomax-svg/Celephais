#!/usr/bin/env bash
# Build one standalone Gate executable. Linux and macOS.
# Windows uses build/make-gate.ps1; PyInstaller cannot cross-compile, so the
# .exe must be built on a Windows machine.
set -euo pipefail
cd "$(dirname "$0")/.."

VENV=build/.venv
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet pyinstaller cryptography certifi

# Carry Tailscale inside the binary so a clean host needs nothing installed.
TS_BINS=()
for name in tailscaled tailscale; do
  found=$(command -v "$name" || ls /usr/sbin/$name /usr/local/bin/$name 2>/dev/null | head -1 || true)
  if [ -n "$found" ]; then
    TS_BINS+=(--add-binary "$found:.")
  else
    echo "warning: $name not found, the build will fall back to host networking" >&2
  fi
done

# Carry a CA bundle too: a minimal host may have no certificate store, and the
# OAuth path talks to api.tailscale.com over HTTPS.
CA=$("$VENV/bin/python" -c "import certifi;print(certifi.where())" 2>/dev/null || true)
[ -n "$CA" ] || CA=/etc/ssl/certs/ca-certificates.crt
CA_ARG=()
if [ -f "$CA" ]; then
  cp "$CA" build/cacert.pem
  # absolute: --specpath makes relative data paths resolve against the spec dir
  CA_ARG=(--add-data "$PWD/build/cacert.pem:.")
fi

"$VENV/bin/pyinstaller" \
  --onefile --clean --noconfirm \
  --name celephais-gate \
  --paths bin \
  --hidden-import celephais_vault \
  --hidden-import celephais_net \
  "${TS_BINS[@]}" "${CA_ARG[@]}" \
  --distpath build/dist --workpath build/work --specpath build \
  bin/celephais-gate

echo
echo "built: build/dist/celephais-gate"
ls -lh build/dist/celephais-gate
