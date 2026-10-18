# Buddy settings

Every setting has a default, an effect, one home and a reader. Settings → Buddy shows all of them;
the context menu and the tray reach the common ones ([#3901]).

| Setting | Default | Effect | Home |
|---|---|---|---|
| Buddy status | off | on: the buddy window, and the buddy's faces on the tray, the dock and alert notifications; turning it on or off applies live to every buddy surface, with no restart | owner knob (config, with the gateway's reader) |
| Where it starts | docked at the top edge | docked or floating at the last place it was left | this machine (the shell's store) |
| Its place | — | the floating position and the display, kept per machine | this machine (the shell's store) |
| Surfaces | the window, the tray face and the dock face on; notifications for alerts follow the notification rules | which surfaces show the buddy's state | owner knob |
| Hide from screen sharing and screenshots | on | the buddy window is excluded from screen capture, so an approval's brief never shows in a share | this machine |
| Focus shortcut | none | a key chord that opens the buddy and gives it keyboard focus | owner knob, with the push-to-talk chord grammar; still open against the keyboard model ([`interactions.md`](interactions.md#keyboard-and-focus)) |
| When I'm away | hide after the absence time | docked: hides after `absence`; floating: dozes; alerts still appear | owner knob |
| Quiet hours | the existing quiet hours | in quiet hours alerts arrive as a quiet peek with no sound; nothing ambient shows | the existing notification settings |
| Reduced motion | the system's setting | shown, not set: whether it is on, and where to change it in the system's accessibility settings | the system |
| Notification posture | the existing notification settings | shown, not set: the mute, quiet hours and alert rules the buddy's alerts follow, with a link to Settings → Notifications | the existing notification settings |

## Where each setting lives, and why

- **Owner knobs** are in the gateway's config with all five points of the config contract, and the
  gateway reads each one: it serves the buddy's effective policy (`GET /api/buddy/policy`: Buddy
  status, which surfaces, the presence timings, and whether an alert may interrupt now under quiet
  hours and mute) so every window and every machine follows one answer. That reader is what keeps a
  desktop-only switch from being a setting nothing reads.
- **This machine's** placement and screen-capture choice live in the desktop shell's store, which
  gains a small preload API so Settings can show and change them.
- **Appearance** (the colour scheme, expressiveness, bounciness, personality) and the sound-cue
  switch are not buddy settings: the buddy follows them. Today they live in each browser window's
  storage and a second window reads them only when it opens, so the buddy work makes them update
  live across windows of the same app.
- **Reduced motion** follows the system. The buddy has no switch of its own for it, so it adds no
  config field and no policy entry; Settings → Buddy shows whether it is on and where to change it.
- **The way out.** The context menu and the tray each offer turning Buddy status off.

## Later work

Settings for later work appear only when that work returns: the drop, quick ask, the sprite sheet
and the bridges each bring theirs ([#3895], [#3903], [PersonalClaw/PersonalClawApps#164]).

### Play reactions

Later work: play ([#3894]).

| Setting | Default | Effect | Home |
|---|---|---|---|
| Play reactions | on | off: no gaze follow, no reactions to hits, drags or drops beyond what an action needs; fidgets stop | owner knob |

### Sound and volume

Later work: sound ([#3900]).

| Setting | Default | Effect | Home |
|---|---|---|---|
| Sound | off | on: the buddy's sound set plays for the events in [`sound.md`](sound.md), never for ambient updates | owner knob, beside the existing sound-cue switch |
| Volume | low | the buddy's sounds only | owner knob |

[#3894]: https://github.com/PersonalClaw/PersonalClaw/issues/3894
[#3895]: https://github.com/PersonalClaw/PersonalClaw/issues/3895
[#3900]: https://github.com/PersonalClaw/PersonalClaw/issues/3900
[#3901]: https://github.com/PersonalClaw/PersonalClaw/issues/3901
[#3903]: https://github.com/PersonalClaw/PersonalClaw/issues/3903
[PersonalClaw/PersonalClawApps#164]: https://github.com/PersonalClaw/PersonalClawApps/issues/164
