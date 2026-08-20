# KNOWLEDGE-LIBRARY

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/KL.md`](../atomic/KL.md) as 12 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Knowledge Library — Collections, Curation, and Reading

**Status:** IN PROGRESS — Session 1 (collections + item curation) shipped 2026-07-29; **Session 2
COMPLETE** — T2.1 curation display/filters, T2.2 tags taxonomy, T2.3 bulk ops, all 2026-07-29.
**Session 3 PARTIAL** — T3.1 (reading view) landed 2026-08-16 as `KL-7`; **T3.2 is COMPLETE**
2026-08-16 as `KL-6` (the near-dupe surfacing UI + merge action), which also found that the backend
half landed 2026-07-30 had shipped **inert** — `find_duplicates` read a `DupVerdict` field that does
not exist, so it returned `[]` for every input until `KL-6` fixed it (see the execution log; the
earlier "no frontend consumer yet" note was correct but understated the state). T3.3 (library home,
`KL-8`) is the only Session-3 task not started.
The 2026-07-29 indexing amendment (H1.1-H1.5) is **COMPLETE** (2026-08-13): H1.1+H1.2 (`chunks`
table + structural chunker + chunk embedding in ingest, retiring the `content[:1000]` top-up), H1.3
(chunk-level vector arm with MAX roll-up to items), H1.4 (`sqlite-vec` in core + a `vec0` index over
the chunk vectors, cached probe, fail-soft to the exact scan, Doctor capability line) and H1.5
(`knowledge/chunk_backfill.py` + its boot hook, so libraries ingested before H1.1 gain chunks) are
all done, and VH holds. (The earlier "NOT started" note here was corrected 2026-08-04 by code audit
and superseded by `KL-9`/`KL-10`/`KL-11`/`KL-12`.) Created 2026-07-18 (roadmap rev 10; owner ask: more
library-management capabilities for knowledge articles)

---

> 📎 **Artifacts-as-a-knowledge-source lives in [PRODUCT-EXPERIENCE-PARITY](PRODUCT-EXPERIENCE-PARITY.md) §6 (#68)** — added 2026-08-05: an aggregate `artifact://` source row + an in-process change-listener that auto-ingests content-bearing artifacts (searchable but **not** listed as knowledge items), following a `knowledge/artifact_ingest.py` design. It plugs into *this* plan's source framework (`knowledge/pipeline/`, `connectors/`, `KnowledgeStore`). If you change the source-type model or the ingestion path here, read #68 §6 so the artifact source type lands compatibly.

## Context (code recon, 2026-07-18)

The store is already rich (`knowledge/store.py`): `items` table with `title, content, summary, tags(JSON), status, url, word_count, provider, is_pinned, is_archived, created_at, updated_at`; FTS5 `items_fts`; `entities` + `entity_relations` + `mentions` (the knowledge graph); `extracted_contents`; `intent_outcomes`. Retrieval: `retrieval.py::search(query, limit, include_archived=False)`; a P12 "same-type prefilter" for related items. Frontend: `web/src/pages/knowledge/` — List/Detail/Create pages, `KnowledgeGraph.tsx`, `GistEditor`, `AudioRecorder`, `knowledgeStore.ts`.

**What's missing for a *library*:** no **collections/shelves** (tags exist but are flat labels, not curated groupings); no **read/unread** state (only pinned/archived); no **saved views/smart collections** (a query you name and revisit); no **reading experience** (Detail is a data view, not a reading view with typography/progress/annotations); no **dedup/merge** UI (URL-normalization dedups on ingest, but no manual merge of near-dupes); no **bulk operations** (select-many → tag/collect/archive). Tags are JSON on the row — fine for labels, insufficient as a taxonomy.

## Design

