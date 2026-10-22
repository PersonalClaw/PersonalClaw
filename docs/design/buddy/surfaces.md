# Buddy surfaces

One orb, one state contract, every surface. Each surface has a different amount of room, so
each one is designed for what it must show and what it drops, rather than being a smaller copy of
another. Sizes are in points and are starting values for the prototype to prove in motion.

## The room budget

These sizes are starting values. Where one conflicts with [compact layouts](#compact-layouts)
(hit areas of at least 44 px, no clipping at 200% text zoom), compact layouts wins ([#3902]).

The buddy is one orb of one size that stays where the owner put it, and everything it says is on a
card beside it ([#5216], the owner's ruling after playing the playground). The rooms the machine
names (peek, compact, expanded, floating) say how much the card shows, never how big the orb is or
where it sits:

- **Peek, sleeping, and an orb with nothing to say:** the orb alone.
- **Compact, and an alert's bubble away from the top edge:** the line card.
- **Expanded, and a click on an orb away from the top edge:** the card.

The card opens on the side of the orb that has room on its display, chosen where the orb settles
and never when the card opens: to the orb's right if the card fits there, else to its left, opening
down from the orb's top if it fits, else up from its bottom; and if neither side has the width,
below the orb or above it (where there is more room), centred on it and kept on the display. It
never covers the orb, and past its area's height (560) it scrolls inside. The buddy's window is
the orb's 56 pt box and that area, one fixed size, and the shell pushes the shape to the page
(`desktop/buddyPlacement.js`, `layoutAt`); the playground and a browser tab run the same rule
(`web/src/lib/buddy/cardPlacement.ts`). Opening, growing, shrinking or closing the card never moves
the orb or changes its size: the card is laid out apart from the orb, anchored to its edge
(`web/src/pages/buddy/BuddyPresence.tsx`).

| Surface | Size | Shows | Drops |
|---|---|---|---|
| **The orb** | 48, in a 56 by 56 target, where the owner put it (until the first move, centred just below the menu bar or the notch) | its state in its dots, its badge and its wash, the same size and place in every state | words, counts: they are on its card |
| **The line card** | one line, up to 360 wide, beside the orb | while work runs, the focused work's one line and `+N` for other live work; on an alert away from the top edge, the alert's one line | steps, controls |
| **The card** | 420 wide, as tall as its content up to 560, beside the orb; less where the display has less, scrolling inside past that | the focused work's name and its state, its run as one step flow, the alert's brief and its answer controls, then every other live piece of work as an orb (20) and one line | history, settings |
| **Tray or menu-bar icon** | 18 (template image on macOS) | a still orb per state: idle, working, needs you, error, listening, offline; the exact state in words in its tooltip and as the menu's first line | animation, a count standing alone |
| **Tray menu** | a menu | everything waiting on the owner, in the list's order, then live work, each one line, plus Show or Hide the buddy, Hide for an hour, Hide until tomorrow, Open PersonalClaw | the step flow |
| **Dock or app icon** | the app icon | the orb's still for the state while something needs the owner, instead of a red count | words |
| **OS notification** | the system's banner | the state's still orb as the icon, the one line of what changed, a tap that opens the buddy on that alert | everything else; no answer buttons, because an answer needs its brief |
| **Phone companion** | the phone screen | two views: everything at once (every piece of work as an orb and one line, alerts first), or one piece of work owning the screen (its orb large, its step flow, its alert and answer controls, Open in PersonalClaw) | play reactions |

- **The tray's six still orbs are variants inside the four families:** idle is quiet; working and
  listening are active; needs you is waiting; error and offline are blocked. Listening and offline
  keep their own badge inside their family's pose, and an announced restart shows the working orb
  with the restarting line. A count may sit beside the words, never stand in for them, and the
  stills stay apart in high contrast and with animation off ([#3896]).
- **A notification** carries one exact state change, follows the gateway's delivery posture and
  offers no shortcut that answers. The orb is decorative beside words that say the same thing, and
  every buddy alert notification is silent in v0.3.0. With Buddy status off, a tap opens the app on
  that alert ([#3897]).
- **The phone companion** reads Waiting on You and Running Now, shows each row's exact phase in
  words, and opens the focused work in PersonalClaw ([#3898]).

## Many pieces of work at once

One in focus, the rest in sight. The work in focus (the shown alert's, else the one already in
focus, else the earliest live one) gets the room for its state and its current step. Every other
piece of live work collapses to a small orb and the one line it is working on, so its state stays
readable while it is small. The line card shows only a count of them.

Small orbs are told apart by their state (the orb's density and its badge) and by a small glyph for
their kind (a chat, a loop, a workflow run, a helper), with the work's name beside them. They are
not given a colour each: coral stays the one colour that means "an agent is alive", and the alert
colours keep their meanings.

When the focused work finishes, its orb plays the done beat and the space its step window used goes
to the work in sight, so the owner sees at once what is still running.

The buddy's orb never moves when its card opens or closes, so the owner never loses sight of it.
On the phone, where one piece of work takes the screen, that work's orb is the shared element
between its two views: it moves and scales into its new place while the rest enter around it.

## The expanded card, top to bottom

1. **Header:** the focused work's name and its state in words. The buddy's orb beside the card is
   its orb; the card draws none of its own.
2. **The run as one continuous flow:** a three-line step window. The middle line is the current
   step (plan, a step, a tool, a result) at full ink with its kind's icon, and its label fills with
   the alive colour as the step runs (a slow shimmer when there is no progress to show). Above it,
   dimmed and marked done, is the step before; below it, dimmed, is the next step when it is known.
   When a step finishes, the window scrolls up by one line, and for a moment both the finished and
   the new label show while the finished one shrinks into a small done mark in a trail above the
   window (at most eight marks, then `+N`), so the run's length stays visible without its history.
   Never a scrolling log, never more than three lines. A subagent batch shows as a row of small
   orbs, one per member, each in its own state.
3. **The alert, when one shows:** its brief exactly as the app's approval prompt shows it (what will
   run, where, its source, its reach, what declining does), then its controls: Allow and Deny, the
   app's own words with the app's accessible names ([#3899]), or the question's options and an
   answer field, or Open and Dismiss for an error. `+N waiting` when more are queued.
4. **In sight:** every other piece of live work, orb and one line each.
5. **Footer:** Open in PersonalClaw, and the countdown line while it is about to fold.

So the v0.3.0 card shows the focused work, its exact phase, the current step, the other running
work, the waiting decisions and Open in PersonalClaw, with no drop target and no ask field
([#3893]). Neither Allow nor Deny carries the primary style: the buddy styles them as the chat's
approval card does, so it never argues for an answer ([#3899]).

As built ([#3893]): the card is `web/src/ui/BuddyCard.tsx`, and its step window is the fold in
`web/src/lib/buddy/steps.ts`, which turns the machine's phase inputs into the step before, the
current one and the next, with the trail's count. The brief is the app's approval prompt with the
waiting-list row's own fields (`approvalRowFields`, the phone companion's mapping), its full
arguments in a block that scrolls within the card; a question is the chat's question card, and an
error has Open and Dismiss. The controls only raise the answer, which the presence sends
([#3832]). In sight is the running list's rows, each with its agent orb, its kind's glyph, its one
line, its exact phase, its last update and its route, and the list's own states (reconnecting,
could not be read, interrupted, just finished, many). When the focused work finishes, the step
window goes and the rows take its room. The card says while the pointer or the keyboard focus is
on it, so a fold on a timer waits. The sections enter on the region stagger, instant under reduced
motion. The
next step shows only when the work knows it, so a free chat turn's window has two lines.

## Compact layouts

Every buddy surface the web app draws renders without clipping at 320, 390, 412, 768, 1024 and
1440 px and at 200% text zoom, and at the compact widths (320, 390 and 412 px) every hit area is at
least 44 px ([#3902]).

- **The expanded card** stacks the same information at 320, 390 and 412 px, with nothing clipped
  and nothing lost on a timer ([#3893]).
- **The phone companion** gives each of its two views its own composition at 320, 390 and 412 px,
  rather than a shrunk desktop layout ([#3898]).

## Docking

The orb stays where the owner put it ([#5216]). Only a drag moves it, and the spot is kept per
display across a restart. No alert, no presence change, no absence and no hide moves it; a display
that is gone puts it back at the default. It is the app's own: the orb on its own, never on a black
shape, and never joined to the notch, the menu bar or the screen's edge as one shape
([the originality record](decisions/originality.md), [#4570]).

- **The default, until the first move:** centred just below the menu bar on the display with the
  menu bar, or just below the notch on a notched Mac, with no orbs on both sides of the camera and
  no group of orbs in a wing ([#3831]).
- **At the top edge or away from it:** a drop within `dockZone` of the top centre counts as the top
  edge, and a drag more than `undockDistance` from it leaves it. The starting values are
  `undockDistance` 48 points and `dockZone` 96 points, measured from the top centre of the menu-bar
  display's work area (`desktop/buddyPlacement.js`). Where it sits changes what the machine does
  while the owner is away (at the top edge it steps aside, elsewhere it dozes) and what its card
  shows on an alert, never the orb's size or look.
- **Its card** opens beside it on the side with room ([the room budget](#the-room-budget)), and it
  covers no menu-bar item and no window control ([#3831]).

## How a state change travels

A state change moves through every surface at once, each in its own room: the orb's dots, badge
and wash change where it stands and its card's line follows, the tray icon swaps its still orb, the
dock icon changes only while something needs the owner, and a notification is sent only for an
alert (and only when the interruption rules allow it). The phone companion follows the same change.

## Later work

### Bridges to other desktop companions

Later work: bridges ([PersonalClaw/PersonalClawApps#164]).

A small app, outside core and removable, shows PersonalClaw's live work in desktop island
companions an owner already runs: a pill per piece of live work with its state and one line,
"needs you" while something waits, and a finished beat when the work ends. It reads the two lists
and treats their change events as a doorbell, never as data
([`signals.md`](signals.md#the-doorbell)), and every answer stays in PersonalClaw. It returns only
after the status MVP ships and a third-party surface is still wanted. Then it binds to an exact core
version and phase contract, stays read-only, signs in with a credential scoped to the app, sends
only the text the owner approved, and records the core and app commits validated together.

[#3831]: https://github.com/PersonalClaw/PersonalClaw/issues/3831
[#3832]: https://github.com/PersonalClaw/PersonalClaw/issues/3832
[#3892]: https://github.com/PersonalClaw/PersonalClaw/issues/3892
[#3893]: https://github.com/PersonalClaw/PersonalClaw/issues/3893
[#3896]: https://github.com/PersonalClaw/PersonalClaw/issues/3896
[#3897]: https://github.com/PersonalClaw/PersonalClaw/issues/3897
[#3898]: https://github.com/PersonalClaw/PersonalClaw/issues/3898
[#3899]: https://github.com/PersonalClaw/PersonalClaw/issues/3899
[#3902]: https://github.com/PersonalClaw/PersonalClaw/issues/3902
[#4570]: https://github.com/PersonalClaw/PersonalClaw/issues/4570
[#5216]: https://github.com/PersonalClaw/PersonalClaw/issues/5216
[PersonalClaw/PersonalClawApps#164]: https://github.com/PersonalClaw/PersonalClawApps/issues/164
