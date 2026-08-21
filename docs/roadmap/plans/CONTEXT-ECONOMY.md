# CONTEXT-ECONOMY

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/CE2.md`](../atomic/CE2.md) as 7 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Context Economy — Reversible Compression + Dynamic Tool-Group Activation

**Status:** DONE — all six sessions shipped 2026-07-26/28 (S1 projection core, S2 type-routed
compressors, S3 background compression, S4-S5 tool groups, S6 codebase graph + `code_map`).
Created 2026-07-13 from research synthesis, promoted from backlog.
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

---

## Execution log

- **[2026-08-12][CE2-7] DONE — grammar availability is a capability, and the suite now treats it as one.**
  `tests/test_codegraph.py` asserted `parser_available("python") is True` inside a test named
  `test_parser_available_is_a_question_not_an_assertion`, while `parse.py`'s own docstring says "False is a normal
  answer, not an error". MEASURED cost of that contradiction: on 2026-08-12 PRs **#1144** (2 failures) and **#1162**
  (21 failures) went red in CI, every failure inside this one file, because a runner could not load the python
  grammar — and a manual re-run was the only remedy. 70/70 passed locally the whole time. The grammars are not in the
  wheels: `tree_sitter_language_pack` fetches each into a per-user cache on first use, so a cold cache without network
  legitimately has none.
  **What shipped.** (1) `parser_status()` returns `(language, available, reason)` and `_record_load_failure` logs
  `"<ExceptionType>: <message>"` once per language with the grammar cache path and a fixed pre-fetch remedy — WARNING
  for a language the indexer asks for, DEBUG for anything else. CI previously reported only the absence, so there was
  nothing to diagnose; `parser_available` keeps its `bool` contract. (2) The suite probes the capability once and skips
  the **35** grammar-dependent tests naming the recorded reason; the other **37** keep running, which is the fail-soft
  property this file exists to assert. The floor that never skips is `test_the_parser_dependency_is_installed` — the
  wheels are declared in `pyproject.toml`, so an unimportable package stays a hard red as the packaging regression it
  is. (3) The contradictory test now tests the contract it names.
  **DEVIATION — no parser caching.** The brief offered it as a plausible fix for load pressure. Measured instead: the
  language pack already memoizes the grammar (5.8 ms first load, 0.1 ms for the next 200), so a local cache buys
  microseconds and costs thread-safety — one shared `Parser` driven from two gateway threads is not safe. Not done, on
  evidence.
  **Both postures proven:** 72/72 pass with a working grammar; with `_get_parser` raising, **37 passed / 35 skipped /
  0 failed** — and the probe was reverted with a targeted edit. A leftover `raise` from an earlier interrupted attempt
  was found in the working tree and removed before anything was committed; shipping it would have disabled codegraph
  permanently.

- [2026-07-26][S1] DONE: retrieval hardening + savings accounting + subagent projection (§1, §2.5a). (a) **Content-hash result ids** — `result_store._content_id` = `r_<sha256(raw)[:12]>` replaces the count-based `_next_id`; `store_result` dedupes identical raw to ONE file (idempotent, touches mtime); eviction + never-raise unchanged; `get_result` legacy read-compat is automatic (reads any `r_*.json`). (b) **`fetch_slice` line addressing** — `line_start`/`line_end` (1-indexed inclusive, mutually exclusive with char `start`/`end`); `tool_result_get` schema + handler in `builtin_tools.py` gain both; the `project_and_retain` recovery hint now names all three modes (line/char/grep). (c) **Savings accounting** — `tool_providers/savings.py` aggregated ledger (`~/.personalclaw/tokenjuice_savings.json`, keyed month|model|compressor, bounded by construction, best-effort never-raise) + `GET /api/tools/savings` + a Settings→Tool-output card in `ProjectionRulesPanel.tsx`. Model hint is `"unknown"` at the seam (no resolved model in scope; plan allows it — will cross-ref guardrails token counts later). (d) **Subagent projection** — the blind 3000-char parent-injection cut in `gateway.py` now routes through `project_and_retain` (keyed by the PARENT session), so a buried subagent finding gets a typed digest + a recoverable raw_ref instead of a truncated prefix. Tests: extended `test_tool_result_store.py` (content-hash/idempotency/line-addressing + OP4-analog no-double-loss on the new id), new `test_tool_savings.py`, `test_tools_handler.py` savings-endpoint tests. Success Criteria #1 (id + line ranges + one-file idempotent) + the §2.5a subagent contract met. Gate: `make lint` green, web typecheck+vitest(231)+build+render-smoke green, `make test` green (7986 passed).
- [2026-07-26][S1] DEVIATION: the plan lacks an executor-ready `| ID | Task |` table (it has a §7 prose "Implementation Effort"). Derived Session-1 tasks from §1/§2.5a and the Success Criteria; no scope guessing beyond what those name. Recorded here for auditability.
- [2026-07-26][S1] DEVIATION: regenerated the checked-in agent reference (`src/personalclaw/reference/{index,routes}.md`) via `python -m personalclaw.manifest_reference` — required because the `tool_result_get` schema changed + `/api/tools/savings` was added (`test_agent_reference.py` byte-matches a fresh render). Diff is exactly the new route + route count (442→443).
- [2026-07-26][S1] DISCOVERY (out of scope, deferred): the savings model hint is `"unknown"` because `project_and_retain` is a dispatch-time seam with no resolved model in scope. Threading a real model hint through the tool-dispatch path is a larger change the plan explicitly defers (accounting must never block/slow dispatch); the ledger will cross-reference AUTONOMY-GUARDRAILS' real token counts when that lands rather than duplicating metering. Remaining Sessions 2-6 (type-routed compressors, background compression, tool-groups, codebase graph) are separate clean sub-scopes — not started.
- [2026-07-26][S1] DISCOVERY (pre-existing, not mine): the full suite also surfaces `test_config_loader.py::test_unrecognized_keys_detected` (a Hypothesis property-test bug fixed separately on `bugfix-config-loader-known-top-keys`). Verified CE-S1 is green in combination with that fix (7986 passed / 0 failed). This branch doesn't touch config-loader.
- [2026-07-26][S1] DEVIATION (found in as-a-user validation): the savings feature was undiscoverable from the Settings LANDING bento grid. The Settings landing is built from a SEPARATE registry (`settingsWidgets.tsx`), NOT `SUBPAGES` — so the `tool-output` bento card only surfaced the custom-rules count, and the in-panel savings card `return null`s when empty; a user browsing Settings saw no hint the feature exists. (Recurring miss: a panel ships without its bento widget being updated.) Fixed: the `tool-output` bento card now headlines the savings meter when present (falls back to rule count / builtin-projectors hint so it's never empty) and its `useSearchText` includes savings terms. Validated LIVE on an isolated gateway (:10011, isolated dev home, real :10000 untouched): the card is discoverable + searchable in both empty and populated states; the savings headline matches `/api/tools/savings`.
- [2026-07-26][S2] DONE: type-routed compressors + three-layer rule overlay + rule ops v2 + prose compressor (§2.1-2.4). (a) **JSON crusher** — `_project_json` now folds arrays via `_fold_array`: per-field schema inferred from a bounded 50-item sample (names, types, numeric ranges, null counts), uniform-shape detection, first/last item verbatim; dict values holding big arrays fold per-path. Verified: an 829K-char 20K-item API response projects to 379 chars carrying its raw_ref. Parse failure still falls to head/tail. (b) **`code` content type** — stdlib-`ast` outline for Python (module docstring, imports, class/def signatures with docstring first-lines + `# line N` map, methods indented); regex outliner for other languages; conservative density sniff (`_looks_like_code`: shebang, or ≥3 definition/import markers at ≥5% of lines) so prose never trips it; empty outline → head/tail. `CONTENT_TYPES` grows `"code"`; FE renderer + client sniff mirror + strategy picker updated. (c) **Three-layer overlay** — builtin pack (`tool_providers/rules_builtin.json`, 26 rules: git/pytest/npm/pip/uv/docker/cargo/rustc/make/cc/tsc/eslint/flake8/brew/curl/journal/ISO-timestamp markers; packaged via pyproject package-data), user layer (existing config field), project layer (`.personalclaw/projection_rules.json` in the session cwd, mtime-cached + bounded, bound per-dispatch via a contextvar in `bind_tool_context`); precedence project > user > builtin. (d) **Rule ops v2** — rules gain declarative `head`/`tail`/`keep`/`skip`/`count` ops run by one shared interpreter (`_apply_ops`); validated at the PATCH boundary (compile check, 0..10000 bounds, allowlisted keys) and live-applied; config wiring across all four points (`ProjectionRuleConfig` fields+`_meta`, `load()`, asdict `to_dict`, PATCH validator + FE ops editor using ui primitives). (e) **Prose compressor** — new `tool_providers/prose_compress.py`, background-only (`one_shot_completion(use_case="background")`), bounded-summary contract, deterministic `log`-projector fallback on any failure, raw_ref line survives both paths, savings under `"prose"`; locked OUT of the synchronous projector table by test. Mapped in the Platform-Resilience degraded-lint (`assistant_reasoning` surface — its floor is the built-in deterministic fallback).
- [2026-07-26][S2] DEVIATION (owner-visible design choice): a matched RULE now beats the tool's DECLARED `content_type` in `project_output` (specificity order: rule > declared > sniff). The plan didn't state this ordering, but without it the builtin pack is dead code on the shell path — `run_command` declares everything `"log"`, so `git diff`/pytest output through the shell would never reach the rules. Worst case of any rule stays "wrong projector, raw retained." Locked by tests (`TestBuiltinRulePack`).
- [2026-07-26][S2] DISCOVERY: `test_resilience_degraded_lint.py` ratchets every new `one_shot_completion` call site to a registered degraded surface — `prose_compress.py` tripped it (good ratchet); mapped with a note that its no-model floor is the built-in deterministic fallback rather than a pause.
- [2026-07-26][S2] Validated as-a-user on an isolated gateway (:10012, fresh dev home; real instance untouched): onboarding → Settings → Tool output; `code` strategy visible in the picker; ops editor (head/tail/keep/skip/fold) persists through PATCH (verified in config.json) and live-applies (count-fold verified against the loaded config: 5002 → 3 lines); bad op regex rejected 400 at the boundary; raw recovery via `fetch_slice` grep + line modes on the crushed 829K result. Gate: `make lint` green (504 files), `make test` 8083 passed / 28 skipped / 13 xfailed, web typecheck + vitest (238) + build green. Remaining sessions: S3 (background compression service), S4-5 (tool groups), S6 (codebase graph) — separate clean sub-scopes, not started.
- [2026-07-27][S3] DONE: continuous background compression service (§3, §4). (a) **Shared segmenter** — new `context_segmentation.py` (`segment_messages`, pure, no config/provider graph): embedding-drift boundaries when an `embed_fn` is bound (adjacent user-turn cosine < `DEFAULT_DRIFT_THRESHOLD=0.6` = topic break, mirroring the surfacing/skills gate family), with a **deterministic turn-count fallback** (`DEFAULT_TURNS_PER_SEGMENT=8`) as the DESIGNED no-model tier — also the fallback when embedding yields no boundary on a flat single-topic transcript. Built standalone because LOOP-R13 consumes the SAME primitive (one segmenter, two callers). (b) **The service** — new `bg_compress.py`: `run_bg_compression_pass` (budgeted, `max_sessions=3` oldest/most-idle-first) + `compress_session` (per-session, never-raise). Eligibility: persistent + idle > `bg_compress_idle_days` + transcript ≥ 8K chars; incognito/temporary SKIPPED (the durable `memory_mode` mark from `list_sessions`); active (fresh mtime) skipped (at-rest only → prefix invariant 3). Attention weighting: most-recent segment verbatim, middle → capped request/response (tool rows dropped, their raw_refs already inline), oldest half → `compress_prose` (§2.4) bulk summary carrying a preserved-raw_ref line (OP4 regex lifted from `context_compaction`). Rewrite via `rewrite_session(..., reason="bg_compress")` — which archives dropped lines first (reversibility); savings under `bg_topic`. (c) **`rewrite_session` gained a `reason` kwarg** (default "compact") so the bg pass archives distinctly without double-archiving. (d) **Cadence** — hourly (`_BG_COMPRESS_TICKS=60`), budgeted, off the request path, in `HeartbeatService._beat` → `_run_bg_compression` (resolves the active embedder; deterministic fallback when none). (e) **Config** — `ToolsConfig.bg_compress_enabled` (default True; a feature flag, missing=default) + `bg_compress_idle_days` (default 7.0) wired dataclass+`_meta` → `load()` → asdict `to_dict` → `_EDITABLE_CONFIG` (runtime-editable). Tests: `test_context_segmentation.py` (5), `test_bg_compress.py` (6: shrink+archive-reason, size-floor skip, raw_ref preservation, incognito/active skip, kill-switch, prefix-stability determinism). Success Criteria #5 (topic compress at cadence, incognito untouched, disable stops within a tick) + #6 (byte-stability determinism, at-rest only) met. Gate: `make lint` green, `make test` green (full suite). **Remaining: S4-5 (tool groups), S6 (codebase graph)** — separate clean sub-scopes, not started. NOTE: this session ships no `_EDITABLE_CONFIG` FE control for the two flags (the plan puts the FE surface in S5's config-wiring completion + Tools page) — the flags round-trip and are PATCH-editable now; a Settings toggle lands with S5.
- [2026-07-27][S4] DONE: dynamic tool-group activation core (§5.1-5.4). (a) **The group model** — new `tool_providers/groups.py`: frozen `ToolGroup` (name/display/instructions/always_on/capability/tools) DERIVED one-per-registered-provider (`group_name_for_provider` shortens registry names to model-facing labels: `personalclaw-schedule`→`schedule`, `personalclaw-knowledge-tools`→`knowledge`, `mcp-tools:github`→`mcp:github`); `partition()` puts the always-on `core` group first then first-appearance order (stable ⇒ stable serialization). KEY SAFETY CALL: `group_of_tool` maps every `CORE_LOCKED` name to `core` REGARDLESS of provider — a core-locked tool surfaced by an MCP server must not become deactivatable (verified: `grep` from `mcp-tools:github` → `core`). (b) **Activation lifecycle** — `reset_tools` synthetic meta-tool with agentscope FINAL-STATE semantics (omitted ⇒ deactivated; deltas drift over long sessions), registered in `_META_TOOLS` so it's never approval-gated (it changes what the model SEES, not what it can do), returns the new active set + each NEWLY-activated group's `instructions` (the R12 router-entry shape) + the batch-your-changes warning; unknown groups are named and ignored, not fatal. (c) **The assembly seam** — `start()` split so schema assembly is re-runnable: `providers → disable → unattended strip → GROUP FILTER → serialize`, factored into `_assemble_schema()` + `refresh_toolset()`. Group changes land at the NEXT turn boundary (§3 prefix corollary — verified both in-turn inferences see the identical block). (d) **Retrieval composes, not competes** — `ToolRetriever.select()` gained a `restrict` kwarg so the top-K budget is spent WITHIN active groups, while `search()` still ranks the FULL catalog. (e) **The fail-open triad, each test-locked**: dispatch index NEVER filtered (an inactive group's tool still runs — validated live: `artifact_list` dispatched while `artifacts` was inactive); inactive groups leave a one-line STUB naming the activation call; `tool_search` annotates a hit in an inactive group with `[in INACTIVE group 'x' — still callable by name, or reset_tools(...)]`. (f) **Per-surface defaults** — keyed off the model AXIS already threaded through resolution (`surface=inner_axis` in `_build_native_runtime`): background/orchestration → `core+memory`, loops → `core+workflows+subagents`, chat → NO entry = all active. Plus the `tool_groups` kwarg (the WORKFLOWS-V2 stage-spawn seam) which wins over the surface default. (g) **Config** — `groups_enabled` (default **False**) + `group_defaults` (dict[str, list[str]], `"*"` = all) wired dataclass+`_meta` → `load()` → `to_dict` → `_EDITABLE_CONFIG` (`groups_enabled` runtime-editable; PATCH round-trip verified persisted). (h) `GET /api/tools` now reports each tool's `group` (read-only; activation is per-session runtime state, not a pref) + FE `ToolItem.group`. **Success Criterion #10 LOCKED by test**: with grouping off, the tools kwarg is `json.dumps`-identical to `_tool_schema` with no reset_tools and no injected system note — enabling the feature is a literal no-op for interactive chat. 22 tests in `test_tool_groups.py`. **MEASURED on the real 69-tool bundled surface** (isolated dev home :10022): full schema 48,951 chars/turn → background surface 21,428 chars **= 56% smaller, ~6,880 tokens saved per turn**; loops ~49% smaller. Gate: `make lint` green, `make test` 8235 passed, web typecheck + 251 vitest + build green. **Remaining: S5 (declaration surfaces — per-capability gating probe, Tools-page group chips FE, validation sweep with real MCP servers), S6 (codebase graph).** NOTE: §5.5 per-capability gating ships only as the `ToolGroup.capability` FIELD here (no probe yet) — S5 owns `can_resolve_use_case`; and the ACP aggregated surface stays full-set per the plan's explicit v1 non-goal.
- [2026-07-28][S5] DONE: declaration surfaces + the product surface (§5.4-5.5, FE). (a) **Per-capability gating (§5.5)** — `_GROUP_CAPABILITY` declares the capability a group's tools NEED; `capability_available()` supports two cheap probe kinds (`model:<use_case>` via the no-instantiate `provider_bridge.can_resolve_use_case` — the SAME probe behind the onboarding needs-a-model nudge, so the two never disagree — and `tool_provider:`/`search_provider:` registry presence) and `offerable()` gates a group. Deliberately SPARSE: only `subagents` (`model:orchestration`) ships gated, because subagent tools inference through a ModelProvider and fail at the first turn with no model; `memory` is deliberately NOT gated (its lesson store works without an embedder — recall degrades, it doesn't break). Fail-OPEN on every uncertainty (unknown probe kind / probe error / empty declaration → available) and `always_on` is never gated. A non-offerable group is neither active NOR stub-listed (the model never sees tools that can't work), and `reset_tools` REFUSES it with a named reason ("Unavailable in this install") rather than silently returning an unchanged set, and omits it from the "Inactive:" list (which would imply activatable). (b) **`GET /api/tools/groups`** — the derived partition (name/display/alwaysOn/toolCount/tools/capability/offerable/instructions) + `enabled` + per-surface defaults. READ-ONLY by design and documented as such: activation is per-session RUNTIME state (seeded from defaults, changed by the agent's `reset_tools`), so a persisted per-group toggle would imply a durability the mechanism doesn't have. What IS configurable is the flag + `group_defaults`. Degrades on a broken registry rather than 500ing. (c) **FE** — new `ToolGroupsTile` on the Tools page (browse view only): the partition as chips with tool counts + always-on marker, the feature-flag switch (the one genuinely persistable control), a "not available in this install" line for gated groups, and a per-surface "what each surface starts with" panel. Each provider block gained a `group: <name>` badge (the page ALREADY groups by provider, which IS the group grain) shown only when grouping is on. `ToolItem.group` + `api.toolGroups()`/`setToolGroupsEnabled()`. 13 new tests (5 gating in `test_tool_groups.py` → 28 total; 2 endpoint in `test_tools_handler.py`). **PERF REGRESSION FOUND AND FIXED DURING THE GATE** (the important discovery): probing offerability unconditionally in `_assemble_schema` made the full suite **276s vs main's 39s (~7x)** and pushed two `test_subagent.py` timeouts over their 120s cap — the probes read config + walk the provider registry, and `start()` runs on every runtime construction including every test's. Fixed by probing ONLY when grouping is in effect (`_active_groups is not None`) AND only for groups that actually declare a capability (usually zero probes). Suite back to **8243 passed in 41.5s**. NOTE: this also means the 2 `test_subagent.py::TestOnDoneTimeout` failures were MINE, not the pre-existing xdist flake they resembled — a same-suite baseline run on untouched main (8236 passed, 39s, green) is what proved it. (d) **FE taste fix from as-a-user validation**: the surface-defaults panel listed `subagents` for Autonomous runs while the tile simultaneously said Subagents was unavailable — a visible self-contradiction; unavailable names now render struck-through with an explanatory title. Gate: `make lint` green, `make test` **8243 passed**, web typecheck + 251 vitest + build green; primitive-adoption ratchet tripped on a raw `<button>` wrapper and was fixed by ADOPTION (`Toggle` already owns the switch role + `onChange` — no wrapping control), never a baseline bump. Validated as-a-user on :10023 (fresh dev home): endpoint partitions the real 66-tool/9-group surface, `subagents` correctly HIDDEN with no model bound, flag PATCH round-trips + persists to config.json, the switch flips live (copy + defaults panel update, badges appear/disappear), zero console errors, clean gateway log. **Remaining: S6 (codebase graph) only.** §5.4's per-template `tool_groups` kwarg shipped in S4 and stays the documented WORKFLOWS-V2 consumer seam; the ACP aggregated surface remains full-set per the plan's explicit v1 non-goal.
- [2026-07-28][S6] DONE — the codebase graph (§5.5), which completes this plan. (a) **New `codegraph/` package**: `parse.py` (tree-sitter extraction — definitions with owner/kind/signature/line-span, import edges, call-site references) and `index.py` (per-workspace SQLite at `<home>/codegraph/<workspace-key>.db`, mtime+size invalidation, atomic per-file delete-then-insert, wall-clock AND file-count budgets). Node-type tables were read off the real grammars, not guessed — Rust has both `function_item` and `function_signature_item`, Go separates `method_declaration` from `function_declaration`, and a function inside a `class`/`impl` body is recorded as a METHOD with its owner attached. `workspace_key` follows `loop/worktree.py`'s sha1[:12] precedent (a filename only code resolves — no need for the readable-slug style, and a hash can't blow the name-length limit). (b) **`code_map` + `code_map_overview` tools** in a new `code_map.py` provider. Registered as **`workflows-tools`** so `group_name_for_provider` derives `workflows` per §5.1 while keeping a DISTINCT registry key — `personalclaw-workflows` is already taken and `_providers` is a dict keyed by provider name, so reusing it would have silently replaced the workflows provider (test-locked). Bundled app manifest at `apps/native/personalclaw-code-map/`. (c) **Consumers, all three**: the tools; the SDLC planning brief (`code_plan_briefs._code_map_block`, budget-capped at 8,000 chars ≈ 2K tokens with a visible truncation marker); and `@`-mention ranking (`files._apply_centrality`, applied to BOTH the FileIndex fast path and the walk fallback). (d) **Dependency**: `tree-sitter` + `tree-sitter-language-pack` as CORE deps per the owner's 2026-07-28 decision (not an extra) — an accelerator half the installs lack is one nobody can rely on; the language pack ships prebuilt wheels so there's no compiler requirement.

  **MEASURED on this repo** (1,467 files): full index 3.9s / 20,737 definitions / 213K references — well inside the plan's ~30s budget; the incremental second pass is **37ms** (stat-per-file). One `code_map` call returns a symbol's definition site, signature and every referring file, replacing the grep→read→grep sequence it stands in for.

  **A REAL BUG FOUND BY RUNNING IT ON THE REAL REPO — centrality measured nothing.** The first implementation counted raw reference rows per defining file, which ranked `tests/test_native_runtime.py` first with 8,562 "inbound refs" and filled the planning summary with test scaffolding (`_ScriptedModel.complete`, `_Tool.name`). Raw counts multiply a name's AMBIGUITY by its popularity: a file defining a generic `name` method outscored every actual hub. Fixed with two corrections — count DISTINCT referring files, and weight each name by `1/(files that define it)` so a name defined in forty files contributes almost nothing while a uniquely-named symbol is strong evidence. The same repo now ranks `schedule.py`, `memory_vault.py`, `acp_agent.py`, `session.py` — a genuinely useful architecture sketch. Locked by `test_centrality_prefers_widely_referenced_files`.

  **Fail-soft verified by simulation, not assertion**: with `tree_sitter*` imports forced to fail (a stripped install / the desktop bundle without the wheels), `parser_available` reports False, parsing yields zero definitions, indexing completes without raising, `code_map` still answers and NAMES grep/read as the way through, the planning block is empty, and the planner brief builds unchanged. Also covered: syntax errors, unreadable files, >1MB files (generated, not authored), unknown suffixes, missing workspaces, and both budget caps — where a PARTIAL pass deliberately does NOT prune "missing" files, since a truncated walk hasn't seen the whole tree and would delete live rows.

  **Drift guards caught two real omissions during the gate** (neither a flake): `test_api_manifest_drift` required `TOOL_META` entries with response types + examples for both new tools, and `test_agent_reference` required regenerating the offline reference. Both added. Also wired `code_map_block` into the `task-code_design_brief` prompt TEMPLATE + its `BundledPrompt` variable declaration — the rendered template wins over the inline fallback, so adding the block to only the fallback would have been dead code (caught by driving `build_design_brief` and finding the map absent).

  Tests: `tests/test_codegraph.py`, 70 cases. Gate: `make lint` green (mypy 528 files), `make test` **8575 passed** in 40.4s excluding the new file (which costs 3.4s alone — an apparent 59s full-suite reading was contention from a concurrently-running validation gateway, verified by isolating). Validated as-a-user on :10033: both tools appear in `GET /api/tools` with `provider=workflows-tools group=workflows approval=False`, `/api/tools/groups` places them in Workflows, `code_map_overview` returns the real repo's shape, `@`-mention search returns centrality-ranked results, zero errors in the gateway log. **This session completes CONTEXT-ECONOMY — all six sessions are now DONE.**

