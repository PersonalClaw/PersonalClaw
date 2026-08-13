# PLATFORM-RESILIENCE

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/PR2.md`](../atomic/PR2.md) as 11 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Platform Resilience — Doctor, No-Model Degraded Mode, Mid-Turn Message Handling

**Status:** DONE — all 6 sessions landed (S1-S5 2026-07-25; S6 mid-turn steering 2026-07-29). S1 Doctor core (§1) · S2 no-model degraded contract (§5) · S3 mid-turn message handling (§6) · S4 confirm-gated fixes + trust simulators + crash capture (§2/§3.1/§3.2/§6.5) · S5 health-scored remediation engine (§4). Deferred-as-future-infra (E6, recorded per session): §3.3 automation would-execute (AUTOMATION-SUBSTRATE), the richer §3.2 memory-pipeline alarm + judgment-lane remediation jobs (LEARN-R19/KNOW-R17 flywheel infra), and the AUTOMATION-SUBSTRATE trigger-form for the engine's cadence (it runs off the heartbeat until then). Created 2026-07-13 from research synthesis, promoted from backlog.
**Created:** 2026-07-13
**Wave:** split — §1-§3 (doctor probes + read-only surface) and §5 (degraded contract) are Wave 0/1 invariants (every existing surface touches models; offline behavior must be designed before the engine multiplies unattended runs); §6 (mid-turn handling) is Wave 1, independent; §4 (remediation engine) is Wave 3 — it consumes AUTONOMY-GUARDRAILS budgets (SpendMeter, §1.1 there) and should land after them.
**Depends on:** nothing for §1-§3/§5/§6 (Wave-0-compatible). §4 depends on AUTONOMY-GUARDRAILS (SpendMeter + model-call audit for cost caps) and prefers AUTOMATION-SUBSTRATE (runs as an adaptive-cadence trigger once triggers.json exists; hangs off the heartbeat until then).
**Scope:** one diagnosis-and-degradation substrate: tiered health probes with a capability-degraded-is-never-core-failure doctrine + confirm-gated auto-fixes (NEW-18); a trust/debug simulator surface (surfacing simulator, memory-pipeline probes, automation dry-run affordance); ONE health-scored self-remediation engine replacing N maintenance crons (NEW-18/GBrain); a platform-wide declared no-model fallback contract with pending-enrichment queues (NEW-21); and a declared per-channel mid-turn message policy — prompt queue + optional cancel-and-replace (NEW-29).

---

## Research Integration (2026-07-13)

- **NEW-18** (Doctor: tiered probes process → socket → cheap RPC → per-capability; capability-degraded-is-never-core-failure doctrine; confirm-gated auto-fixes — stale cache, symlink repair, orphan pruning; per-provider selftest endpoints; GBrain health-scored self-remediation — deficit score, dependency-ordered plan, target-score + max-cost caps, adaptive idle cadence, one engine replacing N maintenance crons) → §1, §2, §4. Sources: `clawx`, `gbrain-memory`, `omnivoice-studio`, `ai-context-os`, `claude-memory-compiler`, `openjarvis`.
- **NEW-18 amendment a** (surfacing simulator: dry-run any hypothetical query → per-candidate per-signal score breakdown, tier decision, inclusion/exclusion reason, zero LLM calls) → §3.1.
- **NEW-18 amendment b** (memory-pipeline probe set: last capture per source, no-op vs saved counts, staging backlog, per-op LLM cost — silent memory-pipeline death becomes visible) → §3.2.
- **NEW-18 amendment c** (dry-run mode for triggers/automations: would-execute description without touching any wired system, sibling of the surfacing simulator on the same trust/debug surface) → §3.3, honoring the approved **AUTO-R15** dry-fire smoke gate + `automation_run(dry_run?)` (WORKFLOWS-V2-AUTOMATION-SUBSTRATE §4.1/§4) — this plan adds only the unified would-execute rendering on the trust surface.
- **NEW-21** (platform-wide no-model degraded mode: per-surface declared fallback contract — deterministic fallbacks + pending-enrichment queues that drain when a provider returns + visible degraded indicator) → §5. Sources: `knowledge-forge`, `moss-audio`. The knowledge instance is ALREADY APPROVED as **KNOW-R17** (WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS §2.3, zero-model heuristic extraction floor); this plan generalizes the contract platform-wide and registers KNOW-R17 as its first instance rather than re-specifying it.
- **NEW-29** (mid-turn message handling: prompt queue + optional cancel-and-replace per channel via a per-channel active-job tracker with cancellation propagation) → §6. Sources: `air-dev` (prompt queue for busy sessions), `localagi` (cancel-previous-on-new-message per conversation_id).

---

## Overview

PersonalClaw is a constellation of degradable subsystems — gateway, app backend subprocesses, channel transports, six local-model providers, memory/knowledge stores, the FE static-dist symlink — and its failure history is dominated by *silent* degradation: the static/dist copy-shadows-symlink bug served a stale SPA for days; sentence-transformers showed every model "not downloaded" while embedding live; a dead NDJSON transcript read killed settings/archive with zero errors; the S05 class generally. Verified starting points:

- **Health surface today is thin:** `GET /api/status` and `GET /api/system` exist (`dashboard/server.py:368-369`) and the dashboard polls them (`DashboardLive` FAST/SLOW_POLL), but they are aggregate snapshots, not tiered probes. Settings → Diagnostics (`web/src/pages/settings/DiagnosticsPanel.tsx`) is a **log tail only** — the SUBPAGES registry (`SettingsPage.tsx:66`) is the plug-in point for a real Doctor tab.
- **Per-capability probe seams already exist, unconsulted:** `ChannelTransportProvider.health()/test()` (`channel_transports/base.py:69`), `LocalModelProvider.is_available()` (`local_models/provider.py`), `ModelCatalog.test_connection()` (`llm/catalog.py:302`, contract: must NOT open a session), app backend `healthCheck` + watchdog (`apps/backend_runtime.py`, `start_enabled_app_backends()`), the loader `availability()` hook (`providers/loader.py`), and `provider_bridge.can_resolve_use_case` (`provider_bridge.py:672`, the cheap no-instantiate probe). Nothing composes them into one triage view.
- **Maintenance is N independent tick-modulo jobs:** heartbeat (`heartbeat.py`, hard 60s) runs FTS rebuild every 15 ticks, daily history/SEL prune + skill-curator aging; inbox runs its own 6h maintenance pass; `verify_skill_integrity`/`run_aging` are callable seams with no scheduled caller verified (persistence recon, explicit absences). GBrain's doctor/autopilot shape (deficit score → dependency-ordered plan → target-score/max-USD caps → adaptive idle cadence) is strictly better and AUTOMATION-SUBSTRATE §4.1 already gestures at it ("optionally run as ONE health-scored maintenance trigger") — this plan builds that engine.
- **No-model behavior is accidental, not designed:** `one_shot_completion` callers (inbox classify/draft/digest, memory after-turn review, knowledge insights) fail per-call-site with inconsistent behavior; the two *good* precedents — inbox alert evaluation is deterministic at ingestion (`inbox.py:270 evaluate_alert`, keyword/name-mention, zero LLM) and diarization is a declared "unbound ⇒ feature off" tier (`use_cases.py:47`) — prove the contract shape but nothing generalizes it. KNOW-R17 (approved) declares it for knowledge; the rest of the platform has no floor.
- **Mid-turn machinery half-exists:** `SessionManager.enqueue/dequeue` serializes channel threads (`session.py:1331`), the dashboard session `_queue` + `queue_push/pop/cancel` WS events are live (`chat_handlers.py:162/915`, `ChatPage.tsx` handles all three), `dashboard.merge_queued_messages` exists (`chat_runner.py:2618`), mid-turn *steering* exists (`session.py:add_steer`, #37), and `stop_turn(preserve_queue=True)` is the /interrupt verb (`session.py:1529`). What is MISSING is a *declared policy*: cancel-and-replace does not exist, there is no per-channel choice, and busy-state is only an internal semaphore (`session.semaphore.locked()`) invisible to channels and the FE.

**Soul guardrail:** this is a *personal* resilience layer — one user, one gateway, probes over local files and loopback sockets. No fleet monitoring, no alerting infrastructure, no SLO machinery. The Doctor is a Settings tab; the remediation engine is one background job with a dollar cap; degraded mode exists so the assistant stays useful on an offline laptop with a dead ollama.

---

## 1. Doctor — Tiered Probe Framework

### 1.1 Probe tiers (ClawX three-tier readiness, extended per-capability)

Readiness is NOT boolean. Every diagnosis names the tier that failed:

```
tier 0  process    — gateway alive; app backend subprocesses alive (watchdog state);
                     MCP tool processes reachable
tier 1  socket     — :10000 listening; app reverse-proxy ports connectable
tier 2  cheap RPC  — GET /api/status succeeds (the system-presence analog);
                     app healthCheck route returns 200
