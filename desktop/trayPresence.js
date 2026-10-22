/**
 * Menu-bar presence.
 *
 * The earlier tray was a static six-item menu (Show / New Tab / Merge All Windows /
 * Quit) built inline in `main.js`. This module replaces it with a LIVE one — what is
 * waiting on you, what is running now, quick capture, open dashboard, an opt-in login item,
 * quit — and pulls the logic out of the Electron glue so it can be tested without
 * launching an app.
 *
 * Three things this module exists to get right:
 *
 * 1. **ONE writer for the menu-bar title.** The shell already drives `tray.setTitle()`
 *    for the "● Listening" push-to-talk indicator. A counts badge that also called
 *    `setTitle` would be a second writer to a single surface, and whichever fired
 *    last would win — so both go through `composeTrayTitle()`, and capture ALWAYS
 *    wins (a live-microphone indicator is a privacy signal; a waiting badge can
 *    wait a second).
 *
 * 2. **Presence must degrade.** If `new Tray()` throws, or the icon file is missing
 *    or unreadable, `makeTrayPresence()` returns an object whose `available` is
 *    `false` instead of throwing. That flag is load-bearing beyond cosmetics:
 *    `main.js` hides the window on close INSTEAD of closing it, which is only safe
 *    while there is a menu-bar item to bring it back. `shouldHideOnClose()` and
 *    `shouldQuitOnAllWindowsClosed()` encode that coupling, so a tray-less run can
 *    never leave a phantom hidden window with no way back.
 *
 * 3. **Counts come from the loopback gateway**, whose two shapes are fixed:
 *    `GET /api/waiting` → `{"waiting": [item, ...], "unreadable": [...]}`, the one list of
 *    everything waiting on you every surface counts (`personalclaw.waiting`, #3888): an
 *    approval, an agent's question, a loop's question, a workflow's gate, an automation's
 *    parked action, a browser grant, a plan review and a control-bridge confirmation;
 *    `GET /api/running` → `{"running": [row, ...], "finished": [row, ...]}`, the one list of
 *    live work every reader shares (`personalclaw.running_now`, #3889): a chat's turn, a
 *    helper, a loop with its workers folded in, a workflow run, an automation's fire and a
 *    webhook's turn, each with its phase, its line, when it last changed and the route that
 *    opens it. `summarizePresence()` is tolerant of nulls and of either shape, because a
 *    failed poll must render as "0 / not connected", never as a crash in a menu builder.
 */

const path = require("node:path");

/**
 * SPA deep-link targets. The dashboard is a HASH-routed SPA (`web/src/app/App.tsx`),
 * and these are its real routes — Mission Control lists everything waiting on you, each
 * answerable there or on its own page, and every piece of live work. A running row opens the
 * route the gateway names for it (`running_now`), which `hashAssignmentScript` checks
 * against the hash grammar before the window takes it.
 */
const DEEP_LINKS = {
  dashboard: "#/dashboard",
  waiting: "#/mission-control",
  inbox: "#/inbox",
  settings: "#/settings",
  missionControl: "#/mission-control",
};

/** A tray menu row's and the tooltip's longest (`docs/design/buddy/copy.md`). */
const ROW_MAX = 56;
const TOOLTIP_MAX = 80;

/** Most rows listed inline before the menu stops growing. */
const MAX_LISTED_ROWS = 8;

/** Work a restart cut off is said at most this many times, and for this long: as the gateway
 *  keeps its own endings (`running_now.TAIL_ROWS`, `TAIL_SECS`). */
const INTERRUPTED_KEPT = 20;
const INTERRUPTED_MS = 10 * 60 * 1000;

/** What it says of work a restart cut off. */
const INTERRUPTED_LINE = "The gateway restarted while it ran.";

/**
 * What each phase is called, in the dashboard's words (`web/src/lib/workPhase.ts`'s labels;
 * `test/trayListsWhatIsRunning.test.js` holds the two equal). A row names its phase as text,
 * whatever else it says.
 */
const PHASE_WORDS = Object.freeze({
  queued: "Waiting to start",
  thinking: "Thinking",
  searching: "Searching",
  tool: "Working",
  planning: "Planning",
  delegating: "Handing off",
  streaming: "Writing",
  listening: "Listening",
  approval: "Needs your OK",
  input: "Waiting for your answer",
  done: "Done",
  error: "Stopped",
  retrying: "Retrying",
  waiting_model: "Waiting for the model",
  idle: "Nothing running",
});

