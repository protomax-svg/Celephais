# Celephaïs — Build / Handoff Log

_Last updated: 2026-10-08_

## Goal
Celephaïs is a personal-cloud-computing platform where persistent virtual environments ("Realms") run on a Proxmox host and physical devices act as portals/thin clients.

Current focus: prove a reliable **Local Seat** model where one Realm owns the physical RTX 2060 + monitors + USB input, while the same Realm can also run in remote/headless mode over GNOME Remote Desktop.

## Host hardware
- CPU: Intel Core i7-8700K (6c/12t), UHD 630
- Board: ASUS TUF Z390M-PRO GAMING (WI-FI)
- RAM: 64 GB
- GPU: NVIDIA GeForce RTX 2060 6 GB
- Proxmox VE 9.2.2
- Main Proxmox SSD: Kingston 256 GB
- Data disk `/dev/sdb`: HGST 2 TB, contains NTFS partitions — preserve
- Fallback Kubuntu disk `/dev/sdc`: Samsung 850 EVO 250 GB — preserve until Proxmox setup is stable
- All three physical monitors are connected to the RTX 2060
- Host i915 is blacklisted; Proxmox is effectively headless

## RTX 2060 passthrough
RTX functions:
- `01:00.0` GPU `10de:1f08`
- `01:00.1` audio `10de:10f9`
- `01:00.2` USB
- `01:00.3` UCSI

GPU is bound to `vfio-pci` on the host.

Current production-style passthrough uses:
- `01:00.0` GPU
- `01:00.1` HDMI/DP audio

Do not depend on UHD 630 passthrough; that path proved fragile and was deprioritized.

## Local Seat concept
One Realm at a time owns:
- RTX 2060
- all 3 physical monitors
- keyboard
- mouse
- later possibly audio / selected USB devices

Realm switching is:
1. clean shutdown current owner
2. release GPU/USB
3. start target Realm
4. target acquires RTX/USB

No GPU hot-move between running VMs is assumed.

## Windows Gaming VM — VM 100
Known-good facts:
- OVMF / q35
- CPU host
- RTX 2060 passthrough works
- all 3 physical monitors work
- USB input works after correct VID:PID passthrough and USB re-enumeration
- Unigine Superposition 1080p High:
  - score ~10429
  - avg FPS ~78.01
  - min ~64.08
  - max ~104.86
  - GPU utilization ~98%
  - max GPU temp ~72 C
- Practical conclusion: GPU passthrough performance is healthy

Virtual QEMU display is still present as recovery, but it interferes with Windows desktop topology. Plan: remove/disable it later after RTX path is considered fully reliable.

USB devices used:
- Razer Ornata V3 X: `1532:02a2`
- Logitech Unifying Receiver: `046d:c52b`

Important USB observation:
- VID:PID remains stable across physical USB ports and USB-switch toggles
- bus/device numbering changes, but VID:PID does not
- Logitech/Razer devices may need a real disconnect/reconnect after VM boot for Windows to enumerate HID interfaces cleanly
- Long-term Celephaïs should detect USB devices automatically and attach them to the active Realm; do not rely on manual `qm set` work
- Prefer `VID + PID + serial` where serial exists; VID:PID alone is not guaranteed unique per physical unit

## Work Realm — VM 201
Fresh reinstall completed on Ubuntu 26.04.1 Desktop.

Base config:
- VM ID: 201
- name: `work`
- OVMF / UEFI
- q35
- Secure Boot enabled
- EFI disk on local-lvm
- QEMU agent enabled
- CPU: host
- 1 socket, 6 cores
- RAM: 16 GB, ballooning off
- disk: 64 GB SCSI, VirtIO SCSI single, discard ON, IO thread ON
- network: VirtIO on vmbr0
- **NO LUKS / NO disk encryption** (critical for unattended Realm startup)

QEMU guest agent installed and running.

Current Local Seat passthrough:
- `hostpci0: 0000:01:00.0,pcie=1`
- `hostpci1: 0000:01:00.1,pcie=1`
- `usb0: host=1532:02a2`
- `usb1: host=046d:c52b`

Result:
- Ubuntu boots successfully on all 3 physical RTX-connected monitors
- keyboard/mouse work
- no LUKS boot prompt

## NVIDIA driver in Work Realm
Installed:
- `nvidia-driver-595-open`
- driver version observed: `595.99.02`

Secure Boot / MOK:
- MOK enrollment was completed successfully during reboot
- previous failure mode (module rejected by Secure Boot) is resolved

Verified:
- `nvidia-smi` works
- `lspci -nnk -s 01:00.0` shows `Kernel driver in use: nvidia`
- Vulkan sees `NVIDIA GeForce RTX 2060` as discrete GPU
- FFmpeg exposes NVIDIA NVENC encoders (`h264_nvenc`, `hevc_nvenc`; AV1 may appear in FFmpeg capabilities even though RTX 2060 hardware does not support AV1 encode)

