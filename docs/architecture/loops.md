# Loops — the Autonomous Work Engine

A **loop** is a long-running autonomous work unit: a worker agent iterates in
cycles toward a goal while a supervisor judges progress against ground truth.
The engine lives in `PersonalClaw/src/personalclaw/loop/`; this doc covers the
five kinds, stage progression, directory resolution, deliverable gates, and how
the UI dispatches to per-kind cockpits.

## Engine layout

`loop/` — `manager.py` (lifecycle + orchestration), `tick.py` (the cycle
engine), `judge.py` (supervisor judgment), `gates.py` (verify-command +
verdict helpers), `lifecycle.py`, `watchdog.py` (stall detection),
`worktree.py` (parallel task isolation), `store.py` (persistence),
`classify.py` (goal classification), plus per-kind planning-brief modules
(`code_plan_briefs.py`, `goal_plan_briefs.py`, `research_plan_briefs.py`,
`design_plan_briefs.py`).

## The five kinds

`loop/loop.py` defines `LoopKind`:

| Kind | What it is |
|---|---|
| `general` | generic iterative goal — runs as a **workflow run**, see below |
| `goal` | open-ended / verifiable / monitor research + action |
| `code` | SDLC stage-gated work in a workspace (mini-IDE cockpit) |
| `design` | design-system creation (live canvas, tokens, components) |
| `research` | deep iterative web research → synthesized report |

Kind behavior is pluggable: `loop/kinds/__init__.py` defines the
`LoopKindStrategy` protocol and a registry — **a new kind is a new strategy
module plus one `register()` call; no engine edits**. A strategy declares its
kind id, whether it needs a bound workspace, its default worker agent, whether
it provisions per-phase TaskLists at launch, its classifier, phase keys, the
deliverable document it maintains (e.g. an open-ended goal keeps `REPORT.md`,
a monitor keeps `MONITOR_LOG.md`; code has no document — the code itself is
the deliverable), and readiness prerequisites (a brownfield code loop with no
bound workspace cannot start).

**The supervisor is NOT part of that plugin seam.** How a loop
converges is *declared*, not coded: `workflows/supervisor_policy.py` holds the
`ConvergenceSpec` type, the closed `DONE_SIGNALS` vocabulary
(`orchestrated` | `never` | `verify_command` | `judge_assessment`) and the
`KIND_CONVERGENCE` table with one row per kind — or per `kind:goal_type`
variant, because goal-type is the axis convergence actually varies on.
`policy_for_kind(kind, kind_config)` resolves a kind to the one
`SupervisorPolicy` that carries it, and `loop/supervisor.py` is the single
kind-agnostic evaluator that reads it (`done_signal`, `has_done_check`,
`budget_stop_is_genuine`, `stagnation_enabled`). The watchdog calls only those.
A kind may not supply a convergence mechanism in Python — adding a fifth
mechanism means extending the closed vocabulary and the one evaluator, in one
place, for every kind at once.

| Kind (variant) | marked done-signal |
|---|---|
| `code`, `design` | `orchestrated` — the per-cycle `on_new_cycle` hook owns done-ness |
| `general` | `verify_command` (optional: no command ⇒ defers to budget by design) |
| `goal:verifiable`, `research:verifiable` | `verify_command`, plus a sub-goal judge when >1 sub-goal is declared |
| `goal:open_ended`, `research:open_ended` | `judge_assessment` (ground truth: `REPORT.md` / `RESEARCH.md`) |
| `goal:monitor`, `research:monitor` | `never` — only a user Stop (or the budget, counted as a clean stop) ends it |

## What a loop runs on

A loops-table loop's planner and workers run on PersonalClaw, its kind's own worker agent, unless
it is put on an agent CLI. Two spine fields say which: `provider` (the CLI's runtime id,
`acp:<cli>`) and `provider_agent` (the agent that CLI offered, its ACP mode id; empty for a CLI
that offers one). `loop/manager.py` binds them onto the stage worker and every per-task worker the
way the chat's agent picker binds a chat, and `loop/plan_walkthrough.py` onto the planner. The
supervisor's judge still runs on the model its use case binds (`loops.judge_use_case`).

