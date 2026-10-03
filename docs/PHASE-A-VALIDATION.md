# Celephaïs — Phase A: Architecture Validation

Date: 2026-10-02. Supersedes the stack choices in `ARCHITECTURE.md` (written for the earlier "Bifröst" scope, before multi-monitor was a requirement).

Research method: primary sources — KDE and GNOME git source, Microsoft protocol specs, QEMU/Linux source, Intel media-driver docs, Proxmox source and wiki, Intel ARK, NVIDIA support matrix, Launchpad package data, docs.rs.

**UNKNOWN — MUST TEST** marks anything not confirmed at a primary source.

---

## 0. Verdict first

**The project is feasible. Four of your choices are wrong, and one of them is fatal if you keep it.**

| # | Your assumption | Verdict | Replace with |
|---|---|---|---|
| 1 | Work Realm runs **KDE/Kubuntu** | **RED — fatal for multi-monitor** | **GNOME on Ubuntu 26.04** |
| 2 | One big framebuffer, split on the client | **RED — physically impossible on your iGPU** | Per-monitor surfaces inside **one** RDP session (RDP already works this way) |
| 3 | Work Realm is a **VM** | **RED for hardware encoding** | **LXC container** sharing `/dev/dri` |
| 4 | Sunshine/Moonlight may be the streaming layer | **RED for this use case** | RDP. Moonlight has no multi-monitor at all. |
| 5 | NoMachine is a good architectural model | **Reject** | Its virtual multi-monitor is a paid tier, built on its own X server, which dies with Wayland-only desktops |
| 6 | Proxmox is the base | **GREEN — keep it** | — |
| 7 | Write our own Rust client early | **YELLOW — premature** | Use FreeRDP's SDL3 client first |
| 8 | 2 × 250 GB SSD is enough | **RED — it is not** | See R4 in Task 13 |

**The single most important discovery:** the API you designed —
`configureDisplays([{width, height, x, y, scale, orientation}, ...])` —
**already exists as a published Microsoft protocol**: MS-RDPEDISP, the RDP Display Control Virtual Channel. The client sends exactly that list. The server rebuilds its monitors. FreeRDP implements the client half. GNOME implements the server half, for up to 16 monitors.

Celephaïs does not need to invent session negotiation. It needs to pick a server that implements MS-RDPEDISP properly, and orchestrate around it.

**The second most important discovery:** GNOME's hardware encoder has **no H.264 software fallback**. If VA-API fails to engage, it silently drops to RemoteFX Progressive on the CPU — a much worse experience. And VA-API needs a *real* Intel GPU with a working Vulkan driver. A plain VM with virtio-gpu gives software only. **This is what forces the container design.**

---

## 1. Proposed architecture

```
                    PHYSICAL PC  (i7-8700K, 64 GB)
                              |
                       Proxmox VE 9.2
         (host keeps UHD 630;  RTX 2060 bound to vfio-pci)
                              |
        +---------------------+----------------------+
        |                                            |
  LXC: work-realm                            VM: windows-gaming
  Ubuntu 26.04 + GNOME                       Windows 11
  gnome-remote-desktop 50.2  (RDP :3389)     RTX 2060 passthrough
  mode: --system (Remote Login via GDM)      physical monitors
  dev0: /dev/dri/renderD128                  local keyboard/mouse (evdev)
     -> real Intel GPU -> Vulkan -> VA-API           |
        |                                     (local use only,
        | RDP over Tailscale                   never streamed)
        v
  CELEPHAÏS CLIENT
  - enumerate physical monitors
  - start/stop realm via Proxmox API
  - spawn FreeRDP 3:  /multimon /dynamic-resolution /gfx:AVC444 /sound /clipboard
  - FreeRDP sends monitor layout -> GNOME builds matching virtual monitors
```

**What Celephaïs actually writes:** a launcher. Authentication, realm list, Proxmox API calls, spawning FreeRDP with the right arguments. Everything below that is existing software.

**What Celephaïs does NOT write:** hypervisor, VPN, codec, remote desktop protocol, filesystem, display-negotiation protocol, or a video client.

---

## TASK 1 — Hardware preflight

**Question:** Can this hardware run the design, and what must change in BIOS?

**Known / proven:**

