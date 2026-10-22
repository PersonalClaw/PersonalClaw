const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const {
  ORB_BOX,
  CARD_GAP,
  CARD_BUDGET,
  DOCK_ZONE,
  PLACEMENT_KEY,
  hasNotch,
  defaultSpot,
  layoutAt,
  readPlacement,
  writePlacement,
  resolvePlacement,
  settleDrop,
} = require("../buddyPlacement");

// ── Where the buddy's orb sits, and where its card opens (#5216) ──────────────────────────────
//
// One orb of one constant size, where the owner put it: only a drag moves it. Until the first move
// it sits at the default, centred below the menu bar, which on a notched Mac is below the notch.
// Its window is the orb's box and a card area on the side of the orb that has room, chosen when the
// orb settles, so neither a state nor the card ever resizes or moves the orb. The spot is kept per
// display, relative to its work area, across a restart; a display that's gone puts the orb at the
// default and keeps its spot for when it is back.

/** A 14" MacBook Pro: 1512 × 982 points, its menu bar as tall as the notch. */
const NOTCHED = { id: 1, bounds: { x: 0, y: 0, width: 1512, height: 982 }, workArea: { x: 0, y: 38, width: 1512, height: 944 } };
/** An external display to its right, with an ordinary menu bar. */
const EXTERNAL = { id: 2, bounds: { x: 1512, y: 0, width: 2560, height: 1440 }, workArea: { x: 1512, y: 25, width: 2560, height: 1415 } };
/** A Linux desktop with a top panel. */
const LINUX = { id: 7, bounds: { x: 0, y: 0, width: 1920, height: 1080 }, workArea: { x: 0, y: 32, width: 1920, height: 1048 } };
/** A small display, narrower than the orb and a card side by side. */
const NARROW = { id: 9, bounds: { x: 0, y: 0, width: 480, height: 800 }, workArea: { x: 0, y: 24, width: 480, height: 776 } };

function memoryStore(initial = {}) {
  const data = { ...initial };
  const writes = [];
  return {
    data,
    writes,
    getItem: (k) => (Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null),
    setItem: (k, v) => { writes.push(k); data[String(k)] = String(v); },
    removeItem: (k) => { delete data[k]; },
  };
}

const overlaps = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
const inside = (r, area) => r.x >= area.x && r.y >= area.y && r.x + r.width <= area.x + area.width && r.y + r.height <= area.y + area.height;
/** A layout's rectangle on the screen, from its window-relative one. */
const onScreen = (layout, r) => ({ x: layout.window.x + r.x, y: layout.window.y + r.y, width: r.width, height: r.height });

describe("the default spot", () => {
  it("is just below the notch on a notched Mac, centred on it", () => {
    assert.equal(hasNotch(NOTCHED, "darwin"), true);
    const at = defaultSpot(NOTCHED);
    assert.equal(at.x + ORB_BOX / 2, NOTCHED.bounds.width / 2, "centred on the notch");
    assert.ok(at.y >= NOTCHED.workArea.y, "below the notch, covering none of the menu bar");
    assert.ok(at.y - NOTCHED.workArea.y <= 12, "just below it");
  });

  it("is centred below the menu bar or panel of a display without a notch", () => {
    assert.equal(hasNotch(LINUX, "linux"), false);
    const at = defaultSpot(LINUX);
    assert.equal(at.x, (LINUX.bounds.width - ORB_BOX) / 2);
    assert.ok(at.y >= LINUX.workArea.y);
  });
});

