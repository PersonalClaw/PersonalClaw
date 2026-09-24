/**
 * Preload for the connect dialog (`CA-8`).
 *
 * Six invokes and nothing else. Deliberately NOT `preload.js`: the switcher has no business
 * reaching the capability bridge, and the bridge has no business being reachable from a window
 * whose only job is picking a gateway. Two small surfaces beat one wide one.
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
});
