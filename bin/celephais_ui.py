"""The Gate overlay: a black layer over the running desktop, on every monitor.

Deliberately plain tkinter, which ships with Python, so the one-file build
gains no dependency.

One real window per physical monitor, each one fullscreen on its own screen.
That is the whole trick, and it was learned the hard way. The first version
used a single `overrideredirect` window stretched across the virtual desktop.
`overrideredirect` hides a window from the window manager, and KWin, like most
managers, then refuses it the keyboard and claws focus back to the window
underneath. `focus_force` does not survive that: Tk believes it holds the
focus while X disagrees, so nothing typed and Esc never fired. A managed
window gets the keyboard normally, Alt+Tab still works, and the overlay can
never trap the desktop.

Controls are drawn once per monitor, so nothing lands on the seam between two
screens. The copies share one password variable, so typing into whichever copy
holds the keyboard fills them all.

Three screens: the vault key, the Realms, then a sign-in for the Realm itself.
The last one exists so the RDP client never raises its own small dialog in the
middle of the desktop the overlay is covering. Slow work (the tailnet coming up, a
Realm booting) runs in a worker thread and reports back through `after`, so the
overlay never freezes.

`hooks` is supplied by the Gate:
  unlock(password)        -> opens the vault and brings the tailnet up
  realms()                -> list of {realm, vmid, power, seat, ip}
  open_remote(name, say, hide, user, password)
                          -> starts it, waits for READY, launches RDP.
                             It calls hide() just before the RDP window opens.
  stored_password()       -> the vault password this machine keeps, or ""
  rdp_login(realm)        -> (user, password) already known; password may be ""
  devices()               -> [ {id, name, usb, action, present}, ... ]
  set_device(id, action)  -> "seat", "never" or "forget" for that device
  take_seat(name, say, confirmed)
                          -> hands it the physical monitors. Returns the
                             warning dict instead when Core wants telling twice
  park(name, say, confirmed)
                          -> saves its session to disk and frees its RAM
  stop(name, say, confirmed)
                          -> shuts it down
  release(name, say, confirmed)
                          -> takes the RTX and the monitors back off it
"""
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk

import celephais_net                 # for redact(), below

BG, FG, DIM, ACCENT = "#07080a", "#e8e6e3", "#6b7280", "#7dd3fc"
BAD = "#f87171"
FONT = "TkDefaultFont"


def reason(error):
    """What this screen may say about a failure: the message, with any secret
    taken out of it.

    The last stop before an exception becomes text across every monitor. The
    hooks reach a long way down - Core, Tailscale, an RDP client - and one of
    them quoting a command line back is all it takes to put an auth key on the
    screen, so nothing is rendered that did not come through here.
    """
    return celephais_net.redact(str(error)).strip()


# How long the dark screen covers the hand-over to an RDP client before it
# withdraws from under - or, where the window manager stacked it wrong, from
# in front of - the client's window. See Overlay.step_aside.
STEP_ASIDE_MS = 6000


def _wayland():
    return bool(os.environ.get("WAYLAND_DISPLAY")) and sys.platform != "win32"


def screens(root):
    """Every physical monitor as (x, y, width, height), in desktop pixels.

    Empty when the platform will not say, which is the signal to fall back to
    a single window and let the window manager fullscreen it wherever it
    already is. One correct screen beats a window stretched across the whole
    desktop with its controls sitting on the seam between two monitors.
    """
    rects = []
    if _wayland():
        # A Wayland client is not allowed to place its own windows, and
        # Hyprland puts each new XWayland window on the active workspace. One
        # window per monitor came out as a 626x335 box floating on the laptop
        # screen and a fullscreen window on the other one, with the third
        # monitor untouched. So ask for nothing: one window, and the
        # compositor fullscreens it where it already is, which is the screen
        # the person is looking at.
        return rects
    try:
        if sys.platform == "win32":
            import ctypes

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.POINTER(RECT),
                                      ctypes.c_double)

            def collect(_monitor, _dc, box, _data):
                r = box.contents
                rects.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
                return 1

            ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(collect), 0)
        else:
            out = subprocess.run(["xrandr", "--listmonitors"], timeout=5,
                                 capture_output=True, text=True).stdout
            for w, h, x, y in re.findall(r"(\d+)/\d+x(\d+)/\d+\+(-?\d+)\+(-?\d+)",
                                         out):
                rects.append((int(x), int(y), int(w), int(h)))
    except Exception:                      # no xrandr, no Windows, no display
        pass
    return rects


