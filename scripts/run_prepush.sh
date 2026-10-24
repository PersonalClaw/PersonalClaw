#!/bin/sh
# Pre-push gate: the change tier's jobs, as CI runs them, from the catalogue (invoked by
# .githooks/pre-push; #3920).
#
# The hook runs `python -m tools.ci run change --pre-push --changed --working-tree --fail-fast
# --workers 2`: every job of the change tier in tools/ci/catalogue.toml that the outgoing commits
# touch, step for step as CI runs it, except a job whose entry says `prepush = false` (a test
# suite too slow for a push: CI is its gate). It runs no check command of its own, which
# tests/rails/test_ci_contract.py holds, so a job added to the change tier runs here with no edit
# to this file. Today that is `static`: lint, types, the asset licences and the installer's
# checks. A run admits each job by its memory (tools/ci/admission.py); here a job waits at most
# ADMISSION_WAIT seconds for it, not the run's half hour, so a push on a full machine is not held:
# the job is not run, the run is partial, and the push goes through. The run leaves this
# checkout's `.venv` alone, so a worktree that shares its clone's venv keeps it: off GitHub a uv
# job syncs a venv under the run's work folder (`_cache_env` in tools/ci/jobs.py).
#
# The tree/ref guard exists because two facts do not agree by themselves. Scope is decided from
# the outgoing REFS, but the jobs then check the WORKING TREE (`--working-tree`), and those are
# the same thing only when the ref being pushed is what this worktree has checked out. With many
# `git worktree`s in play they routinely are not: `git push origin some-branch` from a checkout
# sitting on `main` would scope the run by some-branch's diff and then check main's tree. It
# goes green and proves nothing about what shipped. So every outgoing ref must resolve to this
# worktree's HEAD commit or the push is refused: one push per worktree, from the worktree that
# owns the branch. Refs are peeled to a commit first (`^{commit}`), so an annotated tag compares
# as the commit it points at.
#
# Scope is what the branch adds on top of `main`: the merge base with `origin/main` is the run's
# `--base`. A stale `origin/main` makes the base too old, which runs more: the safe direction.
# With no `origin/main` (a fork tracking `upstream`) the old remote tip is the base, exact while
# the branch is a fast-forward of its remote and too wide after a rewrite. With neither, or a
# manual run from a terminal, every pre-push job of the tier runs.
#
# The run's exit decides the push: red (1) refuses it. A partial run (2: a job this machine
# cannot run, one that never got its memory, an interrupted one) lets it through and says so,
# since CI runs every job. With no Python 3.12 or newer to run tools/ci, the hook says so and
# lets the push through.
#
# Bypass: none built in. `git push --no-verify` exists for owner-declared emergencies only
# (AGENTS.md): a red gate means the push ships something broken.
set -eu

repository_root=$(git rev-parse --show-toplevel)
cd "$repository_root"

ZERO=0000000000000000000000000000000000000000
ADMISSION_WAIT=60

head_commit=$(git rev-parse --verify HEAD 2>/dev/null || echo unknown)
# The run's --base, once every outgoing ref has named the same one; "whole" runs every job.
base=""
scope=changed
outgoing=0
if [ -t 0 ]; then
  # Manual invocation from a terminal (no ref ranges on stdin): nothing named a ref, so the
  # guard has nothing to compare, and the run is a check of this tree, which is what it claims.
  scope=whole
  outgoing=1
fi
while [ ! -t 0 ] && read -r local_ref local_sha _remote_ref remote_sha; do
  [ "$local_sha" = "$ZERO" ] && continue  # branch deletion — nothing outgoing
  outgoing=1
  # Refuse to gate a tree that is not the thing being pushed. Peel to a commit so an annotated
  # tag compares as the commit it points at, and fall back to the raw SHA (which cannot equal
  # HEAD) so an unpeelable ref is refused, not crashed on.
  pushed_commit=$(git rev-parse --verify --quiet "$local_sha^{commit}" || echo "$local_sha")
  if [ "$pushed_commit" != "$head_commit" ]; then
    echo "" >&2
    echo "pre-push: refusing to gate a tree that is not what you are pushing." >&2
    echo "          $local_ref resolves to $pushed_commit" >&2
    echo "          this worktree's HEAD is $head_commit" >&2
    echo "          The run checks the working tree, not the pushed commits, so running" >&2
    echo "          it here would prove nothing about what ships." >&2
    echo "          Fix: push from the worktree that has that ref checked out — see" >&2
    echo "          'git worktree list' — and push one ref per worktree rather than" >&2
    echo "          batching several refs into one push." >&2
    exit 1
  fi
  if ref_base=$(git merge-base "$local_sha" origin/main 2>/dev/null); then
    :
  elif [ "$remote_sha" != "$ZERO" ]; then
    ref_base=$remote_sha
  else
    # A new branch AND no shared history: nothing to compare. Run every job rather than none.
    scope=whole
    continue
  fi
  if [ -z "$base" ]; then
    base=$ref_base
  elif [ "$base" != "$ref_base" ]; then
    scope=whole  # two refs, two bases: the run takes neither
  fi
done

if [ "$outgoing" -eq 0 ]; then
  echo "pre-push: nothing outgoing to check."
  exit 0
fi

# tools/ci is standard-library Python and needs 3.12 or newer: the repo's venv first, then a
# `python3` on PATH. Each candidate must actually start: a version-manager shim with no version
# set is on PATH and exits non-zero.
python=""
for candidate in .venv/bin/python python3; do
  case "$candidate" in
    */*) [ -x "$candidate" ] || continue ;;
    *) command -v "$candidate" >/dev/null 2>&1 || continue ;;
  esac
  if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 12))' >/dev/null 2>&1; then
    python=$candidate
    break
  fi
done
if [ -z "$python" ]; then
  echo "pre-push: no Python 3.12 or newer (.venv/bin/python or python3) to run the change tier;"
  echo "          the push goes through, and CI runs every job."
  exit 0
fi

if [ "$scope" = changed ] && [ -n "$base" ]; then
  set -- --changed --base "$base"
else
  set --
fi
echo "pre-push: running the change tier's pre-push jobs on the working tree ($*)."
started=$(date +%s)
status=0
"$python" -m tools.ci run change --pre-push "$@" --working-tree --fail-fast --workers 2 \
  --admission-wait "$ADMISSION_WAIT" || status=$?
echo "pre-push: the run took $(($(date +%s) - started))s."
case "$status" in
  0) exit 0 ;;
  2)
    echo "pre-push: the run is partial (above: a job that could not run here, or did not finish);"
    echo "          the push goes through, and CI runs every job."
    exit 0
    ;;
  *)
    echo "" >&2
    echo "pre-push: the change tier is red (above). Fix it and commit the result before pushing;" >&2
    echo "          CI runs the same jobs." >&2
    exit 1
    ;;
esac
