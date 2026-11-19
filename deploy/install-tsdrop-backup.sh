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
#                                             switched on in the app; its own
#                                             restic repo, which the app can
#                                             read back through a folder link
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

for f in celephais-tsdrop-mirror celephais-tsdrop-offsite celephais-mega-sharekeys celephais-notify celephais-backup-cloud celephais-backup-report; do
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

# TSDROP's own MEGA repository, separate from the cloud share's: the app reads it back
# when no server answers, so the key it carries must open TSDROP's data and nothing else.
# A read-only folder link is how the app gets in; the hourly job hands link and password
# to the app through /.offsite.json in TSDROP's root.
PASS=/etc/celephais/tsdrop-restic.pass
LINK=/etc/celephais/tsdrop-restic.link
[ -s "$PASS" ] || (umask 077; head -c 32 /dev/urandom | base64 > "$PASS")
(
  export RESTIC_REPOSITORY=rclone:mega:tsdrop-restic RESTIC_PASSWORD_FILE=$PASS XDG_CACHE_HOME=/var/cache/celephais
  restic cat config >/dev/null 2>&1 || restic init
)
# The app needs a folder link that carries its key. rclone's may not be one (its MEGA
# library makes file-style links); then it is a minute by hand on mega.nz. Backups run
# either way - only the app's way back in waits for the link.
if [ ! -s "$LINK" ]; then
  link=$(rclone link mega:tsdrop-restic 2>/dev/null | tail -n1 || true)
  case "$link" in
    https://mega.nz/folder/*#*|https://mega.nz/#F!*!*|https://mega.co.nz/#F!*!*) (umask 077; printf '%s\n' "$link" > "$LINK") ;;
    *) cat >&2 <<MSG
NO FOLDER LINK YET (rclone gave: ${link:-nothing}). Make one by hand: sign in to the
backup's MEGA account on mega.nz, right-click the folder tsdrop-restic, Share > Copy link
(the full link, with the key after #), then on this host:
  printf '%s\\n' 'https://mega.nz/folder/XXXX#YYYY' > $LINK; chmod 600 $LINK
The next hourly run hands it to the app.
MSG
    ;;
  esac
fi

systemctl daemon-reload
systemctl enable --now celephais-tsdrop-mirror.timer celephais-tsdrop-offsite.timer
# first copy now, not at the next hour; it also writes /.offsite.json for the app
systemctl start --no-block celephais-tsdrop-offsite.service
systemctl list-timers 'celephais-tsdrop-*' --no-pager | head -4
cat <<'NOTE'

TSDROP's MEGA copy now lives in mega:tsdrop-restic, with its own password in
/etc/celephais/tsdrop-restic.pass. Keep a copy of that password and of
/etc/celephais/tsdrop-restic.link outside this host (a password manager): every
device that has opened TSDROP since also holds them, but that is the only other copy.

Older TSDROP snapshots stay in the cloud share's repository (tag tsdrop) until removed.
Once the new copy shows up in the app, they can go:
  export RCLONE_CONFIG=/etc/celephais/rclone.conf RESTIC_REPOSITORY=rclone:mega:celephais-restic \
         RESTIC_PASSWORD_FILE=/etc/celephais/restic-offsite.pass XDG_CACHE_HOME=/var/cache/celephais
  restic forget --prune $(restic snapshots --tag tsdrop --json | python3 -c 'import json,sys; print(*(s["id"] for s in json.load(sys.stdin) or []))')
NOTE
