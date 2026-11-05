/**
 * Following a gateway the app found (`adoptedWatch.js`), against fakes: the home's runtime record,
 * the processes that are alive, and `homeGateway.reach`'s answer are each the test's, and the timers
 * are stepped by hand.
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const { makeAdoptedWatch } = require("../adoptedWatch");

const HOME = "/home/someone/.personalclaw";

function watched({ record = { pid: 200, port: 5000 }, alive = [200], proof = { ok: false, reason: "not_gateway" }, follows = false } = {}) {
  const timers = [];
  const said = { proven: [], gone: [], followed: 0, reached: 0 };
  const world = { record, alive: new Set(alive), proof };
  const w = makeAdoptedWatch({
    home: HOME,
    reach: async (home) => {
      assert.equal(home, HOME);
      said.reached += 1;
      return world.proof;
    },
    readRecord: () => world.record,
    pidIsAlive: (pid) => world.alive.has(pid),
    follow: async () => {
      said.followed += 1;
      return follows;
    },
    proven: (gateway) => said.proven.push(gateway),
    gone: (gateway) => said.gone.push(gateway),
    setTimer: (fn, ms) => (timers.push({ fn, ms }), timers.length),
    clearTimer: () => {},
  });
  /** Run every timer that is due, as the clock moving on does. */
  const tick = async (times = 1) => {
    for (let i = 0; i < times; i++) {
      const due = timers.splice(0);
      for (const t of due) await t.fn();
    }
  };
  return { w, said, world, timers, tick };
}

describe("adoptedWatch: nothing carries the home's credentials to an unproven port", () => {
  it("🔴 withholds them from the doorbell's loss, and a fake that answers the doorbell on that port is never registered with", async () => {
    // The gateway stopped; something else listens on its port and answers the doorbell, with no proof.
    const { w, said, tick } = watched({ record: { pid: 200, port: 5000 }, alive: [200], proof: { ok: false, reason: "not_gateway" } });
    w.adopt({ pid: 100, port: 5000 });
    assert.equal(w.withheld, false);
    w.lost();
    assert.equal(w.withheld, true, "the credentials went on after the socket was lost");
    w.up();
    await tick(5);
    assert.deepEqual(said.proven, [], "registered (the local secret sent) with an unproven gateway");
    assert.equal(w.withheld, true, "a socket that came back up let the credentials go");
    assert.ok(said.reached >= 1, "the port was never asked for its proof");
  });

  it("lets them go again only on a proof on the same port, with the pid the proof names", async () => {
    const { w, said, world, tick } = watched({ record: { pid: 300, port: 5000 }, alive: [300] });
    w.adopt({ pid: 100, port: 5000 });
    w.lost();
    await tick();
    assert.deepEqual(said.proven, []);
    world.proof = { ok: true, gateway: { pid: 300, port: 5000 } };
    await tick();
    assert.deepEqual(said.proven, [{ pid: 300, port: 5000 }]);
    assert.equal(w.withheld, false);
    assert.deepEqual(w.gateway, { pid: 300, port: 5000 });
  });

  it("does not take a proof on another port as the gateway being back where it was", async () => {
    const { w, said, tick } = watched({ record: { pid: 300, port: 5000 }, alive: [300], proof: { ok: true, gateway: { pid: 400, port: 6000 } } });
    w.adopt({ pid: 100, port: 5000 });
    w.lost();
    await tick(3);
    assert.deepEqual(said.proven, []);
    assert.equal(w.withheld, true);
  });

  it("follows a gateway whose record names a new port, and keeps looking while it cannot be found there", async () => {
    const { w, said, tick } = watched({ record: { pid: 300, port: 6000 }, alive: [300], follows: false });
    w.adopt({ pid: 100, port: 5000 });
    w.lost();
    await tick(2);
    assert.equal(said.followed, 2);
    assert.equal(w.withheld, true);
  });

  it("says the gateway is gone when no live record names it and its process has ended, and keeps the credentials withheld", async () => {
    const { w, said, world, tick } = watched({ record: null, alive: [100] });
    w.adopt({ pid: 100, port: 5000 });
    w.lost();
    await tick();
    assert.deepEqual(said.gone, [], "gone while its process still runs");
    world.alive.delete(100);
    await tick();
    assert.deepEqual(said.gone, [{ pid: 100, port: 5000 }]);
    assert.equal(w.gateway, null);
    assert.deepEqual(said.proven, []);
  });

  it("withholds nothing for a gateway the app started, and nothing once it drops a found one", () => {
    const { w } = watched();
    w.lost();
    assert.equal(w.withheld, false, "no found gateway, nothing to withhold here");
    w.adopt({ pid: 100, port: 5000 });
    w.lost();
    w.drop();
    assert.equal(w.withheld, false);
  });
});
