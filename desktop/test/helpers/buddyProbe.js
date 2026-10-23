"use strict";

/**
 * A real Electron main process that builds the buddy's window as `buddyWindow.js` builds it, with
 * its own preload, and reports what the window and its renderer really are (#3831).
 *
 * Not a test: `buddyIdle.test.js` spawns it. Unlike `bridgeProbe.js` it requires the shell's
 * `buddyWindow.js`, since the window's options are what it checks; a require that fails still
 * prints its one line, so a missing line means Electron could not start on this host.
 *
 * The page stands in for the buddy route under `BuddyWindowRoot`: it draws a frame loop and an
 * interval while shown. `PC_PROBE_PAGE=root` stops both on `visibilitychange` as the root does by
 * unmounting; `PC_PROBE_PAGE=ticks` keeps ticking while hidden, the negative control that proves
 * the probe can see a page that schedules work while hidden. Every `setTimeout`, `setInterval` and
 * `requestAnimationFrame` the page makes is counted.
 *
 * Covered while shown by a window above it, as a screen saver or a sleeping display covers it, the
 * page must still read visible: it is the shell's show and hide the page follows, not the OS's
 * occlusion (#3832). `PC_PROBE_SHOW=inactive` shows the window with a bare `showInactive()`, the
 * control that proves this host reports occlusion at all.
 *
 * In the `root` run the probe also builds a window with the shell's own controller
 * (`makeBuddyWindow`) on the real screen, and reports the orb's box (the window's real bounds plus
 * the layout its page heard over the buddy's preload) through every presentation, a hide and a
 * show, a drag, and a restart on the same store (#5216). With the card closed the window passes
 * every click but the orb's: on a display of its own, the probe moves the real pointer over every
 * pixel of the window and clicks it all over, over another window just below, and reports which
 * window each press reached.
 *
 * `PC_PROBE_PAGE=route` loads the real thing instead (#3902): the built `web/dist` at `#/buddy`,
 * served over a `pclaw-probe:` scheme of the probe's own (no socket), whose `/api/running`,
 * `/api/waiting` and `/api/listening` answer from fixtures, shown and hidden as the shell shows and
 * hides it (`showOnScreen`, `hideFromScreen`). It measures the buddy's renderer in each mode for the
 * idle span: still (shown, nothing running, nothing to draw), animating (one errand thinking),
 * covered (animating under a window above it, which the shell's show keeps drawing, #5210) and
 * hidden, each with its CPU, wakeups, working set, frames and timers.
 *
 * Env:
 *   PC_PROBE_PAGE     `root` (default), `ticks`, `route` or `orbBox` (the real `#/buddy` in the
 *                     controller's window, measured through each step, #5216)
 *   PC_PROBE_DIST     the built SPA for `route` (default `web/dist` beside the shell)
 *   PC_PROBE_SHOW     `shell` (default, `showOnScreen`) or `inactive`
 *   PC_PROBE_IDLE_MS  how long to keep the window hidden and watch it (default 60000)
 *   PC_PROBE_OS_CLICKS  `1` where the probe runs on a display of its own (`xvfb-run`): it then posts
 *                     real pointer input over the buddy's window to see where it lands (`osPointer.js`)
 *
 * Emits exactly one line on stdout:
 *   PC_PROBE {"electronReady":true,"shown":{…},"hidden":{…},"bridge":{…},…}
 */

const path = require("node:path");
const fs = require("node:fs");
const { app, BrowserWindow, ipcMain, protocol } = require("electron");
const { contentProtection } = require("./electronHost");
const { postPointer, stacking, xid } = require("./osPointer");

const PAGE = ["ticks", "route", "orbBox"].includes(process.env.PC_PROBE_PAGE) ? process.env.PC_PROBE_PAGE : "root";
const SHOW = process.env.PC_PROBE_SHOW === "inactive" ? "inactive" : "shell";
const IDLE_MS = Number(process.env.PC_PROBE_IDLE_MS) > 0 ? Number(process.env.PC_PROBE_IDLE_MS) : 60_000;
const ROOT = path.join(__dirname, "..", "..");

app.disableHardwareAcceleration();

// ── The route page: the built SPA over the probe's own scheme (#3902) ──
const SCHEME = "pclaw-probe";
const DIST = process.env.PC_PROBE_DIST || path.join(ROOT, "..", "web", "dist");
if (PAGE === "route" || PAGE === "orbBox") {
  protocol.registerSchemesAsPrivileged([
    { scheme: SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } },
  ]);
}
/** What the gateway's lists say in each mode: nothing at all (still), one errand thinking
 *  (animating), or that errand waiting on an approval (approval). */
