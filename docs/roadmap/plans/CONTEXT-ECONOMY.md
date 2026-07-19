# Plan: Context Economy — Reversible Compression + Dynamic Tool-Group Activation

**Status:** PROPOSED (created 2026-07-13 from research synthesis, promoted from backlog)
**Created:** 2026-07-13
**Wave:** 0-1 — the compressor library, retrieval hardening, savings accounting, background compression service, and tool-group lifecycle are all v2-INDEPENDENT (they extend shipped subsystems: TokenJuice, the native runtime toolset assembly, the consolidation cadence). The workflow-node consumers (§2.5, §5.4) land WITH the engine (WORKFLOWS-V2 Slices 0-3) and are speced here only as adapters.
**Depends on:** nothing for Sessions 1-4. Session 5's template-declared groups consume WORKFLOWS-V2's spec format when it exists; until then per-surface defaults carry the value.
**Companions:** WORKFLOWS-V2 (§2 Context Lifecycle / WF2-R6 output offloading consumes the shared compressor), WORKFLOWS-V2-LOOPS-EVOLUTION (LOOP-R13 topic-segmented compression consumes the same segmenter), WORKFLOWS-V2-LEARNING-FLYWHEEL (LEARN-R12 owns the skills-side context-budget reclaim; this plan owns the tool-schema side), AUTONOMY-GUARDRAILS (§2 metering is the eventual authoritative token/dollar source for savings accounting).
**Scope:** one token-economy substrate with two halves: (a) extend the SHIPPED TokenJuice (OP1/OP5/OP6) into a full compress-cache-retrieve loop — type-routed compressors, three-layer rule overlay, prefix-stable output, per-model savings accounting, and a continuous background compression service over conversation/loop history; (b) partition the growing tool surface into named groups with an activation lifecycle and a `reset_tools`-style final-state meta-tool, so inactive groups cost zero context.

---

## Research Integration (2026-07-13)

- **NEW-16** (reversible context compression: compress-cache-retrieve loop, lossy compressed originals behind unguessable hash markers + retrieval tool with byte/line ranges; type-routed compressors — JSON crusher, AST-aware code, prose model — over subagent transcripts and tool outputs; builtin/user/project three-layer JSON rule overlay; prefix-stable output preserving KV-cache; persisted per-model savings accounting) → §1, §2, §3, Sessions 1-2. Sources: `youtube-agent-video-mmywe` (OpenHuman TokenJuice CCR: `⟦tj:<hash>⟧` markers, `tokenjuice_retrieve`, ~96-rule builtin overlay, per-model savings), `githubsignals-instagram`, `agent-zero`.
- **NEW-16 amendment** (continuous background compression service over old conversation and loop history using topic segmentation and attention-weighted summarization — the always-on complement to the on-demand loop) → §4, Session 3. Source: `agent-zero` (topic segmentation with attention ratios: current ~65%, historical request/response only, bulk summarized; async between iterations).
- **NEW-22** (dynamic tool-group activation: named groups with activation/deactivation lifecycle; inactive groups remove their tool schemas from the agent context; a reset_tools-style final-state meta-tool; groups declared per-template, per-surface, per-capability) → §5, Sessions 4-5. Sources: `agentscope` (ToolGroup partition, reserved always-on `basic` group, `reset_tools` boolean-per-group FINAL-STATE semantics, activation returns per-group instructions), `claude-code-best-practice` (structural tool denial; 15k-char description budget discipline).

**Overlap with approved roadmap (honored, not duplicated):**
- **LEARN-R12** (approved, WORKFLOWS-V2-LEARNING-FLYWHEEL §1/§2.4): the `model_invoked: bool` axis already removes command-like skills from the surfacing embeddings and INDEX — the SKILLS-side context-budget reclaim is that plan's job and is only referenced here. This plan owns the orthogonal TOOL-SCHEMA side; the group-activation "returns per-group instructions" pattern (§5.2) deliberately mirrors R12's router-entry shape so the two budgets read consistently.
- **WF2-R6 / WF2-R13** (approved, WORKFLOWS-V2 §2 Context Lifecycle + Run Ledger): node output offloading to `runs/<id>/artifacts/` and crystallize-before-prune journal digests are the ENGINE's context lifecycle and stay speced there. This plan supplies the shared compressor library those mechanisms call (§2.5) and does NOT re-spec run-journal offloading. The background service (§4) covers chat sessions + pre-v2 loop history only; workflow-run history compression is owned by crystallize-before-prune.
- **LOOP-R13** (approved, WORKFLOWS-V2-LOOPS-EVOLUTION, Context-overflow recovery): proactive topic-segmented compression for workflow-template loops is that plan's migration item. §4's topic segmenter is built as the SHARED primitive LOOP-R13 consumes — one segmenter, two callers.

