const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const { PAGE_OWN_PAIR_HEADERS, PAGE_PAIR_KEY, PAIR_CHANGED, PAIR_HEADER, PAIR_SHAPE, decide, headersFor, isLoopbackOrigin, originKey, registerWindowTraffic } = require("../windowTraffic");

const HELD = "http://127.0.0.1:51234";
const PAIR = "pair-of-the-window-session";

/** The windows' sign-in state, held on *HELD* with a pair, not withheld. */
function state(over = {}) {
  return {
    held: { origin: HELD, token: "window-session", pair: PAIR },
    withheld: false,
    retired: new Set(),
    paired: new Set(),
    cookieFollows: true,
    ...over,
  };
}

/** A request from the held origin's own page, unless *over* says otherwise. */
function request(over = {}) {
  return {
    url: `${HELD}/api/chat`,
    resourceType: "xhr",
    initiatorOrigin: HELD,
    requestHeaders: {},
    hasWebContents: true,
    ...over,
  };
}

const EVERY_TYPE = ["mainFrame", "subFrame", "stylesheet", "script", "image", "font", "object", "xhr", "ping", "cspReport", "media", "webSocket", "other"];

describe("originKey: an origin compared parsed, never as a string", () => {
  it("reads ws as http and wss as https, with the default port spelled out", () => {
    assert.equal(originKey("ws://127.0.0.1:51234/api/ws"), "http://127.0.0.1:51234");
    assert.equal(originKey("wss://pc.example.test/socket"), "https://pc.example.test:443");
    assert.equal(originKey("http://127.0.0.1/"), "http://127.0.0.1:80");
  });

  it("lowercases the host, drops a trailing dot and userinfo, and keeps IPv6 brackets", () => {
    assert.equal(originKey("HTTP://LOCALHOST.:9000/x"), "http://localhost:9000");
    assert.equal(originKey("http://someone:pw@127.0.0.1:9000/"), "http://127.0.0.1:9000");
    assert.equal(originKey("http://[0:0:0:0:0:0:0:1]:9000/"), "http://[::1]:9000");
  });

  it("names no origin for file:, about:, data:, a sandboxed frame's \"null\" or nothing", () => {
    for (const value of ["file:///app/offline.html", "about:blank", "data:text/html,x", "null", "", undefined, "not a url"]) {
      assert.equal(originKey(value), "", String(value));
    }
  });
});

describe("isLoopbackOrigin: what reaches this computer", () => {
  it("takes 127/8, ::1, an IPv4-mapped 127 address, localhost, *.localhost and the unspecified addresses", () => {
    for (const url of ["http://127.0.0.1:1", "http://127.9.8.7:1", "http://2130706433:1", "http://[::1]:1", "http://[::ffff:127.0.0.1]:1", "http://localhost:1", "http://LocalHost.:1", "http://pc.localhost:1", "http://0.0.0.0:1", "http://[::]:1"]) {
      assert.equal(isLoopbackOrigin(originKey(url)), true, url);
    }
  });

  it("does not take another address or a name", () => {
    for (const url of ["http://10.0.0.4:1", "http://192.168.1.5:1", "http://[fd00::5]:1", "https://pc.example.test", "http://localhost.example.test:1"]) {
      assert.equal(isLoopbackOrigin(originKey(url)), false, url);
    }
  });
});

describe("decide: cancel", () => {
  it("cancels every kind of request to the held origin while the sign-in is withheld", () => {
    for (const resourceType of EVERY_TYPE) {
      const d = decide(request({ resourceType }), state({ withheld: true }));
      assert.equal(d.cancel, true, resourceType);
      assert.equal(d.pair, null, resourceType);
    }
    assert.equal(decide(request({ url: "ws://127.0.0.1:51234/api/ws", resourceType: "webSocket" }), state({ withheld: true })).cancel, true);
  });

  it("lets the held origin through while it is not withheld", () => {
    for (const resourceType of EVERY_TYPE) assert.equal(decide(request({ resourceType }), state()).cancel, false, resourceType);
  });

  it("cancels every request to a retired origin, ws and wss included, whatever started it", () => {
    const retired = new Set([originKey("http://127.0.0.1:40000")]);
    for (const url of ["http://127.0.0.1:40000/api/x", "ws://127.0.0.1:40000/api/ws", "wss://127.0.0.1:40000/api/ws"]) {
      for (const resourceType of ["mainFrame", "xhr", "webSocket", "image"]) {
        assert.equal(decide(request({ url, resourceType, initiatorOrigin: undefined }), state({ retired })).cancel, true, `${url} ${resourceType}`);
      }
    }
  });

  it("an origin held again is reached, though this run once retired it", () => {
    const d = decide(request(), state({ retired: new Set([originKey(HELD)]) }));
    assert.equal(d.cancel, false);
    assert.equal(d.pair, PAIR);
  });

  it("cancels any other loopback origin, and lets a gateway the owner added through", () => {
    const paired = new Set([originKey("http://127.0.0.1:10000")]);
    assert.equal(decide(request({ url: "http://127.0.0.1:10000/" }), state({ paired })).cancel, false);
    for (const url of ["http://127.0.0.1:10001/", "http://localhost:51234/", "http://[::1]:51234/", "http://127.0.0.2:51234/"]) {
      assert.equal(decide(request({ url, resourceType: "mainFrame" }), state({ paired })).cancel, true, url);
    }
  });

  it("leaves another host, file:, about: and data: alone, withheld or not", () => {
    for (const url of ["https://pc.example.test/x", "http://10.0.0.4:10000/", "file:///app/offline.html", "about:blank", "data:text/html,x"]) {
      assert.equal(decide(request({ url, resourceType: "mainFrame" }), state({ withheld: true })).cancel, false, url);
    }
  });
});

