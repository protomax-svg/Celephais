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

print("PASS")
