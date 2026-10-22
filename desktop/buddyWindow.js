/**
 * The buddy's own window (#3831): a second window, not a tab. Electron is injected
 * (`BrowserWindowCtor`, `screen`), as in `trayPresence.js` and `nativeNotifications.js`, so a node
 * test drives every rule without launching an app.
 *
 * - **It never takes focus to show.** Transparent, frameless, always on top, out of the task bar and
 *   the tab group (no `tabbingIdentifier`, and it never goes through `makeWindow`), shown with
 *   `showInactive()`. It takes the keyboard only when the owner clicks into it.
 * - **Clicks pass through** everywhere but the face and the open card. On macOS the window ignores
 *   the pointer and forwards its moves, and the page says when the pointer is over the shape
 *   (`pointerOver`); on Linux the window's shape is the page's regions (`setRegions`).
 * - **Free while hidden.** Hiding the window stops the forwarded pointer as well, so a hidden page
 *   samples nothing; the page itself schedules no timer and no frame while `document.hidden`.
 * - **Kept out of screen capture** while "Hide from screen sharing and screenshots" is on, which it
 *   is unless this machine's store says `"0"` under `CAPTURE_KEY`.
 * - **Placed** by `buddyPlacement.js`: docked on the menu-bar display, or floating where it was
 *   dropped, saved in the shell's store.
 * - **The owner's to have.** It exists only while "Buddy status" is on for this machine
 *   (`makeBuddyPresence`), and it never keeps the app running by itself (`buddyOutlivesClose`).
 */

const placement = require("./buddyPlacement");
const { SCHEME, parseDeepLink } = require("./deepLinks");

/** "Hide from screen sharing and screenshots": on unless this is `"0"`. */
const CAPTURE_KEY = "buddy:hideFromCapture";
/** "Buddy status": the window exists only while this is `"1"`, and nothing sets it but the owner. */
const ENABLED_KEY = "buddy:enabled";

/** The window's options. Exported for the test: what the window IS is only observable through them. */
function buddyWindowOptions({ bounds, preload }) {
  return {
    ...bounds,
    show: false,
    transparent: true,
    backgroundColor: "#00000000",
    frame: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    resizable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    roundedCorners: false,
    title: "PersonalClaw buddy",
    webPreferences: {
      preload,
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      // A hidden page is throttled like a background tab, on top of scheduling nothing itself.
      backgroundThrottling: true,
      spellcheck: false,
    },
  };
}

