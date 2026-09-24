/**
 * Menu-bar presence (DC-4 T4.1).
 *
 * The pre-DC-4 tray was a static six-item menu (Show / New Tab / Merge All Windows /
 * Quit) built inline in `main.js`. This module replaces it with a LIVE one — pending
 * approvals, running loops, quick capture, open dashboard, an opt-in login item, quit
 * — and pulls the logic out of the Electron glue so it can be tested without
 * launching an app.
 *
 * Three things this module exists to get right:
 *
 * 1. **ONE writer for the menu-bar title.** DC-3 already drives `tray.setTitle()`
 *    for the "● Listening" push-to-talk indicator. A counts badge that also called
 *    `setTitle` would be a second writer to a single surface, and whichever fired
 *    last would win — so both go through `composeTrayTitle()`, and capture ALWAYS
 *    wins (a live-microphone indicator is a privacy signal; an approvals badge can
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
 *    `GET /api/approvals` → a bare JSON array of pending approvals;
 *    `GET /api/loops` → `{"loops": [...]}` with a `status` per loop
 *    (`running` is one of `personalclaw.loop.loop.LoopStatus`). `summarizePresence()`
 *    is tolerant of nulls and of either shape, because a failed poll must render as
 *    "0 / not connected", never as a crash in a menu builder.
 */

/**
 * SPA deep-link targets. The dashboard is a HASH-routed SPA (`web/src/app/App.tsx`),
 * and these are its real routes — `chat` is where `ApprovalPrompt` renders pending
 * approvals, and `loops/<id>` is a routable loop detail deep-link.
 */
const DEEP_LINKS = {
  dashboard: "#/dashboard",
  approvals: "#/chat",
  inbox: "#/inbox",
  settings: "#/settings",
  loop: (id) => `#/loops/${encodeURIComponent(String(id))}`,
};

/** The loop status that "running loops" means. See `LoopStatus.RUNNING`. */
const RUNNING_STATUS = "running";

/** Most loops listed inline before the menu stops growing. */
const MAX_LISTED_LOOPS = 8;

/** An empty presence — what a disconnected or failed poll renders as. */
const EMPTY_PRESENCE = Object.freeze({ approvals: 0, running: [], connected: false });

/**
 * Fold the two gateway payloads into the menu's view model.
 *
 * @param {unknown} approvalsPayload body of `GET /api/approvals` (a bare array)
 * @param {unknown} loopsPayload body of `GET /api/loops` (`{loops: [...]}`)
 * @returns {{approvals: number, running: Array<{id: string, label: string}>,
 *            connected: boolean}}
 */
function summarizePresence(approvalsPayload, loopsPayload) {
  const connected = approvalsPayload !== null && approvalsPayload !== undefined;

  // A bare array is the documented shape; the object forms are accepted so a future
  // envelope change degrades to a count rather than to zero.
  let approvals = 0;
  if (Array.isArray(approvalsPayload)) approvals = approvalsPayload.length;
  else if (approvalsPayload && typeof approvalsPayload === "object") {
    const inner = approvalsPayload.approvals ?? approvalsPayload.items;
    if (Array.isArray(inner)) approvals = inner.length;
    else if (Number.isFinite(approvalsPayload.pending)) approvals = Number(approvalsPayload.pending);
  }

  const rows = Array.isArray(loopsPayload)
    ? loopsPayload
    : Array.isArray(loopsPayload?.loops)
      ? loopsPayload.loops
      : [];
  const running = rows
    .filter((l) => l && typeof l === "object" && String(l.status || "").toLowerCase() === RUNNING_STATUS)
    .map((l) => ({
      id: String(l.id ?? ""),
      // Loop rows carry a name or a task; either is a better menu label than an id,
      // and an id-only row is still clickable.
      label: truncateLabel(String(l.name || l.task || l.goal || l.id || "Untitled loop")),
    }))
    .filter((l) => l.id !== "");

  return { approvals, running, connected };
}

/** Keep a menu row from growing to the width of a task description. */
function truncateLabel(text, max = 48) {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean;
}

/**
 * The menu-bar title (macOS shows text beside the icon).
 *
 * The SINGLE writer for `tray.setTitle`. Capture wins over the badge; with neither,
 * the title is empty so the menu bar stays quiet.
 *
 * @param {{capturing?: boolean, approvals?: number}} state
 */
function composeTrayTitle({ capturing = false, approvals = 0 } = {}) {
  if (capturing) return "● Listening";
  if (Number(approvals) > 0) return `${Number(approvals)}`;
  return "";
}

