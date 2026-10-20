# The signals the buddy reads

The buddy is a new view of signals PersonalClaw already has, but three of them do not exist in one
place yet. Each is built once, for every surface that tells the owner what is happening: the chat's
thinking indicator, Mission Control, the tray, the menu-bar app, the phone companion, notifications
and the buddy.

## 1. One phase contract

**Today:** a chat turn sends one free-text status ("Thinking…"), and the chat guesses its phase from
words. Searching, planning and delegating are never classified on the server; listening never
leaves the desktop shell; waiting on the owner is signalled in six different ways; and "working" is
green in one view, coral in another, and "needs you" is blue for loops but orange for workflows.

**The contract** ([#3833]):

- One module defines the phases of any piece of work: `queued`, `thinking`, `searching`, `tool`,
  `planning`, `delegating`, `streaming`, `listening`, `approval`, `input`, `done`, `error`. Each
  phase has one meaning, one tone, one visual family and one line template, and every surface maps
  every phase.
- Four visual families carry urgency, and the line carries the exact phase. Chat, the tray, the
  buddy and the phone use the same mapping and the same owner-facing text, and colour and animation
  never carry the phase alone. Each surface's own looks (the agent orb's states, which the buddy's
  orb shares, and the tray's six still orbs) are variants inside the four families.

| Phase | Meaning | Family | Tone |
|---|---|---|---|
| `queued` | accepted, not started yet | active | open |
| `thinking` | started, no output yet | active | alive |
| `searching` | searching knowledge, the web or the files | active | alive |
| `tool` | running any other tool | active | alive |
| `planning` | making or revising a plan | active | alive |
| `delegating` | handing work to helpers | active | alive |
| `streaming` | writing its answer | active | alive |
| `listening` | push-to-talk capture is live | active | open |
| `approval` | waiting on the owner's yes | waiting | warn |
| `input` | waiting on the owner's answer | waiting | info |
| `done` | finished | quiet | ok |
| `error` | failed | blocked | danger |

The system's own states map too: idle is quiet, restarting is active and offline is blocked. Each
phase's line is in [`copy.md`](copy.md#one-line-per-state).

- The gateway classifies the phase and sends it as a structured event for each chat turn, loop,
  workflow run and subagent: `{work, kind, phase, line, since}`. The line is the server's own
  one-line description of what that work is doing now (the tool's label, the search's scope, the
  step's name).
- A turn that is accepted but not started yet is `queued`, so an idle chat's first turn is never
  invisible.
- Push-to-talk reports listening from every capture source (the composer, hands-free, the Knowledge
  recorder), and the shell passes it to the gateway and every window.
- One tone per phase across every surface: coral (the scheme's primary) for alive, warn for an
  approval, info for a question, danger for an error, ok for done. Two tones are still open:
  `queued`'s, and `listening`'s, because the microphone chip is the in-app half of the
  capture-safety indicator and recolouring it is the maintainer's call ([#3833]).

## 2. One list of what is waiting on the owner

Everything the owner must answer, from every source ([#3888]): tool approvals, an agent's
question, a loop's question, a workflow gate, a connected tool server's question, a browser grant,
a plan review and a bridge confirmation. One list route and one change event. Each item carries:

- its kind, the work it belongs to (a chat, loop, run, subagent or trigger id), and when it arrived;
- its brief, exactly as the app's prompt shows it;
- its answer route and the answers it takes;
- the exact action the owner can take, its consequence and its risk, and whether it is still
  current;
- whether it may interrupt now, decided by the gateway from mute, quiet hours and the notification
  rules, so every surface applies one policy.

The list keeps one order, consequence then age, and every surface keeps it: the buddy's
one-at-a-time alert queue, Home's Needs You, the tray and the phone. The compact and phone
compositions show it with text, never by the orb's colour or pose. An item that is answered
or expires leaves the list, and no surface keeps an action that could still send it.

What mute does to the buddy's alerts is still open. The design says what quiet hours do, and
[#3888] proposes carrying the posture itself (deliver, quiet, hush or drop) so a surface can tell
quiet hours from mute.

## 3. One list of what is running now

Every piece of live work ([#3889]): chat turns, loops (with their workers folded in), workflow runs,
subagents (with queued ones marked as queued), trigger fires and webhook turns. Each row carries
its kind, its name, its exact phase from the contract, its one-line "doing now", when it started,
when it last changed, a route into the work that every reader shares, and the id of anything
waiting on the owner for it. A cheap count read answers "how many, and is anything waiting" without
the full rows.

The four families may sum up a row's urgency, but its phase is always shown as text. Every reader
handles each state of the list: a large one, an empty one, reconnecting, work interrupted by a
gateway restart, failed work, and the just-finished tail.

## The doorbell

- One socket for the desktop: the main process holds it for the tray, the dock and the buddy, so
  nothing polls while connected.
- A burst of change events is coalesced into one read of the two lists.
- While the socket is down, the main process retries with backoff; the buddy's renderer holds no
  timer of its own.
- The phone companion and the web app use their own existing socket and the same two lists.

[#3833]: https://github.com/PersonalClaw/PersonalClaw/issues/3833
[#3888]: https://github.com/PersonalClaw/PersonalClaw/issues/3888
[#3889]: https://github.com/PersonalClaw/PersonalClaw/issues/3889