function makeBuddyWindow({ BrowserWindowCtor, screen, store, preload, platform = process.platform, onPlacement = () => {}, log = () => {} }) {
  let win = null;
  let presentation = "compact";
  let current = placement.readPlacement(store);
  let drag = null;
  let visible = false;
  let following = false;

  const displays = () => {
    try { return screen.getAllDisplays(); } catch { return []; }
  };
  const primary = () => {
    try { return screen.getPrimaryDisplay(); } catch { return null; }
  };
  const resolved = () => placement.resolveBounds({ placement: current, presentation, displays: displays(), primary: primary() });

  const hideFromCapture = () => store.getItem(CAPTURE_KEY) !== "0";

  /** Click-through on: the window passes every click, and (on macOS) forwards moves only while it
   *  shows, so a hidden window samples nothing. */
  const passClicks = () => {
    if (!win) return;
    if (platform === "darwin") win.setIgnoreMouseEvents(true, visible ? { forward: true } : undefined);
  };

  const place = (animate = false) => {
    const at = resolved();
    if (!win || !at) return;
    win.setBounds(at.bounds, animate && platform === "darwin");
  };

  function create(url) {
    if (win) return win;
    const at = resolved();
    win = new BrowserWindowCtor(buddyWindowOptions({ bounds: at ? at.bounds : { width: 360, height: 28 }, preload }));
    if (platform === "darwin") {
      win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
      // Not one of the app's windows in the Window menu either.
      win.excludedFromShownWindowsMenu = true;
    }
    win.setAlwaysOnTop(true, "floating");
    win.setContentProtection(hideFromCapture());
    passClicks();
    win.on("closed", () => { win = null; visible = false; drag = null; });
    if (url) win.loadURL(url);
    // Once for the controller, however many times the owner turns the buddy off and on.
    if (!following) {
      following = true;
      for (const event of ["display-added", "display-removed", "display-metrics-changed"]) {
        try { screen.on(event, () => place()); } catch (err) { log(`buddy: cannot follow ${event}: ${err && err.message}`); }
      }
    }
    return win;
  }

  return {
    create,
    get window() { return win; },
    get placement() { return { ...current }; },
    isVisible: () => visible,
    /** Show without taking focus. */
    show() {
      if (!win) return;
      visible = true;
      place();
      win.showInactive();
      passClicks();
    },
    hide() {
      if (!win) return;
      visible = false;
      passClicks();
      win.hide();
    },
    destroy() {
      if (win) win.destroy();
      win = null;
      visible = false;
      drag = null;
    },
    /** The page's presentation (`peek`, `compact`, `expanded`): the window takes its room. */
    setPresentation(next) {
      if (!placement.SIZES[next] || next === presentation) return;
      presentation = next;
      place();
    },
    /** macOS: the forwarded pointer is over the face or the card (take clicks), or not (pass them). */
    pointerOver(hit) {
      if (!win || platform !== "darwin") return;
      if (hit && visible) win.setIgnoreMouseEvents(false);
      else passClicks();
    },
    /** Linux: the face's and the card's rectangles, in the window's own points, are its shape. No
     *  rectangle is one empty rectangle, not an empty list: Electron reads `[]` as "the whole
     *  window again", which would take every click. */
    setRegions(rects) {
      if (!win || platform === "darwin" || typeof win.setShape !== "function") return;
      const ok = Array.isArray(rects) ? rects.filter((r) => r && [r.x, r.y, r.width, r.height].every(Number.isFinite)) : [];
      const shape = ok.map((r) => ({ x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) }));
      win.setShape(shape.length ? shape : [{ x: 0, y: 0, width: 0, height: 0 }]);
    },
    /** A drag of the face starts: the window follows the pointer's deltas until `endDrag`. */
    beginDrag() {
      if (!win) return;
      const [x, y] = win.getPosition();
      drag = { x, y, out: current.mode === "floating", moved: false };
    },
    /** A docked buddy holds its place until the drag takes it past the undock distance. */
    dragBy(dx, dy) {
      if (!win || !drag || !Number.isFinite(dx) || !Number.isFinite(dy)) return;
      drag = { ...drag, x: drag.x + dx, y: drag.y + dy };
      if (!drag.out) {
        const at = resolved();
        const home = displays().find((d) => at && d.id === at.displayId) || primary();
        if (!home || !placement.draggedOut(home, drag.y)) return;
        drag.out = true;
      }
      win.setPosition(Math.round(drag.x), Math.round(drag.y));
      // The page hears that the window is moving once, when it first moves: a press that never
      // moves it is a click, not a drag.
      if (!drag.moved) {
        drag.moved = true;
        onPlacement("moving");
      }
    },
    /** The drop: dock in the dock zone, else float where it fell; saved for this machine. `instant`
     *  under reduced motion. */
    endDrag({ instant = false } = {}) {
      if (!win || !drag) return;
      const { out, moved } = drag;
      const [x, y] = win.getPosition();
      drag = null;
      if (!out) {
        place();
        if (moved) onPlacement(current.mode);
        return;
      }
      current = placement.settleDrop({ faceTopLeft: { x, y }, displays: displays(), primary: primary() });
      placement.writePlacement(store, current);
      place(!instant);
      if (moved) onPlacement(current.mode);
    },
    /** Back to the dock, for this machine: Settings' reset. */
    resetPlacement() {
      current = { mode: "docked" };
      placement.writePlacement(store, current);
      place();
    },
    /** "Hide from screen sharing and screenshots", for this machine. */
    setHideFromCapture(on) {
      store.setItem(CAPTURE_KEY, on ? "1" : "0");
      if (win) win.setContentProtection(!!on);
    },
    hideFromCapture,
  };
}

