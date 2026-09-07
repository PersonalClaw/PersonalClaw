/**
 * Connect-mode's decisions, against real files and real sockets (`CA-8`).
 *
 * 🔑 THE RECONNECT LEG KILLS A REAL LISTENER. `probeEndpoint` is driven against an `http.Server`
 * bound on loopback, which is then genuinely torn down (`closeAllConnections()` + `close()`), probed
 * again, and rebound on the same port. A reconnect test that hands the prober a fake returning
 * `{status:'unreachable'}` proves that the fake works; only an actual `ECONNREFUSED` from an actual
 * closed port proves the shell notices a gateway going away and comes back when it returns.
 *
 * 🔑 THE STORE LEG WRITES REAL FILES. `absent`, `empty`, `unparseable` and `unreadable` are four
 * separate facts with four separate behaviours, so each one is produced on disk (a missing file, a
 * zero-length file, a file of garbage, a file with its read bit removed) rather than stubbed.
 */

const { describe, it, before, after } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const {
  LOCAL_ENDPOINT_ID,
  HEALTH_REACHABLE,
  HEALTH_UNREACHABLE,
  HEALTH_NEEDS_PAIRING,
  HEALTH_NOT_A_GATEWAY,
  HEALTH_REDIRECTED,
  HEALTH_HTTP_ERROR,
  HEALTH_UNKNOWN,
  HEALTH_REFUSED_BY_POLICY,
  MAX_RECONNECT_ATTEMPTS,
  BACKOFF_BASE_MS,
  BACKOFF_CEILING,
  assertLoopbackTarget,
  shouldAttachBridge,
  describeStartup,
  prepareEndpoint,
  confirmEndpoint,
  rememberLocalGateway,
  forgetEndpoint,
  switchTo,
  probeEndpoint,
  probeAll,
  nextReconnectStep,
  readConfirmation,
  isRetryable,
  currentFingerprintFor,
  sanitizeLabel,
  adoptGatewayLabel,
  LABEL_MAX,
} = require("../connectMode");

const { openShellStore, readStoreFile, inspect, storePath, STORE_ABSENT, STORE_EMPTY, STORE_UNPARSEABLE, STORE_UNREADABLE, STORE_OK } = require("../shellStore");
const { loadRegistry, endpointScope, endpointKey } = require("../endpointRegistry");

// ── helpers ────────────────────────────────────────────────────────────────────────────────────

let tmpRoots = [];
function tmpHome() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ca8-"));
  tmpRoots.push(dir);
  return dir;
}
after(() => {
  for (const dir of tmpRoots) {
    try {
      fs.rmSync(dir, { recursive: true, force: true });
    } catch {
      /* a temp dir left behind is not a test failure */
    }
  }
  tmpRoots = [];
});

/**
 * Write a store fixture the way the shell would: 0700 directory, 0600 file.
 *
 * A hand-written fixture at the default 0644 is world-READABLE, and the permission rail correctly
 * refuses to trust rows in it — which made three tests here fail for the right reason and the wrong
 * subject. Keeping the fixture faithful is what lets those tests measure what they name.
 */
function seedStore(file, body) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  fs.writeFileSync(file, body, { mode: 0o600 });
  fs.chmodSync(file, 0o600);
  fs.chmodSync(path.dirname(file), 0o700);
}

/** A real loopback HTTP server. `handler(req,res)` decides what it is pretending to be. */
function listen(handler, port = 0) {
  return new Promise((resolve, reject) => {
    const server = http.createServer(handler);
    server.on("error", reject);
    server.listen(port, "127.0.0.1", () => {
      const addr = server.address();
      resolve({ server, port: addr.port, origin: `http://127.0.0.1:${addr.port}` });
    });
  });
}

/** Take a listener down for real, not just stop accepting. */
function kill({ server }) {
  return new Promise((resolve) => {
    server.closeAllConnections?.();
    server.close(() => resolve());
  });
}

const gatewayHandler = (version = "9.9.9") => (req, res) => {
  if (req.url === "/api/healthz") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ status: "ok", version }));
    return;
  }
  res.writeHead(404).end();
};

const lookupOf = (map) => async (host) => {
  if (!Object.prototype.hasOwnProperty.call(map, host)) throw Object.assign(new Error("nf"), { code: "ENOTFOUND" });
  return map[host].map((address) => ({ address, family: address.includes(":") ? 6 : 4 }));
};

// ── the credential rails ───────────────────────────────────────────────────────────────────────

describe("assertLoopbackTarget — the rail that keeps .local_secret on this machine", () => {
  it("passes for the shapes the spawned gateway actually takes", () => {
    for (const url of ["http://localhost:10000", "http://127.0.0.1:53211", "http://[::1]:9000"]) {
      assert.strictEqual(assertLoopbackTarget(url), true, url);
    }
  });

  it("throws for a LAN host, a public host, and a name that merely resolves to loopback", () => {
    for (const url of ["http://10.0.0.4:10000", "https://pc.example.com", "http://claw.local:10000", "http://lh.0x41.pw"]) {
      assert.throws(() => assertLoopbackTarget(url, "capability manifest"), /non-loopback target/, url);
    }
  });

  it("throws for an empty or unparseable target rather than treating it as harmless", () => {
    assert.throws(() => assertLoopbackTarget(""), /non-loopback target/);
    assert.throws(() => assertLoopbackTarget("not a url"), /non-loopback target/);
  });
});