## GNOME Remote Desktop / Remote Login
Ubuntu package:
- `gnome-remote-desktop` version 50.2
- control utility: `grdctl`

Chosen mode:
- **system Remote Login**, not user Desktop Sharing
- port: TCP 3389

Configured:
- system RDP enabled
- credentials configured with `grdctl --system rdp set-credentials`
- system daemon enabled and running

TLS:
- cert: `/var/lib/gnome-remote-desktop/rdp.crt`
- key: `/var/lib/gnome-remote-desktop/rdp.key`

Important permission fix:
The system daemon runs as user/group `gnome-remote-desktop`, so the TLS files must be readable by it.

Working ownership/permissions:
- owner/group: `gnome-remote-desktop:gnome-remote-desktop`
- `rdp.key`: mode 600
- `rdp.crt`: mode 644

After fixing ownership, service logs showed:
- `RDP server started`

and:
- `ss -ltnp` showed listener on `*:3389`

Remote test from ThinkPad T580:
- FreeRDP 3 client
- connection to Work Realm succeeded
- `/multimon` succeeded
- GNOME remote session usable

Observed FreeRDP warnings:
- some X11 keycode -> RDP scancode warnings
- Kerberos/KDC warnings when connecting by IP
- `/cert:ignore` warning in LAN test
- these did not prevent successful login
- `ERRINFO_LOGOFF_BY_USER` occurred when session was logged off; not a crash

## Current milestone
Confirmed working:
1. Work Realm local mode:
   - RTX 2060
   - 3 monitors
   - keyboard/mouse
   - NVIDIA driver
   - Vulkan
   - NVENC
2. Work Realm remote mode:
   - GNOME system Remote Login
   - RDP
   - multimon
   - access from T580
3. Windows Gaming Realm:
   - RTX passthrough
   - physical monitors
   - USB input
   - healthy 3D performance
4. RTX can move Windows <-> Linux without rebooting Proxmox, provided only one VM owns it at a time

## Next high-value test
Prove the same Work VM can start in a **remote/headless mode without RTX/USB attached**:
1. stop Work cleanly
2. remove/disable RTX + Local Seat USB from VM 201 config
3. start Work with virtual/headless display
4. connect from T580 via GNOME Remote Login
5. verify session usability
6. later automate Local Seat ownership switching in Celephaïs

## Architecture notes
- The Proxmox host must remain independently manageable; a Realm must never be required to repair the infrastructure that launches it.
- Recommended management/recovery paths:
  - Proxmox Web UI / SSH
  - Tailscale on host for emergency remote access
  - old always-on server as independent jump host
  - future PiKVM / smart power for hard host failures
- Avoid putting experimental app stacks directly on the hypervisor; keep control-plane services in an LXC or separate host.
- Local Seat scheduler must enforce exclusivity: two running VMs must never be allowed to claim the same passthrough GPU simultaneously.

## Security / cleanup notes
- Do not store or transfer passwords in this handoff document
- `/cert:ignore` is acceptable only as a temporary LAN test
- later establish proper certificate trust
- old experimental LXC credentials/config should be cleaned up


## Prototype control-plane decision (2026-10-08)

Recommended MVP remote-start flow:

```text
Gate client
  -> establishes private Celephaïs/Tailscale connectivity
  -> calls Celephaïs Core API
  -> Core authenticates/authorizes requested Realm
  -> Core calls Proxmox API for VM 201
  -> waits for VM to become running / guest agent ready
  -> resolves Realm connection endpoint
  -> Gate launches RDP connection
```

Key architecture decisions:
- Do **not** put Proxmox root credentials or a privileged Proxmox API token in the portable Gate executable.
- Gate should talk to a small Celephaïs Core/control-plane service; Core owns the Proxmox API credential.
- Prefer Proxmox HTTP API over executing `qm start` remotely over SSH.
- Tailscale on the Proxmox host is useful as an emergency management path, but normal Gate traffic does not need to target the hypervisor directly.
- Core can run in a small service LXC on the Proxmox host or on the independent always-on server. Keeping it off the hypervisor itself is cleaner.
- For the first prototype, Realm addressing can be resolved after boot through QEMU Guest Agent / Proxmox API. Do not depend on a manually remembered DHCP IPv4 address.
- Longer term, either:
  1. run Tailscale inside each Realm and connect directly by stable tailnet hostname/address, or
  2. expose the Realm LAN subnet to the tailnet through a subnet router and keep Tailscale out of the Realm.
- A Realm-local Tailscale node gives the cleanest direct RDP path but becomes available only after the Realm boots, so Gate/Core must poll readiness before connecting.
- A subnet-router design keeps Realm images cleaner and lets Gate reach the Realm's LAN IP over the tailnet.
- Prototype should begin with a single hard-coded Realm mapping (`work` -> VMID 201), then generalize to a Realm registry.

