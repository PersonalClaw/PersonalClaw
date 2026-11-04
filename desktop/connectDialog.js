/**
 * The connect dialog and multi-gateway switcher.
 *
 * The window is a modal `BrowserWindow` loading a STATIC local file. Nothing about a gateway is
 * interpolated into that document: `connectDialog.html` ships with no placeholders, asks the main
 * process for the list over IPC, and builds every row with `textContent`. That is deliberate —
 * labels and URLs in the registry are attacker-influenceable in the tampering case this change has
 * to survive, and the existing `renameCurrentTab` pattern in `main.js` (a `data:` URL with
 * hand-escaped interpolation) is exactly the shape not to copy for untrusted strings.
 *
 * The dialog's preload (`connectPreload.js`) exposes ten invokes and one event, `changed`, and
 * nothing else. It carries no capability surface: the switcher has no business reaching a microphone.
 *
 * When nothing serves the app's windows (its own gateway did not start, a gateway stopped), the app
 * opens a smaller window instead, the chooser (`gatewayChooser.html`, on the same preload): use this
 * home's gateway, when one is running and proves it serves the home; add a gateway, which is the
 * Gateways window; or try again. Neither window is ever a sheet. A sheet holds the window it hangs
 * from, and anything else that window shows as a sheet waits behind it: the Gateways window used to
 * be one, and behind the start-failure sheet it never showed.
 */

const path = require("path");

const {
  LOCAL_ENDPOINT_ID,
  HEALTH_UNKNOWN,
  HEALTH_REACHABLE,
  HEALTH_UNREACHABLE,
  HEALTH_TIMEOUT,
  HEALTH_REFUSED,
  HEALTH_SIGNED_OUT,
  HEALTH_NOT_A_GATEWAY,
  HEALTH_HTTP_ERROR,
  HEALTH_REDIRECTED,
  HEALTH_REFUSED_BY_POLICY,
} = require("./connectMode");

/** The dialog's IPC vocabulary. Its own namespace, not the capability bridge's. */
const CONNECT_CHANNELS = Object.freeze({
  list: "pclaw:connect:list",
  prepare: "pclaw:connect:prepare",
  confirm: "pclaw:connect:confirm",
  switch: "pclaw:connect:switch",
  forget: "pclaw:connect:forget",
  refresh: "pclaw:connect:refresh",
  start: "pclaw:connect:start",
  /** Use this home's running gateway, which the app did not start (`{remember}`: every time). */
  use: "pclaw:connect:use",
  /** Whether a running gateway this home's own start finds is used without asking (`{on}`). */
  autoUse: "pclaw:connect:auto-use",
  /** The chooser's Add a Gateway…: the Gateways window, where one is added. */
  gateways: "pclaw:connect:gateways",
  /** Main → dialog: the list changed (the local gateway came up, stopped, or was found), so read it again. */
  changed: "pclaw:connect:changed",
});

/** The "This computer" row's states, as `main.js` keeps them (`localState`). */
const LOCAL_STATES = Object.freeze(["starting", "own", "found", "adopted", "stopped", "served"]);

/** The reserved row for this computer's gateway, for a registry that has none yet (a first start
 *  that failed): the row is where a gateway is started here, so it is always there. */
const LOCAL_ROW = Object.freeze({ id: LOCAL_ENDPOINT_ID, label: "This computer", base_url: "", kind: "local", device_session_ref: "" });

/**
 * The "This computer" row as this computer's gateway stands: what it says and what it offers.
 *
 * - `own`: the gateway this app started.
 * - `found`: the app's own start failed, and this home's gateway, started outside the app, is
 *   running and proved it serves the home. Use it.
 * - `adopted`: that gateway, in use; the app never stops it. Used without asking (`autoUse`), it
 *   offers to ask again.
 * - `starting`: on its way up.
 * - `stopped`: none runs, and the sentence says why. Start a gateway here.
 * - `served`: a gateway serves this home that the app cannot sign in to, and the sentence says
 *   which and why. Try again, which looks for it again: a second gateway on the home would only be
 *   refused.
 */
