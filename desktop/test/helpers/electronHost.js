"use strict";

/**
 * Whether this host can drive a real Electron, for the real-Electron tiers (`bridgeLoads.test.js`,
 * `buddyIdle.test.js`). A tier that cannot run skips and says why, out loud.
 */

const fs = require("node:fs");

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
  if (process.platform === "linux" && !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY) {
    return "no display on linux (needs Xvfb)";
  }
  return null;
}

module.exports = { electronUnavailable };
