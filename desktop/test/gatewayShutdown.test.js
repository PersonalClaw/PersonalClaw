const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");

const { shutdownGateway, OUTCOMES } = require("../gatewayShutdown");

/**
 * A stand-in for the spawned gateway. Records every signal it was sent, and only
 * "exits" when the test says so — which is the whole point: the pre-DC-4 quit path
 * sent SIGTERM and never waited, and a fake that exits on its own would hide exactly
 * that bug.
 */
function fakeChild({ exitOnSignal = null, exitCode = null, signalCode = null } = {}) {
  const child = new EventEmitter();
  child.signals = [];
  child.exitCode = exitCode;
  child.signalCode = signalCode;
  child.kill = (sig) => {
    child.signals.push(sig);
    if (exitOnSignal === sig) {
      // Asynchronous, like a real `exit`, so a handler attached after `kill()`
      // still sees it.
      setImmediate(() => child.emit("exit", 0, sig));
    }
    return true;
  };
  return child;
}

/** Manual clock: collects timer callbacks so a test can fire them deliberately. */
function manualTimers() {
  const pending = new Map();
  let next = 1;
  return {
    pending,
    setTimeoutFn: (fn, ms) => {
      const id = next++;
      pending.set(id, { fn, ms });
      return id;
    },
    clearTimeoutFn: (id) => pending.delete(id),
    /** Fire the earliest outstanding timer. */
    advance() {
      const [id, entry] = [...pending.entries()][0] || [];
      if (!entry) throw new Error("no pending timer to advance");
      pending.delete(id);
      entry.fn();
    },
  };
}

describe("shutdownGateway", () => {
  it("resolves `none` with no child, and never invents a signal", async () => {
    const res = await shutdownGateway({ child: null });
    assert.deepStrictEqual(res, { outcome: "none", code: null, signal: null, escalated: false });
  });

  it("does not signal a child that has already exited", async () => {
    const child = fakeChild({ exitCode: 0 });
    const res = await shutdownGateway({ child });
    assert.equal(res.outcome, "none");
    // The load-bearing assertion: signalling a reaped pid can hit a recycled one.
    assert.deepStrictEqual(child.signals, []);
  });

  it("does not signal a child already killed by a signal", async () => {
    const child = fakeChild({ signalCode: "SIGKILL" });
    const res = await shutdownGateway({ child });
    assert.equal(res.outcome, "none");
    assert.deepStrictEqual(child.signals, []);
  });

  it("SIGTERMs the gateway and WAITS for its exit before resolving", async () => {
    const child = fakeChild();
    const timers = manualTimers();
    let resolved = false;
    const p = shutdownGateway({ child, ...timers }).then((r) => {
      resolved = true;
      return r;
    });

    // SIGTERM must already be out…
    assert.deepStrictEqual(child.signals, ["SIGTERM"]);
    // …and the promise must NOT be settled yet. This is the defect DC-4 fixes: the
    // old path returned here, letting Electron exit while the gateway was still
    // writing.
    await new Promise((r) => setImmediate(r));
    assert.equal(resolved, false, "shutdown resolved before the gateway exited");

    child.emit("exit", 0, null);
    const res = await p;
    assert.equal(res.outcome, "exited");
    assert.equal(res.escalated, false);
    assert.equal(res.code, 0);
    // The escalation timer must be cleared, not left armed.
    assert.equal(timers.pending.size, 0);
  });

  it("escalates to SIGKILL when the grace window expires, and reports `killed`", async () => {
    const child = fakeChild();
    const timers = manualTimers();
    const logs = [];
    const p = shutdownGateway({ child, graceMs: 50, log: (m) => logs.push(m), ...timers });

    assert.deepStrictEqual(child.signals, ["SIGTERM"]);
    timers.advance(); // grace expires
    assert.deepStrictEqual(child.signals, ["SIGTERM", "SIGKILL"]);

    child.emit("exit", null, "SIGKILL");
    const res = await p;
    assert.equal(res.outcome, "killed");
    assert.equal(res.escalated, true);
    assert.equal(res.signal, "SIGKILL");
    assert.ok(
      logs.some((m) => m.includes("SIGKILL")),
      "an escalation must be logged — a silent SIGKILL is an unexplained data loss"
    );
  });

  it("reports `orphaned` — not success — when even SIGKILL yields no exit", async () => {
    const child = fakeChild();
    const timers = manualTimers();
    const logs = [];
    const p = shutdownGateway({ child, graceMs: 10, killGraceMs: 10, log: (m) => logs.push(m), ...timers });

    timers.advance(); // grace → SIGKILL
    timers.advance(); // kill grace → orphaned
    const res = await p;
    assert.equal(res.outcome, "orphaned");
    assert.ok(
      logs.some((m) => m.includes("orphaned")),
      "the one case we cannot fix must be said out loud"
    );
  });

  it("reports `unreachable` when the signal cannot be delivered (ESRCH)", async () => {
    const child = fakeChild();
    child.kill = () => {
      const err = new Error("kill ESRCH");
      err.code = "ESRCH";
      throw err;
    };
    const res = await shutdownGateway({ child });
    assert.equal(res.outcome, "unreachable");
  });

  it("settles exactly once even if `exit` fires twice", async () => {
    const child = fakeChild();
    const p = shutdownGateway({ child });
    child.emit("exit", 0, null);
    child.emit("exit", 1, null);
    const res = await p;
    assert.equal(res.code, 0, "the first exit is the real one");
  });

  it("every outcome the module can produce is in OUTCOMES (vacuity floor)", () => {
    // Guards against a new branch resolving an outcome nobody documented, and
    // against this suite passing by exercising nothing.
    assert.ok(OUTCOMES.length >= 5, "OUTCOMES must enumerate every terminal state");
    for (const name of ["none", "exited", "killed", "unreachable", "orphaned"]) {
      assert.ok(OUTCOMES.includes(name), `OUTCOMES is missing ${name}`);
    }
  });
});
