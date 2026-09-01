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
# The tree/ref guard exists because those two facts do not agree by themselves.
# Scope is decided from the outgoing REFS, but both halves then check the WORKING
# TREE (`git rev-parse --show-toplevel`) — and those are the same thing only when
# the ref being pushed is what this worktree has checked out. With many
# `git worktree`s in play they routinely are not: `git push origin some-branch`
# from a checkout sitting on `main` scopes the gate by some-branch's diff and then
# validates main's tree. It goes green and proves nothing about what shipped.
# Batching pushes (`git push origin br1 br2 br3`) to pay the ~20-minute `npm ci` +
# render-smoke cost once instead of three times is exactly how that happens. So
# every outgoing ref must resolve to this worktree's HEAD commit or the push is
# refused — one push per worktree, from the worktree that owns the branch. Refs are
# peeled to a commit first (`^{commit}`): an annotated tag's own object SHA is never
# a commit, and tagging the merge commit on `main` from a `main` checkout is the
# documented release step (docs/maintainers/release-runbook.md).
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
  # gate unconditionally rather than blocking on read. Nothing named a ref here,
  # so the tree/ref guard below has nothing to compare and correctly never runs:
  # a manual run is a check of this tree, which is exactly what it claims to be.
  needs_gate=1
  needs_lint=1
fi
head_commit=$(git rev-parse --verify HEAD 2>/dev/null || echo unknown)
while [ ! -t 0 ] && read -r local_ref local_sha _remote_ref remote_sha; do
  [ "$local_sha" = "$ZERO" ] && continue  # branch deletion — nothing outgoing
  # Refuse to gate a tree that is not the thing being pushed. Peel to a commit so
  # an annotated tag compares as the commit it points at, and fall back to the raw
  # SHA (which cannot equal HEAD) so an unpeelable ref is refused, not crashed on.
  pushed_commit=$(git rev-parse --verify --quiet "$local_sha^{commit}" || echo "$local_sha")
  if [ "$pushed_commit" != "$head_commit" ]; then
    echo "" >&2
    echo "pre-push: refusing to gate a tree that is not what you are pushing." >&2
    echo "          $local_ref resolves to $pushed_commit" >&2
    echo "          this worktree's HEAD is $head_commit" >&2
    echo "          Both halves of this gate check the working tree, not the pushed" >&2
    echo "          commits, so running them here would prove nothing about what ships." >&2
    echo "          Fix: push from the worktree that has that ref checked out — see" >&2
    echo "          'git worktree list' — and push one ref per worktree rather than" >&2
    echo "          batching several refs into one push." >&2
    exit 1
  fi
  # Scope by what this branch ADDS, not by how far its base has moved. Rebasing
  # rewrites a branch onto a newer `main`, so `$remote_sha..$local_sha` spans every
  # commit main gained while the branch waited. Measured on a one-commit backend PR that
  # had sat four days: that range held 37 commits touching 96 frontend files, while the
  # branch's own diff was 1 commit touching none — so a backend-only push paid the
  # ~20-minute render-smoke chain for somebody else's web change, which had already been
  # gated when it landed. The merge-base range is the branch's own contribution, and is
  # what CI scopes a PR by.
  #
  # It cannot under-run: the only commits it drops are ancestors of origin/main, i.e.
  # already on main and already gated. It over-runs for a stacked branch (the range then
  # includes the parent PR's commits too), which is the safe direction. When origin/main
  # is missing — a fresh clone that has not fetched — fall back to the remote range, and
  # when there is no remote range either, gate unconditionally rather than skip blind.
  if base=$(git merge-base "$local_sha" origin/main 2>/dev/null); then
    range="$base..$local_sha"
  elif [ "$remote_sha" != "$ZERO" ]; then
    range="$remote_sha..$local_sha"
  else
    needs_gate=1
    needs_lint=1
    continue
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
