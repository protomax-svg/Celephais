#!/usr/bin/env python3
"""Hardware scenarios: power and Local Seat must stay independent.

This is NOT a unit test. It starts, stops and restarts real VMs and moves the
RTX 2060 between them, so the physical monitors will switch during it.

    python3 bin/test_scenarios.py --yes
    python3 bin/test_scenarios.py --yes --only 1,2

Scenarios 2, 3 and 4 change passthrough, which only root can do, so Core must
be installed on the Proxmox host (deploy/install-core.sh). Run from anywhere.
"""
import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
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


SCENARIOS = {1: scenario_1, 2: scenario_2, 3: scenario_3, 4: scenario_4}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--yes", action="store_true", required=True,
                    help="confirm that real VMs may be started, stopped and restarted")
    ap.add_argument("--only", default="1,2,3,4")
    args = ap.parse_args()

    wanted = [int(n) for n in args.only.split(",")]
    failed = []
    for number in wanted:
        print(f"\n--- scenario {number}: {SCENARIOS[number].__doc__}")
        try:
            SCENARIOS[number]()
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
