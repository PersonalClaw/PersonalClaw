const { app, BaseWindow, BrowserWindow, WebContentsView, shell, dialog, Tray, Menu, nativeImage, nativeTheme, ipcMain, systemPreferences, Notification, globalShortcut } = require("electron");
const fs = require("fs");
const os = require("os");
const { spawn, execFileSync } = require("child_process");
const path = require("path");
const http = require("http");
const { findPersonalclawBin } = require("./find-bin");
const { attachContextMenu } = require("./context-menu");
const { IPC_CHANNELS, makeCapabilities, registerCapabilityIpc } = require("./capabilities");
const { makePushToTalk, registerPushToTalkIpc } = require("./pushToTalk");
const {
  DEEP_LINKS,
  summarizePresence,
  makeTrayPresence,
  shouldHideOnClose,
  shouldQuitOnAllWindowsClosed,
} = require("./trayPresence");
const { makeLoginItem, registerLoginItemIpc } = require("./loginItem");
const {
  makeNativeNotifications,
  registerNativeNotificationIpc,
} = require("./nativeNotifications");
const { shutdownGateway } = require("./gatewayShutdown");

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
/** How often the menu bar refreshes its approvals/loops counts (DC-4 T4.1). */
const PRESENCE_REFRESH_MS = 5000;
/** How long quit waits for the gateway to stop before escalating (DC-4 T4.3). */
const GATEWAY_GRACE_MS = 8000;

// Set app name for macOS menu bar and dock
app.name = "PersonalClaw";

let mainWindow = null;
let gatewayProcess = null;
let isQuitting = false;
let presenceTimer = null;
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
        // Its OWN process group (Node's `detached` calls setsid), so quit can signal
        // the GROUP and take the gateway's subprocesses — ACP CLIs, MCP servers,
        // terminal sessions — with it. Measured under the previous `detached: false`:
        // the gateway pid reaped cleanly while a child of its own survived with
        // `ppid=1`, and its pgid was THIS app's group, which made a group-kill
        // unavailable rather than merely unused. Deliberately NOT `unref()`ed: we
        // still track the handle, wait for its `exit`, and reap it in `before-quit`.
        detached: true,
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

/**
 * Stop the gateway and WAIT for it (DC-4 T4.3).
 *
 * The shell spawned this process, so quit is the one moment its data can be torn in
 * half. This used to be `kill("SIGTERM")` followed immediately by dropping the
 * handle inside a synchronous `before-quit` — which never learned whether the child
 * exited, and left an orphan gateway holding the port whenever SIGTERM was slow to
 * land. The waiting and the SIGKILL escalation live in `gatewayShutdown.js`; the
 * outcome is logged so a quit that MIGHT have orphaned something says so.
 *
 * The second half is `killGroup`. Waiting for the gateway pid was never enough: the
 * gateway spawns its own subprocesses, and signalling one pid leaves them running
 * (measured — a child survived with `ppid=1`). The spawn now gives the gateway its own
 * process group so this signal can reach the whole tree.
 */
async function stopGateway() {
  const child = gatewayProcess;
  if (!child) return { outcome: "none" };
  gatewayProcess = null;
  const result = await shutdownGateway({
    child,
    graceMs: GATEWAY_GRACE_MS,
    // We spawned it `detached: true`, so it leads its own group and the signal can
    // reach the subprocesses IT started. `shutdownGateway` re-verifies that against
    // the OS and degrades to a single-pid signal if this ever stops being true.
    killGroup: true,
    log: (msg) => console.log(`gateway shutdown: ${msg}`),
  });
  console.log(
    `Gateway shutdown outcome: ${result.outcome}` +
      (result.groupSwept ? " (residual process-group members were killed)" : "")
  );
  return result;
}

// ── Capability bridge ↔ gateway registration (DC-2) ──

/**
 * The per-session `shell_token` the gateway mints for us.
 *
 * It lives in MAIN-process module scope and nowhere else: never written to disk,
 * never logged, never handed to a renderer. `preload.js` exposes probe/request/on
 * and no token accessor, so page JS has no path to this value even if a page is
 * compromised. It dies with the process, and re-registering rotates it server-side
 * so a stale shell cannot keep writing capability state.
 */
