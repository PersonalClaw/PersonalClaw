const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const fs = require("node:fs");
const path = require("node:path");

const {
  LAST_LINE_MAX,
  QUIT,
  START_AGAIN,
  firstStartDialog,
  gatewayLostDialog,
  isUnasked,
  makeLastLine,
} = require("../gatewayLost");

/**
 * What the shell says when the gateway it started stops on its own.
 *
 * A restart whose new image could not start left the app showing an address nothing answered,
 * with one console line as the only record, until the owner quit and opened it again. These
 * execute the three parts of the answer: which exits are news, the line that says why, and the
 * dialog that offers what to do.
 */
describe("isUnasked", () => {
  it("is news when the gateway the shell holds exits while the app is not quitting", () => {
    assert.equal(isUnasked({ held: true, quitting: false }), true);
  });

  it("is not news while the app is quitting: quitting stops the gateway on purpose", () => {
    assert.equal(isUnasked({ held: true, quitting: true }), false);
  });

  it("is not news for a gateway the shell already let go of (stopped to make way for a start)", () => {
    assert.equal(isUnasked({ held: false, quitting: false }), false);
  });
});

describe("makeLastLine", () => {
  it("keeps the last non-empty line the gateway wrote, across chunk boundaries", () => {
    const tail = makeLastLine();
    tail.push("12:00:01 INFO gateway: serving\nPersonalClaw stopped to re");
    tail.push("start and could not start again (/app/backend: No such file or directory). Start it again.\n\n");
    assert.equal(
      tail.get(),
      "PersonalClaw stopped to restart and could not start again (/app/backend: No such file or " +
        "directory). Start it again."
    );
  });

  it("reads a last line the stream never ended with a newline", () => {
    const tail = makeLastLine();
    tail.push("first\nusage: personalclaw [-h]\npersonalclaw: error: argument: invalid choice");
    assert.equal(tail.get(), "personalclaw: error: argument: invalid choice");
  });

  it("shows the gateway's text as text: escape sequences and control characters out", () => {
    const tail = makeLastLine();
    tail.push("\u001b[31mERROR\u001b[0m the\tstop\u0007 failed\n");
    assert.equal(tail.get(), "ERROR the stop failed");
  });

  it("clamps a long line", () => {
    const tail = makeLastLine();
    tail.push(`${"x".repeat(LAST_LINE_MAX * 2)}\n`);
    const line = tail.get();
    assert.equal(line.length, LAST_LINE_MAX);
    assert.ok(line.endsWith("…"));
  });

  it("is empty when the gateway wrote nothing", () => {
    assert.equal(makeLastLine().get(), "");
  });
});

describe("gatewayLostDialog", () => {
  it("says what happened, with the gateway's last message, and what to do", () => {
    const options = gatewayLostDialog({ code: 1, signal: null, lastLine: "It could not start again." });
    assert.equal(options.message, "PersonalClaw's gateway stopped.");
    assert.match(options.detail, /exited with code 1/);
    assert.match(options.detail, /Its last message was: It could not start again\./);
    assert.match(options.detail, /Start it again, or quit PersonalClaw and open it again later\./);
  });

  it("offers Start Again and Quit, and only the Quit button quits", () => {
    const options = gatewayLostDialog({ code: 2 });
    assert.deepEqual(options.buttons, ["Start Again", "Quit"]);
    assert.equal(options.buttons[START_AGAIN], "Start Again");
    assert.equal(options.buttons[QUIT], "Quit");
    // Return, and a dialog dismissed with Escape, start it again; quitting is a choice she makes.
    assert.equal(options.defaultId, START_AGAIN);
    assert.equal(options.cancelId, START_AGAIN);
  });

  it("names a signal when one ended the gateway", () => {
    const options = gatewayLostDialog({ code: null, signal: "SIGKILL" });
    assert.match(options.detail, /was stopped by a signal \(SIGKILL\)/);
    assert.doesNotMatch(options.detail, /Its last message/);
  });

  it("says a start that failed before there was a process failed to start", () => {
    const options = gatewayLostDialog({ startError: "spawn /app/backend ENOENT" });
    assert.match(options.detail, /could not be started again \(spawn \/app\/backend ENOENT\)/);
  });
});

