# WATCHED-SOURCES

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/WS.md`](../atomic/WS.md) as 9 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Watched Sources — Any Page, Feed, or Folder Becomes a Structured Item Stream

**Status:** PROPOSED (rev 2 — research-integrated 2026-07-12). Not started — verified 2026-08-04: no
`knowledge/sources/` package, no `sources` table, no feed layer, no `dir-source`, and §1.3's three
named fixes are all undone (`knowledge_providers/registry.py::create_native_provider` still returns
None; `detect_changes` is still caller-less).
🔴 **PREMISE DRIFT — re-scope before starting.** This plan's Disposition table says
AUTOMATION-SUBSTRATE's `web_watch` kind "CONSUMES this plan", but the consumer arrived FIRST:
`triggers/web_poll.py` (S121) and `triggers/file_poll.py` (S93) shipped their own extraction, novelty
seen-set, daily request budget and `net.fetch` routing, driven from the gateway's poll loops. So §2/§3/§4
now overlap shipped code rather than feeding it — reconcile against what exists rather than
re-deriving.

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

## Execution log

- [WS-1] DONE: **§1.1 contract + §1.3 fix 1 (KnowledgeTypeHandler).** Defined the poll contract in
  `knowledge_providers/base.py` — `SourceItem` (guid/title/content/url/published_at/also_seen_in),
  `SourcePollResult` (items/cursor/error), and `KnowledgeSourceProvider(KnowledgeProvider)` adding
  `poll(source_id, cursor) -> SourcePollResult` + `poll_interval_seconds` — and re-exported all three
  via `sdk/knowledge.py`. Replaced the `knowledge` `EntitySeamHandler` no-op with a real
  `KnowledgeTypeHandler` in `providers/registry.py` that registers an external provider into
  `knowledge_providers.registry` (consumed by `list_provider_info` + `search_all`); moved `knowledge`
  from `SEAM_TYPES` to `REAL_REGISTRY_TYPES` in `test_entity_seam_handlers.py`. A `KnowledgeProvider`
  fixture registers through the handler and appears in `list_provider_info` as `kind:external`; the
  `#47` `PROVIDER_TYPES`↔handlers parity test stays green (`knowledge` was already in `PROVIDER_TYPES`).
  **DEVIATION (recorded, owner licence):** the atom's clause "`create_native_provider` returns a real
  factory (no longer None)" was RE-SCOPED. The registry-level factory stays `None` **by design**: the
  native provider needs the `DashboardState` store/queue (unavailable at manifest-factory time) and
  self-registers via `state.knowledge_provider()` — the single source of truth. `_enable_one` skips
  `register()` for a `None` instance, so enabling `native-knowledge` through the now-real handler does
  NOT double-register it (the exact "second source of truth" trap the seam docstring warns against).
  Making it return a real instance would require store-injection into the manifest path or a second
  registration — neither is a clean break, and the `done_when`'s observable goal (an EXTERNAL provider
  appears as `kind:external`) is fully met by the real handler. `§1.3 fix 2/3` (search_all non-goal /
  further create_native_provider work) belong to later WS atoms with a real consumer. Fixed a latent
  bug found in review: the handler's `deregister` used a `getattr(instance, "name", ext.name)` default
  that eagerly dereferenced `ext` — now computes the fallback lazily. **Gates:** `make lint` clean
  (692 files); `test_entity_seam_handlers.py` (10) + `test_provider_registry.py` + `test_app_manifest.py`
  (incl. #47 parity) + `test_knowledge_provider*` = 80 passed. Contract-only; no user-facing surface
  yet (WS-2's SourceEngine is the consumer) → no CHANGELOG entry.

- [WS-3] DONE: **§2 web-source — five detectors, selector configs, escalating fetch, preview.**
  `knowledge_providers/web_source.py` runs the five §2.1 detectors as pure functions over ONE
  dependency-free HTML tree (`knowledge_providers/html_dom.py`: stdlib `html.parser` + a CSS SUBSET —
  type/`*`/`#id`/`.class`/`[attr]`/`[attr=v]`/descendant/child/comma — that RAISES on anything outside
  it). No new runtime dependency: BeautifulSoup/lxml on every install (desktop bundle included) to read
  a handful of changelog pages is not the trade this project makes, and `html.parser` is lenient about
  the malformed markup real pages ship. **Structural parsing runs on RAW markup**, for the reason
  `browse/extraction.py` documents for its own stdlib parse: nh3's prose allowlist strips `<script>`
  (where `json_ld` and `json_state` live) and drops `class`/`id` (the entire input to
  `selector_frequency` and to a user's config). Sanitization is not skipped, it is MOVED to the
  extracted item's html field, which is where the untrusted bytes actually go.
  **Reconciled against `triggers/web_poll.py` rather than re-derived, and the reconciliation had a
  measured answer.** Its `extract_items` is a NOVELTY-KEY extractor (a list of opaque strings) not an
  item extractor — there is no title/url/content to lift — so what was reused is the *decision shape*
  and the disciplines around it, verbatim in intent: escalate on extraction OUTCOME not HTTP status;
  charge the expensive tier WIN-OR-LOSE so a failing render cannot retry forever; and make the
  escalation VISIBLE on the record whether it fired, was refused, failed, or found the tier absent.
  Its per-day `WatchState` budget was NOT lifted: WS-3's budget is per POLL (`budget.max_requests`), a
  different axis, and the daily one is `SourcesConfig.daily_request_budget` — see the open item below.
  **Two ordering decisions carry the whole detection quality, and both are falsifiable.** (a)
  `selector_frequency` runs LAST rather than §2.1's fourth — DEVIATION, recorded: it is the only
  detector that infers structure the page never declared, so ahead of `json_state` a heuristic would
  outrank a declaration. §2.1's table is otherwise kept in order. (b) Hygiene runs INSIDE the stack
  loop, per detector: a detector whose every candidate fails the §2.2 floors found NOTHING and the
  stack falls through. Deciding a winner on RAW candidates is exactly how a page's sponsored rail
  becomes "the source found nothing today" — measured on a fixture where `semantic_html` produces two
  off-domain promo articles and `selector_frequency` three real cards. A spec's `detectors` list is a
  FILTER over the order, never a re-ordering.
  **DEVIATION (recorded): §2.2's schema is derived the other way round — schema FIRST.** The atom says
  "JSON Schema derived from the runtime validators"; shipped is `SPEC_SCHEMA` as the single artifact
  with `validate_spec` a generic walker over it. Reason: a schema *generated from* imperative
  validators is a second artifact that can drift, while a schema *interpreted by* the validator has no
  second artifact to drift from — the same single-source-of-truth property, held structurally instead
  of by a generator nobody runs. The walker RAISES on a schema `type` it does not implement (so a
  keyword nobody enforces cannot silently accept everything — asserted), and the `detectors` enum IS
  `DETECTOR_ORDER`, so a sixth detector cannot exist without the schema admitting it. Fail-closed, and
  re-validated at POLL time: the stakes here are the fetch TARGET, and a mutated row is refused before
  the fetch seam is reached (asserted).
  **The escalation split is a MEASURED discrimination, not a guess about the URL.** Zero items on a
  page that rendered plenty of text is the WRONG URL → §2.1's listing-page guidance and NO render
  attempt (a browser finds the same nothing, more expensively). Zero items on a page carrying script
  with under 400 chars of visible text (`looks_like_js_shell`) is a JS shell → one render attempt when
  `budget.allow_render` is true, else the distinct `needs render tier` status. Without that split both
  failures surface as "found nothing" with OPPOSITE remediations, and the user gets sent the wrong way.
  Tier 1, the WordPress sub-request and the render all draw on ONE `budget.max_requests` — proved by a
  `max_requests: 1` WordPress page where the REST sub-request cannot happen and the stack falls through
  to `semantic_html` on exactly one request.
  **DEVIATION (recorded): `preview`/`validate_spec` are NOT added to the `KnowledgeSourceProvider`
  ABC** as §1.1's sketch shows. The shipped ABC (WS-1) has neither, and `feed_source`/`dir_source`
  already carry `validate_spec(spec) -> tuple[bool, str]` as a convention — not §1.1's
  `list[str]`. A feed's or a directory's "preview" IS its poll; only the web kind has a
  detect-then-tune loop worth previewing, so an abstract `preview` would be a stub on two shipped
  providers — a dead surface on both. `preview` therefore lives on `WebSourceProvider` and takes a
  SPEC (not a `source_id`), because the create flow runs before a source exists.
  **Contract + store additions:** `SourcePollResult` gains `escalations` and `health_status` (a
  provider that knows WHY it found nothing declares it; the engine no longer flattens `needs render
  tier` into `degraded`), `SourcePreview` is the §2.4 dry run, `HEALTH_OK/DEGRADED/ERROR/NEEDS_RENDER`
  becomes a closed vocabulary in `base.py` that the engine now uses instead of string literals, and
  `sources.last_escalations` persists the last poll's tiers — overwritten per poll on BOTH the success
  and failure paths, because a rollup column is not a log and an escalation visible only on failure
  makes the expensive-but-working case the invisible one. Conditional-GET validator plumbing was
  LIFTED into `knowledge_providers/conditional_get.py` and `feed_source` refactored onto it: two copies
  of a persisted cursor's shape would be one fix away from disagreeing, which is a data divergence
  rather than a code smell.
  **Falsification: 18 mutations, and THREE reded nothing — each was fixed, not noted.** (1) Hygiene at
  the end of the stack instead of per detector (`if items:` → `if rows:`) — all 44 green, so a new
  fall-through fixture was added. (2) The identity guard (`if not guid: continue` → `guid = "x"`) — all
  green, because the existing fixture's row was ALSO empty of title and description and was already
  dropped by that earlier floor; the fixture was rebuilt with real body text and nothing else, plus a
  with-a-date vacuity counterpart. (3) The `sanitize_html` default-ON — all green, because that test's
  chain also ran `html_to_markdown`, which strips the script anyway; an isolating test now keeps the
  value as HTML and asserts `sanitize_html: false` lets `alert(1)` through. A FOURTH, the implicit
  `parse_uri` on an `href`, reded nothing for a different reason worth recording: `apply_hygiene`
  already resolves the url field against the page for BOTH paths, so the branch was genuinely
  redundant — it was DELETED rather than tested, and resolution now has ONE falsifiable point
  (removing it reds 12 tests). Mutations that reded correctly: detector order (4), guidance suppressed
  (2), `min_words_title` floor (1), off-domain drop (1), `allow_render` ignored (1), render escalation
  off-budget (1), escalations not persisted (5), health status flattened (2), poll-time revalidation
  removed (1), a 304 treated as a detection failure (1), manual config falling back to the stack (1),
  the JS-shell floor always-true (1), preview budget unbounded (2).
  **OPEN (not WS-3's, stated so it is not lost):** `SourcesConfig.daily_request_budget` is still
  enforced by nothing — its own help text says "enforced by the fetching providers", and no provider
  reads it (the provider is not handed `cfg`). WS-3's budget is the per-poll `max_requests` the atom
  names; the rolling-day axis wants either engine plumbing or a cursor-carried day bucket, and belongs
  with whichever atom takes the config surface.
  **Gates:** `make lint` clean (black 1677 files, isort, flake8, mypy 867 source files);
  `tests/test_web_source.py` = **46 passed**; `test_source_engine.py` + `test_feed_source.py` +
  `test_dir_source.py` = **62 passed** (the `conditional_get` refactor is behaviour-identical);
  repo-wide rails `test_inert_surface_baseline.py` + `test_portability.py` +
  `test_durability_inventory.py` + `test_config_baseline.py` + `test_resilience_degraded_lint.py` +
  `test_roadmap_dag_derived.py` = **116 passed**; full suite **20278 passed, 30 skipped, 12 xfailed, 0
  failed**. No config field added, so `test_config_baseline` needed no regeneration; no new public
  enum/config/trigger-kind/SDK export, so the inert-surface count is unchanged. No `web/` change, so
  no FE gate.
  **No CHANGELOG entry, deliberately, for the same reason WS-4 recorded:** `store.create_source` still
  has zero non-test callers, so there is no HTTP route, CLI command or UI through which a user can
  create a watched source of ANY kind. `WS-9` (Sources UI + as-a-user validation) owns that surface and
  the user-facing entry lands with it; announcing a paste-URL flow now would promise something nobody
  can reach.
  **Independent verification (parent, not the implementer).** Re-ran the whole gate rather than
  accepting the report: `make lint` clean (mypy 867) and the full suite **20278 passed / 0 failed**, both
  reproduced. Four fresh mutations on live lines: `_allow_render` → `return True` reds
  `test_allow_render_false_degrades_to_needs_render_tier_and_never_renders`; suppressing the guidance on
  the no-escalation path reds one test; suppressing it on the wrong-URL path reds the homepage clause
  with `assert 'LISTING pages' in ''` **plus** the manual-config test. **The fourth found a real gap:**
  reversing `DETECTOR_ORDER`'s last two entries — this atom's own deviation from §2.1 — reded exactly
  ONE test, the schema-enum parity assertion on the literal sequence, so nothing proved the *precedence
  outcome* the deviation exists for. Added `test_a_declared_state_blob_outranks_a_frequent_selector`
  (one page with BOTH a `__NEXT_DATA__` blob and a thrice-repeated card signature → `json_state` wins,
  with the state's items), which reds on that reorder with "a heuristic must not outrank a declaration".
  Also verified in code rather than on report: `regen_dag_derived.py` genuinely prints no `regressed:`
  line (the string does not exist in the tool — `test_regenerating_the_committed_file_is_a_no_op` is the
  real check, and the parent's brief was wrong to ask for it); the shipped `KnowledgeSourceProvider` ABC
  really has no `preview`/`validate_spec` and `poll` really is `poll(source_id, cursor="")`, so the
  implementer's correction of §1.1 and of the brief stands; `sanitize_html` reuses
  `web.extract.sanitize_html` and the render tier reuses `web.render.render_url`, with **no dependency
  file touched and no third-party HTML/CSS library added** — `html_dom.py` is a genuinely new capability
  (a DOM tree + CSS-subset selector engine) that `web/extract.py` never had, not a duplicate of it; and
  the `sources.last_escalations` migration is a guarded, idempotent `ALTER` with the `CREATE` carrying
  the column for a fresh database.

- [WS-4] DONE: **§3 feed kinds + §3.3 cross-source identity + §6.3 raw mode.**
  `knowledge_providers/feed_source.py` parses RSS 2.0/Atom (one XML parser, root-tag sniffed), JSON (one
  declarative field-map) and CSV, with `hn_algolia`/`github_trending`/`json_feed` as entries in `PRESETS`
  rather than code branches — a preset is a partial spec the source's own spec overrides key-by-key.
  Conditional GET lives in the cursor (`{etag,last_modified}` → `If-None-Match`/`If-Modified-Since`; a 304
  returns zero items and KEEPS the validators). Every byte enters through the single `fetch_fn` seam onto
  `net.fetch` under the engine-owned `SOURCE` policy — no socket of its own, asserted structurally.
  Identity is TWO keys (`knowledge/source_identity.py`): `compose_guid` = "same item from THIS source?"
  (feed guid → canonical URL → `sha256(title+published_at)[:16]`; un-keyable rows are DROPPED, since the
  seen-set can only gate what it can name), and `merge_key` = "same story from a DIFFERENT source?",
  deliberately canonicalized-URL equality and nothing else, reusing the store's own `normalize_url` so it
  is byte-identical to `items.url` and the lookup is one indexed equality. Two guards make "prefer two
  items over one wrong merge" literal: no URL → no merge key, and a bare origin → no merge key (a site is
  not a story). The merge does BOTH writes — `mark_source_seen` on the second source (this path bypasses
  `create_typed_item`'s folded-in gate, so nothing else would record the sighting) and an APPEND to the
  surviving item's `also_seen_in`. §6.3 raw mode is kept by ABSENCE in both halves: `FeedItemGraph` has
  one pure-python node and no model-backed backend, and the runner does not CALL insights/entities/intents
  for a raw item (they report `skipped`, never `done`); an item whose `sources` row has vanished degrades
  to raw, because content whose no-AI promise can no longer be read must not be handed to a model.
  **Corrected the briefing recon:** `graph_for` had no `enrichment=` parameter and `FeedItemGraph` did not
  exist anywhere — both were this atom's to create, not to extend; `SourceItem.also_seen_in` did exist but
  was INERT (no reader anywhere), so this atom is what makes it mean something.
  **Defect found and fixed in passing:** `int(spec.get("max_items") or MAX)` swallowed an explicit `0` as
  "unset" — a user asking for zero items got the maximum.
  **Measured layering (independent re-falsification, recorded because it is counter-intuitive):**
  neutering the `source_seen` novelty gate reds NOTHING — the never-pruned `UNIQUE(source_id, guid)` index
  raises and the handler rolls back exactly as the gate would. The code already names these roles (index =
  authoritative persist gate, seen-set = storm guard), so it is layering rather than a hole, but no test
  distinguishes them. Breaking identity itself (`compose_guid` unstable per call) reds 6 tests including
  `assert 6 == 3`; replacing instead of appending the attribution reds the third-feed test.
  **Gates:** `make lint` clean (864 files, mypy); full suite **20233 passed, 30 skipped, 12 xfailed, 0
  failed**; `tests/test_feed_source.py` = 28. No `web/` change, so no FE gate.
  **No CHANGELOG entry, deliberately — and the reason is worth stating plainly:** `store.create_source`
  has **zero non-test callers**. There is no HTTP route, no CLI command and no UI through which a user can
  create a watched source of ANY kind, so `WS-2`, `WS-4` and `WS-5` are all complete and the feature as a
  whole is still store-only. `WS-9` (Sources UI in the Knowledge section + as-a-user validation) owns that
  surface; `WS-3` owns the web-source preview+create flow. Announcing feeds now would promise something
  nobody can reach, so the user-facing entry lands with `WS-9`.
