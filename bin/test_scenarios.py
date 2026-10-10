#!/usr/bin/env python3
"""Hardware scenarios: power and Local Seat must stay independent.

This is NOT a unit test. It starts, stops and restarts real VMs and moves the
RTX 2060 between them, so the physical monitors will switch during it.

    python3 bin/test_scenarios.py --yes              # the ones that do not
    python3 bin/test_scenarios.py --yes --seat       # all of them
    python3 bin/test_scenarios.py --yes --only 5,6

Some of them move the Local Seat: the physical monitors switch, and whatever
is on them is restarted. Those are left out unless --seat is given, so the
rest can be run from a laptop without touching the machine in the other room.
Changing passthrough needs root, so Core must be installed on the Proxmox host
(deploy/install-core.sh). Run from anywhere.
"""
import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent.parent
CFG = {}
for _file in (HERE / ".env",):
    if _file.exists():
        for _line in _file.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                CFG[_k.strip()] = _v.strip().strip("\"'")
CFG.update({k: v for k, v in os.environ.items() if k in ("CORE_URL", "CORE_TOKEN")})
BASE = CFG.get("CORE_URL", "http://127.0.0.1:8080").rstrip("/")


def core(path, method="GET"):
    req = urllib.request.Request(BASE + path, method=method,
                                 headers={"Authorization": "Bearer " + CFG.get("CORE_TOKEN", "")})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path}: {exc.code} {exc.read().decode(errors='replace')}")


def state(name):
    return core(f"/realms/{name}")


def wait_power(name, want, timeout=240):
    deadline = time.monotonic() + timeout
    seen = None
    while time.monotonic() < deadline:
        now = state(name)
        if now["power"] != seen:
            seen = now["power"]
            print(f"      {name}: {seen}{' seat' if now.get('seat') else ''}")
        if now["power"] == want:
            return now
        time.sleep(3)
    raise RuntimeError(f"{name} stayed {seen}, wanted {want}")


def off(name):
    if state(name)["power"] != "OFF":
        core(f"/realms/{name}/stop", "POST")
        wait_power(name, "OFF")


def refused(path, method="POST"):
    """Core said no, and said why. Anything else is a failure."""
    try:
        core(path, method)
    except RuntimeError as exc:
        if "409" in str(exc):
            return str(exc)
        raise
    raise RuntimeError(f"{path} was allowed; it should have been refused")


def check(label, got, want):
    if got != want:
        raise RuntimeError(f"{label}: got {got!r}, wanted {want!r}")
    print(f"      ok: {label} == {want!r}")


def scenario_1():
    """work OFF, no seat -> start -> READY, still no seat."""
    off("work")
    core("/realms/work/release", "POST")
    check("work seat before", state("work")["seat"], False)
    core("/realms/work/start", "POST")
    now = wait_power("work", "READY")
    check("work seat after remote start", now["seat"], False)
    check("work has an ip", bool(now["ip"]), True)


def scenario_2():
    """work OFF but still holding the seat -> start -> seat dropped, READY."""
    core("/realms/work/seat", "POST")        # attaches the seat and starts work
    wait_power("work", "READY")
    off("work")
    check("work keeps the seat in config while off", state("work")["seat"], True)
    core("/realms/work/start", "POST")
    now = wait_power("work", "READY")
    check("remote start dropped the seat", now["seat"], False)
    check("work is reachable", bool(now["ip"]), True)


def scenario_3():
    """work running headless -> take the Local Seat."""
    if state("work")["power"] != "READY":
        core("/realms/work/start", "POST")
        wait_power("work", "READY")
    check("work seat before", state("work")["seat"], False)
    core("/realms/work/seat", "POST")        # restarts work; passthrough is not hot-pluggable
    now = wait_power("work", "READY")
    check("work took the seat", now["seat"], True)
    check("seat owner", core("/seat")["owner"], "work")


def scenario_4():
    """windows owns the seat; work still starts headless and both keep running."""
    core("/realms/windows/seat", "POST")     # takes the seat, stopping work if it held it
    wait_power("windows", "BOOTING", timeout=120)
    check("seat owner", core("/seat")["owner"], "windows")
    off("work")
    core("/realms/work/start", "POST")
    now = wait_power("work", "READY")
    check("work runs without the seat", now["seat"], False)
    check("work is reachable", bool(now["ip"]), True)
    windows = state("windows")
    check("windows still has the seat", windows["seat"], True)
    check("windows is still running", windows["power"] in ("BOOTING", "READY"), True)


