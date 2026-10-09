#!/usr/bin/env python3
"""Check the SOCKS5 forwarder. Run: python3 bin/test_forward.py

Uses a fake SOCKS5 server, so it needs no network and no Tailscale.
"""
import pathlib
import socket
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import celephais_net as n  # noqa: E402

PAYLOAD = b"celephais" * 9000          # big enough to need several reads


KEEP = []           # hold the listeners open for the whole run


def echo_server():
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(8)
    KEEP.append(srv)

    def serve(conn):
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
            threading.Thread(target=serve, args=(conn,), daemon=True).start()

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

print("PASS")