- **S1 — Collections (the core new primitive):** a `collections` table (`id, name, description, icon, color, kind: manual|smart, query(for smart), created_at, sort`) + a `collection_items` join (manual membership) — smart collections resolve a saved FTS/filter query at read time (no membership rows). An item can be in many collections. Collections are the library's shelves; the Knowledge page gains a collections rail. Additive migration; existing items simply have no collections until curated.
- **S2 — Curation lifecycle + taxonomy + bulk:** add `read_state: unread|reading|read` and `favorited` (distinct from pinned, which is a surfacing weight) to `items`; promote tags to a **taxonomy** (a `tags` table with optional parent for hierarchy + usage counts; the row's JSON tags become references — migration reconciles); **saved views** (named filter+sort combos, = smart collections' UI); **bulk operations** (multi-select → add-to-collection / tag / archive / mark-read / delete) via a batch endpoint.
- **S3 — Reading experience + intelligence surfacing:** a proper **reading view** (tuned reading type scale — reuse the editorial-document skill's house style; progress indicator; in-reader highlight/annotation that becomes a `mention`/note linked to the item); **related-items** rail (existing P12 prefilter + entity-graph neighbors); **library home** (recently added, continue-reading, favorites, per-collection counts) — a composable surface coordinating with AMBIENT-SURFACES (20). Dedup/merge UI: surface near-duplicate candidates (URL + title + embedding similarity) with a merge action (keeps one, redirects mentions/collections).

## Contracts & Interfaces (conventions per [AGENTS.md](../../../AGENTS.md); class B — clean break under the pre-1.0 banner)

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
- **Calls:** existing `knowledge/store.py` + `retrieval.py::search` (smart collections + dedup similarity), the embedding path (dedup), knowledge.db's own additive column ladder (no separate migration framework — it does not exist).
- **Called by:** the Knowledge frontend (collections rail, reading view, bulk bar); WATCHED-SOURCES (15) lands items into a declared collection; KNOWLEDGE-SYNTHESIS (5) synthesis outputs become library items.
- **Storage owned:** the three new tables + two new item columns (all in knowledge.db).
- **Gate/migration:** `knowledge_library` (class B) + `m_*_knowledge_library` (creates tables + reconciles JSON tags → tags table; idempotent).

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

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
- **Tag-migration reconciliation** (JSON → table) is the one delicate step — a single-pass idempotent backfill keyed on inspecting the data (clean break under the pre-1.0 banner); a fixture with messy tags is the test.
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
    gate, but **`src/personalclaw/lifecycle/` does not exist** (the migration-backed
    lifecycle regime is owner-deferred). knowledge.db has a store-native additive ladder, so this went
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

**Provenance.** A capability gap analysis plus a code audit found that PersonalClaw's *retrieval machinery* is genuinely strong while the *index underneath it* is thin — and that the owner's question ("should this be fixed by the knowledge tools implementation?") resolves to **no**: it is neither a tools problem nor a library-management problem. It lands here because this plan owns `knowledge/store.py`, but it is a distinct sub-scope from S1-S3's curation work and should be sequenced independently.

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

### Amendment task table (extends this plan; run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

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
  "dual-path" JSON→table migration, which the pre-1.0 clean-break doctrine forbids
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
    **re-scope, never a blocker** — see [AGENTS.md doctrine](../../../AGENTS.md) and
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
  during dual-path", which the pre-1.0 clean-break doctrine forbids — but that was ONE
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
- [2026-08-09][KL-9] DONE: chunks table + structural chunker + chunk embeddings (H1.1+H1.2). New `knowledge/chunking.py` (`chunk_text()` + `Chunk`); a `chunks` table `(id,item_id,chunk_index,text,embedding,section,line_start,line_end)` in `store._init_schema()` (idempotent CREATE, FK ON DELETE CASCADE, `idx_chunks_item_id`) with replace/get/clear + cascade sweep; `_embed_chunks()` wired into the ingest `_embed` — chunks ADDITIVE, whole-item vector retained, re-ingest replaces chunk rows. A single markdown-heading rule captures all structured types because readers.py already normalizes pptx→`## Slide N`, xlsx/csv→`## <sheet>`, docx/md→native `#`; PDF/structureless fall back to size split with 200-char overlap; section=heading, line_start/end=1-based blank-trimmed source span. CLEAN BREAK: retired `compose_item_text`'s 1000-char body top-up (item vector = title+summary; passage recall is the chunks' job) — signature unchanged so all 3 callers stay compatible; the one stale unit test was rewritten. Reused `UnifiedEmbedder.embed()` (no new dep); chunk-embed is a graceful no-op with no model bound. Gate: black/isort/flake8/mypy=0 (737 files); 169 targeted + 173 related + 202 ratchet/inventory tests pass; no reference/ratchet drift. PR #931 (stack base off main).
- [2026-08-13][KL-10] DONE: the vector arm now searches CHUNK vectors and rolls chunk hits up to their item before RRF (H1.3). `_vector_search` keeps its exact `[(item_id, rank)] | None` return so `_rrf_fuse` and its `k` are untouched (§Risks: do not redesign fusion) — a chunk id can never reach fusion or a caller. The chunk arm joins `chunks` to `items` and honours the same `status='active'` + `is_archived` rails as the item arm, so a chunk cannot smuggle an archived item into a default search. The KL-9 dimension guard now covers chunk vectors too: a half-re-embedded library holds old-model chunk rows, and a `zip()`-truncated cosine over them would score a meaningless prefix.

  **Roll-up rule: MAX** — an item's vector score is its single best above-floor similarity, across its chunk vectors and its own whole-item vector; the floor is applied per vector BEFORE the roll-up so a weak chunk can never become an item's cited passage. Max, not mean or sum: retrieval asks "does this document contain the answer", which is a max over passages (a mean drags a 50-chunk document with one perfect passage below a 2-chunk document with two mediocre ones; a sum simply rewards length — the same bias `_TITLE_BOOST` exists to counteract in BM25). Decisively, **max is the only aggregate that leaves the score on the identical scale as the old item-level cosine, so `_VECTOR_MIN_SIMILARITY` keeps its calibrated meaning** — any averaging aggregate would silently re-scale a threshold this task is forbidden to retune (E6). Pinned by a test where six 0.5-similarity chunks must not outrank one 1.0-similarity chunk (a summing roll-up would score them 3.0 and win).

  **DEVIATION (broader than the task line, nothing dropped).** H1.3's done-when says items *lacking* chunks fall back to the whole-item vector. Implemented instead as: the whole-item scan is kept **unchanged** and simply maxed against the chunk scan, so every item consults both signals. The required fallback is then a *consequence* of the max (an item with no chunks, or with chunk rows whose embeddings are still NULL mid-backfill, contributes only its whole-item vector) rather than a conditional branch — no `NOT EXISTS` subquery, less code, and provably non-regressive. The reason not to gate the item arm on "has no chunks": after KL-9 the item vector is title+summary ONLY, and no chunk carries either, so gating would have silently dropped semantic title/summary recall for every chunked item. FTS indexes `title`, but only by literal/prefix term match — a semantic-only title match ("auth doc" against a title "Login and Credential Design") has no other arm to reach it. Costs ~N extra rows on top of the M chunk rows, where M dominates.

  **DISCOVERY — today's item-level locator is real, and its gap is precisely the semantic case.** The returned shape already carries a P12 locator (`source_type`/`section`/`line_range`/`deep_link`) built by `_attach_locator` from a whole-document query-term scan. So "at least as specific" is NOT trivially satisfied — it had to be earned, and it is, in two ways, asserted as a before/after comparison on one corpus rather than in a vacuum: (1) for a semantic-only query no term matches any line, so today's locator is the honest null `section=None, line_range=None` with no `?loc=` — the winning chunk knows exactly where it sits, so it now cites a real section and span; (2) when the query term appears twice, the whole-document scan anchors on the FIRST mention (a passing one) while the chunk-narrowed scan anchors inside the passage that actually matched, at the identical ±1-line window width, and names the right heading. The window can therefore never widen. Enabler: KL-9 chunks `item["content"]` itself and numbers lines 1-based over that same string (`enumerate(lines, 1)`), so chunk spans are directly usable as indices into what `_attach_locator` splits — verified, not assumed. Also found: the chunker's heading rule tolerates up to three leading spaces (CommonMark) while the read-time `_HEADER_RE` does not, so a chunk can name a heading the read-time scan misses; the chunk section is used as a fill when the scan finds none.

  **DISCOVERY — H1.4 is now required, not an optimization.** The exact scan reads every embedded CHUNK blob plus every item blob, so its row count goes from N to roughly N·(1 + content_chars/1500). Measured on a synthetic 300-item library with 5 chunks each at 384 dimensions: 300 rows → 6.4 ms/query, 1800 rows → 39.1 ms/query — 6.0x rows for 6.1x time, i.e. strictly linear, ~21 µs/row. Extrapolating, a 5,000-item library at the same chunk density scans ~30,000 rows for roughly 650 ms/query, which is user-visible. Mitigated here only in memory (both cursors are STREAMED rather than `.fetchall()`-ed, so peak memory is O(1) rows instead of O(corpus) — the old item arm materialized the whole table); eliminating the scan is `KL-11`/H1.4's ANN index, which slots in behind this same roll-up seam. Recorded so H1.4 is not treated as deferrable polish.

  **Behaviour-unchanged proof.** A fixed-corpus pin (`TestVectorArmUnchangedOnFixedCorpus`) was written and made GREEN on the unmodified tree first, then kept green: it pins the exact `search("token")` ordering AND its RRF **scores** to 1e-6 (so any change to `_rrf_fuse`, `k`, the title boost or the cliff cut moves the numbers), the exact result-dict key set, the `_VECTOR_MIN_SIMILARITY` floor dropping a 0.00-cosine item, and the cliff cut returning 3 of 4 indexed items. A chunkless library IS the pre-change world, so this freezes it. Ordering stability is preserved because `best` is populated in item-row order on a chunkless corpus and Python's sort is stable.

  Vectors throughout the tests are deterministic hand-written BLOBs written straight into `items.embedding`/`chunks.embedding` (no embedding model is involved in any assertion), so the cosine, floor, roll-up, RRF and cliff arithmetic is exactly reproducible; a ranking test that could not embed would prove nothing. **Falsified the central rail:** returning `c.id` instead of `c.item_id` from the chunk arm turned 5 of the 10 new tests red (roll-up, mixed fallback, max-vs-sum, locator specificity, archived rail — failures showed bare chunk hex ids against dashed item ids); restored, all green. Also corrected a stale comment in `retrieval.py` that claimed the locator is per-item because "VISION forbids chunk rows" — KL-9 landed chunk rows and this task consumes them. Gate: `make lint` clean (mypy 811 files) · `tests/test_knowledge.py` 109 passed (10 new) · full suite green.
