/**
 * Where the buddy's orb sits, and where its card opens (#5216). Pure: Electron's `screen` is read
 * by the caller and handed in as plain display descriptions (`{id, bounds, workArea}`), so a node
 * test drives every rule.
 *
 * - **One orb of one size, where the owner put it.** The orb's box is `ORB_BOX` square, and only a
 *   drag moves it: no state, presentation, alert, away or hide does.
 * - **The default**, until the first move, is centred on the menu-bar display just below the top of
 *   its work area: below the menu bar, and on a notched Mac below the notch, since the notch sits at
 *   the menu bar's centre and the work area starts below it (`docs/design/buddy/surfaces.md`).
 * - **The card opens beside the orb, on the side with room** (`layoutAt`): right of it where the
 *   card fits, else left, opening down from the orb's top where it fits, else up from its bottom;
 *   where neither side fits, below the orb or above it. It never covers the orb. The side is chosen
 *   when the orb settles, never when the card opens, so the window (the orb's box and the card's
 *   area) is never resized for what the buddy shows.
 * - **Kept per display**, across a restart, as one JSON string under `PLACEMENT_KEY` in the shell's
 *   store (`shellStore.js`, strings only): `{v: 2, on, spots}`, each spot relative to its display's
 *   work area, so a moved display arrangement keeps it. A display that's gone puts the orb at the
 *   default and keeps its spot, and the orb goes back there when it returns. A drop within
 *   `DOCK_ZONE` of the default snaps to the default, so it can always be put back by hand.
 */

/** The orb's box, in points: one size in every state (the floating face's, before #5216). */
const ORB_BOX = 56;
/** The gap between the orb and its card. */
const CARD_GAP = 8;
/** The card's largest area (`surfaces.md`'s expanded card): its width, and its height at most. */
const CARD_BUDGET = Object.freeze({ width: 420, maxHeight: 560 });
/** How far below the top of the work area the default spot sits. */
const DEFAULT_TOP_GAP = 8;
/** A drop whose orb's centre is this near the default's snaps to the default. */
const DOCK_ZONE = 96;

const PLACEMENT_KEY = "buddy:placement";
/** What the page may say it presents: the orb alone, its line card, or its full card. Only `hidden`
 *  changes the window: it hides it. */
const PRESENTATIONS = Object.freeze(["hidden", "orb", "line", "card"]);

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

/** The default spot's top-left on `display`: centred, just below the top of its work area. */
function defaultSpot(display) {
  return { x: Math.round(display.bounds.x + (display.bounds.width - ORB_BOX) / 2), y: display.workArea.y + DEFAULT_TOP_GAP };
}

/** `orb` (a top-left) kept so the whole orb is inside `display`'s work area. */
function keptInside(display, orb) {
  const wa = display.workArea;
  return {
    x: Math.round(clamp(orb.x, wa.x, wa.x + wa.width - ORB_BOX)),
    y: Math.round(clamp(orb.y, wa.y, wa.y + wa.height - ORB_BOX)),
  };
}

/**
 * The window for an orb whose top-left is `orb` on `display`: `{side, opens, window, orb, card}`.
 * `window` is on the screen; `orb` and `card` are in the window's own points. The card's area is
 * the card's budget on the side with room, which the card draws inside, anchored to the orb: from
 * the orb's top when it opens down, from its bottom when it opens up.
 */
function layoutAt(display, orb, budget = CARD_BUDGET) {
  const wa = display.workArea;
  const right = wa.x + wa.width;
  const bottom = wa.y + wa.height;
  const cardWidth = Math.min(budget.width, wa.width);
  const orbBox = { x: orb.x, y: orb.y, width: ORB_BOX, height: ORB_BOX };
  const fitsRight = orb.x + ORB_BOX + CARD_GAP + cardWidth <= right;
  const fitsLeft = orb.x - CARD_GAP - cardWidth >= wa.x;
  let side;
  let opens;
  let card;
  if (fitsRight || fitsLeft) {
    side = fitsRight ? "right" : "left";
    const height = Math.min(budget.maxHeight, wa.height);
    const x = side === "right" ? orb.x + ORB_BOX + CARD_GAP : orb.x - CARD_GAP - cardWidth;
    let y;
    if (orb.y + height <= bottom) {
      opens = "down";
      y = orb.y;
    } else if (orb.y + ORB_BOX - height >= wa.y) {
      opens = "up";
      y = orb.y + ORB_BOX - height;
    } else {
      opens = "down";
      y = Math.max(wa.y, bottom - height);
    }
    card = { x, y, width: cardWidth, height };
  } else {
    const below = bottom - (orb.y + ORB_BOX + CARD_GAP);
    const above = orb.y - CARD_GAP - wa.y;
    side = below >= above ? "below" : "above";
    opens = side === "below" ? "down" : "up";
    const height = Math.max(0, Math.min(budget.maxHeight, side === "below" ? below : above));
    const x = Math.round(clamp(orb.x + ORB_BOX / 2 - cardWidth / 2, wa.x, right - cardWidth));
    const y = side === "below" ? orb.y + ORB_BOX + CARD_GAP : orb.y - CARD_GAP - height;
    card = { x, y, width: cardWidth, height };
  }
  const x = Math.min(orbBox.x, card.x);
  const y = Math.min(orbBox.y, card.y);
  const window = {
    x,
    y,
    width: Math.max(orbBox.x + orbBox.width, card.x + card.width) - x,
    height: Math.max(orbBox.y + orbBox.height, card.y + card.height) - y,
  };
  const within = (r) => ({ x: r.x - x, y: r.y - y, width: r.width, height: r.height });
  return { side, opens, window, orb: within(orbBox), card: within(card) };
}

