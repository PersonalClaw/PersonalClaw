"use strict";

/**
 * A real Electron main process whose windows are wired by a copy of `main.js`'s wiring around
 * `windowAppearance.js`; it switches the theme in stand-in pages and reports what the windows did.
 * `main.js`'s own listeners are held by the source pins in `windowAppearance.test.js`.
 *
 * Not a test: `windowAppearanceElectron.test.js` spawns it. Every window is shown inactive. The
 * stand-in pages keep the page's theme contract with no gateway: `data-mode`,
 * `data-mode-preference`, the body's ground, the theme colour, and `pclawDesktop.appearance.changed`
 * when the bridge is there; the dashboard's stand-in carries `web/index.html`'s own first-paint
 * script.
 *
 *   PC_PROBE_WIRING  "new" (default) wires as main.js does now; "old" replays fabc4dbc6's sync,
 *                    the mode pinned from `data-mode` on each load and focus and nothing else.
 *
 * Emits one line on stdout: PC_PROBE {"wiring":…,"legs":{…}}
 */

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { app, BaseWindow, WebContentsView, ipcMain, nativeTheme } = require("electron");

const DESKTOP = path.resolve(__dirname, "..", "..");
const {
  STORE_KEY, READ_SCRIPT, canvasOf, fromPageRead, launch, makeWindowAppearance, focusedAppWindow, appWindowOfSender,
} = require(path.join(DESKTOP, "windowAppearance"));
const { IPC_CHANNELS } = require(path.join(DESKTOP, "capabilities"));

const WIRING = process.env.PC_PROBE_WIRING === "old" ? "old" : "new";
const PRELOAD = path.join(DESKTOP, "preload.js");
const WITHIN_MS = 250;

app.disableHardwareAcceleration();
// Every leg destroys its windows: the app must not quit when the last one goes.
app.on("window-all-closed", () => {});

/** `web/index.html`'s first-paint script, so the dashboard's stand-in opens as the real one does. */
function firstPaintScript() {
  const html = fs.readFileSync(path.join(DESKTOP, "..", "web", "index.html"), "utf8");
  const head = html.slice(html.indexOf("<head>"), html.indexOf("</head>"));
  const after = head.slice(head.indexOf('<meta name="theme-color"'));
  const open = after.indexOf("<script>");
  const close = after.indexOf("</script>", open);
  if (open < 0 || close < 0) throw new Error("web/index.html has no first-paint script");
  return after.slice(open + "<script>".length, close);
}

/** The stand-in pages, written to a folder of their own. */
function pages() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pclaw-appearance-probe-"));
  const dashboard = `<!doctype html><html data-mode="dark"><head>
<meta name="theme-color" content="#0f0f0f"><script>${firstPaintScript()}</script>
<script>window.__firstMode = document.documentElement.dataset.mode</script>
<style>body{margin:0;height:100vh;background:#0f0f0f} html.light body{background:#f0f4f8}
#theme{position:fixed;left:10px;top:10px;width:120px;height:40px}</style>
<script>
  var stored = localStorage.getItem('mode');
  var pref = stored === 'dark' || stored === 'light' || stored === 'auto' ? stored : 'dark';
  var systemLight = matchMedia('(prefers-color-scheme: light)');
  function render() {
    var mode = pref === 'auto' ? (systemLight.matches ? 'light' : 'dark') : pref;
    var root = document.documentElement;
    root.classList.toggle('light', mode === 'light');
    root.dataset.mode = mode;
    root.dataset.modePreference = pref;
    localStorage.setItem('mode', pref);
    var canvas = mode === 'light' ? '#f0f4f8' : '#0f0f0f';
    document.querySelector('meta[name="theme-color"]').setAttribute('content', canvas);
    var shell = window.pclawDesktop && window.pclawDesktop.appearance;
    if (shell) shell.changed({ preference: pref, mode: mode, canvas: canvas });
  }
  function setTheme(p) { pref = p; render(); }
  systemLight.addEventListener('change', function () { if (pref === 'auto') render(); });
  addEventListener('DOMContentLoaded', function () {
    var button = document.createElement('button');
    button.id = 'theme';
    button.textContent = 'Theme';
    button.onclick = function () { setTheme(window.__next); };
    document.body.appendChild(button);
    render();
  });
</script></head><body></body></html>`;
  const older = `<!doctype html><html data-mode="dark"><head><meta name="theme-color" content="#0f0f0f">
<style>body{margin:0;background:#0f0f0f}</style></head><body></body></html>`;
  fs.writeFileSync(path.join(dir, "dashboard.html"), dashboard);
  fs.writeFileSync(path.join(dir, "older.html"), older);
  return {
    dashboard: path.join(dir, "dashboard.html"),
    older: path.join(dir, "older.html"),
    loading: path.join(DESKTOP, "loading.html"),
  };
}

