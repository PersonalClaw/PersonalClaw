/**
 * Preload for the buddy's window (#3831).
 *
 * Five named sends and nothing else. Deliberately NOT `preload.js`: the buddy has no business
 * reaching the capability bridge (the microphone, notifications, the login item), so it gets a
 * surface of its own, the smallest that drives its window: where the pointer is, the shape that
 * takes clicks, a drag of the face, the room it needs, and opening a route of this app in the main
 * window. No `send` or `invoke` of the page's choosing is exposed, and nothing comes back.
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
});