describe("the orb's box and the card's side", () => {
  /** Every edge and corner of *display*, and its centre, as orb top-lefts. */
  function spots(d) {
    const wa = d.workArea;
    const xs = [wa.x, wa.x + (wa.width - ORB_BOX) / 2, wa.x + wa.width - ORB_BOX];
    const ys = [wa.y, wa.y + (wa.height - ORB_BOX) / 2, wa.y + wa.height - ORB_BOX];
    return xs.flatMap((x) => ys.map((y) => ({ x, y })));
  }

  for (const display of [NOTCHED, EXTERNAL, LINUX, NARROW]) {
    for (const width of [320, 390, 412, CARD_BUDGET.width]) {
      it(`on a ${display.bounds.width}-wide display with a ${width}-wide card: the orb stays where it is, and the card has room and never covers it`, () => {
        for (const orb of spots(display)) {
          const layout = layoutAt(display, orb, { ...CARD_BUDGET, width });
          const orbRect = onScreen(layout, layout.orb);
          const cardRect = onScreen(layout, layout.card);
          assert.deepEqual(orbRect, { x: orb.x, y: orb.y, width: ORB_BOX, height: ORB_BOX }, "the orb's box is where it was put, at its one size");
          assert.equal(overlaps(orbRect, cardRect), false, `${layout.side}/${layout.opens}: the card never covers the orb`);
          assert.ok(inside(cardRect, display.workArea), `${layout.side}/${layout.opens} at ${orb.x},${orb.y}: the card is on the display`);
          assert.ok(cardRect.width <= width, "the card keeps to its budget");
          assert.ok(inside(layout.orb, { x: 0, y: 0, width: layout.window.width, height: layout.window.height }));
          assert.ok(inside(layout.card, { x: 0, y: 0, width: layout.window.width, height: layout.window.height }));
        }
      });
    }
  }

  it("opens right where there is room, else left; down where there is room, else up", () => {
    const wa = LINUX.workArea;
    assert.deepEqual(pick(layoutAt(LINUX, { x: wa.x, y: wa.y })), ["right", "down"]);
    assert.deepEqual(pick(layoutAt(LINUX, { x: wa.x + wa.width - ORB_BOX, y: wa.y })), ["left", "down"]);
    assert.deepEqual(pick(layoutAt(LINUX, { x: wa.x, y: wa.y + wa.height - ORB_BOX })), ["right", "up"]);
    assert.deepEqual(pick(layoutAt(LINUX, { x: wa.x + wa.width - ORB_BOX, y: wa.y + wa.height - ORB_BOX })), ["left", "up"]);
  });

  it("goes below the orb, or above it, where neither side has room", () => {
    const wa = NARROW.workArea;
    const middle = wa.x + (wa.width - ORB_BOX) / 2;
    assert.equal(layoutAt(NARROW, { x: middle, y: wa.y }).side, "below");
    assert.equal(layoutAt(NARROW, { x: middle, y: wa.y + wa.height - ORB_BOX }).side, "above");
  });

  it("keeps the card a gap away from the orb, anchored to it", () => {
    const layout = layoutAt(LINUX, { x: 100, y: 200 });
    assert.equal(layout.card.x - (layout.orb.x + ORB_BOX), CARD_GAP, "right of the orb, one gap away");
    assert.equal(layout.card.y, layout.orb.y, "opening down from the orb's top");
    const up = layoutAt(LINUX, { x: 100, y: LINUX.workArea.y + LINUX.workArea.height - ORB_BOX });
    assert.equal(up.card.y + up.card.height, up.orb.y + ORB_BOX, "opening up from the orb's bottom");
  });

  it("is the page's own layout where no shell says: `cardPlacement.ts` lays out the same at every spot", async () => {
    // The playground and a browser tab lay the buddy out with the page's port of this rule, so a
    // change to one that the other does not take is a playground that no longer shows the window.
    const page = await import(pathToFileURL(path.join(__dirname, "..", "..", "web", "src", "lib", "buddy", "cardPlacement.ts")).href);
    assert.equal(page.ORB_BOX, ORB_BOX);
    assert.equal(page.CARD_GAP, CARD_GAP);
    assert.deepEqual({ ...page.CARD_BUDGET }, { ...CARD_BUDGET });
    let compared = 0;
    for (const display of [NOTCHED, EXTERNAL, LINUX, NARROW]) {
      for (const width of [320, 390, 412, CARD_BUDGET.width]) {
        for (const orb of spots(display)) {
          const budget = { ...CARD_BUDGET, width };
          const shell = layoutAt(display, orb, budget);
          const theirs = page.layoutAt(display.workArea, orb, budget);
          const where = `${display.id} at ${orb.x},${orb.y}, ${width} wide`;
          assert.deepEqual([shell.side, shell.opens], [theirs.side, theirs.opens], where);
          assert.deepEqual(onScreen(shell, shell.orb), theirs.orb, `${where}: the orb`);
          assert.deepEqual(onScreen(shell, shell.card), theirs.card, `${where}: the card's area`);
          compared += 1;
        }
      }
    }
    assert.ok(compared >= 100);
  });

  it("is the same layout for the same spot, whatever the buddy shows: nothing but the spot decides it", () => {
    assert.deepEqual(layoutAt(NOTCHED, defaultSpot(NOTCHED)), layoutAt(NOTCHED, defaultSpot(NOTCHED)));
    assert.equal(layoutAt.length, 2, "the spot and the display, and the budget only as an option: no state goes in");
  });
});

const pick = (layout) => [layout.side, layout.opens];

