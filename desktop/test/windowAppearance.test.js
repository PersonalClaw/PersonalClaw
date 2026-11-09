const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const read = (name) => fs.readFileSync(path.join(ROOT, name), "utf8");
const MAIN = read("main.js");

/** The module, loaded in each test, so each says for itself what is missing. */
const appearance = () => require("../windowAppearance");

/** A window stand-in that records the colours it was given. */
function win(name = "w") {
  const colours = [];
  return { name, colours, isDestroyed: () => false, setBackgroundColor: (c) => colours.push(c) };
}

/** A shell store stand-in: string items, and every write it was asked for. */
function store(initial = {}) {
  const data = { ...initial };
  const writes = [];
  return {
    data,
    writes,
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = String(v);
      writes.push([k, String(v)]);
    },
  };
}

/** A clock and timers the test moves by hand. */
function clock() {
  let t = 0;
  const timers = [];
  return {
    now: () => t,
    setTimer: (fn, ms) => {
      const timer = { fn, at: t + ms, done: false };
      timers.push(timer);
      return timer;
    },
    clearTimer: (timer) => {
      if (timer) timer.done = true;
    },
    advance(ms) {
      t += ms;
      for (const timer of timers) {
        if (!timer.done && timer.at <= t) {
          timer.done = true;
          timer.fn();
        }
      }
    },
  };
}

const LIGHT = { preference: "light", mode: "light", canvas: "#f0f4f8" };
const DARK = { preference: "dark", mode: "dark", canvas: "#0f0f0f" };
const AUTO_DARK = { preference: "auto", mode: "dark", canvas: "#0f0f0f" };

function shell({ focused = null, kept = store(), time = clock() } = {}) {
  const nativeTheme = { themeSource: "system" };
  const a = appearance().makeWindowAppearance({
    nativeTheme,
    store: kept,
    canvas: "#0f0f0f",
    focusedAppWindow: () => focused,
    now: time.now,
    setTimer: time.setTimer,
    clearTimer: time.clearTimer,
  });
  return { a, nativeTheme, kept, time };
}

describe("the appearance the shell keeps", () => {
  it("each preference asks for its own themeSource, and auto follows the system", () => {
    const { themeSourceFor } = appearance();
    assert.equal(themeSourceFor("dark"), "dark");
    assert.equal(themeSourceFor("light"), "light");
    assert.equal(themeSourceFor("auto"), "system");
  });

  it("a report is taken only with a known preference and mode and a #rrggbb canvas", () => {
    const { parseAppearance } = appearance();
    assert.deepEqual(parseAppearance(LIGHT), LIGHT);
    assert.deepEqual(parseAppearance({ ...LIGHT, extra: 1 }), LIGHT, "nothing else is kept");
    for (const bad of [
      null,
      "light",
      { ...LIGHT, preference: "system" },
      { ...LIGHT, mode: "auto" },
      { ...LIGHT, canvas: "#F0F4F8" },
      { ...LIGHT, canvas: "#f0f4f8; x" },
      { ...LIGHT, canvas: "red" },
      { ...LIGHT, canvas: 15791352 },
    ]) {
      assert.equal(parseAppearance(bad), null, JSON.stringify(bad));
    }
  });
});

