const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

// ── The desktop tier's built-page legs run after the build, and nowhere else ──────────────────
//
// A leg that drives the built web app (`web/dist`) in a real Electron lives in `test/built/` and
// runs in CI's `web-build` entry, after the build and its stamp, under PCLAW_REQUIRE_ELECTRON=1, so
// a missing or stale build fails it. `test:desktop`, which runs before the build, never collects
// that folder, so no built-page leg can sit where it would skip on every run.

const DESKTOP = path.join(__dirname, "..");
const REPO = path.join(DESKTOP, "..");
const BUILT = path.join(__dirname, "built");

/** Whether a test file drives the built page: it starts a real Electron (`electronCommand`) and
 *  needs the build (it reads `web/dist`, or asks `pageUnbuilt`). A unit test of the helpers, which
 *  starts nothing, is neither. */
function drivesTheBuiltPage(src) {
  const code = src.replace(/^\s*\/\/.*$/gm, "");
  return /electronCommand\(/.test(code) && /web[\\/"', ]+dist|pageUnbuilt\(/.test(code);
}

/** The `argv`s of one catalogue job's `command`, in order (`tools/ci/catalogue.toml`). */
function catalogueCommand(id) {
  const toml = fs.readFileSync(path.join(REPO, "tools", "ci", "catalogue.toml"), "utf8");
  const at = toml.indexOf(`id = "${id}"`);
  assert.ok(at >= 0, `catalogue job ${id}`);
  const block = toml.slice(at, toml.indexOf("\n[[job]]", at) === -1 ? undefined : toml.indexOf("\n[[job]]", at));
  const steps = [...block.matchAll(/\{ argv = \[([^\]]*)\](?:, env = \{([^}]*)\})?/g)].map((m) => ({
    argv: [...m[1].matchAll(/"([^"]*)"/g)].map((x) => x[1]),
    env: m[2] || "",
  }));
  const toolchain = /toolchain = \[([^\]]*)\]/.exec(block);
  return { steps, toolchain: toolchain ? [...toolchain[1].matchAll(/"([^"]*)"/g)].map((x) => x[1]) : [] };
}

describe("the built-page legs", () => {
  it("live in test/built/, and no test test:desktop collects drives the built page", () => {
    const top = fs.readdirSync(__dirname).filter((f) => f.endsWith(".test.js"));
    assert.ok(top.length > 20, "vacuity floor: the desktop tier's own tests");
    const strays = top.filter((f) => f !== path.basename(__filename) && drivesTheBuiltPage(fs.readFileSync(path.join(__dirname, f), "utf8")));
    assert.deepEqual(strays, [], "move these to desktop/test/built/: before the build they would only ever skip");
    const built = fs.existsSync(BUILT) ? fs.readdirSync(BUILT).filter((f) => f.endsWith(".test.js")) : [];
    assert.ok(built.includes("buddyAnswer.test.js"), "the buddy's answers in a real Electron are a built-page leg");
    for (const f of built) {
      assert.match(fs.readFileSync(path.join(BUILT, f), "utf8"), /require\("\.\.\/helpers\/builtPage"\)/, `${f} takes the built-page tier`);
    }
  });

  it("are a script of their own, which test:desktop's glob does not reach", () => {
    const desktop = JSON.parse(fs.readFileSync(path.join(DESKTOP, "package.json"), "utf8")).scripts;
    assert.equal(desktop.test, "node --test test/*.test.js", "one folder deep: test/built/ is not collected");
    assert.equal(desktop["test:built"], "node --test test/built/*.test.js");
    const root = JSON.parse(fs.readFileSync(path.join(REPO, "package.json"), "utf8")).scripts;
    assert.equal(root["test:desktop:built"], "npm run test:built --workspace=desktop");
  });

  it("run in web-build after the build and its stamp, where the host must run them, with Electron's binary", () => {
    const { steps, toolchain } = catalogueCommand("web-build");
    const at = (pred, what) => {
      const i = steps.findIndex(pred);
      assert.ok(i >= 0, `web-build runs ${what}`);
      return i;
    };
    const build = at((s) => s.argv.join(" ") === "npm run build", "the build");
    const stamp = at((s) => s.argv.join(" ") === "python3 scripts/spa_dist_freshness.py stamp", "the stamp");
    const legs = at((s) => s.argv.join(" ") === "npm run test:desktop:built", "the built-page legs");
    assert.ok(build < stamp && stamp < legs, "the legs come after the build and its stamp");
    assert.match(steps[legs].env, /PCLAW_REQUIRE_ELECTRON = "1"/);
    assert.ok(toolchain.includes("electron"), "web-build installs Electron's binary");
    const unit = catalogueCommand("web-unit").steps.map((s) => s.argv.join(" "));
    assert.equal(unit.includes("npm run test:desktop:built"), false, "never before the build");
  });
});
