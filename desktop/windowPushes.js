/**
 * State pushes reach every window that carries the capability bridge (#3831), not only the first.
 *
 * A grant, a status line: a fact every bridged page should hold, so a second tab draws the same
 * truth as the first instead of none. The main window and its tabs are `BaseWindow`s holding one
 * view each, whose contents `setupWindowContents` hangs on `win.webContents` and marks with
 * `_pcBridgeAttached`. A window without the bridge (a paired gateway's page) has no listener to
 * hear it, and the buddy's window has a preload of its own (`buddyPreload.js`) that listens to none
 * of these channels, so neither is sent one.
 *
 * Action pushes (a chord press, a notification's tap) are not this: each has one window to act in.
 */

/** The live contents of each bridged window in `windows`. */
function bridgedContents(windows) {
  const out = [];
  for (const win of windows || []) {
    if (!win || (win.isDestroyed && win.isDestroyed()) || win._pcBridgeAttached !== true) continue;
    const wc = win.webContents;
    if (!wc || (wc.isDestroyed && wc.isDestroyed())) continue;
    out.push(wc);
  }
  return out;
}

/** Send `payload` on `channel` to every bridged window; one that is going away is skipped. */
function pushToBridged(windows, channel, payload) {
  let sent = 0;
  for (const wc of bridgedContents(windows)) {
    try {
      wc.send(channel, payload);
      sent += 1;
    } catch {
      /* the window is going away mid-push */
    }
  }
  return sent;
}

module.exports = { bridgedContents, pushToBridged };
