# Plan: Prompt-Cache Substrate — One Middleware Seam That Makes Every Turn Cheaper

**Status:** DESIGNED — created 2026-07-29 (owner ask: competitive gap analysis, Genspark + Manus; owner direction: "an elaborated base middleware type subsystem that just plugs into the right location and provides all the places with prompt caching benefits")
**Created:** 2026-07-29
**Wave:** 2 (S1: the marker substrate + prefix stability; S2: provider adoption + measurement surface)
**Depends on:** nothing hard. Builds entirely on shipped seams: `ModelProvider.complete()` (`llm/base.py:150`), `ProviderCapability` (`llm/capabilities.py:47`), `pricing.estimate_cost` (which ALREADY prices `cache_read`/`cache_write`), and `LLMEvent.cache_creation_tokens`/`cache_read_tokens` (`llm/events.py:63-64`). Coordinates with COST-AND-TOKEN-OBSERVABILITY (its savings readout is this plan's proof surface — that plan owns the UI, this plan owns the numbers reaching it), CONTEXT-ECONOMY (DONE — compaction rewrites history, which is a cache-invalidation event this plan must reason about, §3 C4), MODEL-USE-CASES-V2 (per-use-case chains resolve different providers; caching is declared per provider *type*, so a chain fallback must not assume the cache), CONTEXT-ENGINEERING-PRINCIPLES (the sibling plan that owns tool-schema stability and failure retention — **this plan owns ONLY the cache marker + prefix ordering**; do not implement that plan's items here).
**Scope:** PersonalClaw prices cached tokens in `model_pricing.json` for 26 models and accumulates `cache_creation_tokens`/`cache_read_tokens` off every provider response — but **never asks any provider to cache anything**. Verified: zero `cache_control` blocks in core and in all 40 first-party apps. Manus's published position is that KV-cache hit rate is the single most important production-agent metric (a measured ~10× delta on their ~100:1 input:output ratio); Genspark reports a 72% inference cost reduction from Bedrock prompt caching. This plan builds **one provider-agnostic middleware seam** that (a) declares cache *intent* on the message list in a vendor-neutral shape, (b) is translated to each vendor's wire format inside that vendor's own adapter (never in core), (c) makes the assembled prefix byte-stable enough to actually hit, and (d) reports hit-rate + saved dollars so the win is measurable rather than asserted. **Soul guardrails:** (1) **provider-agnostic marker, vendor translation at the edge** — core emits a neutral `cache: true` hint on message dicts; the word `cache_control` appears ONLY inside a vendor adapter, never in `agents/`, `context.py`, or any core caller (the provider boundary is lint-enforced); (2) **byte-identical when off** — with caching unavailable or disabled, the assembled message list must be byte-for-byte what ships today (a test asserts this), so a non-caching provider is never penalised; (3) **never trade correctness for a cache hit** — no content is reordered, dropped, or deferred to improve hit rate beyond the ONE deliberate volatile-field relocation in C3; if a stability change would alter what the model is told, it is out of scope. Class **A** for the marker/middleware (no persisted state), class **B** for the one new config section + the cache-stats persistence that COST-AND-TOKEN-OBSERVABILITY owns — pre-LIFECYCLE-DOCTRINE, so those land as **plain clean breaks under the pre-1.0 banner** (tolerant reads, no gate/migration; CHANGELOG entry).

---

## Context (code recon, 2026-07-29 — verified against code, every claim has a citation)