Suggested MVP states:
`OFF -> STARTING -> BOOTING -> READY -> CONNECTING -> ONLINE`
and on shutdown:
`ONLINE -> STOPPING -> OFF`

Gate UX prototype:
- Work Linux
- Personal Linux
- Windows

Selecting Work Linux should eventually be one action:
1. connect/authenticate to private control plane,
2. request `work`,
3. Core starts VM 201 if stopped,
4. wait until RDP is ready,
5. launch/attach RDP automatically.

Do not treat Tailscale identity/state as an ordinary config blob to copy into an EXE. Machine/node identity is sensitive credential material. For a portable client prototype, prefer a supported Tailscale client/service or an explicitly designed embedded/userspace integration with separate protected state.


## Remote/headless Work Realm test — PASS
Confirmed that Work VM 201 can run without the Local Seat devices attached and remains usable remotely over GNOME system Remote Login / RDP from the T580.

This proves the same Realm supports both:
- Local mode: RTX 2060 + 3 physical monitors + USB keyboard/mouse
- Remote mode: no RTX/Local Seat USB required; access over RDP

This test is complete and should not be repeated unless later changes regress it.


## Exact Local Seat device discovery and switching commands

### Discover RTX PCI functions on Proxmox
Use:

```bash
lspci -nn | grep -i nvidia
```

Known current RTX 2060 functions:

```text
01:00.0 NVIDIA GPU             [10de:1f08]
01:00.1 NVIDIA HDMI/DP audio   [10de:10f9]
01:00.2 NVIDIA USB/VirtualLink [10de:1ada]
01:00.3 NVIDIA UCSI            [10de:1adb]
```

The currently used Local Seat GPU passthrough is only:

```text
0000:01:00.0
0000:01:00.1
```

Check ownership/driver binding with:

```bash
lspci -nnk -s 01:00.0
lspci -nnk -s 01:00.1
```

On the Proxmox host, the GPU should be available to VFIO when not owned by a running Realm.

### Discover USB Local Seat devices
Use:

```bash
lsusb
```

Known current devices:

```text
Razer Ornata V3 X
VID:PID = 1532:02a2

Logitech Unifying Receiver
VID:PID = 046d:c52b
```

A Razer Basilisk V3 observed during testing had:

```text
1532:0099
```

This was only a temporary test mouse and is not the normal Local Seat mapping.

Important discovery behavior:
- `Bus XXX Device YYY` values may change after reconnects.
- device ordering in `lsusb` may change.
- VID:PID remained stable across tested physical USB ports and USB-switch toggles.
- therefore do NOT persist Linux bus/device numbers as device identity.
- for long-term device identity prefer `VID + PID + serial` when serial is available.
- if no serial exists, additional topology/port metadata may be required to distinguish identical devices.

### Inspect which Local Seat devices a VM currently owns

```bash
qm config <VMID> | grep -E '^(hostpci|usb)'
```

Examples:

```bash
qm config 100 | grep -E '^(hostpci|usb)'
qm config 201 | grep -E '^(hostpci|usb)'
```

### Attach Local Seat to Work Linux VM 201

VM must be stopped before changing PCI passthrough ownership.

```bash
qm set 201 -hostpci0 0000:01:00.0,pcie=1
qm set 201 -hostpci1 0000:01:00.1,pcie=1
qm set 201 -usb0 host=1532:02a2
qm set 201 -usb1 host=046d:c52b
```

Verify:

```bash
qm config 201 | grep -E '^(hostpci|usb)'
```

Expected logical mapping:

```text
hostpci0: 0000:01:00.0,pcie=1
hostpci1: 0000:01:00.1,pcie=1
usb0: host=1532:02a2
usb1: host=046d:c52b
```

Then:

```bash
qm start 201
```

### Detach Local Seat from Work Linux VM 201

First perform a clean guest shutdown and verify:

```bash
qm status 201
```

Expected:

```text
status: stopped
```

Then:

```bash
qm set 201 -delete hostpci0
qm set 201 -delete hostpci1
qm set 201 -delete usb0
qm set 201 -delete usb1
```

### Attach Local Seat to Windows Gaming VM 100

VM must be stopped before editing ownership:

```bash
qm set 100 -hostpci0 0000:01:00.0,pcie=1
qm set 100 -hostpci1 0000:01:00.1,pcie=1
qm set 100 -usb0 host=1532:02a2
qm set 100 -usb1 host=046d:c52b
```

Verify:

```bash
qm config 100 | grep -E '^(hostpci|usb)'
```

Then:

```bash
qm start 100
```

### Detach Local Seat from Windows Gaming VM 100

After clean Windows shutdown:

```bash
qm status 100
```

Expected:

```text
status: stopped
```

Then:

```bash
qm set 100 -delete hostpci0
qm set 100 -delete hostpci1
qm set 100 -delete usb0
qm set 100 -delete usb1
```

