"""Embedded Tailscale for the Gate. No install, no service, no driver.

Runs the bundled `tailscaled` in userspace networking mode with its state kept
in memory, so the node registers as ephemeral and disappears when the Gate
exits. Nothing is written to the host except a short-lived control socket in a
temporary directory, which carries no secret.

Everything leaves through two local proxies:
  - an HTTP proxy, used to reach Core
  - a SOCKS5 proxy, handed to FreeRDP with /proxy:

The credential is secret vault data. On Linux and macOS it reaches
`tailscale up` through a FIFO, so it never exists as a file and never appears
in the process list. Windows has no FIFO, so there it goes on the command line.

Two kinds of credential are accepted, told apart by their prefix:

  tskey-auth-...    a plain auth key. Expires after at most 90 days.
  tskey-client-...  an OAuth client secret. Does NOT expire, mints a fresh key
                    on every launch, and requires tags.

The node is ephemeral either way, because the state is kept in memory.

Nothing these subprocesses say reaches the user. They are launched without a
console of their own, their output is piped rather than inherited, and what
comes back is redacted and kept for the terminal. The Gate shows a sentence of
its own instead, because `tailscale up` quotes its own command line back when
it is unhappy and that command line can carry the key.
"""
import contextlib
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

READY_TIMEOUT = int(os.environ.get("TS_READY_TIMEOUT", "60"))
# How long a peer may take to become reachable once the network is up. See
# reach(): measured at 10.5 seconds on the same wire as pve, once over 20,
# and longer from a laptop on somebody else's network.
REACH_TIMEOUT = int(os.environ.get("TS_REACH_TIMEOUT", "90"))
# How long a registered node may take to say it is Running. It only says so
# once it holds a live connection to a relay or a peer, and a laptop on a
# strange network can be slow to get one; 60 was not always enough.
RUNNING_TIMEOUT = int(os.environ.get("TS_RUNNING_TIMEOUT", "120"))

# Windows only. Launch a console program without giving it a console: without
# this the Gate either writes tailscaled's output into its own console, which
# the overlay does not cover, or opens a console window per child when it was
# built windowed. getattr because the constant exists only on Windows.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

# Whether Tailscale's own words may reach the terminal. The overlay turns this
# off: behind a fullscreen screen there is no terminal to read them in, and
# anything printed there is simply a leak.
SHOW_DETAIL = True

# What must never be seen: the key itself, and the flag that carries it. The
# second catches a key that does not start with the prefix we expect.
_SECRETS = (
    (re.compile(r"(--auth-key|--client-secret)=\S*"), r"\1=[redacted]"),
    (re.compile(r"tskey-\S+"), "tskey-[redacted]"),
)

# Tailscale's complaints, as the Gate says them. Matched on the raw output and
# never quoting it, so no key and no path can ride along.
# What only one thing can mean, and which no amount of waiting will fix.
# Checked while the daemon is still running, so every needle here has to be
# final: tailscaled says "dial tcp ... i/o timeout" on its way to a network
# that comes up perfectly well, and a bare "access is denied" matches the
# entirely routine "syspolicy: GP lock not acquired (Access denied.)". Only
# the refusal of its own control socket belongs in here.
_FATAL = (
    # tailscaled wants a named pipe owned by the Administrators group, and a
    # plain Windows account may not create one. Nothing the Gate can do about
    # it from inside, so the sentence says what will work instead.
    ("namedpipe.listen", "Tailscale cannot start here without administrator "
                         "rights. Install Tailscale on this machine, or start "
                         "the Gate as administrator."),
    ("safesocket.listen", "Tailscale cannot start here without administrator "
                          "rights. Install Tailscale on this machine, or start "
                          "the Gate as administrator."),
)

_HINTS = _FATAL + (
    ("invalid key", "the Tailscale key was refused. It may have expired or been revoked."),
    ("invalid authkey", "the Tailscale key was refused. It may have expired or been revoked."),
    ("unauthorized", "Tailscale refused the Gate's key."),
    ("not permitted", "the Tailscale key may not use the tags the Gate asks for."),
    ("requested tags", "the Tailscale key may not use the tags the Gate asks for."),
    ("no such host", "this machine cannot reach Tailscale. Check its internet connection."),
    ("dial tcp", "this machine cannot reach Tailscale. Check its internet connection."),
    ("certificate", "this machine rejected Tailscale's certificate. Check its clock."),
)


class NetError(Exception):
    pass


