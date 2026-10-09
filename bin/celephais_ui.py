"""The Gate overlay: a black layer over the running desktop, on every monitor.

Deliberately plain tkinter, which ships with Python, so the one-file build
gains no dependency. The window is borderless, topmost and sized to the whole
virtual desktop, which is what spans several monitors on both X11 and Windows.

The controls are drawn once per physical monitor, so nothing lands on the seam
between two screens. All the copies share one password variable, so typing into
whichever copy has the pointer fills them all.

`overrideredirect` hides the window from the window manager, and a window the
manager does not know about is never given the keyboard. So focus is taken by
force, and every screen carries a "leave" button: the mouse is the one way out
that cannot depend on a key press arriving.

Two screens: a password, then the Realms. Slow work (the tailnet coming up, a
Realm booting) runs in a worker thread and reports back through `after`, so the
overlay never freezes.

`hooks` is supplied by the Gate:
  unlock(password)        -> opens the vault and brings the tailnet up
  realms()                -> list of {realm, vmid, power, seat, ip}
  open_remote(name, say)  -> starts it, waits for READY, launches RDP
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

    tkinter has no idea where one screen ends and the next begins, so ask the
    platform. If that fails we fall back to one rectangle covering everything,
    which is the old behaviour and still usable.
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
    if not rects:
        rects = [(0, 0, root.winfo_vrootwidth(), root.winfo_vrootheight())]
    return rects


class Overlay:
    def __init__(self, hooks):
        self.hooks = hooks
        self.events = queue.Queue()
        self.root = tk.Tk()
        self.root.title("Celephais")
        self.root.configure(bg=BG)
        self.root.overrideredirect(True)          # no title bar, no decoration
        self.root.attributes("-topmost", True)
        self.root.update_idletasks()

        rects = screens(self.root)
        left = min(x for x, _y, _w, _h in rects)
        top = min(y for _x, y, _w, _h in rects)
        width = max(x + w for x, _y, w, _h in rects) - left
        height = max(y + h for _x, y, _w, h in rects) - top
        self.root.geometry(f"{width}x{height}+{left}+{top}")

        # One content frame per monitor, centred inside that monitor.
        self.panes = []
        for x, y, w, h in rects:
            area = tk.Frame(self.root, bg=BG)
            area.place(x=x - left, y=y - top, width=w, height=h)
            content = tk.Frame(area, bg=BG)
            content.place(relx=0.5, rely=0.5, anchor="center")
            self.panes.append(content)

        self.root.bind_all("<Escape>", lambda _e: self.leave())
        self.root.after(80, self._drain)
        self.claim_focus()

    # --- plumbing -------------------------------------------------------
    def claim_focus(self, tries=12):
        """Take the keyboard. The window manager will not hand it to us."""
        try:
            if self.root.state() == "withdrawn":
                return
            self.root.focus_force()
            if self.focus_widget is not None:
                self.focus_widget.focus_set()
        except tk.TclError:                       # window already gone
            return
        if tries:
            self.root.after(150, self.claim_focus, tries - 1)

    focus_widget = None

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
            except Exception as exc:                # show it, never swallow it
                # Python clears the name at the end of the except block, so
                # the callback has to capture it under its own name.
                failure = exc
                self.post(lambda: done(None, failure))
                return
            self.post(lambda: done(result, None))
        threading.Thread(target=run, daemon=True).start()

    def clear(self):
        self.focus_widget = None
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
            self.button(pane, "leave", self.leave).pack(pady=(0, 0))
            self.label(pane, "or press Esc", 9, DIM, pady=(10, 0))

        self.submit = submit            # the hidden self-check calls this
        self.focus_widget = entries[0]
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
            # The RDP window has to be on top of us, not under us.
            self.root.attributes("-topmost", False)
            self.root.withdraw()

            def finished(result, error):
                self.root.deiconify()
                self.root.attributes("-topmost", True)
                self.claim_focus()
                done(result, error)

            self.work(lambda: self.hooks[action](name, say), finished)
        else:
            self.work(lambda: self.hooks[action](name, say), done)

    def run(self):
        self.ask_password()
        self.root.mainloop()


def run(hooks):
    Overlay(hooks).run()
