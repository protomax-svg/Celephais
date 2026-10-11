#!/usr/bin/env bash
# Put the Gate on this machine as a normal application. No root, no system
# directories: everything lands under the user's own home.
#
#   bash install-gate.sh [--autostart] [--no-password]
#   bash install-gate.sh --remove
#
# --autostart    opens it at login
# --no-password  keep the vault key on this machine, so the Gate never asks.
#                Only for a machine that is already trusted: anyone who can
#                read this home directory can then open every Realm.
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
KEY=$HOME/.config/celephais/key

WANT_AUTOSTART=no
WANT_KEY=no
for arg in "$@"; do
  case "$arg" in
    --autostart)   WANT_AUTOSTART=yes ;;
    --no-password) WANT_KEY=yes ;;
    --remove)      ;;
    *) echo "unknown option: $arg" >&2; exit 1 ;;
  esac
done

if [ "${1:-}" = "--remove" ]; then
  for path in "$BIN" "$DESKTOP" "$ICON" "$AUTOSTART" "$KEY"; do
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

# The key is checked against the vault before it is kept, so a typo is caught
# here and not at the next login with no screen to report it on.
KEY_ENV=
if [ "$WANT_KEY" = yes ]; then
  read -r -s -p "Vault password (stored on this machine): " pw; echo
  PY=$SRC/build/.venv/bin/python
  [ -x "$PY" ] || PY=python3
  CELEPHAIS_VAULT_PW=$pw CELEPHAIS_BIN=$SRC/bin \
  "$PY" - "$VAULT" <<'CHECK' || {
import os, pathlib, sys
sys.path.insert(0, os.environ["CELEPHAIS_BIN"])
import celephais_vault
celephais_vault.load(pathlib.Path(sys.argv[1]), os.environ["CELEPHAIS_VAULT_PW"])
CHECK
    echo "that password does not open $VAULT; nothing was stored" >&2; exit 1; }
  install -d -m 700 "$(dirname "$KEY")"
  printf '%s\n' "$pw" > "$KEY"
  chmod 600 "$KEY"
  unset pw
  KEY_ENV="CELEPHAIS_KEY_FILE=$KEY "
  echo "the vault key is kept in $KEY; the Gate will not ask again here"
elif [ -f "$KEY" ]; then
  KEY_ENV="CELEPHAIS_KEY_FILE=$KEY "      # keep what an earlier run stored
fi

install -d -m 700 "$(dirname "$DESKTOP")"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Celephais
Comment=Open a Realm
Exec=env CELEPHAIS_VAULT=$VAULT ${KEY_ENV}$BIN --ui
Icon=celephais
Terminal=false
Categories=Network;RemoteAccess;
StartupNotify=false
EOF
chmod 644 "$DESKTOP"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

if [ "$WANT_AUTOSTART" = yes ]; then
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
