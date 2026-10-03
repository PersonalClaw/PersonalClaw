const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  READY_PREFIX,
  SIGN_OUT_PATH,
  makeLocalSignIn,
  movedUrl,
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
  it("names the gateway's port and the sign-in it minted for this start", () => {
    const ready = parseReadyLine(readyLine({ port: 51234, token: TOKEN, pid: 7, home: "/tmp/h" }));
    assert.deepStrictEqual(ready, { port: 51234, token: TOKEN, url: "http://localhost:51234" });
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
      url: "http://localhost:51234",
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
      url: "http://localhost:51234",
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
    assert.strictEqual(windowCookie({ url: "http://localhost:9", port: 9, token: TOKEN }).name, "pc_token_9");
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

    assert.deepStrictEqual(replaced, { url: "http://localhost:51234", port: 51234, token: TOKEN });
    assert.deepStrictEqual([...cookies.jar.keys()], ["http://localhost:52345 pc_token_52345"]);
    assert.strictEqual(cookies.jar.get("http://localhost:52345 pc_token_52345").value, NEXT_TOKEN);
    assert.deepStrictEqual(cookies.calls[1], ["remove", "http://localhost:51234", "pc_token_51234"]);
    assert.deepStrictEqual(signIn.authorization(), { Authorization: `Bearer ${NEXT_TOKEN}` });
  });

  it("replaces the cookie in place when the restarted gateway lands on the same port", async () => {
    const cookies = cookieStore();
    const signIn = makeLocalSignIn({ cookies });
    await signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: TOKEN })));
    const replaced = await signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: NEXT_TOKEN })));

    assert.strictEqual(replaced.token, TOKEN, "the old start's sign-in would never be signed out");
    assert.strictEqual(cookies.jar.size, 1);
    assert.strictEqual(cookies.jar.get("http://localhost:51234 pc_token_51234").value, NEXT_TOKEN);
  });

  it("replaces nothing when the same ready line is read twice", async () => {
    const signIn = makeLocalSignIn({ cookies: cookieStore() });
    const ready = parseReadyLine(readyLine({ port: 51234, token: TOKEN }));
    await signIn.adopt(ready);
    assert.strictEqual(await signIn.adopt(ready), null);
  });

  it("sends each window that showed the old address to the same route at the new one", () => {
    const old = "http://localhost:51234";
    const next = "http://localhost:52345";
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
    const request = signOutRequest({ url: "http://localhost:51234", port: 51234, token: TOKEN }, 52345);
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
    await assert.rejects(signIn.adopt(parseReadyLine(readyLine({ port: 51234, token: TOKEN }))));
    await signIn.adopt(parseReadyLine(readyLine({ port: 52345, token: NEXT_TOKEN }))).catch(() => {});
    await signIn.release().catch(() => {});
    assert.ok(said.length > 0, "the failures were not reported at all");
    for (const line of said) {
      assert.strictEqual(line.includes(TOKEN) || line.includes(NEXT_TOKEN), false, line);
    }
  });

  it("has no Electron import, so it is testable without a display", () => {
    const src = fs.readFileSync(path.join(__dirname, "..", "localSignIn.js"), "utf8");
    assert.strictEqual(/require\("electron"\)/.test(src), false);
  });
});
