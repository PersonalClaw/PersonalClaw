# KNOWLEDGE-LIBRARY — atomic plans

**Source plan:** [`KNOWLEDGE-LIBRARY`](../plans/KNOWLEDGE-LIBRARY.md)  
**Code:** `KL`  
**Source status:** in_progress

Plan status is IN PROGRESS. Done: S1 collections primitive, all of S2 (read/favorite curation, tags taxonomy, bulk ops), and the S3 dedup/merge store+API backend. S3's reading view (T3.1) landed as `KL-7`: a `?read=1` reading mode on `KnowledgeDetailPage` at the shared editorial type scale, a scroll-progress ring, and text-anchored highlights persisted in a dedicated `annotations` table (the plan's open question resolved AGAINST reusing `mentions` — see `KL-7`'s DONE block). S3's dedup/merge FRONTEND landed as `KL-6` — and driving it found that T3.2's backend "surfacing half" had shipped INERT: `find_duplicates` read `getattr(verdict, "is_duplicate", False)` on a `DupVerdict` whose field is `is_dup`, so it returned `[]` for every input in existence; `KL-6` fixed that and added the positive test whose absence hid it. Not started: S3 library home (T3.3, wants AMBIENT-SURFACES coordination), and the tail of the H1.1-H1.5 indexing amendment. Indexing landed: H1.1+H1.2 (`KL-9`, chunks table + structural chunker + chunk embedding in ingest), H1.3 (`KL-10`, chunk-level vector arm with max roll-up to items + chunk-derived locators) and H1.4 (`KL-11`, a `sqlite-vec` `vec0` index over the chunk vectors inside the same `knowledge.db`, with a cached probe that fails soft to the exact scan and a Doctor capability line — measured 40.1 ms → 2.2 ms per vector-arm query at KL-10's own 1,800-row benchmark shape, at recall 1.0000 against the exact scan). Indexing landed in full: H1.5 (`KL-12`) added the resumable batched chunk backfill (`knowledge/chunk_backfill.py`) plus its boot hook, and the VH validation held — on `origin/main` a deep-answer question returned only the wrong (title-matching) document with no section citation and never retrieved the document holding the answer; after the backfill the right document ranks 1 and is cited to its mid-document section and exact line span. Vector-arm latency on a 300-item/2,100-chunk library: 5.45 ms with the ANN index vs 48.84 ms with it force-disabled, recall@20 1.0000. The H1.1-H1.5 indexing amendment is now COMPLETE. Remaining KL work is library UX only: `KL-8` (library home) — `KL-6` (dedup/merge frontend) and `KL-7` (reading view) are done. The indexing amendment was a distinct sub-scope from the library UX and sequenced on its own linear chain.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `KL-1` | ✅ | S1 Collections primitive: schema, store API, routes + Knowledge-page rail | — | collections + collection_items tables and read_state/favorited item columns added via the store's own additive ladder (store-native, no lifecycle gate); create a manual shelf and a smart shelf, both resolve to the same item shape; rail deep-links via ?collection=; a new matching item appears on a smart shelf with no backfill; smart shelf refuses membership writes with typed smart_collection_immutable |
| `KL-2` | ✅ | S2 read-state + favorited display and filters | `KL-1` | favorite gets its own star glyph (distinct from pin), a reading badge marks in-progress, read items dim; Reading/Unread/Read/Favorites filter chips with live counts appear only when the state is present; reader has a read-state cycle + favorite toggle routed through the non-touching curation endpoints (updated_at verified unchanged); pin has no retrieval weighting so favorite-vs-pin distinctness holds by inspection |
| `KL-3` | ✅ | S2 tags taxonomy: authoritative tags + item_tags tables, JSON backfill, hierarchy UI | `KL-1` | tags moved into authoritative tags + item_tags (surrogate id, parent_id self-FK with ON DELETE SET NULL, computed counts), JSON tags column DROPPED; single-pass idempotent backfill guarded on column presence; FTS sources from items_fts_src view so rebuild preserves recall; cycle guard rejects self/multi-hop parenting; GET /api/knowledge/tag-tree + Tags view render the hierarchy with live counts; external list[str] tag contract unchanged |
| `KL-4` | ✅ | S2 bulk operations: bulk_apply + /api/knowledge/bulk + multi-select bar | `KL-1` | bulk_apply + POST /api/knowledge/bulk with 7 reversible ops (collect/uncollect/read_state/favorite/archive/restore/pin), per-item best-effort reporting changed/unchanged/missing; 500-item cap; delete deliberately excluded; typed 400s for missing args and smart_collection_immutable; read_state/favorite stay non-touching through the bulk path; multi-select bar wired with a shared Checkbox primitive; HTTP-level test suite added |
| `KL-5` | ✅ | S3 dedup/merge store + API backend (find_duplicates, merge_items, routes) | `KL-1`, `KL-3` | find_duplicates wraps the existing TIER-2 prefilter + dedup.resolve_duplicate scorer (no embedding → no candidates); merge_items runs as one transaction where the survivor inherits both items' collections, tags and entity mentions plus the stronger read-state/favorited signal; FTS handled via _delete_item_cascade; self-merge refused; GET /api/knowledge/items/{id}/duplicates + POST /api/knowledge/items/{id}/merge (survivor in path, loser in body, confirm:true required) |
| `KL-6` | ✅ | S3 dedup/merge frontend: near-duplicate surfacing UI with merge action | `KL-5` | the Knowledge UI surfaces near-duplicate candidates for an item and a merge action drives the existing GET /duplicates + POST /merge routes; two near-dupes merge from the UI, the survivor keeps both items' collection memberships + mentions, and the loser 404s; reduced-motion/theme/token-lint pass on the new UI |
| `KL-7` | ✅ | S3 reading view: editorial type scale, progress, in-reader highlight→note | `KL-1` | KnowledgeDetailPage gains a reading mode with the editorial-document house-style reading type scale and a progress indicator; an in-reader highlight persists as a mention/note linked to the item and reappears on the item; a long article reads well; reduced-motion/theme/token-lint pass |
| `KL-8` | ⬜ | S3 library home: recently-added / continue-reading / favorites / collection counts | `KL-1`, `KL-2`, `KL-7`, `EXT:AMBIENT-SURFACES:tile-registry-for-composable-library-home` | a library home component renders live per-collection counts, recently-added, favorites, and a continue-reading section that resumes at the persisted reading position; built as a composable surface that consumes the AMBIENT-SURFACES tile registry if landed, standalone otherwise |
| `KL-9` | ✅ | Indexing H1.1+H1.2: chunks table + structural chunker + chunk embedding in ingest (retire 1000-char top-up) | — | a chunks table (id,item_id,chunk_index,text,embedding,section,line_start,line_end) and knowledge/chunking.py exist; long markdown/PDF/pptx items chunk on real structural boundaries (headings/slides/sheets) with size fallback + overlap; structureless items chunk by size; chunk section/line_start/line_end are populated; the item row keeps its whole-item embedding; ingest embeds chunks and the compose_item_text 1000-char top-up is deleted (clean break, no dual path); a test retrieves content deep in a long document that fails before the change |
| `KL-10` | ✅ | Indexing H1.3: vector arm searches chunks and rolls up to items before RRF | `KL-9` | the vector arm queries chunk vectors and rolls chunk hits up to their item before RRF; fusion, cliff-cut, _VECTOR_MIN_SIMILARITY and the returned item shape are unchanged on a fixed corpus; a chunk hit returns an item-shaped result whose section/line_range locator is asserted at least as specific as today's item-level one; items lacking chunks fall back to the whole-item vector (partial-chunk libraries degrade, never return zero) |
| `KL-11` | ✅ | Indexing H1.4: sqlite-vec ANN index with cached probe, fail-soft to exact scan, Doctor line | `KL-10` | sqlite-vec>=0.1,<1 added to core dependencies with a WHY comment; an ANN index over chunk vectors lives inside the vector arm; a cached enable_load_extension probe fails soft to the existing exact scan with a one-time INFO log; Doctor reports the degraded capability line; a recall-tolerance test asserts ANN-vs-exact match within the stated tolerance and a force-disabled-extension test proves correct results still return via exact scan; faiss and the [embeddings] extra are untouched |
| `KL-12` | ✅ | Indexing H1.5: resumable batched chunk backfill + VH validation | `KL-9`, `KL-10` | a resumable, batched, progress-reporting backfill (modeled on reembed_all + the ingest queue's recover_pending) chunks existing items; interrupting and restarting resumes without duplicating or skipping; mid-backfill search returns sensible results (degrades to whole-item vectors, never zero); VH holds — a deep-answer question on a 30+ page PDF/long markdown is retrieved and cited to the right section (failing on main first), search latency measured before/after ANN on a large seeded library, backfill interrupt/resume verified, full local gate green |

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

**Status:** done

Session 3 — T3.2 (frontend consumer; the half not yet shipped — status line: 'no frontend consumer yet')

**Done when:** the Knowledge UI surfaces near-duplicate candidates for an item and a merge action drives the existing GET /duplicates + POST /merge routes; two near-dupes merge from the UI, the survivor keeps both items' collection memberships + mentions, and the loser 404s; reduced-motion/theme/token-lint pass on the new UI

**DONE.** A `Possible duplicates · N` section in the item's **More details** panel
(`DuplicateList.tsx`), placed second — above the read-only sections, because it is the only one
there that asks the user to DO something — and counted into the `More details · N` badge. Each row
carries the scorer's own `reason`, the loser's word count and age, an **Open** (inspect before you
destroy) and a danger **Merge into this item**. The merge is gated by a `danger` `confirm()`, which
`DialogShell` promotes to `role="alertdialog"` with focus on Cancel.

**🔴 THE BACKEND SURFACING HALF WAS INERT, and this atom found it by driving it.**
`store.find_duplicates` tested `getattr(verdict, "is_duplicate", False)` — a field `DupVerdict` does
not have (it is `is_dup`) — so the `getattr` default won on every comparison and the route returned
`[]` for **every input in existence**, however identical the two items. A hand-built pair scoring
`filename_sim=1.0`, `cosine=0.9949`, `is_dup=True` surfaced nothing. The three tests that existed
were all negative (no-embedding, unknown-item) or **vacuous** — the never-return-the-embedding loop
iterated ZERO rows — so a total outage read as covered. Fixed to a direct `verdict.is_dup`
(the `getattr` indirection is what made the typo silent; an attribute access RAISES), plus the
positive test, a not-a-duplicate counter-test, and a length floor on the leak loop. `KL-5`'s
`done_when` was therefore never actually met; this atom completes it.

**DEVIATION — the confirmation names the two copies by POSITION, not by title.** The first draft
read *Merge "X" into "X"?*, naming neither item: `find_duplicates` requires title similarity ≥ 0.85,
so a candidate nearly always shares the survivor's title and very often matches it exactly. That is
the defining case, not an edge case. So the survivor is "the item you have open" (unambiguous by
construction) and the loser is identified by the metadata that differs (word count + age).

**Direction is one-way on purpose.** The survivor is always the item on screen; the reverse is
reachable by opening the other copy, and the dialog says so. Two destructive buttons per row
differing only in which of two identically-titled documents dies is how a merge UI destroys the copy
the user meant to keep.

**An empty list and a failed lookup are different answers**, and this surface is the sharpest case in
the app: "no duplicates" is the correct answer for almost every item, so a swallowed rejection is
indistinguishable from the truth. `api.knowledgeDuplicates` carries no `.catch(() => [])`, the page
STORES the rejection, and the section mounts when there are candidates **or** the lookup failed —
rendering a `role="alert"` with the server's own message, "This item may still have duplicates", and
a Retry, and no merge control. The title is plain text with a separate `Button` for Open: `Button` is
`whitespace-nowrap` by contract so a truncating title cannot be one, and hand-rolling the element
would trip the primitive-adoption ratchet.

### `KL-7` — S3 reading view: editorial type scale, progress, in-reader highlight→note

**Status:** done

Session 3 — T3.1 (reading view); Open question (annotations as mentions vs dedicated table — default: reuse mentions)

**Done when:** KnowledgeDetailPage gains a reading mode with the editorial-document house-style reading type scale and a progress indicator; an in-reader highlight persists as a mention/note linked to the item and reappears on the item; a long article reads well; reduced-motion/theme/token-lint pass

**DONE.** `?read=1` on `#/knowledge/item/<id>` is reading mode — a navigable URL state beside
`?details=1`, published as a constant-label `Reading mode` `HeaderControl` (soft-off with a
reason on an item with no text body). The reader replaces the preview + insights dock rather
than sitting beside them, and keeps the metadata/tag rows.

**The type scale is the SAME editorial scale `.doc` already gave the `document` content type**
— one house style, two hosts, rather than a second scale. Making that reuse real needed one
change to the block: it moved OUT of `@layer base`. `ui/Markdown` pins every prose element with
Tailwind utilities tuned for CHAT density (`p`/`li` at 0.9375rem, `h2`-`h4` at 1.0625rem, all on
`text-on-surface-var`), and utilities live in `@layer utilities`, which beats `@layer base`
regardless of specificity — so a layered `.reading` would have been INERT while reading as
correct code. Unlayered normal declarations outrank every layered one; that is the whole
mechanism, and it is the same argument the `::highlight()` registry at the end of `tokens.css`
already makes. `.doc`'s own host renders raw HTML with no competing utilities, so unlayering it
changes nothing there. What it cannot reach is the inline `style={fvs(500)}` on `h2`-`h4`
(subheads keep weight 500 rather than 640); beating an inline style needs `!important`, which is
a worse trade than a slightly lighter subhead.

**DEVIATION — the annotations open question resolves to a DEDICATED TABLE, not `mentions`.**
The plan's default was "reuse `mentions`; promote to its own table only if reading-notes need
richer structure (revisit in S3)", and S3 is where it gets revisited. `mentions` is
`(item_id, entity_id)`-keyed, so a highlight there would have to MINT AN ENTITY per highlighted
sentence — putting reading debris into the entity graph, the `/entities` surfaces and
orphan-pruning — and `mentions.context` cannot carry a re-anchoring locator. So
`annotations (id, item_id, quote, occurrence, note, created_at)`, added via the store's own
additive `CREATE TABLE IF NOT EXISTS` ladder (the same store-native route KL-1 took), with
`ON DELETE CASCADE`. Three routes: `GET`/`POST /api/knowledge/items/{id}/annotations` and
`DELETE /api/knowledge/annotations/{id}` (keyed by the highlight's own id — nesting it under the
item would let a caller delete row A while naming item B).

**Anchoring is by TEXT, not by offset**, and that is forced: the reader renders markdown, so a
character index into the item's source does not survive the transform. A highlight stores its
`quote` plus which `occurrence` of that string it is, and `readingAnchors.ts` is the invertible
pair — `anchorFromSelection` flattens the article's text nodes and reports (quote, occurrence);
`markAnchors` flattens the same way and paints the Nth match, one `<mark>` per text node the
range crosses (a range spanning a `<strong>` or a paragraph break has no single valid wrapper).
The quote comes from the flattened text, NOT `Selection.toString()`, because those two normalize
whitespace differently across block boundaries and an anchor that cannot find its own quote is
inert. An anchor whose passage was edited away stops painting, is COUNTED AND REPORTED in the
reader, and still lists on the item — a highlight is a note about a passage and outlives it.

Curation invariants held: highlighting is a NON-TOUCHING write (`updated_at` asserted unchanged,
same contract read-state and favorites hold), and `merge_items` now moves annotations to the
survivor alongside collections/tags/mentions — a merge that dropped them would quietly delete
the reader's own work.

The highlights are owned by `KnowledgeDetailPage`, not the reader, so ONE fetch feeds both the
painted prose and a `Highlights · N` section in the More-details panel; deleting there re-paints
here. That is what makes "reappears on the item" true whether or not the reader is open.

A11y/motion: the floating `SelectionPill` (the existing shared primitive) is the POINTER
shortcut only — the keyboard route is a persistent `Highlight selection` button in the reading
rail, enabled by a `selectionchange`-driven capture so a shift+arrow selection reaches it;
`SelectionPill` activates on `onMouseDown` alone and would be unreachable by keyboard as the sole
affordance. Progress is the shared `ProgressRing` (already `role="progressbar"` + named), the
article region carries the `tabIndex`/`role`/`aria-label` scroll trio, and per-row remove
controls are named with their passage.

**Two defects came from DRIVING it, not from a test** (a seeded 1,633-word article on a real
gateway, scripted Playwright, both themes). (1) **The measure was 101 characters per line.** The
document reader caps at `72ch`, which sounds like 72 characters and is not — `ch` is the advance
of "0", 0.66em in this font, so `72ch` resolved to 758px. Re-set to `35rem`, re-measured at **69
characters**, inside the 45-90 band. (2) **The title printed twice.** A saved article's body
normally opens with the headline the item is titled after, so the reader's own title heading and
the body's `#` said the same words one line apart at nearly the same size. Now suppressed when the
body's first heading matches the title (`bodyOpensWithTitle`), with tests both ways — and the
regression test asserts its fixture really does repeat the headline, since a fixture without one
would pass against no fix at all.

**Falsifications recorded.** Deleting the `INSERT` from `add_annotation` reds 9 tests including
`assert [] == ['persisted passage']` from a FRESH store handle — the assertion component state
cannot pass. Removing the API call from the reader's save reds 3. Dropping ProgressRing's
`reduce ?` guard reds the reduced-motion file with 25 swept offsets where a jump must show none.
Hard-coding `occurrence: 0` reds the repeated-passage round trip. **One mutation reded nothing:**
removing `self.db.commit()` from `add_annotation` — the connection is `isolation_level=None`
(autocommit), so all 34 `commit()` calls in `knowledge/store.py` are decorative. Pre-existing
file-wide convention, not introduced here; mine stay for consistency with the other 33.

**Also recorded: a test-isolation trap.** framer-motion reads `prefers-reduced-motion` ONCE into
a module singleton, so a `matchMedia` stub applied after any earlier render in the same file is
inert — the assertion reported 25 swept samples under a stub claiming reduce. The reduced-motion
case therefore lives in its own file (`readingViewReducedMotion.test.tsx`) with the stub at module
scope, paired with a tweens-when-allowed case in the main file so neither side is vacuous.

Not in scope, and left for `KL-8`, which declares it: persisting a reading POSITION. This atom's
progress indicator reports how far through the article the reader is; "resumes at the persisted
reading position" is `KL-8`'s own done-when clause.

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

**Status:** done

Amendment task table — H1.5 (backfill) and VH (validation); Amendment Design (c) re-embed as a migration concern

**Done when:** a resumable, batched, progress-reporting backfill (modeled on reembed_all + the ingest queue's recover_pending) chunks existing items; interrupting and restarting resumes without duplicating or skipping; mid-backfill search returns sensible results (degrades to whole-item vectors, never zero); VH holds — a deep-answer question on a 30+ page PDF/long markdown is retrieved and cited to the right section (failing on main first), search latency measured before/after ANN on a large seeded library, backfill interrupt/resume verified, full local gate green

