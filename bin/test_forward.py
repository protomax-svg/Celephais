#!/usr/bin/env python3
"""Check the SOCKS5 forwarder. Run: python3 bin/test_forward.py

Uses a fake SOCKS5 server, so it needs no network and no Tailscale.
"""
import pathlib
import socket
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import celephais_net as n  # noqa: E402

PAYLOAD = b"celephais" * 9000          # big enough to need several reads


KEEP = []           # hold the listeners open for the whole run


def echo_server():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(8)
    KEEP.append(srv)

    def echo(conn):
        with conn:
            while True:
                data = conn.recv(65536)
                if not data:
                    return
                conn.sendall(data)

    def run():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=echo, args=(conn,), daemon=True).start()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def fake_socks(upstream_port, refuse=False, chunked=False):
    """A SOCKS5 server that speaks the parts we use, badly on purpose."""
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(4)
    KEEP.append(srv)
    seen = []

    def run():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    def handle(conn):
        greeting = n._recv_exact(conn, 2)
        n._recv_exact(conn, greeting[1])
        conn.sendall(b"\x05\x00")
        header = n._recv_exact(conn, 4)
        length = n._recv_exact(conn, 1)[0]
        host = n._recv_exact(conn, length).decode()
        port = int.from_bytes(n._recv_exact(conn, 2), "big")
        seen.append((host, port))
        if refuse:
            conn.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)
            conn.close()
            return
        reply = b"\x05\x00\x00\x03\x09localhost" + b"\x00\x00"   # domain form
        if chunked:
            for byte in reply:                 # one byte at a time, on purpose
                conn.sendall(bytes([byte]))
        else:
            conn.sendall(reply)
        upstream = socket.create_connection(("127.0.0.1", upstream_port))
        threading.Thread(target=n._splice, args=(conn, upstream), daemon=True).start()
        threading.Thread(target=n._splice, args=(upstream, conn), daemon=True).start()

    threading.Thread(target=run, daemon=True).start()
    return f"127.0.0.1:{srv.getsockname()[1]}", seen


def roundtrip(proxy):
    port, listener = n.forward(proxy, "work.realm", 3389)
    try:
        with socket.create_connection(("127.0.0.1", port), 10) as sock:
            got = b""
            try:
                sock.sendall(PAYLOAD)
                while len(got) < len(PAYLOAD):
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    got += chunk
            except OSError:
                # A refused connection dies as EOF or as a reset, depending on
                # the system. Either way it carried nothing.
                pass
        return got
    finally:
        listener.close()


upstream = echo_server()

proxy, seen = fake_socks(upstream)
assert roundtrip(proxy) == PAYLOAD, "the payload did not survive the forwarder"
assert seen == [("work.realm", 3389)], seen       # the name went to the proxy, not an IP

# a reply that dribbles in one byte at a time must still be parsed
proxy, _ = fake_socks(upstream, chunked=True)
assert roundtrip(proxy) == PAYLOAD, "a split SOCKS reply broke the handshake"

# a refusal must not hang or crash the listener
proxy, _ = fake_socks(upstream, refuse=True)
assert roundtrip(proxy) == b"", "a refused connection should carry nothing"

try:
    n._socks_connect("127.0.0.1:1", "work.realm", 3389, timeout=3)
    raise AssertionError("a dead proxy was accepted")
except OSError:
    pass

# The fake servers above are rude on purpose, and their threads outlive the
# tests that started them. Given the few seconds the reach() tests below take,
# they get round to failing out loud about connections those tests had long
# finished with. Their complaints are the fakes', not the forwarder's, whose
# own threads (serve and _splice inside forward) still report as before.
_report = threading.excepthook
threading.excepthook = lambda failed: (
    None if failed.thread.name.endswith(("(handle)", "(echo)"))
    else _report(failed))

# The handshake's timeout must not outlive the handshake. Left on the socket,
# it ended a spliced RDP session the first time the Realm said nothing for
# ten seconds.
proxy, _ = fake_socks(upstream)
live = n._socks_connect(proxy, "work.realm", 3389, timeout=3)
assert live.gettimeout() is None, live.gettimeout()
live.close()