const fixtures = { mode: "still" };
const ERRAND = {
  work: "chat:probe", kind: "chat", session: "probe", phase: "thinking", line: "Thinking", since: null,
  name: "Release notes", agent: "", started_at: new Date().toISOString(), route: "#/chat/probe", blocked: false, waiting: [],
};
/** The approval the `approval` mode waits on, as `GET /api/waiting` carries it. */
const APPROVAL = (() => {
  const ap = {
    id: "ap-1", request_id: "ap-1", source: "chat", tool: "Bash", tool_input: "ls /tmp", tool_purpose: "List /tmp",
    session: "probe", ts: Math.round(Date.now() / 1000) - 30, confirms: [], session_title: "", agent: "",
    risk: "safe", grant_agent: "", source_label: "", deny_effect: "The run stops here.", work: "chat:probe",
  };
  return {
    id: "approval:ap-1", kind: "approval", work: "chat:probe", session: "probe", brief: ap, consequence: "changes", risk: "safe",
    asked_at: new Date(ap.ts * 1000).toISOString(), age_secs: 30, expires_at: null, current: true, interrupt: "deliver",
    answer: { method: "POST", path: "/api/approvals/ap-1/{answer}", takes: ["approve", "reject"], body: {} }, route: "#/chat/probe",
  };
})();
const API = {
  "/api/running": () => ({
    running: fixtures.mode === "still" ? [] : [fixtures.mode === "approval" ? { ...ERRAND, phase: "approval", line: "Needs your OK", waiting: ["ap-1"] } : ERRAND],
    finished: [],
  }),
  "/api/waiting": () => ({ waiting: fixtures.mode === "approval" ? [APPROVAL] : [], unreadable: [] }),
  "/api/listening": () => ({ on: false, sources: [], since: null }),
};
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".json": "application/json", ".woff2": "font/woff2", ".png": "image/png", ".webmanifest": "application/manifest+json" };
function serveRoute() {
  protocol.handle(SCHEME, (request) => {
    const { pathname } = new URL(request.url);
    if (API[pathname]) return new Response(JSON.stringify(API[pathname]()), { headers: { "content-type": "application/json" } });
    if (pathname.startsWith("/api/")) return new Response("{}", { status: 404, headers: { "content-type": "application/json" } });
    const file = path.join(DIST, path.normalize(pathname === "/" ? "/index.html" : pathname));
    const inside = file.startsWith(DIST + path.sep) && fs.existsSync(file) && fs.statSync(file).isFile();
    const served = inside ? file : path.join(DIST, "index.html");
    return new Response(fs.readFileSync(served), { headers: { "content-type": TYPES[path.extname(served)] || "application/octet-stream" } });
  });
}

/** Counts every timer and frame the page asks for from now on. */
const COUNT_JS = `(() => {
  const c = window.__pcCounts || (window.__pcCounts = { timers: 0, frames: 0 });
  if (!window.__pcCounting) {
    window.__pcCounting = true;
    const st = window.setTimeout.bind(window), si = window.setInterval.bind(window), raf = window.requestAnimationFrame.bind(window);
    window.setTimeout = (...a) => { c.timers += 1; return st(...a); };
    window.setInterval = (...a) => { c.timers += 1; return si(...a); };
    window.requestAnimationFrame = (f) => { c.frames += 1; return raf(f); };
  }
  c.timers = 0; c.frames = 0;
  return true;
})()`;

/** One mode, measured over *ms*: the renderer's CPU and wakeups from app.getAppMetrics() since the
 *  span began, its working set, and the timers and frames the page asked for in it. */
async function measure(win, ms) {
  const page = (js) => win.webContents.executeJavaScript(js);
  const pid = win.webContents.getOSProcessId();
  const sample = () => app.getAppMetrics().find((m) => m.pid === pid);
  await page(COUNT_JS);
  const before = sample();
  await wait(ms);
  const after = sample();
  const counts = await page("window.__pcCounts");
  return {
    rendererFound: Boolean(before && after),
    documentHidden: await page("document.hidden"),
    percentCPU: after ? after.cpu.percentCPUUsage : null,
    idleWakeupsPerSecond: after ? after.cpu.idleWakeupsPerSecond : null,
    workingSetMB: after && after.memory ? Math.round((after.memory.workingSetSize / 1024) * 10) / 10 : null,
    timers: counts.timers,
    frames: counts.frames,
    drawn: await page("document.querySelectorAll('[data-buddy-orb]').length"),
  };
}

