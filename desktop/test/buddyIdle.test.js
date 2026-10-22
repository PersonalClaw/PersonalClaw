const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { electronUnavailable } = require("./helpers/electronHost");

// ── The buddy's window, in a real Electron (#3831) ─────────────────────────────────────────────
//
// What `buddyWindow.test.js` checks against a fake, checked against the thing itself: the window
// shows without taking focus, stays on top, is kept out of capture, carries only the buddy's
// bridge, and, hidden, its renderer is idle. Hidden, the page reads `document.hidden`, which is
// what `BuddyWindowRoot` unmounts on (`web/src/app/BuddyWindowRoot.test.tsx`); the page here
// stands in for the route and stops as the root does. Over the hidden span the probe counts every
// timer and frame the page schedules and reads the renderer's CPU from `app.getAppMetrics()`.
//
// A page that keeps ticking while hidden is the negative control: the probe must see its timers,
// or a clean run proves nothing.
//
// Skips where Electron cannot start (no binary, or Linux with no display), and says why, as
// `bridgeLoads.test.js`'s tier 2 does.

/** How long the window stays hidden and watched: the issue's 60 s unless the env asks less. */
const IDLE_MS = Number(process.env.PC_BUDDY_IDLE_MS) > 0 ? Number(process.env.PC_BUDDY_IDLE_MS) : 60_000;

function probe(page, idleMs) {
  const res = spawnSync(require("electron"), [path.join(__dirname, "helpers", "buddyProbe.js")], {
    timeout: idleMs + 60_000,
    encoding: "utf8",
    env: { ...process.env, PC_PROBE_PAGE: page, PC_PROBE_IDLE_MS: String(idleMs), ELECTRON_DISABLE_SECURITY_WARNINGS: "1" },
  });
  const line = (res.stdout || "").split("\n").find((l) => l.startsWith("PC_PROBE "));
  if (!line) return { launchFailed: true, detail: `status=${res.status} signal=${res.signal} stderr=${(res.stderr || "").slice(-400)}` };
  return JSON.parse(line.slice("PC_PROBE ".length));
}

const unavailable = electronUnavailable();
const root = unavailable ? null : probe("root", IDLE_MS);
const skip = unavailable || (root && root.launchFailed ? `electron did not start: ${root.detail}` : false);

describe("the buddy's window in a real Electron", () => {
  it("built without error, carrying only the buddy's bridge", { skip }, () => {
    assert.equal(root.error, undefined, root.error);
    assert.deepEqual(root.preloadErrors, []);
    assert.equal(root.bridge.buddy, "object");
    assert.deepEqual(root.bridge.keys, ["dragBy", "dragEnd", "dragStart", "openInApp", "pointerOver", "setPresentation", "setRegions"]);
    assert.equal(root.bridge.desktop, "undefined", "no capability bridge in the buddy's window");
  });

  it("shows without taking focus, on top, and kept out of capture", { skip }, () => {
    assert.equal(root.shown.visible, true);
    assert.equal(root.shown.focused, false);
    assert.equal(root.shown.focusedWindowIsBuddy, false);
    assert.equal(root.shown.alwaysOnTop, true);
    if (root.shown.contentProtected !== null) assert.equal(root.shown.contentProtected, true);
    assert.equal(root.shown.documentHidden, false);
    assert.ok(root.shown.frames > 0, "shown, the page draws");
  });

  it(`is idle while hidden: no timer, no frame, no wakeup and no CPU over ${IDLE_MS / 1000} s`, { skip }, () => {
    assert.equal(root.hidden.documentHidden, true, "a hidden window's page reads hidden, which the root unmounts on");
    assert.equal(root.hidden.timers, 0);
    assert.equal(root.hidden.frames, 0);
    assert.equal(root.hidden.rendererFound, true, "the buddy's renderer is in app.getAppMetrics(), by its pid");
    assert.equal(root.hidden.idleWakeupsPerSecond, 0);
    assert.ok(root.hidden.percentCPU < 0.5, `the renderer used ${root.hidden.percentCPU}% of a core while hidden`);
  });

  it("can see a page that keeps ticking while hidden", { skip }, () => {
    const ticks = probe("ticks", 4000);
    assert.equal(ticks.hidden.documentHidden, true);
    assert.ok(ticks.hidden.timers > 0, "the negative control scheduled nothing the probe could count");
  });
});
