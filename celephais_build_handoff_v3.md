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

## Windows build, prepared 2026-10-09

PyInstaller cannot cross-compile, so `Gate.exe` has to be built on a Windows
machine with `build\make-gate.ps1`. Three real problems were found and fixed in
the code first, so the first Windows run is not spent on them.

### 1. A clean Windows host has no FreeRDP *installed*
FreeRDP is still the client of choice and is used wherever it is present:
multimon, dynamic resolution and sound all work there. But a Windows machine
that has never been set up has only `mstsc.exe`, and `mstsc` has no proxy
setting, while in userspace networking the Realm is reachable only through the
SOCKS proxy.

**Fix: the Gate carries the connection itself.** `celephais_net.forward()`
listens on localhost and splices every connection to the Realm through SOCKS5,
so *any* RDP client works, with no proxy support of its own. That also makes
the Linux path simpler, since `/proxy:` is no longer needed at all.

Client order on Windows: `sdl-freerdp3.exe`, `wfreerdp3.exe`, `wfreerdp.exe`,
`sdl-freerdp.exe`, then `mstsc.exe` as the fallback. Elsewhere: the FreeRDP
builds, as before.

**Verified against the real thing:** through the real tailnet, an X.224
connection request sent at `127.0.0.1:<port>` came back as a proper RDP reply
from VM 201 — `030000130ed00000`.

`bin/test_forward.py` covers the handshake with a fake SOCKS server and no
network: a large payload round trip, the hostname reaching the proxy rather
than an IP, a reply that dribbles in one byte at a time, a refusal, and a dead
proxy.

### 2. The bundled binaries are `.exe` on Windows
`bundled()` looked for a file called exactly `tailscaled`, which never exists
there. It now tries both names.

### 3. tailscaled has no unix socket on Windows
It listens on a named pipe, so waiting for a socket *file* to appear would have
hung until the timeout. The wait now asks the CLI whether the daemon answers,
which works on both.

### Left to find out on the machine itself
Whether `tailscaled --tun=userspace-networking` runs on Windows **without
administrator rights**. Userspace mode needs no TUN driver, which is the usual
reason for admin, but this is unverified. If it does need admin, the fallback
is Tailscale installed normally plus `--no-tailnet`, and the Gate says so
clearly rather than hanging.

### Build it
```powershell
# once: install Python 3 from python.org (tick "Add python.exe to PATH")
# once: install Tailscale, so tailscaled.exe exists to bundle
git clone <this repo>; cd Cloud_infrastructure
.\build\make-gate.ps1          # -> build\dist\Gate.exe
```

Then copy `Gate.exe` and a `celephais.vault` into one folder and double-click
it: no arguments means the overlay.

## The subprocesses are silent — `bin/celephais_net.py` (2026-10-13)

Max ran `Gate.exe`, the overlay covered every monitor, and Tailscale's own
command line and output were legible on top of it. An auth key is on that
command line. This was three bugs wearing one coat.

### 1. The exe had a console and the overlay did not cover it
`make-gate.ps1` built a single console exe, on the reasoning that the overlay
is topmost and would hide it. It does not: a console the Gate still owns keeps
drawing, and what tailscaled writes into it shows through. The Windows build
is now two executables out of the same script:

```text
Gate.exe              windowed (--noconsole). The double-click. No console
                      exists, so nothing can be written to one.
celephais-gate.exe    console. The command line, where output is the point.
```

`build/make-gate-wine.sh` builds the same pair. The Linux build is unchanged:
there is no console to inherit there.

### 2. The children were launched as if a terminal were watching
Every `tailscaled` and `tailscale` now goes through `_quiet()`: `shell=False`
so no argument is ever handed to a command interpreter, `stdin=DEVNULL` so
nothing waits for an answer nobody will type, output on a pipe rather than
inherited, and on Windows `CREATE_NO_WINDOW` plus `STARTUPINFO`/`SW_HIDE` so
no console window is created either — a windowed build without those flags
flashes one console per child across the screen, which is the same bug seen
from the other side.

`tailscaled`'s stderr is a pipe now, so something has to read it: a pipe
nobody drains holds about 64K and then the daemon blocks on its own logging.
`_Tail` reads it in a thread, redacts it and keeps the last ten lines, saying
nothing until the daemon dies — then those lines are its explanation.

### 3. The error messages were the subprocess's, not the Gate's
`NetError` carried `result.stderr` verbatim, and the overlay rendered it.
Tailscale, like most Go programs, quotes the command line back when it is
unhappy, so the key came with it. Now:

- `redact()` takes out `tskey-...` and anything after `--auth-key=` /
  `--client-secret=`. It runs on everything coming back from either binary.
- `_explain()` picks one sentence of the Gate's own, chosen by what Tailscale
  said but never quoting it — "the Tailscale key was refused. It may have
  expired or been revoked." and so on, with a plain fallback.
- `_note()` is the one way any of that output reaches a terminal, and only
  after something has failed. The overlay sets `SHOW_DETAIL = False`, because
  behind a fullscreen screen there is no terminal to read it in.
- `subprocess.TimeoutExpired` from `tailscale up` is caught and replaced:
  its own message prints the whole command it was given, key and all.
- `celephais_ui.reason()` is the last stop before any failure becomes text on
  the screen, and it redacts too. The hooks reach down to Core, Tailscale and
  an RDP client; one of them quoting a command line back should not be able to
  put a key on the monitors no matter which one it is.

Covered by `bin/test_forward.py`: the redaction, the sentences, the launch
flags, and a whole `tailnet()` run against a fake `tailscale up` that fails by
printing its own command line — the key must not be anywhere in what comes
out. `bin/test_ui.py` checks the overlay's own last stop.

## The seat carried a gamepad and left the keyboard — `bin/celephais_core.py` (2026-10-13)

Max: "don't forget to move not only GPU with monitors but host=1532:02a2 and
host=046d:c52b too." Core was written to do exactly that, and against the live
host it would not have.

`GET /devices` on pve answers with two devices: the Intel wireless card,
refused, and an Xbox controller, approved for the seat. The Razer keyboard and
the Logitech receiver are not in that list at all, because right now they are
enumerated on `acnit1` — they hang off a USB switch and the switch points at
the desktop. `seat_usb()` built its answer by walking the host's own USB tree,
so the two defaults were filtered out for not being there, while the one
approved device that happened to be plugged in came through on its own. Core
would have handed `celephais-seat` a single argument, `045e:028e`, and the
switcher falls back to its own `SEAT_USB` only when it is given nothing at all.
The Realm would have come up holding the RTX, both monitors and a gamepad, with
no keyboard or mouse to tell it anything.

The switch pointing the other way is not an edge case. It is the normal state
at the moment the seat is asked for, because that is where Max is sitting when
he asks. So the two defaults now travel whether or not Core can see them:

- `SEAT_DEFAULT_USB` goes in first and unconditionally, minus anything refused
  by name;
- an approved extra still has to be plugged in to come along — nothing is lost
  by waiting for a gamepad to arrive;
- a refusal is matched on the VID:PID that would go into the VM config, not on
  the registry key, because the key carries a serial number when the maker
  wrote one and a default never does.

`host=vid:pid` in a VM config is a standing claim and not a snapshot: QEMU
attaches the device when it appears, which is the mechanism behind the USB
switch toggle recorded under "USB hotplug observations" above. Writing the
claim for a device nobody can see costs nothing, and it is what makes the
toggle work.

