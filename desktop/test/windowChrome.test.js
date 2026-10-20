const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { EventEmitter } = require("node:events");

const ROOT = path.resolve(__dirname, "..");
const MAIN = fs.readFileSync(path.join(ROOT, "main.js"), "utf8");
const {
  TAB_BAR_HEIGHT,
  TRAFFIC_LIGHTS,
  TRAFFIC_LIGHT_HEIGHT,
  OPAQUE_BACKGROUND,
  CLEAR_BACKGROUND,
  materialScript,
  makeWindowChrome,
} = require("../windowChrome");

/** A nativeTheme stand-in: the one field the chrome reads, and its `updated` event. */
function theme(reduced = false) {
  const t = new EventEmitter();
  t.prefersReducedTransparency = reduced;
  return t;
}

/** A window stand-in that records what the chrome did to it. */
function win() {
  const calls = [];
  return {
    calls,
    isDestroyed: () => false,
    setVibrancy: (v) => calls.push(["vibrancy", v]),
    setBackgroundColor: (c) => calls.push(["background", c]),
    webContents: { executeJavaScript: (s) => (calls.push(["script", s]), Promise.resolve()) },
  };
}

describe("the window's title bar, traffic lights and material", () => {
  it("on macOS: the inset title bar, the lights in the strip, and the sidebar material", () => {
    const o = makeWindowChrome({ platform: "darwin", nativeTheme: theme(false) }).options();
    assert.deepEqual(o, {
      titleBarStyle: "hiddenInset",
      trafficLightPosition: TRAFFIC_LIGHTS,
      vibrancy: "under-window",
      backgroundColor: CLEAR_BACKGROUND,
    });
  });

  it("on macOS with reduced transparency: the same title bar and lights, opaque, no material", () => {
    const o = makeWindowChrome({ platform: "darwin", nativeTheme: theme(true) }).options();
    assert.deepEqual(o, {
      titleBarStyle: "hiddenInset",
      trafficLightPosition: TRAFFIC_LIGHTS,
      backgroundColor: OPAQUE_BACKGROUND,
    });
  });

  for (const platform of ["linux", "win32"]) {
    it(`on ${platform}: the hidden title bar as before, opaque, no lights and no material`, () => {
      const o = makeWindowChrome({ platform, nativeTheme: theme(false) }).options();
      assert.deepEqual(o, { titleBarStyle: "hidden", backgroundColor: OPAQUE_BACKGROUND });
    });
  }

  it("the material shows only on macOS without reduced transparency", () => {
    assert.equal(makeWindowChrome({ platform: "darwin", nativeTheme: theme(false) }).shows(), true);
    assert.equal(makeWindowChrome({ platform: "darwin", nativeTheme: theme(true) }).shows(), false);
    assert.equal(makeWindowChrome({ platform: "linux", nativeTheme: theme(false) }).shows(), false);
    // A theme with no answer (an older shell) is read as "reduce": opaque is the safe side.
    const unknown = new EventEmitter();
    assert.equal(makeWindowChrome({ platform: "darwin", nativeTheme: unknown }).shows(), false);
  });

  it("the lights sit inside the strip above the page, and their place is fixed in one constant", () => {
    assert.ok(Object.isFrozen(TRAFFIC_LIGHTS));
    assert.equal(TAB_BAR_HEIGHT, 28);
    assert.ok(TRAFFIC_LIGHTS.x > 0);
    assert.ok(TRAFFIC_LIGHTS.y >= 0 && TRAFFIC_LIGHTS.y + TRAFFIC_LIGHT_HEIGHT <= TAB_BAR_HEIGHT);
    assert.equal(TRAFFIC_LIGHTS.y, (TAB_BAR_HEIGHT - TRAFFIC_LIGHT_HEIGHT) / 2, "centred in the strip");
    // Nothing else positions the lights or sizes the strip: one change moves them.
    assert.doesNotMatch(MAIN, /trafficLightPosition|setWindowButtonPosition/);
    assert.doesNotMatch(MAIN, /TAB_BAR_HEIGHT\s*=\s*\d/);
  });

  it("the opaque fallback is the dark canvas the window opened on before", () => {
    assert.equal(OPAQUE_BACKGROUND, "#0f0f0f");
    assert.equal(CLEAR_BACKGROUND, "#00000000");
  });

  it("tells the page by a fixed script that carries no input", () => {
    assert.equal(materialScript(true), 'document.documentElement.dataset.windowMaterial = "sidebar";');
    assert.equal(materialScript(false), "delete document.documentElement.dataset.windowMaterial;");
    assert.equal(materialScript("sidebar"), materialScript(false), "only `true` turns it on");
    const on = makeWindowChrome({ platform: "darwin", nativeTheme: theme(false) });
    const off = makeWindowChrome({ platform: "darwin", nativeTheme: theme(true) });
    assert.equal(on.script(), materialScript(true));
    assert.equal(off.script(), materialScript(false));
  });
});

