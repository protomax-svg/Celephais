#!/usr/bin/env bash
# Telegram alerts for the backups. Run as root on the Proxmox host, after
# install-backup.sh. Idempotent.
#
#   bash install-alerts.sh
#
# Needs /etc/celephais/telegram.env (0600) with TG_TOKEN and TG_CHAT. The
# token comes from @BotFather; the chat is your user id, which the bot learns
# when you send it any message - this script looks it up if TG_CHAT is unset.
#
# Three things report in:
#   - celephais-backup-disk   udev, the moment the USB disk leaves or returns.
#                             It also remounts the disk when it comes back.
#   - celephais-backup-cloud  OnFailure= sends the journal tail straight away.
#   - celephais-backup-report 05:00 every morning: one message on how last
#                             night went. Silent if fine, loud if not.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "run this as root" >&2; exit 1; }
SRC=$(cd "$(dirname "$0")/.." && pwd)
ENV=/etc/celephais/telegram.env
UUID=${UUID:-6380-6E6F}

[ -s "$ENV" ] || { echo "write TG_TOKEN=... into $ENV first (chmod 600)" >&2; exit 1; }
chmod 600 "$ENV"

if ! grep -q '^TG_CHAT=' "$ENV"; then
  chat=$(python3 - "$ENV" <<'PY'
import json, sys, urllib.request
env = dict(l.strip().split("=", 1) for l in open(sys.argv[1]) if "=" in l)
r = json.load(urllib.request.urlopen(
    f"https://api.telegram.org/bot{env['TG_TOKEN']}/getUpdates", timeout=20))
chats = {(u.get("message") or {}).get("chat", {}).get("id")
         for u in r["result"]} - {None}
print(chats.pop() if len(chats) == 1 else "")
PY
)
  [ -n "$chat" ] || { echo "send the bot any message in Telegram, then rerun" >&2; exit 1; }
  echo "TG_CHAT=$chat" >> "$ENV"
  echo "found chat $chat"
fi

for f in celephais-notify celephais-backup-disk celephais-backup-report; do
  install -m 755 "$SRC/bin/$f" "/usr/local/bin/$f"
done

# --- failures of any celephais unit ------------------------------------------
cat > /etc/systemd/system/celephais-failed@.service <<'UNIT'
[Unit]
Description=Tell Telegram that %i failed

[Service]
Type=oneshot
ExecStart=/usr/local/bin/celephais-notify --unit-failed %i
UNIT

install -d /etc/systemd/system/celephais-backup-cloud.service.d
cat > /etc/systemd/system/celephais-backup-cloud.service.d/alert.conf <<'UNIT'
[Unit]
OnFailure=celephais-failed@%n.service
UNIT

# --- the disk coming and going -------------------------------------------------
cat > /etc/systemd/system/celephais-backup-disk.service <<'UNIT'
[Unit]
Description=Remount the backup disk if it came back, and report its state
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/celephais-backup-disk
UNIT

# udev may not run long tasks itself, so it only asks systemd to start the
# unit. --no-block matters: udev waits for RUN+= to return.
cat > /etc/udev/rules.d/90-celephais-backup-disk.rules <<RULES
SUBSYSTEM=="block", ACTION=="add|remove", ENV{ID_FS_UUID}=="$UUID", RUN+="/usr/bin/systemctl --no-block start celephais-backup-disk.service"
RULES
udevadm control --reload

# --- the morning report ----------------------------------------------------
cat > /etc/systemd/system/celephais-backup-report.service <<'UNIT'
[Unit]
Description=Report last night's backups to Telegram
After=network-online.target
Wants=network-online.target
OnFailure=celephais-failed@%n.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/celephais-backup-report
UNIT

cat > /etc/systemd/system/celephais-backup-report.timer <<'UNIT'
[Unit]
Description=Morning backup report

[Timer]
# 02:30 guests (~8 min), 03:30 cloud (~1 min). 05:00 leaves room for a slow
# night and still lands before anyone is up.
OnCalendar=*-*-* 05:00:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now celephais-backup-report.timer

# Seed the disk state so the first udev event compares against reality.
install -d /var/lib/celephais
if mountpoint -q /mnt/backup; then echo mounted; else echo gone; fi > /var/lib/celephais/backup-disk.state

/usr/local/bin/celephais-notify --quiet "🔔 Celephais alerts are wired up on $(hostname). Morning report at 05:00."
echo "alerts installed; a test message went to Telegram"