function describeLocal(local = {}) {
  const state = LOCAL_STATES.includes(local && local.state) ? local.state : "starting";
  const sentence = local && typeof local.sentence === "string" ? local.sentence : "";
  switch (state) {
    case "own":
      return { state, badge: "started by this app", detail: "", action: null };
    case "found":
      return { state, badge: "already running", statusText: "Running, not used yet", statusTone: "idle", detail: sentence, action: { id: "use", label: "Use It" } };
    case "adopted":
      return {
        state,
        badge: "already running",
        detail: local && local.autoUse
          ? "Not started by this app, and used without asking when the app's own gateway doesn't start. Quitting PersonalClaw leaves it running."
          : "Not started by this app. Quitting PersonalClaw leaves it running.",
        action: null,
        option: local && local.autoUse ? { id: "ask", label: "Ask Each Time" } : null,
      };
    case "stopped":
      return { state, badge: "", statusText: "Not running", statusTone: "warn", detail: sentence, action: { id: "start", label: "Start a Gateway Here" } };
    case "served":
      return { state, badge: "", statusText: "Can't sign in to it", statusTone: "bad", detail: sentence, action: { id: "start", label: "Try Again" } };
    default:
      return { state: "starting", badge: "", statusText: "Starting…", statusTone: "idle", detail: "", action: null };
  }
}

/**
 * One health state → what the row says, in words a person can act on.
 *
 * 🔑 `unknown` IS NOT `unreachable`. A row that has never been probed and a row that was probed
 * and did not answer are two different facts, and telling the user the second when the first is
 * true is how a switcher gets blamed for an outage it invented. Same for `signed_out`, which is
 * the ONE state that means "act" — the gateway signed this app out, and its sentence (on the row,
 * and in the window) says how to sign back in — versus `unreachable`, which means "wait". A health
 * check answered 401/403 (`refused`) is not a sign-in problem at all: a PersonalClaw gateway never
 * refuses it, so pairing again would not help, and the row does not say it would.
 */
function healthCopy(status) {
  switch (status) {
    case HEALTH_REACHABLE:
      return { tone: "ok", text: "Reachable", action: "" };
    case HEALTH_UNREACHABLE:
      return { tone: "warn", text: "Not answering", action: "" };
    case HEALTH_TIMEOUT:
      return { tone: "warn", text: "Timed out", action: "" };
    case HEALTH_SIGNED_OUT:
      return { tone: "act", text: "Signed out", action: "" };
    case HEALTH_REFUSED:
      return { tone: "bad", text: "Refused the connection check", action: "" };
    case HEALTH_NOT_A_GATEWAY:
      return { tone: "bad", text: "Answered, but not a PersonalClaw gateway", action: "" };
    case HEALTH_REDIRECTED:
      return { tone: "bad", text: "Redirected somewhere else — not followed", action: "" };
    case HEALTH_HTTP_ERROR:
      return { tone: "warn", text: "Answered with an error", action: "" };
    case HEALTH_REFUSED_BY_POLICY:
      return { tone: "bad", text: "Address the app will not connect to", action: "" };
    case HEALTH_UNKNOWN:
    default:
      return { tone: "idle", text: "Not checked yet", action: "" };
  }
}

/**
 * Registry row + health → the display model the renderer draws.
 *
 * Pure, so `test/connectDialog.test.js` can assert what each state renders without an Electron
 * window. The renderer receives these objects and never re-derives any of it.
 */
