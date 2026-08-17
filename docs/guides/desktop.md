# The desktop app

PersonalClaw's desktop shell is a thin Electron wrapper around the same gateway and
dashboard you get in a browser. It exists for the things a browser tab cannot do: a
menu-bar presence, native permissions, and a **global push-to-talk shortcut** that works
while another app has focus.

Everything the shell adds is a *capability*, granted individually and visible in
**Settings → Security → Desktop capabilities**. In a browser tab that panel says "desktop
app not connected" and none of this applies.

## Push-to-talk

Press the shortcut, speak, press it again. The recording is transcribed by whichever
model you bound to the **STT** use case (Settings → Models) and the text is inserted into
the composer **at your cursor** — it does not replace or append to what you had already
typed.

Dictation needs somewhere to land, so the shortcut is armed wherever a composer is — chat and
the goal composer. On a screen with no composer (Settings, say) it does nothing rather than
recording into nowhere, which also means the capture indicator is never lit somewhere you cannot
see what it is feeding.

The shortcut is configurable in **Settings → Speech & Transcription → Hands-free voice**.
The default is `⌘⇧Space`. A shortcut needs at least one modifier: a bare key would be
taken away from every other app on your machine, so PersonalClaw refuses to bind one. If
another app already owns the chord you pick, the setting says so and keeps your previous
shortcut rather than silently failing at the next launch.

### It toggles; it does not read key-release

The global shortcut **toggles** capture: one press starts, the next stops. This is a real
platform limit rather than a shortcut we took — Electron's global-shortcut API delivers a
press and exposes no key-release event, and reading raw key-up system-wide would mean
requesting an accessibility-level input tap, which PersonalClaw does not do.

The practical difference is that you cannot hold the chord down. So that a forgotten
toggle cannot leave the microphone open indefinitely, a capture that runs past two
minutes is stopped for you.

### While it is capturing you can always see it

Two indicators are lit while the microphone is live, and both matter:

1. **macOS's own** orange microphone dot in the menu bar. That one is drawn by the
   system, and PersonalClaw cannot suppress or fake it — which is exactly why it is the
   one to trust.
2. **PersonalClaw's menu-bar item** changes to `● Listening`. This says *which* app is
   listening, which the system dot cannot. It is in the menu bar rather than in the page
   because the shortcut is global: if the window were hidden behind a full-screen app, an
   in-window indicator would be a capture indicator you could not see. Inside the app
   there is also a "Listening" chip beside the composer.

Releasing the toggle ends the recording and **stops the microphone track**. The app does
not hold an idle-but-open microphone between captures.

If you have not granted microphone access yet, the first capture asks. If you have
already denied it, PersonalClaw cannot re-ask — macOS will not prompt twice — so it tells
you that and points at Privacy & Security in System Settings.

## System audio is not captured — microphone only

PersonalClaw captures **the microphone only**. It does not capture system audio: not what
your speakers are playing, not the other side of a call, not another app's output.

This is a deliberate refusal, not an unfinished feature. macOS exposes no audio-only tap
for system output; the only route is the **Screen Recording** entitlement's audio
side-channel, which would mean asking you for the right to record your screen in order to
record sound. That is not a trade worth making quietly for a transcription feature, so
there is no code path in PersonalClaw that captures system audio at all.

Probing the capability reflects this: `system_audio` reports `unavailable` with that
reason on every platform, rather than reporting "not implemented" as though a later
release will simply turn it on.

## Related

- [Platforms](platforms.md) — which OSes the desktop shell targets.
- [Configuration reference](../reference/configuration.md) — `voice.push_to_talk_chord`
  and the rest of the voice settings.
