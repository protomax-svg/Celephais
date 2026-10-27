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
# The cause, which FreeRDP states first and then follows with consequences.
assert gate._last_error(LOG) == \
    "[freerdp_tcp_default_connect]: Couldn't get socket ip address", \
    gate._last_error(LOG)
assert gate._last_error("") == ""
assert gate._last_error(None) == ""
assert gate._last_error("no brackets here at all") == ""
# a line with no "] - " separator must still come back whole, not empty
assert gate._last_error("[ERROR] plain") == "[ERROR] plain"

# What Max saw on the overlay when `personal` would not open: the client had
# refused a monitor layout, and the only line reported back was the one it
# wrote on the way out, about a terminal it never had.
NOISY = """[15:16:13:365] [ERROR][com.freerdp.settings] - [freerdp_settings_client_monitors_check_primary_and_origin]: Monitor configuration virtual desktop width must be 200 <= 160 <= 32766
[15:16:13:365] [ERROR][com.freerdp.settings] - [freerdp_settings_check_client_after_preconnect]: Check if the configuration is valid.
[15:16:13:365] [ERROR][com.freerdp.settings] - [log_monitor]: [0] [primary] {0x0-80x80} [3]
[15:16:13:365] [ERROR][com.freerdp.core] - [freerdp_connect_begin]: ERRCONNECT_PRE_CONNECT_FAILED [0x00020001]
[15:16:13:667] [ERROR][com.freerdp.utils.passphrase] - [set_termianl_nonblock]: tcsetattr(TCSANOW) failed with Inappropriate ioctl for device
"""
assert "monitor" in gate._last_error(NOISY), gate._last_error(NOISY)
assert "tcsetattr" not in gate._last_error(NOISY), gate._last_error(NOISY)
# and that log is a client that cannot cope with this machine, not a Realm
# that said no: there is another client worth trying.
assert gate._client_cannot_cope(NOISY)
assert not gate._client_cannot_cope(LOG)

# FreeRDP's Kerberos attempt comes first in every log and means nothing: a
# wrong password must still be reported as the logon failure it is, so the
# overlay asks for the password again.
KERBEROS = """[21:10:14:297] [ERROR][com.winpr.sspi.Kerberos] - [krb_log_context_encryption]: fn (Client not found in Kerberos database [-1765328378])
[21:10:14:437] [ERROR][com.winpr.sspi.Kerberos] - [krb5glue_get_init_creds]: krb5_init_creds_get (Client 'workrealm@ATHENA.MIT.EDU' not found in Kerberos database [-1765328378])
[21:10:15:001] [ERROR][com.freerdp.core] - [nla_recv_pdu]: ERRCONNECT_LOGON_FAILURE [0x00020014]
"""
assert "logon" in gate._last_error(KERBEROS).lower(), gate._last_error(KERBEROS)

# The two codes a person is most likely to meet are said in words. A Realm
# that is up while the machine in front of it has no route - a Tailscale exit
# node holding the whole LAN, say - is the one Max met.
UNREACHABLE = ("[15:20:57:369] [ERROR][com.freerdp.core] - [get_next_addrinfo]: "
               "ERRCONNECT_CONNECT_FAILED [0x00020006]")
# Not a port number: a Realm's own desktop answers on 3389 and the one it
# keeps for an agent on a port of its own, and the sentence is for both.
assert "RDP port" in gate._last_error(UNREACHABLE), gate._last_error(UNREACHABLE)
# and a logon failure is left alone: the overlay reads the word and asks for
# the password again, which no sentence here would do better.
assert "logon" in gate._last_error(
    "[ERROR][com.freerdp.core] - ERRCONNECT_LOGON_FAILURE [0x0002000C]").lower()

# FreeRDP first, everywhere. mstsc is the Windows fallback and must stay last.
assert gate.RDP_CLIENTS[0].startswith("sdl-freerdp"), gate.RDP_CLIENTS
assert "mstsc.exe" not in gate.RDP_CLIENTS[:-1], gate.RDP_CLIENTS

# Every client on the machine is on the list to try, in that same order.
assert gate.rdp_clients()[:1] == ([gate.rdp_client()] if gate.rdp_client() else [])
assert all(found for found in gate.rdp_clients())

# A host that already has a client must never reach the package manager.
import contextlib  # noqa: E402
import subprocess  # noqa: E402

