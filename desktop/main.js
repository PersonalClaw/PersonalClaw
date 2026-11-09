const { app, BaseWindow, BrowserWindow, WebContentsView, shell, Tray, Menu, nativeImage, nativeTheme, ipcMain, systemPreferences, Notification, globalShortcut, webContents, session } = require("electron");
const fs = require("fs");
const os = require("os");
const { spawn, execFile, execFileSync } = require("child_process");
const path = require("path");
const http = require("http");
const { findPersonalclawBin } = require("./find-bin");
const { attachContextMenu } = require("./context-menu");
const { IPC_CHANNELS, makeCapabilities, registerCapabilityIpc } = require("./capabilities");
const { listeningReport, makePushToTalk, pressTarget, registerPushToTalkIpc } = require("./pushToTalk");
const {
  DEEP_LINKS,
  bootRead,
  carryPresence,
  summarizePresence,
  makeTrayPresence, trayImageFile,
  shouldHideOnClose,
  shouldQuitOnAllWindowsClosed,
} = require("./trayPresence");
const { makeLoginItem, registerLoginItemIpc } = require("./loginItem");
const {
  makeNativeNotifications,
  registerNativeNotificationIpc,
} = require("./nativeNotifications");
const { shutdownGateway } = require("./gatewayShutdown");
const { quitWithin } = require("./quitSequence");
const { makeSystemBrowser, navigationGuard, registerSystemBrowserIpc, windowOpenHandler } = require("./systemBrowser");
const { appHome, buildGatewayEnv } = require("./gatewayEnv");
const {
  HOME_NEWER_EXIT,
  HOME_SERVED_EXIT,
  adoptedGoneSentence,
  isUnasked,
  makeLastLine,
  notStartedSentence,
  olderSentence,
  stoppedSentence,
} = require("./gatewayLost");
const homeGateway = require("./homeGateway");
const { makeGatewayWatch } = require("./gatewayWatch");
const { makeLocalSignIn, movedUrl, movesOnRestart, parseReadyLine, signOutRequest } = require("./localSignIn");
const { PAGE_OWN_PAIR_HEADERS, PAIR_CHANGED, originKey, registerWindowTraffic } = require("./windowTraffic");
const { classify: classifyRefusal } = require("./shellRefusal");
const { openShellStore } = require("./shellStore");
const { ENABLED_KEY, buddyOutlivesClose, makeBuddyPresence, makeBuddyWindow, pushToBuddy, registerBuddyIpc, registerBuddySettingsIpc } = require("./buddyWindow");
const { makeDoorbell, mayRaise, openGatewaySocket } = require("./doorbell");
const { makeAbsence, makeBuddyHide } = require("./buddyAway");
const { pushToBridged } = require("./windowPushes");
const { cachedPolicy, keepPolicy, readPolicy, showWrite } = require("./buddyPolicy");
const { loadRegistry } = require("./endpointRegistry");
const {
  LOCAL_ENDPOINT_ID,
  HEALTH_REACHABLE,
  HEALTH_SIGNED_OUT,
  sessionEventFrom,
  signedOutHealth,
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
const { RELEASES_PAGE_URL, describeList, makeConnectDialog } = require("./connectDialog");

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
// Resolved as the gateway resolves its own (`appHome`): `~` expanded, links followed, a system
// folder refused. A home kept as written while the gateway resolves it is two folders, and this
// app's record watch then reads no record and withholds the windows' sign-in for good.
const PERSONALCLAW_HOME = appHome(process.env).home;
const { TAB_BAR_HEIGHT, makeWindowChrome } = require("./windowChrome");
const {
  STORE_KEY: APPEARANCE_KEY,
  DARK_CANVAS,
  READ_SCRIPT,
  fromPageRead,
  launch: launchAppearance,
  makeWindowAppearance,
  focusedAppWindow,
  appWindowOfSender,
} = require("./windowAppearance");

/** Every window the app has, for the appearance's window checks. */
const allWindows = () => BaseWindow.getAllWindows();

/** How long after a click or a key a page with no bridge is read for its theme. */
const READ_AFTER_INPUT_MS = 100;
/** How often the menu bar reads its count of what waits on you and what is running, while the
 *  doorbell's socket is down (connected, it reads on each ring instead). */
const PRESENCE_REFRESH_MS = 5000;
/** How long a stop of the gateway waits for it before escalating, outside the quit: the quit's
 *  stop has the grace its deadline leaves (`quitSequence.js`). */
const GATEWAY_GRACE_MS = 8000;

// Set app name for macOS menu bar and dock
app.name = "PersonalClaw";

let mainWindow = null;
/** The app window (the main window or a tab) focused last: a push-to-talk press made from another
 *  app is for it. */
let lastFocusedAppWindow = null;
/** The buddy's window (`buddyWindow.js`), and whether it exists: made once the app is ready. */
let buddy = null;
let buddyPresence = null;
let gatewayProcess = null;
/** The pid of the gateway this shell started, while it runs: it leads its own group, which the
 *  shell's exit takes down if a quit did not (see the `exit` handler below). */
let liveGatewayGroup = null;
let isQuitting = false;
/** How the latest start of the gateway failed (`startGateway`'s `err.exit`), until one succeeds. */
let startFailure = null;
/**
 * The gateway this app uses and did not start, while it uses one: `{pid, port}` of this home's
 * running gateway, once it proved it serves this home (`homeGateway.reach`). The app never signals,
 * stops or restarts it: `stopGateway` acts on `gatewayProcess`, the app's own child, alone, and
 * quitting leaves this one running.
 */
let adoptedGateway = null;
/** Set once the gateway the app used stopped, its own or one it found, until a new one is in use:
 *  nothing that carries this home's credentials goes to the port it left (`credentialsWithheld`). */
let gatewayGone = false;
/** What the Gateways window's "This computer" row says (`connectDialog.describeLocal`): `starting`,
 *  `own`, `found`, `adopted`, `stopped`, `served`, `older`, `signed_out` or `cant_sign_in`, with the
 *  sentence a state carries. */
let localState = { state: "starting", sentence: "" };

/**
 * The gateway THIS shell spawned, resolved from its READY line once bound. Always loopback.
 *
 * 🔒 EVERY CREDENTIAL-BEARING CALL TARGETS THIS URL AND NOTHING ELSE. `.local_secret` and the
 * capability `shell_token` prove "I am a process running as this user on this machine", a claim
 * that is false for any other gateway by construction, and the shell's sign-in (`localSignIn`) is
 * the owner's session on this one gateway. So `postGateway`/`getGateway` are bound
 * here and re-assert it with `assertLoopbackTarget` before every send. Connect-mode added
 * a SECOND url to this file; keeping the two apart by name is what stops the day someone points a
 * registration call at the one the user typed.
 */
let localGatewayUrl = null;

/**
 * How many starts of the gateway this shell has taken a ready line from (`adoptLocalGateway`): the
 * first, each restart in place, and each start again after one stopped. A new value is a new
 * start, so what ran on the one before and is gone now was cut off (`carryPresence`).
 */
let gatewayStarts = 0;

/**
 * The shell's sign-in to that gateway: the owner session its ready line hands out for this start,
 * held in this process's memory and nowhere else (`localSignIn.js`). Built once Electron is ready,
 * because it writes the window cookie through the app's session.
 */
let localSignIn = null;

/**
 * What the WebView actually loads. Defaults to `localGatewayUrl` — spawn-local is still
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
  pushToBridged(BaseWindow.getAllWindows(), "status", msg);
}

/**
 * Spawn the bundled gateway on an OS-assigned ephemeral port and resolve once
 * it prints its `PERSONALCLAW_READY:{...}` line. The gateway asks every request
 * for a sign-in, as every install does: the ready line carries the owner session
 * it minted for this start, and the shell signs its windows in with that
 * (`adoptLocalGateway`). Every LATER ready line is the same process restarted in
 * place, on a new port with a new session, and the shell follows it there
 * (`followRestartedGateway`). So does the first ready line of a gateway started
 * again after one stopped (`gatewayLost`): the windows still show the old address.
 *
 * An exit before the ready line rejects, carrying the exit (`err.exit`). An exit after it,
 * while the app is not quitting, is one nobody asked for, and the owner is told (`gatewayLost`).
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
        // One builder, executed by its own test — including the install kind, which the
        // gateway cannot infer for itself inside a frozen bundle. See gatewayEnv.js.
        env: buildGatewayEnv({
          env: process.env,
          loginPath: resolveLoginPath(),
          projectDir: path.resolve(__dirname, ".."),
        }),
      }
    );
    // This start's own process, which its exit is about (`gatewayProcess` moves on to the next).
    const child = gatewayProcess;
    liveGatewayGroup = child.pid ?? null;

    let settled = false;
    let stdoutBuf = "";
    // Where the gateway says why it stopped, if it ever does (`gatewayLost.js`).
    const lastLine = makeLastLine();
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
        // Nothing read from this pipe is logged: the ready line carries a live owner session.
        const ready = parseReadyLine(line);
        if (!ready) continue;
        // Only while this child is still the app's gateway (`readyLineIsStale`).
        if (readyLineIsStale(child, ready)) continue;
        if (settled) {
          takeReadyLine(child, ready, true);
          continue;
        }
        settled = true;
        clearTimeout(timer);
        takeReadyLine(child, ready, false).then(() => {
          sendStatus("Connected ✓");
          resolve(localGatewayUrl);
        });
      }
    });
    gatewayProcess.stderr.on("data", (c) => {
      lastLine.push(c);
      console.error("gateway:", c.toString().trim());
    });
    gatewayProcess.on("error", (err) => {
      console.error("Failed to start gateway:", err.message);
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(err);
      }
    });
    gatewayProcess.on("exit", (code, signal) => {
      console.log(`Gateway exited with code ${code}`);
      if (liveGatewayGroup === child.pid) liveGatewayGroup = null;
      const held = gatewayProcess === child;
      if (held) gatewayProcess = null;
      const exit = { code, signal, lastLine: lastLine.get() };
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(Object.assign(new Error(`Gateway exited with code ${code}`), { exit }));
        return;
      }
      // A restart whose new image could not start is one of these.
      if (isUnasked({ held, quitting: isQuitting })) {
        gatewayLost(exit).catch((err) => console.error(`desktop: ${err.message}`));
      }
    });
  });
}

/**
 * Whether the app's own gateway is up or starting: the child of its latest start spawned and has
 * not exited. A start that timed out is still so, since `startGateway` rejects and leaves the child
 * running. While it is, this home's running gateway is that child, never one the app did not start.
 */
function ownGatewayLives() {
  const child = gatewayProcess;
  return Boolean(child) && child.pid !== undefined && child.exitCode === null && child.signalCode === null;
}

/**
 * Whether *child*, whose ready line *ready* is, is no longer this app's gateway: one the app is
 * stopping, or one whose exit Node delivered before its last output. Such a line arms nothing and
 * sends nothing; its session is kept, to be signed out at the next gateway proven to be there. A
 * restart in place keeps the pid, so its line is taken.
 */
function readyLineIsStale(child, ready) {
  if (gatewayProcess && gatewayProcess.pid === child.pid) return false;
  if (ready.token) keepToSignOut(ready);
  return true;
}

/**
 * A ready line from the app's own child: this start is watched from it (the child's pid, its port
 * and the secret it wrote), then taken, as the first start or a restart in place. The only place
 * the own gateway's watch is armed from a start.
 */
function takeReadyLine(child, ready, settled) {
  if (readyLineIsStale(child, ready)) return Promise.resolve();
  const start = { pid: child.pid, port: ready.port, secretDigest: homeGateway.startDigest(PERSONALCLAW_HOME) };
  gatewayWatch.own(start);
  const taken = settled || localGatewayUrl ? followRestartedGateway(ready, { start }) : adoptLocalGateway(ready, { start });
  return taken.catch((err) => console.warn(`desktop: could not take the gateway's ready line: ${err.message}`));
}

/**
 * Stop the gateway and WAIT for it.
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
async function stopGateway({ graceMs = GATEWAY_GRACE_MS, killGraceMs } = {}) {
  const child = gatewayProcess;
  if (!child) return { outcome: "none" };
  gatewayProcess = null;
  const result = await shutdownGateway({
    child,
    graceMs,
    ...(killGraceMs === undefined ? {} : { killGraceMs }),
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

/**
 * The gateway this app started stopped when nobody asked it to (`startGateway`'s exit handler), so
 * nothing serves the windows now. The main window says so, and the Gateways window opens with how
 * it ended on its "This computer" row, where the owner starts a gateway here again or connects to
 * another one. Started again, its first ready line moves every window to its address, signed in,
 * the way a restart does (`startLocalHere`).
 *
 * This used to be a sheet with Start Again and Quit. A sheet holds its window, and the Gateways
 * window is that window's child, so it queued behind the sheet and never showed: with a start that
 * could only fail again, there was nothing to do but quit.
 */
async function gatewayLost(exit) {
  startFailure = exit;
  gatewayGone = true;
  gatewayWatch.drop();
  sawGateway("none", null);
  withholdForGood();
  if (exit.code === HOME_NEWER_EXIT) setOlder();
  else setLocalState("stopped", stoppedSentence(exit));
  showNotConnected();
}

/** The gateway in use stopped: its origin is retired and the windows' cookie leaves the jar. A
 *  gateway that starts again is signed in afresh. */
function withholdForGood() {
  if (!localSignIn) return;
  localSignIn
    .withhold(() => session.defaultSession.closeAllConnections(), { retire: true })
    .catch((err) => console.warn(`desktop: could not withhold the windows' sign-in: ${err.message}`));
}

/** The app's own gateway was refused this home because a newer PersonalClaw has served it
 *  (`HOME_NEWER_EXIT`): the row names that version and offers the newer app, never a start that
 *  would be refused again. */
function setOlder() {
  setLocalState("older", olderSentence(homeGateway.newestThatServed(PERSONALCLAW_HOME)));
}

/**
 * Nothing serves the app's windows: the main window says so, with no spinner, and the chooser opens
 * (use this home's running gateway, when one proved it serves the home; add a gateway; try again).
 * The Gateways window stays in the menu. A window showing a paired gateway keeps it.
 */
function showNotConnected() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (!activeUrl || activeUrl === localGatewayUrl) {
      mainWindow.webContents.loadFile(path.join(__dirname, "loading.html"), { query: { state: "offline" } });
    }
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
  }
  connectDialog?.openChooser();
}

/** What the Gateways window's "This computer" row says, told to the window if it is open. */
function setLocalState(state, sentence = "") {
  localState = { state, sentence };
  connectDialog?.changed();
}

// ── This home's running gateway, when the app did not start it ──

/**
 * This home's running gateway, proven to serve it (`homeGateway.reach`) and not signed in to:
 * `{ok, gateway}`, or `{ok: false, reason, sentence}`. With *waitMs*, a home that has no running
 * gateway yet is asked again each second until then: a gateway that has just won the home from this
 * app's start writes its runtime record only once it listens.
 */
async function findHomeGateway({ waitMs = 0 } = {}) {
  const until = Date.now() + waitMs;
  for (;;) {
    const found = await homeGateway.reach(PERSONALCLAW_HOME);
    if (found.ok || found.reason !== "none" || Date.now() >= until) return found;
    await new Promise((r) => setTimeout(r, 1000));
  }
}

/**
 * This home's running gateway, proven and signed in: `{ok, gateway, ready}`, or `{ok: false,
 * reason, sentence}` (`homeGateway.reach`'s reasons, or `sign_in`).
 */
async function reachHomeGateway({ waitMs = 0 } = {}) {
  const found = await findHomeGateway({ waitMs });
  if (!found.ok) return found;
  const verdict = mayUseFoundGateway(found.gateway);
  if (!verdict.ok) return { ok: false, reason: verdict.state, sentence: verdict.sentence, gateway: found.gateway, verdict };
  const signed = await homeGateway.signIn(found.gateway, PERSONALCLAW_HOME);
  if (!signed.ok) return { ok: false, reason: "sign_in", sentence: signed.sentence };
  return { ok: true, gateway: found.gateway, ready: signed.ready, pair: signed.pair };
}

