# Celephaïs — Phase C: Proxmox Migration & Realm Build

Date: 2026-10-03. Follows `PHASE-B-PREFLIGHT.md`, all three preflight tests passed.

**Note on numbering.** `2ndprompt.md` planned Phase C = POC, Phase D = migration. The POC's question — *"can a 3-monitor client drive a headless Linux desktop, with hardware encoding, from a container?"* — was answered by Tests 1 and 3. So Phase C is now the migration, and the Celephaïs launcher becomes Phase D.

> **This is the first destructive phase.** Everything before this was reversible. This wipes a disk.

---

## 0. The three things that can actually hurt you

1. **Your data.** Whatever is on those two SSDs is gone after §2. No undo.
2. **Your Windows install.** It is destroyed. You rebuild it as a VM. You need your licence.
3. **Your working computer.** For a few hours you will not have a usable desktop PC. Do not start this the night before you need it.

Your laptop stays untouched throughout and remains your escape hatch.

---

## 1. STOP GATE — before you touch anything

Tick every box. Do not start §2 until all are ticked.

- [ ] **I know what is on both SSDs.** Run `wmic logicaldisk get name,size,freespace` in Windows, then actually look in each drive.
- [ ] **Everything I care about is copied somewhere else** — external disk, the old laptop, or cloud. *Verify by opening a few files from the copy.* A backup you have not opened is not a backup.
- [ ] **Browser profiles are saved** — bookmarks, passwords, 2FA sessions. These hide in `%LOCALAPPDATA%` and are the most-regretted loss.
- [ ] **I have my Windows licence.** Digital licence tied to the motherboard usually re-activates; a retail key should be written down. Check with `slmgr /dli`.
- [ ] **Game saves that are not cloud-synced are backed up.**
- [ ] **I have a second working computer** (the laptop) with internet, to read these docs and search for fixes.
- [ ] **I have a wired network cable** for the PC. Do not debug Wi-Fi drivers during a migration.
- [ ] **I have a spare USB stick** for the Proxmox installer.
- [ ] **I accept the storage plan in §3** (no mirror).
- [ ] **I have 4+ uninterrupted hours.**

**If any box is unticked, stop. The cost of waiting a day is zero. The cost of a missing backup is permanent.**

---

## 2. Storage plan — decide now, it is hard to change later

Your hardware: 2 × ~250 GB SSD, plus separate disks for games.

**The problem:** Proxmox (~20 GB) + Linux work realm (60–100 GB) + Windows 11 (100 GB minimum) exceeds 230 GB. Mirroring the two SSDs would leave ~230 GB total, which does not fit.

**The plan — no mirror:**

| Disk | Holds | Redundancy |
|---|---|---|
| **SSD #1** | Proxmox host + Work Realm container | None — backed up nightly |
| **SSD #2** | Windows gaming VM disk | None — re-installable |
| **Game disks** | Steam library, large files | None — re-downloadable |
| **Old laptop** | `vzdump` backup target | *this is your redundancy* |

**Why this is acceptable:** the irreplaceable thing is your *work realm state* — config, projects, installed apps. That gets backed up nightly to the old laptop. Windows and games cost time to rebuild, not data.

**When money allows:** add one NVMe and mirror SSD #1. Not before.

---

## 3. Install Proxmox VE 9.2

### 3.1 Make the installer

On the laptop:
```
# download from https://www.proxmox.com/en/downloads  (Proxmox VE 9.2 ISO)
lsblk -d -o NAME,SIZE,MODEL,TRAN          # find the USB stick; TRAN=usb
sudo dd if=proxmox-ve_9.2-1.iso of=/dev/sdX bs=4M status=progress oflag=sync
```
> Erases `/dev/sdX`. Check twice.

### 3.2 BIOS

Your Test 2 settings should already be in place. Confirm:

| Setting | Value |
|---|---|
| Intel (VMX) Virtualization Technology | Enabled |
| VT-d | Enabled |
| Above 4G Decoding | Enabled |
| Primary Display | **CPU Graphics** |
| iGPU Multi-Monitor | Enabled |
| Launch CSM | Disabled |

**Monitor cable goes to the motherboard**, not the RTX 2060.

### 3.3 Install

Boot the installer. At the disk selection screen:

