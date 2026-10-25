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
 *
 * Before the gateway starts, the script seeds one paused code loop (`CODE_LOOP`) through the
 * checkout's own loop store, so the gateway names a cockpit the smoke's `personalclaw://code/<id>`
 * link can open (#3827): no model, no worker, and nothing runs it. The seed refuses the default
 * home, `~/.personalclaw`, before it writes anything. The file the link's diff names is written new
 * in the loop's workspace, so the cockpit has that file's diff to draw.
 */

const fs = require("node:fs");
const path = require("node:path");

const REPO = path.resolve(__dirname, "..", "..", "..");
const BACKEND_DIR = path.join(REPO, "desktop", "backend-dist", "personalclaw-backend");
const SCRIPT = path.join(BACKEND_DIR, "personalclaw-backend");
const SEED = path.join(REPO, "web", "e2e", "fixtures", "home");
const SCRIPTED = path.join(REPO, "web", "e2e", "fixtures", "scripted-chat.json");

/** The paused code loop the gateway names: its cockpit, the file its changes pane opens, and a task. */
const CODE_LOOP = Object.freeze({ id: "c0ffee11", name: "Smoke cockpit", diff: "src/app.py", task: "ta-5m0ke0aa" });

/** The seed, run by the checkout's Python in the gateway's home: the loop once, the default home never. */
const SEED_CODE_LOOP = [
  "import os, sys",
  "from personalclaw.config.loader import uses_default_home",
  "if not os.environ.get('PERSONALCLAW_HOME') or uses_default_home():",
  "    sys.exit('the smoke seeds its own home only, never the default one')",
  "from personalclaw.loop import store",
  "from personalclaw.loop.loop import Loop",
  `if store.get(${JSON.stringify(CODE_LOOP.id)}) is None:`,
  `    store.create(Loop(id=${JSON.stringify(CODE_LOOP.id)}, name=${JSON.stringify(CODE_LOOP.name)}, kind='code', task='Opened by a link, and run by nothing', status='paused', workspace_dir=os.environ['PERSONALCLAW_WORKSPACE']))`,
].join("\n");

/** The Python beside the checkout's `personalclaw`, else the one on PATH. */
function pythonFor(command) {
  const beside = path.isAbsolute(command) ? path.join(path.dirname(command), "python") : null;
  return beside && fs.existsSync(beside) ? beside : "python3";
}

/** The checkout's `personalclaw`: the repository's own virtualenv, else the one on PATH. */
function gatewayCommand(repo = REPO) {
  const venv = path.join(repo, ".venv", "bin", "personalclaw");
  return fs.existsSync(venv) ? venv : "personalclaw";
}

/** The script's text, for a gateway home at *home*, seeded by *python* when one is given. Every
 * value is quoted for the shell. */
function stubScript({ home, workspace, scripted, command, src, pidFile, python = null }) {
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
    ...(python ? [`${q(python)} -c ${q(SEED_CODE_LOOP)}`] : []),
    `exec ${q(command)} "$@"`,
    "",
  ].join("\n");
}

/** Write the script and seed its home under *smokeDir*; return the script's path. */
function writeStubBackend(smokeDir, { repo = REPO, backendDir = BACKEND_DIR } = {}) {
  const home = path.join(smokeDir, "gateway-home");
  const workspace = path.join(home, "workspace");
  fs.mkdirSync(workspace, { recursive: true });
  const changed = path.join(workspace, CODE_LOOP.diff);
  fs.mkdirSync(path.dirname(changed), { recursive: true });
  fs.writeFileSync(changed, 'print("opened by a link")\n');
  for (const name of ["config.json", "triggers.json"]) fs.copyFileSync(path.join(SEED, name), path.join(home, name));
  if (!fs.existsSync(SCRIPTED)) throw new Error(`the scripted provider's script is missing: ${SCRIPTED}`);
  fs.mkdirSync(backendDir, { recursive: true });
  const script = path.join(backendDir, "personalclaw-backend");
  const pidFile = path.join(smokeDir, "gateway.pid");
  const command = gatewayCommand(repo);
  fs.writeFileSync(script, stubScript({ home, workspace, scripted: SCRIPTED, command, src: path.join(repo, "src"), pidFile, python: pythonFor(command) }), { mode: 0o755 });
  fs.chmodSync(script, 0o755);
  return script;
}

module.exports = { BACKEND_DIR, CODE_LOOP, SCRIPT, SEED_CODE_LOOP, gatewayCommand, pythonFor, stubScript, writeStubBackend };

if (require.main === module) {
  const dir = process.argv[2];
  if (!dir) {
    console.error("usage: node desktop/test/smoke/writeStubBackend.js <smoke-dir>");
    process.exit(2);
  }
  console.log(writeStubBackend(path.resolve(dir)));
}