// ── Whether the window exists ──────────────────────────────────────────────────────────────────

/**
 * The window exists only while "Buddy status" is on for this machine (`ENABLED_KEY`), which it is
 * not until the owner turns it on, and only once there is a gateway to load it from. `url()` is the
 * buddy route on the gateway this shell started, or null before it is up. `prepare(win)` runs
 * before the page loads (main's navigation guards). The window shows once its page has painted,
 * docked or where it was left.
 */
function makeBuddyPresence({ buddy, store, url, prepare = () => {}, log = () => {} }) {
  const enabled = () => store.getItem(ENABLED_KEY) === "1";
  function start() {
    if (buddy.window) return true;
    if (!enabled()) return false;
    const at = url();
    if (!at) return false;
    const win = buddy.create(null);
    prepare(win);
    win.once("ready-to-show", () => buddy.show());
    win.loadURL(at);
    log("buddy: the window is up");
    return true;
  }
  const stop = () => buddy.destroy();
  return {
    enabled,
    start,
    stop,
    /** The owner's switch: on creates the window, off closes it. */
    setEnabled(on) {
      store.setItem(ENABLED_KEY, on ? "1" : "0");
      if (on) return start();
      stop();
      return false;
    },
  };
}

/**
 * Whether the buddy stays when one of the app's own windows closes. It never keeps the app alive on
 * its own: once the last of them is gone, and the app would quit with no window left (no menu-bar
 * item to bring one back), the buddy goes too, so the app quits as it did before there was one.
 */
function buddyOutlivesClose({ appWindowsLeft, quitsWithNoWindow }) {
  return appWindowsLeft > 0 || !quitsWithNoWindow;
}

// ── The buddy's bridge (main side) ─────────────────────────────────────────────────────────────
//
// The page reaches the shell through five named sends and nothing else (`buddyPreload.js` copies
// the names, sandboxed preloads cannot require them). Each is heard only from the buddy window's own
// web contents, and each payload is checked here whatever the preload sent: a page that is not the
// buddy's, or a value outside its shape, changes nothing.

const BUDDY_PREFIX = "pclaw-buddy:";
const BUDDY_CHANNELS = Object.freeze({
  pointer: `${BUDDY_PREFIX}pointer`,
  regions: `${BUDDY_PREFIX}regions`,
  drag: `${BUDDY_PREFIX}drag`,
  presentation: `${BUDDY_PREFIX}presentation`,
  open: `${BUDDY_PREFIX}open`,
});

/** What main tells the buddy's page (#3832), the only two messages that go that way: a ring, which
 *  carries nothing and says the lists moved (read them again), and the doorbell's connection. */
const BUDDY_PUSHES = Object.freeze({
  ring: `${BUDDY_PREFIX}ring`,
  connection: `${BUDDY_PREFIX}connection`,
  placement: `${BUDDY_PREFIX}placement`,
  fullScreen: `${BUDDY_PREFIX}full-screen`,
  // A tap on an alert's notification opens the buddy (#3897): a ring carrying nothing, which the
  // page opens on as a click on the orb opens it, on the alert the one order shows first.
  openAlert: `${BUDDY_PREFIX}open-alert`,
});

/** The doorbell's connection states the page hears (`doorbell.js`). */
const BUDDY_CONNECTIONS = Object.freeze(["up", "lost", "restarting"]);

/** Where the window is, as the page hears it: a drag moving it, or where it settled. */
const BUDDY_PLACEMENTS = Object.freeze(["moving", "docked", "floating"]);

/**
 * Tell the buddy's page *name*: `ring`; `connection`, one of `BUDDY_CONNECTIONS`; `placement`, one
 * of `BUDDY_PLACEMENTS`; `fullScreen`, whether one of the app's own windows is full screen; or
 * `openAlert`, a tap on an alert's notification, carrying nothing.
 * Nothing else is sent, and nothing at all to a window that is gone. Returns whether it was sent.
 */
