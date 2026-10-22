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
and in their place an animated orb made of dots, in the same visual language as the agent orb. Its
colour is the scheme's primary while an agent is alive and Ink Variant where nothing is, and it is
no creature: it has no eyes, mouth or limb. The rest of this file is the design worked out from
that choice. The v0.3.0 poses are now drawn at real sizes ([the drawn poses](#the-drawn-poses)),
with contact sheets the maintainer can look at any time; the side-by-side check of what the buddy
adds and of the whole orb ([the constraints](#constraints)) is recorded beside them
([the side-by-side check](#the-side-by-side-check)), where the originality record cites it ([#4570]). With no body, the buddy needs no body
colour: the warm-neutral token the brief asked for is withdrawn, and nothing is added to the design
system for the buddy.

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

The phase contract's two last tones are settled ([`signals.md`](signals.md#1-one-phase-contract)):
queued work is drawn in the primary like the rest of the active family, and listening keeps the
warn tone for its microphone, while its dots stay the primary's. The rest of the set:

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

## The drawn poses

Every pose of the v0.3.0 set, and every still the tray and the dock show, is drawn on the agent
orb's drawing the way the rig's in-place candidate draws it ([#3887],
[`decisions/rig.md`](decisions/rig.md)): the dots are the agent orb's engine's, moved by the rig's
poses (their density, how far they spread, how far they sink and their swirl), and the badge and the
wash are drawn as dots in the alert's tone. The layered candidate reads the same table of poses and
the parts stay apart, so whichever extension the rig settles on draws these sources without rework.
One table holds them all (`web/src/buddyRig/poseSet.ts`), `web/src/buddyRig/poseSources.ts` draws
them, and a test holds the committed files to what the table draws.

- **A file per pose, its still.** `poses/<pose>.svg` is the pose as it shows under reduced motion:
  `<family>-<phase>` for each phase in the table above, `card-<family>` for the focused card's orb,
  and `presence-<name>` and `answer-<name>` for the lists below it. A pose that moves has
  `poses/<pose>.motion.svg` beside it, the instant its motion is drawn at: the active family's dots
  mid-motion, the peek half above the top edge, the waking orb not yet at full brightness, the nod
  at the bottom of its dip, the shake at its widest, and the badge half folded as an answer
  elsewhere closes it.
- **In parts, round, in the tokens.** Each source is one whole orb, its pivot at its centre,
  holding its dots, its wash and its badge as separate groups, and the whole orb only moves, turns
  or scales by one factor. Every colour is a token set in a style (`--color-orb-alive`,
  `--color-orb-rest` and the alert tones), so one file is the pose in light, in dark and in every
  scheme. A dot's depth is its opacity, which is how the agent orb's painter shades a tinted dot
  toward the canvas.
- **Sizes.** A pose is drawn at the room its surface gives the orb
  ([`surfaces.md`](surfaces.md#the-room-budget)): the buddy's one orb at 48 in every state, docked
  or floating ([#5216]), 96 where one piece of work owns the phone's screen, and the small orbs of
  the work in sight, the tray and the phone's rows. Below 24 px the badge drops its
  ring of dots and its mark fills it, since a ring that small reads as a blur. A waiting badge (the
  "!" and the "?") is solid there instead: one dot of the badge's size in the full warn or info
  tone, its mark cut out of it, so the badge alone tells a still waiting orb from an active one, in
  colour and as a template, where motion cannot.
- **The tray's and the dock's stills** are `poses/tray-<still>-<size>.svg` at 16, 18 and 22 px,
  each its family's still with the badge inside the square, and beside each a `-template.svg`: black
  on clear with no wash, for the macOS menu bar, which recolours it. The rasters the tray and the
  dock load are their task's export ([#3896]): the macOS menu bar takes the templates, the Linux
  tray the same template drawing painted in its appearance's own ink (`--color-on-surface`), and
  the dock the colour drawing at 22 px. At 18 px the colour drawing's dots, shaded by their
  opacity, hold under 3:1 against a plain bar, which the one ink holds for every still
  (`desktop/test/buddyStillsContrast.test.js`).
- **The marks the buddy adds:** the microphone while listening and a slash while offline, beside the
  agent orb's "!", "?" and "×", the working mark and the check.
- **What the drawing settles:** presence and the answers are drawn on the working pose, the one an
  alert interrupts and an answer goes back to; dozing dims it to 60%, sleeping dims the idle orb to
  40%, and offline dims the error's settled dots to 55% with no wash. Every amplitude is the default
  dials'.

The contact sheets show every pose at 18, 24, 48 and 96 px, light above dark, each with its words
beside it and its motion frame at 48 px, and every still at its size in colour and as a template,
with the 18 px ones also at four times: [the four families](poses/sheets/families.svg),
[the focused card's orb](poses/sheets/card.svg), [presence](poses/sheets/presence.svg),
[the answers](poses/sheets/answers.svg) and [the tray and the dock](poses/sheets/tray.svg). They are
drawn in the default scheme, for the maintainer to look at whenever they like; they are not a gate.

### The side-by-side check

Made on 2026-10-22 on the written design and the drawn poses ([#4570]), from public material only,
by the rule in [the constraints](#constraints): what the buddy adds and the whole orb, against other
products' status indicators and assistant orbs. The dots and their states are the agent orb's
licensed base and are not judged on their own. Each aspect is a row of the originality record
([`decisions/originality.md`](decisions/originality.md#the-character)).

| Aspect | Result |
|---|---|
| The outline | distinct: a round orb of many small separate dots in one colour, with no glow, no gradient fill and no liquid surface; never a few large dots, never a colour per dot, never a ring |
| The badge | distinct: a still mark on the orb's upper edge, one of the app's own marks, that unfolds into the state's words; its working mark never pulses dot by dot |
| The wash | distinct: one alert tone that rises once from under the orb and holds; it never pulses, never takes several colours and never lights the screen's edge |
| The poses | distinct: the families are told apart by the dots' density and spread and by the badge, in two colours; no colour per state, no spinning arc |
| The gestures | distinct: motions of one orb's dots; the wave is a ripple out from the centre, and the flourish is one swirl that comes to rest |
| The presence | changed: one orb of one size that stays where the owner put it, its state only in its dots, badge and wash, with a card beside it on the side that has room; nothing forms around it or grows out of the edge ([#5216], [`surfaces.md`](surfaces.md#docking), [`motion.md`](motion.md#transitions)) |
| The whole orb | distinct: a sphere of dots in the app's primary, with a still badge and a one-tone wash; it reads as the agent orb |

The checks. `tests/rails/test_buddy_design_matrix.py` reads the set from this file's own words (the
family table's phases, the focused card's orb for each family, the presence and answer lists, and
the stills at each size with their template versions) and holds `poses/` to it: a source for each,
none for a pose this file does not name, and a motion frame beside each pose that moves. It holds
each source to its parts, its tokens and its round outline. At 18 px it tells the four families
apart by their dots, at least a quarter more of them or 5% of the orb's size in how far they reach
from its centre or where their middle sits, and by their badge, in colour and as templates, with
waiting's badge at full strength and at least four times the ink of active's mark; and it holds
the contact sheets to every pose at its sizes in both modes, with its words.
`web/src/buddyRig/poseSources.test.ts` holds the files to the table and the table to the phase
contract. After a change to the table, redraw them from `web/`:

```bash
PC_WRITE_BUDDY_POSES=1 npx vitest run src/buddyRig/poseSources.test.ts
```

## The component

`web/src/ui/BuddyOrb.tsx` draws the orb in the app ([#3891]), on the in-place rig
[`decisions/rig.md`](decisions/rig.md) chose: one canvas, painted with the agent orb's engine and
painter, in the pose the table above gives each state, with the state's exact line beside it so the
art never carries the state alone. The buddy's window, the playground, the focused card and the
phone's views all draw this one component.

- **Poses and transitions.** `web/src/ui/buddy/orbPoses.ts` is the transitions table: each entry
  names one of `design/motion.ts`'s presets and the timing token it is held to, so the renderer
  writes no spring and no length of its own. Each pose parameter moves on its preset, stepped on the
  dots' capped clock (`web/src/ui/buddy/useOrbPose.ts`).
- **Colour.** The dots are `--color-orb-alive` or `--color-orb-rest`, and the badge and the wash
  the alert's tone; a scheme change repaints the orb as it lands. Under more contrast the dots take
  the words' ink, and under forced colours the system's, as the agent orb's do.
- **Cost.** It paints only while its dots or its pose move, at most 30 frames a second, and nothing
  at all while a pose holds, while the page is hidden or while the orb is out of view.
- **The dials and reduced motion.** At bounciness 0 no pose passes its target, and at the
  expressiveness floor every state still moves the orb. Under reduced motion every state is its
  still and a change jumps, stated on `data-buddy-orb`; switching it keeps the same orb, state and
  line.

`web/src/ui/BuddyOrb.test.tsx`, `BuddyOrb.frames.test.tsx` and `buddy/orbPoses.test.ts` hold it to
this.

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
[#3887]: https://github.com/PersonalClaw/PersonalClaw/issues/3887
[#3891]: https://github.com/PersonalClaw/PersonalClaw/issues/3891
[#3894]: https://github.com/PersonalClaw/PersonalClaw/issues/3894
[#3895]: https://github.com/PersonalClaw/PersonalClaw/issues/3895
[#3896]: https://github.com/PersonalClaw/PersonalClaw/issues/3896
[#4570]: https://github.com/PersonalClaw/PersonalClaw/issues/4570
[#5216]: https://github.com/PersonalClaw/PersonalClaw/issues/5216
