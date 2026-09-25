#!/usr/bin/env bash
# Execute the PACKAGED backend hard enough to catch an incomplete bundle. Release-blocking.
#
# `release.yml`'s desktop jobs already mounted the artefact, asserted `app.asar` and the
# backend binary existed, and ran `"$BACKEND" --version`. That last line is the only one
# that executed real code, and `--version` is argparse: it prints and exits BEFORE the
# runner catalogue is read, before any `personalclaw.sdk.*` submodule is imported, and
# before an MCP server command is resolved. So the 2026-09-23 dmg passed this smoke while
# being, in the packaged form the owner installed, unable to enable a single extension
# and running its agent with no core tools.
#
# This boots the packaged gateway on a THROWAWAY home, drives the API surfaces that force
# the reads, and then fails on any signature of an incomplete bundle. Every signature below
# was observed in a real `gateway.log`, so none of them is invented.
#
# It lives in `scripts/` rather than inline in the workflow because `desktop-mac` and
# `desktop-linux` need the identical assertion, and two inline copies of a growing list is
# the exact rot that produced these findings — `personalclaw-backend.spec` had
# hand-transcribed the wheel's package-data globs and drifted eleven of thirty.
#
#   usage: scripts/smoke_backend_bundle.sh <path to personalclaw-backend>
#
# MEASURED both directions on 2026-09-23, two bundles built from one tree with only the
# spec differing: RED on the pre-fix bundle (`ModuleNotFoundError: No module named
# 'personalclaw.sdk.tool'` + `Failed to enable extension personalclaw-ui-docs`), GREEN on
# the post-fix bundle. An assertion added after the fix, never seen failing, is not a test.

set -euo pipefail

BACKEND="${1:?usage: scripts/smoke_backend_bundle.sh <path to personalclaw-backend>}"
test -x "$BACKEND" || { echo "smoke: $BACKEND is not executable"; exit 1; }

# The cheap check, kept: a bundle that cannot even print its version is broken outright.
"$BACKEND" --version

# An isolated home. NEVER the real one: this boots a gateway and seeds bundled apps, and a
# release smoke must not write to whatever ~/.personalclaw happens to be on the runner.
SMOKE_HOME="$(mktemp -d)/fresh"
mkdir -p "$SMOKE_HOME"
BOOT_OUT="$(mktemp)"
echo "smoke: home=$SMOKE_HOME"

# Spawn the gateway the way the SHELL does, minus the shell. `desktop/main.js:177` passes
# `projectDir: path.resolve(__dirname, "..")` — i.e. `…/Contents/Resources` — and without
# it the `git fetch` defect is UNREACHABLE: `_do_update_check` returns at its
# "no project dir" guard, so that arm of this smoke would pass vacuously. Measured: with
# this line the pre-fix bundle logs `not a git repository`; without it, it does not.
#
# `PERSONALCLAW_INSTALL_KIND` is deliberately NOT set. The shell does set it, but the
# taxonomy must also answer correctly from the artefact alone — with the env unset a frozen
# bundle used to classify itself as a `pip` install — so leaving it out tests the harder
# path and the shell's own declaration is covered by `desktop/test/gatewayEnv.test.js`.
PROJECT_DIR="$(cd "$(dirname "$BACKEND")/../.." && pwd)"
echo "smoke: project dir=$PROJECT_DIR (what the Electron shell passes)"

# AUTH_MODE=none forces a loopback-only bind, so the probes below need no token and the
# surface is not exposed off-host for the duration of the smoke.
PERSONALCLAW_HOME="$SMOKE_HOME" PERSONALCLAW_AUTH_MODE=none \
PERSONALCLAW_PROJECT_DIR="$PROJECT_DIR" \
  "$BACKEND" gateway --port auto --no-open --json-ready >"$BOOT_OUT" 2>&1 &
GATEWAY_PID=$!
cleanup() {
  kill "$GATEWAY_PID" 2>/dev/null || true
  sleep 2
  kill -9 "$GATEWAY_PID" 2>/dev/null || true
}
trap cleanup EXIT

PORT=""
for _ in $(seq 1 120); do
  PORT="$(grep -o '"port"[[:space:]]*:[[:space:]]*[0-9]*' "$BOOT_OUT" 2>/dev/null \
          | head -1 | grep -o '[0-9]*$' || true)"
  [ -n "$PORT" ] && break
  # A dead child will never print a port; stop waiting the full two minutes for it.
  kill -0 "$GATEWAY_PID" 2>/dev/null || break
  sleep 1
done

if [ -z "$PORT" ]; then
  echo "smoke: FAIL — the packaged gateway never became ready"
  echo "--- stdout/stderr ---"; cat "$BOOT_OUT"
  echo "--- gateway.log ---"; cat "$SMOKE_HOME/gateway.log" 2>/dev/null || echo "(none)"
  exit 1
fi
echo "smoke: gateway ready on port $PORT"

# Drive the surfaces that force the reads a bare `--version` never reaches. Each is
# asserted on its HTTP status as well, because an unreachable gateway produces a CLEAN log
# and a clean log is what this script treats as success — a probe that silently failed to
# connect would turn the whole smoke vacuous.
#
#   /api/agent-runners      -> agents/runner_catalog.json (the shipped BYO-runner catalogue)
#   /api/apps               -> the installed-app set, i.e. every enabled extension's
#                              `personalclaw.sdk.*` import and its provider module
#   /api/config/personalclaw-> the config load + validation pass
#   /api/update/check       -> the install-kind decision and the update probe
for route in /api/agent-runners /api/apps /api/config/personalclaw /api/update/check; do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 60 \
          "http://127.0.0.1:$PORT$route" || echo 000)"
  echo "smoke: GET $route -> $code"
  case "$code" in
    2*) ;;
    *) echo "smoke: FAIL — $route answered $code; the probe proved nothing"; exit 1 ;;
  esac
done

cleanup
trap - EXIT
sleep 1

LOG="$SMOKE_HOME/gateway.log"
test -f "$LOG" || { echo "smoke: FAIL — no gateway.log was written"; exit 1; }
echo "smoke: gateway.log is $(wc -l <"$LOG") lines"

# Every one of these was in the owner's 2026-09-23 log. A packaged bundle that produces
# any of them is broken in a way no source-tree test can see, because in a checkout the
# files these name are simply on disk.
SIGNATURES=(
  "ModuleNotFoundError"                        # an sdk.* (or any) submodule missing
  "Failed to enable extension"                 # the consequence: a dead app platform
  "FileNotFoundError"                          # a package data file missing
  "Dropping MCP server"                        # the agent lost a tool surface
  "Could not resolve personalclaw binary"      # ...because the bundle has no console script
  "runner catalog: shipped"                    # the catalogue read failed
  "projection rule pack unreadable"            # the builtin rule pack read failed
  "not a git repository"                       # git shelled out inside a bundle
  "unrecognized top-level keys"                # a config key nothing recognizes
)
failed=0
for sig in "${SIGNATURES[@]}"; do
  if grep -qF "$sig" "$LOG"; then
    echo "smoke: FAIL — packaged gateway logged: $sig"
    grep -nF "$sig" "$LOG" | head -5
    failed=1
  fi
done

if [ "$failed" -ne 0 ]; then
  echo "--- full gateway.log ---"
  cat "$LOG"
  exit 1
fi

echo "smoke: PASS — the packaged backend booted and logged none of the ${#SIGNATURES[@]} incomplete-bundle signatures"