let shellToken = null;

const capabilities = makeCapabilities({
  platform: process.platform,
  systemPreferences,
  notification: Notification,
  // A grant changes two consumers at once: the renderer (so a panel re-renders
  // without polling) and the gateway (so a browser tab and any app with a
  // `desktop` permission see the same truth).
  onChange: (cap, state) => {
    try {
      mainWindow?.webContents?.send(IPC_CHANNELS.state, { capability: cap, state });
    } catch {
      /* window may be gone mid-grant */
    }
    pushCapabilityState();
  },
});

/**
 * Push-to-talk (DC-3 T3.1). The shell owns the chord; the RENDERER owns the
 * microphone. `onCapturing` is therefore driven by the renderer's report of its live
 * stream, never by "we forwarded a press" — see the module header for why that
 * direction is the one that keeps the indicator honest.
 */
const pushToTalk = makePushToTalk({
  globalShortcut,
  send: (payload) => {
    try {
      mainWindow?.webContents?.send(IPC_CHANNELS.pushToTalk, payload);
    } catch {
      /* window may be gone mid-press */
    }
  },
  onCapturing: (on) => setCaptureIndicator(on),
});

/**
 * Native OS notifications (DC-5 T4.2) — plan-42's `native` delivery target, actuated.
 *
 * The gateway decides (a rule naming `native` + this shell reporting the capability
 * available); the renderer relays, because it holds the WS the main process does not; this
 * raises the banner. Note the tap does NOT go through `deepLink()`: the route came from the
 * renderer in the first place, so it navigates itself and the main process never has to
 * interpret a route string it did not author. Focusing the window is the half only the main
 * process can do, and it happens whether or not a route was named.
 */
const nativeNotifications = makeNativeNotifications({
  Notification,
  focusWindow: () => {
    if (!mainWindow || mainWindow.isDestroyed()) return false;
    showMainWindow();
    return true;
  },
  sendToRenderer: (payload) => {
    try {
      mainWindow?.webContents?.send(IPC_CHANNELS.notificationActivate, payload);
    } catch {
      /* window may be gone between the tap and the send */
    }
  },
  log: (msg) => console.warn(msg),
});

/**
 * The always-on capturing indicator (DC-3 T3.1).
 *
 * It lives in the MENU BAR, not in the page, because the chord is global: press it
 * while the window is hidden behind a full-screen editor and an in-app chip would be
 * a capture indicator nobody can see. macOS draws its own orange mic dot too, and that
 * one is the trustworthy signal precisely because the app cannot suppress it — this is
 * an addition to it, saying WHICH app is listening, never a substitute for it.
 *
 * `title` (text beside the icon) rather than only a tooltip: a tooltip requires a
 * hover to discover, and "you have to go looking for it" disqualifies an indicator
 * whose whole job is to be noticed without being sought.
 *
 * Since DC-4 the title has exactly ONE writer (`composeTrayTitle` in
 * `trayPresence.js`), because the approvals badge wants the same pixels. Capture wins
 * there: an approvals count can wait a second, a live-microphone indicator cannot.
 *
 * ORDERING: `trayPresence` is declared further down this file, so this function must
 * not be called during module evaluation. It cannot be — `onCapturing` fires only from
 * `setCapturing`/`clearCapturing`, which run on Electron events, and every one of
 * those is after the module finished loading. Keep it that way: a synchronous caller
 * added above the `trayPresence` declaration would be a temporal-dead-zone crash at
 * startup, not a lint error.
 */
function setCaptureIndicator(on) {
  trayPresence.setCapturing(on);
}

/** Read the gateway's per-session local secret. Same-user filesystem access is the
 * claim being proved: "I am a process running as this user on this machine". */
function readLocalSecret() {
  try {
    return fs.readFileSync(path.join(PERSONALCLAW_HOME, ".local_secret"), "utf8").trim();
  } catch {
    return "";
  }
}

/** POST JSON to the loopback gateway. Resolves the parsed body, or null on any
 * failure — capability registration must never be able to break app startup. */