describe("kept per display", () => {
  it("starts at the default, with nothing saved", () => {
    const at = resolvePlacement({ placement: readPlacement(memoryStore()), displays: [NOTCHED, EXTERNAL], primary: NOTCHED });
    assert.equal(at.atDefault, true);
    assert.equal(at.displayId, NOTCHED.id);
    assert.deepEqual(at.orb, defaultSpot(NOTCHED));
  });

  it("comes back where it was dropped, on its display, after a restart", () => {
    const store = memoryStore();
    const dropped = settleDrop({ orb: { x: 2000, y: 600 }, placement: readPlacement(store), displays: [NOTCHED, EXTERNAL], primary: NOTCHED });
    writePlacement(store, dropped);
    const again = resolvePlacement({ placement: readPlacement(store), displays: [NOTCHED, EXTERNAL], primary: NOTCHED });
    assert.equal(again.atDefault, false);
    assert.equal(again.displayId, EXTERNAL.id);
    assert.deepEqual(again.orb, { x: 2000, y: 600 });
  });

  it("is kept relative to its display, so a moved display arrangement keeps the spot", () => {
    const store = memoryStore();
    writePlacement(store, settleDrop({ orb: { x: 2000, y: 600 }, placement: readPlacement(store), displays: [NOTCHED, EXTERNAL], primary: NOTCHED }));
    const movedLeft = { ...EXTERNAL, bounds: { ...EXTERNAL.bounds, x: -2560 }, workArea: { ...EXTERNAL.workArea, x: -2560 } };
    const at = resolvePlacement({ placement: readPlacement(store), displays: [NOTCHED, movedLeft], primary: NOTCHED });
    assert.deepEqual(at.orb, { x: 2000 - 1512 - 2560, y: 600 });
  });

  it("goes to the default while its display is gone, and back to its spot there when it returns", () => {
    const store = memoryStore();
    writePlacement(store, settleDrop({ orb: { x: 2000, y: 600 }, placement: readPlacement(store), displays: [NOTCHED, EXTERNAL], primary: NOTCHED }));
    const gone = resolvePlacement({ placement: readPlacement(store), displays: [NOTCHED], primary: NOTCHED });
    assert.equal(gone.atDefault, true);
    assert.deepEqual(gone.orb, defaultSpot(NOTCHED));
    const back = resolvePlacement({ placement: readPlacement(store), displays: [NOTCHED, EXTERNAL], primary: NOTCHED });
    assert.deepEqual(back.orb, { x: 2000, y: 600 });
  });

  it("keeps each display's own spot, and the one dropped on last is where the orb is", () => {
    let placement = readPlacement(memoryStore());
    placement = settleDrop({ orb: { x: 2000, y: 600 }, placement, displays: [NOTCHED, EXTERNAL], primary: NOTCHED });
    placement = settleDrop({ orb: { x: 300, y: 500 }, placement, displays: [NOTCHED, EXTERNAL], primary: NOTCHED });
    assert.equal(placement.on, NOTCHED.id);
    assert.deepEqual(Object.keys(placement.spots).map(Number).sort(), [NOTCHED.id, EXTERNAL.id]);
    assert.deepEqual(resolvePlacement({ placement, displays: [NOTCHED, EXTERNAL], primary: NOTCHED }).orb, { x: 300, y: 500 });
  });

  it("snaps a drop near the default back to the default", () => {
    const home = defaultSpot(NOTCHED);
    const placement = settleDrop({ orb: { x: home.x + DOCK_ZONE / 2, y: home.y + 10 }, placement: readPlacement(memoryStore()), displays: [NOTCHED], primary: NOTCHED });
    const at = resolvePlacement({ placement, displays: [NOTCHED], primary: NOTCHED });
    assert.equal(at.atDefault, true);
    assert.deepEqual(at.orb, home);
  });

  it("keeps a dropped orb inside its display's work area", () => {
    const placement = settleDrop({ orb: { x: 5000, y: -40 }, placement: readPlacement(memoryStore()), displays: [NOTCHED, EXTERNAL], primary: NOTCHED });
    const at = resolvePlacement({ placement, displays: [NOTCHED, EXTERNAL], primary: NOTCHED });
    assert.ok(inside({ ...at.orb, width: ORB_BOX, height: ORB_BOX }, EXTERNAL.workArea));
  });

  it("is one string under the buddy's key, and writes no other key", () => {
    const store = memoryStore();
    writePlacement(store, settleDrop({ orb: { x: 300, y: 500 }, placement: readPlacement(store), displays: [NOTCHED], primary: NOTCHED }));
    assert.deepEqual(store.writes, [PLACEMENT_KEY]);
    assert.equal(JSON.parse(store.data[PLACEMENT_KEY]).v, 2);
  });

  it("reads the default from nothing saved, or from anything it cannot read", () => {
    for (const raw of [undefined, "not json", "null", "[]", '{"v":2,"on":"x","spots":{}}', '{"mode":"sideways"}', '{"v":2,"on":1,"spots":{"1":{"x":"a","y":2}}}']) {
      const store = memoryStore(raw === undefined ? {} : { [PLACEMENT_KEY]: raw });
      const at = resolvePlacement({ placement: readPlacement(store), displays: [NOTCHED], primary: NOTCHED });
      assert.equal(at.atDefault, true, String(raw));
    }
  });

  it("reads the window's earlier placement: docked is the default, floating is a spot on its display", () => {
    const docked = memoryStore({ [PLACEMENT_KEY]: JSON.stringify({ mode: "docked" }) });
    assert.equal(resolvePlacement({ placement: readPlacement(docked, [NOTCHED, EXTERNAL]), displays: [NOTCHED, EXTERNAL], primary: NOTCHED }).atDefault, true);
    const floating = memoryStore({ [PLACEMENT_KEY]: JSON.stringify({ mode: "floating", displayId: 2, x: 2000, y: 600 }) });
    const at = resolvePlacement({ placement: readPlacement(floating, [NOTCHED, EXTERNAL]), displays: [NOTCHED, EXTERNAL], primary: NOTCHED });
    assert.equal(at.displayId, EXTERNAL.id);
    assert.deepEqual(at.orb, { x: 2000, y: 600 });
  });
});
