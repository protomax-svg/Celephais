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

Two screens: a password, then the Realms. Slow work (the tailnet coming up, a
Realm booting) runs in a worker thread and reports back through `after`, so the
overlay never freezes.

`hooks` is supplied by the Gate:
  unlock(password)        -> opens the vault and brings the tailnet up
  realms()                -> list of {realm, vmid, power, seat, ip}
  open_remote(name, say, hide)
                          -> starts it, waits for READY, launches RDP.
                             It calls hide() just before the RDP window opens.
  take_seat(name, say)    -> hands it the physical monitors
"""
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk

BG, FG, DIM, ACCENT = "#07080a", "#e8e6e3", "#6b7280", "#7dd3fc"
BAD = "#f87171"
FONT = "TkDefaultFont"


def screens(root):
    """Every physical monitor as (x, y, width, height), in desktop pixels.

    Empty when the platform will not say, which is the signal to fall back to
    a single window and let the window manager fullscreen it wherever it
    already is. One correct screen beats a window stretched across the whole
    desktop with its controls sitting on the seam between two monitors.
    """
    rects = []
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


class Overlay:
    focus_widgets = ()

    def __init__(self, hooks):
        self.hooks = hooks
        self.events = queue.Queue()
        self.root = tk.Tk()
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
            if self.root.state() == "withdrawn":
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

    def show(self):
        for win in self.windows:
            try:
                win.deiconify()
                win.attributes("-topmost", True)
            except tk.TclError:
                pass
        self.claim_focus()

    def leave(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _drain(self):
        """Apply whatever the worker thread sent us. Tk is not thread safe."""
        while True:
            try:
                fn = self.events.get_nowait()
            except queue.Empty:
                break
            fn()
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

    # --- screens --------------------------------------------------------
    def ask_password(self, message=None):
        self.clear()
        self.password = tk.StringVar()            # shared by every copy
        entries, status = [], []
        for pane in self.panes:
            self.label(pane, "CELEPHAIS", 30, FG, pady=0)
            self.label(pane, "insert the key", 12, DIM, pady=(2, 18))
            entry = tk.Entry(pane, show="•", width=26, justify="center",
                             textvariable=self.password,
                             bg="#101317", fg=FG, insertbackground=FG,
                             relief="flat", font=(FONT, 16))
            entry.pack(ipady=8, pady=6)
            entry.bind("<Return>", lambda _e: submit())
            entry.bind("<Button-1>", lambda e: e.widget.focus_set())
            entries.append(entry)
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
                self.ask_password(str(error))
            else:
                self.show_realms()

        for pane in self.panes:
            self.button(pane, "unlock", submit, accent=True).pack(pady=14)
            self.button(pane, "leave", self.leave).pack()
            self.label(pane, "or press Esc", 9, DIM, pady=(10, 0))

        self.submit = submit            # the hidden self-check calls this
        self.focus_widgets = entries
        self.claim_focus()

    def show_realms(self, note=""):
        self.clear()
        tables, status = [], []
        for pane in self.panes:
            self.label(pane, "choose an environment", 20, FG, pady=(0, 16))
            rows = tk.Frame(pane, bg=BG)
            rows.pack()
            tables.append(rows)
            status.append(self.label(pane, note, 11, DIM, pady=(18, 0)))
            self.button(pane, "leave", self.leave).pack(pady=(14, 0))

        def refresh(realms, error):
            if error:
                self.say_on(status, str(error), BAD)
                return
            self.say_on(status, note)
            for rows in tables:
                for index, realm in enumerate(realms):
                    seat = "  ● on the monitors" if realm.get("seat") else ""
                    tk.Label(rows, text=realm["realm"], bg=BG, fg=FG,
                             font=(FONT, 15), anchor="w", width=12
                             ).grid(row=index, column=0, sticky="w", pady=6)
                    tk.Label(rows, text=f"{realm['power'].lower()}{seat}", bg=BG,
                             fg=DIM, font=(FONT, 11), anchor="w", width=22
                             ).grid(row=index, column=1, sticky="w", padx=10)
                    name = realm["realm"]
                    self.button(rows, "open here",
                                lambda n=name: self.go("open_remote", n),
                                accent=True).grid(row=index, column=2, padx=6)
                    self.button(rows, "use the monitors",
                                lambda n=name: self.go("take_seat", n)
                                ).grid(row=index, column=3, padx=6)

        self.say_on(status, "reading the realms…")
        self.work(self.hooks["realms"], refresh)

    def go(self, action, name):
        self.clear()
        lines = []
        for pane in self.panes:
            self.label(pane, name, 26, FG, pady=(0, 10))
            lines.append(self.label(pane, "starting…", 12, DIM))
            self.button(pane, "leave", self.leave).pack(pady=(18, 0))

        def say(text):
            self.post(lambda: self.say_on(lines, text))

        def done(_result, error):
            self.show_realms(str(error) if error else "")

        if action == "open_remote":
            # Stay visible and keep reporting while the Realm boots. Only the
            # RDP window itself has to be on top of us, and the Gate says when
            # it is about to open one.
            def hide_now():
                """Called from the worker thread, so hand it to the UI one."""
                hidden = threading.Event()
                self.post(lambda: (self.hide(), hidden.set()))
                hidden.wait(timeout=5)

            def finished(result, error):
                self.show()
                done(result, error)

            self.work(lambda: self.hooks[action](name, say, hide_now), finished)
        else:
            self.work(lambda: self.hooks[action](name, say), done)

    def run(self):
        self.ask_password()
        self.root.mainloop()


def run(hooks):
    Overlay(hooks).run()