describe("shouldAttachBridge — the capability bridge is loopback-only", () => {
  it("attaches for the spawned gateway", () => {
    assert.strictEqual(shouldAttachBridge("http://127.0.0.1:10000"), true);
    assert.strictEqual(shouldAttachBridge("http://localhost:10000"), true);
  });

  it("does NOT attach for a LAN or remote gateway", () => {
    for (const url of ["http://10.0.0.4:10000", "http://claw.local:10000", "https://pc.example.com", "http://brain.ts.net"]) {
      assert.strictEqual(shouldAttachBridge(url), false, url);
    }
  });

  it("does NOT attach for a name that resolves to loopback — a spelling must not earn a microphone", () => {
    assert.strictEqual(shouldAttachBridge("http://lh.0x41.pw"), false);
  });

  it("does not attach to nothing", () => {
    assert.strictEqual(shouldAttachBridge(""), false);
    assert.strictEqual(shouldAttachBridge(null), false);
  });
});

// ── the store: four facts, four behaviours ─────────────────────────────────────────────────────

describe("shellStore — absent, empty, unparseable and unreadable are four different facts", () => {
  it("reports ABSENT for a file that is not there", () => {
    const file = storePath(tmpHome());
    const r = readStoreFile(file);
    assert.strictEqual(r.status, STORE_ABSENT);
    assert.strictEqual(r.reason, "no_file");
  });

  it("reports EMPTY for a real file with nothing in it", () => {
    const home = tmpHome();
    const file = storePath(home);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, "   \n");
    const r = readStoreFile(file);
    assert.strictEqual(r.status, STORE_EMPTY);
    assert.strictEqual(r.reason, "zero_length");
  });

  it("reports UNPARSEABLE for garbage, for a JSON array, and for non-string values", () => {
    for (const [body, reason] of [["{not json", "invalid_json"], ["[1,2,3]", "not_an_object"], ['{"k":{"nested":1}}', "non_string_values"]]) {
      const file = storePath(tmpHome());
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, body);
      const r = readStoreFile(file);
      assert.strictEqual(r.status, STORE_UNPARSEABLE, body);
      assert.strictEqual(r.reason, reason, body);
    }
  });

  it("reports UNREADABLE — distinct from unparseable, because the content may be fine", (t) => {
    if (typeof process.getuid === "function" && process.getuid() === 0) {
      t.skip("root reads anything, so this case cannot be produced");
      return;
    }
    const file = storePath(tmpHome());
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, '{"companion:endpoints":"{}"}');
    fs.chmodSync(file, 0o000);
    const r = readStoreFile(file);
    assert.strictEqual(r.status, STORE_UNREADABLE);
    assert.strictEqual(r.reason, "EACCES");
    fs.chmodSync(file, 0o600);
  });

  it("reports OK and hands back the values", () => {
    const file = storePath(tmpHome());
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, '{"companion:endpoints":"{\\"active\\":\\"\\",\\"endpoints\\":[]}"}');
    const r = readStoreFile(file);
    assert.strictEqual(r.status, STORE_OK);
    assert.strictEqual(typeof r.data["companion:endpoints"], "string");
  });

  it("writes owner-only at 0600 inside a 0700 directory, and says so", () => {
    const home = tmpHome();
    const store = openShellStore({ home });
    store.setItem("companion:endpoints", '{"active":"","endpoints":[]}');
    const perms = inspect(store.file);
    assert.strictEqual(perms.exists, true);
    assert.strictEqual(perms.ownerOnly, true, `mode was ${perms.mode?.toString(8)}`);
    assert.strictEqual(perms.safe, true, perms.reason);
    assert.strictEqual(fs.statSync(store.file).mode & 0o777, 0o600);
  });

  it("notices a store another account can read or write", () => {
    const home = tmpHome();
    const store = openShellStore({ home });
    store.setItem("k", "v");
    fs.chmodSync(store.file, 0o666);
    const perms = inspect(store.file);
    assert.strictEqual(perms.ownerOnly, false);
    assert.strictEqual(perms.safe, false);
    assert.strictEqual(perms.reason, "readable_or_writable_by_others");
  });

  it("survives an unwritable store by degrading to in-memory rather than throwing", (t) => {
    if (typeof process.getuid === "function" && process.getuid() === 0) {
      t.skip("root writes anywhere");
      return;
    }
    const home = tmpHome();
    const store = openShellStore({ home });
    store.setItem("k", "v");
    fs.chmodSync(path.dirname(store.file), 0o500);
    store.setItem("k2", "v2");
    assert.strictEqual(store.readOnly, true);
    assert.strictEqual(store.getItem("k2"), "v2", "the value is still usable this session");
    fs.chmodSync(path.dirname(store.file), 0o700);
  });

  it("leaves a corrupt store on disk instead of overwriting the only copy", () => {
    const home = tmpHome();
    const file = storePath(home);
    seedStore(file, "{corrupt");
    const store = openShellStore({ home });
    assert.strictEqual(store.status, STORE_UNPARSEABLE);
    assert.strictEqual(fs.readFileSync(file, "utf8"), "{corrupt");
  });
});

// ── startup: spawn-local is the default, and every fallback says which reason ───────────────────

