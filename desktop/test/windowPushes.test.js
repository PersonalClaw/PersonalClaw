const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const { IPC_CHANNELS } = require("../capabilities");
const { pushToBridged } = require("../windowPushes");
const { buddyWindowOptions } = require("../buddyWindow");

// ── State pushes reach every bridged window (#3831) ────────────────────────────────────────────
//
// Before, every push went to `mainWindow.webContents`: a second tab heard no grant and no status.

/** A web contents that keeps what it was sent. */
function contents({ destroyed = false, throws = false } = {}) {
  const heard = [];
  return {
    heard,
    isDestroyed: () => destroyed,
    send: (channel, payload) => {
      if (throws) throw new Error("gone");
      heard.push([channel, payload]);
    },
  };
}

/** A window as `setupWindowContents` leaves it: its view's contents, and whether it is bridged. */
const appWindow = (bridged, wc = contents()) => ({ _pcBridgeAttached: bridged, webContents: wc, isDestroyed: () => false });

describe("a state push", () => {
  it("reaches the main window and every tab with the bridge", () => {
    const main = appWindow(true);
    const tab = appWindow(true);
    const sent = pushToBridged([main, tab], IPC_CHANNELS.state, { capability: "microphone", state: "granted" });
    assert.equal(sent, 2);
    for (const w of [main, tab]) assert.deepEqual(w.webContents.heard, [[IPC_CHANNELS.state, { capability: "microphone", state: "granted" }]]);
  });

  it("is not sent to a window without the bridge, nor to the buddy's, whose preload hears none of it", () => {
    const paired = appWindow(false);
    // The buddy is a BrowserWindow: its contents are its own, and it carries no `_pcBridgeAttached`.
    const buddy = { webContents: contents(), isDestroyed: () => false, options: buddyWindowOptions({ bounds: {}, preload: "/app/buddyPreload.js" }) };
    const main = appWindow(true);
    assert.equal(pushToBridged([paired, buddy, main], "status", "Starting…"), 1);
    assert.deepEqual(paired.webContents.heard, []);
    assert.deepEqual(buddy.webContents.heard, []);
    assert.deepEqual(main.webContents.heard, [["status", "Starting…"]]);
  });

  it("skips a window that is going away, and still reaches the rest", () => {
    const gone = { ...appWindow(true), isDestroyed: () => true };
    const closing = appWindow(true, contents({ destroyed: true }));
    const throwing = appWindow(true, contents({ throws: true }));
    const live = appWindow(true);
    assert.equal(pushToBridged([gone, closing, throwing, null, live], IPC_CHANNELS.state, {}), 1);
    assert.equal(live.webContents.heard.length, 1);
  });
});