## Execution log — CE2-8 (a declared headroom contract at the assembly seam)

- **[2026-08-21][CE2-8] DONE.** "Will this prompt fit?" now has one answer computed in one
  place, in three closed states, before the provider call.

  `src/personalclaw/context_headroom.py` declares `HeadroomState(str, Enum)` — `FITS` /
  `FITS_AFTER_COMPRESSION` / `CANNOT_FIT` — and decides with `check(components, *, window)`
  (pure, sync); `check_for_model(..., model_ref=)` resolves the window first. `Headroom.text` is
  `""` on `CANNOT_FIT`, so a refusal is one `if` away from **nothing** rather than one `if` away
  from sending the thing it just refused.

- **The seam acts on it** in `context_engine.py` (`check_headroom`, `headroom_components`) and
  `dashboard/chat_runner.py:2179-2214`, before the provider call: `CANNOT_FIT` → error card +
  `_last_turn_errored = True` + `return` (the `finally` still runs every finalizer, matching the
  existing `return`-after-`_emit_error` precedent at `:3572`); `FITS_AFTER_COMPRESSION` → send
  `verdict.text` and recompute `injected_chars`; any state → broadcast `notice()` when non-empty.

- **Naming which block is too big required making the assembly nameable.** `build_message` now
  assembles through a `_Parts` collector (`context.py:696-750`) filling a
  `components_out: list[Component]` — 17 labelled pieces (`system prompt`,
  `session context (memory · lessons · history)`, `episodic memory`, `skill: <name>`,
  `hook context`, `the user's request`, …) with `compressible=False` on the pieces that must be
  refused rather than trimmed.