tier 3  capability — per-capability probe packs (§1.2)
```

New module `resilience/doctor.py`: `Probe{id, capability, tier, run() -> ProbeResult{ok, detail, evidence, fix_id?}}` + a flat probe registry. `run_doctor()` executes tiers in order, **short-circuiting downward** (a tier-2 failure doesn't run tier-3 packs against a dead gateway — it reports "core failure at tier 2"). Probes are read-only by contract; exceptions become `ok=False` results, never 500s (the AUTO-R15 rule, restated here as the framework invariant). Secrets masked in `detail`/`evidence`.

- **HTTP surface:** `GET /api/doctor` (run all, grouped by capability, cached 30s) + `GET /api/doctor/{capability}` — new handler `dashboard/handlers/doctor.py` beside the existing handler modules.
- **FE:** a **Doctor tab** in Settings via the SUBPAGES registry (`SettingsPage.tsx:66` precedent, next to the existing Diagnostics log-tail, which stays). Grouped capability cards, tier-failed badges, evidence disclosure, fix buttons (§2). The dashboard `SystemHealth` widget gains a one-line doctor rollup (worst capability), linking to the tab.

### 1.2 Per-capability probe packs (the four named in NEW-18, plus what the recon exposes)

| Capability | Probes (all derived from EXISTING seams) |
|---|---|
| **memory** | `memory.db` opens + WAL healthy; `memory_index.db` FTS row count vs source-of-truth count; `memory.faiss` + `memory.ids.json` sidecar consistency (id count matches index size); embedding use-case resolvable (`can_resolve_use_case("embedding")`) |
| **channels** | per registered transport: `connected` prop → `health()` → `test()` (`channel_transports/base.py:69`); inbound receiver liveness (`start_inbound` task alive) |
| **local-models** | per provider: `is_available()`; per BOUND model (active_models refs): downloaded-layout probe covering **every path the download writes** — HF `models--…` layouts included (the delete/detection bug-class, `reference_local_model_delete_detection`); phantom-binding detection (bound ref absent from catalog) |
| **apps** | per enabled app: backend subprocess alive (watchdog) + `healthCheck` 200; installed-copy manifest vs repo drift (the `POST /api/apps/{name}/update` gap); leftover `.{name}.rollback` dirs from interrupted updates |
| **knowledge** | `knowledge.db` opens; `items_fts` consistency; provider attribution list loads (`dashboard/handlers/knowledge.py:481`) |
| **model-providers** | per config entry: `ModelCatalog.test_connection()` (fail-soft, no session); breaker state + latency percentiles COMPOSED from AUTONOMY-GUARDRAILS §2.5's `GET /api/models/health` (that plan owns the model-call audit; the Doctor renders it, never rebuilds it) |
| **serving/fs** | `static/dist` is a SYMLINK to `web/dist` (the documented bug-class: a copy shadows the runtime auto-symlink and serves a stale SPA); stale `locks/*.lock` files (`concurrency.reap_orphans` seam, `concurrency.py:91`); `session_pids`/`agent_pids` entries whose PIDs are dead |
| **automations** | mounts the approved **AUTO-R15 automation doctor** check set (unknown kinds, orphaned workflow refs, stale next_fire, broad file-watch globs) as one pack — owned by AUTOMATION-SUBSTRATE, registered here, not re-specified |
| **memory-pipeline** | the §3.2 probe set |

### 1.3 Doctrine: capability-degraded is never core failure

Adopted verbatim from ClawX (learning #3) as a written invariant on the framework:

1. A tier-3 capability failure degrades ONLY that capability's row — it never marks the gateway unhealthy and **never justifies a restart**. Restart is justified only when the tier-2 cheap-RPC probe itself fails.
2. Diagnostics trust native probes over log-scraping — the log tail (existing DiagnosticsPanel) is *supporting evidence only*; every finding cites a probe result.
3. No ready/healthy signal may come from a pure timer — it must re-probe first, and must not emit duplicate ready transitions (probe results carry a monotonic `probe_seq`).

This doctrine also feeds §5: a capability whose probe fails flips that capability's degraded contract on (one signal source, two consumers).

### 1.4 Per-provider selftest endpoints (ground truth for the Test buttons)

`POST /api/providers/{name}/selftest` (extends the existing `providers/routes.py` `/api/providers/...` surface): dispatches a **tiny real inference** per declared capability — one-token chat completion, one short-string embed, sub-second TTS synth, tiny STT on a bundled 1s wav — instead of the availability guess `test_connection` gives. **User-click only** (never run by the background engine — real inference costs tokens/compute); result cached on the extension row so the Settings Test buttons show last-selftest ground truth + timestamp. Providers need no contract change: dispatch goes through the same use-case ABCs the provider already implements (`SttProvider.transcribe`, embed, etc.), with a hard timeout.

---

## 2. Confirm-Gated Auto-Fixes

Every fix is a `Fix{id, title, impact_description, dry_preview(), apply()}` attached to a probe result via `fix_id`. **Nothing auto-applies.** The Doctor tab renders the fix with its impact description; a two-step confirm (the FE's armed-delete pattern) runs it; every application is SEL-audited (`sel.py`), same as egress/skill-install guards. The ClawX Dreams precedent (destructive maintenance verbs confirm-gated) is the UX template.

Initial fix catalog (each pairs with a §1.2 probe):

| Fix | Mechanism |
|---|---|
| **Symlink repair** | replace a non-symlink `static/dist` with `ln -s` to `web/dist` (backing up the shadow copy) — closes the serve-stale-SPA bug-class permanently |
| **Stale-cache cleanup** | purge `.skill_embeddings.json` entries whose path+mtime+model key no longer matches (`skills/surfacing.py _EmbedCache`); FTS index rebuild (the heartbeat's existing rebuild, invoked on demand); faiss/ids.json re-index when counts disagree (single-process guards per `reference_st_reindex_loky_segfault`) |
| **Orphan pruning** | dead `locks/*.lock` (via `reap_orphans`); dead PID rows; `.{name}.rollback` leftovers; `cron-history/{job_id}.jsonl` files for deleted jobs; active_models refs to removed providers (surfacing what `load_active_models` already prunes silently — here it's shown + confirmed) |
| **Manifest resync** | push repo `apps/` manifest → installed copy via the existing `POST /api/apps/{name}/update {source, confirm:true}` |

Fixes never delete user content (memory entries, knowledge items, tasks): pruning targets harness mechanics only. Anything content-adjacent (e.g. orphaned knowledge relations) is *flagged, never auto-deleted* — the GBrain maintain rule.

---

## 3. The Trust/Debug Simulator Surface

One Doctor-tab section answering "why did/didn't X happen?" and "what WOULD Y do?" — all three simulators share the property of **zero side effects and zero LLM calls**.

### 3.1 Surfacing simulator

A dry-run box: type any hypothetical query/turn text → every candidate entity (skill / lesson / SOP / template) renders with its **per-signal score breakdown** (keyword-gate score, semantic cosine, negative-trigger vetoes, archived skips, use-count tiebreak — exactly the arms `skills/surfacing.py:surface_skills` computes today), the threshold applied (0.55 semantic / 0.7 keyword), the tier decision, and a one-line inclusion/exclusion reason. Zero LLM calls — it runs the same deterministic scorer the real turn runs, in explain mode.

**Overlap honored:** the *scoring machinery* and its evolution (per-arm confidence, slot allocator, surfacing_events measurement, near-miss ledger) are owned by the approved **LEARN-R4/R7/R15** (WORKFLOWS-V2-LEARNING-FLYWHEEL §2.4/§2.5) and the observability panel by **LEARN-R14** (§6 there). This plan's remainder is only the *dry-run explain UI*: `POST /api/doctor/simulate/surfacing {text}` calls the scorer with an `explain=True` flag (a pure-function addition — the scorer returns the intermediate arm scores it already computes instead of discarding them). When the flywheel's merged allocator lands, the simulator upgrades to its richer breakdown for free — same seam.

### 3.2 Memory-pipeline probe set

Silent memory-pipeline death (the S05 bug-class: a 100%-broken transcript read with zero errors) becomes a Doctor capability row:

- **last capture per source** — most recent successful extraction per cadence (per-turn, session-end, consolidation), from the staging log;
- **no-op vs saved counts** — reading the approved **LEARN-R19** outcome records (`FLUSH_OK` / `FLUSH_ERROR` / proposal IDs — WORKFLOWS-V2-LEARNING-FLYWHEEL §2.1): "7 days of all-FLUSH_OK on an active system" renders as a WARN, exactly the alarm LEARN-R19d specifies;
- **staging backlog** — uncompiled-staging-entry count (consolidation falling behind);
- **per-op LLM cost** — LEARN-R19e's metered costs, aggregated.

**Overlap honored:** the *records* are LEARN-R19's; this plan adds only the probe pack that READS them into the Doctor. Until learning.db lands, the pack degrades to what exists today (last consolidation timestamp from `history.py`, `after_turn_review` invocation counts) with a "richer after flywheel" note.

### 3.3 Automation dry-run affordance

"What would this automation do right now?" — a per-trigger **would-execute description**: resolved schedule/next-fire, the action provider + rendered `action_config` with `$vars` substituted from a sample payload, the target session key, capability grants, and — for providers where a true dry-run is possible — the observe-mode result.

**Overlap honored:** the execution machinery is APPROVED — `automation_run(id, dry_run?)`, the dry-fire smoke gate button, and the T9 recon rule (dry-run against bash/run-script/webhook is *refused and recorded as a preview*; only run-prompt/run-workflow truly dry-run, `ActionProvider.supports_dry_run`) all belong to **AUTO-R15**/WORKFLOWS-V2-AUTOMATION-SUBSTRATE §4. This plan's remainder is the unified *rendering* of that output on the trust surface (same panel as §3.1/§3.2), so "simulate a query" and "simulate a trigger" live side by side before the user grants unattended operation.

---

## 4. Health-Scored Self-Remediation Engine (Wave 3)

ONE background engine replacing N independent maintenance crons, shaped on GBrain's `doctor --remediate --target-score --max-usd` + `autopilot` (gbrain-memory learnings #10, mapped improvement "Health-scored maintenance runs"):

### 4.1 Deficit score

