# Chat & Sessions

How a message becomes a turn: the session model, the dashboard chat pipeline,
persistence, and the memory-privacy modes. Paths are relative to
`PersonalClaw/src/personalclaw/`.

## Session model

A session is one conversation thread, whatever surface it lives on (dashboard
chat, channel thread, loop worker, webhook, subagent).

- **`session.py` — `SessionManager`.** Owns live session state. Each session
  has a FIFO message queue (`deque` of pending messages) guarded by a
  semaphore, so messages arriving on the same channel thread are serialized —
  a turn finishes before the next queued message starts.
- **`session_map.py` — the persistent session↔thread map.** Stored at
  `~/.personalclaw/session_map.json` (atomic tmp+rename writes). Each entry
  carries `sid`, `thread_ts`, `channel_id` and, for a linked session,
  `channel_provider` (the channel the thread is on) — generic keys, no
  channel-vendor shape assumed. `set_channel_link` / `get_channel_link` /
  `get_channel_provider` are the one API for linking a dashboard session to a
  channel thread; a reverse index maps
  `thread_ts` → session key (`get_session_for_thread`). It is the ONE place a
  link is kept: nothing holds a copy of it in memory, so a restart loses none.
  A changed entry is written last and the index is rebuilt in file order, so
  the session that linked a thread most recently is the one it continues,
  before a restart and after it.
- **`session_restrictions.py` — memory modes.** Two restriction registries,
  kept in core because any surface can request them:
  - **temporary** — blank-slate thread: memory READS suppressed
    (`blocks_reads`) *and* writes suppressed, and the chat is forgotten when its
    session ends (below).
  - **incognito** — ephemeral: memory WRITES suppressed, reads allowed. Its
    transcript is kept, out of the chat list and search.
  - **unreadable** — work handed on from a chat whose mode nothing can say:
    reads and writes suppressed, as for a temporary session, and what it is
    refused says that the chat's setting cannot be read.

  `is_restricted()` (either mode) gates the after-turn learning path, session
  listing/search, and memory recall (see
  [knowledge-memory.md](knowledge-memory.md)). Restricted sessions never write
  lessons (`after_turn_review.py` checks `session.is_restricted`).

  Work for a restricted session takes its mode: a subagent's key is marked when
  it is spawned (`memory_writes.hand_on`), Temporary when the chat it works for is
  Temporary, unreadable when nothing can say what that chat is, and Incognito
  otherwise, and a workflow run marks its origin's mode on its keys and records
  it on the run, as do a subworkflow it starts and a fork of it
  (`ownership.inherited_extra`). The mode handed on is read by the one reader of a
  session's mode (`memory_writes.session_mode`), the live chat first, together
  with the mode of the work that starts it (`ownership.inherit_mode`): a chat's
  transcript is written when its first turn ends, so the live chat is all that
  says what a chat is when that turn starts a run or a subagent. So a Temporary
  chat's subagents, its subagents' subagents and the steps of a run it started
  read no memory and write none, as the chat does, from its first turn on. It
  takes the chat's model the same way (below). Whichever door its call reaches
  the workflow engine through (the workflow routes, which an agent CLI's tool
  server and a batch `subagent_run` call, or a native agent's workflow tools in
  the gateway), such a chat's call starts a run or a batch and changes only a run
  it started; anything else it asks is refused, saying why, by the one rule both
  ask (`workflows/restricted_calls.py`).
- **`memory_reads.py` — whose work may read your memory.** `reach_of(state,
  key)` is the one answer every memory read asks, for the work a session key
  names: it follows a subagent to the session it works for, an app's agent run to
  its app and a workflow step to the chat that started its run, and reads each
  one's mode as `memory_writes.session_mode` does: the live chat, the registry,
  its transcript and the run, in that order. A mode nothing can say (a record
  that cannot be read, a value this build does not know, a chat the gateway
  does not hold that nothing records, as for a Temporary chat that has ended)
  reads nothing, as a Temporary chat's work does, and says why. Two
  kinds of work read none of your memory: a Temporary chat's, and an app's that
  does not hold the `memory` permission (a conversation the app started, an agent
  run it asked for, an agent its scheduled job started, every agent working for
  any of them). An agent a job's fire started carries the job's id
  (`SubagentInfo.trigger_id`), and so do the job's own session (`cron:<id>`) and a
  run it started, so the app is found by it (`app_crons.app_of`). The context a
  turn, a subagent's first prompt and a job's announce turn are assembled with,
  active recall and the push reflex, `memory_recall`, `memory_list`,
  `triage_rules_list`, `get_context`'s memory tier and the Learning page's facts
  all ask it, and a refused read says why (`Reach.refusal`). A turn that read none
  says so in its details: its context line is `context_without_memory`
  (`memory_reads.fed`), kept on its answer for a reload.

  The same grant is what lets an app's work change your memory. The write scope
  (`memory_writes.derived_from`) names the app whose work it is: the turn's
  (`runs_as_its_session`), a request's (the memory-write middleware, asking
  `reach_of` only when the request writes), a consolidation pass's (the app its
  transcript records). Without the grant the memory store refuses the change
  (`check_memory_statement`, and the markdown memory files through
  `refuse_memory_write`), the lesson and triage-rule routes refuse first with the
  app's reason, the learning gate denies every cadence (`app_without_memory`) and
  the consolidator runs no pass. With it, what the work writes names the app
  (`written_by`).

  A change to memory that a subagent's or a workflow step's request makes is
  judged the same way, as the work it does for the chat at the top. The routes'
  guard of a memory change (`_is_restricted_session`, and the lesson route's own
  check) refuses it when any session up the chain keeps nothing
  (`Reach.restricted_mode`), whatever the caller's own key is marked, and what it
  writes is filed under that chat (`memory_writes.filed_under`, found by the same
  walk the middleware asks for the app). So a subagent's `memory_remember` saves
  its lesson where its chat keeps memory, filed under the chat, and is refused in
  the words every refused memory write uses (`memory_writes.REFUSAL`) where the
  chat is Incognito or Temporary or nothing can say what it is.

  The agent's own search of your chats (`chat_search`, served by
  `GET /api/sessions/recall` over `chat_recall.py`) runs the same
  `session_search.search`, so it never finds a restricted chat either. It also
  leaves out the chat the call is made for (and that chat's older files, one
  `tab_id`), and for a Temporary chat's work it searches nothing, by the same
  `memory_reads.reach_of`: a subagent's or a step's call is judged by the chat it
  works for, up its parents, and an app's work finds only that app's
  conversations. It hands back a few chats, a few turns of each and a window of
  each turn, masked, and quoted as data (`fence_untrusted`).

  A channel's mark is recorded in the thread's transcript metadata
  (`ConversationLog.append`), so a restricted thread is still restricted after
  a restart, when the in-process registry is empty.
