#!/bin/sh
# The `electron` toolchain (#3831): Electron's binary, which `npm ci` does not download. CI's web job
# and `run-job` both install it with this script, so the two cannot drift.
#
# Electron's own install script fetches the build the locked package names and checks it against the
# checksums that package ships, so the tree pins it.
#
# On a GitHub-hosted Linux runner (not a self-hosted one, which sets GITHUB_ACTIONS too, but is a
# machine someone else owns), Ubuntu's AppArmor rule (kernel.apparmor_restrict_unprivileged_userns)
# withholds the user namespaces Chromium's own sandbox needs, so Electron falls back to its setuid
# sandbox helper, and aborts unless that helper is root's with mode 4755 (tools/ci/sandbox_helper.sh,
# which a packed app's own helper takes too). Only the helper gets the
# bit: the renderer sandbox stays on, and no kernel setting is loosened. Elsewhere this script sets
# nothing; a Linux host that withholds the namespaces and must run the real-Electron legs is told
# so by the legs themselves (desktop/test/helpers/electronHost.js).
set -eu

node node_modules/electron/install.js

sh tools/ci/sandbox_helper.sh node_modules/electron/dist/chrome-sandbox