- **Choosing.** The loop composer's **Runs on** pill sends both fields with the create. Plan Review
  changes them until launch, and so does the Runs on chip on the page of a loop that
  has not started. The picker lists every agent CLI set up here (`GET /api/agent-providers`), each
  ready one with the agents its last Test listed; one that is not ready is shown with why and
  cannot be picked, and a page names a loop's runtime as unavailable when it is no longer set up,
  no longer ready, or no longer lists the chosen agent (`web/src/pages/loop/loopRuntime.ts`).
- **Refused, not stored.** `POST /api/loops`, `POST /api/loops/validate` and a pre-launch
  `PUT /api/loops/{id}` refuse a `provider` that is not an `acp:<cli>` runtime registered here, a
  malformed `provider_agent`, and a `provider_agent` with no `provider`
  (`loop/validation.runtime_errors`). A `general` loop runs as a workflow, which cannot be put on
  one agent CLI, so it refuses a runtime too rather than dropping it.
- **Starting.** `PATCH /api/loops/{id} {action: start}` (and the agent's start tools) refuse a loop
  whose CLI is not ready as the user's last Test of it found, with the CLI's own reason
  (`loop/validation.runtime_blocker`, which starts nothing). The loop stays `ready`. A resume is not
  re-checked, as with the kind's own launch blockers.
- **Not the chat default.** Settings → Agent defaults' Default agent is a chat's default and never
  reaches a loop: a loop moves onto another program only when it is chosen for that loop.

## One listing, two homes

A kind in `workflows/service.py:PORTED_LOOP_KINDS` (today `general`) no longer writes a
loops-table row: `POST /api/loops` starts a `WorkflowRun` on the kind's bundled template
(`general-project`: a root `loop` over `sequence[work, judge]`), stamping the run with the loop's
`loop_kind` and `title` and carrying the composer's knobs as the run's sparse `policy_overrides`.
Every other kind stays a loops-table row driven by `loop/watchdog.py` + autonudge.

The loop surfaces do not care which home a loop has:

- **Listing.** `GET /api/loops` returns both — loops-table rows and every run started as a loop,
  projected into the loop wire shape by `workflows/loop_view.py` — newest first, filtered alike.
  A projected row carries **`run_id`**; that field, never the kind, is how a surface tells the two
  apart (`web/src/lib/loopKind.ts:loopRoute`, `lib/loopStatus.ts:loopActionSources`). The Loops
  list, Home (hero + Active Work) and Mission Control's Working lane all read this one listing.
  The Working lane also reads `GET /api/workflows/runs?status=running`, because a run started
  from Workflows, by a trigger or by a project is neither a chat session nor a loop. It leaves a
  run that backs a loop to the loop's card, and a sub-run to the run that spawned it
  (`lib/attentionLanes.ts:toLanes`). Both lists are re-read when a run starts, moves or ends: the
  watchdog sends the listing hint `refresh` naming `workflow_runs` on every `workflow_run_update`
  (`workflows/watchdog._raw_publish`), and Mission Control, Home and the Workflows list's Runs tab
  re-read on it, so a card moves lanes or leaves without a reload.
- **Status.** A run status is projected onto the nearest truthful loop status (`loop_view._STATUS`):
  `cancelled` → `stopped`; `escalated` → `complete` with a non-`done` stop reason, which renders
  "Ended early" (`cycle_budget` when the loop spent its iterations, `worker_failed` otherwise).
- **Actions.** `GET/PATCH/DELETE /api/loops/{id}` and `/nudge` answer for a run-backed id through
  the RUN's own verbs and guards (pause/resume/cancel/delete/steer). Its action table is narrower
  (`loop_view.RUN_ACTION_SOURCE_STATES`, mirrored in `RUN_LOOP_ACTION_SOURCE_STATUSES`): a run has
  one attempt, so no resume from `failed`, and a gate is answered on the run page.
- **Unattended.** `attended: false` in the overlay is the explicit grant that lets the run's stages
  spawn without a per-stage approval (`approval_mode: auto`), still inside the operator's safety
  ceiling; anything else asks per stage. `max_cycles` bounds the root loop's iterations. The grant
  reaches the worker's own tool calls because `SessionManager.get_or_create` hands a session's
  approval policy to its provider (a native runtime gates tools itself), and a native session
  spawned with no cwd works in the validated workspace root — never the gateway process's cwd.
- **Pause** stops the step in flight: the controller reads a sticky `PAUSE` intent in the run dir,
  cancels the dispatched stage's subagent and re-queues it at the same epoch, and a paused run is
  not re-adopted after a restart. Resume clears the intent and wakes the controller.
- **Restart.** A resumed controller rebuilds each loop's iteration counter from the ledger's
  `continue` iteration rows (`iteration_context.rehydrate_loop_progress`) and re-queues a
  dispatched stage whose subagent this process does not know
  (`stage_settlement.requeue_orphaned_stages`) — without both, a run past its first iteration
  failed "run deadlocked" after a restart.
- **Ending.** A loop's own exit test is asked before its cycle budget
  (`workflows/tick.py:loop_should_continue`), and the budget stops it only when it would otherwise
  go on: on the cycle the budget ends on, a judge stage that accepted the work is the loop's done
  (`workflows/loop_iteration.py:advance_loop`). A loop that genuinely runs out escalates with a
  sentence naming the budget ("It used its budget of 6 cycles, and the judge did not accept the
  last one."), and its escalation record says it was a budget stop (`budget: true`,
  `workflows/resilience.py:escalation_artifact`). That sentence is the run's ending, naming no step
  when the loop is the run's root (`workflows/ending_sentence.py`), and every run surface (the run
  page, the Workflows list, the chat card) reads the record and says "Stopped at its budget", never
  "Escalated", without offering a workflow change for a cycle budget the run was given. A run with a
  `loop_kind` announces its end as a loops-table loop does
  (`workflows/attention.py:announce_loop_end`): a `loop_complete` / `loop_failed` notification, and
  an inbox item when it escalates, titled "Loop stopped at its budget" or "Loop stopped before it
  finished"; a cancel says nothing.

