#!/usr/bin/env bash
# Set up a Linux Realm for remote use. Run as root INSIDE the Realm.
#
#   bash install-realm.sh <user> <rdp-password>
#
# Remote Login, GNOME's other RDP mode, hands every client a brand new login
# and cannot reattach to one already running: the desktop you left is gone on
# the next connection. This sets up the headless mode instead, which serves
# the session the Realm is already running, so windows survive a disconnect
# and no one has to be logged out first.
set -euo pipefail

USER_NAME=${1:-}
RDP_PASSWORD=${2:-}
[ -n "$USER_NAME" ] && [ -n "$RDP_PASSWORD" ] || {
  echo "usage: install-realm.sh <user> <rdp-password>" >&2; exit 1; }
id "$USER_NAME" >/dev/null || exit 1
UID_NUM=$(id -u "$USER_NAME")
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
SRC=$(cd "$(dirname "$0")" && pwd)

command -v grdctl >/dev/null || {
  echo "gnome-remote-desktop is missing: apt install gnome-remote-desktop" >&2; exit 1; }

# Run a command inside the user's own session, where the headless daemon lives.
as_user() {
  sudo -u "$USER_NAME" \
    XDG_RUNTIME_DIR="/run/user/$UID_NUM" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UID_NUM/bus" "$@"
}

# The daemon needs a certificate it can read itself, so it lives in the user's
# own data directory rather than anywhere root keeps things.
GRD_DIR="$HOME_DIR/.local/share/gnome-remote-desktop"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 700 "$GRD_DIR"
if [ ! -f "$GRD_DIR/rdp.key" ]; then
  openssl req -new -newkey rsa:4096 -days 3650 -nodes -x509 \
    -subj "/CN=celephais-$(hostname -s)" \
    -out "$GRD_DIR/rdp.crt" -keyout "$GRD_DIR/rdp.key" 2>/dev/null
  chown "$USER_NAME:$USER_NAME" "$GRD_DIR/rdp.crt" "$GRD_DIR/rdp.key"
  chmod 600 "$GRD_DIR/rdp.key"; chmod 644 "$GRD_DIR/rdp.crt"
fi

# The two modes listen on the same port, and Remote Login is the one that
# cannot reattach. A Realm set up for headless must not keep offering it.
if grdctl --system status 2>/dev/null | grep -q "Status: enabled"; then
  grdctl --system rdp disable
  systemctl restart gnome-remote-desktop.service || true
  echo "turned off Remote Login: it hands out a new session every time"
fi

as_user grdctl --headless rdp set-tls-cert "$GRD_DIR/rdp.crt"
as_user grdctl --headless rdp set-tls-key  "$GRD_DIR/rdp.key"
as_user grdctl --headless rdp set-credentials "$USER_NAME" "$RDP_PASSWORD"
as_user grdctl --headless rdp enable
# The running daemon keeps the credentials it started with, so a password
# changed under a live service is written to disk and ignored on the wire.
# Re-running this script to change the password must restart it.
as_user systemctl --user restart gnome-remote-desktop-headless.service 2>/dev/null || true

# GNOME refuses to start a remote session while the screen is locked, and
# there is no screen here to unlock it on: the lock would make the Realm
# unreachable. The way in is already a password over a private network.
as_user gsettings set org.gnome.desktop.screensaver lock-enabled false
as_user gsettings set org.gnome.desktop.screensaver idle-activation-enabled false
as_user gsettings set org.gnome.desktop.session idle-delay 0
loginctl unlock-sessions || true

install -m 755 "$SRC/celephais-monitors" /usr/local/bin/celephais-monitors
install -d -m 755 /etc/systemd/user
cat > /etc/systemd/user/celephais-monitors.service <<'EOF'
[Unit]
Description=Celephais: lay the desktop out on the client's monitors
After=gnome-session.target

[Service]
ExecStart=/usr/local/bin/celephais-monitors
Restart=on-failure
RestartSec=5

[Install]
WantedBy=gnome-session.target
EOF

as_user systemctl --user daemon-reload
as_user systemctl --user enable --now gnome-remote-desktop-headless.service
as_user systemctl --user enable --now celephais-monitors.service
sleep 2
as_user systemctl --user is-active gnome-remote-desktop-headless.service celephais-monitors.service
ss -ltn | grep -q ':3389' && echo "RDP is listening on 3389" || echo "nothing on 3389 yet" >&2

echo
echo "Connect with:  sdl-freerdp3 /v:<realm-ip>:3389 /u:$USER_NAME /multimon /gfx /cert:ignore /f"
echo "Do NOT pass /dynamic-resolution with /multimon: FreeRDP then sends a"
echo "layout GNOME rejects, and the session is dropped on connect."
