"use strict";

/**
 * Native capability state machine for the PersonalClaw desktop shell (DC-2 T2.1).
 *
 * Split out of main.js so it is testable without launching Electron: every OS call
 * arrives through the injected `deps` object, so `desktop/test/capabilities.test.js`
 * drives the whole state machine against stubs.
 *
 * The shell's main process is the ONLY place that knows the host's real permission
 * state. Two consumers read it, and neither is trusted with more than it needs:
 *
 *   - the renderer, through the `pclawDesktop` preload bridge (probe/request/on);
 *   - the gateway, which the main process pushes a manifest to, so a browser tab
 *     and any app with a `desktop` permission see the same truth.
 *
 * Honesty rules baked into the specs below:
 *
 *   - `requestable: false` means this process CANNOT raise the OS prompt. macOS has
 *     no API to ask for Screen Recording and no API to read notification
 *     authorization, so those capabilities are disclosure-only: we report what we
 *     can observe and tell the user where to grant it, instead of offering a button
 *     that silently does nothing.
 *   - Capability probing is implemented for macOS only (the shipped shell target).
 *     Elsewhere a capability reports `unavailable` with a reason rather than a
 *     guessed `granted` — a wrong optimistic answer is what makes a mic indicator
 *     lie.
 *   - `request()` prompts ONLY from `not-determined`. From `denied`/`restricted` it
 *     returns the reason and does not re-ask (macOS would not re-prompt anyway), so
 *     a user sees exactly one TCC dialog per capability per grant.
 */

/** The canonical capability vocabulary — mirrors CAPABILITIES in
 * src/personalclaw/dashboard/desktop_registry.py (a test asserts they agree). */
const CAPABILITIES = [
  "audio_capture",
  "global_hotkey",
  "native_notifications",
  "tray",
  "screen_capture",
  "login_item",
  "system_audio",
];

/** The permission states a capability may report (mirrors GRANT_STATES). */
const GRANT_STATES = ["granted", "denied", "restricted", "not-determined", "unavailable"];

/** IPC channel names. Every channel the bridge uses lives under this one prefix so
 * the preload namespace and the main-process handlers cannot drift, and so a test
 * can assert the renderer reaches nothing outside it. */
const IPC_PREFIX = "pclaw-desktop:";
const IPC_CHANNELS = {
  probe: `${IPC_PREFIX}probe`,
  request: `${IPC_PREFIX}request`,
  snapshot: `${IPC_PREFIX}snapshot`,
  /** main → renderer push when a capability changes */
  state: `${IPC_PREFIX}state`,
  /** renderer → main: bind the push-to-talk chord (DC-3). Resolves the
   * registration result, so an already-taken chord is an answer, not a silence. */
  hotkeyBind: `${IPC_PREFIX}hotkey-bind`,
  /** renderer → main: "the microphone is live" / "it is not". The renderer is the
   * only process that opens the mic, so it is the only honest source for this. */
  capturing: `${IPC_PREFIX}capturing`,
  /** main → renderer push: the chord fired, or the shell is asking capture to stop. */
  pushToTalk: `${IPC_PREFIX}push-to-talk`,
  /** renderer → main: read the "open at login" registration (DC-4). A preference,
   * not an OS permission — so it is handled by `registerLoginItemIpc` on its own
   * channels rather than folded into the capability vocabulary. */
  loginItemGet: `${IPC_PREFIX}login-item-get`,
  /** renderer → main: register or un-register the login item (DC-4). */
  loginItemSet: `${IPC_PREFIX}login-item-set`,
};

/**
 * Per-capability spec.
 *  kind: "tcc"    — macOS TCC-gated; `media` names the systemPreferences media type
 *        "shell"  — available whenever the shell runs; no OS permission involved
 *        "opaque" — the OS does not expose its authorization state to us
 *  platforms: process.platform values where the capability is implemented
 */
