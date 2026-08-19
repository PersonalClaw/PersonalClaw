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

- **No new provider TYPE.** Routing is substrate at the resolution seam, same deliberate stance as guardrails ("no space provider type", `providers/registry.py:555`). Nothing registers through `_TypeHandler`s; `PROVIDER_TYPES` (manifest.py:453) is untouched.
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
  worker's turns run through `_run_chat` (`gateway.py:2137`, with `_app == "loop"` at `:2120`) →
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
