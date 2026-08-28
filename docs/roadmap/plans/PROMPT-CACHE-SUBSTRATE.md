# PROMPT-CACHE-SUBSTRATE

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/PCS.md`](../atomic/PCS.md) as 8 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Prompt-Cache Substrate — One Middleware Seam That Makes Every Turn Cheaper

**Status:** DESIGNED (rev 2) — created 2026-07-29; **redesigned 2026-07-30** after a code audit falsified rev 1's central mechanism (owner ask: capability gap analysis; owner direction: "an elaborated base middleware type subsystem that just plugs into the right location and provides all the places with prompt caching benefits"; owner ruling 2026-07-29: "Redesign the plan now and implement when it should be implemented in natural order of roadmap items wherever it fits")
**Created:** 2026-07-29
**Wave:** 2 (S1: wire-order repair + prefix stability; S2: the marker + provider adoption; S3: the measurement surface)
**Depends on:** nothing hard. Builds entirely on shipped seams: `ModelProvider.complete()` (`llm/base.py:150`), `ProviderCapability` (`llm/capabilities.py:47`), `pricing.estimate_cost` (which ALREADY prices `cache_read`/`cache_write` — `pricing.py:63-83`), and `LLMEvent.cache_creation_tokens`/`cache_read_tokens` (`llm/events.py:63-64`). Coordinates with COST-AND-TOKEN-OBSERVABILITY (its savings readout is this plan's proof surface — that plan owns the UI, this plan owns the numbers reaching it), CONTEXT-ECONOMY (DONE — compaction rewrites history, a cache-invalidation event this plan must reason about, §C5), MODEL-USE-CASES-V2 (per-use-case chains resolve different providers; caching is declared per provider *type*, so a chain fallback must not assume the cache), CONTEXT-ENGINEERING-PRINCIPLES (the sibling plan that owns tool-schema stability and failure retention — **this plan owns ONLY the cache marker + prefix/wire ordering**; do not implement that plan's items here).

**Scope:** PersonalClaw prices cached tokens in `model_pricing.json` for 26 models and *declares* `cache_creation_tokens`/`cache_read_tokens` on `LLMEvent` — but **never asks any provider to cache anything, and never reads a cache number off any response**. Verified 2026-07-30: zero `cache_control` blocks in core and in all first-party apps; zero `cachePoint` in the apps repo. A known production-agent position is that KV-cache hit rate is the single most important production-agent metric (a measured ~10× delta on a ~100:1 input:output ratio); another report cites a 72% inference cost reduction from Bedrock prompt caching. This plan builds **one provider-agnostic middleware seam** that (a) declares cache *intent* on the message list in a vendor-neutral shape, (b) is translated to each vendor's wire format inside that vendor's own adapter (never in core), (c) makes the assembled prefix actually reachable and byte-stable enough to hit, and (d) reports hit-rate + saved dollars so the win is measurable rather than asserted.

**Soul guardrails:** (1) **provider-agnostic marker, vendor translation at the edge** — core emits a neutral hint on message dicts; the word `cache_control` appears ONLY inside a vendor adapter, never in `agents/`, `context.py`, or any core caller (the provider boundary is lint-enforced); (2) **byte-identical when off** — with caching unavailable or disabled, the request that reaches the wire must be byte-for-byte what ships today (a test asserts this), so a non-caching provider is never penalised; (3) **never trade correctness for a cache hit** — no content is reordered, dropped, or deferred to improve hit rate beyond the two deliberate, enumerated relocations in §C2/§C3; if a stability change would alter *what the model is told*, it is out of scope; (4) **measure before claiming** — no session may be marked complete on an asserted win; a provider-reported cache read is the only acceptable evidence (§S3).

Class **A** for the marker/middleware and the wire-order repair (no persisted state), class **B** for the one new config section + the cache-stats persistence that COST-AND-TOKEN-OBSERVABILITY owns — so those land as **plain clean breaks under the pre-1.0 banner** (tolerant reads, no gate/migration; CHANGELOG entry).

---

## Why this plan was redesigned (read this before the Design — rev 1 would not have worked)

Rev 1 was written from a reading of the call sites; a full audit of the **wire** path found three defects. Two were false premises, one was a latent data-loss bug the plan would have introduced. All four findings below are reproduced from code, with the reproduction recorded so an executor need not re-derive them.

**F1 — FATAL to rev 1's mechanism: the "stable head" is not the head of the wire request.** Rev 1's §C4 defined the cacheable span as `messages[0..k]` where `k` is the assembled-context *user* message, and marked a breakpoint at its end. But `_translate_messages` (`anthropic.py:108-183`) **hoists every `system`-role message out of the list and concatenates them into the top-level `system=` parameter** (`anthropic.py:183`), which Anthropic places **ahead of `messages[0]`** in the served prompt. The native loop appends exactly one `system` message: the per-turn `turn_note` (`runtime.py:711-712`), which carries the tool catalog and group stubs inside it (joined at `runtime.py:930`). Reproduced:

```
$ python -c "from personalclaw.llm.anthropic import _translate_messages; ..."
system= param -> 'VOLATILE_TURN_NOTE'
messages    -> [{'role':'user','content':'ASSEMBLED_CONTEXT_PREFIX'}, ...]
```

So a **volatile, per-turn string is the first thing the model sees**, ahead of the entire stable prefix. A cache breakpoint at the end of `messages[0]` covers a span whose *prefix* changes every turn — and prompt caching matches on an exact prefix, so **the hit rate would have been zero while every test passed**. This is the exact failure mode rev 1's own risk section called "silent no-win," and it was structural, not incidental. §C2 fixes the wire order; it is now S1's first task and the precondition for the marker having any value.

**F2 — a latent bug rev 1 would have SHIPPED: moving the date to the tail puts it in the truncation window.** Rev 1's §C3 moved `[CURRENT DATE]` to the **end** of the assembled block. But `build_session_context` hard-truncates from the **end**: `context = context[:_MAX_CONTEXT_CHARS]` with `_MAX_CONTEXT_CHARS = 165_000` (`context.py:120, 992-1002`). A user with large memory/skills/thread history would silently lose the date line entirely — the model would no longer know what day it is, a correctness regression traded for a cache hit, which guardrail (3) forbids. §C3 keeps the relocation (it is the right idea) but makes the date line **truncation-immune** by appending it after the truncation step, and states the invariant as a test.

**F3 — a false premise about existing plumbing.** Rev 1 asserted "cache token accounting is ALREADY plumbed end-to-end … the measurement machinery needs no new math." Half true, and the wrong half was load-bearing. The **consumers** exist (`stats.py:42-84`, `pricing.py:63-83`, `chat_runner.py:2530-2574`, `guardrails/model_call.py:296-297`, `acp/adapter.py:31-32`); there is **no producer**. Neither adapter reads a cache field off a response: `anthropic.py` parses only `usage.input_tokens` / `usage.output_tokens` (`:302-309`, `:457-464`) and never constructs `LLMEvent` with either cache argument. So `cache_read_tokens` is **always 0**, `estimate_cost` always applies a zero cache term, and the stats counters can only ever increment by 0. Reading the two `usage.cache_*_input_tokens` fields off the Anthropic response is **new work this plan owns** — it is now T3.1, and without it the whole plan is unfalsifiable.

**F4 — a false premise about the capability precedent.** Rev 1 said `prompt_cache` should copy `structured_output` "verbatim … same apps-declare mechanism." The field exists as described (`capabilities.py:63`, default `NONE`, with the documented rationale) — but per AUTONOMY-GUARDRAILS' execution log its apps-side half shipped as a **documented DEVIATION**: core ships the descriptor and apps declare it through `BrandedProviderSpec`, a deliberate cross-repo seam. It is a valid precedent for the *shape* of a graded default-off capability, and this plan copies that shape. It is **not** evidence that the apps-side declaration is free: that half is a coordinated apps-repo change, and it is listed as an owner task rather than assumed. Do not delete or "clean up" `structured_output` on the belief that it is dead code — it is not.

---

## Context (code recon, 2026-07-30 — verified against code, every claim has a citation)

**What already exists:**
- **The stateless completion seam is the right insertion point.** `ModelProvider.complete(messages: list[dict], *, tools, model, reasoning_effort)` (`llm/base.py:150`). The native loop owns history and passes the entire message list every turn (`agents/native/runtime.py:747`). One decoration of that list reaches every native turn, every provider, with no per-provider caller changes.
- **The pricing half is real and needs no new math.** `pricing.estimate_cost(..., cache_read_tokens=0, cache_creation_tokens=0)` (`pricing.py:63-64`) applies `cache_read` and `cache_write` rates (`pricing.py:82-83`); `model_pricing.json` carries real per-model rates for 26 models (e.g. `claude-sonnet-4.6`: `in 3.0 / cache_read 0.3 / cache_write 3.75` — a 10× read discount). What is missing is the **producer** (F3), not the arithmetic.
- **The capability-declaration shape to copy.** `ProviderCapability.structured_output: StructuredOutput = StructuredOutput.NONE` (`capabilities.py:63`), with the in-code rationale "Defaults to NONE so a provider that doesn't declare it gets the universal … path — the correct, safe behavior for every provider until it opts into native enforcement." `prompt_cache` copies this **shape** (graded enum, default-off, apps declare via `BrandedProviderSpec`) — see F4 for what that precedent does and does not license.
- **The Anthropic adapter is the reference translation site.** `llm/anthropic.py:393::complete` builds `request_kwargs`, calls `_translate_messages(messages)` and `_translate_tools(tools)`. It already demonstrates a provider-specific request-shape decision made locally (the `thinking` budget clamp + `temperature` drop, `:428-437`).
- **The OpenAI adapter needs NO marker** — OpenAI-family prompt caching is automatic on a stable prefix (no per-request opt-in). Its entire win comes from §C2 + §C3, which makes the ordering half of this plan load-bearing rather than cosmetic.

**The obstacles (each verified, each addressed by a numbered contract):**
1. **The wire order defeats a naive prefix marker** — F1 above. Addressed by §C2.
2. **A minute-precision timestamp sits at position 2 of the assembled prefix.** `context.py:773` — `parts.append(f"[CURRENT DATE] {now.strftime('%A, %Y-%m-%d %H:%M %Z')}\n\n")`, immediately after `render_snippet_block("critical-rules")` (`context.py:764`). Because `%H:%M` changes every minute, **every new session assembles a different prefix**, so cross-session reuse is impossible even where a provider would offer it free. This is precisely the well-known anti-pattern that is the canonical cache killer. Addressed by §C3 — with F2's truncation clause.
3. **There is no stable `system` message today.** There is exactly ONE `system`-role message the native loop ever appends: the per-turn `turn_note` at `runtime.py:712`. It is the joined `notes` list from `_prepare_turn_tools` (`runtime.py:930`), so the group-change note, the tool catalog and the inactive-group stubs all ride inside that single volatile message. The assembled session context ships as the **first `user` message** (`runtime.py:707`). So the cacheable span is "the assembled-context message", not "the system prompt"; and after §C2 it is "the stable system block **plus** that message". An executor who assumes a stable system prompt already exists will build the wrong thing.
4. **Compaction rewrites history wholesale.** `_maybe_compact()` (`runtime.py:739` → `:1292`) can replace `self._messages`. Addressed by §C5's generation bump.

**Honest limits of the win (state these in the PR, do not oversell):**
- The per-turn tool-schema reselection is a real cache limiter, but it is **a no-op until the active tool pool exceeds `DEFAULT_K = 48`** (`tool_retrieval.py:30`) and it *defers parameter schemas* rather than adding/removing tools (`runtime.py:869-930`). Its fix belongs to CONTEXT-ENGINEERING-PRINCIPLES. This plan must not "fix" it — but §C2 does move the catalog **behind** the stable span so its churn stops poisoning the prefix.
- Only vendors with explicit cache markers benefit from §C4 (Anthropic-family and Bedrock-Anthropic today). OpenAI-family benefits via §C2+§C3 only. Local/Ollama/vLLM benefit only if the app declares a prefix-caching capability.
- **Never log a saving a provider didn't report.** The readout is driven by provider-reported cache tokens, never an estimate — mirroring `model_pricing.json`'s existing "a model absent here costs 0.0 (honest: we never invent a price)" discipline.

## Design

- **S1 — make the prefix reachable and stable (no marker yet, and valuable on its own).** Two ordering repairs, both of which pay off *immediately* for automatic-caching vendors and are the precondition for the marker to hit at all. (a) §C2 splits `system`-role messages by stability at the translation edge: the stable assembled context leads the served prompt, and per-turn runtime notes move to the **end** of the message list, so nothing volatile precedes the stable span. (b) §C3 moves the volatile `[CURRENT DATE]` line out of prefix position 2 — truncation-immune per F2. After S1, an OpenAI-family provider can hit a cross-turn prefix with **zero** further work.
- **S2 — the neutral marker + provider adoption.** A new core module `llm/prompt_cache.py` owns the vendor-neutral hint and the one middleware function that decides which message carries it; `ProviderCapability` gains `prompt_cache: PromptCache = PromptCache.NONE`; the core Anthropic adapter translates the hint into `cache_control: {"type": "ephemeral"}`; the core OpenAI adapter declares `AUTOMATIC` and translates nothing. A rails test pins vendor cache syntax to the one adapter.
- **S3 — the proof.** Read the two cache fields off the Anthropic response into `LLMEvent` (**the missing producer, F3**), aggregate a hit-rate + saved-USD off provider-reported numbers only, and hand them to the surface COST-AND-TOKEN-OBSERVABILITY renders. **S3 is not optional polish — it is what makes S1/S2 falsifiable.** Ordering note: S3's producer is what V2 measures against, so if S2 and S3 land in one session, do T3.1 first.
- **What this is NOT:** not a response cache (identical-prompt→stored-answer changes semantics; explicitly out of scope); not a semantic/embedding cache; not context compaction (CONTEXT-ECONOMY owns it, this plan only reacts); not tool-schema stabilisation or failure retention (CONTEXT-ENGINEERING-PRINCIPLES owns both); not a new capability-negotiation protocol (it extends the one that exists).

## Contracts & Interfaces (conventions per [AGENTS.md](../../../AGENTS.md))

### C1 — The neutral cache hint + middleware (`llm/prompt_cache.py`, new)

```python
class PromptCache(str, Enum):
    """Graded, opt-in prompt-cache support — copies the SHAPE of
    StructuredOutput (capabilities.py:63) deliberately: default NONE = the
    safe path for every provider until it declares otherwise."""
    NONE = "none"            # no caching; middleware is a byte-identical no-op
    AUTOMATIC = "automatic"  # vendor caches a stable prefix with no request marker (OpenAI-family)
    EXPLICIT = "explicit"    # vendor requires a per-request breakpoint marker (Anthropic-family)

