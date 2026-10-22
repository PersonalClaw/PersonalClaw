const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const {
  SIZES,
  UNDOCK_DISTANCE,
  DOCK_ZONE,
  PLACEMENT_KEY,
  hasNotch,
  dockedBounds,
  floatingBounds,
  draggedOut,
  readPlacement,
  writePlacement,
  resolveBounds,
  settleDrop,
} = require("../buddyPlacement");

// ── Where the buddy's window sits (#3831) ──────────────────────────────────────────────────────
//
// Docked on the menu-bar display, centred, hung from the top of its work area: below the notch on a
// notched Mac, below the menu bar everywhere, so it covers no menu-bar item and no window control.
// Floating where it was dropped, inside that display's work area, and back there after a restart.

/** A 14" MacBook Pro: 1512 × 982 points, its menu bar as tall as the notch. */
const NOTCHED = { id: 1, bounds: { x: 0, y: 0, width: 1512, height: 982 }, workArea: { x: 0, y: 38, width: 1512, height: 944 } };
/** An external display to its right, with an ordinary menu bar. */
const EXTERNAL = { id: 2, bounds: { x: 1512, y: 0, width: 2560, height: 1440 }, workArea: { x: 1512, y: 25, width: 2560, height: 1415 } };
/** A Linux desktop with a top panel. */
const LINUX = { id: 7, bounds: { x: 0, y: 0, width: 1920, height: 1080 }, workArea: { x: 0, y: 32, width: 1920, height: 1048 } };

/** A shell store as `shellStore.js` keeps it: strings only, with every write recorded. */
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

const covers = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

describe("docked", () => {
  it("hangs just below the notch on a notched Mac, centred on it, covering none of the menu bar", () => {
    assert.equal(hasNotch(NOTCHED, "darwin"), true);
    for (const presentation of ["peek", "compact", "expanded"]) {
      const b = dockedBounds(NOTCHED, presentation);
      assert.equal(b.y, 38, `${presentation} starts below the menu bar and the notch`);
      assert.equal(b.x + b.width / 2, 1512 / 2, `${presentation} is centred on the notch`);
      const menuBar = { x: 0, y: 0, width: 1512, height: 38 };
      assert.equal(covers(b, menuBar), false, `${presentation} covers the menu bar`);
      assert.deepEqual({ width: b.width, height: b.height }, SIZES[presentation]);
    }
  });

  it("hangs from the top centre of a display without a notch, below its menu bar or panel", () => {
    assert.equal(hasNotch(EXTERNAL, "darwin"), false);
    assert.equal(hasNotch(LINUX, "linux"), false, "only a Mac has a notch");
    const mac = dockedBounds(EXTERNAL, "compact");
    assert.deepEqual(mac, { x: 1512 + (2560 - 360) / 2, y: 25, width: 360, height: 28 });
    const linux = dockedBounds(LINUX, "compact");
    assert.equal(linux.y, 32, "below the top panel");
    assert.equal(linux.x + linux.width / 2, 960);
  });

  it("lives on the menu-bar display", () => {
    const placed = resolveBounds({ placement: { mode: "docked" }, presentation: "compact", displays: [EXTERNAL, NOTCHED], primary: NOTCHED });
    assert.equal(placed.displayId, 1);
    assert.equal(placed.mode, "docked");
  });
});

describe("floating", () => {
  it("stays inside the work area of the display it was dropped on", () => {
    assert.deepEqual(floatingBounds(EXTERNAL, { x: 1600, y: 300 }, "compact"), { x: 1600, y: 300, width: 56, height: 56 });
    const offRight = floatingBounds(EXTERNAL, { x: 9000, y: 9000 }, "compact");
    assert.deepEqual(offRight, { x: 1512 + 2560 - 56, y: 25 + 1415 - 56, width: 56, height: 56 });
    const offTop = floatingBounds(NOTCHED, { x: 10, y: 0 }, "compact");
    assert.equal(offTop.y, 38, "never over the menu bar");
    const card = floatingBounds(NOTCHED, { x: 1500, y: 900 }, "expanded");
    assert.ok(card.x + card.width <= 1512 && card.y + card.height <= 982, "the open card fits too");
  });

  it("comes back on its display after a restart, and docks when that display is gone", () => {
    const placement = { mode: "floating", displayId: 2, x: 2000, y: 400 };
    const back = resolveBounds({ placement, presentation: "compact", displays: [NOTCHED, EXTERNAL], primary: NOTCHED });
    assert.deepEqual(back, { mode: "floating", displayId: 2, bounds: { x: 2000, y: 400, width: 56, height: 56 } });
    const gone = resolveBounds({ placement, presentation: "compact", displays: [NOTCHED], primary: NOTCHED });
    assert.equal(gone.mode, "docked");
    assert.equal(gone.displayId, 1);
  });
});

describe("dragging", () => {
  it("undocks once the face is past the undock distance below the top", () => {
    assert.equal(draggedOut(NOTCHED, 38 + UNDOCK_DISTANCE), false);
    assert.equal(draggedOut(NOTCHED, 38 + UNDOCK_DISTANCE + 1), true);
  });

  it("docks a drop within the dock zone of the top centre, and floats any other drop where it fell", () => {
    const near = settleDrop({ faceTopLeft: { x: 756 - 28, y: 38 + DOCK_ZONE / 2 - 28 }, displays: [NOTCHED, EXTERNAL], primary: NOTCHED });
    assert.deepEqual(near, { mode: "docked" });
    const away = settleDrop({ faceTopLeft: { x: 2200, y: 600 }, displays: [NOTCHED, EXTERNAL], primary: NOTCHED });
    assert.deepEqual(away, { mode: "floating", displayId: 2, x: 2200, y: 600 });
    const edge = settleDrop({ faceTopLeft: { x: 1450, y: 950 }, displays: [NOTCHED, EXTERNAL], primary: NOTCHED });
    assert.equal(edge.displayId, 1);
    assert.ok(edge.x <= 1512 - 56 && edge.y <= 982 - 56, "kept on the display it fell on");
  });
});

describe("the placement is the machine's, in the shell store", () => {
  it("round-trips as one string under the buddy's key, and writes no other key", () => {
    const store = memoryStore({ "endpoints:paired": "[]" });
    writePlacement(store, { mode: "floating", displayId: 2, x: 2000.4, y: 400.6 });
    assert.deepEqual(store.writes, [PLACEMENT_KEY]);
    assert.equal(typeof store.data[PLACEMENT_KEY], "string");
    assert.deepEqual(readPlacement(store), { mode: "floating", displayId: 2, x: 2000, y: 401 });
    assert.equal(store.data["endpoints:paired"], "[]", "the paired gateways are untouched");
    writePlacement(store, { mode: "docked", displayId: 2, x: 1, y: 1 });
    assert.deepEqual(readPlacement(store), { mode: "docked" });
  });

  it("reads docked from nothing saved, or from anything it cannot read", () => {
    for (const raw of [undefined, "", "not json", "{}", '{"mode":"sideways"}', '{"mode":"floating","x":"a","y":2}']) {
      const store = memoryStore(raw === undefined ? {} : { [PLACEMENT_KEY]: raw });
      assert.deepEqual(readPlacement(store), { mode: "docked" }, JSON.stringify(raw));
    }
  });
});
