"use strict";

/**
 * The packed shell, as electron-builder unpacks it (`electron-builder --linux dir`), for the shell
 * smoke (#3942): where its executable and its `app.asar` are, whether the asar holds every entry
 * of `build.files`, and copies of it with one thing planted, for the smoke's own controls.
 *
 *   node desktop/test/smoke/packedApp.js check <unpacked-dir>
 *   node desktop/test/smoke/packedApp.js controls <unpacked-dir> --gateway-pid-file <file>
 *
 * `check` lists the asar against `build.files` and exits 1 naming any entry it lacks, or any file
 * in it that carries a remote-debugging switch: the smoke's DevTools pipe is the harness's alone,
 * and the app never opens a debugging port or pipe of its own. `controls`
 * runs the smoke on two planted copies, which must each fail for their own reason:
 *
 *   - the asar without a module `main.js` requires, which must fail at launch naming it: what only
 *     the packed app shows (`packaging.test.js` reads the source, not the asar);
 *   - a preload that throws, so the window loads no desktop bridge: a renderer that loads nothing
 *     of the shell's.
 */

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const asar = require("@electron/asar");

const { smoke } = require("./shellSmoke");

const DESKTOP = path.join(__dirname, "..", "..");
const PKG = JSON.parse(fs.readFileSync(path.join(DESKTOP, "package.json"), "utf8"));
/** A Chromium remote-debugging switch, by any spelling: a port, a pipe, an address. */
const DEBUG_SWITCH = /remote[-_]debugging/i;
/** The kinds of file a switch could be set from. */
const SOURCE = /\.(js|cjs|mjs|html|json)$/;
/** The module the first control removes: `main.js` requires it, and no fallback covers it. */
const PLANTED_MISSING = "doorbell.js";

/** The executable and the asar of the app unpacked at *dir* (electron-builder's `linux-unpacked`). */
function packed(dir) {
  const name = PKG.build.linux?.executableName || PKG.name;
  const executable = path.join(dir, name);
  const appAsar = path.join(dir, "resources", "app.asar");
  for (const f of [executable, appAsar]) if (!fs.existsSync(f)) throw new Error(`the unpacked app has no ${path.relative(dir, f)}`);
  return { executable, appAsar };
}

/** The `build.files` entries the asar at *appAsar* lacks; a folder counts when anything is in it. */
function missingEntries(appAsar, files = PKG.build.files) {
  const listed = new Set(asar.listPackage(appAsar).map((p) => p.replace(/^[/\\]+/, "").split(path.sep).join("/")));
  return files.filter((entry) => !listed.has(entry) && ![...listed].some((p) => p.startsWith(`${entry}/`)));
}

/** Each of *sources* (`{name, text}`) that carries a remote-debugging switch, as a problem. */
function debugSwitchProblems(sources) {
  return sources.filter((s) => DEBUG_SWITCH.test(s.text)).map((s) => `${s.name} carries a remote-debugging switch`);
}

/** The shell's own sources, as `build.files` packs them, and its builder config. */
function shellSources(dir = DESKTOP, pkg = PKG) {
  const out = [{ name: "desktop/package.json's build config", text: JSON.stringify(pkg.build) }];
  for (const entry of pkg.build.files) {
    const file = path.join(dir, entry);
    if (SOURCE.test(entry) && fs.existsSync(file) && fs.statSync(file).isFile()) out.push({ name: `desktop/${entry}`, text: fs.readFileSync(file, "utf8") });
  }
  return out;
}

/** The sources packed in the asar at *appAsar*. */
function asarSources(appAsar) {
  return asar
    .listPackage(appAsar)
    .map((p) => p.replace(/^[/\\]+/, ""))
    .filter((p) => SOURCE.test(p))
    .map((p) => ({ name: `app.asar/${p}`, text: asar.extractFile(appAsar, p).toString("utf8") }));
}