# The neutral marker. Set by the middleware on AT MOST ONE message dict.
#   {"role": "user", "content": "...", "cache": True}
# Meaning: "a provider with EXPLICIT support should place a cache breakpoint at
# the END of this message." A provider that does not understand the key IGNORES
# it — every existing adapter passes unknown message keys over silently, so this
# is additive and safe by construction.
CACHE_HINT_KEY = "cache"

def mark_cacheable_prefix(
    messages: list[dict], *, support: PromptCache, generation: int = 0
) -> list[dict]:
    """Return ``messages`` with the stable-head message hinted as cacheable.

    Rules (all load-bearing — an executor must implement every clause):
      * ``support is PromptCache.NONE`` → return ``messages`` UNCHANGED (same
        list object, same dicts, no copies). The byte-identical guarantee.
      * ``support is PromptCache.AUTOMATIC`` → return ``messages`` unchanged
        too: the vendor needs no marker, and adding a key it ignores would make
        the dicts differ from today's for no benefit.
      * ``support is PromptCache.EXPLICIT`` → hint the LAST message of the
        STABLE HEAD (§C5 defines the head). Never hint more than one message:
        vendors cap breakpoints (Anthropic allows 4) and one at the head
        boundary captures the large, reusable span. Never hint a ``tool`` role.
      * The returned list is a NEW list; the hinted dict is a SHALLOW COPY with
        the hint added (never mutate a caller's dict — the native loop keeps
        ``self._messages`` as durable turn state and a mutation would persist a
        wire-format detail into conversation history).
      * ``generation`` is opaque here; it exists so a caller can force a miss
        (§C5) — the middleware does not interpret it.
    """
```

Placement rule: this module imports NOTHING vendor-specific and contains no vendor string. `grep -i "cache_control\|ephemeral" src/personalclaw/llm/prompt_cache.py` must return zero — the neutral/edge split is the whole point.

### C2 — Wire order: stability-ordered `system` messages (`llm/anthropic.py`) — **the fix for F1**

The defect: `_translate_messages` concatenates **all** `system` messages into `system=` (`anthropic.py:129-132, 183`), which Anthropic serves **ahead of `messages[0]`**. Since the native loop's only `system` messages are per-turn volatile ones, today's served prompt begins with a string that changes every turn.

The contract: **`system` messages are split by stability, not merged blindly.**

```
TODAY (served order, verified by reproduction):
  system=  ← the per-turn turn_note (note+catalog+stubs)   ★ VOLATILE, FIRST
  messages[0] = assembled context (stable)
  messages[1..] = conversation

AFTER:
  system=  ← the STABLE assembled context only            ★ stable, first
  messages[0..] = conversation
  messages[-1] = the per-turn turn_note (note+catalog+stubs)       ★ volatile, LAST
```

Rules an executor must honor:
- The native loop **tags** its messages so the edge can tell stable from volatile; the adapter must not sniff content. Add a neutral, documented key set by the loop (e.g. `{"role": "system", "content": ..., "volatile": True}` on the turn-note message at `runtime.py:712` — the one and only volatile `system` message). Untagged `system` messages keep **today's** behavior (hoisted into `system=`) — a provider or caller that knows nothing of this key is unaffected.
- A volatile note is delivered as a **`system`-role message at the end of the message list** if the vendor permits mid-list system content, and otherwise as the closing `user`-role turn. Whichever shape is chosen, **the note's text is unchanged and it is still delivered every turn** — this is a position change, never a content change. Guardrail (3) applies: if a vendor cannot carry it late without altering meaning, leave that vendor's order alone and record it.
- **The content the model receives must be equivalent, and a test must prove it**: assert that the concatenation of all delivered text (system + messages) contains every note that shipped before, exactly once.
- This is an ordering change with a **behavioral surface** (the model sees the turn-note late rather than early). Recency generally *helps* an instruction take effect, but V1 must confirm no regression in tool-catalog comprehension — specifically, that a model still calls `tool_schema` for a deferred tool after the catalog moved late. If it regresses, that is a DISCOVERY and a stop, not something to paper over.

### C3 — Prefix stability: the volatile date line (`context.py`) — **with F2's truncation fix**

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
  [CURRENT DATE] Tuesday, 2026-07-29 14:23 JST      ← appended AFTER truncation
```

The date line's **text is byte-identical**; only its position moves. Keep this rationale in the code comment: everything ahead of the volatile line becomes a stable prefix reusable across sessions for the same agent, and recency also helps the model treat "today" as current.

Three clauses an executor must honor — the third is the F2 bug fix and is **not optional**:
- `build_session_context` has an `is_custom` branch that skips skills/workspace identity (`context.py:751-758`). The date line must be last in **both** branches.
- Any test asserting the date's position must be updated in the same commit — `tests/test_context.py:698-707` and the freeze fixture at `tests/test_context_engine.py:45` are the known sites; grep `CURRENT DATE` for others.
- **The date line must be appended AFTER the `_MAX_CONTEXT_CHARS` truncation step** (`context.py:992-1002`), not before. Truncation cuts from the end (`context[:_MAX_CONTEXT_CHARS]`), so a tail-positioned date is the first thing a large context loses. Structure it as: assemble → truncate → append the date line. A test must assert the date survives when the assembled body exceeds `_MAX_CONTEXT_CHARS` (build an oversized context and assert `[CURRENT DATE]` is still present and last). Without this clause the change silently regresses "what day is it" for exactly the heaviest users.

### C4 — Capability declaration + vendor translation (edge-only)

```python
# llm/capabilities.py — additive field on the EXISTING dataclass (capabilities.py:47)
prompt_cache: PromptCache = PromptCache.NONE   # default-off, same shape as structured_output

# llm/anthropic.py::complete — the ONLY core site that may say "cache_control".
# After `system_prompt, anth_messages = _translate_messages(messages)` (anthropic.py:417):
#   for a hinted message, append cache_control to its LAST content block:
#     block["cache_control"] = {"type": "ephemeral"}
#   After §C2 the stable span IS the `system=` param, so the marker normally
#   attaches to the last system block — which requires `system=` to be
#   BLOCK-SHAPED (a list of {"type":"text",...}) rather than a bare string.
#   The translation owns this asymmetry; core never learns it.

# llm/openai.py — declares PromptCache.AUTOMATIC and translates NOTHING.
```

Provider-boundary note: vendor cache syntax in a *core* adapter is permitted **only** because `llm/anthropic.py` and `llm/openai.py` are already the two in-core protocol clients enumerated in `docs/architecture/provider-boundary.md`. No new exception is created, and **no vendor cache syntax may appear anywhere else in core** — a rails test asserts it (T2.4).

### C5 — What counts as the "stable head", and when the cache generation bumps

After §C2, the stable head is **the assembled-context content that reaches `system=`** plus, for vendors without a system param, `messages[0]`. Everything after it — every conversation turn, the per-turn runtime notes now at the tail, and the tool block — is **outside** the head and is never hinted.