async function runRoute(out) {
  if (!fs.existsSync(path.join(DIST, "index.html"))) {
    out.routeMissing = `no built SPA at ${DIST} (run \`make web-build\`)`;
    return;
  }
  serveRoute();
  const { buddyWindowOptions, showOnScreen, hideFromScreen } = require(path.join(ROOT, "buddyWindow.js"));
  out.defaultLayoutSize = defaultLayoutSize();
  const options = buddyWindowOptions({ bounds: { x: 40, y: 40, ...out.defaultLayoutSize }, preload: path.join(ROOT, "buddyPreload.js") });
  const win = new BrowserWindow(options);
  out.windowSize = { width: win.getBounds().width, height: win.getBounds().height };
  win.webContents.on("preload-error", (_e, _p, err) => out.preloadErrors.push(String((err && err.message) || err)));
  await win.loadURL(`${SCHEME}://app/index.html#/buddy`);
  showOnScreen(win);
  await wait(2500);
  const ring = async (mode) => { fixtures.mode = mode; win.webContents.send("pclaw-buddy:ring"); await wait(1500); };
  out.modes = {};
  await ring("still");
  out.modes.still = await measure(win, IDLE_MS);
  await ring("animating");
  out.modes.animating = await measure(win, IDLE_MS);
  // Covered by a window above it, as a screen saver or another always-on-top window covers it: the
  // shell's show keeps it drawing (#5210), and this is what that costs.
  const cover = await coverOver(win.getBounds());
  await wait(1000);
  out.modes.covered = await measure(win, IDLE_MS);
  cover.destroy();
  hideFromScreen(win);
  await wait(800);
  out.modes.hidden = await measure(win, IDLE_MS);
  win.destroy();
}

/** What the page draws of the orb and its card, in the window's own points. */
const MEASURE_JS = `(() => {
  const rect = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height }; };
  const slot = document.querySelector("[data-buddy-orb-slot]");
  return {
    room: document.querySelector("[data-buddy-presence]")?.dataset.room ?? null,
    slot: rect(slot),
    orb: rect(slot && slot.querySelector("[data-buddy-orb] > span")),
    card: rect(document.querySelector("[data-buddy-card-surface]")),
    alert: Boolean(document.querySelector('[role="group"][aria-label^="Permission needed"]')),
  };
})()`;

/** The real `#/buddy` in a window the shell's controller builds, places and lays out (#5216): what
 *  the page draws of the orb and its card through work running, the card opened and closed, an
 *  alert, the whole-UI zoom at 80, 150 and 200 %, and a drag. */
