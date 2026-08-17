"use strict";

/**
 * Push-to-talk: the global chord that arms microphone capture (DC-3 T3.1).
 *
 * Split out of main.js for the same reason `capabilities.js` was: every OS handle
 * arrives through the injected `deps` object, so `desktop/test/pushToTalk.test.js`
 * drives the whole thing without launching Electron.
 *
 * ## The shell never opens the microphone
 *
 * This module registers a chord and forwards the press. It opens no capture device,
 * holds no stream, and buffers no audio — the renderer owns capture, because
 * the renderer is where the composer that receives the transcript lives, and because
 * one owner of a live microphone is auditable where two are not. The main process
 * therefore knows whether the mic is live only because the renderer TELLS it
 * (`setCapturing`), and the tray indicator reflects that report rather than a guess
 * about what the renderer probably did with the press we sent it.
 *
 * That direction of trust matters for the indicator's honesty: if the renderer
 * crashes mid-capture the mic dies with it, and `clearCapturing()` (called when the
 * window goes away) takes the indicator down with it. An indicator driven from "we
 * sent a press" instead would keep glowing over a dead stream, or worse, go dark over
 * a live one.
 *
 * ## Why the global chord TOGGLES rather than reading key-release
 *
 * Electron's `globalShortcut` delivers one callback per press and exposes no
 * key-release event at all — a true "while physically held" global gesture would need
 * a native key-event tap (an accessibility-permission-class API we deliberately do not
 * request). So the global chord is a TOGGLE: press to start, press again to stop. The
 * atom's clause is "captures only while held/toggled", and this is the toggled half,
 * honestly labelled everywhere the user reads about it.
 *
 * A toggle has a failure mode a hold does not: press it, walk away, and the microphone
 * stays live. `MAX_CAPTURE_MS` is the answer — the shell asks the renderer to stop a
 * capture that has run past the ceiling, so the worst case for a forgotten toggle is
 * bounded rather than "until you notice".
 */

/** The shipped default chord. Owner task 4 may change this; it is deliberately a
 * three-key chord including a modifier, because a global single-key shortcut would
 * swallow that key in every other app on the machine. */
const DEFAULT_CHORD = "CommandOrControl+Shift+Space";

/** A toggled capture is force-stopped after this long. See the note above: this is
 * the bound on "pressed the chord and walked away", not a normal code path. */
const MAX_CAPTURE_MS = 120000;

/** Accelerator modifier tokens Electron understands. */
const MODIFIERS = new Set([
  "Command",
  "Cmd",
  "Control",
  "Ctrl",
  "CommandOrControl",
  "CmdOrCtrl",
  "Alt",
  "Option",
  "AltGr",
  "Shift",
  "Super",
  "Meta",
]);

/** Non-modifier keys we accept as the chord's terminal key. A closed set, because an
 * accelerator Electron cannot parse fails at `register()` time with no useful reason,
 * and "your chord is invalid" discovered on the next launch is worse than a refusal
 * at the moment the user typed it. */
const KEYS = new Set([
  ..."ABCDEFGHIJKLMNOPQRSTUVWXYZ".split(""),
  ..."0123456789".split(""),
  ...Array.from({ length: 24 }, (_, i) => `F${i + 1}`),
  "Space",
  "Tab",
  "Backspace",
  "Delete",
  "Insert",
  "Return",
  "Enter",
  "Up",
  "Down",
  "Left",
  "Right",
  "Home",
  "End",
  "PageUp",
  "PageDown",
  "Escape",
  "Esc",
  "Plus",
  "-",
  "=",
  "[",
  "]",
  "\\",
  ";",
  "'",
  ",",
  ".",
  "/",
  "`",
]);

/**
 * Validate a chord string against the accelerator grammar.
 *
 * @returns {{ok: boolean, reason: string}}
 */
function validateChord(chord) {
  if (typeof chord !== "string" || !chord.trim()) {
    return { ok: false, reason: "Enter a shortcut." };
  }
  const parts = chord.split("+").map((p) => p.trim());
  if (parts.some((p) => !p)) {
    return { ok: false, reason: `“${chord}” has an empty key — check the “+” separators.` };
  }
  const key = parts[parts.length - 1];
  const mods = parts.slice(0, -1);
  if (MODIFIERS.has(key)) {
    // "Command+Shift" is every press of Shift while Command is down — it would fire
    // constantly and register as a shortcut that cannot be typed around.
    return { ok: false, reason: "A shortcut needs a key after its modifiers." };
  }
  if (!KEYS.has(key)) {
    return { ok: false, reason: `“${key}” is not a key a global shortcut can use.` };
  }
  for (const mod of mods) {
    if (!MODIFIERS.has(mod)) {
      return { ok: false, reason: `“${mod}” is not a modifier key.` };
    }
  }
  if (mods.length === 0) {
    // A bare global key is taken away from EVERY app on the machine. Refusing it is
    // the restrictive reading, and the one a user thanks you for.
    return {
      ok: false,
      reason: "Add a modifier (⌘, ⌃, ⌥ or ⇧) — a single key would be captured from every app.",
    };
  }
  if (new Set(mods).size !== mods.length) {
    return { ok: false, reason: "A modifier is repeated." };
  }
  return { ok: true, reason: "" };
}

