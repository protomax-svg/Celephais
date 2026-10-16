#!/usr/bin/env python3
"""Check Core against a fake Proxmox. No Proxmox needed. Run: python3 bin/test_core.py"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import celephais_core as c  # noqa: E402

fake = type("fake", (), {"status": "stopped", "agent": False, "rdp": False,
                         "seat": False, "seat_calls": [], "seat_fails": False,
                         "vmstate": None, "suspended": [],
                         "exec_cmd": None, "exec_exit": 0, "exec_fails": False,
                         "exec_out": "", "exec_cmds": [], "stops": [],
                         "agent_shutdown_fails": False, "tailnet": False,
                         "qmp": None, "woken": 0})


def fake_pve(method, path, **form):
    if path.endswith("/status/current"):
        return {"status": fake.status, "qmpstatus": fake.qmp or fake.status}
    if path.endswith("/status/resume"):
        fake.woken += 1
        fake.qmp = None                 # system_wakeup: the guest runs again
        return "UPID:pve:task"
    if path.endswith("/agent/ping"):
        return {}
    if path.endswith("/config"):
        config = ({"hostpci0": "0000:01:00.0,pcie=1", "usb0": "host=1532:02a2"}
                  if fake.seat else {"name": "vm"})
        if fake.vmstate:
            config["vmstate"] = fake.vmstate
        return config
    if path.endswith("/agent/shutdown"):
        if fake.agent_shutdown_fails:
            raise OSError("QEMU guest agent is not running")
        fake.stops.append("agent")
        return {}
    if path.endswith("/status/shutdown"):
        fake.stops.append("acpi")
        return "UPID:pve:task"
    if path.endswith("/status/suspend"):
        fake.suspended.append(form)
        return "UPID:pve:task"
    if path.endswith("/agent/exec"):
        if fake.exec_fails:
            raise OSError("QEMU guest agent is not running")
        fake.exec_cmd = form["command"]
        fake.exec_cmds.append(form["command"])
        return {"pid": 7}
    if "/agent/exec-status" in path:
        return {"exited": 1, "exitcode": fake.exec_exit,
                "out-data": fake.exec_out}
    if path.endswith("/network-get-interfaces"):
        if not fake.agent:
            raise OSError("QEMU guest agent is not running")
        # the real PVE agent reply is wrapped in "result"
        ifaces = [
            {"name": "lo", "ip-addresses": [
                {"ip-address-type": "ipv4", "ip-address": "127.0.0.1"}]},
            {"name": "enp6s18", "ip-addresses": [
                {"ip-address-type": "ipv6", "ip-address": "fe80::1"},
                {"ip-address-type": "ipv4", "ip-address": "192.168.2.50"}]}]
        if fake.tailnet:
            # the agent lists it after the wire, as the real one does
            ifaces.append({"name": "tailscale0", "ip-addresses": [
                {"ip-address-type": "ipv4", "ip-address": "100.99.187.90"}]})
        return {"result": ifaces}
    return {}


def fake_seat(*args):
    fake.seat_calls.append(args)
    if fake.seat_fails:
        raise c.SeatError("another realm is still running with the seat")
    fake.seat = args[0] == "switch"
    return "ok"


# --- the USB registry: a fake sysfs tree, no host hardware needed ---
import tempfile  # noqa: E402

usb_root = pathlib.Path(tempfile.mkdtemp()) / "usb"


def fake_usb(name, vid, pid, cls="00", serial="", maker="", product="",
             interfaces=()):
    entry = usb_root / name
    entry.mkdir(parents=True)
    for key, value in (("idVendor", vid), ("idProduct", pid),
                       ("bDeviceClass", cls), ("serial", serial),
                       ("manufacturer", maker), ("product", product)):
        if value:
            (entry / key).write_text(value + "\n")
    for number, iface_class in enumerate(interfaces):
        iface = entry / f"{name}:1.{number}"
        iface.mkdir()
        (iface / "bInterfaceClass").write_text(iface_class + "\n")


fake_usb("1-0:1.0", "", "")                                  # an interface
fake_usb("1-1", "1d6b", "0002", cls="09")                    # a root hub
fake_usb("1-4", "1532", "02a2", product="Ornata V3 X", maker="Razer")
fake_usb("1-5", "046d", "c52b", serial="A17F9C", product="Unifying Receiver",
         interfaces=("03",))
# A headset announces nothing itself and leaves it to its interfaces.
fake_usb("1-7", "0b0e", "0309", product="Headset", interfaces=("01", "03"))
c.USB_ROOT = usb_root
c.DEVICES_FILE = usb_root.parent / "devices.json"

found = {d["id"]: d for d in c.usb_devices()}
assert set(found) == {"1532:02a2", "046d:c52b:A17F9C", "0b0e:0309"}, found
# Handing a Realm an unknown storage device is a different decision from
# handing it a keyboard, so the screen has to be able to say which it is.
assert found["0b0e:0309"]["kind"] == "audio", found["0b0e:0309"]
assert found["046d:c52b:A17F9C"]["kind"] == "keyboard or mouse", found
assert found["1532:02a2"]["kind"] == "", "no class announced anywhere"
assert found["046d:c52b:A17F9C"]["usb"] == "046d:c52b", found
assert "Razer Ornata V3 X" == found["1532:02a2"]["name"], found

# Two of the same keyboard: only then does the socket become part of the name.
fake_usb("1-6", "1532", "02a2", product="Ornata V3 X", maker="Razer")
twins = {d["id"] for d in c.usb_devices()}
assert twins == {"1532:02a2@1-4", "1532:02a2@1-6", "046d:c52b:A17F9C",
                 "0b0e:0309"}, twins
import shutil  # noqa: E402
shutil.rmtree(usb_root / "1-6")
assert {d["id"] for d in c.usb_devices()} == {"1532:02a2", "046d:c52b:A17F9C",
                                              "0b0e:0309"}

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

# A Realm that is on the tailnet itself is dialled there, even though the agent
# lists the LAN address first. A Gate on a hotel network cannot reach
# 192.168.2.50 at all, and on the same wire the tailnet address is no slower -
# it is also the one a Tailscale exit node does not swallow.
fake.tailnet = True
assert c.realm_state("work")["ip"] == "100.99.187.90", c.realm_state("work")
fake.tailnet = False
assert c.realm_state("work")["ip"] == "192.168.2.50"    # no Tailscale: the wire

# 100.64.0.0/10 and nothing else: 100.63 and 100.128 are ordinary addresses.
assert c._on_tailnet("100.64.0.1") and c._on_tailnet("100.127.255.254")
assert not c._on_tailnet("100.63.255.255") and not c._on_tailnet("100.128.0.1")
assert not c._on_tailnet("192.168.2.50") and not c._on_tailnet("100.x.1.1")

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
# The keyboard and mouse travel with the seat before anybody has decided
# anything: a screen with no way to type on it is not a working machine.
assert fake.seat_calls == [("switch", "work", "1532:02a2", "046d:c52b",
                          "046d:c08a")], \
    fake.seat_calls
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

# A parked Realm must not be handed the Local Seat without being told twice:
# Proxmox would restore its saved session into hardware it never had.
fake.status, fake.seat, fake.vmstate, fake.seat_calls = "stopped", False, "store:state", []
assert status_of("/realms/work/seat", method="POST") == 409
assert fake.seat_calls == [], "it changed the hardware anyway"
code, body = call("/realms/work/seat?confirm=1", method="POST")
assert code == 200 and fake.seat_calls == [("switch", "work", "1532:02a2",
                                           "046d:c52b", "046d:c08a")], fake.seat_calls
fake.vmstate = None

# Nothing running, nothing to save.
fake.status, fake.seat, fake.suspended = "stopped", False, []
code, body = call("/realms/work/park", method="POST")
assert code == 200 and fake.suspended == [], body

# --- egress: read it from the Realm, never from memory ---
fake.exec_exit = 0            # the logout tests left a failure behind
fake.exec_out = ('{"ExitNodeID": "nkLfnDPEGa11CNTRL", "ExitNodeIP": ""}')
assert c.egress(201) == {"mode": "exit-node", "node": "nkLfnDPEGa11CNTRL"}, c.egress(201)
fake.exec_out = '{"ExitNodeID": "", "ExitNodeIP": ""}'
assert c.egress(201) == {"mode": "direct", "node": ""}, c.egress(201)
fake.exec_out = ""                      # no tailscale in that Realm at all
assert c.egress(201)["mode"] == "direct", c.egress(201)
assert "not installed" in c.egress(201)["detail"], c.egress(201)

code, body = call("/realms/work/egress")
assert code == 200 and body["mode"] == "direct", body
assert status_of("/realms/work/egress?mode=sideways", method="POST") == 400

# Asking for an exit node when none is configured must say so, not go quiet.
c.EXIT_NODE = ""
os.environ.pop("EXIT_NODE_WORK", None)
assert status_of("/realms/work/egress?mode=exit-node", method="POST") == 400

# Realms do not share one exit node, so each may name its own.
os.environ["EXIT_NODE_WORK"] = "server-yo"
c.EXIT_NODE = "hostadmin"
assert c.exit_node("work") == "server-yo" and c.exit_node("personal") == "hostadmin"
fake.exec_out = '{"ExitNodeID": "abc", "ExitNodeIP": "100.124.90.77"}'
fake.exec_cmds = []
code, body = call("/realms/work/egress?mode=exit-node", method="POST")
assert code == 200 and body["node"] == "100.124.90.77", body
assert ["/bin/sh", "-c", "tailscale set --exit-node=server-yo"] \
    in fake.exec_cmds, fake.exec_cmds
fake.exec_out = ""

# --- devices over HTTP ---
code, body = call("/devices")
assert code == 200 and all(d["action"] is None for d in body), body
assert status_of("/devices/1532:02a2@1-4", method="POST") == 400   # no action

call("/devices/1532%3A02a2?action=seat", method="POST")
call("/devices/046d%3Ac52b%3AA17F9C?action=never", method="POST")
by_id = {d["id"]: d for d in call("/devices")[1]}
assert by_id["1532:02a2"]["action"] == "seat", by_id
assert by_id["046d:c52b:A17F9C"]["action"] == "never", by_id
# The receiver is refused, the mouse's own cable is not: they are two devices
# to the host, and a refusal is of one of them.
assert c.seat_usb() == ["1532:02a2", "046d:c08a"], c.seat_usb()

# Audio is not a special case: an approved headset travels like anything else.
call("/devices/0b0e%3A0309?action=seat", method="POST")
assert c.seat_usb() == ["1532:02a2", "046d:c08a", "0b0e:0309"], c.seat_usb()
assert c.device_registry()["0b0e:0309"]["role"] == "audio", c.device_registry()
call("/devices/0b0e%3A0309?action=forget", method="POST")

# Taking the seat must carry exactly what was approved, and nothing else.
fake.status, fake.seat, fake.vmstate, fake.seat_calls = "running", False, None, []
call("/realms/work/seat", method="POST")
assert fake.seat_calls == [("switch", "work", "1532:02a2", "046d:c08a")], \
    fake.seat_calls

# A decision survives the device being unplugged, and can be taken back.
c.USB_ROOT = usb_root.parent / "nothing-plugged-in"
absent = {d["id"]: d for d in c.devices()}
assert absent["1532:02a2"] == {"id": "1532:02a2", "usb": "1532:02a2",
                               "name": "Razer Ornata V3 X", "action": "seat",
                               "role": "", "kind": "", "present": False}, absent
# The keyboard and the mouse travel even while Core cannot see them. They
# hang off a USB switch: when it points at the other machine they are gone
# from this host's USB tree, and that is exactly when the seat is asked for,
# because that is where Max is sitting. The claim in the VM config waits for
# them. The receiver is refused by name here, so it stays out regardless.
assert c.seat_usb() == ["1532:02a2", "046d:c08a"], c.seat_usb()
# Forgetting the keyboard does not take it off the seat; it only takes back
# the decision. Refusing it by name is how it is taken off, and the mouse is
# refused here, which is why it stays out.
call("/devices/1532%3A02a2?action=forget", method="POST")
assert c.seat_usb() == ["1532:02a2", "046d:c08a"], c.seat_usb()
call("/devices/1532%3A02a2?action=never", method="POST")
assert c.seat_usb() == ["046d:c08a"], "a refusal must beat the default"
call("/devices/1532%3A02a2?action=forget", method="POST")
call("/devices/046d%3Ac52b%3AA17F9C?action=forget", method="POST")
# Nothing decided and nothing plugged in: the seat still claims both, because
# a Realm that comes up with the monitors and no input cannot be told anything.
assert c.seat_usb() == ["1532:02a2", "046d:c52b", "046d:c08a"], c.seat_usb()
c.USB_ROOT = usb_root

# The bug Max hit: he plugged in a gamepad, approved it, refused the headset,
# and lost the keyboard and mouse with them. A list built only from approvals
# replaced the two devices nobody had ever been asked about.
call("/devices/0b0e%3A0309?action=seat", method="POST")
assert c.seat_usb() == ["1532:02a2", "046d:c52b", "046d:c08a",
                        "0b0e:0309"], c.seat_usb()
call("/devices/0b0e%3A0309?action=forget", method="POST")

# A stop goes through the guest agent. The ACPI button is swallowed by GNOME,
# which holds a block inhibitor on the power key, and Proxmox then waits until
# it gives up with "VM quit/powerdown failed - got timeout".
fake.status, fake.stops = "running", []
code, body = call("/realms/work/stop", method="POST")
assert body["power"] == "STOPPING", body   # not BOOTING: the agent is already gone
assert fake.stops == ["agent"], fake.stops

# A Realm whose agent is missing still gets the button: a stop that does
# nothing at all is worse than a slow one.
fake.stops, fake.agent_shutdown_fails = [], True
call("/realms/work/stop", method="POST")
assert fake.stops == ["acpi"], fake.stops
fake.agent_shutdown_fails = False

# A guest that suspended itself is "running" to Proxmox and frozen inside:
# no agent, no remote desktop. It read as BOOTING for ever, and with nothing
# awake to hear a shutdown it could not be stopped either. Max had to stop
# `personal` in Proxmox while it sat on the monitors.
fake.status, fake.qmp, fake.agent, fake.stops, fake.woken = \
    "running", "suspended", False, [], 0
assert c.realm_state("work")["power"] == "ASLEEP", c.realm_state("work")
code, body = call("/realms/work/stop", method="POST")
assert code == 202 and body["power"] == "STOPPING", body
# Woken first, then shut down by the agent, not the button GNOME swallows.
assert fake.woken == 1 and fake.stops == ["agent"], (fake.woken, fake.stops)

# Opening a sleeping Realm wakes it rather than starting what already runs.
fake.qmp, fake.woken = "suspended", 0
call("/realms/work/start", method="POST")
assert fake.woken == 1 and fake.qmp is None, fake.woken
# Nothing asleep, nothing woken.
fake.stops = []
call("/realms/work/stop", method="POST")
assert fake.woken == 1, "a Realm that was awake was woken"
fake.status = "stopped"

srv.shutdown()
print("PASS")
