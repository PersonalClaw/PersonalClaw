/**
 * The switcher's display model and its IPC adaptor.
 *
 * The dialog itself is a static document that builds every row with `textContent`, so what is worth
 * testing here is the model it renders from — in particular that the health states a user must act
 * on stay distinguishable from the ones they should ignore, and that the IPC layer answers a shaped
 * refusal instead of leaking a main-process stack into a window.
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { CONNECT_CHANNELS, LOCAL_STATES, describeLocal, describeChooser, healthCopy, describeRow, describeList, makeConnectDialog } = require("../connectDialog");
const {
  LOCAL_ENDPOINT_ID,
  HEALTH_STATES,
  HEALTH_UNKNOWN,
  HEALTH_REACHABLE,
  HEALTH_UNREACHABLE,
  HEALTH_SIGNED_OUT,
} = require("../connectMode");

const row = (over = {}) => ({ id: "ep_a", label: "Work brain", base_url: "http://10.0.0.4:10000", kind: "remote", device_session_ref: "", ...over });

describe("healthCopy", () => {
  it("has copy for every state in the closed set — no state renders as blank", () => {
    for (const status of HEALTH_STATES) {
      const c = healthCopy(status);
      assert.ok(c.text, status);
      assert.ok(["ok", "warn", "act", "bad", "idle"].includes(c.tone), `${status} -> ${c.tone}`);
    }
  });

  it("says NOT CHECKED YET for unknown and NOT ANSWERING for unreachable — these are different facts", () => {
    assert.notStrictEqual(healthCopy(HEALTH_UNKNOWN).text, healthCopy(HEALTH_UNREACHABLE).text);
    assert.match(healthCopy(HEALTH_UNKNOWN).text, /not checked/i);
    assert.match(healthCopy(HEALTH_UNREACHABLE).text, /not answering/i);
    assert.strictEqual(healthCopy(HEALTH_UNKNOWN).tone, "idle");
  });

  it("marks signed_out as the ONE state that asks the user to do something", () => {
    const actionable = HEALTH_STATES.filter((s) => healthCopy(s).tone === "act");
    assert.deepStrictEqual(actionable, [HEALTH_SIGNED_OUT]);
  });

  it("falls back to the idle copy for a status it has never heard of", () => {
    assert.strictEqual(healthCopy("something_new").tone, "idle");
  });
});

describe("describeRow", () => {
  it("marks the active row and reports the bridge-relevant kind", () => {
    const r = describeRow(row(), { activeId: "ep_a", health: { ep_a: { status: HEALTH_REACHABLE, version: "1.2.3" } } });
    assert.strictEqual(r.active, true);
    assert.strictEqual(r.kind, "remote");
    assert.strictEqual(r.removable, true);
    assert.strictEqual(r.version, "1.2.3");
    assert.strictEqual(r.statusTone, "ok");
  });

  it("shows the LIVE local URL for the reserved row, not the one that was persisted last launch", () => {
    // The spawn-local port is OS-assigned, so the stored value is stale by definition on relaunch.
    const r = describeRow(row({ id: LOCAL_ENDPOINT_ID, kind: "local", base_url: "http://localhost:1111" }), {
      activeId: LOCAL_ENDPOINT_ID,
      localBaseUrl: "http://localhost:52222",
    });
    assert.strictEqual(r.url, "http://localhost:52222");
    assert.strictEqual(r.kind, "local");
    assert.strictEqual(r.removable, false, "the gateway this shell spawned is not forgettable");
  });

  it("reports a row with no URL as missingUrl, and does not call it unreachable", () => {
    const r = describeRow(row({ base_url: "" }), { activeId: "other" });
    assert.strictEqual(r.missingUrl, true);
    assert.strictEqual(r.status, HEALTH_UNKNOWN);
  });

  it("falls back to the URL then the id when a row has no label", () => {
    assert.strictEqual(describeRow(row({ label: "" }), {}).label, "http://10.0.0.4:10000");
    assert.strictEqual(describeRow(row({ label: "", base_url: "" }), {}).label, "ep_a");
  });

  it("defaults to `unknown` when no health has been probed — an empty map is not all-unreachable", () => {
    assert.strictEqual(describeRow(row(), { activeId: "ep_a" }).status, HEALTH_UNKNOWN);
  });
});

describe("describeList", () => {
  it("carries the store's status and safety through, so the dialog can warn", () => {
    const list = describeList({
      registry: { active: "ep_a", endpoints: [row()] },
      health: {},
      localBaseUrl: "",
      warnings: [{ code: "store_permissions", message: "m" }],
      storeStatus: "ok",
      storeSafe: false,
      readOnly: true,
    });
    assert.strictEqual(list.activeId, "ep_a");
    assert.deepStrictEqual(list.rows.map((r) => r.id), [LOCAL_ENDPOINT_ID, "ep_a"], "this computer's row, then the paired one");
    assert.strictEqual(list.storeSafe, false);
    assert.strictEqual(list.readOnly, true);
    assert.strictEqual(list.warnings[0].code, "store_permissions");
  });

  it("handles a registry with no rows at all, and still lists this computer: it is where a gateway is started", () => {
    const list = describeList({ registry: { active: "", endpoints: [] }, local: { state: "stopped", sentence: "PersonalClaw's gateway did not start." } });
    assert.strictEqual(list.rows.length, 1);
    assert.strictEqual(list.rows[0].id, LOCAL_ENDPOINT_ID);
    assert.strictEqual(list.rows[0].kind, "local");
    assert.deepStrictEqual(list.rows[0].localAction, { id: "start", label: "Start a Gateway Here" });
    assert.strictEqual(list.activeId, "");
  });

  it("lists this computer's row once, where the registry keeps it", () => {
    const local = row({ id: LOCAL_ENDPOINT_ID, kind: "local", label: "This computer", base_url: "http://localhost:1" });
    const list = describeList({ registry: { active: "", endpoints: [row(), local] } });
    assert.deepStrictEqual(list.rows.map((r) => r.id), ["ep_a", LOCAL_ENDPOINT_ID]);
  });
});

describe("the \"This computer\" row, by how this computer's gateway stands", () => {
  const localRow = row({ id: LOCAL_ENDPOINT_ID, kind: "local", label: "This computer", base_url: "http://127.0.0.1:10000" });
  const at = (local, health = {}) => describeRow(localRow, { activeId: LOCAL_ENDPOINT_ID, localBaseUrl: "http://127.0.0.1:10000", health, local });

  it("has a word and an offer for every state, and an unknown state reads as starting", () => {
    for (const state of LOCAL_STATES) assert.equal(describeLocal({ state }).state, state);
    assert.equal(describeLocal({ state: "something-new" }).state, "starting");
    assert.equal(describeLocal(undefined).state, "starting");
  });

  it("says the app started it, or that it was already running and keeps running when the app quits", () => {
    const own = at({ state: "own" }, { [LOCAL_ENDPOINT_ID]: { status: HEALTH_REACHABLE, version: "0.3.6" } });
    assert.equal(own.localBadge, "started by this app");
    assert.equal(own.statusText, "Reachable");
    assert.equal(own.version, "0.3.6");
    assert.equal(own.localAction, null);
    const adopted = at({ state: "adopted" }, { [LOCAL_ENDPOINT_ID]: { status: HEALTH_REACHABLE, version: "0.3.7" } });
    assert.equal(adopted.localBadge, "already running");
    assert.match(adopted.statusDetail, /Not started by this app\. Quitting PersonalClaw leaves it running\./);
    assert.equal(adopted.switchable, true);
  });

  it("offers Start a gateway here when none runs, with why, and nothing to switch to", () => {
    const stopped = at({ state: "stopped", sentence: "PersonalClaw's gateway stopped. The gateway exited with code 1." }, { [LOCAL_ENDPOINT_ID]: { status: HEALTH_REACHABLE, version: "0.3.6" } });
    assert.equal(stopped.statusText, "Not running", "a stale probe does not say a stopped gateway is reachable");
    assert.equal(stopped.version, "");
    assert.equal(stopped.statusDetail, "PersonalClaw's gateway stopped. The gateway exited with code 1.");
    assert.deepEqual(stopped.localAction, { id: "start", label: "Start a Gateway Here" });
    assert.equal(stopped.switchable, false);
  });

  it("offers Try again, never a second start, while a gateway the app cannot sign in to serves the home", () => {
    const served = at({ state: "served", sentence: "Port 10000 … did not prove it holds this home's local secret …" });
    assert.equal(served.statusText, "Can't sign in to it");
    assert.deepEqual(served.localAction, { id: "start", label: "Try Again" });
    assert.match(served.statusDetail, /did not prove/);
  });

  it("offers Use It for a running gateway the app found, with why, and nothing to switch to before then", () => {
    const found = at({ state: "found", sentence: "PersonalClaw's own gateway didn't start: this home's gateway is already running, on port 10000 (pid 4242)…" });
    assert.equal(found.localBadge, "already running");
    assert.equal(found.statusText, "Running, not used yet");
    assert.deepEqual(found.localAction, { id: "use", label: "Use It" });
    assert.match(found.statusDetail, /already running, on port 10000/);
    assert.equal(found.switchable, false, "Switch would open a gateway the app is not signed in to");
  });

  it("offers Ask Each Time on a gateway used without asking, and nothing more on one used on a press", () => {
    const asked = at({ state: "adopted" }, { [LOCAL_ENDPOINT_ID]: { status: HEALTH_REACHABLE } });
    assert.equal(asked.localOption, null);
    const always = at({ state: "adopted", autoUse: true }, { [LOCAL_ENDPOINT_ID]: { status: HEALTH_REACHABLE } });
    assert.deepEqual(always.localOption, { id: "ask", label: "Ask Each Time" });
    assert.match(always.statusDetail, /used without asking when the app's own gateway doesn't start/);
    assert.equal(always.localAction, null, "Switch stays");
    assert.equal(at({ state: "own", autoUse: true }).localOption, null, "the app's own gateway is never 'used without asking'");
  });

  it("offers nothing while it starts", () => {
    const starting = at({ state: "starting" });
    assert.equal(starting.statusText, "Starting…");
    assert.equal(starting.localAction, null);
    assert.equal(starting.switchable, false);
  });

  it("leaves a paired row as it was", () => {
    const paired = describeRow(row(), { activeId: "x", local: { state: "stopped", sentence: "s" } });
    assert.equal(paired.localState, "");
    assert.equal(paired.localAction, null);
    assert.equal(paired.statusDetail, "");
  });
});

describe("the chooser, while nothing serves the windows", () => {
  it("offers the running gateway only once it is found, and Add a Gateway and Try Again always", () => {
    const found = describeChooser({ state: "found", sentence: "already running, on port 10000" });
    assert.equal(found.use, true);
    assert.equal(found.sentence, "already running, on port 10000");
    for (const state of ["stopped", "served"]) {
      const none = describeChooser({ state, sentence: "why" });
      assert.equal(none.use, false, `${state} offers a gateway it has not proved`);
      assert.equal(none.sentence, "why");
      assert.equal(none.busy, false);
      assert.equal(none.settled, false);
    }
  });

  it("holds its offers while a start runs, and has nothing to choose once a gateway serves", () => {
    assert.equal(describeChooser({ state: "starting" }).busy, true);
    assert.equal(describeChooser({ state: "starting" }).use, false);
    for (const state of ["own", "adopted"]) assert.equal(describeChooser({ state }).settled, true);
    assert.equal(describeChooser(undefined).busy, true, "an unknown state reads as starting");
  });

  it("is in the list the windows read", () => {
    const listed = describeList({ registry: { endpoints: [] }, local: { state: "found", sentence: "s" } });
    assert.equal(listed.chooser.use, true);
  });
});

describe("the IPC adaptor", () => {
  function fakeIpc() {
    const handlers = {};
    return {
      handle: (name, fn) => {
        handlers[name] = fn;
      },
      call: (name, arg) => handlers[name](null, arg),
      names: () => Object.keys(handlers),
    };
  }

  it("registers exactly the ten invokes the preload exposes; `changed` is the main process's to send", () => {
    const ipc = fakeIpc();
    makeConnectDialog({ BrowserWindowCtor: class {}, ipcMain: ipc, handlers: {} }).registerIpc(ipc);
    const invokes = Object.entries(CONNECT_CHANNELS).filter(([k]) => k !== "changed").map(([, v]) => v);
    assert.equal(invokes.length, 10);
    assert.deepStrictEqual(ipc.names().sort(), invokes.sort());
    const preload = fs.readFileSync(path.join(__dirname, "..", "connectPreload.js"), "utf8");
    for (const key of Object.keys(CONNECT_CHANNELS)) assert.match(preload, new RegExp(`CONNECT_CHANNELS\\.${key}\\b`), `the preload does not expose ${key}`);
  });

  it("takes the chooser's yes only as `true`: a renderer's other values mean no", async () => {
    const seen = [];
    const ipc = fakeIpc();
    const handlers = { use: async (arg) => seen.push(["use", arg]), autoUse: async (arg) => seen.push(["autoUse", arg]) };
    makeConnectDialog({ BrowserWindowCtor: class {}, ipcMain: ipc, handlers }).registerIpc(ipc);
    await ipc.call(CONNECT_CHANNELS.use, { remember: "yes" });
    await ipc.call(CONNECT_CHANNELS.use, { remember: true });
    await ipc.call(CONNECT_CHANNELS.autoUse, { on: 1 });
    await ipc.call(CONNECT_CHANNELS.autoUse, undefined);
    assert.deepStrictEqual(seen, [["use", { remember: false }], ["use", { remember: true }], ["autoUse", { on: false }], ["autoUse", { on: false }]]);
  });

  it("opens a window of its own, never a sheet on another: a sheet would queue behind the one already there", () => {
    const made = [];
    class Win {
      constructor(options) {
        this.options = options;
        made.push(this);
      }
      setMenu() {}
      loadFile() {}
      on() {}
      isDestroyed() {
        return false;
      }
    }
    const dialog = makeConnectDialog({ BrowserWindowCtor: Win, ipcMain: fakeIpc(), handlers: {} });
    dialog.open({ any: "parent" });
    assert.equal(made.length, 1);
    assert.equal(made[0].options.modal, false);
    assert.equal("parent" in made[0].options, false, "a parent makes it that window's child");
  });

  it("opens the chooser as a small window of its own, on the dialog's preload, and closes both on a gateway", () => {
    const made = [];
    class Win {
      constructor(options) {
        this.options = options;
        this.closed = false;
        made.push(this);
      }
      setMenu() {}
      loadFile(file) {
        this.file = file;
      }
      on() {}
      close() {
        this.closed = true;
      }
      isDestroyed() {
        return this.closed;
      }
    }
    const dialog = makeConnectDialog({ BrowserWindowCtor: Win, ipcMain: fakeIpc(), handlers: {} });
    const chooser = dialog.openChooser();
    assert.equal(chooser.options.title, "Choose a Gateway");
    assert.equal(chooser.options.modal, false);
    assert.equal("parent" in chooser.options, false);
    assert.equal(chooser.options.resizable, false);
    assert.ok(chooser.options.width < 560 && chooser.options.height < 600, "the chooser is the Gateways window's size");
    assert.match(chooser.file, /gatewayChooser\.html$/);
    assert.match(chooser.options.webPreferences.preload, /connectPreload\.js$/);
    assert.equal(chooser.options.webPreferences.contextIsolation, true);
    assert.equal(chooser.options.webPreferences.nodeIntegration, false);
    const gateways = dialog.open();
    dialog.close();
    assert.equal(chooser.closed, true);
    assert.equal(gateways.closed, true);
  });

  it("tells an open window its list changed", () => {
    const sent = [];
    class Win {
      constructor() {
        this.webContents = { send: (ch) => sent.push(ch) };
      }
      setMenu() {}
      loadFile() {}
      on() {}
      isDestroyed() {
        return false;
      }
    }
    const dialog = makeConnectDialog({ BrowserWindowCtor: Win, ipcMain: fakeIpc(), handlers: {} });
    dialog.changed();
    assert.deepEqual(sent, [], "no window, nothing to tell");
    dialog.open();
    dialog.changed();
    assert.deepEqual(sent, [CONNECT_CHANNELS.changed]);
    dialog.openChooser();
    dialog.changed();
    assert.deepEqual(sent, [CONNECT_CHANNELS.changed, CONNECT_CHANNELS.changed, CONNECT_CHANNELS.changed], "the chooser is told too");
  });

  it("coerces its arguments, so a renderer cannot hand a handler an object where an id belongs", async () => {
    const seen = [];
    const ipc = fakeIpc();
    const handlers = {
      list: async () => ({}),
      prepare: async (input) => seen.push(["prepare", input]),
      confirm: async (arg) => seen.push(["confirm", arg]),
      switchTo: async (id) => seen.push(["switch", id]),
      forget: async (id) => seen.push(["forget", id]),
      refresh: async () => ({}),
    };
    makeConnectDialog({ BrowserWindowCtor: class {}, ipcMain: ipc, handlers }).registerIpc(ipc);
    await ipc.call(CONNECT_CHANNELS.switch, { id: { evil: true } });
    await ipc.call(CONNECT_CHANNELS.forget, undefined);
    await ipc.call(CONNECT_CHANNELS.prepare, { input: 42 });
    await ipc.call(CONNECT_CHANNELS.confirm, { input: null, label: ["x"] });
    for (const [, arg] of seen) {
      if (typeof arg === "object" && arg !== null) {
        for (const v of Object.values(arg)) assert.strictEqual(typeof v, "string");
      } else {
        assert.strictEqual(typeof arg, "string");
      }
    }
  });

  it("answers a shaped refusal when a handler throws, instead of returning a main-process stack", async () => {
    const ipc = fakeIpc();
    const logged = [];
    makeConnectDialog({
      BrowserWindowCtor: class {},
      ipcMain: ipc,
      handlers: {
        list: async () => {
          throw new Error("/Users/someone/secret/path exploded");
        },
      },
      log: (m) => logged.push(m),
    }).registerIpc(ipc);
    const out = await ipc.call(CONNECT_CHANNELS.list, {});
    assert.deepStrictEqual(out, { ok: false, code: "INTERNAL", message: "That did not work. See the app log." });
    assert.strictEqual(logged.length, 1, "the real error must still reach the log");
    assert.match(logged[0], /exploded/);
  });
});

describe("the dialog document", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "connectDialog.html"), "utf8");

  it("offers the local row's action in place of Switch, through the start invoke, and reads the list again when told", () => {
    assert.match(html, /if \(row\.localAction\) \{[\s\S]*?window\.pclawConnect\.start\(\)/);
    assert.match(html, /else if \(!row\.active && row\.switchable !== false\)/, "Switch is offered for a row with nothing behind it");
    assert.match(html, /window\.pclawConnect\.onChanged\(\(\) => load\(\)\)/);
  });

  it("builds every row with textContent and never with innerHTML", () => {
    // A registry label or URL is exactly the string a tampered store controls, so the document must
    // have no HTML-string sink at all.
    for (const sink of ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"]) {
      assert.strictEqual(html.includes(sink), false, `connectDialog.html uses ${sink}`);
    }
    assert.ok(html.includes("textContent"));
  });

  it("carries a CSP that denies this window a network of its own", () => {
    // Read the policy out of the meta tag rather than scanning the whole file: the document's own
    // comment mentions `connect-src` to explain its absence, and a file-wide grep cannot tell an
    // explanation from a directive.
    const meta = html.match(/http-equiv="Content-Security-Policy"[\s\S]*?content="([^"]+)"/);
    assert.ok(meta, "connectDialog.html has no CSP meta tag");
    const policy = meta[1];
    assert.match(policy, /default-src 'none'/);
    assert.strictEqual(/connect-src/.test(policy), false, "the switcher must not be able to fetch");
    assert.match(policy, /form-action 'none'/);
    assert.match(policy, /base-uri 'none'/);
  });

  it("is listed in electron-builder's files, or the packaged app opens a blank switcher", () => {
    const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"));
    for (const f of ["connectDialog.html", "gatewayChooser.html", "connectPreload.js", "connectDialog.js", "connectMode.js", "gatewayUrl.js", "endpointRegistry.js", "shellStore.js"]) {
      assert.ok(pkg.build.files.includes(f), `build.files is missing ${f}`);
    }
  });

  it("offers Use It through the use invoke, and Ask Each Time through autoUse", () => {
    assert.match(html, /const using = row\.localAction\.id === 'use';[\s\S]*?window\.pclawConnect\.use\(false\)/);
    assert.match(html, /if \(row\.localOption\) \{[\s\S]*?window\.pclawConnect\.autoUse\(false\)/);
  });
});

describe("the chooser document", () => {
  const html = fs.readFileSync(path.join(__dirname, "..", "gatewayChooser.html"), "utf8");

  it("shows the running gateway's offer and its box only when the chooser offers it, and the box starts off", () => {
    assert.match(html, /byId\('use'\)\.hidden = !chooser\.use;/);
    assert.match(html, /byId\('remember-row'\)\.hidden = !chooser\.use;/);
    const box = html.match(/<input id="remember"[^>]*>/);
    assert.ok(box, "the box is gone");
    assert.doesNotMatch(box[0], /checked/, "Use it automatically next time is ticked before anyone ticks it");
    assert.match(html, /window\.pclawConnect\.use\(byId\('remember'\)\.checked\)/);
    // The box's row is a flex row: without this, its display rule shows it while it is hidden.
    assert.match(html, /\[hidden\] \{ display: none !important; \}/);
  });

  it("offers Add a Gateway and Try Again through their invokes, and closes once a gateway serves", () => {
    assert.match(html, /window\.pclawConnect\.openGateways\(\)/);
    assert.match(html, /window\.pclawConnect\.start\(\)/);
    assert.match(html, /if \(chooser\.settled\) \{\s*window\.close\(\);/);
    assert.match(html, /window\.pclawConnect\.onChanged\(\(\) => load\(\)\)/);
  });

  it("sets every value with textContent, and carries the Gateways window's policy", () => {
    for (const sink of ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"]) {
      assert.strictEqual(html.includes(sink), false, `gatewayChooser.html uses ${sink}`);
    }
    assert.ok(html.includes("textContent"));
    const policy = html.match(/http-equiv="Content-Security-Policy"[\s\S]*?content="([^"]+)"/)[1];
    const dialogPolicy = fs.readFileSync(path.join(__dirname, "..", "connectDialog.html"), "utf8").match(/http-equiv="Content-Security-Policy"[\s\S]*?content="([^"]+)"/)[1];
    assert.equal(policy, dialogPolicy);
  });
});
