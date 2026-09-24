"use strict";

/**
 * Does the bridge actually LOAD? (#3348)
 *
 * The other 359 cases in this suite are source text. Measured: prepending
 * `throw new Error("preload dies at load")` to `preload.js` left the suite at 359 pass / 0
 * fail while a real renderer got `window.pclawDesktop === undefined`. That is the exact
 * defect `#3346` was written to end, and a regex over `main.js` structurally cannot see it —
 * a preload that throws takes the whole bridge with it and Electron reports it on
 * `preload-error`, a channel nothing in this repo listened to.
 *
 * So this file observes, in two tiers, and each tier carries its own control:
 *
 *   TIER 1 — Node, no display, every platform, ~1ms. `loadPreloadSandboxed()` EXECUTES
 *     `preload.js` under a `require` shaped like the sandboxed one. Catches a preload that
 *     throws at load, a preload that reaches for a module the sandbox does not carry, and any
 *     drift between the vocabulary inlined in the preload and `capabilities.js`. Its control
 *     is `connectPreload.js`, which really does require a sibling: the same loader must refuse
 *     it, or the loader proves nothing.
 *
 *   TIER 2 — a real Electron binary, three runs, ~6s. Confirms tier 1's MODEL of the sandbox
 *     against the thing itself, because a model of a sandbox that has never met the sandbox is
 *     just another regex. Runs: (1) `preload.js` with the flag `main.js` really sets — the
 *     rail; (2) `connectPreload.js` sandboxed — must come back `undefined` with
 *     `module not found`, proving the probe can SEE a dead bridge; (3) the same file with
 *     `sandbox: false` — must come back `object`, proving run 2 failed because of the sandbox
 *     and not because the file is broken.
 *
 * Tier 2 SKIPS where Electron cannot start (no binary — `npm ci` blocks lifecycle scripts, so
 * CI has no `electron/dist` — or no display on Linux). That skip is why tier 1 exists rather
 * than being folded into it: the portable tier is the one that guards CI, and it catches both
 * mutations the issue names. A skipped tier 2 says so out loud, with the reason.
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const ROOT = path.resolve(__dirname, "..");
const { CAPABILITIES, IPC_CHANNELS } = require("../capabilities");
const {
  loadPreloadSandboxed,
  preloadOf,
  unsandboxableRequires,
  webPreferenceBlocks,
} = require("./helpers/sandboxedPreload");

const PRELOAD = path.join(ROOT, "preload.js");
const CONNECT_PRELOAD = path.join(ROOT, "connectPreload.js");
/** The bridge's top-level surface. Exact, so a new namespace member is a decision. */
const BRIDGE_KEYS = ["capabilities", "loginItem", "notifications", "onStatus", "pushToTalk"];

// ---------------------------------------------------------------------------------------
// TIER 1 — execute the preload the way a sandboxed renderer would
// ---------------------------------------------------------------------------------------

describe("the capability preload loads under a sandboxed require (tier 1)", () => {
  it("executes without throwing and exposes exactly one namespace", () => {
    // `loadPreloadSandboxed` rethrows whatever the preload throws, so "the preload dies at
    // load" is this assertion failing rather than 359 green tests and an absent bridge.
    const { exposed } = loadPreloadSandboxed(PRELOAD);
    assert.deepStrictEqual(Object.keys(exposed), ["pclawDesktop"]);
    assert.deepStrictEqual(Object.keys(exposed.pclawDesktop).sort(), BRIDGE_KEYS);
  });

  it("requires nothing the Chromium sandbox withholds", () => {
    // The property that lets `main.js` keep the process sandbox. If a future edit reaches for
    // `./capabilities` again — or for `fs`, or `path` — this reds BEFORE the bridge dies.
    assert.deepStrictEqual(
      unsandboxableRequires(fs.readFileSync(PRELOAD, "utf8")),
      [],
      "a sandboxed preload resolves only electron/events/timers/url; anything else throws " +
        "and the whole bridge is skipped silently",
    );
  });

  it("the loader is not vacuous — connectPreload.js, which does need Node, is refused", () => {
    // The positive control. `connectPreload.js` genuinely does `require("./connectDialog")`,
    // which is why `connectDialog.js` still spends `sandbox: false`. If the loader let that
    // through, the green above would mean nothing.
    assert.throws(
      () => loadPreloadSandboxed(CONNECT_PRELOAD),
      /module not found: \.\/connectDialog/,
    );
    assert.deepStrictEqual(unsandboxableRequires(fs.readFileSync(CONNECT_PRELOAD, "utf8")), [
      "./connectDialog",
    ]);
  });

  it("the vocabulary inlined in the preload cannot drift from capabilities.js", () => {
    // Inlining bought the sandbox back at the price of a second copy of the vocabulary. This
    // is the rail that makes the price zero: it drives every bridge method and checks the
    // channel each one actually reached against the real `IPC_CHANNELS`.
    const { exposed, channels } = loadPreloadSandboxed(PRELOAD);
    const bridge = exposed.pclawDesktop;

    assert.deepStrictEqual(bridge.capabilities.names(), CAPABILITIES, "CAPABILITIES drifted");

    const noop = () => {};
    const drives = [
      ["status", () => bridge.onStatus(noop)],
      [IPC_CHANNELS.probe, () => bridge.capabilities.probe("tray")],
      [IPC_CHANNELS.snapshot, () => bridge.capabilities.snapshot()],
      [IPC_CHANNELS.request, () => bridge.capabilities.request("tray")],
      [IPC_CHANNELS.state, () => bridge.capabilities.on("tray", noop)],
      [IPC_CHANNELS.hotkeyBind, () => bridge.pushToTalk.bind("Cmd+Shift+Space")],
      [IPC_CHANNELS.capturing, () => bridge.pushToTalk.setCapturing(true)],
      [IPC_CHANNELS.pushToTalk, () => bridge.pushToTalk.on(noop)],
      [IPC_CHANNELS.loginItemGet, () => bridge.loginItem.get()],
      [IPC_CHANNELS.loginItemSet, () => bridge.loginItem.set(true)],
      [IPC_CHANNELS.notify, () => bridge.notifications.show({ title: "t", body: "b" })],
      [IPC_CHANNELS.notificationActivate, () => bridge.notifications.on(noop)],
    ];
    for (const [, drive] of drives) drive();
    assert.deepStrictEqual(
      channels,
      drives.map(([channel]) => channel),
      "the inlined IPC_CHANNELS no longer agrees with capabilities.js",
    );
    // Every channel in the module is exercised above, so a channel ADDED to capabilities.js
    // without the preload learning it is a red here too, not a silent gap.
    assert.deepStrictEqual(
      [...new Set(channels)].filter((c) => c !== "status").sort(),
      Object.values(IPC_CHANNELS).sort(),
    );
  });
});