`health_score = 100 − Σ weighted deficits`, computed from **measured problems only** (never guesses): stale/heuristic-stamped embeddings and knowledge entries (§5's pending-enrichment backlogs), FTS/faiss desyncs, orphan counts (§2's probe outputs), staging backlog (§3.2), failed-run backlog, skill-curator aging due, expired-TTL knowledge probes. Each deficit source declares a **`max_reachable_score` ceiling** — no embedding provider bound caps the score contribution of embedding-freshness at its floor, so the engine never burns budget on futile work (GBrain's empty-brain/missing-key ceilings).

### 4.2 Dependency-ordered plan under caps

The engine builds a remediation plan respecting declared job dependencies (sync before extract, embed after consolidate — jobs carry `after: [job_id]`), then executes step-by-step, **re-checking the score after each step**, stopping at whichever comes first: `target_score` reached (default 90), `max_cost_usd` spent (default $1/run), or plan exhausted.

- **Cost accounting consumes AUTONOMY-GUARDRAILS:** every LLM-touching job runs through the §2 model-call chokepoint there; the engine charges `SpendMeter` under scope key `doctor` and reads attempt-level dollar estimates from `model_calls.jsonl`. Deterministic jobs (FTS rebuild, prune) cost $0 and never block on budget.
- **Two-lane rule (GBrain):** deterministic work executes directly in-process; judgment work (re-extraction of heuristic-stamped entries, semantic lint) goes through `one_shot_completion(use_case="background")` under the budget.
- **Storm-proofing:** per-job `cooldown_hours` with **success-only timestamps** + content-hash idempotency — the same three fields the approved AUTOMATION-SUBSTRATE storm guards carry; the engine uses them natively.

### 4.3 Adaptive idle cadence

Healthy (≥95) → sleep 60 min; degraded → 5-min tick with targeted jobs. Once AUTOMATION-SUBSTRATE lands, the engine IS one trigger (adaptive clock kind, `created_by: system`) on the Automations page — fulfilling §4.1 there ("optionally run as ONE health-scored maintenance trigger… replacing N independent maintenance crons") with this plan as the implementation. Before that, it hangs off the heartbeat loop as one job.

### 4.4 What it absorbs (disposition)

| Today | Disposition |
|---|---|
| heartbeat FTS rebuild (every 15 ticks) | → registered remediation job (deterministic lane) |
| heartbeat daily prunes (history, SEL) + skill-curator aging | → registered jobs with `cooldown_hours: 24` |
| inbox 6h maintenance (retention, dismissed-prune) | → registered job |
| `verify_skill_integrity` (currently caller-less) | → registered job — finally scheduled |
| heartbeat consolidator idle-check, commitments delivery, HEARTBEAT.md tasks | **KEPT on heartbeat** — these are user-facing behaviors, not maintenance |

Every remediation run writes a ledger row (`~/.personalclaw/doctor/remediation.jsonl`, notifications.jsonl trim conventions): `{ts, score_before, score_after, jobs: [{id, status, cost}], stopped_reason}` — the Doctor tab renders the last runs, and the runs-inbox "learned overnight" digest (approved AUTOMATION-SUBSTRATE) picks them up like any other run.

---

## 5. Platform-Wide No-Model Degraded Mode

### 5.1 The contract

Every model-dependent surface declares its LLM-free tier explicitly, so offline operation is designed rather than accidental:

```python
# resilience/degraded.py
@dataclass(frozen=True)
class DegradedContract:
    surface: str                  # "knowledge_ingest", "inbox_enrichment", ...
    use_cases: tuple[str, ...]    # active_models use-cases it needs ("background", "embedding", ...)
    floor: str                    # human-readable statement of the deterministic fallback
    backlog_probe: Callable       # () -> int   pending-enrichment count
    drain: Callable | None        # async () -> None   re-enrich when a provider returns

register_contract(contract)       # module registry, consulted by Doctor + FE indicator
```

Availability per contract = `all(can_resolve_use_case(uc) for uc in use_cases)` (`provider_bridge.py:672`, the cheap no-instantiate probe) AND the §1.3 capability probe not failing. The registry re-evaluates on: provider config change (the `sync_entries_from_config`/create-handler path + extension enable/disable), a slow poll (60s), and an explicit Doctor run. On a flip unavailable→available it fires each contract's `drain` as a background task — **under the §4 engine's budget once it exists** (drains are judgment-lane remediation jobs), plain `asyncio.create_task` before then.

### 5.2 Per-surface tiers (initial contract set)

| Surface | Deterministic floor | Pending-enrichment queue + drain |
|---|---|---|
| **knowledge ingest** | **KNOW-R17 (approved)** — frequency+bigram extraction, first-paragraph summary, structural linking, `extraction: heuristic` stamp | KNOW-R17's own: heuristic-stamped entries re-extracted in place. This plan REGISTERS it, does not re-specify it |
| **memory extraction** | per-turn/after-turn LLM review skipped; `capture_preference_facet`-class deterministic captures continue; transcript refs appended to the LEARN-R19 staging log (the queue that already exists by design) | staging entries flagged `pending_model`; consolidation pass drains them on provider return |
| **inbox** | alerts ALREADY deterministic at ingestion (`inbox.py:270 evaluate_alert` — keyword + name-mention, zero LLM); ingestion/dedup/mute all LLM-free. Declared as the existing floor | classify/draft/digest per-item: items get `enrichment: pending`; drain re-runs the one-shot affordances over pending items |
| **search ranking** | hybrid retrieval degrades vector-arm-off: FTS/keyword arms + timestamp sort remain (memory FTS index + knowledge `items_fts`); the KNOW plan's explicit ladder vector → FTS → substring is the template | new/changed items get embeddings backfilled on drain (re-index job, §4 deterministic-then-embed ordering) |
| **synthesis watchers** | `mode: append_evidence` continues (persist-raw-first is already the approved KNOW design); compiled section marked `stale: awaiting model` | compiled-section rewrite queued; periodic synthesizer drains |
| **STT/TTS/diarization** | diarization precedent kept as-is: unbound ⇒ feature off (`use_cases.py:47`) — a declared floor of "feature visibly off", which is a valid tier | none (feature-off surfaces don't queue) |
| **chat** | **honestly unavailable** — no fake fallback. The composer shows the degraded banner with a Doctor deep link and the `needs_model` onboarding affordance (already keyed off `can_resolve_use_case`). Pretending to chat without a model violates trust more than admitting it | n/a |

The floor doctrine: a degraded surface **never error-walls** — it does less, says so, and queues the rest. A surface with no declared contract that calls `one_shot_completion` gets the default contract ("skip + queue nothing + show degraded"), and a lint test asserts every `use_case=` call site maps to a registered contract — the mechanism that keeps FUTURE surfaces honest.

### 5.3 Visible degraded indicator

- `GET /api/resilience/degraded` → `[{surface, available, floor, backlog}]`.
- FE: a compact **degraded chip** in the shell TopBar area when any contract is down (count + worst surface), expanding to a popover listing each degraded surface, its floor statement, and its pending-enrichment backlog size; per-surface pages (knowledge, inbox) render an inline one-line banner on their own surface only. Rendered from the same poll slice DashboardLive already runs (a new key beside `api.status()`), not a new socket.
- Notification on transition (down AND recovered-with-drain-summary: "embedding provider back — 214 items re-enriched") through the existing `DashboardState.notify` gate — severity `warning` on down, `info` on recovery, so quiet hours behave correctly.

---

## 6. Mid-Turn Message Handling — Prompt Queue + Cancel-and-Replace

### 6.1 Declared per-channel policy

```
mid_turn_policy: queue (default) | cancel_and_replace
```

- **Platform default** in config (§7 wiring). **Per-channel override**: for app channel transports (slack-channel) a `mid_turn_policy` field in the app's `settingsSchema`/ProviderSettings (`~/.personalclaw/apps/{name}/data/config.json` — the same file the Configure form writes); for the webui channel, a Chat settings field. Resolution precedence: per-channel setting > platform default — matching the explicit > binding > default chain the platform already uses.
- **`queue` (today's behavior, formalized):** follow-up messages enqueue and deliver next turn. This EXISTS — `SessionManager.enqueue` (channel threads), dashboard `_queue` + `queue_push/pop/cancel` WS events, `dashboard.merge_queued_messages` coalescing — and is kept as the default; the plan declares it rather than rebuilds it. Mid-turn *steering* (`add_steer`, #37) is unchanged and orthogonal (steer = inject into the CURRENT turn; queue = next turn).
- **`cancel_and_replace` (new, opt-in per channel):** a rapid follow-up to the same channel cancels the in-flight generation and starts fresh with the new message — preventing stale ghost responses and wasted compute (the LocalAGI conversation-scoped cancellation).

### 6.2 The per-channel active-job tracker

A small formalization of what is currently an internal semaphore: `resilience/active_jobs.py` — `ActiveJobTracker` mapping session key → `ActiveJob{job_id, origin (webui|slack:<chan>|cron|loop|subagent), started_at, cancel_scope}`. Registered at turn start / cleared at turn end in `chat_runner` (the same places `notify_turn_complete` already fires) and in the channel inbound path. Consumers: (a) the cancel-and-replace decision, (b) channels wanting a typing/busy signal, (c) the Doctor ("3 sessions mid-turn"), (d) the FE queue indicator. **This is bookkeeping over existing state (`session.semaphore.locked()` + running task), not a scheduler.**

### 6.3 Cancel-and-replace mechanics (cancellation propagation)

On inbound message to a busy session whose resolved policy is `cancel_and_replace`:

1. **Eligibility guard:** cancel ONLY when the in-flight job's `origin` is the same interactive channel as the new message. Loop workers (`loop-*`), cron sessions (`cron:*`), subagents, and heartbeat `_bg` are NEVER cancel-and-replace targets — a user message landing on a busy loop session queues regardless of policy. (Autonudge's deliberate drop-when-mid-turn also stays as-is, per the automations recon — nudges are not user messages.)
2. **Cancel via the existing verb:** `stop_turn(key, force=False, preserve_queue=True)` (`session.py:1529`) — soft cancel with the kill-fallback + eager-respawn ladder already built; `preserve_queue=True` so previously queued items survive. The existing `prev_turn_cancelled` re-inject contract handles the ACP discard behavior.
3. **Propagation to the streaming client:** the superseded turn's stream is closed with a terminal `chat_status` frame carrying `superseded: true` + the superseding message id; the FE marks the partial answer bubble "superseded" (dimmed, collapsible) instead of leaving a ghost half-response. Channel transports get the same signal through the tracker so Slack can edit/annotate the partial message (`ChannelCapabilities.edits` permitting).
4. **Deliver the new message** as a normal turn.
5. **Debounce guard:** a per-channel `cancel_replace_min_interval` (default 2s) so a burst of N rapid messages produces ONE cancel + the last message (intermediate ones merge via the existing `merge_queued_messages` path), not N cancels.

### 6.4 FE affordances

The composer already shows queued items with per-item cancel (`queue_push/pop/cancel`). Added: a small "will replace the current answer" hint when the active session's policy is cancel_and_replace and a turn is in flight (read from the tracker via the session snapshot the chat handlers already return `queue` in); the superseded-bubble treatment (§6.3.3). New WS behavior rides the EXISTING event types plus one new field — no new envelope type, honoring the FE recon's "WS envelopes are refetch signals / consumers string-match inline" reality.

---

## 6.5 Structured Crash Capture — Session-State Dump on Unhandled Failure (grok-build learning, 2026-07-17)

grok-build ships a dedicated `xai-crash-handler` crate: unhandled failures produce a structured, recoverable artifact instead of a stack trace in a log nobody reads. PersonalClaw catches exceptions defensively throughout, but a gateway-level unhandled crash (or a turn that dies mid-stream) leaves only scattered log lines — no single artifact that says "here's exactly what was happening."

- **Capture:** a top-level exception hook (gateway lifecycle + per-turn chat runner boundary + loop worker boundary) writes `~/.personalclaw/crashes/<ts>-<kind>.json` on unhandled failure: `{ts, kind: gateway|turn|loop_worker, exception (type/message/traceback), session_key, last_n_turns: 5 (content digests, not full text), in_flight_tool: {name, args_clipped}, active_model, config_digest, versions, uptime}`. Redaction pass (`redact()`) before write — no credentials in crash files. Atomic write; directory capped at 20 files (oldest pruned).
- **Surfacing:** Doctor (§1) gains a `crashes` probe — recent crash files render as a Doctor card with the crash kind, when, and what session; one click opens the full JSON. A gateway restart after a crash shows a one-time notification ("PersonalClaw recovered from a crash — see Doctor") instead of silently coming back.
- **Recovery hook:** for `kind=turn` crashes, the crash file carries enough (session_key + last turns) that the session is resumable — the notification offers "resume that chat". For `kind=loop_worker`, the existing restart-reap path picks up; the crash file adds the WHY that reap currently lacks.
- **Not telemetry:** crash files never leave the machine. No upload, no aggregation service. They exist for the user (and for the agent-run Doctor diagnosis) only.
- **Scope note (+~half session, folds into Session 4):** the hook + store + redaction is small; the Doctor card and notification ride Session 4's fixes/simulators surface work.

---

## 7. Data Model & Stores

| Store | File (`~/.personalclaw/`) | Format | Notes |
|---|---|---|---|
| Doctor results cache | in-process (30s TTL) | — | probes re-run cheaply; nothing persisted per-run |
| Remediation ledger | `doctor/remediation.jsonl` | JSONL | trim at 2× cap (notifications.jsonl pattern) |
| Remediation job state | `doctor/jobs.json` | JSON `{job_id: {last_success_ts, content_hash}}` | atomic_write; success-only timestamps |
| Degraded-mode state | in-process registry | — | derived from probes + `can_resolve_use_case`; recomputable, so never persisted |
| Pending-enrichment queues | **each surface's own store** | — | knowledge: `extraction: heuristic` stamps (KNOW-R17); memory: LEARN-R19 staging log flags; inbox: `enrichment: pending` on items in `inbox.json`. Deliberately NO central queue store — the stamp lives with the data it describes |
| Resilience config | `config.json` → `resilience` section | `ResilienceConfig` dataclass | four wiring points (§8) |
| Per-channel mid-turn override | app `data/config.json` (ProviderSettings) | existing per-extension settings file | survives app updates |

`ResilienceConfig` (new top-level section beside `SecurityConfig`): `doctor_enabled`, `remediation` (`target_score`, `max_cost_usd`, `idle_minutes_healthy`, `tick_minutes_degraded`), `degraded_indicator: bool`, `mid_turn_policy` (platform default) + `cancel_replace_min_interval_secs`.

**Memory vs Knowledge boundary:** everything here is harness mechanics — doctor files under `~/.personalclaw/doctor/`, stamps in stores that already exist. Nothing writes memory entries to `memory.db` or items to `knowledge.db`; the knowledge-side heuristic stamps are KNOW-R17's own approved schema. Lessons drawn from doctor findings ("this provider flaps nightly") belong to LEARNING-FLYWHEEL and stay propose-don't-write.

---

## 8. Provider-Fidelity Wiring (where each piece plugs in)

- **No new provider TYPE.** Doctor/degraded/mid-turn are substrate — the same deliberate stance as "no space provider type" (`providers/registry.py:555`). Probe packs derive from EXISTING registry surfaces: `channel_transports` registry → `health()/test()`, `local_models/registry.py` (keyed by APP name — probes must use `ext.name`, the documented alias gotcha), the apps watchdog, `llm` registry catalogs. An app that ships a channel/local-model provider inherits Doctor coverage with zero manifest changes because the probes consume the ABCs it already implements. An OPTIONAL duck-typed `selftest()` on providers can later enrich §1.4; absence falls back to capability-generic dispatch.
- **Action providers:** this plan adds NONE. If a future session exposes remediation as a `run-doctor` action provider (so triggers can fire it), it MUST be added to `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`) or hook create/update rejects it — restated because §4's engine is where that provider would be born.
- **Config:** every `ResilienceConfig` field wired through the FOUR points — (a) dataclass fields with `_meta(label, help)` (schema reachability tests enforce), (b) `AppConfig.load()` explicit field-by-field mapping (`loader.py:1638+` — omission = silent drop), (c) `to_dict()` (new top-level `resilience` section at `loader.py:1930`), (d) `_EDITABLE_CONFIG` (`dashboard/handlers/core.py:363`) + FE for the runtime-editable subset (`mid_turn_policy`, `remediation.target_score`, `remediation.max_cost_usd`, `degraded_indicator`).
- **Per-channel settings:** ride the existing `ProviderSettings` file (`providers/settings.py`) + `settingsSchema` in the channel app's manifest — the same seam the Configure UI writes; no new settings machinery.
- **Guard flags:** `doctor_enabled` and `degraded_indicator` are guard-class — they parse per the AUTONOMY-GUARDRAILS §5 fail-safe tenet (missing/unknown ⇒ enabled; safe dataclass defaults).
- **SEL:** every applied fix (§2), remediation run summary (§4), and cancel-and-replace hard-kill escalation logs to `sel.py:SecurityEventLog`.
- **Snapshot/portability:** `doctor/` files are small JSON/JSONL and join the snapshot set; noted honestly — snapshot coverage is already partial (persistence recon gotcha 10) and this plan does not claim to fix that.
- **FE:** Doctor tab via the Settings SUBPAGES registry; degraded chip in the shell; new API methods land in `lib/api.ts` (one flat file — high merge-conflict surface, so this plan's endpoints ship in ONE api.ts patch per session); no new WS envelope types (§6.4).

---

## 9. Disposition & Dependency Notes

| Adjacent approved work | Relationship |
|---|---|
| **AUTONOMY-GUARDRAILS** §2.5 provider health view, §1.1 SpendMeter, §1.3 incident switch | Doctor COMPOSES the health view (renders `/api/models/health`, never rebuilds the audit); §4 engine CONSUMES SpendMeter + `model_calls.jsonl` cost rows; incident ≠ doctor — incident stops unattended work, doctor diagnoses; the Doctor shows incident state as a banner |
| **WORKFLOWS-V2-AUTOMATION-SUBSTRATE** AUTO-R15 (automation doctor, dry-fire, health-scored-trigger option), storm-guard fields | §1.2 mounts the automation check set as one pack (owned there); §3.3 renders its dry-run output; §4 IS the health-scored maintenance trigger that plan left optional; cooldown/idempotency fields shared |
| **WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS** KNOW-R17 (heuristic floor), degradation ladder (§2.2 there) | The knowledge instance of §5 — registered, not re-specified. The generalized contract is this plan's remainder |
| **WORKFLOWS-V2-LEARNING-FLYWHEEL** LEARN-R4/R7/R14/R15 (surfacing scoring/observability), LEARN-R19 (staging outcomes + cost metering) | §3.1 simulator is a dry-run explain UI over their scorer; §3.2 probes READ LEARN-R19 records; both degrade gracefully pre-flywheel |
| **SELF-VERIFICATION / EVALUATION-SUBSTRATE** | No overlap — those verify *changes/templates*; this plan verifies the *running system* |

Sequencing: §1-§3 + §5 + §6 have no hard dependencies (Wave 0/1); §4 after AUTONOMY-GUARDRAILS Session 2 (SpendMeter) and ideally with AUTOMATION-SUBSTRATE's trigger store for the adaptive-cadence trigger form.

---

## 10. Implementation Effort

**~5 sessions.**

- **Session 1 — Doctor core (§1):** `resilience/doctor.py` framework + tier ladder + the memory/channels/local-models/apps/serving-fs probe packs; `GET /api/doctor`; Settings Doctor tab (read-only); doctrine invariants as tests (capability failure never marks core unhealthy).
- **Session 2 — degraded contract (§5):** `resilience/degraded.py` registry + `can_resolve_use_case` re-probe wiring; the seven initial contracts (registering KNOW-R17, formalizing the inbox/diarization floors, search-ranking vector-off ladder, memory-extraction skip+stage); pending-enrichment stamps + drain hooks; `GET /api/resilience/degraded` + FE chip/banners + transition notifications; the "every `use_case=` call site maps to a contract" lint test.
- **Session 3 — mid-turn handling (§6):** `ActiveJobTracker`; `mid_turn_policy` resolution (config four-point wiring + per-channel ProviderSettings field); cancel-and-replace via `stop_turn(preserve_queue=True)` with eligibility guard + debounce; `superseded` propagation to FE bubble + channel edit; as-a-user validation on webui + slack.
- **Session 4 — fixes + simulators + crash capture (§2, §3, §6.5):** confirm-gated Fix catalog (symlink repair, cache cleanup, orphan pruning, manifest resync) with SEL audit; per-provider selftest endpoint; surfacing simulator (`explain=True` on the scorer + panel); memory-pipeline probe pack (current-seam version); automation would-execute rendering; structured crash capture (exception hooks at gateway/turn/loop-worker boundaries, redacted crash files, Doctor card + recovery notification — grok-build learning).
- **Session 5 — remediation engine (§4, Wave 3):** deficit scoring with `max_reachable_score` ceilings; dependency-ordered plan executor under target-score/max-USD caps charging SpendMeter; absorb heartbeat/inbox maintenance jobs + schedule `verify_skill_integrity`; adaptive cadence (trigger form if AUTOMATION-SUBSTRATE has landed, heartbeat job otherwise); remediation ledger + Doctor rendering; drains re-homed onto the engine's judgment lane.

Sessions 1-4 each ship independently; Session 1 alone is a Wave-0 win (the symlink/ST-detection bug-classes become one click to diagnose).

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| Probe suite itself becomes a load source (probing every capability on a poll) | Tier short-circuiting; 30s result cache; tier-3 packs run only on Doctor open/explicit run, not on the dashboard poll (the dashboard gets the cached rollup) |
| Auto-fix does damage (the L6-model deletion class) | Nothing auto-applies; confirm-gated + `dry_preview()` + SEL audit; fixes touch harness mechanics only, never user content; destructive tests for fixes isolate to `tmp_path` per the documented bug-class |
| Remediation engine = a new complexity center (ClawX deleted their breaker machinery for a dumb cooldown) | The engine is a plan-executor over declared jobs, not a policy brain: deficit inputs are measured counts, ordering is declared `after:` edges, stopping is three plain caps; per-job cooldowns are the "dumb cooldown"; if it misbehaves, disabling it restores today's heartbeat jobs (kept callable) |
| Degraded floors mask real outages (user never notices the model is gone) | The visible chip + down/recovery notifications are part of the CONTRACT, not optional polish; chat deliberately refuses to fake it |
| Drain storms when a provider returns (214 items re-enrich at once) | Drains are budgeted remediation jobs (§4 lane) with cost caps; pre-engine, drains batch with a fixed chunk size + inter-batch sleep |
| Cancel-and-replace kills work the user wanted (message was an addendum, not a replacement) | Opt-in per channel, default `queue`; eligibility guard restricts to same-origin interactive turns; soft-cancel first (ACP ack path); superseded partials remain visible (dimmed), never deleted |
| Ghost cancellation races (new message arrives as the turn finishes) | The tracker consults `semaphore.locked()` at decision time and `stop_turn` returns `idle` harmlessly when the turn already ended — the existing verb is race-tolerant |
| Silent config drop (four-wiring-points gotcha) | Explicit checklist §8; schema reachability tests enforce `_meta`; guard-class defaults tested per AUTONOMY-GUARDRAILS §5 |
| api.ts merge conflicts (one flat 2000-line file) | One consolidated api.ts patch per session, coordinated with co-tenant sessions (explicit-path staging per the co-tenant note) |

---

## Success Criteria

1. With the gateway healthy but ollama dead and the HF cache wiped, `GET /api/doctor` reports core OK, `local-models` failed at tier 3 with per-model layout evidence — and the gateway is NOT flagged for restart (doctrine test).
2. Replacing the `static/dist` symlink with a copy is detected by the serving/fs probe, and the confirm-gated fix restores the symlink (shadow copy backed up), SEL-audited — the stale-SPA bug-class is one click to diagnose and repair.
3. Pull the network + stop all local model providers: knowledge ingest files heuristic-stamped entries, inbox still raises keyword alerts, memory capture stages transcripts, search returns FTS-ranked results, and the shell shows a degraded chip listing each surface with backlog counts. Re-enable a provider: queues drain automatically and a recovery notification summarizes what was re-enriched. Zero error walls anywhere in the flow.
4. The surfacing simulator, given a query that should have matched a known skill, shows the per-signal breakdown identifying WHY it was excluded (e.g. semantic 0.51 < 0.55, no keyword hit) — with zero LLM calls; the same panel answers "what would this trigger do right now?" for any automation without touching any wired system.
5. A week of `FLUSH_OK`-only memory-pipeline outcomes on an active system renders as a WARN on the Doctor's memory-pipeline row (the S05 silent-death class is structurally visible).
6. One remediation run on a deliberately-degraded store (stale FTS, 50 heuristic-stamped items, dead locks) executes a dependency-ordered plan, stops at `max_cost_usd`, raises the health score, writes a ledger row — and the old heartbeat maintenance jobs no longer run independently.
7. On a channel with `cancel_and_replace`, sending a rapid follow-up mid-generation cancels the stale turn (soft-cancel ack observed), marks the partial answer superseded in the UI, and answers the new message; the same follow-up on a `queue` channel queues and delivers next turn; a user message landing on a busy loop-worker session queues regardless of policy.
8. Every new config field round-trips through load → to_dict → PATCH → FE toggle → config.json inspection (the four-wiring-points as-a-user check).

---

## Execution log

- [2026-07-25][S1] DONE: Doctor core (§1). New `resilience/` package — `resilience/doctor.py`
  (Tier ladder 0-3, `Probe`/`ProbeResult` frozen dataclasses, flat registry, `run_doctor`
  with downward short-circuit + the "capability-degraded is never core failure" doctrine,
  `run_capability` for the single-card re-probe; read-only-by-contract — every probe
  exception becomes `ok=False`, never a 500; secrets redacted from `detail`/`evidence`).
  Probe packs: memory (db open + WAL + integrity + faiss↔embedded consistency via a
  short-lived RO sqlite connection), channels (registry `health()`, no `bind_state` side
  effect), local-models (per-provider `is_available()` + phantom-binding detection scoped
  to registered-local providers only), apps (backend-supervisor liveness + `.rollback`
  leftovers), serving-fs (static/dist symlink-vs-copy detection replicated read-only from
  `frontend.py` + dead lock/PID counts), model-providers (COMPOSES guardrails
  `provider_health()`, never rebuilds it). HTTP: `dashboard/handlers/doctor.py` +
  `GET /api/doctor` (cached 30s) + `GET /api/doctor/{capability}`. FE: read-only Doctor tab
  (`web/src/pages/settings/DoctorPanel.tsx`, grouped capability cards + tier-failed badges +
  `<details>` evidence disclosure), `api.doctor()`/`doctorCapability()` + `DoctorReport`
  types, `DashboardLive` `doctor` slice (SLOW_POLL), and a SystemHealth one-line rollup
  (surfaces only when unhealthy, links to the tab). Tests: `tests/test_resilience_doctor.py`
  (13 — doctrine invariants: capability-fail-never-core, cheap-RPC-fail short-circuits +
  suggests restart, socket-fail short-circuits without restart, probe-raise→ok=false,
  secret masking, single-capability re-probe, + real memory/serving-fs probe packs against
  isolated `tmp_path`). Success-criterion #1 (healthy core + dead capability → core OK, no
  restart) and #2's DETECTION half (copy-shadowed static/dist flagged) validated live +
  in unit tests. DoD green: `make lint` (black/isort/flake8/mypy), `make test`
  (7911 passed / 0 failed), web typecheck + vitest 231 (ratchet held at rawButton=278 — new
  disclosure uses native `<details>`, the rollup uses the `RowAction` primitive) + build.
  Reference regenerated (`python -m personalclaw.manifest_reference` — both doctor routes
  present). Clean break under the pre-1.0 banner: no persisted-state migration (Doctor
  results are an in-process 30s cache; nothing durable added this session).
- [2026-07-25][S1] DISCOVERY (plan citation drift — NOT a premise mismatch; every seam
  exists functionally, only line numbers moved): `channel_transports/base.py` health/test are
  at :135-155 (plan says 69/70); `can_resolve_use_case` is at `provider_bridge.py:730` (plan
  says :672); `knowledge.db` lives under `config_dir()/workspace/knowledge/` (not directly in
  `config_dir()`); the status/system routes register at `server.py:370-371` and their handlers
  live in `handlers_system.py`, not `handlers/core.py`. Left the plan body as-is (it's a
  design doc); recording here so a later session doesn't chase the stale lines.
- [2026-07-25][S1] DISCOVERY (deferred to owning plans, NOT built here): (a) the HF
  `models--…` on-disk cache-layout probe has no shared helper — `local_models/layouts.py` is
  unbuilt and owned by LOCAL-MODEL-MANAGER-V2; the local-models pack therefore uses
  provider-computed `is_available()`/catalog membership, not a raw disk scan (E6 scope
  discipline). (b) installed-copy-vs-repo manifest DRIFT has no stored hash to diff (no
  checksum in `installed.json`), so the apps pack probes backend liveness + `.rollback`
  leftovers — the real signals available today — and drift detection waits for a plan that
  records a manifest checksum.
- [2026-07-25][S1] DISCOVERY (`redact()` coverage): the Doctor correctly WIRES `security.redact`
  over all probe output, but `redact()` targets AWS-key/exfiltration-URL shapes — it does NOT
  mask anthropic `sk-ant-…` keys. The masking test asserts against an AWS-key-shaped token (a
  pattern `redact()` genuinely catches). Broadening `redact()`'s credential coverage is
  security.py's contract, out of scope for this session; flagged for SECURITY-HARDENING.
- [2026-07-25][S1] DEVIATION: capability key `serving/fs` → `serving-fs` (URL-safe slug). The
  aiohttp `{capability}` path param can't match an embedded slash, so `GET /api/doctor/serving/fs`
  was unreachable. The plan's "serving/fs" is treated as a display label (the FE prettifies the
  slug); every capability is now addressable via the per-capability route. Validated live.

