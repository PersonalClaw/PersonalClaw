"use strict";

/**
 * Following the gateway the app uses through the doorbell's news: the one the app started (`own`),
 * and one it found serving its home, which it did not start (`adopt`).
 *
 * Once the doorbell has lost its socket, nothing that carries the home's credentials goes to the
 * port again (`withheld`) until a proof lets it: not the doorbell's own reconnect, not a read, not
 * the registration that sends the local secret. A socket that comes back up proves nothing. The
 * watch reads the home's runtime record every *everyMs* instead:
 *
 * - **A gateway the app found:**
 *   - on another port, *follow()* finds it there, proof and all (`homeGateway.reach`);
 *   - on the same port, `homeGateway.reach` proves it, and only a proof there lets the credentials
 *     go again, with the start the proof names (its pid and the secret it was checked with);
 *   - with no live record and its process gone, it is *gone*.
 * - **The app's own gateway:** only the same start lets the credentials go again. The proof on its
 *   port names the child's pid; the record, read again after the proof, still names that port and
 *   pid; and the secret the proof was checked with is the one this start wrote (its digest, taken
 *   at the ready line). Anything else is a new start or a stopping one: the watch keeps looking,
 *   and the next ready line or the child's exit settles it (`main.js`). It is never *gone*.
 *
 * On a proof, *proven(watched, gateway)* takes the windows' sign-in back (writes the same session
 * again, or signs in afresh) and resolves whether it did. Only then is the withhold lifted and
 * *back(watched)* called, for what carries the credentials again (the registration, the doorbell,
 * the windows). Until *proven* succeeds the watch keeps looking, so a failed rotation is tried
 * again.
 *
 * After an End, or once the doorbell is stopped for a found start (its bound, or a pairless ending
 * read), nothing is lifted for that start again (`ended`). A found gateway's look keeps going with
 * no credential, and only a new start, a moved record or a gone gateway goes ahead, as after a loss;
 * the app's own rests until its next ready line, its exit or a Sign In Again. `ended` itself
 * withholds nothing: the gateway is still the one proven, and what the shell sends stops on the
 * sign-out alone.
 *
 * Electron-free: the reads and the timers are injected (`test/gatewayWatch.test.js`).
 */
