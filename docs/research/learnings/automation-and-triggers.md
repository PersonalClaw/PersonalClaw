# Automation & Triggers

> Part of the PersonalClaw research-learnings library. Source-agnostic; distilled 2026-07-13 from a 95-source competitive-research corpus.

## Principles (the durable truths)

**The scheduler dispatches; it never executes.** The most robust always-on designs converge on a strict split: a trigger firing produces a durable dispatch record (typed payload onto an inbox/queue + a wakeup), and a separate dispatcher/engine picks work off the queue. The scheduler process never invokes execution directly. This yields crash-safety (the queue survives), multi-process readiness, and one code path for every trigger kind. Multiple independent systems converge on this shape.

**Every non-fire is an observable outcome, never silence.** Overlap skips, cooldown skips, budget-cap skips, missed fires, no-op fires, and deferred fires each get a typed, recorded outcome with a reason string. The single most common defect across the corpus is silent skips and silent double-fires; the systems that work make "what my machine did NOT do" as legible as what it did.

**Cheap-deterministic-first, reasoning-last.** Every background loop should be tiered: a free predicate check (SQL count, watermark compare, healthy-gate), then deterministic code fixes, and only then an LLM. Anything deterministic logic can solve never goes to a probabilistic model; where that line is drawn decides whether the automation is reliable and affordable. Cadence is a linear cost multiplier (a 5-minute vs daily schedule is 288× runs/day), so the empty-work path must be near-free.

**Unattended runs are a different policy regime than interactive ones.** Approval timeouts, permission modes, tool allowlists, and model tiers must all resolve differently when no human is present: fail fast instead of parking, convert ask→deny instead of ask→wait, freeze the action set at schedule-activation time, and default to read-only for auto-fired work. Multiple systems converge on a named "headless/unattended" profile distinct from interactive defaults.

**Pull complements push.** For surfaces a human looks at (dashboards, digests-of-record), refresh-on-view beats scheduled synthesis: fresher when it matters, zero cost when unviewed. One measured case deleted a nightly scheduled task that "chewed through tokens" in favor of open-on-demand. Push (clock/event) remains right for work that must happen whether or not anyone looks.

**Manual invocation is just another trigger of the same definition.** One workflow definition, N trigger kinds; every trigger-owning automation needs an on-demand "Run Now" test fire. Never fork "manual variant" vs "scheduled variant". Manual fires should bypass rate limits but not consume run budgets.

**Crash safety comes from state-in-store, not process memory.** Fresh session per fire with all state persisted (DB rows, journal, flag columns) makes an always-on process restart-safe by construction. Anything held only in module dicts is a deliberate choice that must be justified (one system keeps in-flight chains non-persistent precisely because "re-firing on a stale schedule after multi-day downtime is worse than a missed ping").

## Mechanisms (implementation-ready designs)

### 1. Trigger taxonomy
The converged kind set, each with distinct semantics:
- **clock**: cron expression (5/6-field + IANA timezone), fixed interval, or one-shot at-time (auto-delete after success). Enforce a minimum-interval floor (~15 min for LLM-backed work) and clamp intervals (≤365 d).
- **event**: bus/webhook/app events. Discipline: **one trigger = one narrow event** (e.g. "PR updated" = only the synchronize action; label events fire once per label; noisy sources like comments deliberately excluded and routed to webhooks instead). Narrow semantics keep storm guards simple.
- **file**: watched directory/path, poll ~5 s, dot-file and extension filters. Dedupe MUST key on `(path, content_hash)` — path-only keying re-ingests renamed duplicates and never re-ingests edited files (a verified bug class).
- **webhook (inbound)**: generated per-trigger URL + permanent token (`Authorization: ApiKey ...`); optional JSON payload becomes part of the run context **as fenced untrusted content**. Verification is a format enum: `hmac-sha256`, `timestamped-hmac-sha256` (Stripe-style `t=,v1=`, multiple accepted v1 values for secret rotation, tolerance window), `token-equality`. Secret refs resolved per-request, never at create time; **fail closed**: verification configured without a resolvable secret refuses requests.
- **view**: fires when a bound surface (dashboard tile, artifact) is rendered, with `{ttl_secs}` cache semantics — within TTL serve cached, past TTL fire the refresh. The pull-based complement to clock.
- **idle / interactive-gate**: fires only when the user is away (see mechanism 12).
- **chain / run-completed**: automation A completes → B starts, receiving `{chainSource, previousResult}` as fenced payload. Cycle-detect, cap depth (~10), block cross-owner chains.
- **recency/staleness**: fires when `now − last_successful_run > cadence`, with optional seasonal windows and once-per-day-while-overdue throttling. Distinct from cron: it tracks *elapsed since success*, not wall-clock slots — the natural semantics for upkeep automations.
- **pulse/heartbeat**: periodic ambient agent turn (see mechanism 10).
- **manual**: always present on every automation, bypasses rate limits, doesn't consume `max_runs`.
Optional per-trigger **payload_schema** (deliberate JSON-Schema subset: type/required/properties/enum/const/items) validated on every ingress path *before* a run is created; the rejection response echoes the schema so an agent caller self-corrects in one turn.