- **Target: SSD #1 only.**
- Filesystem: **zfs (RAID0)** — single disk. You still get snapshots and checksums.
- Options → **ashift 12**, compression **lz4**.
- **Do not select SSD #2.** You add it afterwards.
- Hostname: something you will recognise, e.g. `celephais`.
- Set a strong root password and a real email.
- Network: **wired**, static IP recommended (e.g. `192.168.0.50/24`).

Reboot. Remove the USB. You should reach a login prompt showing `https://192.168.0.50:8006`.

**From here on, work from the laptop's browser and SSH. The PC is a server now.**

### 3.4 First-boot housekeeping

```
# no subscription? switch to the no-subscription repo
sed -i 's/^deb/#deb/' /etc/apt/sources.list.d/pve-enterprise.sources 2>/dev/null
echo 'deb http://download.proxmox.com/debian/pve trixie pve-no-subscription' \
  > /etc/apt/sources.list.d/pve-no-sub.list
apt update && apt -y full-upgrade

# cap ZFS ARC at 8 GB (default would take up to 16)
echo "options zfs zfs_arc_max=8589934592" > /etc/modprobe.d/zfs.conf
update-initramfs -u -k all
```

### 3.5 Add SSD #2 for the Windows VM

```
lsblk -d -o NAME,SIZE,MODEL        # identify SSD #2, e.g. /dev/sdb
# DOUBLE CHECK this is the right disk - it will be erased
zpool create -o ashift=12 winpool /dev/disk/by-id/<ssd2-id>
zfs set compression=lz4 winpool
pvesm add zfspool winstore -pool winpool -content images,rootdir
```

---

## 4. Host configuration

### 4.1 GPU split — RTX 2060 to VFIO, iGPU stays on the host

```
# confirm the IDs from Test 2
lspci -nn | grep -i nvidia          # expect 10de:1f08 (VGA) and 10de:10f9 (audio)

cat > /etc/modprobe.d/vfio.conf <<'EOF'
options vfio-pci ids=10de:1f08,10de:10f9
softdep nouveau pre: vfio-pci
softdep nvidia pre: vfio-pci
EOF

# Intel hardware encoding on the host, HuC firmware on
echo "options i915 enable_guc=2" > /etc/modprobe.d/i915.conf
apt install -y intel-media-va-driver-non-free vainfo intel-gpu-tools

update-initramfs -u -k all
reboot
```

**Verify after reboot:**
```
lspci -nnk -d 10de:                               # Kernel driver in use: vfio-pci
ls -l /dev/dri/                                   # renderD128 present (the iGPU)
vainfo --display drm --device /dev/dri/renderD128 | grep -E 'Driver|H264.*Enc'
cat /sys/module/i915/parameters/enable_guc        # 2
```
**Checkpoint:** RTX 2060 on `vfio-pci`, iGPU usable by the host. If the iGPU is missing, BIOS `iGPU Multi-Monitor` is off.

### 4.2 Network

Proxmox creates `vmbr0` during install. Leave it alone.

```
# Tailscale on the host
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --ssh
tailscale ip -4
```

Then in the Tailscale admin console, restrict what the client tag may reach:
```
"acls": [
  { "action": "accept", "src": ["tag:celephais-client"],
    "dst": ["celephais:8006", "celephais:3389"] }
]
```
**Nothing is exposed to the public internet. Do not port-forward 3389.**

### 4.3 API access for the future launcher

```
pveum group add celephais
pveum user add celephais@pve --groups celephais
pveum user token add celephais@pve client --privsep 1
#  >>> COPY THE TOKEN SECRET NOW. It is shown once. <<<
```
Grant rights after the guests exist (§5.6).

> **Known limit:** only the literal `root@pam` can set `args:`, `hostpci` or LXC `dev0`. Configure those once as root; the launcher only ever needs start/stop/status.

---

## 5. The Work Realm container — **do this before migrating anything**

Test 3 proved the container path but stopped at GNOME's session handover. **Confirm the full login here, on real Proxmox, before you move a single file into it.**

### 5.1 Create the container

```
pveam update
pveam available | grep ubuntu-26.04
pveam download local ubuntu-26.04-standard_26.04-1_amd64.tar.zst

pct create 110 local:vztmpl/ubuntu-26.04-standard_26.04-1_amd64.tar.zst \
  --hostname work-realm \
  --cores 4 --memory 16384 --swap 2048 \
  --rootfs local-zfs:80 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot 1
```

