# Celephaïs — Phase B: Preflight Tests

Date: 2026-10-02. Follows `PHASE-A-VALIDATION.md`. Decision taken: **the Work Realm desktop is GNOME**, conditional on Test 1 passing.

**Purpose:** prove or kill the design *before* anything is wiped.

---

## 0. Ground rules

**Nothing in this document modifies an installed operating system.**

| Test | What it touches | Permanent change? |
|---|---|---|
| 1 | A live USB stick (erased), plus `mstsc.exe` on Windows | **No** |
| 2 | BIOS settings on the 8700K (written down first, reverted after), same live USB | **No** (BIOS is reversible) |
| 3 | Docker containers on the laptop, auto-deleted | **No** |

The only thing genuinely erased is **the USB stick you choose for the live image**. Pick an empty one.

**Run them in this order.** Test 1 can kill the project. Do it first and do not skip ahead.

---

# TEST 1 — Does multi-monitor GNOME over RDP actually work?

**This is the test that matters.** Everything else is details.

**Question:** When a 3-monitor client connects to a headless GNOME desktop, does GNOME build 3 separate working monitors?

**Setup:**
- **Server** = your laptop, booted from an Ubuntu 26.04 Desktop live USB. Its installed system is not touched.
- **Client** = your 3-monitor PC, still running Windows, using the built-in `mstsc.exe`.
- Both on the same network. No Tailscale needed for this test.

**Time:** about 1 hour, most of it downloading.

### 1.1 Make the live USB

Download **Ubuntu 26.04 Desktop** (not Server — you need GNOME).

```
# On the laptop. Replace sdX with your USB stick. CHECK THIS TWICE.
lsblk -d -o NAME,SIZE,MODEL,TRAN          # find the stick; TRAN=usb
sudo dd if=ubuntu-26.04.1-desktop-amd64.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

> **This erases /dev/sdX completely.** If unsure, use the GNOME "Disks" app or Rufus on Windows instead.

### 1.2 Boot the laptop from it

Boot the stick. Choose **"Try Ubuntu"**. Do **not** choose Install.

You are now in a GNOME session running entirely in RAM.

### 1.3 Set up the remote desktop server

```
# Give the live user a password (RDP login needs one)
sudo passwd ubuntu                         # set anything, e.g. "test"

# Install the RDP server into RAM
sudo apt update
sudo apt install -y gnome-remote-desktop
```

Now enable **Remote Login** (the headless mode — this is the one that creates virtual monitors):

**Easy path — the GUI.** Settings → System → **Remote Desktop** → turn on **Remote Login**. Note the user name and set a password.

**If the GUI has no "Remote Login" switch, use the command line:**

```
# Self-signed certificate (required)
mkdir -p ~/rdpcert && cd ~/rdpcert
openssl req -new -newkey rsa:4096 -days 3650 -nodes -x509 \
  -subj "/CN=celephais-test" -out cert.pem -keyout key.pem
sudo mkdir -p /etc/gnome-remote-desktop
sudo cp cert.pem key.pem /etc/gnome-remote-desktop/

sudo grdctl --system rdp set-tls-cert /etc/gnome-remote-desktop/cert.pem
sudo grdctl --system rdp set-tls-key  /etc/gnome-remote-desktop/key.pem
sudo grdctl --system rdp enable
sudo systemctl enable --now gnome-remote-desktop.service