- **The reserve is `output_budget`'s number, not a second one.** `resolve_window` calls
  `local_models.budgets.model_budget(ref)` once and takes `.output_tokens` — the value
  `output_budget` (`budgets.py:181-187`) is the narrow accessor of, and the same number
  `llm_helpers.py:498` puts in `max_tokens`. One catalog lookup serves window + reserve +
  source; calling `output_budget` separately would hit `list_models()` twice per turn.
  `test_the_reserve_is_output_budgets_number_not_a_second_one` asserts
  `resolve_window(ref).output_reserve_tokens == await output_budget(ref)` across three ref
  shapes, so the shortcut cannot drift into a second reserve. The bound is
  `Window.input_tokens`, carried from `ContextBudget` and never recomputed.

- **UNKNOWN is a property of the evidence, not a fourth state.** `model_context_window` answers
  *every* query with a hardcoded 200k, so `resolve_window` re-asks with `default=0` to tell "the
  table named this model" from "the table defaulted". Unmeasured yields `FITS` with
  `pressure is None` and `level == "unmeasured"`: refusing would make a mistyped model id an
  outage, and claiming headroom would restore the silent failure this atom exists to remove. The
  state set stays closed at three, asserted. Same `None`-vs-`0` discipline as
  `local_models/fit.py`. Logged once per ref, not per turn.