- [2026-07-25][S2] DONE: No-model degraded contract (§5). New `resilience/degraded.py` —
  `DegradedContract{surface, use_cases, floor, backlog_probe, drain}` frozen dataclass +
  a module registry (`register_contract`/`all_contracts`/`get_contract`), `evaluate()`
  deriving each surface's availability from `all(can_resolve_use_case(uc))` (cheap,
  no-instantiate) with read-only fail-safe backlog probes, and one-notification-per-
  transition (silent baseline on first sight → `warning` on down → `info` on recovery,
  via the live `DashboardState.notify` gate). Seven contracts registered for the floors
  that EXIST today — chat (honestly unavailable), inbox_enrichment (real backlog =
  un-classified pending items), memory_extraction, knowledge_ingest, search_ranking (real
  backlog = `count_items_missing_embedding`), transcription, assistant_reasoning
  (catch-all for the reasoning axis behind `one_shot_completion`). `GET /api/resilience/
  degraded` (handler folded into `dashboard/handlers/doctor.py`). `ResilienceConfig` wired
  5-point (dataclass + `_meta`, `AppConfig` field, `load()` via a local fail-safe
  `_guard_flag`, `to_dict()`, `_EDITABLE_CONFIG`) with two guard-class switches
  (`doctor_enabled`, `degraded_indicator`, default True); the S1 Doctor endpoints are now
  gated on `doctor_enabled`. FE: a self-polling shell chip `web/src/ui/DegradedChip.tsx`
  (+ `.doc.ts`) mounted in `ShellCornerRight` beside SystemWidget — warn-toned pill +
  popover (surface / floor / backlog); `api.degraded()` + `DegradedReport`/`DegradedSurface`
  types. Tests: `test_resilience_degraded.py` (14 — registry, availability-all-must-resolve,
  probe-fault-fails-available, backlog-fail-safe, transition warning→info, silent baseline)
  + `test_resilience_degraded_lint.py` (3 — the honesty ratchet: every `one_shot_completion`
  call-site file maps to a registered contract surface, no stale/unmapped entries) +
  `resilience` added to `test_config_roundtrip._SECTIONS`. DoD green: `make lint`,
  `make test` (7925 passed / 0 failed), web typecheck + vitest 231 (ratchet held —
  DegradedChip lives in exempt `ui/`, documented per the ui-docs drift guard) + build.
  Reference regenerated. Clean break under the pre-1.0 banner — the only durable state is
  two config booleans (round-trip-tested); the degraded registry is derived/recomputable,
  never persisted (§7). Validated live on the (binding-less) dev instance: all 7 surfaces
  report degraded with floors, both config gates flip the surface on/off, Doctor unregressed.
