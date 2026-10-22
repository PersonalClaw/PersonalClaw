const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { REQUIRE_ELECTRON, contentProtection, electronTier, launchDetail, sandboxHelperProblem } = require("./helpers/electronHost");

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

describe("sandboxHelperProblem", () => {
  const BIN = "/repo/node_modules/electron/dist/electron";
  const at = (uid, mode) => () => ({ uid, mode: 0o100000 | mode });

  it("passes a helper that is root's with mode 4755", () => {
    assert.equal(sandboxHelperProblem(BIN, at(0, 0o4755)), null);
  });

  it("names a helper the runner left as it unpacked it, or with the wrong bits, or missing", () => {
    assert.match(sandboxHelperProblem(BIN, at(1001, 0o755)), /chrome-sandbox is uid 1001, mode 755: it must be root's with mode 4755/);
    assert.match(sandboxHelperProblem(BIN, at(0, 0o755)), /uid 0, mode 755/);
    assert.match(sandboxHelperProblem(BIN, at(1001, 0o4755)), /uid 1001, mode 4755/);
    assert.match(sandboxHelperProblem(BIN, () => { throw new Error("ENOENT"); }), /no setuid sandbox helper at \/repo\/node_modules\/electron\/dist\/chrome-sandbox/);
  });
});

describe("launchDetail", () => {
  it("says why from either stream, since xvfb-run hands stderr to stdout", () => {
    const fatal = "[123:FATAL:zygote_host_impl_linux.cc(127)] No usable sandbox!";
    const viaXvfb = launchDetail({ status: 133, signal: null, stderr: "", stdout: `noise\n${fatal}\n` });
    assert.match(viaXvfb, /^status=133 signal=null stderr= stdout=noise\n.*No usable sandbox!$/s);
    assert.match(launchDetail({ status: 1, signal: null, stderr: fatal, stdout: "" }), /stderr=.*No usable sandbox!/);
    assert.ok(launchDetail({ status: 1, signal: null, stderr: "x".repeat(5000), stdout: "" }).length < 700, "a tail, not the whole output");
  });
});

describe("a tier wired as the real ones are", () => {
  /** Run a tier file shaped like the real ones, whose host cannot start Electron. */
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

  it("is how every real tier is wired", () => {
    for (const name of ["bridgeLoads.test.js", "buddyIdle.test.js"]) {
      const src = fs.readFileSync(path.join(__dirname, name), "utf8");
      assert.match(src, /const \{ skip, fail \} = electronTier\(/, name);
      assert.match(src, /if \(fail\) it\("starts a real Electron on this host", \(\) => assert\.fail\(fail\)\);/, name);
      assert.doesNotMatch(src, /\{ skip \}/, `${name}: every leg skips on \`skipped\`, so none runs without Electron`);
    }
    // The built-page legs take the built-page tier, which fails a missing build too (`builtPage.js`).
    for (const name of fs.readdirSync(path.join(__dirname, "built")).filter((f) => f.endsWith(".test.js"))) {
      const src = fs.readFileSync(path.join(__dirname, "built", name), "utf8");
      assert.match(src, /const \{ skip, fail \} = builtPageTier\(/, name);
      assert.match(src, /if \(fail\) it\("runs the built page in a real Electron on this host", \(\) => assert\.fail\(fail\)\);/, name);
      assert.doesNotMatch(src, /\{ skip \}/, name);
    }
  });
});

// ── Content protection is read only where Electron can set it ─────────────────────────────────
//
// `setContentProtection` works on macOS and Windows only, so on Linux `isContentProtected()` reads
// false whatever the window asked for. The probe reports null there, and buddyIdle.test.js skips
// that one check; where it can be set, a window left unprotected still fails the leg.

describe("contentProtection", () => {
  const win = (value) => ({ isContentProtected: () => value });

  it("reads null on Linux, so a false there is neither a pass nor a failure", () => {
    assert.equal(contentProtection(win(false), "linux"), null);
    assert.equal(contentProtection(win(true), "linux"), null);
  });

  it("reads the window's own answer on macOS and Windows, so an unprotected window fails there", () => {
    for (const platform of ["darwin", "win32"]) {
      assert.equal(contentProtection(win(false), platform), false, platform);
      assert.equal(contentProtection(win(true), platform), true, platform);
    }
  });

  it("reads null from a window with no reading", () => {
    assert.equal(contentProtection({}, "darwin"), null);
  });

  it("is what the buddy's probe reports", () => {
    const src = fs.readFileSync(path.join(__dirname, "helpers", "buddyProbe.js"), "utf8");
    assert.match(src, /contentProtected: contentProtection\(win\)/);
  });
});
