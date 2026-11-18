#!/usr/bin/env bash
# Inside the tsdrop container (and, in Docker, on the laptop's fallback copy):
# Apache with mod_dav, serving /srv/tsdrop/data at Nextcloud's old path.
#
#   tsdrop-setup.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
DAV_PATH=/remote.php/dav/files/transferapp/file-transfer-app

apt-get update -qq
apt-get install -y -qq apache2 apache2-utils curl ca-certificates >/dev/null

a2enmod -q dav dav_fs auth_basic authn_file headers deflate >/dev/null
a2dissite -q 000-default >/dev/null 2>&1 || true

echo "Listen 8080" > /etc/apache2/ports.conf
install -d -m 750 -o www-data -g www-data /var/lib/apache2/dav /var/www/empty
touch /etc/apache2/tsdrop.htpasswd
chown root:www-data /etc/apache2/tsdrop.htpasswd
chmod 640 /etc/apache2/tsdrop.htpasswd

cat > /etc/apache2/sites-available/tsdrop.conf <<EOF
# Plain http on purpose: it is only reachable over the tailnet, and WireGuard
# already encrypts every hop. TSDROP's locked chats and vault are AES-GCM on
# the device before they get here anyway.
<VirtualHost *:8080>
  ServerName tsdrop
  DocumentRoot /var/www/empty
  DavLockDB /var/lib/apache2/dav/lockdb
  <Directory /var/www/empty>
    Require all denied
  </Directory>

  # Apache 2.4.54+ caps a request body at 1 GiB by default. TSDROP sends
  # files of any size.
  LimitRequestBody 0
  Timeout 3600

  # The app polls every few seconds, so let a connection live across polls instead of
  # paying a new TCP (and, remotely, WireGuard) round trip each time.
  KeepAlive On
  KeepAliveTimeout 30
  MaxKeepAliveRequests 2000
  # Large downloads straight from the page cache, without a copy through Apache.
  EnableSendfile On

  Alias $DAV_PATH /srv/tsdrop/data
  <Directory /srv/tsdrop/data>
    DAV On
    # Folder listings (PROPFIND) and message JSON shrink ~10x when gzipped. By type
    # (AddOutputFilterByType) does not reach mod_dav's own responses, so: gzip every
    # reply, then exempt file transfers - media is compressed already and big files
    # should keep sendfile - except .json, which is what the app reads all day.
    SetOutputFilter DEFLATE
    SetEnvIf Request_Method "^(GET|HEAD|PUT)$" no-gzip
    SetEnvIfNoCase Request_URI "\.json$" !no-gzip
    Options None
    AllowOverride None
    # mod_dir would answer a collection without its trailing slash with a
    # 301, and a DELETE or MOVE does not follow redirects.
    DirectorySlash Off
    FileETag MTime Size
    AuthType Basic
    AuthName "tsdrop"
    AuthBasicProvider file
    AuthUserFile /etc/apache2/tsdrop.htpasswd
    Require valid-user
  </Directory>

  ErrorLog \${APACHE_LOG_DIR}/tsdrop-error.log
  CustomLog \${APACHE_LOG_DIR}/tsdrop-access.log combined
</VirtualHost>
EOF
a2ensite -q tsdrop >/dev/null
apache2ctl configtest
systemctl enable apache2 >/dev/null 2>&1
systemctl restart apache2
