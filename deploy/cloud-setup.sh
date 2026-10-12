#!/usr/bin/env bash
# Runs inside the cloud container. install-cloud.sh pushes it in and calls it.
#
#   cloud-setup.sh <password> <tailscale-auth-key> [tags]
#
# Two doors onto /srv/cloud and nothing else:
#   samba      so a Realm can mount it as an ordinary folder
#   copyparty  so a phone can open it in a browser, and any WebDAV client can
#              mount it. One Python file, no database, no web server in front.
set -euo pipefail

PASSWORD=${1:-}
AUTHKEY=${2:-}
TAGS=${3:-}
[ -n "$PASSWORD" ] && [ -n "$AUTHKEY" ] || {
  echo "usage: cloud-setup.sh <password> <tailscale-auth-key> [tags]" >&2; exit 1; }

# An OAuth client secret is not an auth key: Tailscale will not take one
# without being told which tags the new node may claim.
case "$AUTHKEY" in
  tskey-client-*) [ -n "$TAGS" ] || {
    echo "an OAuth client secret needs tags, e.g. TS_TAGS=tag:celephais-cloud" >&2
    exit 1; } ;;
esac

COPYPARTY_VERSION=${COPYPARTY_VERSION:-1.20.25}
SHARE=/srv/cloud

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq samba python3 curl ca-certificates >/dev/null

# One account owns the files, and both doors sign in as it. The folder is a
# loan from the host, so the ownership set here is what the host sees too,
# shifted by the offset an unprivileged container runs under.
id cloud >/dev/null 2>&1 || useradd --create-home --shell /usr/sbin/nologin cloud
chown -R cloud:cloud "$SHARE"
chmod 755 "$SHARE"

# --- door one: the folder a Realm mounts ----------------------------------
cat > /etc/samba/smb.conf <<EOF
[global]
   workgroup = WORKGROUP
   server string = Celephais
   security = user
   map to guest = never
   server min protocol = SMB3
   # No printers, no home directories, no browsing: one share and nothing else
   # to find by looking.
   load printers = no
   printing = bsd
   printcap name = /dev/null
   disable spoolss = yes
   restrict anonymous = 2

[cloud]
   path = $SHARE
   valid users = cloud
   force user = cloud
   force group = cloud
   read only = no
   create mask = 0644
   directory mask = 0755
   vfs objects = recycle
   recycle:repository = .trash/%U
   recycle:keeptree = yes
   recycle:versions = yes
EOF
printf '%s\n%s\n' "$PASSWORD" "$PASSWORD" | smbpasswd -s -a cloud >/dev/null
smbpasswd -e cloud >/dev/null
systemctl enable --now smbd >/dev/null 2>&1
systemctl restart smbd

# --- door two: the web page and the WebDAV address ------------------------
install -d -m 755 /opt/copyparty
curl -fsSL -o /opt/copyparty/copyparty-sfx.py \
  "https://github.com/9001/copyparty/releases/download/v${COPYPARTY_VERSION}/copyparty-sfx.py"
chmod 755 /opt/copyparty/copyparty-sfx.py

# The password lives in a file only root can read, not on the command line
# where every process on the box could read it out of /proc.
install -d -m 700 /etc/copyparty
cat > /etc/copyparty/server.conf <<EOF
[global]
  i: 0.0.0.0
  p: 3923
  no-crt
  e2dsa
  e2ts

[accounts]
  cloud: $PASSWORD

[/]
  $SHARE
  accs:
    A: cloud
EOF
chmod 600 /etc/copyparty/server.conf

cat > /etc/systemd/system/copyparty.service <<'EOF'
[Unit]
Description=Celephais cloud, the web door onto the shared disk
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 /opt/copyparty/copyparty-sfx.py -c /etc/copyparty/server.conf
User=cloud
Group=cloud
Restart=always
RestartSec=5
# It needs the files and nothing else on the system.
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
NoNewPrivileges=yes
ReadWritePaths=/srv/cloud /var/lib/copyparty
StateDirectory=copyparty
Environment=XDG_CONFIG_HOME=/var/lib/copyparty

[Install]
WantedBy=multi-user.target
EOF
# The config holds the password, so the service user has to be able to read it
# while nobody else can.
chown root:cloud /etc/copyparty/server.conf
chmod 640 /etc/copyparty/server.conf
systemctl daemon-reload
systemctl enable --now copyparty

# --- the private network --------------------------------------------------
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh >/dev/null
fi
systemctl enable --now tailscaled
up=(tailscale up --hostname=celephais-cloud --accept-dns=false --auth-key="$AUTHKEY")
[ -n "$TAGS" ] && up+=("--advertise-tags=$TAGS")
"${up[@]}"

echo
echo "samba     : $(systemctl is-active smbd)"
echo "copyparty : $(systemctl is-active copyparty)"
echo "tailscale : $(tailscale ip -4 2>/dev/null || echo 'not up')"