- [2026-07-25][S2] DEVIATION: §5.3 says the degraded chip rides "the same poll slice
  DashboardLive already runs." Verified FALSE for a shell element — `DashboardLiveProvider`
  wraps only `DashboardPage`, not the app shell, and `useDashboardLive()` throws outside it.
  The chip instead SELF-POLLS via `useVisiblePoll(20s)`, matching the established
  `IncidentBanner`/`SystemWidget` shell-element pattern. (A `DashboardLive` `degraded` slice
  would still be correct for a dashboard-page widget, but the shell chip can't use it.)
- [2026-07-25][S2] DISCOVERY (deferred to owning plans — floors that do NOT exist in code
  today, so their contracts describe the CURRENT floor with backlog=0 rather than the plan's
  future floor): LEARN-R19's memory-staging log, KNOW-R17's heuristic knowledge extractor,
  and the synthesis-watcher `append_evidence` mode are all unbuilt Workflows-v2 infra. Per
  E6 they were NOT built here — memory_extraction/knowledge_ingest register their *real*
  present-day skip-and-continue floors, the synthesis surface is not registered at all
  (nothing to declare against), and every contract's `drain` is `None` (drains are §4
  remediation-engine jobs). The lint ratchet will force each future surface to declare a
  contract as it lands.
- [2026-07-25][S2] DISCOVERY (plan citation drift, non-blocking): `evaluate_alert` is at
  `inbox.py:266` (plan says :270); `can_resolve_use_case` at `provider_bridge.py:730` (plan
  says :672); the diarization floor comment is `use_cases.py:49-52` (plan says :47);
  `DashboardState.notify` is at `state.py:1061` (plan says ~:1027). Seams all exist; only the
  line numbers moved.

