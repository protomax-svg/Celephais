#!/usr/bin/env bash
# Set up a Linux Realm for remote use. Run as root INSIDE the Realm, from a
# terminal in the user's own desktop session. Ubuntu and Fedora.
#
#   bash install-realm.sh <user> [rdp-password] [--tailnet <name>]
#
# Without a password it asks for one, so it stays out of the shell history.
# --tailnet puts the Realm on the tailnet under that name, which is what makes
# it reachable from a Gate away from home. The login link it prints has to be
# opened once.
#
# Remote Login, GNOME's other RDP mode, hands every client a brand new login
# and cannot reattach to one already running: the desktop you left is gone on
# the next connection. This sets up the headless mode instead, which serves
# the session the Realm is already running, so windows survive a disconnect
# and no one has to be logged out first.
set -euo pipefail

USER_NAME=""; RDP_PASSWORD=""; TAILNET_NAME=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tailnet) TAILNET_NAME=${2:-}; shift ;;
    *) if [ -z "$USER_NAME" ]; then USER_NAME=$1; else RDP_PASSWORD=$1; fi ;;
  esac
  shift
done
[ -n "$USER_NAME" ] || {
  echo "usage: install-realm.sh <user> [rdp-password] [--tailnet <name>]" >&2; exit 1; }
[ "$(id -u)" = 0 ] || { echo "run this as root: sudo bash $0 ..." >&2; exit 1; }
id "$USER_NAME" >/dev/null || exit 1
if [ -z "$RDP_PASSWORD" ]; then
  [ -t 0 ] || { echo "no password given and nobody to ask" >&2; exit 1; }
  read -rsp "RDP password for $USER_NAME: " RDP_PASSWORD; echo
  read -rsp "again: " _again; echo
  [ -n "$RDP_PASSWORD" ] && [ "$RDP_PASSWORD" = "$_again" ] || {
    echo "the two passwords differ, or are empty" >&2; exit 1; }
fi
UID_NUM=$(id -u "$USER_NAME")
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
SRC=$(cd "$(dirname "$0")" && pwd)
[ -d "/run/user/$UID_NUM" ] || {
  echo "$USER_NAME has no session running: log in on the desktop first" >&2; exit 1; }

