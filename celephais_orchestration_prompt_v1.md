# Celephaïs — Orchestration / Realm Lifecycle implementation prompt

## Context

We are building **Celephaïs**, a personal cloud computing system on top of Proxmox VE.

Current host:
- Proxmox VE 9.2.2
- Intel i7-8700K / 64 GB RAM
- Intel UHD 630 is now reserved for the **Proxmox local console** (`i915`)
- NVIDIA RTX 2060 is bound to `vfio-pci` and is the exclusive **Local Seat GPU**
- 3 physical monitors are connected to the RTX 2060
- Local keyboard/mouse/USB devices are passed to whichever Realm owns the Local Seat

Current Realms:
- VM 100 — Windows Gaming
- VM 201 — Work, Ubuntu 26.04.1
- VM 202 — Personal, Ubuntu 26.04.1

Known-good behavior:
- Work can run headless and be accessed via GNOME RDP/Tailscale.
- Work can own RTX 2060 + 3 monitors + USB and work locally.
- Windows can own RTX while Work remains running headless/remotely.
- Local Seat switching between Work and Windows has already been hardware-tested.
- Personal exists and must now be integrated into the same lifecycle, remote-access and Local Seat logic.
- Tailscale is used for private connectivity.
- Linux Realms normally use a Tailscale Exit Node; Windows normally uses direct egress.
- Celephaïs Core already exists as a service on the Proxmox host and owns Proxmox orchestration.
- Gate already exists as a portable client prototype with an encrypted vault.

## Important finding: VM state + PCI/VFIO passthrough

Before implementing lifecycle logic, treat the following as a hard constraint unless direct hardware tests prove otherwise:

**Proxmox `qm suspend <VMID> --todisk 1` cannot normally save a VM that has ordinary passed-through PCI/VFIO devices, because QEMU/Proxmox cannot save and restore the internal state of those devices.**

Do not assume consumer RTX 2060 VFIO state migration support.

QEMU has a VFIO migration framework, but it only works when the VFIO device/driver explicitly exposes migration/save-restore support. The current RTX 2060 + normal `vfio-pci` setup must be considered **non-saveable** until proven otherwise on this exact host.

Therefore:
- headless VM, no physical PCI passthrough -> Proxmox suspend-to-disk is the preferred persistent-state mechanism;
- VM with RTX/physical PCI passthrough -> do NOT blindly call `qm suspend --todisk 1`;
- saved VM state must be restored with a compatible device topology;
- changing from local-with-RTX to headless-without-RTX may require sacrificing the saved live session unless a validated hibernation path exists.

## Required research / validation before coding

Perform a small, explicit capability test layer.

For every Realm determine:
- whether `hostpci*` exists;
- whether physical USB Local Seat devices are attached;
- whether Proxmox will accept `qm suspend --todisk 1`;
- whether the exact passed-through device exposes a usable VFIO migration/save-state capability;
- whether guest hibernation works reliably with the NVIDIA driver and with the same/different PCI topology.

Never infer support from generic QEMU documentation alone. Test this exact PVE/QEMU/kernel/RTX combination.

No destructive test should run without a safe guest shutdown path and backup/recovery plan.

---

# Target lifecycle model

A Realm is not simply ON/OFF.

Use a state model similar to:

- `OFF`
- `BOOTING`
- `READY_HEADLESS`
- `READY_LOCAL`
- `PARKING`
- `PARKED_PVE`
- `PARKED_GUEST_HIBERNATE`
- `STOPPING`
- `ERROR`

Also track independent properties:

- `seat_owner`: none | work | personal | windows
- `launch_mode`: headless | local
- `egress`: exit-node | direct
- `saved_topology`: headless | local
- `saved_state_type`: none | pve-suspend | guest-hibernate

The UI should hide most of this complexity from the user.

---

# Parking policy

## Case A — Realm is headless and has no physical PCI/VFIO Local Seat devices

Preferred path:

```bash
qm suspend <VMID> --todisk 1
```