describe("a change to the system's transparency setting reaches every open window", () => {
  it("turning reduced transparency on drops the material, paints opaque and tells the page", () => {
    const t = theme(false);
    const chrome = makeWindowChrome({ platform: "darwin", nativeTheme: t });
    const a = win();
    const b = win();
    chrome.watch(() => [a, b]);
    t.prefersReducedTransparency = true;
    t.emit("updated");
    for (const w of [a, b]) {
      assert.deepEqual(w.calls, [
        ["vibrancy", null],
        ["background", OPAQUE_BACKGROUND],
        ["script", materialScript(false)],
      ]);
    }
  });

  it("turning it off again brings the material back", () => {
    const t = theme(true);
    const chrome = makeWindowChrome({ platform: "darwin", nativeTheme: t });
    const a = win();
    chrome.watch(() => [a]);
    t.prefersReducedTransparency = false;
    t.emit("updated");
    assert.deepEqual(a.calls, [
      ["vibrancy", "under-window"],
      ["background", CLEAR_BACKGROUND],
      ["script", materialScript(true)],
    ]);
  });

  it("an update that does not change the setting (a dark-mode switch) changes nothing", () => {
    const t = theme(false);
    const chrome = makeWindowChrome({ platform: "darwin", nativeTheme: t });
    const a = win();
    chrome.watch(() => [a]);
    t.emit("updated");
    assert.deepEqual(a.calls, []);
  });

  it("skips a destroyed window", () => {
    const t = theme(false);
    const chrome = makeWindowChrome({ platform: "darwin", nativeTheme: t });
    const gone = { ...win(), isDestroyed: () => true };
    chrome.watch(() => [gone]);
    t.prefersReducedTransparency = true;
    t.emit("updated");
    assert.deepEqual(gone.calls, []);
  });

  it("off macOS it never listens, so no window gets a material", () => {
    const t = theme(false);
    makeWindowChrome({ platform: "linux", nativeTheme: t }).watch(() => [win()]);
    assert.equal(t.listenerCount("updated"), 0);
  });
});

describe("main.js takes the window chrome from windowChrome.js", () => {
  const makeWindow = /function makeWindow\(\) \{[\s\S]*?\n\}/.exec(MAIN);

  it("makeWindow spreads the chrome's options and sets no title bar or colour of its own", () => {
    assert.ok(makeWindow, "makeWindow must be findable");
    assert.match(makeWindow[0], /\.\.\.windowChrome\.options\(\)/);
    assert.doesNotMatch(makeWindow[0], /titleBarStyle|backgroundColor|vibrancy/);
    assert.match(MAIN, /const windowChrome = makeWindowChrome\(\{ platform: process\.platform, nativeTheme \}\);/);
  });

  it("the strip's height comes from windowChrome.js", () => {
    assert.match(MAIN, /const \{ TAB_BAR_HEIGHT, makeWindowChrome \} = require\("\.\/windowChrome"\);/);
  });

  it("the page's colour is not painted over the material after a load", () => {
    assert.match(MAIN, /if \(bg && !win\.isDestroyed\(\) && !windowChrome\.shows\(\)\) win\.setBackgroundColor\(bg\);/);
  });

  it("each load tells the page whether the material shows", () => {
    assert.match(MAIN, /view\.webContents\.executeJavaScript\(windowChrome\.script\(\)\)/);
  });

  it("watches the system setting once the app is ready", () => {
    const ready = MAIN.slice(MAIN.indexOf("app.whenReady().then("));
    assert.match(ready, /windowChrome\.watch\(\(\) => BaseWindow\.getAllWindows\(\)\);/);
  });
});