describe("firstStartDialog", () => {
  it("says the gateway did not start, how it ended and its last message", () => {
    const options = firstStartDialog({ code: 2, lastLine: "personalclaw: error: invalid choice" });
    assert.equal(options.message, "PersonalClaw's gateway did not start.");
    assert.match(options.detail, /exited with code 2, so the app has nothing to show/);
    assert.match(options.detail, /Its last message was: personalclaw: error: invalid choice/);
    assert.match(options.detail, /Start it again, or quit PersonalClaw and open it again later\./);
  });

  it("offers what the gateway-lost dialog offers: Start Again, the default, or Quit", () => {
    const options = firstStartDialog({ code: 1 });
    assert.deepEqual(options.buttons, gatewayLostDialog({ code: 1 }).buttons);
    assert.equal(options.buttons[START_AGAIN], "Start Again");
    assert.equal(options.defaultId, START_AGAIN);
    assert.equal(options.cancelId, START_AGAIN);
  });

  it("says why a start failed before there was a process, without 'again'", () => {
    const options = firstStartDialog({ startError: "spawn /app/backend ENOENT" });
    assert.match(options.detail, /The gateway could not be started \(spawn \/app\/backend ENOENT\)/);
    assert.doesNotMatch(options.detail, /started again/);
  });

  it("says a gateway that started and never answered, without saying it could not start", () => {
    const options = firstStartDialog({ unanswered: true });
    assert.match(options.detail, /^The gateway started and never answered, so the app has nothing to show\./);
    assert.doesNotMatch(options.detail, /could not be started/);
  });
});

/**
 * The loading screen is `main.js` code, which no test launches, so its wiring is read as a FILE,
 * with a floor that proves each pattern is still there to read (the `connectSeam.test.js` rule).
 */
describe("the loading screen starts the gateway again", () => {
  const MAIN = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");
  const body = (name) => {
    const start = MAIN.indexOf(`async function ${name}(`);
    assert.ok(start > 0, `${name} is gone — this rail is now blind`);
    return MAIN.slice(start, MAIN.indexOf("\n}\n", start));
  };
  /** *first* comes before *then* in *text*, and both are there: a missing one is not "before". */
  const comesBefore = (text, first, then) => {
    const at = text.indexOf(first);
    assert.ok(at >= 0, `${first} is gone — this rail is now blind`);
    assert.ok(at < text.indexOf(then), `${first} no longer comes before ${then}`);
  };

  it("says a failed start at once, with the gateway-lost dialog's choices", () => {
    const loading = body("showLoadingThenConnect");
    assert.match(loading, /firstStartDialog\(/);
    // A start that failed has nothing to wait for: the dialog comes before the two-minute wait,
    // a failed Start Again after a gateway that never answered included (its address is still set).
    comesBefore(loading, "if (startFailure) throw", "await waitForBackend(");
    assert.match(loading, /const exit = startFailure \|\| \{ unanswered: true \};/);
    assert.doesNotMatch(loading, /Try reopening the app/);
  });

  it("starts a new gateway on Start Again, not only waits again", () => {
    const loading = body("showLoadingThenConnect");
    assert.match(loading, /if \(response === START_AGAIN\) \{\s*await startAgain\(\);/);
    const again = body("startAgain");
    // Never two gateways on one home: a start that timed out can still be running.
    comesBefore(again, "await stopGateway()", "await startGateway()");
    assert.match(again, /startFailure = err\.exit \|\| \{ startError: err\.message \}/);
  });

  it("keeps the first start's failure for the dialog to say", () => {
    assert.match(MAIN, /console\.error\("Gateway did not start:", err\.message\);\s*\/\/[^\n]*\n\s*startFailure = err\.exit/);
  });
});
