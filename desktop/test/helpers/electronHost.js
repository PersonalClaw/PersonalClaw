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
  return null;
}

/** The command and arguments that start Electron on `args`: under `xvfb-run` where there is no
 *  display, else the binary itself. */
function electronCommand(args) {
  const bin = require("electron");
  if (needsDisplay()) return { command: onPath("xvfb-run"), args: ["-a", bin, ...args] };
  return { command: bin, args };
}

/** The switch a host sets when it must run the real-Electron tiers. */
const REQUIRE_ELECTRON = "PCLAW_REQUIRE_ELECTRON";

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

module.exports = { REQUIRE_ELECTRON, electronUnavailable, electronCommand, electronTier };
