#!/usr/bin/env bash
# Put the Gate on this machine as a normal application. No root, no system
# directories: everything lands under the user's own home.
#
#   bash install-gate.sh [--autostart]   # --autostart opens it at login
#   bash install-gate.sh --remove
#
# The Gate itself stays the same single file that runs from a USB stick. This
# only gives it a menu entry, an icon and, if asked, a place in the session's
# startup, so a trusted laptop does not have to be handed a stick to reach a
# Realm.
set -euo pipefail

SRC=$(cd "$(dirname "$0")/.." && pwd)
BIN=$HOME/.local/bin/celephais-gate
DESKTOP=$HOME/.local/share/applications/celephais.desktop
ICON=$HOME/.local/share/icons/hicolor/scalable/apps/celephais.svg
AUTOSTART=$HOME/.config/autostart/celephais.desktop

if [ "${1:-}" = "--remove" ]; then
  for path in "$BIN" "$DESKTOP" "$ICON" "$AUTOSTART"; do
    [ -e "$path" ] && echo "removing $path"
    rm -f "$path"
  done
  update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
  echo "the vault at $SRC/celephais.vault was left alone"
  exit 0
fi

BUILT=$SRC/build/dist/celephais-gate
[ -f "$BUILT" ] || { echo "no built Gate at $BUILT; run build/make-gate.sh first" >&2
                     exit 1; }

install -D -m 755 "$BUILT" "$BIN"
install -D -m 644 "$SRC/deploy/celephais.svg" "$ICON"

# The vault travels with the Gate on a stick. Installed here it has to be
# named, because $HOME is not the directory the binary sits in.
VAULT=${CELEPHAIS_VAULT:-$SRC/celephais.vault}
install -d -m 700 "$(dirname "$DESKTOP")"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Celephais
Comment=Open a Realm
Exec=env CELEPHAIS_VAULT=$VAULT $BIN --ui
Icon=celephais
Terminal=false
Categories=Network;RemoteAccess;
StartupNotify=false
EOF
chmod 644 "$DESKTOP"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

if [ "${1:-}" = "--autostart" ]; then
  install -D -m 644 "$DESKTOP" "$AUTOSTART"
  echo "it will open at login"
fi

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "note: $HOME/.local/bin is not on your PATH, so the command line"
     echo "      needs the full path: $BIN" ;;
esac

echo "installed: $BIN"
echo "vault:     $VAULT"
echo "Look for Celephais in the application menu, or run: $BIN --ui"
