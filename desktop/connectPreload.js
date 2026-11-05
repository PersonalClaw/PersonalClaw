/**
 * Preload for the connect dialog, and for the chooser that opens when nothing serves the windows.
 *
 * Eleven invokes and one event, and nothing else. Deliberately NOT `preload.js`: the switcher has no
 * business reaching the capability bridge, and the bridge has no business being reachable from a
 * window whose only job is picking a gateway. Two small surfaces beat one wide one.
 */

const { contextBridge, ipcRenderer } = require("electron");
const { CONNECT_CHANNELS } = require("./connectDialog");

const str = (v) => String(v === null || v === undefined ? "" : v);

contextBridge.exposeInMainWorld("pclawConnect", {
  /** `{activeId, rows[], warnings[], storeStatus, storeSafe, readOnly}` — see `describeList`. */
  list: () => ipcRenderer.invoke(CONNECT_CHANNELS.list),
  /** Validate + classify an address or pairing link WITHOUT connecting to it. */
  prepare: (input) => ipcRenderer.invoke(CONNECT_CHANNELS.prepare, { input: str(input) }),
  /** Record the user's confirmation, save the row, and navigate the main window. */
  confirm: (input, label) => ipcRenderer.invoke(CONNECT_CHANNELS.confirm, { input: str(input), label: str(label) }),
  /** Re-point `active` and navigate. Refuses if the row's confirmation no longer holds. */
  switchTo: (id) => ipcRenderer.invoke(CONNECT_CHANNELS.switch, { id: str(id) }),
  /** Drop the row AND sweep its namespaced shell state. */
  forget: (id) => ipcRenderer.invoke(CONNECT_CHANNELS.forget, { id: str(id) }),
  /** Re-probe every row's health. Never presents a credential. */
  refresh: () => ipcRenderer.invoke(CONNECT_CHANNELS.refresh),
  /** The "This computer" row's Start a gateway here, and Try again: the app's own gateway, and when
   *  that doesn't start, this home's running gateway is offered. Resolves `{ok}`, or `{ok: false,
   *  message}` saying why not. */
  start: () => ipcRenderer.invoke(CONNECT_CHANNELS.start),
  /** Use this home's running gateway, which the app did not start, and with *remember* every time
   *  the app's own start finds it. Resolves `{ok}`, or `{ok: false, message}` saying why not. */
  use: (remember) => ipcRenderer.invoke(CONNECT_CHANNELS.use, { remember: remember === true }),
  /** Whether a running gateway the app's own start finds is used without asking. */
  autoUse: (on) => ipcRenderer.invoke(CONNECT_CHANNELS.autoUse, { on: on === true }),
  /** The chooser's Add a Gateway…: open the Gateways window, where a gateway is added. */
  openGateways: () => ipcRenderer.invoke(CONNECT_CHANNELS.gateways),
  /** The "This computer" row's Open the Releases Page, for a home a newer PersonalClaw served: the
   *  main process opens its one fixed address in the browser. Takes nothing. */
  releases: () => ipcRenderer.invoke(CONNECT_CHANNELS.releases),
  /** Hear when the list changed in the main process, to read it again. Returns an unsubscribe fn. */
  onChanged: (cb) => {
    if (typeof cb !== "function") return () => {};
    const handler = () => cb();
    ipcRenderer.on(CONNECT_CHANNELS.changed, handler);
    return () => ipcRenderer.removeListener(CONNECT_CHANNELS.changed, handler);
  },
});
