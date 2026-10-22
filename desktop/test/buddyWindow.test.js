const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const { CAPTURE_KEY, ENABLED_KEY, buddyOutlivesClose, makeBuddyPresence, makeBuddyWindow } = require("../buddyWindow");
const { ORB_BOX, PLACEMENT_KEY, PRESENTATIONS, defaultSpot } = require("../buddyPlacement");

// ── The buddy's own window (#3831) ─────────────────────────────────────────────────────────────
//
// Transparent, frameless, always on top, out of the task bar and the tab group; shown without taking
// focus; clicks passing through but for the face and the card; kept out of screen capture by
// default; and, hidden, forwarding no pointer to the page.

const NOTCHED = { id: 1, bounds: { x: 0, y: 0, width: 1512, height: 982 }, workArea: { x: 0, y: 38, width: 1512, height: 944 } };
const EXTERNAL = { id: 2, bounds: { x: 1512, y: 0, width: 2560, height: 1440 }, workArea: { x: 1512, y: 25, width: 2560, height: 1415 } };

/** A BrowserWindow that records what is done to it. */
function fakeWindowClass() {
  const made = [];
  class FakeWindow {
    constructor(options) {
      this.options = options;
      this.calls = [];
      this.position = [options.x ?? 0, options.y ?? 0];
      this.bounds = { x: options.x, y: options.y, width: options.width, height: options.height };
      this.handlers = {};
      this.webContents = { setBackgroundThrottling: (on) => this.record("setBackgroundThrottling", on) };
      made.push(this);
    }
    record(name, ...args) { this.calls.push([name, ...args]); }
    loadURL(u) { this.record("loadURL", u) }
    showInactive() { this.record("showInactive") }
    show() { this.record("show") }
    focus() { this.record("focus") }
    hide() { this.record("hide") }
    destroy() { this.record("destroy") }
    setIgnoreMouseEvents(...a) { this.record("setIgnoreMouseEvents", ...a) }
    setShape(r) { this.record("setShape", r) }
    setContentProtection(on) { this.record("setContentProtection", on) }
    setVisibleOnAllWorkspaces(...a) { this.record("setVisibleOnAllWorkspaces", ...a) }
    setAlwaysOnTop(...a) { this.record("setAlwaysOnTop", ...a) }
    setBounds(b, animate) { this.bounds = b; this.position = [b.x, b.y]; this.record("setBounds", b, animate) }
    getPosition() { return [...this.position] }
    setPosition(x, y) { this.position = [x, y]; this.record("setPosition", x, y) }
    on(event, fn) { this.handlers[event] = fn }
    once(event, fn) { this.handlers[event] = fn }
    emit(event) { if (this.handlers[event]) this.handlers[event]() }
  }
  return { FakeWindow, made };
}

function memoryStore(initial = {}) {
  const data = { ...initial };
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v) }, removeItem: (k) => { delete data[k] } };
}

/** A screen whose displays a test can change, firing the events Electron fires. */
function fakeScreen(displays = [NOTCHED, EXTERNAL]) {
  const handlers = {};
  const screen = {
    displays,
    getAllDisplays: () => screen.displays,
    getPrimaryDisplay: () => screen.displays[0],
    on: (event, fn) => { (handlers[event] ||= []).push(fn); },
    change(next, event) { screen.displays = next; for (const fn of handlers[event] || []) fn(); },
  };
  return screen;
}

function make(platform = "darwin", store = memoryStore(), screen = fakeScreen()) {
  const { FakeWindow, made } = fakeWindowClass();
  const placed = [];
  const layouts = [];
  const buddy = makeBuddyWindow({ BrowserWindowCtor: FakeWindow, screen, store, preload: "/app/buddyPreload.js", platform, onPlacement: (p) => placed.push(p), onLayout: (l) => layouts.push(l) });
  buddy.create("http://127.0.0.1:10000/#/buddy");
  return { buddy, win: made[0], made, store, placed, layouts, screen };
}
/** The orb's box on the screen: the window's position plus the layout's orb. */
const orbOnScreen = (buddy, win) => ({ x: win.position[0] + buddy.layout.orb.x, y: win.position[1] + buddy.layout.orb.y, width: buddy.layout.orb.width, height: buddy.layout.orb.height });
const moves = (win) => win.calls.filter((c) => c[0] === "setBounds" || c[0] === "setPosition");
const names = (win) => win.calls.map((c) => c[0])

