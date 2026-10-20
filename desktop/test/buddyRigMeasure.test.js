const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

// The rig spike's measurement (#3887) reads a scratch window's raw numbers against the bars in
// docs/design/buddy/rig.md and names the provisional rig by the issue's rule: the smallest by the
// chunks only the candidate loads, among the candidates whose every run passes the at-rest and the
// animating CPU bars. The window itself needs a gateway and a display; what it is read with does
// not, so these hold that reading to the rule.

const SCRIPT = path.resolve(__dirname, "..", "..", "docs", "design", "buddy", "decisions", "rig-measure.mjs");
const load = () => import(pathToFileURL(SCRIPT).href);

const rows = (cpu, kb = 40 * 1024) => Array.from({ length: 60 }, (_, i) => ({
  t: i + 1,
  renderer: { cpu: typeof cpu === "function" ? cpu(i) : cpu, workingSetKb: kb },
  load: [10, 10, 10],
}));

/** A run that passes every bar; `patch` breaks the one a test is about. */
function run(candidate, patch = {}) {
  const dials = [];
  for (const bounciness of [0, 1]) {
    for (const expressiveness of [0, 1]) {
      for (const key of ["spread", "lift", "sink", "swirl"]) {
        dials.push({ bounciness, expressiveness, key, amplitude: expressiveness ? 0.3 : 0.1, overshoot: bounciness && key !== "sink" ? 0.06 : 0.002 });
      }
    }
  }
  return {
    candidate,
    rest: { rows: rows(0.1), callbacks: 0, frames: 0 },
    loop: { rows: rows(2), callbacks: 3000, frames: 3000 },
    reduced: { rows: rows(0.2), setting: true, callbacks: 0, frames: 0, valueChanges: { [`${candidate}.lift`]: 3 }, steps: 25 },
    retint: { moving: { framesToFollow: { [candidate]: 1 } }, still: { framesToFollow: { [candidate]: 1 } } },
    dials,
    bundle: { candidate: [{ name: "assets/Candidate.js", bytes: 4000, gzip: 2048 }], route: [] },
    ...patch,
  };
}

const verdict = (v, measure) => v.find((m) => m.measure === measure);

describe("the numbers are read against the bars", () => {
  it("the working hold alone is read beside the loop, with no bar of its own", async () => {
    const { verdicts } = await load();
    const v = verdict(verdicts(run("layered", { hold: { rows: rows(9), callbacks: 2400, frames: 2400 } })), "The working hold, alone");
    assert.equal(v.passes, null);
    assert.deepEqual(v.value, { cpu: 9, fps: 40 });
  });

  it("a run that clears every bar passes every measure", async () => {
    const { verdicts } = await load();
    const failed = verdicts(run("layered")).filter((m) => m.passes === false).map((m) => m.measure);
    assert.deepEqual(failed, []);
  });

  it("CPU is the mean over the window, in percent of one core", async () => {
    const { verdicts } = await load();
    const spiky = verdicts(run("layered", { rest: { rows: rows((i) => (i === 0 ? 6 : 0.3)), callbacks: 0 } }));
    assert.equal(verdict(spiky, "CPU at rest, visible").passes, true, "one busy second over sixty is a mean of 0.4");
    const busy = verdicts(run("layered", { rest: { rows: rows(0.6), callbacks: 0 } }));
    assert.equal(verdict(busy, "CPU at rest, visible").passes, false);
    const hot = verdicts(run("layered", { loop: { rows: rows(5.5), callbacks: 1 } }));
    assert.equal(verdict(hot, "CPU while animating").passes, false);
  });

  it("one frame callback while still fails the zero-frames bar", async () => {
    const { verdicts } = await load();
    const v = verdicts(run("in-place", { rest: { rows: rows(0.1), callbacks: 1 } }));
    assert.equal(verdict(v, "Frames while still").passes, false);
  });

  it("the bundle is the gzip of the chunks only the candidate loads", async () => {
    const { verdicts } = await load();
    const big = run("in-place", { bundle: { candidate: [{ name: "a.js", bytes: 1, gzip: 100 * 1024 }, { name: "b.js", bytes: 1, gzip: 60 * 1024 }], route: [] } });
    const v = verdict(verdicts(big), "Bundle size");
    assert.equal(v.value, 160);
    assert.equal(v.passes, false);
  });

  it("a retint that takes a second frame misses the one-frame bar", async () => {
    const { verdicts } = await load();
    const slow = run("in-place", { retint: { moving: { framesToFollow: { "in-place": 2 } }, still: { framesToFollow: { "in-place": 1 } } } });
    assert.equal(verdict(verdicts(slow), "Live retint").passes, false);
  });

  it("dials that do not scale, or a value that moves under reduced motion, fail", async () => {
    const { verdicts } = await load();
    const flat = run("layered");
    flat.dials = flat.dials.map((d) => ({ ...d, overshoot: 0.06 }));
    assert.equal(verdict(verdicts(flat), "The dials").passes, false, "overshoot at bounciness 0");
    const moving = run("layered", { reduced: { rows: rows(0.2), setting: true, callbacks: 40, valueChanges: { "layered.lift": 90 }, steps: 25 } });
    assert.equal(verdict(verdicts(moving), "Reduced motion").passes, false);
  });
});