const SPECS = {
  audio_capture: {
    kind: "tcc",
    media: "microphone",
    platforms: ["darwin"],
    requestable: true,
    label: "Microphone",
  },
  screen_capture: {
    kind: "tcc",
    media: "screen",
    platforms: ["darwin"],
    // macOS exposes no programmatic prompt for Screen Recording.
    requestable: false,
    disclosure: "Grant Screen Recording in System Settings › Privacy & Security.",
    label: "Screen recording",
  },
  native_notifications: {
    kind: "opaque",
    platforms: ["darwin"],
    requestable: false,
    disclosure:
      "macOS does not report notification authorization to the app; the first " +
      "notification asks, and Notifications in System Settings is the only control.",
    label: "Native notifications",
  },
  global_hotkey: { kind: "shell", platforms: ["darwin"], requestable: false, label: "Global hotkey" },
  // DC-3 T3.3 — the honest system-audio answer. Capturing what the SPEAKERS play
  // (as opposed to what the microphone hears) is not a permission we are missing:
  // macOS exposes no audio-only tap, so the only route is the Screen Recording
  // entitlement's audio side-channel, which means asking for the right to record
  // the screen in order to record sound. We are not shipping that trade quietly,
  // so this capability reports `unavailable` WITH THE REASON on every platform and
  // there is no code path that captures system audio. `docs/guides/desktop.md`
  // states the same thing in prose; a test asserts the two agree.
  system_audio: {
    kind: "unsupported",
    platforms: [],
    requestable: false,
    unsupported:
      "Capturing system audio needs the macOS Screen Recording entitlement (there " +
      "is no audio-only tap), so PersonalClaw captures the microphone only.",
    label: "System audio",
  },
  tray: { kind: "shell", platforms: ["darwin"], requestable: false, label: "Menu-bar item" },
  login_item: { kind: "shell", platforms: ["darwin"], requestable: false, label: "Open at login" },
};

const UNAVAILABLE = (reason) => ({
  available: false,
  granted: "unavailable",
  requestable: false,
  reason,
});

/**
 * Build the capability API over injected OS handles.
 *
 * @param {object} deps
 * @param {string} deps.platform            process.platform
 * @param {object} [deps.systemPreferences] Electron systemPreferences
 * @param {object} [deps.notification]      Electron Notification (for isSupported)
 * @param {function} [deps.onChange]        called with (cap, state) after a change
 */
