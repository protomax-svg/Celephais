# Celephaïs — Architecture v0

Status: draft for review. Date: 2026-09-28.
Items marked **[verify]** come from research that was not confirmed at the source. Check them in the spike (Phase 0).

---

## 0. Summary

- The idea is sound. Most parts exist as mature software. Celephaïs is mainly glue plus UX.
- The biggest risk is not technical. It is **feel**: is a remote desktop good enough to be "my computer"? We can test that **before writing code** (Phase 0).
- v0.1 stack:
  - Server: **Proxmox VE** (KVM/QEMU).
  - Environment: **Ubuntu VM** with headless **RDP** (gnome-remote-desktop, or xrdp as fallback).
  - Display client: **FreeRDP 3** (`sdl-freerdp`), run as a child process.
  - Network: **Go tsnet sidecar** (embedded Tailscale, userspace, no admin, no install).
  - Control: the **Proxmox API** directly, with a narrow-scope token. No custom server in v0.1.
  - Launcher UI: **Rust + Slint**, one static binary, black fullscreen.
  - USB: **GPT, 2 partitions** (reserved ESP + exFAT data). An app-level encrypted vault holds secrets and local files.

---

## 1. Straightforward with current technology

- Persistent VMs, start / stop / suspend / status: Proxmox API.
- A private network between devices: Tailscale (already in use).
- Fullscreen remote desktop with clipboard, audio, dynamic resolution, and H.264 on Linux and Windows VMs: RDP (FreeRDP client).
- Run the client from USB with no install: a static Rust binary plus a static Go sidecar.
- Server-side shared storage for VMs: ZFS dataset plus virtiofs or NFS.
- A persistent Android environment for ordinary apps: Redroid or BlissOS plus scrcpy (later).

## 2. Difficult

- **"Feels like a local computer."**
  - RDP with H.264 is very good for desktop work on a LAN or a good WAN.
  - For video, games, or fast motion, you need **GPU encoding** in the VM: GPU passthrough + Sunshine/Moonlight.
  - GPU passthrough is hardware-dependent. Intel iGPU SR-IOV is experimental. NVIDIA vGPU needs a license. **[verify]**
- **Latency over WAN.** Physics wins. From a café to a home server, expect 20–60 ms of network time plus about 10–20 ms of encode and decode. That is OK for work, but not "local".
- **Session persistence on disconnect.** Files and apps persist anyway, because the disk persists. Open windows surviving a disconnect depends on the RDP server:
  - xrdp reconnects to the existing session reliably.
  - gnome-remote-desktop headless: **[verify]** in the spike.
- **Keyboard capture on the host.** The client can grab most keys. Some combinations belong to the host OS and cannot be captured:
  - Windows: `Ctrl+Alt+Del`, `Win+L`.
  - Some Linux desktops: global shortcuts.
- **Encrypted USB workspace on Windows without admin rights** (see §8).
- **Portable secrets.** A USB that is lost or copied must not become a key to everything (see §17).

## 3. Parts that will not work exactly as you imagine

| Assumption | Reality | What we do |
|---|---|---|
| "Insert USB → Celephaïs opens" on any computer | Windows and Linux block USB autorun on purpose. | Unknown host: double-click once. Trusted host: a small per-user helper (no admin). See §7. |
| "Nothing personal is written to the host disk" | Windows records that an exe ran: Prefetch, ShimCache, UserAssist. Defender and EDR may scan and upload the exe. We cannot stop that. | Goal becomes: **no app data, no credentials, no files, no caches** on the host disk. Execution traces are accepted and documented. |
| An encrypted USB partition, mounted like a drive | Mounting LUKS, VeraCrypt, or BitLocker-To-Go needs admin or drivers on some hosts. BitLocker is not on every Windows edition, and there is no Linux-to-Windows parity. | An **app-level vault**: encrypted files on exFAT that only Celephaïs opens. See §8. |
| Android opens Celephaïs when the USB stick is plugged in | Android mounts mass-storage devices itself. An app usually does not get the "USB attached" intent for them. **[verify]** on a real phone. | A FIDO2 USB-C key *can* trigger the intent. Or just tap the app icon. |
| A cloud filesystem on every client | In a thin-client model, files live **next to the VMs**. The host rarely needs them. | Serve storage to the VMs server-side. A client-side CloudFS is only for your own trusted devices, later. |
| "Secure Boot Mode" | The name collides with UEFI Secure Boot, which is a different thing. | Rename it **"Clean Boot Mode"**. It can still *use* UEFI Secure Boot, with a signed shim and kernel. |
| An Android VM as a safe place for crypto or 2FA | A VM has no secure element and no TEE. A hypervisor compromise means everything in it is compromised. | See §23. Keep seeds on a hardware wallet and 2FA on hardware keys. |
| A custom server API is needed from day 1 | Proxmox already has a good API and scoped tokens. | v0.1 uses it directly. Add a "hub" service only when a feature needs one (Android, storage, revocation, multiple users). |

