const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const { makeLoginItem, registerLoginItemIpc, opensQuietly, SUPPORTED_PLATFORMS } = require("../loginItem");
const { buildTrayMenuTemplate } = require("../trayPresence");

/**
 * A fake Electron `app`. NOTHING in this file may reach the real login-item registry:
 * every read and write lands here, and `writes` is what the idempotence and
 * opt-in assertions inspect. There is no filesystem path involved on either side —
 * the real implementation calls `app.setLoginItemSettings()`, so mocking that API IS
 * the boundary.
 */
function fakeApp({ openAtLogin = false, throwOnGet = false, throwOnSet = false, ignoreWrites = false } = {}) {
  const app = {
    state: { openAtLogin },
    writes: [],
    reads: 0,
    getLoginItemSettings() {
      app.reads += 1;
      if (throwOnGet) throw new Error("settings unavailable");
      return { openAtLogin: app.state.openAtLogin };
    },
    setLoginItemSettings(settings) {
      app.writes.push(settings);
      if (throwOnSet) throw new Error("write refused");
      if (!ignoreWrites) app.state.openAtLogin = Boolean(settings.openAtLogin);
    },
  };
  return app;
}

describe("login item — opt-in", () => {
  it("constructing it writes NOTHING (never enabled by default)", () => {
    const app = fakeApp();
    const item = makeLoginItem({ app, platform: "darwin" });
    assert.deepStrictEqual(app.writes, [], "construction must not register a login item");
    assert.equal(item.isEnabled(), false);
  });

  it("reads the OS rather than caching an assumption", () => {
    const app = fakeApp({ openAtLogin: true });
    const item = makeLoginItem({ app, platform: "darwin" });
    assert.equal(item.isEnabled(), true);
    app.state.openAtLogin = false;
    assert.equal(item.isEnabled(), false, "state must come from the OS on every read");
  });
});

describe("login item — idempotence", () => {
  it("enabling twice writes once (cannot create two entries)", () => {
    const app = fakeApp();
    const item = makeLoginItem({ app, platform: "darwin" });

    const first = item.set(true);
    assert.deepStrictEqual(
      { ok: first.ok, enabled: first.enabled, changed: first.changed },
      { ok: true, enabled: true, changed: true }
    );
    assert.equal(app.writes.length, 1);

    const second = item.set(true);
    assert.equal(second.ok, true);
    assert.equal(second.enabled, true);
    assert.equal(second.changed, false, "a repeat enable must report no change");
    assert.equal(app.writes.length, 1, "a repeat enable must not write again");
  });

  it("disabling twice writes once", () => {
    const app = fakeApp({ openAtLogin: true });
    const item = makeLoginItem({ app, platform: "darwin" });
    assert.equal(item.set(false).changed, true);
    assert.equal(app.writes.length, 1);
    assert.equal(item.set(false).changed, false);
    assert.equal(app.writes.length, 1);
  });
});

describe("login item — reversibility", () => {
  it("set(false) undoes set(true) and leaves no residue", () => {
    const app = fakeApp();
    const item = makeLoginItem({ app, platform: "darwin" });
    item.set(true);
    assert.equal(item.isEnabled(), true);
    item.set(false);
    assert.equal(item.isEnabled(), false);
    // Both writes are plain openAtLogin flips — no second mechanism to unwind.
    assert.deepStrictEqual(
      app.writes.map((w) => w.openAtLogin),
      [true, false]
    );
  });

  it("writes only openAtLogin", () => {
    const app = fakeApp();
    makeLoginItem({ app, platform: "darwin" }).set(true);
    assert.deepStrictEqual(app.writes, [{ openAtLogin: true }]);
  });
});

