#!/usr/bin/env bash
# Install Celephais Core as a service on the Proxmox host. Run as root, on that host.
#
#   ./install-core.sh 'PVEAPIToken=gate@pve!core=<uuid>' '<core-token>'
#
# Core listens only on the host's Tailscale address, so the Gate reaches it from
# anywhere on the tailnet and nothing on the LAN can see it.
set -euo pipefail

PVE_TOKEN=${1:?usage: install-core.sh <PVE_TOKEN> <CORE_TOKEN>}
CORE_TOKEN=${2:?usage: install-core.sh <PVE_TOKEN> <CORE_TOKEN>}
SRC=$(cd "$(dirname "$0")/.." && pwd)

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

install -d -m 750 -o root -g celephais /etc/celephais
umask 027
cat > /etc/celephais/core.env <<EOF
PVE_HOST=127.0.0.1:8006
PVE_NODE=$(hostname -s)
PVE_TOKEN=$PVE_TOKEN
CORE_TOKEN=$CORE_TOKEN
PVE_VERIFY_TLS=0
EOF
chown root:celephais /etc/celephais/core.env
chmod 640 /etc/celephais/core.env

cat > /etc/systemd/system/celephais-core.service <<'EOF'
[Unit]
Description=Celephais Core control plane
After=network-online.target tailscaled.service pve-cluster.service
Wants=network-online.target

[Service]
User=celephais
EnvironmentFile=/etc/celephais/core.env
# The Tailscale address only exists once tailscaled is up, so resolve it here.
ExecStart=/bin/sh -c 'CORE_BIND="$(tailscale ip -4 | head -1):8080" exec /usr/bin/python3 /opt/celephais/celephais_core.py'
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now celephais-core.service
sleep 2
systemctl --no-pager --lines=15 status celephais-core.service || true

echo
echo "Core is listening on http://$TS_IP:8080"
echo "On the client, put this in .env:  CORE_URL=http://$TS_IP:8080"