Explicit non-goals inside this contract, so nobody widens it:
- The **tools kwarg is never marked.** It is reselected per turn (`runtime.py:851-930`) and stabilising it belongs to CONTEXT-ENGINEERING-PRINCIPLES.
- The volatile runtime notes are never marked (they are per-turn metadata by construction — `runtime.py:711` says so).

Generation bump (forces a deliberate miss rather than a silent one) on:
1. **Compaction** — `_maybe_compact()` replacing `self._messages` (`runtime.py:1292`) invalidates everything; the loop increments its generation counter at that point.
2. **Agent-definition change** mid-session (model or prompt swap), which rewrites the prefix content anyway.

A bump is a normal, expected event; log at DEBUG only (never a warning — this is not an error condition).

### C6 — Config (§2.1 five-point wiring — all five points required)

```python
# config/loader.py, inside the models/llm section (follow the nearest existing sibling):
prompt_cache_enabled: bool = field(
    default=True,
    metadata=_meta("Prompt caching", "Ask providers that support it to cache the stable "
                   "prompt prefix. Reduces cost and latency on multi-turn work. No effect "
                   "on providers without cache support."),
)
```

Wire through: (1) dataclass + `_meta`; (2) `load()`'s explicit mapping; (3) `to_dict()`; (4) the `_EDITABLE_CONFIG` PATCH allowlist (`dashboard/handlers/core.py:436` — the `_EDITABLE_CONFIG` dict, `{"type": "bool"}`); (5) a frontend control in the Models settings panel. `tests/test_config_roundtrip.py` catches misses — complete the wiring, don't fight it.

**Scope of the switch (sharpened in rev 2):** it gates the **marker** (§C1/§C4) only. It does **not** revert the §C2/§C3 ordering repairs: those are unconditional improvements that must not fork into two maintained orderings (clean-break doctrine — no dual paths). An executor must not add a second code path to "restore" the old order when caching is off.

**Default-ON justification** (an executor will ask): caching is semantically transparent — the model sees the same tokens either way — and every provider path degrades to a byte-identical no-op. It is a cost optimisation with no behavioral surface, so opt-out is the honest default. The switch exists for diagnosis (ruling caching out when debugging a provider), not because caching is risky.

### Integration points

- **Calls:** `ProviderCapability` (`llm/capabilities.py:47`), `pricing.estimate_cost` (`pricing.py:63`, unchanged — already accepts both cache args), `AppConfig.load()`.
- **Called by:** `agents/native/runtime.py` (ONE new middleware call immediately before `complete()` at `:747`, plus the volatile tagging in §C2); the two core adapters read the capability.
- **Storage owned:** none in S1/S2 (class A). The cache-hit aggregate persists through whatever store COST-AND-TOKEN-OBSERVABILITY defines — **this plan must not invent a second stats file**; `stats.py:42-84` already has the counters.
- **Deliberately NOT touched:** compaction internals (CONTEXT-ECONOMY), tool retrieval/`_prepare_turn_tools` (CONTEXT-ENGINEERING-PRINCIPLES), ACP runtimes (an external CLI owns its own request shape — out of scope by construction), `stream()`'s stateful history path (only the stateless `complete()` path is in scope; `stream()` is the legacy simple-prompt adapter).

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

### Session 1 — Wire-order repair + prefix stability (no marker; pays off on its own)

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | §C2 wire order: tag the loop's per-turn `system` message volatile (`runtime.py:712` — a single message carrying note+catalog+stubs); `_translate_messages` keeps untagged `system` in `system=` and delivers volatile notes at the END of the message list. Test asserts (a) the stable assembled context leads the served prompt, (b) every note that shipped before still ships exactly once, (c) an untagged-only message list produces **today's exact** request kwargs | `src/personalclaw/llm/anthropic.py`, `src/personalclaw/agents/native/runtime.py`, tests | served order is stable-then-volatile; content-equivalence test green; no-tag path byte-identical |
| T1.2 | §C3 date relocation **incl. the truncation fix**: move `[CURRENT DATE]` to the end of the assembled block in BOTH branches, appended AFTER the `_MAX_CONTEXT_CHARS` truncation; update `tests/test_context.py:698-707` and the `test_context_engine.py:45` fixture; comment states both reasons (cache stability + recency) | `src/personalclaw/context.py`, affected tests | date text unchanged and present in both branches; **an oversized context still ends with the date line** (explicit test); assembling twice ~1 min apart is byte-identical up to the final date line |
| V1 | Validation as a user: on an isolated dev home with a real Anthropic-family model, run a 4-turn conversation including one turn that triggers the deferred-tool catalog; confirm from the gateway log that the served prefix is stable across turns, and confirm the model still calls `tool_schema` for a catalog tool (the §C2 recency check). `make lint` + targeted pytest + `make test` + web typecheck/test/build | — | holds; no tool-comprehension regression |

