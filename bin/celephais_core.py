#!/usr/bin/env python3
"""Celephais Core - the control plane. Starts and stops Realms via the Proxmox API.

Run it on the service LXC or the always-on server, NOT on the hypervisor.
The Gate client talks to this; only this holds the Proxmox credential.

Environment:
  PVE_HOST        proxmox api host:port, e.g. 10.0.0.5:8006
  PVE_NODE        proxmox node name (default: pve)
  PVE_TOKEN       PVEAPIToken=gate@pve!core=<uuid>
  CORE_TOKEN      shared secret; the Gate sends it as "Authorization: Bearer <token>"
  PVE_VERIFY_TLS  0 to accept the self-signed Proxmox cert (LAN prototype only)
  CORE_BIND       default 127.0.0.1:8080

Powering a Realm and giving it the Local Seat are two separate operations.
Starting a Realm never claims the RTX 2060, the physical monitors or the USB
keyboard and mouse; a start first strips any seat the previous session left in
the VM config. Several Realms may run at once, but only one may hold the seat.

API:
  GET  /realms              -> [ {realm, vmid, power, seat, ip}, ... ]
  GET  /realms/<name>       -> one of those
  GET  /seat                -> {owner: <realm name or null>}
  POST /realms/<name>/start -> 202, started headless, never with the seat
  POST /realms/<name>/stop  -> 202
  POST /realms/<name>/seat  -> 200, give that Realm the Local Seat

power: OFF -> BOOTING -> READY, plus STOPPING after a stop and ERROR if
Proxmox cannot answer for that Realm. seat is a separate boolean. CONNECTING
and ONLINE belong to the Gate; it polls until READY and then launches RDP.

Changing passthrough is restricted to root@pam by Proxmox, so the seat
operations shell out to the hardware-tested bin/celephais-seat through sudo.
"""
import hmac
import json
import os
import pathlib
import socket
import subprocess
import sys
import ssl
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Secrets live in .env next to the repo, which is gitignored. A real environment
# variable still wins, so systemd or a shell export overrides the file.
_ENV_FILE = pathlib.Path(__file__).resolve().parent.parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip("\"'"))

# Add a Realm here once its VM exists. "personal" (202) is planned, not built.
REALMS = {"work": 201, "windows": 100}
RDP_PORT = 3389
SEAT_CMD = os.environ.get("SEAT_CMD", "sudo -n /usr/local/bin/celephais-seat").split()

PVE_HOST = os.environ.get("PVE_HOST", "")
PVE_NODE = os.environ.get("PVE_NODE", "pve")
PVE_TOKEN = os.environ.get("PVE_TOKEN", "")
CORE_TOKEN = os.environ.get("CORE_TOKEN", "")

_ctx = ssl.create_default_context()
if os.environ.get("PVE_VERIFY_TLS", "1") == "0":
    _ctx.check_hostname = False
    _ctx.verify_mode = ssl.CERT_NONE


def pve(method, path, **form):
    """One Proxmox API call. Returns the `data` field."""
    data = urllib.parse.urlencode(form).encode() if form else None
    req = urllib.request.Request(
        f"https://{PVE_HOST}/api2/json{path}", data=data, method=method,
        headers={"Authorization": PVE_TOKEN})
    with urllib.request.urlopen(req, timeout=15, context=_ctx) as r:
        return json.load(r)["data"]


def realm_ip(vmid):
    """First non-loopback IPv4 the guest agent reports, or None if the agent is not up."""
    try:
        ifaces = pve("GET", f"/nodes/{PVE_NODE}/qemu/{vmid}/agent/network-get-interfaces")
    except Exception as exc:
        # A booting guest has no agent yet, so this is normal early on. Say why
        # anyway: a silent None hides a permission error as an endless BOOTING.
        print(f"realm_ip({vmid}): {exc}", file=sys.stderr, flush=True)
        return None
    if isinstance(ifaces, dict):          # PVE wraps the agent reply in "result"
        ifaces = ifaces.get("result") or []
    for iface in ifaces or []:
        for addr in iface.get("ip-addresses") or []:
            ip = addr.get("ip-address", "")
            if addr.get("ip-address-type") == "ipv4" and not ip.startswith("127."):
                return ip
    return None


def port_open(ip, port, timeout=1.0):
    try:
        with socket.create_connection((ip, port), timeout):
            return True
    except OSError:
        return False