For a loops-table loop, pause, stop and delete also stop the worker's turn IN FLIGHT
(`manager.halt_worker_turns`) — disarming the nudge loop only stops the NEXT cycle — and the cycle
driver checks the loop is still armed before each re-prompt. A restart re-arm keeps the running
stretch's `started_at` (the trust window and deadline are measured from it), and the watchdog's
credited-cycle baseline is durable (`credited.json`), so a restart neither resets elapsed time nor
re-credits a cycle.

Incident mode (`personalclaw incident on`, or Settings → Guardrails) **holds** a running loops-table
loop, the way it suspends cron, hooks, triggers and app workers. The idle runtime fires no nudge row
while the switch is on (`triggers/idle_poll.py`, skip reason `incident_active`), so no cycle starts;
the watchdog stops the turn in flight on every worker through the same stop a pause uses and does no
cycle bookkeeping — no crediting, no done-ness check, no stage hook — until the switch is off
(`watchdog.LoopWatchdog._hold_for_incident`); and the cycle driver abandons the cycle's re-prompts.
A loop's planner passes are held the same way (`planning/runner.py`), without spending their time
budget. The status stays `running`: a hold is not a pause, and the loop carries on by itself once the
switch is off. Both redacted views carry the sentence that says why (`held`, `loop.held_reason`),
which the loop surfaces show as **Held**. The time held is not worker silence (the unresponsive
deadline restarts at the release), and a turn the hold stopped is not counted as a worker failure.
The gateway watches the switch (`guardrails/incident.watch`) and sends the `refresh` hint naming
`incident` and `loops` when it moves, the CLI's flips included, so open pages re-read at once.

