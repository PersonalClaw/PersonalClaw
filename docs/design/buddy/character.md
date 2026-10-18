# Buddy character brief

The buddy is PersonalClaw's own character, built from the ClawMark and the lobster identity. Its
whole job is to make the agents' state readable at a glance, at any size, while staying good
company ([#3830]).

## Constraints

- **Built from the ClawMark:** the mark is an open claw, a short rounded blade and a long curved
  hook with a gap between them. The character keeps that silhouette recognisable at 18 px.
- **Emotion through the eyes and one claw gesture.** Everything a state family means must survive
  with only the eyes and the claw.
- **Exact state text stays beside it.** The family's look carries urgency, and words always name the
  exact phase: the badge unfolds into them, and the focused status card shows them. Colour and
  motion never carry the phase alone.
- **Warm-neutral body at rest; coral only while an agent is alive.** The body colour is a new
  warm-neutral token for light and dark, `--color-buddy-body`
  ([`web/DESIGN.md` §2](../../../web/DESIGN.md#neutral)). Coral is the active scheme's primary, so
  in a teal scheme the alive colour is teal: the character never hard-codes coral.
- **Semantic colour for alerts only, as a badge or a wash, never as the whole body:** warn for an
  approval, info for a question, danger for an error, ok for done. The wash is a soft gradient up
  from the lower edge of the body; the alive colour uses the same wash while an agent works.
- **The badge is the caption, folded.** A small badge sits on the body's upper edge: a calm moving
  dot pattern while working, a mark for each alert kind, a check for done. Resting the pointer on it
  unfolds it into the state's words, and it folds back when the pointer leaves. On the tray and the
  dock, the badge is what carries the state.
- **The body can change shape and turn.** It may squash and stretch, lean in, or turn its head away
  and back, as long as it returns to its own silhouette.
- **Reads at 18, 24, 48 and 96 px**, in light and dark, in every colour scheme, with a still frame
  for every pose under reduced motion.
- **Original:** it must pass a side-by-side check against well-known assistant and app mascots, and
  it shares no proportion, expression set, colour scheme or sound with any of them.
- **Drawn in layers a renderer can move:** the body, the blade, the hook, each eye (white, pupil,
  lid), a mouth line if any, the badge, and the glow, each a separate shape with a named pivot.

## Three directions to explore

Each direction gets the same sheet at the four sizes, so they can be compared side by side.

1. **The living mark.** The ClawMark itself is the creature. Two eyes sit in the short blade, which
   becomes its head; the long hook is its one gesturing limb: it curls to point, waves, taps while
   working, and wraps around a dropped file. The gap of the claw is its mouth: open to greet and
   listen, closed in focus, snapping softly as it writes. The closest tie to the brand, and the
   strongest silhouette at 18 px.
2. **The small claw creature.** A rounded warm body with two small claws, its eyes large and set
   apart, and the ClawMark as the crest on its head that glows when an agent is alive. More
   character and more room for body language; harder to keep readable at 18 px.
3. **The face in the island.** No body at all: the island or the floating pill is the face, with two
   eyes and a claw-shaped mark beneath that acts as mouth and hand. The quietest of the three, almost
   an interface element, and the easiest to fit into the top edge.

## The choice

Not made yet. At gate one the maintainer chooses one direction, or a combination, from the sheets
at real sizes ([#3830]). The choice, the three sheets, and each rejected direction with the reason
it was cut are recorded here, with the side-by-side originality check beside them. The warm-neutral
body's values for light and dark come with the chosen direction.

## Poses every direction must cover

The v0.3.0 set, which the status MVP needs:

- **The four families:** quiet, active, waiting and blocked, each a pose that reads at 18 px. Every
  truth phase shows through its family, with its words beside it: queued, thinking, searching, tool
  (working), planning, delegating, streaming (writing), listening, approval, question, error, done,
  idle, restarting, offline.
- **The focused status card's face,** at the card's size, for each family.
- **Presence:** peek (eyes over the edge), compact, expanded (large face), floating, dozing,
  sleeping, waking.
- **Answers:** the yes nod, the no shake, answered elsewhere.
- **Still faces for the tray and the dock:** idle, working, needs you, error, listening, offline, at
  16, 18 and 22 px, plus macOS template versions for the menu bar. Each is a variant of its family's
  pose: idle is quiet; working and listening are active; needs you is waiting; error and offline are
  blocked.

## The gesture vocabulary

One claw, a small set of moves, each with one meaning: **point** (toward what changed), **wave**
(greeting and goodbye), **tap** (working rhythm), **raise** (a question), **hold up** (stop: an
approval is waiting), **droop** (an error), **flourish** (done). Nothing else; a move never means
two things.

## Later work

### Play and drop poses

Later work: play ([#3894]), and the drop poses with drop and quick ask ([#3895]).

- **Play:** greeting, follow (eyes and head at their limits in eight directions), poked, annoyed,
  dizzy, dragged (dangling), glance, blink, look around.
- **Drop:** file over (claw open) and swallowing, where the body becomes a container for the dropped
  file and returns to its own silhouette, with one more gesture, **wrap** (holding a dropped file).

Settled for their return on 2026-10-14: there is no dizzy, and the drop is the claw's catch, the
pose "holding", in place of swallowing ([#4570]). The portable sprite sheet's frames are an export
of the chosen rig ([`rig.md`](rig.md#the-portable-sprite-sheet)).

[#3830]: https://github.com/PersonalClaw/PersonalClaw/issues/3830
[#3894]: https://github.com/PersonalClaw/PersonalClaw/issues/3894
[#3895]: https://github.com/PersonalClaw/PersonalClaw/issues/3895
[#4570]: https://github.com/PersonalClaw/PersonalClaw/issues/4570
