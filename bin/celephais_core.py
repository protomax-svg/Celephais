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
  POST /realms/<name>/seat    -> 200, give that Realm the Local Seat
                                 409 + confirm:true if a saved session would be
                                 lost; repeat with ?confirm=1 to accept that
  POST /realms/<name>/release -> 200, strip the seat from a stopped Realm
  POST /realms/<name>/logout  -> 200, log out the Realm's desktop session
  POST /realms/<name>/park    -> 202, save the live session to disk and stop
  GET  /devices             -> every USB device on the host, with what we do with it
  POST /devices/<id>?action=seat|never|forget

power: OFF -> BOOTING -> READY, plus PARKED for a Realm whose live session is
saved on disk, STOPPING after a stop, PARKING after a park, and ERROR if
Proxmox cannot answer for that Realm. A parked Realm wakes with an ordinary
start: Proxmox restores the saved state instead of booting. seat is a separate boolean. CONNECTING
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
import time
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

# The host's USB tree. Core reads it straight from sysfs rather than asking
# Proxmox: it runs on the host anyway, and sysfs carries the serial numbers and
# the physical port, which the API summary does not.
USB_ROOT = pathlib.Path("/sys/bus/usb/devices")
# What to do with which device, by stable id. Kept here and not in any VM's
# configuration: an approval belongs to the household, not to one Realm.
# /var, not /etc/celephais: Core writes this one itself, and /etc/celephais
# holds the credentials, which it must not be able to replace.
DEVICES_FILE = pathlib.Path(os.environ.get("CELEPHAIS_DEVICES",
                                           "/var/lib/celephais/devices.json"))

# Add a Realm here once its VM exists.
REALMS = {"work": 201, "personal": 202, "windows": 100}
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
    data = urllib.parse.urlencode(form, doseq=True).encode() if form else None
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


# GNOME Remote Login can only hand a client over to a *fresh* login. A session
# left behind by the last connection, or by whoever sat at the Local Seat,
# refuses the next one, and the Gate's user has to log out on the Realm's own
# screen before they can get back in. So log them out from here instead. Only
# real people (uid 1000..59999); system users keep their sessions.
LOGOUT_SH = (
    "loginctl --no-legend list-users | awk '$1>=1000 && $1<60000 {print $2}' "
    "| xargs -r -n1 loginctl terminate-user; "
    # terminate-user returns before the session is gone, and logging in during
    # the teardown is exactly the race we are trying to avoid.
    "for i in $(seq 15); do loginctl --no-legend list-users "
    "| awk '$1>=1000 && $1<60000' | grep -q . || break; sleep 1; done")