- [2026-08-13][KL-11] DONE: a `sqlite-vec` `vec0` ANN index over the chunk vectors, inside the vector arm (H1.4). `sqlite-vec>=0.1,<1` added to CORE `dependencies` with the WHY comment the Dependency ruling asks for; new `knowledge/vector_index.py` (`probe()` + `ChunkVectorIndex`); the arm in `retrieval.py` uses it as a candidate generator; `resilience/doctor.py` grows a `knowledge.vector-index` capability probe. **`faiss` and the `[embeddings]` extra are untouched** — asserted by a test that reads `pyproject.toml` and requires `faiss` absent from core and present in `embeddings` (the assertion filters comment lines: BOTH blocks explain in prose why faiss is not core, and a naive substring scan failed on the very comment that documents the rule).

  **The index is a CANDIDATE GENERATOR, not a second scorer.** `vec0` narrows the corpus; `_consider` still applies the KL-9 dimension guard, the same cosine, the same `_VECTOR_MIN_SIMILARITY` floor and the same MAX roll-up. So ANN and the exact scan share one scoring implementation and cannot disagree on a similarity value — the only way they can differ is candidate truncation, which is one testable failure mode instead of a float-level divergence that no test would notice. `search()`'s signature, `_rrf_fuse`, its `k`, the cliff cut and the floor are untouched.

  **STALENESS — decided, not deferred.** Three layers. (1) **Write-through:** the store's only three chunk write sites (`replace_chunks`, `clear_chunks`, `_delete_item_cascade`) maintain the index on the same connection. A re-chunk mints fresh uuids, so `drop_item` runs BEFORE the old rows are deleted — an index that only removed the ids it just wrote would keep every previous generation as orphan candidates (caught in review of my own first draft, then pinned by a test). (2) **Reconciliation once per process:** the first search compares the index's row count against the live embedded-chunk count at that dimension and rebuilds on a mismatch, logged at INFO; this repairs a DB written before the index existed or by a process where the extension would not load. (3) **Stale EXTRAS are harmless:** the reader joins candidates back to the live `chunks`/`items` rows under the same `status`/`is_archived` rails, so an orphan candidate is dropped, not returned. Residual gap stated rather than hidden: equal counts with different CONTENTS (an unmaintained delete paired with an unmaintained insert) would pass reconciliation — detecting that needs the full content scan this index exists to remove, so the Doctor probe reports per-dimension `indexed` vs `live` instead, making drift visible. One index table per embedding dimension (`chunk_vec_384`), so a half-re-embedded library keeps one self-consistent index per model; that matches the reader's dimension guard exactly, since a differently-dimensioned vector is unscoreable either way.

  **MEASURED, at KL-10's own benchmark shape** (300 items × 5 chunks @ 384 dims = 1,800 scoreable rows), through the real `_vector_search`, ANN on vs the extension force-disabled: **40.13 ms → 2.23 ms per query, 18-20x**, at **recall 1.0000 at k=1/5/10/20 and byte-identical ranked lists on 20/20 queries**. At 1,000 items (6,000 rows): **133.6 ms → 6.5 ms, 20.7x**, recall 1.0000 on 67/67. Extrapolating the amendment's 5,000-item case: ~650 ms → ~30 ms.

  **DEVIATION — "queries stop scanning the full table" is not literally achieved, and cannot be with this dependency.** `sqlite-vec` 0.1.9's `vec0` KNN is an **exhaustive SIMD scan in C**, not a graph/IVF index, so the request path stays O(rows) — the win is a ~280x smaller constant (raw index: 1,800 rows 37.7 ms → 0.16 ms; 30,000 rows 633 ms → 2.3 ms). The owner ruling names `sqlite-vec` explicitly and forbids substituting faiss, so this is the dependency's ceiling, not an implementation shortfall; the seam is exactly where an approximate index would sit, so nothing moves when `sqlite-vec` gains one. Recorded because the task table's phrasing implies sublinearity that no shipped `sqlite-vec` release provides.

  **DISCOVERY — a correct index can be SLOWER than the scan it replaces, and mine was, twice.** First draft escalated `k` until `limit` distinct items survived the roll-up; on a corpus with fewer than `limit` items above the floor that target is unreachable, so it escalated to exhaustion and scored 3,180 rows where the exact scan reads 1,500 — **measured 58.5 ms vs 41.7 ms, i.e. 0.7x**. Second draft applied the fix per ATTEMPT rather than per CANDIDATE and so always decoded the whole first over-fetch (80 rows), landing at 5.8 ms. The real stop rule exploits the one property `vec0` guarantees: candidates come back in **exact cosine order**, so the FIRST candidate scoring below `_VECTOR_MIN_SIMILARITY` proves every chunk the index has not returned is also below the floor — the candidate set is COMPLETE, not truncated, and both scoring and escalation stop there. That also required keying the re-fetch by chunk id and walking `batch` in index order, because `WHERE id IN (...)` returns rows in STORAGE order and the stop rule is only sound in cosine order. Same argument, same 3 lines, applied to the item arm. Had I trusted the first "it works and recall is 1.0" reading, this atom would have shipped a slower search.

  **DEVIATION (scope, in the atom's spirit).** The whole-item arm was also lifted off the Python scan — but with `vec_distance_cosine` over the LIVE `items.embedding` column rather than a second `vec0` table. Reason: `items.embedding` has several writers (`reembed_all`, `clear_embeddings`, generic `update_item`), so a materialized item index would multiply the staleness surface, while an ordered scalar scan reads the live column and can never be stale. Leaving that arm on the Python scan would have capped the whole atom at ~6x (5,000 items × ~21 µs ≈ 105 ms of unavoidable residue). `AND length(embedding) = ?` is load-bearing, not decoration: it is the SQL spelling of the dimension guard, and without it `vec_distance_cosine` RAISES on a mixed-dimension library — failing the whole query closed instead of skipping unscoreable rows.

  **Recall tolerance: 0.95 stated, 1.0000 asserted.** Justified by the candidate-generator design (shared scoring ⇒ truncation is the only divergence) plus the completeness stop rule, so the tolerance is a floor, never a target — the test asserts `>= 0.95` AND exact equality, so a regression to a merely-tolerable 0.96 still fails. The corpus is built to make ANN actually diverge: 40 items × 12 chunks, every chunk above the floor with one item owning a run of the top ranking, so the first 80 candidates come from ~7 items and a single-shot query cannot fill a 20-item request. **The escalation is asserted to have fired** (a run that never escalated would agree trivially and prove nothing), and a companion test caps attempts at one on the SAME corpus and asserts recall drops BELOW the tolerance — the truncation failure mode made visible rather than argued away. The 3-row corpus that KL-10's tests use would have proved nothing here.

  **Fail-soft, proven not asserted.** `probe()` is `lru_cache`d and every failure path (`sqlite_vec` absent, no `enable_load_extension`, load into the real DB failing, any KNN error) degrades to the existing exact scan with the reason logged ONCE at INFO. Tests pin: the probe body runs exactly once across five searches; exactly one INFO record; a force-disabled store still returns the same ranking (same titles, same ranks, 10 results) and its chunk WRITES still succeed. **Falsified the central rail:** making the probe re-raise instead of degrade turned 3 tests red (force-disabled, cached-probe/one-log, Doctor degraded line); restored, green. An earlier accidental falsification was more useful still — the capability probe's own DDL used a column named `k`, which `vec0` reserves for the KNN limit, so a perfectly good build reported the capability ABSENT and silently ran the exact scan. It was the fail-soft path working correctly that hid it; only the coverage numbers exposed it.

  **Beyond `pyproject.toml`, the dependency touched two more surfaces.** (a) `uv.lock` — CI runs `uv sync --locked`, so an unregenerated lock fails every job before a test runs. (b) `personalclaw-backend.spec` — `sqlite_vec` ships its loadable extension as a shared library INSIDE the package, found at runtime via `loadable_path()`; it is data, not an importable module, so PyInstaller's import analysis never sees it (`collect_data_files("sqlite_vec", include_py_files=False)`, path preserved). If that ever fails to land, the desktop bundle degrades to the exact scan and says so in the Doctor rather than breaking search. Also noted: `vec0` creates SHADOW tables beside each virtual table (`chunk_vec_384_chunks`, `_info`, `_rowids`, …), so the table census matches `<prefix><digits>` exactly — a bare `LIKE 'chunk_vec_%'` swept those in, turning every delete into a failed statement and making the Doctor's coverage read throw while parsing `"384_chunks"` as a dimension.

  **Doctor:** a `knowledge.vector-index` probe under capability `knowledge`, `ok=True` in BOTH states — a correct-but-slower search is degraded, not failed, and failing hard would make a stripped SQLite build read as an outage. Degraded carries `degraded: true`, the reason and the remedy; healthy reports the indexed vector count; a drifted index reports which dimensions are out of step and that the next search rebuilds. Registered as a Doctor probe rather than a `resilience/degraded.py` contract deliberately: that registry derives availability from `can_resolve_use_case` over MODEL use-cases, so a contract with no use-cases would have reported `available: True` forever — a live reader of a key nothing writes. The plan's clause asks for the honest-degradation *pattern*, which this follows.

  **`sqlite_features()` did not exist** — PLATFORM-REACH's contract has not landed (only `sqlite_compat.probe()` for driver/FTS5/JSON1), so per the clause the probe lands here for that plan to consume; it is deliberately shaped like `sqlite_compat.probe()` (memoized, throwaway `:memory:` connection, never raises, a named remedy string) so absorbing it is a move, not a rewrite.

  **DISCOVERY — the repo's own coherence guard caught the Doctor probe.** `test_no_module_composes_the_knowledge_path_itself` lints the whole package for a second copy of the knowledge DB path (a duplicate once split the store's brain: workflows wrote one file, the UI read another, both "succeeded"). My probe composed `ctx.home / "workspace" / "knowledge" / "knowledge.db"` and went red on the full suite, not the targeted run. Fixed at the root: `knowledge_db_path()` now takes an optional `home` and a `create=False` flag, so a caller handed a home (the Doctor gets `DoctorContext.home`) can still come through the one owner of the path, and a read-only prober does not `mkdir` a directory that the state-inventory probe would then report as unclaimed.

  **Gate:** `make lint` clean (black/isort/flake8/mypy, 812 files) · `tests/test_knowledge.py` **123 passed** (110 → 123, 13 new) · full Python suite **19025 passed, 0 failed**, 30 skipped, 12 xfailed (baseline 19012 accounted; the known `test_mid_flight_kill_resumes_byte_equal` contention flake did not trip). `web/` untouched.