function describeRow(row, { activeId, health = {}, localBaseUrl = "", local } = {}) {
  const isLocal = row.id === LOCAL_ENDPOINT_ID || row.kind === "local";
  const url = isLocal ? localBaseUrl || row.base_url : row.base_url;
  const status = (health[row.id] && health[row.id].status) || HEALTH_UNKNOWN;
  const copy = healthCopy(status);
  const here = isLocal && local ? describeLocal(local) : null;
  // A local row with no gateway behind it (starting, found, stopped, served) says that, not a probe's word.
  const running = !here || here.state === "own" || here.state === "adopted";
  return {
    id: row.id,
    label: row.label || url || row.id,
    url: url || "",
    /** `local` rows are the gateway this shell spawned; they cannot be forgotten or re-paired. */
    kind: isLocal ? "local" : "remote",
    active: row.id === activeId,
    removable: !isLocal,
    status,
    statusText: running ? copy.text : here.statusText,
    statusTone: running ? copy.tone : here.statusTone,
    statusAction: copy.action,
    /** The gateway's own sentence for this row's state, when it gave one (a sign-out: why, when,
     *  and how to sign back in), or for the local row, why no gateway serves it. Untrusted text,
     *  already cleaned; rendered with `textContent`. */
    statusDetail: (here && here.detail) || (health[row.id] && health[row.id].message) || "",
    version: running ? (health[row.id] && health[row.id].version) || "" : "",
    /** Present only when the row has no URL at all — distinct from an unreachable one. */
    missingUrl: !url,
    /** The local row's own badge (`started by this app`, `already running`), or "". */
    localBadge: here ? here.badge : "",
    /** The local row's state (`describeLocal`), or "" for a paired row. */
    localState: here ? here.state : "",
    /** What the local row offers in place of Switch while no gateway serves it, or null. */
    localAction: here ? here.action : null,
    /** A quiet second offer on the local row (an adopted gateway's Ask Each Time), or null. */
    localOption: (here && here.option) || null,
    /** Whether Switch can open this row: a local row only while a gateway serves it. */
    switchable: running,
  };
}

/**
 * What the chooser says and offers while nothing serves the app's windows, from the "This computer"
 * row's state: why (its sentence), Use the Gateway Already Running only while one is `found`, and
 * Add a Gateway… and Try Again always, held while a start runs. Pure, as `describeLocal` is.
 */
function describeChooser(local = {}) {
  const here = describeLocal(local);
  const busy = here.state === "starting";
  return {
    state: here.state,
    sentence: busy ? "Starting PersonalClaw's gateway…" : here.detail || "PersonalClaw's gateway isn't running.",
    use: here.state === "found",
    busy,
    /** Nothing more to choose: a gateway serves the windows. */
    settled: here.state === "own" || here.state === "adopted",
  };
}

/** Row list → the payload `connect:list` answers with. */
function describeList({ registry, activeId, health, localBaseUrl, local, warnings = [], storeStatus = "", storeSafe = true, readOnly = false }) {
  const endpoints = registry.endpoints || [];
  const rows = endpoints.some((row) => row.id === LOCAL_ENDPOINT_ID) ? endpoints : [LOCAL_ROW, ...endpoints];
  return {
    activeId: activeId || registry.active || "",
    rows: rows.map((row) => describeRow(row, { activeId: activeId || registry.active, health, localBaseUrl, local })),
    chooser: describeChooser(local),
    warnings,
    storeStatus,
    storeSafe,
    readOnly,
  };
}

/**
 * The Electron half. Everything it needs is injected so `main.js` stays the only file that knows
 * about Electron globals, and so this module is loadable (and its pure exports testable) outside
 * an Electron process.
 */
