# MODEL-ROUTING-TELEMETRY

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/MRT.md`](../atomic/MRT.md) as 5 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Model Routing & Telemetry — Pareto Views + Learned Local-vs-Cloud Routing

**Status:** PROPOSED (created 2026-07-13 from research synthesis, promoted from backlog)
**Created:** 2026-07-13
**Wave:** 3 — consumes recorded telemetry that only exists after AUTONOMY-GUARDRAILS §2 (the model-call chokepoint + attempt audit, Wave 0/1) and WORKFLOWS-V2's Run Ledger (Wave 1/2) have been landing data for a while. A learned router with no traces is a heuristic router with extra steps; Wave 3 is the first moment ≥5-sample confidence floors are actually reachable.
**Depends on:** AUTONOMY-GUARDRAILS (§2 ModelCallGuard seam, `model_calls.jsonl` attempt audit, SpendMeter dollar estimates) — hard dependency for the recording substrate; WORKFLOWS-V2 (WF2-R13 `step_completed` ledger events) + WORK-R9 (RunStats) — soft dependency, enriches outcome signals but the router functions from `model_calls.jsonl` alone.
**Feeds:** EVALUATION-SUBSTRATE (NEW-11) — routing scores and Pareto data are inputs to model bake-offs and judge tier-recommendation tables; LEARNING-FLYWHEEL — routing-policy proposals converge onto the unified proposal queue when it lands.
**Scope:** the REMAINDER of NEW-25 after the approved recording half: Pareto-frontier views over already-recorded telemetry; a TraceDrivenPolicy-style learned router choosing local vs cloud per query class; cost-aware scoring over per-provider pricing metadata; staged heuristic→learned rollout; an inspectable, user-overridable routing policy table where every learned change lands as a proposal.

---

## Research Integration (2026-07-13)

- **NEW-25** (efficiency telemetry + learned local-vs-cloud routing: latency/tokens/cost per model call, optional macOS powermetrics energy, Pareto-frontier views, TraceDrivenPolicy-style per-query-class router with 60% success + 40% feedback scoring and a ≥5-sample confidence floor, cost-aware routing over per-provider pricing metadata, heuristic local-first-with-cloud-fallback-on-timeout evolving toward learned, inspectable + per-use-case-overridable policy table) → §1–§6, all three sessions. NEW-25 carries no "Additional mechanisms" amendments.
- **Overlap honored (scoped OUT):** the *recording* half is approved elsewhere and is NOT rebuilt here. WF2-R13 (WORKFLOWS-V2 § Run Ledger) already mandates `step_completed {tokens, model, provider, cost_usd (backend-authoritative, rate-table floor), duration_secs, degraded_reason?}` per node. WORK-R9 (WORKFLOWS-V2-WORK-CONTAINERS §6.2) already delivers RunStats as a pure journal projection (`{token + cache-token splits, resolved model/agent, costUsd}`) plus template p50/p95 cost cards. AUTONOMY-GUARDRAILS §2.1 already delivers the attempt-level JSONL audit (`model_calls.jsonl`: `{use_case, provider, model, latency_ms, tokens_in/out, dollars_est, passed, failure_mode, degraded}`) at the one seam every non-interactive LLM call passes through. This plan **reads** those three stores and adds only the fields they lack (§1.2).
- **Source mechanisms:** OpenJarvis `TraceDrivenPolicy` (5 query classes; per-class model scoring 60% success-rate + 40% avg feedback; ≥5 samples before trusting; conservative online updates; `HeuristicRouter` as the pre-confidence stage) and its Pareto-frontier accuracy/latency/cost optimizer → §3–§5. AIOS `SmartRouting` (cost/performance ILP solver over live LiteLLM pricing + a ChromaDB historical query store) → **the explicit enterprise ceiling this plan does NOT build** (§ Soul + §5.3). Two independent systems converging on trace-driven local-vs-cloud routing is the confidence basis for the design.

---

## Overview

PersonalClaw is uniquely positioned for "local by default, cloud only when necessary": **six local providers already speak one `LocalModel`/`LocalModelProvider` contract** (`local_models/provider.py` — faster-whisper, piper-tts, sentence-transformers, diarization-onnx, diarization-pyannote, ollama-models), ollama already serves chat+embedding through the same use-case bindings as cloud providers, and every use-case-bound call already resolves through **one seam**: `providers/provider_bridge.py:477 resolve_provider_for_use_case`, reading `~/.personalclaw/active_models.json` (`{use_case: ["Provider:model_id", …]}` — every binding is already a *list*), with background/one-shot work funneled through the `reasoning` axis (`llm_helpers.py:275 one_shot_completion`). What is missing is not plumbing — it is (a) a way to *see* which bound model is efficient for which kind of work, and (b) a policy layer that *acts* on it.

Verified starting points:

- **Telemetry recording exists on paper, not in aggregate.** Once AUTONOMY-GUARDRAILS lands, `model_calls.jsonl` has per-attempt latency/tokens/dollars, and the WF2 Run Ledger has per-node cost/model. Neither store carries a *query class*, and nothing folds them into per-(class, model) statistics a router could consult in O(1). Pass-rate/P50-P99 "become ledger queries" (WF2-R13) — but no surface renders the local-vs-cloud trade-off.
- **Resolution today is order-of-binding, not fitness.** `resolve_provider_for_use_case` walks the active refs in stored order; the first resolvable ref wins. The **pinned-ref-raises rule** is load-bearing and MUST survive routing: an unresolvable pinned ref RAISES `ProviderResolutionError` ("block, don't silently fall back"); implicit fallback applies only when no selection exists.
- **Multi-binding is UI-gated, not store-gated.** `MULTI_ACTIVE_USE_CASES = {chat, image_modality}` (`providers/use_cases.py`) gates which use cases the ModelsPanel lets you bind >1 ref to — the store itself is lists everywhere. A router needs a candidate pool per use case; §3.1 extends multi-binding to routed use cases rather than inventing a second binding store.
- **Pricing metadata has one authoritative consumer already planned:** WF2-R13's `cost_usd` is specified "backend-authoritative, rate-table floor" and AUTONOMY-GUARDRAILS' SpendMeter needs dollar estimates — but no plan owns *where the rate table lives*. §5.1 owns it, so cost recording (approved) and cost-aware routing (this plan) read the same numbers.

**Soul guardrail:** one user, one machine, JSON files. The router is a scoring table consulted at one seam — NOT AIOS's ILP solver (PuLP optimization + vector-store query history + live pricing feeds is the enterprise ceiling explicitly not built). Learned routing changes are **proposals** the user accepts; per-use-case pins ("always local" / "always cloud") are never overridden by learning. No fleet dashboards, no telemetry service — the Pareto view is derived on request from files already on disk.

---

## 1. Telemetry Read Model (consume the approved recording, add only what's missing)

### 1.1 The three sources (all approved elsewhere — read, don't re-record)

| Source | Owner | What this plan reads from it |
|---|---|---|
| `~/.personalclaw/model_calls.jsonl` | AUTONOMY-GUARDRAILS §2.1 | per-attempt `{use_case, provider, model, latency_ms, tokens_in/out, dollars_est, passed, failure_mode, degraded}` — the primary signal; covers every non-interactive call regardless of whether it ran inside a workflow |
| WF2 Run Ledger `step_completed` / `gate_criterion` / `step_failed` | WORKFLOWS-V2 (WF2-R13) | run-level *outcome* signals: did the node whose LLM call this was ultimately pass its gate / complete without retries — the "feedback" half of scoring (§4.2) |
| RunStats projection | WORK-R9 | per-run/per-template cost aggregates — reused as-is by the Pareto view's template lens; NOT recomputed |

### 1.2 The two missing fields (small, upstream-coordinated additions)

1. **`query_class`** on the `model_calls.jsonl` attempt record. Classification happens where the call originates (§2), is threaded through the ModelCallGuard as one string field, and costs nothing when routing is disabled. This is a one-field extension to the AUTONOMY-GUARDRAILS record shape, proposed to that plan as an amendment rather than a fork of the store.
2. **`routed: {policy: "heuristic"|"learned"|"pinned"|"off", candidate_rank: int, routed_fallback: bool}`** on the same record — provenance for every routing decision, so the Pareto view and the learned scorer can distinguish "local was chosen and succeeded" from "local was chosen, timed out, cloud rescued it." `routed_fallback: true` is deliberately DISTINCT from the guardrails `degraded: true` flag: a heuristic local→cloud timeout fallback is the design working as intended, not a degraded result to be discounted.

### 1.3 Rolling stats fold — `routing_stats.json`

The router must not scan JSONL per call. A fold (incremental, updated post-attempt by the same code path that appends the audit line) maintains `~/.personalclaw/routing_stats.json` (`atomic_write`, the universal convention):

```json
{ "reasoning": { "summarize": { "ollama-models:qwen3:8b":
      { "n": 41, "success_rate": 0.93, "feedback": 0.71,
        "p50_ms": 2100, "p95_ms": 6800, "avg_cost_usd": 0.0,
        "score": 0.84, "updated_at": "…" }, … }, … } }
```

- Keyed `(use_case → query_class → "provider:model_id" ref)` — refs in exactly the `active_models.json` spelling (`split_ref` on first colon, so `gpt-oss:20b`-style ids survive).
- Conservative online updates (OpenJarvis's rule): exponential moving averages with a small alpha, so one bad night doesn't flip a policy; `n` counts total samples for the confidence floor.
- Rebuildable: a `--rebuild-routing-stats` maintenance path refolds from `model_calls.jsonl` + ledger (the JSONL is capped/rotated, so the fold is the durable long-horizon record and the JSONL is the recent forensic record — same relationship as notifications).

### 1.4 Optional energy sampling (off by default)

macOS `powermetrics` requires root and is invasive; instead of per-call energy, an **opt-in coarse sampler** (config `routing.energy_sampling`, default false) records machine-level power draw during local-provider inference windows and folds an `est_joules_per_call` column into local rows, always flagged `estimated: true`. If the sampler can't run (no permission, non-macOS), the column is absent — never fabricated. Wall-time on local providers is the honest default proxy and is always present.

### 1.5 Pareto-frontier views

- **New backend route** `GET /api/models/telemetry?use_case=&query_class=&window=` — derives, on request, per-model rows `{ref, n, success, feedback, p50/p95 latency, cost/call, est_energy?, on_frontier: bool}` from `routing_stats.json` + a bounded tail of `model_calls.jsonl`. `on_frontier` = not dominated on (quality, latency, cost) — a ~20-line dominance check over ≤ dozens of rows, not an optimizer.
- **FE:** a "Routing & Efficiency" tab inside Settings → Models, beside the AUTONOMY-GUARDRAILS §2.5 provider-health panel (`GET /api/models/health`) — same derived-not-collected philosophy, same page. Scatter (cost vs quality, latency as mark size) per use-case/class with frontier models highlighted; a table lens; and the WORK-R9 template p50/p95 cards linked, not duplicated. Reality note carried over from the guardrails plan: `capableModels` is a **frontend** function (`web/src/pages/settings/ModelsPanel.tsx:43`) — there is no backend symbol to extend; this is a new route + FE composition.

---

## 2. Query Classification (deterministic, zero-LLM)

Routing cannot spend an LLM call to route. `routing/classifier.py:classify_query(text, use_case) -> str` is a pure heuristic function (length bands, code-fence/regex signals, use_case label, structured-output request presence) mapping into a small fixed vocabulary, seeded from OpenJarvis's 5-class shape and PClaw's existing chat sub-categories (`CHAT_SUBCATEGORIES = code_tools, summarization, planning, reasoning` — `providers/use_cases.py`):

`short_chat | code | summarize | extract_structured | long_reasoning`

- Where the use case is already a specific sub-category (e.g. `summarization`), the class is largely determined by it; classification adds discrimination mainly on the broad `reasoning`/`background` axis where most `one_shot_completion` traffic lands.
- The vocabulary is a module constant, versioned in the stats file (`classifier_version`) so a future vocabulary change starts fresh buckets instead of polluting old ones.
- Unclassifiable → `short_chat` (the cheapest-model-safe default, mirroring OpenJarvis's short→smallest rule).

---

## 3. The Routing Seam & Candidate Pool

### 3.1 Candidate pool = the user's own bindings (no second store of truth)

Candidates for a routed use case are exactly the refs in `active_models.json` for that use case (falling back to `parent_capability()` refs, the existing rule). The router **never invents a ref**. To give the router a local-vs-cloud choice, the user binds both (e.g. `ollama-models:qwen3:8b` AND `anthropic:claude-…` under `reasoning`). Mechanically:

- Enabling routing for a use case (per-use-case setting, §6.2) makes that use case multi-active — the ModelsPanel binding UI treats it like the existing `MULTI_ACTIVE_USE_CASES` members. The store needs no migration (all values are already lists). The FIRST ref remains the user-visible primary/default.
- **The pinned-ref-raises rule survives intact:** the router only *reorders* the bound list per class; whichever ref it puts first is resolved through the existing `_resolve_from_config_registry(capability, provider_hint=…)` path, and an unresolvable pinned ref still RAISES — routing changes *order*, never *resolution semantics*. The guardrails chokepoint's ordered-fallback-across-bound-refs (§2.1 there) is the failure path; routing is the happy-path ordering ahead of it.

### 3.2 Hook placement (one function, one call site)

`routing/policy.py:route_refs(use_case, query_class, refs) -> list[ref]` — a pure reordering function called inside `resolve_provider_for_use_case` (`provider_bridge.py:477`) at step (2), immediately before the active-ref loop, ONLY when routing is enabled for that use case. Everything upstream and downstream is untouched:

- Step (0) native `AgentProvider` branch (interactive chat/code_tools → `NativeAgentRuntime`) — **out of scope v1**, same boundary as the guardrails chokepoint: a human is watching interactive chat. Routed capabilities are the non-interactive set: `reasoning`, `background`-mapped labels, `summarization`, `planning`, `eval_judge`, and embedding/stt/tts where multiple providers are bound.
- Step (1) explicit `model_override` ("Prov/model" or "Prov:model") — bypasses routing entirely; an explicit user/caller choice always wins.
- The `model` build-kwarg override convention (`provider_bridge.py:844`) is untouched; `route_refs` operates on refs, not on built providers.

### 3.3 Provenance

Every routed resolution stamps the §1.2 `routed` record through the ModelCallGuard so the decision is auditable per attempt. Routing decisions are NOT SEL events (they are not security-relevant); policy-table *changes* are (§6.4).

---

## 4. Staged Policy: Heuristic First, Learned Second

### 4.1 Stage 1 — `HeuristicPolicy`: local-first with cloud-fallback-on-timeout

Shipped first, useful immediately, and the permanent floor the learned stage falls back to below the confidence floor:

- If the candidate pool contains a local ref (provider registered in `local_models/registry.py` — the existing app-name-keyed registry; recall the gotcha that it keys on APP name, e.g. `ollama-models`) → order it first.
- The local attempt runs under the guardrails chokepoint's **hard timeout** with a per-use-case `routing.local_timeout_secs` (default 20s); on `timeout` / `provider_error` / breaker-OPEN, the chokepoint's fallback chain proceeds to the next (cloud) ref, and the attempt records `routed_fallback: true`.
- Class exceptions mirror OpenJarvis's static rules where they're free: `extract_structured` prefers a candidate declaring the `structured_output: json_schema` capability (ollama qualifies via its `format` parameter — capability channel per AUTONOMY-GUARDRAILS §2.4); `long_reasoning` skips local models below a size hint when the catalog exposes one.

### 4.2 Stage 2 — `LearnedPolicy` (TraceDrivenPolicy shape)

Per (use_case, query_class, ref), a score over the §1.3 fold:

```
score = 0.60 * success_rate + 0.40 * feedback
```

- **success_rate** — fraction of attempts with `passed: true` and no terminal `failure_mode` (schema_violation resolved by retry counts against the model that violated, not the one that rescued).
- **feedback** — a [0,1] composite from the signals PClaw actually has, in priority order per attempt: WF2 ledger outcome for calls inside runs (`gate_criterion` score normalized; `step_failed` → 0; clean `step_completed` with `retries=0` → 1), `eval_judge` verdict scores where the call *was* a judged artifact, and — for the few user-visible background outputs (inbox drafts, digests) — accept/edit/reject signals where those surfaces already record them. No new feedback-collection UI is built; absent feedback, the weight collapses onto success_rate (renormalized), honestly recorded as `feedback_n: 0`.
- **Confidence floor:** a (class, ref) cell participates in learned ordering only at `n ≥ 5` (config `routing.min_samples`); below floor the heuristic ordering stands. A use case flips from `heuristic` to `learned` per-class, not wholesale.
- **Conservative updates:** EMA folding (§1.3); a score change only produces a *policy change* (reordering) when it crosses a hysteresis margin (default 0.05), preventing ping-pong routing.

### 4.3 Stage 3 — cost-aware adjustment (§5), still a table, never a solver

---

## 5. Cost-Aware Routing & Pricing Metadata

### 5.1 Where pricing lives (this plan owns the rate table)

- **Per-provider pricing metadata rides the existing provider channels:** `BrandedProviderSpec` (`sdk/provider_helpers.py`) gains an optional `pricing: {model_pattern: {in_per_mtok, out_per_mtok}}` map, so branded apps (anthropic, together, groq, deepseek, mistral, google, the generic compat apps) ship defaults with the app — the same place `default_model`/`capabilities` already live. Local providers price as `0.0` (their cost axis is latency/energy).
- **User-editable overlay:** `~/.personalclaw/model_rates.json` (atomic_write) — overrides/extends app-shipped defaults (prices drift; a personal tool must let the user correct them without an app update). Effective rate = overlay > app default > absent.
- **One table, three consumers:** this router, AUTONOMY-GUARDRAILS' SpendMeter `dollars_est`, and WF2-R13's `cost_usd (rate-table floor)` all read `routing/rates.py:rate_for(provider, model)`. This closes the "who owns the rate table" gap the approved plans left open — proposed to both as the shared implementation, not a competing one.

### 5.2 Cost in the score

Cost adjusts *ordering between near-equals*, not correctness: among candidates within the hysteresis margin on `score`, prefer the cheaper (then the faster). A cloud model must beat the local candidate's score by `routing.cloud_quality_margin` (default 0.10) to be ordered ahead of free-and-private local — the "local by default, cloud only when necessary" posture as one comparison, not an objective function.

### 5.3 Explicit non-goal (the AIOS ceiling)

No ILP solver, no live pricing feeds, no vector store of historical queries, no per-request optimization under constraint systems (AIOS SmartRouting: PuLP + LiteLLM live pricing + ChromaDB query store). At personal scale the candidate pool is 2–4 refs; a scored, hysteresis-damped table is the whole mechanism. If a future need genuinely outgrows it, that is a new plan, not a flag on this one.

---

## 6. The Routing Policy Table — Inspectable, Overridable, Propose-Don't-Write

### 6.1 The store

`~/.personalclaw/routing_policy.json` (atomic_write):

```json
{ "version": 1, "classifier_version": 1,
  "use_cases": { "reasoning": {
      "mode": "learned",              // off | heuristic | learned
      "pin": null,                    // "local" | "cloud" | "<ref>" | null — user pin, never overridden by learning
      "classes": { "summarize": {
          "order": ["ollama-models:qwen3:8b", "anthropic:claude-…"],
          "basis": {"scores": {...}, "n": {...}, "decided_at": "…", "proposal_id": "…"} } } } } }
