# Automations you can leave alone

An automation is only useful if you can stop watching it. That needs one property, and it is not
"it works" — it is **that you find out when it doesn't.** An automation that fails silently is worse
than no automation, because you have stopped doing the thing yourself and nothing told you to start
again.

This page states the three guarantees PersonalClaw makes about unattended runs, in plain terms, with
the surface each one is checked on and the code that implements it. Then it gives you a recipe that
**falsifies all three in one automation**, so none of this has to be taken on trust.

Nothing here is a roadmap item. All three are shipped; this page exists because a guarantee nobody
can find is not a guarantee.

---

## The three guarantees

### 1. A run that FAILS reaches your inbox — even when the automation's delivery is `none`

**In your words:** *"If I tell an automation to stay quiet, it stays quiet about its ordinary runs.
It does not stay quiet about breaking."*

Silence you asked for is silence about **success**. A failure has a separate route, and that route
defaults to your inbox. So an automation set to `delivery: none` that starts failing still says so —
otherwise "quiet" would mean "I will not tell you when I stop working", which is the opposite of what
anyone means by it.

The silence covers the work an automation starts, too. When the agent it started (**Invoke Agent**,
**Run Prompt**) or its workflow run (**Run workflow**) finishes well, no note says so, from the
automation or from the agent: its run history does. That is what keeps an app's scheduled job quiet,
since an app's job always runs with delivery `none`.

| | |
|---|---|
| **Checked on** | the **Inbox** (an item for the failure, saying why, which the bell also shows once), and the automation's **run history** on the Schedule page (the run is recorded `failure`) |
| **The event name** | `automation.run.failed` |

How it is implemented, in the order the fire path runs it:

- `src/personalclaw/triggers/delivery.py` — `report_run`, called once per run with the outcome:
  by a scheduled fire, and by every run you start by hand (**Run now**), and again when the work a
  run only started ends. Its answer says whether the automation has spoken for the outcome, a
  `none` route's silence included, and the agent's own completion note goes out only when it has
  not.
- `report_run` asks for the destination **per outcome**: `destination=route_for(trigger, ok=ok)`.
  This is the line that makes the guarantee real; it used to pass `trigger.delivery`
  unconditionally, which routed a failure through the silent channel.
- `src/personalclaw/triggers/delivery.py:363` — `route_for`, the decision. Its body
  (`delivery.py:387`–`390`) is the whole rule: a success returns `delivery`; a failure returns
  `failure_delivery`, falling back to `delivery` only when the failure route is blank. A success
  never inherits the failure route, or a quiet automation would start announcing ordinary runs.
- `src/personalclaw/triggers/delivery.py:398` — `files_in_inbox`: a failure whose route is the Inbox
  is filed there as an item (`_file_in_inbox`, `delivery.py:695`), and its one notification is the
  item's view, so the bell shows it once and the Inbox lists it until you deal with it. A result,
  and a failure that follows the results ("Same as results"), stays a notification.
- `src/personalclaw/triggers/models.py:884` — `failure_delivery: str = "inbox"`. The default is what
  makes this true without configuring anything.
- `src/personalclaw/triggers/delivery.py:502` — `is_muted`, the single place that decides what
  silence is. It mutes the literal `"none"` and nothing else, so an inbox-routed failure is not
  muted.
- `src/personalclaw/triggers/delivery.py:51` — `EVENT_FAILED = "automation.run.failed"`, the event
  name above.

A **web watch** can fail without ever running: its checks of the page are refused by the network
settings, cannot read the page, or find nothing on it the watch can track (no links, no feed
entries), and then it can never fire. The first check of each such stretch is reported on the same
failure route, once rather than at every check, and the Triggers page shows the watch's last check
with a warning, and says it is not firing, until a check reads something it can watch.

- `src/personalclaw/triggers/web_poll.py` — `report_checks` reports the stretch through
  `report_run`; `last_check` is the record the page and `automation_list` read.

