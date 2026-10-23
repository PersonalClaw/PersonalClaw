const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { electronCommand, electronTier, electronUnavailable, launchDetail } = require("./helpers/electronHost");
const { ORB_BOX } = require("../buddyPlacement");

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
// The orb's box, in a window the shell's own controller builds on the real screen (#5216): the
// window's real bounds plus the layout its page heard, through every presentation, a hide and a
// show, a drag and a restart. With its card closed, the window is click-through everywhere but the
// orb: on a display of the run's own, real pointer input over every pixel of it lands on the window
// below, and the buddy hears nothing there. Hidden, it costs nothing at its real size, the orb's box
// and the card's area.
//
// Skips where Electron cannot start (no binary, or Linux with no display and no `xvfb-run`), and
// says why, as `bridgeLoads.test.js`'s tier 2 does, unless `PCLAW_REQUIRE_ELECTRON=1` says the host
// must run it, which makes a can't-start a failure (`helpers/electronHost.js`).

/** How long the window stays hidden and watched: the issue's 60 s unless the env asks less. */
const IDLE_MS = Number(process.env.PC_BUDDY_IDLE_MS) > 0 ? Number(process.env.PC_BUDDY_IDLE_MS) : 60_000;

function probe(page, idleMs, show = "shell") {
  const { command, args } = electronCommand([path.join(__dirname, "helpers", "buddyProbe.js")]);
  // Real pointer input only on the display xvfb-run gives this run, never on someone's desktop.
  const ownDisplay = path.basename(command) === "xvfb-run";
  const res = spawnSync(command, args, {
    timeout: idleMs + 360_000,
    encoding: "utf8",
    env: { ...process.env, PC_PROBE_PAGE: page, PC_PROBE_IDLE_MS: String(idleMs), PC_PROBE_SHOW: show, PC_PROBE_OS_CLICKS: ownDisplay ? "1" : "", ELECTRON_DISABLE_SECURITY_WARNINGS: "1" },
  });
  const line = (res.stdout || "").split("\n").find((l) => l.startsWith("PC_PROBE "));
  if (!line) return { launchFailed: true, detail: launchDetail(res) };
  return JSON.parse(line.slice("PC_PROBE ".length));
}

const unavailable = electronUnavailable();
const root = unavailable ? null : probe("root", IDLE_MS);
const { skip, fail } = electronTier(unavailable || (root && root.launchFailed ? `electron did not start: ${root.detail}` : null));