def scenario_5():
    """work parks its session to disk and comes back to it."""
    if state("work")["power"] != "READY":
        core("/realms/work/start", "POST")
        wait_power("work", "READY")
    core("/realms/work/park", "POST")
    wait_power("work", "PARKED")
    check("parked is not off", state("work")["power"], "PARKED")
    core("/realms/work/start", "POST")
    now = wait_power("work", "READY")
    check("it came back", bool(now["ip"]), True)
    check("the saved state was used up", state("work")["power"], "READY")


def scenario_6():
    """a parked Realm will not be handed the monitors without being told twice."""
    if state("work")["power"] != "READY":
        core("/realms/work/start", "POST")
        wait_power("work", "READY")
    core("/realms/work/park", "POST")
    wait_power("work", "PARKED")
    why = refused("/realms/work/seat")
    check("it said what would be lost", "saved session" in why, True)
    check("and changed nothing", state("work")["seat"], False)
    check("the session is still parked", state("work")["power"], "PARKED")
    core("/realms/work/start", "POST")       # leave it as we found it
    wait_power("work", "READY")


def scenario_7():
    """personal comes up headless and takes RDP, same as work."""
    off("personal")
    core("/realms/personal/start", "POST")
    now = wait_power("personal", "READY")
    check("personal is reachable", bool(now["ip"]), True)
    check("personal took no seat", now["seat"], False)


def scenario_8():
    """a decision about a device outlives the asking."""
    plugged = [d for d in core("/devices") if d["present"]]
    if not plugged:
        print("      nothing plugged into the host; nothing to decide about")
        return
    device = plugged[0]
    ident = urllib.parse.quote(device["id"], safe="")
    print(f"      using {device['name']} ({device['id']})")
    try:
        core(f"/devices/{ident}?action=never", "POST")
        after = {d["id"]: d for d in core("/devices")}[device["id"]]
        check("it remembered", after["action"], "never")
        check("and it is not offered to the seat", device["usb"] in
              [d["usb"] for d in core("/devices") if d["action"] == "seat"], False)
    finally:
        if device["action"]:
            core(f"/devices/{ident}?action={device['action']}", "POST")
        else:
            core(f"/devices/{ident}?action=forget", "POST")
    check("put back as it was", {d["id"]: d for d in core("/devices")}
          [device["id"]]["action"], device["action"])


def scenario_9():
    """every Realm can say how it reaches the internet. Nothing is changed."""
    for name in ("work", "personal"):
        if state(name)["power"] != "READY":
            print(f"      {name} is not running; skipped")
            continue
        answer = core(f"/realms/{name}/egress")
        check(f"{name} egress", answer["mode"] in
              ("exit-node", "direct", "unknown"), True)
        print(f"      {name}: {answer['mode']} {answer.get('node') or answer.get('detail', '')}")


# True where the physical monitors move and whatever is on them is restarted.
SCENARIOS = {1: (scenario_1, False), 2: (scenario_2, True), 3: (scenario_3, True),
             4: (scenario_4, True), 5: (scenario_5, False), 6: (scenario_6, False),
             7: (scenario_7, False), 8: (scenario_8, False), 9: (scenario_9, False)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--yes", action="store_true", required=True,
                    help="confirm that real VMs may be started, stopped and restarted")
    ap.add_argument("--seat", action="store_true",
                    help="also run the ones that move the physical monitors")
    ap.add_argument("--only", help="comma separated scenario numbers")
    args = ap.parse_args()

    if args.only:
        wanted = [int(n) for n in args.only.split(",")]
    else:
        wanted = [n for n, (_fn, moves) in sorted(SCENARIOS.items())
                  if args.seat or not moves]
        if not args.seat:
            print("leaving out the ones that move the monitors; --seat runs those too")
    failed = []
    for number in wanted:
        run, moves = SCENARIOS[number]
        print(f"\n--- scenario {number}: {run.__doc__}"
              + ("\n    (the monitors will switch)" if moves else ""))
        try:
            run()
            print(f"    PASS scenario {number}")
        except Exception as exc:
            print(f"    FAIL scenario {number}: {exc}")
            failed.append(number)

    print("\nfinal state:")
    for realm in core("/realms"):
        print(f"  {realm['realm']:<10} {realm['power']:<9} "
              f"{'seat' if realm.get('seat') else '    '}  {realm.get('ip') or ''}")
    if failed:
        print(f"\nFAILED: {failed}")
        return 1
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