- **No `web/` change was needed, and the reason was verified rather than assumed.**
  `ChatPage.tsx:935` drops only `status`/`session`; `:3539-3541` ledgers only
  `context`/`learned`/`stats`; `:3563` + `:3685` render any other `activityKind` inline through
  `ActivityLine` (kind-agnostic, design-system tokens, text as its own accessible name, no
  colour-only state); `chatTypes.ts:62` types `activityKind?: string`. So compression and
  pressure go out as `activity_event {kind:"headroom"}` — an inline line in the turn flow at the
  moment it happens — and the refusal uses the existing error card. Live-only, not persisted;
  recorded as a tradeoff.

- **A silent drop closed on the way past.** `build_session_context`'s `_MAX_CONTEXT_CHARS` cut
  previously reached only `logger.warning`; it now reports through `dropped_out`/`notices_out`
  into the same notice channel.

- **No knob added, deliberately.** `PRESSURE_WARN_FRACTION = 0.75` and
  `PRESSURE_CRITICAL_FRACTION = 0.9` are named module constants with the reason recorded: the
  cheap remedies need a turn or two of room, so warning at 0.95 warns after the room to act is
  gone. The atom does not ask for a knob, so no config baseline regeneration was needed.

- **Falsifications.** Live lines mutated, restored from file copies, each restore verified with
  an empty `git diff HEAD --stat` before proceeding.
  1. `limit = window.input_tokens` → `window.tokens` (drop the reserve) → 5 failed / 19 passed,
     including `test_a_prompt_that_fills_the_window_exactly_does_not_fit`.
  2. `notice()`'s `FITS_AFTER_COMPRESSION` branch → `return ""` → `assert 'hook context' in ''`.
  3. `model_context_window(ref, default=0) > 0` → `True` (adopt the 200k default) →
     `assert 'window-table' == 'unknown'`. **Re-run independently before this PR opened:** 1
     failed / 23 passed with the mutation, clean without it.
  4. `build_message` returns `parts.text() + "[UNLABELLED BLOCK]"` → the join guard reds.
  - **One falsification came back vacuous and taught something real.** Making `_Parts.add`
    *skip* a piece stayed green, because `_Parts` is the single source of both the joined text
    and the labels — a skipped piece leaves neither and the join still balances. The guard only
    catches text reaching `message` from *outside* the labelled set (the recall/push prepends).
    That limit is now documented in `headroom_components`' docstring rather than left as an
    over-trusted rail.