## 4. Windows-specific limitations

- USB AutoRun is ignored since Windows 7. `autorun.inf` only sets the icon and label.
- Unsigned exe files:
  - An exe copied to NTFS from the internet keeps Mark-of-the-Web, and SmartScreen shows a warning.
  - On exFAT the mark is lost, so there is no warning. Defender still scans the file.
  - Sign releases later (Azure Trusted Signing is the cheapest). This is optional for personal use.
- Tailscale (`tailscaled`) needs admin rights and a service. Non-admin mode is in progress upstream **[verify]**. The embedded tsnet library avoids this completely.
- There is no RAM disk without a driver. Temporary files go to the USB, not to `%TEMP%`. We set `TMP`/`TEMP` for our child processes.
- WebView2 writes a user-data folder. This is one reason we avoid Tauri (§15).
- Hotkeys `Ctrl+Alt+Del` and `Win+L` always go to the host.
- Isolation Mode: **WFP dynamic sessions** (`FWPM_SESSION_FLAG_DYNAMIC`). The filters are removed automatically when the process exits or crashes. This is the right primitive. It needs admin.

## 5. Linux-specific limitations

- Many distributions mount removable media as `noexec`. Workaround: the launcher script copies the binary to `/dev/shm` (RAM) and runs it from there.
- AppImage needs FUSE2, which is missing on new Ubuntu by default. **Do not use AppImage.** Ship plain static binaries (Rust with musl, Go with `CGO_ENABLED=0`).
- A GUI binary still needs system libraries: X11 or Wayland, GL, and a font stack. Every desktop distribution has them.
- `sdl-freerdp` needs SDL and codecs. Either build it with static libraries in CI, or bundle it with its `.so` files and an `LD_LIBRARY_PATH` wrapper. Decide in Phase 1.
- Isolation Mode: `nftables` in a dedicated table, plus a watchdog that removes the table. Rules are not saved, so a reboot always clears them.

## 6. Android-specific limitations

**Android as a client (phone → server):**
- Possible: authentication, a list of environments, start and stop, and RDP or Moonlight clients (both exist on Android).
- An RDP client is required. Options: embed FreeRDP (hard), or hand off to an installed client app (easy). Decide later.
- USB OTG auto-open: see §3.

**Android as a server environment (Android VM):**

| Possible | Difficult | Fundamentally restricted |
|---|---|---|
| Install, update and remove APKs, launcher, browser, persistent data | Google Play services (grey-area licensing; manual device registration; MicroG as an alternative) | Play Integrity **STRONG** (needs hardware attestation) |
| Access to server files (mounted inside) | Play Integrity BASIC/DEVICE (often fails on emulators) | Hardware-backed Keystore / StrongBox |
| Software TOTP apps (back them up!) | Banking apps (case by case) | Google Wallet NFC tap-to-pay |
| Widevine L3 (SD video) | ARM-only apps on an x86 host (libhoudini/libndk translation) | Widevine L1 (HD DRM) |
| scrcpy control over Tailscale | Camera, microphone and GPS pass-through | Real NFC and Bluetooth hardware |

