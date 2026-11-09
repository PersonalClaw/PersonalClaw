/**
 * The page's appearance in a real Electron (`helpers/windowAppearanceProbe.js`): windows wired by a
 * copy of `main.js`'s wiring around `windowAppearance.js` (`main.js`'s own listeners are held by the
 * source pins in `windowAppearance.test.js`), every one shown inactive, and stand-in pages that
 * switch their theme. Within 250 ms of each switch the window's colour is the page's canvas and
 * `themeSource` is the theme asked for, through the bridge and, in a view with no preload, through
 * the theme colour; a click in such a view on a switch that keeps the colour; a window on the
 * loading page follows a switch made in another window; and a new origin with nothing stored opens
 * in the kept theme from its first frame. Run with the old wiring (fabc4dbc6's load-and-focus sync),
 * each of those legs fails.
 *
 * Skips where Electron cannot start, unless `PCLAW_REQUIRE_ELECTRON=1` says the host must run it
 * (`helpers/electronHost.js`).
 */
const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { electronCommand, electronTier, electronUnavailable, launchDetail } = require("./helpers/electronHost");

function probe(wiring) {
  const { command, args } = electronCommand([path.join(__dirname, "helpers", "windowAppearanceProbe.js")]);
  const res = spawnSync(command, args, {
    timeout: 120_000,
    encoding: "utf8",
    env: { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: "1", PC_PROBE_WIRING: wiring },
  });
  const lines = (res.stdout || "").split("\n");
  const line = lines.find((l) => l.startsWith("PC_PROBE "));
  if (!line && lines.includes("PC_PROBE_READY")) {
    return { error: `started but printed no result: ${launchDetail(res)}`, legs: {} };
  }
  if (!line) return { launchFailed: true, detail: launchDetail(res) };
  return JSON.parse(line.slice("PC_PROBE ".length));
}

const unavailable = electronUnavailable();
const now = unavailable ? null : probe("new");
const old = unavailable || !now || now.launchFailed ? null : probe("old");
const { skip, fail } = electronTier(unavailable || (now && now.launchFailed ? `electron did not start: ${now.detail}` : null));

describe("the page's appearance in a real Electron", () => {
  if (fail) it("starts a real Electron on this host", () => assert.fail(fail));
  const skipped = skip || (fail ? "this host cannot start Electron: the test above fails with why" : false);

  it("ran without error, both wirings", { skip: skipped }, () => {
    assert.ok(!old.launchFailed, old.detail);
    assert.equal(now.error, undefined, now.error);
    assert.equal(old.error, undefined, old.error);
  });

  it("each switch through the bridge reaches the window within 250 ms, System included", { skip: skipped }, () => {
    assert.deepEqual(now.legs.bridge.map((r) => [r.pref, r.ok]), [["dark", true], ["light", true], ["auto", true], ["dark", true]], JSON.stringify(now.legs.bridge));
  });

  it("each switch in a view with no preload reaches the window by its theme colour", { skip: skipped }, () => {
    assert.ok(now.legs.meta.every((r) => r.ok), JSON.stringify(now.legs.meta));
  });

  it("a click in a view with no preload, on a switch that keeps the colour, reaches themeSource", { skip: skipped }, () => {
    assert.equal(now.legs.click.ok, true, JSON.stringify(now.legs.click));
  });

  it("a window on the loading page follows a switch made in another window", { skip: skipped }, () => {
    assert.equal(now.legs.loading.ok, true, JSON.stringify(now.legs.loading));
  });

  it("a new origin with nothing stored opens in the kept theme from its first frame, and the store keeps it", { skip: skipped }, () => {
    assert.equal(now.legs.kept.ok, true, JSON.stringify(now.legs.kept));
  });

  it("an older page is pinned to its mode on load and on focus, and nothing is kept", { skip: skipped }, () => {
    assert.equal(now.legs.older.ok, true, JSON.stringify(now.legs.older));
  });

  it("with the old wiring, every leg but the older page's fails", { skip: skipped }, () => {
    assert.ok(old.legs.bridge.some((r) => !r.ok), "the bridge leg");
    assert.ok(old.legs.meta.some((r) => !r.ok), "the theme colour leg");
    assert.equal(old.legs.click.ok, false, "the click leg");
    assert.equal(old.legs.loading.ok, false, "the loading page's leg");
    assert.equal(old.legs.kept.ok, false, "the kept theme's leg");
  });
});