describe("the window", () => {
  it("is transparent, frameless, always on top, out of the task bar and the tab group, sandboxed", () => {
    const { win } = make();
    const o = win.options;
    assert.equal(o.show, false);
    assert.equal(o.transparent, true);
    assert.equal(o.frame, false);
    assert.equal(o.alwaysOnTop, true);
    assert.equal(o.skipTaskbar, true);
    assert.equal(o.hasShadow, false);
    assert.equal(o.resizable, false);
    assert.equal("tabbingIdentifier" in o, false, "never joins the native tab group");
    assert.deepEqual(
      { preload: o.webPreferences.preload, contextIsolation: o.webPreferences.contextIsolation, sandbox: o.webPreferences.sandbox, nodeIntegration: o.webPreferences.nodeIntegration },
      { preload: "/app/buddyPreload.js", contextIsolation: true, sandbox: true, nodeIntegration: false },
    );
    assert.deepEqual(win.calls.find((c) => c[0] === "setVisibleOnAllWorkspaces"), ["setVisibleOnAllWorkspaces", true, { visibleOnFullScreen: true }]);
  });

  it("shows without taking focus", () => {
    const { buddy, win } = make();
    buddy.show();
    assert.ok(names(win).includes("showInactive"));
    assert.equal(names(win).includes("show"), false);
    assert.equal(names(win).includes("focus"), false);
  });

  it("starts at the default, below the notch and centred on it, and loads the buddy route", () => {
    const { buddy, win } = make();
    assert.deepEqual(orbOnScreen(buddy, win), { ...defaultSpot(NOTCHED), width: ORB_BOX, height: ORB_BOX });
    assert.ok(orbOnScreen(buddy, win).y >= NOTCHED.workArea.y, "below the notch");
    assert.equal(orbOnScreen(buddy, win).x + ORB_BOX / 2, 756, "centred on it");
    assert.deepEqual({ width: win.options.width, height: win.options.height }, { width: buddy.layout.window.width, height: buddy.layout.window.height });
    assert.deepEqual(win.calls.find((c) => c[0] === "loadURL"), ["loadURL", "http://127.0.0.1:10000/#/buddy"]);
  });
});

describe("screen capture", () => {
  it("is kept out of a share and a screenshot by default, and in when the owner turns that off", () => {
    const { buddy, win, store } = make();
    assert.deepEqual(win.calls.find((c) => c[0] === "setContentProtection"), ["setContentProtection", true]);
    buddy.setHideFromCapture(false);
    assert.equal(store.data[CAPTURE_KEY], "0");
    assert.deepEqual(win.calls.at(-1), ["setContentProtection", false]);
    const again = make("darwin", store);
    assert.deepEqual(again.win.calls.find((c) => c[0] === "setContentProtection"), ["setContentProtection", false], "the machine's choice holds");
  });
});

describe("what the page reads of its window", () => {
  // An unfocused window on macOS can be read as occluded while it is on screen, and the page would
  // read itself hidden and unmount the buddy (#3832): the real-Electron answer tier caught the card
  // gone for seconds after an answer. Shown, the page reads visible; hidden, it reads hidden again.
  it("shown, the page is not throttled as a background page; hidden, it is, before the window hides", () => {
    const { buddy, win } = make("darwin");
    buddy.show();
    const show = win.calls.map((c) => c[0]);
    assert.ok(show.indexOf("setBackgroundThrottling") > show.indexOf("showInactive"), "after it is on screen");
    assert.deepEqual(win.calls.filter((c) => c[0] === "setBackgroundThrottling").at(-1), ["setBackgroundThrottling", false]);
    win.calls.length = 0;
    buddy.hide();
    const hide = win.calls.map((c) => c[0]);
    assert.deepEqual(win.calls.filter((c) => c[0] === "setBackgroundThrottling"), [["setBackgroundThrottling", true]]);
    assert.ok(hide.indexOf("setBackgroundThrottling") > hide.indexOf("hide"), "throttled again after it hides: before, macOS never told the page it was hidden");
  });
});

