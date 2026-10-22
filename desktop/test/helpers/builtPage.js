"use strict";

/**
 * The built-page legs (`test/built/`): what they need beyond a real Electron is the built web app
 * (`web/dist`), built from the checkout's own sources. CI's `web-build` runs them after its build
 * and stamp under PCLAW_REQUIRE_ELECTRON=1; a local host without a fresh build skips and says why.
 */

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { REQUIRE_ELECTRON } = require("./electronHost");

const REPO = path.join(__dirname, "..", "..", "..");

/**
 * Why the built page cannot be the one under test, or null: not built, built from other sources than
 * the checkout's, or not shown to be current (`scripts/spa_dist_freshness.py check`). Only a clean
 * pass is a pass: a check that fails another way, times out or cannot run is a reason, never a
 * build taken on trust. Where the host must run the legs (PCLAW_REQUIRE_ELECTRON=1) the check also
 * requires the build's stamp, so an unstamped build is no pass there.
 */
function pageUnbuilt(repo = REPO, { spawn = spawnSync, exists = fs.existsSync, env = process.env } = {}) {
  if (!exists(path.join(repo, "web", "dist", "index.html"))) return "the web app is not built: run `make web-build`";
  const args = ["scripts/spa_dist_freshness.py", "check", ...(env[REQUIRE_ELECTRON] === "1" ? ["--require-stamp"] : [])];
  const check = spawn("python3", args, { cwd: repo, encoding: "utf8", timeout: 30_000 });
  const said = (text) => String(text || "").trim().slice(-300);
  if (check.status === 0) return null;
  if (check.status === 1) return `the built web app is older than web/'s sources: run \`make web-build\` (${said(check.stdout)})`;
  const why = check.error ? check.error.message : `exit ${check.status}${check.signal ? ` (${check.signal})` : ""}: ${said(check.stderr) || said(check.stdout)}`;
  return `the build's freshness could not be checked: ${why}`;
}

/**
 * What a built-page leg does with why it cannot run: `electron`, why Electron cannot start, and
 * `page`, why the built page is not the one to test (each a reason, or null). It skips, saying why,
 * or, where `PCLAW_REQUIRE_ELECTRON=1` says the host must run it, fails with the reason.
 */
function builtPageTier({ electron = null, page = null } = {}, env = process.env) {
  const reason = electron ? `cannot start Electron: ${electron}` : page ? `cannot run the built page: ${page}` : null;
  if (!reason) return { skip: false, fail: null };
  if (env[REQUIRE_ELECTRON] === "1") return { skip: false, fail: `${REQUIRE_ELECTRON}=1, and this host ${reason}` };
  return { skip: electron || page, fail: null };
}

module.exports = { pageUnbuilt, builtPageTier };
