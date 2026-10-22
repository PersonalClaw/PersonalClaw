"use strict";

/**
 * A real Electron main process that loads the built buddy page (`web/dist`, `#/buddy`) into the
 * buddy's window, built as `buddyWindow.js` builds it with its own preload, against a stand-in
 * gateway on loopback, and answers its approvals the ways an owner can (#3832).
 *
 * Not a test: `buddyAnswer.test.js` spawns it. The stand-in gateway serves the built page and the
 * three lists the buddy reads (`/api/running`, `/api/waiting`, `/api/listening`), and records every
 * request that is not a read, so the test sees what reached an answer route and what did not.
 * A second window stands in for the app the owner works in, and takes focus from the buddy.
 *
 * The clicks and keys are real input events (`webContents.sendInputEvent`), delivered to the
 * buddy's renderer whether or not its window has focus, which is how a key pressed while the
 * buddy is not the focused window is put to the page at all. A key observer, added in the capture
 * phase, only counts what arrived and where focus was.
 *
 * Env:
 *   PC_PROBE_DIST  the built web app (default `../web/dist` from the shell)
 *
 * Emits exactly one line on stdout:
 *   PC_PROBE {"electronReady":true,"legs":{…},"posts":[…],…}
 */

const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { app, BrowserWindow } = require("electron");

const ROOT = path.join(__dirname, "..", "..");
const DIST = path.resolve(process.env.PC_PROBE_DIST || path.join(ROOT, "..", "web", "dist"));
const MOD = process.platform === "darwin" ? "meta" : "control";

app.disableHardwareAcceleration();

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".json": "application/json", ".png": "image/png", ".woff2": "font/woff2", ".webmanifest": "application/manifest+json" };

/** One approval the stand-in gateway lists, as `GET /api/waiting` carries it. */
function approval(id, input) {
  const ap = {
    id, request_id: id, source: "chat", tool: "Bash", tool_input: input, tool_purpose: `Run ${input}`,
    session: "s1", ts: Math.round(Date.now() / 1000) - 30, confirms: [], session_title: "", agent: "",
    risk: "safe", grant_agent: "", source_label: "", deny_effect: "The run stops here.", work: "chat:s1",
  };
  return {
    id: `approval:${id}`, kind: "approval", work: "chat:s1", session: "s1", brief: ap, consequence: "changes", risk: "safe",
    asked_at: new Date(ap.ts * 1000).toISOString(), age_secs: 30, expires_at: null, current: true, interrupt: "deliver",
    answer: { method: "POST", path: `/api/approvals/${id}/{answer}`, takes: ["approve", "reject"], body: {} }, route: "#/chat/s1",
  };
}

const RUNNING = {
  running: [{ work: "chat:s1", kind: "chat", session: "s1", phase: "approval", line: "Needs your OK", since: null, name: "Sweep", agent: "", started_at: new Date().toISOString(), route: "#/chat/s1", blocked: false, waiting: [] }],
  finished: [],
};

