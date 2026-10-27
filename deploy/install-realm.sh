#!/usr/bin/env bash
# Set up a Linux Realm for remote use. Run as root INSIDE the Realm, from a
# terminal in the user's own desktop session. Ubuntu and Fedora, GNOME and Xfce.
#
#   bash install-realm.sh <user> [password] [--desktop gnome|xfce] [--switchable]
#                         [--screen <width>x<height>] [--tailnet <name>]
#
# --switchable sets up both servers at once, on a Realm that has both desktops
# installed, and leaves the one --desktop names running. The Gate can then move
# the Realm from one to the other without anybody logging into it to do the
# work; celephais-desktop is what carries that out.
#
# Without a password it asks for one, unless the Realm already has one stored
# from an earlier run: re-running to change something else leaves it alone.
# --tailnet puts the Realm on the tailnet under that name, which is what makes
# it reachable from a Gate away from home. The login link it prints has to be
# opened once.
#
# Either way the Realm answers RDP on 3389 and serves the session it is already
# running, so windows survive a disconnect. How it gets there depends on the
# desktop, and the two ways have nothing in common but the port:
#
#   GNOME: gnome-remote-desktop in its headless mode. Remote Login, GNOME's
#     other RDP mode, hands every client a brand new login and cannot reattach
#     to one already running: the desktop you left is gone on the next
#     connection.
#   Xfce, and anything else on X11: x11vnc attached to the session on :0, with
#     xrdp in front of it translating RDP to VNC over the loopback. grdctl
#     drives mutter's own screencast API and no other desktop implements it, so
#     a Realm that leaves GNOME leaves gnome-remote-desktop behind with it.
#     x11vnc asks for nothing but an X server. The Gate speaks RDP and only
#     RDP - FreeRDP, port 3389, Core's readiness probe, the tailnet forward -
#     and none of it has to know which of these is behind the port.
set -euo pipefail

# 1920x1080 by default: the size of the screen a Gate is usually opened on,
# and the one an x11vnc Realm has to be set to in advance. See celephais-x11vnc
# and docs/ARCHITECTURE.md on why the client cannot ask for its own.
USER_NAME=""; PASSWORD=""; TAILNET_NAME=""; DESKTOP=""; SCREEN=1920x1080
SWITCHABLE=no
# --agent adds a second desktop that belongs to an agent rather than to the
# person whose Realm this is: its own X server, up from boot, never logged out
# of, on ports of its own beside the Realm's. See celephais-agent-desktop.
AGENT=no; AGENT_SCREEN=""; AGENT_DISPLAY=:2
AGENT_VNC_PORT=5901; AGENT_RDP_PORT=3390
while [ $# -gt 0 ]; do
  case "$1" in
    --tailnet) TAILNET_NAME=${2:-}; shift ;;
    --desktop) DESKTOP=${2:-}; shift ;;
    --screen) SCREEN=${2:-}; shift ;;
    --switchable) SWITCHABLE=yes ;;
    --agent) AGENT=yes ;;
    --agent-screen) AGENT_SCREEN=${2:-}; shift ;;
    --agent-display) AGENT_DISPLAY=${2:-}; shift ;;
    *) if [ -z "$USER_NAME" ]; then USER_NAME=$1; else PASSWORD=$1; fi ;;
  esac
  shift
done
[ -n "$USER_NAME" ] || {
  echo "usage: install-realm.sh <user> [password] [--desktop gnome|xfce]" >&2
  echo "       [--switchable] [--screen <width>x<height>] [--tailnet <name>]" >&2
  echo "       [--agent [--agent-screen <width>x<height>]]" >&2
  exit 1; }
[ "$(id -u)" = 0 ] || { echo "run this as root: sudo bash $0 ..." >&2; exit 1; }
id "$USER_NAME" >/dev/null || exit 1
UID_NUM=$(id -u "$USER_NAME")
GID_NUM=$(id -g "$USER_NAME")
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
SRC=$(cd "$(dirname "$0")" && pwd)
[ -d "/run/user/$UID_NUM" ] || {
  echo "$USER_NAME has no session running: log in on the desktop first" >&2; exit 1; }

# Run a command inside the user's own session, where the desktop lives.
as_user() {
  sudo -u "$USER_NAME" \
    XDG_RUNTIME_DIR="/run/user/$UID_NUM" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$UID_NUM/bus" "$@"
}

