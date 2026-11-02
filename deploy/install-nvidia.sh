#!/usr/bin/env bash
# Give a Realm the NVIDIA driver. Run as root INSIDE the Realm. Fedora and
# Omarchy; `work`'s Ubuntu driver is already installed and is not handled here.
#
#   sudo bash install-nvidia.sh
#
# On Fedora, run it at the Realm's own console while it holds the Local Seat.
# On Omarchy it can be run either way, and before is better - see below.
#
# This is what makes the monitors' own speakers work, and it is not a sound
# setting. The RTX's audio function is passed to the Realm already - the seat
# carries 0000:01:00.1 beside the GPU, and the guest sees it as card 1,
# "HDA NVidia", with four HDMI outputs. What is missing is the ELD: the block
# of data a monitor sends back saying "I can play sound, and these formats".
# nouveau, the open driver Fedora ships, does not pass it to the audio
# hardware - NVIDIA says so in its own HDMI audio document - so every pin on
# `personal` reads `eld_valid 0`, ALSA disables the outputs, and the desktop
# offers nothing to play to. The proprietary driver forwards it.
#
# It also gives the Realm real 3D and NVENC, which nouveau on a Turing card
# does not do well. `work` runs this driver already (595.99.02, MOK enrolled).
#
# Secure Boot is on in these Realms, so the module has to be signed by a key
# the firmware trusts. This generates that key and hands it to the firmware;
# the enrollment itself is three screens at the next boot, and only someone
# at the monitors can answer them.
#
# If the Realm comes back with no picture at all, nothing is lost: take the
# seat away from it in Celephais ("give the monitors back"), which puts its
# virtual display back, and it can be reached again from the Proxmox console.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo "run this as root: sudo bash $0" >&2; exit 1; }

# ---------------------------------------------------------------- Omarchy ---
# Omarchy already knows how to do this: install/hardware/nvidia.sh picks the
# driver by GPU generation, writes the modprobe and mkinitcpio drop-ins, and
# is idempotent. It is the source of truth for a machine that has the card,
# and this hands straight over to it when the card is there.
#
# The card is only there while the Realm holds the Local Seat, and that is the
# awkward part. `celephais-seat switch` takes the virtual display away in the
# same breath as it gives out the RTX, so a Realm that reboots onto the
# monitors with no driver has no screen to install one from - not its own, and
# not the Proxmox console either. nouveau would probably carry it; "probably"
# is not what you want to find out with the monitors dark.
#
# So the branch below does the same work ahead of the switch, off the same
# package list, while the virtual display is still there to fall back on.
# Afterwards run it again with the card attached: Omarchy's own script then
# takes over and settles the initramfs properly, because its mkinitcpio
# drop-in reads the live PCI tree - with the RTX the only display controller
# in the Realm, it drops the `kms` hook and leaves nouveau and its ~100 MB of
# GSP firmware out.
if command -v pacman >/dev/null; then
  OMARCHY_NVIDIA=/usr/share/omarchy/install/hardware/nvidia.sh
  [ -f "$OMARCHY_NVIDIA" ] || {
    echo "no $OMARCHY_NVIDIA: this is the Omarchy route and needs the omarchy package" >&2
    exit 1; }

  # nvidia-open-dkms builds an unsigned module. These Realms boot OVMF, and
  # `work2`'s firmware has no keys enrolled, so nothing checks it. A Realm set
  # up like `work` or `personal`, with pre-enrolled keys, would need the
  # module signed, and this script does not do that.
  if [ "$(bootctl status 2>/dev/null | grep -ci 'Secure Boot: enabled')" != 0 ]; then
    echo "Secure Boot is on in this Realm. nvidia-open-dkms is not signed and" >&2
    echo "will not load. Turn it off in the firmware, or sign the module." >&2
    exit 1
  fi

  if lspci | grep -qi nvidia; then
    echo "the RTX is attached; handing over to Omarchy's own installer"
    bash "$OMARCHY_NVIDIA"
  else
    echo "no NVIDIA card in this Realm yet - installing ahead of the seat switch."
    # Kept in step with the GSP branch of $OMARCHY_NVIDIA. Turing and newer,
    # which the RTX 2060 is; the pre-GSP 580xx branch there is for cards the
    # Local Seat does not have and is deliberately not mirrored.
    omarchy-pkg-add nvidia-open-dkms nvidia-utils lib32-nvidia-utils libva-nvidia-driver
    mkdir -p /etc/modprobe.d /etc/mkinitcpio.conf.d
    printf 'options nvidia_drm modeset=1\n' > /etc/modprobe.d/nvidia.conf
    printf 'MODULES+=(nvidia nvidia_modeset nvidia_uvm nvidia_drm)\n' \
      > /etc/mkinitcpio.conf.d/nvidia.conf
  fi

  # Omarchy's script writes the mkinitcpio drop-in but does not rebuild, and
  # the drop-in is written after the packages install, so the hook that fires
  # during the install has not seen it yet.
  #
  # Not `mkinitcpio -P`. Omarchy boots a unified kernel image through limine
  # and keeps no presets in /etc/mkinitcpio.d, so -P fails there; worse,
  # /usr/local/bin/mkinitcpio is a wrapper that answers that failure by
  # *asking on the terminal* whether to run limine-mkinitcpio instead. Run
  # with no stdin that prompt reads EOF and takes the default, which happens
  # to be yes - the image came out right here by that accident, and would not
  # on a wrapper whose default ever changed. Ask for the image directly.
  if command -v limine-mkinitcpio >/dev/null; then
    limine-mkinitcpio
  else
    mkinitcpio -P
  fi

  echo
  echo "driver: $(pacman -Q nvidia-open-dkms 2>/dev/null || echo 'not installed')"
  if lspci | grep -qi nvidia; then
    echo "Reboot, then check: nvidia-smi"
  else
    cat <<'NOTE'

