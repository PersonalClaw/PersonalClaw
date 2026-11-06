/**
 * A real Electron's window traffic under the app's own decision (`windowTraffic.registerWindowTraffic`)
 * and record (`localSignIn.makeLocalSignIn`), against a stand-in gateway on 127.0.0.1 that answers
 * with a page of its own and records every request it gets, with the pair header it carried.
 *
 * The page asks the stand-in in every way a page can: fetch, EventSource, a socket's upgrade,
 * sendBeacon, a keepalive DELETE, a service worker's fetch, a download, and a fetch from a sandboxed
 * frame. It does so once while the windows' sign-in is held, and once while it is withheld. A
 * request from this process itself (Node's http, which no webRequest sees) is the control: it reaches
 * the stand-in while everything the window sends is cancelled, so silence can't pass for a pass.
 *
 * While held, the page also fetches /api/hop, which the stand-in redirects to a second stand-in (a
 * gateway the owner added), and opens a socket that offers a `pc-pair.` subprotocol of its own,
 * before and after a real cookie of another session goes in the jar. The probe records whether
 * webRequest shows the upgrade's Sec-WebSocket-Protocol.
 *
 * Prints one line, `PC_PROBE {json}`, and quits. Run by `test/windowTrafficElectron.test.js`.
 */
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const { app, BrowserWindow, session } = require("electron");

// A profile of its own: its service worker, cookies and caches never meet another run's.
app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "pclaw-window-traffic-")));

const ROOT = path.join(__dirname, "..", "..");
const { makeLocalSignIn } = require(path.join(ROOT, "localSignIn.js"));
const { PAIR_HEADER, registerWindowTraffic } = require(path.join(ROOT, "windowTraffic.js"));

const PAIR = "pair-of-the-window-session";
const seen = [];

const PAGE = `<!doctype html><meta charset="utf-8"><title>stand-in</title>
<script>
async function runAll(tag) {
  const q = "?" + tag;
  const tries = [];
  tries.push(fetch("/api/fetch" + q).catch(() => {}));
  tries.push(fetch("/api/own" + q, { headers: { "X-PersonalClaw-Pair": "the-pages-own" } }).catch(() => {}));
  if (tag === "held") tries.push(fetch("/api/hop" + q).catch(() => {}));
  tries.push(new Promise((done) => {
    const es = new EventSource("/api/sse" + q);
    es.onmessage = () => { es.close(); done(); };
    es.onerror = () => { es.close(); done(); };
  }));
  tries.push(new Promise((done) => {
    const ws = new WebSocket("ws://" + location.host + "/api/ws" + q);
    ws.onerror = ws.onclose = () => done();
  }));
  try { navigator.sendBeacon("/api/beacon" + q, "x"); } catch {}
  tries.push(fetch("/api/upload" + q, { method: "DELETE", keepalive: true }).catch(() => {}));
  const frame = document.createElement("iframe");
  frame.sandbox = "allow-scripts";
  frame.srcdoc = "<script>fetch('" + location.origin + "/api/sandboxed" + q + "').catch(() => {})<\\/script>";
  document.body.appendChild(frame);
  const a = document.createElement("a");
  a.href = "/api/download" + q;
  a.download = "x.bin";
  document.body.appendChild(a);
  a.click();
  try {
    const reg = await navigator.serviceWorker.register("/sw.js");
    await navigator.serviceWorker.ready;
    (reg.active || reg.waiting || reg.installing).postMessage(tag);
  } catch {}
  await Promise.race([Promise.all(tries), new Promise((r) => setTimeout(r, 2500))]);
  return true;
}
function offerOwn(tag) {
  return new Promise((done) => {
    const ws = new WebSocket("ws://" + location.host + "/api/ws?" + tag, ["personalclaw", "pc-pair.a-stale-pair-of-the-page"]);
    ws.onerror = ws.onclose = () => done(true);
    setTimeout(() => done(true), 2000);
  });
}
</script><body></body>`;

const WORKER = `self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("message", (e) => e.waitUntil(fetch("/api/worker?" + e.data).catch(() => {})));`;

/** The second stand-in's address, a gateway the owner added, where /api/hop lands. */
let landing = "";

