const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  READY_PREFIX,
  SIGN_OUT_PATH,
  makeLocalSignIn,
  movedUrl,
  movesOnRestart,
  parseReadyLine,
  sessionCookieName,
  signOutRequest,
  windowCookie,
} = require("../localSignIn");

/**
 * How the shell signs its windows in to the gateway it spawned. The gateway asks every request for
 * a sign-in; the shell's credential is the owner session the gateway's ready line hands out for
 * this start. These tests drive the module with a recording cookie store in place of Electron's.
 */

/** A sign-in token as the gateway mints it: an opaque string the shell never looks inside. */
const TOKEN = "eyJzdWIiOiJsb2NhbC1zdGFydHVwIn0.c2lnbmVkLWJ5LXRoZS1nYXRld2F5";
const NEXT_TOKEN = "eyJzdWIiOiJsb2NhbC1zdGFydHVwIiwibiI6Mn0.c2lnbmVkLWFnYWlu";

const readyLine = (fields) => `${READY_PREFIX}${JSON.stringify(fields)}`;

function cookieStore() {
  const jar = new Map();
  const calls = [];
  return {
    jar,
    calls,
    async set(details) {
      calls.push(["set", details]);
      jar.set(`${details.url} ${details.name}`, details);
    },
    async remove(url, name) {
      calls.push(["remove", url, name]);
      jar.delete(`${url} ${name}`);
    },
  };
}

describe("the ready line", () => {
  it("names the gateway's port and the sign-in it minted for this start, at the address the gateway listens on", () => {
    const ready = parseReadyLine(readyLine({ port: 51234, token: TOKEN, pid: 7, home: "/tmp/h" }));
    assert.deepStrictEqual(ready, { port: 51234, token: TOKEN, url: "http://127.0.0.1:51234", windowDoor: false });
  });

  it("is the only line that counts", () => {
    for (const line of [
      "Dashboard: http://localhost:51234",
      `  ${readyLine({ port: 51234, token: TOKEN })}`,
      `${READY_PREFIX}not json`,
      readyLine({ port: "51234", token: TOKEN }),
      readyLine({ port: 0, token: TOKEN }),
      readyLine({ port: 70000, token: TOKEN }),
      readyLine({ token: TOKEN }),
      "",
    ]) {
      assert.strictEqual(parseReadyLine(line), null, `read a ready line out of ${JSON.stringify(line)}`);
    }
  });

  it("still names the port when it carries no sign-in, so the window meets the sign-in page", () => {
    assert.deepStrictEqual(parseReadyLine(readyLine({ port: 51234 })), {
      port: 51234,
      token: "",
      url: "http://127.0.0.1:51234",
      windowDoor: false,
    });
  });
});

describe("signing the windows in", () => {
  it("sets the gateway's own session cookie, for its origin only, kept in memory only", async () => {
    const cookies = cookieStore();
    const signIn = makeLocalSignIn({ cookies });
    await signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: TOKEN })));

    assert.strictEqual(cookies.calls.length, 1);
    const [verb, details] = cookies.calls[0];
    assert.strictEqual(verb, "set");
    assert.deepStrictEqual(details, {
      url: "http://127.0.0.1:51234",
      name: "pc_token_51234",
      value: TOKEN,
      path: "/",
      httpOnly: true,
      secure: false,
      sameSite: "lax",
    });
    // NO expiry: a session cookie, which the browser never writes to its cookie file, so the
    // sign-in ends with the app. NO domain: no other host receives it.
    assert.ok(!("expirationDate" in details), "the window's sign-in would be kept on disk");
    assert.ok(!("domain" in details), "the window's sign-in would reach other hosts");
  });

  it("names the cookie the way the gateway does: pc_token_<the port it serves on>", () => {
    assert.strictEqual(sessionCookieName(51234), "pc_token_51234");
    assert.strictEqual(windowCookie({ url: "http://127.0.0.1:9", port: 9, token: TOKEN }).name, "pc_token_9");
  });

  it("puts the same session on the shell's own requests as a Bearer header, and none before", async () => {
    const signIn = makeLocalSignIn({ cookies: cookieStore() });
    assert.deepStrictEqual(signIn.authorization(), {}, "a header before there is a sign-in");
    await signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: TOKEN })));
    assert.deepStrictEqual(signIn.authorization(), { Authorization: `Bearer ${TOKEN}` });
  });

  it("sets nothing for a ready line with no sign-in, and says so without naming a credential", async () => {
    const cookies = cookieStore();
    const said = [];
    const signIn = makeLocalSignIn({ cookies, log: (msg) => said.push(msg) });
    assert.strictEqual(await signIn.adopt(parseReadyLine(readyLine({ port: 51234 }))), null);
    assert.strictEqual(cookies.calls.length, 0);
    assert.deepStrictEqual(signIn.authorization(), {});
    assert.strictEqual(said.length, 1);
  });
});

