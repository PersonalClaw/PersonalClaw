# WATCHED-SOURCES — atomic plans

**Source plan:** [`WATCHED-SOURCES`](../plans/WATCHED-SOURCES.md)  
**Code:** `WS`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `WS-1` | ⬜ | Source-provider contract + make the dormant knowledge seam real | — | KnowledgeSourceProvider/SourceItem/SourcePollResult defined and re-exported via sdk/knowledge.py; EntitySeamHandler no-op for `knowledge` replaced by a real KnowledgeTypeHandler (load_factory->register into knowledge_providers.registry); create_native_provider returns a real factory (no longer None); a KnowledgeProvider-shaped fixture registers and appears in list_provider_info as kind:external; test_manifest_types_match_handlers stays green |
| `WS-2` | ⬜ | WatchedSource store + SourceEngine poll loop + SourcesConfig + SOURCE egress policy | `WS-1` | knowledge.db migration adds sources/source_cursors/source_seen tables + source_id/guid item columns with UNIQUE(source_id,guid); SourceEngine single re-armed asyncio loop enrolls poll-capable providers and polls a fixture source on schedule, writing items via store.create_typed_item(provider,source_id,guid)+ingest_queue.enqueue; SOURCE EgressPolicy profile added via egress_policy_for; SourcesConfig knobs round-trip (SC#12); kill-mid-poll+restart yields no dup/no loss via cursor+seen-set atomicity + recover_pending (SC#4) |
| `WS-3` | ⬜ | web-source: five-detector stack, selector configs, escalating fetch, preview+create flow | `WS-2` | Pasting a real changelog/blog URL yields a correct zero-LLM item preview via auto-detection and a homepage yields the pick-a-listing-page guidance; a manual selector config rescues a JS-lite failure (SC#1); a JS-heavy source succeeds only after render-tier escalation within max_requests with the escalation recorded, and allow_render:false degrades to a 'needs render tier' health status (SC#2) |
| `WS-4` | ⬜ | feed-source (RSS/Atom/JSON/CSV + HN/GitHub presets) + cross-feed dedupe + raw-mode FeedItemGraph | `WS-2` | Polling the same feed twice produces zero duplicate items and the same story arriving via HN Algolia AND RSS produces ONE item with both attributions (also_seen_in) (SC#3); a raw source's items reach FTS + vector search with zero LLM calls, asserted structurally that the raw graph contains no LLM nodes (SC#6) |
| `WS-5` | ⬜ | dir-source: signature-diff observer, debounce, archive-on-delete | `WS-2` | Editing three files in a watched dir within the debounce window re-indexes each exactly once (create->new item, modify->re-enqueue existing item); deleting one archives its item with metadata source_deleted_at and never hard-deletes (SC#5); first pass seeds only (no startup ingestion storm) |
| `WS-6` | ⬜ | Fetch-and-slice ingestion primitive (arXiv/DOI/PDF sniff, section detection, slices, sha256 cache, references) | `WS-2` | An arXiv PDF ingests: sections detected deterministically, slice:brief/body/meta rows persist in extracted_contents on the ONE item (no chunking), references extracted by the cascade, and re-ingest is served from the sha256 cache with zero network (SC#9) |
| `WS-7` | ⬜ | Streams: SourceItemIngested/SourcePollCompleted/SourceQueryMatched events + saved queries + digest handoff | `WS-2`, `EXT:AUTOMATION-SUBSTRATE:event bus for SourceItemIngested/SourcePollCompleted + morning-digest template + web_watch source_id consumption (interim JSONL spool until bus lands)` | Engine emits SourceItemIngested per new item + SourcePollCompleted per poll onto the substrate bus (interim spool until it lands); a saved source query matches new items with zero tokens and emits SourceQueryMatched, a subscribed Trigger fires, and the morning-digest template produces ONE knowledge item + one notification through notification_allowed() (SC#10); an injection payload in a scraped page cannot steer a digest run, fenced at the LLM boundary (SC#8) |
| `WS-8` | ⬜ | Connector-pack app kind (parse-only, engine-mediated fetch) + source-recipe directory | `WS-1`, `WS-2`, `WS-3` | A connector-pack app installs and registers via KnowledgeTypeHandler; its parse-only script receives an engine-fetched body over stdin (never owns a socket) and emits SourceItem JSON lines that land as items; bundled recipes surface in the create flow; no socket opens outside net.fetch/web/render.py (SC#11 for the pack path) |
| `WS-9` | ⬜ | Sources UI in the Knowledge section + as-a-user validation | `WS-2`, `WS-3`, `WS-4`, `WS-5` | Sources UI in the Knowledge section lists all source kinds with health status, drives the paste-URL preview/tune/save create flow, shows the 'no AI' chip on raw sources, and offers listing-page/render-tier remediation affordances; validated as a user driving web/feed/dir sources end-to-end from the frontend |

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

**Status:** todo

§2.1 five detectors; §2.2 declarative selector configs + schema-derived validate_spec; §2.3 outcome-driven escalating fetch (net.fetch tier1 + web/render.py tier2) under one budget; §2.4 preview-then-save; §11 step 2. Reconcile against shipped triggers/web_poll.py extraction/budget rather than re-derive

**Done when:** Pasting a real changelog/blog URL yields a correct zero-LLM item preview via auto-detection and a homepage yields the pick-a-listing-page guidance; a manual selector config rescues a JS-lite failure (SC#1); a JS-heavy source succeeds only after render-tier escalation within max_requests with the escalation recorded, and allow_render:false degrades to a 'needs render tier' health status (SC#2)

### `WS-4` — feed-source (RSS/Atom/JSON/CSV + HN/GitHub presets) + cross-feed dedupe + raw-mode FeedItemGraph

**Status:** todo

§3.1 feed kinds + presets-as-recipes; §3.2 ETag/Last-Modified conditional-GET cursors; §3.3 guid composition + cross-feed canonical-URL dedupe; §6.3 raw enrichment via FeedItemGraph (no LLM nodes) + full variant fencing; §11 step 3

**Done when:** Polling the same feed twice produces zero duplicate items and the same story arriving via HN Algolia AND RSS produces ONE item with both attributions (also_seen_in) (SC#3); a raw source's items reach FTS + vector search with zero LLM calls, asserted structurally that the raw graph contains no LLM nodes (SC#6)

### `WS-5` — dir-source: signature-diff observer, debounce, archive-on-delete

**Status:** todo

§4 Watched Local Directories (am.5): dir-source spec + save-time validate_file_path/sensitive-path/path-cap; dependency-free mtime+size signature-diff poll (not watchdog); debounced incremental re-index; §11 step 3. Reconcile against shipped triggers/file_poll.py; fs_watch.py stays untouched

**Done when:** Editing three files in a watched dir within the debounce window re-indexes each exactly once (create->new item, modify->re-enqueue existing item); deleting one archives its item with metadata source_deleted_at and never hard-deletes (SC#5); first pass seeds only (no startup ingestion storm)

### `WS-6` — Fetch-and-slice ingestion primitive (arXiv/DOI/PDF sniff, section detection, slices, sha256 cache, references)

**Status:** todo

§5 Fetch-and-Slice (am.1): knowledge/slicing.py source-sniffing, cascaded section detection (thresholds in one constants block), purpose-cut slices as extracted_contents rows, sha256 source cache under knowledge_files_dir(), deterministic reference extraction; consumed by Document graph + chat file-drop + deep-research template; §11 step 4

**Done when:** An arXiv PDF ingests: sections detected deterministically, slice:brief/body/meta rows persist in extracted_contents on the ONE item (no chunking), references extracted by the cascade, and re-ingest is served from the sha256 cache with zero network (SC#9)

### `WS-7` — Streams: SourceItemIngested/SourcePollCompleted/SourceQueryMatched events + saved queries + digest handoff

**Status:** todo

§6.1 provenance + events (bus or interim JSONL spool); §6.2 digest as background one-shot; §6.4 filters-as-streams SavedSourceQuery + SourceQueryMatched; §8 fence_untrusted at LLM/digest boundary; §11 step 5 (events half)

**Done when:** Engine emits SourceItemIngested per new item + SourcePollCompleted per poll onto the substrate bus (interim spool until it lands); a saved source query matches new items with zero tokens and emits SourceQueryMatched, a subscribed Trigger fires, and the morning-digest template produces ONE knowledge item + one notification through notification_allowed() (SC#10); an injection payload in a scraped page cannot steer a digest run, fenced at the LLM boundary (SC#8)

### `WS-8` — Connector-pack app kind (parse-only, engine-mediated fetch) + source-recipe directory

**Status:** todo

§7.1 connector packs as knowledge-capability apps with parse-only scripts (fetch_spec + engine net.fetch + stdin body + JSON-lines stdout, sandbox.wrap_argv); §7.2 recipes as data under knowledge/sources/recipes/ + bundled set surfaced in create flow; §Plug-in Map ALLOWED_HOOK_PROVIDERS note; §11 step 5 (ecosystem half)

**Done when:** A connector-pack app installs and registers via KnowledgeTypeHandler; its parse-only script receives an engine-fetched body over stdin (never owns a socket) and emits SourceItem JSON lines that land as items; bundled recipes surface in the create flow; no socket opens outside net.fetch/web/render.py (SC#11 for the pack path)

### `WS-9` — Sources UI in the Knowledge section + as-a-user validation

**Status:** todo

§2.4 create flow UI; §6.3 'no AI' chip; health rollups (§12 risk row); §11 step 5 (UI + as-a-user validation). Implementation-owns-product tenet: users can find/create/tune/inspect sources

**Done when:** Sources UI in the Knowledge section lists all source kinds with health status, drives the paste-URL preview/tune/save create flow, shows the 'no AI' chip on raw sources, and offers listing-page/render-tier remediation affordances; validated as a user driving web/feed/dir sources end-to-end from the frontend