def redact(text):
    """The same text with every secret taken out of it.

    Everything that comes back from tailscale or tailscaled goes through here
    before it is stored, printed or raised, because on Windows the key is on
    the command line and Go programs like to quote their arguments back.
    """
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text or "")
    return text


def _note(text):
    """Say one piece of Tailscale's output, redacted. Returns what was said.

    The only place that output is allowed to go, and only once something has
    gone wrong: a terminal that asked for it, and nowhere else. Never the
    overlay, never a dialog, never an exception the UI will render.
    """
    kept = redact(text).strip()
    if not kept or not SHOW_DETAIL:
        return kept
    stream = sys.stderr
    try:
        if stream is not None and stream.isatty():
            for line in kept.splitlines():
                print(f"gate: tailscale: {line}", file=stream, flush=True)
    except (OSError, ValueError):            # a closed or frozen-away stderr
        pass
    return kept


def _recognised(output, hints=None):
    """The sentence for something Tailscale said, or None if nothing fits."""
    low = (output or "").lower()
    for needle, sentence in hints or _HINTS:
        if needle in low:
            return sentence
    return None


def _explain(output, fallback):
    """One sentence for the user, chosen by what Tailscale said but never
    containing it."""
    return _recognised(output) or fallback


def _window_flags(nt):
    """Creation flags that keep a child from showing a console. Windows only."""
    return CREATE_NO_WINDOW if nt else 0


