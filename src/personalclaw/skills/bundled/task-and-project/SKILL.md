---
name: task-and-project
description: Track work as Tasks (the /api/tasks CRUD + native task provider), and decompose larger projects into independent, verifiable units — fanning parallel investigation out to subagents while keeping one coherent plan; hand open-ended goal-driven work to a goal loop.
always: false
triggers: task, tasks, create a task, track work, project, decompose, break down, plan the work, parallelize, work breakdown, goal loop, autonomous, organize work
---

# Tasks & Projects

Bigger work is delivered by **decomposing** it into small, independently
verifiable units, **tracking** those units as Tasks, and then executing them —
some inline, some in parallel, some as an autonomous goal-driven loop.

## Tasks — the unit of tracked work

A **Task** is a tracked, persisted unit of work with a title, description,
status, and dependency links. Tasks live in the Tasks entity, backed by the
native task provider and exposed over the `/api/tasks` CRUD:

- `GET /api/tasks` / `GET /api/tasks/{task_id}` — list / fetch.
- `POST /api/tasks` — create a task.
- `PATCH /api/tasks/{task_id}` — update title, description, or status.
- `DELETE /api/tasks/{task_id}` — remove a task.
- `GET /api/tasks/{task_id}/comments` / `POST` — task discussion.
- `GET /api/tasks/graph` — the dependency graph (which tasks block which).

The dashboard Tasks page is the primary surface — create, organize, and watch
status there. Use Tasks to make a decomposition **visible and durable**: one
task per atomic unit, dependencies recorded, status updated as work progresses
so nothing is dropped.

## Project decomposition discipline

Before executing anything non-trivial, **decompose**:

1. **State the outcome** in one sentence — what "done" looks like for the whole
   project.
2. **Break it into atomic units** — each unit independently doable and
   independently verifiable. If a unit can't be checked on its own, it's still
   too big; split it. Record each as a **Task**.
3. **Map dependencies** — which units block which. Independent units can run in
   parallel; dependent ones must be sequenced. Record the edges in the task
   graph so the order is explicit.
4. **Assign an execution mode per unit:**
   - trivial / fast / shared-context → do it **inline**.
   - independent investigation or an isolated long task → `subagent_run`
     (parallelize the independent ones).
   - open-ended "grind until the goal is met" → a **goal loop** (see below).
5. **Keep one coherent plan** — track units and their state as Tasks so progress
   is visible. Integrate results back into the whole as units complete; don't
   just accumulate fragments.

## Goal loops — autonomous execution toward a goal

When the work is **open-ended toward a definition of done** — "grind on this
until it's met", a continuous-improvement target, a north-star objective — hand
it to a **goal loop** rather than driving it step-by-step yourself. A goal loop
classifies the target, decomposes it, then runs one self-directed cycle per turn
until a deterministic supervisor (or a separate judge) decides the goal is met,
at which point it self-retires. Goal loops are the unified autonomous goal
engine; manage them from the dashboard Goal Loops page. The per-cycle worker
protocol lives in the `loop-worker` skill.

Use a goal loop instead of trying to specify every step up front: you set the
goal and the definition of done, the loop decides direction each cycle.

## Parallel work with subagents

When several units are **independent** (no shared mutable state, no ordering
dependency), run them concurrently with `subagent_run` — e.g. investigate three
candidate root causes at once, or research several sub-questions in parallel —
then synthesize the returns. Keep each subagent's task self-contained with its
own clear deliverable so the results compose cleanly. (See `delegation` for the
spawn-vs-inline cost rule — don't spawn for trivial work.)

## Discipline

- **One unit = one verifiable step.** Resist doing "the whole thing" in a single
  uncontrolled pass.
- **Define done before starting** — for the project and for each unit.
- **Track it as a Task** — a unit that isn't recorded gets dropped.
- **Don't fan out work that shares mutable state** — parallel workers stepping on
  the same files race. Parallelize only genuinely independent units.
- **Re-plan when reality diverges** — if a unit reveals the breakdown was wrong,
  update the plan and the tasks rather than forcing the old one.
