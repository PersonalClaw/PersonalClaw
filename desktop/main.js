const { app, BaseWindow, BrowserWindow, WebContentsView, shell, dialog, Tray, Menu, nativeImage, nativeTheme } = require("electron");
const fs = require("fs");
const os = require("os");
const { spawn, execFileSync } = require("child_process");
const path = require("path");
const http = require("http");
const { findPersonalclawBin } = require("./find-bin");
const { attachContextMenu } = require("./context-menu");

/**
 * Resolve the user's real login-shell PATH.
 *
 * macOS launches a Finder/Dock .app via launchd with a minimal PATH
 * (/usr/bin:/bin:/usr/sbin:/sbin) — NOT the PATH from the user's shell rc.
 * So tools installed by node managers, homebrew, etc. are
 * invisible to the spawned backend, and provider CLIs (claude, node, npx)
 * can't be resolved. Run the login shell once, non-interactively
 * enough to source the user's profile, and read back its PATH. Cached for the
 * process lifetime. Falls back to the inherited PATH on any failure.
 */
let _loginPathCache;
function resolveLoginPath() {
  if (_loginPathCache !== undefined) return _loginPathCache;
  const inherited = process.env.PATH || "/usr/bin:/bin:/usr/sbin:/sbin";
  // Already rich (e.g. launched from a terminal) — don't pay the shell cost.
  if (inherited.includes("/.nvm/") || inherited.includes("/homebrew/") || inherited.split(":").length > 6) {
    _loginPathCache = inherited;
    return inherited;
  }
  try {
    const shellBin = process.env.SHELL || "/bin/zsh";
    // -i -l -c so both interactive (.zshrc) and login (.zprofile) rc files run,
    // matching what the user's terminal sees. Marker-delimited so we ignore any
    // banner noise the profile prints.
    const out = execFileSync(shellBin, ["-ilc", "printf '__PCPATH__%s__PCPATH__' \"$PATH\""], {
      encoding: "utf8",
      timeout: 5000,
      stdio: ["ignore", "pipe", "ignore"],
    });
    const m = out.match(/__PCPATH__(.*?)__PCPATH__/s);
    const resolved = m && m[1].trim();
    _loginPathCache = resolved && resolved.includes("/") ? resolved : inherited;
  } catch (e) {
    console.warn("login-shell PATH resolve failed:", e.message);
    _loginPathCache = inherited;
  }
  return _loginPathCache;
}

const POLL_INTERVAL_MS = 500;
const MAX_WAIT_MS = 120_000; // 2 min max wait for backend
const PERSONALCLAW_HOME = process.env.PERSONALCLAW_HOME || path.join(os.homedir(), ".personalclaw");
const TAB_BAR_HEIGHT = 28; // macOS native tab bar height in px

// Set app name for macOS menu bar and dock
app.name = "PersonalClaw";

let mainWindow = null;
let tray = null;
let gatewayProcess = null;
let isQuitting = false;
let backendUrl = null; // resolved from the gateway's READY line once bound

// ── Backend lifecycle ──

function sendStatus(msg) {
  mainWindow?.webContents?.send("status", msg);
}

/**
 * Spawn the bundled gateway on an OS-assigned ephemeral port and resolve once
 * it prints its `PERSONALCLAW_READY:{...}` line. The gateway is a private child
 * process bound to loopback, so auth is disabled via PERSONALCLAW_DEV_NO_AUTH
 * and the dashboard loads without a token.
 */