### 5.2 Give it the iGPU

```
# find the render group id INSIDE the container
pct start 110
pct exec 110 -- getent group render        # e.g. render:x:993:
pct stop 110

pct set 110 --dev0 /dev/dri/renderD128,gid=993      # use YOUR number
pct start 110
```

**Verify immediately:**
```
pct exec 110 -- bash -c "apt update -qq && apt install -y -qq vainfo vulkan-tools intel-media-va-driver mesa-vulkan-drivers"
pct exec 110 -- vainfo --display drm --device /dev/dri/renderD128 | grep -E 'Driver version|H264High'
pct exec 110 -- vulkaninfo --summary | grep -iE 'deviceName|driverName'
```
**Checkpoint:** iHD driver, `VAEntrypointEncSlice` or `EncSliceLP`, and an **Intel** Vulkan device.
**If either is missing, stop** — go to §7 fallback.

### 5.3 Install the desktop

```
pct exec 110 -- bash -c "DEBIAN_FRONTEND=noninteractive apt install -y \
  ubuntu-desktop-minimal gnome-remote-desktop gdm3 pipewire wireplumber"
```

### 5.4 Configure Remote Login

```
pct enter 110
```
Inside the container:
```
# a real user for yourself
adduser max && usermod -aG sudo max

# TLS certificate — must NOT live in /root, the service is sandboxed away from it
mkdir -p /etc/gnome-remote-desktop
openssl req -new -newkey rsa:4096 -days 3650 -nodes -x509 \
  -subj "/CN=celephais-work" \
  -out /etc/gnome-remote-desktop/tls.crt \
  -keyout /etc/gnome-remote-desktop/tls.key
chmod 644 /etc/gnome-remote-desktop/tls.crt /etc/gnome-remote-desktop/tls.key

grdctl --system rdp set-tls-cert /etc/gnome-remote-desktop/tls.crt
grdctl --system rdp set-tls-key  /etc/gnome-remote-desktop/tls.key
grdctl --system rdp set-credentials max '<a strong password>'
grdctl --system rdp enable

# LESSON FROM TEST 3: the daemon caches credentials at startup.
# Without this restart it answers "Credentials are not set, denying client".
systemctl restart gnome-remote-desktop.service

grdctl --system status --show-credentials
ss -tln | grep 3389
```
**Checkpoint:** `Status: enabled`, and something listening on `3389`.

### 5.5 ⭐ THE GATE — one real multi-monitor login

From your 3-monitor PC… which is now the server. So use **the laptop plus any second screen**, or borrow a monitor. Two displays is enough to prove the mechanism.

```
# on the laptop
sudo apt install -y freerdp3-sdl
sdl-freerdp3 /v:<container-ip>:3389 /u:max /p:'<password>' \
  /multimon /dynamic-resolution /gfx /sound /clipboard /cert:ignore
```

**Must pass:**
1. You reach a GNOME login screen, then a desktop.
2. **Settings → Displays shows one display per physical monitor.**
3. Maximising a window fills only one monitor.
4. On the container: `journalctl -u gnome-remote-desktop | grep -i vaapi` shows **`Successfully initialized VAAPI`**.
5. Disconnect, wait 30 s, reconnect — your windows are still there.

**If 1–3 pass but 4 does not:** it works, but on the CPU codec. Usable; investigate before relying on 3 monitors.
**If 1 fails at the handover** (the step Test 3 could not reach): go to §7.

**Do not proceed past this gate until it passes.**

### 5.6 Backups and API rights

```
# nightly backup to the old laptop (add it as storage first, NFS or SMB)
# Datacenter → Backup → Add:  daily 03:00, mode snapshot, target: laptop share

pveum acl modify /vms/110 --users celephais@pve --roles PVEVMUser
```

---

## 6. Windows gaming VM

Only after §5.5 passes.