Candidates:
- **Redroid** (a container, Android up to 15/16, light, runs on the host kernel through binder).
- **BlissOS** in KVM (a full VM, better isolation, but the project is slowing).
- Cuttlefish has built-in WebRTC, but it is a development device and heavy to set up.

Pick one later with a small spike.

## 7. USB autorun limitations and the supported solution

- **Unknown host:** manual launch of one file:
  - Windows: `Bifrost.exe`.
  - Linux: `bifrost.sh`, which copies the binary to RAM and runs it.
- **Trusted Windows host:** a one-time setup installs a **per-user** helper. It starts from the `HKCU\...\Run` key and needs no admin.
  1. The helper listens for volume arrival (`WM_DEVICECHANGE`).
  2. It checks that the volume label is `BIFROST` and that the volume serial is on its allowlist.
  3. It checks that the exe's **signature matches a pinned public key**.
  4. Only then does it launch the exe.
- **Trusted Linux host:** a one-time setup installs a **systemd user path unit** on the desktop automount point, for example `/run/media/$USER/BIFROST`. It needs no root and runs the same signature check.
- **Why the signature check is mandatory:** without it, any USB stick named `BIFROST` gets code execution on your trusted computer.

## 8. Portable application limitations and host-storage policy

Every child process gets redirected paths:
- `HOME`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `XDG_DATA_HOME`, `APPDATA`, `LOCALAPPDATA`, `TMP`, `TEMP` point to:
  - RAM (`/dev/shm/bifrost-<rand>`) on Linux.
  - `USB:\runtime\tmp` on Windows, which is wiped at start and at exit.
- FreeRDP state (known hosts, certificates) goes to the vault or to RAM, with `/cert:` pinned from config.
- Logs are off by default. If they are on, they go to the USB only.
- The tsnet state is **in memory** (a custom `ipn.StateStore`). It is never written as plain files.

The **USB vault** (app-level encryption, no admin, works on Windows, Linux and Android):
- It is a folder on exFAT with **age** files (X25519 + ChaCha20-Poly1305). The age identity is protected by a passphrase (scrypt).
- It holds credentials, tsnet auth, VM credentials, and local files you choose to keep.
- The launcher UI can list, open and save files in the vault. It decrypts them to RAM or a temporary area, and never to the host disk.
- Limit: this is not a mounted drive. Other host apps cannot open vault files directly. This is accepted for v0.x.

## 9. Security of running over an untrusted host (Portable Mode)

Portable Mode is **not safe on a hostile host**. A compromised host can:
- Log keys, including your vault passphrase.
- Capture the screen: it sees everything you see in the VM.
- Read process memory: decrypted keys and session tokens.
- Copy the whole USB stick while it is plugged in. The vault stays encrypted, but the passphrase may have been keylogged.
- Inject input into your remote session.

Mitigations, in order of value:
1. **Clean Boot Mode** for hosts you do not trust.
2. A **hardware key** (FIDO2 `hmac-secret`) as part of the vault unlock, later. A copied USB plus a keylogged passphrase is then still not enough.
3. **Short-lived, revocable credentials.** Ephemeral tailnet nodes, and Proxmox tokens that you can revoke.
4. Server-side **audit**: Proxmox task log and tailnet connection log.
5. Never unlock the crypto or 2FA environment from Portable Mode on a foreign host (UI warning).

## 10. Existing open-source projects to reuse

| Need | Project |
|---|---|
| Hypervisor + API | Proxmox VE (KVM/QEMU, ZFS, LXC) |
| Private network, portable | Tailscale **tsnet** (Go library), optionally **Headscale** later |
| Remote display (v0.1) | **FreeRDP 3** client; **gnome-remote-desktop** or **xrdp** server |
| Remote display (GPU, later) | **Sunshine** + **Moonlight** (GPL-3; run as a separate process, do not link) |
| Android environment (later) | **Redroid** or **BlissOS**; **scrcpy** |
| Encryption | **age** (Rust crate `age`), **rustls** |
| UI | **Slint** |
| Clean Boot image (later) | **mkosi** (systemd) + **cage** (Wayland kiosk) + distribution-signed shim/kernel |
| Storage | ZFS, **virtiofs**, NFS, existing **Nextcloud** (External Storage app), **rclone** (later, trusted clients) |

