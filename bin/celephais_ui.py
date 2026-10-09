"""The Gate overlay: a black layer over the running desktop, on every monitor.

Deliberately plain tkinter, which ships with Python, so the one-file build
gains no dependency. The window is borderless, topmost and sized to the whole
virtual desktop, which is what spans several monitors on both X11 and Windows.

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
import threading
import tkinter as tk

BG, FG, DIM, ACCENT = "#07080a", "#e8e6e3", "#6b7280", "#7dd3fc"
FONT = "TkDefaultFont"


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
        self.root.geometry(f"{self.root.winfo_vrootwidth()}x"
                           f"{self.root.winfo_vrootheight()}+0+0")
        self.root.bind("<Escape>", lambda _e: self.root.destroy())
        self.body = tk.Frame(self.root, bg=BG)
        self.body.place(relx=0.5, rely=0.5, anchor="center")
        self.root.after(80, self._drain)

    # --- plumbing -------------------------------------------------------
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
        for child in self.body.winfo_children():
            child.destroy()

    def label(self, text, size=14, colour=FG, pady=4):
        widget = tk.Label(self.body, text=text, bg=BG, fg=colour,
                          font=(FONT, size))
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

    # --- screens --------------------------------------------------------
    def ask_password(self, message=None):
        self.clear()
        self.label("CELEPHAIS", 30, FG, pady=(0))
        self.label("insert the key", 12, DIM, pady=(2, 18))
        entry = tk.Entry(self.body, show="•", width=26, justify="center",
                         bg="#101317", fg=FG, insertbackground=FG,
                         relief="flat", font=(FONT, 16))
        entry.pack(ipady=8, pady=6)
        entry.focus_set()
        status = self.label(message or "", 11, "#f87171" if message else DIM)

        def submit(_event=None):
            password = entry.get()
            if not password:
                return
            entry.config(state="disabled")
            status.config(text="opening the vault…", fg=DIM)
            self.work(lambda: self.hooks["unlock"](password), after_unlock)

        def after_unlock(_result, error):
            if error:
                self.ask_password(str(error))
            else:
                self.show_realms()

        entry.bind("<Return>", submit)
        self.submit = submit            # the hidden self-check calls this
        self.button(self.body, "unlock", submit, accent=True).pack(pady=14)
        self.label("Esc to leave", 9, DIM, pady=(14, 0))

    def show_realms(self, note=""):
        self.clear()
        self.label("choose an environment", 20, FG, pady=(0, 16))
        rows = tk.Frame(self.body, bg=BG)
        rows.pack()
        status = self.label(note, 11, DIM, pady=(18, 0))

        def refresh(realms, error):
            if error:
                status.config(text=str(error), fg="#f87171")
                return
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
                            lambda n=name: self.go("open_remote", n, status),
                            accent=True).grid(row=index, column=2, padx=6)
                self.button(rows, "use the monitors",
                            lambda n=name: self.go("take_seat", n, status)
                            ).grid(row=index, column=3, padx=6)

        status.config(text="reading the realms…", fg=DIM)
        self.work(self.hooks["realms"], refresh)

    def go(self, action, name, status):
        self.clear()
        self.label(name, 26, FG, pady=(0, 10))
        line = self.label("starting…", 12, DIM)

        def say(text):
            self.post(lambda: line.config(text=text))

        def done(_result, error):
            if error:
                self.show_realms(str(error))
            else:
                self.show_realms()

        if action == "open_remote":
            # The RDP window has to be on top of us, not under us.
            self.root.attributes("-topmost", False)
            self.root.withdraw()

            def finished(result, error):
                self.root.deiconify()
                self.root.attributes("-topmost", True)
                done(result, error)

            self.work(lambda: self.hooks[action](name, say), finished)
        else:
            self.work(lambda: self.hooks[action](name, say), done)

    def run(self):
        self.ask_password()
        self.root.mainloop()


def run(hooks):
    Overlay(hooks).run()
