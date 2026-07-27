#!/usr/bin/env bash
# One-time heavy provisioning of the Goose desktop app for e2e testing.
# Idempotent: every step is skipped if its output already exists.
# This step is REPO-AGNOSTIC - it only builds Goose + Electron, so the build under
# $E2E_HOME/goose_src is shared with any other goose-gui-e2e harness on the machine.
#
# Encodes the sandbox-specific fixes so a future session hits ZERO of the errors
# we hit discovering them:
#   - goose is BUILT from source (release-asset download is egress-blocked; `git clone` is not)
#   - Electron binary comes from the npmmirror MIRROR (GitHub release assets 403), checksum-verified
#   - pnpm install overrides @electron/node-gyp to a registry build (its git tarball 403s on codeload)
#   - the verified Electron binary is placed into node_modules so electron-forge/Playwright use it
#   - the dev main-process bundle is produced so Playwright can launch the app
#
# PRECONDITION (do this in the environment settings, once): Network access = Custom with
#   registry.npmmirror.com
#   cdn.npmmirror.com
# added (keep the default package-manager list checked). Without it, the Electron download 403s.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

GOOSE_REPO_URL="${GOOSE_REPO_URL:-https://github.com/block/goose.git}"
export ELECTRON_MIRROR="https://registry.npmmirror.com/-/binary/electron/"

log "E2E_HOME=$E2E_HOME  GOOSE_SRC=$GOOSE_SRC"

# ---- preflight: mirror reachable? (the one thing a human must enable) ----
if ! curl -sf --max-time 10 -o /dev/null "https://registry.npmmirror.com/-/binary/electron/"; then
  echo "FATAL: registry.npmmirror.com not reachable."
  echo "  Add registry.npmmirror.com + cdn.npmmirror.com to the environment's Custom network allowlist."
  exit 1
fi

# ---- 1. clone + build the goose CLI (the backend Goose desktop spawns as 'goose serve') ----
if [ ! -x "$GOOSE_BINARY" ]; then
  [ -d "$GOOSE_SRC/.git" ] || { log "cloning goose"; git clone --depth 1 "$GOOSE_REPO_URL" "$GOOSE_SRC"; }
  log "building goose CLI (portable-default: no local-inference/keyring) - ~5 min"
  ( cd "$GOOSE_SRC" && cargo build -p goose-cli --bin goose --no-default-features --features portable-default )
  log "goose CLI built: $($GOOSE_BINARY --version)"
else
  log "goose CLI present: $($GOOSE_BINARY --version)"
fi

# ---- 2. Electron binary from the mirror, checksum-verified ----
EV="$(grep -m1 '"electron":' "$DESK/package.json" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
log "pinned Electron version: $EV"
if [ ! -x "$ELECTRON_BIN" ]; then
  DL="$E2E_HOME/electron-dl"; mkdir -p "$DL"
  BASE="https://registry.npmmirror.com/-/binary/electron/v${EV}"
  ZIP="electron-v${EV}-linux-x64.zip"
  log "downloading $ZIP from mirror"
  curl -fsSL -o "$DL/$ZIP" "$BASE/$ZIP"
  curl -fsSL -o "$DL/SHASUMS256.txt" "$BASE/SHASUMS256.txt"
  # SHASUMS256.txt lists each file as `<hash> *<name>` (sha256sum binary-mode marker),
  # so match a space-or-star before the (dot-escaped) name, not a bare space.
  ZIP_RE="[ *]${ZIP//./\\.}\$"
  ( cd "$DL" && grep -E "$ZIP_RE" SHASUMS256.txt | sha256sum -c - ) || { echo "FATAL: Electron checksum mismatch"; exit 1; }
  log "checksum OK; extracting"
  rm -rf "$DL/dist"; mkdir -p "$DL/dist"
  ( cd "$DL/dist" && (command -v unzip >/dev/null && unzip -oq "../$ZIP" || python3 -c "import zipfile;zipfile.ZipFile('../$ZIP').extractall('.')") )
  chmod +x "$DL/dist/electron"
