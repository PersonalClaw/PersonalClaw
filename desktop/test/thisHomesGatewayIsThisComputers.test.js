"use strict";

/**
 * This home's own gateway, saved or typed as a paired gateway, is used as this computer's.
 *
 * A gateway started outside the app (`personalclaw gateway`, or the service) and then added in the
 * Gateways window by its address (`http://127.0.0.1:10000`) used to open as a paired gateway: a
 * window with no desktop bridge, so Settings' Buddy status and the menu bar's Show Buddy did
 * nothing, and Settings › Updates, the status bar and the menu's Check for Updates… offered only
 * the gateway's update, never the app's. The Gateways window listed the one gateway twice.
 *
 * Now an address that is this home's proven gateway (loopback, plain http, the port
 * `homeGateway.reach` proved serves the home) is used as This computer, as Use It uses a gateway
 * the app found, and its paired rows fold into This computer. A remote address, or a loopback one
 * on another port, still opens as the paired gateway it is; and with no gateway of this
 * computer's in use, Show Buddy says why the buddy can't show instead of doing nothing.
 *
 * `useAsThisComputer` runs as main.js has it, beside the real `useRunningHere` and
 * `setUpFirstGateway`, in a context that stands in for the gateway, the window and the buddy.
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const connectMode = require("../connectMode");
const { addEndpoint, loadRegistry, newEndpointId, saveRegistry, setActive } = require("../endpointRegistry");
const { BUDDY_NOT_HERE, buildTrayMenuTemplate, makeTrayPresence } = require("../trayPresence");

const MAIN = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
const { LOCAL_ENDPOINT_ID, isThisHomesGateway, foldIntoThisComputer } = connectMode;

/** This home's gateway, as `homeGateway.reach` proves it: started outside the app. */
const HOME_PORT = 10190;

function memoryStore() {
  const data = {};
  return {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null),
    setItem: (k, v) => {
      data[k] = String(v);
    },
    removeItem: (k) => {
      delete data[k];
    },
    get length() {
      return Object.keys(data).length;
    },
    key: (i) => Object.keys(data)[i] ?? null,
  };
}

/** A store holding the local row and *paired* rows, the first of them active. */
function storeWith(paired) {
  const store = memoryStore();
  let reg = loadRegistry(store);
  reg = addEndpoint(reg, { id: LOCAL_ENDPOINT_ID, label: "This computer", base_url: "", kind: "local", device_session_ref: "" });
  const ids = paired.map((base_url) => {
    const id = newEndpointId();
    reg = addEndpoint(reg, { id, label: base_url, base_url, kind: "remote", device_session_ref: "" });
    return id;
  });
  if (ids.length) reg = setActive(reg, ids[0]);
  saveRegistry(store, reg);
  return { store, ids };
}

/** The source of main.js's top-level function *name*, as written (`async` included). */
function fnSource(name) {
  const at = MAIN.search(new RegExp(`^(async )?function ${name}\\(`, "m"));
  assert.ok(at >= 0, `${name} is gone — this rail is now blind`);
  return MAIN.slice(at, MAIN.indexOf("\n}\n", at) + 2);
}

/**
 * `useAsThisComputer`, `useRunningHere` and `setUpFirstGateway` as main.js has them. *found* is what
 * looking for this home's gateway finds; *own* is a gateway the app already uses; *usable* is
 * whether Use It may use the one found. *did* records each step, in order.
 */
function run({ store, found = { ok: true, gateway: { pid: 4242, port: HOME_PORT } }, own = null, usable = true }) {
  const did = [];
  const box = {
    URL,
    console: { log() {}, warn() {}, error() {} },
    LOCAL_ENDPOINT_ID,
    isThisHomesGateway,
    foldIntoThisComputer,
    switchTo: connectMode.switchTo,
    rememberLocalGateway: connectMode.rememberLocalGateway,
    shouldAttachBridge: connectMode.shouldAttachBridge,
    shellStore: store,
    localGatewayUrl: own,
    startupWarnings: [],
    findHomeGateway: async () => (did.push("look"), found),
    // Use It's adoption: proven again, signed in to, and the windows' gateway from here on.
    useRunningGateway: async () => {
      did.push("adopt");
      if (!usable) return { ok: false, code: "behind", message: "older than this app" };
      box.localGatewayUrl = `http://127.0.0.1:${found.gateway.port}`;
      return { ok: true };
    },
    syncPaired: () => did.push("sync"),
    startPresenceRefresh: () => did.push("presence"),
    registerWithGateway: async () => did.push("register"),
    buddyPresence: { start: () => did.push("buddy") },
    navigateToEndpoint: async (_win, url, { isLocal = false } = {}) => {
      did.push({ navigate: url, isLocal, bridge: connectMode.shouldAttachBridge(url, box.localGatewayUrl) });
      return true;
    },
  };
  vm.createContext(box);
  vm.runInContext(["setUpFirstGateway", "useRunningHere", "useAsThisComputer"].map(fnSource).join("\n"), box);
  return { box, did, use: (origin) => box.useAsThisComputer(origin, { window: true }) };
}