function postGateway(pathname, body, headers = {}) {
  return new Promise((resolve) => {
    if (!backendUrl) return resolve(null);
    const payload = Buffer.from(JSON.stringify(body));
    let url;
    try {
      url = new URL(pathname, backendUrl);
    } catch {
      return resolve(null);
    }
    const req = http.request(
      {
        hostname: url.hostname,
        port: url.port,
        path: url.pathname,
        method: "POST",
        timeout: 5000,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": payload.length,
          ...headers,
        },
      },
      (res) => {
        let buf = "";
        res.on("data", (c) => (buf += c));
        res.on("end", () => {
          if (res.statusCode !== 200) {
            // Log the STATUS only. An error body could quote a credential.
            console.warn(`desktop ${pathname} → HTTP ${res.statusCode}`);
            return resolve(null);
          }
          try {
            resolve(JSON.parse(buf));
          } catch {
            resolve(null);
          }
        });
      }
    );
    req.on("error", (err) => {
      console.warn(`desktop ${pathname} failed: ${err.message}`);
      resolve(null);
    });
    req.on("timeout", () => {
      req.destroy();
      resolve(null);
    });
    req.write(payload);
    req.end();
  });
}

/** Announce the shell to the gateway and remember the token it mints. */
async function registerWithGateway() {
  const secret = readLocalSecret();
  if (!secret) {
    console.warn("desktop: no local secret; capabilities stay unregistered");
    return false;
  }
  const res = await postGateway(
    "/api/desktop/register",
    {
      shell: { version: app.getVersion(), platform: process.platform },
      capabilities: capabilities.snapshot(),
    },
    { "X-Local-Secret": secret }
  );
  if (!res || !res.shell_token) return false;
  shellToken = res.shell_token;
  console.log("desktop: capability manifest registered with the gateway");
  return true;
}

/** Push a refreshed manifest after a grant/deny. No token → no push (fail closed). */
async function pushCapabilityState() {
  if (!shellToken) return;
  await postGateway(
    "/api/desktop/state",
    { capabilities: capabilities.snapshot() },
    { "X-Shell-Token": shellToken }
  );
}

/** Tell the gateway the shell is going away, so a still-open tab stops claiming
 * the desktop can do anything. Best-effort: quit does not wait on it. */
function unregisterFromGateway() {
  if (!shellToken) return;
  const token = shellToken;
  shellToken = null;
  postGateway("/api/desktop/unregister", {}, { "X-Shell-Token": token });
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

  // Hiding on close is only safe while the menu bar can bring the window back, so
  // the decision reads the tray's real availability rather than assuming macOS has
  // one (DC-4). With no tray this closes for real.
  mainWindow.on("close", (e) => {
    if (shouldHideOnClose({ trayAvailable: trayPresence.available, isQuitting })) {
      e.preventDefault();
      mainWindow.hide();
    }
  });

  return mainWindow;
}

// ── Menu-bar presence (DC-4 T4.1) ──

/** Bring the window forward, creating nothing: the tray is presence, not a spawner. */
function showMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (!mainWindow.isVisible()) mainWindow.show();
  mainWindow.focus();
}

/**
 * Deep-link the dashboard SPA from the menu bar.
 *
 * The dashboard is hash-routed, so a link is a hash assignment rather than a reload —
 * reloading would throw away the live WS connection and any in-flight chat turn just
 * to change route. `mainWindow.webContents` is the ACTIVE tab's contents (assigned in
 * `setupWindowContents`), so this follows the tab the user is actually looking at.
 *
 * The hash comes from `DEEP_LINKS`, a closed map, and any interpolated id is
 * `encodeURIComponent`-ed there before `JSON.stringify` quotes it here — a loop name
 * from the gateway never reaches the page as code.
 */
function deepLink(hash) {
  showMainWindow();
  const wc = mainWindow?.webContents;
  if (!wc || wc.isDestroyed?.()) return;
  try {
    wc.executeJavaScript(`window.location.hash = ${JSON.stringify(String(hash))}`);
  } catch (err) {
    console.warn(`deep link to ${hash} failed: ${err.message}`);
  }
}

