# Plan: Memory Graph & Readable Vault — Typed Entity Linking, Push-Context, Slots

**Status:** PROPOSED (rev 2 — research-integrated 2026-07-12)  
**Created:** 2026-07-12  
**Depends on:** nothing hard. Pairs with WORKFLOWS-V2-LEARNING-FLYWHEEL (which owns lesson/skill lifecycle — this plan owns the *store structure* under it) and with the git-snapshot work (NEW-4) for vault versioning  
**Scope:** memory.db data-model change (typed entity graph, zero-LLM write-time linking, graph recall arm, Extract→Decide formation, holder attribution) + memory UX primitives (editable Markdown vault, memory citations, memory slots incl. a self-model slot). Knowledge.db is *enriched* (denser mentions, shared alias resolution), never restructured — it already has the graph tables this plan gives memory.

---

## Research Integration (2026-07-12)

Two approved recommendations folded in (mechanism-level, not appendix):

- **NEW-3** — typed entity graph over memory (zero-LLM write-time linking, alias table, backlinks, edge vocabulary `same_topic/temporal_proximity/references/same_project`, graph recall arm, orphans lint) → §1, §2; ambient push-context reflex + volunteered-vs-used stat → §3; Louvain community topology → §2.4; takes/claims holder attribution → §4.2; Extract→Decide two-phase formation → §4.1; interactive graph visualization/export → §2.4, §7.2
- **NEW-15** — readable Markdown vault (Obsidian-compatible, wikilinked, edits flow back, three-type split, symlink + raw/ capture, starter seeding, static export) → §5; memory citations in chat + admit-ignorance → §5.4; memory slots (named size-capped always-injected registers) + per-project glossary variant + self-model slot → §6

---

## Overview

Two trust problems, one plan. First: PersonalClaw's memory records are **not entity-linked at all** — recall is flat hybrid retrieval (0.6·vec + 0.4·kw, `vector_memory.py` L1063) with no notion that `user.persona.role`, an episodic row about a standup, and a lesson about a repo all concern the same *person* or *project*. GBrain's published ablation attributes **+31.4 P@5 to exactly a deterministic write-time typed-edge graph** — more than hybrid search itself, at zero token cost. Second: "you can't trust a memory you can't read." memory.db is a vector-store black box to the user; the existing vault mirror (`memory_vault.py`) is read-only. This plan makes the graph the memory store's skeleton and the vault its human-editable face, plus a small set of bounded always-injected registers (slots) that can never blow the context budget by construction.

**Soul guardrail:** personal-scale, single user, plain files under `~/.personalclaw`. No graph database, no enterprise KG pipeline — SQLite tables inside the existing memory.db, regex/alias matching at write time, LLM calls only where they already happen (consolidation). Everything proactive proposes; the human edits the vault, the assistant proposes.

**Memory vs Knowledge boundary (user directive — load-bearing for this whole plan):** KNOWLEDGE = the user's personal items (documents, files, photos, notes; future providers: Google Drive, Google Photos). MEMORY = the harness's own internal mechanics (facts/facets/episodic/procedural/lessons about the user and work). This is a **MEMORY-subsystem plan**: the new tables live in `memory.db`, the intelligence lives in `MemoryService`, the vault mirrors memory. Knowledge.db **already has** `entities` / `entity_relations` / `mentions` tables and a graph arm in its `HybridRetriever` (RRF fusion, `knowledge/retrieval.py`) — the graph work here *also serves* knowledge by (a) sharing one alias-resolution index across both stores and (b) adding a deterministic zero-LLM mention pre-pass to the knowledge ingestion pipeline's entities stage. It never adds tables to knowledge.db and never cross-writes between the stores (recon: no FK, no shared ID space — that stays true; the bridge is a read-only alias index, §1.3).

### Starting points (verified against code, 2026-07-12 recon)

The design builds on what actually exists — several research assumptions were corrected against it:

- **Memory record kinds are key-prefix-inferred** (`memory_record.py:_kind_from_key` L310: `lesson.*`, `user.procedural.*`, `user.persona.*`, `user.commitment.*`) — there is **no kind column**. New record classes in this plan (slots §6, claims §4.2) follow the same convention: new key prefixes + allowlist entries, not schema kinds.
- **memory.db schema** (migrations v1→v6, `vector_memory.py` L233-375): `semantic_memory` (key PK, embedding BLOB, recall_count, superseded_by/invalidated_at, tier/scope/category, visit_count), `episodic_memories`, `memory_events` (reversible WAL, capped 10k, `undo_event`). Embeddings L2-normalized f32 + FAISS IP sidecar. The graph tables land as **migration v7**.
- **memory.db already carries legacy `knowledge_facts`/`knowledge_edges` tables** (persistence recon) — a naming collision waiting to bite. Migration v7 audits them: adopt-and-rename if populated, drop if empty. Either way the new tables are named `mem_entities`/`mem_links` so "knowledge_*" names never again appear inside memory.db (they belong to knowledge.db).
- **A memory vault already exists**: `memory_vault.py` (430 ln), an Obsidian-style **read-only** markdown mirror, config `memory.vault_enabled`/`vault_path` (default `~/.personalclaw/memory-vault`), mirrored on `consolidate_session`. And `memory.py`'s `MemoryStore` is *already a markdown projection* (preferences.md / projects.md / daily history + FTS5 `memory_index.db`; post-M2 explicitly not a MemoryProvider). §5 **extends these real seams** — it does not build a vault from scratch, and NEW-15's "no plan owns a vault" claim is corrected to "no plan owns an *editable* vault."
- **`MemoryService` (memory_service.py) is where ALL intelligence lives** — `get_context`, `active_recall`, `promote_by_heat` (→global gate: heat ≥1.0 AND recall_count ≥2), `heat()` = 0.7·log1p(visits)/ln10 + 0.5·e^(−days/30) (`memory_record.py` L259), category-TTL, S5 write-injection scan (`_memory_write_blocked` L900). Every new intelligence op in this plan is a MemoryService method; the graph arm's recalls count as visits so linking *feeds* the existing heat/promotion math instead of competing with it.
- **`recall_with_provenance` already exists** (memory_service.py) — memory citations (§5.4) surface it, they don't invent provenance.
- **Consolidation is one LLM prompt** (`history.py:_consolidate_locked` L1106 extracts history + semantic/episodic/lessons/persona/commitments via the `memory_consolidation` use-case) with post-steps on maintenance cadence (`promote_by_heat`, `expire_by_category`, `synthesize_failures`, digests). Extract→Decide (§4.1) restructures *this* pipeline. "DELETE" decisions map to the **existing supersession chain** (`superseded_by`/`invalidated_at`, v4) + the reversible `memory_events` WAL — never physical deletes; propose-don't-write survives.
- **Knowledge is NEVER auto-injected into prompts** (deliberate — recon gotcha 8: it enters chat only via the composer @-picker or agent `knowledge_search` calls). The push-context reflex (§3) respects this: memory records may inject ambiently; knowledge hits render as *suggestion chips*, never silent context.
- **Ambient injection order is fixed** (context.py `build_session_context` ~L846-940: memory context → working memory → persona → USER PROFILE facets → skills → lessons; per-turn `active_recall` via context_engine.py L107). Slots and the push reflex slot into *this* sequence at named positions — no second injection path.
- **Memory partitioning:** memory is cwd-partitioned (`memory_dir_for_cwd`), knowledge is one global library (namespace column deliberately dropped). The vault mirrors the gateway's main store; per-partition sub-vaults are out of scope v1.
- **FE:** memory is a **Settings panel, not a nav page** (`web/src/pages/settings/MemoryPanel.tsx`, tabs `studio|health|recall|inspect|audit|settings`, + `MemoryGraph.tsx`). All new UI lands as MemoryPanel tabs/extensions — no new nav tile.
- **Foreign memory providers degrade**: `MemoryService._vs` only recognizes a `VectorMemoryStore` (explicit or via `.vector_store`); graph/vault/slots intelligence is native-store functionality and no-ops gracefully for a foreign `MemoryProvider` (same posture as every existing `_vs is None` guard). `MemoryCapabilities` gains an advisory `entity_graph` flag so a future provider *can* declare it.

---

## 1. The Typed Entity Graph (memory.db data model)

Migration v7 adds three tables to memory.db (WAL, 0600, same conventions as v1-v6):

```sql
mem_entities(
  id TEXT PK,             -- e-<8hex>
  name TEXT,              -- canonical display name
  entity_type TEXT,       -- person | project | tool | org | topic | place
  aliases JSON,           -- ["@handle", "nickname", "Full Name"]
  source TEXT,            -- seeded_from: facet | knowledge | user | consolidation
  created_at, updated_at, is_deleted
)
mem_links(                -- THE backlinks table
  id INTEGER AUTOINC,
  from_kind TEXT,         -- semantic | episodic
  from_ref TEXT,          -- semantic key or episodic id
  to_entity TEXT,         -- mem_entities.id  (entity links)
  to_ref TEXT,            -- OR another record ref (record↔record edges)
  link_type TEXT,         -- §1.2 vocabulary
  provenance TEXT,        -- extracted | inferred      (confidence semantics §1.2)
  confidence REAL,        -- extracted=1.0; inferred ≥0.0
  context TEXT,           -- ≤200-char snippet around the mention
  created_at
)
mem_link_stats(           -- denormalized per-entity rollup for O(1) ranking boost
  entity_id TEXT PK, inbound_count INT, last_linked_at TEXT, community INT
)
```

Indexes on `(to_entity)`, `(from_kind, from_ref)`, `(link_type)`. Every `mem_links` insert/delete also appends a `memory_events` WAL row (`event_type: link_add|link_remove`) so graph writes are **undoable** through the existing `undo_event` machinery — the graph inherits reversibility instead of building its own.

### 1.1 Zero-LLM write-time linking

On every memory write — `VectorMemoryStore.put` (semantic), episodic write (L1445), `write_lesson` (L1858) — a deterministic linker runs *after* the existing S5 injection scan and validation:

1. **Mention matching** against the alias index (§1.3): word-boundary matches of entity names, aliases, and @handles in the record's value/text. No LLM, no regex authored per entity — one compiled Aho-Corasick-style pass over the alias set (rebuilt on alias change, cached in-process).
2. Each hit → one `mem_links` row `{link_type: mentions, provenance: extracted, confidence: 1.0, context: ±100 chars}`.
3. **Typed-edge cascade** (GBrain's fixed heuristic order, adapted): key-prefix and structural cues upgrade `mentions` to a stronger type — a `project.*` key mentioning a project entity → `same_project`; two records written within the same conversation/consolidation batch → `temporal_proximity`; an explicit key/URL reference → `references`; embedding-cluster co-membership at consolidation time → `same_topic` (§2.4). First match in the cascade wins.
4. **Stub discipline (notability gate):** unknown capitalized names do NOT auto-create entities (GBrain: "when in doubt, DON'T create — a junk page degrades search"). Unknown-mention counts accumulate in a scratch tally; ≥3 distinct-record mentions promotes the name to a *proposed entity* surfaced in the orphans lint (§2.3) for one-click accept — propose-don't-write applied to the graph itself.

Cost: zero tokens, one string-matching pass per write, one batched insert. The write path stays synchronous and fast (matching GBrain's "17K-page full extract completes in seconds").

### 1.2 Edge vocabulary + provenance

Two independently-validated vocabularies, merged:

| link_type | Semantics | Producer |
|---|---|---|
| `mentions` | record text names the entity | deterministic (extracted, 1.0) |
| `about` | the entity is the record's primary subject (key-derived: `user.persona.*` → the user entity, `project.<slug>.*` → that project) | deterministic |
| `same_project` | record ↔ project-entity affiliation | deterministic cascade |
| `references` | explicit key/URL/artifact reference to another record or item | deterministic |
| `temporal_proximity` | records from the same conversation / consolidation batch | deterministic |
| `same_topic` | embedding-cluster co-membership | consolidation-time (inferred, carries cluster cosine as confidence) |

Provenance follows llm-wiki-agent's taxonomy: `extracted` (deterministic, confidence 1.0, wins dedup ties) vs `inferred` (consolidation-time, confidence <1.0, retrieval/UI filterable). One edge per (from, to, type) — duplicates reinforce `mem_link_stats` counts rather than inserting (agent-memory.dev's reinforce-not-duplicate rule).

### 1.3 The alias index — the one memory↔knowledge bridge (read-only)

`mem_entities` is seeded and refreshed from three sources, composed into one in-process alias index used by BOTH stores' deterministic linkers:

- **Memory facts**: person/project names parsed from `pref.facet.identity.*`, `user.persona.*`, `project.*` keys (deterministic key/value parse, no LLM).
- **Knowledge entities**: knowledge.db's existing `entities(name, entity_type, aliases)` rows, read-only. A `mem_entities` row seeded from knowledge stores the knowledge entity id in `source` for display-time cross-navigation — **not a FK**; each store remains independently rebuildable, and deleting either side degrades to a dangling attribution string, never a broken constraint.
- **User edits**: the vault's entity pages (§5) and the MemoryPanel studio — `aliases:` frontmatter is the user-facing alias editor (GBrain's `page_aliases` pattern).

The knowledge side gains one thing here: a **deterministic alias pre-pass in the ingestion pipeline's entities terminal stage** (`knowledge/pipeline/runner.py` terminal stages: … → entities → …). Before the existing LLM EntityExtractor runs, alias matching populates `mentions` rows in knowledge.db for already-known entities at zero cost — the LLM pass then only handles *novel* entity discovery. This densifies the graph arm knowledge's `HybridRetriever` already has, without touching its schema or RRF math.

---

## 2. Graph Recall Arm + Ranking

### 2.1 The graph arm in memory retrieval

Memory's hybrid retrieval (0.6·vec + 0.4·kw) gains a third arm, mirroring the shape knowledge's `HybridRetriever` already proved (FTS + entity-graph + vector):

