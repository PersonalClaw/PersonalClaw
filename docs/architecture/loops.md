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
| `general` | generic iterative goal — runs as a **workflow run** (PP-16), see below |
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

| Kind (variant) | Declared done-signal |
|---|---|
| `code`, `design` | `orchestrated` — the per-cycle `on_new_cycle` hook owns done-ness |
| `general` | `verify_command` (optional: no command ⇒ defers to budget by design) |
| `goal:verifiable`, `research:verifiable` | `verify_command`, plus a sub-goal judge when >1 sub-goal is declared |
| `goal:open_ended`, `research:open_ended` | `judge_assessment` (ground truth: `REPORT.md` / `RESEARCH.md`) |
| `goal:monitor`, `research:monitor` | `never` — only a user Stop (or the budget, counted as a clean stop) ends it |

## One listing, two homes (PP-16)

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
  `continue` iteration rows (`_rehydrate_loop_progress`) and re-queues a dispatched stage whose
  subagent this process does not know (`_requeue_orphaned_stages`) — without both, a run past its
  first iteration failed "run deadlocked" after a restart.
- **Ending.** A run with a `loop_kind` announces its end as a loops-table loop does
  (`workflows/attention.py:announce_loop_end`): a `loop_complete` / `loop_failed` notification, and
  a "Loop needs a decision" inbox item when it escalates; a cancel says nothing.

For a loops-table loop, pause, stop and delete also stop the worker's turn IN FLIGHT
(`manager.halt_worker_turns`) — disarming the nudge loop only stops the NEXT cycle — and the cycle
driver checks the loop is still armed before each re-prompt. A restart re-arm keeps the running
stretch's `started_at` (the trust window and deadline are measured from it), and the watchdog's
credited-cycle baseline is durable (`credited.json`), so a restart neither resets elapsed time nor
re-credits a cycle.

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
  zombifying (`LoopWatchdog._boot_sweep`). That sweep runs through
  `concurrency.boot_sweep`, the ONE boot-adoption path it shares with
  `workflows/watchdog.py`. There is deliberately **no gateway boot hook**: a
  hook cannot be retried when it raises, and awaiting it delays startup by however long
  N stranded planner passes take.

## Planning walkthrough & grill

- **`planning/`** (`session.py` data model + `runner.py` state machine) is the
  shared stepwise **gated planning walkthrough** used before launching Code
  and Goal loops: the plan is presented step by step with approve/comment
  gates; a comment triggers a redraft of that step.
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
  worktrees merge back when the phase's tasks finish. A non-git workspace
  falls back to sequential execution.

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