An automation can also be **refused before it runs**, because something its action needs is gone:
the app that provides the action is deactivated, was removed or did not start, a `{{secret:…}}` the
action uses is not in Settings → Secrets, or it has no action at all. An automation runs in no
project, so its action reads the global secrets only; a secret kept for one project is read by that
project's work (a workflow run an automation starts in a project reads it in its own steps). What
an action a secret was filled into prints, returns or fails with is masked of that value, however
short, before its run history, its last error, the note that reports the run and Run now's answer
keep or show it: a command that echoes its token is recorded with `[REDACTED: credential]` where
the token was. So is what the action writes itself as it runs: the audit log's row for a command
refused before it ran or for a request, and the gateway log. A value the command changes on the
way (encodes, cuts short, splits) is not recognised. A `{{secret:…}}` in a **Run workflow**
action's inputs is handed to the run as the reference, never the value: the run's record, its run
list entry, a preview's row in the run history and the prompt of a model step that reads the input
hold the reference, and the run fills it in where one of its steps uses the input, the run's
project's secret first, then the global one. Such a fire is refused only when the run could read
the secret from neither. Each such fire is recorded as
`refused` in its run history, in a sentence that names the app and its action, or the secret by its
name (never a value), and says what to do; the Triggers page shows it as the automation's last run,
refused, with the same sentence. It is reported on the same failure route ("<name> did not run")
the first time, and not again at every fire: the next report waits for a run that gets through, or
a refusal for another reason. A refusal is not a failure, so it never pauses the automation, and it
spends none of its hourly cap. **Run now**, a webhook's call and a view's refresh are refused the
same way, in the same words. A fire that comes while PersonalClaw is still starting its apps waits
for them rather than being refused.

- `src/personalclaw/triggers/cannot_run.py` — the sentences, and `refuse`, which both dispatches
  call: `gateway._fire_store_trigger` for a fire (a webhook's call and a view's refresh included),
  `trigger_runs._dispatch_store_action` for a run by hand.
- `src/personalclaw/triggers/run_record.py` — `record_refusal`: the `refused` row, the automation's
  last run, and whether its owner has heard of this refusal yet.
- `src/personalclaw/action_providers/registry.py` — `action_origin`, the app that registered an
  action in this gateway, kept after the app goes so the sentence can name it.

**An automation that keeps failing pauses itself.** After 5 failed runs in a row (or the number
its `failure_policy.autopause_after` sets), it stops firing and says so: the bell reads
"<name> paused itself" with "paused after 5 failed runs: <why the last one failed>", and the
Triggers page "Stopped by the system after repeated failures". A run counts as failed however it
went wrong: the command it ran, or the agent (**Invoke Agent**, **Run Prompt**) or workflow run
(**Run workflow**) it started, whose failure comes later than the fire that started it. Until that
work ends its run reads `launched` and counts for nothing; when it ends, its row and the automation's
last run say how it went. A run that succeeds starts the count over. A webhook's call and a view's
refresh are the automation firing, so their failures count as any fire's do. A run you start by
hand (**Run now**), a run a restart cut off, a run you stopped (it reads `stopped`, and sends no
note), a refusal and a skip count for neither.

Switching it back on resumes it: it fires on its own again. The failures that stopped it are still
its last runs, so one more failure stops it again, and says so again; a run that succeeds starts the
count over. A quarantined automation is not switched back on: re-author it.

- `src/personalclaw/triggers/autopause.py` — `ending_decision`, the one decision every run's ending
  takes, over the failure count its history derives (`consecutive_failures_from`).
- `src/personalclaw/triggers/run_record.py` — `record_run` for a run that ended with its action,
  `record_ending` for the work a run only started; `src/personalclaw/triggers/settle.py` writes
  that work's ending onto its run's row, for an agent and a workflow run.
- `src/personalclaw/triggers/tools.py` — `set_paused`, every switch's path, resumes what paused
  itself (`autopause.resume_state`).

### 2. A run that did NOTHING is labelled inert — not green

**In your words:** *"A tick that was skipped must not look like a tick that worked. I need to be able
to tell 'it ran and succeeded' from 'it declined to run'."*

When a gate stops a fire — quiet hours, a cooldown, a rate cap, a budget, an overlap — the run is
recorded with a **typed inert outcome** (the `skipped_*` family) rather than as a success. The UI
renders it neutral grey with a pause icon, never the green check. A green tick therefore always means
*something actually happened*, which is the only way the colour carries information.

The same rule runs the other way: an inert row is not an **error** either. A quiet-hours skip is the
automation working exactly as configured, so its reason is not painted in danger red.

| | |
|---|---|
| **Checked on** | the automation's **run history** and the **Last run** block on the Schedule page — colour, icon, and label |
| **The vocabulary** | any outcome prefixed `skipped_` (`skipped_gate`, `skipped_budget`, `skipped_overlap`) |

- `src/personalclaw/triggers/firepath.py:82` — `GATE_OUTCOMES`, the map from each gate to its typed
  outcome. Every value is a member of the run-outcome vocabulary, so a suppression is always
  filterable in the runs inbox.
