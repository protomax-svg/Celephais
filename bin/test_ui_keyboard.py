#!/usr/bin/env python3
"""Check that the overlay actually receives the keyboard. Run:

    python3 bin/test_ui_keyboard.py

This is the check that two releases needed and did not have. Tk's own
`focus_get` is not evidence: it reports what Tk believes, and when the window
manager refuses a window the keyboard Tk goes on believing it has focus while
every key press lands somewhere else.

So this presses a real key through the X test extension, the same path a
physical keyboard takes, and reads what arrived in the password box. The
overlay is visible for about two seconds. A hard alarm kills it whatever
happens, so it cannot trap the desktop.

X11 only. It skips on Wayland, on Windows and without libXtst.
"""
import ctypes
import pathlib
import signal
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def skip(why):
    print(f"SKIP: {why}")
    sys.exit(0)


if sys.platform == "win32":
    skip("X test extension is Linux only")
try:
    import tkinter
    tkinter.Tk().destroy()
except Exception as exc:
    skip(f"no usable display ({exc})")
try:
    xlib = ctypes.CDLL("libX11.so.6")
    xtest = ctypes.CDLL("libXtst.so.6")
except OSError as exc:
    skip(f"no libXtst, cannot press a key ({exc})")

import celephais_ui as ui  # noqa: E402

signal.alarm(60)                    # never leave the overlay up, whatever fails

xlib.XOpenDisplay.restype = ctypes.c_void_p
display = ctypes.c_void_p(xlib.XOpenDisplay(None))
if not display.value:
    skip("cannot open the display")


def press(letter):
    """Press one key the way a keyboard does, through the X server."""
    keysym = xlib.XStringToKeysym(letter.encode())
    code = xlib.XKeysymToKeycode(display, ctypes.c_ulong(keysym))
    xtest.XTestFakeKeyEvent(display, ctypes.c_uint(code), 1, 0)
    xtest.XTestFakeKeyEvent(display, ctypes.c_uint(code), 0, 0)
    xlib.XFlush(display)


hooks = {"unlock": lambda password: True, "realms": lambda: [],
         "open_remote": lambda name, say: True,
         "take_seat": lambda name, say: True}

app = ui.Overlay(hooks)
try:
    app.ask_password()
    for _ in range(40):             # let the manager map it and hand over focus
        app.root.update()
        time.sleep(0.03)

    for letter in "abcd":
        press(letter)
        time.sleep(0.05)
        app.root.update()
    for _ in range(15):
        app.root.update()
        time.sleep(0.02)

    # The letters themselves depend on the keyboard layout, so count them
    # instead. Four in, four out means the keyboard reaches the overlay.
    typed = app.password.get()
    assert len(typed) == 4, (
        f"the overlay did not get the keyboard: {typed!r}. The window manager "
        f"is refusing it focus, which is what overrideredirect used to cause.")

    # Esc must reach us too, or the only way out is the mouse.
    assert app.root.bind_all("<Escape>"), "Esc must be bound"
    labels = [c.cget("text") for c in app.panes[0].winfo_children()
              if "text" in c.keys()]
    assert "leave" in labels, f"no way out with the mouse: {labels}"

    print("PASS")
finally:
    app.leave()