describe("the provisional choice follows the issue's rule", () => {
  const sized = (candidate, kb, patch) => run(candidate, { ...patch, bundle: { candidate: [{ name: "x.js", bytes: 1, gzip: kb * 1024 }], route: [] } });

  it("the smallest of the candidates that pass both CPU bars", async () => {
    const { provisional } = await load();
    const choice = provisional([sized("in-place", 3), sized("in-place", 3), sized("layered", 2), sized("layered", 2)]);
    assert.equal(choice.choice, "layered");
  });

  it("a smaller candidate with one run over a CPU bar is passed over", async () => {
    const { provisional } = await load();
    const choice = provisional([sized("in-place", 3), sized("in-place", 3), sized("layered", 2), sized("layered", 2, { loop: { rows: rows(7), callbacks: 1 } })]);
    assert.equal(choice.choice, "in-place");
  });

  it("names none when none passes, or two tie", async () => {
    const { provisional } = await load();
    const over = { rest: { rows: rows(1), callbacks: 0 } };
    assert.equal(provisional([sized("in-place", 3, over), sized("layered", 2, over)]).choice, null);
    assert.equal(provisional([sized("in-place", 2), sized("layered", 2)]).choice, null);
  });
});

describe("what the window loaded becomes the candidate's chunks", () => {
  it("the shared app code is what index.html loads", async () => {
    const { entryAssets } = await load();
    const html = '<script type="module" crossorigin src="/assets/index-a.js"></script>\n<link rel="modulepreload" crossorigin href="/assets/react-b.js">\n<link rel="stylesheet" crossorigin href="/assets/index-c.css">';
    assert.deepEqual([...entryAssets(html)].sort(), ["/assets/index-a.js", "/assets/index-c.css", "/assets/react-b.js"]);
  });

  it("the candidate's chunks are those it loaded that the harness alone did not", async () => {
    const { chunksOf } = await load();
    const chunks = chunksOf({
      entry: new Set(["assets/index-a.js"]),
      baseline: ["assets/index-a.js", "assets/BuddyRigHarness-h.js"],
      loaded: ["assets/index-a.js", "assets/BuddyRigHarness-h.js", "assets/CandidateLayered-l.js", "assets/usePose-p.js"],
    });
    assert.deepEqual(chunks.candidate, ["assets/CandidateLayered-l.js", "assets/usePose-p.js"]);
    assert.deepEqual(chunks.route, ["assets/BuddyRigHarness-h.js", "assets/CandidateLayered-l.js", "assets/usePose-p.js"]);
  });

  it("summarises a window of samples", async () => {
    const { stats } = await load();
    assert.deepEqual(stats([3, 1, null, 2]), { n: 3, mean: 2, p50: 2, p95: 3, max: 3 });
    assert.deepEqual(stats([]), { n: 0, mean: null, p50: null, p95: null, max: null });
  });
});