function startGateway() {
  return new Promise((resolve, reject) => {
    try {
      fs.mkdirSync(PERSONALCLAW_HOME, { recursive: true, mode: 0o700 });
    } catch (err) {
      console.warn("Failed to create personalclaw dir:", err.message);
    }

    const bin = findPersonalclawBin(fs, os, path, process.resourcesPath, __dirname);
    const args = ["gateway", "--port", "auto", "--json-ready", "--no-open"];
    sendStatus("Starting gateway…");
    console.log(`Starting gateway: ${bin} ${args.join(" ")}`);

    // Drop any inherited PERSONALCLAW_PORT so the gateway honors `--port auto`.
    const { PERSONALCLAW_PORT: _ignored, ...baseEnv } = process.env;
    gatewayProcess = spawn(
      bin,
      args,
      {
        stdio: ["ignore", "pipe", "pipe"],
        detached: false,
        env: {
          ...baseEnv,
          // Restore the user's real login-shell PATH so the backend can resolve
          // provider CLIs (claude, node, npx) that live outside the
          // minimal PATH a Finder-launched .app inherits from launchd.
          PATH: resolveLoginPath(),
          PERSONALCLAW_DEV_NO_AUTH: "1",
          PERSONALCLAW_PROJECT_DIR: path.resolve(__dirname, ".."),
        },
      }
    );

    let settled = false;
    let stdoutBuf = "";
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        reject(new Error("Gateway start timed out"));
      }
    }, MAX_WAIT_MS);

    gatewayProcess.stdout.on("data", (chunk) => {
      stdoutBuf += chunk.toString();
      let nl;
      while ((nl = stdoutBuf.indexOf("\n")) !== -1) {
        const line = stdoutBuf.slice(0, nl);
        stdoutBuf = stdoutBuf.slice(nl + 1);
        const m = line.match(/^PERSONALCLAW_READY:(.*)$/);
        if (m && !settled) {
          try {
            const payload = JSON.parse(m[1]);
            backendUrl = `http://localhost:${payload.port}`;
            settled = true;
            clearTimeout(timer);
            sendStatus("Connected ✓");
            resolve(backendUrl);
          } catch {
            // Keep scanning later lines for a valid READY payload.
          }
        }
      }
    });
    gatewayProcess.stderr.on("data", (c) => console.error("gateway:", c.toString().trim()));
    gatewayProcess.on("error", (err) => {
      console.error("Failed to start gateway:", err.message);
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(err);
      }
    });
    gatewayProcess.on("exit", (code) => {
      console.log(`Gateway exited with code ${code}`);
      gatewayProcess = null;
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(new Error(`Gateway exited with code ${code}`));
      }
    });
  });
}

function stopGateway() {
  if (gatewayProcess) {
    console.log("Stopping gateway...");
    gatewayProcess.kill("SIGTERM");
    gatewayProcess = null;
  }
}

function checkBackend(healthUrl) {
  return new Promise((resolve, reject) => {
    const req = http.get(healthUrl, { timeout: 2000 }, (res) => {
      res.resume();
      res.statusCode < 500 ? resolve() : reject();
    });
    req.on("error", reject);
    req.on("timeout", () => { req.destroy(); reject(); });
  });
}

function waitForBackend(targetWin) {
  const healthUrl = `${backendUrl}/api/status`;
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const poll = () => {
      if (targetWin?.isDestroyed()) return reject(new Error("Window closed"));
      if (Date.now() - start > MAX_WAIT_MS) return reject(new Error("Backend timeout"));
      checkBackend(healthUrl).then(resolve).catch(() => setTimeout(poll, POLL_INTERVAL_MS));
    };
    poll();
  });
}

// ── Theme-aware modal styles ──

/** Read CSS custom properties from the active PersonalClaw dashboard. */
async function getDashboardThemeVars() {
  const win = BaseWindow.getFocusedWindow() || mainWindow;
  if (!win || win.isDestroyed()) return null;
  try {
    return await win.webContents.executeJavaScript(`
      (() => {
        const s = getComputedStyle(document.documentElement);
        return {
          bg: s.getPropertyValue('--bg').trim(),
          card: s.getPropertyValue('--card').trim(),
          text: s.getPropertyValue('--text').trim(),
          muted: s.getPropertyValue('--muted').trim(),
          border: s.getPropertyValue('--border').trim(),
          accent: s.getPropertyValue('--accent').trim(),
          accentHover: s.getPropertyValue('--accent-hover').trim(),
          bgAccent: s.getPropertyValue('--bg-accent').trim(),
        };
      })()
    `);
  } catch {}
  return null;
}