# Confirm it is listening
sudo grdctl --system status
ss -tlnp | grep 3389
ip -4 addr show | grep inet            # note the laptop's IP
```

**Expected:** `ss` shows something listening on `0.0.0.0:3389` or `:::3389`.

### 1.4 Connect from the 3-monitor Windows PC

On the Windows PC, press `Win+R` and run:

```
mstsc /multimon /v:<laptop-ip>
```

Log in with the live user name and the password you set.

> If `mstsc` refuses the self-signed certificate, accept the warning. If it still fails, install FreeRDP for Windows instead and use:
> `wfreerdp /multimon /dynamic-resolution /gfx:AVC444 /u:ubuntu /v:<laptop-ip> /cert:ignore`

### 1.5 What to check — pass/fail

| # | Check | PASS | FAIL |
|---|---|---|---|
| 1 | Remote desktop spans all 3 monitors | Yes | One monitor only, or one stretched image |
| 2 | **Settings → Displays** inside the session | Shows **3 separate displays** in the right arrangement | Shows 1 display |
| 3 | Maximize a window on the middle monitor | Fills **only** that monitor | Fills all 3 |
| 4 | Drag a window between monitors | Snaps per monitor | Behaves like one big screen |
| 5 | Mouse crosses monitor edges | Smooth, no jump | Cursor jumps or sticks |
| 6 | Each monitor's resolution | Matches the real monitor | Wrong / all identical |
| 7 | Audio: play a YouTube video | Sound on the client | Silent |
| 8 | Clipboard: copy text both directions | Works | Does not |
| 9 | **Disconnect, wait 30s, reconnect** | Windows still open where you left them | Session gone / apps restarted |
| 10 | Unplug one monitor while connected | Session drops to 2 monitors, no disconnect | Connection drops |

**Hardware encoding check — run this on the laptop while connected:**

```
journalctl -u gnome-remote-desktop -b --no-pager | grep -iE 'vaapi|vulkan|hwaccel'
```

**PASS:** a line containing `Successfully initialized VAAPI` and `Intel iHD`.
**FAIL / degraded:** no VAAPI line → GNOME is using the CPU codec. The picture will still work but look worse. Note this and continue; it is not fatal here, because the laptop is not the final server.

**Visual quality check:** open a text editor with small text. Coloured fringes on letters mean the connection is using AVC420 (4:2:0 colour). Ask for `AVC444` with the FreeRDP client to compare.

### 1.6 Verdict

- **Checks 1–6 pass** → **the architecture is proven. Choose GNOME. Continue to Test 2.**
- **Check 2 shows only 1 display** → the whole multi-monitor premise fails. **Stop. Do not wipe anything.** Come back and we redesign.
- **Checks 1–6 pass but 9 fails** → session persistence is broken. Annoying, not fatal. Note it.

### 1.7 Cleanup / rollback

Shut down the laptop and remove the USB stick. **Nothing was written to the laptop.** The Windows PC only ran `mstsc`; you may delete the saved connection in Remote Desktop Connection history if you care.

---

# TEST 2 — Does your actual hardware do it?

**Question:** Does the 8700K have the virtualization features, clean IOMMU groups, and enough encoding power?

**Setup:** the 8700K, BIOS changes, then the same live USB. Windows stays installed and untouched.

**Time:** about 45 minutes.

### 2.1 Before you touch the BIOS — write down what is there now

Enter BIOS (`Del` at boot). Photograph or write down the **current** value of every setting in the table below, so you can put it back.

### 2.2 Change these settings

Names come from the sibling ROG STRIX Z390-E manual. **Your TUF board may word them differently** — look for the same meaning.

| Where | Setting | Set to |
|---|---|---|
| Advanced → CPU Configuration | **Intel (VMX) Virtualization Technology** | Enabled |
| Advanced → System Agent (SA) Configuration | **VT-d** | Enabled |
| Advanced → System Agent (SA) Configuration | **Above 4G Decoding** | Enabled |
| Advanced → SA → Graphics Configuration | **Primary Display** | **CPU Graphics** |
| Advanced → SA → Graphics Configuration | **iGPU Multi-Monitor** | Enabled |
| Boot → CSM | **Launch CSM** | Disabled |

> After setting **Primary Display = CPU Graphics**, your screen output comes from the **motherboard** HDMI/DisplayPort, not the RTX 2060. Move your monitor cable to the motherboard for this test, or you will see nothing.

Save and exit. Boot the live USB. Choose **"Try Ubuntu"**.

### 2.3 Verify virtualization

```
# a) CPU virtualization
grep -ow vmx /proc/cpuinfo | sort -u
```
**Expect:** `vmx` — **FAIL if empty.**

```
# b) VT-d / IOMMU present
ls /sys/firmware/acpi/tables | grep -x DMAR
sudo dmesg | grep -E 'DMAR|IOMMU' | head
ls /sys/kernel/iommu_groups | wc -l
```
**Expect:** `DMAR` printed · a line like `DMAR: IOMMU enabled` · group count **greater than 0**.
**FAIL if** no DMAR table → VT-d is still off in BIOS.

### 2.4 Verify both GPUs are alive

```
lspci -nnk | grep -A3 -E 'VGA|Display|3D'
ls -l /dev/dri/by-path/
```
**Expect:** two entries — Intel `UHD Graphics 630` at `00:02.0`, and NVIDIA `TU106` at `01:00.0` — and **two** `-render` nodes in `/dev/dri/by-path/`.
**FAIL if** only the NVIDIA card appears → `iGPU Multi-Monitor` is not enabled.

```
# Which card owns the boot screen (we want the Intel one)
cat /sys/bus/pci/devices/0000:00:02.0/boot_vga
cat /sys/bus/pci/devices/0000:01:00.0/boot_vga
```
**Expect:** `1` for the Intel one, `0` for the NVIDIA one. If reversed, `Primary Display` is not set to CPU Graphics.

### 2.5 Verify the RTX 2060 can be isolated

```
# Dump IOMMU groups
for g in $(find /sys/kernel/iommu_groups -mindepth 1 -maxdepth 1 | sort -V); do
  echo "Group ${g##*/}:"
  for d in $g/devices/*; do echo -e "\t$(lspci -nns ${d##*/})"; done
