const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const { makeLoginItem, registerLoginItemIpc, SUPPORTED_PLATFORMS } = require("../loginItem");

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

  it("requests a hidden launch, so a login start is quiet", () => {
    const app = fakeApp();
    makeLoginItem({ app, platform: "darwin" }).set(true);
    assert.equal(app.writes[0].openAsHidden, true);
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