### Manual Windows <-> Work switching sequence

Windows -> Work:

```text
1. clean shutdown VM 100
2. verify VM 100 is stopped
3. remove Local Seat mappings from VM 100 if using dynamic config
4. attach RTX + USB mappings to VM 201
5. start VM 201
6. verify guest boot / input / display
```

Work -> Windows:

```text
1. clean shutdown VM 201
2. verify VM 201 is stopped
3. remove Local Seat mappings from VM 201 if using dynamic config
4. attach RTX + USB mappings to VM 100
5. start VM 100
6. verify guest boot / input / display
```

Note:
Both VM configurations can technically contain references to the same physical PCI devices while stopped, but Celephaïs Core must enforce exclusive ownership and never allow VM 100 and VM 201 to run simultaneously while both claim the RTX.

For a robust orchestrator, explicitly model Local Seat ownership rather than relying on Proxmox failure behavior.

### USB hotplug observations

During Windows testing:
- devices were initially configured with correct VID:PID but input did not immediately work;
- toggling the physical USB switch away from and back to the PC caused a real USB disconnect/reconnect;
- Windows re-enumerated the composite/HID devices;
- mouse and keyboard then worked normally;
- repeated USB-switch toggles subsequently worked.

This means Celephaïs should be prepared to handle device arrival/removal events and may need to perform an explicit USB detach/attach when a known device is present but not active in the Realm.

Long-term desired behavior:

```text
USB device appears
-> detect VID/PID/serial
-> identify known device or prompt user
-> classify as Local Seat / host / Realm-specific / ignored
-> attach to current Local Seat Realm automatically
-> remember policy
```

Do not build the production implementation around manually editing `qm` configuration for every connect event if Proxmox/QEMU hotplug can provide a cleaner runtime attach/detach path. The commands above are the verified manual prototype mechanism.

## Local Seat switcher — `bin/celephais-seat` (2026-10-09)

Automates the manual `qm set` sequences above. Copy to the Proxmox host, run there:

```bash
celephais-seat status
celephais-seat switch work
celephais-seat switch windows
```

Rules it enforces:
- only a Realm that *holds* the seat is shut down; a headless Realm keeps running
- seat keys are detached from the old owner before being attached to the new one
- switching to the Realm that already owns the seat does nothing

Hardware IDs are the three arrays at the top of the script. `bin/test_seat.sh` checks
the logic against a fake `qm`, so it needs no Proxmox.

**Verified on the real Proxmox host 2026-10-09 — PASS.** Work <-> Windows seat switching
works end to end. Do not repeat the manual `qm set` sequence by hand; use the script.

## Celephais Core — `bin/celephais_core.py` (2026-10-09)

The control plane from the MVP flow above. Stdlib Python only, no dependencies.
Runs on the service LXC or the always-on server, **not** on the hypervisor.
It is the only thing that holds the Proxmox credential.

### Proxmox API token — narrow scope, not root

```bash
pveum user add gate@pve
pveum role add CelephaisCore -privs "VM.Audit VM.PowerMgmt VM.GuestAgent.Audit"
pveum aclmod /vms/201 -user gate@pve -role CelephaisCore
pveum aclmod /vms/100 -user gate@pve -role CelephaisCore

# separated token: it carries its own ACL, it does not inherit the user's
pveum user token add gate@pve core --privsep 1
pveum aclmod /vms/201 -token 'gate@pve!core' -role CelephaisCore
pveum aclmod /vms/100 -token 'gate@pve!core' -role CelephaisCore
```

Use `--privsep 1`, which is what Proxmox recommends when a token is meant to
hold its own limited rights. With `--privsep 0` the token simply inherits
everything the user has, so a later widening of `gate@pve` silently widens the
token too. With `--privsep 1` the effective rights are the *intersection* of the
user ACL and the token ACL, so both sets of `aclmod` lines above are required.

`VM.Audit` reads status, `VM.PowerMgmt` starts and shuts down, and
`VM.GuestAgent.Audit` lets Core ask the QEMU guest agent for the Realm IP.
`VM.Monitor` is *not* enough: on PVE 9 the `/agent/` endpoints answer 403 with
`Permission check failed (/vms/201, VM.GuestAgent.Audit|VM.GuestAgent.Unrestricted)`,
which Core shows as a Realm stuck in `BOOTING` forever. Copy the printed token value
once; Proxmox does not show it again.

Check the result with:

```bash
pveum user token permissions gate@pve core
```

### Run it

Secrets go in `.env` at the repo root, which is gitignored. Copy `.env.example`
and fill it in. Core reads that file at startup; a real environment variable
still wins, so systemd can override it.

```bash
cp .env.example .env
$EDITOR .env
python3 bin/celephais_core.py
```

Proxmox was found at `192.168.2.103:8006`, node `pve` (read from its TLS cert).