**What already exists (more than you'd expect — the read/report half is done):**
- **The stateless completion seam is the right insertion point.** `ModelProvider.complete(messages: list[dict], *, tools, model, reasoning_effort)` (`llm/base.py:150`). The native loop owns history and passes the entire message list every turn (`agents/native/runtime.py:746`). So one decoration of that list reaches every native turn, every provider, with no per-provider caller changes.
- **Cache token accounting is ALREADY plumbed end-to-end.** `LLMEvent` carries `cache_creation_tokens` and `cache_read_tokens` (`llm/events.py:63-64`); `pricing.estimate_cost(..., cache_read_tokens=0, cache_creation_tokens=0)` (`pricing.py:63-64`) applies `cache_read` and `cache_write` rates (`pricing.py:82-83`); `model_pricing.json` carries real per-model rates for 26 models (e.g. `claude-sonnet-4.6`: `in 3.0 / cache_read 0.3 / cache_write 3.75` — a 10× read discount). **Nothing populates the request side.** Grep `cache_control` across `src/` and `../PersonalClawApps/` → zero hits. This plan closes exactly that one gap; the measurement machinery needs no new math.
- **The capability-declaration precedent to copy EXACTLY.** `ProviderCapability` (`llm/capabilities.py:47`) already carries a *graded, opt-in, safe-by-default* capability of this shape: `structured_output: StructuredOutput = StructuredOutput.NONE`, with the in-code rationale "Defaults to NONE so a provider that doesn't declare it gets the universal … path — the correct, safe behavior for every provider until it opts into native enforcement. Branded/ollama apps that support it declare it via `BrandedProviderSpec.structured_output` (a coordinated apps-repo change)" (`capabilities.py:58-63`). **`prompt_cache` follows this pattern verbatim** — same default-off grading, same apps-declare mechanism, same coordinated-apps-change shape. Do not invent a different mechanism.
- **The Anthropic adapter is the reference translation site.** `llm/anthropic.py:393::complete` builds `request_kwargs` and calls `_translate_messages(messages)` (which lifts a `system` message to the top-level `system=` param — `anthropic.py:72` documents this) and `_translate_tools(tools)`. This is where a neutral marker becomes `cache_control`. It already demonstrates the pattern of a provider-specific request-shape decision made locally (the `thinking` budget clamp + `temperature` drop, `anthropic.py:430-437`).
- **The OpenAI adapter needs NO marker** — OpenAI-family prompt caching is automatic on a stable prefix (no per-request opt-in). So the win there comes entirely from §C3 prefix stability, which makes the stability half of this plan load-bearing rather than cosmetic.

**The three real obstacles (each verified, each addressed by a numbered contract below):**
1. **A minute-precision timestamp sits at position 2 of the assembled prefix.** `context.py:773` — `parts.append(f"[CURRENT DATE] {now.strftime('%A, %Y-%m-%d %H:%M %Z')}\n\n")`, appended immediately after `render_snippet_block("critical-rules")` (`context.py:764`). This is *precisely* the anti-pattern Manus names as the canonical cache killer. Because `%H:%M` changes every minute, **every new session assembles a different prefix**, so cross-session prefix reuse is impossible today even where a provider would offer it for free. §C3 relocates it — the ONE deliberate content-position change in this plan.
2. **There is no stable `system` message at all.** Verified: the ONLY `system`-role messages the native loop ever appends are (a) a mid-conversation runtime `turn_note` at `runtime.py:712` and (b) the progressive-disclosure tool catalog (`runtime.py:901`). The assembled session context is delivered as the **first `user` message** (`runtime.py:707`, content = the fully-built turn-0 prompt from `context.build_session_context`). So the cacheable prefix is "the first user message", not a system block — the marker design in §C1 must therefore be *positional* (mark a message index), not "mark the system prompt". An executor who assumes a system prompt exists will build the wrong thing.
3. **Two per-turn mutations sit in front of history and would invalidate a naive prefix marker.** (a) `_prepare_turn_tools` (`runtime.py:851`) re-selects the surfaced tool schema **per turn** and can emit a `turn_note` appended as a `system` message *after* the first user message (`runtime.py:709-712`); (b) `_maybe_compact()` (`runtime.py:739`) can rewrite `self._messages` wholesale (`runtime.py:1297`). §C4 states the rule: mark only the **stable head** (through the assembled-context user message), never the tool block, and treat a compaction as an explicit cache-generation bump.

**Honest limits of the win (state these in the PR, do not oversell):**
- The per-turn tool-schema reselection is a *real* cache limiter, but it is **already a no-op until the tool pool exceeds K** and it *defers parameter schemas* rather than adding/removing tools (`runtime.py:871-905`). Its fix belongs to CONTEXT-ENGINEERING-PRINCIPLES, not here. This plan must not "fix" it.
- Only providers whose vendor supports explicit cache markers benefit from §C2 (Anthropic-family and Bedrock-Anthropic today). OpenAI-family benefits only via §C3. Local/Ollama/vLLM benefit only if the app declares a prefix-caching capability. **Never log a saving a provider didn't report** — the readout is driven by provider-reported `cache_read_tokens`, never by an estimate (this mirrors `model_pricing.json`'s existing "a model absent here costs 0.0 (honest: we never invent a price)" discipline).

## Design

