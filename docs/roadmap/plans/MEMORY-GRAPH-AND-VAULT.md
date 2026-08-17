# MEMORY-GRAPH-AND-VAULT

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/MGAV.md`](../atomic/MGAV.md) as 9 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Memory Graph & Readable Vault — Typed Entity Linking, Push-Context, Slots

**Status:** IN PROGRESS — Session 1 (graph data model + write-time linker) shipped 2026-07-28;
Session 2 COMPLETE (recall arm 2026-07-28 + push reflex 2026-07-30); **the §1.3 knowledge-side alias
pre-pass LANDED 2026-07-30** (`knowledge/alias_prepass.py::link_known_entities`, called from
`knowledge/pipeline/runner.py`, PR #118) — this line previously claimed it was still open, contradicting
the plan's own log.
Sessions 3-5 (formation, two-way vault, slots + FE) not started: the vault is still the one-way
mirror, and `vault_mode` / the `slot.*` family do not exist. Status corrected 2026-08-04 by code audit.
(rev 2 — research-integrated 2026-07-12)

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

---

## Execution log

### 2026-07-28 — Session 1 (graph data model + write-time linker): DONE

Migration **v7** adds `mem_entities` / `mem_links` / `mem_link_stats` (plus
`mem_entity_proposals`, see below) to memory.db, with a deterministic zero-LLM
linker on the write paths, three-source alias seeding, an idempotent backfill, and
graph checks in the existing lint. Per the owner's ruling this extends memory.db's
own `_MIGRATIONS` ladder (v1–v6 house style) rather than waiting on the deferred
`lifecycle/` machinery. Class-B clean break under the pre-1.0 banner.

**E1 — PREMISE CORRECTION (logged, not guessed).** §1's "memory.db already carries
legacy `knowledge_facts`/`knowledge_edges` tables — a naming collision waiting to
bite… Migration v7 audits them: adopt-and-rename if populated, drop if empty."
**Those tables do not exist.** The v1–v6 ladder creates exactly `schema_version`,
`semantic_memory`, `episodic_memories`, `memory_events`; the owner's real store
(`~/.personalclaw/memory.db`, schema v6) carries only those plus
`sqlite_sequence`. The adopt-or-drop deliverable is a no-op, so v7 does not carry a
DROP for tables we never created. Instead it *warns* if one ever appears and leaves
it alone — an unexpected table in a user's store is evidence of something we don't
understand, and dropping is not reversible. Success Criterion #6's "a
`knowledge_*`-named table no longer exists in memory.db" holds vacuously and is now
test-asserted.

**Second premise correction:** §1 and the Session-1 line say the backfill links "the
existing 334 semantic + 234 episodic rows". The real store has **0 active rows in
both tables** (the counts are stale). The backfill is therefore validated against a
seeded fixture store rather than the live one; it is correct and idempotent, but
nobody should read the shipped numbers as evidence about the owner's data.

**Aho-Corasick → hand-rolled TOKEN TRIE.** No new dependency, following the
`SimpleDiGraph` precedent (which replaced networkx by hand in
`knowledge/store.py`). Tokenizing the text *and* the aliases gives word-boundary
matching for free — "Ann" cannot match inside "Announcement" because they are
different tokens, where a character automaton would need explicit boundary checks
at every hit. Longest match wins, so "Keyur Golani" beats a bare "Keyur".
Single-token aliases shorter than 3 chars are refused outright: "AI"/"ML" as bare
words generate far more false links than true ones, and a false link pollutes
ranking for every future query.

**DEVIATION — a fourth table (`mem_entity_proposals`).** §1.1 specifies the
notability gate as "unknown-mention counts accumulate in a scratch tally". A tally
that must survive restarts and count *distinct records* is a table; keeping it in
memory would reset the count on every gateway restart and never reach the
threshold. It counts distinct `from_ref`s, not mentions — one chatty record
repeating a name ten times is not evidence that the name matters (test-asserted).

**Reversibility.** Every link add/remove appends a `memory_events` row and
`undo_event` gained a `link` branch, so graph writes undo through the machinery
that already exists. The event payload carries the whole edge, so add and remove
are exact inverses regardless of current state. Verified end-to-end through
`POST /api/memory/events/{id}/undo` on a running gateway.

**Deferred to Session 2 by design:** the graph *recall arm* — this session ships the
skeleton and the writes, not the retrieval fusion, backlink ranking boost, or
evidence tags. `graph_backlinks()` already answers "what do I know about X?"
structurally and is what S2's arm will consume.

**Fold-in from TEAM-SHARED-ENTITIES S1:** the deferred memory contributor stamp
(`holder` column) is NOT in this change after all. It belongs to §4.2's holder
attribution, which is Session 3's scope, and adding a column nothing reads or
writes would be dead schema. Re-deferred to the session that actually implements
attribution — noted so it isn't lost a second time.

**Four validation findings (all found by driving the real API, none by unit tests):**

1. **Health-tab noise.** With no entities declared, *every* record is trivially
   unlinked, so the lint reported an orphan flag per record — technically true,
   completely unactionable, and it buried the one useful signal. The graph checks
   now stay quiet until at least one entity exists; the proposal queue is what
   surfaces in that state.
2. **Stale proposals.** After seeding created "Dana Whitfield" as an entity, the
   health tab still asked the user to accept her as a proposal. `proposals()` now
   filters (and prunes) names that have since become an entity or an alias —
   filtered at READ time rather than requiring all four entity-creation paths to
   remember to clear the tally.
3. **Lowercase entity names.** Record keys are lowercase slugs, so seeding
   `project.personalclaw.stack` produced an entity literally named
   "personalclaw". The seeder now recovers the author's capitalization from the
   record's own text ("PersonalClaw runs on aiohttp…" → "PersonalClaw"), falling
   back to the slug when the text offers no evidence.
4. **The kill switch wasn't live.** The store captured `graph_enabled` at
   construction, so flipping the Settings toggle updated config.json while the
   running gateway kept linking. `graph_enabled` is now a live-reading property
   (the `tools.groups_enabled` house pattern), fail-SAFE to ON — losing free
   deterministic linking is a worse surprise than doing it. Verified OFF→ON→OFF on
   a running gateway with no restart.

**Config:** `memory.graph_enabled` (default true) wired four-point — dataclass +
`_meta`, `load()` via `_guard_flag` (guard polarity: ambiguity keeps it ON),
`to_dict()`, `_EDITABLE_CONFIG` PATCH allowlist — plus the `/api/memory/settings`
PUT allowlist (a SEPARATE list from the PATCH one; missing it was a real bug in
TEAM-SHARED-ENTITIES S1 and was wired here from the start) and a Settings → Memory
toggle. `MemoryCapabilities` gained the advisory `entity_graph` flag; a foreign
`MemoryProvider` or the flag off degrades every surface to today's behavior
(test-asserted).

**Product surface:** `GET/POST /api/memory/entities`,
`GET /api/memory/entities/{id}/backlinks`, `POST /api/memory/entities/proposals`,
`POST /api/memory/graph/rebuild`, and an Entity-graph section in the Memory panel's
Health tab (entity list with inbound counts, expandable backlinks, add-with-aliases,
rebuild with a before/after report).

**Validated as a user** on an isolated dev home seeded with 3 semantic + 3 episodic
records: rebuild seeded 2 entities from facts and linked 6/6 records → 6 links, 0
orphans; "what do I know about PersonalClaw?" returned all three edge types
correctly typed (`about` for the persona record, `same_project` for the
project-keyed one, `mentions` for the episode); manual add + all three input
validations; the kill switch both directions; undo removing exactly one link. No
errors or tracebacks in the gateway log.

**Tests:** `tests/test_memory_entity_graph.py`, 97 cases. Also updated
`test_memory_record.py` (the new capability field) and regenerated the offline
agent reference (`manifest_reference`) for the five new routes. Full suite 8505
passed in 48.5s (baseline); lint clean; web typecheck/tests/build clean.

### 2026-07-28 — Session 2 (partial): the graph recall arm (§2.1–2.2) — DONE

The third retrieval arm. `MemoryGraph.resolve_query` / `recall_refs` /
`recall_evidence`, wired into the hybrid scorer in `get_semantic_context` through
`VectorMemoryStore._graph_boosts`, plus `MemoryService.graph_recall_evidence`.

**Entity resolution uses the SAME matcher as write time.** Without that symmetry the
arm would go looking for links the linker never made — the query has to resolve
exactly the way the record that mentioned the entity did.

**Boosted AND admitted.** A record linked to an entity the query names enters the
candidate set even when it shares no words with the question. That is the recall
similarity search structurally cannot reach, and it is the whole point of the arm.

**A REAL RANKING BUG, found by driving recall rather than by unit tests.** §2.1
specifies the boost as `β·log1p(inbound_count)` with β≈0.1. Implemented literally, a
lightly-linked entity yields ≈0.069 — which LOSES to the ~0.1 an unrelated record
earns from incidental stopword overlap ("is", "on"). Asking "what is Sparrow working
on" returned the record about Sparrow **third**, behind a note about a coffee machine.
The arm was working and the ranking made it useless. Added `GRAPH_BOOST_FLOOR = 0.25`:
naming an entity is a deliberate, high-signal act and must clear that noise floor,
while staying well under what a genuine keyword match scores so typed words still win.
Both directions are now test-locked
(`test_a_named_entity_outranks_incidental_word_overlap`,
`test_a_real_keyword_match_still_beats_the_graph`).

Worth recording as a class: a ranking weight specified in the abstract can be
arithmetically correct and behaviorally wrong. The only way to find that is to run a
real query against a real store and look at the ORDER.

**§2.1 step 4 needs no new code** — graph-arm hits flow through the existing
`record_recall`, so a well-linked record accrues `heat()` and becomes
`promote_by_heat`-eligible through the normal path. The graph feeds the existing
promotion math rather than adding a parallel one, which is what the plan asks for.

**§2.2 evidence tags** ship as `recall_evidence` → `{record: [entity names]}`,
answering "why did recall show me this?". A graph hit that cannot name the entity that
connected it is an unfalsifiable claim.

Degradation is total and tested: graph off, empty, or raising → `{}` boosts and
today's vector+keyword behavior exactly.

**NOT in this session:** the push-context reflex (§3), the knowledge-pipeline alias
pre-pass (§1.3's knowledge side), and the volunteer-stats table — the rest of Session
2. This slice is the retrieval win and stands alone; the reflex is a separate
opt-in surface with its own config and restricted-session rules.

Tests: 26 new cases in `tests/test_memory_entity_graph.py` (113 total). Validated as a
user on an isolated gateway: `/api/memory/recall?q=what+is+Sparrow+working+on` returns
the linked record FIRST via a nickname sharing no characters with the stored text,
while `q=coffee+machine+floor` still ranks by words. Full suite 8712 passed; lint clean.

### 2026-07-30 — Session 2 (remainder): the push-context reflex (§3) — DONE

The reflex volunteers memory the conversation is implicitly *about*. Migration **v8** adds
`mem_volunteer_events`; `memory_push.py` owns the per-arm resolver; `MemoryService.push_context`
picks records and logs events; the injection rides `context_engine.assemble` on **every** turn.

**E1 — PREMISE CORRECTION (the reflex would have gone silent after turn 1).** §3 says the
reflex "rides the proven context_engine seam" — the seam at `context_engine.py:109`, which
calls `active_recall_block`. That call is gated on `is_new_session`, and `is_new_session`
tracks the **runtime client** (recreated between turns, on idle eviction), NOT the
conversation — `chat_runner.py:1416` says so in its own comment. A reflex sharing that guard
fires once per client and then goes quiet, which is the opposite of "ambient, per-turn". The
seam was right; the *condition* was wrong. The push block is therefore a sibling branch with
no `is_new_session` term. Verified by calling `assemble_context` with `is_new_session=False`
against a real store: the reflex injects. Under the plan's literal reading it would not have.

**E1 — PREMISE CORRECTION (the cited restriction check was wrong twice).** §3: "the reflex
checks `session_restrictions.is_restricted` exactly as the recall endpoint does." Neither half
holds. The recall endpoint gates reads on `_blocks_reads_session`/`is_temporary`
(`handlers/memory.py:687`), and `is_restricted` is the **write** gate — true for incognito too
(`session_restrictions.py:5-9`, `state.py:563-571`). Using it for reads would have silently
killed the reflex in incognito, contradicting §3's own next sentence. Corrected: reads gate on
`blocks_reads`, the volunteer **log** gates on `is_restricted` (threaded as a new
`blocks_writes` kwarg, popped in `assemble` because `build_message` has an explicit signature).
Verified live — temporary: no injection; incognito: injection with **0 events logged**.

**A REAL BUG the plan's headline example would have hit, found by testing against the real
tokenizer.** §3 names `@handle` as the strongest arm. But `_tokenize` strips `@`, so a mention
of `@sparrow` returns `matched="sparrow"` — byte-identical to the entity's own NAME for the
common `@sparrow`/`Sparrow` pair. Classifying on `matched` alone made **every `@handle` hit
report as `exact_name`, so the alias arm could never fire at all** — the per-arm stat §3 exists
to produce would have had a permanently empty column. Fixed by recovering the sigil from the
source text at the mention's span (`_sigil_before`); only the abutting character counts, so
"email @ Sparrow" is still `exact_name`. Both directions test-locked. This was invisible to a
stand-in matcher, which is what hid it initially — the test that catches it drives the real
`AliasIndex`.

**`recall_at_volunteer` is the load-bearing column.** "Used" = the record's `recall_count`
having risen SINCE it was volunteered, so the count at volunteer time must be captured then.
Against an absolute count, every already-popular record would score as used and precision would
flatter the reflex no matter how it behaved. Test-locked with a record recalled *before* the
reflex ever offered it (n=1, used=0 → then used=1 after a real later recall).

**Episodic records are never volunteered.** An episodic row is a conversation fragment;
volunteering one pastes old dialogue into a new turn as if it were a fact. Also: the precision
query counts only `from_kind='semantic'`, because `record_recall` bumps `recall_count` on
`semantic_memory` only — including episodic rows would drag every arm's ratio toward zero for a
reason unrelated to volunteer quality.

**Design decisions worth recording:**
- **Own timeout + breaker (400ms, 3 strikes)**, tighter than active recall's 1500ms: this runs
  on the critical path of *every* message, not once per session.
- **Config read LIVE per turn** (`_push_settings`), because S1 shipped a captured-at-construction
  kill switch and flipping it did nothing until restart. Same mistake avoided deliberately.
- **`push_context` defaults FALSE and parses as a plain bool, not `_guard_flag`** — that helper
  fails ON, which would have enabled volunteering for every existing user on upgrade. Same
  shape as `vault_enabled`.
- **`HARD_CAP_RECORDS = 5` enforced in the service**, so a config value cannot raise the ceiling.
- **The UI refuses to report a precision below 10 events** (`VOLUNTEER_MIN_N`), mirroring
  `feedback.producer_stats`' min-N discipline: "0% precision" off two events is misleading.
- **The volunteer log stores no conversation text** — entity, arm, confidence, ref only
  (test-asserted, including that no column named text/content exists).

**Config:** `memory.push_context` + `memory.push_min_confidence` wired through the dataclass +
`_meta`, `load()`, `to_dict()`, `_EDITABLE_CONFIG`, **and** the separate `/api/memory/settings`
PUT allowlist + its GET mirror (three places in that handler), plus Settings → Memory controls.
`test_config_roundtrip` caught the clamped-probability case and took a `_SPECIAL` entry, the
same treatment `agent.soft_stop_budget_secs` already has.

**Product surface:** `GET /api/memory/volunteer-stats` + a "Volunteered memory" section in the
Memory panel's Health tab (volunteered / used-after / precision, per-arm breakdown, the current
gate) + the two Settings toggles, with the volunteer switch **disabled when the entity graph is
off** (it depends on it).

**Validated as a user** on an isolated dev home: entity created and linked through the real API;
the reflex injected the fenced block against the real store; `is_new_session` False still fired;
temporary blocked, incognito injected-without-logging; precision moved **0.0 → 1.0** through the
real `/api/memory/recall` endpoint bumping `recall_count`; both config write paths round-tripped
with out-of-range and bad-type both rejected (400); graph-off degraded to `enabled:false` + empty
block with no restart; Health tab and Settings rendered and the dependency-disable worked in the
browser. **0 console errors; 0 gateway tracebacks from the new code** (the log's only two are
`no model provider resolves` — expected with no model bound).

**Gates:** `make lint` clean (mypy 551 files) · backend **9080 passed** · web **283 passed** +
typecheck + build. Offline agent reference regenerated for the new route.
Tests: `tests/test_memory_push_reflex.py`, 46 cases.

**Still NOT in this session** (the rest of §1.3 / later sessions): the knowledge-pipeline alias
pre-pass, and Sessions 3-5 (formation, two-way vault, slots + FE). The knowledge-side pre-pass is
a knowledge-repo-adjacent change with its own ingestion seam and is cleanly separable.

- 2026-07-30 — **DONE (§1.3 remainder: the knowledge-side alias pre-pass).** The piece
  deliberately left out of the push-reflex session as cleanly separable; this is its own
  ingestion seam.

  **The gap.** The pipeline's entities stage was **LLM-only**: `EntityExtractor` asks a model
  what the item is about, and mentions/relations/graph edges all hang off that one call. So with
  **no model bound, nothing linked at all** — a user running local-only, or between providers,
  ingests a document that plainly names a known entity and gets zero mentions. The graph looks
  empty because the extractor never ran, not because the document said nothing. Separately, a
  model *misses* what a trie hits every time: an entity named once in passing, or written as a
  declared alias rather than its canonical name.

  **The fix.** `knowledge/alias_prepass.py` walks the item text with the **same `AliasIndex`**
  the memory store uses (one matcher, so the knowledge graph and the push reflex agree what a
  mention is — two would drift, and the symptom would be a document that links in one surface
  and not the other). It runs BEFORE extraction and **unconditionally**, including when
  `pool is None`. It ADDS only: it can link to entities that already exist, never discover new
  ones, so extraction still does the discovering. `add_mention` is `INSERT OR IGNORE`, so an
  entity both stages find is one mention.

  **The bug the tests caught, and it is subtle.** `clear_item_entities` (which the extraction
  path calls before re-writing, so a re-ingest doesn't dup) deletes this item's mentions **AND
  any entity left with no mentions and no relations**. So my first shape — re-run the pre-pass
  after the clear — returned **0**: on a small store the pre-pass's entity had itself been
  deleted, and the index it rebuilt was empty. Measured, not assumed. The working shape
  SNAPSHOTS `(name, entity_type, context)` before the clear and restores after, because a name
  can be re-found or re-created whereas an id cannot. Without this, every item WITH a model
  would silently lose its deterministic links — the pre-pass would appear to work only in the
  no-model case, which is the harder failure to notice.

  **A test of mine that would have hidden it.** The regression test first asserted on entity
  **ids**; the restored entity is a NEW row with a new id, so the test failed on correct
  behavior. Re-expressed to assert linked **names** — the link is what must survive, not the
  identifier. An id-based assertion there would have sent the next reader hunting a phantom bug.

  Bounded deliberately: `MAX_INDEXED_ENTITIES` (5000) so a pathological store can't turn every
  ingest into a pause, and `MAX_MENTIONS_PER_ITEM` (60) so a glossary page can't attach itself to
  the whole graph and drown the real signal. One mention per DISTINCT entity, since forty hits of
  one name is forty identical rows the `INSERT OR IGNORE` would collapse anyway.

  **Validated as a user** on an isolated dev home (port 10748, never :10000), through the REAL
  HTTP ingest with **no model bound**: a note reading "The SPRW cutover is Tuesday" linked the
  **Sparrow** entity via its declared `SPRW` alias, with the surrounding text recorded as
  context. Before this change that ingest produced zero mentions.

  *Two false trails worth recording so the next person doesn't repeat them:* (1) the gateway's
  knowledge store lives at **`workspace/knowledge/knowledge.db`**, not the home root — my first
  seed went to the wrong file and looked like a failure; (2) a seeded entity with no mentions is
  an **orphan**, so the next ingest's `clear_item_entities` deletes it before the pre-pass can
  ever match. A realistic fixture anchors the entity with at least one mention.

  **Gates:** `make lint` clean (mypy 556 files) · `make test` **9465 passed, 0 failed**.
  Tests: `tests/test_knowledge_alias_prepass.py`, 26 cases.

- 2026-07-31 — **DISCOVERY + FIX (an embedding-model change silently stopped all memory).**

  Not a planned task — found while seeding a marketing instance for the 0.1.3 release. Every
  episodic write was raising `AssertionError` from **inside** `faiss.IndexFlat.add`, whose width
  check is a bare `assert d == self.d`: no dimensions in the message, raised from a library
  frame, and it took the whole write with it. The agent had simply stopped remembering, and
  nothing said so.

  **Root cause was TWO things, and separating them mattered.** The instance's on-disk index was
  384-dim while every stored vector — and the bound `qwen3-embedding:0.6b` — is 1024, so the
  index predated a model change and had never been rebuilt. That is a *data* fault, repaired by
  rebuilding at the real width (the same store then read 72 semantic / 13 episodic / 161 events).
  The *code* fault is that this was allowed to be silent, and that is what got fixed.

  **`rebuild_faiss_index` already skipped-and-warned on exactly this mismatch.** The
  inconsistency was that no other path did. Three were unguarded, and the tests found the two the
  first pass missed:

  1. `write_episodic`'s `index.add` — the reported crash.
  2. **Both `search` calls** — dedup-on-write and semantic recall. `faiss.search` asserts its
     QUERY width the same way `add` asserts the added vector's, so a query from the new model
     raised too. Recall now returns empty and lets the caller fall back to keyword search.
  3. `promote_episodic_patterns`, which `np.dot`s stored vectors against each other: mixed widths
     raise `ValueError`, so ONE stale row would abort the whole consolidation pass. Rows are now
     filtered to the current width first — a similarity between two different models' spaces is a
     number without a meaning anyway.

  Every case keeps the memory text and its stored embedding, so a later re-embed restores
  semantic recall; the warning names BOTH dimensions and says to re-embed, because a silent skip
  would be its own version of this bug. **Mutation-verified:** neutralising the guards fails 5 of
  the 7 new tests.

  *Lesson worth recording:* the crash was in `add`, so `add` looked like the fix. It took writing
  the tests to discover that `search` and the consolidation pass fail the same way for the same
  reason — a fix aimed only at the reported traceback would have left two of the three paths
  broken and looked complete.

  **Gates:** `make lint` clean (mypy 563 files) · `make test` **9759 passed, 0 failed**.
  Tests: `tests/test_vector_memory.py::TestEmbeddingDimensionChange`, 7 cases.

- **DONE — MGAV-7: memory citations in chat + admit-ignorance (§5.4).** Episodic-recall answers now
  cite facts inline as `[Memory N]`, rendered as chips that deep-link to the episode
  (`#/settings/memory?tab=studio&sel=epi:<id>`); the system prompt gains a cite-by-index clause
  (only when the block carries markers) and an unconditional admit-ignorance clause when an episodic
  block is injected. Implemented at the prompt + renderer level with **no new tool or API route**:
  `get_episodic_context(citations_out=...)` numbers only emitted fragments and appends a
  `{n,id,preview}` manifest; the engine threads it via `AssembledContext.metadata`; the chat runner
  stamps it onto the assistant message's existing `meta` channel (persists to disk, rides the wire
  via `_prepare_messages`); the FE hydrates it onto the turn and resolves each token by **record id**
  (never the model's echoed text), so a mis-cited/hallucinated index degrades to plain text.
  **DEVIATION (recorded):** the plan's `?tab=inspect&key=…` target was imprecise — episodics live in
  the Memory **Studio** keyed `epi:<id>`, so the deep-link uses `?tab=studio&sel=epi:<id>` and a
  small `initialSel` preselect was added to `MemoryStudio`. On a new session, episodic content can be
  injected by two paths (the canonical block + active-recall's fenced block); only the canonical
  block is cite-numbered, to keep `[Memory N]` a single unambiguous set. Non-citation callers are
  byte-identical (param defaults to `None`). **Gates:** `make lint` clean (black+isort+flake8+mypy
  692 files); backend `tests/test_vector_memory.py::TestEpisodicCitations` (4) +
  `tests/test_context.py` citation clauses (3) + 152 context/memory tests pass; FE `npm run
  typecheck` clean + `Markdown.citations.test.tsx` (4) pass.

- **DONE — MGAV-5: memory formation (§4.1 Extract→Gather→Decide, §4.2 holder attribution, §2.4
  Louvain topology).** All three clauses landed. `memory_formation.py` restructures
  `_consolidate_locked`: the existing prompt is Extract, `gather()` is a fully deterministic
  overlap pass (same key → shared dotted key namespace → keyword overlap → the §2.1 graph arm, zero
  model calls), and Decide is **ONE structured call for the whole batch**, built only when some
  candidate actually has an overlap. That gate is the point: with no collisions every verdict is
  `ADD` by construction, so a normal consolidation still costs one call and "one extra cheap call"
  stays literally true. `UPDATE` = same-key put; `SUPERSEDE` = write the new row **then**
  `supersede_semantic` (that order matters — superseding first opens a window where neither value is
  live); **nothing physically deletes**, the old row keeps `superseded_by` + `invalidated_at`, stays
  readable, and every verdict lands in the `memory_events` WAL. Every failure path (unresolvable
  snippet, no model, garbled verdicts, a Gather exception) degrades to "ADD everything" — exactly
  today's behavior, because adjudication is optional and the user's facts are not.

  **Unsure keeps BOTH, visibly.** An unsure `SUPERSEDE`/cross-key `UPDATE` retires nothing and
  records the contradiction twice: a `references` edge with `provenance='conflict'` (what the lint
  reads, what §7.2's viz can draw) plus a `conflict_keep_both` WAL event (what survives with the
  graph off). The lint's new `keep_both` flag is deliberately **not** gated on `graph_enabled` —
  it is a data-safety notice about semantic rows, and hiding it behind an unrelated toggle would
  recreate the invisible-contradiction failure that keeping both exists to prevent. Resolving the
  pair clears the flag.

  **§4.2:** migration **v10** adds `holder` + `weight` (guarded idempotent `ALTER`, no backfill —
  every pre-existing row genuinely IS an unattributed plain fact, and back-stamping would invent
  provenance). `memory_holder.py` quantizes weight to 0.05 and **clamps** to the class ceiling
  (self-report ≤0.75, secondhand ≤0.55) rather than rejecting: losing a memory because a model
  over-claimed its strength is the worse bug. A plain fact is never re-weighted and renders
  byte-identically; `holder=None` means "don't touch", so a plain rewrite can't turn a recorded
  claim into an asserted fact. `claim.*` joined `_BUILTIN_PREFIXES` (kind inference is key-prefix
  based — no kind column, per the recon invariant). Both fact-block paths render
  `[<who>, weight <n>]` with a fence clause; precedence (`user` > plain/`assistant` > `external`
  /`person:*`) is enforced **at the decision point**, so a lower-authority claim cannot supersede a
  higher-authority one at all — the reverse direction still supersedes, which is the vacuity guard.

  **§2.4:** `memory_topology.py` runs a seeded Louvain on the existing consolidation maintenance
  cadence (no new loop), writes `community` into `mem_link_stats`, and materializes a ≤400-char
  block gated by `memory.graph_topology_in_context` (default off). "New sessions only" comes free:
  `get_context` is only reached from `build_session_context`. Determinism = sorted iteration
  everywhere + a per-call `random.Random(42)` + **canonical community numbering** by (size desc,
  smallest member). Aggregation carries intra-community weight as a **self-loop**; dropping it is
  the classic Louvain bug that collapses every graph to one community and then reads as "this graph
  has no structure" rather than as a defect.

  **DEVIATIONS (recorded).** (1) §4.1 asks Gather for a `vector_query` arm; in this schema
  embeddings live on `episodic_memories`, not `semantic_memory`, so a vector arm would search the
  wrong table or re-embed every fact each consolidation. The graph arm already covers the
  wording-independent case, so Gather is same-key + key-namespace + keyword + graph. (2) The holder
  columns arrive via the migration ladder only, not by also editing `_SCHEMA_V1` — v1's schema must
  stay v1's, and the ladder runs on a fresh DB (the `_migrate_v9`/`contributor` precedent).
  (3) Caps clamp instead of rejecting, per the reasoning above. (4) The Decide instructions ship as
  a bundled **snippet** (`memory-decide`), not a use-case-bound prompt: it is a fragment of the
  consolidation flow rather than a second consolidation a user would bind a model to, and rendering
  `""` when unresolvable is exactly the fail-safe Decide wants.

  **KNOWN TENSION (untouched, needs an owner call).** `memory_lint`'s pre-existing
  `_SUPERSEDED_RETENTION_DAYS` auto-fix physically DELETEs rows superseded more than 90 days ago.
  That is a shipped bounded retention policy with its own test and is not part of the supersede
  verdict path, so this atom left it alone — but it means "the superseded row stays readable" holds
  for 90 days, not forever. Worth deciding alongside §5.2's no-data-loss guarantee.

  **Falsified, not assumed** (every mutation reverted): `SUPERSEDE` → hard delete reds
  `test_supersede_keeps_the_old_row_readable` ("the superseded row was PHYSICALLY DELETED") and
  `test_row_count_never_drops_across_a_formation_pass` ("a formation pass removed rows from the
  table"); a per-run Louvain seed reds `test_the_same_graph_yields_the_same_communities_across_runs`
  and the cross-**process** test ("communities depend on the interpreter's hash seed"); deleting the
  precedence check reds "an outside rumour retired what the user said"; a no-op `_lint_conflicts`
  reds "an undecided contradiction was invisible in the lint". The determinism fixture graph was
  found by sweeping random graphs for one whose partition genuinely depends on visit order — a clean
  two-cluster fixture would have passed with the seed removed entirely, proving nothing, so
  `test_the_seed_is_load_bearing` asserts that at least two seeds disagree on it.

  **Config:** `graph_topology_in_context` + `holder_attribution`, both four-point wired (dataclass
  `_meta`, explicit `load()` mapping, `to_dict` via asdict, `_EDITABLE_CONFIG` PATCH allowlist) and
  surfaced in `/api/memory/settings`; `config-baseline.json` regenerated. FE controls are MGAV-9's
  declared scope, so that atom only has to render two toggles already in the allowlist.

  **Gates:** `make lint` clean (black + isort + flake8 + mypy 874 files) · new
  `tests/test_memory_formation.py` **47 passed** · all memory/history suites **764 passed** ·
  rails `test_config_baseline` / `test_config_roundtrip` / `test_inert_surface_baseline` /
  `test_portability` / `test_durability_inventory` / `test_resilience_degraded_lint` /
  `test_agent_reference` / `test_roadmap_dag_derived` **132 passed**.

### 2026-08-16 — Session 4 (§5.1–5.3, §5.5): the two-way readable vault — DONE (`MGAV-6`)

- **DONE `MGAV-6`.** `memory_vault.py` became mode-driven. `memory.vault_mode`
  (`off|mirror|two_way`) **replaces** `vault_enabled` as a clean-break rename on the
  `conductor_skill`→`orchestrator_skill` precedent, with a **one-way** back-read in `load()`:
  a legacy `true` loads as `mirror`, never `two_way` — reading a user's files back into memory
  is a new capability and must be chosen, not inherited on upgrade. An unrecognized mode falls
  back to the legacy read rather than to `off` (a typo must not stop a mirror someone is
  browsing); through `PUT /api/memory/settings` the same typo is a **400**, because coercing it
  would look like the setting saved while quietly turning the vault off.

  **`source_hash` covers the BODY ONLY, and that single decision carries the atom.** The sync
  rewrites frontmatter on every pass (heat counters, `updated_at`, the hash itself), so a hash
  spanning the frontmatter would make every page read as hand-edited forever — the mutation
  proving it reds three tests. Body-only buys the conflict flag for free: stamping
  `sync_conflict` into a page we refuse to touch leaves the body hash untouched, so the page
  keeps reporting itself unresolved instead of becoming "clean" because we wrote to it.

  **The refusal is the feature.** `absorb_edits()` runs BEFORE re-projection (otherwise the
  projection overwrites the edit before anything reads it). Applying one requires frontmatter
  `id`, a `kind` in `{semantic, preference, note, slot}`, an H1, and non-empty text between the
  H1 and the `personalclaw:generated` marker. Everything else — no heading, empty value, an
  episodic page (evidence is immutable, §5.1), a lesson's rule/counter-example structure — is
  left **byte-for-byte** as the human wrote it, flagged, and reported by the lint. Its manifest
  entry is carried over so the prune pass cannot delete the file the sync just declined to
  rewrite.

  **The write path.** `MemoryService.apply_vault_edit` → `set_semantic(source="vault_edit")`.
  `vault_edit` joined `user_explicit` in a new `vector_memory._HUMAN_AUTHORED_SOURCES` so
  conflict resolution lets it win; without that, editing a fact you originally typed is refused
  as "an automated source cannot overwrite a user fact" and the vault silently discards the
  change (that mutation reds five tests). It stays OUT of `_TRUSTED_WRITE_SOURCES` and out of
  the reserved `system.` prefix — authority over your own facts is not trust in the bytes.
  **Measured layer split:** `validate_semantic` already refuses instruction-override *prose*
  (`injection_blocked`), while ONLY the S5 `supply_chain` scan catches a **bidi override**
  (`validate_semantic` returns `None` for it). My first S5 test used prose and was vacuous; the
  real one uses `‮` and first asserts the deeper validator passes it, so the S5 call is
  proven load-bearing rather than redundant with a deeper default.

  **Three real defects found on the way.** (1) 🔴 `mirror` mode **never restored a hand-edited
  page**: the write-skip compared the freshly rendered digest to the *manifest*, which answers
  "has the projection changed", not "does the file still hold it". It now compares the bytes on
  disk. (2) 🔴 Every episodic page has emitted `**Session:** [[session-<id>]]` since the mirror
  shipped and **no such page was ever written** — Obsidian tolerates an unresolved link, so it
  hid until §5.3's broken-link check measured the vault against itself; `render_session_hub`
  makes them resolve. (3) 🔴 The seeded `raw/README.md` was **ingested into Knowledge by the
  sweep it documented**; `raw/` is now a pure drop box documented once in the top-level README,
  so the sweep needs no exception that would also swallow a README the user really dropped.

  **Durability (§5.5's snapshot clause).** `memory-vault` is a declared `StateEntry` (tree,
  domain `memory`, `replace_only`) — `audit_home()` reported it **unclaimed** the moment anyone
  turned the vault on, and the audit's synthetic fixture never contained one. Deliberately
  **not `derived=True`**: in `two_way` a page can hold an edit that exists nowhere else, so
  dropping it as "rebuildable" would lose the one thing in there that is not. Restoring is safe
  *because of* `source_hash` — a restored page still matching its own hash is recognized as an
  untouched projection and re-rendered from whatever the store now says, so only a genuine
  unsynced edit is read back, and that write rides the WAL. Only the DEFAULT path is declared; a
  relocated vault is the user putting state beyond the manifest's reach.

- **DEVIATION (§5.2 supersession chain).** "the store version preserved in the supersession
  chain" is served by the `memory_events` row instead: `undo_event` restores the exact prior
  value, whereas minting a synthetic superseded key would pollute the semantic keyspace with
  rows nothing reads. Edit-wins still wins; the concurrent store write is logged and surfaced.
- **DEVIATION (§5.1 `last_updated`).** Not emitted. `_FM_ORDER` already carries the record's
  `updated_at`, which IS the store version the pass compares against; two frontmatter keys for
  one fact would drift.
- **DEVIATION (§5.5 starter seeding source).** Seeds are the vault's own README rather than an
  agent-profile `memory_seed/` dir — `agents/` is a flat file, not per-agent directories. The
  missing-or-pristine primitive (`MemoryVault.seed`) is public, so an app's `setup` can call it
  with its own dict; adding a `memory_seed/` home directory would have needed its own inventory
  entry for one shipped file.
- **DEVIATION (§5.3 page types).** Only `concept|slot|synthesis|entity|session|tag|index|guide`
  are declared. `connection` and `qa` are in §5.3's vocabulary but nothing generates them, and a
  declared type nobody writes reads as a decision while behaving as an omission.
- **DISCOVERY (out of scope, left open).** §5.5's `--html` static export ("lowest priority",
  shares §7.2's renderer) and the reveal/copy-symlink affordance are not built. `index.md` stays
  `MEMORY.md` and is not injected into the L1-manifest region; the done_when names neither, and
  MGAV-9 owns the settings surface.
- **DISCOVERY (test hygiene).** Two of eleven mutations redded NOTHING, both my tests' fault:
  blanking the timeline merge passed because `mem_links` rows **survive a soft delete**
  (measured), so a rebuild-from-evidence produced byte-identical lines; and removing the S5 scan
  passed for the layer-redundancy reason above. A third gap: dropping `vault=vault` from
  `MemoryService.lint()` — the only thing `GET /api/memory/lint` calls — redded nothing, so the
  vault checks could have been complete and never reached the Health tab. All three now have
  tests that can fail.

  **Config:** `vault_mode` four-point wired (dataclass `_meta`, explicit `load()` mapping with
  the back-read, `to_dict` via asdict, the dedicated `PUT /api/memory/settings` write path) and
  surfaced in the settings payload + a `MemoryPanel` mode selector; `config-baseline.json` and
  `src/personalclaw/reference/routes.md` regenerated.

  **Gates:** `make lint` clean · new `tests/test_memory_vault_two_way.py` **63 passed** · vault
  and memory suites green · rails `test_config_baseline` / `test_config_roundtrip` /
  `test_inert_surface_baseline` / `test_portability` / `test_durability_inventory` /
  `test_resilience_degraded_lint` / `test_agent_reference` / `test_api_manifest_drift` /
  `test_roadmap_dag_derived` / `test_docs_lint_baseline` / `test_version_consistency` green ·
  full `pytest tests/` green.

### 2026-08-16 — Session 5 (§7.1–7.2 FE + config wiring): PARTIAL (`MGAV-9`)

**Six of seven `done_when` clauses landed; the atom stays `todo` on the seventh** (the
volunteer / edit-vault / undo legs of the validation sweep were not driven — see BLOCKED-ish note
at the end). Full clause-by-clause account + the five deviations live in
`docs/roadmap/atomic/MGAV.md` under `MGAV-9`; this entry records what changed and what it proved.

**The through-line: three shipped mechanisms had no way for a human to reach them.**
`api.memoryEntityProposal` had existed since `MGAV-1` with **zero FE callers** — the POST could
decide, but nothing could LIST the queue, so the decision surface was unreachable.
`graph_recall_evidence` was written "for the inspect/recall surfaces" and had **zero production
callers**. `memory_slots` (`MGAV-8`) had no editor at all, making a register the plan calls
"user-editable" machine-only. Each got its read half here.

**Backend.** `MemoryGraphStore.entity_graph()` — entities as nodes carrying the Louvain
`community` already stored in `mem_link_stats`, plus co-occurrence edges carrying the
`link_type`/`provenance`/`confidence` that `memory_topology.cooccurrence_edges` deliberately
drops (which is exactly what §7.2's filters need, so this is a sibling query, not a fork). An
edge's confidence is `max` over supporting records of `min(both legs)`: a record's support is only
as strong as its weaker link. Isolated entities are KEPT as nodes — an entity nothing links to is
the orphan signal the lint reports, and dropping it would hide it. `MemoryService` gained
`graph_proposals`, `entity_graph`, `graph_record_links` (composite `sem:`/`epi:` ref → the CLOSED
two-value `from_kind` map; an unrecognized prefix returns `[]` rather than guessing) and
`slots`/`slot_append`/`slot_tombstone`. Seven routes, all in the regenerated `routes.md`. The
over-cap append is a **409 carrying the trim proposal**, not a 400 with a message: `MGAV-8`'s
contract is that the human picks which of their own lines to lose, which only holds if the
candidate list reaches the UI.

**`memory_graph_export.py`** renders the one-file export. Script-free static SVG rather than the
plan's interactive `graph.html`, importing `knowledge/reports.py`'s own `assert_self_contained`
instead of re-deriving a second forbidden-pattern list. The JSON island rides an HTML comment with
`--` JSON-escaped (`--`), so a name containing `--` cannot close the comment early and the
island still parses. The viewBox crops to the drawn extent — the ring layout only fills 1000x1000
once the outer rings do, and the canvas gets away with that because it has zoom while this file
has none. Node hue is re-derived from the CANVAS's formula so the exported artifact is the picture
the user saw; that consistency surfaced a real defect (adjacent communities hash to hue 209/210 —
indistinguishable), left as an owner taste call because the fix is in the shared `groupColor`.

**Config.** `memory.slot_size_cap` (default 1400 = `SLOTS_BLOCK_MAX_CHARS`, so behaviour is
unchanged) wired through all five points + `config-baseline.json` + `docs/reference/configuration.md`
— which also gained the FIVE memory rows `MGAV-1`/`-3`/`-5` had left undocumented
(`graph_enabled`, `push_context`, `push_min_confidence`, `graph_topology_in_context`,
`holder_attribution`). Clamped at the CONSUMER (`memory_slots.resolve_block_limit`, 200-4000),
mirroring `HARD_CAP_RECORDS`, so config.json cannot widen the always-injected block by any route.

**Coherence repairs found by driving it.** (a) The Health tab's entity list + backlink expander
was a SECOND browser over the same objects the Studio explorer now lists; the browse/add/decide
affordances consolidated into the Studio and Health kept only graph maintenance (relink, counts,
export). (b) A `slot.*` row appeared BOTH as a Slot and as a raw-JSON Fact — one object, two
entries, the second uneditable without corrupting the register; `slot.` keys are now excluded from
the fact list. (c) Four Studio readers carried `.catch(() => [])`, so a failed load rendered "No
memories yet" to someone whose memories merely failed to load; they now let the rejection reach
the hook behind a `LoadError` that precedes both the loading and empty branches, and
`settingsListHonesty.test.ts` gained the explorer as a pinned site.

**Falsified three ways.** (1) Deleting `slot_size_cap` from `load()`'s explicit mapping →
`assert 1400 == 900` (the silently-dropped-field bug class the `MemoryConfig` comment records).
(2) Emitting a `<script type="application/json">` island → `SpecError: refusing to export: the
rendered document contains a script element` from the borrowed guard. (3) Dropping `limit=` from
`ContextBuilder._slots_block` → **the whole suite stayed GREEN**, because the covering test called
the primitive directly. That is the "test exercises the mechanism, not its use" shape, so
`test_the_session_builder_actually_READS_the_configured_budget` now drives the consumer and fails
with `assert 645 <= 250` when the argument goes missing.

**Drove on a live gateway** (port 10199, isolated home, no reseed): entity creation → write-time
linking → the typed entity graph with communities → per-record evidence tags → slot append, a real
over-cap trim proposal (`over_by: 81` naming the exact line), retire-then-re-add proving the human
tombstone is final → the proposal queue at `mention_count: 3` accepted into a live entity → the
exported file opened in a browser → the settings controls, including typing `900` into the Slots
budget and confirming it reached `config.json` **and** came back as the served `block_limit`, and
an out-of-range `99999` refused with `must be between 200 and 4000` while config stayed intact.

**Gates:** `make lint` clean (black/isort/flake8/mypy, 887 files) · `tests/test_memory_fe_surfaces.py`
**13 passed** · `tests/test_memory_slots_config.py` **7 passed** · memory + context suites **236
passed, 1 xfailed** · rails `test_config_roundtrip` / `test_config_baseline` /
`test_api_manifest_drift` / `test_inert_surface_baseline` / `test_roadmap_dag_derived` /
`test_docs_lint_baseline` green · `npm run typecheck` clean · full `npm test` **314 files / 3243
tests passed** (six repo-wide ratchets tripped and were fixed at the source, not loosened: the
graph-mode switch moved to the canonical `Segmented`, two new buttons took `loading` instead of a
hand-rolled spinner, a bare tinted `<p>` became `FieldError`, a skeleton noun was matched to its
`LoadError` sibling, `Select` gained the documented `ariaLabel` the other form primitives already
had, and the disabled-Toggle census moved 15→16 with the reason recorded in the rail).

**Not claimed:** the volunteer leg needs a real chat turn against a bound model (this dev home has
none), and edit-vault + undo exercise `MGAV-3`/`MGAV-6` machinery this atom only surfaces. Those
three legs of clause 7 remain, which is why `MGAV-9` stays `todo`.