/** The shell-store key of the chooser's "Use it automatically next time": a running gateway the
 *  app's own start finds is used without asking. Off until the owner ticks it, and the Gateways
 *  window's Ask Each Time turns it off again. */
const AUTO_USE_KEY = "pclaw:local-gateway:use-running";

function autoUseRunning() {
  return Boolean(shellStore) && shellStore.getItem(AUTO_USE_KEY) === "1";
}

function setAutoUseRunning(on) {
  if (!shellStore) return;
  if (on) shellStore.setItem(AUTO_USE_KEY, "1");
  else shellStore.removeItem(AUTO_USE_KEY);
}

/** The "This computer" row's sentence for a running gateway the app found and has not used. */
function foundSentence(gateway) {
  return (
    `PersonalClaw's own gateway didn't start: this home's gateway is already running, on port ` +
    `${gateway.port} (pid ${gateway.pid}), started outside this app. Use it, or quit it and try again.`
  );
}

/**
 * Use this home's running gateway, which the app did not start: proven again and signed in to, as
 * a command run for the home is (`reachHomeGateway`). Refused while the app's own gateway is up or
 * starting (`ownGatewayLives`), on a press and on the standing yes alike: the home's gateway is then
 * that one. Resolves `{ok: true}` once it serves the windows, or `{ok: false, code, message}`.
 */
async function useRunningGateway() {
  if (ownGatewayLives()) return { ok: false, code: "own", message: "This app's own gateway is running, so there is no other one to use." };
  setLocalState("starting");
  const found = await reachHomeGateway();
  if (found.ok) {
    if (await adoptHomeGateway(found)) return { ok: true };
    return { ok: false, code: localState.state, message: localState.sentence };
  }
  if (found.verdict) {
    setBehind(found.gateway, found.verdict);
    return { ok: false, code: found.verdict.state, message: found.sentence };
  }
  const gone = found.reason === "none";
  startFailure = gone ? { gone: true } : { served: true };
  setLocalState(gone ? "stopped" : "served", found.sentence);
  return { ok: false, code: localState.state, message: found.sentence || "This home's gateway could not be used." };
}

/**
 * Use *found*, this home's running gateway, as the app's own gateway: its session is held as a ready
 * line's (`adoptLocalGateway`), every request to it goes to its proven 127.0.0.1 address, and the
 * capability bridge attaches to it, as to the gateway the app starts. A gateway the app used before
 * (one that stopped, or the app's own start) is followed there, the way a restart is.
 */
async function adoptHomeGateway(found) {
  // A gateway this app found is used only with its window's door, whose session comes with a pair.
  if (!found.pair) return refusePairless(found.gateway, found.ready);
  const followed = Boolean(localGatewayUrl);
  adoptedGateway = { pid: found.gateway.pid, port: found.gateway.port };
  sawGateway("adopted", found.gateway);
  const start = startOf(found.gateway);
  gatewayWatch.adopt(start);
  startFailure = null;
  if (followed) await followRestartedGateway(found.ready, { pair: found.pair, start });
  else await adoptLocalGateway(found.ready, { pair: found.pair, start });
  setLocalState("adopted");
  console.log(`desktop: using this home's gateway on port ${found.gateway.port} (pid ${found.gateway.pid}), which this app did not start`);
  return true;
}

/**
 * Bring a gateway up for this home: the app's own, first. Only when that start fails, for any
 * reason (a home another gateway serves included), and its child is gone, is this home's running
 * gateway looked for. One that proves it serves the home is used at once if the owner said to every
 * time (`autoUseRunning`), and otherwise offered, unused and unsigned-in, in the chooser (`found`);
 * nothing that carries a credential goes to it before then. Resolves whether a gateway serves the
 * windows now. Every failure lands on the chooser and the Gateways window's "This computer" row,
 * with its sentence.
 */
async function bringUpLocalGateway() {
  // No watch outlives the start it followed: the next one is armed by its own ready line.
  gatewayWatch.drop();
  setLocalState("starting");
  sendStatus("Starting PersonalClaw's gateway…");
  try {
    await startGateway();
    startFailure = null;
    adoptedGateway = null;
    sawGateway("own", null);
    // The window's door may have said otherwise on the way (`askWindowDoor`): that row stands.
    if (localState.state === "starting") setLocalState("own");
    return true;
  } catch (err) {
    const exit = err.exit || { startError: err.message };
    // A start that timed out leaves its child running, and a gateway that serves the home then may
    // be that child: nothing is looked for while it lives, and the row says it did not start.
    if (ownGatewayLives()) {
      console.error("Gateway did not start:", err.message);
      startFailure = exit;
      setLocalState("stopped", notStartedSentence(exit));
      return false;
    }
    // A newer PersonalClaw has served this home: no gateway runs (it would hold the lock), and a
    // start here would be refused again. The newer app is the way on.
    if (exit.code === HOME_NEWER_EXIT) {
      startFailure = { ...exit, older: true };
      setOlder();
      return false;
    }
    // A start the home refused because another gateway serves it waits for that one's runtime
    // record, which it writes only once it listens.
    const served = exit.code === HOME_SERVED_EXIT;
    if (!served) console.error("Gateway did not start:", err.message);
    const found = await findHomeGateway({ waitMs: served ? 20_000 : 0 });
    if (found.ok) {
      // One this app may not use is said on the row, and neither offered nor used.
      const verdict = mayUseFoundGateway(found.gateway);
      if (!verdict.ok) {
        setBehind(found.gateway, verdict, { show: false });
        return false;
      }
      // Used without asking only on the owner's standing yes; its own failure is said on the row.
      if (autoUseRunning()) return (await useRunningGateway()).ok;
      startFailure = { ...exit, found: true };
      setLocalState("found", foundSentence(found.gateway));
      return false;
    }
    if (found.reason !== "none") {
      // Something serves this home that the app cannot sign in to.
      startFailure = { ...exit, served: true };
      setLocalState("served", found.sentence);
      return false;
    }
    startFailure = exit;
    setLocalState("stopped", notStartedSentence(exit));
    return false;
  }
}

/**
 * Follow the gateway in use through the doorbell's news (`gatewayWatch.js`), the app's own and one
 * it found. Once its socket is lost, nothing that carries this home's credentials goes to its port,
 * and the windows' cookie is out of the jar (`loseGateway`), until `homeGateway.reach` proves it
 * there again; a socket that comes back up proves nothing.
 *
 * - **The same start proven back** (its pid, its port, the secret it wrote): the windows' session is
 *   written back as it was, with nothing minted (`localSignIn.lift`).
 * - **A gateway the app found, started again where it was:** the app signs in to the new start and
 *   signs the old session out there (`takeBackAdoptedGateway`). Moved, it is found where it went;
 *   gone, the app says so.
 * - **The app's own gateway, started again:** its next ready line rotates the session
 *   (`followRestartedGateway`), and its exit is `gatewayLost`.
 *
 * Only once the windows' sign-in is back does anything carry the credentials again: the
 * registration (the capability registry and the local secret are new with each start), the
 * doorbell, and the windows parked at their routes (`resumeGateway`).
 */
const gatewayWatch = makeGatewayWatch({
  home: PERSONALCLAW_HOME,
  reach: (home) => homeGateway.reach(home),
  readRecord: (home) => homeGateway.readRecord(home),
  pidIsAlive: (pid) => homeGateway.pidIsAlive(pid),
  follow: () => followMovedGateway(),
  // The own gateway's lift is keyed to the start the held session came from, as a found one's is.
  proven: (was, gateway) => (was.kind === "own" ? localSignIn.lift(lostAt, { start: startOf(gateway) }) : takeBackAdoptedGateway(was, gateway)),
  back: (was) => resumeGateway(was),
  gone: (was) => adoptedGone(was),
  log: (msg) => console.log(`desktop: ${msg}`),
});

/**
 * The gateway the app found moved to another port: its start there is signed in to at its door, at
 * most WINDOW_DOOR_ATTEMPTS times in all, as a new start on the same port is (`takeBackAdoptedGateway`).
 * Once they are spent the windows stay parked, and the row offers nothing: Sign In Again signs in
 * only at the windows' own address, never at another port. Resolves whether it is the gateway in
 * use now.
 */
async function followMovedGateway() {
  const found = await findHomeGateway();
  if (!found.ok) return false;
  const verdict = mayUseFoundGateway(found.gateway);
  if (!verdict.ok) {
    setBehind(found.gateway, verdict);
    return false;
  }
  const start = startOf(found.gateway);
  if (!spendDoorAsk(start)) return false;
  const gen = localSignIn.gen;
  const signed = await homeGateway.signIn(found.gateway, PERSONALCLAW_HOME);
  if (localSignIn.gen !== gen) {
    if (signed.ok) keepToSignOut(signed.ready);
    return false;
  }
  if (!signed.ok) {
    if (signed.status === 400 || signed.status === 403) spendDoorAsks(start);
    if (!doorAsksLeft(start)) {
      console.warn(`desktop: the windows could not be signed in at the gateway on port ${start.port}; they stay parked`);
      setLocalState("cant_sign_in", NOT_SIGNED_IN_AGAIN_SENTENCE);
    }
    return false;
  }
  // A refusal said meanwhile ends once the gateway is in use at its new port: the adopt clears the
  // start's failure, so whether one was in force is read before it.
  const refused = Boolean(startFailure && startFailure.behind);
  const used = await adoptHomeGateway({ ok: true, gateway: found.gateway, ready: signed.ready, pair: signed.pair });
  if (used && refused) refusalEnded();
  return used;
}

/** The windows' sign-in generation at the last loss (`loseGateway`): a lift acts only on it. */
let lostAt = 0;
/** Sessions this app minted or replaced that are still live, each signed out at the next gateway proven
 *  to be there (`signOutKept`): never at a port that is not. */
let keptSessions = [];

/** Keep *session* to be signed out once a gateway is proven there. */
function keepToSignOut(session) {
  if (session && session.token && !keptSessions.some((s) => s.token === session.token)) keptSessions.push(session);
}

/** Sign out every kept session the gateway in use can be asked about now, each within *timeoutMs*;
 *  keep the rest. */
async function signOutKept(timeoutMs) {
  const list = keptSessions;
  keptSessions = [];
  for (const session of list) {
    if (gatewayGone || gatewayWatch.withheld) keepToSignOut(session);
    else await signOutLocalSession(session, timeoutMs);
  }
}

/**
 * The doorbell lost the gateway in use, or heard it restarting. In this order, before anything is
 * awaited: the credentials are withheld (`gatewayWatch.lost`); the windows' sign-in is withheld and
 * every connection the windows hold is closed; then the window cookie leaves the jar
 * (`localSignIn.withhold`). The windows stay where they are, and what they send to the gateway is
 * cancelled (`windowTraffic`). A gateway the app found has its origin retired with the session:
 * its next sign-in is a new one, unless the same start is proven back.
 */
function loseGateway() {
  const kind = gatewayWatch.kind;
  if (!kind) return;
  const already = gatewayWatch.withheld;
  gatewayWatch.lost();
  // Withheld already for this very loss: nothing more. A lift of the shell's own hold leaves the
  // windows withheld at the old generation, and an End withholds nothing, so a later loss moves it.
  if (already && localSignIn.withheld && lostAt === localSignIn.gen) return;
  localSignIn
    .withhold(() => session.defaultSession.closeAllConnections(), { retire: kind === "adopted" })
    .catch((err) => console.warn(`desktop: could not withhold the windows' sign-in: ${err.message}`));
  lostAt = localSignIn.gen;
}

/**
 * A gateway the app found proved it serves this home on its port again. The same start (its pid
 * and the secret it wrote) gets the windows' session back as it was, its origin no longer retired;
 * a new start is signed in to afresh, and the session it replaces is signed out once the gateway is
 * back (`resumeGateway`), at that start's own door. Resolves whether the windows' sign-in is back.
 */
async function takeBackAdoptedGateway(was, gateway) {
  const start = startOf(gateway);
  const sameStart =
    gateway.pid === was.pid &&
    Boolean(gateway.secretDigest) &&
    gateway.secretDigest === was.secretDigest &&
    Boolean(gateway.windowDoor) === windowDoorInUse;
  if (sameStart) {
    // One read with the held session at the proven port first: an ended sign-in is not lifted.
    const read = await readHeldSessionAt(gateway.port);
    if (read && read.verdict === "end") {
      // With a pair, the sign-in ends (`noteShellRefusal`). Without one, the doorbell stops for this
      // start, which ends it in the watch (`stopDoorbellForStart`): no later look reads it again.
      if (localSignIn.held && localSignIn.held.pair) noteShellRefusal(read.stamp, read.status, read.body, { retiredToo: true });
      else stopDoorbellForStart();
      return false;
    }
    const lifted = await localSignIn.lift(lostAt, { unretire: true, start });
    // A refusal said meanwhile (`setBehind`) told the updater no gateway is in use: this one is again.
    if (lifted && startFailure && startFailure.behind) {
      sawGateway("adopted", gateway);
      endRefusal();
    }
    return lifted;
  }
  // A new start, one this app may use: asked at its door WINDOW_DOOR_ATTEMPTS times in all, across
  // looks and losses.
  const verdict = mayUseFoundGateway(gateway);
  if (!verdict.ok) {
    setBehind(gateway, verdict);
    return false;
  }
  if (!spendDoorAsk(start)) return false;
  const gen = localSignIn.gen;
  const signed = await homeGateway.signIn(gateway, PERSONALCLAW_HOME);
  if (localSignIn.gen !== gen) {
    if (signed.ok) keepToSignOut(signed.ready);
    return false;
  }
  if (!signed.ok) {
    if (signed.status === 400 || signed.status === 403) spendDoorAsks(start);
    if (!doorAsksLeft(start)) doorGaveUp(start, Boolean(gateway.windowDoor));
    return false;
  }
  if (!signed.pair) return refusePairless(gateway, signed.ready);
  windowDoorInUse = Boolean(signed.ready.windowDoor);
  // Pages that latched an End stay latched until Sign In Again's reloads run, whichever take-back
  // brings the sign-in back (`latchedSinceEnd`).
  const wasSignedOut = localSignIn.signedOut || latchedSinceEnd;
  // The adopt's generation is taken in the tick it starts: a loss during its cookie write moves it.
  const adopting = localSignIn.adopt(signed.ready, { pair: signed.pair, start });
  const mine = localSignIn.gen;
  keepToSignOut(await adopting);
  if (localSignIn.gen !== mine) {
    if (localSignIn.stamp().token !== signed.ready.token) keepToSignOut(signed.ready);
    return false;
  }
  signedInAfresh = { wasSignedOut, gen: mine };
  sawGateway("adopted", gateway);
  endRefusal();
  return true;
}

/**
 * The watch took back a start this app may use while the row said why it refused one (`setBehind`):
 * that refusal ends (`refusalEnded`). Only while one is in force.
 */
function endRefusal() {
  if (!(startFailure && startFailure.behind)) return;
  startFailure = null;
  refusalEnded();
}

/**
 * A refusal ended, at a take-back (`endRefusal`) or at a follow to another port
 * (`followMovedGateway`): the row says the gateway is in use, the chooser closes, and the main
 * window, which the refusal left on the offline page, loads the gateway again as the chooser's
 * press does (`connectHandlers`' switchTo, for this computer's row). A main window on a paired
 * gateway stays there.
 */
function refusalEnded() {
  refusedStart = "";
  setLocalState("adopted");
  connectDialog?.closeChooser();
  if (localGatewayUrl && (!activeUrl || activeUrl === localGatewayUrl)) {
    void navigateToEndpoint(mainWindow, localGatewayUrl, { isLocal: true });
  }
}