done
```
**Expect:** the group containing the NVIDIA VGA device and its HDMI audio device contains **nothing else** except PCI bridges / root ports.
**FAIL if** it shares a group with your network card, SATA controller, or USB controller → you would need the ACS override patch, which is a security compromise. Report back if this happens.

```
# Can the card be reset cleanly?
cat /sys/bus/pci/devices/0000:01:00.0/reset_method
sudo lspci -vvs 01:00.0 | grep -i FLReset
```
**Expect:** a reset method listed, and `FLReset+`. `FLReset-` is a yellow flag, not fatal.

### 2.6 Verify the 4096-pixel encoder limit on YOUR chip

```
sudo apt update
sudo apt install -y vainfo intel-media-va-driver ffmpeg

# Confirm the hardware limit directly
vainfo --display drm --device /dev/dri/by-path/pci-0000:00:02.0-render -a 2>/dev/null \
  | grep -A6 'VAProfileH264High.*VAEntrypointEncSlice'
```
**Expect:** `VAConfigAttribMaxPictureWidth : 4096` and `VAConfigAttribMaxPictureHeight : 4096`.

```
# Prove it the other way — this MUST fail
ffmpeg -hide_banner -init_hw_device vaapi=va:/dev/dri/by-path/pci-0000:00:02.0-render \
  -f lavfi -i testsrc2=s=5760x1080:r=30:d=1 -vf format=nv12,hwupload \
  -filter_hw_device va -c:v h264_vaapi -f null - 2>&1 | head -2
```
**Expect:** `Hardware does not support encoding at size 5760x1088 (constraints: width 32-4096 height 32-4096)`.
This confirms: **one combined picture for 3 monitors is impossible; separate pictures per monitor are required.** (Already verified in Intel's source — `CODEC_4K_MAX_PIC_WIDTH 4096` in `codec_def_common.h` — and reproduced on a different Intel chip.)

### 2.7 The encoding benchmark — the real pass/fail

```
# HuC firmware must be loaded for low-power encoding
cat /sys/module/i915/parameters/enable_guc
sudo dmesg | grep -iE 'huc|guc' | head
```
If it is not `2`, reboot the live USB, press `e` at the GRUB menu, append `i915.enable_guc=2` to the `linux` line, and press `F10`.

```
cd /tmp
R=/dev/dri/by-path/pci-0000:00:02.0-render

# 10 seconds of 1080p60 test video (~1.8 GB in RAM)
ffmpeg -v error -f lavfi -i testsrc2=size=1920x1080:rate=60 -t 10 \
  -pix_fmt nv12 -f rawvideo -y s.nv12

# THREE simultaneous encodes - one per monitor
for i in 1 2 3; do
 ( ffmpeg -nostdin -v error -stats -f rawvideo -pix_fmt nv12 -s 1920x1080 -r 60 -i s.nv12 \
     -vaapi_device $R -vf 'format=nv12,hwupload' \
     -c:v h264_vaapi -low_power 1 -rc_mode CBR -b:v 15M -bf 0 -g 60 -async_depth 1 -f null - 2>&1 \
   | tr '\r' '\n' | grep -E '^frame=' | tail -1 | sed "s/^/stream$i: /" ) &