### Session 2 — The neutral marker + provider adoption

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `llm/prompt_cache.py`: `PromptCache`, `CACHE_HINT_KEY`, `mark_cacheable_prefix` (every C1 clause incl. shallow-copy-never-mutate); unit tests incl. **the byte-identical assertion** for `NONE`/`AUTOMATIC` (assert the returned list `is` the input and no dict gained a key) | `src/personalclaw/llm/prompt_cache.py`, `tests/test_prompt_cache.py` | `NONE`/`AUTOMATIC` return the input object unchanged; `EXPLICIT` hints exactly one non-`tool` message; caller dicts never mutated; vendor-string grep returns zero |
| T2.2 | `ProviderCapability.prompt_cache` (default `NONE`, docstring mirroring `structured_output`'s rationale); core OpenAI adapter declares `AUTOMATIC`; middleware wired into the native loop before `complete()` with a loop-owned `_cache_generation` bumped on compaction (`runtime.py:1292`) and agent-definition change, DEBUG-logged | `llm/capabilities.py`, `llm/openai.py`, `agents/native/runtime.py`, tests | default-off preserved for every undeclared provider; a `NONE` provider's message list is byte-identical to today; a compaction bumps the generation |
| T2.3 | Anthropic translation: hinted span → `cache_control: {"type": "ephemeral"}` on its last block, incl. the **block-shaped `system=`** required by §C4; declares `EXPLICIT`; test asserts the wire shape and that an unhinted list produces today's exact request kwargs | `llm/anthropic.py`, tests | marker lands on exactly one block; unhinted requests byte-identical; block-shaped-system case covered |
| T2.4 | Config `prompt_cache_enabled` through all five §C6 points incl. the frontend control; middleware treats disabled as `NONE`; **assert the ordering repairs are NOT gated** by it | `config/loader.py`, `dashboard/handlers/core.py`, `web/src/pages/settings/`, tests | `test_config_roundtrip.py` green; PATCH round-trips; toggling off yields the byte-identical marker path with ordering intact |
| T2.5 | Rails test: `cache_control` / `"ephemeral"` appear ONLY in `llm/anthropic.py` — a sweep over `src/` fails on any other occurrence, mirroring the existing provider-boundary residue sweep. Prove it fails by temporarily introducing a violation | `tests/` (beside the existing rails sweep) | sweep fails on an injected violation |

### Session 3 — The measurement surface (**the producer — F3; do T3.1 first if merged with S2**)

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | **The missing producer.** Read `usage.cache_creation_input_tokens` / `usage.cache_read_input_tokens` off the Anthropic response in BOTH accumulation sites (`anthropic.py:302-309` and `:457-464`) and pass them into the terminal `LLMEvent` (the terminal event constructed at `:386` and `:533`). Defensive `getattr` like the existing token reads, so an SDK without the fields yields 0 | `llm/anthropic.py`, tests | a mocked response carrying cache usage produces a non-zero `cache_read_tokens` on the event; a response without the fields yields 0 and does not raise |
| T3.2 | Aggregate per turn and expose `cache_read_tokens`/`cache_creation_tokens`/`cache_hit_pct` + saved-USD (via existing `estimate_cost` rates) on the turn-complete telemetry COST-AND-TOKEN-OBSERVABILITY consumes, reusing `stats.py`'s counters — **do NOT create a second store**; never estimate when the provider reported nothing | `agents/native/runtime.py`, `stats.py`, tests | a real multi-turn run reports a rising hit-rate and non-zero saved-USD; an unpriced model reports tokens with 0.0 saved (honest-zero test) |
| V2 | Validation as a user: with a real Anthropic-family model AND a real OpenAI-family model bound, run the same 5-turn conversation on each; record hit-rate + saved-USD for both; confirm turn 1 reports `cache_creation_tokens` and turns 2+ report non-zero `cache_read_tokens`; confirm the OpenAI path reports vendor-side cache reads with **no marker sent**; confirm a local/Ollama model (undeclared ⇒ `NONE`) runs byte-identically with zeros; toggle the config off and confirm the marker stops while the ordering holds; full local gate | — | holds. **A near-zero hit rate is a DISCOVERY (log it), not a silent pass** |

## Owner tasks (real world)
1. **Coordinated apps-repo change (S2 follow-up, owner-sequenced):** declare the real posture on the branded model apps — `bedrock-models` (`EXPLICIT` for Anthropic-family on Converse, which uses `cachePoint` blocks rather than `cache_control` — the app owns that translation, core never learns it), `openai-compatible`/`openrouter-models` (`AUTOMATIC` where the upstream caches), `ollama-models`/`vllm-models` (`EXPLICIT` only if prefix caching is actually enabled server-side; otherwise leave `NONE` — an undeclared provider must never be assumed).
2. **Confirm the §C2 volatile-note relocation is acceptable behaviorally.** It is the one change in this plan that alters what position the model sees an instruction in. V1 checks tool-catalog comprehension; the owner should sanity-check a real session before S2 builds on it.
3. **Decide whether to widen the marker beyond one breakpoint** after seeing real hit-rates. Anthropic permits 4; a second breakpoint after the tool block only pays once CONTEXT-ENGINEERING-PRINCIPLES stabilises tool schemas. Deliberately deferred, not forgotten.
4. **Confirm the default-ON call** (§C6). The plan argues for it on transparency grounds; reversible with one field default.

## Risks & open questions
- **Silent no-win — the risk that already materialised once.** Rev 1 would have shipped a marker that could never hit (F1) with every test green. Mitigation is now structural: S1 fixes the wire order *before* any marker exists, and V2 requires *provider-reported* cache reads before a session may be marked complete. An unmeasured claim is not a done task. If hit-rates are near-zero after S1–S3, that is a DISCOVERY pointing at CONTEXT-ENGINEERING-PRINCIPLES' tool churn, not licence to widen this plan.
- **§C2 has a real behavioral surface.** Moving per-turn notes late changes where the model sees them. Recency usually helps, but "usually" is not "verified" — hence the explicit V1 comprehension check and owner task 2.
- **Cache-write cost exceeding the read saving on short sessions.** Writes are priced above base input (`cache_write 3.75` vs `in 3.0` for sonnet-4.6). A one-turn session pays ~25% more on the cached span. Accepted: the marker rides only the large stable head, and multi-turn agentic work (the dominant shape here, per the ~100:1 ratio) amortises it within two turns. The saved-USD readout must show this honestly, **including when it is negative** — do not hide a negative.
- **Provider drift.** Vendor cache syntax and TTLs change (Anthropic's `ephemeral` TTL is 5 minutes by default; Bedrock uses a different block shape entirely). Containment is the point of §C4: exactly one file per vendor to update.
- **Open:** whether `stream()`'s stateful path should also mark (deferred — it is the legacy simple-prompt adapter; the native loop uses `complete()`).
- **Open:** ACP-backed runtimes cannot participate (the external CLI owns its request shape). Recorded as a known parity gap rather than papered over; ACP-AGENT-PARITY may revisit.

## Execution log

**2026-07-30 — REDESIGN (rev 2), docs-only. No code changed.**

Owner ruling 2026-07-29: "Redesign the plan now and implement when it should be implemented in natural order of roadmap items wherever it fits." A pre-implementation audit of the wire path (not just the call sites) falsified rev 1's central mechanism and found a bug rev 1 would have introduced. Four findings, all reproduced from code and recorded in the new "Why this plan was redesigned" section:

- **F1 (fatal to rev 1):** `_translate_messages` (`anthropic.py:129-132, 183`) hoists ALL `system` messages into the top-level `system=` param, which Anthropic serves ahead of `messages[0]`. The native loop's only `system` messages are the single per-turn `turn_note` (`runtime.py:712`), which carries the tool catalog inside it (`runtime.py:930`). Reproduced: a volatile string leads the served prompt. Rev 1's breakpoint at the end of `messages[0]` would therefore have had a **structurally zero hit rate with every test passing**. New §C2 fixes the wire order and is now S1's first task.
- **F2 (a bug rev 1 would have shipped):** rev 1 moved `[CURRENT DATE]` to the tail, but `build_session_context` truncates from the tail (`context.py:992-1002`, `_MAX_CONTEXT_CHARS = 165_000`). Heavy-context users would have silently lost the date line — a correctness regression traded for a cache hit. §C3 keeps the relocation but appends the date **after** truncation, with a required oversized-context test.
- **F3 (false premise):** rev 1 claimed cache accounting was "ALREADY plumbed end-to-end." The consumers exist (`stats.py`, `pricing.py`, `chat_runner.py`, `guardrails/model_call.py`, `acp/adapter.py`); **no producer does** — neither adapter reads a cache field off a response, so `cache_read_tokens` is always 0 and the plan was unfalsifiable. Reading those two fields is new work, now T3.1.
- **F4 (false premise):** `structured_output` is a valid precedent for the *shape* of a graded default-off capability, but its apps-side half shipped as a documented DEVIATION (a deliberate cross-repo seam), not for free. Noted explicitly, with a standing warning **not** to delete `structured_output` as dead code — it is not.

Restructured S1/S2 into three sessions so the ordering repairs (valuable alone, and the precondition for the marker) land before the marker, and the producer lands before the proof. Sharpened §C6 to state that the config switch gates the marker only and must not fork the ordering into a dual path. No task was dropped; T1.1 and T3.1 are new, and every rev-1 task survives with its clauses intact.

Status stays DESIGNED — implementation deferred to its natural roadmap position per the owner ruling.

- **PCS-2 DONE — [CURRENT DATE] moved after truncation (§C3).** `context.py::build_session_context`
  no longer appends the date line into `parts` (which is joined then hard-truncated at
  `_MAX_CONTEXT_CHARS`, cutting from the END — so a mid-block date was the first thing an oversized
  context silently lost, regressing "what day is it" for the heaviest users). The line is now
  rendered once at assembly time (timestamp captured there) and appended to `context` AFTER the
  truncation step, for the single assembly tail both the custom and personalclaw paths take. Date
  text byte-identical; date now always the final block, exactly once. **Scope:** PCS-2 only (the
  ordering repair) — independent of PCS-1's volatile-tagging (different file: `context.py` vs
  `llm/anthropic.py`+`runtime.py`), so taken now without waiting on PCS-1; the cache MARKER + its
  measure-before-claiming gate remain PCS-1/T2.2/S3. No user surface → no CHANGELOG. **Gates:**
  `make lint` clean (697 files); 2 new `test_context.py` cases (date survives a shrunken-cap
  truncation as the tail block; present exactly once on a small context) + full `test_context.py`
  (52) + `test_context_engine.py`/`test_thread_context.py`/`test_project_context.py` (51) pass.

- **PCS-4 DONE — the Anthropic EXPLICIT translation (§C4, T2.3 + T2.5).** `llm/anthropic.py` is now
  the one core module that names `cache_control`. `_translate_messages` reads the NEUTRAL
  `CACHE_HINT_KEY` PCS-3 places and attaches `{"type": "ephemeral"}` to the **last content block** of
  the hinted message: a hinted `system` message forces `system=` from a bare `str` to a
  BLOCK-SHAPED one-element text list (§C4's asymmetry, owned entirely by the adapter); a hinted
  plain `user`/`assistant` message becomes one marked `text` block; a hinted
  assistant-with-`tool_calls` / `tool` message keeps its block list and only its trailing block is
  marked. `AnthropicProvider.prompt_cache = PromptCache.EXPLICIT` (the attr `runtime.py:775` reads by
  `getattr`, mirroring `supports_tools`), so the marker ships on real Anthropic turns as of this
  change. A hint on an absent/empty span, or on the PCS-1 volatile note, is a NO-OP — never a marker
  on nothing, never a breakpoint on per-turn content. The neutral hint key and its `generation` are
  consumed, never forwarded to the wire.
  **Guardrail 2 held and was falsified in both directions:** an unhinted list produces the pre-PCS-4
  kwargs exactly, `system` still a `str`, pinned against a hand-written literal; making the
  block-shaped `system=` UNCONDITIONAL turned 16 tests red (5 new + 11 of PCS-1's own byte-identity
  cases) before being reverted, and injecting a `cache_control` literal into a second core module
  turned the new rails sweep red (both offender and vacuity assertions) before being reverted.
  **Scope:** PCS-4 only. `prompt_cache_enabled` stays PCS-5's (no config field added here, so the
  marker is currently unconditional for Anthropic); the per-turn saved-USD/hit-rate readout stays
  PCS-7's. **CHANGELOG:** yes, under Changed — a user on an Anthropic model gets a cheaper, faster
  second turn onward starting with this change, so it is perceivable without PCS-5/PCS-7.
  **Gates:** `make lint` clean (black/isort/flake8 + mypy 811 files); new
  `tests/test_prompt_cache_wire_translation.py` 19/19 **serially (`-n 0`)** as well as under xdist;
  the touched neighbours green together (`test_anthropic_wire_order.py`,
  `test_prompt_cache_marker.py`, `test_anthropic_cache_usage.py`, `test_model_provider_complete.py` —
  57 serially); FULL suite **19020 passed / 30 skipped / 12 xfailed / 0 failed** in 4m55s, with the
  +19 delta over this branch point's own collection (19041 → 19060) exactly accounting for the new
  file.

- **PCS-4 DISCOVERY — the rails sweep must match vendor LITERALS, not the word "ephemeral".** The
  `done_when` says `cache_control`/`ephemeral` may appear only in `llm/anthropic.py`, but core uses
  the bare word for ~20 unrelated things (ephemeral ports in `gateway.py`, `LearningGate.EPHEMERAL`,
  `skills/ephemeral.py`), so a word-level sweep would have been ~20 false positives on day one. The
  sweep therefore matches ACTIONABLE syntax — `cache_control`, `cachePoint` (Bedrock's shape, PCS-8's
  to translate in-app), and a `"type": "ephemeral"` literal — exactly mirroring the choice
  `test_provider_boundary_residue.py` already documents ("it deliberately does NOT flag vendor words
  in prose"). A vacuity assertion pins that the patterns still match the ONE file allowed to carry
  them, so a rename can never leave a rail that passes by matching nothing.

- **PCS-4 DISCOVERY — §C4's "the marker normally attaches to the last system block" is not what the
  native loop produces today.** §C4 reasons that after §C2 the stable span IS `system=`. In the
  as-built loop `self._messages` never holds a NON-volatile `system` message: `stream()` appends the
  assembled context as a `user` message (`runtime.py:722`) and the turn_note as the volatile `system`
  note (`:735`), and nothing else. `mark_cacheable_prefix` therefore selects the last
  `user`/`assistant` message, so the breakpoint lands on that message's trailing block and `system=`
  stays a `str` on the real path. That is the correct Anthropic incremental-caching shape and needs no
  change — but the block-shaped `system=` branch is currently reachable only via a caller that hoists
  stable system content, so it is implemented and tested per §C4 rather than exercised by the loop.
  It becomes the normal case the moment the agent's system prompt moves into `self._messages`; PCS-7's
  V2 measurement is what will show which block the provider actually cached.

- **PCS-4 DECISION (not a deviation) — block-shaped `system=` is ONE text block, not one per part.**
  It carries the same `"\n\n"`-joined string the `str` form returns, so block-shaping cannot change
  *what the model is told* (soul guardrail 3). The breakpoint position is unaffected: a hinted system
  message is by construction the LAST hoisted one (the neutral marker picks the last non-tool,
  non-volatile message, so anything after it is a tool result or a volatile note), and caching
  "everything up to and including the hinted span" is exactly one block here.

- **PCS-4 DISCOVERY — PCS-3's "OpenAI adapter declares AUTOMATIC" clause is UNMET on `main`.**
  `llm/openai.py` carries no `prompt_cache` declaration, so `OpenAIProvider` inherits
  `PromptCache.NONE` from `ModelProvider` (`base.py:55`). Behaviour is identical today — neither
  posture places a marker and OpenAI caches on its own — so this is a declaration/legibility gap, not
  a defect, and it is PCS-3's row rather than PCS-4's. Left untouched here so the miss stays visible;
  it is a one-line fix whenever PCS-3 is revisited (PCS-7's V2 asserts "OpenAI reports vendor reads
  with no marker sent", which will read the posture).

- **PCS-4 — no live cache hit observed; the evidence here is kwargs-level.** The marker was verified
  by capturing the exact request kwargs handed to the SDK (a fake `messages.stream` records them),
  not against a live provider response. PCS-6 already ships the reader, so a hit is observable the
  moment a real Anthropic turn runs — soul guardrail 4's measure-before-claiming gate stays PCS-7's
  V2 and is NOT claimed here.
- **PCS-5 DONE — the prompt-cache switch (T2.4 / §C6).** `agent.prompt_cache_enabled` (default
  `True`) through all five §2.1 wiring points: the `AgentConfig` field + `_meta`
  (`config/loader.py:427`), `load()`'s explicit mapping (`:3372`), `to_dict()` (already covered by
  `asdict(self.agent)` — asserted rather than assumed), the `_EDITABLE_CONFIG` bool entry
  (`dashboard/handlers/core.py:566`), and a **Prompt caching** switch in Settings → Models
  (`web/src/pages/settings/ModelsPanel.tsx`, new `PromptCacheSection`). Regenerated
  `config-baseline.json` in the same change.
  **Middleware:** `llm/prompt_cache.py::effective_cache_mode(declared, *, enabled)` folds the
  switch into the provider's declared mode; off resolves to `PromptCache.NONE`, the mode that
  ALREADY means "hand the list back untouched". The native loop therefore keeps ONE code path —
  `mark_cacheable_prefix` is still called either way — so disabling caching takes the exact route an
  undeclared provider takes, instead of a second bypass branch. The loop reads the switch per turn
  via a deferred `AppConfig` import (`runtime.py::_prompt_cache_enabled`), matching the ACP
  concurrency gate's pattern; an unreadable config reads as ENABLED, which is the field's default.
  **No dual path (the clause that matters):** two ratchet tests assert the §C2/§C3 ordering repairs
  hold with the switch OFF — the adapter side (`_translate_messages` keeps stable context in
  `system=` and the volatile note at the tail) and the loop side (the per-turn note is still tagged
  `_volatile`), plus §C3's trailing date line. They were already ungated in code; the tests are what
  stops a future session from gating them.
  **DISCOVERY — PCS-4 is NOT on `origin/main`.** The session brief stated PCS-4 had shipped as
  #1272 and left the marker unconditional. At this branch point `llm/anthropic.py` contains no
  `cache_control` translation, no `CACHE_HINT_KEY` reader and no `EXPLICIT` declaration; the PCS-4
  commit exists on an unmerged branch and `dag.json` still carries `PCS-5`'s sibling as `todo` with
  no `pr`. PCS-5 was completable anyway and needed no change: the switch gates the marker PRODUCER
  (PCS-3's middleware, which IS on main), so the mode is already collapsed before any adapter could
  see a hint. PCS-4 inherits the switch with zero further work when it merges.
  **Falsified twice:** (a) gating the `_volatile` tag on the switch turned
  `test_switch_off_keeps_the_volatile_tag_on_the_per_turn_note` RED — reverted; (b) dropping
  `load()`'s mapping turned `test_config_roundtrip.py::test_every_leaf_field_survives_save_load` RED
  with `agent.prompt_cache_enabled: saved False but loaded True` — restored. Note that
  `test_config_roundtrip.py` covers wiring points 1-3 only; points 4 (the PATCH allowlist) and 5
  (the control) have no pre-existing rail, so this atom's own tests are theirs.
  **Gates:** `make lint` clean (1582 files, mypy 811); full python suite **19018 passed, 30 skipped,
  12 xfailed** vs a measured pre-change baseline of **19001 passed, 30 skipped, 12 xfailed** on the
  same branch point (+17 new, no regressions), and the touched files re-run serially (`-n 0`);
  `npm run typecheck:web` clean; full `npx vitest run` **221 files / 2183 tests** (+4 new);
  `npm run build` clean; `make gates` all three PASS (config-baseline regenerated in this change).
  **Driven live** on an isolated dev home: `GET /api/config/personalclaw` reported the field `True`
  by default, `PATCH agent.prompt_cache_enabled=false` returned 200 and the value came back `False`
  from both the API and `config.json` on disk, flipping back returned 200 and `True`, and a non-bool
  value was refused with 400.

- **PCS-8 PARTIAL — branded-app cache posture + the Bedrock `cachePoint` translation
  (PersonalClawApps).** Two commits, one per repo. **Apps** (`feature-pcs8-cache-posture`): all
  **15** apps that register a model-provider type now declare a `prompt_cache` posture EXPLICITLY,
  each with the evidence for it inline — `bedrock-models` **EXPLICIT** (owns the wire; translates
  the marker itself), `anthropic-models` / `anthropic-compatible` **EXPLICIT** (their provider IS
  core's `AnthropicProvider`, already `EXPLICIT` at `llm/anthropic.py:359`, so any other value
  would make `ProviderCapability` contradict the instance the factory returns), `openai-models` /
  `openrouter-models` / `deepseek-models` / `google-models` **AUTOMATIC**, and
  `alibaba-models` / `groq-models` / `mistral-models` / `together-models` / `meta-muse-spark` /
  `openai-compatible` / `vllm-models` / `ollama-models` **NONE**. `bedrock-models` translates
  `CACHE_HINT_KEY` into Converse's `{"cachePoint": {"type": "default"}}` content block inside its
  own `_translate_messages` (system-block list for a hinted system message, last content block
  otherwise), and reads `cacheReadInputTokens`/`cacheWriteInputTokens` into
  `LLMEvent.cache_read_tokens`/`.cache_creation_tokens` — **core never learns `cachePoint`**, and
  core's own sweep at `tests/test_prompt_cache_wire_translation.py:421` (which already named
  `cachePoint` as "PCS-8's") stays green. **Core** (`feature-pcs8-cache-posture-core`): one export
  + one rail, nothing else.

- **PCS-8 DEVIATION — one core change was required and is not Execution-log-only.**
  `CACHE_HINT_KEY` was promoted to `personalclaw.sdk.model` (import + `__all__`). An app may reach
  core ONLY via `personalclaw.sdk.*`, and a provider that owns its own wire must READ the neutral
  marker to translate it, so without the promotion the atom is unbuildable except by hand-copying
  the string literal into the app. This is not an invention: core's own
  `tests/test_apps_import_boundary.py` docstring prescribes it — *"If a symbol isn't on the SDK yet,
  the fix is to PROMOTE it to a `personalclaw.sdk` submodule instead of reaching around the
  boundary."* `sdk/provider_helpers.py` was NOT touched (fenced to a sibling atom) and needed no
  change: `BrandedProviderSpec.prompt_cache` already threads to `ProviderCapability`.
  **Consequence — the two commits are ORDERED.** The apps commit does not import against core
  `origin/main`; the apps CI `tests` job installs core from `main`, so it stays red until the core
  commit lands. Land core first. Verified locally by running the apps suite against the core
  worktree's `src` (against `main`'s it fails with
  `ImportError: cannot import name 'CACHE_HINT_KEY' from 'personalclaw.sdk.model'`).

- **PCS-8 DEVIATION — three of the done_when's postures were measured to be wrong, so they are
  NONE.** The done_when says "openai-compatible/openrouter-models AUTOMATIC where upstream caches";
  the qualifier is load-bearing and `openai-compatible` cannot satisfy it. That app is the
  bring-your-own-endpoint shell (`default_base_url=""`, "user MUST supply the endpoint"), so
  whether the upstream caches is unknowable at declaration time → **NONE**. `groq-models` →
  **NONE**: Groq's caching is automatic and cannot be disabled, but Groq's own docs scope it to
  three `openai/gpt-oss-*` models (OpenRouter's matrix says Kimi K2), and the app pins
  `default_model=""` and resolves from live discovery, so a provider-wide AUTOMATIC would promise
  hits most selections never get. `alibaba-models` → **NONE**: Qwen DOES cache but only behind an
  explicit `cache_control` breakpoint (OpenRouter's provider matrix), and this app rides core's
  `OpenAIProvider`, which places no marker — declaring EXPLICIT would mark a message nothing
  translates. `mistral-models` / `together-models` → **NONE**, no prompt-caching documentation
  exists (`docs.mistral.ai/capabilities/prompt_caching/` and `docs.together.ai/docs/prompt-caching`
  both HTTP 404, checked 2026-08-18). The rule applied throughout: a posture is a claim about the
  upstream service, and an unbacked one is worse than NONE, because NONE is honest while a wrong
  AUTOMATIC silently promises reads that never arrive.

- **PCS-8 DISCOVERY — Bedrock's `system` hoisting made an EXPLICIT posture unreachable, so the
  §C2 volatile relocation had to be done a second time, app-side.** `runtime.py:621` appends the
  per-turn note as `{"role": "system", …, "_volatile": True}` for EVERY provider, and
  `bedrock-models` hoisted every `role: "system"` message into Converse's `system` block list.
  Converse serves `system` ahead of `messages[0]`, so the cacheable prefix would have differed on
  every turn and **no checkpoint could ever have been read** — the declaration would have been
  true on the wire and false in effect. The app now relocates the note to the TAIL as a trailing
  user turn, exactly as `llm/anthropic.py` does for the same reason: the note moves POSITION, never
  existence. This widens the atom by one step beyond its done_when, and is the difference between a
  posture that works and one that lies. §C2 is therefore a per-wire repair, not a one-time core
  fix — any future app owning its own wire inherits the same obligation.

- **PCS-8 DISCOVERY — PCS-3's unmet "OpenAI adapter declares AUTOMATIC" clause is still unmet, and
  `BrandedProviderSpec.prompt_cache` never reaches the provider INSTANCE.** `_build_provider`
  (`sdk/provider_helpers.py:155-179`) constructs `AnthropicProvider`/`OpenAIProvider` without
  passing the spec's posture, so the runtime value is always the protocol client's class attr:
  `EXPLICIT` for the Anthropic protocol, `NONE` for the OpenAI protocol. Harmless for AUTOMATIC
  (AUTOMATIC and NONE are wire-identical by design — `mark_cacheable_prefix` returns the list
  untouched for both), which is why `openai-models` still declares the truthful vendor behaviour.
  Left untouched: the fix is one line in `llm/openai.py` and belongs to PCS-3's row, and
  `provider_helpers.py` was fenced to a sibling atom this session.

- **PCS-8 UNMET — the live-Bedrock validation clause. Not run, not faked.** "validated by driving
  a real Bedrock-Anthropic multi-turn run that reports cache reads" needs usable AWS Bedrock
  credentials. `aws sts get-caller-identity` fails with `InvalidClientTokenId` on the default
  chain, and every one of the 13 named profiles in `~/.aws/config` is an Amazon-internal account
  (`ias-*-prod`, `marq-na-prod`, `issuance-prod-ro`, …). Under the standing production-safety rules
  — treat any environment you cannot positively identify as production, prefer least privilege —
  billing model invocations to a production account for a personal-project validation is not a
  read-only act on that account's budget, so no run was attempted. **What is missing, precisely:**
  a non-production AWS account with `bedrock:InvokeModelWithResponseStream` on an Anthropic model,
  configured as a Bedrock provider under an isolated `PERSONALCLAW_HOME`; then two turns of the
  same conversation, asserting the second reports a non-zero cache-read count.
  **Substitute evidence (payload-level, not a live hit):**
  `test_complete_sends_the_cache_point_on_the_wire_and_reports_cache_reads` drives `complete()`
  over a stubbed `boto3` with a marked multi-turn history and asserts the request Converse would
  have received carries EXACTLY ONE `cachePoint`, at the end of the hinted message, with the
  volatile note after it and the system head unchanged — and that a returned
  `cacheReadInputTokens: 4096` surfaces as `LLMEvent.cache_read_tokens == 4096`. That proves the
  translation and the reporting path; it does not prove Bedrock honours the checkpoint.

- **PCS-8 — the posture rail lives in apps CI, not in a bundle's `test_*.py`.** A cross-app sweep
  cannot live in one app's tests: the apps CI `tests` job runs `pytest` PER bundle, so a
  root-level test file would never execute (it would read as a pass forever). The rail is
  `.github/scripts/check_prompt_cache_posture.py` + a `prompt-cache-posture` job, alongside the two
  cross-app sweeps that already work that way (`manifest-validate`, `boundary`). It enforces four
  rules — a posture is declared; an evidence comment sits above it; EXPLICIT must actually read
  `CACHE_HINT_KEY` or ride core's Anthropic client; and vendor cache syntax on the wire must be
  declared EXPLICIT — over a **vacuity floor** (>= 14 model apps discovered and declared, >= 2
  EXPLICIT, >= 2 AUTOMATIC, >= 4 NONE), so it cannot pass by inspecting nothing.

- **PCS-8 falsified — eight mutations, each red, each restored from a file copy.** Translation:
  (a) dropping the `hinted` guard so every message gets a checkpoint →
  `assert _blocks_with_cache_point(request) == 1` / `assert 3 == 1`, 5 tests red including the
  PRE-EXISTING `test_translate_messages_plain_string_unchanged`; (b) removing the append entirely →
  `assert out[0]["content"] == [{"text": "first"}, _CACHE_POINT]` red and `assert 0 == 1` on the
  wire test; (c) hoisting the volatile note back into `system` →
  `AssertionError: the volatile note must NOT be in system`. Rail: (d) `bedrock-models` flipped to
  AUTOMATIC while still emitting `cachePoint` → "emits vendor cache syntax on the wire but declares
  ['AUTOMATIC']"; (e) `openai-compatible` flipped to EXPLICIT → "declares PromptCache.EXPLICIT but
  neither reads CACHE_HINT_KEY nor rides core's Anthropic protocol client"; (f) stripping the
  evidence comment above `deepseek-models`' declaration → "has no evidence comment above it";
  (g) deleting `groq-models`' declaration → "declares no prompt_cache posture"; (h) running the
  rail against an empty directory → five VACUITY failures, exit 1. Core: dropping
  `"CACHE_HINT_KEY"` from `sdk/model.py`'s `__all__` →
  `test_the_marker_key_is_on_the_app_facing_sdk_facade` red.

- **2026-08-22 — `PCS-7`: the numbers ship AND soul guardrail 4 is measured. Atom stays `todo` only
  because this code is unmerged** (the tick rule is never to flip an atom whose implementation is not
  yet on `main`); flip it when this PR lands.
  The substrate could price a cached turn correctly and could not *show* that it had saved anything.
  `_turn_complete_line` summed the two counters into one `13,600 cached` figure — the one shape that
  answers neither question a reader has (how much of this prompt came from cache, and what did the
  cache save). Shipped: `pricing.cache_savings_usd`, `stats.cache_hit_pct`, and the line carrying
  `cache 84% hit (12,400 read / 1,200 written) · saved $0.0231`.
  **Savings is counterfactual-minus-actual, both through `estimate_cost` itself**, not hand-derived
  rate deltas — one rate table, one arithmetic path, so the two cannot drift. All 26 real rows in
  `model_pricing.json` carry both `cache_read` and `cache_write`, so nothing needed a new rate.
  **The denominator was settled from code, not assumed.** `input_tokens` EXCLUDES the cached tokens,
  so the hit rate divides by `input + cache_read + cache_creation`. The decisive evidence is this
  repo's own billing model: `pricing.py:76-83` bills all three additively, so if `input_tokens`
  already contained them every cached turn would be double-billed. Corroborated at
  `llm/anthropic.py:524-526` (and its twin at `:689-691`), which assigns `input_tokens` verbatim
  while the cache counts arrive from separate SDK fields via `_read_cache_usage` (`:84-98`) with no
  arithmetic relating the three.
  **Honesty rules, each with its own test.** `cache_hit_pct` returns `None` — not `0.0` — with no
  denominator, because no prompt tokens is no measurement; a measured `0.0` still prints `0% hit`.
  `cache_savings_usd` returns `None` for a model with no price row, so an unpriced turn reads
  `saved unpriced` and can never be mistaken for one that saved nothing. A first turn only WRITES the
  cache, so its net saving is NEGATIVE and renders with its sign — there is no `abs()` or
  `max(0, ...)` anywhere in the path, and a falsification proves it (`abs()` reds 2 of 15).
  **Two integration defects found while wiring, both fixed here.** `_record_model` is assigned only
  inside the `EVENT_COMPLETE` branch while the broadcast guard fires on the event count alone, so
  pricing against it would have raised `NameError` on a turn that never reported usage; the model now
  rides a `_turn_model` local beside `_turn_priced`. And the cache fragment was de-nested from inside
  `if input_tokens or output_tokens:` — nested, it silently dropped measured cache data whenever in
  and out were both zero.
  **Rails.** The call site is asserted by AST to pass the two counts as separate keywords and no
  summed `cache_tokens=`, with a vacuity assertion that the walk found the call at all (a rail that
  matches nothing looks clean). The two shipped callers of the old signature were corrected to the new
  contract, not loosened. Falsifications reproduced independently by the integrating session: re-summing
  at the call site reds 1 of 15; dropping `cache_creation_tokens` from the denominator reds 2 of 8
  (`75.0` vs `30.0`, and the write-only turn flips to `None`).
  **Soul guardrail 4 is SATISFIED — a real provider-reported cache read, with the rising hit rate.**
  Two Bedrock Converse turns on `us.anthropic.claude-sonnet-4-5-20250929-v1:0`, the stable head carrying
  core's neutral marker and the volatile question in its own message:

      TURN 1  input=20  cache_read=0       cache_creation=14,448
              · cache 0% hit (0 read / 14,448 written) · saved unpriced
      TURN 2  input=20  cache_read=14,448  cache_creation=0
              · cache 100% hit (14,448 read / 0 written) · saved unpriced

  Turn 1 writes, turn 2 reads, and the fragment reports the rise from real numbers — which is exactly
  the V2 clause. Two things this cost, both worth recording:
  **(1) A RETRACTION.** An earlier revision of this entry claimed "the installed `bedrock-models` app
  contains zero occurrences of `cachePoint`/`cacheRead`/`cacheWrite`, so Bedrock is never asked to cache
  — that translation is `PCS-8`'s unbuilt deliverable." **That inference was wrong.** The translation
  has been on apps `main` all along — in `bedrock-models/provider.py`, which is in the APPS repo, not
  this one: `_CACHE_POINT_BLOCK`, the `cacheReadInputTokens`/`cacheWriteInputTokens` reads, and
  `prompt_cache = EXPLICIT`. (Named by symbol rather than `file.py:NNN` because the docs-lint citation
  rule resolves paths against THIS repo, so a cross-repo line citation reads as a dead one; the same
  reason `CI-RELEASE-ENGINEERING.md` cites `anthropic-models/test_provider.py` without a line.) The
  zero readings
  were real but came from **two different stale copies**: first the dev home's INSTALLED app (the
  gateway runs installed copies, not the repo), then the apps clone's checked-out branch, which is 114
  lines behind `origin/main` for that file and has no `prompt_cache` at all. Loading the provider from a
  worktree pinned at `origin/main` produced the cache write on the first attempt.
  **(2) The marker's PLACEMENT is load-bearing, and a plausible-looking driver hides it.** With the
  question crammed into the same message as the prefix, both turns reported
  `cache_creation=14,458, cache_read=0` — a WRITE every turn and never a read, because the cachePoint
  lands after content that changes. Splitting the stable head into its own marked message (the shape
  core's middleware actually produces) is what turned the second turn into a read. A validation that had
  stopped at the first shape would have concluded the cache does not work.
  **DISCOVERY (pre-existing, outside this atom, not swept in): no real Bedrock model id is priced at
  all.** The table's key is `claude-haiku-4.5` (dotted family form) while live ids are dashed and
  vendor-prefixed, and `_rates` matches with `model.startswith(key)` — so
  `has_pricing("global.anthropic.claude-haiku-4-5-20251001-v1:0")`, `has_pricing("us.anthropic.claude-sonnet-4-5-20250929-v1:0")`
  and even the bare `has_pricing("claude-haiku-4-5-20251001")` are all **False**. Consequence: on the
  only provider reachable in dev, CATO's cost figure and this plan's saved-USD both read `unpriced`.
  That is the honest-zero design behaving correctly, not a new bug, but the id-normalisation belongs to
  the pricing/CATO surface — fixing it would move cost numbers repo-wide, far outside this atom.
  **DISCOVERY (pre-existing, outside this atom): `context_usage_pct` under-reports on a cached prompt.**
  `llm/anthropic.py:601-603` and `:761-763` compute `input_tokens / ctx * 100`, and since
  `input_tokens` excludes cache reads, a 200k-token prompt served 95% from cache reports ~5%. This got
  worse the moment `PCS-6` began populating the cache fields. It belongs to whoever owns the
  context-% surface (see `G8`'s honest-unmeasured work).
  **Also left alone, recorded rather than widened:** the broadcast guard still keys on
  events/tools/in/out only, so a turn with cache activity and none of those emits no line at all.

- **2026-08-22 — `PCS-8` DONE. The last clause was a live run, and it passes.**
  The declarative half has been on apps `main` since `3c44598`: all **15** first-party model apps state a
  posture (`bedrock-models`/`anthropic-models`/`anthropic-compatible`/`claude-subscription` EXPLICIT;
  `openai-models`/`openrouter-models`/`google-models`/`deepseek-models` AUTOMATIC; the remaining seven,
  including `ollama-models`, `vllm-models` and `openai-compatible`, NONE), each next to an evidence
  comment enforced by `.github/scripts/check_prompt_cache_posture.py`'s four rules — R2 requires the
  evidence to travel with the claim, R3 refuses an EXPLICIT that emits nothing ("marker into the void"),
  R4 refuses vendor cache syntax on the wire without an EXPLICIT declaration. Core's half
  (`CACHE_HINT_KEY` on the `sdk.model` facade) is on core `main`.
  **The open clause was "validated by driving a real Bedrock-Anthropic multi-turn run that reports cache
  reads", and that is now measured** on `us.anthropic.claude-sonnet-4-5-20250929-v1:0`: turn 1
  `cache_creation=14,448 / cache_read=0`, turn 2 `cache_read=14,448 / cache_creation=0`. Bedrock reported
  the read, so the app's Converse `cachePoint` translation works end to end against the live service and
  core never learned `cachePoint`.
  **The clause it does NOT cover, stated so nobody reads more into this than it proves.** `openai-models`,
  `openrouter-models`, `google-models` and `deepseek-models` declare AUTOMATIC on documentary evidence
  only; no live run confirms an upstream cache read for any of them, and this environment has no
  credentials for any. Their postures rest on R1-R4 plus vendor documentation, which is what the atom
  asked for — but an AUTOMATIC that silently never caches would look identical from here.
  **`openai-compatible` is NONE, not AUTOMATIC**, which the atom's wording ("openai-compatible/
  openrouter-models AUTOMATIC where upstream caches") could be read as contradicting. It is the honest
  reading of the conditional: the app points at an arbitrary OpenAI-shaped endpoint, so what the upstream
  does is unknowable at declaration time and `NONE` is the only claim that cannot be false. Recorded as a
  DEVIATION from the literal wording, not from the intent.
  **Bedrock saved-USD is structurally invisible, and that is a pricing-table gap, not this atom's.** Both
  live turns rendered `saved unpriced`, because no Bedrock inference-profile id resolves to a price row —
  see the `PCS-7` entry above for the measurement (`_rates` uses `model.startswith(key)` against dotted
  family keys like `claude-sonnet-4.5`). The cache is working; the money it saves cannot be shown on this
  provider until ids are normalised.

- **2026-08-24 — `PCS-7` AUDIT (close-or-record): the T3.2 half is DONE AND ON `main`; the V2 half is
  PARTIALLY met and its remainder is environment-gated. The atom should be SPLIT, not flipped whole.**
  Verified by CONTENT against `origin/main` (`9e0f727b`), never by PR state — PR #1918 reads
  `CLOSED, mergedAt=null`, which in this repo means nothing either way because the merge train
  cherry-picks per commit.
  **T3.2 — MET, every clause, with its rails.** `pricing.py:93` `cache_savings_usd` and
  `stats.py:146` `cache_hit_pct` are both on `main`; the renderer carries
  `cache_read_tokens`/`cache_creation_tokens`/`cache_hit_pct`/`cache_saved_usd`
  (`dashboard/chat_runner.py:500-503`), the fragment composes at `:537-550`, and the call site passes
  all four at `:4086-4101`.
  **"No second store" HOLDS STRUCTURALLY, not just numerically.** A repo-wide census of
  `cache_read_tokens` writers finds exactly ONE tally: `Stats._c` (`stats.py:43-44`), incremented at
  `dashboard/chat_runner.py:3805-3806`. PCS-7 added no dict, class or file — only two PURE functions and
  two per-turn LOCALS (`:3867-3868`), assigned (not accumulated) from the same terminal `EVENT_COMPLETE`
  that feeds `stats.inc_*`. `test_cache_hit_pct_is_module_level_and_stateless` asserts
  `not hasattr(Stats, "cache_hit_pct")`, so the helper cannot silently acquire a turn-scoped store.
  `guardrails/model_call.py:439` and `_record_turn_usage` (`:3846-3847`) read the event directly and are
  pre-existing consumers, not new stores.
  **Nuance worth recording:** the clause's literal "reusing `stats.py:42-84` counters" is satisfied in the
  no-second-store sense and NOT in a "reads `Stats._c`" sense — and it must not be. Those counters are
  process-lifetime cumulative, so a per-turn ratio derived from them would be wrong arithmetic. The helper
  lives beside them in `stats.py` and its docstring states exactly that reasoning.
  **The two negatives, each with a vacuity floor.** Honest-zero:
  `test_unpriced_model_is_none_and_not_a_zero`, floored by
  `test_unpriced_none_is_a_real_distinction_not_existing_behaviour`, which proves `estimate_cost` returns
  a bare `0.0` for the same model — so the `None` is a NEW distinction, not a restatement of existing
  behaviour. Negative-not-hidden: `test_first_turn_that_only_writes_the_cache_is_negative`, floored by a
  premise assertion that `row["cache_write"] > row["in"]` in the SHIPPED `model_pricing.json` — without
  it the test could pass on a table where writes were free. The call-site rail is floored by
  `test_the_walk_actually_found_the_call` plus a `**`-splat guard.
  **THE PRODUCTION READER, NAMED.** `web/src/pages/ChatPage.tsx:3595` folds `activityKind === 'stats'`
  into `ledger.stats`, `:3679` passes it to `ContextLedger`, and `:3790` renders it verbatim in a
  `Telemetry` `LedgerRow`. The numbers do reach a user. **Two caveats, recorded rather than widened:**
  (1) that row sits behind a disclosure defaulting to CLOSED (`:3754`), so the collapsed summary shows
  only the word `telemetry`; (2) **no frontend test asserts the path at all** — `activityKind` appears in
  exactly two FE tests, both for other kinds, so `ak === 'stats'` could be deleted with every gate green.
  Both belong to the readout owner (`EXT:COST-AND-TOKEN-OBSERVABILITY`, which this atom's own dep row
  says "owns/renders" it), so neither was fixed inside PCS-7.
  **V2 — PARTIALLY met; the remainder needs real credentials and real spend.**
  (a) Anthropic-family live multi-turn, turn-1 creation → turn-2 read, rising hit rate: **MET** (the
  Bedrock run in the `PCS-7` entry above). Two turns rather than the row's five, which exercises the
  assertion (creation→read) if not the literal shape.
  (b) **non-zero saved-USD on a REAL run: NOT met, and structurally unreachable here.** Both live turns
  rendered `saved unpriced` because no Bedrock inference-profile id resolves to a price row. That is the
  honest-zero design behaving correctly; the fix (id normalisation in `_rates`) is a pricing/CATO change
  that moves cost figures repo-wide.
  (c) **OpenAI-family live run reporting vendor cache reads with no marker sent: NOT met — ENV-GATED.**
  No `OPENAI_API_KEY` in the environment, credentials live in the OS keychain (`cli_doctor.py:131`), and
  the real home's `.env` carries only `PERSONALCLAW_OWNER_ID`. It is ALSO coupled to another atom's known
  unmet clause: `llm/openai.py` still declares no posture, so `OpenAIProvider` inherits
  `PromptCache.NONE` (PCS-3's row, recorded twice above). Not chased — paying real OpenAI spend for a
  personal-project validation is an owner call, not an agent's.
  (d) undeclared/Ollama byte-identical zeros: **met at kwargs level, not by a live run** —
  `test_unhinted_request_kwargs_are_byte_identical_to_today`,
  `test_a_none_provider_posture_still_yields_the_same_kwargs`,
  `test_runtime_hands_same_object_to_complete_when_undeclared`.
  (e) config off stops the marker while ordering holds: **met at unit level** —
  `test_disabled_collapses_every_declared_mode_to_none`,
  `test_runtime_explicit_with_switch_off_hands_back_the_same_object`, and crucially
  `test_ordering_repairs_are_not_gated_by_the_switch`.
  **RECOMMENDATION: SPLIT the atom.** The numbers half has no remaining work and no remaining risk; the
  live-OpenAI half cannot be closed from this environment at all. Keeping them in one row means a
  fully-built, fully-railed deliverable reads `todo` indefinitely because of a credential the repo does
  not have.
  **The three prior branches carry ZERO content that is not on `main`.**
  `feature-pcs7-cache-savings-primitive`, `-turn-cache-telemetry` and `-cache-proof-surface` (a stack off
  base `12469c65`): each one's `src/` + `tests/` patch REVERSE-applies cleanly to `origin/main` AND
  forward-applies with a failure. That pair is the non-vacuity floor — an empty patch passes the reverse
  check alone, and a forward apply that succeeded would mean the lines were absent. Their
  `docs/roadmap/atomic/` hunks also reverse-apply, but only because that hunk was the `PCS-8` flip
  (already on `main`), not a `PCS-7` one; `PCS-7` is still `"status": "todo"` in `dag.json`.
  **CORRECTION to a plausible-looking probe.** A `git grep 'cache_hit_rate\|saved_usd'` over
  `src/personalclaw/` looks like it confirms this atom, but its hits are noise:
  `workflows/introspection.py:113` `cache_hit_rate` is the WORKFLOW-introspection cache and has nothing
  to do with the prompt cache, and the `chat_runner.py` hits match `cache_saved_usd` by substring. This
  atom's real symbols are `cache_savings_usd`, `cache_hit_pct` and the `cache_saved_usd` keyword.
  **Falsified — three mutations, each red, each restored from a file COPY (never `git checkout`).**
  (i) `max(0.0, round(counterfactual - actual, 6))` in `pricing.py` →
  `test_first_turn_that_only_writes_the_cache_is_negative` red (`assert 0.0 < 0`), 1 of 6.
  (ii) unpriced returning `0.0` instead of `None` → 3 of 6 red, including the vacuity-paired test.
  (iii) re-summing at the call site (`cache_tokens=read + creation`) →
  `TestCallSiteRail::test_call_site_passes_the_split_counts_and_no_summed_keyword` red, 1 of 15.
  No production line was changed by this session — it is an audit, and the tree carries only this entry.

- **2026-08-25 — `PCS-7` CLOSURE PASS: the 2026-08-24 audit's one un-executable claim is now a
  rail, and running it found an accumulator the hand census had missed. Atom stays `todo` —
  PARTIAL, on the credential-gated V2 clause only.**
  The audit above settled the content question and this pass re-settled it independently, per file
  and per symbol, against `origin/main` `20488b9e`: `pricing.py`, `stats.py` and all three of the
  atom's test files are **byte-identical** to `main`, and `git diff origin/main..<branch> --
  dashboard/chat_runner.py` adds **zero lines mentioning `cache`** — its 745-line delta is `main`
  moving forward under other plans, not un-landed PCS-7 content. So the feature is fully on `main`
  and the row is mis-statused for the T3.2 half.
  **The two remaining unpushed local branches are STALE — nothing to land.**
  `feature-pcs7-cache-proof-surface` (`40a69cf9`) is a strict superset of
  `feature-pcs7-turn-cache-telemetry` (`a68b4f7f`): the superset adds `pricing.py`, `stats.py`,
  the two primitive test files and the docs hunks, and B's four files are A's four. Every one of
  A's `src/` and `tests/` files diffs to zero against `main`, including `PCS.md` — `main` already
  carries A's `PCS-8` flip. Verdict: both **already-on-main**, neither cherry-picked, no authorship
  to preserve because the content is already authored on `main`. Deleting them is safe; left alone
  they will keep reading as un-landed work to the next audit.
  **THE REAL GAP, and it was the one the audit named without being able to close: "no second
  store" was held by a HAND-RUN census recorded in this log.** The three shipped rails cannot see a
  second store appear. `test_stats_counters_still_carry_both_cache_keys` checks the counters are
  PRESENT — and its docstring's claim that it reds "if a refactor adds a parallel per-turn store" is
  **false**, since a new accumulator in another module leaves `snapshot()` and the `hasattr` checks
  untouched. `test_cache_hit_pct_is_module_level_and_stateless` pins only that the helper is not a
  `Stats` method. `TestCallSiteRail` pins that the shared helpers are CALLED — calling them and also
  keeping a private accumulator are not mutually exclusive. Anything a human counts once, a later
  change un-counts silently.
  **Built: `tests/test_cache_counters_single_store.py`** — the census, by AST rather than grep
  (a docstring naming `cache_read_tokens` is not a store), in four halves. (1) No module on the live
  turn path AugAssigns a prompt-cache-named target. (2) The tally's only writers are
  `Stats.inc_cache_read_tokens`/`inc_cache_creation_tokens`, called from exactly one module. (3) The
  two per-turn locals are `Assign`ed from the terminal complete event, never accumulated across
  events — accumulating them would both duplicate the tally AND desynchronise the hit-rate
  denominator, since `_turn_input_tokens` beside them is assigned. (4) The singleton carries no
  cache-named attribute outside its one `_c` dict. Half (1)'s expected answer is the EMPTY set —
  the shape a scanner that matches nothing also returns — so it carries a positive control it MUST
  flag and a negative control it must not; halves (2) and (3) assert their subjects were found
  before asserting anything about them.
  **DISCOVERY — the census found a second accumulator the hand census missed: `usage_ledger._fold`
  (`usage_ledger.py:199-200`).** It is **not** a second store: it is a query-time group-by reducing
  rows the ledger already persisted (from the same terminal event that feeds `Stats`) into a
  transient per-group dict, the way SQL `SUM()` would. Exempted explicitly, and the exemption is
  floored two ways rather than granted: `_blank_agg()` must return a DISTINCT object per call —
  proven behaviourally by folding into one group and asserting the other is untouched, so a shared
  dict reds — and the exemption reds if `_fold` ever stops accumulating, so it cannot outlive its
  reason. The audit's census was not wrong about the live path; it was scoped to writers of the
  counter and never asked which modules accumulate.
  **`done_when`, clause by clause.** Per-turn `cache_read_tokens`/`cache_creation_tokens`/
  `cache_hit_pct` + saved-USD on turn-complete telemetry: **MET** (on `main`, unchanged by this
  pass). "Reusing `stats.py:42-84` counters with no second store": **MET, and now EXECUTABLE** —
  this pass's only addition. Honest-zero for unpriced models: **MET** (`main`'s
  `test_unpriced_model_is_none_and_not_a_zero`, floored by
  `test_unpriced_none_is_a_real_distinction_not_existing_behaviour`). Negative saved-USD not hidden:
  **MET** (`test_first_turn_that_only_writes_the_cache_is_negative`, floored on the shipped price
  table). No rail was added over either negative — a second rail over the same invariant is dead
  code, and both already carry vacuity floors.
  **V2 — the ONLY reason this atom is not `done`, and it is CREDENTIAL-GATED, not unbuilt.**
  Anthropic-family live multi-turn (turn-1 creation → turn-2 read): **MET** (the Bedrock run above).
  Undeclared/Ollama byte-identical zeros and config-off-stops-the-marker: **met at unit level**.
  **NOT met: (a) non-zero saved-USD on a real run** — both live turns rendered `saved unpriced`
  because no Bedrock inference-profile id resolves to a price row, which is the honest-zero design
  behaving correctly and whose fix is a repo-wide pricing/CATO change; **(b) the OpenAI-family live
  run reporting vendor cache reads** — no `OPENAI_API_KEY` in this environment, and it is coupled to
  `PCS-3`'s still-unmet "OpenAI adapter declares AUTOMATIC" clause. Neither was simulated. Paying
  real OpenAI spend to close a personal-project validation clause is an owner call.
  **Falsified — four mutations, each red on exactly the predicted test, each restored from a file
  COPY (never `git checkout`).** (i) `_turn_cache_read_tokens` `=` → `+=` at
  `chat_runner.py:4003` → 2 of 7 red (the live-path census AND the turn-locals half, which is the
  pair that should both catch it). (ii) the named mutator routed through the generic one
  (`stats.inc("cache_read_tokens", ...)` at `:3942`) →
  `test_the_only_tally_writers_are_the_stats_mutators` red, 1 of 7. (iii) `self._cache_read_snapshot
  = 0` added to `Stats._init_counters` → `test_the_singleton_holds_no_cache_named_attribute_beside_
  its_counter_dict` red, 1 of 7. (iv) a deliberately STALE exemption (`pricing.py` added to
  `_READ_SIDE_FOLDS`) → `test_the_read_side_exemption_is_still_read_side` red, 1 of 7.
  **Recorded, not widened** (both belong to `EXT:COST-AND-TOKEN-OBSERVABILITY`, which this atom's
  own dep row says owns the readout): the `Telemetry` row sits behind a disclosure defaulting to
  CLOSED, and **no frontend test asserts `activityKind === 'stats'` at all**, so the production
  reader of these numbers could be deleted with every gate green. Also unchanged, and outside this
  atom's file fence: `dashboard/handlers/model_telemetry.py` and `exposure.py`.
  **Still standing after this pass: the audit's SPLIT recommendation.** The numbers half has no
  remaining work and no remaining risk; the live-OpenAI half cannot be closed from this environment
  at all. One row holding both makes a fully-built, fully-railed deliverable read `todo`
  indefinitely over a credential the repo does not have. `dag.json` was not touched — that flip is
  the owner's.

- **2026-08-27 — `PCS-7`: the FRONTEND reader is now railed. Atom stays `todo` on the same
  credential-gated V2 half; nothing about the numbers changed.** The 2026-08-24 audit above closed
  by recording that "no frontend test asserts `activityKind === 'stats'` at all, so the production
  reader of these numbers could be deleted with every gate green", and deferred it to
  `EXT:COST-AND-TOKEN-OBSERVABILITY` on the dep row's "owns/renders the readout" wording. That was
  the wrong home for it: COST-AND-TOKEN-OBSERVABILITY owns the *readout's design*, but the four lines
  that carry PCS-7's own numbers from the wire to the DOM are this atom's deliverable, and an
  unrailed reader is the inert-control shape whichever plan owns the pixels. Verified before writing,
  not assumed: `git grep` over every `web/src/**/*.test.ts{,x}` found `stats` named in ten files and
  the `'stats'` ACTIVITY KIND in none — `contextLedgerReach.test.tsx` passes a `stats=` prop into its
  fixture and asserts only the `learned` row, so it was the closest thing to coverage and covered
  nothing.
  **What the reader actually is — four deletable lines, none of them type-checked into place.**
  `ChatPage.tsx:3650` folds `ak === 'stats'` into `ledger.stats`; `:3652` counts it in `hasLedger`
  (drop it and a turn that fed no context and learned nothing has no way to open the disclosure at
  all); `:3687` excludes it from `isProcess` so it renders once in the ledger rather than twice;
  `:3740` hands it to `<ContextLedger stats={…}>`, which gates the `Telemetry` row on it
  (`ContextLedger.tsx:98-102`). All four are string comparisons and JSX attributes — `tsc` is happy
  with every one of them removed.
  **`web/src/pages/chat/turnTelemetryReader.test.tsx`, 16 tests, two halves.** The RENDERING half
  mounts the real `ContextLedger` (which is a separate module precisely so it can be mounted — the
  reason `contextLedgerReach.test.tsx` gives) and opens the disclosure the way a user does. The FOLD
  half scans `ChatPage.tsx` as source in the JSX attribute/expression form `skillsUsedChip.test.ts`
  already uses for the neighbouring `learned` row — a ~4k-line page owning a socket and a composer is
  not mountable here — plus ONE behavioural leg through the real `insertActivity` proving the
  `'stats'` discriminator the fold matches on is the string the live WS handler actually stamps. Both
  halves carry a vacuity floor: a turn with no stats renders NO `Telemetry` row (without it every
  "contains" assertion is satisfiable by an always-on row), and the source scan asserts it reached a
  >100k-char page containing `function AssistantSegments(`.
  **The honest-`None` legs are the ones that matter, and their fixtures are the PRODUCER'S own
  sentences.** The five strings under test were taken from a real `_turn_complete_line` call and match
  `tests/test_turn_complete_cache_telemetry.py`'s literals character for character, because a fixture
  invented on the frontend would let this file guard a sentence the backend never emits — the
  one-sided-inventory failure. `saved unpriced` must survive with no `$` anywhere after `saved`;
  `cache (12,400 read / 1,200 written)` must survive with no `% hit`; and the DISCRIMINATION leg
  renders unpriced / measured-`$0.0000` / no-percentage / measured-`0% hit` in four independent mounts
  and asserts **four distinct texts**, because each of the other legs is individually satisfiable by a
  reader that collapses "we don't know" into "it was zero". One scoping correction worth recording:
  "the row shows no percentage" cannot be asserted against the whole line — it carries a
  `context 42%` fragment — so every such assertion is scoped to the `Telemetry` row and, for the
  money, to the substring after `saved`.
  **Falsified five ways — each mutation on the LIVE line, `git grep`'d back to prove it applied, each
  restored from a file COPY at the literal path (never `git checkout --`), tree confirmed clean
  between runs. Five DISTINCT red sets, so the legs discriminate instead of all keying on one line.**
  (i) deleting the fold at `:3650` → **1 failed / 54 passed of 55**, and both
  `contextLedgerReach.test.tsx` and `skillsUsedChip.test.ts` stayed GREEN — which is the measured
  proof of the gap, since before this file that deletion reddened nothing at all. (ii) deleting the
  whole `Telemetry` row from `ContextLedger.tsx` → **8 failed / 16 passed of 24**, with the 8-test
  `contextLedgerReach.test.tsx` fully green beside it. (iii) a "tidying" reader that rewrites
  `saved unpriced` → `saved $0.0000` and `cache (` → `cache 0% hit (` — the realistic future
  regression — → **3 failed / 21 passed of 24**: exactly the two honest-`None` legs plus the
  discrimination set, while the priced and measured-zero legs stayed green. (iv) dropping
  `ledger.stats` from `hasLedger` → **1 failed / 15 passed of 16**. (v) dropping `'stats'` from the
  `isProcess` exclusion → **1 failed / 15 passed of 16**.
  **Test-only, deliberately.** The reader was found CORRECT: it formats none of these numbers, which
  is why the backend's honesty rules are the ones that hold end to end, and no production line was
  changed. `docs/design/consistency-audit.json` was rewritten by `npm run build` and reverted — it is
  stale on `main` and is not this diff. **Gate:** `npm run typecheck:web` clean, `npm run test:web`
  **539 files / 5869 tests all passed** (16 of them new), `npm run build` clean. No Python changed,
  so no `make` leg applies. `dag.json` untouched — that flip is the owner's, and the atom's remaining
  clause is still the one no local environment can close: a real OpenAI-family run, plus a Bedrock
  inference-profile id that resolves to a price row.
