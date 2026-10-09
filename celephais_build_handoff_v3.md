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
Gate client · Realm-local Tailscale or subnet router · calling the Local Seat
switcher from Core · any persistent state. Core is stateless; it reads the truth
from Proxmox on every request.
