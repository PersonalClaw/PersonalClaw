const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { electronCommand, electronUnavailable, launchDetail } = require("../helpers/electronHost");
const { builtPageTier, pageUnbuilt } = require("../helpers/builtPage");
const { ORB_BOX } = require("../../buddyPlacement");

// ── The orb's box in the real window, the real #/buddy in it (#5216) ───────────────────────────
//
// The built web app's `#/buddy` in a window the shell's own controller builds, places and lays out
// (`makeBuddyWindow`), with the buddy's preload, the shell's `layout` push and its IPC, its lists
// answered by the probe's own scheme (`helpers/buddyProbe.js`, `PC_PROBE_PAGE=orbBox`). Through
// work running with its line card, the card opened and closed, an alert, the whole-UI zoom at 80,
// 150 and 200 %, and a drag, the probe reads the orb's slot, the orb drawn in it and the card from the page, and
// the window's bounds from the shell:
// - the slot is the box the shell laid out (`ORB_BOX` square, what takes a click and a drag), in
//   every step;
// - the orb is drawn at 48 in the slot's middle;
// - the card is inside the area the shell keeps for it, and never covers the orb;
// - nothing but the drag moves or resizes the window, and the orb lands where it was dropped.
//
// A built-page leg (`test/built/`, `npm run test:desktop:built`), skipped and saying why where
// Electron cannot start or the page is not built from this checkout, unless
// `PCLAW_REQUIRE_ELECTRON=1` (`helpers/builtPage.js`).

function probe() {
  const { command, args } = electronCommand([path.join(__dirname, "..", "helpers", "buddyProbe.js")]);
  const res = spawnSync(command, args, {
    timeout: 120_000,
    encoding: "utf8",
    env: { ...process.env, PC_PROBE_PAGE: "orbBox", ELECTRON_DISABLE_SECURITY_WARNINGS: "1" },
  });
  const line = (res.stdout || "").split("\n").find((l) => l.startsWith("PC_PROBE "));
  if (!line) return { launchFailed: true, detail: launchDetail(res) };
  return JSON.parse(line.slice("PC_PROBE ".length));
}

const unavailable = electronUnavailable();
const unbuilt = unavailable ? null : pageUnbuilt();
const run = unavailable || unbuilt ? null : probe();
const { skip, fail } = builtPageTier({
  electron: unavailable || (run && run.launchFailed ? `electron did not start: ${run.detail}` : null),
  page: unbuilt,
});

/** Within half a point: the page lays out in fractions a rounding of the shell's points may show. */
function near(actual, expected, what) {
  for (const k of ["x", "y", "width", "height"]) assert.ok(Math.abs(actual[k] - expected[k]) <= 0.5, `${what}: ${k} is ${actual[k]}, not ${expected[k]}`);
}
const overlaps = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
const inside = (r, area) => r.x >= area.x - 0.5 && r.y >= area.y - 0.5 && r.x + r.width <= area.x + area.width + 0.5 && r.y + r.height <= area.y + area.height + 0.5;

describe("the orb holds its box in the real window, the real #/buddy in it (#5216)", () => {
  if (fail) it("runs the built page in a real Electron on this host", () => assert.fail(fail));
  const skipped = skip || (fail ? "this host cannot run the built page: the test above fails with why" : false);
  const steps = () => run.orbBox.steps;

  it("loads the route in the window the shell lays out, and plays every step", { skip: skipped }, () => {
    assert.equal(run.error, undefined, run.error);
    assert.equal(run.orbBox.error, undefined, run.orbBox.error);
    assert.deepEqual(run.preloadErrors, []);
    const names = steps().map((s) => s.step);
    assert.deepEqual(names, ["work running", "card open", "card closed", "an alert", "zoom 80%", "zoom 150%", "zoom 200%", "dropped"]);
    const at = Object.fromEntries(steps().map((s) => [s.step, s]));
    assert.ok(at["work running"].card, "work running shows its line card");
    assert.ok(at["card open"].card.height > at["work running"].card.height, "the card opened is the full card");
    assert.equal(at["an alert"].alert, true, "the alert's card shows");
  });

  it("draws the orb's slot at the box the shell laid out, at its one size, in every step", { skip: skipped }, () => {
    for (const s of steps()) {
      near(s.slot, s.layout.orb, `${s.step}: the slot is the shell's box`);
      assert.deepEqual({ width: s.layout.orb.width, height: s.layout.orb.height }, { width: ORB_BOX, height: ORB_BOX }, `${s.step}: the shell's orb box`);
    }
  });

  it("draws the orb at 48 in the middle of its slot, in every step", { skip: skipped }, () => {
    for (const s of steps()) {
      near({ ...s.orb, x: 0, y: 0 }, { x: 0, y: 0, width: 48, height: 48 }, `${s.step}: drawn at 48`);
      assert.ok(Math.abs(s.orb.x + s.orb.width / 2 - (s.slot.x + s.slot.width / 2)) <= 0.5, `${s.step}: centred across`);
      assert.ok(Math.abs(s.orb.y + s.orb.height / 2 - (s.slot.y + s.slot.height / 2)) <= 0.5, `${s.step}: centred down`);
    }
  });

  it("keeps the card inside the area the shell keeps for it, never over the orb", { skip: skipped }, () => {
    for (const s of steps().filter((x) => x.card)) {
      assert.ok(inside(s.card, s.layout.card), `${s.step}: the card is in its area`);
      assert.equal(overlaps(s.card, s.slot), false, `${s.step}: the card never covers the orb`);
    }
  });

  it("moves and resizes the window for nothing but the drag, and the orb lands where it was dropped", { skip: skipped }, () => {
    const before = steps().filter((s) => s.step !== "dropped");
    for (const s of before) assert.deepEqual(s.window, before[0].window, `${s.step}: the window is where it was, at its size`);
    const dropped = steps().find((s) => s.step === "dropped");
    near({ ...dropped.slot, x: dropped.window.x + dropped.slot.x, y: dropped.window.y + dropped.slot.y }, run.orbBox.expectedDrop, "the orb on the screen, after the drop");
  });
});
