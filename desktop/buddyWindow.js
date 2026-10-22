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
 */

const placement = require("./buddyPlacement");

/** "Hide from screen sharing and screenshots": on unless this is `"0"`. */
const CAPTURE_KEY = "buddy:hideFromCapture";

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

function makeBuddyWindow({ BrowserWindowCtor, screen, store, preload, platform = process.platform, log = () => {} }) {
  let win = null;
  let presentation = "compact";
  let current = placement.readPlacement(store);
  let drag = null;
  let visible = false;

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
    if (platform === "darwin") win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    win.setAlwaysOnTop(true, "floating");
    win.setContentProtection(hideFromCapture());
    passClicks();
    win.on("closed", () => { win = null; visible = false; });
    if (url) win.loadURL(url);
    for (const event of ["display-added", "display-removed", "display-metrics-changed"]) {
      try { screen.on(event, () => place()); } catch (err) { log(`buddy: cannot follow ${event}: ${err && err.message}`); }
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
    /** Linux: the face's and the card's rectangles, in the window's own points, are its shape. */
    setRegions(rects) {
      if (!win || platform === "darwin" || typeof win.setShape !== "function") return;
      const ok = Array.isArray(rects) ? rects.filter((r) => r && [r.x, r.y, r.width, r.height].every(Number.isFinite)) : [];
      win.setShape(ok.map((r) => ({ x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) })));
    },
    /** A drag of the face starts: the window follows the pointer's deltas until `endDrag`. */
    beginDrag() {
      if (!win) return;
      const [x, y] = win.getPosition();
      drag = { x, y, out: current.mode === "floating" };
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
    },
    /** The drop: dock in the dock zone, else float where it fell; saved for this machine. `instant`
     *  under reduced motion. */
    endDrag({ instant = false } = {}) {
      if (!win || !drag) return;
      const out = drag.out;
      const [x, y] = win.getPosition();
      drag = null;
      if (!out) return place();
      current = placement.settleDrop({ faceTopLeft: { x, y }, displays: displays(), primary: primary() });
      placement.writePlacement(store, current);
      place(!instant);
    },
    /** "Hide from screen sharing and screenshots", for this machine. */
    setHideFromCapture(on) {
      store.setItem(CAPTURE_KEY, on ? "1" : "0");
      if (win) win.setContentProtection(!!on);
    },
    hideFromCapture,
  };
}

module.exports = { CAPTURE_KEY, buddyWindowOptions, makeBuddyWindow };
