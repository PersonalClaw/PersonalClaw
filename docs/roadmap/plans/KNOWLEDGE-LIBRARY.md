# Plan: Knowledge Library — Collections, Curation, and Reading

**Status:** IN PROGRESS — Session 1 (collections + item curation) shipped 2026-07-29; **Session 2
COMPLETE** — T2.1 curation display/filters, T2.2 tags taxonomy, T2.3 bulk ops, all 2026-07-29.
**Session 3 PARTIAL** — T3.2's backend landed 2026-07-30 (`find_duplicates`/`merge_items` + the
duplicates/merge routes) but has **no frontend consumer yet**; T3.1 (reading view) and T3.3 (library
home) are not started.
🔴 The 2026-07-29 indexing amendment (H1.1-H1.5: chunking, `sqlite-vec` ANN, backfill) is **NOT
started** — `sqlite-vec` is absent from `pyproject.toml` and there is no `chunks` table, so the
one-vector-per-item over `content[:1000]` with a brute-force cosine scan is still what ships.
Status corrected 2026-08-04 by code audit. Created 2026-07-18 (roadmap rev 10; owner ask: more
library-management capabilities for knowledge articles)

---

> 📎 **Artifacts-as-a-knowledge-source lives in [SIBLING-PRODUCT-PARITY-KIROCREW](SIBLING-PRODUCT-PARITY-KIROCREW.md) §6 (#68)** — added 2026-08-05: an aggregate `artifact://` source row + an in-process change-listener that auto-ingests content-bearing artifacts (searchable but **not** listed as knowledge items), ported from KiroCrew's `knowledge/artifact_ingest.py`. It plugs into *this* plan's source framework (`knowledge/pipeline/`, `connectors/`, `KnowledgeStore`). If you change the source-type model or the ingestion path here, read #68 §6 so the artifact source type lands compatibly.

## Context (code recon, 2026-07-18)

The store is already rich (`knowledge/store.py`): `items` table with `title, content, summary, tags(JSON), status, url, word_count, provider, is_pinned, is_archived, created_at, updated_at`; FTS5 `items_fts`; `entities` + `entity_relations` + `mentions` (the knowledge graph); `extracted_contents`; `intent_outcomes`. Retrieval: `retrieval.py::search(query, limit, include_archived=False)`; a P12 "same-type prefilter" for related items. Frontend: `web/src/pages/knowledge/` — List/Detail/Create pages, `KnowledgeGraph.tsx`, `GistEditor`, `AudioRecorder`, `knowledgeStore.ts`.

**What's missing for a *library*:** no **collections/shelves** (tags exist but are flat labels, not curated groupings); no **read/unread** state (only pinned/archived); no **saved views/smart collections** (a query you name and revisit); no **reading experience** (Detail is a data view, not a reading view with typography/progress/annotations); no **dedup/merge** UI (URL-normalization dedups on ingest, but no manual merge of near-dupes); no **bulk operations** (select-many → tag/collect/archive). Tags are JSON on the row — fine for labels, insufficient as a taxonomy.

## Design

- **S1 — Collections (the core new primitive):** a `collections` table (`id, name, description, icon, color, kind: manual|smart, query(for smart), created_at, sort`) + a `collection_items` join (manual membership) — smart collections resolve a saved FTS/filter query at read time (no membership rows). An item can be in many collections. Collections are the library's shelves; the Knowledge page gains a collections rail. Additive migration; existing items simply have no collections until curated.
- **S2 — Curation lifecycle + taxonomy + bulk:** add `read_state: unread|reading|read` and `favorited` (distinct from pinned, which is a surfacing weight) to `items`; promote tags to a **taxonomy** (a `tags` table with optional parent for hierarchy + usage counts; the row's JSON tags become references — migration reconciles); **saved views** (named filter+sort combos, = smart collections' UI); **bulk operations** (multi-select → add-to-collection / tag / archive / mark-read / delete) via a batch endpoint.
- **S3 — Reading experience + intelligence surfacing:** a proper **reading view** (tuned reading type scale — reuse the editorial-document skill's house style; progress indicator; in-reader highlight/annotation that becomes a `mention`/note linked to the item); **related-items** rail (existing P12 prefilter + entity-graph neighbors); **library home** (recently added, continue-reading, favorites, per-collection counts) — a composable surface coordinating with AMBIENT-SURFACES (20). Dedup/merge UI: surface near-duplicate candidates (URL + title + embedding similarity) with a merge action (keeps one, redirects mentions/collections).

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md); class B per plan 31)

### C1 — Schema additions (`knowledge/store.py`, additive; migration `m_*_knowledge_library`)
```sql
CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT, description TEXT DEFAULT '',
  icon TEXT DEFAULT '', color TEXT DEFAULT '', kind TEXT DEFAULT 'manual',
  query TEXT DEFAULT '', created_at TEXT, sort INTEGER DEFAULT 0);
CREATE TABLE collection_items (collection_id TEXT, item_id TEXT, added_at TEXT,
  PRIMARY KEY (collection_id, item_id));
CREATE TABLE tags (name TEXT PRIMARY KEY, parent TEXT DEFAULT '', usage_count INTEGER DEFAULT 0);
-- items gains: read_state TEXT DEFAULT 'unread', favorited INTEGER DEFAULT 0  (via the additive-column pattern already used for is_archived, store.py:276)
```

### C2 — Store API (new methods on the knowledge store)
```python
def create_collection(*, name, kind="manual", query="", **meta) -> str: ...
def add_to_collection(collection_id, item_id) -> None: ...
def resolve_collection(collection_id, limit=50) -> list[dict]: ...   # manual: join; smart: run query
def set_read_state(item_id, state: Literal["unread","reading","read"]) -> None: ...
def set_favorited(item_id, value: bool) -> None: ...
def bulk_apply(item_ids: list[str], *, add_collection=None, add_tags=None, archive=None, read_state=None) -> int: ...
def find_duplicates(item_id) -> list[dict]: ...   # URL + title + embedding similarity
def merge_items(keep_id, drop_id) -> None: ...    # redirects mentions + collection_items to keep_id
```

