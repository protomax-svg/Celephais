#!/usr/bin/env python3
"""Check the overlay without showing it. Run: python3 bin/test_ui.py

The window is hidden straight away, so this is safe to run on a desktop you
are using. It needs a display; skip it on a headless machine.
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
    "open_remote": lambda name, say: calls.append(("open", name)),
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
app.root.withdraw()                           # never show it during the check
try:
    assert app.root.overrideredirect(), "the overlay must have no window frame"
    rects = ui.screens(app.root)
    assert rects and all(w > 0 and h > 0 for _x, _y, w, h in rects), rects
    assert len(app.panes) == len(rects), "one set of controls per monitor"
    # Every pane sits inside its own monitor, never across the seam.
    for pane, (_x, _y, w, h) in zip(app.panes, rects):
        area = pane.master
        assert (area.place_info()["width"], area.place_info()["height"]) == \
            (str(w), str(h)), (area.place_info(), w, h)

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

    print("PASS")
finally:
    app.root.destroy()