async function runOrbBox(out) {
  if (!fs.existsSync(path.join(DIST, "index.html"))) {
    out.orbBox = { error: `no built SPA at ${DIST} (run \`make web-build\`)` };
    return;
  }
  serveRoute();
  const { screen } = require("electron");
  const { makeBuddyWindow, pushToBuddy, registerBuddyIpc } = require(path.join(ROOT, "buddyWindow.js"));
  const data = {};
  const store = { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, removeItem: (k) => { delete data[k]; } };
  const buddy = makeBuddyWindow({
    BrowserWindowCtor: BrowserWindow,
    screen,
    store,
    preload: path.join(ROOT, "buddyPreload.js"),
    onPlacement: (where) => pushToBuddy(buddy.window, "placement", where),
    onLayout: (layout) => pushToBuddy(buddy.window, "layout", layout),
  });
  const win = buddy.create();
  win.webContents.on("preload-error", (_e, _p, err) => out.preloadErrors.push(String((err && err.message) || err)));
  const unregister = registerBuddyIpc({ ipcMain, buddy, isBuddy: (sender) => sender === win.webContents, openRoute: () => {}, openMenu: () => {} });
  win.webContents.on("did-finish-load", () => {
    pushToBuddy(win, "placement", buddy.placement.mode);
    pushToBuddy(win, "layout", buddy.layout);
    pushToBuddy(win, "away", false);
    pushToBuddy(win, "hidden", false);
  });
  const page = (js) => win.webContents.executeJavaScript(js);
  /** Until the page says *js*, for as long as a loaded runner may take, then on. */
  const until = async (js, what) => {
    for (let t = 0; t < 150; t += 1) {
      if (await page(js).catch(() => false)) return;
      await wait(100);
    }
    throw new Error(`the page never showed ${what}`);
  };
  const ring = async (mode) => { fixtures.mode = mode; pushToBuddy(win, "ring"); await wait(500); };
  const result = { steps: [] };
  const look = async (step) => {
    await wait(700);
    const { side, opens, orb, card } = buddy.layout;
    result.steps.push({ step, ...(await page(MEASURE_JS)), layout: { side, opens, orb, card }, window: win.getBounds() });
  };
  fixtures.mode = "animating";
  await win.loadURL(`${SCHEME}://app/index.html#/buddy`);
  buddy.show();
  await until("document.querySelector('[data-buddy-presence][data-room=compact] [data-buddy-card-surface]') !== null", "work running with its line");
  await look("work running");
  await page("document.querySelector('[data-buddy-orb-slot] button').click(), true");
  await until("document.querySelector('[data-buddy-presence][data-room=expanded]') !== null", "the card open");
  await look("card open");
  await page("document.querySelector('[data-full-screen=buddy]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })), true");
  await until("document.querySelector('[data-buddy-presence][data-room=compact]') !== null", "the card closed");
  await look("card closed");
  await ring("approval");
  await until("document.querySelector('[role=group][aria-label^=\"Permission needed\"]') !== null", "the alert");
  await look("an alert");
  // The whole-UI zoom Appearance offers at its ends (80 % and 150 %), and twice the size.
  for (const zoom of [0.8, 1.5, 2]) {
    await page(`document.documentElement.style.zoom = '${zoom}', true`);
    await look(`zoom ${zoom * 100}%`);
  }
  await page("document.documentElement.style.zoom = '1', true");

  const wa = screen.getPrimaryDisplay().workArea;
  const b = win.getBounds();
  const start = { x: b.x + buddy.layout.orb.x, y: b.y + buddy.layout.orb.y };
  const dx = -Math.round(wa.width / 4);
  const dy = Math.round(wa.height / 4);
  buddy.beginDrag();
  for (let i = 1; i <= 4; i += 1) {
    buddy.dragBy(dx / 4, dy / 4);
    await wait(40);
  }
  buddy.endDrag({ instant: true });
  result.expectedDrop = { x: start.x + dx, y: start.y + dy, width: buddy.layout.orb.width, height: buddy.layout.orb.height };
  await look("dropped");
  unregister();
  buddy.destroy();
  out.orbBox = result;
}

/** The buddy with its card closed: it shapes itself to the orb, as the page does, and keeps every
 *  pointer event it gets, whether inside the orb or not. */
const SHAPE_HTML = `<!doctype html><html><body style="margin:0;background:transparent"><script>
  window.__outside = [];
  window.__insidePresses = 0;
  window.__downs = [];
  let orb = null;
  const inOrb = (e) => !!orb && e.clientX >= orb.x && e.clientX < orb.x + orb.width && e.clientY >= orb.y && e.clientY < orb.y + orb.height;
  for (const type of ["mousemove", "mousedown", "mouseup", "click", "wheel", "pointerdown"]) {
    window.addEventListener(type, (e) => {
      if (type === "mousedown" && window.__downs.length < 20) window.__downs.push([e.clientX, e.clientY, e.screenX, e.screenY]);
      if (!inOrb(e)) window.__outside.push([type, e.screenX, e.screenY, Math.round(performance.now())]);
      else if (type === "mousedown") window.__insidePresses += 1;
    }, true);
  }
  window.addEventListener("mousemove", (e) => window.pclawBuddy.pointerOver(inOrb(e)));
  window.pclawBuddy.onLayout((l) => { orb = l.orb; window.pclawBuddy.setRegions([l.orb]); });
</script></body></html>`;

/** The window below the buddy's: it keeps where each press reached it. */
const BELOW_HTML = `<!doctype html><html><body style="margin:0;background:#202020"><script>
  window.__presses = [];
  window.addEventListener("mousedown", (e) => window.__presses.push([e.screenX, e.screenY]), true);
</script></body></html>`;

/** Where real clicks over the buddy's window land while its card is closed (#5216): every one
 *  outside the orb on the window below, none on the buddy, and one on the orb on the buddy. */
