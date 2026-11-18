#!/usr/bin/env bash
# Set up backups on the Proxmox host. Run as root, there.
#
#   bash install-backup.sh
#
# Two halves, because the thing being protected comes in two kinds.
#
# The guests are block devices, so they go through vzdump: a nightly job that
# takes an LVM-thin snapshot, writes a compressed archive and lets the Realms
# keep running while it does. win11-test is left out on purpose - its second
# disk is an 800 GB preallocated raw file, and vzdump would have to read all
# 800 GB of mostly-zeros every night, competing with the Realms for the same
# spindles, to protect a machine nobody keeps anything in.
#
# The cloud data is files, and it is not in any guest's volume: it lives on
# the host at /mnt/hdd/cloud and is bind-mounted into CT 210. vzdump says so
# itself - "excluding bind mount point mp0 (not a volume)" - so it needs its
# own job, which is celephais-backup-cloud beside this script.
#
# The destination is an external USB disk that already had 314 GB of
# unrelated data on it when this was written. Nothing here formats it or
# touches anything outside /mnt/backup/celephais. It is exfat, which cannot
# store Unix ownership or permissions; that is why both halves write into
# container formats (vzdump archives, a restic repository) rather than
# copying files out in the open.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "run this as root: sudo bash $0" >&2; exit 1; }
command -v pvesm >/dev/null || { echo "this is for the Proxmox host" >&2; exit 1; }

MOUNT=${MOUNT:-/mnt/backup}
ROOT=$MOUNT/celephais
STORAGE=${STORAGE:-backup-hdd}
RETENTION=${RETENTION:-keep-daily=7,keep-weekly=4}
GUESTS=${GUESTS:-201,202,203,210,211}
SRC=$(cd "$(dirname "$0")/.." && pwd)

mountpoint -q "$MOUNT" || {
  echo "$MOUNT is not mounted. Add the disk to /etc/fstab with nofail and" >&2
  echo "mount it before running this, or backups would land on pve-root." >&2
  exit 1; }

install -d -m 755 "$ROOT"

# --- the guests -------------------------------------------------------------
# is_mountpoint is the important flag: without it, a night when the USB disk
# is unplugged would write a full set of backups onto the root filesystem.
if ! pvesm status --storage "$STORAGE" >/dev/null 2>&1; then
  pvesm add dir "$STORAGE" --path "$ROOT" --content backup \
    --is_mountpoint "$MOUNT" --prune-backups "$RETENTION"
  echo "added storage $STORAGE"
else
  pvesm set "$STORAGE" --prune-backups "$RETENTION"
  echo "storage $STORAGE already present"
fi

if ! grep -q "^vzdump: celephais-nightly" /etc/pve/jobs.cfg 2>/dev/null; then
  pvesh create /cluster/backup \
    --id celephais-nightly \
    --schedule "02:30" \
    --storage "$STORAGE" \
    --vmid "$GUESTS" \
    --mode snapshot \
    --compress zstd \
    --prune-backups "$RETENTION" \
    --notes-template '{{guestname}}' \
    --enabled 1
  echo "created the nightly vzdump job"
else
  pvesh set /cluster/backup/celephais-nightly --vmid "$GUESTS"
  echo "vzdump job already present; guests set to $GUESTS"
fi

# --- the cloud data ---------------------------------------------------------
command -v restic >/dev/null || {
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq restic; }

install -d -m 700 /etc/celephais
PASSFILE=/etc/celephais/restic-cloud.pass
if [ ! -s "$PASSFILE" ]; then
  umask 077
  head -c 32 /dev/urandom | base64 > "$PASSFILE"
  chmod 600 "$PASSFILE"
  echo
  echo "A new repository key was written to $PASSFILE."
  echo "Copy it somewhere off this machine. It lives on pve-root, not on the"
  echo "backup disk, so losing the host loses the key and the backups with it."
  echo
fi

export RESTIC_REPOSITORY=$ROOT/restic-cloud RESTIC_PASSWORD_FILE=$PASSFILE
restic cat config >/dev/null 2>&1 || restic init

install -m 755 "$SRC/bin/celephais-backup-cloud" /usr/local/bin/celephais-backup-cloud

cat > /etc/systemd/system/celephais-backup-cloud.service <<'UNIT'
[Unit]
Description=Back up the cloud share to the external disk
# The disk is removable, so the unit asks for the mount rather than assuming
# it. The script checks again anyway; this only keeps the journal honest
# about why a run did not happen.
RequiresMountsFor=/mnt/backup /mnt/hdd

[Service]
Type=oneshot
ExecStart=/usr/local/bin/celephais-backup-cloud
# systemd gives the unit no $HOME, so without this restic runs with no cache
# at all ("unable to locate cache directory") and re-reads the repository
# index off the USB disk every night.
Environment=XDG_CACHE_HOME=/var/cache/celephais
# It reads 25 GB of somebody's working files every night, and it is never
# more urgent than the Realms somebody is actually sitting in front of.
Nice=10
IOSchedulingClass=idle
UNIT

cat > /etc/systemd/system/celephais-backup-cloud.timer <<'UNIT'
[Unit]
Description=Nightly cloud share backup

[Timer]
# An hour after the vzdump job, so the two are not reading the same disks at
# once. Persistent catches up a run the host slept through rather than
# silently skipping a day.
OnCalendar=*-*-* 03:30:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now celephais-backup-cloud.timer

echo
echo "guests  : $GUESTS nightly at 02:30 -> $STORAGE ($RETENTION)"
echo "cloud   : /mnt/hdd/cloud nightly at 03:30 -> $ROOT/restic-cloud"
systemctl list-timers celephais-backup-cloud.timer --no-pager 2>/dev/null | head -3