A workflow run, and so a general loop, is held the same way (`workflows/incident_hold.py`). On its
first step with the switch on, the run's controller withdraws the work in flight as a pause does: a
stage's subagent is stopped ("Stopped: incident mode is on") and the stage goes back in the queue at
the same epoch, its attempt not counted. It starts nothing more, looking again each tick, until the
switch is off, when it carries on by itself. A stage the switch meets at dispatch, or one the subagent
manager refuses for it, waits rather than failing. The status stays `running`; the run's status, its
row in the run list and its loop view carry the sentence (`held`). The run page shows it under a
**Held** status, as the run list and the chat's run card read Held rather than Running
(`workflowMeta.runLook`), and Mission Control's Working lane leaves a held run out, as it leaves out a
held loop.

### Mode: Attended and Unattended

A loops-table loop's Mode (`loop.attended`) decides how every session the loop runs is answered
and paid for: its planner (the walkthrough's design pass and each step pass), its stage worker,
each per-task worker, and the merges of their work. One module decides it (`loop/posture.py`):
`posture.of(loop)` reads the Mode, `posture.arm` sets it on a session each time the loop arms one
(the planner at every pass, a worker at every start, resume and task spawn), and `posture.frame`
frames the session's cycle message. So no session of a loop runs looser than its Mode, and a resume
never carries one run's posture into the next. A Mode that is not exactly `true` or `false` (a loop
created without one, a row the store cannot read) is the cautious reading of both halves: its
sessions ask a person, and their spend still counts against the cap. A loop created with no Mode
(`POST /api/loops` without `attended`, an agent's `code_project_create`) is Attended.

- **Attended.** A session's call that needs approval goes through the same path a chat's does
  (`approval_state._hold_approval`): the card on the loop's page (`LoopApprovals`, in the loop, code
  and design cockpits and in the planning walkthrough), the bell and Inbox row, a phone push and the
  channel approvals go to. The grants that stand for chats stand here too (an agent's "Always allow"
  or "Trust reads", YOLO, the operator's hook patterns), and the card's "This loop" scope lets every
  worker of this run, and the planner for the rest of the planning, act without asking until the run
  ends: a launch, a pause, a stop or a restart ends it (`manager.grant_every_worker`). A worker may
  also write one question and pause the loop. The time a turn spends waiting on your answer does not
  count against it: the watchdog does not read the wait as a wedged worker, the cycle's own time
  bound stops while it waits (`cancellation.wait_for_unpaused`), and so does a planner pass's
  (`planning/runner.py`). No session is told it runs as an autonomous run. An Attended loop holds no
  standing grant, so nothing of it expires. Its planner's and workers' model spend is the owner's,
  as a chat's is: the daily cap for unattended work does not count it (`chat_runner` passes
  `unmetered` from `posture.spend_metered`, and the runtime's model guard is built uncounted). The
  supervisor's own checks of the loop's work (its stage judge, its verify and test commands) are
  automation's and are metered as before.
- **Unattended.** Nobody is there to ask, so the sessions run on a standing grant: their calls go
  ahead without asking, inside the deny-list, your hooks and the operator ceiling, and a call the
  grant cannot cover is declined at once rather than left waiting. An agent CLI is told the mode
  that stops it asking. Each cycle message is framed as an autonomous run (`autonomous_framing`).
  A running loop's grant lasts `loops.trust_ttl_secs` from the start of the running stretch; then
  every worker loses it (`manager.end_unattended_grant`) and the loop waits for you (`needs_input`)
  to resume it. A stray question is discarded. Its spend counts against the daily cap.

A cached runtime is rebuilt when who answers its session or whose spend it is changes
(`session._posture_moved`), since a runtime keeps both from when it was built.

A loop session's model call rides the guard every automated call does (`ModelCallGuard`: the
breaker, the clock, the attempt audit and the outbound scan), whatever the loop's Mode; only an
Unattended loop's is counted against the day's and the run's spend ceilings (`counted`). The guard
puts no clock of its own on a call whose provider instance keeps one (`ModelProvider.request_timeout_secs`:
an Ollama instance's Request Timeout, the wait for the first word and then between the parts of the
answer); for a provider that keeps none it stops the call at 300 s. A call stopped either way says
which model on which instance, how long it waited, and that a faster model can be bound to Loops in
Settings → Models (`guardrails.failure.ModelCallTimeout`). On a local model the call first waits for
its turn ([overview.md](overview.md#a-local-model-takes-one-call-at-a-time-yours-first)), and that
wait counts against the same limit.

A loop stays on the machine that ran it. The watchdog's first poll re-arms every loop it finds
running with no worker (`watchdog._boot_sweep`), so `loop/loops.db` and each loop's folder under
`loop/` are `machine_local` and not `merged_in` (`durability/inventory.py`): no sync carries them,
and a merge restore or an archive import leaves the archive's out. Another machine's running loop
used to arrive and be re-armed here, with the workspace and trust its owner gave it there. A backup
carries them, and a replace restore brings them back with the whole home, holding each loop that was
running or planning (`store.hold_restored`): it waits for you (`needs_input`), asking to be resumed,
since the snapshot is a moment it has gone on from since. A run-backed loop is a workflow run, which
stays where it ran and is held by the same rules ([workflows.md](workflows.md)).

## Stage progression

- **Code loops** walk the canonical SDLC ladder (`loop/sdlc_meta.py`):
  `ideation → requirements → design → decomposition → implementation →
  verification → review`. Lateral entries (`bugfix`, `cr_comments`,
  `refactor`, `investigation`) start mid-ladder with a tailored shorter plan.
  The code strategy (`loop/kinds/sdlc.py`, kind id `"code"`) advances stages
  and provisions tasks each cycle.
- **Design loops** advance design steps (token system → components → …) on a
  live canvas.
- **Goal/general loops** are done when their `is_done_signal` says so — no
  stage machinery.
- Classification (`loop/classify.py` + per-kind classifiers) picks kind, stop
  logic, and entry stage up front; classifiers never raise — they return safe
  defaults flagged `classified=False`.

## `effective_dir` — where a loop's work actually lives

`loop/loop.py::effective_dir` is the **single resolver** every ground-truth
check uses, so the supervisor reads exactly where the worker writes. Its
precedence:

1. `workspace_dir` — an explicitly bound codebase;
2. a **greenfield code loop's own `loop_dir`** — a code loop with no bound
   workspace operates *from* its files dir (code-kind only; goal/general keep
   only engine files there and write deliverables to the project/workspace).
   This tier exists because its absence hard-failed the deliverable gate
   forever: the supervisor looked in the workspace root while the worker wrote
   to the loop dir, so a genuinely-complete stage was "held" across cycles;
3. the containing project's shared context dir;
4. `workspace_root()` — the default session workspace.

## Deliverable gates & the independent judge

The supervisor does not take the worker's word for it:

- **`loop/gates.py`** — `run_verify_command` re-runs a stage's verify command
  itself (with a cwd from `effective_dir`); `judge_verdict` renders an LLM
  verdict; `verdict_is_pass` parses it strictly.
- The **SDLC gate** reads the deliverable *content* (not just existence), and
  the **goal judge** re-runs commands / reads artifacts — ground truth over
  worker self-report.
- **`loop/watchdog.py`** detects stalls, and its own first poll re-arms loops left
  RUNNING/PLANNING by a gateway restart so an interrupted loop resumes rather than
  zombifying (`LoopWatchdog._boot_sweep`). A turn running on ANY of a loop's workers — its stage
  worker or a task worker — counts as the loop working, so a long model call is not a stall. A
  failed loop can be resumed, so it ends the way a pause does (`manager.stand_down`): every worker
  is switched off and kept, its turn in flight is stopped, and each task worker keeps its worktree
  and the edits in it; Resume switches the workers back on where they were. Only a Stop or a delete
  removes the worktrees. That sweep runs through
  `concurrency.boot_sweep`, the ONE boot-adoption path it shares with
  `workflows/watchdog.py`. There is deliberately **no gateway boot hook**: a
  hook cannot be retried when it raises, and awaiting it delays startup by however long
  N stranded planner passes take.

## Planning walkthrough & grill

- **`planning/`** (`session.py` data model + `runner.py` state machine) is the
  shared stepwise **gated planning walkthrough** used before launching Code
  and Goal loops: the plan is presented step by step with approve/comment
  gates; a comment triggers a redraft of that step. A pass reports how it ended
  (`runner.PlannerPass`): a file the kind cannot read is named for what is wrong with it (where
  its JSON breaks), and a pass that ran out of time or ended without writing says that. The one
  automatic retry tells the planner which, and a step that still has no draft keeps the reason
  (`PlanStep.error`), which the walkthrough shows with its Retry. "Cancel and edit the task"
  deletes the draft and opens the composer with the task, project, codebase and Mode it had; Stop
  ends the loop with its plan kept. Both end the planner for good: the walkthrough pass in flight
  is cancelled, the planner's nudge row is removed and its turn stopped (`manager.halt_planner`),
  and no pass starts for a loop that is no longer planning. A planner's nudge row is kept on disk,
  so at boot, when no pass can be in flight, every one is removed before the loops still planning
  are driven again. Every surface that shows a loop at work offers its Stop — its page, the
  walkthrough, the Code list and a project's Work board (whose rows also open their work) — and
  the Loops list says how many code loops are at work and links to the Code list.
- **`grill.py`** is the memory-checked goal-scoping pipeline:
  `assess_goal → check_memory → decompose(shape) → save_decisions`. It pulls
  prior decisions/lessons so a decomposition doesn't re-litigate settled
  choices, and persists new decisions as lessons. Reused by goal loops,
  Projects, and the chat skill.

## Projects & worktrees

- **`projects.py`** is the small service layer that resolves which project a
  work unit binds to (`resolve_project_id` auto-creates one when none chosen;
  `ensure_task_list` finds/creates the unit's TaskList under that project).
  The entity itself is the Tasks `hierarchy.Project`
  (`tasks/hierarchy.py`): each project owns
  `~/.personalclaw/projects/<id>/` with `project.json` + `context/` (the
  cross-feature consolidation dir, and the working area when no external
  workspace is bound).
- **`loop/worktree.py`** — parallel task execution: workers run several tasks
  of a phase at once, each in its own git worktree under
  `projects/<project_id>/worktrees/<task_id>` (never the user's workspace);
  a finished task's branch merges back into the workspace's checked-out branch. A non-git
  workspace falls back to sequential execution.
- **Where a task's work lands, and under whose name.** Every commit a loop makes (a task's
  leftover edits on its branch, a merge commit, the first commit of an empty repository) is made
  as git is configured to commit in that workspace, `user.name` and `user.email` from the
  repository's, the owner's or the machine's settings, with `user.useConfigOnly` so git never
  guesses one (`worktree.commit_identity`, `_CONFIGURED_IDENTITY_ONLY`). The name PersonalClaw
  knows its owner by (Settings → Account) carries no email, so it is not one. With none
  configured in a repository, a code loop there does not start (`CodeKind.launch_blocker` says
  how to set one), and a running one pauses before any worker's next cycle (`CodeKind.schedule`
  asks first, every poll), so no worker is left to commit as a name it made up; its brief says to
  commit as git is configured and to stop and say so when git has none. A task whose branch holds
  a commit made as anyone else (`worktree.commits_not_by`) is neither merged nor put up for
  merging: the loop pauses and names the commits and the name they carry. An
  **Unattended** loop merges each task's branch into the workspace's checked-out branch as the task
  finishes. An **Attended** loop puts nothing on that branch unasked: a finished task's work stays
  committed on its own branch, nothing new starts on top of it, and once the stage's running
  workers finish the loop pauses with a merge review (`GET /api/loops/{id}/merge`: each task's
  branch, the commit it is at, its commits and its diff against the branch). **Merge** approves it
  at exactly those commits (`POST /api/loops/{id}/merge` with the commits and `{"confirm": true}`, owner-only, audited as
  `loop_merge_approved`); the approval is held by the scheduler alone (`CodeKind.approve_merge`,
  in memory: the loop's folder is its workers' to write, so nothing there stands for it, and after
  a restart the loop asks again), the loop resumes, and the scheduler merges each task at that
  commit, or asks again if its branch moved. Its questions (a merge to approve, an identity to set)
  are asked again while they hold, so a Resume never stops on one already settled
  (`files.SCHEDULER_QUESTION`). An Attended loop
  never makes the first commit of an empty repository either: its tasks then stay the stage
  worker's, in the tree. Every merge is recorded with who made it (`files.record_merge`,
  `merges.jsonl`), and the cockpit's Changes tab lists them, since a merge leaves the tree clean.
  A workflow run's own branch is committed the same way (`workflows/provisioning._commit_outstanding`),
  and with no identity its folder is kept rather than deleted with the work in it.
- **One writer at a time.** The code kind's scheduler (`CodeKind.schedule`, asked on every
  watchdog poll) never lets the stage worker and task workers write at once. A worktree is cut
  from HEAD, so a phase fans out only from a tree with no uncommitted changes to tracked files and
  only while the stage worker is between cycles; otherwise its tasks stay the stage worker's. A task
  worker that spent its cycle budget, or whose turns kept failing, asks its owner
  (`nudge.why_it_ended`); one whose nudge loop is gone was torn down by something else, and the
  scheduler starts it afresh, so a steer and Resume give the task a turn. While
  task workers run, the stage worker's cycles stand down, and it stands back up when they drain. A
  loop whose model's entry sends to this machine (`llm.registry.sends_to_this_machine`, which counts
  an OpenAI-compatible endpoint here too) runs one task worker at a time, since calls sent to one
  machine's model at once only queue behind each other.