```
qm create 120 --name windows-gaming \
  --machine q35,version=11.0+pve2 \
  --bios ovmf --efidisk0 winstore:1,efitype=4m,pre-enrolled-keys=1 \
  --cpu host --cores 8 --sockets 1 \
  --memory 24576 --balloon 0 \
  --scsihw virtio-scsi-single \
  --scsi0 winstore:120,iothread=1,discard=on,ssd=1 \
  --net0 virtio,bridge=vmbr0 \
  --ostype win11 \
  --tpmstate0 winstore:1,version=v2.0 \
  --ide2 local:iso/Win11.iso,media=cdrom \
  --ide0 local:iso/virtio-win.iso,media=cdrom

# GPU: pass the WHOLE device, both functions
qm set 120 --hostpci0 0000:01:00,pcie=1,x-vga=1

# pin to 4 physical cores, leave the rest for the host and the realm
qm set 120 --affinity 0-3,6-9      # check `lscpu -e` for YOUR sibling layout
```

**Notes that will save you time:**
- `--balloon 0` is **required**. Passthrough VMs need all RAM pinned.
- Load the **virtio** storage driver during Windows setup, or it sees no disk.
- Install the NVIDIA driver *after* Windows is up. Code 43 is not expected (NVIDIA allowed VM passthrough from driver R465).
- **Keyboard and mouse:** use evdev rather than passing a USB controller —
  ```
  qm set 120 -args "-object input-linux,id=kbd,evdev=/dev/input/by-id/<your-kbd>,grab_all=on,repeat=on \
                    -object input-linux,id=mouse,evdev=/dev/input/by-id/<your-mouse>"
  ```
  Both Ctrl keys together switch between host and guest.
- **Anti-cheat:** some kernel-level anti-cheat blocks VMs. Not fixable. Assume some titles will not run.
- RAM snapshots are impossible with passthrough. Disk snapshots work.

**Sitting at the PC and wanting Linux?** Run the Celephaïs client *inside* this Windows VM — it owns the monitors, and the hop to the realm is local and fast.

---

## 7. If the realm fails — fallbacks in order

| Try | What | Cost |
|---|---|---|
| 1 | **Autologin + `--headless`** instead of Remote Login. Set GDM autologin for `max`, then `grdctl --headless rdp enable` and `systemctl --user enable --now gnome-remote-desktop-headless`. Skips the handover step entirely. | None |
| 2 | **Newer GNOME** from a backports PPA — the handover bugs (#330, #354) are actively worked on. | Small |
| 3 | **Privileged container** (`--unprivileged 0`). Often fixes logind oddities. | Weaker isolation |
| 4 | **VM with full iGPU passthrough (GVT-d).** Works, but the host and all containers lose the iGPU. | Architectural |

Fallback 4 is the real retreat. Everything above it keeps the design intact.

---

## 8. Rollback — how to get your PC back

**During §2–3 (before Proxmox is installed):** nothing has happened. Just boot Windows.

**After Proxmox is installed:** your Windows install is gone. Recovery means reinstalling Windows to bare metal from a USB stick. **This is why §1 exists.**

**If Proxmox installs but something is broken:**
- Proxmox boots to a shell even with a broken GPU config — SSH in from the laptop and undo `/etc/modprobe.d/vfio.conf`, then `update-initramfs -u -k all`.
- Container will not start: `pct start 110 --debug`.
- You locked yourself out of the network: plug a monitor into the **motherboard** port and log in locally.

---

## 9. Not in Phase C

The USB client · Isolation Mode · Clean Boot Mode · Android realm · the cloud filesystem · P2P transfers · encrypted containers · multiple realms.

Phase C ends when: **you can sit at your laptop, connect to the Work Realm over Tailscale, see your monitors, and want to work there.**

---

## 10. Order of operations — the short version

```
1.  Back up everything. Verify the backup.          <- STOP GATE
2.  Decide storage (no mirror).
3.  Install Proxmox on SSD #1.
4.  Add SSD #2 as winstore.
5.  Split the GPUs (RTX -> vfio, iGPU -> host). Verify.
6.  Tailscale + ACLs + API token.
7.  Build the Work Realm container.
8.  Verify VA-API + Vulkan inside it.               <- CHECKPOINT
9.  Install GNOME + Remote Login (restart after credentials!).
10. ONE REAL MULTI-MONITOR LOGIN.                   <- THE GATE
11. Backups to the old laptop.
12. Windows gaming VM + RTX passthrough.
13. Use it for a week before building anything else.
```

Step 13 is not padding. Phase D (the launcher) should be designed around how you *actually* use the realm, not how we imagine you will.