const navigations = (did) => did.filter((d) => d && d.navigate);

describe("an address is this home's gateway only on loopback, plain http, and the proven port", () => {
  it("reads 127.0.0.1, localhost and [::1] on the home's port as this home's gateway", () => {
    for (const url of [`http://127.0.0.1:${HOME_PORT}`, `http://localhost:${HOME_PORT}`, `http://[::1]:${HOME_PORT}`, `http://127.0.0.1:${HOME_PORT}/pair?code=abc`]) {
      assert.equal(isThisHomesGateway(url, HOME_PORT), true, url);
    }
  });

  it("never reads a remote host as this home's gateway, even on the home's port", () => {
    for (const url of [`http://claw.local:${HOME_PORT}`, `http://192.168.1.5:${HOME_PORT}`, `http://10.0.0.4:${HOME_PORT}`, `https://gateway.example.com:${HOME_PORT}`]) {
      assert.equal(isThisHomesGateway(url, HOME_PORT), false, url);
    }
  });

  it("never reads a loopback address on another port, or over https, as this home's gateway", () => {
    assert.equal(isThisHomesGateway(`http://127.0.0.1:${HOME_PORT + 1}`, HOME_PORT), false);
    assert.equal(isThisHomesGateway("http://127.0.0.1", HOME_PORT), false);
    assert.equal(isThisHomesGateway(`https://127.0.0.1:${HOME_PORT}`, HOME_PORT), false);
    assert.equal(isThisHomesGateway(`http://127.0.0.1:${HOME_PORT}`, 0), false, "no proven port, no match");
    assert.equal(isThisHomesGateway("", HOME_PORT), false);
  });
});

describe("the paired rows of this home's gateway fold into This computer", () => {
  it("forgets each paired row that is this home's gateway and keeps every other row", () => {
    const { store, ids } = storeWith([
      `http://127.0.0.1:${HOME_PORT}`,
      `http://localhost:${HOME_PORT}`,
      `http://127.0.0.1:${HOME_PORT + 1}`,
      `http://claw.local:${HOME_PORT}`,
    ]);
    assert.deepEqual(foldIntoThisComputer(store, HOME_PORT).sort(), [ids[0], ids[1]].sort());
    const left = loadRegistry(store).endpoints.map((e) => e.id).sort();
    assert.deepEqual(left, [LOCAL_ENDPOINT_ID, ids[2], ids[3]].sort());
  });
});

describe("this home's gateway, saved as a paired one, is used as this computer's", () => {
  it("adopts it, opens the window on it with the app's bridge, starts the buddy, and folds its row", async () => {
    const { store } = storeWith([`http://localhost:${HOME_PORT}`]);
    const { box, did, use } = run({ store });
    assert.equal(await use(`http://localhost:${HOME_PORT}`), true);
    assert.ok(did.includes("adopt"), "used as Use It uses a gateway the app found");
    assert.ok(did.includes("buddy"), "the buddy starts on this computer's gateway");
    assert.deepEqual(navigations(did), [{ navigate: `http://127.0.0.1:${HOME_PORT}`, isLocal: true, bridge: true }]);
    const reg = loadRegistry(store);
    assert.equal(reg.active, LOCAL_ENDPOINT_ID, "This computer is the active gateway");
    assert.deepEqual(reg.endpoints.map((e) => e.id), [LOCAL_ENDPOINT_ID], "the paired copy folded into This computer");
    assert.equal(box.localGatewayUrl, `http://127.0.0.1:${HOME_PORT}`);
  });

  it("switches to a gateway the app already uses when the paired address is it, without adopting again", async () => {
    const { store } = storeWith([`http://127.0.0.1:${HOME_PORT}`]);
    const { did, use } = run({ store, own: `http://127.0.0.1:${HOME_PORT}` });
    assert.equal(await use(`http://127.0.0.1:${HOME_PORT}`), true);
    assert.equal(did.includes("look") || did.includes("adopt"), false, "nothing looked for or adopted");
    assert.deepEqual(navigations(did), [{ navigate: `http://127.0.0.1:${HOME_PORT}`, isLocal: true, bridge: true }]);
    assert.equal(loadRegistry(store).endpoints.length, 1);
  });

  it("leaves a remote gateway on the home's port a paired gateway: nothing is looked for or adopted", async () => {
    const { store } = storeWith([`http://claw.local:${HOME_PORT}`]);
    const { did, use } = run({ store });
    assert.equal(await use(`http://claw.local:${HOME_PORT}`), false);
    assert.deepEqual(did, []);
    assert.equal(loadRegistry(store).endpoints.length, 2, "its paired row stays");
  });

  it("leaves a loopback address on another port, which isn't this home's gateway, a paired gateway", async () => {
    const other = `http://127.0.0.1:${HOME_PORT + 7}`;
    const { store } = storeWith([other]);
    const { did, use } = run({ store });
    assert.equal(await use(other), false);
    assert.equal(did.includes("adopt"), false, "not adopted");
    assert.deepEqual(navigations(did), []);
    assert.equal(loadRegistry(store).endpoints.length, 2);
  });

  it("with this computer's gateway in use, leaves a loopback address on another port a paired gateway, its own", async () => {
    const own = `http://127.0.0.1:${HOME_PORT}`;
    const other = `http://127.0.0.1:${HOME_PORT + 3}`;
    const { store, ids } = storeWith([other]);
    const { box, did, use } = run({ store, own });
    assert.equal(await use(other), false, "opened as the paired gateway it is");
    assert.deepEqual(did, [], "nothing looked for, adopted or opened as This computer");
    assert.equal(box.localGatewayUrl, own, "this computer's gateway is still the one in use");
    const reg = loadRegistry(store);
    assert.equal(reg.active, ids[0], "its paired row stays the active one");
    assert.deepEqual(reg.endpoints.map((e) => e.base_url).sort(), ["", other].sort(), "and stays paired, not folded");
  });

  it("leaves it a paired gateway when no gateway serves this home, or Use It may not use the one found", async () => {
    for (const setup of [{ found: { ok: false, reason: "none" } }, { usable: false }]) {
      const { store } = storeWith([`http://127.0.0.1:${HOME_PORT}`]);
      const { did, use } = run({ store, ...setup });
      assert.equal(await use(`http://127.0.0.1:${HOME_PORT}`), false, JSON.stringify(setup));
      assert.deepEqual(navigations(did), []);
      assert.equal(loadRegistry(store).endpoints.length, 2, "its paired row stays");
    }
  });
});

