"use strict";

/**
 * DC-3 T3.1 — the push-to-talk chord and the capturing indicator.
 *
 * What this file can and cannot prove, stated up front so nobody reads a green run as
 * more than it is:
 *
 *  - PROVEN here: the chord grammar, the bind/conflict/rebind state machine, that the
 *    shell forwards a press rather than opening the microphone itself, that the
 *    indicator follows the RENDERER's report and nothing else, and that a toggled
 *    capture is bounded by the timeout.
 *  - NOT proven here: that macOS actually delivers the chord to us. `globalShortcut`
 *    is a stub below. A real key press on a real machine is V3's job and has not been
 *    performed — see the plan's execution log.
 */

const { test, describe } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");

const {
  DEFAULT_CHORD,
  MAX_CAPTURE_MS,
  validateChord,
  makePushToTalk,
  registerPushToTalkIpc,
} = require("../pushToTalk");
const { CAPABILITIES, IPC_CHANNELS, SPECS, makeCapabilities } = require("../capabilities");

/** A `globalShortcut` stub that records registrations and can pretend a chord is
 * already owned by another app. */
function shortcutStub({ taken = [], refuse = false } = {}) {
  const registered = new Map();
  return {
    registered,
    taken: new Set(taken),
    register(chord, cb) {
      if (refuse) return false;
      registered.set(chord, cb);
      return true;
    },
    unregister(chord) {
      registered.delete(chord);
    },
    isRegistered(chord) {
      return this.taken.has(chord) || registered.has(chord);
    },
    fire(chord) {
      const cb = registered.get(chord);
      assert.ok(cb, `nothing registered for ${chord}`);
      cb();
    },
  };
}

/** A controller wired to stubs, with the sent payloads and indicator flips recorded. */
function harness(opts = {}) {
  const sent = [];
  const indicator = [];
  const timers = [];
  const shortcuts = shortcutStub(opts);
  const ptt = makePushToTalk({
    globalShortcut: shortcuts,
    send: (p) => sent.push(p),
    onCapturing: (on) => indicator.push(on),
    setTimer: (fn, ms) => {
      timers.push({ fn, ms });
      return timers.length;
    },
    clearTimer: (id) => {
      if (timers[id - 1]) timers[id - 1].cleared = true;
    },
    maxCaptureMs: opts.maxCaptureMs,
  });
  return { ptt, sent, indicator, timers, shortcuts };
}

describe("chord grammar", () => {
  test("the shipped default is valid", () => {
    assert.equal(validateChord(DEFAULT_CHORD).ok, true);
  });

  test("a chord needs a modifier — a bare global key is refused", () => {
    const r = validateChord("Space");
    assert.equal(r.ok, false);
    // The reason has to say WHY, because "invalid" on a chord the user can obviously
    // type is the kind of refusal people work around by giving up.
    assert.match(r.reason, /modifier/i);
    assert.match(r.reason, /every app/i);
  });

  test("modifiers alone are refused", () => {
    const r = validateChord("CommandOrControl+Shift");
    assert.equal(r.ok, false);
    assert.match(r.reason, /needs a key/i);
  });

  test("an unknown key names itself in the reason", () => {
    const r = validateChord("CommandOrControl+Banana");
    assert.equal(r.ok, false);
    assert.match(r.reason, /Banana/);
  });

  test("an unknown modifier names itself in the reason", () => {
    const r = validateChord("Hyper+K");
    assert.equal(r.ok, false);
    assert.match(r.reason, /Hyper/);
  });

  test("empty, blank and non-string chords are refused without throwing", () => {
    for (const bad of ["", "   ", null, undefined, 42, {}, "Command++K"]) {
      const r = validateChord(bad);
      assert.equal(r.ok, false, `${JSON.stringify(bad)} should be refused`);
      assert.ok(r.reason, "a refusal must carry a reason");
    }
  });

  test("function keys and punctuation terminals are accepted", () => {
    for (const good of ["Alt+F13", "CommandOrControl+Shift+/", "Control+Option+M", "Super+Enter"]) {
      assert.equal(validateChord(good).ok, true, `${good} should be accepted`);
    }
  });
});