/** A found gateway's new start signed the windows in afresh (`takeBackAdoptedGateway`), for the
 *  resume that follows at that generation: whether the gateway had signed this app out before it. */
let signedInAfresh = null;
/** Set at an End (`shellSignedOut`), cleared once Sign In Again's reloads run (`reloadAfterSignInAgain`):
 *  the windows' pages latched the sign-out, and a resume a newer take-back overtook never reloaded them. */
let latchedSinceEnd = false;

/**
 * The gateway in use is back and the windows' sign-in with it (`gatewayWatch`'s *back*): what
 * carries the credentials goes again, once. A session replaced on the way is signed out at it.
 */
function resumeGateway(was) {
  if (was.kind === "adopted") adoptedGateway = { pid: was.pid, port: was.port };
  shellToken = null;
  // Only the take-back this resume follows: a flag left by one whose hand-over never came is stale.
  const afresh = signedInAfresh && signedInAfresh.gen === localSignIn.gen ? signedInAfresh : null;
  signedInAfresh = null;
  (async () => {
    if (localSignIn.signedOut) return;
    startPresenceRefresh();
    await signOutKept();
    await registerWithGateway();
    // An End while the registration was out stops here: its sign-out stopped the doorbell, and
    // nothing reloads or asks the door again.
    if (localSignIn.signedOut) return;
    // So does a start the watch has left while the sign-outs or the registration were out: what
    // moved the watch (a ready line, an exit, a Start, a take-back, a follow or a gone gateway) does
    // what follows.
    if (startKey(gatewayWatch.start) !== startKey(was)) return;
    // The shell's own hold came back without the windows (`localSignIn.lift`): the window's door is
    // asked again, with what is left of this start's asks.
    if (was.kind === "own" && localSignIn.forShell && localGatewayUrl && !credentialsWithheld()) {
      await askWindowDoor({ port: Number(new URL(localGatewayUrl).port), url: localGatewayUrl }, localSignIn.start);
      // And one that came while the ask was out.
      if (startKey(gatewayWatch.start) !== startKey(was)) return;
    }
    // A new start signed in afresh brings the row back; the same start's lift leaves it as it was.
    if (afresh) rowBackFromSignedOut();
    if (!localSignIn.withheld) reloadParkedWindows();
    // A sign-in afresh at the same address changed the session under the pages that stayed. After an
    // End their pages latched the sign-out, so they reload as Sign In Again's do; otherwise each is
    // told its pair changed, and reads it again in place.
    if (afresh && afresh.wasSignedOut) reloadAfterSignInAgain();
    else if (afresh) dispatchPairChanged();
  })().catch((err) => console.warn(`desktop: could not take the gateway back: ${err.message}`));
}

/** Whether nothing that carries this home's credentials (the shell's session, the local secret, the
 *  capability token) may go to `localGatewayUrl` now: while the gateway in use is unproven since the
 *  doorbell lost it, once it stopped, and once it ended this app's sign-in (`shellSignedOut`). */
function credentialsWithheld() {
  return gatewayGone || gatewayWatch.withheld || Boolean(localSignIn && localSignIn.signedOut);
}

/** The adopted gateway stopped and nothing took its place: the app says so and offers the Gateways
 *  window. It did not stop it and does not start it again. */
function adoptedGone(was) {
  const port = was ? was.port : adoptedGateway ? adoptedGateway.port : 0;
  adoptedGateway = null;
  sawGateway("none", null);
  gatewayGone = true;
  withholdForGood();
  shellToken = null;
  stopPresenceRefresh();
  startFailure = { adoptedGone: true };
  setLocalState("stopped", adoptedGoneSentence(port));
  console.log(`desktop: the gateway on port ${port}, which this app did not start, stopped`);
  showNotConnected();
}

// ── Signing the windows in ──

/** `origin` of *url*, or "" for anything that is not one. */
function originOf(url) {
  try {
    return new URL(url).origin;
  } catch {
    return "";
  }
}

/**
 * Take the gateway at *ready*'s address as this shell's own, and sign its windows in with the
 * session its ready line carries. Resolves the session that replaced one (a restart's), or null.
 * A failure to write the cookie is logged and leaves the window to the gateway's sign-in page:
 * an honest "you're signed out" rather than a window that never loads.
 */
async function adoptLocalGateway(ready, { pair = null, start = null, restarted = false } = {}) {
  localGatewayUrl = ready.url;
  gatewayStarts += 1;
  gatewayGone = false;
  windowDoorInUse = Boolean(ready.windowDoor);
  try {
    if (ready.windowDoor && !pair) return await takeWindowDoor(ready, start, { restarted });
    return await localSignIn.adopt(ready, { pair, start });
  } catch (err) {
    console.warn(`desktop: could not sign the window in to ${ready.url}: ${err.message}`);
    return null;
  }
}

/** Whether the gateway in use has the window's own door: its ready line, or its proven answer, said so. */
let windowDoorInUse = false;
/** How many times the window's door is asked a start, at most, and how far apart. */
const WINDOW_DOOR_ATTEMPTS = 3;
const WINDOW_DOOR_RETRY_MS = 2000;
/** The asks at the window's door spent on one gateway start (`{pid, secretDigest}`): WINDOW_DOOR_ATTEMPTS
 *  a start in all, across looks and losses, for the app's own gateway and one it found. */
let doorAsks = { key: "", spent: 0 };
const startKey = (start) => `${start ? start.pid : ""}:${start ? start.secretDigest || "" : ""}`;
/** Spend one of *start*'s asks; false once they are all spent. */
function spendDoorAsk(start) {
  const key = startKey(start);
  if (doorAsks.key !== key) doorAsks = { key, spent: 0 };
  if (doorAsks.spent >= WINDOW_DOOR_ATTEMPTS) return false;
  doorAsks.spent += 1;
  return true;
}
/** Spend every ask *start* has left: a refusal (400, 403) or a disagreement ends them. */
function spendDoorAsks(start) {
  doorAsks = { key: startKey(start), spent: WINDOW_DOOR_ATTEMPTS };
}
/** How many asks *start* has left. */
function doorAsksLeft(start) {
  return doorAsks.key === startKey(start) ? WINDOW_DOOR_ATTEMPTS - doorAsks.spent : WINDOW_DOOR_ATTEMPTS;
}

/** The gateway's start that *gateway*, a proven answer, names: `{pid, port, secretDigest}`. */
function startOf(gateway) {
  return { pid: gateway.pid, port: gateway.port, secretDigest: gateway.secretDigest || "" };
}

/**
 * *start*'s asks at the window's door are spent and none signed the windows in: they stay parked.
 * The flag the start proved is the one in use from now, so a press is judged by it. With the door,
 * the This computer row says signed out, with Sign In Again; without it (a disagreement, or a start
 * that has none), Sign In Again would be refused, so the row says so, with no action.
 */
function doorGaveUp(start, door) {
  windowDoorInUse = door;
  console.warn(`desktop: the windows could not be signed in at the gateway on port ${start.port}; they stay parked`);
  if (door) setLocalState("signed_out", SIGNED_OUT_SENTENCE);
  else setLocalState("cant_sign_in", NOT_SIGNED_IN_AGAIN_SENTENCE);
}

/**
 * The app's own gateway has the window's own door (its ready line says so). The windows get a
 * session of their own, with its pair, asked at that door (`askWindowDoor`). Until then the ready
 * line's session serves the shell's own requests and no window loads (`localSignIn.holdForShell`).
 * Resolves the session an earlier start held, for the caller to sign out, or null.
 */
async function takeWindowDoor(ready, start, { restarted = false } = {}) {
  // The generation is taken in step with the hold's own bump, before anything is awaited.
  const holding = localSignIn.holdForShell(ready, { start });
  const gen = localSignIn.gen;
  const earlier = await holding;
  // At a restart, the follow brings parked windows back itself, once it has retired the old address.
  await askWindowDoor(ready, start, gen, { reloadParked: !restarted });
  return earlier;
}

/**
 * Ask the window's door of the app's own gateway for the windows' session (`homeGateway.signIn`),
 * through a proof on *ready*'s port whose pid is the child's, while *start* has asks left
 * (WINDOW_DOOR_ATTEMPTS a start, WINDOW_DOOR_RETRY_MS apart), and while the generation is still
 * *gen*, or, once it adopts, that adopt's. Once the windows' session is written, the ready line's is
 * signed out, by Bearer. A loss stops the asks, and the same start proven back asks again with what
 * is left (`resumeGateway`). A refusal (400, 403) or a proven answer that says it has no such door
 * spends them all: nothing falls back to the ready line's session for the windows. Once none is
 * left, the windows stay parked (`doorGaveUp`). Resolves whether this ask signed the windows in and
 * saw it through.
 */
async function askWindowDoor(ready, start, gen = localSignIn.gen, { reloadParked = true } = {}) {
  let asked = 0;
  while (doorAsksLeft(start) > 0) {
    if (asked > 0) await new Promise((r) => setTimeout(r, WINDOW_DOOR_RETRY_MS));
    if (localSignIn.gen !== gen) return false;
    spendDoorAsk(start);
    asked += 1;
    const found = await homeGateway.reach(PERSONALCLAW_HOME);
    if (localSignIn.gen !== gen) return false;
    const child = gatewayProcess;
    const proven = found.ok && found.gateway.port === ready.port && Boolean(child) && found.gateway.pid === child.pid;
    if (proven && !found.gateway.windowDoor) {
      console.warn(`desktop: the gateway on ${ready.url} said it has the window's door, and its proven answer says it hasn't`);
      spendDoorAsks(start);
      doorGaveUp(start, false);
      return false;
    }
    if (proven) {
      // No lifetime asked: the gateway gives its own sign-in's, what the ready line's lasts.
      const signed = await homeGateway.signIn(found.gateway, PERSONALCLAW_HOME, { ttl: null });
      if (localSignIn.gen !== gen) {
        if (signed.ok) keepToSignOut(signed.ready);
        return false;
      }
      if (signed.ok) {
        // As Sign In Again's: a loss or a newer ready line during the cookie write moves the
        // generation, and nothing of this ask goes on; what it replaced is kept to be signed out.
        const adopting = localSignIn.adopt(signed.ready, { pair: signed.pair, start });
        const mine = localSignIn.gen;
        const readySession = await adopting;
        if (localSignIn.gen !== mine) {
          keepToSignOut(readySession);
          if (localSignIn.stamp().token !== signed.ready.token) keepToSignOut(signed.ready);
          return false;
        }
        startPresenceRefresh();
        keepToSignOut(readySession);
        await signOutKept();
        // Again after the sign-outs: a loss, a newer start, Sign In Again, a stop or a quit while they
        // were out moves the generation, and this ask goes no further. The lift's resume reloads the
        // parked windows and leaves the row, a newer start does both once it has retired this address,
        // and Sign In Again does its own.
        if (localSignIn.gen !== mine) return false;
        rowBackFromSignedOut();
        if (reloadParked) reloadParkedWindows();
        return true;
      }
      if (signed.status === 400 || signed.status === 403) spendDoorAsks(start);
    }
  }
  if (asked > 0) doorGaveUp(start, true);
  return false;
}

/**
 * End *held* — a sign-in this shell holds — at the gateway it runs now. Best-effort: quitting and
 * a restart both carry on whatever the gateway answers. The request carries only that session's
 * own credential, so it is the session ending itself (`/api/auth/logout`): its cookie at a gateway
 * without the window's door, as a Bearer with its own pair at one that has it (`signOutRequest`).
 */
async function signOutLocalSession(held, timeoutMs) {
  if (!held || !localGatewayUrl) return false;
  const { path: route, headers } = signOutRequest(held, Number(new URL(localGatewayUrl).port), { windowDoor: windowDoorInUse });
  const res = await postGateway(route, {}, headers, { signedIn: false, timeoutMs });
  return Boolean(res && res.revoked);
}

/**
 * The gateway restarted in place — Settings → Restart, or an applied update re-executes the same
 * process — and printed a new ready line: a new port, and a new session for the new start.
 *
 * Every piece of this shell that pointed at the old address moves to the new one: the window
 * cookie (`adopt`), the switcher's local row, the capability registration (the registry is the
 * gateway's memory, so it did not survive), and every window that showed the old address, which
 * loads the same route at the new one, signed in. The session the old start handed out is signed
 * out, so a restart leaves one desktop sign-in in Settings → Devices, not one more each time.
 */
async function followRestartedGateway(ready, { pair = null, start = null } = {}) {
  const previousOrigin = originOf(localGatewayUrl);
  const replaced = await adoptLocalGateway(ready, { pair, start, restarted: true });
  if (previousOrigin && previousOrigin !== originOf(localGatewayUrl)) localSignIn.retireOrigin(previousOrigin);
  console.log(`desktop: the gateway restarted; it is now at ${localGatewayUrl}`);
  if (shellStore) rememberLocalGateway(shellStore, localGatewayUrl);
  shellToken = null;
  // The doorbell rings at the new address, with the new start's session, right away.
  startPresenceRefresh();
  keepToSignOut(replaced);
  await signOutKept();
  await registerWithGateway();
  rowBackFromSignedOut();
  if (originOf(activeUrl) === previousOrigin) activeUrl = localGatewayUrl;
  for (const wc of webContents.getAllWebContents()) {
    if (wc.isDestroyed()) continue;
    const next = movedUrl(wc.getURL(), previousOrigin, localGatewayUrl);
    if (!next) continue;
    // Only a page that loads on the window's cookie alone moves; any other window closes.
    if (movesOnRestart(next)) {
      loadOrPark(wc, next);
    } else {
      moveOffExempt(wc, next);
    }
  }
  reloadParkedWindows();
}

/**
 * A window whose page at *next* doesn't load on the cookie alone, after a restart moved the
 * gateway: the main window goes to the dashboard at its hash, the buddy is left to its own route,
 * and any other window closes.
 */
function moveOffExempt(wc, next) {
  const owner = BaseWindow.getAllWindows().find((w) => !w.isDestroyed() && w.webContents === wc);
  if (owner && owner === buddy?.window) return;
  if (owner && owner !== mainWindow) owner.close();
  else loadOrPark(wc, dashboardAt(localGatewayUrl, next));
}

// ── Capability bridge ↔ gateway registration ──

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
    // Every bridged window and tab, so a second tab draws the same grant (#3831).
    pushToBridged(BaseWindow.getAllWindows(), IPC_CHANNELS.state, { capability: cap, state });
    pushCapabilityState();
  },
});

/**
 * Push-to-talk. The shell owns the chord; the RENDERER owns the
 * microphone. `onCapturing` is therefore driven by the renderer's report of its live
 * stream, never by "we forwarded a press" — see the module header for why that
 * direction is the one that keeps the indicator honest.
 */
const pushToTalk = makePushToTalk({
  globalShortcut,
  // A press goes to the tab in use (`pressTarget`), and a capture's stop to the tab it runs in:
  // every tab is a window of its own, and each press went to the first one whichever was in use.
  send: (payload, window) => {
    try {
      const id = Number(window);
      const wc = id > 0
        ? webContents.fromId(id)
        : pressTarget({
            focused: BaseWindow.getFocusedWindow(),
            lastFocused: lastFocusedAppWindow || mainWindow,
            isAppWindow: (win) => win._pcBridgeAttached === true,
          });
      if (wc && !wc.isDestroyed()) wc.send(IPC_CHANNELS.pushToTalk, payload);
    } catch {
      /* window may be gone mid-press */
    }
  },
  // The tray, every window and the gateway hear it (#3833). `postGateway` is a function
  // declaration below, read when a report is made, never while this module evaluates.
  onCapturing: listeningReport({
    // Every window's contents (the main window is a BaseWindow holding views), as the shell
    // reaches all of them when the gateway moves.
    windows: () =>
      webContents
        .getAllWebContents()
        .filter((wc) => !wc.isDestroyed())
        .map((wc) => ({ webContents: wc })),
    postGateway: (route, body) => postGateway(route, body),
    // The buddy shows its listening face even when its owner hid it, so a hidden window comes up.
    onIndicator: (on) => {
      setCaptureIndicator(on);
      if (on && buddy?.window && !buddy.window.isDestroyed() && !buddy.isVisible()) buddy.show();
    },
    channel: IPC_CHANNELS.listening,
  }),
});

