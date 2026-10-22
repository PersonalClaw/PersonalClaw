/**
 * Where the buddy's window sits (#3831). Pure: Electron's `screen` is read by the caller and handed
 * in as plain display descriptions (`{id, bounds, workArea}`), so a node test drives every rule.
 *
 * Two placements, the owner's, per machine:
 *
 * - **Docked** on the display with the menu bar, hung from the top of its work area at the centre,
 *   so it covers no menu-bar item and no window control. On a notched Mac the same pill hangs just
 *   below the notch, centred on it: the notch sits at the menu bar's centre, and the work area starts
 *   below the menu bar, so one rule places both (`docs/design/buddy/surfaces.md#docking`).
 * - **Floating** where the owner dropped it, on the display it was dropped on, kept inside that
 *   display's work area. A display that is gone docks it on the menu-bar display instead.
 *
 * A drag undocks once the face is more than `UNDOCK_DISTANCE` below the work area's top, and a drop
 * within `DOCK_ZONE` of the top centre docks it again. The placement is saved as one JSON string
 * under `PLACEMENT_KEY` in the shell's store (`shellStore.js`), which holds strings only.
 */

/** The room each presentation needs, in points (`surfaces.md#the-room-budget`'s starting values). */
const SIZES = Object.freeze({
  hidden: Object.freeze({ width: 56, height: 28 }),
  peek: Object.freeze({ width: 56, height: 28 }),
  compact: Object.freeze({ width: 360, height: 28 }),
  expanded: Object.freeze({ width: 420, height: 360 }),
});
/** The floating face, and the gap between it and the card that opens beside it. */
const FLOAT_FACE = 56;
const FLOAT_GAP = 8;

/** Starting values; the playground (#3892) tunes them and `surfaces.md` records them. */
const UNDOCK_DISTANCE = 48;
const DOCK_ZONE = 96;

const PLACEMENT_KEY = "buddy:placement";
const MODES = new Set(["docked", "floating"]);
const PRESENTATIONS = new Set(Object.keys(SIZES));

const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);

/** A display description is usable when its bounds and work area are four numbers each. */
function usableDisplay(d) {
  const ok = (r) => r && [r.x, r.y, r.width, r.height].every((n) => num(n) !== null) && r.width > 0 && r.height > 0;
  return !!d && ok(d.bounds) && ok(d.workArea);
}

/** The menu bar's height on `display`: where its work area starts below its top. */
function menuBarHeight(display) {
  return Math.max(0, display.workArea.y - display.bounds.y);
}

/** Whether `display` has a camera notch. Electron's `Display` says nothing of one, but a notched
 *  Mac's menu bar is as tall as the notch (37 or 38 points) where any other is 24 or 25. */
function hasNotch(display, platform = process.platform) {
  return platform === "darwin" && menuBarHeight(display) >= 32;
}

const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), Math.max(lo, hi));

/** The window's size for a presentation, floating or docked. Floating, the card opens beside the
 *  face, so an expanded window holds both. */
function sizeFor(mode, presentation) {
  const p = PRESENTATIONS.has(presentation) ? presentation : "compact";
  if (mode === "floating") {
    if (p === "expanded") return { width: FLOAT_FACE + FLOAT_GAP + SIZES.expanded.width, height: SIZES.expanded.height };
    return { width: FLOAT_FACE, height: FLOAT_FACE };
  }
  return { ...SIZES[p] };
}

/** Docked bounds: centred on `display`, hung from the top of its work area. */
function dockedBounds(display, presentation) {
  const { width, height } = sizeFor("docked", presentation);
  const wa = display.workArea;
  return { x: Math.round(display.bounds.x + (display.bounds.width - width) / 2), y: wa.y, width, height };
}

/** Floating bounds at the face's saved top-left, kept inside `display`'s work area. */
function floatingBounds(display, at, presentation) {
  const { width, height } = sizeFor("floating", presentation);
  const wa = display.workArea;
  return {
    x: Math.round(clamp(at.x, wa.x, wa.x + wa.width - width)),
    y: Math.round(clamp(at.y, wa.y, wa.y + wa.height - height)),
    width,
    height,
  };
}

