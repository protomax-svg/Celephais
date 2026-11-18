#!/usr/bin/env bash
# The copies of TSDROP's data. Run as root on the Proxmox host, after
# install-tsdrop.sh, install-alerts.sh and install-offsite.sh. Idempotent.
#
#   bash install-tsdrop-backup.sh
#
#   every 5 min  celephais-tsdrop-mirror   -> the laptop's fallback server,
#                                             everything; new things made there
#                                             while Proxmox was away come back
#   hourly       celephais-tsdrop-offsite  -> MEGA, encrypted, only what is
#                                             switched on in the app
#   nightly      celephais-backup-cloud    -> USB disk, everything (tag tsdrop)
#
# Needs, on the laptop: ~/tsdrop-mirror from deploy/tsdrop-mirror running,
# and this host's /etc/celephais/tsdrop-mirror.key.pub in protomax's
# authorized_keys as
#   restrict,command="/usr/lib/openssh/sftp-server" ssh-ed25519 AAAA...
# and a `laptop` sftp remote in /etc/celephais/rclone.conf
# (shell_type none, disable_hashcheck true, known_hosts_file pinned).
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "run this as root" >&2; exit 1; }
SRC=$(cd "$(dirname "$0")/.." && pwd)
export RCLONE_CONFIG=/etc/celephais/rclone.conf

rclone listremotes | grep -qx 'laptop:' || { echo "no laptop: remote in $RCLONE_CONFIG" >&2; exit 1; }
rclone listremotes | grep -qx 'mega:' || { echo "no mega: remote; run install-offsite.sh first" >&2; exit 1; }

for f in celephais-tsdrop-mirror celephais-tsdrop-offsite celephais-notify celephais-backup-cloud celephais-backup-report; do
  install -m 755 "$SRC/bin/$f" "/usr/local/bin/$f"
done

cat > /etc/systemd/system/celephais-tsdrop-mirror.service <<'UNIT'
[Unit]
Description=Mirror TSDROP to the laptop's fallback server
RequiresMountsFor=/mnt/hdd
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/celephais-tsdrop-mirror
Nice=10
IOSchedulingClass=idle
TimeoutStartSec=2h
UNIT

cat > /etc/systemd/system/celephais-tsdrop-mirror.timer <<'UNIT'
[Unit]
Description=Mirror TSDROP to the laptop every 5 minutes

[Timer]
OnCalendar=*:0/5
# A run that is still going (a big file on a slow link) simply makes the next
# tick a no-op; systemd does not start a oneshot twice.
AccuracySec=30s

[Install]
WantedBy=timers.target
UNIT

cat > /etc/systemd/system/celephais-tsdrop-offsite.service <<'UNIT'
[Unit]
Description=Back up the switched-on parts of TSDROP to MEGA (encrypted)
RequiresMountsFor=/mnt/hdd
After=network-online.target
Wants=network-online.target
OnFailure=celephais-failed@%n.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/celephais-tsdrop-offsite
Nice=10
IOSchedulingClass=idle
MemoryHigh=2G
TimeoutStartSec=6h
UNIT

cat > /etc/systemd/system/celephais-tsdrop-offsite.timer <<'UNIT'
[Unit]
Description=Hourly TSDROP backup to MEGA

[Timer]
OnCalendar=hourly
RandomizedDelaySec=300
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now celephais-tsdrop-mirror.timer celephais-tsdrop-offsite.timer
systemctl list-timers 'celephais-tsdrop-*' --no-pager | head -4
