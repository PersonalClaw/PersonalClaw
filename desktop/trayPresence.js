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
    line: truncateLabel(String(r.line || ""), 60),
    since: typeof r.since === "string" ? r.since : "",
    route: typeof r.route === "string" ? r.route : "",
    blocked: r.blocked === true,
    waiting: Array.isArray(r.waiting) ? r.waiting.length : 0,
  };
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
  if (Array.isArray(waitingPayload)) waiting = waitingPayload.length;
  else if (waitingPayload && typeof waitingPayload === "object") {
    const inner = waitingPayload.waiting ?? waitingPayload.items;
    if (Array.isArray(inner)) waiting = inner.length;
    else if (Number.isFinite(waitingPayload.pending)) waiting = Number(waitingPayload.pending);
  }

  const listed = Boolean(runningPayload && typeof runningPayload === "object" && Array.isArray(runningPayload.running));
  const rows = (key) => (listed && Array.isArray(runningPayload[key]) ? runningPayload[key] : []).map(rowOf).filter(Boolean);
  return { ...EMPTY_PRESENCE, waiting, connected, listed, running: rows("running"), finished: rows("finished") };
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

/**
 * The menu-bar title (macOS shows text beside the icon).
 *
 * The SINGLE writer for `tray.setTitle`. Capture wins over the badge; with neither,
 * the title is empty so the menu bar stays quiet.
 *
 * @param {{capturing?: boolean, waiting?: number}} state
 */
function composeTrayTitle({ capturing = false, waiting = 0 } = {}) {
  if (capturing) return "● Listening";
  if (Number(waiting) > 0) return `${Number(waiting)}`;
  return "";
}

/** What is waiting on you, counted: every kind the gateway's list holds. */
function waitingWords(waiting) {
  return waiting === 0 ? "Nothing waiting on you" : `${waiting} waiting on you`;
}

/** Tooltip text — the same facts, spelled out for a hover. */
function composeTrayTooltip({ capturing = false, waiting = 0, running = [], connected = false, listed = true, stale = false } = {}) {
  if (capturing) return "PersonalClaw is listening — press the shortcut again to stop";
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
 * @param {object} opts.actions open/deepLink/quickCapture/toggleLoginItem/quit
 * @param {Array<{label: string, click?: Function}>} [opts.tiles] ambient
 *        menu-bar tiles. Optional — rendered when supplied,
 *        skipped when not, so the menu never depends on them.
 */
function buildTrayMenuTemplate({ presence = EMPTY_PRESENCE, loginItem = { supported: false, enabled: false }, actions = {}, tiles = [] } = {}) {
  const { waiting, running, finished, interrupted, connected, listed, stale } = { ...EMPTY_PRESENCE, ...presence };
  const noop = () => {};
  const open = actions.open || noop;
  const deepLink = actions.deepLink || noop;
  const quickCapture = actions.quickCapture || noop;
  const toggleLoginItem = actions.toggleLoginItem || noop;
  const quit = actions.quit || noop;

  const template = [
    {
      label: connected ? "PersonalClaw — connected" : "PersonalClaw — not connected",
      enabled: false,
    },
    { type: "separator" },
    {
      label: waitingWords(waiting),
      // Click-through even at zero: the row is the way to the surface, and a
      // disabled row would make the menu a dead end the moment the count cleared.
      click: () => deepLink(DEEP_LINKS.waiting),
    },
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
    { label: "Open Dashboard", click: () => open() }
  );

  // Ambient tiles, when supplied. Non-blocking by design:
  // an empty list adds nothing, not an empty section.
  const validTiles = (Array.isArray(tiles) ? tiles : []).filter((t) => t && typeof t.label === "string");
  if (validTiles.length > 0) {
    template.push({ type: "separator" });
    for (const tile of validTiles) {
      template.push({ label: tile.label, click: tile.click || noop });
    }
  }

  template.push(
    { type: "separator" },
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
 * @param {(msg: string) => void} [deps.log]
 */
function makeTrayPresence({ TrayCtor, MenuCtor, nativeImageMod, trayImagePath, actions = {}, log = () => {} } = {}) {
  let tray = null;
  let presence = { ...EMPTY_PRESENCE };
  let loginItemState = { supported: false, enabled: false };
  let capturing = false;
  let tiles = [];

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
      const template = buildTrayMenuTemplate({ presence, loginItem: loginItemState, actions, tiles });
      tray.setContextMenu(MenuCtor.buildFromTemplate(template));
      tray.setTitle(composeTrayTitle({ capturing, waiting: presence.waiting }));
      tray.setToolTip(composeTrayTooltip({ capturing, ...presence }));
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
    /** The microphone indicator, routed through the one title writer. */
    setCapturing(on) {
      capturing = Boolean(on);
      render();
    },
    /** Ambient tiles, when supplied. */
    setTiles(next) {
      tiles = Array.isArray(next) ? next : [];
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
  PHASE_WORDS,
  EMPTY_PRESENCE,
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
