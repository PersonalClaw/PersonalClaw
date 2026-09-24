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

| | |
|---|---|
| **Checked on** | the **Inbox** (a notification arrives), and the automation's **run history** on the Schedule page (the run is recorded `failure`) |
| **The event name** | `automation.run.failed` |

How it is implemented, in the order the fire path runs it:

- `src/personalclaw/gateway.py:1490` — `_deliver_fire_outcome`, called once per fire with the
  outcome.
- `src/personalclaw/gateway.py:1556` — that call asks for the destination **per outcome**:
  `destination=_delivery.route_for(trigger, ok=ok)`. This is the line that makes the guarantee real;
  it used to pass `trigger.delivery` unconditionally, which routed a failure through the silent
  channel.
- `src/personalclaw/triggers/delivery.py:246` — `route_for`, the decision. Its body
  (`delivery.py:270`–`273`) is the whole rule: a success returns `delivery`; a failure returns
  `failure_delivery`, falling back to `delivery` only when the failure route is blank. A success
  never inherits the failure route, or a quiet automation would start announcing ordinary runs.
- `src/personalclaw/triggers/models.py:664` — `failure_delivery: str = "inbox"`. The default is what
  makes this true without configuring anything.
- `src/personalclaw/triggers/delivery.py:363` — `is_muted`, the single place that decides what
  silence is. It mutes the literal `"none"` and nothing else, so an inbox-routed failure is not
  muted.
- `src/personalclaw/triggers/delivery.py:49` — `EVENT_FAILED = "automation.run.failed"`, the event
  name above.

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

| | |
|---|---|
| **Checked on** | the **run history** (the run has a terminal `timeout` status, with a reason naming the restart) and the Schedule row (it stops rendering as in flight) |
| **The status** | `timeout` — deliberately inside the existing four-member status vocabulary |

- `src/personalclaw/triggers/reaper.py:231` — `terminalize_orphans_sync`, the boot pass.
- `src/personalclaw/triggers/reaper.py:256` — the three writes, and why a terminalized run reuses
  `timeout` rather than adding a fifth status: the frontend switches on that closed vocabulary, and
  an unknown value would render as "never run" grey. The interrupted-by-restart distinction rides in
  the run's `error` text, which both the Last-run block and the history already render.
- `src/personalclaw/triggers/reaper.py:83` — `RESTART_INTERRUPTED_STATUS = "timeout"`, that choice as
  one named constant.

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

2. **Fire it and watch the Inbox — guarantee 1.** Press **Run now**. The manual-run path and an
   autonomous tick fire the same action through the same path, so this is a real test and not a
   special case.
   - **Expected:** a notification appears in your **Inbox** even though delivery is `none`, and the
     run history shows one `failure` row.
   - **The guarantee broken would look like:** a `failure` row in the history and **nothing** in the
     inbox — a broken automation that told you nothing. That is the defect
     `gateway.py:1556` exists to prevent.
   - Confirm the route rather than inferring it: the notification is the
     `automation.run.failed` event from `src/personalclaw/triggers/delivery.py:49`.

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

4. **Kill the gateway mid-run and restart it — guarantee 3.** Remove the quiet-hours window and point
   the action at something slow (`sleep 300`). Press **Run now**, confirm the row is in flight, then
   kill the gateway **without** letting it shut down cleanly:

   ```bash
   kill -9 "$(pgrep -f 'personalclaw gateway' | head -1)"
   ```

   Start it again.
   - **Expected:** at boot the run is terminalized — the history shows a terminal `timeout` row whose
     reason says it was interrupted by a gateway restart and how long it ran, and the automation is
     no longer rendered as running. It does **not** wait out a 30-minute deadline first, because the
     owning pid is provably gone.
   - **The guarantee broken would look like:** a row stuck at *running* indefinitely, with the next
     scheduled fire silently suppressed by an overlap gate waiting on a run that ended when you
     killed the process.
   - This is the boot pass at `src/personalclaw/triggers/reaper.py:231`.

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