describe("a restart signs the windows in again", () => {
  it("moves the sign-in to the new port and hands back the one it replaced", async () => {
    const cookies = cookieStore();
    const signIn = makeLocalSignIn({ cookies });
    await signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: TOKEN })));

    const replaced = await signIn.adopt(parseReadyLine(readyLine({ port: 52345, token: NEXT_TOKEN })));

    assert.deepStrictEqual(replaced, { url: "http://127.0.0.1:51234", origin: "http://127.0.0.1:51234", port: 51234, token: TOKEN, pair: null, windowDoor: false, start: null });
    assert.deepStrictEqual([...cookies.jar.keys()], ["http://127.0.0.1:52345 pc_token_52345"]);
    assert.strictEqual(cookies.jar.get("http://127.0.0.1:52345 pc_token_52345").value, NEXT_TOKEN);
    assert.deepStrictEqual(cookies.calls[1], ["remove", "http://127.0.0.1:51234", "pc_token_51234"]);
    assert.deepStrictEqual(signIn.authorization(), { Authorization: `Bearer ${NEXT_TOKEN}` });
  });

  it("replaces the cookie in place when the restarted gateway lands on the same port", async () => {
    const cookies = cookieStore();
    const signIn = makeLocalSignIn({ cookies });
    await signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: TOKEN })));
    const replaced = await signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: NEXT_TOKEN })));

    assert.strictEqual(replaced.token, TOKEN, "the old start's sign-in would never be signed out");
    assert.strictEqual(cookies.jar.size, 1);
    assert.strictEqual(cookies.jar.get("http://127.0.0.1:51234 pc_token_51234").value, NEXT_TOKEN);
  });

  it("replaces nothing when the same ready line is read twice", async () => {
    const signIn = makeLocalSignIn({ cookies: cookieStore() });
    const ready = parseReadyLine(readyLine({ port: 51234, token: TOKEN }));
    await signIn.adopt(ready);
    assert.strictEqual(await signIn.adopt(ready), null);
  });

  it("sends each window that showed the old address to the same route at the new one", () => {
    const old = "http://127.0.0.1:51234";
    const next = "http://127.0.0.1:52345";
    assert.strictEqual(movedUrl(`${old}/#/chat/abc`, old, next), `${next}/#/chat/abc`);
    assert.strictEqual(movedUrl(`${old}/#/inbox?capture=1`, old, next), `${next}/#/inbox?capture=1`);
    // A query is dropped: the dashboard routes in the hash, and a query is where a link's token rides.
    assert.strictEqual(movedUrl(`${old}/?token=abc#/loops`, old, next), `${next}/#/loops`);
    // Anything that was not showing the old gateway stays where it is.
    for (const other of ["http://claw.local:10000/#/chat", "file:///app/loading.html", "about:blank", "", "not a url"]) {
      assert.strictEqual(movedUrl(other, old, next), null, other);
    }
    assert.strictEqual(movedUrl(`${old}/`, "", next), null, "no previous address moves everything");
  });
});