def slow_socks(silent_for):
    """A SOCKS proxy that leaves CONNECTs unanswered until `silent_for` has
    passed, the way tailscaled does while it is still finding the peer."""
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(16)
    KEEP.append(srv)
    ready_at = time.monotonic() + silent_for
    tries = []

    def handle(conn):
        greeting = n._recv_exact(conn, 2)
        n._recv_exact(conn, greeting[1])
        conn.sendall(b"\x05\x00")
        n._recv_exact(conn, 4)
        n._recv_exact(conn, n._recv_exact(conn, 1)[0] + 2)
        tries.append(time.monotonic())
        if time.monotonic() < ready_at:
            time.sleep(60)                     # hang, like a path not there yet
            return
        conn.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)
        conn.close()

    def run():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=run, daemon=True).start()
    return f"127.0.0.1:{srv.getsockname()[1]}", tries


# A network that is up but has not found the peer yet: the first tries hang,
# and the Gate keeps asking with short ones until one gets through, instead of
# spending one long try and giving up just before the path was there.
proxy, tries = slow_socks(silent_for=2.5)
took = n.reach(proxy, "100.112.229.100", 8080, wait=20, every=1)
assert 2 <= took < 8, took
assert len(tries) >= 2, f"it gave up waiting after one try: {tries}"

# and a peer that never answers ends in a sentence, at the deadline, not later
proxy, _ = slow_socks(silent_for=999)
began = time.monotonic()
try:
    n.reach(proxy, "100.112.229.100", 8080, wait=2, every=1)
    raise AssertionError("a peer that never answered was reported as reached")
except n.NetError as exc:
    assert "did not answer" in str(exc), exc
assert time.monotonic() - began < 5, "it overran its deadline"

# A node that registered and never said Running: the laptop's whole
# afternoon. What Tailscale knew about why used to be thrown away; now its
# first health warning reaches the user, and everything - warnings and the
# daemon's last lines, redacted - is kept in tailnet.log for afterwards.
import os                                                  # noqa: E402
import tempfile as _tempfile                               # noqa: E402

with _tempfile.TemporaryDirectory() as cache:
    was = os.environ.get("XDG_CACHE_HOME")
    os.environ["XDG_CACHE_HOME"] = cache
    tail = type("tail", (), {"lines": [
        "magicsock: derp-12 connect failed: tls: handshake failure",
        "control: up --auth-key=tskey-auth-SECRET"]})()
    try:
        said = n._diagnosis({"BackendState": "Starting",
                             "Self": {"Relay": "nue"},
                             "Health": ["Tailscale could not connect to the "
                                        "'Nuremberg' relay server."]}, tail)
        assert said.startswith("Tailscale could not connect"), said
        kept = pathlib.Path(cache, "celephais", "tailnet.log").read_text()
        assert "BackendState: Starting" in kept and "home relay: nue" in kept, kept
        assert "handshake failure" in kept, kept
        assert "SECRET" not in kept, "a key reached the log"
        # nothing to say is said as nothing, not as an empty quotation
        assert n._diagnosis({}, type("tail", (), {"lines": []})()) == ""
    finally:
        if was is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = was

# The packed Tailscale keeps its .exe suffix on Windows. Looking for the bare
# name found nothing there, so a clean Windows host fell back to a PATH search
# that was always going to fail.
import tempfile                                           # noqa: E402

with tempfile.TemporaryDirectory() as packed:
    pathlib.Path(packed, "tailscaled.exe").write_bytes(b"")
    was_platform, was_frozen, was_meipass = (
        sys.platform, getattr(sys, "frozen", None), getattr(sys, "_MEIPASS", None))
    sys.platform, sys.frozen, sys._MEIPASS = "win32", True, packed
    try:
        found = n.bundled("tailscaled")
        assert found.endswith("tailscaled.exe"), found
    finally:
        sys.platform = was_platform
        for name, value in (("frozen", was_frozen), ("_MEIPASS", was_meipass)):
            if value is None:
                delattr(sys, name)
            else:
                setattr(sys, name, value)

# Windows refuses a pipe of our own unless tailscaled is told it is being run
# by hand. Without this the Gate died on the password screen with the refusal
# printed across it.
import os  # noqa: E402

