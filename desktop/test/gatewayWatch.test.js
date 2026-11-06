/**
 * Following the gateway the app uses (`gatewayWatch.js`), the one it found and its own, against
 * fakes: the home's runtime record,
 * the processes that are alive, and `homeGateway.reach`'s answer are each the test's, and the timers
 * are stepped by hand.
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const { makeGatewayWatch } = require("../gatewayWatch");

const HOME = "/home/someone/.personalclaw";

function watched({ record = { pid: 200, port: 5000 }, alive = [200], proof = { ok: false, reason: "not_gateway" }, follows = false, takes = true, log = () => {} } = {}) {
  const timers = [];
  const said = { proven: [], provenWas: [], back: [], gone: [], followed: 0, reached: 0, withheldAtBack: [] };
  const world = { record, alive: new Set(alive), proof, takes, afterProof: null };
  const w = makeGatewayWatch({
    home: HOME,
    reach: async (home) => {
      assert.equal(home, HOME);
      said.reached += 1;
      // What the record says once the proof is in, when the test moves it under the read.
      if (world.afterProof) world.record = world.afterProof;
      return world.proof;
    },
    readRecord: () => world.record,
    pidIsAlive: (pid) => world.alive.has(pid),
    follow: async () => {
      said.followed += 1;
      if (world.onFollow) await world.onFollow();
      return follows;
    },
    proven: async (was, gateway) => {
      said.proven.push({ pid: gateway.pid, port: gateway.port });
      said.provenWas.push({ ...was });
      return world.takes;
    },
    back: (gateway) => {
      said.back.push(gateway.kind);
      said.withheldAtBack.push(w.withheld);
    },
    gone: (gateway) => said.gone.push(gateway),
    log,
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

describe("gatewayWatch, a gateway the app found: nothing carries the home's credentials to an unproven port", () => {
  it("🔴 withholds them from the doorbell's loss, and a port that answers with no proof is never registered with", async () => {
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

  it("withholds nothing before a gateway is watched, and nothing once it drops one", () => {
    const { w } = watched();
    w.lost();
    assert.equal(w.withheld, false, "no gateway watched, nothing to withhold here");
    w.adopt({ pid: 100, port: 5000 });
    w.lost();
    w.drop();
    assert.equal(w.withheld, false);
  });

  it("keeps looking, withheld, until the sign-in at the proven gateway takes, and hands over only then", async () => {
    const { w, said, world, tick } = watched({ record: { pid: 300, port: 5000 }, alive: [300], proof: { ok: true, gateway: { pid: 300, port: 5000 } }, takes: false });
    w.adopt({ pid: 100, port: 5000 });
    w.lost();
    await tick(2);
    assert.equal(said.proven.length, 2, "a failed rotation is tried again");
    assert.equal(w.withheld, true);
    assert.deepEqual(said.back, []);
    world.takes = true;
    await tick();
    assert.equal(w.withheld, false);
    assert.deepEqual(said.back, ["adopted"]);
    assert.deepEqual(said.withheldAtBack, [false], "the registration and the doorbell come after the withhold lifts");
  });
});

/** A proof from the app's own gateway: the child's pid, its port, and the digest of its start's secret. */
const SAME_START = { ok: true, gateway: { pid: 700, port: 5100, secretDigest: "digest-of-this-start" } };
const START = { pid: 700, port: 5100, secretDigest: "digest-of-this-start" };

describe("gatewayWatch, the app's own gateway: only the same start lets the credentials go again", () => {
  it("takes the same start back: its pid, its port, the record unmoved after the proof, and its secret", async () => {
    const { w, said, tick } = watched({ record: { pid: 700, port: 5100 }, alive: [700], proof: SAME_START });
    w.own(START);
    w.lost();
    assert.equal(w.withheld, true);
    await tick();
    assert.deepEqual(said.proven, [{ pid: 700, port: 5100 }]);
    assert.equal(w.withheld, false);
    assert.deepEqual(said.back, ["own"]);
  });

  it("keeps withholding for a new start on the same port: another secret", async () => {
    const { w, said, tick } = watched({ record: { pid: 700, port: 5100 }, alive: [700], proof: { ok: true, gateway: { ...SAME_START.gateway, secretDigest: "a-new-start" } } });
    w.own(START);
    w.lost();
    await tick(3);
    assert.deepEqual(said.proven, []);
    assert.equal(w.withheld, true);
  });

  it("keeps withholding when the proof names another process than the child", async () => {
    const { w, said, tick } = watched({ record: { pid: 700, port: 5100 }, alive: [700], proof: { ok: true, gateway: { ...SAME_START.gateway, pid: 701 } } });
    w.own(START);
    w.lost();
    await tick(2);
    assert.deepEqual(said.proven, []);
  });

  it("keeps withholding when the record moves under the proof", async () => {
    const { w, said, world, tick } = watched({ record: { pid: 700, port: 5100 }, alive: [700, 800], proof: SAME_START });
    world.afterProof = { pid: 800, port: 5100 };
    w.own(START);
    w.lost();
    await tick();
    assert.deepEqual(said.proven, []);
    assert.equal(w.withheld, true);
  });

  it("waits for the ready line when the record names another port, and never follows it", async () => {
    const { w, said, tick } = watched({ record: { pid: 700, port: 6200 }, alive: [700], proof: SAME_START });
    w.own(START);
    w.lost();
    await tick(3);
    assert.equal(said.followed, 0);
    assert.equal(said.reached, 0);
    assert.equal(w.withheld, true);
  });

  it("never says the app's own gateway is gone: its exit does", async () => {
    const { w, said, tick } = watched({ record: null, alive: [] });
    w.own(START);
    w.lost();
    await tick(3);
    assert.deepEqual(said.gone, []);
    assert.equal(w.withheld, true);
  });
});

