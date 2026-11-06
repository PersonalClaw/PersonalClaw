/**
 * The windows' traffic in a real Electron (`helpers/windowTrafficProbe.js`): what reaches a stand-in
 * gateway on 127.0.0.1, with which pair, while the windows' sign-in is held and while it is
 * withheld. The table test (`windowTraffic.test.js`) says what `decide` answers; this says that a
 * real Chromium asks it for every request, with the kinds and initiators the table assumes.
 *
 * Skips where Electron cannot start, unless `PCLAW_REQUIRE_ELECTRON=1` says the host must run it
 * (`helpers/electronHost.js`).
 */
const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { electronCommand, electronTier, electronUnavailable, launchDetail } = require("./helpers/electronHost");

const PAIR = "pair-of-the-window-session";

function probe() {
  const { command, args } = electronCommand([path.join(__dirname, "helpers", "windowTrafficProbe.js")]);
  const res = spawnSync(command, args, { timeout: 120_000, encoding: "utf8", env: { ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: "1" } });
  const line = (res.stdout || "").split("\n").find((l) => l.startsWith("PC_PROBE "));
  if (!line) return { launchFailed: true, detail: launchDetail(res) };
  return JSON.parse(line.slice("PC_PROBE ".length));
}

const unavailable = electronUnavailable();
const seen = unavailable ? null : probe();
const { skip, fail } = electronTier(unavailable || (seen && seen.launchFailed ? `electron did not start: ${seen.detail}` : null));

describe("the windows' traffic in a real Electron", () => {
  if (fail) it("starts a real Electron on this host", () => assert.fail(fail));
  const skipped = skip || (fail ? "this host cannot start Electron: the test above fails with why" : false);
  const one = (pathname, method) => seen.held.find((r) => r.path === pathname && (!method || r.method === method));

  it("ran without error", { skip: skipped }, () => {
    assert.equal(seen.error, undefined, seen.error);
  });

  it("puts the pair on the held origin's own page's fetch, EventSource, socket upgrade and keepalive DELETE", { skip: skipped }, () => {
    assert.equal(one("/api/fetch").pair, PAIR);
    assert.equal(one("/api/sse").pair, PAIR, "an EventSource is not an xhr to webRequest");
    assert.equal(one("/api/ws", "UPGRADE").pair, PAIR, "the header was left off the upgrade");
    assert.equal(one("/api/upload", "DELETE").pair, PAIR);
  });

  it("puts the window session's pair over one the held origin's own page sent", { skip: skipped }, () => {
    const own = one("/api/own");
    assert.ok(own, "the page's own-pair fetch never arrived");
    assert.equal(own.pair, PAIR, "the page's own pair went out in place of the window session's");
  });

  it("puts none on main's own load of the page, or on a sandboxed frame's fetch", { skip: skipped }, () => {
    assert.ok(seen.root, "the page was never loaded");
    assert.equal(seen.root.pair, null, "main's loadURL carried the pair");
    const sandboxed = one("/api/sandboxed", "GET");
    assert.ok(sandboxed, "the sandboxed frame's fetch never arrived: the case would pass blind");
    assert.equal(sandboxed.pair, null, "a \"null\" initiator carried the pair");
    assert.equal(one("/api/sandboxed", "OPTIONS"), undefined, "the sandboxed frame's fetch was preflighted: its GET is not the one the rule answered");
  });

  it("puts none on a beacon, a download, or a service worker's fetch, which no window made", { skip: skipped }, () => {
    for (const [pathname, method] of [["/api/beacon", "POST"], ["/api/download", "GET"], ["/api/worker", "GET"]]) {
      const r = one(pathname, method);
      assert.ok(r, `${method} ${pathname} never arrived while held: its silence while withheld would prove nothing`);
      assert.equal(r.pair, null, `${method} ${pathname} carried the pair`);
    }
  });

  it("lets nothing a window sends reach the held origin while the sign-in is withheld, while the control does", { skip: skipped }, () => {
    assert.equal(seen.control, true, "the control did not reach the stand-in: the silence below proves nothing");
    assert.deepEqual(
      seen.withheld.map((r) => `${r.method} ${r.path}`),
      [],
      "requests reached the gateway while the windows' sign-in was withheld"
    );
    assert.equal(seen.jarAfterWithhold, 0, "the window cookie stayed in the jar after the withhold");
  });

  // What it shows is Chromium's: on Electron 44 a redirect's next request goes without the pair either
  // way, so this row can't tell the app's strip from Chromium's (`windowTraffic.test.js` pins the strip).
  it("the hop arrives at another origin without the pair", { skip: skipped }, () => {
    const hop = one("/api/hop", "GET");
    assert.ok(hop, "the hop never left the page");
    assert.equal(hop.pair, PAIR, "the held page's own request went without the pair");
    assert.equal(one("/api/hop", "OPTIONS"), undefined, "the hop was preflighted");
    const landed = seen.landed.find((r) => r.path === "/landing" && r.method === "GET");
    assert.ok(landed, "the redirect never arrived: the case would pass blind");
    assert.equal(landed.pair, null, "the pair crossed to another origin on a redirect");
    assert.ok(!seen.landed.some((r) => r.method === "OPTIONS"), "the redirect's hop was preflighted");
  });

  it("sets the window session's pair over a socket's own subprotocol while the jar holds it, and none once it holds another session", { skip: skipped }, (t) => {
    const followed = seen.followed.find((r) => r.method === "UPGRADE");
    assert.ok(followed, "the followed upgrade never arrived");
    assert.match(String(followed.protocol), /pc-pair\./, "the page's subprotocol was not offered");
    assert.equal(followed.pair, PAIR, "the page's own subprotocol stood in for the window session's pair");
    assert.equal(seen.cookieFollowsAfterAnother, false, "the jar's change never reached the record: the case below is blind");
    const another = seen.another.find((r) => r.method === "UPGRADE");
    assert.ok(another, "the upgrade after another session's cookie never arrived");
    assert.equal(another.pair, null, "the window session's pair went over another session's cookie");
    // Recorded only: whether webRequest shows an upgrade's subprotocols (the rule doesn't read them).
    t.diagnostic(`webRequest shows an upgrade's Sec-WebSocket-Protocol: ${JSON.stringify(seen.upgradeHeaders)}`);
    assert.ok(seen.upgradeHeaders.length > 0 && seen.upgradeHeaders.every((b) => typeof b === "boolean"));
  });
});
