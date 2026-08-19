const { contextBridge, ipcRenderer } = require("electron");
const { CAPABILITIES, IPC_CHANNELS } = require("./capabilities");

/**
 * The ONE bridge the renderer gets (DC-2 C1).
 *
 * `window.pclawDesktop` is the whole surface: the loading screen's startup status
 * feed plus the native capability API. The earlier `window.electronAPI` namespace
 * (which carried `onStatus` alone) is GONE rather than kept alongside — two
 * overlapping bridges would be two places to audit, and the next person to add a
 * capability would have to guess which one it belongs in. `loading.html` moved with
 * it in the same change.
 *
 * `contextIsolation: true` + `nodeIntegration: false` (set in main.js for every
 * window) mean the renderer sees ONLY what is exposed here: no `require`, no
 * `ipcRenderer`, no channel not listed in IPC_CHANNELS. The capability methods
 * validate their argument against the closed vocabulary here as well as in the main
 * process — the renderer check is a courtesy, the main-process check is the boundary.
 *
 * Nothing here exposes the gateway `shell_token`. It is minted by the gateway, held
 * by the main process, and used only for main→gateway calls, so page JS has no path
 * to it even if a page is compromised.
 */

const isKnown = (cap) => typeof cap === "string" && CAPABILITIES.includes(cap);

const unknown = (cap) => ({
  available: false,
  granted: "unavailable",
  requestable: false,
  reason: `unknown capability: ${String(cap)}`,
});

contextBridge.exposeInMainWorld("pclawDesktop", {
  /** Startup status feed for the loading screen. Returns an unsubscribe function. */
  onStatus: (cb) => {
    const handler = (_e, msg) => cb(msg);
    ipcRenderer.on("status", handler);
    return () => ipcRenderer.removeListener("status", handler);
  },

  capabilities: {
    /** The closed capability vocabulary, so a renderer never has to hardcode it. */
    names: () => CAPABILITIES.slice(),

    /** Current state of one capability: {available, granted, requestable, reason}. */
    probe: (cap) =>
      isKnown(cap) ? ipcRenderer.invoke(IPC_CHANNELS.probe, cap) : Promise.resolve(unknown(cap)),

    /** Every capability at once — what the shell pushes to the gateway. */
    snapshot: () => ipcRenderer.invoke(IPC_CHANNELS.snapshot),

    /** Ask the OS. Resolves {granted, state, prompted, reason}; prompts only from
     * `not-determined`, so a user sees one dialog per capability per grant. */
    request: (cap) =>
      isKnown(cap)
        ? ipcRenderer.invoke(IPC_CHANNELS.request, cap)
        : Promise.resolve({
            granted: false,
            state: "unavailable",
            prompted: false,
            reason: `unknown capability: ${String(cap)}`,
          }),

    /** Subscribe to state pushes for one capability. Returns an unsubscribe fn. */
    on: (cap, cb) => {
      if (!isKnown(cap) || typeof cb !== "function") return () => {};
      const handler = (_e, payload) => {
        if (payload && payload.capability === cap) cb(payload.state);
      };
      ipcRenderer.on(IPC_CHANNELS.state, handler);
      return () => ipcRenderer.removeListener(IPC_CHANNELS.state, handler);
    },
  },

  /** Push-to-talk (DC-3). Three methods, and deliberately no `start()`: the shell
   * cannot open the microphone, it can only tell the renderer that the chord fired.
   * `setCapturing` runs the other way — the renderer reporting the live stream it
   * owns, which is what lights the menu-bar indicator. */
  pushToTalk: {
    /** Bind the chord. Resolves {ok, chord, conflict, reason} — an already-taken
     * chord comes back as `conflict: true` so Settings can say which it was. */
    bind: (chord) => ipcRenderer.invoke(IPC_CHANNELS.hotkeyBind, String(chord ?? "")),

    /** Report the microphone's real state to the shell (drives the indicator). */
    setCapturing: (on) => ipcRenderer.invoke(IPC_CHANNELS.capturing, Boolean(on)),

    /** Subscribe to chord presses and to the shell's stop requests. Returns an
     * unsubscribe fn. */
    on: (cb) => {
      if (typeof cb !== "function") return () => {};
      const handler = (_e, payload) => cb(payload);
      ipcRenderer.on(IPC_CHANNELS.pushToTalk, handler);
      return () => ipcRenderer.removeListener(IPC_CHANNELS.pushToTalk, handler);
    },
  },

  /** "Open PersonalClaw at login" (DC-4), so Settings can drive the same registration
   * the tray's checkbox drives — one mechanism, two surfaces.
   *
   * A preference, not an OS permission, so it is NOT in the capability vocabulary:
   * `probe`/`request` answer "may we?", this answers "should we?".
   *
   * `set()` is the only persistent change the bridge can make to the user's machine.
   * It is idempotent and reversible by the same call with `false`, and the main
   * process coerces the argument and reads the result back from the OS, so a caller
   * cannot be told "enabled" when nothing was registered. */
  loginItem: {
    /** Resolves {enabled, supported, describes} — `describes` names what it touches,
     * so a Settings UI can tell the user before they flip it. */
    get: () => ipcRenderer.invoke(IPC_CHANNELS.loginItemGet),

    /** Resolves {ok, enabled, changed, supported, reason?}. */
    set: (enabled) => ipcRenderer.invoke(IPC_CHANNELS.loginItemSet, Boolean(enabled)),
  },
});