real_name = os.name
try:
    os.name = "nt"
    win = n.daemon_argv("tailscaled.exe", r"\\.\pipe\celephais-1", 1080, 1081)
    assert "--windows-mode=dev" in win, win
    assert win.index("--windows-mode=dev") == 1, f"it must come first: {win}"
    os.name = "posix"
    nix = n.daemon_argv("tailscaled", "/tmp/sock", 1080, 1081)
    assert "--windows-mode=dev" not in nix, nix
    # The rest of the command line is the same on both.
    assert win[2:] == [a.replace("/tmp/sock", r"\\.\pipe\celephais-1")
                       for a in nix[1:]], (win, nix)

    # And `up` must ask for unattended mode on Windows, or tailscaled switches
    # the network off the moment `tailscale up` exits. What the laptop showed:
    #   control: doLogin(regen=false, hasUrl=false)        (17 times)
    #   health(warnable=wantrunning-false): error: Tailscale is stopped.
    #   control: authRoutine: exiting
    os.name = "nt"
    win_up = n.up_argv(["tailscale.exe"], "celephais-gate", "tag:celephais-gate")
    assert "--unattended" in win_up, win_up
    os.name = "posix"
    nix_up = n.up_argv(["tailscale"], "celephais-gate", "tag:celephais-gate")
    assert "--unattended" not in nix_up, "the flag does not exist off Windows"
    assert [a for a in win_up[1:] if a != "--unattended"] == nix_up[1:], \
        (win_up, nix_up)
finally:
    os.name = real_name

# ---------------------------------------------------------------------------
# Nothing a subprocess says, and nothing it was given, may reach the user.
# Max watched tailscale's own output scroll across the Gate's fullscreen
# screen, auth key and all.
# ---------------------------------------------------------------------------
import contextlib  # noqa: E402
import io  # noqa: E402
import subprocess  # noqa: E402

KEY = "tskey-auth-kN0tAR3alK3y-leakme"

# The key goes, whatever shape it arrives in: on its own, or quoted back
# inside the command line Go programs like to print when they are unhappy.
assert KEY not in n.redact(f"tailscale up --auth-key={KEY} --hostname=gate")
assert KEY not in n.redact(f"error: {KEY} is not valid")
assert "[redacted]" in n.redact(f"--client-secret={KEY}")
assert n.redact(n.redact(f"--auth-key={KEY}")) == n.redact(f"--auth-key={KEY}")
assert n.redact("") == "" and n.redact(None) == ""
# What is not a secret must survive, or the message stops being useful.
assert "--hostname=gate" in n.redact("tailscale up --hostname=gate")

# The sentence the user reads is ours. The output that chose it never travels
# with it: a key quoted inside that output would ride along.
said = f"tailscale up --auth-key={KEY}: invalid key: unauthorized"
sentence = n._explain(said, "fallback")
assert "tskey" not in sentence and "auth-key" not in sentence, sentence
assert sentence != "fallback", sentence
# The Windows laptop: tailscaled asks for a named pipe owned by the
# Administrators group, a plain account may not create one, and the Gate's key
# screen filled up with Go log output instead of saying what to do about it.
DENIED = (r"safesocket.Listen: namedpipe.Listen: open "
          r"\\.\pipe\celephais-22740: Access is denied.")
admin = n._explain(DENIED, "the Gate's private network could not start.")
assert "administrator" in admin.lower(), admin
assert "pipe" not in admin.lower() and "22740" not in admin, admin
assert n._explain("something nobody predicted", "fallback") == "fallback"
assert n._explain("", "fallback") == "fallback" and n._explain(None, "f") == "f"

# ---------------------------------------------------------------------------
# Readiness is "does anything accept", not "does the name exist". Max got "the
# Gate's private network did not start in time" on Windows 11 while tailscaled
# was running the whole minute: the control socket there is a named pipe, and
# watching for its name to turn up is not a question os.stat can answer.
# ---------------------------------------------------------------------------
import os as _os  # noqa: E402
import tempfile  # noqa: E402

_dir = tempfile.mkdtemp(prefix="celephais-ready-")
_sock = _os.path.join(_dir, "tailscaled.sock")
assert n._control_ready(_sock) is False, "nothing is there at all"

# A socket file that exists but has nobody listening is the POSIX race: the
# name appears first, and the CLI call used to be allowed to arrive in between.
_bare = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
_bare.bind(_sock)
assert _os.path.exists(_sock), "the name is there"
assert n._control_ready(_sock) is False, "the name is there, the listener is not"
_bare.listen(1)
assert n._control_ready(_sock) is True, "something accepts now"
_bare.close()