- **S1 — the marker substrate + prefix stability.** A new core module `llm/prompt_cache.py` owns three things and nothing else: (1) the neutral **cache hint** — a `cache: True` key on a message dict, meaning "a provider that supports explicit cache markers should place a breakpoint at the END of this message"; (2) `mark_cacheable_prefix(messages, *, capability) -> list[dict]` — the one middleware function that decides *which* message gets the hint, returning the list unchanged (same object identity for each dict, byte-identical when the capability is absent or config-disabled); (3) `cache_generation` bookkeeping so a compaction or agent-definition change deliberately abandons the old prefix rather than silently missing. The native loop calls the middleware immediately before `complete()` (`runtime.py:746`) — one call site. Separately, §C3 makes the prefix worth caching: the volatile `[CURRENT DATE]` line moves from position 2 of the assembled prefix to the **end** of the assembled block, so everything ahead of it (critical rules, agent identity, workspace identity, skills, memory) is byte-stable across sessions for the same agent. The date content is unchanged and still present — only its position moves.
- **S2 — provider adoption + the proof.** `ProviderCapability` gains `prompt_cache: PromptCache = PromptCache.NONE` (graded enum: `NONE` / `AUTOMATIC` / `EXPLICIT`), declared by provider types exactly as `structured_output` is. The core Anthropic adapter translates the hint into `cache_control: {"type": "ephemeral"}` on the marked block; the core OpenAI adapter declares `AUTOMATIC` and translates nothing. A `cache_hit_pct` + saved-dollars aggregate rides the existing `cache_read_tokens`/`cache_creation_tokens` accumulation into the stats surface COST-AND-TOKEN-OBSERVABILITY renders. The apps-repo half (bedrock-models declaring `EXPLICIT`, openai-compatible/ollama/vllm declaring their real posture) is a **coordinated apps change listed as an owner task**, not core work.
- **What this is NOT:** not a response cache (identical-prompt→stored-answer; that changes semantics and is explicitly out of scope); not a semantic/embedding cache; not context compaction (CONTEXT-ECONOMY owns that, and this plan only reacts to it); not tool-schema stabilisation or failure retention (CONTEXT-ENGINEERING-PRINCIPLES owns both); not a new provider capability negotiation protocol (it extends the one that exists).

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

### C1 — The neutral cache hint + middleware (`llm/prompt_cache.py`, new)

```python
class PromptCache(str, Enum):
    """Graded, opt-in prompt-cache support — mirrors StructuredOutput
    (capabilities.py:58) deliberately: default NONE = the safe path for every
    provider until it declares otherwise."""
    NONE = "none"            # no caching; middleware is a byte-identical no-op
    AUTOMATIC = "automatic"  # vendor caches a stable prefix with no request marker (OpenAI-family)
    EXPLICIT = "explicit"    # vendor requires a per-request breakpoint marker (Anthropic-family)

# The neutral marker. Set by the middleware on AT MOST ONE message dict.
#   {"role": "user", "content": "...", "cache": True}
# Meaning: "a provider with EXPLICIT support should place a cache breakpoint at
# the END of this message." A provider that does not understand the key IGNORES it —
# every existing adapter already ignores unknown message keys, so this is additive
# and safe by construction.
CACHE_HINT_KEY = "cache"

def mark_cacheable_prefix(
    messages: list[dict], *, support: PromptCache, generation: int = 0
) -> list[dict]:
    """Return ``messages`` with the stable-head message hinted as cacheable.

    Rules (all of them load-bearing — an executor must implement every clause):
      * ``support is PromptCache.NONE`` → return ``messages`` UNCHANGED (same list
        object, same dicts, no copies). The byte-identical guarantee.
      * ``support is PromptCache.AUTOMATIC`` → return ``messages`` unchanged too:
        the vendor needs no marker, and adding a key it ignores would still make
        the dicts differ from today's for no benefit.
      * ``support is PromptCache.EXPLICIT`` → hint the LAST message of the STABLE
        HEAD (§C4 defines the head). Never hint more than one message: vendors cap
        breakpoints (Anthropic allows 4) and one at the head boundary captures the
        large, reusable span. Never hint a message whose role is ``tool``.
      * The returned list is a NEW list; the hinted dict is a SHALLOW COPY with the
        hint added (never mutate a caller's dict — the native loop keeps
        ``self._messages`` as durable turn state and a mutation would persist a
        wire-format detail into conversation history).
      * ``generation`` is opaque here; it exists so a caller can force a miss
        (§C4) — the middleware does not interpret it.
    """

def cache_stats_from_event(ev) -> tuple[int, int]:
    """``(cache_read_tokens, cache_creation_tokens)`` off an EVENT_COMPLETE.
    Zero when the provider reported nothing — NEVER estimated (mirrors
    model_pricing.json's "we never invent a price" rule)."""
```