done; wait

rm -f s.nv12
```

**Pass/fail — read the `speed=` value on each line:**

| Result | Meaning |
|---|---|
| **speed ≥ 1.5×** on all three | **PASS.** 3 monitors at 60 fps with headroom. |
| speed 1.0–1.5× | **MARGINAL.** 3 monitors will work but with no spare capacity. Plan for 2 monitors, or 3 at 30 fps. |
| speed < 1.0× | **FAIL for 3 monitors.** Design for 1–2 monitors on this chip. |
| `low_power` errors out | HuC not loaded. Re-run without `-low_power 1` and compare. |

**Reference numbers I measured on an Alder Lake Iris Xe** (a *newer, stronger* chip than yours): 3 streams at **2.7×**, or **2.8×** with low-power. Your UHD 630 is an older generation — expect roughly half. This is why the test exists.

### 2.8 Cleanup / rollback

Shut down, remove the USB. **Nothing was written to either SSD.**

**To put the BIOS back:** restore the settings you wrote down in 2.1, and move your monitor cable back to the RTX 2060.

**You can also leave the BIOS as-is** — Windows runs fine with virtualization enabled and the iGPU on. Only `Primary Display = CPU Graphics` changes which port drives your monitor.

---

# TEST 3 — Can GNOME run in a container with hardware encoding?

**Question:** Does the Work Realm work as a container sharing the iGPU, rather than a VM?

> **Correction to the earlier draft of this document.** The first version of Test 3 used Docker. **That was the wrong tool.** The thing most likely to fail in a Proxmox LXC is `systemd`, `systemd-logind` and `udev` — and a plain Docker container has none of them. Docker would fail for reasons that say nothing about Proxmox.
>
> **Use `systemd-nspawn` instead.** It is already installed on the laptop. It runs real systemd as PID 1 with real logind, on a shared kernel — the same shape as an LXC container. It is a genuine proxy; Docker was not.

**Setup:** the laptop. Everything lives in one scratch directory that you delete afterwards. Nothing is installed on the host.

**Needs:** `sudo` (containers need root to start), about **3 GB** of free disk, about 45 minutes.

### 3.1 Build a throwaway Ubuntu root filesystem

```
WORK=$HOME/celephais-test3
mkdir -p "$WORK/rootfs" && cd "$WORK"

# Full Ubuntu 26.04 server root filesystem — 268 MB. Includes systemd.
curl -LO https://cloud-images.ubuntu.com/releases/26.04/release/ubuntu-26.04-server-cloudimg-amd64-root.tar.xz
sudo tar -xpJf ubuntu-26.04-server-cloudimg-amd64-root.tar.xz -C rootfs
```

### 3.2 Install the software inside it (no systemd yet, just a shell)

```
sudo systemd-nspawn -D "$WORK/rootfs" --resolv-conf=copy-host bash
```
Inside the container:
```
apt update
DEBIAN_FRONTEND=noninteractive apt install -y \
  vainfo vulkan-tools intel-media-va-driver mesa-vulkan-drivers \
  gnome-shell gnome-session-bin gnome-remote-desktop dbus-x11 \
  systemd-container libpam-systemd

passwd root          # set any password; you need it to log in later
exit
```

### 3.3 Part A — does the GPU reach the container?

Boot it with systemd and the graphics device attached:

```
sudo systemd-nspawn -b -D "$WORK/rootfs" \
  --machine=celephais \
  --resolv-conf=copy-host \
  --bind=/dev/dri \
  --property=DeviceAllow='/dev/dri/renderD128 rwm'
```
Log in as `root` at the prompt, then:
```
# 1. Hardware H.264 encoder present?
vainfo --display drm --device /dev/dri/renderD128 -a 2>/dev/null \
  | grep -E 'Driver version|VAProfileH264High.*Enc'

