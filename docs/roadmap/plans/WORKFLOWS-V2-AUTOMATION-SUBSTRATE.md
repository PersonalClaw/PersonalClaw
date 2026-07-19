# Plan: One Automation Substrate — Triggers Fire (or Resume) Workflow Runs

**Status:** PROPOSED (rev 2 — research-integrated 2026-07-12)  
**Created:** 2026-07-11  
**Depends on:** WORKFLOWS-V2.md Slices 0-2 (run engine + retention); final step blocked on WORKFLOWS-V2-LOOPS-EVOLUTION Phase 4  
**Scope:** Unify crons, lifecycle hooks, event triggers, autonudge, heartbeat tasks, and inbox alerts onto one substrate

---

## Research Integration (2026-07-12)

Twenty approved recommendations folded in (mechanism-level, not appendix):

- **AUTO-R1** — crash-safe scheduling discipline → §3.1
- **AUTO-R2** — typed fire/run outcome vocabulary + overlap policy → §1.3, §5.2
- **AUTO-R3** — creation-time capability allowlist / write scopes → §1.4 decision 7
- **AUTO-R4** — hardened `fence_untrusted` (screen, strip, provenance, typed extraction) → §1.4 decision 4
- **AUTO-R5** — cost/budget gates, background model tier, fire→spawn triage → §1.2 gates, §3.6
- **AUTO-R6** — event-bus delivery/consumer reliability contract → §3.3
- **AUTO-R7** — health rollups, typed run exits, parking (no circuit breakers) → §1.1, §3.7
- **AUTO-R8** — missed-fire review + opt-in `catch_up` → §3.4
- **AUTO-R9** — foreground-yield + resource-slot arbitration → §3.5
- **AUTO-R10** — pull kinds `view` + `web_watch` → §1.2 kind table
- **AUTO-R11** — resume-targets (trigger resolves gates in existing runs) → §1.1 `workflow` field, Overview
- **AUTO-R12** — trigger entity extensions bundle (one-shot union, condition gates, retry, idempotency, chain/vcs/datetime-list variants) → §1.1, §1.2
- **AUTO-R13** — durable approval objects, unattended-vs-attended timeouts → §5.2
- **AUTO-R14** — `{{secret:KEY}}` server-side secret templating → §1.4 decision 12
- **AUTO-R15** — substrate integrity tooling (OS-scheduler gate, doctor, lenient parse, audit) → §4.1, §7
- **AUTO-R16** — inbox+wakeup dispatch, wake-vs-resume, unattended `headless` permission profile → §3.2
- **AUTO-R17** — composite trigger shape, substrate recursion guard, IO-only hook doctrine → §1.2, §1.4 decisions 5/10
- **AUTO-R18** — outbound delivery contract (stable event-id, statusUrl, destination formatting) + Run Now/Duplicate → §1.4 decision 13, §5.1
- **AUTO-R19** — proactive `pulse` kind + standing delegations (Phase 2, deferred) → §1.2, §7 step 10
- **AUTO-R20** — opt-in `observe` screen kind (Phase 2, deferred, app-delivered) → §1.2, §7 step 10

---

## Overview

Every background behavior in PersonalClaw becomes one sentence: **a Trigger fires — or resumes — a WorkflowRun of a WorkflowDef.** Trigger = *when*. WorkflowDef = *what* (graph of v2 nodes; degenerate case = one action node). WorkflowRun = *what happened* (journaled, per-step, resumable). "Resumes" is new (AUTO-R11): a fire may resolve a wait/gate node in an *existing* run instead of starting a new one.

Today the codebase has ~6 trigger stores (`crons.json`, `hooks.json`, `event_triggers.json`, autonudge state, HEARTBEAT.md, inbox alert-keywords), ~5 match-then-act engines (hook matcher, event-trigger patterns, inbox keyword matcher, fs-watch, heartbeat parser), and ~5 separately-built unattended-LLM pipelines (cron agent turns, heartbeat BACKGROUND_KEY turns, autonudge injection, event-trigger spawns, hook spawns). These are the same concept wearing different clothes.

**Soul guardrail:** this is a *personal* substrate — one user, one gateway, plain files under `~/.personalclaw`. The n8n idea we steal is the *shape* (trigger → graph → run history), not the enterprise machinery. No queues-as-a-service, no RBAC, no visual canvas as primary authoring. Proactive behaviors propose; they never silently write.

### Starting points (verified against code, 2026-07-12 recon)

The design below builds on what actually exists — not on the idealized versions earlier drafts assumed:

- **There is no timer heap.** `ScheduleService` runs a *single re-armed asyncio task* (`_arm_timer`, schedule.py L1070) sleeping `min(next-due-delay, 30s)`, with cron-expr dueness decided by `croniter.match` on the current minute (`_is_due` L1215) plus a same-UTC-minute refire guard. The TriggerService generalizes *this* mechanism (§3.1) — it does not "extend a heap."
- **Jitter is deterministic per job id** (BLAKE2b of id → fraction of window, L1156-1213), not random and not configurable; `strict_schedule=True` is the only opt-out. New stagger behaviors (boot catch-up, restart spread) reuse the same deterministic-hash pattern.
- **The execution model is already unified.** `ScheduleJob`'s what-runs is ONE canonical `action {provider, config}` (L145); legacy fields (`message`/`script`/`command`/…) are read-only `@property` projections (L152-208). The migration is a *store/entity* unification, not an execution-model rewrite.
- **A unified triggers facade already exists.** `dashboard/handlers/triggers.py` serves `/api/triggers` over the three stores with namespaced ids `schedule:<id>` / `lifecycle:<id>` / `event:<id>`. Store unification (§7 step 2) starts from *that* facade — the API shape survives, the backing stores merge.
- **Only 7 of the 15 `HOOK_EVENTS` actually fire today**: SessionStart, AgentSpawn, UserPromptSubmit, PreToolUse, PostToolUse, Stop, Error (chat_runner.py L1441-2558 + `fire_tool_hooks`). PreResponse, PostResponse, MemoryWrite, ContextCompact, SubagentSpawn, TaskComplete, ApprovalRequest, SessionEnd have **no fire sites**. Chat-turn hooks fire *agent-scoped* (`fire_for_ids` via `AgentProfile.triggers`) — there is no global firing path from chat turns. Wiring the missing 8 is a deliverable (§7 step 1), not an assumption.
- **`event_triggers.py` fires only on memory writes** (`vector_memory._log_event` → `emit_memory_event` is the sole emitter), and a sync-context fire (CLI write, no running loop) records `fire_count` but **skips the action** (L209) — the spool fix in §3.3 addresses a real, verified hole.
- **Autonudge is reactive, not periodic**: `notify_turn_complete` re-arms the idle timer per turn; only *delivered* nudges count toward `max_cycles`; a mid-turn session drops the nudge (no queueing, deliberate). That drop IS `overlap: skip` semantics — the substrate names and generalizes it.
- **Heartbeat is a hard-coded 60s loop** with tick-modulo sub-tasks (FTS rebuild every 15 ticks, daily prunes at 1440) — the interval is not config-exposed; changing it would silently shift the modulo cadences. Conversion (§7 step 5) makes each sub-task an explicit clock trigger instead.
- **Autopause-after-5 already exists for the cron action path** (gateway `_maybe_autopause` in `_run_action_job`). The substrate *generalizes* it to all kinds — it does not invent it.
- **Autonudge counts only delivered fires; inbox alerts evaluate once at ingestion; app crons are force-silent and reconciled at startup** — all preserved semantics, called out in §2.

---

## 1. The Trigger Entity

One store: `~/.personalclaw/triggers.json` (fcntl + atomic write, absorbing crons.json / hooks.json / event_triggers.json / autonudge config). Parsed with **never-throw structural validation** (AUTO-R15): typed issue records + closest-match resolution rendered as WARNING chips — an agent-authored near-miss must never become a silently-dead trigger.

### 1.1 Dataclass