Placement rule: this module imports NOTHING vendor-specific and contains no vendor string. `grep -i "cache_control\|ephemeral" src/personalclaw/llm/prompt_cache.py` must return zero — the neutral/edge split is the whole point.

### C2 — Capability declaration + vendor translation (edge-only)

```python
# llm/capabilities.py — additive field on the EXISTING dataclass (capabilities.py:47)
prompt_cache: PromptCache = PromptCache.NONE   # default-off, exactly like structured_output

# llm/anthropic.py::complete — the ONLY core site that may say "cache_control".
# After `system_prompt, anth_messages = _translate_messages(messages)` (anthropic.py:417):
#   for a hinted message, append cache_control to its LAST content block:
#     block["cache_control"] = {"type": "ephemeral"}
#   If the hinted message was lifted into the top-level `system=` param, the marker
#   attaches to the system block instead (system must then be block-shaped, not a
#   bare string) — the translation owns this asymmetry, core never learns it.

# llm/openai.py — declares PromptCache.AUTOMATIC and translates NOTHING.
```

Provider-boundary note: vendor cache syntax in a *core* adapter is permitted **only** because `llm/anthropic.py` and `llm/openai.py` are already the two in-core protocol clients enumerated in `docs/architecture/provider-boundary.md`. No new exception is created, and **no vendor cache syntax may appear anywhere else in core** — a rails test asserts it (T2.4).

### C3 — Prefix stability: the one content-position change (`context.py`)

```
TODAY   (context.py:764-773, verified):
  [critical-rules snippet]
  [CURRENT DATE] Tuesday, 2026-07-29 14:23 JST      ← changes EVERY MINUTE
  [agent identity / runtime]
  [workspace identity] [skills] [memory] [lessons] [hooks] [thread history]

AFTER:
  [critical-rules snippet]
  [agent identity / runtime]
  [workspace identity] [skills] [memory] [lessons] [hooks] [thread history]
  [CURRENT DATE] Tuesday, 2026-07-29 14:23 JST      ← last line of the assembled block
```

The date line's **text is byte-identical**; only its position changes. Rationale to keep in the code comment: everything ahead of the volatile line becomes a stable prefix reusable across sessions for the same agent, and recency also *helps* the model treat "today" as current (it lands in the most-recent attention span rather than being buried at the top). This is the only reordering this plan performs; no other content moves, and nothing is added or removed.

Two clauses an executor must honor:
- `context.build_session_context` has an `is_custom` branch that skips skills/workspace identity (`context.py:751-758`). The date line must be last in **both** branches.
- Any test asserting the date appears at a specific offset must be updated in the same commit (grep `CURRENT DATE` in `tests/`), not worked around.

### C4 — What counts as the "stable head", and when the cache generation bumps

The stable head is **messages[0 .. k]** where `k` is the index of the assembled-context message — i.e. the first `user` message the native loop appends (`runtime.py:707`), which carries the whole built prefix. Everything after it (the per-turn `system` turn-note at `runtime.py:712`, the tool catalog at `runtime.py:901`, and all conversation turns) is **outside** the head and is never hinted.

Explicit non-goals inside this contract, so nobody widens it:
- The **tools kwarg is never marked.** It is reselected per turn (`runtime.py:851-905`) and stabilising it belongs to CONTEXT-ENGINEERING-PRINCIPLES.
- The turn-note `system` message is never marked (it is per-turn runtime metadata by construction — `runtime.py:711` says so).

Generation bump (forces a deliberate miss rather than a silent one) on:
1. **Compaction** — `_maybe_compact()` replacing `self._messages` (`runtime.py:1297`) invalidates everything; the loop increments its generation counter at that point.
2. **Agent-definition change** mid-session (model or prompt swap), which rewrites the prefix content anyway.
A bump is a normal, expected event; it is logged at DEBUG only (never a warning — this is not an error condition).

### C5 — Config (§2.1 five-point wiring — all five points required)

