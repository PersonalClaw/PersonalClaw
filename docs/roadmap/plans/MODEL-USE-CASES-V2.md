# MODEL-USE-CASES-V2

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/MUCV.md`](../atomic/MUCV.md) as 8 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Model Use-Cases v2 — Sovereign Vocabulary + Ordered Fallback Chains

**Status:** DONE — S1 (vocabulary + chain resolver + storage), S2 (consumer wiring + call-failure
advance + the inner-model axis E1 fix) and S3 (the Settings chain editor + health dots + composer
explainer) all shipped 2026-07-27. Verified on `main`: `CHAT_SUBCATEGORIES` with all five axes,
`resolution_chain()` called from `llm_helpers.py`, the chain WALK live in `provider_bridge.py`
(breaker-OPEN skip → `_log_chain_skip`, unbuildable skip, exhausted → `ProviderResolutionError`), and
every consumer axis wired — `subagent.py` (orchestration), `session.py` (background),
`chat_runner.py` (loops). The flagged E1 risk (native inner recursion hardcoding `"chat"`) is fixed.
**Remaining:** live multi-provider dogfood, Owner task 1. Status corrected 2026-08-04 by code audit.
Created 2026-07-26 (roadmap rev 13; owner ask: model sovereignty with sophisticated use-case routing)

---

## Context (code recon, 2026-07-26)

- **The truth store** (`src/personalclaw/providers/use_cases.py`): `CAPABILITIES` (11 top-grain: chat/embedding/stt/tts/diarization + modality/gen pairs), `CHAT_SUBCATEGORIES = ("code_tools", "reasoning")`, `USE_CASES = CAPABILITIES + CHAT_SUBCATEGORIES`, `MULTI_ACTIVE_USE_CASES = {chat, image_modality}`. `active_models.json` maps `use_case -> list["provider:model"]` — **every binding is already a list**, but the list means "routing pool the picker chooses from" only for the multi-active pair; for everything else it is a single-entry list and `PUT /api/models/active/{use_case}` rejects >1 entry (`handlers/model_registry.py::api_models_active_set`). `active_model_refs(use_case)` applies the sub-category → `chat` fallback. `parent_capability()` returns `chat` for sub-categories. `load_use_case_settings`/`save_use_case_settings` hold provider-agnostic per-use-case behavior under `extensions/use_case_settings/`. `migrate_legacy_bindings()` is the precedent for a one-shot store-shape move.
- **The removal doctrine is written into the file:** the comment block above `CHAT_SUBCATEGORIES` records that `summarization`/`planning` were selectable routing targets with NO resolver — a pinned model was silently ignored — so they were REMOVED. Every new sub-category in this plan lands in the same session as the consumer that resolves through it, or it does not land.
- **The one resolution seam** (`providers/provider_bridge.py::resolve_provider_for_use_case`, ~:504): order today is (a) native short-circuit for `chat`/`code_tools` + native agents → `_build_native_runtime` (whose INNER model re-resolves with `use_case="chat"`, `_force_model_axis=True`); (b) provider-qualified `model_override` (`Provider:model` / `Provider/model`) routes directly; (c) the active refs loop — walks `active_model_refs(use_case)` in order, first buildable ref wins, and an active ref whose provider is NOT configured **raises `ProviderResolutionError`** ("block, don't silently fall back" — the stale-pin rule); (d) implicit first-capable-provider fallback only when no selection exists. `create_provider_factory("chat")` (config/loader.py:2397 delegates) is the factory every session gets.
- **`reasoning` consumers:** `llm_helpers.py::one_shot_completion` (:277) collapses informal labels (`"background"`, `"ingestion"`) to `"reasoning"`; its callers include `inbox_service.py` (classify :312, draft :352, `generate_digest` :393), `after_turn_review.py`:311, `nl_to_cron.py`:57, `chat_retag.py`:268, `web/fetch.py`:319, `knowledge/llm_pool.py`:62. Loop gates/judges resolve `"reasoning"` directly (`loop/gates.py`:122, `loop/judge.py`:218,:286). The `reasoning` axis is the ONLY one wrapped by `ModelCallGuard` (`_guard_use_case` kwarg, provider_bridge :620).
- **The `_bg`/personalclaw-lite path:** `session.py` `BACKGROUND_KEY = "_bg"` (:124); `SessionManager._ensure_background` builds it via `self._provider_factory(BACKGROUND_KEY, agent="personalclaw-lite")` (:342). Consumers: `suggestions.py`:162 (follow-up suggestions), `dashboard/chat_title.py`:83/:99 (titles + `_rephrase_plan_lite`), `context.py`:526 (consolidation), `history.py`:1102/:1789. The lite agent profile has `model=""` (`agents/defaults.py::make_lite_agent_profile`) — so today it inherits the **chat** binding via `_fallback_chat_model` (provider_bridge :140), i.e. background chores burn the flagship chat model.
- **Loop workers:** `loop/manager.py` creates worker sessions via `state.get_or_create_session(..., agent=loop.agent or strat.default_agent, model=loop.model, app="loop")` (:158, :426) — `loop.model` is a per-loop override; with none set, the worker resolves the plain chat binding. No `loops`-grade axis exists.
- **Orchestration turns:** the orchestrator is a generated skill (`orchestrator_skill.py`) on the default agent; delegation spawns subagents (`subagent.py::spawn`, :891 — optional `model` override, else agent-inherited → chat binding). Orchestrated Chat results flow via `session_workspace` (`handlers/core.py::api_session_agents_list` :850). The supervising/orchestrating turn itself is a chat-factory session — no distinct axis.
- **Breaker API** (`guardrails/breaker.py`): process-global `get_breaker(name)` / `all_breakers()`; `CircuitBreaker.is_open()`, `.state()` (lazy OPEN→HALF_OPEN), `.retry_after()`. `ModelCallGuard._guarded` (guardrails/model_call.py:168) raises `CircuitOpenError` in microseconds when OPEN. Breakers are keyed by **provider entry name** — exactly the prefix of a chain ref.
- **Health surface:** `GET /api/models/health` (`dashboard/handlers/core.py::api_models_health` :783 → `guardrails/health.py::provider_health`) returns `{providers: [{name, breaker_state, consecutive_failures, pass_rate, p50_ms, …}]}`; the frontend client exists (`web/src/lib/api.ts`:1204 `providerHealth`).
- **Settings → Models panel** (`web/src/pages/settings/ModelsPanel.tsx`): `USE_CASE_META`/`USE_CASE_ORDER` list ONLY the 11 capabilities — **`code_tools` and `reasoning` are confirmed absent**; a user cannot see or change them anywhere in the UI. Writes go through `api.setActiveModel(useCase, models)` → `PUT /api/models/active/{use_case}` (validates provider prefixes against `_known_provider_names()`). The panel already renders a `fallback` hint ("inherits from …") for meta entries that declare one — the exact affordance sub-category rows need.
- **Composer override today:** `ComposerValue.model` (`web/src/ui/composer/types.ts`) defaults `'Auto'`; a non-Auto pick is sent as the session's `model` (`ChatPage.tsx`:1092, `api.setSessionModel` :1472) and flows `state.get_or_create_session(model=…)` → factory `model_override` → the provider-qualified direct route in the seam. **There is no fallback**: if the override's provider/model fails, the turn fails — nothing walks back to the use-case binding.
- **Storage conventions:** entity/user state → `entity_settings/*.json` (`providers/entity_routes.py::_entity_settings_path`); `atomic_write` everywhere; config round-trip 5-point contract for any new config field (`tests/test_config_roundtrip.py`).

## Design

- **S1 — Vocabulary + chain resolver + storage shape.** `CHAT_SUBCATEGORIES` grows to `("code_tools", "reasoning", "background", "orchestration", "loops")` — all parenting to `chat`. The stored value for every use case becomes an **ordered chain** (semantically; the JSON stays `list[str]`): position 0 is the default, positions 1..n are fallbacks, unlimited length, user-ordered. A new pure resolver in `use_cases.py` — `resolution_chain(use_case, *, session_override="") -> list[str]` — composes: `[override] + chain(use_case or parent-chat-fallback)`, deduped, override never expanded into a chain. `resolve_provider_for_use_case`'s active-refs loop becomes chain-aware: an entry whose provider's breaker `is_open()` is **skipped** (logged, next entry tried); an entry whose provider *builds* is returned as before. The stale-pin rule is preserved but re-scoped to the chain: an unconfigured-provider entry mid-chain is skipped-with-warning **only when a later entry exists**; a chain whose entries ALL fail to build raises the same `ProviderResolutionError` (block, don't silently degrade past the user's whole declared chain into implicit fallback). `MULTI_ACTIVE_USE_CASES` is retired as a concept split: `chat`/`image_modality` keep their "picker pool" meaning for dropdowns, but the >1-entry rejection in `api_models_active_set` is removed for every use case (any use case may store a chain). Old-shape tolerance: a bare string value or single-entry list reads as a one-entry chain (`load_active_models` normalizes); no migration file — clean break under the pre-1.0 banner.
- **S2 — Consumers + breaker-aware call-failure advance.** Each new sub-category gets its consumer wired the same session (the removal-doctrine bar): **`background`** — `one_shot_completion`'s informal-label collapse retargets `"background"`/`"ingestion"` to the `background` axis (not `reasoning`), and the `_bg`/personalclaw-lite session factory resolves `use_case="background"` (titles, tags, suggestions, follow-up chips, digests, consolidation all move); **`orchestration`** — orchestrator/supervisor turns (the orchestrated-chat parent session + subagent spawns with no explicit model) resolve `use_case="orchestration"`; **`loops`** — loop worker sessions (`loop/manager.py` both spawn sites) and loop gates/judges resolve `use_case="loops"` (gates/judges move OFF bare `"reasoning"` onto the loop axis — long-horizon work gets its own knob). `code_tools`/`reasoning` are verified end-to-end (bind a distinct model → observe it in the audit). **Call-failure advance:** for the guarded non-interactive axis, a `CircuitOpenError`/provider failure from entry N advances to entry N+1 *for that call* — implemented at the resolution wrapper (`one_shot_completion` + the direct `resolve_provider_for_use_case` consumers get a `complete_with_chain` helper), NOT inside `ModelCallGuard` (the guard stays per-provider dumb; the chain walk is the caller's loop). Interactive chat streams advance only at call-START (breaker-open skip); mid-stream failure recovery stays PLATFORM-RESILIENCE's territory.
- **S3 — Settings UI + composer explainer + validation.** The Models panel gains a **Chat routing** group: one chain row per chat sub-category (label, description, "inherits your Chat chain" empty-state via the existing `fallback` meta affordance), plus the `chat` row itself upgraded to an ordered chain editor — drag reorder, add/remove entries, per-entry **health dot** from `/api/models/health` (breaker_state → green/amber/red), unavailable-model synthetic rows kept. The composer model pill gains a one-line **precedence explainer** ("This model overrides the session's use-case chain; if it fails, the chain takes over"). Validation drives every axis from the UI: bind chains, kill a provider, watch the skip; run a title/suggestion/loop/orchestration turn and confirm each resolves its own axis in the SEL/audit.
- **What this plan does NOT do:** no auto-reordering, no scoring, no learned local-vs-cloud policy (plan 17 consumes this vocabulary later and lands changes as proposals); no per-app or per-agent chains (agents keep their existing `model` override field); no new telemetry stores.

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md); clean break under the pre-1.0 banner)

### C1 — Vocabulary (`providers/use_cases.py`; additive)
```python
CHAT_SUBCATEGORIES: tuple[str, ...] = (
    "code_tools",      # native agent runtime (existing)
    "reasoning",       # one-shot judgments: web-extract, guarded one-shots (existing)
    "background",      # NEW: titles/tags/suggestions/follow-up chips/digests/consolidation
    "orchestration",   # NEW: orchestrator/supervisor turns + model-less subagent spawns
    "loops",           # NEW: loop workers + loop gates/judges (long-horizon)
)
# parent_capability() unchanged: every sub-category resolves under "chat".
```

### C2 — Chain resolution (`providers/use_cases.py` + `providers/provider_bridge.py`)
```python
def resolution_chain(use_case: str, *, session_override: str = "") -> list[str]:
    """Ordered candidate refs: [override?] + own chain, else parent chat chain.
    The override is ONE ref prepended — never expanded; duplicates deduped
    (override already in chain keeps override's front position)."""

# resolve_provider_for_use_case active-refs loop becomes the chain walk:
#   for ref in resolution_chain(use_case, session_override=model_override or ""):
#     • provider breaker OPEN (get_breaker(name).is_open()) → SKIP, log, next
#     • provider unconfigured → SKIP-with-warning IF a later entry exists,
#       else fall through to the all-failed raise
#     • builds → return (unchanged)
#   all entries exhausted → ProviderResolutionError (same envelope/AgentError)
```
Old-shape tolerance lives in `load_active_models()`: a bare string value normalizes to `[ref]`; missing keys behave exactly as today. `save_active_models` writes lists only.

### C3 — Chain-advancing one-shot (`llm_helpers.py`; the call-failure walk)
```python
async def one_shot_completion(prompt, *, use_case="background", output_type=None) -> str:
    # informal-label collapse now targets the REAL axis:
    #   "background"/"ingestion" → "background"; explicit axes honored as today.
    # On CircuitOpenError / provider failure from chain entry N, rebuild from
    # entry N+1 and retry ONCE per remaining entry (bounded by chain length).
```
Loop gates/judges (`loop/gates.py`, `loop/judge.py`) switch their resolve literal to `"loops"`; the `_guard_use_case` guard-wrap extends from `reasoning` to every non-interactive sub-category (`reasoning`, `background`, `loops`, `orchestration` one-shots) so the breaker + audit see the true axis.

### C4 — API surface (existing routes, chain-capable; §2.2 envelope)
```python
GET  /api/models/active                 # unchanged shape: {use_cases: {uc: [refs...]}}
PUT  /api/models/active/{use_case}      # {models: [refs...]} — >1 allowed for ALL
                                        # use-cases now; order = chain order;
                                        # provider-prefix validation unchanged
GET  /api/models/health                 # unchanged; consumed per-entry by the UI
```
No new endpoints. The composer override path (`POST /api/chat/sessions/{s}/model`, factory `model_override`) is untouched at the API layer — precedence is implemented in the seam.

### Integration points
- **Calls:** `guardrails/breaker.py::get_breaker` (skip check), `guardrails/model_call.py::ModelCallGuard` (unchanged, wraps each entry's provider), `provider_health` (UI dots), `atomic_write` (store writes), SEL `log_api_access` on chain-skip events (`operation="model.chain_skip"`).
- **Called by:** every `resolve_provider_for_use_case` consumer (chat factory, `one_shot_completion`, knowledge pipeline `_llm.py::complete_text`, loop gates/judges, subagent spawns) — no signature changes for them; the `_bg` factory call, loop manager spawn sites, and the orchestration entry points change their `use_case`/factory literal only.
- **Storage owned:** the chain semantics of `active_models.json` values (shape stays `{uc: [refs]}`); no new files. Per-use-case behavior settings stay in `extensions/use_case_settings/`.
- **Class B:** value-shape semantics change + sub-category vocabulary growth — plain clean break (pre-1.0 banner); tolerant read of bare-string/single-ref values; release notes advise `personalclaw snapshot`.
- **MODEL-ROUTING-TELEMETRY (17) coordination:** plan 17's learned router later scores WITHIN this vocabulary and its multi-binding pool IS this chain store; its learned reorderings land as user-accepted proposals — this plan's resolver never reorders, and that invariant is a stated contract plan 17 must honor.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Vocabulary + chain resolver + storage shape

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Grow `CHAT_SUBCATEGORIES` (+`background`/`orchestration`/`loops`); update the doctrine comment to name each new axis's consumer (wired in S2 tasks — cite them); `parent_capability` needs no change | `src/personalclaw/providers/use_cases.py` | `USE_CASES` contains all 5 sub-categories; `parent_capability("loops") == "chat"` (test) |
| T1.2 | Tolerant chain reads: `load_active_models` normalizes a bare-string value to `[ref]`; add `resolution_chain(use_case, *, session_override="")` (override prepended, never expanded; dedup keeps front position; unbound sub-category returns the chat chain) | `use_cases.py`, `tests/test_use_case_chains.py` (new) | old single-binding stores read cleanly; override+chain composition matches the owner semantics (unit tests for all branches) |
| T1.3 | Chain-aware seam: `resolve_provider_for_use_case` walks `resolution_chain(...)`; breaker-OPEN entry → skip + SEL `model.chain_skip`; unconfigured-provider entry → skip-with-warning iff a later entry exists; all-exhausted → the existing `ProviderResolutionError` shape | `src/personalclaw/providers/provider_bridge.py` | with entry-0's breaker forced OPEN, resolution returns entry-1's provider; a one-entry chain with a dead provider still raises (stale-pin rule preserved; tests) |
| T1.4 | Lift the >1-entry rejection in `PUT /api/models/active/{use_case}` for all use-cases (order = chain order; prefix validation unchanged); `MULTI_ACTIVE_USE_CASES` narrows to its true remaining meaning (picker-pool dropdowns) with the constant's comment rewritten | `dashboard/handlers/model_registry.py`, `use_cases.py` | PUT with 3 ordered refs on `reasoning` persists verbatim; GET round-trips order; `/api/models/chat` dropdown unaffected |
| V1 | Validation: seed a 3-entry chat chain in the dev home; trip entry-0's breaker via forced failures; a chat turn resolves entry-1; restore → entry-0 again; `make lint` + targeted `pytest` green | — | holds |

### Session 2 — Consumer wiring + call-failure advance

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `background` axis live: `one_shot_completion` collapse targets `"background"` (explicit real axes still honored); `_bg`/personalclaw-lite factory resolves `use_case="background"` (SessionManager background factory + `_fallback_chat_model` walks the background chain first); guard-wrap extends to the axis | `llm_helpers.py`, `session.py`, `providers/provider_bridge.py` | binding a cheap model to `background` makes titles/suggestions/digests/consolidation use it (audit shows the axis); unbinding falls back to the chat chain |
| T2.2 | `orchestration` axis live: orchestrated-chat parent turns + model-less subagent spawns resolve `use_case="orchestration"` | `subagent.py`, orchestrated-chat entry (`dashboard/` spawn path), `providers/provider_bridge.py` | a subagent spawned with no model override resolves the orchestration chain; an explicit spawn `model` still wins (test) |
| T2.3 | `loops` axis live: both `loop/manager.py` worker-spawn sites resolve `use_case="loops"` when `loop.model` is unset; gates/judges switch `"reasoning"` → `"loops"` | `loop/manager.py`, `loop/gates.py`, `loop/judge.py` | a loop worker with no per-loop model uses the loops chain; gates/judges audit under `loops` |
| T2.4 | Call-failure advance for the non-interactive axes: on `CircuitOpenError`/provider failure from entry N, rebuild from N+1, once per remaining entry; interactive chat advances at call-start only (skip) — document the boundary at the seam | `llm_helpers.py`, `knowledge/pipeline/nodes/_llm.py`, `providers/provider_bridge.py` | with entry-0 hard-failing, a digest completes via entry-1; a whole-chain failure surfaces one clear error, not N stack traces (tests) |
| T2.5 | End-to-end verify `code_tools` + `reasoning`: bind each to a distinct model; a native code turn and a web-extract call resolve their own axes (fixes any drift found — this is the owner-confirmed "backend-real but invisible" pair) | tests + any drift fixes | audit rows show the distinct models per axis; DISCOVERY-logged if drift found |
| V2 | Validation: five sub-categories each bound to a distinguishable model; run title-gen, a loop tick, an orchestrated spawn, a code turn, a web-extract — each hits its model; kill a provider mid-batch and watch chains advance | — | holds |

### Session 3 — Settings UI + composer explainer + validation

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Chat-routing group in the Models panel: rows for all 5 sub-categories (label/description/`fallback: 'chat'` empty-state) + `USE_CASE_ORDER` updated | `web/src/pages/settings/ModelsPanel.tsx`, `modelsPanel.test.ts` | every sub-category is bindable in the UI; unbound rows read "uses your Chat chain" |
| T3.2 | Ordered chain editor: add/remove entries, drag reorder (persist order via `setActiveModel`), position-0 labeled "default", others "fallback n" | `ModelsPanel.tsx` (+ a `ChainEditor` extracted component), design-system primitives | reorder round-trips the store; keyboard-accessible reorder (a11y not weakened) |
| T3.3 | Per-entry health dots from `api.providerHealth()` (`breaker_state`: closed→green, half_open→amber, open→red + retry-after tooltip); refresh on panel focus | `ModelsPanel.tsx`, `web/src/lib/api.ts` (type reuse) | an OPEN breaker shows red on every chain entry of that provider |
| T3.4 | Composer precedence explainer: one line in the model-pill popover ("Overrides this session's chain; if it fails, the chain takes over"), and 'Auto' labeled as "use-case chain" | `web/src/ui/composer/` popover component | explainer visible; no behavior change to the pick path |
| V3 | Validation as a user (full surface): fresh dev home → build chains in Settings → break a provider → watch dots + skips → run every axis's consumer from the UI → composer override wins then fails over; `npm run typecheck && npm test && npm run build` + full `make test` | — | holds |

## Owner tasks (real world)
1. **Pick your real chains** on your instance after S3 — e.g. `background` → local/cheap first (this is the plan's cost story: chores stop burning the flagship chat model), `loops` → a long-context model. Defaults ship empty (inherit chat) — your bindings are the dogfood.
2. Confirm the **orchestration boundary**: this plan routes the *supervising turn* + model-less spawns; a subagent whose task declares its own model keeps it. If you want orchestration to also cap spawned specialists, that's a scope amendment.
3. Release-notes line for the class-B shape change (advise `personalclaw snapshot` before upgrading, per the pre-1.0 banner).
4. When MODEL-ROUTING-TELEMETRY (17) starts, re-read C4's coordination note with its executor — its learned reorderings must land as proposals against THIS store, never silent writes.

## Risks & open questions
- **The native inner-model recursion** (`_build_native_runtime` re-resolves with `use_case="chat"`, `_force_model_axis=True`): a `code_tools`/`orchestration` chain must govern the INNER model too, or the sub-category binding is cosmetic for native agents. T2.5/T2.2 must thread the originating use_case through the inner resolve — this is the likeliest place for a premise mismatch; E1-escalate if the inner path can't carry it cleanly.
- **Chain-advance vs. stale-pin doctrine tension:** today ONE dead pinned ref raises. The chain re-scope (skip mid-chain, raise only when exhausted) is a deliberate softening the user opts into by ADDING fallbacks — a one-entry chain behaves exactly as today. Tests must pin both behaviors so neither regresses.
- **Breaker granularity is provider-level, not model-level:** two chain entries on the same provider share one breaker — an OPEN breaker skips both. Acceptable v1 (the breaker exists to route around a *provider* outage); per-model breakers are a plan-17-adjacent question, not this plan's.
- **`reasoning` semantic shrink:** after T2.1/T2.3 move background chores and loop judgments off it, `reasoning` narrows to explicit one-shot judgment calls (web-extract etc.). Verify no consumer is orphaned (the removal doctrine cuts both ways — an axis with no consumer gets removed, not kept for sentiment).
- **Open:** should the composer override *persist* as a session-level chain-head across restarts (today `session.model` already persists)? Proposed: yes, unchanged behavior — the override ref simply keeps its prepend position on resume. Confirm during V3.

## Execution log

- [2026-07-27][S1] DONE: vocabulary + chain resolver + storage shape (T1.1-T1.4). `CHAT_SUBCATEGORIES` grew to `(code_tools, reasoning, background, orchestration, loops)` with the doctrine comment naming each axis's consumer; `load_active_models` tolerant-reads a bare-string value as a one-entry chain (clean break, no migration file); `resolution_chain(use_case, *, session_override="")` composes `[override?] + chain` (override never expanded, dedup keeps front position, unbound sub-category → chat chain); the seam's active-refs loop became the chain walk — breaker-OPEN entry skipped (SEL `model.chain_skip` via `_log_chain_skip`, only when a later entry exists — a one-entry chain still ATTEMPTS the build), unbuildable entry skipped-with-warning iff a later entry exists, exhausted chain raises the same `ERR_MODEL_UNRESOLVED` envelope (stale-pin rule preserved: one dead pinned ref still raises); `PUT /api/models/active/{use_case}` accepts ordered chains for EVERY use case (cap 20; provider-prefix validation unchanged); `MULTI_ACTIVE_USE_CASES` narrowed to its picker-pool meaning (comment rewritten, dead import removed from model_registry).
- [2026-07-27][S2] DONE: consumer wiring + call-failure advance (T2.1-T2.5). **background**: `one_shot_completion`'s `"ingestion"` label collapses to `background` (explicit real axes honored; unrecognized → `reasoning`); the `_bg`/personalclaw-lite factory passes `model_axis="background"` (both the `_ensure_background` creation path and a cold-start via `get_or_create(BACKGROUND_KEY)`). **orchestration**: model-less subagent spawns pass `model_axis="orchestration"` (an explicit spawn model still wins). **loops**: loop worker sessions pass `model_axis="loops"` keyed off `session._app == "loop"` (the recon-verified trap: NOT the key prefix); `loop/gates.py::judge_verdict` + both `loop/judge.py` provider factories switched `"reasoning"` → `"loops"`. **Guard-wrap** extended from `reasoning` to all four non-interactive axes (breaker + audit see the true axis). **Call-failure advance** in `one_shot_completion`: with a >1-entry chain, a provider failure from entry N rebuilds from N+1 (once per remaining entry); an `OutputContractError` does NOT advance (the model responded — a schema miss is not an outage); whole-chain failure surfaces ONE clear error; a one-entry chain takes the plain path (today's exact behavior).
- [2026-07-27][S2] DONE (the plan's flagged E1 risk, resolved cleanly): the native inner-model recursion now carries the governing axis — `resolve_provider_for_use_case` pops `model_axis` and passes `model_axis or use_case` to `_build_native_runtime`, whose inner resolve uses that axis instead of hardcoded `"chat"` (so a `code_tools` chain governs native code sessions — previously cosmetic); `_fallback_chat_model` + `_provider_entry_name` gained a `use_case` param so the no-model-declared fallback id comes from the SAME governing chain. Unbound axes fall back to the chat chain via `active_model_refs` — zero behavior change until the user binds one.
- [2026-07-27][S3] DONE: Settings UI + composer explainer (T3.1-T3.4). ModelsPanel: `USE_CASE_META/ORDER` now carry all 5 sub-categories under a "Chat routing" group (labels/descriptions/`fallback: 'Chat'` empty-state "uses your Chat chain"); `multi` semantics replaced by `chain` — chat + sub-categories render an ordered chain editor (default/fallback-n labels, keyboard-accessible ArrowUp/ArrowDown reorder via the IconButton primitive, remove, append-on-pick) persisting order through `setActiveModel`; per-entry breaker-health dots from `api.modelsHealth()` (closed→green, half_open→amber, open→red + retry tooltip; NO dot when the provider has no health row — absence must not read as healthy); `capableModels` maps a sub-category to the chat-capable pool (models never declare "code_tools"). Composer: the model-pill's "Auto" hint is now "Use-case chain (Settings → Models)" and the popover carries the precedence explainer ("A picked model overrides this session's use-case chain; if it fails, the chain takes over").
- [2026-07-27][S1-3] Tests: new `tests/test_use_case_chains.py` (33: vocabulary, tolerant reads, resolution_chain composition, chain walk incl. breaker-skip/one-entry-still-tries/stale-pin-preserved/exhausted-raise, PUT chain round-trip + cap + unknown-prefix rejection, consumer axes incl. guard-wrap coverage, inner-model axis threading, call-failure advance incl. OutputContractError-never-advances + single-entry-plain-path); `test_llm_helpers.py` updated ("background" resolves its own axis); FE `modelsPanel.test.ts` gained the sub-category-pool case. Reference regenerated (routes.md — PUT docstring). Gate: `make lint` green (503 files), `make test` 8082 passed / 28 skipped / 13 xfailed, web typecheck + vitest (239) + build green.
- [2026-07-27][S3] Validated as-a-user on an isolated gateway (:10013, fresh dev home): 3-entry `background` chain PUT via the real API → persisted in order; >20 entries rejected 400; Settings → Models shows the Chat-routing group, "uses your Chat chain" empty-states, "chain of 3" summary; the chain editor reorders live (model-b promoted to default; verified in `active_models.json` via GET after the click) with boundary-disabled arrows; the composer Auto popover shows the precedence explainer. No real model bound in the sandbox — breaker-skip behavior is unit-locked (forced-OPEN breaker → entry-1 resolution) rather than live-driven; live multi-provider dogfood is Owner task 1.
- [2026-07-27] V3 note (owner-flagged open question): the composer override persists as the session-level chain-head across restarts — unchanged `session.model` behavior, confirmed by code read (the ref keeps its prepend position via `resolution_chain(session_override=…)` wherever the session model threads as `model_override`).
- [2026-07-27] DISCOVERY (`reasoning` semantic shrink, verified no orphan): most `one_shot_completion` callers already pass `use_case="background"` explicitly (inbox classify/draft/digest, nl_to_cron, chat_retag, after_turn_review, loop_routes summaries, context consolidation, chat_handlers nav-links) — under the old collapse they all rode `reasoning`; they now route to the `background` axis, which is exactly this plan's cost story. `reasoning` keeps real consumers — web/fetch.py names it explicitly, and it remains the default for unrecognized labels — so the axis stays (removal doctrine satisfied). `knowledge/llm_pool.py` passes `"ingestion"` → now `background`.
