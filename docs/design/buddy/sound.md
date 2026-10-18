# Buddy sound

v0.3.0 makes no sound. Sound is later work, and until it returns every buddy alert notification is
silent ([#3897]).

## Later work

### Event map

Later work: sound ([#3900]).

Sound is off by default. When it is on, the buddy makes a sound only when something changed that
the owner would want to know without looking, and never for an ambient update.

| Event | Sound | Length |
|---|---|---|
| An approval arrives | a soft rising two-note call | under 600 ms |
| A question arrives | the call with a lifted last note | under 600 ms |
| Work fails | a low settling note | under 500 ms |
| Work is done (the focused work, while visible) | a warm single chime | under 400 ms |
| An answer is sent from the buddy | a short tick | under 150 ms |
| Listening starts, and ends | a small open and a small close | under 200 ms each |
| A dropped file is swallowed | a soft gulp | under 300 ms |
| Poked, annoyed, dizzy | three short voice-like blips, rising in fuss | under 400 ms each |
| Phase changes, work starting, in-sight updates, fidgets, the greeting, presence changes | silence | — |

Settled for its return on 2026-10-14: the drop's sound is a soft closing note on the catch, not a
gulp, and with the hit chain ending at annoyed the blips are for poked and annoyed only ([#4570]).
[#3900] changes the map to these when it resumes.

### Rules

Later work: sound ([#3900]).

- Off by default; the owner switches it on in Settings → Buddy. The existing sound-cue switch and
  its cap still apply, so the buddy is never louder than the app's own cues.
- Silent in quiet hours, when muted, when the system output is muted, and while listening (the
  microphone is open).
- One sound at a time: a sound that would start while another plays waits, and alerts take
  precedence over everything else.
- Native notifications sent for the buddy's alerts are silent unless sound is on.
- When it returns, it stays off by default, silent for ambient updates, in the owner's control,
  licensed, capped, and testable with no audio loaded on first load ([#3900]). The context menu
  gains Sound on or off.

### Brief for the original set

Later work: sound ([#3900]).

PersonalClaw's own sounds, made for it and licensed to the project, with no sample taken from
another product. Warm and soft, rounded attacks, no harsh highs; they should sound like the coral
light looks. Short enough never to talk over speech. Delivered as small compressed files with their
licence recorded in the project's asset-licence list, or synthesised at run time from a small
parameter set, whichever the rig decision favours. The maintainer signs off on the set before it
ships.

[#3897]: https://github.com/PersonalClaw/PersonalClaw/issues/3897
[#3900]: https://github.com/PersonalClaw/PersonalClaw/issues/3900
[#4570]: https://github.com/PersonalClaw/PersonalClaw/issues/4570