describe("gatewayWatch: the start a lift is keyed to, and an End", () => {
  it("hands on the new start's pid and digest after a same-port restart, so its next loss proves the same start", async () => {
    const proof = { ok: true, gateway: { pid: 301, port: 5000, secretDigest: "start-2" } };
    const { w, said, tick } = watched({ record: { pid: 301, port: 5000 }, alive: [301], proof });
    w.adopt({ pid: 300, port: 5000, secretDigest: "start-1" });
    w.lost();
    await tick();
    assert.equal(said.provenWas.at(-1).secretDigest, "start-1");
    w.lost();
    await tick();
    assert.deepEqual(said.provenWas.at(-1), { kind: "adopted", pid: 301, port: 5000, secretDigest: "start-2" }, "the next loss was compared with the first start");
  });

  it("after an End, a found gateway's same start lifts nothing; a new start on its port goes ahead, and its stop is gone", async () => {
    const { w, said, world, tick } = watched({ record: { pid: 300, port: 5000 }, alive: [300], proof: { ok: true, gateway: { pid: 300, port: 5000, secretDigest: "start-1" } } });
    w.adopt({ pid: 300, port: 5000, secretDigest: "start-1" });
    w.ended();
    assert.equal(w.withheld, false, "an End made the gateway unproven");
    await tick(3);
    assert.ok(said.reached >= 3, "the look stopped after the End");
    assert.deepEqual(said.proven, [], "the ended start was lifted");
    world.record = { pid: 302, port: 5000 };
    world.alive = new Set([302]);
    world.proof = { ok: true, gateway: { pid: 302, port: 5000, secretDigest: "start-3" } };
    await tick();
    assert.deepEqual(said.proven, [{ pid: 302, port: 5000 }], "the next start was not heard");
    assert.deepEqual(said.back, ["adopted"]);
    assert.equal(w.withheld, false);
    world.record = null;
    world.alive = new Set();
    w.lost();
    await tick();
    assert.deepEqual(said.gone, [{ pid: 302, port: 5000 }]);
  });

  it("logs a proof once a start, however often the take-back can't act on it, and none for a start an End ended", async () => {
    const logged = [];
    const proof = { ok: true, gateway: { pid: 300, port: 5000, secretDigest: "start-1" } };
    const { w, world, tick } = watched({ record: { pid: 300, port: 5000 }, alive: [300], proof, takes: false, log: (msg) => logged.push(msg) });
    w.adopt({ pid: 300, port: 5000, secretDigest: "start-1" });
    w.lost();
    await tick(3);
    assert.equal(logged.length, 1, logged.join("\n"));
    world.proof = { ok: true, gateway: { pid: 301, port: 5000, secretDigest: "start-2" } };
    await tick(2);
    assert.equal(logged.length, 2, "a new start's proof was not logged");
    const ended = [];
    const e = watched({ record: { pid: 300, port: 5000 }, alive: [300], proof, log: (msg) => ended.push(msg) });
    e.w.adopt({ pid: 300, port: 5000, secretDigest: "start-1" });
    e.w.ended();
    await e.tick(3);
    assert.ok(e.said.reached >= 3, "the look stopped after the End: this case is blind");
    assert.deepEqual(ended, [], "a proof of the start an End ended was logged");
  });

  it("looks again once a follow hands over, when a loss during the hand-over turned another look away", async () => {
    const { w, said, world, tick } = watched({ record: { pid: 301, port: 5001 }, alive: [300, 301], follows: true });
    w.adopt({ pid: 300, port: 5000, secretDigest: "start-1" });
    let release = null;
    world.onFollow = async () => {
      // main's follow arms the watch on the moved start, and a loss arrives while its sign-outs are out.
      w.adopt({ pid: 301, port: 5001, secretDigest: "start-2" });
      w.lost();
      await new Promise((resolve) => (release = resolve));
    };
    w.lost();
    const running = tick();
    while (!release) await new Promise((r) => setImmediate(r));
    await tick(); // the loss's look, turned away while the follow's look runs
    const reached = said.reached;
    release();
    await running;
    await tick();
    assert.ok(said.reached > reached, "no look ran after the hand-over: the watch is withheld with nothing looking");
    assert.equal(w.withheld, true);
  });

  it("after an End of the app's own gateway, nothing is looked at or lifted, until its next ready line", async () => {
    const { w, said, timers, tick } = watched({ record: { pid: 700, port: 51234 }, alive: [700], proof: { ok: true, gateway: { pid: 700, port: 51234, secretDigest: "start-1" } } });
    w.own({ pid: 700, port: 51234, secretDigest: "start-1" });
    w.ended();
    await tick(3);
    assert.equal(said.reached, 0, "the own gateway was looked at after an End: its ready line and exit settle it");
    assert.equal(timers.length, 0, "an ended own start's look looked again");
    assert.deepEqual(said.proven, []);
    w.own({ pid: 700, port: 51234, secretDigest: "start-2" });
    await tick(2);
    assert.equal(said.reached, 0, "the look went on past the next ready line");
  });
});