real_call = subprocess.call
subprocess.call = lambda *a, **k: (_ for _ in ()).throw(
    AssertionError(f"tried to install although a client exists: {a}"))
try:
    if gate.rdp_client():
        assert gate.ensure_rdp_client(lambda _t: None) == gate.rdp_client()
        assert gate.ensure_rdp_clients(lambda _t: None) == gate.rdp_clients()
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

        # One monitor is the absence of /multimon, not a monitor named by
        # number: a named monitor would have to be checked against what is
        # plugged in, and /monitors:1 fails on a laptop on its own.
        seen.clear()
        gate.connect("work", "10.0.0.1", [], lambda _t: None, password="x",
                     one_monitor=True)
        assert "/multimon" not in seen["cmd"], seen["cmd"]
        assert "/f" in seen["cmd"], "still fullscreen, just on one screen"
        assert not any(part.startswith("/monitors") for part in seen["cmd"]), \
            seen["cmd"]
        seen.clear()
        gate.connect("work", "10.0.0.1", [], lambda _t: None, password="x")
        assert "/multimon" in seen["cmd"], "every monitor is still the default"

        # Without one, nothing is fed and FreeRDP is left to ask the old way.
        seen.clear()
        gate.connect("work", "10.0.0.1", [], lambda _t: None)
        assert "/from-stdin:force" not in seen["cmd"], seen["cmd"]
        assert seen["kw"]["input"] == "", seen["kw"]
        assert "called" not in seen, f"connect() called Core: {seen.get('called')}"
finally:
    subprocess.run = real_run
    gate.CFG.clear()

# A client that cannot use this machine's monitors is not an answer from the
# Realm. `personal` would not open on Max's desktop because sdl-freerdp3 reads
# every monitor there as 80x80 and refuses its own layout; xfreerdp3, two
# lines further down the same list, opens it. So the next client is tried.
class Refuses:
    returncode, stderr = 134, NOISY


class Opens:
    returncode, stderr = 0, ""


class Refused:
    returncode, stderr = 131, LOG


tried = []
real_run = subprocess.run
real_keep = gate._keep_log
real_ensure = gate.ensure_rdp_clients
gate._keep_log = lambda name, text: None
try:
    gate.ensure_rdp_clients = lambda tell=print: ["/usr/bin/one", "/usr/bin/two"]
    subprocess.run = lambda cmd, **kw: (tried.append(cmd[0])
                                        or (Opens if len(tried) > 1 else Refuses))
    code, why = gate.connect("work", "10.0.0.1", [], lambda _t: None,
                             password="hunter2")
    assert (code, why) == (0, ""), (code, why)
    assert tried == ["/usr/bin/one", "/usr/bin/two"], tried

    # A session the person in it ended is not a failure. What Max saw after
    # logging off `work`: FreeRDP's exit code, and the Kerberos line beside it.
    class LoggedOff:
        returncode, stderr = 12, KERBEROS.split("\n")[0] + """
    [21:10:39:480] [ERROR][com.freerdp.core] - [rdp_set_error_info]: ERRINFO_LOGOFF_BY_USER [0x0001000C]
    """


    tried.clear()
    subprocess.run = lambda cmd, **kw: tried.append(cmd[0]) or LoggedOff
    code, why = gate.connect("work", "10.0.0.1", [], lambda _t: None, password="x")
    assert (code, why) == (0, ""), (code, why)
    assert tried == ["/usr/bin/one"], "a session that ended is not a reason to try another client"

    # A Realm that said no says it to every client. Asking the rest would only
    # have it refuse three times, so the first answer is the answer.
    tried.clear()
    subprocess.run = lambda cmd, **kw: tried.append(cmd[0]) or Refused
    code, why = gate.connect("work", "10.0.0.1", [], lambda _t: None,
                             password="hunter2")
    assert tried == ["/usr/bin/one"], tried
    assert code == 131 and "socket ip address" in why, (code, why)
finally:
    subprocess.run = real_run
    gate._keep_log = real_keep
    gate.ensure_rdp_clients = real_ensure
    gate.CFG.clear()

# Over the Gate's own network the Realm is found first, and a Realm that never
# answers is said in words - before any RDP client is launched at a port that
# would only have timed out inside it.
launched = []
real_run, real_reach = subprocess.run, gate.celephais_net.reach
subprocess.run = lambda cmd, **kw: launched.append(cmd) or Opens


