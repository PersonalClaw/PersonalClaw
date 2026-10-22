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
 * Env:
 *   PC_PROBE_PAGE     `root` (default) or `ticks`
 *   PC_PROBE_IDLE_MS  how long to keep the window hidden and watch it (default 60000)
 *
 * Emits exactly one line on stdout:
 *   PC_PROBE {"electronReady":true,"shown":{…},"hidden":{…},"bridge":{…},…}
 */

const path = require("node:path");
const { app, BrowserWindow } = require("electron");

const PAGE = process.env.PC_PROBE_PAGE === "ticks" ? "ticks" : "root";
const IDLE_MS = Number(process.env.PC_PROBE_IDLE_MS) > 0 ? Number(process.env.PC_PROBE_IDLE_MS) : 60_000;
const ROOT = path.join(__dirname, "..", "..");

app.disableHardwareAcceleration();

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
  try {
    app.dock?.hide?.();
    const { buddyWindowOptions } = require(path.join(ROOT, "buddyWindow.js"));
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

    win.showInactive();
    await wait(800);
    out.shown = {
      visible: win.isVisible(),
      focused: win.isFocused(),
      focusedWindowIsBuddy: BrowserWindow.getFocusedWindow() === win,
      alwaysOnTop: win.isAlwaysOnTop(),
      contentProtected: typeof win.isContentProtected === "function" ? win.isContentProtected() : null,
      documentHidden: await page("document.hidden"),
      frames: await page("window.__pcCounts.frames"),
    };

    win.hide();
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
