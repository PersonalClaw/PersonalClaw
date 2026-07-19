# Plan: Knowledge Artifact Synthesis via Workflow Nodes

**Status:** PROPOSED (rev 2 — research-integrated 2026-07-12)  
**Created:** 2026-07-11  
**Revised:** 2026-07-12 (19 approved research recommendations folded in; memory/knowledge boundary made explicit)  
**Depends on:** WORKFLOWS-V2.md (Slices 0-2), existing Knowledge store (`knowledge/store.py`)  
**Scope:** How workflows produce, consume, incrementally update, and MAINTAIN knowledge

---

## Research Integration (2026-07-12)

Approved recommendation IDs folded into this revision (one line each; landing section in parentheses):

- **KNOW-R1** — structured, idempotent, provenance-carrying `knowledge_persist` contract (§2.1)
- **KNOW-R2** — compiled-truth + append-only timeline as THE incremental-update shape (§3.1)
- **KNOW-R3** — item-identity contract + persistent seen-set + delta rule for `until_cancelled` (§4.1)
- **KNOW-R4** — bounded-context, diff-aware continuation semantics for long runs (§4.2)
- **KNOW-R5** — synthesis lineage caps + consolidation mechanics for the periodic synthesizer (§4.4; memory-side consolidation modes re-pointed to LEARNING-FLYWHEEL)
- **KNOW-R6** — contradiction flagging at persist time + typed item relations with edge provenance (§3.2)
- **KNOW-R7** — cost-tiered maintenance templates: knowledge-health / knowledge-lint / gap-healing (§3.4; proposal routing via the LEARNING-FLYWHEEL queue)
- **KNOW-R8** — `knowledge_retrieve` upgrades: tiered modes, detail budgets, evidence contract, freshness, graph expansion (§2.2)
- **KNOW-R9** — buffer-seal (volume-driven) synthesis trigger alongside wall-clock cadence (§4.3)
- **KNOW-R10** — retrieval provenance journaling, citation enforcement, untrusted-content fencing (§5.2)
- **KNOW-R11** — real-content bundled template slate (§7)
- **KNOW-R12** — push-based retrieval: project Session Brief for workflow RUNS, `read_when` triggers, coverage-gap events (§5.3)
- **KNOW-R13** — human-gold provenance weighting + decision-shaped knowledge at gate events (§2.1, §4.4; memory-decay weighting re-pointed to LEARNING-FLYWHEEL)
- **KNOW-R14** — `{{siblings.*}}` share-filter and convergence guard (§4.2)
- **KNOW-R15** — `render_report` action provider (§6.2; deliberately last-slice / deferrable)
- **KNOW-R16** — per-store `schema.md` conventions contract (§3.3; schema-edit proposals route via the LEARNING-FLYWHEEL queue)
- **KNOW-R17** — zero-model heuristic extraction floor (§2.3)
- **KNOW-R18** — classifier-then-dispatch multi-lens rich-ingest template (§7.2; user-modeling lenses re-pointed to the memory subsystem / LEARNING-FLYWHEEL)
- **KNOW-R19** — `expires_at`/TTL field on `knowledge_persist` (§2.1); the environmental context-probe template family is re-pointed to WORKFLOWS-V2-AUTOMATION-SUBSTRATE (harness state is not knowledge)

---

## Boundary: KNOWLEDGE vs MEMORY vs ARTIFACTS (read first)

This plan was flagged for conflating memory and knowledge. Rev 2 draws the line where the code draws it (verified against `src/personalclaw/` 2026-07-12):

**KNOWLEDGE = the user's personal items.** Documents, files, photos, notes, bookmarks, journals — one GLOBAL library in `~/.personalclaw/workspace/knowledge/knowledge.db` (`knowledge/store.py`, one item = one logical document; the `namespace` column was deliberately dropped — there is NO per-project partitioning). Future knowledge PROVIDERS may be Google Drive, Google Photos, Notion, etc. The seam for that exists — `knowledge_providers/base.py` `KnowledgeProvider` ABC (async) + object-keyed registry + the `provider` attribution column on items — but today retrieval bypasses it entirely (`registry.search_all()` has zero production callers; the real path is `knowledge/retrieval.py` `HybridRetriever` directly). **Making that seam real for retrieval is in-scope here** (§2.2).

**MEMORY = the harness's own internal mechanics.** What the agent knows about the user and its work: semantic facts, preference facets, episodic records, procedural priors, lessons, persona, commitments — `~/.personalclaw/memory.db` (`vector_memory.py`, `memory_service.py`), cwd-PARTITIONED (unlike knowledge's one global library), with its own sync `MemoryProvider` ABC, string-keyed registry, normalized embedding blobs, and its own decay/promotion/consolidation machinery. **Memory is entirely OUT of scope for this plan.** Anything in the approved research that is really about memory — lessons, facets, decay weighting, episodic→semantic promotion, user-modeling extraction — is re-pointed to **WORKFLOWS-V2-LEARNING-FLYWHEEL.md** with an explicit cross-reference at the point of re-direction. This plan never writes to memory.db and never introduces a memory↔knowledge cross-link (none exists in code today, deliberately).

**ARTIFACTS = a third, separate registry.** `artifacts/registry.py` holds named, VERSIONED, LLM-generated content (widget/html/markdown/svg) under `~/.personalclaw/artifacts/`. It has no structural relationship to knowledge today — the `also_artifact` link proposed in rev 1 **does not exist**; this plan specifies it as an explicit dual-write (§2.1, field `also_artifact`), i.e., two registry calls with metadata back-references, not a shared record. (Beware the naming collision: `knowledge/pipeline/runner.py:_cleanup_orphaned_artifacts` refers to derived media FILES, not the artifacts registry.)

Consequences enforced throughout this plan:
1. `knowledge_persist` / `knowledge_retrieve` are **knowledge-store action providers ONLY**. They read/write knowledge.db via the store + the ONE ingest queue. No memory writes, ever.
2. Knowledge is **never ambiently injected into chat** (existing deliberate invariant — it enters chat only via the composer @-picker or agent `knowledge_search` tool). §5.3's Session Brief injects into **workflow runs**, not chat context, preserving this.
3. Knowledge embeddings are raw un-normalized f32 (`knowledge/embedder.py`); memory's are L2-normalized. The stores share exactly one seam — the active embedding binding (`embedding_providers/registry.get_active_embed_fn()`) — and nothing else.

---

## Overview

Knowledge artifact synthesis evolves from a terminal side-effect of loop completion into a **first-class composable pattern** within Workflows v2. No new node types are required — synthesis is expressed as a three-node sequence (accumulate → synthesize → persist) using existing node kinds plus new **action providers** (`knowledge_persist`, `knowledge_retrieve`, and an optional deferred `render_report`). Long-running workflows update knowledge incrementally with bounded cost, and workflows consume existing knowledge as input.