### 2. Crash-safe firing: persist-next-fire-before-execute
The load-bearing rule: **persist `next_fire_at` BEFORE executing** the run, so a crash mid-fire cannot double-fire. Companion rules that multiple systems converge on:
- Store `next_fire_at` as a timestamp and fire rows where `next_fire_at <= now`; recompute via the cron library after firing. Firing is driven by a stored timestamp, never by re-parsing schedules on every poll.
- **Anchor recurrence to `created_at`**, so recomputes land on the same grid instead of re-phasing to "now". Do wall-clock math in the trigger's IANA zone and convert to UTC last (9am Monday stays 9am across DST).
- **One scheduler loop for all triggers** — never one task per trigger (rescheduling becomes a thundering re-spawn problem). Sleep `clamp(next_due, 1s, 60s)` on an event that a `kick()` sets after edits/unpause; fires are `create_task`'d fire-and-forget so one slow run can't block other schedules. An adaptive variant wakes just before the next boundary so a `* * * * *` schedule isn't ~60 s late.
- For event triggers with persisted counters, **commit `next_run = now` before dispatch** so if the process dies after a counter reset, the persistent scheduler still picks the task up.

### 3. Boot sweeps and missed-fire review
On startup:
- **Zombie sweep**: mark stale `running` runs `aborted("server restarted")`; heal stuck runs before scheduling anything.
- **Overdue stagger**: push all overdue triggers +60 s so a restart doesn't fire every automation at once.
- **Missed-fire review, not blind auto-catch-up** (the mature policy): enumerate elapsed fires with a cap (~480 ≈ 5 days of a 15-min schedule); the newest ~20 per trigger become pending `MissedRun` records for a launch-time review card; everything older collapses into ONE summarizing "skipped" run ("Skipped N earlier missed runs"). Roll `next_fire_at` forward so hot-reloads don't re-enumerate. The user picks per missed run: execute (logged `ran_late`) or dismiss (recorded `skipped`, last-run fields updated so status dots stay honest).
- The simpler converged alternative for lighter systems: collapse N missed fires into **exactly one catch-up run** tagged `trigger: 'catchup'`, staggered ~2 s between automations to prevent flooding after long absence.
- Scan OS-level schedulers (crontab/schtasks) at startup and offer one-click migration into the native, auditable scheduler; simultaneously *deny* agent attempts to create OS schedules via a permission gate ("deny the generic path, fund the native one").

### 4. Typed fire/run outcome vocabulary
Run records need a richer status set than success/failure. Converged vocabulary:
- `queued → running → {succeeded, failed, timed_out, cancelled, lost}` plus `ran_late` and `skipped(reason)`.
- `ran_late` = started > 300 s after `scheduled_for` (late START, not long duration) — requires recording both `scheduled_for` and `started_at` on every triggered run.
- `skipped` carries a reason string: `overlap` ("previous run still active"), `cooldown`, `cost-cap`, `dismissed-miss`, `precondition-unmet`, `no-tasks-due`, `empty-file`.
- `lost` = the authoritative backing state vanished past a grace period (per-runtime liveness checks); a periodic (60 s) sweeper reconciles and prunes (terminal records ~7 d, lost ~24 h). An audit command classifies findings: `stale_queued` (>10 min), `stale_running` (>30 min), `lost`, `delivery_failed`, `missing_cleanup`, `inconsistent_timestamps`.
- Two control-flow exceptions inside run bodies: **Noop** → run recorded as visible `skipped` (never vanished); **Deferred** → run row deleted but the defer escalates (≥40 min after 2 defers) — keeps the runs inbox signal-dense without losing backoff state.
- **Outcome classification by external side effects**: scan the run's paired tool calls for externally visible actions (messages sent, tickets/PRs created), extract result permalinks, and classify `action` (touched the world) vs `response` (text only) vs `error`. This is the triage-relevant weight axis, derivable purely from the tool-call journal.
- **Two-weight records**: full run dirs for real work; ledger-only rows (fired_at, run_id, launch_status, error, payload_snapshot) for skips/noops/view-refreshes, with TTL/GC (~30 d).