describe("describeStartup — spawn-local unless connect is fully justified", () => {
  const confirmedStore = (origin, { trust = "private", scheme = "http:", addresses = "" } = {}) => {
    const store = openShellStore({ home: tmpHome() });
    const plan = { origin, trust, scheme, host: new URL(origin).host, fingerprint: addresses };
    confirmEndpoint(store, plan, { label: "Work brain" });
    return store;
  };

  it("distinguishes NO SAVED ENDPOINTS from an EMPTY registry — absent is not the same as declared-empty", () => {
    const fresh = openShellStore({ home: tmpHome() });
    assert.strictEqual(fresh.status, STORE_ABSENT);
    assert.strictEqual(describeStartup({ store: fresh }).reason, "no_saved_endpoints");

    const home = tmpHome();
    const file = storePath(home);
    seedStore(file, '{"companion:endpoints":"{\\"active\\":\\"\\",\\"endpoints\\":[]}"}');
    const declared = openShellStore({ home });
    assert.strictEqual(declared.status, STORE_OK);
    assert.strictEqual(describeStartup({ store: declared }).reason, "registry_empty");
  });

  it("falls back with a WARNING when the store is unparseable, and does not connect", () => {
    const home = tmpHome();
    const file = storePath(home);
    seedStore(file, "}}}");
    const startup = describeStartup({ store: openShellStore({ home }) });
    assert.strictEqual(startup.mode, "spawn-local");
    assert.strictEqual(startup.reason, "store_unparseable");
    assert.strictEqual(startup.warnings[0].code, "store_unparseable");
    assert.match(startup.warnings[0].message, /nothing\s+was overwritten/);
  });

  it("refuses to trust rows in a store other accounts can write", () => {
    const store = confirmedStore("http://10.0.0.4:10000");
    fs.chmodSync(store.file, 0o666);
    const reopened = openShellStore({ home: path.dirname(path.dirname(store.file)) });
    const startup = describeStartup({ store: reopened });
    assert.strictEqual(startup.mode, "spawn-local");
    assert.strictEqual(startup.reason, "store_not_owner_only");
    assert.strictEqual(startup.warnings[0].code, "store_permissions");
  });

  it("connects when the active row is confirmed and its policy allows it", () => {
    const store = confirmedStore("http://10.0.0.4:10000");
    const startup = describeStartup({ store });
    assert.strictEqual(startup.mode, "connect");
    assert.strictEqual(startup.origin, "http://10.0.0.4:10000");
    assert.strictEqual(startup.reason, "confirmed_active_endpoint");
  });

  it("stays spawn-local when the active row is the local one", () => {
    const store = openShellStore({ home: tmpHome() });
    rememberLocalGateway(store, "http://localhost:51234");
    assert.strictEqual(describeStartup({ store }).reason, "active_is_local");
  });

  it("refuses a confirmed row whose URL no longer passes policy — no grandfathering", () => {
    // A row confirmed while it was plaintext-on-the-LAN, then hand-edited to a plaintext PUBLIC
    // host. The confirmation record must not carry the old verdict forward.
    const store = openShellStore({ home: tmpHome() });
    confirmEndpoint(store, { origin: "http://93.184.216.34:10000", trust: "private", scheme: "http:", host: "93.184.216.34", fingerprint: "" }, { label: "x" });
    const startup = describeStartup({ store });
    assert.strictEqual(startup.mode, "spawn-local");
    assert.strictEqual(startup.reason, "active_endpoint_refused_by_policy");
  });

  it("treats an UNCONFIRMED row, an UNPARSEABLE confirmation and a MOVED host as three reasons", () => {
    // (a) never confirmed: write the row without a confirmation record.
    const a = openShellStore({ home: tmpHome() });
    const { saveRegistry, addEndpoint } = require("../endpointRegistry");
    saveRegistry(a, addEndpoint({ active: "", endpoints: [] }, { id: "ep_x", label: "x", base_url: "http://10.0.0.4:10000", kind: "remote", device_session_ref: "" }));
    let s = describeStartup({ store: a });
    assert.strictEqual(s.reason, "active_endpoint_unconfirmed");
    assert.strictEqual(s.warnings[0].detail, "unconfirmed:absent");

    // (b) a confirmation record that will not parse — not the same as never having confirmed.
    endpointScope(a, "ep_x").set("connect.confirmed", "{not json");
    s = describeStartup({ store: a });
    assert.strictEqual(s.warnings[0].detail, "unconfirmed:unparseable");

    // (c) confirmed, but the name now resolves elsewhere.
    const c = openShellStore({ home: tmpHome() });
    confirmEndpoint(c, { origin: "http://claw.local:10000", trust: "private", scheme: "http:", host: "claw.local", fingerprint: "10.0.0.4" }, { label: "Home" });
    assert.strictEqual(describeStartup({ store: c, currentFingerprint: "10.0.0.4" }).mode, "connect");
    s = describeStartup({ store: c, currentFingerprint: "10.0.0.99" });
    assert.strictEqual(s.mode, "spawn-local");
    assert.strictEqual(s.reason, "active_endpoint_unconfirmed");
    assert.strictEqual(s.warnings[0].detail, "host_moved");
    assert.match(s.warnings[0].message, /different machine/);
  });

  it("distinguishes a row with NO url from a row with an unusable one", () => {
    const { saveRegistry, addEndpoint } = require("../endpointRegistry");
    const none = openShellStore({ home: tmpHome() });
    saveRegistry(none, addEndpoint({ active: "", endpoints: [] }, { id: "ep_a", label: "a", base_url: "", kind: "remote", device_session_ref: "" }));
    assert.strictEqual(describeStartup({ store: none }).reason, "active_endpoint_has_no_url");

    const bad = openShellStore({ home: tmpHome() });
    saveRegistry(bad, addEndpoint({ active: "", endpoints: [] }, { id: "ep_b", label: "b", base_url: "ftp://x/", kind: "remote", device_session_ref: "" }));
    assert.strictEqual(describeStartup({ store: bad }).reason, "active_endpoint_unparseable");
  });

  it("does not let the local row steal the active pointer on a relaunch", () => {
    const store = confirmedStore("http://10.0.0.4:10000");
    const before = loadRegistry(store).active;
    rememberLocalGateway(store, "http://localhost:51999");
    assert.strictEqual(loadRegistry(store).active, before);
    assert.strictEqual(describeStartup({ store }).mode, "connect");
  });
});