```

Every `order` carries its `basis` — the user can always see WHY the table says what it says. The Settings → Models → Routing tab renders this table read-only with the basis expanded, plus the override controls.

### 6.2 User overrides (three levers, all instant, all mightier than learning)

1. **Per-use-case mode**: off / heuristic / learned — stored in the existing per-use-case behavior store `~/.personalclaw/extensions/use_case_settings/{uc}.json` (recon: the provider-agnostic per-use-case settings seam that already exists), so routing enablement lives beside the use case's other behavior settings.
2. **Per-use-case pin**: `local` / `cloud` / an explicit ref — short-circuits `route_refs` for that use case; learned scoring continues to *accumulate* under a pin (so unpinning is informed) but never reorders.
3. **Manual reorder**: dragging the binding order in ModelsPanel writes the order as a user decision (`basis: {source: "user"}`); learning may later *propose* changing it, never silently do so.

### 6.3 Propose-don't-write: learned changes land as proposals

A learned reordering that crosses the hysteresis margin does NOT edit `routing_policy.json`. It enqueues a **routing proposal** — the `skills/proposals.py` pattern reused verbatim (per-proposal JSON under `~/.personalclaw/routing/.proposals/<id>.json`, `_MAX_PENDING`-style cap, list/accept/reject):

```json
{ "id": "…", "use_case": "reasoning", "query_class": "summarize",
  "current_order": [...], "proposed_order": [...],
  "evidence": {"n": 23, "scores": {...}, "p50_delta_ms": -1400, "cost_delta_usd": -0.002,
               "sample_audit_ids": ["…"]},
  "created_at": "…" }