/** The display a point is on, else the one nearest it. */
function displayAt(displays, point) {
  const hit = displays.find((d) => point.x >= d.bounds.x && point.x < d.bounds.x + d.bounds.width
    && point.y >= d.bounds.y && point.y < d.bounds.y + d.bounds.height);
  if (hit) return hit;
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

const AT_DEFAULT = () => ({ on: null, spots: {} });

/** The saved spots, or the default when nothing (or nothing readable) is saved. The window's
 *  earlier placement is read too: `docked` is the default, and `floating` a spot on its display,
 *  if *displays* still has that display. */
function readPlacement(store, displays = []) {
  let raw = null;
  try { raw = JSON.parse(store.getItem(PLACEMENT_KEY) || "null"); } catch { raw = null; }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return AT_DEFAULT();
  if (raw.v === 2) {
    const spots = {};
    if (raw.spots && typeof raw.spots === "object" && !Array.isArray(raw.spots)) {
      for (const [id, spot] of Object.entries(raw.spots)) {
        const x = num(spot && spot.x);
        const y = num(spot && spot.y);
        if (Number.isFinite(Number(id)) && x !== null && y !== null) spots[Number(id)] = { x, y };
      }
    }
    const on = num(raw.on);
    return { on: on !== null && spots[on] ? on : null, spots };
  }
  if (raw.mode === "floating") {
    const d = displays.filter(usableDisplay).find((s) => s.id === num(raw.displayId));
    const x = num(raw.x);
    const y = num(raw.y);
    if (d && x !== null && y !== null) return { on: d.id, spots: { [d.id]: { x: x - d.workArea.x, y: y - d.workArea.y } } };
  }
  return AT_DEFAULT();
}

/** The display the orb was last put on, as the store says without the screen; null at the default.
 *  The window's earlier `floating` placement names its display too. */
function savedDisplay(store) {
  const at = readPlacement(store);
  if (at.on !== null) return at.on;
  let raw = null;
  try { raw = JSON.parse(store.getItem(PLACEMENT_KEY) || "null"); } catch { raw = null; }
  const earlier = raw && raw.mode === "floating" && [raw.displayId, raw.x, raw.y].every((n) => num(n) !== null);
  return earlier ? raw.displayId : null;
}

/** Save `placement` as one string under the buddy's key, and nothing else. */
function writePlacement(store, placement) {
  const spots = {};
  for (const [id, spot] of Object.entries(placement.spots || {})) spots[id] = { x: Math.round(spot.x), y: Math.round(spot.y) };
  store.setItem(PLACEMENT_KEY, JSON.stringify({ v: 2, on: placement.on ?? null, spots }));
}

/**
 * Where the orb is for `placement` on the displays there are now: `{atDefault, displayId, orb,
 * layout}`, `orb` its top-left on the screen. The spot on the display it was last dropped on, kept
 * inside that display; the default on the menu-bar display while that display is gone.
 */
function resolvePlacement({ placement, displays, primary, budget = CARD_BUDGET }) {
  const shown = displays.filter(usableDisplay);
  const menuBar = usableDisplay(primary) ? primary : shown[0];
  if (!menuBar) return null;
  const on = placement && placement.on;
  const home = on !== null && on !== undefined ? shown.find((d) => d.id === on) : null;
  const spot = home && placement.spots ? placement.spots[on] : null;
  if (home && spot) {
    const orb = keptInside(home, { x: home.workArea.x + spot.x, y: home.workArea.y + spot.y });
    return { atDefault: false, displayId: home.id, orb, layout: layoutAt(home, orb, budget) };
  }
  const orb = defaultSpot(menuBar);
  return { atDefault: true, displayId: menuBar.id, orb, layout: layoutAt(menuBar, orb, budget) };
}

/** The placement after a drop of the orb at `orb` (its top-left on the screen): the default, if the
 *  orb's centre is within `DOCK_ZONE` of the default's; else the display it was dropped on, at that
 *  spot kept inside it, with every other display's spot kept. */
function settleDrop({ orb, placement, displays, primary }) {
  const shown = displays.filter(usableDisplay);
  const menuBar = usableDisplay(primary) ? primary : shown[0];
  const spots = { ...((placement && placement.spots) || {}) };
  if (!menuBar) return { on: null, spots };
  const centre = { x: orb.x + ORB_BOX / 2, y: orb.y + ORB_BOX / 2 };
  const home = defaultSpot(menuBar);
  if (Math.hypot(centre.x - (home.x + ORB_BOX / 2), centre.y - (home.y + ORB_BOX / 2)) <= DOCK_ZONE) return { on: null, spots };
  const display = displayAt(shown, centre) || menuBar;
  const kept = keptInside(display, orb);
  spots[display.id] = { x: kept.x - display.workArea.x, y: kept.y - display.workArea.y };
  return { on: display.id, spots };
}

module.exports = {
  ORB_BOX,
  CARD_GAP,
  CARD_BUDGET,
  DEFAULT_TOP_GAP,
  DOCK_ZONE,
  PLACEMENT_KEY,
  PRESENTATIONS,
  menuBarHeight,
  hasNotch,
  defaultSpot,
  layoutAt,
  displayAt,
  readPlacement,
  savedDisplay,
  writePlacement,
  resolvePlacement,
  settleDrop,
};
