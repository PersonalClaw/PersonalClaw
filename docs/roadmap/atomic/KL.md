# KNOWLEDGE-LIBRARY — atomic plans

**Source plan:** [`KNOWLEDGE-LIBRARY`](../plans/KNOWLEDGE-LIBRARY.md)  
**Code:** `KL`  
**Source status:** in_progress

Plan status is IN PROGRESS. Done: S1 collections primitive, all of S2 (read/favorite curation, tags taxonomy, bulk ops), and the S3 dedup/merge store+API backend. Not started: S3 reading view (T3.1), S3 dedup/merge FRONTEND (T3.2 has backend only, "no frontend consumer yet"), S3 library home (T3.3, wants AMBIENT-SURFACES coordination), and the tail of the H1.1-H1.5 indexing amendment. Indexing landed: H1.1+H1.2 (`KL-9`, chunks table + structural chunker + chunk embedding in ingest), H1.3 (`KL-10`, chunk-level vector arm with max roll-up to items + chunk-derived locators) and H1.4 (`KL-11`, a `sqlite-vec` `vec0` index over the chunk vectors inside the same `knowledge.db`, with a cached probe that fails soft to the exact scan and a Doctor capability line — measured 40.1 ms → 2.2 ms per vector-arm query at KL-10's own 1,800-row benchmark shape, at recall 1.0000 against the exact scan). Indexing remaining: H1.5 (`KL-12`, resumable backfill + VH). The indexing amendment is a distinct sub-scope from the library UX and sequences on its own linear chain.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `KL-1` | ✅ | S1 Collections primitive: schema, store API, routes + Knowledge-page rail | — | collections + collection_items tables and read_state/favorited item columns added via the store's own additive ladder (store-native, no lifecycle gate); create a manual shelf and a smart shelf, both resolve to the same item shape; rail deep-links via ?collection=; a new matching item appears on a smart shelf with no backfill; smart shelf refuses membership writes with typed smart_collection_immutable |
| `KL-2` | ✅ | S2 read-state + favorited display and filters | `KL-1` | favorite gets its own star glyph (distinct from pin), a reading badge marks in-progress, read items dim; Reading/Unread/Read/Favorites filter chips with live counts appear only when the state is present; reader has a read-state cycle + favorite toggle routed through the non-touching curation endpoints (updated_at verified unchanged); pin has no retrieval weighting so favorite-vs-pin distinctness holds by inspection |
| `KL-3` | ✅ | S2 tags taxonomy: authoritative tags + item_tags tables, JSON backfill, hierarchy UI | `KL-1` | tags moved into authoritative tags + item_tags (surrogate id, parent_id self-FK with ON DELETE SET NULL, computed counts), JSON tags column DROPPED; single-pass idempotent backfill guarded on column presence; FTS sources from items_fts_src view so rebuild preserves recall; cycle guard rejects self/multi-hop parenting; GET /api/knowledge/tag-tree + Tags view render the hierarchy with live counts; external list[str] tag contract unchanged |
| `KL-4` | ✅ | S2 bulk operations: bulk_apply + /api/knowledge/bulk + multi-select bar | `KL-1` | bulk_apply + POST /api/knowledge/bulk with 7 reversible ops (collect/uncollect/read_state/favorite/archive/restore/pin), per-item best-effort reporting changed/unchanged/missing; 500-item cap; delete deliberately excluded; typed 400s for missing args and smart_collection_immutable; read_state/favorite stay non-touching through the bulk path; multi-select bar wired with a shared Checkbox primitive; HTTP-level test suite added |
| `KL-5` | ✅ | S3 dedup/merge store + API backend (find_duplicates, merge_items, routes) | `KL-1`, `KL-3` | find_duplicates wraps the existing TIER-2 prefilter + dedup.resolve_duplicate scorer (no embedding → no candidates); merge_items runs as one transaction where the survivor inherits both items' collections, tags and entity mentions plus the stronger read-state/favorited signal; FTS handled via _delete_item_cascade; self-merge refused; GET /api/knowledge/items/{id}/duplicates + POST /api/knowledge/items/{id}/merge (survivor in path, loser in body, confirm:true required) |
| `KL-6` | ⬜ | S3 dedup/merge frontend: near-duplicate surfacing UI with merge action | `KL-5` | the Knowledge UI surfaces near-duplicate candidates for an item and a merge action drives the existing GET /duplicates + POST /merge routes; two near-dupes merge from the UI, the survivor keeps both items' collection memberships + mentions, and the loser 404s; reduced-motion/theme/token-lint pass on the new UI |
| `KL-7` | ⬜ | S3 reading view: editorial type scale, progress, in-reader highlight→note | `KL-1` | KnowledgeDetailPage gains a reading mode with the editorial-document house-style reading type scale and a progress indicator; an in-reader highlight persists as a mention/note linked to the item and reappears on the item; a long article reads well; reduced-motion/theme/token-lint pass |
| `KL-8` | ⬜ | S3 library home: recently-added / continue-reading / favorites / collection counts | `KL-1`, `KL-2`, `KL-7`, `EXT:AMBIENT-SURFACES:tile-registry-for-composable-library-home` | a library home component renders live per-collection counts, recently-added, favorites, and a continue-reading section that resumes at the persisted reading position; built as a composable surface that consumes the AMBIENT-SURFACES tile registry if landed, standalone otherwise |
| `KL-9` | ✅ | Indexing H1.1+H1.2: chunks table + structural chunker + chunk embedding in ingest (retire 1000-char top-up) | — | a chunks table (id,item_id,chunk_index,text,embedding,section,line_start,line_end) and knowledge/chunking.py exist; long markdown/PDF/pptx items chunk on real structural boundaries (headings/slides/sheets) with size fallback + overlap; structureless items chunk by size; chunk section/line_start/line_end are populated; the item row keeps its whole-item embedding; ingest embeds chunks and the compose_item_text 1000-char top-up is deleted (clean break, no dual path); a test retrieves content deep in a long document that fails before the change |
| `KL-10` | ✅ | Indexing H1.3: vector arm searches chunks and rolls up to items before RRF | `KL-9` | the vector arm queries chunk vectors and rolls chunk hits up to their item before RRF; fusion, cliff-cut, _VECTOR_MIN_SIMILARITY and the returned item shape are unchanged on a fixed corpus; a chunk hit returns an item-shaped result whose section/line_range locator is asserted at least as specific as today's item-level one; items lacking chunks fall back to the whole-item vector (partial-chunk libraries degrade, never return zero) |
| `KL-11` | ✅ | Indexing H1.4: sqlite-vec ANN index with cached probe, fail-soft to exact scan, Doctor line | `KL-10` | sqlite-vec>=0.1,<1 added to core dependencies with a WHY comment; an ANN index over chunk vectors lives inside the vector arm; a cached enable_load_extension probe fails soft to the existing exact scan with a one-time INFO log; Doctor reports the degraded capability line; a recall-tolerance test asserts ANN-vs-exact match within the stated tolerance and a force-disabled-extension test proves correct results still return via exact scan; faiss and the [embeddings] extra are untouched |
| `KL-12` | ⬜ | Indexing H1.5: resumable batched chunk backfill + VH validation | `KL-9`, `KL-10` | a resumable, batched, progress-reporting backfill (modeled on reembed_all + the ingest queue's recover_pending) chunks existing items; interrupting and restarting resumes without duplicating or skipping; mid-backfill search returns sensible results (degrades to whole-item vectors, never zero); VH holds — a deep-answer question on a 30+ page PDF/long markdown is retrieved and cited to the right section (failing on main first), search latency measured before/after ANN on a large seeded library, backfill interrupt/resume verified, full local gate green |

## Atom scopes

### `KL-1` — S1 Collections primitive: schema, store API, routes + Knowledge-page rail

**Status:** done

Session 1 — Collections (T1.1 schema, T1.2 store API C2, T1.3 routes C3 + collections rail); Design §S1; Contracts C1/C2/C3

**Done when:** collections + collection_items tables and read_state/favorited item columns added via the store's own additive ladder (store-native, no lifecycle gate); create a manual shelf and a smart shelf, both resolve to the same item shape; rail deep-links via ?collection=; a new matching item appears on a smart shelf with no backfill; smart shelf refuses membership writes with typed smart_collection_immutable

### `KL-2` — S2 read-state + favorited display and filters

**Status:** done

Session 2 — T2.1 (read_state + favorited); Design §S2

**Done when:** favorite gets its own star glyph (distinct from pin), a reading badge marks in-progress, read items dim; Reading/Unread/Read/Favorites filter chips with live counts appear only when the state is present; reader has a read-state cycle + favorite toggle routed through the non-touching curation endpoints (updated_at verified unchanged); pin has no retrieval weighting so favorite-vs-pin distinctness holds by inspection

### `KL-3` — S2 tags taxonomy: authoritative tags + item_tags tables, JSON backfill, hierarchy UI

**Status:** done

Session 2 — T2.2 (tags taxonomy); Design §S2; Risks (tag-migration reconciliation)

**Done when:** tags moved into authoritative tags + item_tags (surrogate id, parent_id self-FK with ON DELETE SET NULL, computed counts), JSON tags column DROPPED; single-pass idempotent backfill guarded on column presence; FTS sources from items_fts_src view so rebuild preserves recall; cycle guard rejects self/multi-hop parenting; GET /api/knowledge/tag-tree + Tags view render the hierarchy with live counts; external list[str] tag contract unchanged

### `KL-4` — S2 bulk operations: bulk_apply + /api/knowledge/bulk + multi-select bar

**Status:** done

Session 2 — T2.3 (bulk ops); Contracts C2 bulk_apply, C3 POST /api/knowledge/bulk

**Done when:** bulk_apply + POST /api/knowledge/bulk with 7 reversible ops (collect/uncollect/read_state/favorite/archive/restore/pin), per-item best-effort reporting changed/unchanged/missing; 500-item cap; delete deliberately excluded; typed 400s for missing args and smart_collection_immutable; read_state/favorite stay non-touching through the bulk path; multi-select bar wired with a shared Checkbox primitive; HTTP-level test suite added

### `KL-5` — S3 dedup/merge store + API backend (find_duplicates, merge_items, routes)

**Status:** done

Session 3 — T3.2 (backend + API half); Contracts C2 find_duplicates/merge_items, C3 duplicates/merge routes

**Done when:** find_duplicates wraps the existing TIER-2 prefilter + dedup.resolve_duplicate scorer (no embedding → no candidates); merge_items runs as one transaction where the survivor inherits both items' collections, tags and entity mentions plus the stronger read-state/favorited signal; FTS handled via _delete_item_cascade; self-merge refused; GET /api/knowledge/items/{id}/duplicates + POST /api/knowledge/items/{id}/merge (survivor in path, loser in body, confirm:true required)

### `KL-6` — S3 dedup/merge frontend: near-duplicate surfacing UI with merge action

**Status:** todo

Session 3 — T3.2 (frontend consumer; the half not yet shipped — status line: 'no frontend consumer yet')

**Done when:** the Knowledge UI surfaces near-duplicate candidates for an item and a merge action drives the existing GET /duplicates + POST /merge routes; two near-dupes merge from the UI, the survivor keeps both items' collection memberships + mentions, and the loser 404s; reduced-motion/theme/token-lint pass on the new UI

### `KL-7` — S3 reading view: editorial type scale, progress, in-reader highlight→note

**Status:** todo

Session 3 — T3.1 (reading view); Open question (annotations as mentions vs dedicated table — default: reuse mentions)

**Done when:** KnowledgeDetailPage gains a reading mode with the editorial-document house-style reading type scale and a progress indicator; an in-reader highlight persists as a mention/note linked to the item and reappears on the item; a long article reads well; reduced-motion/theme/token-lint pass

### `KL-8` — S3 library home: recently-added / continue-reading / favorites / collection counts

**Status:** todo

Session 3 — T3.3 (library home, composable surface); Design §S3 (coordinate with AMBIENT-SURFACES 20 tile registry)

**Done when:** a library home component renders live per-collection counts, recently-added, favorites, and a continue-reading section that resumes at the persisted reading position; built as a composable surface that consumes the AMBIENT-SURFACES tile registry if landed, standalone otherwise

### `KL-9` — Indexing H1.1+H1.2: chunks table + structural chunker + chunk embedding in ingest (retire 1000-char top-up)

**Status:** done (PR #931)

Amendment task table — H1.1 (chunks table + chunker) and H1.2 (embed chunks in ingest, retire compose_item_text top-up); Amendment Design (a); Owner task 2 (confirm clean break)

**Done when:** a chunks table (id,item_id,chunk_index,text,embedding,section,line_start,line_end) and knowledge/chunking.py exist; long markdown/PDF/pptx items chunk on real structural boundaries (headings/slides/sheets) with size fallback + overlap; structureless items chunk by size; chunk section/line_start/line_end are populated; the item row keeps its whole-item embedding; ingest embeds chunks and the compose_item_text 1000-char top-up is deleted (clean break, no dual path); a test retrieves content deep in a long document that fails before the change

### `KL-10` — Indexing H1.3: vector arm searches chunks and rolls up to items before RRF

**Status:** done

Amendment task table — H1.3; Amendment Design (a); Risks (do not redesign fusion)

**Done when:** the vector arm queries chunk vectors and rolls chunk hits up to their item before RRF; fusion, cliff-cut, _VECTOR_MIN_SIMILARITY and the returned item shape are unchanged on a fixed corpus; a chunk hit returns an item-shaped result whose section/line_range locator is asserted at least as specific as today's item-level one; items lacking chunks fall back to the whole-item vector (partial-chunk libraries degrade, never return zero)

### `KL-11` — Indexing H1.4: sqlite-vec ANN index with cached probe, fail-soft to exact scan, Doctor line

**Status:** done

Amendment task table — H1.4; Dependency ruling (sqlite-vec, not faiss) + Runtime-availability clause; Risks (silent recall regression)

**Done when:** sqlite-vec>=0.1,<1 added to core dependencies with a WHY comment; an ANN index over chunk vectors lives inside the vector arm; a cached enable_load_extension probe fails soft to the existing exact scan with a one-time INFO log; Doctor reports the degraded capability line; a recall-tolerance test asserts ANN-vs-exact match within the stated tolerance and a force-disabled-extension test proves correct results still return via exact scan; faiss and the [embeddings] extra are untouched

### `KL-12` — Indexing H1.5: resumable batched chunk backfill + VH validation

**Status:** todo

Amendment task table — H1.5 (backfill) and VH (validation); Amendment Design (c) re-embed as a migration concern

**Done when:** a resumable, batched, progress-reporting backfill (modeled on reembed_all + the ingest queue's recover_pending) chunks existing items; interrupting and restarting resumes without duplicating or skipping; mid-backfill search returns sensible results (degrades to whole-item vectors, never zero); VH holds — a deep-answer question on a 30+ page PDF/long markdown is retrieved and cited to the right section (failing on main first), search latency measured before/after ANN on a large seeded library, backfill interrupt/resume verified, full local gate green