# 2. Vulkan present? GNOME needs BOTH, on the SAME device.
vulkaninfo --summary 2>/dev/null | grep -iE 'driverName|deviceName|GPU id'
```

**PASS:**
- `vainfo` shows driver **iHD** and `VAProfileH264High : VAEntrypointEncSlice` and/or `VAEntrypointEncSliceLP`
- `vulkaninfo` shows an **Intel** device (`Intel open-source Mesa driver`, device ANV)

**FAIL:** either one missing → a container cannot drive the encoder. Go to §4 and plan for a VM with full iGPU passthrough.

### 3.4 Part B — does GNOME start headless inside it?

Still inside the container:
```
export XDG_RUNTIME_DIR=/run/user/0
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
export XDG_SESSION_TYPE=wayland

dbus-run-session -- gnome-shell --wayland --headless --virtual-monitor 1920x1080
```

**PASS:** it keeps running, does not exit, and does not say it fell back to software rendering.

**This is the step most likely to fail.** Read the error against this table:

| Symptom | Cause | Fix to try |
|---|---|---|
| "No GPU found", or software rendering | **udev.** Containers have an empty `/run/udev`, and GNOME discovers GPUs through it. | Exit, restart nspawn adding `--bind-ro=/run/udev`. If that fixes it, **we have also found the fix for Proxmox LXC.** |
| Exits immediately, logind/seat errors | **systemd-logind.** | Check `loginctl` works inside. Try `--capability=CAP_SYS_ADMIN`. |
| Starts, but VA-API never initializes | Vulkan and EGL landed on **different render nodes**. They must match. | Bind only the one render node, not all of `/dev/dri`. |

**The udev variant is the important experiment.** Run Part B twice — once without `--bind-ro=/run/udev` and once with it. The difference tells us exactly what a Proxmox container will need.

### 3.5 Part C — the real pass criterion

With gnome-shell running, open a second terminal on the laptop:
```
sudo machinectl shell celephais
```
Inside, start the RDP server and watch the log:
```
grdctl --headless rdp enable
grdctl --headless rdp set-credentials testuser testpass
systemctl --user start gnome-remote-desktop-headless.service 2>/dev/null || \
  gnome-remote-desktop-daemon --headless &

sleep 3
journalctl -b --no-pager | grep -iE 'vaapi|vulkan|hwaccel'
```

**PASS — the whole design is confirmed:**
```
Successfully initialized VAAPI ... vendor: Intel iHD
```

**PARTIAL:** GNOME runs but no VAAPI line → container works, hardware encoding does not. Usable, but quality drops to the CPU codec.

**FAIL:** GNOME will not start at all → go to §4, use a VM with GVT-d.

### 3.7 RESULTS — run 2026-10-03

Executed on the laptop (i7-12700H, Intel Iris Xe) using `systemd-nspawn`, 9 iterations.

**Every container-specific question passed.**

| # | Question | Result | Evidence |
|---|---|---|---|
| 1 | GPU render node reaches a container | **PASS** | `/dev/dri/renderD128` visible, iHD driver 26.1.2 |
| 2 | Hardware H.264 **encoder** present | **PASS** | `VAProfileH264High : VAEntrypointEncSliceLP` |
| 3 | Intel **Vulkan** present (grd needs it too) | **PASS** | `Intel(R) Iris(R) Xe Graphics`, Mesa ANV |
| 4 | mutter renders on the **GPU**, not software | **PASS** | `Created gbm renderer for '/dev/dri/renderD128'`, `Obtained a high priority EGL context` |
| 5 | **Virtual monitors** can be created | **PASS** | `Added virtual monitor Meta-0` |
| 6 | **udev** needed? | **NO** — hypothesis disproved | GPU found identically with and without `/run/udev` bind |
| 7 | GNOME Shell runs stably headless | **PASS** | `gnome-shell ALIVE after 20s` |
| 8 | systemd + logind + dbus | **PASS** | `PID 1: systemd`, `logind: active` |
| 9 | **GDM** runs in a container | **PASS** | `gdm: active` |
| 10 | **gnome-remote-desktop** runs and listens | **PASS** | `RDP server started`, `LISTEN *:3389` |
| 11 | Client **authenticates** | **PASS** | `Sending server redirection` (auth accepted) |
| 12 | **Graphics channel** negotiated | **PASS** | `Loading Dynamic Virtual Channel rdpgfx` |
| 13 | GDM creates a **login session** | **PASS** | `session closed for user gdm-greeter`, sessions c16/c17 |
| 14 | Session **handover** completes → encoder built | **NOT REACHED** | client times out after the redirect |

**Only step 14 is unproven, and it is not a container question.** System-mode grd authenticates the client and then *redirects* it to a per-user session daemon. That handover is a documented weak spot upstream — GNOME issues [#330](https://gitlab.gnome.org/GNOME/gnome-remote-desktop/-/issues/330) (black screen at remote login) and [#354](https://gitlab.gnome.org/GNOME/gnome-remote-desktop/-/issues/354) (assertion after aborted handover) are open against this exact flow. **Test 1 already demonstrated a working client session with multiple monitors on real hardware**, so the path itself is proven; what failed is this artificial harness, which has no TPM, no keyring, and a loopback client.

**Blockers hit along the way — all mine, none about containers:** no DNS in the container · a broken shell test that returned false on success · `systemd-nspawn` started without `-b` so no init existed · TLS cert placed in `/root`, which the service is sandboxed away from · `grdctl ... enable` needing a systemd *user* manager · missing `set-credentials` · invalid `/gfx:AVC444` syntax · unset `HOME` · daemon caching credentials until restarted.

**Verdict: build the Work Realm as an LXC container.** The GPU path — the thing nobody had published and the reason the container design existed — is proven end to end.

**Carry one open item into the migration:** the first thing to do after installing Proxmox, *before* migrating anything, is to stand up the realm container and complete one real RDP login with multiple monitors. If the handover misbehaves there too, the fallbacks in order are: (a) autologin plus `--headless` instead of Remote Login, (b) a newer grd from a PPA, (c) VM with GVT-d passthrough.

### 3.6 Cleanup / rollback

```
# stop the container if still running
sudo machinectl terminate celephais 2>/dev/null

