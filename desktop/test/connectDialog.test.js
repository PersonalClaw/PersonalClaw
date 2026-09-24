/**
 * The switcher's display model and its IPC adaptor (`CA-8` / T4.4).
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

const { CONNECT_CHANNELS, healthCopy, describeRow, describeList, makeConnectDialog } = require("../connectDialog");
const {
  LOCAL_ENDPOINT_ID,
  HEALTH_STATES,
  HEALTH_UNKNOWN,
  HEALTH_REACHABLE,
  HEALTH_UNREACHABLE,
  HEALTH_NEEDS_PAIRING,
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

  it("marks needs_pairing as the ONE state that asks the user to do something", () => {
    const actionable = HEALTH_STATES.filter((s) => healthCopy(s).action === "pair");
    assert.deepStrictEqual(actionable, [HEALTH_NEEDS_PAIRING]);
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
    assert.strictEqual(list.rows.length, 1);
    assert.strictEqual(list.storeSafe, false);
    assert.strictEqual(list.readOnly, true);
    assert.strictEqual(list.warnings[0].code, "store_permissions");
  });

  it("handles a registry with no rows at all", () => {
    const list = describeList({ registry: { active: "", endpoints: [] } });
    assert.deepStrictEqual(list.rows, []);
    assert.strictEqual(list.activeId, "");
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

  it("registers exactly the six channels the preload exposes", () => {
    const ipc = fakeIpc();
    makeConnectDialog({ BrowserWindowCtor: class {}, ipcMain: ipc, handlers: {} }).registerIpc(ipc);
    assert.deepStrictEqual(ipc.names().sort(), Object.values(CONNECT_CHANNELS).sort());
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
    for (const f of ["connectDialog.html", "connectPreload.js", "connectDialog.js", "connectMode.js", "gatewayUrl.js", "endpointRegistry.js", "shellStore.js"]) {
      assert.ok(pkg.build.files.includes(f), `build.files is missing ${f}`);
    }
  });
});
