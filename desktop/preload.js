const { contextBridge, ipcRenderer } = require("electron");

/**
 * The vocabulary, INLINED rather than `require("./capabilities")`d — and that is the
 * whole reason this window keeps the Chromium process sandbox (#3348).
 *
 * A sandboxed preload gets a polyfilled `require` that resolves `electron` plus three
 * builtins (`events`, `timers`, `url`) and nothing else, so a relative path throws and
 * the entire preload is skipped SILENTLY: `window.pclawDesktop` is simply absent, which
 * is how a dead bridge shipped green (#3346). The fix that shipped there bought the
 * bridge back by turning the renderer sandbox off. It did not have to. Nothing else in
 * this file needs Node — `contextBridge` and `ipcRenderer` are both available to a
 * sandboxed preload — so the two constants come inline and `main.js` keeps the sandbox.
 *
 * That matters because THIS is the view that loads the dashboard, and the dashboard
 * renders agent- and app-authored HTML and script (widget frames, artifact and file
 * previews). The iframe `sandbox` attribute those use is a web-platform boundary; the
 * Chromium process sandbox is an OS one, and Chromium gives no guarantee that a
 * null-origin blob or `srcdoc` frame lands in its own renderer process.
 *
 * Two copies of a vocabulary is two things to drift, so it is a rail rather than a
 * comment: `test/bridgeLoads.test.js` executes this file under a require shim shaped
 * like the sandboxed one and asserts both constants deep-equal `capabilities.js`.
 * Editing one side alone reds the suite.
 */
const IPC_PREFIX = "pclaw-desktop:";

const CAPABILITIES = [
  "audio_capture",
  "global_hotkey",
  "native_notifications",
  "tray",
  "screen_capture",
  "login_item",
  "system_audio",
];

const IPC_CHANNELS = {
  probe: `${IPC_PREFIX}probe`,
  request: `${IPC_PREFIX}request`,
  snapshot: `${IPC_PREFIX}snapshot`,
  state: `${IPC_PREFIX}state`,
  hotkeyBind: `${IPC_PREFIX}hotkey-bind`,
  capturing: `${IPC_PREFIX}capturing`,
  pushToTalk: `${IPC_PREFIX}push-to-talk`,
  loginItemGet: `${IPC_PREFIX}login-item-get`,
  loginItemSet: `${IPC_PREFIX}login-item-set`,
  notify: `${IPC_PREFIX}notify`,
  notificationActivate: `${IPC_PREFIX}notification-activate`,
};

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

  /** Native OS notifications (DC-5) — plan-42's `native` delivery target.
   *
   * Deliberately NOT a general "notify the user" API: the renderer calls `show()` only for
   * a gateway note whose rule named the `native` target and whose `native.deliver` came
   * back true, so the policy lives in the rules engine and this is the actuator. The main
   * process coerces and truncates every field again — the shaping below is a courtesy. */
  notifications: {
    /** Raise one notification. Resolves {ok, route, reason?} — a refusal is an answer, so
     * the renderer can keep the in-app bell as the fallback instead of losing the note. */
    show: (note) =>
      ipcRenderer.invoke(IPC_CHANNELS.notify, {
        title: String((note && note.title) ?? ""),
        body: String((note && note.body) ?? ""),
        route: String((note && note.route) ?? ""),
      }),

    /** Subscribe to taps. The shell has already focused the window; the payload carries
     * the route so the renderer — the only process that knows the SPA's surfaces — can
     * navigate. Returns an unsubscribe fn. */
    on: (cb) => {
      if (typeof cb !== "function") return () => {};
      const handler = (_e, payload) => cb(payload);
      ipcRenderer.on(IPC_CHANNELS.notificationActivate, handler);
      return () => ipcRenderer.removeListener(IPC_CHANNELS.notificationActivate, handler);
    },
  },
});
