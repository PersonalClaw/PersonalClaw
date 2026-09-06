# WF2AUT-11 — porting the loop ticker onto `kind:idle`, then deleting `autonudge.py`

**Atom:** `WF2AUT-11` (half 2). Half 1 — the `kind:idle` runtime for user automations —
shipped 2026-08-10 (`triggers/idle_poll.py`, riding `triggers/loop.tick_once`). This note is
the survey + decision for the remaining half: the OWNER ruling of 2026-08-27 re-scoped the
atom from "delete a module" to "**port the driver, THEN delete**", and the 2026-09-05 audit
re-homed the port here after LOOPS-EVOLUTION decomposed to `WF2LOO-1..8` and closed with
zero mention of the loop-ticker (grep `autonudge|loop-ticker|Phase 4` over
`docs/roadmap/atomic/WF2LOO.md` is empty). Plan scope: [AUTOMATION-SUBSTRATE
§1.2 idle kind (row `idle`, ~line 137), §2 disposition table ("`autonudge.py` ABSORBED as
`kind:idle` — LAST"), §7 step 9, Risks "Loops coupling"](../plans/WORKFLOWS-V2-AUTOMATION-SUBSTRATE.md).

## What `autonudge.py` actually is (358 lines, two jobs)

`src/personalclaw/autonudge.py`:

* **State** — `NudgeLoop{id, session_name, message, idle_secs, max_cycles, cycle_count,
  active, last_fire_ts, created_ts, stop_sentinel_path, error_count, first_idle_secs}`
  (autonudge.py:63-82), persisted to `~/.personalclaw/autonudge.json` (fcntl + atomic
  write, autonudge.py:117-140). One loop per session (add replaces, :194-198).
* **Tick engine** — a private `asyncio` timer per loop (`_arm_timer`/`_timer`,
  :314-358). The timer's fire path checks, in order: shutdown event → stop-sentinel file
  (present → remove the loop, :333-336) → `max_cycles` (reached → deactivate, :338-341) →
  `_on_fire` callback; **delivered-only counting** (`cycle_count`/`last_fire_ts` advance
  only on `delivered=True`, :344-357); `first_idle_secs` is a one-shot short first wait
  keyed on `cycle_count == 0` (:318-325, cleared at :356).
* **Backpressure** — `notify_turn_complete(session, errored=)` re-arms the timer on EVERY
  turn exit and deactivates after 3 consecutive errored turns (`_MAX_CONSECUTIVE_ERRORS`,
  :273-300); `notify_user_input(session)` cancels the pending timer (:302-307). Both are
  reached via the module singleton `get_instance()` (:56-60) from fail-open try/except on
  the chat hot paths.
* **Job A** (chat-idle nudging) is already replaced by `kind:idle` for user automations.
* **Job B** (the loop tick engine) has no replacement: it is what arms and fires every
  Goal/Code/Design/General loop worker cycle, and the planner.

## Importer map (what each consumes)

Direct imports of `personalclaw.autonudge`:

| Site | Primitives |
|---|---|
| `gateway.py:35-39` | `AutoNudgeService`, `NudgeLoop`, `enabled`; constructs the service with `on_fire=_fire` (`_init_autonudge`, gateway.py:2307-2539) — **`_fire` IS the loop-cycle driver**: renders the nudge, drops it if `session.running`, appends a `nudge` message, drives `run_chat` with deliverable-forcing re-prompts (`_MAX_CYCLE_REPROMPTS`, fresh ACP session per retry), reports turn outcome to `LoopWatchdog.record_turn_outcome`; `subscribe(_observer)` → `autonudge_state` WS broadcasts; gateway.py:2475 `get_instance` re-arms once per logical cycle |
| `dashboard/chat_handlers.py:325` | `get_instance().notify_user_input(session.key)` — hot path, fail-open |
| `dashboard/chat_runner.py:4411` | `get_instance().notify_turn_complete(session.key, errored=…)` on every turn exit, gated by `session._suppress_autonudge_rearm` — hot path, fail-open |
| `dashboard/handlers/autonudge.py:10` | HTTP wire: GET `/api/autonudge`, GET `/api/autonudge/{session}`, POST, PATCH `/{loop_id}`, DELETE `/{loop_id}` over `get_instance()` + `asdict(NudgeLoop)`; also owns `resolve_stop_sentinel`/`render_nudge_message` |
| `dashboard/handlers/loop_routes.py:563,602,678,841` | `get_instance()` handed to `loop.manager.start/pause/stop/nudge/teardown_for_delete` and `plan_walkthrough` |
| `dashboard/handlers/prompts.py:441` | `get_instance()` → `manager.start` (launch-from-prompt) |
| `tasks/hierarchy_handlers.py:614` | `get_instance()` → `manager.teardown_for_delete` on project force-delete |
| `agents/native/sdlc_tools.py:48` | `get_instance()` → `manager.start` (agent-facing loop tools) |
| `triggers/idle_poll.py:273` | `_autonudge_owns` — the anti-double-fire fence (skip reason `SKIP_AUTONUDGE`) |
| `tests/test_autonudge.py` | `AutoNudgeService`, `NudgeLoop`, `enabled` |

Duck-typed consumers (take `svc`, never import): `loop/manager.py` (`svc.add` :212,:507
with `stop_sentinel_path=<loop dir>/STOP`; `svc.update(message=…)` in
`rearm_nudge_message` :237-263; `svc.get_by_session`; `svc.remove`; **`svc._loops`
private access in `pause` :275**), `loop/watchdog.py` (constructor argument :223;
`_loop_exhausted` reads `NudgeLoop.active`/`.cycle_count` as lifecycle truth :798-806),
`planning/runner.py:140-191` (`svc.add/get_by_session/remove`).

## The target substrate (what already exists)

* `triggers/models.py` — `idle` has been a declared kind since S87: it is in `KINDS`
  (models.py:34-44; the comment at :31 documents why adding a kind is costed — a kind the
  service cannot dispatch is an authorable trigger that never fires) and
  `SPEC_KEYS["idle"] = {scope, idle_secs, first_idle_secs}` (models.py:226). The §6
  lossless-migration note near models.py:245-270 (the `interval` clock-kind deviation) is
  the precedent for widening a vocabulary rather than letting a migration lie.
* `triggers/idle_poll.py` — the shipped idle RUNTIME: sidecar state
  `trigger-idle/<safe-id>.json` (`IdleState{armed_at, cycle_count, last_fire}`),
  `due_fires` (pure decision), `record_delivery` (**delivered-only**, idle_poll.py:323-350),
  `notify_activity` (user-input re-arm as state instead of a timer, :237-258, writer wired
  at `chat_handlers.py` beside autonudge's own cancel), `poll` → `wakeup.dispatch_fires` +
  `executor.drain` (:353-407), and the `_autonudge_owns` fence (:260-280).
* `triggers/loop.py:154` — `tick_once` calls `_poll_idle` once per tick (≤30s cadence),
  above the empty-fires early return.
* Precedent (`WF2AUT-4`): heartbeat/lifecycle-hook/commitment conversion — deterministic
  `system:*` trigger ids re-registered idempotently on boot, `KIND_RUNTIMES` census test
  (`tests/test_triggers_chain.py:245`) naming one runtime per kind.
* `triggers/disposition.py:104-120` — the autonudge row: verdict ABSORBED, keeps
  {reactive re-arm, delivered-only counting, mid-turn drop == overlap:skip, stop-sentinel,
  error_count deactivation}, note "LAST — blocked on LOOPS-EVOLUTION Phase 4".

## DECISION: (b) — the substrate already carries the semantics; autonudge becomes a thin adapter over it, then dies

**Not (a).** A new kind is unnecessary and explicitly costed: `idle` already exists in
`KINDS` with declared spec keys, NL phrasings, and a shipped runtime whose docstring
(idle_poll.py:1-77) was written as half 1 of THIS atom and names the preserved autonudge
semantics one by one. §2's disposition row says ABSORBED **as `kind:idle`** — the plan never
asked for a second kind, and `KIND_RUNTIMES` already maps `idle → idle_poll`.

**(b), concretely.** The port swaps autonudge's ENGINE for the substrate's while keeping its
API (the duck type every consumer already speaks):

1. **State moves into the trigger store.** Each nudge loop becomes a
   `Trigger{kind: "idle"}` row in `triggers.json` (id `nudge:<8hex>` — the wire keeps
   exposing the full id; `name` derived from the session; `session:
   "conversation:<session_key>"`; `created_by` per caller). `SPEC_KEYS["idle"]` widens by
   three keys — `message`, `max_cycles`, `stop_sentinel_path` — the same §6 argument as the
   `interval` clock kind: the store must be able to carry what the migration puts in it.
   `cycle_count`/`last_fire`/`armed_at` stay in the idle SIDECAR (they churn per turn;
   writing them onto the row would rewrite `triggers.json` constantly — S61d). The sidecar
   gains `error_count` (runtime state, same churn argument).
2. **The tick engine dies; due-ness rides the substrate tick.** No more per-loop asyncio
   timers. `idle_poll.due_fires` (off `tick_once`, ≤30s cadence) decides when a nudge row
   is due from `armed_at` + `wait_secs` — the identical quiet-period predicate, with
   `first_idle_secs` keyed on `cycle_count == 0` exactly as before.
3. **Message-bearing idle rows route to the chat-nudge deliverer, not the wake path.**
   `idle_poll.poll` sends a due idle row with a non-empty `spec.message` to the nudge
   adapter's `deliver()` (which holds the gateway-injected `on_fire` — the unchanged loop
   cycle driver) instead of `wakeup.dispatch_fires`. This is a semantic split, not an
   ownership hack: an idle row with a message means "inject this into the bound
   conversation" (autonudge's contract); one without means "wake the target" (half 1's
   contract). `deliver()` carries the ordered pre-checks verbatim: stop-sentinel → remove;
   `max_cycles` → deactivate; `session.running` or `_suppress_autonudge_rearm` → not
   delivered (the mid-turn drop, plus the re-prompt-gap fence the suppression flag gave the
   timer world); missing session → remove. Delivered-only counting is `record_delivery`,
   already shipped.
4. **Backpressure keeps its exact call-site contracts.** The adapter (new home:
   `src/personalclaw/triggers/nudge.py`, class name kept `AutoNudgeService`, singleton kept
   `get_instance()`) implements `notify_turn_complete(session, errored=)` as: restamp
   `armed_at = now` for its rows on that session (the timer re-arm, as state — the same
   translation half 1 ratified for `notify_activity`) + the 3-consecutive-errors
   deactivation with the `errored_out` observer event; `notify_user_input` restamps
   `armed_at` (cancel-as-state; the turn that input starts restamps again at turn end, so
   the arm point converges on turn-end exactly as the timer world's did).
   `chat_runner.py`/`chat_handlers.py` keep their fail-open try/except shape — only the
   import path changes.
5. **Everything else keeps its duck type.** `add/update/remove/remove_sync/get_by_session/
   list_all/subscribe/start/stop` and the `NudgeLoop` view (assembled row+sidecar, so
   `asdict()` keeps the HTTP wire byte-compatible) survive; `loop/manager.py`,
   `loop/watchdog.py`, `planning/runner.py` need zero changes except `manager.pause`'s
   private `svc._loops` scan, which becomes `svc.list_all()`. The gateway keeps its `_fire`
   and `_observer` verbatim; `_init_autonudge` constructs the adapter instead.
   `start()` restamps `armed_at = now` for every active row (the boot re-arm, :161-164).
6. **Migration is lossless and one-way.** On `start()`, if `autonudge.json` exists its
   loops are converted to rows + sidecars and the file is renamed `autonudge.json.migrated`
   (the `migrate_from_crons` / boot_migrate idiom). Every `NudgeLoop` field has a
   destination (see 1); nothing is dropped.
7. **The fence dissolves into the routing.** `_autonudge_owns` skipped user idle rows on
   sessions autonudge owned. Post-port both populations live in ONE store, so the fence's
   question becomes "does another message-bearing row own this session" — same skip reason,
   same fail-open probe, now answered without a second store.
8. **Then the deletion.** All ten importer files repoint to `triggers.nudge`;
   `src/personalclaw/autonudge.py` is deleted in the same PR (the ruling: trivially true
   once the port lands). The `disposition.py` autonudge row's note flips from "LAST —
   blocked" to the landed wording. No new kind → no `KIND_RUNTIMES` change; no new
   route/tool → no offline-reference regen; no hook-provider or rung surface touched.

**What deliberately does NOT change:** the loop-cycle driver's behavior (re-prompt budget,
fresh-ACP retry, watchdog outcome reporting, turn timeouts) — it moves zero lines; the HTTP
wire shapes; the WS `autonudge_state` event vocabulary; `PERSONALCLAW_AUTONUDGE` as the
adapter's feature flag (user idle rows were never gated by it and still are not).

**Accepted deltas, named:** (i) fire latency gains the tick's ≤30s quantization — a nudge
due at `T` fires at the first tick ≥ `T` (autonudge's timer was exact); loops run 15-20 min
cycles with ≥60s idle windows, so this is noise there, and user-facing nudges already
accepted this in half 1. (ii) nudge rows become visible, pausable rows on the Automations
page — that is the unification's point (§2: one store), not a leak. (iii) restart behavior
strictly improves: rows and arm points persist where timers did not.