describe("click-through", () => {
  it("on macOS passes clicks and forwards moves while shown, takes clicks over the shape, and forwards nothing while hidden", () => {
    const { buddy, win } = make("darwin");
    buddy.show();
    assert.deepEqual(win.calls.at(-1), ["setIgnoreMouseEvents", true, { forward: true }]);
    buddy.pointerOver(true);
    assert.deepEqual(win.calls.at(-1), ["setIgnoreMouseEvents", false]);
    buddy.pointerOver(false);
    assert.deepEqual(win.calls.at(-1), ["setIgnoreMouseEvents", true, { forward: true }]);
    buddy.hide();
    const ignore = win.calls.filter((c) => c[0] === "setIgnoreMouseEvents").at(-1)
    assert.deepEqual(ignore, ["setIgnoreMouseEvents", true, undefined], "hidden: no forwarded pointer, so the page samples nothing");
    buddy.pointerOver(true);
    assert.deepEqual(win.calls.filter((c) => c[0] === "setIgnoreMouseEvents").at(-1), ["setIgnoreMouseEvents", true, undefined], "a hidden window takes no clicks");
  });

  it("on Linux shapes the window to the face and the card", () => {
    const { buddy, win } = make("linux");
    buddy.setRegions([{ x: 0.4, y: 0, width: 56, height: 28 }, { x: 0, y: 28, width: 420, height: 300 }, { x: "a" }]);
    assert.deepEqual(win.calls.at(-1), ["setShape", [{ x: 0, y: 0, width: 56, height: 28 }, { x: 0, y: 28, width: 420, height: 300 }]]);
  });

  it("on Linux takes no click at all when nothing is shown, never the whole window", () => {
    const { buddy, win } = make("linux");
    for (const none of [[], [{ x: "a" }], "nonsense"]) {
      buddy.setRegions(none);
      assert.deepEqual(win.calls.at(-1), ["setShape", [{ x: 0, y: 0, width: 0, height: 0 }]], JSON.stringify(none));
    }
  });
});

