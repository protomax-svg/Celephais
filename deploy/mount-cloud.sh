#!/usr/bin/env bash
# Give one Realm (or the laptop) the shared disk as an ordinary folder.
# Run inside that machine, as root.
#
#   bash mount-cloud.sh <server-address> <username> <password> [desktop-user]
#
# The folder appears at /mnt/cloud and in the desktop user's home as "Cloud".
# It is mounted on first use rather than at boot, so a Realm still starts when
# the container that serves it happens to be down.
#
# Do not run this inside the Windows Realm. That machine is the one this disk
# is kept away from.
set -euo pipefail

SERVER=${1:-}
USERNAME=${2:-}
PASSWORD=${3:-}
OWNER=${4:-$(logname 2>/dev/null || echo "")}
[ -n "$SERVER" ] && [ -n "$USERNAME" ] && [ -n "$PASSWORD" ] || {
  echo "usage: mount-cloud.sh <server-address> <username> <password> [desktop-user]" >&2
  exit 1; }
[ "$(id -u)" = 0 ] || { echo "run this as root" >&2; exit 1; }

MOUNT=${MOUNT:-/mnt/cloud}
CRED=/etc/celephais-cloud.cred

export DEBIAN_FRONTEND=noninteractive
command -v mount.cifs >/dev/null || { apt-get update -qq
                                      apt-get install -y -qq cifs-utils >/dev/null; }

# The password goes in a file only root can read. On the mount line it would
# be visible in /proc/mounts to every user on the machine.
umask 077
cat > "$CRED" <<EOF
username=$USERNAME
password=$PASSWORD
EOF
chmod 600 "$CRED"
umask 022

# Files should belong to whoever is sitting at the desktop, not to root.
UID_NUM=0; GID_NUM=0
if [ -n "$OWNER" ] && id "$OWNER" >/dev/null 2>&1; then
  UID_NUM=$(id -u "$OWNER"); GID_NUM=$(id -g "$OWNER")
fi

install -d -m 755 "$MOUNT"
LINE="//$SERVER/cloud $MOUNT cifs credentials=$CRED,uid=$UID_NUM,gid=$GID_NUM,file_mode=0644,dir_mode=0755,iocharset=utf8,vers=3.1.1,nofail,_netdev,x-systemd.automount,x-systemd.idle-timeout=600 0 0"
sed -i "\| $MOUNT cifs |d" /etc/fstab
echo "$LINE" >> /etc/fstab

systemctl daemon-reload
systemctl restart remote-fs.target 2>/dev/null || true
mount "$MOUNT" 2>/dev/null || true

# A path under /mnt is not where anyone looks for their own files.
if [ -n "$OWNER" ] && id "$OWNER" >/dev/null 2>&1; then
  HOME_DIR=$(getent passwd "$OWNER" | cut -d: -f6)
  [ -e "$HOME_DIR/Cloud" ] || ln -s "$MOUNT" "$HOME_DIR/Cloud"
  chown -h "$OWNER:$OWNER" "$HOME_DIR/Cloud" 2>/dev/null || true
fi

if mountpoint -q "$MOUNT"; then
  echo "mounted: $MOUNT  ($(df -h --output=avail "$MOUNT" | tail -1 | tr -d ' ') free)"
else
  echo "not mounted yet. It will mount on first use; try: ls $MOUNT" >&2
fi