- `src/personalclaw/triggers/firepath.py:91` — `"quiet": Outcome.SKIPPED_GATE.value`, the entry the
  recipe below trips on purpose.
- `web/src/pages/schedule/scheduleMeta.ts:67` — `ok`/`success` is the **only** thing that renders
  green (`--color-ok`, a check icon).
- `web/src/pages/schedule/scheduleMeta.ts:93` — a `skipped_*` outcome renders
  `--color-on-surface-low` with a pause icon, labelled with the specific gate.
- `web/src/pages/schedule/scheduleMeta.ts:114` — `isInertOutcome`, derived from the `skipped_`
  prefix rather than a hand-copied list, so a new backend gate cannot render as "never run" grey by
  being forgotten here.
- `web/src/pages/schedule/ScheduleDetail.tsx:384` — the detail pane reads the same helper, so the
  history fold and the per-row styling can never disagree about what inert means; the reason box
  below it renders neutral for an inert row instead of danger red.

### 3. A run whose host DIED is terminalized — it does not read as running forever

**In your words:** *"If the gateway is killed mid-run, I want that run to end. A row stuck at
'running' blocks the next fire and tells me nothing."*

A killed process cannot close its own run. Two passes close it instead:

- **At boot**, PersonalClaw reads every in-flight claim's owning pid and terminalizes the ones whose
  owner is **provably gone**. Death is observable, so it does not wait on a clock.
- **A deadline sweep** remains the backstop for a run that is alive and merely stuck.

Each terminalized run gets three writes, and all three are surfaces you read: the claim is released
(so the next fire is not suppressed by an overlap gate that thinks the old run is still going), a
**terminal run row** is written (so the history shows an ending), and the trigger's health is marked.