describe("one orb of one size, where it was put (#5216)", () => {
  it("no state moves or resizes it: every presentation, hide and show, away, regions and the pointer leave its box", () => {
    for (const platform of ["darwin", "linux"]) {
      const { buddy, win } = make(platform);
      buddy.show();
      const box = orbOnScreen(buddy, win);
      const bounds = { ...win.bounds };
      win.calls.length = 0;
      for (const presentation of PRESENTATIONS.filter((p) => p !== "hidden")) {
        buddy.setPresentation(presentation);
        buddy.setRegions([{ ...buddy.layout.orb }, { ...buddy.layout.card }]);
        buddy.pointerOver(true);
        buddy.pointerOver(false);
        buddy.hide();
        buddy.show();
        buddy.setRegions([{ ...buddy.layout.orb }]);
      }
      assert.deepEqual(moves(win), [], `${platform}: nothing moved or resized the window`);
      assert.deepEqual(win.bounds, bounds);
      assert.deepEqual(orbOnScreen(buddy, win), box);
    }
  });

  it("only a drag moves it, by any distance: there is no dock to pull it out of", () => {
    const { buddy, win } = make();
    buddy.show();
    const before = orbOnScreen(buddy, win);
    buddy.beginDrag();
    buddy.dragBy(0, 10);
    assert.deepEqual(win.calls.at(-1), ["setPosition", win.bounds.x, win.bounds.y + 10], "ten points is a move");
    buddy.dragBy(300, 200);
    buddy.endDrag({ instant: true });
    assert.deepEqual(orbOnScreen(buddy, win), { x: before.x + 300, y: before.y + 210, width: ORB_BOX, height: ORB_BOX }, "the orb is where it was dropped");
  });

  it("a drop keeps the orb where it fell, lays the card's side out again, saves the spot, and tells the page", () => {
    const { buddy, win, store, placed, layouts } = make();
    buddy.show();
    const sent = layouts.length;
    const start = orbOnScreen(buddy, win);
    // Near the external display's right edge: the card has no room on the right, so it opens left.
    const edge = EXTERNAL.workArea.x + EXTERNAL.workArea.width - ORB_BOX - 10;
    buddy.beginDrag();
    buddy.dragBy(edge - start.x, 400);
    buddy.endDrag({ instant: true });
    assert.equal(buddy.placement.mode, "floating");
    assert.equal(buddy.placement.displayId, 2, "dropped on the external display");
    assert.deepEqual(orbOnScreen(buddy, win), { x: edge, y: start.y + 400, width: ORB_BOX, height: ORB_BOX }, "the orb is where it fell");
    assert.equal(buddy.layout.side, "left", "the card's side is laid out again for the new spot");
    const saved = JSON.parse(store.data[PLACEMENT_KEY]);
    assert.equal(saved.on, 2);
    assert.deepEqual(placed, ["moving", "floating"]);
    assert.equal(layouts.length, sent + 1, "the page hears the new layout once");
    assert.deepEqual(layouts.at(-1), buddy.layout);
    assert.equal(win.calls.filter((c) => c[0] === "setBounds").at(-1)[2], false, "instant under reduced motion");
  });

  it("a drop near the default puts it back at the default", () => {
    const { buddy, win } = make();
    buddy.show();
    buddy.beginDrag();
    buddy.dragBy(30, 20);
    buddy.endDrag({ instant: true });
    assert.equal(buddy.placement.mode, "docked");
    assert.deepEqual(orbOnScreen(buddy, win), { ...defaultSpot(NOTCHED), width: ORB_BOX, height: ORB_BOX });
  });

  it("never animates a move that lays the card out again: the orb would slide away from where it fell", () => {
    const { buddy, win } = make();
    buddy.show();
    const start = orbOnScreen(buddy, win);
    const edge = EXTERNAL.workArea.x + EXTERNAL.workArea.width - ORB_BOX - 10;
    buddy.beginDrag();
    buddy.dragBy(edge - start.x, 400);
    buddy.endDrag();
    assert.equal(buddy.layout.side, "left");
    assert.equal(win.calls.filter((c) => c[0] === "setBounds").at(-1)[2], false, "the side changed: the window jumps with the page's new layout");
    // Back near the default from below it: the layout is the default's again, so the snap is instant too.
    buddy.beginDrag();
    const home = defaultSpot(NOTCHED);
    const here = orbOnScreen(buddy, win);
    buddy.dragBy(home.x + 20 - here.x, home.y + 20 - here.y);
    buddy.endDrag();
    assert.deepEqual(orbOnScreen(buddy, win), { ...home, width: ORB_BOX, height: ORB_BOX });
    assert.equal(win.calls.filter((c) => c[0] === "setBounds").at(-1)[2], false);
  });

  it("animates a snap to the default on macOS when the card's side stays the same", () => {
    const { buddy, win } = make();
    buddy.show();
    buddy.beginDrag();
    buddy.dragBy(40, 30);
    buddy.endDrag();
    assert.equal(buddy.placement.mode, "docked");
    assert.equal(win.calls.filter((c) => c[0] === "setBounds").at(-1)[2], true, "the orb slides home");
  });

  it("tells the page once the drag moves it, and nothing for a press that never moved", () => {
    const { buddy, placed, win } = make();
    buddy.show();
    win.calls.length = 0;
    buddy.beginDrag();
    buddy.endDrag({ instant: true });
    assert.deepEqual(placed, [], "a press on the orb is a click, not a drag");
    assert.deepEqual(moves(win), [], "and moves nothing");
  });

  it("comes back where it was dropped after a restart, on that display", () => {
    const first = make();
    first.buddy.show();
    first.buddy.beginDrag();
    first.buddy.dragBy(1200, 400);
    first.buddy.endDrag({ instant: true });
    const dropped = orbOnScreen(first.buddy, first.win);
    const again = make("darwin", first.store);
    assert.deepEqual(orbOnScreen(again.buddy, again.win), dropped);
  });

  it("reads the window's earlier floating placement as a spot on its display", () => {
    const store = memoryStore({ [PLACEMENT_KEY]: JSON.stringify({ mode: "floating", displayId: 2, x: 2000, y: 400 }) });
    const { buddy, win } = make("darwin", store);
    assert.deepEqual({ x: orbOnScreen(buddy, win).x, y: orbOnScreen(buddy, win).y }, { x: 2000, y: 400 });
  });

  it("a display that's gone puts it at the default; back, the orb goes back to its spot there", () => {
    const { buddy, win, screen, layouts } = make();
    buddy.show();
    buddy.beginDrag();
    buddy.dragBy(1200, 400);
    buddy.endDrag({ instant: true });
    const spot = orbOnScreen(buddy, win);
    const heard = layouts.length;
    screen.change([NOTCHED], "display-removed");
    assert.deepEqual(orbOnScreen(buddy, win), { ...defaultSpot(NOTCHED), width: ORB_BOX, height: ORB_BOX });
    assert.equal(layouts.length, heard, "the card opens right and down there too: the page's layout is the same, so it hears nothing");
    screen.change([NOTCHED, EXTERNAL], "display-added");
    assert.deepEqual(orbOnScreen(buddy, win), spot);
  });

  it("a display change that leaves the card no room on its side lays it out again, and the page hears it", () => {
    const { buddy, win, screen, layouts } = make();
    buddy.show();
    const start = orbOnScreen(buddy, win);
    buddy.beginDrag();
    buddy.dragBy(EXTERNAL.workArea.x + 1500 - start.x, 300);
    buddy.endDrag({ instant: true });
    assert.equal(buddy.layout.side, "right");
    const spot = orbOnScreen(buddy, win);
    const heard = layouts.length;
    const smaller = { ...EXTERNAL, bounds: { ...EXTERNAL.bounds, width: 1920 }, workArea: { ...EXTERNAL.workArea, width: 1920 } };
    screen.change([NOTCHED, smaller], "display-metrics-changed");
    assert.equal(buddy.layout.side, "left");
    assert.deepEqual(orbOnScreen(buddy, win), spot, "the orb stays on its spot");
    assert.equal(layouts.length, heard + 1);
  });

  it("Settings' reset puts it back at the default", () => {
    const { buddy, win } = make();
    buddy.show();
    buddy.beginDrag();
    buddy.dragBy(1200, 400);
    buddy.endDrag({ instant: true });
    buddy.resetPlacement();
    assert.equal(buddy.placement.mode, "docked");
    assert.deepEqual(orbOnScreen(buddy, win), { ...defaultSpot(NOTCHED), width: ORB_BOX, height: ORB_BOX });
  });
});