describe("decide: the pair", () => {
  it("goes on the held origin's own page's fetches and its socket's upgrade", () => {
    assert.equal(decide(request(), state()).pair, PAIR);
    assert.equal(decide(request({ url: "ws://127.0.0.1:51234/api/ws", resourceType: "webSocket" }), state()).pair, PAIR);
  });

  it("goes on no navigation, whoever starts it, and on no other kind of request", () => {
    for (const resourceType of EVERY_TYPE.filter((t) => t !== "xhr" && t !== "webSocket")) {
      assert.equal(decide(request({ resourceType }), state()).pair, null, resourceType);
    }
    assert.equal(decide(request({ resourceType: "mainFrame", initiatorOrigin: undefined }), state()).pair, null, "main's loadURL");
  });

  it("goes on nothing a service worker, a sandboxed frame, another origin's page or no page asks for", () => {
    assert.equal(decide(request({ hasWebContents: false }), state()).pair, null, "no webContents");
    for (const initiatorOrigin of ["null", null, undefined, "", "http://127.0.0.1:40000", "https://pc.example.test"]) {
      assert.equal(decide(request({ initiatorOrigin }), state()).pair, null, String(initiatorOrigin));
    }
  });

  it("goes nowhere but the held origin", () => {
    const paired = new Set([originKey("http://127.0.0.1:10000")]);
    assert.equal(decide(request({ url: "http://127.0.0.1:10000/api/x" }), state({ paired })).pair, null);
    assert.equal(decide(request({ url: "https://pc.example.test/api/x" }), state()).pair, null);
  });

  it("is not set while the gateway gave none, or once the window's cookie holds another sign-in", () => {
    assert.equal(decide(request(), state({ held: { origin: HELD, token: "t", pair: null } })).pair, null);
    assert.equal(decide(request(), state({ cookieFollows: false })).pair, null);
    assert.equal(decide(request(), state({ held: null })).pair, null);
  });

  it("is the window session's, over a pair the held origin's own page sent, while the window's cookie holds that session", () => {
    const req = request({ requestHeaders: { "x-personalclaw-pair": "the-pages-own" } });
    const d = decide(req, state());
    assert.deepEqual(d, { cancel: false, header: "set", pair: PAIR });
    const sent = Object.entries(headersFor(req, d)).filter(([name]) => name.toLowerCase() === PAIR_HEADER.toLowerCase());
    assert.deepEqual(sent, [[PAIR_HEADER, PAIR]], "the page's own pair went out beside the window session's");
  });

  it("keeps the page's own: on a gateway the owner added, and on a held page whose cookie holds another sign-in, as a header or a socket's subprotocol", () => {
    const paired = new Set([originKey("http://127.0.0.1:10000")]);
    const theirs = request({ url: "http://127.0.0.1:10000/api/x", initiatorOrigin: "http://127.0.0.1:10000", requestHeaders: { [PAIR_HEADER]: "their-own" } });
    assert.deepEqual(decide(theirs, state({ paired })), { cancel: false, header: "keep", pair: null });
    assert.equal(headersFor(theirs, decide(theirs, state({ paired })))[PAIR_HEADER], "their-own");
    const password = request({ requestHeaders: { [PAIR_HEADER]: "a-password-sign-ins" } });
    assert.equal(decide(password, state({ cookieFollows: false })).header, "keep");
    const socket = request({ url: "ws://127.0.0.1:51234/api/ws", resourceType: "webSocket", requestHeaders: { "Sec-WebSocket-Protocol": "pc-events, pc-pair.the-pages-own" } });
    assert.deepEqual(decide(socket, state({ cookieFollows: false })), { cancel: false, header: "keep", pair: null });
  });

  it("goes on a socket that offers the page's own as its subprotocol, while the window's cookie holds the held session", () => {
    const socket = request({ url: "ws://127.0.0.1:51234/api/ws", resourceType: "webSocket", requestHeaders: { "Sec-WebSocket-Protocol": "personalclaw, pc-pair.a-stale-pair" } });
    const d = decide(socket, state());
    assert.deepEqual(d, { cancel: false, header: "set", pair: PAIR });
    assert.equal(headersFor(socket, d)[PAIR_HEADER], PAIR, "the gateway reads the header before the subprotocol");
    assert.equal(headersFor(socket, d)["Sec-WebSocket-Protocol"], "personalclaw, pc-pair.a-stale-pair", "the page's subprotocols are its own");
  });

  it("comes off a request that carries one to an origin other than its page's: a redirect's next hop", () => {
    const paired = new Set([originKey("http://127.0.0.1:10000")]);
    const hop = request({ url: "http://127.0.0.1:10000/landing", requestHeaders: { [PAIR_HEADER]: PAIR } });
    const d = decide(hop, state({ paired }));
    assert.equal(d.header, "strip");
    assert.equal(headersFor(hop, d)[PAIR_HEADER], undefined);
    const away = request({ url: "https://pc.example.test/x", requestHeaders: { [PAIR_HEADER]: PAIR } });
    assert.equal(decide(away, state()).header, "strip");
    const unasked = request({ initiatorOrigin: undefined, resourceType: "mainFrame", requestHeaders: { [PAIR_HEADER]: PAIR } });
    assert.equal(decide(unasked, state()).header, "strip", "no page made it");
  });
});