describe("every way to a paired gateway asks first whether it is this home's own", () => {
  it("the start, the Gateways window's Connect and its switch each go through useAsThisComputer", () => {
    assert.match(MAIN, /startup\.mode === "connect" && \(await useAsThisComputer\(startup\.origin, win\)\)/, "the start");
    assert.match(MAIN, /if \(await useAsThisComputer\(plan\.origin, mainWindow\)\)/, "Connect, for an address or a pairing link");
    assert.match(MAIN, /row\.kind !== "local" && \(await useAsThisComputer\(row\.base_url, mainWindow\)\)/, "a switch to a paired row");
    // Each comes before the paired path it replaces.
    const start = MAIN.indexOf("useAsThisComputer(startup.origin, win)");
    assert.ok(start > 0 && start < MAIN.indexOf("connecting to a paired gateway: ${startup.origin}"));
    const confirm = MAIN.indexOf("useAsThisComputer(plan.origin, mainWindow)");
    assert.ok(confirm > 0 && confirm < MAIN.indexOf("confirmEndpoint(shellStore, plan, { label })"));
  });
});

describe("Show Buddy says why when no gateway of this computer's is in use", () => {
  const actions = { toggleBuddy: () => {} };
  const rows = (buddy) => buildTrayMenuTemplate({ buddy, actions });

  it("is turned off, unchecked, and says the buddy shows only with this computer's gateway", () => {
    const menu = rows({ enabled: true, available: false });
    const item = menu.find((r) => r.label === "Show Buddy");
    assert.equal(item.enabled, false);
    assert.equal(item.checked, false);
    const at = menu.indexOf(item);
    assert.deepEqual(menu[at + 1], { label: BUDDY_NOT_HERE, enabled: false });
    assert.equal(menu.some((r) => /Hide Buddy|Show Buddy Now/.test(r.label || "")), false, "nothing to hide that can't show");
  });

  it("works as before while a gateway of this computer's is in use", () => {
    for (const buddy of [{ enabled: true, available: true }, { enabled: true }]) {
      const menu = rows(buddy);
      const item = menu.find((r) => r.label === "Show Buddy");
      assert.equal(item.enabled, true);
      assert.equal(item.checked, true);
      assert.equal(menu.some((r) => r.label === BUDDY_NOT_HERE), false);
    }
  });

  it("the tray reads it from the app at each render, and main.js answers with its own gateway", () => {
    let local = false;
    const menus = [];
    class Tray {
      setTitle() {}
      setToolTip() {}
      setContextMenu(m) {
        menus.push(m.template);
      }
      on() {}
      destroy() {}
    }
    const image = { isEmpty: () => false, resize: () => image };
    const tray = makeTrayPresence({
      TrayCtor: Tray,
      MenuCtor: { buildFromTemplate: (t) => ({ template: t }) },
      nativeImageMod: { createFromPath: () => image },
      trayImagePath: "/tray.png",
      buddyAvailable: () => local,
      actions,
    });
    assert.equal(tray.start(), true);
    assert.ok(menus.at(-1).some((r) => r.label === BUDDY_NOT_HERE));
    local = true;
    tray.setBuddyState({ enabled: true });
    assert.equal(menus.at(-1).some((r) => r.label === BUDDY_NOT_HERE), false);
    assert.match(MAIN, /buddyAvailable: \(\) => Boolean\(localGatewayUrl\),/);
  });
});