| Item | Finding | Source |
|---|---|---|
| i7-8700K VT-x, VT-d, EPT | All **supported**. VT-d is present on this K-SKU. | [Intel ARK](https://www.intel.com/content/www/us/en/products/sku/126684/intel-core-i78700k-processor-12m-cache-up-to-4-70-ghz/specifications.html) |
| Firmware currently | Virtualization **off**. Must be enabled. | Your Windows report |
| UHD 630 H.264 encode | Yes — low-power (VDEnc) and shader-assisted | [intel/media-driver](https://github.com/intel/media-driver) |
| UHD 630 HEVC encode | Only in the **non-free** "full-feature" driver build | same |
| UHD 630 AV1 | **None**, encode or decode | same |
| **UHD 630 max AVC encode size** | **width ≤ 4096 AND height ≤ 4096**, checked **per dimension**, not by area — VERIFIED, see below | [media_features.md](https://github.com/intel/media-driver/blob/2ad1459d2f80af7b034499238c0022ff00652b70/docs/media_features.md) + `codec_def_common.h:100-101` |
| RTX 2060 NVENC | Turing. H.264 + HEVC. No AV1 encode. 12 concurrent sessions. | [NVIDIA matrix](https://developer.nvidia.com/video-encode-and-decode-gpu-support-matrix-new) |
| RTX 2060 VFIO | NVIDIA enabled GeForce-in-VM in driver R465 (Mar 2021). No `kvm hidden` needed. | [Arch wiki](https://wiki.archlinux.org/title/PCI_passthrough_via_OVMF) |
| Turing reset bug | No systemic bug found (that is an AMD Navi problem) | — |
| PCIe lanes | 16 CPU lanes; both x16 slots are CPU lanes (x16, or x8/x8) | [Board manual](https://dlcdnets.asus.com/pub/ASUS/mb/LGA1151/TUF_Z390M-PRO_GAMING_WI-FI/E15012_TUF_Z390M-PRO_GAMING_Wi-Fi_UM_V2_WEB.pdf) |

**The 4096 px limit is the most consequential hardware fact in this document — and it is now VERIFIED three ways.**

1. Intel's driver docs: the H.264 encode "Max Res." row reads `4k` for the KBLx column, whose footnote names CFL (Coffee Lake). [Permalink](https://github.com/intel/media-driver/blob/2ad1459d2f80af7b034499238c0022ff00652b70/docs/media_features.md)
2. Intel's driver source: `#define CODEC_4K_MAX_PIC_WIDTH 4096` and `CODEC_4K_MAX_PIC_HEIGHT 4096` in `media_common/agnostic/common/codec/shared/codec_def_common.h:100-101`. `MediaLibvaCaps::CheckEncodeResolution()` compares **width and height independently**. Coffee Lake uses this path via `MediaLibvaCapsG9Cfl : public MediaLibvaCapsG9`.
3. Reproduced on real Intel hardware (Alder Lake, same code path): `ffmpeg` rejects 5760×1080 with `constraints: width 32-4096 height 32-4096`.

**The limit is per side, not by area.** 5760×1080 has *fewer* total pixels than 4096×4096 and still fails, because 5760 > 4096.

| Layout | Hardware-encodable? |
|---|---|
| Three 1080p monitors as ONE 5760×1080 image | **No** |
| Three 1080p monitors as THREE separate images | **Yes** |
| One 3840×2160 (4K) monitor | Yes |
| One 5120×1440 ultrawide monitor | **No** — a future purchase to avoid |

**A combined framebuffer cannot be hardware-encoded on this machine.** Per-monitor encoding is mandatory — which is what RDP and GNOME already do.

**BIOS settings required.** Names come from the sibling ROG STRIX Z390-E manual; the TUF manual only shows screenshots, so **UNKNOWN — MUST VERIFY on your board**:

| Path | Set to | Why |
|---|---|---|
| Advanced → CPU Configuration → **Intel (VMX) Virtualization Technology** | Enabled | KVM |
| Advanced → System Agent (SA) Configuration → **VT-d** | Enabled | IOMMU / passthrough |
| Advanced → SA Configuration → **Above 4G Decoding** | Enabled | Large BARs |
| Advanced → SA → Graphics Configuration → **Primary Display** | **CPU Graphics** | Keeps the boot framebuffer off the RTX 2060 |
| Advanced → SA → Graphics Configuration → **iGPU Multi-Monitor** | Enabled | Keeps UHD 630 alive alongside the RTX 2060 |
| Boot → CSM → **Launch CSM** | Disabled | UEFI/OVMF |

Setting **Primary Display = CPU Graphics** is the elegant part: the RTX 2060 is never claimed by the boot framebuffer, so you do **not** need `video=efifb:off` or `initcall_blacklist=sysfb_init`. Clean passthrough.

**Uncertain:**
- Exact BIOS labels on the TUF Z390M-PRO. **UNKNOWN — MUST TEST.**
- IOMMU grouping on this board. A similar Z390 board groups the GPU with CPU root ports, which is fine for a single GPU. **Keep the second x16 slot empty.** **UNKNOWN — MUST TEST.**
- Whether the UHD 630 sustains 3 × 1080p60 encode (~180 fps aggregate). **UNKNOWN — MUST BENCHMARK.**

**Limitations:**
- `h264_qsv` needs the legacy `libmfx1`, which is **not** in Debian 13 (Proxmox 9's base) or Ubuntu 26.04. **Use `h264_vaapi`.**
- Low-power H.264 with CBR/VBR needs HuC firmware, off by default on Coffee Lake. Needs `i915.enable_guc=2`.

**Prototype required: YES.** All checks run from a Linux live USB; nothing is written to disk.

**Pass/fail:** `vmx` present · `DMAR` ACPI table present · IOMMU groups > 0 · both GPUs with separate `/dev/dri` nodes · RTX 2060 alone in its IOMMU group apart from root-port bridges · 3 parallel `h264_vaapi` 1080p60 encodes each at **≥ 1.5× realtime**.

---

## TASK 2 — Proxmox vs alternatives

**Question:** Is Proxmox the right base?  **Answer: yes. Keep it.** This was the correct call.

**Known:**
- **Proxmox VE 9.2** (2026-05-21): Debian 13 "Trixie", kernel 7.0, QEMU 11.0, LXC 7.0, ZFS 2.4. Three minor releases in; safe. [Roadmap](https://pve.proxmox.com/wiki/Roadmap)
- Real REST API, per-path ACLs, privilege-separated tokens. Start needs `VM.PowerMgmt`; snapshot needs `VM.Snapshot`. [Source](https://git.proxmox.com/?p=qemu-server.git;a=blob;f=src/PVE/API2/Qemu.pm)
- libvirt has **no REST API** — you would build that layer yourself. That alone decides it.
- Proxmox does not block raw QEMU; the `args:` option is appended unvalidated.

**Limitations that matter to Celephaïs:**
- **Only the literal user `root@pam` can set `args:`.** API tokens are rejected, even root's own. Same for non-mapped `hostpci` and LXC `dev0`.
  **Workaround:** configure those once as root. Then give Celephaïs a token with only `VM.Audit` + `VM.PowerMgmt` + `VM.Snapshot`, scoped per guest. Starting is not re-checked, so this works cleanly and keeps the client's credentials weak.
- `max_outputs` for virtio-gpu is not exposed (QXL only). Not needed in our design.
- RAM snapshots are blocked on passthrough VMs; disk snapshots work.
- Known 9.2 bug: Windows 11 + Intel + `cpu: host` + VBS can freeze. Fix: machine version `11.0+pve2` or newer.

**Recommended:** Proxmox VE 9.2, ZFS, API token scoped per guest.
**Alternative rejected:** Debian + libvirt — no REST API; you build and maintain more.
**Prototype:** optional. Proxmox installs nested inside a VM to rehearse install, ZFS layout, tokens and LXC. Passthrough cannot be tested nested.

---

## TASK 3 — Virtual monitor architecture  ⭐ the decisive task

**Question:** How does a remote Linux desktop expose N virtual monitors matching the client?

### The protocol layer — solved, do not build this

[MS-RDPEDISP](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpedisp/ea2de591-9203-42cd-9908-be7a55237d1c):
- Server advertises `MaxNumMonitors`.
- Client sends `DISPLAYCONTROL_MONITOR_LAYOUT_PDU`: per monitor, a primary flag, signed Left/Top relative to primary, width/height (200–8192, width even), physical size in mm, orientation (0/90/180/270), DesktopScale (100–500%), DeviceScale (100/140/180).
- Monitors are also sent at connect time in the GCC data ([MS-RDPBCGR](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpbcgr/8fb3a83c-f3e2-4a81-8824-8173af03b6bc)).
- [MS-RDPEGFX RESET_GRAPHICS](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-rdpegfx/60c8841c-3288-473b-82c3-340e24f51f98) carries a monitor array of up to 16.

**Windows RDP creates one surface per monitor, each with its own codec context** ([FreeRDP developer write-up](https://www.hardening-consulting.com/en/posts/20170302-multi-monitor-rdp.html)). GNOME does the same — each monitor gets its own encoder surface and its own VA-API session. Per-monitor encoding inside a single session is simply how RDP works, and it fits the UHD 630's 4096 px limit exactly.

### The server layer — this is where KDE fails

| Server | Multi-monitor headless | HW encode | Verdict |
|---|---|---|---|
| **gnome-remote-desktop** | **YES — 16 monitors.** Builds virtual monitors from the client layout; screen-share mode is forced to EXTEND in headless/system mode. | NVENC, then VA-API | **Use this** |
| **KRDP** (KDE) | **NO. `MaxNumMonitors = 1`**, and it rejects any layout PDU with `NumMonitors != 1`. True in Plasma 6.8 and master. | VA-API (AVC420 only) | Cannot meet the requirement |
| **xrdp 0.10 + xorgxrdp** | YES, with hotplug | **No** — software x264/OpenH264 only | X11 only; dead end |
| **Selkies 2.0** | Per-display pages, real RandR outputs | VA-API | X11 only |

Sources: [grd-session-rdp.c](https://gitlab.gnome.org/GNOME/gnome-remote-desktop/-/blob/main/src/grd-session-rdp.c), [grd-rdp-monitor-config.h](https://gitlab.gnome.org/GNOME/gnome-remote-desktop/-/blob/main/src/grd-rdp-monitor-config.h), [KRDP DisplayControl.cpp](https://invent.kde.org/plasma/krdp/-/blob/master/src/DisplayControl.cpp), [krdp issue #30](https://invent.kde.org/plasma/krdp/-/work_items/30), [xrdp 0.10.0](https://github.com/neutrinolabs/xrdp/releases/tag/v0.10.0).

### Constraints GNOME imposes on the layout

Celephaïs must respect these when it builds a layout:
- Maximum **16** monitors.
- Each monitor between **200 and 8192 px** per side.
- Monitors **must not overlap**.
- The **primary monitor must be at (0,0)**.
- The client must support `DesktopResize` and the dynamic virtual channel (`DRDYNVC`). FreeRDP does.

### Two honest caveats

1. **Orientation is parsed and then ignored.** I grepped the whole GNOME source; nothing consumes it. **Portrait monitors still work** — the client sends width and height already swapped, so GNOME sees a tall rectangle and is happy. You just do not get orientation hints inside the session. For your use case this is fine.
2. **Scale is a *preference*, not a command.** It is sanitized to 100–500% and passed to mutter as the PipeWire tag `org.gnome.preferred-scale`. What mutter actually picks is **UNKNOWN — MUST TEST**. HiDPI support only exists from grd 50.

### Why "just use KDE on X11" is a trap

Plasma **6.7 is the last release with an X11 session**; 6.8 (2026-10-14) is Wayland-only ([announcement](https://www.phoronix.com/news/KDE-Plasma-68-Wayland-Exclusive)). Every X11-based path — xrdp, Selkies, NoMachine's embedded X server — expires with it.

### KWin has the pieces; nobody wired them together

If you later insist on KDE:
- KWin **has** runtime virtual outputs via `zkde_screencast_unstable_v1` → `stream_virtual_output(name, width, height, scale, pointer)` ([protocol](https://invent.kde.org/libraries/plasma-wayland-protocols/-/blob/master/src/protocols/zkde-screencast-unstable-v1.xml)). Each output lives as long as its PipeWire stream.
- Outputs resize, move, rotate and rescale at runtime with `kscreen-doctor output.<name>.mode.<WxH@R> position.<x>,<y> scale.<f> rotation.<left|right>`, plus `addCustomMode.<w>.<h>.<mHz>`.
- There is **no** `org.kde.KWin.VirtualOutputs` DBus API. The screencast protocol is the only runtime path, and it is in KWin's restricted-interface list.
- A one-person fork, [westers/krdp](https://github.com/westers/krdp), claims one KWin virtual output per client monitor. 2 stars, no declared licence. **Proof the idea works; not a dependency.**

**Recommended:** GNOME + gnome-remote-desktop, `--system` mode.
**Alternative (keeps KDE, costs months):** extend KRDP to N monitors — FreeRDP server lib + `stream_virtual_output` per monitor + PipeWire capture per output + VA-API encode per output. It is an extension of code that already exists for one monitor, not a from-scratch build. A legitimate "Celephaïs Core" differentiator if you want one. **Not v0.1.**
**Prototype required: YES — this is experiment #1.**

---

## TASK 4 — Client monitor detection

**Question:** How does a portable client enumerate displays on Windows, X11 and Wayland without admin rights?

**Answer: it does not need to for v0.1 — FreeRDP's SDL3 client already does it.** It sends per-monitor physical size, orientation and scale, and reacts to display hotplug ([sdl_disp.cpp](https://github.com/FreeRDP/FreeRDP/blob/master/client/SDL/SDL3/sdl_disp.cpp)). This section is for when Celephaïs replaces that client.

**No admin or root is needed anywhere.**

| Platform | What to call | Gap |
|---|---|---|
| **Windows** | `EnumDisplayMonitors` + `GetMonitorInfoEx` (rect, primary) · `EnumDisplaySettings`→`DEVMODE` (position, pixels, orientation, integer Hz) · `GetDpiForMonitor(MDT_EFFECTIVE_DPI)/96` (scale) · `QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS)` (rotation, rational refresh, friendly name) | DPI is only truthful if the process is **per-monitor-DPI-aware**. Requires a `<dpiAwareness>PerMonitorV2</dpiAwareness>` manifest, applied before the first window. SDL3 and winit 0.30 set this themselves. `QueryDisplayConfig` returns `ERROR_ACCESS_DENIED` without console-session access. DEVMODE rotation is documented counter-clockwise, QueryDisplayConfig clockwise — **UNKNOWN — MUST TEST on a rotated panel.** |
| **Linux / X11** | RandR 1.5 `GetMonitors` (name, primary, geometry, mm) · `GetCrtcInfo` (rotation) · refresh = `dot_clock / (htotal × vtotal)` | **X11 has no per-monitor scale.** Only a global `Xft.dpi`. This is a protocol limitation, not a library gap. |
| **Linux / Wayland** | Bind `wl_output` (v4: transform, mm, mode, integer scale, name) + `zxdg_output_manager_v1` (logical position/size). No window or surface needed. Fractional scale = mode size ÷ logical size. | **No "primary" concept in core Wayland.** Get it from `org.gnome.Mutter.DisplayConfig.GetCurrentState` on GNOME, or `kde-output-order-v1` / `kscreen-doctor -j` on KDE. Binding some KDE interfaces may require allowlisting — **UNKNOWN — MUST TEST.** |

**Crate recommendation: SDL3** (`sdl3` 0.20.0 over `sdl3-sys` 0.7.1, Zlib licence, static linking available). It is the only option that supplies **every** field you listed — bounds, content scale, orientation, refresh rate, primary — **and** emits hotplug events (`DISPLAY_ADDED/REMOVED/MOVED/ORIENTATION/CONTENT_SCALE_CHANGED`).

**Rejected:**
- `winit` 0.30.13 — no orientation, no primary on Wayland, **no monitor hotplug events at all** (confirmed by source grep; issues #3258, #3405 open). Workaround would be polling `available_monitors()` at ~1 Hz.
- `display-info` 0.5.9 — good on Windows and X11, but on Wayland `is_primary` is always false and the scale maths looks wrong. **UNKNOWN — MUST TEST.**
- `monitor-info` — does not exist.

**Prototype required:** not for v0.1. When we write our own client, a 50-line SDL3 enumeration dump on all three platforms is the test.

---

## TASK 5 — Linux desktop rendering

**Question:** How does the remote desktop render and encode with no GPU of its own?

**This is where the container decision is forced.**

GNOME's encoder chain is, in order: **NVENC → VA-API (via Vulkan) → RemoteFX Progressive on the CPU.** There is **no x264 and no openh264**. If VA-API does not engage, you do not get slower H.264 — you get a different, worse codec.

For VA-API to engage, **all** of these must hold:
- mutter renders on a real Intel GPU and produces dma-bufs
- a working **Vulkan** device (Intel ANV) exists in the grd process
- sync objects are available
- the buffer carries a valid modifier
- the client advertises AVC support

**A plain VM with virtio-gpu satisfies none of this.** It renders in software (llvmpipe) and falls to the CPU path. So "Work Realm = VM" quietly costs you hardware encoding.

**An LXC container does satisfy it:**
- Proxmox ≥ 8.1 needs one line: `dev0: /dev/dri/renderD128,gid=<render gid in CT>` ([forum](https://forum.proxmox.com/threads/solved-igpu-passthrough-into-unprivileged-lxc.158325/))
- Render nodes are multi-open, so the host, the work realm and any future container share the iGPU at once. No partitioning, no mdev, no licence.
- `card0` is not needed; it is only required to be KMS master.

**Mutter headless needs no GPU, no seat, no VT and no `/dev/dri`** to *run* (it falls back to EGL surfaceless + llvmpipe). It needs the GPU only to be *fast*.

**Uncertain:**
- **A full GNOME session with GDM inside an unprivileged Proxmox LXC container is unproven.** Secondary reports confirm headless VMs (Fedora Server, Ubuntu); nothing on LXC/Incus/nspawn. Likely friction: GDM and logind inside the container, systemd user services. **UNKNOWN — MUST TEST. This is experiment #3 and the main risk in the design.**

**Fallbacks if the container fails, in order:**
1. **VM + full iGPU passthrough (GVT-d) to a Linux guest.** QEMU "legacy mode" covers Coffee Lake. Cost: the host loses the iGPU entirely — no console, no sharing. Note Proxmox 9 users report **Code 43 passing UHD 630 to Windows guests**; Linux guests reportedly work ([thread](https://forum.proxmox.com/threads/pve-9-0-4-igpu-passthrough-error-43.169951/)).
2. **VM with software encoding.** Works, but you get RemoteFX Progressive on the CPU, not H.264. Acceptable at 1 monitor; poor at 3.

**Rejected: Intel GVT-g.** Intel archived the project on 2024-10-03 citing "known security escapes"; the kernel lists it "Odd Fixes"; a Coffee Lake user measured 58–82% slower transcoding plus kernel panics ([repo](https://github.com/intel/gvt-linux), [report](https://blog.ktz.me/why-i-stopped-using-intel-gvt-g-on-proxmox/)). **Do not use it.**

---

## TASK 6 — Display capture

**Question:** How are the virtual displays captured?

**Answer: you do not choose. The RDP server does.** GNOME captures through mutter and PipeWire, one stream per virtual monitor, with dma-buf zero-copy into the encoder when the GPU path is available. There is no separate capture layer for Celephaïs to design.

Worth knowing:
- **RDP is damage-driven.** It encodes changed regions, not full frames at a fixed rate. GNOME has a 64×64-tile damage detector in tree. An idle monitor costs almost nothing.
- Sunshine captures and encodes the entire framebuffer at a constant rate, because it is built for games. For a desktop — where most pixels are static most of the time — that is pure waste.

**Prototype:** none separately; covered by experiment #1.

---

## TASK 7 — Video encoding

**Question:** Can the UHD 630 encode the Work Realm, given the RTX 2060 belongs to the gaming VM?

**Codecs GNOME advertises:** AVC420 and **AVC444v2** (H.264) plus RemoteFX Progressive. AVC444 matters: 4:2:0 chroma puts colour fringes on small text, and AVC444 fixes it at higher bitrate ([Microsoft](https://learn.microsoft.com/en-us/azure/virtual-desktop/graphics-encoding)). For an IDE and terminals, ask for AVC444.

**Known:**
- UHD 630 does H.264 encode in low-power and shader-assisted modes, with no documented concurrent-session cap (unlike NVIDIA).
- **Hard ceiling: 4096 × 4096 per encoded surface** → per-monitor encoding is mandatory. GNOME already allocates one encoder session per monitor.
- Needs `i915.enable_guc=2` for HuC, or CBR/VBR low-power encoding fails.
- Use `h264_vaapi`; `h264_qsv` is unavailable on Debian 13 / Ubuntu 26.04.
- RTX 2060: 12 concurrent NVENC sessions, H.264 + HEVC, no AV1. Correctly excluded from the Work Realm design.

**Uncertain:**
- 3 × 1080p60 (≈180 fps aggregate) on Gen9.5. No first-party figure exists. One unverified community figure suggests ~221 fps for 1080p H.264 — passing, with little margin. **UNKNOWN — MUST BENCHMARK.** Cheap; run it first.
- **Open bug, directly relevant:** [grd issue #362](https://gitlab.gnome.org/GNOME/gnome-remote-desktop/-/issues/) reports VA-API H.264 **corruption on Ubuntu 26.04.1 with grd 50.2**, self-correcting on reconnect. Still open. Must be reproduced or ruled out in experiment #1.

**Bandwidth** (not the bottleneck): KRDP's own quality anchors are 3.1 Mbps at 1080p ([VideoStream.cpp](https://invent.kde.org/plasma/krdp/-/blob/master/src/VideoStream.cpp)) → roughly **10 Mbps for 3 × 1080p** of normal desktop work. Provision 15–25 Mbps average, 40–60 Mbps burst. Compare Moonlight's default of 20 Mbps *per stream* — tuned for games, wasteful here.

---

## TASK 8 — Transport / remote desktop stack

**Question:** Does something already solve 80–90% of this?

**Answer: yes. GNOME Remote Desktop headless + FreeRDP 3. Do not reinvent it.**

**Rejected, with reasons:**

| Candidate | Why rejected |
|---|---|
| **Sunshine / Moonlight** | One display per Sunshine instance; multi-monitor means N instances on N ports, which Sunshine's own docs call "not advised". Moonlight has **no multi-monitor support at all** — [issue #1904](https://github.com/moonlight-stream/moonlight-qt/issues/1904) open since Jun 2026, no maintainer reply; last release Sep 2024. Independent streams mean no cursor crossing between monitors, no shared clipboard, no client-driven topology. Right tool for games, wrong tool for a desktop. |
| **NoMachine** | Client-monitors-as-virtual-displays is **paid-tier only** ([forum](https://forum.nomachine.com/topic/are-multiple-monitors-supported-when-remote-server-has-no-monitors)). Runs its own embedded X server, which cannot host a Wayland-only desktop. Closed source. |
| **SPICE** | Multiple heads work, but it requires a QEMU guest (killing the LXC design) and its video encoding is mostly software. |
| **RustDesk** | Streams the host's **physical** monitors; client-driven topology unknown; Wayland multi-monitor still preview. |
| **Parsec** | [Linux hosting unsupported](https://support.parsec.app/hc/en-us/articles/32381568346644-Hardware-and-Software-Compatibility). |
| **waypipe** | Forwards individual windows, not a desktop. No reconnect. |
| **wayvnc / KasmVNC / Guacamole** | wlroots-only / X11-only / RDP multimon still an unreleased PR. |

**Client:** FreeRDP 3.32 (Apache-2.0), available as `freerdp3-sdl` in Ubuntu 26.04.
`/multimon` · `/dynamic-resolution` · `/gfx:AVC444` · `/sound` · `/clipboard` · `/auto-reconnect`

**Client-side weakness to watch:** FreeRDP's hardware decode is FFmpeg hwaccel marked *experimental*, and it copies frames back to CPU memory (`av_hwframe_transfer_data`) — no zero-copy ([h264_ffmpeg.c](https://github.com/FreeRDP/FreeRDP/blob/master/libfreerdp/codec/h264_ffmpeg.c)). Some distro builds ship without H.264 entirely, which is slow on 3 monitors ([#13105](https://github.com/FreeRDP/FreeRDP/issues/13105)). **Decoding 3 × 1080p60 on a thin client may be the real bottleneck, not encoding.** **UNKNOWN — MUST TEST.**

**Also missing:** GNOME has **no local drive redirection** ([issue #292](https://gitlab.gnome.org/GNOME/gnome-remote-desktop/-/issues/292)). Clipboard carries text and images. Audio out is AAC/Opus; audio in exists; camera redirection arrived in 50. For moving files, use the shared server-side storage, not the RDP channel.

**Latency budget** (component sums, LAN, 60 fps): capture/vsync 8–16 ms + VA-API encode 3–8 ms + network 1–3 ms + client decode 2–6 ms + present 8–16 ms, plus RDP's frame-ack window of 1–2 frames → **≈ 35–70 ms** for RDP/GFX, versus ≈ 20–50 ms for Sunshine. RDP runs over TCP, so loss on a WAN causes head-of-line stalls; Sunshine uses UDP with FEC. **On a LAN this does not matter. On a poor WAN it will.**

---

## TASK 9 — Client video / display presentation

**Question:** How does the client decode and place streams on physical monitors?

**For v0.1: FreeRDP's SDL3 client does all of it.** Celephaïs spawns it. Writing our own presentation layer is deferred until FreeRDP is measurably insufficient.

For later, the findings:
- **Fullscreen on a chosen monitor works on all three platforms**, using borderless (never exclusive). Windows: `SetWindowPos` to the monitor rect. X11: move to the monitor origin, then `_NET_WM_STATE_FULLSCREEN` (WM-dependent). Wayland: `xdg_toplevel.set_fullscreen(wl_output)` — note the spec calls the output a *preference*, so compositor compliance on GNOME/KDE/sway is **UNKNOWN — MUST TEST**.
- **Wayland does not let a client position a window.** Fullscreen-on-output is the only mechanism. This is fine for us, because every window is fullscreen anyway.
- On Wayland, read the **window's** scale factor, not the monitor's.
- **Decode:** no Rust crate offers a safe hardware-decode API. `ffmpeg-next` 9.0.0 is maintenance-only and needs `unsafe` FFI (`av_hwdevice_ctx_create`, `get_format`). Realistic plan: FFmpeg with hwaccel (D3D11VA on Windows — needs only OS DLLs; VA-API on Linux — needs system libva), software fallback, upload NV12 to an SDL or wgpu texture. Ship the FFmpeg libraries next to the executable with `rpath $ORIGIN`.
- **Input capture:** SDL3 uses a low-level keyboard hook on Windows (no admin) and `zwp_keyboard_shortcuts_inhibit_v1` on Wayland (supported by KWin, Mutter, Sway). Mouse via pointer-constraints + relative-pointer.
- **Never capturable from user mode:** `Ctrl+Alt+Del` anywhere; `Win+L` on Windows (hooks reportedly cannot block it; Microsoft's admin-configured Keyboard Filter is the only documented way). On X11, `Ctrl+Alt+F<n>` and `Ctrl+Alt+Backspace` stay with the X server unless `DontVTSwitch`/`DontZap` are set.
- **Hotplug:** SDL3 emits display add/remove events. `winit` emits none.

---

## TASK 10 — Dynamic session negotiation

**Question:** What protocol handles connect / disconnect / monitor changes?

**Answer: MS-RDPEDISP. Specified and implemented on both sides. Do not design one.**

| Event | What happens | VM reboot? |
|---|---|---|
| Client connects | Monitor rectangles arrive in the GCC data; GNOME builds matching virtual monitors immediately (headless mode is forced to EXTEND) | No |
| Resolution changes | Client sends `DISPLAYCONTROL_MONITOR_LAYOUT_PDU`; a layout state machine applies it | No |
| Monitor plugged in / unplugged / lid closed | Same PDU, more or fewer entries. The connection is **not** dropped. | No |
| Client disconnects | Session persists with apps running — **GNOME 47+ only**. Ubuntu 26.04 has it. | No |
| Client reconnects | Fresh layout; monitors rebuilt; windows return | No |
| A second device connects | One session per user. Concurrent multi-user headless is **not** solved. **UNKNOWN — MUST TEST** | — |

**Gap in the spec:** there is **no server-initiated layout request**. The client always drives. That matches your design, but it means the server cannot ask the client to change.

**What Celephaïs adds on top:** realm lifecycle (is it running? start it; wait for port 3389), authentication, realm selection. A thin orchestration layer — exactly the right amount of custom code.

---

## TASK 11 — Prior art: what not to re-solve

| Already solved by others | Lesson |
|---|---|
| Client-driven display topology as a wire protocol | MS-RDPEDISP. Use it. |
| Per-monitor surfaces with independent codec contexts in one session | Windows RDP since ~2017; GNOME today. Copy the shape. |
| Damage-driven encoding for desktop content | RDP. Never encode static pixels. |
| Virtual output creation on a Wayland compositor | mutter (16 monitors headless), KWin (`stream_virtual_output`). |
| The server owns the topology; output hotplug is the mechanism | NoMachine, xrdp and GNOME all converge here. |
| Virtual-desktop mode is distinct from mirroring a physical desktop | Every product separates them. Celephaïs needs only the virtual one. |

**The honest summary:** almost all of the hard part is solved, by GNOME. Celephaïs's value is the *portal* — USB portability, realm lifecycle, identity, and a one-keypress path from a strange computer into your environment. Not the display pipeline.

---

## TASK 12 — Local Windows gaming VM

**Known / proven:**
- RTX 2060 passthrough is routine on this hardware class. NVIDIA removed the VM block in R465 (2021).
- Pass the whole device, both functions: `hostpci0: 0000:01:00,pcie=1`. Video and HDMI audio are the same PCI device and IOMMU group.
- `/etc/modprobe.d/vfio.conf`: `options vfio-pci ids=10de:1f08,10de:10f9` and `softdep nouveau pre: vfio-pci`, then `update-initramfs -u -k all`.
- With Primary Display = CPU Graphics, no `video=efifb:off` is needed.
- Q35, OVMF/UEFI, `cpu: host`, virtio-scsi-single with `iothread=1` and `discard=on`.
- **`balloon: 0` is required.** Passthrough VMs need all RAM pinned; Proxmox adds a balloon device unless told not to ([wiki](https://pve.proxmox.com/wiki/Dynamic_Memory_Management)).

**Limitations — state these plainly:**
- **Anti-cheat.** Some kernel-level anti-cheat systems detect and block VMs. Not fixable from our side and not a bug. Assume some titles will not run.
- RAM snapshots are impossible on a passthrough VM. Disk snapshots work.
- Proxmox 9.2 + Intel + `cpu: host` + Windows VBS can freeze → machine version `11.0+pve2` or newer.
- Keyboard and mouse: pass a USB controller, pass individual USB devices, or use QEMU evdev input with a hotkey. **evdev is the practical choice** — no spare controller needed and it switches between host and guest.

**Resource plan** (6C/12T, 64 GB):
- Windows: 8 vCPUs pinned via `affinity` to 4 physical cores (check `lscpu -e` for sibling layout), 24–32 GB fixed, `balloon: 0`.
- Work realm LXC: a disjoint cpuset on the other 2 cores, 16–24 GB cgroup limit.
- Host: ~8 GB. ZFS ARC defaults to 10% capped at 16 GiB (≈6.4 GB here) — fine; cap explicitly if you want.
- Skip hugepages and `isolcpus` initially. Add only if you measure stutter.

**Uncertain:** TU106 function-level reset behaviour on this board. **UNKNOWN — MUST TEST** from the live USB.

---

## TASK 13 — Blockers: GREEN / YELLOW / RED

### RED — must change the design

| # | Finding | Action |
|---|---|---|
| R1 | **KDE cannot do client-driven multi-monitor.** KRDP is hard-coded to 1 monitor in every release including master. | Switch the Work Realm to **GNOME**, or accept 1 monitor, or commit months to building the server. |
| R2 | **A combined framebuffer cannot be hardware-encoded.** 5560–5760 px wide vs the UHD 630's 4096 px ceiling. | Per-monitor surfaces. RDP and GNOME already work this way. |
| R3 | **Every X11-based option expires.** Plasma 6.8 (2026-10-14) is Wayland-only; GNOME already is. | Do not build on xrdp, Selkies or NoMachine. |
| R4 | **Storage is undersized.** 2 × 250 GB mirrored ≈ 230 GB usable for host + Linux desktop + Windows 11 + games. | See below. |
| R5 | **Moonlight has no multi-monitor support.** Not a setting, not a plugin. | Remove Sunshine/Moonlight from the Work Realm design. Keep it in mind only for a future *gaming* realm. |
| R6 | **A plain VM gives software encoding only.** GNOME's VA-API path needs a real Intel GPU with Vulkan; virtio-gpu does not qualify, and there is **no H.264 software fallback** — it drops to RemoteFX Progressive. | Work Realm must be an **LXC container** with `/dev/dri`, or a VM with full iGPU passthrough. |

**On R4 — you asked not to recommend hardware unless it is a real blocker. It is one.** Proxmox ~20 GB + GNOME work realm 60–100 GB + Windows 11 100 GB before any game ≈ exceeds 230 GB immediately.
Two ways out, **no purchase required**:
- **Do not mirror.** SSD #1 = Proxmox + work realm. SSD #2 = Windows alone. No redundancy; rely on scheduled `vzdump` backups to the old laptop. Games are re-downloadable, so losing SSD #2 costs time, not data.
- Keep the work realm small and put personal files on the old laptop over the network.

A third NVMe is the clean fix when money allows, not before.

### YELLOW — probably fine, must prototype

| # | Finding |
|---|---|
| Y1 | ~~**Full GNOME + GDM session in an unprivileged container.**~~ **RESOLVED 2026-10-03** by Test 3. GPU, Vulkan, hardware renderer, virtual monitors, systemd, logind, GDM, the RDP server, client authentication and session creation **all work in a container**. Only grd's system-mode *handover* step was not reached, and that is a known upstream bug, not a container limit. See `PHASE-B-PREFLIGHT.md` §3.7. |
| Y2 | **UHD 630 sustaining 3 × 1080p60 H.264** (~180 fps aggregate). Cheap to benchmark. |
| Y3 | **Client-side decode of 3 × 1080p60.** FreeRDP's hwaccel is experimental and copies back to CPU. May be the true bottleneck. |
| Y4 | **VA-API silently not engaging.** Needs mutter dma-bufs + Vulkan ANV + sync objects + valid modifier + AVC-capable client. Any failure drops to CPU RemoteFX with no warning. Must be verified, not assumed. |
| Y5 | **Open GNOME bugs on exactly our target version:** VA-API H.264 corruption on Ubuntu 26.04.1 / grd 50.2 (#362); mstsc much slower than Remmina (#333); black screen at GDM remote login (#330); assertion errors after aborted handover (#354). All open. |
| Y6 | **Scale is a preference, not a command.** What mutter picks for a given DesktopScale is unverified. |
| Y7 | **IOMMU grouping** on the TUF Z390M-PRO. |
| Y8 | **RDP over a lossy WAN.** TCP head-of-line blocking. Fine on LAN. |
| Y9 | **Two devices connected to one realm** at once. |

### GREEN — established

Proxmox 9.2 as base and its API · VT-x/VT-d/EPT on the 8700K · RTX 2060 VFIO passthrough · iGPU and dGPU coexisting · iGPU shared with containers via render node · RDP multi-monitor as a protocol · GNOME headless 16-monitor support in source · GNOME session persistence across disconnect (47+) · portrait monitors (as swapped rectangles) · FreeRDP multimon client · SDL3 for client display enumeration · Tailscale transport · ZFS snapshots · bandwidth requirements.

---

## TASK 16 — Go / no-go

**Verdict: proceed, but do not wipe anything yet.**

Nothing found is fatal to the concept. Three findings change the design — GNOME instead of KDE, container instead of VM, per-monitor instead of one framebuffer — and all three are cheaper to adopt now than later. The remaining risk sits in a handful of cheap experiments that all run **before** touching the main PC.

**Do NOT install Proxmox until:**
1. **Experiment 1 passes** — a 3-monitor client drives a headless GNOME session into 3 separate working monitors, with VA-API confirmed active and no corruption.
2. **Experiment 0 passes** — 3 parallel 1080p60 VA-API encodes at ≥ 1.5× realtime on the UHD 630.
3. **Experiment 2 passes** — IOMMU groups clean, both GPUs visible, BIOS options found.
4. You have decided what happens to the data currently on those two SSDs, and accepted the no-mirror storage plan.

**Proceed when all four pass.** If #1 fails, the whole multi-monitor premise needs rethinking — far better to learn that before a wipe. If #2 fails, the design still works at 1–2 monitors.

**Recommended changes to the Celephaïs vision:**
1. **Drop "Kubuntu" from the vision. The Work Realm desktop is GNOME on Ubuntu 26.04.** Treat the desktop environment as an implementation detail, not a requirement. It is the cheapest change in this document and it unlocks the headline feature. Ubuntu 26.04 specifically: it is the only release with headless login, session persistence, VA-API on by default and HiDPI together.
2. **Rename "Secure Boot Mode".** It collides with UEFI Secure Boot, a different thing. Suggest **"Clean Boot Mode"**.
3. **Reframe what Celephaïs is.** The display pipeline is solved by others. Celephaïs is the *portal*: USB identity, realm lifecycle, and the path from a strange computer into your environment in one keypress. Build that; orchestrate the rest.
4. **Accept the "local Linux at the desk" gap.** With Proxmox headless, reaching the Work Realm while sitting at the PC means running the Celephaïs client inside the Windows gaming VM, which owns the monitors. That works and is low-latency. Name it now so it is not a surprise later.
5. **Plan file movement around server-side storage, not the RDP channel.** GNOME has no drive redirection.

---

## Experiment order

Details and exact commands come in Phase B. Order matters: cheapest and most decisive first.

| # | Experiment | Where | Risk | Resolves |
|---|---|---|---|---|
| **0** | iGPU VA-API encode benchmark | Live USB on the 8700K | None | Y2 |
| **1** | **Multi-monitor headless GNOME over RDP** | Laptop (VM/container) → 3-monitor Windows PC as client | None | R1, Y4, Y5, Y6, Y9 |
| **2** | Hardware preflight: BIOS, IOMMU, both GPUs, GPU reset | Live USB on the 8700K | None — nothing written | Y7, Task 12 |
| **3** | GNOME + GDM in an unprivileged LXC | Nested Proxmox, or the laptop | None | Y1 |
| **4** | Client decode load at 3 × 1080p60 | Real client hardware | None | Y3 |
| **5** | Latency and feel over Tailscale | Laptop ↔ laptop | None | Y8 |

**Experiment 1 is the one that matters.** Run experiment 0 first only because it takes ten minutes.

**A useful asset you may not have realised you have.** Your laptop (i7-12700H, Iris Xe + RTX 3060, Ubuntu 26.04) already has `/dev/kvm`, Docker, and repository access to `freerdp3-sdl` 3.32.0, `gnome-remote-desktop` 50.2 and `krdp` 6.6.4 — the exact versions in this report. Experiments 1, 3, 4 and 5 all run on it. Your 3-monitor Windows PC can act as the test client with the built-in `mstsc.exe /multimon` — nothing to install, nothing to wipe.

Caveat: the laptop's Iris Xe is a much stronger media engine than the UHD 630. **It proves the architecture; it does not prove the 8700K's throughput.** That is what experiment 0 is for.
