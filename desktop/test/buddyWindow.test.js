const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const { CAPTURE_KEY, makeBuddyWindow } = require("../buddyWindow");
const { PLACEMENT_KEY, UNDOCK_DISTANCE } = require("../buddyPlacement");

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
  }
  return { FakeWindow, made };
}

function memoryStore(initial = {}) {
  const data = { ...initial };
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v) }, removeItem: (k) => { delete data[k] } };
}

const fakeScreen = (displays = [NOTCHED, EXTERNAL]) => ({ getAllDisplays: () => displays, getPrimaryDisplay: () => displays[0], on: () => {} });

function make(platform = "darwin", store = memoryStore()) {
  const { FakeWindow, made } = fakeWindowClass();
  const buddy = makeBuddyWindow({ BrowserWindowCtor: FakeWindow, screen: fakeScreen(), store, preload: "/app/buddyPreload.js", platform });
  buddy.create("http://127.0.0.1:10000/#/buddy");
  return { buddy, win: made[0], made, store };
}
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

  it("starts docked below the notch, centred, and loads the buddy route", () => {
    const { win } = make();
    assert.equal(win.options.y, 38);
    assert.equal(win.options.x + win.options.width / 2, 756);
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
});

describe("dragging", () => {
  it("holds its dock until the drag passes the undock distance, then floats where it is dropped, saved", () => {
    const { buddy, win, store } = make();
    buddy.show();
    buddy.beginDrag();
    buddy.dragBy(0, UNDOCK_DISTANCE - 10);
    assert.equal(names(win).includes("setPosition"), false, "still docked");
    buddy.dragBy(1200, 400);
    assert.ok(names(win).includes("setPosition"));
    buddy.endDrag({ instant: true });
    assert.equal(buddy.placement.mode, "floating");
    assert.equal(buddy.placement.displayId, 2, "dropped on the external display");
    assert.equal(JSON.parse(store.data[PLACEMENT_KEY]).mode, "floating");
    assert.deepEqual(win.calls.at(-1).slice(0, 1), ["setBounds"]);
    assert.equal(win.calls.at(-1)[2], false, "instant under reduced motion");
  });

  it("comes back floating on that display after a restart", () => {
    const store = memoryStore({ [PLACEMENT_KEY]: JSON.stringify({ mode: "floating", displayId: 2, x: 2000, y: 400 }) });
    const { win } = make("darwin", store);
    assert.deepEqual({ x: win.options.x, y: win.options.y }, { x: 2000, y: 400 });
  });
});