// ---------------------------------------------------------------------------------------
// TIER 2 — a real Electron renderer
// ---------------------------------------------------------------------------------------

/** The `webPreferences` block `main.js` really uses for the bridged view. */
function bridgedBlock() {
  const src = fs.readFileSync(path.join(ROOT, "main.js"), "utf8");
  const blocks = webPreferenceBlocks(src).filter((b) => preloadOf(b) === "preload.js");
  assert.strictEqual(blocks.length, 1, `expected one bridged webPreferences block in main.js`);
  return blocks[0];
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
  if (process.platform === "linux" && !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY) {
    return "no display on linux (needs Xvfb)";
  }
  return null;
}

/** One probe run. Returns the parsed `PC_PROBE` payload, or `{ launchFailed, detail }`. */
function probe({ preload, sandboxOff, namespace }) {
  const res = spawnSync(
    require("electron"),
    [path.join(__dirname, "helpers", "bridgeProbe.js")],
    {
      cwd: ROOT,
      timeout: 90_000,
      encoding: "utf8",
      env: {
        ...process.env,
        PC_PROBE_PRELOAD: preload,
        PC_PROBE_SANDBOX: sandboxOff ? "false" : "default",
        PC_PROBE_NAMESPACE: namespace,
        ELECTRON_DISABLE_SECURITY_WARNINGS: "1",
      },
    },
  );
  const line = (res.stdout || "").split("\n").find((l) => l.startsWith("PC_PROBE "));
  if (!line) {
    // No payload at all means the harness never reached `app.whenReady()`. The harness
    // requires no shell module, so that is the HOST, not this repo.
    return {
      launchFailed: true,
      detail: `status=${res.status} signal=${res.signal} stderr=${(res.stderr || "").slice(-400)}`,
    };
  }
  return JSON.parse(line.slice("PC_PROBE ".length));
}

const unavailable = electronUnavailable();
// One launch decides for the whole tier, so a host that cannot run Electron costs one spawn
// instead of three. The bridged run is also the rail, so nothing is spent twice.
const rail = unavailable
  ? null
  : probe({
      preload: PRELOAD,
      sandboxOff: /\bsandbox:\s*false\b/.test(bridgedBlock()),
      namespace: "pclawDesktop",
    });
const skip =
  unavailable ||
  (rail && rail.launchFailed ? `electron did not start: ${rail.detail}` : false);

describe("the bridge loads in a real Electron renderer (tier 2)", () => {
  it("window.pclawDesktop is an object, with no preload-error", { skip }, () => {
    assert.deepStrictEqual(rail.preloadErrors, [], "the preload errored during load");
    assert.strictEqual(rail.bridge, "object", `bridge was ${rail.bridge}`);
    assert.deepStrictEqual(rail.keys, BRIDGE_KEYS);
  });

  it("the probe can SEE a dead bridge — a sandboxed relative require", { skip }, () => {
    // Negative control. Same harness, same sandbox setting the bridged view now uses, pointed
    // at the one preload in this shell that does need Node.
    const dead = probe({
      preload: CONNECT_PRELOAD,
      sandboxOff: false,
      namespace: "pclawConnect",
    });
    assert.strictEqual(dead.launchFailed, undefined, dead.detail);
    assert.strictEqual(dead.bridge, "undefined", "a sandboxed relative require still bridged");
    assert.match(dead.preloadErrors.join("|"), /module not found/);
  });

  it("and that failure is the sandbox, not the file — sandbox: false bridges it", { skip }, () => {
    // Positive control, which is what makes the run above a measurement rather than a guess:
    // the only difference is the flag.
    const live = probe({
      preload: CONNECT_PRELOAD,
      sandboxOff: true,
      namespace: "pclawConnect",
    });
    assert.strictEqual(live.launchFailed, undefined, live.detail);
    assert.deepStrictEqual(live.preloadErrors, []);
    assert.strictEqual(live.bridge, "object");
  });
});