def _crisp():
    """Ask Windows for real pixels before any window exists.

    A window that has not said it understands display scaling is drawn small
    and then stretched by the compositor, which turns every letter soft. Tk
    has no setting for this; it has to be declared on the process, and before
    the first window. Nothing to do anywhere else: X11 hands out real pixels
    already.
    """
    if os.name != "nt":
        return
    import ctypes
    for call in (lambda: ctypes.windll.shcore.SetProcessDpiAwareness(1),
                 lambda: ctypes.windll.user32.SetProcessDPIAware()):
        try:
            call()
            return
        except Exception:                 # old Windows, or already set
            continue


class Overlay:
    focus_widgets = ()

    def __init__(self, hooks):
        self.hooks = hooks
        self.events = queue.Queue()
        _crisp()
        self.root = tk.Tk()
        # Real pixels mean a point is no longer 1/72 of an inch to Tk, so the
        # text would come out tiny. Scaling puts the sizes back.
        self.root.tk.call("tk", "scaling",
                          self.root.winfo_fpixels("1i") / 72.0)
        self.root.title("Celephais")
        # None means "wherever you are": one window, no placing, which is the
        # fallback on a host that will not report its monitors.
        self.rects = screens(self.root) or [None]

        self.windows, self.panes = [], []
        for index, rect in enumerate(self.rects):
            win = self.root if index == 0 else tk.Toplevel(self.root)
            win.title("Celephais")
            win.configure(bg=BG)
            if rect:
                # Place it on the target monitor first; the manager then makes
                # it fullscreen on the screen it is already sitting on.
                x, y, w, h = rect
                win.geometry(f"{w}x{h}+{x}+{y}")
                win.update_idletasks()
            win.attributes("-fullscreen", True)
            win.attributes("-topmost", True)
            win.protocol("WM_DELETE_WINDOW", self.leave)
            content = tk.Frame(win, bg=BG)
            content.place(relx=0.5, rely=0.5, anchor="center")
            self.windows.append(win)
            self.panes.append(content)

        self.rdp = {}              # realm -> (user, password), asked once each
        self.aside = False         # stepped aside for an RDP client
        # None until the realm screen asks the Gate what this machine prefers;
        # the vault is not open yet when this runs.
        self.one_monitor = None
        self.monitor_switches = []
        self.root.bind_all("<Escape>", lambda _e: self.leave())
        self.root.after(80, self._drain)

    # --- plumbing -------------------------------------------------------
    def claim_focus(self, tries=8):
        """Point every window at its own entry, then ask for the keyboard.

        Tk keeps a separate focus for each toplevel. The manager hands the
        keyboard to one window of its choosing, usually the last one mapped,
        so setting the focus on one window's entry is not enough: keys arrive
        at a different window where nothing is focused and vanish. Every
        window gets its own entry focused, and then it does not matter which
        one the manager picks.

        Asking is repeated for about a second, because a desktop that was busy
        starting something else can take a moment to settle.
        """
        try:
            if self.root.state() == "withdrawn" or self.aside:
                # Stepped aside, the overlay is still mapped: without this a
                # grab still queued from the realm screen would take the
                # keyboard back from the remote desktop a moment after it
                # opened.
                return
            for widget in self.focus_widgets:
                widget.focus_set()                # per window, not per app
            self.root.focus_force()               # prefer the primary monitor
        except tk.TclError:                       # window already gone
            return
        if tries:
            self.root.after(150, self.claim_focus, tries - 1)

    def hide(self):
        """Get out of the way of a child window, such as the RDP client."""
        for win in self.windows:
            try:
                win.attributes("-topmost", False)
                win.withdraw()
            except tk.TclError:
                pass

    def step_aside(self):
        """Make way for the RDP client without showing the desktop between.

        hide() withdrew every window before the client was launched, and for
        the second or two until its window appeared the person was looking at
        their own desktop, icons and all, between the Gate and the Realm. Now
        the overlay stays, empty and dark, and only stops being topmost: the
        client's window opens over it, and if the client is shown on fewer
        monitors than there are, the others stay dark rather than showing the
        desktop.

        Then, once the client has had STEP_ASIDE_MS to put its window up, the
        overlay withdraws as before. A new window is normally put above
        everything that is not topmost, but not everywhere - Hyprland opens it
        behind a fullscreen one by default - and a dark screen in front of a
        connected session is far worse than none: Esc on it would close the
        Gate, and the session with it. By then the client's window is up and
        covers the withdrawal; a session that has already ended has called
        show(), and is not withdrawn after the fact.
        """
        self.aside = True
        self.clear()
        for win in self.windows:
            try:
                win.attributes("-topmost", False)
            except tk.TclError:
                pass
        self.root.after(STEP_ASIDE_MS, lambda: self.aside and self.hide())

    def show(self):
        self.aside = False
        for win in self.windows:
            try:
                win.deiconify()
                win.attributes("-topmost", True)
            except tk.TclError:
                pass
        self.claim_focus()

    def leave(self):
        try:
            # Drop the queued callbacks first. Destroying with `after` jobs
            # still pending makes Tcl complain about commands that no longer
            # exist, which reads like a crash and is only litter.
            for job in self.root.tk.call("after", "info"):
                self.root.after_cancel(job)
            self.root.destroy()
        except tk.TclError:
            pass

    def _drain(self):
        """Apply whatever the worker thread sent us. Tk is not thread safe.

        One callback must never stop the loop. A worker that finishes after
        its screen has been replaced writes into widgets that no longer exist,
        and the TclError used to escape here and take the drain with it: from
        then on nothing the workers sent ever arrived, and the overlay sat on
        a dead screen with no word of why.
        """
        while True:
            try:
                fn = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception as exc:      # a stale screen, usually
                print(f"overlay: {exc}", file=sys.stderr, flush=True)
        self.root.after(80, self._drain)

    def post(self, fn):
        self.events.put(fn)

    def work(self, job, done):
        def run():
            try:
                result = job()
            except (Exception, SystemExit) as exc:  # show it, never swallow it
                # SystemExit is a BaseException, so `except Exception` let it
                # past. The Gate raises it for a boot timeout and for a missing
                # RDP client, and the thread died silently every time: no
                # message, and the overlay stayed hidden for ever.
                # Python clears the name at the end of the except block, so
                # the callback has to capture it under its own name.
                failure = exc
                self.post(lambda: done(None, failure))
                return
            self.post(lambda: done(result, None))
        threading.Thread(target=run, daemon=True).start()

    def clear(self):
        self.focus_widgets = ()
        for pane in self.panes:
            for child in pane.winfo_children():
                child.destroy()

    # --- widgets --------------------------------------------------------
    def label(self, parent, text, size=14, colour=FG, pady=4):
        widget = tk.Label(parent, text=text, bg=BG, fg=colour, font=(FONT, size))
        widget.pack(pady=pady)
        return widget

    def button(self, parent, text, command, accent=False):
        return tk.Button(parent, text=text, command=command,
                         bg=BG, fg=ACCENT if accent else FG,
                         activebackground="#15181d", activeforeground=FG,
                         relief="flat", bd=0, padx=16, pady=6,
                         highlightthickness=1,
                         highlightbackground=ACCENT if accent else DIM,
                         font=(FONT, 11))

    @staticmethod
    def say_on(widgets, text, colour=DIM):
        """Same message on every monitor's copy of a label."""
        for widget in widgets:
            try:
                widget.config(text=text, fg=colour)
            except tk.TclError:
                pass

    def secret_field(self, pane, variable, entries, eyes, on_return):
        """A hidden entry plus the button that reveals it.

        Revealing matters: three keyboard layouts on one machine is normal
        here, and a password typed under the wrong one looks exactly like a
        password typed wrong. Every monitor's copies share one list, so the
        eye works whichever copy you click.
        """
        entry = tk.Entry(pane, show="•", width=26, justify="center",
                         textvariable=variable, bg="#101317", fg=FG,
                         insertbackground=FG, relief="flat", font=(FONT, 16))
        entry.pack(ipady=8, pady=6)
        entry.bind("<Return>", on_return)
        entry.bind("<Button-1>", lambda e: e.widget.focus_set())
        entries.append(entry)

        def reveal():
            hidden = bool(entries[0].cget("show"))
            for widget in entries:
                widget.config(show="" if hidden else "\u2022")
            for widget in eyes:
                widget.config(text="hide the key" if hidden else "show the key")

        eye = self.button(pane, "show the key", reveal)
        eye.pack(pady=(8, 0))
        eyes.append(eye)
        return entry

    # --- screens --------------------------------------------------------
    def ask_password(self, message=None):
        self.clear()
        self.password = tk.StringVar()            # shared by every copy
        entries, status, eyes = [], [], []

        for pane in self.panes:
            self.label(pane, "CELEPHAIS", 30, FG, pady=0)
            self.label(pane, "insert the key", 12, DIM, pady=(2, 18))
            self.secret_field(pane, self.password, entries, eyes,
                              lambda _e: submit())
            status.append(self.label(pane, message or "", 11,
                                     BAD if message else DIM))

        def submit(_event=None):
            password = self.password.get()
            if not password:
                return
            for entry in entries:
                entry.config(state="disabled")
            self.say_on(status, "opening the vault…")
            self.work(lambda: self.hooks["unlock"](password), after_unlock)

        def after_unlock(_result, error):
            if error:
                self.ask_password(reason(error))
            else:
                self.show_realms()

        for pane in self.panes:
            self.button(pane, "unlock", submit, accent=True).pack(pady=14)
            self.button(pane, "leave", self.leave).pack()
            self.label(pane, "or press Esc", 9, DIM, pady=(10, 0))

        self.submit = submit            # the hidden self-check calls this
        self.focus_widgets = entries
        self.claim_focus()

    def monitor_text(self):
        return ("remote desktop: one monitor" if self.one_monitor
                else "remote desktop: every monitor")

    def flip_monitors(self):
        """Put the remote desktop on one screen, or back on all of them.

        Nothing is reread and nothing is sent: the choice is carried into the
        next connection, so a Realm that is already open is not disturbed by
        it. Every screen's copy of the switch is relabelled, like every other
        control the overlay mirrors.
        """
        self.one_monitor = not self.one_monitor
        for switch in self.monitor_switches:
            try:
                switch.config(text=self.monitor_text())
            except tk.TclError:                   # that screen is gone already
                pass

    def show_realms(self, note=""):
        self.clear()
        if self.one_monitor is None:
            prefers = self.hooks.get("prefer_one_monitor")
            self.one_monitor = bool(prefers and prefers())
        tables, status = [], []
        self.monitor_switches = []
        for pane in self.panes:
            self.label(pane, "choose an environment", 20, FG, pady=(0, 16))
            rows = tk.Frame(pane, bg=BG)
            rows.pack()
            tables.append(rows)
            status.append(self.label(pane, note, 11, DIM, pady=(18, 0)))
            switch = self.button(pane, self.monitor_text(), self.flip_monitors)
            switch.pack(pady=(12, 0))
            self.monitor_switches.append(switch)
            self.button(pane, "leave", self.leave).pack(pady=(14, 0))

        def refresh(realms, error):
            if error:
                self.say_on(status, reason(error), BAD)
                return
            self.say_on(status, note)
            for rows in tables:
                for index, realm in enumerate(realms):
                    seat = "  ● on the monitors" if realm.get("seat") else ""
                    # A Realm Proxmox cannot answer for says only "error" in a
                    # column this narrow, which tells nobody anything.
                    trouble = realm.get("error")
                    tk.Label(rows, text=realm["realm"], bg=BG, fg=FG,
                             font=(FONT, 15), anchor="w", width=12
                             ).grid(row=index, column=0, sticky="w", pady=6)
                    tk.Label(rows, text=f"{realm['power'].lower()}{seat}", bg=BG,
                             fg=BAD if trouble else DIM, font=(FONT, 11),
                             anchor="w", width=22
                             ).grid(row=index, column=1, sticky="w", padx=10)
                    if trouble:
                        tk.Label(rows, text=trouble, bg=BG, fg=BAD, anchor="w",
                                 font=(FONT, 10), wraplength=520, justify="left"
                                 ).grid(row=index, column=2, columnspan=5,
                                        sticky="w", padx=5)
                        continue
                    name = realm["realm"]
                    # One row shows only what makes sense for that Realm right
                    # now: no parking what is not running, no taking monitors
                    # back off a Realm that does not hold them.
                    offered = {"open_remote": ("open here", True),
                               "take_seat": ("use the monitors", False)}
                    if realm["power"] == "READY":
                        offered["park"] = ("park", False)
                    # Anything running can be stopped, whatever it looks like
                    # from here. Offering stop only when READY left a Realm
                    # that never got there - asleep, or with no remote desktop
                    # - holding the monitors with no way to get them back.
                    if realm["power"] in ("READY", "BOOTING", "ASLEEP"):
                        offered["stop"] = ("stop", False)
                    if realm.get("seat"):
                        offered["release"] = ("give the monitors back", False)
                    # Only a Realm that is up can be asked: the choice is kept
                    # inside the guest, and the agent answers for nobody when
                    # the VM is off.
                    if realm["power"] == "READY":
                        offered["desktop"] = ("desktop", False)
                    # A fixed column per action, so the same button sits under
                    # the same button on every row.
                    for column, action in enumerate(("open_remote", "take_seat",
                                                     "park", "stop", "release",
                                                     "desktop"),
                                                    start=2):
                        if action not in offered:
                            continue
                        text, accent = offered[action]
                        self.button(rows, text,
                                    lambda n=name, a=action: self.go(a, n),
                                    accent=accent).grid(row=index, column=column,
                                                        padx=5, sticky="w")

        self.say_on(status, "reading the realms…")
        self.work(self.hooks["realms"], refresh)

    def ask_login(self, name, message=None):
        """Sign in to the Realm, in our own screen.

        FreeRDP would ask for this itself, in a small window of its own, after
        the overlay had already stepped aside for it. Asking here keeps the
        whole handover inside one full-screen surface.
        """
        self.clear()
        user, password = self.rdp.get(name) or ("", "")
        self.rdp_user = tk.StringVar(value=user)
        self.rdp_password = tk.StringVar(value=password)
        users, secrets, eyes, status = [], [], [], []

        for pane in self.panes:
            self.label(pane, name, 26, FG, pady=(0, 2))
            self.label(pane, "sign in", 12, DIM, pady=(0, 16))
            entry = tk.Entry(pane, width=26, justify="center",
                             textvariable=self.rdp_user, bg="#101317", fg=FG,
                             insertbackground=FG, relief="flat", font=(FONT, 16))
            entry.pack(ipady=8, pady=6)
            entry.bind("<Return>", lambda _e: submit())
            entry.bind("<Button-1>", lambda e: e.widget.focus_set())
            users.append(entry)
            self.secret_field(pane, self.rdp_password, secrets, eyes,
                              lambda _e: submit())
            status.append(self.label(pane, message or "", 11,
                                     BAD if message else DIM))

        def submit(_event=None):
            if not self.rdp_password.get():
                return
            self.rdp[name] = (self.rdp_user.get(), self.rdp_password.get())
            self.go("open_remote", name)

        for pane in self.panes:
            self.button(pane, "open", submit, accent=True).pack(pady=14)
            self.button(pane, "back", self.show_realms).pack()

        self.submit_login = submit      # the headless self-check calls this
        self.focus_widgets = secrets
        self.claim_focus()

    def ask_confirm(self, name, warning, proceed):
        """Core answered that going on throws something away. Say what, and
        make it a second, deliberate press rather than a surprise."""
        self.clear()
        for pane in self.panes:
            self.label(pane, name, 26, FG, pady=(0, 10))
            tk.Label(pane, text=warning, bg=BG, fg=BAD, font=(FONT, 12),
                     wraplength=560, justify="center").pack(pady=(0, 20))
            self.button(pane, "go on", proceed, accent=True).pack(pady=(0, 8))
            self.button(pane, "back", self.show_realms).pack()
        self.focus_widgets = ()
        self.claim_focus()

    def ask_devices(self, name, devices):
        """What is plugged in that nobody has decided about yet.

        A Realm on the monitors can only use the devices the host hands it, so
        this is the moment to decide: a keyboard nobody approved stays on the
        host and the Realm comes up without one. Decided once, by make and
        model, and it holds in any socket.
        """
        self.clear()
        unknown = [d for d in devices if d.get("present") and not d.get("action")]
        if not unknown:
            return self.go("take_seat", name, checked=True)

        def decide(ident, action):
            self.work(lambda: self.hooks["set_device"](ident, action),
                      lambda _r, error: self.go("take_seat", name)
                      if not error else self.show_realms(reason(error)))

        for pane in self.panes:
            self.label(pane, "new devices", 24, FG, pady=(0, 4))
            self.label(pane, "decide before the monitors move", 11, DIM, pady=(0, 16))
            rows = tk.Frame(pane, bg=BG)
            rows.pack()
            for index, device in enumerate(unknown):
                tk.Label(rows, text=device["name"], bg=BG, fg=FG, anchor="w",
                         font=(FONT, 13), width=30
                         ).grid(row=index, column=0, sticky="w", pady=5)
                about = device["usb"]
                if device.get("role"):
                    about += "   " + device["role"]
                tk.Label(rows, text=about, bg=BG, fg=DIM, anchor="w",
                         font=(FONT, 10), width=24
                         ).grid(row=index, column=1, sticky="w", padx=8)
                self.button(rows, "with the monitors",
                            lambda i=device["id"]: decide(i, "seat"), accent=True
                            ).grid(row=index, column=2, padx=5)
                self.button(rows, "leave it alone",
                            lambda i=device["id"]: decide(i, "never")
                            ).grid(row=index, column=3, padx=5)
            self.button(pane, "go on without deciding",
                        lambda: self.go("take_seat", name, checked=True)
                        ).pack(pady=(20, 0))
            self.button(pane, "back", self.show_realms).pack(pady=(8, 0))
        self.focus_widgets = ()
        self.claim_focus()

    def ask_desktop(self, name, info):
        """Which desktop this Realm comes up in, and a way to change it.

        The Realm answers for itself: what it boots into, what is installed on
        it, and which of those Celephais can actually serve - a Wayland
        desktop that is not GNOME is listed and refused, with the reason
        where the button would be, because a desktop nobody can connect to is
        worth knowing about before it is chosen rather than after.

        Choosing is not free and the screen says so: the session is logged
        out, so everything open in it closes, and the Realm is away for as
        long as it takes to log back in.
        """
        self.clear()
        choices = info.get("choices") or []
        current = info.get("current", "")
        for pane in self.panes:
            self.label(pane, f"desktop for {name}", 24, FG, pady=(0, 4))
            self.label(pane, "it boots into this, and comes back into it "
                             "after every restart", 11, DIM, pady=(0, 16))
            if not choices:
                tk.Label(pane, text=info.get("detail", "this Realm cannot "
                                             "choose its desktop"),
                         bg=BG, fg=BAD, font=(FONT, 12), wraplength=560,
                         justify="center").pack(pady=(0, 20))
            rows = tk.Frame(pane, bg=BG)
            rows.pack()
            for index, choice in enumerate(choices):
                here = choice["name"] == current
                tk.Label(rows, text=choice["label"], bg=BG, anchor="w",
                         fg=FG if choice.get("usable") else DIM,
                         font=(FONT, 13), width=22
                         ).grid(row=index, column=0, sticky="w", pady=5)
                tk.Label(rows, text="now" if here else "", bg=BG, fg=DIM,
                         font=(FONT, 10), width=5
                         ).grid(row=index, column=1, sticky="w")
                if choice.get("usable"):
                    tk.Label(rows, text=choice.get("backend", ""), bg=BG,
                             fg=DIM, font=(FONT, 10), width=10, anchor="w"
                             ).grid(row=index, column=2, sticky="w", padx=6)
                    if not here:
                        self.button(rows, "boot into this",
                                    lambda c=choice["name"]: self.switch(name, c)
                                    ).grid(row=index, column=3, padx=5)
                else:
                    # The reason, where its button would have been: a desktop
                    # that cannot be served is not a choice, and saying only
                    # "no" would send somebody looking for a fault.
                    tk.Label(rows, text=choice.get("why", ""), bg=BG, fg=DIM,
                             font=(FONT, 10), wraplength=300, justify="left",
                             anchor="w").grid(row=index, column=2, columnspan=2,
                                              sticky="w", padx=6)
            self.label(pane, "switching logs the session out: everything open "
                             "in it closes", 11, DIM, pady=(18, 0))
            # The agent's desktop is not one of the choices above and must not
            # look like one: nothing switches to it, it is already running
            # beside whichever the Realm boots into, and opening it leaves
            # that one exactly where it was.
            agent = info.get("agent")
            if agent:
                where = agent.get("display", "")
                what = (agent.get("session") or "").title() or "a desktop"
                kind = (agent.get("type") or "").upper()
                self.label(pane, f"this Realm also keeps a desktop of its own "
                                 f"for an agent: {what} on {kind}, at {where}",
                           11, DIM, pady=(16, 2))
                if agent.get("running"):
                    self.button(pane, "watch the agent",
                                lambda: self.go("open_agent", name)
                                ).pack(pady=(2, 0))
                else:
                    self.label(pane, "it is not running", 11, BAD, pady=(0, 0))
            self.button(pane, "back", self.show_realms).pack(pady=(14, 0))
        self.focus_widgets = ()
        self.claim_focus()

    def switch(self, name, choice):
        """Carry out the change, reporting while the Realm is away."""
        self.clear()
        lines = []
        for pane in self.panes:
            self.label(pane, name, 26, FG, pady=(0, 10))
            lines.append(self.label(pane, f"switching to {choice}\u2026", 12, DIM))
            self.button(pane, "leave", self.leave).pack(pady=(18, 0))

        def say(text):
            self.post(lambda: self.say_on(lines, text))

        self.work(lambda: self.hooks["set_desktop"](name, choice, say),
                  lambda _result, error: self.show_realms(
                      reason(error) if error else f"{name} boots into {choice} now"))

    def go(self, action, name, confirmed=False, checked=False):
        if action == "take_seat" and not checked:
            # The Realm is about to take the physical machine. Look at what is
            # plugged into it first.
            return self.work(
                self.hooks["devices"],
                lambda result, error: self.go(action, name, confirmed, True)
                if error else self.ask_devices(name, result))
        if action == "desktop":
            # Asked of the Realm itself, and only when somebody wants to know:
            # it goes through the guest agent, which is too slow to put in the
            # list every Realm is drawn from.
            return self.work(
                lambda: self.hooks["desktops"](name),
                lambda result, error: self.show_realms(reason(error))
                if error else self.ask_desktop(name, result))
        if action in ("open_remote", "open_agent"):
            if name not in self.rdp:
                ask = self.hooks.get("rdp_login", lambda _n: ("", ""))
                self.rdp[name] = tuple(ask(name))
            if not self.rdp[name][1]:
                return self.ask_login(name)
        self.clear()
        lines = []
        for pane in self.panes:
            self.label(pane, name, 26, FG, pady=(0, 10))
            lines.append(self.label(pane, "starting…", 12, DIM))
            self.button(pane, "leave", self.leave).pack(pady=(18, 0))

        def say(text):
            self.post(lambda: self.say_on(lines, text))

        def done(result, error):
            if isinstance(result, dict) and result.get("confirm"):
                return self.ask_confirm(
                    name, result.get("warning", "this cannot be undone"),
                    lambda: self.go(action, name, confirmed=True, checked=True))
            self.show_realms(reason(error) if error else "")

        if action in ("open_remote", "open_agent"):
            # Stay visible and keep reporting while the Realm boots. Only the
            # RDP window itself has to be on top of us, and the Gate says when
            # it is about to open one.
            def hide_now():
                """Called from the worker thread, so hand it to the UI one."""
                hidden = threading.Event()
                self.post(lambda: (self.step_aside(), hidden.set()))
                hidden.wait(timeout=5)

            def finished(result, error):
                self.show()
                # A refused password must be asked again, and only a refused
                # one: a boot timeout is not a reason to retype anything.
                if error and "logon" in reason(error).lower():
                    self.rdp.pop(name, None)
                    # Name both fields: each Realm has its own account, and a
                    # wrong username is refused exactly like a wrong password.
                    return self.ask_login(
                        name, "that username and password were refused")
                done(result, error)

            self.work(lambda: self.hooks[action](
                name, say, hide_now, *self.rdp[name],
                one_monitor=bool(self.one_monitor)), finished)
        else:
            self.work(lambda: self.hooks[action](name, say, confirmed), done)

    def run(self):
        self.ask_password()
        # A machine that keeps the key opens the vault by itself, and the
        # screen is only there to say so while it does. A stored key that no
        # longer fits lands back on this same screen with the reason on it,
        # so trying costs nothing.
        stored = self.hooks.get("stored_password", lambda: "")()
        if stored:
            self.password.set(stored)
            self.post(self.submit)
        self.root.mainloop()


def run(hooks):
    Overlay(hooks).run()
