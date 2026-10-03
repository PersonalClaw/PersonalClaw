/**
 * What the shell tells the owner when the gateway it started stops on its own.
 *
 * The gateway is this shell's child. It stops when the app quits, and it restarts in place
 * (Settings → Restart, an applied update) without ever exiting, coming back on a new port that the
 * shell follows (`followRestartedGateway`). Any other exit is one nobody asked for: a restart whose
 * new image could not start, a crash, a kill. The windows are then left on an address nothing
 * answers, and this used to be one console line the owner never sees: the app just stopped working
 * until it was quit and opened again.
 *
 * So the shell says what happened and offers what to do about it: start the gateway again, and the
 * windows follow it to its new address the way they do after a restart, or quit. What happened is
 * the exit status and the last line the gateway wrote to stderr, which is where it says why it
 * stopped (a restart that could not start its new image says so there, `restart_request.start`).
 * That line is the gateway's text, so it gets the treatment the shell gives any text it did not
 * write: escape sequences and control characters out, whitespace collapsed, a length clamp.
 *
 * A gateway that does not start when the app opens is the same news with the same two choices
 * (`firstStartDialog`): said as soon as the start fails, and Start Again starts a new one.
 *
 * A separate pure module so the sentence and the tail are executed by
 * `desktop/test/gatewayLost.test.js` rather than read out of `main.js` as text.
 */

/** The longest last line the dialog shows: a traceback's last line can run long. */
const LAST_LINE_MAX = 300;

/** The buttons, in order: `response` 0 starts the gateway again, 1 quits. */
const START_AGAIN = 0;
const QUIT = 1;

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

/**
 * The two choices, said the same way whether the gateway stopped or never started: Start Again,
 * which starts a new gateway, or Quit.
 *
 * @param {string} message - the dialog's headline.
 * @param {string} what - how it ended, without a full stop.
 * @param {string} lastLine - the last line it wrote to stderr, or "".
 */
function startAgainDialog(message, what, lastLine) {
  const said = lastLine ? ` Its last message was: ${lastLine}` : "";
  return {
    type: "warning",
    title: "PersonalClaw",
    message,
    detail:
      `${what}, so the app has nothing to show.${said}\n\n` +
      "Start it again, or quit PersonalClaw and open it again later.",
    buttons: ["Start Again", "Quit"],
    defaultId: START_AGAIN,
    // A dialog dismissed without a choice starts the gateway again: that is safe to repeat (a start
    // that fails says so again), where quitting would take the app away from a reflexive Escape.
    cancelId: START_AGAIN,
    noLink: true,
  };
}

/** How a gateway ended: its exit status, the signal that ended it, or why it could not start. */
function howItEnded({ code = null, signal = null, startError = "" }, startFailed) {
  if (startError) return `${startFailed} (${cleanLine(startError)})`;
  if (code !== null && code !== undefined) return `The gateway exited with code ${code}`;
  return `The gateway was stopped by ${signal ? `a signal (${signal})` : "the system"}`;
}

/**
 * The dialog for a gateway that stopped when nobody asked it to, or that could not be started again.
 *
 * @param {object} exit
 * @param {number|null} [exit.code] - the exit status, or null when a signal ended it.
 * @param {string|null} [exit.signal] - the signal that ended it, if one did.
 * @param {string} [exit.lastLine] - the last line it wrote to stderr (`makeLastLine`).
 * @param {string} [exit.startError] - why a start failed before there was a process to exit.
 * @returns {object} `dialog.showMessageBox` options; `response` is START_AGAIN or QUIT.
 */
function gatewayLostDialog(exit = {}) {
  const what = howItEnded(exit, "The gateway could not be started again");
  return startAgainDialog("PersonalClaw's gateway stopped.", what, exit.lastLine || "");
}

/**
 * The dialog for a gateway the app could not start when it opened, or that started and never
 * answered. It used to say "Try reopening the app" and offer Retry, which only waited for the
 * same gateway again: nothing had started it, so it waited out the whole two minutes and failed
 * the same way. Now it says how the start ended, at once, and Start Again starts a new gateway,
 * as the gateway-lost dialog's does.
 *
 * @param {object} exit - as `gatewayLostDialog`'s, or `{ unanswered: true }` for a gateway that
 *   started and never answered.
 * @returns {object} `dialog.showMessageBox` options; `response` is START_AGAIN or QUIT.
 */
function firstStartDialog(exit = {}) {
  const what = exit.unanswered
    ? "The gateway started and never answered"
    : howItEnded(exit, "The gateway could not be started");
  return startAgainDialog("PersonalClaw's gateway did not start.", what, exit.lastLine || "");
}

module.exports = {
  LAST_LINE_MAX,
  QUIT,
  START_AGAIN,
  firstStartDialog,
  gatewayLostDialog,
  isUnasked,
  makeLastLine,
};