def never(proxy, host, port, wait=None, every=5):
    raise gate.celephais_net.NetError(f"{host}:{port} did not answer")


gate.NET.update(socks5="127.0.0.1:1")
gate.celephais_net.reach = never
try:
    said = []
    code, why = gate.connect("work", "100.99.187.90", [], said.append,
                             password="hunter2")
    assert code == 1 and "did not answer at 100.99.187.90" in why, (code, why)
    assert not launched, f"a client was launched at an unreachable Realm: {launched}"
    assert any("finding the way to work" in line for line in said), said

    # and Core the same way: one sentence naming it, not a socket error
    try:
        gate.reach_core(lambda _t: None)
        raise AssertionError("an unreachable Core was reported as reached")
    except SystemExit as exc:
        assert "Core did not answer" in str(exc), exc
finally:
    subprocess.run, gate.celephais_net.reach = real_run, real_reach
    gate.NET.clear()
    gate.CFG.clear()

# Windows' own Remote Desktop takes no password on its command line and none on
# stdin, so it asked again, in a Windows Security dialog of its own, for an
# account the Gate's sign-in screen had just been given. It is lent that
# sign-in as a Windows credential instead, for as long as the client runs.
assert gate._termsrv("127.0.0.1:50123") == ["TERMSRV/127.0.0.1",
                                            "TERMSRV/127.0.0.1:50123"]
assert gate._termsrv("100.99.187.90") == ["TERMSRV/100.99.187.90"]

lent, ran = [], []
real_cred, real_ensure, real_run = (gate.windows_credential,
                                    gate.ensure_rdp_clients, subprocess.run)


@contextlib.contextmanager
def lend(targets, user, password):
    lent.append((list(targets), user, password))
    yield


warned = []
real_warning = gate.mstsc_without_certificate_warning


@contextlib.contextmanager
def no_warning():
    warned.append(True)
    yield


gate.windows_credential = lend
gate.mstsc_without_certificate_warning = no_warning
subprocess.run = lambda cmd, **kw: ran.append((cmd, kw)) or Opens
try:
    gate.ensure_rdp_clients = lambda tell=print: [r"C:\Windows\System32\mstsc.exe"]
    gate.connect("work", "100.99.187.90", [], lambda _t: None,
                 user="workrealm", password="hunter2")
    assert lent == [(["TERMSRV/100.99.187.90"], "workrealm", "hunter2")], lent
    cmd, kw = ran[-1]
    assert not any("hunter2" in part for part in cmd), cmd
    assert kw["input"] == "", "mstsc reads no stdin; the password must not go there"
    # and its "identity cannot be verified" question is skipped for this
    # session only, the way FreeRDP is told /cert:ignore
    assert warned == [True], warned

    # FreeRDP is handed nothing of the kind: it reads the password itself.
    lent.clear()
    gate.ensure_rdp_clients = lambda tell=print: ["/usr/bin/xfreerdp3"]
    gate.connect("work", "100.99.187.90", [], lambda _t: None,
                 user="workrealm", password="hunter2")
    assert lent == [([], "workrealm", "hunter2")], lent
    assert ran[-1][1]["input"] == "hunter2\n", ran[-1][1]
    assert warned == [True], "FreeRDP has /cert:ignore and needs no registry"
finally:
    gate.windows_credential, gate.ensure_rdp_clients = real_cred, real_ensure
    gate.mstsc_without_certificate_warning = real_warning
    subprocess.run = real_run
    gate.CFG.clear()

# And off Windows the real ones do nothing at all.
with gate.windows_credential(["TERMSRV/x"], "u", "p") as nothing:
    assert nothing is None
with gate.mstsc_without_certificate_warning() as nothing:
    assert nothing is None

# A yes/no setting is written by a person, so all the ways a person writes yes
# mean yes, and an empty one does not.
gate.CFG.clear()
for written in ("1", "true", "TRUE", "yes", "on", " on "):
    gate.CFG["ONE_MONITOR"] = written
    assert gate.flag("ONE_MONITOR") is True, written
for written in ("", "0", "no", "off", "maybe"):
    gate.CFG["ONE_MONITOR"] = written
    assert gate.flag("ONE_MONITOR") is False, written
gate.CFG.clear()
assert gate.flag("ONE_MONITOR") is False, "nothing written at all"

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