function pushToBuddy(win, name, value) {
  if (!win || win.isDestroyed() || !win.webContents || win.webContents.isDestroyed()) return false;
  if (name === "ring") win.webContents.send(BUDDY_PUSHES.ring);
  else if (name === "connection" && BUDDY_CONNECTIONS.includes(value)) win.webContents.send(BUDDY_PUSHES.connection, value);
  else if (name === "placement" && BUDDY_PLACEMENTS.includes(value)) win.webContents.send(BUDDY_PUSHES.placement, value);
  else if (name === "fullScreen" && typeof value === "boolean") win.webContents.send(BUDDY_PUSHES.fullScreen, value);
  else if (name === "openAlert") win.webContents.send(BUDDY_PUSHES.openAlert);
  else return false;
  return true;
}

/** At most this many rectangles make the window's shape (the face, the card and their parts). */
const MAX_REGIONS = 8;
/** No rectangle or drag step is larger than the largest display the shell has met. */
const MAX_EXTENT = 16384;
/** The longest route the buddy may ask the main window to open. */
const MAX_ROUTE = 512;

/** A route the buddy may open in the main window, in the form the page wrote it (`#/chat/abc`):
 *  exactly the routes and parameters a `pclaw://` link may open, checked by the same parser, and
 *  handed on in that parser's own spelling. */
function openableRoute(route) {
  if (typeof route !== "string" || route.length > MAX_ROUTE || !route.startsWith("#/")) return null;
  const link = parseDeepLink(`${SCHEME}://${route.slice(2)}`);
  return link.ok ? link.hash : null;
}

const finite = (v, limit = MAX_EXTENT) => typeof v === "number" && Number.isFinite(v) && Math.abs(v) <= limit;
const plain = (v) => !!v && typeof v === "object" && !Array.isArray(v);

/** The checked form of each payload, or null for one outside its shape. Exported for the test. */
const BUDDY_PAYLOADS = Object.freeze({
  pointer: (p) => (plain(p) && typeof p.hit === "boolean" ? { hit: p.hit } : null),
  regions: (p) => {
    if (!plain(p) || !Array.isArray(p.rects) || p.rects.length > MAX_REGIONS) return null;
    const rects = p.rects.filter((r) => plain(r) && finite(r.x) && finite(r.y) && finite(r.width) && finite(r.height) && r.width >= 0 && r.height >= 0);
    return rects.length === p.rects.length ? { rects: rects.map(({ x, y, width, height }) => ({ x, y, width, height })) } : null;
  },
  drag: (p) => {
    if (!plain(p) || !["start", "move", "end"].includes(p.phase)) return null;
    if (p.phase === "move") return finite(p.dx) && finite(p.dy) ? { phase: "move", dx: p.dx, dy: p.dy } : null;
    if (p.phase === "end") return { phase: "end", instant: p.instant === true };
    return { phase: "start" };
  },
  presentation: (p) => (plain(p) && typeof p.presentation === "string" && Object.prototype.hasOwnProperty.call(placement.SIZES, p.presentation) ? { presentation: p.presentation } : null),
  open: (p) => {
    const route = plain(p) ? openableRoute(p.route) : null;
    return route ? { route } : null;
  },
});

/**
 * Hear the buddy's five sends. `isBuddy(sender)` says whether a send came from the buddy window's own
 * web contents; `openRoute(hash)` opens a route of this app in the main window (`main.js`'s
 * `deepLink`, which shows the window and assigns the hash through `hashAssignmentScript`). A send
 * from anywhere else, or a payload outside its shape, is dropped and logged.
 */
