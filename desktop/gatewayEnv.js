/**
 * The environment the shell hands the gateway it spawns (DESKTOP-CAPABILITIES `DC-1` T1.3,
 * DISTRIBUTION contract C1).
 *
 * 🔑 THE SHELL MUST DECLARE THE INSTALL KIND, because nothing else can work it out.
 * `self_update.detect_install_kind()` resolves `PERSONALCLAW_INSTALL_KIND` first, then probes
 * `PERSONALCLAW_PROJECT_DIR` for a `.git`, then falls back to `"pip"`. In a PACKAGED app that
 * project dir is `…/resources` inside the bundle, which carries no `.git` — so a shell that does
 * not set the env ships a gateway that classifies itself as a **pip install**. The Updates panel
 * then offers an in-app "Update" button whose apply path runs
 * `<installer> install -U personalclaw==<tag>` against `sys.executable`, and inside the AppImage
 * / .deb / .dmg `sys.executable` is the FROZEN PyInstaller binary. There is no interpreter there
 * to upgrade: the best case is a failed install with a confusing error, and every case is the
 * wrong answer to "how do I update this?". The gateway is also a child of this process — it does
 * not own its own lifecycle and cannot re-exec itself out from under the shell.
 *
 * So this is set unconditionally, in dev as well as packaged. "Was this gateway spawned by the
 * desktop shell?" is the question the kind answers, and the answer is yes both times: a
 * `git pull` + re-exec triggered from the panel is just as wrong when the shell was started with
 * `npm start` from a checkout, because the shell still holds the process handle and the stdout
 * pipe it reads the READY line from.
 *
 * It also wins over anything INHERITED. A stray `PERSONALCLAW_INSTALL_KIND=container` in the
 * user's shell profile must not make a desktop install describe itself as a container, so the
 * explicit keys are spread AFTER the inherited environment.
 *
 * A separate pure module rather than an object literal inside `startGateway`: an inline literal
 * is only assertable by reading `main.js` as text, and this env is a CONTRACT with the Python
 * side (`self_update.INSTALL_KINDS`). `desktop/test/gatewayEnv.test.js` executes it, and
 * `tests/test_desktop_install_kind.py` pins the two sides together.
 */

/** The install kind a shell-spawned gateway reports. Must be a `self_update.INSTALL_KINDS` member. */
const INSTALL_KIND = "desktop";

/**
 * Build the child environment for the gateway spawn.
 *
 * @param {object} opts
 * @param {Record<string, string|undefined>} opts.env - the shell's own environment (`process.env`).
 * @param {string} opts.loginPath - the resolved login-shell PATH (see `resolveLoginPath`).
 * @param {string} opts.projectDir - what the gateway should treat as its project dir.
 * @returns {Record<string, string|undefined>} a NEW object; `opts.env` is never mutated.
 */
function buildGatewayEnv({ env = {}, loginPath = "", projectDir = "" } = {}) {
  // Drop any inherited PERSONALCLAW_PORT so the gateway honors `--port auto`.
  const { PERSONALCLAW_PORT: _ignored, ...inherited } = env;
  return {
    ...inherited,
    // Restore the user's real login-shell PATH so the backend can resolve provider CLIs
    // (claude, node, npx) that live outside the minimal PATH a Finder-launched .app
    // inherits from launchd.
    PATH: loginPath,
    PERSONALCLAW_DEV_NO_AUTH: "1",
    PERSONALCLAW_PROJECT_DIR: projectDir,
    PERSONALCLAW_INSTALL_KIND: INSTALL_KIND,
  };
}

module.exports = { INSTALL_KIND, buildGatewayEnv };
