"use strict";

/**
 * Whether this host can drive a real Electron, and how to start it, for the real-Electron tiers
 * (`bridgeLoads.test.js`, `buddyIdle.test.js`). A tier that cannot run skips and says why, out loud,
 * except where the host must run it: with `PCLAW_REQUIRE_ELECTRON=1` (CI's web job), a host that
 * cannot start Electron fails the tier with the reason, so a runner that lost its display, its
 * binary or its sandbox cannot pass with the tier skipped.
 *
 * Linux with no display (a CI runner) starts Electron under `xvfb-run`, which gives the one run a
 * display of its own and takes it down after, when the host has it.
 */

const fs = require("node:fs");
const path = require("node:path");

/** `name`'s path on PATH, or null. */
function onPath(name) {
  for (const dir of (process.env.PATH || "").split(path.delimiter)) {
    if (!dir) continue;
    const file = path.join(dir, name);
    try {
      fs.accessSync(file, fs.constants.X_OK);
      return file;
    } catch {
      /* not here */
    }
  }
  return null;
}

const needsDisplay = () => process.platform === "linux" && !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY;

/** The switch a host sets when it must run the real-Electron tiers. */
const REQUIRE_ELECTRON = "PCLAW_REQUIRE_ELECTRON";

/**
 * What is wrong with Electron's setuid sandbox helper, or null. On a Linux host whose kernel
 * withholds user namespaces (Ubuntu's AppArmor rule, `kernel.apparmor_restrict_unprivileged_userns`),
 * Chromium's sandbox runs through `chrome-sandbox` beside the binary, which must be root's with mode
 * 4755, or Electron aborts rather than run unsandboxed. `stat` is injected for the test.
 */
function sandboxHelperProblem(bin, stat = fs.statSync) {
  const helper = path.join(path.dirname(bin), "chrome-sandbox");
  let st;
  try {
    st = stat(helper);
  } catch {
    return `no setuid sandbox helper at ${helper}`;
  }
  const mode = st.mode & 0o7777;
  if (st.uid === 0 && mode === 0o4755) return null;
  return `the setuid sandbox helper ${helper} is uid ${st.uid}, mode ${mode.toString(8)}: it must be root's with mode 4755 where the kernel withholds user namespaces`;
}

/** Why Electron cannot be driven here, or `null` if it can. */
function electronUnavailable() {
  let bin = null;
  try {
    bin = require("electron");
  } catch (err) {
    return `electron is not resolvable (${err.code || err.message})`;
  }
  if (typeof bin !== "string" || !fs.existsSync(bin)) {
    return "electron/dist is absent — `npm ci` did not run the download lifecycle script";
  }
  if (needsDisplay() && !onPath("xvfb-run")) {
    return "no display on linux, and no xvfb-run to give it one";
  }
  // Where the host must run the tiers, a sandbox that cannot start is named before Electron is.
  if (process.platform === "linux" && process.env[REQUIRE_ELECTRON] === "1") return sandboxHelperProblem(bin);
  return null;
}

/** The command and arguments that start Electron on `args`: under `xvfb-run` where there is no
 *  display, else the binary itself. */
function electronCommand(args) {
  const bin = require("electron");
  if (needsDisplay()) return { command: onPath("xvfb-run"), args: ["-a", bin, ...args] };
  return { command: bin, args };
}

/**
 * Why a probe run printed no result line: its exit, and the end of what it wrote. Both streams,
 * since `xvfb-run` hands the program's stderr to its stdout, where Chromium's fatal line (a sandbox
 * it cannot start, a library it cannot load) would otherwise be lost.
 */
function launchDetail(res) {
  const tail = (text) => String(text || "").trim().slice(-600);
  return `status=${res.status} signal=${res.signal} stderr=${tail(res.stderr)} stdout=${tail(res.stdout)}`;
}

/**
 * What a tier does with why Electron cannot start (`reason`, or null when it can): skip, saying
 * why, or, where `PCLAW_REQUIRE_ELECTRON=1` says the host must run it, fail with the reason.
 * Returns `{skip, fail}`: `skip` is the reason or false, `fail` the message or null.
 */
function electronTier(reason, env = process.env) {
  if (!reason) return { skip: false, fail: null };
  if (env[REQUIRE_ELECTRON] === "1") {
    return { skip: false, fail: `${REQUIRE_ELECTRON}=1, and this host cannot start Electron: ${reason}` };
  }
  return { skip: reason, fail: null };
}

/** The platforms where Electron can keep a window out of screen capture (`setContentProtection`). */
const CONTENT_PROTECTION_PLATFORMS = ["darwin", "win32"];

/**
 * A window's content protection as a probe reports it: the window's own answer where Electron can
 * set it, and null elsewhere (Linux, where `isContentProtected()` reads false whatever was asked),
 * so a leg checks it only where it means something.
 */
function contentProtection(win, platform = process.platform) {
  if (!CONTENT_PROTECTION_PLATFORMS.includes(platform) || typeof win.isContentProtected !== "function") return null;
  return win.isContentProtected();
}

module.exports = { REQUIRE_ELECTRON, contentProtection, electronUnavailable, electronCommand, electronTier, launchDetail, sandboxHelperProblem };