def has_seat(vmid):
    """True if this VM's config claims the GPU. Readable with VM.Audit."""
    config = pve("GET", f"/nodes/{PVE_NODE}/qemu/{vmid}/config")
    return "hostpci0" in config


def seat_cmd(*args):
    """Run the hardware-tested switcher. Proxmox lets only root change passthrough."""
    proc = subprocess.run(SEAT_CMD + list(args), capture_output=True, text=True)
    if proc.returncode != 0:
        raise SeatError((proc.stderr or proc.stdout).strip()
                        or f"{' '.join(SEAT_CMD)} failed with {proc.returncode}")
    return proc.stdout.strip()


class SeatError(Exception):
    pass


def realm_state(name):
    vmid = REALMS[name]
    try:
        status = pve("GET", f"/nodes/{PVE_NODE}/qemu/{vmid}/status/current")["status"]
        seat = has_seat(vmid)
    except KeyError:
        raise
    except Exception as exc:
        return {"realm": name, "vmid": vmid, "power": "ERROR", "seat": None,
                "ip": None, "error": str(exc)}
    if status != "running":
        return {"realm": name, "vmid": vmid, "power": "OFF", "seat": seat, "ip": None}
    ip = realm_ip(vmid)
    # READY means the desktop can actually be connected to, not just that the VM runs.
    power = "READY" if ip and port_open(ip, RDP_PORT) else "BOOTING"
    return {"realm": name, "vmid": vmid, "power": power, "seat": seat, "ip": ip}


def seat_owner():
    for name, vmid in REALMS.items():
        try:
            if has_seat(vmid):
                return name
        except Exception:
            continue
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "celephais-core"

    def _reply(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authed(self):
        if CORE_TOKEN and hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + CORE_TOKEN):
            return True
        self._reply(401, {"error": "unauthorized"})
        return False

    def _route(self, method):
        if not self._authed():
            return
        parts = self.path.strip("/").split("/")
        try:
            if method == "GET" and parts == ["realms"]:
                return self._reply(200, [realm_state(n) for n in REALMS])
            if method == "GET" and parts == ["seat"]:
                return self._reply(200, {"owner": seat_owner()})
            if method == "GET" and len(parts) == 2 and parts[0] == "realms":
                return self._reply(200, realm_state(parts[1]))
            if (method == "POST" and len(parts) == 3 and parts[0] == "realms"
                    and parts[2] == "seat"):
                name = parts[1]
                REALMS[name]                     # 404 for an unknown realm
                return self._reply(200, {"result": seat_cmd("switch", name),
                                         "owner": seat_owner()})
            if (method == "POST" and len(parts) == 3 and parts[0] == "realms"
                    and parts[2] in ("start", "stop")):
                name, vmid = parts[1], REALMS[parts[1]]
                running = pve("GET", f"/nodes/{PVE_NODE}/qemu/{vmid}/status/current")["status"] == "running"
                if parts[2] == "start" and not running:
                    # A start is always headless. Never inherit a seat the last
                    # session left behind, or a remote start would silently take
                    # over the physical monitors.
                    if has_seat(vmid):
                        seat_cmd("release", name)
                    pve("POST", f"/nodes/{PVE_NODE}/qemu/{vmid}/status/start")
                elif parts[2] == "stop" and running:
                    pve("POST", f"/nodes/{PVE_NODE}/qemu/{vmid}/status/shutdown")
                    # The guest is still "running" to Proxmox for a few seconds
                    # more, and its agent is already gone, so realm_state would
                    # read that as BOOTING. Say what was actually asked for.
                    return self._reply(202, {"realm": name, "vmid": vmid,
                                             "power": "STOPPING", "seat": has_seat(vmid),
                                             "ip": None})
                return self._reply(202, realm_state(name))
        except KeyError:
            return self._reply(404, {"error": "unknown realm"})
        except SeatError as exc:
            # Refusing is the safe answer: never start a Realm that would grab
            # the GPU because the seat could not be released.
            return self._reply(409, {"error": f"local seat: {exc}"})
        except Exception as exc:
            return self._reply(502, {"error": f"proxmox: {exc}"})
        self._reply(404, {"error": "not found"})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")


if __name__ == "__main__":
    for var in ("PVE_HOST", "PVE_TOKEN", "CORE_TOKEN"):
        if not os.environ.get(var):
            raise SystemExit(f"celephais-core: set {var}")
    host, _, port = os.environ.get("CORE_BIND", "127.0.0.1:8080").rpartition(":")
    print(f"celephais-core listening on {host}:{port}", flush=True)
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()
