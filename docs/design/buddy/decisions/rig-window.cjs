// The rig spike's scratch window (#3887): one Electron window on the harness route, measured from
// the main process. `rig-measure.mjs` starts it and reads its one result line; run that, not this.
//
// It is a real renderer signed in the way the desktop app's own windows are: the gateway's session
// cookie for the gateway's origin, set on the window's session by the shell's own sign-in module
// (`desktop/localSignIn.js`), never a token in a URL. The session is an in-memory partition and the
// app's data folder a fresh temporary one, so nothing of the run outlives it.
//
// What it measures, in this order, for the one candidate it was given:
//
//   1. the chunks the harness loads with no candidate, then with this candidate: the launcher
//      subtracts the first from the second to find the chunks only the candidate loads. The
//      harness alone is sampled too, so the renderer's own cost can be told from the candidate's;
//   2. 60 s at rest, in the quiet family with nothing moving: `app.getAppMetrics()` each second
//      for the renderer (`webContents.getOSProcessId()`), its CPU and working set, and the
//      animation-frame callbacks the page ran, counted by a script that runs before any of the
//      app's own, so the count sees every caller (the zero-frames bar);
//   3. 60 s looping the transitions: into each of the four families and the focused card, then
//      the active family's working hold alone, for reading the loop's number against;
//   4. a scheme change while it runs, and the frames until the painted dots follow it (again, as a
//      still, under reduced motion in step 6);
//   5. both dials at 0 and 1: each pose move's overshoot and travel, read from the pose's values;
//   6. the system's reduced-motion setting on, emulated through the DevTools protocol: the loop
//      again, the frames it ran, and how many times each pose value changed (a jump is once).
//
// The window is shown without taking focus in the screen's lower right corner, and kept above other
// windows on every space, a full-screen app's among them, for the run: a covered window's frames are
// throttled, and "at rest, visible" is the case the bar names.

"use strict";

const { app, BrowserWindow, session } = require("electron");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { makeLocalSignIn } = require("../../../../desktop/localSignIn.js");

/** Counts every animation-frame callback the page runs, the distinct frames they ran in, and each
 *  time the page was hidden (a covered window or a locked screen stops its frames). */
const FRAME_COUNTER = `(() => {
  const request = window.requestAnimationFrame.bind(window);
  let callbacks = 0, frames = 0, last = -1, hidden = 0;
  window.requestAnimationFrame = (cb) => request((t) => {
    callbacks += 1;
    if (t !== last) { frames += 1; last = t; }
    cb(t);
  });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") hidden += 1; });
  Object.defineProperty(window, "__rigFrames", { value: () => ({ callbacks, frames, hidden }) });
})();`;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** `promise`, or a failure naming `what` after `ms`: a probe that waits on frames waits forever on a
 *  window that gets none. */
