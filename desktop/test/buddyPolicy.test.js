const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { ENABLED_KEY } = require("../buddyWindow");
const { cachedPolicy, keepPolicy, readPolicy, showWrite } = require("../buddyPolicy");

// ── Buddy status, as the shell follows it (#3896, #3901) ──────────────────────────────────────
//
// The gateway's `buddy.show` is the one switch, served with its surfaces at `GET /api/buddy/policy`.
// The shell keeps the last answer for a start with no gateway, and the menu bar writes the gateway's.

function memoryStore(initial = {}) {
  const data = { ...initial };
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } };
}

const POLICY = {
  show: true,
  surfaces: { window: true, tray: true, dock: false, notifications: true },
  away_after_secs: 180,
  notifications: { muted: false, quiet_now: false, may_interrupt: true, changes_at: null },
};

describe("the policy the shell reads", () => {
  it("is the gateway's answer, each surface as the gateway says", () => {
    assert.deepEqual(readPolicy(POLICY), { show: true, surfaces: { window: true, tray: true, dock: false, notifications: true }, cached: false });
  });

  it("has every surface off while show is off, whatever the surfaces say", () => {
    const off = readPolicy({ ...POLICY, show: false });
    assert.deepEqual(Object.values(off.surfaces), [false, false, false, false]);
  });

  it("reads a surface it is not told of as off, and refuses what is not a policy", () => {
    assert.deepEqual(readPolicy({ show: true }).surfaces, { window: false, tray: false, dock: false, notifications: false });
    assert.equal(readPolicy({ show: true, surfaces: { window: "yes" } }).surfaces.window, false);
    for (const raw of [null, undefined, "", [], {}, { show: "true" }, { detail: "Not Found" }]) assert.equal(readPolicy(raw), null, JSON.stringify(raw));
  });
});

describe("what the shell keeps", () => {
  it("keeps whether the window was on, and a start with no gateway uses it for every surface", () => {
    const store = memoryStore();
    assert.equal(cachedPolicy(store).show, false, "off until the owner turns it on");
    keepPolicy(store, readPolicy(POLICY));
    assert.equal(store.data[ENABLED_KEY], "1");
    assert.deepEqual(cachedPolicy(store), { show: true, surfaces: { window: true, tray: true, dock: true, notifications: true }, cached: true });
    keepPolicy(store, readPolicy({ ...POLICY, surfaces: { ...POLICY.surfaces, window: false } }));
    assert.equal(store.data[ENABLED_KEY], "0", "the window's surface off: no window at the next start");
    assert.equal(cachedPolicy(null).show, false);
  });
});

describe("the menu bar's Show Buddy", () => {
  it("writes the gateway's one path, as Settings does", () => {
    assert.deepEqual(showWrite(true), { path: "buddy.show", value: true });
    assert.deepEqual(showWrite("yes"), { path: "buddy.show", value: false });
  });

  it("is wired so: the policy read on each ring, the write a PATCH, the kept value only with no gateway", () => {
    const main = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
    assert.match(main, /getGateway\("\/api\/buddy\/policy"\),/, "read beside the lists, on the doorbell's rings");
    assert.match(main, /followBuddyPolicy\(readPolicy\(policy\)\);/);
    assert.match(main, /postGateway\("\/api\/config\/personalclaw", showWrite\(on\), \{\}, \{ method: "PATCH" \}\)/);
    assert.match(main, /toggleBuddy: \(next\) => \{\n\s+setBuddyStatus\(next\)/);
  });
});