### C3 — HTTP (new routes beside existing knowledge handlers; §2.2 error envelope)
`GET/POST /api/knowledge/collections`, `PATCH/DELETE /api/knowledge/collections/{id}`, `POST /api/knowledge/collections/{id}/items`, `POST /api/knowledge/bulk`, `POST /api/knowledge/items/{id}/read-state`, `POST /api/knowledge/items/{id}/merge`. All Tier-I (dashboard API).

### Integration points
- **Calls:** existing `knowledge/store.py` + `retrieval.py::search` (smart collections + dedup similarity), the embedding path (dedup), plan-31 migration framework.
- **Called by:** the Knowledge frontend (collections rail, reading view, bulk bar); WATCHED-SOURCES (15) lands items into a declared collection; KNOWLEDGE-SYNTHESIS (5) synthesis outputs become library items.
- **Storage owned:** the three new tables + two new item columns (all in knowledge.db).
- **Gate/migration:** `knowledge_library` (class B) + `m_*_knowledge_library` (creates tables + reconciles JSON tags → tags table; idempotent).

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Collections

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Schema (collections, collection_items; the two item columns via the additive-column pattern at `store.py:276`) — **store-native, no gate and no `lifecycle/` file** (the original wording asked for both; see the S1 execution-log DEVIATION) | `knowledge/store.py`, tests | opening the store on a pre-collections fixture home creates both tables + both columns; existing items load unchanged; idempotent on reopen |
| T1.2 | Store API C2 collection methods + smart-collection resolution (via `retrieval.search`) | `knowledge/store.py`, tests | manual + smart collections resolve; item in N collections works |
| T1.3 | HTTP routes C3 for collections; frontend collections rail on the Knowledge page (create/rename/reorder, per-collection view) | knowledge handlers, `web/src/pages/knowledge/` | create a collection, add items, view it; smart collection updates as items match |
| V1 | Validation as a user: build a manual shelf + a smart collection ("all PDFs about X"); both behave; reduced-motion/theme/token-lint pass on new UI | — | holds |

### Session 2 — Curation + taxonomy + bulk

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | read_state + favorited store API + routes; item rows/reader show state; toggles persist | `knowledge/store.py`, handlers, frontend | mark unread→reading→read; favorite distinct from pin (verify weighting unaffected) |
| T2.2 | Tags taxonomy: `tags` table, JSON-tags→references migration step, hierarchy + usage counts, tag management UI | `knowledge/store.py`, migration, tag UI | existing tags appear with counts; nesting works; old JSON still readable during dual-path |
| T2.3 | Bulk ops: `bulk_apply` + `POST /api/knowledge/bulk`; multi-select bar on the list (add-to-collection/tag/archive/read/delete) | store, handler, list page | select 10 → add to a collection in one action; SEL/audit sane |
| V2 | Validation: curate a real set — bulk-collect, tag-hierarchy, mark-read; counts consistent everywhere | — | holds |

### Session 3 — Reading + dedup + library home (Wave 3)

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Reading view: reading type scale (editorial-document house style), progress, in-reader highlight→note (stored as a mention/annotation linked to the item) | `web/src/pages/knowledge/KnowledgeDetailPage.tsx` (reading mode), store note API | a long article reads well; a highlight persists and appears on the item |
| T3.2 | Dedup/merge: `find_duplicates` + `merge_items` + a near-dupe surfacing UI with merge action (redirects mentions + collections) | `knowledge/store.py`, handler, UI | two near-dupes merge; the survivor keeps both's collection memberships + mentions |
| T3.3 | Library home: recently-added / continue-reading / favorites / collection counts — a composable surface (coordinate with AMBIENT-SURFACES 20 tile registry if landed) | `web/src/pages/knowledge/` home component | home renders live counts; continue-reading resumes at the reading position |
| V3 | Validation: full library workflow — ingest, collect, read to a point, come back via continue-reading, merge a dupe | — | holds |

## Owner tasks (real world)
1. **Curate with real data during S2 dogfood** — the taxonomy hierarchy + collection model are taste calls only real articles validate; report what feels missing.
2. Decide default collections seeded for new users (proposal: none — an empty, self-explaining library beats prescriptive shelves), and whether watched-sources auto-create a per-source collection.

## Risks & open questions
- **Tag-migration reconciliation** (JSON → table) is the one delicate step — dual-path (both readable) until the migration verifies, per plan 31; a fixture with messy tags is the test.
- **Open:** annotations as `mentions` vs a dedicated `annotations` table — default: reuse `mentions` (already links entities↔items); promote to its own table only if reading-notes need richer structure (revisit in S3).
- **Open:** whether smart collections should be materializable (cached) for large libraries — defer until a real library shows the query cost (bottleneck-gated).

## Execution log

