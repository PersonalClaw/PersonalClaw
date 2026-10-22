const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { electronCommand, electronUnavailable, launchDetail } = require("../helpers/electronHost");
const { builtPageTier, pageUnbuilt } = require("../helpers/builtPage");

// ── What the real buddy costs, measured in its own window (#3902) ───────────────────────────────
//
// `buddyIdle.test.js` stands a page in for the route; this is the route: the built web app's
// `#/buddy` in the buddy's real window, built by `buddyWindow.js` with its own preload, shown and
// hidden as the shell shows and hides it, its lists answered by the probe's own scheme
// (`helpers/buddyProbe.js`, `PC_PROBE_PAGE=route`). Each mode is measured for the idle span against
// the rig's bars (`docs/design/buddy/rig.md`):
// - still (shown, nothing running, nothing to draw): no frame, no timer, under 0.5% of a core;
// - animating (one errand thinking): the orb draws and moves, under 5%;
// - covered (animating under a window above it): the shell's show keeps it drawing (#5210), under
//   the animating bar;
// - hidden: no frame, no timer, under 0.5%.
// Shared runners are noisy (#3902's open question 2), so CI holds the CPU bars at ×3
// (`PC_BUDDY_CPU_MARGIN`); the report prints each mode beside the design's own bar. The renderer's
// working set is reported against its 60 MB bar, not held to it, until #3829's memory task decides
// it. Every run writes the report to the CI job summary (`$GITHUB_STEP_SUMMARY`): the owner-visible
// cost report Gate D asks for.
//
// A built-page leg (`test/built/`, `npm run test:desktop:built`): CI's web-build runs it after its
// build and stamp. Where Electron cannot start, or the page is not built or was built from older
// sources than the checkout's, it skips and says why, unless `PCLAW_REQUIRE_ELECTRON=1` says the
// host must run it (`helpers/builtPage.js`). `make web-build` builds the page.

/** How long each mode is watched: the issue's 60 s unless the env asks less. */
const SPAN_MS = Number(process.env.PC_BUDDY_IDLE_MS) > 0 ? Number(process.env.PC_BUDDY_IDLE_MS) : 60_000;
const MODES = ["still", "animating", "covered", "hidden"];
const BARS = { still: 0.5, animating: 5, covered: 5, hidden: 0.5 };
const MEMORY_BAR_MB = 60;
const MARGIN = Number(process.env.PC_BUDDY_CPU_MARGIN) > 0 ? Number(process.env.PC_BUDDY_CPU_MARGIN) : 3;

function probe() {
  const { command, args } = electronCommand([path.join(__dirname, "..", "helpers", "buddyProbe.js")]);
  const res = spawnSync(command, args, {
    timeout: MODES.length * SPAN_MS + 90_000,
    encoding: "utf8",
    env: { ...process.env, PC_PROBE_PAGE: "route", PC_PROBE_IDLE_MS: String(SPAN_MS), ELECTRON_DISABLE_SECURITY_WARNINGS: "1" },
  });
  const line = (res.stdout || "").split("\n").find((l) => l.startsWith("PC_PROBE "));
  if (!line) return { launchFailed: true, detail: launchDetail(res) };
  return JSON.parse(line.slice("PC_PROBE ".length));
}

/** The cost report, one row per mode, as the CI job summary shows it. */
function costReport(run) {
  const fmt = (n, d = 2) => (typeof n === "number" ? n.toFixed(d) : "n/a");
  const rows = MODES.map((mode) => {
    const m = run.modes[mode];
    return `| ${mode} | ${fmt(m.percentCPU, 3)}% | < ${BARS[mode]}% | ${m.frames} | ${m.timers} | ${fmt(m.idleWakeupsPerSecond, 0)} | ${fmt(m.workingSetMB, 1)} MB | < ${MEMORY_BAR_MB} MB |`;
  });
  return [
    `### The buddy's cost, measured (${process.platform}, ${SPAN_MS / 1000} s a mode)`,
    "",
    "| Mode | CPU (one core) | Bar | Frames | Timers | Wakeups/s | Working set | Bar |",
    "|---|---|---|---|---|---|---|---|",
    ...rows,
    "",
    `CI holds the CPU bars at ×${MARGIN} for shared runners; the working set is reported, not held.`,
    "",
  ].join("\n");
}

const unavailable = electronUnavailable();
const unbuilt = unavailable ? null : pageUnbuilt();
const run = unavailable || unbuilt ? null : probe();
const { skip, fail } = builtPageTier({
  electron: unavailable || (run && run.launchFailed ? `electron did not start: ${run.detail}` : null),
  page: unbuilt,
});

describe("the real #/buddy in the buddy's window costs what its bars say", () => {
  if (fail) it("runs the built page in a real Electron on this host", () => assert.fail(fail));
  const skipped = skip || (fail ? "this host cannot run the built page: the test above fails with why" : false);

  it("loads the route in the buddy's own window, and draws it", { skip: skipped }, () => {
    assert.equal(run.error, undefined, run.error);
    assert.equal(run.routeMissing, undefined, run.routeMissing);
    assert.deepEqual(run.preloadErrors, []);
    for (const mode of MODES) assert.equal(run.modes[mode].rendererFound, true, `${mode}: the buddy's renderer is in app.getAppMetrics()`);
    assert.ok(run.modes.animating.drawn > 0, "an errand thinking draws the orb");
    assert.ok(run.modes.animating.frames > 0, "and it moves");
  });

  it("still: shown, nothing to draw, no frame, no timer, and under its bar", { skip: skipped }, () => {
    const m = run.modes.still;
    assert.equal(m.documentHidden, false);
    assert.equal(m.frames, 0);
    assert.equal(m.timers, 0);
    assert.ok(m.percentCPU < BARS.still * MARGIN, `still used ${m.percentCPU}% of a core`);
  });

  it("animating: under its bar", { skip: skipped }, () => {
    const m = run.modes.animating;
    assert.ok(m.percentCPU < BARS.animating * MARGIN, `animating used ${m.percentCPU}% of a core`);
  });

  it("covered: it keeps drawing, as the shell's show asks, under the animating bar", { skip: skipped }, () => {
    const m = run.modes.covered;
    assert.equal(m.documentHidden, false, "covered, the page still reads shown");
    assert.ok(m.frames > 0, "covered and animating, it keeps drawing (#5210)");
    assert.ok(m.percentCPU < BARS.covered * MARGIN, `covered used ${m.percentCPU}% of a core`);
  });

  it("hidden: no frame, no timer, and idle", { skip: skipped }, () => {
    const m = run.modes.hidden;
    assert.equal(m.documentHidden, true);
    assert.equal(m.frames, 0, "hidden, the buddy asks for no frame");
    assert.equal(m.timers, 0, "hidden, the buddy sets no timer");
    assert.ok(m.percentCPU < BARS.hidden * MARGIN, `hidden used ${m.percentCPU}% of a core`);
  });

  it("writes its cost report, to the CI job summary where there is one", { skip: skipped }, () => {
    const report = costReport(run);
    for (const mode of MODES) assert.match(report, new RegExp(`\\| ${mode} \\|`));
    process.stdout.write(`${report}\n`);
    if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${report}\n`);
  });
});
