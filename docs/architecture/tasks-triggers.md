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
  window ends (inside it the gate would suppress its ping); mute and a raised
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
  how it is allowed: `POST /api/triggers/{id}/toggle` answers
  `confirmation_required` until the owner consents, then grants the providers,
  makes the row theirs and writes the grant to the SEL (**Grants**, below). One Inbox
  item (`cron/trigger_import`) lists what waits, raised once the dashboard is
  up from the rows still waiting, so a crash between the import and the
  announcement still announces it — once. The legacy MCP store
  `settings/mcp.json` is not read at all (the Doctor names a server left in
  it, `tools.legacy_mcp_settings`).
- **Grants** (`triggers/grants.py`) — a trigger runs only what its frozen
  `capabilities` block allows: a read-only action needs nothing, and every
  other one needs its provider listed (`screen.ungranted_providers`). An
  action is read-only by what it declares (`ActionProvider.effect`), never
  by its name: `call-app-route` reads only when the app declares the route
  `readOnly`, and an action that declares nothing is a change. Both
  dispatches check it — the attended one (`_dispatch_store_action`: Run now,
  the restart review's Run now, a view refresh, a webhook fire) and the
  unattended one (`gateway._fire_store_trigger`: clock, event, file,
  web_watch, chained) — and a refusal names the missing action and how the
  owner allows it; an unattended refusal is a `skipped_gate` row in the
  trigger's Runs history. A grant is for the action as the owner allowed it:
  an edit that changes what a granted action runs (another command, URL,
  prompt, agent or workflow) keeps no grant for the change, and neither does a
  provider the edited action stopped using (`grants.narrow`); the step keys
  that decide whether its agent asks you are asked about on their own, when
  they loosen. Only the owner grants, by saying yes: creating a trigger whose
  action needs one (the create dialog asks), saving an edit that needs one (the
  editor asks, in the same question as a loosened approval posture), switching
  a trigger on (or Allow on one that is on, the same toggle sent on again), and
  `personalclaw cron add|update --yes`, which asks the same questions in the
  terminal and changes nothing without it. A trigger the chat makes
  (`automation_create`, `set_onetime_task`, `set_recurring_task`) is made with
  no grant, so one whose action needs a grant does not run until the owner
  allows it (words sent as written, `automation_create`'s `say`, need none and
  are active at once); a chat edit that needs a grant is
  saved switched off; a chat edit that loosens the posture is refused; and the
  chat cannot switch such a trigger on or run it. An action that starts an
  agent (`invoke-agent`, `run-prompt`) starts it on its trigger's grant: the
  owner allowed it "to use the “Invoke Agent” action when it runs", so the
  agent's start does not ask again (`grants.allows_its_agent`, bounded by the
  operator ceiling like every grant), and the agent's own calls ask as any
  agent's do. PersonalClaw's own triggers
  are granted by the code that makes them, since each runs an action it fixes
  behind a switch the owner holds (an app's crons, the `system:*` singletons, a
  research report's schedule, the triage digest, the Self-QA watch, a logged
  decision's review card). Nothing unattended grants: there is no boot
  backfill, a pack's triggers are deployed with no grant, and a legacy import
  grants nothing. A lifecycle trigger carries the same grant and meets the same
  rule when it fires (`hooks.run_script_hook`); on the gating seam an ungranted
  one blocks the tool call it was asked about rather than letting it through.
  The wire carries `needs_grant` (display names) so the page can badge the row
  and offer Allow. A `webhook` trigger that is switched off or paused answers
  `/fire` with the 404 an unknown one gets. Each question is the gateway's,
  heading included (`http_errors.consent_required` takes a `title` from every
  caller): "Allow what this trigger runs?", "Allow the changed action?", "Allow
  this trigger to run?", and "Loosen a security setting?" only for a posture
  that loosens — a question with both halves says both.
- **Callbacks the agent registers** (`webhook_callbacks.py`) — the chat's
  `hook_register` saves context for a later `POST /api/hooks/agent`, which
  starts an agent turn, with the agent's tools, from that context. Callbacks
  live in `webhook_callbacks.json`, which only that module writes; the
  lifecycle trigger store (`hooks.json`, `hooks.ScriptHookStore`) is the
  owner's, and a store that meets an entry that is not a trigger skips it
  rather than failing to load. A callback follows the grant rule: registered
  switched off, listed on the Triggers page as a **Callback**, and allowed by
  switching it on, which asks first and names the context the page read (its
  seal), so a callback registered again with other context waits again. A post
  naming a callback the owner has not allowed answers `403 not_allowed`; a
  session key nobody registered is the owner's own integration.
- **A `when` is one time or a cadence, never a cron for one time**
  (`triggers/tools._read_when`). An explicit time — "at 5pm", "in 20 minutes",
  "tomorrow at 9am", "2026-10-01 14:00", "at 9am Europe/London" — is read with
  no model by `triggers/when.py`, in the owner's zone (`timezones`), into a
  one-time `at` trigger that runs once at that time and then switches itself
  off. Only a phrase that leaves the time to judgement ("tomorrow morning",
  "later today") goes to **`nl_to_cron.py`**, which hands the model the clock
  and the zone and reads its answer as either a 5-field cron expression,
  **validated with croniter before use**, or `ONCE <local time>`, which must be
  still to come (a hallucinated answer never reaches the store). Each chat
  tool keeps its promise: `set_onetime_task` refuses a cadence and
  `set_recurring_task` refuses one time, each naming the tool that fits, and
  `automation_create` takes either. The created message says what the time
  was read as ("read as one time: today, 5:00 PM PDT"). An agent's one-time
  task expires a day after its own time at the earliest, so one set further
  out than the week-long default still runs.

### Action providers

`action_providers/` is the pluggable "what a trigger does" registry: `bash`,
`create_task`, `invoke_agent`, `notify`, `run_prompt`, `run_script`,
`run_workflow`, `send_message` (each `*_provider.py`, with `base.py` +
`registry.py`). Template variables are exported as environment variables for
the bash action.

**`run-prompt` runs its saved Prompt, else its own message, else `loop.md`.**
An automation the chat makes carries its instruction as the action's `message`
(`tools.create`), and the provider runs it, framed as an unattended turn; the
Triggers page shows it as the action's Prompt and lets you edit it as
**Message**. Before, the provider read no `message`, so every automation made in
chat failed on its first fire, or ran the owner's `loop.md` in its place.

**Words for the owner are a `send-message`, on the channel they named.** When
the chat is asked to send the owner words ("every Wednesday at 18:00, message me
on Telegram: 'Bins out tonight.'"), `automation_create` takes them as `say` and the
channel as `via`, and makes a `send-message` action: nothing runs but the send,
and it goes out on that channel alone (the action's **Send on**). A name that is
not a chat channel set up here is refused with the ones that are, so the chat
asks which. When the channel cannot deliver at the time, the words go to the
Inbox saying why (`channel_delivery.reach_owner`'s `only`); no other channel
stands in. Before, the chat could only make an agent task, and nothing could
name the channel a message for the owner went out on: it went to the first
connected channel by name, Discord ahead of Telegram.

**A task's result goes to the channel the owner named, too.** With `message`
instead of `say`, `via` (and `to`, a chat on that channel by the channel's own
id) sets the trigger's `delivery` to `channel:<name>[:<chat>]`: the route the
Triggers page's Notify channel writes, delivered by the same code. Without `to`
it is the owner's direct messages there; an id the channel does not take is
refused in the channel's own words before anything is saved. Without `via`, a
task's result is a notification (`inbox`), as one made on the Triggers page is;
only words in `say`, which are their own delivery, are made with `none`.

**A route is checked where it is written.** The chat's and CLI's
`automation_update` take `delivery` and `failure_delivery` through
`delivery.written_route`: the vocabulary the store reads a route with
(`is_valid_route`), and a channel route's channel set up here. A route's own
word in another case (`Inbox`) is that route, and a chat channel set up here
named by its name (`telegram`) is its direct-messages route, as `via` reads it.
Anything else is refused with what a route can be and the channels set up here,
and nothing is saved. Before, any value was saved and answered "Updated", and
the store read one outside the vocabulary back as `inbox` with a warning, so
results went somewhere other than where the chat said. An update's result, like
a create's, says where results and failures go and whether the automation runs
now, read from the row as saved.

**"When a run finishes" waits on the work, not its start.** A `run_completed`
trigger waits on one workflow run (`source_run`), any run of a workflow
(`source_def`) or another trigger's work (`source_trigger`). A workflow run's
end fires the ones waiting on it, its workflow and the trigger that started it
(`EngineServices.run_ended`), and an agent task's end the ones waiting on its
trigger; a fire whose action only started its work chains when that work ends.
The chained action is told which run ended, how, and what it said it produced
(fenced, like every value from outside). `automation_create` reads the run from
`when` ("when the research run 9c2c10ab finishes", "when my nightly run
finishes"): an id is the run, a name the one run going under that workflow, the
one trigger by that name, or the workflow, and anything it cannot place is
refused with the runs going now. The Triggers page makes one as **Run
finishes**. A chain carried through a workflow run keeps its depth and path on
the run, so a loop through a run is refused as a loop.

**A Send message action is checked where it is saved.** The Triggers page's
create and edit (`_action_problem`) and the chat's and CLI's `automation_create`
and `automation_update` (`tools.unsendable_message_refusal`) ask
`send_message_provider.config_problem`: its **Send on** must be a chat channel
set up here, a channel id must be one that channel takes, and an id with no
channel named must be one exactly one chat channel set up here takes, as its
fire requires (`docs/architecture/inbox-channels.md`, "An id goes out on the
channel that issued it"). Before, a name for a channel that isn't set up saved,
and every fire failed.

**A trigger dry run executes nothing and records nothing.** `POST
/api/triggers/{id}/run {"dry_run": true}` (the **Dry run** button) answers with
the gate plan a hand-run would apply and `would_run` — the action a real run
would dispatch, as `{provider, config}` — and that answer is the whole result:
no run row, no `last_run_ts` move, so the panel renders it instead of waiting
for one. `supports_dry_run` (run-prompt, run-workflow) is a provider's own
observe-mode capability, reported by the Doctor's would-execute simulator.

**A run's history row says what the action did.** Both recorders write the
row's summary as the sentence the action wrote for a person
(`ActionResult.summary`, `schedule_history.summary_for_result`), else what it
printed, which stays the row's trace. A browse run prints its whole account as
JSON for the workflow engine to bind, and says "Browse finished in 3 steps at
example.com. Noted: …" for its row. The other providers whose `stdout` is JSON
write one too: run-workflow says what it started, queued or skipped and why
("Started “triage-inbox” as run 3f2a91c0."), net-fetch how much it read and from
where ("Fetched 1,234 characters from example.com (HTTP 200, text/html)."), and
inbox-op what it did to which message ("Archived the message from alice in
#general."). A sentence names a site by `host[:port]` and a message by who sent
it and where, and quotes nothing either of them said. The row's line flattens
markdown (an agent's reply is markdown) but strips markup only where it is
markup (`scheduleMeta.mdToPlain`), so the `#`, `-` and `_` a name is spelled with
survive; and since the line is cut to the panel's width, an opened row says the
whole sentence above the trace.

**A Run button says what its run recorded.** `POST /api/triggers/{id}/run`
answers `status`, the status the run recorded (`last_run_status` on the list row
reads the same record), and its `result` is the row's own line for a run that
stopped for you ("Waiting for you. Sign in to …") rather than "ran". So the
schedule panel says "Waiting for you", "Launched" or "Queued" under its Run
button where that is what happened, and "Run finished" only for a run that did
its work, for a couple of seconds, as a status (announced, at full strength; it
used to be the disabled button's own label, drawn at 40% opacity); the
automation panel and the restart review's Run now say the same. The line used
to read the trigger's health rollup, which says how the automation has been
going and nothing about this run.

**An automation from another home arrives switched off, with nothing of what
happened to it there and no grant.** Every way a row from another home comes in
applies one rule, `triggers.store.arrived_from_another_home`: a snapshot restore's
or archive import's merge (`snapshot._merge_triggers`, into a home with no store
too), a device sync (the `triggers` inventory entry's `arrives`, applied by
`durability.reconcile`), and a sync conflict resolved with the other machine's
version or a drafted merge (`durability.conflict_resolve`) for an automation this
home no longer has. It keeps what the
automation is (`triggers.store.what_it_is`) and drops `RUNTIME_FIELDS`: its armed
fire, run count, success, failure and waiting stamps, debounce and park stamps,
alert dedupe, health and state, its switch, and its grant (`capabilities`), with
the step keys that loosen whether its agent asks. So it arrives with
`enabled: false`, and switching it on here asks first for what it runs, as the
Triggers page asks of any row that holds no grant: a yes is given where the owner
is shown what runs. A lifecycle hook from another home arrives by the same rule
(`hooks.hook_arrived_from_another_home`, `HOOK_RUNTIME_FIELDS`), through the `hooks` inventory
entry, which a sync, a restore's merge and an import all apply (`durability.reconcile.bring_in`
for the last two). A standing research report arrives the same way, switched off and without its
runs there (`knowledge.research_reports.arrived_from_another_home`): it runs on its cadence and
each run is a model call.

A sync reconciles `triggers.json` and `hooks.json` one entry at a time
(`StateEntry.records`), although each is exported as one file: a peer's
automation lands in a home that has automations of its own, each where this home
keeps it and the new ones after them, and a pull that brings nothing leaves the
file as the store wrote it. Two homes compare only what a person makes of an
automation (`StateEntry.compared`), so a fire, a switch or a grant in either is
never an edit to review; an edit in both is a conflict on that one automation.
"Keep this machine's" writes nothing: the automation stays as it is now.

An edit made in only one home reaches the other. The sync merges each record
three ways, against the version the two homes last agreed on (kept on each
home, per peer: `durability.ancestors`): changed only there, the other home's
edit is taken in; changed only here, this home's stays, whatever either clock
says. For an automation or a hook the other home's edit is what the automation
is (`merge.forward` over `StateEntry.compared`), and this home's own part stays:
its switch, what happened to it here, and its grant, which keeps only what the
edited action still runs as it ran here, so a changed command waits for the
owner's yes here (`triggers.store.edit_arrived_from_another_home`,
`hooks.hook_edit_arrived_from_another_home`). A new cadence re-arms the next
fire, by the rule every edit made here follows (`arm.next_fire_after_edit`). "Take the other
machine's version" in the conflict review, and its drafted merge, take the
version in by the same rule (`durability.reconcile.take_in`): the automation stays
switched on or off as it is here, and what it runs differently asks first. It
used to come in as an automation this home did not have, switched off, so taking
the other machine's schedule stopped one that ran here.

**What a run is told, and what it may change.** An action that starts an agent
(`run-prompt`, `invoke-agent`) hands it the automation's instruction followed by what
started this run (`triggers.fire_facts.describe`): the file that arrived (its path, its
name, and whether it was added, changed or removed), the Inbox message with each
attachment's text, the page's new items, the webhook's body. All of it is data from
outside, each piece in its own fence, and a Run now or a schedule adds nothing. The file
that arrived is one the run's file tools may read (`ActionContext.fire_files` →
`SubagentInfo.may_read`). A run that fires on its own is read-only; an action may name the
files its job changes (`writes`, "Files it may change": at most ten full paths, files or
folders), and its agent may then write those with the native file tools and nothing else:
every other change and every command stays refused (`write_scope.admits`,
`guardrails.policy.declared_tool_grant_denial`). A path inside PersonalClaw's own files
(its workspace excepted), a credential location or the home folder itself is refused when
the automation is saved and again when it fires (`write_scope.problem`). `writes` is part
of what the action runs, so an edit to it asks the owner again, and the Allow says what its
agent may do in words: only read, change only the files named, or change files, run
commands and send messages, and, for a reading run, that it may message the owner in
PersonalClaw or on a chat channel they connected (`notify`, to the owner only), the one
thing such a run may send. One mapping turns a step's posture into its run
(`automation_posture.agent_run_policy`): the action builds its agent's run from it, the
run's tool policy is built from what that run is handed (`subagent_tier.tier_for`), and
the Allow is the policy's own sentence, so the two cannot drift apart (a rail drives both for
every posture). A run whose own limits refused calls it made (its grants, or an approval
nobody was there to give) is recorded as `refused`, naming the calls, rather than as a
success (`triggers.settle`).

Two facts outside the step decide what its run may do too, and the Allow says both as they
stand. A working folder (`cwd`) the owner has not trusted is in Preview
(`guardrails.project_trust`): its agent only reads, whatever write access the step asks for,
for Run Prompt and Invoke Agent alike (one check, `automation_posture.fire_policy`), and the
Allow names the folder and what trusting it gives. The files the Allow names one by one stay
its to change. The first fire a folder holds back records it as Preview and asks the owner
once, in the Inbox, where the request offers Trust; the trigger's panel says why its agent is
held back and offers Trust too (`held_back` on the trigger). A step that only reads is not
held back by Preview and asks nothing. An agent that runs on an agent CLI changes files with
its own tools, which no scope can be held to, so it is given none (`write_scope.not_held_on`):
saving files to change for one is refused, its Allow says it may not change them, and a run
that reaches a CLI anyway (an agent inherited from the session that started it) gives them
up. When either fact changes after the Allow, the run follows it as it is now and never goes
past what the Allow named for that case: a run held back says why in its history, in its
trigger's last error and in the note it sends when it ends (`SubagentInfo.held_back`).

**What another machine's owner allowed is theirs.** A workflow step's
`approval_mode: auto` and `capability: mutating` are the owner's yes, given where
a save shows them, so a workflow definition from another machine arrives without
either on any step, and another machine's edit keeps what this home allowed only
on the steps it left as they were (`automation_posture.workflow_what_it_is`,
`workflow_edit_arrived`); a tightening value comes as written. A runner
definition under `runners/` names the program PersonalClaw runs for a Check and a
second opinion, so it runs only once this home's owner allowed what it runs,
sealed to it (`agents.runner_grants`, in `grants/`, which no sync or export
carries): one from another machine waits for Allow on its row in Settings →
Agent defaults, and so does another machine's edit to one allowed here. An agent
file's `approval_mode` does nothing until the owner here adds the agent, which
asks first for a looser one.

Every writer of `triggers.json` holds the trigger store's own lock
(`.triggers.lock`, `record_files.locked`) and re-reads the file under it: the
store's mutations, a sync, a restore's merge and an import alike. Each writes it
through the one JSON writer (`atomic_write.atomic_json_write`), so the file is
0600 under the home and its write is announced to the post-write subscribers,
as every store's is (`tests/test_every_store_writer_uses_the_shared_writer.py`). One that read
the file, merged and wrote it back while the store added an automation used to
write that automation away. Every other file of user records is written the same
way — the inbox, the document comments, the research reports, the tags, the tag
boards, the folders, the dashboard's views and the hooks each have one lock their
store and those paths hold — and a store that holds its records in memory keeps,
when it writes, what another writer put there since it last read the file.

A store whose every record is a grant never takes another machine's in by a
sync: which projects run their scripts (`project_trust.json`), which actions run
without asking and their undo handles (`autonomy_rungs.json`,
`autonomy_reversals.json`), and which integrations may connect
(`inbound_clients.json`, `inbound_tokens.json`). They are `replace_only`, as the
configuration is: restored whole or not at all, and left as they are by a pull.

**What is one machine's own stays on it.** Its model spend by day, which its
budget caps count (`spend.json`), its tool counters (`tool_usage.json`), its
context-savings ledger (`tokenjuice_savings.json`), when its own backups and
syncs last ran (`durability_state.json`), which due-date notices it sent
(`task_due_notices.json`), and the legacy files each home imports once
(`crons.json`, `event_triggers.json`, `autonudge.json`) are `machine_local`: a
snapshot and a backup carry them, a sync never does, a pull leaves this
machine's as they are even from a peer that still sends one, and the review
closes an old conflict on one only by keeping this machine's. Merged as one row,
two machines could never agree on a counter, so once both had moved every pull
was a conflict to review. A merge restore and an archive import leave the
counters, the scheduler's state and the due-date notices as they are here too
(not `merged_in`): a merge took in the days of spend this machine had none for,
so another machine's dollars counted against its budget caps, and another
machine's scheduler marks read as backups this one had just taken. A replace
restore brings them back with the whole home. The same holds for files of a
synced folder
(`machine_local_within`): the agent CLI's runtime config
(`agents/personalclaw.json`), which lists what this machine's owner lets it run
without asking and the servers it starts, each runner's health as this machine
measured it (`agent-metadata/*.runner.json`), which picks the runner a second
opinion fires, and the template nudges' counters and candidates
(`workflows/template_nudges.json`, `workflows/template_candidates.json`).

**What ran on a machine stays on it too.** A workflow run's records (the run
ledger `workflows/runs.db` and each run's folder in `workflows/runs`), a
loop's (`loop/loops.db` and its folder in `loop`) and an agent's (its folder in
`subagents`) are `machine_local`, and not `merged_in`: a sync never carries
them, a pull never takes another machine's in (`durability.db_merge` leaves
them even from a peer that still sends them), and a merge restore or an
archive import leaves the archive's out. The workflow watchdog adopts every
active run it finds and resumes it from its journal, the loop watchdog's first
poll re-arms every running loop, and the start stops every agent a folder says
is running, by its recorded process id, so another machine's run or loop used
to run a second time here, on this machine's files, and another machine's
agent could name a process of this one. A backup carries them. Named
workspaces (`workflows/workspaces`), the working folders runs share, stay on
each machine by the same rule.

**A replace restore holds what was in flight.** A replace restore brings the
records back with the whole home, and a snapshot is a moment in the past: a run
or a loop that was working then has gone on since, on the machine it ran on or
here. So each one it brings back working is held (`snapshot._hold_what_was_in_flight`):
a workflow run running, waiting on a gate, or queued to start is paused with a
pause that holds across a restart, and says why; a loop running or planning
waits for you, asking to be resumed; an agent's folder is closed as restored,
so its recorded process is never signalled. Resume takes each on from where
the snapshot left it. This holds whichever machine took the snapshot, since
this machine's own run went on after its snapshot too.

**Every file of a synced folder is carried.** The hourly export and a sync
read every file of a folder store, not its JSON files alone
(`durability.shards.read_entity_dir`): a JSON file as its data, any other file
as its text or, when it is not text, its bytes. Saved prompts and prompt
snippets are YAML, and neither reached the export or another machine before. A
file the export cannot carry (JSON that does not parse, a file over 36 MiB, one
named as another file's row) is named, with why, in the export's result and the
sync's report, never dropped in silence. The append-only stores that are
folders of files, one per chat, scheduled job or channel (`sessions/`,
`cron-history/`, `history/`), are not synced: their rows, read as one stream,
name no file to go back to, and a pull wrote them into a file named for the
year beside the store's own.

**The snapshot is the backup; the shards are a copy of the records.** A
restore reads a snapshot, which holds every store whole: a folder with every
file at its path, a database through the backup API. The shards — the hourly
export and what a sync carries — hold the stores whose content is records, and
each database as rows, and nothing restores a home from them. A folder of files
(skills, cron scripts, uploads, the workspace, installed apps, hooks) is in
neither the export nor a sync: each machine keeps its own, and its snapshot
holds it. The shards used to carry each folder's files as blobs named by their
content, with no path, so nothing could put one back where it was, and every
sync cycle uploaded all of them again. Carrying them with their paths would
carry them into the other machine: app bundles, hook and cron scripts, and
skills that run what they hold, arriving without this machine's owner's yes.
Settings → Backups, `personalclaw backup export` and `personalclaw backup
validate` say which to trust.

**A pull writes only what changed, over what it read.** It writes back only
the files the merge changed, and replaces a file only while it still holds what
the pull read (`durability.writeback.apply_rows` compares its sha256): a file
this machine wrote in between is left as it is and is not agreed on, so the
next pull takes the other machine's version in again against it. A one-file
append-only store is only appended to. A row another machine names by a path
that is not a file of the store (outside it, a database, a lock) is never
written. The conflict review writes the same way, and refuses (`moved`) when
this machine's file changed while it wrote.

**A pull writes nothing outside what a sync may write.** Every path another
machine names is resolved before anything of its change is written: each
object's key, each path its export's manifest declares, the file each of its
rows stands for, and its machine id, which names its folder of the remote. One
that is not inside the export it came in or the store it names, every symlink
on the way followed (`record_ids.is_path_in_store`), is refused: nothing of
that change is taken in, the cursor moves past it, and the sync report names
the path and why (`durability.pull_engine`), as a run that did not go well. A
pulled key used to be joined onto the pull's scratch folder as it came, so
`../` in one wrote a file anywhere this machine's user may.

**A merge restore and an import take a folder in by the sync's rule.** A merge
restore or an archive import brings a folder store's files in the way a sync
brings another machine's (`durability.reconcile.bring_in_folder`): each file
this home lacks arrives, a workflow without its steps' `approval_mode: auto`
and `capability: mutating`; the files it has stay exactly as they are; and
what stays on each machine (`agents/personalclaw.json`, the runner health, the
template nudges) never comes in. Copied whole, an archive's workflow ran its
steps here as its machine's owner had allowed them there, and its agent
runtime config the tools that machine's agent runs without asking.

**No other machine gives an agent here a tool back.** An agent file's
`managedToolPolicy.exclude` lists the tools its sessions are kept from, and the
list is each machine's own (the `agents` inventory entry's `compared` and
`edit_arrives`): an agent another machine makes arrives with its list, and
another machine's edit to an agent this home has takes in everything but the
list, which stays as it is here. Two homes never compare it, so a difference in
it is never an edit or a conflict. It synced as written, so a tool the owner
kept from an agent here came back when the other machine's copy dropped it.

**One notification per fire.** A fire's completion report ("X finished" /
"X failed", `triggers/delivery.py`) carries `statusUrl` back to the trigger.
When the action IS a dashboard notification (`notify`), a successful fire
sends no report — the action's own note is the notification, and it carries
the `statusUrl` itself (`ActionContext.status_url`). A failed notify still
reports.

**Every run reports on its route.** A scheduled fire and every run the attended
dispatch makes (`trigger_runs._dispatch_store_action`: Run now, the restart
review's Run now, an answered park, a webhook fire, a view refresh) report
through one reporter, `delivery.report_run`, with the same words, route and
dedup. The route was the scheduled fire's alone, so a Run now of a trigger set
to report to a chat channel told that channel nothing.

**A report says what the run produced.** "X finished" carries the action's
sentence for a person, else what it printed (`schedule_history.summary_for_result`),
and "X failed" says why (`schedule_history.failure_for_result`): its error, else
its exit code and the end of what it wrote to stderr, which is also the history
row's error. The words are sized for where they land (`delivery.excerpt`): up to
1,200 characters on the notification, whose first line is the bell's, and 3,500
in a chat channel. Longer output is cut at a line end, else at a word, and
closed with where the rest is (the run's page, or the trigger's history, whose
row keeps the whole trace). Both surfaces used to get the title alone, since the
fire passed its report no result and a failed command sets no `error`.

**A failure routed to the Inbox is an Inbox item.** "If it fails: Inbox", the
default `failure_delivery`, files the failure there (`delivery.files_in_inbox`,
through `inbox.emit_attention_item`): one item saying why, whose notification is
its view, so the bell shows it once, under the owner's rule for the kind, and it
waits in the Inbox until it is dealt with. It used to be a notification alone,
which the Inbox never listed. A result is a notification, as the Triggers page
says ("It still reaches the dashboard"), and so is a failure whose route is
"Same as results".

**Work a fire only started reports when it ends.** An action that starts an
agent task or a workflow run returns `launched` (or `queued` behind a run in
flight), and one that stopped for you returns `needs_input`; the fire sends no
report for any of them (`delivery.says_nothing_now`), since nothing has finished.
An action that had nothing to do (`skip`) sends none at all: its history row
says why, and "X finished" would say something happened.
The agent task reports on the trigger's route when it ends
(`GatewayOrchestrator._report_to_its_trigger`, from the subagent completion):
"X finished" and the agent's reply, or "X failed" and why, and the plain
subagent note is not sent as well. Its run's history row says the same: the row
names the agent it started (`ActionResult.work_id`, kept as `ScheduleRun.work_id`),
and the agent's ending rewrites `launched` into `success` and the reply, or
`failure` and why (`triggers/settle.py`, `ScheduleRunStore.settle_sync`); an
agent that ends before its fire wrote the row still lands on it. An agent whose
start you declined is `declined` (`SubagentInfo.declined`, mapped to
`skipped_gate`): your own decision, so it sends no note and never reads as a
failure. A workflow run reports the same way from its
terminal write (`workflows/run_finish.report_to_its_trigger`, wired as
`EngineServices.report_to_trigger`) and links to the run, including the two
endings the workflow watchdog writes with no controller: a run whose spec cannot
be read, and one whose steps all ended while nothing drove it. A run that was
cancelled or declined says nothing, since whoever stopped it knows. A Run now
that starts either reports the same way. A spawn the subagent manager refuses on
the spot (low memory, an incident, the day's budget) is the fire's own failure
(`action_providers.services.spawn_refusal`), and a fire that stopped for you
asks in the Inbox (`triggers/parks.py`).

**An agent a trigger starts carries the trigger.** Both store-trigger dispatches
set `ActionContext.trigger_id`, and so does a lifecycle hook's fire and its Test
(`lifecycle:<id>`, `hooks.run_script_hook`); `invoke-agent` and `run-prompt` spawn their
agent with it (`SubagentInfo.trigger_id`). An Invoke Agent agent asks before each call
that needs approval unless its step sets its own `approval_mode: auto` (saved with the
owner's yes) or the owner chose Approval mode Auto, which ships off
(`approval_grants.setting_grant`); its Allow says which. An approval that agent asks for is
listed under the trigger ("The trigger “Nightly plan” is waiting for your
decision on write_file"), and a call nobody answered leaves an Inbox note that
can run the trigger again. That re-run is Run now, and a trigger with
`needs_grant` goes through Allow first, the same consent its page asks
(`docs/architecture/inbox-channels.md`, "How long it waits").

**An Invoke Agent trigger works in the folder it names.** Its **Working folder**
(`cwd`, in `apps/native/invoke-agent-action/app.json`; the schedule form's field of
the same name) is the folder its agent starts in, so the agent's file tools reach
the files there, and only as they reach any session's folder
(`docs/architecture/security.md`, "Where the file tools reach"). It
must be the workspace or a folder in Settings → Agent defaults → Allowed working
directories (`agent.subagent_cwd_allowed_roots`), the rule a Run prompt action's
Working Directory already met: the trigger's save refuses any other with why
(`_action_problem`, through `action_providers.services.validate_spawn_cwd`, for both
actions), the fire asks again, since the list can change after the save, and the
spawn asks last (`subagent.validate_cwd`). Empty is the workspace. An Invoke Agent
agent always worked in the workspace before, so one asked to read a file under a
folder the owner had allowed could not reach it.

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
Before that, the form says what it is waiting for beside **Create trigger** — the
first requirement outstanding, a required action setting named by its label
("“Workflow” is required") — and the trigger's panel reads back the workflow the
action saved (`config.workflow`) and the inputs its run starts with.

**The 900s cadence floor is for model calls.** `MIN_CLOCK_INTERVAL_SECS`
warns only when the action can call a model: providers listed in
`triggers/models.py`'s `ZERO_TOKEN_PROVIDERS` (bash, notify, the digests, …)
are exempt, and anything unlisted — app-contributed actions included — keeps
the warning.

**An edit moves the next fire, wherever it is made.** The clock fires a trigger
at its stored `next_fire_at`, so one rule (`arm.next_fire_after_edit`) decides
it after every edit and switch: a change to when it runs (its cadence, zone, skip
dates, or a one-shot's time) re-arms it from the new schedule, and a trigger
left on with no next fire is armed; a rename or a cosmetic save keeps its
instant. `tools.update` and `tools.set_paused` apply it, and the Triggers page,
the chat's `automation_update` / `automation_resume` and the CLI all go through
them, so a time changed in chat fires at the new time, as one changed on the
page does.

### A one-shot runs, is recorded, and only then leaves the list

A clock trigger with no next fire — a one-time `at` — has its slot taken by the
fire that grants it (`service.tick`): switched off with no next fire, in the same
write that records the grant, after its claim is on disk. The gateway's runner
fires the STORED row (read through `routing.routed`, so a row an app serves is
found too), and the recorder writes its run. A one-shot that retires after its
run (`spec.delete_after_run`: the Triggers page's One-shot and a decision's
review) leaves the store only then (`service.retire_after_run`), and only for a
run that did its work: on time, late, or with nothing to do. One whose action
only started work (an agent, a workflow run) goes when that work ends and its
note has gone out (`GatewayOrchestrator._report_to_its_trigger`). A run that
failed, was held by a gate or waits on you leaves the row in the list, switched
off, with its record. The chat's one-time task keeps its row either way. A
run's record outlives its trigger, so a new trigger never takes the id of one
whose runs are kept (`tools._unique_id`): its history is its own.

A slot the tick reaches more than `scheduling.LATE_THRESHOLD_SECS` after it is
not run late: it was missed (`scheduling.slot_missed`, below). A catch-up fire,
which stands in for a missed slot, is recorded `ran_late` with how late against
that slot (`missed.late_outcome`, carried on the fire as `DueFire.late`). A tick
that dies before the grant is written leaves the one-shot armed, so it fires on
the next tick; one that dies after leaves its claim, which the boot's orphan
pass below records as interrupted and puts on the review, where Run now runs it
and it then retires.

### After a restart or a sleep: missed and interrupted runs wait for you

A slot is **missed** when the wall clock is past it by
`scheduling.LATE_THRESHOLD_SECS` or more (`scheduling.slot_missed`), whatever
kept it from running: the gateway was stopped, or it was alive and could not
tick because the computer slept or the process was stopped. One rule, applied
by one function, `service.recover`: `service.boot` calls it for every trigger
after a restart, and `service.tick` calls it for the triggers a wake finds
missed, before it computes the due set. Within the threshold a slot runs as
normal (at boot, pushed into the stagger window: `scheduling.boot_recovery`'s
`due_staggered`). Past it, a trigger with `catch_up` fires once, staggered
(`missed.catch_up_plan`), and the clock loop holds the slot it stands in for
(`review.catch_up_slots`, the tick's `catching_up`) so its record says how late
it ran; every other missed slot is dropped, the schedule resumed on its own
grid (or, for a one-shot, its slot taken: switched off with no next fire), and
the slot goes to the review. Neither path re-runs one on its own: running a 3am
job at 9am is sometimes right and sometimes exactly wrong, and a run a restart
cut off may already have done part of its work.

- **Missed slots.** `triggers/missed.review_at_boot` walks each active,
  enabled schedule whose armed slot was missed, from that slot to now: on the
  interval grid, or for a cron or a one-shot by walking the schedule
  (`missed.stepped_slots`). A paused or disabled schedule missed nothing, and
  neither did one whose next fire does the missed one's work
  (`models.MISSED_FIRE_SUPERSEDED_PROVIDERS`: the HEARTBEAT.md queue). A wake's
  review cards are kept by the tick that dropped the slots, and the gateway
  announces them (`GatewayOrchestrator._surface_wake_review`).
- **Interrupted runs.** `triggers/reaper.terminalize_orphans` closes a run
  whose process is gone with the status `interrupted`
  (`RESTART_INTERRUPTED_STATUS`) and the reason in `error`. It is not
  retried. A claim names its owner's pid and the program image that took it
  (`scheduling.PROCESS_IMAGE`), because a Restart re-execs the gateway in the
  same process: a claim an earlier image left is closed the same way. A stop
  or a Restart records the runs it cancels itself, as it stops
  (`reaper.record_stopped_run`, from `GatewayOrchestrator._record_stopped_fire`
  and the Run now dispatch), since a cancelled run gives its claim back and
  leaves the boot pass nothing to close. A Run now holds the trigger's claim
  while it runs, so it reads as running and a second one is refused 409. It
  gives back only its own claim: one a tick wrote over it meanwhile stays.

Both land in `trigger-review.json` beside `triggers.json`
(`triggers/review.py`): one card per trigger and kind, until you decide it. A
card names why its slots did not run (`ReviewCard.cause`: `stopped`, `paused`,
or `stopped_or_paused` once a card holds both), and the notice and the card's
sentence say it in those words: "while PersonalClaw was not running" is false
of a laptop whose lid was shut.
The "Missed scheduled runs" notice points at the Triggers page, where the
cards sit above the list (`GET /api/triggers/review`). It is composed from the
cards this boot kept (`review.boot_notice`, over the one reading of the report
the cards come from, `review.missed_by_trigger`), so it counts what waits
there. It and a stop's "… was interrupted" notice are the decision kind
`cron/run_review` (warning), so quiet hours put them in the bell without a
toast rather than dropping them. The boot passes run
before the dashboard exists, so the gateway holds the notice and sends it once
the dashboard is up (`GatewayOrchestrator._surface_held_boot_review`). **Run now** runs the
action once, however many slots the card covers, and records it `ran_late`
with the reason; **Dismiss** records `skipped_missed`. Both go through
`missed.resolve_missed` and land in that automation's history
(`POST /api/triggers/review`).

### An action that stops for you asks you

An action can stop on something only a person can lift: browse at a sign-in
page returns `outcome="needs_input"` with the card its handoff composes ("Sign
in to example.com, then confirm", and what it tried). Inside a workflow run
the engine parks the step and asks through the run
([workflows.md](workflows.md#waiting-on-a-person)); from a trigger,
`triggers/parks.py` asks. Both recorders — the fire path
(`gateway._record_fire_outcome`) and the Run button (`_record_manual_run`) —
record the run `waiting` (`schedule_history.status_for_result`; the runs feed
reads it as `deferred`), with the summary "Waiting for you." and the question,
and stamp the trigger's `last_waiting_at` rather than `last_success_at`: the
action did nothing it was asked yet. `last_run_ts` is the newest of the
success, failure and waiting stamps, so a Run button still clears when its run
stops for you. Then both call `parks.settle`:

- **A park raises one question.** One park file per trigger
  (`trigger_parks/` in the home) holds a single-use token and the card; its
  ONE Inbox row carries the card and the token, deduped per trigger, so a
  trigger that runs again before anyone answers asks nothing new. An expired
  browse session raises no row of its own: the mirror's banner says it
  expired, and the trigger's row is the one that runs anything.
- **Approve runs it again, with the answer.** `POST
  /api/triggers/{id}/answer {resume_token, answer}` spends the token once
  (a double click runs nothing twice; a stale token answers `409
  trigger_park_gone`), then dispatches the action through the Run button's own
  path — its grants and capability fence — with `ActionContext.answer=True` on
  that one dispatch, which is what lets browse leave its pre-run sign-in check,
  as an approved in-run park does. A refusal the Run button honours (incident
  mode, the kill switch) is read before the token is spent. **Deny** closes the
  question until the trigger next stops.
- **A run that goes through withdraws it.** A later plain success (a session
  signed in since) makes the question moot, so the park and its row go; a
  failure or a skip leaves it standing.

The park holds the trigger's id and the card, never the action's config:
Approve re-reads the trigger, so a secret the config names is resolved at run
time and never written to the park. The answer route is the owner's alone. It is refused to an
app (`apps/permissions.ROUTE_AUTHZ`, for the reason Run now's is) and to an agent's tool. The
gateway's internal credential opens only `/run` of the trigger routes, so the auth middleware
refuses it here (`403 internal_route_refused`), and where no sign-in is asked (`auth_mode=none`)
the route's own check refuses it: a `403 approval_owner_only` with an audit row, given before
anything about the trigger is read (`approval_answer`). `trigger_parks/` is in the durability inventory's `IGNORED`: the question is
about a sign-in in this machine's browser profile, which no snapshot carries
either, so a restored copy could only ask again.

### The HEARTBEAT.md queue is a system trigger

`workspace/HEARTBEAT.md` is the queue the agent writes "keep checking until
done" work to. It is read by one system trigger, `system:heartbeat-tasks`
("Heartbeat tasks", every 60 s, created at boot by
`action_providers/heartbeat_tasks_provider.reconcile_heartbeat_tasks_trigger`),
so it is listed with the other automations, with its runs, and switched off
or slowed there. The boot reconcile converges only its action, never its
switch or its cadence. Each task still runs as the gateway's heartbeat turn
(`heartbeat.set_task_runner`), and `HEARTBEAT_KEEP` keeps an unfinished task
for the next pass. A task runs in a session of its own
(`cron:system:heartbeat-tasks:<run>`), as the owner's agent with its tools, and
that session ends with the task. The background chores keep no session at all:
each is a call of its own to a model offered no tools (`chores.run_chore`). A
pass over an empty queue reports `skip`, which the run
history records as the inert `skipped_noop` (`schedule_history
.status_for_result`, the one status rule the fire path and the Run button
share), so it folds out of the default history. The heartbeat loop itself no
longer reads the file.

**A task runs only once the owner allowed it** (`heartbeat.py`), as a trigger
the chat makes does. The agent writes this file, as can its shell and an app
that declared `/api/file-write`, so a task is the agent's words until the owner
says otherwise, and a pass does not run it: it stays in the file as written,
and the pass reports how many wait (`no task ran: 1 waiting for your Allow on
the Triggers page`, an inert `skip` when nothing ran). The owner's yes is sealed
to the task's text (`grants/heartbeat.json`): a task they type in the Files
editor is allowed as they save it (`files._allow_heartbeat_tasks_the_owner_wrote`,
never for an app's write), and any other is listed on the Heartbeat tasks
trigger's panel with **Allow** (`GET /api/heartbeat/tasks`,
`POST /api/heartbeat/tasks/allow`, which asks first and is owner-only). An edit
to a task is a new task, and a finished task takes its yes with it. Not
"read-only until allowed": a waiting task does not run at all, so what it would
do is never the question — only the owner's yes is.

### A research report's schedule is one schedule

A scheduled research report (`#/knowledge/reports`) owns its schedule — its
cadence, the zone it runs in, and its switch (`knowledge.research_reports`) —
and its automation, `report-schedule:<report id>` (`created_by:
research-report`), mirrors it (`knowledge.report_schedules`). The clock fires
the automation and a fire IS the report's run: the runner never reads the
schedule a second time. An edit moves both, from either side. Saving the report
re-derives only what the report owns on its automation (the cadence and zone in
`spec`, the switch, the action, the name, the grant) and keeps the rest — where
its result and failures go, a missed time's catch-up, its skip dates, and what
the clock wrote (its next fire, runs, health). An edit through `triggers.tools`
(the Triggers page, the chat's automation tools, the CLI) is asked
`report_schedules.edit_refusal` before it is saved — a rename, another action,
or a schedule a report cannot hold (a sequence) is refused in words — and
`report_schedules.adopt` writes the saved cadence, zone and switch into the
report; the clock's autopause hands its paused row there too. Deleting the
automation leaves the report with no schedule (`adopt_removal`): it stays, and
runs when you press Run now. The automation's row names its report
(`report_id`, by the same `report_schedules.owns` rule the edits follow), so its
panel on the Triggers page says it is a report's schedule and where the report
is renamed or deleted, and its delete dialog says the report stays.

A run that reads its sources and finds nothing new records `nothing_new` and its
sentence on the report ("Found no new material in your knowledge tagged perf
since its previous run."), and reports `skip`, which its automation's history
records as the inert `skipped_noop` with that sentence. A fire that meets a run
of the same report already in flight runs nothing and is recorded the same way.
A report reads only the knowledge library — what a watched source, a note or an
import brought in — and never the web; its card says what it reads, worded from
the same scope a run resolves (`research_reports.sources_shown`).

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
