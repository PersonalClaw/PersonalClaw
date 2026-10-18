# Buddy state machine

The buddy is layers with a fixed priority, so nothing it shows can hide what the agents are doing
and nothing it does can act for the owner. v0.3.0 ships two of them, truth and presence. The third,
play, is later work ([Play rules](#play-rules)).

| Layer | Decides | States | Rule |
|---|---|---|---|
| **Truth** | what the orb and its one line say | the shared phases of the work in focus (queued, thinking, searching, tool, planning, delegating, streaming, listening, approval, input, done, error) and three system states (idle, restarting, offline), each shown through its visual family with its exact line | Always recoverable: a truth change shows within one `beat`, and a higher-priority truth always replaces a cosmetic state at once |
| **Presence** | how much room the orb has | hidden, peek, compact, expanded, floating, sleeping, plus hidden-by-the-owner | Changes the room, never the words |

**Priority, highest first:** listening, then the shown alert (approval, question, error), then the
truth phase of the work in focus, then idle and sleep.

## The four families

Every truth state shows through one of four visual families, mapped by the phase contract
([`signals.md`](signals.md#1-one-phase-contract)): **quiet** (done, idle), **active** (queued,
thinking, searching, tool, planning, delegating, streaming, listening, restarting), **waiting**
(approval, input) and **blocked** (error, offline). The family's look carries urgency, and the line
beside it always names the exact phase, so colour and motion never carry the phase alone.

The v0.3.0 machine handles nine states, each with a test of what it shows: quiet, active, waiting,
blocked, hidden, focused, interrupted, failed and recovered ([#3890]).

## The anatomy of a state

Every state the orb shows has three parts: an **entrance** (a short move into it), a **hold**
(a still pose or a calm loop for as long as the state lasts) and an **exit** (a short move back
toward the neutral pose). The neutral pose is the hub every state leaves to and enters from:
changing state plays the current state's exit, then the next state's entrance. A state may have a
direct entrance from a particular state when going through the hub would look wrong (for example
error straight from working). An interrupting alert or truth change cuts the hold at once and plays
a short exit, so the whole change stays within one `beat`.

## Inputs

The state machine is a pure function of its current state and one input at a time. Every input
comes from exactly one source.

Truth inputs are **levels, not one-shot events**: what the work is doing now, and what is waiting
now, re-read on every doorbell. A change that arrives while the orb is busy is never lost; the
machine shows the current level the moment it can, so a missed event can never leave a stale pose.

| Source | Inputs |
|---|---|
| The phase contract ([#3833]), per piece of live work | started, phase changed (with its one line), finished (done, error, stopped, declined) |
| The "waiting on you" list ([#3888]) | alert raised (kind, the work it belongs to, brief, answer route, consequence and risk, age, whether it is still current, may interrupt now), alert resolved (answered here, answered elsewhere, expired, withdrawn) |
| The connection | lost, back, restarting (the gateway announced a restart) |
| Push-to-talk (every capture source) | listening started, listening ended |
| The desktop shell | pointer entered the top-edge zone, pointer on the orb, pointer left, click, drag start/move/end, owner idle (system idle time passes `absence`), owner back, display changed, a full-screen app in front |
| The owner's controls | hide for an hour, hide until tomorrow, show, settings changed (Buddy status on or off among them), keyboard focus shortcut, ⌘Enter, Enter, Esc, Tab |
| Timers (only while visible) | hoverExpand, collapse and its countdown, finishedHold, peekLeave, sleep, offlineGrace, alertGap |

Keys are written for macOS: elsewhere ⌘ is Ctrl.

## Which work is in focus

1. While an alert shows, its work is in focus.
2. Otherwise the work already in focus keeps it for as long as it is live. Focus never jumps between
   two pieces of running work.
3. When the focused work ends, focus moves to the live work that started earliest.
4. Every other live piece of work is "in sight": a small orb and one line each, in start order,
   with `+N` past what the surface can hold.

## The alert queue

- Alerts are shown one at a time, in the waiting list's one order, consequence then age, so the
  buddy agrees with every other surface ([#3888]). The count of the others waiting is always
  visible.
- A shown alert stays until it is answered here, answered elsewhere, expires or is withdrawn by its
  work. No timer ever dismisses it.
- Answering takes an explicit click on Allow, Deny, an option or Send, or ⌘Enter while the buddy
  window has keyboard focus and the request is the focused element. Esc closes without deciding,
  and Deny has no key of its own. A broad-reach approval (a delete, an overwrite outside the
  workspace, a send to other people, or spending) answers only after its confirmation. Nothing else
  answers: not a hover, not a timer, not focus arriving, not the alert arriving, and not a key on a
  stale request ([#3832]).
- After an answer, the answer beat plays (a nod for yes, a small shake for no), and the next alert
  enters after `alertGap`.
- Listening holds the shown alert without answering it, and disables its actions until listening
  ends.
- While offline, the shown alert stays and its actions are disabled with a line saying nothing can
  be sent. When the connection is back, it is re-read: still pending, its actions return; gone, it
  leaves as answered elsewhere.
- An alert that expires before an answer leaves with `<work>: the request expired`
  ([`copy.md`](copy.md#one-line-per-state)).

## Presence rules

- **Off** until the owner turns on Buddy status ([`settings.md`](settings.md)). Turning it on or off
  applies at once to the window, the tray's and the dock's still orbs and the alert notifications
  ([#3901]).
- **Hidden** when nothing is live and nothing waits, when the owner has been idle for `absence`
  (docked only), or when the owner hid it. Hidden means no timers, no animation frames and no
  pointer sampling in the buddy's renderer, and no polling, media or audio work; it wakes only on
  the doorbell.
- **Peek** when the pointer rests on the top-edge zone while docked.
- **Compact** while work runs and the owner is active: the focused work's orb, its one line and
  the in-sight count.
- **Expanded** after the pointer rests on it for `hoverExpand`, on a click, or when an alert
  arrives. It goes back to compact after `collapse` without interaction, with a countdown line for
  the last `collapseCountdown`, or on Esc when no alert shows. An alert never collapses.
- **Finished** plays the done beat on the focused work and holds its line for `finishedHold`, then
  folds away.
- **Floating** after a drag carries it out of the dock zone. It stays where it was put, on that
  display. It never hides for absence: it dozes (still and dimmed) and wakes when something
  happens. After `sleep` with nothing live it sleeps the same way. In v0.3.0 a drag carries it
  without the dangle, which is play ([#3831]).
- **Returning:** when the owner comes back after being away and anything finished, failed or
  started meanwhile, the line shows a return summary ("While you were away: 2 done, 1 needs you")
  for `finishedHold`.
- **Hidden by the owner** (an hour, or until tomorrow, from the context menu or the tray) shows
  nothing at all except the listening orb. The tray, the dock and notifications keep telling.
- **Quiet hours:** an alert arrives as a quiet peek (the orb and its badge, no sound, no expansion)
  and expands only when the owner rests the pointer on it or clicks. Ambient updates do nothing.
- **Over a full-screen app:** only alerts appear, as a quiet peek.
- **Offline** shows only after `offlineGrace`, so a short reconnect never flashes. A restart the
  gateway announced shows as restarting, not offline. When the connection is back, the line says
  `Connected again`.
- **No timed loss of content.** A fold on a timer, such as `finishedHold` or `collapse`, pauses
  while the pointer or the keyboard focus is on the buddy, or what it folds stays reachable in
  Running Now, Recent or the return summary ([#3902]).

## The badge

The badge is the state's caption, folded ([`character.md`](character.md#constraints)). Resting the
pointer on it unfolds the state's words, which stay at least `badgeDwell`, and starts nothing else.

## Invariants, each a test

1. No input except an explicit click on an answer control, or ⌘Enter while the buddy window and the
   request have focus, sends an answer. Esc never sends one, and a broad-reach approval sends only
   after its confirmation. Proven by driving every other input in every situation and asserting no
   answer request was made.
2. No play input ever makes a network request.
3. While an alert shows, no play reaction starts, and a running one ends within one `beat` of the
   alert arriving.
4. A truth change ends any running reaction within one `beat`.
5. Listening wins over every other state, and when it ends no queued alert has been lost or
   answered.
6. Exactly one alert is shown at a time, and the shown alert never leaves except by being answered,
   answered elsewhere, expiring or being withdrawn.
7. While hidden, the buddy's renderer schedules no timer and no animation frame, samples no pointer
   position, and does no polling, media or audio work.
8. Focus never moves between two pieces of live work except when the focused one ends or an alert
   arrives.
9. Presence never changes the words: the same truth produces the same line on every surface that
   has room for it.
10. Every situation has a reduced-motion rendering, and under reduced motion no spatial animation
    runs.
11. When the connection is lost, the buddy never shows work as running past `offlineGrace`.
12. The badge and the wash show the truth within one frame of a truth change, in every situation,
    play reactions included: no reaction hides them.
13. An error outranks done: they never mask each other, and whichever is higher in priority shows.
14. A truth change that arrives during any entrance, hold or exit is shown within one `beat`; none
    is dropped.
15. Every shipping cell of the interruption matrix
    ([`interruption-matrix.json`](interruption-matrix.json)), every cell outside its `later` list,
    has a test. The later cells' tests come with their work ([#3894], [#3895]).

v0.3.0 has no play layer, so invariants 2 and 3 hold with nothing to drive, and invariant 3's
broken-reducer control returns with play ([#3894]).

## Timing names

Every duration is a named token, never a literal. The names are the contract. Their values, the
part of the buddy that reads each, and each one's reduced-motion answer live with the app's motion
tokens, in [`docs/design/motion.md` §9](../motion.md#9-the-desktop-buddys-timings).

| Token | Meaning |
|---|---|
| `beat` | the longest a reaction may take to yield to the truth |
| `hoverExpand` | rest on the orb this long to expand |
| `peekLeave` | after the pointer leaves the top-edge zone, the peek folds after this long |
| `collapse`, `collapseCountdown` | expanded folds to compact after this long without interaction; the countdown line shows for the last part |
| `finishedHold` | how long the done line and the return summary stay |
| `absence` | system idle time after which the owner counts as away |
| `sleep` | floating with nothing live for this long, it sleeps |
| `offlineGrace` | a lost connection shows as offline only after this long |
| `alertGap` | the pause between one alert leaving and the next entering |
| `showDelay`, `minHold` | a state shows only after `showDelay` and stays at least `minHold`, so fast work does not flash |
| `badgeDwell` | the shortest time the badge's unfolded words stay |

The full matrix of every situation crossed with every event is in
[`interruption-matrix.md`](interruption-matrix.md).

## Later work

Play, the drop and quick ask are designed here and return only after the status MVP's sign-off
([`README.md`](README.md)).

### Play rules

Later work: play ([#3894]), and the file rule with drop and quick ask ([#3895]).

Play is the third layer, below the truth and above idle and sleep: listening, then the shown alert,
then the truth, then play, then idle and sleep.

| Layer | Decides | States | Rule |
|---|---|---|---|
| **Play** | cosmetic reactions | follow (gaze), greeting, poked, annoyed, dizzy, glance, dragged (dangle), swallowing a dropped file, fidgets (blink, look around) | Never calls the API, never starts during an alert or while listening, always settles back to the truth |

- Play runs only when the orb is visible, no alert shows, the owner is not listening, play is
  switched on, and reduced motion is off. Under reduced motion no reaction plays, not even as an
  instant pose, and the gaze does not follow ([#3894]).
- **The greeting:** the first peek of a session plays the greeting. It is play: it never blocks an
  input, and it yields like any other reaction.
- **Follow:** the orb's lean, the side its dots crowd to, turns toward the pointer on the `gaze`
  spring: a short lag, one small overshoot, then still. The gaze moves in proportion to the
  pointer's distance with no dead zone, and stops at the edge of `followRange`; after `followIdle`
  without pointer movement it returns to centre. The pointer is sampled only while visible and the
  owner is active.
- **Hits:** a click on the orb while it is expanded or floating is a hit. One hit is poked (a
  flinch and a blink), and the reaction lasts `hitWindow`. A second hit while that reaction still
  plays is annoyed; a third while annoyed still plays is dizzy for `dizzyHold`, then it settles.
  The reaction chain is the counter: no timer counts hits. After dizzy, hits only blink for
  `playCooldown`. A click on a control or on the badge is never a hit.
- **Pointer away:** when the pointer leaves the orb's area, the gaze returns to centre over
  `pointerAway`, and the orb settles; a truth change still enters at once from that pose.
- **Gaze authority:** while a truth pose or a big reaction (annoyed, dizzy, swallowing, an alert)
  holds the lean, the gaze does not follow; control passes back to the gaze by fading over
  `gazeHandback`, never in a jump.
- **Drag:** the orb dangles behind the pointer on a spring and settles on release.
- **File:** a file held over it opens the drop target (the orb's dots spread wide); leaving closes
  it; a drop plays the swallow and opens the choice. The choice is the only play outcome that shows
  controls, and every choice still needs a click.
- **Glance:** when a review opens in the app, it glances toward the app's window for one beat;
  inside the open card, the orb leans toward the control under the pointer (Allow, Deny, an option,
  Open), so the orb reads what the owner is about to do. Cosmetic: a glance never changes what the
  control does.
- **Fidgets:** a blink at a random interval inside `blinkRange` and an occasional look around, only
  while visible and the owner is active, never during an alert.
- A truth change, an alert, listening or a presence change ends any reaction within one `beat`.
- Every exit and every blend between states can be cut by a truth change; nothing the owner or the
  agents do is ever swallowed by an animation that cannot be interrupted.
- Clearing a state never waits for its entrance: when an error is resolved or work moves on while
  the entrance still plays, the exit starts within one `beat`.
- No play input answers anything, a drop included (invariant 1).

Settled for their return on 2026-10-14: the hit chain ends at annoyed, with no dizzy, and after
annoyed further hits only blink for `playCooldown`; and the drop is a catch that holds the file up
while the choice opens, never a swallow ([#4570]), which for the orb means its spread dots close
around the file. [#3894] and [#3895] rewrite these rules when they resume.

### Play timing names

Later work: play ([#3894]).

| Token | Meaning |
|---|---|
| `gaze` (a spring), `followRange`, `followIdle`, `pointerAway`, `gazeHandback` | gaze follow |
| `hitWindow`, `dizzyHold`, `playCooldown` | hits (`hitWindow` is also how long a poke plays) |
| `blinkRange` | the random interval between blinks |

### Later inputs

Later work: play ([#3894]) and drop and quick ask ([#3895]).

The desktop shell's file drag enter, leave and drop join the inputs with the drop, and the play
timers join the timers with play.

[#3831]: https://github.com/PersonalClaw/PersonalClaw/issues/3831
[#3832]: https://github.com/PersonalClaw/PersonalClaw/issues/3832
[#3833]: https://github.com/PersonalClaw/PersonalClaw/issues/3833
[#3888]: https://github.com/PersonalClaw/PersonalClaw/issues/3888
[#3890]: https://github.com/PersonalClaw/PersonalClaw/issues/3890
[#3894]: https://github.com/PersonalClaw/PersonalClaw/issues/3894
[#3895]: https://github.com/PersonalClaw/PersonalClaw/issues/3895
[#3901]: https://github.com/PersonalClaw/PersonalClaw/issues/3901
[#3902]: https://github.com/PersonalClaw/PersonalClaw/issues/3902
[#4570]: https://github.com/PersonalClaw/PersonalClaw/issues/4570
