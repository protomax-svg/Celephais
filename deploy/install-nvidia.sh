#!/usr/bin/env bash
# Give a Fedora Realm the proprietary NVIDIA driver. Run as root INSIDE the
# Realm, at its own console, while it holds the Local Seat.
#
#   sudo bash install-nvidia.sh
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
command -v dnf >/dev/null || {
  echo "this is the Fedora route; work's Ubuntu driver is already installed" >&2
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
