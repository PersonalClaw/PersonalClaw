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
- **Due dates announce themselves** (`tasks/due_notices.py`) — a gateway
  loop (`gateway._task_due_loop`: once the dashboard is up, then every five
  minutes; off under `--no-crons`) sends one `tasks/due` notification per due
  date: at 09:00 the day before a date-only due date (what the task form
  writes), 24 hours before one with a time. What was sent is recorded as
  `{task_id: due}` in `task_due_notices.json` (its own durability entry), so a
  restart neither repeats a notice nor loses one — a notice missed while the
  gateway was down goes out as it starts again, unless the due moment is more
  than a day gone. Moving the date re-arms it; a record is dropped only once
  its date can no longer be announced, never because one sweep did not see
  the task. Quiet hours hold the notice until the
  window ends (the gate would otherwise drop an INFO note); mute and a raised
  minimum severity mean "not at all". Only the owner's open tasks are
  announced, and only while the task's `due_reminder` is on — the per-task
  opt-out beside the Due field in the task form.

## Triggers & schedules

- **`schedule.py`** — `CronStore` (file-locked via fcntl, with mtime-based
  `_sync()` so external writes to the store are picked up) +
  `ScheduleService`. Jobs carry a `silent` flag: silent jobs suppress
  auto-delivery (no dashboard notification, no channel post) — the agent
  decides what, if anything, to send.
- **`schedule_history.py`** — run history; **`schedule_script.py`** /
  **`schedule_trigger.py`** — script- and trigger-shaped jobs.
- **Event triggers** — a `kind: "event"` row in the one trigger store
  (`triggers.json`), made by the Triggers page's **Data event** form, the
  chat's `automation_create` or `POST /api/triggers`. It fires when
  PersonalClaw's own state changes: a memory write (`MemoryUpdate`,
  `MemoryKeyPattern` — a key glob such as `project.acme.*`, `ContentMatch` — a
  value regex), an accepted inbox message (`InboxMessage`, `InboxSender`,
  `InboxAddress`) or an app's trigger-source event (`AppEvent`, a glob on
  `app:<app>:<event>`). The pattern grammar and the bus are
  `event_triggers.py`; every source calls its `emit_event`. In the gateway the
  router (`triggers/event_fire.py`) matches the store's event rows, admits each
  match through the gate walk a clock fire takes (`service.admit_fire`:
  incident, spacing, rate, quiet hours, budget, overlap claim, capability
  fence) and runs it through the one store dispatch
  (`gateway._fire_store_trigger`) — injection screen, fence, denylist, rung
  ladder, run record, delivery — so an event fire leaves a history row like
  any other. `gates.max_fires` switches the trigger off once spent ("alert me
  the NEXT time X"); `gates.debounce_secs` (5 s unless set) collapses a burst,
  and at most 30 event fires a minute run across all event triggers. A process
  with no gateway — the CLI, or the `mcp-core` server an agent's memory tools
  run in — parks an event a stored trigger wants in `trigger-spool.jsonl`, and
  the gateway's next tick re-emits it. An agent-lifecycle event (a session
  ending, a tool call) is a **lifecycle** trigger, not an event trigger.
- **Legacy automation files** (`triggers/legacy_import.py`) — `crons.json`,
  `event_triggers.json` and `autonudge.json` held automations before the one
  store. All three are imported by the boot pass
  (`boot_migrate.migrate_and_arm`, before the dashboard is up) **once per home**
  and renamed `<name>.imported-<date>`; a copy by that name (or the `.migrated` an earlier
  build left) means the import happened, so a file found again is not read and
  the Doctor names it (`automations.legacy_files`). An imported row is
  `created_by: import`, carries no capability block and none of the step keys
  that loosen whether its agent asks (`automation_posture.loosened_keys`), and
  one that would run anything needing a grant — or a nudge, which types into a
  chat — arrives switched off (`needs_review` on the wire). Switching it on is
  the only grant: `POST /api/triggers/{id}/toggle` answers
  `confirmation_required` until the owner consents, then freezes the providers
  (`screen.grant_action`), makes the row theirs and writes the grant to the SEL.
  The boot's `backfill_capabilities` skips imported rows, the chat's
  `automation_resume`/`automation_update` refuse to switch one on, and a manual
  run (`POST /api/triggers/{id}/run`, which the chat's `automation_run` and
  `schedule_trigger` also use, and whose dispatch does not read the capability
  block) is refused until it is on. One Inbox
  item (`cron/trigger_import`) lists what waits, raised once the dashboard is
  up from the rows still waiting, so a crash between the import and the
  announcement still announces it — once. The legacy MCP store
  `settings/mcp.json` is not read at all (the Doctor names a server left in
  it, `tools.legacy_mcp_settings`).