fi

# ---- 3. pnpm install the ui workspace (with the node-gyp override) ----
# Gate on a completion stamp, NOT on node_modules existing: a failed pnpm install
# leaves a partial node_modules behind, and gating on the dir would skip the retry.
PNPM_STAMP="$GOOSE_SRC/ui/node_modules/.daysurface-install-done"
if [ ! -f "$PNPM_STAMP" ]; then
  WS="$GOOSE_SRC/ui/pnpm-workspace.yaml"
  # Match the override KEY (quoted, with colon), not any mention: goose's file has a
  # *comment* about @electron/node-gyp that a bare `grep @electron/node-gyp` matches,
  # which would fool us into thinking the override is already there and skip inserting it.
  if ! grep -q "'@electron/node-gyp':" "$WS"; then
    log "adding @electron/node-gyp registry override (codeload git tarball is egress-blocked)"
    # insert under the existing `overrides:` block
    python3 - "$WS" <<'PY'
import sys,re
p=sys.argv[1]; s=open(p).read()
# subn returns the match count: if the `overrides:` block was reformatted upstream
# (e.g. `overrides: {}`) the sub silently no-ops, the override goes missing, and
# pnpm install later 403s on the codeload tarball with a misleading error. Fail here.
s,n=re.subn(r"(\noverrides:\n)", r"\1  '@electron/node-gyp': 10.2.0-electron.2\n", s, count=1)
if not n:
    sys.exit("FATAL: 'overrides:' block not found in pnpm-workspace.yaml - upstream goose layout changed; update setup.sh")
open(p,"w").write(s)
PY
  fi
  log "pnpm install (Electron binary download skipped; we supply a verified one)"
  ( cd "$GOOSE_SRC/ui" && ELECTRON_SKIP_BINARY_DOWNLOAD=1 PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm_config_engine_strict=false pnpm install --no-frozen-lockfile )
  touch "$PNPM_STAMP"  # only reached if pnpm install succeeded (set -e aborts otherwise)
fi

# ---- 4. place the verified Electron binary where the package expects it ----
if [ ! -x "$ELECTRON_BIN" ]; then
  log "installing verified Electron into node_modules"
  rm -rf "$GOOSE_SRC/ui/node_modules/electron/dist"
  cp -r "$E2E_HOME/electron-dl/dist" "$GOOSE_SRC/ui/node_modules/electron/dist"
  printf 'electron' > "$GOOSE_SRC/ui/node_modules/electron/path.txt"
fi
log "Electron: $($ELECTRON_BIN --no-sandbox --version 2>/dev/null | tail -1)"

# ---- 5. build the dev main-process bundle (.vite/build/main.js) that Playwright launches ----
if [ ! -f "$DESK/.vite/build/main.js" ]; then
  log "building desktop bundles via electron-forge start (dev), then stopping"
  ( cd "$DESK" && node scripts/i18n-compile.js >/dev/null 2>&1 || true )
  xvfb-run -a bash -c "cd '$DESK' && ELECTRON_DISABLE_SANDBOX=1 timeout 180 ../node_modules/.bin/electron-forge start -- --no-sandbox --disable-gpu >'$E2E_HOME/forge_build.log' 2>&1" &
  FPID=$!
  # wait (bounded) for the bundle to appear, then stop forge
  for _ in $(seq 1 90); do [ -f "$DESK/.vite/build/main.js" ] && break; sleep 2; done
  pkill -9 -P "$FPID" 2>/dev/null || true; pkill -9 -f 'electron-forge' 2>/dev/null || true; pkill -9 -f 'dist/electron' 2>/dev/null || true
  [ -f "$DESK/.vite/build/main.js" ] || { echo "FATAL: main.js not built (see $E2E_HOME/forge_build.log)"; exit 1; }
fi
log "desktop main bundle ready: $DESK/.vite/build/main.js"

log "SETUP COMPLETE - now: bash up.sh && bash run_test.sh settings_render"
