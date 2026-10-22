const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { electronCommand, electronTier, electronUnavailable, launchDetail } = require("./helpers/electronHost");

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
// Skips where Electron cannot start, and says why, unless `PCLAW_REQUIRE_ELECTRON=1` says the host
// must run it (`helpers/electronHost.js`). It also skips, saying why, where the page is not built
// or was built from older sources than the checkout's (`scripts/spa_dist_freshness.py check`):
// `make web-build` builds it.

const REPO = path.join(__dirname, "..", "..");

/** Why the built page cannot be the one under test, or null. */
function pageUnbuilt() {
  if (!fs.existsSync(path.join(REPO, "web", "dist", "index.html"))) return "the web app is not built: run `make web-build`";
  const check = spawnSync("python3", ["scripts/spa_dist_freshness.py", "check"], { cwd: REPO, encoding: "utf8", timeout: 30_000 });
  if (check.status === 1) return `the built web app is older than web/'s sources: run \`make web-build\` (${(check.stdout || "").trim()})`;
  return null;
}

function probe() {
  const { command, args } = electronCommand([path.join(__dirname, "helpers", "buddyAnswerProbe.js")]);
  const res = spawnSync(command, args, { timeout: 90_000, encoding: "utf8", env: { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: "1" } });
  const line = (res.stdout || "").split("\n").find((l) => l.startsWith("PC_PROBE "));
  if (!line) return { launchFailed: true, detail: launchDetail(res) };
  return JSON.parse(line.slice("PC_PROBE ".length));
}

const unavailable = electronUnavailable();
const unbuilt = unavailable ? null : pageUnbuilt();
const run = unavailable || unbuilt ? null : probe();
const { skip, fail } = electronTier(unavailable || (run && run.launchFailed ? `electron did not start: ${run.detail}` : null));

describe("the buddy's answers in a real Electron", () => {
  if (fail) it("starts a real Electron on this host", () => assert.fail(fail));
  const skipped = skip || unbuilt || (fail ? "this host cannot start Electron: the test above fails with why" : false);

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
