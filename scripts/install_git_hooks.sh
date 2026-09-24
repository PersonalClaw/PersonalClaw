#!/bin/sh
# Point this clone at the repository-owned hooks (.githooks/). Runs automatically
# from npm `postinstall`, and on demand:
#     npm run hooks:install
# Same pattern as personalclaw.dev; the local core.hooksPath wins over any
# machine-level hook configuration for this repository.
#
# Because postinstall runs it unattended, every not-a-dev-clone case exits 0
# quietly rather than failing the install: no git, not a work tree (npm tarball,
# Docker build context), no .githooks/ directory, or CI (hooks never fire there
# and CI is the enforcement boundary anyway). Only a genuine misconfiguration in
# a real clone is an error.
set -eu

# CI: nothing to install, the workflows are the gate.
if [ -n "${CI:-}" ]; then
  exit 0
fi

command -v git >/dev/null 2>&1 || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0

repository_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$repository_root" || exit 0

# Source checkout only; a packaged copy has no hooks to point at.
[ -d .githooks ] || exit 0

git config --local core.hooksPath .githooks

if [ "$(git config --local --get core.hooksPath)" != ".githooks" ]; then
  echo "Failed to configure the repository-owned Git hooks." >&2
  exit 1
fi

echo "Installed repository Git hooks from .githooks (pre-commit lint, DCO sign-off, pre-push)."