describe("decide: set, keep or strip, by the request's page and its target", () => {
  const PAIRED = "http://127.0.0.1:10000";
  const REMOTE = "https://pc.example.test";
  const paired = new Set([originKey(PAIRED)]);
  const ROWS = [
    // [the page (initiatorOrigin), the target, the kind, the verdict]
    [HELD, `${HELD}/api/chat`, "xhr", "set"],
    [HELD, "ws://127.0.0.1:51234/api/ws", "webSocket", "set"],
    [HELD, `${HELD}/`, "mainFrame", "keep"],
    [HELD, `${HELD}/app.js`, "script", "keep"],
    [PAIRED, `${PAIRED}/api/chat`, "xhr", "keep"],
    [PAIRED, "ws://127.0.0.1:10000/api/ws", "webSocket", "keep"],
    [REMOTE, `${REMOTE}/api/chat`, "xhr", "keep"],
    [HELD, `${PAIRED}/api/chat`, "xhr", "strip"],
    [HELD, `${REMOTE}/api/chat`, "xhr", "strip"],
    [PAIRED, `${HELD}/api/chat`, "xhr", "strip"],
    [REMOTE, `${HELD}/api/chat`, "xhr", "strip"],
    [undefined, `${HELD}/api/chat`, "xhr", "strip"],
    ["null", `${HELD}/api/chat`, "xhr", "strip"],
    ["", `${PAIRED}/api/chat`, "xhr", "strip"],
    [undefined, `${HELD}/`, "mainFrame", "strip"],
    [HELD, "file:///app/loading.html", "mainFrame", "strip"],
  ];

  it("strips where the page is missing, \"null\" or another origin's, sets the window session's on the held origin's own, and keeps a page's own elsewhere", () => {
    for (const [initiatorOrigin, url, resourceType, verdict] of ROWS) {
      const req = request({ url, resourceType, initiatorOrigin, requestHeaders: { [PAIR_HEADER]: "carried" } });
      const d = decide(req, state({ paired }));
      assert.equal(d.cancel, false, `${initiatorOrigin} → ${url}`);
      assert.equal(d.header, verdict, `${initiatorOrigin} → ${url} (${resourceType})`);
      assert.equal(d.pair, verdict === "set" ? PAIR : null, `${initiatorOrigin} → ${url}`);
      const out = headersFor(req, d)[PAIR_HEADER];
      assert.equal(out, { set: PAIR, keep: "carried", strip: undefined }[verdict], `${initiatorOrigin} → ${url}: what went out`);
    }
  });
});

describe("the event a page's sign-in change is told by", () => {
  it("is the dashboard's own (web/src/lib/pair.ts)", () => {
    const web = fs.readFileSync(path.join(__dirname, "..", "..", "web", "src", "lib", "pair.ts"), "utf8");
    assert.ok(web.includes(`export const PAIR_CHANGED = '${PAIR_CHANGED}'`), "the dashboard's event moved");
    assert.equal(PAIR_CHANGED, "personalclaw:pair-changed");
  });
});

