#!/usr/bin/env bash
# Slice 2 smoke — run the exemplar through the real engine with a fake model, assert the
# required_artifacts gate fails the run. No network, no real LLM. Target: well under 30s.
#
# Isolates PERSONALCLAW_HOME to a throwaway dir so nothing touches the real ~/.personalclaw.
# Run from anywhere; it resolves the repo root from its own path.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$REPO_ROOT"

PY="${PERSONALCLAW_PY:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"

HOME_DIR="$(mktemp -d)"
trap 'rm -rf "$HOME_DIR"' EXIT

PERSONALCLAW_HOME="$HOME_DIR" "$PY" -m harness.exemplars.slice_2.exemplar
