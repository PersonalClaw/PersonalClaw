# WORKFLOWS-V2-AUTOMATION-SUBSTRATE — atomic plans

**Source plan:** [`WORKFLOWS-V2-AUTOMATION-SUBSTRATE`](../plans/WORKFLOWS-V2-AUTOMATION-SUBSTRATE.md)  
**Code:** `WF2AUT`  
**Source status:** in_progress



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `WF2AUT-1` | ✅ (##216-#250) | Substrate foundation: event bus + six-rule fencing hardening + trigger-store unification + lossless cron migration | — | one event bus with reliability contract; event_triggers.py absorbed; 8 dormant HOOK_EVENTS fire; fence_untrusted hardened (screen/strip/provenance/schema-extract/no-payload-match/ssrf); triggers.json is the single store behind /api/triggers with row-for-row cron migration + verify-migration diff empty + lenient parse/change-audit |
| `WF2AUT-2` | ✅ (##250-#543) | Clock-engine cutover: crash-safe scheduler + wakeup dispatch + missed-fire/catch_up + boot sweep + ScheduleService deleted | `WF2AUT-1` | tick is the sole clock engine (S100 cutover); persist-before-execute + exactly-one-upcoming + single-flight + boot stagger + lock self-expiry; headless unattended profile; missed-fire review card + per-trigger catch_up fires once staggered; ScheduleService class deleted (S112); kill-gateway-mid-fire test: no double/lost fire |
| `WF2AUT-3` | ✅ (##250-#543) | Records, health & safety: two-weight run records + typed outcomes + parking/autopause + budgets/caps/retention + capability allowlists + secrets | `WF2AUT-2` | ledger-only vs full runs with materiality classification; every suppressed fire is a typed ledger row (zero silent drops under 24h storm); autopause after 5 true failures; budget/rate/spend caps metered + fair per-job retention; frozen action-set fence + global kill switch + PathGuard enforced; {{secret:KEY}} never resolved in store/journal/ledger |
| `WF2AUT-4` | ✅ (##250-#543) | Lifecycle-hook/heartbeat/commitment conversion + new-kinds-wave-1 runtimes + quiet windows + duty-gate seam | `WF2AUT-1` | hook lifecycle events fire with agent-scoping preserved; heartbeat sub-tasks as visible system clock triggers; commitments as one-shot at triggers; file/web_watch/run_completed/view runtimes live (KIND_RUNTIMES test green); quiet_windows catch_up\|skip in fire path; duty_gate provider type + handler + built-in manual toggle, test_manifest_types_match_handlers green |
| `WF2AUT-5` | ✅ (##250-#543) | FE Automations page + runs inbox + durable approvals + delivery contract + Week tab + doctor + snapshot coverage | `WF2AUT-3`, `WF2AUT-4` | Automations page replaces pages/schedule+triggers; runs inbox renders typed outcomes read-only with statusUrl deep-link; approvals re-arm from disk on restart; automation.run.succeeded\|failed delivery via notification_allowed with stable event-id; Week grid matches recurrence engine; snapshot/portability carry triggers.json + ledger; schedule_* aliases retired |
| `WF2AUT-6` | ✅ | Wire the view kind's on_render runtime to a production render surface | `WF2AUT-4`, `WF2AUT-5` | a real dashboard-tile/artifact render calls on_render(trigger,now) so a view trigger past its TTL actually refreshes (currently zero production callers); TTL-serve-cache verified; the gateway-must-not-import-as-loop test still holds |
| `WF2AUT-7` | ✅ | web_watch headless-Chromium escalation tier + knowledge-store digest routing | `WF2AUT-4` | web_poll escalates plain net.fetch -> egress-guarded headless tier under one max_requests budget with ledger-logged escalations; digest output lands in the knowledge store (user items), not memory; web_poll.py stale no-browser-runtime docstring corrected |
| `WF2AUT-8` | ✅ | trigger_source provider seam (AUTO-A4): PROVIDER_TYPES + handler, manifest declaration, namespaced app:<name>:<event> bus sources | `WF2AUT-4`, `EXT:APP-PLATFORM-EVOLUTION:events-emit install-consent grant surfacing` | a fixture app's declared trigger_source fires an event trigger end-to-end with fenced+provenanced payload and frozen capabilities honored; disabling the app parks its bound triggers with a typed reason; test_manifest_types_match_handlers green; core contains no vendor names |
| `WF2AUT-9` | ✅ | §3.5 skip_if_active liveness guard + acting_on resource claim on mutating triggers | `WF2AUT-3` | skip_if_active declared on the Trigger entity and evaluated at fire time via cheap liveness heuristics (dirty worktree/lockfiles/recent mtime); acting_on resource claim serializes two trigger-fired runs targeting the same resource; a busy target yields a typed deferred ledger row |
| `WF2AUT-10` | ✅ | §5 did/suppressed fold affordance FE consumer | `WF2AUT-5` | the Automations runs-inbox surfaces the did-vs-suppressed fold control so archived/inert rows can be revealed on demand; backend archive split already present, this wires the FE toggle |
| `WF2AUT-11` | 🔴 | idle kind runtime for user automations + autonudge.py deletion (loop-ticker absorption) | `WF2AUT-3`, `EXT:LOOPS-EVOLUTION:Phase 4 loop-ticker before autonudge deletion` | kind:idle fires for user automations preserving reactive re-arm/delivered-only counting/mid-turn-drop; autonudge.py deleted and the loop tick engine rides kind:idle (this half only after LOOPS-EVOLUTION Phase 4) |
| `WF2AUT-12` | ⬜ | webhook kind fire endpoint + scoped token verification (E4-blocked) | `WF2AUT-5`, `EXT:MCP-READONLY-INBOUND:fail-closed inbound HTTP substrate`, `EXT:EXTERNAL-ACCESS:generalized inbound surface + owner E4 decision` | owner clears E4 and the inbound surface owner is decided; POST /api/triggers/{id}/fire verifies the SHA-256-hashed scoped bearer token and fences the payload; token_ref lint (shipped S119) then has a fire path to guard |
| `WF2AUT-13` | ✅ | §3.3 cursor rule call site: the spool drain acts on `drain_decision` instead of acking unconditionally | `WF2AUT-1`, `WF2AUT-2` | the spool drain classifies each re-entry into `Handling` at an explicit side-effect boundary, calls `drain_decision`, and acts on every `DrainAction` (consume/hold/give-up/skip-duplicate) with a durable retry budget; a failure AFTER the boundary is never retried; `SKIP_CYCLE` deleted for want of an honest producer; exhaustiveness ratchet over both enums with a raising tail |
| `WF2AUT-14` | ⬜ | Resume-target substrate: ratify shipped resume-targets + file the orphaned scope | — | The shipped resume-target surface is documented as the substrate contract (ratified as filed); the orphaned remainder (delta between original WF2AUT scope and what shipped 08-28) is enumerated and implemented or explicitly descoped with reasons; WF2LOO-9 consumes the contract without private workarounds; tests pin the contract surface. |

## Atom scopes

### `WF2AUT-1` — Substrate foundation: event bus + six-rule fencing hardening + trigger-store unification + lossless cron migration

**Status:** done (PR ##216-#250)

§7 steps 1-2; §3.3 event-bus delivery contract; §1.4 decision 4 (six fencing rules); §1 triggers.json store; §2 disposition (event_triggers/inbox-alerts ABSORBED); AUTO-R6/R4/R15

**Done when:** one event bus with reliability contract; event_triggers.py absorbed; 8 dormant HOOK_EVENTS fire; fence_untrusted hardened (screen/strip/provenance/schema-extract/no-payload-match/ssrf); triggers.json is the single store behind /api/triggers with row-for-row cron migration + verify-migration diff empty + lenient parse/change-audit

### `WF2AUT-2` — Clock-engine cutover: crash-safe scheduler + wakeup dispatch + missed-fire/catch_up + boot sweep + ScheduleService deleted

**Status:** done (PR ##250-#543)

§7 step 3; §3.1 crash-safe discipline; §3.2 inbox+wakeup dispatch; §3.4 missed fires; crit 7 & 12 (system-scheduler handoff); AUTO-R1/R16/R8; §2 schedule.py ABSORBED

**Done when:** tick is the sole clock engine (S100 cutover); persist-before-execute + exactly-one-upcoming + single-flight + boot stagger + lock self-expiry; headless unattended profile; missed-fire review card + per-trigger catch_up fires once staggered; ScheduleService class deleted (S112); kill-gateway-mid-fire test: no double/lost fire

### `WF2AUT-3` — Records, health & safety: two-weight run records + typed outcomes + parking/autopause + budgets/caps/retention + capability allowlists + secrets

**Status:** done (PR ##250-#543)

§7 steps 4 & 6; §1.3 record weights + typed outcome vocabulary + materiality; §3.6 budgets/triage; §3.7 health/typed-exits/parking; §1.4 decisions 7/11/12 (frozen capabilities, kill switch, PathGuard, {{secret:KEY}}); crit 3/8/11

**Done when:** ledger-only vs full runs with materiality classification; every suppressed fire is a typed ledger row (zero silent drops under 24h storm); autopause after 5 true failures; budget/rate/spend caps metered + fair per-job retention; frozen action-set fence + global kill switch + PathGuard enforced; {{secret:KEY}} never resolved in store/journal/ledger

### `WF2AUT-4` — Lifecycle-hook/heartbeat/commitment conversion + new-kinds-wave-1 runtimes + quiet windows + duty-gate seam

**Status:** done (PR ##250-#543)

§7 steps 5 & 8; §2 hooks.py/heartbeat.py ABSORBED; §1.2 kind specs (file/web_watch/run_completed/view/composite/condition/vcs/sequence); Amendment AUTO-A1 (quiet windows, tz/jitter/skip_dates) + AUTO-A2 (DutyGateProvider)

**Done when:** hook lifecycle events fire with agent-scoping preserved; heartbeat sub-tasks as visible system clock triggers; commitments as one-shot at triggers; file/web_watch/run_completed/view runtimes live (KIND_RUNTIMES test green); quiet_windows catch_up|skip in fire path; duty_gate provider type + handler + built-in manual toggle, test_manifest_types_match_handlers green

### `WF2AUT-5` — FE Automations page + runs inbox + durable approvals + delivery contract + Week tab + doctor + snapshot coverage

**Status:** done (PR ##250-#543)

§7 steps 7 & 9 (partial); §5 FE (triggers list, runs inbox, templates); §5.2 durable approval objects (AUTO-R13); §1.4 decision 13 delivery contract (AUTO-R18); AUTO-A3 Week grid; §4.1 doctor; §7 step 9 snapshot/portability

**Done when:** Automations page replaces pages/schedule+triggers; runs inbox renders typed outcomes read-only with statusUrl deep-link; approvals re-arm from disk on restart; automation.run.succeeded|failed delivery via notification_allowed with stable event-id; Week grid matches recurrence engine; snapshot/portability carry triggers.json + ledger; schedule_* aliases retired

### `WF2AUT-6` — Wire the view kind's on_render runtime to a production render surface

**Status:** todo

Status REMAINING; §1.2 view kind; §7 step 8; triggers/pull_on_view.py; AUTO-R10

**Done when:** a real dashboard-tile/artifact render calls on_render(trigger,now) so a view trigger past its TTL actually refreshes (currently zero production callers); TTL-serve-cache verified; the gateway-must-not-import-as-loop test still holds

### `WF2AUT-7` — web_watch headless-Chromium escalation tier + knowledge-store digest routing

**Status:** todo

Status REMAINING (NEWLY STARTABLE via web/render.py js-render extra); §1.2 web_watch; §3 escalating-fetch-with-budget; Plug-in Map egress; S121 DEVIATIONs

**Done when:** web_poll escalates plain net.fetch -> egress-guarded headless tier under one max_requests budget with ledger-logged escalations; digest output lands in the knowledge store (user items), not memory; web_poll.py stale no-browser-runtime docstring corrected

### `WF2AUT-8` — trigger_source provider seam (AUTO-A4): PROVIDER_TYPES + handler, manifest declaration, namespaced app:<name>:<event> bus sources

**Status:** ✅ done (2026-08-09, PR #948)

Amendment round-2 AUTO-A4; §7 step 8; Plug-in Map (#47 rule); apps/manifest.py, providers/registry.py, event-bus ingestion, sdk/ facade

`SOURCE_APP` had been declared in `event_triggers.EVENT_SOURCES` since EIAT-1 with **no producer and no pattern** — an enum member nobody writes. This atom is its producer. An app declares `provider: {type: "trigger_source"}` and implements `TriggerSourceProvider` (new `trigger_sources/base.py`, reached only via `personalclaw.sdk.trigger_source`), declaring the event NAMES it emits. `TriggerSourceTypeHandler` registers it into the flat `trigger_sources` registry on enable and starts its watch as a task; `PROVIDER_TYPES` gains `trigger_source` in the SAME commit (the #47 rule, asserted in **both** directions — the shipped guard only checks `handlers ⊆ PROVIDER_TYPES` and would miss a type with no handler). `trigger_sources.emit` is the ONE ingestion point: it derives the namespace from the REGISTERED name (`app:<app>:<event>`), never from the payload, so an app cannot emit into another app's namespace, and it fences **at origin** with rich provenance (`source_type=app:<name>`, `transformation_path=app-source:emit`) per the `web_watch` precedent. Both downstream fences are now idempotent via `security.is_fenced` (never `UNTRUSTED_OPEN in text`, which misses attributed fences and fails OPEN). The new `AppEvent` pattern globs the namespaced `event_type` via `event_glob` — ONE pattern, not two, because both app variants read `event_type` (unlike the inbox split, whose matchers read different `meta` fields), so the catch-all is just an empty glob. Every other gate governing an `event` fire is inherited unchanged (injection screen, frozen capability fence, denylist, incident mode, debounce, rate cap) because the app path re-enters through the same `emit_event` seam a memory write uses. On disable the source is unregistered BEFORE parking (closing ingestion first), then `park_for_app` reuses `autopause.evaluate(TRANSPORT_UNAVAILABLE)` for state/health/reason/cooldown — reversible, and re-enabling un-parks. **DEVIATION:** `test_agent_scope_validation`'s pinned `EVENT_PATTERNS` tuple gains `AppEvent`; the guard's real invariant (no source is a chat turn ⇒ `agent_scope` legitimately unread) still holds, since an app naming its event `chat_turn` still arrives with `source=app` — only a new `EVENT_SOURCES` member can trip the pin, which is what it was written to catch. The APE-side install-consent GRANT surfacing (`eventSubscriptions`, the platform event registry) stays with `APE-1`/`APE-2`, its EXT dep.

**Done when:** a fixture app's declared trigger_source fires an event trigger end-to-end with fenced+provenanced payload and frozen capabilities honored; disabling the app parks its bound triggers with a typed reason; test_manifest_types_match_handlers green; core contains no vendor names

### `WF2AUT-9` — §3.5 skip_if_active liveness guard + acting_on resource claim on mutating triggers

**Status:** todo

Status REMAINING (undeclared anywhere today); §3.5 foreground-yield/resource-slots; AUTO-R9

**Done when:** skip_if_active declared on the Trigger entity and evaluated at fire time via cheap liveness heuristics (dirty worktree/lockfiles/recent mtime); acting_on resource claim serializes two trigger-fired runs targeting the same resource; a busy target yields a typed deferred ledger row

### `WF2AUT-10` — §5 did/suppressed fold affordance FE consumer

**Status:** todo

Status REMAINING (no FE consumer); §5.2 runs inbox (no-op/suppressed rows auto-archived out of default view); §1.3 archive split (backend shipped S165)

**Done when:** the Automations runs-inbox surfaces the did-vs-suppressed fold control so archived/inert rows can be revealed on demand; backend archive split already present, this wires the FE toggle

### `WF2AUT-11` — idle kind runtime for user automations + autonudge.py deletion (loop-ticker absorption)

**Status:** BLOCKED — half 1 (`kind:idle` for user automations) shipped; half 2 (autonudge deletion)
is NOT startable. Verified against code 2026-08-11: `autonudge.py` is still the tick engine for the
live legacy Loops engine — `LoopWatchdog` takes the service as a constructor argument
(`gateway.py:2110`), `loop/watchdog.py:445` reads `NudgeLoop.active`/`.cycle_count` as loop
lifecycle truth, and `loop/manager.py:211,458` arms every cycle through `svc.add`. Nothing in
`triggers/` replaces that: `triggers/loop.py` has ZERO references to `NudgeLoop`/`loop.manager`, and
`idle_poll`/`wakeup`/`executor` have ZERO occurrences of `_run_chat`/`record_turn_outcome`/
`stop_sentinel`. `triggers/loop.py:103 tick_once` shipping the idle TRIGGER runtime is not the
loop-ticker this atom's EXT names. Deleting the module today would break every live loop at import.
Unblock by porting the loop-cycle driver off autonudge first, per LOOPS-EVOLUTION Phase 4's own
3-step endgame. See the AUTOMATION-SUBSTRATE Execution log.

§2 autonudge.py ABSORBED as kind:idle (LAST); §7 step 9; §1.2 idle kind; Risks (Loops coupling)

**Done when:** kind:idle fires for user automations preserving reactive re-arm/delivered-only counting/mid-turn-drop; autonudge.py deleted and the loop tick engine rides kind:idle (this half only after LOOPS-EVOLUTION Phase 4)

### `WF2AUT-12` — webhook kind fire endpoint + scoped token verification (E4-blocked)

**Status:** todo

Status REMAINING BLOCKED (E4 owner decision); §1.2 webhook kind; §1.4 decision 12 scoped tokens; §7 step 6; S119/S123

**Done when:** owner clears E4 and the inbound surface owner is decided; POST /api/triggers/{id}/fire verifies the SHA-256-hashed scoped bearer token and fences the payload; token_ref lint (shipped S119) then has a fire path to guard

### `WF2AUT-13` — §3.3 cursor rule call site: the spool drain acts on `drain_decision` instead of acking unconditionally

**Status:** done (PR PENDING)

§3.3 consumer cursor rule ("the `trigger-spool.jsonl` drain adopts this rule"); §3.2 peek-then-deliver-then-ack; §7 crit 7 (no double-fire / no lost fire) and crit 8 (no silent drop); `triggers/dispatch.py` + `triggers/loop.py`

**Done when:** the spool drain classifies each re-entry into `Handling` at an explicit side-effect boundary, calls `drain_decision`, and acts on every `DrainAction` (consume/hold/give-up/skip-duplicate) with a durable retry budget; a failure AFTER the boundary is never retried; `SKIP_CYCLE` deleted for want of an honest producer; exhaustiveness ratchet over both enums with a raising tail

**Design**

The finding: `triggers/dispatch.py` shipped a complete, documented, unit-tested decision layer for
the event drain with **zero production callers**. `Handling` ("the vocabulary that makes 'never drop'
checkable"), `DrainAction`, `drain_decision` and `classify_handler_outcome` were referenced nowhere
in `src/`. Meanwhile the real drain — `loop.py::_drain_spool` — re-entered each spooled envelope
through `emit_event`, caught everything with a `logger.warning`, and did `handled += 1`
**unconditionally**, then acked the whole batch. Its own comment said holding "would retry it on
every tick forever — the poison pill `drain_decision` names", naming the function it never called.
So §3.3's retry/hold policy, its bounded budget and its poison-pill give-up were all inert, and a
transient re-entry failure was silently swallowed as a delivered fire. §3.3 states the rule and says
"the `trigger-spool.jsonl` drain adopts this rule" — so this atom is that adoption, not new policy.

**The side-effect boundary is the constraint that shapes everything.** A double-fire is the one
outcome §7 crit 7 bans, and `handled += 1` on failure existed precisely to guarantee at-most-once.
Introducing HOLD means an envelope can run twice, so the boundary must be explicit: the drain may
only hold a failure that provably happened **before** any side effect. `emit_event` is that line. Its
pre-flight (read the envelope, split `kind`, build kwargs, resolve `get_engine()`) touches no store
and fires no trigger; once `emit_event` is entered it matches every stored trigger and schedules
their actions, and the drain cannot know how far it got — so "I don't know" resolves to DELIVERED.
The two halves are separated by two `try` blocks rather than a comment, and `get_engine()` is the
last pre-flight step deliberately: `emit_event` swallows an unreachable engine into a `logger.debug`,
so resolving it above the line converts a silent total loss of the batch into a bounded retry.

**HOLD is head-of-line, because `clear_spool(handled=N)` can only express a PREFIX ack.** There is no
"keep line 2, ack lines 1 and 3". Extending it to an arbitrary keep-set was rejected: it would have
to re-serialize lines a concurrent `spool_fire` may have appended since the read, trading a bounded
retry for a lost fire. So the drain acks the prefix it consumed, stops at the first envelope it must
hold, and leaves that envelope plus the tail on disk in order. Blocking is bounded by
`MAX_TRANSIENT_RETRIES` ticks, and the transient this exists for (engine unreachable) fails the whole
batch identically anyway.

**The retry budget is durable, or there is no hold.** `held_retries` lives in a one-record sidecar
(`trigger-spool-hold.json`) keyed on the deterministic `event_id` — not on `Cursor.seq`, because
every spooled envelope is written with `seq=0`. One record suffices because HOLD is head-of-line,
which also makes the file self-pruning. A sidecar rather than a field on the spooled line for the
same append-path reason as above. `write_spool_hold` returns whether it persisted, and the caller
honours it: a hold that cannot record its count is **acked loudly instead of held**, because an
unbounded retry across process lifetimes is strictly worse than the unconditional ack it replaced.

**Per skip_* member.** `SKIP_DUPLICATE` is WIRED: `event_id`/`payload_hash` are deterministic over
(source, kind, payload), so two spool lines sharing a hash inside `DEDUP_WINDOW_SECS` are exactly
what the window was written to collapse; the shipped `is_duplicate` is the producer, compared on
emit times so a family seen an hour apart stays two facts. `SKIP_CYCLE` is **DELETED**: a cycle skip
needs a `trigger_id` to compare `Envelope.spawned_by` against, and the drain has none (it re-enters
through `emit_event`, which matches against every stored trigger), and nothing in production writes
`spawned_by` at all. `cycle_guard`, which returns a plain `(bool, reason)`, stays the only expression
of that rule.

**Implementation plan**

1. `dispatch.py`: delete `DrainAction.SKIP_CYCLE` and document why; give `drain_decision` an explicit
   `TRANSIENT` branch plus a raising tail (the transient rules were the *fallthrough*, so an unknown
   handling string silently inherited "retry five times then drop"); document the prefix-ack
   constraint on `clear_spool`; add `spool_hold_path` / `read_spool_hold` / `write_spool_hold` /
   `clear_spool_hold`.
2. `loop.py`: add `_reenter_spooled` — the boundary, returning `(handling, detail)` and calling
   `classify_handler_outcome` for a pre-flight throw, `Handling.PERMANENT` for an envelope with no
   event kind (it used to reach `emit_event` with an empty `event_type`, match nothing, and count as
   delivered — a drop wearing a success), and `Handling.DELIVERED` for anything past the line.
3. `loop.py`: rewrite `_drain_spool` as the call site — dedup check, `drain_decision`, a branch per
   `DrainAction` with a raising tail, prefix ack, sidecar write-or-ack.
4. Tests in `tests/test_triggers_loop.py`, all driving the REAL drain via `tick_once`: hold, the
   post-boundary never-retried pin, budget durability to give-up, head-of-line ordering, duplicate
   collapse, the window not collapsing two facts, permanent, unbudgetable hold, plus the ratchet
   (AST over both functions' source + both raising tails + a producer per `Handling` member).
5. Regenerate `inert-surface-baseline.json` (a legitimate shrink) in the same commit.

