const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { electronCommand, electronUnavailable, launchDetail } = require("../helpers/electronHost");
const { builtPageTier, pageUnbuilt } = require("../helpers/builtPage");

// ── The buddy answers in a real Electron only for the owner's click or focused key (#3832) ──────
//
// What `web/src/lib/buddy/host.consent.test.ts` and
// `web/src/ui/approvalKeysAnswerOnlyTheFocusedRequest.test.tsx` check in a DOM model, checked in the
// buddy's real window on the built page (`web/dist`, `#/buddy`), with another window standing in for
// the app the owner works in (`helpers/buddyAnswerProbe.js`):
// - a click on Allow, with the buddy not the focused window, sends one approve to the approval's
//   own route;
// - with focus in the card, Enter alone sends nothing; ⌘Enter (Ctrl+Enter elsewhere) sends nothing
//   while another window has focus, though the key reached the page and focus is still in the card;
//   and the same key, the buddy's window focused again, sends one approve;
// - Escape on the focused card sends nothing, and the approval still waits.
//
// The unfocused key is the one an owner typing in another window cannot see land; the focused one
// after it is its positive control, on the same card with focus where it was.
//
// A built-page leg (`test/built/`, `npm run test:desktop:built`): CI's web-build runs it after its
// build and stamp. Where Electron cannot start, or the page is not built or was built from older
// sources than the checkout's (`scripts/spa_dist_freshness.py check`), it skips and says why, unless
// `PCLAW_REQUIRE_ELECTRON=1` says the host must run it (`helpers/builtPage.js`). `make web-build`
// builds the page.

function probe() {
  const { command, args } = electronCommand([path.join(__dirname, "..", "helpers", "buddyAnswerProbe.js")]);
  const res = spawnSync(command, args, { timeout: 90_000, encoding: "utf8", env: { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: "1" } });
  const line = (res.stdout || "").split("\n").find((l) => l.startsWith("PC_PROBE "));
  if (!line) return { launchFailed: true, detail: launchDetail(res) };
  return JSON.parse(line.slice("PC_PROBE ".length));
}

const unavailable = electronUnavailable();
const unbuilt = unavailable ? null : pageUnbuilt();
const run = unavailable || unbuilt ? null : probe();
const { skip, fail } = builtPageTier({
  electron: unavailable || (run && run.launchFailed ? `electron did not start: ${run.detail}` : null),
  page: unbuilt,
});

describe("the buddy's answers in a real Electron", () => {
  if (fail) it("runs the built page in a real Electron on this host", () => assert.fail(fail));
  const skipped = skip || (fail ? "this host cannot run the built page: the test above fails with why" : false);

  it("loaded the page and drove it without error", { skip: skipped }, () => {
    assert.equal(run.error, undefined, run.error);
    assert.deepEqual(run.consoleErrors, []);
  });

  it("a click on Allow, the buddy not the focused window, sends one approve to the approval's route", { skip: skipped }, () => {
    assert.equal(run.legs.click.windowFocused, false);
    assert.deepEqual(run.legs.click.posts, ["POST /api/approvals/ap-1/approve"]);
  });

  it("⌘Enter sends nothing while another window has focus, and one approve once the buddy's has", { skip: skipped }, () => {
    const k = run.legs.keys;
    assert.equal(k.onCard, true, "a click on the card's heading put focus in the card");
    assert.deepEqual(k.afterEnter, [], "Enter alone decides nothing");
    assert.deepEqual(k.unfocusedKeys, [{ key: "Enter", mod: true, windowFocused: false, onCard: true }], "the key reached the page, focus in the card, its window unfocused");
    assert.deepEqual(k.afterUnfocused, []);
    assert.equal(k.stillOnCard, true, "the same card, focus where it was");
    assert.deepEqual(k.afterFocused, ["POST /api/approvals/ap-2/approve"]);
  });

  it("Escape on the focused card sends nothing, and the approval still waits", { skip: skipped }, () => {
    assert.equal(run.legs.escape.onCard, true);
    assert.deepEqual(run.legs.escape.posts, []);
    assert.deepEqual(run.legs.escape.stillWaiting, ["approval:ap-3"]);
  });
});