/**
 * "Open at login" (DC-4 T4.3). Opt-in, reversible, idempotent — see `loginItem.js`
 * for exactly what it registers with the OS. Nothing enables it implicitly: the only
 * callers are the tray checkbox and the Settings bridge, both user actions.
 */
const loginItem = makeLoginItem({
  app,
  log: (msg) => console.warn(`login item: ${msg}`),
});

/**
 * The ONE place the tray's checkbox learns what the OS did.
 *
 * Two surfaces can write this registration — the tray checkbox and Settings over the
 * bridge — and the tray renders from a cached state, so every writer must land here
 * or the two disagree. It re-READS rather than taking the requested value: the OS is
 * the authority, and a refused write must leave the checkbox showing what is
 * actually registered.
 */
function syncLoginItemToTray() {
  trayPresence.setLoginItemState({
    supported: loginItem.supported,
    enabled: loginItem.isEnabled(),
  });
}

/**
 * The menu-bar item. Electron's pieces are injected so the menu, the title
 * arbitration and the degradation paths are unit-testable without launching an app
 * (`test/trayPresence.test.js`).
 *
 * `trayPresence.available` is load-bearing beyond cosmetics: window-close hides the
 * window instead of closing it, which is only safe while a menu-bar item exists to
 * bring it back. A tray that fails to build therefore changes the close behavior
 * rather than leaving a phantom hidden window.
 */
const trayPresence = makeTrayPresence({
  TrayCtor: Tray,
  MenuCtor: Menu,
  nativeImageMod: nativeImage,
  iconPath: path.join(__dirname, "icon.png"),
  log: (msg) => console.warn(`tray: ${msg}`),
  actions: {
    open: () => showMainWindow(),
    deepLink: (hash) => deepLink(hash),
    // Quick capture routes to the Inbox with a capture intent. The note-writing half
    // is NOT ours to invent: no endpoint creates an inbox item from text (every
    // /api/inbox POST acts on an existing item), and the attention-path contracts
    // belong to INBOX/Notifications-Unification. Minting POST /api/inbox/capture here
    // would be a consumer defining its owner's contract.
    quickCapture: () => deepLink(`${DEEP_LINKS.inbox}?capture=1`),
    toggleLoginItem: (next) => {
      const result = loginItem.set(next);
      if (!result.ok && result.reason) console.warn(`login item unchanged: ${result.reason}`);
      syncLoginItemToTray();
    },
    quit: () => {
      isQuitting = true;
      app.quit();
    },
  },
});

/** GET JSON from the loopback gateway. Resolves null on any failure — a menu-bar
 * refresh must never be able to throw into the app. */
function getGateway(pathname) {
  return new Promise((resolve) => {
    if (!backendUrl) return resolve(null);
    let url;
    try {
      url = new URL(pathname, backendUrl);
    } catch {
      return resolve(null);
    }
    const req = http.request(
      {
        hostname: url.hostname,
        port: url.port,
        path: `${url.pathname}${url.search}`,
        method: "GET",
        timeout: 3000,
      },
      (res) => {
        let buf = "";
        res.on("data", (c) => (buf += c));
        res.on("end", () => {
          if (res.statusCode !== 200) return resolve(null);
          try {
            resolve(JSON.parse(buf));
          } catch {
            resolve(null);
          }
        });
      }
    );
    req.on("error", () => resolve(null));
    req.on("timeout", () => {
      req.destroy();
      resolve(null);
    });
    req.end();
  });
}

/**
 * Refresh the live counts. `GET /api/approvals` is a bare array; `GET /api/loops` is
 * `{loops: [...]}` — both shapes are folded by `summarizePresence`, which renders a
 * failed poll as "not connected" rather than as a zero that looks like good news.
 */
async function refreshPresence() {
  if (!trayPresence.available) return;
  const [approvals, loops] = await Promise.all([getGateway("/api/approvals"), getGateway("/api/loops")]);
  trayPresence.setPresence(summarizePresence(approvals, loops));
}

/** Start the presence poll. Polling the loopback API (not the WS) is deliberate: the
 * menu bar needs a low-frequency count, and a poll cannot leave a half-open socket
 * behind on quit. */