Rev 2 adds the two halves rev 1 was missing:

1. **Store semantics** (§3): structured, idempotent, provenance-carrying writes (typed kinds + claims, deterministic identity, compiled-truth + append-only timeline, typed relations, contradiction flagging) — a correctness requirement under the engine's retry/resume/rewind, since a naively re-executed persist node must be a harmless no-op.
2. **Long-run mechanics** (§4): item identity + persistent seen-sets, bounded diff-aware synthesis windows, volume-driven triggers, and lineage-capped consolidation — the difference between the monitoring template demoing once and running for months.

It also adds the missing **maintenance lifecycle** (§3.4): the plan previously only created knowledge; nothing maintained it.

---

## 1. The Synthesis Pattern: Three Existing Node Types

The canonical knowledge-synthesis pattern composes from standard nodes:

```yaml
# Accumulate (zero-token, reshapes prior stage outputs)
- id: accumulate
  kind: transform
  expr: "{{nodes.research.output | flatten | filter('verdict','confirmed')}}"

# Synthesize (LLM stage, produces structured article)
- id: synthesize
  kind: stage
  label: "Synthesize knowledge article"
  prompt: |
    Synthesize the accumulated findings into a structured knowledge article.
    Findings: {{nodes.accumulate.output}}
    Consolidate existing data; do not generate new knowledge.
    Produce: title, summary, body (markdown), topics, key points, claims with evidence.
  schema:
    title: string
    summary: string
    body: string
    topics: [string]
    key_points: [string]
    claims: [object]
  effort: high

# Persist (zero-token action, writes to knowledge store)
- id: persist
  kind: action
  label: "Save to knowledge base"
  provider: knowledge_persist
  config:
    title: "{{nodes.synthesize.output.title}}"
    content: "{{nodes.synthesize.output.body}}"
    summary: "{{nodes.synthesize.output.summary}}"
    tags: "{{nodes.synthesize.output.topics}}"
    kind: insight
    claims: "{{nodes.synthesize.output.claims}}"
    citations: "{{nodes.rag.output.trace_id | as_list}}"
    also_artifact: true
    artifact_kind: markdown
```

**Why no new node type:** Each step maps cleanly to an existing kind. The `transform` collects data, the `stage` runs synthesis intelligence, and the `action` persists. This is a PATTERN (reified as a template macro / subworkflow), not a primitive.

**Persist returns its item id** in the node output (`{{nodes.persist.output.item_id}}`) — downstream nodes and later iterations reference the persisted item through normal node-output bindings. (Rev 1's `{{state.last_persist_id}}` was a dangling reference to state that nothing set; KNOW-R1c removes the need for it.)

---

## 2. New Action Providers

### How these plug into the provider architecture (non-negotiable wiring)

Per the platform's pluggable-provider rules (recon: providers.md):

- **Delivery:** the providers ship as a native app `apps/native/knowledge-actions/` with `providers[]` in the manifest (an app may contribute N providers via `AppManifest.all_providers()`), each `{type: "action", implementation: "provider:create_<name>", entity: "knowledge"}` — following the `apps/webhook-action` precedent and WORKFLOWS-V2's own `apps/native/run-workflow-action/`. Each factory returns an `ActionProvider` (`action_providers/base.py:50`: `name`/`display_name`, `supports_blocking=False`, `supports_dry_run=True` for `knowledge_retrieve` (read-only) and False for `knowledge_persist`, `execute(action_config, ctx, timeout) -> ActionResult`).
- **Allowlist:** every new action provider name MUST be added to `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`) or hook create/update rejects it even though the UI offers it. Names added: `knowledge-persist`, `knowledge-retrieve` (+ `render-report` when §6.2 ships).
- **Registration:** the app loader's ActionTypeHandler registers into `action_providers/registry.py:register_action_provider`, making them dispatchable from all three dispatch sites (`hooks.py:494`, `gateway.py:701`, `event_triggers.py:214`) AND the workflow engine's `action` node dispatcher (WORKFLOWS-V2 Slice 1).
- **Config:** knowledge currently has NO config dataclass — `knowledge.fetch_top_n`/`fetch_max_tokens` are raw dict reads in `handlers/knowledge.py`. This plan introduces a typed `KnowledgeConfig` dataclass and wires it through **all FOUR points**: (a) dataclass fields with `_meta(label, help)`, (b) `AppConfig.load()` field-by-field mapping, (c) `to_dict()` (new top-level section), (d) `_EDITABLE_CONFIG` PATCH allowlist + FE for the runtime-editable knobs. New fields: `fetch_top_n` / `fetch_max_tokens` (migrated from raw reads), `synthesis_window` (default 20), `seal_threshold_items` (0 = off), `lint_every_n_persists` (12), `session_brief_max_tokens` (1200).
- **Retrieval through the provider seam (making it real):** `knowledge_retrieve` resolves providers via `knowledge_providers/registry.py` — native first, then any registered external — instead of calling `HybridRetriever` directly. The `NativeKnowledgeProvider` grows a `search()` implementation that delegates to `HybridRetriever`; `search_all()` gains its first production caller. This is the concrete step that lets a future Google Drive/Photos `KnowledgeProvider` participate in workflow retrieval the day it registers (the ABC + registry + `provider` column already exist; no OAuth/sync work in this plan). Also fix in passing: `knowledge_providers.registry:create_native_provider` currently returns `None` (the app-manifest factory is a stub; real construction lives in `dashboard/state.py:1108`) — repoint the manifest factory at the real constructor.
- **Writes through the ONE queue:** `knowledge_persist` writes via `store.create_typed_item(...)` (or the idempotent update path, §2.1) + `ingest_queue.enqueue(item_id)` — the single enrichment path all creators use. Synthesized notes route through the Passthrough pipeline graph (they arrive already structured; terminal stages still run entities/embedding/dedup).

### 2.1 `knowledge_persist` — Write to Knowledge Store *(KNOW-R1, R13, R17, R19)*

