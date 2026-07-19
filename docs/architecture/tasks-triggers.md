# Tasks, Triggers & Workflows

The "get things done" layer: a task hierarchy shared by chat and the UI,
cron/event triggers with pluggable actions, and SOP-style workflows. Paths are
relative to `PersonalClaw/src/personalclaw/`.

## Tasks

`tasks/` implements a Project → TaskList → Task hierarchy:

- **Persistence** — tasks are one-JSON-per-file under
  `~/.personalclaw/tasks/` (ids `t-<hex8>`, `tasks/native.py`); task lists
  under `tasks/task_lists/`; projects are top-level entities at
  `~/.personalclaw/projects/<id>/` (`project.json` + `context/`,
  ids `p-<hex8>` — `tasks/hierarchy.py`). Projects own context and worktrees,
  which is why they live at the config root rather than under `tasks/`.
- **Rich task model** (`tasks/models.py`) — dependencies, structured exit
  criteria, priorities, an action plan, comments.
- **Dependency reconciliation** (`tasks/reconcile.py`) — on any task change,
  the changed task and its transitive dependents are re-evaluated: a task
  auto-blocks while prerequisites aren't all terminal and auto-unblocks when
  they are. Cancelling a prerequisite counts as terminal (a cancelled blocker
  is "resolved"). Graph walks are cycle-tolerant.
- **Honest APIs** — completing a task with unfinished exit criteria fails
  loudly at the provider layer (`tasks/native.py`: "cannot complete:
  unfinished exit criteria — …"); an invalid status on update is a 400 naming
  the valid set.
- **One registry, every surface** (`tasks/registry.py`) — the chat
  `task_create` tool and the Tasks UI share the same provider registry, so
  a task created in conversation is the same object the board shows.

## Triggers & schedules

- **`schedule.py`** — `CronStore` (file-locked via fcntl, with mtime-based
  `_sync()` so external writes to the store are picked up) +
  `ScheduleService`. Jobs carry a `silent` flag: silent jobs suppress
  auto-delivery (no dashboard notification, no channel post) — the agent
  decides what, if anything, to send.
- **`schedule_history.py`** — run history; **`schedule_script.py`** /
  **`schedule_trigger.py`** — script- and trigger-shaped jobs.
- **`event_triggers.py`** — data-event triggers: `MemoryUpdate` (any memory
  write), `MemoryKeyPattern` (a write whose key matches a glob, e.g.
  `project.acme.*`), `ContentMatch` (value matches a regex/substring). A
  `max_fires` budget auto-disables a trigger once exhausted ("alert me the
  NEXT time X").
- **`nl_to_cron.py`** — natural language → 5-field cron via a constrained
  one-shot LLM call, **validated with croniter before use** (a hallucinated
  expression never reaches the store).

### Action providers

`action_providers/` is the pluggable "what a trigger does" registry: `bash`,
`create_task`, `invoke_agent`, `notify`, `run_prompt`, `run_script`,
`run_workflow`, `send_message` (each `*_provider.py`, with `base.py` +
`registry.py`). Template variables are exported as environment variables for
the bash action.

**Dry-run is honest**: only providers that declare `supports_dry_run`
(run-prompt, run-workflow) actually execute in observe mode; every other
action records a `[dry run]` preview and refuses to run.

### App-manifest crons

Apps can declare crons in their manifest; `apps/app_crons.py` reconciles them
on every app lifecycle transition and registers them `silent=True` always
(headless — the manifest flag is advisory; a failing app cron must not spam
the owner's DM). See [app-platform.md](app-platform.md).

## Workflows

`workflows/` — SOP-style reusable procedures:

- **Store + lifecycle** (`models.py`, `lifecycle.py`, `registry.py`) —
  workflow names follow the skill-name rule (`^[a-z0-9][a-z0-9-]{0,62}$`).
- **Surfacing** (`surfacing.py`) — when a chat message resembles a stored
  SOP's match text, the workflow is offered. The match is embedding-based with
  an honest cosine gate (`DEFAULT_MATCH_THRESHOLD = 0.62`, tunable via
  `config.workflows.match_threshold`), degrading to keyword word-overlap when
  no embedding provider is bound.
- **Invocation** — the `workflow_run` / `workflow_get` tools accept an id OR a
  name (names resolve via the list), so agents can call workflows the way
  users refer to them.
- MCP exposure for agents is `mcp_workflows.py`; schedule tools are
  `mcp_schedule.py` (see the tool-category list in
  [overview.md](overview.md#capability-seams)).

## Related docs

- Loops provision per-phase TaskLists under a project: [loops.md](loops.md)
- Memory writes that event triggers observe:
  [knowledge-memory.md](knowledge-memory.md)
- Where trigger results get delivered: [inbox-channels.md](inbox-channels.md)
