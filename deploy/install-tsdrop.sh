#!/usr/bin/env bash
# TSDROP's server: a small container that speaks WebDAV and nothing else.
# Run on the Proxmox host, as root.
#
#   bash install-tsdrop.sh
#   bash install-tsdrop.sh --remove
#
# It reaches the tailnet through the host, not a Tailscale of its own: the
# host's `tailscale serve` forwards pve.<tailnet>.ts.net:8090 to the
# container's 8080. Not 8080 on the host - Celephais Core already listens on
# the host's tailnet address there, and a serve on that port would quietly
# take the Gate's traffic away from it (it did, for a minute, 2026-11-18).
#
# TSDROP used to keep everything in a Nextcloud on the laptop. It never used
# Nextcloud for anything but WebDAV - PROPFIND, MKCOL, PUT, GET, MOVE, DELETE
# - so the PHP, the database and the Redis in between were weight it paid for
# on every request ("~35ms per webdav request (it is PHP)", says its own
# source). Apache's mod_dav serves the same verbs straight off the disk.
#
# The URL path is kept exactly as Nextcloud spelled it,
#   /remote.php/dav/files/transferapp/file-transfer-app
# so a device only needs the new host name, not a new path.
#
# The data lives on the host at /mnt/hdd/tsdrop/data and is only lent to the
# container: wiping the container never touches it, and the backups read it
# from the host side (see install-tsdrop-sync.sh and celephais-tsdrop-backup).
#
# The password for the `transferapp` account is set by hand afterwards, so it
# is never generated, printed or stored in clear by this script:
#   pct exec 211 -- htpasswd /etc/apache2/tsdrop.htpasswd transferapp
set -euo pipefail
export LC_ALL=C LANG=C

CTID=${CTID:-211}
CT_IP=${CT_IP:-192.168.2.121/24}
CT_GW=${CT_GW:-192.168.2.1}
BRIDGE=${BRIDGE:-vmbr0}
NAMESERVER=${NAMESERVER:-${CT_GW%%/*} 1.1.1.1}
TEMPLATE=${TEMPLATE:-local:vztmpl/ubuntu-26.04-standard_26.04-1_amd64.tar.zst}
ROOTFS=${ROOTFS:-realms-ssd:4}
DATA=${DATA:-/mnt/hdd/tsdrop/data}
# www-data is uid 33 inside; an unprivileged container shifts it by 100000.
WWW_UID=${WWW_UID:-33}
UID_OFFSET=${UID_OFFSET:-100000}
HERE=$(cd "$(dirname "$0")" && pwd)

die() { echo "install-tsdrop: $*" >&2; exit 1; }

if [ "${1:-}" = "--remove" ]; then
  pct stop "$CTID" 2>/dev/null || true
  pct destroy "$CTID" 2>/dev/null || true
  echo "container $CTID is gone; $DATA was left alone"
  exit 0
fi
TS_PORT=${TS_PORT:-8090}

command -v pct >/dev/null || die "this runs on the Proxmox host"
mountpoint -q /mnt/hdd || die "/mnt/hdd is not mounted"
pct status "$CTID" >/dev/null 2>&1 && die "container $CTID already exists; --remove it first"

install -d -m 750 "$DATA"
chown "$((UID_OFFSET + WWW_UID)):$((UID_OFFSET + WWW_UID))" "$DATA" "$(dirname "$DATA")"

echo "creating container $CTID…"
pct create "$CTID" "$TEMPLATE" \
  --hostname tsdrop \
  --cores 2 --memory 512 --swap 256 \
  --rootfs "$ROOTFS" \
  --net0 "name=eth0,bridge=$BRIDGE,ip=$CT_IP,gw=$CT_GW,firewall=1" \
  --nameserver "$NAMESERVER" \
  --mp0 "$DATA,mp=/srv/tsdrop/data" \
  --unprivileged 1 --features nesting=1 \
  --onboot 1 --start 0 >/dev/null

pct start "$CTID"
for _ in $(seq 60); do
  pct exec "$CTID" -- test -e /run/systemd/system 2>/dev/null && break
  sleep 1
done
pct exec "$CTID" -- test -e /run/systemd/system || die "container $CTID never finished booting"

echo "setting it up inside…"
pct push "$CTID" "$HERE/tsdrop-setup.sh" /root/tsdrop-setup.sh --perms 755
pct exec "$CTID" -- /root/tsdrop-setup.sh
pct exec "$CTID" -- rm -f /root/tsdrop-setup.sh

if ss -ltn | grep -q ":$TS_PORT "; then
  die "port $TS_PORT is already in use on the host; pick another with TS_PORT="
fi
tailscale serve --bg --tcp "$TS_PORT" "tcp://${CT_IP%%/*}:8080" >/dev/null

cat <<EOF

TSDROP's WebDAV server is up.

  data on the host : $DATA
  container        : $CTID (tsdrop) at ${CT_IP%%/*}
  address          : http://pve.<tailnet>.ts.net:$TS_PORT/remote.php/dav/files/transferapp/file-transfer-app

Set the password for the account the app uses:
  pct exec $CTID -- htpasswd /etc/apache2/tsdrop.htpasswd transferapp
EOF
