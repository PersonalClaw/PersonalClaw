#!/bin/sh
# breadth.yml's install-smoke, the served leg: the actual one-liner, verbatim, as a user pastes
# it. Written as the documented `curl … | sh` pipeline on purpose: piping into sh is the thing
# being asserted (no TTY on stdin, a partial transfer must install nothing). Running the
# downloaded file as an argument instead would test a path no user takes.
#
# `set -o pipefail` is why a 404 or a TLS failure cannot pass: without it the pipeline's status
# is sh's, and sh happily exits 0 having executed an empty or truncated body, a green check for
# having installed nothing. The step runs this as `sh <path>`, so under the container's sh:
# ubuntu:26.04's dash, which has `pipefail` (Debian's dash since 0.5.12-7). A sh without it stops
# at that line, a special builtin's error under `set -e`, so the leg is red, never a weaker pipe.
#
# This is the one script the supply-chain check lets pipe a download into sh, and only when the
# install-smoke step that tests the served installer runs it (tools/ci/supply_chain.py,
# OWN_INSTALLER_STEP and OWN_INSTALLER_SCRIPT).
set -eu
# shellcheck disable=SC3040 # POSIX.1-2024 and this dash have it, as said above
set -o pipefail
UV_TOOL_DIR="$RUNNER_TEMP/uv-tools-served"
UV_TOOL_BIN_DIR="$RUNNER_TEMP/uv-bin-served"
export UV_TOOL_DIR UV_TOOL_BIN_DIR
mkdir -p "$UV_TOOL_DIR" "$UV_TOOL_BIN_DIR"
PATH="$UV_TOOL_BIN_DIR:$PATH"; export PATH
if ! curl -fsSL --retry 3 --retry-delay 5 --max-time 120 \
       https://personalclaw.dev/install | sh; then
  echo "::error title=install one-liner unproven::unproven — the served one-liner did" \
       "not complete. If personalclaw.dev or PyPI is unreachable this run proved" \
       "NOTHING; it is red rather than green because an unanswered question is not a" \
       "passing answer. Re-run once reachability is confirmed."
  exit 1
fi
personalclaw --version
