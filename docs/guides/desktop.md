# The desktop app

PersonalClaw's desktop shell is a thin Electron wrapper around the same gateway and
dashboard you get in a browser. It exists for the things a browser tab cannot do: a
menu-bar presence, native permissions, and a **global push-to-talk shortcut** that works
while another app has focus.

Everything the shell adds is a *capability*, granted individually and visible in
**Settings → Security → Desktop capabilities**. In a browser tab that panel says "desktop
app not connected" and none of this applies.

## The menu-bar item

The shell puts an icon in the macOS menu bar and keeps it there while PersonalClaw is
running. Its menu is live, refreshed every few seconds from the local gateway:

- **Approvals waiting** — how many tool approvals are pending. Clicking the row opens
  the chat surface where they are answered. It stays clickable at zero, so it is a way
  in rather than a row that greys out the moment you catch up.
- **Loops running** — a submenu of the loops currently `running`; each entry deep-links
  to that loop. Paused, blocked and awaiting-input loops are *not* counted here — they
  are active, but nothing is working on them.
- **Quick Capture Note** — opens the Inbox ready to capture. *(The note-writing half is
  not built yet; today this is a shortcut to the Inbox.)*
- **Open Dashboard**, **Open at Login**, and **Quit PersonalClaw**.

When the count beside the icon and the "● Listening" capture indicator want the same
space, **listening always wins** — a live microphone is never hidden behind a badge.

If the gateway cannot be reached the menu says **"not connected"** rather than showing
zeroes, because a zero looks like good news.

### Closing the window is not quitting

Closing the window hides it; the menu-bar item brings it back, and PersonalClaw keeps
running. If the menu-bar item could not be created — a missing icon, or a platform
without one — the shell notices and closes the window for real instead, so you can
never end up with a running app you have no way to reach.

## Open PersonalClaw at login

**Off by default.** There are two places to turn it on, and they are the same switch:
the menu bar's **Open at Login**, and **Settings → Security → Desktop capabilities →
Open at login**. Flip either and the other follows — there is one registration, not one
per surface.

It registers this app with macOS Login Items — the same list in **System Settings →
General → Login Items**, where you can also remove it without launching PersonalClaw. It
needs no administrator password, writes no launch agent of its own, and turning it on
twice cannot leave two entries behind.

macOS owns this setting, so PersonalClaw keeps no copy of it. Both surfaces read the
registration from the OS every time they draw, which is why removing it in System
Settings shows up here rather than leaving a switch stuck on. If macOS declines the
change, the switch says so and stays where the OS actually left it.

In a browser tab there is no switch: registering a login item needs the desktop app, and
a toggle that could not do anything would be worse than an honest absence.

## Quitting shuts the gateway down, not off

The desktop app starts its own gateway, so quitting has to stop it in the right order.
Quit asks the gateway to shut down and then **waits for it to actually exit** before the
app goes away, rather than sending a signal and disappearing — the difference between a
clean stop and a half-finished write. A gateway that will not stop is escalated after a
few seconds, and if it still will not, the app says so in its log rather than pretending
the quit was clean.

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

## Native notifications

The desktop app can deliver notifications as real OS notifications instead of only as a
badge on the dashboard's bell. It is **per notification kind**, not a global switch:
Settings → Notifications → the rules matrix → open a row's *detail* → tick **Desktop
notification**. Nothing is ticked for you.

Two things follow from where that switch lives:

- **A kind you have set to Badge or Digest never raises one**, even with Desktop ticked.
  Those modes mean "do not interrupt me", and an OS banner is an interruption. Only the
  **Notify** mode delivers natively.
- **The dashboard is always the record.** A native notification is an *addition* — the
  note still lands in the bell and the notifications feed. So nothing is lost when the
  desktop app is closed: the rule simply falls back to the dashboard delivery it would
  have had anyway, and the note's stored detail says why (`the desktop shell is not
  connected`).

Clicking a notification brings PersonalClaw forward and opens the surface the note came
from — an inbox alert opens Inbox, a loop's progress opens Loops, a skill proposal opens
Skills. A kind with no surface of its own opens the notifications feed.

macOS never tells an app whether notifications are authorized, so PersonalClaw cannot show
you a truthful "granted" state for this one and does not pretend to (Settings → Security →
Desktop capabilities says as much). The first notification asks; after that, **System
Settings → Notifications → PersonalClaw** is the only control. If you have turned them off
there, ticking Desktop in a rule will silently do nothing — that is macOS's answer, not a
bug in the rule.

## Related

- [Platforms](platforms.md) — which OSes the desktop shell targets.
- [Configuration reference](../reference/configuration.md) — `voice.push_to_talk_chord`
  and the rest of the voice settings.
