#!/usr/bin/env bash
# Acceptance test: the Gate on a host with no Tailscale and no Python.
#
# The container is bare Debian: no tailscale, no tailscaled, no python, no TUN
# device, no extra privileges. Everything must come from the binary and the
# vault.
#
#   bin/test_clean_host.sh                 # status only, touches no VM
#   bin/test_clean_host.sh work            # full flow up to READY, no RDP
#
# Needs TS_AUTHKEY in .env: a REUSABLE, EPHEMERAL auth key from the Tailscale
# admin console.
#
# Caveat worth knowing: a Docker container still reaches the tailnet through
# the host's routes, so "Core answered" alone would prove nothing. The real
# evidence is that a NEW ephemeral node appears in the tailnet while the
# container runs. That can only come from the Gate's own tailscaled. For a
# fully independent check, run the binary on a machine that never had
# Tailscale installed.
set -euo pipefail
cd "$(dirname "$0")/.."

REALM=${1:-}
IMAGE=${IMAGE:-debian:stable-slim}
BIN=build/dist/celephais-gate
NODE=celephais-gate-test
[ -x "$BIN" ] || { echo "build it first: ./build/make-gate.sh" >&2; exit 1; }

set -a; [ -f .env ] && . ./.env; set +a
: "${TS_AUTHKEY:?set TS_AUTHKEY (a reusable ephemeral key) in .env}"
: "${CORE_URL:?set CORE_URL in .env}"
: "${CORE_TOKEN:?set CORE_TOKEN in .env}"

STAGE=$(mktemp -d); OUT=$(mktemp)
trap 'rm -rf "$STAGE" "$OUT"; docker rm -f "$NODE" >/dev/null 2>&1 || true' EXIT
cp "$BIN" "$STAGE/celephais-gate"

PASSWORD="clean-host-test-$RANDOM$RANDOM"
PASSWORD="$PASSWORD" NODE="$NODE" python3 - "$STAGE" <<'PYEOF'
import os, pathlib, sys
sys.path.insert(0, "bin")
import celephais_vault as v
v.save(pathlib.Path(sys.argv[1]) / "celephais.vault", os.environ["PASSWORD"],
       {"CORE_URL": os.environ["CORE_URL"], "CORE_TOKEN": os.environ["CORE_TOKEN"],
        "TS_AUTHKEY": os.environ["TS_AUTHKEY"], "TS_HOSTNAME": os.environ["NODE"],
        "RDP_USER": os.environ.get("RDP_USER", "max")})
PYEOF
echo "staged: the binary and a vault holding the tailnet key"

echo
echo "--- 1. the container is bare ---"
docker run --rm "$IMAGE" sh -c 'command -v tailscale tailscaled python3 || echo "nothing installed"'

echo
echo "--- 2. the node must not exist in the tailnet yet ---"
if tailscale status 2>/dev/null | grep -q "$NODE"; then
  echo "FAIL: $NODE is already in the tailnet; remove it first" >&2; exit 1
fi
echo "ok: $NODE is absent"

ARGS=(--status)
[ -n "$REALM" ] && ARGS=("$REALM" --no-connect)

echo
echo "--- 3. running the Gate in the clean container ---"
echo "$PASSWORD" | docker run --rm -i --name "$NODE" \
  -v "$STAGE:/gate:ro" -w /gate \
  "$IMAGE" ./celephais-gate "${ARGS[@]}" > "$OUT" 2>&1 &
RUNNER=$!

echo "--- 4. watching the tailnet for a node the container created itself ---"
JOINED=no
for _ in $(seq 1 60); do
  if tailscale status 2>/dev/null | grep -q "$NODE"; then JOINED=yes; break; fi
  kill -0 $RUNNER 2>/dev/null || break
  sleep 1
done
wait $RUNNER || true

echo
sed 's/^/    /' "$OUT"
echo
if [ "$JOINED" = yes ]; then
  echo "PASS: the container joined the tailnet on its own as $NODE"
else
  echo "FAIL: no new tailnet node appeared; the Gate did not bring up its own tailscaled"
  exit 1
fi
grep -q "tailnet: up" "$OUT" || { echo "FAIL: the Gate never reported the tailnet up"; exit 1; }
echo "PASS: clean host, no Tailscale installed, Gate reached Core through its own tailnet"
