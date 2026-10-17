#!/usr/bin/env bash
# =========================================================================
# ACE Pro panel for Fluidd - installer (part of the ACEPRO Klipper driver)
#
# Run this ON the Klipper host (e.g. via SSH):
#   git clone --branch dev https://github.com/Kobra-S1/ACEPRO.git
#   cd ACEPRO/fluidd
#   bash install.sh
#
# Or straight from a curl one-liner:
#   curl -fsSL https://raw.githubusercontent.com/Kobra-S1/ACEPRO/dev/fluidd/install.sh | bash
#
# The script is self-sufficient. It will:
#   1. check Moonraker is up and the acepro-any-klipper driver is visible
#      in the API (override with FORCE=1)
#   2. install Node.js >= 18 (pinned 22 LTS tarball) if missing
#   3. install pnpm if missing
#   4. add a temporary swap file if the host has little memory (the build
#      needs it on <2 GB hosts; removed afterwards)
#   5. clone a fresh Fluidd, copy the panel, patch Fluidd, build
#   6. back up the current webroot, deploy, verify, roll back on failure
#
# Optional environment variables:
#   FLUIDD_DIR    where to build (default: fresh clone into ./fluidd)
#   WEBROOT       where to deploy (default: auto-detect, see below)
#   KEEP_REPO=1   keep the cloned/built repository after deploy
#   MOONRAKER_URL Moonraker API URL for the checks
#                 (default: http://localhost:7125)
#   FORCE=1       skip the ACE driver preflight check
#   NO_SWAP=1     never create a temporary swap file
# =========================================================================
set -euo pipefail

PKG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLUIDD_DIR="${FLUIDD_DIR:-$PKG_DIR/fluidd}"
WEBROOT="${WEBROOT:-}"
KEEP_REPO="${KEEP_REPO:-0}"
MOONRAKER_URL="${MOONRAKER_URL:-http://localhost:7125}"
FORCE="${FORCE:-0}"
NO_SWAP="${NO_SWAP:-0}"

NODE_VERSION="v22.23.3"
# minimum total memory (RAM + swap, MB) we want before building Fluidd
MIN_BUILD_MEM_MB=2400
SWAP_FILE_MB=2048

# When piped through curl, BASH_SOURCE is the pipe: find the panel sources
# relative to the checkout if present, otherwise clone this repo first.
if [ ! -f "$PKG_DIR/src/components/widgets/ace/AceCard.vue" ]; then
  echo "==> Panel sources not found next to the script - fetching the repo..."
  TMP_CLONE="$(mktemp -d "${HOME:-/var/tmp}/ACEPRO.XXXXXX")"
  git clone --depth 50 --branch dev https://github.com/Kobra-S1/ACEPRO.git "$TMP_CLONE/ACEPRO"
  PKG_DIR="$TMP_CLONE/ACEPRO/fluidd"
  FLUIDD_DIR="$PKG_DIR/fluidd"
fi

echo "------------------------------------------------------------"
echo " ACE Pro panel for Fluidd - installer"
echo " host   : $(hostname) ($(uname -m))"
echo "------------------------------------------------------------"

