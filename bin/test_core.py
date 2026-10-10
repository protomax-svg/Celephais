#!/usr/bin/env python3
"""Check Core against a fake Proxmox. No Proxmox needed. Run: python3 bin/test_core.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import celephais_core as c  # noqa: E402

fake = type("fake", (), {"status": "stopped", "agent": False, "rdp": False,
                         "seat": False, "seat_calls": [], "seat_fails": False,
                         "vmstate": None, "suspended": [],
                         "exec_cmd": None, "exec_exit": 0, "exec_fails": False})


def fake_pve(method, path, **form):
    if path.endswith("/status/current"):
        return {"status": fake.status}
    if path.endswith("/config"):
        config = ({"hostpci0": "0000:01:00.0,pcie=1", "usb0": "host=1532:02a2"}
                  if fake.seat else {"name": "vm"})
        if fake.vmstate:
            config["vmstate"] = fake.vmstate
        return config
    if path.endswith("/status/suspend"):
        fake.suspended.append(form)
        return "UPID:pve:task"
    if path.endswith("/agent/exec"):
        if fake.exec_fails:
            raise OSError("QEMU guest agent is not running")
        fake.exec_cmd = form["command"]
        return {"pid": 7}
    if "/agent/exec-status" in path:
        return {"exited": 1, "exitcode": fake.exec_exit, "out-data": ""}
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


def fake_seat(*args):
    fake.seat_calls.append(args)
    if fake.seat_fails:
        raise c.SeatError("another realm is still running with the seat")
    fake.seat = args[0] == "switch"
    return "ok"


c.pve = fake_pve
c.port_open = lambda ip, port, timeout=1.0: fake.rdp
c.seat_cmd = fake_seat

s = c.realm_state("work")
assert s == {"realm": "work", "vmid": 201, "power": "OFF", "seat": False, "ip": None}, s

fake.status = "running"
s = c.realm_state("work")
assert s["power"] == "BOOTING" and s["ip"] is None, s   # agent not up yet

fake.agent = True
s = c.realm_state("work")
assert s["power"] == "BOOTING", s                       # agent up, RDP not listening
assert s["ip"] == "192.168.2.50", s                     # loopback and IPv6 skipped

fake.rdp = True
assert c.realm_state("work")["power"] == "READY"

# power and seat are independent: a running Realm may or may not hold the seat
assert c.realm_state("work")["seat"] is False
assert c.seat_owner() is None
fake.seat = True
assert c.realm_state("work")["power"] == "READY", "the seat changed the power state"
assert c.realm_state("work")["seat"] is True
assert c.seat_owner() == "work"
fake.seat = False

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
assert code == 200 and {r["realm"] for r in body} == {"work", "personal",
                                                      "windows"}, body

# A start of a seatless Realm must not touch the switcher at all.
fake.status, fake.seat, fake.seat_calls = "stopped", False, []
code, body = call("/realms/work/start", method="POST")
assert code == 202 and body["vmid"] == 201, body
assert fake.seat_calls == [], fake.seat_calls

# A start of a Realm that still holds the seat must release it first.
fake.status, fake.seat, fake.seat_calls = "stopped", True, []
call("/realms/work/start", method="POST")
assert fake.seat_calls == [("release", "work")], fake.seat_calls

# If the seat cannot be released, refuse rather than grab the monitors.
fake.status, fake.seat, fake.seat_fails = "stopped", True, True
assert status_of("/realms/work/start", method="POST") == 409
fake.seat_fails = False

# Asking for the seat is a separate operation.
fake.status, fake.seat, fake.seat_calls = "running", False, []
code, body = call("/realms/work/seat", method="POST")
assert code == 200 and body["owner"] == "work", body
assert fake.seat_calls == [("switch", "work")], fake.seat_calls
assert status_of("/realms/nope/seat", method="POST") == 404

code, body = call("/seat")
assert body["owner"] == "work", body

# release is the inverse: it strips the seat without powering anything
fake.seat_calls = []
code, body = call("/realms/work/release", method="POST")
assert code == 200 and fake.seat_calls == [("release", "work")], fake.seat_calls
assert body["owner"] is None, body

# releasing an already headless realm must not need root at all
fake.seat, fake.seat_calls = False, []
code, body = call("/realms/work/release", method="POST")
assert code == 200 and fake.seat_calls == [], fake.seat_calls

# logout runs one command in the guest and never raises, whatever the agent says
fake.exec_exit, fake.exec_fails = 0, False
code, body = call("/realms/work/logout", method="POST")
assert code == 200 and body["ok"] is True, body
assert fake.exec_cmd[:2] == ["/bin/sh", "-c"] and "terminate-user" in fake.exec_cmd[2], \
    fake.exec_cmd
fake.exec_exit = 1
assert call("/realms/work/logout", method="POST")[1]["ok"] is False
fake.exec_fails = True
code, body = call("/realms/work/logout", method="POST")
assert code == 200 and body["ok"] is False, body     # a dead agent must not 502
fake.exec_fails = False
assert status_of("/realms/nope/logout", method="POST") == 404

# --- parking: the live session goes to disk, the RAM comes back ---
fake.status, fake.seat, fake.vmstate, fake.suspended = "running", False, None, []
code, body = call("/realms/work/park", method="POST")
assert code == 202 and body["power"] == "PARKING", body
assert fake.suspended == [{"todisk": 1}], fake.suspended

# A saved session is not OFF: starting it restores the desktop, so say so.
fake.status, fake.vmstate = "stopped", "local-lvm:vm-201-state-suspend"
assert c.realm_state("work")["power"] == "PARKED", c.realm_state("work")

# Parking a Realm that holds the Local Seat must be refused, not attempted: a
# consumer RTX on plain vfio-pci cannot have its state saved.
fake.status, fake.seat, fake.vmstate, fake.suspended = "running", True, None, []
assert status_of("/realms/work/park", method="POST") == 409
assert fake.suspended == [], "it suspended a Realm holding the GPU"

# Nothing running, nothing to save.
fake.status, fake.seat, fake.suspended = "stopped", False, []
code, body = call("/realms/work/park", method="POST")
assert code == 200 and fake.suspended == [], body

fake.status = "running"
code, body = call("/realms/work/stop", method="POST")
assert body["power"] == "STOPPING", body   # not BOOTING: the agent is already gone

srv.shutdown()
print("PASS")
