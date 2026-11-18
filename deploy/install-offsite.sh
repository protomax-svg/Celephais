#!/usr/bin/env bash
# Offsite copy of the cloud share to MEGA. Run as root on the Proxmox host,
# after install-backup.sh and install-alerts.sh. Idempotent.
#
#   bash install-offsite.sh
#
# Needs, before it will do anything:
#   - rclone from rclone.org in /usr/local/bin. Debian's package (+dfsg) is
#     built without the MEGA backend.
#   - a `mega` remote in /etc/celephais/rclone.conf. Create it so the password
#     never touches a command line or a chat:
#       read -rp "MEGA email: " u; read -rsp "MEGA password: " p; echo
#       o=$(printf %s "$p" | rclone obscure -)
#       RCLONE_CONFIG=/etc/celephais/rclone.conf \
#         rclone config create mega mega user "$u" pass "$o" --no-obscure
#
# Makes its own repository key at /etc/celephais/restic-offsite.pass. Like the
# USB one it lives only on pve-root: copy it into a password manager, or the
# offsite copy is unreadable the day it is needed.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "run this as root" >&2; exit 1; }
SRC=$(cd "$(dirname "$0")/.." && pwd)
export RCLONE_CONFIG=/etc/celephais/rclone.conf

rclone help backends 2>/dev/null | grep -qw mega \
  || { echo "this rclone has no MEGA backend; install the rclone.org build" >&2; exit 1; }
rclone listremotes | grep -qx 'mega:' \
  || { echo "no mega: remote in $RCLONE_CONFIG (see the top of this script)" >&2; exit 1; }
chmod 600 "$RCLONE_CONFIG"

PASSFILE=/etc/celephais/restic-offsite.pass
if [ ! -s "$PASSFILE" ]; then
  (umask 077; head -c 32 /dev/urandom | base64 > "$PASSFILE")
  echo "new offsite repository key in $PASSFILE - copy it off this machine"
fi
chmod 600 "$PASSFILE"

install -m 755 "$SRC/bin/celephais-backup-offsite" /usr/local/bin/celephais-backup-offsite

cat > /etc/systemd/system/celephais-backup-offsite.service <<'UNIT'
[Unit]
Description=Back up the cloud share to MEGA (encrypted)
# Only the source disk. Not the USB backup disk: this copy is meant to keep
# going on the nights that one is missing.
RequiresMountsFor=/mnt/hdd
After=network-online.target
Wants=network-online.target
OnFailure=celephais-failed@%n.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/celephais-backup-offsite
Environment=XDG_CACHE_HOME=/var/cache/celephais
Nice=10
IOSchedulingClass=idle
# The first run peaked at 10.5 GB, most of it page cache from reading 16 GB
# of files, all charged to this unit. A soft ceiling: past it the kernel
# reclaims this unit's cache before it touches the Realms'. Never a kill.
MemoryHigh=4G
# The first upload is several GB over a home uplink; the nightly ones are
# minutes. Generous, but not unbounded.
TimeoutStartSec=12h
UNIT

cat > /etc/systemd/system/celephais-backup-offsite.timer <<'UNIT'
[Unit]
Description=Nightly offsite backup of the cloud share

[Timer]
# After the USB job at 03:30, before the report at 05:00.
OnCalendar=*-*-* 04:00:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable celephais-backup-offsite.timer
systemctl start celephais-backup-offsite.timer
echo "offsite: /mnt/hdd/cloud nightly at 04:00 -> MEGA (rclone:mega:celephais-restic)"