// ── adding an endpoint ─────────────────────────────────────────────────────────────────────────

describe("prepareEndpoint — validate and classify BEFORE anything is dialed", () => {
  it("reads a pairing link into an origin, a code and a pair target", async () => {
    const plan = await prepareEndpoint("http://claw.local:10000/pair?code=ABCD-2345", { lookup: lookupOf({ "claw.local": ["10.0.0.4"] }) });
    assert.ok(plan.ok, plan.code);
    assert.strictEqual(plan.origin, "http://claw.local:10000");
    assert.strictEqual(plan.pairingCode, "ABCD-2345");
    assert.strictEqual(plan.navigateTo, "http://claw.local:10000/pair?code=ABCD-2345");
    assert.strictEqual(plan.trust, "private");
    assert.strictEqual(plan.policy.requiresConfirmation, true);
  });

  it("navigates to the bare origin when no code came with the input", async () => {
    const plan = await prepareEndpoint("https://pc.example.com", { lookup: lookupOf({ "pc.example.com": ["93.184.216.34"] }) });
    assert.ok(plan.ok, plan.code);
    assert.strictEqual(plan.navigateTo, "https://pc.example.com");
    assert.strictEqual(plan.trust, "public");
  });

  it("REFUSES plaintext to a public host", async () => {
    const plan = await prepareEndpoint("http://pc.example.com", { lookup: lookupOf({ "pc.example.com": ["93.184.216.34"] }) });
    assert.strictEqual(plan.ok, false);
    assert.strictEqual(plan.code, "PLAINTEXT_PUBLIC_REFUSED");
  });

  it("floors a name that resolves to loopback at `private`, so it cannot earn the bridge", async () => {
    const plan = await prepareEndpoint("http://lh.0x41.pw", { lookup: lookupOf({ "lh.0x41.pw": ["127.0.0.1"] }) });
    assert.ok(plan.ok, plan.code);
    assert.strictEqual(plan.resolvedTrust, "loopback");
    assert.strictEqual(plan.trust, "private");
    assert.strictEqual(plan.policy.requiresConfirmation, true);
  });

  it("refuses a name that resolves into the metadata range", async () => {
    const plan = await prepareEndpoint("http://mt.0x41.pw", { lookup: lookupOf({ "mt.0x41.pw": ["169.254.169.254"] }) });
    assert.strictEqual(plan.ok, false);
    assert.match(plan.code, /RESOLUTION:resolves_to_refused_range/);
  });

  it("reports empty input as EMPTY, not as an invalid address", async () => {
    assert.strictEqual((await prepareEndpoint("")).code, "EMPTY");
    assert.strictEqual((await prepareEndpoint("   ")).code, "EMPTY");
  });
});