function modalCSSForMode(dark) {
  return `* { margin:0; padding:0; box-sizing:border-box; }
    body { font-family:-apple-system,sans-serif; padding:24px; background:${dark ? "#1e293b" : "#f8fafc"}; color:${dark ? "#e2e8f0" : "#1e293b"}; }
    label { display:block; margin-bottom:8px; font-size:13px; color:${dark ? "#94a3b8" : "#64748b"}; }
    input { width:100%; padding:10px; border-radius:6px; border:1px solid ${dark ? "#475569" : "#cbd5e1"};
      background:${dark ? "#0f172a" : "#ffffff"}; color:${dark ? "#e2e8f0" : "#1e293b"}; font-size:14px; outline:none; margin-bottom:12px; }
    input:focus { border-color:#f97316; }
    .row { display:flex; gap:8px; }
    button { flex:1; padding:8px; border-radius:6px; border:none; cursor:pointer; font-size:13px; font-weight:600; }
    .ok { background:#f97316; color:#fff; } .ok:hover { background:#ea580c; }
    .cancel { background:${dark ? "#334155" : "#e2e8f0"}; color:${dark ? "#94a3b8" : "#475569"}; } .cancel:hover { background:${dark ? "#475569" : "#cbd5e1"}; }`;
}

function modalCSSFromVars(v) {
  return `* { margin:0; padding:0; box-sizing:border-box; }
    body { font-family:-apple-system,sans-serif; padding:24px; background:${v.bg}; color:${v.text}; }
    label { display:block; margin-bottom:8px; font-size:13px; color:${v.muted}; }
    input { width:100%; padding:10px; border-radius:6px; border:1px solid ${v.border};
      background:${v.card}; color:${v.text}; font-size:14px; outline:none; margin-bottom:12px; }
    input:focus { border-color:${v.accent}; }
    .row { display:flex; gap:8px; }
    button { flex:1; padding:8px; border-radius:6px; border:none; cursor:pointer; font-size:13px; font-weight:600; }
    .ok { background:${v.accent}; color:#fff; } .ok:hover { background:${v.accentHover || v.accent}; }
    .cancel { background:${v.bgAccent || v.card}; color:${v.muted}; } .cancel:hover { background:${v.border}; }`;
}

// ── Window ──

function syncNativeTheme(view, win) {
  if (win.isDestroyed()) return;
  view.webContents.executeJavaScript(
    `document.documentElement.dataset.mode || ""`
  ).then(mode => {
    if (mode === "dark" || mode === "light") nativeTheme.themeSource = mode;
  }).catch(() => {});
}

