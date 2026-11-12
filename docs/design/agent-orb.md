# Agent orb

The chat's thinking and answering indicator. #3807 built it, #6117 gave each state a form of its
own, and a change to the tables below lands here first. [`web/DESIGN.md`](../../web/DESIGN.md) wins
every conflict.

## Purpose

One small orb in the turn's live line, at the end of the conversation, shows what the agent is doing in
a chat turn, and says it in one static line. Each state has its own form and its own motion, so the
owner can tell at a glance whether the agent is thinking, working, waiting on them or done, and the
words beside it always say the same thing. It goes still the moment the owner is needed. It replaces the pulsing "Thinking…"
indicator and the composer halo as the working signal, so the default look needs no light around
the composer to show that the agent is at work. When a turn ends, its line shows how it ended, done
(with its one bloom when the turn completed) or a failure with its ×, and then goes; after that,
with no turn running, nothing on the page animates.

## The component

- `web/src/ui/AgentOrb.tsx` takes a phase from the phase contract
  ([#3833](https://github.com/PersonalClaw/PersonalClaw/issues/3833)) and a size, and reads its
  state from the one mapping, `web/src/lib/orbState.ts`. Pages pass a phase, never a raw orb state,
  so no page can show a state the contract does not name.
- **The form is the claw ring** (`web/src/ui/orb/clawRing.ts`): six curved strokes around a soft
  centre, drawn like the brand mark's two, its blade and its hook (`web/public/claw.svg`), each as
  one curve with round caps (`web/src/ui/orb/paintRing.ts`). "A capable engine with a soft shell"
  (`web/DESIGN.md` §1): the strokes are the mark, the open centre the shell. A state is one set of stroke values, so
  a change of state morphs the form and never jumps. It draws no fill, no glow, no gradient and no
  backdrop.
- Three sizes, 64, 20 and 12 px. The strokes widen at 20 px and below, so the form still reads; the
  12 px mark never moves.
- Its colour is `--color-orb-alive`, one of the shared brand and state tokens
  ([`web/DESIGN.md` §2](../../web/DESIGN.md#the-token-layers)): the active scheme's primary, never a
  literal coral, so every scheme and identity recolours it. Where nothing is alive (idle, done and
  a failure) it takes `--color-orb-rest`; listening is ready's form in the alive ink. The orb reads the token's value again whenever the
  scheme, the mode or an owner's colour changes.
- Under a request for more contrast (`prefers-contrast: more`) the strokes take the words' ink,
  `--color-on-surface`, and in forced colours the system's text colour. The badge is ringed, so in
  forced colours its ring and its glyph stay, and every state keeps its own shape.
- The form and its painter are the project's own code: no package draws the chat's orb. The
  desktop buddy's orb (`web/src/ui/BuddyOrb.tsx`) still draws the `thinking-orbs` package's dots
  (MIT, credited in the web build's third-party notices) until it moves to the claw ring too.

## The five states

| State | Form | Motion | Ink |
|---|---|---|---|
| Ready | the open ring, its six strokes spaced around the centre | none | rest; alive while it listens |
| Thinking | the strokes swell outward and draw back, each at its own phase, so the ring never moves as one | continuous, one swell per `--duration-breath` | alive |
| Working | two rings, three strokes near the centre and three outside, turning in opposite directions, like a small mechanism | continuous, the inner ring once per `--duration-loading`, never faster than once a second, and the outer once per two | alive |
| Waiting on you | the strokes lock into a clasp, two halves closed round the centre and hooked across their seams | none: the morph into it is its only motion | alive, with the ! or ? badge |
| Done | the strokes close into the claw mark itself, its short blade and its long hook | one success bloom on `physics.playful` when the turn completed, then none | rest |

- A failure is done's form, in the rest ink, with the × badge, and it never blooms. It is not a
  sixth form. A turn stopped by the owner or declined closes to done's form without the bloom.
- A change of state morphs on `physics.smooth` (`web/DESIGN.md` §5: a spatial change is a spring).
  Arriving at done from a completed turn adds the one success bloom, a sanctioned personality
  moment; only a completed turn blooms.
- Nothing in the orb repeats faster than three times a second.

## Phase to state

Every phase maps to one of the five states. The text always names the exact phase: colour, motion
and the small drawing never carry it alone. The exact words and their lengths per surface are
#3899's.

| Phase | Meaning | State | Status text |
|---|---|---|---|
| `idle` | nothing queued or running | ready | no status row; in an empty chat, the 64 px orb at rest |
| `listening` | push-to-talk capture is live | ready, in the alive ink | Listening |
| `queued` | accepted, not started yet | thinking | Waiting to start |
| `thinking` | started, no answer yet | thinking | Thinking |
| `planning` | making or revising a plan | thinking | Planning |
| `streaming` | writing its answer | thinking | Writing |
| `waiting_model` | held behind other calls on a local model | thinking | the phase's line |
| `retrying` | a retry or a fail-over of the model call is pending | thinking | the phase's line |
| `tool` | running any other tool | working | the tool's own label |
| `searching` | searching knowledge, the web or the files | working | the phase's line, such as "Searching your knowledge" |
| `delegating` | handing work to helpers | working | the phase's line, such as "Handing off to 2 helpers" |
| `approval` | waiting on the owner's yes | waiting on you, with a "!" badge in the warn tone | the phase's line, such as "Needs your OK: shell" |
| `input` | waiting on the owner's answer | waiting on you, with a "?" badge in the info tone | the phase's line: the question |
| `done` | finished, stopped by the owner or declined | done, with its one bloom only when the turn completed (`last_outcome` is `complete`) | the turn's ending line, such as "Done in 12 seconds" or "Stopped" |
| `error` | failed | done's form with a "×" badge in the danger tone, and no bloom | the turn's ending line, such as "Stopped: the reason" |

The status text is the gateway's line for the phase (`work_phase`'s `line`). When the line is
empty it is the phase's own word from the phase contract (`web/src/lib/workPhase.ts`), never a stock
phrase, so the words name the exact phase with the orb hidden. The orb says which of the five
states a turn is in; the text, not the form, says which phase. The badges differ by glyph as well
as tone, so no two waiting or failed states are told apart by colour alone.

No percent is drawn: the gateway measures a phase and a line, not how far a turn has come. The line
beside a working orb is the current action, with the time since the turn began and the tokens the
model reported.

## Behaviour

- One orb per turn. The status line beside it is static, and while the turn runs it is the turn's
  `aria-live="polite"` region, said once each time its words change and never per frame. The
  transcript's own narration says that a turn started and how it ended, so the ending line is
  shown and not said a second time: the region empties while the ending holds.
- The orb appears only after `showDelay` (150 ms) and stays at least `minHold` (300 ms), unless its
  turn ends without saying how, as a Stop pressed here does, so a fast reply never flashes it. A
  turn's ending holds `minHold` once its orb has settled on it, then fades over `--duration-medium`
  (`web/src/ui/turnEnding.ts`). The buddy uses the same pair.
- Ready, waiting on you and done are still, so the card that needs the owner holds their attention.
- Under reduced motion each state is its still form, and a change of state is an instant swap,
  with no morph and no bloom.
- It pauses offscreen and in a hidden tab. With no turn running and no turn's ending on show,
  neither the orb nor anything else schedules an animation frame.
- While it moves or morphs it paints at most 30 frames a second, whatever the display offers,
  through the loop the buddy's dots are painted with (`web/src/ui/orbDots.ts`): it waits out each
  gap on a timer and only then asks for one frame, so it asks for none between two paints. The
  morph and the bloom step on the same clock.
- It is `role="img"` with a label for each phase, and it is announced once. The status line carries
  the words.

## Sizes and places

- **The turn's live line, 20 px:** one bare line at the end of the conversation, under the latest
  message in the turn column, with no border, background or pill (`web/src/ui/TurnStatusRow.tsx`,
  `web/REDESIGN.md` §5 "Live state sits at the bottom"). It scrolls with the thread and shows
  whenever a turn runs. When the turn ends it shows how it ended, done (blooming only for a
  completed turn) or a failure's × beside the ending's line, with its clock stopped and nothing new
  said, until the orb has settled and `minHold` more; then it fades and goes, so a finished reply
  carries no mark and the head of a reply never animates. Voice mode's orb shows the same ending
  before it rests. Beside the orb are the status line, a word that turns over while the turn works
  (never said, and still under reduced motion), the time since the turn began and the tokens the
  model reported for it (none until one is reported). Stop is on the composer: its send button, or
  a secondary Stop beside Steer or Queue once a draft is typed. Esc presses it too.
- **The history peek, 20 px:** the chat history's peek panel heads the peeked chat's running reply
  with a turn head (`web/src/pages/chat/transcript/TurnHead.tsx`), from that chat's run state and
  its `work_phase` frames, so it says what a turn started anywhere is doing, and a message sent
  from the peek is waiting to start until the gateway says more.
- **The sidebar's rows, 12 px:** a row's state is the orb's still mark (`web/src/ui/orb/OrbMark.tsx`),
  shape and colour only, never moving at any setting. The rail knows a row's pip, not its phase
  (`web/src/lib/useThreads.ts`), so it reads the pip's state from the same file
  (`ORB_STATE_OF_PIP`): a running row is working's two rings in green, a waiting row the clasp in
  orange, an unread row done's mark in grey and a failed row done's mark in red with its ×. The
  colours are the rail's own (`web/src/ui/ThreadRail.tsx`); the word follows the name in the row's
  tooltip, the app's own (`web/src/ui/Tooltip.tsx`), before any note of an app's permissions or a
  helper's ask, and it ends the row's name. No browser tooltip shows on the sidebar's rows and
  marks, a project's chat rows or the live line's tokens. A calm row, ready or read, has no mark.
- **A project's chat rows, 12 px:** the project page lists its chats with the sidebar's marks, from
  the same pips (`web/src/pages/projects/ProjectThreads.tsx`), in the family's shared state token
  (`--color-state-<family>`) with the state's words beside it.
- **Home's agent rows, 12 px:** each agent's row on Home draws the still mark for its state
  (`web/src/pages/dashboard/agents/AgentDay.tsx`, `ORB_STATE_OF_AGENT_ROW`): a working agent is
  working's two rings, a waiting one the clasp, a failed one done's mark with its × and an idle one
  the open ring, in the shared state tokens with the state's words beside it.
- **The buddy card's rows, 12 px:** each row of the work in sight and each of a step's helpers is
  the still mark (`web/src/ui/BuddyCard.tsx`), so nothing in the card's lists moves. The buddy's own
  orb, on its window, its stage, its sheets and the phone's list of all work, keeps its dots until
  it moves to the claw ring too.
- **Not the orb:** a workflow's runs and its run page keep their own status icons, since a
  workflow run's statuses are not a turn's states.
- **The send button:** while a turn runs, it is the Stop control, morphed from the send arrow with
  a thin sweep. It stays the Stop control until a draft is typed, when it steers or queues the
  draft and a secondary Stop, with no sweep, sits beside it. The sweep is a quarter arc in the button's own ink, just
  inside its rim, that turns once per Loading sweep (`sweep()` in `web/src/design/motion.ts`), and
  it goes when the turn ends. It reads the turn's state as the orb does: while the orb holds still
  (the turn waits on the owner) the arc holds still where it is, and it turns on from there once the
  turn runs again. Under reduced motion there is no sweep, and the button is not remounted when its
  kind changes, so keyboard focus stays on it.
- **64 px, rarely:** only in the empty chat and voice mode. The empty chat
  opens on it, ready and in the rest colour (`idle`), where the brand mark used to morph: nothing
  runs in a new chat, so nothing on it moves. Voice mode is the composer's hands-free state: while
  it is on, the orb stands above the draft, ready while the microphone hears the owner (the words
  say Listening), which is the whole mode, since it stays open between pieces, and the turn's own
  phase while it is muted for a reply read aloud. In an empty chat it takes the place of the
  chat's orb, so one shows.
- **Settings › Appearance › Design, every size:** every phase at 64, 20 and 12 px under its state, in
  the owner's own scheme, closed until asked for, since thinking and working move. The visual suite
  photographs it.

Removed with the orb: the guessed phase, the rotating phrases and the glow of
`web/src/ui/chat/StreamingIndicator.tsx`, the history peek's opacity pulse, the empty chat's
morphing brand mark, the composer halo as a working signal, and the sidebar rows' glowing dots.
Claw Arcade keeps its halo as decoration (`web/DESIGN.md` §7).

## Acceptance

- Every phase in the contract maps to exactly one state, and no state can be reached from a raw
  string (`web/src/lib/orbState.test.ts`); every phase has a status text of its own
  (`web/src/lib/workPhase.test.ts`).
- Every state has a form of its own, the morph from any state to any other moves in small steps,
  and a failure never blooms (`web/src/ui/orb/clawRing.test.ts`).
- With fake timers, a phase that ends before 150 ms never shows the orb, and a shown orb stays at
  least 300 ms, unless its turn ends without saying how, as a Stop pressed here does.
- No animation-frame callback runs at rest, with no turn running and no ending on show, under
  reduced motion, in a hidden tab, for the 12 px mark, or once a morph into ready, waiting on you or
  done has settled (a `requestAnimationFrame` spy with a positive control that does see frames).
- Every phase is checked under reduced motion and high contrast, and with the orb hidden its text
  still names the phase for a screen reader.
- `npm run e2e:a11y --workspace=web` stays green.
- Snapshots of every phase at 64, 20 and 12 px, light and dark, and under reduced motion: Settings ›
  Design's states, photographed by `web/e2e/visual.spec.ts` with the page's clock paused, so a
  moving state is the same frame every run.
