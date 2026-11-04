const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const fs = require("node:fs");
const path = require("node:path");

const {
  HOME_NEWER_EXIT,
  HOME_SERVED_EXIT,
  LAST_LINE_MAX,
  adoptedGoneSentence,
  isUnasked,
  makeLastLine,
  notStartedSentence,
  olderSentence,
  stoppedSentence,
} = require("../gatewayLost");

/**
 * What the shell says when the gateway it started stops on its own.
 *
 * A restart whose new image could not start left the app showing an address nothing answered,
 * with one console line as the only record, until the owner quit and opened it again. These
 * execute the three parts of the answer: which exits are news, the line that says why, and the
 * sentence the Gateways window's "This computer" row says it with.
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

describe("stoppedSentence", () => {
  it("says what happened, with the gateway's last message", () => {
    const said = stoppedSentence({ code: 1, signal: null, lastLine: "It could not start again." });
    assert.equal(said, "PersonalClaw's gateway stopped. The gateway exited with code 1. Its last message was: It could not start again.");
  });

  it("names a signal when one ended the gateway", () => {
    const said = stoppedSentence({ code: null, signal: "SIGKILL" });
    assert.match(said, /was stopped by a signal \(SIGKILL\)\.$/);
    assert.doesNotMatch(said, /Its last message/);
  });

  it("says a start that failed before there was a process failed to start", () => {
    assert.match(stoppedSentence({ startError: "spawn /app/backend ENOENT" }), /could not be started again \(spawn \/app\/backend ENOENT\)/);
  });
});

describe("notStartedSentence", () => {
  it("says the gateway did not start, how it ended and its last message", () => {
    const said = notStartedSentence({ code: 2, lastLine: "personalclaw: error: invalid choice" });
    assert.equal(said, "PersonalClaw's gateway did not start. The gateway exited with code 2. Its last message was: personalclaw: error: invalid choice");
  });

  it("says why a start failed before there was a process, without 'again'", () => {
    const said = notStartedSentence({ startError: "spawn /app/backend ENOENT" });
    assert.match(said, /The gateway could not be started \(spawn \/app\/backend ENOENT\)/);
    assert.doesNotMatch(said, /started again/);
  });

  it("says a gateway that started and never answered, without saying it could not start", () => {
    const said = notStartedSentence({ unanswered: true });
    assert.equal(said, "PersonalClaw's gateway did not start. It started and never answered.");
  });
});

describe("adoptedGoneSentence", () => {
  it("says a gateway the app did not start has stopped, that the app neither stopped it nor restarts it, and what to do", () => {
    const said = adoptedGoneSentence(10000);
    assert.match(said, /^The gateway on port 10000 stopped\./);
    assert.match(said, /started outside this app, so PersonalClaw didn't stop it and won't restart it/);
    assert.match(said, /Start a gateway here, or connect to another one\.$/);
  });
});

describe("HOME_SERVED_EXIT", () => {
  it("is gateway_base's exit status for a home another gateway serves", () => {
    // `tests/test_desktop_home_gateway_parity.py` holds this to `gateway_base.HOME_SERVED_EXIT`.
    assert.equal(HOME_SERVED_EXIT, 3);
  });
});

describe("a home a newer PersonalClaw served (HOME_NEWER_EXIT)", () => {
  it("is gateway_base's exit status for it, its own", () => {
    // `tests/test_desktop_home_gateway_parity.py` holds this to `gateway_base.HOME_NEWER_EXIT`.
    assert.equal(HOME_NEWER_EXIT, 4);
    assert.notEqual(HOME_NEWER_EXIT, HOME_SERVED_EXIT);
  });

  it("says which version served the home, that this app's is older, and what the owner can do here", () => {
    const said = olderSentence("0.3.7");
    assert.match(said, /^This home was last used by PersonalClaw 0\.3\.7, and this app's gateway is older, so it won't start on it/);
    assert.match(said, /could lose what the newer one saved/);
    assert.match(said, /releases page, or connect to another gateway\.$/);
    assert.doesNotMatch(said, /PERSONALCLAW_ROLL_BACK_FROM|start a gateway here/i, "it offers what the app does not do");
    assert.match(olderSentence(""), /^This home was last used by a newer PersonalClaw/);
  });
});

/**
 * The loading screen is `main.js` code, which no test launches, so its wiring is read as a FILE,
 * with a floor that proves each pattern is still there to read (the `connectSeam.test.js` rule).
 */
describe("a gateway that did not start, or stopped, lands on the Gateways window, never on a sheet", () => {
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

  it("shows no sheet anywhere in the app: a sheet would hold the window the Gateways window opens on", () => {
    assert.doesNotMatch(MAIN, /showMessageBox\(/);
    assert.doesNotMatch(MAIN, /\bdialog\b\s*[,}]/, "main.js still takes Electron's dialog");
  });

  it("says a failed start at once, before the two-minute wait, on the Gateways window", () => {
    const loading = body("showLoadingThenConnect");
    comesBefore(loading, "if (startFailure || !localGatewayUrl) throw", "await waitForBackend(");
    assert.match(loading, /showNotConnected\(\)/);
    assert.match(loading, /startFailure = \{ unanswered: true \};/);
  });

  it("says a gateway that stopped there too, with how it ended", () => {
    const lost = body("gatewayLost");
    assert.match(lost, /setLocalState\("stopped", stoppedSentence\(exit\)\)/);
    assert.match(lost, /showNotConnected\(\)/);
  });

  it("starts a gateway here only after stopping one that may still be starting", () => {
    // Never two gateways on one home: a start that timed out can still be running.
    comesBefore(body("startLocalHere"), "await stopGateway()", "await bringUpLocalGateway()");
  });

  it("says a start refused for a newer home on the row, with no start to offer: one would be refused again", () => {
    const up = body("bringUpLocalGateway");
    assert.match(up, /if \(exit\.code === HOME_NEWER_EXIT\) \{\s*startFailure = \{ \.\.\.exit, older: true \};\s*setOlder\(\);\s*return false;/);
    assert.match(body("gatewayLost"), /if \(exit\.code === HOME_NEWER_EXIT\) setOlder\(\);/, "a restart's new image refused the home says it too");
    assert.match(MAIN, /setLocalState\("older", olderSentence\(homeGateway\.newestThatServed\(PERSONALCLAW_HOME\)\)\)/);
  });

  it("keeps a failed start's exit for the row to say", () => {
    const up = body("bringUpLocalGateway");
    assert.match(up, /const exit = err\.exit \|\| \{ startError: err\.message \};/);
    assert.match(up, /setLocalState\("stopped", notStartedSentence\(exit\)\)/);
  });
});