# The Windows branch, with `open` standing in for the pipe. Missing is not
# ready; busy and access-denied both mean the pipe is there, which is what was
# being asked - the second one then fails further down with its own sentence
# rather than timing out here.
class _Winerr(OSError):
    pass


def _opens(exc):
    def fake(*_a, **_k):
        if exc is None:
            class Handle:
                def __enter__(self):
                    return self

                def __exit__(self, *_e):
                    return False
            return Handle()
        raise exc
    return fake


_busy = _Winerr("all pipe instances are busy")
_busy.winerror = 231
_other = _Winerr("something else")
_other.winerror = 5555

_real_name, _real_open = _os.name, n.open if hasattr(n, "open") else None
try:
    n.os.name = "nt"
    for raises, expected in ((None, True),
                             (FileNotFoundError(2, "no pipe"), False),
                             (PermissionError(5, "access is denied"), True),
                             (_busy, True),
                             (_other, False)):
        n.open = _opens(raises)
        got = n._control_ready(r"\.\pipe\celephais-1")
        assert got is expected, (raises, got, expected)
finally:
    n.os.name = _real_name
    if _real_open is None:
        del n.open
    else:
        n.open = _real_open

# Windows: no console, ever. Neither the Gate's own, which the overlay does
# not cover, nor one of the child's, which would flash across the screen.
assert n._window_flags(True) == 0x08000000, n._window_flags(True)
assert n._window_flags(False) == 0
quiet = n._quiet({"capture_output": True})
assert quiet["shell"] is False, quiet
assert quiet["stdin"] is subprocess.DEVNULL, quiet
assert quiet["capture_output"] is True, quiet

# Detail reaches a terminal that asked for it, and no other place.
class FakeTTY(io.StringIO):
    def isatty(self):
        return True

for show, expected in ((True, True), (False, False)):
    was, n.SHOW_DETAIL = n.SHOW_DETAIL, show
    try:
        screen = FakeTTY()
        with contextlib.redirect_stderr(screen):
            kept = n._note(f"up failed --auth-key={KEY}")
        assert (len(screen.getvalue()) > 0) is expected, (show, screen.getvalue())
        assert KEY not in screen.getvalue(), screen.getvalue()
        assert KEY not in kept, kept          # the caller gets it redacted too
    finally:
        n.SHOW_DETAIL = was

# The whole path, end to end: a `tailscale up` that fails and quotes its own
# command line back must leave the Gate with a sentence and nothing else.
LAUNCHED = []


class FakeDaemon:
    """tailscaled that starts, says a little and keeps running.

    It listens on the control socket for real. The Gate asks whether anything
    accepts there, not whether the name exists - that is the only form of the
    question Windows can answer - so a fake that touches the file is not a
    daemon that anything can connect to.
    """

    def __init__(self, argv, **kwargs):
        LAUNCHED.append((argv, kwargs))
        sock = next(a.split("=", 1)[1] for a in argv if a.startswith("--socket="))
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(sock)
        self.listener.listen(1)
        KEEP.append(self.listener)
        self.stderr = io.StringIO("logtail started\n")

    def poll(self):
        return None

    def terminate(self):
        self.listener.close()

    def wait(self, timeout=None):
        return 0


class FakeUp:
    """`tailscale up` refusing the key, the way a Go program does it: by
    printing the command line it was given."""
    returncode = 1
    stdout = ""

    def __init__(self, argv, **kwargs):
        LAUNCHED.append((argv, kwargs))
        self.stderr = f"invalid key: {' '.join(argv)}"


real_popen, real_run, real_bundled = subprocess.Popen, subprocess.run, n.bundled
was_detail, n.SHOW_DETAIL = n.SHOW_DETAIL, False
try:
    n.bundled = lambda name: f"/nonexistent/{name}"
    subprocess.Popen = FakeDaemon
    subprocess.run = FakeUp
    try:
        with n.tailnet(KEY, "celephais-gate"):
            raise AssertionError("a refused key was accepted")
    except n.NetError as exc:
        complaint = str(exc)
        assert KEY not in complaint, complaint
        assert "tskey" not in complaint, complaint
        assert "--auth-key" not in complaint and "/nonexistent" not in complaint, complaint
        assert "invalid key:" not in complaint, "raw output reached the user"
        assert complaint[0].islower() and complaint.endswith("."), complaint