function makeConnectDialog({ BrowserWindowCtor, ipcMain, handlers, log = () => {} }) {
  let win = null;
  let chooser = null;

  /** Open the window, or bring it forward. Never a sheet, and never anyone's child: see the top. */
  function open() {
    if (win && !win.isDestroyed()) {
      win.show();
      win.focus();
      return win;
    }
    win = new BrowserWindowCtor({
      width: 560,
      height: 600,
      resizable: true,
      minimizable: false,
      useContentSize: true,
      modal: false,
      title: "Gateways",
      backgroundColor: "#0f0f0f",
      webPreferences: {
        preload: path.join(__dirname, "connectPreload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        // The dialog is a local document and must stay one. A switcher that could be navigated
        // by content would be a way to put attacker HTML inside a window holding this IPC.
        sandbox: false,
      },
    });
    win.setMenu?.(null);
    win.loadFile(path.join(__dirname, "connectDialog.html"));
    win.on("closed", () => {
      win = null;
    });
    return win;
  }

  /** Open the chooser, or bring it forward: a small window of its own, on the same preload. */
  function openChooser() {
    if (chooser && !chooser.isDestroyed()) {
      chooser.show();
      chooser.focus();
      return chooser;
    }
    chooser = new BrowserWindowCtor({
      width: 440,
      height: 290,
      resizable: false,
      minimizable: false,
      maximizable: false,
      useContentSize: true,
      modal: false,
      title: "Choose a Gateway",
      backgroundColor: "#0f0f0f",
      webPreferences: {
        preload: path.join(__dirname, "connectPreload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: false,
      },
    });
    chooser.setMenu?.(null);
    chooser.loadFile(path.join(__dirname, "gatewayChooser.html"));
    chooser.on("closed", () => {
      chooser = null;
    });
    return chooser;
  }

  function closeChooser() {
    if (chooser && !chooser.isDestroyed()) chooser.close();
    chooser = null;
  }

  /** Close both windows: a gateway serves the windows now. */
  function close() {
    if (win && !win.isDestroyed()) win.close();
    win = null;
    closeChooser();
  }

  /** Tell each open window its list changed, so it reads it again. */
  function changed() {
    for (const w of [win, chooser]) if (w && !w.isDestroyed()) w.webContents.send(CONNECT_CHANNELS.changed);
  }

  /** Register the ten invokes. Each one is a thin adaptor over `handlers`, which `main.js` owns. */
  function registerIpc(ipc = ipcMain) {
    const wrap = (name, fn) => {
      ipc.handle(name, async (_event, arg) => {
        try {
          return await fn(arg);
        } catch (err) {
          log(`connect dialog ${name} failed: ${err && err.message}`);
          // A thrown handler surfaces in the renderer as a rejected promise with the main-process
          // stack in its message. Answering a shaped refusal instead keeps internals out of a
          // window and lets the dialog say something useful.
          return { ok: false, code: "INTERNAL", message: "That did not work. See the app log." };
        }
      });
    };
    wrap(CONNECT_CHANNELS.list, () => handlers.list());
    wrap(CONNECT_CHANNELS.prepare, (arg) => handlers.prepare(String((arg && arg.input) || "")));
    wrap(CONNECT_CHANNELS.confirm, (arg) => handlers.confirm({ input: String((arg && arg.input) || ""), label: String((arg && arg.label) || "") }));
    wrap(CONNECT_CHANNELS.switch, (arg) => handlers.switchTo(String((arg && arg.id) || "")));
    wrap(CONNECT_CHANNELS.forget, (arg) => handlers.forget(String((arg && arg.id) || "")));
    wrap(CONNECT_CHANNELS.refresh, () => handlers.refresh());
    wrap(CONNECT_CHANNELS.start, () => handlers.start());
    wrap(CONNECT_CHANNELS.use, (arg) => handlers.use({ remember: (arg && arg.remember) === true }));
    wrap(CONNECT_CHANNELS.autoUse, (arg) => handlers.autoUse({ on: (arg && arg.on) === true }));
    wrap(CONNECT_CHANNELS.gateways, () => handlers.gateways());
  }

  return {
    open,
    close,
    openChooser,
    closeChooser,
    changed,
    registerIpc,
    get window() {
      return win;
    },
    get chooserWindow() {
      return chooser;
    },
  };
}

module.exports = { CONNECT_CHANNELS, LOCAL_STATES, describeLocal, describeChooser, healthCopy, describeRow, describeList, makeConnectDialog };