async function runClickThrough() {
  if (process.env.PC_PROBE_OS_CLICKS !== "1") return { skipped: "real input is posted only on a display of the run's own (xvfb-run), never on a desktop someone is using" };
  const { screen } = require("electron");
  const { makeBuddyWindow, pushToBuddy, registerBuddyIpc } = require(path.join(ROOT, "buddyWindow.js"));
  const placement = require(path.join(ROOT, "buddyPlacement.js"));
  const data = {};
  const store = { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, removeItem: (k) => { delete data[k]; } };
  const buddy = makeBuddyWindow({ BrowserWindowCtor: BrowserWindow, screen, store, preload: path.join(ROOT, "buddyPreload.js") });
  // The window below first: with no window manager here nothing keeps the buddy's always-on-top
  // window above, and an X window is created on top of the ones before it.
  // Larger than the buddy's by a margin, and not resizable: a frameless window that can be resized
  // keeps a band at its edges for the resize handles, where a press reaches no page.
  const at = placement.resolvePlacement({ placement: placement.readPlacement(store), displays: screen.getAllDisplays(), primary: screen.getPrimaryDisplay() }).layout.window;
  const MARGIN = 24;
  const below = new BrowserWindow({ x: at.x - MARGIN, y: at.y - MARGIN, width: at.width + 2 * MARGIN, height: at.height + 2 * MARGIN, show: false, frame: false, resizable: false });
  await below.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(BELOW_HTML)}`);
  below.showInactive();
  await wait(400);
  const win = buddy.create();
  // Every shape the window is given, for the report.
  const shapes = [];
  const setShape = win.setShape.bind(win);
  win.setShape = (rects) => { shapes.push(rects); setShape(rects); };
  const unregister = registerBuddyIpc({ ipcMain, buddy, isBuddy: (sender) => sender === win.webContents, openRoute: () => {}, openMenu: () => {} });
  // Every press each window's web contents is sent, whatever its page does with it: the count at
  // the window, beside the one its page keeps.
  const raw = { buddy: [], below: [] };
  win.webContents.on("input-event", (_e, input) => { if (input.type === "mouseDown") raw.buddy.push([input.x, input.y]); });
  below.webContents.on("input-event", (_e, input) => { if (input.type === "mouseDown") raw.below.push([input.x, input.y]); });
  const b = buddy.layout.window;
  win.webContents.on("did-finish-load", () => pushToBuddy(win, "layout", buddy.layout));
  await win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(SHAPE_HTML)}`);
  buddy.show();
  await wait(800);

  const o = buddy.layout.orb;
  const orb = { x: b.x + o.x, y: b.y + o.y, width: o.width, height: o.height };
  const inWindow = ([x, y]) => x >= b.x && x < b.x + b.width && y >= b.y && y < b.y + b.height;
  const inOrb = ([x, y]) => x >= orb.x && x < orb.x + orb.width && y >= orb.y && y < orb.y + orb.height;
  const clicks = [];
  // Every pixel just outside the orb, then the whole window every 12 points, and its far corner.
  for (let x = orb.x - 1; x <= orb.x + orb.width; x += 1) clicks.push([x, orb.y - 1], [x, orb.y + orb.height]);
  for (let y = orb.y; y < orb.y + orb.height; y += 1) clicks.push([orb.x - 1, y], [orb.x + orb.width, y]);
  for (let y = b.y; y < b.y + b.height; y += 12) for (let x = b.x; x < b.x + b.width; x += 12) clicks.push([x, y]);
  clicks.push([b.x + b.width - 1, b.y + b.height - 1]);
  const outside = clicks.filter((p) => inWindow(p) && !inOrb(p));
  const control = [Math.round(orb.x + orb.width / 2), Math.round(orb.y + orb.height / 2)];
  const buddyPage = (js) => win.webContents.executeJavaScript(js);
  const belowPage = (js) => below.webContents.executeJavaScript(js);
  /** Until the windows have had every press posted, for as long as a loaded runner may take: the
   *  pointer's moves queue ahead of the presses. */
  const settled = async (presses, orbPresses) => {
    const start = Date.now();
    while (Date.now() - start < 60_000) {
      if ((await belowPage("window.__presses.length")) >= presses && (await buddyPage("window.__insidePresses")) >= orbPresses) break;
      await wait(250);
    }
    await wait(300);
    return Date.now() - start;
  };
  // Which window is above which, as the X server has them.
  const stack = stacking();
  const order = stack.ids ? { buddy: stack.ids.indexOf(xid(win)), below: stack.ids.indexOf(xid(below)) } : stack;
  // From here on every pointer event is real input: what came before is the window's own (a move
  // Chromium makes at the pointer's resting place as the window shows), reported apart.
  const inputFrom = await buddyPage("Math.round(performance.now())");
  // A press on the orb before anything else: the window takes it, or the shape does not hold it.
  const first = postPointer({ clicks: [control] });
  await settled(0, 1);
  const firstOrbPress = await buddyPage("window.__insidePresses");
  const posted = first.ok ? postPointer({ sweep: b, clicks: [...outside, control] }) : first;
  const waitedMs = await settled(outside.length, firstOrbPress + 1);
  const presses = await belowPage("window.__presses");
  const out = {
    posted,
    window: b,
    orb,
    firstOrbPress,
    waitedMs,
    stacking: order,
    diagnosis: {
      shapes: shapes.slice(-3),
      layout: buddy.layout,
      buddyBounds: win.getBounds(),
      buddyContentBounds: win.getContentBounds(),
      belowBounds: below.getBounds(),
      control,
      buddyDowns: await buddyPage("window.__downs"),
      belowAtTheOrbPoints: presses.filter((p) => inOrb(p)).slice(0, 5),
      // Presses outside the orb that reached neither page, as points in the buddy's window.
      nowhere: (() => {
        const reached = new Set(presses.map(([x, y]) => `${x},${y}`));
        return outside.filter(([x, y]) => !reached.has(`${x},${y}`)).slice(0, 30).map(([x, y]) => [x - b.x, y - b.y]);
      })(),
    },
    clicked: outside.length,
    landedBelow: presses.filter((p) => !inOrb(p)).length,
    belowAtTheOrb: presses.filter((p) => inOrb(p)).length,
    ...(await (async () => {
      const heard = await buddyPage("window.__outside");
      const real = heard.filter((e) => e[3] >= inputFrom);
      return {
        inputFrom,
        // No press, click or wheel outside the orb, ever; and no move from the real input.
        buddyOutsidePresses: heard.filter((e) => e[0] !== "mousemove").slice(0, 20),
        buddyOutsideMovesFromInput: real.filter((e) => e[0] === "mousemove").slice(0, 20),
        // The window's own moves before any input, counted and shown.
        strayMoves: heard.filter((e) => e[0] === "mousemove" && e[3] < inputFrom).slice(0, 20),
      };
    })()),
    buddyOrbPresses: (await buddyPage("window.__insidePresses")) - firstOrbPress,
  };
  // The negative control: a page that shapes its whole window, as a closed card that still took
  // clicks would. The same clicks must then reach the buddy, or a clean pass proves nothing.
  const shapesBefore = shapes.length;
  await win.webContents.executeJavaScript("window.__outside = []; window.pclawBuddy.setRegions([{ x: 0, y: 0, width: innerWidth, height: innerHeight }]); true");
  await below.webContents.executeJavaScript("window.__presses = []; true");
  // Until the window has its new shape, then a moment for the X server to take it.
  for (let t = 0; t < 50 && shapes.length === shapesBefore; t += 1) await wait(100);
  await wait(300);
  const sample = outside.filter((_, i) => i % Math.ceil(outside.length / 20) === 0);
  const rawBefore = { buddy: raw.buddy.length, below: raw.below.length };
  const again = postPointer({ clicks: sample });
  for (let t = 0; t < 100 && raw.buddy.length + raw.below.length - rawBefore.buddy - rawBefore.below < sample.length; t += 1) await wait(100);
  await wait(500);
  out.control = {
    posted: again,
    clicked: sample.length,
    // At the window: the presses its web contents was sent.
    buddyPresses: raw.buddy.length - rawBefore.buddy,
    landedBelow: raw.below.length - rawBefore.below,
    // And as the pages heard them, for the report.
    buddyPagePresses: await win.webContents.executeJavaScript("window.__outside.filter((e) => e[0] === 'mousedown').length"),
    belowPagePresses: (await below.webContents.executeJavaScript("window.__presses")).length,
    shape: shapes.length > shapesBefore ? shapes[shapes.length - 1] : null,
    window: win.getBounds(),
  };
  // The second control: the other window raised above the buddy's takes even the orb's press, as
  // the earlier run's did, so a pass here is the stacking's and the shape's, told apart.
  below.moveTop();
  await wait(300);
  await belowPage("window.__presses = []; true");
  const orbPressesBefore = await buddyPage("window.__insidePresses");
  const raised = postPointer({ clicks: [control] });
  await wait(1200);
  out.raised = {
    posted: raised,
    landedBelow: (await belowPage("window.__presses")).length,
    buddyOrbPresses: (await buddyPage("window.__insidePresses")) - orbPressesBefore,
  };
  unregister();
  buddy.destroy();
  below.destroy();
  return out;
}

