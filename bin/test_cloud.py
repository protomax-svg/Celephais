#!/usr/bin/env python3
"""Check the shared disk's two config files. Run: python3 bin/test_cloud.py

They are written by deploy/cloud-setup.sh inside a container nobody watches.
A typo in either one is a share that quietly lets the wrong person in, or
does not come up at all, and the first sign of it is a Realm that cannot see
its files. The text is pulled out of the script itself, so this checks what
is actually shipped and not a copy of it.

testparm does the reading for Samba. The copyparty half is read here, because
starting a real server would mean downloading one.
"""
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SETUP = (HERE.parent / "deploy" / "cloud-setup.sh").read_text()


def heredoc(target):
    """The text cloud-setup.sh writes to `target`, with its variables filled."""
    match = re.search(r"^cat > %s <<'?EOF'?\n(.*?)^EOF$" % re.escape(target),
                      SETUP, re.M | re.S)
    assert match, f"cloud-setup.sh no longer writes {target}"
    return match.group(1).replace("$SHARE", "/srv/cloud")


smb = heredoc("/etc/samba/smb.conf")
copyparty = heredoc("/etc/copyparty/server.conf").replace("$PASSWORD", "secret")

# --- Samba: it has to parse, and it has to be shut to strangers ---------
if shutil.which("testparm"):
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as handle:
        handle.write(smb)
        path = handle.name
    done = subprocess.run(["testparm", "-s", path], capture_output=True, text=True)
    assert done.returncode == 0, f"samba will not read this:\n{done.stderr}"
    parsed = done.stdout
    assert "[cloud]" in parsed, parsed
    # testparm prints the settings it kept, so these are the live values.
    for wanted in ("read only = No", "valid users = cloud", "force user = cloud"):
        assert wanted in parsed, f"missing {wanted!r} in:\n{parsed}"
    assert "map to guest = Never" in parsed or "map to guest" not in parsed.lower(), \
        f"guests must never be mapped to a real user:\n{parsed}"
    assert "[homes]" not in parsed, "home directories must not be shared"
    assert "[printers]" not in parsed, "printers must not be shared"
else:
    print("testparm is not installed; skipped the Samba half", file=sys.stderr)

# --- copyparty: one account, and nothing for anyone who has no account ---
assert "[accounts]" in copyparty and "cloud: secret" in copyparty, copyparty
assert "/srv/cloud" in copyparty, copyparty
# "A" is every permission, and it is granted to cloud by name. A line that
# granted it to nobody in particular would open the disk to the internet side
# of the container.
grant = re.search(r"accs:\s*\n\s*A:\s*(.+)", copyparty)
assert grant and grant.group(1).strip() == "cloud", copyparty
assert "no-crt" in copyparty, "it must not serve a shipped certificate as if real"

# The password must not reach the command line, where any process on the box
# can read it out of /proc.
unit = re.search(r"ExecStart=(.+)", SETUP)
assert unit and "$PASSWORD" not in unit.group(1), unit.group(1) if unit else SETUP
assert "-c /etc/copyparty/server.conf" in unit.group(1), unit.group(1)

# And the file that does hold it must not be readable by the whole container.
assert "chmod 640 /etc/copyparty/server.conf" in SETUP, SETUP
assert "chown root:cloud /etc/copyparty/server.conf" in SETUP, SETUP

# The directory around it has to be walkable by the account the service runs
# as, or the config reads as missing and copyparty quietly serves nothing.
d = re.search(r"install -d -m (\d+)(.*) /etc/copyparty", SETUP)
assert d, SETUP
assert d.group(1) == "750", f"/etc/copyparty is {d.group(1)}; cloud cannot enter it"
assert "-g cloud" in d.group(2), d.group(0)

print("PASS")
