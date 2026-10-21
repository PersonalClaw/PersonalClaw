"use strict";

/**
 * The Go menu: the keys a browser keeps for itself (⌘N, ⌘1 to ⌘9), bound by the desktop app as
 * menu accelerators. Each item sends the page one command id from the dashboard's keymap
 * (`web/src/app/keymap.ts`, `DESKTOP_COMMANDS`), never a key press, so the page runs the same row
 * its palette lists. An approval is never one of them: no menu item can answer an approval.
 *
 * A command goes to the tab in view (each tab is a window of its own), only to the dashboard of
 * the gateway the window is pointed at, and only once that page has loaded.
 */

const { isDashboardPage } = require("./deepLinks");

/** The window event the page listens on for a command id (`keymap.ts`'s `COMMAND_EVENT`). */
const COMMAND_EVENT = "pclaw:command";

/** The ids a menu item may send, exactly the page's `DESKTOP_COMMANDS`. */
const GO_COMMANDS = Object.freeze([
  "new-chat",
  ...[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => `chat-${n}`),
]);

/** The script that hands the page *id*. Only a listed id; quoted, so the page receives a string. */
function commandScript(id) {
  if (!GO_COMMANDS.includes(id)) throw new Error(`not a menu command: ${id}`);
  return `window.dispatchEvent(new CustomEvent(${JSON.stringify(COMMAND_EVENT)}, { detail: ${JSON.stringify(id)} }));`;
}

/**
 * The web contents of the tab in view: the focused window's, else the first window's when no window
 * of the app has focus. `null` when neither is there.
 */
function activeTabContents(focusedWindow, mainWindow) {
  for (const win of [focusedWindow, mainWindow]) {
    if (win && !win.isDestroyed() && win.webContents) return win.webContents;
  }
  return null;
}

/**
 * What a Go menu item calls with its command id: hands it to *contents()* (the tab in view, or
 * null) when that tab shows the dashboard of *gatewayUrl()* and has finished loading. An id the
 * menu may not send, and a tab showing anything else, get nothing.
 */
function makeGoCommandSender({ contents, gatewayUrl }) {
  return (id) => {
    if (!GO_COMMANDS.includes(id)) return;
    const wc = contents();
    if (!wc || wc.isDestroyed() || wc.isLoading()) return;
    if (!isDashboardPage(wc.getURL(), gatewayUrl())) return;
    Promise.resolve(wc.executeJavaScript(commandScript(id))).catch(() => {});
  };
}

/**
 * The Go menu's template. *send* is called with a command id when an item is chosen
 * (`makeGoCommandSender`, which delivers it to the tab in view).
 */
function goMenuTemplate(send) {
  return {
    label: "Go",
    submenu: [
      { label: "New Thread", accelerator: "CmdOrCtrl+N", click: () => send("new-chat") },
      { type: "separator" },
      // The sidebar's threads by number: the page opens the nth thread row it shows.
      ...[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => ({
        label: `Thread ${n}`,
        accelerator: `CmdOrCtrl+${n}`,
        click: () => send(`chat-${n}`),
      })),
    ],
  };
}

module.exports = { COMMAND_EVENT, GO_COMMANDS, activeTabContents, commandScript, goMenuTemplate, makeGoCommandSender };
