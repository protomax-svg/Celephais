CELEPHAIS GATE
==============

Three files, and they must stay together in this folder. Both programs read
celephais.vault from the folder they sit in.

    celephais-gate    Linux
    Gate.exe          Windows
    celephais.vault   the secrets, one password opens them

HOW TO USE IT
-------------
Windows: double-click Gate.exe. A black screen covers every monitor and asks
for the key. Type the vault password. Gate.exe has no console on purpose, so
it prints nothing: the command line lives in celephais-gate.exe, built beside
it, and takes the same arguments as the Linux one.

If it says Tailscale needs administrator rights, this machine is not on the
tailnet and the Gate cannot build its own network without them. Use
Gate-admin.bat, which is the same program started with administrator rights.
A machine that is already on the tailnet never sees that message: the Gate
notices Core answers and starts no network of its own.

Linux: run ./celephais-gate from a terminal. Add --ui for the same black
screen, or give it a realm name to stay on the command line.

WAYS OUT
--------
The "leave" button on any monitor, or the Esc key, or Alt+Tab away. The
overlay is a normal window, so the desktop is never locked.

WHAT THE SCREENS DO
-------------------
First screen: the vault password. It opens the vault and brings up the private
network. Nothing is installed on the host; Tailscale is carried inside the
program and runs in memory only.

Second screen: the Realms. Each row offers only what makes sense for it.
    open here                starts that Realm and opens it over RDP, here
    use the monitors         hands that Realm the graphics card and the monitors
    park                     saves its open desktop to disk and frees its memory
    stop                     shuts it down
    give the monitors back   takes the graphics card off it again
    desktop                  which desktop that Realm comes up in, on a Realm
                             that has more than one installed

A parked Realm says "parked", not "off": its open windows are waiting on disk
and come back when you open it again.

Then it may ask for the Realm's own password, once per session, on its own
screen. A Realm set up by deploy/install-realm.sh keeps your desktop between
connections, so you come back to the windows you left open.

WHEN IT ASKS SOMETHING
----------------------
New devices: something is plugged into the machine that nobody has decided
about. A Realm on the monitors can only use what it is given, so say whether
each one travels with the monitors or stays put. Decided once, by make and
model, and it holds in any socket.

Which desktop: a Realm with more than one desktop installed can come up in
any of them, and it says which ones it can actually serve - a Wayland desktop
that is not GNOME is listed with the reason it cannot be offered. Choosing one
logs that Realm's session out, so everything open in it closes, and the Realm
is away for the half-minute it takes to come back up. What you choose is what
it boots into from then on.

A red screen with one button: going on throws something away, usually a saved
desktop that cannot survive the hardware change being asked for. Read it. The
button is deliberate.

IF SOMETHING GOES WRONG
-----------------------
A Realm the system cannot reach says why, in red, across its own row. When one
line is not enough, the RDP client's whole log is kept after any failed
connection:

    Windows   %LOCALAPPDATA%\celephais\rdp-<realm>.log
    Linux     ~/.cache/celephais/rdp-<realm>.log

"nothing answered on the Realm's RDP port" means the Realm is up but this
machine has no way to its network. Away from home, only a Realm on the tailnet
can be reached - `work` and `personal`. Windows answers on the house wire
only.

If a client cannot make sense of this machine's monitors it says so and the
next client installed here is tried. On Linux that is usually sdl-freerdp3
handing over to xfreerdp3; nothing needs to be chosen.

On Windows, install FreeRDP for several monitors and a password typed in this
program's own screen; without it Windows' own client asks in a window of its
own and gives you one screen.