## 11. Hypervisor / server architecture

```
Proxmox host (ZFS)
├── pool "bifrost"             ← only these VMs are visible to the client token
│   ├── vm 101 work-linux      (Ubuntu LTS, RDP server, tags: bifrost, proto-rdp)
│   └── later: personal-linux, windows, android (LXC/VM)
├── dataset tank/personal      ← personal files (shared)
│   ├── virtiofs → VMs
│   └── Nextcloud External Storage → phones, browser
└── tailscale on host + in each VM (or a subnet router)
```

- **Environment state** is in the VM disk. **Personal files** are in `tank/personal`, shared.
- Proxmox token `bifrost@pve!client` gets only `VM.Audit` and `VM.PowerMgmt` on `/pool/bifrost`. It cannot create or delete VMs, and has no shell.
- Tailscale ACL: `tag:bifrost-client` → `proxmox:8006` and `vm-*:3389` only.

## 12. Remote display architecture

- v0.1: **RDP everywhere.**
  - Linux VM: gnome-remote-desktop headless (Ubuntu 24.04 or 26.04), with xrdp as the fallback if session persistence fails.
  - Windows VM (later): RDP is built into Windows. It is free and needs no GPU.
  - Client: `sdl-freerdp /f /dynamic-resolution /gfx:AVC444 +clipboard /sound /microphone ...` These are FreeRDP command-line flags. The launcher builds this command.
- Later: **Sunshine + Moonlight** for VMs with a passed-through GPU (lowest latency, 60–120 fps).
  - ⚠ Moonlight video uses **UDP**. The tsnet sidecar must forward UDP, not only TCP. This is planned work in the sidecar, not a blocker.
- Protocol is a per-environment field (a Proxmox tag). The launcher maps each protocol to a client command. That is the whole abstraction.

## 13. Client architecture

```
USB:/Bifrost.exe | bifrost.sh
        │
  bifrost (Rust, Slint UI, one process)
        ├── vault: age decrypt → secrets in memory
        ├── spawns bifrost-net (Go tsnet sidecar)
        │     stdin: auth key + config;  exposes 127.0.0.1:<port> → tailnet targets
        ├── proxmox client: list / start / stop / status (HTTPS through sidecar)
        └── session: spawns sdl-freerdp → 127.0.0.1:<port>, watches exit, returns to selector
```

- **UX flow:** black screen → passphrase → environment list → Enter.
  1. Start the VM if needed.
  2. Wait for the RDP port.
  3. FreeRDP opens fullscreen above our black window.
  4. When the session ends, we are back at the list.
- **Reconnect:** FreeRDP has auto-reconnect (`/auto-reconnect`). The launcher re-spawns it if it exits with a network error.

## 14. Is Rust right for each component?

| Component | Language | Why |
|---|---|---|
| Launcher / core | **Rust** | Static binary, safe, good crypto crates |
| Network sidecar | **Go** | tsnet is Go. tailscale-rs is experimental: DERP-only, not audited **[verify]**. Swap to it when it matures. |
| Display client | C (FreeRDP), we do not write it | Mature |
| Trusted-host helper | **Rust** | Tiny, native OS APIs |
| Hub (later) | **Rust** (axum) | Same toolchain |
| Clean Boot image | No code, **mkosi** config | Configuration, not software |
| Android client (later) | Rust core + Slint, or Kotlin shell | Decide after the desktop is proven |

## 15. GUI technology

**Recommendation: Slint.** Tauri is not recommended.

- **Tauri problems for this project:**
  - It needs WebView2 on Windows, which writes a data folder, or a bundled 180 MB runtime.
  - It needs webkit2gtk on Linux.
  - Its AppImage breaks on Mesa 25+.
  - Every one of these works against "portable" and "no host writes".