describe("binding the chord", () => {
  test("a valid chord binds and is reported back", () => {
    const { ptt, shortcuts } = harness();
    const r = ptt.bind("CommandOrControl+Shift+Space");
    assert.deepEqual(r, {
      ok: true,
      chord: "CommandOrControl+Shift+Space",
      conflict: false,
      reason: "",
    });
    assert.equal(ptt.boundChord(), "CommandOrControl+Shift+Space");
    assert.equal(shortcuts.registered.has("CommandOrControl+Shift+Space"), true);
  });

  test("a chord another app already owns comes back as a CONFLICT, named", () => {
    const { ptt, shortcuts } = harness({ taken: ["CommandOrControl+Shift+Space"] });
    const r = ptt.bind("CommandOrControl+Shift+Space");
    assert.equal(r.ok, false);
    // `conflict` is what lets Settings say "that one is taken" instead of "invalid" —
    // the two failures need different sentences.
    assert.equal(r.conflict, true);
    assert.match(r.reason, /already used by another app/i);
    assert.match(r.reason, /CommandOrControl\+Shift\+Space/);
    // Nothing was bound, so the old chord is not silently lost either.
    assert.equal(ptt.boundChord(), "");
    assert.equal(shortcuts.registered.size, 0);
  });

  test("an invalid chord is NOT a conflict", () => {
    const { ptt } = harness();
    const r = ptt.bind("Space");
    assert.equal(r.ok, false);
    assert.equal(r.conflict, false);
  });

  test("re-binding the same chord succeeds — it does not conflict with itself", () => {
    const { ptt } = harness();
    assert.equal(ptt.bind("Alt+F13").ok, true);
    // Without the unbind-first step this reads as "already registered" against our own
    // registration, and the user cannot re-save their own shortcut.
    const again = ptt.bind("Alt+F13");
    assert.equal(again.ok, true);
    assert.equal(again.conflict, false);
  });

  test("re-binding a different chord releases the old one", () => {
    const { ptt, shortcuts } = harness();
    ptt.bind("Alt+F13");
    ptt.bind("Alt+F14");
    assert.equal(shortcuts.registered.has("Alt+F13"), false, "the old chord must be released");
    assert.equal(shortcuts.registered.has("Alt+F14"), true);
    assert.equal(ptt.boundChord(), "Alt+F14");
  });

  test("a system refusal is surfaced as a conflict, not a silent no-op", () => {
    const { ptt } = harness({ refuse: true });
    const r = ptt.bind("Alt+F13");
    assert.equal(r.ok, false);
    assert.equal(r.conflict, true);
    assert.match(r.reason, /would not give/i);
  });

  test("unbind is idempotent", () => {
    const { ptt } = harness();
    ptt.bind("Alt+F13");
    ptt.unbind();
    ptt.unbind();
    assert.equal(ptt.boundChord(), "");
  });

  test("with no globalShortcut handle, binding fails honestly", () => {
    const ptt = makePushToTalk({});
    const r = ptt.bind("Alt+F13");
    assert.equal(r.ok, false);
    assert.match(r.reason, /unavailable/i);
  });
});

describe("the shell forwards the press and never opens the microphone", () => {
  test("a chord press sends a toggle to the renderer", () => {
    const { ptt, sent, shortcuts } = harness();
    ptt.bind("Alt+F13");
    shortcuts.fire("Alt+F13");
    assert.deepEqual(sent, [{ action: "toggle" }]);
  });

  test("a press does NOT move the indicator by itself", () => {
    // The indicator must describe the microphone, not our intent. If a press lit it,
    // a renderer that refused the capture (mic denied) would leave a lit indicator over
    // a dead stream — the exact lie the clause exists to prevent.
    const { ptt, indicator, shortcuts } = harness();
    ptt.bind("Alt+F13");
    shortcuts.fire("Alt+F13");
    assert.deepEqual(indicator, []);
    assert.equal(ptt.isCapturing(), false);
  });

  test("the module never references getUserMedia or a media stream", () => {
    const src = readFileSync(join(__dirname, "..", "pushToTalk.js"), "utf8");
    // A source-level rail: the split of responsibility (renderer captures, shell
    // forwards) is the property, and the cheapest way to keep it is to assert the
    // shell has no capture API in it at all.
    assert.ok(!/getUserMedia|mediaDevices|MediaRecorder/.test(src));
  });
});

describe("the capturing indicator follows the renderer's report", () => {
  test("the renderer's report lights and clears it", () => {
    const { ptt, indicator } = harness();
    ptt.setCapturing(true);
    assert.deepEqual(indicator, [true]);
    assert.equal(ptt.isCapturing(), true);
    ptt.setCapturing(false);
    assert.deepEqual(indicator, [true, false]);
    assert.equal(ptt.isCapturing(), false);
  });

  test("repeated identical reports do not re-fire it", () => {
    const { ptt, indicator } = harness();
    ptt.setCapturing(true);
    ptt.setCapturing(true);
    ptt.setCapturing(1);
    assert.deepEqual(indicator, [true]);
  });

  test("a lost renderer takes the indicator down with it", () => {
    // The window closing kills the stream, so an indicator left lit would outlive the
    // capture it describes.
    const { ptt, indicator } = harness();
    ptt.setCapturing(true);
    ptt.clearCapturing();
    assert.deepEqual(indicator, [true, false]);
    assert.equal(ptt.isCapturing(), false);
  });

  test("clearCapturing on an idle controller is a no-op", () => {
    const { ptt, indicator } = harness();
    ptt.clearCapturing();
    assert.deepEqual(indicator, []);
  });
});