To use the same values in a shell, for a `curl` test:

```bash
set -a; . ./.env; set +a
curl -k -i -H "Authorization: $PVE_TOKEN" https://$PVE_HOST/api2/json/version
```

A Proxmox 401 has an empty body, so `curl -s` with a missing token prints
nothing at all. Use `-i` when debugging auth.

### API

```text
GET  /realms              -> [ {realm, vmid, state, ip}, ... ]
GET  /realms/<name>       -> {realm, vmid, state, ip}
POST /realms/<name>/start -> 202 + current state
POST /realms/<name>/stop  -> 202 + current state
```

All calls need `Authorization: Bearer $CORE_TOKEN`.

States are `OFF -> BOOTING -> READY`, and `STOPPING` as the reply to a stop.
`READY` means the guest agent reported an IPv4 address *and* TCP 3389 accepts a
connection, so the Gate can connect at once. `CONNECTING` and `ONLINE` belong to
the Gate, not to Core. `STOPPING` is returned only by `POST /stop`; polling after
that reads `BOOTING` briefly, then `OFF`.

**Verified against the real host 2026-10-09 — PASS.** Full cycle on VM 201:
`OFF -> BOOTING -> READY (ip 192.168.2.114) -> STOPPING -> OFF`.

Realm registry is the `REALMS` dict at the top of the file, same as the switcher.

`bin/test_core.py` checks state resolution, token auth and routing against a fake
Proxmox, so it needs no Proxmox. Not yet run against the real host.

### Not built yet
Realm-local Tailscale or subnet router · calling the Local Seat switcher from
Core · any persistent state. Core is stateless; it reads the truth from Proxmox
on every request.


## Celephais Gate — `bin/celephais-gate` (2026-10-09)

The client. One command opens a Realm:

```bash
bin/celephais-gate work          # start if needed, wait for READY, launch RDP
bin/celephais-gate --status      # list realms
bin/celephais-gate work --stop   # shut it down
bin/celephais-gate work --no-connect   # start and wait, print the IP, no RDP
```

Anything it does not recognise is passed straight through to FreeRDP, so
`bin/celephais-gate work /f /scale:140` works.

It talks only to Core. It holds no Proxmox credential, and no guest password
either: FreeRDP asks for that. Reads `CORE_URL`, `CORE_TOKEN` and `RDP_USER`
from the same `.env`.

Client used: `xfreerdp3` (FreeRDP 3.32.1), with `/multimon /dynamic-resolution
/clipboard /sound`. `/cert:ignore` stays only while this is a LAN prototype.

### The whole chain now
```text
celephais-gate work
  -> POST /realms/work/start      (Core)
  -> Core -> Proxmox API          (VM 201 start)
  -> poll until READY             (guest agent IP + TCP 3389 answers)
  -> xfreerdp3 to the Realm IP
```

## Target shape, restated by the owner (2026-10-09)

The end product is the USB key flow from `vision.md`, not a terminal command:

```text
insert USB on any machine
  -> launch one portable executable
  -> fullscreen layer over the host OS, across all monitors
  -> one password, which decrypts the identity material
  -> Tailscale connection to the Proxmox host
  -> environment selector
  -> the Realm, fullscreen, feeling like a different computer
```

So **Core lives on the Proxmox machine and is always on.** The Gate is the only
portable part. This overrides the earlier note that preferred Core on a separate
host: Core is ~150 lines of stdlib Python with no dependencies, so it is run on
the host as an unprivileged `celephais` user bound to the Tailscale address,
rather than in its own LXC. If Core ever grows a real dependency stack, move it.

### `deploy/install-core.sh` — run on the Proxmox host, as root

```bash
./install-core.sh 'PVEAPIToken=gate@pve!core=<uuid>' '<core-token>'
```

It creates the `celephais` system user, installs `/opt/celephais/celephais_core.py`,
writes `/etc/celephais/core.env` (0640, root:celephais), and enables
`celephais-core.service`.

Core then listens **only** on the host's Tailscale IPv4, so the LAN cannot see it
and the Gate reaches it from anywhere on the tailnet. It talks to Proxmox over
`127.0.0.1:8006`, so the API token never crosses a network.

Tailscale must already be up on the Proxmox host; the script refuses otherwise.

### Still missing for the USB flow
1. **Vault** — one password decrypting `CORE_TOKEN` and the Tailscale identity.
   Do not copy tailnet node state around as a plain config blob.
2. **Overlay** — fullscreen layer across all monitors, with the environment
   selector. FreeRDP already covers the Realm itself with `/f /multimon`; what is
   missing is the selector shown *before* a Realm is picked.
3. **Portability** — one executable per platform, no install, running from the
   USB; plus the trusted-host helper that launches it on insert.

## The vault — `celephais.vault` + `bin/celephais_vault.py` (2026-10-09)