function standIn({ record = seen } = {}) {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, "http://x");
    record.push({ method: req.method, path: url.pathname, tag: url.search.slice(1), pair: req.headers[PAIR_HEADER.toLowerCase()] || null });
    if (url.pathname === "/api/hop") {
      res.writeHead(302, { Location: `${landing}/landing${url.search}` });
      return res.end();
    }
    if (url.pathname === "/") {
      res.writeHead(200, { "Content-Type": "text/html" });
      return res.end(PAGE);
    }
    if (url.pathname === "/sw.js") {
      res.writeHead(200, { "Content-Type": "text/javascript" });
      return res.end(WORKER);
    }
    if (url.pathname === "/api/sse") {
      res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-store" });
      return res.end("data: one\n\n");
    }
    res.writeHead(200, { "Content-Type": "application/octet-stream", "Access-Control-Allow-Origin": "*" });
    res.end("ok");
  });
  server.on("upgrade", (req, socket) => {
    const url = new URL(req.url, "http://x");
    record.push({ method: "UPGRADE", path: url.pathname, tag: url.search.slice(1), pair: req.headers[PAIR_HEADER.toLowerCase()] || null, protocol: req.headers["sec-websocket-protocol"] || null });
    socket.destroy();
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const control = (origin) =>
  new Promise((resolve) => {
    http.get(`${origin}/api/control?withheld`, (res) => {
      res.resume();
      res.on("end", () => resolve(true));
    }).on("error", () => resolve(false));
  });

app.whenReady().then(async () => {
  const out = {};
  try {
    const server = await standIn();
    const { port } = server.address();
    const origin = `http://127.0.0.1:${port}`;
    const landed = [];
    const second = await standIn({ record: landed });
    landing = `http://127.0.0.1:${second.address().port}`;
    const record = makeLocalSignIn({ cookies: session.defaultSession.cookies });
    await record.adopt({ url: origin, port, token: "window-session-token" }, { pair: PAIR });
    record.setPaired([landing]);
    // What webRequest shows of a socket's upgrade: whether its subprotocols reach onBeforeSendHeaders.
    const webRequest = session.defaultSession.webRequest;
    const realHeaders = webRequest.onBeforeSendHeaders.bind(webRequest);
    out.upgradeHeaders = [];
    webRequest.onBeforeSendHeaders = (listener) =>
      realHeaders((details, callback) => {
        if (details.resourceType === "webSocket") out.upgradeHeaders.push(Object.keys(details.requestHeaders || {}).map((k) => k.toLowerCase()).includes("sec-websocket-protocol"));
        listener(details, callback);
      });
    registerWindowTraffic(session.defaultSession, record);
    session.defaultSession.on("will-download", (_e, item) => item.cancel());

    const win = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true } });
    await win.loadURL(`${origin}/`);
    await win.webContents.executeJavaScript("runAll('held')");
    await win.webContents.executeJavaScript("offerOwn('followed')");
    await wait(1500);
    // Another session's cookie in the jar, set for real: the page's own pair is its own from now.
    await session.defaultSession.cookies.set({ url: origin, name: `pc_token_${port}`, value: "another-session", path: "/", httpOnly: true, sameSite: "lax" });
    // The jar's change reaches the record on its own (`cookies.on("changed")`): about 2 s at most.
    for (let waited = 0; record.trafficState().cookieFollows !== false && waited < 2000; waited += 50) await wait(50);
    out.cookieFollowsAfterAnother = record.trafficState().cookieFollows;
    await win.webContents.executeJavaScript("offerOwn('another')");
    await wait(800);

    const before = seen.length;
    await record.withhold(() => session.defaultSession.closeAllConnections());
    out.jarAfterWithhold = (await session.defaultSession.cookies.get({ name: `pc_token_${port}` })).length;
    await win.webContents.executeJavaScript("runAll('withheld')").catch(() => {});
    await wait(2000);
    out.control = await control(origin);
    out.root = seen.find((r) => r.path === "/") || null;
    out.held = seen.filter((r) => r.tag === "held");
    out.followed = seen.filter((r) => r.tag === "followed");
    out.another = seen.filter((r) => r.tag === "another");
    out.landed = landed;
    out.withheld = seen.slice(before).filter((r) => r.path !== "/api/control");
    server.close();
    second.close();
  } catch (err) {
    out.error = String(err && err.stack ? err.stack : err);
  }
  process.stdout.write(`PC_PROBE ${JSON.stringify(out)}\n`);
  app.exit(0);
});