Expected result:
- guest live state is stored on disk;
- host RAM is released;
- guest CPU no longer consumes host CPU;
- resume returns to the previous desktop/session.

Resume:

```bash
qm resume <VMID>
```

Validate after every operation:
- PVE task success;
- VM status;
- guest agent after resume;
- RDP/health endpoint readiness.

This should be the normal behavior for Work/Personal when parked remotely/headless.

## Case B — Realm owns RTX / Local Seat

First choice: investigate and hardware-test **guest hibernation**.

Possible flow:

1. Ask Linux guest to hibernate itself to its own virtual disk.
2. Verify the VM reaches a stopped/off state.
3. Only after it is stopped, remove `hostpci*` / Local Seat USB mappings if necessary.
4. Mark saved state as `PARKED_GUEST_HIBERNATE`.
5. To restore with the **same local topology**, re-add the exact same devices first and boot the VM so Linux resumes its hibernation image.

Do not claim this is production-ready until NVIDIA + Ubuntu + passthrough resume is repeatedly tested.

## Case C — local saved state exists but next launch is requested headless

A saved live/hibernated session created with RTX attached must NOT simply be restored with RTX missing unless this exact topology change is proven safe.

Fallback algorithm if topology-changing resume is unsupported:

1. Reconstruct the original saved topology.
2. Restore/resume the Realm using the same RTX/USB devices it had when saved.
3. Wait for the guest to become healthy.
4. Request a graceful guest shutdown.
5. Wait until VM is fully stopped.
6. Remove RTX/Local Seat passthrough.
7. Start the VM headless.
8. Report clearly that crossing local -> headless required losing the preserved live desktop session.

This is intentionally a **fallback**. The user accepts losing the current live session only when changing topology makes restoration impossible.

The reverse direction applies too:

If a Realm is `PARKED_PVE` from a headless configuration and the next launch is local-with-RTX, do not attach RTX to an incompatible suspended VM state.

Fallback:

1. Resume the saved headless state.
2. Wait for guest health.
3. Gracefully shut down.
4. Add Local Seat PCI/USB.
5. Start locally.

Again, this sacrifices the saved live desktop session only at the topology transition.

## Case D — saving with RTX is impossible and guest hibernation is unreliable

Fallback:
- graceful shutdown;
- mark state as `OFF`;
- never hard-stop unless guest is unresponsive and user approves or timeout/recovery policy explicitly allows it.

Data integrity is more important than pretending a live state was preserved.

---

# Local Seat rules

The RTX 2060 is an **exclusive resource**.

A Realm may own Local Seat only if no other Realm owns it.

Local Seat includes at minimum:
- RTX GPU PCI function;
- RTX audio PCI function;
- configured keyboard;
- configured mouse / receiver;
- approved USB/audio peripherals.

Do not hot-move the RTX between running VMs.

Expected safe pattern:
- stop/park to a topology-safe point;
- edit Proxmox passthrough config;
- start/resume using a compatible topology.

The Intel UHD 630 is never part of Local Seat.
It stays with the Proxmox host permanently for the physical local Proxmox shell.

---

# Personal Realm integration

Add VM 202 / `personal` everywhere Work is currently supported.

Required:
- status
- start headless
- remote readiness
- RDP connection
- park/resume
- Local Seat acquire/release
- exit-node/direct egress control
- device passthrough policy
- UI listing
- portable Gate listing

Do not hard-code logic specifically for only Work and Windows.
Use a Realm registry/config object.

Example conceptual registry:

```yaml
realms:
  work:
    vmid: 201
    os: linux
    remote_protocol: rdp
    default_egress: exit-node
    local_seat_capable: true

  personal:
    vmid: 202
    os: linux
    remote_protocol: rdp
    default_egress: exit-node
    local_seat_capable: true

  windows:
    vmid: 100
    os: windows
    default_egress: direct
    local_seat_capable: true
```

---

# Permanent app + portable USB app

We need two distributions of the same Gate UX:

## 1. Installed desktop Gate

For trusted everyday devices, e.g. the ThinkPad.