/**
 * Native OS notifications — the `native` delivery target, actuated.
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
  // A buddy alert's still orb, from the PNG data URL the renderer painted: never a path or a URL.
  createIcon: (url) => {
    const image = nativeImage.createFromDataURL(url);
    return image.isEmpty() ? null : image;
  },
  // A tap on a buddy alert opens the buddy (#3897) while Buddy status is on for this computer and
  // its window lives: shown without taking focus, and rung so its page opens as a click opens it.
  openAlert: () => {
    if (!buddyPresence?.enabled() || !buddy?.window || buddy.window.isDestroyed()) return false;
    buddy.show();
    return pushToBuddy(buddy.window, "openAlert");
  },
  log: (msg) => console.warn(msg),
});

/**
 * The system's default browser, for every page that is not the gateway's: a window a page asks
 * for, a navigation away from the gateway, and the bridge's `systemBrowser.open`, which a page
 * calls when it has to know whether the browser opened (a remote tool server's sign-in).
 */
const systemBrowser = makeSystemBrowser({ shell, log: (msg) => console.warn(msg) });

/**
 * The always-on capturing indicator.
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
 * The title has exactly ONE writer (`composeTrayTitle` in
 * `trayPresence.js`), because the waiting badge wants the same pixels. Capture wins
 * there: a waiting count can wait a second, a live-microphone indicator cannot.
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
 * 🔒 THE TARGET IS RE-ASSERTED, NOT ASSUMED. Every caller carries the shell's sign-in, and
 * most carry `.local_secret` or the capability `shell_token` beside it, all of them claims about
 * THIS machine. Connect-mode put a second, user-supplied URL in this file, so the loopback
 * property is checked here rather than left to the reader of the variable name. A failed
 * assertion resolves `null` — the same answer as any other failure, and crucially *nothing is
 * sent* — instead of throwing into a startup path that has no handler for it.
 *
 * The gateway asks every request for a sign-in, so each carries the shell's session as a Bearer
 * header; `signedIn: false` leaves it off, for the one request that must present only the
 * session it ends (`signOutLocalSession`). `method` is POST but for a write the gateway takes as a
 * PATCH (`PATCH /api/config/personalclaw`, the menu bar's Show Buddy). */