describe("apply: a switch reaches the window at once", () => {
  it("sets the window's colour and the app's themeSource when the window is focused", () => {
    const w = win();
    const { a, nativeTheme } = shell({ focused: w });
    assert.equal(a.apply(w, LIGHT), true);
    assert.deepEqual(w.colours, ["#f0f4f8"]);
    assert.equal(nativeTheme.themeSource, "light");
    assert.equal(a.canvas(), "#f0f4f8", "a new window opens in it");
    a.apply(w, AUTO_DARK);
    assert.equal(nativeTheme.themeSource, "system");
  });

  it("sets themeSource when no app window is focused, as when the switch comes in the background", () => {
    const w = win();
    const { a, nativeTheme } = shell({ focused: null });
    assert.equal(a.apply(w, LIGHT), true);
    assert.equal(nativeTheme.themeSource, "light");
  });

  it("an unfocused window takes its own colour but leaves themeSource to the focused one", () => {
    const front = win("front");
    const back = win("back");
    const { a, nativeTheme, kept } = shell({ focused: front });
    a.apply(front, DARK);
    assert.equal(a.apply(back, LIGHT), false);
    assert.deepEqual(back.colours, ["#f0f4f8"]);
    assert.equal(nativeTheme.themeSource, "dark");
    assert.equal(a.canvas(), "#0f0f0f");
    assert.deepEqual(kept.writes.map(([, v]) => JSON.parse(v).preference), ["dark"]);
  });

  it("keeps what it set for the next launch: unchanged reports dropped, at most every 2 s, flushed on quit", () => {
    const w = win();
    const time = clock();
    const kept = store();
    const { a } = shell({ focused: w, kept, time });
    a.apply(w, LIGHT);
    assert.equal(kept.writes.length, 1, "the first is written at once");
    a.apply(w, LIGHT);
    assert.equal(kept.writes.length, 1, "the same report again writes nothing");
    a.apply(w, DARK);
    a.apply(w, AUTO_DARK);
    assert.equal(kept.writes.length, 1, "a run of switches waits");
    time.advance(2000);
    assert.equal(kept.writes.length, 2, "and is written once, as its last");
    assert.deepEqual(JSON.parse(kept.data.appearance), AUTO_DARK);
    a.apply(w, LIGHT);
    a.flush();
    assert.equal(kept.writes.length, 3, "quitting writes what waits");
    assert.deepEqual(JSON.parse(kept.data.appearance), LIGHT);
    time.advance(5000);
    assert.equal(kept.writes.length, 3, "and nothing after it");
  });

  it("an older page, which names no preference, is pinned to its mode and never stored", () => {
    const { fromPageRead } = appearance();
    const older = fromPageRead({ preference: "", mode: "light", canvas: "rgb(240, 244, 248)" });
    assert.deepEqual(older, { preference: "light", mode: "light", canvas: "#f0f4f8", pinned: true });
    const w = win();
    const kept = store();
    const { a, nativeTheme } = shell({ focused: w, kept });
    a.apply(w, older);
    assert.equal(nativeTheme.themeSource, "light");
    assert.deepEqual(w.colours, ["#f0f4f8"]);
    assert.deepEqual(kept.writes, []);
  });

  it("a page's read names its own preference; one with no mode, like the loading page, says nothing", () => {
    const { fromPageRead } = appearance();
    assert.deepEqual(fromPageRead({ preference: "auto", mode: "dark", canvas: "rgb(15, 15, 15)" }), AUTO_DARK);
    assert.deepEqual(
      fromPageRead({ preference: "light", mode: "light", canvas: "" }),
      LIGHT,
      "an unreadable ground is the mode's canvas",
    );
    assert.equal(fromPageRead({ preference: "", mode: "", canvas: "rgb(0, 0, 0)" }), null);
    assert.equal(fromPageRead(null), null);
  });
});

describe("the bridge's report", () => {
  it("is applied from an app window's own page, and refused from any other sender or when malformed", () => {
    const w = win();
    const { a, nativeTheme } = shell({ focused: w });
    const page = {};
    const appWindowOf = (sender) => (sender === page ? w : null);
    assert.equal(a.changed({}, LIGHT, appWindowOf), false, "not an app window's page");
    assert.equal(a.changed(page, { ...LIGHT, canvas: "url(x)" }, appWindowOf), false);
    assert.deepEqual(w.colours, []);
    assert.equal(nativeTheme.themeSource, "system");
    assert.equal(a.changed(page, LIGHT, appWindowOf), true);
    assert.equal(nativeTheme.themeSource, "light");
  });
});