| Config Field | Type | Description |
|---|---|---|
| `title` | string | Article title |
| `content` | string | Markdown body |
| `summary` | string | One-line summary for retrieval |
| `tags` | [string] | Topic tags |
| `kind` | string | Typed taxonomy: `fact` / `decision` / `insight` / `report` / `reference` / `known-issue` / `preference-note` / `glossary` / `overview` / `probe`. Stored in a new nullable `kind` column added via the store's `_migrate` machinery; `item_type` stays one of the 12 `NATIVE_TYPES` (default `note`) because it routes the ingestion pipeline graph. |
| `claims` | [object] | Optional structured claims: `{id, statement, status, confidence, hedging: asserted\|hedged\|speculative, evidence: [{source_ref, quote}], valid_at, invalid_at}`. Statement rule: phenomenon-level (numbers/specifics live in `quote`/`source_ref`), so claims are comparable across sources. Supersession SETS `invalid_at`, never deletes — "what was true when" stays queryable. Stored in `file_metadata` JSON (items are not chunked; no new table). |
| `ops` | object | Optional op-list payload alternative to a blob: `{create: [...], update: [...], delete: [...]}` against near items the synthesis stage was pre-loaded with — diff-shaped writes with built-in dedup. |
| `citations` | [string] | Ids into retrieval traces / source items (§5.2). The provider ENFORCES "no synthesized item without citations" for `kind: insight|report` unless `unsourced: true` is set explicitly. |
| `source_ref` | string | Provenance (auto-filled: `workflow:{run_id}:{node_id}`) |
| `source` | object | `{source_system, date, url?, source_tier: primary\|secondary, origin: user\|agent}`. `origin: user` items are protected gold (§4.4). `unsourced: true` replaces silent unattributed storage. |
| `extraction` | string | `llm` / `heuristic` — provenance of the extraction tier (§2.3) |
| `expires_at` / `ttl` | string | Optional expiry for knowledge that goes stale on a known clock (price quotes, schedules). `knowledge_retrieve` demotes/filters past-expiry items via freshness metadata (§2.2d). |
| `also_artifact` | bool | ALSO register in the artifacts registry (`artifacts/registry.py`) — an explicit second write producing an `Artifact` whose metadata carries the knowledge `item_id` back-reference, and the knowledge item's `file_metadata` carries the artifact name. No shared record exists or is created (boundary section). |
| `artifact_kind` | string | `markdown` / `json` / `csv` / `html` |
| `mode` | string | `create` / `upsert` (default) / `append_evidence` (§3.1) |

**Idempotency by construction (KNOW-R1b):** deterministic logical identity `{kind}:{normalized_title}` resolved via lookup-before-write (items keep their UUID PKs; the logical key is an indexed derived column), plus a content hash of the persisted payload. A retried/resumed/rewound persist node whose `(logical_key, content_hash)` already exists is a **no-op returning the existing `item_id`**. Same-content re-persist of a known claim appends a *mention* `{source_ref, confidence, quote}` and re-aggregates confidence via `1-∏(1-cᵢ)`, exposing `support_count` as a retrieval ranking signal (§3.1) — reinforce-on-duplicate, never duplicate-insert.

**Capacity budgets with error-as-return (KNOW-R1 am5):** each `kind` carries a size budget (KnowledgeConfig); a persist that would exceed it returns a descriptive `ActionResult{success: false, error: "over budget by N chars — condense and retry"}` (not an exception), so the synthesizing stage can condense and retry under the engine's normal retry semantics.

**Fire-and-forget enrichment:** the primary record writes synchronously; embedding/entity extraction runs async via the existing ingest queue (never blocks the workflow path); an idempotent backfill loop (`{processed, remaining, done}`) covers records the queue missed.

**Return value:** `{item_id, logical_key, created: bool, mentions_appended: int}`.

