# Agent orb

The chat's thinking and answering indicator. #3807 builds it, and a change to the table below lands
here first. [`web/DESIGN.md`](../../web/DESIGN.md) wins every conflict.

## Purpose

One small orb at the head of the reply shows what the agent is doing in a chat turn, and says it in
one static line. It goes still the moment the owner is needed. It replaces the pulsing "Thinking…"
indicator and the composer halo as the working signal, so the default look needs no light around
the composer to show that the agent is at work. With no turn running, nothing on the page animates.

## The component

- `web/src/ui/AgentOrb.tsx` takes a phase from the phase contract
  ([#3833](https://github.com/PersonalClaw/PersonalClaw/issues/3833)) and a size, and maps the phase
  to an orb state. Pages pass a phase, never a raw orb state, so no page can show a state the
  contract does not name.
- Two sizes, 20 px and 64 px. They are separately tuned designs, never one drawing scaled to the
  other.
- Its colour is `--color-orb-alive`, one of the shared brand and state tokens
  ([`web/DESIGN.md` §2](../../web/DESIGN.md#the-token-layers)): the active scheme's primary, never a
  literal coral, so every scheme and identity recolours it. A still mark where nothing is alive
  (done) takes `--color-orb-rest`, and each family's colour is `--color-state-<family>`.
- Its drawing comes from the `thinking-orbs` component (MIT), given a colour prop because that
  component draws in one colour. Whether it arrives as an npm dependency or a vendored copy is
  #3807's call; a vendored copy brings its own licence file.

## Phase to state

Every phase maps to one of four families (quiet, active, waiting, blocked) and to one orb state.
The text always names the exact phase: colour, motion and the small drawing never carry it alone.
The exact words and their lengths per surface are #3899's.

| Phase | Meaning | Family | Orb | Status text |
|---|---|---|---|---|
| `queued` | accepted, not started yet | active | breathing | the line for `queued` ("waiting to start") |
| `thinking` | started, no answer yet | active | breathing | Thinking… |
| `searching` | searching knowledge, the web or the files | active | searching | the phase's line, such as "Searching your knowledge" |
| `tool` | running any other tool | active | working | the tool's own label |
| `planning` | making or revising a plan | active | solving | Planning |
| `delegating` | handing work to helpers | active | connecting | Delegating |
| `streaming` | writing its answer | active | composing | Writing |
| `listening` | push-to-talk capture is live | active | listening | Listening |
| `approval` | waiting on the owner's yes | waiting | a still frame with a "!" badge in the warn tone | Needs your approval |
| `input` | waiting on the owner's answer | waiting | a still frame with a "?" badge in the info tone | the line for `input` (the question) |
| `done` | finished | quiet | a still mark, faded within 300 ms | the turn's ending line |
| `error` | failed | blocked | a still mark with a badge in the danger tone | the turn's ending line |

The orb's states inside the active family are variants of one family: the text, not the
animation, says which phase it is.

## Behaviour

- One animated orb per turn. The status line beside it is static, and it is the turn's
  `aria-live="polite"` region.
- The orb appears only after `showDelay` (150 ms) and stays at least `minHold` (300 ms), so a fast
  reply never flashes it. The buddy uses the same pair.
- Approval, input and error are still frames, so the card that needs the owner holds their
  attention.
- Under reduced motion the orb is one static frame for its state.
- It pauses offscreen and in a hidden tab. With no turn running, neither the orb nor anything else
  schedules an animation frame.
- It is `role="img"` with a label for each state, and it is announced once. The status line carries
  the words.
- A sidebar row never shows the orb: a working thread's row has a static pip.

## Placement

- **The turn head, 20 px:** at the start of the assistant's reply slot, left of the status line. It
  stays as the reply's mark while the text streams, and settles to a still mark when the turn ends.
- **The send button:** while a turn runs, it is the Stop control, morphed from the send arrow with
  a thin sweep. It stays the Stop control.
- **64 px, rarely:** only in empty states, voice mode and the buddy's expanded view.

Removed with the orb: the guessed phase, the rotating phrases and the glow of
`web/src/ui/chat/StreamingIndicator.tsx`, the mini chat panel's opacity pulse, and the composer halo
as a working signal. Claw Arcade keeps its halo as decoration (`web/DESIGN.md` §7).

## Acceptance

- Every phase in the contract maps to exactly one orb state, one family and one status text, and no
  orb state can be reached from a raw string (a unit test).
- With fake timers, a phase that ends before 150 ms never shows the orb, and a shown orb stays at
  least 300 ms.
- No animation-frame callback runs with no turn running, under reduced motion, in a hidden tab, or
  while the phase is `approval`, `input` or `error` (a `requestAnimationFrame` spy with a positive
  control that does see frames).
- Every phase is checked under reduced motion and high contrast, and with the orb hidden its text
  still names the phase for a screen reader.
- `npm run e2e:a11y --workspace=web` stays green.
- Snapshots of every state at 20 px and 64 px, light and dark, and under reduced motion.