Requirements:
- normal installed app;
- tray icon / small overlay;
- starts with user session if enabled;
- persistent encrypted config/vault;
- talks to Celephaïs Core over Tailscale/private connectivity;
- must work whether laptop is physically on the home LAN or elsewhere;
- does not require opening Proxmox WebUI;
- does not store Proxmox root credentials.

## 2. Portable Gate

For the USB key.

Requirements:
- self-contained executable;
- encrypted vault;
- same Realm list and actions as installed Gate;
- no installation required;
- no plaintext secret files;
- eventually bundle/use userspace/embedded Tailscale or equivalent so a clean host does not need Tailscale preinstalled;
- same Core API as installed Gate.

Both clients must expose the same logical actions:
- Open Remote
- Open Local
- Park
- Shutdown
- Acquire Local Seat
- Release Local Seat
- Direct / Exit Node egress
- status / errors
- Personal, Work and Windows

Do not duplicate orchestration logic in clients.
Clients call Core.
Core owns Proxmox state transitions.

---

# Local overlay / Web UI

Create a lightweight Celephaïs UI, not a custom modification of Proxmox WebUI.

It may be:
- local desktop app;
- tray/overlay;
- optionally a small web UI served by Core for trusted access.

Example:

```text
Celephaïs

WORK
State: READY_LOCAL
Seat: RTX
Network: Exit Node
[Park] [Open Remote] [Release Seat]

PERSONAL
State: PARKED_PVE
Seat: none
Network: Exit Node
[Open Remote] [Open Local]

WINDOWS
State: OFF
Seat: none
Network: Direct
[Open Local]
```

The user should never need to run a sequence of `qm` commands for normal operation.

Proxmox WebUI/SSH remains an administrator/recovery interface only.

---

# USB / audio device discovery and approval

Before every **local** launch, Core should inventory physical devices connected to the Proxmox host.

Do not rely on volatile USB bus/device numbers.

Prefer stable identity in this order:
1. USB serial number, if present;
2. VID:PID + product/manufacturer strings + stable physical USB port path;
3. stable physical port path as fallback.

Known devices can be automatically assigned according to policy.

Examples:
- keyboard -> Local Seat
- mouse/receiver -> Local Seat
- approved USB headset -> Local Seat
- unknown mass-storage -> never auto-pass by default
- unknown security key -> never auto-pass by default

Maintain a device registry:

```yaml
devices:
  - id: keyboard-main
    vid: "1532"
    pid: "02a2"
    role: keyboard
    action: auto-seat

  - id: mouse-main
    vid: "046d"
    pid: "c52b"
    role: mouse
    action: auto-seat
```

## Unknown device UX

If a new device appears before local launch:

Show:
- manufacturer/product if available;
- VID:PID;
- serial if available;
- physical port;
- device class;
- whether it looks like audio/storage/HID/etc.

Ask the user:
- Ignore
- Pass to this Realm once
- Remember and auto-pass to Local Seat
- Remember but never pass
- assign a human-readable role/name

If exactly one unknown device was newly connected, highlight it directly.

If several indistinguishable unknown devices are present and names/serials are insufficient:
- ask the user to unplug/replug the target device;
- watch udev events;
- correlate the disconnect/reconnect with the physical port/device identity;
- then offer approval.

Do not ask the user to unplug devices if stable serial/product/port identity already makes them unambiguous.

Use `udevadm monitor` / sysfs / udev metadata or an equivalent event source rather than polling only `lsusb`.

Persist approval in Core configuration, not in individual VM configuration.

---

# Audio policy

Handle audio as part of Local Seat.

There are two classes:

1. RTX HDMI/DisplayPort audio:
   - tied to the RTX audio PCI function;
   - attach with the GPU for local monitor audio.

2. USB audio devices:
   - headphones/headsets/DACs/microphones;
   - discover and approve using the same stable USB registry;
   - pass to the active Local Seat Realm if policy says so.

For remote sessions, use the remote protocol's audio/microphone redirection rather than physical Local Seat passthrough.