- **Gates.** `make lint` clean (mypy 945 files). Targeted **281 passed, 1 xfailed** across
  headroom + context + context_engine + learning-ambient/surfacing + local-model-budgets +
  config-roundtrip + thread-context + context-management + mem-adaptive-budget +
  inert-surface-baseline, every path existence-checked and iterated as a quoted zsh array; an
  independent re-run of a six-file subset gave 135 passed, 1 xfailed. Full `make test`
  **23489 passed, 30 skipped, 12 xfailed, 0 failed** in 7m41s, real-home rail clean on every
  run. No `web/` change → no web gate and no `consistency-audit.json` drift.

- **One clause scoped rather than satisfied.** The clause's "which oversized **tool result**" is
  representable and tested at the contract level
  (`test_a_refusal_names_the_specific_oversized_component` uses a `tool result: run_command`
  component), but a *mid-turn* tool result never passes through the assembly seam — native
  history carries it and a follow-up assembly injects almost nothing, so it is still bounded
  where it is produced, by `project_output` at dispatch. Routing the mid-turn dispatch seam
  through this contract is a wiring change rather than a redesign; it is declared out of scope in
  the module docstring instead of being left to look like an oversight. Also left alone:
  `_MAX_CONTEXT_CHARS` itself, now a second model-blind cap beside this contract — a clean-break
  deletion candidate, but broader behaviour than this atom can validate.

