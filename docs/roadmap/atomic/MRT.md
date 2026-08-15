# MODEL-ROUTING-TELEMETRY — atomic plans

**Source plan:** [`MODEL-ROUTING-TELEMETRY`](../plans/MODEL-ROUTING-TELEMETRY.md)  
**Code:** `MRT`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `MRT-1` | ⬜ | Query classifier + telemetry read model + Pareto/Efficiency view | `EXT:AUTONOMY-GUARDRAILS:model_calls.jsonl attempt audit + ModelCallGuard seam (built in guardrails/audit.py + model_call.py)` | routing/classifier.py:classify_query(text,use_case) maps into the fixed 5-class vocab (short_chat\|code\|summarize\|extract_structured\|long_reasoning, classifier_version stamped); query_class field threaded through ModelCallGuard onto model_calls.jsonl; routing_stats.json fold (EMA, keyed use_case->query_class->ref) matches a recorded-audit fixture and is rebuildable via --rebuild-routing-stats; GET /api/models/telemetry returns per-model rows with n, success/feedback, p50/p95, cost/call, on_frontier dominance flag; Settings->Models 'Routing & Efficiency' tab renders scatter+table with local rows at $0 + real latency; with routing absent, resolution order/latency/tests unchanged (SC #1, #2) |
| `MRT-2` | ✅ | Shared pricing rate table (rate_for + model_rates.json + BrandedProviderSpec.pricing) | — | routing/rates.py:rate_for(provider,model) resolves effective rate as overlay > app default > absent; BrandedProviderSpec (sdk/provider_helpers.py) gains optional pricing map that round-trips; ~/.personalclaw/model_rates.json (atomic_write) overrides app defaults and editing it changes rate_for output with no restart-order dependency; local providers price 0.0 (SC #7). Cross-plan: offered as the shared impl to AUTONOMY-GUARDRAILS SpendMeter dollars_est and WF2-R13 cost_usd (both already built) — adoption, not a blocker. |
| `MRT-3` | ⬜ | Usage/spend read model — fold, /api/usage, Usage UI, monthly recap | `MRT-1` | routing/usage.py folds usage_stats.json (per-day keyed date->provider:model->purpose with interactive\|background\|loop\|eval\|app mapping) matching a hand-computed fixture over 50 audit lines and reproducible after delete via the shared §1.3 rebuild; GET /api/usage?window=&group= returns rows+total+estimated_share; Usage section on Settings->Models renders daily/weekly charts and a run/loop detail shows '~$X this run' via run_totals; usage_recap(month) template renders verbatim-predictable and delivers one digest-mode notification honoring quiet hours/mute via the rules engine + system cron |
| `MRT-4` | ⬜ | Heuristic router + policy table + RoutingConfig | `MRT-1` | routing/policy.py:route_refs is a pure reorder called at resolve_provider_for_use_case step (2), enabled per-use-case only, with model_override (step 1) and native-agent (step 0) bypassing it; binding local+cloud on reasoning and enabling heuristic routes background one_shot_completion local-first, and killing ollama produces cloud-rescued calls stamped routed_fallback:true (distinct from degraded) within one breaker window with no stacked timeouts (SC #3); model_override and per-use-case pin bypass/short-circuit, verified by routed provenance (SC #4); unresolvable pinned first-ordered ref still raises ProviderResolutionError and a ref removed from active_models.json drops from candidates on next load (SC #6); routing_policy.json + read-only table UI with mode/pin/reorder controls; RoutingConfig wired through all 4 points (test_config_roundtrip green) |
| `MRT-5` | ⬜ | Learned policy + cost-aware ordering + propose-don't-write proposals | `MRT-4`, `MRT-1`, `MRT-2`, `EXT:WORKFLOWS-V2:step_completed ledger events for the feedback signal (soft; router functions from model_calls.jsonl alone; already built)` | per-(use_case,query_class,ref) score=0.60*success_rate+0.40*feedback over the fold with feedback extracted from WF2 ledger/eval_judge (renormalizing onto success_rate + feedback_n:0 when absent); a genuine quality gap at n>=5 enqueues a routing proposal (skills/proposals.py pattern) with inspectable evidence (scores, n, latency/cost deltas, sample_audit_ids) WITHOUT editing routing_policy.json; accept updates the table with proposal_id basis + SEL entry, reject suppresses re-propose for reproposal_cooldown_days (SC #5); cost only reorders within-hysteresis near-equals with cloud needing to beat local by cloud_quality_margin; deleting routing_stats.json/routing_policy.json degrades to heuristic/off and nothing writes memory.db/knowledge.db (SC #8) |

## Atom scopes

### `MRT-1` — Query classifier + telemetry read model + Pareto/Efficiency view

**Status:** todo

§1 Telemetry Read Model (§1.1-1.5), §2 Query Classification; Session 1 first half (§9)

**Done when:** routing/classifier.py:classify_query(text,use_case) maps into the fixed 5-class vocab (short_chat|code|summarize|extract_structured|long_reasoning, classifier_version stamped); query_class field threaded through ModelCallGuard onto model_calls.jsonl; routing_stats.json fold (EMA, keyed use_case->query_class->ref) matches a recorded-audit fixture and is rebuildable via --rebuild-routing-stats; GET /api/models/telemetry returns per-model rows with n, success/feedback, p50/p95, cost/call, on_frontier dominance flag; Settings->Models 'Routing & Efficiency' tab renders scatter+table with local rows at $0 + real latency; with routing absent, resolution order/latency/tests unchanged (SC #1, #2)

### `MRT-2` — Shared pricing rate table (rate_for + model_rates.json + BrandedProviderSpec.pricing)

**Status:** done

**DONE** — `routing/rates.py` owns the table. `rate_for(provider, model)` resolves through a total, explicit precedence: **overlay** (`~/.personalclaw/model_rates.json`, `atomic_write`, stat-keyed per call so a live edit lands with no restart-order dependency) > **local** (a local provider is a known `0.0`, SC #7) > **app default** (`BrandedProviderSpec.pricing`, read from the live app registration via the new `spec_pricing()`) > **builtin** (core's shipped `model_pricing.json`, through the public `pricing` API) > **absent = `None`**. Absent is `None`, never a fabricated `0.0` — a zero would report an unpriced cloud model as free. A corrupt/foreign-shaped overlay fails open to the next tier (log + continue) and never breaks a routing decision. `BrandedProviderSpec` gained the optional `pricing` map plus `to_dict()`/`from_dict()` round-trip parity (`hash=False` keeps the frozen spec hashable). `cost_for()` is the shared helper offered to SpendMeter `dollars_est` and WF2 `cost_usd`; per the atom, that adoption is deliberately NOT part of this scope, so the two existing call sites still use `pricing.estimate_cost` and no consumer reads `rate_for` yet.

§5.1 Where pricing lives (this plan owns the rate table)

**Done when:** routing/rates.py:rate_for(provider,model) resolves effective rate as overlay > app default > absent; BrandedProviderSpec (sdk/provider_helpers.py) gains optional pricing map that round-trips; ~/.personalclaw/model_rates.json (atomic_write) overrides app defaults and editing it changes rate_for output with no restart-order dependency; local providers price 0.0 (SC #7). Cross-plan: offered as the shared impl to AUTONOMY-GUARDRAILS SpendMeter dollars_est and WF2-R13 cost_usd (both already built) — adoption, not a blocker.

### `MRT-3` — Usage/spend read model — fold, /api/usage, Usage UI, monthly recap

**Status:** todo

Amendment (2026-07-26) — the usage story: T-U1 (usage fold + route), T-U2 (usage UI + per-run cost), T-U3 (monthly recap)

**Done when:** routing/usage.py folds usage_stats.json (per-day keyed date->provider:model->purpose with interactive|background|loop|eval|app mapping) matching a hand-computed fixture over 50 audit lines and reproducible after delete via the shared §1.3 rebuild; GET /api/usage?window=&group= returns rows+total+estimated_share; Usage section on Settings->Models renders daily/weekly charts and a run/loop detail shows '~$X this run' via run_totals; usage_recap(month) template renders verbatim-predictable and delivers one digest-mode notification honoring quiet hours/mute via the rules engine + system cron

### `MRT-4` — Heuristic router + policy table + RoutingConfig

**Status:** todo

§3 Routing Seam & Candidate Pool, §4.1 HeuristicPolicy (local-first + cloud-fallback-on-timeout), §6.1-6.2 policy table + overrides, §7 config/provider-fidelity wiring; Session 2 (§9)

**Done when:** routing/policy.py:route_refs is a pure reorder called at resolve_provider_for_use_case step (2), enabled per-use-case only, with model_override (step 1) and native-agent (step 0) bypassing it; binding local+cloud on reasoning and enabling heuristic routes background one_shot_completion local-first, and killing ollama produces cloud-rescued calls stamped routed_fallback:true (distinct from degraded) within one breaker window with no stacked timeouts (SC #3); model_override and per-use-case pin bypass/short-circuit, verified by routed provenance (SC #4); unresolvable pinned first-ordered ref still raises ProviderResolutionError and a ref removed from active_models.json drops from candidates on next load (SC #6); routing_policy.json + read-only table UI with mode/pin/reorder controls; RoutingConfig wired through all 4 points (test_config_roundtrip green)

### `MRT-5` — Learned policy + cost-aware ordering + propose-don't-write proposals

**Status:** todo

§4.2 LearnedPolicy (60/40 scoring, >=5-sample floor, EMA+hysteresis), §4.3/§5.2 cost-aware near-equal ordering + cloud_quality_margin, §6.3-6.4 proposals + cooldown + notification + SEL audit; Session 3 (§9)

**Done when:** per-(use_case,query_class,ref) score=0.60*success_rate+0.40*feedback over the fold with feedback extracted from WF2 ledger/eval_judge (renormalizing onto success_rate + feedback_n:0 when absent); a genuine quality gap at n>=5 enqueues a routing proposal (skills/proposals.py pattern) with inspectable evidence (scores, n, latency/cost deltas, sample_audit_ids) WITHOUT editing routing_policy.json; accept updates the table with proposal_id basis + SEL entry, reject suppresses re-propose for reproposal_cooldown_days (SC #5); cost only reorders within-hysteresis near-equals with cloud needing to beat local by cloud_quality_margin; deleting routing_stats.json/routing_policy.json degrades to heuristic/off and nothing writes memory.db/knowledge.db (SC #8)

