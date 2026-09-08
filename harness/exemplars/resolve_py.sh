# shellcheck shell=bash
# Interpreter resolution for the exemplar smoke scripts. SOURCED, never executed — hence a
# `shell` directive rather than a shebang (shellcheck SC2148); the sourcing scripts own that.
#
# Sets `PY` to a Python that actually has this repo's dev dependencies, or exits 1 saying how to
# supply one. Sourced by every `slice_*/smoke.sh` so the rule lives once — six copies of it is
# what let the bug below survive in all six at the same time.
#
# 🔴 WHY THIS EXISTS (#2718). The scripts used to resolve:
#
#     PY="${PERSONALCLAW_PY:-.venv/bin/python}"
#     [ -x "$PY" ] || PY="python3"
#
# `.venv/` lives in the PRIMARY checkout and a `git worktree` does not have one, so in any
# worktree the first path missed, the fallback took a bare `python3` off PATH with no
# `personalclaw` and no `jsonschema` installed, and the exemplar died on an ImportError. The
# failure is misleading in the worst way: it lands in `tests/test_harness_exemplars.py`, a file
# the change under review did not touch, and it reads like a dependency problem rather than a
# wrong interpreter. Three separate contributors each spent a diagnosis establishing "my diff is
# fine, this worktree has no venv" — and the conclusion was written into the contributor notes as
# folklore ("expect 3/11 red in harness-validate in any fresh worktree") rather than fixed.
#
# Worktrees are the NORMAL way to work in this repo, so that tax was paid by essentially everyone
# making an isolated change.
#
# TWO changes, because the issue is right that either would do and both are cheap:
#
#  1. `git rev-parse --git-common-dir` resolves the venv from the PRIMARY checkout. In a worktree
#     it points at the primary repo's `.git`, so its parent is the checkout that holds `.venv`. In
#     an ordinary clone it is just `.git` and the parent is the repo root — the old behaviour,
#     unchanged.
#  2. It FAILS LOUDLY instead of falling through to `python3`. A wrong interpreter cannot produce
#     a useful error, only a plausible one, so there is nothing to gain by continuing: an
#     unresolvable interpreter now names both knobs instead of surfacing 30 lines later as a
#     missing module.
#
# `PERSONALCLAW_PY` still wins outright when set, which is how CI and any non-venv setup pin it.

if [ -n "${PERSONALCLAW_PY:-}" ]; then
  PY="$PERSONALCLAW_PY"
else
  # This checkout first: in an ordinary clone this is the answer and no `git` call is needed.
  PY="$REPO_ROOT/.venv/bin/python"
  if [ ! -x "$PY" ]; then
    # Then the primary checkout, which is where a worktree's venv actually lives.
    _common_dir="$(git -C "$REPO_ROOT" rev-parse --git-common-dir 2>/dev/null || true)"
    if [ -n "$_common_dir" ]; then
      # `--git-common-dir` is relative (`.git`) in an ordinary clone and absolute in a worktree;
      # resolving it against REPO_ROOT handles both without branching on which one it gave.
      _primary="$(cd "$REPO_ROOT" && cd "$(dirname "$_common_dir")" 2>/dev/null && pwd || true)"
      [ -n "$_primary" ] && PY="$_primary/.venv/bin/python"
    fi
    unset _common_dir _primary
  fi
fi

if [ ! -x "$PY" ]; then
  {
    echo "harness smoke: no usable Python interpreter."
    echo
    echo "  PERSONALCLAW_PY is unset (or not executable), and no .venv/bin/python was found in"
    echo "    this checkout:      $REPO_ROOT"
    echo "    the primary one:    resolved via 'git rev-parse --git-common-dir'"
    echo
    echo "  A bare 'python3' is NOT used as a fallback on purpose: it lacks this repo's dev"
    echo "  dependencies, so the exemplar would fail on a missing module instead of on this."
    echo
    echo "  In a git worktree, point PERSONALCLAW_PY at the primary checkout's venv:"
    echo "    PERSONALCLAW_PY=/path/to/primary-checkout/.venv/bin/python \\"
    echo "      bash ${BASH_SOURCE[1]:-harness/exemplars/slice_N/smoke.sh}"
    echo
    echo "  Or create one here:  uv sync --locked --extra dev"
  } >&2
  exit 1
fi