# Whatever is missing, from whichever package manager this Realm has.
need() {
  local missing=() cmd pkg
  for pair in "$@"; do
    cmd=${pair%%:*}; pkg=${pair#*:}
    command -v "$cmd" >/dev/null || missing+=("$pkg")
  done
  [ ${#missing[@]} -gt 0 ] || return 0
  if command -v dnf >/dev/null; then
    dnf install -y -q --setopt=skip_if_unavailable=True "${missing[@]}"
  else
    export DEBIAN_FRONTEND=noninteractive
    apt-get install -y -qq "${missing[@]}" || {
      apt-get update -qq || true; apt-get install -y -qq "${missing[@]}"; }
  fi
}
need grdctl:gnome-remote-desktop openssl:openssl

# Core reaches into a Realm through the QEMU guest agent: to ask which exit
# node it uses, to change it, and to log the desktop out. On Fedora SELinux
# keeps the agent in a domain that may list network interfaces and not much
# else, so those commands came back "permission denied", and the egress check
# read that as "tailscale is not installed here". The agent is already a root
# channel from the host, which owns this VM outright; confining it protects
# nothing here. Permissive keeps the policy loaded and its denials logged.
if command -v getenforce >/dev/null && [ "$(getenforce)" != Disabled ]; then
  need semanage:policycoreutils-python-utils
  semanage permissive -l 2>/dev/null | grep -qx 'virt_qemu_ga_t' ||
    semanage permissive -a virt_qemu_ga_t
  echo "SELinux: the guest agent may run Core's commands"
fi

# The headless remote desktop lives inside a desktop session, and a Realm
# that boots without anyone at it has none unless GDM logs the user in by
# itself. Without this a Realm only answers RDP after somebody has logged in
# at its screen, and a headless Realm has no screen.
GDM_CONF=/etc/gdm/custom.conf
[ -d /etc/gdm3 ] && GDM_CONF=/etc/gdm3/custom.conf
if [ -d "$(dirname "$GDM_CONF")" ]; then
  [ -f "$GDM_CONF" ] || printf '[daemon]\n' > "$GDM_CONF"
  grep -q '^\[daemon\]' "$GDM_CONF" || printf '\n[daemon]\n' >> "$GDM_CONF"
  sed -i '/^AutomaticLogin\(Enable\)\?=/d' "$GDM_CONF"
  sed -i "/^\[daemon\]/a AutomaticLoginEnable=True\nAutomaticLogin=$USER_NAME" "$GDM_CONF"
  echo "GDM logs $USER_NAME in at boot ($GDM_CONF)"
fi

# Fedora's firewall already lets 1025-65535 in on a Workstation, but a Realm
# installed from another spin may not, and a closed 3389 looks exactly like a
# Realm that never started its remote desktop.
if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd -q --permanent --add-port=3389/tcp
  firewall-cmd -q --reload
fi

# Run a command inside the user's own session, where the headless daemon lives.
as_user() {
  sudo -u "$USER_NAME" \
    XDG_RUNTIME_DIR="/run/user/$UID_NUM" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UID_NUM/bus" "$@"
}

# The daemon needs a certificate it can read itself, so it lives in the user's
# own data directory rather than anywhere root keeps things.
GRD_DIR="$HOME_DIR/.local/share/gnome-remote-desktop"
install -d -o "$USER_NAME" -g "$(id -g "$USER_NAME")" -m 700 "$GRD_DIR"
if [ ! -f "$GRD_DIR/rdp.key" ]; then
  # uname, not hostname: Fedora no longer installs the hostname command.
  openssl req -new -newkey rsa:4096 -days 3650 -nodes -x509 \
    -subj "/CN=celephais-$(uname -n | cut -d. -f1)" \
    -out "$GRD_DIR/rdp.crt" -keyout "$GRD_DIR/rdp.key" 2>/dev/null
  chown "$USER_NAME:$(id -g "$USER_NAME")" "$GRD_DIR/rdp.crt" "$GRD_DIR/rdp.key"
  chmod 600 "$GRD_DIR/rdp.key"; chmod 644 "$GRD_DIR/rdp.crt"
  restorecon -R "$GRD_DIR" 2>/dev/null || true
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

# Nor may it put itself to sleep. Fedora's GNOME suspends after a while idle,
# and a suspended guest is "running" to Proxmox with its agent and its remote
# desktop frozen: `personal` did exactly that on the monitors, read as BOOTING,
# and had to be stopped in Proxmox. Masked rather than only switched off in
# GNOME, so the menu's Suspend cannot do it either. Hibernation is left alone:
# it is the one guest-side way to keep a session across a seat change.
systemctl mask --quiet suspend.target hybrid-sleep.target suspend-then-hibernate.target
as_user gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type nothing
as_user gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type nothing

install -m 755 "$SRC/celephais-monitors" /usr/local/bin/celephais-monitors
restorecon /usr/local/bin/celephais-monitors 2>/dev/null || true
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

# On the tailnet Core hands the Gate this Realm's 100.x address, which works
# from anywhere. Without it the Realm answers on the house wire only.
if [ -n "$TAILNET_NAME" ]; then
  # Tailscale's own script knows Fedora and Ubuntu; Fedora also packages it.
  command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh ||
    { command -v dnf >/dev/null && dnf install -y -q tailscale; }
  systemctl enable --now tailscaled
  if tailscale status >/dev/null 2>&1; then
    tailscale set --hostname="$TAILNET_NAME"
  else
    echo "open the link below once to put this Realm on the tailnet"
    tailscale up --hostname="$TAILNET_NAME"
  fi
  # An exit node otherwise swallows the house network along with the rest,
  # and with it the shared disk and every machine on the same wire.
  tailscale set --exit-node-allow-lan-access=true
  echo "on the tailnet as $TAILNET_NAME: $(tailscale ip -4 | head -1)"
fi

echo
echo "Connect with:  sdl-freerdp3 /v:<realm-ip>:3389 /u:$USER_NAME /multimon /gfx /cert:ignore /f"
echo "Do NOT pass /dynamic-resolution with /multimon: FreeRDP then sends a"
echo "layout GNOME rejects, and the session is dropped on connect."
