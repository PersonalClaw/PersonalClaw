# WATCHED-SOURCES — atomic plans

**Source plan:** [`WATCHED-SOURCES`](../plans/WATCHED-SOURCES.md)  
**Code:** `WS`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `WS-1` | ✅ | Source-provider contract + make the dormant knowledge seam real | — | KnowledgeSourceProvider/SourceItem/SourcePollResult defined and re-exported via sdk/knowledge.py; EntitySeamHandler no-op for `knowledge` replaced by a real KnowledgeTypeHandler (load_factory->register into knowledge_providers.registry); create_native_provider returns a real factory (no longer None); a KnowledgeProvider-shaped fixture registers and appears in list_provider_info as kind:external; test_manifest_types_match_handlers stays green |
| `WS-2` | ✅ | WatchedSource store + SourceEngine poll loop + SourcesConfig + SOURCE egress policy | `WS-1` | knowledge.db migration adds sources/source_cursors/source_seen tables + source_id/guid item columns with UNIQUE(source_id,guid); SourceEngine single re-armed asyncio loop enrolls poll-capable providers and polls a fixture source on schedule, writing items via store.create_typed_item(provider,source_id,guid)+ingest_queue.enqueue; SOURCE EgressPolicy profile added via egress_policy_for; SourcesConfig knobs round-trip (SC#12); kill-mid-poll+restart yields no dup/no loss via cursor+seen-set atomicity + recover_pending (SC#4) |
| `WS-3` | ✅ | web-source: five-detector stack, selector configs, escalating fetch, preview+create flow | `WS-2` | Pasting a real changelog/blog URL yields a correct zero-LLM item preview via auto-detection and a homepage yields the pick-a-listing-page guidance; a manual selector config rescues a JS-lite failure (SC#1); a JS-heavy source succeeds only after render-tier escalation within max_requests with the escalation recorded, and allow_render:false degrades to a 'needs render tier' health status (SC#2) |
| `WS-4` | ✅ | feed-source (RSS/Atom/JSON/CSV + HN/GitHub presets) + cross-feed dedupe + raw-mode FeedItemGraph | `WS-2` | Polling the same feed twice produces zero duplicate items and the same story arriving via HN Algolia AND RSS produces ONE item with both attributions (also_seen_in) (SC#3); a raw source's items reach FTS + vector search with zero LLM calls, asserted structurally that the raw graph contains no LLM nodes (SC#6) |
| `WS-5` | ✅ | dir-source: signature-diff observer, debounce, archive-on-delete | `WS-2` | Editing three files in a watched dir within the debounce window re-indexes each exactly once (create->new item, modify->re-enqueue existing item); deleting one archives its item with metadata source_deleted_at and never hard-deletes (SC#5); first pass seeds only (no startup ingestion storm) |
| `WS-6` | ✅ | Fetch-and-slice ingestion primitive (arXiv/DOI/PDF sniff, section detection, slices, sha256 cache, references) | `WS-2` | An arXiv PDF ingests: sections detected deterministically, slice:brief/body/meta rows persist in extracted_contents on the ONE item (no chunking), references extracted by the cascade, and re-ingest is served from the sha256 cache with zero network (SC#9) |
| `WS-7` | ✅ | Streams: SourceItemIngested/SourcePollCompleted/SourceQueryMatched events + saved queries + digest handoff | `WS-2`, `EXT:AUTOMATION-SUBSTRATE:event bus for SourceItemIngested/SourcePollCompleted + morning-digest template + web_watch source_id consumption (interim JSONL spool until bus lands)` | Engine emits SourceItemIngested per new item + SourcePollCompleted per poll onto the substrate bus (interim spool until it lands); a saved source query matches new items with zero tokens and emits SourceQueryMatched, a subscribed Trigger fires, and the morning-digest template produces ONE knowledge item + one notification through notification_allowed() (SC#10); an injection payload in a scraped page cannot steer a digest run, fenced at the LLM boundary (SC#8) |
| `WS-8` | ✅ | Connector-pack app kind (parse-only, engine-mediated fetch) + source-recipe directory | `WS-1`, `WS-2`, `WS-3` | A connector-pack app installs and registers via KnowledgeTypeHandler; its parse-only script receives an engine-fetched body over stdin (never owns a socket) and emits SourceItem JSON lines that land as items; bundled recipes surface in the create flow; no socket opens outside net.fetch/web/render.py (SC#11 for the pack path) |
| `WS-9` | ✅ | Sources UI in the Knowledge section + as-a-user validation | `WS-2`, `WS-3`, `WS-4`, `WS-5` | Sources UI in the Knowledge section lists all source kinds with health status, drives the paste-URL preview/tune/save create flow, shows the 'no AI' chip on raw sources, and offers listing-page/render-tier remediation affordances; validated as a user driving web/feed/dir sources end-to-end from the frontend |

## Atom scopes

### `WS-1` — Source-provider contract + make the dormant knowledge seam real

**Status:** todo

§1.1 One contract four shapes; §1.3 fixes 1-2-3 (KnowledgeTypeHandler, create_native_provider, search_all non-goal); §Plug-in Map SDK surface; Disposition rows for base.py/registry.py/EntitySeamHandler

**Done when:** KnowledgeSourceProvider/SourceItem/SourcePollResult defined and re-exported via sdk/knowledge.py; EntitySeamHandler no-op for `knowledge` replaced by a real KnowledgeTypeHandler (load_factory->register into knowledge_providers.registry); create_native_provider returns a real factory (no longer None); a KnowledgeProvider-shaped fixture registers and appears in list_provider_info as kind:external; test_manifest_types_match_handlers stays green

### `WS-2` — WatchedSource store + SourceEngine poll loop + SourcesConfig + SOURCE egress policy

**Status:** todo

§1.2 WatchedSource entity + SourceEngine; §3.2 cursors + §3.3 seen-set/unique-index (tables sources/source_cursors/source_seen + item source_id/guid cols); §Plug-in Map SourcesConfig four-point wiring + SOURCE net/policy.py profile; §11 step 1

**Done when:** knowledge.db migration adds sources/source_cursors/source_seen tables + source_id/guid item columns with UNIQUE(source_id,guid); SourceEngine single re-armed asyncio loop enrolls poll-capable providers and polls a fixture source on schedule, writing items via store.create_typed_item(provider,source_id,guid)+ingest_queue.enqueue; SOURCE EgressPolicy profile added via egress_policy_for; SourcesConfig knobs round-trip (SC#12); kill-mid-poll+restart yields no dup/no loss via cursor+seen-set atomicity + recover_pending (SC#4)

### `WS-3` — web-source: five-detector stack, selector configs, escalating fetch, preview+create flow

**Status:** done

§2.1 five detectors; §2.2 declarative selector configs + schema-derived validate_spec; §2.3 outcome-driven escalating fetch (net.fetch tier1 + web/render.py tier2) under one budget; §2.4 preview-then-save; §11 step 2. Reconcile against shipped triggers/web_poll.py extraction/budget rather than re-derive

**Done when:** Pasting a real changelog/blog URL yields a correct zero-LLM item preview via auto-detection and a homepage yields the pick-a-listing-page guidance; a manual selector config rescues a JS-lite failure (SC#1); a JS-heavy source succeeds only after render-tier escalation within max_requests with the escalation recorded, and allow_render:false degrades to a 'needs render tier' health status (SC#2)

**DONE.** `knowledge_providers/web_source.py` — the five §2.1 detectors as five pure functions over
one dependency-free HTML tree (`knowledge_providers/html_dom.py`: stdlib `html.parser` plus a CSS
SUBSET — type/`*`/`#id`/`.class`/`[attr]`/`[attr=v]`/descendant/child/comma — that RAISES on anything
outside it, because a selector quietly meaning something other than what the user wrote is worse than
one refused with a reason). Structural parsing runs on RAW markup for the reason `browse/extraction.py`
already documents: nh3's prose allowlist strips `<script>` (where `json_ld` and `json_state` live) and
drops `class`/`id` (the entire input to `selector_frequency` and to a user's config). Sanitization is
not skipped, it is MOVED to the extracted item's html field, which is where the untrusted bytes go.

**Two ordering decisions carry the detection quality, and both are falsifiable.**
`selector_frequency` runs LAST rather than fourth (§2.1's table order otherwise kept): it is the only
detector that infers structure the page never declared, so ahead of `json_state` a heuristic would
outrank a declaration. And hygiene runs INSIDE the stack loop, per detector — a detector whose every
candidate fails the §2.2 floors found NOTHING and the stack falls through. Deciding on raw candidates
is how a page's sponsored rail becomes "the source found nothing today". A spec's `detectors` list is
a FILTER over the order, never a re-ordering.

**§2.2 is schema-FIRST, which inverts the atom's wording deliberately.** `SPEC_SCHEMA` is a JSON
Schema subset and `validate_spec` is a generic walker over it, rather than hand-written checks a
generator later mirrors into a schema. Deriving a schema from imperative validators produces a second
artifact that can drift; interpreting the schema means there is no second artifact. The walker RAISES
on a schema `type` it does not implement, so a keyword nobody enforces cannot silently accept
everything, and the `detectors` enum IS `DETECTOR_ORDER` — a sixth detector cannot exist without the
schema admitting it. Fail-closed, and re-validated at POLL time (the stakes here are the fetch
TARGET, not just a parse).

**Escalation is decided by outcome, and by a MEASURED discrimination.** Zero items on a page that
rendered plenty of text is the WRONG URL → §2.1's listing-page guidance and no render attempt (a
browser finds the same nothing, more expensively). Zero items on a page carrying script with under
400 chars of visible text is a JS shell → one render attempt if `budget.allow_render` is true, else
the distinct `needs render tier` health status. Without that split both failures would surface as
"found nothing" with opposite remediations. Tier 1, the WordPress sub-request and the render all draw
on ONE `budget.max_requests`.

**Contract additions:** `SourcePollResult` gains `escalations` + `health_status` (a provider that
knows WHY it found nothing says so; the engine no longer flattens it into `degraded`),
`SourcePreview` is the §2.4 dry run, `HEALTH_*` becomes a closed vocabulary in `base.py`, and
`sources.last_escalations` persists the last poll's tiers (overwritten per poll — a rollup column is
not a log). Conditional-GET validator plumbing was LIFTED into `knowledge_providers/conditional_get.py`
and `feed_source` refactored onto it: two copies of a persisted cursor's shape would be one fix away
from disagreeing.

Tests: `tests/test_web_source.py` (46). Falsified 18 ways; three mutations reded NOTHING and each was
fixed rather than noted — per-detector hygiene, the identity guard (shadowed by the title-or-
description floor until the fixture was given real body text and nothing else), and the `sanitize_html`
default (masked by `html_to_markdown`, now isolated with an `sanitize_html: false` counterpart). A
fourth, the implicit `parse_uri` on an `href`, reded nothing because `apply_hygiene` already resolves
the url field for both paths — so the redundant branch was DELETED and resolution now has one
falsifiable point (removing it reds 12 tests).

**Test added during independent verification (the 46th).** Reversing `DETECTOR_ORDER`'s last two
entries — the atom's own DEVIATION from §2.1's table — reded exactly ONE test, and it was the
schema-enum parity assertion on the literal sequence. Nothing proved the *outcome* the deviation
exists for. `test_a_declared_state_blob_outranks_a_frequent_selector` now feeds one page carrying BOTH
a real `__NEXT_DATA__` blob and a thrice-repeated card signature, and asserts `json_state` wins with
the state's items; reversing the order reds it with "a heuristic must not outrank a declaration". A
sequence assertion reds on any reorder, including a harmless one, so it could never have been the
evidence for this claim.

### `WS-4` — feed-source (RSS/Atom/JSON/CSV + HN/GitHub presets) + cross-feed dedupe + raw-mode FeedItemGraph

**Status:** done

§3.1 feed kinds + presets-as-recipes; §3.2 ETag/Last-Modified conditional-GET cursors; §3.3 guid composition + cross-feed canonical-URL dedupe; §6.3 raw enrichment via FeedItemGraph (no LLM nodes) + full variant fencing; §11 step 3

**Done when:** Polling the same feed twice produces zero duplicate items and the same story arriving via HN Algolia AND RSS produces ONE item with both attributions (also_seen_in) (SC#3); a raw source's items reach FTS + vector search with zero LLM calls, asserted structurally that the raw graph contains no LLM nodes (SC#6)

**DONE.** `knowledge_providers/feed_source.py` — RSS 2.0/Atom (one XML parser, root-tag sniffed),
JSON (one declarative field-map parser) and CSV, with `hn_algolia` / `github_trending` / `json_feed`
as entries in `PRESETS` rather than code branches: a preset is a partial spec the source's own spec
overrides key-by-key. §3.2 conditional GET lives in the cursor (`{etag,last_modified}` offered as
`If-None-Match`/`If-Modified-Since`; a 304 returns zero items and KEEPS the validators, so the cheap
steady state stays cheap). Every byte enters through the single `fetch_fn` seam onto `net.fetch` under
the engine-owned `SOURCE` policy — no socket of its own, structurally asserted.

**Identity is two keys, not one** (`knowledge/source_identity.py`): `compose_guid` answers "same item
from THIS source?" (feed guid → canonical URL → `sha256(title+published_at)[:16]`; an un-keyable row
is DROPPED, since the seen-set can only gate what it can name), while `merge_key` answers "same story
from a DIFFERENT source?" and is **canonicalized-URL equality and nothing else** — reusing the store's
own `normalize_url` so the key is byte-identical to what `items.url` holds and the lookup is one
indexed equality. Two guards make "prefer two items over one wrong merge" literal: no URL → no merge
key, and a bare origin → no merge key (it is a site, not a story). Same title+date with different
links stays two items on purpose. The merge itself does BOTH required writes — `mark_source_seen` on
the second source (this path bypasses `create_typed_item`'s folded-in gate, so nothing else would
record the sighting) and an APPEND to the surviving item's `also_seen_in`; a provider's own
`SourceItem.also_seen_in` claims are recorded verbatim in the same string vocabulary.

**§6.3 raw mode is kept by absence, in both halves.** `FeedItemGraph` has one pure-python node and no
model-backed backend, and `graph_for(item_type, enrichment=...)` routes a raw source to it whatever its
item_type (a raw image source would otherwise reach OCR + vision); the runner then does not CALL
insights/entities/intents for a raw item — they report `skipped`, never `done`. `ENRICHMENT_*` is a
closed vocabulary in `knowledge_providers/base.py`, matched explicitly; an item whose `sources` row has
vanished degrades to **raw**, because content whose no-AI promise can no longer be read must not be
handed to a model on the assumption it was fine. The deterministic reach is unchanged: FTS row at
create, local embedding, dedup.

Tests: `tests/test_feed_source.py` (28). Every clause is a count or a structural fact — 3 items polled
twice is `COUNT(*) == 3` (never 6); the merge asserts the single row AND that `also_seen_in` names the
other source; zero-LLM patches the three model stages to RAISE, with a non-raw vacuity counterpart so a
"skip everything" regression cannot read as a pass. Falsified all three ways (dedup off → `assert 6 ==
3`; attribution replaced instead of appended → `got []`; a model call added to the raw path → `ran
['_run_insights']`).

**Measured layering (independent re-falsification):** "zero duplicates" is held by TWO gates, and only
the outer one is load-bearing for the clause. Neutering the `source_seen` novelty gate inside
`create_typed_item` (`if cur.rowcount == 0` → `if False`) reds **nothing** — all 28 stay green, because
the never-pruned `UNIQUE(source_id, guid)` index on `items` then raises `IntegrityError` and the handler
rolls back and returns `None` exactly as the gate would have. That matches the roles the code already
names (the item index is the authoritative persist gate; the seen-set is the FIFO-capped storm guard on
top) — so this is layering, not a hole. It does mean **no test distinguishes the two**, which is
acceptable only because their observable behaviour is identical; breaking identity itself instead
(`compose_guid` → unstable per call) reds 6 tests including `assert 6 == 3`.

### `WS-5` — dir-source: signature-diff observer, debounce, archive-on-delete

**Status:** done

§4 Watched Local Directories (am.5): dir-source spec + save-time validate_file_path/sensitive-path/path-cap; dependency-free mtime+size signature-diff poll (not watchdog); debounced incremental re-index; §11 step 3. Reconcile against shipped triggers/file_poll.py; fs_watch.py stays untouched

**Done when:** Editing three files in a watched dir within the debounce window re-indexes each exactly once (create->new item, modify->re-enqueue existing item); deleting one archives its item with metadata source_deleted_at and never hard-deletes (SC#5); first pass seeds only (no startup ingestion storm)

**DONE.** `knowledge_providers/dir_source.py` — a `(mtime, size)` signature diff per poll against the
baseline in the source cursor; no `watchdog` dependency, and `fs_watch.py` / `triggers/file_watch.py`
are untouched. The debounce window is timed off the file's OWN mtime, so a further save inside the
window restarts it by construction and a settling file's baseline stays uncommitted (no timer state
to lose across a restart): three files edited in one window re-index exactly three times, three saves
of one file exactly once. `SourceItem.change` (`created`/`modified`/`deleted` — a closed vocabulary in
`knowledge_providers/base.py`) is the new contract field the ENGINE dispatches: created → a new item,
modified → the existing row updated and re-enqueued, deleted → `store.archive_source_item` stamping
`file_metadata.source_deleted_at`. There is no delete path to reach — engine and provider are both
structurally asserted to contain no `DELETE FROM items` — and a reported deletion is tombstoned in the
cursor so a restored file revives its archived item instead of being dropped by the seen-set. First
pass seeds only; `validate_spec` (sensitive-path + path-cap, fail-closed) runs on every poll rather
than at save time only; a per-file read error skips that file and advances its baseline instead of
aborting the cycle. Tests: `tests/test_dir_source.py`.

### `WS-6` — Fetch-and-slice ingestion primitive (arXiv/DOI/PDF sniff, section detection, slices, sha256 cache, references)

**Status:** done

§5 Fetch-and-Slice (am.1): knowledge/slicing.py source-sniffing, cascaded section detection (thresholds in one constants block), purpose-cut slices as extracted_contents rows, sha256 source cache under knowledge_files_dir(), deterministic reference extraction; consumed by Document graph + chat file-drop + deep-research template; §11 step 4

**Done when:** An arXiv PDF ingests: sections detected deterministically, slice:brief/body/meta rows persist in extracted_contents on the ONE item (no chunking), references extracted by the cascade, and re-ingest is served from the sha256 cache with zero network (SC#9)

**DONE.** `knowledge/slicing.py` — sniff, cache, detect, cut, extract, in one module with **zero
model calls**. Thresholds live in ONE `# ══ THRESHOLDS ══` block per §5's paperloom drift lesson, and
each one is falsified by monkeypatching it and re-measuring the OUTCOME, so a constant the code does
not actually read cannot pass.

**Layout reading stays in `readers.py`, so the cascade stays pure.** `_read_pdf` answers "what does
this say"; the new `read_pdf_structure` answers "how is it laid out" (per-page text, per-line max
glyph size, bookmark titles) from the SAME guarded `pdfplumber` import — a second PDF library would
be a second place for the PDF path to be present-or-absent. `slicing.py` therefore never imports
pdfplumber and `slice_structure` is a pure function of a plain dataclass, which is why the cascade is
tested with no PDF at all as well as end-to-end on a real generated one. Outline entries carry TITLES
and not destinations: resolving a PDF destination goes through named-destination indirection real
papers get wrong, whereas a title can simply be LOCATED in the extracted text — which is the offset
the caller needs anyway.

**Two ordering decisions carry the detection, and both are now falsifiable.** Tiers 1+2 are unioned
and resolved by `(offset, strategy rank, title)`, so a heading found by the outline AND the font tier
is ONE section attributed to the outline — the document declaring its own structure beats a
typographic inference. Tier 3 (header regex) fires only when the union is EMPTY and proposes only
headings it can NAME: a fallback that guessed at structure would be worse than one section spanning
the document, because a wrong span silently truncates what an enrichment node reads. Body size is the
CHAR-WEIGHTED mode with ties broken to the smaller size; line-counted, a paper with many short
headings and few long body lines elects a heading size as "body" and then finds no headings at all.

**Determinism is asserted across PROCESSES, not just twice in one.** Two in-process runs share a
string-hash seed, so a detector that iterated a set would produce the same order twice and pass. The
suite runs detection in three children under `PYTHONHASHSEED` 0/1/524287 and compares a digest —
the only assertion that observes hash-order dependence. Removing the candidate `.sort()` reds it.

**Slices are ranges, not concatenations, and require detected structure.** Each slice is built from
MERGED char ranges so a body section overlapping a kept page cannot be emitted twice (a duplicated
method section is a doubled token bill for whoever reads the slice). `brief` is clamped into
[`BRIEF_MIN_FRACTION`, `BRIEF_MAX_FRACTION`] — the floor rounds UP, because a truncating floor lands
below the fraction it claims to guarantee. The §5 kept-pages floor is **pre-bibliography** by
construction: the last pages of a paper ARE its references, so a naive last-2 floor would re-import
exactly what `body` strips. A document with no section the cascade could NAME gets no slices at all —
without that gate `meta` ("the first pages") fired on every plain `.txt`, where it is byte-identical
to the content. `full` is the fourth §5 role and is retrievable via `slice_for`, but never a row: it
equals the item's own `content`, so a row would double every paper's storage to repeat a column.

**The reference cascade is ordered by TRUST, and the order is load-bearing.** arXiv id → DOI →
fuzzy title (sliding window ≥ `TITLE_MATCH_RATIO` against titles already keyed from THIS bibliography,
which is the deterministic replacement for asking a model "same paper?") → author+year proximity. A
bibliography entry carrying BOTH an arXiv id and a DOI is in the fixture precisely so the order can
fail: it must key as arXiv, the identifier this codebase can re-fetch. An entry satisfying none of the
four tiers is COUNTED as unkeyed, never given an invented key — a fabricated key is worse than an
admitted gap because KNOWLEDGE-SYNTHESIS's later linking pass would treat it as real. §5 stops at
extraction: a stored record carries `{key, tier, title, year}` and no resolved target.

**The cache is two levels, and that is not redundancy.** Originals are content-addressed
(`sha256-<hex>.pdf`, so two refs to identical bytes share one file) plus a `ref-<hash>.json` pointer
recording which digest a normalized reference resolved to. Content-addressing ALONE cannot serve a
re-ingest — computing the hash needs the bytes we are trying not to fetch — so the pointer is what
makes SC#9's zero-network clause achievable. Both live in a `sources/` subdirectory of the existing
`knowledge_files_dir()`; §5's "no new cache root" holds and the durability inventory's
`workspace/knowledge/files` tree entry already claims it. The cached original's extension comes from
the **content** sniff (`%PDF-`), not the URL or the server's content-type, so a DOI resolving to a
publisher HTML page is not stored as `.pdf` and handed to the PDF reader.

**Wiring: `document_slice` is a graph LEAF in both DocumentGraph and BookmarkGraph.** Feeding
`consolidate` would header-concat three derived views onto the document itself, tripling the text the
insights/embed stages read — asserted. `NodeOutput.pool_rows` is the engine's new multi-row
mechanism: one node is one STEP, but a step whose product is a SET of role-sized views needs rows with
names of its own (`slice:brief`, not `document_slice`), and three graph nodes would each re-run the
same detection — giving one deterministic cascade three chances to disagree with itself.

**Two defects found by the falsification pass, not by review.** (1) `pre_bib_pages[-0:]` is the WHOLE
list, so setting `KEEP_LAST_PAGES = 0` turned the floor maximally ON instead of off — a zero
keep-count now has to be spelled out. (2) `_consolidated_text` (the retroactive-intent LLM path)
concatenates an item's ENTIRE pool, so slice rows would have sent every paper through a model two or
three times; it now skips them via `is_slice_row`, which is why that constant is public.

**A user reaches this today** through the two surfaces that exist: uploading a PDF to the Knowledge
Library, and saving an arXiv/DOI/`.pdf` URL as a bookmark — which previously ran the HTML scraper over
PDF bytes and stored binary noise. WS-9's Sources UI has NOT shipped (`WS-9` is still `todo`), so the
watched-source create flow is not a route to this yet.

Tests: `tests/test_knowledge_slicing.py` (56). No test opens a socket — `fetch_fn` is the primitive's
only byte seam, and the default seam is pinned to `net.fetch` under the `SOURCE` egress policy by
asserting the policy it is handed. The zero-network claim is asserted by giving the SECOND fetch a
seam that RAISES, both at the primitive and through the real `ingest_item` pipeline: "the fetcher was
called once" would pass a cache that fetched and discarded.

### `WS-7` — Streams: SourceItemIngested/SourcePollCompleted/SourceQueryMatched events + saved queries + digest handoff

**Status:** todo

§6.1 provenance + events (bus or interim JSONL spool); §6.2 digest as background one-shot; §6.4 filters-as-streams SavedSourceQuery + SourceQueryMatched; §8 fence_untrusted at LLM/digest boundary; §11 step 5 (events half)

**Done when:** Engine emits SourceItemIngested per new item + SourcePollCompleted per poll onto the substrate bus (interim spool until it lands); a saved source query matches new items with zero tokens and emits SourceQueryMatched, a subscribed Trigger fires, and the morning-digest template produces ONE knowledge item + one notification through notification_allowed() (SC#10); an injection payload in a scraped page cannot steer a digest run, fenced at the LLM boundary (SC#8)

### `WS-8` — Connector-pack app kind (parse-only, engine-mediated fetch) + source-recipe directory

**Status:** done

§7.1 connector packs as knowledge-capability apps with parse-only scripts (fetch_spec + engine net.fetch + stdin body + JSON-lines stdout, sandbox.wrap_argv); §7.2 recipes as data under knowledge/sources/recipes/ + bundled set surfaced in create flow; §Plug-in Map ALLOWED_HOOK_PROVIDERS note; §11 step 5 (ecosystem half)

**Done when:** A connector-pack app installs and registers via KnowledgeTypeHandler; its parse-only script receives an engine-fetched body over stdin (never owns a socket) and emits SourceItem JSON lines that land as items; bundled recipes surface in the create flow; no socket opens outside net.fetch/web/render.py (SC#11 for the pack path)

**DONE.** A connector pack is an ordinary app with a `sources[]` manifest block and a
THREE-LINE `provider.py` that calls `sdk.knowledge.connector_pack_provider(__file__, config)`.
The class that polls is therefore CORE's (`knowledge_providers/connector_pack.py`): a pack
cannot substitute its own fetch even by accident, because it never writes one. The poll
resolves `spec.pack_source` → a declared `PackSourceEntry`, validates the user's `args`,
renders the manifest's `fetchSpec` (`{{args.x}}` percent-encoded into the URL, `{{secret:KEY}}`
resolved from the credential store into HEADERS ONLY), performs one `net.fetch` under the
engine-supplied `SOURCE` policy, and hands the body to the script on stdin.

**The atom's premise about `sandbox.wrap_argv` is measurably wrong, and that finding is the
whole security design.** `wrap_argv` is a *filesystem* control: its Seatbelt profile is
`(allow default)` plus deny-READ rules, its Linux launcher unshares only
`CLONE_NEWUSER`/`CLONE_NEWNS`, and on this project's own dev machine `detect_backend()`
answers `none` (macOS 26 refuses `sandbox_apply` for third-party callers) so it is not applied
at all. Nothing in it denies egress. So the live rail is `pack_parse.py`'s in-process fence,
installed before the script runs. It is THREE mechanisms plus a verification, and which one
covers what is MEASURED: under `python -I` exactly three denied names are pre-imported (`os`,
`os.path`, `posix`). A `sys.meta_path[0]` finder refuses everything absent — `socket`, `ssl`,
`ctypes`, `subprocess`, `urllib.request`, `importlib` — down every route. Eviction of the three
pre-imported names is what stops `import os`, which the finder can never see because the cache
answers first. Neutering the process-spawning callables on those live module objects is what
stops `object.__subclasses__()` → `os._wrap_close.__init__.__globals__` → `os.system`, which
needs no import at all; the child is thrown away after one parse, so wrecking its stdlib is
free. Then the harness verifies the first two survived and reports `fence: tampered`, which
discards the batch. A FOURTH mechanism (wrapping `builtins.__import__`) was built and DELETED:
removing it reded nothing, because with the three names evicted every denied import already
reaches the finder. `DENIED_MODULES` is a **denylist deliberately**:
an allowlist of importable modules was tried first and rejected because the stdlib's own lazy
imports (`csv`→`_csv`, `re`→`re._compiler`) break legitimate parsers unpredictably, and a fence
that fails on correct code gets removed. It is closed *for this property*: every in-process
network path in CPython bottoms out at `_socket`, `_ctypes` or a spawned child.

**Fail closed on output is structural, not a check.** The harness emits a nonce-tagged
terminator only after the script returns; the nonce arrives in a config file the harness
unlinks on read, so a script cannot forge it, and the parent REQUIRES that line. Garbage, a
torn final line, a kill, an over-cap batch and a wrong-shaped row all yield ZERO items plus a
typed `ParseFailure.code` — and the cursor does NOT advance, so a refused batch is re-offered
rather than skipped past. Bounds reuse core's own ceilings (`spawn_shim_argv(PROFILE_TOOL)`,
`build_child_env`, timeout, input cap, output cap) rather than a bare `subprocess.run`.

§7.2 recipes are data: seven bundled JSON files under `knowledge/sources/recipes/` whose
`matchPatterns` carry NAMED capture groups that fill `{{group}}` in the spec, so
`https://github.com/astral-sh/uv` resolves to a concrete releases-Atom spec. Every bundled
recipe is put through its OWNING provider's `validate_spec` in the tests — a recipe the create
flow would refuse on save cannot ship. Surfaced at `GET /api/knowledge/source-recipes` and, in
the UI WS-9 shipped mid-session, as the FIRST thing the create screen asks: paste the link you
already have. A match seeds the form's own fields from the resolved spec (never a hidden spec
riding alongside them), and a recipe naming a provider this install has not registered is
filtered out rather than offered and refused on save.

### `WS-9` — Sources UI in the Knowledge section + as-a-user validation

**Status:** done

§2.4 create flow UI; §6.3 'no AI' chip; health rollups (§12 risk row); §11 step 5 (UI + as-a-user validation). Implementation-owns-product tenet: users can find/create/tune/inspect sources

**Done when:** Sources UI in the Knowledge section lists all source kinds with health status, drives the paste-URL preview/tune/save create flow, shows the 'no AI' chip on raw sources, and offers listing-page/render-tier remediation affordances; validated as a user driving web/feed/dir sources end-to-end from the frontend

**DONE.** This is the atom that turned four finished atoms into a feature. Before it,
`store.create_source` had **zero non-test callers**: WS-2's store, WS-3's five-detector web
kind, WS-4's feeds and WS-5's directory observer all worked and were **entirely unreachable** —
no route, no CLI, no UI through which a user could create a watched source of any kind. So the
scope was the whole loop: four HTTP routes
(`GET`/`POST /api/knowledge/sources`, `POST …/sources/preview`, `PATCH …/sources/{id}`), a
`#/knowledge/sources` destination inside the Knowledge section, and its create flow.

**Every closed vocabulary and every remediation string is read from the provider, not retyped
in TypeScript.** The health statuses come from `base.SOURCE_HEALTH`, the detector list from
`web_source.DETECTOR_ORDER`, the feed recipes from `feed_source.PRESETS`, the folder defaults
from `dir_source.DEFAULT_INCLUDE`, and the two remediations from `LISTING_PAGE_GUIDANCE` /
`RENDER_TIER_GUIDANCE`. A Python rail holds the UI's status map and its kind-form switch to
those vocabularies, so a fifth status added in `base.py` reds CI instead of falling through a
hardcoded default branch — which would happen to the one status that most needed its own
message. Specs are validated by the PROVIDER's own `validate_spec` at both save and edit, so
there is no second copy of its rules in the handler to drift.

**The preview asymmetry is reported honestly rather than faked.** WS-3 deliberately kept
`preview` off the `KnowledgeSourceProvider` ABC — a feed's or a folder's preview IS its poll —
so `SourceKind.previewable` is MEASURED per provider (`callable(getattr(prov, "preview"))`)
and the create page offers a paste-URL dry run for the web kind and says plainly, for the other
two, that their first poll is their preview. A provider without one is refused with that reason
rather than answered with an empty item list that reads like a failure. The preview runs under
`SourceEngine.egress_policy()` — made a `staticmethod` for this — because a preview fetches the
same targets a poll does and two postures for one act is the hole in the `SOURCE` profile.

**The two remediations stay OPPOSITE, which is the whole point of WS-3's discrimination.**
`needs render tier` yields the render-tier guidance plus a real "Allow the render tier" button
(one `budget.allow_render` PATCH); a wrong-URL failure yields the listing-page guidance plus a
URL field. Neither offers the other's control. The render knob is offered only while it is OFF
— allowed-but-failing (a render that raised, or the `js-render` extra missing) is advice, and a
button that re-sets a flag already set would lie about what pressing it does. The match is a
PREFIX test, not equality, because `record_poll` clips the summary to 200 chars and
`LISTING_PAGE_GUIDANCE` is longer than that: equality would have silently never fired for
exactly the longer of the two messages.

**Store surface:** `update_source` with a CLOSED `_EDITABLE_SOURCE_FIELDS` map. `provider` and
`kind` are excluded on purpose (they decide which validator a spec is read against, so changing
them in place would silently reinterpret it), and so are the engine's rollups — a generic setter
would let a client overwrite a poll's verdict. No `delete_source`: the `done_when` needs
remediation, not removal, and `enabled: false` already stops a source polling without orphaning
its `source_seen` rows.

**Four defects found by driving the real thing, each fixed at source.** (1) A WordPress
`title.rendered` is HTML-ESCAPED, so all 20 previewed rows on `github.blog/changelog` read
`Don&#8217;t stop early`; decoded in `_rendered_title`, scoped to the title because `content` is
markup whose escaping is meaningful. (2) An item's `content` IS markup, and the client renders
the snippet as text, so every row showed `<p>…</p>`; converted through the app's one html→text
seam. (3) `sources.health_status` DEFAULTS to `ok`, so a source saved seconds ago read "Healthy
· never polled" in one breath. (4) `record_poll`'s `next_poll_at` was written on the SUCCESS
path only, so the two rows carrying a remediation were exactly the two that could not say a
retry was coming — the same shape WS-3 fixed for `last_escalations`. Plus a header-overflow fix:
at 390px a bare create pill painted over both the back button and the title (squeezed to 8px).

**Validated as a user** on an isolated dev home: created a web page (github.blog/changelog → 20
items via `wordpress_api` in 2 requests), a RAW HN-Algolia feed (20 items, `no AI` chip) and a
watched folder, all three from the frontend; drove both remediations live on real JS-shell and
homepage URLs; pressed both fix buttons; paused a source. Full detail in the plan's
`## Execution log`.