- 2026-08-13 — **DONE (`KL-12`: Amendment H1.5 + VH).** `knowledge/chunk_backfill.py`
  (`backfill_item_chunks`) chunks every item that predates chunking, plus
  `dashboard/embedding_reindex.start_chunk_backfill` and the
  `_backfill_item_chunks_startup` boot hook that runs it. **The H1.1-H1.5 indexing
  amendment is now complete.**

  **This atom is what makes H1.3/H1.4 reach real data.** KL-10 put the vector arm on chunk
  vectors and KL-11 indexed them, but both only help items that HAVE chunks, and every item
  ingested before H1.1 has none. Measured, not argued: on the KL-12 branch itself, a library
  with 0 chunk rows answers the VH question exactly as `main` does — the deep answer is
  unreachable until the backfill runs.

  **Resume state is the DATA, not a cursor** — the idiom both named models already use
  (`count_items_missing_embedding`'s boot auto-resume, `recover_pending`'s
  `processing_status IN ('queued','processing')`). `store.count_items_missing_chunks()` /
  `store.items_missing_chunks(limit, after_id)` define the backlog as *active, non-archived,
  content-bearing, zero chunk rows*, so an item leaves the backlog the instant its rows
  commit and a restart resumes by re-asking. Nothing is persisted, so no crash window can
  leave a cursor disagreeing with the rows. **Batch bound: 25 items** (`BATCH_SIZE`), with a
  keyset cursor on `items.id` — that bounds peak memory (item content is unbounded; a 30-page
  PDF is ~20 KB of text) *and* guarantees forward progress within a run past an item the
  chunker declines, which a re-fetch of the backlog head would loop on forever.

  **The predicate's whitespace set is load-bearing.** SQLite's `TRIM` strips only spaces
  unless the set is spelled out, while `chunk_text` tests `content.strip()`. Without
  `TRIM(content, ' \t\n\r\v\f')` an item of blank-but-not-empty content is selected forever
  and never chunks — a re-run that is not a no-op. Archived items are excluded (retrieval
  already skips their chunks, so chunking them would only grow the DB); because the predicate
  is re-derived rather than remembered, a RESTORED item simply re-enters the backlog.

  **Chunk embeddings are in scope; item vectors are not.** The backfill calls the ingest
  path's own unit — `_embed_chunks` promoted to public `embed_item_chunks` (clean break, one
  name, one path) — so chunks get vectors the same way a new item's do, and every write goes
  through `store.replace_chunks`, which is what keeps KL-11's ANN index in step. The items'
  own whole-item vectors are never touched: that is `reembed_all`'s job on a model switch, and
  Design (c) keeps it a separate migration-level concern. A test asserts the item `embedding`
  column is byte-identical across a backfill. **ANN staleness proven, not assumed:** after a
  backfill `vec_index.coverage()` is `{"384": {"indexed": 2100, "live": 2100}}` on the large
  library and `{"384": {"indexed": 181, "live": 181}}` on the VH library — and 49/49 at the
  moment of a mid-flight kill, because `replace_chunks` commits per item.

  **VH — BEFORE, on `origin/main` (811aaee4), ingested exactly as `main` would.** Corpus: five
  ~30 KB markdown documents (32 sections each) + a 32-page PDF, one local 384-dim
  sentence-transformers model, one question — *"how far behind can a replica get before it
  stops serving reads"* — whose answer is a coherent mid-document section (`## 18. Staleness
  thresholds for query routing`, lines 224-230) of **Storage Platform Guide**, sharing no term
  with the question but sitting in a document whose TITLE is not the closest match. `main`
  returned **1 hit: the title trap ("Replication Architecture", which contains the answer
  nowhere), `section=None`, and the answer's document NOT RETRIEVED AT ALL.** The same run on
  this branch with 0 chunk rows returns the identical wrong answer.
  **AFTER, same library / question / model, post-backfill: 2 hits, Storage Platform Guide at
  rank 1, cited to `'18. Staleness thresholds for query routing'` lines `[224, 230]`** — which
  is exactly the heading plus the answer paragraph.

  **DISCOVERY — a chunk vector is only as good as the chunk's topical COHERENCE, and my first
  VH corpus proved it by failing.** v2 buried the fact inside a section about something else,
  so the chunk was ~72% off-topic filler. The chunk index still found it — **rank 1 of 181
  chunks** — but at cosine **0.2025**, under `_VECTOR_MIN_SIMILARITY` (0.25), so it was dropped
  and the title trap won. Restating the section as a real document's section would read it
  (coherent prose on the actual topic) put it over the floor. Two lessons: the structural
  chunker's value is precisely that it cuts on boundaries that make chunks topically coherent
  — a size chunker would reproduce the diluted case; and a VH corpus can be adversarial in a
  way that tests the floor rather than the feature. Recorded because the first reading looked
  like "chunking does not work".

  **DISCOVERY — H1.1/H1.2 (`KL-9`) is ALREADY on `origin/main`; only H1.3/H1.4 are unmerged.**
  So the "fails on main" state is not "no chunks" but the sharper "chunks are written at
  ingest and retrieval cannot read them" — 181 chunk rows sitting inert in `main`'s DB. Worth
  noting for anyone reading the merge order.

  **Interrupt/resume, verified with a real SIGKILL, not a second call.** The live run sends
  `SIGKILL` to itself from inside the progress callback after 2 of 6 items (shell saw exit
  137 — no unwinding, no `finally`, nothing flushed that sqlite had not committed). State
  after: 2 items / 49 chunks, index 49/49, 4 pending. The resume did **`total: 4, chunked: 4`**
  — exactly the remaining work, not 6 — and afterwards: pre-kill chunk ids **byte-identical**,
  **0** duplicate `(item_id, chunk_index)` pairs, **0** non-dense `chunk_index` sequences, 0
  pending, 181 chunks total (identical to a clean single run). The unit test uses a
  `KeyboardInterrupt` from the embedder, which is a `BaseException` and so unwinds through
  every `except Exception` in the path — a genuine mid-batch interrupt.

  **Mid-backfill search never returns zero, live.** At 2-of-6 chunked, the vector arm returns
  BOTH kinds in one result set: `'network topology and connection pooling reference'` →
  `Networking Reference` (whole-item-vector only) ahead of two chunked items;
  `'observability metrics standards'` likewise. The mixed state degrades in specificity, never
  to zero — a hit on a chunk-less item just carries the honest-null locator.

  **Latency before/after ANN on a large seeded library** (300 items → 2,100 chunk vectors at
  384 dims, 10 queries × 3, through the real `_vector_search`): **5.45 ms median with the
  `sqlite-vec` index vs 48.84 ms with `candidate_chunk_ids` force-disabled — 9.0x — at
  recall@20 1.0000 (min 1.0000)**. Consistent with KL-11's own 40.13 → 2.23 ms at its 1,800-row
  shape. **Storage growth, the plan's named risk, measured rather than estimated:** the same
  backfill took the DB from **1.52 MB → 11.08 MB (+9.57 MB for 2,100 chunks)**, i.e. ~4.6 KB
  per chunk (1,536 B of float32 vector in the row + the same again in the vec0 index + text),
  and ran in **8.9 s (30 ms/item, 4.2 ms/chunk)** against a real model.

  **Falsified the central rail (resume), twice.** (a) Dropping `NOT EXISTS (SELECT 1 FROM
  chunks …)` from the batch query — "the restart re-chunks from zero" — turned **3** tests red,
  the resume test on its no-skipping assertion (2 items left un-chunked, because re-work ate
  the budget). (b) Also dropping it from the COUNT, so the budget covers every item and the
  restart duplicates without running out — turned **5** red, including
  `test_a_completed_backfill_is_a_no_op_not_a_rechunk`. Restored: green. The narrower
  chunk-id-stability assertion is preempted by the count assertions in both mutations, so it
  was checked for vacuity separately: a `replace_chunks` re-chunk does mint fresh uuids, so
  that assertion can fail.

  **The boot hook is tested as a hook, not just as a mechanism.** `start_chunk_backfill` lives
  in `dashboard/embedding_reindex.py` beside the item-vector re-index it is deliberately
  separate from, so it is reachable without starting a server; tests pin that it chunks a
  pre-existing library, that it does NOT even resolve an embedder when the backlog is empty
  (it runs on every boot), that it defers with a log when no model is ready, that it cannot
  raise into startup — and an AST test asserts `server.py` actually appends it to
  `on_startup`, since a startup hook nobody registers is inert.

  **Gate:** `make lint` rc 0 (black/isort/flake8/mypy, 813 files) · `tests/test_knowledge.py`
  **135 passed** (123 → 135, 12 new) · `tests/test_embedding_reindex.py` **12 passed** (7 → 12,
  5 new) · full Python suite **19042 passed, 0 failed**, 30 skipped, 12 xfailed (baseline
  19025 + 17 new). `web/` untouched — backend only.

- 2026-08-16 — **DONE (S3: T3.1 reading view).** `?read=1` on `#/knowledge/item/<id>` is a reading
  mode: the item's body at the editorial type scale, a scroll-progress ring, and text-selection
  highlights that persist on the item. Atom `KL-7`.
  - **T3.1 type scale — the plan says "reuse the editorial-document skill's house style", and it
    is reused LITERALLY.** `tokens.css` already carried that scale as `.doc` (for the `document`
    content type). The reading view shares the same block via a second selector rather than
    getting a second scale — an article and a document are the same reading problem.
  - **DEVIATION (mechanical, to make that reuse real): the scale block moved OUT of
    `@layer base`.** `ui/Markdown` pins every prose element with Tailwind utilities tuned for CHAT
    density (`p`/`li` 0.9375rem, `h2`-`h4` 1.0625rem, muted ink), and utilities live in
    `@layer utilities`, which beats `@layer base` regardless of specificity — so a layered
    `.reading` would have been INERT while reading as correct code. Unlayered normal declarations
    outrank every layered one; that is the whole mechanism, and it is the argument the
    `::highlight()` registry at the end of the same file already makes. `.doc`'s own host renders
    raw HTML with no competing utilities, so unlayering changes nothing there. The one thing it
    cannot reach is the inline `style={fvs(500)}` on `h2`-`h4`; subheads keep weight 500 rather
    than `.doc`'s 640, because beating an inline style needs `!important`.
  - **DEVIATION (E-none, the plan asked for this call) — the "annotations as `mentions` vs a
    dedicated table" open question resolves to a DEDICATED TABLE.** The plan's default was reuse,
    "promote to its own table only if reading-notes need richer structure (revisit in S3)"; this is
    S3. `mentions` is `(item_id, entity_id)`-keyed, so a highlight there would have to MINT AN
    ENTITY per highlighted sentence — reading debris in the entity graph, the `/entities` surfaces
    and orphan-pruning — and `mentions.context` cannot carry a re-anchoring locator. So
    `annotations (id, item_id, quote, occurrence, note, created_at)` with `ON DELETE CASCADE`,
    added through the store's own additive `CREATE TABLE IF NOT EXISTS` ladder — the same
    store-native route S1 took, no lifecycle gate.
  - **Anchoring is by TEXT, not offset, and that is forced.** The reader renders markdown, so a
    character index into the item's source does not survive the transform. A highlight stores its
    `quote` plus which `occurrence` of that string it is; `readingAnchors.ts` is the invertible
    pair (flatten the article's text nodes → report quote+occurrence; flatten the same way → paint
    the Nth match, one `<mark>` per text node the range crosses). The quote comes from the
    flattened text and NOT from `Selection.toString()`, because the two normalize whitespace
    differently across block boundaries and an anchor that cannot find its own quote is inert.
  - **The degradation is designed, not incidental.** An anchor whose passage was edited away stops
    painting, is counted and reported in the reader, and still lists on the item — a highlight is a
    note ABOUT a passage and outlives it.
  - **Curation invariants kept.** Highlighting is a NON-TOUCHING write (`updated_at` asserted
    unchanged — the contract read-state and favorites already hold to), and T3.2's `merge_items`
    now moves annotations to the survivor alongside collections/tags/mentions. A merge that dropped
    them would quietly delete the reader's own work.
  - **"Reappears on the item" is two surfaces from one fetch.** `KnowledgeDetailPage` owns the
    annotations, so the painted prose and a `Highlights · N` section in the More-details panel read
    the same rows; deleting in the panel re-paints the reader. Two independent copies would let the
    two surfaces disagree.
  - **The keyboard route is the primary one.** The floating `SelectionPill` (existing shared
    primitive) activates on `onMouseDown` only, so as the sole affordance it would be unreachable
    by keyboard. The persistent `Highlight selection` button in the reading rail is the real
    control, fed by a `selectionchange`-driven capture so a shift+arrow selection reaches it; the
    pill is the pointer shortcut. Progress is the shared `ProgressRing` (already
    `role="progressbar"` and named); the article region carries the `tabIndex`/`role`/`aria-label`
    scroll trio; per-row remove controls are named with their passage.
  - **DISCOVERY — framer-motion's reduced-motion probe is a module singleton, so a reduced-motion
    assertion is vacuous unless it owns its file.** With the case inside the main component file it
    reported 25 swept `stroke-dashoffset` samples under a `matchMedia` stub claiming reduce:
    `initPrefersReducedMotion` had already cached `false` from an earlier render.
    `readingViewReducedMotion.test.tsx` therefore stubs at module scope, before any import-time
    read, and is paired with a tweens-when-allowed case in the main file so neither side is vacuous
    on its own.
  - **DISCOVERY — every `self.db.commit()` in `knowledge/store.py` is decorative.** Removing the
    one in `add_annotation` reded ZERO tests: the connection is opened `isolation_level=None`
    (autocommit), so all 34 call sites are no-ops. Pre-existing file-wide convention, not
    introduced here; the new methods keep theirs for consistency with the other 33 rather than
    becoming the one exception. Worth knowing before anyone writes a test that believes a
    `commit()` is what makes a write durable in this store.
  - **VALIDATED AS A USER, and it found two defects a test suite could not.** A seeded 1,633-word
    article on a real gateway (own dev home, port 10077), scripted Playwright, both themes, zero
    console/page errors. (1) **The measure was 101 characters per line.** The document reader caps
    at `72ch`, which sounds like 72 characters and is not — `ch` is the advance of "0", 0.66em in
    this font, so `72ch` resolved to 758px. Re-set to `35rem`, re-measured at 69 characters, inside
    the 45-90 band a reader can return-sweep. (2) **The title printed twice.** A saved article's
    body normally opens with the headline the item is titled after, so the reader's own title and
    the body's `#` said the same words one line apart at nearly the same size; now suppressed when
    they match, with tests both ways. Also confirmed on the wire: a highlight made in one browser
    context was still painted after a reload AND in a FRESH context (different `localStorage`, same
    server) — real persistence, not a client cache; the mark's tint is the primary token at 26% with
    inherited ink, so it is legible in both themes (the UA default mark is black-on-yellow, which
    dark mode would have made unreadable); the More-details panel lists it as `Highlights · 1` with
    its note. **Probe artifact worth recording:** the first panel check read `false` because
    `innerText` returns CSS-`text-transform`ed text — the DOM says "Highlights", `innerText` says
    "HIGHLIGHTS". **Not verified in the browser:** the reduced-motion difference. CDP round-trip
    latency per sample is the same order as the spring's settle time, so browser sampling could not
    distinguish jump from sweep; that contract is proven in jsdom with frame stepping instead.
  - **Falsifications.** Deleting the `INSERT` from `add_annotation` reds 9 tests including
    `assert [] == ['persisted passage']` read back from a FRESH store handle — the assertion
    component state cannot pass. Removing the API call from the reader's save reds 3. Dropping
    `ProgressRing`'s `reduce ?` guard reds the reduced-motion file (25 swept offsets where a jump
    must show none). Hard-coding `occurrence: 0` reds the repeated-passage round trip.
  - **Not in scope:** persisting a reading POSITION. This atom's clause is a progress *indicator*;
    "resumes at the persisted reading position" is `KL-8`'s own done-when.
  - **Gate:** `make lint` rc 0 (black/isort/flake8/mypy, 873 files) · `web` typecheck clean ·
    full `npm test --workspace web` **276 files / 2771 tests passed** (no design, token,
    primitive-adoption or `uiDocs.drift` ratchet moved; `docs/design/consistency-audit.json`
    regenerated with `driftHits` unchanged at 7) · `npm run build --workspace web` rc 0 ·
    `tests/test_knowledge_annotations.py` **23 passed** (new) · `test_api_manifest_drift.py`,
    `test_agent_reference.py`, `test_roadmap_dag_derived.py`, `test_knowledge_merge.py`,
    `test_knowledge_collections.py` **137 passed** together.