function setupWindowContents(win) {
  let customName = null;

  // Create a WebContentsView positioned below the tab bar
  const view = new WebContentsView({
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  view.setBackgroundColor("#00000000");
  win.contentView.addChildView(view);

  // Drag region in the tab bar padding area (makes it draggable)
  const dragView = new WebContentsView();
  dragView.setBackgroundColor("#00000000");
  dragView.webContents.loadURL("about:blank");
  dragView.webContents.on("did-finish-load", () => {
    dragView.webContents.insertCSS("html { -webkit-app-region: drag; height: 100%; }");
  });
  win.contentView.addChildView(dragView);

  win.on("closed", () => {
    view.webContents.close();
    dragView.webContents.close();
  });

  // Position the content view below the tab bar area
  function updateViewBounds() {
    if (win.isDestroyed()) return;
    const { width, height } = win.getContentBounds();
    const offset = win.isFullScreen() ? 0 : TAB_BAR_HEIGHT;
    dragView.setBounds({ x: 0, y: 0, width, height: offset });
    view.setBounds({ x: 0, y: offset, width, height: height - offset });
  }
  updateViewBounds();
  win.on("resize", updateViewBounds);
  win.on("enter-full-screen", updateViewBounds);
  win.on("leave-full-screen", updateViewBounds);

  win.webContents = view.webContents;

  function applyTitle() {
    win.setTitle(customName ? `PersonalClaw ${customName}` : "PersonalClaw");
  }

  win._pcSetCustomName = (name) => { customName = name; applyTitle(); };
  attachContextMenu(view.webContents);

  win.on("system-context-menu", (e, point) => {
    e.preventDefault();
    Menu.buildFromTemplate([
      { label: "Rename Tab…", click: () => renameCurrentTab() },
      { type: "separator" },
      { label: "New Tab", click: () => openNewTab() },
      { label: "Merge All Windows", click: () => mergeAllWindows() },
    ]).popup({ window: win, x: point.x, y: point.y });
  });

  view.webContents.on("did-finish-load", applyTitle);
  view.webContents.on("page-title-updated", (e) => { e.preventDefault(); applyTitle(); });

  view.webContents.on("did-finish-load", () => {
    view.webContents.insertCSS(`
      #electron-drag-bar {
        position: fixed;
        top: 0; left: 0; right: 0;
        height: 52px;
        -webkit-app-region: drag;
        z-index: 99999;
        pointer-events: none;
      }
      a, button, input, select, textarea,
      [role="button"], [tabindex] {
        -webkit-app-region: no-drag;
      }
    `);
    view.webContents.executeJavaScript(`
      if (!document.getElementById('electron-drag-bar')) {
        const bar = document.createElement('div');
        bar.id = 'electron-drag-bar';
        document.body.prepend(bar);
      }
    `);
    view.webContents.executeJavaScript(
      `getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()`
    ).then(bg => { if (bg && !win.isDestroyed()) win.setBackgroundColor(bg); }).catch(() => {});
    syncNativeTheme(view, win);
  });

  // Sync native tab bar to dashboard dark/light mode on focus (process-global setting)
  win.on("focus", () => syncNativeTheme(view, win));

  view.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const u = new URL(url);
      if (backendUrl && u.origin === new URL(backendUrl).origin) {
        return { action: 'allow' };
      }
      if (u.protocol === 'http:' || u.protocol === 'https:') {
        shell.openExternal(url);
      }
    } catch {}
    return { action: 'deny' };
  });

  view.webContents.session.webRequest.onBeforeSendHeaders((details, callback) => {
    delete details.requestHeaders["Referer"];
    callback({ requestHeaders: details.requestHeaders });
  });
}

function makeWindow() {
  return new BaseWindow({
    width: 1280,
    height: 860,
    minWidth: 550,
    minHeight: 600,
    tabbingIdentifier: "personalclaw",
    titleBarStyle: "hidden",
    backgroundColor: "#0f1117",
  });
}

function createWindow() {
  mainWindow = makeWindow();
  setupWindowContents(mainWindow);

  mainWindow.on("close", (e) => {
    if (!isQuitting) {
      e.preventDefault();
      mainWindow.hide();
    }
  });

  return mainWindow;
}

function createTray() {
  const iconPath = path.join(__dirname, "icon.png");
  const icon = nativeImage.createFromPath(iconPath).resize({ width: 18, height: 18 });
  tray = new Tray(icon);
  tray.setToolTip("PersonalClaw");
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: "Show PersonalClaw", click: () => mainWindow?.show() },
      { type: "separator" },
      { label: "New Tab", click: () => openNewTab() },
      { label: "Merge All Windows", click: () => mergeAllWindows() },
      { type: "separator" },
      { label: "Quit", click: () => { isQuitting = true; app.quit(); } },
    ])
  );
  tray.on("click", () => mainWindow?.show());
}

// ── Loading screen ──

async function showLoadingThenConnect(win) {
  const wc = win.webContents;
  wc.loadFile(path.join(__dirname, "loading.html"));
  win.show();

  try {
    await waitForBackend(win);
    if (win.isDestroyed()) return;
    wc.loadURL(backendUrl);
  } catch {
    if (win.isDestroyed()) return;
    const { response } = await dialog.showMessageBox(win, {
      type: "error",
      title: "PersonalClaw",
      message: "Could not connect to the PersonalClaw backend.",
      detail: "The gateway failed to start. Try reopening the app.",
      buttons: ["Retry", "Quit"],
    });
    if (response === 0) return showLoadingThenConnect(win);
    if (win === mainWindow) {
      isQuitting = true;
      app.quit();
    } else {
      win.destroy();
    }
  }
}

// ── New Tab — opens another view onto the running gateway ──

