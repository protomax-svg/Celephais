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
for the key. Type the vault password.

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

A red screen with one button: going on throws something away, usually a saved
desktop that cannot survive the hardware change being asked for. Read it. The
button is deliberate.

IF SOMETHING GOES WRONG
-----------------------
A Realm the system cannot reach says why, in red, across its own row.
On Windows, install FreeRDP for several monitors and a password typed in this
program's own screen; without it Windows' own client asks in a window of its
own and gives you one screen.