### 5. Overlap policies
- Default: **single-flight per trigger** via an in-memory `{trigger_id → run_id}` map under a lock; a second fire records a `skipped` run with reason "previous run still active" — observable, not silent. A system that shipped *without* any overlap guard is a proven double-fire hazard.
- Make the policy explicit and per-trigger: `overlap: skip | queue | parallel`, plus a fire-time lock.
- **Two wakeup kinds with different drop semantics**: `wake` (idempotent nudge; skipped entirely if the target is already running) vs `resume` (carries a human-gate answer for a parked run; MUST re-queue until the parked run releases its lock). Without this split, gate answers get lost to the overlap guard.
- **Cancel-previous-on-new-input** for conversation-scoped work: a new message in the same conversation auto-cancels the in-flight stale job.
- A registry enforcing one run per session (double submit → 409) plus a DB-backed process lock (with max_duration timeout) for multi-worker safety.

### 6. Storm guards & recursion prevention
A compositional kit — adopt several, they're cheap:
- **Sliding-window rate limit** per automation (`maxRunsPerHour`, default 5) with manual triggers bypassing it.
- **Cooldown keyed on last-attempt-START, not last-success-finish**: skip if the last successful run finished within the window OR any run (failed/running) *started* within it. The naive last-success check never engages when every run fails, so an all-failing trigger re-fires forever. Cooldown skips recorded as `skipped(cooldown)`.
- **Threshold-counter event debouncing**: event triggers carry `trigger_count` N and a persisted counter — fire every Nth event. DB-native, crash-surviving debounce.
- **Additive debounce buffer** for human follow-up ingress: coalesce rapid inputs keyed on a context key into ONE task; timer resets per append; buffer cleared *before* flush so re-enqueues during flush open a fresh buffer. Explicitly NEVER applied to schedule/workflow ingress.
- **Anti-thundering-herd stagger**: recurring jobs landing at the top of the hour get offset up to 5 min unless marked `--exact`.
- **Error paths always advance `next_fire`** so a broken trigger can't busy-loop.
- **Cluster audit**: log/warn any minute where >1 automations fire — a built-in diagnostics surface.
- **Novelty-key gating** for pollers over changing content: a stable per-item guid + persisted seen-set; a page that changes every render must not fire a run per poll.
- **Recursion guards**: explicit run flags (e.g. an escalation run marks itself so it can't re-trigger escalation); chain triggers cycle-detected with max depth; deterministic trigger/task IDs (`operator:{id}`) so re-activation is idempotent.
- **Log state *changes*, not state**: a "held/backlog" entry is emitted only when the count changes, not on every poll tick.
- **Recompute next-fire-from-now after each run** for interval triggers, preventing re-fire storms after slow runs.

### 7. Event-bus delivery contract (durable outbox → deliverers → cursor drain)
A production-grade eventual-delivery contract between local-first peers, directly reusable for any trigger→run dispatch bus:
- **Envelope**: `{schema_version, event_id, emitted_at, emitted_by, action, payload}`; stamp a stable deterministic `event_id` at emit time (notify-style: stable per-change id; create-style: entity-derived id) so every retry is a server-side no-op.
- **Durable outbox**: store the raw intent (not the built envelope); entry `{id, intent, createdAt, targets: {t1:'pending',...}, attempts}`. Per-target status `pending | delivered | given-up`; one target giving up never blocks others. Enqueue is durable-before-resolve and idempotent on `event_id`. An in-flight lock collapses concurrent flushes.
- **Deliverer contract**: `async (intent) => 'delivered' | 'transient' | 'permanent'`; HTTP mapping 2xx→delivered, 5xx/429/408/network→transient, other 4xx→permanent; unexpected throw→transient ("never drop"). Retry bounds deliberately asymmetric: ~50 send-side attempts vs ~5 receive-side, justified because server writes are insert-only idempotent on event id so re-POST is cheap.
- **Receive drain**: the consumer's seq cursor advances **only on consumed rows** (sending never touches it); mandatory paginated loop (page ~500); per-seq consecutive-failure counter bounded at 5. Failure taxonomy is the load-bearing distinction: **prerequisite-absent (handler threw, key missing) = transient → HOLD the whole drain with bounded retry** (advancing would lose the event); **prerequisite-present-but-processing-fails (malformed, contract-violating) = permanent → advance + log**. Relevance filtering advances the cursor without surfacing (skip ≠ fail).
- **Fence at the boundary in BOTH directions**: contract-violating payloads are rejected on send AND receive as permanent-skips, never error loops. Loopback skipped via `emitted_by`. Rows TTL'd (~30 d) and GC'd.
- Deterministic derived-field backfills must NOT bump `updated_at`/version stamps — every peer derives identical values, and bumping triggers fleet-wide churn storms.

### 8. Inbox + wakeup substrate (the cleanest "trigger fires a run" shape)
Scheduled fires, background-tool completions, and agent-to-agent messages all reduce to: (1) push a **typed hint block** onto the target session's inbox queue, (2) enqueue a wakeup; any dispatcher in the cluster claims it and drives the run; an inbox middleware drains hints before the next reasoning step. One typed unit (XML-fenced at the boundary) for every out-of-band injection — trigger payloads, sibling messages, background results, budget warnings — gives the untrusted-content fence a data type, not just a string wrapper. See [multi-agent-orchestration](multi-agent-orchestration.md) for the team-message half and [security-and-guardrails](security-and-guardrails.md) for fencing.

### 9. Precondition guards (skip before you spend)
Periodic triggers over queues support a cheap predicate evaluated **before materializing a run**: e.g. a consolidation timer fires every 30 min but is skipped with zero LLM spend unless ≥2 items are pending (one SQL count). A failed precondition emits at most a ledger-only "skipped" event. The companion pattern in workflow bodies: **no-op-first** — node 1 is a cheap "did anything change since last time?" watermark compare; silent skip recorded as a lightweight no-op run; "nothing changed" explicitly separated from "check failed" (which alerts). This is what makes minute-cadence triggers affordable and prevents empty-digest junk.

### 10. Heartbeat vs schedule: two distinct primitives
Multiple independent systems converge on splitting **ambient pulse** from **exact-timing schedules**:
- **Heartbeat**: a periodic main-session agent turn (defaults observed: 30 min; or daily) with full session context, batching ambient checks, **never creating task records**. A checklist file drives it: a structured `tasks:` block (`name/interval/prompt`) injects only the tasks currently due per tick, else the tick is skipped (`reason=no-tasks-due`); an effectively-empty file skips entirely. An ack token (`HEARTBEAT_OK`) is stripped if the remainder is ≤300 chars. Busy-deferral when other lanes are active. Cost levers: isolated session, light context, cheaper model, no delivery target. Hard execution cap per pulse (~5 min). Documented failure mode: the heartbeat's cheap model "bleeding" into the shared session's runtime model.
- **Schedule/cron**: exact timing, full run records, per-job overrides (model/thinking/tools/light-context), retry defaults (3 attempts, 30 s/60 s/5 m backoff), delivery routing (announce to channel / webhook POST / none, with a `NO_REPLY` suppression token and a separate failure-alert destination).
- **3-tier heartbeat triage** (every ~90 s + immediate sweep on startup): (1) preflight gate bails if healthy, (2) code-level triage for routine fixes (stall detection, status correction, stale-resource cleanup), (3) LLM escalation only for ambiguity, with the tier reached recorded on the run record.

### 11. Proactive/pulse patterns with feedback suppression
The bounded-proactivity design for "the agent surfaces things on its own":
- Proactive matters carry `{fingerprint, intentKey, targetScope, confidence, turnPrompt, notifyOwner}`. **Dedup by fingerprint**; hard bounds: max 6 pending matters, 12 recent outcomes; **at most one matter executed per tick**; skip entirely if a conversation is active (2-min grace).
- **Escalating suppression cooldowns after owner declines: 24 h → 7 d → 30 d.** Accept/decline feedback is the learning signal.
- Matters are generated from *deltas*: context-file stamps track what changed between ticks so only newly-worth-surfacing items appear.
- The digest-triage variant: a scheduled (e.g. 5am) run collects overnight sources and outputs a **strict JSON action array** (action_type, payload, permission_key, tier ∈ trivial/low/medium/high, reasoning, max 8 actions). Routing: trivial tier or a stored always-approve rule → auto-execute; stored always-deny → silently skip; else queue pending. One-word user replies ("{id} yes", "always no {id}", "yes all") persist as **pattern-keyed permission rules** (e.g. `email_delete:domain:noreply.example.com`). Anti-hallucination: item IDs must be copied exactly from digest lines. Seen-ID dedup + stale expiry per run.
- **Commitments** — a third temporal object between memory and automation: a hidden post-reply, tools-off LLM pass extracts high-confidence follow-ups ("check back on X"); records carry agent/session/channel scope + due window; delivered by the heartbeat scoped to the exact agent+channel, metadata marked untrusted, due times clamped ≥1 heartbeat interval (avoids echo-back), capped per day (3).

### 12. Interactive gate: background yields to foreground
A global cooperative gate for shared local resources: background jobs proceed only after ≥1.5 s of no in-flight interactive HTTP requests AND no browser-tab heartbeat within 45 s AND no active model stream (checked by introspecting live stream registries, since a stream outlives its originating request). Passive endpoints (health, heartbeats, stream-status polls) are excluded so polling can't hold the gate closed. Stronger still: a *running* background task is cancelled and deferred ~15 min if the user becomes active — "background means background." Combine with strict serialization of model-backed background work (Semaphore(1)), with non-model housekeeping bypassing the slot, and the queued run row written BEFORE the semaphore wait so the UI shows "queued".

### 13. Unattended-run policy regime
- **Asymmetric approval timeouts**: gate/approval waits get ~30 s under scheduled fires vs ~600 s attended — an unattended fire fails fast and observably instead of parking.
- **Ask→deny mode for unattended execution** ("DONT_ASK"): converts every would-be prompt, including safety asks, into a deny — preferred over bypass because it preserves the tools' safety net. Certain safety asks are bypass-immune (writes to `~/.ssh/`, shell rc, credentials files) and cannot be silenced by allow rules.
- **Named headless permission profile** distinct from interactive defaults; per-app/per-surface grants for external clients.
- **Declared tool/MCP allowlist per automation**: the runtime filters the global tool config down to what the automation declared; missing servers degrade with a warning. Schedule activation **freezes the action set** from the session that tested it.
- **Creation-time capability grant hazard**: auto-fired runs (view/clock) that hold write-capable connectors will fire them on every open with no per-use prompt — default read-only for auto-fired runs; write capability requires explicit flagged opt-in.
- Pre-authorize the declared toolset at session level for unattended fires, so runs don't die on the first prompt.

### 14. Budgets and retry taxonomy
- Per-run: hard wall-clock timeout (5 min observed default, via Promise.race-style races) AND `maxToolCalls` cap (~60) with mid-flight abort — two orthogonal budgets that compose.
- **Retry taxonomy**: classify failures before retrying — retry transient errors ×3 with exponential backoff (2 s/4 s/8 s); **never retry timeouts, budget-cap hits, or configuration errors** ("not configured", missing key). Two independent products landed on exactly this split.
- **Per-trigger monthly cost cap** enforced pre-claim as a recorded skip; automation listings show `cost_estimate = last_run_cost × fires_in_window(30d)` computed from the same recurrence engine that powers calendar previews (fire enumeration capped ~5000).
- Per-pattern token cost model: noop/report/action tiers (e.g. 3k/80k/250k) + daily cap (2M) + an early-exit-required flag; "an empty watchlist exits in <5k tokens" is the discipline that makes short cadences affordable. On budget exceed: pause schedulers, notify the human. `runs_count` vs `max_runs` end conditions bump only for schedule-triggered fires — manual runs never consume the budget.
- **Background model tier as a substrate-level knob**: all 24/7 loops share one cheap-model policy ("cost and speed matter more than raw intelligence" for continuous work), env/config-overridable in one place; portable intent tiers (`smol|regular|smart|ultra`) rather than provider-coupled model IDs. See [local-models-and-inference](local-models-and-inference.md).

### 15. Trigger entity schema and lifecycle
Converged fields for a triggers store: `trigger_type`, polymorphic `target_type/target_id` (target kinds `workflow | script | agent_prompt`, with the documented anti-pattern: never an agent prompt whose body is "run workflow X" — schedule the target directly), `schedule_expression`/`interval_seconds`/`event_type`, `payload_template`, `is_enabled` (separate from lifecycle status), `last_fired_at`, `next_fire_at`, plus **health rollups** (`last_run_id/at`, `last_success_at`, `last_failure_at`, `last_error_summary`) so status surfaces never scan runs. Keep a **TriggerFireHistory append log** (fired_at, run_id, launch_status, error, payload_snapshot) as the ledger-only weight class, separate from full runs. Deployment-as-instance: an automation definition is inert until deployed with baked inputs + a trigger binding + optional owned scratch workspace for cross-run continuity. Automation templates should "mainly pre-fill the prompt/config" — data, not code paths. Soft-delete with tombstones; restore leaves the schedule off.

### 16. Agent-created schedules and NL scheduling
Expose scheduling as tools the agent can call (`set_recurring_task`, `set_onetime_task`, schedule presets like daily_morning/weekly_monday), with guardrails: destructive/behavior-altering ops require explicit user confirmation; cadence-vague requests route to a **local-only nudge tool** that highlights "convert to automation" in the UI (at most one sentence, never revealing the nudge) rather than creating directly — cheaper and less naggy than auto-proposal. NL→cron inference ("every Monday morning" → cron) must echo a human-readable confirmation ("At 09:00, only on Monday"). Deny agents access to OS schedulers (crontab/launchctl/schtasks force-prompted or denied) so everything lands in the visible, auditable, cost-capped native scheduler.

### 17. Per-automation rolling memory
Cross-run continuity without a knowledge store: each automation record carries `{summary, recentTopics (cap 10), recentFiles (cap 20)}`; after each run, append ONE dated outcome line to the summary, capped at the last 5 lines; prefix the next run's instructions with "## Context from Previous Runs ... avoid repeating recent content and build upon previous work." Complementary rule from another system: recurring/automatic runs do NOT write to shared memory/knowledge stores unless the run explicitly opts in (`persistMemory`) — otherwise scheduled synthesis floods stores with near-duplicates. See [memory-architectures](memory-architectures.md).

### 18. Live-updating dashboards and view-triggered refresh
- **Refresh-on-open with stale-while-revalidate**: serve the cached view instantly, re-query sources, swap in place; a short-TTL cache bounds cost. Measured: heavy multi-source dashboards cost 3-5% of a daily token budget per open, 5-20 s latency — cheap enough on-demand, ruinous on a nightly cron nobody reads.
- **Layout/data separation**: the visual skeleton is generated once (LLM); steady-state refreshes re-run only the data-producing steps and re-bind slots — no LLM rewriting HTML, so layout stays stable across refreshes ("new data, same layout, no re-prompting").
- **Dumb-renderer snapshot widgets**: push a pre-computed JSON snapshot holding **raw ISO timestamps only**; relative labels ("in 12 days", "running 2h") computed at render time — a midnight tick re-renders everything fresh with zero data re-push.
- **Bulk-hydrate first paint**: one endpoint returns `{widgetData: {[id]: data}, errors, loaded, total, loadedAt}`; each tile wrapped in its own try/catch so one failure never poisons the batch; slow tiles get explicit typed skeletons (`{status:'loading'}`), not absent keys; every entry stamped `loadedAt` for staleness display.
- **Tri-state liveness** per tile/automation row: `null` (gray, before first probe) / online / offline, with a probe-kind enum (`http` — GET, 10 s timeout, online iff 200-399; `ping` — one ICMP, 1 s wait) distinct from the launch URL.
- Per-source status chips + freshness timestamps + last-run deep-links on every tile — silent empty panels and silent write failures are the top practitioner complaints.
- **Backlog counts as health stats**: pending-queue depth (unprocessed items, pending proposals) surfaced as a first-class dashboard number.
- A read-only observation surface (tray/inbox view) **never consumes/acks** the underlying events — a separate explicit action does.

### 19. Outbound notification/webhook delivery discipline
Every automation-run completion POST carries: a **stable event id across delivery attempts** (the consumer's idempotency key), an event-type header (`*.execution.succeeded|failed`), a **statusUrl deep link** to the run record, and per-destination format negotiation (payloads auto-flattened to primitive strings for destinations that can't take nested JSON, sniffed by URL). Notification policy per source: `done_only | state_changes | silent`. Anti-fatigue rules: only ping when a human decision is needed; batch report-only output into digests; alert when a needs-input item lingers >24 h.

### 20. Digest pipelines
The converged shape: **synthesize/collect sources → aggregate into one triage point → deterministic filter → (optional LLM synthesis) → single digest**. Load-bearing details: (a) every item needs a **stable novelty key (guid)** + persisted seen-set so pollers only act on NEW items — without it, until-cancelled monitors re-process the same items forever; (b) an escalating fetch chain (plain HTTP → scrape API → real browser) driven by *extraction outcome* (did we get items?) not HTTP status, all attempts sharing one per-run `max_requests` budget, escalations logged; (c) a terse deterministic rule grammar (`intitle:`, `author:`, `#tag`, ISO-8601 `date:P1M`, regex, boolean+parens) does 90% of triage with zero tokens, and a saved filter is itself re-exposable as a new event source; (d) a raw no-AI pass-through mode must exist — some users explicitly reject LLM curation; (e) hygiene presets (drop off-domain links, min title words, sanitize HTML) at the substrate boundary. See [knowledge-pipelines](knowledge-pipelines.md) for the synthesis half.

### 21. Standing delegations (authority documents)
Persistent authority-granting documents referenced by triggers rather than duplicated in prompts: each standing order defines **Scope (authorized actions), Triggers, Approval gates, Escalation rules, and an explicit "What NOT to do" list**. The trigger fires the *when*; the order defines the *what*. Discipline loop: Execute → Verify → Report; retry once/max 3 then escalate; never fail silently; "start narrow, expand as trust builds." The recommended layered stack for a scheduled workflow: cron for timing → persistent session for context → deterministic steps + approval gates → durable run tracking across retries/restarts → **preflight checks (credentials/network/tool availability) before work** → provenance fields (`sourceUrl, retrievedAt, asOf`) on collected data → schema-validated model steps. See [planning-and-decomposition](planning-and-decomposition.md) and [verification-and-judging](verification-and-judging.md).

## Patterns & compositions

- **The complete loop = discovery + handoff + verification + persistence + scheduling.** Scheduling is what "makes a loop an actual loop"; the trigger should invoke a *named skill*, not a wall of instructions pasted into a cron job nobody updates. Minimal viable loop = schedule + one triage skill + a state file; add isolation when it edits, a verifier when it acts autonomously, connectors when it drives external systems — each primitive added only after the previous has proven its value. See [self-improvement-loops](self-improvement-loops.md).
- **Morning digest**: clock trigger → foreach over registered sources → fetch+extract new items (novelty-gated) → rule-grammar filter → one digest into the inbox/runs surface. Proven end-user demand (replaces the morning-tabs ritual).
- **Proactive triage flagship**: scheduled digest-collect → strict-JSON tiered action proposals → persistent always-approve/deny pattern rules → channel notification with one-word replies (mechanism 11).
- **Recency/upkeep tracker → planner escalation**: a passive recency store (elapsed-since-last-done, fresh→amber→red gradient, optional cadence targets, seasonal windows) pushes overdue items into the active task system, per-item Manual or Auto mode, Auto throttled to once daily while overdue.
- **Monitor → dual sink**: one until-cancelled watcher feeds both a queryable knowledge entry and a glanceable live dashboard tile from the same run.
- **Push-to-pull migration**: any scheduled synthesis whose only consumer is a human view should become a view-triggered refresh; the planner should ask "one-time answer or a view that stays fresh?" when the intent looks like a tracker/dashboard.
- **Operator/recipe compilation**: one declarative manifest (agent config + schedule + metrics allowlist) compiles via pure functions into BOTH a production schedule and an eval suite; `status()` merges the static manifest with live scheduler state (next_run/last_run); manifests declare which telemetry fields the automation may expose.
- **"While you were away" review card**: the missed-fire records + newest-N review + collapsed summary (mechanism 3) rendered as a launch-time surface — the "what did my machine NOT do while I slept" complement to the runs inbox, with sent/held/received-style badges, newest-first, hard-capped.
- **Filters-as-feeds**: a saved query over any event stream is itself addressable as a new event source triggers can subscribe to — composable stream algebra without new trigger kinds.

## Anti-patterns & failure modes

- **No overlap guard / no fire-time lock**: verified double-fire hazard in a schema-first system that stored `next_fire_at` correctly but never checked in-flight runs. Persist-before-execute alone is not enough.
- **Cooldown keyed only on last success**: all-failing triggers never engage the cooldown and re-trigger forever (rate-limit spirals).
- **Path-keyed file-watch dedupe**: re-ingests renamed duplicates, never re-ingests edited files. Key on content hash.
- **Silent skips of any kind** — overlap, cooldown, cap, missed fire. Every one becomes an untraceable "my automation didn't run" support mystery.
- **One asyncio task per trigger**: rescheduling becomes a thundering re-spawn problem; a single loop with a kick event scales better and is easier to reason about.
- **Auto-catch-up of all missed fires** after downtime: floods the system and re-executes stale intent. Review-or-collapse instead.
- **Sub-minute/minute cadences for LLM-backed triggers** without a floor: "no once-a-minute token-burning loop" — floor at ~15 min, and route truly-reactive needs through event triggers.
- **Scheduled push synthesis for human-viewed surfaces**: a nightly task that "chewed through tokens" was strictly dominated by refresh-on-open. Decayed static reports are the "photograph"; pull views are the "window."
- **Write-capable connectors on auto-fired runs**: they execute on every fire/open with no per-use prompt ("if it can post to a channel, it will do that on open unless constrained"). Default read-only; explicit opt-in for writes.
- **Write-only telemetry**: a score computed at fire/ingest time that no code path ever reads (filters, sorts, thresholds) is dead weight plus wasted tokens. Every score/threshold must name its consumer.
- **The nodding/amnesiac/manual loop triad**: no verification (a loop that has never once said "no" to itself across hundreds of runs is statistical proof no real check exists), no persistence (each morning starts from zero), no schedule ("last run was the day it was demoed"). Hasty automations install the visible-output primitives and skip the safety ones.
- **Agent-prompt dispatch shims**: a scheduled agent task whose body is "trigger workflow X" — schedule the workflow directly; keep LLMs out of pure dispatch.
- **Notification fatigue**: pinging on every run outcome trains the owner to mute everything, which is itself a kill criterion; decision-needed pings only, digests for the rest.
- **Heartbeat/cron/background runs extending session idle-freshness or bleeding their cheap model into the interactive session** — background lanes must be isolated on both axes.
- **A no-op per-node timeout**: `future.result(timeout=...)` inside an `as_completed` loop never fires — the exact bug class to regression-test in any parallel dispatch path.
- **Dead schema fields**: `retry_count`, `checkpoint_id`, `state_snapshot` columns with no engine semantics behind them — schema-first ambition without executor behavior misleads every consumer.

## Quantitative findings

- Missed-fire enumeration cap 480 (~5 days of a 15-min schedule); newest 20/trigger reviewable; `ran_late` threshold = start >300 s after slot.
- Catch-up policy: exactly 1 run per missed trigger, staggered 2 s apart; default rate limit 5 runs/hour sliding window (manual bypasses).
- Retry defaults: 3 attempts, exponential 2 s/4 s/8 s (batch runs) or 30 s/60 s/5 m (cron); never retry timeout/budget/config failures.
- Per-run budgets: 5-min hard timeout + 60 max tool calls (both enforced, they compose); heartbeat execution cap 5 min; subagent watchdog 3-min inactivity kill.
- Scheduler loop: sleep clamp 1 s–60 s; interval floor 15 min; interval clamp ≤365 d; top-of-hour stagger ≤5 min; startup overdue push +60 s; fire-enumeration cap 5000 for cost projections.
- Approval timeouts: 30 s unattended vs 600 s attended (20× asymmetry).
- Outbox/drain: send-side retry bound 50 vs receive-side 5 (asymmetric by design); page size 500; event TTL 30 d; activity log cap 200 entries.
- Proactive pulse bounds: max 6 pending matters, 12 recent outcomes, 1 executed per tick, 2-min active-conversation grace; decline suppression 24 h → 7 d → 30 d; commitments ≤3/day, due ≥1 heartbeat interval out.
- Interactive gate: ≥1.5 s HTTP quiet + no tab heartbeat 45 s + no live stream; running background work cancelled and deferred 15 min on user return; escalating defer ≥40 min after 2 defers.
- Consolidation-style precondition: skip unless ≥2 pending items; batch LIMIT 10; timer 30 min.
- Task-ledger health thresholds: `stale_queued` >10 min, `stale_running` >30 min; sweeper every 60 s; terminal records pruned 7 d, `lost` 24 h; workboard stuck-work diagnostics: ready-unclaimed >1 h, no heartbeat >20 min, blocked >24 h.
- Loop cost tiers (one published model): noop 3k / report 80k / action 250k tokens, daily cap 2M, early-exit required; "empty watchlist exits in <5k tokens"; cadence multiplier: 5-min vs daily = 288×/day.
- View-refresh economics: heavy 4-source dashboards 3-5% of a daily token budget per open, 5-10 s (single source) to 15-20 s (4 sources) latency; one practitioner deleted a nightly scheduled task in favor of pull-on-open.
- Heartbeat triage sweep every 90 s + immediate on startup; SSE live-stream comment heartbeats every 10 s, replay-buffer eviction 180 s after last subscriber.
- Ops thresholds (slow/pause/kill): slow at budget >80% mid-week or triage false-positive >30%; kill when "cost > value for 2 consecutive weeks" or the team mutes all notifications.
- Chain-trigger bounds: cycle-detected, max depth 10, cross-owner blocked; heartbeat resume generations bounded by env cap.

## Open questions

- **Where exactly is the push/pull boundary?** Refresh-on-view demonstrably wins for human-viewed surfaces, but hybrid cases (a digest that should be ready *before* the user wakes, but only if they'll read it) have no converged answer; predictive pre-warm from view-history is unexplored in the corpus.
- **Event-condition scripts with full tool policy**: one system ships condition scripts on cron jobs (`{fire, message?, state?}`) but leaves them off by default because they run with the agent's full tool policy — no source demonstrates a satisfying sandboxed-predicate design.
- **Cross-run resource claims**: storm guards protect the scheduler, but concurrent autonomous runs contending for the same external resource (a repo, an inbox) only have a sketch (`acting_on` claims + one-owner-per-resource); no production-verified protocol appeared.
- **How far to trust NL→cron inference**: human-readable echo-back is the converged mitigation, but no system measured mis-inference rates or ships a validation harness for schedule inference.
- **Pulse feedback beyond suppression**: escalating decline cooldowns are proven, but no source closes the loop into *positive* learning (raising confidence/frequency for accepted matter classes) with measured outcomes — the accept side of the feedback loop is unbuilt everywhere.
- **Unified vs split substrates**: one camp routes everything through a single trigger→run substrate with weight classes; another deliberately keeps heartbeat, commitments, and cron as separate primitives with different record semantics. Both work; the corpus does not settle which scales better with automation count.