def _quiet(extra=None):
    """The keyword arguments every Tailscale subprocess is launched with.

    shell=False so no argument is ever parsed by a command interpreter, stdin
    closed so nothing can stop waiting for an answer nobody will type, and on
    Windows no console at all: neither the Gate's own, which the overlay does
    not cover, nor one of its own, which would flash across the screen.
    """
    kwargs = {"shell": False, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        flags = _window_flags(True)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        kwargs.update(creationflags=flags, startupinfo=startup)
    kwargs.update(extra or {})
    return kwargs


class _Tail:
    """Reads a child's stderr so its pipe cannot fill, keeping the last lines.

    tailscaled talks for as long as it runs. A pipe nobody reads holds about
    64K and then the daemon blocks on its own logging, so the reading is not
    optional once stderr is a pipe rather than the console it used to be.

    Read, redacted, and kept - not shown. Its running commentary is of no
    interest to anybody until it dies, and then its last words are the whole
    story.
    """

    def __init__(self, stream):
        self.lines = []
        self.thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self.thread.start()

    def _pump(self, stream):
        with stream:
            for line in stream:
                kept = redact(line).strip()
                if kept:
                    self.lines.append(kept)
                    del self.lines[:-10]

    def text(self):
        self.thread.join(timeout=2)          # the child is gone; let it finish
        return " ".join(self.lines)

    def snapshot(self):
        """What it has said so far, for reading while it is still talking.

        text() waits for the reader to finish, which is right once the child is
        gone and two seconds of nothing when it is not.
        """
        return " ".join(self.lines)


def log_dir():
    """Where the Gate keeps what it learned from a failure: ~/.cache/celephais
    on Linux, %LOCALAPPDATA%\\celephais on Windows. None if it cannot be made."""
    base = (os.environ.get("LOCALAPPDATA") if os.name == "nt"
            else os.environ.get("XDG_CACHE_HOME"))
    where = pathlib.Path(base or pathlib.Path.home() / ".cache") / "celephais"
    try:
        where.mkdir(parents=True, exist_ok=True)
        return where
    except OSError:
        return None


def _status(cli):
    """`tailscale status --json`, parsed, or {} if it cannot be had."""
    try:
        done = subprocess.run(cli + ["status", "--json"],
                              **_quiet({"capture_output": True, "text": True,
                                        "timeout": 15}))
        return json.loads(done.stdout) if done.returncode == 0 else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def _diagnosis(state, tail):
    """What Tailscale itself says is wrong, for a network that did not come up.

    Its health warnings are sentences written for people - "could not connect
    to the relay server", "network is down" - so the first one is worth
    putting in front of the user, where its raw log never is. Everything,
    warnings and the daemon's last lines both, is redacted, written to
    tailnet.log in log_dir(), and said on a terminal if there is one. The
    overlay has no terminal, and this is the failure that only ever happens
    on a machine nobody here can see.
    """
    health = [redact(str(line)).strip() for line in state.get("Health") or []]
    self_node = state.get("Self") or {}
    lines = [f"BackendState: {state.get('BackendState', 'unknown')}",
             f"home relay: {self_node.get('Relay') or 'none'}"]
    lines += [f"health: {line}" for line in health]
    # _Tail redacts as it reads; again here, because this is the one place its
    # lines are written to disk.
    lines += [f"tailscaled: {redact(line)}" for line in tail.lines]
    where = log_dir()
    if where:
        try:
            (where / "tailnet.log").write_text(
                time.strftime("%Y-%m-%d %H:%M:%S\n") + "\n".join(lines) + "\n")
        except OSError:
            pass
    _note("\n".join(lines))
    return health[0] if health else ""


def bundled(name):
    """The binary next to us when frozen, otherwise whatever is on PATH.

    On Windows the packed file keeps its .exe suffix, so looking for the bare
    name finds nothing and a clean Windows host falls back to a PATH search
    that was never going to succeed. Try both spellings.
    """
    names = [name, name + ".exe"] if sys.platform == "win32" else [name]
    if getattr(sys, "frozen", False):
        for spelling in names:
            candidate = pathlib.Path(sys._MEIPASS) / spelling
            if candidate.exists():
                return str(candidate)
    found = shutil.which(name) or shutil.which(name, path="/usr/sbin:/usr/local/sbin")
    if not found:
        raise NetError(f"{name} is not bundled and not on PATH")
    return found


def ca_bundle():
    """A CA file to hand to the Go binaries, or None to let them decide.

    A minimal host can have no certificate store at all, and the OAuth path
    calls api.tailscale.com over HTTPS, so the bundle travels with us. Go reads
    SSL_CERT_FILE on unix and uses the system store on Windows.
    """
    if getattr(sys, "frozen", False):
        packed = pathlib.Path(sys._MEIPASS) / "cacert.pem"
        if packed.exists():
            return str(packed)
    try:
        import certifi
        return certifi.where()
    except Exception:
        return None


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _feed_key(path, key):
    """Write the auth key into the FIFO once tailscale opens it to read."""
    def run():
        try:
            with open(path, "w") as fifo:
                fifo.write(key)
        except OSError:
            pass
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _recv_exact(sock, count):
    """A socket may hand back less than asked for. Keep going until it does not."""
    buffer = b""
    while len(buffer) < count:
        chunk = sock.recv(count - len(buffer))
        if not chunk:
            raise NetError("the SOCKS proxy closed the connection early")
        buffer += chunk
    return buffer


def _socks_connect(proxy, host, port, timeout=10):
    """One SOCKS5 CONNECT, no authentication. Returns the open socket."""
    proxy_host, proxy_port = proxy.rsplit(":", 1)
    sock = socket.create_connection((proxy_host, int(proxy_port)), timeout)
    try:
        sock.sendall(b"\x05\x01\x00")                      # version 5, "no auth"
        if _recv_exact(sock, 2) != b"\x05\x00":
            raise NetError("the SOCKS proxy refused the greeting")
        name = host.encode()
        sock.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name
                     + port.to_bytes(2, "big"))
        reply = _recv_exact(sock, 4)
        if reply[1] != 0:
            raise NetError(f"the SOCKS proxy refused {host}:{port}, code {reply[1]}")
        # Read past the bound address that follows; we have no use for it.
        kind = reply[3]
        if kind == 1:
            length = 4
        elif kind == 4:
            length = 16
        elif kind == 3:
            length = _recv_exact(sock, 1)[0]
        else:
            raise NetError(f"the SOCKS proxy sent an unknown address type {kind}")
        _recv_exact(sock, length + 2)
        # The timeout was for the handshake. Left on, it would end a spliced
        # RDP session the first time the Realm had nothing to say for ten
        # seconds - a still desktop is a quiet one.
        sock.settimeout(None)
        return sock
    except Exception:
        sock.close()
        raise


def reach(proxy, host, port, wait=None, every=5):
    """Wait until host:port answers through the tailnet. Returns the seconds.

    "Running" is not the same as "has a way to every peer". A network that
    has just come up still has to find each peer before the first packet to
    it gets through, and until then a connection just hangs. Measured on the
    same wire as pve: the first request to Core took 10.5 seconds, once more
    than 20, and every request after it was instant. The Gate gave Core one
    try with a 20 second timeout, so from a laptop on another network it gave
    up just before the path was there, every time - joined the tailnet and
    never reached Core.

    So: short tries, again and again, until one gets through. A try that is
    abandoned still moves the path along inside tailscaled, and a fresh one
    goes out the moment the path is there instead of waiting out a long
    retransmit. A refusal is tried again as well, until the deadline: through
    the proxy it looks the same as a path that is not there yet.
    """
    deadline = time.monotonic() + (REACH_TIMEOUT if wait is None else wait)
    start = time.monotonic()
    while True:
        try:
            _socks_connect(proxy, host, port, timeout=every).close()
            return time.monotonic() - start
        except (OSError, NetError):
            if time.monotonic() >= deadline:
                raise NetError(f"{host}:{port} did not answer through the "
                               "Gate's private network.")
            time.sleep(0.5)