describe("confirmEndpoint + switchTo", () => {
  it("records the confirmation in the endpoint's OWN namespace, not as a registry field", () => {
    const store = openShellStore({ home: tmpHome() });
    const { id } = confirmEndpoint(store, { origin: "http://10.0.0.4:10000", trust: "private", scheme: "http:", host: "10.0.0.4", fingerprint: "10.0.0.4" }, { label: "Work" });
    // The registry row carries exactly the five contract fields and nothing else.
    const row = loadRegistry(store).endpoints.find((e) => e.id === id);
    assert.deepStrictEqual(Object.keys(row).sort(), ["base_url", "device_session_ref", "id", "kind", "label"]);
    // The confirmation lives under the namespaced key.
    assert.ok(store.getItem(endpointKey(id, "connect.confirmed")));
    const rec = readConfirmation(store, id);
    assert.strictEqual(rec.present, true);
    assert.strictEqual(rec.origin, "http://10.0.0.4:10000");
  });

  it("leaves device_session_ref EMPTY — the shell cannot know the nonce it names", () => {
    const store = openShellStore({ home: tmpHome() });
    const { id } = confirmEndpoint(store, { origin: "http://10.0.0.4:10000", trust: "private", scheme: "http:", host: "10.0.0.4", fingerprint: "" }, {});
    assert.strictEqual(loadRegistry(store).endpoints.find((e) => e.id === id).device_session_ref, "");
  });

  it("reuses the row for an origin already paired instead of accumulating duplicates", () => {
    const store = openShellStore({ home: tmpHome() });
    const plan = { origin: "http://10.0.0.4:10000", trust: "private", scheme: "http:", host: "10.0.0.4", fingerprint: "" };
    const first = confirmEndpoint(store, plan, { label: "Work" });
    const second = confirmEndpoint(store, plan, { label: "Work renamed" });
    assert.strictEqual(first.id, second.id);
    assert.strictEqual(loadRegistry(store).endpoints.filter((e) => e.id !== LOCAL_ENDPOINT_ID).length, 1);
  });

  it("switches to a confirmed row and reports the bridge decision", () => {
    const store = openShellStore({ home: tmpHome() });
    rememberLocalGateway(store, "http://localhost:51234");
    const { id } = confirmEndpoint(store, { origin: "http://10.0.0.4:10000", trust: "private", scheme: "http:", host: "10.0.0.4", fingerprint: "" }, { label: "Work" });

    const toLocal = switchTo(store, LOCAL_ENDPOINT_ID, { localBaseUrl: "http://localhost:51234" });
    assert.ok(toLocal.ok);
    assert.strictEqual(toLocal.attachBridge, true);

    const toRemote = switchTo(store, id);
    assert.ok(toRemote.ok);
    assert.strictEqual(toRemote.navigateTo, "http://10.0.0.4:10000");
    assert.strictEqual(toRemote.attachBridge, false);
    assert.strictEqual(loadRegistry(store).active, id);
  });

  it("REFUSES to switch to a row whose confirmation no longer holds, rather than re-confirming silently", () => {
    const store = openShellStore({ home: tmpHome() });
    const { id } = confirmEndpoint(store, { origin: "http://claw.local:10000", trust: "private", scheme: "http:", host: "claw.local", fingerprint: "10.0.0.4" }, { label: "Home" });
    const result = switchTo(store, id, { currentFingerprint: "10.0.0.99" });
    assert.strictEqual(result.ok, false);
    assert.strictEqual(result.needsConfirmation, true);
    assert.match(result.reason, /needs_confirmation:host_moved/);
  });

  it("refuses an unknown id and refuses to remove the local row", () => {
    const store = openShellStore({ home: tmpHome() });
    rememberLocalGateway(store, "http://localhost:1");
    assert.strictEqual(switchTo(store, "ep_nope").ok, false);
    assert.strictEqual(forgetEndpoint(store, LOCAL_ENDPOINT_ID).reason, "local_endpoint_is_not_removable");
    assert.strictEqual(forgetEndpoint(store, "ep_nope").reason, "unknown_endpoint");
  });

  it("forgetting one endpoint sweeps ITS namespaced state and nothing else", () => {
    const store = openShellStore({ home: tmpHome() });
    const work = confirmEndpoint(store, { origin: "http://10.0.0.4:10000", trust: "private", scheme: "http:", host: "10.0.0.4", fingerprint: "" }, { label: "Work" }).id;
    const home = confirmEndpoint(store, { origin: "http://10.0.0.5:10000", trust: "private", scheme: "http:", host: "10.0.0.5", fingerprint: "" }, { label: "Home" }).id;
    endpointScope(store, work).set("last.route", "#/chat");
    endpointScope(store, home).set("last.route", "#/inbox");

    forgetEndpoint(store, work);

    assert.strictEqual(loadRegistry(store).endpoints.some((e) => e.id === work), false);
    assert.strictEqual(endpointScope(store, work).logicalKeys().length, 0);
    assert.deepStrictEqual(endpointScope(store, home).logicalKeys().sort(), ["connect.confirmed", "connect.labelSource", "last.route"]);
    assert.strictEqual(readConfirmation(store, home).present, true);
  });
});

describe("currentFingerprintFor - the guard's only source of a current answer", () => {
  it("fingerprints what a name resolves to right now", async () => {
    const fp = await currentFingerprintFor("http://claw.local:10000", { lookup: lookupOf({ "claw.local": ["10.0.0.4"] }) });
    assert.strictEqual(fp, "10.0.0.4");
  });

  it("is order-insensitive, so a resolver rotating its answers is not a host move", async () => {
    const a = await currentFingerprintFor("http://x.local", { lookup: lookupOf({ "x.local": ["10.0.0.1", "10.0.0.2"] }) });
    const b = await currentFingerprintFor("http://x.local", { lookup: lookupOf({ "x.local": ["10.0.0.2", "10.0.0.1"] }) });
    assert.strictEqual(a, b);
  });

  it("fingerprints an IP literal as ITSELF, so the check is a trivial match rather than a skip", async () => {
    // Measured, not guessed: `resolveHostTrust` answers with the literal for an IP, so the
    // fingerprint recorded at confirm time and the one computed later are the same string. A change
    // to the literal is caught by the origin comparison instead, so nothing is lost.
    assert.strictEqual(await currentFingerprintFor("http://10.0.0.4:10000"), "10.0.0.4");
    const store = openShellStore({ home: tmpHome() });
    const plan = await prepareEndpoint("http://10.0.0.4:10000");
    confirmEndpoint(store, plan, {});
    const now = await currentFingerprintFor("http://10.0.0.4:10000");
    assert.strictEqual(describeStartup({ store, currentFingerprint: now }).mode, "connect");
  });

  it("answers empty on a FAILED lookup, so a DNS outage is not reported as a moved host", async () => {
    // The distinction matters: an empty fingerprint makes `confirmationHolds` skip the comparison,
    // so the endpoint stays usable. Treating a lookup failure as a move would demand a
    // re-confirmation the user cannot give while their resolver is down.
    assert.strictEqual(await currentFingerprintFor("http://gone.example.com", { lookup: lookupOf({}) }), "");
  });

  it("feeds a confirmation that then HOLDS, and a moved one that does not", async () => {
    const store = openShellStore({ home: tmpHome() });
    const lookup = lookupOf({ "claw.local": ["10.0.0.4"] });
    const plan = await prepareEndpoint("http://claw.local:10000", { lookup });
    const { id } = confirmEndpoint(store, plan, { label: "Home" });
    const same = await currentFingerprintFor("http://claw.local:10000", { lookup });
    assert.strictEqual(describeStartup({ store, currentFingerprint: same }).mode, "connect");
    const moved = await currentFingerprintFor("http://claw.local:10000", { lookup: lookupOf({ "claw.local": ["10.0.0.99"] }) });
    const s = describeStartup({ store, currentFingerprint: moved });
    assert.strictEqual(s.mode, "spawn-local");
    assert.strictEqual(s.warnings[0].detail, "host_moved");
    assert.strictEqual(switchTo(store, id, { currentFingerprint: moved }).needsConfirmation, true);
  });
});