**Decision-shaped payloads (KNOW-R13):** `kind: decision` accepts `{question, options, choice, rationale, rejected_alternatives, constraints}` rendered into a canonical body — journals keep the "what", decisions keep the "why" that consolidation otherwise drops. A bundled decision-log pattern: each approval/gate/steer event in a run (e.g. §6's `approve-publish` gate) appends a `kind: decision` item tagged with the run's project, included in §5.3's Session Brief so resumed/forked runs don't re-litigate settled choices. *(The memory-side half of R13 — retention/decay weighting for `source=user` MEMORY records — belongs to the memory subsystem and is re-pointed to WORKFLOWS-V2-LEARNING-FLYWHEEL.)*

### 2.2 `knowledge_retrieve` — Query Knowledge Store *(KNOW-R8, R12)*

Routes through the `knowledge_providers` registry (native → `HybridRetriever`: FTS5 + entity-graph + vector, RRF k=60, relevance-cliff 0.30, min cosine 0.25 — all existing).

| Config Field | Type | Description |
|---|---|---|
| `query` | string | Natural-language query (or binding expression) |
| `mode` | string | `semantic` (default) / `fts` — an LLM-free full-text tier over `items_fts` with cursor pagination, distinct from the budgeted semantic tier |
| `filters` | object | `{tags: [...], kind: ..., item_type: ..., date_range: ..., project: ...}` (project = tag-scoped within the ONE global library — knowledge has no partitions) |
| `top_k` | integer | Max results (default 5, hard cap) |
| `detail` | string | `brief` / `compact` / `full` — per-result token caps replacing rev 1's `include_content: bool`, so a `top_k: 10` context-load can't blow a downstream stage's window |

**Each hit carries (KNOW-R8c,d):**
- `evidence: exact_title | keyword | vector | graph` + `create_safety: exists | probable | unknown` — so workflow nodes branch update-vs-create WITHOUT an LLM duplicate check (this is what §2.1's idempotent persist keys off);
- freshness metadata `{age_days, last_verified, expired: bool}` (from `updated_at` + a new `last_verified` column + `expires_at`) so the synthesizing LLM can reason about staleness;
- `relevance_score`, `support_count`, `status: seedling|growing|evergreen` (§4.4f) as ranking inputs.

**Retrieval mechanics (KNOW-R8e + amendments):** always-include the matching `kind: overview` item when one exists; one-hop expansion over `confidence ≥ 0.7` typed relations (§3.2) with graph hits enriching but never displacing direct hits; explicit degradation ladder vector → FTS → substring with strategy telemetry in the trace (§5.2); topic-extraction-before-retrieval as a template convention for persist-time neighbor checks (cheap pass extracts topics from new input, uses them as queries — "understand first, then check what I already know"); optional local cross-encoder rerank arm delivered later as a normal local-model provider (out of this plan's critical path).

**`read_when` triggers (KNOW-R12):** `knowledge_persist` accepts an optional `read_when: [string]` trigger-condition list (stored in `file_metadata`); `knowledge_retrieve` additionally matches `read_when` conditions against the current node's task text. A retrieve returning zero results emits a `coverage_gap` run-journal event that the periodic synthesizer turns into a persist proposal.

**Output:** `{items: [...], trace_id, strategy, truncated: "N older items not consulted" | null}`.

### 2.3 Zero-Model Heuristic Extraction Floor *(KNOW-R17)*

When no LLM resolves for the synthesis use-case (`provider_bridge.can_resolve_use_case` is the cheap no-instantiate probe), `knowledge_persist` and ingest templates degrade to a deterministic tier: frequency + bigram candidate extraction, first-paragraph summary, wikilink/reference structural linking — filing entries with `extraction: heuristic`. A background enrichment workflow (provider-available event or sparse cadence) re-runs LLM extraction on `heuristic`-stamped entries and upgrades them in place. The template graph shape stays identical; only the extraction tier swaps. This is the local-first resilience floor — the pipeline never silently stops on provider loss.

---

## 3. Store Semantics for Workflow-Maintained Knowledge

### 3.1 Compiled Truth + Append-Only Timeline *(KNOW-R2)*

Every workflow-MAINTAINED knowledge item (monitors, watchers, living documents) has one shape:

- a **compiled-summary section** on top (the current truth), and
- **append-only dated evidence entries** below — delimited regions with explicit markers so the document stays human-editable and machine-parseable (the item's single `content` field; items are one logical document by design, chunking was removed).

The periodic synthesizer rewrites ONLY the compiled section (`mode: upsert` targets the compiled region). Evidence entries are immutable and can be persisted EARLY (`mode: append_evidence` — persist-raw-first while sources are fresh, including evidence-bundle manifests `{files, digests, summary}`), with synthesis arriving later.

Store-level invariants:
1. Scoring/annotation of existing items persists as **sidecar overlay records** `{value, reason, model, scored_at}` in the item's `file_metadata`, keyed to the canonical item — the watcher re-processes only overlay-stale items.
2. Persisting an already-known claim appends a **mention** and re-aggregates confidence (§2.1) — `support_count` becomes a ranking signal.
3. **Contradiction rule:** keep both claims with citations under a source-precedence ladder (user statement > compiled truth > timeline > external), never silently pick one.

Rev 1's `"Topic — Progress ({{iter}})"` per-iteration item spam becomes **ONE canonical item** with an evidence/mention history.

### 3.2 Typed Relations + Contradiction Flagging at Persist Time *(KNOW-R6)*

**Typed item relations:** a new `item_relations(source_item_id, target_item_id, relation_type, confidence, provenance, created_at)` table (added via `_migrate`; sibling to the existing entity-level `entity_relations`), with a 5-verb vocabulary: `supersedes`, `contradicts`, `derived_from`, `depends_on`, `part_of`; upsert on `(source, target, relation)`. Edges carry provenance `extracted` (deterministic, confidence 1.0) vs `inferred`/`ambiguous` (LLM, confidence-scored). `derived_from` doubles as §4.4's `parent_ids` — one mechanism. Deliberately item-fields-plus-report, NOT a graph database, per personal-scale guardrails.

**Contradiction check on persist:** (1) deterministic pre-LLM test — flag claims sharing subject+predicate with different objects, or subject+object with different predicates (zero-cost, enabled by §2.1's claims structure); (2) then retrieve semantically-near items (the existing hybrid retriever gives a strictly better neighbor set than a recency window) and run a fast-model conflict check via `one_shot_completion(use_case="background")`. Conflicts store as first-class records surfaced in the knowledge UI and in periodic-synthesizer output — flag at ingest, not query.

**Cost controls:** background edge inference is memoized per-item by content hash (only changed items re-hit a haiku-class model); deterministic candidate shortlist (field/entity overlap scoring, cap 30, exclude same-source) before ONE fast-model call proposing ≤5 typed edges with ≤25-word justification — graph-size-independent marginal cost; ≤10 relations per pass. The background pass's action vocabulary for each new item's neighbors: `link` / `merge` / `update-neighbor-tags` — new knowledge can retroactively refine its neighbors. Merges canonicalize to a stable representative id preserving source lineage (never orphan back-references).

### 3.3 Per-Store `schema.md` Conventions Contract *(KNOW-R16)*

A durable conventions document at `workspace/knowledge/schema.md` — kinds in use, naming conventions, linking rules, ingest conventions, emphasis rules — loaded into `knowledge_persist`/synthesis context on every write/synthesis operation. Structure IS the contract; the intelligence tier is swappable (heuristic floor and LLM nodes write into identical structure).

The synthesizer may **PROPOSE** edits to schema.md when the user repeatedly overrides conventions — routed through the propose-don't-write proposal queue owned by **WORKFLOWS-V2-LEARNING-FLYWHEEL** (the trust anchor; modeled on the existing `skills/proposals.py` pattern), never direct writes. Accepted schema edits trigger a re-validate warning on downstream templates.

### 3.4 Maintenance Lifecycle: Three Cost-Tiered Bundled Templates *(KNOW-R7)*

The plan previously only created knowledge; nothing maintained it. Three bundled templates, cheap→expensive:

1. **`knowledge-health`** — deterministic, zero-LLM, frequent-trigger: stub detection (<100-char body), orphans (zero inbound relations — flag, never auto-delete), broken references/citations, FTS/embedding/store consistency, past-expiry `kind: probe`/TTL items. Pure `transform` + `action` nodes.
2. **`knowledge-lint`** — semantic LLM pass cadenced by **mutation count** (`lint_every_n_persists`, default ~12 — not wall clock): contradictions (§3.2 report), staleness, gaps + suggested sources. Rule: health runs first ("linting an empty item wastes tokens").
3. **`gap-healing`** — phantom-hub detection (entity referenced by ≥N items with no item of its own) drafts missing entries from source excerpts but emits them as **PROPOSALS through the LEARNING-FLYWHEEL queue**, never direct writes (direct-write healing is the studied anti-pattern). Complement: unresolved wikilinks render in the knowledge reading UI as a visible affordance with click-to-propose-draft — the store's growth frontier surfaced continuously.

**Differential refresh:** ingested items store a source back-pointer + content hash — whole-source `content_hash` PLUS per-section `chunk_hashes` in `file_metadata` — and the refresh template re-synthesizes only sections whose hash changed. Invariant: store and compare the SAME hash form (the studied truncated-vs-full mismatch made everything permanently stale).

---

## 4. Incremental Knowledge Updates During Long Runs

### Problem

A research workflow running for hours accumulates findings across dozens of stages. Waiting until the end to synthesize means partial results are invisible, a crash loses unsynthesized work, and the user can't query in-progress findings. And a naive `until_cancelled` watcher degrades within days: re-processed items, unbounded sibling context, self-synthesis drift.

### Solution: Periodic Synthesis via Parallel Watcher (rev 2 mechanics)

```yaml
root:
  kind: parallel
  join: any  # Complete when the main work finishes (watcher runs alongside)
  children:
    - id: main-work
      kind: loop
      mode: {until_dry: {streak: 3, progress_field: "new_findings"}}
      body:
        kind: stage
        label: "Research cycle"
        prompt: |
          Already known (do NOT re-report — return only the DELTA):
          {{nodes.context-load.output.items | map('summary') | join('\n')}}
          ...
        schema: {findings: [{guid: string, statement: string, significance: string}],
                 new_findings: integer}

    - id: periodic-synthesizer
      kind: loop
      mode: {until_cancelled: true}  # runs until sibling completes (join:any cancels it)
      body:
        kind: sequence
        children:
          - kind: wait
            duration_secs: 300
            # or buffer-seal (§4.3): condition = buffer_full OR stale_timeout

          - id: novel
            kind: transform
            expr: "{{siblings.main-work.output | window(20) | unseen}}"
            # window = synthesis_window; unseen = engine-maintained seen-set (§4.1)

          - id: synthesize-progress
            kind: stage
            label: "Synthesize progress snapshot"
            prompt: |
              Consolidate existing data; never generate new knowledge.
              Prior compiled truth: {{previous.output.summary | default('None yet')}}
              Novel findings this window: {{nodes.novel.output}}
            schema: {report: string, confidence: string, gaps: [string]}
            effort: low

          - id: persist
            kind: action
            provider: knowledge_persist
            config:
              title: "{{inputs.topic}} — Monitor"
              content: "{{nodes.synthesize-progress.output.report}}"
              kind: insight
              mode: upsert          # ONE canonical item; compiled section rewritten,
                                    # evidence appended (§3.1). Idempotent on retry.
            on_error: journal_and_continue   # persist failures are non-fatal (§4.2e)
```

### 4.1 Item Identity + Persistent Seen-Set + Delta Rule *(KNOW-R3)*

- Every accumulate iteration emits items with a stable **`guid`** (composable from extracted fields, RSS-style).
- The engine maintains a per-watcher **persistent seen-set** for `until_cancelled` loops (in the run journal, surviving resume/crash) so the synthesizer only ever sees novel items.
- **Delta rule:** the retrieval stage's already-known output is injected into the scan/research prompt with an explicit "return only the DELTA" instruction.
- Source streams are consumed **cursor-style** with a consumed-only-advance rule and per-item bounded retry (window queries drop late/transiently-failed items rather than stalling the cursor).
- A **web-item hygiene** transform preset (off-domain drop, min-title-words) so monitoring templates don't reimplement junk filtering.
- Template-doc anti-pattern note: stateless repetition of cumulative work is a designed-in failure — `until_cancelled` bodies MUST thread persisted per-iteration state.

### 4.2 Bounded-Context, Diff-Aware Continuation *(KNOW-R4, R14)*

- **`synthesis_window: N`** (KnowledgeConfig default 20): the synthesize stage merges only the last N findings per cycle — `{{siblings.main-work.output}}` no longer returns ALL iterations unbounded.
- **`{{siblings.<id>.output}}` defaults to a filtered insight view** — only items above a significance threshold (default 0.7) cross the sibling boundary, with an explicit `| full` opt-out (KNOW-R14). The engine additionally compresses any single sibling payload >2000 chars via LLM-summarize with a deterministic-truncate fallback when the model call fails.
- **Convergence guard (KNOW-R14):** in multi-source templates, the synthesize stage computes pairwise diversity over accumulated inputs; diversity < 0.7 without high confidence flags "sources converged early / possible echo" in the output schema and persisted artifact.
- **`{{previous.output}}` binding** — the prior successful cycle/run of the same template — plus auto-threaded continuation inputs (`prior_report`, `prior_findings`, `prior_seen_refs`) make synthesis **diff-aware**: the interesting knowledge is the diff, not the snapshot.
- **Run-continuity state (zero-setup tier):** a bounded rolling object stored ON the workflow def/trigger record itself — `{summary: last-5 dated outcome lines, recent_topics: cap 10, recent_refs: cap 20}` — auto-injected into the next recurring run under a "Context from previous runs — avoid repeating, build on prior work" header. This is engine/run state, NOT the memory subsystem, and no knowledge-store round-trip is needed for it.
- **Synthesize-then-truncate:** after a successful persist, the accumulated sibling window is pruned from next cycle's injection — the synthesis output doubles as a structured handoff for a fresh session.
- The cycle's structured output may propose `next_cycle_delay_seconds`, clamped to a configured `[min, max]`.
- **Persist failures inside the watcher are non-fatal** — journaled, retried next cycle.
- **Payload discipline:** run-outcome persists capped (~800 chars, keyword-searchable hints, not blob storage); retrieved snippets injected capped under explicit headers with a verify-before-relying disclaimer.

### 4.3 Buffer-Seal: Volume-Driven Synthesis Trigger *(KNOW-R9)*

Alternative watcher trigger alongside wall-clock cadence: accumulate findings into a buffer; synthesize fires when the buffer FILLS (seal), with a `flush_stale` path for long-idle buffers. Template config: `seal_threshold: N items | K tokens`, `flush_stale_after: duration`. Composes with existing loop/wait nodes (the wait condition becomes buffer-full OR stale-timeout) — no new node kind. A quiet week costs zero LLM calls; a busy hour synthesizes promptly.

Third trigger mode (write-triggered compaction, production-verified): on each `knowledge_persist`, retrieve the new item's neighborhood; if ≥5 items cluster at ≥0.62 similarity, LLM-summarize with doctrine "preserve EVERY distinct detail"; archive originals with `summary_id` back-refs (reversible — `is_archived` flag, never delete); on failure leave the cluster untouched. Cost scales with writes, not store size.

### 4.4 Consolidation Mechanics + Lineage Caps for the Periodic Synthesizer *(KNOW-R5, R13)*

The "synthesize progress snapshot" stage gets tested, parameterized mechanics:

1. **Lineage caps:** every synthesized item carries `parent_ids` (= `derived_from` relations, §3.2) back to its inputs; inputs get a `reflection_count` increment with eligibility `reflection_count < 3` for future passes — without this the watcher re-synthesizes its own output and drift compounds.
2. **Deterministic pre-dedup** (normalize, fuzzy-hash ~0.95) before any LLM call; plus a zero-LLM structural pre-pass clearing already-consolidated raw entries so each pass is incremental.
3. **Reference algorithm:** cluster accumulated items via similarity/graph traversal (similarity ≥0.75, min cluster 5, cap ~10 clusters/sweep, batch cap 100); LLM-synthesize ONE insight per cluster with a confidence score; persist the summary WITH parent links; demote originals (archive + ranking demotion via `is_archived` — knowledge items have no decay field and this plan does not add one) but **never delete**.
4. **Prompt doctrine** for all background synthesis: "consolidate existing data, never generate new knowledge".
5. **Trigger/precondition gate stack:** a `consolidated` flag-cursor (batch-select-where-unprocessed, flip on success; backlog count as a health metric), min-hours + min-new-material gates, consolidation lock (abort if contended, via the existing `single_flight` seam), pre-run snapshot + post-run diff + rollback on failure, preview/dry-run mode, bounded stale-candidate set (top 20). Aging tier: DATE-BUCKETED compaction — entries older than a threshold grouped by week/month, one summary per bucket replacing originals with source-id back-references. Track a **compression ratio** per pass as the subsystem's health metric.
6. **Maturity field:** `status: seedling | growing | evergreen` on items, weighted by `knowledge_retrieve` ranking.
7. **Human-gold protection (KNOW-R13):** `source.origin: user` items are persisted FIRST and are exempt from demotion/archival in any consolidation pass — agent discoveries are re-derivable; user decisions are not.

> **Boundary note (KNOW-R5 re-point):** all of the above operates on knowledge.db items produced by workflows. The research's per-memory-kind consolidation modes (episodic-only / semantic-only "dream" passes) describe the MEMORY subsystem — PersonalClaw already has that machinery (`vector_memory.promote_episodic_patterns`, `memory_service` consolidation cadence), and any changes to it belong to **WORKFLOWS-V2-LEARNING-FLYWHEEL**, not here.

### Loop Mode: `until_cancelled`

Unchanged from rev 1 (adopted into WORKFLOWS-V2): the loop runs indefinitely until externally cancelled (sibling completing in a `join:any` parallel, user cancellation, or timeout). Cleanest expression of watcher/monitor patterns.

```
loop modes: counted{n} | until{condition} | until_dry{streak, progress_field} | until_cancelled
```

### Sibling Output Access

**Binding: `{{siblings.<id>.output}}`** — resolves to outputs emitted by a named sibling within the same `parallel` block, as a filtered, windowed view by default (§4.2). `| full` opts out of the significance filter; `| window(N)` bounds it; `| unseen` applies the persistent seen-set.

---

## 5. Knowledge as Workflow Input

### 5.1 Pattern: Retrieval Stage at Workflow Start

```yaml
- id: context-load
  kind: action
  label: "Load relevant knowledge"
  provider: knowledge_retrieve
  config:
    query: "{{inputs.topic}}"
    filters: {tags: ["{{inputs.domain}}"]}
    top_k: 10
    detail: compact        # per-result token caps — can't blow the downstream window

- id: work
  kind: stage
  prompt: |
    {{nodes.context-load.output.items | fenced_sources}}
    Now do: {{inputs.task}}
```

### 5.2 Provenance Journaling, Citation Enforcement, Fencing *(KNOW-R10)*

- `knowledge_retrieve` journals a **retrieval trace** `{query, strategy, ranked candidates with scores, selected flags}` as a run-journal record (trace_id returned in output) and emits sources as a typed side-channel event on the progress stream.
- Retrieved content interpolated into stage prompts MUST be wrapped in the existing `<untrusted_content>` fence (`security.fence_untrusted`, re-exported via `sdk.security`) with a numbered-sources block and a cite-[n] / "say so if sources don't answer" instruction — knowledge items partly derive from web/inbox content, so raw interpolation bypasses the platform's fencing doctrine. (Knowledge already fences at ingest — `knowledge/insights.py:29` — and redacts on the way out in `search-for-context`; this extends the same doctrine to workflow interpolation via a `fenced_sources` binding filter.)
- `knowledge_persist` for synthesis nodes accepts `citations[]` (ids into retrieval traces / source items), enforcing "no synthesized item without source citations" **at the provider, not the prompt** (§2.1).

### 5.3 Push-Based Retrieval: Project Session Brief *(KNOW-R12)*

Per-project curated knowledge (project = tag/`project_id`-scoped within the ONE global library; knowledge has no partitions) compiles into a bounded **Session Brief** (`session_brief_max_tokens`) injected at the start of every workflow RUN in that project — the cheap, non-engine-invasive realization of the explicitly-deferred `auto_rag` runtime hint (no per-stage engine injection). Includes the project's decision log (§2.1).

**Scope guard:** the Session Brief is composed by the workflow engine into RUN context only. It does NOT create ambient knowledge injection into chat sessions — the platform's deliberate "knowledge is never auto-injected into chat" invariant (composer @-picker / agent tool only) stands.

`coverage_gap` journal events (zero-result retrieves, §2.2) become persist proposals for the periodic synthesizer — closing the accumulate→synthesize→persist feedback loop.

### Automatic Context Enrichment (still deferred)

Engine-level `auto_rag` (inject a retrieve before every stage) remains explicitly DEFERRED post-v2. The Session Brief covers the 80% case without engine complexity.

---

## 6. The Monitoring → Synthesis Pattern + Visual Outputs

### 6.1 Monitoring Template (rev 2)

```yaml
name: market-monitor
description: "Monitor market trends and produce periodic synthesis reports"
inputs:
  topic: {type: string, required: true}
  sources: {type: array, required: true}
  report_interval_hours: {type: integer, default: 168}
  # KNOW-R11/OpenJarvis parameter vocabulary — variation is configuration, not new YAML:
  observation_compression: {type: string, default: summarize, enum: [summarize, truncate, none]}
  retrieval_strategy: {type: string, default: hybrid, enum: [hybrid, keyword, semantic, none]}
root:
  kind: loop
  mode: {until_cancelled: true}
  body:
    kind: sequence
    children:
      - id: known
        kind: action
        provider: knowledge_retrieve
        config: {query: "{{inputs.topic}}", filters: {kind: report}, top_k: 3, detail: brief}

      - id: scan
        kind: foreach
        items: "{{inputs.sources}}"
        max_concurrency: 3
        body:
          kind: stage
          label: "Scan {{item}}"
          prompt: |
            Already known: {{nodes.known.output.items | fenced_sources}}
            Check {{item}} for NEW developments about {{inputs.topic}} — return only the DELTA.
          schema: {developments: [{guid: string, headline: string, significance: string, source: string}]}

      - id: filter
        kind: transform
        expr: "{{nodes.scan.output | flatten | hygiene | unseen | filter('significance','high')}}"

      - id: synthesize
        kind: stage
        label: "Periodic synthesis"
        prompt: |
          Consolidate existing data; never generate new knowledge.
          Prior compiled truth: {{previous.output.report | default('None yet')}}
          New developments this period: {{nodes.filter.output}}
        schema: {report: string, key_trends: [string], alerts: [string],
                 next_cycle_delay_seconds: integer}
        effort: high

      - id: persist
        kind: action
        provider: knowledge_persist
        config:
          title: "{{inputs.topic}} Monitor"
          content: "{{nodes.synthesize.output.report}}"
          tags: ["monitor", "{{inputs.topic}}"]
          kind: report
          mode: upsert            # ONE canonical item, compiled truth + evidence timeline
          also_artifact: true
        on_error: journal_and_continue

      - id: wait-interval
        kind: wait
        duration_secs: "{{nodes.synthesize.output.next_cycle_delay_seconds | clamp(3600, 604800) | default(inputs.report_interval_hours * 3600)}}"
```

Composes `loop{until_cancelled}` + `foreach` + `transform(hygiene|unseen|filter)` + `stage` + `action(upsert)` + `wait(adaptive)`. No special "monitoring" node type.

### 6.2 `render_report` Action Provider *(KNOW-R15 — deliberately last, deferrable)*

Optional terminal step for synthesis/monitoring templates: takes synthesized content plus a declarative spec (markdown sections; sort/filter/compute table ops over collected outputs; charts compiling to Mermaid xychart-class output) and produces a **sanitized** self-contained HTML/SVG artifact (nh3-style sanitization — inputs are LLM-over-untrusted-web). The SPEC TEXT is stored as the versioned record in the **artifacts registry** (`artifacts/registry.py` — versioning lives there, not in knowledge; knowledge items have no version history) with the rendered output as a derived export, so the periodic synthesizer regenerates visuals from updated data for free.

Plumbing: same as §2 — ActionProvider in `apps/native/knowledge-actions/` `providers[]`, name added to `ALLOWED_HOOK_PROVIDERS`. Ships only after the store-semantics work lands.

---

## 7. Bundled Template Slate *(KNOW-R11, R18)*

Templates are the plan's proof-of-life — field-tested shapes with real daily content that exercise every new mechanism (guid-dedupe, compiled-truth upserts, retrieval, watcher):

### 7.1 The Slate

1. **`knowledge-synthesis`** — the three-node pattern as a reusable subworkflow macro (§1).
2. **`market-monitor`** — §6.1, the flagship `until_cancelled` instance.
3. **`trending-repo-digest`** — flagship monitor instance with real content: clock → HN/GitHub-trending fetch (via `net.fetch` egress chokepoint) → foreach → guid-dedupe vs store → synthesize plain-English pitch → `knowledge_persist` + inbox digest, per-item "spotted on X" provenance.
4. **Dual-sink watcher variant** — the same synthesizer emits `knowledge_persist` (queryable article) AND an artifact update, with a delivery leg (digest → inbox/channel alert via existing `notify`/`send-message` action providers, subject to the `notification_allowed()` gate).
5. **`meeting-prep`** — upcoming event input → `knowledge_retrieve` on attendees/topics → synthesize brief. (No calendar provider exists today; the event is a template input until one does.)
6. **`thesis-tracker`** — falsifiable statement + pillars + invalidating risks + evidence entries tagged strengthen/weaken/neutralize + review cadence; a pure §3.1 compiled-truth item.
7. **Raw → rolling-summary → one-pager tiering** — period-keyed raw evidence entries, rolling N-month summary, one-page distillation with a will-not-do negative-space slot.
8. **`quality-document`** — synthesizer-maintained artifact grading project modules A–D on verification/legibility/test-stability; downstream runs read it to pick the lowest-grade target first.
9. **`paper-ingest`** — fetch+slice (action) → parallel {summarize+critique on reasoning axis, extract-claims on fast model} → metadata → assemble → parallel deterministic {persist, reference-match, candidate-shortlist, entity-stubs} → link (1 fast-model call) → apply-edges → lint scoped to new items — with a provable 4-LLM-call-per-item budget as a design invariant. (Model tiers via use-case bindings: `reasoning` axis for deep passes, `background` for fast ones — `one_shot_completion` semantics.)
10. **`living-document`** — source set → structured per-source extraction → diff-vs-current-state → NEW/RECURRING/RESOLVED/ON-HOLD reconciliation against the document's own append-only changelog → approval gate; suppressed-recurring-findings state stored in the knowledge item (learned noise-suppression).
11. **`publish-article`** (retained from rev 1) — the full artifact lifecycle: draft (stage) → dual review (parallel, join: all) → revise → approval gate → `knowledge_persist {kind: reference, also_artifact: true}`, with the gate decision appended as a `kind: decision` item (§2.1).
12. **Maintenance trio** — `knowledge-health`, `knowledge-lint`, `gap-healing` (§3.4).

### 7.2 Classifier-Then-Dispatch Rich-Ingest Template *(KNOW-R18)*

"Ingest a 2-hour meeting transcript and populate knowledge + tasks in one pass", composed ONLY of existing node kinds: a cheap classifier stage examines the input and selects which extraction lenses apply (bias: when uncertain, include more lenses — the specialists do final filtering); a `parallel` node fans the SAME input through per-lens extraction stages, each with a different prompt lens and output schema; a converge node dedupes and conflict-checks across lens outputs (§3.2) before final persist.

**Lenses (knowledge + tasks ONLY):** decisions (→ `kind: decision`, §2.1 decision shape), reference materials (→ `kind: reference`), facts/concepts (→ `kind: fact`/`glossary`), reports/summaries (→ `kind: report`), action items (→ the existing `create-task` action provider).

> **Boundary note (KNOW-R18 re-point):** the research's episodic-events and preference/procedural lenses are user-modeling — that is the MEMORY subsystem's job (after-turn capture, preference facets, episodic writes) and is re-pointed to **WORKFLOWS-V2-LEARNING-FLYWHEEL**. This template writes knowledge items and tasks only; it never writes memory.

---

## 8. Changes to WORKFLOWS-V2.md

1. **New loop mode:** `until_cancelled` (§4) — add to Section 1 node taxonomy, with per-watcher persistent seen-set state in the run journal.
2. **New bindings:** `{{siblings.<id>.output}}` (filtered/windowed by default, §4.2) and `{{previous.output}}` (prior successful cycle/run). Binding filters: `window(N)`, `unseen`, `hygiene`, `fenced_sources`, `full`, `clamp`.
3. **New action providers:** `knowledge-persist`, `knowledge-retrieve` (+ deferred `render-report`) — delivered via `apps/native/knowledge-actions/` `providers[]`, registered through the action `_TypeHandler` into `action_providers/registry`, **added to `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`)**.
4. **Run-continuity state** on the workflow def/trigger record (§4.2) — bounded rolling `{summary, recent_topics, recent_refs}` auto-injected into recurring runs.
5. **Journal event types:** `coverage_gap`, retrieval traces, non-fatal persist-failure records.
6. **Bundled templates:** the §7 slate (was: two templates).
7. **Add to Slice 7:** `until_cancelled` + sibling/previous bindings + seen-set.

## 9. Store & Config Changes (this plan's own scope)

- `knowledge/store.py` `_migrate`: new nullable `kind` column + indexed `logical_key` derived column + `last_verified`, `expires_at` columns; new `item_relations` table (§3.2). All additive; existing rows unaffected.
- `file_metadata` JSON carries: claims, mentions, overlays, `parent_ids`/`reflection_count`, `read_when`, content/chunk hashes, `source` provenance block, artifact back-ref.
- New `KnowledgeConfig` dataclass wired through the FOUR config points (§2).
- `NativeKnowledgeProvider.search()` implemented over `HybridRetriever`; `create_native_provider` manifest stub repointed at the real constructor; `search_all()` gains its first caller (`knowledge_retrieve`).
- `workspace/knowledge/schema.md` conventions file (§3.3).
- Knowledge UI: contradiction report surface + unresolved-wikilink affordance (§3.2, §3.4).

---

## Implementation Effort

**6 sessions** (was 2), after Workflows v2 Slices 0-2; +1 optional for `render_report`:

- **Session 1 — Store semantics groundwork:** `kind`/`logical_key`/`last_verified`/`expires_at` migrations, `item_relations` table, claims/mentions/overlay `file_metadata` shapes, content+chunk hashing, `KnowledgeConfig` four-point wiring, `schema.md` scaffold.
- **Session 2 — The provider pair:** `apps/native/knowledge-actions/` app; `knowledge_persist` (idempotent upsert/append_evidence/ops modes, budgets, citations enforcement, dual-artifact write, heuristic floor) + `knowledge_retrieve` (modes, detail budgets, evidence/create_safety, freshness, graph expansion, traces, `read_when`); `ALLOWED_HOOK_PROVIDERS` additions; native provider `search()` + registry routing; three-node pattern end-to-end.
- **Session 3 — Engine additions:** `until_cancelled` loop mode + persistent seen-set; `{{siblings.*}}` (filter/window/unseen) + `{{previous.output}}` + run-continuity state; buffer-seal wait condition; adaptive delay clamp; non-fatal persist handling; convergence guard.
- **Session 4 — Consolidation + maintenance:** §4.4 reflect mechanics (lineage caps, pre-dedup, gate stack, date-bucketed aging, compression-ratio metric); `knowledge-health` / `knowledge-lint` / `gap-healing` templates; proposal routing to the flywheel queue; differential refresh.
- **Session 5 — Contradiction + retrieval polish:** persist-time deterministic + fast-model conflict pass, typed-edge background inference (memoized, capped), contradiction report UI surface; Session Brief + coverage-gap loop; fencing filter (`fenced_sources`).
- **Session 6 — Template slate + validation:** §7 slate incl. rich-ingest; long-run validation of the watcher (idempotent re-runs, bounded cycle cost, seen-set persistence across restart).
- **Session 7 (optional/deferred) — `render_report`** (§6.2).

## Dependencies

- Knowledge store (`knowledge/store.py`) + ingest queue (`knowledge/ingest_queue.py`) — existing; ALL persist writes route through them.
- Artifacts registry (`artifacts/registry.py`) — existing; `also_artifact`/`render_report` write into it explicitly (no knowledge↔artifact link exists today; this plan adds metadata back-refs only).
- Action provider registry + app-loader action `_TypeHandler` (existing) + `ALLOWED_HOOK_PROVIDERS` update (code change in `validation.py`).
- `wait` node support (WORKFLOWS-V2 Slice 1); `until_cancelled` mode (this plan's scope).
- `one_shot_completion(use_case="background")` for fast-model passes; `reasoning` axis for deep synthesis (existing use-case bindings).
- `security.fence_untrusted` / `sdk.security` (existing) for §5.2.
- LEARNING-FLYWHEEL proposal queue for §3.3/§3.4 proposal routing (can stub to `skills/proposals.py`-pattern queue if flywheel lands later).

## Risks & Guardrails

- **Self-synthesis drift** → lineage caps (`reflection_count < 3`), "consolidate, never generate" doctrine, demote-never-delete, pre/post snapshots + rollback (§4.4).
- **Retry/rewind double-writes** → idempotency by construction: logical key + content hash no-op, mention-append on duplicates (§2.1).
- **Token burn in long runs** → seen-set + delta rule, `synthesis_window`, filtered siblings, buffer-seal, payload caps (§4).
- **Prompt injection via retrieved knowledge** → mandatory `<untrusted_content>` fencing at interpolation + provider-enforced citations (§5.2).
- **Boundary creep into memory** → this plan touches only knowledge.db + the run journal; every memory-shaped rec is explicitly re-pointed to LEARNING-FLYWHEEL (see Research Integration + boundary notes in §4.4, §7.2).
- **Enterprise-machinery creep** → item-fields-plus-report instead of a graph DB; single-user cadences (mutation-count lint, not fleets); everything propose-don't-write for schema/gap healing.

## Success Criteria

1. A research workflow that runs 10+ cycles produces ONE canonical knowledge article (compiled truth + evidence timeline) accessible via knowledge search, with claims, citations, and `workflow:{run_id}` provenance.
2. Re-executing a persist node (retry/resume/rewind) is a provable no-op: no duplicate items, mention counts stable.
3. The periodic synthesizer runs for a week of simulated cycles with bounded per-cycle token cost (seen-set + window verified) and zero re-processed guids.
4. A `knowledge_retrieve` at workflow start enriches downstream prompts inside `<untrusted_content>` fences, under `detail` budgets, with a journaled retrieval trace.
5. The monitoring template runs indefinitely, adapting its cadence, surviving gateway restart (seen-set + cursor persisted), until cancelled.
6. `knowledge-health` (zero-LLM) and `knowledge-lint` (mutation-cadenced) run over a 100+-item store; gap-healing emits proposals, never direct writes.
7. The contradiction report surfaces a seeded conflict (same subject+predicate, different object) at persist time, with both claims retained under the precedence ladder.
8. The rich-ingest template turns one meeting transcript into typed knowledge items + created tasks in one pass — and writes nothing to memory.db (asserted in the test).
9. The `publish-article` workflow goes draft → dual-review → revise → gate → persist, appending a `kind: decision` item at the gate.
10. With no LLM provider bound, `knowledge_persist` still files entries (`extraction: heuristic`) and the enrichment workflow upgrades them when a provider returns.