```python
# config/loader.py, inside the models/llm section (follow the nearest existing sibling):
prompt_cache_enabled: bool = field(
    default=True,
    metadata=_meta("Prompt caching", "Ask providers that support it to cache the stable "
                   "prompt prefix. Reduces cost and latency on multi-turn work. No effect "
                   "on providers without cache support."),
)
```
Wire through: (1) dataclass + `_meta`; (2) `load()`'s explicit mapping; (3) `to_dict()`; (4) the `_EDITABLE_CONFIG` PATCH allowlist (`dashboard/handlers/core.py:436`, `{"type": "bool"}`); (5) a frontend control in the Models settings panel. `tests/test_config_roundtrip.py` catches misses — complete the wiring, don't fight it.

**Default-ON justification** (an executor will ask): caching is semantically transparent — the model sees the same tokens either way — and every provider path degrades to a byte-identical no-op. It is a cost optimisation with no behavioral surface, so opt-out is the honest default. The switch exists for diagnosis (ruling caching out when debugging a provider), not because caching is risky.

### Integration points

- **Calls:** `ProviderCapability` (`llm/capabilities.py:47`), `pricing.estimate_cost` (`pricing.py:63`, unchanged — already accepts both cache args), `AppConfig.load()`.
- **Called by:** `agents/native/runtime.py` (ONE new call immediately before `complete()` at `:746`); the two core adapters (`llm/anthropic.py::complete`, `llm/openai.py`) read the capability.
- **Storage owned:** none in S1 (class A). The cache-hit aggregate persists through whatever store COST-AND-TOKEN-OBSERVABILITY defines — **this plan must not invent a second stats file**.
- **Deliberately NOT touched:** compaction internals (CONTEXT-ECONOMY), tool retrieval/`_prepare_turn_tools` (CONTEXT-ENGINEERING-PRINCIPLES), ACP runtimes (an external CLI owns its own request shape — out of scope by construction, and the plan says so rather than pretending parity), `stream()`'s stateful history path (only the stateless `complete()` path is in scope; `stream()` is the legacy simple-prompt adapter).

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — The marker substrate + prefix stability

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | `llm/prompt_cache.py`: `PromptCache` enum, `CACHE_HINT_KEY`, `mark_cacheable_prefix` (all C1 clauses incl. shallow-copy-never-mutate), `cache_stats_from_event`; unit tests incl. **the byte-identical assertion** for `NONE`/`AUTOMATIC` (assert the returned list `is` the input and no dict gained a key) | `src/personalclaw/llm/prompt_cache.py`, `tests/test_prompt_cache.py` | `NONE`/`AUTOMATIC` return the input object unchanged; `EXPLICIT` hints exactly one non-`tool` message; a caller's dicts are never mutated; grep for vendor strings in the new module returns zero |
| T1.2 | Wire the middleware into the native loop: one call before `complete()`; loop-owned `_cache_generation` int bumped on compaction (`runtime.py:1297`) and on agent-definition change; DEBUG log on bump | `src/personalclaw/agents/native/runtime.py`, tests | with a `NONE` provider the message list reaching `complete()` is byte-identical to today (test asserts); with `EXPLICIT` exactly one hint rides; a compaction bumps the generation |
| T1.3 | C3 prefix stability: move the `[CURRENT DATE]` line to the END of the assembled block in BOTH the custom and non-custom branches; update any test asserting its position; code comment states the two reasons (cache stability + recency) | `src/personalclaw/context.py`, affected tests | the date text is unchanged and present in both branches; assembling twice ~1 minute apart yields byte-identical output up to the final date line (test) |
| T1.4 | Config field `prompt_cache_enabled` wired through all five §2.1 points incl. the frontend control; middleware consults it (disabled ⇒ treated as `NONE`) | `config/loader.py`, `dashboard/handlers/core.py`, `web/src/pages/settings/` (Models panel), tests | `test_config_roundtrip.py` green; PATCH round-trips; toggling off yields the byte-identical path |
| V1 | Validation as a user: bind a real Anthropic-family model on an isolated dev home; run a 3-turn conversation; confirm from the gateway log + provider-reported usage that turn 2+ report non-zero `cache_read_tokens` and turn 1 reports `cache_creation_tokens`; toggle the config off and confirm both go to zero; `make lint` + targeted pytest + `make test` + web typecheck/test/build | — | holds |

