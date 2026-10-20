# Work phases: what an agent is doing, said one way

What an agent is doing reaches every surface as one **phase** of a closed set, which
the gateway decides from what it measures and sends as a `work_phase` frame. No
surface guesses a phase from words, a timer or a flag of its own.

- Vocabulary: `src/personalclaw/work_phase.py` (gateway) and `web/src/lib/workPhase.ts`
  (web), pinned equal in both directions by `tests/test_work_phase_vocabulary.py`
- A chat's phases: `src/personalclaw/dashboard/chat_phase.py`, fed by the chat runner
  (`dashboard/chat_runner.py`), the approval registry and the owner's questions
- Work references: `src/personalclaw/workref.py`
- Design: [the phase contract](../design/buddy/signals.md#1-one-phase-contract), each
  phase's line in [copy](../design/buddy/copy.md#one-line-per-state), and the agent
  orb's [phase to state](../design/agent-orb.md#phase-to-state) table

## The vocabulary

Each phase has one meaning, one tone, one of four families and the copy row its line
is written from. The line holds the state only; a surface adds the work's name and
cuts the line to its own room.

| Phase | Meaning | Tone | Family | Line |
| --- | --- | --- | --- | --- |
| `queued` | accepted, not started yet | primary | active | Waiting to start |
| `thinking` | started, no output yet | primary | active | Thinking |
| `searching` | searching knowledge, memory, the web or the files | primary | active | Searching your knowledge |
| `tool` | running any other tool | primary | active | the tool's own label |
| `planning` | making or revising a plan | primary | active | Planning |
| `delegating` | handing work to helpers | primary | active | Handing off to 2 helpers |
| `streaming` | writing its answer | primary | active | Writing |
| `listening` | push-to-talk capture is live | warn | active | |
| `approval` | waiting on the owner's yes | warn | waiting | Needs your OK: shell |
| `input` | waiting on the owner's answer | info | waiting | Asks: Which branch? |
| `done` | finished | ok | quiet | Done in 12 seconds |
| `error` | failed | danger | blocked | Stopped: the reason |
| `retrying` | a retry or a fail-over is pending | primary | active | |
| `waiting_model` | held behind other calls on a local model | primary | active | |
| `idle` | nothing queued or running | neutral | quiet | (empty) |

**Tones.** Live work is the scheme's primary (coral), never a colour of its own.
Waiting on the owner's yes is warn and on her answer info, a finish is ok, a failure
danger, and nothing running carries no accent. Listening keeps warn, which matches the
system's own microphone dot.

**Families** carry urgency: active, waiting, blocked and quiet. Each surface's own
looks are variants inside them, and the line always carries the exact phase, so no
surface shows a phase by colour or motion alone. The gateway's own states map too:
idle is quiet, restarting active and offline blocked.

No producer sends `listening`, `retrying` or `waiting_model` yet; they are in the
vocabulary so every surface already has their looks.

## The frame

`work_phase` is one frame type for every kind of work. For a chat its payload is:

| Field | Meaning |
| --- | --- |
| `work` | `chat:<session>`. A work reference is `<kind>:<id>`, with the kinds `chat`, `loop`, `workflow`, `subagent`, `trigger` and `webhook`; `workref.parse` refuses any other, and reads the names a trigger's fire writes (`tests/rails/test_every_work_id_has_a_known_writer.py`) |
| `session` | the chat key, so an app's socket hears only its own conversations' phases (`dashboard/ws_state.py::frame_subject`) |
| `kind` | `chat` |
| `phase`, `line` | the phase and its line, redacted and capped at 160 characters |
| `since` | when the phase or its line last changed |
| `turn` | `{message_id, started_at}` of the running turn (the row that started it), `null` when nothing runs |
| `last_step_at` | the turn's latest runtime event: a streamed chunk, a tool starting or ending, a decision raised or answered |
| `stopping` | a stop was asked and the turn has not ended; never set in a frame with no turn |
| `steerable` | the running turn takes a steer |
| `last_outcome` | how the latest settled turn ended: `complete`, `stopped`, `error` or `interrupted` (`chat_runner.terminal_outcome_for_turn`) |
| `queued_messages` | how many messages wait behind the turn |
| `seq` | the stream stamp (`DashboardState.next_stream_seq`), so a page applies a frame only when it is newer than its snapshot |

Times are UTC instants. A frame is sent when the phase, its line or one of the other
fields changes, never per streamed chunk.

## How a chat turn says its phases

`run_chat` runs inside one wrapper, `_says_its_phases`: it begins the turn's phase
before the turn awaits anything and ends it on every way out, an exception and a
cancellation included. Nothing runs the turn engine around it
(`tests/rails/test_a_chat_turns_body_runs_only_inside_run_chat.py`).

- `queued` as soon as the turn is accepted, before its context is put together, so a
  first message on a new chat shows at once.
- `thinking` when the model's stream starts, or `planning` in the chat's plan mode.
- On each tool call, by what the call is (`work_phase.classify_call`): an agent CLI
  declares a kind per call, so a `search` is searching under its own label, a `think`
  is planning, and any other kind is a tool labelled as its card is. PersonalClaw's
  own runtime declares none, so its search tools are known by name and say where they
  look ("Searching your knowledge", "your memory", "the files", "your tools", "the
  web"); any other call is a tool.
- `streaming` once, on the first word of the answer after any other phase, and
  `thinking` again after a tool's result.
- `approval` while the approval registry holds a request for the chat, and `input`
  while one of the owner's questions for it is open. Both come from the registries,
  so native and agent-CLI turns are covered in one place.
- `delegating` while helpers the chat started are running, with their count; back to
  thinking when the last one ends. A helper that outlives its turn holds nothing.

**Endings.** Every way a turn ends sends one last frame with no turn in it:

| How the turn ended | Phase | Line |
| --- | --- | --- |
| complete (an answer, or a command answered by the chat itself) | `done` | Done in 12 seconds |
| stopped (her Stop, a move, a stop before its prompt) | `done` | Stopped |
| declined (her Deny, and the agent ends the turn) | `done` | the decline's own sentence |
| error (a failure, a turn past its time limit) | `error` | Stopped: the turn's own error |
| interrupted (a restart or a shutdown cut it short) | `error` | Stopped: The gateway restarted before this reply finished. … |

A retry of the same turn (its connection was lost before any step) is not an ending:
the turn goes on in its retry. `tests/test_every_way_a_turn_ends_sends_the_final_phase.py`
walks every exit, anchored on the outcomes `terminal_outcome_for_turn` can give.

## Liveness

While a turn runs, the gateway sends its chat's `work_phase` again every 15 s, with
the phase unchanged and `last_step_at` current, as a frame that carries data. A turn
whose model is silent still says it is running. It is one task per running turn
(named `work_phase:<chat>`), cancelled where the turn's last frame is sent.

## The routes serve the last frame

`GET /api/chat/sessions` rows, `GET /api/chat/sessions/{session}` and the chats of
`GET /api/projects/{id}/linked` carry the chat's `run_state` (`_ChatSession.run_state`):
the last `work_phase` payload sent for it, served with `phase: "idle"`, an empty line
and no turn once nothing runs. A chat that never ran here is served the idle payload.
A page that opens or reconnects mid-turn reads the same state the frames say, and a
chat's running, stopping and steerable state, whether it waits on her OK, and how its
last turn ended, are read there and nowhere else: the routes' own flags for them are
gone (`web/src/lib/noSurfaceDerivesRunning.test.ts`). The chat page's stall
reconciler, which recovers an approval card whose frame it lost, reads the `approval`
phase.

## Who reads what

- **The chat's own turn view** reads its turn's frames directly, as it reads the
  turn's other frames, applying one only when its `seq` is newer than the snapshot's.
  The head of the running reply shows them: the agent orb in the phase's look and the
  phase's line beside it, settling on the ending the last frame says
  ([agent-orb.md](../design/agent-orb.md)).
  So do the loop and code cockpits for their worker's turn: their list of its steps
  gains a phase's line once per change, but none for a phase the list shows its own
  way (a step's, which its row says, and in the code cockpit the writing, whose words
  it shows), nor for the thinking said after each step's result
  (`workPhase.phaseFeedLine`).
- **Every ambient surface** (Mission Control, Home, the agent worlds) treats the frame
  as a doorbell and reads the phase from the chat list
  ([agent-activity-feed.md](agent-activity-feed.md)).
