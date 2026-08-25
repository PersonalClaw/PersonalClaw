const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const path = require("node:path");

const { shutdownGateway, OUTCOMES } = require("../gatewayShutdown");

/**
 * A stand-in for the spawned gateway. Records every signal it was sent, and only
 * "exits" when the test says so — which is the whole point: the pre-DC-4 quit path
 * sent SIGTERM and never waited, and a fake that exits on its own would hide exactly
 * that bug.
 */
function fakeChild({ exitOnSignal = null, exitCode = null, signalCode = null, pid = 4242 } = {}) {
  const child = new EventEmitter();
  child.signals = [];
  child.pid = pid;
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
    assert.deepStrictEqual(res, {
      outcome: "none",
      code: null,
      signal: null,
      escalated: false,
      groupSwept: false,
    });
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

/**
 * Group shutdown — the half that reaches the gateway's OWN children.
 *
 * Measured 2026-08-25 against a real gateway: signalling the gateway pid reaped the
 * gateway and left a child of its own alive with `ppid=1`. So the assertions below are
 * about the SIGN of the pid that reaches the OS: `-pid` addresses the whole group,
 * `pid` addresses one process, and the difference is the leak.
 */
describe("shutdownGateway — process group", () => {
  /** Records every (pid, signal) the OS was asked for; can be told to throw ESRCH. */
  function fakeKill({ esrchFor = () => false } = {}) {
    const calls = [];
    const fn = (pid, sig) => {
      calls.push({ pid, sig });
      if (esrchFor({ pid, sig, index: calls.length - 1 })) {
        const err = new Error("kill ESRCH");
        err.code = "ESRCH";
        throw err;
      }
    };
    fn.calls = calls;
    return fn;
  }

  it("signals the NEGATIVE pid — the group — when the child leads its own group", async () => {
    const child = fakeChild({ pid: 777 });
    const killPid = fakeKill({ esrchFor: ({ sig }) => sig === 0 }); // no residue
    const p = shutdownGateway({ child, killGroup: true, readPgid: () => 777, killPid });

    assert.deepStrictEqual(
      killPid.calls,
      [{ pid: -777, sig: "SIGTERM" }],
      "the SIGTERM must go to -pid; a positive pid is the leak this fix exists to close"
    );
    assert.deepStrictEqual(child.signals, [], "group mode must not also signal the pid alone");

    child.emit("exit", 0, null);
    const res = await p;
    assert.equal(res.outcome, "exited");
    assert.equal(res.groupSwept, false, "an empty group must not report a sweep it did not do");
  });

  it("DEGRADES to the single pid when the OS says the child is not its own group leader", async () => {
    // The drift guard. `detached: true` at the spawn site and `killGroup: true` here
    // are two halves of one contract, and nothing in the language ties them together.
    // If the spawn flag is ever flipped back, `-pid` would name the group THIS APP
    // lives in — so the OS's answer, not the caller's request, decides.
    const child = fakeChild({ pid: 777 });
    const killPid = fakeKill();
    const logs = [];
    const p = shutdownGateway({
      child,
      killGroup: true,
      readPgid: () => 12345, // a different group: the parent's
      killPid,
      log: (m) => logs.push(m),
    });

    assert.deepStrictEqual(child.signals, ["SIGTERM"], "it must fall back to signalling the pid");
    assert.deepStrictEqual(
      killPid.calls.filter((c) => c.pid < 0),
      [],
      "a negative pid must NEVER be signalled once the group check has failed"
    );
    assert.ok(
      logs.some((m) => m.includes("declined") && m.includes("survive")),
      "the degradation must be logged AND must say what it costs — a silent fallback to " +
        "the leaky path is how this defect went unnoticed the first time"
    );

    child.emit("exit", 0, null);
    assert.equal((await p).outcome, "exited");
  });

  it("declines the group signal for an implausible pid rather than signalling -1", async () => {
    // `process.kill(-1, sig)` is "every process you are allowed to signal".
    const child = fakeChild({ pid: 1 });
    const killPid = fakeKill();
    const p = shutdownGateway({ child, killGroup: true, readPgid: () => 1, killPid });
    assert.deepStrictEqual(killPid.calls, [], "nothing may be group-signalled for pid<=1");
    assert.deepStrictEqual(child.signals, ["SIGTERM"]);
    child.emit("exit", 0, null);
    await p;
  });

  it("does not group-signal at all when killGroup was not requested", async () => {
    const child = fakeChild({ pid: 777 });
    const killPid = fakeKill();
    let pgidAsked = false;
    const p = shutdownGateway({
      child,
      readPgid: () => {
        pgidAsked = true;
        return 777;
      },
      killPid,
    });
    assert.deepStrictEqual(killPid.calls, []);
    assert.equal(pgidAsked, false, "opt-in means the pgid is not even read");
    assert.deepStrictEqual(child.signals, ["SIGTERM"]);
    child.emit("exit", 0, null);
    assert.equal((await p).groupSwept, false);
  });

  it("SWEEPS the group with SIGKILL when members survive the gateway's exit", async () => {
    const child = fakeChild({ pid: 777 });
    // Signal 0 succeeds → the group still has members → residue exists.
    const killPid = fakeKill();
    const timers = manualTimers();
    const logs = [];
    const p = shutdownGateway({
      child,
      killGroup: true,
      readPgid: () => 777,
      killPid,
      sweepMs: 50,
      log: (m) => logs.push(m),
      ...timers,
    });

    child.emit("exit", 0, null);
    await new Promise((r) => setImmediate(r));
    assert.deepStrictEqual(
      killPid.calls,
      [
        { pid: -777, sig: "SIGTERM" },
        { pid: -777, sig: 0 },
      ],
      "the group must be PROBED with signal 0 before a sweep is scheduled"
    );

    timers.advance(); // the sweep window expires
    const res = await p;
    assert.deepStrictEqual(
      killPid.calls[2],
      { pid: -777, sig: "SIGKILL" },
      "surviving group members must be SIGKILLed, or the quit leaks them"
    );
    assert.equal(res.outcome, "exited", "the gateway itself still exited cleanly");
    assert.equal(res.groupSwept, true, "a sweep that happened must be reported");
    assert.ok(logs.some((m) => m.includes("sweep")), "the sweep must be logged");
  });

  it("skips the sweep window entirely when the group emptied on SIGTERM (fast quit)", async () => {
    // The common case. Paying the sweep delay on every quit would be a tax on the
    // quits that had nothing to clean up.
    const child = fakeChild({ pid: 777 });
    const killPid = fakeKill({ esrchFor: ({ sig }) => sig === 0 });
    const timers = manualTimers();
    const p = shutdownGateway({
      child,
      killGroup: true,
      readPgid: () => 777,
      killPid,
      sweepMs: 50,
      ...timers,
    });

    child.emit("exit", 0, null);
    const res = await p;
    assert.equal(res.groupSwept, false);
    assert.equal(timers.pending.size, 0, "no sweep timer may be left armed");
    assert.deepStrictEqual(
      killPid.calls.map((c) => c.sig),
      ["SIGTERM", 0],
      "no SIGKILL may be sent to a group that is already gone"
    );
  });

  it("reports groupSwept=false when the residue exits on its own inside the window", async () => {
    const child = fakeChild({ pid: 777 });
    // Signal 0 succeeds (residue present), but the later SIGKILL finds it gone.
    const killPid = fakeKill({ esrchFor: ({ sig }) => sig === "SIGKILL" });
    const timers = manualTimers();
    const p = shutdownGateway({
      child,
      killGroup: true,
      readPgid: () => 777,
      killPid,
      sweepMs: 50,
      ...timers,
    });
    child.emit("exit", 0, null);
    await new Promise((r) => setImmediate(r));
    timers.advance();
    const res = await p;
    assert.equal(res.groupSwept, false, "we only claim a sweep when something was actually there");
    assert.equal(res.outcome, "exited");
  });

  it("escalation also targets the group, not the bare pid", async () => {
    const child = fakeChild({ pid: 777 });
    const killPid = fakeKill();
    const timers = manualTimers();
    const p = shutdownGateway({
      child,
      killGroup: true,
      readPgid: () => 777,
      killPid,
      graceMs: 10,
      sweepMs: 5,
      ...timers,
    });
    timers.advance(); // grace expires → SIGKILL
    assert.deepStrictEqual(killPid.calls, [
      { pid: -777, sig: "SIGTERM" },
      { pid: -777, sig: "SIGKILL" },
    ]);
    child.emit("exit", null, "SIGKILL");
    await new Promise((r) => setImmediate(r));
    timers.advance(); // sweep
    const res = await p;
    assert.equal(res.outcome, "killed");
    assert.equal(res.escalated, true);
  });
});

/**
 * The spawn site is the other half of the contract.
 *
 * `shutdownGateway` cannot make the gateway its own group leader — `main.js` does that
 * with `detached: true`, and the runtime guard above silently (and correctly) degrades
 * to the leaky single-pid path if it stops. Silently is the problem: this rail turns
 * that degradation into a red at the one place that can cause it.
 */
describe("main.js gateway spawn shape", () => {
  const raw = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
  /**
   * Comments stripped, because the ABSENCE assertions below are about code.
   * `main.js` documents the old `detached: false` shape it replaced, and a scanner
   * that reads prose would red on the explanation of the very fix it is guarding.
   */
  const code = raw.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

  it("reads a main.js that actually spawns the gateway (vacuity floor)", () => {
    // Without this, every assertion below would pass against an empty or renamed file
    // — and the comment-stripping above is exactly the kind of step that can gut a
    // source string without anyone noticing.
    assert.ok(code.length > 5000, "main.js looks truncated — the rails below prove nothing");
    assert.match(code, /gatewayProcess = spawn\(/, "the gateway spawn site must be findable");
    assert.match(code, /shutdownGateway\(\{/, "the shutdown call site must be findable");
    assert.ok(
      raw.includes("detached: false"),
      "this rail's comment-stripping is load-bearing only while main.js still DISCUSSES " +
        "the old shape; if that prose is gone, simplify the rail rather than leaving a " +
        "step that no longer does anything"
    );
  });

  it("spawns the gateway detached, so it leads its own process group", () => {
    assert.match(
      code,
      /detached: true/,
      "the gateway must be spawned `detached: true`; with `detached: false` its children " +
        "cannot be group-signalled at all — measured: a child survived with ppid=1"
    );
    assert.doesNotMatch(
      code,
      /detached: false/,
      "a `detached: false` spawn is the leak; there must not be one left in this file"
    );
  });

  it("asks shutdownGateway for the group kill", () => {
    assert.match(
      code,
      /killGroup: true/,
      "detaching the spawn buys nothing unless the shutdown opts into the group signal"
    );
  });

  it("does not unref the gateway — quit must still be able to wait for it", () => {
    assert.doesNotMatch(
      code,
      /gatewayProcess\.unref\(\)/,
      "unref would let Electron exit without waiting, re-opening the original defect"
    );
  });
});