The Gate binary is public. Only the vault beside it is secret. It holds
`CORE_URL`, `CORE_TOKEN`, `RDP_USER`, and later the Tailscale identity.
It never holds the Proxmox token: that belongs to Core alone, and `init --from-env`
strips `PVE_*` on the way in.

- key derivation: `hashlib.scrypt` from the standard library, N=32768, r=8, p=1
- cipher: AES-256-GCM (`cryptography`, the one dependency; wheels exist for Windows)
- the KDF parameters live in the file **and** are authenticated as additional data,
  so N can be raised later and nobody can weaken it by editing the file
- a fresh salt and nonce per save; writes go to a temp file and are renamed, so a
  crash never leaves half a vault

```bash
python3 bin/celephais_vault.py init --from-env   # one password, asked twice
python3 bin/celephais_vault.py set CORE_URL http://100.x.x.x:8080
python3 bin/celephais_vault.py list              # key names only, never values
```

The Gate opens the vault if it exists and falls back to `.env` otherwise, which
keeps development easy. `.env`, `celephais.vault` and `*.vault.tmp` are gitignored.

`bin/test_vault.py` checks the round trip, three wrong passwords, a tampered
ciphertext, a tampered salt, a downgraded KDF, and that two saves of the same
data differ.

### Gate is now Windows-aware
It looks for an RDP client in this order: `wfreerdp.exe`, `sdl-freerdp3`,
`xfreerdp3`, `sdl-freerdp`, `xfreerdp`, and says so plainly if none is present.
It connects with `/f /multimon`, so the Realm covers every monitor.

## One-file Gate — `build/make-gate.sh` / `build/make-gate.ps1` (2026-10-09)

PyInstaller `--onefile`, built in a throwaway venv so nothing is installed
system-wide. PyInstaller cannot cross-compile, so the `.exe` has to be built on
a Windows machine; the two scripts take the same arguments.

```bash
./build/make-gate.sh            # -> build/dist/celephais-gate   (14 MB)
.\build\make-gate.ps1           # -> build\dist\Gate.exe
```

The USB layout is then just two files:

```text
Gate.exe              public, no secrets in it
celephais.vault       the only secret
```

When frozen, the Gate resolves the vault next to `sys.executable`, not next to
the unpacked script, which is what makes the USB layout work at all.

**Verified 2026-10-09:** the Linux build was copied with a vault into an empty
directory, with no repo and no `.env` present, and listed the live Realms after
one password. With the vault removed it refuses with a plain message. `strings`
on the binary does not contain the token.

### Remaining for the full USB flow
1. **Overlay** — the fullscreen layer and environment selector shown *before* a
   Realm is picked. FreeRDP already covers the Realm itself with `/f /multimon`.
2. **Tailscale from the Gate** — right now the client machine must already be on
   the tailnet. `vision.md` wants the USB to carry its own identity.
3. **Launch on insert** — the trusted-host helper, per platform, no autorun hacks.

## Power and Local Seat are separate — the model (2026-10-09)

Stated by the owner and now enforced in code. Two independent facts:

```text
Realm power:         OFF | BOOTING | READY | STOPPING | ERROR
Local Seat owner:    exactly one Realm, or nobody
```

Several Realms may run at once. Only one may hold the RTX 2060, the three
physical monitors and the USB keyboard/mouse. **Owning the seat is not required
for a Linux Realm to run**, so a remote start must never claim it.

The hazard was real: Proxmox keeps `hostpci0` in the VM config, so a Realm shut
down while holding the seat still holds it in config. A remote start would then
quietly take over the physical desktop. Every start therefore releases the seat
first, and refuses to start at all if the release fails.

### API

```text
GET  /realms              -> [ {realm, vmid, power, seat, ip}, ... ]
GET  /realms/<name>
GET  /seat                -> {"owner": "<realm>"} or {"owner": null}
POST /realms/<name>/start -> headless, never with the seat
POST /realms/<name>/stop
POST /realms/<name>/seat  -> explicitly give this Realm the Local Seat
```

Changing passthrough is restricted to `root@pam` by Proxmox, which the token
cannot do. Reading it is allowed with `VM.Audit`, so Core *detects* the seat over
the API, and *changes* it by calling the hardware-tested `bin/celephais-seat`
through one narrow sudo rule:

```text
celephais ALL=(root) NOPASSWD: /usr/local/bin/celephais-seat
```

`install-core.sh` writes that rule and installs the switcher. One implementation
of the seat rules, not two.

### Gate
```bash
celephais-gate work           # remote: headless start, wait for READY, open RDP
celephais-gate work --local   # explicit: hand this Realm the physical desktop
celephais-gate --status       # power and seat for every Realm
```

### Verified against the real host 2026-10-09
- `work` READY at 192.168.2.114 with `seat: false`, while `windows` was OFF and
  still held the seat keys in its config — the two facts read independently.