/** A page that keeps every layout it hears. */
const LAYOUT_HTML = `<!doctype html><html><body style="background:transparent"><script>
  window.__heard = [];
  window.pclawBuddy.onLayout((l) => window.__heard.push(l));
</script></body></html>`;

/** The buddy window's real size on this screen, before any drop: the orb's box and the card's
 *  area beside it (#5216). The probes measure a window of this size. */
function defaultLayoutSize() {
  const { screen } = require("electron");
  const { defaultSpot, layoutAt } = require(path.join(ROOT, "buddyPlacement.js"));
  const primary = screen.getPrimaryDisplay();
  const { width, height } = layoutAt(primary, defaultSpot(primary)).window;
  return { width, height };
}

/** A window above *bounds*, covering all of it. */
async function coverOver(bounds) {
  const cover = new BrowserWindow({ x: 0, y: 0, width: bounds.x + bounds.width + 40, height: bounds.y + bounds.height + 40, show: false, backgroundColor: "#000000", alwaysOnTop: true });
  cover.setAlwaysOnTop(true, "screen-saver");
  await cover.loadURL("data:text/html;charset=utf-8,<body style='background:black'></body>");
  cover.showInactive();
  return cover;
}

/** The orb's box in a window the shell's controller builds and places (#5216). */
async function runPlaced() {
  const { screen } = require("electron");
  const { makeBuddyWindow, pushToBuddy } = require(path.join(ROOT, "buddyWindow.js"));
  const { defaultSpot } = require(path.join(ROOT, "buddyPlacement.js"));
  const data = {};
  const store = { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, removeItem: (k) => { delete data[k]; } };
  const make = () => {
    const controller = makeBuddyWindow({
      BrowserWindowCtor: BrowserWindow,
      screen,
      store,
      preload: path.join(ROOT, "buddyPreload.js"),
      onLayout: (l) => pushToBuddy(controller.window, "layout", l),
    });
    return controller;
  };
  const out = { steps: [], boxes: [], bounds: [], defaultSpot: defaultSpot(screen.getPrimaryDisplay()) };
  const buddy = make();
  const win = buddy.create();
  win.webContents.on("did-finish-load", () => pushToBuddy(win, "layout", buddy.layout));
  await win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(LAYOUT_HTML)}`);
  const heard = () => win.webContents.executeJavaScript("window.__heard");
  /** The orb on the screen: the real window's position, and the orb's place in it as the page heard it. */
  const box = async (w, orb) => {
    const b = w.getBounds();
    return { x: b.x + orb.x, y: b.y + orb.y, width: orb.width, height: orb.height };
  };
  const look = async (step) => {
    await wait(250);
    const last = (await heard()).at(-1);
    out.steps.push(step);
    out.boxes.push(last ? await box(win, last.orb) : null);
    out.bounds.push(win.getBounds());
  };
  buddy.show();
  await look("shown");
  for (const p of ["orb", "line", "card", "line", "orb"]) {
    buddy.setPresentation(p);
    await look(p);
  }
  buddy.hide();
  await wait(250);
  buddy.show();
  await look("hidden, then shown");

  const wa = screen.getPrimaryDisplay().workArea;
  const start = out.boxes[0];
  const dx = -Math.round(wa.width / 4);
  const dy = Math.round(wa.height / 3);
  buddy.beginDrag();
  for (let i = 1; i <= 4; i += 1) {
    buddy.dragBy(dx / 4, dy / 4);
    await wait(30);
  }
  buddy.endDrag({ instant: true });
  await wait(400);
  out.expectedDrop = { x: start.x + dx, y: start.y + dy, width: start.width, height: start.height };
  out.dropped = await box(win, (await heard()).at(-1).orb);
  out.heard = await heard();
  buddy.destroy();

  const again = make();
  const win2 = again.create();
  again.show();
  await wait(250);
  out.restarted = await box(win2, again.layout.orb);
  again.destroy();
  return out;
}

const PAGE_HTML = `<!doctype html><html><body style="background:transparent">
<div data-buddy-shape style="width:56px;height:28px"></div>
<script>
  const c = { timers: 0, frames: 0 };
  window.__pcCounts = c;
  const st = window.setTimeout.bind(window), si = window.setInterval.bind(window), raf = window.requestAnimationFrame.bind(window);
  window.setTimeout = (...a) => { c.timers += 1; return st(...a); };
  window.setInterval = (...a) => { c.timers += 1; return si(...a); };
  window.requestAnimationFrame = (f) => { c.frames += 1; return raf(f); };
  let frame = null, beat = null;
  const start = () => {
    if (beat !== null) return;
    const tick = () => { frame = requestAnimationFrame(tick); };
    frame = requestAnimationFrame(tick);
    beat = setInterval(() => setTimeout(() => {}, 0), 250);
  };
  const stop = () => { cancelAnimationFrame(frame); clearInterval(beat); frame = null; beat = null; };
  if (${JSON.stringify(PAGE)} === "root") document.addEventListener("visibilitychange", () => (document.hidden ? stop() : start()));
  start();