/** An empty presence — what a disconnected or failed poll renders as. */
const EMPTY_PRESENCE = Object.freeze({
  waiting: 0,
  /** Each item waiting on you as its menu row, in the list's order (`waitingRowOf`). */
  waitingRows: Object.freeze([]),
  connected: false,
  listed: false,
  stale: false,
  running: [],
  finished: [],
  interrupted: [],
  /** The rows last read on one start of the gateway (`boot`), which a later start is compared
   *  with. Replaced only by a poll that read the list and knows which start it read it from. */
  basis: Object.freeze({ boot: null, rows: [] }),
});

/** A gateway stamp as the reader's clock time (`14:32`), or `""` for one that names no instant: a
 *  date-time with no zone is refused, as the dashboard refuses it (`web/src/lib/epoch.ts`). */
function clockOf(stamp) {
  if (typeof stamp !== "string" || !/(Z|[+-]\d\d:?\d\d)$/i.test(stamp)) return "";
  const at = Date.parse(stamp);
  return Number.isFinite(at) ? new Date(at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "";
}

/** One row of `GET /api/running` as the menu shows it, or null for one it cannot name. */
function rowOf(r) {
  if (!r || typeof r !== "object" || typeof r.work !== "string" || r.work === "") return null;
  const phase = String(r.phase || "");
  return {
    work: r.work,
    name: truncateLabel(String(r.name || "Work")),
    phase: PHASE_WORDS[phase] || phase,
    phaseKey: phase,
    line: truncateLabel(String(r.line || ""), 60),
    since: typeof r.since === "string" ? r.since : "",
    route: typeof r.route === "string" ? r.route : "",
    blocked: r.blocked === true,
    waiting: Array.isArray(r.waiting) ? r.waiting.length : 0,
  };
}

// ── What waits on you, one row each (#3896) ─────────────────────────────────────────────────────
//
// The tray menu lists everything waiting on the owner, in the list's order, before live work, each
// in one line (`docs/design/buddy/surfaces.md`, the room budget). A row is the item's kind and its
// text in the app's own words (`web/src/lib/waitingList.ts`, which a test holds this to), and it
// opens the item's route.

/** What each kind of waiting item is called on a card, as the app calls it. */
const WAITING_KIND_LABEL = Object.freeze({
  approval: "Approval",
  question: "Question",
  loop_question: "Loop",
  gate: "Workflow",
  park: "Automation",
  browse_grant: "Browser",
  plan_review: "Plan",
  bridge_confirmation: "Control bridge",
});

const textOf = (value) => (typeof value === "string" ? value.trim() : "");
const recordOf = (value) => (value && typeof value === "object" && !Array.isArray(value) ? value : {});

/** An item's text in its source's own words, as the app's `waitingText` reads it: a source that
 *  said nothing is named by its kind, never left empty. */
function waitingText(kind, brief) {
  const b = recordOf(brief);
  switch (kind) {
    case "approval":
      return textOf(b.tool_purpose) || textOf(b.tool) || "A tool call";
    case "question": {
      const first = recordOf(Array.isArray(b.questions) ? b.questions[0] : undefined);
      return textOf(first.question) || textOf(first.header) || "A question";
    }
    case "loop_question":
      return textOf(b.question) || "The loop asks you";
    case "gate": {
      const ask = recordOf(b.ask);
      return textOf(ask.prompt) || textOf(ask.title) || textOf(b.node_id) || "The run waits at a gate";
    }
    case "park":
      return textOf(b.question) || textOf(recordOf(b.card).blocker) || "The action stopped for you";
    case "browse_grant":
      return textOf(b.task) || "A task asks to use your browser";
    case "plan_review": {
      const step = recordOf(b.step);
      return textOf(step.title) || (textOf(b.name) ? `Launch “${textOf(b.name)}”` : "A plan waits for your review");
    }
    case "bridge_confirmation":
      return textOf(b.body) || textOf(b.action) || "A control-bridge action waits for you";
    default:
      return "";
  }
}

/** One waiting item as its menu row: its kind and its text in one line, and the route it opens. A
 *  kind the shell does not know yet is still listed, as waiting on you. */
function waitingRowOf(item) {
  const it = recordOf(item);
  const kind = textOf(it.kind);
  const text = waitingText(kind, it.brief);
  const name = WAITING_KIND_LABEL[kind];
  return { id: textOf(it.id), kind, text, label: truncateLabel(name ? `${name}: ${text}` : "Waiting on you", ROW_MAX), route: textOf(it.route) };
}

/**
 * Fold the two gateway payloads into the menu's view model.
 *
 * @param {unknown} waitingPayload body of `GET /api/waiting` (`{waiting: [...], unreadable: [...]}`)
 * @param {unknown} runningPayload body of `GET /api/running` (`{running: [...], finished: [...]}`)
 */
function summarizePresence(waitingPayload, runningPayload) {
  const connected = waitingPayload !== null && waitingPayload !== undefined;

  // `{waiting: [...]}` is the documented shape; a bare array and the other envelopes are
  // accepted so a change of envelope degrades to a count rather than to zero.
  let waiting = 0;
  let items = [];
  if (Array.isArray(waitingPayload)) items = waitingPayload;
  else if (waitingPayload && typeof waitingPayload === "object") {
    const inner = waitingPayload.waiting ?? waitingPayload.items;
    if (Array.isArray(inner)) items = inner;
    else if (Number.isFinite(waitingPayload.pending)) waiting = Number(waitingPayload.pending);
  }
  if (items.length) waiting = items.length;

  const listed = Boolean(runningPayload && typeof runningPayload === "object" && Array.isArray(runningPayload.running));
  const rows = (key) => (listed && Array.isArray(runningPayload[key]) ? runningPayload[key] : []).map(rowOf).filter(Boolean);
  return { ...EMPTY_PRESENCE, waiting, waitingRows: items.map(waitingRowOf), connected, listed, running: rows("running"), finished: rows("finished") };
}

/**
 * The presence after a poll, from the one before it.
 *
 * - **Away.** A poll that could not read the list keeps the rows it last read, said as the last
 *   known (`stale`), rather than an empty menu that reads as "nothing runs".
 * - **Interrupted.** `boot` says which start of the gateway this poll read (`main.js` counts the
 *   ready lines it takes). On a new start, every row that was live on the one before and is now
 *   neither live nor ended is said as cut off by the restart: the gateway that came back kept no
 *   ending of it. Said only while it is true: it goes once its work is live or ended again, or
 *   after as long as the gateway keeps an ending.
 *
 * @param {object} prev the presence before this poll
 * @param {object} next `summarizePresence()` of this poll
 * @param {{boot?: number|null, now?: number}} [opts]
 */
function carryPresence(prev, next, { boot = null, now = Date.now() } = {}) {
  const was = { ...EMPTY_PRESENCE, ...(prev || {}) };
  const out = { ...EMPTY_PRESENCE, ...next };
  if (!out.listed && (was.running.length > 0 || was.finished.length > 0)) {
    out.running = was.running;
    out.finished = was.finished;
    out.stale = true;
  }
  let interrupted = was.interrupted;
  let basis = was.basis;
  if (out.listed && boot !== null) {
    if (basis.boot !== null && basis.boot !== boot) {
      const known = new Set([...out.running, ...out.finished].map((r) => r.work));
      const cut = basis.rows.filter((r) => !known.has(r.work)).map((row) => ({ row, at: now }));
      interrupted = [...cut, ...interrupted].slice(0, INTERRUPTED_KEPT);
    }
    basis = { boot, rows: out.running };
  }
  if (out.listed) {
    const known = new Set([...out.running, ...out.finished].map((r) => r.work));
    interrupted = interrupted.filter((i) => !known.has(i.row.work));
  }
  out.interrupted = interrupted.filter((i) => now - i.at < INTERRUPTED_MS);
  out.basis = basis;
  return out;
}

/**
 * Which start of the gateway a poll read: the one it began on (*before*, `main.js`'s count of the
 * starts it took), or `null` when another start came while its reads were out (*after*), or none
 * has been taken. A restart moves the gateway to a new port, so the reads already out went to the
 * start before it, and which one answered cannot be told.
 */
function bootRead(before, after) {
  return before !== null && before > 0 && before === after ? before : null;
}

/** Keep a menu row from growing to the width of a task description. A name is the work's own
 *  text, so the controls that reorder text (the bidirectional overrides and isolates) are dropped
 *  rather than let it turn the rest of the row around. */
function truncateLabel(text, max = 48) {
  const clean = text.replace(/[\u202a-\u202e\u2066-\u2069]/g, "").replace(/\s+/g, " ").trim();
  return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean;
}

// ── The buddy's state on the tray (#3896) ──────────────────────────────────────────────────────
//
// While Buddy status is on, the menu-bar image is the buddy's still orb for the state, in place of
// the claw: idle, working, needs you, error, listening or offline. Each is a variant inside one of
// the phase contract's four families (`docs/design/buddy/surfaces.md`), and listening wins, as the
// title's capture rule does. The exact state is always in words, in the tooltip and as the menu's
// first line; a count only supports the words. An announced restart is working, said as
// restarting, never offline.

/** The still orbs the tray shows, as `docs/design/buddy/render_stills.mjs` renders them. */
const TRAY_STILLS = Object.freeze(["idle", "working", "needs-you", "error", "listening", "offline"]);

/** Each still's family: the phase contract's four (`web/src/lib/workPhase.ts`). */
const STILL_FAMILY = Object.freeze({
  idle: "quiet",
  working: "active",
  listening: "active",
  "needs-you": "waiting",
  error: "blocked",
  offline: "blocked",
});

/** Whether something stopped: a live row blocked with nothing waiting on you for it, an ending in
 *  error, or work a restart cut off. */
function somethingStopped({ running = [], finished = [], interrupted = [] }) {
  return running.some((r) => r.blocked && !(r.waiting > 0)) || finished.some((r) => r.phaseKey === "error") || interrupted.length > 0;
}

/**
 * The still for now. Listening wins, then an announced restart (working), then the gateway being
 * away (offline), then anything waiting on you, then something stopped, then live work.
 *
 * @param {object} presence `carryPresence()`'s view
 * @param {{capturing?: boolean, restarting?: boolean}} [flags]
 */
function trayStill(presence, { capturing = false, restarting = false } = {}) {
  const p = { ...EMPTY_PRESENCE, ...(presence || {}) };
  if (capturing) return "listening";
  if (restarting) return "working";
  if (!p.connected) return "offline";
  if (p.waiting > 0) return "needs-you";
  if (somethingStopped(p)) return "error";
  if (p.running.length > 0) return "working";
  return "idle";
}

/** The exact state in words, with the counts that support it. */
function stateWords(presence, { capturing = false, restarting = false } = {}) {
  const p = { ...EMPTY_PRESENCE, ...(presence || {}) };
  const still = trayStill(p, { capturing, restarting });
  const running = p.stale ? `last known ${p.running.length} running` : `${p.running.length} running`;
  switch (still) {
    case "listening": return "Listening";
    case "offline": return "Not connected to the gateway";
    case "needs-you": return `Needs you: ${waitingWords(p.waiting)}`;
    case "error": return p.running.length > 0 ? `Something stopped, ${running}` : "Something stopped";
    case "working": return restarting ? "PersonalClaw is restarting" : `Working: ${running}`;
    default: return p.listed || p.stale ? "Nothing running" : "Connected; what is running could not be read";
  }
}

/** The menu-bar image's file, beside this module: the still for the state while Buddy status is on
 *  (a template image on macOS, else the one drawn in the system's light or dark appearance's ink),
 *  or the claw (`trayImageFile`) while it is off. */
function trayStillFile(still, { platform, dark = false } = {}) {
  if (!TRAY_STILLS.includes(still)) return trayImageFile(platform);
  return platform === "darwin" ? `buddy/tray-${still}Template.png` : `buddy/tray-${still}-${dark ? "dark" : "light"}.png`;
}

/** The dock's image while something needs the owner, or null for the app's own icon. macOS only:
 *  Linux has no dock icon to change. */
function dockStillFile(presence, { platform, dark = false, buddyOn = false } = {}) {
  if (platform !== "darwin" || !buddyOn) return null;
  const p = { ...EMPTY_PRESENCE, ...(presence || {}) };
  return p.connected && p.waiting > 0 ? `buddy/dock-needs-you-${dark ? "dark" : "light"}.png` : null;
}

/**
 * The menu-bar title (macOS shows text beside the icon).
 *
 * The SINGLE writer for `tray.setTitle`. Capture wins over the badge; with neither,
 * the title is empty so the menu bar stays quiet.
 *
 * @param {{capturing?: boolean, waiting?: number}} state
 */
function composeTrayTitle({ capturing = false, waiting = 0, buddyOn = false } = {}) {
  if (capturing) return "● Listening";
  // While Buddy status is on the still orb says it, and a count never stands alone beside it.
  if (!buddyOn && Number(waiting) > 0) return `${Number(waiting)}`;
  return "";
}

/** What is waiting on you, counted: every kind the gateway's list holds. */
/**
 * What waits on you, as menu rows: each item in the list's order up to the cap, each opening its
 * route, then the rest as one row. With no item to list, the count alone. The rows past the cap and
 * the count open Mission Control, which lists every item with its answer.
 */
function waitingMenuRows(waiting, rows, deepLink) {
  // Click-through even at zero: the row is the way to the surface, and a
  // disabled row would make the menu a dead end the moment the count cleared.
  const toList = () => deepLink(DEEP_LINKS.waiting);
  if (waiting === 0 || !rows || rows.length === 0) return [{ label: waitingWords(waiting), click: toList }];
  const listed = rows.slice(0, MAX_LISTED_ROWS).map((r) => ({ label: r.label, click: () => deepLink(r.route || DEEP_LINKS.waiting) }));
  const rest = waiting - listed.length;
  if (rest > 0) listed.push({ label: `${rest} more waiting on you…`, click: toList });
  return listed;
}

function waitingWords(waiting) {
  return waiting === 0 ? "Nothing waiting on you" : `${waiting} waiting on you`;
}

/** Tooltip text — the same facts, spelled out for a hover. */
function composeTrayTooltip({ capturing = false, waiting = 0, running = [], connected = false, listed = true, stale = false, buddyOn = false, restarting = false, ...rest } = {}) {
  if (capturing) return "PersonalClaw is listening — press the shortcut again to stop";
  if (buddyOn) return truncateLabel(`PersonalClaw — ${stateWords({ waiting, running, connected, listed, stale, ...rest }, { restarting })}`, TOOLTIP_MAX);
  if (!connected) return "PersonalClaw — not connected to the gateway";
  const parts = [];
  parts.push(waitingWords(waiting));
  // A list that could not be read is no zero: a zero looks like good news.
  parts.push(stale ? `last known ${running.length} running` : listed ? `${running.length} running` : "what is running could not be read");
  return `PersonalClaw — ${parts.join(", ")}`;
}

/** A live row's menu label: its name, its exact phase (and that it is blocked), what it is doing,
 *  when it last changed, and what waits on you for it. */
function runningLabel(r) {
  const phase = r.blocked ? `Blocked, ${r.phase}` : r.phase;
  const line = r.line && r.line !== r.phase ? `: ${r.line}` : "";
  const at = clockOf(r.since);
  const waits = r.waiting > 0 ? ` · ${r.waiting} waiting on you` : "";
  return `${r.name} — ${phase}${line}${at ? ` · ${at}` : ""}${waits}`;
}

/** An ending's menu label: how it ended, in its phase's word and its line, and when. */
function endedLabel(r, { word, line } = {}) {
  const said = line ?? r.line;
  const how = word ?? r.phase;
  const at = word ? "" : clockOf(r.since);
  return `${r.name} — ${how}${said && said !== how ? `: ${said}` : ""}${at ? ` · ${at}` : ""}`;
}

/**
 * Build the tray menu template.
 *
 * Pure: it returns plain descriptors (the same shape `Menu.buildFromTemplate` takes)
 * and calls nothing, so every row and every handler can be asserted in a unit test.
 *
 * @param {object} opts
 * @param {object} opts.presence `summarizePresence()`, carried by `carryPresence()`
 * @param {{supported: boolean, enabled: boolean}} opts.loginItem
 * @param {{enabled: boolean, hidden?: boolean}} [opts.buddy] whether the buddy's window is on for this
 *        machine (#3831), and whether its owner hid it for a while (#3832)
 * @param {{capturing?: boolean, restarting?: boolean}} [opts.flags] what the state's words read
 * @param {object} opts.actions open/deepLink/quickCapture/toggleBuddy/hideBuddy/showBuddyNow/toggleLoginItem/quit
 */
function buildTrayMenuTemplate({ presence = EMPTY_PRESENCE, loginItem = { supported: false, enabled: false }, buddy = { enabled: false }, flags = {}, actions = {} } = {}) {
  const { waiting, waitingRows, running, finished, interrupted, listed, stale } = { ...EMPTY_PRESENCE, ...presence };
  const noop = () => {};
  const open = actions.open || noop;
  const deepLink = actions.deepLink || noop;
  const quickCapture = actions.quickCapture || noop;
  const toggleLoginItem = actions.toggleLoginItem || noop;
  const toggleBuddy = actions.toggleBuddy || noop;
  const hideBuddy = actions.hideBuddy || noop;
  const showBuddyNow = actions.showBuddyNow || noop;
  const quit = actions.quit || noop;

  const template = [
    {
      // The exact state in words (#3896), whatever the image shows.
      label: truncateLabel(`PersonalClaw — ${stateWords(presence, flags)}`, ROW_MAX),
      enabled: false,
    },
    { type: "separator" },
    ...waitingMenuRows(waiting, waitingRows, deepLink),
  ];

  // What is running now: every piece of live work, each opening where it runs (Mission Control
  // for work no page opens), the first rows inline and the rest in Mission Control.
  const opens = (route) => () => deepLink(route || DEEP_LINKS.missionControl);
  if (running.length === 0) {
    const word = stale ? "Last known: nothing running" : listed ? "Nothing running" : "What is running could not be read";
    template.push({ label: word, enabled: false });
  } else {
    const blocked = running.filter((r) => r.blocked).length;
    const count = `${running.length} running${blocked > 0 ? `, ${blocked} blocked` : ""}`;
    const rows = running.slice(0, MAX_LISTED_ROWS).map((r) => ({ label: runningLabel(r), click: opens(r.route) }));
    if (running.length > MAX_LISTED_ROWS) {
      rows.push({ label: `${running.length - MAX_LISTED_ROWS} more in Mission Control…`, click: opens(DEEP_LINKS.missionControl) });
    }
    template.push({ label: stale ? `Last known: ${count}` : count, submenu: rows });
  }

  // What just finished, failed or was cut off by a restart: the gateway's last endings, and what
  // it kept no ending of (`carryPresence` keeps neither of a work that ran or ended again).
  if (finished.length + interrupted.length > 0) {
    const rows = [
      ...interrupted.map((i) => ({ label: endedLabel(i.row, { word: "Interrupted", line: INTERRUPTED_LINE }), click: opens(i.row.route) })),
      ...finished.map((r) => ({ label: endedLabel(r), click: opens(r.route) })),
    ];
    template.push({ label: `Just finished: ${rows.length}`, submenu: rows.slice(0, MAX_LISTED_ROWS) });
  }

  template.push(
    { type: "separator" },
    { label: "Quick Capture Note…", click: () => quickCapture() },
    { label: "Open PersonalClaw", click: () => open() }
  );

  template.push(
    { type: "separator" },
    {
      // Buddy status, for this machine: the buddy's own window, off until the owner turns it on.
      label: "Show Buddy",
      type: "checkbox",
      checked: Boolean(buddy.enabled),
      click: (menuItem) => toggleBuddy(menuItem ? Boolean(menuItem.checked) : !buddy.enabled),
    },
    // Hiding it, for a while: the buddy shows nothing but its listening face until then, and the
    // menu bar keeps counting (#3832).
    ...(!buddy.enabled ? [] : buddy.hidden
      ? [{ label: "Show Buddy Now", click: () => showBuddyNow() }]
      : [
          { label: "Hide Buddy for an Hour", click: () => hideBuddy("hour") },
          { label: "Hide Buddy Until Tomorrow", click: () => hideBuddy("tomorrow") },
        ]),
    {
      label: "Open at Login",
      type: "checkbox",
      checked: Boolean(loginItem.enabled),
      enabled: Boolean(loginItem.supported),
      click: (menuItem) => toggleLoginItem(menuItem ? Boolean(menuItem.checked) : !loginItem.enabled),
    },
    { type: "separator" },
    { label: "Quit PersonalClaw", click: () => quit() }
  );

  return template;
}

/**
 * Should a window `close` hide the window instead of closing it?
 *
 * Hiding is only safe while a menu-bar item can bring the window back. With no tray,
 * hiding would strand the user in a running app with no window and no affordance —
 * so a tray-less run closes for real.
 */
function shouldHideOnClose({ trayAvailable, isQuitting }) {
  return Boolean(trayAvailable) && !isQuitting;
}

/**
 * Should `window-all-closed` quit the app?
 *
 * macOS convention is to keep running — but ONLY because the menu-bar item is still
 * there. Without a tray, staying alive with no windows is the phantom state.
 */
function shouldQuitOnAllWindowsClosed({ platform, trayAvailable }) {
  if (platform !== "darwin") return true;
  return !trayAvailable;
}

/**
 * The menu-bar image for a platform, a file beside this module.
 *
 * macOS gets a black silhouette with alpha whose name ends in `Template`, which makes
 * Electron treat it as a template image that macOS recolours for a light, dark or tinted
 * menu bar. Elsewhere the image is the mark in its gradient. Both are rendered from the
 * mark by `docs/brand/render_marks.mjs`, at 18 points with an `@2x` beside them.
 */
function trayImageFile(platform) {
  return platform === "darwin" ? "trayTemplate.png" : "tray.png";
}

/**
 * The Electron-facing wrapper: builds the tray if it can, and reports honestly if it
 * cannot. Every Electron dependency is injected so tests drive fakes.
 *
 * @param {object} deps
 * @param {Function} deps.TrayCtor Electron `Tray`
 * @param {{buildFromTemplate: Function}} deps.MenuCtor Electron `Menu`
 * @param {{createFromPath: Function}} deps.nativeImageMod Electron `nativeImage`
 * @param {string} deps.trayImagePath the platform's menu-bar image, drawn at 18 points
 *        with an `@2x` beside it (`trayImageFile()`), loaded as it is
 * @param {object} deps.actions passed through to `buildTrayMenuTemplate`
 * @param {string} [deps.platform] `process.platform`, which picks the stills' files
 * @param {() => boolean} [deps.isDark] the system's appearance (`nativeTheme.shouldUseDarkColors`)
 * @param {{setIcon: Function}} [deps.dock] Electron's `app.dock`, on macOS only
 * @param {string} [deps.appIconPath] the app's own icon, which the dock goes back to
 * @param {(msg: string) => void} [deps.log]
 */
function makeTrayPresence({
  TrayCtor,
  MenuCtor,
  nativeImageMod,
  trayImagePath,
  actions = {},
  platform = process.platform,
  isDark = () => false,
  dock = null,
  appIconPath = null,
  log = () => {},
} = {}) {
  let tray = null;
  let presence = { ...EMPTY_PRESENCE };
  let loginItemState = { supported: false, enabled: false };
  /** Buddy status (`enabled`, the checkbox) and the surfaces it shows on here: the tray's stills
   *  and the dock's (`buddyPolicy.js`). */
  let buddyState = { enabled: false, tray: false, dock: false };
  let capturing = false;
  let restarting = false;
  /** The files the tray and the dock show now, so an unchanged state sets no image. */
  let shownTray = trayImagePath || null;
  let shownDock = null;
  const images = new Map();
  const imageDir = trayImagePath ? path.dirname(trayImagePath) : null;

  /** An image beside the claw's, loaded once, or null when it cannot be read. */
  function imageAt(file) {
    if (!nativeImageMod || !file) return null;
    if (images.has(file)) return images.get(file);
    let image = null;
    try {
      image = nativeImageMod.createFromPath(file);
      if (!image || (typeof image.isEmpty === "function" && image.isEmpty())) image = null;
    } catch (err) {
      log(`image ${file} failed to load: ${err.message}`);
    }
    images.set(file, image);
    return image;
  }

  /** The tray's image and the dock's for the state: the still while Buddy status is on, else the
   *  claw and the app icon. A still that cannot be read leaves the image as it was. */
  function showState() {
    const flags = { capturing, restarting };
    const dark = Boolean(isDark());
    const wantTray = buddyState.tray && imageDir
      ? path.join(imageDir, trayStillFile(trayStill(presence, flags), { platform, dark }))
      : trayImagePath;
    if (wantTray && wantTray !== shownTray) {
      const image = imageAt(wantTray);
      if (image) {
        try {
          tray.setImage(image);
          shownTray = wantTray;
        } catch (err) {
          log(`tray image not set: ${err.message}`);
        }
      }
    }
    if (!dock || typeof dock.setIcon !== "function") return;
    const still = dockStillFile(presence, { platform, dark, buddyOn: buddyState.dock });
    const wantDock = still && imageDir ? path.join(imageDir, still) : null;
    if (wantDock === shownDock) return;
    const image = imageAt(wantDock || appIconPath);
    if (!image) return;
    try {
      dock.setIcon(image);
      shownDock = wantDock;
    } catch (err) {
      log(`dock icon not set: ${err.message}`);
    }
  }

  /** Load the menu-bar image, tolerating a missing/corrupt file. Returns null when unusable. */
  function loadIcon() {
    if (!nativeImageMod || !trayImagePath) return null;
    try {
      // Taken as drawn: the file is already menu-bar sized, and Electron picks up the
      // `@2x` beside it on a Retina display. A `Template` name is what makes macOS
      // treat it as a template image and recolour it for the menu bar.
      const image = nativeImageMod.createFromPath(trayImagePath);
      if (!image || (typeof image.isEmpty === "function" && image.isEmpty())) {
        log(`tray icon at ${trayImagePath} is empty or unreadable`);
        return null;
      }
      return image;
    } catch (err) {
      log(`tray icon failed to load: ${err.message}`);
      return null;
    }
  }

  function start() {
    if (!TrayCtor || !MenuCtor) {
      log("no Tray implementation on this platform — running without menu-bar presence");
      return false;
    }
    const icon = loadIcon();
    if (!icon) {
      // No icon means no tray: an invisible menu-bar item is worse than none,
      // because the window-close behavior would still assume one is there.
      log("skipping menu-bar presence — no usable icon");
      return false;
    }
    try {
      tray = new TrayCtor(icon);
    } catch (err) {
      log(`menu-bar presence unavailable: ${err.message}`);
      tray = null;
      return false;
    }
    try {
      tray.on("click", () => (actions.open || (() => {}))());
    } catch (err) {
      log(`tray click handler could not be attached: ${err.message}`);
    }
    render();
    return true;
  }

  /** Rebuild the menu, title and tooltip from current state. Never throws. */
  function render() {
    if (!tray) return;
    try {
      const flags = { capturing, restarting };
      const template = buildTrayMenuTemplate({ presence, loginItem: loginItemState, buddy: buddyState, flags, actions });
      tray.setContextMenu(MenuCtor.buildFromTemplate(template));
      tray.setTitle(composeTrayTitle({ capturing, waiting: presence.waiting, buddyOn: buddyState.tray }));
      tray.setToolTip(composeTrayTooltip({ capturing, ...presence, buddyOn: buddyState.tray, restarting }));
      showState();
    } catch (err) {
      // A tray destroyed mid-render during shutdown is the common case here.
      log(`tray render skipped: ${err.message}`);
    }
  }

  return {
    /** Whether a menu-bar item actually exists. Drives the close/quit behavior. */
    get available() {
      return tray !== null;
    },
    /** What the menu shows now, which the next poll is carried from (`carryPresence`). */
    get presence() {
      return presence;
    },
    start,
    render,
    /** Live counts from the loopback gateway. */
    setPresence(next) {
      presence = { ...EMPTY_PRESENCE, ...(next || {}) };
      render();
    },
    /** The login item's current registration, for the checkbox. */
    setLoginItemState(next) {
      loginItemState = { supported: false, enabled: false, ...(next || {}) };
      render();
    },
    /** Buddy status for the checkbox, whether its owner hid it for a while, and whether the tray's
     *  stills and the dock's show (each on with it unless the policy turns that surface off). */
    setBuddyState(next) {
      const enabled = Boolean(next && next.enabled);
      const surface = (key) => enabled && (typeof (next && next[key]) === "boolean" ? next[key] : true);
      buddyState = { enabled, hidden: Boolean(next && next.hidden), tray: surface("tray"), dock: surface("dock") };
      render();
    },
    /** The microphone indicator, routed through the one title writer. */
    setCapturing(on) {
      capturing = Boolean(on);
      render();
    },
    /** The gateway announced a restart (the doorbell's `restarting`), until it is up again. */
    setRestarting(on) {
      restarting = Boolean(on);
      render();
    },
    /** The system's appearance changed: the Linux stills follow it. */
    appearanceChanged() {
      render();
    },
    destroy() {
      if (!tray) return;
      try {
        tray.destroy();
      } catch (err) {
        log(`tray destroy skipped: ${err.message}`);
      }
      tray = null;
    },
  };
}

module.exports = {
  DEEP_LINKS,
  MAX_LISTED_ROWS,
  INTERRUPTED_KEPT,
  INTERRUPTED_MS,
  TRAY_STILLS,
  STILL_FAMILY,
  ROW_MAX,
  TOOLTIP_MAX,
  trayStill,
  stateWords,
  trayStillFile,
  dockStillFile,
  PHASE_WORDS,
  EMPTY_PRESENCE,
  WAITING_KIND_LABEL,
  waitingRowOf,
  summarizePresence,
  carryPresence,
  bootRead,
  composeTrayTitle,
  composeTrayTooltip,
  buildTrayMenuTemplate,
  shouldHideOnClose,
  shouldQuitOnAllWindowsClosed,
  makeTrayPresence,
  trayImageFile,
  truncateLabel,
};
