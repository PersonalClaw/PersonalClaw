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
 */

/** How long the gateway gets to finish its own shutdown after SIGTERM. */
const DEFAULT_GRACE_MS = 8000;
/** How long we then wait for SIGKILL to be reaped before reporting `orphaned`. */
const DEFAULT_KILL_GRACE_MS = 2000;

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
 * Stop a spawned gateway and RESOLVE ONLY once its fate is known.
 *
 * @param {object} opts
 * @param {import("child_process").ChildProcess|null} opts.child the spawned gateway
 * @param {number} [opts.graceMs] SIGTERM → SIGKILL escalation window
 * @param {number} [opts.killGraceMs] SIGKILL → `orphaned` window
 * @param {(msg: string) => void} [opts.log]
 * @param {typeof setTimeout} [opts.setTimeoutFn] injectable for tests
 * @param {typeof clearTimeout} [opts.clearTimeoutFn]
 * @returns {Promise<{outcome: string, code: number|null, signal: string|null,
 *                    escalated: boolean}>}
 */
function shutdownGateway({
  child,
  graceMs = DEFAULT_GRACE_MS,
  killGraceMs = DEFAULT_KILL_GRACE_MS,
  log = () => {},
  setTimeoutFn = setTimeout,
  clearTimeoutFn = clearTimeout,
} = {}) {
  if (!child) return Promise.resolve({ outcome: "none", code: null, signal: null, escalated: false });

  // Already reaped: node sets exitCode/signalCode once the child is gone. Signalling
  // a dead pid would either throw ESRCH or, far worse on a recycled pid, hit an
  // unrelated process.
  if (child.exitCode !== null && child.exitCode !== undefined) {
    return Promise.resolve({
      outcome: "none",
      code: child.exitCode,
      signal: null,
      escalated: false,
    });
  }
  if (child.signalCode) {
    return Promise.resolve({
      outcome: "none",
      code: null,
      signal: child.signalCode,
      escalated: false,
    });
  }

  return new Promise((resolve) => {
    let settled = false;
    let escalated = false;
    let graceTimer = null;
    let killTimer = null;

    const finish = (outcome, code = null, signal = null) => {
      if (settled) return;
      settled = true;
      if (graceTimer) clearTimeoutFn(graceTimer);
      if (killTimer) clearTimeoutFn(killTimer);
      resolve({ outcome, code, signal, escalated });
    };

    child.once("exit", (code, signal) => {
      log(`gateway exited during shutdown (code=${code} signal=${signal})`);
      finish(escalated ? "killed" : "exited", code ?? null, signal ?? null);
    });

    /** Send a signal; a dead pid (ESRCH) is an answer, not an error. */
    const signalChild = (sig) => {
      try {
        child.kill(sig);
        return true;
      } catch (err) {
        log(`gateway ${sig} could not be delivered: ${err.message}`);
        finish("unreachable");
        return false;
      }
    };

    log("asking the gateway to shut down (SIGTERM)…");
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

module.exports = { shutdownGateway, OUTCOMES, DEFAULT_GRACE_MS, DEFAULT_KILL_GRACE_MS };
