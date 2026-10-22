const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { IPC_CHANNELS } = require("../capabilities");
const { CAPTURE_KEY, registerBuddySettingsIpc } = require("../buddyWindow");
const { PLACEMENT_KEY } = require("../buddyPlacement");
const { loadPreloadSandboxed } = require("./helpers/sandboxedPreload");

// ── This machine's buddy rows in Settings (#3901) ─────────────────────────────────────────────
//
// `pclawDesktop.buddy`: where the buddy's window sits, putting it back docked, and whether it is
// kept out of screen capture. Desktop security surface: main answers the app's own bridged
// windows only, checks each value, and changes the live window through the buddy's controller.

function memoryStore(initial = {}) {
  const data = { ...initial };
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, removeItem: (k) => { delete data[k]; } };
}

/** An `ipcMain` whose handlers a test can invoke as a given web contents. */
function fakeIpcMain() {
  const handlers = new Map();
  return {
    handle: (channel, fn) => handlers.set(channel, fn),
    removeHandler: (channel) => handlers.delete(channel),
    invoke: async (channel, sender, ...args) => handlers.get(channel)({ sender }, ...args),
    channels: () => [...handlers.keys()],
  };
}

function wired({ store = memoryStore(), controller = null, platform = "darwin" } = {}) {
  const ipcMain = fakeIpcMain();
  const app = { id: "main window" };
  const logs = [];
  const unregister = registerBuddySettingsIpc({
    ipcMain,
    channels: IPC_CHANNELS,
    buddy: () => controller,
    store: () => store,
    isAppWindow: (sender) => sender === app,
    log: (m) => logs.push(m),
    platform,
  });
  return { ipcMain, app, store, logs, unregister };
}

const CHANNELS = [IPC_CHANNELS.buddyGetPlace, IPC_CHANNELS.buddyResetPlace, IPC_CHANNELS.buddyGetHideFromCapture, IPC_CHANNELS.buddySetHideFromCapture];

describe("pclawDesktop.buddy, in the preload", () => {
  it("is four calls on the app's own channels, each one invoke", async () => {
    const calls = [];
    const { exposed } = loadPreloadSandboxed(path.join(__dirname, "..", "preload.js"), { invoke: (channel, ...args) => { calls.push([channel, ...args]); return Promise.resolve(null); } });
    const buddy = exposed.pclawDesktop.buddy;
    assert.deepEqual(Object.keys(buddy).sort(), ["getHideFromCapture", "getPlace", "resetPlace", "setHideFromCapture"]);
    await buddy.getPlace();
    await buddy.resetPlace();
    await buddy.getHideFromCapture();
    await buddy.setHideFromCapture(true);
    await buddy.setHideFromCapture("yes");
    assert.deepEqual(calls, [
      [IPC_CHANNELS.buddyGetPlace],
      [IPC_CHANNELS.buddyResetPlace],
      [IPC_CHANNELS.buddyGetHideFromCapture],
      [IPC_CHANNELS.buddySetHideFromCapture, true],
      [IPC_CHANNELS.buddySetHideFromCapture, false],
    ], "only true turns it on");
  });
});

