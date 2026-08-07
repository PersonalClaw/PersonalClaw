#!/bin/sh
# Pre-commit gate — Python formatting + lint (invoked by .githooks/pre-commit).
#
# Purpose: take the mechanical, auto-fixable checks off the contributor's plate.
# black and isort RUN IN WRITE MODE over the staged Python files and re-stage
# what they touch, so a commit lands already-formatted the way CI's `lint` job
# demands — the class of red (unsorted import, unformatted block) that otherwise
# fails CI after the fact and sends a contributor back for a fixup round.
#
# Scope mirrors the CI lint job (ci.yml): black + isort + flake8. mypy is
# deliberately NOT run here — a per-file type check without the full module
# graph produces false positives, and it is slow; it stays on CI and the full
# local gate. Run `make lint` for the complete check including mypy.
#
# Fast path: does nothing unless staged changes include a .py file, so
# frontend-only and docs commits are not slowed down.
#
# Bypass: `git commit --no-verify` for the rare case you must commit WIP that
# does not yet pass — CI still enforces the same checks on the PR.
set -eu

repository_root=$(git rev-parse --show-toplevel)
cd "$repository_root"

# Staged, still-present Python files (Added/Copied/Modified/Renamed — not
# Deleted), NUL-delimited so paths with spaces survive.
staged_py=$(git diff --cached --name-only --diff-filter=ACMR -z -- '*.py' | tr '\0' '\n')

if [ -z "$staged_py" ]; then
  exit 0
fi

echo "pre-commit: formatting + linting staged Python files"

# Resolve each dev tool to a concrete command. Prefer the in-repo virtualenv the
# Makefile and contributors use (.venv, populated by `pip install -e '.[dev]'`):
# black/isort/flake8 are dev extras, so a bare `uv run <tool>` would spin up an
# env WITHOUT them. Fall back to a tool already on PATH, else fail with guidance.
resolve() {
  if [ -x ".venv/bin/$1" ]; then
    echo ".venv/bin/$1"
  elif command -v "$1" >/dev/null 2>&1; then
    echo "$1"
  else
    echo "pre-commit: '$1' not found — install dev deps (pip install -e '.[dev]')" >&2
    echo "            or run 'make lint' manually, then commit." >&2
    exit 1
  fi
}

BLACK=$(resolve black)
ISORT=$(resolve isort)
FLAKE8=$(resolve flake8)

# 1) Auto-fix: black + isort write in place over the staged files, then re-stage
#    exactly those files so the formatting is part of THIS commit. NUL-delimited
#    piping keeps paths with spaces intact.
printf '%s\n' "$staged_py" | tr '\n' '\0' | xargs -0 "$BLACK" --quiet
printf '%s\n' "$staged_py" | tr '\n' '\0' | xargs -0 "$ISORT"
printf '%s\n' "$staged_py" | tr '\n' '\0' | xargs -0 git add

# 2) Check-only: flake8 cannot auto-fix, so a violation blocks the commit with a
#    pointer to the fix. Runs after the formatters so it never trips on a
#    formatting issue black/isort would have resolved.
if ! printf '%s\n' "$staged_py" | tr '\n' '\0' | xargs -0 "$FLAKE8"; then
  echo "" >&2
  echo "pre-commit: flake8 found issues that can't be auto-fixed (see above)." >&2
  echo "            Fix them and re-commit, or 'git commit --no-verify' to skip." >&2
  exit 1
fi

echo "pre-commit: staged Python is formatted and lint-clean."
