#!/bin/sh
# Give an Electron setuid sandbox helper the owner and mode it needs: the toolchain's binary's
# (tools/ci/install_electron.sh) and a packed app's (#3942).
#
#   (from the repository root) the helper's path is its one argument
#
# On a GitHub-hosted Linux runner (not a self-hosted one, which sets GITHUB_ACTIONS too, but is a
# machine someone else owns): a workflow step says so with GITHUB_ACTIONS and RUNNER_ENVIRONMENT,
# and under run-job, whose steps get neither, run-job says so with PCLAW_SANDBOX_HELPER_HOST, which
# it gives this script's callers only after reading the runner from its own process environment
# (tools/ci/jobs.py SANDBOX_HELPER_ENV). There, Ubuntu's AppArmor rule
# (kernel.apparmor_restrict_unprivileged_userns) withholds the user namespaces Chromium's own
# sandbox needs, so Electron falls back to its setuid
# sandbox helper, and aborts unless that helper is root's with mode 4755. electron-builder's dir
# build carries a copy of its own, which needs what the toolchain's binary needs. Only the helper
# gets the bit: the renderer sandbox stays on, and no kernel setting is loosened. Elsewhere this
# script sets nothing and says so, and a host that withholds the namespaces is told so by the app.
set -eu

helper=${1:?give the path of the sandbox helper}
if [ "$(uname -s)" = Linux ] && { { [ "${GITHUB_ACTIONS:-}" = true ] && [ "${RUNNER_ENVIRONMENT:-}" = github-hosted ]; } || [ "${PCLAW_SANDBOX_HELPER_HOST:-}" = github-hosted ]; }; then
  [ -e "$helper" ] || { echo "sandbox_helper: no helper at $helper" >&2; exit 1; }
  sudo chown root:root "$helper"
  sudo chmod 4755 "$helper"
  echo "sandbox_helper: $helper is root's, mode 4755"
else
  echo "sandbox_helper: not a GitHub-hosted Linux runner, $helper left as it is"
fi
