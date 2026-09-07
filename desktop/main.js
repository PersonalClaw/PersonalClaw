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
const { openShellStore } = require("./shellStore");
const { loadRegistry } = require("./endpointRegistry");
const {
  LOCAL_ENDPOINT_ID,
  HEALTH_REACHABLE,
  adoptGatewayLabel,
  assertLoopbackTarget,
  shouldAttachBridge,
  describeStartup,
  prepareEndpoint,
  currentFingerprintFor,
  confirmEndpoint,
  rememberLocalGateway,
  forgetEndpoint,
  switchTo,
  probeEndpoint,
  probeAll,
  nextReconnectStep,
} = require("./connectMode");
const { describeList, makeConnectDialog } = require("./connectDialog");

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

/**
 * The gateway THIS shell spawned, resolved from its READY line once bound. Always loopback.
 *
 * 🔒 EVERY CREDENTIAL-BEARING CALL TARGETS THIS URL AND NOTHING ELSE. `.local_secret` and the
 * capability `shell_token` prove "I am a process running as this user on this machine", a claim
 * that is false for any other gateway by construction, so `postGateway`/`getGateway` are bound
 * here and re-assert it with `assertLoopbackTarget` before every send. Connect-mode (CA-8) added
 * a SECOND url to this file; keeping the two apart by name is what stops the day someone points a
 * registration call at the one the user typed.
 */
let localGatewayUrl = null;

/**
 * What the WebView actually loads (CA-8). Defaults to `localGatewayUrl` — spawn-local is still
 * the default connection model and nothing here changes it — and becomes a paired gateway's
 * origin only when `describeStartup` or the switcher can fully justify it.
 */
let activeUrl = null;

/** The shell's own storage scope (`shellStore.js`): the registry + per-endpoint state. */
let shellStore = null;
/** Last probe result per endpoint id. `{}` means "nothing probed yet", not "all unreachable". */
let endpointHealth = {};
/** The connect dialog / switcher, built in `whenReady` once Electron's classes exist. */
let connectDialog = null;
/** Reachability-probe bookkeeping for the ACTIVE endpoint. Bounded — see `scheduleReachProbe`. */
let reachAttempt = 0;
let reachTimer = null;
/** Warnings from the last startup decision, shown in the dialog's banner area. */
let startupWarnings = [];

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
            localGatewayUrl = `http://localhost:${payload.port}`;
            settled = true;
            clearTimeout(timer);
            sendStatus("Connected ✓");
            resolve(localGatewayUrl);
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
 * failure — capability registration must never be able to break app startup.
 *
 * 🔒 THE TARGET IS RE-ASSERTED, NOT ASSUMED (CA-8). Every caller carries either `.local_secret` or
 * the capability `shell_token`, both of which are claims about THIS machine. Connect-mode put a
 * second, user-supplied URL in this file, so the loopback property is checked here rather than
 * left to the reader of the variable name. A failed assertion resolves `null` — the same answer as
 * any other failure, and crucially *nothing is sent* — instead of throwing into a startup path
 * that has no handler for it. */