# A trusted machine may keep the vault key in a file. No file named, or a
# file that is not there, means ask: the USB stick must never skip the screen.
import tempfile  # noqa: E402

assert gate.KEY_FILE is None, "no key file unless one is named"
assert gate.stored_password() == ""
with tempfile.TemporaryDirectory() as tmp:
    kept = pathlib.Path(tmp) / "key"
    gate.KEY_FILE = kept
    assert gate.stored_password() == "", "a missing file must not raise"
    kept.write_text("open sesame\n")
    assert gate.stored_password() == "open sesame", gate.stored_password()
gate.KEY_FILE = None

# Core's refusals land on the overlay as a sentence, not as a line of JSON.
# Max pressed park while work held the seat and read
# `core said 409: {"error": "local seat: ..."}` across the screen.
assert gate._core_error(409, '{"error": "it holds the Local Seat"}') \
    == "it holds the Local Seat"
assert gate._core_error(500, "upstream died") == "core said 500: upstream died"
assert gate._core_error(500, "{}") == "core said 500: {}"

# Every recipe must name a manager that could exist and a command to run.
for tool, script in gate.PACKAGERS:
    assert script.startswith(tool), f"{tool} recipe does not run {tool}: {script}"
    assert "freerdp" in script, (tool, script)

# ---------------------------------------------------------------------------
# The Gate must not build a network it already has. Core listens on nothing
# but the Proxmox host's Tailscale address, so an answer from it proves this
# machine is on the tailnet - and on Windows the Gate's own tailnet cannot
# come up at all without administrator rights. Max was held at his own key
# screen on a laptop that had Tailscale installed and could reach Core.
# ---------------------------------------------------------------------------
import contextlib  # noqa: E402
import http.server  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402

import celephais_net  # noqa: E402
import celephais_vault  # noqa: E402


def answering(code):
    class Answer(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"[]")

        def log_message(self, *_a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Answer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


LIVE = answering(200)
REFUSING = answering(401)
dead = socket.socket()
dead.bind(("127.0.0.1", 0))
DEAD = f"http://127.0.0.1:{dead.getsockname()[1]}"
dead.close()

gate.CFG.update({"CORE_URL": LIVE, "CORE_TOKEN": "t"})
assert gate.core_reachable(timeout=5) is True
# A refusal is still an answer. A token Core dislikes is not a reason to build
# a network; core() will say what is actually wrong with it.
gate.CFG["CORE_URL"] = REFUSING
assert gate.core_reachable(timeout=5) is True
gate.CFG["CORE_URL"] = DEAD
assert gate.core_reachable(timeout=2) is False

# The overlay makes the same decision, which is where it mattered: unlock
# brought up a tailnet before anyone asked whether one was needed.
real_load, real_tailnet = celephais_vault.load, celephais_net.tailnet
real_reach_net = celephais_net.reach
try:
    celephais_vault.load = lambda _path, _pw: {"CORE_URL": LIVE,
                                               "CORE_TOKEN": "t",
                                               "TS_AUTHKEY": "tskey-auth-x"}

    def refuse(*_a, **_k):
        raise AssertionError("built a tailnet although Core answered")

    celephais_net.tailnet = refuse
    stack, hooks = gate.ui_session()
    with stack:
        assert hooks["unlock"]("a password") is True

    # And when Core cannot be reached, it still builds one: a stranger's
    # laptop is what the private tailnet is for.
    built = []

    @contextlib.contextmanager
    def fake_tailnet(*args, **_k):
        built.append(args)
        yield {"socks5": "127.0.0.1:1", "http_proxy": "http://127.0.0.1:1"}

    def no_way(*_a, **_k):
        raise celephais_net.NetError("did not answer")

    celephais_net.tailnet = fake_tailnet
    celephais_net.reach = no_way
    celephais_vault.load = lambda _path, _pw: {"CORE_URL": DEAD,
                                               "CORE_TOKEN": "t",
                                               "TS_AUTHKEY": "tskey-auth-x"}
    stack, hooks = gate.ui_session()
    with stack:
        try:
            hooks["unlock"]("a password")
        except SystemExit:
            pass                      # Core is not there; that is the point
    assert built, "no tailnet although Core could not be reached"
finally:
    celephais_vault.load, celephais_net.tailnet = real_load, real_tailnet
    celephais_net.reach = real_reach_net
    gate.CFG.clear()
    gate.NET.clear()

print("PASS")