function registerBuddyIpc({ ipcMain, buddy, isBuddy, openRoute, log = () => {} }) {
  const hear = (name, act) => {
    ipcMain.on(BUDDY_CHANNELS[name], (event, payload) => {
      if (!event || !isBuddy(event.sender)) return log(`buddy: ${name} from a page that is not the buddy's, dropped`);
      const checked = BUDDY_PAYLOADS[name](payload);
      if (!checked) return log(`buddy: ${name} outside its shape, dropped`);
      act(checked);
    });
  };
  hear("pointer", ({ hit }) => buddy.pointerOver(hit));
  hear("regions", ({ rects }) => buddy.setRegions(rects));
  hear("drag", (d) => {
    if (d.phase === "start") buddy.beginDrag();
    else if (d.phase === "move") buddy.dragBy(d.dx, d.dy);
    else buddy.endDrag({ instant: d.instant });
  });
  hear("presentation", ({ presentation }) => {
    if (presentation === "hidden") buddy.hide();
    else buddy.setPresentation(presentation);
  });
  hear("open", ({ route }) => openRoute(route));
  return () => { for (const channel of Object.values(BUDDY_CHANNELS)) ipcMain.removeAllListeners(channel); };
}

// ── This machine's buddy rows in Settings (#3901) ─────────────────────────────────────────────
//
// Settings shows and changes where the buddy's window sits and whether it is kept out of screen
// capture, which are this machine's (the shell's store), not the gateway's. Four calls on
// `pclawDesktop.buddy`, answered for the app's own bridged windows only: never the buddy's page,
// whose preload has none of them, and never another web contents. A change goes through the
// buddy's controller, so the live window moves or changes at once.

/** Where the buddy sits, as Settings shows it: docked, or floating on a display. */
function placeShown(store) {
  const at = placement.readPlacement(store);
  return at.mode === "floating" && Number.isFinite(at.displayId) ? { mode: "floating", displayId: at.displayId } : { mode: at.mode };
}

/**
 * Answer the four calls. `buddy()` is the controller (`makeBuddyWindow`) once main has made it,
 * else null; `store()` the shell's store; `isAppWindow(sender)` whether a call came from one of
 * the app's own bridged windows. A call from anywhere else is refused, and a value outside its
 * shape changes nothing.
 */
function registerBuddySettingsIpc({ ipcMain, channels, buddy, store, isAppWindow, log = () => {} }) {
  const answer = (channel, act) => {
    ipcMain.handle(channel, (event, ...args) => {
      if (!event || !isAppWindow(event.sender)) {
        log(`buddy settings: ${channel} from a page that is not the app's, refused`);
        throw new Error("not one of the app's windows");
      }
      const shell = store();
      if (!shell) throw new Error("the shell's store is not open");
      return act(shell, ...args);
    });
  };
  const captureOn = (shell) => shell.getItem(CAPTURE_KEY) !== "0";
  answer(channels.buddyGetPlace, (shell) => placeShown(shell));
  answer(channels.buddyResetPlace, (shell) => {
    const controller = buddy();
    if (controller) controller.resetPlacement();
    else placement.writePlacement(shell, { mode: "docked" });
    return placeShown(shell);
  });
  answer(channels.buddyGetHideFromCapture, (shell) => captureOn(shell));
  answer(channels.buddySetHideFromCapture, (shell, on) => {
    if (typeof on !== "boolean") {
      log("buddy settings: hide-from-capture outside its shape, unchanged");
      return captureOn(shell);
    }
    const controller = buddy();
    if (controller) controller.setHideFromCapture(on);
    else shell.setItem(CAPTURE_KEY, on ? "1" : "0");
    return captureOn(shell);
  });
  return () => {
    for (const c of [channels.buddyGetPlace, channels.buddyResetPlace, channels.buddyGetHideFromCapture, channels.buddySetHideFromCapture]) ipcMain.removeHandler(c);
  };
}

module.exports = {
  CAPTURE_KEY,
  ENABLED_KEY,
  BUDDY_CHANNELS,
  BUDDY_CONNECTIONS,
  BUDDY_PAYLOADS,
  BUDDY_PLACEMENTS,
  BUDDY_PUSHES,
  pushToBuddy,
  buddyWindowOptions,
  makeBuddyWindow,
  makeBuddyPresence,
  buddyOutlivesClose,
  registerBuddyIpc,
  registerBuddySettingsIpc,
};
