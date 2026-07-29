# Plan: Knowledge Library — Collections, Curation, and Reading

**Status:** IN PROGRESS — Session 1 (collections + item curation) shipped 2026-07-29; Sessions 2-4 not started. Created as DESIGNED — created 2026-07-18 (roadmap rev 10; owner ask: more library-management capabilities for knowledge articles)
**Created:** 2026-07-18
**Wave:** 2 (S1-2: collections + curation) + 3 (S3: reading experience + saved views)
**Depends on:** nothing hard (builds on the shipped knowledge store). Coordinates with KNOWLEDGE-SYNTHESIS (5 — synthesis nodes produce library items), WATCHED-SOURCES (15 — watched sources land in collections), DESIGN-SYSTEM-CONSISTENCY (51 — the library UI is a flagship consistency surface), MEMORY-GRAPH-AND-VAULT (14 — knowledge-side graph is distinct: knowledge.db = the user's items).
**Scope:** turn the knowledge base from a flat searchable list into a **managed library** — collections/shelves, curation lifecycle (read/unread/favorite/archive), taxonomy (tags → hierarchy + saved views), a real reading experience, dedup/merge, and bulk management. **Soul guardrail:** knowledge.db is *the user's personal items* (per the roadmap's Memory-vs-Knowledge boundary) — this plan is library UX over that store; it does NOT touch memory.db mechanics. Class **B** (knowledge.db schema additions → gate `knowledge_library` + migration, plan 31). Additive schema only; every existing item stays valid.

---

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
| T1.1 | Schema + migration (collections, collection_items; the two item columns via the additive-column pattern at `store.py:276`); gate `knowledge_library` | `knowledge/store.py`, `lifecycle/migrations/m_*_knowledge_library.py`, gates | migration creates tables on a fixture home; existing items load unchanged; idempotent |
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