describe("login item — degradation", () => {
  it("is unsupported (and silent) on a platform Electron does not implement", () => {
    const app = fakeApp();
    const item = makeLoginItem({ app, platform: "linux" });
    assert.equal(item.supported, false);
    const res = item.set(true);
    assert.equal(res.ok, false);
    assert.equal(res.supported, false);
    assert.match(res.reason, /linux/);
    assert.deepStrictEqual(app.writes, [], "an unsupported platform must not write");
  });

  it("an unreadable settings API reads as off instead of throwing", () => {
    const item = makeLoginItem({ app: fakeApp({ throwOnGet: true }), platform: "darwin" });
    assert.equal(item.isEnabled(), false);
  });

  it("a refused write is reported, not swallowed", () => {
    const item = makeLoginItem({ app: fakeApp({ throwOnSet: true }), platform: "darwin" });
    const res = item.set(true);
    assert.equal(res.ok, false);
    assert.equal(res.reason, "write refused");
  });

  it("a write the OS silently ignores is NOT reported as success", () => {
    // The inert-control case: setLoginItemSettings returns void, so trusting it
    // would let a toggle claim success while nothing was registered.
    const item = makeLoginItem({ app: fakeApp({ ignoreWrites: true }), platform: "darwin" });
    const res = item.set(true);
    assert.equal(res.ok, false);
    assert.equal(res.enabled, false);
    assert.match(res.reason, /did not apply/);
  });

  it("describe() names what it touches on macOS", () => {
    const item = makeLoginItem({ app: fakeApp(), platform: "darwin" });
    assert.match(item.describe(), /Login Items/);
  });
});

describe("login item — IPC surface", () => {
  it("registers exactly get + set, and coerces the renderer's argument", async () => {
    const app = fakeApp();
    const item = makeLoginItem({ app, platform: "darwin" });
    const handlers = new Map();
    const ipcMain = { handle: (ch, fn) => handlers.set(ch, fn) };
    const channels = { loginItemGet: "pclaw:login-item-get", loginItemSet: "pclaw:login-item-set" };

    registerLoginItemIpc(ipcMain, item, channels);
    // Vacuity floor: a registration that wired nothing would pass every assertion
    // below by never being called.
    assert.equal(handlers.size, 2, "both login-item channels must be registered");

    const got = await handlers.get(channels.loginItemGet)(null);
    assert.deepStrictEqual({ enabled: got.enabled, supported: got.supported }, { enabled: false, supported: true });

    // A compromised renderer can send anything over IPC; the main process coerces.
    const set = await handlers.get(channels.loginItemSet)(null, "yes please");
    assert.equal(set.enabled, true);
    assert.equal(app.writes[0].openAtLogin, true, "the argument must be coerced to a boolean");
  });

  it("SUPPORTED_PLATFORMS is non-empty and excludes linux (vacuity floor)", () => {
    assert.ok(SUPPORTED_PLATFORMS.length >= 1);
    assert.ok(SUPPORTED_PLATFORMS.includes("darwin"));
    assert.ok(!SUPPORTED_PLATFORMS.includes("linux"));
  });
});

/**
 * ONE registration, TWO surfaces.
 *
 * The tray checkbox and the Settings toggle must not be two mechanisms. The bar these
 * cases hold is the one that makes them one: drive each surface against a single
 * `makeLoginItem` over a single fake `app`, and read the OTHER surface's rendered
 * state afterwards. If either write could be removed without reddening a case here,
 * they would be two mechanisms wearing one label.
 *
 * `buildTrayMenuTemplate` is imported deliberately: the tray's rendered checkbox is
 * the observable the Settings path has to move, and asserting on `loginItem.isEnabled()`
 * instead would pass even while the menu showed a stale value — which is exactly the
 * bug this closes.
 */