describe("labels — the gateway may name itself, but only over a name the SHELL guessed", () => {
  const plan = (origin) => ({ origin, trust: "private", scheme: "http:", host: new URL(origin).host, fingerprint: "" });

  it("adopts companion.instance_name over a hostname the shell filled in", () => {
    const store = openShellStore({ home: tmpHome() });
    const { id } = confirmEndpoint(store, plan("http://10.0.0.4:10000"), { label: "" });
    assert.strictEqual(loadRegistry(store).endpoints.find((e) => e.id === id).label, "10.0.0.4:10000");
    const r = adoptGatewayLabel(store, id, "Living room Mac");
    assert.strictEqual(r.changed, true);
    assert.strictEqual(loadRegistry(store).endpoints.find((e) => e.id === id).label, "Living room Mac");
  });

  it("does NOT overwrite a name the user typed", () => {
    const store = openShellStore({ home: tmpHome() });
    const { id } = confirmEndpoint(store, plan("http://10.0.0.4:10000"), { label: "Work brain" });
    const r = adoptGatewayLabel(store, id, "Living room Mac");
    assert.strictEqual(r.changed, false);
    assert.strictEqual(r.reason, "user_named_it");
    assert.strictEqual(loadRegistry(store).endpoints.find((e) => e.id === id).label, "Work brain");
  });

  it("does not steal the active pointer while renaming a row", () => {
    const store = openShellStore({ home: tmpHome() });
    const first = confirmEndpoint(store, plan("http://10.0.0.4:10000"), {}).id;
    const second = confirmEndpoint(store, plan("http://10.0.0.5:10000"), {}).id;
    assert.strictEqual(loadRegistry(store).active, second);
    adoptGatewayLabel(store, first, "Renamed");
    assert.strictEqual(loadRegistry(store).active, second);
  });

  it("sanitises an untrusted name: control characters out, whitespace collapsed, length clamped", () => {
    assert.strictEqual(sanitizeLabel("  Living   room\tMac \n"), "Living room Mac");
    assert.strictEqual(sanitizeLabel("bad name[31m"), "badname[31m");
    assert.strictEqual(sanitizeLabel("x".repeat(500)).length, LABEL_MAX);
    assert.strictEqual(sanitizeLabel(""), "");
    assert.strictEqual(sanitizeLabel(null), "");
    assert.strictEqual(sanitizeLabel(undefined), "");
    assert.strictEqual(sanitizeLabel("   "), "");
  });

  it("treats an empty or unusable name as nothing to adopt, and says which", () => {
    const store = openShellStore({ home: tmpHome() });
    const { id } = confirmEndpoint(store, plan("http://10.0.0.4:10000"), {});
    assert.strictEqual(adoptGatewayLabel(store, id, "").reason, "empty_name");
    assert.strictEqual(adoptGatewayLabel(store, id, "   ").reason, "empty_name");
    assert.strictEqual(adoptGatewayLabel(store, "ep_ghost", "Name").reason, "unknown_endpoint");
    adoptGatewayLabel(store, id, "Same");
    assert.strictEqual(adoptGatewayLabel(store, id, "Same").reason, "already_current");
  });
});

// ── probing a real server ──────────────────────────────────────────────────────────────────────

