# PROMPT-CACHE-SUBSTRATE — atomic plans

**Source plan:** [`PROMPT-CACHE-SUBSTRATE`](../plans/PROMPT-CACHE-SUBSTRATE.md)  
**Code:** `PCS`  
**Source status:** proposed

PROMPT-CACHE-SUBSTRATE decomposed into 8 atoms. **Landed:** PCS-1/PCS-2 (S1 ordering repairs), PCS-3 (the neutral marker seam + middleware), PCS-6 (the cache-usage producer), PCS-4 (the Anthropic EXPLICIT translation + its rails sweep). **Remaining:** PCS-5 (prompt_cache_enabled config + FE control), PCS-7 (the telemetry proof surface), PCS-8 (cross-repo branded-app postures). One provider-agnostic cache-marker seam: S1 ordering repairs (PCS-1/PCS-2), S2 marker+adoption (PCS-3/PCS-4/PCS-5), S3 producer+proof (PCS-6/PCS-7), plus cross-repo apps posture (PCS-8). No hard cross-plan blockers; one soft coordination edge to COST-AND-TOKEN-OBSERVABILITY for the rendered readout.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `PCS-1` | ✅ (#882) | §C2 wire-order repair: stability-ordered system messages (F1 fix) | — | Native loop tags its single per-turn turn_note volatile (runtime.py:712); _translate_messages keeps untagged system in system= so the stable assembled context leads the served prompt and delivers volatile notes at the END of the message list. Content-equivalence test green (every prior note ships exactly once); an untagged-only message list produces today's byte-identical request kwargs; V1 recency check confirms the model still calls tool_schema after the catalog moved late (no comprehension regression). |
| `PCS-2` | ✅ (#788) | §C3 date-line relocation with truncation-immunity (F2 fix) | — | [CURRENT DATE] moved to the end of the assembled block in BOTH the is_custom and normal branches, appended AFTER the _MAX_CONTEXT_CHARS truncation step; date text byte-identical; explicit test proves an oversized context still ends with the date line; test_context.py:698-707 and the test_context_engine.py:45 fixture updated; two assemblies ~1 min apart are byte-identical up to the final date line. |
| `PCS-3` | ✅ (#885) | Neutral cache-marker module + middleware wiring + OpenAI AUTOMATIC | `PCS-1` | llm/prompt_cache.py ships PromptCache enum, CACHE_HINT_KEY, mark_cacheable_prefix (NONE/AUTOMATIC return the input object unchanged, EXPLICIT hints exactly one non-tool message via shallow copy, never mutates caller dicts, vendor-string grep returns zero); ProviderCapability.prompt_cache defaults NONE; OpenAI adapter declares AUTOMATIC and translates nothing; middleware called in the native loop before complete() with a loop-owned _cache_generation bumped on compaction (runtime.py:1292) and agent-definition change, DEBUG-logged; every undeclared provider's message list is byte-identical to today. |
| `PCS-4` | ✅ | Anthropic EXPLICIT translation (cache_control on block-shaped system=) + rails test | `PCS-1`, `PCS-3` | Hinted span translated to cache_control {type: ephemeral} on its last block, including the block-shaped system= (list of text blocks) required by §C4; adapter declares EXPLICIT; an unhinted list produces today's byte-identical request kwargs; rails sweep asserts cache_control/ephemeral appear ONLY in llm/anthropic.py and fails on a temporarily injected violation. |
| `PCS-5` | ⬜ | prompt_cache_enabled config through all five wiring points + FE control | `PCS-3` | prompt_cache_enabled (default True) wired through dataclass+_meta, load() mapping, to_dict(), the _EDITABLE_CONFIG PATCH allowlist (dashboard/handlers/core.py), and a Models-settings-panel frontend control; test_config_roundtrip.py green; PATCH round-trips; middleware treats disabled as NONE; explicit test asserts the §C2/§C3 ordering repairs are NOT gated by the toggle (no dual path). |
| `PCS-6` | ✅ (#884) | The missing producer: read Anthropic cache-usage fields into LLMEvent (F3) | — | usage.cache_creation_input_tokens / usage.cache_read_input_tokens read via defensive getattr in BOTH accumulation sites (anthropic.py:302-309 and :457-464) and passed into the terminal LLMEvent (:386, :533); a mocked response carrying cache usage yields a non-zero cache_read_tokens on the event; a response lacking the fields yields 0 and does not raise. |
| `PCS-1` | ⬜ | §C2 wire-order repair: stability-ordered system messages (F1 fix) | — | Native loop tags its single per-turn turn_note volatile (runtime.py:712); _translate_messages keeps untagged system in system= so the stable assembled context leads the served prompt and delivers volatile notes at the END of the message list. Content-equivalence test green (every prior note ships exactly once); an untagged-only message list produces today's byte-identical request kwargs; V1 recency check confirms the model still calls tool_schema after the catalog moved late (no comprehension regression). |
| `PCS-2` | ⬜ | §C3 date-line relocation with truncation-immunity (F2 fix) | — | [CURRENT DATE] moved to the end of the assembled block in BOTH the is_custom and normal branches, appended AFTER the _MAX_CONTEXT_CHARS truncation step; date text byte-identical; explicit test proves an oversized context still ends with the date line; test_context.py:698-707 and the test_context_engine.py:45 fixture updated; two assemblies ~1 min apart are byte-identical up to the final date line. |
| `PCS-3` | ⬜ | Neutral cache-marker module + middleware wiring + OpenAI AUTOMATIC | `PCS-1` | llm/prompt_cache.py ships PromptCache enum, CACHE_HINT_KEY, mark_cacheable_prefix (NONE/AUTOMATIC return the input object unchanged, EXPLICIT hints exactly one non-tool message via shallow copy, never mutates caller dicts, vendor-string grep returns zero); ProviderCapability.prompt_cache defaults NONE; OpenAI adapter declares AUTOMATIC and translates nothing; middleware called in the native loop before complete() with a loop-owned _cache_generation bumped on compaction (runtime.py:1292) and agent-definition change, DEBUG-logged; every undeclared provider's message list is byte-identical to today. |
| `PCS-4` | ⬜ | Anthropic EXPLICIT translation (cache_control on block-shaped system=) + rails test | `PCS-1`, `PCS-3` | Hinted span translated to cache_control {type: ephemeral} on its last block, including the block-shaped system= (list of text blocks) required by §C4; adapter declares EXPLICIT; an unhinted list produces today's byte-identical request kwargs; rails sweep asserts cache_control/ephemeral appear ONLY in llm/anthropic.py and fails on a temporarily injected violation. |
| `PCS-5` | ✅ | prompt_cache_enabled config through all five wiring points + FE control | `PCS-3` | prompt_cache_enabled (default True) wired through dataclass+_meta, load() mapping, to_dict(), the _EDITABLE_CONFIG PATCH allowlist (dashboard/handlers/core.py), and a Models-settings-panel frontend control; test_config_roundtrip.py green; PATCH round-trips; middleware treats disabled as NONE; explicit test asserts the §C2/§C3 ordering repairs are NOT gated by the toggle (no dual path). |
| `PCS-6` | ⬜ | The missing producer: read Anthropic cache-usage fields into LLMEvent (F3) | — | usage.cache_creation_input_tokens / usage.cache_read_input_tokens read via defensive getattr in BOTH accumulation sites (anthropic.py:302-309 and :457-464) and passed into the terminal LLMEvent (:386, :533); a mocked response carrying cache usage yields a non-zero cache_read_tokens on the event; a response lacking the fields yields 0 and does not raise. |
| `PCS-7` | ⬜ | Aggregate cache hit-rate + saved-USD into turn telemetry (the proof surface, V2) | `PCS-6`, `PCS-4`, `EXT:COST-AND-TOKEN-OBSERVABILITY:owns/renders the saved-USD & hit-rate readout these numbers feed` | Per-turn aggregate exposes cache_read_tokens/cache_creation_tokens/cache_hit_pct + saved-USD (via existing estimate_cost rates) on the turn-complete telemetry, reusing stats.py:42-84 counters with no second store; never estimates when the provider reported nothing (honest-zero test for unpriced models, negative saved-USD not hidden); V2: real multi-turn Anthropic + OpenAI runs report rising hit-rate / non-zero saved-USD (turn1 creation, turns2+ read), OpenAI reports vendor reads with no marker sent, an undeclared/Ollama model runs byte-identical with zeros, toggling config off stops the marker while ordering holds. |
| `PCS-8` | ⬜ | Branded-app cache posture declaration (PersonalClawApps, incl. Bedrock cachePoint) | `PCS-3`, `PCS-4` | Branded model apps declare prompt_cache via BrandedProviderSpec: bedrock-models EXPLICIT with in-app Converse cachePoint translation (core never learns cachePoint), openai-compatible/openrouter-models AUTOMATIC where upstream caches, ollama-models/vllm-models EXPLICIT only if server-side prefix caching is actually enabled else NONE; validated by driving a real Bedrock-Anthropic multi-turn run that reports cache reads. |

## Atom scopes

### `PCS-1` — §C2 wire-order repair: stability-ordered system messages (F1 fix)

**Status:** done (PR #882)

Session 1 / T1.1; §C2 (wire order); soul guardrail 3

**Done when:** Native loop tags its single per-turn turn_note volatile (runtime.py:712); _translate_messages keeps untagged system in system= so the stable assembled context leads the served prompt and delivers volatile notes at the END of the message list. Content-equivalence test green (every prior note ships exactly once); an untagged-only message list produces today's byte-identical request kwargs; V1 recency check confirms the model still calls tool_schema after the catalog moved late (no comprehension regression).

### `PCS-2` — §C3 date-line relocation with truncation-immunity (F2 fix)

**Status:** done (PR #788)

Session 1 / T1.2; §C3 (prefix stability)

**Done when:** [CURRENT DATE] moved to the end of the assembled block in BOTH the is_custom and normal branches, appended AFTER the _MAX_CONTEXT_CHARS truncation step; date text byte-identical; explicit test proves an oversized context still ends with the date line; test_context.py:698-707 and the test_context_engine.py:45 fixture updated; two assemblies ~1 min apart are byte-identical up to the final date line.

### `PCS-3` — Neutral cache-marker module + middleware wiring + OpenAI AUTOMATIC

**Status:** done (PR #885)

Session 2 / T2.1+T2.2; §C1, §C4 (capability field), §C5 (generation bump)

**Done when:** llm/prompt_cache.py ships PromptCache enum, CACHE_HINT_KEY, mark_cacheable_prefix (NONE/AUTOMATIC return the input object unchanged, EXPLICIT hints exactly one non-tool message via shallow copy, never mutates caller dicts, vendor-string grep returns zero); ProviderCapability.prompt_cache defaults NONE; OpenAI adapter declares AUTOMATIC and translates nothing; middleware called in the native loop before complete() with a loop-owned _cache_generation bumped on compaction (runtime.py:1292) and agent-definition change, DEBUG-logged; every undeclared provider's message list is byte-identical to today.

### `PCS-4` — Anthropic EXPLICIT translation (cache_control on block-shaped system=) + rails test

**Status:** done

Session 2 / T2.3+T2.5; §C4 (edge-only translation); soul guardrail 1

**Done when:** Hinted span translated to cache_control {type: ephemeral} on its last block, including the block-shaped system= (list of text blocks) required by §C4; adapter declares EXPLICIT; an unhinted list produces today's byte-identical request kwargs; rails sweep asserts cache_control/ephemeral appear ONLY in llm/anthropic.py and fails on a temporarily injected violation.

### `PCS-5` — prompt_cache_enabled config through all five wiring points + FE control

**Status:** done

Session 2 / T2.4; §C6 (config, §2.1 five-point wiring)

**Done when:** prompt_cache_enabled (default True) wired through dataclass+_meta, load() mapping, to_dict(), the _EDITABLE_CONFIG PATCH allowlist (dashboard/handlers/core.py), and a Models-settings-panel frontend control; test_config_roundtrip.py green; PATCH round-trips; middleware treats disabled as NONE; explicit test asserts the §C2/§C3 ordering repairs are NOT gated by the toggle (no dual path).

**Landed:** all five wiring points — `AgentConfig.prompt_cache_enabled` (default `True`) with `_meta`, `load()`'s explicit mapping, `to_dict()` (via `asdict`, proven by test), the `agent.prompt_cache_enabled` `_EDITABLE_CONFIG` bool entry, and a **Prompt caching** switch in Settings → Models. `effective_cache_mode()` folds the switch into the provider's declared mode, so disabled resolves to `PromptCache.NONE` — the mode that already means byte-identical passthrough — rather than branching around the marker call. Tests: the switch through the real native loop (off → `complete()` sees the loop's own list), a PATCH round trip in both directions, and the **no-dual-path ratchet** asserting §C2's stable-then-volatile wire order, §C2's volatile tag as the loop produces it, and §C3's trailing date line all hold with the switch off.

**Remaining:** nothing.

### `PCS-6` — The missing producer: read Anthropic cache-usage fields into LLMEvent (F3)

**Status:** done (PR #884)

Session 3 / T3.1; Context F3

**Done when:** usage.cache_creation_input_tokens / usage.cache_read_input_tokens read via defensive getattr in BOTH accumulation sites (anthropic.py:302-309 and :457-464) and passed into the terminal LLMEvent (:386, :533); a mocked response carrying cache usage yields a non-zero cache_read_tokens on the event; a response lacking the fields yields 0 and does not raise.

### `PCS-7` — Aggregate cache hit-rate + saved-USD into turn telemetry (the proof surface, V2)

**Status:** todo

Session 3 / T3.2 + V2; §C5 integration points (reuse stats.py, no second store); soul guardrail 4

**Done when:** Per-turn aggregate exposes cache_read_tokens/cache_creation_tokens/cache_hit_pct + saved-USD (via existing estimate_cost rates) on the turn-complete telemetry, reusing stats.py:42-84 counters with no second store; never estimates when the provider reported nothing (honest-zero test for unpriced models, negative saved-USD not hidden); V2: real multi-turn Anthropic + OpenAI runs report rising hit-rate / non-zero saved-USD (turn1 creation, turns2+ read), OpenAI reports vendor reads with no marker sent, an undeclared/Ollama model runs byte-identical with zeros, toggling config off stops the marker while ordering holds.

### `PCS-8` — Branded-app cache posture declaration (PersonalClawApps, incl. Bedrock cachePoint)

**Status:** todo

Owner tasks #1 (S2 follow-up, cross-repo PersonalClawApps)

**Done when:** Branded model apps declare prompt_cache via BrandedProviderSpec: bedrock-models EXPLICIT with in-app Converse cachePoint translation (core never learns cachePoint), openai-compatible/openrouter-models AUTOMATIC where upstream caches, ollama-models/vllm-models EXPLICIT only if server-side prefix caching is actually enabled else NONE; validated by driving a real Bedrock-Anthropic multi-turn run that reports cache reads.

