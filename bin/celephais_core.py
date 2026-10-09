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

API:
  GET  /realms              -> [ {realm, vmid, state, ip}, ... ]
  GET  /realms/<name>       -> {realm, vmid, state, ip}
  POST /realms/<name>/start -> 202 + current state
  POST /realms/<name>/stop  -> 202 + current state

States: OFF -> BOOTING -> READY. CONNECTING and ONLINE belong to the Gate,
not here; the Gate polls until READY and then launches RDP.
"""
import hmac
import json
import os
import socket
import ssl
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REALMS = {"work": 201, "windows": 100}
RDP_PORT = 3389

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
    except Exception:
        return None
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


def realm_state(name):
    vmid = REALMS[name]
    status = pve("GET", f"/nodes/{PVE_NODE}/qemu/{vmid}/status/current")["status"]
    if status != "running":
        return {"realm": name, "vmid": vmid, "state": "OFF", "ip": None}
    ip = realm_ip(vmid)
    # READY means the desktop can actually be connected to, not just that the VM runs.
    state = "READY" if ip and port_open(ip, RDP_PORT) else "BOOTING"
    return {"realm": name, "vmid": vmid, "state": state, "ip": ip}


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
            if method == "GET" and len(parts) == 2 and parts[0] == "realms":
                return self._reply(200, realm_state(parts[1]))
            if (method == "POST" and len(parts) == 3 and parts[0] == "realms"
                    and parts[2] in ("start", "stop")):
                name, vmid = parts[1], REALMS[parts[1]]
                running = pve("GET", f"/nodes/{PVE_NODE}/qemu/{vmid}/status/current")["status"] == "running"
                if parts[2] == "start" and not running:
                    pve("POST", f"/nodes/{PVE_NODE}/qemu/{vmid}/status/start")
                elif parts[2] == "stop" and running:
                    pve("POST", f"/nodes/{PVE_NODE}/qemu/{vmid}/status/shutdown")
                return self._reply(202, realm_state(name))
        except KeyError:
            return self._reply(404, {"error": "unknown realm"})
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
