/**
 * What the shell says when the gateway it started stops on its own, or does not start.
 *
 * The gateway is this shell's child. It stops when the app quits, and it restarts in place
 * (Settings → Restart, an applied update) without ever exiting, coming back on a new port that the
 * shell follows (`followRestartedGateway`). Any other exit is one nobody asked for: a restart whose
 * new image could not start, a crash, a kill. The windows are then left on an address nothing
 * answers, and this used to be one console line the owner never sees: the app just stopped working
 * until it was quit and opened again.
 *
 * So the shell says what happened, on the Gateways window's "This computer" row, where the owner
 * starts a gateway here again (the windows follow it to its new address the way they do after a
 * restart) or connects to another one. What happened is the exit status and the last line the
 * gateway wrote to stderr, which is where it says why it stopped (a restart that could not start
 * its new image says so there, `restart_request.start`). That line is the gateway's text, so it
 * gets the treatment the shell gives any text it did not write: escape sequences and control
 * characters out, whitespace collapsed, a length clamp.
 *
 * A gateway that does not start when the app opens is the same news (`notStartedSentence`), said as
 * soon as the start fails. Both were once a sheet with Start Again and Quit, which held the main
 * window, so the Gateways window, its child, queued behind it and never showed.
 *
 * A separate pure module so the sentences and the tail are executed by
 * `desktop/test/gatewayLost.test.js` rather than read out of `main.js` as text.
 */

/** The longest last line the row shows: a traceback's last line can run long. */
const LAST_LINE_MAX = 300;

/** The exit status of a start refused because another gateway already serves its home
 *  (`gateway_base.HOME_SERVED_EXIT`): the app then uses that gateway instead (`homeGateway.js`). */
const HOME_SERVED_EXIT = 3;

/** The exit status of a start refused because a newer PersonalClaw has served its home
 *  (`gateway_base.HOME_NEWER_EXIT`): a start here would be refused again, so the row offers none. */
const HOME_NEWER_EXIT = 4;

/** Terminal escape sequences (colour, cursor) a console log line may carry. */
const ESCAPE_SEQUENCE = /\u001b\[[0-9;?]*[ -/]*[@-~]/g;

/** *text* with escape sequences and control characters out and whitespace collapsed. */
function cleanLine(text) {
  let out = "";
  for (const ch of String(text).replace(ESCAPE_SEQUENCE, "")) {
    const cp = ch.codePointAt(0);
    if (cp === 0x09) {
      out += " ";
      continue;
    }
    if (cp < 0x20 || cp === 0x7f) continue;
    out += ch;
  }
  return out.replace(/\s+/g, " ").trim();
}

/**
 * Keep the last non-empty line a stream wrote, as its chunks arrive. A chunk can end mid-line, so
 * the unfinished part waits for the rest; a stream that ends without a newline still has its last
 * line read.
 */
function makeLastLine() {
  let unfinished = "";
  let last = "";
  return {
    push(chunk) {
      const lines = (unfinished + String(chunk)).split(/\r?\n/);
      unfinished = lines.pop();
      for (const line of lines) {
        const clean = cleanLine(line);
        if (clean) last = clean;
      }
    },
    get() {
      const line = cleanLine(unfinished) || last;
      return line.length > LAST_LINE_MAX ? `${line.slice(0, LAST_LINE_MAX - 1)}…` : line;
    },
  };
}

/**
 * Whether a gateway's exit is one to tell the owner about. Quitting stops the gateway on purpose,
 * and so does making way for a new start; `stopGateway` lets go of the handle before it signals,
 * so the gateway it stops is no longer the one the shell *holds*. A restart replaces the process
 * without an exit at all. Every other exit, of the gateway the shell holds, is one nobody asked for.
 *
 * @param {object} state
 * @param {boolean} state.held - the exited process is still the one the shell holds.
 * @param {boolean} state.quitting - the app is quitting.
 */
function isUnasked({ held, quitting }) {
  return Boolean(held) && !quitting;
}

/** How a gateway ended: its exit status, the signal that ended it, or why it could not start. */
function howItEnded({ code = null, signal = null, startError = "" }, startFailed) {
  if (startError) return `${startFailed} (${cleanLine(startError)})`;
  if (code !== null && code !== undefined) return `The gateway exited with code ${code}`;
  return `The gateway was stopped by ${signal ? `a signal (${signal})` : "the system"}`;
}

/** *what* and, when the gateway said why, its last message, as one row's sentence. */
function withLastLine(headline, what, lastLine) {
  return `${headline} ${what}.${lastLine ? ` Its last message was: ${lastLine}` : ""}`;
}

/**
 * The "This computer" row's sentence for a gateway that stopped when nobody asked it to, or that
 * could not be started again.
 *
 * @param {object} exit
 * @param {number|null} [exit.code] - the exit status, or null when a signal ended it.
 * @param {string|null} [exit.signal] - the signal that ended it, if one did.
 * @param {string} [exit.lastLine] - the last line it wrote to stderr (`makeLastLine`).
 * @param {string} [exit.startError] - why a start failed before there was a process to exit.
 */
function stoppedSentence(exit = {}) {
  return withLastLine("PersonalClaw's gateway stopped.", howItEnded(exit, "The gateway could not be started again"), exit.lastLine || "");
}

/**
 * The row's sentence for a gateway the app could not start when it opened, or that started and
 * never answered (`{ unanswered: true }`).
 */
function notStartedSentence(exit = {}) {
  const what = exit.unanswered ? "It started and never answered" : howItEnded(exit, "The gateway could not be started");
  return withLastLine("PersonalClaw's gateway did not start.", what, exit.lastLine || "");
}

/**
 * The row's sentence for a gateway the app used and did not start, once it has stopped: the app
 * neither stopped it nor starts it again, and says so.
 */
function adoptedGoneSentence(port) {
  return (
    `The gateway on port ${port} stopped. It was started outside this app, so PersonalClaw ` +
    "didn't stop it and won't restart it. Start a gateway here, or connect to another one."
  );
}

/**
 * The row's sentence for the app's own gateway refused this home because a newer PersonalClaw has
 * served it (`HOME_NEWER_EXIT`), *newest* being that version as the home's stamp says it
 * (`homeGateway.newestThatServed`), or "" when it cannot be read. The gateway's own line also names
 * the variable a person rolls a home back with, which the app does not pass on (`gatewayEnv.js`),
 * so the row says what the owner can do here instead: a newer app, or another gateway.
 */
function olderSentence(newest = "") {
  const which = newest ? `PersonalClaw ${newest}` : "a newer PersonalClaw";
  return (
    `This home was last used by ${which}, and this app's gateway is older, so it won't start on ` +
    "it: an older version could lose what the newer one saved. Get the newest app from the " +
    "releases page, or connect to another gateway."
  );
}

module.exports = {
  HOME_NEWER_EXIT,
  HOME_SERVED_EXIT,
  LAST_LINE_MAX,
  adoptedGoneSentence,
  isUnasked,
  makeLastLine,
  notStartedSentence,
  olderSentence,
  stoppedSentence,
};