have() { command -v "$1" >/dev/null 2>&1; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if sudo -n true 2>/dev/null; then
    SUDO="sudo"
  elif have sudo; then
    echo "!!! sudo requires a password - re-run as root or enable passwordless sudo."
    exit 1
  else
    echo "!!! root rights are required to install Node.js into /usr/local."
    exit 1
  fi
fi

# ------------------------------------------------------------- preflight ----
echo "==> Checking Moonraker at $MOONRAKER_URL ..."
have curl || { echo "!!! curl is required."; exit 1; }
curl -fsS --max-time 10 "$MOONRAKER_URL/server/info" >/dev/null \
  || { echo "!!! Moonraker is not reachable at $MOONRAKER_URL - is Klipper up?"; exit 1; }

if [ "$FORCE" != 1 ]; then
  if ! curl -fsS --max-time 10 "$MOONRAKER_URL/printer/objects/list" | grep -q '"ace'; then
    cat >&2 <<'EOF'
!!! No ACE driver objects (printer.ace*) found in the Moonraker API.
    Install the ACEPRO Klipper driver first:
      https://github.com/Kobra-S1/ACEPRO
    Re-run with FORCE=1 to deploy anyway (the card stays hidden until the
    driver is present).
EOF
    exit 1
  fi
  echo "    ACE driver objects found in the API."
else
  echo "!!! Skipping ACE driver preflight (FORCE=1)."
fi

# ------------------------------------------------------------ webroot -------
if [ -z "$WEBROOT" ]; then
  for candidate in /usr/share/fluidd /var/www/fluidd /opt/fluidd /home/pi/fluidd; do
    if [ -d "$candidate" ]; then
      WEBROOT="$candidate"
      break
    fi
  done
fi
if [ -z "$WEBROOT" ]; then
  echo "!!! Could not auto-detect the Fluidd webroot."
  echo "    Re-run with:  WEBROOT=/path/to/webroot bash install.sh"
  exit 1
fi
if [ ! -d "$WEBROOT" ]; then
  echo "!!! Webroot $WEBROOT does not exist."
  exit 1
fi
echo "==> Webroot: $WEBROOT"

# ------------------------------------------------------- Node.js >= 18 ------
NODE_M="$(node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/' || echo 0)"
if [ "${NODE_M:-0}" -lt 18 ]; then
  echo "==> Node.js missing or too old - installing $NODE_VERSION ..."
  case "$(uname -m)" in
    aarch64|arm64)  NODE_ARCH="arm64";;
    armv7l)         NODE_ARCH="armv7l";;
    x86_64)         NODE_ARCH="x64";;
    *) echo "!!! Unsupported architecture: $(uname -m)"; exit 1;;
  esac
  TMP_NODE="$(mktemp -d)"
  curl -fsSL "https://nodejs.org/dist/$NODE_VERSION/node-$NODE_VERSION-linux-$NODE_ARCH.tar.xz" \
    -o "$TMP_NODE/node.tar.xz" || { echo "!!! Failed to download Node.js."; exit 1; }
  tar -xJf "$TMP_NODE/node.tar.xz" -C "$TMP_NODE"
  $SUDO cp -r "$TMP_NODE/node-$NODE_VERSION-linux-$NODE_ARCH"/{bin,include,lib,share} /usr/local/ \
    || { echo "!!! Failed to install Node.js into /usr/local."; exit 1; }
  rm -rf "$TMP_NODE"
  hash -r
fi
echo "==> Node $(node -v)"
have npm || { echo "!!! npm not found after Node.js install."; exit 1; }

# --------------------------------------------------------------- pnpm -------
if ! have pnpm; then
  echo "==> pnpm not found, installing..."
  if have corepack; then
    $SUDO corepack enable >/dev/null 2>&1 || true
  fi
  if ! have pnpm; then
    $SUDO npm i -g pnpm || { echo "!!! Failed to install pnpm."; exit 1; }
  fi
fi
echo "==> pnpm $(pnpm -v)"

# ------------------------------------------- temporary swap (if needed) -----
SWAP_TMP=""
cleanup() {
  if [ -n "${TMP_CLONE:-}" ] && [ -d "$TMP_CLONE" ]; then
    rm -rf "$TMP_CLONE"
  fi
  if [ -n "$SWAP_TMP" ] && [ -f "$SWAP_TMP" ]; then
    echo "==> Removing temporary swap..."
    $SUDO /usr/sbin/swapoff "$SWAP_TMP" 2>/dev/null || true
    $SUDO rm -f "$SWAP_TMP"
  fi
}
trap cleanup EXIT

if [ "$NO_SWAP" != 1 ]; then
  TOTAL_MEM_MB=$(( $(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo) \
                + $(awk '/SwapTotal/{print int($2/1024)}' /proc/meminfo) ))
  if [ "$TOTAL_MEM_MB" -lt "$MIN_BUILD_MEM_MB" ]; then
    DISK_FREE_MB=$(df -Pm /var/tmp | awk 'NR==2{print $4}')
    if [ "$DISK_FREE_MB" -gt $((SWAP_FILE_MB + 1000)) ]; then
      echo "==> Low memory (${TOTAL_MEM_MB} MB) - adding temporary ${SWAP_FILE_MB} MB swap for the build..."
      SWAP_TMP="/var/tmp/fluidd-ace-build.swap"
      $SUDO fallocate -l "${SWAP_FILE_MB}M" "$SWAP_TMP" \
        || $SUDO dd if=/dev/zero of="$SWAP_TMP" bs=1M count="$SWAP_FILE_MB"
      $SUDO chmod 600 "$SWAP_TMP"
      $SUDO /usr/sbin/mkswap "$SWAP_TMP" >/dev/null
      $SUDO /usr/sbin/swapon "$SWAP_TMP"
    else
      echo "!!! Low memory and not enough disk for a temporary swap - the build may fail."
    fi
  fi