- [2026-07-25][S3] DONE: Mid-turn message handling (§6). New `resilience/active_jobs.py` —
  `ActiveJob{job_id, origin, started_at}` + `ActiveJobTracker` (register at turn start /
  clear at turn end, both wired into `chat_runner._run_chat` at the semaphore-acquire and
  the `finally` boundary where `notify_turn_complete` fires), plus `classify_origin` and
  `is_cancellable_origin` (webui/channel = interactive+cancellable; loop-/cron:/subagent:/
  _bg = never). `mid_turn_policy` (enum queue|cancel_and_replace, default queue) +
  `cancel_replace_min_interval_secs` (2.0) added to `ResilienceConfig`, wired 5-point (+ the
  enum in `test_config_roundtrip._SPECIAL`). Cancel-and-replace lives in
  `chat_handlers._maybe_cancel_and_replace`: on a mid-turn follow-up to an interactive
  session under the cancel_and_replace policy (past the debounce window), it soft-cancels
  via the EXISTING `stop_turn(preserve_queue=True)` verb, broadcasts `chat_done
  {superseded:true, superseded_by:qid}`, and queues the new message so the EXISTING
  turn-end queue-drain delivers it as the next turn (§6.3.4 — no new dispatch path). FE:
  the `chat_done` handler consumes `superseded` with a brief "Superseded by your new
  message…" status. Tests: `test_resilience_active_jobs.py` (19 — origin classification,
  interactive/cancellable predicate, register/clear lifecycle, per-session debounce
  window, config policy default/invalid-fallback/honored). DoD green: `make lint`,
  `make test` (7944 passed / 0 failed), web typecheck + vitest 231 + build. Clean break —
  the only durable state is two config fields (round-trip-tested); the tracker is in-memory.
  Validated live: the resilience config carries the mid-turn fields, the enum PATCH
  round-trips (queue↔cancel_and_replace), an invalid value is rejected 400.
- [2026-07-25][S3] DEVIATION (implementation, not behavior): the plan describes cancel-and-
  replace as "cancel + deliver the new message as a normal turn." Rather than add a second
  dispatch path, the new message is QUEUED after the cancel and delivered by the existing
  turn-end queue-drain (`chat_runner` finally → `_dequeue_next_message` → re-dispatch). Same
  observable result (stale turn dies, new message answers next), zero new hot-path code.
- [2026-07-25][S3] DEVIATION (scope): the plan's FE §6.4 also asks for a composer "will
  replace the current answer" hint and a dimmed/collapsible superseded partial bubble. The
  functional behavior (cancel + replace + no ghost) is complete and the `superseded` signal
  is consumed as a status line; the composer policy-hint and per-turn bubble dimming are
  DEFERRED as polish — both require threading resilience config into the composer and
  per-turn superseded state through the streaming render path, a change disproportionate to
  the value and risky on the streaming hot path. Recorded honestly, not silently dropped.
- [2026-07-25][S3] DISCOVERY (dead-code finding, not fixed): `SessionManager.enqueue`/
  `dequeue`/`cancel_queued`/`is_cancelled`/`clear_queue` (session.py) have NO production
  callers — only tests. The live dashboard queue is `_ChatSession._queue` +
  `enqueue_or_run_prompt` + `_dequeue_next_message`. The plan's "`SessionManager.enqueue`
  serializes channel threads" overstates current reality; S3 targets the live path. The dead
  methods are left as-is (out of scope; removing them is a separate cleanup).
- [2026-07-25][S3] DISCOVERY (plan citation drift): `stop_turn` is session.py:1581 (plan
  :1529); the queue WS events are chat_handlers ~:172/:981 (plan :162/915); `chat_status` is
  emitted from exactly one line (chat_runner:1494) and the turn-terminal frame is `chat_done`
  (chat_runner:2833) — `superseded` was added there. Seams exist; line numbers moved.