- **Slint:**
  - Pure Rust with static linking.
  - Runs on Windows, Linux and Android.
  - Small, and good for a black minimal UI.
  - License: the royalty-free license is fine for this project.
- The alternative is **egui**: simpler and MIT-licensed, but its Android support is less mature.
- The UI is small anyway: a passphrase field, a list, and status. The remote session itself is FreeRDP's window.

## 16. Authentication architecture (v0.1)

Two factors:
1. **Something you have:** the USB stick with the vault.
2. **Something you know:** the vault passphrase.

The vault unlocks:
- A **Tailscale auth key**: reusable, tagged `tag:bifrost-client`, and **ephemeral**, so each run is a new node that disappears afterwards. You can revoke it in the admin console.
- A **Proxmox API token**: narrow scope (§11), and revocable.
- **VM login credentials** for RDP.

Later:
- A FIDO2 hardware key as a third factor.
- A hub with per-USB identities and revocation.
- Optionally Headscale.

## 17. USB identity / credential architecture

```
GPT, 32 GB
p1  ESP   FAT32   1 GiB   reserved for Clean Boot Mode (empty in v0.1)
p2  data  exFAT   rest    label BIFROST
    ├── Bifrost.exe, bifrost.sh
    ├── bin/{windows,linux}/  bifrost, bifrost-net, freerdp
    ├── vault/                *.age   (identity, secrets, local files)
    ├── config.toml           non-secret (server address, env hints)
    └── runtime/              tmp, wiped each run
```

- Why two partitions:
  - Repartitioning later destroys data, so we reserve the ESP now.
  - exFAT is readable on Windows, Linux, Android and macOS without drivers.
- No plaintext secrets on the USB. Everything secret is in `vault/`.
- A lost USB means: revoke the tailnet key and the Proxmox token (2 clicks), then re-provision.

## 18. Server / client communication

- The client talks only through the tailnet (the sidecar). Nothing is public.
- Control plane: Proxmox REST API over HTTPS. Pin the Proxmox certificate in `config.toml`.
- Data plane: RDP (TLS) directly to the VM's tailnet IP.
- Later hub: HTTPS JSON over the tailnet with mTLS. Add it only when needed.

## 19. Server-side shared storage

- ZFS dataset `tank/personal` is the source of truth.
- Linux VMs: **virtiofs** mount. It is native in Proxmox 8.4+ **[verify version]**.
- Windows VM: virtiofs (WinFsp + virtio-win driver) or SMB.
- Android (Redroid): bind-mount into the container.
- Phones and browser: **Nextcloud External Storage** pointing at the same dataset. Do not put files into Nextcloud's own data folder from outside.
- Client CloudFS, P2P routing and caching: later, for trusted devices only (rclone mount).

## 20. Android virtualization approach

- Later phase. Spike Redroid first (light, current Android versions). Use BlissOS in a VM if you need stronger isolation.
- Control: scrcpy over the tailnet. From an Android phone, use a scrcpy-compatible client or Moonlight + Sunshine-for-Android. **[verify]**
- Keep "fundamentally restricted" apps (§6) on the physical phone.

## 21. Repository structure

```
/client        Rust crate: launcher + UI (Slint) + vault + proxmox client
/net           Go module: bifrost-net (tsnet sidecar)
/usb           scripts: partition, build, copy release onto stick
/server        docs + scripts: Proxmox pool/token/ACL, VM setup (cloud-init)
/docs          this file, decisions log
```

- Later: `/helper` (trusted-host autolaunch), `/hub`, `/android`, `/boot` (mkosi).
- No Cargo workspace until there is a second Rust crate.

## 22. Development / testing strategy

1. **Phase 0 spike, no code:** real VM, real RDP, your real laptop. Use it for real work for several days. This answers "the test that matters" cheaply.
2. Hosts for testing: a Windows VM and a Linux VM on your desktop act as "foreign" hosts.
3. **Host-hygiene test** (repeatable):
   - Windows: Process Monitor with a filter on file writes by our processes.
   - Linux: `fatrace` or `inotifywait -r` on `/home` and `/tmp` during a session.
   - Passing rule: no writes outside the USB and RAM.