A **stop or a Restart** does not leave its runs for the boot pass: it records each run it cuts off
as it stops, with the same row, health and card, and a reason that says which it was ("Interrupted
by a gateway restart" or "Interrupted when the gateway stopped"). That includes a run whose action
only started an agent (**Invoke Agent**, **Run Prompt**) and was still waiting on it: the stop records
the run as interrupted before it stops the agent, so its history does not read as a failure. The
stop itself sends no notice; when the gateway is back, one notice in the bell says what it cut off
("Runs interrupted by a restart"). A run cut off this way starts no `run_completed` chain, because
it did not complete.

A Restart starts the new gateway in the same process, so its pid is the old one's. Each claim also
names the program image that took it, and a claim an earlier image left is closed at boot like one
whose process is gone.

A run you start by hand with **Run now** holds the trigger's claim while it runs, as a scheduled
fire does: a second **Run now** meanwhile is refused ("This automation is already running."), and
a stop that cuts it off records it. Its row is the hand run's (`manual`), and the trigger's health
is left alone, as it is for any hand run.

An interrupted run is **not run again on its own**, because it may already have done part of its
work. It waits for you instead, with the times PersonalClaw missed (see
[When a scheduled time is missed](#when-a-scheduled-time-is-missed)): the Triggers page lists both
under **Waiting for your decision**, and each card offers **Run now** (once, however many times it
covers, recorded as late) and **Dismiss** (recorded as your decision). One notice in the bell says
what is waiting ("Missed scheduled runs", or "Runs interrupted by a restart" when nothing was
missed), even inside your quiet hours, where it is kept without a toast; its row in Settings →
Notifications is **Missed or interrupted runs to review**.

| | |
|---|---|
| **Checked on** | the **run history** (the run has a terminal `interrupted` status, labelled "interrupted by a restart", with a reason naming the restart), the Schedule row (it stops rendering as in flight), and the review card at the top of the **Triggers** page |
| **The status** | `interrupted` — its own word, because the run did not blow a deadline, and its own outcome in the runs feed: not a failure, and it never counts toward pausing the automation |

- `src/personalclaw/triggers/reaper.py:326` — `terminalize_orphans_sync`, the boot pass, and its
  three writes; `reaper.py:437` — `record_stopped_run`, the run a stop cuts off; `reaper.py:497` —
  `record_stopped_work`, the run whose agent a stop cuts off.
- `src/personalclaw/triggers/claims.py` — `orphaned_ids`, which judges a claim naming this very
  process by the image that took it.
- `src/personalclaw/triggers/reaper.py:98` — `RESTART_INTERRUPTED_STATUS = "interrupted"`. The
  frontend renders it (`web/src/pages/schedule/scheduleMeta.ts:151`), and
  `web/src/pages/triggers/triggerStatusVocabulary.test.ts` fails if a run status the backend can
  record has no rendering there.
- `src/personalclaw/triggers/review.py:259` — `cards_from_orphans`, the interrupted run's card,
  `review.py:204` — `cards_from_boot`, the missed slots' card (`missed.review_at_boot` walks a cron's
  schedule as well as an interval's grid), and `review.py:382` — `take_unannounced`, the cards a
  stop kept that the next start's notice counts.
- `src/personalclaw/dashboard/handlers/triggers.py:2404` — `api_trigger_review`, the cards and the
  decision; the decision's outcome comes from `src/personalclaw/triggers/missed.py:461`,
  `resolve_missed`.

---

## When a scheduled time is missed

**In your words:** *"If my laptop was closed at 3am, I want to decide whether the 3am backup runs
at 9am. Some jobs I'd rather just run late; most I want to be asked about."*

A time is **missed** when PersonalClaw could not run it within seven minutes: PersonalClaw was
stopped, or it was running and the computer was asleep (the lid shut, or the process paused). Both
are judged the same way, by the clock against the time, so a laptop that slept through a time and a
gateway that was not running at all are treated alike. A run only a minute or two late is not
missed: it runs as usual.

What a missed time does is a setting on each scheduled automation, **If a time is missed**, under
**Advanced** when you create or edit it on the Triggers page:

- **Wait for me to decide** (the default). It does not run late by itself. The Triggers page shows
  it under **Waiting for your decision**, with **Run now** and **Dismiss**, and the bell has one
  "Missed scheduled runs" notice saying how many were missed and why. A one-time automation whose
  time was missed is switched off with its time taken, so nothing runs it later unless you choose
  **Run now**.
- **Run it once, late.** It runs once by itself within a few minutes of PersonalClaw being back,
  however many of its times were missed, and gets no card. Its history row reads **ran late**, with
  how many minutes after the time it stood in for. The notice still counts it, and says it will
  fire once on its own.

The panel shows a **runs once if missed** chip on an automation set to run late. An agent sets the
same thing with the automation tools' `catch_up` field (`true` runs a missed time once, late;
`false`, the default, waits for you), which their schema describes in these terms.

| | |
|---|---|
| **Checked on** | the review at the top of the **Triggers** page, the "Missed scheduled runs" notice, the automation's **run history** (a late catch-up reads *ran late*), and the chip on its panel |
| **The setting** | `catch_up` on the trigger: off waits on the review, on runs once, late |

- `src/personalclaw/triggers/scheduling.py` — `slot_missed`, the one rule for what counts as
  missed, and `LATE_THRESHOLD_SECS`.
- `src/personalclaw/triggers/service.py` — `recover`, what both a restart (`boot`) and a wake
  (`tick`) do with missed times.
- `src/personalclaw/triggers/review.py` — the cards, and the notice's wording for each cause.

## After a restore

**In your words:** *"I restored my laptop's snapshot onto the new machine. I don't want both of
them sending me the morning brief."*

A replace restore (`personalclaw restore <snapshot> --mode replace`, or an export archive restored
the same way) brings each automation that runs on its own back **paused**: switched off, with no
time armed. It says how many and why. If the snapshot came from another PersonalClaw home, that
home may still be running the same automations, so resume them once it is retired. If the snapshot
is this home's own, nothing else runs them, and at the terminal the restore asks whether to resume
them now. An automation you had switched off, one that runs only when you run it, and one somebody
else wrote are left as they were. A merge restore never needs this: what it brings in arrives
switched off already. Chat channels do not come back connected either: a snapshot never carries a
channel's token, so on a new machine each channel connects only once you enter its token there.

The **Triggers** page shows **Paused by the restore** above the list with **Resume all**, badges
each paused row *paused by the restore*, and gives each one's panel its own **Resume**. Once
resumed, an automation runs at its next scheduled time; the time it waited is never counted as a
missed time.

| | |
|---|---|
| **Checked on** | the restore's own output, the notice and the badges on the **Triggers** page, and the automation's panel |
| **The setting** | none: `restore_hold` on the trigger says where the snapshot came from until it is resumed |

- `src/personalclaw/triggers/restore_hold.py` — what is paused, Resume all, and what the restore
  says.

## When the workflow it runs changes

**In your words:** *"I allowed my weekly report to run on Fridays. If an agent rewrites the
workflow, I want to see what changed before Friday's run uses it."*

An automation whose action is **Run workflow** runs the version of the workflow you allowed: Allow
(the create dialog, the switch, the editor) records the version the workflow is at then, and says
which. When you save a newer version yourself, in the workflow's editor, the automation runs it,
since that save is your yes. A newer version saved any other way — an agent's `workflow_author`, a
chat's batch, an accepted refiner proposal, a prompt card, another machine's sync or a restore — is
not used: the automation keeps running the version you allowed.

The automation's panel on the **Triggers** page then says it runs vN and that vM is newer, with who
saved each version since, and the row is badged *vM waits for you*. **Use vM** asks first, naming
them, and moves the automation to the workflow as it is now. The workflow's own page lists, on its
**Versions** tab, who saved each version and every automation that runs it, with the version each
runs. A version an automation may run that is no longer kept here stops its runs, saying so and
offering Use vM; it is never swapped for another.

The same holds for the workflows yours starts as steps (a *subworkflow* step, or a *Run workflow*
action step): each runs the version it was when you allowed the automation, or a newer one you saved
in its editor, and the panel says when one has a newer version, with **Use the newest versions** to
move them, which asks first. A step that starts a workflow the automation was not allowed with does
not run, and says why. A workflow's **Versions** tab lists the automations that run it as a step too.

| | |
|---|---|
| **Checked on** | the automation's panel and its row on the **Triggers** page, and the workflow's **Versions** tab |
| **The setting** | none: the version you allowed is part of the automation's Allow, which stays on this machine |

- `src/personalclaw/workflows/automation_version.py` — which version a fire runs.
- `src/personalclaw/workflows/versions.py` — the history, and who saved each version.

## When a program starts one: webhooks

**In your words:** *"When my build server finishes, I want PersonalClaw to tell me what it said."*

A **webhook automation** runs when a program posts to its address. Ask for one in the chat ("when
my build server posts, tell me what it says"), and allow it on the **Triggers** page if its action
needs your yes. Its page there shows two things:

- **Its address**, `http://127.0.0.1:<port>/api/triggers/store:webhook:<name>/fire`. It takes
  requests only from programs on the machine PersonalClaw runs on, whatever address PersonalClaw
  listens on. From another machine, forward a port to that machine's 127.0.0.1 over SSH
  (`ssh -L 10000:127.0.0.1:10000 you@that-machine`, then post to the same address through the
  tunnel), or run a relay on that machine that forwards to the address.
- **Its sender tokens.** A program fires the automation with a token made for it, sent as the
  header `Authorization: Bearer <sender token>`. **Make a sender token** shows the token once,
  with a command that tries it from this machine. It is kept only as a hash, works at most 90
  days, and fires this automation and nothing else. The page and Settings → Devices list it and
  revoke it, and Settings → External Access lists it, switches it off and revokes it. From a
  terminal: `personalclaw inbound webhook
  create store:webhook:<name>`, `personalclaw inbound webhook list` and `personalclaw inbound
  webhook revoke <id>`. Deleting the automation revokes its sender tokens.

What the program posts, up to 64 KB, reaches what the automation runs as data, never as
instructions. The fire answers `202` once it is accepted, and the run appears in the automation's
run history like any other fire's. A program's call is the automation firing, not you pressing
**Run now**: it keeps every rule the automation's other fires keep. Its hourly cap and its spacing
count it, its quiet hours, its budget and a run of it still going hold it, its failures count
toward the streak that pauses it, and what it runs is held as any fire's is. A call its own rules
hold runs nothing, answers `429` when the automation has fired as often as you allow for now and
`409` otherwise, saying which, and leaves a skipped row in its run history saying why. A refusal
says why, in a sentence: `401` for no token or one PersonalClaw does not know (and for one that
was revoked or ran its lifetime, saying so), `403` for a token made for another automation, a
request from another machine, or an action you have not allowed yet, `404` when the automation is
switched off or not there, `429` when a sender posts faster than its rate, and `503` during an
incident. Every request the address answers is in the inbound audit (Settings → External Access
counts each sender token's), and every refusal and accepted fire is in the Security log.

An agent can also register a **callback** with `hook_register`, for an outside system to call back
later at `http://127.0.0.1:<port>/api/hooks/agent` with your **webhook token**, which you set with
`personalclaw config set hooks.webhook_token <token>` (at least 32 characters). The tool's answer
says exactly what the outside system sends, and the callback waits on the Triggers page until you
allow it.

| | |
|---|---|
| **Checked on** | the automation's page (its address and sender tokens), its run history, Settings → External Access, and the Security log |
| **The setting** | none: its sender tokens, and its own switch |

- `src/personalclaw/inbound/webhook.py` — the two addresses, the rules both keep, and their words.
- `src/personalclaw/dashboard/handlers/trigger_runs.py` — `api_trigger_fire`, the fire.
- `src/personalclaw/dashboard/handlers/hooks.py` — `api_hooks_agent`, the callback's address.

---

## Falsify all three, in one automation

Ten minutes, one automation, three checks. Each step tells you **what to look for** and **what a
failure of the guarantee would look like**, so a pass is informative rather than a shrug.

Use an isolated home so none of this touches your real state:

```bash
export PERSONALCLAW_HOME="$PWD/.dev-home"
```

1. **Create one automation that is deliberately quiet and deliberately broken.** On the **Schedule**
   page, add a schedule trigger. Set its delivery to **none** — that is the setting the first
   guarantee is about. Give it an action that cannot succeed (a shell action running
   `exit 1` is enough). Leave `failure_delivery` alone; the point is that its default already
   protects you.

2. **Let it fire and watch the Inbox — guarantee 1.** Set it to run every minute and wait for its
   next fire. (**Run now** records the same history row and reports the same way, but this
   guarantee is about what a fire tells you when nobody is watching, so let the schedule fire it.)
   - **Expected:** an item appears in your **Inbox** even though delivery is `none`, reading
     "<name> failed" and what the command said (its error output, or its exit code), with one
     entry in the bell for it; and the run history shows one `failure` row.
   - **The guarantee broken would look like:** a `failure` row in the history and **nothing** in the
     inbox — a broken automation that told you nothing. That is the defect the outcome-picks-the-route
     rule in `delivery.report_run` exists to prevent.
   - Confirm the route rather than inferring it: the item's notification is the
     `automation.run.failed` event (`EVENT_FAILED` in `src/personalclaw/triggers/delivery.py`).

3. **Now make it decline to fire, and check the colour — guarantee 2.** Edit the same automation and
   set an all-day quiet-hours window on it (`gates.quiet_hours` = `00:00-23:59`). Press **Run now**
   again.
   - **Expected:** a new run row appears in neutral **grey with a pause icon**, labelled with the
     gate (`gate`), and its reason names the window. It is **not** a green check, and it is **not**
     red.
   - **The guarantee broken would look like** either failure mode: a green tick for a fire that
     never happened (you would believe work was done), or an alarming red row for a suppression that
     is the automation behaving correctly (you would go hunting a fault that is not there).
   - The grey comes from `web/src/pages/schedule/scheduleMeta.ts:93`; green is reserved to
     `scheduleMeta.ts:67`.

4. **Kill the gateway mid-run and restart it — guarantee 3.** Remove the quiet-hours window, point
   the action at something slow (`sleep 300`), and set it to run every minute. Wait for the schedule
   to start it (not **Run now**: a run you start by hand is not covered, see guarantee 3), confirm
   the row is in flight, then kill the gateway **without** letting it shut down cleanly:

   ```bash
   kill -9 "$(pgrep -f 'personalclaw gateway' | head -1)"
   ```

   Start it again.
   - **Expected:** at boot the run is terminalized — the history shows a terminal row labelled
     *interrupted by a restart* whose reason says it was interrupted by a gateway restart and how
     long it ran, and the automation is no longer rendered as running. It does **not** wait out a
     30-minute deadline first, because the owning pid is provably gone. It is not run again: the top
     of the **Triggers** page shows it under *Waiting for your decision*, with **Run now** and
     **Dismiss**.
   - **The guarantee broken would look like:** a row stuck at *running* indefinitely, with the next
     scheduled fire silently suppressed by an overlap gate waiting on a run that ended when you
     killed the process.
   - This is the boot pass at `src/personalclaw/triggers/reaper.py:326`.

5. **Delete the automation.** The guarantees are checked; the broken trigger has no further use.

---

## If a step does not behave as described

Then either this page or the code is wrong, and that is worth a report — the two are pinned together
by `tests/test_automation_guide_event_name_matches_code.py`, which fails the build if the event name
this guide promises stops matching the constant the code sends. That rail only covers the name; the
behaviour above is what the recipe is for. Please
[open an issue](https://github.com/PersonalClaw/PersonalClaw/issues) with which numbered step
diverged and what you saw instead.

## Related

- [Getting started](getting-started.md) — install through first chat.
- [Remote access](remote-access.md) — reaching the dashboard (and its automations) from outside your
  network.
