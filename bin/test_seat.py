#!/usr/bin/env python3
"""Check the Local Seat switcher against a fake `qm`. Run: python3 bin/test_seat.py

bin/test_scenarios.py moves the real RTX between real VMs. This one moves
nothing: it puts a pretend `qm` first on PATH, keeps each VM's config in a
file, and runs the real bin/celephais-seat against it.
"""
import os
import pathlib
import subprocess
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SEAT = HERE / "celephais-seat"

# Enough of qm for the switcher: status, config, set (with -delete), start,
# shutdown, and the agent's shutdown. Configs are "key: value" lines in
# $QM_DIR/<vmid>.conf, power in $QM_DIR/<vmid>.status.
FAKE_QM = r"""#!/usr/bin/env bash
set -eu
d=$QM_DIR
cmd=$1; shift
case "$cmd" in
  status)
    echo "status: $(cat "$d/$1.status")"
    if [ "${2:-}" = --verbose ]; then
      if [ -f "$d/$1.asleep" ]; then echo "qmpstatus: suspended"
      else echo "qmpstatus: $(cat "$d/$1.status")"; fi
    fi ;;
  resume) rm -f "$d/$1.asleep"; echo "$1" >> "$d/woken" ;;
  agent) [ ! -f "$d/$1.asleep" ] ;;
  config) cat "$d/$1.conf" ;;
  set)
    vmid=$1; shift
    while [ $# -gt 0 ]; do
      key=${1#-}; val=${2:-}; shift 2
      if [ "$key" = delete ]; then
        for k in ${val//,/ }; do sed -i "/^$k: /d" "$d/$vmid.conf"; done
      else
        sed -i "/^$key: /d" "$d/$vmid.conf"; echo "$key: $val" >> "$d/$vmid.conf"
      fi
    done ;;
  start) echo running > "$d/$1.status" ;;
  shutdown) [ ! -f "$d/$1.asleep" ] && echo stopped > "$d/$1.status" || true ;;
  # A sleeping guest's agent hears nothing, so it cannot be shut down.
  guest) [ "$1" = cmd ] && [ ! -f "$d/$2.asleep" ] && echo stopped > "$d/$2.status" ;;
esac
"""


def setup(root, vms):
    bindir = root / "bin"
    bindir.mkdir()
    qm = bindir / "qm"
    qm.write_text(FAKE_QM)
    qm.chmod(0o755)
    for vmid, (status, conf) in vms.items():
        (root / f"{vmid}.status").write_text(status + "\n")
        (root / f"{vmid}.conf").write_text("".join(f"{k}: {v}\n" for k, v in conf.items()))
    return {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
            "QM_DIR": str(root), "SHUTDOWN_TIMEOUT": "2"}


def conf(root, vmid):
    return dict(line.split(": ", 1) for line in
                (root / f"{vmid}.conf").read_text().splitlines() if ": " in line)


def seat(env, *args):
    done = subprocess.run(["bash", str(SEAT), *args], env=env,
                          capture_output=True, text=True)
    assert done.returncode == 0, (args, done.stdout, done.stderr)
    return done.stdout


with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    env = setup(root, {
        # How `work` was found: stopped, no passthrough left, and no display.
        201: ("stopped", {"name": "work", "vga": "none"}),
        202: ("stopped", {"name": "personal", "vga": "std"}),
        100: ("stopped", {"name": "win11-test", "vga": "virtio"}),
    })

    # A Realm with nothing to release still gets its display back. The early
    # return in detach_seat used to come first, and this is the case it hid.
    seat(env, "release", "work")
    assert conf(root, 201)["vga"] == "std", conf(root, 201)

    # Taking the seat: the RTX and the keyboard go on, the virtual display
    # comes off, and the Realm is started.
    seat(env, "switch", "work", "1532:02a2", "046d:c52b")
    c = conf(root, 201)
    assert c["vga"] == "none", c
    assert c["hostpci0"].startswith("0000:01:00.0") and c["usb0"] == "host=1532:02a2", c
    assert (root / "201.status").read_text().strip() == "running"

    # Moving the seat on: `work` is stopped, stripped, and given its display
    # back, so its next headless start has something to log in on.
    seat(env, "switch", "personal")
    w, p = conf(root, 201), conf(root, 202)
    assert "hostpci0" not in w and "usb0" not in w, w
    assert w["vga"] == "std", w
    assert p["vga"] == "none" and "hostpci0" in p, p

    # Windows is not a Linux Realm and its display is never touched, with the
    # seat or without it.
    seat(env, "switch", "windows")
    assert conf(root, 100)["vga"] == "virtio", conf(root, 100)
    assert conf(root, 202)["vga"] == "std", conf(root, 202)
    seat(env, "release", "personal")       # nothing to do, still fine

    # Moving the seat off a Realm whose guest suspended itself. Neither the
    # agent nor the power button reaches a sleeping guest, so it used to sit
    # there until the switcher gave up. It is woken first, then shut down.
    seat(env, "switch", "personal")
    (root / "202.asleep").write_text("")
    seat(env, "switch", "work")
    assert (root / "woken").read_text().split() == ["202"], "it was never woken"
    assert (root / "202.status").read_text().strip() == "stopped"
    assert "hostpci0" in conf(root, 201) and "hostpci0" not in conf(root, 202)
    (root / "201.status").write_text("stopped\n")
    seat(env, "release", "work")
    for vmid in (100,):
        (root / f"{vmid}.status").write_text("stopped\n")
    seat(env, "release", "windows")
    assert conf(root, 100)["vga"] == "virtio", conf(root, 100)

print("PASS")