/** A shell store in memory, holding `initial`. */
function memoryStore(initial = {}) {
  const data = { ...initial };
  return { getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); }, data };
}

const all = () => BaseWindow.getAllWindows();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const colourOf = (win) => String(win.getBackgroundColor() || "").toLowerCase().slice(0, 7);

/** Wait up to WITHIN_MS for `cond()`; how long it took, or null. */
async function within(cond) {
  const start = Date.now();
  while (Date.now() - start <= WITHIN_MS) {
    if (await cond()) return Date.now() - start;
    await sleep(10);
  }
  return null;
}

app.whenReady().then(async () => {
  process.stdout.write("PC_PROBE_READY\n");
  const out = { wiring: WIRING, legs: {} };
  const watchdog = setTimeout(() => { out.error = "timed out"; finish(); }, 90_000);
  let finished = false;
  function finish() {
    if (finished) return;
    finished = true;
    clearTimeout(watchdog);
    process.stdout.write(`PC_PROBE ${JSON.stringify(out)}\n`);
    app.exit(0);
  }
  try {
    app.dock?.hide?.();
    const page = pages();
    // The launch: the store keeps a light theme, as a second launch on this version would.
    const systemDark = nativeTheme.shouldUseDarkColors;
    const systemMode = systemDark ? "dark" : "light";
    const store = memoryStore({ [STORE_KEY]: JSON.stringify({ preference: "light", mode: "light", canvas: "#f0f4f8" }) });
    const launched = launch({ stored: store.getItem(STORE_KEY), systemDark });
    if (WIRING === "new") nativeTheme.themeSource = launched.themeSource;
    const wa = makeWindowAppearance({
      nativeTheme, store, canvas: launched.canvas, focusedAppWindow: () => focusedAppWindow(all),
    });

    function wire(win, view, bridged) {
      if (WIRING === "old") {
        // fabc4dbc6's syncNativeTheme: the mode pinned on each load and focus, never on a switch.
        const sync = () => view.webContents.executeJavaScript('document.documentElement.dataset.mode || ""')
          .then((m) => { if (m === "dark" || m === "light") nativeTheme.themeSource = m; }).catch(() => {});
        view.webContents.on("did-finish-load", sync);
        win.on("focus", sync);
        return;
      }
      const read = () => view.webContents.executeJavaScript(READ_SCRIPT).then((r) => {
        const a = fromPageRead(r);
        if (win.isDestroyed()) return;
        if (a) wa.apply(win, a);
        else wa.paint(win, nativeTheme.shouldUseDarkColors ? "dark" : "light");
      }).catch(() => {});
      win._pcReadAppearance = read;
      view.webContents.on("did-change-theme-color", read);
      view.webContents.on("did-finish-load", read);
      win.on("focus", read);
      let soon = null;
      if (!bridged) view.webContents.on("input-event", (_e, input) => {
        if (input.type !== "mouseUp" && input.type !== "keyUp") return;
        if (soon) return;
        soon = setTimeout(() => { soon = null; read(); }, 100);
      });
    }

    if (WIRING === "new") {
      ipcMain.on(IPC_CHANNELS.appearanceChanged, (e, payload) => {
        wa.changed(e.sender, payload, (s) => appWindowOfSender(all, s));
      });
      nativeTheme.on("updated", () => {
        for (const w of all()) {
          if (w.isDestroyed() || !w._pcView) continue;
          w._pcView.webContents.executeJavaScript(READ_SCRIPT).then((r) => {
            if (!w.isDestroyed() && fromPageRead(r) === null) wa.paint(w, nativeTheme.shouldUseDarkColors ? "dark" : "light");
          }).catch(() => {});
        }
      });
    }

    /** A window built as setupWindowContents builds one, shown inactive, with `file` loaded. */
    async function windowOn(file, { bridged, partition }) {
      const win = new BaseWindow({ show: false, width: 480, height: 360, backgroundColor: wa.canvas() });
      const argv = bridged && WIRING === "new" ? [`--pclaw-appearance=${wa.preference()}`] : null;
      const view = new WebContentsView({
        webPreferences: {
          ...(bridged ? { preload: PRELOAD, ...(argv ? { additionalArguments: argv } : {}) } : {}),
          contextIsolation: true,
          nodeIntegration: false,
          partition: partition || `probe-${Math.random().toString(36).slice(2)}`,
        },
      });
      view.setBackgroundColor("#00000000");
      win.contentView.addChildView(view);
      view.setBounds({ x: 0, y: 0, width: 480, height: 360 });
      win.webContents = view.webContents;
      win._pcView = view;
      win._pcBridgeAttached = bridged;
      wire(win, view, bridged);
      win.showInactive();
      await view.webContents.loadFile(file);
      await sleep(50);
      return { win, view, js: (s) => view.webContents.executeJavaScript(s) };
    }

    // C2: a fresh origin with no 'mode' opens in the kept theme from its first frame.
    {
      const bridged = await windowOn(page.dashboard, { bridged: true });
      const plain = await windowOn(page.loading, { bridged: false });
      const kept = await bridged.js("window.pclawDesktop && window.pclawDesktop.appearance ? window.pclawDesktop.appearance.kept : 'no bridge'");
      out.legs.kept = {
        kept,
        plainBridge: await plain.js("typeof window.pclawDesktop"),
        firstMode: await bridged.js("window.__firstMode"),
        colour: colourOf(bridged.win),
        storeSays: (() => { try { return JSON.parse(store.data[STORE_KEY]).preference; } catch { return ""; } })(),
      };
      out.legs.kept.ok = kept === "light" && out.legs.kept.plainBridge === "undefined"
        && out.legs.kept.firstMode === "light" && out.legs.kept.colour === "#f0f4f8" && out.legs.kept.storeSays === "light";
      bridged.win.destroy();
      plain.win.destroy();
    }

    // The brief's legs: dark, light, System, dark, by the bridge; light, dark, light by the meta.
    for (const [leg, bridgedLeg, steps] of [
      ["bridge", true, ["dark", "light", "auto", "dark"]],
      ["meta", false, ["light", "dark", "light"]],
    ]) {
      const w = await windowOn(page.dashboard, { bridged: bridgedLeg });
      const rows = [];
      for (const pref of steps) {
        await w.js(`setTheme(${JSON.stringify(pref)})`);
        const mode = pref === "auto" ? systemMode : pref;
        const source = pref === "auto" ? "system" : pref;
        const ms = await within(() => colourOf(w.win) === canvasOf(mode) && nativeTheme.themeSource === source);
        rows.push({ pref, ms, ok: ms !== null, colour: colourOf(w.win), themeSource: nativeTheme.themeSource });
      }
      out.legs[leg] = rows;
      w.win.destroy();
    }

    // An older page: pinned to its mode on load and on focus, and never kept.
    {
      wa.flush();
      nativeTheme.themeSource = "light";
      const before = store.data[STORE_KEY];
      const w = await windowOn(page.older, { bridged: false });
      const onLoad = await within(() => nativeTheme.themeSource === "dark");
      nativeTheme.themeSource = "light";
      w.win.emit("focus");
      const onFocus = await within(() => nativeTheme.themeSource === "dark");
      wa.flush();
      out.legs.older = { onLoad, onFocus, kept: store.data[STORE_KEY] === before };
      out.legs.older.ok = onLoad !== null && onFocus !== null && out.legs.older.kept;
      w.win.destroy();
    }

    // C3: a window on the loading page follows a switch made in another window.
    {
      // b, on the loading page, opens first, so a is the newest window: under xvfb the newest can
      // read focused, and a focused b would keep a's switch from setting themeSource.
      const b = await windowOn(page.loading, { bridged: true });
      const a = await windowOn(page.dashboard, { bridged: true });
      await a.js("setTheme('dark')");
      const toDark = await within(() => colourOf(b.win) === "#0f0f0f");
      await a.js("setTheme('light')");
      const toLight = await within(() => colourOf(b.win) === "#f0f4f8");
      out.legs.loading = { toDark, toLight, ok: toDark !== null && toLight !== null, colour: colourOf(b.win) };
      a.win.destroy();
      b.win.destroy();
    }

    // C1: a click on the Theme button in a view with no bridge, a switch that keeps the colour.
    {
      const w = await windowOn(page.dashboard, { bridged: false });
      await w.js(`setTheme(${JSON.stringify(systemMode)})`);
      await within(() => nativeTheme.themeSource === systemMode);
      await w.js("window.__next = 'auto'");
      const box = await w.js("(() => { const r = document.getElementById('theme').getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }; })()");
      w.view.webContents.sendInputEvent({ type: "mouseDown", x: box.x, y: box.y, button: "left", clickCount: 1 });
      w.view.webContents.sendInputEvent({ type: "mouseUp", x: box.x, y: box.y, button: "left", clickCount: 1 });
      const ms = await within(() => nativeTheme.themeSource === "system");
      out.legs.click = { ms, ok: ms !== null, themeSource: nativeTheme.themeSource, pref: await w.js("document.documentElement.dataset.modePreference") };
      w.win.destroy();
    }
  } catch (err) {
    out.error = String((err && err.stack) || err);
  }
  finish();
});