describe("what a restart carries to the new start", () => {
  it("moves the pages that load on the window's cookie alone: the dashboard and an app's own UI", () => {
    for (const [url, moves] of [
      ["http://127.0.0.1:51234/", true],
      ["http://127.0.0.1:51234/#/chat/abc", true],
      ["http://127.0.0.1:51234/apps/notes/ui/", true],
      ["http://127.0.0.1:51234/apps/notes/ui/index.html#/x", true],
      ["http://127.0.0.1:51234/apps/notes/", false],
      ["http://127.0.0.1:51234/apps/notes/api/x", false],
      ["http://127.0.0.1:51234/download/report", false],
      ["http://127.0.0.1:51234/api/files/x?token=t", false],
      ["not a url", false],
    ]) {
      assert.equal(movesOnRestart(url), moves, url);
    }
  });
});

describe("quitting", () => {
  it("drops the sign-in and its cookie, and hands back the session to sign out", async () => {
    const cookies = cookieStore();
    const signIn = makeLocalSignIn({ cookies });
    await signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: TOKEN })));

    const held = await signIn.release();

    assert.strictEqual(held.token, TOKEN);
    assert.strictEqual(cookies.jar.size, 0);
    assert.deepStrictEqual(signIn.authorization(), {});
    assert.strictEqual(await signIn.release(), null, "a second release ends nothing");
  });

  it("signs a session out with its own cookie, at the gateway being asked", () => {
    // After a restart the old session is signed out at the NEW gateway, whose cookie name follows
    // its own port; the session itself is the old one.
    const request = signOutRequest({ url: "http://127.0.0.1:51234", port: 51234, token: TOKEN }, 52345);
    assert.deepStrictEqual(request, {
      path: SIGN_OUT_PATH,
      headers: { Cookie: `pc_token_52345=${TOKEN}` },
    });
    assert.strictEqual(SIGN_OUT_PATH, "/api/auth/logout");
    assert.strictEqual(request.path.includes(TOKEN), false, "the token rides in the URL");
  });
});

describe("the credential stays in this module", () => {
  it("never names the token in anything it says", async () => {
    const said = [];
    const failing = {
      async set() {
        throw new Error("the cookie store refused the write");
      },
      async remove() {
        throw new Error("the cookie store refused the removal");
      },
    };
    const signIn = makeLocalSignIn({ cookies: failing, log: (msg) => said.push(msg) });
    const pair = "pair-that-must-never-be-said";
    await signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: TOKEN })));
    await signIn.adopt(parseReadyLine(readyLine({ port: 52345, token: NEXT_TOKEN })), { pair }).catch(() => {});
    await signIn.withhold().catch(() => {});
    await signIn.end(signIn.stamp(), { defect: true }).catch(() => {});
    await signIn.release().catch(() => {});
    assert.ok(said.length > 0, "the failures were not reported at all");
    for (const line of said) {
      assert.strictEqual(line.includes(TOKEN) || line.includes(NEXT_TOKEN) || line.includes(pair), false, line);
    }
  });

  it("has no Electron import, so it is testable without a display", () => {
    const src = fs.readFileSync(path.join(__dirname, "..", "localSignIn.js"), "utf8");
    assert.strictEqual(/require\("electron"\)/.test(src), false);
  });
});

// ── the record the windows' traffic reads ───────────────────────────────────────────────────────

/** A cookie store whose writes and removals wait until the test lets each one through. */
function deferredStore() {
  const jar = new Map();
  const calls = [];
  const pending = [];
  const hold = (apply) =>
    new Promise((resolve) => {
      pending.push(() => {
        apply();
        resolve();
      });
    });
  return {
    jar,
    calls,
    pending,
    set(details) {
      calls.push(["set", details.value]);
      return hold(() => jar.set(details.name, details.value));
    },
    remove(_url, name) {
      calls.push(["remove", name]);
      return hold(() => jar.delete(name));
    },
    /** Let the oldest waiting write or removal through. */
    next() {
      const step = pending.shift();
      if (step) step();
      return new Promise((r) => setImmediate(r));
    },
  };
}

const READY = () => parseReadyLine(readyLine({ port: 51234, token: TOKEN }));
const PAIR = "pair-of-the-window-session";