### Session 2 — Provider adoption + the measurement surface

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `ProviderCapability.prompt_cache` field (default `NONE`, docstring mirroring `structured_output`'s rationale); core OpenAI adapter declares `AUTOMATIC` | `llm/capabilities.py`, `llm/openai.py`, tests | default-off preserved for every undeclared provider; OpenAI path translates nothing |
| T2.2 | Anthropic translation: hinted message → `cache_control: {"type": "ephemeral"}` on its last content block, incl. the system-lift asymmetry (block-shaped `system=` when the head was lifted); declares `EXPLICIT`; test asserts the wire shape and that an UNhinted list produces today's exact request kwargs | `llm/anthropic.py`, tests | marker lands on exactly one block; unhinted requests are byte-identical to today; system-lift case covered |
| T2.3 | Cache-hit aggregation: accumulate provider-reported cache tokens per turn and expose `cache_read_tokens`/`cache_creation_tokens`/`cache_hit_pct` + saved-USD (via the existing `estimate_cost` rates) on the turn-complete telemetry COST-AND-TOKEN-OBSERVABILITY consumes; **never estimate when the provider reported nothing** | `agents/native/runtime.py`, `stats.py` (or the store that plan defines — do NOT create a second one), tests | a real 3-turn run reports a rising hit-rate and a non-zero saved-USD; an unpriced model reports tokens with 0.0 saved (honest-zero test) |
| T2.4 | Rails test: vendor cache syntax (`cache_control`, `"ephemeral"`) appears ONLY in `llm/anthropic.py` — an AST/grep sweep over `src/` fails on any other occurrence, mirroring the existing provider-boundary residue sweep | `tests/` (beside the existing rails sweep) | the sweep fails when a violation is introduced (prove it by temporarily adding one) |
| V2 | Validation as a user: with a real Anthropic-family model and a real OpenAI-family model bound, run the same 5-turn conversation on each; record hit-rate + saved-USD for both; confirm the OpenAI path reports vendor-side cache reads with no marker sent; confirm a local/Ollama model (undeclared ⇒ `NONE`) runs byte-identically with zeros; full local gate | — | holds |

## Owner tasks (real world)
1. **Coordinated apps-repo change (S2 follow-up, owner-sequenced):** declare the real posture on the branded model apps — `bedrock-models` (`EXPLICIT` for Anthropic-family models on Converse), `openai-compatible`/`openrouter-models` (`AUTOMATIC` where the upstream caches), `ollama-models`/`vllm-models` (`EXPLICIT` only if prefix caching is actually enabled server-side; otherwise leave `NONE` — an undeclared provider must never be assumed).
2. **Decide whether to widen the marker beyond one breakpoint** after seeing real hit-rates. Anthropic permits 4; a second breakpoint after the tool block only pays once CONTEXT-ENGINEERING-PRINCIPLES stabilises tool schemas. Deliberately deferred, not forgotten.
3. **Confirm the default-ON call** (§C5). The plan argues for it on transparency grounds; it is reversible with one field default.

## Risks & open questions
- **Silent no-win.** The most likely failure is shipping the substrate and hitting nothing because tool schemas churn per turn. Mitigation: V1/V2 require *provider-reported* cache reads before the session may be marked complete — an unmeasured claim is not a done task. If hit-rates are near-zero, that is a DISCOVERY pointing at CONTEXT-ENGINEERING-PRINCIPLES, not a reason to widen this plan's scope.
- **Cache-write cost exceeding the read saving on short sessions.** Writes are priced above base input (`cache_write 3.75` vs `in 3.0` for sonnet-4.6). A one-turn session pays ~25% more on the cached span. Accepted: the marker rides only the *large stable head*, and multi-turn agentic work (the dominant shape here, per the ~100:1 ratio) amortises it within two turns. The saved-USD readout will show this honestly, including when it is negative — do not hide a negative.
- **Provider drift.** Vendor cache syntax and TTLs change. Containment is the point of §C2: exactly one file per vendor to update.
- **Open:** whether `stream()`'s stateful path should also mark (deferred — it is the legacy simple-prompt adapter; the native loop uses `complete()`).
- **Open:** ACP-backed runtimes cannot participate (the external CLI owns its request shape). Recorded as a known parity gap rather than papered over; ACP-AGENT-PARITY may revisit.

## Execution log

_(empty — no session has run yet)_
