"use strict";

/**
 * The shell smoke's backend (#3942): a script in the place of the bundled PyInstaller backend,
 * `backend-dist/personalclaw-backend/personalclaw-backend`, that runs this checkout's gateway.
 *
 * The packed shell finds it where it finds the real one (`findPersonalclawBin`), spawns it with
 * `gateway --port auto --json-ready --no-open`, waits for its ready line and signs its window in
 * with that token (`localSignIn`): the path every desktop user takes. The script hands those
 * arguments to the checkout's `personalclaw`, in a home of the smoke's own, seeded as the e2e
 * suite seeds its home (`web/e2e/fixtures/home/`) and answering from the offline scripted
 * provider. It never proves the bundled backend starts; the release rehearsal does.
 *
 *   node desktop/test/smoke/writeStubBackend.js <smoke-dir>
 *
 * writes the script and the seeded home under `<smoke-dir>`, and prints the script's path. The
 * script leaves the gateway's pid in `<smoke-dir>/gateway.pid`: the shell starts the gateway in a
 * process group of its own, so the smoke stops it by that pid when it is done.
 */

const fs = require("node:fs");
const path = require("node:path");

const REPO = path.resolve(__dirname, "..", "..", "..");
const BACKEND_DIR = path.join(REPO, "desktop", "backend-dist", "personalclaw-backend");
const SCRIPT = path.join(BACKEND_DIR, "personalclaw-backend");
const SEED = path.join(REPO, "web", "e2e", "fixtures", "home");
const SCRIPTED = path.join(REPO, "web", "e2e", "fixtures", "scripted-chat.json");

/** The checkout's `personalclaw`: the repository's own virtualenv, else the one on PATH. */
function gatewayCommand(repo = REPO) {
  const venv = path.join(repo, ".venv", "bin", "personalclaw");
  return fs.existsSync(venv) ? venv : "personalclaw";
}

/** The script's text, for a gateway home at *home*. Every value is quoted for the shell. */
function stubScript({ home, workspace, scripted, command, src, pidFile }) {
  const q = (s) => `'${String(s).replace(/'/g, `'\\''`)}'`;
  return [
    "#!/bin/sh",
    "# Written by desktop/test/smoke/writeStubBackend.js for the shell smoke (#3942); never shipped.",
    "set -eu",
    `export PERSONALCLAW_HOME=${q(home)}`,
    `export PERSONALCLAW_WORKSPACE=${q(workspace)}`,
    `export PERSONALCLAW_SCRIPTED_MODEL_SCRIPT=${q(scripted)}`,
    `export PYTHONPATH=${q(src)}\${PYTHONPATH:+:$PYTHONPATH}`,
    `echo $$ > ${q(pidFile)}`,
    `exec ${q(command)} "$@"`,
    "",
  ].join("\n");
}

/** Write the script and seed its home under *smokeDir*; return the script's path. */
function writeStubBackend(smokeDir, { repo = REPO, backendDir = BACKEND_DIR } = {}) {
  const home = path.join(smokeDir, "gateway-home");
  const workspace = path.join(home, "workspace");
  fs.mkdirSync(workspace, { recursive: true });
  for (const name of ["config.json", "triggers.json"]) fs.copyFileSync(path.join(SEED, name), path.join(home, name));
  if (!fs.existsSync(SCRIPTED)) throw new Error(`the scripted provider's script is missing: ${SCRIPTED}`);
  fs.mkdirSync(backendDir, { recursive: true });
  const script = path.join(backendDir, "personalclaw-backend");
  const pidFile = path.join(smokeDir, "gateway.pid");
  fs.writeFileSync(script, stubScript({ home, workspace, scripted: SCRIPTED, command: gatewayCommand(repo), src: path.join(repo, "src"), pidFile }), { mode: 0o755 });
  fs.chmodSync(script, 0o755);
  return script;
}

module.exports = { BACKEND_DIR, SCRIPT, gatewayCommand, stubScript, writeStubBackend };

if (require.main === module) {
  const dir = process.argv[2];
  if (!dir) {
    console.error("usage: node desktop/test/smoke/writeStubBackend.js <smoke-dir>");
    process.exit(2);
  }
  console.log(writeStubBackend(path.resolve(dir)));
}
