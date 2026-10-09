#!/usr/bin/env python3
"""Check the overlay without showing it. Run: python3 bin/test_ui.py

The overlay shows for about half a second and is then hidden for the rest of
the check, so this is safe to run on a desktop you are using. That half second
is not waste: the window manager applies fullscreen asynchronously, so the
placement cannot be read until it has been given a moment to act.

It needs a display; skip it on a headless machine.
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
try:
    import tkinter
    tkinter.Tk().destroy()
except Exception as exc:                      # no display, nothing to check
    print(f"SKIP: no usable display ({exc})")
    sys.exit(0)

import celephais_ui as ui  # noqa: E402

REALMS = [{"realm": "work", "vmid": 201, "power": "READY", "seat": False,
           "ip": "192.168.2.114"},
          {"realm": "windows", "vmid": 100, "power": "OFF", "seat": True,
           "ip": None}]
calls = []
hooks = {
    "unlock": lambda password: (calls.append(("unlock", password)) or True
                                if password == "right" else _wrong()),
    "realms": lambda: REALMS,
    "open_remote": lambda name, say, hide: calls.append(("open", name)),
    "take_seat": lambda name, say: calls.append(("seat", name)),
}


def _wrong():
    raise ValueError("wrong password, or the vault has been tampered with")


def pump(app, seconds=3.0, until=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.root.update()
        if until and until():
            return True
        time.sleep(0.02)
    return False


def texts(widget):
    out = []
    for child in widget.winfo_children():
        try:
            out.append(str(child.cget("text")))
        except Exception:
            pass
        out += texts(child)
    return out


app = ui.Overlay(hooks)
for _ in range(20):                           # let the manager apply fullscreen
    app.root.update()
    time.sleep(0.02)
placed = [(bool(w.attributes("-fullscreen")), w.winfo_geometry())
          for w in app.windows]
app.hide()                                    # off the screen for the rest
try:
    # The window manager must know about every window. An overrideredirect
    # window is refused the keyboard, which is how the overlay went deaf.
    assert not app.root.overrideredirect(), "the manager must see the window"
    rects = ui.screens(app.root)
    assert len(app.windows) == len(rects), "one window per monitor"
    for (full, where), (x, y, w, h) in zip(placed, rects):
        assert full, f"window not fullscreen: {placed}"
        assert where == f"{w}x{h}+{x}+{y}", f"wrong monitor: {where} != {w}x{h}+{x}+{y}"
    assert rects and all(w > 0 and h > 0 for _x, _y, w, h in rects), rects
    assert len(app.panes) == len(rects), "one set of controls per monitor"
    # Each set of controls is centred in its own window, never across the seam.
    for pane, win in zip(app.panes, app.windows):
        assert pane.master is win, "controls must live in that monitor's window"
        assert pane.place_info()["anchor"] == "center", pane.place_info()

    app.ask_password()
    app.root.update()
    entry = [c for c in app.panes[0].winfo_children() if isinstance(c, tkinter.Entry)][0]
    assert entry.cget("show") != "", "the password must not be shown in clear"
    assert "leave" in texts(app.panes[0]), "there must be a way out with the mouse"
    assert app.root.bind_all("<Escape>"), "Esc must be bound"
    # typing on one monitor fills the copies on the others
    assert all(e.cget("textvariable") == str(app.password)
               for p in app.panes
               for e in p.winfo_children() if isinstance(e, tkinter.Entry))

    # wrong password comes back to the same screen with the reason
    entry.insert(0, "nope")
    app.submit()
    assert pump(app, until=lambda: any("tampered" in t for t in texts(app.panes[0]))), \
        f"no error shown: {texts(app.panes[0])}"

    # right password reaches the realm list
    entry = [c for c in app.panes[0].winfo_children() if isinstance(c, tkinter.Entry)][0]
    entry.insert(0, "right")
    app.submit()
    assert pump(app, until=lambda: "work" in texts(app.panes[0])), \
        f"the realms never appeared: {texts(app.panes[0])}"

    shown = texts(app.panes[0])
    assert "windows" in shown, shown
    assert any("on the monitors" in t for t in shown), f"seat not shown: {shown}"
    assert any("ready" in t for t in shown), f"power not shown: {shown}"
    assert ("unlock", "right") in calls, calls

    # A hook that raises SystemExit must still reach the screen. wait_ready and
    # rdp_client both raise it, and `except Exception` used to let it past: the
    # worker thread died without a word and the overlay waited for ever.
    def explode(name, say):
        raise SystemExit("the boot timed out")

    hooks["take_seat"] = explode
    app.go("take_seat", "work")
    assert pump(app, until=lambda: any("timed out" in t for t in texts(app.panes[0]))), \
        f"a SystemExit vanished instead of being shown: {texts(app.panes[0])}"

    print("PASS")
finally:
    app.root.destroy()
