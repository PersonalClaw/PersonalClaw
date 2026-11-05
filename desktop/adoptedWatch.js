"use strict";

/**
 * Following a gateway the app found serving its home, which it did not start, through the doorbell's
 * news, without sending this home's credentials to whatever answers that gateway's port.
 *
 * Once the doorbell has lost its socket, nothing that carries the home's credentials goes to the
 * port again (`withheld`): not the doorbell's own reconnect, not a read, not the registration that
 * sends the local secret. A socket that comes back up proves nothing, since anything that listens
 * there can answer it. The watch reads the home's runtime record every *everyMs* instead:
 *
 * - the gateway on another port: *follow()* finds it there, proof and all (`homeGateway.reach`);
 * - the record names the same port: `homeGateway.reach` proves it, and only a proof on that port
 *   lets the credentials go again, with the pid the proof names (*proven*);
 * - no live record and the gateway's process gone: *gone*.
 *
 * Electron-free: the reads and the timers are injected (`test/adoptedWatch.test.js`).
 */

function makeAdoptedWatch({
  home,
  reach,
  readRecord,
  pidIsAlive,
  follow,
  proven,
  gone,
  everyMs = 2000,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  log = () => {},
}) {
  /** The gateway in use, `{pid, port}`, while it is one the app found. */
  let adopted = null;
  let withheld = false;
  let timer = null;

  const stopLooking = () => {
    if (timer) clearTimer(timer);
    timer = null;
  };

  const look = async () => {
    timer = null;
    if (!adopted) return;
    const record = readRecord(home);
    const live = Boolean(record && pidIsAlive(record.pid));
    if (live && record.port !== adopted.port) {
      if (await follow()) return;
    } else if (live) {
      const found = await reach(home);
      if (!adopted) return;
      if (found.ok && found.gateway.port === adopted.port) {
        adopted = { pid: found.gateway.pid, port: adopted.port };
        withheld = false;
        log(`the gateway on port ${adopted.port} proved again that it serves this home (pid ${adopted.pid})`);
        proven(adopted);
        return;
      }
    } else if (!pidIsAlive(adopted.pid)) {
      const was = adopted;
      adopted = null;
      gone(was);
      return;
    }
    if (adopted && !timer) timer = setTimer(look, everyMs);
  };

  return {
    /** Use *gateway*, `{pid, port}`, which the app has just proven serves this home. */
    adopt(gateway) {
      stopLooking();
      adopted = { pid: gateway.pid, port: gateway.port };
      withheld = false;
    },
    /** The app no longer uses a gateway it found (it started its own, or quits). */
    drop() {
      stopLooking();
      adopted = null;
    },
    /** The doorbell lost its socket: withhold the credentials, and look until it is proven again. */
    lost() {
      if (!adopted) return;
      withheld = true;
      if (!timer) timer = setTimer(look, everyMs);
    },
    /** The doorbell's socket is up. That proves nothing: only `look` lets the credentials go. */
    up() {},
    /** Whether nothing may carry this home's credentials to the gateway's port now. */
    get withheld() {
      return Boolean(adopted) && withheld;
    },
    /** The gateway in use, while it is one the app found. */
    get gateway() {
      return adopted;
    },
  };
}

module.exports = { makeAdoptedWatch };
