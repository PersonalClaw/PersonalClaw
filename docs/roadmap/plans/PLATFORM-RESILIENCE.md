# Plan: Platform Resilience — Doctor, No-Model Degraded Mode, Mid-Turn Message Handling

**Status:** PROPOSED (created 2026-07-13 from research synthesis, promoted from backlog)
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