# delete everything
sudo rm -rf "$HOME/celephais-test3"
```
That is the whole cleanup. Nothing was installed on the laptop and nothing outside that directory was touched.

---

# 4. Your question answered: LXC container or VM?

You asked whether a VM can reach the iGPU safely instead. **I checked every supported mechanism. The answer is no — except one, and it costs you the iGPU entirely.**

### Why the requirement is strict

Reading GNOME's source (`grd-hwaccel-vulkan.c`, `grd-hwaccel-vaapi.c`), the hardware encoder needs **all** of:
1. A **Vulkan** device with dma-buf, DRM-format-modifier and explicit-sync extensions
2. **VA-API** H.264 encode on **the same render node** as the Vulkan and EGL device
3. If any step fails, it silently drops to the CPU codec — there is **no H.264 software fallback**

So the realm needs a real Intel GPU, not an emulated one.

### Every option, checked

| Mechanism | VA-API encode in guest? | Host keeps iGPU? | Verdict |
|---|---|---|---|
| **LXC + `dev0: /dev/dri/renderD128`** | **Yes** — proven pattern (Jellyfin, Plex use it daily) | **Yes** — shared with host and other containers | **Recommended** |
| **VM + GVT-d full passthrough** | Yes — it is a real device | **No** — host and all containers lose it completely | Fallback only |
| VM + virtio-gpu **native context** | **No** | Yes | Rejected — see below |
| VM + **Venus** (Vulkan) | **No** — VA-API cannot open a virtio render node | Yes | Rejected — fails requirement 2 |
| VM + **SR-IOV** | n/a | n/a | **Not available on Coffee Lake.** Intel iGPU SR-IOV is 12th-gen and newer. |
| VM + **GVT-g** | n/a | n/a | **Dead.** Intel archived it Oct 2024 citing security escapes; broken since kernel ~6.8; your host runs 7.0. |
| VM + **virtio-media / virtio-video** | **No** | Yes | Not merged in QEMU. No usable path today. |
| VM + **VirGL video** | No | Yes | QEMU never passes the required flag; [issue #2196](https://gitlab.com/qemu-project/qemu/-/issues/2196) stalled since 2024. |

**On native context specifically** — this was the most promising candidate and I checked it carefully:
- The Intel native context **is merged** (virglrenderer 1.3.0, Feb 2026) and gives a guest near-real GPU access.
- But it covers **OpenGL and Vulkan only**. There is no VA-API path.
- It is marked experimental and was tested on Tiger Lake and newer — **Coffee Lake is untested**.
- Debian 13 (Proxmox 9's base) ships virglrenderer **1.1.0**; you would have to build 1.3.0 yourself.
- The only VA-API-over-native-context work that exists is an out-of-tree project for Intel **Xe** GPUs needing three patched components. Nothing for i915/Coffee Lake.

### Verdict

**Stay with LXC.** The only VM option that delivers hardware encoding is GVT-d, and it takes the iGPU away from the host and every other container — which breaks the architecture, not just a feature.

### Limitations of LXC you are accepting

| Limitation | Severity |
|---|---|
| **Shared kernel.** No separate guest kernel. A GPU driver crash can take down the host. | Real. Accept it. |
| **Weaker isolation** than a VM, even unprivileged. | Acceptable — it is your own desktop, not hostile code. |
| **No live migration** (restart-migrate only). | Irrelevant — single host. |
| **Device passthrough is not included in backups.** Bind-mount and device contents are skipped by `vzdump`. | Minor — re-add one config line after restore. |
| Needs `features: nesting=1` for systemd sandboxing and Flatpak. | Trivial. |
| **Session bring-up is the genuine unknown** — udev and logind inside a container, not the GPU. | This is what Test 3 measures. |

**Evidence found (encouraging, not conclusive):**
- KDE Plasma **Wayland** running in an unprivileged Proxmox LXC with `/dev/dri` passed in — [Proxmox forum](https://forum.proxmox.com/threads/gpu-accelerated-graphical-remote-desktop-with-support-for-nested-containers-and-flatpaks-in-unprivileged-lxc-containers.181245/)
- GNOME on X11 via xrdp in an unprivileged Debian 12 LXC with hardware acceleration — [pe0alx.nl](https://pe0alx.nl/2025/04/a-hardware-accelerated-gnome-workstation-running-in-a-container-on-proxmox/)
- **No published case of GNOME *Wayland* in a Proxmox LXC.** You would be first. That is the risk.

**Good news from the source:** mutter's headless backend explicitly does **not** need logind or a seat — it sets the session launcher to NULL in headless mode and opens DRM nodes with a plain `open()`. And `/dev/uinput` is not needed, because input arrives through the RemoteDesktop portal. Two of the three feared blockers are not blockers. **udev remains the real question.**

### If Test 3 fails

Fallback order:
1. Try **GDM + `grdctl --system`** inside the container instead of a manual headless session.
2. Try a **privileged** container (weaker security, often fixes logind).
3. Fall back to **VM + GVT-d**, and accept that the host loses the iGPU. The architecture still works; you just cannot share the chip.

---

# 5. Decision table — what to do with the results

| Test 1 | Test 2 | Test 3 | Decision |
|---|---|---|---|
| Pass | Pass | Pass | **Go.** Install Proxmox, build the realm as an LXC container. |
| Pass | Pass | Fail | **Go**, but build the realm as a VM with GVT-d. Host loses the iGPU. |
| Pass | Marginal | any | **Go**, but design for **2 monitors**, not 3. |
| Pass | Fail (encode) | any | **Go**, 1–2 monitors only. Revisit when hardware allows. |
| Pass | Fail (IOMMU) | any | **Stop and report.** Gaming VM may be impossible without a security compromise. |
| **Fail** | any | any | **STOP. Do not wipe.** The core premise is wrong. Redesign first. |

---

# 6. What we are NOT testing yet, and why

- **Tailscale / WAN latency** — only matters after the design is proven. Test on a LAN first; a LAN failure is a design failure, a WAN failure is a tuning problem.
- **The USB portable client** — nothing to carry until there is a realm to connect to.
- **Windows gaming VM** — Test 2 proves the hardware can do it. Building it comes after Proxmox exists.
- **Client-side decode load at 3×1080p60** — observed informally during Test 1. Measure properly later; FreeRDP's hardware decode is marked experimental and copies frames back to the CPU, so this may be the real bottleneck.
- **Storage layout** — a decision, not a test. See R4 in Phase A: do not mirror; Windows alone on one SSD, everything else on the other, backups to the old laptop.