- 2026-07-29 — **DONE (S1: T1.1 + T1.2 + T1.3).** Collections (manual + smart), item
  curation (read state + favorites), the HTTP surface, and the Knowledge-page rail.
  - **T1.1 store + migration.** Two tables (`collections`, `collection_items`) and two
    item columns (`read_state`, `favorited`) added via the store's OWN additive ladder
    (`_NEW_ITEM_COLUMNS` + `_migrate`) and its `CREATE TABLE IF NOT EXISTS` schema.
  - **DEVIATION (E1, premise mismatch) — the plan's `lifecycle/` tasks are STALE.** T1.1
    names `lifecycle/migrations/m_*_knowledge_library.py` and a `knowledge_library`
    gate, but **`src/personalclaw/lifecycle/` does not exist** (Lifecycle-Doctrine is
    owner-deferred). knowledge.db has a store-native additive ladder, so this went
    store-native — the same ruling the owner already made for Memory-Graph S1's v7. No
    gate, no migration file. Verified with a hand-built PRE-collections DB: opening the
    store adds both columns + both tables, the existing item survives, its NULL
    `read_state` normalizes to `unread`, and a second open is a no-op.
  - **Also stale: the plan's "needs a bound provider key" assumption.** Collections and
    curation touch zero LLM paths, and knowledge search degrades cleanly with no
    embedder (`retrieval.py:73-76` returns None from `_vector_search`; RRF fuses
    keyword+graph). Smart shelves resolve through hybrid retrieval either way. The
    earlier skip-ruling rested on a false premise.
  - **T1.2 store API (C2).** `create_collection` / `list_collections` /
    `get_collection` / `update_collection` / `delete_collection` /
    `add_to_collection` / `remove_from_collection` / `collections_for_item` /
    `resolve_collection` / `set_read_state` / `set_favorited`.
    - A **smart shelf with no query is refused** at create AND at kind-switch — it
      would match nothing forever and read as broken.
    - `list_collections` reports `item_count: None` for smart shelves **deliberately**:
      counting them means one search per shelf on every rail render.
    - `resolve_collection` re-reads each smart hit as a **full item**, so a smart and a
      manual shelf hand the UI the same shape (a retrieval hit is a search projection).
    - Read-state / favorite are **non-touching** writes (`touch=False`): marking
      something read is not editing it, and bumping `updated_at` would silently
      reorder a recency-sorted library while the user reads through a backlog.
    - Deleting a shelf keeps its items — a shelf is a view, not a container.
    - Archived items never appear on a shelf (an archive is "not in my active library").
  - **T1.3 routes (C3) + UI.** 9 routes; literal `collections` paths registered before
    the `{id}` patterns. Frontend: a collections rail on the Knowledge page (URL-backed
    `?collection=`, so a shelf is deep-linkable), create/rename/delete, per-row
    add-to-shelf / remove-from-shelf / read-state cycle / favorite. A selected shelf
    **replaces the item source** rather than filtering the loaded list — a smart shelf's
    membership is server-resolved and isn't derivable client-side.
    - Adding to a **smart** shelf returns a typed 400 (`smart_collection_immutable`)
      rather than silently accepting a row its reads ignore.
    - `add_collection_items` reports per-item `added`/`missing`, so shelving 30 items
      doesn't fail wholesale because one was deleted in another tab.
  - **Design-system note:** the primitive-adoption ratchet rejected a raw `<button>` for
    "New shelf"; it now reuses the rail's own `FilterChip`, which is also the right
    reading (it lives in the chip row).
  - **V1 validated as a user** on an isolated dev home: created 3 items, a manual shelf
    (shelved 1 of 2 ids — the bogus one reported `missing`, batch survived), and a smart
    shelf; the rail rendered both kinds with the manual count and smart-as-unknown; each
    shelf deep-linked and resolved correctly; **a brand-new matching item appeared on
    the smart shelf with no backfill** (the live-ness claim, confirmed in the browser);
    the smart shelf refused a membership write. 0 gateway tracebacks; the one console
    error was my own probe hitting a wrong config path, not the feature.
  - **Gates:** `make lint` clean (mypy 538 files) · backend **8839 passed** (30 new) ·
    web **283 passed** (32 files) + typecheck + build. Offline agent reference
    regenerated for the 9 new routes.
  - Pre-existing/unrelated: `test_cron.py`'s spring-forward test (core issue #85).
  - **NOT taken:** T1.2's `bulk_apply` / `find_duplicates` / `merge_items` belong to
    S2 (curation + dedup) per the plan's own session split; this slice is collections
    + read state + favorites, which is what T1.1-T1.3 name.

---

## Amendment (2026-07-29 — owner-approved: indexing depth, the layer under the library)

**Provenance.** A competitive gap analysis (Genspark + Manus) plus a code audit found that PersonalClaw's *retrieval machinery* is genuinely strong while the *index underneath it* is thin — and that the owner's question ("should this be fixed by the knowledge tools implementation?") resolves to **no**: it is neither a tools problem nor a library-management problem. It lands here because this plan owns `knowledge/store.py`, but it is a distinct sub-scope from S1-S3's curation work and should be sequenced independently.

### The three limits, each verified

1. **No chunking — one vector per item, over a truncated body.** `knowledge/embedder.py:17-30::compose_item_text` embeds `title + summary`, topped up with **`content[:1000]`** *and only when the summary is shorter than 80 characters*. So a well-summarized 40-page PDF is represented by title+summary alone; a summary-less one gets its first ~1000 characters. The schema comment states the absence plainly: there is **no `sources` table and no `chunk_index`**. The ingest pipeline's "chunk+embed" naming is therefore aspirational.
   **Consequence:** semantic recall degrades exactly where a knowledge base earns its keep — long documents where the answer is on page 12. Keyword (FTS5) still finds it, so the failure is *partial and quiet*: the vector arm contributes nothing useful for long items, and the hybrid fusion silently leans on one leg.
2. **Brute-force vector search.** `knowledge/retrieval.py:257` issues `SELECT id, embedding FROM items WHERE embedding IS NOT NULL AND status = 'active'` and scores **every row in Python** per query (`:262`). No ANN index anywhere in `knowledge/` (verified: no faiss/hnsw/IndexFlat usage). Fine at hundreds of items; linear in library size thereafter, on the request path.
3. **One connector.** `knowledge/connectors/web_url.py` is the only ingestion connector — no Gmail, Notion, Drive, GitHub, Slack, or Confluence. **Explicitly NOT this amendment's scope:** WATCHED-SOURCES (15) owns making the source-provider seam real, and EMAIL-INBOX-AND-TRIGGERS owns mail. Named here only so the reader knows the thin index is not the whole ingestion story.