</script></body></html>`;

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

app.whenReady().then(async () => {
  const out = { electronReady: true, page: PAGE, idleMs: IDLE_MS, preloadErrors: [] };
  if (PAGE === "route" || PAGE === "orbBox") {
    try {
      app.dock?.hide?.();
      if (PAGE === "route") await runRoute(out);
      else await runOrbBox(out);
    } catch (err) {
      out.error = String((err && err.stack) || err);
    }
    process.stdout.write(`PC_PROBE ${JSON.stringify(out)}\n`);
    app.exit(0);
    return;
  }
  try {
    app.dock?.hide?.();
    const { buddyWindowOptions, showOnScreen, hideFromScreen } = require(path.join(ROOT, "buddyWindow.js"));
    out.defaultLayoutSize = defaultLayoutSize();
    const options = buddyWindowOptions({ bounds: { x: 40, y: 40, ...out.defaultLayoutSize }, preload: path.join(ROOT, "buddyPreload.js") });
    const win = new BrowserWindow(options);
    out.windowSize = { width: win.getBounds().width, height: win.getBounds().height };
    win.setContentProtection(true);
    win.webContents.on("preload-error", (_e, _p, err) => out.preloadErrors.push(String((err && err.message) || err)));
    await win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(PAGE_HTML)}`);

    try {
      out.placed = await runPlaced();
    } catch (err) {
      out.placed = { error: String((err && err.stack) || err) };
    }
    try {
      out.clickThrough = await runClickThrough();
    } catch (err) {
      out.clickThrough = { error: String((err && err.stack) || err) };
    }

    const page = (js) => win.webContents.executeJavaScript(js);
    out.bridge = {
      buddy: await page("typeof window.pclawBuddy"),
      keys: await page("window.pclawBuddy ? Object.keys(window.pclawBuddy).sort() : []"),
      desktop: await page("typeof window.pclawDesktop"),
    };

    if (SHOW === "inactive") win.showInactive();
    else showOnScreen(win);
    await wait(800);
    out.shown = {
      visible: win.isVisible(),
      focused: win.isFocused(),
      focusedWindowIsBuddy: BrowserWindow.getFocusedWindow() === win,
      alwaysOnTop: win.isAlwaysOnTop(),
      contentProtected: contentProtection(win),
      documentHidden: await page("document.hidden"),
      frames: await page("window.__pcCounts.frames"),
    };

    // Covered by a window above it, then uncovered.
    const cover = await coverOver(win.getBounds());
    await wait(1500);
    out.covered = { documentHidden: await page("document.hidden") };
    cover.destroy();
    await wait(500);
    out.covered.afterDocumentHidden = await page("document.hidden");

    hideFromScreen(win);
    await wait(500);
    out.hidden = { documentHidden: await page("document.hidden") };
    await page("window.__pcCounts.timers = 0; window.__pcCounts.frames = 0;");
    const pid = win.webContents.getOSProcessId();
    const sample = () => app.getAppMetrics().find((m) => m.pid === pid);
    const before = sample();
    await wait(IDLE_MS);
    const after = sample();
    Object.assign(out.hidden, {
      timers: await page("window.__pcCounts.timers"),
      frames: await page("window.__pcCounts.frames"),
      rendererFound: Boolean(before && after),
      // Since the previous sample: the renderer's share of one core over the whole hidden span.
      percentCPU: after ? after.cpu.percentCPUUsage : null,
      cumulativeCPUSeconds: before && after && typeof after.cpu.cumulativeCPUUsage === "number"
        ? after.cpu.cumulativeCPUUsage - before.cpu.cumulativeCPUUsage
        : null,
      idleWakeupsPerSecond: after ? after.cpu.idleWakeupsPerSecond : null,
    });
    win.destroy();
  } catch (err) {
    out.error = String((err && err.stack) || err);
  }
  process.stdout.write(`PC_PROBE ${JSON.stringify(out)}\n`);
  app.exit(0);
});