`bin/test_core.py` covers it. Nothing plugged in and nothing decided: the seat
claims both defaults. The mouse refused by name: it claims the keyboard alone.
A refusal still beats the default.

**Not live until it is deployed.** Core on pve runs
`/opt/celephais/celephais_core.py`; the file has to be replaced and
`celephais-core.service` restarted.

## A permanent Gate on the Omarchy desktop — `deploy/install-gate.sh` (2026-10-13)

The Gate was built for a USB stick carried to a strange machine. Max wants it
as an ordinary application on `acnit1`, the Omarchy desktop, asked for the
vault password once and never again, so every Realm can be started from there.
`deploy/install-gate.sh --no-password` was already most of that. Four things
were in the way.

### 1. The built Linux Gate has no tkinter in it
`build/dist/celephais-gate --ui` dies with `ModuleNotFoundError: No module
named 'tkinter'`. Arch does not install `tk` with python, so `import tkinter`
raises `libtk8.6.so: cannot open shared object file` on this machine —
PyInstaller saw the same failure at build time, quietly left the module out,
and produced a 35 MB binary whose only interesting mode cannot start. The
command line still worked, which is why it went unnoticed. `pacman -S tk`, then
rebuild; there is nothing to fix in the spec.

### 2. The build virtualenv cannot live in the repository
This checkout is on the CIFS share, and `python3 -m venv build/.venv` there
fails with `[Errno 95] Operation not supported: 'lib' -> '.../lib64'`: the
share will not take the symlink. `build/make-gate.sh` now puts the virtualenv
in `$XDG_CACHE_HOME/celephais/venv`, overridable with `CELEPHAIS_VENV`, which
is local on every machine. That also explains the missing `build/.venv` that
`install-gate.sh` went looking for.

### 3. "That password does not open the vault" was a lie
`install-gate.sh --no-password` checks the password against the vault before
keeping it, so a typo is caught at install time rather than at the next login
with no screen to report on. The check ran under whatever python it found, and
a bare Arch python3 cannot import `cryptography`, so a perfectly good password
came back as wrong. It now picks the first interpreter that can actually open
the vault and, finding none, says so.

### 4. The Gate brought up a second tailnet on a machine already on the tailnet
`acnit1` runs tailscaled and reaches Core at `100.112.229.100:8080` directly.
The overlay path ignored `--no-tailnet` — only the command line honoured it —
so unlocking the vault started a userspace tailscaled of its own and registered
a duplicate node. `ui_session(no_tailnet=True)` skips it, `install-gate.sh
--no-tailnet` writes the flag into the `.desktop` entry, and the Gate uses the
network the machine already has.

### The vault does not live on the share either
`/mnt/cloud` is a CIFS automount with a ten-minute idle timeout. An application
whose vault sits there stops working the first time the share is asleep at
login, so the installed Gate is pointed at `~/.config/celephais/celephais.vault`
through `CELEPHAIS_VAULT`.

### Install it
```bash
sudo pacman -S --needed tk freerdp        # tkinter for the overlay, FreeRDP to connect
bash build/make-gate.sh                   # -> build/dist/celephais-gate, tkinter inside
bash deploy/install-gate.sh --no-password --no-tailnet
```
The password is asked once, checked against the vault and kept in
`~/.config/celephais/key`, mode 600. Add `--autostart` to open it at login.

### Left to see on the machine itself
Whether the one-window-per-monitor overlay lands correctly under Hyprland. A
Wayland client cannot place its own windows, and Hyprland puts new XWayland
windows on the active workspace, so both fullscreen windows may end up on the
same monitor with the second screen untouched. `screens()` already has the
fallback — returning no rectangles gives one window for the window manager to
fullscreen where it is — and the compositor can be detected if that is what the
real behaviour calls for. Not changed on a guess.

## Windows answered the open question: no private tailnet without administrator rights — `bin/celephais-gate` (2026-10-14)

Max, from the Legion laptop: "can't even log in on windows", with a photograph
of the key screen. Under the password box, eight lines of tailscaled's own log,
ending:

```text
safesocket.Listen: namedpipe.Listen: open \\.\pipe\celephais-22740: Access is denied.
```

That settles what "Left to find out on the machine itself" asked under the
2026-10-09 Windows build notes. `tailscaled --tun=userspace-networking` does
**not** run on Windows under a plain account. Userspace mode does away with the
TUN driver, which was the hoped-for reason admin would not be needed, but the
daemon still creates its control pipe with a security descriptor a process
without administrator rights may not apply, and Windows refuses it.
`--windows-mode=dev` buys the right to name a pipe of our own; it does not
change who is allowed to own one.

### The Gate was building a network it did not need
The laptop has Tailscale installed and is a node on the tailnet. Core was
reachable from it the whole time. The Gate never asked: it brought up its
private tailnet on every unlock because the vault had a key in it, and died
when that failed, holding Max at his own key screen with a working route to
Core sitting right there.

`core_reachable()` now runs first, on both paths. Core listens on nothing but
the Proxmox host's Tailscale address, so an answer from it is proof this
machine is already on the tailnet, and the Gate goes straight on. A four second
timeout, because on a machine that really is a stranger this has to fail fast
and get out of the way; a 401 counts as an answer, since a token Core dislikes
is not a reason to build a network, and `core()` will say what is wrong with it.
The private tailnet is still what makes the USB stick work on a borrowed
laptop — it is just no longer the only way in.

When it is needed and still cannot start, `_HINTS` now has a sentence for the
refused pipe: *Tailscale cannot start here without administrator rights.
Install Tailscale on this machine, or start the Gate as administrator.* It
names what will work, which is all the Gate can usefully say about a right it
does not have.

### The wall of log text on the screen is already fixed
The photograph is of the 2026-10-12 exe, which still raised
`NetError(f"tailscaled exited: {tail.text()}")`. Yesterday's work replaced that
with `_explain()` and `_Tail`. Nothing more to do there except build a new
`Gate.exe`: wine is not installed on acnit1 yet, `pacman -Q wine` finds
nothing, so `build/make-gate-wine.sh` has never run and `build/dist-win/Gate.exe`
is still the old one.

### Until that exe exists
Right-click `Gate.exe` → Run as administrator. The pipe is then created by an
account allowed to own it, and the old build gets through its own unlock.

Covered by `bin/test_gate.py`: Core answering 200 or 401 is reachable and a
dead port is not; the overlay's unlock builds no tailnet when Core answers and
still builds one when it does not. `bin/test_forward.py` checks that the
refused pipe produces the administrator sentence and that neither the pipe name
nor the rest of the daemon's output travels with it.

## The Windows build has never actually run — `build/make-gate-wine.sh` (2026-10-14)

Max hit the login failure again, from the stick this time, and plugged it in
here. The stick's `Gate.exe` is dated 2026-10-12 00:58 — older than the one in
`build/dist-win`, and older than every fix since: no redaction, no
windowed/console split, no `core_reachable()`. Its Linux neighbour was the
35 MB build with no tkinter in it. The vault on the stick is byte-identical to
the repo's, so nothing had drifted there.