- [2026-07-25][S4] DONE: Fixes + simulators + crash capture (§2, §3.1, §3.2, §6.5). New
  `resilience/fixes.py` — `Fix{id, title, impact, dry_preview(), apply()}` registry + 3
  confirm-gated fixes (serving-fs.symlink-repair — replicates frontend.py's rmtree+symlink
  with a shadow-copy backup since ensure_dev_dist_symlink early-returns on a valid copy;
  serving-fs.orphan-prune — stale locks + recover_interrupted_updates; model-providers.
  prune-bindings — persists load_active_models's removed-provider pruning). `apply_fix`
  runs under a SEL audit, exception-safe. The serving-fs probe now attaches the matching
  `fix_id`. New `resilience/crashes.py` — `record_crash` writes a redacted, capped
  (20-file) `~/.personalclaw/crashes/<ts>-<kind>.json`; `recent_crashes`/`read_crash`
  (traversal-guarded)/`crash_count` read them; a loop exception-handler installed in
  `GatewayOrchestrator.run` captures unhandled turn/loop-worker exceptions and chains to
  the default handler. `surface_skills` gains an `explain=True` path (with @overload) that
  returns per-candidate arm scores + inclusion/exclusion reason — the §3.1 simulator, zero
  LLM calls. Two new Doctor probes: `crashes` (recent artifacts → WARN) and
  `memory-pipeline` (current-seam presence check). Endpoints: GET /api/doctor/fixes,
  POST /api/doctor/fix/{fix_id} (confirm-gated), POST /api/doctor/simulate/surfacing,
  POST /api/model-providers/{name}/selftest (real per-capability inference, user-click),
  GET /api/doctor/crash/{filename} — the two GET sub-paths registered BEFORE the
  {capability} catch-all (aiohttp registration-order shadowing). FE: DoctorPanel probe rows
  render a confirm-gated Fix button (armed dialog → apply → re-run); api methods + types.
  Tests: `test_resilience_fixes_crashes.py` (13 — fix registry/preview/apply+backup, crash
  roundtrip/redaction/cap/traversal-guard, explain-mode breakdown/veto/opt-in) +
  `dashboard/handlers/doctor.py` added to the degraded-lint call-site map (the honesty
  ratchet caught the selftest's new one_shot_completion site — expected + correct). DoD
  green: `make lint`, `make test` (7957 passed / 0 failed), web typecheck + vitest 231 +
  build. Clean break — no persisted config; crash files are capped local artifacts.
  Validated live: fixes catalog + previews, simulator (12 candidates for "help me deploy"),
  confirm-required 400, crashes/memory-pipeline probes in the report.
- [2026-07-25][S4] DEVIATION (scope, E6 — skipped as future infra): §3.3 (automation
  would-execute / dry-run rendering) is NOT built — its execution machinery
  (`automation_run(dry_run?)`, AUTO-R15, `triggers.json`) belongs to the unbuilt
  WORKFLOWS-V2-AUTOMATION-SUBSTRATE; only unrelated cron-action/subagent observe-mode
  dry-run primitives exist. Skipped entirely and noted rather than half-built against a
  contract another plan owns.
- [2026-07-25][S4] DEVIATION (scope): §3.2 memory-pipeline is the "current-seam version"
  the plan sanctions — LEARN-R19's outcome records (FLUSH_OK/FLUSH_ERROR streak WARN,
  staging backlog, per-op cost) are future flywheel infra (learning.db doesn't exist), so
  the probe reports structural presence, not the richer freshness alarm. It never false-
  alarms; the richer metrics arrive with the flywheel, same probe seam.
- [2026-07-25][S4] DISCOVERY (route-ordering hazard, fixed): aiohttp matches routes in
  registration order, so GET /api/doctor/fixes and /api/doctor/crash/{filename} had to be
  registered BEFORE the pre-existing GET /api/doctor/{capability} catch-all or they'd bind
  as a capability name. The POST fix/simulate/selftest routes don't collide (different verb).

- [2026-07-25][S5] DONE: Health-scored self-remediation engine (§4). New
  `resilience/remediation.py` — `measure_deficits()` reads only REAL counts today
  (knowledge missing-embeddings via count_items_missing_embedding, orphan stale locks,
  skill aging-due via run_aging(dry_run=True).changed); each Deficit carries a
  `max_penalty` ceiling and a `reachable` flag (an unfixable deficit — e.g. no embedder
  bound — is excluded from the score, so the engine never burns budget on futile work).
  `health_score = 100 − Σ reachable penalties`. `run_remediation()` builds a dependency-
  ordered (`after:` edges, cycle-tolerant) plan of jobs whose deficit is present +
  reachable, runs each re-checking the score, and stops at target_score / max_cost_usd /
  exhausted. Two lanes: deterministic ($0, the 3 registered jobs — orphan-prune, skill-age,
  knowledge-reindex) and judgment (model-touching, charges guardrails SpendMeter under
  run_key "doctor" — none registered yet, mechanism ready). Per-job cooldown with
  success-only timestamps (`doctor/jobs.json`) is the storm guard; every run writes
  `doctor/remediation.jsonl` (2×-cap trim). Wired onto the heartbeat as ONE adaptive-cadence
  job (`HeartbeatService._maybe_remediate`: healthy→idle_minutes_healthy, degraded→
  tick_minutes_degraded). `RemediationConfig` sub-config (enabled/target_score/max_cost_usd/
  idle/tick) nested under ResilienceConfig, wired 5-point. Endpoints: GET /api/doctor/
  remediation (score + dry-run plan + ledger), POST /api/doctor/remediation/run (confirm-
  gated, SEL-audited). FE: a Maintenance section in DoctorPanel (score, Run-now, recent
  runs). Tests: `test_resilience_remediation.py` (13 — penalty cap, reachable-exclusion,
  score clamp, dependency order + cycle tolerance, three stop conditions, cooldown skip,
  dry-run no-op, ledger). DoD green: `make lint`, `make test` (7970 passed / 0 failed), web
  typecheck + vitest 231 + build. Clean break — durable state is config + two capped local
  JSON artifacts under doctor/. Validated live: score 100/target 90 on the healthy dev-home,
  run stops "target_score already met", confirm-gate 400s without confirm, config round-trips.
- [2026-07-25][S5] DEVIATION (safe superset, not the plan's deletion): §4.4 says the engine
  ABSORBS the heartbeat maintenance jobs (FTS rebuild, prunes, skill aging move OUT of the
  heartbeat into registered jobs). To avoid destabilizing the live heartbeat, S5 ADDS the
  engine as one heartbeat job and registers its OWN deterministic jobs (orphan-prune, skill-
  age, knowledge-reindex) rather than deleting the existing per-tick maintenance — which is
  kept as the documented fallback when the engine is disabled (the plan's own risk-table
  mitigation: "disabling it restores today's heartbeat jobs, kept callable"). Fully retiring
  the duplicate heartbeat maintenance is a follow-on cleanup once the engine has soaked.
- [2026-07-25][S5] DEVIATION (scope): the engine hangs off the heartbeat, NOT the
  AUTOMATION-SUBSTRATE adaptive-clock trigger form (§4.3) — that substrate is future infra.
  The "runs-inbox learned-overnight digest" pickup (also §4.4) likewise awaits it. The
  judgment lane charges SpendMeter under run_key "doctor" (a run-key value — there is no
  "doctor" SCOPE; day/run are the only scopes), and max_cost_usd is enforced against
  run_totals("doctor").dollars since run_budget_from_config is tokens-only.

## Amendment (2026-07-26 — sibling-platform gap analysis, owner greenlight)

**What & why.** A third mid-turn policy, `steer`, joins the shipped `queue|cancel_and_replace` (§6, S3 landed 2026-07-25). Recon: steering ALREADY EXISTS as an untyped mechanism — `SessionManager.add_steer/drain_steers` (session.py:1415/:1426, `steers` deque at :161), the native loop drains it at every model boundary between tool iterations (`agents/native/runtime.py:620-643`, `_pull_steer` capped by `_MAX_STEERS_PER_TURN`), `chat_runner.py:1494` wires `set_steer_source` via `hasattr`, and `chat_handlers.py:164-176` defaults `queue_mode` to `"steer"` for the webui. What's missing is exactly what §6.1 gave queue/cancel: a **declared policy value**, ACP capability honesty (the ACP CLIs don't expose the seam — today `add_steer` returns True and the buffer silently drains into the NEXT turn), and a real FE affordance (the composer sends `queue_mode` but offers no steer control; only a redacted `activity_event` status flashes).

**Design (contract level).**
- `ResilienceConfig.mid_turn_policy` enum grows: `queue | steer | cancel_and_replace` (loader.py:1263 `_meta` enum + `_EDITABLE_CONFIG` values at handlers/core.py:459 + `test_config_roundtrip._SPECIAL`). Resolution precedence unchanged (per-channel ProviderSettings > platform default). Policy `steer` means: mid-turn interactive message → `add_steer` when the running turn is steer-capable, else fall back to queue (never drop, never cancel).
- **Capability gate:** `steer_capable(client) -> bool` = `hasattr(client, "set_steer_source")` AND the source was wired this turn — today true only for `NativeAgentRuntime`. ACP: per-dialect probing via a new `ACPDialect.supports_mid_turn_prompt: bool = False` flag (acp/dialect.py — same pattern as `supports_concurrent_sessions`; no dialect is assumed True until a live spike proves interleaved `session/prompt` mid-turn is serviced, not queued-or-clobbered). Non-capable runtime → graceful fallback to queue with the existing `queue_push` WS event, and `add_steer` gains a capability check so steers can no longer buffer against a turn that will never drain them (the current silent-leak-into-next-turn bug).
- **FE:** composer shows a "steer" send-mode affordance while `session.running` and the resolved policy/capability allow it (the API plumbing exists: `api.sendChat(..., queue_mode)`); a steered message renders as a distinct inline chip in the transcript (from the `activity_event {kind:"status", text:"Steering: …"}` already broadcast), not a normal user bubble.

**Lands in:** an added **Session 6** (the plan's 5 sessions are all DONE — extending S3 retroactively would falsify the log). Count 5 → **6 sessions**.

| ID | Task | Files | Done when |
|---|---|---|---|
| S6.1 | `steer` joins the `mid_turn_policy` enum (5-point wiring + PATCH values + roundtrip `_SPECIAL`); chat_handlers resolves policy→mode: steer→`add_steer` when capable, else queue; capability check in `add_steer` (no buffering against non-draining turns) | `config/loader.py`, `dashboard/handlers/core.py`, `dashboard/chat_handlers.py`, `session.py` | policy round-trips; steer policy on a native turn injects at the next tool boundary; same policy on a non-capable turn queues with `queue_push`; no steer buffer survives into a later turn |
| S6.2 | ACP capability gate: `ACPDialect.supports_mid_turn_prompt` (default False) + per-dialect probe note; steer path consults it before `add_steer`; fallback queue is loud (WS event), never silent | `acp/dialect.py`, `dashboard/chat_runner.py`, `dashboard/chat_handlers.py` | ACP-backed session under steer policy queues gracefully; flag flip on a fixture dialect routes to steer; zero behavior change for existing dialects |
| S6.3 | FE steer affordance: composer steer control while a turn runs (policy+capability-gated), steered messages render as distinct inline chips; as-a-user validation on webui | `web/src/pages/ChatPage.tsx`, chat composer component, `lib/api.ts` | mid-turn steer visibly lands inside the SAME answer; steered message visually distinct from queued; reduced-motion/theme checks pass |

## Execution log — Session 6 (mid-turn steering)

- [2026-07-29][S6] **DONE (S6.1 + S6.2 + S6.3).** `steer` joins `mid_turn_policy`, the
  capability gate lands, and the composer's steer button finally does what it says.

  **E1 — the amendment's central premise was WRONG, in a way that changes the fix.** It
  states the ACP failure mode is that "`add_steer` returns True and the buffer silently
  drains into the NEXT turn." It does not. `session.steers` is cleared by exactly ONE
  function (`drain_steers`), which had exactly ONE caller (the native-only lambda in
  `chat_runner`), and there was no turn-end reset anywhere. So on an ACP-backed session a
  steer was buffered and **never read** — a permanent silent drop, with the deque growing
  unbounded for the life of the process, while the HTTP caller was told
  `{"steered": true}`. (A *later native* turn on a mixed-runtime session would have
  drained the stale buffer into an unrelated answer — so the amendment's leak was
  reachable, just as a second-order effect rather than the primary one.) Fixed by keying
  `add_steer` on a wired drain source and clearing at turn end, which closes both.

  **THREE MORE DEFECTS, none in the plan, each independently fatal.** The amendment scoped
  one bug; steering was broken four ways, which is why the mechanism shipped in #37 and had
  never once worked:
  1. **The lookup key never matched.** `chat_handlers` passed the BARE `session.key` while
     `SessionManager` registers under the namespaced `dashboard:<id>`
     (`chat_runner.py:971` → `get_or_create(session_key)`). `_sessions.get(bare)` returned
     `None`, so `add_steer` returned False and every steer fell through to the queue — on
     **every runtime, native included**. The drain lambda had the same bug symmetrically.
     Proven with a standalone probe before fixing: bare key → False, namespaced → True.
  2. **The drain sat past an early return.** It lived only at step 3b, *after* the tool
     batch, but step 2 `return`s the turn when the model made no tool calls. A plain-prose
     turn — the most common shape by far — therefore ran past the drain and discarded the
     steer. Extracted to `_drain_steers_into_history()` and called at BOTH boundaries; a
     pending steer now continues the turn for one more inference instead of ending it.
     Pinned structurally by a test asserting the drain precedes the no-tool-call return.
  3. **The frontend opted out of its own feature.** `ChatPage` sent
     `queue_mode: 'followup'` unconditionally, while the button rendered
     *"Steer — send into the running turn"*. The backend already defaulted to `steer`, so
     the FE was actively overriding it. And `ChatPage.tsx`'s `activity_event` handler
     breaks on `kind === 'status'` — precisely the kind the backend's "Steering: …"
     broadcast uses — so a successful steer had no UI either. Now sends `steer`, renders
     the server's own `{steered}`/`{queued}` answer, and shows a steered strip.

  **DEVIATION from S6.2.** The plan has the steer path consult `supports_mid_turn_prompt`
  before `add_steer`. Implemented the flag and `AcpAgentProvider.steer_capable()`, but
  deliberately did **not** let a declaration alone enable buffering: `steer_drains` tracks
  a WIRED DRAIN CALLABLE, never a declared intention. ACP exposes no tool-boundary hook to
  drain at, so a dialect flipping the flag would re-create the exact silent drop this
  session fixed. A test pins that invariant. Making a capable dialect actually steer needs
  the ACP delivery path built first, and validating it needs an authenticated ACP CLI
  (owner-blocked) — recorded as the stop point rather than faked.

  **DEVIATION on the policy default.** The webui's mid-turn default was hardcoded to
  `"steer"`, so `mid_turn_policy` could not express "queue" for the dashboard at all. Now
  derived from the policy via `_default_mid_turn_mode()`, honoring the owner ruling that
  **an explicit `queue_mode` in the request still wins** — policy is a floor, not an
  override. Because the key bug made the steer branch unreachable, today's observable
  behavior (queueing) is preserved exactly for anyone who doesn't opt in.

  **Also closed:** `mid_turn_policy` had no frontend control (the 5-point config
  contract's fifth leg was unmet — the field was file-editable only). Added a Settings →
  Chat "Mid-turn messages" section with the three policies, plus an honest note that
  steering reaches only the built-in agent today.

  **Validated as a user** on an isolated dev home (port 10731, never the owner's :10000),
  against a real Bedrock-backed model: PATCH `resilience.mid_turn_policy=steer`
  round-tripped and persisted; a bogus value was rejected with all three valid values
  enumerated; a mid-flight steer returned `{"ok":true,"steered":true}` (the branch that
  was previously unreachable); and **the steer landed inside the SAME assistant message**
  — an essay-in-progress ended with the answer to the steered question appended, and a
  counting task ended with the steered keyword. Both re-run after the drain fix; before
  it, the same test returned `steered:true` and the model never saw the message, which is
  what exposed defect 2. Two transient Bedrock `InternalServerException`s occurred during
  setup and were confirmed environmental (identical requests succeeded after).

  Tests: 22 new cases in `tests/test_mid_turn_steer.py` — steering previously had **zero
  test coverage**, which is how four defects shipped in one path. All 22 verified failing
  against the pre-fix tree and passing after. Gate: `make lint` green (1032 files, mypy
  538 sources) · `make test` **8881 passed** · web typecheck + 283 vitest + build green.
  The one red test (`test_cron.py::test_is_due_spring_forward_skipped_hour`, a `croniter`
  DST-gap behavior assertion) was confirmed **pre-existing on clean `main`** by stashing
  this work and re-running — unrelated to steering, not introduced here.

  **NOT done in this session:** the ACP mid-turn delivery path (needs the seam plus an
  authenticated ACP CLI to validate) and a distinct visual chip in the transcript body —
  the steered strip renders above the composer instead, which keeps it beside the queued
  strip it must be distinguished from.

## Execution log — PR-2 (FTS5 capability guard at init)

- **PR-2 DONE.** Every FTS5-dependent module now checks `sqlite_compat.probe().fts5` ONCE at init and
  fails/degrades with a shared `FTS5_REMEDY` constant (added to `sqlite_compat.py`, `__all__`-exported,
  naming the concrete fix: the `pysqlite3-binary` wheel) — never a mid-query `no such module: fts5`
  traceback. **3 FTS5-creating modules guarded** (the plan's "6 modules" = 3 creators + 3 query-only
  consumers of `items_fts`; guarding the creators transitively covers the consumers — reconciliation
  recorded rather than inventing 3 nonexistent sites):
  - **`knowledge/store.py` → RAISE** (`KnowledgeStore.__init__`, before connect): FTS5 is ESSENTIAL —
    every knowledge search is `items_fts MATCH` with no non-FTS path, so a no-FTS5 build can't produce
    a usable store; failing at open is honest. Its lazy singleton `get_knowledge_store()` fires the
    guard once, covering `retrieval.py` + the two knowledge action providers.
  - **`memory.py` → DEGRADE** (`_fts_available = probe().fts5` at `__init__`): the markdown projection
    (preferences/projects/history) is the real job; FTS5 only powers optional `search()`. On a no-FTS5
    build it logs the remedy once and `search`/`rebuild_index`/`_index_file` no-op; read/write untouched.
  - **`session_search.py` → DEGRADE** (`_connect` returns None before connect): a disposable index whose
    documented fallback is a linear scan; readers already treat `None`/`[]` that way.
- **DISCOVERY (a PR-1 unification miss, corrected):** `session_search.py` used a **bare stdlib
  `import sqlite3`**, not the shared `sqlite_compat` driver — one of PR-1's seven bind points it missed.
  A `probe()`-based guard there would have been CATEGORY-WRONG (probe measures the compat/pysqlite3
  driver; queries ran on stdlib). Switched it to `from personalclaw.sqlite_compat import sqlite3` so the
  guarded capability and the actual query driver are the same one, and corrected a now-stale
  `except OperationalError` comment. (`vector_memory.py::_fts5_episodic_search` is named "fts5" but is a
  LIKE fallback — no virtual table, no guard needed.)
- **Dead-control hazard avoided:** the guard is a NO-OP on a normal FTS5-present build (probe().fts5 True),
  so the happy path is provably unaffected — all existing memory/knowledge/session tests pass unchanged.
  No user surface (it changes only the no-FTS5 failure mode from a mid-query crash to an init-time
  actionable message) → no CHANGELOG. **Gates:** `make lint` clean (715 files);
  `tests/test_fts5_capability_guard.py` (11: RAISE raises-with-remedy + no DB file created + happy path;
  memory degrade + happy-path finds; session connect→None-logs-once + degrade + happy path) +
  memory/knowledge/session/sqlite_compat regression (my run 178, subagent's broader run 186+35) pass.

## Execution log — PR2-11 (retire duplicate heartbeat maintenance into the engine, §4.4)

- **[2026-08-13][PR2-11] DONE for every heartbeat-driven pass; the inbox pass is a recorded
  remainder.** S5 deferred the §4.4 deletion ("fully retiring the duplicate heartbeat
  maintenance is a follow-on cleanup once the engine has soaked"). This is that cleanup —
  but the pre-work measurement contradicted the atom's premise, so it ran **register first,
  retire second**. Measured disposition before touching anything:

  | heartbeat/service pass | engine counterpart BEFORE | after PR2-11 |
  |---|---|---|
  | skill-curator aging (`heartbeat.py:149`) | `skills.age` — a true duplicate | retired from the tick |
  | memory FTS rebuild (`_FTS_REBUILD_TICKS=15`) | **none** (`knowledge.reindex-embeddings` is knowledge EMBEDDINGS, a different subsystem) | new `memory.rebuild-fts`, retired from the tick |
  | history prune (`_PRUNE_TICKS=1440`) | **none** (`serving-fs.prune-orphans` is serving-fs locks) | new `memory.prune-history`, retired from the tick |
  | SEL prune (`sel().prune()`, same tick) | **none** | new `sel.prune`, retired from the tick |
  | inbox 6h maintenance | **none** | **NOT retired** — see the remainder below |
  | `verify_skill_integrity` | unscheduled (on-demand only) | scheduled as a measured DETECTOR, not a job |

  Retiring all four at once would have silently stopped FTS reconciliation and both prunes:
  three of the four had no counterpart at all. So each was registered, driven, and proven to
  do the work BEFORE its tick invocation was removed.

- **Proof each registered job does the WORK (not merely that it ran).** Driven under an
  isolated `PERSONALCLAW_HOME` (never the real home) against a deliberately degraded store —
  criterion #6's own scenario: 1 hand-edited memory file, 3 history files 400 days old, 300
  aged SEL entries. `measure_deficits()` reported 1/3/300; `health_score` 59.0; one
  `run_remediation(target_score=90)` executed `memory.prune-history` → `memory.rebuild-fts`
  → `sel.prune` in declared order, score 59 → 100, `stopped_reason` "target_score reached",
  one ledger row. Observable after-state asserted, not inferred: the 3 files gone AND zero
  orphan FTS rows left behind, `fts_desync_count()` 0 with the index row byte-equal to disk,
  SEL 301 → 1 lines with the fresh entry surviving. A second pass was a no-op
  ("target_score already met"). Pinned as tests in `test_resilience_remediation.py`.

- **The docstring/`kept callable` contradiction — resolved as a REAL fallback, not a
  correction.** `remediation.py` promised that disabling the engine "restores today's
  heartbeat maintenance, which is kept callable", which a plain deletion would have made
  false. Chosen resolution: **exclusive ownership.** `_maybe_remediate` now returns whether
  the engine owns maintenance this tick, and the old per-tick pass moved wholesale into
  `HeartbeatService._legacy_maintenance`, which runs ONLY when the engine is disabled. One
  declared mechanism, one owner per tick, never both — so criterion #6 ("no longer runs
  independently") holds while `remediation.enabled=false` still yields full maintenance
  rather than none. Both directions are tested (`TestMaintenanceOwnership`), including the
  fail-safe: if the engine itself raises, the legacy pass takes that tick and the failure is
  logged at WARNING (three swallowed `logger.debug` maintenance failures were also raised to
  WARNING — an absent prune is invisible by nature).

- **DISCOVERY — two shipped jobs could never be scheduled (fixed here).** `orphan_locks` and
  `skill_aging_due` both declared `max_penalty=10.0`, exactly `100 − target_score` at the
  default target. `run_remediation` returns before planning anything while
  `score_before >= target_score`, so at *any* backlog magnitude those deficits scored 90.0
  and stopped with "target_score already met" (verified: count=1000 → penalty 10.0 → score
  90.0 → early return). `skills.age` — the one true duplicate this atom set out to retire —
  was therefore unschedulable by its own deficit, and retiring the heartbeat's aging pass on
  top of it would have been a silent gap. Both ceilings raised to 12.0, the rule named as
  `_MIN_SCHEDULABLE_PENALTY`, and a rail added asserting every job-bearing deficit can cross
  the default gate alone. Absorbed-maintenance weights were chosen against the same
  question: FTS desync and history-over-retention at weight 11 (one divergent file / one file
  past a retention promise must cross the gate), SEL at weight 0.05 deliberately NOT
  triggering at count 1 — the size cap is a high-rate moving target, so a count-1 trigger
  would park the score permanently below target with the job stuck in cooldown.

- **`verify_skill_integrity` is scheduled as a DETECTOR, not a job — deliberately.** The
  engine's job contract is `run()` that REDUCES a measured deficit. Verification reduces
  nothing: no job can un-tamper a skill, and re-baselining a mutated one would launder the
  tamper (the only real remediations — quarantine, forced reinstall — are new security policy,
  not this atom, and are left for the owner). So it is registered as a measured, job-less,
  `reachable=False` deficit (`skills_tampered`) evaluated on every engine pass and every
  Doctor read, surfaced on the Doctor's existing deficit list (`handlers/doctor.py:298` →
  `RemediationSection`), with the SEL audit `verify_skill_integrity` already emits. That is
  strictly more scheduling than before (it previously ran only when a human opened the Skills
  page) without pretending a detector is a fix. Only *locked* skills are hashed, so bundled
  ones cost a stat.

- **REMAINDER (why the inbox pass was left alone).** Premise correction: inbox 6h
  maintenance is **not** on the heartbeat. It runs in `InboxService._loop`
  (`inbox_service.py:198`) on its own `_MAINTENANCE_EVERY_SECS` timer, interleaved with
  polling, and `run_maintenance()` mutates the **live** `InboxStore`/`InboxState` and calls
  `check_retire_candidates(state=_dashboard_state())` (FEEDBACK-SIGNAL explicitly rides this
  cadence). The job registry is module-global with no handle on the running service, so
  honest absorption means gateway-side registration bound to `self.inbox_svc` plus a deficit
  measured off that live store; constructing a fresh store inside the engine's executor
  thread instead would fork the live store — the known live-store hazard. Deliberately not
  improvised. `PR2-11` therefore stays **todo** with this as its single outstanding scope.

- **Gates.** `make lint` clean; `tests/test_resilience_remediation.py` 19, 
  `tests/test_heartbeat_retention.py` 27, memory/SEL/doctor/session-search 183, and the
  skills/inbox/heartbeat/gateway/curator selection 881 — then the full suite. Four
  generators re-run byte-identical. No `web/` change (the new deficit rides the existing
  deficit list the panel already renders). CHANGELOG entry added — this changes WHEN
  maintenance runs, which is user-visible.