```python
@dataclass
class Trigger:
    id: str                   # deterministic where minted by a feature (e.g. "system:heartbeat:fts") so re-registration is idempotent (R1)
    name: str                 # unique per concern — creation refuses duplicates, surfaces the existing trigger (R15)
    enabled: bool
    created_by: str           # user | app:<name> | agent | workflow | system:<feature>
    kind: str                 # clock | event | idle | file | webhook | view | web_watch | manual  (+ Phase 2: pulse, observe)
    spec: dict                # per-kind (below); may include composite gates (R17)
    gates: dict               # {debounce_secs?, rate_cap?, max_fires?, skip_dates?, quiet_hours?,
                              #  cost_cap?, max_cost_usd_per_run?, max_actions_per_hour?,        (R5)
                              #  cooldown_secs?, idempotency: bool?, threshold: int?,            (R12)
                              #  condition?: {checkType, compareMode, …},                        (R12)
                              #  max_runs_per_hour?}                                             (R8; manual fires bypass)
    capabilities: dict        # frozen at save (R3): {allowed_actions: [provider names],
                              #  allowed_write_scopes: {paths: [], entity_kinds: []}, network: bool}
    workflow: dict            # {ref: <def name>} | {inline: <single-action spec>} | {resume: {run_id, node_id}}  (R11)
    overlap: str              # skip | queue | parallel — default skip (R2); fire-time claim lock
    session: str              # fresh | pinned:<key> | conversation:<chat session key>
    model_tier: str           # background | standard — resolved ONCE via config (R5)
    delivery: str             # none | notify | channel:<target> | inbox   (contract in decision 13)
    failure_delivery: str     # separate route for failures — failures reach the inbox even when delivery:none (R12)
    retry: dict               # {attempts, backoff: [secs, …]} per typed exit (R12/R7)
    failure_policy: dict      # {autopause_after: 5, dedupe_hash: true} — per-EXIT-TYPE (R7): only true failures count
    yield_to_user: bool       # background fire waits for a quiet window (R9)
    resource_slots: list      # named slots this trigger's runs claim, e.g. ["local-llm"] (R9)
    catch_up: bool            # fire ONCE at boot/wake if the last slot was missed (R8); default false
    expires_at: str           # default auto-expiry on user-created recurring triggers, deliberate renewal (R12)
    # --- runtime / rollup fields (written by the service, never by forms) ---
    next_fire_at: str         # persisted BEFORE execution (R1)
    last_run_id: str
    run_count: int
    last_success_at: str      # health rollups (R7) — lists render status dots without scanning runs
    last_failure_at: str
    health_status: str        # ok | degraded | parked | failing
    last_error_summary: str
    state: str                # active | paused | autopaused | parked | quarantined | retired
```

`spawned_by` lineage and `provenance_chain` ride the *fire/run records*, not the trigger row (decision 5).

### 1.2 Per-Kind Specs