4. **Latency test:** a phone slow-motion video of a key press and the screen (glass-to-glass). This is simple and honest.
5. Code tests: a few `assert` checks for the vault round trip and the config parser. No big framework.

## 23. Major security threats to design around

1. **A lost or copied USB.** Mitigation: vault encryption, revocable ephemeral credentials, hardware key later.
2. **An untrusted host OS** (keylogger, screen capture). Mitigation: Clean Boot Mode, UI warnings (§9).
3. **Proxmox host compromise means all environments are compromised.** Mitigations:
   - Keep the host minimal and patched.
   - Proxmox UI and SSH on the tailnet only.
   - Admin 2FA.
   - Encrypted off-site backups (Proxmox Backup Server).
4. **Tailnet ACL mistakes.** Mitigation: the client tag can reach only 8006 and 3389. Review ACL changes.
5. **Autolaunch helper abuse.** Mitigation: a pinned signature check (§7).
6. **Supply chain.** Mitigations:
   - Pin versions of FreeRDP, tsnet and crates.
   - Build them ourselves in CI.
   - `cargo audit`.
7. **Crypto seeds and critical 2FA.**
   - Do **not** put them in any VM.
   - Seeds go on a **hardware wallet**. 2FA uses **FIDO2 keys**, with backup codes offline.
   - The server must not be a single point of failure for your identity.
8. **Isolation Mode breaking host networking.** Mitigations:
   - WFP dynamic sessions (Windows).
   - A watchdog plus a volatile nft table (Linux).

## 24. NOT in v0.1

- Custom CloudFS, P2P storage routing, and client-side caching.
- Android VM, Windows VM, and the Android client.
- Sunshine/Moonlight and GPU passthrough.
- The hub server and Headscale.
- Isolation Mode.
- Clean Boot Mode (the ESP partition is reserved only).
- The trusted-host autolaunch helper (Phase 3; not needed to prove the concept).
- FIDO2 unlock.
- USB forwarding, multi-monitor, and a custom protocol.
- Code signing.

---

## Roadmap

| Phase | Goal | Done when |
|---|---|---|
| **0 — Spike (no code)** | Set up an Ubuntu VM with headless RDP on Proxmox. Connect from your laptop with FreeRDP fullscreen over normal Tailscale. | You have used it for real work for 3+ days. You know whether it feels good. You know whether sessions persist (grd vs xrdp). |
| **1 — Portable network** | `bifrost-net` sidecar + USB layout. Run FreeRDP through the sidecar on a host **without** Tailscale. | Connect from a clean Windows VM and a clean Linux VM with no install. |
| **2 — Launcher** | Rust + Slint: vault, list, start/stop/status via Proxmox, launch and return, black transitions. | The "Choose environment → Connect" flow works end to end from USB. |
| **3 — Hygiene + polish** | Host-hygiene tests pass, reconnect, trusted-host helper. | Procmon and fatrace are clean. Autolaunch works on your own PCs. |
| **Gate** | The v0.1 question: "do I want to use this as my normal computer?" | Yes → continue. No → find out why first. |
| Later | Windows VM (RDP) → GPU + Sunshine → Android client → Android VM → storage → Clean Boot → Isolation → hub/FIDO2 | — |

## Open questions for you

1. Does your server already run **Proxmox**? If it runs something else (plain libvirt, TrueNAS, Unraid), §11 changes.
2. Does the server have a **GPU** (or an iGPU) that you can pass through? This decides when Sunshine becomes realistic.
3. Where is the server, and where do you usually connect from? Home LAN only, or often over the internet?

## Visual direction (from vision.md)

- Pure black background, white text, rare green accent (for example the "Running" state and the focus ring).
- No window frames. Fullscreen, borderless.
- Transitions are black on black. No splash screens and no animations beyond a short fade.