function makeCapabilities(deps = {}) {
  const platform = deps.platform || "";
  const sysPrefs = deps.systemPreferences || null;
  const notification = deps.notification || null;
  const onChange = typeof deps.onChange === "function" ? deps.onChange : () => {};

  function normalizeGrant(value) {
    return GRANT_STATES.includes(value) ? value : "unavailable";
  }

  /** Read one capability's current state. Never throws: an OS call that blows up
   * degrades to `unavailable` with the reason, because a thrown probe would leave
   * the UI with no state at all — strictly worse than an honest "unknown". */
  function probe(cap) {
    const spec = SPECS[cap];
    if (!spec) return UNAVAILABLE("unknown capability");
    // Checked BEFORE the platform test: "not implemented on darwin" would read as a
    // porting gap that a later release closes, when the real answer is a deliberate
    // refusal that no platform changes. The reason has to say which it is.
    if (spec.kind === "unsupported") return UNAVAILABLE(spec.unsupported);
    if (!spec.platforms.includes(platform)) {
      return UNAVAILABLE(`not implemented on ${platform || "this platform"}`);
    }
    try {
      if (spec.kind === "tcc") {
        if (!sysPrefs || typeof sysPrefs.getMediaAccessStatus !== "function") {
          return UNAVAILABLE("permission state unavailable in this build");
        }
        const granted = normalizeGrant(sysPrefs.getMediaAccessStatus(spec.media));
        if (granted === "unavailable") return UNAVAILABLE("the OS did not report a state");
        return {
          available: true,
          granted,
          requestable: spec.requestable && granted === "not-determined",
          reason: spec.requestable ? "" : spec.disclosure || "",
        };
      }
      if (spec.kind === "opaque") {
        const supported = !notification || typeof notification.isSupported !== "function"
          ? true
          : Boolean(notification.isSupported());
        if (!supported) return UNAVAILABLE("the OS does not support notifications");
        return {
          available: true,
          // Deliberately NOT "granted": we cannot see the authorization, and
          // claiming a grant we cannot observe is exactly the lie this panel exists
          // to avoid.
          granted: "not-determined",
          requestable: false,
          reason: spec.disclosure || "",
        };
      }
      // kind === "shell": the shell is running, so the capability exists.
      return { available: true, granted: "granted", requestable: false, reason: "" };
    } catch (err) {
      return UNAVAILABLE(`probe failed: ${err && err.message ? err.message : "unknown error"}`);
    }
  }

  /** The full manifest, in the shape POSTed to /api/desktop/register. */
  function snapshot() {
    const out = {};
    for (const cap of CAPABILITIES) out[cap] = probe(cap);
    return out;
  }

  /**
   * Ask the OS for a capability. Resolves `{granted, state, prompted, reason}`.
   *
   * `prompted` is the property V2 checks: it is true only on the one transition
   * that can raise a dialog (`not-determined` → granted/denied), so "the TCC prompt
   * appears exactly once per grant" is an assertion, not a hope.
   */
  async function request(cap) {
    const spec = SPECS[cap];
    const current = probe(cap);
    if (!spec || !current.available) {
      return {
        granted: false,
        state: "unavailable",
        prompted: false,
        reason: current.reason || "unavailable",
      };
    }
    if (current.granted === "granted") {
      return { granted: true, state: "granted", prompted: false, reason: "" };
    }
    if (!spec.requestable) {
      return {
        granted: false,
        state: current.granted,
        prompted: false,
        reason: spec.disclosure || "this capability cannot be requested from the app",
      };
    }
    if (current.granted === "denied" || current.granted === "restricted") {
      return {
        granted: false,
        state: current.granted,
        prompted: false,
        reason:
          `${spec.label} was already ${current.granted} for PersonalClaw. ` +
          "Change it in System Settings › Privacy & Security.",
      };
    }
    // not-determined → the single prompting transition.
    let granted = false;
    try {
      granted = Boolean(await sysPrefs.askForMediaAccess(spec.media));
    } catch (err) {
      return {
        granted: false,
        state: "not-determined",
        prompted: true,
        reason: `request failed: ${err && err.message ? err.message : "unknown error"}`,
      };
    }
    const state = granted ? "granted" : "denied";
    onChange(cap, probe(cap));
    return { granted, state, prompted: true, reason: "" };
  }

  return { CAPABILITIES, IPC_CHANNELS, probe, snapshot, request };
}

/**
 * Register the bridge's IPC handlers on the main process.
 *
 * Only the three channels in IPC_CHANNELS are handled, and each validates its
 * argument against the closed capability vocabulary before touching an OS API — a
 * compromised renderer can pass any string over IPC, so the main process treats the
 * argument as untrusted input rather than as a lookup key.
 */
function registerCapabilityIpc(ipcMain, caps) {
  ipcMain.handle(IPC_CHANNELS.probe, (_e, cap) => {
    if (!CAPABILITIES.includes(cap)) return UNAVAILABLE("unknown capability");
    return caps.probe(cap);
  });
  ipcMain.handle(IPC_CHANNELS.snapshot, () => caps.snapshot());
  ipcMain.handle(IPC_CHANNELS.request, async (_e, cap) => {
    if (!CAPABILITIES.includes(cap)) {
      return { granted: false, state: "unavailable", prompted: false, reason: "unknown capability" };
    }
    return caps.request(cap);
  });
}

module.exports = {
  CAPABILITIES,
  GRANT_STATES,
  IPC_CHANNELS,
  IPC_PREFIX,
  SPECS,
  makeCapabilities,
  registerCapabilityIpc,
};