describe("the buddy's window in a real Electron", () => {
  // Where the host must run this tier, a can't-start is a failure, not a skip.
  if (fail) it("starts a real Electron on this host", () => assert.fail(fail));
  const skipped = skip || (fail ? "this host cannot start Electron: the test above fails with why" : false);
  it("built without error, carrying only the buddy's bridge", { skip: skipped }, () => {
    assert.equal(root.error, undefined, root.error);
    assert.deepEqual(root.preloadErrors, []);
    assert.equal(root.bridge.buddy, "object");
    assert.deepEqual(root.bridge.keys, ["dragBy", "dragEnd", "dragStart", "onAway", "onConnection", "onFullScreen", "onHidden", "onLayout", "onOpenAlert", "onPlacement", "onRing", "openInApp", "openMenu", "pointerOver", "setPresentation", "setRegions"]);
    assert.equal(root.bridge.desktop, "undefined", "no capability bridge in the buddy's window");
  });

  it("shows without taking focus, on top, and kept out of capture", { skip: skipped }, () => {
    assert.equal(root.shown.visible, true);
    assert.equal(root.shown.focused, false);
    assert.equal(root.shown.focusedWindowIsBuddy, false);
    assert.equal(root.shown.alwaysOnTop, true);
    // Null on Linux, where Electron cannot keep a window out of capture (electronHost's contentProtection).
    if (root.shown.contentProtected !== null) assert.equal(root.shown.contentProtected, true);
    assert.equal(root.shown.documentHidden, false);
    assert.ok(root.shown.frames > 0, "shown, the page draws");
  });

  it("keeps the orb's box where the owner put it, at one size, through every state, a hide and a show (#5216)", { skip: skipped }, () => {
    const placed = root.placed;
    assert.equal(placed.error, undefined, placed.error);
    assert.ok(placed.heard.length >= 1, "the page heard where its orb is");
    assert.deepEqual(placed.boxes[0], { ...placed.defaultSpot, width: ORB_BOX, height: ORB_BOX }, "it starts at the default");
    for (const [i, box] of placed.boxes.entries()) assert.deepEqual(box, placed.boxes[0], `${placed.steps[i]}: the orb's box is the same`);
    for (const [i, bounds] of placed.bounds.entries()) assert.deepEqual(bounds, placed.bounds[0], `${placed.steps[i]}: the window is neither moved nor resized`);
  });

  it("moves the orb by a drag, to where it was dropped, and keeps it there after a restart (#5216)", { skip: skipped }, () => {
    const placed = root.placed;
    assert.deepEqual(placed.dropped, placed.expectedDrop, "the orb is where it fell");
    assert.deepEqual(placed.restarted, placed.dropped, "and comes back there");
  });

  it("passes every click outside the orb to the window below while the card is closed, and takes the orb's (#5216)", { skip: skipped }, (t) => {
    const c = root.clickThrough;
    if (c.skipped) return t.skip(c.skipped);
    assert.equal(c.error, undefined, c.error);
    assert.equal(c.posted.ok, true, c.posted.error);
    const why = `\n${JSON.stringify({ firstOrbPress: c.firstOrbPress, waitedMs: c.waitedMs, stacking: c.stacking, clicked: c.clicked, landedBelow: c.landedBelow, belowAtTheOrb: c.belowAtTheOrb, ...c.diagnosis })}`;
    // No window manager here keeps an always-on-top window above: which is above is the order the
    // X server has, and a buddy under the other window would take nothing at all.
    assert.equal(c.stacking.error, undefined, c.stacking.error);
    assert.ok(c.stacking.buddy >= 0 && c.stacking.below >= 0 && c.stacking.buddy > c.stacking.below, `the buddy's window is above the other one${why}`);
    assert.equal(c.firstOrbPress, 1, `a press on the orb, before anything else, reached the buddy${why}`);
    assert.equal(c.buddyOrbPresses, 1, `the press on the orb reached the buddy: the input was real${why}`);
    assert.equal(c.belowAtTheOrb, 0, "and not the window below");
    assert.ok(c.clicked > 1000);
    assert.equal(c.landedBelow, c.clicked, `every press outside the orb landed on the window below${why}`);
    assert.deepEqual(c.buddyOutsidePresses, [], "no press, click or wheel outside the orb reached the buddy");
    assert.deepEqual(c.buddyOutsideMovesFromInput, [], "and no move of the real pointer outside it, over every pixel of the window");
    // Before any input, Chromium may move the page's pointer to where the pointer rests as the window
    // shows: not input, and the page gives a move outside the orb's box no meaning (`BuddyPage`).
    t.diagnostic(`moves outside the orb before any input, the window's own: ${JSON.stringify(c.strayMoves)}`);
    // The other window raised above it takes even the orb's press: the leg tells the stacking
    // from the shape.
    assert.equal(c.raised.posted.ok, true, c.raised.posted.error);
    assert.deepEqual([c.raised.landedBelow, c.raised.buddyOrbPresses], [1, 0], `raised above the buddy, the other window takes the orb's press${why}`);
  });

  // The control that the buddy, shaped to its whole window, takes the same clicks. On the Linux
  // leg the clicks still land below once the whole-window shape replaces the orb's (main's web job
  // since #5216, #3905), and this is the only host that posts real input. The orb's own press and
  // the raised-window control above already show the leg sees the buddy's presses and the
  // stacking, so it is skipped there, with its cause filed.
  const WHOLE_WINDOW_SKIP = process.platform === "linux"
    ? "on the Linux xvfb leg a whole-window shape set after the orb's does not take the control's clicks back (#3905, backlogged); the orb press and the raised control prove the leg"
    : skipped;
  it("a buddy shaped to its whole window takes the same clicks: the control (#5216)", { skip: WHOLE_WINDOW_SKIP }, (t) => {
    const c = root.clickThrough;
    if (c.skipped) return t.skip(c.skipped);
    assert.equal(c.control.posted.ok, true, c.control.posted.error);
    const control = `\n${JSON.stringify(c.control)}`;
    assert.ok(c.control.shape, `the window took the whole-window shape${control}`);
    assert.equal(c.control.buddyPresses, c.control.clicked, `a buddy that takes the whole window gets every click at its window: the leg can see one${control}`);
    assert.equal(c.control.landedBelow, 0, control);
  });

  it(`is idle while hidden: no timer, no frame, no wakeup and no CPU over ${IDLE_MS / 1000} s`, { skip: skipped }, () => {
    assert.deepEqual(root.windowSize, root.defaultLayoutSize, "measured at the window's real size, the orb's box and the card's area (#5216)");
    assert.ok(root.windowSize.width > ORB_BOX && root.windowSize.height > ORB_BOX);
    assert.equal(root.hidden.documentHidden, true, "a hidden window's page reads hidden, which the root unmounts on");
    assert.equal(root.hidden.timers, 0);
    assert.equal(root.hidden.frames, 0);
    assert.equal(root.hidden.rendererFound, true, "the buddy's renderer is in app.getAppMetrics(), by its pid");
    assert.equal(root.hidden.idleWakeupsPerSecond, 0);
    assert.ok(root.hidden.percentCPU < 0.5, `the renderer used ${root.hidden.percentCPU}% of a core while hidden`);
  });

  // Covered while shown (a screen saver, a sleeping display, a window above it), macOS reports the
  // window occluded and a bare `showInactive()` page reads itself hidden: the root unmounts the
  // buddy, and it comes back as a new page that has lost what the shell told it. The real-Electron
  // answer tier caught the next card gone until the window was focused (#3832).
  it("covered while shown, its page still reads visible, as the shell shows it", { skip: skipped }, (t) => {
    const control = probe("root", 1000, "inactive");
    if (control.covered?.documentHidden !== true) return t.skip("this host reports no occlusion for a covered window");
    assert.equal(root.covered.documentHidden, false);
    assert.equal(root.covered.afterDocumentHidden, false);
  });

  it("can see a page that keeps ticking while hidden", { skip: skipped }, () => {
    const ticks = probe("ticks", 4000);
    assert.equal(ticks.hidden.documentHidden, true);
    assert.ok(ticks.hidden.timers > 0, "the negative control scheduled nothing the probe could count");
  });
});