describe("main answers the app's own windows only", () => {
  it("refuses every call from another web contents, and changes nothing", async () => {
    const { ipcMain, store, logs } = wired({ store: memoryStore({ [PLACEMENT_KEY]: '{"mode":"floating","displayId":2,"x":10,"y":20}' }) });
    assert.deepEqual(ipcMain.channels().sort(), [...CHANNELS].sort());
    const buddyPage = { id: "the buddy's own window" };
    for (const channel of CHANNELS) {
      await assert.rejects(ipcMain.invoke(channel, buddyPage, false), /not one of the app's windows/);
    }
    assert.equal(store.data[PLACEMENT_KEY], '{"mode":"floating","displayId":2,"x":10,"y":20}');
    assert.equal(CAPTURE_KEY in store.data, false);
    assert.equal(logs.length, 4);
  });

  it("reads where the buddy sits from the live window, and puts it back docked through it", async () => {
    let reset = 0;
    let at = { mode: "floating", displayId: 2 };
    const store = memoryStore({ [PLACEMENT_KEY]: '{"v":2,"on":3,"spots":{"3":{"x":10,"y":20}}}' });
    const controller = { get placement() { return at; }, resetPlacement: () => { reset += 1; at = { mode: "docked" }; }, setHideFromCapture: () => {} };
    const { ipcMain, app } = wired({ store, controller });
    assert.deepEqual(await ipcMain.invoke(IPC_CHANNELS.buddyGetPlace, app), { mode: "floating", displayId: 2 }, "the window's, which knows the displays there are");
    assert.deepEqual(await ipcMain.invoke(IPC_CHANNELS.buddyResetPlace, app), { mode: "docked" });
    assert.equal(reset, 1, "the controller moved the window");
  });

  it("reads where the buddy sits from the store when there is no window yet, the earlier placement too (#5216)", async () => {
    for (const [raw, shown] of [
      ['{"v":2,"on":2,"spots":{"2":{"x":10,"y":20}}}', { mode: "floating", displayId: 2 }],
      ['{"v":2,"on":null,"spots":{"2":{"x":10,"y":20}}}', { mode: "docked" }],
      ['{"mode":"floating","displayId":2,"x":10,"y":20}', { mode: "floating", displayId: 2 }],
      ['{"mode":"docked"}', { mode: "docked" }],
      [null, { mode: "docked" }],
    ]) {
      const { ipcMain, app } = wired({ store: memoryStore(raw === null ? {} : { [PLACEMENT_KEY]: raw }) });
      assert.deepEqual(await ipcMain.invoke(IPC_CHANNELS.buddyGetPlace, app), shown, String(raw));
    }
  });

  it("puts it back docked in the store when there is no window yet, keeping each display's spot", async () => {
    const store = memoryStore({ [PLACEMENT_KEY]: '{"v":2,"on":2,"spots":{"2":{"x":10,"y":20}}}' });
    const { ipcMain, app } = wired({ store });
    assert.deepEqual(await ipcMain.invoke(IPC_CHANNELS.buddyResetPlace, app), { mode: "docked" });
    assert.deepEqual(JSON.parse(store.data[PLACEMENT_KEY]), { v: 2, on: null, spots: { 2: { x: 10, y: 20 } } });
  });

  it("reads and sets hide-from-capture, on by default, and ignores a value that is not a boolean", async () => {
    const seen = [];
    const store = memoryStore();
    const controller = { resetPlacement: () => {}, setHideFromCapture: (on) => { seen.push(on); store.setItem(CAPTURE_KEY, on ? "1" : "0"); } };
    const { ipcMain, app, logs } = wired({ store, controller });
    assert.equal(await ipcMain.invoke(IPC_CHANNELS.buddyGetHideFromCapture, app), true, "on by default");
    assert.equal(await ipcMain.invoke(IPC_CHANNELS.buddySetHideFromCapture, app, false), false);
    assert.equal(await ipcMain.invoke(IPC_CHANNELS.buddySetHideFromCapture, app, "true"), false, "unchanged");
    assert.equal(await ipcMain.invoke(IPC_CHANNELS.buddySetHideFromCapture, app, 1), false, "unchanged");
    assert.equal(await ipcMain.invoke(IPC_CHANNELS.buddySetHideFromCapture, app, true), true);
    assert.deepEqual(seen, [false, true], "the live window took each real change");
    assert.equal(logs.filter((l) => /outside its shape/.test(l)).length, 2);
  });

  it("on Linux says hiding from capture is not available, and a change there sets nothing", async () => {
    const seen = [];
    const store = memoryStore();
    const controller = { resetPlacement: () => {}, setHideFromCapture: (on) => seen.push(on) };
    const { ipcMain, app, logs } = wired({ store, controller, platform: "linux" });
    assert.equal(await ipcMain.invoke(IPC_CHANNELS.buddyGetHideFromCapture, app), null, "null: this computer cannot");
    assert.equal(await ipcMain.invoke(IPC_CHANNELS.buddySetHideFromCapture, app, false), null);
    assert.equal(await ipcMain.invoke(IPC_CHANNELS.buddySetHideFromCapture, app, true), null);
    assert.deepEqual(seen, [], "the window was asked for nothing");
    assert.equal(CAPTURE_KEY in store.data, false, "the store keeps no switch that does nothing");
    assert.equal(logs.filter((l) => /not available on linux/.test(l)).length, 2);
  });

  it("on Windows it reads and sets as on macOS", async () => {
    const { ipcMain, app } = wired({ platform: "win32" });
    assert.equal(await ipcMain.invoke(IPC_CHANNELS.buddyGetHideFromCapture, app), true);
    assert.equal(await ipcMain.invoke(IPC_CHANNELS.buddySetHideFromCapture, app, false), false);
  });

  it("unregisters all four", () => {
    const { ipcMain, unregister } = wired();
    unregister();
    assert.deepEqual(ipcMain.channels(), []);
  });
});