1. Resolve entity mentions in the query text via the alias index (same matcher as write time).
2. For each resolved entity, pull backlinked records from `mem_links` (one hop; `same_topic` chains and cluster neighborhoods walkable at depth 2 for explicit graph-walk recall — Memoh's strategies — but depth 1 is the default).
3. Fuse: graph hits merge into the candidate set before scoring; final score gains a **backlink-count boost** — `+ β·log1p(inbound_count)` with β small (~0.1), reading `mem_link_stats` O(1). Structural queries ("what do I know about X?") get answered by traversal, not similarity.
4. Graph-arm hits increment `visit_count`/`recall_count` exactly like vector hits — so a well-linked record accrues `heat()` and becomes eligible for `promote_by_heat` (heat ≥1.0 AND recall_count ≥2). **The graph feeds the existing promotion math; it does not add a parallel promotion path.**

Plugs in at `vector_memory.py`'s hybrid scorer + `MemoryService.active_recall` (L225), inside the existing `active_recall_timeout_ms` budget (1500ms — the deterministic arm is microseconds). `MemoryCapabilities` gains `entity_graph: bool`; the service guards on it as it does `vector`.

### 2.2 Retrieval provenance tags

Each recall hit carries an `evidence` tag (`alias_hit | exact_key | vector | keyword | graph_hop`) in `recall_with_provenance` output — the debuggability contract the push reflex (§3) and citations (§5.4) both consume.

### 2.3 Orphans lint

`memory_lint.py` (exists) gains graph checks, all deterministic and zero-LLM:

- **Orphans**: semantic/episodic records with zero `mem_links` rows (excluding slots and manifest keys) — surfaced as a count + list, never auto-fixed.
- **Proposed entities**: the ≥3-mention promotion queue from §1.1.
- **Phantom entities**: entities with zero inbound links (candidates for merge/delete — flag only).
- **Stale aliases**: alias-index entries whose seed record was superseded.

Rendered in MemoryPanel's existing `health` tab; also exposed on `/api/memory/lint` (route exists).

### 2.4 Community topology (Louvain) — session-start orientation

On the consolidation maintenance cadence (the existing post-step slot in `history.py`, NOT a new loop): run Louvain community detection (fixed seed, deterministic — llm-wiki-agent's `seed=42` discipline) over `mem_links`, write `community` into `mem_link_stats`, and materialize a **compact topology block** (≤400 chars: top-N communities with 2-3 label entities each). `MemoryService.get_context` may include it in the L1-manifest region on NEW sessions only — orientation ("here are the neighborhoods of what I know"), gated by `memory.graph_topology_in_context` (default off). Same computation powers the visualization (§7.2).

---

## 3. Ambient Push-Context Reflex (zero-LLM, per-turn)

Distinct from pull-based `active_recall` and from skill/lesson surfacing: the store *volunteers* records the current conversation is implicitly about.

- **Where it runs:** the per-turn context hook that already calls `active_recall` (context_engine.py L107) — one extra deterministic pass, no new lifecycle plumbing. (The `MemoryProvider.on_turn_start` hook exists but is called nowhere today — recon; this plan does NOT quietly wire it. The reflex rides the proven context_engine seam; wiring the dormant provider hooks stays a LEARNING-FLYWHEEL/C27 decision.)
- **Mechanism** (GBrain's volunteer pipeline, adapted): extract entity candidates over the **rolling window of the last N turns** (capitalized runs, @handles; pronoun follow-ups resolve to the newest prior entity) → resolve via the alias index with **per-arm confidence: alias 0.9, exact name 0.8, fuzzy/suffix 0.6, +0.05 if the entity appears in ≥2 turns or the newest turn** → gate at `min_confidence` (default **0.7**, config) → suppress records already surfaced this session → cap **3 records per turn** (hard 5).
- **What it volunteers:** MEMORY records inject as a small "possibly relevant" block appended after the episodic/active-recall region (existing order preserved). KNOWLEDGE hits (the alias index knows knowledge-seeded entities) do **not** inject — knowledge is never auto-injected (deliberate invariant); instead the reflex emits a `chat.side_result`-style suggestion the composer renders as an @-picker chip ("Related: <item title>"), keeping the human in the selection loop.
- **Volunteered-vs-used stat:** every volunteer event is logged (deterministic template string, never raw conversation text — GBrain's privacy rule) to a `mem_volunteer_events` table: `{entity, arm, confidence, record_ref, ts}`. "Used" = the record's `recall_count`/`visit_count` incremented (or knowledge chip clicked) later in the session. Per-arm precision renders in MemoryPanel `health`; the user (or a future flywheel pass) tunes `min_confidence` from data, not vibes. Events pruned at 90d by the consolidation maintenance cadence.
- **Restricted sessions:** temporary sessions block memory reads — the reflex checks `session_restrictions.is_restricted` exactly as the recall endpoint does; incognito allows reads, so the reflex runs but its volunteer events don't log (write suppression).

Zero LLM calls, zero tokens beyond the (small, capped) injected records. "Pushed noise never gets worse than pull silence" — and the stat proves or disproves it.

---

## 4. Memory Formation — Extract→Decide + Holder Attribution

### 4.1 Two-phase Extract→Decide consolidation (Memoh)

Today `_consolidate_locked` extracts everything in one prompt and writes it. Restructured into two phases within the same consolidation flow (same `memory_consolidation` use-case binding, one extra cheap call):

1. **Extract** — the existing prompt, unchanged in spirit: candidate facts/episodics/lessons/persona/commitments from the transcript.
2. **Gather** — for each candidate, `vector_query` + graph-arm lookup over existing memories (deterministic + one embed batch; the `episodic_dedup_threshold` .88 machinery already half-does this for episodics — it generalizes).
3. **Decide** — one structured LLM call per batch: for each candidate with its overlap set, verdict `ADD | UPDATE | SUPERSEDE | NOOP`, with a one-line reason. Mapping to the store: `UPDATE` = same-key put (v4 supersession chain records the old value); `SUPERSEDE` = write new + set `superseded_by` on the contradicted row + `invalidated_at` — **never a physical DELETE**; every verdict lands in the `memory_events` WAL so any decision is undoable. Contradictions where the Decide model is unsure keep BOTH rows linked by a `references` edge + a conflict flag (GBrain: "note the contradiction with both citations, don't silently pick one"), surfaced in the lint.

This prevents duplicate/contradictory accumulation and gives controlled forgetting a principled path — while staying inside propose-don't-write: destructive verdicts are supersessions with provenance and undo, and the vault (§5) makes every one human-reviewable as a plain-text diff.

### 4.2 Takes/claims — optional holder attribution axis

Semantic rows gain an optional `holder` (migration v7 column, default NULL = plain fact): `user | assistant | person:<entity_id> | external`, plus `weight` in **0.05 increments** (no false precision). Caps encoded as validation, not vibes: self-reported facts ≤0.75; secondhand/amplified claims ≤0.55. Key-prefix `claim.*` (added to `_BUILTIN_PREFIXES`) marks explicit takes; kind inference stays key-prefix-based (no kind column — recon invariant). The injection path's fact block renders holder-attributed claims with attribution ("Alex believes…"), and the Decide phase (§4.1) uses holder precedence (user statement > compiled synthesis > external) when adjudicating contradictions. Deliberately small: an axis on existing rows, not a claims subsystem.

---

## 5. The Readable Vault (extends `memory_vault.py`)

### 5.1 From read-only mirror to two-way projection

`memory_vault.py` already mirrors memory to `~/.personalclaw/memory-vault` on consolidation. It becomes mode-driven: `memory.vault_mode: off | mirror | two_way` (back-reads legacy `vault_enabled` on load — the orchestrator/conductor rename precedent). Layout:

```
memory-vault/
  index.md            # the always-injected bounded summary table (§5.3)
  entities/<slug>.md  # one page per mem_entity: compiled truth + timeline
  facts/<domain>.md   # semantic keys grouped by prefix (pref/, project/, user/)
  episodes/YYYY-MM/…  # episodic digests (read-only even in two_way — evidence is immutable)
  slots/<name>.md     # §6 registers — THE primary slot editor
  raw/                # watched capture dir → routed to KNOWLEDGE ingestion (§5.5)
```

Every page: YAML frontmatter (`type`, `title`, `aliases`, `sources`, `connects`, `source_hash`, `last_updated`) + `[[wikilinks]]` generated from `mem_links` edges. Entity pages follow GBrain's **compiled truth + append-only timeline** shape: consolidation rewrites only the compiled section; dated evidence lines below are never rewritten — the answer to "stale vs unbounded."

### 5.2 Edits flow back

Not via fs_watch (that's the FE SSE refresh engine — recon) and not via a daemon: a **vault sync pass** runs (a) on the consolidation cadence and (b) on demand (`POST /api/memory/vault/sync`, MemoryPanel button). Mechanism:

- Each mirrored page's frontmatter carries `source_hash` = hash of the projected content at write time. Sync compares disk hash vs `source_hash`: unchanged → skip; changed → parse the human's edit.
- Parsed edits apply **through the normal MemoryService write path**: same key validation, same S5 injection scan (`_memory_write_blocked` — a vault file can contain pasted untrusted text; human-authored ≠ safe), same `memory_events` WAL row (`source: vault_edit`) — fully undoable.
- **Human edits are authoritative** (the propose-don't-write inversion: the human edits, the assistant proposes). A conflicting concurrent store write is resolved edit-wins with the store version preserved in the supersession chain; a genuinely unparseable page gets a `⚠ sync-conflict` frontmatter flag + lint entry, never a silent overwrite and never data loss.
- Deleting a vault page proposes deletion (lint queue) — it does not delete store rows.

### 5.3 Structure conventions + lints (three-type split)

Pages carry `type: entity | concept | connection | qa | slot | synthesis` and may declare `connects: [[A]] ↔ [[B]]` frontmatter (connection pages name the relationship between two entities). Deterministic vault lints (folded into §2.3's lint pass): **backlink symmetry** (if A links B, B's page lists A in its backlinks section), **stale source-hash** (page claims a source whose hash changed), broken wikilinks, orphan pages. `index.md` is a generated bounded table (≤2k chars: entities by community, slot summaries, freshness) — the vault's always-injected summary, injected via the L1-manifest region rather than a new context block.

### 5.4 Memory citations in chat + admit-ignorance

Any memory-backed answer carries inline `[Memory N]` provenance chips: the injection blocks already know their source records; `recall_with_provenance` supplies `{key/id, evidence-arm}` per hit; the system prompt instructs citing injected memories by index; the FE renders `[Memory N]` tokens in `ui/Markdown.tsx` as chips deep-linking to `#/settings/memory?tab=inspect&key=…` (and to the vault page path). The same prompt addition carries the **admit-ignorance clause**: when recall returns nothing relevant, say so — never confabulate a remembered fact. Cheap, prompt+renderer level, no new tool.

### 5.5 Interop: symlink, raw/ capture, starter seeding, export

- **Obsidian symlink**: the vault is plain files — `ln -s` into any Obsidian vault gives graph view, Web Clipper, Dataview for free. A one-click "reveal/copy symlink command" affordance in settings; nothing to build beyond docs + the button.
- **`raw/` capture dir**: files dropped there are the *user's items* → routed into the **knowledge ingest queue** (`KnowledgeIngestQueue.enqueue` via `create_typed_item(provider="native")`), NOT into memory — the boundary holds even inside the vault dir. The vault sync pass does the sweep (no new watcher).
- **Starter seeding**: agents/templates/apps may ship starter vault files + slot contents (a `memory_seed/` dir in the agent profile / app manifest `setup`); seeding writes **only missing files or pristine (hash-unmodified) indexes** — never over user-modified content. Enables shipped starter context without clobbering.
- **Static-site export**: `personalclaw memory vault export --html` — nice-to-have CLI, lowest priority, shares §7.2's renderer.
- **Git versioning**: not built here — the vault being plain files makes NEW-4's git snapshots cover it for free; `snapshot.py`'s `memory` component adds the vault dir (one `CORE_FILES`-adjacent tree addition).

---

## 6. Memory Slots — bounded, always-injected, user-editable registers

A **different primitive from searchable memory**: a small set of named registers, size-capped by construction, injected every session, editable by the human, appendable by reflection.

- **Storage**: semantic rows under key prefix `slot.<name>` (prefix added to `_BUILTIN_PREFIXES`; kind stays key-prefix-inferred — no schema change). Value cap enforced at put: default **2,000 chars** per slot (hard cap 8,000), append-over-limit **fails loudly** (agent-memory.dev rule) and becomes a trim proposal instead.
- **Built-ins** (created lazily, all optional): `persona`, `preferences`, `pending_items`, `self_notes`, `glossary` (the per-project domain-glossary variant — scope `workspace` via the existing `MemoryScope` axis, riding memory's cwd-partitioning), `self_model` (§6.1).
- **Injection**: one "Slots" block in `build_session_context`, positioned adjacent to the existing persona/USER PROFILE region (those two existing mechanisms are effectively proto-slots; they stay — slots generalize the pattern without migrating them in v1). Total slot budget ≤ N_slots × cap, bounded by construction — slots can never blow the context budget.
- **Editing**: MemoryPanel `studio` gets a Slots editor; the vault's `slots/<name>.md` pages are the plain-text editor (two_way sync applies). Both write through MemoryService (S5 scan, WAL, undo).
- **Reflection hook**: `after_turn_review` (the existing per-turn learning seam) may **append** into `pending_items`/`self_notes` — append-only, cap-guarded, WAL-logged, and each append is visible in the vault as a diffable line. It never rewrites slot content wholesale.

### 6.1 The self-model slot

The concrete filling mechanism for the reflection hook: a compact **private register of pattern observations and working theories** ("user prefers terse answers before 10am — 3 observations"). Mechanics:

- Observations accumulate as candidate lines with a reinforcement counter (reusing the facet-style decay/reinforcement idea, but inside one bounded register).
- A candidate is **promoted to a behavioral principle** (moved above the fold, injected into planning prompts as part of the slot snapshot) only after repeated reinforcement pushes it over a confidence threshold (default ≥3 reinforcements — the same ≥3 constant the dream-promotion and skill-ladder gates converge on).
- Bounded (2k cap), inspectable (it's a vault page), propose-don't-write compatible (the assistant appends observations; promotions are visible; the human can edit or delete any line and the reflection hook respects human deletions via a tombstone comment).

---

## 7. FE Surfaces

All within existing surfaces (memory is a Settings panel, not nav — recon):

### 7.1 MemoryPanel extensions
- `studio` tab: Slots editor; entity browser (aliases editable); proposed-entity accept queue.
- `health` tab: orphans/phantom/symmetry lints; volunteered-vs-used per-arm precision; sync-conflict list.
- `inspect` tab: per-record backlinks + evidence tags (deep-link target for citation chips).
- `settings` tab: vault mode/path, push-context toggle + min-confidence, slot caps, topology-block toggle — all via `patchConfig` against the new `_EDITABLE_CONFIG` paths.

### 7.2 Graph visualization + export
`MemoryGraph.tsx` (exists) upgrades to the real graph: nodes = entities colored by Louvain community, edges typed/filtered by `link_type` + provenance + min-confidence slider, click-to-focus neighbor dimming, side drawer showing the entity's vault page markdown. A **self-contained HTML export** (JSON embedded, no server — llm-wiki-agent's `graph.html` pattern) ships as `GET /api/memory/graph/export` for sharing/archiving; the knowledge graph page (`KnowledgeGraph.tsx`) is untouched (it already exists over knowledge.db's own tables). Token-lint ratchet applies (no raw hex/px; canvas/SVG internals go through the existing EXEMPT_FILES route if needed).

---

## 8. Disposition Table

| Surface | Verdict | Detail |
|---|---|---|
| `memory_vault.py` (read-only mirror) | **EXTENDED** | becomes mode-driven `off|mirror|two_way`; layout+frontmatter conventions §5.1; sync pass §5.2. Legacy `vault_enabled` back-read on load |
| `memory.py` MemoryStore markdown projection + `memory_index.db` FTS | **KEPT** | remains the per-partition projection + FilesystemMemoryProvider FTS backing; the vault is the *global* human-facing projection. No merge in v1 |
| memory.db legacy `knowledge_facts`/`knowledge_edges` tables | **RETIRED/RENAMED** in migration v7 | adopt-and-rename if populated, drop if empty; `knowledge_*` names never again inside memory.db |
| `vector_memory.py` hybrid retrieval (0.6/0.4) | **EXTENDED** | gains the graph arm + backlink boost (§2.1); vec/kw math untouched |
| `heat()` / `promote_by_heat` | **KEPT — fed, not forked** | graph-arm recalls increment the same counters; no parallel promotion path |
| `history.py:_consolidate_locked` | **RESTRUCTURED** | Extract→Gather→Decide (§4.1); same use-case binding, one added structured call; post-step cadence gains Louvain + volunteer-event pruning + vault sync |
| supersession chain (v4) + `memory_events` WAL/undo | **KEPT — load-bearing** | Decide's UPDATE/SUPERSEDE verdicts, vault edits, and link writes all ride it |
| `memory_lint.py` | **EXTENDED** | orphans/phantoms/proposed-entities/vault lints (§2.3, §5.3) |
| `recall_with_provenance` | **EXTENDED** | evidence-arm tags; feeds citations + reflex stats |
| `after_turn_review.py` | **EXTENDED (append-only)** | slot reflection hook (§6); eligibility gates untouched (LEARNING-FLYWHEEL owns their evolution) |
| knowledge.db `entities`/`entity_relations`/`mentions` + `HybridRetriever` graph arm | **KEPT — enriched only** | deterministic alias pre-pass in the pipeline entities stage (§1.3); zero schema change |
| knowledge no-auto-inject invariant | **KEPT** | push reflex volunteers knowledge as chips, never context (§3) |
| `MemoryProvider` dormant lifecycle hooks (`on_turn_start`…) | **NOT wired here** | reflex rides context_engine; hook wiring stays a C27/flywheel decision |
| `preference_facets.py` USER PROFILE block / persona keys | **KEPT** | proto-slots; coexist with the Slots block in v1; unification deferred |
| `MemoryGraph.tsx` / MemoryPanel | **EXTENDED** | §7 |

---

## 9. What We Deliberately Do NOT Build

- **No graph database, no embedding-based entity resolution at write time** — alias/mention matching only; `same_topic` inference happens where embeddings already exist (consolidation).
- **No LLM in the write path or the push reflex** — the entire NEW-3 write-time mechanism is zero-token by design; that IS the recommendation.
- **No auto-created entities** — notability gate + ≥3-mention proposal queue; junk degrades recall.
- **No physical deletes from Decide** — supersession + WAL only.
- **No knowledge.db schema changes, no cross-store FKs, no memory→knowledge writes** (raw/ routes *user files* to knowledge ingestion — that is knowledge ingesting user items, not memory writing knowledge).
- **No ambient knowledge injection** — chips only; the @-picker stays the human gate.
- **No daemon/watcher for vault edits** — cadence + on-demand sync.
- **No multi-person belief subsystem** — holder attribution is one optional column axis (§4.2), full claims machinery explicitly out.
- **No per-partition sub-vaults in v1** — the vault mirrors the main store.
- **No new nav page, no new notification path, no new injection pipeline** — Settings panel, existing gates, existing context-build order.

---

## Provider & Config Plug-in Map

Where each piece plugs into the pluggable-provider architecture (recon: providers.md) — nothing invents a parallel extension path:

- **Memory provider seam:** all graph/vault/slot intelligence lands in `VectorMemoryStore` + `MemoryService` — the NATIVE memory provider (`native-vector-memory` app manifest, `provider.type: "memory"`). `MemoryCapabilities` gains an advisory `entity_graph: bool`; `MemoryService` guards graph ops on it exactly as it guards `vector` — a foreign `MemoryProvider` registered via `memory_providers.registry.register_provider(name, p)` degrades to today's CRUD/FTS behavior (the existing `_vs is None` posture; recon confirms foreign providers already get the degraded path).
- **Knowledge seam:** the alias pre-pass is a change *inside* `knowledge/pipeline/` (the entities terminal stage) — the `KnowledgeProvider` ABC, registry, and the uber-pool `items` model are untouched; a future Google Drive/Photos provider still plugs into `knowledge_providers` unchanged and its ingested items get alias-matched for free through the one queue.
- **No new action providers** → `ALLOWED_HOOK_PROVIDERS` (validation.py:555) is **untouched** — this plan adds no hook-fireable actions. (If a later phase adds a `vault-sync` action for the automation substrate, it follows the app-delivered action rule + allowlist addition; explicitly out of v1.)
- **No new provider types** → `PROVIDER_TYPES` and the `_TypeHandler` set are untouched (the #47 both-sides guard is moot here).
- **Config — the FOUR wiring points** (recon: persistence-security gotcha #1) for every new `MemoryConfig` field — `graph_enabled` (default true), `graph_topology_in_context` (false), `push_context_enabled` (false, opt-in), `push_context_min_confidence` (0.7), `push_context_max_items` (3), `vault_mode` (`mirror`; back-reads `vault_enabled`), `slots_enabled` (true), `slot_size_cap` (2000), `holder_attribution` (false): (a) dataclass field with `_meta(label, help)` (schema reachability tests enforce), (b) `AppConfig.load()` explicit field-by-field mapping (omission = silently dropped — the MemoryConfig comment at loader.py:1689 records exactly this bug class), (c) `to_dict()` (asdict per-section — MemoryConfig exists, so mostly free), (d) `_EDITABLE_CONFIG` PATCH allowlist + `api.patchConfig` for the runtime-editable knobs in §7.1.
- **LLM resolution:** the Decide phase rides the existing `memory_consolidation` use-case binding; nothing new touches the chat/code_tools axis (recon: those return the NativeAgentRuntime — background callers stay on their own use-cases).
- **Security chokepoints reused:** S5 write-injection scan on vault-edit ingestion and slot writes; `session_restrictions` checks on the push reflex; `memory_events` WAL + `undo_event` for every graph/vault/slot mutation; SEL audit rows for vault sync-conflict resolutions. No new fencing sites (vault content is user-authored memory, not inbound untrusted payload — but the S5 scan still runs because pasted content is a real vector).
- **FE plug-ins:** MemoryPanel tab extensions + `ui/Markdown.tsx` citation-chip rendering + `MemoryGraph.tsx` upgrade — no `NAV` change, no new route; api.ts gains `memoryVaultSync`, `memoryGraphExport`, `memorySlots*`, `memoryVolunteerStats` methods (flat-file merge-conflict surface noted; land them in one PR).

---

## Implementation Effort

**~5 sessions:**

- **Session 1 — graph data model + write-time linker:** migration v7 (`mem_entities`/`mem_links`/`mem_link_stats`, holder column, legacy `knowledge_facts/edges` audit), alias index (3 seed sources), deterministic linker on all three write paths, WAL link events, orphans/proposed-entity lint. Backfill pass linking existing 334 semantic + 234 episodic rows (idempotent, batched).
- **Session 2 — recall arm + push reflex:** graph arm + backlink boost in hybrid retrieval, evidence tags in `recall_with_provenance`, push-context reflex + volunteer-events table + per-arm stats, knowledge-pipeline alias pre-pass, restricted-session guards.
- **Session 3 — formation:** Extract→Gather→Decide restructure of `_consolidate_locked` (supersession mapping, conflict-keep-both), holder-attribution validation + fact-block rendering, Louvain post-step + topology block.
- **Session 4 — vault two-way:** vault_mode config (+legacy back-read), layout/frontmatter/wikilink generation from `mem_links`, sync pass (hash compare → MemoryService write path → conflict flags), vault lints, `raw/` → knowledge-queue sweep, starter seeding, snapshot.py vault-tree addition, memory citations (prompt + Markdown chip renderer + admit-ignorance).
- **Session 5 — slots + FE + wiring:** `slot.*` prefix + caps + built-ins + reflection append hook + self-model promotion logic, MemoryPanel tabs (Slots editor, entity browser, lint/stats surfaces, settings), MemoryGraph upgrade + HTML export, full FOUR-point config wiring, as-a-user validation sweep (write→link→recall→volunteer→edit-vault→undo round trips).

Graph viz polish and static-site export are the designated slip items if session 5 runs long.

## Success Criteria

1. Writing a memory that mentions a known person/project creates typed `mem_links` rows with zero LLM calls, visible in the inspect tab within the same request; the backfill links the existing store and the orphans lint reports a before/after count.
2. "What do I know about <entity>?" recall returns graph-arm hits that pure vector/keyword recall missed (verified on the live store), each tagged with its evidence arm; graph-arm recalls increment recall_count and a well-linked record crosses the `promote_by_heat` gate through normal use.
3. The push reflex volunteers ≤3 confidence-gated memory records on a turn that names a known entity, never injects a knowledge item (chip only), stays silent on entity-free turns, and the health tab shows per-arm volunteered-vs-used precision after a week of use. Temporary sessions get nothing.
4. Consolidating a transcript containing a fact that contradicts a stored fact produces a SUPERSEDE (old row retained in the chain, WAL row undoable) or a flagged keep-both conflict — never a silent overwrite, never a physical delete; duplicate facts produce NOOP, not new rows.
5. Editing an entity's compiled-truth section in the vault (in Obsidian, via symlink) round-trips into memory.db on the next sync through the S5 scan, is undoable via `undo_event`, and a deliberately conflicting concurrent store write resolves edit-wins with the store version preserved in the supersession chain.
6. A file dropped into `vault/raw/` becomes a knowledge item through the one ingest queue — and nothing in memory.db; a `knowledge_*`-named table no longer exists in memory.db.
7. Slots inject every session within their caps, an over-cap append fails loudly as a trim proposal, and the self-model slot promotes a pattern line only after ≥3 reinforcements — with the human able to edit/delete any line from the vault and have it stick.
8. A memory-backed chat answer renders `[Memory N]` chips deep-linking to the exact record, and a question the store can't answer gets an explicit "I don't have that in memory" rather than confabulation.
9. All new config fields survive a load/save round trip (four-point wiring verified by the schema tests) and the runtime-editable ones toggle live via PATCH from the settings tab.
10. With `graph_enabled: false` (or a foreign memory provider bound), every surface degrades to today's behavior — no errors, no dead UI.