describe("login item — one registration, two surfaces", () => {
  /** The `checked`/`enabled` the menu-bar item would actually draw. */
  const trayCheckbox = (state) =>
    buildTrayMenuTemplate({ loginItem: state }).find((row) => row.label === "Open at Login");

  /** The wiring `main.js` performs: one writer of the tray's cached state, called by
   * every surface that can write the registration. */
  function wire({ platform = "darwin" } = {}) {
    const app = fakeApp();
    const item = makeLoginItem({ app, platform });
    let trayState = { supported: item.supported, enabled: item.isEnabled() };
    const syncToTray = () => {
      trayState = { supported: item.supported, enabled: item.isEnabled() };
    };
    const handlers = new Map();
    const channels = { loginItemGet: "pclaw:login-item-get", loginItemSet: "pclaw:login-item-set" };
    registerLoginItemIpc({ handle: (ch, fn) => handlers.set(ch, fn) }, item, channels, syncToTray);
    return {
      app,
      item,
      // The Settings surface: the renderer's `pclawDesktop.loginItem.set()`.
      settingsSet: (v) => handlers.get(channels.loginItemSet)(null, v),
      settingsGet: () => handlers.get(channels.loginItemGet)(null),
      // The tray surface: the checkbox's own click path from `main.js`.
      trayClick: (next) => { item.set(next); syncToTray(); },
      tray: () => trayCheckbox(trayState),
    };
  }

  it("a Settings flip moves the tray checkbox (the stale-menu bug)", async () => {
    const w = wire();
    assert.equal(w.tray().checked, false, "vacuity floor: it must start unchecked");

    await w.settingsSet(true);

    assert.equal(w.tray().checked, true, "the tray checkbox must follow a Settings write");
    assert.equal(w.app.state.openAtLogin, true, "and the OS must actually hold the registration");
  });

  it("a tray click is visible to Settings' next read", async () => {
    const w = wire();
    w.trayClick(true);
    assert.deepStrictEqual(
      await w.settingsGet(),
      { enabled: true, supported: true, describes: w.item.describe() },
      "Settings must read the registration the tray wrote, not a cache of its own",
    );
  });

  it("the two surfaces write the SAME registration, not one each", async () => {
    const w = wire();
    // Settings on, tray off, Settings on again — if these were two mechanisms the
    // second write would not see the first one's state and the writes would not
    // alternate.
    await w.settingsSet(true);
    w.trayClick(false);
    await w.settingsSet(true);
    assert.deepStrictEqual(
      w.app.writes.map((s) => s.openAtLogin),
      [true, false, true],
      "each write must observe the previous one — one registration, not two",
    );
    assert.equal(w.tray().checked, true);
  });

  it("a Settings write the OS refuses leaves BOTH surfaces showing the truth", async () => {
    const app = fakeApp({ ignoreWrites: true });
    const item = makeLoginItem({ app, platform: "darwin" });
    let trayState = { supported: item.supported, enabled: item.isEnabled() };
    const handlers = new Map();
    const channels = { loginItemGet: "g", loginItemSet: "s" };
    registerLoginItemIpc({ handle: (ch, fn) => handlers.set(ch, fn) }, item, channels, () => {
      trayState = { supported: item.supported, enabled: item.isEnabled() };
    });

    const res = await handlers.get("s")(null, true);

    assert.equal(res.ok, false, "a write the OS drops is not ok");
    assert.equal(res.enabled, false, "and it reports the OS's state, not the request");
    assert.equal(trayCheckbox(trayState).checked, false, "the tray must not show a registration that does not exist");
  });

  it("an unsupported platform disables the checkbox on both surfaces", async () => {
    const w = wire({ platform: "linux" });
    const got = await w.settingsGet();
    assert.equal(got.supported, false);
    assert.equal(w.tray().enabled, false, "an unsupported platform must not offer a live checkbox");
    await w.settingsSet(true);
    assert.deepStrictEqual(w.app.writes, [], "and nothing may be written there");
  });
});

