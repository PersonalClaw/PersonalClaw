/**
 * Graceful gateway shutdown (DC-4 T4.3).
 *
 * The desktop shell SPAWNS the gateway (`main.js` `startGateway`), so quitting the
 * app is the one moment the backend's data can be torn in half. The pre-DC-4 quit
 * path was `gatewayProcess.kill("SIGTERM"); gatewayProcess = null;` inside a
 * SYNCHRONOUS `before-quit` handler — it sent the signal and then let Electron exit
 * immediately, which means:
 *
 *  - it never learned whether the child actually exited, and
 *  - because the child is spawned `detached: false` WITHOUT its own process group,
 *    a gateway that was slow to handle SIGTERM (mid-write, mid-flush) simply
 *    outlived its parent and became an ORPHAN holding the port and the home dir.
 *
 * "Graceful" here is therefore a claim about STATE, not a menu item: ask the child
 * to stop, WAIT for its `exit`, and escalate only if it will not go. Every path
 * resolves with a named outcome so the caller can log the truth — including the one
 * case we cannot fix, `orphaned`, where even SIGKILL produced no `exit` within the
 * grace window. Naming that outcome is the point: a quit that might have orphaned a
 * gateway should say so rather than look clean.
 *
 * Deliberately Electron-free and injectable (child, timers, log) so the ordering can
 * be tested against a fake child process without launching a real gateway.
 *
 * ── The grandchild leak (measured 2026-08-25) ──────────────────────────────────────
 * Signalling the gateway pid alone is not enough. Driving the real module against a
 * real gateway showed the gateway itself reaped cleanly (`exited`, 3ms, nothing left
 * in the process table); but a stand-in with the same spawn shape and ONE child of its
 * own left that child alive with `ppid=1` — reparented to launchd, still holding the
 * inherited stdout pipe. The gateway spawns real subprocesses (ACP CLIs, MCP servers,
 * terminal sessions), so "no orphan gateway" was true of the pid and false of the
 * tree. `child.kill()` cannot reach them: it signals one pid.
 *
 * The fix has two halves, and BOTH are required. `main.js` spawns the gateway
 * `detached: true` so it leads its own process group (Node's `detached` calls
 * `setsid()`), and this module signals the GROUP. Under the old `detached: false`
 * shape the group-signal was not merely useless but dangerous: the measured pgid was
 * the PARENT's group, so `process.kill(-pid)` would have killed the app itself and
 * every sibling in it. That is why group signalling here is opt-in AND verified
 * against the OS rather than trusted from a flag — see `killGroup`.
 */

/** How long the gateway gets to finish its own shutdown after SIGTERM. */
const DEFAULT_GRACE_MS = 8000;
/** How long we then wait for SIGKILL to be reaped before reporting `orphaned`. */
const DEFAULT_KILL_GRACE_MS = 2000;
/**
 * How long the gateway's OWN children get, after the gateway is gone, before the
 * group residue is SIGKILLed. They already received the group SIGTERM; this is the
 * beat in which a well-behaved child finishes flushing.
 */
const DEFAULT_SWEEP_MS = 500;

/**
 * Outcomes, all terminal:
 *  `none`      — there was no child to stop (gateway never started, or already reaped)
 *  `exited`    — SIGTERM was honored and the child's `exit` fired (the happy path)
 *  `killed`    — SIGTERM timed out, SIGKILL was sent, `exit` then fired
 *  `unreachable` — the signal could not be delivered (ESRCH): the pid is already gone
 *  `orphaned`  — no `exit` even after SIGKILL. The app is quitting anyway; SAY IT.
 */
const OUTCOMES = ["none", "exited", "killed", "unreachable", "orphaned"];

/**
 * Read a pid's process-group id from the OS, or `null` if it cannot be determined.
 *
 * Node exposes no `getpgid`, and this answer gates a `process.kill(-pid)` — the one
 * call in the quit path that can take the whole app down if it is wrong — so it is
 * asked of the OS rather than inferred from how we believe we spawned. One `ps` in a
 * quit path that is already waiting seconds for a child is not a cost worth trading
 * for that risk. Any failure returns `null`, which DECLINES the group signal.
 */