describe("a lost gateway: the sign-in is withheld, its cookie leaves, the session stays held", () => {
  it("withholds and moves the generation on before anything is awaited, and closes connections before the cookie goes", async () => {
    const cookies = deferredStore();
    const signIn = makeLocalSignIn({ cookies });
    const adopting = signIn.adopt(READY());
    await cookies.next();
    await adopting;
    const before = signIn.gen;
    const order = [];
    const done = signIn.withhold(() => order.push(["closeAll", signIn.withheld, cookies.calls.length]));
    assert.equal(signIn.withheld, true, "withheld before the first await");
    assert.equal(signIn.gen, before + 1);
    assert.deepEqual(order, [["closeAll", true, 1]], "closeAll ran before the cookie's removal was asked");
    assert.deepEqual(cookies.calls.at(-1), ["remove", "pc_token_51234"]);
    await cookies.next();
    await done;
    assert.equal(cookies.jar.has("pc_token_51234"), false);
    assert.deepEqual(signIn.held, { url: "http://127.0.0.1:51234", port: 51234, pair: null }, "the session is still held");
  });

  it("writes the same session back on a same-start proof, and lets the windows in only once the write resolved", async () => {
    const cookies = cookieStore();
    const signIn = makeLocalSignIn({ cookies });
    await signIn.adopt(READY());
    const gen = await signIn.withhold();
    assert.equal(await signIn.lift(gen), true);
    assert.equal(signIn.withheld, false);
    assert.equal(cookies.jar.get("http://127.0.0.1:51234 pc_token_51234").value, TOKEN, "nothing minted: the same token");
  });

  it("keeps the windows out when a loss arrives while the write back is pending", async () => {
    const cookies = deferredStore();
    const signIn = makeLocalSignIn({ cookies });
    const a = signIn.adopt(READY());
    await cookies.next();
    await a;
    const w = signIn.withhold();
    await cookies.next();
    const gen = await w;
    const lifting = signIn.lift(gen);
    const again = signIn.withhold();
    await cookies.next(); // the write back
    await cookies.next(); // the second loss's removal, asked after the write, so it wins
    // Before the lift is awaited: a removal it asked after the second loss's would hold it.
    assert.deepEqual(cookies.calls.slice(-2), [["set", TOKEN], ["remove", "pc_token_51234"]]);
    assert.equal(await lifting, false);
    await again;
    assert.equal(signIn.withheld, true);
    assert.equal(cookies.jar.has("pc_token_51234"), false, "no session cookie in the jar while withheld");
  });

  it("refuses a lift for a generation that moved on, or a retired session", async () => {
    const signIn = makeLocalSignIn({ cookies: cookieStore() });
    await signIn.adopt(READY());
    const gen = await signIn.withhold();
    await signIn.withhold();
    assert.equal(await signIn.lift(gen), false);
    const retiredGen = await signIn.withhold(() => {}, { retire: true });
    assert.equal(await signIn.lift(retiredGen), false);
    assert.deepEqual(signIn.authorization(), {}, "a retired session is carried by nothing");
    assert.equal(signIn.trafficState().retired.has("http://127.0.0.1:51234"), true);
  });

  it("an origin held again leaves the retired ones", async () => {
    const signIn = makeLocalSignIn({ cookies: cookieStore() });
    await signIn.adopt(READY());
    await signIn.withhold(() => {}, { retire: true });
    await signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: NEXT_TOKEN })));
    assert.equal(signIn.trafficState().retired.size, 0);
    assert.equal(signIn.withheld, false);
  });

  it("a refused cookie write still hands back the session it replaced, to be signed out", async () => {
    const said = [];
    const refusing = { async set() { throw new Error("the cookie store refused the write"); }, async remove() {} };
    const signIn = makeLocalSignIn({ cookies: refusing, log: (m) => said.push(m) });
    await signIn.adopt(READY());
    const replaced = await signIn.adopt(parseReadyLine(readyLine({ port: 52345, token: NEXT_TOKEN })));
    assert.equal(replaced && replaced.token, TOKEN);
    assert.ok(said.some((m) => m.includes("could not sign the window in")));
  });
});

