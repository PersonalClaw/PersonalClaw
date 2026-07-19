# Plan: Watched Sources — Any Page, Feed, or Folder Becomes a Structured Item Stream

**Status:** PROPOSED (rev 2 — research-integrated 2026-07-12)  
**Created:** 2026-07-12  
**Depends on:** WORKFLOWS-V2-AUTOMATION-SUBSTRATE step 1 (event bus) for §6; standalone otherwise. AUTOMATION-SUBSTRATE's `web_watch` trigger kind consumes this plan's source engine (see Division of Labor)  
**Scope:** The missing front half of ingestion — manufacture a structured item stream from any web page, feed, or local directory, and make the knowledge-source provider seam real (the seam future Google Drive / Google Photos connectors plug into)

---

## Research Integration (2026-07-12)

One approved recommendation (three parts) folded in (mechanism-level, not appendix):

- **NEW-5** (core) — five-detector auto-source stack + declarative selector configs + preview → §2; outcome-driven escalating fetch under one budget at the `net.fetch` chokepoint → §2.3; guid-keyed novelty gating → §3.3; first-class watched feeds (HN Algolia, GitHub trending, RSS/CSV) with since-cursors + cross-feed dedupe → §3; connector-pack thin fetch scripts as a lightweight app kind → §7.1; shippable source-recipe directory → §7.2; filters-as-streams saved queries as event sources → §6.4; guaranteed raw/no-AI triage mode per source → §6.3
- **NEW-5 am.1** — PDF/paper-aware fetch-and-slice ingestion primitive (arXiv/DOI/URL/PDF sniffing, cascaded section detection, purpose-cut slices, sha256-keyed cache, deterministic reference extraction) → §5
- **NEW-5 am.5** — local filesystem directories as a watched-feed kind (debounced incremental re-index on create/modify/delete) → §4

---

## Overview

Every plan in the Workflows-v2 program assumes events and items *already arrive*. Nothing owns manufacturing a source from an unstructured page, a feed endpoint, or a changing folder. This plan builds that front half — and it is the **architectural centerpiece where the knowledge-provider seam becomes real**:

> **KNOWLEDGE = the user's personal items** — documents, files, photos, notes, and now *watched streams of items the user cares about*. Future knowledge providers are Google Drive, Google Photos, Notion, etc. **MEMORY = the harness's own internal mechanics** (facts/facets/episodic/procedural/lessons) and is untouched by this plan. Watched-source items, digests, and slices land in `knowledge.db` — never in `memory.db`.

One sentence: **a WatchedSource is a knowledge-source provider binding; polling it yields items with stable identity that land in knowledge.db with provenance, fire typed events into the automation substrate, and feed digests, monitors, and retrieval like any other knowledge item.** Watched web pages, watched feeds, watched local directories, and future Drive/Photos connectors are all the *same contract* — apps deliver them, exactly the way model/action/channel providers arrive today.

**Soul guardrail:** personal scale. One user, a handful of sources (tens, not thousands), plain files + one SQLite library under `~/.personalclaw`. The html2rss idea we steal is the *shape* (paste a URL → get an item stream, five deterministic detectors, zero LLM) — not a scraping farm. The FreshRSS lesson we honor: **human-mode is first-class** — every source supports a raw, no-AI pass-through; AI enrichment is opt-in per source ("I like to sift through my news manually... instead of an LLM deciding for me").

### Starting points (verified against code, 2026-07-12 recon)

The design builds on what actually exists — several earlier assumptions were wrong:

- **The KnowledgeProvider seam EXISTS but is dormant.** `knowledge_providers/base.py` defines the async ABC (`name/display_name/list_sources/search/get_item` + optional `ingest/delete_item`); `knowledge_providers/registry.py` exists — but `search_all()` has **no production caller**, the extension type `knowledge` is an `EntitySeamHandler` **intentional no-op** (providers/registry.py:364), and the manifest factory `knowledge_providers.registry:create_native_provider` **returns None** (registry.py:56) — real native-provider construction happens in `dashboard/state.py:1108`. This plan does not design a new seam; it **makes the dormant one real** (§1).
- **Retrieval is NOT federated.** The pipeline runs through `personalclaw.knowledge.*` directly; the uber-pool model is: external providers write into THE ONE `items` table (`provider` column = attribution) via `store.create_typed_item(provider=<name>)` + `ingest_queue.enqueue(item_id)`, and cross-cutting intelligence (insights/entities/intents/embed/retrieval) runs over them identically. **We adopt the uber-pool, not `search_all` federation** — `search_all` stays scaffolding (§1.3).
- **Connectors exist but nothing polls them.** `knowledge/connectors/BaseConnector` (fetch/detect_changes/validate_config/source_type) has exactly one implementation (`WebUrlConnector`, used by the bookmark_scrape node) and `detect_changes` has **zero callers** — there is no sync/poll loop. The SourceEngine (§1.2) is that missing loop.
- **Inbox sources are NOT the seam.** `inbox_providers` are the only entry-point-discovered provider group (`personalclaw.message_source_providers`), the gateway is **hard-wired to `"filesystem"`** (gateway.py:1629), and there is NO app-loader path to `InboxService`. Watched sources therefore do **not** masquerade as inbox message sources — they are knowledge-source providers, and they reach the inbox the same way inbox alerts do: typed events on the bus (`SourceItemIngested`, modeled on the substrate's `InboxItemIngested`) that triggers/digests consume (§6).
- **A headless-browser fetch path already exists in core.** `web/render.py` does its own pre-flight `guard.evaluate()` because Playwright bypasses IP pinning. The escalating fetch chain (§2.3) **reuses it** as the escalation tier — no new scrape container ships in core.
- **`fs_watch.py` is UI-refresh only.** The config-tree watcher (dependency-free 3s poll, mtime+size signature) publishes SSE to the frontend and "is not a trigger kind." Watched local dirs (§4) get their own observer following the same dependency-free poll-signature pattern — `fs_watch` itself is untouched.
- **Knowledge is ONE global library** (the `namespace` column was deliberately dropped), items are **not chunked** (one item = one logical document; chunk model actively removed in `_migrate`), and knowledge has **no config dataclass** — `knowledge.fetch_top_n` is a raw dict read. New config here gets a real typed section wired through all four points (§ Plug-in Map).
- **Fencing is caller responsibility.** `net.fetch`/`web_fetch` output is NOT fenced at the fetch layer; today's `fence_untrusted` call sites are exactly inbox_service ×2, knowledge/insights.py, skills/proposals.py (+ one inline in after_turn_review). The source pipeline becomes a disciplined new call site at the LLM boundary only (§8).
- **The `web_watch` trigger kind is AUTOMATION-SUBSTRATE's** (its §1.2), including the storm-guard framing ("the seen-set IS the storm guard"). **Division of labor:** the substrate owns *when* (trigger kind, gates, budgets-as-gates, ledger rows); this plan owns *what a source is* — the source registry, detectors, cursor/seen-set store, item schema, and provenance. `web_watch.spec.source_id` references a WatchedSource from this plan.

---

## 1. The Source-Provider Contract (the centerpiece)

### 1.1 One contract, four shapes

A **knowledge-source provider** is a `KnowledgeProvider` (existing SDK ABC, re-exported via `sdk/knowledge.py`) that additionally implements the source axis:

```python
class KnowledgeSourceProvider(KnowledgeProvider):        # knowledge_providers/base.py, SDK-exported
    source_kinds: tuple[str, ...]                        # e.g. ("web_page",), ("feed",), ("local_dir",), ("drive",)

    async def poll(self, source: WatchedSource, cursor: dict) -> SourcePollResult:
        """Fetch new items since cursor. MUST route all network through sdk.net.fetch.
        Returns (items: list[SourceItem], cursor: dict, escalations: list[str])."""

    async def preview(self, spec: dict) -> SourcePreview:   # dry extraction for the create flow (§2.4)
    def validate_spec(self, spec: dict) -> list[str]:        # never-throw structural validation
```

`SourceItem` is the normalized unit: `{guid, title, url, summary, content, published_at, author, media, extra}` — `guid` is mandatory (composed per §3.3 when the source doesn't supply one). Duck-typed detection (`is_knowledge_source_provider`, mirroring `local_models/registry.py:is_local_model_provider`) so a plain KnowledgeProvider without `poll` still registers for attribution without joining the poll loop.

The four shapes shipping/enabled by this plan:

| Shape | Provider | Delivered as |
|---|---|---|
| Watched web page | `web-source` (five-detector stack, §2) | core-native, registered like the native knowledge provider |
| Watched feed | `feed-source` (RSS/Atom/JSON-feed/CSV + HN Algolia + GitHub trending presets, §3) | core-native |
| Watched local dir | `dir-source` (§4) | core-native |
| Future connectors | Google Drive, Google Photos, Notion, … | **apps** with `provider: {type: "knowledge", implementation: "provider:create_provider"}` — the seam this plan proves out; OAuth plumbing is their problem, `poll()` + the uber-pool write path is the contract |

### 1.2 WatchedSource entity + SourceEngine

`WatchedSource` rows live in **knowledge.db** (they are user-library configuration, not harness state — migration adds tables `sources`, `source_cursors`, `source_seen`):

```python
@dataclass
class WatchedSource:
    id: str                    # "src-<8hex>"
    name: str
    provider: str              # registered knowledge-source provider name
    kind: str                  # web_page | feed | local_dir | <app-defined>
    spec: dict                 # per-kind (URL + detector toggles / feed URL + preset / dir + globs / recipe ref)
    enrichment: str            # "full" | "raw"  — §6.3, raw is guaranteed no-LLM
    poll_interval_secs: int    # floor 15 min for network kinds (matches the substrate's LLM-clock floor discipline)
    budget: dict               # {max_requests: 10, allow_render: false} — per-poll fetch budget (§2.3)
    item_type: str             # knowledge item_type minted for items (default "bookmark"; feeds may use "note")
    enabled: bool
    created_by: str            # user | app:<name> | agent
    # runtime rollups (engine-written): last_poll_at, last_new_count, health_status, last_error_summary
```

The **SourceEngine** (`knowledge/sources/engine.py`) is the missing poll loop the recon flagged (`detect_changes` has zero callers): a single asyncio loop in the gateway, sleep-until-next-due exactly like `ScheduleService._arm_timer` (single re-armed task, ≤30s cap — **there is no timer heap to extend**, per the substrate recon). Per due source: `provider.poll(source, cursor)` → novelty gate (§3.3) → for each NEW item: `store.create_typed_item(..., provider=<provider>, source_id, guid)` + `ingest_queue.enqueue(item_id)` (the ONE ingestion path — `recover_pending()` gives crash recovery for free) → persist cursor atomically WITH the seen-set delta → emit `SourceItemIngested` per item + `SourcePollCompleted` per poll (§6.1). Once AUTOMATION-SUBSTRATE lands, the engine's clock rebinds onto `web_watch`/system triggers so the substrate's gates/ledger apply; until then it is self-contained (see Disposition).

### 1.3 Making the dormant seam real (three fixes)

1. **Promote `knowledge` from EntitySeamHandler no-op to a real `_TypeHandler`.** A `KnowledgeTypeHandler` in `providers/registry.py` whose `create()` goes through `providers/loader.py:load_factory` and whose `register()` calls `knowledge_providers.registry.register_provider(provider)` (object-keyed, per recon) — plus SourceEngine enrollment when the duck-type check passes. `PROVIDER_TYPES` already contains `knowledge`, so the manifest side is done; the handler swap must keep `test_manifest_types_match_handlers` green (the #47 bug class guard: manifest set == runtime handler set, changed **together**).
2. **Fix the stub factory.** `knowledge_providers.registry:create_native_provider` returns None today; the native provider gains a real factory (still constructed with the store + enqueue closures from `dashboard/state.py` — the handler asks state for them via the existing lazy accessors), so `apps/native/native-knowledge/app.json` stops lying.
3. **Declare `search_all` non-goal.** Cross-provider federated live search stays scaffolding. External items are searchable because they are IN the items table — `HybridRetriever` (FTS5 + entity graph + vector, RRF fusion) needs zero changes. `list_provider_info()` (the one consumed registry API, `dashboard/handlers/knowledge.py:481`) now truthfully lists externals as `kind: "external"`.

---

## 2. Watch This URL — Web-Source Synthesis

### 2.1 Five-detector auto-source stack (html2rss's proven, LLM-free recipe)

`web-source.poll` runs five detectors, all enabled by default, individually toggleable in `spec.detectors`:

| Detector | Mechanism |
|---|---|
| `wordpress_api` | `<link rel="https://api.w.org/">` present → pull posts via WP REST (structured, no scraping) |
| `json_ld` | parse `<script type="application/ld+json">` Schema.org blobs |
| `semantic_html` | HTML5 `<article>/<main>/<section>` item extraction |
| `selector_frequency` | structural frequency analysis — frequently-occurring selectors likely to contain items; tunables `minimum_selector_frequency: 2`, `use_top_selectors: 5` |
| `json_state` | walk SPA state blobs (`<script type="application/json">`, `window.__NEXT_DATA__`, `__NUXT__`, `STATE`) for arrays with title/url pairs |

All deterministic Python, zero tokens, results cacheable. Failure-diagnosis UX carried over verbatim: auto-detection works on *listing* pages (changelogs, category/tag/archive/newsroom pages), not homepages or single posts — when a source yields nothing, the first remediation the UI suggests is **pick a better input URL**, not rewrite the extraction.

### 2.2 Declarative selector configs (the escape hatch)

When auto fails, `spec.extraction` holds an html2rss-shaped declarative config: `items.selector` (CSS) + per-field selectors mapped to item attributes (`title/description/url/author/guid/published_at`), per-field `extractor` (`text|html|href|attribute|static`) + `post_process` chain (`gsub`, `html_to_markdown`, `parse_time`, `parse_uri` resolving relative URLs, `sanitize_html` — **default-on**, `substring`, `template`). Output hygiene defaults: `keep_different_domain: drop` (kills ad/recommendation links), `min_words_title: 3`; a valid item needs title or description. Configs are **data validated by `validate_spec`** — JSON Schema derived from the runtime validators (the html2rss single-source-of-truth pattern) so the FE form and agents validate client-side without drift.

### 2.3 Outcome-driven escalating fetch under one budget — at the existing chokepoints

Fetch strategy `auto` = plain `net.fetch` → (optional) headless render tier. **Escalation is decided by extraction outcome** (did the detectors produce items?), not HTTP status. All attempts in one poll draw on a single `budget.max_requests`; escalations are recorded on the poll record (and, post-substrate, in the Run Ledger).

Reality corrections applied:

- **Every network request routes through `net.fetch`** (`net/client.py:fetch` — evaluate → SEL audit → pinned-IP resolver → per-hop redirect re-evaluation → byte cap), with a `SOURCE` `EgressPolicy` profile derived from `CONNECTOR` (10MB/20s) layered via `egress_policy_for()`. Never hand-rolled aiohttp.
- **The render tier is core's existing `web/render.py`** (which already pre-flights `guard.evaluate` because Playwright bypasses IP pinning) — NOT a new Botasaurus/Browserless-style scrape container. It is off by default per source (`budget.allow_render: false`); JS-heavy sources opt in. A future beefier render provider may arrive as an app, but the chain's tier-2 contract is "core render path or nothing."
- No per-source proxies, UA rotation, or anti-bot navigation modes — personal scale, not scraping infrastructure. A source that needs those is a source to drop.

### 2.4 Preview-then-save create flow

"Watch this URL" (Knowledge section + chat tool): paste URL → `provider.preview(spec)` runs detection once and returns extracted items + which detector won → user sees the item list, tunes detectors/selectors, names the source → save. Zero items → the listing-page guidance (§2.1). Preview is a dry run: no items persisted, no cursor written, budget still enforced.

---

## 3. Watched Feeds

### 3.1 First-class feed kinds

`feed-source` handles endpoints that are *already* structured: RSS/Atom, JSON Feed, CSV-with-header (the githubsignals `export.csv` shape), plus two bundled presets that are just parameterized specs — **HN Algolia** (front-page/query polls via its JSON API) and **GitHub trending**. Presets are source recipes (§7.2), not code branches.

### 3.2 Per-feed since-cursors

Each source's `source_cursors` row holds provider-defined opaque state: `{last_seen_guid, last_published_at, etag, last_modified}` for feeds (conditional GET via ETag/Last-Modified keeps polls nearly free); `{since_ts}` for HN Algolia; `{mtime_signatures}` for dirs (§4). Cursor writes are atomic with the poll's seen-set delta — a crash between item-persist and cursor-persist re-yields items on next poll, and the novelty gate (unique index) makes that harmless (at-least-once poll, exactly-once persist).

### 3.3 Novelty keying + cross-feed dedupe

- **Stable identity per item:** `guid` = feed-supplied guid, else composed deterministically from extracted fields (canonicalized URL, else `sha256(title + published_at)[:16]`) — the html2rss composable-guid discipline. Without this, every monitor/digest re-processes the same items forever.
- **Seen-set:** `source_seen(source_id, guid, first_seen_at)` with a UNIQUE index — the INSERT-or-ignore *is* the novelty gate, and (per the substrate plan's own words) **the seen-set IS the storm guard**: a page that changes every render cannot fire per poll. Capped per source (~5000, FIFO prune).
- **Cross-feed dedupe:** before persisting, a canonical-URL lookup against `items.url` + recent `source_seen` across sources; a duplicate arriving from a second feed records an attribution mention on the existing item (metadata `also_seen_in`) instead of a new item. Deterministic (URL/id matching is code, not model — the paperloom rule).

---

## 4. Watched Local Directories (am.5)

Local dirs are **just another source kind** — closing the freshness gap for frequently-changing local files exactly as the URL detectors do for web sources:

- `dir-source` spec: `{path, include_globs, exclude_globs, recursive, max_files}`. Watch roots are validated at save: must not be under `~/.personalclaw` internals, must pass `validate_file_path` sensitive-path checks (security.py `_SENSITIVE_HOME_DIRS`), path-count capped with a warning on broad globs (the substrate's fs-watch scope-guard discipline).
- **Observer = the existing dependency-free poll pattern**, not watchdog. Recon: `fs_watch.py` is a 3s mtime+size-signature poller and PClaw deliberately avoids the dependency; the amendment's "watchdog observer" is adapted to this reality — `dir-source.poll` diffs the signature map in its cursor (`{path: (mtime, size)}`) at the source's `poll_interval_secs` (default 60s for dirs, not 15 min), detecting create/modify/delete. First pass seeds only (no startup ingestion storm — fs_watch's own rule).
- **Debounced incremental re-index:** created → new knowledge item through the normal typed pipeline (Document/Image/Audio graph by MIME); modified → re-enqueue the EXISTING item (`ingest_queue` re-runs the graph; extracted_contents is a regenerable pool by design); deleted → item marked `is_archived` with metadata `source_deleted_at` — **never hard-deleted** (propose-don't-destroy; the user's library outlives the folder). A per-source debounce window (default 30s) coalesces editor save-storms; the guid for dir items is the relative path (stable across edits).
- Events: modifications emit `SourceItemIngested` with `change: created|updated|removed` so "when anything in ~/notes changes, summarize it into my knowledge base" (the substrate's success criterion #2) rides this rail.

---

## 5. Fetch-and-Slice Ingestion Primitive (am.1)

A shared, deterministic document-shaping layer (`knowledge/slicing.py`, beside the existing `readers.py`/`extract.py`), used by the pipeline's Document graph, the deep-research template, chat file-drops, and paper-ish feed items:

- **Source sniffing:** arXiv ID / DOI / URL / raw-PDF detection with normalization (the paperloom regexes: arXiv `(\d{4}\.\d{4,5})(?:v\d+)?` version-insensitive; DOI `10\.\d{4,}/…`).
- **Cascaded section detection**, three deterministic strategies (first two unioned): PDF outline/TOC entries matching cue regexes → font-size headings (body size = char-weighted mode; heading = >1.1× body, ≤120 chars) → page-header regex fallback. First 3 + last 2 pre-bibliography pages always kept. Thresholds live in ONE constants block (the paperloom doc-vs-code drift lesson).
- **Purpose-cut, role-sized slices:** `brief` (abstract/intro/conclusion, ~10-25%), `body` (method/results, references stripped), `meta` (first pages), `full` (deterministic passes ONLY — the full text never reaches an LLM). Slices persist as `extracted_contents` rows (`node_type: "slice:brief"` etc.) on the ONE item — **not** chunks; the one-item-one-document model is untouched (chunking was actively removed, per recon).
- **sha256-keyed source cache:** originals cached under `knowledge_files_dir()` keyed by content hash (the existing media-originals dir; no new cache root), so re-ingest/re-slice is fetch-free.
- **Deterministic reference extraction:** the citation cascade (arXiv-id → DOI → fuzzy-title ≥0.85 sliding window → author+year proximity) emits reference metadata onto the item — deterministic-replaces-LLM where identifiers exist. Cross-item reference *linking* belongs to KNOWLEDGE-SYNTHESIS (its relate-on-persist step); this plan only extracts and stores the references.

Enrichment discipline inherited by pipeline graphs that consume slices: each LLM node receives exactly the slice its role needs — token control by input shaping, not prompt pleading.

---

## 6. Items, Events, Streams

### 6.1 Provenance + events

Every source-born item carries provenance: `provider` column (existing attribution column) + new `source_id`, `guid` columns (migration; UNIQUE (source_id, guid) doubles as the novelty gate) + metadata `{source_name, fetched_at, detector, escalated, also_seen_in}` — so digests can render "Spotted on Hacker News"-style provenance flags and retrieval answers can cite the watched origin.

The engine emits onto the substrate's event bus (dependency: AUTOMATION-SUBSTRATE step 1): **`SourceItemIngested`** `{source_id, item_id, guid, title, url, change}` per new item and **`SourcePollCompleted`** `{source_id, new_count, escalations, budget_spent}` per poll — the exact pattern of the substrate's `InboxItemIngested`. Item *content* rides the knowledge store, never the event payload beyond the fenced title/summary snippet (payload content never participates in pattern matching — substrate decision 4d). Pre-bus interim: fires spool to the engine's own JSONL and drain when the bus lands (the substrate's spool/cursor rule).

### 6.2 What consumes the stream

- **Digests:** the "Morning web digest" template (substrate §5.3) = clock trigger → foreach over new items since cursor → rule-grammar filter → ONE digest **knowledge item** (item_type `note`, provider `digest`) + inbox notification via `DashboardState.notify` → `notification_allowed()`. Digest synthesis is a `background` use-case one-shot (`one_shot_completion(use_case="background")` — the reasoning axis, never chat/code_tools which returns the NativeAgentRuntime).
- **Monitors:** `web_watch` triggers (substrate) reference `source_id` and fire only on novelty — this plan supplies the seen-set the substrate's storm-guard framing presumes.
- **Knowledge ingestion:** automatic — items ARE knowledge items; `HybridRetriever`, the entity graph, insights, and the composer @-picker see them with zero new wiring. **Knowledge is never auto-injected into prompts** (recon: deliberate) — watched items follow the same rule.

### 6.3 Guaranteed raw/no-AI triage mode per source

`enrichment: "raw"` is a hard contract, honored structurally: the ingest pipeline routes raw-source items through a `FeedItemGraph` whose LLM nodes (consolidate/insights/intents) are absent — not skipped-by-flag — so no config drift can re-enable them. Deterministic stages (FTS index, local embedding via `get_active_embed_fn()`, entity extraction OFF, dedup ON) still run. Raw sources render in the Sources UI with a "no AI" chip; per-source upgrade to `full` re-enqueues existing items on request only.

### 6.4 Filters-as-streams

A saved query over source items (the FreshRSS lesson: a filter over streams becomes a new stream) is addressable as an event source: `SavedSourceQuery {id, name, query}` using the existing retrieval/FTS grammar; the engine evaluates saved queries against each `SourceItemIngested` batch (cheap, deterministic, zero tokens) and emits **`SourceQueryMatched`** `{query_id, item_id}` — a Trigger subscribes with `kind: event, source: SourceQueryMatched, pattern: {query_id}`. Deterministic rule language before LLM: 90% of triage costs zero tokens; the substrate's triage stage remains available for the rest.

---

## 7. Connector Packs + Source Recipes

### 7.1 Connector packs — a lightweight app kind (parse-only scripts)

The Fincept manifest pattern (N thin scripts + a generated manifest), adapted to PClaw's egress discipline: a connector pack is an ordinary app whose manifest declares `provider: {type: "knowledge", implementation: "provider:create_provider", capabilities: ["source"]}` plus a `sources[]` manifest block of thin **parse-only** scripts: `{name, script, fetch_spec, args_schema}`.

**Reality-corrected mechanism:** scripts do NOT perform their own HTTP (that would bypass the `net.fetch` chokepoint). Instead each script declares a `fetch_spec` (URL template + method + headers-from-`{{secret:KEY}}`); the SourceEngine performs the fetch through `net.fetch` and pipes the body to the script over stdin; the script parses and emits `SourceItem` JSON lines on stdout (the argv/JSON-stdout contract, sandboxed via `sandbox.wrap_argv` like `schedule_script.py`). Network stays at the chokepoint; the pack contributes parsing only. Packs needing true API clients (OAuth'd Drive/Photos) graduate to a full `KnowledgeSourceProvider` implementation — `poll()` receives an SDK net handle so even those route through `sdk.net`.

### 7.2 Source-recipe directory

Recipes are **data, not code**: bundled JSON files (a selector config or feed preset + name + input-URL guidance) under `knowledge/sources/recipes/`, surfaced in the create flow ("check if your site is already covered" — the html2rss feed-directory workflow). A user's hand-tuned selector config that works is exactly the LEARNING-FLYWHEEL "repeated ad-hoc artifact → proposable template" shape — the flywheel plan owns proposing recipe persistence; this plan owns the recipe format + bundled set (release-notes pages, changelogs, HN, GitHub trending, a few blogs).

---

## 8. Security Posture

- **Egress:** every source fetch through `net.fetch` with the `SOURCE` profile (operator `security.egress` layered via `egress_policy_for`); render tier pre-flights `guard.evaluate` (web/render.py pattern); connector-pack fetches engine-mediated (§7.1). SEL (`sel.py`) audits source creation, escalations, and budget breaches like egress/skill installs today.
- **Fencing:** scraped/feed content is untrusted. `sanitize_html` default-on at extraction; `fence_untrusted(text, source=f"source:{source_id}")` wraps content at every **LLM boundary** (pipeline enrichment nodes, digest synthesis, triage) — becoming a disciplined new call site alongside inbox_service/insights/proposals. Raw mode has no LLM boundary, hence nothing to fence. Event payloads carry only fenced snippets (§6.1).
- **Write scope:** a source can only ever create/update knowledge items it minted (its own `source_id`) — it cannot touch memory, config, tasks, or other providers' items. No action execution lives in this plan; anything action-shaped is a substrate Trigger downstream of the events, governed by the substrate's capability allowlists.
- **Injection screen:** once the substrate's decision-4a InputGuard regex screen exists, source-item snippets pass through it before any unattended LLM consumption — same boundary, shared code.

---

## 9. Disposition Table

| Surface | Verdict | Detail |
|---|---|---|
| `knowledge_providers/base.py` ABC | **EXTENDED** | Gains `KnowledgeSourceProvider` (poll/preview/validate_spec) as a subclass contract; base ABC unchanged for plain providers |
| `knowledge_providers/registry.py` | **MADE REAL** | Gains its first consumer beyond `list_provider_info`; `create_native_provider` None-stub fixed; `search_all` explicitly declared scaffolding (uber-pool wins) |
| `EntitySeamHandler` for `knowledge` | **REPLACED** by `KnowledgeTypeHandler` | The intentional no-op becomes a real handler (load_factory → register → SourceEngine enrollment); manifest `PROVIDER_TYPES` already lists `knowledge` so only the handler side moves — `test_manifest_types_match_handlers` guards the pairing |
| `knowledge/connectors/` (`BaseConnector`, `WebUrlConnector`) | **ABSORBED** | `WebUrlConnector` becomes the fetch leg of `web-source`; the caller-less `detect_changes` seam is superseded by `poll(cursor)`; the bookmark_scrape node keeps working through the same code |
| `knowledge/ingest_queue.py` | **KEPT — THE path** | All source items enqueue through it; `recover_pending()` is the crash-recovery story; no second ingestion path |
| `knowledge/pipeline/graphs.py` | **EXTENDED** | New `FeedItemGraph` (raw + full variants); Document graph consumes §5 slices |
| `fs_watch.py` | **KEPT, untouched** | Stays UI-refresh SSE; `dir-source` is a separate observer at source-level roots (config-tree vs user-content roots never mix) |
| `inbox_providers/` + `InboxService` | **KEPT, untouched** | Watched sources are NOT inbox message sources (no app-loader path exists and we don't force one); the inbox consumes source events via triggers/digests only |
| `web/fetch.py` + `web/render.py` | **REUSED** | fetch = tier 1 via `net.fetch`; render = tier 2 escalation with its existing pre-flight |
| AUTOMATION-SUBSTRATE `web_watch` kind | **CONSUMES this plan** | `spec.source_id` references a WatchedSource; the trigger owns firing/gates/ledger, this plan owns extraction/cursor/seen-set. Pre-substrate the SourceEngine self-schedules; post-substrate its clock rebinds onto system triggers (`created_by: system:sources`) |
| KNOWLEDGE-SYNTHESIS monitoring templates | **FED by this plan** | Its accumulate legs stop being hypothetical: sources[] = WatchedSource refs; its item-identity contract = §3.3 guids |

---

## 10. What We Deliberately Do NOT Build

- **No federated live search** across providers (`search_all` stays dead) — the uber-pool items table is the model.
- **No scraping infrastructure** — no proxies, UA rotation, anti-bot bypass, or scrape containers; personal scale, `net.fetch` + core render only.
- **No OAuth plumbing in core** — Drive/Photos connectors are apps; they bring their own auth (credential store via `save_credential`), the seam gives them registration + uber-pool + events.
- **No LLM in the extraction path** — five detectors + selector configs are deterministic; LLM enters only at opt-in enrichment/digest, fenced.
- **No hard-deletes from dir sync** — removed files archive their items.
- **No per-source AI ranking by default** — raw mode is guaranteed; AI assist opt-in per source (the anti-LLM-curation user is a first-class persona).
- **No new inbox source path** and no second notification path — events + `DashboardState.notify` → `notification_allowed()`.
- **No watchdog dependency** — the poll-signature pattern PClaw already uses.

---

## 11. Migration / Build Order (each step ships independently)

1. **Source contract + store + seam repair:** `KnowledgeSourceProvider`, `WatchedSource` tables (knowledge.db migration incl. `source_id`/`guid` item columns + unique index), SourceEngine loop, `KnowledgeTypeHandler` replacing the no-op, `create_native_provider` fix, `SourcesConfig` (four wiring points).
2. **`web-source`:** five detectors, selector configs + schema-derived validation, escalating fetch (`SOURCE` policy, render tier), preview API + create flow, hygiene defaults.
3. **`feed-source` + `dir-source`:** RSS/Atom/JSON/CSV + HN/GitHub presets, ETag/Last-Modified cursors, cross-feed dedupe, dir signature-diff observer + debounce + archive-on-delete, raw-mode `FeedItemGraph`.
4. **Fetch-and-slice primitive:** sniffing, cascaded section detection, slices-as-extracted_contents, sha256 cache, reference extraction; Document graph + chat file-drop + deep-research template consume it.
5. **Streams + ecosystem:** `SourceItemIngested`/`SourcePollCompleted`/`SourceQueryMatched` on the bus (or interim spool), saved source queries, connector-pack app kind (parse-only contract), recipe directory + bundled recipes, morning-digest template handoff to the substrate, Sources UI in the Knowledge section.

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Provider-seam promotion breaks app install/update | `PROVIDER_TYPES` already contains `knowledge`; only the handler changes, guarded by `test_manifest_types_match_handlers`; native app manifest gains a real factory in the same change |
| Poll storms / battery drain | Single re-armed timer (the schedule.py mechanism), 15-min network floor, per-poll request budget, conditional GET, seen-set novelty gate, dir-glob path caps |
| Item flood into the library | Per-source seen-set caps, cross-feed dedupe, `max_files` on dirs, digest-not-item default for high-volume feeds (digest is ONE item), archive views |
| Scraped content steering unattended LLMs | sanitize_html at extraction + `fence_untrusted` at every LLM boundary + raw mode has no boundary + substrate InputGuard screen when it lands + events carry snippets only |
| SSRF via user-pasted URLs / feed redirects | Everything through `net.fetch` (classify_host, pinned IPs, per-hop redirect re-eval); render tier pre-flights evaluate; connector scripts never own sockets |
| Duplicate items on crash | At-least-once poll + UNIQUE(source_id, guid) insert-or-ignore = exactly-once persist; cursor written atomically with seen-set delta |
| Selector configs rot as sites change | Health rollups on the source row (last_new_count trend → `degraded`), listing-page remediation guidance, recipe directory updates, preview re-run affordance |
| Substrate timing (bus not landed yet) | Engine self-schedules + spools events; rebind to triggers/bus is step 5 and additive |
| Editor save-storms on watched dirs | 30s debounce window + signature diff (mtime+size) + re-enqueue-existing-item semantics (idempotent pipeline re-run) |

---

## Provider & Config Plug-in Map

Where each new piece plugs into the pluggable-provider architecture (recon: providers.md) — nothing invents a parallel extension path:

- **Knowledge-source providers ride the `knowledge` extension type.** Apps declare `provider: {type: "knowledge", implementation: "module:create_provider", capabilities: ["source"]}`; the new `KnowledgeTypeHandler` `create()`s via `providers/loader.py:load_factory` (namespaced import, `ProviderSettings.load` config from `~/.personalclaw/apps/{name}/data/config.json`) and `register()`s into `knowledge_providers.registry` — exactly the task/tool/search/action handler pattern. Enable/disable lifecycle, availability greying, and the `/api/providers/{name}/(schema|config|enable|disable)` HTTP surface come for free. Multi-instance (two Drive accounts) uses the existing `multiInstance` + `ExtensionInstance` machinery.
- **Core-native sources (web/feed/dir) register like the native knowledge provider** — built in `dashboard/state.py` beside `knowledge_provider()` and registered at startup; they are not apps (they're the reference implementations of the contract).
- **No new action providers ship in this plan.** Downstream actions are the substrate's business. IF a connector pack ever ships one (e.g. a `post-back` action), it follows the `apps/webhook-action` precedent — `type: "action"` app **AND its name added to `ALLOWED_HOOK_PROVIDERS` (validation.py:555)** or hook/trigger create rejects it — stated here so no pack author trips on it.
- **Config = a new typed `SourcesConfig` section** (knowledge currently has NO config dataclass — raw dict reads; this is its first), wired through the FOUR points (recon: persistence-security gotcha #1): (a) dataclass fields with `_meta(label, help)` (schema reachability tests enforce), (b) `AppConfig.load()` explicit field-by-field mapping (omission = silently dropped), (c) `to_dict()` **including the new top-level section** (new sections are the non-free case), (d) `_EDITABLE_CONFIG` PATCH allowlist + FE for the runtime-editable knobs: default poll interval, network floor, per-poll request budget default, render-tier global enable, dir debounce window.
- **Egress:** `SOURCE` policy profile added beside `STRICT/CONNECTOR/WEBHOOK` in `net/policy.py`, layered with operator `security.egress` via `egress_policy_for()`; all fetches through `net/client.py:fetch`; SEL audit events for source lifecycle + escalations.
- **SDK surface:** `sdk/knowledge.py` re-exports `KnowledgeSourceProvider`, `SourceItem`, `SourcePollResult`; `sdk/net` + `sdk/security.fence_untrusted` are already exported — a connector app imports nothing outside `sdk.*` (Property 11 discipline: no provider SDK imports in core registries; factories lazy-import).
- **Background LLM (digest/enrichment)** resolves via `one_shot_completion(use_case="background")` / the reasoning axis over `active_models.json` — never chat/code_tools (NativeAgentRuntime). Embedding via the shared `get_active_embed_fn()` binding (the ONE memory/knowledge shared seam).
- **Memory vs knowledge routing (user directive):** everything this plan writes is KNOWLEDGE (`knowledge.db` items with provenance). No memory writes anywhere; learning about source usefulness (which digests get acted on) is LEARNING-FLYWHEEL's memory-side concern, fed by the substrate's ledger outcome fields — not by this plan writing memory.

---

## Implementation Effort

**~5 sessions**, mapping 1:1 onto §11:

- Session 1: contract + store + SourceEngine + KnowledgeTypeHandler seam repair + SourcesConfig (step 1)
- Session 2: web-source — detectors, selector configs, escalating fetch, preview + create flow (step 2)
- Session 3: feed-source + dir-source + novelty/dedupe + raw mode (step 3)
- Session 4: fetch-and-slice primitive + consumers (step 4)
- Session 5: events/streams + saved queries + connector packs + recipes + digest handoff + Sources UI + as-a-user validation (step 5)

Trigger-side work (`web_watch` wiring, morning-digest template install, triage defaults) is counted in AUTOMATION-SUBSTRATE's sessions, not here.

## Success Criteria

1. Pasting a real changelog/blog URL into "Watch this URL" yields a correct item preview via auto-detection with zero LLM calls; a homepage yields the pick-a-listing-page guidance, and a manual selector config rescues one JS-lite failure case.
2. A JS-heavy source succeeds only after the render-tier escalation, within one `max_requests` budget, with the escalation recorded — and with `allow_render: false` it degrades to a clear "needs render tier" health status instead of silently failing.
3. Polling the same feed twice produces zero duplicate items (guid gate); the same story arriving via HN Algolia AND an RSS feed produces ONE item with both attributions.
4. Kill the gateway mid-poll and restart: no duplicate items, no lost items (cursor+seen-set atomicity + `recover_pending`), and the next poll resumes from the cursor.
5. Editing three files in a watched dir within the debounce window re-indexes each exactly once; deleting one archives (never deletes) its item with `source_deleted_at`.
6. A `raw` source's items reach FTS + vector search with ZERO LLM calls end-to-end (asserted structurally: the raw graph contains no LLM nodes), and render with the "no AI" chip.
7. A Google-Drive-shaped test app (fixture provider implementing `KnowledgeSourceProvider`) installs via the App Store, enables through `KnowledgeTypeHandler`, writes items with `provider: "test-drive"` into knowledge.db, appears in `list_provider_info` as external, and its items surface in `HybridRetriever` results and the composer @-picker with no retrieval changes.
8. A prompt-injection payload in a scraped page cannot steer a digest run (fenced at the LLM boundary, verified adversarially) and cannot reach any surface unfenced.
9. An arXiv PDF ingests through fetch-and-slice: sections detected deterministically, `slice:brief/body/meta` rows in extracted_contents, references extracted by the cascade, re-ingest served from the sha256 cache with zero network.
10. A saved source query ("intitle:release !beta") matches new items with zero tokens and emits `SourceQueryMatched`; a Trigger subscribed to it fires; the morning-digest template produces ONE knowledge item + one notification through `notification_allowed()`.
11. Every fetch in a 24h soak appears in SEL/egress audit with the `SOURCE` policy; no socket is opened outside `net.fetch`/`web/render.py` (asserted by test instrumentation).
12. `SourcesConfig` knobs round-trip: PATCH via `_EDITABLE_CONFIG`, survive `AppConfig.load()`, appear in `to_dict()`, and render in Settings (the four-point wiring verified by the schema reachability tests).
