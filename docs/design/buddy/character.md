# Buddy character

The buddy is one orb, drawn the way the chat's agent orb is: the agent orb's halftone, a sphere of
dots that becomes a picture of what the agent is doing ([`agent-orb.md`](../agent-orb.md), which
[#3807] built and the owner chose in its round-6 form on 2026-11-16): the same phases in the same
four families, each as the same one of the agent orb's states, and the agent orb's colour while an
agent is alive. Its whole job is to make the agents' state readable at a glance, at any size, while
staying good company ([#3830]). The maintainer chose it over three character directions on
2026-10-18 ([the choice](#the-choice)).

The choice changes the buddy alone; nothing else in PersonalClaw's look changes with it. The
ClawMark stays the brand mark everywhere else: the app icon, the menu-bar image, the loading mark,
the wordmark and the backdrop's claw dot glyph keep the claw, and the buddy never draws it. The
buddy's stills stand in on the tray and the dock only to show its state, while Buddy status is on
([`settings.md`](settings.md)).

## Constraints

- **One orb, drawn the way the agent orb is.** Where the two show the same phase, they show it in
  the same family and as the same one of the agent orb's states, read from the one mapping
  (`web/src/lib/orbState.ts`, [`agent-orb.md`](../agent-orb.md#phase-to-state)), with one
  exception: the buddy's listening is a push-to-talk capture, so it is the recording dot, as the
  tray's listening still is. Its dots move exactly where the agent orb's do and hold still while it
  is ready, waits on you or stops. The buddy's done is the agent orb's done, the orb beside its
  plate with the check; a failure is the error state, sunk, with its ×. An announced restart is
  retrying, and the gateway away is offline. The quiet and blocked families are drawn in the
  neutral, since nothing in them is alive. Everything the buddy adds to the agent orb's drawing is
  listed once, with the rig's candidates ([`rig.md`](rig.md#candidates)). Its outline is round at
  every size, and the ClawMark's silhouette is no part of it.
- **State through the orb's picture and its plate.** Everything a family means survives with only
  the dots' picture and the plate's mark, so it holds in a still frame: under reduced motion, on the
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
- **Semantic colour for alerts only, as the plate's mark, never as the whole orb:** warn for an
  approval, info for a question, danger for an error, ok for done. The working states keep the
  tones the agent orb's own pictures give them (a diff's bands while editing, the recording dot),
  and the buddy adds none.
- **The badge is the plate, and the caption folded.** In a plate state (done, an approval, a
  question, an error, offline) the orb steps aside for a small plate at its lower right, the agent
  orb's own, holding the state's mark: the check, "!", "?", "×" or the broken link, in the state's
  tone. Work in progress has no badge, since its dots already carry it. The badge holds still once
  it lands. Resting the pointer on it unfolds it into the state's words, and it folds back when the
  pointer leaves. At 20 px and under the orb gives way to the mark, a dot at its upper left and
  the mark large, so on the tray and the dock the mark is what carries a plate state.
- **It moves as an orb.** Its dots move as the agent orb's state for the phase moves them, and a
  change of state morphs every dot into the next picture; the whole orb may lift, sink, swell, dim
  or sway a little and come back. It never squashes, stretches or turns into another shape, and it
  has no face to turn: no eyes, no mouth, no limb.
- **Reads at 18, 24, 48 and 96 px**, in light and dark, in every colour scheme, with a still frame
  for every pose under reduced motion. Below 30 px the halftone draws its dots larger and a plate
  state gives them up to its mark, so its state still reads, and the buddy's sizes take the same
  rule ([`rig.md`](rig.md#starting-position)).
- **Original:** the halftone and its states are the agent orb's, the project's own drawing,
  shared on purpose ([`agent-orb.md`](../agent-orb.md#the-component), [#3807]). What the buddy adds
  (its outline, badge, poses, gestures, presence and, later, its sound) and the whole orb are
  checked side by side with other products' status indicators and assistant orbs, from public
  material. They pass if a viewer would recognise no drawn form, choreography or sound in them as
  another product's ([#4570]), and the record names none of those products.
- **Drawn in parts a renderer can move:** the dots, the plate's mark and, on the desktop, the disc
  under the orb, each separate, with the orb's centre as the pivot of its whole-orb moves. Every
  colour is a token, never baked in, and nothing glows: the alive colour is the dots' own.

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

On 2026-11-12 the orb's dots became the claw ring's strokes ([#6117]), and on 2026-11-16 the owner
chose the halftone's round-6 form for the agent orb and the buddy alike, so the buddy is an orb of
dots again, the agent orb's own. The choice above stands: the buddy is no creature, and it never
draws the ClawMark.

### Three directions to explore

The three directions the brief set out to explore:

1. **The living mark**, the ClawMark itself as the creature: rejected in favour of the dot orb.
2. **The small claw creature**, a rounded body with two claws: rejected in favour of the dot orb.
3. **The face in the island**, the island or the pill as a face: rejected in favour of the dot orb.

## The poses

A pose is the orb's look for a state: the agent orb's picture for it, its colour, its badge and
what the whole orb does. Every pose has a still frame for reduced motion. The v0.3.0 set, which the
status MVP needs, starts with the four families. Each reads at 18 px, and every truth phase shows
through its family with its words beside it: queued, thinking, searching, tool (working), planning,
delegating, streaming (writing), listening, approval, question, error, done, idle, restarting and
offline.

| Family | Phases | The orb | Its badge |
|---|---|---|---|
| Quiet | done, idle | at rest: the sphere, still, in the neutral; done is the orb beside its plate, with one swirl and bump on arrival | none at idle; after done, the check in the ok tone |
| Active | queued, thinking, searching, tool, planning, delegating, streaming, listening, restarting | in the primary, its dots moving as the agent orb's state for the phase does: thinking's ripple, searching's scan, running a command's stream, planning's route, delegating's helpers and answering's bubble; listening is the recording dot, and an announced restart is retrying; its still is the state's rest frame | none: the dots carry it |
| Waiting | approval, input | the orb drawn in tight and lit, still, beside its plate | "!" in the warn tone for an approval, "?" in the info tone for a question |
| Blocked | error, offline | still, in the neutral: an error is the orb sunk beside its plate; offline is the orb dimmed beside its plate | "×" in the danger tone for an error; the broken link for offline |

The phase contract's two last tones are settled ([`signals.md`](signals.md#1-one-phase-contract)):
queued work is drawn in the primary like the rest of the active family, and listening is the
recording dot, the agent orb's own. The rest of the set:

- **The focused status card's orb,** at the card's size, for each family.
- **Presence:** peek (the orb just below the top edge), compact, expanded (the orb large), floating,
  dozing and sleeping (still and dimmed), waking (brightening back into its pose).
- **Answers:** the yes nod (the orb dips once, into done), the no shake (once side to side),
  answered elsewhere (the badge goes and the orb goes back to the pose under the alert).
- **Still orbs for the tray and the dock:** idle, working, needs you, error, listening, offline, at
  16, 18 and 22 px, plus macOS template versions for the menu bar. Each is a still of its family's
  pose: idle is quiet; working and listening are active; needs you is waiting; error and offline
  are blocked. Working is the running command's stream, which reads as at work in one ink where
  thinking's ripple does not (decided on the maintainer's delegation, 2026-11-17, "Decide yourself.",
  over the earlier mapping's thinking), and listening the recording dot. In a template image the dots' shape
  and the plate's mark carry the state alone.

## The gesture vocabulary

The orb has no limb, so its gestures are motions of its dots and of the whole orb. A small set,
each with one meaning:

- **point** (toward what changed): the orb leans a little toward it, drawn back at once.
- **wave** (greeting and goodbye): one ripple runs out through the dots from the centre.
- **stir** (working rhythm): the dots move as the agent orb's state for the phase does. Its
  variants all mean that work is running, and the words say which phase.
- **raise** (a question): the orb lifts a little and goes still.
- **hold** (stop: an approval is waiting): the orb draws in tight and goes still.
- **droop** (an error): the orb sinks and settles low, and stays there, still.
- **flourish** (done): the dots swirl once and the orb bumps, then settles, soft.

Nothing else; a motion never means two things.

## The drawn poses

Every pose of the v0.3.0 set, and every still the tray and the dock show, is drawn on the agent
orb's halftone: its dots are the agent orb's picture for the state (`web/src/ui/orb/halftone.ts`
and its pictures, `web/src/ui/orb/scenes.ts`), the very particles its painter draws, and its badge
is the plate's mark (`web/src/ui/orb/plateMarks.ts`), moved and dimmed by the pose as one whole orb
(`web/src/ui/buddy/buddyHalftone.ts`). The rig's in-place extension ([#3887],
[`decisions/rig.md`](decisions/rig.md)) is how they are drawn: one canvas, the parts apart. One
table holds them all (`web/src/buddyRig/poseSet.ts`), `web/src/buddyRig/poseSources.ts` draws them,
and a test holds the committed files to what the table draws.

- **A file per pose, its still.** `poses/<pose>.svg` is the pose as it shows under reduced motion:
  `<family>-<phase>` for each phase in the table above, `card-<family>` for the focused card's orb,
  and `presence-<name>` and `answer-<name>` for the lists below it. A pose that moves has
  `poses/<pose>.motion.svg` beside it, the instant its motion is drawn at: the active family's dots
  mid-picture, the peek half above the top edge, the waking orb not yet at full brightness, the nod
  at the bottom of its dip, the shake at its widest, and the badge half gone as an answer
  elsewhere closes it.
- **In parts, round, in the tokens.** Each source is one whole orb, its pivot at its centre,
  holding its dots (round dots, and round-capped short lines where the picture streaks them) and,
  in a plate state, its mark, as separate groups, and the whole orb only moves, turns or scales by
  one factor. Every colour is a token set in a style (`--color-orb-alive`, `--color-orb-rest` and
  the alert tones), or a mix of two where a dot blends them, so one file is the pose in light, in
  dark and in every scheme.
- **Sizes.** A pose is drawn at the room its surface gives the orb
  ([`surfaces.md`](surfaces.md#the-room-budget)): the buddy's one orb at 48 in every state, docked
  or floating ([#5216]), 96 where one piece of work owns the phone's screen, and the small orbs of
  the work in sight, the tray and the phone's rows. Below 30 px the dots are drawn larger, and a
  plate state gives them up to its mark: a dot at the upper left in the orb's ink and the mark
  large, solid in its tone, so the mark alone tells a still waiting orb from an active one, in
  colour and as a template, where motion cannot.
- **The tray's and the dock's stills** are `poses/tray-<still>-<size>.svg` at 16, 18 and 22 px,
  each its family's still filling the square, and beside each a `-template.svg`: black on clear,
  for the macOS menu bar, which recolours it. The rasters the tray and the dock load are their
  task's export ([#3896]): the macOS menu bar takes the templates, the Linux tray the same template
  drawing painted in its appearance's own ink (`--color-on-surface`), and the dock the colour
  drawing at 22 px. Against a plain bar the one ink holds 3:1 for every still
  (`desktop/test/buddyStillsContrast.test.js`), which the colour drawing's tones cannot promise.
- **The marks:** the agent orb's five, the check, "!", "?", "×" and the broken link. The buddy adds
  none.
- **What the drawing settles:** presence and the answers are drawn on the working pose, the one an
  alert interrupts and an answer goes back to; dozing dims it to 60%, sleeping dims the idle orb to
  40%, and offline is the agent orb's own dimmed orb. Every amplitude is the default dials'.
- **On the desktop** the buddy's window sets the orb on a disc of its own, the surface with a
  hairline and a soft shadow, so it reads over any wallpaper or window (round 6's desktop strip).
  The disc is the orb's own seat, so it rises, sways and swells with the whole orb. The sources
  and the stills are the orb alone, as every other surface shows it.

The contact sheets show every pose at 18, 24, 48 and 96 px, light above dark, each with its words
beside it and its motion frame at 48 px, and every still at its size in colour and as a template,
with the 18 px ones also at four times: [the four families](poses/sheets/families.svg),
[the focused card's orb](poses/sheets/card.svg), [presence](poses/sheets/presence.svg),
[the answers](poses/sheets/answers.svg) and [the tray and the dock](poses/sheets/tray.svg). They are
drawn in the default scheme, for the maintainer to look at whenever they like; they are not a gate.

### The side-by-side check

Made on 2026-10-22 on the written design and the drawn poses ([#4570]), from public material only,
by the rule in [the constraints](#constraints): what the buddy adds and the whole orb, against other
products' status indicators and assistant orbs. The dots and their states were the agent orb's
licensed base and were not judged on their own. It is the record of the first orb of dots: the
rows below describe that orb, with its badge on the upper edge and its wash. The round-6 halftone,
its states, its plate and the disc it sits on on the desktop were checked the same way on
2026-11-17, on the round-6 visuals, and each has a row in the record's halftone part
([`decisions/originality.md`](decisions/originality.md#ships-with-the-halftone-orb)). Each aspect is a row of the originality record
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
each source to its parts, its tokens and its round outline, and every mark to the agent orb's
five, so no buddy pose or still draws a mark of its own. At 18 px it tells the four families
apart by their mark, or, where two draw the same mark or none, by their dots: at least a quarter
more ink or 5% of the orb's size in how far their middles reach from its centre, how far those
reaches spread or where their middle sits, in colour and as templates, with waiting's mark at full
strength; and it holds the contact sheets to every pose at its sizes in both modes, with its words.
`web/src/buddyRig/poseSources.test.ts` holds the files to the table and the table to the phase
contract. After a change to the table, redraw them from `web/`:

```bash
PC_WRITE_BUDDY_POSES=1 npx vitest run src/buddyRig/poseSources.test.ts
```

## The component

`web/src/ui/BuddyOrb.tsx` draws the orb in the app ([#3891]), on the in-place rig
[`decisions/rig.md`](decisions/rig.md) chose: one canvas, painted with the agent orb's halftone
(`web/src/ui/buddy/buddyHalftone.ts`), in the state the table above gives each phase, with the
state's exact line beside it so the art never carries the state alone. The buddy's window, the
playground, the focused card and the phone's views all draw this one component, and the window's
orb sits on its disc.

- **Changes of state.** A change morphs every dot from one picture into the next on the smooth
  spring, as the agent orb's does (`web/src/ui/orb/useSceneMorph.ts`), stepped on the orb's capped
  clock, and done swirls and bumps once on arrival. The whole-orb moves of presence and the
  answers are the pose table's (`web/src/buddyRig/poseSet.ts`), so the renderer writes no spring
  and no length of its own.
- **Colour.** The dots are `--color-orb-alive` or `--color-orb-rest`, and the plate's mark the
  alert's tone; a scheme change repaints the orb as it lands. Under more contrast the dots take the
  words' ink, and under forced colours the system's, as the agent orb's do.
- **Cost.** It paints only while its dots or its morph move, at most 30 frames a second, and
  nothing at all while a state holds still, while the page is hidden or while the orb is out of
  view.
- **Reduced motion.** Every state is its still and a change jumps, stated on `data-buddy-orb`;
  switching it keeps the same orb, state and line.

`web/src/ui/BuddyOrb.test.tsx`, `BuddyOrb.frames.test.tsx` and
`buddy/theBuddyDrawsTheAgentOrbsHalftoneState.test.ts` hold it to this.

## Later work

### Play and drop poses

Later work: play ([#3894]), and the drop poses with drop and quick ask ([#3895]).

The orb has no eyes, so where play speaks of its gaze, it means the orb's lean: the side it leans
to.

- **Play:** greeting (a quick swell to full size, one wave, a blink), follow (the lean at its limit
  in eight directions), poked (the dots scatter a little and regather), annoyed (a shiver through
  the dots, and the orb grows a little), dizzy, dragged (dangling behind the pointer), glance (a
  lean toward the app's window, or toward the control under the pointer), blink (the dots dim for
  an instant), look around (the lean drifting once around the rim).
- **Drop:** file over (the dots open wide, ready to catch) and swallowing, where the orb becomes a
  container for the dropped file and comes back round, with one more gesture, **wrap** (holding a
  dropped file: the dots close around it and hold it up).

Settled for their return on 2026-10-14: there is no dizzy, and the drop is a catch, the pose
"holding", in place of swallowing ([#4570]). For the orb, the catch is the wrap: the opened dots
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
[#6117]: https://github.com/PersonalClaw/PersonalClaw/issues/6117