- 2026-08-16 — **DONE (S3: T3.2 FRONTEND half — atom `KL-6`).** The near-duplicate surfacing UI +
  merge action. Also the atom that discovered T3.2's *backend* surfacing half had shipped inert.
  - **🔴 DISCOVERY — `find_duplicates` returned `[]` for EVERY input in existence.**
    `store.py` tested `getattr(verdict, "is_duplicate", False)`; `DupVerdict`'s field is `is_dup`,
    so the `getattr` default won on every comparison and no candidate could ever be surfaced. Found
    by driving it, not by reading it: a hand-built pair scoring `filename_sim=1.0`,
    `cosine=0.9949`, `is_dup=True` produced `{"duplicates": []}` on the wire. The other consumer of
    the same verdict (`pipeline/runner.py:899`) had `verdict.is_dup` right all along, so the two
    readers of one scorer had silently disagreed since T3.2 landed.
    **Why three green tests hid a total outage:** all of them asserted EMPTINESS. Two were
    legitimately negative (no embedding, unknown item) and the third —
    `test_duplicates_never_return_the_embedding` — was **vacuous**: a single embedded item has no
    candidate, so its `for row in …` loop ran over ZERO rows while reading as a passing guard on the
    leak. A rail that only ever asserts an empty list cannot tell a working scorer from a
    disconnected one. Fixed to a direct `verdict.is_dup` — the `getattr` indirection is precisely
    what made the typo silent, and an attribute access on a dataclass RAISES on a wrong name — and
    backed by a positive test, a not-a-duplicate counter-test (so the fix cannot degrade to "return
    everything"), and a `len(rows) == 1` vacuity floor on the leak loop.
    **Consequence for `KL-5`:** its `done_when` ("find_duplicates wraps the existing TIER-2
    prefilter + scorer") was never actually met. This atom completes it.
  - **The surface.** `Possible duplicates · N` in the item's **More details** panel
    (`web/src/pages/knowledge/DuplicateList.tsx`), second in the panel — above the read-only
    sections, because it is the only section there that asks the user to act, and two copies of a
    document get more expensive to reconcile the longer they coexist — and counted into the
    `More details · N` badge so it is reachable without opening the panel on spec. Each row carries
    the scorer's own `reason` (a merge button with no stated grounds asks the user to destroy a
    document on the app's unexplained word), the loser's word count and age, an **Open** and a
    danger **Merge into this item**.
  - **The confirmation.** `confirm({ danger: true })`, which `DialogShell` promotes to
    `role="alertdialog"` with focus on Cancel. It names the four things needed before an
    irreversible delete: which copy survives, what it inherits, which copy is deleted, and how to
    get the opposite outcome.
  - **DEVIATION — the two copies are named by POSITION, not by title.** The first draft read
    *Merge "Rust async book notes" into "Rust async book notes"?*, naming neither item. That is not
    an edge case: `find_duplicates` requires title similarity ≥ 0.85, so a candidate nearly always
    shares the survivor's title and very often matches it exactly. Rewritten so the survivor is
    "the item you have open" (unambiguous by construction) and the loser is identified by the
    metadata that actually differs. Caught by driving a real pair; a title-based dialog passes every
    render assertion and is useless in the common case.
  - **Direction is one-way on purpose.** The survivor is always the item on screen (the route's own
    asymmetry — survivor in the path, loser in the body). The reverse is reachable by opening the
    other copy, and the dialog says so. Two destructive buttons per row, differing only in which of
    two identically-titled documents dies, is how a merge UI destroys the copy the user meant to
    keep.
  - **An empty list and a failed lookup are different answers**, and this is the sharpest instance
    of that family in the app: "no duplicates" is the CORRECT answer for almost every item, so a
    swallowed rejection is indistinguishable from the truth, permanently, on the one surface whose
    whole job is to say a second copy exists. `api.knowledgeDuplicates` carries no
    `.catch(() => [])`, the page STORES the rejection rather than substituting `[]`, and the section
    mounts when there are candidates **or** the lookup failed.
  - **Primitive adoption shaped the row.** The candidate title is plain text with a separate
    `Button` for Open: `Button` is `whitespace-nowrap` by contract, so a document title in a
    resizable panel cannot live inside one, and hand-rolling the element trips the adoption ratchet
    (which counts the literal tag TEXTUALLY — a `<button>` inside a code comment tripped it once
    during this atom, so the comment now spells it out in prose).
  - **Validated as a user** (isolated home, `:10233`, two notes authored through the app; the demo
    seed's knowledge store is deliberately empty, and no embedding provider is configured in a dev
    home, so the two embeddings were written directly — the only way to make a scorable pair).
    Panel showed `Possible duplicates · 1` with the reason; the dialog named both copies; **the
    merge landed and the panel repainted live** — tags `rust` → `async, rust`, `Entities · 1` →
    `Entities · 2` (`Tokio` + the loser's `Pin API`), the duplicates section gone. On the wire:
    survivor's collections `{Survivor shelf, Loser shelf}` (BOTH), mentions `{Tokio, Pin API}`
    (BOTH), `GET` loser → **404**, `GET` survivor → 200, and the toast announced through the
    Toaster's live region: *"Merged … in — 1 collection, 1 tag, 1 mention moved across"*. The
    failure leg was driven too, by patching `fetch` to 500 on `/duplicates`: `role="alert"` reading
    *"Couldn't check for duplicates: database is locked"*, "This item may still have duplicates", a
    Retry, **no** merge control, and **no** "no duplicates" claim anywhere — and with a clean fetch
    the section is absent entirely, so the empty case is silent and the failed case is loud.
    Light theme checked; the new row/alert/danger-button read correctly and match their sibling
    sections' surface.
  - **Falsifications (3).** Dropping the confirmation gate (`if (!ok) return` → `void ok`) reds
    *"expected 'mergeKnowledgeItems' to not be called at all, but actually been called 1 times"*.
    Making the merge lose the loser's collection memberships (widening the pre-redirect `DELETE` to
    all of the loser's rows) reds both the new route test and the pre-existing store test:
    *"AssertionError: assert {'9fbe9d79-…'} == {'50c4f1f0-…'} / Extra items in the right set"*.
    Swallowing the duplicates rejection reds *"the rejection is stored, not swallowed"*.
  - **New route coverage.** `GET …/duplicates` and `POST …/merge` had **no** tests at all — every
    existing test called the store directly, so the path/body direction (swapping them deletes the
    document the user is reading) and the `confirm: true` gate were unproven at the layer the UI
    crosses. Six route tests added, including that a refused merge is a total no-op.
  - **Gate:** `make lint` rc 0 (black/isort/flake8/mypy, 895 files) · `web` typecheck clean ·
    full `npm test --workspace web` **320 files / 3298 tests passed** (no design, token,
    primitive-adoption or reduced-motion ratchet moved) · `npm run build --workspace web` rc 0 ·
    `tests/test_knowledge_merge.py` **37 passed** (was 28) · `regen_dag_derived.py --check` current.

- 2026-08-18 — **DONE (S3: T3.2 remainder — atom `KL-6`'s unmet clauses).** The frontend and the
  routes landed above; what had not was the clause they rest on — *"the Knowledge UI **surfaces**
  near-duplicate candidates for an item"*. Census of `KL-6`'s `done_when` against `origin/main`
  @ `fc1a220d`, clause by clause: merge action drives `GET /duplicates` + `POST /merge` —
  **shipped**. Two near-dupes merge from the UI, survivor keeps collections + mentions, loser 404s —
  **shipped** (`tests/test_knowledge_merge.py::test_the_route_leaves_the_loser_404ing`, `…_keeps_the_path_item_and_moves_both_sides_curation`).
  Reduced-motion / theme / token-lint — **shipped**. Swallowed merge failure and failed-fetch-as-empty-state,
  the two shapes worth suspecting here — **both clean** (`DuplicateList.tsx:45-59,121-125`,
  `KnowledgeDetailPage.tsx:119-124,252`). Surfacing — **three defects, all fixed here.**
  - **🔴 THE SECOND SILENT-EMPTY PATH ON THIS SURFACE.** `store.find_duplicates` delegated to
    `find_fuzzy_dup_candidates`, which is `ORDER BY created_at DESC LIMIT ?` — the right bound for
    the INGEST path (the anchor is the row being ingested; the cosine loop must stay cheap) and the
    wrong one read on demand, because it spends the caller's `limit` on *how many items are scored
    at all*, newest first. **Measured on a 32-item library:** an exact-title pair scoring
    `is_dup=True`, `filename_sim=1.0000`, `cosine=0.9950` returned **`[]`** at the shipped default
    `limit=25`. The route caps `limit` at 50, so past ~50 same-type embedded items the panel whose
    entire job is to say "a second copy exists" said *"no duplicates"* permanently — and the shape it
    hid is precisely the one it exists for: an old copy, a new copy, and a library grown in between.
    It failed the same way the `is_duplicate` typo did, which is why it survived that fix: on a
    surface where empty is the right answer for almost every item, a scan that never looked and a
    scan that found nothing print the same list.
    **Now bounded by the RULE, not by recency**, in two phases: the free filename leg over the WHOLE
    eligible corpus (title/path columns only, no `LIMIT`, no blob reads) via the new public
    `dedup.filename_similarity` at the resolver's own `dedup.FILENAME_SIM_MIN`; then embeddings
    decoded and cosine scored ONLY for what survives. Strictly less BLOB work than before (which
    decoded up to 25 embeddings unconditionally; this decodes only title-gated survivors — 1 in the
    benchmark below), traded for a full-table pass over two text columns. **Measured on a 2,001-item
    / 384-dim library:** new path **15.8 ms, finds the duplicate**; the old recency bound **5.9 ms,
    finds nothing** (the same corpus, the cap restored on the live line). So this is not free — it is
    ~10 ms to answer the question at all, and the honest framing is that the old number bought its
    speed by not looking. `limit` now caps RESULTS — what the route and the UI always meant. `find_fuzzy_dup_candidates` is untouched, so the ingest path is
    byte-identical; the two share eligibility (active, unarchived, same type, embedded) and differ
    only in how many they are willing to score.
  - **"Best match first" was a docstring's word, never code.** Both the store method and the route
    said it; rows came back in the prefilter's `created_at DESC` order, so the weakest match could
    head a list the UI renders top-down beside a delete button. Now sorted by cosine, then filename
    similarity, then id for a stable total order.
  - **The reason the UI shows to justify a delete was one constant.** `DuplicateList.tsx:149-151`
    renders `reason` because "without it a merge button asks the user to destroy a document on the
    app's unexplained word" — but `DupVerdict.reason` on the positive branch is the single literal
    `"fuzzy dup (filename+cosine+date-gate)"`, identical for a 0.90 match and a 1.00 one, naming the
    RULE rather than the MATCH, while the verdict's two measured numbers were dropped on the floor in
    `find_duplicates`. Telling: the frontend test's own mock (`duplicateMerge.test.tsx:40`) reads
    `'title 0.97 + cosine 0.99, same series date'` — the fixture was more informative than the wire.
    Rows now carry `similarity` + `title_similarity` and a per-candidate sentence composed in
    `store._dup_reason` (server-composed copy is a UI surface, so it is written for the person about
    to delete a document): *"Same title · content similarity 0.99"*. The exact-title case is named
    rather than scored `1.00`, because the filename leg gates at 0.85 so a surfaced candidate usually
    shares the anchor's title outright. Similarities **truncate**, never round — `0.999` must not
    print as `1.00` next to a delete button, which is a stronger claim than the scorer made.
  - **DEVIATION — no frontend diff, deliberately.** The numbers reach the user through the `reason`
    sentence the UI already renders verbatim. Adding `similarity`/`title_similarity` to the
    `KnowledgeDuplicate` interface with no reader would be a declared-but-unread field; the fields
    exist on the wire for a future consumer and the type gains them when one appears.
  - **Threshold boundary rails (the flagged "no test at its boundary").** Every pre-existing gate
    test sat far from the line (1.0 vs 0.0 cosine, disjoint vs identical titles), so both `<`
    comparisons and both constants were unpinned — a flip to `<=` or a nudge to 0.91 re-scopes which
    pairs a destructive UI proposes, with nothing going red. Added: the constants pinned to
    `(0.85, 0.90)`; the cosine floor exercised AT the threshold, one thousandth below, one above; the
    filename floor at `17/20 = 0.85` exactly, `16/20 = 0.80` below, `18/20 = 0.90` above; and that
    `filename_similarity` is the same metric the resolver gates on (the prefilter and the scorer
    disagreeing would drop confirmable pairs silently, as an empty list).
  - **Vacuity floors, because a dedup rail over a corpus with no near-duplicates passes forever.**
    The window test asserts its corpus really exceeds the default limit (else it stops testing the
    window the moment the limit rises); the ordering and reason tests assert BOTH candidates cleared
    the gates; the boundary parametrizes assert the fixture LANDED on the boundary and that the other
    leg is out of the way. That last floor earned itself immediately: the first filename-boundary
    fixture put distinct tokens on both sides, making the union `2n − shared` — `17/23 = 0.739`, not
    `0.85` — and the assertion caught it (`AssertionError: the fixture missed the boundary it
    targets / assert 0.7391304347826086 == 0.85 ± 1.0e-09`) instead of quietly testing an interior
    point.
  - **Falsifications (5), each mutated on the live line, `py_compile`d, run, then restored from a
    file copy.** (1) Recency cap restored (`ORDER BY created_at DESC LIMIT 25` back on the phase-1
    scan) → *"AssertionError: the older copy is the duplicate; a recency-capped scan reports no
    duplicates at all / assert [] == ['aff09a39-…']"*. (2) Sort inverted to weakest-first → the
    ordering test AND the result-cap test red (*"assert ['c22a4cbe…'] == ['6d071464…']"*). (3)
    `_dup_reason` reverted to `verdict.reason` → *"one constant for every row reviews nothing:
    ['fuzzy dup (filename+cosine+date-gate)', 'fuzzy dup (filename+cosine+date-gate)'] / assert
    1 == 2"*. (4) Filename floor made exclusive (`<` → `<=`) → the at-threshold case red (*"assert
    False is True"*). (5) `FUZZY_COSINE_MIN` nudged to 0.91 → *"assert (0.85, 0.91) == (0.85, 0.9)"*.
    Tree verified clean afterwards (`git status --porcelain` empty; 13 benign pre-existing
    probe-word matches, zero `MUTANT` residue).
  - **Gate:** `make lint` rc 0 (black 1789 files, isort, flake8, mypy **919 source files, no
    issues**) · `pytest tests/test_knowledge_dedup.py tests/test_knowledge_merge.py
    tests/test_knowledge_pipeline.py tests/test_knowledge_annotations.py` **144 passed**
    (`test_knowledge_merge.py` 42, was 37; `test_knowledge_dedup.py` 21, was 12) · `web` typecheck
    clean · full `npm test --workspace web` **412 files / 4154 tests passed** (no design, token,
    primitive-adoption or reduced-motion ratchet moved) · `npm run build --workspace web` rc 0.
    `duplicateMerge.test.tsx` — the known intermittent — **passed first try, 17 tests in 927 ms, in
    the union run**; not touched by this change, so no concurrency probe was needed.

- [2026-08-20][KL-14] **PARTIAL — the atom stays `todo`.** The host is built, wired and driven; two
  clauses of clause 7 are unmet and named below with their evidence rather than papered over.

  **What landed.** `knowledge/maintenance.py` is the deferred graph-maintenance host: a dirty
  watermark, a due rule, a snapshot-bounded clear, a bounded-batch claim loop and a pass registry.
  `knowledge/maintenance_passes.py` registers the standing jobs. The durability tick drives it,
  the gateway installs its in-flight probe and registers its passes, and every index-affecting
  knowledge write moves the watermark instead of doing graph work inline.

  **The clauses, one at a time.**
  1. *Writes mark a watermark, not inline work.* Five sites: `create_typed_item`, `update_item`,
     `delete_item`, `forget_source_item`, `merge_items` — each AFTER the commit, so a rolled-back
     write marks nothing. `update_item` is gated on an ALLOWLIST
     (`_INDEX_AFFECTING_FIELDS`), and the allowlist direction is load-bearing: `embedding`,
     `insights`, `ai_title` and the processing columns are what the maintenance passes THEMSELVES
     write, so a denylist would have every pass re-dirty the index it just cleaned and
     `clear_up_to(snapshot)` would find `dirty_ts` past its snapshot forever — a loop that reports
     success. An allowlist cannot acquire that bug when a column is added later; a denylist
     acquires it by default.
  2. *Due only when dirty AND (queue drained OR dirt older than max-staleness).* Both disjuncts
     asserted. TWO stamps, not one: `dirty_since` is the FIRST write since the last clean and is
     what the staleness rule reads, because a busy pipeline's LATEST write is always recent — a
     rule written against it would defer forever exactly when the backlog is largest. `dirty_ts`
     is the snapshot boundary. One stamp cannot be both, and that was the first thing I got wrong.
  3. *Coalescing and anti-starvation.* A 7-item bulk import performs ZERO passes inline and ONE
     after a host run (asserted at the host with a counting pass, not inferred from the stamp).
  4. *The snapshot.* `execute` reads `dirty_ts` before running and clears only up to it, so a
     write landing mid-pass leaves the state dirty. Falsified by clearing to `time.time()` instead.
  5. *Bounded batches.* Each `run(batch_size)` call opens and closes its own store work, so the
     lock is released between sub-batches; a pass that always claims more is capped at
     `max_batches` so a buggy job costs one tick, not the loop.
  6. *The boot hook is deleted (clean break).* `_backfill_item_chunks_startup` and
     `start_chunk_backfill` are gone — a hook that only fires at gateway start never runs again,
     which is the defect. Verified as a DELETION the way mypy cannot (`ignore_missing_imports`
     hides a stranded first-party import): a runtime import sweep over 10 modules, all clean, and
     `grep -rn start_chunk_backfill src tests` → **0**. `backfill_item_chunks` already had both a
     `batch_size` and a `max_items` bound, so the pass uses them rather than inventing one.
  7. **UNMET, in two ways.**
     * *The graph linker backfill has no callable.* Measured:
       `grep -rn "def .*backfill" src/personalclaw/knowledge/` returns
       `chunk_backfill.backfill_item_chunks` (registered), `store.backfill_entity_description` (a
       one-entity setter) and `artifact_ingest.ArtifactIngest.backfill`. The nearest thing to a
       linker pass is `knowledge_maintain_provider._reindex` / `_wikilink_mentions` — both PRIVATE
       helpers inside an action provider, neither a resumable library sweep. Registering a private
       helper across a module boundary, or inventing a public linker backfill, is a design decision
       that belongs to whoever owns the linker, not a side effect of building a host.
       `maintenance_passes._probe_unregistered_linker_backfill` carries the reason at the grep site
       and a test asserts it stays absent, so this reads as a decision rather than an omission.
     * *Consolidation is registered PLAN-ONLY.* `plan_consolidation`'s own docstring is "Everything
       a pass would do, without doing any of it", and that is the half that belongs on an
       unattended cadence today. `KnowledgeConsolidateActionProvider.execute` spends model calls and
       merges the user's items; putting that on a timer is an autonomy decision, not a cadence fix.
  8. *Tests.* 20 write-side + 25 host-side + 13 reindex, plus the store slice (491) and
     `test_knowledge.py`. Max-staleness round-trips dataclass → `_meta` → `load()` → `to_dict()` →
     `_EDITABLE_CONFIG` → the reader, asserted end to end.

  **A host-selection hazard worth recording.** The host rides the durability tick because that loop
  already exists and `gateway.py`'s rule is one dispatch path "rather than two that drift". But
  that loop gates its jobs on `durability.auto_backup`, so registering inside the gate would mean a
  user turning off scheduled backups silently loses graph maintenance — two unrelated failure
  mitigations tied to one switch. Maintenance is called OUTSIDE the gate and
  `test_maintenance_runs_with_auto_backup_off` is the proof rather than the intention.

  **A second inert-by-default trap.** The in-flight probe cannot be imported: the ingest queue is a
  lazy accessor on `DashboardState` that STARTS a worker when none exists, so asking "how busy is
  it?" would create one, and `knowledge/` importing the dashboard inverts the boundary. It is
  injected by the gateway, reads the PRIVATE attribute so probing cannot construct the thing it
  probes, and an ABSENT probe logs a warning instead of silently reading "drained" — the first
  version called a `knowledge.get_ingest_queue` that does not exist, so every lookup fell into an
  except and the coalescing clause would have been inert.

  **Gate:** `make lint` clean (mypy 936 files); 193 targeted + the store slice; full suite green.
  Ten falsifications across the three parts, each mutating the LIVE line, confirming it applied,
  observing the named red and restoring from a file copy: clear-to-now (swallowed write), staleness
  off the latest stamp (starvation), the in-flight gate dropped (a pass per tick), a single-sweep
  pass treated as batched (the lint busy-loop — 10 runs), maintenance behind the `auto_backup` gate,
  the probe not installed, the registration call dropped, the no-op-on-chunked-library guard removed,
  `mark_dirty` removed from `create_typed_item`, and inline `execute()` in the write path (the exact
  N-vs-1 defect). One test of mine failed for a real reason and was fixed rather than adjusted: the
  tick's guard lived in the durability loop, not in the seam a direct caller touches.

  **Two facts for whoever takes KL-13 (which this unblocks once clause 7 closes):** `source_seen`
  has a FOREIGN KEY to `sources`, so a fabricated `source_id` raises `IntegrityError` instead of
  exercising the novelty gate; and `maintenance`'s only clock use is `time.time()`, so
  `monkeypatch.setattr(maintenance, "time", fake)` makes the stamp inequalities exact — under real
  time the mid-run assertion flakes in the direction that HIDES a swallowed write.