Installed, with nothing to drive yet. Now give the Realm the monitors from
Celephais ("take the monitors"), which reboots it onto the RTX. Then run this
again in the Realm, with the card attached, to let Omarchy settle the rest.
NOTE
  fi
  exit 0
fi

# ----------------------------------------------------------------- Fedora ---
command -v dnf >/dev/null || {
  echo "no dnf and no pacman here; work's Ubuntu driver is already installed" >&2
  exit 1; }
RELEASE=$(rpm -E %fedora)

# RPM Fusion carries the driver; Fedora itself will not.
if ! rpm -q rpmfusion-nonfree-release >/dev/null 2>&1; then
  dnf install -y -q \
    "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$RELEASE.noarch.rpm" \
    "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$RELEASE.noarch.rpm"
  echo "added RPM Fusion"
fi

# akmod rebuilds the module against each new kernel by itself, which matters
# on Fedora: a kernel update with a driver that does not follow it is a Realm
# that boots to nothing.
dnf install -y -q akmods kmodtool mokutil openssl
# akmod-nvidia alone leaves no `nvidia-smi`: the driver installs
# xorg-x11-drv-nvidia-cuda-libs, the libraries, while the tools - nvidia-smi
# among them - are in xorg-x11-drv-nvidia-cuda, which nothing pulls in. On
# `personal` that read as a failed install when the driver was in fact fine.
dnf install -y -q akmod-nvidia xorg-x11-drv-nvidia-cuda

# One key for this machine, kept by akmods and used to sign every module it
# builds from now on. Generated once; a second run must not replace it, or
# everything enrolled so far stops matching.
CERT=/etc/pki/akmods/certs/public_key.der
[ -f "$CERT" ] || kmodgenca -a

if mokutil --sb-state 2>/dev/null | grep -qi "SecureBoot enabled"; then
  if mokutil --test-key "$CERT" 2>&1 | grep -qi "already enrolled"; then
    echo "the signing key is already enrolled"
  else
    echo
    echo "Secure Boot is on, so the firmware has to be told about this key."
    echo "Choose a password now; it is asked for once, at the next boot, and"
    echo "then never again. It is not an account password and nothing keeps it."
    mokutil --import "$CERT"
    NEEDS_ENROLL=yes
  fi
fi

# Build the module for the running kernel now rather than at the next boot,
# so a failure is seen here, with a shell to read it in.
akmods --force
dracut --force

echo
echo "built:  $(ls /lib/modules/"$(uname -r)"/extra/nvidia*/nvidia.ko* 2>/dev/null | head -1 || echo 'no module yet - read the output above')"
if [ "${NEEDS_ENROLL:-no}" = yes ]; then
  cat <<'NOTE'

Now reboot. The machine stops at a blue screen, "MOK management", before
Fedora starts. It is waiting for the keyboard on the monitors:

    Enroll MOK  ->  Continue  ->  Yes  ->  the password you just chose

It then reboots once more by itself. Miss it, or choose "Continue boot", and
the key is not enrolled: the module stays unsigned, Fedora comes up on
nouveau again, and this script can be run again to retry.
NOTE
else
  echo
  echo "Now reboot."
fi

cat <<'AFTER'

After the reboot, from here or from Celephais:

    nvidia-smi                      the driver is live
    cat /proc/asound/card1/eld#0.*  | grep -c "eld_valid.*1"

One or more valid ELDs means the monitors are telling the Realm they can
play sound. The outputs then appear in Settings -> Sound; pick the monitor
with the speakers.
AFTER