/**
 * A copy of the unpacked app at *dir*, its asar rewritten by *plant* on the extracted tree. The
 * copy's sandbox helper links to the original's: a copy is the runner's and loses the root owner
 * and setuid bit the helper was given, without which Electron aborts before main.js runs.
 */
async function plantedCopy(dir, plant) {
  const copy = fs.mkdtempSync(path.join(os.tmpdir(), "pclaw-planted-"));
  fs.cpSync(dir, copy, { recursive: true, verbatimSymlinks: true });
  const helper = path.join(copy, "chrome-sandbox");
  if (fs.existsSync(helper)) {
    fs.rmSync(helper);
    fs.symlinkSync(path.resolve(dir, "chrome-sandbox"), helper);
  }
  const appAsar = path.join(copy, "resources", "app.asar");
  const tree = path.join(copy, "app-tree");
  asar.extractAll(appAsar, tree);
  plant(tree);
  fs.rmSync(appAsar);
  await asar.createPackage(tree, appAsar);
  asar.uncache(appAsar);
  fs.rmSync(tree, { recursive: true, force: true });
  return copy;
}

/** The two planted controls: each must fail, and for its own reason. */
const CONTROLS = [
  {
    name: `the asar without ${PLANTED_MISSING}, which main.js requires`,
    plant: (tree) => fs.rmSync(path.join(tree, PLANTED_MISSING)),
    reads: (r) => /Cannot find module/.test(r.mainOutput) && r.mainOutput.includes(PLANTED_MISSING.replace(/\.js$/, "")),
  },
  {
    name: "a preload that throws, so the window has no desktop bridge",
    plant: (tree) => fs.writeFileSync(path.join(tree, "preload.js"), 'throw new Error("planted: the preload throws");\n'),
    reads: (r) => /no desktop bridge/.test(r.reason || ""),
  },
];

async function runControls(dir, { gatewayPidFile, log = () => {}, timeoutMs } = {}) {
  const results = [];
  for (const control of CONTROLS) {
    const copy = await plantedCopy(dir, control.plant);
    try {
      const r = await smoke(packed(copy).executable, { gatewayPidFile, timeoutMs, log: (s) => log(`  ${s}`) });
      const ok = !r.ok && control.reads(r);
      log(`${ok ? "caught" : "MISSED"}: ${control.name}: ${r.ok ? "the smoke passed" : r.reason}`);
      results.push({ name: control.name, ok });
    } finally {
      fs.rmSync(copy, { recursive: true, force: true });
    }
  }
  return results;
}

module.exports = { CONTROLS, DEBUG_SWITCH, PLANTED_MISSING, asarSources, debugSwitchProblems, missingEntries, packed, plantedCopy, runControls, shellSources };

if (require.main === module) {
  const [cmd, dir, ...rest] = process.argv.slice(2);
  const opt = (name) => (rest.indexOf(name) >= 0 ? rest[rest.indexOf(name) + 1] : null);
  const log = (s) => console.log(`packed-app: ${s}`);
  if (cmd === "check" && dir) {
    const { appAsar } = packed(path.resolve(dir));
    const missing = missingEntries(appAsar);
    log(missing.length ? `app.asar lacks ${missing.join(", ")}` : `app.asar holds every one of the ${PKG.build.files.length} build.files entries`);
    const switches = debugSwitchProblems(asarSources(appAsar));
    log(switches.length ? switches.join("; ") : "no file in app.asar carries a remote-debugging switch");
    process.exit(missing.length || switches.length ? 1 : 0);
  } else if (cmd === "controls" && dir) {
    const gatewayPidFile = opt("--gateway-pid-file") ? path.resolve(opt("--gateway-pid-file")) : null;
    runControls(path.resolve(dir), { gatewayPidFile, log }).then((results) => process.exit(results.every((r) => r.ok) ? 0 : 1));
  } else {
    console.error("usage: node desktop/test/smoke/packedApp.js check|controls <unpacked-dir> [--gateway-pid-file <file>]");
    process.exit(2);
  }
}