describe("whether the window exists", () => {
  function presence(store, url = () => "http://127.0.0.1:10000/#/buddy") {
    const { FakeWindow, made } = fakeWindowClass();
    let follows = 0;
    const screen = { ...fakeScreen(), on: () => { follows += 1; } };
    const buddy = makeBuddyWindow({ BrowserWindowCtor: FakeWindow, screen, store, preload: "/app/buddyPreload.js", platform: "darwin" });
    const prepared = [];
    const p = makeBuddyPresence({ buddy, store, url, prepare: (w) => prepared.push(names(w).length) });
    return { p, buddy, made, prepared, follows: () => follows };
  }

  it("is no window until the owner turns Buddy status on", () => {
    const store = memoryStore();
    const { p, made } = presence(store);
    assert.equal(p.enabled(), false);
    assert.equal(p.start(), false);
    assert.equal(made.length, 0, "off by default: nothing is created, nothing loads");
    for (const raw of ["", "true", "yes", "0"]) {
      store.data[ENABLED_KEY] = raw;
      assert.equal(p.start(), false, JSON.stringify(raw));
    }
    assert.equal(made.length, 0);
  });

  it("opens on the owner's switch, guarded before it loads, and shows once painted without focus", () => {
    const store = memoryStore();
    const { p, buddy, made, prepared } = presence(store);
    assert.equal(p.setEnabled(true), true);
    assert.equal(store.data[ENABLED_KEY], "1");
    const [win] = made;
    assert.equal(prepared.length, 1);
    const loadedAt = names(win).indexOf("loadURL");
    assert.ok(loadedAt >= prepared[0], "the guards are on before the page loads");
    assert.deepEqual(win.calls[loadedAt], ["loadURL", "http://127.0.0.1:10000/#/buddy"]);
    assert.equal(buddy.isVisible(), false, "nothing shows before the page has painted");
    win.emit("ready-to-show");
    assert.equal(buddy.isVisible(), true);
    assert.ok(names(win).includes("showInactive"));
    assert.equal(names(win).includes("focus") || names(win).includes("show"), false);
    assert.equal(win.excludedFromShownWindowsMenu, true, "not in the Window menu");
  });

  it("closes when the owner turns it off, and is one window however often it is turned on", () => {
    const store = memoryStore();
    const { p, buddy, made, follows } = presence(store);
    p.setEnabled(true);
    p.start();
    assert.equal(made.length, 1, "a second start is the same window");
    assert.equal(p.setEnabled(false), false);
    assert.equal(store.data[ENABLED_KEY], "0");
    assert.ok(names(made[0]).includes("destroy"));
    assert.equal(buddy.window, null);
    p.setEnabled(true);
    assert.equal(made.length, 2);
    assert.equal(follows(), 3, "the three display events are followed once, not once per window");
  });

  it("waits for a gateway to load from", () => {
    const store = memoryStore({ [ENABLED_KEY]: "1" });
    let url = null;
    const { p, made } = presence(store, () => url);
    assert.equal(p.start(), false);
    assert.equal(made.length, 0);
    url = "http://127.0.0.1:10000/#/buddy";
    assert.equal(p.start(), true);
    assert.equal(made.length, 1);
  });

  it("never keeps the app running by itself", () => {
    assert.equal(buddyOutlivesClose({ appWindowsLeft: 0, quitsWithNoWindow: true }), false, "no tray: the last window closing quits, the buddy with it");
    assert.equal(buddyOutlivesClose({ appWindowsLeft: 0, quitsWithNoWindow: false }), true, "the menu-bar item keeps the app, and the buddy with it");
    assert.equal(buddyOutlivesClose({ appWindowsLeft: 1, quitsWithNoWindow: true }), true, "a tab is still open");
  });
});
