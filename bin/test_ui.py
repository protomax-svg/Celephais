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
    "open_remote": lambda name, say, hide, user, password:
        calls.append(("open", name, user, password)),
    "take_seat": lambda name, say, confirmed=False:
        {"confirm": True, "warning": "the saved desktop cannot be kept"}
        if not confirmed else calls.append(("seat", name)),
    "rdp_login": lambda: ("workrealm", ""),      # nothing stored: it must ask
    "stop": lambda name, say, confirmed=False: calls.append(("stop", name)),
    "release": lambda name, say, confirmed=False: calls.append(("release", name)),
    "devices": lambda: DEVICES,
    "set_device": lambda ident, action: (calls.append(("device", ident, action))
                                         or DEVICES[0].update(action=action)),
}
DEVICES = [{"id": "1532:02a2", "name": "Razer Ornata V3 X", "usb": "1532:02a2",
            "action": None, "present": True},
           {"id": "8087:0aaa", "name": "Bluetooth", "usb": "8087:0aaa",
            "action": "never", "present": True}]


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


def all_buttons(widget):
    found = []
    for child in widget.winfo_children():
        if isinstance(child, tkinter.Button):
            found.append(child)
        found += all_buttons(child)
    return found


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
    # A password typed under the wrong keyboard layout looks exactly like a
    # password typed wrong, so it has to be possible to look at it.
    eye = [c for c in app.panes[0].winfo_children()
           if isinstance(c, tkinter.Button) and "key" in str(c.cget("text"))][0]
    eye.invoke()
    assert all(e.cget("show") == "" for p in app.panes
               for e in p.winfo_children() if isinstance(e, tkinter.Entry)), \
        "show the key must reveal it on every monitor"
    eye.invoke()
    assert entry.cget("show") != "", "and hide it again"
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
    # Both clients are this same screen, so every action lives here, and only
    # the ones that make sense for a Realm in that state are offered.
    assert "stop" in shown and "park" in shown, f"nothing to do with work: {shown}"
    assert "give the monitors back" in shown, f"windows holds the seat: {shown}"
    assert "windows" in shown, shown
    assert any("on the monitors" in t for t in shown), f"seat not shown: {shown}"
    assert any("ready" in t for t in shown), f"power not shown: {shown}"
    assert ("unlock", "right") in calls, calls

    # Signing in to the Realm happens in our own screen. FreeRDP would
    # otherwise raise its own small window after the overlay had stepped
    # aside, which is the thing this screen exists to prevent.
    app.go("open_remote", "work")
    app.root.update()
    shown = texts(app.panes[0])
    assert "sign in" in shown and "work" in shown, shown
    assert ("open", "work", "workrealm", "hunter2") not in calls, "asked nothing"
    secrets = [c for c in app.panes[0].winfo_children()
               if isinstance(c, tkinter.Entry) and c.cget("show")]
    assert len(secrets) == 1, "exactly one hidden field"
    plain = [c for c in app.panes[0].winfo_children()
             if isinstance(c, tkinter.Entry) and not c.cget("show")]
    assert len(plain) == 1 and plain[0].get() == "workrealm", "user prefilled"
    app.rdp_password.set("hunter2")
    app.submit_login()
    assert pump(app, until=lambda: ("open", "work", "workrealm", "hunter2") in calls), \
        f"the credentials never reached the Gate: {calls}"

    # Asked once, not once per connection.
    calls.clear()
    app.go("open_remote", "work")
    assert pump(app, until=lambda: ("open", "work", "workrealm", "hunter2") in calls), \
        f"it asked again: {texts(app.panes[0])}"
    pump(app, 1.0)              # let the worker's "finished" land before the next test
    app.hide()                  # it deiconified the windows on its way out

    # A hook that raises SystemExit must still reach the screen. wait_ready and
    # rdp_client both raise it, and `except Exception` used to let it past: the
    # worker thread died without a word and the overlay waited for ever.
    # A device nobody has decided about has to be decided about before the
    # Realm takes the physical machine: an unapproved keyboard stays on the
    # host and the Realm comes up without one. One already decided is not asked
    # about again.
    calls.clear()
    app.go("take_seat", "work")
    assert pump(app, until=lambda: "new devices" in texts(app.panes[0])), \
        f"it never asked: {texts(app.panes[0])}"
    shown = texts(app.panes[0])
    assert "Razer Ornata V3 X" in shown, shown
    assert "Bluetooth" not in shown, "it asked again about a decided device"
    assert ("seat", "work") not in calls, "the monitors moved before deciding"
    with_seat = [c for c in all_buttons(app.panes[0])
                 if c.cget("text") == "with the monitors"][0]
    with_seat.invoke()
    assert pump(app, until=lambda: ("device", "1532:02a2", "seat") in calls), \
        f"the decision never reached Core: {calls}"
    pump(app, 1.0)
    # It stays decided from here on, which is the point: the screens that
    # follow must not stop to ask about it again.

    # Core refuses to throw a saved session away without being told twice, so
    # the overlay must say what is lost and wait for a second, deliberate press.
    calls.clear()
    app.go("take_seat", "work")
    assert pump(app, until=lambda: any("cannot be kept" in t
                                       for t in texts(app.panes[0]))), \
        f"no warning shown: {texts(app.panes[0])}"
    assert ("seat", "work") not in calls, "it went ahead without asking"
    goon = [c for c in app.panes[0].winfo_children()
            if isinstance(c, tkinter.Button) and c.cget("text") == "go on"][0]
    goon.invoke()
    assert pump(app, until=lambda: ("seat", "work") in calls), \
        f"the second press did nothing: {calls}"
    pump(app, 1.0)

    def explode(name, say, confirmed=False):
        raise SystemExit("the boot timed out")

    hooks["take_seat"] = explode
    app.go("take_seat", "work")
    assert pump(app, until=lambda: any("timed out" in t for t in texts(app.panes[0]))), \
        f"a SystemExit vanished instead of being shown: {texts(app.panes[0])}"

finally:
    app.leave()

# A host that will not name its monitors gets one window, fullscreened
# wherever the manager puts it. One whole screen beats a window stretched
# across the desktop with its controls on the seam between two monitors.
real_screens = ui.screens
ui.screens = lambda root: []
try:
    lone = ui.Overlay(hooks)
    lone.hide()
    assert lone.rects == [None], lone.rects
    assert len(lone.windows) == 1 and len(lone.panes) == 1, lone.windows
    lone.leave()
finally:
    ui.screens = real_screens

print("PASS")
