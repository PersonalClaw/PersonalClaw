/**
 * The desktop registry is a PORT of `web/src/lib/endpoints.ts`, so this file's job is to prove the
 * port has not drifted (`CA-8`).
 *
 * 🔑 THIS IS A DIFFERENTIAL, NOT A TEXT SCAN. Node ≥22 strips types from a `.ts` on import, so the
 * TypeScript module is imported and EXECUTED here, and every shared function is run over one vector
 * table with both implementations required to agree. `tests/test_mobile_shell.py` compares the two
 * sides' spellings, which is all it can do from Python; a spelling comparison passes happily when
 * two implementations agree on names and disagree on behaviour — and a behavioural divergence in
 * `endpointKey` is precisely how one gateway's state ends up in another's slot.
 *
 * If the import of the `.ts` ever fails (a Node without type stripping, or `endpoints.ts` growing
 * non-erasable syntax), the FIRST test fails loudly instead of the file quietly skipping. A parity
 * rail that can silently stop comparing is worse than no rail.
 */

const { describe, it, before } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const cjs = require("../endpointRegistry");

const TS_PATH = path.resolve(__dirname, "..", "..", "web", "src", "lib", "endpoints.ts");

/** A `KeyValueStore` over a plain object — the five members `endpoints.ts` declares. */
function fakeStore(initial = {}) {
  const data = { ...initial };
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
    _data: data,
  };
}

let ts = null;

describe("parity with web/src/lib/endpoints.ts", () => {
  before(async () => {
    // A bare `import()` of a `.ts` file: Node's type stripping makes this the real module, not a
    // transpiled copy this test maintains.
    ts = await import(TS_PATH);
    assert.ok(ts, "could not import endpoints.ts");
  });

  it("imports the TypeScript contract for real, so the comparison below is not vacuous", () => {
    assert.strictEqual(typeof ts.endpointKey, "function");
    assert.strictEqual(typeof ts.normalizeRegistry, "function");
    assert.strictEqual(typeof ts.endpointSocketUrl, "function");
  });

  it("agrees on the storage key and the zero value", () => {
    assert.strictEqual(cjs.REGISTRY_STORAGE_KEY, ts.REGISTRY_STORAGE_KEY);
    assert.strictEqual(cjs.ENDPOINT_KEY_PREFIX, ts.ENDPOINT_KEY_PREFIX);
    assert.strictEqual(cjs.WS_PATH, ts.WS_PATH);
    assert.deepStrictEqual({ ...cjs.EMPTY_REGISTRY }, { ...ts.EMPTY_REGISTRY });
  });

  it("agrees on endpointKey for every id/key pair, including ids containing the separator", () => {
    const ids = ["a", "a:b", "", "ep_abc123", "10:", "::", "ep:3:x"];
    const keys = ["k", "b:c", "", "connect.confirmed", "a:b:c:d"];
    for (const id of ids) {
      for (const k of keys) {
        assert.strictEqual(cjs.endpointKey(id, k), ts.endpointKey(id, k), `${JSON.stringify([id, k])}`);
      }
    }
  });

  it("agrees on parseEndpointKey — including the round trip and the refusals", () => {
    const ids = ["a", "a:b", "ep_abc123", "::"];
    for (const id of ids) {
      for (const k of ["k", "b:c", "connect.confirmed"]) {
        const encoded = cjs.endpointKey(id, k);
        assert.deepStrictEqual(cjs.parseEndpointKey(encoded), ts.parseEndpointKey(encoded));
        assert.deepStrictEqual(cjs.parseEndpointKey(encoded), { id, logicalKey: k });
      }
    }
    for (const bad of ["", "nope", "ep:", "ep::x", "ep:x:y", "ep:99:short", "cache:foo", "ep:1:a"]) {
      assert.deepStrictEqual(cjs.parseEndpointKey(bad), ts.parseEndpointKey(bad), bad);
    }
  });

  it("agrees on normalizeRegistry over corrupt, partial and hostile inputs", () => {
    const vectors = [
      undefined,
      null,
      0,
      "",
      [],
      {},
      { active: "x" },
      { endpoints: "not an array" },
      { endpoints: [null, 3, "x", {}, { id: "" }] },
      { active: "b", endpoints: [{ id: "a" }, { id: "b" }] },
      { active: "gone", endpoints: [{ id: "a" }] },
      { active: "a", endpoints: [{ id: "a", label: 5, base_url: {}, kind: "weird", device_session_ref: 1 }] },
      { active: "a", endpoints: [{ id: "a", kind: "local" }, { id: "a", kind: "remote", label: "dupe" }] },
      { active: "b", endpoints: [{ id: "a" }, { id: "b" }, { id: "a", label: "second a" }] },
    ];
    for (const v of vectors) {
      assert.deepStrictEqual(cjs.normalizeRegistry(v), ts.normalizeRegistry(v), JSON.stringify(v));
    }
  });

  it("agrees on parseRegistry for malformed JSON and for the empty cases", () => {
    for (const raw of [null, undefined, "", "not json", "[]", "3", '"x"', '{"active":"a","endpoints":[{"id":"a"}]}']) {
      assert.deepStrictEqual(cjs.parseRegistry(raw), ts.parseRegistry(raw), String(raw));
    }
  });

  it("agrees on the reducers, including the invariant that `active` never dangles", () => {
    let a = cjs.normalizeRegistry({});
    let b = ts.normalizeRegistry({});
    const row = (id, kind) => ({ id, label: id, base_url: `http://${id}.local`, kind, device_session_ref: "" });
    for (const step of [row("one", "local"), row("two", "remote"), row("three", "remote")]) {
      a = cjs.addEndpoint(a, step);
      b = ts.addEndpoint(b, step);
      assert.deepStrictEqual(a, b);
    }
    // Re-adding replaces in place and keeps position.
    a = cjs.addEndpoint(a, { ...row("two", "remote"), label: "renamed" });
    b = ts.addEndpoint(b, { ...row("two", "remote"), label: "renamed" });
    assert.deepStrictEqual(a, b);
    for (const id of ["two", "nope", "one"]) {
      a = cjs.removeEndpoint(a, id);
      b = ts.removeEndpoint(b, id);
      assert.deepStrictEqual(a, b, `remove ${id}`);
    }
    for (const id of ["three", "ghost"]) {
      a = cjs.setActive(a, id);
      b = ts.setActive(b, id);
      assert.deepStrictEqual(a, b, `setActive ${id}`);
    }
    assert.deepStrictEqual(cjs.activeEndpoint(a), ts.activeEndpoint(b));
  });

  it("agrees on endpointSocketUrl, refusals included", () => {
    const vectors = [
      "https://pc.example.com",
      "http://claw.local:10000",
      "http://10.0.0.4:10000/ignored/path",
      "claw.local:10000",
      "",
      "   ",
      "file:///x",
      "ws://claw.local",
      "https://[::1]:8443",
    ];
    for (const v of vectors) {
      assert.strictEqual(cjs.endpointSocketUrl(v), ts.endpointSocketUrl(v), v);
    }
    assert.strictEqual(cjs.endpointSocketUrl("https://x.example", "/api/other"), ts.endpointSocketUrl("https://x.example", "/api/other"));
    assert.strictEqual(cjs.endpointSocketUrl("https://x.example", "api/other"), ts.endpointSocketUrl("https://x.example", "api/other"));
    assert.strictEqual(cjs.endpointSocket(undefined), ts.endpointSocket(undefined));
  });

  it("agrees on endpointScope and clearEndpointState over one shared store", () => {
    const seed = {
      [cjs.endpointKey("a", "x")]: "1",
      [cjs.endpointKey("a", "y:z")]: "2",
      [cjs.endpointKey("a:b", "z")]: "3",
      "companion:endpoints": "{}",
      "unrelated": "keep",
    };
    const s1 = fakeStore(seed);
    const s2 = fakeStore(seed);
    assert.deepStrictEqual(cjs.endpointScope(s1, "a").logicalKeys().sort(), ts.endpointScope(s2, "a").logicalKeys().sort());
    cjs.clearEndpointState(s1, "a");
    ts.clearEndpointState(s2, "a");
    assert.deepStrictEqual(s1._data, s2._data);
    // The neighbour whose id merely SHARES a prefix survives — the length field is what makes that
    // true, and it is the whole reason the encoding is not `id + ':' + key`.
    assert.strictEqual(s1.getItem(cjs.endpointKey("a:b", "z")), "3");
    assert.strictEqual(s1.getItem("unrelated"), "keep");
  });

  it("mints ids in the same shape", () => {
    const rand = () => 0.5;
    assert.strictEqual(cjs.newEndpointId(rand), ts.newEndpointId(rand));
    assert.match(cjs.newEndpointId(), /^ep_[a-z0-9]{12}$/);
  });

  it("declares the same field vocabularies the TS interfaces do", () => {
    // Text-level, because interfaces are erased at import time and cannot be reflected on.
    const src = require("node:fs").readFileSync(TS_PATH, "utf8");
    const iface = (name) => {
      const m = src.match(new RegExp(`export interface ${name} \\{([\\s\\S]*?)\\n\\}`));
      assert.ok(m, `endpoints.ts no longer declares interface ${name}`);
      return [...m[1].matchAll(/^\s{2}([a-z_]+)\??:/gm)].map((x) => x[1]);
    };
    assert.deepStrictEqual([...cjs.ENDPOINT_FIELDS].sort(), iface("CompanionEndpoint").sort());
    assert.deepStrictEqual([...cjs.REGISTRY_FIELDS].sort(), iface("EndpointRegistry").sort());
  });
});