- `POST /realms/windows/start` for that seat-holding Realm was **refused** with
  409 and VM 100 stayed OFF. The monitors were never touched.

### Still to test on hardware
Two Linux Realms at once: one holding the seat on the RTX, the other running
headless and reachable over RDP. Needs the Personal Realm, VM 202, which does
not exist yet. Adding it is one line in `REALMS` in both Core and the switcher.

## Hardware scenarios — `bin/test_scenarios.py` (2026-10-09)

Four scenarios that prove power and seat stay independent. This is not a unit
test: it starts, stops and restarts real VMs and moves the RTX between them, so
the physical monitors switch while it runs. `--yes` is required.

```bash
python3 bin/test_scenarios.py --yes            # all four
python3 bin/test_scenarios.py --yes --only 1,2
```

| # | Start state | Action | Must happen |
|---|---|---|---|
| 1 | work OFF, no seat | remote start | READY, seat stays false, has an IP |
| 2 | work OFF, **holding** seat | remote start | seat dropped first, then READY remotely |
| 3 | work READY headless, no seat | `--local` | work takes the RTX and USB |
| 4 | windows READY with seat, work OFF | remote start work | both keep running, windows keeps the seat |

Scenarios 2, 3 and 4 change passthrough, so **Core must be installed on the
Proxmox host** first (`deploy/install-core.sh`). Run from a laptop against a
laptop-hosted Core, they fail with `409 local seat: sudo: interactive
authentication is required` — which is itself the correct refusal.

**ALL FOUR PASS on the real host, 2026-10-09**, with Core installed on the
Proxmox machine:

1. work OFF, no seat -> remote start -> READY at 192.168.2.114, seat still false.
2. work OFF but holding the seat in config -> remote start -> seat dropped
   first, then READY remotely. This is the case that would otherwise have taken
   over the physical monitors from a cafe.
3. work READY headless -> take the seat -> controlled restart, READY with the
   RTX, `/seat` reports `work`.
4. windows takes the seat and keeps it while work starts headless. Both running,
   only one on the RTX. **The concurrent-Realm requirement is proven.**

### Five setup bugs this run exposed, all in the deployment
Each produced a clean 409 refusal rather than a half-done seat change.

| Symptom | Cause |
|---|---|
| unit not found | Proxmox had no `sudo` package; the installer died at `visudo` |
| service never started | the unit asked the `celephais` user to run `tailscale ip`, which only root may do |
| 409 "no new privileges" | `NoNewPrivileges=yes` blocks the very sudo rule the design depends on |
| the fix did not apply | `systemctl enable --now` leaves a running service on the old unit file |
| 409 read-only filesystem | `ProtectSystem=full` makes `/etc` read-only, and Proxmox keeps VM config in `/etc/pve` |

The unit now keeps `PrivateTmp`, `ProtectHome` and `ProtectSystem=true`, and the
installer restarts rather than merely enabling.

### Scenario 3 is a restart, not a hot-plug
PCI passthrough cannot be hot-added to a running VM. Taking the Local Seat
therefore means a controlled stop, reconfigure and start. The Gate names every
Realm that will be restarted and asks for a typed `yes` first; `--yes` skips it.

### New Core endpoint
`POST /realms/<name>/release` strips the seat from a **stopped** Realm without
powering anything. It is the inverse of `POST /realms/<name>/seat` and is what
makes scenario 1's setup deterministic.

## Gate carries its own Tailscale — `bin/celephais_net.py` (2026-10-09)

A clean Windows or Linux host needs nothing installed. Two files are enough:

```text
Gate.exe / celephais-gate     public, now ~52 MB: tailscaled travels inside it
celephais.vault               the only secret
```

### How
The Gate runs the bundled `tailscaled` with:

```text
--tun=userspace-networking     no TUN device, no driver, no admin rights
--state=mem:                   nothing written to disk; registers as EPHEMERAL
--socket=<temp>/tailscaled.sock
--socks5-server=127.0.0.1:<port>
--outbound-http-proxy-listen=127.0.0.1:<port>
```

Core is reached through the HTTP proxy; FreeRDP gets
`/proxy:socks5://127.0.0.1:<port>`. When the Gate exits, `tailscaled` is
terminated and the ephemeral node disappears by itself. No service is
installed, no machine-wide configuration is written.

### The key
`TS_AUTHKEY` lives in the vault, like every other secret, and must be a
**reusable, ephemeral** auth key from the Tailscale admin console. With
`--state=mem:` there is no saved identity to restore, so each launch is a fresh
ephemeral node and no interactive login ever happens.

On Linux and macOS the key reaches `tailscale up` through a FIFO, so it is never
a file and never appears in the process list. Windows has no FIFO, so there it
goes on the command line of a process the same user already owns; the code says
so at the point it happens.

### Two kinds of credential, told apart by their prefix

