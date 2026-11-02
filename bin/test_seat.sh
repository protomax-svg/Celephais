#!/usr/bin/env bash
# Check celephais-seat against a fake `qm`. No Proxmox needed. Run: bin/test_seat.sh
set -euo pipefail
SEAT=$(cd "$(dirname "$0")" && pwd)/celephais-seat
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

cat > "$tmp/qm" <<'FAKE'
#!/usr/bin/env bash
echo "qm $*" >> "$QMSTATE/log"
vmid=$2
case "$1" in
  status)   echo "status: $(cat "$QMSTATE/$vmid.status")" ;;
  guest)    vmid=$3; echo stopped > "$QMSTATE/$vmid.status" ;;   # guest cmd <vmid> shutdown
  agent)    ;;
  config)   cat "$QMSTATE/$vmid.config" ;;
  shutdown) echo stopped > "$QMSTATE/$vmid.status" ;;
  start)    echo running > "$QMSTATE/$vmid.status" ;;
  set)      shift 2
            if [ "$1" = -delete ]; then
              for k in ${2//,/ }; do sed -i "/^$k:/d" "$QMSTATE/$vmid.config"; done
            else
              echo "${1#-}: $2" >> "$QMSTATE/$vmid.config"
            fi ;;
esac
FAKE
chmod +x "$tmp/qm"
export QMSTATE=$tmp PATH=$tmp:$PATH

seed() { # seed <vmid> <running|stopped> <seat:yes|no>
  echo "$2" > "$tmp/$1.status"
  { echo "name: vm$1"; [ "$3" = yes ] && printf 'hostpci0: 0000:01:00.0,pcie=1\nhostpci1: x\nusb0: a\nusb1: b\n'; true; } > "$tmp/$1.config"
}
log() { cat "$tmp/log"; }
fail() { echo "FAIL: $1"; log; exit 1; }

seed 202 stopped no; seed 203 stopped no   # asked about on every switch

# 1. windows owns the seat, work is off -> switch to work
: > "$tmp/log"; seed 100 running yes; seed 201 stopped no
"$SEAT" switch work > /dev/null
grep -q stopped "$tmp/100.status" || fail "windows was not shut down"
grep -q 'qm start 201'    "$tmp/log" || fail "work was not started"
! grep -q "^hostpci0:" "$tmp/100.config" || fail "seat not released from windows"
grep -q '^hostpci1:' "$tmp/201.config" || fail "GPU audio not attached to work"
grep -q '^usb1:'     "$tmp/201.config" || fail "USB not attached to work"

# 2. same switch again -> nothing happens
: > "$tmp/log"
"$SEAT" switch work > /dev/null
! grep -q "qm start" "$tmp/log" || fail "not idempotent: restarted a running owner"

# 3. work runs headless, windows owns the seat -> switch to windows, work survives
: > "$tmp/log"; seed 100 stopped no; seed 201 running no
"$SEAT" switch windows > /dev/null
grep -q running "$tmp/201.status" || fail "headless realm was killed"
grep -q 'qm start 100'    "$tmp/log" || fail "windows was not started"

# 4. release strips the seat from a stopped realm, and refuses a running one
: > "$tmp/log"; seed 201 stopped yes
"$SEAT" release work > /dev/null
! grep -q '^hostpci0:' "$tmp/201.config" || fail "release left the GPU attached"
! grep -q '^usb0:'     "$tmp/201.config" || fail "release left the USB attached"
! grep -q 'qm start' "$tmp/log" || fail "release started the realm"

seed 201 running yes
! "$SEAT" release work 2>/dev/null || fail "release accepted a running realm"

# 5. work2 takes the seat from work: it is in REALMS, and in HEADLESS_VGA, so
#    it loses its virtual display on the way in and work gets one back.
: > "$tmp/log"; seed 201 running yes; seed 203 stopped no
"$SEAT" switch work2 > /dev/null
grep -q '^vga: none' "$tmp/203.config" || fail "work2 kept a display under the RTX"
grep -q '^hostpci0:' "$tmp/203.config" || fail "GPU not attached to work2"
grep -q '^usb0:'     "$tmp/203.config" || fail "keyboard did not travel to work2"
grep -q 'qm start 203' "$tmp/log"      || fail "work2 was not started"
grep -q '^vga: std'  "$tmp/201.config" || fail "work was left with no display at all"

# 6. unknown realm is refused
! "$SEAT" switch nope 2>/dev/null || fail "unknown realm accepted"

echo "PASS"