async function openNewTab() {
  if (!mainWindow || mainWindow.isDestroyed() || !backendUrl) return;
  mainWindow.show();

  const tabWin = makeWindow();
  setupWindowContents(tabWin);
  mainWindow.addTabbedWindow(tabWin);

  const wc = tabWin.webContents;
  wc.loadFile(path.join(__dirname, "loading.html"));
  try {
    await waitForBackend(tabWin);
    if (!tabWin.isDestroyed()) wc.loadURL(backendUrl);
  } catch {
    if (!tabWin.isDestroyed()) tabWin.destroy();
  }
}

// ── Rename Tab ──

function renameCurrentTab() {
  const focused = BaseWindow.getFocusedWindow();
  if (!focused || !focused._pcSetCustomName) return;

  const currentTitle = focused.getTitle();
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  getDashboardThemeVars().then((vars) => {
    const css = vars && vars.bg ? modalCSSFromVars(vars) : modalCSSForMode(nativeTheme.shouldUseDarkColors);
    const promptWin = new BrowserWindow({
      width: 400, height: 180, resizable: false, useContentSize: true,
      parent: focused, modal: true, backgroundColor: "#00000000",
      webPreferences: { nodeIntegration: false, contextIsolation: true },
    });
    const html = `<!DOCTYPE html><html><head><style>
      ${css}
    </style></head><body>
      <label>Tab name</label>
      <input id="n" value="${esc(currentTitle.replace(/^PersonalClaw /g, ''))}" autofocus>
      <div class="row"><button class="ok" onclick="go()">Rename</button>
      <button class="cancel" onclick="window.close()">Cancel</button></div>
      <script>
        function go() { document.title = document.getElementById('n').value.trim(); window.close(); }
        document.addEventListener('keydown', e => { if(e.key==='Enter') go(); if(e.key==='Escape') window.close(); });
      </script>
    </body></html>`;
    promptWin.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
    promptWin.setMenu(null);

    let savedTitle = null;
    promptWin.on("page-title-updated", (_e, title) => { savedTitle = title; });
    promptWin.on("closed", () => {
      if (savedTitle && focused && !focused.isDestroyed()) {
        focused._pcSetCustomName(savedTitle);
      }
    });
  });
}

// ── Merge Windows ──

function mergeAllWindows() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.show();

  const others = BaseWindow.getAllWindows().filter(
    (w) => w !== mainWindow && !w.isDestroyed() && w._pcSetCustomName
  );
  for (const win of others) {
    mainWindow.addTabbedWindow(win);
  }
  setTimeout(() => {
    if (!mainWindow.isDestroyed()) {
      mainWindow.setHasShadow(false);
      mainWindow.setHasShadow(true);
    }
  }, 50);
}

// ── App lifecycle ──

// Single-instance: a second launch focuses the existing window instead of
// spawning a second gateway.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (!mainWindow.isVisible()) mainWindow.show();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    const appMenu = Menu.buildFromTemplate([
      { role: "appMenu" },
      { role: "editMenu" },
      {
        label: "Tab",
        submenu: [
          { label: "New Tab", accelerator: "CmdOrCtrl+T", click: () => openNewTab() },
          { label: "Rename Tab…", accelerator: "CmdOrCtrl+Shift+R", click: () => renameCurrentTab() },
          { type: "separator" },
          { label: "Merge All Windows", click: () => mergeAllWindows() },
        ],
      },
      { role: "windowMenu" },
    ]);
    Menu.setApplicationMenu(appMenu);

    createTray();
    const win = createWindow();

    try {
      await startGateway();
    } catch (err) {
      console.error("Gateway did not start:", err.message);
    }
    await showLoadingThenConnect(win);

    app.on("activate", () => {
      if (!mainWindow?.isVisible()) mainWindow?.show();
    });

    app.on("new-window-for-tab", () => {
      openNewTab();
    });
  });
}

app.on("before-quit", () => {
  isQuitting = true;
  stopGateway();
});

app.on("window-all-closed", () => {
  // macOS: keep running in tray
  if (process.platform !== "darwin") app.quit();
});
