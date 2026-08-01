#!/usr/bin/env bash
# One-time heavy provisioning of the Goose desktop app for e2e testing.
# Idempotent: every step is skipped if its output already exists.
# This step is REPO-AGNOSTIC - it only builds Goose + Electron, so the build under
# $E2E_HOME/goose_src is shared with any other goose-gui-e2e harness on the machine.
#
# Encodes the sandbox-specific fixes so a future session hits ZERO of the errors
# we hit discovering them:
#   - goose is BUILT from source (release-asset download is egress-blocked; `git clone` is not)
#   - Electron binary comes from the OFFICIAL GitHub release assets, checksum-verified
#   - pnpm install overrides @electron/node-gyp to a registry build (its git tarball 403s on codeload)
#   - the verified Electron binary is placed into node_modules so electron-forge/Playwright use it
#   - the dev main-process bundle is produced so Playwright can launch the app
#
# PRECONDITION (do this in the environment settings, once): Network access = Custom with
#   github.com                             (issues the release 302; also used by git clone)
#   release-assets.githubusercontent.com   (Azure-backed asset CDN the 302 points to)
# added (keep the default package-manager list checked). Without it, the Electron download fails.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

GOOSE_REPO_URL="${GOOSE_REPO_URL:-https://github.com/block/goose.git}"
# Electron binaries are pulled from the OFFICIAL GitHub release assets (see step 2).
# github.com 302-redirects to release-assets.githubusercontent.com (Azure-backed); both
# hosts must be on the environment's Custom network allowlist.
ELECTRON_RELEASE_BASE="https://github.com/electron/electron/releases/download"

log "E2E_HOME=$E2E_HOME  GOOSE_SRC=$GOOSE_SRC"

# ---- preflight: both download hosts reachable? (the one thing a human must enable) ----
# A non-allowlisted host fails to connect (curl code 000); a reachable one answers with a
# real status (even 400/404 for the bare host root), so 000 is the only signal we treat
# as "missing". BOTH hosts are probed: the Electron download starts at github.com and
# 302s to the Azure-backed asset CDN. Don't be tempted to let step 1's `git clone` stand
# in for the github.com check - it is skipped whenever the shared Goose build already
# exists, which is the common case on a re-run, and the download would then fail with a
# bare curl error instead of the allowlist guidance below.
PF_MISSING=""
for pf_host in github.com release-assets.githubusercontent.com; do
  pf_code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "https://$pf_host/" 2>/dev/null || true)"
  if [ -z "$pf_code" ] || [ "$pf_code" = "000" ]; then
    PF_MISSING="$PF_MISSING $pf_host"
  fi
done
if [ -n "$PF_MISSING" ]; then
  echo "FATAL: host(s) not reachable:$PF_MISSING"
  echo "  Add$PF_MISSING to the environment's Custom network allowlist."
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

# ---- 2. Electron binary from the official GitHub release assets, checksum-verified ----
EV="$(grep -m1 '"electron":' "$DESK/package.json" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
log "pinned Electron version: $EV"
if [ ! -x "$ELECTRON_BIN" ]; then
  DL="$E2E_HOME/electron-dl"; mkdir -p "$DL"
  BASE="${ELECTRON_RELEASE_BASE}/v${EV}"
  ZIP="electron-v${EV}-linux-x64.zip"
  log "downloading $ZIP from official GitHub release assets"
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
