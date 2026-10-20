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
  (done, error) takes `--color-orb-rest`, and each family's colour is `--color-state-<family>`. The
  orb reads the token's value again whenever the scheme, the mode or an owner's colour changes.
- Under a request for more contrast (`prefers-contrast: more`) the dots take the words' ink,
  `--color-on-surface`, and in forced colours the system's text colour. The badge is ringed, so in
  forced colours its ring and its glyph stay.
- Its drawing is the `thinking-orbs` npm package (MIT), a dependency of the web app. Its `color`
  prop takes the orb token's resolved value, so no copy of it lives in this tree, and its licence
  ships in the build's third-party notices with every other bundled package's.

## Phase to state

Every phase maps to one of four families (quiet, active, waiting, blocked) and to one orb state.
The text always names the exact phase: colour, motion and the small drawing never carry it alone.
The exact words and their lengths per surface are #3899's.

| Phase | Meaning | Family | Orb | Status text |
|---|---|---|---|---|
| `queued` | accepted, not started yet | active | breathing | Waiting to start |
| `thinking` | started, no answer yet | active | breathing | Thinking |
| `searching` | searching knowledge, the web or the files | active | searching | the phase's line, such as "Searching your knowledge" |
| `tool` | running any other tool | active | working | the tool's own label |
| `planning` | making or revising a plan | active | solving | Planning |
| `delegating` | handing work to helpers | active | connecting | the phase's line, such as "Handing off to 2 helpers" |
| `streaming` | writing its answer | active | composing | Writing |
| `listening` | push-to-talk capture is live | active | listening | Listening |
| `retrying` | a retry or a fail-over is pending | active | working | the phase's line |
| `waiting_model` | held behind other calls on a local model | active | breathing | the phase's line |
| `approval` | waiting on the owner's yes | waiting | a still frame with a "!" badge in the warn tone | the phase's line, such as "Needs your OK: shell" |
| `input` | waiting on the owner's answer | waiting | a still frame with a "?" badge in the info tone | the phase's line: the question |
| `done` | finished | quiet | a still mark in the rest colour, faded within 300 ms | the turn's ending line, such as "Done in 12 seconds" |
| `error` | failed | blocked | a still mark in the rest colour with a "×" badge in the danger tone | the turn's ending line, such as "Stopped: the reason" |
| `idle` | nothing queued or running | quiet | no orb at the head of a reply; in an empty chat, the 64 px still mark in the rest colour | none |

The status text is the gateway's line for the phase (`work_phase`'s `line`). When the line is
empty it is the phase's own word from the phase contract (`web/src/lib/workPhase.ts`), never a stock
phrase, so the words name the exact phase with the orb hidden. The orb's states inside the active
family are variants of one family: the text, not the animation, says which phase it is. The badges
differ by glyph as well as tone, so no two waiting or failed states are told apart by colour alone.

## Behaviour

- One animated orb per turn. The status line beside it is static, and while the turn runs it is
  the turn's `aria-live="polite"` region. The transcript's own narration says that a turn started
  and how it ended, so the ending line is shown and not said a second time.
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
  stays as the reply's mark while the text streams, and settles to a still mark when the turn ends,
  then fades, so a finished reply carries no mark and a reloaded chat reads as the live one did.
  Before the reply's first word it stands where the reply will be, under the owner's message, and
  it is one element throughout, so the reply's slot appearing under it restarts neither its timing
  nor its drawing.
- **The history peek, 20 px:** the chat history's peek panel heads the peeked chat's running reply
  with the same head as the chat, from that chat's run state and its `work_phase` frames, so it says
  what a turn started anywhere is doing, and a message sent from the peek is waiting to start until
  the gateway says more.
- **The send button:** while a turn runs, it is the Stop control, morphed from the send arrow with
  a thin sweep. It stays the Stop control. The sweep is a quarter arc in the button's own ink, just
  inside its rim, that turns once per Loading sweep (`sweep()` in `web/src/design/motion.ts`), and
  it goes when the turn ends. Under reduced motion there is no sweep, and the button is not
  remounted when its kind changes, so keyboard focus stays on it.
- **64 px, rarely:** only in empty states, voice mode and the buddy's expanded view. The empty chat
  opens on it, still and in the rest colour (`idle`), where the brand mark used to morph: nothing
  runs in a new chat, so nothing on it moves.
- **Settings › Design, both sizes:** every phase at 20 and 64 px under its family, in the owner's own
  scheme, closed until asked for, since the active family moves. The visual suite photographs it.

Removed with the orb: the guessed phase, the rotating phrases and the glow of
`web/src/ui/chat/StreamingIndicator.tsx`, the history peek's opacity pulse, the empty chat's
morphing brand mark, and the composer halo as a working signal. Claw Arcade keeps its halo as decoration (`web/DESIGN.md` §7).

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
- Snapshots of every state at 20 px and 64 px, light and dark, and under reduced motion: Settings ›
  Design's states, photographed by `web/e2e/visual.spec.ts` with the page's clock paused, so a
  moving state is the same frame every run.
