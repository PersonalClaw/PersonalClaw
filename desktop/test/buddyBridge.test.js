const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { BUDDY_CHANNELS, BUDDY_PAYLOADS, buddyWindowOptions, registerBuddyIpc } = require("../buddyWindow");
const { loadPreloadSandboxed, unsandboxableRequires } = require("./helpers/sandboxedPreload");

// ── The buddy's bridge (#3831) ─────────────────────────────────────────────────────────────────
//
// Desktop security surface. The buddy's page reaches the shell through seven named calls on
// `window.pclawBuddy` and nothing else: no `send` or `invoke` of its choosing, nothing from the
// capability bridge. Main hears the five channels behind them only from the buddy window's own web
// contents, and checks every payload whatever the preload sent. A route it opens is one a
// `pclaw://` link may open, checked by the deep-link parser itself.

const PRELOAD = path.join(__dirname, "..", "buddyPreload.js");
const BUDDY_KEYS = ["dragBy", "dragEnd", "dragStart", "openInApp", "pointerOver", "setPresentation", "setRegions"];

/** The preload loaded as the sandboxed buddy window loads it, with every send it makes recorded. */
function loadBuddyBridge() {
  const sent = [];
  const { exposed, channels } = loadPreloadSandboxed(PRELOAD, { send: (channel, ...args) => sent.push({ channel, args }) });
  return { exposed, channels, sent };
}

/** An `ipcMain` that keeps its listeners, so a test can send as a given web contents. */
function fakeIpcMain() {
  const listeners = new Map();
  return {
    on: (channel, fn) => { listeners.set(channel, [...(listeners.get(channel) || []), fn]); },
    removeAllListeners: (channel) => { listeners.delete(channel); },
    emit: (channel, sender, payload) => { for (const fn of listeners.get(channel) || []) fn({ sender }, payload); },
    channels: () => [...listeners.keys()],
  };
}

/** A buddy window that records what main asked of it. */
function fakeBuddy() {
  const calls = [];
  const record = (name) => (...args) => calls.push([name, ...args]);
  return {
    calls,
    pointerOver: record("pointerOver"),
    setRegions: record("setRegions"),
    beginDrag: record("beginDrag"),
    dragBy: record("dragBy"),
    endDrag: record("endDrag"),
    setPresentation: record("setPresentation"),
    hide: record("hide"),
  };
}

function wired() {
  const ipcMain = fakeIpcMain();
  const buddy = fakeBuddy();
  const own = { id: 7 };
  const opened = [];
  const logs = [];
  const unregister = registerBuddyIpc({ ipcMain, buddy, isBuddy: (sender) => sender === own, openRoute: (r) => opened.push(r), log: (m) => logs.push(m) });
  return { ipcMain, buddy, own, opened, logs, unregister };
}