```

- Surfaced in the Routing tab (badge count) and as an `info`-severity notification through the existing `DashboardState.notify` gate. Accept → the table updates with `proposal_id` in `basis`; reject → the proposal is dropped and a cooldown suppresses re-proposing the same reordering for `routing.reproposal_cooldown_days` (default 14) unless the evidence direction strengthens materially.
- One deliberate exception needs no proposal because it changes no policy: the heuristic stage's per-call timeout-fallback (§4.1) is runtime behavior inside an already-approved ordering.
- When LEARNING-FLYWHEEL's unified proposal queue lands, routing proposals migrate onto it (same record, different inbox) — noted as a convergence point, not a dependency.

### 6.4 Audit

Policy-table mutations (accept/reject/pin/mode-change) are SEL-logged (`sel.py:SecurityEventLog`) — the routing table decides which providers see which content, which IS security-relevant (a routing change can move prompts from local to cloud). The proposal evidence includes `sample_audit_ids` correlating back to `model_calls.jsonl` lines.

---

## 7. Provider-Fidelity Wiring (where each piece plugs in)

- **No new provider TYPE.** Routing is substrate at the resolution seam, same deliberate stance as guardrails ("no space provider type", `providers/registry.py:555`). Nothing registers through `_TypeHandler`s; `PROVIDER_TYPES` (manifest.py:914) is untouched.
- **No new action provider** → no `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`) change. (Rule restated per checklist: any future action provider must be added there or hook create/update rejects it.)
- **Resolution seam:** one call to `route_refs` inside `resolve_provider_for_use_case` (`provider_bridge.py:477`) step (2); `model_override` (step 1) and the native-agent branch (step 0) bypass it; the `model` build-kwarg convention (`provider_bridge.py:844`) and the pinned-ref-raises rule are preserved verbatim.
- **Local detection:** `local_models/registry.py` membership (APP-name-keyed — `ollama-models`, not `ollama`; the documented spelling gotcha) is the local/cloud classifier for candidates.
- **Pricing metadata:** rides `BrandedProviderSpec` (`sdk/provider_helpers.py`) + the `model_rates.json` overlay; consumed via `rate_for()` by this plan, SpendMeter, and WF2 cost stamping. No factory signature changes.
- **Capability channel:** the `structured_output` preference in §4.1 reads the capability exactly where AUTONOMY-GUARDRAILS §2.4 puts it (`ProviderEntry.declared_capabilities` / `BrandedProviderSpec.capabilities` / `infer_capabilities`, `llm/catalog.py:206`) — no new channel.
- **Config — the FOUR wiring points:** new top-level `RoutingConfig` section (beside `SecurityConfig`, `config/loader.py:1023`): `enabled` (master, default false), `local_timeout_secs`, `min_samples`, `weights {success, feedback}`, `hysteresis`, `cloud_quality_margin`, `energy_sampling`, `reproposal_cooldown_days`. Wired through (a) dataclass fields with `_meta(label, help)` (schema reachability tests enforce), (b) `AppConfig.load()` explicit field-by-field mapping (loader.py:1638+ — omission = silent drop), (c) `to_dict()` new section (loader.py:1930), (d) `_EDITABLE_CONFIG` (`dashboard/handlers/core.py:363`) + FE for the runtime-editable subset (timeouts, margins, min_samples, energy_sampling). Per-use-case mode/pin deliberately live in `use_case_settings/{uc}.json` + `routing_policy.json`, NOT in config.json — they are bindings-adjacent state, matching where use-case behavior already lives.
- **Stores:** `routing_stats.json`, `routing_policy.json`, `model_rates.json`, `routing/.proposals/` — all under `~/.personalclaw/`, all `atomic_write`. Snapshot/portability: small JSON, added to `snapshot.py:CORE_FILES` candidates alongside the guardrails files (noting, as that plan does, that snapshot coverage is already partial — no fuller claim made).
- **Memory vs Knowledge boundary:** untouched. Telemetry folds, policy tables, and proposals are harness mechanics (files under `~/.personalclaw/`) — nothing writes to `memory.db`, and none of this is a knowledge item (`knowledge.db`). Insights like "local handles my summaries" become user-visible via the Pareto view and proposals — not memory entries; any lesson-ification belongs to LEARNING-FLYWHEEL and stays propose-don't-write there too.

---

## 8. Disposition & Dependency Notes

- **WF2-R13 / WORK-R9 (approved):** consumed, not duplicated. This plan's only asks upstream: the shared `rate_for()` implementation (§5.1) and the two attempt-record fields (§1.2) — both proposed as amendments to AUTONOMY-GUARDRAILS' audit record, which is the natural owner of that store.
- **AUTONOMY-GUARDRAILS:** hard prerequisite (chokepoint, timeout, fallback chain, audit JSONL). The router deliberately reuses its fallback machinery rather than owning a second retry path; breaker-OPEN on a local provider naturally routes cloud-ward with zero routing-side code.
- **EVALUATION-SUBSTRATE (NEW-11):** downstream consumer — model bake-offs sample from `model_calls.jsonl` (real production inputs), and routing scores give bake-offs a live baseline. Distinct concerns: NEW-11 evaluates templates/harness quality; this plan chooses providers per call.
- **Interactive chat routing** (step-0 native runtime) is explicitly deferred — same v1 boundary as the guardrails chokepoint. If it ever comes, it enters as a new section here, not silently.
- **Degraded-mode note:** with zero telemetry (fresh install), everything works — routing off by default; enabled, the heuristic stage needs no data.

---

## 9. Implementation Effort

**~3 sessions.**

- **Session 1 — telemetry remainder + Pareto view (§1, §2, §5.1):** `classify_query` + vocabulary; the two attempt-record fields threaded through the ModelCallGuard; `routing_stats.json` fold + rebuild path; `rate_for()` + `model_rates.json` + `BrandedProviderSpec.pricing`; `GET /api/models/telemetry` + the Routing & Efficiency tab (frontier check, scatter + table, template-card links). Ships standalone: visibility with zero routing.
- **Session 2 — heuristic router (§3, §4.1, §6.1–6.2):** `route_refs` at the bridge seam; per-use-case mode/pin in `use_case_settings` + multi-active extension for routed use cases; `routing_policy.json` + read-only table UI with override controls; local-first-with-cloud-fallback-on-timeout via the chokepoint; `routed`/`routed_fallback` provenance; `RoutingConfig` through all four wiring points.
- **Session 3 — learned stage + proposals (§4.2, §5.2, §6.3–6.4):** per-class 60/40 scoring over the fold with feedback signal extraction from ledger/judge; ≥5-sample floor + hysteresis; cost-aware near-equal ordering + cloud-quality margin; proposal enqueue/accept/reject + cooldown + notification + SEL; as-a-user validation sweep (bind local+cloud on `reasoning`, drive real background traffic, watch a proposal appear with honest evidence, accept it, verify the table + provenance).

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| Learned router trained on sparse personal-scale data overfits noise | ≥5-sample floor per (class, ref); EMA + hysteresis margin; heuristic floor below confidence; proposals (human gate) instead of auto-apply |
| Routing reordering breaks the pinned-ref-raises invariant or resurrects silent fallback | `route_refs` is a pure reorder over user-bound refs only; resolution semantics untouched; unit tests assert an unresolvable first-ordered ref still raises; fallback remains the chokepoint's bound-refs-only chain |
| Local-first timeout fallback doubles latency on every hard query (local burns 20s, then cloud) | per-class heuristics skip local for `long_reasoning`; learned stage demotes local per class as evidence accrues; breaker-OPEN skips a struggling local provider in microseconds; `local_timeout_secs` is per-use-case editable |
| Success/feedback signals are weak for background calls (no human in the loop) | honest weighting: absent feedback renormalizes onto success_rate and records `feedback_n: 0`; ledger outcome signals arrive as WF2 adoption grows; the Pareto view shows `n` so the user sees thin evidence |
| Rate-table drift (provider reprices) silently skews cost-aware ordering | user-editable `model_rates.json` overlay wins over app defaults; cost only reorders near-equals (margin-bounded), so a stale rate cannot override a quality gap; rates shown in the Pareto view for eyeball correction |
| Query classifier mislabels → wrong bucket pollution | tiny fixed vocabulary + versioned buckets (`classifier_version` starts fresh on change); misrouting is bounded by the candidate pool being user-bound refs either way |
| Second store of routing truth drifts from `active_models.json` | candidates are always read live from active_models (+ parent capability); `routing_policy.json` stores only *order + basis* per class and is pruned of refs no longer bound (same pruning discipline as `load_active_models()`) |
| Silent config drop (four-wiring-points gotcha) | explicit checklist in §7; schema reachability tests enforce `_meta`; the loader-mapping omission class is called out per recon gotcha #1 |
| Proposal fatigue (router nags) | hysteresis + `reproposal_cooldown_days` + per-use-case `mode: heuristic` as a permanent opt-out of learning; proposals are info-severity through the existing notification gate (quiet hours/mute honored) |

---

## Success Criteria

1. With routing OFF (default), zero behavior change anywhere: resolution order, latency, and test suites identical; the only new artifact is the (empty-tolerant) Routing & Efficiency tab.
2. The Pareto view answers, from recorded telemetry alone, "which bound model is on the cost/quality/latency frontier for summarize-class background work?" — with per-row `n`, and local rows showing $0 cost + real latency.
3. Binding `ollama-models:<model>` + a cloud ref on `reasoning` and enabling heuristic routing routes background `one_shot_completion` traffic local-first; killing ollama mid-stream produces cloud-rescued calls stamped `routed_fallback: true` (not `degraded`) within one breaker window, with no stacked timeouts.
4. An explicit `model_override` and a per-use-case pin each bypass/short-circuit routing, verified by attempt provenance (`routed.policy: "pinned"` / no routing stamp).
5. After ≥5 samples in a class, a genuine quality gap produces a routing **proposal** with inspectable evidence (scores, n, latency/cost deltas, sample audit ids) — the policy table does NOT change until the user accepts; accept updates the table with `proposal_id` basis and an SEL entry; reject suppresses re-proposal for the cooldown.
6. An unresolvable pinned ref under routing still raises `ProviderResolutionError` — proven by test — and a ref removed from `active_models.json` disappears from policy candidates on next load.
7. The rate table serves three consumers (router ordering, SpendMeter estimates, WF2 `cost_usd`) from one `rate_for()`; editing `model_rates.json` changes all three without a restart-order dependency.
8. The whole substrate is files: deleting `routing_stats.json`/`routing_policy.json` degrades to heuristic/off gracefully (rebuildable from the audit JSONL), and nothing in `memory.db` or `knowledge.db` changed during the entire validation sweep.

## Amendment (2026-07-26 — sibling-platform gap analysis, owner greenlight)

**The usage story (legible spend).** Sibling-platform evidence: the single most-requested legibility surface is "what did this cost me, in words a human uses" — not a router. Session 1 already ships the Routing & Efficiency tab (§1.5), but it is fitness-framed (Pareto frontier per query class). This amendment adds the spend-framed read model over the SAME recorded telemetry: daily/weekly charts by model/provider/purpose (interactive vs background vs loops), per-task/loop cost attribution ("this run cost ~$X"), and a plain-language monthly recap. **Zero new collection** — recon confirms `model_calls.jsonl` (`guardrails/audit.py:39` `AttemptRecord`: `ts, use_case, provider, model, tokens_in/out, dollars_est, estimated, passed`) plus `spend.json` day totals (`guardrails/budgets.py`, `_PRUNE_DAYS=30`) already carry everything; this is a fold + two surfaces. It deliberately front-runs the router: it needs only AUTONOMY-GUARDRAILS §2.1, none of §2-§6 here.

### Contract-level design

- **Durable usage fold** — `routing/usage.py` (rides §1.3's fold discipline; same updated-post-attempt code path): `~/.personalclaw/usage_stats.json` (atomic_write, rebuildable via the §1.3 `--rebuild` path) — per-day rows keyed `(date → provider:model → purpose)` of `{calls, tokens_in, tokens_out, dollars_est, estimated_share}`. The JSONL trims at 2×5000 lines (`audit.py:29`), so the fold — not the JSONL — is the long-horizon record (the §1.3 relationship, restated). `purpose` derives deterministically from `use_case` + the audit's run/session provenance: `interactive | background | loop | eval | app` (mapping table is a module constant; unknown → `background`).
- **Attribution:** per-run cost reuses the SpendMeter run scope (`budgets.py::run_totals(run_key)`) live, and the fold's run-keyed rollup after `end_run`; WORK-R9 RunStats stays the workflow-native view — linked, not recomputed (§1.1 discipline).
- **Route:** `GET /api/usage?window=day|week|month&group=model|provider|purpose` → `{rows: [{key, calls, tokens, dollars_est, estimated_share}], total, window}`; errors per §2.2 envelope. Derived on request from the fold — no collector process.
- **Recap:** `usage_recap(month) -> str` — a template-rendered (NOT LLM) plain-language summary ("July so far: ~$4.20 across 312 calls; 78% local at $0; your loops cost $1.10; biggest line item: anthropic:… on planning") delivered as one `digest`-mode notification through the plan-42 rules engine on the monthly boundary (system cron, respects `--no-crons`). `estimated: true` share is always disclosed ("~" and a footnote) — never fake precision.
- **FE:** a "Usage" section on the same Settings → Models page as the Session-1 tab (one page, two lenses: fitness and spend) + an optional dashboard stat tile later via AMBIENT-SURFACES (not built here). Charts follow the dataviz conventions.

### Session placement

Folded into **Session 1** as its second half (Session 1 was already "telemetry remainder + Pareto view"); session count stays ~3 but Session 1 is now explicitly shippable stand-alone before any router work — record a DEVIATION if it needs splitting into 1a/1b.

| ID | Task | Files | Done when |
|---|---|---|---|
| T-U1 | Usage fold: `routing/usage.py` (purpose mapping, per-day rollup, rebuild-from-JSONL path shared with §1.3), `GET /api/usage` | `routing/usage.py`, dashboard route, tests | fold matches a hand-computed fixture over 50 audit lines; rebuild after deleting the fold reproduces it; estimated_share correct |
| T-U2 | Usage UI: daily/weekly charts (model/provider/purpose group-by), per-run cost line on run/loop detail surfaces via `run_totals` | Settings → Models Usage section, run detail component | charts render from real dev-home traffic; a loop's detail shows "~$X this run" |
| T-U3 | Monthly recap: template renderer + system cron + delivery through the rules engine as a `digest`-mode notification | `routing/usage.py`, cron registration site | fixture month renders the recap verbatim-predictable; notification obeys quiet hours/mute |

## Execution log — MRT-1a (pure query classifier) + MRT decomposition

- **MRT plan DECOMPOSED (tick-12 design verdict).** The two whole-plan atoms split: MRT-1 →
  1a classifier / 1b audit `query_class` field / 1c `routing_stats.json` fold+rebuild / 1d
  `GET /api/models/telemetry` / 1e Routing FE tab; MRT-2 → 2a `rate_for`+`model_rates.json` overlay
  (clean addition) / **2b = OWNER DECISION** (consolidating `pricing.py` onto `rate_for` re-keys the
  SHIPPED CATO cost seam by `(provider,model)` across 6 modules — `usage_ledger`/`subagent`/
  `chat_runner`/`guardrails.model_call` + `pricing.py` + `model_pricing.json`, and `test_pricing.py`
  locks the model-only signatures). 2b DEFERRED until the owner rules; recorded in dag.json.
- **MRT-1a DONE.** `routing/classifier.py::classify_query(text, use_case="", *, wants_structured_output=False)`
  — the pure heuristic routing needs so it never spends an LLM call to decide. Maps every request
  into the fixed 5-class vocabulary `short_chat | code | summarize | extract_structured |
  long_reasoning` (module constant `QUERY_CLASSES`), versioned by `CLASSIFIER_VERSION` so the stats
  layer buckets by `(use_case, query_class)` and starts fresh on a vocabulary change. Precedence:
  structured-output request (flag OR text signal) → `extract_structured`; code fence / `code_tools`
  use-case / real code-shaped signal → `code`; explicit condense ask → `summarize`; long text /
  `reasoning` use-case / reasoning-marker words → `long_reasoning`; else `short_chat` (the
  cheapest-model-safe fallback, incl. empty/None). Pure, deterministic, no I/O, no model call, no
  provider import (use-case labels are bare strings). **A false-positive my own test caught:** the
  first code regex matched the English word "function" ("the function of the mitochondria"); tightened
  so code keywords require code shape (`def name(`, `class Name:`, `function(`, `SELECT…FROM`,
  line-start imports, code punctuation) — prose using function/class/return as words is not miscalled.
  Dep (guardrails `model_calls.jsonl` + `ModelCallGuard` seam, AG-1) verified SHIPPED; the seam
  `query_class` threads through is `AttemptRecord` (audit.py:39) set in `ModelCallGuard._audit`
  (model_call.py:381) — that's MRT-1b. No caller yet in this atom (the classifier is consumed by 1b);
  it's a pure library with a full test suite. No user surface → no CHANGELOG. **Gates:** `make lint`
  clean (712 files); `tests/test_routing_classifier.py` (21: vocab/version constants, all 5 classes,
  precedence incl. structured>code and summarize>length, the function-prose false-positive guard,
  empty/None/mid-length fallback, purity/determinism) pass.

## Execution log — MRT-1b (thread query_class onto the attempt audit)

- **MRT-1b DONE.** The pure classifier (MRT-1a) is now WIRED into the one seam every non-interactive
  model call passes through. `AttemptRecord` (guardrails/audit.py) gains a first-class
  `query_class: str = ""` column (not an `extra` field — the stats layer folds per
  `(use_case, query_class)`). `ModelCallGuard` classifies the CURRENT call at each entry point that
  holds the prompt text — `stream`/`stream_command` (raw text) and `complete` (via a new
  `_joined_content` helper that joins the user turns' text, handling both string and typed-block
  content shapes) — stores it on `self._query_class`, and stamps it onto every attempt row via
  `_audit`. Classification is **fail-open**: a `_classify` helper swallows any classifier error and
  leaves `query_class=""`, so a telemetry field can never break a model call (verified — a boom
  classifier still completes the call and audits an empty class). The guard passes its own
  `use_case` into `classify_query`, so the use-case prior flows through (a `code_tools` call →
  `code`). Every `model_calls.jsonl` row now carries the query class the routing stats layer
  (MRT-1c) will fold on. No user surface → no CHANGELOG; the new audit column is additive (no
  ratchet trips — verified against the audit's consumers: portability, snapshot, learning-detectors,
  budgets/profiles/flags). **Remaining in MRT-1:** 1c (`routing_stats.json` fold + rebuild reading
  this column), 1d (`GET /api/models/telemetry`), 1e (Routing FE tab). MRT-2a (rate_for overlay) is
  a clean addition but pointless without the owner-gated 2b (pricing consolidation) — both deferred.
  **Gates:** `make lint` clean (712 files); `tests/test_guardrails_query_class.py` (10: query_class
  stamped for stream/complete/stream_command, short_chat default, use-case prior flows in, fail-open
  on a broken classifier, AttemptRecord column round-trips + defaults empty, `_joined_content` from
  string+blocks + junk-tolerant) + guardrails/classifier regression (50) + audit consumers (164) pass.

## Execution log — MRT-1c (rolling routing-stats fold + rebuild)

- **MRT-1c DONE.** `routing/stats.py` — the incremental `routing_stats.json` fold keyed
  `(use_case → query_class → "provider:model_id" ref)` so the router reads an O(1) fold instead of
  scanning `model_calls.jsonl` per call. `fold_record` maintains conservative online estimates
  (EMA, alpha 0.2 — one bad night never flips a policy): `n`, `success_rate` (EMA of `passed`),
  `avg_ms`, `avg_cost_usd`, `feedback`/`feedback_n` (0 until Session-3 feedback extraction), and
  `score` (§4.2 0.60·success + 0.40·feedback, but **collapsing onto success_rate when feedback_n=0**
  so an unrated ref isn't docked for a signal it can't have). First sample seeds the EMAs with the
  observed values; a row lacking `use_case` or `query_class` (an unclassified call) is skipped (can't
  attribute it). `ref_of` joins on the ref's natural spelling so a colon-bearing model id
  (`gpt-oss:20b` → `provider:gpt-oss:20b`) round-trips. `record_routing_stats` is the post-attempt
  hook wired into `ModelCallGuard._audit` (folds the SAME `AttemptRecord` — parsed via
  `to_json_line` so the live fold and rebuild see byte-identical row shapes) — best-effort, never
  breaks a call. `rebuild(home, audit_path)` = the `--rebuild-routing-stats` refold over the
  (capped/rotated) JSONL, so the fold is the durable long-horizon record and the JSONL the recent
  forensic one. **DEVIATION (documented in the module):** §1.3's JSON example shows `p50_ms`/`p95_ms`
  in the fold, but true percentiles can't be maintained incrementally from an EMA; per §1.5 the
  telemetry route (MRT-1d) derives p50/p95 at READ time "from routing_stats.json + a bounded tail of
  model_calls.jsonl", so the fold keeps `avg_ms` and stays a true O(1) update rather than a growing
  per-ref latency reservoir. No user surface (the fold is consumed by the MRT-1d route + the Session-3
  router) → no CHANGELOG. **Remaining in MRT-1:** 1d (`GET /api/models/telemetry` deriving per-model
  rows incl. read-time p50/p95 + the ~20-line frontier-dominance check), 1e (Routing FE tab). MRT-2a
  clean-addition + 2b owner-gated pricing consolidation still deferred. **Gates:** `make lint` clean
  (713 files); `tests/test_routing_stats.py` (11: EMA blend + first-sample seed, score-collapse
  without feedback, colon-model ref, unclassified skip, load/save round-trip + corrupt-degrades,
  rebuild-from-JSONL + missing-JSONL, live guard→fold hook) + guardrails/classifier regression (50) pass.

## Execution log — MRT-1d (GET /api/models/telemetry read route)

- **MRT-1d DONE.** The read-model + route behind the Pareto/efficiency view. `routing/telemetry.py`
  is the PURE read-model: `telemetry_rows(stats, audit_rows, use_case, query_class)` derives one row
  per candidate ref `{ref, n, success, feedback, avg_cost_usd, p50_ms, p95_ms, on_frontier}` from the
  O(1) fold (MRT-1c `routing_stats.json`, supplying n/success/feedback/cost) PLUS a bounded
  `model_calls.jsonl` tail (supplying READ-TIME p50/p95 — the fold keeps EMA `avg_ms`; true
  percentiles can't be EMA'd, the deviation documented in 1c, resolved here per §1.5). `on_frontier`
  = the ~20-line dominance check: a row is on the frontier unless another ref dominates it (no worse
  on quality↑/latency↓/cost↓ and strictly better on one). A ref with no latency samples has
  `p50_ms=0`, treated as unknown/∞ so it never falsely knocks a measured row off. Pure given its two
  inputs (fold dict + JSONL rows), so trivially testable. `dashboard/handlers/model_telemetry.py`
  is the thin route `GET /api/models/telemetry?use_case=&query_class=` (both required → clean 400;
  §2.2 error envelope; read-only, 500-safe), registered in `server.py` beside the usage routes. It
  reads `config_dir()`'s fold + a 2000-row JSONL tail. Nothing here ROUTES or decides — it shapes a
  view (the Session-3 router is the consumer of the fold, separately). No CHANGELOG (the FE tab
  MRT-1e is the user surface; this is its data). **Remaining in MRT-1:** 1e (Routing & Efficiency FE
  tab rendering this). MRT-2a clean-addition + 2b owner-gated pricing consolidation still deferred.
  **Gates:** `make lint` clean (715 files); `tests/test_routing_telemetry.py` (12: nearest-rank
  percentile, dominance incl. tradeoff-neither + unknown-latency, fold+latency join, frontier marks
  both on a tradeoff + drops a dominated row, empty bucket; route 400-on-missing-params +
  rows-for-a-bucket + empty-200) pass.

## Execution log — MRT-1e (Routing & Efficiency FE tab) — MRT Session-1 COMPLETE

- **MRT-1e DONE; MRT-1 (telemetry + Pareto view) COMPLETE.** The read-only "Routing & Efficiency"
  settings surface rendering `GET /api/models/telemetry` (MRT-1d). `web/src/pages/settings/RoutingPanel.tsx`:
  two selectors — use_case (`chat`/`code_tools`/`reasoning`, reusing ModelsPanel's USE_CASE_META
  labels, 3 → `Segmented`) and query_class (the 5 `QUERY_CLASSES`, >4 → a `Select` from `ui/forms`) —
  both URL-round-tripped via `useQueryParam` (`?uc=`/`?qc=`) so a reload restores the view. Fetches
  via a new `api.modelsTelemetry({use_case, query_class})` (+ `TelemetryRow` interface mirroring the
  1d JSON) through `useCachedData` keyed by both params. Renders a table (ref/n/success%/feedback/
  p50/p95/cost) with the **Pareto frontier** made visible: `on_frontier` rows floated to the top
  (`sortByFrontier`) and flagged with a `Trophy` badge (text label + `title`, `aria-hidden` icon —
  never color-only) plus an "N of M models on the frontier" summary. THREE distinct states —
  loading (`undefined`), graceful inline error (`.catch→null`), and a friendly empty-bucket message
  (`rows.length===0`) — so a bucket with no telemetry yet never renders broken. Cost shows `free`
  for local/0-cost (honest, not `$0.00`); feedback/latency show `—` when absent. Registered in BOTH
  `SUBPAGES` (SettingsPage.tsx) and `settingsWidgets.tsx` (bento, "AI & Models" group) — the
  two-registration contract. Primitives + token colors only (no ratchet moved). Scatter plot
  DEFERRED (a dependency-free table + frontier flag fully satisfies the visibility goal — no
  charting dep pulled in). Class-B UI, user-facing → CHANGELOG-worthy, but this is the read-only
  view of already-recorded telemetry with no behavior change; noted as a feature addition. **MRT
  Session-1 (1a classifier → 1b audit field → 1c stats fold → 1d read route → 1e visualization) is
  COMPLETE**: local-vs-cloud efficiency is now visible with zero routing. **Remaining MRT:** 2a
  (rate_for overlay, clean addition) + 2b (OWNER-GATED pricing consolidation, deferred) are Session-1
  pricing; MRT-3/4/5 (usage read-model, heuristic router, learned policy) are Sessions 2-3. **Gates:**
  `npm run typecheck` clean; `npm test --workspace web` 753 pass (65 files, 11 new routingPanel + all
  design ratchets green); `npm run build` green.

## Execution log — MRT-3 (usage/spend read model) — BLOCKED, owner scope decision needed

- **MRT-3 NOT STARTED — BLOCKED (E1 premise mismatch + E6 scope). No code written; the atom stays
  `todo` and `dag.json` is untouched.** The amendment's four clauses rest on three premises that are
  false against the code, and the two surfaces it asks for already ship under
  COST-AND-TOKEN-OBSERVABILITY over a *different* record. Building MRT-3 as written would put a
  second spend fold, a second `/api/usage*` route and a second Usage surface beside the shipped ones,
  reporting **different dollar totals for the same month** because the two records cover different
  populations. Everything below is measured, not inferred.

- **① The declared fold input structurally excludes interactive chat.** `model_calls.jsonl` is written
  only by `ModelCallGuard`, and the guard is attached only for
  `use_case in ("reasoning", "background", "loops", "orchestration")`
  (`providers/provider_bridge.py:683`). The comment immediately above it (`:670-682`) states the
  exclusion as a design decision: *"The interactive chat/code_tools stream stays OUT OF SCOPE …
  both human-watched."* `wrap_model_call_guard` has exactly one production call site
  (`provider_bridge.py:1145`). So a spend read model folded from this file omits the user's largest
  line item, and of the atom's five purposes `interactive` would have exactly one producer
  (`orchestration`) while a monthly recap's "~$X across N calls" would be a **wrong money number**.

- **② `app` has no producer at all.** `personalclaw/sdk/` exposes no LLM-call helper (`model.py` is
  provider-ABC re-exports; `one_shot_completion` is not re-exported anywhere under `sdk/`), and there
  are zero `one_shot_completion` / `resolve_provider_for_use_case` callers under
  `src/personalclaw/apps/`. An installed app cannot make a core-audited model call today, so an `app`
  bucket would ship as an enum member nothing can ever write.

- **③ The product is already built, over the better-populated record.** `usage_ledger.py` keeps a
  per-TURN `usage/turns.jsonl` carrying real caller provenance in its `source` field
  (`chat` | an app name | `loop` | `cron` | `channel` | `cli` | `subagent` | `background`) — five live
  writers: `gateway.py:1932`, `gateway.py:2804`, `subagent.py:2231`, `cli_chat.py:56`,
  `chat_runner.py:519` (whose `source=getattr(session, "_app", "") or "chat"` is where both the `app`
  and `loop` labels come from). Over it already ship: `GET /api/usage/rollup` + `GET /api/usage/totals`
  (`dashboard/handlers/usage.py:72-74`, registered `dashboard/server.py:470`) **with `group_by="day"`
  already supported** — i.e. the daily-chart data path exists; a full Usage subpage at
  `#/settings/usage` (`web/src/pages/settings/UsagePanel.tsx`, Today/7d/30d segmented, BigStat row,
  by-model + by-source tables, cache savings, daily-cap line); and the atom's own run-cost string,
  verbatim, at `web/src/pages/workflows/IntrospectPanel.tsx:261`
  (`` `$${data.stats.cost_usd.toFixed(4)} this run` ``). MRT-3's `/api/usage?window=&group=` would sit
  beside two `/api/usage/*` siblings answering the same question from a narrower population.

- **④ A naive union double-counts loop spend, so "fold both" is not a free fix either.** A loop
  worker's turns run through `run_chat` (`gateway.py:2137`, with `_app == "loop"` at `:2120`) →
  `_record_turn_usage` → a `source="loop"` turn row; the SAME session's inner model resolves under
  `inner_axis="loops"` (`provider_bridge.py:379-388`) → guarded → attempt rows in
  `model_calls.jsonl`. The two records therefore overlap on at least loops, and establishing
  disjointness for the rest requires a per-writer audit of both files — a program, not an atom.

- **⑤ `run_totals` cannot answer "~$X this run" for a finished run.** `SpendMeter._run_totals` is an
  in-memory dict (`guardrails/budgets.py:91`) that `end_run` pops (`:156-159`). A completed run
  reads `_ScopeTotal()` → `0.0`, so a run/loop detail wired to `run_totals` as the atom specifies
  would render "~$0.00 this run" — the one thing a money surface must never do. Durable per-run cost
  already exists elsewhere (`WorkflowRunStats.cost_usd`, surfaced by `IntrospectPanel`).

- **⑥ There is no genuine local data to validate a fold against (residue, already fixed upstream).**
  Measured read-only on this machine: `~/.personalclaw/model_calls.jsonl` holds 1231 rows and **100%
  of them are this suite's own fixtures** — `provider="fake-<id>"`, `model="gpt-4o"`,
  `use_case="unattended"`, a value that appears nowhere in `src/` but is set at
  `tests/test_guardrails_budgets.py:471-473`. `routing_stats.json` holds 69 phantom
  `fake-<N>:gpt-4o` refs under a bogus `unattended` axis, and `spend.json` holds **$370.89 across
  2026-08-04..2026-08-11**. Cause: pre-CRE-8 test leakage; **CRE-8 (#1111, 2026-08-12) fixed it** with
  the global `guarded_config_dir` autouse fixture + the real-home rail (`tests/conftest.py:100-137`,
  `tests/real_home_guard.py`), and the rail reports the home unchanged on a current run. So this is
  historical residue, not an active leak, and it does not bind a day budget (`day_totals` reads only
  today's key). Two consequences: T-U1's "hand-computed fixture over 50 audit lines" must be
  synthetic, and nobody should read this machine's fold as evidence of real routing behaviour. *(A
  candidate per-file isolation fixture was written, measured against `conftest.py`, found redundant
  with CRE-8's global one, and reverted — no dual path shipped.)*

- **THE OWNER DECISION MRT-3 NEEDS: which record is THE spend read model, and how do the two
  reconcile?** Three coherent answers, none of them "build the atom as written": **(a) retire MRT-3's
  fold** and make the amendment's remaining value additive to CATO — a purpose/`source` grouping, a
  daily/weekly chart `Section` inside the shipped `UsagePanel`, a loop-detail cost line (the one
  genuinely missing surface — `LoopCockpitPage.tsx` shows no dollar figure anywhere), and
  `usage_recap(month)` reading `usage_ledger.rollup`; **(b) widen the attempt audit** so
  `model_calls.jsonl` covers interactive chat and carries run/app provenance, then fold it and retire
  CATO's ledger — a class-B clean break across five writers; **(c) keep both and define the
  partition** explicitly, with a disjointness test per writer. (a) is the smallest honest change and
  keeps one answer to "what did this cost me"; (b) is the only path that makes the atom's literal
  done_when true; (c) is the most expensive and the easiest to drift.

## Execution log — MRT-3 (usage/spend read model) — BUILT, resolving the BLOCKED above

- **The BLOCKED entry's scope question is answered by choosing its option (a), extended.** That
  entry offered three ways out: (a) retire MRT-3's fold and make the amendment additive to CATO,
  (b) widen the attempt audit and retire CATO's ledger, (c) keep both records and define the
  partition. **(c) was attempted first and abandoned on measurement**: a cross-store union needs a
  join key to deduplicate, and there is none — a turn row carries no `audit_id` and an attempt row
  carries no session key — so the ④ loop overlap (a loop's inner inference is recorded in BOTH) is
  not merely hard to reconcile, it is undetectable at fold time. A money total that can
  double-count with no way to notice is worse than one that admits a gap. So the fold sums the
  **ledger only** (the record that covers interactive chat, per ①) and **censuses** the attempt
  audit into `fold["uncounted"]`, which the route and the UI both state out loud with its size.
  Option (b) remains the only path to the atom's literal `done_when`; it is a class-B clean break
  across five writers and is not this atom.

- **Built.** `routing/usage.py` (fold + `audit_census` + `query` + `usage_recap`), `GET /api/usage`
  added to the EXISTING `dashboard/handlers/usage.py` (one usage handler module, three GETs — a
  sibling module would have split "what did this cost me" across two files), and a
  "By day and purpose" `Section` inside the shipped `UsagePanel` (no new page shell, no second
  Usage tab). `usage_stats.json` is per-day `date -> provider:model -> purpose`; the purpose
  vocabulary is mapped from the ledger's `source`, with an unrecognized source correctly read as an
  **app name** (`chat_runner` sets `source = session._app or "chat"`) and censused in
  `app_sources` — so `app` has a real producer, correcting the BLOCKED entry's ② for the ledger axis.
  Tests: `tests/test_routing_usage.py` (20), `tests/test_usage_routes.py` (+7, 13 total),
  `web/src/pages/settings/usageFoldSection.test.tsx` (9).

- **DEVIATION — the fixture is 50 LEDGER lines, not 50 audit lines.** The clause says "a
  hand-computed fixture over 50 audit lines"; the audit is no longer the fold's input, so the
  hand-computed fixture is 50 `usage/turns.jsonl` rows (expected cells written out by hand, with a
  stubbed rate table so the expectation cannot drift with shipped price defaults) plus a separately
  hand-computed 12-row audit census. `test_a_guarded_attempt_is_censused_never_summed` proves the
  audit contributes to no money figure: the fold is byte-identical with and without the audit file.

- **DEVIATION — `estimated_share` is 1.0 for every row today, and that is honest, not inert.**
  `TurnUsage` has no "estimated" flag, so a rate-derived cost is indistinguishable from a
  provider-reported one and the fold treats every turn dollar as an estimate (over-disclosing an
  estimate is safe; claiming absent precision is not). The per-cell `estimated_dollars` is kept, so
  the share drops below 1.0 the moment a writer marks a reported cost. Separately measured and
  worth a follow-up: `guardrails/model_call.py` sets `estimated=True` unconditionally on every
  attempt row while its own comment says "unless the provider reported a real cost_usd" — a
  wired-but-constant flag, out of this atom's fence.

- **NOT BUILT — the recap's DELIVERY.** `usage_recap(month)` renders and is pinned verbatim, but the
  `digest`-mode notification through the rules engine and the system-cron registration are outside
  this atom's file fence (they live at the cron registration site, not in `routing/usage.py`). The
  renderer is a pure function of the fold, so wiring it later needs no rework here.

- **NOT BUILT, deliberately — the run/loop-detail "~$X this run" line.** The BLOCKED entry's ⑤ is
  correct and re-verified: `SpendMeter._run_totals` is in-memory and `end_run` pops it, so a
  FINISHED run reads `_ScopeTotal()` → `0.0`. Wiring a run-detail money line to `run_totals` as the
  atom specifies would render "~$0.00 this run" — the one thing a money surface must never do. The
  durable equivalent already ships (`WorkflowRunStats.cost_usd`, rendered by
  `IntrospectPanel.tsx:261` as the atom's own string, verbatim), so this clause is satisfied by
  existing code rather than duplicated.

- **Also corrected while here:** `UsagePanel`'s header claimed the ledger covered "every turn (chat,
  subagents, loops, automations)". It does not cover guarded `complete()` calls, so the copy
  overstated its own coverage; it now says what is excluded and points at the section that sizes it.

## Execution log — MRT-3, session 2 (the recap's DELIVERY + the money-honesty defect)

- **Census first: 7 of 10 clauses were already SHIPPED and correct.** The fold, the hand-computed
  fixture, `GET /api/usage`'s `rows`/`total`/`estimated_share`, the Usage section's charts and
  `usage_recap`'s verbatim pin all measured green. Two shapes this repo keeps hitting were checked
  and were NOT present: the rebuild clause is a real property (`test_routing_usage.py:262`
  `.unlink()`s `usage_stats.json` and re-folds, rather than commenting that it could), and
  `estimated_share` is computed by `_share()` (`routing/usage.py:477`) with
  `test_routing_usage.py:392` pinning 0.0 when there are no dollars — not a field nobody fills.

- **DONE — the recap's delivery, the clause the previous session recorded as NOT BUILT.** New
  `action_providers/usage_recap_provider.py`: renders `usage_recap(previous_month())` and emits it
  through `DashboardState.notify()`, so it inherits the whole policy stack rather than
  re-implementing any of it — global gate (mute-all / min-severity / quiet hours) first, then the
  per-`(source, kind)` rule. Registered `system/usage_recap` in `notification_kinds.py`, added the
  provider to the three places a provider must appear together (`action_providers/registry.py`,
  `validation.ALLOWED_HOOK_PROVIDERS`, `triggers/screen.py`'s write-capable set), declared it under
  `action.digest` in `guardrails/rungs.py`, gave it a row in the SPA display map, and reconciled a
  monthly `0 9 1 * *` system cron at boot in the `--no-crons` else-branch beside the digest's.

- **The writer trace, since "delivers one notification" is a claim about a writer.** boot
  `_init_cron` (`gateway.py:1866`, `--no-crons` else-branch) → `reconcile_usage_recap_cron` writes
  the UNIFIED trigger store directly and arms it (writing `crons.json` is the S108 bug that made
  the digest inert for a release) → the clock tick fires `usage-recap` → the provider renders and
  calls `notify()` → `notification_allowed()` → the rule → the digest queue. Nothing in that path
  touches the real home: the fold, the mark and the queue are all under `config_dir()`, and the
  suite's real-home rail reports `~/.personalclaw` unchanged.

- **Exactly-one is enforced by a mark, not by trusting the cron.** `usage_recap_sent.json` keys on
  the month and is checked BEFORE rendering. A monthly cron firing twice is real rather than
  hypothetical — the boot sweep re-arms an overdue trigger, so a machine asleep across the 1st
  fires on wake — and without the mark the feature is "one recap per boot in the first week of the
  month". The mark is written whether or not the note survived the gate: a recap suppressed by the
  user's own quiet hours is suppressed, not deferred, and retrying until it lands is the escalation
  a quiet-hours setting exists to refuse. `delivered` records which happened.

- **FALSIFIED, both gates, by mutating the live line and quoting the red.** Deleting the
  quiet-hours/mute gate (`dashboard/state.py:1240` → `if False:`) reds
  `AssertionError: quiet hours did not suppress the recap` and `assert [{'kind': 'us...}] == []`,
  plus the same for `mute_all` — so those two tests observe the gate rather than an empty fixture.
  Deleting the mark check (`usage_recap_provider.py:123` → `if False:`) reds
  `assert 2 == 1` / `the second fire delivered a SECOND recap`. That second red arrived on the
  wording assertion first, so the assertions were REORDERED to put the queue count ahead of the
  stdout string — a falsification that reds on a message rather than on the property is measuring
  the message.

- **Every rail carries a vacuity floor.** A recap rail over a month with no usage passes forever, so
  the delivery test asserts the fixture produced `"12 turns"` before asserting anything about the
  gate, each suppression test is paired with an identical-fixture unsuppressed control, and the
  once-per-month test asserts the FIRST fire delivered before asserting the second did not.

- **DEVIATION — the `default_mode == "immediate"` invariant was SCOPED, not weakened.** The clause
  requires a `digest`-mode notification, and `test_every_default_mode_is_immediate` /
  `test_no_rules_file_delivers_exactly_like_before` asserted every registered kind defaults to
  `immediate`. That invariant's own docstring gives its reason — "Every emitter that passes the
  global gate produces a toast today", i.e. BEHAVIOR PRESERVATION — and `usage_recap` has no
  pre-registry emitter, so there is no prior delivery to preserve. Both now bind the
  `_LEGACY_FLAT` population, which is the same no-history-no-obligation split `_ATTENTION_FLAT`
  already makes for SEVERITY. Guarded against becoming a loophole by
  `test_the_kinds_defaulting_to_something_other_than_immediate_are_EXACTLY_these`, which pins the
  exempt set to exactly `["system/usage_recap"]`. Also widened
  `test_every_wire_constant_resolves` from `_LEGACY_FLAT` to `_WIRE_TO_PAIR` — the map the
  import-time guard three lines below it already used — because `USAGE_RECAP` is the first named
  wire constant for a kind with no legacy history.

- **DONE — a money-honesty defect in the run-cost line the previous session declared "satisfied by
  existing code".** It is not: `IntrospectPanel.tsx:261` rendered
  `` `$${data.stats.cost_usd.toFixed(4)} this run` `` — four decimals of apparent precision on a
  figure `pricing.py` describes as derived ("Providers report token counts but not always a dollar
  cost (most set `cost_usd=0.0`)"), and with no `~` where the atom's own clause says `~$X`. Now
  `runCostText()`: `~$0.1234 this run — estimated from model prices, not a provider-reported
  charge`, `~$4.20` once past a dollar (reusing `routing/usage.py::_usd`'s rounding rather than
  re-deciding it), and for zero `Nothing recorded — a local model, or one with no price row`
  instead of `$0.00`, because zero cannot distinguish free-and-local from unpriced.

- **Also corrected while here — one panel was making two different claims about one dollar.** The
  same `cost_usd` renders three times in `IntrospectPanel`: the "Cost" stat (`:101`), the money
  answer (`:261`) and each timeline row (`:325`). Only one disclosed. All three now carry the
  tilde and the stat is labelled `Cost (est.)`, with the full sentence stated once.

- **STILL UNMET, with a sharper measurement than the previous session's — the LOOP detail's
  "~$X this run".** That session called it blocked on `SpendMeter._run_totals` being in-memory.
  PP-5 has since landed, so a loop IS a ledger producer (`loop/journal.py` — "the SECOND producer
  of the platform ledger") and `ledger.run_totals` is durable. But `LoopLedger.cycle()`
  (`loop/journal.py:104-112`) writes `STEP_COMPLETED` with `cycle`/`node_id`/`task_id`/
  `source_file`/`finding` and **no `tokens` and no `cost_usd`** — the two fields `run_totals`
  sums — so `run_totals` over a loop store returns `cost_usd: 0.0` and a loop-detail money line
  wired to it would render exactly the "~$0.00 this run" that must never ship.
  `LoopCockpitPage.tsx` still contains no dollar figure. The fix is a cost-carrying loop cycle
  emitter, which needs the loop's per-cycle model spend threaded to the ledger write; that is
  PP-5 follow-on work in the loop engine, not this atom's fence, and inventing a number to fill
  the surface would be worse than the gap. **So MRT-3 remains PARTIAL on this one clause.**

- **Not swept into this change:** `docs/design/consistency-audit.json` regenerates as a side
  effect of the web suite and drifted (`filesScanned` 522→526, a new
  `pages/settings/AlwaysOnConventions.tsx` row) because `ef42a5cb` landed that file without a
  regen. `driftHits` and `filesWithDrift` are unchanged and this change adds no web file, so the
  artifact was discarded rather than committed — the staleness is PEP-10's to regenerate. Worth
  noting for whoever does: a `npx vitest run --root web` of the audit test reds where the full
  `npm test --workspace web` (417 files, 4282 tests) is green, so the rooted invocation is a false
  red on that file.

## Execution log — MRT-3, session 3 (why the last clause is still unmet, measured one layer deeper)

**MRT-3 REMAINS `todo`.** This session set out to close the one unmet clause — the loop detail's
"~$X this run" — on the reasoning that session 2 had deferred it to "PP-5 follow-on work" and PP-5
has since landed. PP-5 landing was necessary but not sufficient, and the measurement below is the
reason. No money surface was added.

**What session 2 established (re-verified, both citations still accurate):** `LoopLedger.cycle()`
writes `STEP_COMPLETED` with `cycle`/`node_id`/`task_id`/`source_file`/`finding` and no `tokens`,
no `cost_usd`; `ledger.run_totals` (`ledger/reader.py:43-44`) sums exactly those two fields. So
`run_totals` over a loop store returns `cost_usd: 0.0`.

**The deeper reason, new this session: NOTHING ties model spend to a specific loop, in either
record.**

- **The turn ledger has no loop rows at all.** `PURPOSE_BY_SOURCE` declares `"loop": "loop"`, but a
  census of the five live `record_from_event` call sites shows the sources actually passed are
  `background` (`gateway.py:1943`, the heartbeat), `channel`/`cron` (`gateway.py:2864`),
  `subagent` (`subagent.py:2231`), `cli` (`cli_chat.py:56`) and `chat`-or-an-app-name
  (`chat_runner.py:3515`, `session._app or "chat"`). **No writer passes `loop`.** The loop package
  contains zero references to `cost_usd`, `usage`, `tokens_in` or `total_tokens`, and no
  `record_from_event` call.
- **Therefore the module note's double-count example is aspirational, not current.**
  `routing/usage.py:22` states "a loop worker's turn is recorded as a `source="loop"` turn AND its
  inner inference resolves under the `loops` axis into a guarded attempt row". The first half does
  not happen. Loop spend exists only in `model_calls.jsonl`, which the fold CENSUSES into
  `fold["uncounted"]` rather than sums — which is why loop spend is invisible in the fold today,
  and it is not a double-count problem but a no-writer one.
- **And the audit row cannot be scoped to one loop.** `AttemptRecord` (`guardrails/audit.py:39`)
  carries `audit_id`/`ts`/`use_case`/`provider`/`model`/`attempt`/`tokens_in`/`tokens_out`/
  `dollars_est`/… and no loop, run or session identifier — confirming the note's "no session key on
  an attempt".

**So the clause needs spend ATTRIBUTION, not just a durable ledger.** The available half-measures
were both rejected: wiring the cockpit to `run_totals` renders the forbidden "~$0.00 this run", and
recording turn rows only at the loop's judge/gate inference sites (`loop/gates.py:133`, which does
observe `EVENT_COMPLETE`) would attribute a FRACTION of a loop's spend — the worker's inference does
not run through the loop package at all — so the cockpit would state a dollar figure that is
confidently wrong and lower than the truth. An understated money number is the same defect class as
an invented one. Closing this needs the loop's worker execution seam identified and a loop
identifier threaded to whichever record is chosen as authoritative; that remains loop-engine scope.

**Shipped instead — the one defect this measurement exposed, in MRT-3's own module.**
`reachable_purposes()` existed precisely so a surface would not render a bucket nothing can fill,
and it excluded only `eval` while advertising `loop`. It now excludes both, via a named
`UNWRITTEN_PURPOSES` frozenset whose basis is stated, plus `tests/test_usage_reachable_purposes.py`,
which censuses the real call sites so the exclusion cannot go stale silently — the load-bearing
direction being a purpose that GAINS a writer while still listed as unwritten, which would hide real
spend. Falsified by pointing a live call site at `source="loop"`: 3 red including
`['loop'] now HAS a turn-ledger writer but is still listed as unwritten`; restored from a file copy.
The pre-existing `test_reachable_purposes_reports_only_what_a_writer_can_produce` asserted `loop`
was reachable, contradicting its own name; it now asserts the measured set.

**Scoped honestly:** `reachable_purposes` ships in the `/api/usage` payload and is typed in the
frontend (`api.ts:3120`), but `UsagePanel.tsx` does not read it yet — so this fixes a payload
contract and forecloses the zero-row before a consumer arrives, rather than removing a row a user
currently sees. The census test's first cut was itself wrong in the same family: scoped per FILE it
swept up `dashboard`/`gateway` from unrelated calls and would have counted `watchdog.py`'s
`emit_attention_item(source="loop")` — an INBOX source — as a turn-ledger writer, inverting the very
finding above. It is now scoped per CALL by paren balance, with a test asserting the innocent line
does not count.

## Execution log — MRT-3, session 4 (the `loop` exclusion was hiding real spend)

**The finding: `loop` HAS a live turn-ledger writer, and sessions 2 and 3 both recorded the
opposite.** Session 3 concluded *"the turn ledger has no loop rows at all"* and *"NOTHING ties model
spend to a specific loop, in either record"*, and shipped `UNWRITTEN_PURPOSES = {"eval", "loop"}`
on that basis. `reachable_purposes()` subtracts that set, so the rail written to prevent a hidden
row was itself hiding one: every dollar a loop worker spends lands in a `loop` cell that no surface
filtering on `reachable_purposes()` would render.

**The writer, every hop verified in this session's tree:**

- `loop/manager.py:161` creates the loop's worker session with `app="loop"` (and `:429` does the
  same for each parallel task worker) — so it is two call sites, not one.
- `dashboard/state.py:1595` `session._app = app` is the ONLY assignment of that field in
  `src/personalclaw` (grepped: one hit).
- `gateway.py:2399` wires the autonudge `on_fire=_fire`; `_fire` drives the loop's cycle through
  `gateway.py:2241` `_run_one` → `await _run_chat(dstate, _sess, _msg)`.
- `chat_runner.py:3567` (inside `_run_chat`) passes
  `source=getattr(session, "_app", "") or "chat"` to `_record_turn_usage` →
  `usage_ledger.record_from_event`.
- `routing/usage.py::purpose_for_source` checks `PURPOSE_BY_SOURCE` **first**, and that map declares
  `"loop": "loop"` — so the string never reaches the app fallback.

**Why the census could not see it, stated precisely.** `tests/test_usage_reachable_purposes.py`
resolved `source=` per call by paren balance — correct — but the loop's spelling is a RUNTIME value,
so the only thing to resolve was the expression `source`, and `_RESOLVED_EXPRESSIONS` hand-resolved
it as `("chat",)` with the comment *"plus an app name → APP_PURPOSE, asserted separately"*. **That
hand-resolution is the bug.** An app name reaches `APP_PURPOSE` only when it is NOT already a key of
`PURPOSE_BY_SOURCE`, and `loop` is a key. Measured on the real tree, the old census yields exactly
`{background, channel, chat, cli, cron, subagent}`; `loop` is absent, so
`test_every_unwritten_purpose_really_has_no_writer` computed `wrongly_excluded == set()` and passed
while the writer existed. A corrected set with an uncorrected census would silently re-break on the
next app name that collides.

**So the fix is the census, not only the set.** `_app_names()` censuses every `app=` string literal
in `src/personalclaw` (comment lines stripped first — `gateway.py` and `chat_runner.py` mention
`app="loop"` in prose three times, and a text scanner that counted those would look like it had
measured the call sites after they moved), and `_writer_sources()` merges them wherever the chat
seam's `source` expression reaches the ledger. Three new rails carry it: the chain above asserted
hop by hop; a vacuity floor for the app census (non-empty, and it must contain `loops` — the
plan-walkthrough planner session, a real app name that is NOT a source key, so the collision filter
below is discriminating rather than total); and
`test_the_app_names_that_collide_with_the_source_vocabulary_are_exactly_these`, pinning the
colliding set to `{"loop"}` so a future app named `cron` or `subagent` is a decision instead of a
silent re-bucketing.

**FALSIFIED, three ways, mutating the live line and restoring from a file copy.**

- `UNWRITTEN_PURPOSES` back to `{"eval", "loop"}` → **3 red**, the load-bearing one reading
  `['loop'] now HAS a turn-ledger writer but is still listed as unwritten, so reachable_purposes()
  hides a row that has real spend. Writer sources found: ['background', 'channel', 'chat', 'cli',
  'cron', 'loop', 'loops', 'subagent']`.
- `found.update(_app_names())` → `pass` in `_writer_sources` → **1 red**, and the reported set
  collapses to exactly the six the old census found — so the app census is what carries the writer,
  not an incidental match.
- `chat_runner.py:3567` → `source="chat"` → **1 red** on the chain hop, so that assertion observes
  the seam rather than restating it.

Restored from `/tmp/*.bak` copies (never `git checkout --`); `git status --porcelain` clean after
each.

**What this does NOT close, and what it changes about the remaining clause.** The loop detail's
`~$X this run` is still unbuilt, but session 3's stated reason for deferring it is now falsified:
loop spend IS attributable, because a turn row carries `session_key` and a loop's worker keys are
`loop-<id>` (`manager.session_key`) and `loop-<id>-<task_id>` (`manager.task_session_key`), so a
prefix-scoped read of `usage/turns.jsonl` yields a loop's exact spend. What remains genuinely open
is the *shape*: `ledger.run_totals` sums `tokens`/`cost_usd` off `step_completed`, and
`loop/journal.py::cycle()` writes neither, so "via `run_totals`" as the clause spells it requires
copying turn dollars into the loop ledger — a SECOND record of one dollar, in a module whose own
docstring argues that a money surface which can double-count is worse than one that admits a gap,
and one that `learning/mining` also reads. The cheaper and more accurate alternative (the loop
detail reading the turn ledger by session-key prefix) is exact and duplicates nothing but is not
`run_totals`. That is an owner shape decision, recorded here rather than guessed. **MRT-3 stays
PARTIAL on that one clause.**

**Not in scope but measured while here:** `loop/plan_walkthrough.py:145` names the planner session
`app="loops"` (plural), which is NOT a `PURPOSE_BY_SOURCE` key, so loop-planning spend is censused
under `app_sources` as `loops` rather than folded into the `loop` purpose. One engine, two spellings,
two buckets. Left alone deliberately — renaming it moves money between buckets, which is a product
call, and the new collision ratchet makes the pair visible instead of invisible.
## Execution log — MRT-3, session 5 (the loop money line, and why it is not `run_totals`)

**The last unmet clause is CLOSED. MRT-3's `done_when` is now satisfied end to end**, with one
recorded deviation on the mechanism.

**DEVIATION — "via `run_totals`" is deliberately NOT how this reads. It is a prefix-scoped read of
the turn ledger.** `ledger.reader.run_totals` sums `tokens`/`cost_usd` off `step_completed`, and
`loop/journal.py::cycle()` writes neither, so `run_totals` over a loop store returns `0.0`. Making
it return money requires copying turn dollars into the loop ledger, and three measured facts make
that the WRONG shape rather than merely the more expensive one:

1. **One dollar would live in two records.** `routing/usage.py`'s module docstring argues exactly
   this case — "a money surface that silently double-counts is worse than one that admits a gap" —
   and there is still no join key between the two stores.
2. **`learning/mining` reads the loop ledger too**, so a dollar minted there becomes an input to
   the flywheel as well as to the cockpit.
3. **It would undercount the tail.** `record_cycle_findings` (`loop/store.py:938`) is the only
   cycle write and it only fires when a NEW finding file appears, so a loop that stops without one
   never gets its last spend attributed. An understated money number is the same defect class as an
   invented one — the reason session 3 refused a half-measure.

Session 3's stated reason for deferring this clause — *"NOTHING ties model spend to a specific
loop, in either record"* — is **falsified**. A turn row carries `session_key`, and `loop/manager.py`
owns both spellings: `session_key` (`:25`, `loop-<id>`) and `task_session_key` (`:342`,
`loop-<id>-<task_id>`). That is the attribution seam, and it was there the whole time.

**Built.** `usage_ledger` gained a `session_prefix` filter (`_session_matches`, threaded through
`_row_selected`/`rollup`/`totals`); `loop.manager.loop_spend(loop_id)` reads it; `api_loop_get`
carries it as `spend` on the loop DETAIL only; the cockpit renders a `MetaPill` beside Elapsed.
Tests: `tests/test_loop_spend.py` (15), `web/src/lib/runCost.test.ts` (16).

- **Both worker key shapes are summed.** A fan-out loop is one figure across the main worker and
  every task worker. Asserted as an exact total ($0.25 + $0.50 + $0.50 = $1.25), because the
  failure is arithmetic: a read matching only `session_key` returns $0.25 and understates the loop
  by 80% while rendering perfectly.
- **The prefix is separator-aware, not a bare `startswith`.** A key matches when it EQUALS the
  prefix or extends it at `-`. `loop.store._LOOP_ID_RE` is `^[a-f0-9]{8}$`, so two real ids cannot
  collide today — but this ledger accepts whatever `session_key` a writer hands it, and borrowing
  safety from a regex in another module is how a widened id shape becomes a silent money bug later.
  Covered by two loops whose keys share a prefix (`loop-abc123` / `loop-abc1234`) in both
  directions, since an over-correction that demanded the separator even for the key itself would
  report $0.00 for the main worker — also plausible, also wrong.
- **DETAIL-only, deliberately.** It is one JSONL scan per loop, so putting it on `api_loop_list`
  would be N scans per poll. Best-effort: a money read must never turn a working cockpit into a 500.
- **The figure is held OUTSIDE the loop entity in the frontend.** `GET /api/loops/{id}` carries
  `spend`; the per-loop SSE snapshot is `store.get_redacted` WITHOUT it (`loop_routes.py:689`), and
  `loopToGoalLoop` spreads the raw loop — so folding `spend` into `c` would make the pill vanish on
  the first lifecycle event, a money figure that disappears while the loop is still spending. Its
  own `useState` means SSE cannot clobber it and the 30s poll refreshes it. Railed by a source
  assertion, because the defect is structural rather than arithmetic.

**The `app="loops"` planning gap is surfaced AT the number, not just recorded here.** The planner
session is keyed `loop-plan-<id>` (`plan_walkthrough.py:107`) — outside the worker prefix — and is
named `app="loops"` (plural, `:145`), so it lands in a different purpose bucket too. It is reported
as a SEPARATE `planning` figure and rendered in the pill's VISIBLE text (`~$1.25 + ~$0.4000
planning`), not hidden in a tooltip. Summing them would overstate "this run"; omitting them would
imply a completeness the number lacks. `loopSpendTitle` states the rest — the turn count, that the
figure spans the task workers, and (when `priced` is False) that the total is a FLOOR rather than a
total. Renaming `loops` → `loop` stays a product call: it would move money between buckets.

**FALSIFIED four ways, each red on a WRONG FIGURE rather than a missing element** — the specific
hazard here, since `~$0.00` renders fine and means something false. Mutations applied to the live
line, re-read, enclosing function confirmed, restored from `/tmp/*.bak` (never `git checkout --`):

| mutation | observed red |
|---|---|
| prefix matches the key only (task workers dropped) | `assert 0.25 == 1.25`, `assert 110 == 330`, `assert 0.0 == 5.0` — the too-LOW shape |
| bare `key.startswith(prefix)` | `assert 3.0 == 1.0`, `assert 6.0 == 1.0` — the too-HIGH shape, one loop swallowing another |
| planning folded into `dollars_est` | `planning must not inflate the run figure`, `assert 1.4 == 1.0` |
| prefix read matches NOTHING | `assert 0.0 == 1.25`, `assert 0 == 330` |

The fourth is the one that proves the test design: under it `loop_spend` returns `0.0`, so the pill
renders cleanly as `no spend recorded` and every presence-only assertion passes. Only the figure
assertions catch it.

**Also moved while here — one rounding rule, two sentences.** `runCostText` left
`IntrospectPanel.tsx` for `web/src/lib/runCost.ts` alongside `runUsd`, `loopSpendPill` and
`loopSpendTitle`. Two surfaces re-deciding how to round a dollar is how `$0.00` and `~$0.0001` come
to mean the same thing in one product, so the 2dp-above/4dp-below rule (`routing/usage.py::_usd`'s)
now lives once and each surface composes its own disclosure on top. A clean break — no re-export
shim; `IntrospectPanel` and its test import from the new home.

**Not swept in:** `docs/design/consistency-audit.json` regenerates as a side effect of the web build
and drifted (`filesScanned` 527 → 540, plus a new `pages/settings/ProjectionRulesPanel.tsx` row)
from atoms that landed without a regen. `driftHits` (8) and `filesWithDrift` (7) are unchanged and
this change's two new `web/` files add no drift, so the artifact was discarded rather than committed.

## Execution log — MRT-3 status reconciliation (2026-08-22)

**DONE — `MRT-3` flips `todo` → `done`.** No code changed. Session 5 above already concluded *"the
last unmet clause is CLOSED; MRT-3's `done_when` is now satisfied end to end"*, but the atom was
never flipped in `docs/roadmap/atomic/MRT.md` or `dag.json`, so the roadmap kept reporting a shipped
capability as unstarted. This session verified the whole contract against `origin/main` (`05bba66e`)
rather than trusting that claim, clause by clause:

| clause | as-built evidence on `origin/main` |
|---|---|
| fold `usage_stats.json`, per-day `date→provider:model→purpose`, 5-purpose vocab | `routing/usage.py:92` `PURPOSES = ("interactive","background","loop","eval","app")`; `_USAGE_FILE = "usage_stats.json"`; `fold_turn_row`, `purpose_for_source`, `reachable_purposes` |
| matches a hand-computed fixture over **50** audit lines, reproducible after delete | `tests/test_routing_usage.py::test_fold_matches_hand_computed_fixture` asserts `len(_fixture_turns()) == 50` (20+12+10+5+3) against hand-computed cells, via `U.rebuild` |
| `GET /api/usage?window=&group=` → `rows` + `total` + `estimated_share` | `dashboard/handlers/usage.py:118` registers `/api/usage`; the handler validates `window ∈ WINDOW_DAYS` / `group ∈ GROUPS` and returns `{rows, total, estimated_share, series, unmapped}` — the clause's exact spelling, not the `/rollup` variant |
| Usage section on Settings→Models, daily/weekly charts | `web/src/pages/settings/UsagePanel.tsx` (417 lines) |
| run/loop detail shows `~$X this run` | `LoopCockpitPage.tsx:543` renders `<MetaPill … text={loopSpendPill(spend)} title={loopSpendTitle(spend)} />` off `api_loop_get`'s `spend` (`loop_routes.py:388`) |
| `usage_recap(month)` verbatim-predictable + one digest notification honoring quiet hours/mute via the rules engine + system cron | `action_providers/usage_recap_provider.py` (deterministic, no model call; `system/usage_recap` defaults to `digest`; monthly cron with `reconcile_usage_recap_cron` re-arm; quiet-hours suppression) |

**The `run_totals` deviation stands as session 5 recorded it** and is not re-litigated here: the loop
money line is a prefix-scoped read of the turn ledger, because `run_totals` sums `step_completed`
which `loop/journal.py::cycle()` never writes, so it returns `0.0` over a loop store. Copying turn
dollars into the loop ledger would put one dollar in two records, feed a minted figure to
`learning/mining`, and still undercount the tail. Recorded as a DEVIATION on the mechanism, with the
clause's user-visible promise (`~$X this run` on the loop detail) met.

**Verified by execution, not by reading.** `tests/test_routing_usage.py`, `test_usage_routes.py`,
`test_usage_recap_delivery.py`, `test_loop_spend.py`, `test_usage_reachable_purposes.py`,
`test_usage_ledger.py` → **115 passed** (each path confirmed to exist first, since a mistyped path
yields "no tests ran" — an unrun leg, not a pass).

**Falsification — three mutations, to prove the suite covers the CLAUSES rather than passing
vacuously.** Each applied to the live line, confirmed applied by grep, restored from a `/tmp/*.bak`
copy (never `git checkout --`):

1. **Prefix match reduced to equality only** (`key == session_prefix`, dropping the separator-extend
   branch) → 7 red, including `assert 0.25 == 1.25` — *exactly* the 80% fan-out understatement
   session 5 predicted, reproduced as arithmetic rather than argued.
2. **Prefix match widened to a bare `startswith`** → 4 red with `assert 12.0 == 3.0` and
   `6.0 == 1.0`, the over-count direction. Both directions matter: an over-correction that demanded
   the separator even for the key itself would report `$0.00` for the main worker.
3. **Dropped `loop` from `PURPOSES`** → 3 red across `test_routing_usage.py` and
   `test_usage_reachable_purposes.py`, pinning the 5-value vocab the fold clause names.

**🪤 A falsification that does not apply is not a falsification.** The first attempt at (1) targeted
a single-line `return key == prefix or key.startswith(prefix + "-")` that does not exist — the real
guard is a multi-line negated condition using `_SESSION_KEY_SEP`. The mutation silently applied to
nothing and the suite stayed green at 15/15, which would have been reported as "the mutation was
absorbed" if the applied-count check had not printed `0`. Grep the mutated form and assert the count
before trusting any red *or* green. See [[a-mutation-that-lands-can-still-be-off-the-property-path]].

- **2026-08-23 — `MRT-5` COMPLETE in code; the feedback SIGNAL is structurally zero today, and that is the
  clause's own declared state. Atom stays `todo` only because this code is unmerged**; flip it when the PR
  lands.
  `MRT-4` shipped `MODES = ("off", "heuristic", "learned")` and `policy.py` named its own gap twice: *"Mode
  `learned` is accepted and folded onto the heuristic here: the learned scoring stage is [MRT-5's]"* (39-41)
  and *"The heuristic floor. `learned` lands here too **until MRT-5 scores the fold**"* (417). Three config
  knobs — `hysteresis` 0.05, `cloud_quality_margin` 0.10, `min_samples` 5 — were parsed and **inert** for
  want of a reader. This is that reader, wired as **Lever 4** of `route_refs`.
  **The 60/40 score was NOT re-derived.** `stats._score`, `_ema` and the `feedback_n: 0` renormalisation
  already existed (`stats.py:44-60`); all three thirds import and call them, asserted by AST with no
  `0.6`/`0.4` literal anywhere in the new modules. Three copies of one formula is how the router and the
  proposal end up disagreeing about which model is better.
  **Hysteresis is a band, and the band is anchored at each band's BEST score.** Chaining off the previous
  member would drift (0.90 → 0.86 → 0.82) and let a cheap, meaningfully worse ref overtake a better one
  through individually-legal steps. Inside a band the incoming order stands and cost may reorder; outside
  it, cost cannot. Probed at integration: `0.90 vs 0.88` (band 0.05) puts the cheap ref first, `0.90 vs
  0.60` does not.
  **`cloud_quality_margin` is a penalty applied BEFORE banding**, so it is asymmetric — an equal-scoring
  cloud ref loses to local. Recorded consequence: with `hysteresis >= cloud_quality_margin` an equal pair
  bands together and the incoming order stands (still never a cloud *promotion*); at the shipped defaults
  it does not bite.
  **Sub-threshold refs keep their incoming slot.** Demoting is a demotion on absent evidence and promoting
  lets one lucky call outrank a long record — and `loader.py:2598-2604`'s own prose says that below the
  floor *"the simple local-first rule stands"*, i.e. the incoming order IS the answer. Verified: a
  1-sample 100% ref does not leapfrog a 20-sample 0.90.
  **Propose-don't-write, asserted on the BYTES.** `propose` reads `routing_policy.json` before and after and
  compares the file, not a key — with a vacuity floor driving a real `save_policy` in the same fixture home
  to prove the harness can see a change. Acceptance is the only writer; a `{"source": "user"}` basis makes
  `accept` return `False` and write **no table**, recording `status="refused"` + a `refusal_reason` so the
  surface can explain rather than appear to do nothing — the only reading that satisfies "writes the
  proposal_id basis" and "does not clobber a user basis" together. A raising SEL leaves an acceptance
  **standing** (the table write already happened; rolling back would discard a human decision to protect an
  audit line, and raising would report failure for a change that occurred).
  **Cooldown keys on `(use_case, query_class, proposed[0])`** — the ref being promoted to first. Keying the
  whole order lets a one-token tail reorder re-nag immediately; keying the pair alone swallows a genuinely
  different finding ("try the *other* local model first") for a fortnight. Durable in the same file as the
  queue, so it survives a restart. An unparseable rejection timestamp reads as **expired**: this module
  never writes the table, so an extra proposal costs one notification, whereas a corrupt byte silencing a
  real finding leaves the router quietly wrong forever.
  **THE FINDING — the feedback signal exists as a KIND and cannot be attributed.** `JUDGE_VERDICT` is real
  (`ledger/kinds.py:76`) and live, but **no producer stamps `(use_case, query_class, ref)`**: verified
  field-by-field against both writers (`workflows/controller.py:2945` carries `instance_path, node_id,
  epoch, template, verdict, status, evidence`; `loop/journal.py:121` carries `cycle` + `JudgeVerdict`),
  zero hits for `use_case`, `query_class`, `model` or `provider` on either. A `step_completed` join on
  `node_id` was available and **was refused**, because the controller stamps the JUDGE GATE's `node_id` —
  that join attributes a PASS to the model that *issued* the verdict, making a lenient judge read as a
  strong worker. `query_class` is computed only in `guardrails/model_call.py:170` and lands only in
  `model_calls.jsonl`, which has no run/node coordinate and no `audit_id` on the ledger side, so there is
  **no join key between the two stores** — the identical gap `MRT-3` recorded for spend, resolved the same
  way: fold what is attributable, census what is not, state the gap. So `feedback_for` returns `(0.0, 0)`
  on every real install and `_score` renormalises onto `success_rate` alone, which is exactly the clause's
  *"renormalizing onto success_rate + feedback_n:0 when absent"*. The extractor picks the fields up the
  moment a producer stamps them; **stamping them is producer-side work this atom does not own.**
  **`quality_score` (0-5) is deliberately unused** — the loop producer carries it and the workflow producer
  does not, so preferring it would make two producers non-comparable inside one cell: a fifth dialect of
  the same measurement. An unrecognised verdict is **dropped**, not neutralised (0.5 is a number nobody
  chose; 0.0 punishes a model for a parser gap), and `RETRY`/`REPLAN`/`ESCALATE`/`NEEDS_INPUT` are dropped
  as control flow. `feedback_n` increments once per `event_id`, only for an attributable `PASS`/`REJECT` —
  its falsification proves why: counting verdict-less events inflated n from 1 to 3, straight through the
  `n >= 5` floor on nothing.
  **Feedback is OVERLAID onto the in-memory fold, never written into `routing_stats.json`.** A judge verdict
  is not a routing observation, and persisting it would make the fold's `feedback` field mean two different
  things depending on which writer touched it last.
  **An unpriced model costs `inf`, not `0.0`.** Zero would make every unpriced cloud model win each
  within-band cost tie — the exact inverse of the local-first posture.
  **THREE fixture bugs found while writing the seam test, each of which would have passed for the wrong
  reason.** Recorded because they are the shape of a green test that measures nothing:
  1. `master_enabled()` gates every mode on `routing.enabled`, whose default AND failure mode are both
     `False` — so without overriding it every mode reads `off`, and an "off returns the bound order" test
     passes while measuring the master switch. The fixture now asserts its own premise.
  2. **The table's mode key is `"mode"`, not `MODE_KEY`.** `MODE_KEY` (`"routing_mode"`) is the
     *settings-store* key the UI writes and `mode_for` consults FIRST; the table's fallback is the plain
     `"mode"`. Writing `MODE_KEY` into the table sets nothing any reader looks at.
  3. **`_use_case_entry` is a READ accessor** — it returns `{}` for an unlisted use case and does not
     insert it, so mutating its result writes into a throwaway dict. Caught immediately by the
     `assert mode_for(...) == mode` guard added after (2).
  Also: in a bare `tmp_path` home `local_keys` is **empty** (local providers come from installed apps), so
  the heuristic is a no-op there — the heuristic-vs-learned contrast test asserts that premise rather than
  assuming a local-first reordering it would never observe.
  **DISCOVERY — routing proposals are not reachable from the unified inbox apply path.**
  `proposals_contract.py` (INU-7) closes its apply-case set to four — `action`, `workflow`,
  `skill_promotion`, `app_callback` — and a routing-order change is none of them. So a proposal surfaces as
  an `INFO` notification plus the `pending()` queue, and accept/reject are API-level today. Wiring it in
  needs a fifth apply case in that file, which belongs to whichever atom owns the routing-proposal UI —
  outside §6.3-6.4 and outside this fence.
  **Notification went through the existing choke point on purpose.** `tests/test_notification_kinds.py`
  AST-sweeps every literal `emit_attention_item(source=…, kind=…)` call and asserts the pair is registered
  (registration also raises on a duplicate at import), so minting a pair would have been a new inventory
  entry; `state.notify(notification_kinds.INFO, …)` is the same accessor `guardrails/rungs.py:488` uses
  from a non-request context, with an already-registered kind.

- **2026-08-24 — `MRT-5` re-audited against `origin/main` (`9e0f727b`): the scoring/ordering two-thirds are
  LANDED AND LIVE; the proposal third is on main but INERT. The atom is PARTIALLY satisfied and must NOT be
  flipped `done` yet.** The 2026-08-23 entry above says "COMPLETE in code … atom stays `todo` only because
  this code is unmerged". The code is now merged (`ae0dc12c`) and that reading does not survive the merge:
  one clause of the `done_when` has no implementation and one has no surface.
  **The merge question is settled — all four decomposition branches are fully landed.** Blob-hash compared
  per file against `origin/main`: `feature-mrt5-learned-routing` (the union: 9 files),
  `-learned-ordering` (`learned.py`, `test_routing_learned_order.py`), `-routing-proposals`
  (`proposals.py`, `test_routing_proposals.py`) and `-feedback-signal` (`feedback.py`,
  `test_routing_feedback_signal.py`) are byte-identical to main on **every source and test file**. The only
  divergence is the union branch's copy of this plan, where main is a strict superset (two line-number
  corrections from other atoms). Nothing is stranded on any of the four; per
  [[stacked-pr-merged-is-not-on-main]] this was decided on content, not on PR state or `git cherry`.
  **MET, with the production importer named.** `stats._score` is the single 60/40 implementation
  (`_W_SUCCESS`/`_W_FEEDBACK` = 0.60/0.40) and `learned._opinion` (`learned.py:112`) calls it rather than
  re-deriving it; `learned_order` is reached in production from `policy.py:397` (Lever 4 of `route_refs`)
  and `feedback_index` from `policy.py:430`. Hysteresis banding, the asymmetric `cloud_quality_margin`
  penalty, within-band cost ordering, the unpriced-model `inf`, and the delete-the-fold degradation are all
  live through that seam. The `≥5` floor is now asserted from BOTH sides at the exact boundary.
  **The ledger hazard does not bite here — checked, not assumed.** `feedback.py` reads exactly one kind,
  `JUDGE_VERDICT`, and `judge_verdict` **IS** in `LEDGER_KINDS` (`kinds.py:177`), so `read_events()` can
  see the records it wants. (Contrast `RUN_STARTED`/`RUN_FINISHED`, verified absent from `LEDGER_KINDS` —
  the reason a run-input reader gets an empty list indistinguishable from "no data". That trap is real, but
  it is not this module's.) The 2026-08-23 attribution finding re-verified and CONFIRMED: the controller
  stamps `instance_path, node_id, epoch, template, verdict, status, evidence` (`controller.py:2945`) and the
  loop stamps `{"cycle": n, **JudgeVerdict.to_dict()}` (`loop/journal.py:121`), while `_cell_of`
  (`feedback.py:176`) requires `use_case` + `query_class` + (`ref` | `provider`+`model`) to be ON the event.
  So every real verdict counts as `unattributed`, `feedback_for` returns `(0.0, 0)`, and `_score`
  renormalizes onto `success_rate` — which is what the clause asks for when feedback is absent. Extraction
  is correct and ready; the producer-side stamp is not this atom's to add.
  **NOT MET (1) — the enqueuer does not exist. `routing/proposals.py` has ZERO production importers.**
  Swept `src/` and `web/` for `propose`/`accept`/`reject`/`pending`/`RoutingProposal`/`routing_proposals`:
  every hit outside the module is an unrelated namesake (`memory_graph`, `packs/fingerprint`,
  `durability/conflict_resolve`). `propose()` is a **queue primitive whose caller was never written** — it
  takes `current`, `proposed` and `evidence` as arguments and computes no gap. Decisively:
  `routing.min_samples` is read at exactly one place in `src/`, `policy.py:420` → the *ordering* path. The
  proposal path never consults it, so the clause *"a genuine quality gap at n>=5 enqueues a routing
  proposal"* has no `n>=5`, no gap detection and no call. Nothing on any real install can ever enqueue a
  proposal today. Cf. [[declared-strategy-without-an-executor]].
  **NOT MET (2) — there is no accept/reject surface, and the entry above is WRONG about this.** It states
  *"accept/reject are API-level today"*. They are not: `model_telemetry.py` registers only
  `GET`/`PUT /api/models/routing-policy` (MRT-4's table), and no handler in
  `src/personalclaw/dashboard/` imports `routing.proposals`. `accept`/`reject`/`pending` are **library
  functions reachable from `tests/test_routing_proposals.py` alone**. §6.3 also asks for the queue to be
  *"Surfaced in the Routing tab (badge count)"*; `RoutingPanel.tsx` has no proposals surface. The narrower
  claim in that entry — that the unified-inbox apply path (`proposals_contract.py`'s four apply cases)
  cannot carry a routing-order change — was re-checked and stands; it is the "API-level" gloss that is
  false. Cf. [[mechanism-present-but-inert]].
  **NEW TEST — the propose-don't-write negative was passing for the wrong reason on the exception path.**
  The shipped suite asserted byte-identity only on the success path. Falsified by replacing the tail of
  `propose` with the textbook leak — stash the table, `set_order`, notify, restore — which restores
  correctly whenever nothing raises: **that leak passed all 31 pre-existing tests, including
  `test_propose_leaves_routing_policy_byte_identical`.** So
  `test_a_raising_propose_still_leaves_routing_policy_byte_identical` injects a fault at `_notify` (the one
  unguarded call in `propose`, every other helper swallowing its own failures) and asserts the table's bytes
  are untouched, plus that the enqueue really preceded the fault. Its own vacuity floor,
  `test_the_byte_harness_can_see_a_real_write_on_the_raising_path`, drives a real `set_order` inside the
  same raising run and proves the comparison can see it — the success-path floor does not cover that.
  Property pinned: `propose` never *opens* `routing_policy.json`, which is stronger than "puts it back".
  **NEW TEST — the `≥5` floor from below.** `test_exactly_min_samples_counts_as_an_opinion` (n=5 reorders)
  is satisfied by a `>= 4` floor too, and the existing sub-threshold tests use n=1/n=2. `test_one_below_
  min_samples_is_not_an_opinion` holds the decisive 0.20-vs-0.95 fold fixed and moves only `n` 5 → 4, so
  the boundary is the single variable. Falsified together: `samples < min_samples` → `samples < 0` turns 3
  red including the new one.
  **The unmet clauses need no invented numbers — §6.3/§5.2 specify all three.** `reproposal_cooldown_days`
  default 14, `cloud_quality_margin` 0.10, `hysteresis` 0.05, and all three match the shipped config
  (`loader.py:5301-5314`, `policy.py:466`). Nothing here is blocked on a threshold.
  **⚠️ OWNER DECISION — the enqueuer's trigger point, which the plan never names.** §6.3 says a crossing
  reordering "enqueues" a proposal and `propose`'s own docstring assumes *"a learned stage calls this on a
  schedule"*, but no such schedule exists and the plan specifies no host job or interval. The three
  candidates are not interchangeable: (a) **at route time** inside `route_refs` — puts a queue read, a
  config read and a file write on the hot path of every model call, which the ordering stage was
  deliberately written to be pure to avoid; (b) **on the stats-fold write** — cheap and already per-call,
  but the fold write is documented as best-effort observability that "must not break a model call"; (c) **a
  scheduled sweep** — matches the docstring and keeps the hot path pure, but needs an interval and a job
  owner that no plan section supplies. Picking one is an architectural call with a latency/behaviour
  consequence, not a number to default, so it is recorded rather than improvised (E2/E6). **Remaining MRT-5
  scope, once that is decided:** the gap detector (fold → `n>=5` → hysteresis-crossing → `propose` with the
  §6.3 evidence payload), and the accept/reject/list API + Routing-tab badge. Both are startable
  immediately after the trigger-point call; neither is blocked on anything else.

## Execution log — two unrailed wires in the routing subsystem (2026-08-24)

**DONE — rails only; no production line changed.** A wire-depth check ("would deleting this caller be
caught?") found two calls in this plan's own subsystem that no test observed. Both calls are correct;
what was missing was any test that would notice their removal. Measured on `origin/main` `2d7f5b6b`:

- **`policy::set_order` at `dashboard/handlers/model_telemetry.py:135`.** Replacing the call with `pass`
  left `PUT /api/models/routing-policy` answering **200 with `applied: ["order"]`** while
  `routing_policy.json` stayed **byte-identical** — a live swallowed write, invisible to the client. The
  whole routing test surface (10 files, **294** tests) stayed green. `set_order` was named in exactly one
  test file, `test_routing_proposals.py`, which is about the *propose* path.
- **`policy::_overlay_feedback` at `routing/policy.py:408`.** Replacing the call with `pass` left the
  learned ordering **unchanged** by a real ledger `judge_verdict` (`['cloudy:big','ollama:small']` both
  with and without a `REJECT`), and the same 294 tests stayed green. Every fold the suite writes carries
  `feedback_n: 0`, and the overlay is the only thing that ever raises it — so `_score` renormalized onto
  `success_rate` alone and every pre-existing assertion still held.

**NEW RAILS — the effect, not the call.** `tests/test_routing_telemetry.py::TestRoutingPolicyWrite` (9
cases) drives the real route through `TestClient` and reads the order back **out of the JSON on disk**,
deliberately not through `policy.table_order`: a reader's own "no order recorded → the input order"
fallback must not be able to stand in for a persisted one. Monkeypatching `set_order` and asserting it was
called was rejected as the weaker form — it passes for a handler that faithfully calls a function which
writes nothing, which is the exact failure being ruled out. Both directions are covered: an accepted PUT
persists the sent sequence (order-sensitive, and scoped to the `query_class` it was sent — the other class
is asserted untouched, since `set_order`'s signature is `(use_case, query_class, order)`), and five
**rejected** shapes each leave the table byte-identical.
`tests/test_routing_learned_wiring.py` gains three overlay rails asserting the resulting ORDER: `REJECT`
demotes, `PASS` promotes (both directions, so a hardcoded `feedback: 0.0` cannot pass), and a verdict
stamped for another `query_class` must not steer this one.

**Two vacuity floors.** (1) `test_the_byte_harness_can_see_an_accepted_write` — without it the five
byte-identity rails would hold for a handler that persisted nothing at all; it is the first thing to go red
when the write is swallowed. (2) Each overlay rail asserts the same fold with an EMPTY ledger first
(`feedback_index(home=home) == {}`), so the flip is attributable to the feedback rather than to the fold.
The `_judge_verdict` helper additionally asserts `feedback_index` actually *attributed* its event — the
attribution rule is strict and `routing/feedback.py` records that no shipped producer stamps the triple
yet, so a rail built on an unattributable event would drive an empty overlay and pass either way.

**Fixture correction that mattered.** The handler calls `set_order(...)` with no `home=`, so the write
resolves through `policy._default_home()` → `from personalclaw.config import config_dir`, a binding
`config/__init__.py` made at import time. The file's pre-existing `_home` fixture patches only
`personalclaw.config.loader.config_dir`, which does **not** reach it. The new `policy_home` fixture patches
both bindings and asserts the redirect. `tests/conftest.py`'s real-home rail confirms `~/.personalclaw` was
untouched by every run.

**Falsified.** Each call mutated on the live line (`pass  # MUTATION: …`), the mutation grepped back to
confirm it applied, red observed, restored from a file copy taken beforehand: `set_order` → 4 of 9 red
(including the vacuity floor); `_overlay_feedback` → both directional rails red. Gate: `make lint` clean,
`make test` **25734 passed / 30 skipped / 12 xfailed**, `gate_report.py` 6/6 PASS, probe sweep 16
pre-existing / 0 introduced.

**MRT-5's propose-don't-write rail still holds** — `test_routing_proposals.py` is green including its
raising-path byte-identity case. These rails cover the *write* path, which is legitimately allowed to write.

**Correction to the framing:** the endpoint is a **PUT**, not a PATCH (`add_put`, and its docstring calls
the per-lever behaviour "PATCH-like"). Nothing in the routing subsystem registers a PATCH.

## Execution log — wire-depth sweep of the remaining routing wires (2026-08-24, DISCOVERY)

**DISCOVERY slot — measurement only, nothing fixed.** Continues the sweep logged above over the wires
that outing did not reach. `tools/audit_landed_atoms.py --check-wires` is **already on `main`**
(`b96217e1`, hardened by `0737ccfc` + `7aad0281`); PR #1989 reads `CLOSED / mergedAt:null` because the
merge train cherry-picked it, which is exactly the trap the tool's own docstring warns about. Nothing was
brought in.

**Wire census, read-only, before any mutation** (via the tool's own `find_wires`, no tree write):
MRT-5 derives **9** wires from 4 annotated modules, not the 6 the brief assumed remained; APE-3 derives
**24** from 6 modules, not 22. PHF-7 derives exactly **1**.

**Ground-truth pair had to be re-sourced.** The brief's stated known-answer pair (APE-3 UNRAILED on
`main`, RAILED on `improvement-ape3-close-or-record`) is stale in both halves: the rail landed on `main`
as `860c2c1d`, and the branch no longer exists on `origin`. The still-live UNRAILED case on `main` is
PHF-7's `registry::sync_entries_from_config` — its fix sits in PR **#1994, still OPEN**, so both call
sites (`dashboard/server.py:1317` and `:1491`) are still there.

**Both directions of the checker reproduced on `main`** before any backlog wire was scored:
`MRT-5 policy::set_order` → **RAILED** (4 red, the rails the entry above added), and
`PHF-7 registry::sync_entries_from_config` → **UNRAILED** (257 selected tests green). A checker that
can only ever say UNRAILED is unusable; this one distinguishes its own known-answer cases.

**MRT-5's seven remaining wires: 3 RAILED, 3 UNRAILED, 1 REFUSED.**

UNRAILED — each a plain side-effect call statement, so each is a *measurement*, not one of the
locator's blind shapes:

- **`policy::set_mode` at `dashboard/handlers/model_telemetry.py:119`** — 601 selected tests green
  (1 skipped / 600 passed). Bound: 10 further scored files cut by the cap, and **1003 of 1017 test
  files overall not run**.
- **`policy::set_pin` at `dashboard/handlers/model_telemetry.py:122`** — 449 selected tests green.
  Bound: 0 cut by the cap, **1004 of 1017 test files overall not run**.
- **`policy::_sel_policy_change`, 3 sites** (`routing/policy.py:604` mode, `:617` pin, `:654` order)
  — 449 selected tests green. Bound: 0 cut by the cap, **1004 of 1017 overall not run**.

The selection bound does **not** weaken the first two, and this is checkable without a suite run:
`git grep -l routing-policy -- tests/` returns exactly **one** file, and
`tests/test_routing_telemetry.py`'s write section is headed *"the write path: PUT
/api/models/routing-policy, `order` lever (§6.2)"*. The `mode` and `pin` levers of the same PUT have
no rail **anywhere in the suite**, not merely outside this selection.

Both calls are **correct today** — the swallow shape is the same one the `order` lever actually had:
delete the call and the handler still answers **200 with `applied: ["mode"]` / `["pin"]`** while
`entity_settings` stays byte-identical, invisible to the client. Note these two levers persist through
`providers.use_cases.save_use_case_settings`, a *different* store from `set_order`'s
`routing_policy.json`, so the rails added above cannot cover them by accident. A fix asserts, per
lever: the PUT persists the value and a re-read returns it, a rejected value leaves the store
byte-identical, and a vacuity floor proving the byte harness can see an accepted write.

`_sel_policy_change` is an **audit-trail** gap rather than a user-visible one: every lever change
emits a SEL row and nothing observes it. The asymmetry is the finding — `proposals::_sel_decision`
**is** railed (`test_accept_logs_exactly_one_sel_row_naming_the_proposal`), so proposal *decisions*
are audited-and-asserted while direct lever changes are audited-and-unasserted. That matters because
MRT's own roadmap position is a hard dependency on Guardrails' audit. A fix asserts one SEL row per
lever naming the use case, the lever and the new value, with an empty-ledger floor.

RAILED (these bound the problem — the routing *proposal* path is genuinely covered):
`proposals::_notify` (`proposals.py:351`) → 2 red; `proposals::_save_queue` (`:404`, `:423`) → 4 red;
`proposals::_sel_decision` (`:424`, `:455`) → 1 red.

REFUSED — **`policy::save_policy` at `routing/policy.py:653`**: the mutated run produced **9
collection/setup errors** alongside 6 failures, so the tool reported *"that measures the mutation, not
the wire"* rather than scoring it. Correct behaviour: `save_policy` is the persistence primitive
`set_order` and the proposal accept path both stand on, so neutralising it breaks fixtures that *set
up* through it. This is the collection-error guard, **not** the ANSI/`FAILED `-prefix attribution trap
— that one is a different guard and was not hit.** Scoring this wire needs a selection that does not
build state through `set_order`.

### Confirmation pass — `set_mode` re-measured with nothing cut

`set_mode` was the one MRT-5 UNRAILED verdict with a non-zero cut (10 files). Re-run at
`--max-test-files 50`: **`cut=0`, 24 files selected, 883 selected tests green, still UNRAILED.** It is a
measurement. `set_pin` and `_sel_policy_change` already had `cut=0` on the first pass, so all three MRT-5
UNRAILED verdicts now stand un-cut. (The same re-run flipped 8 of 11 APE-3 verdicts — see
`APP-PLATFORM-EVOLUTION.md`; an UNRAILED with a non-zero cut is not a result.)

**PHF-7's `registry::sync_entries_from_config` could not be confirmed.** At the tool's default cap it is
UNRAILED (257 green, **33 cut**); re-run at cap 50 with `cut=0` it **REFUSED** on a red baseline —
1 skipped / 5 failed / 945 passed, first reds in `tests/test_acp_session_activity_line.py` and
`tests/test_acp_unattended_and_loop_breaker.py`, which are ACP suites unrelated to the wire and outside
this session's changes (the tree was clean and no production line was touched). So that verdict stays
**provisional**: UNRAILED at cap 14, unconfirmed at `cut=0`. Its fix is already in PR **#1994** (still
OPEN — it is *not* on `main`, contrary to the briefing that said to expect RAILED there).
## Execution log — the routing-policy `mode` + `pin` levers, and a 400 that persisted (2026-08-24)

**DISCOVERY.** The sequel to the section above. `PUT /api/models/routing-policy` has three levers; the
`order` lever's swallowed write was railed there. A wire-depth sweep then measured the other two as
unrailed — `policy::set_mode` (`model_telemetry.py:119`) and `policy::set_pin` (`:122`), both green with
the wire deleted. Verified independently and without a suite run: `git grep -l 'routing-policy' -- tests/`
returns **one** file, whose only write section is headed *"the `order` lever"*. Since `set_mode`/`set_pin`
persist through `save_use_case_settings` (`extensions/use_case_settings/<uc>.json`) — a **different store**
from `routing_policy.json` — the `order` rails could not have covered them by accident.

**Both levers DO persist.** Measured by driving the real route against a redirected home and reading the
settings file off disk: mode `off→heuristic→learned`, pin set, and `pin: ""` correctly *removes* the key.
So this was not a second swallowed write.

**DEFECT (the headline) — a 400 that had already moved the store.** Validation was interleaved with
application, so a body carrying a valid lever beside an invalid one persisted the valid one and *then*
rejected the request. Measured, two distinct 400s:

- `{use_case, mode: "learned", order: "notalist"}` → `400 order must be a list of refs`, with
  `routing_mode` moved `learned → heuristic` on disk.
- `{use_case, pin: "clobber:ref", order: ["p:a"]}` (no `query_class`) → `400 query_class is required`,
  with `routing_pin` moved `seed:ref → clobber:ref` on disk.

The client sees `applied` absent and reverts nothing while the table has changed under it — the mirror
image of the `order` bug (a 200 backed by nothing) rather than a repeat of it. **Fixed** by validating every
lever before applying any. The levers straddle two stores, so there is no single write to make atomic; the
fix is to have nothing left to reject once the first write goes out. The residual (`set_pin` raising *after*
`set_mode` committed → 500 with mode persisted) is a disk-failure path, not a reachable input path, and is
noted in the handler docstring rather than papered over with hand-rolled two-store rollback.

**Rails** (`tests/test_routing_telemetry.py`, +14 tests → 35 in the file; the whole routing surface is
**352 passed** across 11 files). Per lever, asserting the **persisted** value read straight out of the JSON
— never the response body, and never through `policy.mode_for`/`pin_for`, whose defaults (`"off"`/`""`)
would let a reader's fallback stand in for a value never written:

1. the PUT persists, and the sibling lever is left alone (the store is a read-modify-write of one dict);
2. a rejected value leaves **both** stores byte-identical — five parametrized bodies including the two
   regressions above;
3. a vacuity floor proving the byte harness can see an accepted write.

Plus `policy::_sel_policy_change`, folded in: all **three** call sites (`:604`/`:617`/`:654`) emitted an
audit row that nothing observed, while proposal *decisions* were audited **and** asserted — so the audit
trail for the levers a user actually moves rested on nothing, which matters because MRT's roadmap position
is a hard dependency on Guardrails' audit. One row per change, `resources` asserted whole (`use_case:value`,
and `use_case:class:refs` for `order`), with an empty-ledger floor: a rejected change adds no row, and the
same test then requires an accepted change to add one, so the rail cannot pass vacuously.

**Fixture.** Reuses the existing `policy_home` (which re-points both `config_dir` bindings) rather than
adding patches on top of `conftest.py:133-137` — and **asserts the redirect** it depends on
(`use_cases._settings_dir() == home/"extensions"/"use_case_settings"`), because `save_use_case_settings`
reaches for `config_dir` through its own function-local import. `conftest.py`'s real-home rail confirms
`~/.personalclaw` was untouched by every run.

**Falsified.** Each wire mutated on the live line, grepped back to confirm the mutation applied, red
observed, restored from a file copy taken beforehand (literal path, never `git checkout --`):
`set_mode` → **4 red** (incl. both vacuity floors); `set_pin` → **4 red**; all three `_sel_policy_change`
sites → **5 red**; and reinstating the pre-fix interleaved validate/apply body verbatim from the base →
**exactly the 2 regression params red**, nothing else. The first splice of that last mutation dropped the
final `return`, producing 500s — a botched mutation, not a falsification; repaired and re-run.

**Gate.** `make lint` clean (black/isort/flake8/mypy, 1001 source files). `make test` **26142 passed / 30
skipped / 12 xfailed**, one red: `test_loop_worktree_sparse::TestPoolBound::test_batch_creates_every_worktree`
— the known sparse-cone flake, **1 passed** re-run isolated with `-n0`, and it touches no routing code.
`gate_report.py` 6/6 PASS. Probe sweep 16 pre-existing / **0** introduced. No `web/` change.

**The sweep's line numbers were all exact** against `03729754` — `:119`, `:122`, and the three SEL sites
at `:604`/`:617`/`:654`. No drift to correct.

## Execution log — MRT-5's missing executor: the gap detector, its trigger point, and the review surface (2026-08-25)

**DONE — the two NOT-MET clauses of the 2026-08-24 audit are built, and its ⚠️ OWNER DECISION is
answered.** Base `origin/main` `20488b9e`, branch `improvement-mrt5-close`, commit `6335c929`.

**Branch triage first — both leftover local branches are ALREADY ON MAIN and were deleted from the
work plan, not landed.** Decided on CONTENT, per [[stacked-pr-merged-is-not-on-main]], never on PR
state or `git cherry`:

- `feature-mrt5-learned-ordering` (`7850420e`) — `routing/learned.py` blob
  `5edc24cd` **byte-identical** to `origin/main`; `tests/test_routing_learned_order.py` is a strict
  SUBSET of main's (`git diff branch main` = **13 added / 0 removed**, main's copy carrying the
  later `≥5`-from-below rails). Verdict: **already-on-main, stale.**
- `feature-mrt5-routing-proposals` (`9c289d8c`) — `routing/proposals.py` blob `d28994c9`
  **byte-identical**; `tests/test_routing_proposals.py` again a strict subset (**45 added / 0
  removed** — main has the raising-path byte-identity case the branch predates). Verdict:
  **already-on-main, stale.**

Nothing was cherry-picked. Rebuilding either would have been a second rail over a live invariant.

**⚠️ OWNER DECISION — RESOLVED: the enqueuer runs on the STATS-FOLD WRITE** (option (b) of the
2026-08-24 entry), from `stats.record_routing_stats` via a named `_check_for_gap` helper into the new
`routing/gap.py`. Four reasons, in the order they decided it:

1. **A proposal is a function of the evidence, not of a request.** The fold write is the one moment
   new evidence arrives, so it is the only moment a gap can newly appear.
2. **A proposal must reach a user who never opens the Routing tab.** That disqualifies the cheapest
   variant considered — sweeping when the proposals list is read — because its notification would
   only ever fire while the user was already looking at the surface it points to.
3. **Route time (option (a)) is worse than the entry above assumed, and measurably so.** `route_refs`
   already pays `AppConfig.load()`, a fold read, `feedback_index`'s walk of
   `workflows/runs/*/events.jsonl` and the rate table **per call** (`policy.py:397-421`), so "keep the
   hot path pure" is not the live property; what route time would ADD is a queue read plus a write on
   the path a model call waits on, AND running the learned stage on the lever-3 branch that currently
   short-circuits before it — new work on every call for every cell that already has a recorded order.
4. **The module was written for this call site.** `proposals._notify`'s own docstring says it is
   "reached from a fold, not a request", which is why it fetches the dashboard state through the
   process-wide accessor instead of a request object. Option (c)'s missing interval and job owner
   stayed missing; no scheduler was invented.

**What a gap IS — the definition that makes propose-don't-write meaningful rather than noisy.**
*The learned finding is not what routing will actually do.* `current` is `route_refs`' answer
(whichever lever wins) and `proposed` is the learned stage applied on top of it, so:

| state | `current` | proposal? |
|---|---|---|
| `learned`, no recorded order | the learned order (lever 4 is live) | **no** — the machine does not ask permission for what it already does |
| `heuristic` | the heuristic floor | **yes** — a proposal is the only route to the table |
| any non-`off` mode, recorded order the evidence outgrew | the recorded order (lever 3 short-circuits lever 4) | **yes** |
| `off`, or a pin set, or master switch off | — | **no** — the order could not take effect |

`learned_order`'s **idempotence** is load-bearing for row 1 and is asserted, not assumed
(`test_the_learned_stage_is_idempotent`): a stage that permuted its own output would propose against
itself forever.

**Every floor is borrowed, never restated.** `n >= min_samples` is `learned._opinion`'s floor (which
is `stats._score` behind it), and the quality floor is the same `hysteresis` band the ordering stage
bands by. A **within-band** difference is deliberately NOT a gap: cost is the only thing allowed to
reorder near-equals (§5.2), and a cost preference must not nag a user about their table. So
`routing.min_samples` now has a second reader — the proposal path — which is what the audit found
missing ("read at exactly one place, the ordering path").

**Evidence (§6.3) is built from the EXISTING read model, not a second derivation.**
`telemetry.telemetry_rows` supplies p50 and cost for the same bucket the Routing tab renders, so a
proposal's numbers cannot disagree with the tab's; `sample_audit_ids` come from the same bounded
`read_recent` tail, newest first, this cell and these two refs only. Payload: `n` and `scores`
per ref, the three floors that applied, `p50_delta_ms`, `cost_delta_usd`, `sample_audit_ids` — all
numbers, dicts of numbers, or id lists, because `_clean_evidence` FENCES any other string and would
mangle a ref into an untrusted-text block. **Documented deviation from the §6.3 example:** `n` is
per-ref rather than the example's scalar — a scalar hides which ref is thin, which is the one thing
the floor exists to expose.

**The surface (§6.3's "surfaced in the Routing tab") — three routes and a section.**
`GET /api/models/routing-proposals` (fail-open to an empty queue), `POST …/{id}/accept`,
`DELETE …/{id}` — the shape of `/api/learning/proposals`, the tree's other propose-only queue, so
"dismiss" means the same verb in both places. Accept distinguishes its two falsehoods: an id that is
not pending is a **404**, a REFUSAL (hand-set basis) is a **200** with `applied:false` and the
recorded reason, because a refusal is a correct answer to a legitimate request and the surface has to
say why rather than appearing to do nothing. `RoutingPanel.tsx` grows a "Proposed routing changes"
section that renders EVEN WHEN EMPTY — one line saying routing proposes rather than rewrites, since a
section that appeared only once there was something to accept would never teach the property. §6.3's
badge count lands as a sentence ("2 proposed changes waiting on you. Routing measured these — it has
not applied them"), because the number is only meaningful beside what it means.

**Wire census: MRT-5 now derives 14 wires, not 9** — annotating `stats.py` with §6.3 pulled its three
pre-existing wires (`record_routing_stats`, `fold_record`, `save_stats`) into this atom's scope and
added two new ones (`_check_for_gap`, `detect_gap`). All 14 answer YES to "would deleting the caller
be caught?": the nine previously scored (`set_order`, `_overlay_feedback`, `set_mode`, `set_pin`,
`_sel_policy_change`×3, `_notify`, `_save_queue`, `_sel_decision`) plus `record_routing_stats`
(railed by the pre-existing `TestLiveHookThroughGuard`), `fold_record`/`save_stats` (round-trip +
rebuild), and the two new ones railed here. **`policy::save_policy` at `policy.py:653` — the wire the
tool REFUSED to score, because a mutated run broke fixtures that set up THROUGH `set_order` — is now
RAILED**: neutralised, it turns **6 tests red with ZERO collection errors**, because the accept path
reaches it through a proposal rather than through a fixture that builds state with it.

**Clause-by-clause.** `0.60*success_rate + 0.40*feedback` with the renormalisation — MET (unchanged,
`stats._score`). `n>=5` gap enqueues a proposal — **MET, now with a caller.** Inspectable evidence —
MET. **WITHOUT editing `routing_policy.json`** — MET and falsified end-to-end. Accept updates the
table with a `proposal_id` basis + a SEL row — MET (asserted off disk, through the route). Reject
suppresses re-propose for `reproposal_cooldown_days` — MET (asserted by re-proposing after a
rejection). Cost only reorders within-hysteresis near-equals — MET (unchanged), and now also the
reason the proposal path is quality-only. SC #8 degradation with nothing writing `memory.db` /
`knowledge.db` — MET, with the negative given a floor.

**Falsified — four mutations, each applied on the live line, grepped back to confirm, red observed,
restored from a file copy at the literal path (never `git checkout --`):**

1. **propose-don't-write.** `policy.set_order(...)` spliced in before the `propose` call in
   `detect_gap` → **2 red**, including `test_the_byte_harness_can_see_the_write_that_accepting_makes`.
2. **SC #8's db negative.** `sqlite3.connect(home/"memory.db")` at the top of `_check_for_gap` →
   `assert ['memory.db'] == []` **red**. The negative is live, not vacuous.
3. **SC #8's degradation half.** `mode_for`'s unknown-value fallback `"off"` → `"learned"` →
   `assert 'learned' == 'off'` **red**.
4. **The wire itself.** `_check_for_gap(stats, rec, home=home)` → `pass` → **6 red**, including the
   byte-identity test's own guard *"nothing was proposed — this would pass vacuously"*, which is the
   assertion that stops that rail from passing for the wrong reason.
   Plus the fifth above: `save_policy` in `set_order` → `pass` → **6 red**.

**Gate.** `make lint` clean (black 2073 files / isort / flake8 / mypy 1018 sources). Targeted pytest,
paths `ls`-verified first: the whole routing surface + the reference ratchet (13 files) **368 passed**;
the model-call-guard suites (5 files, the fold's producer side) **91 passed**; `test_routing_proposal_gap.py`
**32** and `test_routing_proposals_api.py` **10**. `scripts/gate_report.py` **6/6 PASS**.
Web: `npm run typecheck:web` clean, `npm run test:web` **488 files / 5189 passed** (the full suite, not
a path-scoped run), `npm run build` clean. Probe sweep **16 pre-existing / 0 introduced**;
`git status --porcelain` empty; no `.bak` in the tree; conftest's real-home rail reported
`~/.personalclaw` unchanged on every run, and `routing_proposals.json` does not exist there.

**One collision found and fixed while building the surface.** `RoutingPanel`'s new status region was
initially `sr-only` at rest, which shadowed `RoutingPolicySection`'s reorder announcement for
`routingReorderAnnounced.test.tsx`'s "first `role=status` with `sr-only`" lookup — a real ambiguity,
not a test artifact. The proposals region is now visible-when-spoken and bare-when-empty (it must be
visible anyway: the row it describes is gone after the reload, so it is the only confirmation ANY
user gets). `routingReorderAnnounced.test.tsx`'s api mock also gained `routingProposals` — a module
mock has to answer every call the component makes.

**Left for the owner.** ① The atom's `todo` → `done` flip lives in `docs/roadmap/atomic/dag.json`,
untouched here by instruction. ② The feedback signal is still structurally zero on every install (no
`judge_verdict` producer stamps `use_case`/`query_class`/`ref`), so `_score` renormalises onto
`success_rate` — correct per the clause, and the producer-side stamp remains EXT's, not this atom's.
③ A user who never triggers unattended model calls never accumulates a fold, so the queue stays empty
by construction — that is the mechanism working, not a gap.
- [2026-08-25][MRT-5] ✅ **ATOM FLIPPED `done` (PR #2066).** Integration re-gated the branch on
  `origin/main` = `20488b9e` rather than inheriting: `make lint` pass (black 2073, mypy 1018 sources);
  `pytest` 274 passed over 8 routing suites, 27 over chokepoint+guard, 53 over the reference/alias
  ratchet; `scripts/gate_report.py` 6/6; `npm run test:web` **488 files / 5189 passed** (full suite),
  `typecheck:web` + `build` green; probe sweep 16 pre-existing / 0 introduced. **Falsification re-run
  independently:** cutting the enqueuer wire at `routing/stats.py:153` (`_check_for_gap(...)` → `pass`,
  grepped back to 1 remaining hit = the `def` only) produced **6 red**, and the load-bearing one is
  `test_the_fold_write_leaves_routing_policy_byte_identical` failing with *"nothing was proposed — this
  would pass vacuously"*. Without that floor the propose-don't-write assertion would have gone **green
  with the wire cut** — the vacuity requirement earning its place, not decorating the test.
  **Both unpushed MRT-5 branches were adjudicated STALE on blob identity** (`routing/learned.py`
  `5edc24cd` and `routing/proposals.py` `d28994c9` are byte-identical to main; both test files strict
  subsets), so nothing was cherry-picked and no authorship was lost. The flip is justified against the
  criterion: the trigger-point choice was an in-scope implementation decision resolved with a
  measurement (route time already pays `AppConfig.load()`, a fold read, `feedback_index`'s events walk
  and the rate table per call, so "keep the hot path pure" was already false), and the structurally-zero
  feedback signal is the path the clause **explicitly permits** (renormalise onto `success_rate` with
  `feedback_n: 0`) with the producer-side stamp owned by the EXT dep.
