#!/usr/bin/env bash
# Install Celephais Core as a service on the Proxmox host. Run as root, on that host.
#
#   bash install-core.sh                 # reads ../.env  (preferred: no secrets in argv)
#   bash install-core.sh 'PVEAPIToken=gate@pve!core=<uuid>' '<core-token>'
#
# Core listens only on the host's Tailscale address, so the Gate reaches it from
# anywhere on the tailnet and nothing on the LAN can see it.
set -euo pipefail

SRC=$(cd "$(dirname "$0")/.." && pwd)

# Arguments win; otherwise take the tokens from .env, which keeps them out of
# the command line and the shell history.
PVE_TOKEN=${1:-${PVE_TOKEN:-}}
CORE_TOKEN=${2:-${CORE_TOKEN:-}}
if [ -z "$PVE_TOKEN" ] || [ -z "$CORE_TOKEN" ]; then
  [ -f "$SRC/.env" ] || { echo "no tokens given and no $SRC/.env found" >&2; exit 1; }
  set -a; . "$SRC/.env"; set +a
fi
[ -n "${PVE_TOKEN:-}" ]  || { echo "PVE_TOKEN is empty" >&2; exit 1; }
[ -n "${CORE_TOKEN:-}" ] || { echo "CORE_TOKEN is empty" >&2; exit 1; }

command -v tailscale >/dev/null || {
  echo "Tailscale is missing. Install it first:" >&2
  echo "  curl -fsSL https://tailscale.com/install.sh | sh && tailscale up" >&2
  exit 1
}
TS_IP=$(tailscale ip -4 | head -1)
[ -n "$TS_IP" ] || { echo "Tailscale has no IPv4 address yet. Run: tailscale up" >&2; exit 1; }

id celephais >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin celephais

install -d -m 755 /opt/celephais
install -m 755 "$SRC/bin/celephais_core.py" /opt/celephais/celephais_core.py
install -m 755 "$SRC/bin/celephais-seat" /usr/local/bin/celephais-seat

# Proxmox lets only root change PCI passthrough, so Core calls the switcher
# through sudo. One command, no password, nothing else. A minimal Proxmox
# install has no sudo package at all.
if ! command -v sudo >/dev/null; then
  echo "installing sudo..."
  apt-get update -qq && apt-get install -y -qq sudo
fi
command -v sudo >/dev/null || {
  echo "sudo is still missing. Install it, then run this script again:" >&2
  echo "  apt-get install sudo" >&2
  exit 1
}

install -d -m 755 /etc/sudoers.d
cat > /etc/sudoers.d/celephais <<'EOF'
celephais ALL=(root) NOPASSWD: /usr/local/bin/celephais-seat
EOF
chmod 440 /etc/sudoers.d/celephais
if command -v visudo >/dev/null; then
  visudo -cf /etc/sudoers.d/celephais >/dev/null
fi

# Two operations need a privilege beyond powering VMs on and off: logging a
# Realm out runs one command inside the guest, and parking one writes its live
# session to a new state volume. Grant exactly those two, on VMs only.
TOKENID=${PVE_TOKEN#PVEAPIToken=}; TOKENID=${TOKENID%%=*}
if [ -n "$TOKENID" ] && command -v pveum >/dev/null; then
  pveum role add CelephaisGuest -privs VM.GuestAgent.Unrestricted 2>/dev/null ||
    pveum role modify CelephaisGuest -privs VM.GuestAgent.Unrestricted
  # qm suspend --todisk allocates the state volume: a disk change on the VM,
  # and space taken on the storage that holds it. Its own role, so the power
  # role keeps its narrow meaning.
  PARK_PRIVS=VM.Config.Disk,Datastore.AllocateSpace
  pveum role add CelephaisPark -privs "$PARK_PRIVS" 2>/dev/null ||
    pveum role modify CelephaisPark -privs "$PARK_PRIVS"
  pveum acl modify /storage --tokens "$TOKENID" --roles CelephaisPark
  pveum acl modify /storage --users "${TOKENID%%!*}" --roles CelephaisPark
  # Two Proxmox rules bite here. Tokens are privilege-separated: what a token
  # may do is the intersection of its own rights and its user's, so grant both.
  # And an ACL on /vms/<id> overrides the one inherited from /vms, so a Realm
  # that already has its own entry needs the role named on that entry too.
  for _path in /vms $(grep '^REALMS *=' "$SRC/bin/celephais_core.py" |
                      grep -oE '[0-9]+' | sed 's|^|/vms/|'); do
    pveum acl modify "$_path" --tokens "$TOKENID" --roles CelephaisGuest,CelephaisPark &&
    pveum acl modify "$_path" --users "${TOKENID%%!*}" --roles CelephaisGuest,CelephaisPark ||
      echo "could not grant the extra privileges on $_path; logout and park will fail" >&2
  done
fi

# Core's own writable state: which USB devices may travel with the Local Seat.
# Deliberately not in /etc/celephais, which holds the credentials Core may read
# and must never be able to replace.
install -d -m 750 -o celephais -g celephais /var/lib/celephais

install -d -m 750 -o root -g celephais /etc/celephais
umask 027
cat > /etc/celephais/core.env <<EOF
PVE_HOST=127.0.0.1:8006
PVE_NODE=$(hostname -s)
PVE_TOKEN=$PVE_TOKEN
CORE_TOKEN=$CORE_TOKEN
PVE_VERIFY_TLS=0
CORE_BIND=$TS_IP:8080
EOF
chown root:celephais /etc/celephais/core.env
chmod 640 /etc/celephais/core.env

cat > /etc/systemd/system/celephais-core.service <<'EOF'
[Unit]
Description=Celephais Core control plane
After=network-online.target tailscaled.service pve-cluster.service
Wants=network-online.target
# Keep retrying: the Tailscale address may not be up at the first attempt.
StartLimitIntervalSec=0

[Service]
User=celephais
EnvironmentFile=/etc/celephais/core.env
# CORE_BIND is the Tailscale address, written at install time: the celephais
# user cannot query tailscaled itself. If the address is not up yet the start
# fails and Restart= retries until it is.
ExecStart=/usr/bin/python3 /opt/celephais/celephais_core.py
Restart=on-failure
RestartSec=5
# No NoNewPrivileges here: it blocks sudo, and the whole point of the one
# sudoers rule is to let Core call the seat switcher as root.
PrivateTmp=yes
# Only /usr, /boot and /efi are read-only. Not "full" and not "strict":
# sudo writes its timestamp under /var, and the seat switcher writes VM
# configuration into /etc/pve, the Proxmox cluster filesystem.
ProtectSystem=true
ProtectHome=yes
# The USB registry lives here, and nothing else Core writes does.
ReadWritePaths=/var/lib/celephais

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable celephais-core.service
# restart, not "enable --now": that would leave an already running service
# on the old unit file, which is exactly how a fixed setting goes unnoticed.
systemctl restart celephais-core.service
sleep 2
systemctl --no-pager --lines=15 status celephais-core.service || true

echo
echo "Core is listening on http://$TS_IP:8080"
echo "Local Seat switcher: /usr/local/bin/celephais-seat"
echo "On the client, put this in .env:  CORE_URL=http://$TS_IP:8080"