function startPresenceRefresh() {
  if (!trayPresence.available || presenceTimer) return;
  refreshPresence();
  presenceTimer = setInterval(refreshPresence, PRESENCE_REFRESH_MS);
}

function stopPresenceRefresh() {
  if (presenceTimer) {
    clearInterval(presenceTimer);
    presenceTimer = null;
  }
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

    // The capability bridge's main-process half. Registered before any window
    // loads so a renderer's first `pclawDesktop.capabilities.probe()` always has
    // a handler waiting.
    registerCapabilityIpc(ipcMain, capabilities);
    // Push-to-talk's handlers, on their own channels. The chord itself is bound by
    // the RENDERER (it is the process that reads `voice.push_to_talk_chord` from the
    // gateway), so the shell never has to parse config to know what to listen for.
    registerPushToTalkIpc(ipcMain, pushToTalk, IPC_CHANNELS);
    // The login item's bridge half (DC-4), so Settings can drive the same toggle the
    // tray checkbox drives. Its own channels, not the capability vocabulary's. The
    // fourth argument is what keeps the two surfaces agreeing: a Settings flip
    // re-renders the tray checkbox instead of leaving it stale until restart.
    registerLoginItemIpc(ipcMain, loginItem, IPC_CHANNELS, syncLoginItemToTray);
    // The `native` notification target's actuator (DC-5). Registered before any window
    // loads, like the rest: the first gateway note can arrive as soon as the WS opens.
    registerNativeNotificationIpc(ipcMain, nativeNotifications, IPC_CHANNELS);

    // Menu-bar presence. A failed tray is reported, not fatal — and it changes the
    // window-close behavior below so the window can never become unreachable.
    if (!trayPresence.start()) {
      console.warn("running without menu-bar presence — the window will close on close");
    }
    syncLoginItemToTray();
    const win = createWindow();

    try {
      await startGateway();
    } catch (err) {
      console.error("Gateway did not start:", err.message);
    }
    // Needs backendUrl from the READY line, so it follows the gateway start. A
    // failure here leaves the gateway reporting "not connected" — degraded but
    // honest — and never blocks the window.
    await registerWithGateway();
    // Counts need `backendUrl`, so the poll starts after the gateway is up. With no
    // gateway the menu simply reads "not connected".
    startPresenceRefresh();
    await showLoadingThenConnect(win);

    app.on("activate", () => {
      if (!mainWindow?.isVisible()) mainWindow?.show();
    });

    app.on("new-window-for-tab", () => {
      openNewTab();
    });
  });
}

/** Set once the async shutdown has run, so the second `before-quit` lets go. */
let shutdownComplete = false;

/**
 * Graceful quit (DC-4 T4.3).
 *
 * `before-quit` is synchronous, so waiting for the gateway means taking the quit back
 * once: `preventDefault()`, run the shutdown, then `app.quit()` again — which fires
 * this handler a second time, now with `shutdownComplete` set, and the app exits for
 * real. Without the deferral Electron tears the process down while the gateway is
 * still flushing, which is the difference between "we sent SIGTERM" and "the gateway
 * stopped".
 */
app.on("before-quit", (event) => {
  isQuitting = true;
  if (shutdownComplete) return;
  event.preventDefault();

  // Release the chord and take the indicator down before the tray is torn out from
  // under it — a quit that left "● Listening" as the last thing drawn would be the
  // one moment the indicator is guaranteed to be lying.
  pushToTalk.unbind();
  pushToTalk.clearCapturing();
  stopPresenceRefresh();
  unregisterFromGateway();

  stopGateway()
    .catch((err) => console.warn(`gateway shutdown failed: ${err.message}`))
    .finally(() => {
      trayPresence.destroy();
      shutdownComplete = true;
      app.quit();
    });
});

app.on("window-all-closed", () => {
  // macOS keeps running with no windows ONLY because the menu-bar item is still there
  // to bring one back. With no tray, staying alive is the phantom state.
  if (shouldQuitOnAllWindowsClosed({ platform: process.platform, trayAvailable: trayPresence.available })) {
    app.quit();
  }
});