| Kind | Spec | Source Semantics |
|---|---|---|
| `clock` | tagged union `{kind:'cron', expr}` \| `{kind:'at', at, delete_after_run: true}` \| `{kind:'sequence', at: [datetimes]}` (R12); tz (IANA), deterministic jitter (kept verbatim from schedule.py), `strict`, `skip_dates` | Everything `schedule.py` has today. `at` one-shots default `delete_after_run: true` + a create-grace-window rule so auto-deleted one-shots vanish instead of resurrecting — what §7 step 5's commitment one-shots need. NL authoring via existing `nl_to_cron`. Min-interval floor: 15 min default for LLM-invoking clock triggers, overridable (R1) |
| `event` | `{source, pattern: {glob?, regex?, keywords?}}` | ONE event bus + ONE matcher grammar unifying three vocabularies: the agent-lifecycle `HOOK_EVENTS` (**7 fire today — SessionStart, AgentSpawn, UserPromptSubmit, PreToolUse, PostToolUse, Stop, Error; the other 8 get fire sites wired in §7 step 1**), data events (MemoryUpdate/MemoryKeyPattern/ContentMatch — today emitted only by memory writes), and new sources the code already produces but nobody consumes: `FileChanged` (fs_watch), `InboxItemIngested` (inbox). Completeness rule (R6): every entity CRUD in the gateway emits a typed event on the one bus. **Taxonomy discipline (R12): one trigger = one narrow event** — narrowness is what keeps storm guards simple. Payload content NEVER participates in pattern matching (decision 4e) |
| `run_completed` | `{source_trigger \| source_def}` | Chain kind (R12): fires when a named trigger/def's run completes, payload `{chainSource, previousResult}` with `previousResult` delivered FENCED; composes with the `spawned_by` recursion guard (decision 5) so chains can't cycle |
| `idle` | `{scope: session:<key> \| gateway, idle_secs, first_idle_secs}` | Autonudge's predicate, generalized. **Recon note:** autonudge is *reactive* — re-armed by `notify_turn_complete`, cancelled by user input, delivered-only counting; those semantics carry over verbatim |
| `file` | `{paths: [globs], dedup: (path, content_hash)}` | Sugar over `event/FileChanged`. Content-hash dedup keyed on (path, content_hash), not path-only (R12); fires carry the **changed-file delta payload** so fired workflows foreach only over new items. Ships a `vcs` preset (watch `.git/refs/heads/*`) for on-commit automations |
| `webhook` | `{token_ref}` → `POST /api/triggers/{id}/fire` | Per-trigger bearer token stored in the secrets store as `{{secret:…}}`, SHA-256-hashed at rest, never verbatim in triggers.json (R14). Optional freeform text payload — fenced + screened (decision 4) |
| `view` | `{surface_binding, ttl_secs}` | Pull-on-view (R10): fires when a bound surface (dashboard tile, artifact open) renders past TTL; within TTL serve cache. Refreshes are ledger-only rows carrying per-refresh token cost; per-trigger rate caps; the runs inbox shows a freshness column. Sidesteps the 1440-run-dirs critique by never firing unviewed |
| `web_watch` | `{url, poll_interval, extraction: auto\|selectors, novelty_key}` | Fires ONLY when new items appear (guid/novelty-keyed seen-set — the seen-set IS the storm guard). Per-connection cursor + dedup set + daily request budget. Fetching goes through the **`net.fetch` egress chokepoint** with escalating-fetch-with-budget (plain fetch → optional headless tier, one `max_requests` budget per firing, escalations ledger-logged). Digest output lands in the **knowledge store** (user items), never in memory |
| `manual` | — | Run-now / replay / dry-run (T9 observe-mode kept; recon: dry-run against bash/run-script/webhook is *refused and recorded as a preview* — only run-prompt/run-workflow truly dry-run). Manual fires bypass min-interval + `max_runs_per_hour` but never `max_requests_per_sec`-class floors (R6d) |
| `pulse` *(Phase 2)* | `{watch: [memory-delta classes], confidence_floor}` | Proactive kind (R19): watches **memory/context deltas** (episodic entries, task/calendar signals — explicitly NOT raw screen frames; this is MEMORY, the harness's own mechanics — see decision 14) and generates a BOUNDED queue of proposed matters with confidence scores, surfaced as needs-input proposals, never silently executed. Accept/decline feedback drives escalating per-matter-class suppression cooldowns; only repeatedly-accepted low-risk classes may graduate to auto-execute. Standing delegations = ONE visible delegation object bundling a recurring trigger + the R5 triage gate + learned filters |
| `observe` *(Phase 2, opt-in, app-delivered)* | `{interval, exclusions: [app/window], batch_n}` | Screen-observation ingestion (R20): capture daemon → idle filter (discard unchanged frames) → accumulator (batch N frames = one fire, never per-frame — the accumulator IS the storm guard) → typed multi-modal extraction into memory/knowledge/tasks. Privacy structural: the capture toggle renders as a visible edit-locked `created_by: system` row; all processing local; media compression in a subprocess (GIL avoidance). Ships as an **app-delivered provider**, not core (§ Plug-in Map) |

**Composite trigger shape (R17):** any event/clock trigger may declare a conjunction — event wake source + time-window gate + input-hash idempotence — evaluated at fire time ("on session_end, if hour≥22 and content_hash ≠ last_processed, fire once"). The time-window and hash checks are free gates; only the event is the wake source. Replaces the two-triggers-plus-manual-dedup workaround for "end-of-day compilation" patterns.

**Condition gates (R12):** an optional LLM-free `condition` on clock/event triggers, evaluated at fire time — matrix: `checkType` http/command/file/agent × `compareMode` hash/status/jsonpath/regex, with persisted `last_state` + consecutive-unchanged counter; agent-evaluated conditions are an explicit expensive tier. Plus declarative preconditions (artifact path + dotted field + required value) classifying FRESH / ALREADY_DONE / PARTIAL_DRIFT / UNEXPECTED_DRIFT before mutating. A field, not new kinds.

### 1.3 Fire & Run Records — two weights, typed outcomes (R2)

Trigger history vs run state are separate concerns. Two record weights:

- **Full run** (dir + journal): any workflow with ≥2 nodes, any LLM stage, anything resumable.
- **Ledger-only fire** (row in runs.db + one events.jsonl line per trigger): single-action fires. Carries `ScheduleRun`'s honest semantics (`launched≠succeeded`, trace, per-trigger caps of 100 — the existing `schedule_history.py` shape) without the directory tax. ~30d TTL/GC.

**The materiality predicate is the classification criterion (R2):** did the run mutate durable state? Derived from the tool-call journal — did any call touch the world (send/post/create/write) vs merely produce text (tryfriday's action|response|error derivation; no new instrumentation beyond the journal). No-op runs collapse to ledger-only and auto-archive out of the default inbox view. Productive rows carry a diff-style "written/learned" section (artifacts/memories/proposals created) + extracted external artifact permalinks, so "what did my machine do while I slept" rows deep-link to the thing produced. An emdash-style **convert-to-full** affordance promotes a ledger row to a full run/work container retroactively.

**Every suppressed or degenerate fire becomes a ledger row with a typed outcome + one-line reason** — silent drops are banned:

| Outcome | Meaning |
|---|---|
| `skipped_overlap` | overlap:skip claim lock held (a run in flight) |
| `skipped_budget` | cost/action cap breached pre-claim (R5) |
| `skipped_gate` | quiet-hours / debounce / cooldown / condition-false |
| `skipped_noop` | ran, mutated nothing durable |
| `skipped_triage` | triage stage said ignore — WITH rationale (R5) |
| `skipped_missed` | user dismissed a missed-fire card (R8) |
| `deferred` | parked/yielded/resource-busy — escalating backoff, ONE row per episode, not per attempt |
| `ran_late` | executed with `scheduled_for` recorded alongside `started_at` |
| `refused` | policy refusal — distinct from failed/skipped, mandatory human-readable reason posted back to the triggering surface (R2) |
| `blocked_injection` | pre-LLM screen match (decision 4a) — names the matched pattern, NEVER auto-retried |

Ledger rows pre-allocate outcome-feedback fields (`acted_on`/`dismissed`) for the LEARNING-FLYWHEEL plan, carry duration + counters + an `incomplete: true` flag where counting was cut short ("at least N"), and a severity→policy mapping (severity maps to run weight, response deadline, decision authority). **Tracked-mutation contract:** background jobs mutate only through event-emitting wrappers so projections never drift.

### 1.4 Design Decisions (from adversarial review + research integration)

1. **Gates are trigger-level config, not graph nodes.** Debounce/rate-cap/max_fires/skip_dates/quiet_hours/budget/cooldown/condition answer "should this fire at all" and belong on the Trigger (a person edits them in one form). Conditions-inside-work, retry-until-done, and approval gates answer "what happens inside the run" and belong in the graph. Degenerate automations (99% of personal use) stay a two-field form. **Fail-open vs fail-closed is classified per gate (R3 am.):** budget/storm-guard checks time-box and fail-open; security fences (capabilities, injection screen, fencing) stay fail-closed.

2. **Blocking `PreToolUse` hooks stay synchronous.** A blocking decision must return inside the agent loop — it cannot be an async WorkflowRun. Kept as `kind: event, spec.blocking: true`, executed inline through the action registry exactly as today, journaled as a zero-step ledger record after the fact. `HookManager`'s declarative allow/deny rules are a **policy layer**, not automation — untouched. **Recon note:** chat-turn hooks fire agent-scoped (`fire_for_ids` over `AgentProfile.triggers`); the substrate preserves agent scoping as an optional `spec.agent_scope` and does not silently introduce a global chat firing path.

3. **Two record weights + typed outcomes** — see §1.3. This replaces rev-1's bare "ledger-only fire" with the full outcome vocabulary; the flagship surface stays honest because nothing is dropped silently.

4. **Untrusted-input fencing at the substrate boundary — hardened (R4).** Webhook text payloads, `InboxItemIngested` content, `ContentMatch`-captured values, and `run_completed.previousResult` cross the trust boundary into unattended LLM execution. Six rules, enforced at trigger-fire time (extending `security.fence_untrusted`, whose only call sites today are inbox_service ×2, knowledge/insights, skills/proposals + one inline fence in after_turn_review — the substrate becomes call site #5, centralized):
   - (a) an **InputGuard-style regex screen** (OWASP 6 groups: override, token smuggling, persona hijack, jailbreak, prompt leaking, indirect injection) runs BEFORE fencing+LLM at the webhook/file/inbox boundary (~0.2ms, zero tokens); blocked payloads → `blocked_injection` ledger row naming the pattern, never auto-retried (no-retry prevents trigger loops brute-forcing the guard);
   - (b) fencing **strips chat-template special/role tokens** so untrusted text can't forge role boundaries — essential with local model providers;
   - (c) the fence tag carries **provenance attributes** (`source_type, source_id, transformation_path` — extending the existing `source=` kwarg); trust promotion is an explicit recorded operation;
   - (d) **payload content never participates in event-pattern/template matching** — only trigger spec patterns match; payload is data;
   - (e) payloads becoming structured workflow input are parsed via **schema-constrained extraction** (jsonschema, `additionalProperties: false`, length caps) at the boundary; cross-run/workflow-minted trigger events are typed bus events gated by a per-source target-template allowlist, never parsed from run prose (the forged-handoff attack);
   - (f) pre-fetch URL classification blocks private-IP/loopback targets from untrusted payloads — already what `net/guard.py:classify_host` does; the substrate routes ALL payload-derived fetches through `net.fetch` (ssrf category).
   - Extension target (cross-ref security roadmap): app/MCP-delivered tool and skill DESCRIPTIONS are also untrusted prompt text.

5. **Feedback-loop storm guard — two lineage fields (R17).** `created_by: workflow` triggers + the event bus make cycles trivial (run writes memory → MemoryUpdate → run…). Layers: (a) the global event rate cap (30 fires/60s — today a module constant in event_triggers.py, promoted to config §Plug-in Map) carries over; (b) a fire carries a transient `provenance_chain` (trigger ids seen this cascade); a chain revisiting the same trigger id is dropped + surfaced as a warning; (c) every trigger-fired run carries persistent **`spawned_by` lineage**, and lifecycle triggers (`run_completed`, MemoryWrite) default to skipping runs their own workflow spawned — catching *indirect* self-improvement loops the direct-cycle check misses. Both fields are needed: provenance_chain is the cascade check, spawned_by the durable lineage; (d) `created_by: workflow|agent` triggers are announced to the user on creation and capped (default 20 active) — visible, not silent; workflow-minted `resume` triggers auto-retire when their target run completes (no orphan watchers).

6. **Dual-writer ownership rule.** Where a settings UI writes triggers (inbox alert-keywords) the rows carry `created_by: system:<feature>` and the Automations page renders them edit-locked with a "managed by Inbox settings" chip — one writer per row, ever.

7. **Creation-time capability allowlist (R3).** Every non-manual trigger carries a `capabilities` block frozen at save time — `{allowed_actions, allowed_write_scopes: {paths, entity_kinds}, network}` — enforced by the engine at execution. **Auto-fired triggers (clock/event/file/webhook/view/web_watch) default to read-only action providers**; write-capable actions require explicit opt-in rendered as a badge on the Automations row. Untrusted payloads may only BIND ARGUMENTS to the pre-declared action set, never introduce actions (frozen action-set invariant); every run stamps a trust-origin chain (trigger kind → workflow → payload source). Sensitive workflow classes (learning/consolidation/**memory-write** — harness internals per decision 14) are launchable only by clock/manual triggers, never webhook/event-origin. Violations fail fast as a typed ledger record. Mechanics (R3 am.5): at run construction the GLOBAL tool/MCP config is *filtered down* to the trigger's declared list (missing servers degrade with a logged warning — availability and policy are separate concerns); subagents spawned inside trigger-fired runs default to a read-only research class gated by tool-name pattern. Enforcement plumbing: provider-registration invariant (every action provider declares its enforcement chokepoint, with a test asserting no execution without a policy check); 4-tier policy vocabulary (silent / first-prompt+allowlist / always-prompt / hard-block) with `bypass_immune` safety checks no allowlist may silence — this layers onto the existing `HookManager` sensitive-path-first ordering and `security.py` deny patterns; PathGuard (realpath + symlink-target matching) for fs-touching providers; runtime per-action enforcement (payload trust × tool sensitivity at each sensitive action, not just fire-time); optional LLM permission classifier as an intermediate mode (forced structured tool call, fail-closed); and a **global manual kill switch** pausing all background execution. The policy layer also owns the outbound mirror: machine-generated disclaimer prepended to externally-posted content from trigger-fired runs.

8. **Missed fires are reviewed, not silently skipped and not blindly replayed (R8).** Explicitly rejected: full auto-catch-up (restart storm) and silent skip (a lie). The middle path is §3.4's review card + the per-trigger `catch_up` flag.

9. **No circuit breakers (R7).** Capability failure ≠ core failure. Unhealthy-provider fires are PARKED with a simple per-target cooldown + `suppressed_total/executed_total` counters + `retry_after_ms` + a `deferred` ledger row — explicitly NOT a 3-state breaker (clawx deleted theirs because a 10-min lockout silently dropped legitimate work). Pause only affected triggers. After N similar failures, quarantine the trigger's runs with captured evidence + explicit replay (dead-letter richer than bare autopause).

10. **Hook doctrine — IO-only lifecycle budget (R17).** Lifecycle handlers (event subscribers that are NOT triggers) get an IO-only sub-second budget; anything needing an LLM call or multi-step logic MUST spawn a WorkflowRun through the normal fire path. This keeps the bus fast and makes it impossible for a slow handler to block dispatch. Scale evidence (Memoh's 38 hook points): a fine-grained event vocabulary is only usable *because* handlers are cheap — the vocabulary can grow (pre/post model call, memory-formation-completed, tool-approval requested/resolved, subagent start/stop) without freezing the agent loop. **Recon caution carried over:** today every `ScriptHookStore._fire` rewrites the whole store to persist run stats — the unified store batches stat writes so a high-frequency PreToolUse hook doesn't imply a JSON rewrite per tool call.

11. **Unattended permission posture = a named `headless` profile (R16).** Trigger-fired runs convert every permission-ask to deny (tool safety checks stay live; the run never parks on a prompt), with denied decisions recorded alongside a suggested allowlist rule for user review. Packaged as ONE named profile object (distinct from interactive defaults, resolved by construction for every trigger-fired run) so the user inspects the entire unattended permission surface in one place — belt-and-suspenders with the capability allowlist (decision 7). Per-app grants cover external clients hitting the webhook kind. This formalizes what the cron path does today (`ToolApprovalPolicy.AUTO_APPROVE`/`HOOK_BASED`, never interactive — gateway `_cron_callback`).

12. **`{{secret:KEY}}` server-side secret templating (R14).** Trigger specs, inline action params, and workflow-def params may reference `{{secret:KEY}}`; resolution happens server-side at execution time only, backed by the existing credential store (`config/loader.py:save_credential` → `.env`, 0600). API/UI expose key NAMES only (presence flags, never values); every journal/ledger/transcript record stores the template string, never the resolved value (redact-before-journal sink, composing with `security.redact()`). Webhook bearer tokens live here (SHA-256-hashed at rest), with scoped tokens (owner/collaborator/viewer) for webhook/manual auth rather than one shared secret.

13. **Outbound delivery contract (R18).** The `delivery` field stops being an unspecified enum. On run completion the substrate emits `automation.run.succeeded|failed` events carrying: a **stable event-id preserved across retries** (the idempotency key — channel consumers dedupe re-delivered notifications); an event-type header; a **statusUrl deep link** into the exact runs-inbox row / run journal (fixing the notification→journal dead end); and **destination-aware formatting** — rich block for inbox/notify, flattened text for `channel:slack` (directly relevant to the existing Slack transport). Delivery routes through the existing gate: `DashboardState.notify` → `notification_allowed()` (providers/entity_routes.py) — the substrate does not build a second notification path. Redaction (`redact_exfiltration_urls` + `redact_credentials`) before any surface, as heartbeat delivery does today.

14. **Memory vs Knowledge boundary (user directive).** KNOWLEDGE = the user's personal items (documents, files, photos, notes; future knowledge providers: Google Drive, Google Photos, …). MEMORY = the harness's own internal mechanics (facts/facets/episodic/procedural/lessons). The substrate touches both and must never conflate them: `web_watch`/`file` digests write to the **knowledge store**; `pulse` watches **memory** deltas; MemoryWrite-class triggers and memory-writing workflow classes are gated per decision 7; learning-outcome feedback fields on ledger rows feed the LEARNING-FLYWHEEL plan (memory side), not knowledge.

---

## 2. Disposition Table

| Surface | Verdict | Detail |
|---|---|---|
| `schedule.py` machinery | **ABSORBED** (rename, not rewrite) | Becomes TriggerService clock kind. crons.json migrates row-for-row. **What is actually kept verbatim (per recon): the single re-armed asyncio timer task (≤30s poll) + croniter minute-match dueness + same-minute refire guard, deterministic per-id jitter, tz + `skip_dates`, mtime `_sync`, fcntl `.crons.lock`, per-job timeout + the reaper (SIGKILL escalation, PID-recycle checks), `_merge_job_result` runtime-field merge-back, and the canonical `action {provider, config}` execution model** (legacy fields are already projections — no exec-model migration needed). `schedule_script.py` sandbox + Skip/Done/Report contract kept as the `run-script` provider. §3.1's crash discipline is layered ON this mechanism |
| `schedule_history.py` ScheduleRun | **ABSORBED** | Its record shape (honest `launched≠succeeded`, dry-run replay, JSONL caps 100/job + index) becomes the ledger-only fire record, extended with the §1.3 typed-outcome vocabulary; full runs use the v2 run layout. `last_run_status()` semantics (UI badge reads history, not the volatile job field) carry over to health rollups |
| `hooks.py` ScriptHooks | **ABSORBED** | → `Trigger{kind:event, source:agent}`. Blocking PreToolUse = the synchronous special case. hooks.json migrated; matcher → unified grammar; agent-scoping preserved (decision 2). `__hook_depth` (invoke-agent refuses at ≥3) folds into the `__wf_depth` cap. **Only 7 of 15 events fire today — wiring the missing 8 fire sites is part of §7 step 1**, following the existing convention (constant + `HOOK_EVENTS` + `LIFECYCLE_EVENT_CATALOG` row + fire site, co-located so catalog and payload can't drift) |
| `hooks.py` HookManager (declarative rules) | **KEPT** (policy layer) | Not an automation. Stays in config.json; gains the decision-7 enforcement vocabulary on top |
| `event_triggers.py` | **ABSORBED** | Its own docstring asks for this. max_fires/debounce/rate-cap → trigger gates. Fixes the facade unevenness (event kind gains toggle/update/run/test/history) and the **verified sync-CLI silent-skip** (a fire with no running loop records fire_count but skips the action — fires now spool per §3.3). Its only emitter today is memory writes (`vector_memory._log_event`); the bus adds the missing sources |
| `autonudge.py` | **ABSORBED as `kind:idle`** — LAST | An autonudge = `Trigger{kind:idle, session:conversation}` + a run-prompt def. **Blocked on LOOPS-EVOLUTION Phase 4** (the loop engine rides autonudge as its tick engine — `loop-<id>` worker sessions armed via `svc.add`). `kind:idle` ships for USER automations early; the loop-ticker use waits. Preserved semantics: reactive re-arm, delivered-only cycle counting, mid-turn drop (= `overlap: skip`), stop-sentinel, error_count deactivation |
| `heartbeat.py` tasks | **ABSORBED**; HEARTBEAT.md kept as sugar | Each HEARTBEAT.md line = a clock trigger + invoke-agent. The 4 tick-modulo maintenance sub-tasks (FTS rebuild, history/SEL prune, skill-curator aging, consolidator check — today locked to a hard-coded 60s interval) become visible, pausable `created_by: system` triggers with real cron cadences (transparency win; the modulo-coupling gotcha dies). HEARTBEAT.md stays as an import surface — a `kind:file` watcher syncs lines → triggers (one-way import; two-way sync deferred). `HEARTBEAT_KEEP` retry semantics preserved via the `deferred` outcome. Optionally the maintenance set collapses into ONE health-scored doctor trigger (§4.1) |
| Commitments delivery | **ABSORBED** | Commitments live in `memory_service.py` (a MemoryKind, not a file — recon); each due commitment becomes a one-shot `clock/at` trigger with `delete_after_run` (per §1.2), replacing the per-tick `due_commitments_all` scan. Guardrails (opt-in config, confidence ≥0.8, ≤3/day/agent) untouched — they are memory-subsystem policy, not substrate policy |
| Inbox poll loop | **KEPT as a service** | Provider polling is plumbing, not user automation. It gains ONE new duty: emit `InboxItemIngested` onto the event bus. The 6h maintenance sub-loop → a system clock trigger |
| Inbox alerts (`evaluate_alert`) | **ABSORBED** | → `Trigger{kind:event, ContentMatch on InboxItemIngested} → notify`. Settings UI writes edit-locked system triggers. Kills the third matcher implementation. Preserved gotcha: evaluation happens at ingestion; editing keywords doesn't re-evaluate stored items (documented, unchanged). "AI digest every morning" ships as a template |
| `fs_watch.py` | **KEPT + gains a consumer** | Stays the SSE refresh engine (3s poll, mtime+size signature); additionally publishes `FileChanged`. **Scope guard:** `kind:file` triggers register EXPLICIT watch roots with a path-count cap and a warning on broad globs — the poller must not become a battery drain. Watch-root registration is part of trigger validation |
| `after_turn_review.py` | **KEPT as code**, surfaced as a read-only row | The learning pipeline is hot-path and cheap; per-turn run records would be journal spam. Appears on the Automations page as an informational row (on/off wired to `config.learning`) marked "runs outside the engine" — data model records `execution: external`, so the substrate's invariant stays honest. (This is MEMORY-side machinery — LEARNING-FLYWHEEL owns its evolution) |
| `suggestions.py`, `engagement_signals.py` | **KEPT lazy / KEPT** | Read-time computation; the counter-example stays the counter-example |
| `/api/triggers` facade + `schedule_trigger.py` | **KEPT — becomes the single API** | The facade already exists (namespaced `kind:<raw>` ids over 3 stores, routes for toggle/run/test/to-chat/ack). Unification re-points it at triggers.json instead of three stores; the id namespace becomes the migration map. `schedule_trigger` (CLI + MCP) already fires via HTTP `/run` with `X-Internal-Secret` — unchanged. **MCP-process gotcha carried over:** MCP tools mutate the store from a separate process; mtime `_sync` within the ≤30s poll remains the propagation contract |
| App crons (`apps/app_crons.py`) | **ABSORBED** | Manifest-declared jobs reconcile into triggers `created_by: app:<name>:<cron>` at startup exactly as today (pruned/converged, gated on `can_use_cron`, force-`silent` because the pseudo-user can't receive DM — all preserved) |

---

## 3. TriggerService (one scheduler)

One asyncio loop — **the existing single re-armed `_arm_timer` task generalized** (there is no heap to extend; recon confirmed the mechanism is one task sleeping `min(next-due-delay, 30s)`):

- The task computes the earliest `next_fire_at` across all clock/idle triggers and sleeps until it (capped at 30s for external-edit pickup via mtime `_sync`), coalescing same-second firings so N triggers replacing one 60s heartbeat don't wake the laptop N times.
- **Event bus subscription** for event/file/run_completed kinds; sync-context fires spool to `~/.personalclaw/trigger-spool.jsonl`, drained on next tick under the §3.3 cursor rule.
- Fire path (order matters): **injection screen (4a) → gates (debounce/quiet/cooldown/condition) → budget check pre-claim (R5, fail-closed) → overlap claim lock (R2) → yield/resource-slot check (R9) → fence payload → capability filter (R3) → resolve def / resume target → create run (full or ledger-only) → engine executes under the `headless` profile → outcome classification (§1.3) → delivery contract (decision 13) → health rollup + failure policy.**

Unattended LLM turns all route through `SubagentManager.spawn` with the `__wf_depth` cap and `session` binding (`pinned:cron:{id}` parity for stateful crons / `conversation:` for in-chat nudge rendering / fresh default — preserving today's `cron:{id}` / `cron:{id}:{uuid8}` conventions and the `_STATELESS_PREFIXES` reset behavior). The reaper (60s sweep, SIGKILL escalation, jitter allowance) is kept as defense-in-depth over ALL trigger-fired runs, not just crons.

### 3.1 Crash-safe scheduling discipline (AUTO-R1)

Layered onto the re-armed-task mechanism:

- **Persist-before-execute:** `next_fire_at` is computed on fire via the recurrence engine and PERSISTED to the trigger row *before* executing (never re-parse-on-poll), so a crash mid-fire cannot double-fire. This replaces the croniter-minute-match due check as the primary dueness source; the match logic moves into the recurrence engine.
- **Exactly-one-upcoming invariant:** exactly ONE persisted upcoming fire per enabled trigger, recovered/re-armed on gateway boot; interrupted queued/starting runs are re-queued or marked `failed('gateway restarted')`.
- **Single-flight per trigger** as an explicit invariant (OpenJARVIS); the overlap claim lock is its enforcement point.
- **Recompute-from-NOW after completion, anchored to `created_at`:** next fire is computed from completion time (never the missed slot — prevents re-fire storms when a run overruns its interval), but recurrence anchors to the trigger's `created_at` grid so recomputes don't re-phase to "now".
- **Wall-clock math in the trigger's IANA timezone**, convert to UTC last (9am Monday survives DST).
- **Minimum-interval floor** in trigger validation (15 min default for LLM-invoking clock triggers, overridable); a minute-level recurrence floor for everything (Khoj convergence).
- **Boot stagger:** overdue fires pushed +60s, staggered deterministically (reusing the per-id BLAKE2b jitter pattern) so a restart doesn't fire every automation at once.
- **Lock self-expiry:** the fire claim lock carries a `max_duration` self-expiry (Khoj's ProcessLock) so a crashed holder can never permanently wedge a trigger's next fire — complements the existing reaper.
- **Shipped-scheduler details (emdash):** 32-bit timer-ceiling clamping with automatic re-tick for far-future fires; re-fetch-and-revalidate trigger state on fire (bail if disabled/rescheduled mid-wait); refuse-enable-overdue-one-shot (force reschedule instead of surprise fire).
- **Deterministic ids** for feature-minted triggers (`system:<feature>:<slug>`) so re-registration at startup is idempotent — matching how app crons reconcile today.

### 3.2 Dispatch architecture: inbox + wakeup (AUTO-R16)

The scheduler never executes directly. A fired trigger enqueues a typed payload onto the target session's inbox queue + a wakeup signal; a **WakeupDispatcher** claims and drives runs. Two wakeup kinds with different drop semantics:

- **wake** — drain inbox; skipped entirely if the session is already running (the natural implementation of `overlap: skip`, and exactly the semantics autonudge already has for mid-turn nudges);
- **resume** — a gate-answer/HITL result for a parked run; **must re-queue until the parked lock releases** — overlap guards must never eat gate answers intended for parked runs (this is what makes R11 resume-targets and R13 approvals safe).

Crash-safety falls out: the payload survives an executor crash in the inbox. One code path serves all trigger kinds. All bus/queue key formats are centralized in one auditable module (the `MessageBusKeys` pattern) — extending the session-key conventions table (`cron:{id}`, `cron-{id}` dashboard pair, `_bg`, `loop-<id>`, …) rather than inventing a parallel one.

### 3.3 Event-bus delivery contract (AUTO-R6)

The bus is migration step 1 — it gets reliability semantics up front:

- Every trigger-dispatch record carries per-target `{status: pending|delivered|given-up, attempts}`; handler outcomes are typed `delivered|transient|permanent` (unexpected throw = transient, "never drop"); bounded give-up is loud-logged to the Run Ledger.
- **Consumer cursor rule:** the seq cursor advances ONLY on consumed events. Transient failures (prerequisite absent — provider down, key missing) HOLD the drain with a per-seq bounded retry counter (~5); permanent failures (payload bad) advance+log. The key-absent-vs-key-bad distinction prevents both event loss and poison-pill stalls. The `trigger-spool.jsonl` drain (the fix for event_triggers' verified sync-context skip) adopts this rule; **monotonic cursor per (trigger, stream)** so a repeatedly-firing trigger never reprocesses history.
- **Peek-then-deliver-then-ack** queue semantics (not atomic read-and-mark) — at-least-once delivery; events are acked/deleted only after an error-free handling cycle (AionUI's documented crash-loss bug is the counter-case).
- **SHA-256 payload-hash dedup window** (~5 min) at ingestion kills webhook/fs double-fires; stable deterministic event_ids make re-delivery idempotent.
- **Per-event-family coalescing windows** (50-250ms) and per-source rate floors that survive manual/force fires (force bypasses min-interval; `max_requests_per_sec`-class floors still apply).
- **Completeness rule:** every entity CRUD in the gateway emits a typed event on the one bus, so triggers subscribe to entity lifecycle without per-surface glue.
- The hook-recursion storm case (a run's own lifecycle events re-matching its trigger) is an explicit cycle-guard TEST, backed by decision 5's spawned_by skip.
- Anti-flood logging: held/backlog counts logged only on change.

### 3.4 Missed fires: review, don't lie and don't storm (AUTO-R8)

On gateway startup, enumerate fires elapsed while down (cap ~480): create MissedRun ledger records for the newest ~20 per trigger, presented as a **review card** on the Automations page (run-now → recorded `ran_late`; dismiss → `skipped_missed`); older collapse into ONE summarizing entry per trigger; `next_fire_at` rolls forward so re-opens don't re-enumerate. The per-trigger `catch_up: bool` (launchd RunAtLoad semantics) fires once automatically at boot/wake when the last slot was missed — storm-guarded to at most ONE catch-up run per trigger, **staggered across triggers at startup, tagged `trigger: 'catchup'`** (a distinct ledger origin), and backstopped by the per-trigger `max_runs_per_hour` sliding window (which manual fires bypass). Local-first means lid-closed = the loop stopped; this is the missing half of the runs-inbox story.

### 3.5 Foreground yield + resource slots (AUTO-R9)

PClaw shares one machine between the interactive user and local models (whisper, embeddings, ollama):

- Per-trigger `yield_to_user` — background fires wait for a quiet window (no in-flight interactive HTTP ~1.5s, no browser heartbeat ~45s, no live model stream; passive/polling endpoints excluded); a running yielded fire is cancelled+deferred with escalating backoff (~15min) when the user becomes active.
- **Named resource slots** — triggers/runs declare needs (`gpu`, `local-llm`); the substrate serializes conflicting runs per slot and refuses over-capacity starts with a typed `RESOURCE_BUSY` + holder identity (a `deferred` ledger row).
- Optional `skip_if_active` guard on mutating triggers using cheap liveness heuristics (dirty worktree, lockfiles, recent mtime) at fire time, plus an `acting_on` resource claim so two trigger-fired runs never mutate the same target concurrently.

### 3.6 Budgets + triage (AUTO-R5)

- Budget gates checked PRE-claim against a persistent per-window budget table for LLM-invoking fires; breaches → `skipped_budget` rows + inbox alert + autopause on sustained breach; budget-check errors **fail-closed**. Metered by LLM-cost weight, not run count — deterministic/transform-only runs are cheap/exempt.
- `model_tier: background|standard` per trigger, resolved ONCE via config so all 24/7 loops share one cheap-model knob — implemented over the existing use-case machinery: background fires resolve through `one_shot_completion(use_case="background")` / the `reasoning` axis (recon: chat/code_tools resolution returns the NativeAgentRuntime — background callers must use the reasoning axis), bound in `active_models.json`.
- **Fire→spawn triage stage** — recommended DEFAULT for noisy event/webhook/inbox triggers (three-source convergence: CORE's shouldAct/shouldSurface/shouldIgnore, LocalAGI's ClassifierFilter, the R5 lever): a small local model evaluates per-trigger NL rules ("from Gmail only extract action items; skip newsletters") and verdicts `{drop | notify | spawn-ledger-only | spawn-full}`, decisions cached on trigger fingerprint. Doubles as a storm guard. `skipped_triage` rows carry the rationale.
- Automations rows show `cost_estimate = last-run cost × fires_in_window(30d)`; the recurrence engine also powers a next-fires calendar preview. Goal-style triggered runs carry max-turns + a stop condition; a **no-improvement-halt** stop-condition (score unchanged across N firings suspends the trigger and files a needs-input item) covers self-improvement loops (R12 am.).
- Poka-yoke review of action-provider config schemas (enums over free text, absolute paths, no silent coercion) — applied to the `settingsSchema` in each `<provider>-action` extension manifest.

### 3.7 Health + typed exits + parking (AUTO-R7)

- Typed run-exit taxonomy: `ok / partial (resumable, cursor persisted) / auth_unavailable / transport_unavailable / failed` — with per-outcome `failure_policy` (auto-refire on partial; alert-and-pause on auth_unavailable; **only true failures count toward autopause_after: 5** — generalizing the existing gateway `_maybe_autopause`, which today covers only the cron action path).
- `stall_after_s` in run policy: a run emitting no ledger events for N seconds renders "stalled" vs "running".
- Trigger firing gates on capability-level readiness of the target provider/runtime; unhealthy targets → parking per decision 9.
- Health rollups (`last_success_at/last_failure_at/health_status/last_error_summary`) updated on every fire — the Automations list renders status dots O(triggers), not O(runs).

---

## 4. Chat Tools

One namespace replacing `schedule_add/…` + ad-hoc trigger creation:

| Tool | Description |
|---|---|
| `automation_create` | `(name, trigger{kind, spec}, workflow{ref\|inline_action\|resume}, gates?, capabilities?, session?, delivery?)`. NL-friendly: `when: "every weekday at 9"` routes through `nl_to_cron`; `when: "when a file in ~/notes changes"` → file kind. Write-capable `capabilities` echo back a confirmation |
| `automation_list` | `(kind?, state?)` — includes health rollups |
| `automation_update` | `(id, patch)` |
| `automation_pause` / `automation_resume` | `(id)` |
| `automation_run` | `(id, dry_run?)` — manual fire / observe-mode replay (bypasses min-interval + max_runs_per_hour, never rate floors) |
| `automation_history` | `(id, n?)` — run/fire rows incl. typed outcomes + step statuses (agents self-debug their automations) |
| `automation_doctor` | read-only check set (§4.1) |
| `automation_delete` | `(id, confirm: true)` |

Agent-created triggers are tagged `created_by: agent`, announced to the user on creation, capped (decision 5d), and visible on the Automations page. MCP `schedule_*` tools kept as thin aliases for one release, then removed — after the grace release the policy layer **denies** them (§4.1). CLI: `personalclaw automation` with a `cron` alias. **Recon carry-over:** MCP tools run in a separate process writing the shared store; propagation stays mtime-`_sync`-within-30s; immediate firing goes through HTTP `/run` (the `schedule_trigger` pattern).

### 4.1 Substrate integrity tooling (AUTO-R15)

- **OS-scheduler gate + on-ramp:** the policy layer force-prompts word-bounded OS-scheduler commands in Bash (`crontab`, `launchctl`, `schtasks`, `systemd-run`) — added to `denied_command_patterns()` machinery as prompt-tier, not hard-deny; a startup crontab/launchd scan surfaces PClaw-ish entries as a one-click migrate-to-Trigger banner. The invariant "every background behavior = a Trigger" only holds if agents can't route around it into invisible OS schedules.
- **Automation doctor:** read-only checks (unknown kinds, orphaned workflow refs, cycle potential, broad file-watch globs, stale next_fire) each PASS/WARN/FAIL with suggested fix — optionally run as ONE health-scored maintenance trigger that builds a dependency-ordered remediation plan under target-score/cost-cap semantics with adaptive idle cadence (healthy → sleep longer), replacing N independent maintenance crons. Health probes never raise to 500 (exceptions → ok=false, secrets masked).
- **Dry-fire smoke gate** button on the Automations page (`--check` style): boot the workflow, verify health + event stream, exit without side effects — catching dead triggers before the nightly batch.
- **Lenient ingestion + audit:** never-throw structural parse (§1); per-trigger append-only JSONL change audit (`{ts, who, diff}`) surfaced as "who changed this automation". First paint of the Automations page is one bulk endpoint with per-row error isolation.

---

## 5. FE — One Automations Page

Replaces `pages/triggers/` + `pages/schedule/`:

### 5.1 Triggers list

Grouped by kind, state chips, `created_by` badges (system rows edit-locked), next-fire for clocks, **health status dot from rollups** (R7), **write-capability badge** (R3), **cost estimate + next-fires preview** (R5). Create flow = two-field form (When: kind picker + NL box → parsed spec confirmed; Do: action editor or workflow picker); "Advanced" folds out gates/capabilities/session/delivery. Clock UI: Recurring/Once tabs + presets + raw-cron escape hatch + live next-fire preview (R12). Row affordances: **Run Now** and **Duplicate** (copy trigger+workflow, new id, disabled by default) (R18), dry-fire smoke gate (R15). The **missed-fire review card** (§3.4) renders at top after a boot with missed slots. A **global kill switch** (decision 7) pauses all background execution.

### 5.2 Runs inbox

Reverse-chron feed of runs/fires across all triggers with the §1.3 **typed outcome vocabulary** — every skip/defer/refusal visible with its one-line reason; no-op rows auto-archived out of the default view; All/Failed/Needs-input filters; needs-input pinned to top. Productive rows carry the "written/learned" diff section + artifact permalinks. **Strictly read-only — viewing never consumes/acks; a separate explicit action does** (lifeglance tray rule). Click-through to the run journal via the same statusUrl the delivery contract emits.

**Needs-input rows are durable approval objects (AUTO-R13):** `{id, requested_action, payload_preview, policy_rule_matched, request_context, ttl_seconds, auto_reject_on_expiry, reviewed_by/at/note, status}` — live wait = asyncio.Event backed by a durable row; a background sweeper expires by TTL; **pending waiters are RE-ARMED from disk on gateway restart** (PClaw restarts constantly — without re-arm every restart orphans pending approvals). Approval waits inside trigger-fired (unattended) runs use a short timeout (~30s → fail fast into the needs-input inbox as a resumable item, which a resume-target trigger can later resolve) vs long attended timeouts. Needs-input queue depth surfaces on the Automations page.

### 5.3 Templates

Bundled defs with a trigger pre-attached: Morning inbox digest, Watch-folder summarize (→ knowledge store), Weekly memory review (clock-only launchable, per decision 7), Commitment reminder, Idle-nudge, **Morning web digest** (web_watch sources → new-items foreach → rule-grammar filter → one digest) (R10). One-click install → editable trigger.

---

## 6. What We Deliberately Do NOT Build

- No visual graph builder as primary authoring (OpenAI Agent-Builder anti-lesson: it lost to code/chat). The graph is a readable spec the chat agent writes and the user reviews.
- No standalone "agent app" surface — automations live inside chat (widget, notifications) + one page.
- No RBAC/sharing. `created_by` is provenance, not permissions; webhook scoped tokens are auth, not roles.
- No matcher DSL beyond glob+regex+keywords.
- **No 3-state circuit breakers** — parking + typed exits only (decision 9).
- **No full auto-catch-up and no silent skip** for missed fires — review card + opt-in per-trigger `catch_up` (decision 8).
- **No raw-screen-frame pulse** — `pulse` watches memory/context deltas; screen capture is the separate opt-in `observe` kind, app-delivered, Phase 2.
- No second notification path — delivery routes through `DashboardState.notify` + `notification_allowed()`.

---

## 7. Migration Order (each step ships independently)

1. **Event bus + absorb `event_triggers.py`** with the §3.3 reliability contract from day one + new sources (fs_watch `FileChanged`, inbox `InboxItemIngested`, entity-CRUD events) + **fire sites for the 8 dormant `HOOK_EVENTS`** + inbox alerts re-pointed. Lowest-risk, highest-new-capability; the bus is the proof-of-pattern. Fencing hardening (decision 4: screen, strip, provenance, typed extraction) lands here because the bus is the trust boundary.
2. **Trigger store unification**: `triggers.json` + row-for-row cron migration (old file read-only one release; `personalclaw automation verify-migration` diff command). **The existing `/api/triggers` facade becomes the single API by re-pointing its three backends at one store — its `kind:<raw>` id namespace is the migration map.** Lenient parse + change audit (R15) land with the store.
3. **Crash-safe scheduler + dispatch**: §3.1 discipline (persist-before-execute, boot sweep, stagger, single-flight) layered onto the re-armed timer task; §3.2 inbox+wakeup dispatcher; `headless` unattended profile; missed-fire review + `catch_up` (§3.4).
4. **Run-record integration**: two-weight records with the full §1.3 outcome vocabulary + materiality classification; typed exits + health rollups + parking (§3.7); generalized autopause; budget gates + background model tier (§3.6, minus triage).
5. **Lifecycle hooks** → kind:event (blocking special case + agent scoping preserved); hooks.json migrated; batched stat writes. **Heartbeat** → system triggers + HEARTBEAT.md one-way import; delete `HeartbeatService`. Commitments → one-shot `at` triggers with `delete_after_run`.
6. **Capability allowlists + secrets**: decision 7 enforcement chain (frozen action sets, PathGuard, kill switch, provider chokepoint tests) + `{{secret:KEY}}` templating + scoped webhook tokens (decisions 11/12).
7. **FE Automations page** (list + runs inbox + approval objects + missed-fire card + templates); delivery contract (decision 13); retire `pages/schedule/` + `pages/triggers/`.
8. **New kinds wave 1**: `view`, `web_watch`, `run_completed`, composite shape, condition gates, `vcs`/sequence variants; fire→spawn triage stage default-on for noisy sources; foreground yield + resource slots (§3.5). Doctor + OS-scheduler gate + dry-fire (§4.1).
9. **Autonudge → kind:idle** — only after LOOPS-EVOLUTION Phase 4; then delete `autonudge.py`. Retire `schedule_*` aliases (deny via policy layer); update `snapshot.py`/`portability.py` to carry `triggers.json` + the ledger (**recon: today snapshot covers crons.json/hooks.json but NOT event_triggers.json/autonudge.json — the unified store closes that gap**).
10. **Phase 2 (separate go/no-go)**: `pulse` + standing delegations (R19); `observe` app (R20). Both propose-by-default; auto-execute must be earned per matter class.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Loops coupling (autonudge is the loop tick engine) | kind:idle ships early for user automations; loop-ticker absorption strictly after LOOPS-EVOLUTION Phase 4; two idle engines coexist in the interim |
| Run-record flood | Two-weight records + materiality predicate + no-op auto-archive + ledger TTL (~30d) + tiered retention from the v2 plan; `view` kind never fires unviewed |
| Double-fire / restart storm / DST drift | §3.1 discipline: persist-before-execute, exactly-one-upcoming, anchored recurrence, tz-last conversion, boot stagger, single-flight, lock self-expiry |
| Timer wake-storm on laptop | Single re-armed next-deadline task (the existing mechanism), same-second coalescing, deterministic jitter kept |
| Migration trust (crons are the most-loved automations) | Row-for-row migration + read-only legacy file + verify-migration diff command; execution model already canonical `action {provider, config}` so only the store moves |
| Webhook/inbox payloads reaching unattended LLMs | Decision 4's six-rule fencing (screen → strip → fence-with-provenance → schema-extract → no-payload-matching → ssrf gate), substrate-enforced, fail-closed |
| Injected run DOING harm despite fencing | Decision 7 capability allowlists: read-only default for auto-fired, frozen action sets, write scopes, trust-origin chain, kill switch — text fencing alone does not bound actions |
| Trigger cycles (workflow-minted triggers, flywheel loops) | provenance_chain (direct) + spawned_by lineage skip (indirect) + global rate cap + agent-trigger cap & announcement + auto-retiring resume watchers + cycle-guard test |
| Runaway LLM cost | Pre-claim budget gates (fail-closed), background model tier, triage stage, cost projection on rows, no-improvement-halt |
| Local-model contention degrading chat | yield_to_user + named resource slots + RESOURCE_BUSY typed defers (§3.5) |
| Lost/duplicated bus events | §3.3 contract: typed handler outcomes, cursor rule, payload-hash dedup, peek-deliver-ack, at-least-once |
| Orphaned approvals on restart | Durable approval objects re-armed from disk (§5.2) |
| Secrets leaking into journals/store | `{{secret:KEY}}` resolution at execution only + redact-before-journal + hashed webhook tokens (decision 12) |
| Substrate bypass via OS schedulers | Policy-layer prompt on crontab/launchctl/systemd-run + startup scan + migrate banner (§4.1) |

---

## Provider & Config Plug-in Map

Where each new piece plugs into the pluggable-provider architecture (recon: providers.md) — nothing here invents a parallel extension path:

- **Action providers stay THE execution seam.** Every trigger kind dispatches through `action_providers/registry.py` exactly as schedule/hooks/event-triggers do today. Any NEW action provider (e.g. a future `digest` action) follows the existing rule: implement `ActionProvider`, register via `register_action_provider` (core) or ship as an app with `provider: {type: "action", implementation: "provider:create_provider"}` (the `apps/webhook-action` precedent — webhook is already OUT of core), **AND add its name to `ALLOWED_HOOK_PROVIDERS` (validation.py:555)** or trigger create/update rejects it. Settings schema via a `<name>-action` extension manifest.
- **Trigger kinds as extension points:** core kinds (clock/event/idle/file/webhook/view/web_watch/manual/run_completed) are native. Phase-2 `observe` ships as an **app-delivered provider** (capture daemon + extraction workflow in an app; its capture toggle is a `created_by: system` trigger row), consistent with the app platform's manifest/permissions model (`permissions.cron`, `permissions.events`). If a pluggable trigger-source seam is warranted, it enters `PROVIDER_TYPES` + a `_TypeHandler` **together** (recon: `test_manifest_types_match_handlers` guards the #47 bug class — never add one side only).
- **Background LLM resolution** uses the existing use-case machinery: `one_shot_completion(use_case="background")` / the `reasoning` axis via `active_models.json` bindings — never the chat/code_tools axis (which returns the NativeAgentRuntime). The triage stage binds a small local model the same way (a use-case ref like `ollama-models:<small>`); local models arrive via the established `LocalModelProvider` app path.
- **New config = an `AutomationConfig` section**, wired through the FOUR points (recon: persistence-security gotcha #1): (a) dataclass fields with `_meta(label, help)` (schema reachability tests enforce), (b) `AppConfig.load()` explicit field-by-field mapping (omission = silently dropped), (c) `to_dict()` (new top-level section must be added), (d) `_EDITABLE_CONFIG` PATCH allowlist + FE for runtime-editable knobs. Fields promoted from today's module constants: event rate cap, dedup windows, min-interval floor, budget defaults, yield thresholds, ledger TTL. (Heartbeat's non-configurable 60s dies with `HeartbeatService`.)
- **Egress:** `web_watch` and payload-derived fetches go through `net.fetch` with an `EgressPolicy` profile via `egress_policy_for()`; the headless escalation tier pre-flights `guard.evaluate` (web/render.py pattern). Never hand-rolled aiohttp.
- **Fencing/security:** `security.fence_untrusted` extended (provenance attrs) and re-exported unchanged via `sdk.security` for apps; the injection screen and PathGuard live beside `security.py`'s existing deny machinery; SEL (`sel.py`) receives audit events for capability violations, secret resolutions, and OS-scheduler prompts, as it does for egress/skill installs today.
- **Delivery:** through `DashboardState.notify` → `notification_allowed()` (providers/entity_routes.py) — the entity-settings gate stays THE gate; channel formatting uses the registered `ChannelTransportProvider`s (webui, slack app).
- **Knowledge vs memory routing:** workflows writing user items call the knowledge pipeline (`personalclaw.knowledge.*`); memory-writing workflow classes go through the memory subsystem and are launch-gated per decision 7. Future knowledge providers (Drive, Photos) slot into the existing `knowledge_providers` seam without touching the substrate.

---

## Implementation Effort

- **9 sessions core + Phase 2 track** (after v2 Slices 0-2) — up from 5 in rev 1; the added scope is R1-R18.
- Session 1-2: event bus + reliability contract + event-trigger absorption + dormant-hook fire sites + fencing hardening (steps 1)
- Session 3: trigger store + facade re-point + migration tooling + lenient parse/audit (step 2)
- Session 4: crash-safe scheduler + wakeup dispatcher + headless profile + missed-fire review (step 3)
- Session 5: run records + outcome vocabulary + health/parking + budgets (step 4)
- Session 6: hooks + heartbeat + commitments conversion (step 5)
- Session 7: capabilities + secrets (step 6)
- Session 8: FE Automations page + approvals + delivery contract + templates (step 7)
- Session 9: new kinds wave 1 + triage + yield/slots + doctor + validation + cleanup (step 8; autonudge deferred to loops timeline, step 9 rides that)
- Phase 2 (`pulse`, `observe`): separately scoped after the substrate proves out — both are `large` and gated on a go/no-go.

## Success Criteria

1. Every cron in a real user store migrates losslessly (verify-migration diff empty) and fires identically — including deterministic jitter, tz, skip_dates, persistent-session semantics.
2. "When a file in ~/notes changes, summarize it into my knowledge base" is creatable in chat in one message (and the summary lands in the knowledge store, not memory).
3. A failing automation autopauses after 5 *true* failures (typed exits — auth/transport outages park instead) and surfaces in the Runs inbox.
4. A hook, an event trigger, and a cron all show run history in the same feed with the same record shape and typed outcomes.
5. The event kind has full API parity (toggle/update/run/test/history) — closing today's facade gap — and the 8 dormant lifecycle events actually fire.
6. An inbox item containing prompt-injection text cannot steer an unattended digest run (fencing verified adversarially) **and cannot cause any action outside the trigger's frozen capability set** (allowlist verified adversarially).
7. Kill the gateway mid-fire and restart: no double-fire, no lost fire, missed slots appear in the review card, pending approvals re-arm, `catch_up` triggers fire exactly once, staggered.
8. Every suppressed fire (overlap/budget/gate/triage/noop) appears as a typed ledger row with a reason — zero silent drops under a 24h storm test.
9. A per-minute noisy webhook stays within budget, triages most fires to `skipped_triage`/ledger-only, and never degrades interactive chat latency (yield + slots verified with a local model loaded).
10. A completed-run notification deep-links (statusUrl) to the exact run journal row; a retried delivery does not double-ping.
11. `{{secret:KEY}}` never appears resolved in triggers.json, journals, ledger, or `automation_history` output.
12. An agent attempting `crontab -e` is prompted and offered the substrate; `automation doctor` flags an orphaned workflow ref and a broad file-watch glob.
