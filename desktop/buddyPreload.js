/**
 * Preload for the buddy's window (#3831).
 *
 * Five named sends, four named pushes back (#3832), and nothing else. Deliberately NOT `preload.js`: the buddy has no business
 * reaching the capability bridge (the microphone, notifications, the login item), so it gets a
 * surface of its own, the smallest that drives its window: where the pointer is, the shape that
 * takes clicks, a drag of the face, the room it needs, and opening a route of this app in the main
 * window. No `send` or `invoke` of the page's choosing is exposed. What comes back is main's
 * doorbell, a ring that carries nothing (the lists moved: read them again) and its connection, one
 * of three words; where the window is (moving, docked or floating); and whether one of the app's own
 * windows is full screen.
 *
 * The window is sandboxed, and a sandboxed preload's `require` resolves `electron` and three
 * builtins only, so the channel names are copied here; `buddyBridge.test.js` holds them equal to
 * `buddyWindow.js`'s `BUDDY_CHANNELS`. Main checks every payload again and hears these sends only
 * from the buddy window's own web contents (`registerBuddyIpc`).
 */

const { contextBridge, ipcRenderer } = require("electron");

const BUDDY_PREFIX = "pclaw-buddy:";
const BUDDY_CHANNELS = Object.freeze({
  pointer: `${BUDDY_PREFIX}pointer`,
  regions: `${BUDDY_PREFIX}regions`,
  drag: `${BUDDY_PREFIX}drag`,
  presentation: `${BUDDY_PREFIX}presentation`,
  open: `${BUDDY_PREFIX}open`,
});
const BUDDY_PUSHES = Object.freeze({
  ring: `${BUDDY_PREFIX}ring`,
  connection: `${BUDDY_PREFIX}connection`,
  placement: `${BUDDY_PREFIX}placement`,
  fullScreen: `${BUDDY_PREFIX}full-screen`,
  openAlert: `${BUDDY_PREFIX}open-alert`,
});
const BUDDY_CONNECTIONS = ["up", "lost", "restarting"];
const BUDDY_PLACEMENTS = ["moving", "docked", "floating"];

/** Hear *channel*, handing *cb* only what *shape* lets through; returns the way to stop. */
function hear(channel, cb, shape) {
  const listener = (_event, value) => {
    const checked = shape(value);
    if (checked !== undefined) cb(checked);
  };
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

/** A ring to open on an alert that came while the page was not listening: the tap that rings the
 *  buddy may also be what shows its window, so its page can mount after the ring. Kept, and handed
 *  to the next listener once. */
let openAlertListeners = 0;
let openAlertWaiting = false;
ipcRenderer.on(BUDDY_PUSHES.openAlert, () => {
  if (openAlertListeners === 0) openAlertWaiting = true;
});

const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : NaN);
const rect = (r) => ({ x: num(r && r.x), y: num(r && r.y), width: num(r && r.width), height: num(r && r.height) });

contextBridge.exposeInMainWorld("pclawBuddy", {
  /** macOS: whether the pointer is over the face or the open card (take clicks) or not (pass them). */
  pointerOver: (hit) => ipcRenderer.send(BUDDY_CHANNELS.pointer, { hit: hit === true }),
  /** Linux: the face's and the card's rectangles, in the window's own points. */
  setRegions: (rects) => ipcRenderer.send(BUDDY_CHANNELS.regions, { rects: Array.isArray(rects) ? rects.slice(0, 8).map(rect) : [] }),
  /** A drag of the face: `start`, each `move` by the pointer's delta, and the `end` (instant under
   *  reduced motion). */
  dragStart: () => ipcRenderer.send(BUDDY_CHANNELS.drag, { phase: "start" }),
  dragBy: (dx, dy) => ipcRenderer.send(BUDDY_CHANNELS.drag, { phase: "move", dx: num(dx), dy: num(dy) }),
  dragEnd: (instant) => ipcRenderer.send(BUDDY_CHANNELS.drag, { phase: "end", instant: instant === true }),
  /** The room the buddy needs: `peek`, `compact`, `expanded`, or `hidden`, which hides the window. */
  setPresentation: (presentation) => ipcRenderer.send(BUDDY_CHANNELS.presentation, { presentation: String(presentation) }),
  /** Open a route of this app (`#/chat/…`) in the main window: the focused work. */
  openInApp: (route) => ipcRenderer.send(BUDDY_CHANNELS.open, { route: String(route) }),
  /** The doorbell rang: something running or waiting changed. Carries nothing. */
  onRing: (cb) => hear(BUDDY_PUSHES.ring, () => cb(), () => null),
  /** The doorbell's connection: `up`, `lost` or `restarting`. */
  onConnection: (cb) => hear(BUDDY_PUSHES.connection, cb, (v) => (BUDDY_CONNECTIONS.includes(v) ? v : undefined)),
  /** Where the window is: `moving` once a drag moves it, then `docked` or `floating` where it settled. */
  onPlacement: (cb) => hear(BUDDY_PUSHES.placement, cb, (v) => (BUDDY_PLACEMENTS.includes(v) ? v : undefined)),
  /** Whether one of the app's own windows is full screen. */
  onFullScreen: (cb) => hear(BUDDY_PUSHES.fullScreen, cb, (v) => (typeof v === "boolean" ? v : undefined)),
  /** A tap on an alert's notification (#3897): open as a click on the orb opens. Carries nothing;
   *  a ring that came before the page listened is handed over once. */
  onOpenAlert: (cb) => {
    if (typeof cb !== "function") return () => {};
    const stop = hear(BUDDY_PUSHES.openAlert, () => cb(), () => null);
    openAlertListeners += 1;
    if (openAlertWaiting) {
      openAlertWaiting = false;
      cb();
    }
    let stopped = false;
    return () => {
      if (stopped) return;
      stopped = true;
      openAlertListeners -= 1;
      stop();
    };
  },
});