- **The planner's files.** The planner works in the bound workspace, but the files it writes for the
  walkthrough (`plan_steps.json`, `step_artifact.json`) go to the loop's own folder: its brief names
  the absolute path, and the runner (`planning/runner.py`) reads and clears only there. A file of
  that name the planner wrote into the workspace during the pass is moved out; one that was there
  before is the user's own and is never read or touched. Its scratch work (a throwaway copy of the
  workspace, a test run's output) goes in `scratch/` in the loop's own folder, which its brief names
  and the pass removes when it ends; a worker's brief sends its scratch to a `mktemp -d` folder
  (the user's temporary folder), never to a path under `/tmp` by name or into the checkout.

## Cockpit dispatch (frontend)

`web/src/pages/loops/LoopsSection.tsx` dispatches on the loop's kind — after first
redirecting a run-backed loop (`run_id`) to its run page, `#/workflows/runs/<id>`, so every
`#/loops/<id>` link lands:

- `kind === 'design'` → `DesignCockpitPage.tsx` (live canvas, token views,
  and an "agentic build" path that seeds a project-bound chat with the loop id
  so react artifacts tagged `loop:<id>` render on the canvas);
- everything else → `LoopCockpitPage.tsx` (the generic loop cockpit: cycle
  trail, findings, sub-goal prompt bar, artifact/task/project links). It names
  the loop's `work_dir` — `loop.effective_dir`, where the worker's own files land,
  which for a goal/general loop is not `files_dir` — with a link into Files, and a
  failed outputs read renders as an error, never as "No outputs saved yet";
- code loops additionally get the mini-IDE at
  `web/src/pages/code/CodeCockpitPage.tsx` — Monaco-based edit/save,
  PTY-backed build/test commands, and the SDLC stage trail.

## Related docs

- Tasks that loops provision: [tasks-triggers.md](tasks-triggers.md)
- The memory the grill consults: [knowledge-memory.md](knowledge-memory.md)
- Trust/YOLO state a loop worker runs under: [security.md](security.md)