/** Tooltip text — the same facts, spelled out for a hover. */
function composeTrayTooltip({ capturing = false, approvals = 0, running = [], connected = false } = {}) {
  if (capturing) return "PersonalClaw is listening — press the shortcut again to stop";
  if (!connected) return "PersonalClaw — not connected to the gateway";
  const parts = [];
  parts.push(approvals === 1 ? "1 approval waiting" : `${approvals} approvals waiting`);
  parts.push(running.length === 1 ? "1 loop running" : `${running.length} loops running`);
  return `PersonalClaw — ${parts.join(", ")}`;
}

/**
 * Build the tray menu template.
 *
 * Pure: it returns plain descriptors (the same shape `Menu.buildFromTemplate` takes)
 * and calls nothing, so every row and every handler can be asserted in a unit test.
 *
 * @param {object} opts
 * @param {{approvals: number, running: Array, connected: boolean}} opts.presence
 * @param {{supported: boolean, enabled: boolean}} opts.loginItem
 * @param {object} opts.actions open/deepLink/quickCapture/toggleLoginItem/quit
 * @param {Array<{label: string, click?: Function}>} [opts.tiles] AMBIENT-SURFACES
 *        menu-bar tiles. Absent until that plan exists — rendered when supplied,
 *        skipped when not, so this atom never blocks on it.
 */
function buildTrayMenuTemplate({ presence = EMPTY_PRESENCE, loginItem = { supported: false, enabled: false }, actions = {}, tiles = [] } = {}) {
  const { approvals, running, connected } = { ...EMPTY_PRESENCE, ...presence };
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
      label:
        approvals === 0
          ? "No approvals waiting"
          : approvals === 1
            ? "1 approval waiting"
            : `${approvals} approvals waiting`,
      // Click-through even at zero: the row is the way to the surface, and a
      // disabled row would make the menu a dead end the moment the count cleared.
      click: () => deepLink(DEEP_LINKS.approvals),
    },
  ];

  if (running.length === 0) {
    template.push({ label: "No loops running", enabled: false });
  } else {
    template.push({
      label: running.length === 1 ? "1 loop running" : `${running.length} loops running`,
      submenu: running.slice(0, MAX_LISTED_LOOPS).map((loop) => ({
        label: loop.label,
        click: () => deepLink(DEEP_LINKS.loop(loop.id)),
      })),
    });
  }

  template.push(
    { type: "separator" },
    { label: "Quick Capture Note…", click: () => quickCapture() },
    { label: "Open Dashboard", click: () => open() }
  );

  // AMBIENT-SURFACES tiles, when that plan is available. Non-blocking by design:
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
 * The Electron-facing wrapper: builds the tray if it can, and reports honestly if it
 * cannot. Every Electron dependency is injected so tests drive fakes.
 *
 * @param {object} deps
 * @param {Function} deps.TrayCtor Electron `Tray`
 * @param {{buildFromTemplate: Function}} deps.MenuCtor Electron `Menu`
 * @param {{createFromPath: Function}} deps.nativeImageMod Electron `nativeImage`
 * @param {string} deps.iconPath
 * @param {object} deps.actions passed through to `buildTrayMenuTemplate`
 * @param {(msg: string) => void} [deps.log]
 */
function makeTrayPresence({ TrayCtor, MenuCtor, nativeImageMod, iconPath, actions = {}, log = () => {} } = {}) {
  let tray = null;
  let presence = { ...EMPTY_PRESENCE };
  let loginItemState = { supported: false, enabled: false };
  let capturing = false;
  let tiles = [];

  /** Load the icon, tolerating a missing/corrupt file. Returns null when unusable. */
  function loadIcon() {
    if (!nativeImageMod || !iconPath) return null;
    try {
      const image = nativeImageMod.createFromPath(iconPath);
      if (!image || (typeof image.isEmpty === "function" && image.isEmpty())) {
        log(`tray icon at ${iconPath} is empty or unreadable`);
        return null;
      }
      return typeof image.resize === "function" ? image.resize({ width: 18, height: 18 }) : image;
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
      tray.setTitle(composeTrayTitle({ capturing, approvals: presence.approvals }));
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
    /** DC-3's microphone indicator, routed through the one title writer. */
    setCapturing(on) {
      capturing = Boolean(on);
      render();
    },
    /** AMBIENT-SURFACES tiles, when that plan lands. */
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
  RUNNING_STATUS,
  MAX_LISTED_LOOPS,
  EMPTY_PRESENCE,
  summarizePresence,
  composeTrayTitle,
  composeTrayTooltip,
  buildTrayMenuTemplate,
  shouldHideOnClose,
  shouldQuitOnAllWindowsClosed,
  makeTrayPresence,
  truncateLabel,
};
