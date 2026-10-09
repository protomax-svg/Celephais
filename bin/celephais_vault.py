#!/usr/bin/env python3
"""celephais.vault - the encrypted state the Gate carries on the USB stick.

The executable itself is public. Only this file is secret: it holds CORE_URL,
CORE_TOKEN, and later the Tailscale identity material. One password opens it.

    python3 bin/celephais_vault.py init --from-env
    python3 bin/celephais_vault.py set CORE_URL http://100.64.0.1:8080
    python3 bin/celephais_vault.py list

Format: JSON so it stays readable and portable, with base64 fields.
Key derivation is scrypt from the standard library; the cipher is AES-256-GCM.
The KDF parameters are stored in the file and authenticated as additional data,
so they can be raised later without breaking old vaults and cannot be weakened
by editing the file.
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
import pathlib
import sys

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VAULT_VERSION = 1
# ~32 MB and well under a second on a laptop. Raise N, not the other knobs.
DEFAULT_KDF = {"name": "scrypt", "n": 1 << 15, "r": 8, "p": 1}
DEFAULT_PATH = pathlib.Path(__file__).resolve().parent.parent / "celephais.vault"


class VaultError(Exception):
    pass


def _b64(raw):
    return base64.b64encode(raw).decode()


def _unb64(text):
    return base64.b64decode(text)


def _key(password, kdf, salt):
    if kdf["name"] != "scrypt":
        raise VaultError(f"unknown key derivation {kdf['name']!r}")
    try:
        # scrypt needs about 128*N*r bytes; leave headroom and a small floor.
        maxmem = 128 * kdf["n"] * kdf["r"] * 2 + (1 << 20)
        return hashlib.scrypt(password.encode(), salt=salt, n=kdf["n"], r=kdf["r"],
                              p=kdf["p"], maxmem=maxmem, dklen=32)
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise VaultError(f"bad key derivation parameters: {exc}") from exc


def _header(kdf, salt):
    """The part that is authenticated but not encrypted."""
    return {"v": VAULT_VERSION, "kdf": kdf, "salt": _b64(salt)}


def _aad(header):
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode()


def save(path, password, data, kdf=None):
    kdf = kdf or DEFAULT_KDF
    salt, nonce = os.urandom(16), os.urandom(12)
    header = _header(kdf, salt)
    ct = AESGCM(_key(password, kdf, salt)).encrypt(
        nonce, json.dumps(data).encode(), _aad(header))
    blob = dict(header, nonce=_b64(nonce), ct=_b64(ct))
    path = pathlib.Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(blob, indent=2) + "\n")
    os.chmod(tmp, 0o600)          # no-op on Windows, correct everywhere else
    tmp.replace(path)             # never leave a half-written vault behind


def load(path, password):
    try:
        blob = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise VaultError(f"cannot read vault: {exc}") from exc
    if blob.get("v") != VAULT_VERSION:
        raise VaultError(f"vault version {blob.get('v')} is not supported")
    try:
        header = _header(blob["kdf"], _unb64(blob["salt"]))
        key = _key(password, blob["kdf"], _unb64(blob["salt"]))
    except VaultError:
        raise
    except Exception as exc:      # a field is missing or not base64
        raise VaultError(f"the vault file is malformed: {exc}") from exc
    try:
        plain = AESGCM(key).decrypt(_unb64(blob["nonce"]), _unb64(blob["ct"]),
                                    _aad(header))
    except Exception as exc:      # wrong password, or the file was edited
        raise VaultError("wrong password, or the vault has been tampered with") from exc
    return json.loads(plain)


def _ask_new_password():
    first = getpass.getpass("New vault password: ")
    if len(first) < 8:
        raise SystemExit("vault: use at least 8 characters")
    if first != getpass.getpass("Repeat: "):
        raise SystemExit("vault: the two passwords differ")
    return first


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default=DEFAULT_PATH, type=pathlib.Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    init = sub.add_parser("init", help="create a new vault")
    init.add_argument("--from-env", action="store_true",
                      help="seed it from the .env file next to the repo")
    setter = sub.add_parser("set", help="add or change one value")
    setter.add_argument("key")
    setter.add_argument("value")
    sub.add_parser("list", help="show the key names, never the values")
    args = ap.parse_args(argv)

    if args.cmd == "init":
        if args.vault.exists():
            raise SystemExit(f"vault: {args.vault} already exists")
        data = {}
        if args.from_env:
            env = args.vault.parent / ".env"
            for line in env.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip().strip("\"'")
            data.pop("PVE_TOKEN", None)    # belongs to Core, never to the Gate
            data.pop("PVE_HOST", None)
            data.pop("PVE_NODE", None)
            data.pop("PVE_VERIFY_TLS", None)
            data.pop("CORE_BIND", None)
        save(args.vault, _ask_new_password(), data)
        print(f"vault: wrote {args.vault} with {len(data)} value(s)")
        return 0

    password = getpass.getpass("Vault password: ")
    data = load(args.vault, password)
    if args.cmd == "set":
        data[args.key] = args.value
        save(args.vault, password, data)
        print(f"vault: set {args.key}")
    else:
        for key in sorted(data):
            print(key)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except VaultError as exc:
        sys.exit(f"vault: {exc}")