- **`nl_to_cron.py`** — natural language → 5-field cron via a constrained
  one-shot LLM call, **validated with croniter before use** (a hallucinated
  expression never reaches the store).

### Action providers

`action_providers/` is the pluggable "what a trigger does" registry: `bash`,
`create_task`, `invoke_agent`, `notify`, `run_prompt`, `run_script`,
`run_workflow`, `send_message` (each `*_provider.py`, with `base.py` +
`registry.py`). Template variables are exported as environment variables for
the bash action.

**A trigger dry run executes nothing and records nothing.** `POST
/api/triggers/{id}/run {"dry_run": true}` (the **Dry run** button) answers with
the gate plan a hand-run would apply and `would_run` — the action a real run
would dispatch, as `{provider, config}` — and that answer is the whole result:
no run row, no `last_run_ts` move, so the panel renders it instead of waiting
for one. `supports_dry_run` (run-prompt, run-workflow) is a provider's own
observe-mode capability, reported by the Doctor's would-execute simulator.

**One notification per fire.** A fire's completion report ("X finished" /
"X failed", `triggers/delivery.py`) carries `statusUrl` back to the trigger.
When the action IS a dashboard notification (`notify`), a successful fire
sends no report — the action's own note is the notification, and it carries
the `statusUrl` itself (`ActionContext.status_url`). A failed notify still
reports.

**A Run workflow action names its workflow, and is checked where it is saved.**
Its form comes from the bundled `apps/native/run-workflow-action/app.json`: a
picker of your workflows, then the chosen workflow's declared inputs as fields
(`web/src/pages/workflows/workflowWidgets.tsx`). Saving or editing a trigger
asks `run_workflow_provider.config_problem` the question the Run button's start
asks (`workflows/contracts.start_problem`: the workflow exists, every required
input is given, every input is its declared type), so a trigger that could
never start its workflow is refused with that sentence instead of failing at
every fire. A fire asks again, because the workflow can change after the
trigger was saved, and starts the run with the declared defaults applied.

**The 900s cadence floor is for model calls.** `MIN_CLOCK_INTERVAL_SECS`
warns only when the action can call a model: providers listed in
`triggers/models.py`'s `ZERO_TOKEN_PROVIDERS` (bash, notify, the digests, …)
are exempt, and anything unlisted — app-contributed actions included — keeps
the warning.

### After a restart: missed and interrupted runs wait for you

Two boot passes find work that did not happen, and neither re-runs it on its
own: running a 3am job at 9am is sometimes right and sometimes exactly wrong,
and a run a restart cut off may already have done part of its work.

- **Missed slots.** `triggers/missed.review_at_boot` walks each active,
  enabled schedule from its armed slot to now, on the interval grid or, for a
  cron, by walking the expression (`missed.cron_slots`). A paused or disabled
  schedule missed nothing, and neither did one whose next fire does the missed
  one's work (`models.MISSED_FIRE_SUPERSEDED_PROVIDERS`: the HEARTBEAT.md
  queue).
- **Interrupted runs.** `triggers/reaper.terminalize_orphans` closes a run
  whose process is gone with the status `interrupted`
  (`RESTART_INTERRUPTED_STATUS`) and the reason in `error`. It is not
  retried.

Both land in `trigger-review.json` beside `triggers.json`
(`triggers/review.py`): one card per trigger and kind, until you decide it.
The "Missed scheduled runs" notice points at the Triggers page, where the
cards sit above the list (`GET /api/triggers/review`). The boot passes run
before the dashboard exists, so the gateway holds the notice and sends it once
the dashboard is up (`GatewayOrchestrator._surface_held_boot_review`). **Run now** runs the
action once, however many slots the card covers, and records it `ran_late`
with the reason; **Dismiss** records `skipped_missed`. Both go through
`missed.resolve_missed` and land in that automation's history
(`POST /api/triggers/review`).

### The HEARTBEAT.md queue is a system trigger

`workspace/HEARTBEAT.md` is the queue the agent writes "keep checking until
done" work to. It is read by one system trigger, `system:heartbeat-tasks`
("Heartbeat tasks", every 60 s, created at boot by
`action_providers/heartbeat_tasks_provider.reconcile_heartbeat_tasks_trigger`),
so it is listed with the other automations, with its runs, and switched off
or slowed there. The boot reconcile converges only its action, never its
switch or its cadence. Each task still runs as the gateway's heartbeat turn
(`heartbeat.set_task_runner`), and `HEARTBEAT_KEEP` keeps an unfinished task
for the next pass. A pass over an empty queue reports `skip`, which the run
history records as the inert `skipped_noop` (`schedule_history
.status_for_result`, the one status rule the fire path and the Run button
share), so it folds out of the default history. The heartbeat loop itself no
longer reads the file.

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