**What must NOT change — the machinery above the index is good and is not the problem.** `HybridRetriever.search` fuses three arms with **RRF (k=60)**: FTS5 keyword (with prefix-OR sanitization so conversational queries match), graph traversal (entity resolution at word/bigram/trigram granularity, neighbor expansion to depth 2), and vector (cosine, `_VECTOR_MIN_SIMILARITY = 0.25`, skipping dimension-mismatched vectors). Post-fusion: title boost in RRF-score units, recency tie-break, and `relevance_cliff_cut` (gap 0.30) returning the natural relevant cluster instead of a fixed top-K. Every hit carries `match_type` and citation locators (`source_type`, `section` from the nearest heading/slide/sheet, 1-based `line_range`, `deep_link`) — **and honestly returns `null` for structureless items rather than fabricating a locator.** An executor must treat all of this as fixed: this amendment feeds it better inputs, it does not redesign fusion.

### Design

- **(a) Chunking with citation fidelity preserved.** Add a `chunks` table (`id, item_id, chunk_index, text, embedding, section, line_start, line_end`) with the item row keeping its whole-item embedding for cheap title/summary matching. Ingest chunks long items on **structural boundaries first** (headings, slides, sheets — the readers already extract these, which is what makes the existing `section`/`line_range` locators possible) with a size fallback and modest overlap. Retrieval's vector arm searches chunks, then **rolls chunk hits up to their item** before RRF so the fusion contract and the returned shape are unchanged — a chunk hit simply carries a *better* `section`/`line_range` than a whole-item hit could. **The citation locators must get more precise, never less**; a test should assert a chunk hit's locator is at least as specific as today's item-level one.
- **(b) An ANN index, behind the existing arm.** Introduce an approximate index for the chunk vectors with an exact-scan fallback, chosen so the request path stops being linear. **Dependency: RESOLVED by owner ruling 2026-07-29 — add `sqlite-vec` as a CORE dependency** (see §Dependency ruling below). Keep it **inside** the vector arm: `search()`'s signature, the RRF fusion, the cliff-cut, and `_VECTOR_MIN_SIMILARITY` are untouched. Correctness bar: for a fixed corpus and query set, ANN results must match exact-scan results within a stated recall tolerance, and the test asserts it — an index that silently loses relevant items is worse than a slow one.

### Dependency ruling (owner, 2026-07-29) — `sqlite-vec`, and why NOT faiss

```toml
# pyproject.toml, CORE `dependencies`. Comment must say WHY it is core, matching the
# neighbouring blocks' style (see the codegraph rationale at pyproject.toml:57-60).
"sqlite-vec>=0.1,<1",
```

**Why `sqlite-vec` and not `faiss`** — this is the load-bearing part of the ruling, and an executor must not "simplify" it back to faiss on the grounds that faiss is already in the tree:
- **`faiss` is deliberately EXCLUDED from the desktop bundle** — `personalclaw-backend.spec:182` lists `"faiss"` under "Heavy optional deps," and `pyproject.toml:111` says so explicitly ("faiss stays here — excluded from the desktop bundle, degrades to SQLite-only"). It is also only an **extra** (`[embeddings]`), not core. Promoting it to core would either bloat the desktop bundle or hand desktop users a knowledge search that silently degrades to a full-table scan — precisely the "accelerator half the installs lack" failure the codegraph decision rejected.
- **`sqlite-vec` fits the store's existing shape.** The knowledge store is SQLite-first (`items` + FTS5 `items_fts` + the graph tables in one DB); a vector index as a SQLite extension keeps the index in the **same file and the same transaction** as the rows it indexes, so a chunk write and its vector write cannot diverge. faiss would add a second on-disk artifact needing its own consistency story — exactly the split-brain that `memory_graph.py` deliberately avoided by installing `SCHEMA_V7` *inside* `memory.db` rather than a sidecar.
- **It is small and pure-wheel**, so unlike faiss it passes the desktop-bundle bar the doc-reader block sets (`pyproject.toml:42-43`).
- **`faiss` stays exactly where it is** — the `[embeddings]` extra, serving `vector_memory.py`'s optional `memory.faiss` index (`vector_memory.py:55-59`, `:78`). This plan does not touch memory-side vector search, and the two subsystems keep separate indexes by design.

**Runtime-availability clause (required, not optional):** SQLite extension loading is not universally available — it depends on how the interpreter's SQLite was built, and the repo already special-cases SQLite capability (`pysqlite3-binary` is a conditional core dep at `pyproject.toml:41`, and PLATFORM-REACH owns a `sqlite_features()` contract). So the implementation MUST:
1. probe `enable_load_extension` availability once and cache it;
2. **fail soft to the existing exact scan** when the extension cannot load — never raise into a search, and log the degradation once at INFO with the reason;
3. surface the degraded state in Doctor as a capability line (the honest-degradation pattern `resilience/degraded.py` already models), so a user on a stripped SQLite knows why search is slower rather than assuming it is broken.
Reuse PLATFORM-REACH's `sqlite_features()` contract for the probe if it has landed; if not, the probe lands here and that plan consumes it (record which happened in the execution log).
- **(c) Re-embed as a migration concern.** Existing items have whole-item vectors and no chunks. Chunking is a **class-B** change: it lands as a plain clean break under the pre-1.0 banner (no gate/migration machinery, per the workspace doctrine), but it needs a **backfill** that is resumable, batched, and progress-reporting — the store already has a batch re-embed path (`reembed_all`) and an ingest queue with `recover_pending()` restart recovery to model it on. A library with a half-built chunk table must degrade to whole-item vectors, not to zero results.

### Amendment task table (extends this plan; run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

Sequence these **independently of S1-S3** — they touch the store's indexing layer, not the library UI.