function postGateway(pathname, body, headers = {}) {
  return new Promise((resolve) => {
    if (!localGatewayUrl) return resolve(null);
    try {
      assertLoopbackTarget(localGatewayUrl, `POST ${pathname}`);
    } catch (err) {
      console.error(`desktop: ${err.message}`);
      return resolve(null);
    }
    const payload = Buffer.from(JSON.stringify(body));
    let url;
    try {
      url = new URL(pathname, localGatewayUrl);
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
  const healthUrl = `${localGatewayUrl}/api/status`;
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

/**
 * Wait for a PAIRED gateway to answer (CA-8). Deliberately a second function rather than a
 * generalisation of `waitForBackend`: the spawn-local readiness path is a `done_when` clause of
 * this atom ("the spawn-local path is unchanged"), so it is left byte-identical above.
 *
 * The difference that matters is not the URL, it is the refusals. `waitForBackend` retries
 * anything below a 500 because a gateway it just spawned is only ever slow. A gateway on the
 * network can *answer and refuse* — a revoked device session (401/403), a redirect, or a host that
 * is not a PersonalClaw gateway at all — and none of those becomes ready by waiting. Each rejects
 * immediately, carrying the probe so the caller can say which happened.
 */
function waitForEndpoint(targetWin, url) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    const poll = async () => {
      if (targetWin?.isDestroyed()) return reject(new Error("Window closed"));
      if (Date.now() - start > MAX_WAIT_MS) return reject(new Error("Endpoint timeout"));
      const probe = await probeEndpoint(url, { timeoutMs: 2500 });
      if (probe.status === HEALTH_REACHABLE) return resolve(probe);
      const step = nextReconnectStep({ status: probe.status, attempt: 0 });
      if (step.action !== "retry") {
        return reject(Object.assign(new Error(`endpoint ${probe.status}`), { probe }));
      }
      setTimeout(poll, POLL_INTERVAL_MS);
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

/**
 * Build (or REBUILD) a window's content views.
 *
 * 🔒 `attachBridge` IS A SECURITY DECISION, NOT A FEATURE FLAG (CA-8). `preload.js` exposes the
 * microphone chord, native notifications, the login item and the capability probes, and the
 * gateway it registers against had to prove same-machine access with `.local_secret`. A gateway on
 * the network cannot make that claim, so its origin gets a view with **no preload at all** — which
 * `web/src/lib/desktopBridge.ts:123` already handles by answering `null` and reporting every
 * capability unavailable. A preload cannot be detached from a live `WebContents`, which is why
 * this function is re-callable: switching between a loopback gateway and a paired one replaces the
 * view rather than re-pointing it.
 */
function setupWindowContents(win, { attachBridge = true } = {}) {
  let customName = win._pcCustomName || null;

  // Replace any views this function built earlier on this window (a bridge-state change).
  if (typeof win._pcTeardown === "function") {
    try {
      win._pcTeardown();
    } catch (err) {
      console.warn(`could not tear down the previous view: ${err.message}`);
    }
  }

  // Create a WebContentsView positioned below the tab bar
  const view = new WebContentsView({
    webPreferences: {
      ...(attachBridge ? { preload: path.join(__dirname, "preload.js") } : {}),
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

  win._pcBridgeAttached = attachBridge;
  win._pcTeardown = () => {
    win._pcTeardown = null;
    if (!win.isDestroyed()) {
      try {
        win.contentView.removeChildView(view);
        win.contentView.removeChildView(dragView);
      } catch {
        /* the window may already be tearing down */
      }
    }
    view.webContents.close();
    dragView.webContents.close();
  };

  // Position the content view below the tab bar area
  function updateViewBounds() {
    if (win.isDestroyed()) return;
    const { width, height } = win.getContentBounds();
    const offset = win.isFullScreen() ? 0 : TAB_BAR_HEIGHT;
    dragView.setBounds({ x: 0, y: 0, width, height: offset });
    view.setBounds({ x: 0, y: offset, width, height: height - offset });
  }
  updateViewBounds();
  // Window-level listeners are registered ONCE per window. Re-registering them on a rebuild would
  // leak a listener per gateway switch, and Electron caps them at ten before it starts warning.
  win._pcBounds = updateViewBounds;
  win._pcSyncTheme = () => syncNativeTheme(win._pcView, win);
  win._pcView = view;
  if (!win._pcWired) {
    win._pcWired = true;
    win.on("resize", () => win._pcBounds());
    win.on("enter-full-screen", () => win._pcBounds());
    win.on("leave-full-screen", () => win._pcBounds());
    win.on("focus", () => win._pcSyncTheme());
    win.on("closed", () => {
      if (typeof win._pcTeardown === "function") win._pcTeardown();
    });
    win.on("system-context-menu", (e, point) => {
      e.preventDefault();
      Menu.buildFromTemplate([
        { label: "Rename Tab…", click: () => renameCurrentTab() },
        { type: "separator" },
        { label: "New Tab", click: () => openNewTab() },
        { label: "Merge All Windows", click: () => mergeAllWindows() },
        { type: "separator" },
        { label: "Gateways…", click: () => openConnectDialog() },
      ]).popup({ window: win, x: point.x, y: point.y });
    });
  }

  win.webContents = view.webContents;

  function applyTitle() {
    win.setTitle(customName ? `PersonalClaw ${customName}` : "PersonalClaw");
  }

  win._pcSetCustomName = (name) => {
    customName = name;
    win._pcCustomName = name;
    applyTitle();
  };
  attachContextMenu(view.webContents);

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

  /** The origin this view is allowed to be, or `""` before anything is loaded. */
  const allowedOrigin = () => {
    const target = activeUrl || localGatewayUrl;
    if (!target) return "";
    try {
      return new URL(target).origin;
    } catch {
      return "";
    }
  };

  view.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const u = new URL(url);
      // Compare against the ACTIVE origin, not the spawned gateway's: in connect-mode the page
      // being rendered belongs to the paired gateway, and a window it opens on its own origin is
      // as legitimate there as it is on loopback.
      if (allowedOrigin() && u.origin === allowedOrigin()) {
        return { action: 'allow' };
      }
      if (u.protocol === 'http:' || u.protocol === 'https:') {
        shell.openExternal(url);
      }
    } catch {}
    return { action: 'deny' };
  });

  /**
   * 🔒 THE VIEW MAY NOT LEAVE THE ORIGIN THE USER CONFIRMED (CA-8).
   *
   * This is the guard that matters most once a bridge exists. Without it, a link or a script in
   * rendered content could navigate this same `WebContents` to any origin — and on the loopback
   * path that `WebContents` is carrying `preload.js`, so the microphone, hotkey and notification
   * bridge would follow it there. Same-origin navigations and the local loading document are
   * allowed; everything else is handed to the system browser, where it belongs.
   *
   * `will-navigate` covers link clicks and `location` assignments; `will-redirect` covers a server
   * 3xx, which is the shape the SSRF guidance singles out — a host that passes validation and then
   * points the client somewhere it would never have accepted.
   */
  const guardNavigation = (event, url) => {
    let u;
    try {
      u = new URL(url);
    } catch {
      event.preventDefault();
      return;
    }
    if (u.protocol === "file:" || u.protocol === "about:") return; // loading.html, about:blank
    const origin = allowedOrigin();
    if (origin && u.origin === origin) return;
    event.preventDefault();
    console.warn(`blocked in-app navigation to ${u.origin} (allowed: ${origin || "none"})`);
    if (u.protocol === "http:" || u.protocol === "https:") shell.openExternal(url);
  };
  view.webContents.on("will-navigate", guardNavigation);
  view.webContents.on("will-redirect", guardNavigation);

  /**
   * A page that never loaded is the shell's problem; a page that loaded and lost its socket is the
   * SPA's (its capped-backoff reconnect is the published contract, and a second timer racing it
   * would duplicate every catch-up fetch). So the reachability probe starts HERE and nowhere else.
   */
  view.webContents.on("did-fail-load", (_e, errorCode, errorDescription, failedUrl, isMainFrame) => {
    if (!isMainFrame) return;
    if (errorCode === -3) return; // ERR_ABORTED — a navigation we superseded ourselves
    if (!activeUrl || activeUrl === localGatewayUrl) return; // spawn-local has its own retry dialog
    console.warn(`load failed for ${failedUrl}: ${errorDescription} (${errorCode})`);
    scheduleReachProbe(win);
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
    // Quick capture routes to the Inbox with a capture intent. The URL is UNCHANGED from
    // DC-4 — it was the reader that was missing, not the contract. INU-9 supplied both
    // halves in the owning plan: `POST /api/inbox/notes` writes a `user_note` item, and
    // `InboxPage` now reads `?capture=1` (`useQueryFlag(query, setQuery, 'capture')`) to
    // open the compose surface. Keeping the existing flag rather than inventing a second
    // one is what leaves no window where the tray and the SPA disagree.
    //
    // 🔑 This shell still mints no endpoint of its own. The tray is one ENTRANCE to a
    // capability core owns; the inbox header's Capture control sets the identical flag.
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
    if (!localGatewayUrl) return resolve(null);
    let url;
    try {
      url = new URL(pathname, localGatewayUrl);
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

// ── Connect mode: the shell pointed at a gateway it did not spawn (CA-8) ──

/** Open (or focus) the connect dialog / switcher. */
function openConnectDialog() {
  if (!connectDialog) return;
  connectDialog.open(mainWindow && !mainWindow.isDestroyed() ? mainWindow : null);
}

/** Re-probe every row's health, credential-free. Never mutates the registry. */
async function refreshHealth() {
  if (!shellStore) return endpointHealth;
  endpointHealth = await probeAll(loadRegistry(shellStore), { localBaseUrl: localGatewayUrl || "" });
  return endpointHealth;
}

function cancelReachProbe() {
  if (reachTimer) {
    clearTimeout(reachTimer);
    reachTimer = null;
  }
  reachAttempt = 0;
}

/**
 * The shell's ONE reconnect job: notice that a paired gateway is not answering, say so, and load
 * the page again when it comes back.
 *
 * 🔑 THIS PROBES; IT DOES NOT RETRY A CREDENTIAL, AND IT DOES NOT RELOAD ON A TIMER. The probe is
 * `/api/healthz` with no cookie and no header (`connectMode.probeEndpoint`), so there is no
 * credential in the loop to be retried in the first place. `nextReconnectStep` decides what
 * happens next, and its three non-retry answers are the whole point:
 *
 *   - `needs_pairing` (the gateway answered 401/403 — a revoked device session) → **stop dead**.
 *     Zero further attempts. The row says "needs pairing again" and the user decides. Hammering it
 *     would also drive that gateway's own per-IP pairing lockout against its owner.
 *   - `stop` (not a gateway / redirected / refused by policy) → stop. The host answered, and the
 *     answer was not "try later".
 *   - `give_up` (attempts exhausted) → stop and wait for a human. Bounded, so a machine that is
 *     simply off does not leave a timer running all day.
 *
 * A reload happens only on a probe that came back `reachable`, which is also why this cannot race
 * the SPA: while the SPA is loaded there is nothing here to run.
 */
function scheduleReachProbe(win, delayMs = 0) {
  if (!activeUrl || activeUrl === localGatewayUrl) return;
  const target = activeUrl;
  if (reachTimer) clearTimeout(reachTimer);
  reachTimer = setTimeout(async () => {
    reachTimer = null;
    if (!win || win.isDestroyed() || activeUrl !== target) return;
    const probe = await probeEndpoint(target);
    if (activeUrl !== target) return; // the user switched while we were waiting
    const id = loadRegistry(shellStore).active;
    if (id) endpointHealth = { ...endpointHealth, [id]: probe };

    const step = nextReconnectStep({ status: probe.status, attempt: reachAttempt });
    if (step.action === "stay") {
      cancelReachProbe();
      if (!win.isDestroyed()) win.webContents.loadURL(target);
      return;
    }
    if (step.action === "retry") {
      reachAttempt = step.attempt;
      console.log(`gateway ${target} ${probe.status}; re-checking in ${step.delayMs}ms (attempt ${step.attempt})`);
      scheduleReachProbe(win, step.delayMs);
      return;
    }
    cancelReachProbe();
    console.warn(`gateway ${target} ${probe.status}: ${step.reason} — stopping automatic re-checks`);
    // A terminal outcome is a decision for the user, so it goes to the switcher rather than into a
    // retry. The dialog reads `endpointHealth`, so the row already says which of them it was.
    openConnectDialog();
  }, delayMs);
}

/**
 * Point the shell at `url` and load it.
 *
 * The bridge decision is re-taken here rather than inherited, and a change to it REPLACES the view
 * (a preload cannot be detached from a live `WebContents`). That is the enforcement point for
 * "the capability bridge only ever reaches loopback".
 */
async function navigateToEndpoint(win, url, { isLocal = false } = {}) {
  if (!win || win.isDestroyed()) return false;
  cancelReachProbe();
  activeUrl = url;
  const wantBridge = shouldAttachBridge(url);
  if (win._pcBridgeAttached !== wantBridge) {
    setupWindowContents(win, { attachBridge: wantBridge });
    win._pcBounds();
  }
  const wc = win.webContents;
  wc.loadFile(path.join(__dirname, "loading.html"));
  try {
    if (isLocal) await waitForBackend(win);
    else await waitForEndpoint(win, url);
    if (win.isDestroyed()) return false;
    wc.loadURL(url);
    if (!isLocal) wc.once("did-finish-load", () => adoptInstanceName(wc, url));
    return true;
  } catch (err) {
    if (win.isDestroyed()) return false;
    const probe = err && err.probe;
    if (probe) {
      const id = loadRegistry(shellStore).active;
      if (id) endpointHealth = { ...endpointHealth, [id]: probe };
    }
    console.warn(`could not reach ${url}: ${err.message}`);
    // Fall back to the gateway this shell owns rather than leaving a blank window. Spawn-local is
    // the default connection model, and an unreachable paired gateway is exactly when that matters.
    if (localGatewayUrl && url !== localGatewayUrl) {
      openConnectDialog();
      return navigateToEndpoint(win, localGatewayUrl, { isLocal: true });
    }
    return false;
  }
}

/**
 * Let a gateway name itself in the switcher (contract item 8: label from
 * `companion.instance_name`, falling back to the hostname, user override wins).
 *
 * 🔑 THE PAGE FETCHES IT, NOT THIS PROCESS. `GET /api/companion/discovery` needs a session, and the
 * session for a paired gateway is an httponly cookie in the WebView's jar — the main process cannot
 * present it and must not go looking for a way to. So the loaded page fetches its own gateway's name
 * and the answer comes back through `executeJavaScript`.
 *
 * The result is untrusted text from a machine the shell does not control, so it goes through
 * `sanitizeLabel` (control characters out, clamped) and `adoptGatewayLabel` (a name the user typed
 * is never overwritten). Best-effort throughout: a gateway with discovery disabled, an older
 * gateway without the route, or a fetch that simply fails leaves the hostname label alone.
 */
async function adoptInstanceName(wc, url) {
  if (!wc || wc.isDestroyed?.() || !shellStore) return;
  const reg = loadRegistry(shellStore);
  const row = reg.endpoints.find((e) => e.base_url === url && e.id !== LOCAL_ENDPOINT_ID);
  if (!row) return;
  try {
    const name = await wc.executeJavaScript(
      `fetch('/api/companion/discovery', {credentials: 'same-origin'})
         .then(r => r.ok ? r.json() : null)
         .then(j => (j && typeof j.instance_name === 'string') ? j.instance_name : '')
         .catch(() => '')`
    );
    const result = adoptGatewayLabel(shellStore, row.id, name);
    if (result.changed) console.log(`gateway ${row.id} named itself "${result.label}"`);
  } catch (err) {
    console.warn(`could not read the gateway's instance name: ${err.message}`);
  }
}

/** The handler set the connect dialog drives. `main.js` owns them so the dialog module stays a
 *  view, and so every one of them goes through `connectMode`'s decisions rather than around them. */
function connectHandlers() {
  return {
    list: async () => {
      const registry = loadRegistry(shellStore);
      return describeList({
        registry,
        activeId: registry.active,
        health: endpointHealth,
        localBaseUrl: localGatewayUrl || "",
        warnings: startupWarnings,
        storeStatus: shellStore.status,
        storeSafe: Boolean(shellStore.permissions && shellStore.permissions.safe),
        readOnly: shellStore.readOnly,
      });
    },
    prepare: (input) => prepareEndpoint(input),
    confirm: async ({ input, label }) => {
      const plan = await prepareEndpoint(input);
      if (!plan.ok) return plan;
      const { id } = confirmEndpoint(shellStore, plan, { label });
      startupWarnings = [];
      const ok = await navigateToEndpoint(mainWindow, plan.navigateTo, { isLocal: plan.trust === "loopback" && plan.navigateTo === localGatewayUrl });
      if (ok) connectDialog.close();
      return { ok, id, origin: plan.origin, navigateTo: plan.navigateTo };
    },
    switchTo: async (id) => {
      // Resolve the row's host BEFORE deciding, so the confirmation's host-moved check has
      // something to compare against. Passing nothing here skips that check silently.
      const row = loadRegistry(shellStore).endpoints.find((e) => e.id === id);
      const fingerprint = row && row.kind !== "local" ? await currentFingerprintFor(row.base_url) : "";
      const result = switchTo(shellStore, id, { localBaseUrl: localGatewayUrl || "", currentFingerprint: fingerprint });
      if (!result.ok) {
        return {
          ok: false,
          code: result.reason,
          message: result.needsConfirmation
            ? "This gateway needs to be confirmed again before the app will connect to it."
            : `That gateway could not be opened (${result.reason}).`,
        };
      }
      startupWarnings = [];
      const isLocal = result.endpoint.id === LOCAL_ENDPOINT_ID || result.endpoint.kind === "local";
      const ok = await navigateToEndpoint(mainWindow, result.navigateTo, { isLocal });
      if (ok) connectDialog.close();
      return { ok, id };
    },
    forget: async (id) => {
      const result = forgetEndpoint(shellStore, id);
      if (result.ok) {
        const next = { ...endpointHealth };
        delete next[id];
        endpointHealth = next;
      }
      return result;
    },
    refresh: async () => {
      await refreshHealth();
      return { ok: true };
    },
  };
}

// ── Loading screen ──

async function showLoadingThenConnect(win) {
  const wc = win.webContents;
  wc.loadFile(path.join(__dirname, "loading.html"));
  win.show();

  try {
    await waitForBackend(win);
    if (win.isDestroyed()) return;
    activeUrl = localGatewayUrl;
    wc.loadURL(localGatewayUrl);
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

// ── New Tab — opens another view onto the ACTIVE gateway ──

async function openNewTab() {
  // A new tab follows the gateway the user is looking at, so in connect-mode it opens the paired
  // gateway rather than silently dropping them back onto the local one. It takes the same bridge
  // decision for the same reason the main window does.
  const target = activeUrl || localGatewayUrl;
  if (!mainWindow || mainWindow.isDestroyed() || !target) return;
  mainWindow.show();

  const isLocal = target === localGatewayUrl;
  const tabWin = makeWindow();
  setupWindowContents(tabWin, { attachBridge: shouldAttachBridge(target) });
  mainWindow.addTabbedWindow(tabWin);

  const wc = tabWin.webContents;
  wc.loadFile(path.join(__dirname, "loading.html"));
  try {
    if (isLocal) await waitForBackend(tabWin);
    else await waitForEndpoint(tabWin, target);
    if (!tabWin.isDestroyed()) wc.loadURL(target);
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
      {
        // The switcher's entrance (CA-8 / T4.4). One menu item rather than a submenu that mirrors
        // the registry: the dialog IS the list, and a menu rebuilt on every pairing would be a
        // second place the active gateway is drawn, free to disagree with the first.
        label: "Gateway",
        submenu: [
          { label: "Gateways…", accelerator: "CmdOrCtrl+Shift+G", click: () => openConnectDialog() },
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

    // The shell's own storage scope (CA-8). Opened before any window so the startup decision below
    // has the registry, and non-fatal by construction: a corrupt or unreadable store degrades to
    // spawn-local with a warning rather than taking the app down with it.
    shellStore = openShellStore({ home: PERSONALCLAW_HOME, log: (msg) => console.warn(`desktop: ${msg}`) });
    connectDialog = makeConnectDialog({
      BrowserWindowCtor: BrowserWindow,
      ipcMain,
      handlers: connectHandlers(),
      log: (msg) => console.warn(msg),
    });
    connectDialog.registerIpc(ipcMain);

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
    // Needs localGatewayUrl from the READY line, so it follows the gateway start. A
    // failure here leaves the gateway reporting "not connected" — degraded but
    // honest — and never blocks the window.
    await registerWithGateway();
    // Counts need `localGatewayUrl`, so the poll starts after the gateway is up. With no
    // gateway the menu simply reads "not connected".
    startPresenceRefresh();

    // ── spawn-local vs connect (CA-8) ──
    // `describeStartup` returns spawn-local for every reason it cannot fully justify doing
    // something else, and names which reason. The spawn-local branch below is the ORIGINAL code
    // path, unchanged.
    if (localGatewayUrl) rememberLocalGateway(shellStore, localGatewayUrl);
    // Resolve the active row's host first: `describeStartup` can only apply the host-moved check if
    // it is handed a current fingerprint, so omitting this would leave that guard unreachable.
    const activeRow = loadRegistry(shellStore).endpoints.find((e) => e.id === loadRegistry(shellStore).active);
    const activeFingerprint =
      activeRow && activeRow.kind !== "local" ? await currentFingerprintFor(activeRow.base_url) : "";
    const startup = describeStartup({ store: shellStore, currentFingerprint: activeFingerprint });
    startupWarnings = startup.warnings || [];
    if (startup.mode === "connect") {
      console.log(`connecting to a paired gateway: ${startup.origin} (${startup.trust})`);
      await navigateToEndpoint(win, startup.origin, { isLocal: false });
    } else {
      console.log(`starting in spawn-local mode (${startup.reason})`);
      await showLoadingThenConnect(win);
    }
    if (startupWarnings.length) openConnectDialog();
    // Health is probed AFTER the window is up: it is switcher decoration, not a gate on booting.
    refreshHealth().catch(() => {});

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
  // A pending reachability re-check would otherwise fire during teardown and call `loadURL` on a
  // window that is being destroyed (CA-8).
  cancelReachProbe();
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