describe("the buddy's preload", () => {
  it("loads in a sandbox and exposes exactly the seven named calls, under its own name", () => {
    assert.deepEqual(unsandboxableRequires(fs.readFileSync(PRELOAD, "utf8")), []);
    const { exposed } = loadBuddyBridge();
    assert.deepEqual(Object.keys(exposed), ["pclawBuddy"], "not the capability bridge, and nothing else");
    assert.deepEqual(Object.keys(exposed.pclawBuddy).sort(), BUDDY_KEYS);
    for (const key of BUDDY_KEYS) assert.equal(typeof exposed.pclawBuddy[key], "function", key);
    assert.equal("send" in exposed.pclawBuddy || "invoke" in exposed.pclawBuddy || "on" in exposed.pclawBuddy, false, "no generic channel");
  });

  it("sends only on the channels main hears, and each call's payload passes main's own check", () => {
    const { exposed, sent } = loadBuddyBridge();
    const b = exposed.pclawBuddy;
    b.pointerOver(true);
    b.setRegions([{ x: 0, y: 0, width: 56, height: 28 }]);
    b.dragStart();
    b.dragBy(3, 4);
    b.dragEnd(true);
    b.setPresentation("expanded");
    b.openInApp("#/chat/abc");
    const names = Object.fromEntries(Object.entries(BUDDY_CHANNELS).map(([name, channel]) => [channel, name]));
    assert.deepEqual([...new Set(sent.map((s) => s.channel))].sort(), Object.values(BUDDY_CHANNELS).sort(), "the preload's copy of the names is main's");
    for (const { channel, args } of sent) {
      assert.equal(args.length, 1, `${channel} sends one payload`);
      assert.notEqual(BUDDY_PAYLOADS[names[channel]](args[0]), null, `${channel} ${JSON.stringify(args[0])}`);
    }
  });

  it("hands main nothing but plain values, whatever the page passes", () => {
    const { exposed, sent } = loadBuddyBridge();
    const b = exposed.pclawBuddy;
    const sneaky = { toString: () => "#/chat/x", valueOf: () => 1 };
    b.openInApp(sneaky);
    b.setPresentation(sneaky);
    b.dragBy(sneaky, "2");
    b.pointerOver("yes");
    b.setRegions(Array.from({ length: 20 }, () => ({ x: 1, y: 1, width: 1, height: 1, extra: () => {} })));
    const [open, presentation, drag, pointer, regions] = sent.map((s) => s.args[0]);
    assert.deepEqual(open, { route: "#/chat/x" });
    assert.deepEqual(presentation, { presentation: "#/chat/x" });
    assert.ok(Number.isNaN(drag.dx) && Number.isNaN(drag.dy), "a value that is not a number is not one");
    assert.deepEqual(pointer, { hit: false }, "only `true` takes clicks");
    assert.equal(regions.rects.length, 8);
    assert.deepEqual(Object.keys(regions.rects[0]), ["x", "y", "width", "height"]);
    assert.equal(BUDDY_PAYLOADS.presentation(presentation), null, "and main still refuses what is not a presentation");
    assert.equal(BUDDY_PAYLOADS.drag(drag), null);
  });
});