- **Roadmap bookkeeping — no `dag.json` row to flip.** `main`'s
  `docs/roadmap/atomic/dag.json` carries **CE2-1…CE2-7** for CONTEXT-ECONOMY; CE2-8 arrived with
  the rev-18 capability-gap set, still on an unmerged branch. No row was invented — a mirrored
  status surface must not be flipped without the row it mirrors. This entry is the record until
  that set lands. (The pre-existing "This session completes CONTEXT-ECONOMY — all six sessions
  are now DONE" line above refers to the plan's original six sessions, not to the rev-18
  amendment atoms CE2-8/9/10.)

## Execution log — CE2-9 (skill bodies allocate on the one budget)

- **[2026-08-21][CE2-9] DONE.** A loaded skill can no longer crowd out the conversation, because
  skill bodies stopped being concatenated ahead of the budget and became candidates inside it.

- **Where the bodies were.** `context.py:1562-1565` (forced, goal-loop `skill_ids`) and
  `context.py:1611-1615` (surfaced) each did
  `parts.add(f"[Skill: {name}]\n{stripped}\n[End of skill]\n\n")` with nothing measuring the
  result. The allocator already declared a `skills` slot (`learning/surfacing.py:165`) and had
  never seen a body — only the skill *index*, via `learning/ambient.sources_for(skill_index=…)`.
  Both sites now GATHER `SkillRequest`s and hand them to one `allocate_skills(...)` call, whose
  blocks land in the same `skills` slot at priority 3, non-sacrificial (so an oversized item
  skips rather than truncates — the slot policy already said the right thing).