So the wine build was finally tried, and it had never worked. wine creates the
prefix directory itself but not the path above it: with
`~/.local/share/wineprefixes` missing, every call died with `wine: chdir to
.../celephais-build : No such file or directory`. The script sent `wineboot`'s
output to `/dev/null`, and the Python installer line runs under `set -e`, so
the script died right there without ever reaching its own "python.exe did not
appear" message. The build log held exactly one line. Two changes: `mkdir -p
"$(dirname "$WINEPREFIX")"`, and `wineboot.log` and `python-install.log` are
kept in `build/win-tools` instead of discarded, because a silent install that
does nothing looks exactly like one that worked.

### Verified rather than assumed
`celephais-gate.exe` — the console half of the pair — run under wine on acnit1
against the live Core:

```text
no Tailscale credential in reach:
  work       vm 201   READY     seat  192.168.2.114
  personal   vm 202   OFF
  windows    vm 100   OFF

with a credential present:
    tailnet: not needed, this machine is on it already
  work       vm 201   READY     seat  192.168.2.114
```

The second run is the photograph's failure, short-circuited: the probe answers
before tailscaled is ever launched, so the named pipe is never asked for. (The
credential in that test is a fake, which is safe precisely because the probe
returns first and nothing ever uses it.)

Note that a Windows Gate reads only what the vault gives it. The repo `.env`
has an empty `TS_AUTHKEY` and an empty `TS_AUTH_CLIENT_SECRET` — only the OAuth
client *id* and the tags are set there — so the first run above took the
no-credential path by accident rather than by arrangement.

### On the stick now
`Gate.exe` and `celephais-gate.exe` from today's build, the 45 MB Linux binary
with tkinter in it, the unchanged vault, and the README that explains the two
executables. All three binaries checksum-matched after the copy.

## Waiting for a pipe that was already there — `bin/celephais_net.py` (2026-10-14)

The new exe got further and stopped differently: *"the Gate's private network
did not start in time."* That sentence comes from one place, the wait for
tailscaled's control socket, and it is only reached while the daemon is still
alive — `daemon.poll()` returns None every time round the loop. So tailscaled
ran for the full sixty seconds and the Gate never saw it come up.

The wait was `os.path.exists(sock)`. On Windows that socket is a named pipe,
`\\.\pipe\celephais-<pid>`, and a name under `\\.\pipe\` is not a file
`os.stat` can be asked about. Most likely the pipe was there within a second
and the Gate watched an unanswerable question for a minute.

`_control_ready()` asks the question that has the same answer on both systems:
does anything accept there. `open()` on Windows, `connect()` on POSIX. Access
denied is reported as *ready* on purpose — the pipe exists and this account may
not open it, which is a different failure, and the CLI that runs next gives it
a sentence of its own instead of this timing out again. On POSIX it also closes
a real race that nobody had hit yet: the socket file appears before anything
listens on it, and the CLI call that came next could land in the gap.

Two more, found while reading that function:

- The `BackendState == "Running"` wait reused `deadline` from the socket wait,
  and `tailscale up` between them is allowed to spend that whole budget. If it
  did, the loop ran zero iterations and a network that had not come up was
  reported as one that timed out. It has its own deadline now.
- `core_reachable()` waits eight seconds instead of four. A Tailscale that has
  been idle can need several seconds to put a route together, and four was
  tight enough to send a laptop that could reach Core off to build a second
  network — which is how this code path was reached at all. A machine with no
  Tailscale has no route to a 100.x address and is refused at once, so the
  longer wait costs it nothing.

`bin/test_forward.py` covers `_control_ready` on both branches: a socket with
no listener is not ready and the same socket listening is, which is exactly the
POSIX race; and on the Windows branch, missing is not ready while busy (231)
and access-denied are. Its end-to-end fake daemon now listens on the control
socket for real instead of touching the file — the honest fake, and the test
went from two sixty-second timeouts to 0.13s.

### wine cannot check this one
Running the fresh `celephais-gate.exe` under wine against a dead Core forces
the tailnet path, and the bundled daemon dies in six seconds:

```text
TPM: error opening: TBS Error 0x8028400F: A compatible Trusted Platform
Module (TPM) Security Device cannot be found on this computer.
```

wine has no TPM; Max's Windows 11 machine does, and its log read "TPM:
successfully read all properties". So wine builds the exe and proves the Core
path, but the tailnet path can only be tested on the machine itself.

## The daemon was refused, and said so to nobody — `bin/celephais_net.py` (2026-10-14)

Same sentence again from the rebuilt exe: *"the Gate's private network did not
start in time."* The readiness fix was right and it changed nothing, because
the answer it now gives correctly is "nothing accepts there" — tailscaled is
refused its control socket, carries on running anyway, and never opens one.
The whole minute of waiting is spent on a daemon that has already failed.

It had said so, in the first second, into `_Tail`. The timeout branch threw
that away and reported the clock instead: the diagnosis was sitting in memory
for sixty seconds while the Gate blamed time. Now the wait reads what the
daemon has said on every pass, and a refusal of its own control socket ends it
there and then with the sentence that names the ways through. The timeout
branch, when it is genuinely a timeout, explains itself with whatever was said
rather than only naming the symptom.

### The hint added this morning was too broad
`("access is denied", ...)` had to go. tailscaled says `syspolicy: GP lock not
acquired (Access denied.); reading policy settings without it` as a matter of
routine — it is in the photograph, and in every run under wine — and it has
nothing to do with the pipe. That matters much more now that the list is
consulted *while the daemon is still running*: a false positive there kills a
network that was about to come up. So the needles are split.

`_FATAL` holds only what nothing can recover from, which today means the
refusal of the control socket itself: `namedpipe.listen`, `safesocket.listen`.
`_HINTS` keeps those and adds the rest, and is used only to explain a daemon
that has already died or a `tailscale up` that has already failed. `dial tcp
... i/o timeout` is the other line that must never count as fatal: tailscaled
says it on the way to a network that works.

`_Tail.snapshot()` reads the lines without joining the reader thread. `text()`
waits two seconds for it to finish, which is right when the child is gone and
two seconds of nothing on every pass of a loop when it is not.

### What this does and does not fix
It does not get a plain Windows account onto the tailnet. Nothing in the Gate
can: the pipe tailscaled asks for belongs to the Administrators group and
Windows will not hand it over. What it does is replace a minute of silence
followed by a misleading sentence with one accurate sentence, straight away.

The two ways through a Windows machine remain what they were — be on the
tailnet already, or run with administrator rights — so the stick now carries
`Gate-admin.bat`, which is Gate.exe started through `Start-Process -Verb
RunAs`. `deploy/stick-README.txt` says when to reach for it and that a machine
already on the tailnet never needs it.

Covered in `bin/test_forward.py`: the routine lines are not fatal and the
refusal is; and a fake daemon that stays up, never listens and logs the
refusal gets the administrator sentence in under ten seconds instead of
waiting out `READY_TIMEOUT`.

## The remote desktop on one screen — `bin/celephais-gate`, `bin/celephais_ui.py` (2026-10-14)

Asked for: a way to keep the Realm's desktop on a single monitor, and it must
not break on a machine that only has one.

Both of those are answered by the same decision — the option is the *absence*
of `/multimon`, not a monitor named by number. FreeRDP then fills the screen
it opens on, which cannot be wrong on a laptop on its own, on this desk with
three, or on an arrangement nobody anticipated. `/monitors:1` would have had to
be checked against what is actually plugged in first, and would fail on the
laptop, which is exactly the thing that was asked not to happen.

Three ways to ask for it, and they agree:

```text
celephais-gate work --one-monitor      the command line
ONE_MONITOR=1                          in the vault: the default on a machine
remote desktop: one monitor            the overlay's own switch
```

The switch sits under the realm list, is mirrored on every screen like every
other control, and only changes the text on itself: nothing is reread and
nothing is sent, so a Realm that is already open is not disturbed. Its starting
position comes from the vault through a `prefer_one_monitor` hook, asked once
the vault is open. `flag()` reads the setting the way a person writes one -
"1", "true", "yes", "on" all mean yes, an empty value does not.

### The overlay was landing wrong on Hyprland, and now it is known
`bin/test_ui.py` could not run on acnit1 until today - there was no tkinter on
this machine - and the first run answered the question left open when the Gate
was installed here:

```text
window not fullscreen: [(False, '626x335+4482+740'), (True, '2560x1440+2560+0')]
```

One window floating at 626x335 on the laptop screen, one fullscreen on the
second monitor, the third untouched. A Wayland client may not place its own
windows and Hyprland puts each new XWayland window on the active workspace, so
one window per monitor cannot work there. `screens()` now returns nothing under
Wayland, which is already the signal for "ask for one window and let the window
manager fullscreen it where it is" - the fallback was written for a host that
would not name its monitors, and this is one. The per-monitor assertions in the
test only apply where the platform does name them.

Still noisy, not chased: three `overlay: bad window path name` lines during the
test run. A `realms` worker that returns after its screen has been replaced
builds labels into a frame that no longer exists; the drain catches it, says so
on a stderr the windowed build does not have, and carries on. Harmless, and
`refresh()` could check `winfo_exists()` before it builds.

## The client that could not read the monitors, and the line that hid it — `bin/celephais-gate` (2026-10-14)
`personal` would not open from the overlay on acnit1. What the overlay said:

```text
[set_termianl_nonblock]: tcsetattr(TCSANOW) failed with Inappropriate ioctl for device
```

Which is FreeRDP's typo, not ours, and says nothing. It is written *last*, while
the client puts back a terminal it was never given - the Gate feeds the Realm
password down its stdin, so there is no terminal there to put back - and
`_last_error()` reported the last `[ERROR]` line in the log. For a password fed
on stdin that is always this one. Every failure of every kind has been reading
the same sentence.

The real reason was six lines above it:

```text
[freerdp_settings_client_monitors_check_primary_and_origin]: Monitor configuration virtual desktop width must be 200 <= 160 <= 32766
[log_monitor]: [0] [primary] {0x0-80x80} [3] {80x80, desktopScale: 125}
[log_monitor]: [1] [       ] {-80x0-80x80} [4] {80x80, desktopScale: 125}
[freerdp_connect_begin]: ERRCONNECT_PRE_CONNECT_FAILED [0x00020001]
```

### sdl-freerdp3 cannot use this desktop
FreeRDP 3.31.1's SDL client reads every monitor on this Hyprland session as
80x80, builds a 160x80 virtual desktop out of them, and then refuses its own
layout as too small. It is not `/multimon`: the same thing happens with one
screen asked for, and with `SDL_VIDEODRIVER=x11` it reads them as 0x0 instead.
The client's own enumeration is right - `sdl-freerdp3 /list:monitor` prints
2048x1152 and 2560x1440 - so nothing can be checked in advance; only running it
shows this. `xfreerdp3`, two names further down the same list, passes
pre-connect on the same machine with the same arguments.

```text
sdl_f_multimon: PRECONNECT FAIL | Update size to 160x80
sdl_multimon:   PRECONNECT FAIL | Update size to 160x80
sdl_f:          PRECONNECT FAIL | Update size to 80x80
x11_f_multimon: preconnect ok
x11_f:          preconnect ok
```

So the Gate no longer picks one client and lives with it. `rdp_clients()`
returns every client on the machine in preference order, and `connect()` walks
the list - but only while a client failed *before it had reached the Realm at
all*, which is `ERRCONNECT_PRE_CONNECT_FAILED` or a monitor configuration it
would not accept. A refused password or a Realm that is not listening is the
same answer from every client on the machine, and trying them all would only
ask the Realm to refuse three times. On the overlay it reads:

```text
say: sdl-freerdp3 cannot use this machine's monitors; trying xfreerdp3
```

### What the one line says now
`_last_error()` takes the *first* real error, not the last: FreeRDP names the
cause and then follows it with the consequences, the advice, and finally the
terminal. `_NOISE` drops the advice lines, the monitor dump and the terminal
one. Two codes are said in words - `ERRCONNECT_CONNECT_FAILED` and
`ERRCONNECT_PRE_CONNECT_FAILED`. `ERRCONNECT_LOGON_FAILURE` is deliberately
left as FreeRDP writes it: the overlay reads the word "logon" in it and asks
for the password again, which is better than any sentence here.

And when one line is not enough, the whole log is kept in
`~/.cache/celephais/rdp-<realm>.log` on any failure. From the overlay there is
no terminal for it to land in; from the command line it is now written back out
after the client exits, so nothing is swallowed there either. A client given no
password still keeps the terminal to itself - its prompt is the terminal's -
and is then also the only client tried.

### The second wall: the exit node holds the LAN
With the client sorted, `xfreerdp3` got as far as the Realm and found nothing
there. acnit1 is at 192.168.2.102 on the same wire as pve at 192.168.2.103, and
nothing on that subnet answers:

```text
ip route get 192.168.2.115   ->  dev tailscale0 table 52
192.168.2.115 3389  TimeoutError      192.168.2.103 22  TimeoutError
ping -I enp43s0 192.168.2.115  ->  1 received, 1.366 ms
tailscale debug prefs: "ExitNodeID": "nAbHviE6N521CNTRL",
                       "ExitNodeAllowLANAccess": false,
