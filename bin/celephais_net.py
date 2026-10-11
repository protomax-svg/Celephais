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
"""
import contextlib
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

READY_TIMEOUT = int(os.environ.get("TS_READY_TIMEOUT", "60"))


class NetError(Exception):
    pass


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
        return sock
    except Exception:
        sock.close()
        raise


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
                remote = _socks_connect(proxy, host, port)
            except Exception:
                client.close()
                continue
            threading.Thread(target=_splice, args=(client, remote), daemon=True).start()
            threading.Thread(target=_splice, args=(remote, client), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    return listener.getsockname()[1], listener


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
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        deadline = time.monotonic() + READY_TIMEOUT
        while not os.path.exists(sock):
            if daemon.poll() is not None:
                raise NetError(f"tailscaled exited: {daemon.stderr.read().strip()}")
            if time.monotonic() > deadline:
                raise NetError("tailscaled never created its control socket")
            time.sleep(0.2)

        cli = [bundled("tailscale"), f"--socket={sock}"]
        up = cli + ["up", f"--hostname={hostname}", "--accept-dns=false"]
        if tags:
            up.append(f"--advertise-tags={tags}")
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
        result = subprocess.run(up, capture_output=True, text=True,
                                timeout=READY_TIMEOUT, env=env)
        if result.returncode != 0:
            raise NetError(f"tailscale up failed: {(result.stderr or result.stdout).strip()}")

        while time.monotonic() < deadline:
            status = subprocess.run(cli + ["status", "--json"],
                                    capture_output=True, text=True)
            if status.returncode == 0:
                state = json.loads(status.stdout)
                if state.get("BackendState") == "Running":
                    break
            time.sleep(0.5)
        else:
            raise NetError("tailnet did not come up in time")

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
