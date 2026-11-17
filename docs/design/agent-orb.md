# Agent orb

The chat's thinking and answering indicator. #3807 built it, #6117 gave each state a form of its
own, and the owner's round-6 design ("Looks good. Go with this proposal.", 11-16) made it a halftone
sphere of dots with twenty states. A change to the tables below lands here first.
[`web/DESIGN.md`](../../web/DESIGN.md) wins every conflict; where this design asked for something
else, the section says so.

## Purpose

One small orb in the turn's live line, at the end of the conversation, shows what the agent is doing in
a chat turn, and says it in one static line. Each state has its own drawing and its own motion, so the
owner can tell at a glance whether the agent is thinking, reading, running a command, waiting on them
or done, and the words beside it always say the same thing. It goes still the moment the owner is
needed. It replaces the pulsing "Thinking…" indicator and the composer halo as the working signal, so
the default look needs no light around the composer to show that the agent is at work. When a turn
ends, its line shows how it ended, done (with its one swirl when the turn completed) or a failure
with its ×, and then goes; after that, with no turn running, nothing on the page animates.

## The component

- `web/src/ui/AgentOrb.tsx` takes a phase from the phase contract
  ([#3833](https://github.com/PersonalClaw/PersonalClaw/issues/3833)), and a stage where a page has one
  that is not a phase, and a size, and reads its state from the one mapping, `web/src/lib/orbState.ts`.
  Pages pass a phase or a typed stage, never a raw orb state, so no page can show a state the contract
  or the page did not measure.
- **The form is the halftone sphere** (`web/src/ui/orb/halftone.ts`): 272 dots on a golden-angle
  spiral over a sphere tilted 0.32 rad toward you, each dot as large and as solid as it is near. A
  state places every one of the 272 dots, so a change of state moves dots and never jumps. The
  scenes are `web/src/ui/orb/scenes.ts` and the plate's marks `web/src/ui/orb/plateMarks.ts`. It
  draws no glow, no gradient and no backdrop; the one shadow is the plate's (below).
- Three sizes, 64, 20 and 12 px. At 20 px and below a dot is drawn a little larger so it still
  reads, and a state with a plate gives its dots up to the mark. The 12 px mark never moves.
- Its inks are the shared brand and state tokens
  ([`web/DESIGN.md` §2](../../web/DESIGN.md#the-token-layers)): `--color-orb-alive`, the active
  scheme's primary, for what is active, and `--color-orb-rest` for what is passive; `--color-ok`,
  `--color-warn`, `--color-info` and `--color-danger` only where a state names them. Never a literal
  coral, so every scheme and identity recolours it. The orb reads the tokens again whenever the
  scheme, the mode or an owner's colour changes.
- Under a request for more contrast (`prefers-contrast: more`) what is active and what is passive take
  the words' ink, `--color-on-surface`, and in forced colours everything takes the system's text
  colour. Every state keeps its own shape, and each mark its own glyph.
- The form and its painter are the project's own code, and no package draws either orb. The desktop
  buddy's orb (`web/src/ui/BuddyOrb.tsx`) still draws the claw ring until it moves to the halftone
  ([`buddy/character.md`](buddy/character.md#the-poses)).

## The form

- **Ready.** The sphere, still and grey.
- **Thinking.** A ripple in the alive ink travels out from the centre.
- **The five plate states** (done, needs your OK, asks you, error, offline). The orb steps up and to
  the left at 0.74 scale, and a bold mark sits on a small plate at its lower right: the surface, a
  hairline and a soft drop shadow.
  - **Done.** The orb swirls once over 0.9 s, the whole orb bumps, and the mark is a green check.
    Only a completed turn swirls; a stop or a decline settles into done without.
  - **Needs your OK, asks you.** The orb is drawn in tight and lit; the mark is "!" in the warn tone or
    "?" in the info tone.
  - **Error.** The orb sinks; the mark is "×" in the danger tone.
  - **Offline.** The orb dims; the mark is a broken link in the rest ink.
- **Every other state** is a scene the same dots fly out of the sphere to become, and any dot may
  stretch into a short line. The orb stays on screen, smaller, with its ripple, and the work flows
  into it or out of it; for the jobs about the whole, planning and searching, it grows into a faint
  globe.
- **Words** in a scene are dots that join into one clean dash once written.
- **Plate shadow.** `web/DESIGN.md` asks for "elevation by tone, not shadow"; the approved design
  lifts the plate with a soft drop shadow, and the port keeps it as approved. It is the orb's one
  shadow, at 64 px only.

## The twenty states

| State | What it draws | Motion | Ink |
|---|---|---|---|
| Ready | the sphere | none | rest |
| Thinking | the ripple, out from the centre | one ripple per 1.9 s | alive |
| Queued | the orb waits, dimmed and breathing, behind a small grey orb whose turn it is; a quiet ellipsis between | slow breath, the ellipsis every 2 s | alive, the other's grey |
| Planning | the globe turns a little and charts a great-circle route on its surface, waypoint by waypoint (rings of 8 dots, the last one green); a 3-dot pulse then runs the route | one route per 6.4 s | alive, ok at the end |
| Searching | a scan line sweeps the globe, lighting the dots under it; three hits ping once and stay ringed | one sweep per 3.6 s | alive on rest |
| Reading | a dotted document with a folded corner; a highlight grows along the line being read, read words turn coral, and a 5-dot stream flows into the orb; the page scrolls a line at a time | one line per 2.6 s | alive, unread grey |
| Editing | a diff in place: a red band and "-" mark a line whose dots drop away; a caret types the replacement from a 4-dot stream out of the orb, under a green band and "+", then it settles to ink | one edit per 4.4 s | alive, danger, ok |
| Running a command | a 3-dot stream goes from the orb through a ">"; output lines scroll up, the newest lit | a line per 0.46 s | alive, older lines grey |
| Delegating | the big orb, two little orbs over dotted links, and packets; each helper pulses as a packet lands | a packet per 0.5 s a link | alive |
| Answering | the orb at bottom left; a dotted bubble whose tail is part of its outline points at it and fills word by word; a stream goes up into the write head | one reply per 3.8 s | alive |
| Retrying | a little orb circles the orb on a 30-dot track, lighting it behind; a 3-dot arrowhead leads; it backs up a quarter turn, pauses, and goes on | one try per 2.6 s | alive on rest |
| Needs your OK | the orb drawn in tight and lit, "!" on its plate | none: the morph into it is its only motion | alive, warn |
| Asks you | the same, "?" on its plate | none | alive, info |
| Listening | a tapered audio graph whose waves travel into the orb | continuous, with the level | alive |
| Recording | the orb becomes a small, dense red dot that pulses with the level, in a ring of 24 grey ticks | continuous, with the level | danger, rest |
| Transcribing | sound bars go in on the left of the orb, and words come out on the right, as dots that join into dashes | continuous | alive |
| Speaking | three dotted arcs go out from the orb, as loud as the voice | an arc per 0.53 s | alive |
| Done | the orb beside its plate, a green check | one swirl and bump on arrival from a completed turn, then none | rest, ok |
| Error | the orb sunk, "×" on its plate | none | rest, danger |
| Offline | the orb dimmed, a broken link on its plate | none | rest |

- Every scene with phases starts from its beginning each time its state is entered: the time since
  the state was entered drives it. Its loops are calm, 2.4 to 6.4 s, and nothing in the orb repeats
  faster than three times a second. The timings are the approved design's own, written in the
  scenes as seconds rather than read from the motion tokens, so a slider in Settings › Motion does
  not redraw what the owner approved.
- The voice states move with a level. Until the voice paths hand the orb the microphone's or the
  reply's level, it is a smooth, irregular stand-in, so the drawing rises and falls like a voice
  without claiming to measure one.
- Reading and editing are drawn and in the gallery's code, but nothing drives them yet: they need the
  gateway to say what a tool call does (read, edit or run). Until it does, every tool call is drawn as
  running a command.

## Phase to state

Every phase maps to one of the twenty states (`ORB_SCENE_OF`), and the few things that drive the orb
without being a phase are a closed set of typed stages (`ORB_STAGES`), each named by the page that
knows it. The text always names the exact phase or stage: colour, motion and the drawing never carry
it alone. The exact words and their lengths per surface are #3899's.

| Phase or stage | Meaning | State | Status text |
|---|---|---|---|
| `idle` | nothing queued or running | ready | no status row; in an empty chat, the 64 px orb at rest |
| `queued` | accepted, not started yet | queued | Waiting to start |
| `waiting_model` | held behind other calls on a local model | queued: it waits behind another's turn | the phase's line |
| `thinking` | started, no answer yet | thinking | Thinking |
| `planning` | making or revising a plan | planning | Planning |
| `searching` | searching knowledge, the web or the files | searching | the phase's line, such as "Searching your knowledge" |
| `tool` | running any other tool | running a command, until the gateway says what a call does | the tool's own label |
| `delegating` | handing work to helpers | delegating | the phase's line, such as "Handing off to 2 helpers" |
| `streaming` | writing its answer | answering | Writing |
| `retrying` | a retry or a fail-over of the model call is pending | retrying | the phase's line |
| `approval` | waiting on the owner's yes | needs your OK, "!" in the warn tone | the phase's line, such as "Needs your OK: shell" |
| `input` | waiting on the owner's answer | asks you, "?" in the info tone | the phase's line: the question |
| `done` | finished, stopped by the owner or declined | done, with its one swirl only when the turn completed (`last_outcome` is `complete`) | the turn's ending line, such as "Done in 12 seconds" or "Stopped" |
| `error` | failed | error, "×" in the danger tone, and no swirl | the turn's ending line, such as "Stopped: the reason" |
| `listening` | voice mode hears the owner, hands-free | listening | Listening |
| stage `recording` | a push-to-talk capture: the buddy's capture, and the tray's listening still | recording | Recording |
| stage `transcribing` | voice mode turns the last piece of what the owner said into text | transcribing | Turning that into text |
| stage `speaking` | a reply read aloud in voice mode | speaking | Reading the reply aloud |
| stage `offline` | the gateway out of reach: the buddy and the tray | offline | Offline |

- A stage wins over the phase while it holds, since a page names one only while it is true. Voice
  mode is listening while the microphone hears the owner, speaking while a reply is read aloud, and
  transcribing while the last piece is turned into text (`web/src/ui/composer/voiceOrb.ts`); the
  rest of the time it is the turn's own phase.
- The buddy's announced restart is drawn as retrying, since it comes back: never offline.
- The status text is the gateway's line for the phase (`work_phase`'s `line`). When the line is
  empty it is the phase's own word from the phase contract (`web/src/lib/workPhase.ts`), never a stock
  phrase, so the words name the exact phase with the orb hidden. The marks differ by glyph as well
  as tone, so no two waiting or stopped states are told apart by colour alone.

No percent is drawn: the gateway measures a phase and a line, not how far a turn has come. The line
beside a working orb is the current action, with the time since the turn began and the tokens the
model reported.

## Rows and the tray

A scene's drawing does not hold at 12 px, so every 12 px mark is one of the sphere's own forms:
ready, thinking and the five plate states. An orb drawn at 12 px takes the form for its phase or
stage (`markSceneOfPhase`: live work and listening think, a wait or a stop is its plate, the gateway
away is offline); a row knows only its pip, and is drawn as the form that says it. The tray's six
stills take the same forms, but for its listening, the recording dot, which a menu bar draws by its
shape alone.

| Where | What it knows | State drawn |
|---|---|---|
| a sidebar row, a project's chat row (`ORB_STATE_OF_PIP`) | running, waiting, failed, unread | thinking, needs your OK, error, done |
| Home's agent rows (`ORB_STATE_OF_AGENT_ROW`) | working, blocked, waiting, failed, interrupted, idle | thinking, thinking, needs your OK, error, error, ready |
| the tray's six stills (`desktop/trayPresence.js`'s `TRAY_STILLS`) | idle, working, needs-you, error, listening, offline | ready, thinking (the lit orb with its ripple, the one still that reads as at work at 18 px), needs your OK, error, recording (the dense dot in its ring of ticks, which a template image draws by shape alone), offline |

The tray's stills move to the halftone with the buddy.

## Behaviour

- One orb per turn. The status line beside it is static, and while the turn runs it is the turn's
  `aria-live="polite"` region, said once each time its words change and never per frame. The
  transcript's own narration says that a turn started and how it ended, so the ending line is
  shown and not said a second time: the region empties while the ending holds.
- The orb appears only after `showDelay` (150 ms) and stays at least `minHold` (300 ms), unless its
  turn ends without saying how, as a Stop pressed here does, so a fast reply never flashes it. A
  turn's ending holds `minHold` once its orb has settled on it, then fades over `--duration-medium`
  (`web/src/ui/turnEnding.ts`). The buddy uses the same pair.
- Ready, the two waits on the owner, done and the two stops are still, so the card that needs the
  owner holds their attention.
- A change of state moves every dot from its place in the frame last painted to its place in the new
  state's, on `physics.smooth` (`web/DESIGN.md` §5: a spatial change is a spring), and mid-change the
  dots burst a little outward, streaked, and land exactly (`web/src/ui/orb/useSceneMorph.ts`). A
  change that starts mid-change starts from what was last painted. Arriving at done from a completed
  turn adds its one swirl and bump, a sanctioned personality moment.
- Under reduced motion each state is its still frame, which tells it apart from every other, and a
  change of state is an instant swap, with no morph and no swirl. The approved design crossfades the
  stills over 0.2 s; the app keeps its rule that a reduced change asks for no frame, so it swaps.
- It pauses offscreen and in a hidden tab. With no turn running and no turn's ending on show,
  neither the orb nor anything else schedules an animation frame.
- While it moves or morphs it paints at most 30 frames a second, whatever the display offers,
  through the loop the buddy's dots are painted with (`web/src/ui/orbDots.ts`): it waits out each
  gap on a timer and only then asks for one frame, so it asks for none between two paints. The
  morph and the swirl step on the same clock.
- It is `role="img"` with a label for each phase or stage, and it is announced once. The status line
  carries the words.

## Sizes and places

- **The turn's live line, 20 px:** one bare line at the end of the conversation, under the latest
  message in the turn column, with no border, background or pill (`web/src/ui/TurnStatusRow.tsx`,
  `web/REDESIGN.md` §5 "Live state sits at the bottom"). It scrolls with the thread and shows
  whenever a turn runs. When the turn ends it shows how it ended, done (swirling only for a
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
  (`ORB_STATE_OF_PIP`, "Rows and the tray"): a running row is the thinking sphere in green, a waiting
  row needs your OK with its "!" in orange, an unread row done with its check in grey and a failed
  row the error mark with its × in red. The
  colours are the rail's own (`web/src/ui/ThreadRail.tsx`); the word follows the name in the row's
  tooltip, the app's own (`web/src/ui/Tooltip.tsx`), before any note of an app's permissions or a
  helper's ask, and it ends the row's name. No browser tooltip shows on the sidebar's rows and
  marks, a project's chat rows or the live line's tokens. A calm row, ready or read, has no mark.
- **A project's chat rows, 12 px:** the project page lists its chats with the sidebar's marks, from
  the same pips (`web/src/pages/projects/ProjectThreads.tsx`), in the family's shared state token
  (`--color-state-<family>`) with the state's words beside it.
- **Home's agent rows, 12 px:** each agent's row on Home draws the still mark for its state
  (`web/src/pages/dashboard/agents/AgentDay.tsx`, `ORB_STATE_OF_AGENT_ROW`): a working agent is the
  thinking sphere, a waiting one needs your OK, a failed one the error mark with its × and an idle one
  the resting sphere, in the shared state tokens with the state's words beside it.
- **The buddy card's rows, 12 px:** each row of the work in sight and each of a step's helpers is
  the still mark (`web/src/ui/BuddyCard.tsx`), so nothing in the card's lists moves. The buddy's own
  orb, on its window, its stage, its sheets and the phone's list of all work, keeps the claw ring
  until it moves to the halftone too.
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
  it is on, the orb stands above the draft, listening while the microphone hears the owner (the words
  say Listening), which is the whole mode, since it stays open between pieces; speaking while a
  reply is read aloud; transcribing while the last piece is turned into text; and the turn's own
  phase the rest of the time. In an empty chat it takes the place of the chat's orb, so one shows.
- **Settings › Appearance › Design, every size:** every state the orb can show, by family, each with
  the phases and stages that drive it, at 64, 20 and 12 px, in the owner's own scheme, closed until
  asked for, since most states move. The visual suite photographs it.

Removed with the orb: the guessed phase, the rotating phrases and the glow of
`web/src/ui/chat/StreamingIndicator.tsx`, the history peek's opacity pulse, the empty chat's
morphing brand mark, the composer halo as a working signal, and the sidebar rows' glowing dots.
Claw Arcade keeps its halo as decoration (`web/DESIGN.md` §7).

## Acceptance

- Every phase in the contract maps to exactly one of the twenty states, every stage to its own,
  and no state can be reached from a raw string
  (`web/src/lib/theOrbDrawsEachPhaseAsOneOfItsTwentyStates.test.ts`); every phase has a status text
  of its own (`web/src/lib/workPhase.test.ts`).
- Every state places all 272 dots at every size and in both modes, still, moving and mid-change;
  every state's still frame is its own; a change lands exactly and its burst stays on the canvas;
  and done swirls only on an arrival that may (`web/src/ui/orb/halftone.test.ts`). The port was
  drawn beside the round-6 reference on the same clock, 488 frames across every state, size, mode
  and instant, and matched it to the pixel but for one pixel in two mid-change frames.
- With fake timers, a phase that ends before 150 ms never shows the orb, and a shown orb stays at
  least 300 ms, unless its turn ends without saying how, as a Stop pressed here does.
- No animation-frame callback runs at rest, with no turn running and no ending on show, under
  reduced motion, in a hidden tab, for the 12 px mark, or once a morph into a still state has
  settled (a `requestAnimationFrame` spy with a positive control that does see frames).
- Every phase is checked under reduced motion and high contrast, and with the orb hidden its text
  still names the phase for a screen reader.
- `npm run e2e:a11y --workspace=web` stays green.
- Snapshots of every state at 64, 20 and 12 px, light and dark, and under reduced motion: Settings ›
  Design's states, photographed by `web/e2e/visual.spec.ts` with the page's clock paused, so a
  moving state is the same frame every run.