describe("at launch", () => {
  const cases = [
    ["nothing stored follows the system, in its mode's canvas", null, true, { themeSource: "system", canvas: "#0f0f0f" }],
    ["nothing stored, the system light", null, false, { themeSource: "system", canvas: "#f0f4f8" }],
    ["stored light opens light", JSON.stringify(LIGHT), true, { themeSource: "light", canvas: "#f0f4f8" }],
    ["stored auto, the system dark, opens dark", JSON.stringify({ ...AUTO_DARK, mode: "light", canvas: "#f0f4f8" }), true, { themeSource: "system", canvas: "#0f0f0f" }],
    ["junk is as nothing stored", "{not json", false, { themeSource: "system", canvas: "#f0f4f8" }],
    ["a bad canvas is as nothing stored", JSON.stringify({ ...LIGHT, canvas: "red" }), true, { themeSource: "system", canvas: "#0f0f0f" }],
  ];
  for (const [what, stored, systemDark, expected] of cases) {
    it(what, () => {
      assert.deepEqual(appearance().launch({ stored, systemDark }), expected);
    });
  }
});

describe("main.js wires the appearance in", () => {
  it("main.js sets themeSource once, at launch; a page's read goes to apply as it was read", () => {
    assert.equal(MAIN.match(/nativeTheme\.themeSource\s*=(?!=)/g)?.length, 1);
    assert.match(MAIN, /const a = fromPageRead\(read\);\s*if \(win\.isDestroyed\(\)\) return;\s*if \(a\) windowAppearance\.apply\(win, a\);/);
  });

  it("a page with no mode of its own, like the loading page, is painted in the mode in force", () => {
    assert.match(MAIN, /else windowAppearance\.paint\(win, nativeTheme\.shouldUseDarkColors \? "dark" : "light"\);/);
  });

  it("a system switch repaints only the windows whose page has no mode, and never applies", () => {
    const at = MAIN.indexOf('nativeTheme.on("updated", () => paintModelessWindows())');
    assert.ok(at > 0, "listens for the system's switch");
    const body = /function paintModelessWindows\(\) \{[\s\S]*?\n\}/.exec(MAIN);
    assert.ok(body, "paintModelessWindows is findable");
    assert.match(body[0], /fromPageRead\(read\) === null/);
    assert.match(body[0], /windowAppearance\.paint\(/);
    assert.doesNotMatch(body[0], /apply\(|_pcReadAppearance|readAppearance\(/);
    assert.equal(MAIN.match(/nativeTheme\.on\("updated"/g)?.length, 2, "the tray's listener and this one");
  });

  it("a bridged view is handed the kept theme as an argument, and a paired gateway's view none", () => {
    assert.equal(MAIN.match(/additionalArguments/g)?.length, 1);
    assert.match(MAIN, /\.\.\.\(attachBridge\s*\? \{ preload: path\.join\(__dirname, "preload\.js"\), additionalArguments: \[`--pclaw-appearance=\$\{windowAppearance\.preference\(\)\}`\] \}\s*: \{\}\)/);
  });

  it("a view with no bridge is read after each click or key, at most one read waiting", () => {
    assert.match(MAIN, /if \(!attachBridge\) view\.webContents\.on\("input-event", \(_e, input\) => \{/);
    assert.match(MAIN, /if \(input\.type !== "mouseUp" && input\.type !== "keyUp"\) return;/);
    assert.match(MAIN, /if \(readSoon\) return;\s*readSoon = setTimeout\(\(\) => \{\s*readSoon = null;\s*win\._pcReadAppearance\(\);\s*\}, READ_AFTER_INPUT_MS\);/);
  });

  it("the launch reads the system mode, opens in the launched canvas and keeps to the shell store", () => {
    assert.match(MAIN, /systemDark: nativeTheme\.shouldUseDarkColors,/);
    assert.match(MAIN, /store: shellStore,\s*canvas: launched\.canvas,\s*focusedAppWindow: \(\) => focusedAppWindow\(allWindows\),/);
    assert.match(MAIN, /stored: shellStore\.getItem\(APPEARANCE_KEY\),/);
    const quit = MAIN.slice(MAIN.indexOf('app.on("before-quit"'));
    assert.match(quit.slice(0, 200), /windowAppearance\.flush\(\);/);
  });

  it("the view's theme colour, each load and each focus read the page", () => {
    assert.match(MAIN, /view\.webContents\.on\("did-change-theme-color", \(\) => win\._pcReadAppearance\(\)\)/);
    assert.match(MAIN, /view\.webContents\.on\("did-finish-load", \(\) => win\._pcReadAppearance\(\)\)/);
    assert.match(MAIN, /win\.on\("focus", \(\) => win\._pcReadAppearance\(\)\)/);
  });

  it("themeSource is set from the stored appearance before the first window opens", () => {
    const ready = MAIN.slice(MAIN.indexOf("app.whenReady().then("));
    const set = ready.indexOf("nativeTheme.themeSource = launched.themeSource");
    assert.ok(set > 0, "set at launch");
    assert.ok(set < ready.indexOf("const win = createWindow();"), "before the first window");
    assert.ok(ready.indexOf("shellStore = openShellStore(") < set, "after the store opens");
  });

  it("the bridge's report is taken on its channel only through the app window check", () => {
    assert.match(
      MAIN,
      /ipcMain\.on\(IPC_CHANNELS\.appearanceChanged, \(e, payload\) => \{\s*windowAppearance\.changed\(e\.sender, payload, \(s\) => appWindowOfSender\(allWindows, s\)\);/,
    );
    assert.match(MAIN, /focusedAppWindow: \(\) => focusedAppWindow\(allWindows\)/);
    assert.match(MAIN, /const allWindows = \(\) => BaseWindow\.getAllWindows\(\);/);
  });

  it("the app packs the module", () => {
    const files = JSON.parse(read("package.json")).build.files;
    assert.ok(files.includes("windowAppearance.js"), "build.files");
  });
});

describe("the app windows the shell's checks find", () => {
  /** A window stand-in as main.js builds one: the page view, whether it has the bridge, focus. */
  function appWin({ bridge = true, view = true, destroyed = false, focused = false } = {}) {
    const webContents = {};
    return {
      webContents,
      _pcBridgeAttached: bridge,
      _pcView: view ? { webContents } : undefined,
      isDestroyed: () => destroyed,
      isFocused: () => focused,
    };
  }

  it("the sender's window is an app window with the bridge whose page sent it", () => {
    const { appWindowOfSender } = appearance();
    const app = appWin();
    const other = appWin();
    const all = () => [other, app];
    assert.equal(appWindowOfSender(all, app.webContents), app);
    assert.equal(appWindowOfSender(all, {}), null, "a sender that is no window's page");
    const paired = appWin({ bridge: false });
    assert.equal(appWindowOfSender(() => [paired], paired.webContents), null, "a paired gateway's view");
    const buddy = appWin({ view: false });
    assert.equal(appWindowOfSender(() => [buddy], buddy.webContents), null, "the buddy, no page view");
    const gone = appWin({ destroyed: true });
    assert.equal(appWindowOfSender(() => [gone], gone.webContents), null, "a destroyed window");
  });

  it("the focused app window is the one with focus and a page view, never the buddy or a dialog", () => {
    const { focusedAppWindow } = appearance();
    const back = appWin();
    const front = appWin({ focused: true });
    assert.equal(focusedAppWindow(() => [back, front]), front);
    assert.equal(focusedAppWindow(() => [back]), null, "none focused");
    assert.equal(focusedAppWindow(() => [appWin({ view: false, focused: true })]), null, "the buddy");
    assert.equal(focusedAppWindow(() => [appWin({ destroyed: true, focused: true })]), null, "destroyed");
  });
});

describe("the kept preference and a page with no mode", () => {
  it("preference() is the kept one, at launch and after a switch is kept, and none with nothing kept", () => {
    const { makeWindowAppearance } = appearance();
    const make = (kept) =>
      makeWindowAppearance({ nativeTheme: { themeSource: "system" }, store: kept, canvas: "#0f0f0f", focusedAppWindow: () => null });
    assert.equal(make(store()).preference(), "");
    assert.equal(make(store({ appearance: "junk" })).preference(), "");
    assert.equal(make(store({ appearance: JSON.stringify(AUTO_DARK) })).preference(), "auto");
    const time = clock();
    const { a } = shell({ time });
    a.apply(win(), LIGHT);
    assert.equal(a.preference(), "light");
    a.apply(win(), DARK);
    assert.equal(a.preference(), "dark", "a switch still waiting to be written counts");
  });

  it("paint() colours a window in a mode's canvas, and the app's colour only when it is focused or none is", () => {
    const front = win("front");
    const back = win("back");
    const { a, nativeTheme, kept } = shell({ focused: front });
    a.paint(back, "light");
    assert.deepEqual(back.colours, ["#f0f4f8"]);
    assert.equal(a.canvas(), "#0f0f0f", "an unfocused window leaves the app's colour");
    a.paint(front, "light");
    assert.equal(a.canvas(), "#f0f4f8");
    assert.equal(nativeTheme.themeSource, "system", "never themeSource");
    assert.deepEqual(kept.writes, [], "never the store");
    const alone = shell({ focused: null }).a;
    alone.paint(win(), "light");
    assert.equal(alone.canvas(), "#f0f4f8", "with no app window focused, the app's colour follows");
  });

  it("READ_SCRIPT reads a new page, an older page, the loading page and a page with no body", () => {
    const { READ_SCRIPT, fromPageRead } = appearance();
    const vm = require("node:vm");
    const source = read("windowAppearance.js");
    const start = source.indexOf("const READ_SCRIPT = `");
    const text = source.slice(start, source.indexOf("`;", start));
    assert.ok(start > 0 && text.length > 40, "READ_SCRIPT is findable");
    assert.doesNotMatch(text, /\$\{/, "fixed text, with nothing put into it");
    const page = (dataset, body) =>
      vm.runInNewContext(READ_SCRIPT, {
        document: { documentElement: { dataset }, body },
        getComputedStyle: (el) => ({ backgroundColor: el.bg }),
      });
    assert.deepEqual(fromPageRead(page({ mode: "light", modePreference: "auto" }, { bg: "rgb(250, 250, 250)" })), {
      preference: "auto", mode: "light", canvas: "#fafafa",
    });
    assert.deepEqual(fromPageRead(page({ mode: "dark" }, { bg: "rgb(15, 15, 15)" })), {
      preference: "dark", mode: "dark", canvas: "#0f0f0f", pinned: true,
    });
    assert.equal(fromPageRead(page({}, { bg: "rgb(240, 244, 248)" })), null, "the loading page");
    assert.deepEqual(fromPageRead(page({ mode: "light", modePreference: "light" }, null)), LIGHT, "no body");
  });
});

describe("small cases", () => {
  it("a kept theme switched away from and back within 2 s is written as the last", () => {
    const w = win();
    const time = clock();
    const kept = store();
    const { a } = shell({ focused: w, kept, time });
    a.apply(w, LIGHT);
    a.apply(w, DARK);
    a.apply(w, LIGHT);
    time.advance(2000);
    assert.deepEqual(kept.writes.map(([, v]) => JSON.parse(v).preference), ["light"], "back where it was");
    a.apply(w, DARK);
    time.advance(2000);
    assert.deepEqual(kept.writes.map(([, v]) => JSON.parse(v).preference), ["light", "dark"]);
  });

  it("a kept canvas that is no token is used when its mode is in force", () => {
    const kept = JSON.stringify({ preference: "light", mode: "light", canvas: "#fafafa" });
    assert.deepEqual(appearance().launch({ stored: kept, systemDark: true }), { themeSource: "light", canvas: "#fafafa" });
  });

  it("a clear ground is no colour, and the mode's canvas is used", () => {
    const { hexOf, fromPageRead } = appearance();
    assert.equal(hexOf("rgba(0, 0, 0, 0)"), "");
    assert.equal(fromPageRead({ preference: "light", mode: "light", canvas: "rgba(0, 0, 0, 0)" }).canvas, "#f0f4f8");
  });

  it("apply on a destroyed window does nothing", () => {
    const gone = { ...win(), isDestroyed: () => true };
    const { a, nativeTheme, kept } = shell({ focused: null });
    assert.equal(a.apply(gone, LIGHT), false);
    assert.deepEqual(gone.colours, []);
    assert.equal(nativeTheme.themeSource, "system");
    assert.deepEqual(kept.writes, []);
  });
});