describe("what the shell's own requests carry, and what ends it", () => {
  it("is the held session as a Bearer, with its pair once the window's door gave one", async () => {
    const signIn = makeLocalSignIn({ cookies: cookieStore() });
    await signIn.adopt(READY());
    assert.deepEqual(signIn.authorization(), { Authorization: `Bearer ${TOKEN}` });
    await signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: NEXT_TOKEN })), { pair: PAIR });
    assert.deepEqual(signIn.authorization(), { Authorization: `Bearer ${NEXT_TOKEN}`, "X-PersonalClaw-Pair": PAIR });
  });

  it("holds the ready session for the shell alone until the window's own session is written", async () => {
    const cookies = cookieStore();
    const signIn = makeLocalSignIn({ cookies });
    assert.equal(await signIn.holdForShell(READY()), null);
    assert.equal(signIn.withheld, true, "no window loads before the bound cookie is written");
    assert.equal(cookies.calls.length, 0, "no window cookie for the ready session");
    assert.deepEqual(signIn.authorization(), { Authorization: `Bearer ${TOKEN}` });
    const replaced = await signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: NEXT_TOKEN })), { pair: PAIR });
    assert.equal(replaced.token, TOKEN, "the ready session is handed back to be signed out");
    assert.equal(signIn.withheld, false);
    assert.equal(cookies.jar.get("http://127.0.0.1:51234 pc_token_51234").value, NEXT_TOKEN);
  });

  it("ends only the current sign-in, once, and only while it has a pair", async () => {
    const signIn = makeLocalSignIn({ cookies: cookieStore() });
    await signIn.adopt(READY());
    assert.equal(await signIn.end(signIn.stamp()), false, "a gateway without the door: handled as today");
    await signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: NEXT_TOKEN })), { pair: PAIR });
    const stale = { token: TOKEN, gen: signIn.gen - 1 };
    assert.equal(await signIn.end(stale), false, "an answer to a request sent before the rotation");
    const now = signIn.stamp();
    assert.equal(await signIn.end(now), true);
    assert.equal(await signIn.end(now), false, "counted once");
    assert.equal(signIn.signedOut, true);
    assert.deepEqual(signIn.authorization(), {});
  });

  it("a defect takes the cookie and the pair away as it ends the sign-in", async () => {
    const cookies = cookieStore();
    const signIn = makeLocalSignIn({ cookies });
    await signIn.adopt(READY(), { pair: PAIR });
    assert.equal(await signIn.end(signIn.stamp(), { defect: true }), true);
    assert.equal(signIn.held.pair, null);
    assert.equal(cookies.jar.size, 0);
  });

  it("after a defect, the sign-out on quitting is that session's Bearer alone, with no pair", async () => {
    const signIn = makeLocalSignIn({ cookies: cookieStore() });
    await signIn.adopt(READY(), { pair: PAIR });
    await signIn.end(signIn.stamp(), { defect: true });
    const session = await signIn.release();
    assert.deepEqual(signOutRequest(session, 51234, { windowDoor: true }).headers, { Authorization: `Bearer ${TOKEN}` });
  });

  it("drops the pair from the windows' traffic once their cookie holds another sign-in", async () => {
    const signIn = makeLocalSignIn({ cookies: cookieStore() });
    await signIn.adopt(READY(), { pair: PAIR });
    signIn.cookieChanged({ name: "pc_token_51234", domain: "127.0.0.1", value: "a-password-sign-in" }, false);
    assert.equal(signIn.trafficState().cookieFollows, false);
    signIn.cookieChanged({ name: "pc_token_51234", domain: "127.0.0.1", value: TOKEN }, false);
    assert.equal(signIn.trafficState().cookieFollows, true);
  });

  it("signs out at a gateway with the window's door by Bearer, with the session's own pair", () => {
    const flagged = signOutRequest({ token: TOKEN, pair: PAIR }, 51234, { windowDoor: true });
    assert.deepEqual(flagged.headers, { Authorization: `Bearer ${TOKEN}`, "X-PersonalClaw-Pair": PAIR });
    const ready = signOutRequest({ token: TOKEN, pair: null }, 51234, { windowDoor: true });
    assert.deepEqual(ready.headers, { Authorization: `Bearer ${TOKEN}` });
    assert.deepEqual(signOutRequest({ token: TOKEN }, 51234).headers, { Cookie: `pc_token_51234=${TOKEN}` });
  });

  it("reads the window door's flag off the ready line", () => {
    assert.equal(parseReadyLine(readyLine({ port: 51234, token: TOKEN, window_door: true })).windowDoor, true);
    assert.equal(parseReadyLine(readyLine({ port: 51234, token: TOKEN, window_door: "yes" })).windowDoor, false);
  });
});

