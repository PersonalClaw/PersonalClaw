# MODEL-USE-CASES-V2 — atomic plans

**Source plan:** [`MODEL-USE-CASES-V2`](../plans/MODEL-USE-CASES-V2.md)  
**Code:** `MUCV`  
**Source status:** done



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `MUCV-1` | ✅ (##58) | Vocabulary growth + tolerant chain reads + resolution_chain resolver + breaker-aware seam walk | — | USE_CASES contains all 5 sub-categories and parent_capability('loops')=='chat'; load_active_models normalizes a bare-string value to [ref]; resolution_chain composes [override?]+chain (override never expanded, dedup keeps front, unbound sub-category -> chat chain); resolve_provider_for_use_case walks the chain skipping breaker-OPEN entries (SEL model.chain_skip) and raising ProviderResolutionError only when exhausted (one-entry stale-pin still raises) |
| `MUCV-2` | ✅ (##58) | Chain-capable PUT /api/models/active/{use_case} + MULTI_ACTIVE_USE_CASES narrowing | `MUCV-1` | PUT with N ordered refs on any use case (e.g. reasoning) persists verbatim, GET round-trips order, cap 20 enforced, provider-prefix validation unchanged; MULTI_ACTIVE_USE_CASES comment/meaning narrowed to picker-pool membership and dead import removed |
| `MUCV-3` | ✅ (##58) | background axis live: one_shot collapse retarget + _bg/personalclaw-lite factory | `MUCV-1` | one_shot_completion collapses 'background'/'ingestion' to the background axis (explicit axes honored, unrecognized -> reasoning); the _bg/personalclaw-lite factory resolves use_case='background' (both _ensure_background and cold-start paths); guard-wrap extends to the axis; binding a cheap model to background moves titles/tags/suggestions/digests/consolidation off flagship chat |
| `MUCV-4` | ✅ (##58) | orchestration axis + native inner-model axis threading (E1 fix) | `MUCV-1` | model-less subagent spawns and orchestrated-chat parent turns resolve use_case='orchestration' (explicit spawn model still wins); resolve_provider_for_use_case threads model_axis (or use_case) into _build_native_runtime so the inner model uses the governing axis instead of hardcoded 'chat'; _fallback_chat_model/_provider_entry_name gained a use_case param |
| `MUCV-5` | ✅ (##58) | loops axis live: worker sessions + gates/judges off reasoning | `MUCV-1` | loop worker sessions resolve use_case='loops' keyed off session._app=='loop' (not key prefix) when loop.model unset; loop/gates.py judge_verdict and both loop/judge.py provider factories switched 'reasoning'->'loops'; loop work audits under the loops axis |
| `MUCV-6` | ✅ (##58) | Call-failure chain-advance for non-interactive axes | `MUCV-1`, `MUCV-3` | on CircuitOpenError/provider failure from chain entry N a >1-entry chain rebuilds from N+1 (once per remaining entry) in one_shot_completion and the direct resolve consumers; OutputContractError does NOT advance; whole-chain failure surfaces one clear error; a one-entry chain takes today's plain path; interactive chat advances at call-start only (documented at the seam) |
| `MUCV-7` | ✅ (##58) | code_tools + reasoning end-to-end verification (+ any drift fixes) | `MUCV-1`, `MUCV-4` | binding code_tools and reasoning to distinct models shows each resolving its own axis in the audit for a native code turn and a web-extract one-shot; DISCOVERY logged (verified no orphaned reasoning consumer — web/fetch.py names it explicitly, remains default for unrecognized labels) |
| `MUCV-8` | ✅ (##58) | Settings chain editor + per-entry health dots + composer precedence explainer | `MUCV-1`, `MUCV-2` | ModelsPanel shows a Chat-routing group with all 5 sub-category rows (fallback:'Chat' empty-state) plus an ordered chain editor for chat+sub-categories (default/fallback-n labels, keyboard-accessible reorder, remove, append-on-pick) persisting order via setActiveModel; per-entry breaker-health dots from api.modelsHealth() (closed->green/half_open->amber/open->red+retry tooltip, no dot when no health row); composer Auto hint reads 'Use-case chain' with the precedence explainer in the pill popover; web typecheck+vitest+build green |

## Atom scopes

### `MUCV-1` — Vocabulary growth + tolerant chain reads + resolution_chain resolver + breaker-aware seam walk

**Status:** done (PR ##58)

S1 T1.1-T1.3 (Design S1; C1 Vocabulary; C2 Chain resolution)

**Done when:** USE_CASES contains all 5 sub-categories and parent_capability('loops')=='chat'; load_active_models normalizes a bare-string value to [ref]; resolution_chain composes [override?]+chain (override never expanded, dedup keeps front, unbound sub-category -> chat chain); resolve_provider_for_use_case walks the chain skipping breaker-OPEN entries (SEL model.chain_skip) and raising ProviderResolutionError only when exhausted (one-entry stale-pin still raises)

### `MUCV-2` — Chain-capable PUT /api/models/active/{use_case} + MULTI_ACTIVE_USE_CASES narrowing

**Status:** done (PR ##58)

S1 T1.4 (C4 API surface)

**Done when:** PUT with N ordered refs on any use case (e.g. reasoning) persists verbatim, GET round-trips order, cap 20 enforced, provider-prefix validation unchanged; MULTI_ACTIVE_USE_CASES comment/meaning narrowed to picker-pool membership and dead import removed

### `MUCV-3` — background axis live: one_shot collapse retarget + _bg/personalclaw-lite factory

**Status:** done (PR ##58)

S2 T2.1 (C3; Design S2 background)

**Done when:** one_shot_completion collapses 'background'/'ingestion' to the background axis (explicit axes honored, unrecognized -> reasoning); the _bg/personalclaw-lite factory resolves use_case='background' (both _ensure_background and cold-start paths); guard-wrap extends to the axis; binding a cheap model to background moves titles/tags/suggestions/digests/consolidation off flagship chat

### `MUCV-4` — orchestration axis + native inner-model axis threading (E1 fix)

**Status:** done (PR ##58)

S2 T2.2 + the flagged E1 risk (Risks: native inner-model recursion)

**Done when:** model-less subagent spawns and orchestrated-chat parent turns resolve use_case='orchestration' (explicit spawn model still wins); resolve_provider_for_use_case threads model_axis (or use_case) into _build_native_runtime so the inner model uses the governing axis instead of hardcoded 'chat'; _fallback_chat_model/_provider_entry_name gained a use_case param

### `MUCV-5` — loops axis live: worker sessions + gates/judges off reasoning

**Status:** done (PR ##58)

S2 T2.3 (Design S2 loops; C3 loop gates/judges)

**Done when:** loop worker sessions resolve use_case='loops' keyed off session._app=='loop' (not key prefix) when loop.model unset; loop/gates.py judge_verdict and both loop/judge.py provider factories switched 'reasoning'->'loops'; loop work audits under the loops axis

### `MUCV-6` — Call-failure chain-advance for non-interactive axes

**Status:** done (PR ##58)

S2 T2.4 (Design S2 call-failure advance; C3)

**Done when:** on CircuitOpenError/provider failure from chain entry N a >1-entry chain rebuilds from N+1 (once per remaining entry) in one_shot_completion and the direct resolve consumers; OutputContractError does NOT advance; whole-chain failure surfaces one clear error; a one-entry chain takes today's plain path; interactive chat advances at call-start only (documented at the seam)

### `MUCV-7` — code_tools + reasoning end-to-end verification (+ any drift fixes)

**Status:** done (PR ##58)

S2 T2.5 (Design S2; Risks reasoning semantic shrink)

**Done when:** binding code_tools and reasoning to distinct models shows each resolving its own axis in the audit for a native code turn and a web-extract one-shot; DISCOVERY logged (verified no orphaned reasoning consumer — web/fetch.py names it explicitly, remains default for unrecognized labels)

### `MUCV-8` — Settings chain editor + per-entry health dots + composer precedence explainer

**Status:** done (PR ##58)

S3 T3.1-T3.4 (Design S3; C4 health consumption)

**Done when:** ModelsPanel shows a Chat-routing group with all 5 sub-category rows (fallback:'Chat' empty-state) plus an ordered chain editor for chat+sub-categories (default/fallback-n labels, keyboard-accessible reorder, remove, append-on-pick) persisting order via setActiveModel; per-entry breaker-health dots from api.modelsHealth() (closed->green/half_open->amber/open->red+retry tooltip, no dot when no health row); composer Auto hint reads 'Use-case chain' with the precedence explainer in the pill popover; web typecheck+vitest+build green