function postGateway(pathname, body, headers = {}, { signedIn = true, method = "POST", timeoutMs = 5000 } = {}) {
  return new Promise((resolve) => {
    // A sign-out carries only the session it ends: it waits only for a proven gateway, not for the
    // shell's own sign-in. Anything signed in needs the held session's credential to go.
    if (!localGatewayUrl || gatewayGone || gatewayWatch.withheld) return resolve(null);
    if (signedIn && (credentialsWithheld() || !localSignIn.authorization().Authorization)) return resolve(null);
    try {
      assertLoopbackTarget(localGatewayUrl, `${method} ${pathname}`);
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
    // The sign-in this request carries, so a refusal acts on it alone (`noteShellRefusal`).
    const stamp = signedIn ? localSignIn.stamp() : null;
    const req = http.request(
      {
        hostname: url.hostname,
        port: url.port,
        path: url.pathname,
        method,
        timeout: timeoutMs,
        headers: {
          "Content-Type": "application/json",
          "Content-Length": payload.length,
          ...(signedIn ? localSignIn.authorization() : {}),
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
            if (stamp) noteShellRefusal(stamp, res.statusCode, buf);
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

/**
 * What a refusal of one of the shell's own requests says about the sign-in it carried (`shellRefusal`):
 * the gateway ended it, or refused this app's own pair on it (a defect, logged as one). Acted on only
 * while that sign-in is still the one held, once, and only on a gateway that gives a pair; a gateway
 * that gives none is handled as it always was. Nothing mints a new sign-in here: the owner presses
 * Sign In Again on the This computer row, or the gateway's next start signs the app in.
 */
function noteShellRefusal(stamp, status, body, { retiredToo = false } = {}) {
  const verdict = classifyRefusal(status, parseJson(body));
  if (verdict !== "end" && verdict !== "defect") return;
  localSignIn
    .end(stamp, { defect: verdict === "defect", retiredToo })
    .then((ended) => {
      if (ended) shellSignedOut();
    })
    .catch((err) => console.warn(`desktop: ${err.message}`));
}

/** *text* parsed as JSON, or null. */
function parseJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/** The gateway signed this app out: nothing the shell sends carries the sign-in again, the menu bar
 *  says not connected, and the This computer row says so, with Sign In Again. */
function shellSignedOut() {
  stopPresenceRefresh();
  // Nothing is lifted for this start again. A found gateway's look keeps going with no credential; the
  // app's own rests until its next ready line, its exit or a Sign In Again.
  gatewayWatch.ended();
  latchedSinceEnd = true;
  shellToken = null;
  if (trayPresence.available) trayPresence.setPresence(summarizePresence(null, null));
  setLocalState("signed_out", SIGNED_OUT_SENTENCE);
  console.log("desktop: the gateway signed this app out; it waits for Sign In Again");
}

/** What the This computer row says once the gateway signed this app out. */
const SIGNED_OUT_SENTENCE = "This computer's gateway is running, but this app is signed out of it.";
/** What a Sign In Again says when the gateway changed under the press. */
const GATEWAY_CHANGED_SENTENCE = "This computer's gateway changed while this app signed in. Try again.";
/** What a Sign In Again that can't sign in here says. */
const NOT_SIGNED_IN_AGAIN_SENTENCE = "This app can't sign in to this computer's gateway again from here. Quit PersonalClaw and open it again.";

/**
 * Sign In Again, pressed on the This computer row: the one place a sign-in is minted after the
 * gateway ended one, through the door its proven answer names (`homeGateway.signIn`). The row
 * offers it only on a gateway with the window's door: only a sign-in that holds its pair is ever
 * signed out (`localSignIn.end`), and only that door gives one. A press on any other gateway is
 * refused and logged as a defect; one whose proven answer no longer names the door is refused too.
 */
async function signInAgain() {
  if (!windowDoorInUse) {
    console.warn("desktop: Sign In Again was pressed on a gateway without the window's door: a defect in this app; nothing was signed in");
    return { ok: false, message: NOT_SIGNED_IN_AGAIN_SENTENCE };
  }
  // Where the press began: an answer that lands after a loss, a restart or a move is not adopted.
  const gen = localSignIn.gen;
  const at = localGatewayUrl;
  const found = await homeGateway.reach(PERSONALCLAW_HOME);
  if (!found.ok || !at || found.gateway.port !== Number(new URL(at).port)) {
    return { ok: false, message: found.sentence || "This computer's gateway could not be reached." };
  }
  // The app's own gateway is the child it started: a proof naming another pid is not it.
  if (!adoptedGateway && !(gatewayProcess && found.gateway.pid === gatewayProcess.pid)) {
    return { ok: false, message: "This computer's gateway could not be reached." };
  }
  // A gateway this app found is asked its version first, as everywhere the app takes one up
  // (`mayUseFoundGateway`): an older one is behind, door or not.
  if (adoptedGateway) {
    const verdict = mayUseFoundGateway(found.gateway);
    if (!verdict.ok) {
      setBehind(found.gateway, verdict);
      return { ok: false, message: verdict.sentence };
    }
  }
  if (!found.gateway.windowDoor) {
    console.warn(`desktop: the gateway on ${at} had the window's door, and its proven answer says it hasn't; nothing was signed in`);
    return { ok: false, message: NOT_SIGNED_IN_AGAIN_SENTENCE };
  }
  if (localSignIn.gen !== gen || localGatewayUrl !== at) return { ok: false, message: GATEWAY_CHANGED_SENTENCE };
  // The app's own gateway gives its own lifetime; one the app found, the door's 30 days.
  const signed = await homeGateway.signIn(found.gateway, PERSONALCLAW_HOME, adoptedGateway ? {} : { ttl: null });
  if (!signed.ok) return { ok: false, message: signed.sentence };
  if (localSignIn.gen !== gen || localGatewayUrl !== at) {
    keepToSignOut(signed.ready);
    return { ok: false, message: GATEWAY_CHANGED_SENTENCE };
  }
  const start = startOf(found.gateway);
  // The adopt starts and its generation is taken in one tick, and the watch follows the start this
  // press proved before the cookie write is awaited: a loss or a ready line during the write moves
  // the generation (and the watch with it), and then nothing of this press goes on.
  const adopting = localSignIn.adopt(signed.ready, { pair: signed.pair, start });
  const mine = localSignIn.gen;
  if (adoptedGateway) {
    adoptedGateway = { pid: start.pid, port: start.port };
    gatewayWatch.adopt(start);
    sawGateway("adopted", found.gateway);
  } else {
    gatewayWatch.own(start);
  }
  // What it replaces is signed out later: the session the gateway ended, or the ready line's, which
  // the shell held while the window's door gave the windows nothing.
  keepToSignOut(await adopting);
  if (localSignIn.gen !== mine) {
    if (localSignIn.stamp().token !== signed.ready.token) keepToSignOut(signed.ready);
    return { ok: false, message: GATEWAY_CHANGED_SENTENCE };
  }
  setLocalState(adoptedGateway ? "adopted" : "own");
  // Signed in again at a found gateway: a refusal said before it no longer stands.
  if (startFailure && startFailure.behind) {
    startFailure = null;
    refusedStart = "";
  }
  shellToken = null;
  doorbellBound = { key: "", failures: 0, stopped: false };
  startPresenceRefresh();
  await signOutKept();
  await registerWithGateway();
  reloadAfterSignInAgain();
  reloadParkedWindows({ signedInAgain: true });
  return { ok: true };
}

/**
 * The windows on the held origin after Sign In Again, whose pages latched the sign-out: one that
 * loads on the window's cookie alone (`movesOnRestart`) reloads, the buddy loads its own route, a
 * main window anywhere else goes to the dashboard at its hash, and every other window is left as it
 * is.
 */
function reloadAfterSignInAgain() {
  latchedSinceEnd = false;
  const origin = originOf(localGatewayUrl);
  const bw = buddy?.window;
  if (bw && !bw.isDestroyed() && bw.webContents && originOf(bw.webContents.getURL()) === origin) {
    bw.webContents.loadURL(`${localGatewayUrl}/#/buddy`);
  }
  for (const win of appWindows()) {
    const wc = win.webContents;
    const url = wc.getURL();
    if (originOf(url) !== origin) continue;
    if (movesOnRestart(url)) wc.reload();
    else if (win === mainWindow) wc.loadURL(dashboardAt(localGatewayUrl, url));
  }
}

/** The dashboard at *base*, at the hash route *url* shows. */
function dashboardAt(base, url) {
  let hash = "";
  try {
    hash = new URL(url).hash;
  } catch {
    hash = "";
  }
  return `${base}/${hash}`;
}

/** Tell each live page on the held origin that its sign-in's pair changed (`PAIR_CHANGED`), so it
 *  reads it again in place, with no reload. */
function dispatchPairChanged() {
  const origin = originOf(localGatewayUrl);
  for (const wc of webContents.getAllWebContents()) {
    if (wc.isDestroyed() || originOf(wc.getURL()) !== origin) continue;
    wc.executeJavaScript(`document.dispatchEvent(new Event(${JSON.stringify(PAIR_CHANGED)}))`).catch(() => {});
  }
}

/**
 * One read with the held session at *port*, a found gateway proven back on it, before its
 * same-start lift: `{verdict: "ok" | "end" | "keep" | "none", status, body, stamp}`, "end" being a
 * refusal that ends the sign-in or names a defect (`shellRefusal`), or null when nothing answered.
 */
function readHeldSessionAt(port) {
  const headers = localSignIn.heldAuthorization();
  if (!headers.Authorization) return Promise.resolve(null);
  const stamp = localSignIn.stamp();
  return new Promise((resolve) => {
    const req = http.request(
      { hostname: "127.0.0.1", port, path: "/api/status", method: "GET", timeout: 5000, headers: { Accept: "application/json", ...headers } },
      (res) => {
        let buf = "";
        res.on("data", (c) => (buf += c));
        res.on("end", () => {
          if (res.statusCode === 200) return resolve({ verdict: "ok", status: 200, body: buf, stamp });
          const v = classifyRefusal(res.statusCode, parseJson(buf));
          resolve({ verdict: v === "end" || v === "defect" ? "end" : v, status: res.statusCode, body: buf, stamp });
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

/** The doorbell's failures before it came up on one start (`{key, failures, stopped}`): two, counted
 *  across lifts, stop it for that start (`stopDoorbellForStart`). */
let doorbellBound = { key: "", failures: 0, stopped: false };
const DOORBELL_FAILURES = 2;
/** How often the runtime record is read while the doorbell is up, and at the app's own gateway while
 *  the bound has it stopped: its removal, or another pid or port in it, is a loss. */
const RECORD_WATCH_MS = 2000;
let recordWatch = null;

const watchedKey = () => (gatewayWatch.start ? startKey(gatewayWatch.start) : "");

/** The doorbell said lost or restarting: one more failure before up on this start. */
function noteDoorbellFailure() {
  const key = watchedKey();
  if (doorbellBound.key !== key) doorbellBound = { key, failures: 0, stopped: false };
  doorbellBound.failures += 1;
  if (doorbellBound.failures >= DOORBELL_FAILURES) stopDoorbellForStart();
}

/**
 * Stop the doorbell for the start in use, and keep it stopped for that start, lifts included: the
 * gateway refuses this sign-in. With the window's door, the This computer row says signed out, with
 * Sign In Again; without it, that Sign In Again would be refused, and the row says so. A found start
 * is ended in the watch. The app's own keeps its same-start lift, and its record is still read with
 * no credential while the doorbell is stopped, so a restart in place, which unpublishes it, is a loss
 * within RECORD_WATCH_MS; its row describes the shell's sign-in, which a lift doesn't restore, so it
 * stands.
 */
function stopDoorbellForStart() {
  doorbellBound = { key: watchedKey(), failures: doorbellBound.failures, stopped: true };
  stopPresenceRefresh();
  console.warn("desktop: the gateway refuses this sign-in; the doorbell stops for this start");
  // A found start is ended in the watch: with its doorbell stopped nothing would hear its loss, so no
  // lift lets the windows back in, and the look, with no credential, still hears a gone, moved or new
  // start. The app's own gateway keeps its lift: once a lift resumes, its record is read with no
  // credential (`startPresenceRefresh`), so a restart in place is a loss; its exit and its next ready
  // line settle the rest.
  if (gatewayWatch.kind === "adopted") gatewayWatch.ended();
  if (windowDoorInUse) setLocalState("signed_out", SIGNED_OUT_SENTENCE);
  else setLocalState("cant_sign_in", NOT_SIGNED_IN_AGAIN_SENTENCE);
}

/**
 * The runtime record, read every RECORD_WATCH_MS with no credential, on a timeout `stopRecordWatch`
 * clears: while the doorbell is up, and at the app's own gateway while the bound has its doorbell
 * stopped (`startPresenceRefresh`). Gone, or naming another pid or port than the start watched, it
 * is a loss, and presence stops first: no socket opened before the loss speaks after it.
 */
function watchRecord() {
  stopRecordWatch();
  const read = () => {
    recordWatch = null;
    const start = gatewayWatch.start;
    if (!start) return;
    const record = homeGateway.readRecord(PERSONALCLAW_HOME);
    const live = Boolean(record) && homeGateway.pidIsAlive(record.pid);
    if (!live || record.pid !== start.pid || record.port !== start.port) {
      noteRecordLoss(start, record, live);
      stopPresenceRefresh();
      return loseGateway();
    }
    recordWatch = setTimeout(read, RECORD_WATCH_MS);
    if (recordWatch.unref) recordWatch.unref();
  };
  recordWatch = setTimeout(read, RECORD_WATCH_MS);
  if (recordWatch.unref) recordWatch.unref();
}

/** The start the record watch last said a loss for: once a start, the line names the file and why. */
let recordLossNoted = "";
/** Say, once a start, which of the record watch's checks lost the gateway and at which file. The
 *  record holds a pid and a port alone, so the line carries no credential. */
function noteRecordLoss(start, record, live) {
  if (recordLossNoted === startKey(start)) return;
  recordLossNoted = startKey(start);
  const file = path.join(PERSONALCLAW_HOME, homeGateway.RUNTIME_FILE);
  const cause = !record
    ? "is missing or unreadable"
    : !live
      ? `names pid ${record.pid}, which is not running`
      : `names pid ${record.pid} on port ${record.port}, not this start's (pid ${start.pid}, port ${start.port})`;
  console.warn(`desktop: the gateway's runtime record at ${file} ${cause}; the windows' sign-in is withheld until a proof brings it back`);
}

function stopRecordWatch() {
  if (recordWatch) clearTimeout(recordWatch);
  recordWatch = null;
}

/** The row back from signed out once a new start signed the windows in, with nothing withheld and
 *  the doorbell not stopped for that start. */
function rowBackFromSignedOut() {
  const out = localState.state === "signed_out" || localState.state === "cant_sign_in";
  const stopped = doorbellBound.stopped && doorbellBound.key === watchedKey();
  if (out && !stopped && !localSignIn.signedOut && !localSignIn.withheld) setLocalState(adoptedGateway ? "adopted" : "own");
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
 * the desktop can do anything. Best-effort: resolves whatever the gateway answers. */
function unregisterFromGateway(timeoutMs) {
  if (!shellToken) return Promise.resolve(null);
  const token = shellToken;
  shellToken = null;
  return postGateway("/api/desktop/unregister", {}, { "X-Shell-Token": token }, { timeoutMs });
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

/**
 * Wait for the spawned gateway to answer. The probe is `/api/healthz`, the one route that needs no
 * sign-in: the gateway asks every other request for one, and a credential-free probe of another
 * route would write a refusal to its audit log at every launch and every new tab.
 */
function waitForBackend(targetWin) {
  const healthUrl = `${localGatewayUrl}/api/healthz`;
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
 * Wait for a PAIRED gateway to answer. Deliberately a second function rather than a
 * generalisation of `waitForBackend`: the spawn-local readiness path is promised to stay
 * unchanged, so it is left byte-identical above.
 *
 * The difference that matters is not the URL, it is the refusals. `waitForBackend` retries
 * anything below a 500 because a gateway it just spawned is only ever slow. A gateway on the
 * network can *answer and refuse* — a health check answered 401/403, a redirect, or a host that
 * is not a PersonalClaw gateway at all — and none of those becomes ready by waiting. Each rejects
 * immediately, carrying the probe so the caller can say which happened. (A revoked device session
 * is not one of them: the probe carries no session, so it cannot see one. The page loads, the
 * gateway tells it why it is signed out, and `noteSessionEvent` puts that on the row.)
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
    body { font-family:-apple-system,sans-serif; padding:24px; background:${dark ? "#1e1f20" : "#ffffff"}; color:${dark ? "#e3e3e3" : "#1f1f1f"}; }
    label { display:block; margin-bottom:8px; font-size:13px; color:${dark ? "#9a9b9c" : "#444746"}; }
    input { width:100%; padding:10px; border-radius:6px; border:1px solid ${dark ? "#444746" : "#e1e3e1"};
      background:${dark ? "#131314" : "#ffffff"}; color:${dark ? "#e3e3e3" : "#1f1f1f"}; font-size:14px; outline:none; margin-bottom:12px; }
    input:focus { border-color:${dark ? "#ff6b5b" : "#b12e18"}; }
    .row { display:flex; gap:8px; }
    button { flex:1; padding:8px; border-radius:6px; border:none; cursor:pointer; font-size:13px; font-weight:600; }
    .ok { background:${dark ? "#ff6b5b" : "#b12e18"}; color:${dark ? "#3f1008" : "#ffffff"}; } .ok:hover { background:${dark ? "#ff9a86" : "#8d240b"}; }
    .cancel { background:${dark ? "#282a2c" : "#eef1f5"}; color:${dark ? "#9a9b9c" : "#444746"}; } .cancel:hover { background:${dark ? "#333537" : "#e6eaef"}; }`;
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

/** Read the page's appearance with a fixed script, and bring the window to it (`windowAppearance.js`). */
function readAppearance(view, win) {
  if (win.isDestroyed() || view.webContents.isDestroyed()) return;
  view.webContents.executeJavaScript(READ_SCRIPT).then((read) => {
    const a = fromPageRead(read);
    if (win.isDestroyed()) return;
    if (a) windowAppearance.apply(win, a);
    // A page with no mode of its own (the loading or offline page) follows `themeSource` by
    // itself, so its window takes the canvas of the mode in force.
    else windowAppearance.paint(win, nativeTheme.shouldUseDarkColors ? "dark" : "light");
  }).catch(() => {});
}

/**
 * After the system's switch, colour each app window whose page has no mode of its own in the mode
 * now in force. Never `apply`: a page that has a mode says its own theme, and two unfocused windows
 * on gateways in different themes would otherwise take turns over `themeSource`.
 */
function paintModelessWindows() {
  for (const win of appWindows()) {
    const view = win._pcView;
    if (!view || view.webContents.isDestroyed()) continue;
    view.webContents.executeJavaScript(READ_SCRIPT).then((read) => {
      if (win.isDestroyed()) return;
      if (fromPageRead(read) === null) windowAppearance.paint(win, nativeTheme.shouldUseDarkColors ? "dark" : "light");
    }).catch(() => {});
  }
}

/**
 * Build (or REBUILD) a window's content views.
 *
 * 🔒 `attachBridge` IS A SECURITY DECISION, NOT A FEATURE FLAG. `preload.js` exposes the
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

  // Create a WebContentsView positioned below the tab bar.
  //
  // NO `sandbox: false` here, deliberately — Electron's renderer sandbox stays ON, at its
  // default. It was off for exactly one line: `preload.js` used to
  // `require("./capabilities")`, and a sandboxed preload's polyfilled `require` resolves
  // `electron` plus three builtins and nothing else, so the preload threw on its second
  // line and `window.pclawDesktop` was never defined — absent rather than broken, which is
  // why nothing ever logged. #3348 inlined those two constants instead, so the bridge loads
  // with the sandbox intact.
  //
  // This is the view that loads the dashboard, i.e. the one that renders agent- and
  // app-authored HTML and script, so it is the LAST one that should give up an OS boundary:
  // the iframe `sandbox` attribute the widget frames use is a web-platform boundary, and
  // Chromium does not promise a null-origin blob frame its own renderer process.
  // `connectDialog.js` still pairs its preload with `sandbox: false` because
  // `connectPreload.js` does need a relative require; `capabilities.test.js` asserts that
  // pairing as a property (a preload needing Node ⇒ the flag) rather than as a spelling, so
  // neither half can drift silently.
  const view = new WebContentsView({
    webPreferences: {
      // The kept theme rides in as an argument, for a page of the app's own whose origin, a new
      // port at each launch, keeps none (the preload hands it over as `appearance.kept`).
      ...(attachBridge
        ? { preload: path.join(__dirname, "preload.js"), additionalArguments: [`--pclaw-appearance=${windowAppearance.preference()}`] }
        : {}),
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
  win._pcReadAppearance = () => readAppearance(win._pcView, win);
  win._pcView = view;
  if (!win._pcWired) {
    win._pcWired = true;
    win.on("resize", () => win._pcBounds());
    win.on("enter-full-screen", () => win._pcBounds());
    win.on("leave-full-screen", () => win._pcBounds());
    win.on("focus", () => win._pcReadAppearance());
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
  // The page's theme reaches the window at once: when its theme colour changes, which a paired
  // gateway's view, with no bridge, says too; and on every load, as a backstop.
  view.webContents.on("did-change-theme-color", () => win._pcReadAppearance());
  view.webContents.on("did-finish-load", () => win._pcReadAppearance());
  // A paired gateway's page has no bridge, and a switch that keeps its colour changes no theme
  // colour, so it is also read shortly after each click or key, with at most one read waiting.
  let readSoon = null;
  if (!attachBridge) view.webContents.on("input-event", (_e, input) => {
    if (input.type !== "mouseUp" && input.type !== "keyUp") return;
    if (readSoon) return;
    readSoon = setTimeout(() => {
      readSoon = null;
      win._pcReadAppearance();
    }, READ_AFTER_INPUT_MS);
  });
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

  // A window opens in the app only on the active origin; a blank or another origin's window is
  // denied, and a web page goes to the system browser. 🔒 The view never leaves that origin either:
  // the guard is what keeps the bridge from following a link elsewhere (`systemBrowser.js`).
  view.webContents.setWindowOpenHandler(windowOpenHandler({ allowedOrigin, systemBrowser }));
  const guardNavigation = navigationGuard({ allowedOrigin, systemBrowser, log: (msg) => console.warn(msg) });
  view.webContents.on("will-navigate", guardNavigation);
  view.webContents.on("will-redirect", guardNavigation);

  /**
   * A page that never loaded is the shell's problem; a page that loaded and lost its socket is the
   * SPA's (its capped-backoff reconnect is the published contract, and a second timer racing it
   * would duplicate every catch-up fetch). So the reachability probe starts HERE and nowhere else.
   */
  view.webContents.on("did-fail-load", (_e, errorCode, errorDescription, failedUrl, isMainFrame) =>
    windowLoadFailed(win, view.webContents, { errorCode, errorDescription, failedUrl, isMainFrame })
  );

  // The window's traffic is decided once, for every window, on the app's session
  // (`registerWindowTraffic`, at `whenReady`): never here, per window.
}

/** What a request the app's own webRequest listener cancelled fails with (`net::ERR_BLOCKED_BY_CLIENT`). */
const ERR_BLOCKED_BY_CLIENT = -20;

/**
 * A window's main-frame load failed. A navigation the windows' traffic cancelled (`windowTraffic`),
 * on any origin, parks this window alone on the offline page, to go back to its route once the
 * gateway is back (`reloadParkedWindows`): probing a cancelled origin would only load it again, to
 * be cancelled again. Any other failure at a gateway the owner added starts the reach probe.
 */
function windowLoadFailed(win, wc, { errorCode, errorDescription, failedUrl, isMainFrame }) {
  if (!isMainFrame) return;
  if (errorCode === -3) return; // ERR_ABORTED — a navigation we superseded ourselves
  if (errorCode === ERR_BLOCKED_BY_CLIENT) {
    park(wc, failedUrl);
    return;
  }
  if (!activeUrl || activeUrl === localGatewayUrl) return; // the app's own gateway is watched elsewhere
  console.warn(`load failed for ${failedUrl}: ${errorDescription} (${errorCode})`);
  scheduleReachProbe(win);
}

/** Windows parked on the offline page while the windows' sign-in was withheld: webContents id → the
 *  route each goes back to. */
const parked = new Map();

/** Show *wc* the offline page, keeping *url* for when the gateway is back. */
function park(wc, url) {
  if (!wc || wc.isDestroyed()) return;
  parked.set(wc.id, url);
  wc.loadFile(path.join(__dirname, "loading.html"), { query: { state: "offline" } });
}

/** Load *url* in *wc*, or park it there while the windows' sign-in to its origin is withheld. */
function loadOrPark(wc, url) {
  if (localSignIn && localSignIn.withheld && originOf(url) === originOf(localGatewayUrl)) {
    park(wc, url);
    return false;
  }
  wc.loadURL(url);
  return true;
}

/**
 * Every parked window goes back to its route. A route on the held origin loads there; one on an
 * origin this run retired moves to the gateway's address now, by the restart move's rule
 * (`movesOnRestart`); any other loads as it is, and the windows' traffic answers it again. After
 * Sign In Again (*signedInAgain*), a parked main window whose route doesn't load on the cookie alone
 * goes to the dashboard at its hash.
 */
function reloadParkedWindows({ signedInAgain = false } = {}) {
  const retired = localSignIn ? localSignIn.trafficState().retired : new Set();
  for (const [id, url] of [...parked]) {
    parked.delete(id);
    const wc = webContents.fromId(id);
    if (!wc || wc.isDestroyed()) continue;
    const onHeld = originOf(url) === originOf(localGatewayUrl);
    if (!onHeld && !retired.has(originKey(url))) {
      loadOrPark(wc, url);
      continue;
    }
    const next = onHeld ? url : movedUrl(url, originOf(url), localGatewayUrl) || url;
    const owner = BaseWindow.getAllWindows().find((w) => !w.isDestroyed() && w.webContents === wc);
    if (!onHeld && !movesOnRestart(next)) moveOffExempt(wc, next);
    else if (signedInAgain && owner && owner === mainWindow && !movesOnRestart(next)) loadOrPark(wc, dashboardAt(localGatewayUrl, next));
    else loadOrPark(wc, next);
  }
}

/**
 * Every request the app's windows make passes one decision (`windowTraffic.decide`), registered
 * once on the app's session (`windowTraffic.registerWindowTraffic`): cancelled while the windows'
 * sign-in is withheld or aimed at an origin this run left, and given the window session's pair
 * where it goes.
 *
 * A paired gateway that signs this app out says so on the page's OWN traffic: a 401/403 carrying
 * `X-Auth-Required`. The credential-free health probe cannot see that — the probe presents no
 * session — so the switcher's row read "Reachable" while the window showed the gateway's sentence.
 * Status and headers only; the shell presents nothing (`noteSessionEvent`).
 */
function watchWindowTraffic(appSession) {
  registerWindowTraffic(appSession, localSignIn);
  appSession.webRequest.onCompleted((details) => {
    noteSessionEvent(details).catch((err) => console.warn(`session note failed: ${err.message}`));
  });
}

/** The origin whose sign-out sentence was last asked for, so one sign-out is one ask. */
let signedOutOrigin = "";

/**
 * Record what the page's response says about the active gateway's sign-in on its row.
 *
 * 🔑 THE PAGE FETCHES THE SENTENCE, NOT THIS PROCESS — the same rule as `adoptInstanceName`. The
 * session is an httponly cookie in the WebView's jar; the main process cannot present it and must
 * not go looking for a way to. So the page asks its own gateway once, and the refusal's
 * `error.message` — why, when, and how to sign back in — comes back through `executeJavaScript`
 * as untrusted text, cleaned and clamped by `signedOutHealth`.
 */
async function noteSessionEvent(details) {
  if (!activeUrl || activeUrl === localGatewayUrl || !shellStore) return;
  let origin;
  try {
    origin = new URL(activeUrl).origin;
  } catch {
    return;
  }
  const event = sessionEventFrom(details, origin);
  const id = loadRegistry(shellStore).active;
  if (!event || !id) return;
  if (event === "signed_in") {
    signedOutOrigin = "";
    if (endpointHealth[id] && endpointHealth[id].status === HEALTH_SIGNED_OUT) {
      endpointHealth = { ...endpointHealth, [id]: { status: HEALTH_REACHABLE, httpStatus: details.statusCode, version: "", detail: "" } };
    }
    return;
  }
  if (signedOutOrigin === origin) return;
  signedOutOrigin = origin;
  const wc = webContents.fromId(details.webContentsId);
  let message = "";
  if (wc && !wc.isDestroyed()) {
    try {
      message = await wc.executeJavaScript(
        `fetch('/api/status', {credentials: 'same-origin', headers: { Accept: 'application/json', ...${PAGE_OWN_PAIR_HEADERS} }})
           .then(r => r.json().catch(() => null))
           .then(j => (j && j.error && typeof j.error.message === 'string') ? j.error.message : '')
           .catch(() => '')`
      );
    } catch {
      message = "";
    }
  }
  if (activeUrl && new URL(activeUrl).origin === origin) {
    endpointHealth = { ...endpointHealth, [id]: signedOutHealth(message, details.statusCode) };
  }
  console.log(`gateway ${origin} signed this app out`);
}

/** The appearance the shell keeps (`windowAppearance.js`), made again at launch from the store. */
let windowAppearance = makeWindowAppearance({ nativeTheme, canvas: DARK_CANVAS, focusedAppWindow: () => focusedAppWindow(allWindows) });
const windowChrome = makeWindowChrome({ platform: process.platform, canvas: () => windowAppearance.canvas() });
function makeWindow() {
  const win = new BaseWindow({
    width: 1280,
    height: 860,
    minWidth: 550, minHeight: 600,
    tabbingIdentifier: "personalclaw",
    ...windowChrome.options(),
  });
  win.on("closed", () => setImmediate(releaseBuddyIfAlone));
  win.on("focus", () => { lastFocusedAppWindow = win; });
  win.on("closed", () => { if (lastFocusedAppWindow === win) lastFocusedAppWindow = null; });
  // Over a full-screen window of the app's own the buddy shows only alerts, as a quiet peek
  // (`docs/design/buddy/state-machine.md`). Another app's full screen is not visible to the shell.
  win.on("enter-full-screen", tellBuddyFullScreen);
  win.on("leave-full-screen", tellBuddyFullScreen);
  win.on("closed", () => setImmediate(tellBuddyFullScreen));
  return win;
}

/** Tell the buddy's page whether one of the app's own windows is full screen. */
function tellBuddyFullScreen() {
  pushToBuddy(buddy?.window, "fullScreen", appWindows().some((w) => w.isFullScreen()));
}

/** The app's own windows, the main window and its tabs: every window but the buddy's. */
function appWindows() {
  return BaseWindow.getAllWindows().filter((w) => !w.isDestroyed() && w !== buddy?.window);
}

/** The buddy never keeps the app running by itself: with no menu-bar item, the last of the app's
 *  windows closing takes it too, so `window-all-closed` quits as it did before there was one. */
function releaseBuddyIfAlone() {
  if (!buddy?.window || !buddyPresence) return;
  const quitsWithNoWindow = shouldQuitOnAllWindowsClosed({ platform: process.platform, trayAvailable: trayPresence.available });
  if (!buddyOutlivesClose({ appWindowsLeft: appWindows().length, quitsWithNoWindow })) buddyPresence.stop();
}

function createWindow() {
  mainWindow = makeWindow();
  setupWindowContents(mainWindow);

  // Hiding on close is only safe while the menu bar can bring the window back, so
  // the decision reads the tray's real availability rather than assuming macOS has
  // one. With no tray this closes for real.
  mainWindow.on("close", (e) => {
    if (shouldHideOnClose({ trayAvailable: trayPresence.available, isQuitting })) {
      e.preventDefault();
      mainWindow.hide();
    }
  });

  return mainWindow;
}

// ── Menu-bar presence ──

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
 * The hash comes from `DEEP_LINKS`, a closed map, or is the route the local gateway names for a
 * running row (`running_now`). Either way `hashAssignmentScript` checks it against the hash
 * grammar and quotes it before the page takes it, so neither a name nor a route from the gateway
 * reaches the page as code, and a route outside the grammar opens nothing.
 */
function deepLink(hash) {
  showMainWindow();
  const wc = mainWindow?.webContents;
  if (!wc || wc.isDestroyed?.()) return;
  try {
    wc.executeJavaScript(hashAssignmentScript(hash));
  } catch (err) {
    console.warn(`deep link to ${hash} failed: ${err.message}`);
  }
}

/**
 * "Open at login". Opt-in, reversible, idempotent — see `loginItem.js`
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
  trayImagePath: path.join(__dirname, trayImageFile(process.platform)),
  // While Buddy status is on, the buddy's still for the state, in the system's appearance, and the
  // dock's while something needs you (#3896).
  platform: process.platform,
  isDark: () => nativeTheme.shouldUseDarkColors,
  dock: process.platform === "darwin" ? app.dock : null,
  appIconPath: path.join(__dirname, "icon.png"),
  log: (msg) => console.warn(`tray: ${msg}`),
  actions: {
    open: () => showMainWindow(),
    deepLink: (hash) => deepLink(hash),
    // Quick capture routes to the Inbox with a capture intent. The URL is UNCHANGED:
    // it was the reader that was missing, not the contract. Both halves exist now:
    // `POST /api/inbox/notes` writes a `user_note` item, and
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
    toggleBuddy: (next) => {
      setBuddyStatus(next).catch((err) => console.warn(`desktop: Show Buddy unchanged: ${err.message}`));
    },
    hideBuddy: (span) => (span === "tomorrow" ? buddyHide?.hideUntilTomorrow() : buddyHide?.hideForAnHour()),
    showBuddyNow: () => buddyHide?.showNow(),
    quit: () => {
      isQuitting = true;
      app.quit();
    },
  },
});

/** GET JSON from the loopback gateway, signed in with the shell's session. Resolves null on
 * any failure — a menu-bar refresh must never be able to throw into the app. */
function getGateway(pathname) {
  return new Promise((resolve) => {
    if (!localGatewayUrl || credentialsWithheld() || !localSignIn.authorization().Authorization) return resolve(null);
    let url;
    try {
      // The same re-assertion `postGateway` makes: this request carries the owner's session.
      assertLoopbackTarget(localGatewayUrl, `GET ${pathname}`);
      url = new URL(pathname, localGatewayUrl);
    } catch {
      return resolve(null);
    }
    const stamp = localSignIn.stamp();
    const req = http.request(
      {
        hostname: url.hostname,
        port: url.port,
        path: `${url.pathname}${url.search}`,
        method: "GET",
        timeout: 3000,
        headers: localSignIn.authorization(),
      },
      (res) => {
        let buf = "";
        res.on("data", (c) => (buf += c));
        res.on("end", () => {
          if (res.statusCode !== 200) {
            noteShellRefusal(stamp, res.statusCode, buf);
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
    req.on("error", () => resolve(null));
    req.on("timeout", () => {
      req.destroy();
      resolve(null);
    });
    req.end();
  });
}

/**
 * Read the live counts. `GET /api/waiting` is `{waiting: [...], unreadable: [...]}`; `GET /api/running` is
 * `{running: [...], finished: [...]}`. Which start of the gateway the read saw (`gatewayStarts`,
 * taken before the reads) is how a restart that cut work off is told apart: this shell takes every
 * start's ready line, so it needs no read of its own.
 */
async function readPresence() {
  const before = gatewayStarts;
  const [waiting, running, policy] = await Promise.all([
    getGateway("/api/waiting"),
    getGateway("/api/running"),
    // Buddy status and its surfaces (#3901), re-read on the same rings: `buddy_policy_changed` is one.
    getGateway("/api/buddy/policy"),
  ]);
  const boot = bootRead(before, gatewayStarts);
  return { waiting, running, policy, boot };
}

/** The last buddy policy read (`GET /api/buddy/policy`), or null before the first. */
let buddyPolicy = null;

/** Whether the owner is away (`buddyAway.js`): read while the buddy's window exists. */
const absence = makeAbsence({
  idleSeconds: () => require("electron").powerMonitor.getSystemIdleTime(),
  awayAfterSecs: () => buddyPolicy?.away_after_secs,
  onChange: (away) => pushToBuddy(buddy?.window, "away", away),
});

/** The owner's hide (`buddyAway.js`), made once the shell's store is open. */
let buddyHide = null;

function makeHide(store) {
  buddyHide = makeBuddyHide({
    store,
    onChange: (hidden) => {
      pushToBuddy(buddy?.window, "hidden", hidden);
      syncBuddyToTray();
      // The hide over, the buddy shows what it has (or keeps its top-edge zone).
      if (!hidden && buddy?.window && !buddy.isVisible()) buddy.show();
    },
  });
  buddyHide.start();
}

/** The buddy's context menu, at the pointer: hide it for a while, or open PersonalClaw. */
function openBuddyMenu() {
  if (!buddy?.window || buddy.window.isDestroyed() || !buddyHide) return;
  Menu.buildFromTemplate([
    { label: "Hide for an Hour", click: () => buddyHide.hideForAnHour() },
    { label: "Hide Until Tomorrow", click: () => buddyHide.hideUntilTomorrow() },
    { type: "separator" },
    { label: "Open PersonalClaw", click: () => showMainWindow() },
  ]).popup({ window: buddy.window });
}

/**
 * The doorbell's socket at the gateway this shell started, signed in with the shell's session: the
 * same re-assertion `getGateway` makes, since the socket carries the owner's session. A socket to
 * anywhere else, or before there is a gateway, is refused (the doorbell waits and tries again).
 */
function socketGateway(url, gateway) {
  if (!localGatewayUrl || originOf(gateway) !== originOf(localGatewayUrl)) throw new Error("not the gateway this shell started");
  if (credentialsWithheld()) throw new Error("this home's gateway is not proven to be there since it was lost");
  if (!localSignIn.authorization().Authorization) throw new Error("this app holds no sign-in to ring with");
  assertLoopbackTarget(localGatewayUrl, "WS /api/ws");
  return openGatewaySocket(url, gateway, () => localSignIn.authorization());
}

/**
 * One read of the lists, for each consumer. The menu bar folds it (`summarizePresence`, which
 * renders a failed read as "not connected" rather than as a zero that looks like good news, and
 * `carryPresence`, which carries the last rows over a read that failed). The buddy's page is rung
 * to read its own, and its window, hidden, is shown again for an item the gateway says may
 * interrupt you now (`mayRaise`): nothing else raises it.
 */
function takePresence({ waiting, running, policy, boot }) {
  // The policy's absence time is the buddy's (`buddyAway.js`); its status and surfaces the shell's.
  if (policy) buddyPolicy = policy;
  followBuddyPolicy(readPolicy(policy));
  if (trayPresence.available) {
    trayPresence.setPresence(carryPresence(trayPresence.presence, summarizePresence(waiting, running), { boot }));
  }
  const win = buddy?.window;
  if (!win || win.isDestroyed()) return;
  // Hidden by the owner, nothing waiting raises it.
  if (!buddy.isVisible() && !buddyHide?.hidden && mayRaise(waiting)) buddy.show();
  pushToBuddy(win, "ring");
}

/**
 * The doorbell (`doorbell.js`): the one socket this shell holds to the gateway it started, signed in
 * with the shell's session. The menu bar and the buddy read the lists on its rings, so nothing polls
 * while it is connected; while it is down it retries with backoff and the menu bar reads every
 * `PRESENCE_REFRESH_MS`. Its connection is the buddy's: lost, back, or a restart the gateway
 * announced.
 */
const doorbell = makeDoorbell({
  openSocket: socketGateway,
  read: readPresence,
  onLevels: takePresence,
  onConnection: (state) => doorbellConnection(state),
  pollMs: PRESENCE_REFRESH_MS,
  log: (msg) => console.warn(`desktop: ${msg}`),
});

/**
 * The doorbell's news. A restart the gateway announced is working, said as restarting, never
 * offline (#3896). Lost or restarting, the gateway in use is withheld until a proof brings it back
 * (`gatewayWatch`), and it counts toward the doorbell's bound for the start. Up, the runtime record
 * is read while it stays up.
 */
function doorbellConnection(state) {
  trayPresence.setRestarting(state === "restarting");
  pushToBuddy(buddy?.window, "connection", state);
  if (state === "up") {
    doorbellBound = { key: watchedKey(), failures: 0, stopped: false };
    gatewayWatch.up();
    watchRecord();
  } else {
    stopRecordWatch();
    noteDoorbellFailure();
    loseGateway();
  }
}

/** The buddy's page, at the gateway in use; none while the windows' sign-in is withheld. */
function buddyUrl() {
  return localGatewayUrl && !(localSignIn && localSignIn.withheld) ? `${localGatewayUrl}/#/buddy` : null;
}

/** Ring at the gateway this shell started; with no gateway, the menu simply reads "not connected". */
function startPresenceRefresh() {
  // Nothing rings at a gateway the credentials are withheld from: the lift's resume rings again.
  if (credentialsWithheld()) return;
  // A doorbell the bound stopped for this start stays stopped for it, whatever lifts it. At the
  // app's own gateway the record is still read: a restart in place unpublishes it, and that is a
  // loss (`watchRecord`).
  if (doorbellBound.stopped && doorbellBound.key === watchedKey()) {
    if (gatewayWatch.kind === "own") watchRecord();
    return;
  }
  if (localGatewayUrl && localSignIn && localSignIn.authorization().Authorization) doorbell.connect(localGatewayUrl);
}

function stopPresenceRefresh() {
  stopRecordWatch();
  doorbell.stop();
}

// ── Connect mode: the shell pointed at a gateway it did not spawn ──

/** Open (or focus) the connect dialog / switcher. */
function openConnectDialog() {
  if (!connectDialog) return;
  connectDialog.open();
}

/** The gateways the owner added, every registry row but the local one: the loopback origins a
 *  window may load besides the held one (`localSignIn.setPaired`, `windowTraffic.decide`). */
function syncPaired() {
  if (!shellStore || !localSignIn) return;
  const rows = loadRegistry(shellStore).endpoints || [];
  localSignIn.setPaired(rows.filter((e) => e.id !== LOCAL_ENDPOINT_ID && e.kind !== "local").map((e) => e.base_url));
}

/**
 * Removed before the first window loads: the window cookies an earlier version left for this
 * computer's gateways at localhost and [::1] (`pc_token`, `pc_token_<port>`), all but a localhost
 * gateway's own the owner added; the store is flushed.
 */
async function purgeStaleLocalCookies(appSession) {
  const keep = new Set();
  for (const row of (shellStore && loadRegistry(shellStore).endpoints) || []) {
    if (row.id === LOCAL_ENDPOINT_ID || row.kind === "local") continue;
    try {
      const u = new URL(row.base_url);
      // Only the cookie named for the port in the row's address: the scheme's default where it names none.
      if (u.hostname === "localhost" || u.hostname === "[::1]") keep.add(`${u.hostname}:${u.port || (u.protocol === "https:" ? "443" : "80")}`);
    } catch {
      /* a row with no address keeps nothing */
    }
  }
  let all = [];
  try {
    all = await appSession.cookies.get({});
  } catch (err) {
    console.warn(`desktop: could not read the app's cookies: ${err.message}`);
    return;
  }
  for (const cookie of all) {
    const host = String(cookie.domain || "").replace(/^\./, "");
    const at = host === "localhost" ? "localhost" : host === "[::1]" || host === "::1" ? "[::1]" : "";
    const named = /^pc_token(?:_(\d+))?$/.exec(cookie.name || "");
    if (!at || !named) continue;
    if (named[1] && keep.has(`${at}:${named[1]}`)) continue;
    await appSession.cookies.remove(`http://${at}/`, cookie.name).catch((err) => console.warn(`desktop: could not remove ${cookie.name} for ${at}: ${err.message}`));
  }
  await appSession.cookies.flushStore().catch((err) => console.warn(`desktop: could not flush the app's cookies: ${err.message}`));
}

/** Re-probe every row's health, credential-free. Never mutates the registry. */
async function refreshHealth() {
  if (!shellStore) return endpointHealth;
  syncPaired();
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
 *   - `refused` (the health check itself answered 401/403) → **stop dead**. Zero further
 *     attempts. A PersonalClaw gateway never refuses `/api/healthz`, so this is something in front
 *     of it, or something else; hammering it would also drive a gateway's per-IP lockout against
 *     its owner. (A revoked device session is seen on the page's own traffic instead —
 *     `noteSessionEvent` — because this probe, carrying no session, cannot see one.)
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
  const wantBridge = shouldAttachBridge(url, localGatewayUrl);
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
    loadOrPark(wc, url);
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
    if (localGatewayUrl && url !== localGatewayUrl && !startFailure) {
      openConnectDialog();
      return navigateToEndpoint(win, localGatewayUrl, { isLocal: true });
    }
    // Nothing to fall back to: the window says so, and the Gateways window is where to go next.
    wc.loadFile(path.join(__dirname, "loading.html"), { query: { state: "offline" } });
    openConnectDialog();
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
      `fetch('/api/companion/discovery', {credentials: 'same-origin', headers: ${PAGE_OWN_PAIR_HEADERS}})
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
  const handlers = {
    list: async () => {
      const registry = loadRegistry(shellStore);
      return describeList({
        registry,
        activeId: registry.active,
        health: endpointHealth,
        localBaseUrl: localGatewayUrl || "",
        local: { ...localState, autoUse: autoUseRunning() },
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
      syncPaired();
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
      syncPaired();
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
    // "This computer" row's Start a gateway here, and the chooser's and the row's Try again: the
    // app's own gateway first (`bringUpLocalGateway`), and the main window on it.
    start: async () => {
      const started = await startFromRow();
      return started.ok ? handlers.switchTo(LOCAL_ENDPOINT_ID) : started;
    },
    // The chooser's Use the Gateway Already Running, and the row's Use It: proven again and signed
    // in to only now, on the press. With *remember*, the app's own start uses it without asking.
    use: async ({ remember }) => {
      if (localState.state === "starting") return { ok: false, code: "starting", message: "A gateway for this home is already starting." };
      // Refused while the app's own gateway is up or starting (`useRunningGateway`).
      const used = await useRunningHere();
      if (!used.ok) return used;
      setAutoUseRunning(remember);
      return handlers.switchTo(LOCAL_ENDPOINT_ID);
    },
    // The Gateways window's Ask Each Time (and the chooser's box, through `use`).
    autoUse: async ({ on }) => {
      setAutoUseRunning(on);
      connectDialog.changed();
      return { ok: true, on: autoUseRunning() };
    },
    // The chooser's Add a Gateway…: the Gateways window, where a gateway is added.
    gateways: async () => {
      connectDialog.closeChooser();
      openConnectDialog();
      return { ok: true };
    },
    // "This computer" row's Open the Releases Page, for a home a newer PersonalClaw served: its one
    // fixed address, in the system browser, through the one door to it.
    releases: () => systemBrowser.open(RELEASES_PAGE_URL),
  };
  return handlers;
}

// ── Loading screen ──

/**
 * Show the loading screen until the gateway this app uses answers, then the dashboard.
 *
 * With no gateway (a start that failed or was refused, or a home served by one the app cannot sign
 * in to), or one that never answered, the window says it is not connected, at once and with no
 * spinner, and the Gateways window opens, where the owner starts a gateway here or connects to
 * another one (`showNotConnected`). That used to be a sheet with Start Again and Quit, and the
 * Gateways window, a child of the same window, queued behind it.
 */
async function showLoadingThenConnect(win) {
  const wc = win.webContents;
  wc.loadFile(path.join(__dirname, "loading.html"));
  win.show();

  try {
    if (startFailure || !localGatewayUrl) throw new Error("no gateway serves this home");
    await waitForBackend(win);
    if (win.isDestroyed()) return;
    activeUrl = localGatewayUrl;
    wc.loadURL(localGatewayUrl);
  } catch {
    if (win.isDestroyed()) return;
    if (!startFailure) {
      startFailure = { unanswered: true };
      setLocalState("stopped", notStartedSentence(startFailure));
    }
    showNotConnected();
  }
}

/**
 * The row's Start a Gateway Here, Try Again and Sign In Again, before the main window goes to this
 * computer's row (`connectHandlers`' start): `{ok: true}`, or `{ok: false, code, message}` with
 * the row as it is.
 */
async function startFromRow() {
  if (localState.state === "starting") return { ok: false, code: "starting", message: "A gateway for this home is already starting." };
  // The row's Sign In Again: the gateway runs and ended this app's sign-in (`shellSignedOut`).
  if (localState.state === "signed_out") {
    const signed = await signInAgain();
    return signed.ok ? { ok: true } : { ok: false, code: "signed_out", message: signed.message };
  }
  if (!(await startLocalHere())) {
    return { ok: false, code: localState.state, message: localState.sentence || "No gateway could be brought up for this home." };
  }
  return { ok: true };
}

/**
 * The Gateways window's Start a gateway here, and its Try again: a gateway for this home, the app's
 * own first, and the one already serving it only when that fails (`bringUpLocalGateway`). A start
 * that timed out can still be running, so it is stopped first: never two gateways on one home. The
 * first gateway the windows ever have is set up as the app's start sets one up; one that takes over
 * from a gateway that stopped is followed there (`followRestartedGateway`), which registers again.
 */
async function startLocalHere() {
  if (adoptedGateway && gatewayWatch.kind === "adopted") {
    // A refused start of the gateway this app found and uses: its watch follows it and takes it back
    // once a start this app may use is proven there (`endRefusal`); until then the press answers
    // with the row as it is.
    if (startFailure && startFailure.behind) return false;
    // Any other press lets go of that gateway first, as a gone one is (`adoptedGone`): the windows'
    // sign-in is withheld for good before the start drops the watch, so no session stays in the jar
    // that nothing watches.
    adoptedGateway = null;
    sawGateway("none", null);
    gatewayGone = true;
    withholdForGood();
    shellToken = null;
    stopPresenceRefresh();
  }
  const hadGateway = Boolean(localGatewayUrl);
  if (gatewayProcess) {
    // The last start's session is still held: nothing carries it to the port while that start stops
    // and the next one starts. The next ready line takes it from there (`adoptLocalGateway`).
    if (localSignIn && localSignIn.held && !adoptedGateway) {
      stopPresenceRefresh();
      gatewayGone = true;
      withholdForGood();
    }
    await stopGateway();
  }
  if (!(await bringUpLocalGateway())) return false;
  if (!hadGateway) await setUpFirstGateway();
  return true;
}

/**
 * The chooser's and the "This computer" row's Use It: this home's running gateway, which the app
 * did not start, set up as the windows' gateway the way `startLocalHere` sets up its own. Resolves
 * `useRunningGateway`'s answer.
 */
async function useRunningHere() {
  const hadGateway = Boolean(localGatewayUrl);
  const used = await useRunningGateway();
  if (used.ok && !hadGateway) await setUpFirstGateway();
  return used;
}

/** The first gateway the windows have, set up as the app's start sets one up. */
async function setUpFirstGateway() {
  if (shellStore) rememberLocalGateway(shellStore, localGatewayUrl);
  startPresenceRefresh();
  await registerWithGateway();
  buddyPresence?.start();
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
  setupWindowContents(tabWin, { attachBridge: shouldAttachBridge(target, localGatewayUrl) });
  mainWindow.addTabbedWindow(tabWin);

  const wc = tabWin.webContents;
  wc.loadFile(path.join(__dirname, "loading.html"));
  try {
    if (isLocal) await waitForBackend(tabWin);
    else await waitForEndpoint(tabWin, target);
    if (!tabWin.isDestroyed()) loadOrPark(wc, target);
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

// ── The buddy's window (#3831) ──

/**
 * The buddy's own window: made once, created only while "Buddy status" is on for this machine
 * (`buddy:enabled` in the shell's store), on the default session so it carries the shell's sign-in,
 * loading the buddy route from the gateway this shell started. A restart moves it with every other
 * web contents (`followRestartedGateway`). It hears six named sends from its own page and no other
 * (`registerBuddyIpc`), and a route it opens goes to the main window through `deepLink`.
 */
function startBuddy() {
  if (buddy) return buddyPresence.start();
  const { screen } = require("electron");
  buddy = makeBuddyWindow({
    BrowserWindowCtor: BrowserWindow,
    screen,
    store: shellStore,
    preload: path.join(__dirname, "buddyPreload.js"),
    // A drag that moves the window, and where it settles, are the page's to hear: its machine
    // docks or floats with it.
    onPlacement: (where) => pushToBuddy(buddy?.window, "placement", where),
    // Where the orb and its card's area are in the window (#5216): the page draws them there.
    onLayout: (layout) => pushToBuddy(buddy?.window, "layout", layout),
    log: (msg) => console.warn(`desktop: ${msg}`),
  });
  registerBuddyIpc({
    ipcMain,
    buddy,
    isBuddy: (sender) => Boolean(buddy.window) && !buddy.window.isDestroyed() && sender === buddy.window.webContents,
    openRoute: (hash) => deepLink(hash),
    openMenu: () => openBuddyMenu(),
    log: (msg) => console.warn(`desktop: ${msg}`),
  });
  buddyPresence = makeBuddyPresence({
    buddy,
    store: shellStore,
    // Nothing loads the buddy while the windows' sign-in is withheld: it starts again once it is back.
    url: () => buddyUrl(),
    prepare: guardBuddyContents,
    onStart: () => absence.start(),
    onStop: () => absence.stop(),
    log: (msg) => console.log(`desktop: ${msg}`),
  });
  return buddyPresence.start();
}

/**
 * Follow Buddy status as the gateway says it (`buddyPolicy.js`): the window exists only while its
 * surface is on, and the tray's stills and the dock follow theirs. With no answer (no gateway yet,
 * or one too old to serve the policy) the last one kept stands.
 */
function followBuddyPolicy(policy) {
  if (!policy) return;
  keepPolicy(shellStore, policy);
  trayPresence.setBuddyState({ enabled: policy.show, hidden: Boolean(buddyHide?.hidden), tray: policy.surfaces.tray, dock: policy.surfaces.dock });
  if (!buddyPresence) return;
  if (policy.surfaces.window) buddyPresence.start();
  else buddyPresence.stop();
}

/**
 * The menu bar's Show Buddy: the gateway's `buddy.show`, written as Settings writes it, so every
 * window and the phone follow one value; the gateway's `buddy_policy_changed` rings the doorbell,
 * whose read brings the change here too. Read again at once so the menu does not wait for the ring.
 * Only with no gateway to write to does the switch change what this machine keeps, alone.
 */
async function setBuddyStatus(on) {
  const wrote = await postGateway("/api/config/personalclaw", showWrite(on), {}, { method: "PATCH" });
  if (wrote) {
    followBuddyPolicy(readPolicy(await getGateway("/api/buddy/policy")));
    return;
  }
  if (buddyPresence) buddyPresence.setEnabled(on);
  else if (shellStore) shellStore.setItem(ENABLED_KEY, on ? "1" : "0");
  syncBuddyToTray();
}

/** The tray's checkbox at start, before the gateway answers: the last Buddy status kept. */
function syncBuddyToTray() {
  const kept = cachedPolicy(shellStore);
  trayPresence.setBuddyState({ enabled: kept.show, hidden: Boolean(buddyHide?.hidden), tray: kept.surfaces.tray, dock: kept.surfaces.dock });
}

/** The buddy's page stays on the gateway it came from and opens no window of any kind. */
function guardBuddyContents(win) {
  const wc = win.webContents;
  // Each load of the page hears where the window is, where the orb and its card's area are in it,
  // and whether an app window is full screen.
  wc.on("did-finish-load", () => {
    pushToBuddy(win, "placement", buddy?.placement?.mode === "floating" ? "floating" : "docked");
    pushToBuddy(win, "layout", buddy?.layout);
    tellBuddyFullScreen();
    pushToBuddy(win, "away", absence.away);
    pushToBuddy(win, "hidden", Boolean(buddyHide?.hidden));
  });
  const onGateway = (url) => {
    try {
      return new URL(url).origin === originOf(localGatewayUrl);
    } catch {
      return false;
    }
  };
  const stay = (event, url) => {
    if (onGateway(url)) return;
    event.preventDefault();
    console.warn(`desktop: the buddy's window may not leave its gateway (${String(url).slice(0, 80)})`);
  };
  wc.on("will-navigate", stay);
  wc.on("will-redirect", stay);
  wc.setWindowOpenHandler(() => ({ action: "deny" }));
}

// ── personalclaw:// links ──

// Required here rather than at the top: `deepLink()` above runs only on menu-bar clicks, long after
// this file has loaded, and keeping the new lines below the ones the docs cite by number keeps
// those citations true (`test/connectSeam.test.js`).
const { hashAssignmentScript, makeWindowDeepLinkInbox, registerDeepLinks } = require("./deepLinks");

/**
 * Links from other apps and pages wait here until the dashboard can take them. A link is
 * delivered only to the active tab's dashboard, on the gateway the window is pointed at: never to
 * the loading page, the gateway switcher, another of the gateway's pages or another origin. It
 * moves the page to a route and nothing more (`deepLinks.js`).
 */
const deepLinkInbox = makeWindowDeepLinkInbox({
  contents: () => (mainWindow && !mainWindow.isDestroyed() ? mainWindow.webContents : null),
  gatewayUrl: () => activeUrl || localGatewayUrl,
  show: () => showMainWindow(),
  log: (msg) => console.log(`desktop: ${msg}`),
});

// ── The Go menu ──

// ⌘N and ⌘1 to ⌘9, which a browser keeps for itself, as menu accelerators. Each hands the tab in
// view one command id from its keymap, on the gateway the window is pointed at only (`goMenu.js`).
const { activeTabContents, goMenuTemplate, makeGoCommandSender } = require("./goMenu");

const sendGoCommand = makeGoCommandSender({
  contents: () => activeTabContents(BaseWindow.getFocusedWindow(), mainWindow),
  gatewayUrl: () => activeUrl || localGatewayUrl,
});

// ── The app's own update ──

// Required here, below the lines the docs cite by number (`test/connectSeam.test.js`).
const { compareVersions, makeAppUpdater, readUpdatePolicy, registerAppUpdateIpc } = require("./appUpdate");

/** The app's own update (`appUpdate.js`): made once the shell's store is open, started once the
 *  window and its gateway are up. */
let appUpdater = null;
/** Whether `startAppUpdater` has run, and whether the copy an update replaced went to the Trash. */
let appUpdaterStarted = false;
let previousTrashed = false;

// ── A gateway this app found: whether it is used, and what the updater hears of it ──

const { behindSentence, unusableSentence } = require("./gatewayLost");

/** *version*, a gateway's reported version, as a sentence, the updater's state or a log may carry
 *  it: trimmed, as the compare reads it (`compareVersions`), when it then has a version's shape
 *  (`homeGateway.VERSION_SHAPE`), else "". */
function shapedVersion(version) {
  const text = typeof version === "string" ? version.trim() : "";
  return homeGateway.VERSION_SHAPE.test(text) ? text : "";
}

/**
 * Whether this app may use *gateway*, one it found and proved (`homeGateway.reach`), asked before
 * anything is sent to it: `{ok: true}`, or `{ok: false, state, sentence}` for the This computer row.
 * The app needs its gateway at its own version or newer, with the window's door: an older one is
 * `behind`, and one without the door that isn't older is `unusable`. A version this app can't
 * compare with its own is used only on a gateway with the door, and logged. Whether the home's
 * secret may go there is the sign-in's own question (`homeGateway.signIn`), asked after this one.
 */
function mayUseFoundGateway(gateway) {
  const mine = app.getVersion();
  // Compared as reported; only what is said or logged is shaped.
  const order = compareVersions(gateway.version, mine);
  const theirs = shapedVersion(gateway.version);
  if (order === -1) return { ok: false, state: "behind", sentence: behindSentence({ port: gateway.port, version: theirs, app: mine }) };
  if (!gateway.windowDoor) {
    return { ok: false, state: "unusable", sentence: unusableSentence({ port: gateway.port, version: theirs, app: mine, comparable: order !== null }) };
  }
  if (order === null) {
    console.log(`desktop: this home's gateway on port ${gateway.port} reports ${theirs ? `version ${theirs}` : "a version this app can't read"}, which can't be compared with this app's; it is used`);
  }
  return { ok: true };
}

/** The start the row last said a refusal for (`setBehind`): the chooser opens once a start. */
let refusedStart = "";

/**
 * *gateway*, one this app found and proved, isn't used (`mayUseFoundGateway`): nothing is signed in
 * to it, no window loads it, nothing registers there and no doorbell rings. The This computer row
 * says why, with Try Again, and the chooser opens the first time for that start, unless the app's
 * start says it itself (*show* false).
 */
function setBehind(gateway, verdict, { show = true } = {}) {
  const key = startKey(startOf(gateway));
  const first = key !== refusedStart;
  refusedStart = key;
  startFailure = { behind: true };
  sawGateway("none", null);
  setLocalState(verdict.state, verdict.sentence);
  if (first) console.log(`desktop: this home's gateway on port ${gateway.port} (pid ${gateway.pid}) isn't used: ${verdict.state}`);
  if (first && show) showNotConnected();
}

/**
 * A gateway this app found gave it a session without a pair: it isn't used. The session is signed
 * out there with its own credential and goes in no cookie store, and the row says the build can't be
 * used. Resolves false, for the caller's own answer.
 */
async function refusePairless(gateway, session) {
  const ended = await signOutAtPort(session, gateway.port, { windowDoor: Boolean(gateway.windowDoor) });
  // One that the sign-out doesn't end there is signed out at the next gateway this app uses
  // (`signOutKept`).
  if (!ended) keepToSignOut(session);
  console.warn(
    `desktop: the gateway on port ${gateway.port} signed this app in without a pair; that session ${ended ? "was ended" : "is kept to be signed out later"} and the gateway isn't used`
  );
  const comparable = compareVersions(gateway.version, app.getVersion()) !== null;
  setBehind(gateway, { state: "unusable", sentence: unusableSentence({ port: gateway.port, version: shapedVersion(gateway.version), app: app.getVersion(), comparable }) });
  return false;
}

/** End *session* at the gateway on *port*, 127.0.0.1, with that session's own credential
 *  (`signOutRequest`): the session ends itself. Logs the status only. Resolves whether it answered 200. */
function signOutAtPort(session, port, { windowDoor }) {
  return new Promise((resolve) => {
    if (!session || !session.token) return resolve(false);
    const { path: route, headers } = signOutRequest(session, port, { windowDoor });
    const req = http.request(
      { host: "127.0.0.1", port, path: route, method: "POST", agent: false, timeout: 5000, headers: { "Content-Length": "0", ...headers } },
      (res) => {
        res.on("data", () => {});
        res.on("end", () => {
          if (res.statusCode !== 200) console.warn(`desktop: a sign-out at port ${port} → HTTP ${res.statusCode}`);
          resolve(res.statusCode === 200);
        });
      }
    );
    req.on("timeout", () => req.destroy());
    req.on("error", () => resolve(false));
    req.end();
  });
}

/** The gateway in use, as the app last proved it, told to the updater (`noteGateway`): *kind* `own`,
 *  `adopted` with *gateway* the proven answer, or `none`. A gateway in use also lets the copy an
 *  update replaced go to the Trash. */
function sawGateway(kind, gateway) {
  if (!appUpdater) return;
  appUpdater.noteGateway({ kind, version: gateway ? shapedVersion(gateway.version) : "" });
  if (kind !== "none") trashPreviousOnce();
}

/** Once the updater has started and a gateway is in use, in this run: the copy an update replaced
 *  goes to the Trash, once. */
function trashPreviousOnce() {
  if (previousTrashed || !appUpdaterStarted || !appUpdater) return;
  previousTrashed = true;
  appUpdater.trashPrevious();
}

/** Run one program and resolve what it printed (its stderr with `{stream: "stderr"}`, where
 *  `codesign -d` writes): the checks and the copy an update makes on macOS. */
function runProgram(cmd, args, { stream = "stdout" } = {}) {
  return new Promise((resolve, reject) => {
    execFile(cmd, args, { timeout: 10 * 60_000, maxBuffer: 4 * 1024 * 1024 }, (err, stdout, stderr) =>
      err ? reject(err) : resolve(stream === "stderr" ? stderr : stdout)
    );
  });
}

function makeThisAppsUpdater() {
  return makeAppUpdater({
    current: app.getVersion(),
    platform: process.platform,
    arch: process.arch,
    isPackaged: app.isPackaged,
    execPath: process.execPath,
    appImage: process.env.APPIMAGE || "",
    // The pin, unless the home is served by a gateway the app found running: its own install pins it.
    // The kind of gateway, not what the row says (a signed-out row is still one kind or the other).
    policy: () => readUpdatePolicy({ home: PERSONALCLAW_HOME, ownGateway: !adoptedGateway && !["served", "behind", "unusable"].includes(localState.state) }),
    // The app's own folder, which the system's temp cleaning never empties under a waiting update
    // and no other account can make: the updater keeps it 0700.
    workDir: path.join(app.getPath("userData"), "app-update"),
    run: runProgram,
    quit: () => app.quit(),
    // Asked as the quit's last step, once the new copy is in place: the start that follows is on it.
    relaunch: (program) =>
      app.relaunch({ execPath: program, args: process.argv.slice(1).filter((a) => !/^personalclaw:/i.test(a)) }),
    trash: (p) => shell.trashItem(p),
    request: require("https").request,
    store: shellStore,
    ownsGateway: () => gatewayWatch.kind === "own",
    log: (msg) => console.log(`desktop: ${msg}`),
    onState: (state) => pushToBridged(BaseWindow.getAllWindows(), IPC_CHANNELS.appUpdateState, state),
  });
}

/** Once the window and this home's gateway are up: say how an update the last copy started ended
 *  (Settings › Updates opens on one that failed), keep the daily check, and move the copy an update
 *  replaced to the Trash, which waits until the new one has its gateway. */
function startAppUpdater() {
  if (!appUpdater) return;
  const last = appUpdater.start();
  appUpdaterStarted = true;
  if (last && !last.ok) deepLinkInbox.receive("personalclaw://settings/updates");
  // Whether a gateway is up, not what the row says (a signed-out row is still a gateway that runs).
  // A start that lands on the chooser moves it once a gateway is in use (`sawGateway`).
  if (gatewayWatch.kind && !gatewayGone) trashPreviousOnce();
}

/** The app menu's Check for Updates…: check now, and show the answer in Settings › Updates. */
function checkForAppUpdates() {
  appUpdater?.check({ asked: true });
  deepLinkInbox.receive("personalclaw://settings/updates");
}

/** The app's menu. On macOS it is the system's, with Check for Updates… under About. */
function appMenuTemplate() {
  if (process.platform !== "darwin") return [{ role: "appMenu" }];
  return [
    {
      label: app.name,
      submenu: [
        { role: "about" },
        { label: "Check for Updates…", click: () => checkForAppUpdates() },
        { type: "separator" },
        { role: "services" },
        { type: "separator" },
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    },
  ];
}

/** Elsewhere, Check for Updates… is under Help. */
function helpMenuTemplate() {
  if (process.platform === "darwin") return [];
  return [{ label: "Help", submenu: [{ label: "Check for Updates…", click: () => checkForAppUpdates() }] }];
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
  // Before ready: macOS hands a link that launched the app to `open-url` early in the start. This
  // also offers a held link again each time a page finishes loading.
  registerDeepLinks({
    app,
    inbox: deepLinkInbox,
    isPackaged: app.isPackaged,
    argv: process.argv,
    log: (msg) => console.log(`desktop: ${msg}`),
  });

  app.whenReady().then(async () => {
    // Before anything can talk to the gateway: every request to it carries this sign-in.
    localSignIn = makeLocalSignIn({
      cookies: session.defaultSession.cookies,
      log: (msg) => console.warn(`desktop: ${msg}`),
    });
    watchWindowTraffic(session.defaultSession);
    const appMenu = Menu.buildFromTemplate([
      ...appMenuTemplate(),
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
        // The switcher's entrance. One menu item rather than a submenu that mirrors
        // the registry: the dialog IS the list, and a menu rebuilt on every pairing would be a
        // second place the active gateway is drawn, free to disagree with the first.
        label: "Gateway",
        submenu: [
          { label: "Gateways…", accelerator: "CmdOrCtrl+Shift+G", click: () => openConnectDialog() },
        ],
      },
      goMenuTemplate(sendGoCommand),
      { role: "windowMenu" },
      ...helpMenuTemplate(),
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
    // The login item's bridge half, so Settings can drive the same toggle the
    // tray checkbox drives. Its own channels, not the capability vocabulary's. The
    // fourth argument is what keeps the two surfaces agreeing: a Settings flip
    // re-renders the tray checkbox instead of leaving it stale until restart.
    registerLoginItemIpc(ipcMain, loginItem, IPC_CHANNELS, syncLoginItemToTray);
    // The `native` notification target's actuator. Registered before any window
    // loads, like the rest: the first gateway note can arrive as soon as the WS opens.
    registerNativeNotificationIpc(ipcMain, nativeNotifications, IPC_CHANNELS);
    // A page's request to open a page in the system browser, answered with whether it opened.
    registerSystemBrowserIpc(ipcMain, systemBrowser, IPC_CHANNELS);
    // This machine's buddy rows in Settings, for the app's own bridged windows only (#3901).
    registerBuddySettingsIpc({
      ipcMain,
      channels: IPC_CHANNELS,
      buddy: () => buddy,
      store: () => shellStore,
      isAppWindow: (sender) => BaseWindow.getAllWindows().some((w) => w._pcBridgeAttached === true && w.webContents === sender),
      log: (msg) => console.warn(`desktop: ${msg}`),
    });
    // The app's own update, for the same windows only: Settings › Updates › This app and the
    // System popover's row.
    registerAppUpdateIpc({
      ipcMain,
      channels: IPC_CHANNELS,
      updater: () => appUpdater,
      isAppWindow: (sender) => BaseWindow.getAllWindows().some((w) => w._pcBridgeAttached === true && w.webContents === sender),
      log: (msg) => console.warn(`desktop: ${msg}`),
    });

    // The shell's own storage scope. Opened before any window so the startup decision below
    // has the registry, and non-fatal by construction: a corrupt or unreadable store degrades to
    // spawn-local with a warning rather than taking the app down with it.
    shellStore = openShellStore({ home: PERSONALCLAW_HOME, log: (msg) => console.warn(`desktop: ${msg}`) });
    // The app opens in the owner's theme: `themeSource` from the stored appearance before any
    // window, or the loading page, is shown; nothing stored follows the system.
    const launched = launchAppearance({
      stored: shellStore.getItem(APPEARANCE_KEY),
      systemDark: nativeTheme.shouldUseDarkColors,
    });
    nativeTheme.themeSource = launched.themeSource;
    windowAppearance = makeWindowAppearance({
      nativeTheme,
      store: shellStore,
      canvas: launched.canvas,
      focusedAppWindow: () => focusedAppWindow(allWindows),
    });
    ipcMain.on(IPC_CHANNELS.appearanceChanged, (e, payload) => {
      windowAppearance.changed(e.sender, payload, (s) => appWindowOfSender(allWindows, s));
    });
    // Before any window loads one: the gateways the owner added are the loopback origins it may,
    // and no earlier version's localhost window cookie is left.
    syncPaired();
    await purgeStaleLocalCookies(session.defaultSession);
    makeHide(shellStore);
    appUpdater = makeThisAppsUpdater();
    // A locked screen or sleep is away at once; unlocking or waking reads the idle time again.
    const { powerMonitor } = require("electron");
    for (const e of ["lock-screen", "suspend"]) powerMonitor.on(e, () => absence.lock());
    for (const e of ["unlock-screen", "resume"]) powerMonitor.on(e, () => absence.unlock());
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
    syncBuddyToTray();
    const win = createWindow();
    // Never a blank window while this home's gateway is looked for or started.
    win.webContents.loadFile(path.join(__dirname, "loading.html"));
    // The Linux tray's stills follow the system's light or dark appearance.
    nativeTheme.on("updated", () => trayPresence.appearanceChanged());
    // A window on the loading or offline page follows the system's switch too.
    nativeTheme.on("updated", () => paintModelessWindows());

    // This home's running gateway when one serves it, else the app's own. One that cannot be
    // brought up is said on the Gateways window's "This computer" row (`showLoadingThenConnect`).
    await bringUpLocalGateway();
    // Counts need `localGatewayUrl`, so the doorbell rings once the gateway is up, before the
    // registration. With no gateway the menu simply reads "not connected".
    startPresenceRefresh();
    // Needs localGatewayUrl from the READY line, so it follows the gateway start. A
    // failure here leaves the gateway reporting "not connected" — degraded but
    // honest — and never blocks the window.
    await registerWithGateway();

    // ── spawn-local vs connect ──
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
    startBuddy();
    startAppUpdater();

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
 * Graceful quit.
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
  windowAppearance.flush();
  if (shutdownComplete) return;
  event.preventDefault();

  // Release the chord and take the indicator down before the tray is torn out from
  // under it — a quit that left "● Listening" as the last thing drawn would be the
  // one moment the indicator is guaranteed to be lying.
  pushToTalk.unbind();
  pushToTalk.clearCapturing();
  stopPresenceRefresh();
  absence.stop();
  buddyHide?.stop();
  appUpdater?.stop();
  // A pending reachability re-check would otherwise fire during teardown and call `loadURL` on a
  // window that is being destroyed.
  cancelReachProbe();
  // The windows are going away. Hidden first, so none of them draws "You're signed out" for the
  // moment between the sign-out below and the gateway stopping.
  for (const win of BaseWindow.getAllWindows()) {
    if (!win.isDestroyed()) win.hide();
  }

  // The whole quit has one deadline, whatever started it (`quitSequence.js`): a SIGTERM's quit is
  // followed by a SIGKILL that never reaches the gateway's own group. The asks go together, each
  // held to its share; then the gateway is stopped at once, with the grace that is left.
  quitWithin({
    asks: [
      (ms) => unregisterFromGateway(ms),
      // End this start's sign-in while its gateway can still be asked to, so quitting leaves no
      // live owner session behind in Settings → Devices that nothing holds.
      async (ms) => {
        try {
          const held = localSignIn ? await localSignIn.release() : null;
          if (held) await signOutLocalSession(held, ms);
          await signOutKept(ms);
        } catch (err) {
          console.warn(`desktop: sign-out on quit failed: ${err.message}`);
        }
      },
    ],
    stop: (budget) => stopGateway(budget),
  })
    .catch((err) => console.warn(`gateway shutdown failed: ${err.message}`))
    .finally(() => {
      trayPresence.destroy();
      // Everything the app ran has stopped, so a new copy an update staged takes this one's place
      // now, and the app is asked to start again (`appUpdate.js`). A download still under way is let
      // go, its disk image detached.
      appUpdater?.abandon();
      appUpdater?.swapIfStaged();
      shutdownComplete = true;
      app.quit();
    });
});

// A shell that exits without its quit having stopped the gateway (`app.exit`, a quit cut short)
// takes the gateway's group with it: that group is the gateway's own, so nothing else would end
// it. Synchronous, as an `exit` handler must be. A SIGKILLed shell runs nothing, which is why the
// quit itself is held to a deadline.
process.on("exit", () => {
  if (!liveGatewayGroup) return;
  try {
    process.kill(-liveGatewayGroup, "SIGKILL");
  } catch {
    // Already gone.
  }
});

app.on("window-all-closed", () => {
  // macOS keeps running with no windows ONLY because the menu-bar item is still there
  // to bring one back. With no tray, staying alive is the phantom state.
  if (shouldQuitOnAllWindowsClosed({ platform: process.platform, trayAvailable: trayPresence.available })) {
    app.quit();
  }
});