/** The stand-in gateway: the built page, the three lists, and a record of everything else. */
function gateway(state) {
  return http.createServer((req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    const json = (status, body) => {
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(body));
    };
    if (req.method !== "GET") {
      state.posts.push(`${req.method} ${url.pathname}`);
      const m = /^\/api\/approvals\/([^/]+)\/(approve|reject)$/.exec(url.pathname);
      if (req.method === "POST" && m) {
        state.waiting = state.waiting.filter((w) => w.brief.id !== m[1]);
        return json(200, { ok: true });
      }
      return json(404, { error: "not_found" });
    }
    if (url.pathname === "/api/running") return json(200, RUNNING);
    if (url.pathname === "/api/waiting") return json(200, { waiting: state.waiting, unreadable: [] });
    if (url.pathname === "/api/listening") return json(200, { on: false, sources: [], since: null });
    if (url.pathname.startsWith("/api/")) return json(404, { error: "not_found" });
    const file = path.resolve(DIST, `.${url.pathname === "/" ? "/index.html" : decodeURIComponent(url.pathname)}`);
    if (!file.startsWith(DIST + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404);
      return res.end();
    }
    res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

app.whenReady().then(async () => {
  const out = { electronReady: true, platform: process.platform, legs: {} };
  const state = { waiting: [approval("ap-1", "ls /tmp/one")], posts: [] };
  const server = gateway(state);
  try {
    if (!fs.existsSync(path.join(DIST, "index.html"))) throw new Error(`the web app is not built at ${DIST}`);
    await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    const { buddyWindowOptions, BUDDY_PUSHES } = require(path.join(ROOT, "buddyWindow.js"));

    // The app the owner is working in: it has focus.
    const other = new BrowserWindow({ x: 480, y: 40, width: 320, height: 200, show: false, title: "another app" });
    await other.loadURL("data:text/html;charset=utf-8,<p>another app</p>");
    other.show();
    if (process.platform === "darwin") app.focus({ steal: true });
    other.focus();

    const win = new BrowserWindow(buddyWindowOptions({ bounds: { x: 40, y: 40, width: 420, height: 640 }, preload: path.join(ROOT, "buddyPreload.js") }));
    const wc = win.webContents;
    out.consoleErrors = [];
    wc.on("console-message", (_e, level, message) => { if (level >= 3) out.consoleErrors.push(String(message).slice(0, 300)); });
    await win.loadURL(`${origin}/#/buddy`);
    win.showInactive();

    const page = (js) => wc.executeJavaScript(js);
    const until = async (js, ms = 8000) => {
      const end = Date.now() + ms;
      for (;;) {
        const v = await page(js);
        if (v) return v;
        if (Date.now() > end) throw new Error(`timed out waiting for: ${js}`);
        await wait(100);
      }
    };
    await page(`window.__pcKeys = []; window.addEventListener("keydown", (e) => {
      const card = document.querySelector('[role="group"][aria-label^="Permission needed"]');
      window.__pcKeys.push({ key: e.key, mod: e.metaKey || e.ctrlKey, windowFocused: document.hasFocus(), onCard: Boolean(card && card.contains(document.activeElement)) });
    }, true); true`);

    /** The card showing the approval whose input is *input*, once it shows. */
    const CARD = (input) => `[...document.querySelectorAll('[role="group"][aria-label^="Permission needed"]')].find((g) => g.textContent.includes(${JSON.stringify(input)}))`;
    const centre = (selectorJs) => page(`(() => { const el = ${selectorJs}; if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; })()`);
    const click = async (at) => {
      wc.sendInputEvent({ type: "mouseMove", x: at.x, y: at.y });
      wc.sendInputEvent({ type: "mouseDown", x: at.x, y: at.y, button: "left", clickCount: 1 });
      wc.sendInputEvent({ type: "mouseUp", x: at.x, y: at.y, button: "left", clickCount: 1 });
      await wait(150);
    };
    const press = async (keyCode, modifiers = []) => {
      wc.sendInputEvent({ type: "keyDown", keyCode, modifiers });
      wc.sendInputEvent({ type: "keyUp", keyCode, modifiers });
      await wait(150);
    };
    const settled = async (ms = 1200) => { await wait(ms); return [...state.posts]; };
    const focusBuddy = async () => {
      if (process.platform === "darwin") app.focus({ steal: true });
      win.focus();
      await until("document.hasFocus()", 4000);
    };
    const focusOther = async () => {
      other.focus();
      await until("!document.hasFocus()", 4000);
    };
    /** A click on the card's heading, which is no control: focus moves into the card. */
    const focusCard = async (input) => {
      await click(await centre(`${CARD(input)}?.querySelector('[role="alert"]')`));
      return page(`Boolean(${CARD(input)}?.contains(document.activeElement))`);
    };
    const nextApproval = async (id, input) => {
      state.waiting = [approval(id, input)];
      wc.send(BUDDY_PUSHES.ring);
      await until(`Boolean(${CARD(input)})`);
    };

    // 1. A click on Allow, the buddy not the focused window.
    await until(`Boolean(${CARD("ls /tmp/one")})`, 15000);
    out.legs.click = { windowFocused: await page("document.hasFocus()") };
    await click(await centre(`${CARD("ls /tmp/one")}?.querySelector('button[aria-label="Allow Bash"]')`));
    out.legs.click.posts = await settled();

    // 2. Focus in the card: Enter alone, and ⌘Enter with another window focused, send nothing;
    //    the same key with the buddy's window focused again approves.
    state.posts.length = 0;
    await nextApproval("ap-2", "ls /tmp/two");
    await focusBuddy();
    const onCard = await focusCard("ls /tmp/two");
    await press("Enter");
    const afterEnter = await settled(800);
    await focusOther();
    const keysBefore = await page("window.__pcKeys.length");
    await press("Enter", [MOD]);
    const afterUnfocused = await settled();
    const unfocusedKeys = await page(`window.__pcKeys.slice(${keysBefore})`);
    await focusBuddy();
    const stillOnCard = await page(`Boolean(${CARD("ls /tmp/two")}?.contains(document.activeElement))`);
    await press("Enter", [MOD]);
    out.legs.keys = { onCard, afterEnter, afterUnfocused, unfocusedKeys, stillOnCard, afterFocused: await settled() };

    // 3. Escape on the focused card closes it and decides nothing.
    state.posts.length = 0;
    await nextApproval("ap-3", "ls /tmp/three");
    await focusBuddy();
    const onCard3 = await focusCard("ls /tmp/three");
    await press("Escape");
    out.legs.escape = { onCard: onCard3, posts: await settled(), stillWaiting: state.waiting.map((w) => w.id) };

    win.destroy();
    other.destroy();
  } catch (err) {
    out.error = String((err && err.stack) || err);
  }
  server.close();
  process.stdout.write(`PC_PROBE ${JSON.stringify(out)}\n`);
  app.exit(0);
});
