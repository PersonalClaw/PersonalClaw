const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { REQUIRE_ELECTRON, electronTier } = require("./helpers/electronHost");

// ── A real-Electron tier skips where it cannot run, except where the host must run it ─────────
//
// CI's web job sets PCLAW_REQUIRE_ELECTRON=1: there, a runner that cannot start Electron (no
// binary, no display, a sandbox the kernel refuses) fails the tier with the reason, instead of
// passing with every leg skipped. A local host without Electron still skips, out loud.

describe("electronTier", () => {
  it("runs the tier where Electron starts, switch or not", () => {
    assert.deepEqual(electronTier(null, {}), { skip: false, fail: null });
    assert.deepEqual(electronTier(null, { [REQUIRE_ELECTRON]: "1" }), { skip: false, fail: null });
  });

  it("skips with the reason where it cannot start and nothing requires it", () => {
    for (const env of [{}, { [REQUIRE_ELECTRON]: "0" }, { [REQUIRE_ELECTRON]: "" }, { [REQUIRE_ELECTRON]: "true" }]) {
      assert.deepEqual(electronTier("no display on linux", env), { skip: "no display on linux", fail: null }, JSON.stringify(env));
    }
  });

  it("fails with the reason where it cannot start and the host must run it", () => {
    const { skip, fail } = electronTier("electron did not start: status=1 signal=null stderr=No usable sandbox!", { [REQUIRE_ELECTRON]: "1" });
    assert.equal(skip, false);
    assert.match(fail, /^PCLAW_REQUIRE_ELECTRON=1, and this host cannot start Electron: electron did not start: .*No usable sandbox!$/);
  });
});

describe("a tier wired as bridgeLoads.test.js and buddyIdle.test.js are", () => {
  /** Run a tier file shaped like the two real ones, whose host cannot start Electron. */
  function runTier(env) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pclaw-tier-"));
    const file = path.join(dir, "tier.test.js");
    fs.writeFileSync(file, `
      const { describe, it } = require("node:test");
      const assert = require("node:assert/strict");
      const { electronTier } = require(${JSON.stringify(path.join(__dirname, "helpers", "electronHost.js"))});
      const { skip, fail } = electronTier("no display on linux");
      describe("tier", () => {
        if (fail) it("starts a real Electron on this host", () => assert.fail(fail));
        const skipped = skip || (fail ? "this host cannot start Electron: the test above fails with why" : false);
        it("a leg", { skip: skipped }, () => { throw new Error("ran without Electron"); });
      });
    `);
    // Not as a child of this runner: NODE_TEST_CONTEXT would make it report to us and exit 0.
    const { NODE_TEST_CONTEXT: _child, ...outer } = process.env;
    const res = spawnSync(process.execPath, ["--test", "--test-reporter=tap", file], { encoding: "utf8", env: { ...outer, ...env } });
    fs.rmSync(dir, { recursive: true, force: true });
    return res;
  }

  it("goes red under the switch, saying why, and runs no leg", () => {
    const res = runTier({ [REQUIRE_ELECTRON]: "1" });
    assert.notEqual(res.status, 0, "the tier must fail");
    assert.match(res.stdout, /this host cannot start Electron: no display on linux/);
    assert.doesNotMatch(res.stdout, /ran without Electron/);
  });

  it("stays green without it, with every leg skipped and the reason said", () => {
    const res = runTier({ [REQUIRE_ELECTRON]: "" });
    assert.equal(res.status, 0, res.stdout);
    assert.match(res.stdout, /# SKIP no display on linux/);
  });

  it("is how both real tiers are wired", () => {
    for (const name of ["bridgeLoads.test.js", "buddyIdle.test.js"]) {
      const src = fs.readFileSync(path.join(__dirname, name), "utf8");
      assert.match(src, /const \{ skip, fail \} = electronTier\(/, name);
      assert.match(src, /if \(fail\) it\("starts a real Electron on this host", \(\) => assert\.fail\(fail\)\);/, name);
      assert.doesNotMatch(src, /\{ skip \}/, `${name}: every leg skips on \`skipped\`, so none runs without Electron`);
    }
  });
});