/**
 * Build the push-to-talk controller over injected handles.
 *
 * @param {object} deps
 * @param {object} deps.globalShortcut  Electron globalShortcut (register/unregister/isRegistered)
 * @param {function} [deps.send]        (payload) => void — main → renderer push
 * @param {function} [deps.onCapturing] (boolean) => void — drives the tray indicator
 * @param {function} [deps.setTimer]    setTimeout (injected for tests)
 * @param {function} [deps.clearTimer]  clearTimeout (injected for tests)
 * @param {number} [deps.maxCaptureMs]  capture ceiling (defaults to MAX_CAPTURE_MS)
 */
function makePushToTalk(deps = {}) {
  const shortcuts = deps.globalShortcut || null;
  const send = typeof deps.send === "function" ? deps.send : () => {};
  const onCapturing = typeof deps.onCapturing === "function" ? deps.onCapturing : () => {};
  const setTimer = deps.setTimer || setTimeout;
  const clearTimer = deps.clearTimer || clearTimeout;
  const maxCaptureMs = typeof deps.maxCaptureMs === "number" ? deps.maxCaptureMs : MAX_CAPTURE_MS;

  let bound = "";
  let capturing = false;
  let timer = null;

  const cancelTimer = () => {
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
  };

  /**
   * Bind a chord, replacing whatever was bound before.
   *
   * @returns {{ok: boolean, chord: string, conflict: boolean, reason: string}}
   *   `conflict` distinguishes "another app already owns this chord" from "that is not
   *   a valid chord" — the two need different sentences in Settings, and collapsing
   *   them into one failure is how a user ends up retyping a perfectly good chord.
   */
  function bind(chord) {
    const valid = validateChord(chord);
    if (!valid.ok) return { ok: false, chord: "", conflict: false, reason: valid.reason };
    if (!shortcuts) {
      return {
        ok: false,
        chord: "",
        conflict: false,
        reason: "Global shortcuts are unavailable in this build.",
      };
    }
    // Release ours first, so re-binding the SAME chord is not reported as a conflict
    // with itself.
    unbind();
    try {
      if (typeof shortcuts.isRegistered === "function" && shortcuts.isRegistered(chord)) {
        return {
          ok: false,
          chord: "",
          conflict: true,
          reason: `${chord} is already used by another app — pick a different shortcut.`,
        };
      }
      const ok = Boolean(shortcuts.register(chord, () => press()));
      if (!ok) {
        // Electron returns false for a chord the OS refused to hand over. From here it
        // is indistinguishable from the isRegistered case, and it means the same thing
        // to the user: this chord is not available to us.
        return {
          ok: false,
          chord: "",
          conflict: true,
          reason: `The system would not give ${chord} to PersonalClaw — pick a different shortcut.`,
        };
      }
    } catch (err) {
      return {
        ok: false,
        chord: "",
        conflict: false,
        reason: `Could not bind ${chord}: ${err && err.message ? err.message : "unknown error"}`,
      };
    }
    bound = chord;
    return { ok: true, chord, conflict: false, reason: "" };
  }

  /** Release the chord. Idempotent — `before-quit` may run after a window teardown
   * that already unbound. */
  function unbind() {
    if (!bound) return;
    try {
      if (shortcuts && typeof shortcuts.unregister === "function") shortcuts.unregister(bound);
    } catch {
      /* the shortcut may already be gone; nothing to recover */
    }
    bound = "";
  }

  /** The chord fired. Forward it; the renderer decides start-or-stop from the state it
   * actually holds, so a dropped push cannot leave the two processes disagreeing about
   * whether the mic is open. */
  function press() {
    send({ action: "toggle" });
  }

  /**
   * The renderer's report that the microphone is (or is no longer) live. This is the
   * ONLY thing that moves the indicator.
   */
  function setCapturing(next) {
    const on = Boolean(next);
    if (on === capturing) return capturing;
    capturing = on;
    cancelTimer();
    onCapturing(on);
    if (on) {
      timer = setTimer(() => {
        timer = null;
        // Ask, do not assume: the renderer owns the stream, so it performs the stop
        // and reports back through setCapturing(false). The indicator stays lit until
        // it does, because until it does, the microphone genuinely still is.
        send({ action: "stop", reason: "capture-timeout" });
      }, maxCaptureMs);
    }
    return capturing;
  }

  /** The renderer went away (window closed / crashed), so its stream went with it. */
  function clearCapturing() {
    cancelTimer();
    if (!capturing) return;
    capturing = false;
    onCapturing(false);
  }

  return {
    DEFAULT_CHORD,
    bind,
    unbind,
    press,
    setCapturing,
    clearCapturing,
    validateChord,
    boundChord: () => bound,
    isCapturing: () => capturing,
  };
}

/**
 * Register the push-to-talk IPC handlers.
 *
 * Deliberately a SEPARATE function from `registerCapabilityIpc`: that one's test
 * asserts the exact set of channels it registers, and folding these in would have
 * turned a meaningful rail into a list that grows every time anything is added.
 */
function registerPushToTalkIpc(ipcMain, ptt, channels) {
  ipcMain.handle(channels.hotkeyBind, (_e, chord) => ptt.bind(chord));
  ipcMain.handle(channels.capturing, (_e, on) => ptt.setCapturing(on));
}

module.exports = {
  DEFAULT_CHORD,
  MAX_CAPTURE_MS,
  MODIFIERS,
  KEYS,
  validateChord,
  makePushToTalk,
  registerPushToTalkIpc,
};
