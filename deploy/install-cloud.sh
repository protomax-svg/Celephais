#!/usr/bin/env bash
# The shared disk every Realm can reach. Run on the Proxmox host, as root.
#
#   bash install-cloud.sh <tailscale-auth-key>
#   bash install-cloud.sh --remove
#
# What is left of the 2 TB drive once Windows has taken its 800 GB becomes one
# folder, served two ways out of one small container:
#
#   * a network folder the Realms and the laptop mount, over SMB
#   * a web page and a WebDAV address for a phone, over copyparty
#
# Both doors look at the same files. Windows is the one machine kept out, and
# it is kept out by not being given the password: an address list would be a
# lie the moment the router hands out a different one.
#
# The container is unprivileged. The folder lives on the host and is only
# lent to it, so wiping the container never touches the data.
set -euo pipefail

CTID=${CTID:-210}
CT_IP=${CT_IP:-192.168.2.120/24}
CT_GW=${CT_GW:-192.168.2.1}
BRIDGE=${BRIDGE:-vmbr0}
TEMPLATE=${TEMPLATE:-local:vztmpl/ubuntu-26.04-standard_26.04-1_amd64.tar.zst}
ROOTFS=${ROOTFS:-realms-ssd:8}
SHARE=${SHARE:-/mnt/hdd/cloud}
MEMORY=${MEMORY:-1024}
CORES=${CORES:-2}
HERE=$(cd "$(dirname "$0")" && pwd)

die() { echo "install-cloud: $*" >&2; exit 1; }

if [ "${1:-}" = "--remove" ]; then
  pct stop "$CTID" 2>/dev/null || true
  pct destroy "$CTID" 2>/dev/null || true
  echo "container $CTID is gone; $SHARE and everything in it was left alone"
  exit 0
fi

AUTHKEY=${1:-}
[ -n "$AUTHKEY" ] || die "usage: install-cloud.sh <tailscale-auth-key>
Make one at https://login.tailscale.com/admin/settings/keys
An OAuth client secret (tskey-client-...) works too; it needs TS_TAGS set."

command -v pct >/dev/null || die "this runs on the Proxmox host"
[ -d "$(dirname "$SHARE")" ] || die "$(dirname "$SHARE") is not there; is the 2 TB drive mounted?"
pct status "$CTID" >/dev/null 2>&1 && die "container $CTID already exists; --remove it first"

# One password for both doors. Printed once at the end and nowhere else: this
# script never writes it to disk on the host.
PASSWORD=$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 20)

# The folder is made before the container, because the container only borrows
# it. Ownership is set from inside later, where the user ids are the ones that
# matter.
install -d -m 755 "$SHARE"

echo "creating container $CTID…"
pct create "$CTID" "$TEMPLATE" \
  --hostname celephais-cloud \
  --cores "$CORES" --memory "$MEMORY" --swap 512 \
  --rootfs "$ROOTFS" \
  --net0 "name=eth0,bridge=$BRIDGE,ip=$CT_IP,gw=$CT_GW,firewall=1" \
  --mp0 "$SHARE,mp=/srv/cloud" \
  --unprivileged 1 --features nesting=1 \
  --onboot 1 --start 0 >/dev/null

# Tailscale needs the tunnel device, which an unprivileged container is not
# given by default.
CONF=/etc/pve/lxc/$CTID.conf
grep -q "10:200" "$CONF" || cat >> "$CONF" <<'EOF'
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
EOF

pct start "$CTID"
for _ in $(seq 60); do
  pct exec "$CTID" -- test -e /run/systemd/system 2>/dev/null && break
  sleep 1
done
pct exec "$CTID" -- test -e /run/systemd/system || die "container $CTID never finished booting"

echo "setting it up inside…"
pct push "$CTID" "$HERE/cloud-setup.sh" /root/cloud-setup.sh --perms 755
pct exec "$CTID" -- /root/cloud-setup.sh "$PASSWORD" "$AUTHKEY" "${TS_TAGS:-}"
pct exec "$CTID" -- rm -f /root/cloud-setup.sh

ADDR=${CT_IP%%/*}
cat <<EOF

the shared disk is up.

  folder on the host : $SHARE
  container          : $CTID (celephais-cloud) at $ADDR
  username           : cloud
  password           : $PASSWORD

  from a Realm or the laptop : bash deploy/mount-cloud.sh $ADDR cloud $PASSWORD
  from a phone or a browser  : http://$ADDR:3923/
  as a WebDAV address        : http://$ADDR:3923/

Write the password down now. It is not kept anywhere on the host, and Windows
is never to be given it.
EOF
