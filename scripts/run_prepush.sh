#!/bin/sh
# Pre-push gate — the frontend render-smoke rail (invoked by .githooks/pre-push).
#
# Born from the v0.1.0 blank-dashboard release: typecheck + vitest + vite build
# all passed while the built bundle crashed at first render in a real browser
# (dual-React dependency skew). This gate re-runs the whole frontend chain from
# a CLEAN, locked install — `npm ci` is the step that catches declared-vs-
# resolved tree skew — and then mounts the built SPA in headless Chromium
# (scripts/render_smoke.mjs) before the push leaves the machine.
#
# Scope: runs ONLY when the outgoing commits touch frontend-affecting paths
# (web/, the root npm manifest/lockfile, or the smoke harness itself), so
# backend-only pushes stay fast. Ref ranges arrive on stdin per githooks(5).
#
# Bypass: none built in. `git push --no-verify` exists for owner-declared
# emergencies only (AGENTS.md) — a red gate means the push ships a broken SPA.
set -eu

repository_root=$(git rev-parse --show-toplevel)
cd "$repository_root"

FRONTEND_PATHS="web package.json package-lock.json scripts/render_smoke.mjs scripts/run_prepush.sh"
ZERO=0000000000000000000000000000000000000000

needs_gate=0
if [ -t 0 ]; then
  # Manual invocation from a terminal (no ref ranges on stdin) — run the full
  # gate unconditionally rather than blocking on read.
  needs_gate=1
fi
while [ ! -t 0 ] && read -r _local_ref local_sha _remote_ref remote_sha; do
  [ "$local_sha" = "$ZERO" ] && continue  # branch deletion — nothing outgoing
  if [ "$remote_sha" = "$ZERO" ]; then
    # New remote branch: compare against the shared history with origin/main
    # when we have it; otherwise gate unconditionally rather than skip blind.
    if base=$(git merge-base "$local_sha" origin/main 2>/dev/null); then
      range="$base..$local_sha"
    else
      needs_gate=1
      continue
    fi
  else
    range="$remote_sha..$local_sha"
  fi
  # shellcheck disable=SC2086 — FRONTEND_PATHS is a deliberate word list
  if [ -n "$(git diff --name-only "$range" -- $FRONTEND_PATHS 2>/dev/null || echo changed)" ]; then
    needs_gate=1
  fi
done

if [ "$needs_gate" -eq 0 ]; then
  echo "pre-push: no frontend changes outgoing — render-smoke gate skipped."
  exit 0
fi

echo "pre-push: frontend changes outgoing — running the render-smoke gate"
echo "          (clean npm ci -> typecheck -> vitest -> build -> headless render)."

npm ci
npm run typecheck:web
npm run test:web
npm run build
npx playwright install chromium
npm run smoke:render

echo "pre-push: render-smoke gate green."
