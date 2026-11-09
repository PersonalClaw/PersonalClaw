const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const MAIN = fs.readFileSync(path.join(ROOT, "main.js"), "utf8");
const CHROME = fs.readFileSync(path.join(ROOT, "windowChrome.js"), "utf8");
const { TAB_BAR_HEIGHT, TRAFFIC_LIGHTS, TRAFFIC_LIGHT_HEIGHT, makeWindowChrome } = require("../windowChrome");

/** The appearance's canvas as the shell keeps it, one colour a call: it can change between windows. */
function canvases(...colours) {
  let i = 0;
  return () => colours[Math.min(i++, colours.length - 1)];
}

describe("the window's title bar, traffic lights and colour", () => {
  it("on macOS: the inset title bar, the lights in the strip, and the appearance's colour", () => {
    const o = makeWindowChrome({ platform: "darwin", canvas: () => "#f0f4f8" }).options();
    assert.deepEqual(o, {
      titleBarStyle: "hiddenInset",
      trafficLightPosition: TRAFFIC_LIGHTS,
      backgroundColor: "#f0f4f8",
    });
  });

  for (const platform of ["linux", "win32"]) {
    it(`on ${platform}: the hidden title bar as before, in the appearance's colour, no lights`, () => {
      const o = makeWindowChrome({ platform, canvas: () => "#f0f4f8" }).options();
      assert.deepEqual(o, { titleBarStyle: "hidden", backgroundColor: "#f0f4f8" });
    });
  }

  it("each new window or tab opens in the colour in force when it opens", () => {
    const chrome = makeWindowChrome({ platform: "darwin", canvas: canvases("#0f0f0f", "#f0f4f8") });
    assert.equal(chrome.options().backgroundColor, "#0f0f0f");
    assert.equal(chrome.options().backgroundColor, "#f0f4f8");
  });

  it("no window is clear and none takes the system's material, whatever the platform", () => {
    for (const platform of ["darwin", "linux", "win32"]) {
      const o = makeWindowChrome({ platform, canvas: () => "#f0f4f8" }).options();
      assert.equal("vibrancy" in o, false, platform);
      assert.match(o.backgroundColor, /^#[0-9a-f]{6}$/, `${platform}: an opaque colour`);
    }
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
});

describe("the shell paints the web UI's own surfaces", () => {
  it("neither windowChrome.js nor main.js names a material or tells the page of one", () => {
    for (const [name, text] of [["windowChrome.js", CHROME], ["main.js", MAIN]]) {
      assert.doesNotMatch(text, /vibrancy|setVibrancy|windowMaterial|under-window/, name);
    }
  });
});

describe("main.js takes the window chrome from windowChrome.js", () => {
  const makeWindow = /function makeWindow\(\) \{[\s\S]*?\n\}/.exec(MAIN);

  it("makeWindow spreads the chrome's options and sets no title bar or colour of its own", () => {
    assert.ok(makeWindow, "makeWindow must be findable");
    assert.match(makeWindow[0], /\.\.\.windowChrome\.options\(\)/);
    assert.doesNotMatch(makeWindow[0], /titleBarStyle|backgroundColor|vibrancy/);
  });

  it("the chrome's colour is the canvas of the appearance the shell keeps", () => {
    assert.match(MAIN, /const windowChrome = makeWindowChrome\(\{ platform: process\.platform, canvas: \(\) => [\w.]+\.canvas\(\) \}\);/);
  });

  it("the strip's height comes from windowChrome.js", () => {
    assert.match(MAIN, /const \{ TAB_BAR_HEIGHT, makeWindowChrome \} = require\("\.\/windowChrome"\);/);
  });
});
