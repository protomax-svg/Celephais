#!/usr/bin/env python3
"""Check the Gate's own helpers. Run: python3 bin/test_gate.py

Imports celephais-gate by path, because the hyphen keeps it out of a plain
import. It has no side effects at import: main() is behind __main__.
"""
import importlib.machinery
import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_loader(
    "celephais_gate",
    importlib.machinery.SourceFileLoader("celephais_gate",
                                         str(HERE / "celephais-gate")))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

# FreeRDP says what went wrong on stderr. From the overlay there is no
# terminal for that to land in, so the last complaint is carried back with the
# exit code. Max saw a bare "code 145" and it told him nothing.
LOG = """[19:44:19:832] [80850:00013bd4] [ERROR][com.freerdp.core] - [freerdp_tcp_default_connect]: Couldn't get socket ip address
[19:44:19:832] [80850:00013bd4] [WARN][com.freerdp.core] - something milder
[19:44:19:832] [80850:00013bd4] [ERROR][com.freerdp.core.nego] - [nego_connect]: Failed to connect
"""
assert gate._last_error(LOG) == "[nego_connect]: Failed to connect", gate._last_error(LOG)
assert gate._last_error("") == ""
assert gate._last_error(None) == ""
assert gate._last_error("no brackets here at all") == ""
# a line with no "] - " separator must still come back whole, not empty
assert gate._last_error("[ERROR] plain") == "[ERROR] plain"

# FreeRDP first, everywhere. mstsc is the Windows fallback and must stay last.
assert gate.RDP_CLIENTS[0].startswith("sdl-freerdp"), gate.RDP_CLIENTS
assert "mstsc.exe" not in gate.RDP_CLIENTS[:-1], gate.RDP_CLIENTS

# A host that already has a client must never reach the package manager.
import subprocess  # noqa: E402

real_call = subprocess.call
subprocess.call = lambda *a, **k: (_ for _ in ()).throw(
    AssertionError(f"tried to install although a client exists: {a}"))
try:
    if gate.rdp_client():
        assert gate.ensure_rdp_client(lambda _t: None) == gate.rdp_client()
finally:
    subprocess.call = real_call

# The Realm password goes down the client's stdin, never on the command line
# where `ps` would show it to anyone on this machine.
class FakeDone:
    returncode, stderr = 0, ""


seen = {}
# Connecting must never touch the Realm's session. The headless remote desktop
# that deploy/install-realm.sh sets up serves the session already running, so
# anything that logged it out first would throw away the open windows.
gate.core = lambda path, method="GET": seen.update(called=path) or {"seat": False}
real_run = subprocess.run
subprocess.run = lambda cmd, **kw: seen.update(cmd=cmd, kw=kw) or FakeDone
gate.CFG.clear()
gate.CFG.update({"RDP_USER": "workrealm"})
try:
    if gate.rdp_client():
        gate.connect("work", "10.0.0.1", [], lambda _t: None, password="hunter2")
        assert "/from-stdin:force" in seen["cmd"], seen["cmd"]
        assert not any("hunter2" in part for part in seen["cmd"]), "password on argv"
        assert seen["kw"]["input"] == "hunter2\n", seen["kw"]
        assert "/u:workrealm" in seen["cmd"], seen["cmd"]

        # Without one, nothing is fed and FreeRDP is left to ask the old way.
        seen.clear()
        gate.connect("work", "10.0.0.1", [], lambda _t: None)
        assert "/from-stdin:force" not in seen["cmd"], seen["cmd"]
        assert seen["kw"]["input"] == "", seen["kw"]
        assert "called" not in seen, f"connect() called Core: {seen.get('called')}"
finally:
    subprocess.run = real_run
    gate.CFG.clear()

# Each Realm has its own account: `work` is served by `workrealm`, `personal`
# by `max`. A shared RDP_USER would be wrong for one of them, and the Realm
# answers LOGON_FAILURE, which reads exactly like a bad password.
gate.CFG.clear()
gate.CFG.update({"RDP_USER": "shared", "RDP_PASSWORD": "shared-pw",
                 "RDP_USER_WORK": "workrealm", "RDP_PASSWORD_WORK": "work-pw"})
assert gate.realm_login("work") == ("workrealm", "work-pw"), gate.realm_login("work")
assert gate.realm_login("personal") == ("shared", "shared-pw"), gate.realm_login("personal")
gate.CFG.clear()
import getpass  # noqa: E402
assert gate.realm_login("work") == (getpass.getuser(), ""), "no keys: fall back to this user"
gate.CFG.clear()

# Every recipe must name a manager that could exist and a command to run.
for tool, script in gate.PACKAGERS:
    assert script.startswith(tool), f"{tool} recipe does not run {tool}: {script}"
    assert "freerdp" in script, (tool, script)

print("PASS")