- **`memory_writes.py` — a restricted session keeps nothing, by any path.**
  An incognito or temporary session writes nothing to long-term memory
  (working, episodic or semantic records, persona notes, commitments, lessons,
  knowledge, vocabulary, the markdown memory files), and nothing from it is
  embedded. Enforced at the stores, not by each caller:
  - `blocks_memory_writes(key, memory_mode=)` is the one answer, keyed on the
    mode `session_mode` reads: the one reader of a session's mode, which the
    reads, a run's inheritance, a subagent's hand-off, the routes' restricted
    session guard and the learning gate ask too. It reads every record of the
    mode, the live chat first (what the caller or the current work holds, or the
    chat the gateway holds), then the registry, the transcript and a step's run,
    and the strictest record wins. It fails closed: only a mode known to be
    `persistent` keeps memory. A record that cannot be read, an unknown mode, and
    a dashboard chat the gateway does not hold that nothing records keep
    nothing.
  - `derived_from(key)` names the session work derives from. It is set by the
    turn (`chat_runner.run_chat`) and by a side question asked beside the chat
    (`dashboard/side.py`), both as the chat's own work (`as_its_session`), by
    every consolidation pass
    (`HistoryConsolidator._consolidate` / `consolidate_session`) and by every
    API request that names a session in `X-Session-Key`
    (`dashboard/memory_write_gate.py`), and it follows the work into the tasks
    it spawns, `asyncio.to_thread`, and every worker pool, each of which is a
    `ScopeCarryingExecutor` (the gateway's default executor too). A plain pool
    drops it: a memory read bounded by a timeout once ran on one, and an
    Incognito chat's message reached the embedding model from its worker.
  - Inside a restricted scope the memory, knowledge and vocabulary databases
    refuse every statement that changes them (`SharedConnection.statement_check`)
    and `MemoryStore._persist` refuses the markdown files. Reads still work, by
    keyword, and leave no mark (no recall counts, access stamps or volunteer
    log). The API answers a refused write `403`.
  - The markdown memory files are also files in the folder every chat starts in
    (`<home>/workspace`), so the agent's own tools reach them, and they hold the
    same line. The memory folders (`memory.memory_folders`: the home's `memory`
    folder, with preferences.md, projects.md and the daily history, and `_ext`,
    every working folder's memory) are changed by no tool in work that may
    change none of your memory (an Incognito or Temporary chat's, an app's not
    given your memory). `write_file` and `edit_file` refuse the change before
    anyone is asked to approve it (`file_scope.memory_kept_from_work`, in the
    one check every file tool makes), in the memory-write refusal's sentence
    and under its code (`restricted_session_block`, `app_memory_not_granted`),
    and tell the agent nothing was written. The shell refuses a command that
    names a path there and does more than read it (`file_scope.memory_named_in`),
    and the OS sandbox keeps the folders read-only to every command started for
    such work (`sandbox._memory_fence`), whatever the command says. An ordinary
    chat's write of a memory document, and the owner's save of one in Files,
    is the document's store's own write (`memory.write_document`), under the
    documents' lock and indexed. What this does not hold is in
    [limitations §19](../security/limitations.md#19-a-private-chat-is-kept-out-of-the-memory-folders-not-out-of-every-store).
  - Nothing of a restricted session reaches a model but the one its turn runs
    on. `model_may_read(ref)` is the one answer to "may this work hand what it
    carries to that model": the turn names its model once its runtime is built
    (`answered_by`, in `run_chat`), and inside such work every other model is
    refused (`OtherModelRefused`, before anything is sent). Every seam that
    reaches a model asks it: the embedding functions (`embed_fn_for` /
    `embed_many_fn_for`, and `VectorMemoryStore._try_embed` for a function
    pinned on a store), so such a session's memory is searched by keyword and
    its tools are ranked by their words: a turn whose catalog must be ranked
    (`agents/native/tool_retrieval.py`), a subagent's turn and a side question's
    alike, as with no embedding model bound, and `tool_search` too;
    `provider_bridge.metered`, the guard every model built for anything but a
    person's own turn passes (a tool's model, a subagent's, a knowledge node's,
    a loop's), and an agent CLI built for such work; the image reader
    (`image_input.resolve_image_reader`); the image and video tools
    (`mcp_artifacts`); and the models a turn falls back to
    (`ModelFailover.admits`). A one-shot call such work makes
    (`one_shot_completion`, e.g. `web_extract` or `visualize`) runs on the
    chat's own model: the model-call log names that model as the one that
    answered, and the call is stamped as serving in the bound model's place, so a
    call log that captures it reads "ran on <chat model> instead of <Reasoning
    model>: this chat is Incognito, …". A tool that needs another kind of model
    says it cannot run in this chat. A restricted chat whose own model cannot run is not
    sent to the chain's next one: the turn ends saying why
    (`chat_runner.refused_on_a_substitute`). An agent CLI runs PersonalClaw's
    tools in a process of its own (`personalclaw mcp-core`), which no context
    variable reaches, so that server runs each call as the chat it serves
    (`mcp_core._call_as_its_session`): it asks the gateway what the calling
    session is (`GET /api/chat/sessions/model-reach`, answered under the same
    scope the gateway's stores use), once per session, and runs the call as
    deriving from it when it keeps nothing; a session the gateway cannot answer
    for is taken to keep nothing. What the person gives such a chat in a form
    its model cannot read is the one exception, and the chat's notice says so: a
    file they attach, read for its text, and a screen they share, described, are
    read by the models set up for them (`reading_their_input`, which changes only
    which model may read and nothing the work may write). Dictation and
    read-aloud are the page's own requests, made as the dashboard rather than as
    the chat. `tests/test_model_reach_census.py` holds the tree to all of it:
    every pool carries the scope, every seam asks, the agent CLI's tool server
    serves each call as its chat, the exception is taken only where it names, and
    nothing builds a model provider or calls an embedding model around them.
  - The work such a chat starts away from its own turn stays on that model too,
    handed on with the mode. The turn records its model for its session
    (`answered_by`, read by `model_of`; an agent CLI's chat names its runtime,
    `acp:<cli>`, `provider_bridge.turn_model_ref`). A request its agent's tool
    makes runs as the chat on it (`as_work_of`, in `memory_write_gate`), so a
    subagent `subagent_run` starts is handed it when it is spawned (`hand_on`)
    and its runtime is built on it rather than on the Orchestration chain
    (`spawn_model`, `subagent_session.session_kwargs`), on the chat's own agent
    CLI when the chat runs on one. Each subagent runs as its own work
    (`work_context`): a queued one that starts when another agent ends stays on
    its own chat's model, not the other's. A workflow run records the model
    beside the mode it inherits (`ownership.run_model`) and its tick loop runs as
    that work from the record (`run_start.run_context`), so a batch's or a
    workflow's steps keep the mode and the model after a restart, when no request
    or mark is left to say them. A start that cannot run on the chat's model is
    refused before anything is built, in words: one naming another model "…:
    <model> was not asked.", one whose chat's model is not known "…, and this work
    was not told which model that is." A side question asked beside the chat
    reads its conversation, so it is the chat's own work as a turn is
    (`as_its_session`): it names the model it is answered on, the one the chat's
    own choice of model builds, and nothing of the question or the conversation
    reaches any other model.
  - A consolidation pass over a restricted session is skipped before its
    transcript is read or a model is called: the idle sweep's expiry, a
    channel's end of session, `personalclaw consolidate`, the consolidate
    request and the per-turn and idle passes all end in `_consolidate`.
  - Every record the memory store writes is stamped with the session it came
    from (`source_session`, `memory_writes.filed_under`: what a subagent's or a
    step's request writes names the chat it works for), and at start the gateway
    removes every record a restricted session left
    (`forget_what_restricted_sessions_left`): by that
    stamp, an episodic row's `conversation_id`, a `consolidation:<key>` source or
    a session-scoped row's `scope_ref`, with its history events, links and
    vectors. A record that names no session (a persona note or a lesson an
    earlier version wrote) cannot be traced and is left for the owner to review.
  - Nothing of a restricted session is handed to a background model, a model
    its own turn did not ask for. `blocks_background_models(key, *aliases,
    memory_mode=)` is the same answer, and work inside a restricted scope hands
    nothing on whichever session it names. The chat is titled by its mode
    ("Incognito chat", "Temporary chat") on its first turn and when a title is
    asked for again (`dashboard/chat_title.py`), and gets no model tags or
    follow-ups (`dashboard/chat_followups.py`); a reopened chat whose history
    does not fit is cut to fit rather than condensed
    (`context.compress_thread_history`); the suggestions built from recent chats
    leave it out (`suggestions.py`); no agent-routing suggestion is worked out
    for a message sent to it, which would embed the message outside its turn
    (`agents/routing.suggest_for_send`). The batch re-tag, organize proposals and
    background compression already leave restricted chats out of what they list.
    The one way a chore reaches a model (`chores.run_chore`) keeps the rule
    itself, so a caller that did not ask first still hands the chat to no model:
    a chore for such a chat (read by the key its spend is recorded under and the
    mode its caller holds) or made in its work runs on the chat's own model only
    in the chat's own turn, once the turn has named it, and is refused
    (`OtherModelRefused`) anywhere else, before anything is sent.
  - Nothing of a restricted session is left behind in work that lasts after it
    (`lasting_work.py`). A loop or project, an automation or scheduled task (a
    lifecycle trigger among them) and a callback are kept after the chat and run
    later as a session of their own, on their own model, which keeps what it
    does: nothing in the loop engine or the trigger store reads a chat's mode. So
    work that derives from such a session (`writes_refused`) makes none of them,
    hands none its words and sets no loop going. Each is refused at the one place
    every door to it reaches, before anything is written: a new loop
    (`loop.store.create`), a start, a resume or a steer (`loop.manager.start`,
    `nudge`; a merge or conflict answer resumes its loop, so it is refused before
    the answer is written), a new or changed automation (`triggers.tools.create`,
    `update`, and `hooks.ScriptHookStore` for a lifecycle trigger) and a callback
    (`webhook_callbacks.register`). The doors are the native agent's
    `project_run_*`, automation and `hook_register` tools, the same tools in the
    tool server an agent CLI runs, the tool route (`POST /api/tools/invoke`) and
    the loop and trigger routes. A tool says so before anyone is asked to allow
    the call and a route answers `403 restricted_session`, each in a sentence
    that says why and to do it from an ordinary chat, with an audit row.
    Reading such work, pausing, stopping or deleting it, switching an automation
    off or back on, and running one now (a run held as the chat's own work) are
    unchanged, and a workflow run the chat starts keeps its mode and its model as
    above, a General loop started through the loop door included.
- **`chat_traces.py` / `dashboard/chat_forget.py` — what a chat keeps on disk,
  and forgetting it.** A chat leaves its transcript, its working folder
  (`sessions/<key>/`), its turn checkpoints and the files attached to it
  (`uploads/`, `screenshots/`); `chat_traces` reads that and `chat_forget` deletes it.
  `purge_chat` deletes them for the Delete button (which keeps a kept chat's
  uploads, since Files lists them) and for a **temporary** chat's end, which
  takes its attachments too. A temporary chat's session lives in the gateway
  running it, so it ends when that gateway stops or restarts, when the chat is
  deleted, and when cleanup evicts it as inactive: the last save before a stop
  forgets each one instead of saving it, the next start forgets any a crash or
  a restored backup left behind before it restores anything, and a read or a
  send naming one that is not running here forgets it and answers 404. While
  it runs its transcript is written like any chat's (a reload keeps it); no
  snapshot or shard export copies it (`chat_traces.kept_by_temporary_chats`).
- **`session_workspace.py` / `session_pid.py`** — per-session working
  directory resolution and process-id tracking.

## History & persistence

- **`history.py`** — one JSONL file per session at
  `~/.personalclaw/sessions/{safe_key}.jsonl`. **The file is the user's
  record and nothing shortens it**: there is no size cap, no rotation and no
  background rewrite, at any size. What bounds cost is what a reader takes —
  `recent()` and `history_for_model()` return a window.
- **Background compression shortens what the model reads, never the file**
  (`bg_compress.py`). An idle chat gets a derived record beside its
  transcript, `{safe_key}.summary.json`, naming the span it covers — counted
  in turns (`summarized`, `reduced`), so it applies to the file and to a
  resident session's buffer alike — and a digest of that span. `model_view()`
  is the one transform: while the digest still matches, the model reads the
  summary in place of the oldest turns and the next ones capped; the moment a
  turn in the span changes, it reads the turns as written. The dashboard applies
  it to the turns a fresh runtime is handed (`prior_turns_transcript`); a reader
  with no live session reads `history_for_model()`. `model_window()` is the one
  message budget both use, and it keeps a summary first. Deleting a chat
  deletes its record, and the pass drops any record whose transcript is gone.
- **`sessions/archive/`** holds lines EARLIER versions trimmed out of chats
  (2 MB rotation and the old background rewrite). Nothing writes it and nothing
  prunes it — for those chats a batch can be the only copy — and neither the
  startup/shutdown workspace sweep (`cleanup_stale_sessions`) nor any write
  path touches it; a batch is removed when its chat is deleted. Archive *reads*
  are redacted through `redact_credentials` / `redact_exfiltration_urls` before
  anything leaves the store.
- **`resolve_history_key()`** resolves whether a bare key is a channel-thread
  key or lives in the `dashboard:` namespace *by asking the store* — core
  assumes no key shape and names no provider.
- **A new chat's name is one no chat has, and a save never replaces another
  chat's transcript.** A new chat is named `chat-<number>-<second>` in
  `dashboard/chat_names.py`, the one place a new chat is named: every surface
  that opens one (the dashboard, a Temporary or Incognito chat, a channel's new
  thread, a fork, an investigation, a delivered result) asks
  `get_or_create_session` for it with no name. The number carries on past the
  highest one a kept chat has, and a name that a chat open here or a transcript
  kept on disk (an archived one included) holds is stepped past, so neither a
  restart in the same second nor a clock set back across one names a new chat
  after a kept one. Names already given stay as they are. A transcript records
  the `tab_id` of the chat it is; a session minted for a kept chat takes it on
  (`get_or_create_session`), and `save_session_to_history` refuses, and logs, any
  save, `force` included, whose session's tab id is not the one the transcript
  records: two gateways on one home that opened a chat in the same second before
  either saved, or a save still on its way for a chat deleted and named again.
  `POST /api/chat/sessions` given a kept chat's name opens that chat, so
  `personalclaw run --session` continues its conversation.
- **A chat opened again by its name continues it.** `get_or_create_session` is
  the one place a session is made, and a name whose transcript is kept on disk
  (the file the chat's saves rewrite, whatever its first line says) makes that
  chat: `chat_persistence.continue_kept_chat`, the one loader, gives it every
  field of its record a save writes back and its whole transcript before the
  chat is open here. What the caller asks for (an agent, a model, a folder) shapes
  a new chat only, as for a chat already open, and a memory mode other than the
  kept chat's is refused. The ways a chat comes back (the start's restore, opening
  a chat from the list, resume) and the callers that name their sessions all go
  through it. Each way back used to restore its own part of the record, and the
  callers that name their sessions none of it, so a session made blank for a kept
  name was saved over the transcript: a loop worker the watchdog re-armed after a
  restart lost every earlier turn at the stop. A Temporary chat that is not
  running here has ended, so its name makes a new chat. A kept chat whose record
  or transcript cannot be read is still opened (`open_kept_chat`), since a disk that misbehaves locks
  nobody out of a chat that is there, but it holds none of what is kept, so
  `save_session_to_history` refuses, and logs, every save from it (`force`
  included) and what is kept stays as it is; it is not opened as a Temporary chat,
  whose end would delete the transcript. Each named worker session
  is one ongoing conversation by what its feature does, so after a restart each
  continues, and its model is given its earlier turns through the history a fresh
  runtime is handed (`prior_turns_transcript`; an agent CLI that can load its own
  session by the kept id loads that instead):
  - a loop's stage worker the watchdog re-arms (`loop/manager.start`): an
    interrupted loop resumes. The cycle prompts are not among the turns handed
    back, as for any chat (they are `nudge` rows); the cycle in flight carries the
    current one.
  - a loop's task worker the scheduler spawns again
    (`loop/manager.spawn_task_worker`): it carries on with its one task, in its own
    checkout.
  - a loop's planner (`planning/runner.run_planner_pass`): one session across its
    loop's passes, whose retry tells it to use what it found already.
  - a client of the OpenAI-compatible endpoint registered to keep its
    conversation (`inbound/openai_dialect`). A client that keeps none is let go of
    its earlier requests before each turn (`_reset_session`), and its model is given
    none of them.
  - a schedule's chat (`dashboard/schedule_inject`): filled from the schedule's own
    conversation only while it is new, and threaded each result after that, as
    while it stays open, so its runs are not poured into it again as turns of
    their own.
- **`dashboard/chat_persistence.py`** — the dashboard-side persistence
  contract over the JSONL store (message append, metadata, variants).
  Model-to-provider matching is data-driven via
  `catalog.model_family_provider_types(model)` — no vendor names at the call
  site, and unknown model families are never restricted.
- **The transcript buffer is the whole file.** `save_session_to_history`
  rewrites a session's file from `_ChatSession.messages`, so the buffer holds
  every message the file holds and nothing trims it. Every chat made for a kept
  transcript is loaded through `_seed_transcript` (in `continue_kept_chat`), which
  loads the whole file with each line's `cls` and
  `meta`. The buffer holds transcript entries only: a streamed answer is ONE
  `streaming` entry however many chunks it arrives in (`stream_chunk`), settled
  in place into an `assistant` entry (`finish_stream`), and the end-of-turn
  marker goes to live readers only (`signal_done`). An approval is written once
  it is decided. The save records `message_count` in the metadata line, which
  `ConversationLog.list_sessions` serves as the chat list's count.
- **Where each line came from is the line's own, and a save never writes one**
  (`turn_source.py`). A line records the thread it arrived on, who sent it there
  and, when a chat channel took it in, which channel (`source_thread`,
  `source_user`, `source_channel`), the same fields in the buffer and the file,
  written once by the code that takes the line in. The dashboard's own chat
  records `dashboard` for the thread and the sender: what you type there, and the
  rows the chat adds itself (its answers, a tool's card, a notice). The inbound
  door records a channel message's thread, sender and channel
  (`channel_inbound._route_to_session`), on its queued item too, so the row it
  runs as records them. A channel that runs a conversation itself writes its
  turns with theirs (`save_conversation_turn(…, source_channel=…)`), and the chat
  open for that conversation takes each turn as written (`take_channel_turn`).
  The OpenAI-compatible door records the conversation and the client that sent
  the message. A copy carries the line's own: the chat loaded from its file
  (`_seed_transcript`), a fork, a rewound tail; a row that several queued
  messages run as records the source they share, or none when they came from
  different places. `save_session_to_history` writes back what each line
  records. It used to write `dashboard` on every line, so once the dashboard
  saved a channel's chat, the channel's turns read as typed in the dashboard.
  What memory takes as your own words reads these fields
  (`turn_source.sent_by_owner`, see
  [knowledge-memory.md](knowledge-memory.md)): a line is yours when the
  dashboard took it in, or when its sender is the owner its channel keeps. What
  `web_fetch` may open, and a channel conversation's Trust and approvals, are
  decided from how a message was taken in (in process), from its fence, or from
  the chat's own link. The agent's `chat_search` names a channel turn's sender
  from `source_user`, and `GET /api/sessions/{key}` serves the lines as written. A
  line saved before lines recorded their source keeps the `dashboard` it was
  given: nothing else recorded where it came from. A channel's line saved before
  lines named their channel names a sender and no channel, and memory reads it as
  nobody's.
- **A reload shows the turn the live page showed.** The page builds a turn's
  steps from the gateway's frames (`web/src/pages/chat/liveToolFrames.ts`) and a
  reload rebuilds them from the persisted rows (`hydrateTurns`), so what one
  shows the other must. A call's row carries its call's id; a `tool` row without
  one, in a turn the gateway ran, is a line it wrote ABOUT a step, which neither
  the live page nor a reload draws as a step (`foldStepLine`). A refused
  approval's line puts the agent's option the refusal was sent as on the
  approval's own line. A line about what happened to a call (a gate refused it
  before it ran: the chat's task mode, the shell denylist, a hook, an unattended
  run's bounds, a tool name that does not validate; or it keeps failing the same
  way, the loop breaker) names the call in `meta.about_call` and carries its
  sentence in `meta.note` (`dashboard/step_notes.py`), and the sentence is said on
  that call's card, live from the row's frame and after a reload from the row; a
  line about a call the turn shows no card for is said on the turn. An imported
  conversation's call lines carry no ids at all and stay its calls. A call an
  agent CLI ran without asking is said on its own card
  (`docs/architecture/security.md`), never on a row of its own.
- **Who started a conversation.** `_ChatSession.created_by_app` is the app whose
  token started it, or empty for yours, and it is the one thing an app's reach
  into a conversation is decided on (`apps/permissions.ROUTE_AUTHZ` rows that
  carry `owns`, enforced by `dashboard/server.py::app_permission_middleware`).
  It is set only from a verified app identity, written to the metadata line as
  `created_by_app`, and read back in `DashboardState.get_or_create_session`, the
  one place every restore path mints a session, so a restart keeps an app's
  conversation the app's and never makes one of yours an app's. It is not
  `_app`: that is an origin tag (`loop`, a channel's provider name, an app's
  name) saying where a conversation came from, which the chat list groups by and
  its turns are counted under, and an app can share its name. It does not say
  where a chat answers: that is its channel link's (see *Channel-linked
  sessions*). A turn in an app's conversation needs the app's
  `agent` permission at the `tools` tier, and runs under it, never under your
  approval switches, whoever sends the message: the app approves none of its
  calls, so each one that needs approval asks you (`chat_runner.started_by_app`).
  The same field decides what an app
  READS: a detail, map, export or tool result of any other conversation is
  refused before the handler loads it; the chat list, `/api/sessions` and the
  content search leave out every conversation it did not start; and the
  websocket drops a frame about one (`dashboard/ws_state.py::frame_subject`).
  The chat list and the detail carry `created_by_app` and `created_by_app_name`,
  so your history says "Started by <app>", and the composer says what a message
  you send there runs under. A folder chosen for an app's conversation is not
  added to your recent projects.

## The dashboard chat pipeline

`dashboard/chat_runner.py` is the turn engine. A turn flows:

1. **Prompt-mention expansion** — a leading `@name key=value` expands a saved
   prompt via `_expand_prompt_mention` (user prompts live at
   `~/.personalclaw/prompts/`, snippets at `prompt_snippets/`; the composer's
   @-menu suggests prompts only at message start). The rest of the message fills
   the prompt's own inputs, the way the tool it was imported from passes them:
   the whole text fills `{{arguments}}` (unless given as `arguments=…`), and its
   words fill `{{arg1}}` to `{{arg9}}` in order. Text a prompt has no input for
   follows the rendered prompt.
2. **Context assembly** — `context.py` (`ContextBuilder`) builds the system
   context: the runtime values `{{bot_name}}` and `{{user_name}}` (live-resolved
   from `agent.bot_name` and `dashboard.user_name`, Settings → Account), memory
   context, and — for channel-linked sessions — the `channel-thread-context`
   snippet. A fresh runtime is restored ONLY from the session's own turns before
   the one being sent (`chat_persistence.prior_turns_transcript`) — never the
   in-flight message, never another session's transcript — with the chat's
   background summary standing in for its oldest turns while it still describes
   them (`history.model_view`). `context_engine.py` and
   `context_compaction.py` manage sizing and compaction. The native loop
   compacts its own history at the Settings threshold
   (`session.autocompact_pct`) and when a model rejects a prompt as too long,
   and the chat says so where it happened, in `/compact`'s words
   ("Conversation compacted: freed 42% of the conversation (…)"): a
   `compaction_status` of `automatic` (`llm/events.COMPACTION_AUTOMATIC`),
   which keeps the answer streamed before it. Every compaction notice (a
   `/compact`'s result, the loop's own pass, and the session manager's
   restart of a session at the threshold) is also said on the channel thread
   the conversation is linked to, where its replies go
   (`DashboardState.tell_linked_channel`): a chat that came from a channel on
   that channel, and a channel's own thread on the channel that issued its id.
   A compaction keeps the first messages and the latest ones verbatim and folds
   the rest into a summary and an account of the calls it folded; the kept head
   and tail hold whole exchanges, a call with every result it got
   (`context_compaction._whole_exchanges`). Cut between them, a kept result was
   dropped as an orphan and the account said its call had no result, so an agent
   disowned a fact it had read from that result.
3. **Agent resolution** — an agent's own `system_prompt` governs when it has one;
   the default agent ships with none, so its prompt is the one bound in Settings →
   Prompts for the turn's context (`chat`, or `background` for unattended runs).
   An agent's own instructions and voice are read in one place,
   `agents.instructions.agent_instructions`: its profile in `config.json`, else its
   file under `<home>/agents` (never another agent's words for a name `config.json`
   does not hold). Every path that runs a named agent hands its model those words: a
   chat routed to it, a spawn, workflow step, automation or app run that names it
   (`build_message` is told the agent), a webhook on it, a loop it works, a room it
   sits in (its first message), and the heartbeat, which runs the default agent (as
   `personalclaw chat` does: its turns are chats of the gateway's). A spawn that names no
   agent is a helper of whatever started it,
   on that one's runtime, framed as a sub-agent on the Background prompt and handed no
   agent's own instructions: handed its parent's, a goal loop worker's helper would run
   the loop's own cycle protocol, and handed the default agent's, a helper working for
   another agent would be told it is the default one.
   `tests/test_agent_safety_rules_census.py` fails for a new path that names the agent
   it starts and hands its model none of its instructions.
   The agent's voice and the task-mode posture (`system_prompt_suffix`) are layered
   ON TOP of whichever prompt resolved — never a replacement (see `build_message`).
   So are the platform's safety rules, last and once: the `safety-rules` snippet the
   Chat and Background prompts include, worded in that one place, which an agent's own
   prompt adds to and cannot remove (`prompt_providers.runtime.with_safety_rules`). A
   room member's first message carries them the same way, and
   `tests/test_agent_safety_rules_census.py` fails for a new path that starts an agent
   without them. The resolved prompt opens the session's first message, verbatim, ahead of
   memory, history and the request, and a compaction keeps that message. A word
   limit an agent's own instructions set for its answer ("Keep the answer under 200
   words") is checked when the turn ends (`answer_rules`): an answer past it is
   followed by a notice row that gives its word count and quotes the agent's
   sentence. The answer is never cut, and an answer to a message that sets a word
   limit of its own is not checked against the agent's.
4. **Model resolution** — the `chat` use-case binding from
   `active_models.json`, unless the composer picked a model for the session or
   the agent pins one (in that order; the `model` kwarg threads through
   `llm/registry.py` `registry.build`; every factory honors it). A chosen
   model runs on ITS provider, not on the chain head with a borrowed model id.
   One rule decides whether a chosen model can run
   (`providers/provider_bridge.named_model_problem`): it must be one of the
   chat models set up in Settings → Models, and its provider must be able to
   serve it. A choice that cannot run is kept, never rewritten; the turn runs on
   the chat binding and says so (`ModelSubstitution`: "Ran on X instead of
   Researcher's model Y: …") — live as an `activity_event` of kind
   `model_substitution`, and on the reply's `meta.model_substitution`, so a
   reload says it too. A room member's turn says it as a room note, and the
   Agents page and a room's members panel show the pin as unavailable
   (`GET /api/agents` → `model_unavailable`). There is no strict setting that
   would refuse the turn instead. Editing or deleting an agent marks its open
   sessions (`SessionManager.mark_agent_stale`): a turn already running finishes
   as it started, and each session's next turn rebuilds its runtime from the
   agent as it now reads.
   **A turn falls back down its chain** (`agents/native/failover.py`). When the
   model a chat turn runs on fails before anything of the turn was shown (a
   provider error or a timeout, after its one retry; its breaker is open, since a
   runtime keeps the model it was built on; or, on this machine, it stays busy with
   background work past the turn's short wait, `guardrails/local_queue.py`), the
   native loop
   moves the turn's inner model to the next model in the same order (the chat's
   pick, the agent's pin, then the chain), once each, skipping one that cannot be
   built, cannot use the turn's tools or cannot take its images. The one that
   answers says so before its reply, as an `EVENT_MODEL_SUBSTITUTION` the chat
   runner turns into the same `model_substitution` line and reply meta ("Ran on
   Y instead of X: it failed before it replied (…).") and a room writes as a
   note in the member's slot. The turn's terminal `EVENT_COMPLETE` names the
   model that answered (`served_model_ref`), and the chat records and prices the
   turn by that model's id: its `usage/turns.jsonl` row, its "Turn complete"
   line and `meta.turn_telemetry` (every ledger write does,
   `usage_ledger.answered_model`). The row's `provider` is that ref's entry, the
   `Y` of `Y:gpt-4o` (`usage_ledger.answered_provider`); an ACP turn, which names
   none, keeps its runtime (`acp:claude-code`). A room member's turn writes its
   own row (`source: room`, under the member's session key and agent), so
   Settings → Usage counts a room by source, by provider and by model. So does
   the summary a member folds its context with (`source: room`, on the member's
   own model), and a chat's history compression writes one too (`source:
   background`, the lite agent, under the chat's key; the idle-chat pass in
   `bg_compress` the same, with no agent): a one-shot call records itself with
   `one_shot_completion(usage=Attribution(…))`. Every guarded call's
   `EVENT_COMPLETE` names the call (`LLMEvent.audit_ids`, stamped by
   `ModelCallGuard`, and summed over a turn's inferences by the native loop), and
   the row keeps those ids, so the usage page's "Not included" census of
   `model_calls.jsonl` leaves out a call a row already counts: it states only the
   calls that wrote no row (a chat's title, a judge). `audit_ids` is an SDK
   addition to `LLMEvent`, defaulted, which no app has to set. The guard prices
   each call once, at the model the call asked for, and stamps that price beside
   the id (`LLMEvent.charged`, the figure it charged the spend meter and wrote to
   `model_calls.jsonl`); the native loop sums its turn's, and the row takes the
   sum rather than pricing the turn's tokens again, so Usage, the budget meter and
   the call log hold one figure per call. A turn that ends in an error (a dollar
   cap refusing its next call) first sends `EVENT_SPENT`, the usage of the calls it
   made, and the writer of the turn's row writes it from that. The next
   turn starts on X again. When every model fails, the error names each one and why (`NoModelAnswered`, in a
   room's words on a room). Only a caller that says so asks for this
   (`NativeAgentRuntime.announce_failover`: the chat runner, and a room through
   `stream_and_collect(on_substitution=…)`): a loop keeps the failure rather than
   another model's reply presented as the chosen one's. A background chore
   (history consolidation, thread compression, the chat title, its organize
   proposal, suggestions, follow-up chips and a folder's icon) is a call of its
   own (`chores.run_chore`): it is sent its own prompt and nothing of any chore
   before it, whichever chat that one was for, and it walks its chain as every
   one-shot call does, each step said in the log (a census in
   `tests/test_an_open_breaker_moves_the_chain_on.py`). A chore names what it
   reads the answer as (`validate`), so an empty answer or one in the wrong shape
   is that model failing and the next one answers, as a failure, a timeout and an
   open breaker do. A title or a consolidation that no model could answer is owed
   (`owed_chores`): the heartbeat tries it again, at once after a provider whose
   breaker opened answers again, and an ended chat is sealed only after its
   consolidation ran. A
   one-shot call walks its chain the same way with a time budget per model
   (`one_shot_completion(attempt_timeout=…)`, which knowledge enrichment uses), so a
   slow first model hands over instead of spending the whole wait, and a chain that
   only timed out is reported as a timeout (`llm_helpers.ChainExhausted`), not as
   no model being available. The chores whose answer she sends as her own words, the
   follow-up chips and the suggestions, may say for her only what they were written
   from says. Each prompt has rules added after whatever template is bound (a seeded
   prompt is never rewritten on an existing install); the chips' rules also say a chip
   never answers what the reply asked her. The answer is then checked
   (`given_details.keep_given`): one that states a time, number, name, path or file
   its source does not give is left out. A prompt rewrite is checked the same way
   (`given_details.ungiven`) and is not used when it adds such a detail;
   `/api/optimizer/optimize` names it in `added`.
5. **Streaming + persistence** — chunks stream over the dashboard WebSocket;
   the finished turn is saved by rewriting the session JSONL from the buffer.
   Every exit from a turn, an error included, first settles the answer
   streamed so far (`_flush_segment`), so a partial answer is kept like a
   finished one.

Around the engine:

- **`dashboard/chat_handlers.py`** — session listing/history. Channel-linked
  rows carry `origin="channel"` (computed at list-time from the session map,
  never persisted); the frontend `ChatPage.tsx` switches tabs on that literal.
- **`dashboard/chat_title.py`** — auto-title plus optional auto-tagging in ONE
  background LLM call (config `dashboard.auto_tag_sessions`); an Incognito or
  Temporary chat is titled by its mode instead, with no model call;
  `chat_retag.py` is the batch re-tag job (cancellable, board-triggered). A
  name the chat is given while the model answers (a rename, a channel's name
  for its conversation) is the later word: the auto-title is not applied over
  it, and Generate title keeps it and answers with it.
- **`dashboard/chat_folders.py` / `chat_tags.py`** — organization; persisted
  in `folders.json` / `tags.json`.
- **`dashboard/chat_channel.py`** — channel link/handoff routes
  (`POST /api/chat/sessions/{session}/channel-link`,
  `GET /api/channels/reply-targets`) — provider-blind, built on
  `ChannelDelivery` only (see [inbox-channels.md](inbox-channels.md)).
- **`dashboard/chat_voice.py`** — `POST /api/voice/synthesize`, sentence-
  chunked TTS through `tts.registry.active_voice_params` (whatever TTS
  provider is bound). Its `voice_chunk` frames go to every page with the chat
  open and carry the asking page's name for the reading (`request`), so only
  that page plays them.

## Variant branching (regenerate)

`dashboard/chat_regenerate.py`: regenerating an assistant message preserves
the prior answer as a **variant**. The message's `variants[]` list (capped at
`_MAX_VARIANTS`) plus `variant_idx` are persisted in the session JSONL, so the
user can flip between alternative answers and the choice survives reload. The
backend broadcasts variant switches; the frontend renders prev/next navigation
on the message.

Regenerate runs again the message that started the anchored turn: the user's,
or the row an automation, a subagent's report or an auto-nudge dispatched it
with. On a failed turn's error row it is a plain retry, and nothing is kept as
a variant.

**A turn with no answer.** A native turn that ran tools and then wrote nothing
is asked once for its reply (`ANSWER_OWED_NOTE` in `agents/native/owed_reply.py`,
a note on that one request that never enters the history). If it still writes
nothing, the chat runner ends the turn in an error row ("The agent ran 3
steps but did not write an answer. …"), the turn's outcome is `error`, a linked
channel hears the same line, and the chat offers Retry on the notice, as on any
notice a turn ends on. A turn that wrote nothing and ran nothing is resent
once, silently. A loop's worker is left to its own re-prompt.

A model can also stop at its output cap before it writes or calls anything
(every provider the native loop drives carries its length stop on the terminal
event, read by `llm.events.is_length_stop`). The loop then asks once more in
another form, on every surface, a loop's worker included: a brief answer, and
the request goes out without the turn's reasoning effort, which on the
providers whose cap counts reasoning is what spent the room
(`out_of_room_note` in `agents/native/owed_reply.py`). The cap itself is not
raised: it is the model instance's own setting, and a hosted model's ceiling
above it is not declared anywhere core can read. A model still out of room
ends the turn at the cap, its terminal event naming the tokens it stopped at
(`AgentEvent.output_cap`); the chat says so and never resends it, a subagent
ends failed ("Couldn't do its task: the model ran out of output room before it
answered (8,192 tokens)…"), and its workflow step fails with that cause. The
model-call log records each such call as `output_cap`, failed when it produced
no text and no tool call that can run.

A subagent started to read or change things on a model that can't use tools
could only claim it had: it ends failed before any call is made for it
("Couldn't do its task: “…” can't use tools, and it was started to work with
them. Choose a model that uses tools for Orchestration in Settings → Models, or
start it on one.", `subagent_tier.without_tools`), and one whose model refused
the tools a call offered it ends the same way when its turn is over. Its
workflow step fails with a fix about its model. A subagent of the `text` class
asked for no tools and runs as before, and an agent CLI brings its own. A chat
on such a model is answered without tools, as the chat says.

**How a turn ended is said by its cause.** The chat runner names one ending per
turn, and an agent CLI's endings map onto the same ones
(`acp/session.py`, `AcpSession._dispatch_frames`):

| What ended it | Outcome | What the chat says |
|---|---|---|
| The agent finished (`end_turn`) with an answer | `complete` | the answer |
| It stopped itself (`cancelled`) when a refusal of one of its calls ended its turn | the carried-on turn's | "Codex ended its turn when you denied Run command, so PersonalClaw asked it to carry on without it." — the same session is told what was refused and asked to go on without it, and what it does next is this turn (`AcpSession._dispatch_frames`, at most four times a turn) |
| It finished with no answer after you denied one of its calls, or stopped itself and could not be carried on again | `error` / `stopped` | "Codex stopped after you denied Run command." — never resent, which would ask you again |
| It refused to continue (`refusal`) and wrote nothing | `error` | "Codex refused to continue and wrote no answer." — never resent |
| Its model ran into its output cap (`max_tokens`) and wrote nothing | `error` | "The model ran out of output room before it answered (8,192 tokens). …" — never resent |
| It stopped itself (`cancelled`), and nobody denied or stopped anything | `stopped` | "The reply stopped before it finished. …" |
| You pressed Stop | `stopped` | the stop card; never a timeout or an error |
| Its process ended, or its connection closed, before it answered | the resent turn's | "⟳ Connection lost — retrying…", and the message is resent (up to three times) — unless the turn had made calls nobody refused: "The connection to Codex closed after 2 steps of this turn, so your message was not sent again: that could repeat them. Send it again to retry." |
| A turn that runs on its own (a queued message, a retry, a subagent's report, a loop's nudge) ran past its time limit | `error` | "This turn ran past its 10-minute limit and was stopped. Send your message again to retry." — its clock stops while one of its calls waits on your answer (`dashboard/turn_deadline.py`) |
| It did not answer within the turn's time limit | `error` | "The agent did not finish within the turn's time limit, so the turn was stopped. …" — its late answer is dropped, never shown in the next turn |

Silence is not one of these endings: while its prompt is unanswered and its
connection is alive, an agent that is thinking after its last step is still
working. A turn that ends before its answer — its time limit, a Stop it never
acknowledged — tells the agent to stop, and the session settles that answer
and drops what came with it before it takes another turn; an agent that never
answers is restarted.

An agent CLI's session sends one prompt at a time, and a turn gives the session
back the moment it ends: as its terminal event is handed on, or as the error that
ends it is raised (`AcpSession._turn`). Whatever reads a turn's stream and stops
before the stream's end — the chat runner at the terminal event, a Stop, a reader
whose own work fails part way — closes it, and every layer that passes a stream
on reads it with `turn_streams.closing_stream`, so closing the outer stream closes
the session's at once. A turn closed part way tells the agent to stop and owes its
answer before the session is given back. Nothing waits for the interpreter to
collect a stream: that came at once on one Python and not for seconds on another,
and the next prompt on the session waited, unsent, all that time.

A **Deny** answers the agent with its own refusal that declines the call and lets
it continue — an agent can offer another refusal that ends its turn, under the same
`reject_once` kind — and the refused step's row names the option that was sent.
An agent can also offer only the refusal that ends its turn (an agent asking for an
escalation, or for a file change, does), and then the card says before the Deny that
it ends the agent's turn (`deny_effect` on the approval). Whatever the refusal, a turn
it ends is carried on: the session tells the agent which step was refused and asks it
to go on without it, and the chat says so. The audit row of every decision on an agent
CLI's call names every answer the agent offered (`offered`), beside the one sent
(`answered`).
A **Stop** sends `session/cancel`, answers a pending approval `cancelled`, and
waits for the agent's answer (`agent.soft_stop_budget_secs`). An agent that
answers keeps its process for the chat's next turn; one that does not is killed,
and nothing starts in its place until a turn needs one. Between turns an agent
CLI's process stays up for the chat's next turn until it has been idle for
`session.timeout_secs`. What its commands start inherits the process's own run marker
(`run_processes`), so what they leave running, a server that detached itself included, ends
when that process is taken down; a loop worker's ends with its loop.

An agent's JSON-RPC error is said in its own words — its message and data,
masked like any child's output (`acp/errors.py`, `AcpRequestError`) — and a
process that exits is said with its exit code and its last output. A runtime's
Test that fails says all three and what to check: "handshake failed:
session/new was refused: … claude-agent-acp exited with code 64 (a usage error:
…); its last output: …. Check that claude runs on its own and is a version
claude-agent-acp supports, then Test again." The gateway log carries the same
sentence.

## Forking

`dashboard/chat_fork.py` — `POST /api/chat/sessions/{session}/fork` copies a
session into a new tab. An app may fork only a conversation it started (its
`ROUTE_AUTHZ` row carries `owns`, checked against `created_by_app`), and the
fork is the app's too; one of yours is refused to every app.

## The session map (in-session index) + per-turn telemetry

`dashboard/chat_session_map.py` — `GET /api/chat/sessions/{session}/map` answers a
JSON array of **marks**: one per turn (`user` / `assistant`) plus one per indexable
sub-event inside a turn (`tool`, `approval`, `error`), in turn order, each with
`{markIndex, kind, role, visibleIndex, ts, preview}` and an optional `telemetry`.

- **`visibleIndex` is the jump coordinate** — the index of the turn's LAST message in
  the backend's visible (`user`/`assistant`) list, i.e. the same inclusive
  `at_message_index` that `POST .../fork` and edit-resend speak. It is derived from the
  identical message list `GET /api/chat/sessions/{session}` serves
  (`chat_utils.full_session_messages` → `_prepare_messages`), because an index into any
  other list would address a different message than a fork does.
- **The derivation mirrors the frontend's.** `web/src/pages/chat/sessionMap.ts`
  (`sessionMapMarks`) derives the same shape from the hydrated turns; the endpoint is a
  second SOURCE for one contract, not a second contract. Both reproduce
  `hydrateTurns`'s two collapses — a native-loop prompt re-injection consumes a visible
  slot without producing a turn, and consecutive assistant messages merge into one turn
  keyed on the last message folded in. The map UI draws a coarser view of the same marks:
  one entry per user message (`sessionMapEntries`, which groups the marks by exchange), so
  the typed marks remain the contract even though the rail no longer draws every kind.
- **Two kinds are live-only.** `subagent` and `activity` ride WS streams that are never
  written to the conversation log, so the durable endpoint witnesses
  `user`/`assistant`/`tool`/`error` after a restart, and `approval` once it is decided
  (`_persistable` writes a resolved `permission` row and holds back a pending one).
- **Per-turn telemetry** (cost, tokens, cache split, duration, context %, event and
  tool-call counts, the model that answered) is stamped by `chat_runner` onto the
  turn's LAST assistant message as `meta.turn_telemetry`, *before*
  `save_session_to_history` — that function
  rewrites the transcript file from the buffer, so a later stamp would be in-memory
  only. It rides the same `meta` seam as `memory_citations` (and as `skills_used`, which
  is on the message that started the turn — the user's, or a loop's nudge, an automation's
  or a subagent report's row; the message each skill joined): no new file and no new
  channel. Absent = the turn reported nothing; `priced: false` means the
  model has no price row (never "free"); `context_pct: null` means the provider measured
  nothing (never 0%). What fed the turn rides the same message as `meta.context_fed`: the
  sentence its footer says ("Injected 1,204 chars of context …"), which a runtime's first
  turn says live, so a reload's footer says it too. Absent = the turn said nothing about
  its context. The footer ledger (`ContextLedger`) says what fed the turn, what it learned
  and its telemetry, live from their activity lines and after a reload from these keys; its
  lines are never drawn in the turn itself, and a turn's tool cards never keep them out
  (`LEDGER_ACTIVITY_KINDS`, `insertActivity`).

## Channel-linked sessions

A dashboard session can be linked to a channel thread (and vice versa):

- Linking goes through core `session_map.set_channel_link` — the channel app
  never touches the map file directly. `dashboard/channel_links.py` is how the
  dashboard reads and writes a chat's link: `DashboardState.link_channel` links
  a chat (the inbound door, a link or a handoff from the dashboard, a channel
  app's import), and a thread continues one chat, so the chat that had the
  thread loses it, found in the store whether or not it is resident.
- **The link survives a restart.** The inbound door asks the store which chat
  a thread continues (`DashboardState.get_linked_session`), and a chat that is
  not resident comes back from disk, so the next message on a channel thread
  after a restart continues its chat with everything said before. A chat reads
  its own link from the store too (`_ChatSession.channel_link`, which its frame
  and its question cards read), never from a copy. A thread whose chat is gone
  (deleted, archived, a Temporary chat that ended) starts a new chat.
- **A link names the channel its thread is on, and that is where the chat
  answers** (`channel_provider_for`, which reads `channel_links.chat_channel`):
  its replies, its notices and its approval prompts go there, before a restart
  and after it. Every link is made with its channel
  (`link_channel(chat, thread, channel_id, provider=…)`): the door's for the
  channel a message came on, a handoff's or a link's for the channel it opened
  the thread on. The door continues only a chat whose link names one: a channel
  thread's own conversation opened from the chat list (a Slack thread the Slack
  app runs) has none, and its channel app answers it itself. The origin tag
  (`_app`, the meta line's `app`) still says where a chat came from; it no
  longer says where it answers, and a chat that came from Telegram and was
  continued in a Slack thread answers in the Slack thread.
- **A chat continues in one place.** Continuing it somewhere else (the chat's
  "Continue on …", a link from the dashboard, a channel app's resume) moves its
  link: the thread it was on no longer continues it, so a message there starts
  a chat of its own. When that thread is the owner's own DM on its channel (the
  one the channel opens for the owner's id), it is told, once, where the chat
  went ("This chat continues on Slack now. Messages here no longer reach it.").
  A group, a shared channel, someone else's DM or a correspondent's mail thread
  hears nothing: the note would tell other people where the owner went on, and
  on a channel that speaks as the owner it would go out in the owner's name.
  Continued where it already is, nothing changes and nothing is said.
- `sync_bridge.py` implements the dashboard↔channel handoff
  (`handoff_to_channel` over `ChannelDelivery`): the conversation continues in
  the channel with context intact.
- `voice_reply.py` uploads TTS voice replies to the channel
  (`upload_voice_to_channel`); markdown deep links are stripped generically
  before synthesis.

## Prompt entities

`prompt_providers/` is the prompt-catalog subsystem (bundled use-case prompts
plus user prompts). Use-case prompts include e.g. `task-channel-title`
(use case `channel_title`) for naming channel-originated tasks. All bundled
prompts are provider-blind.

## Related docs

- Memory recall/write rules per session mode:
  [knowledge-memory.md](knowledge-memory.md)
- Channel delivery and thread linking: [inbox-channels.md](inbox-channels.md)
- The agent/tool layer a turn can reach: [overview.md](overview.md#capability-seams)
