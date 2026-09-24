/**
 * The connect dialog and multi-gateway switcher (COMPANION-APPS T4.1 + T4.4, `CA-8`).
 *
 * The window is a modal `BrowserWindow` loading a STATIC local file. Nothing about a gateway is
 * interpolated into that document: `connectDialog.html` ships with no placeholders, asks the main
 * process for the list over IPC, and builds every row with `textContent`. That is deliberate —
 * labels and URLs in the registry are attacker-influenceable in the tampering case this atom has
 * to survive, and the existing `renameCurrentTab` pattern in `main.js` (a `data:` URL with
 * hand-escaped interpolation) is exactly the shape not to copy for untrusted strings.
 *
 * The dialog's preload (`connectPreload.js`) exposes six invokes and nothing else. It carries no
 * capability surface: the switcher has no business reaching a microphone.
 */

const path = require("path");

const {
  LOCAL_ENDPOINT_ID,
  HEALTH_UNKNOWN,
  HEALTH_REACHABLE,
  HEALTH_UNREACHABLE,
  HEALTH_TIMEOUT,
  HEALTH_NEEDS_PAIRING,
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
});

/**
 * One health state → what the row says, in words a person can act on.
 *
 * 🔑 `unknown` IS NOT `unreachable`. A row that has never been probed and a row that was probed
 * and did not answer are two different facts, and telling the user the second when the first is
 * true is how a switcher gets blamed for an outage it invented. Same for `needs_pairing`, which is
 * the ONE state that means "act", versus `unreachable`, which means "wait".
 */
function healthCopy(status) {
  switch (status) {
    case HEALTH_REACHABLE:
      return { tone: "ok", text: "Reachable", action: "" };
    case HEALTH_UNREACHABLE:
      return { tone: "warn", text: "Not answering", action: "" };
    case HEALTH_TIMEOUT:
      return { tone: "warn", text: "Timed out", action: "" };
    case HEALTH_NEEDS_PAIRING:
      return { tone: "act", text: "Needs pairing again", action: "pair" };
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
function describeRow(row, { activeId, health = {}, localBaseUrl = "" } = {}) {
  const isLocal = row.id === LOCAL_ENDPOINT_ID || row.kind === "local";
  const url = isLocal ? localBaseUrl || row.base_url : row.base_url;
  const status = (health[row.id] && health[row.id].status) || HEALTH_UNKNOWN;
  const copy = healthCopy(status);
  return {
    id: row.id,
    label: row.label || url || row.id,
    url: url || "",
    /** `local` rows are the gateway this shell spawned; they cannot be forgotten or re-paired. */
    kind: isLocal ? "local" : "remote",
    active: row.id === activeId,
    removable: !isLocal,
    status,
    statusText: copy.text,
    statusTone: copy.tone,
    statusAction: copy.action,
    version: (health[row.id] && health[row.id].version) || "",
    /** Present only when the row has no URL at all — distinct from an unreachable one. */
    missingUrl: !url,
  };
}

/** Row list → the payload `connect:list` answers with. */
function describeList({ registry, activeId, health, localBaseUrl, warnings = [], storeStatus = "", storeSafe = true, readOnly = false }) {
  return {
    activeId: activeId || registry.active || "",
    rows: (registry.endpoints || []).map((row) => describeRow(row, { activeId: activeId || registry.active, health, localBaseUrl })),
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

  function open(parent) {
    if (win && !win.isDestroyed()) {
      win.focus();
      return win;
    }
    win = new BrowserWindowCtor({
      width: 560,
      height: 560,
      resizable: true,
      minimizable: false,
      useContentSize: true,
      parent: parent || undefined,
      modal: Boolean(parent),
      title: "Gateways",
      backgroundColor: "#0f1117",
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

  function close() {
    if (win && !win.isDestroyed()) win.close();
    win = null;
  }

  /** Register the six invokes. Each one is a thin adaptor over `handlers`, which `main.js` owns. */
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
  }

  return { open, close, registerIpc, get window() { return win; } };
}

module.exports = { CONNECT_CHANNELS, healthCopy, describeRow, describeList, makeConnectDialog };
