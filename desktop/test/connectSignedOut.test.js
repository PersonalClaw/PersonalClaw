/**
 * A paired gateway that signs this app out is SEEN, and the switcher says so in the gateway's own
 * words (ledger 286).
 *
 * Measured before this: the switcher's only refusal state, "Needs pairing again", came from the
 * credential-free `/api/healthz` probe answering 401/403 — which a PersonalClaw gateway never does,
 * because `/api/healthz` is auth-exempt. So a desktop app signed out from Settings → Devices on
 * another device showed the gateway's sentence in its window (#3727) while its row still read
 * "Reachable", and the docs described a state no revoked session produces.
 *
 * The shell still never presents a credential of its own. It watches the WebView's completed
 * requests — status and headers only — for the gateway refusing THIS app's sign-in (`401`/`403`
 * with `X-Auth-Required: true`), and asks the PAGE, which holds the session cookie, for the
 * sentence, exactly as `adoptInstanceName` asks it for the gateway's name.
 */

const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  HEALTH_STATES,
  HEALTH_SIGNED_OUT,
  HEALTH_REFUSED,
  NOTICE_MAX,
  sessionEventFrom,
  sessionRefusal,
  signedOutHealth,
} = require("../connectMode");
const { healthCopy, describeRow } = require("../connectDialog");

describe("sessionRefusal — the gateway refusing THIS app's sign-in", () => {
  it("is a 401 or 403 that carries X-Auth-Required, however the header is cased", () => {
    assert.equal(sessionRefusal({ statusCode: 403, responseHeaders: { "X-Auth-Required": ["true"] } }), true);
    assert.equal(sessionRefusal({ statusCode: 401, responseHeaders: { "x-auth-required": ["true"] } }), true);
  });

  it("is not a route refusing an action, not a success, and not garbage", () => {
    assert.equal(sessionRefusal({ statusCode: 403, responseHeaders: {} }), false);
    assert.equal(sessionRefusal({ statusCode: 403, responseHeaders: { "X-Auth-Required": ["false"] } }), false);
    assert.equal(sessionRefusal({ statusCode: 200, responseHeaders: { "X-Auth-Required": ["true"] } }), false);
    assert.equal(sessionRefusal({}), false);
    assert.equal(sessionRefusal(null), false);
  });
});

describe("the signed-out row", () => {
  it("carries the gateway's sentence, cleaned of control characters and clamped", () => {
    const h = signedOutHealth("This device was signed out today at 09:14.\u0007\u001b[2J", 403);
    assert.equal(h.status, HEALTH_SIGNED_OUT);
    assert.equal(h.httpStatus, 403);
    assert.equal(h.message, "This device was signed out today at 09:14.[2J");
    assert.ok(signedOutHealth("x".repeat(5000), 403).message.length <= NOTICE_MAX);
    assert.equal(signedOutHealth(undefined, 403).message, "");
  });

  it("reads Signed out, with the sentence beside it", () => {
    const row = describeRow(
      { id: "ep", label: "Work", base_url: "http://claw.local:10000", kind: "remote" },
      { activeId: "ep", health: { ep: signedOutHealth("This device was signed out today at 09:14.", 403) } }
    );
    assert.equal(row.status, HEALTH_SIGNED_OUT);
    assert.equal(row.statusText, "Signed out");
    assert.equal(row.statusDetail, "This device was signed out today at 09:14.");
  });

  it("is the ONE state that asks the user to act", () => {
    const acting = HEALTH_STATES.filter((s) => healthCopy(s).tone === "act");
    assert.deepEqual(acting, [HEALTH_SIGNED_OUT]);
  });

  it("does not call a refused health check a pairing problem — pairing again would not fix it", () => {
    assert.ok(HEALTH_STATES.includes(HEALTH_REFUSED), "the refused state exists");
    assert.doesNotMatch(healthCopy(HEALTH_REFUSED).text, /pair/i);
  });
});

describe("sessionEventFrom — what one response means for the active row", () => {
  const origin = "http://claw.local:10000";
  const refused = { "X-Auth-Required": ["true"] };

  it("a refusal of this app's sign-in on the active gateway is a sign-out", () => {
    const d = { url: `${origin}/api/status`, statusCode: 403, responseHeaders: refused, resourceType: "xhr" };
    assert.equal(sessionEventFrom(d, origin), "signed_out");
  });

  it("the same refusal from another gateway is not this row's", () => {
    const d = { url: "http://other.local:10000/api/status", statusCode: 403, responseHeaders: refused };
    assert.equal(sessionEventFrom(d, origin), "");
  });

  it("a page of the gateway loading is being signed in again — but not the pairing page", () => {
    assert.equal(sessionEventFrom({ url: `${origin}/`, statusCode: 200, resourceType: "mainFrame" }, origin), "signed_in");
    assert.equal(sessionEventFrom({ url: `${origin}/pair?code=AB12-CD34`, statusCode: 200, resourceType: "mainFrame" }, origin), "");
    assert.equal(sessionEventFrom({ url: `${origin}/assets/x.js`, statusCode: 200, resourceType: "script" }, origin), "");
  });
});

describe("main.js sees the page's own refusals (source scan; each pattern floors the next)", () => {
  const MAIN = fs.readFileSync(path.join(__dirname, "..", "main.js"), "utf8");

  it("watches the WebView's completed requests and reads each through sessionEventFrom", () => {
    assert.match(MAIN, /webRequest\.onCompleted\(/);
    assert.match(MAIN, /sessionEventFrom\(details, origin\)/);
  });

  it("asks the PAGE for the sentence, same-origin, and records it as the row's health", () => {
    assert.match(MAIN, /credentials:\s*'same-origin'/);
    assert.match(MAIN, /signedOutHealth\(/);
  });
});