---

## Overview

PersonalClaw already shipped more of NEW-16 than the backlog text assumes — verified starting points (code read 2026-07-13):

1. **TokenJuice is live** (`src/personalclaw/tool_providers/projection.py`; code labels OP1 dispatch / OP5 retain / OP6 user rules — there is NO "SB3" identifier in code). `_PROJECTORS = {log, diff, json, test, csv}` (projection.py:271), `infer_content_type` conservative sniff, `DEFAULT_TOOL_OUTPUT_CAP = 60_000`, and — critically — **`project_and_retain` (projection.py:321) already implements the cache-retrieve half**: every projected result stores its full raw via `result_store.store_result` and appends a `tool_result_get(result_id="r_…")` recovery hint to the preview. Native builtins AND the MCP adapter share this discipline.
2. **The retrieval tool exists**: `tool_result_get` (registered core-locked, `agents/native/builtin_tools.py:108,718`) backed by `result_store.fetch_slice` (result_store.py:115) which already supports **char ranges (`start`/`end`) and `grep`** over the stored raw. NEW-16's "retrieve_original tool w/ byte/line ranges" is therefore an EXTENSION of `tool_result_get` (line ranges + hash ids), not a new tool — the backlog's assumed seam is adapted to the real one.
3. **The rule overlay is two-layer today**: builtin projectors + user `ProjectionRuleConfig` rules (`config/loader.py:1389 ToolsConfig.projection_rules`, PATCH-editable via `_EDITABLE_CONFIG` with a live `projection.set_user_rules` side effect, `dashboard/handlers/core.py:608`). Rules are declarative regex→strategy dispatch — no user code runs. Missing: the builtin RULE pack (dispatch rules, not just projectors), the project layer, and richer rule operations.
4. **Gaps that make compression lossy-without-recourse elsewhere**: subagent results are blind-capped (result file 500KB, in-memory 3000 chars post-injection, `subagent.py`; `context_management.cap_result_file` is one of that module's few ALIVE parts) with no raw_ref; result ids are count-based (`r_{n:03d}{4hex}`, result_store.py:41) so identical outputs store twice and markers are not content-stable; `fetch_slice` has no line addressing; nothing meters what projection saves; and old session/loop history only ever gets whole-transcript compaction (`history.py:845 HistoryConsolidator`, `sliding_window`/`rewrite_session` + `_archive_lines(reason="compact")`).
5. **The tool-surface side**: the native runtime assembles ALL enabled providers' schemas every session (`agents/native/runtime.py:309 start()` — the verified single assembly seam, where user-disabled tools (`tool_providers/tool_prefs.py`, CORE_LOCKED frozenset :38) and unattended interactive-tool stripping already happen). Per-turn `ToolRetriever` (TR2, `agents/native/tool_retrieval.py`, DEFAULT_K=48, fails OPEN) plus the `tool_search`/`tool_schema` progressive-disclosure pair already reduce per-turn schema bytes — but there is NO activation lifecycle: every enabled provider's tools occupy catalog space every turn, and nothing lets a template/surface/capability declare "these groups only." Workflows alone will add ~15 tool schemas; groups are the mitigation the roadmap otherwise lacks.

**Soul guardrail:** this is *personal-scale* token thrift — one user's laptop, local JSON stores, deterministic compressors on the hot path, the LLM only in background passes. No compression microservice, no telemetry pipeline; the savings panel is a Settings card derived from one JSON file. Everything reversible: the cardinal failure of compression is hiding the part the model needed, so every lossy step keeps a named road back to the raw bytes.

---

## 1. The Compress-Cache-Retrieve Loop, Hardened (NEW-16 core)

### 1.1 Content-hash markers (unguessable + idempotent)

Replace the count-based result id (`r_{n:03d}{suffix}`, `result_store._next_id`) with a content-addressed id: `r_<sha256(raw)[:12]>`. Three wins, in priority order:

- **Idempotent storage**: the same large output stored twice in a session (retries, re-runs) dedupes to one file — content addressing is the OpenHuman CCR design and matches the engine plan's idempotency doctrine.
- **Marker stability**: the recovery hint appended to a preview becomes a pure function of the content, so replayed/compacted transcripts stay byte-identical (prefix stability, §3).
- **Unguessability**: a hash id can't be enumerated by a prompt-injected instruction fishing for other results (`fetch_slice` already rejects path-traversal ids; single-user threat model, so this is defense-in-depth, not the headline).

Backward-compat: `get_result` keeps reading legacy `r_NNN…` files; new writes are hash-form. `_MAX_PER_SESSION = 200` eviction and the never-raise contract are unchanged.

### 1.2 `tool_result_get` gains line addressing

`fetch_slice(session_id, result_id, *, start, end, grep, max_chars)` (result_store.py:115) gains `line_start`/`line_end` (1-indexed, mutually exclusive with char `start`/`end`; `grep` unchanged). The tool schema in `builtin_tools.py` and the recovery hint text in `project_and_retain` are updated to name all three access modes ("full result: `tool_result_get(result_id=…, line_start=…, line_end=…)` / `grep=…`"). This closes NEW-16's "byte/line ranges" on the REAL tool rather than adding a parallel `retrieve_original` — one recovery affordance, already known to the model and the ACP MCP surface.

### 1.3 Per-model savings accounting

A small meter at the `project_and_retain` seam (and §2's compressor calls): every projection that truncated records `(model_hint, compressor/content_type, chars_in, chars_out)` into `~/.personalclaw/tokenjuice_savings.json` (atomic_write; aggregated rows keyed `(month, model, compressor)` — bounded by construction, no per-event log). Tokens are estimated (`chars/4`, flagged `estimated: true`); dollar figures are computed ONLY when per-model input pricing is known and are labeled estimates. **Disposition:** `LLMEvent.cost_usd` exists but is unpopulated (verified `llm/anthropic.py:535`, `llm/openai.py:438` hardcode 0.0) — authoritative spend metering is AUTONOMY-GUARDRAILS §2's attempt records; this store is the *savings* (counterfactual) ledger and will cross-reference the guardrails `model_calls.jsonl` for real token counts once that lands, rather than duplicating metering. The model hint comes from the session's resolved provider where available and `"unknown"` otherwise — accounting must never block or slow dispatch. Surface: a card in Settings → Tools ("TokenJuice saved ~N tokens this month, top compressor: log"), GET `/api/tools/savings`.

---

## 2. Type-Routed Compressors

The projector table grows from shaping-by-elision to true type-routed compression. All hot-path compressors stay **deterministic and synchronous** (the tool-dispatch path cannot await an LLM); the prose-model compressor is background-only (§2.4). Every compressor keeps `project_and_retain`'s invariant: lossy output ⇒ raw retained ⇒ recovery hint appended.

### 2.1 JSON crusher (upgrade `_project_json`)

Today's `_project_json` shows shape + a half-cap sample. The crusher adds: per-path schema inference over arrays (field names, types, value ranges, null counts from a bounded sample), first/last item verbatim, and repeated-structure folding (`[array: 4,812 items, uniform shape {…}]`) — a 100K-item API response projects to ~1K chars that still answer "what shape is this and what's in it." Parse failure falls back to head/tail exactly as today (fail-soft is non-negotiable on this path).

### 2.2 AST-aware code compressor (new `code` content type)

New projector for large code outputs (a tool that `cat`s a module, a generated file echoed back): Python via stdlib `ast` → module docstring + import block + class/def signatures with their docstring first-lines + line-number map; other languages via a regex outliner (def/function/class/interface headers). Soul check: stdlib only — no tree-sitter dependency for v1; the regex outliner is the honest fallback and the raw is one `tool_result_get` away. `infer_content_type` gains a conservative code sniff (shebang/`def |class |import |function |=>` density gate); `CONTENT_TYPES` grows `"code"`.

### 2.3 Three-layer rule overlay (builtin / user / project)

Extends OP6 in place — same declarative, no-user-code stance, now three layers with fixed precedence **project > user > builtin** (most-specific intent wins; verified current behavior is user-rules-before-sniff, which becomes the middle layer):

- **Builtin pack**: a JSON rule file shipped in-tree (`tool_providers/rules_builtin.json`) mapping common command-output markers to strategies (git/npm/pip/pytest/docker/cargo heads → log/diff/test) — the dispatch analog of OpenHuman's ~96-rule pack, sized to what PClaw's own tools actually emit (start ~25 rules, grown by evidence).
- **User layer**: the existing `ToolsConfig.projection_rules` (all four config wiring points already done for this field — verified; the PATCH live-apply side effect at `core.py:608` is kept).
- **Project layer**: `.personalclaw/projection_rules.json` in the session cwd, loaded per-session (mtime-cached), same `ProjectionRule` schema, versionable with the repo. **Trust note:** a project file is repo-supplied config; rules remain pure dispatch data (regex → builtin strategy name) so the blast radius of a hostile rule is "wrong projector chosen," never code execution — same posture as the existing user rules, and consistent with the scope doctrine that repo-level config must not gain write powers (`claude-code-best-practice` autoMemoryDirectory lesson).
- **Rule ops v2**: rules gain optional declarative operations beyond strategy dispatch — `head`/`tail` line counts, `keep`/`skip` line-regex filters, and a `count` folder ("N lines matching X elided") — all executed by one shared interpreter, still no user code. Validated at the PATCH boundary exactly like today (bad regex skipped + logged, never raising into dispatch).

### 2.4 Prose-model compressor (background paths only)

An LLM summarizer (`one_shot_completion(use_case="background")` — the `reasoning`-axis resolution path, never the native chat runtime) for long natural-language outputs, used ONLY where latency is already tolerable: the background compression service (§4) and the subagent-result path (§2.5). It is never wired into `project_output`'s synchronous dispatch. Output contract: bounded summary + the raw_ref line; a summarizer failure degrades to the deterministic `log` projector (the guard-the-guard pattern). When AUTONOMY-GUARDRAILS lands, these calls inherit its chokepoint (breaker/metering) for free — no bespoke resilience built here.

### 2.5 First consumers (the two the backlog names)

- **Subagent transcripts/results** (v2-independent, Session 1-2): `SubagentManager`'s result handling routes through `project_and_retain` instead of the blind 3000-char injection cap — the parent session receives a type-projected digest carrying a raw_ref, so "the subagent found it but the cap ate it" stops being a failure class. The 500KB result-file cap (`context_management.cap_result_file`) stays as the outer bound. Session key for retention is the PARENT's (the injected message lives in the parent transcript; its raw must share that lifecycle).
- **Workflow-node tool outputs** (lands with the engine): the engine's output offloading (WF2-R6: journal keeps head/tail, body to `runs/<id>/artifacts/`) calls this plan's compressors for the head/tail shaping and the `artifact_inspect` fallback chain — this plan ships `project_and_retain` as the shared library call and defers ALL journal/artifact mechanics to WORKFLOWS-V2 §2 (no duplication; see Overlap notes).

---

## 3. Prefix Stability — the KV-Cache Contract

Compression must not silently destroy provider KV-cache hits (`youtube-agent-video-mmywe`: system prompt built once per session, byte changes force re-prefill). Three invariants, testable:

1. **Dispatch-time only, never retroactive**: projection happens when a tool result is CREATED; no mechanism in this plan ever rewrites a message already sent to the model mid-session. (Verified current behavior — preserved as an explicit contract.)
2. **Deterministic markers**: with §1.1 content-hash ids, projecting the same raw twice yields byte-identical previews and recovery hints — a resumed/replayed transcript re-serializes identically.
3. **Compaction is a declared prefix break**: `rewrite_session` (history.py:682) and the compact hook (`context_compaction.py`) are discrete whole-transcript events — inherently cache-invalidating, and that is fine because they are RARE. The background service (§4) only touches sessions **at rest** (no live in-flight message list), so it never breaks an active session's prefix. A unit test locks invariants 1-2 (same input → same bytes; no API that mutates prior messages outside the two named compaction events).

Corollary for §5: changing the active tool-group set changes the tool-schema block → a prefix break. Therefore group changes take effect at the NEXT turn boundary, the schema block serializes groups and tools in stable sorted order (identical active set ⇒ identical bytes), and the `reset_tools` description tells the model group changes are not free ("batch your group changes").

---

## 4. Continuous Background Compression Service (NEW-16 amendment)

The always-on complement to on-demand projection: old conversation and loop history gets compressed without manual compaction triggers, so long-running sessions and high-volume channels stay fast.

- **Cadence (real seam, not a new daemon):** rides the existing consolidation maintenance cadence — the `HistoryConsolidator` tick that already runs post-consolidation maintenance (the same verified tick LEARNING-FLYWHEEL wires its curator to). One additional maintenance pass, budgeted (max N sessions per tick, oldest-first), never on the request path.
- **Eligibility:** sessions idle > `tools.bg_compress_idle_days` (default 7) whose transcript exceeds a size floor; per-loop file dirs (`config_dir()/loop/<id>/findings/`) of TERMINAL loops past the same idle window. Workflow-run dirs are EXCLUDED — crystallize-before-prune (WF2-R13) owns those.
- **Topic segmentation + attention-weighted summarization** (`agent-zero`): segment the transcript by embedding-drift boundaries (active embedding provider; deterministic turn-count fallback when no embedder is bound — degraded mode is designed, not accidental), then compress per-topic with attention ratios: most-recent topic kept near-verbatim, middle topics reduced to request/response pairs (tool noise dropped — it already carries raw_refs), oldest tier bulk-summarized via the §2.4 prose compressor. The segmenter is built as a standalone module (`context_segmentation.py`) because LOOP-R13's proactive in-loop compression is speced to consume the SAME primitive — one segmenter, two callers.
- **Reversibility:** every dropped span goes through the existing `_archive_lines(key, …, reason="bg_compress")` archive path (history.py:55) before `rewrite_session` — the same mechanism manual compaction uses, so nothing this service touches is unrecoverable; the summary line names the archive file. `tool_result_get` raw stores survive per their own session-dir lifecycle (OP4 no-double-loss: compaction summaries preserve raw_ref strings verbatim — already the `prune_tool_outputs` contract, `context_compaction.py:69`, extended to this pass).
- **Privacy boundaries:** sessions whose JSONL metadata carries `memory_mode: incognito|temporary` are SKIPPED entirely (the durable mark, same check consolidation uses — history.py:483-497); this service produces derived text within the same session store — it never writes to memory.db (MEMORY = harness mechanics is untouched except via the normal consolidation that already exists) and never touches knowledge.db (KNOWLEDGE = user's items; compression of the user's conversation history is not knowledge ingestion).
- **Kill switch + budget:** `tools.bg_compress_enabled` (default ON, fail-safe parse per the AUTONOMY-GUARDRAILS §5 tenet — but note this flag is a *feature* flag, not a guard flag: missing parses as the DEFAULT, documented in `_meta`); per-tick LLM-call cap so a backlog of old sessions can't burn a night of background tokens. Savings recorded in the §1.3 store under compressor `"bg_topic"`.

---

## 5. Dynamic Tool-Group Activation (NEW-22)

### 5.1 The group model

```python
# tool_providers/groups.py
@dataclass(frozen=True)
class ToolGroup:
    name: str                 # "core", "schedule", "artifacts", "workflows", "memory",
                              # "subagents", "mcp:<server>", "app:<name>", "browse", ...
    display: str
    instructions: str = ""    # returned to the model on activation (agentscope pattern)
    always_on: bool = False   # the reserved basic group; cannot be deactivated
    capability: str = ""      # optional gate: group only OFFERABLE when this resolves (§5.5)
```

Groups are derived, not hand-maintained: **one group per registered tool provider** — the six in-process category providers (`tool_providers/registry.py:create_{native,schedule,artifacts,workflows,memory,subagents}_provider`), one per MCP server, one per app-contributed tool provider (the `tool` `_TypeHandler` → `tool_providers/registry.py` path). The `core` group (the `personalclaw-core` provider ∪ `CORE_LOCKED` names ∪ the synthetic `tool_search`/`tool_schema`/`tool_result_get`/`reset_tools` defs) is `always_on=True` — agentscope's reserved `basic` group. A provider MAY declare finer subgroups in the future; v1 is provider-grain because that is the partition the registry already maintains.

### 5.2 Activation lifecycle + the `reset_tools` meta-tool

Per-session activation state lives on the runtime (in-memory, seeded from §5.3 defaults; a restart re-seeds — acceptable, single-user). The meta-tool follows agentscope's **final-state semantics** exactly (one boolean per group; unset ⇒ deactivate) because delta semantics ("activate X") accumulate drift over long sessions:

```
reset_tools(groups={"schedule": true, "memory": true})   # ALL other non-always-on groups deactivate
```

- Registered in the `core` group (always available, `requires_approval=False`, `RiskLevel.SAFE` — it changes what the model sees, not what it can do).
- The result message lists the new active set and returns each newly-activated group's `instructions` — the model gets usage guidance exactly when it gains the tools (the R12 router-entry shape, applied to tools).
- Takes effect at the **next turn boundary** (§3 prefix corollary); the current turn's in-flight tool calls are unaffected.
- **Selection ≠ dispatch (fail-open doctrine, preserved):** deactivation removes schemas and catalog entries from what the model SEES; the runtime `_tool_index` dispatch map keeps every tool callable — same invariant the shipped `ToolRetriever` established (tool_retrieval.py docstring: "a hidden tool is a capability regression, not a safety risk"). Group activation is context economy, NOT a security boundary; structural tool DENIAL remains `tool_prefs` disable + the engine's node-level tool policy (WORKFLOWS-V2 §"node-level tool allow/deny", approved) + unattended interactive-stripping — all of which apply BEFORE grouping in the assembly order.

### 5.3 Where it plugs in — the assembly seam

`NativeAgentRuntime.start()` (`agents/native/runtime.py:309`) is the ONE verified toolset-assembly seam (disabled tools, unattended stripping, risk map, retriever construction all live there). Group filtering slots into the existing chain, and a `refresh_toolset()` re-runs schema assembly (not provider discovery) on group change:

```
provider list → tool_prefs disable (hard gate) → unattended strip (hard gate)
→ GROUP FILTER (schema visibility)  ← new
→ ToolRetriever per-turn selection (within active groups)  ← scoped
→ schema serialization (stable sort)
```

- **`ToolRetriever` composes, not competes:** retrieval selects within ACTIVE groups (its embeddings/sticky-set logic unchanged); `tool_search` searches the FULL catalog **across inactive groups too**, and a hit in an inactive group returns `"…in inactive group 'schedule' — activate via reset_tools"` — search becomes the discovery path INTO groups, so a hidden tool is one search + one activation away (fails open in spirit).
- **Inactive groups leave a stub, not silence:** the per-turn catalog (`tool_retrieval.catalog`) renders each inactive group as ONE line (`schedule (7 tools, inactive): cron + reminders — reset_tools to activate`) so the model knows the capability exists at ~15 tokens instead of ~7 schemas.
- **ACP surface disposition:** the aggregated MCP server for ACP CLIs (`mcp_core._AGGREGATED_CATEGORY_MODULES`, mcp_core.py:918) keeps exposing the full set in v1 — external CLIs manage their own context, and MCP `tools/list_changed` dynamics across three ACP dialects is a validated-risk area (P9#7) not worth coupling to this plan. Explicit non-goal, revisit if ACP context pressure materializes.

### 5.4 Declaration surfaces

- **Per-surface (v2-independent, Session 4):** defaults keyed off the session-class conventions that already exist — chat/dashboard sessions: all groups active (today's behavior — zero regression until the user or a template opts in); background/`subagent:` sessions (already routed to the "background" prompt use-case, context.py:279): `core + memory` default; loop workers (`session._app == "loop"` — the manager sets it; do NOT key on the `loop-` prefix, recon-verified trap): kind-appropriate defaults (code loops get `core + workflows + subagents`). Defaults in `ToolsConfig.group_defaults` (§6 wiring).
- **Per-template (lands with the engine):** the WORKFLOWS-V2 spec's stage/node config gains `tool_groups: [...]` resolved at stage-session spawn — only relevant groups active per stage. This plan ships the runtime parameter (`tool_groups` kwarg on toolset assembly); the spec-field plumbing is one line in the engine's stage-spawn path and is listed in that plan's consumption notes, not rebuilt here.
- **Per-capability (§5.5).**

### 5.5 Per-capability gating

A group with `capability` set is only OFFERABLE (appears in stubs / activatable) when the capability resolves: a future `browse` group checks its action/search provider binding via the cheap no-instantiate probe (`provider_bridge.can_resolve_use_case` for model-shaped capabilities; registry presence for tool/search providers). An unbound capability's group is neither active nor stub-listed — the model never sees tools that cannot work. This is evaluated at assembly time (cheap), re-checked on `refresh_toolset()`.

---

## 5.5 Codebase Graph — Semantic Code Index for Agent Navigation (grok-build learning, 2026-07-17)

xAI's grok-build ships a dedicated `xai-codebase-graph` crate + `code_nav.rs` inside the agent loop — a semantic index (functions, classes, imports, references) the agent queries instead of grepping blind. For PersonalClaw this is a context-economy win first (fewer exploratory tool calls = fewer tokens) and a Code-loop quality win second.

- **Scope (deliberately light):** a per-workspace index of definitions (functions/classes/methods with file:line), import edges, and a reverse-reference map — built with tree-sitter (Python/TS/JS/Rust/Go first; the AST outliner from §2.2 shares the parser install). NOT a full LSP; no type inference.
- **Store:** `~/.personalclaw/codegraph/<workspace-hash>.db` (SQLite, atomic rebuild). Built lazily on first Code-loop/chat use of a workspace; invalidated per-file by mtime; full rebuild capped (~30s budget, fail-soft to no-graph).
- **Consumers:** (a) a `code_map` tool (grouped under `workflows` per §5.1) — query by symbol name/file → definitions + references, replacing 3-5 grep/read round-trips with one call; (b) the SDLC engine's planning stage receives a top-level module summary (package layout + public API surface) in its context assembly — bounded to ~2K tokens via the §2 projectors; (c) chat `@`-mention file search ranks by graph centrality when the index exists.
- **Fail-soft doctrine:** no graph → everything works exactly as today (grep/read). The graph is an accelerator, never a dependency. Indexing errors log and skip the file.
- **Session (+1, appended as Session 6):** tree-sitter indexer + SQLite store + mtime invalidation; `code_map` tool + group registration; SDLC planning-context integration; as-a-user validation (Code loop on a multi-module repo shows fewer exploration calls in the transcript).

---

## 6. Provider-Fidelity Wiring (where each piece plugs in)

- **No new provider TYPE.** Compression and grouping are substrate over the EXISTING tool surface — same stance as guardrails ("no space provider type", `providers/registry.py:555`). Nothing registers through `_TypeHandler`s; no entry in `PROVIDER_TYPES` changes.
- **Tool providers:** app-contributed tool providers (manifest `provider: {type: "tool"}` → tool `_TypeHandler` → `tool_providers/registry.py`) inherit BOTH halves for free: their outputs pass `project_and_retain` at the shared dispatch discipline (native builtins + MCP adapter path, already unified), and each registered provider automatically becomes a group (§5.1) — an app ships tools, the platform owns their context economics.
- **Action providers:** none added; `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`) is untouched. (Restated per platform rule: any future action provider must be added there or hook create/update rejects it.)
- **Model providers:** untouched. The prose compressor rides `one_shot_completion(use_case="background")` → `resolve_provider_for_use_case` like every background caller; the `model` build-kwarg convention and active_models.json bindings are not modified.
- **Config — the FOUR wiring points** for every new field: (a) dataclass field with `_meta(label, help)` on `ToolsConfig` (loader.py:1410) — new fields: `bg_compress_enabled`, `bg_compress_idle_days`, `groups_enabled`, `group_defaults`, plus `ProjectionRuleConfig` gaining the §2.3 op fields (each element field needs `_meta`, the list[dataclass] precedent); (b) `AppConfig.load()` explicit mapping (loader.py:1638-1802 — omission = silent drop); (c) `to_dict()` (:1930 — ToolsConfig section exists, fields extend it); (d) `_EDITABLE_CONFIG` (`dashboard/handlers/core.py:363`) + FE for the runtime-editable subset (`tools.projection_rules` is already there with its live-apply side effect; `bg_compress_*` and `groups_enabled` join it).
- **SDK:** `sdk.tool` (existing facade) re-exports `project_and_retain` + the rule schema so contributed tool backends can pre-project; no new SDK module.
- **SEL:** nothing here is security-eventful (no blocks, no trust transitions) — deliberately NOT logged to `sel.py`, keeping the SEL signal-dense. Background-compression actions log to the normal logger + the savings store.
- **Stores (all `~/.personalclaw/`, atomic_write):** `tokenjuice_savings.json` (aggregated, bounded); `sessions/<key>/tool_results/r_<hash>.json` (existing store, new id form); project rules read from cwd (not a home store). Snapshot/portability: savings is derived data — excluded; archived compaction lines already live inside the session tree portability exports.
- **Memory vs Knowledge boundary:** untouched on both sides. Tool-result stores, savings accounting, and compressed transcripts are harness mechanics under `~/.personalclaw/` — not memory.db entries, not knowledge.db items. The background service reads/writes session JSONL only; any LESSON about compression behavior belongs to LEARNING-FLYWHEEL and stays propose-don't-write.

---

## 7. Implementation Effort

**~6 sessions.**

- **Session 1 — retrieval hardening + accounting (§1, §2.5a):** content-hash result ids with legacy-read compat; `fetch_slice` line ranges + tool schema/hint updates; savings meter + store + `GET /api/tools/savings` + Settings card; subagent-result path through `project_and_retain` (raw_ref in parent injection). Regression: OP4 no-double-loss test extended to the new id form.
- **Session 2 — type-routed compressors + rule overlay (§2.1-2.4):** JSON crusher upgrade; `code` content type + AST/regex outliner; builtin rule pack + project rule layer + rule ops v2 with one interpreter + PATCH-boundary validation; prose-model compressor module (background-only, deterministic fallback).
- **Session 3 — background compression service (§3, §4):** `context_segmentation.py` (shared with LOOP-R13); attention-weighted per-topic compression; consolidation-cadence wiring with per-tick budget; incognito/temporary skip; archive-before-rewrite reversibility; prefix-stability unit tests (invariants 1-2).
- **Session 4 — tool groups core (§5.1-5.3):** `ToolGroup` derivation from the provider registry; per-session activation state + `refresh_toolset()`; `reset_tools` meta-tool with final-state semantics + activation instructions; group filter in the assembly chain; catalog stubs; `tool_search` cross-group discovery; stable-sort schema serialization; per-surface defaults.
- **Session 5 — declaration surfaces + validation (§5.4-5.5, FE):** per-capability gating; `tool_groups` assembly kwarg (the engine-consumer seam, documented for WORKFLOWS-V2); config wiring completion across all four points; FE (Tools page: group chips with active/inactive/toggle, savings card polish); as-a-user validation sweep (real sessions, real MCP servers, group churn under a long chat).

- **Session 6 — codebase graph (§5.5, grok-build learning):** tree-sitter indexer (shares §2.2's parser install) + SQLite store + mtime invalidation; `code_map` tool registered as a group; SDLC planning-context module summary; `@`-mention centrality ranking; as-a-user validation on a multi-module repo.

Sessions 1-3 (NEW-16) and 4-5 (NEW-22) are independent tracks; either alone is a Wave-0 win. Session 6 depends only on Session 2's tree-sitter install (or ships its own) and is otherwise independent. The engine consumers (§2.5b node outputs, §5.4 per-template groups) activate when WORKFLOWS-V2 Slices 0-3 land — one library call and one kwarg respectively, both speced above so the engine plan consumes rather than designs.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Compression hides the exact thing the model needed (the cardinal TokenJuice failure) | Unchanged doctrine: under-cap passes through untouched; unknown types → today's head/tail; EVERY lossy step retains raw + names the recovery call; new compressors are additive projectors behind the same fail-soft dispatch |
| Prose-model compressor cost/failure on background paths | Background-only, per-tick LLM budget, deterministic `log`-projector fallback; inherits the AUTONOMY-GUARDRAILS chokepoint (breaker/metering) when it lands |
| Background service compresses something the user wanted verbatim | At-rest + idle-window only; archive-before-rewrite via the existing `_archive_lines` path (nothing unrecoverable); summary lines cite the archive; kill switch + idle-days knob |
| Topic segmentation without an embedder degrades badly | Deterministic turn-count fallback declared as the designed no-model tier (not an error path); segments merely coarser |
| Hash-id migration breaks stored-result recovery | Legacy id READ path kept; ids only change for new writes; eviction logic id-form-agnostic (mtime-sorted, verified) |
| Group deactivation strands a mid-task model without its tools | Fail-open triad: dispatch index never filtered; catalog stubs advertise inactive groups; `tool_search` reaches across groups and names the activation step. Default-all-active for chat = zero behavior change until opted in |
| `reset_tools` thrash shreds KV-cache | Final-state semantics discourage incremental churn; next-turn-boundary application; stable serialization; description warns the model to batch; per-session change counter surfaced in the Tools panel if tuning is needed |
| Per-template groups couple this plan to the unlanded engine | Inverted: this plan ships only the runtime kwarg; the spec field is a one-line consumer listed in WORKFLOWS-V2's landing notes — no dependency for Sessions 1-5 |
| Project-layer rules from an untrusted repo | Rules are pure dispatch data (regex → builtin strategy); worst case is a wrong projector with the raw still retained; validated with the same fail-soft compile as user rules |
| Silent config drop (four-wiring-points gotcha) | Explicit checklist in §6; schema reachability tests enforce (a); `list[dataclass]` element `_meta` precedent followed for the rule-op fields |

---

## Success Criteria

1. A 200K-char JSON tool result reaches the model as a ~1K-char typed crush carrying a stable `r_<hash>` recovery hint; `tool_result_get(result_id=…, line_start=…, line_end=…)` returns the exact requested lines; running the identical tool twice stores ONE raw file.
2. A subagent whose finding sits at char 40,000 of its result no longer loses it to the 3000-char injection cap: the parent gets a typed digest + raw_ref and recovers the finding with one `tool_result_get` call — verified as-a-user with a deliberately verbose subagent.
3. `.personalclaw/projection_rules.json` in a project repo reroutes that project's custom log format to the `log` projector, beating both the user layer and the sniff; a bad regex in it is skipped + logged and never breaks a tool call.
4. After a month of use, Settings → Tools shows per-model, per-compressor savings from `tokenjuice_savings.json` with `estimated` flags — derived from one JSON file, no telemetry pipeline.
5. The background service compresses a 7-day-idle 500-message session into topic summaries at the consolidation tick: recent topic near-verbatim, old topics folded, all dropped lines recoverable from the archive file the summary cites; an `incognito` session is untouched; disabling `tools.bg_compress_enabled` stops the pass within one tick.
6. Byte-stability holds: projecting the same raw twice yields identical previews/hints (unit-locked), and an at-rest compression never mutates a session with a live in-flight turn.
7. With groups enabled, a background session carries only `core + memory` schemas; asking it to schedule something surfaces the stub, `tool_search("cron")` names the inactive `schedule` group, and one `reset_tools` call activates it — the activation result includes the schedule group's instructions, and the next turn's schema block contains its tools.
8. `reset_tools(groups={})` on a chat session leaves every `always_on` core tool (including `tool_result_get` and `reset_tools` itself) present and every tool still DISPATCHABLE if the model calls it by name — selection ≠ dispatch verified.
9. A tool provider contributed by an installed app appears as its own group with zero app-side code; disabling the app removes the group; an unbound `capability` group never renders.
10. Chat with `groups_enabled` default state shows byte-identical tool schemas to today (no regression until opted in) — locked by a serialization snapshot test.