```

An exit node is in use (`hostadmin`), and without `ExitNodeAllowLANAccess` it
captures the machine's own LAN along with everything else. Core answers because
it is addressed by its tailnet name, 100.112.229.100; the Realms are addressed
by the LAN addresses Core reports, and those go into the tunnel and out in
another country. Forced out of the wire with `-I enp43s0` the Realm answers in
a millisecond. The fix is one command on the machine, and it is Max's to run:

```bash
sudo tailscale set --exit-node-allow-lan-access=true
```

The Gate does not try to detect this - a routing table, a local address and a
Tailscale preference to read, on three platforms, for one hint - but it now
says what it saw, which is enough to find it:

```text
why: nothing answered on port 3389: the Realm is up, but this machine cannot
     reach its network
```

### Verified
All six tests pass. `bin/test_gate.py` covers the noise filter against the real
log, the two plain sentences, the word "logon" surviving, `_client_cannot_cope`
telling a bad client from a refused password, and the walk down the client list
stopping after the first client when the Realm itself said no. The walk was
also run for real against 127.0.0.1 and against `personal` at 192.168.2.115:
sdl-freerdp3 refused, xfreerdp3 was tried, the log was kept, and the sentence
above came back. Rebuilt and reinstalled: `~/.local/bin/celephais-gate` and the
stick's Linux binary are both `ae9c74fa` now.

Not verified: that `personal` actually opens. That needs the exit node command
above, and it is a session on Max's own screens.

## The Gate was being sent to an address only the house can reach — `bin/celephais_core.py` (2026-10-14)
Max, testing from the Windows laptop: *why don't you use tailscale IP?* He is
right, and this is the reason the stick has never worked away from home.

`realm_ip()` returned the **first** non-loopback IPv4 the guest agent reported.
The agent lists the wire first, so `work` came back as 192.168.2.114 - even
though that same Realm is on the tailnet as `workrealm-1`, 100.99.187.90, and
answers there from anywhere in the world:

```text
tailscale status:  100.99.187.90  workrealm-1
100.99.187.90:3389  open
```

A Gate on a hotel network cannot reach 192.168.2.114 at all. That is the whole
point of the tailnet the Gate carries inside itself, and it was being handed an
address the tunnel cannot deliver. Core now prefers an address in
100.64.0.0/10 - the range Tailscale hands out, and nothing else in a Realm has
one - and falls back to the wire for a Realm with no Tailscale of its own.
Nothing is lost on the LAN: two machines on one wire peer directly, and the
tailnet address is also the one a Tailscale exit node does not swallow, which
is the wall acnit1 hit this afternoon.

`bin/test_core.py` now has a `tailnet` flag on the fake agent: with it the
Realm is dialled at 100.99.187.90 although the agent still lists the LAN
address first, without it at 192.168.2.50, and `_on_tailnet()` is checked at
both ends of the range (100.63 and 100.128 are ordinary addresses).

### This has to be deployed, and it only fixes half of it
Core runs on pve, so nothing changes until it is replaced there:

```bash
install -m755 bin/celephais_core.py /opt/celephais/celephais_core.py
systemctl restart celephais-core
```

And `personal` (202) has no Tailscale inside it - there is no node for it in
the tailnet at all - so it stays unreachable from outside the house whatever
Core reports. Two ways out, neither taken yet: install Tailscale in the Realm
the way `work` has it, or make pve advertise 192.168.2.0/24 as a subnet route.
No peer advertises any route today, which is also why nothing but the Realms'
own tailnet addresses can be reached remotely.

### The stick, rewritten (16:37)
Everything but the vault was replaced and checksum-matched against the build:
`Gate.exe` `015ef281`, `celephais-gate.exe` `2a93f9b6`, `celephais-gate`
`fdcdf03d`, plus `Gate-admin.bat` and a `README.txt` that now says where the
client's log is kept, what "nothing answered on port 3389" means, and that away
from home only a Realm on the tailnet - today only `work` - can be reached.
The log goes where each platform keeps such things: `%LOCALAPPDATA%\celephais`
on Windows, `~/.cache/celephais` on Linux.

A deploy key for pve was made on acnit1 as `~/.ssh/celephais-pve`, so Core can
be replaced and its journal read from here instead of asking for every command
to be typed by hand. It is useless until its public half is on pve.

## On the tailnet, and never once in Core's log — `bin/celephais_net.py`, `bin/celephais-gate` (2026-10-14)
The laptop kept failing "the same way", and for most of the afternoon nobody
here knew which way that was. pve's journal settled it once there was a key to
read it with. Requests to Core, by source, since the 12th:

```text
2026-10-13T15  100.86.155.0    24     <- a Gate on another machine, working
2026-10-13T16  100.74.208.79    9
2026-10-14T11..19  100.76.200.49 only  <- acnit1; nothing else all day
```

Meanwhile the laptop's Gate *had* joined the tailnet - `celephais-gate`,
`-2`, `-3`, a new address each run, the last one four minutes before the
journal was read. On the network, and its first request to Core never arrived.

Reproduced on acnit1 by running the Gate's own private tailnet here, and timing
what came back:

```text
15.0s  tailnet up                     (BackendState: Running)
25.5s  first request to Core -> 200   took 10.5 s
25.5s  every request after it         instant
       (an earlier run: the first request timed out at 20 s)
