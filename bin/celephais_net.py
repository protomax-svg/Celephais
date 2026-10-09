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
    """The binary next to us when frozen, otherwise whatever is on PATH."""
    if getattr(sys, "frozen", False):
        candidate = pathlib.Path(sys._MEIPASS) / name
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


@contextlib.contextmanager
def tailnet(credential, hostname="celephais-gate", tags=""):
    """Bring up userspace Tailscale. Yields {'http_proxy': ..., 'socks5': ...}."""
    oauth = credential.startswith("tskey-client-")
    if oauth and not tags:
        raise NetError("an OAuth client secret needs TS_TAGS, e.g. tag:celephais-gate")
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="celephais-"))
    sock = workdir / "tailscaled.sock"
    http_port, socks_port = free_port(), free_port()
    daemon = None
    try:
        daemon = subprocess.Popen(
            [bundled("tailscaled"),
             "--tun=userspace-networking",
             "--state=mem:",                      # ephemeral node, nothing on disk
             f"--socket={sock}",
             f"--socks5-server=127.0.0.1:{socks_port}",
             f"--outbound-http-proxy-listen=127.0.0.1:{http_port}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

        deadline = time.monotonic() + READY_TIMEOUT
        while not sock.exists():
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
