---
name: delegation
description: Decide when to spawn a subagent vs. do work inline (cost-tiered) — don't spawn for trivial work; do spawn for parallel independent investigation or isolated long tasks; use wait for external callbacks and hook_register for external-system handoffs.
always: false
triggers: subagent, spawn, delegate, parallel, in parallel, background task, fan out, isolate, offload, wait for, callback, hook, handoff
---

# Delegation

Spawning a subagent has real cost — a fresh context, its own token budget, setup
and synthesis overhead. Delegate when it **pays for itself** in parallelism or
isolation, and do the work **inline** when it doesn't. Match the mechanism to the
shape of the work.

Tools: `subagent_run` (spawn a worker), `subagent_list` / `subagent_status`
(track them), `wait` (block for an external signal), `hook_register` (hand off to
an external system).

## Do it inline (don't spawn)

Default to inline. **Don't spawn a subagent for trivial or sequential work** that
shares your context:

- A quick lookup, a single file read/edit, a one-shot command.
- Anything that needs your current conversation context to make sense.
- Sequential steps where each depends on the last — there's nothing to
  parallelize, and a subagent just adds overhead and a context hand-off.

If spawning would cost more (setup + synthesis) than just doing it, do it.

## Spawn a subagent (`subagent_run`)

Delegate when the work is either **parallel** or **isolatable**:

- **Parallel independent investigation** — several sub-questions or candidate
  causes with no ordering dependency and no shared mutable state. Fan them out at
  once (e.g. probe three hypotheses, research several topics) and synthesize the
  returns. This is the biggest win: wall-clock time drops.
- **Isolated long task** — a self-contained chunk of work that would otherwise
  flood your context with intermediate detail (a deep search, a long grind, a
  bounded build). Hand it off with a clear deliverable so only the result comes
  back, keeping your own context lean.

Give each subagent a **self-contained task and an explicit deliverable** — it
doesn't share your conversation, so spell out what "done" returns. Track them
with `subagent_list` / `subagent_status`. **Don't** fan out units that touch the
same mutable state — parallel workers racing on the same files corrupt each
other; parallelize only genuinely independent work.

(For an open-ended grind toward a goal — "keep working until done" — a goal loop
fits better than a raw subagent; see `loop-worker` and `task-and-project`.)

## Waiting on the outside world (`wait`)

When the next step depends on something **external** that will arrive later — an
async job finishing, an approval, an out-of-band reply — use `wait` to block for
that signal instead of busy-polling or spawning a worker whose only job is to
sleep. `wait` is for "pause until the callback comes", not for parallel compute.

## External-system handoffs (`hook_register`)

When an **external system** should drive future work — an inbound webhook, an
event from another service, a deferred trigger that fires outside this session —
register a hook with `hook_register` so PersonalClaw responds when the event
arrives. This is the handoff for "when X happens out there, do Y here," distinct
from spawning a worker now (`subagent_run`) or blocking on one specific signal
(`wait`).

## Decision summary

- Trivial / sequential / needs my context → **inline**.
- Independent units, want them concurrent → **`subagent_run`** (parallel).
- Self-contained long task polluting my context → **`subagent_run`** (isolate).
- Open-ended grind toward a definition of done → **goal loop**.
- Pause until an external signal/callback → **`wait`**.
- Let an external system trigger future work → **`hook_register`**.