finally:
    subprocess.Popen, subprocess.run, n.bundled = real_popen, real_run, real_bundled
    n.SHOW_DETAIL = was_detail

# ---------------------------------------------------------------------------
# A daemon that is refused its control socket keeps running and never opens
# one. The Gate sat out the whole minute and then blamed the clock, with the
# diagnosis sitting unread in _Tail the entire time.
# ---------------------------------------------------------------------------
REFUSAL = (r"safesocket.Listen: namedpipe.Listen: open "
           r"\.\pipe\celephais-22740: Access is denied.")


class RefusedDaemon(FakeDaemon):
    """tailscaled that starts, cannot have its socket, and stays up anyway."""

    def __init__(self, argv, **kwargs):
        LAUNCHED.append((argv, kwargs))
        self.listener = None                  # nothing ever accepts
        self.stderr = io.StringIO(
            "syspolicy: GP lock not acquired (Access denied.); reading policy\n"
            "control: dial tcp 1.2.3.4:443: i/o timeout\n"
            + REFUSAL + "\n")

    def terminate(self):
        pass


# Only the refusal of its own socket may end the wait early. The other two
# lines above are routine: tailscaled says both of them on the way to a
# network that comes up perfectly well, and a bare "access is denied" needle
# matched the first one.
assert n._recognised("syspolicy: GP lock not acquired (Access denied.)",
                     n._FATAL) is None
assert n._recognised("control: dial tcp 1.2.3.4:443: i/o timeout",
                     n._FATAL) is None
assert "administrator" in n._recognised(REFUSAL, n._FATAL).lower()

real_popen, real_run, real_bundled = subprocess.Popen, subprocess.run, n.bundled
was_detail, n.SHOW_DETAIL = n.SHOW_DETAIL, False
try:
    n.bundled = lambda name: f"/nonexistent/{name}"
    subprocess.Popen = RefusedDaemon
    subprocess.run = FakeUp
    started = time.monotonic()
    try:
        with n.tailnet(KEY, "celephais-gate"):
            raise AssertionError("a daemon with no socket was accepted")
    except n.NetError as exc:
        spent = time.monotonic() - started
        assert "administrator" in str(exc).lower(), exc
        assert "pipe" not in str(exc).lower() and "22740" not in str(exc), exc
        # It must not have waited the daemon out: READY_TIMEOUT is a minute.
        assert spent < 10, f"waited {spent:.1f}s before saying so"
finally:
    subprocess.Popen, subprocess.run, n.bundled = real_popen, real_run, real_bundled
    n.SHOW_DETAIL = was_detail

# `tailscale up` taking too long must not hand its exception straight on:
# TimeoutExpired prints the command it was given, and on Windows the key is
# one of those arguments.
def slow_up(argv, **kwargs):
    raise subprocess.TimeoutExpired(argv, 1)


real_popen, real_run, real_bundled = subprocess.Popen, subprocess.run, n.bundled
try:
    n.bundled = lambda name: f"/nonexistent/{name}"
    subprocess.Popen, subprocess.run = FakeDaemon, slow_up
    try:
        with n.tailnet(KEY, "celephais-gate"):
            raise AssertionError("a timeout was accepted")
    except n.NetError as exc:
        assert KEY not in str(exc) and "tskey" not in str(exc), exc
finally:
    subprocess.Popen, subprocess.run, n.bundled = real_popen, real_run, real_bundled

# Every one of them was launched the same way: no shell to parse an argument,
# no stdin to wait on, and the output on a pipe instead of the user's screen.
assert len(LAUNCHED) == 4, [argv for argv, _ in LAUNCHED]
#   the refused-key run: a daemon and a `tailscale up`
#   the refused-socket run: a daemon, and nothing after it
#   the slow-`up` run: a daemon
for argv, kwargs in LAUNCHED:
    assert kwargs["shell"] is False, kwargs
    assert kwargs["stdin"] is subprocess.DEVNULL, kwargs
    assert kwargs.get("stdout") is not None or kwargs.get("capture_output"), kwargs
    assert kwargs.get("stderr") is not None or kwargs.get("capture_output"), kwargs
    assert isinstance(argv, list), argv      # a list, never a string for a shell

print("PASS")