describe("the page's own pair, in a script main runs in a page of a gateway the owner added", () => {
  const run = (storage) => vm.runInNewContext(PAGE_OWN_PAIR_HEADERS, { window: { localStorage: storage } });
  const holding = (value) => ({ getItem: (key) => (key === "pc_pair" ? value : null) });
  const PAGE_PAIR = "A".repeat(43);

  it("is the pair the page keeps, as the header the page sends, read when the script runs", () => {
    assert.deepEqual({ ...run(holding(PAGE_PAIR)) }, { [PAIR_HEADER]: PAGE_PAIR });
  });

  it("is nothing for a page that keeps none, a value of another shape, or storage that can't be read", () => {
    assert.deepEqual({ ...run(holding(null)) }, {});
    assert.deepEqual({ ...run(holding("not-a-pair")) }, {});
    assert.deepEqual({ ...run(holding(`${PAGE_PAIR}x`)) }, {});
    assert.deepEqual({ ...run({ getItem: () => { throw new Error("denied"); } }) }, {});
  });

  it("is written out whole, never put together from values, with the key, the shape and the header it is held to", () => {
    assert.ok(PAGE_OWN_PAIR_HEADERS.includes(`window.localStorage.getItem(${JSON.stringify(PAGE_PAIR_KEY)})`), "the key");
    assert.ok(PAGE_OWN_PAIR_HEADERS.includes(`${PAIR_SHAPE}.test(v || "")`), "the shape");
    assert.ok(PAGE_OWN_PAIR_HEADERS.includes(`{ ${JSON.stringify(PAIR_HEADER)}: v }`), "the header");
    const source = fs.readFileSync(path.join(__dirname, "..", "windowTraffic.js"), "utf8");
    assert.match(source, /^const PAGE_OWN_PAIR_HEADERS =\s*'[^'`]*';$/m, "the script is built from values again (CodeQL js/bad-code-sanitization)");
  });

  it("reads the key, the shape and the header the dashboard itself uses (web/src/lib/pair.ts)", () => {
    const web = fs.readFileSync(path.join(__dirname, "..", "..", "web", "src", "lib", "pair.ts"), "utf8");
    assert.match(web, /const KEY = 'pc_pair'/, "the dashboard's key moved: this script reads another");
    assert.ok(web.includes("const SHAPE = /^[A-Za-z0-9_-]{43}$/"), "the dashboard's shape moved");
    assert.ok(web.includes(`export const PAIR_HEADER = '${PAIR_HEADER}'`), "the dashboard's header moved");
  });
});

describe("headersFor", () => {
  it("applies each verdict to whatever pair the page sent, its name in any case", () => {
    const req = request({ requestHeaders: { "x-PersonalClaw-pair": "the-pages-own", Accept: "*/*" } });
    assert.deepEqual(headersFor(req, { cancel: false, header: "set", pair: PAIR }), { Accept: "*/*", [PAIR_HEADER]: PAIR });
    assert.deepEqual(headersFor(req, { cancel: false, header: "keep", pair: null }), { "x-PersonalClaw-pair": "the-pages-own", Accept: "*/*" });
    assert.deepEqual(headersFor(req, { cancel: false, header: "strip", pair: null }), { Accept: "*/*" });
  });

  it("drops the Referer, sets the pair, and leaves every other header as it was", () => {
    const req = request({ requestHeaders: { Referer: "http://127.0.0.1:51234/#/chat", Accept: "application/json" } });
    assert.deepEqual(headersFor(req, decide(req, state())), { Accept: "application/json", [PAIR_HEADER]: PAIR });
  });

  it("never touches Authorization", () => {
    const req = request({ requestHeaders: { Authorization: "Bearer page-token" } });
    assert.equal(headersFor(req, decide(req, state())).Authorization, "Bearer page-token");
  });
});

describe("registerWindowTraffic", () => {
  it("registers each listener once, with no filter, and hands cookie changes to the record", () => {
    const calls = [];
    const listeners = {};
    const fake = {
      webRequest: {
        onBeforeRequest: (...args) => (calls.push(["onBeforeRequest", args.length]), (listeners.request = args.at(-1))),
        onBeforeSendHeaders: (...args) => (calls.push(["onBeforeSendHeaders", args.length]), (listeners.headers = args.at(-1))),
      },
      cookies: { on: (event, fn) => (calls.push(["cookies", event]), (listeners.cookie = fn)) },
    };
    const changed = [];
    const record = { trafficState: () => state({ withheld: true }), cookieChanged: (c, removed) => changed.push([c.name, removed]) };
    registerWindowTraffic(fake, record);
    assert.deepEqual(calls, [["onBeforeRequest", 1], ["onBeforeSendHeaders", 1], ["cookies", "changed"]], "a filter, or a listener registered twice");
    let answer = null;
    listeners.request({ url: `${HELD}/api/x`, resourceType: "xhr", initiatorOrigin: HELD, webContentsId: 3 }, (a) => (answer = a));
    assert.deepEqual(answer, { cancel: true });
    listeners.cookie({}, { name: "pc_token_51234" }, "explicit", true);
    assert.deepEqual(changed, [["pc_token_51234", true]]);
  });
});