describe("the length-prefixed key is injective where the naive form is not", () => {
  it("keeps {id:'a', key:'b:c'} and {id:'a:b', key:'c'} in different slots", () => {
    // The naive `id + ':' + key` renders both as `a:b:c`. That collision IS the state bleed the
    // whole namespacing mechanism exists to prevent, so it gets its own test rather than being
    // implied by the parity vectors.
    const one = cjs.endpointKey("a", "b:c");
    const two = cjs.endpointKey("a:b", "c");
    assert.notStrictEqual(one, two);
    assert.deepStrictEqual(cjs.parseEndpointKey(one), { id: "a", logicalKey: "b:c" });
    assert.deepStrictEqual(cjs.parseEndpointKey(two), { id: "a:b", logicalKey: "c" });
  });
});

describe("loadRegistry / saveRegistry", () => {
  it("survives a storage scope that throws on read", () => {
    const throwing = {
      getItem: () => {
        throw new Error("storage disabled");
      },
      setItem: () => {},
      removeItem: () => {},
      length: 0,
      key: () => null,
    };
    assert.deepStrictEqual(cjs.loadRegistry(throwing), { active: "", endpoints: [] });
  });

  it("round-trips through a store", () => {
    const store = fakeStore();
    const reg = cjs.addEndpoint({ active: "", endpoints: [] }, { id: "ep_x", label: "L", base_url: "http://x.local", kind: "remote", device_session_ref: "" });
    cjs.saveRegistry(store, reg);
    assert.deepStrictEqual(cjs.loadRegistry(store), reg);
  });
});
