/**
 * Whether the owner is away, and the owner's hide (#3832): the idle time against the policy's
 * absence time, a locked screen at once, and a hide kept in the store that ends on its own.
 */

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { CHECK_MS, DEFAULT_AWAY_SECS, HIDDEN_KEY, makeAbsence, makeBuddyHide, nextMidnight } = require("../buddyAway");

function clock(start = Date.UTC(2026, 9, 22, 21, 30)) {
  let now = start;
  let seq = 0;
  const timers = new Map();
  return {
    now: () => now,
    setTimer: (fire, ms) => { const id = ++seq; timers.set(id, { at: now + ms, fire }); return id; },
    clearTimer: (id) => timers.delete(id),
    pending: () => timers.size,
    advance(ms) {
      const until = now + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        now = due[1].at;
        due[1].fire();
      }
      now = until;
    },
  };
}

const memoryStore = () => {
  const data = {};
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } };
};

describe("away", () => {
  test("is the idle time reaching the policy's absence time, and back when it drops", () => {
    const c = clock();
    let idle = 0;
    const said = [];
    const a = makeAbsence({ idleSeconds: () => idle, awayAfterSecs: () => 60, onChange: (v) => said.push(v), setTimer: c.setTimer, clearTimer: c.clearTimer });
    a.start();
    idle = 59;
    c.advance(CHECK_MS);
    assert.deepEqual(said, []);
    idle = 60;
    c.advance(CHECK_MS);
    assert.deepEqual(said, [true]);
    idle = 2;
    c.advance(CHECK_MS);
    assert.deepEqual(said, [true, false]);
  });

  test("uses the default absence time until the policy is read, and for a value that is no time", () => {
    const c = clock();
    for (const secs of [undefined, null, 0, -5, "soon"]) {
      const said = [];
      const a = makeAbsence({ idleSeconds: () => DEFAULT_AWAY_SECS, awayAfterSecs: () => secs, onChange: (v) => said.push(v), setTimer: c.setTimer, clearTimer: c.clearTimer });
      a.start();
      assert.deepEqual(said, [true], String(secs));
      a.stop();
    }
  });

  test("a locked screen or sleep is away at once, and unlocking reads the idle time again", () => {
    const c = clock();
    const said = [];
    const a = makeAbsence({ idleSeconds: () => 0, onChange: (v) => said.push(v), setTimer: c.setTimer, clearTimer: c.clearTimer });
    a.start();
    a.lock();
    assert.equal(a.away, true);
    a.unlock();
    assert.deepEqual(said, [true, false]);
  });

  test("stopped, it holds no timer", () => {
    const c = clock();
    const a = makeAbsence({ idleSeconds: () => 0, onChange: () => {}, setTimer: c.setTimer, clearTimer: c.clearTimer });
    a.start();
    a.stop();
    assert.equal(c.pending(), 0);
  });
});

describe("the owner's hide", () => {
  test("for an hour ends on its own an hour later", () => {
    const c = clock();
    const store = memoryStore();
    const said = [];
    const h = makeBuddyHide({ store, now: c.now, setTimer: c.setTimer, clearTimer: c.clearTimer, onChange: (v) => said.push(v) });
    h.hideForAnHour();
    assert.equal(h.hidden, true);
    c.advance(59 * 60 * 1000);
    assert.equal(h.hidden, true);
    c.advance(60 * 1000);
    assert.equal(h.hidden, false);
    assert.deepEqual(said, [true, false]);
    assert.equal(store.data[HIDDEN_KEY], "0");
  });

  test("until tomorrow ends at the next local midnight", () => {
    const c = clock();
    const h = makeBuddyHide({ store: memoryStore(), now: c.now, setTimer: c.setTimer, clearTimer: c.clearTimer });
    h.hideUntilTomorrow();
    const midnight = nextMidnight(c.now());
    assert.equal(h.until, midnight);
    const d = new Date(midnight);
    assert.deepEqual([d.getHours(), d.getMinutes()], [0, 0]);
    assert.ok(midnight > c.now() && midnight - c.now() <= 24 * 60 * 60 * 1000);
  });

  test("outlasts a restart of the app, and a hide already over is none", () => {
    const c = clock();
    const store = memoryStore();
    makeBuddyHide({ store, now: c.now, setTimer: c.setTimer, clearTimer: c.clearTimer }).hideForAnHour();
    const again = makeBuddyHide({ store, now: c.now, setTimer: c.setTimer, clearTimer: c.clearTimer });
    again.start();
    assert.equal(again.hidden, true, "the next run picks it up");
    store.data[HIDDEN_KEY] = String(c.now() - 1);
    assert.equal(again.hidden, false);
    store.data[HIDDEN_KEY] = "not a time";
    assert.equal(again.hidden, false);
  });

  test("Show Buddy now ends it early and says so once", () => {
    const c = clock();
    const said = [];
    const h = makeBuddyHide({ store: memoryStore(), now: c.now, setTimer: c.setTimer, clearTimer: c.clearTimer, onChange: (v) => said.push(v) });
    h.hideUntilTomorrow();
    h.showNow();
    h.showNow();
    assert.deepEqual(said, [true, false]);
    assert.equal(c.pending(), 0);
  });
});