describe("the shell's own hold, while the window's door hasn't given the windows a session", () => {
  const setsOf = (cookies) => cookies.calls.filter(([op]) => op === "set");

  it("is lifted for the shell alone on a same-start proof: no window cookie, and the windows stay withheld", async () => {
    const cookies = cookieStore();
    const signIn = makeLocalSignIn({ cookies });
    await signIn.holdForShell(READY());
    const gen = await signIn.withhold();
    assert.equal(await signIn.lift(gen), true, "the shell's own credentials come back");
    assert.deepEqual(setsOf(cookies), [], "the ready line's session was written into the jar");
    assert.equal(signIn.withheld, true, "the windows were let in on the ready line's session");
    assert.deepEqual(signIn.authorization(), { Authorization: `Bearer ${TOKEN}` });
    assert.equal(signIn.forShell, true);
    await signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: NEXT_TOKEN })), { pair: PAIR });
    assert.equal(signIn.forShell, false, "the window's own session is not the shell's hold");
  });

  it("lifts nothing while signed out", async () => {
    const cookies = cookieStore();
    const signIn = makeLocalSignIn({ cookies });
    await signIn.adopt(READY(), { pair: PAIR });
    assert.equal(await signIn.end(signIn.stamp()), true);
    const gen = await signIn.withhold();
    const sets = setsOf(cookies).length;
    assert.equal(await signIn.lift(gen), false);
    assert.equal(setsOf(cookies).length, sets, "an ended session was written back");
    assert.equal(signIn.withheld, true);
  });
});

