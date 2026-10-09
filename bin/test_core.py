#!/usr/bin/env python3
"""Check Core against a fake Proxmox. No Proxmox needed. Run: python3 bin/test_core.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import celephais_core as c  # noqa: E402

fake = type("fake", (), {"status": "stopped", "agent": False, "rdp": False})


def fake_pve(method, path, **form):
    if path.endswith("/status/current"):
        return {"status": fake.status}
    if path.endswith("/network-get-interfaces"):
        if not fake.agent:
            raise OSError("QEMU guest agent is not running")
        # the real PVE agent reply is wrapped in "result"
        return {"result": [
            {"name": "lo", "ip-addresses": [
                {"ip-address-type": "ipv4", "ip-address": "127.0.0.1"}]},
            {"name": "enp6s18", "ip-addresses": [
                {"ip-address-type": "ipv6", "ip-address": "fe80::1"},
                {"ip-address-type": "ipv4", "ip-address": "192.168.2.50"}]}]}
    return {}


c.pve = fake_pve
c.port_open = lambda ip, port, timeout=1.0: fake.rdp

s = c.realm_state("work")
assert s == {"realm": "work", "vmid": 201, "state": "OFF", "ip": None}, s

fake.status = "running"
s = c.realm_state("work")
assert s["state"] == "BOOTING" and s["ip"] is None, s   # agent not up yet

fake.agent = True
s = c.realm_state("work")
assert s["state"] == "BOOTING", s                        # agent up, RDP not listening
assert s["ip"] == "192.168.2.50", s                      # loopback and IPv6 skipped

fake.rdp = True
assert c.realm_state("work")["state"] == "READY"

try:
    c.realm_state("nope")
    raise AssertionError("unknown realm accepted")
except KeyError:
    pass

# --- HTTP layer: auth and routing ---
import json          # noqa: E402
import threading     # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

c.CORE_TOKEN = "s3cret"
srv = ThreadingHTTPServer(("127.0.0.1", 0), c.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % srv.server_address[1]


def call(path, token="s3cret", method="GET"):
    req = urllib.request.Request(base + path, method=method)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.load(r)


def status_of(path, token="s3cret", method="GET"):
    try:
        call(path, token, method)
    except urllib.error.HTTPError as e:
        return e.code
    return 200


assert status_of("/realms", token=None) == 401, "no token was accepted"
assert status_of("/realms", token="wrong") == 401, "wrong token was accepted"
assert status_of("/realms/nope") == 404, "unknown realm not rejected"
assert status_of("/nonsense") == 404

code, body = call("/realms")
assert code == 200 and {r["realm"] for r in body} == {"work", "windows"}, body

fake.status = "stopped"
code, body = call("/realms/work/start", method="POST")
assert code == 202 and body["vmid"] == 201, body

fake.status = "running"
code, body = call("/realms/work/stop", method="POST")
assert body["state"] == "STOPPING", body   # not BOOTING: the agent is already gone

srv.shutdown()
print("PASS")