fi

# ------------------------------------------------------- build workspace ----
# Build on a disk-backed filesystem: /tmp is often a small tmpfs, and the
# build (clone + node_modules) needs ~3 GB.
FLUIDD_DIR_PARENT="$(dirname "$FLUIDD_DIR")"
FREE_MB="$(df -Pm "$FLUIDD_DIR_PARENT" | awk 'NR==2{print $4}')"
if [ "${FREE_MB:-0}" -le 3500 ]; then
  echo "!!! Need ~3.5 GB free disk in $FLUIDD_DIR_PARENT for the build (have ${FREE_MB:-0} MB)."
  exit 1
fi

# --- 1. Fluidd sources ---
if [ ! -d "$FLUIDD_DIR/.git" ]; then
  echo "==> Cloning Fluidd..."
  rm -rf "$FLUIDD_DIR"
  git clone --depth 50 https://github.com/fluidd-core/fluidd.git "$FLUIDD_DIR"
fi

# --- 2. ACE panel sources ---
echo "==> Copying ACE panel sources..."
mkdir -p "$FLUIDD_DIR/src/mixins" "$FLUIDD_DIR/src/components/widgets/ace"
cp "$PKG_DIR/src/mixins/ace.ts" "$FLUIDD_DIR/src/mixins/ace.ts"
cp "$PKG_DIR/src/components/widgets/ace/AceCard.vue" "$FLUIDD_DIR/src/components/widgets/ace/AceCard.vue"

# --- 3. Patch Dashboard.vue / layout state ---
echo "==> Patching Fluidd..."
(cd "$FLUIDD_DIR" && python3 "$PKG_DIR/apply_fluidd_ace.py")

# --- 4. Icon alias used by the card menu ---
G="$FLUIDD_DIR/src/globals.ts"
if ! grep -q "dotsVertical:" "$G"; then
  sed -i 's/^  dotsGrid: mdiDotsGrid,$/  dotsGrid: mdiDotsGrid,\n  dotsVertical: mdiDotsVertical,/' "$G"
fi

# --- 5. Build ---
echo "==> Installing dependencies (pnpm install)..."
(cd "$FLUIDD_DIR" && pnpm install)
echo "==> Building (this takes 15-40 min on a Pi)..."
(cd "$FLUIDD_DIR" && pnpm run build)

# --- 6. Deploy (with rollback on failure) ---
BACKUP="$WEBROOT.bak-ace-$(date +%Y-%m-%d-%H%M%S)"
echo "==> Deploying to $WEBROOT (backup: $BACKUP)..."
cp -r "$WEBROOT" "$BACKUP"

rollback() {
  echo "!!! Deployment failed - rolling back from $BACKUP ..."
  rm -rf "$WEBROOT"
  cp -r "$BACKUP" "$WEBROOT"
  echo "!!! Rolled back. Check the build log above for the failure reason."
  exit 1
}

cp -r "$FLUIDD_DIR/dist/." "$WEBROOT/" || rollback

# Restore ownership when run through sudo
if [ -n "${SUDO_USER:-}" ]; then
  chown -R "$SUDO_USER":"$(id -gn "$SUDO_USER")" "$WEBROOT" || true
fi

# --- 7. Verify ---
echo "==> Verifying deployment..."
if ! grep -rql "ACE_SET_SLOT" "$WEBROOT" --include='*.js' 2>/dev/null; then
  echo "!!! The deployed bundle does not contain the ACE panel code."
  rollback
fi
echo "    ACE panel code is present in $WEBROOT."

if [ "$KEEP_REPO" != "1" ]; then
  echo "==> Removing build tree ($FLUIDD_DIR)..."
  rm -rf "$FLUIDD_DIR"
fi

echo ""
echo "Done! Hard-reload Fluidd in your browser: Ctrl+Shift+R"
echo "(If the card does not appear, clear the site data / service worker"
echo " in DevTools -> Application -> Storage, then reload.)"