Do not automatically pass host audio devices to a headless Realm unless explicitly configured.

---

# Pre-launch reconciliation

Before every launch, Core should run a reconciliation step.

Conceptual:

```text
desired = {
  realm,
  launch_mode,
  seat_owner,
  egress,
  approved_devices
}

actual = inspect_proxmox_and_host()

plan = calculate_safe_transition(actual, desired)

show warnings if transition loses a saved live state

execute step-by-step

verify every step

return READY only after health checks
```

Examples of checks:
- VM current power state;
- suspended / hibernated / off;
- current `hostpci*`;
- current USB passthrough;
- seat owner;
- RTX bound to vfio-pci on host;
- USB inventory;
- unknown devices;
- Tailscale/Core reachability;
- guest agent;
- RDP readiness;
- egress mode.

All state transitions should be idempotent where possible.

---

# API concepts

Suggested Core actions:

```text
GET  /realms
GET  /realms/{realm}

POST /realms/{realm}/open
  body: { mode: "remote" | "local" }

POST /realms/{realm}/park
POST /realms/{realm}/shutdown

POST /realms/{realm}/seat/acquire
POST /realms/{realm}/seat/release

POST /realms/{realm}/egress
  body: { mode: "exit-node" | "direct" }

GET  /devices
POST /devices/{id}/approve
POST /devices/{id}/ignore
POST /devices/{id}/assign
```

Core should return:
- current state;
- requested transition;
- whether saved session will be preserved;
- warnings requiring confirmation;
- progress;
- final verified state.

---

# UX rule for state loss

State loss caused by a topology change must never be silent.

Example warning:

```text
Work is parked with the Local Seat / RTX attached.

Opening it remotely requires changing its hardware topology.
The saved desktop session cannot be preserved safely with the current GPU configuration.

Celephaïs will:
1. restore Work with its previous Local Seat;
2. gracefully shut it down;
3. release the RTX;
4. start Work headless.

Continue?
```

If future hardware testing proves a safe GPU-state path, remove this warning and use the preservation path automatically.

---

# Reliability requirements

- Never use `qm stop` as the normal shutdown path.
- Prefer guest-agent graceful shutdown.
- Timeouts must lead to explicit ERROR/recovery state.
- Never start a remote Realm while stale RTX/Local Seat mappings are still attached.
- Never assign RTX to two VMs.
- Never silently discard a suspended/hibernated session.
- Never modify UHD 630 ownership; it belongs to Proxmox.
- Preserve a manual Proxmox recovery path.
- Log every transition and exact command/API operation.
- Add dry-run/planning mode before destructive topology transitions.
- Hardware-test every lifecycle path repeatedly before treating it as production.

---

# Required implementation order

1. Add Personal VM 202 to the Realm registry/API/UI.
2. Finish/verify Personal headless RDP readiness.
3. Implement reliable headless `park -> qm suspend --todisk 1 -> resume`.
4. Add explicit `PARKED_PVE` state detection.
5. Add safe topology compatibility checks.
6. Research/test guest hibernation with Work + RTX 2060.
7. Implement local->local preserved resume only if repeated tests pass.
8. Implement topology-change fallback: restore -> graceful shutdown -> reconfigure -> boot.
9. Implement USB/audio inventory and stable device registry.
10. Implement unknown-device approval UX.
11. Extend Local Seat to approved audio/USB devices.
12. Build permanent desktop Gate/tray/overlay.
13. Keep portable Gate feature-parity.
14. Add progress/warnings/recovery UI.
15. Run repeated real-hardware tests across Work, Personal and Windows.

## Do not shortcut the GPU-state problem

The ideal goal is:
- preserve the live state even when a Realm used the RTX;
- restore it safely;
- preferably allow switching to headless mode without losing state.

But if the exact RTX/VFIO stack cannot safely do that, choose deterministic graceful shutdown over hacks that risk corrupting VM state.

The system should optimize for:
1. data integrity,
2. deterministic recovery,
3. state preservation,
4. convenience,
in that order.