describe("probeEndpoint — against real listeners, carrying no credential", () => {
  it("recognises a real PersonalClaw healthz and reads its version", async () => {
    const s = await listen(gatewayHandler("1.2.3"));
    try {
      const r = await probeEndpoint(s.origin);
      assert.strictEqual(r.status, HEALTH_REACHABLE);
      assert.strictEqual(r.version, "1.2.3");
    } finally {
      await kill(s);
    }
  });

  it("sends NO cookie, NO Authorization and NO X-Local-Secret", async () => {
    const seen = [];
    const s = await listen((req, res) => {
      seen.push({ ...req.headers });
      gatewayHandler()(req, res);
    });
    try {
      await probeEndpoint(s.origin);
      assert.strictEqual(seen.length, 1);
      const h = seen[0];
      for (const forbidden of ["cookie", "authorization", "x-local-secret", "x-shell-token"]) {
        assert.strictEqual(h[forbidden], undefined, `probe sent ${forbidden}`);
      }
    } finally {
      await kill(s);
    }
  });

  it("reads a 401 and a 403 as NEEDS_PAIRING, which is what a revoked device session looks like", async () => {
    for (const code of [401, 403]) {
      const s = await listen((req, res) => res.writeHead(code).end("nope"));
      try {
        const r = await probeEndpoint(s.origin);
        assert.strictEqual(r.status, HEALTH_NEEDS_PAIRING, String(code));
        assert.strictEqual(r.httpStatus, code);
      } finally {
        await kill(s);
      }
    }
  });

  it("does NOT follow a redirect — it reports one", async () => {
    let followed = 0;
    const s = await listen((req, res) => {
      if (req.url === "/api/healthz") {
        res.writeHead(302, { Location: "http://169.254.169.254/latest/meta-data/" }).end();
        return;
      }
      followed += 1;
      res.writeHead(200).end();
    });
    try {
      const r = await probeEndpoint(s.origin);
      assert.strictEqual(r.status, HEALTH_REDIRECTED);
      assert.strictEqual(followed, 0, "the probe followed a redirect");
    } finally {
      await kill(s);
    }
  });

  it("tells a 200 from a web server apart from a 200 from a gateway", async () => {
    for (const body of ["<html>hello</html>", '{"status":"fine"}', '{"status":"ok"}', "null"]) {
      const s = await listen((req, res) => {
        res.writeHead(200, { "Content-Type": "application/json" }).end(body);
      });
      try {
        assert.strictEqual((await probeEndpoint(s.origin)).status, HEALTH_NOT_A_GATEWAY, body);
      } finally {
        await kill(s);
      }
    }
  });

  it("reports an unrelated HTTP error as its own state", async () => {
    const s = await listen((req, res) => res.writeHead(503).end("busy"));
    try {
      assert.strictEqual((await probeEndpoint(s.origin)).status, HEALTH_HTTP_ERROR);
    } finally {
      await kill(s);
    }
  });

  it("refuses by policy before opening a socket at all", async () => {
    let hit = 0;
    const s = await listen((req, res) => {
      hit += 1;
      gatewayHandler()(req, res);
    });
    try {
      // A plaintext PUBLIC address is refused by `transportPolicy`, so nothing is dialed. Using a
      // real listener here is the point: the assertion is that it was never contacted.
      const r = await probeEndpoint("http://93.184.216.34:1/");
      assert.strictEqual(r.status, HEALTH_REFUSED_BY_POLICY);
      assert.strictEqual(hit, 0);
    } finally {
      await kill(s);
    }
  });

  it("does not read an unbounded body from a hostile endpoint", async () => {
    const s = await listen((req, res) => {
      res.writeHead(200, { "Content-Type": "application/json" });
      // 5 MB of junk. The probe caps its buffer, so this must resolve rather than grow.
      res.end("x".repeat(5 * 1024 * 1024));
    });
    try {
      const r = await probeEndpoint(s.origin);
      assert.strictEqual(r.status, HEALTH_NOT_A_GATEWAY);
      assert.ok(r.detail.length < 200);
    } finally {
      await kill(s);
    }
  });
});

// ── the reconnect path, against a connection that really dies ───────────────────────────────────

describe("reconnect — driven by killing a real listener", () => {
  it("goes reachable → unreachable → reachable across a genuine drop and rebind", async () => {
    const s1 = await listen(gatewayHandler("1.0.0"));
    const { port, origin } = s1;

    // 1. A live gateway.
    const before = await probeEndpoint(origin);
    assert.strictEqual(before.status, HEALTH_REACHABLE, "the fixture gateway did not answer");
    assert.strictEqual(nextReconnectStep({ status: before.status }).action, "stay");

    // 2. Kill it for real — connections aborted, listener closed, port released.
    await kill(s1);
    const during = await probeEndpoint(origin);
    assert.strictEqual(during.status, HEALTH_UNREACHABLE, `expected a dead port, got ${during.status}`);
    assert.strictEqual(during.detail, "ECONNREFUSED", "the drop was not a real TCP refusal");

    // 3. The shell schedules a bounded retry rather than giving up or re-presenting anything.
    const step = nextReconnectStep({ status: during.status, attempt: 0 });
    assert.strictEqual(step.action, "retry");
    assert.strictEqual(step.attempt, 1);
    assert.strictEqual(step.delayMs, BACKOFF_BASE_MS * 2);

    // 4. It comes back on the same port.
    const s2 = await listen(gatewayHandler("1.0.1"), port);
    try {
      const after = await probeEndpoint(origin);
      assert.strictEqual(after.status, HEALTH_REACHABLE);
      assert.strictEqual(after.version, "1.0.1", "we probed the OLD listener, not the new one");
      assert.strictEqual(nextReconnectStep({ status: after.status }).action, "stay");
    } finally {
      await kill(s2);
    }
  });

  it("drives the real backoff loop to recovery and counts the probes", async () => {
    // The whole loop, unmocked: the endpoint is down for the first two probes and up for the third.
    const s0 = await listen(gatewayHandler(), 0);
    const { port, origin } = s0;
    await kill(s0);

    let probes = 0;
    let attempt = 0;
    let revived = null;
    let action = "retry";
    while (action === "retry") {
      const probe = await probeEndpoint(origin);
      probes += 1;
      const step = nextReconnectStep({ status: probe.status, attempt });
      action = step.action;
      attempt = step.attempt;
      if (probes === 2) revived = await listen(gatewayHandler(), port); // the gateway comes back
      if (action === "retry") await new Promise((r) => setTimeout(r, Math.min(step.delayMs, 20)));
    }
    try {
      assert.strictEqual(action, "stay", "the loop never recovered");
      assert.strictEqual(probes, 3, `expected 3 probes (down, down, up), got ${probes}`);
    } finally {
      if (revived) await kill(revived);
    }
  });

  it("a 401 gets ZERO retries — the loop makes exactly one probe and stops", async () => {
    let probes = 0;
    const s = await listen((req, res) => {
      probes += 1;
      res.writeHead(401).end();
    });
    try {
      let attempt = 0;
      let action = "retry";
      let guard = 0;
      while (action === "retry" && guard < 20) {
        guard += 1;
        const probe = await probeEndpoint(s.origin);
        const step = nextReconnectStep({ status: probe.status, attempt });
        action = step.action;
        attempt = step.attempt;
      }
      assert.strictEqual(action, "needs_pairing");
      assert.strictEqual(probes, 1, "an auth refusal was retried — this is the credential-retry loop");
      assert.strictEqual(attempt, 0, "an auth refusal incremented the retry counter");
    } finally {
      await kill(s);
    }
  });

  it("a host that answers but is not a gateway also stops dead", async () => {
    let probes = 0;
    const s = await listen((req, res) => {
      probes += 1;
      res.writeHead(200, { "Content-Type": "text/html" }).end("<h1>my router</h1>");
    });
    try {
      const probe = await probeEndpoint(s.origin);
      const step = nextReconnectStep({ status: probe.status, attempt: 0 });
      assert.strictEqual(probe.status, HEALTH_NOT_A_GATEWAY);
      assert.strictEqual(step.action, "stop");
      assert.strictEqual(probes, 1);
    } finally {
      await kill(s);
    }
  });

  it("is bounded: retries give up after MAX_RECONNECT_ATTEMPTS", () => {
    let attempt = 0;
    let action = "retry";
    let rounds = 0;
    const delays = [];
    while (action === "retry" && rounds < 100) {
      rounds += 1;
      const step = nextReconnectStep({ status: HEALTH_UNREACHABLE, attempt });
      action = step.action;
      attempt = step.attempt;
      if (action === "retry") delays.push(step.delayMs);
    }
    assert.strictEqual(action, "give_up");
    assert.strictEqual(delays.length, MAX_RECONNECT_ATTEMPTS);
    // The curve is the SPA's own: `250 * 2 ** min(attempt, 6)`.
    assert.deepStrictEqual(delays.slice(0, 3), [500, 1000, 2000]);
    assert.strictEqual(delays[delays.length - 1], BACKOFF_BASE_MS * 2 ** BACKOFF_CEILING);
  });

  it("classifies exactly three outcomes as retryable", () => {
    assert.deepStrictEqual(
      [HEALTH_REACHABLE, HEALTH_UNREACHABLE, HEALTH_NEEDS_PAIRING, HEALTH_NOT_A_GATEWAY, HEALTH_REDIRECTED, HEALTH_HTTP_ERROR, HEALTH_REFUSED_BY_POLICY].filter(isRetryable),
      [HEALTH_UNREACHABLE, HEALTH_HTTP_ERROR]
    );
    assert.strictEqual(isRetryable("timeout"), true);
  });
});

