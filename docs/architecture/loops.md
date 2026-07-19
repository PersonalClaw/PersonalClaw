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
| `general` | generic iterative goal in a chat session (nudge + watchdog) |
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
- **`loop/watchdog.py`** detects stalls; `loop/manager.py::reap_orphaned_loops`
  re-arms RUNNING loops after a gateway restart so an interrupted loop
  resumes rather than zombifying.

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

`web/src/pages/loops/LoopsSection.tsx` dispatches on the loop's kind:

- `kind === 'design'` → `DesignCockpitPage.tsx` (live canvas, token views,
  and an "agentic build" path that seeds a project-bound chat with the loop id
  so react artifacts tagged `loop:<id>` render on the canvas);
- everything else → `LoopCockpitPage.tsx` (the generic loop cockpit: cycle
  trail, findings, sub-goal prompt bar, artifact/task/project links);
- code loops additionally get the mini-IDE at
  `web/src/pages/code/CodeCockpitPage.tsx` — Monaco-based edit/save,
  PTY-backed build/test commands, and the SDLC stage trail.

## Related docs

- Tasks that loops provision: [tasks-triggers.md](tasks-triggers.md)
- The memory the grill consults: [knowledge-memory.md](knowledge-memory.md)
- Trust/YOLO state a loop worker runs under: [security.md](security.md)