function defaultReadPgid(pid) {
  try {
    const { execFileSync } = require("child_process");
    const out = execFileSync("ps", ["-p", String(pid), "-o", "pgid="], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    const pgid = Number.parseInt(out, 10);
    return Number.isInteger(pgid) && pgid > 0 ? pgid : null;
  } catch {
    return null;
  }
}

/**
 * Stop a spawned gateway and RESOLVE ONLY once its fate is known.
 *
 * @param {object} opts
 * @param {import("child_process").ChildProcess|null} opts.child the spawned gateway
 * @param {number} [opts.graceMs] SIGTERM → SIGKILL escalation window
 * @param {number} [opts.killGraceMs] SIGKILL → `orphaned` window
 * @param {(msg: string) => void} [opts.log]
 * @param {typeof setTimeout} [opts.setTimeoutFn] injectable for tests
 * @param {typeof clearTimeout} [opts.clearTimeoutFn]
 * @param {boolean} [opts.killGroup] signal the child's whole process GROUP, so the
 *   subprocesses the gateway itself started go with it. Requesting this is a request,
 *   not a instruction: it is honored only if the OS confirms the child leads its own
 *   group, because otherwise the negative pid would target the app's own group.
 * @param {number} [opts.sweepMs] post-exit window before the group residue is killed
 * @param {(pid: number) => number|null} [opts.readPgid] injectable for tests
 * @param {(pid: number, sig: string|number) => void} [opts.killPid] injectable; used
 *   ONLY for group signals, so a test can prove the sign of the pid it was handed
 * @returns {Promise<{outcome: string, code: number|null, signal: string|null,
 *                    escalated: boolean, groupSwept: boolean}>}
 */
function shutdownGateway({
  child,
  graceMs = DEFAULT_GRACE_MS,
  killGraceMs = DEFAULT_KILL_GRACE_MS,
  log = () => {},
  setTimeoutFn = setTimeout,
  clearTimeoutFn = clearTimeout,
  killGroup = false,
  sweepMs = DEFAULT_SWEEP_MS,
  readPgid = defaultReadPgid,
  killPid = (pid, sig) => process.kill(pid, sig),
} = {}) {
  if (!child)
    return Promise.resolve({
      outcome: "none",
      code: null,
      signal: null,
      escalated: false,
      groupSwept: false,
    });

  // Already reaped: node sets exitCode/signalCode once the child is gone. Signalling
  // a dead pid would either throw ESRCH or, far worse on a recycled pid, hit an
  // unrelated process.
  if (child.exitCode !== null && child.exitCode !== undefined) {
    return Promise.resolve({
      outcome: "none",
      code: child.exitCode,
      signal: null,
      escalated: false,
      groupSwept: false,
    });
  }
  if (child.signalCode) {
    return Promise.resolve({
      outcome: "none",
      code: null,
      signal: child.signalCode,
      escalated: false,
      groupSwept: false,
    });
  }

  // Resolve the group target ONCE, before any signal goes out. `process.kill(-pid)`
  // addresses a process group, so a child that is not its own group leader would send
  // us at the group its parent lives in — this app. A caller asking for `killGroup`
  // therefore is not sufficient; the OS has to agree.
  let groupTarget = false;
  if (killGroup) {
    const pid = child.pid;
    if (!Number.isInteger(pid) || pid <= 1) {
      log(`group shutdown declined: implausible pid ${pid}`);
    } else {
      const pgid = readPgid(pid);
      if (pgid === pid) {
        groupTarget = true;
      } else {
        // Not a warning to be tuned out: this is the difference between reaping the
        // gateway's children and leaking them, and it means the spawn site stopped
        // passing `detached: true`.
        log(
          `group shutdown declined: pid ${pid} does not lead its own group (pgid=${pgid}) — ` +
            `signalling the pid alone, so the gateway's OWN children will survive`
        );
      }
    }
  }

  return new Promise((resolve) => {
    let settled = false;
    let escalated = false;
    let graceTimer = null;
    let killTimer = null;
    let sweepTimer = null;

    const finish = (outcome, code = null, signal = null, groupSwept = false) => {
      if (settled) return;
      settled = true;
      if (graceTimer) clearTimeoutFn(graceTimer);
      if (killTimer) clearTimeoutFn(killTimer);
      if (sweepTimer) clearTimeoutFn(sweepTimer);
      resolve({ outcome, code, signal, escalated, groupSwept });
    };

    child.once("exit", (code, signal) => {
      log(`gateway exited during shutdown (code=${code} signal=${signal})`);
      const outcome = escalated ? "killed" : "exited";
      // The child is gone: the escalation timers are moot, and leaving them armed
      // would let an `orphaned` verdict fire while the sweep is still running — a
      // false alarm about the one outcome that must stay trustworthy.
      if (graceTimer) clearTimeoutFn(graceTimer);
      if (killTimer) clearTimeoutFn(killTimer);
      graceTimer = null;
      killTimer = null;
      if (!groupTarget) return finish(outcome, code ?? null, signal ?? null);

      // The gateway pid is reaped, but its own children inherited the group — and a
      // leaked ACP CLI or MCP server still holding the home dir is exactly the orphan
      // this clause is about, even though `ps` shows the gateway gone. They already
      // got the group SIGTERM; sweep whatever did not take it.
      //
      // Probe with signal 0 first. A group whose last member just exited no longer
      // exists, so ESRCH here means "nothing survived the SIGTERM" and the quit can
      // finish immediately — the sweep delay is then paid only by the quits that
      // actually have residue to clean up, not by every quit.
      try {
        killPid(-child.pid, 0);
      } catch {
        log("gateway process group emptied on SIGTERM — no residue to sweep");
        return finish(outcome, code ?? null, signal ?? null);
      }
      log("gateway exited but its process group still has members — sweeping");
      sweepTimer = setTimeoutFn(() => {
        let groupSwept = false;
        try {
          killPid(-child.pid, "SIGKILL");
          // No ESRCH means the group still had members: there WAS residue.
          groupSwept = true;
          log("swept residual members of the gateway's process group (SIGKILL)");
        } catch {
          log("residual group members exited on their own during the sweep window");
        }
        finish(outcome, code ?? null, signal ?? null, groupSwept);
      }, sweepMs);
    });

    /** Send a signal; a dead pid (ESRCH) is an answer, not an error. */
    const signalChild = (sig) => {
      try {
        // The negative pid is the whole point of the group path — and is only ever
        // reached once `readPgid` confirmed the child leads its own group.
        if (groupTarget) killPid(-child.pid, sig);
        else child.kill(sig);
        return true;
      } catch (err) {
        log(`gateway ${sig} could not be delivered: ${err.message}`);
        finish("unreachable");
        return false;
      }
    };

    log(
      groupTarget
        ? "asking the gateway and its process group to shut down (SIGTERM)…"
        : "asking the gateway to shut down (SIGTERM)…"
    );
    if (!signalChild("SIGTERM")) return;

    graceTimer = setTimeoutFn(() => {
      escalated = true;
      log(`gateway did not exit within ${graceMs}ms — escalating to SIGKILL`);
      if (!signalChild("SIGKILL")) return;
      killTimer = setTimeoutFn(() => {
        // Nothing more we can do from here, and pretending otherwise would be the
        // lie this whole module exists to avoid.
        log("gateway did not exit after SIGKILL — it may be orphaned");
        finish("orphaned");
      }, killGraceMs);
    }, graceMs);
  });
}

module.exports = {
  shutdownGateway,
  OUTCOMES,
  DEFAULT_GRACE_MS,
  DEFAULT_KILL_GRACE_MS,
  DEFAULT_SWEEP_MS,
  defaultReadPgid,
};