def _splice(a, b):
    try:
        while True:
            chunk = a.recv(65536)
            if not chunk:
                break
            b.sendall(chunk)
    except OSError:
        pass
    finally:
        for side in (a, b):
            try:
                side.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            side.close()


def forward(proxy, host, port):
    """Listen on localhost and carry every connection to host:port via SOCKS.

    This is what lets any RDP client reach the Realm, including Windows' own
    mstsc, which has no proxy setting of its own.
    """
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)

    def serve():
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                return
            try:
                # Longer than a handshake needs: an RDP client's first
                # connection may be the one that waits for the path. See
                # reach(), which the Gate calls first so this rarely matters.
                remote = _socks_connect(proxy, host, port, timeout=30)
            except Exception:
                client.close()
                continue
            threading.Thread(target=_splice, args=(client, remote), daemon=True).start()
            threading.Thread(target=_splice, args=(remote, client), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    return listener.getsockname()[1], listener


def _control_ready(sock):
    r"""Whether tailscaled is accepting on its control socket yet.

    Not the same question as whether the socket's name exists, and on Windows
    the name is not even a file that can be asked about: the control socket
    there is a named pipe, and `os.stat` has no dependable answer for a name
    under \\.\pipe\. The Gate sat watching for a pipe that was already there,
    gave up after a minute with "the Gate's private network did not start in
    time", and the daemon had been running the whole way through.

    Connecting is the question with the same answer on both systems: either
    something accepts or it does not. On POSIX it also closes a real race -
    the socket file appears before anything listens on it, and the CLI call
    that came next could arrive in between.

    Access denied means the pipe is there and this account may not open it,
    which is a different failure with a sentence of its own; it is reported as
    ready so that the CLI gives its own reason rather than this timing out.
    """
    if os.name == "nt":
        try:
            with open(sock, "r+b", buffering=0):
                return True
        except FileNotFoundError:
            return False
        except PermissionError:
            return True
        except OSError as exc:
            # 231 is ERROR_PIPE_BUSY: every instance is taken, so it exists.
            return getattr(exc, "winerror", None) == 231
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(2)
            probe.connect(str(sock))
        return True
    except OSError:
        return False


def daemon_argv(exe, sock, socks_port, http_port):
    r"""How tailscaled is started. Kept apart so the Windows branch is testable.

    tailscaled refuses any --socket outside
    \\.\pipe\ProtectedPrefix\Administrators\, the prefix only an
    administrator may create pipes in. "dev" is the mode for a daemon run by
    hand under a plain account, and it accepts a pipe of our own.
    """
    return ([exe]
            + (["--windows-mode=dev"] if os.name == "nt" else [])
            + ["--tun=userspace-networking",
               "--state=mem:",                  # ephemeral node, nothing on disk
               f"--socket={sock}",
               f"--socks5-server=127.0.0.1:{socks_port}",
               f"--outbound-http-proxy-listen=127.0.0.1:{http_port}"])


def up_argv(cli, hostname, tags):
    """`tailscale up`, without its credential. Kept apart so it is testable.

    --unattended on Windows, and only there - the flag does not exist anywhere
    else. Windows tailscaled ties the network to whoever asked for it: when
    the last program talking to it disconnects, it switches itself off. The
    program that asked is `tailscale up`, which exits the moment it is done.
    So every login was followed at once by "wantrunning-false: Tailscale is
    stopped", "authRoutine: exiting", "Client.Shutdown done", the next status
    query logged in again, and seventeen logins later the Gate reported a
    network that "did not come up in time" from a daemon sitting at NoState.
    Linux has no such rule, which is why it never failed on acnit1.
    """
    up = cli + ["up", f"--hostname={hostname}", "--accept-dns=false"]
    if os.name == "nt":
        up.append("--unattended")
    if tags:
        up.append(f"--advertise-tags={tags}")
    return up


@contextlib.contextmanager
def tailnet(credential, hostname="celephais-gate", tags=""):
    """Bring up userspace Tailscale. Yields {'http_proxy': ..., 'socks5': ...}."""
    oauth = credential.startswith("tskey-client-")
    if oauth and not tags:
        raise NetError("an OAuth client secret needs TS_TAGS, e.g. tag:celephais-gate")
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="celephais-"))
    # Windows has no unix socket; tailscaled listens on a named pipe there.
    # That one is a plain string, not a Path, so everything that waits on it
    # has to go through os.path rather than Path.exists.
    sock = (rf"\\.\pipe\celephais-{os.getpid()}" if os.name == "nt"
            else workdir / "tailscaled.sock")
    http_port, socks_port = free_port(), free_port()
    daemon = None
    try:
        daemon = subprocess.Popen(
            daemon_argv(bundled("tailscaled"), sock, socks_port, http_port),
            **_quiet({"stdout": subprocess.DEVNULL,
                      "stderr": subprocess.PIPE, "text": True}))
        tail = _Tail(daemon.stderr)

        deadline = time.monotonic() + READY_TIMEOUT
        while not _control_ready(sock):
            if daemon.poll() is not None:
                said = _note(tail.text())        # its last words, for a terminal
                raise NetError(_explain(
                    said, "the Gate's private network could not start."))
            # A daemon that is still running may already have said the one
            # thing that settles it. Refused its control socket, tailscaled
            # keeps going and never opens one, and the Gate used to sit out
            # the whole minute and then blame the clock for it.
            settled = _recognised(tail.snapshot(), _FATAL)
            if settled:
                _note(tail.snapshot())
                raise NetError(settled)
            if time.monotonic() > deadline:
                # Whatever it said belongs in the answer. The old message named
                # the symptom and threw away the diagnosis sitting in _Tail.
                said = _note(tail.text())
                raise NetError(_explain(
                    said, "the Gate's private network did not start in time."))
            time.sleep(0.2)

        cli = [bundled("tailscale"), f"--socket={sock}"]
        up = up_argv(cli, hostname, tags)
        flag = "--client-secret" if oauth else "--auth-key"
        if hasattr(os, "mkfifo"):
            fifo = workdir / "key"
            os.mkfifo(fifo, 0o600)
            _feed_key(fifo, credential)
            up.append(f"{flag}=file:{fifo}")
        else:
            # ponytail: Windows has no FIFO, so the secret is on the command
            # line of a process this user already owns. Replace with a named
            # pipe if the Gate ever runs on a shared Windows host.
            up.append(f"{flag}={credential}")

        env = dict(os.environ)
        bundle = ca_bundle()
        if bundle:
            env.setdefault("SSL_CERT_FILE", bundle)
        # The command line is never logged whole: on Windows the key is on it.
        # That is also why the timeout is caught here. TimeoutExpired prints
        # the whole command it was given, and one of those arguments is the
        # key, so letting it out of this function would put it on the screen.
        try:
            result = subprocess.run(up, **_quiet({
                "capture_output": True, "text": True,
                "timeout": READY_TIMEOUT, "env": env}))
        except subprocess.TimeoutExpired:
            raise NetError("the Gate could not join its private network in time.")
        if result.returncode != 0:
            said = _note(result.stderr or result.stdout)
            raise NetError(_explain(said, "the Gate could not join its private network."))

        # Its own deadline. It used to share the one above, which `tailscale
        # up` is allowed to spend in full, so by the time it got here the
        # clock could already be past: the loop ran no iterations at all and
        # reported a network that had not come up as one that timed out.
        #
        # `tailscale up` returns as soon as the node is registered, which is
        # "Starting". "Running" waits for a live connection to a relay or a
        # peer. A laptop that registered and never got one is the failure
        # this loop exists to catch, and it used to end in one bare sentence
        # with everything Tailscale knew about why thrown away.
        deadline = time.monotonic() + RUNNING_TIMEOUT
        state = {}
        while time.monotonic() < deadline:
            if daemon.poll() is not None:
                # Dead is not slow. Say so now, with its last words, rather
                # than ask a daemon that is gone for two minutes.
                said = _note(tail.text())
                raise NetError(_explain(
                    said, "Tailscale stopped while the Gate's private network "
                          "was coming up."))
            state = _status(cli) or state
            if state.get("BackendState") == "Running":
                break
            time.sleep(0.5)
        else:
            said = _diagnosis(state, tail)
            raise NetError("the Gate's private network did not come up in time."
                           + (f" Tailscale says: {said}" if said else ""))

        yield {"http_proxy": f"http://127.0.0.1:{http_port}",
               "socks5": f"127.0.0.1:{socks_port}"}
    finally:
        if daemon and daemon.poll() is None:
            daemon.terminate()
            try:
                daemon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                daemon.kill()
        shutil.rmtree(workdir, ignore_errors=True)
