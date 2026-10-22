const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const { builtPageTier, pageUnbuilt } = require("./helpers/builtPage");

// ── A built-page leg skips where it cannot run, except where the host must run it ─────────────
//
// Without the built web app, or with one built from older sources, a built-page leg has nothing
// true to test. A local host skips and says why; under PCLAW_REQUIRE_ELECTRON=1 (CI's web-build) it
// fails with the reason, so a build that went missing cannot pass with the legs skipped.

describe("builtPageTier", () => {
  it("runs where the page is built and Electron starts", () => {
    assert.deepEqual(builtPageTier({ electron: null, page: null }, {}), { skip: false, fail: null });
    assert.deepEqual(builtPageTier({ electron: null, page: null }, { PCLAW_REQUIRE_ELECTRON: "1" }), { skip: false, fail: null });
  });

  it("skips with the reason where nothing requires it", () => {
    assert.deepEqual(builtPageTier({ electron: null, page: "the web app is not built" }, {}), { skip: "the web app is not built", fail: null });
    assert.deepEqual(builtPageTier({ electron: "no display", page: null }, {}), { skip: "no display", fail: null });
  });

  it("fails with the reason where the host must run it: a missing or stale build, or no Electron", () => {
    const missing = builtPageTier({ electron: null, page: "the web app is not built: run `make web-build`" }, { PCLAW_REQUIRE_ELECTRON: "1" });
    assert.equal(missing.skip, false);
    assert.match(missing.fail, /^PCLAW_REQUIRE_ELECTRON=1, and this host cannot run the built page: the web app is not built/);
    const noElectron = builtPageTier({ electron: "no display on linux", page: null }, { PCLAW_REQUIRE_ELECTRON: "1" });
    assert.match(noElectron.fail, /cannot start Electron: no display on linux/);
  });
});

describe("pageUnbuilt", () => {
  const SWITCH = { PCLAW_REQUIRE_ELECTRON: "1" };
  /** A freshness check that answers as told, and records the arguments it was run with. */
  function checker(answer) {
    const calls = [];
    const spawn = (cmd, args) => {
      calls.push(args);
      return typeof answer === "function" ? answer(args) : answer;
    };
    return { spawn, calls };
  }
  const built = () => true;

  it("is a reason for every answer but a clean pass, with the switch and without it", () => {
    const table = [
      ["fresh", { status: 0, stdout: "spa-check: fresh" }, null],
      ["stale", { status: 1, stdout: "spa-check: STALE" }, /older than web\/'s sources/],
      ["a check that fails another way", { status: 2, stdout: "", stderr: "usage: ..." }, /the build's freshness could not be checked: exit 2/],
      ["a check that could not run", { status: null, error: new Error("spawn python3 ENOENT") }, /the build's freshness could not be checked: spawn python3 ENOENT/],
      ["a check that timed out", { status: null, signal: "SIGTERM", error: new Error("spawnSync python3 ETIMEDOUT") }, /could not be checked: spawnSync python3 ETIMEDOUT/],
    ];
    for (const [what, answer, want] of table) {
      for (const env of [{}, SWITCH]) {
        const { spawn } = checker(answer);
        const reason = pageUnbuilt("/repo", { spawn, exists: built, env });
        if (want === null) assert.equal(reason, null, `${what} ${JSON.stringify(env)}`);
        else assert.match(String(reason), want, `${what} ${JSON.stringify(env)}`);
        const tier = builtPageTier({ electron: null, page: reason }, env);
        if (want === null) assert.deepEqual(tier, { skip: false, fail: null });
        else if (env === SWITCH) assert.ok(tier.fail && !tier.skip, `${what}: fails under the switch`);
        else assert.ok(tier.skip && !tier.fail, `${what}: skips without it`);
      }
    }
  });

  it("asks for the stamp where the host must run the legs, so an unstamped build is no pass there", () => {
    // The check passes an unstamped build unless told to require the stamp.
    const unstamped = (args) => (args.includes("--require-stamp") ? { status: 1, stdout: "spa-check: STALE — no stamp" } : { status: 0, stdout: "spa-check: no stamp (note)" });
    const required = checker(unstamped);
    assert.match(pageUnbuilt("/repo", { spawn: required.spawn, exists: built, env: SWITCH }), /older than web\/'s sources/);
    assert.deepEqual(required.calls, [["scripts/spa_dist_freshness.py", "check", "--require-stamp"]]);
    const local = checker(unstamped);
    assert.equal(pageUnbuilt("/repo", { spawn: local.spawn, exists: built, env: {} }), null, "a local host takes the note");
    assert.deepEqual(local.calls, [["scripts/spa_dist_freshness.py", "check"]]);
  });

  it("is not built where there is no index.html, before any check", () => {
    const { spawn, calls } = checker({ status: 0 });
    assert.match(pageUnbuilt("/repo", { spawn, exists: () => false, env: SWITCH }), /the web app is not built/);
    assert.deepEqual(calls, []);
  });
});
