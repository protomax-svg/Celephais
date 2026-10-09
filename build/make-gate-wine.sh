#!/usr/bin/env bash
# Build Gate.exe on Linux, through wine.
#
# PyInstaller packs the Python of the machine it runs on, so it cannot
# cross-compile. It can however run inside wine against a real Windows Python,
# and that produces a genuine Windows exe without owning a Windows machine.
# build/make-gate.ps1 remains the plain route if a Windows box is handy.
#
# Needs: wine, curl, 7z. Everything else is downloaded once and cached in
# build/win-tools and in the wine prefix.
set -euo pipefail
cd "$(dirname "$0")/.."

PYVER=${PYVER:-3.12.10}
TSVER=${TSVER:-1.104.1}
export WINEPREFIX=${WINEPREFIX:-$HOME/.local/share/wineprefixes/celephais-build}
export WINEDEBUG=${WINEDEBUG:--all}
export WINEDLLOVERRIDES="mscoree,mshtml="      # no mono, no gecko, no dialogs

TOOLS=build/win-tools
mkdir -p "$TOOLS"
PYTAG=${PYVER%.*}; PYTAG=${PYTAG/./}          # 3.12.10 -> 312
PY="C:\\users\\$USER\\AppData\\Local\\Programs\\Python\\Python$PYTAG\\python.exe"
PY_UNIX="$WINEPREFIX/drive_c/users/$USER/AppData/Local/Programs/Python/Python$PYTAG/python.exe"

# --- a Windows Python inside the prefix ------------------------------------
if [ ! -f "$PY_UNIX" ]; then
  echo "installing Windows Python $PYVER into $WINEPREFIX"
  installer="$TOOLS/python-$PYVER-amd64.exe"
  [ -f "$installer" ] || curl -sSLf -o "$installer" \
    "https://www.python.org/ftp/python/$PYVER/python-$PYVER-amd64.exe"
  wineboot -u >/dev/null 2>&1 || true
  wine "$installer" /quiet InstallAllUsers=0 PrependPath=1 \
       Include_test=0 Include_launcher=0 >/dev/null 2>&1
  [ -f "$PY_UNIX" ] || { echo "python.exe did not appear; see $WINEPREFIX" >&2; exit 1; }
fi
wine "$PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
wine "$PY" -m pip install --quiet pyinstaller cryptography certifi >/dev/null 2>&1

# --- Tailscale for Windows, out of the official MSI -------------------------
# The MSI names them after their install roles, not their filenames:
#   ClientCmd -> tailscale.exe      Service -> tailscaled.exe
# No wintun.dll: the Gate runs tailscaled with --tun=userspace-networking.
if [ ! -f "$TOOLS/tailscaled.exe" ]; then
  echo "fetching Tailscale $TSVER for Windows"
  msi="$TOOLS/tailscale-$TSVER-amd64.msi"
  [ -f "$msi" ] || curl -sSLf -o "$msi" \
    "https://pkgs.tailscale.com/stable/tailscale-setup-$TSVER-amd64.msi"
  7z x -y -o"$TOOLS/msi" "$msi" >/dev/null
  cp "$TOOLS/msi/ClientCmd" "$TOOLS/tailscale.exe"
  cp "$TOOLS/msi/Service" "$TOOLS/tailscaled.exe"
  rm -rf "$TOOLS/msi"
fi

# --- the CA bundle, same reason as the Linux build --------------------------
[ -f build/cacert.pem ] || cp /etc/ssl/certs/ca-certificates.crt build/cacert.pem

# --specpath makes relative data paths resolve against the spec directory, so
# every one of these has to be absolute. Z: is wine's view of /.
R="Z:$(pwd | tr '/' '\\')"
wine "$PY" -m PyInstaller \
  --onefile --clean --noconfirm --name Gate --paths bin \
  --hidden-import celephais_vault \
  --hidden-import celephais_net \
  --hidden-import celephais_ui \
  --add-binary "$R\\build\\win-tools\\tailscaled.exe;." \
  --add-binary "$R\\build\\win-tools\\tailscale.exe;." \
  --add-data "$R\\build\\cacert.pem;." \
  --distpath 'build\dist-win' --workpath 'build\work-win' --specpath build \
  'bin\celephais-gate'

echo
echo "built: build/dist-win/Gate.exe"
ls -lh build/dist-win/Gate.exe