describe("the record's writes and lifts, keyed to the session's own start", () => {
  const DOOR = (fields) => parseReadyLine(readyLine({ window_door: true, ...fields }));
  const START = { pid: 700, port: 51234, secretDigest: "digest-of-start-1" };
  const setsOf = (cookies) => cookies.calls.filter(([op]) => op === "set");

  it("writes nothing once the generation moved before the cookie's write, and still hands back what it replaced", async () => {
    const cookies = deferredStore();
    const signIn = makeLocalSignIn({ cookies });
    const a = signIn.adopt(READY());
    await cookies.next();
    await a;
    const moving = signIn.adopt(parseReadyLine(readyLine({ port: 52345, token: NEXT_TOKEN })));
    const loss = signIn.withhold(); // the generation moves while the old cookie's removal is out
    await cookies.next(); // adopt's removal of the old port's cookie
    // Before the adopt is awaited: a write on the moved generation would hold it.
    assert.deepEqual(cookies.calls.filter(([op, v]) => op === "set" && v === NEXT_TOKEN), [], "adopt wrote on a moved generation");
    const replaced = await moving;
    await cookies.next(); // the loss's removal
    await loss;
    assert.equal(replaced && replaced.token, TOKEN, "the session it replaced was not handed back");
    assert.deepEqual(cookies.calls.filter(([op, v]) => op === "set" && v === NEXT_TOKEN), [], "adopt wrote on a moved generation");
    assert.equal(cookies.jar.size, 0);
  });

  it("puts no pairless session in the jar on a gateway with the window's door, on adopt or on a lift", async () => {
    const cookies = cookieStore();
    const signIn = makeLocalSignIn({ cookies });
    await signIn.adopt(DOOR({ port: 51234, token: TOKEN }), { pair: null, start: START });
    assert.deepEqual(setsOf(cookies), [], "a pairless session went in the jar");
    assert.equal(signIn.withheld, true, "the windows were let in without a pair");
    const gen = await signIn.withhold();
    assert.equal(await signIn.lift(gen, { start: START }), true, "the shell's own credentials");
    assert.deepEqual(setsOf(cookies), [], "a lift wrote a pairless session");
    assert.equal(signIn.withheld, true);
  });

  it("lifts only for the start the held session came from", async () => {
    const cookies = cookieStore();
    const signIn = makeLocalSignIn({ cookies });
    await signIn.adopt(READY(), { start: START });
    assert.deepEqual(signIn.start, START);
    const gen = await signIn.withhold();
    const sets = setsOf(cookies).length;
    for (const other of [{ ...START, secretDigest: "digest-of-start-2" }, { ...START, pid: 701 }, { ...START, port: 51235 }]) {
      assert.equal(await signIn.lift(gen, { start: other }), false, JSON.stringify(other));
    }
    assert.equal(setsOf(cookies).length, sets, "another start's proof wrote the session back");
    assert.equal(await signIn.lift(gen, { start: { ...START } }), true);
    assert.equal(signIn.withheld, false);
  });

  it("reads a cookie change only for the held origin's own host", async () => {
    const signIn = makeLocalSignIn({ cookies: cookieStore() });
    await signIn.adopt(READY(), { pair: PAIR });
    signIn.cookieChanged({ name: "pc_token_51234", domain: "localhost", value: "another-hosts-session" }, false);
    signIn.cookieChanged({ name: "pc_token_51234", domain: ".pc.example.test", value: "another-hosts-session" }, false);
    assert.equal(signIn.trafficState().cookieFollows, true, "a cookie of the same name on another host turned the pair off");
    signIn.cookieChanged({ name: "pc_token_51234", domain: "127.0.0.1", value: "a-password-sign-in" }, false);
    assert.equal(signIn.trafficState().cookieFollows, false);
  });

  it("follows the held session again once a lift wrote its cookie back", async () => {
    const signIn = makeLocalSignIn({ cookies: cookieStore() });
    await signIn.adopt(READY(), { pair: PAIR });
    signIn.cookieChanged({ name: "pc_token_51234", domain: "127.0.0.1", value: "a-password-sign-in" }, false);
    const gen = await signIn.withhold();
    assert.equal(await signIn.lift(gen), true);
    assert.equal(signIn.trafficState().cookieFollows, true);
  });

  it("leaves a newer session's cookie on the same port alone when its lift saw the generation move", async () => {
    const cookies = deferredStore();
    const signIn = makeLocalSignIn({ cookies });
    const a = signIn.adopt(READY());
    await cookies.next();
    await a;
    const w = signIn.withhold();
    await cookies.next();
    const gen = await w;
    const lifting = signIn.lift(gen);
    const newer = signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: NEXT_TOKEN })));
    await cookies.next(); // the lift's write
    await cookies.next(); // the newer session's write
    await cookies.next(); // any removal the lift asked after it, let through before the lift is awaited
    assert.equal(await lifting, false);
    await newer;
    assert.equal(cookies.jar.get("pc_token_51234"), NEXT_TOKEN, "the lift removed the newer session's cookie");
    assert.deepEqual(cookies.calls.filter(([op]) => op === "remove").slice(1), [], "a removal after the newer write");
  });

  it("removes its own write when the generation moved to no session, or one on another port", async () => {
    const cookies = deferredStore();
    const signIn = makeLocalSignIn({ cookies });
    const a = signIn.adopt(READY());
    await cookies.next();
    await a;
    const w = signIn.withhold();
    await cookies.next();
    const gen = await w;
    const lifting = signIn.lift(gen);
    const releasing = signIn.release();
    await cookies.next(); // the lift's write
    await cookies.next(); // release's removal
    await cookies.next(); // the lift's own removal
    assert.equal(await lifting, false);
    await releasing;
    assert.equal(cookies.jar.has("pc_token_51234"), false);
  });

  it("lifts no session that names its start for a proof that names none", async () => {
    const signIn = makeLocalSignIn({ cookies: cookieStore() });
    await signIn.adopt(READY(), { start: START });
    const gen = await signIn.withhold();
    assert.equal(await signIn.lift(gen), false, "a proof that named no start lifted a session that names one");
    assert.equal(await signIn.lift(gen, { start: START }), true);
  });

  it("keeps the shell's own hold withheld on a lift, with no token as with one", async () => {
    const cookies = cookieStore();
    const signIn = makeLocalSignIn({ cookies });
    await signIn.holdForShell(parseReadyLine(readyLine({ port: 51234 })), { start: START });
    const gen = await signIn.withhold();
    assert.equal(await signIn.lift(gen, { start: START }), true);
    assert.equal(signIn.withheld, true, "the shell's token-less hold let the windows in");
    assert.deepEqual(setsOf(cookies), []);
  });

  it("takes the session it replaced on the same port out of the jar when the new one has no pair at a door", async () => {
    const cookies = cookieStore();
    const signIn = makeLocalSignIn({ cookies });
    await signIn.adopt(DOOR({ port: 51234, token: TOKEN }), { pair: PAIR, start: START });
    assert.equal(cookies.jar.size, 1);
    await signIn.adopt(DOOR({ port: 51234, token: NEXT_TOKEN }), { pair: null, start: START });
    assert.equal(cookies.jar.size, 0, "the replaced session's cookie stayed in the jar");
    assert.equal(signIn.withheld, true);
  });

  it("keeps the windows out while the replaced session's removal is still out, when the new one has no pair at a door", async () => {
    const cookies = deferredStore();
    const signIn = makeLocalSignIn({ cookies });
    const first = signIn.adopt(DOOR({ port: 51234, token: TOKEN }), { pair: PAIR, start: START });
    await cookies.next();
    await first;
    assert.equal(signIn.withheld, false);
    const pairless = signIn.adopt(DOOR({ port: 51234, token: NEXT_TOKEN }), { pair: null, start: START });
    // The removal of the old session's cookie is out, answered on a later step.
    assert.deepEqual(cookies.calls.at(-1), ["remove", "pc_token_51234"]);
    assert.equal(signIn.trafficState().withheld, true, "the windows were let in while the old cookie's removal was out");
    await cookies.next();
    await pairless;
    assert.equal(signIn.trafficState().withheld, true);
    assert.equal(cookies.jar.size, 0);
  });

  it("reads a cookie change only for the root path", async () => {
    const signIn = makeLocalSignIn({ cookies: cookieStore() });
    await signIn.adopt(READY(), { pair: PAIR });
    signIn.cookieChanged({ name: "pc_token_51234", domain: "127.0.0.1", path: "/x", value: "another-paths-session" }, false);
    assert.equal(signIn.trafficState().cookieFollows, true, "a cookie of the same name on another path turned the pair off");
  });

  it("lifts a hold without a token on a same-start proof, with nothing in the jar", async () => {
    const cookies = cookieStore();
    const signIn = makeLocalSignIn({ cookies });
    await signIn.adopt(parseReadyLine(readyLine({ port: 51234 })), { start: START });
    const gen = await signIn.withhold();
    assert.equal(signIn.withheld, true);
    assert.equal(await signIn.lift(gen, { start: START }), true);
    assert.equal(signIn.withheld, false, "a token-less hold kept its windows out for good");
    assert.deepEqual(setsOf(cookies), []);
  });
});

describe("the traffic state", () => {
  it("never lists the held origin or a retired one among the gateways the owner added", async () => {
    const signIn = makeLocalSignIn({ cookies: cookieStore() });
    await signIn.adopt(READY());
    signIn.setPaired(["http://127.0.0.1:51234", "http://127.0.0.1:10000", "https://pc.example.test"]);
    const open = signIn.trafficState().paired;
    assert.equal(open.has("http://127.0.0.1:51234"), false);
    assert.equal(open.has("http://127.0.0.1:10000"), true);
  });
});
