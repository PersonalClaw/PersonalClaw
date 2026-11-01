# Agent orb

The chat's thinking and answering indicator. #3807 builds it, and a change to the table below lands
here first. [`web/DESIGN.md`](../../web/DESIGN.md) wins every conflict.

## Purpose

One small orb in the turn's live line, at the end of the conversation, shows what the agent is doing in
a chat turn, and says it in one static line. It goes still the moment the owner is needed. It replaces the pulsing "Thinking…"
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
- Its drawing is the `thinking-orbs` npm package (MIT), a dependency of the web app. The orb paints
  the package's drawings with its engine and painter, tinted with the orb token's resolved value,
  so no copy of it lives in this tree, and its licence ships in the build's third-party notices
  with every other bundled package's.

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
| `idle` | nothing queued or running | quiet | no status row; in an empty chat, the 64 px still mark in the rest colour | none |

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
- While it moves it paints at most 30 frames a second, whatever the display offers, through the
  loop the buddy's dots are painted with (`web/src/ui/orbDots.ts`): it waits out each gap on a
  timer and only then asks for one frame, so it asks for none between two paints.
- It is `role="img"` with a label for each state, and it is announced once. The status line carries
  the words.
- A sidebar row never shows the orb: a working thread's row has a static pip.

## Placement

- **The turn's live line, 20 px:** one bare line at the end of the conversation, under the latest
  message in the turn column, with no border, background or pill (`web/src/ui/TurnStatusRow.tsx`,
  `web/REDESIGN.md` §5 "Live state sits at the bottom"). It scrolls with the thread, shows whenever
  a turn runs, and goes when the turn ends, so a finished reply carries no mark and the head of a
  reply never animates. Beside the orb are the status line, a word that turns over while the turn
  works (never said, and still under reduced motion), the time since the turn began and the tokens
  the model reported for it (none until one is reported). Stop is on the composer: its send
  button, or a secondary Stop beside Steer or Queue once a draft is typed. Esc presses it too.
- **The history peek, 20 px:** the chat history's peek panel heads the peeked chat's running reply
  with a turn head (`web/src/pages/chat/transcript/TurnHead.tsx`), from that chat's run state and its `work_phase` frames, so it says
  what a turn started anywhere is doing, and a message sent from the peek is waiting to start until
  the gateway says more.
- **The send button:** while a turn runs, it is the Stop control, morphed from the send arrow with
  a thin sweep. It stays the Stop control until a draft is typed, when it steers or queues the
  draft and a secondary Stop, with no sweep, sits beside it. The sweep is a quarter arc in the button's own ink, just
  inside its rim, that turns once per Loading sweep (`sweep()` in `web/src/design/motion.ts`), and
  it goes when the turn ends. It reads the turn's phase as the orb does: while the turn waits on
  the owner (`approval` or `input`) the arc holds still where it is, and it turns on from there
  once the turn runs again. Under reduced motion there is no sweep, and the button is not
  remounted when its kind changes, so keyboard focus stays on it.
- **64 px, rarely:** only in empty states, voice mode and the buddy's expanded view. The empty chat
  opens on it, still and in the rest colour (`idle`), where the brand mark used to morph: nothing
  runs in a new chat, so nothing on it moves. Voice mode is the composer's hands-free state: while
  it is on, the orb stands above the draft, `listening` while the microphone hears the owner and
  the turn's own phase the rest of the time (muted while a reply is spoken, transcribing, or
  between segments). In an empty chat it takes the place of the chat's orb, so one shows.
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