/** The display a point is on, else the one nearest it. */
function displayAt(displays, point) {
  const inside = displays.find((d) => point.x >= d.bounds.x && point.x < d.bounds.x + d.bounds.width
    && point.y >= d.bounds.y && point.y < d.bounds.y + d.bounds.height);
  if (inside) return inside;
  let best = null;
  let bestDistance = Infinity;
  for (const d of displays) {
    const cx = clamp(point.x, d.bounds.x, d.bounds.x + d.bounds.width);
    const cy = clamp(point.y, d.bounds.y, d.bounds.y + d.bounds.height);
    const distance = Math.hypot(point.x - cx, point.y - cy);
    if (distance < bestDistance) { best = d; bestDistance = distance; }
  }
  return best;
}

/** Whether a drag of a docked buddy has gone far enough down to undock it: its face's top is more
 *  than `UNDOCK_DISTANCE` below the top of the work area it was docked on. */
function draggedOut(display, faceTop) {
  return faceTop - display.workArea.y > UNDOCK_DISTANCE;
}

/** Whether a drop docks: the face's centre within `DOCK_ZONE` of the top centre of the work area of
 *  the menu-bar display. */
function dropsInDock(primary, faceCentre) {
  const topCentre = { x: primary.bounds.x + primary.bounds.width / 2, y: primary.workArea.y };
  return Math.hypot(faceCentre.x - topCentre.x, faceCentre.y - topCentre.y) <= DOCK_ZONE;
}

/** The saved placement, or docked when nothing (or nothing readable) is saved. */
function readPlacement(store) {
  let raw = null;
  try { raw = JSON.parse(store.getItem(PLACEMENT_KEY) || "null"); } catch { raw = null; }
  if (!raw || typeof raw !== "object" || !MODES.has(raw.mode)) return { mode: "docked" };
  if (raw.mode === "docked") return { mode: "docked" };
  const x = num(raw.x);
  const y = num(raw.y);
  if (x === null || y === null) return { mode: "docked" };
  return { mode: "floating", displayId: num(raw.displayId), x, y };
}

/** Save `placement` as one string under the buddy's key, and nothing else. */
function writePlacement(store, placement) {
  const value = placement.mode === "floating"
    ? { mode: "floating", displayId: placement.displayId ?? null, x: Math.round(placement.x), y: Math.round(placement.y) }
    : { mode: "docked" };
  store.setItem(PLACEMENT_KEY, JSON.stringify(value));
}

/** Where the window goes for `placement` and `presentation` on the displays there are now. A
 *  floating buddy whose display is gone docks on the menu-bar display. */
function resolveBounds({ placement, presentation, displays, primary }) {
  const shown = displays.filter(usableDisplay);
  const menuBar = usableDisplay(primary) ? primary : shown[0];
  if (!menuBar) return null;
  if (placement.mode === "floating") {
    const home = shown.find((d) => d.id === placement.displayId);
    if (home) return { mode: "floating", displayId: home.id, bounds: floatingBounds(home, placement, presentation) };
  }
  return { mode: "docked", displayId: menuBar.id, bounds: dockedBounds(menuBar, presentation) };
}

/** Where a drag of the face ends. `faceTopLeft` is the face's top-left on the screen at release.
 *  A drop in the dock zone docks; otherwise it floats where it was dropped, on the display it was
 *  dropped on, kept inside that display. */
function settleDrop({ faceTopLeft, displays, primary }) {
  const shown = displays.filter(usableDisplay);
  const menuBar = usableDisplay(primary) ? primary : shown[0];
  if (!menuBar) return { mode: "docked" };
  const centre = { x: faceTopLeft.x + FLOAT_FACE / 2, y: faceTopLeft.y + FLOAT_FACE / 2 };
  if (dropsInDock(menuBar, centre)) return { mode: "docked" };
  const on = displayAt(shown, centre) || menuBar;
  const kept = floatingBounds(on, faceTopLeft, "compact");
  return { mode: "floating", displayId: on.id, x: kept.x, y: kept.y };
}

module.exports = {
  SIZES,
  FLOAT_FACE,
  UNDOCK_DISTANCE,
  DOCK_ZONE,
  PLACEMENT_KEY,
  menuBarHeight,
  hasNotch,
  sizeFor,
  dockedBounds,
  floatingBounds,
  displayAt,
  draggedOut,
  dropsInDock,
  readPlacement,
  writePlacement,
  resolveBounds,
  settleDrop,
};