- **Two caps, one mechanism — enforced inside `allocate`, not beside it.** `Candidate.max_tokens`
  is the candidate's own declared ceiling, and `surfacing._tier_fits(cost, used, budget, cap)` is
  the single test both bounds go through, feeding the SAME degrade ladder. A per-skill pre-filter
  next to the budget would have been the two-budget defect this atom exists to delete.
  `test_the_per_skill_cap_binds_even_with_the_whole_budget_free` drives a 500,000-token budget so
  only the declared cap can explain the reduction.

- **The tier is declared, and the key is NOT called `resource_tier` — DEVIATION, recorded.** The
  atom's wording is "resource tier", but `resources:` frontmatter is *already* "the skill RESOURCE
  tier" (WF2LEA-10, `loader.py`'s own section header, `tests/test_skill_resource_tier.py`, and
  `docs/reference/skill-format.md`'s interoperability table). Shipping a second meaning of the
  phrase in the same frontmatter block is a coherence defect, so the key is **`context_tier`**
  with the concept still named "resource tier" in prose. Values and caps, MEASURED against the
  17 bundled skills (median 1,183 tokens, largest 4,202, total 23,025): `light` 1,000,
  `standard` 3,000 (the default — clears 16 of 17), `heavy` 8,000. Unknown or absent → `standard`,
  logged: a frontmatter typo must not silently shrink a skill. **Aggregate 16,000** — 8 is the
  progressive-disclosure threshold, so 8 bodies is the most a turn ever carries; at the median all
  8 cost 9,464 and nothing reduces, while at the `standard` cap they would want 24,000. So the
  aggregate binds exactly when several skills sit near their own ceilings, and never otherwise.
  Deliberately model-BLIND: CE2-8 already measures the assembled prompt against the real window,
  and re-deriving that here would be the second budget again.

- **`visual-output` now declares `context_tier: heavy`,** because the census found it is the one
  bundled skill (4,093 tokens by `count_tokens`) over the default cap — discovered by measuring
  the shipped library rather than by trusting the number.
  `test_every_bundled_skill_fits_the_cap_its_declared_tier_grants` is the ratchet, with a
  ≥15-file vacuity floor so an empty glob cannot read as a pass.

- **REDUCED = the DECLARED summary, and the classification reads the TEXT not the tier.**
  `reduced_block` renders `description` + the `resources:` entry points + the `skill_invoke` call,
  complete. Nothing slices a body. Classification keys off the string that really reached the
  prompt, because `Candidate.text` falls back down the chain (`l1 or l0`): a skill with no
  declared summary is *labelled* tier L1 while rendering the L0 pointer, so trusting the tier
  label would have reported a REDUCED load of content that is not there.

- **No declared summary ⇒ REFUSED, and that is a decision not an omission.** There is nothing to
  reduce *to*, and manufacturing a summary from the body's first N characters is exactly the
  byte-boundary cut the clause forbids. What loads is a one-line pointer naming the skill and
  `skill_invoke{name}` — reported REFUSED because no part of the skill's content reached the
  prompt.

- **Continued cost is re-evaluated at `build_message`, which runs EVERY turn.** Verified rather
  than assumed: `chat_runner.py:2041` is `elif state.context_builder:`, not an `is_new` branch, and
  the skill block sits at method-body indent outside `build_message`'s `if is_new_session:`. Nothing
  caches an admission. `test_a_skill_admitted_on_one_turn_is_re_fitted_on_the_next` admits a
  midsize body on turn 1, shrinks only the room, and gets REDUCED on turn 2 from the same file.

- **Visible through CE2-8's channel, not a new one.** Per-skill notices go out via
  `notices_out` → `AssembledContext.notices` → `chat_runner:2218` →
  `activity_event {kind:"headroom"}` → `ActivityLine`. The structured triple rides
  `AssembledContext.metadata["skill_decisions"]`, and `logger.info` reports
  `N admitted, N reduced, N refused — used/budget` every turn a skill was considered — including
  the all-admitted turn, since a report that only appears on a problem cannot answer "did it
  load?". `SkillAllocation.counts` keeps all three keys present at zero for the same reason. **No
  `web/` change**, and the reason was checked rather than assumed: CE2-8 verified `ActivityLine`
  renders any unknown `activityKind` inline, so a new kind was unnecessary and a new component
  would have been a second channel.