```

"Running" means the node is on the network, not that it has a way to every
peer. The first packet to a peer waits while tailscaled finds the path, and on
the same wire as pve that took 10.5 seconds, once over 20. `core()` gives a
request 20 seconds and the overlay's `unlock` made exactly one. From a laptop on
somebody else's network the path takes longer than that, so the Gate gave up
just before it would have worked, every time.

On the 13th it worked, most likely because the path came up in time - that
build bundled the laptop's own installed tailscaled, today's bundles 1.104.1
from `make-gate-wine.sh`, and the network was not the same one. Not proven
which; it does not matter, because nothing should depend on it.

### The fix: wait for the path, in short tries
`celephais_net.reach(proxy, host, port)` keeps making SOCKS5 CONNECTs of five
seconds each until one gets through, for up to `TS_REACH_TIMEOUT` (90 s). An
abandoned try still moves the path along inside tailscaled, and a fresh one
goes out the moment the path is there instead of sitting out a long TCP
retransmit. The Gate calls it twice:

- `reach_core()` right after its own tailnet comes up, in the overlay's
  `unlock` and on the command line, which now prints
  `finding the way to Core…` and `core: reached in Ns`.
- in `connect()`, before any RDP client is launched: the Realm is a different
  peer with a path of its own, and an RDP client gives a connection far less
  time than that. `finding the way to work…` on the overlay; a Realm that
  never answers comes back as one sentence naming it and its address.

Neither changes anything on a machine that is on the tailnet already: there is
no private network then, and nothing to wait for.

### And one that would have bitten next
`_socks_connect()` set a 10-second timeout for the handshake and left it on the
socket. That socket is one side of every spliced RDP connection, so a session
would have been cut the first time the Realm said nothing for ten seconds - a
still desktop is a quiet one. The timeout now ends with the handshake, and the
forwarder gives a first connection 30 seconds rather than 10.

### Verified
`bin/test_forward.py`: a fake SOCKS proxy that leaves CONNECTs hanging for 2.5 s
is reached in more than one try and well under the deadline, one that never
answers ends in a `NetError` at its deadline and not after, and the handshake
timeout is gone from a socket that has finished its handshake. (The test's own
fake servers are rude on purpose and their leftover threads now complain into
a filtered `threading.excepthook`; the forwarder's threads still report.)
`bin/test_gate.py`: an unreachable Realm is said in words and no client is
launched at it; an unreachable Core is one sentence. All six suites pass.

For real: the command line, made to believe it was a stranger
(`core_reachable` patched to False), brought up its own tailnet on acnit1,
found Core and listed the Realms. The Windows exe was rebuilt and its
`--status` runs under wine; its tailnet path still cannot be tested there.

### Registered, never Running — and now it says why (20:15)
With AVG off the laptop finally gave a sentence: *the Gate's private network did
not come up in time.* That is the `BackendState` wait after `tailscale up`.
`up` returns as soon as the node is registered ("Starting"); "Running" waits for
a live connection to a relay or a peer. pve's tailscaled logged no
`magicsock: new contact` from either laptop node, where acnit1's probe at 19:43
showed `new contact … via=derp` at once. So the laptop's tailscaled reaches the
control server - it registers, it even reports `nue` as its home relay - and
never holds a relay connection. Not yet known why. The Oct 13 build that worked
bundled the laptop's own tailscaled; today's bundles 1.104.1.

Until now the Gate threw away everything Tailscale knew at exactly this point.
`_diagnosis()` now takes `tailscale status --json` - BackendState, home relay
and the `Health` warnings - plus tailscaled's last lines, redacted, writes them
to `tailnet.log` in `log_dir()` (`%LOCALAPPDATA%\celephais` on Windows), says
them on a terminal, and puts the first health warning after the sentence:
`… did not come up in time. Tailscale says: <warning>`. Health warnings are
written for people, so this is the one piece of Tailscale's output that is
allowed in front of the user. The wait is now `TS_RUNNING_TIMEOUT`, 120 s.
`log_dir()` is shared with the Gate's RDP log. Covered in `bin/test_forward.py`.

## Windows switched the network off behind every login — `bin/celephais_net.py` (2026-10-14)
The diagnosis earned its keep on its first run. The console exe on the laptop,
unelevated, AVG off, printed what tailscaled had been saying all along:

```text
BackendState: NoState
home relay: waw
health: Tailscale is starting. Please wait.
tailscaled: [RATELIMIT] format("control: doLogin(regen=%v, hasUrl=%v)") (16 dropped)
tailscaled: control: doLogin(regen=false, hasUrl=false)
tailscaled: health(warnable=wantrunning-false): error: Tailscale is stopped.
tailscaled: control: authRoutine: exiting
tailscaled: control: Client.Shutdown done.
```

Not a relay that could not be reached. Seventeen logins, each one followed at
once by "Tailscale is stopped". Windows tailscaled ties the network to whoever
asked for it, and when the last program talking to it disconnects it sets
WantRunning false and shuts the control client down. The program that asks is
`tailscale up`, which exits the moment it is done; every `tailscale status`
the Gate then polled with logged in again and was dropped again. The node
registered each time - that is the stream of `celephais-gate-N` nodes on the
tailnet, each "offline" within a minute - and never once stayed up.

`tailscale up --unattended` is the Windows-only flag that keeps it running
regardless. `up_argv()` builds the command now, kept apart like
`daemon_argv()` so `bin/test_forward.py` can check it under a pretend
`os.name`: `--unattended` on Windows, absent elsewhere (the flag does not exist
there), the rest identical. Linux has no such rule, which is why nothing on
acnit1 ever reproduced it. Why the Oct 13 build got through without it is not
known; it never had the flag either.

Two smaller things from the same photograph:

- The run was **not elevated** (no "Administrator:" in the console's title)
  and tailscaled still made its pipe. This morning's "Access is denied" may
  have been AVG's sandbox all along, not Windows. Gate-admin.bat stays as the
  fallback until a clean unelevated run proves otherwise.
- The Running wait now checks `daemon.poll()` each pass, so a tailscaled that
  dies says so at once with its last words instead of being asked for its
  status for two minutes.

## A headless `work` had nothing to log in on — `bin/celephais-seat` (2026-10-14)
With the tailnet fixed, the laptop got all the way to Core for the first time
today - `tailnet: up`, `core: reached in 0s` - and then:

```text
work: OFF
work: BOOTING
gate: work was still BOOTING after 180s
```

Core was right. Inside the Realm the guest agent and Tailscale were up and
nothing listened on 3389. `gnome-remote-desktop-headless.service` is wanted by
`gnome-session.target`, so it only runs inside a GNOME session, and there was
none: `/dev/dri` did not exist. `work` had `vga: none` and, started headless as
Core always starts it, no RTX either. GDM, configured to log `workrealm` in
automatically, said "your system does not have a primary GPU" and logged
nobody in.

Every boot it had been reached remotely in had the RTX. Boot -3 (Oct 13, the
laptop's working day) and boot -1 (this morning) both show `[HWAccel.CUDA]
Initialization of CUDA was successful`, then `RDP server started`. So `work`
was only ever remotely reachable while it held the seat, and the first truly
headless start - the one the power/seat model was built for - found the gap.
`personal` has `vga: std`, which is why its RDP answered this afternoon.

`vga: none` was right with the RTX attached, where a virtual screen gets
GNOME's top bar and nobody looks at it. So the display now follows the seat:
`HEADLESS_VGA=( [work]=std [personal]=std )`, and `set_display()` sets
`vga: none` in `attach_seat` and the Realm's own value in `detach_seat` -
before its early return, because a Realm with no passthrough left can still be
the one with no display, which is exactly how `work` was found. Windows is not
listed and its display is never touched.

`bin/test_seat.py` is new: it runs the real switcher against a fake `qm` on
PATH, keeping each VM's config in a file. Release of a Realm with nothing to
release still restores its display; switch takes it away and adds the RTX and
the keyboard; moving the seat on gives it back; Windows keeps `virtio`
throughout. Run against the switcher from HEAD it fails on the first assertion
with `{'name': 'work', 'vga': 'none'}` - the state `work` was found in.

Deployed to pve (`/usr/local/bin/celephais-seat`, identical to the repo),
`work` stopped through its agent, released (`update VM 201: -vga std`), and
started through Core the way the laptop does: OFF → BOOTING → READY at
100.99.187.90. Inside: `*:3389` listening, a GDM autologin session on `seat0`,
`/dev/dri/card1`. An X.224 request to 100.99.187.90:3389 got
`030000130ed0…` back. Seven suites pass.

## Windows' Remote Desktop asked for the password again — `bin/celephais-gate` (2026-10-14)
It works from the laptop now. What was left: after the Gate's own sign-in
screen, Windows raised a Windows Security dialog of its own, with the laptop
owner's account filled in, and the Realm's account had to be found under "more
choices" and typed a second time. The laptop has no FreeRDP, so the Gate falls
back to `mstsc`, which takes no password on its command line and none on stdin.
(No official FreeRDP build for Windows could be found to point Max at instead.)

mstsc looks for a saved credential named `TERMSRV/<target>` before it asks.
`windows_credential()` lends it the sign-in the Gate already has, for as long
as the client runs: `CredWriteW` through ctypes, so the password is on nobody's
command line; `CRED_PERSIST_SESSION`, so it cannot outlive the Windows logon;
deleted with `CredDeleteW` the moment the client exits, and the buffer zeroed.
`_termsrv()` names both `TERMSRV/127.0.0.1` and `TERMSRV/127.0.0.1:<port>`,
since through the forward the port changes every time and it is not worth
betting the prompt on which one mstsc uses. mstsc is no longer fed the password
on stdin either; it never read it.

Windows only hands a saved credential to a server it can only check with NTLM
if the machine is not in a domain - the default for a personal laptop. On a
domain machine policy refuses it and mstsc asks exactly as before; nothing is
worse.

Verified: `bin/test_gate.py` checks mstsc gets the credential and the password
is on neither its command line nor its stdin, FreeRDP gets no credential and
keeps reading stdin, and the helper is a no-op off Windows. Against the real
Windows API, under wine with the build prefix's Windows Python: both names
written, read back as user `workrealm` with the exact password (non-ASCII
included) and `Persist` 1, and both gone after the block. Not verified: mstsc
itself skipping its dialog, which only the laptop can show.

### And its certificate question (21:04)
With both sign-ins handled, mstsc still stopped once more: *Identitu vzdáleného
počítače nelze ověřit* - the Realm's certificate, `celephais-workrealm-1`, made
by `install-realm.sh` and signed by nobody. FreeRDP is told `/cert:ignore` for
the same certificate (the `ponytail:` note about pinning it for WAN stands).
`mstsc_without_certificate_warning()` sets mstsc's own per-user
`HKCU\Software\Microsoft\Terminal Server Client\AuthenticationLevelOverride`
to 0 - "connect and don't warn" - only while the Gate's client runs, and puts
back whatever was there: the old value, or no value at all. The user's other
Remote Desktop connections keep their warning. A second mstsc opened by hand
during a Gate session would not warn either; that is the cost.

Against the real registry under wine: absent → 0 inside → absent after;
2 → 0 inside → 2 after. `bin/test_gate.py` checks only mstsc gets it.

## The last two rough edges — `bin/celephais_ui.py`, `bin/celephais-gate` (2026-10-14)
**The desktop between the Gate and the Realm.** `hide()` withdrew every
overlay window before the RDP client was launched, so for the second or two
until the client's window appeared the person was looking at their own
desktop. `step_aside()` replaces it there: the overlay stays mapped, empty and
dark, and only drops `-topmost`, so the client's window opens over it. After
`STEP_ASIDE_MS` (6 s) it withdraws as before - everywhere, not just on
Wayland: a new window is normally put above everything that is not topmost,
but Hyprland opens it behind a fullscreen one by default, and a dark screen in
front of a live session is far worse than none (Esc on it closes the Gate and
the forward with it). A session that ended first has called `show()` and is
not withdrawn after the fact. `claim_focus()` now returns while stepped aside:
a grab still queued from the realm screen would otherwise take the keyboard
back from the remote desktop. `bin/test_ui.py` checks what is *asked* of the
window manager - Tk under Hyprland reads `-topmost` back as 0 even straight
after setting it.

**"[krb_log_context_encryption] … Client not found in Kerberos database"**
after Max logged off `work`. The kept log
(`~/.cache/celephais/rdp-work.log`) ended in `ERRINFO_LOGOFF_BY_USER`: a
normal end, which FreeRDP exits from with a code that is not 0. The Gate called
it a failure and reported the first error in the log, FreeRDP's Kerberos
attempt, which comes before NTLM on every connection (a Realm has no Kerberos
realm; `workrealm@ATHENA.MIT.EDU` is the library's default). Two fixes:
`_ENDED_BY_USER` (logoff and disconnect by user) now counts as success, and
`com.winpr.sspi.Kerberos` is in `_NOISE`. The second matters beyond the
message: always first in the log, the Kerberos line would also have been
reported for a wrong password, hiding the `LOGON_FAILURE` the overlay looks for
before it asks for the password again. Both covered in `bin/test_gate.py`.

Worth knowing: logging off *inside* a Linux Realm ends the session its remote
desktop lives in. GDM's automatic login is believed to run once per boot, in
which case the Realm then has no RDP until it is restarted - not verified on
`work`. Until it is: close the remote desktop window to leave a Realm running,
and log off only on the way to stopping it.

## `personal` is Fedora now — `deploy/install-realm.sh`, `deploy/mount-cloud.sh` (2026-10-15)
Max replaced VM 202's Ubuntu with Fedora 44 Workstation (kernel 6.19, GNOME
50), same id, same name, account `max`, 16 GB, 120 GB on `realms-ssd`. Three
things stood between it and the rest of Celephaïs.

**Core had lost it.** Destroying the old VM took its ACL with it: `user.cfg`
still had `/vms/100` and `/vms/201` and nothing for 202, so the token could
not power it, read its agent or park it. Put back exactly as install-core.sh
grants it, to the token and its user both:

```bash
pveum acl modify /vms/202 --tokens 'gate@pve!core' --roles CelephaisCore,CelephaisGuest,CelephaisPark
pveum acl modify /vms/202 --users gate@pve       --roles CelephaisCore,CelephaisGuest,CelephaisPark
```

After it Core reports `personal BOOTING 192.168.2.116`. The VM config needed
nothing: no `vga:` line means `std`, which is what the seat restores anyway.

**SELinux boxes in the guest agent.** Fedora runs qemu-ga as
`virt_qemu_ga_t`. It answers `network-get-interfaces` and `get-osinfo` and
may write into `/tmp`, but `rpm`, `getenforce`, firewalld, `/etc/gdm` all came
back "permission denied". Core's egress check would have run `tailscale debug
prefs`, been refused, and reported "tailscale is not installed here", and
logout would have failed outright. `install-realm.sh` now makes that one
domain permissive (`semanage permissive -a virt_qemu_ga_t`): the agent is a
root channel from the host already, the policy stays loaded, and denials are
still logged.

**Everything the Ubuntu Realms got by hand is now in the script.**
`install-realm.sh` installs what is missing with dnf or apt, sets GDM's
automatic login (`/etc/gdm/custom.conf` on Fedora, `/etc/gdm3/` on Ubuntu)
since the headless remote desktop only exists inside a session, opens 3389 in
firewalld, names the certificate with `uname -n` because Fedora 44 has no
`hostname` command, and runs `restorecon` on what it installs. The password
is asked for when not given, to keep it out of the shell history. `--tailnet
<name>` installs Tailscale, joins the tailnet under that name and sets
`exit-node-allow-lan-access`, so `personal` stops being the Realm that only
answers at home. `mount-cloud.sh` installs `cifs-utils` with dnf too.

Neither script ran on the VM from here: the agent cannot run them, and
`grdctl` wants the user's own session. They were copied into the guest at
`/var/tmp/celephais/` (root-only, readable by sudo) for Max to run at the VM's
console:

```bash
sudo bash /var/tmp/celephais/install-realm.sh max --tailnet personal
sudo bash /var/tmp/celephais/mount-cloud.sh 100.81.170.6 cloud <password> max
```

The Gate needed no change, as it takes the Realm list from Core and asks for
the sign-in itself. The stick's README now says `work` and `personal` are
reachable away from home; the binaries on it and in `~/.local/bin` were
already the current build and were rewritten from it unchanged.

Not done: the RTX on Fedora. With the seat, `personal` gets nouveau, which
drives a Turing card with its GSP firmware; the NVIDIA driver from RPM Fusion
would need its module signed for Secure Boot (`pre-enrolled-keys=1`). And
`/etc/celephais/core.env` on pve still has no `EXIT_NODE*` lines, so asking
any Realm for exit-node egress is refused until one is chosen.

### The mouse on its cable is a different mouse (2026-10-15)
The MX Vertical reaches the host two ways: through the Unifying receiver,
`046d:c52b`, and on its charging cable as itself, `046d:c08a` (Max's `lsusb`).
Only the receiver was in the seat's defaults, so plugged in by cable it stayed
on pve. `046d:c08a` is now a default in `SEAT_DEFAULT_USB` (Core) and
`SEAT_USB` (the switcher), claimed like the others even while unplugged. A
refusal of the receiver does not refuse the cable; `bin/test_core.py` says so.
Both deployed to pve and checksum-matched, Core restarted, and `usb3:
host=046d:c08a` hot-added to `personal`, which held the seat at the time.

## A Realm asleep on the monitors could not be stopped — `bin/celephais_core.py`, `bin/celephais-seat`, `bin/celephais_ui.py` (2026-10-15)
Max: `personal` went to sleep while it held the monitors, the Gate showed it
as "booting", and neither "stop" nor "give the monitors back" was there to
press. He stopped it in Proxmox at 21:02. Core's log has two refused releases
before that (`realm 'personal' is running; stop it first`) and no stop at all,
because the Gate never offered one.

Three faults, one under the other:

1. **Core could not see sleep.** A guest that suspends itself (ACPI S3) is
   `status: running` to Proxmox; only `qmpstatus: suspended` says otherwise.
   Its agent and its remote desktop are frozen, so `realm_state` found no IP,
   no 3389, and said BOOTING. It now says **ASLEEP** and skips asking the
   agent, which would only wait out a timeout.
2. **Nothing could stop it.** The agent is asleep and the ACPI button reaches
   nobody. `wake()` calls Proxmox's resume, which for a suspended guest sends
   QEMU `system_wakeup` (`PVE/QemuServer/RunState.pm`), then gives the agent
   up to `WAKE_WAIT` (20 s) to answer before the shutdown. Start on a sleeping
   Realm wakes it. The switcher's `stop_realm` does the same with `qm resume`,
   so moving the seat off a sleeping Realm no longer dies with "did not stop".
3. **The Gate offered stop only for READY.** Anything running now gets it:
   READY, BOOTING, ASLEEP. That covers more than sleep. A Realm on the monitors
   whose remote desktop is not set up never becomes READY (`personal` before
   install-realm.sh runs, and Windows, which has no agent at all and reads
   BOOTING whenever it holds the seat). A refused "give the monitors back"
   now shows Core's reason instead of "local seat: nobody".

And the cause inside the guest: Fedora's GNOME suspends when idle.
`install-realm.sh` now masks `suspend.target`, `hybrid-sleep.target` and
`suspend-then-hibernate.target`, and sets GNOME's idle sleep to `nothing`.
Hibernation is left alone, since it is the guest-side way to keep a session
across a seat change.

Tests: `bin/test_core.py` has a sleeping Realm read ASLEEP, woken once and
then shut down by the agent, started by waking it, and an awake one never
woken. `bin/test_seat.py` moves the seat off a sleeping `personal`, with a fake
`qm` whose agent and button both ignore a sleeping guest; against the old
switcher it fails with "VM 202 did not stop in 2s". `bin/test_ui.py` shows a
sleeping Realm on the monitors with a stop button, and fails against the old
UI. `test_ui_keyboard.py` fails here with or without this change: it needs
keyboard focus on a desktop someone is using.

Deployed: Core and the switcher on pve, checksum-matched, Core restarted.
Gate rebuilt (`celephais-gate` 4bea4cc5, `Gate.exe` 837b84fd,
`celephais-gate.exe` 1217dc8c) and installed in `~/.local/bin`. Not written:
the stick, which was unplugged by then, and the copy of install-realm.sh
staged in `personal`'s `/var/tmp/celephais/`, which predates the suspend
masking and needs replacing the next time the VM runs. Not seen on hardware:
a real sleeping guest woken by Core.

## The monitors' speakers were already forwarded; nouveau was swallowing them — `deploy/install-nvidia.sh` (2026-10-16)
Max: `personal` has two monitors with speakers in them and no sound output at
all. Nothing was missing on the Proxmox side. The seat carries the RTX's audio
function (`hostpci1: 0000:01:00.1`) beside the GPU, and the guest has it:

```text
/proc/asound/cards     0 [Intel ] HDA-Intel  (QEMU's, no codec, no PCM)
                       1 [NVidia] HDA-Intel  HDA NVidia
/proc/asound/pcm       01-03..01-09  HDMI 0..3, playback 1
0000:02:00.0  0x040300  snd_hda_intel
```

Four HDMI outputs, driver bound, two monitors connected (`card1-DP-2`,
`card1-HDMI-A-2`). What is missing is the **ELD** - the block a monitor sends
back saying it can play sound and in which formats. Every pin reads
`eld_valid 0`, and ALSA disables an output whose ELD is invalid, silently.
Read twice a minute apart, `monitor_present` had even moved from one device to
another with both still invalid, which is the flapping of a pin nobody is
driving properly.

The cause is nouveau, and it is documented by NVIDIA itself: the open drivers
"don't send the required ELD information to the audio hardware"
(download.nvidia.com/XFree86/gpu-hdmi-audio-document/). The live codec dump
does show `PD = 1, ELDV = 1` on pin 0x04 dev 01 while the driver's own cache
says invalid, so something is half-written, but nothing on the guest side
makes it whole. `work` does not have this problem: it runs the proprietary
driver, 595.99.02, with its key MOK-enrolled.

So `deploy/install-nvidia.sh` is the fix, and it is the same road `work` took:
RPM Fusion, `akmod-nvidia` so the module follows each new kernel, a signing
key from `kmodgenca`, and `mokutil --import`. Secure Boot is on in these
Realms (`mokutil --sb-state`: enabled, `pre-enrolled-keys=1` on the efidisk),
so an unsigned module is refused and Fedora quietly comes back up on nouveau.
The enrollment is three screens at the next boot and only someone at the
monitors can answer them, so the script stops there and says so. It builds the
module before the reboot rather than after, so a failure has a shell to be
read in. Recovery if the Realm comes back blind: release the seat, which gives
it `vga: std` again.

Not verified: that the proprietary driver does produce a valid ELD on this
card and these monitors. It cannot be tested from here - `work` holds no
monitors while `personal` has the seat, so its own HDMI audio has never been
looked at either.

Nothing in Celephais changed. The hardware was being handed over correctly the
whole time.

### The stick, rewritten (10:53)
All five files replaced from the current build and checksum-matched:
`celephais-gate` 4bea4cc5, `Gate.exe` 837b84fd, `celephais-gate.exe`
1217dc8c, plus `README.txt` and `Gate-admin.bat`. The vault was left alone.
`/var/tmp/celephais/` inside `personal` was restaged too: the copy of
install-realm.sh there predated the suspend masking, and install-nvidia.sh is
new.

### It worked, and `nvidia-smi` was a missing package, not a failed driver (2026-10-16, 11:28)
Max ran both scripts and reported the sound output had appeared but
`nvidia-smi` did not work. The driver was never the problem:

```text
lspci -nnk -s 01:00.0   Kernel driver in use: nvidia
lsmod                   nvidia 18522112, nvidia_drm, nvidia_modeset, nvidia_uvm
                        615.78.08 on kernel 7.2.10-200.fc44
```

MOK enrollment held and the signed module loaded. `nvidia-smi` is simply not
in any package the driver pulls in: `akmod-nvidia` brings
`xorg-x11-drv-nvidia-cuda-libs`, the libraries, while the tools live in
`xorg-x11-drv-nvidia-cuda`. Installed, it answers at once - RTX 2060, 40 °C,
414 MiB used, P8. `install-nvidia.sh` now installs it alongside the driver,
so the verification step the script itself prints cannot fail on a working
Realm.

**The ELD came back, exactly where the EDID says it should.** One valid ELD
now, and the desktop has a sink:

```text
/proc/asound/card1/eld#0.0   monitor_present 1  eld_valid 1  BenQ PD2500Q
wpctl status   * 62. TU106 High Definition Audio Controller Digital Stereo
                  (HDMI) [BenQ PD2500Q]
```

The second monitor will never join it, and not for want of a driver. Decoding
both EDIDs off the connectors:

| output | monitor | EDID | audio |
|---|---|---|---|
| HDMI-A-2 | BenQ PD2500Q | 256 B, CEA block rev 3 | basic-audio flag set, one SAD: PCM 2ch |
| DP-2 | DELL U2312HM | 128 B, **no CEA extension at all** | none declared |

A monitor advertises audio only in the CEA extension block, and the Dell has
no such block: it is a 2011 panel whose sound was an optional clip-on
soundbar fed from its own input, not from DisplayPort. There is no audio
endpoint behind that cable to find. One monitor with speakers, one sink, both
working - this is the finished state, not a half-finished one.

Also confirmed on this boot, all from `install-realm.sh`: RDP listening on
3389, `max` logged in automatically on seat0, `suspend.target` masked, and
`personal` on the tailnet as 100.95.216.39 - so it is now reachable from a
Gate away from home, which it never was. The guest agent runs unconfined
(`virt_qemu_ga_t` permissive while the system stays Enforcing), so Core's
egress, logout and every diagnosis above work from here.