function within(ms, what, promise) {
  let timer;
  const late = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${what} took over ${ms / 1000} s: was the window covered or the screen locked?`)), ms);
  });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}
const log = (msg) => process.stderr.write(`rig-window: ${msg}\n`);

function readConfig() {
  return new Promise((resolve, reject) => {
    let text = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      text += chunk;
      const end = text.indexOf("\n");
      if (end >= 0) {
        try {
          resolve(JSON.parse(text.slice(0, end)));
        } catch (err) {
          reject(err);
        }
      }
    });
    process.stdin.on("end", () => reject(new Error("no configuration on stdin")));
  });
}

app.setPath("userData", fs.mkdtempSync(path.join(os.tmpdir(), "buddy-rig-")));
app.dock?.hide();

async function main() {
  const config = await readConfig();
  await app.whenReady();
  log(`signing in to ${new URL(config.url).origin}`);
  const origin = new URL(config.url).origin;
  const ses = session.fromPartition("buddy-rig");
  await makeLocalSignIn({ cookies: ses.cookies, log }).adopt({
    url: origin,
    port: Number(new URL(origin).port),
    token: config.token,
  });

  // The display the run asked for by its rate, whichever is the main one: a bar set at 120 Hz is
  // not met at 60.
  const displays = require("electron").screen.getAllDisplays();
  const shown = displays.find((d) => Math.round(d.displayFrequency) === config.hz);
  if (!shown) throw new Error(`no display refreshes ${config.hz} times a second: ${displays.map((d) => Math.round(d.displayFrequency)).join(", ")} Hz`);
  const area = shown.workArea;
  const win = new BrowserWindow({
    width: 420,
    height: 600,
    x: area.x + area.width - 420,
    y: area.y + area.height - 600,
    show: false,
    alwaysOnTop: true,
    webPreferences: { session: ses, contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  // A window that stays on the desktop's space is hidden whenever the owner works in a full-screen
  // app, and a hidden window draws nothing.
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.showInactive();
  log("window shown");
  const wc = win.webContents;
  // The DevTools protocol answers only once the view has a document of its own.
  await win.loadURL("about:blank");
  wc.debugger.attach("1.3");
  const cdp = (method, params) => wc.debugger.sendCommand(method, params);
  await cdp("Page.enable");
  // Every load from the gateway, never from a cache, so each chunk the page loads is in its timings.
  await cdp("Network.enable");
  await cdp("Network.setCacheDisabled", { cacheDisabled: true });
  await cdp("Page.addScriptToEvaluateOnNewDocument", { source: FRAME_COUNTER });
  const js = (code) => wc.executeJavaScript(code, true);

  async function until(code, ms = 30000) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      if (await js(code).catch(() => false)) return;
      await sleep(100);
    }
    throw new Error(`timed out waiting for: ${code}`);
  }

  async function open(view, candidates) {
    await win.loadURL("about:blank");
    await win.loadURL(`${origin}/#/buddy-rig?view=${view}&size=${config.size}&fps=${config.fps ?? 0}`);
    await until(`!!window.__buddyRig && document.querySelectorAll('[data-rig-candidate]').length === ${candidates}`);
    return js(`performance.getEntriesByType('resource').map((r) => new URL(r.name).pathname).filter((p) => p.startsWith('/assets/'))`);
  }

  const pid = () => wc.getOSProcessId();
  // CPU in percent of one core, from each process's cumulative CPU time over the second between two
  // reads. Electron's `percentCPUUsage` is kept beside it as `reportedCpu`: it is divided by the
  // machine's core count, so it is a share of the whole machine rather than of one core.
  async function sample(seconds) {
    const last = new Map();
    const of = (p, at) => {
      if (!p) return null;
      const used = p.cpu.cumulativeCPUUsage;
      const before = last.get(p.pid);
      last.set(p.pid, { used, at });
      const cpu = before && typeof used === "number" ? ((used - before.used) / ((at - before.at) / 1000)) * 100 : null;
      return { cpu, reportedCpu: p.cpu.percentCPUUsage, idleWakeups: p.cpu.idleWakeupsPerSecond, workingSetKb: p.memory.workingSetSize };
    };
    const read = () => {
      const at = Date.now();
      const metrics = app.getAppMetrics();
      return {
        renderer: of(metrics.find((p) => p.pid === pid()), at),
        gpu: of(metrics.find((p) => p.type === "GPU"), at),
        browser: of(metrics.find((p) => p.type === "Browser"), at),
      };
    };
    read();
    const before = await js("window.__rigFrames()");
    const rows = [];
    for (let t = 1; t <= seconds; t++) {
      await sleep(1000);
      rows.push({ t, ...read(), load: os.loadavg() });
    }
    const after = await js("window.__rigFrames()");
    if (after.hidden > before.hidden) throw new Error("the window was hidden during a sample, so its frames stopped");
    return { rows, callbacks: after.callbacks - before.callbacks, frames: after.frames - before.frames };
  }

  log(`baseline: the harness with no candidate`);
  const baseline = await open("none", 0);
  await sleep(3000);
  const harness = await sample(config.harnessSeconds);
  log(`candidate ${config.candidate}`);
  const loaded = await open(config.candidate, 1);
  // The orb must be on screen: a drawing scrolled out of view pauses, and would measure as free.
  const onScreen = await js(`(() => { const r = document.querySelector('[data-rig-candidate]').getBoundingClientRect(); return r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth })()`);
  if (!onScreen) throw new Error("the candidate is not inside the window, so its drawing would pause");
  const start = await js("window.__buddyRig.state()");
  await sleep(config.settleMs);

  log(`at rest for ${config.seconds} s`);
  const rest = await sample(config.seconds);

  log(`looping for ${config.seconds} s`);
  await js(`window.__buddyRig.loop(true, ${config.stepMs})`);
  const loop = await sample(config.seconds);
  await js("window.__buddyRig.loop(false); window.__buddyRig.card(false)");

  // Not a bar of its own: the active family's working hold alone, the pose an orb holds for as long
  // as an agent works, so the loop's number can be read against its steadiest part.
  log(`the working hold for ${config.holdSeconds} s`);
  await js("window.__buddyRig.show('tool')");
  await sleep(1500);
  const hold = await sample(config.holdSeconds);

  log("retint");
  const other = start.scheme === "jade" ? "ocean" : "jade";
  await js("window.__buddyRig.show('thinking')");
  await sleep(1500);
  const moving = await within(60000, "the retint", js(`window.__buddyRig.retint('${other}')`));
  await js(`window.__buddyRig.scheme('${start.scheme}')`);
  await sleep(1000);

  log("the dials");
  const dials = [];
  for (const bounciness of [0, 1]) {
    for (const expressiveness of [0, 1]) {
      await js(`window.__buddyRig.dials(${bounciness}, ${expressiveness})`);
      for (const [from, to, key] of [["idle", "approval", "spread"], ["idle", "input", "lift"], ["idle", "error", "sink"], ["idle", "done", "swirl"]]) {
        const moved = await within(60000, "a dial's move", js(`window.__buddyRig.dial('${from}', '${to}', '${key}')`));
        dials.push({ bounciness, expressiveness, from, to, key, ...moved[config.candidate] });
      }
    }
  }
  await js(`window.__buddyRig.dials(${start.dials.bounciness}, ${start.dials.expressiveness}); window.__buddyRig.show('idle')`);
  await sleep(1500);

  log(`reduced motion for ${config.reducedSeconds} s`);
  await cdp("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
  await sleep(500);
  const reducedState = await js("window.__buddyRig.state().reduced");
  await js("window.__rigChanges = window.__buddyRig.countChanges(); true");
  await js(`window.__buddyRig.loop(true, ${config.stepMs})`);
  const reduced = await sample(config.reducedSeconds);
  await js("window.__buddyRig.loop(false)");
  const changes = await js("window.__rigChanges()");
  // A still repaints only when something changes it: under reduced motion the active family is a
  // still, so the scheme change here must reach dots that no frame loop is redrawing.
  log("retint, still");
  await js("window.__buddyRig.show('thinking')");
  await sleep(1500);
  const still = await within(60000, "the still retint", js(`window.__buddyRig.retint('${other}')`));
  await js(`window.__buddyRig.scheme('${start.scheme}')`);
  await cdp("Emulation.setEmulatedMedia", { features: [] });

  const result = {
    candidate: config.candidate,
    fps: config.fps ?? null,
    versions: { electron: process.versions.electron, chrome: process.versions.chrome, node: process.versions.node },
    window: (() => {
      const display = require("electron").screen.getDisplayMatching(win.getBounds());
      return { width: 420, height: 600, scaleFactor: display.scaleFactor, refreshHz: display.displayFrequency };
    })(),
    start,
    resources: { baseline, loaded },
    harness,
    rest,
    loop,
    hold,
    retint: { moving, still },
    dials,
    reduced: { setting: reducedState, ...reduced, valueChanges: changes, steps: Math.floor((config.reducedSeconds * 1000) / config.stepMs) },
  };
  process.stdout.write(`RESULT ${JSON.stringify(result)}\n`);
  win.destroy();
  app.quit();
}

main().catch((err) => {
  log(err && err.stack ? err.stack : String(err));
  app.exit(1);
});