def guest_exec(vmid, *cmd, timeout=30):
    """Run one command inside the guest through the QEMU agent -> (exitcode, output).

    Needs VM.GuestAgent.Unrestricted on the API token; install-core.sh grants it.
    """
    pid = pve("POST", f"/nodes/{PVE_NODE}/qemu/{vmid}/agent/exec",
              command=list(cmd))["pid"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = pve("GET", f"/nodes/{PVE_NODE}/qemu/{vmid}/agent/exec-status?pid={pid}")
        if st.get("exited"):
            return st.get("exitcode", 0), (st.get("out-data", "")
                                           + st.get("err-data", "")).strip()
        time.sleep(0.5)
    raise TimeoutError(f"the guest agent did not finish in {timeout}s")


def logout_realm(vmid):
    """Empty the Realm's desktop session. Never raises: a Realm that cannot be
    logged out is still worth trying to connect to, and the Gate says why."""
    try:
        code, out = guest_exec(vmid, "/bin/sh", "-c", LOGOUT_SH)
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": code == 0, "detail": out or f"exit {code}"}


def port_open(ip, port, timeout=1.0):
    try:
        with socket.create_connection((ip, port), timeout):
            return True
    except OSError:
        return False


# What a USB class code means in words. A device announces one, or announces
# zero and leaves it to each interface, which is what a headset does: audio
# plus a few buttons. Worth saying out loud on the approval screen: handing a
# Realm an unknown mass-storage device is a different decision from handing it
# a keyboard.
USB_KINDS = {"01": "audio", "02": "network", "03": "keyboard or mouse",
             "05": "controller", "06": "camera", "07": "printer",
             "08": "storage", "0b": "smart card", "0e": "camera",
             "0d": "security key", "e0": "wireless"}


def _sysfs(path):
    try:
        return path.read_text().strip()
    except OSError:              # unplugged between the listing and the read
        return ""


def usb_devices():
    """Every USB device plugged into the host, hubs left out.

    Bus and device numbers change on every replug, so they are no identity at
    all. A serial number is one when the maker bothered to write one. Without a
    serial the make and model still identify the thing wherever it is plugged
    in, which is what you want: approve the keyboard once, not once per socket.
    The physical port is added only to tell two identical devices apart, since
    that is the one case where make and model are not enough.
    """
    found = []
    for entry in sorted(USB_ROOT.glob("[0-9]*")):
        if ":" in entry.name:                      # an interface, not a device
            continue
        vid, pid = _sysfs(entry / "idVendor"), _sysfs(entry / "idProduct")
        if not vid or not pid or _sysfs(entry / "bDeviceClass") == "09":
            continue                               # no id, or a hub
        serial = _sysfs(entry / "serial")
        name = " ".join(part for part in (_sysfs(entry / "manufacturer"),
                                          _sysfs(entry / "product")) if part)
        codes = [_sysfs(entry / "bDeviceClass")]
        codes += [_sysfs(iface / "bInterfaceClass")
                  for iface in sorted(entry.glob("*:*"))]
        kinds = [USB_KINDS[code] for code in codes if code in USB_KINDS]
        found.append({
            "id": f"{vid}:{pid}:{serial}" if serial else f"{vid}:{pid}",
            "vid": vid, "pid": pid, "serial": serial, "port": entry.name,
            "kind": kinds[0] if kinds else "",
            "name": name or f"{vid}:{pid}", "usb": f"{vid}:{pid}"})
    seen = [device["id"] for device in found]
    for device in found:
        if seen.count(device["id"]) > 1:           # two of the same thing
            device["id"] += "@" + device["port"]
    return found


def device_registry():
    try:
        return json.loads(DEVICES_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_registry(registry):
    DEVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DEVICES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    tmp.replace(DEVICES_FILE)


def devices():
    """The host's USB devices, each with the standing decision about it.

    action is "seat" to hand it to whichever Realm holds the Local Seat,
    "never" to leave it alone, and null for one nobody has decided about yet.
    A device in the registry but not plugged in is listed as absent, so a
    decision made last week is still visible.
    """
    registry = device_registry()
    seen = usb_devices()
    listed = []
    for device in seen:
        entry = registry.get(device["id"], {})
        listed.append({**device, "action": entry.get("action"),
                       "role": entry.get("role") or device["kind"],
                       "present": True})
    here = {device["id"] for device in seen}
    for ident, entry in sorted(registry.items()):
        if ident not in here:
            listed.append({"id": ident, "usb": entry.get("usb", ""),
                           "name": entry.get("name", ident),
                           "action": entry.get("action"),
                           "role": entry.get("role", ""), "kind": "",
                           "present": False})
    return listed


def seat_usb():
    """The VID:PIDs the Local Seat should carry, newest decision wins."""
    approved = {ident for ident, entry in device_registry().items()
                if entry.get("action") == "seat"}
    return [device["usb"] for device in usb_devices() if device["id"] in approved]


def vm_config(vmid):
    """The VM's Proxmox configuration. Readable with VM.Audit."""
    return pve("GET", f"/nodes/{PVE_NODE}/qemu/{vmid}/config")


def has_seat(vmid):
    """True if this VM's config claims the GPU."""
    return "hostpci0" in vm_config(vmid)


def is_parked(vmid):
    """True if a live session is waiting in a state file on disk."""
    return bool(vm_config(vmid).get("vmstate"))


def park(vmid):
    """Save the live session to disk and stop the VM, freeing its RAM and CPU.

    Refused while the Realm holds the Local Seat. QEMU can only save a
    passed-through PCI device's state when the device itself supports
    migration, and a consumer RTX 2060 on plain vfio-pci does not. Suspending
    it anyway would write a state file that cannot be restored. Release the
    seat and shut the Realm down instead.
    """
    config = vm_config(vmid)
    if "hostpci0" in config:
        raise SeatError("it holds the Local Seat, and the RTX 2060 cannot have "
                        "its state saved. Release the seat first.")
    pve("POST", f"/nodes/{PVE_NODE}/qemu/{vmid}/status/suspend", todisk=1)


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
        config = vm_config(vmid)
        seat = "hostpci0" in config
    except KeyError:
        raise
    except Exception as exc:
        return {"realm": name, "vmid": vmid, "power": "ERROR", "seat": None,
                "ip": None, "error": str(exc)}
    if status != "running":
        # A Realm suspended to disk is stopped, but its live session is kept in
        # a state file named by the config. Starting it restores that session,
        # so it is not OFF and must not be reported as if nothing were saved.
        power = "PARKED" if config.get("vmstate") else "OFF"
        return {"realm": name, "vmid": vmid, "power": power, "seat": seat, "ip": None}
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
        raw, _, query = self.path.partition("?")
        parts = raw.strip("/").split("/")
        confirmed = urllib.parse.parse_qs(query).get("confirm", [""])[0] == "1"
        try:
            if method == "GET" and parts == ["realms"]:
                return self._reply(200, [realm_state(n) for n in REALMS])
            if method == "GET" and parts == ["seat"]:
                return self._reply(200, {"owner": seat_owner()})
            if method == "GET" and len(parts) == 2 and parts[0] == "realms":
                return self._reply(200, realm_state(parts[1]))
            if (method == "POST" and len(parts) == 3 and parts[0] == "realms"
                    and parts[2] in ("seat", "release")):
                name, vmid = parts[1], REALMS[parts[1]]
                if parts[2] == "release" and not has_seat(vmid):
                    return self._reply(200, {"result": "already headless",
                                             "owner": seat_owner()})
                # Giving a parked Realm the Local Seat changes the hardware its
                # saved session was taken on, and Proxmox would restore that
                # session into hardware it never had. The switcher wakes it and
                # shuts it down first, which throws the saved desktop away. Say
                # so and wait to be told twice.
                if parts[2] == "seat" and not confirmed and is_parked(vmid):
                    return self._reply(409, {
                        "error": f"{name} has a saved session on disk",
                        "warning": f"Giving {name} the Local Seat means waking "
                                   "it, shutting it down and starting it again "
                                   "with the GPU attached. The saved desktop "
                                   "cannot be kept across that change.",
                        "confirm": True})
                # Which USB devices travel with the seat is Core's registry
                # to answer, not a list baked into the switcher.
                extra = seat_usb() if parts[2] == "seat" else []
                action = "switch" if parts[2] == "seat" else "release"
                return self._reply(200, {"result": seat_cmd(action, name, *extra),
                                         "owner": seat_owner()})
            if method == "GET" and parts == ["devices"]:
                return self._reply(200, devices())
            if (method == "POST" and len(parts) == 2 and parts[0] == "devices"):
                action = urllib.parse.parse_qs(query).get("action", [""])[0]
                if action not in ("seat", "never", "forget"):
                    return self._reply(400, {"error": "action must be seat, "
                                                      "never or forget"})
                registry = device_registry()
                ident = urllib.parse.unquote(parts[1])
                if action == "forget":
                    registry.pop(ident, None)
                else:
                    here = {d["id"]: d for d in usb_devices()}
                    known = here.get(ident, registry.get(ident, {}))
                    registry[ident] = {"action": action,
                                       "usb": known.get("usb", ""),
                                       "name": known.get("name", ident),
                                       "role": known.get("role")
                                       or known.get("kind", "")}
                save_registry(registry)
                return self._reply(200, {"id": ident, "action":
                                         None if action == "forget" else action})
            if (method == "POST" and len(parts) == 3 and parts[0] == "realms"
                    and parts[2] == "park"):
                name, vmid = parts[1], REALMS[parts[1]]
                if pve("GET", f"/nodes/{PVE_NODE}/qemu/{vmid}/status/current"
                       )["status"] != "running":
                    return self._reply(200, realm_state(name))   # nothing to save
                park(vmid)
                # Saving the memory takes a while and Proxmox answers at once,
                # so report what was asked for rather than a half-written state.
                return self._reply(202, {"realm": name, "vmid": vmid,
                                         "power": "PARKING", "seat": False,
                                         "ip": None})
            if (method == "POST" and len(parts) == 3 and parts[0] == "realms"
                    and parts[2] == "logout"):
                return self._reply(200, logout_realm(REALMS[parts[1]]))
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