function makeGatewayWatch({
  home,
  reach,
  readRecord,
  pidIsAlive,
  follow,
  proven,
  back = () => {},
  gone,
  everyMs = 2000,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  log = () => {},
}) {
  /** The gateway in use: `{kind, pid, port, secretDigest}`, `kind` being "own" or "adopted". */
  let watched = null;
  let withheld = false;
  let timer = null;
  let looking = false;
  /** The start nothing is lifted for again (`ended`): `{pid, port, secretDigest}`. */
  let ended = null;
  /** The start a proof was last logged for: once a start. */
  let saidFor = "";
  const say = (gateway, msg) => {
    const key = `${gateway.pid}:${gateway.port}:${gateway.secretDigest || ""}`;
    if (saidFor !== key) log(msg);
    saidFor = key;
  };
  const isEnded = (gateway) =>
    Boolean(ended) && gateway.pid === ended.pid && gateway.port === ended.port && (gateway.secretDigest || "") === ended.secretDigest;

  const stopLooking = () => {
    if (timer) clearTimer(timer);
    timer = null;
  };
  const again = () => {
    if (watched && (withheld || ended) && !timer) timer = setTimer(look, everyMs);
  };
  /** Take *was* back once *proven* has: lift the withhold, then hand over to *back*. */
  const takeBack = async (was, gateway, next) => {
    if (isEnded(gateway)) return false;
    if (!(await proven(was, gateway))) return false;
    if (watched !== was) return true;
    watched = next;
    withheld = false;
    ended = null;
    back(next);
    return true;
  };

  const look = async () => {
    timer = null;
    if (!watched || (!withheld && !ended) || looking) return;
    looking = true;
    // An ended own start rests until its next ready line, its exit or a Sign In Again. Every other
    // look ends by looking again while there is cause, a take-back or a follow that handed over
    // included: a loss during the hand-over, which a look running then could not take, is looked at
    // in its turn.
    let rest = false;
    try {
      const was = watched;
      const record = readRecord(home);
      const live = Boolean(record && pidIsAlive(record.pid));
      if (was.kind === "own") {
        // The app's own rests until its next ready line, its exit or a Sign In Again.
        if (ended) {
          rest = true;
          return;
        }
        if (live && record.port === was.port && record.pid === was.pid) {
          const found = await reach(home);
          const after = readRecord(home);
          const sameStart =
            found.ok &&
            found.gateway.port === was.port &&
            found.gateway.pid === was.pid &&
            found.gateway.secretDigest === was.secretDigest &&
            Boolean(after) &&
            after.port === was.port &&
            after.pid === was.pid;
          if (watched === was && sameStart && !isEnded(found.gateway)) {
            say(found.gateway, `the gateway this app started proved it is the same start, on port ${was.port}`);
            if (await takeBack(was, found.gateway, was)) return;
          }
        }
      } else if (live && record.port !== was.port) {
        if (await follow()) return;
      } else if (live) {
        const found = await reach(home);
        if (watched === was && found.ok && found.gateway.port === was.port && !isEnded(found.gateway)) {
          say(found.gateway, `the gateway on port ${was.port} proved again that it serves this home (pid ${found.gateway.pid})`);
          const next = { ...was, pid: found.gateway.pid, secretDigest: found.gateway.secretDigest || "" };
          if (await takeBack(was, found.gateway, next)) return;
        }
      } else if (!pidIsAlive(was.pid)) {
        watched = null;
        withheld = false;
        gone({ pid: was.pid, port: was.port });
        return;
      }
    } finally {
      looking = false;
      if (!rest) again();
    }
  };

  return {
    /** Use *gateway*, `{pid, port, secretDigest}`, which the app has just proven serves this home. */
    adopt(gateway) {
      stopLooking();
      watched = { kind: "adopted", pid: gateway.pid, port: gateway.port, secretDigest: gateway.secretDigest || "" };
      withheld = false;
      ended = null;
    },
    /** Use the start of the app's own gateway a ready line announced: `{pid, port, secretDigest}`. */
    own(start) {
      stopLooking();
      watched = { kind: "own", pid: start.pid, port: start.port, secretDigest: start.secretDigest || "" };
      withheld = false;
      ended = null;
    },
    /** Watch nothing: the gateway in use stopped, or the app quits. */
    drop() {
      stopLooking();
      watched = null;
      withheld = false;
      ended = null;
    },
    /** After an End, or once the doorbell is stopped for a found start: nothing is lifted for this
     *  start again. A found gateway's look keeps going with no credential, for the next start, a
     *  moved record or a gone gateway; the app's own rests until its next ready line, its exit or a
     *  Sign In Again. The gateway is still the proven one: only a loss withholds it. */
    ended() {
      if (!watched) return;
      ended = { pid: watched.pid, port: watched.port, secretDigest: watched.secretDigest };
      again();
    },
    /** The doorbell lost its socket: withhold the credentials, and look until a proof lets them go. */
    lost() {
      if (!watched) return;
      withheld = true;
      again();
    },
    /** The doorbell's socket is up. That proves nothing: only `look` lets the credentials go. */
    up() {},
    /** Whether nothing may carry this home's credentials to the gateway's port now. */
    get withheld() {
      return Boolean(watched) && withheld;
    },
    /** The gateway in use, while it is one the app found. */
    get gateway() {
      return watched && watched.kind === "adopted" ? { pid: watched.pid, port: watched.port } : null;
    },
    /** "own", "adopted", or null while nothing is watched. */
    get kind() {
      return watched ? watched.kind : null;
    },
    /** The start watched, `{pid, port, secretDigest}`, or null. */
    get start() {
      return watched ? { pid: watched.pid, port: watched.port, secretDigest: watched.secretDigest } : null;
    },
  };
}

module.exports = { makeGatewayWatch };
