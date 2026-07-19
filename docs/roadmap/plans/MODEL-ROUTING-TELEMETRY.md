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
