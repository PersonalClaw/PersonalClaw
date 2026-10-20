#!/bin/sh
# breadth.yml's install-smoke, the current checkout leg: install the wheel this checkout builds,
# remove every system timezone search path, and run the setup command a fresh user is told to
# run. `TZ` is an authored IANA name, so persisting it proves zoneinfo loaded the package's Python
# tzdata fallback rather than an OS database. It names the installer's Python (`PC_PYTHON`) as the
# installer does: uv ignores the upper bound of requires-python, so a bare install here would
# build on the newest Python uv downloads. POSIX sh: the workflow step runs it as `sh <path>`,
# the one way tools/ci/supply_chain.py reads a script a step runs, under the step's rules.
set -eu
UV_TOOL_DIR="$RUNNER_TEMP/uv-tools-current"
UV_TOOL_BIN_DIR="$RUNNER_TEMP/uv-bin-current"
HOME="$RUNNER_TEMP/home-current"
PERSONALCLAW_HOME="$RUNNER_TEMP/personalclaw-home-current"
TZ="America/Los_Angeles"
PYTHONTZPATH=""
export UV_TOOL_DIR UV_TOOL_BIN_DIR HOME PERSONALCLAW_HOME TZ PYTHONTZPATH
mkdir -p "$UV_TOOL_DIR" "$UV_TOOL_BIN_DIR" "$HOME" "$PERSONALCLAW_HOME"
PATH="$UV_TOOL_BIN_DIR:$PATH"; export PATH
uv tool install --python 3.13 .
personalclaw setup
grep -F '"timezone": "America/Los_Angeles"' "$PERSONALCLAW_HOME/config.json"
