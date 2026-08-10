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
| `WF2AUT-6` | ⬜ | Wire the view kind's on_render runtime to a production render surface | `WF2AUT-4`, `WF2AUT-5` | a real dashboard-tile/artifact render calls on_render(trigger,now) so a view trigger past its TTL actually refreshes (currently zero production callers); TTL-serve-cache verified; the gateway-must-not-import-as-loop test still holds |
| `WF2AUT-7` | ⬜ | web_watch headless-Chromium escalation tier + knowledge-store digest routing | `WF2AUT-4` | web_poll escalates plain net.fetch -> egress-guarded headless tier under one max_requests budget with ledger-logged escalations; digest output lands in the knowledge store (user items), not memory; web_poll.py stale no-browser-runtime docstring corrected |
| `WF2AUT-8` | ✅ | trigger_source provider seam (AUTO-A4): PROVIDER_TYPES + handler, manifest declaration, namespaced app:<name>:<event> bus sources | `WF2AUT-4`, `EXT:APP-PLATFORM-EVOLUTION:events-emit install-consent grant surfacing` | a fixture app's declared trigger_source fires an event trigger end-to-end with fenced+provenanced payload and frozen capabilities honored; disabling the app parks its bound triggers with a typed reason; test_manifest_types_match_handlers green; core contains no vendor names |
| `WF2AUT-9` | ⬜ | §3.5 skip_if_active liveness guard + acting_on resource claim on mutating triggers | `WF2AUT-3` | skip_if_active declared on the Trigger entity and evaluated at fire time via cheap liveness heuristics (dirty worktree/lockfiles/recent mtime); acting_on resource claim serializes two trigger-fired runs targeting the same resource; a busy target yields a typed deferred ledger row |
| `WF2AUT-10` | ⬜ | §5 did/suppressed fold affordance FE consumer | `WF2AUT-5` | the Automations runs-inbox surfaces the did-vs-suppressed fold control so archived/inert rows can be revealed on demand; backend archive split already present, this wires the FE toggle |
| `WF2AUT-11` | ⬜ | idle kind runtime for user automations + autonudge.py deletion (loop-ticker absorption) | `WF2AUT-3`, `EXT:LOOPS-EVOLUTION:Phase 4 loop-ticker before autonudge deletion` | kind:idle fires for user automations preserving reactive re-arm/delivered-only counting/mid-turn-drop; autonudge.py deleted and the loop tick engine rides kind:idle (this half only after LOOPS-EVOLUTION Phase 4) |
| `WF2AUT-12` | ⬜ | webhook kind fire endpoint + scoped token verification (E4-blocked) | `WF2AUT-5`, `EXT:MCP-READONLY-INBOUND:fail-closed inbound HTTP substrate`, `EXT:EXTERNAL-ACCESS:generalized inbound surface + owner E4 decision` | owner clears E4 and the inbound surface owner is decided; POST /api/triggers/{id}/fire verifies the SHA-256-hashed scoped bearer token and fences the payload; token_ref lint (shipped S119) then has a fire path to guard |

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

**Status:** todo

§2 autonudge.py ABSORBED as kind:idle (LAST); §7 step 9; §1.2 idle kind; Risks (Loops coupling)

**Done when:** kind:idle fires for user automations preserving reactive re-arm/delivered-only counting/mid-turn-drop; autonudge.py deleted and the loop tick engine rides kind:idle (this half only after LOOPS-EVOLUTION Phase 4)

### `WF2AUT-12` — webhook kind fire endpoint + scoped token verification (E4-blocked)

**Status:** todo

Status REMAINING BLOCKED (E4 owner decision); §1.2 webhook kind; §1.4 decision 12 scoped tokens; §7 step 6; S119/S123

**Done when:** owner clears E4 and the inbound surface owner is decided; POST /api/triggers/{id}/fire verifies the SHA-256-hashed scoped bearer token and fences the payload; token_ref lint (shipped S119) then has a fire path to guard

