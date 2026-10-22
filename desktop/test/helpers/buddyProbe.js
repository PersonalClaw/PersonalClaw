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
 * `PC_PROBE_PAGE=route` loads the real thing instead (#3902): the built `web/dist` at `#/buddy`,
 * served over a `pclaw-probe:` scheme of the probe's own (no socket), whose `/api/running`,
 * `/api/waiting` and `/api/listening` answer from fixtures, shown and hidden as the shell shows and
 * hides it (`showOnScreen`, `hideFromScreen`). It measures the buddy's renderer in each mode for the
 * idle span: still (shown, nothing running, nothing to draw), animating (one errand thinking),
 * covered (animating under a window above it, which the shell's show keeps drawing, #5210) and
 * hidden, each with its CPU, wakeups, working set, frames and timers.
 *
 * Env:
 *   PC_PROBE_PAGE     `root` (default), `ticks` or `route`
 *   PC_PROBE_DIST     the built SPA for `route` (default `web/dist` beside the shell)
 *   PC_PROBE_SHOW     `shell` (default, `showOnScreen`) or `inactive`
 *   PC_PROBE_IDLE_MS  how long to keep the window hidden and watch it (default 60000)
 *
 * Emits exactly one line on stdout:
 *   PC_PROBE {"electronReady":true,"shown":{…},"hidden":{…},"bridge":{…},…}
 */

const path = require("node:path");
const fs = require("node:fs");
const { app, BrowserWindow, protocol } = require("electron");
const { contentProtection } = require("./electronHost");

const PAGE = ["ticks", "route"].includes(process.env.PC_PROBE_PAGE) ? process.env.PC_PROBE_PAGE : "root";
const SHOW = process.env.PC_PROBE_SHOW === "inactive" ? "inactive" : "shell";
const IDLE_MS = Number(process.env.PC_PROBE_IDLE_MS) > 0 ? Number(process.env.PC_PROBE_IDLE_MS) : 60_000;
const ROOT = path.join(__dirname, "..", "..");

app.disableHardwareAcceleration();

// ── The route page: the built SPA over the probe's own scheme (#3902) ──
const SCHEME = "pclaw-probe";
const DIST = process.env.PC_PROBE_DIST || path.join(ROOT, "..", "web", "dist");
if (PAGE === "route") {
  protocol.registerSchemesAsPrivileged([
    { scheme: SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } },
  ]);
}
/** What the gateway's lists say in each mode: nothing at all (still), or one errand thinking. */
const fixtures = { mode: "still" };
const ERRAND = {
  work: "chat:probe", kind: "chat", session: "probe", phase: "thinking", line: "Thinking", since: null,
  name: "Release notes", agent: "", started_at: new Date().toISOString(), route: "#/chat/probe", blocked: false, waiting: [],
};
const API = {
  "/api/running": () => ({ running: fixtures.mode === "still" ? [] : [ERRAND], finished: [] }),
  "/api/waiting": () => ({ waiting: [], unreadable: [] }),
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
  const options = buddyWindowOptions({ bounds: { x: 40, y: 40, width: 360, height: 120 }, preload: path.join(ROOT, "buddyPreload.js") });
  const win = new BrowserWindow(options);
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
  const cover = new BrowserWindow({ x: 0, y: 0, width: 600, height: 300, show: false, backgroundColor: "#000000", alwaysOnTop: true });
  cover.setAlwaysOnTop(true, "screen-saver");
  await cover.loadURL("data:text/html;charset=utf-8,<body style='background:black'></body>");
  cover.showInactive();
  await wait(1000);
  out.modes.covered = await measure(win, IDLE_MS);
  cover.destroy();
  hideFromScreen(win);
  await wait(800);
  out.modes.hidden = await measure(win, IDLE_MS);
  win.destroy();
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
  if (PAGE === "route") {
    try {
      app.dock?.hide?.();
      await runRoute(out);
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
    const options = buddyWindowOptions({ bounds: { x: 40, y: 40, width: 360, height: 28 }, preload: path.join(ROOT, "buddyPreload.js") });
    const win = new BrowserWindow(options);
    win.setContentProtection(true);
    win.webContents.on("preload-error", (_e, _p, err) => out.preloadErrors.push(String((err && err.message) || err)));
    await win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(PAGE_HTML)}`);

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
    const cover = new BrowserWindow({ x: 0, y: 0, width: 600, height: 300, show: false, backgroundColor: "#000000", alwaysOnTop: true });
    cover.setAlwaysOnTop(true, "screen-saver");
    await cover.loadURL("data:text/html;charset=utf-8,<body style='background:black'></body>");
    cover.showInactive();
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
