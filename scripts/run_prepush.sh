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
PYTHON_PATHS="src/personalclaw tests harness scripts pyproject.toml"
ZERO=0000000000000000000000000000000000000000

needs_gate=0
needs_lint=0
# Is any outgoing ref RELEASE-relevant (`main`, a release branch, or a tag)? Those keep the
# full local render-smoke chain; ordinary topic branches let CI be the gate. Ruling recorded
# above the gate block below.
release_ref=0
if [ -t 0 ]; then
  # Manual invocation from a terminal (no ref ranges on stdin) — run the full
  # gate unconditionally rather than blocking on read. Nothing named a ref here,
  # so the tree/ref guard below has nothing to compare and correctly never runs:
  # a manual run is a check of this tree, which is exactly what it claims to be.
  needs_gate=1
  needs_lint=1
  # A manual run is someone deliberately asking for the real gate, so it counts as
  # release-grade and is never downgraded to "CI will catch it".
  release_ref=1
fi
head_commit=$(git rev-parse --verify HEAD 2>/dev/null || echo unknown)
while [ ! -t 0 ] && read -r local_ref local_sha _remote_ref remote_sha; do
  [ "$local_sha" = "$ZERO" ] && continue  # branch deletion — nothing outgoing
  case "$local_ref" in
    refs/heads/main|refs/heads/release/*|refs/tags/*) release_ref=1 ;;
  esac
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
  # Scope by what this BRANCH adds on top of `main`, never by `remote_sha..local_sha`.
  # The two are the same question only while the branch is a fast-forward of its remote.
  # After a rebase (or any force-push) the old remote tip is no longer an ancestor, and
  # `remote_sha..local_sha` diffs the two trees — dragging in every `main` commit that
  # landed in between, including other people's frontend work. Measured on a three-file
  # backend-only branch: 94 frontend files in that range versus 0 the branch touches, so
  # the expensive half ran for ten minutes and then failed on web tests the diff could
  # not have affected (issue 2269). Rebasing before a push is routine, so this was the
  # common case rather than an edge one.
  #
  # A stale local `origin/main` makes the merge-base too OLD, which widens the range and
  # gates more. That is the safe direction; the reverse would skip a real change. Below is
  # the preference order — best available answer first, each falling back only when the one
  # above it cannot be computed at all.
  if base=$(git merge-base "$local_sha" origin/main 2>/dev/null); then
    range="$base..$local_sha"
  elif [ "$remote_sha" != "$ZERO" ]; then
    # No origin/main to measure against (a fork that tracks `upstream`, say). The old
    # range is the best answer left: exact while the branch is a fast-forward of its
    # remote, and too WIDE after a rewrite — which over-gates, never under-gates.
    range="$remote_sha..$local_sha"
  else
    # A new branch AND no shared history: nothing to compare. Gate rather than skip blind.
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

report_dir=$(mktemp -d "${TMPDIR:-/tmp}/personalclaw-prepush.XXXXXX")
trap 'rm -rf "$report_dir"' EXIT HUP INT TERM
results_file="$report_dir/results"
: >"$results_file"
result_count=0
failed_count=0
skipped_count=0
last_result=""

record_result() {
  record_name=$1
  record_status=$2
  record_reason=$3
  record_log=$4
  result_count=$((result_count + 1))
  detail_file="$report_dir/detail-$result_count"
  {
    printf '%s\n' "$record_reason"
    if [ -n "$record_log" ] && [ -s "$record_log" ]; then
      cat "$record_log"
    fi
  } >"$detail_file"
  printf '%s|%s|%s\n' "$record_name" "$record_status" "$detail_file" >>"$results_file"
  last_result=$record_status
  case "$record_status" in
    FAIL) failed_count=$((failed_count + 1)) ;;
    SKIP) skipped_count=$((skipped_count + 1)) ;;
  esac
}

run_check() {
  check_name=$1
  shift
  check_log="$report_dir/check-$((result_count + 1)).log"
  if ! "$@" >"$check_log" 2>&1; then
    record_result "$check_name" "FAIL" "command exited non-zero: $*" "$check_log"
    return
  fi
  record_result "$check_name" "PASS" "" ""
}

skip_check() {
  record_result "$1" "SKIP" "$2" ""
}

skip_python_checks() {
  skip_reason=$1
  skip_check "black" "$skip_reason"
  skip_check "isort" "$skip_reason"
  skip_check "flake8" "$skip_reason"
}

skip_frontend_checks() {
  skip_reason=$1
  skip_check "npm-ci" "$skip_reason"
  skip_check "typecheck-web" "$skip_reason"
  skip_check "test-web" "$skip_reason"
  skip_check "build-web" "$skip_reason"
  skip_check "playwright-chromium" "$skip_reason"
  skip_check "render-smoke" "$skip_reason"
}

render_report() {
  echo ""
  echo "Gate                  | Result | Failures"
  echo "-----------------------------------------"
  while IFS='|' read -r result_name result_status detail_file; do
    if [ "$result_status" = "FAIL" ]; then
      result_failures=1
    else
      result_failures=0
    fi
    printf '%-21s | %-6s | %s\n' "$result_name" "$result_status" "$result_failures"
  done <"$results_file"

  while IFS='|' read -r result_name result_status detail_file; do
    [ "$result_status" = "PASS" ] && continue
    echo ""
    echo "$result_name $result_status:"
    sed 's/^/  /' "$detail_file"
  done <"$results_file"

  echo ""
  if [ "$failed_count" -gt 0 ]; then
    echo "SUMMARY: $failed_count of $result_count gate(s) FAILED; $skipped_count gate(s) SKIPPED."
  elif [ "$skipped_count" -gt 0 ]; then
    passed_count=$((result_count - skipped_count))
    echo "SUMMARY: all $passed_count executed gate(s) passed; $skipped_count gate(s) SKIPPED."
  else
    echo "SUMMARY: all $result_count gate(s) passed."
  fi
}

finish_report() {
  render_report
  if [ "$failed_count" -gt 0 ]; then
    aggregate_exit=1
  else
    aggregate_exit=0
  fi
  exit "$aggregate_exit"
}

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
    skip_python_checks "python dev tools (black/isort/flake8) not found; CI still checks"
  else
    echo "pre-push: python changes outgoing — checking lint (black, isort, flake8)."
    lint_failures_before=$failed_count
    run_check "black" "${PY_BIN}black" --check --quiet src/personalclaw tests harness scripts
    run_check "isort" "${PY_BIN}isort" --check-only --quiet src/personalclaw tests harness scripts
    run_check "flake8" "${PY_BIN}flake8" src/personalclaw tests harness scripts
    if [ "$failed_count" -ne "$lint_failures_before" ]; then
      echo "" >&2
      echo "pre-push: lint is red — run 'make format' then 'make lint', and commit the" >&2
      echo "          result before pushing. (CI's lint job checks the same thing.)" >&2
    else
      echo "pre-push: python lint green."
    fi
  fi
else
  skip_python_checks "no Python changes outgoing"
fi

if [ "$needs_gate" -eq 0 ]; then
  echo "pre-push: no frontend changes outgoing — render-smoke gate skipped."
  skip_frontend_checks "no frontend changes outgoing"
  finish_report
fi

# The render-smoke chain is npm/node all the way down (npm ci -> typecheck ->
# vitest -> vite build -> npx playwright -> render smoke). Those tools are
# mise-managed here and are NOT on the default PATH, so a local push from a shell
# without them would die on `npm: command not found` and no frontend-touching
# branch could ever be pushed from this machine. Mirror the Python lint half
# above: if the toolchain is absent, say so and let the push through rather than
# blocking on a missing tool. CI runs this chain with npm/node present and still
# enforces it in full, so this weakens nothing that ships — it is local graceful
# degradation only.
if ! command -v npm >/dev/null 2>&1 || ! command -v node >/dev/null 2>&1; then
  echo "pre-push: frontend changes outgoing but web toolchain (npm/node) not found — skipping render-smoke locally (CI enforces it)."
  skip_frontend_checks "web toolchain (npm/node) not found; CI still checks"
  finish_report
fi

# Owner ruling 2026-09-18: on a TOPIC branch, CI is the render-smoke gate. This chain stays
# mandatory for `main`, `release/*` and tags.
#
# The chain costs ~20 minutes (the `npm ci` note at the top), and an autonomous worker's
# stream watchdog is 600s. Measured consequence: no worker could push a frontend branch at
# all — 72 of 184 remote branches touch `web/`, and 76 open issues are labelled
# `area:frontend`, so the single largest category of work could only be pushed by hand. A
# gate that cannot be satisfied is not protection; it is a queue that quietly stops moving.
#
# This does NOT weaken what ships, on exactly the reasoning the npm-absent branch above
# already relies on: CI runs this same chain with npm/node present and still enforces it in
# full on every pull request, and `main` is protected, so a frontend regression cannot reach
# a release without passing it. What moves is only WHERE the first failure is observed —
# minutes later in CI instead of before the push leaves the machine. The v0.1.0
# blank-dashboard release this rail was built for shipped from a RELEASE ref, and release
# refs are exactly what still run it here.
#
# Deliberately keyed on the outgoing REF, not on a flag or an env var: a bypass an agent can
# set is a bypass that gets set. Reverting is one line — drop this `release_ref` test.
if [ "$release_ref" -eq 0 ]; then
  echo "pre-push: frontend changes outgoing on a topic branch — CI is the render-smoke gate"
  echo "          (owner ruling 2026-09-18; main/release/tags still run it locally)."
  skip_frontend_checks "topic branch: CI is the render-smoke gate"
  finish_report
fi

echo "pre-push: frontend changes outgoing — running the render-smoke gate"
echo "          (clean npm ci -> typecheck -> vitest -> build -> headless render)."

run_check "npm-ci" npm ci
if [ "$last_result" = "PASS" ]; then
  run_check "typecheck-web" npm run typecheck:web
  run_check "test-web" npm run test:web
  run_check "build-web" npm run build
  build_result=$last_result
  run_check "playwright-chromium" npx playwright install chromium
  playwright_result=$last_result
  if [ "$build_result" = "PASS" ] && [ "$playwright_result" = "PASS" ]; then
    run_check "render-smoke" npm run smoke:render
  else
    skip_check "render-smoke" "requires both build-web and playwright-chromium to pass"
  fi
else
  skip_check "typecheck-web" "requires npm-ci to pass"
  skip_check "test-web" "requires npm-ci to pass"
  skip_check "build-web" "requires npm-ci to pass"
  skip_check "playwright-chromium" "requires npm-ci to pass"
  skip_check "render-smoke" "requires npm-ci, build-web, and playwright-chromium to pass"
fi

if [ "$failed_count" -eq 0 ]; then
  echo "pre-push: render-smoke gate green."
fi
finish_report
