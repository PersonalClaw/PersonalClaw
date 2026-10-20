#!/bin/sh
# breadth.yml's install-smoke, the staged leg: the installer file this repository holds, the one
# we can fix. UV_TOOL_DIR / UV_TOOL_BIN_DIR are redirected into RUNNER_TEMP for two reasons: the
# smoke must not install over whatever else the job has, and the legs must not silently satisfy
# each other: a shared tool dir would let one leg pass by finding another's binary.
#
# UV_TOOL_BIN_DIR goes on PATH BEFORE the installer runs so the script's own
# `have personalclaw` / offer_setup tail is exercised rather than skipped: with no TTY,
# `[ -t 0 ]` is false, so it must print the non-interactive line and exit 0, never hang.
#
# POSIX sh: the step runs it as `sh <path>`, and it runs the installer the same way, so
# tools/ci/supply_chain.py reads both under the step's rules (the installer as the artifact this
# step tests: STAGED_INSTALLER_STEP, STAGED_INSTALLER_SCRIPT).
set -eu
UV_TOOL_DIR="$RUNNER_TEMP/uv-tools-staged"
UV_TOOL_BIN_DIR="$RUNNER_TEMP/uv-bin-staged"
export UV_TOOL_DIR UV_TOOL_BIN_DIR
mkdir -p "$UV_TOOL_DIR" "$UV_TOOL_BIN_DIR"
PATH="$UV_TOOL_BIN_DIR:$PATH"; export PATH
sh deploy/website/install.sh
# Installing proves a wheel downloaded. Running it proves the front door opens.
personalclaw --version
