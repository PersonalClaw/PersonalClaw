#!/bin/sh
# Pre-push gate — Python lint + the frontend render-smoke rail (invoked by
# .githooks/pre-push).
#
# The render-smoke half was born from the v0.1.0 blank-dashboard release:
# typecheck + vitest + vite build all passed while the built bundle crashed at
# first render in a real browser (dual-React dependency skew). That gate re-runs
# the whole frontend chain from a CLEAN, locked install — `npm ci` is the step
# that catches declared-vs-resolved tree skew — then mounts the built SPA in
# headless Chromium (scripts/render_smoke.mjs) before the push leaves the machine.
#
# The Python lint half exists because pre-commit only formats the files a commit
# STAGES: commits made before `hooks:install`, or with `--no-verify`, sail past it
# and CI's `lint` job is the first thing that notices. This re-checks the whole
# tree under the same tools CI uses, so a lint break is caught before the push.
#
# Scope: each half runs ONLY when the outgoing commits touch paths it owns, so a
# backend-only push skips the frontend chain and vice versa. Ref ranges arrive on
# stdin per githooks(5).
#
# Bypass: none built in. `git push --no-verify` exists for owner-declared
# emergencies only (AGENTS.md) — a red gate means the push ships something broken.
set -eu

repository_root=$(git rev-parse --show-toplevel)
cd "$repository_root"

FRONTEND_PATHS="web package.json package-lock.json scripts/render_smoke.mjs scripts/run_prepush.sh"
PYTHON_PATHS="src/personalclaw tests harness pyproject.toml"
ZERO=0000000000000000000000000000000000000000

needs_gate=0
needs_lint=0
if [ -t 0 ]; then
  # Manual invocation from a terminal (no ref ranges on stdin) — run the full
  # gate unconditionally rather than blocking on read.
  needs_gate=1
  needs_lint=1
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
      needs_lint=1
      continue
    fi
  else
    range="$remote_sha..$local_sha"
  fi
  # shellcheck disable=SC2086 — FRONTEND_PATHS is a deliberate word list
  if [ -n "$(git diff --name-only "$range" -- $FRONTEND_PATHS 2>/dev/null || echo changed)" ]; then
    needs_gate=1
  fi
  # shellcheck disable=SC2086 — PYTHON_PATHS is a deliberate word list
  if [ -n "$(git diff --name-only "$range" -- $PYTHON_PATHS 2>/dev/null || echo changed)" ]; then
    needs_lint=1
  fi
done

# Python lint, same tools and scope as CI's `lint` job. Resolve from the in-repo
# venv (what the Makefile uses) and fall back to PATH; if neither has the dev
# tools, say so and let the push through rather than blocking on a missing venv —
# CI still enforces it.
if [ "$needs_lint" -eq 1 ]; then
  if [ -x .venv/bin/black ]; then
    PY_BIN=".venv/bin/"
  elif command -v black >/dev/null 2>&1 && command -v isort >/dev/null 2>&1 \
    && command -v flake8 >/dev/null 2>&1; then
    PY_BIN=""
  else
    PY_BIN="MISSING"
  fi

  if [ "$PY_BIN" = "MISSING" ]; then
    echo "pre-push: python changes outgoing but dev tools not found — skipping lint."
    echo "          Install them with: pip install -e '.[dev]'   (CI still checks.)"
  else
    echo "pre-push: python changes outgoing — checking lint (black, isort, flake8)."
    if ! "${PY_BIN}black" --check --quiet src/personalclaw tests harness \
      || ! "${PY_BIN}isort" --check-only --quiet src/personalclaw tests harness \
      || ! "${PY_BIN}flake8" src/personalclaw tests harness; then
      echo "" >&2
      echo "pre-push: lint is red — run 'make format' then 'make lint', and commit the" >&2
      echo "          result before pushing. (CI's lint job checks the same thing.)" >&2
      exit 1
    fi
    echo "pre-push: python lint green."
  fi
fi

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