- **A REAL MEASUREMENT CHANGED THE DESIGN TWICE.**
  1. **Rank decay let load order beat priority one layer down.** With a confirmed (1.0) skill
     passed *after* a surfaced (0.9) one, `fuse` stamps `source_rank` by list position and
     `score_candidate` decays 0.85 per rank — measured salience **0.405 for the rank-0 guess vs
     0.383 for the rank-1 confirmation**. The guess won. `allocate_skills` now sorts by declared
     score before the pool sees anything, making rank decay a tie-breaker among equal declarations
     instead of a second ranking. Found by a test that deliberately passed them in the wrong
     order; `test_a_forced_skill_outranks_a_surfaced_one_at_the_same_overlap` is the rail.
  2. **A catalogue block would have shipped INERT, and the proof is arithmetic.** The first cut
     emitted the allocator's near-miss catalogue as its own component. It can never render for a
     skill: the catalogue is only appended when `used + tokens(catalogue) <= budget` while the
     item's own L0 already failed `used + tokens(l0) <= budget`, and the catalogue *is* L0 plus a
     header — measured 57 tokens against 43 for the line it would carry. Both conditions cannot
     hold. The block and the `Allocation.catalogue` field it needed were deleted rather than
     shipped as a control nothing can reach; the per-skill pointer lands where the skill would
     have been and says more.

- **Two allocator changes beyond the cap, both with a reason.** `FULL_BODY_KINDS = {"skill"}` —
  L2 is the default for a skill instead of a grant, because `L2_MAX_ITEMS=3` would have made a
  perfectly affordable fourth skill load reduced with budget to spare (and it does not consume the
  grant budget, so three skills cannot starve a lesson's L2). `UNCAPPED_KINDS` gained `"skill"` —
  `MAX_PER_SOURCE=3` runs inside `fuse`, BEFORE the allocator can catalogue a near-miss, so a
  quota there is a drop nobody can see; skills are bounded by their declared cap and the
  aggregate, which are reported. `test_learning_surfacing.py`'s two diversification tests used
  `skill` as their capped EXAMPLE and were re-pointed at `memory` (still capped) with the
  exemption pinned by name — the policy change is explicit, not absorbed.
  Also fixed on the way: the degrade ladder treated an **empty rendering as a fit** (cost 0), which
  would have added a blank block reading as a loaded item. Guarded, and
  `test_a_tier_that_renders_empty_is_not_a_fit` reds without it.

- **MEASURED end to end** (`build_message`, isolated home): a skill with a **42,458-token** body
  against the shipped caps loaded REDUCED at **89 tokens**; the whole assembled prompt came to
  **307 tokens** with the user's request intact, and neither the body's first line nor its last
  reached the prompt.

- **Falsifications** — each mutation applied to the LIVE line, `grep`-confirmed present before the
  run, restored from a `cp` file copy (never `git checkout --`), with `git status --porcelain`
  empty afterwards.
  1. **Restored the pre-CE2-9 concatenation** in `context.py` (bodies pasted straight into
     `parts`) → `test_an_oversized_skill_loads_reduced_and_the_conversation_survives` RED:
     `'Step 2000. Do the 2000th thing…' is contained here:` — the full 42k body back in the
     prompt, the conversation crowded out. 1 failed / 1 passed.
  2. **Replaced reduction with a byte-boundary cut** (`l1=full_block(...)[: cap * 4]`) →
     `test_a_reduced_skill_carries_its_declared_summary_not_a_slice_of_its_body` RED, and the
     failure output shows the exact pathology the clause names — the block ends mid-sentence at
     `Step 146.`.
  3. **Made the reduction silent** (dropped the `notices_out.extend`) →
     `test_the_notice_names_the_skill_and_the_reason` RED (`assert 0 == 1`) and
     `test_the_decisions_ride_out_in_the_assembled_metadata` RED on the notice assertion.
  4. **Collapsed three states to two** (`REDUCED` → `REFUSED` at the classification site) →
     `test_one_turn_can_produce_all_three_states_and_they_are_distinct` RED and
     `test_the_per_turn_report_counts_every_state_including_the_empty_ones` RED
     (`{'reduced': 0} != {'reduced': 1}`).
  - Residue sweep after restoring: `grep -rn "FALSIFICATION\|if False and\|# PROBE\|MUTANT"` = 13
    (the pre-existing benign count), tree clean.

- **Gates.** `make lint` clean (mypy 952 files). Targeted **413 passed, 1 xfailed** across the new
  suite + context / context-headroom / context-engine / learning-surfacing / learning-ambient /
  skills / skill-resource-tier / skill-progressive-disclosure / skill-format-compat /
  bundled-skills-catalog / skill-surfacing / skill-usage / skill-agent-local-tier /
  thread-context / inert-surface-baseline / config-roundtrip, every path existence-checked as a
  quoted zsh array; real-home rail reported clean. Full `make test` **23,717 passed, 30 skipped,
  12 xfailed, 0 failed** in 8m49s, real-home rail clean on that run too
  (`/Users/golani/.personalclaw unchanged by this run.`). No `web/` change → no web gate and no
  `consistency-audit.json` drift. No config field added, so no baseline regeneration.

- **Clauses scoped rather than satisfied.** (a) The **progressive-disclosure index path** (above 8
  matched skills) still bypasses the allocator, and correctly: it injects no bodies, so there is
  nothing to allocate — but it means the index block itself is unbudgeted, bounded only by the
  match count. Left as is; budgeting a name-and-description list is a different, smaller problem.
  (b) `skill_invoke`'s on-demand body is **not** allocated: it is a tool RESULT, bounded by
  `project_output` at dispatch, which is the same seam CE2-8 declared out of its own scope.
  So the agent can still pull a full body past this budget deliberately, which is the point of an
  explicit call — but it is not a second silent path, and saying so is better than implying the
  budget covers it.

- **Roadmap bookkeeping — still no `dag.json` row.** `origin/main`'s
  `docs/roadmap/atomic/dag.json` carries CE2-1…CE2-7 only; CE2-8/9/10 arrived with the rev-18
  capability-gap set, which has not landed. No row invented, same as CE2-8's entry — a mirrored
  status surface must not be flipped without the row it mirrors. This entry is the record.