// ── revoking one gateway breaks only that entry (T4.4's acceptance bar) ─────────────────────────

describe("one gateway's revocation touches only its own row", () => {
  let work;
  let home;
  let store;
  let ids;

  before(async () => {
    // Two REAL gateways. One has had its device session revoked (it answers 401); the other is
    // healthy. Two live listeners rather than one fake, because the claim under test is that the
    // two rows are independent — and a single stubbed prober cannot be independent of itself.
    work = await listen((req, res) => res.writeHead(403).end());
    home = await listen(gatewayHandler("2.0.0"));
    store = openShellStore({ home: tmpHome() });
    const mk = (origin, label) =>
      confirmEndpoint(store, { origin, trust: "loopback", scheme: "http:", host: new URL(origin).host, fingerprint: "" }, { label }).id;
    ids = { work: mk(work.origin, "Work brain"), home: mk(home.origin, "Personal brain") };
    endpointScope(store, ids.work).set("last.route", "#/chat");
    endpointScope(store, ids.home).set("last.route", "#/inbox");
  });

  after(async () => {
    await kill(work);
    await kill(home);
  });

  it("marks the revoked row needs_pairing and leaves the other reachable", async () => {
    const health = await probeAll(loadRegistry(store));
    assert.strictEqual(health[ids.work].status, HEALTH_NEEDS_PAIRING);
    assert.strictEqual(health[ids.home].status, HEALTH_REACHABLE);
    assert.strictEqual(health[ids.home].version, "2.0.0");
  });

  it("leaves the healthy row's registry entry, confirmation and namespaced state untouched", async () => {
    await probeAll(loadRegistry(store));
    const reg = loadRegistry(store);
    assert.strictEqual(reg.endpoints.length, 2); // the two paired rows; no local row in this fixture
    assert.strictEqual(readConfirmation(store, ids.home).present, true);
    assert.strictEqual(endpointScope(store, ids.home).get("last.route"), "#/inbox");
    // And it is still switchable — one endpoint's 403 is not a reason to re-pair the other.
    assert.strictEqual(switchTo(store, ids.home).ok, true);
  });

  it("reports a row with no URL as UNKNOWN, not as unreachable", async () => {
    const { saveRegistry, addEndpoint } = require("../endpointRegistry");
    const s = openShellStore({ home: tmpHome() });
    saveRegistry(s, addEndpoint({ active: "", endpoints: [] }, { id: "ep_nourl", label: "x", base_url: "", kind: "remote", device_session_ref: "" }));
    const health = await probeAll(loadRegistry(s));
    assert.strictEqual(health.ep_nourl.status, HEALTH_UNKNOWN);
    assert.strictEqual(health.ep_nourl.detail, "no_url");
  });
});
