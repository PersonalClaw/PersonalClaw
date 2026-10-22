# Buddy motion

Motion explains what changed and then gets out of the way: every transition resolves quickly and
settles into stillness. The buddy uses the app's motion system as it is
([`docs/design/motion.md`](../motion.md)): the four spring presets (`snappy`, `smooth`, `fluid`,
`playful`) for anything that moves in space, the effects curve for opacity and colour, `expr()` for
amplitude and the bounciness dial for overshoot. The orb's dots move as the agent orb's do, in their
own motion for each state ([`agent-orb.md`](../agent-orb.md#phase-to-state)), and everything the
buddy adds moves on the presets. In v0.3.0 it mints no spring of its own. Under reduced motion every
transition becomes an instant change.

## Timing tokens

Every wait and hold the buddy has is a named token. Their values, the part of the buddy that reads
each, and each one's reduced-motion answer are recorded with the app's motion tokens, in
[`docs/design/motion.md` §9](../motion.md#9-the-desktop-buddys-timings), and what each one times is
the state machine's contract ([`state-machine.md`](state-machine.md#timing-names)). The values are
starting points: the playground tunes them, and a tuned value changes there first ([#3892]).

## Transitions

Every row names its trigger, its preset or curve, its length, whether something can cut it, and
what reduced motion does instead. "Cut" means a higher-priority change may end it at once with a
short exit. Every row here ships in v0.3.0. Phase change, alert arrives, error and done are the rows
that move the buddy between its four families.

| Transition | Trigger | Moves | Preset / curve | Length | Cut | Reduced motion |
|---|---|---|---|---|---|---|
| Peek in | pointer on the top-edge zone | the orb slides down from the top edge until its badge clears it | `smooth` | ~200 ms | yes | the orb appears |
| Compact in | work starts while visible | the pill fades in below the edge as the orb enters; nothing grows out of the edge | `smooth`; the line fades on the effects curve | ~250 ms | yes | instant |
| Expand | hover, click or an alert | the card takes the pill's place, opening downward from where it was, and the pill's outline never stretches into it; the focused orb is the shared element and scales to 48; content follows with a short stagger | open on `smooth`, with no overshoot; content on `regionStagger` | ~320 ms | no | instant |
| Collapse | the collapse timer or Esc | the card folds away and the pill comes back in its place | the effects curve, no overshoot | ~220 ms | yes | instant |
| Phase change | the focused work's phase | the current pose exits, the next enters; the badge swaps with a small pop; the step window scrolls one line | exit and entrance on `snappy`; the badge on `snappy`; the scroll on `smooth` | under `beat` | yes | instant pose and badge change |
| Working hold | while a tool or step runs | the orb stirs, its dots moving in the agent orb's state for the phase; the current step's label fills with the alive colour | the agent orb's own motion for the dots; the effects curve for the label | loops while working | — | the active still, static label |
| Alert arrives | an alert | the orb holds for an approval (its dots draw in tight) or raises for a question (it lifts a little), and goes still; the alert badge pops; the wash rises from below | `snappy` for the hold or the raise and the badge; the effects curve for the wash | ~300 ms | only by listening | instant pose, badge and wash |
| Answer: yes | Allow, Send, an option | a nod (the orb dips once), the check badge | `snappy` | ~300 ms | no | the check badge |
| Answer: no | Deny, Dismiss | a small side shake | `snappy`, amplitude by `expr()` | ~300 ms | no | instant |
| Error | work fails | the orb droops, its dots sinking toward the lower edge and settling there as they take the neutral; the danger wash rises | `smooth`, then the effects curve | ~600 ms | only by listening | the error pose |
| Done | the focused work finishes | the badge turns to a check, and the dots flourish once (a swirl around the orb) and come to rest, soft, in the neutral | `playful`, scaled by `expr()` | ~700 ms, then held for `finishedHold` | yes, by an alert or listening | the done pose |
| Listening | push-to-talk starts | the orb swells a little toward the owner and its dots take the agent orb's listening state; the microphone badge pops | `snappy` | ~200 ms | no | instant |
| Dock and undock | a drop in or out of the dock zone | the orb moves into or out of the pill, which fades in or out around it; no shape melts or forms from another | `smooth` | ~300 ms | no | instant |
| Doze and wake | sleep, absence, return | the dots slow to a stop and the orb dims; on wake, it brightens and its dots move again | the effects curve | ~600 ms | yes | instant |
| Badge unfolds | pointer on the badge | the badge widens into the state's words, and folds back | `snappy` open, the effects curve close | ~200 ms | yes | instant |

Two pops are open for gate two ([#3892]). The redesign moves nothing on a high-frequency action and
makes a count change instant
([`web/REDESIGN.md` §3](../../../web/REDESIGN.md#3-decluttering-rules), rule 12), and both pops may
fall under that rule: the badge's pop in the phase change row, which a tool-heavy turn plays again
and again, and the waiting count's pop when an alert queues behind the one shown
([the matrix](interruption-matrix.md#alert-shown-an-approval-a-question-or-an-error)). The owner
settles at gate two whether they become instant; if they do, the phase change row and that matrix
cell change together.

## The dials

- **Expressiveness** scales amplitude through `expr()`: how far the orb lifts, sinks or swells, the
  shake's distance, the size of the done flourish. At its floor the buddy still changes pose for
  every state; it just moves less.
- **Bounciness** scales overshoot through the presets' own damping. At zero nothing overshoots.
- **Reduced motion** (the system setting) turns every row above into its last column: no spatial
  movement, no shimmer, no springs.

## Cost rules

- Frames run only while something moves; a hold with nothing moving draws nothing.
- The working hold's loop is the one sanctioned continuous motion, and it runs only while visible.
- Nothing runs while hidden: no animation frame, no timer, no pointer sampling, and no polling,
  media or audio work.
- The owner sees what it costs. Each cost run in CI writes every mode's measured CPU, memory, frame
  and timer counts against its bar to the job summary, and the release notes carry a recorded run
  on a named machine ([#3902]).

## Later work

### Play and drop transitions

Later work: play ([#3894]), and the file rows with drop and quick ask ([#3895]).

| Transition | Trigger | Moves | Preset / curve | Length | Cut | Reduced motion |
|---|---|---|---|---|---|---|
| Greeting | the first peek of a session | a quick pop to full size, one wave (a ripple out through the dots), a blink | `playful`, scaled by `expr()` | under 1.6 s | yes | nothing plays |
| Follow | the pointer moves | the orb's lean turns toward the pointer, overshoots a little and settles | the `gaze` spring | continuous while the pointer moves | yes | the orb does not follow |
| Poked | one hit | a flinch: the dots scatter a little and regather, a blink, then recovery | `snappy` | `hitWindow` | yes | nothing plays |
| Annoyed | a hit while the poke still plays | a shiver runs through the dots, and the orb grows a little | `snappy` | ~400 ms, held to `hitWindow` | yes | nothing plays |
| Dizzy | a hit while annoyed still plays | the dots wobble out of step, then settle | `playful` | `dizzyHold` | yes | nothing plays |
| Drag | a drag | the orb dangles behind the pointer and settles on release | `dragSpring` | while dragging | — | the orb follows the pointer rigidly |
| File over | a file held over it | the orb's dots spread wide, ready to catch | `snappy` | ~200 ms | yes | the spread pose |
| Swallow | a drop | the orb becomes a container, takes the file in, closes with a happy blink, and comes back round | `playful` | under 1.4 s | no | the swallowed pose, then the choice |

Under reduced motion no play reaction plays, not even as an instant pose ([#3894]). Fidgets, a
blink inside `blinkRange` and an occasional look around, are scheduled only while visible and the
owner is active. The expressiveness dial also scales how big the greeting's wave is.

Settled for their return on 2026-10-14: there is no dizzy, since the hit chain ends at annoyed, and
the drop is a catch (the transition "Catch", the pose "holding") in place of the swallow ([#4570]):
for the orb, its spread dots close around the file and hold it up.

### The gaze spring

Later work: play ([#3894]).

`gaze` is the one spring the buddy adds, for the follow. Its numbers are in
[`docs/design/motion.md` §9](../motion.md#9-the-desktop-buddys-timings). When play lands it goes
into `web/src/design/motion.ts` beside the four presets, and
[`web/DESIGN.md` §5](../../../web/DESIGN.md#motion-component-level-doctrine) records the follow as a
sanctioned personality moment.

[#3892]: https://github.com/PersonalClaw/PersonalClaw/issues/3892
[#3894]: https://github.com/PersonalClaw/PersonalClaw/issues/3894
[#3895]: https://github.com/PersonalClaw/PersonalClaw/issues/3895
[#3902]: https://github.com/PersonalClaw/PersonalClaw/issues/3902
[#4570]: https://github.com/PersonalClaw/PersonalClaw/issues/4570
