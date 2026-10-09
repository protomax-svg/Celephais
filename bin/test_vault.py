#!/usr/bin/env python3
"""Check the vault: round trip, wrong password, tampering. Run: python3 bin/test_vault.py"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import celephais_vault as v  # noqa: E402

# A small N keeps the check fast; the real default is 1<<15.
FAST = {"name": "scrypt", "n": 1 << 12, "r": 8, "p": 1}
SECRETS = {"CORE_URL": "http://100.64.0.1:8080", "CORE_TOKEN": "abc123", "RDP_USER": "max"}

tmp = pathlib.Path(tempfile.mkdtemp())
path = tmp / "celephais.vault"

v.save(path, "correct horse battery", SECRETS, kdf=FAST)
assert v.load(path, "correct horse battery") == SECRETS

raw = path.read_text()
assert "abc123" not in raw, "the secret is readable in the file"
assert "CORE_TOKEN" not in raw, "the key names leak"

for wrong in ("Correct horse battery", "", "correct horse batter"):
    try:
        v.load(path, wrong)
        raise AssertionError(f"wrong password accepted: {wrong!r}")
    except v.VaultError:
        pass

# Editing the file must fail, including a downgrade of the KDF parameters.
for field, value in (("ct", v._b64(b"nonsense-ciphertext-padding-xx")),
                     ("salt", v._b64(b"0" * 16)),
                     ("kdf", {"name": "scrypt", "n": 2, "r": 1, "p": 1})):
    blob = json.loads(raw)
    blob[field] = value
    path.write_text(json.dumps(blob))
    try:
        v.load(path, "correct horse battery")
        raise AssertionError(f"tampered {field} accepted")
    except v.VaultError:
        pass

# Two saves of the same data must not produce the same bytes.
path.write_text(raw)
v.save(path, "correct horse battery", SECRETS, kdf=FAST)
assert path.read_text() != raw, "salt or nonce is being reused"

print("PASS")