describe("a toggled capture is bounded", () => {
  test("starting a capture arms the ceiling", () => {
    const { ptt, timers } = harness({ maxCaptureMs: 5000 });
    ptt.setCapturing(true);
    assert.equal(timers.length, 1);
    assert.equal(timers[0].ms, 5000);
  });

  test("the ceiling asks the renderer to stop rather than assuming it did", () => {
    const { ptt, sent, indicator, timers } = harness({ maxCaptureMs: 5000 });
    ptt.setCapturing(true);
    timers[0].fn();
    assert.deepEqual(sent, [{ action: "stop", reason: "capture-timeout" }]);
    // Still lit: the renderer holds the stream, so until it reports the stop the
    // microphone really is still open, and the indicator must keep saying so.
    assert.deepEqual(indicator, [true]);
    assert.equal(ptt.isCapturing(), true);
    ptt.setCapturing(false);
    assert.deepEqual(indicator, [true, false]);
  });

  test("stopping normally cancels the ceiling", () => {
    const { ptt, timers } = harness({ maxCaptureMs: 5000 });
    ptt.setCapturing(true);
    ptt.setCapturing(false);
    assert.equal(timers[0].cleared, true);
  });

  test("the shipped ceiling is a real bound, not a placeholder", () => {
    assert.ok(MAX_CAPTURE_MS > 0 && MAX_CAPTURE_MS <= 10 * 60 * 1000);
  });
});

describe("IPC surface", () => {
  test("both channels are registered, and nothing else", () => {
    const handlers = new Map();
    const ipc = { handle: (ch, fn) => handlers.set(ch, fn) };
    const { ptt } = harness();
    registerPushToTalkIpc(ipc, ptt, IPC_CHANNELS);
    assert.deepEqual(
      [...handlers.keys()].sort(),
      [IPC_CHANNELS.capturing, IPC_CHANNELS.hotkeyBind].sort()
    );
  });

  test("the bind channel validates its argument — a renderer can send anything", async () => {
    const handlers = new Map();
    const ipc = { handle: (ch, fn) => handlers.set(ch, fn) };
    const { ptt } = harness();
    registerPushToTalkIpc(ipc, ptt, IPC_CHANNELS);
    const r = await handlers.get(IPC_CHANNELS.hotkeyBind)(null, "../../etc/passwd");
    assert.equal(r.ok, false);
    assert.equal(ptt.boundChord(), "");
  });

  test("the preload exposes push-to-talk with no start method", () => {
    const src = readFileSync(join(__dirname, "..", "preload.js"), "utf8");
    assert.match(src, /pushToTalk:/);
    // The absence is the point: nothing on the bridge lets the shell (or an app
    // reaching the bridge) open the microphone. Only the renderer can, and only from a
    // user gesture.
    assert.ok(!/pushToTalk:[\s\S]{0,600}?\bstart:/.test(src));
  });
});

describe("system audio is refused honestly, not half-shipped (T3.3)", () => {
  test("the capability exists in the vocabulary", () => {
    assert.ok(CAPABILITIES.includes("system_audio"));
  });

  test("it probes unavailable on macOS WITH a reason", () => {
    const caps = makeCapabilities({ platform: "darwin" });
    const s = caps.probe("system_audio");
    assert.equal(s.available, false);
    assert.equal(s.granted, "unavailable");
    assert.equal(s.requestable, false);
    // The reason must explain the trade rather than read as an unfinished port.
    assert.match(s.reason, /Screen Recording/i);
    assert.match(s.reason, /microphone only/i);
  });

  test("the reason is the same refusal on every platform", () => {
    for (const platform of ["darwin", "win32", "linux", ""]) {
      const s = makeCapabilities({ platform }).probe("system_audio");
      assert.equal(s.granted, "unavailable");
      assert.equal(s.reason, SPECS.system_audio.unsupported, `platform ${platform}`);
    }
  });

  test("requesting it cannot prompt", async () => {
    const caps = makeCapabilities({ platform: "darwin" });
    const r = await caps.request("system_audio");
    assert.equal(r.granted, false);
    assert.equal(r.prompted, false);
  });

  test("no source file captures system audio", () => {
    for (const f of ["main.js", "capabilities.js", "pushToTalk.js", "preload.js"]) {
      const src = readFileSync(join(__dirname, "..", f), "utf8");
      // The census that keeps T3.3 honest: the doc says mic-only, so there must be no
      // system-audio capture call anywhere for the doc to be wrong about.
      assert.ok(
        !/desktopCapturer|audioLoopback|systemAudio\s*[:=]\s*true/.test(src),
        `${f} must not capture system audio`
      );
    }
  });

  test("the guide states mic-only, so doc and probe agree", () => {
    const guide = readFileSync(
      join(__dirname, "..", "..", "docs", "guides", "desktop.md"),
      "utf8"
    );
    assert.match(guide, /microphone only/i);
    assert.match(guide, /system audio/i);
  });
});