| ID | Task | Files | Done when |
|---|---|---|---|
| H1.1 | `chunks` table + structural-first chunker (headings/slides/sheets, size fallback, overlap); item row keeps its whole-item embedding; unit tests per source structure | `knowledge/store.py`, a new `knowledge/chunking.py`, tests | a long markdown/PDF/pptx item chunks on real structural boundaries; a structureless item chunks by size; chunk `section`/`line_start`/`line_end` are populated |
| H1.2 | Embed chunks in the ingest pipeline; **retire `compose_item_text`'s 1000-char top-up for long items** in favor of chunk vectors (clean break — no dual path), keeping title+summary embedding for the item row | `knowledge/embedder.py:17-30`, the ingest pipeline, tests | a long item's semantic recall no longer depends on a 1000-char slice (test retrieves content from deep in a long document, which fails before the change); no dead top-up path remains |
| H1.3 | Vector arm searches chunks and rolls up to items before RRF; fusion, cliff-cut, thresholds, and the returned shape unchanged; locators come from the winning chunk | `knowledge/retrieval.py:238-262`, tests | a chunk hit returns an item-shaped result with a MORE specific locator than today (asserted); RRF/cliff-cut behavior is unchanged on a fixed corpus |
| H1.4 | Add `sqlite-vec>=0.1,<1` to core `dependencies` per the Dependency ruling (with the WHY comment); ANN index for chunk vectors inside the vector arm; **cached extension-availability probe with fail-soft to the existing exact scan** + a one-time INFO log + a Doctor capability line; recall-tolerance test against exact scan on a fixed corpus + query set. **Do not touch faiss or the `[embeddings]` extra** | `pyproject.toml`, `knowledge/retrieval.py`, index module, `resilience/doctor.py` (capability line), tests | queries stop scanning the full table; ANN-vs-exact recall meets the stated tolerance; with extension loading force-disabled the search still returns correct results via exact scan (test proves the fail-soft, not just the happy path); Doctor reports the degraded state; `faiss` remains an extra and untouched |
| H1.5 | Resumable batched backfill (progress-reporting, restart-recoverable, modeled on `reembed_all` + the ingest queue's `recover_pending`); a partially-chunked library degrades to whole-item vectors | the store's re-embed path, tests | interrupting the backfill and restarting resumes without duplicating or skipping; mid-backfill search returns sensible results, never zero |
| VH | Validation as a user: ingest a genuinely long document (a 30+ page PDF and a long markdown file); ask a question whose answer is deep in the middle and confirm it is now retrieved **and cited to the right section** (confirm it fails on `main` before the change); measure search latency on a large seeded library before/after the ANN index; interrupt and resume the backfill; full local gate | — | holds |

### Owner tasks (real world)
1. ~~Rule on the ANN dependency.~~ **RESOLVED 2026-07-29:** `sqlite-vec` approved as a core dependency, with faiss explicitly NOT promoted (it is desktop-excluded at `personalclaw-backend.spec:182` and stays in the `[embeddings]` extra). See §Dependency ruling for the full rationale and the mandatory fail-soft clause.
2. **Confirm the chunking clean break.** The plan retires the 1000-char top-up rather than keeping both paths. Under the pre-1.0 banner that is the house style, and release notes should advise `personalclaw snapshot` before upgrading.

### Risks & open questions
- **Storage growth.** Chunk rows plus per-chunk vectors multiply the DB. Bounded by chunk size and by embedding dimension; worth measuring on a real library during VH rather than estimating.
- **Silent recall regression from ANN** is the sharpest risk — an approximate index that drops relevant items looks like a working search. This is why H1.4's tolerance test is a required gate and not an optimization check.
- **Do not redesign fusion.** The RRF + graph + cliff-cut + honest-null-locator machinery is a strength; every task above feeds it better inputs. A task proposing to retune thresholds or fusion weights is out of scope (escalation E6) unless a measurement demands it.
- **Open:** whether the graph arm should also index chunks (entity mentions currently resolve at item granularity). Deferred — item-level entity resolution is coherent, and chunk-level mentions would multiply the graph without a demonstrated need.
- 2026-07-29 — **DONE (S2: T2.3 bulk ops).** `bulk_apply` + `POST /api/knowledge/bulk`
  + a multi-select bar on the library list. Seven ops: collect, uncollect, read_state,
  favorite, archive, restore, pin.

  **Followed `session_bulk.py` (SESSION-MANAGEMENT S2) as the house precedent**, and
  deliberately over the *other* one: `POST /api/tasks/bulk` validates-all-then-aborts,
  which is right for task creation and wrong here. A knowledge selection can go stale
  between the click and the request, so per-item best-effort with
  `changed`/`unchanged`/`missing` is the honest shape — "38 shelved · 2 not found" beats
  a wholesale failure. S1's own `add_collection_items` already made this call inside the
  very file being edited, so this is consistency, not novelty.

  **`delete` is NOT an op**, mirroring `session_bulk`'s explicit exclusion: every op here
  is reversible, and putting an irreversible one beside them is one mis-click from data
  loss. The task line asked for delete in the bulk bar; declining it is a deliberate
  deviation, pinned by a test. Deleting stays the single-item path with its confirmation.

  **`unchanged` is load-bearing, not cosmetic.** `add_to_collection` is `INSERT OR
  IGNORE`, so a naive implementation reporting rowcount would claim 40 changes for
  re-shelving 40 already-shelved items. The bulk path checks membership first. Verified
  live: re-shelving the same two items reports **"Added to Reading: 0 · 2 already set"**.

  **Argument problems are typed 400s, not silent no-ops.** A caller that forgot
  `collection_id` would otherwise get `{"ok": true}` with an empty `changed` list over a
  40-item selection — which reads as "nothing matched" rather than "you left out an
  argument". `smart_collection_immutable` keeps its own code so the frontend can explain
  that a smart shelf fills itself from its query instead of showing a generic failure.

  **Read-state and favorite stay NON-touching writes** through the bulk path too (they
  route through `set_read_state`/`set_favorited`). Marking a backlog read must not
  masquerade as editing every item, or it reshuffles a recency-sorted library out from
  under the user. Archive/pin DO touch, because they genuinely change an item's standing.
  A test pins `updated_at` surviving a bulk read-state pass.

  **Closed a coverage gap the audit found:** S1 shipped 9 collection/curation routes with
  **zero HTTP-level tests** (only store-level). New `tests/test_knowledge_bulk_api.py`
  (10 cases) covers the endpoint's own job — validation, error envelopes, the 500-item
  cap, route registration — using the `_app()` harness pattern from
  `test_knowledge_typed_items.py`. Also closed the SEL gap for this route (`_sel_log`,
  which the S1 block omitted entirely).

  **FE:** row checkbox via the shared `Checkbox` primitive (a raw one would trip the
  primitive-adoption ratchet), hidden until hover or an active selection so the list
  stays calm when nobody is curating; a wrapper stops the tick from also opening the
  peek. Selection is deliberately NOT URL-backed — a transient selection isn't
  meaningfully deep-linkable, and restoring one on reload re-arms a state the user didn't
  ask for. The outcome note renders OUTSIDE the bar so it survives the bar unmounting
  when the selection clears on success. Only MANUAL shelves appear as "Add to …" targets.

  Validated as a user on an isolated dev home (port 10734): ticked 2 of 4 seeded items,
  shelved them in one action (chip went to "Reading 2" live), re-ran to confirm the
  unchanged report, marked 2 read and confirmed `read_state` persisted for exactly those
  two, and confirmed over HTTP that a smart shelf refuses with its typed code and a
  501-item selection is refused. Zero console errors.

  **NOT done: T2.1's remainder and T2.2.** T2.1 is ~85% shipped by S1 — what's left is
  purely FE (neither read-state nor favorite renders ON the row; there's no
  unread/favorites filter chip, so favoriting is currently write-only) plus a premise
  correction: **the plan's "verify pin weighting unaffected" is moot — pin has no
  retrieval weighting.** Its only effect is `ORDER BY` in the *unsearched* list branch
  (`handlers/knowledge.py:201`); nothing in `knowledge/retrieval.py` reads `is_pinned`.
  **T2.2 (tags taxonomy) is NOT startable as written:** its done-when requires a
  "dual-path" JSON→table migration, which the pre-Lifecycle-Doctrine doctrine forbids
  (class-B changes are plain clean breaks), and knowledge.db has **no schema version** to
  gate a one-shot backfill on. It also has a wider blast radius than the plan implies:
  tags reach the FTS index as the raw JSON string via **4 manual sync sites**, the agent's
  tool contract declares `tags` in 3 schemas, and `pipeline/runner.py:519-527` preserves
  user-authored tags by **list-equality on the JSON shape**. Recommend re-scoping to a
  derived tag registry (counts + hierarchy, JSON stays authoritative) — additive, no
  migration, and it satisfies the stated done-when. Owner ruling needed before starting.
  - **⚠️ SUPERSEDED — this "NOT startable" verdict was wrong** (owner correction, kept here
    because an execution log is a record, not a wiki). T2.2 **shipped** later the same day:
    the owner ruled the table becomes authoritative, and the JSON→rows backfill was made
    idempotent **by data inspection** rather than by a schema version — which is the house
    pattern, so the "no schema version to gate on" objection dissolved. The general lesson,
    now standing doctrine: a plan clause conflicting with the clean-break doctrine is a
    **re-scope, never a blocker** — see [EXECUTION-PROTOCOL §2](EXECUTION-PROTOCOL.md) and
    the workspace `AGENTS.md` section "You are working as the OWNER".
- 2026-07-29 — **DONE (S2: T2.1 remainder).** S1 shipped the store API, routes and
  context-menu verbs for read state and favorites; **neither state rendered anywhere**,
  which made favoriting **write-only** — you could star an item and then had no way to
  find your stars. This closes the display and filter half.

  **Row affordances.** Favorite gets its **own glyph** (a star). It shared the `Pin`
  icon before, which made two deliberately distinct concepts indistinguishable on the
  row — pin floats an item to the top of the list, favorite is a personal mark with no
  ordering effect. A `reading` badge marks the in-progress state, and a read item's
  title drops to a lighter weight and a dimmer tone. **Unread deliberately gets NO
  marker:** it is the default state, and badging every fresh item would turn the whole
  library into noise. Only states a reader actually set are shown.

  **Filter chips** for Reading / Unread / Read / Favorites, client-side like the
  existing type/provider/tag filters (keeping the full item set loaded, per the
  page's own stated convention). Each chip appears **only when the state it filters is
  present** — an always-visible "Favorites 0" is a dead end that teaches nothing — and
  the Unread chip additionally hides when *everything* is unread, since filtering to
  "all of it" is not a filter. Counts ride on the chips.

  **Reader controls.** The dedicated item page gained a read-state cycle and a favorite
  toggle beside Pin/Archive, with self-describing labels ("Mark as reading" →
  "Reading — mark read" → "Read — mark unread"). A **cycle rather than a toggle** because
  the middle state is the one a reading list exists to represent. Both route through the
  dedicated curation endpoints, **not** `updateKnowledge`: these are non-touching writes,
  and going through the generic update path would bump `updated_at` and reshuffle a
  recency-sorted library. Verified live — after cycling and favoriting one item, its
  `updated_at` was still the original second while its siblings' had moved.

  **Premise correction carried from the audit:** the plan's T2.1 done-when says "verify
  weighting unaffected" for favorite-vs-pin. **There is no retrieval weighting to
  affect.** `is_pinned` appears only in the no-query list branch's `ORDER BY`
  (`handlers/knowledge.py:201`); nothing in `knowledge/retrieval.py` reads it, so pin
  affects neither searched results nor agent retrieval. Favorite was therefore already
  fully distinct from pin; the verification is satisfied by inspection, not by code.

  Validated as a user on an isolated dev home (port 10735): seeded four items with one
  reading / one read / one favorited, confirmed the chips render with correct counts
  (Reading 1 · Unread 2 · Read 1 · Favorites 1), and confirmed **every filter returns
  exactly the right item(s)** and toggling off restores all four. Drove the reader's full
  read-state cycle and favorite toggle, confirmed both persisted, and confirmed the
  non-touching guarantee. Zero console errors. Gate: `make lint` green · `make test`
  **8912 passed** · web typecheck + 283 vitest + build green.

  **T2.1 is now complete.** Remaining in S2: **T2.2 (tags taxonomy)**, which still needs
  the owner re-scope ruling recorded in the T2.3 log above.
- 2026-07-29 — **DONE (S2: T2.2 tags taxonomy).** Tags moved from a JSON column on
  `items` into an AUTHORITATIVE `tags` + `item_tags` pair; the column is DROPPED. Owner
  ruling (option B) over my recommendation of a derived registry.

  **The clean-break shape.** The plan's done-when asked for "old JSON still readable
  during dual-path", which pre-Lifecycle-Doctrine doctrine forbids — but that was ONE
  CLAUSE, not a blocker, and treating it as one was my error (the owner corrected it).
  Executed as a single-pass migration with no dual path.

  **Schema decisions that differ from the plan's §32 sketch, deliberately:**
  - **Surrogate `id INTEGER PRIMARY KEY` with `name` merely UNIQUE**, not `name` as the
    PK. A rename is then one row instead of a cascade across every membership row and
    every child's parent pointer. (`mem_entities` is the in-repo precedent.)
  - **`parent_id` is NULL for a root**, not `''` — it is what a self-FK can express, and
    it keeps "no parent" out of the name space. `ON DELETE SET NULL` re-parents children
    rather than cascading, so deleting a parent never destroys the branch beneath it.
  - **Usage counts are COMPUTED from the join, never stored.** A denormalized
    `usage_count` cannot express the active-plus-non-archived scope that `all_tags` and
    `corpus_overview` both use (archiving an item would have to decrement every one of
    its tags), and two shipped tests pin that scoping.
  - **`item_tags.source` records PROVENANCE** ('user' | 'ai') — see the runner rewrite.

  **The FTS table now sources from a VIEW (`items_fts_src`), not `items`.** This is a
  correctness requirement, not tidiness: an external-content FTS5 table's column list is
  fixed at creation and its `content=` target must be able to produce every column.
  Pointing it at `items` after the `tags` column is gone makes
  `INSERT INTO items_fts(items_fts) VALUES('rebuild')` **WIPE THE INDEX AND REPORT
  SUCCESS** — measured, with `integrity-check` still reporting ok afterwards. No
  `rebuild` call exists in the repo today, so this was a latent trap for whoever added
  one. A test now asserts a rebuild preserves recall.

  **The migration is the unrecoverable step, so three guards:** (1) a per-row PYTHON
  parse rather than SQL `json_each` — `json_each` RAISES on malformed values, and those
  genuinely exist because `_serialize_item` has always swallowed `JSONDecodeError`, so
  SQL would abort the migration or (wrapped in a bare except) drop that item's tags;
  (2) COUNT RECONCILIATION before `DROP COLUMN`, rolling back on mismatch; (3) the FTS
  table dropped and recreated, never rebuilt. Guarded on `"tags" in cols`, which is the
  whole idempotence mechanism — knowledge.db has no schema version, matching how
  `_init_schema` relies on `IF NOT EXISTS`.

  **PROVENANCE REPLACES INFERENCE in `pipeline/runner.py`.** The old code decided whether
  the AI could refresh an item's tags by comparing them against the previous run's
  `insights.topics` as ORDERED LISTS. Rows come back name-ordered, so that comparison
  would have broken silently: an AI-seeded item whose topics were emitted in a different
  order would compare unequal, the refresh branch would stop firing, and a content-edited
  item would keep stale tags forever. Now `store.tags_are_all_ai_authored(item_id)` asks
  who wrote each tag. **A canary test was verified to FAIL against the old mechanism**
  with the exact message it was written for — the two pre-existing "preserve user tags"
  tests stay green even in the degraded case, so they could not have caught it.

  **BUG IN MY OWN FIRST IMPLEMENTATION, found by running it.** `_resync_fts_for_items`
  used `DELETE FROM items_fts WHERE rowid = ?`, which is a **SILENT NO-OP on an
  external-content FTS5 table** — proven in isolation. A renamed tag stayed findable
  under BOTH names. The correct form is FTS5's `'delete'` command with the EXACT prior
  column values, which means snapshotting them BEFORE the tag rows change (the index
  cannot be asked what it stored). Now `_fts_snapshot` + `_resync_fts`, with three tests
  covering rename, merge and delete. This is the fifth FTS write path and it did not
  exist before tags were rows — missing it is how tag search rots while writes succeed.

  **A pre-existing bug fixed as a side effect:** `json.dumps` defaults to
  `ensure_ascii=True`, so a tag like `日本語` was stored — and therefore indexed — as
  `日本語`, making it **unsearchable**. Measured 0 matches before, 1 after.

  **The external contract is UNCHANGED.** `_serialize_item` still emits `list[str]`, so
  the 2 agent tool schemas, `reference/tools.md`, the HTTP layer and the whole frontend
  needed no contract change — verified by `git diff` on `reference/` and
  `builtin_tools.py` coming back empty. (The plan says 3 schemas; the third,
  `task_search`, belongs to the tasks store and is out of scope.) List paths batch the
  tag lookup via `_serialize_items` so normalization is not an N+1 on the busiest read.

  **Surfaces.** `GET /api/knowledge/tag-tree` (ids + parents + live counts) alongside the
  untouched flat `GET /api/knowledge/tags` autocomplete contract; PATCH (rename and/or
  re-parent), POST `/merge`, DELETE. A new **Tags** view on the Knowledge page renders the
  taxonomy as an indented list with counts and a right-click menu for
  rename/nest/merge/delete. Flat-with-indentation rather than collapsible: the hierarchy
  is one level deep in practice and the point of the screen is to SEE the shape. Every
  mutation re-seeds from the server's returned tree rather than patching local state,
  because a rename can move a child and a delete re-parents to root.

  **Cycle guard added that the mirrored precedent lacks:** `dashboard/chat_folders.py` has
  parent/child with NO cycle detection, so A→B→A is constructible there. Here a cycle
  walk rejects self-parenting and multi-hop loops (with a visited set, so a pre-existing
  cycle can't hang the check).

  **Frontend fix required by the change:** `KnowledgeDetail`'s dirty check compared tags
  as ORDERED lists (`JSON.stringify`). Name-ordered rows would have made every save look
  dirty and fire a pointless PATCH — which re-syncs the search index, so not free. Now a
  set comparison.

  Validated as a user on an isolated dev home (port 10736): seeded four tagged items,
  confirmed the Tags view renders with correct live counts, nested `async` under `rust`
  from the context menu and saw it indent + persist, then over HTTP confirmed the cycle
  guard returns a typed `tag_cycle` 400, a merge reports `moved`/`already` correctly, and
  a rename collision returns `tag_name_taken`. Zero console errors.

  Tests: 24 new store/taxonomy cases in `test_knowledge_collections.py` (61 in file) +
  11 route cases in `test_knowledge_bulk_api.py` (21 in file), including the hostile
  migration fixture (duplicates, blanks, non-ASCII, malformed JSON, NULL, AI-authored)
  and an idempotent-reopen test. Gate: `make lint` green · `make test` **8942 passed** ·
  web typecheck + 283 vitest + build green.

  **S2 is now COMPLETE** (T2.1 + T2.2 + T2.3). Remaining in the plan: Session 3
  (reading view, dedup/merge, library home).

- 2026-07-30 — **PARTIAL (Session 3: T3.2 backend + API). DEVIATION: scoped to dedup/merge.**

  **What landed:** `find_duplicates` + `merge_items` in the store, plus
  `GET /api/knowledge/items/{id}/duplicates` and `POST /api/knowledge/items/{id}/merge`.

  **DEVIATION — T3.1 (reading view) and T3.3 (library home) NOT taken.** Both are substantial
  frontend surfaces (a reading type scale + in-reader highlight→note; a composable home with
  continue-reading), and T3.3 explicitly wants coordination with AMBIENT-SURFACES' tile registry,
  which is unbuilt. T3.2 is the one task that is **complete in itself** and unblocks nothing
  else: a merge is either correct or it destroys data, so it does not want to be half-done
  beside two unfinished UI surfaces. Recorded per the sprint's decide-and-continue rule; the two
  remaining tasks stay S3's scope for a session that can do the reading UI justice.

  **Reused rather than re-derived.** `find_duplicates` wraps the existing TIER-2 prefilter +
  `dedup.resolve_duplicate` scorer instead of inventing a second notion of "duplicate" — the
  resolver already encodes the real rule (filename/title similarity AND cosine AND same
  series-date token), and a second heuristic here would disagree with the ingest-time dedup in
  ways nobody could explain. An item without an embedding returns **no** candidates: it cannot be
  scored, and guessing from titles alone is how a merge UI proposes destroying two unrelated
  documents.

  **What a merge must not lose, and why each is tested.** The survivor inherits **both** items'
  collection memberships, tags and entity mentions — a merge that dropped the losing copy's shelf
  membership would quietly undo the user's curation. It also keeps the **stronger** signal from
  either copy (read-state by rank, favorited by OR): merging a read+favorited copy into an unread
  one must not demote it. Relations discovered FROM the loser are re-attributed so the graph
  edge's provenance doesn't dangle at a deleted item.

  **The FTS landmine, handled by reuse.** `items_fts` is an EXTERNAL-CONTENT table where a plain
  `DELETE` is a silent no-op and a mismatched `'delete'` corrupts the posting list without
  raising. Rather than re-implement that contract, the merge calls `_delete_item_cascade`, which
  already owns it (reading the indexed values BEFORE `item_tags` rows go away). Two tests assert
  the loser's title stops being findable and the survivor's still is.

  **Guard rails.** A self-merge raises rather than running the cascade delete on the survivor and
  destroying the item it was asked to keep. The whole merge is one transaction, so a failure
  leaves **both** items intact — a partial merge is a corrupted library. The HTTP route takes the
  survivor as the path parameter and the loser in the body (so the destructive half is never the
  id a client reuses from a list view), and requires `confirm: true`.

  **Validated as a user** on an isolated dev home (port 10749, never :10000) through the real API:
  two near-identical notes with different tags merged — the survivor ended with **both** tags, the
  loser **404s**, and the duplicates endpoint responds. Then, on a second pair, the loser was
  marked `read` + favorited and merged into an `unread` survivor: the survivor came out
  **`read_state: read, favorited: true`** — the stronger signal won. `confirm: false` was refused
  with a typed error. **0 gateway tracebacks.**

  *(A false trail: `read_state` is not PATCH-able — it has dedicated `read-state`/`favorite`
  routes. My first attempt got a correct 400, not a bug.)*

  **Gates:** `make lint` clean (mypy 556 files) · `make test` **9494 passed, 0 failed**.
  Tests: `tests/test_knowledge_merge.py`, 29 cases.