describe("login item — a login start is quiet", () => {
  const launched = ({ atLogin = false, throwOnGet = false } = {}) => ({
    getLoginItemSettings() {
      if (throwOnGet) throw new Error("settings unavailable");
      return { openAtLogin: true, wasOpenedAtLogin: atLogin };
    },
  });

  it("a launch macOS made at login starts with no window while the menu bar can bring it back", () => {
    assert.equal(opensQuietly({ app: launched({ atLogin: true }), platform: "darwin", trayAvailable: true }), true);
  });

  for (const [why, deps] of [
    ["a launch the user made", { app: launched(), platform: "darwin", trayAvailable: true }],
    ["a login launch with no menu-bar item to open it from", { app: launched({ atLogin: true }), platform: "darwin", trayAvailable: false }],
    ["a login launch whose settings can't be read", { app: launched({ atLogin: true, throwOnGet: true }), platform: "darwin", trayAvailable: true }],
    ["Windows, where Electron never says a launch was a login one", { app: launched({ atLogin: true }), platform: "win32", trayAvailable: true }],
    ["Linux", { app: launched({ atLogin: true }), platform: "linux", trayAvailable: true }],
  ]) {
    it(`${why} opens the window`, () => {
      assert.equal(opensQuietly(deps), false);
    });
  }

  it("main.js starts the main window hidden on a quiet start, and keeps the boot's own shows quiet", () => {
    const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
    const boot = main.slice(main.indexOf("trayPresence.start()"));
    const asked = boot.indexOf("quietLogin = opensQuietly({ app, platform: process.platform, trayAvailable: trayPresence.available });");
    assert.ok(asked >= 0, "the boot must ask once the tray is up");
    assert.ok(asked < boot.indexOf("const win = createWindow({ show: !quietLogin });"), "and before the main window is made");
    assert.match(main, /function makeWindow\(\{ show = true \} = \{\}\) \{\n  const win = new BaseWindow\(\{\n    show,/);
    const loading = /async function showLoadingThenConnect\(win\) \{[\s\S]*?\n\}/.exec(main)[0];
    assert.match(loading, /if \(!quietLogin\) win\.show\(\);/);
    assert.doesNotMatch(loading, /^  win\.show\(\);$/m);
    assert.match(loading, /showNotConnected\(\{ quiet: quietLogin \}\);/);
  });

  it("the Dock can open a quiet start's window before the boot's first wait", () => {
    const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
    assert.equal([...main.matchAll(/app\.on\("activate"/g)].length, 1, "one activate handler");
    const boot = main.slice(main.indexOf("app.whenReady().then("));
    const made = boot.indexOf("const win = createWindow({ show: !quietLogin });");
    const dock = boot.indexOf('app.on("activate"');
    assert.ok(made >= 0 && made < dock, "after the window is made");
    assert.ok(dock < boot.indexOf("await bringUpLocalGateway();"), "and before the boot waits for a gateway");
    assert.ok(dock < boot.indexOf("await ", made), "or for anything else once the window is made");
    assert.match(boot.slice(dock), /^app\.on\("activate", \(\) => \{\n\s*if \(!mainWindow\?\.isVisible\(\)\) mainWindow\?\.show\(\);\n\s*\}\);/, "and it shows the window");
  });

  it("a quiet not-connected only loads the offline page, and a loud one also shows the window and opens the chooser", () => {
    const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
    const source = /function showNotConnected\(\{ quiet = false \} = \{\}\) \{[\s\S]*?\n\}/.exec(main);
    assert.ok(source, "showNotConnected must be findable");
    const run = (quiet) => {
      const did = [];
      const mainWindow = {
        isDestroyed: () => false,
        isMinimized: () => true,
        restore: () => did.push("restore"),
        show: () => did.push("show"),
        webContents: { loadFile: (file, options) => did.push(`load ${path.basename(file)} ${options?.query?.state}`) },
      };
      const connectDialog = { openChooser: () => did.push("chooser") };
      vm.runInNewContext(`${source[0]}\nshowNotConnected({ quiet: ${quiet} });`, {
        path, __dirname: "/app", mainWindow, connectDialog, activeUrl: null, localGatewayUrl: "http://127.0.0.1:1",
      });
      return did;
    };
    assert.deepStrictEqual(run(true), ["load loading.html offline"]);
    assert.deepStrictEqual(run(false), ["load loading.html offline", "restore", "show", "chooser"]);
  });
});
