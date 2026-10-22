# Buddy test plan

Each test maps to an acceptance criterion of an implementation task.

## Logic

- Every shipping cell of [`interruption-matrix.json`](interruption-matrix.json), which is every cell
  outside its `later` list, has a test that drives the state machine from that situation with that
  event and checks the outcome ([#3890]).
- Each invariant in [`state-machine.md`](state-machine.md#invariants-each-a-test) has its own test,
  with a positive control proving the test can fail.
- Each of the nine shipping states (quiet, active, waiting, blocked, hidden, focused, interrupted,
  failed and recovered) shows its family and its exact text, or nothing while hidden, and a
  higher-priority truth replaces a cosmetic state on the same input.
- The focus rule, the alert queue's order, the return summary and every timing token are tested
  with a fake clock.

## Consent

- Driving every input except the explicit answer controls and ⌘Enter (Ctrl+Enter elsewhere) on a
  focused request, in every situation, sends no answer request.
- ⌘Enter with the buddy window unfocused, with focus on anything but the request, or on a stale
  request sends nothing; Esc on a focused alert sends nothing; and a broad-reach approval sends only
  after its confirmation ([#3832]).
- An answer goes through the owner's own answer route, and the gateway refuses it when the request
  holds no owner credential.

## Pixels

- Every pose of the v0.3.0 set at 18, 24, 48 and 96 px, in light and dark, in three colour schemes,
  and as its reduced-motion still, each with its state line beside it: component snapshots that run
  in CI on Linux baselines.
- Every surface (peek, compact, expanded with each alert kind, floating with a bubble, the tray's
  still orbs, the phone's two views) in light and dark, and in high contrast.
- At 18 px, each family's still is told apart from the other three by the orb's density and its
  badge alone, and so is each tray still as a one-colour template image: the census of the drawn
  poses and its 18 px scan in `tests/rails/test_buddy_design_matrix.py`
  ([`character.md`](character.md#the-drawn-poses)). The contact sheets stay for the maintainer to
  look at any time; they are not a gate.
- Side by side with other products' status indicators and assistant orbs, from public material,
  nothing the buddy adds to the agent orb (its outline, badge, wash, poses, gestures and presence),
  nor the whole orb, shows a drawn form or choreography a viewer would recognise as another
  product's. The dots and their states are the agent orb's licensed base
  ([`agent-orb.md`](../agent-orb.md#the-component)), shared on purpose, so they are not judged on
  their own. The result is recorded beside [the choice](character.md#the-choice), naming none of
  them, and the originality record cites it ([#4570]).
- Copy: no rendered line exceeds its surface's limit with names up to 200 characters.

## Motion

- Every transition in [`motion.md`](motion.md#transitions) runs on its named preset and token; under
  reduced motion none runs (no CSS transition or animation longer than 50 ms, and no spring).
- A truth change shows within one `beat`, measured.

## Cost

- Hidden: over 60 s, the buddy renderer schedules no timer and no animation frame, samples no
  pointer, does no polling, media or audio work, and `app.getAppMetrics()` shows it idle.
- Visible and still: under the rig's at-rest bar. Animating: under its animating bar. Floating and
  asleep: as still.
- Twelve hours of a scripted busy day with a fake clock: memory does not grow and no listener leaks.
- The doorbell coalesces a burst of frames into one read.
- Each cost run writes every mode's numbers against its bar to the CI job summary, and the release
  notes carry a recorded run on a named machine ([#3902]).

## The real window

- A real-Electron test tier that runs in CI: the window is transparent, always on top, shows without
  taking focus, passes clicks through outside the shape, docks on a notched and an un-notched
  display, undocks and redocks, and is excluded from screen capture when that setting is on.

## Accessibility

- The axe sweep and the focus-ring checks run over the buddy route in both themes, axe's target-size
  rule included.
- Every buddy surface renders without clipping at 320, 390, 412, 768, 1024 and 1440 px and at 200%
  text zoom, and its hit areas are at least 44 px at 320, 390 and 412 px ([#3902]).
- A keyboard-only pass: the focus shortcut, Tab order, ⌘Enter and Esc on an alert, and leaving
  returns focus to the previous app.
- Screen reader: the live line announces once per alert and never repeats while it waits; every
  control's name includes what it acts on.
- High contrast: every state stays readable, and none needs animation to be told apart.
- Every state has its exact text, and no timed fold loses content: each pauses, or keeps what it
  folds reachable, with a test per fold.

## Acceptance run

A scripted day in the life on a scratch home with fake model and channel services: work starts,
steps, an approval, a question, a failure, finished work, listening, absence and return, a restart,
quiet hours. Every surface is recorded and compared against the design.

## The playground

`#/buddy/playground` is where the buddy is played before any desktop surface ships ([#3892]). It is
a full-screen route outside the navigation, like `#/companion`, and it ships in release builds:
nothing links to it but its address, so the owner can play it on an installed build. It mounts the
real state machine and the real orb (`web/src/pages/buddy/`) and mocks only their inputs: the work
and its phases, the waiting list, the connection, push-to-talk, the pointer, a drag, idle, the
owner's own switches, and a clock that fires the machine's timers at their lengths. It reads
nothing from the gateway, and an answer the machine asks to send is written in its log and never
sent.

- **Play** (`?tab=play`): the stage, one control per input, the clock and the event log. The stage
  holds the buddy as each surface would (the top-edge island in its peek, compact and expanded
  rooms, or the floating orb with its bubble and card), at 320, 390 or 412 px or the page's width,
  with Waiting on You, Running Now and the focused work beside it. A control the matrix says cannot
  happen now is off and says why. The look controls are the app's own scheme, mode and two motion
  dials.
- **A day** (`?tab=day`): the scripted day (`scriptedDay.ts`) on the playground's clock, played at
  1×, 4× or 16×, paused, or a step at a time. The acceptance run reuses it ([#3902]).
- **Poses** (`?tab=poses`): every pose of the four status families at 18, 24, 48 and 96 px and the
  focused card's orb for each family, each as its still with its words beside it, in the current
  scheme and mode. The renderer's snapshots are taken from it.

Its tests: `PlaygroundPage.test.tsx` enters every situation outside the matrix's later-work list
through the controls and sends every such event from them, and uses every control with nothing
reaching the network; `playground.test.ts` runs the day through the reducer; and
`web/e2e/buddyPlayground.spec.ts` steps the day in the browser, saving the stage after every step in
light and dark, at 320, 390 and 412 px, in high contrast and at 200% text zoom, each with an
overflow check. The three tabs are in `web/e2e/routes.ts`, so the UI quality check covers them.

## Later work

### Play and drop tests

Later work: play ([#3894]) and drop and quick ask ([#3895]).

- Every later cell of the matrix gets its test when its work lands.
- Play inputs make no network request, and a reaction yields to a truth change within one `beat`,
  measured.
- Play's own CPU and timer budget is measured, and a cost test fails above it.
- The play and drop poses get their snapshots at the four sizes, in light and dark.
- The acceptance run gains a drop.

[#3832]: https://github.com/PersonalClaw/PersonalClaw/issues/3832
[#3890]: https://github.com/PersonalClaw/PersonalClaw/issues/3890
[#3892]: https://github.com/PersonalClaw/PersonalClaw/issues/3892
[#3894]: https://github.com/PersonalClaw/PersonalClaw/issues/3894
[#3895]: https://github.com/PersonalClaw/PersonalClaw/issues/3895
[#3902]: https://github.com/PersonalClaw/PersonalClaw/issues/3902
[#4570]: https://github.com/PersonalClaw/PersonalClaw/issues/4570
