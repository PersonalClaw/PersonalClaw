# Buddy surfaces

One orb, one state contract, every surface. Each surface has a different amount of room, so
each one is designed for what it must show and what it drops, rather than being a smaller copy of
another. Sizes are in points and are starting values for the prototype to prove in motion.

## The room budget

These sizes are starting values. Where one conflicts with [compact layouts](#compact-layouts)
(hit areas of at least 44 px, no clipping at 200% text zoom), compact layouts wins ([#3902]).

| Surface | Size | Shows | Drops |
|---|---|---|---|
| **Top-edge peek** | the orb just below the top edge, about 28 tall | the orb's pose and its badge | words, counts |
| **Top-edge compact** | 28 tall, up to 360 wide | the focused work's orb (22), its one line, `+N` for other live work | steps, controls |
| **Top-edge expanded** | 420 wide, up to 360 tall | the focused work's orb (48), its state, its run as one step flow, the alert's brief and its answer controls, then every other live piece of work as an orb (18) and one line | history, settings |
| **Floating orb** | the orb at 56, anywhere on screen | the orb; on an alert, a bubble with one line and the alert badge; on click, the expanded card beside it | steps and other work until clicked |
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
readable while it is small. The compact island shows only a count of them.

Small orbs are told apart by their state (the orb's density and its badge) and by a small glyph for
their kind (a chat, a loop, a workflow run, a helper), with the work's name beside them. They are
not given a colour each: coral stays the one colour that means "an agent is alive", and the alert
colours keep their meanings.

When the focused work finishes, its orb plays the done beat and the space its step window used goes
to the work in sight, so the owner sees at once what is still running.

When the room changes (compact to expanded, the phone's two views), the focused orb is the shared
element: it moves and scales into its new place while the rest enter around it, so the owner never
loses sight of it.

## The expanded card, top to bottom

1. **Header:** the focused work's orb at 48, its name, and its state in words.
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

## Compact layouts

Every buddy surface the web app draws renders without clipping at 320, 390, 412, 768, 1024 and
1440 px and at 200% text zoom, and at the compact widths (320, 390 and 412 px) every hit area is at
least 44 px ([#3902]).

- **The expanded card** stacks the same information at 320, 390 and 412 px, with nothing clipped
  and nothing lost on a timer ([#3893]).
- **The phone companion** gives each of its two views its own composition at 320, 390 and 412 px,
  rather than a shrunk desktop layout ([#3898]).

## Docking

**The notched layout changes before the window ships.** Settled on 2026-10-14: on a notched display
the buddy docks as the same pill a screen without a notch has (the orb, the one line and `+N`),
hung just below the notch and centred on it, with no orbs on both sides of the camera and no group
of orbs in a wing ([#3831]). The originality record ([#4570]) rewrites the first bullet below when
it lands.

- **macOS with a notch:** docked, the island continues the notch's shape downward from the top
  edge, centred on it. In compact, the focused orb sits in the left wing beside the camera and the
  work in sight sits in the right wing as a small cluster of up to four orbs (or `+N`); the one
  line slides out below the notch when the pointer rests on it, and for `finishedHold` after a
  change worth telling (done, an alert). Expanded, the island grows down and out from the notch into
  the card.
- **Screens without a notch:** a small pill hangs from the top edge at the centre, holding the orb,
  the one line and `+N`; expanded, it grows into the same card.
- **Which display:** docked, it lives on the display with the menu bar. Floating, it stays on the
  display it was dropped on and comes back there.
- **Drag out:** dragging the orb more than `undockDistance` from the top edge undocks it.
  Dropping it within `dockZone` of the top centre docks it again. Neither distance has a value yet:
  the playground tunes both and records them here ([#3892]).
- Docked or folded, it covers no menu-bar item and no window control ([#3831]).

## How a state change travels

A state change moves through every surface at once, each in its own room: the orb and line change
on the island or the floating orb, the tray icon swaps its still orb, the dock icon changes
only while something needs the owner, and a notification is sent only for an alert (and only when
the interruption rules allow it). The phone companion follows the same change.

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
[#3892]: https://github.com/PersonalClaw/PersonalClaw/issues/3892
[#3893]: https://github.com/PersonalClaw/PersonalClaw/issues/3893
[#3896]: https://github.com/PersonalClaw/PersonalClaw/issues/3896
[#3897]: https://github.com/PersonalClaw/PersonalClaw/issues/3897
[#3898]: https://github.com/PersonalClaw/PersonalClaw/issues/3898
[#3899]: https://github.com/PersonalClaw/PersonalClaw/issues/3899
[#3902]: https://github.com/PersonalClaw/PersonalClaw/issues/3902
[#4570]: https://github.com/PersonalClaw/PersonalClaw/issues/4570
[PersonalClaw/PersonalClawApps#164]: https://github.com/PersonalClaw/PersonalClawApps/issues/164