# Which desktop this Realm runs, asked of the session rather than of the
# packages installed: a Realm switched from GNOME to Xfce still has all of
# GNOME on its disk, and grdctl on the PATH means nothing about what is on
# the screen.
if [ -z "$DESKTOP" ]; then
  if pgrep -u "$UID_NUM" -x gnome-shell >/dev/null 2>&1; then
    DESKTOP=gnome
  elif pgrep -u "$UID_NUM" -x xfce4-session >/dev/null 2>&1; then
    DESKTOP=xfce
  else
    echo "cannot tell which desktop $USER_NAME is logged into:" >&2
    echo "say so with --desktop gnome|xfce" >&2
    exit 1
  fi
  echo "desktop: $DESKTOP (detected)"
else
  echo "desktop: $DESKTOP"
fi
case "$DESKTOP" in
  gnome|xfce) ;;
  *) echo "--desktop takes gnome or xfce, not $DESKTOP" >&2; exit 1 ;;
esac

# Which of the two servers to set up. One Realm, one port: only the desktop
# named by --desktop is left running, and --switchable means the other is put
# in place switched off, ready for celephais-desktop to turn it on. Only what
# this Realm has a desktop for - gnome-remote-desktop on a Realm with no GNOME
# would be a service with nothing to serve.
WANT_GNOME=no; WANT_X11VNC=no
if [ "$DESKTOP" = gnome ]; then WANT_GNOME=yes; else WANT_X11VNC=yes; fi
if [ "$SWITCHABLE" = yes ]; then
  if command -v gnome-shell >/dev/null; then WANT_GNOME=yes
  elif [ "$DESKTOP" != gnome ]; then echo "no GNOME installed here to switch to"; fi
  if ls /usr/share/xsessions/*.desktop >/dev/null 2>&1; then WANT_X11VNC=yes
  elif [ "$DESKTOP" = gnome ]; then echo "no X11 desktop installed here to switch to"; fi
  echo "switchable: gnome=$WANT_GNOME x11vnc=$WANT_X11VNC"
fi

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
need openssl:openssl
[ "$WANT_GNOME" = yes ] && need grdctl:gnome-remote-desktop
[ "$WANT_X11VNC" = yes ] && need x11vnc:x11vnc xrdp:xrdp xfconf-query:xfconf \
                                 xrandr:x11-xserver-utils
if [ "$AGENT" = yes ]; then
  need Xvfb:xvfb xauth:xauth x11vnc:x11vnc xrdp:xrdp \
       dbus-run-session:dbus mcookie:util-linux
  command -v xfce4-session >/dev/null || {
    echo "--agent wants an Xfce to put on the agent's display, and there is" >&2
    echo "none here: install xfce4-session first" >&2; exit 1; }
  [ -n "$AGENT_SCREEN" ] || AGENT_SCREEN=$SCREEN
fi
# The password the Gate signs in with. Each server keeps a copy of its own:
# x11vnc in a password file, gnome-remote-desktop in the login keyring, where
# nothing else can read it back. A server being set up for the first time has
# to be given one; a re-run that changes something else leaves whatever is
# already stored alone. Note that "disabled" contains "enabled", so the status
# is matched with the colon in front of it.
#
# The file is Celephais's own and not x11vnc's default ~/.vnc/passwd, because
# that one may well already exist with a password somebody chose for their own
# reasons. Finding it there and keeping it looks thrifty and is in fact a trap:
# xrdp hands the VNC server whatever password the RDP client sent, so the one
# in this file has to be the one the Gate holds in its vault, and a stranger's
# password in the right place fails authentication every time while looking
# perfectly configured. An unrelated ~/.vnc/passwd is left where it is.
VNC_PASSWD="$HOME_DIR/.vnc/celephais.passwd"
NEED_PASSWORD=no
[ "$WANT_X11VNC" = yes ] && [ ! -s "$VNC_PASSWD" ] && NEED_PASSWORD=yes
if [ "$WANT_GNOME" = yes ] &&
   ! as_user grdctl --headless status 2>/dev/null |
     grep -qiE 'status:[[:space:]]*enabled'; then
  NEED_PASSWORD=yes
fi
if [ -z "$PASSWORD" ] && [ "$NEED_PASSWORD" = yes ]; then
  [ -t 0 ] || { echo "no password given and nobody to ask" >&2; exit 1; }
  read -rsp "RDP password for $USER_NAME: " PASSWORD; echo
  read -rsp "again: " _again; echo
  [ -n "$PASSWORD" ] && [ "$PASSWORD" = "$_again" ] || {
    echo "the two passwords differ, or are empty" >&2; exit 1; }
fi


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

# The remote desktop lives inside a desktop session - GNOME's headless mode
# inside the user's, x11vnc attached to the X server that session owns - and a
# Realm that boots with nobody at it has none unless the display manager logs
# the user in by itself. Without this a Realm only answers RDP after somebody
# has logged in at its screen, and a headless Realm has no screen.
GDM_CONF=/etc/gdm/custom.conf
[ -d /etc/gdm3 ] && GDM_CONF=/etc/gdm3/custom.conf
if [ -d "$(dirname "$GDM_CONF")" ]; then
  [ -f "$GDM_CONF" ] || printf '[daemon]\n' > "$GDM_CONF"
  grep -q '^\[daemon\]' "$GDM_CONF" || printf '\n[daemon]\n' >> "$GDM_CONF"
  sed -i '/^AutomaticLogin\(Enable\)\?=/d' "$GDM_CONF"
  sed -i "/^\[daemon\]/a AutomaticLoginEnable=True\nAutomaticLogin=$USER_NAME" "$GDM_CONF"
  echo "GDM logs $USER_NAME in at boot ($GDM_CONF)"
fi
# GDM starts the session AccountsService remembers for the user, which is how
# a Realm that was switched to Xfce by hand comes back up in Xfce.
if [ -f "/var/lib/AccountsService/users/$USER_NAME" ]; then
  echo "session at next boot: $(awk -F= '/^Session=/{print $2}' \
    "/var/lib/AccountsService/users/$USER_NAME")"
fi

# Fedora's firewall already lets 1025-65535 in on a Workstation, but a Realm
# installed from another spin may not, and a closed 3389 looks exactly like a
# Realm that never started its remote desktop.
if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd -q --permanent --add-port=3389/tcp
  firewall-cmd -q --reload
fi
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q '^Status: active'; then
  ufw allow 3389/tcp >/dev/null
fi

if [ "$WANT_GNOME" = yes ]; then
  # The daemon needs a certificate it can read itself, so it lives in the
  # user's own data directory rather than anywhere root keeps things.
  GRD_DIR="$HOME_DIR/.local/share/gnome-remote-desktop"
  install -d -o "$USER_NAME" -g "$GID_NUM" -m 700 "$GRD_DIR"
  if [ ! -f "$GRD_DIR/rdp.key" ]; then
    # uname, not hostname: Fedora no longer installs the hostname command.
    openssl req -new -newkey rsa:4096 -days 3650 -nodes -x509 \
      -subj "/CN=celephais-$(uname -n | cut -d. -f1)" \
      -out "$GRD_DIR/rdp.crt" -keyout "$GRD_DIR/rdp.key" 2>/dev/null
    chown "$USER_NAME:$GID_NUM" "$GRD_DIR/rdp.crt" "$GRD_DIR/rdp.key"
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
  if [ -n "$PASSWORD" ]; then
    # Set from outside a GNOME session this may fail: grdctl puts the
    # password in the login keyring, and Xfce does not always have one
    # unlocked. Said plainly rather than left to be discovered on the switch.
    if as_user grdctl --headless rdp set-credentials "$USER_NAME" "$PASSWORD"; then
      echo "gnome-remote-desktop has the password"
    else
      echo "could not store the password for gnome-remote-desktop; run this" >&2
      echo "again from inside a GNOME session to finish setting it up" >&2
    fi
  else
    echo "no password given: gnome-remote-desktop keeps the one it has"
  fi
  as_user grdctl --headless rdp enable
  # The running daemon keeps the credentials it started with, so a password
  # changed under a live service is written to disk and ignored on the wire.
  # Re-running this script to change the password must restart it.
  as_user systemctl --user restart gnome-remote-desktop-headless.service 2>/dev/null || true

  # GNOME refuses to start a remote session while the screen is locked, and
  # there is no screen here to unlock it on: the lock would make the Realm
  # unreachable. The way in is already a password over a private network.
  # These are the user's own settings, so they can be written from any
  # desktop and are waiting when GNOME is the one that starts.
  as_user gsettings set org.gnome.desktop.screensaver lock-enabled false
  as_user gsettings set org.gnome.desktop.screensaver idle-activation-enabled false
  as_user gsettings set org.gnome.desktop.session idle-delay 0
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
  if [ "$DESKTOP" = gnome ]; then
    as_user systemctl --user enable --now gnome-remote-desktop-headless.service
    as_user systemctl --user enable --now celephais-monitors.service
    sleep 2
    as_user systemctl --user is-active gnome-remote-desktop-headless.service \
      celephais-monitors.service
  else
    # Set up and switched off: enabled only when celephais-desktop moves the
    # Realm to GNOME, because both servers want port 3389 and a user service
    # cannot start in a session that is not running yet anyway.
    as_user systemctl --user disable --now gnome-remote-desktop-headless.service 2>/dev/null || true
    as_user systemctl --user disable --now celephais-monitors.service 2>/dev/null || true
    echo "gnome-remote-desktop is set up and idle; the Gate can switch to it"
  fi
fi

if [ "$WANT_X11VNC" = yes ]; then
  # x11vnc serves the X server the session already owns, so there is one
  # desktop and everyone sees the same one: the Gate over RDP, and an
  # `ssh -N -L 5900:127.0.0.1:5900` tunnel straight to the VNC port, which is
  # the way in when xrdp itself is what broke. -localhost keeps the VNC port
  # off the wire for exactly that reason: the only ways to it are xrdp on this
  # machine and a tunnel from someone who can already log in over SSH.
  if [ -n "$PASSWORD" ]; then
    install -d -o "$USER_NAME" -g "$GID_NUM" -m 700 "$HOME_DIR/.vnc"
    as_user x11vnc -storepasswd "$PASSWORD" "$VNC_PASSWD" >/dev/null
    chmod 600 "$VNC_PASSWD"
    echo "stored the VNC password in $VNC_PASSWD"
    # Worth saying out loud rather than letting somebody discover it: VNC
    # authentication is DES with the password as the key, and a DES key is
    # eight bytes. Everything past the eighth character is dropped by both
    # ends, so a long password is not the long password it looks like. It
    # still has to match what the Gate sends - the first eight of it do.
    if [ "${#PASSWORD}" -gt 8 ]; then
      echo "note: VNC authentication only reads the first 8 characters"
    fi
  else
    echo "kept the password already in $VNC_PASSWD"
  fi

  # Not a user service: the user's systemd never learns that Xfce has started
  # - xfce4-session reaches no graphical-session.target here - so a unit bound
  # to the login would start before there is an X server to attach to. A
  # system service that restarts for ever waits out the gap after a reboot,
  # and attaches again by itself after a logout and back in.
  install -m 755 "$SRC/celephais-x11vnc" /usr/local/bin/celephais-x11vnc
  restorecon /usr/local/bin/celephais-x11vnc 2>/dev/null || true
  cat > /etc/systemd/system/celephais-x11vnc.service <<EOF
[Unit]
Description=Celephais: serve this Realm's own desktop over VNC on the loopback
After=display-manager.service
Wants=display-manager.service

[Service]
User=$USER_NAME
ExecStart=/usr/local/bin/celephais-x11vnc --password-file $VNC_PASSWD --screen $SCREEN
Restart=always
RestartSec=5
# x11vnc answers SIGTERM with exit 2, so every deliberate stop - and the
# desktop switch stops it every time - would otherwise leave the unit sitting
# in "failed" with nothing wrong with it.
SuccessExitStatus=2

[Install]
WantedBy=graphical.target
EOF
  systemctl daemon-reload

  # xrdp in front of it, speaking RDP on 3389 and VNC to the loopback. Its own
  # session modules are left alone and unused: [Xorg] would start a second
  # desktop beside the one on the screen, which is the reattach problem again
  # under another name. autorun picks ours before the chooser is ever drawn,
  # so FreeRDP lands in the session rather than at a menu, and `password=ask`
  # means the password the Gate sends is the one x11vnc is asked for -
  # username=na because a VNC server has no idea what a user is.
  XRDP_INI=/etc/xrdp/xrdp.ini
  cp -n "$XRDP_INI" "$XRDP_INI.celephais-orig" 2>/dev/null || true
  python3 - "$XRDP_INI" <<'EOF'
import re, sys
path = sys.argv[1]
ini = open(path).read()
# Our own section, rebuilt from scratch on every run.
ini = re.sub(r"\n\[x11vnc\][^\[]*", "\n", ini)
def setting(text, key, value):
    pattern = re.compile(rf"^{key}=.*$", re.M)
    return (pattern.sub(f"{key}={value}", text, count=1) if pattern.search(text)
            else text.replace("[Globals]", f"[Globals]\n{key}={value}", 1))
ini = setting(ini, "port", "3389")
ini = setting(ini, "autorun", "x11vnc")
ini = ini.rstrip("\n") + """

[x11vnc]
name=x11vnc
lib=libvnc.so
ip=127.0.0.1
port=5900
username=na
password=ask
"""
open(path, "w").write(ini)
EOF

  if [ "$DESKTOP" != gnome ]; then
    # An x11vnc somebody started by hand holds the port the service wants. It
    # is the same program reading the same password file, so take it over
    # rather than leave the service restarting against a port it can never
    # have.
    systemctl stop celephais-x11vnc.service 2>/dev/null || true
    pkill -u "$USER_NAME" -x x11vnc 2>/dev/null || true
    systemctl enable --now celephais-x11vnc.service
    systemctl enable --now xrdp
    systemctl restart xrdp
    sleep 2
    systemctl is-active celephais-x11vnc.service xrdp.service
  else
    systemctl disable --now celephais-x11vnc.service 2>/dev/null || true
    systemctl disable --now xrdp 2>/dev/null || true
    echo "x11vnc and xrdp are set up and idle; the Gate can switch to them"
  fi

  # Xfce has its own lock screen, and a locked Realm with no screen to unlock
  # it on is a Realm nobody can get into. light-locker is the one Ubuntu's
  # Xfce installs, and it is started by the session rather than by systemd, so
  # it is stopped where it is and told not to come back.
  as_user xfconf-query -c xfce4-screensaver -p /saver/enabled -n -t bool -s false 2>/dev/null || true
  as_user xfconf-query -c xfce4-screensaver -p /lock/enabled -n -t bool -s false 2>/dev/null || true
  as_user xfconf-query -c xfce4-power-manager \
    -p /xfce4-power-manager/dpms-enabled -n -t bool -s false 2>/dev/null || true
  for setting in blank-on-ac blank-on-battery dpms-on-ac-sleep dpms-on-ac-off \
                 inactivity-on-ac inactivity-on-battery; do
    as_user xfconf-query -c xfce4-power-manager \
      -p "/xfce4-power-manager/$setting" -n -t int -s 0 2>/dev/null || true
  done
  if command -v light-locker >/dev/null; then
    pkill -u "$USER_NAME" -x light-locker || true
    install -d -o "$USER_NAME" -g "$GID_NUM" -m 755 "$HOME_DIR/.config/autostart"
    cat > "$HOME_DIR/.config/autostart/light-locker.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Screen Locker
Exec=/usr/bin/light-locker
Hidden=true
X-GNOME-Autostart-enabled=false
EOF
    chown "$USER_NAME:$GID_NUM" "$HOME_DIR/.config/autostart/light-locker.desktop"
    echo "light-locker will not lock this Realm out of reach"
  fi
  loginctl unlock-sessions || true
fi

# A desktop for an agent, beside the Realm's own and sharing nothing with it
# but the user it runs as. Three services: an X server with Xfce on it that no
# display manager owns, x11vnc serving that display on a port of its own, and
# an xrdp of its own in front of that. The Realm's own desktop keeps 3389 and
# 5900 and goes on being switched between GNOME and Xfce as before; none of
# what follows is touched when that happens, which is the point of it.
if [ "$AGENT" = yes ]; then
  AGENT_AUTH=/run/celephais-agent/Xauthority
  install -m 755 "$SRC/celephais-agent-desktop" /usr/local/bin/celephais-agent-desktop
  restorecon /usr/local/bin/celephais-agent-desktop 2>/dev/null || true
  install -m 755 "$SRC/celephais-x11vnc" /usr/local/bin/celephais-x11vnc
  restorecon /usr/local/bin/celephais-x11vnc 2>/dev/null || true

  cat > /etc/systemd/system/celephais-agent-desktop.service <<EOF
[Unit]
Description=Celephais: a desktop of its own for an agent to work in
After=systemd-user-sessions.service

[Service]
User=$USER_NAME
RuntimeDirectory=celephais-agent
RuntimeDirectoryMode=0700
ExecStart=/usr/local/bin/celephais-agent-desktop --display $AGENT_DISPLAY \
  --screen $AGENT_SCREEN --auth $AGENT_AUTH
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  # Requires rather than Wants: serving a display that is not there is not a
  # degraded service, it is nothing at all. It needs no ordering cleverness
  # beyond that - when the desktop restarts, its X server goes with it, x11vnc
  # loses the server it was attached to and exits, and the one systemd starts
  # in its place waits for the new display and reads the new cookie.
  cat > /etc/systemd/system/celephais-x11vnc-agent.service <<EOF
[Unit]
Description=Celephais: serve the agent's desktop over VNC on the loopback
Requires=celephais-agent-desktop.service
After=celephais-agent-desktop.service

[Service]
User=$USER_NAME
ExecStart=/usr/local/bin/celephais-x11vnc --display $AGENT_DISPLAY \
  --auth $AGENT_AUTH --password-file $VNC_PASSWD --rfbport $AGENT_VNC_PORT
Restart=always
RestartSec=5
SuccessExitStatus=2

[Install]
WantedBy=multi-user.target
EOF

  # A second xrdp rather than a second section in the first one: the Realm's
  # own xrdp is started and stopped as the Realm's desktop is switched, and
  # the agent's desktop has no business going dark because somebody chose
  # GNOME. --config is xrdp's own answer to this, and the agent's copy
  # differs only in its port, its log and which VNC server it fronts.
  python3 - /etc/xrdp/xrdp.ini /etc/xrdp/xrdp-agent.ini \
           "$AGENT_RDP_PORT" "$AGENT_VNC_PORT" <<'EOF'
import re, sys
source, path, rdp_port, vnc_port = sys.argv[1:5]
ini = open(source).read()
# Neither our section for the Realm's own desktop nor xrdp's session modules
# belong in this copy: autorun means only one of them can ever be reached,
# and leaving the others listed invites someone to reach for them.
ini = re.sub(r"\n\[x11vnc\][^\[]*", "\n", ini)
def setting(text, key, value):
    pattern = re.compile(rf"^{key}=.*$", re.M)
    return (pattern.sub(f"{key}={value}", text, count=1) if pattern.search(text)
            else text.replace("[Globals]", f"[Globals]\n{key}={value}", 1))
ini = setting(ini, "port", rdp_port)
ini = setting(ini, "autorun", "agent")
ini = setting(ini, "LogFile", "xrdp-agent.log")
ini = ini.rstrip("\n") + f"""

[agent]
name=agent
lib=libvnc.so
ip=127.0.0.1
port={vnc_port}
username=na
password=ask
"""
open(path, "w").write(ini)
EOF
  chmod 644 /etc/xrdp/xrdp-agent.ini

  cat > /etc/systemd/system/celephais-xrdp-agent.service <<EOF
[Unit]
Description=Celephais: RDP in front of the agent's desktop
Requires=celephais-x11vnc-agent.service
After=celephais-x11vnc-agent.service

[Service]
ExecStart=/usr/sbin/xrdp --nodaemon --config /etc/xrdp/xrdp-agent.ini
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable --now celephais-agent-desktop.service
  systemctl enable --now celephais-x11vnc-agent.service
  systemctl enable --now celephais-xrdp-agent.service
  sleep 3
  systemctl is-active celephais-agent-desktop.service \
                      celephais-x11vnc-agent.service \
                      celephais-xrdp-agent.service
  echo "the agent's desktop is on $AGENT_DISPLAY at $AGENT_SCREEN," \
       "RDP on $AGENT_RDP_PORT"
  echo "an agent on this Realm works in it with DISPLAY=$AGENT_DISPLAY" \
       "XAUTHORITY=$AGENT_AUTH"
fi

# What the Gate asks when it offers the choice, and what carries it out. It
# needs no setting up of its own: everything it switches between was put in
# place above.
install -m 755 "$SRC/celephais-desktop" /usr/local/bin/celephais-desktop
restorecon /usr/local/bin/celephais-desktop 2>/dev/null || true

# Nor may a Realm put itself to sleep. Fedora's GNOME suspends after a while
# idle, and a suspended guest is "running" to Proxmox with its agent and its
# remote desktop frozen: `personal` did exactly that on the monitors, read as
# BOOTING, and had to be stopped in Proxmox. Masked rather than only switched
# off in the desktop, so a Suspend in the menu cannot do it either.
# Hibernation is left alone: it is the one guest-side way to keep a session
# across a seat change.
systemctl mask --quiet suspend.target hybrid-sleep.target suspend-then-hibernate.target

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
if [ "$DESKTOP" = xfce ]; then
  echo
  echo "This Realm has one screen, $SCREEN, and it is set here rather than by"
  echo "whoever connects: x11vnc follows the X screen and refuses to be resized"
  echo "from the other end, so xrdp sizes the client to the Realm instead."
  echo "/multimon therefore spans nothing, and the desktop arrives at $SCREEN on"
  echo "the monitor FreeRDP opens on. The clipboard crosses as text; sound does"
  echo "not cross at all. --screen sets a different size."
fi