| Vault setting | What it is | Expires |
|---|---|---|
| `TS_AUTHKEY` | a plain auth key, `tskey-auth-...` | after at most 90 days |
| `TS_AUTH_CLIENT_SECRET` | an OAuth client secret, `tskey-client-...` | **never** |

The OAuth secret wins when both are present. **Verified on 2026-10-09:** the
clean-container flow passed on the OAuth path, so nothing needs rotating by hand.

With an OAuth client secret the Gate mints a fresh key on every launch, so
nothing has to be rotated by hand. Tailscale requires such keys to be tagged,
so `TS_TAGS` must be set too, and the Gate refuses to start without it rather
than failing later with an unclear message.

The Gate also carries a CA bundle. A minimal host can have no certificate store
at all, and the OAuth path calls `api.tailscale.com` over HTTPS; without it the
bare container failed with `x509: certificate signed by unknown authority`. The
bundle is handed to the Go binaries through `SSL_CERT_FILE`.

Setting it up, once:

1. In the ACL, allow the tag and let yourself own it:
   ```json
   "tagOwners": { "tag:celephais-gate": ["autogroup:admin"] }
   ```
2. https://login.tailscale.com/admin/settings/oauth -> **Generate OAuth client**,
   scope **auth_keys** with write access, tag `tag:celephais-gate`.
3. Put the secret in the vault:
   ```bash
   python3 bin/celephais_vault.py set TS_AUTH_CLIENT_SECRET tskey-client-...
   python3 bin/celephais_vault.py set TS_TAGS tag:celephais-gate
   ```

The node stays ephemeral either way, because that comes from `--state=mem:`,
not from the key.

Without `TS_AUTHKEY` the Gate simply uses the host's own networking, which is
how development on an already-connected laptop stays easy. `--no-tailnet`
forces that.

### Acceptance test — `bin/test_clean_host.sh`
Runs the built binary and a vault inside a bare `debian:stable-slim` container
with no tailscale, no tailscaled, no python, no TUN and no added privileges.

**The honest part:** a container still reaches the tailnet through the host's
routes, so "Core answered" would prove nothing on its own. The test therefore
proves the mechanism: a **new ephemeral node appears in the tailnet** while the
container runs, which can only come from the Gate's own `tailscaled`. For a
fully independent check, run the binary on a machine that never had Tailscale.

**PASS on 2026-10-09**, both the status run and the full flow:

```text
--- 1. the container is bare ---
nothing installed
--- 2. the node must not exist in the tailnet yet ---
ok: celephais-gate-test is absent
--- 3. running the Gate in the clean container ---
Vault password:   tailnet: up
  work: OFF
  work: BOOTING
  work: READY
192.168.2.114
PASS: the container joined the tailnet on its own as celephais-gate-test
```

One password, then the Gate brought up its own tailnet, reached Core, started
VM 201 headless and waited until RDP was ready, from a container with no
Tailscale, no Python and no privileges. The ephemeral node disappeared when the
container exited.

Only the last step, handing over to FreeRDP, was skipped: `--no-connect` is used
because a container has no display. The proxy argument it would pass is
`/proxy:socks5://127.0.0.1:<port>`, which FreeRDP 3.32.1 documents under
`/proxy:`. Run `celephais-gate work` from a desktop to see the window itself.

## The overlay — `bin/celephais_ui.py` (2026-10-09)

The black layer from `vision.md`, over whatever the host OS is already running.

```text
launch -> black layer on every monitor -> one password -> environment selector
```

Plain tkinter, which ships with Python, so the one-file build gains no
dependency. The window is borderless (`overrideredirect`), topmost, and sized
to the **virtual desktop** rather than to one screen, which is what spans
several monitors on both X11 and Windows. Measured here: 5120x1440 across two.

Slow work — the tailnet coming up, a Realm booting — runs in a worker thread
and reports back through `after`, so the layer never freezes. Esc leaves.

Each Realm row offers the two operations the model keeps separate:

| Button | What it does |
|---|---|
| **open here** | remote: headless start, wait for READY, RDP on this machine |
| **use the monitors** | the Local Seat: that Realm takes the RTX and the physical screens |

A Realm holding the seat is marked `on the monitors`. While RDP is running the
overlay hides itself and drops topmost, so the session is not covered, and comes
back when the session ends.

### How it starts
`--ui` forces it. The frozen binary with **no arguments at all** also shows it,
which is the double-click-from-the-USB case. Everything else stays the command
line it was.

### `bin/test_ui.py`
Builds both screens with fake hooks in a **hidden** window, so it is safe to run
on a desktop in use. It checks that the layer is frameless and spans the whole
desktop, that the password is masked, that a wrong password returns to the same
screen carrying the reason, and that the Realm list shows power and seat.

It found a real bug on the first run: Python clears the name bound by
`except ... as exc` at the end of the block, so the callback that reported the
failure raised `NameError` instead, and every error would have been silent.
