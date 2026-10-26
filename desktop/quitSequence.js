"use strict";

/**
 * The shell's quit, held to one deadline.
 *
 * The packed app's smoke quits it the way the system does, with SIGTERM: Electron answers with a
 * graceful quit (`before-quit`), and the smoke SIGKILLs the app's group 10 s later
 * (`test/smoke/shellSmoke.js`, :data:`SMOKE_WINDOW_MS`). The gateway leads a group of its own
 * (`main.js`'s spawn), so that SIGKILL never reaches it: a quit that has not stopped the gateway
 * by then leaves it running, from the dmg the smoke then finds busy. The quit used to ask the
 * gateway two things first, one after the other, each with a 5 s timeout, and only then give it
 * 8 s to stop and 2 s more: on the x64 release rehearsal the gateway answered neither, and the
 * 10 s ran out before the gateway was ever signalled.
 *
 * Now the whole quit has :data:`QUIT_DEADLINE_MS`, whatever started it. The asks (the shell's
 * unregister, its sign-out) go together, each given at most :data:`ASK_MS`; then the gateway is
 * stopped at once, with the grace that is left before the deadline, less :data:`KILL_WAIT_MS` for
 * its SIGKILL to be reaped. A gateway that answers at once still gets the 8 s it had to stop; one
 * that answers nothing gets 6 s, then SIGKILL.
 *
 * Electron-free, so a test drives it against a real gateway that never answers.
 */

/** How long the packed app's smoke waits for the app to exit after its SIGTERM. */
const SMOKE_WINDOW_MS = 10_000;
/** The whole quit, asks and stop: the smoke's window, less 1.5 s for Electron's own teardown on a
 *  loaded runner. */
const QUIT_DEADLINE_MS = 8_500;
/** The most each ask of the gateway at quit may take. */
const ASK_MS = 2_000;
/** What is kept, of the deadline, for a SIGKILLed gateway to be reaped. */
const KILL_WAIT_MS = 500;

/** *promise*'s value, or null once *ms* have passed or it fails: an ask never holds the quit. */
function bounded(promise, ms) {
  let timer;
  return Promise.race([
    Promise.resolve(promise).catch(() => null),
    new Promise((resolve) => {
      timer = setTimeout(() => resolve(null), ms);
    }),
  ]).finally(() => clearTimeout(timer));
}

/**
 * Ask the gateway *asks*, together, then *stop* it within what is left of the deadline.
 *
 * @param {object} opts
 * @param {Array<(ms: number) => Promise<unknown>>} opts.asks each sends one request, given the
 *   most it may take; it is held to that whatever it does with it
 * @param {(o: {graceMs: number, killGraceMs: number}) => Promise<unknown>} opts.stop
 *   `gatewayShutdown.shutdownGateway`, with the grace and the SIGKILL wait it is given
 * @returns {Promise<unknown>} what *stop* resolves
 */
async function quitWithin({
  asks,
  stop,
  deadlineMs = QUIT_DEADLINE_MS,
  askMs = ASK_MS,
  killWaitMs = KILL_WAIT_MS,
  now = Date.now,
}) {
  const began = now();
  await Promise.all(asks.map((ask) => bounded(Promise.resolve().then(() => ask(askMs)), askMs)));
  const graceMs = Math.max(0, deadlineMs - (now() - began) - killWaitMs);
  return stop({ graceMs, killGraceMs: killWaitMs });
}

module.exports = { ASK_MS, KILL_WAIT_MS, QUIT_DEADLINE_MS, SMOKE_WINDOW_MS, bounded, quitWithin };
