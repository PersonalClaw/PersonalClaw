"use strict";

/**
 * A real Electron main process that builds the bridged view the way `setupWindowContents`
 * builds it, then reports what the RENDERER actually got (#3348).
 *
 * Not a test — the harness `bridgeLoads.test.js` spawns. It is deliberately standalone: it
 * requires no shell module, so a failure to emit its one `PC_PROBE` line means Electron could
 * not start on this host, never that the shell is broken. That separation is what lets the
 * test skip honestly on a machine with no display instead of pretending to pass.
 *
 * Driven entirely by env, so the test can re-run it per mutation:
 *   PC_PROBE_PRELOAD    absolute path to the preload to attach (required)
 *   PC_PROBE_SANDBOX    "false" to pass `sandbox: false`; anything else leaves Electron's
 *                       default (sandbox ON). The test reads which one from `main.js`.
 *   PC_PROBE_NAMESPACE  the `window` key to look for (default `pclawDesktop`) — so the same
 *                       harness can carry the test's own negative control.
 *
 * Emits exactly one line on stdout:
 *   PC_PROBE {"electronReady":true,"bridge":"object","keys":[…],"preloadErrors":[]}
 */

const { app, BrowserWindow, WebContentsView } = require("electron");

const PRELOAD = process.env.PC_PROBE_PRELOAD;
const SANDBOX_OFF = process.env.PC_PROBE_SANDBOX === "false";
const NAMESPACE = process.env.PC_PROBE_NAMESPACE || "pclawDesktop";

// Nothing here wants a GPU, a dock icon or the user's focus — other work may be running on
// this machine, and a probe that steals the foreground is a probe nobody will run.
app.disableHardwareAcceleration();

const preloadErrors = [];

app.whenReady().then(async () => {
  const out = { electronReady: true, bridge: "unknown", keys: [], preloadErrors };
  try {
    app.dock?.hide?.();
    const win = new BrowserWindow({ show: false, width: 400, height: 300 });
    const view = new WebContentsView({
      webPreferences: {
        preload: PRELOAD,
        ...(SANDBOX_OFF ? { sandbox: false } : {}),
        contextIsolation: true,
        nodeIntegration: false,
      },
    });
    // Attached BEFORE the load: a sandboxed preload that cannot resolve its require throws
    // here and NOWHERE else — the page itself loads fine, which is exactly why a dead bridge
    // never logged.
    view.webContents.on("preload-error", (_event, _path, err) => {
      preloadErrors.push(String((err && err.message) || err));
    });
    win.contentView.addChildView(view);
    await view.webContents.loadURL("about:blank");
    const ns = JSON.stringify(NAMESPACE);
    out.namespace = NAMESPACE;
    out.bridge = await view.webContents.executeJavaScript(`typeof window[${ns}]`);
    out.keys = await view.webContents.executeJavaScript(
      `window[${ns}] ? Object.keys(window[${ns}]).sort() : []`,
    );
  } catch (err) {
    out.error = String((err && err.stack) || err);
  }
  process.stdout.write(`PC_PROBE ${JSON.stringify(out)}\n`);
  app.exit(0);
});