describe("main hears the buddy and no other page", () => {
  it("listens on the five channels, and drops every one of them from another web contents", () => {
    const { ipcMain, buddy, opened, logs, unregister } = wired();
    assert.deepEqual(ipcMain.channels().sort(), Object.values(BUDDY_CHANNELS).sort());
    const stranger = { id: 1 };
    ipcMain.emit(BUDDY_CHANNELS.pointer, stranger, { hit: true });
    ipcMain.emit(BUDDY_CHANNELS.regions, stranger, { rects: [] });
    ipcMain.emit(BUDDY_CHANNELS.drag, stranger, { phase: "start" });
    ipcMain.emit(BUDDY_CHANNELS.presentation, stranger, { presentation: "hidden" });
    ipcMain.emit(BUDDY_CHANNELS.open, stranger, { route: "#/chat/abc" });
    assert.deepEqual(buddy.calls, []);
    assert.deepEqual(opened, []);
    assert.equal(logs.length, 5);
    assert.ok(logs.every((l) => /not the buddy's/.test(l)));
    unregister();
    assert.deepEqual(ipcMain.channels(), []);
  });

  it("drops a payload outside its shape", () => {
    const { ipcMain, buddy, own, opened, logs } = wired();
    const bad = [
      [BUDDY_CHANNELS.pointer, { hit: "true" }],
      [BUDDY_CHANNELS.pointer, null],
      [BUDDY_CHANNELS.regions, { rects: Array.from({ length: 9 }, () => ({ x: 0, y: 0, width: 1, height: 1 })) }],
      [BUDDY_CHANNELS.regions, { rects: [{ x: 0, y: 0, width: -1, height: 1 }] }],
      [BUDDY_CHANNELS.regions, { rects: [{ x: 1e9, y: 0, width: 1, height: 1 }] }],
      [BUDDY_CHANNELS.regions, { rects: "all of it" }],
      [BUDDY_CHANNELS.drag, { phase: "move", dx: NaN, dy: 0 }],
      [BUDDY_CHANNELS.drag, { phase: "move", dx: Infinity, dy: 0 }],
      [BUDDY_CHANNELS.drag, { phase: "teleport" }],
      [BUDDY_CHANNELS.presentation, { presentation: "fullscreen" }],
      [BUDDY_CHANNELS.presentation, { presentation: "toString" }],
      [BUDDY_CHANNELS.open, { route: "javascript:alert(1)" }],
      [BUDDY_CHANNELS.open, { route: "https://example.com/#/chat" }],
      [BUDDY_CHANNELS.open, { route: "#/chat/x\";alert(1);\"" }],
      [BUDDY_CHANNELS.open, { route: "#/chat/x</script>" }],
      [BUDDY_CHANNELS.open, { route: `#/chat/${"a".repeat(600)}` }],
      [BUDDY_CHANNELS.open, { route: "#/nowhere" }],
      [BUDDY_CHANNELS.open, { route: "#/chat/abc?reset=1" }],
      [BUDDY_CHANNELS.open, { route: "#/chat/../settings" }],
      [BUDDY_CHANNELS.open, { route: "chat/abc" }],
      [BUDDY_CHANNELS.open, ["#/chat/x"]],
    ];
    for (const [channel, payload] of bad) ipcMain.emit(channel, own, payload);
    assert.deepEqual(buddy.calls, []);
    assert.deepEqual(opened, []);
    assert.equal(logs.length, bad.length);
    assert.ok(logs.every((l) => /outside its shape/.test(l)));
  });

  it("acts on the buddy's own sends, each as its window rule says", () => {
    const { ipcMain, buddy, own, opened } = wired();
    ipcMain.emit(BUDDY_CHANNELS.pointer, own, { hit: true });
    ipcMain.emit(BUDDY_CHANNELS.regions, own, { rects: [{ x: 0, y: 0, width: 56, height: 28, extra: 1 }] });
    ipcMain.emit(BUDDY_CHANNELS.drag, own, { phase: "start" });
    ipcMain.emit(BUDDY_CHANNELS.drag, own, { phase: "move", dx: 2, dy: 60 });
    ipcMain.emit(BUDDY_CHANNELS.drag, own, { phase: "end", instant: "yes" });
    ipcMain.emit(BUDDY_CHANNELS.presentation, own, { presentation: "expanded" });
    ipcMain.emit(BUDDY_CHANNELS.presentation, own, { presentation: "hidden" });
    ipcMain.emit(BUDDY_CHANNELS.open, own, { route: "#/chat/abc" });
    ipcMain.emit(BUDDY_CHANNELS.open, own, { route: "#/chat/new?prompt=what%20changed" });
    assert.deepEqual(buddy.calls, [
      ["pointerOver", true],
      ["setRegions", [{ x: 0, y: 0, width: 56, height: 28 }]],
      ["beginDrag"],
      ["dragBy", 2, 60],
      ["endDrag", { instant: false }],
      ["setPresentation", "expanded"],
      ["hide"],
    ]);
    assert.deepEqual(opened, ["#/chat/abc", "#/chat/new?prompt=what+changed"], "what a pclaw:// link may open, in its parser's spelling");
  });
});

describe("the buddy window's web preferences", () => {
  it("isolate and sandbox its page, with no node", () => {
    const { webPreferences } = buddyWindowOptions({ bounds: {}, preload: PRELOAD });
    assert.equal(webPreferences.preload, PRELOAD);
    assert.equal(webPreferences.contextIsolation, true);
    assert.equal(webPreferences.sandbox, true);
    assert.equal(webPreferences.nodeIntegration, false);
    assert.equal("nodeIntegrationInSubFrames" in webPreferences && webPreferences.nodeIntegrationInSubFrames, false);
    assert.equal(webPreferences.webviewTag, undefined, "no webview tag");
  });
});
