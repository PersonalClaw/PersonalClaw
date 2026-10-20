# Buddy character

The buddy is one orb of dots, drawn the way the chat's agent orb is
([`agent-orb.md`](../agent-orb.md), which [#3807] builds): the same phases in the same four
families, the active family in the same orb states, and the agent orb's colour while an agent is
alive. Its whole job is to make the agents' state readable at a glance, at any size, while staying
good company ([#3830]). The maintainer chose it over three character directions on 2026-10-18
([the choice](#the-choice)).

The choice changes the buddy alone; nothing else in PersonalClaw's look changes with it. The
ClawMark stays the brand mark everywhere else: the app icon, the menu-bar image, the loading mark,
the wordmark and the backdrop's claw dot glyph keep the claw, and the buddy never draws it. The
buddy's stills stand in on the tray and the dock only to show its state, while Buddy status is on
([`settings.md`](settings.md)).

## Constraints

- **One orb of dots, drawn the way the agent orb is.** Where the two show the same phase, they show
  it in the same family, and in the active family in the same orb state: breathing, searching,
  working, solving, connecting, composing or listening
  ([`agent-orb.md`](../agent-orb.md#phase-to-state)). The quiet, waiting and blocked poses are the
  buddy's own variants of the agent orb's still frames ([the poses](#the-poses)), and the quiet and
  blocked ones are drawn in the neutral, since nothing in them is alive. Everything the buddy adds
  to the agent orb's drawing, these variants among them, is listed once, with the rig's candidates
  ([`rig.md`](rig.md#candidates)). Its outline is round at every size, and the ClawMark's
  silhouette is no part of it.
- **State through the orb's motion, density and badge.** Everything a family means survives with
  only the orb's density and its badge, so it holds in a still frame: under reduced motion, on the
  tray, and in a macOS template image, which has one colour and carries the state in shape alone.
  Motion adds to them and never carries a family by itself.
- **Exact state text stays beside it.** The family's look carries urgency, and words always name the
  exact phase: the badge unfolds into them, and the focused status card shows them. Colour, motion
  and the small drawing never carry the phase alone.
- **No colours of its own.** While an agent is alive, working or waiting on the owner, the dots are
  `--color-orb-alive`, the active scheme's primary, as the agent orb's are
  ([`agent-orb.md`](../agent-orb.md#the-component)), so in a teal scheme they are teal: the buddy
  never hard-codes coral. Where nothing is alive (at rest, done, blocked or asleep) they are
  `--color-orb-rest`, which is `--color-on-surface-var`, the neutral the app already uses for idle
  icons ([`web/DESIGN.md` §2](../../../web/DESIGN.md#neutral)). Both are the agent orb's, from the shared
  brand and state tokens ([`web/DESIGN.md` §2](../../../web/DESIGN.md#the-token-layers)); the buddy
  names no colour token of its own.
- **Semantic colour for alerts only, as a badge or a wash, never as the whole orb:** warn for an
  approval, info for a question, danger for an error, ok for done. The wash is a soft gradient up
  from the orb's lower edge, in the alert's tone. While an agent works there is no wash: the dots
  already carry the alive colour.
- **The badge is the caption, folded.** A small badge sits on the orb's upper edge: the working mark
  (three dots) while an agent works, a mark for each alert kind, a check for done, the microphone
  while listening and the offline mark while offline. It holds still, since the orb's own dots
  carry the motion. Resting the pointer on it unfolds it into the state's words, and it folds back
  when the pointer leaves. On the tray and the dock, the badge is what carries the state.
- **It moves as an orb.** Its dots gather and spread, crowd to one side, quicken, slow and go still,
  dim and brighten, and the whole orb may lift, sink, swell, dip or sway a little and come back. It
  never squashes, stretches or turns into another shape, and it has no face to turn: no eyes, no
  mouth, no limb.
- **Reads at 18, 24, 48 and 96 px**, in light and dark, in every colour scheme, with a still frame
  for every pose under reduced motion. The agent orb tunes each of its two sizes as a drawing of its
  own rather than scaling one; which of the buddy's sizes share those drawings and which need their
  own is the rig's to settle ([`rig.md`](rig.md#starting-position)).
- **Original:** the dots and their states are the agent orb's licensed base, shared on purpose
  ([`agent-orb.md`](../agent-orb.md#the-component), [#3807]). What the buddy adds (its outline,
  badge, wash, poses, gestures, presence and, later, its sound) and the whole orb are checked side
  by side with other products' status indicators and assistant orbs, from public material. They
  pass if a viewer would recognise no drawn form, choreography or sound in them as another
  product's ([#4570]), and the record names none of those products.
- **Drawn in parts a renderer can move:** the dots, the badge and the wash, each separate, with the
  orb's centre as the pivot of its whole-orb moves. Every colour is a token, never baked in, and
  nothing glows: the alive colour is the dots' own.

## The choice

Made on 2026-10-18, at gate one ([#3830]): the maintainer chose none of the three directions below,
and in their place an animated orb made of dots, in the same visual language as the agent orb. The
rest of this file is the design worked out from that choice, which the maintainer reviews with the
poses. They are drawn next, at real sizes, with the side-by-side check of what the buddy adds and
of the whole orb ([the constraints](#constraints)) recorded here beside them, where the
originality record cites it ([#4570]). With no body, the buddy needs no body colour: the
warm-neutral token the brief asked for is withdrawn, and nothing is added to the design system for
the buddy.

### Three directions to explore

The three directions the brief set out to explore:

1. **The living mark**, the ClawMark itself as the creature: rejected in favour of the dot orb.
2. **The small claw creature**, a rounded body with two claws: rejected in favour of the dot orb.
3. **The face in the island**, the island or the pill as a face: rejected in favour of the dot orb.

## The poses

A pose is the orb's look for a state: what its dots do, how dense they are, its colour, its badge
and its wash. Every pose has a still frame for reduced motion. The v0.3.0 set, which the status MVP
needs, starts with the four families. Each reads at 18 px, and every truth phase shows through its
family with its words beside it: queued, thinking, searching, tool (working), planning, delegating,
streaming (writing), listening, approval, question, error, done, idle, restarting and offline.

| Family | Phases | The orb | Its badge |
|---|---|---|---|
| Quiet | done, idle | at rest: sparse and still, in the neutral | none at idle; after done, the check in the ok tone |
| Active | queued, thinking, searching, tool, planning, delegating, streaming, listening, restarting | full, its dots moving in the agent orb's state for the phase (restarting takes working's), in the primary; its still is full and even | the working mark; the microphone while listening |
| Waiting | approval, input | full and still, in the primary: drawn in tight for an approval, lifted a little for a question, with the alert's wash | "!" in the warn tone for an approval, "?" in the info tone for a question |
| Blocked | error, offline | settled low and still, in the neutral: with the danger wash for an error, dimmed while offline | a mark in the danger tone for an error; the offline mark |

Two tones stay open, as the phase contract leaves them: queued's and listening's, so those two
phases may yet leave the primary ([`signals.md`](signals.md#1-one-phase-contract)). The rest of the
set:

- **The focused status card's orb,** at the card's size, for each family.
- **Presence:** peek (the orb just below the top edge), compact, expanded (the orb large), floating,
  dozing and sleeping (still and dimmed), waking (brightening back into its pose).
- **Answers:** the yes nod (the orb dips once), the no shake (once side to side), answered
  elsewhere (the badge folds away and the orb goes back to the pose under the alert).
- **Still orbs for the tray and the dock:** idle, working, needs you, error, listening, offline, at
  16, 18 and 22 px, plus macOS template versions for the menu bar. Each is a still of its family's
  pose: idle is quiet; working and listening are active; needs you is waiting; error and offline
  are blocked. In a template image the badge's shape and the orb's density carry the state alone.

## The gesture vocabulary

The orb has no limb, so its gestures are motions of its dots and of the whole orb. A small set, each
with one meaning:

- **point** (toward what changed): a short reach of dots toward it, drawn back at once.
- **wave** (greeting and goodbye): one ripple runs out through the dots from the centre.
- **stir** (working rhythm): the dots move in the agent orb's state for the phase. Its variants all
  mean that work is running, and the words say which phase.
- **raise** (a question): the orb lifts a little and goes still.
- **hold** (stop: an approval is waiting): the dots draw in tight and go still.
- **droop** (an error): the dots sink toward the lower edge and stay there, still.
- **flourish** (done): the dots swirl once around the orb and settle, soft.

Nothing else; a motion never means two things.

## Later work

### Play and drop poses

Later work: play ([#3894]), and the drop poses with drop and quick ask ([#3895]).

The orb has no eyes, so where play speaks of its gaze, it means the orb's lean: the side its dots
crowd to.

- **Play:** greeting (a quick swell to full size, one wave, a blink), follow (the lean at its limit
  in eight directions), poked (the dots scatter a little and regather), annoyed (a shiver through
  the dots, and the orb grows a little), dizzy, dragged (dangling behind the pointer), glance (a
  lean toward the app's window, or toward the control under the pointer), blink (the dots dim for
  an instant), look around (the lean drifting once around the rim).
- **Drop:** file over (the dots spread wide, ready to catch) and swallowing, where the orb becomes a
  container for the dropped file and comes back round, with one more gesture, **wrap** (holding a
  dropped file: the dots close around it and hold it up).

Settled for their return on 2026-10-14: there is no dizzy, and the drop is a catch, the pose
"holding", in place of swallowing ([#4570]). For the orb, the catch is the wrap: the spread dots
close around the file and hold it up while the choice opens. The portable sprite sheet's frames are
an export of the chosen rig ([`rig.md`](rig.md#the-portable-sprite-sheet)).

[#3807]: https://github.com/PersonalClaw/PersonalClaw/issues/3807
[#3830]: https://github.com/PersonalClaw/PersonalClaw/issues/3830
[#3894]: https://github.com/PersonalClaw/PersonalClaw/issues/3894
[#3895]: https://github.com/PersonalClaw/PersonalClaw/issues/3895
[#4570]: https://github.com/PersonalClaw/PersonalClaw/issues/4570
