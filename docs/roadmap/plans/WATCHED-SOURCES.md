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

- [WS-9] DONE: **§2.4 create-flow UI + §6.3 'no AI' chip + §12 health rollups + §11 step 5's UI
  half.** This is the atom that made the feature exist. `store.create_source` had **zero non-test
  callers**, so WS-2's store, WS-3's five-detector web kind, WS-4's feeds and WS-5's directory
  observer were all complete and **entirely unreachable** — no route, no CLI, no UI. Shipped: four
  routes in `dashboard/handlers/knowledge.py` (`GET`/`POST /api/knowledge/sources`,
  `POST …/sources/preview`, `PATCH …/sources/{id}`), a URL-addressable `#/knowledge/sources`
  (+ `/sources/new`) destination in the Knowledge section reached from a `Sources` header control on
  the library page, and `store.update_source`.
  **Every closed vocabulary and remediation string is READ FROM THE PROVIDER, not retyped in
  TypeScript.** Health statuses come from `base.SOURCE_HEALTH`, the tune step's detector list from
  `web_source.DETECTOR_ORDER`, the feed recipes from `feed_source.PRESETS`, the folder defaults from
  `dir_source.DEFAULT_INCLUDE`, and the two remediations from `LISTING_PAGE_GUIDANCE` /
  `RENDER_TIER_GUIDANCE`. The list response ships the vocabularies (`health_statuses`,
  `raw_enrichment`) rather than the UI carrying copies, and a Python rail asserts the TS
  `HEALTH_META` keys equal `SOURCE_HEALTH` exactly, that the UI's `RAW_ENRICHMENT`/
  `HEALTH_NEEDS_RENDER` literals equal the Python ones, and that the create page has a form branch
  for every `form` the catalog can emit — each with a vacuity assertion, because a matcher that
  stopped matching would make all of them true. Specs are validated by the provider's OWN
  `validate_spec` on create AND on edit, so save-time validation is byte-identical to the poll-time
  re-validation WS-3/WS-5 already do; there is no second copy of the rules in the handler.
  **The preview asymmetry is reported honestly rather than faked, which was the main design call.**
  WS-3 deliberately kept `preview` off the `KnowledgeSourceProvider` ABC (a feed's or a folder's
  preview IS its poll, so an abstract `preview` would be a stub on two of three providers), so
  `SourceKind.previewable` is **measured** — `callable(getattr(prov, "preview", None))` — and the
  create page renders a paste-URL dry run for the web kind and, for the other two, says plainly
  that their first poll is their preview. A provider without one is refused with that reason instead
  of answered with an empty item list that reads like a failure. §2.2's `SPEC_SCHEMA` is deliberately
  NOT shipped to the client: the authoritative validator is the provider's, and a TS re-walker over
  the schema would be exactly the second artifact WS-3's schema-first inversion exists to avoid.
  **DEVIATION (recorded): `SourceEngine.egress_policy` became a `staticmethod`.** A preview is a
  real fetch at the same targets a poll uses and it happens in an HTTP handler with no engine
  instance in reach; two call sites resolving the `SOURCE` profile independently would be two egress
  postures for one act. One definition, reachable from both. Asserted: the policy the preview hands
  `fetch_fn` is `name == "source"` with the engine's own `max_bytes`.
  **The two remediations stay OPPOSITE — the anti-collapse property is the atom's real risk.**
  `needs render tier` → the render-tier guidance plus a live "Allow the render tier" button (one
  `budget.allow_render` PATCH); a wrong-URL failure → the listing-page guidance plus a URL field
  prefilled with the current URL. Neither surface offers the other's control, at create time or on a
  saved row. The knob is offered only while it is OFF: allowed-but-failing (a render that raised, or
  the `js-render` extra absent) is advice, and a button that re-sets a set flag lies about what
  pressing it does — that path instead surfaces the poll's own reason as `detail`, and only when it
  says something the guidance does not. The match is a PREFIX test rather than equality because
  `record_poll` clips `last_error_summary` to 200 chars and `LISTING_PAGE_GUIDANCE` is ~270:
  equality would have silently never fired for exactly the longer of the two messages, and the test
  asserts `len(guidance) > 200` so that cannot regress unnoticed.
  **`update_source` is a CLOSED allowlist, and there is deliberately no `delete_source`.**
  `_EDITABLE_SOURCE_FIELDS` excludes `provider`/`kind` (they decide which validator a spec is read
  against, so changing them in place would silently reinterpret a validated spec — that is a new
  source, not an edit) and every engine rollup (a generic setter would let a client overwrite a
  poll's verdict with what it believed the verdict to be). An unknown field RAISES rather than being
  ignored, because an edit that silently does nothing is the shape where a UI reports success and
  the row never moved. No delete: the `done_when` needs remediation, not removal, `enabled: false`
  already stops a source polling, and a hard delete would orphan `source_seen` rows and strand
  `items.source_id` — WS-4 already documents that an item whose source row vanished degrades to raw,
  so deletion is a state-shape decision that wants its own atom, not a convenience here.
  **FOUR defects found by driving the real thing, all fixed at source rather than papered over.**
  (1) WordPress `title.rendered` is HTML-**escaped**, so every one of 20 previewed rows on
  `github.blog/changelog` read `Don&#8217;t stop early`; decoded in a new `_rendered_title`, scoped
  to the title only — `content` is markup where the same escaping is meaningful (an escaped
  `&lt;script&gt;` in a post body is *shown code*) and decoding it would hand live markup to
  `sanitize_html`. (2) An item's `content` IS markup and the client renders the snippet as TEXT, so
  every preview row showed `<p>…</p>` with `&#8217;` for apostrophes; converted through the app's
  ONE html→text seam (`connectors.base.html_to_text`, which `web_source`'s own `html_to_markdown`
  post-process already calls) so a snippet and an ingested item read the same way. (3)
  `sources.health_status` DEFAULTS to `ok`, so a source saved seconds ago rendered "Healthy · never
  polled" in one breath — the row now reports "Not polled yet" until a poll has written a verdict;
  fixed in the UI, not by inventing a fifth status outside `SOURCE_HEALTH`. (4) `record_poll`'s
  `next_poll_at` was written on the SUCCESS path ONLY, so the two rows carrying a remediation were
  exactly the two that could not say a retry was coming — the same shape WS-3 fixed for
  `last_escalations`, now recorded on all four exits via `_next_poll_at` (a display rollup only;
  scheduling still measures from `last_poll_at`). Plus a header-overflow fix: at 390px a bare create
  pill laid out 44→206px and painted over BOTH the back button (44→84) and the page title, squeezed
  to **8px** of width — routed through `HeaderActions`/`HeaderControl`, the app's one responsive
  cluster, after which the title measures 67px and pairwise overlap among header controls is **0**.
  Two smaller ones: the feed Format select defaulted to `csv` (the alphabetical first of a
  VOCABULARY, not a preference order) so a user pasting an RSS URL got the CSV parser preselected;
  and the cadence select's options excluded the dir provider's own 300s default, so a `<select>`
  whose value matched no option DISPLAYED "Every 15 min" while holding 5 minutes.
  **Falsification: 9 mutations, and ONE reded nothing — the test was fixed, not noted.** The empty
  one is worth recording: removing the snippet's html→text conversion entirely left all 38 green,
  because that test used a `semantic_html` fixture and DOM extraction yields already-decoded plain
  text — the assertion held for a reason unrelated to the code under test. **The five detectors are
  not interchangeable fixtures; only WordPress produces markup in `content`.** Re-routed through a
  WordPress REST fixture, the same mutation now reds with
  `AssertionError: the client renders this as text, so markup would show up raw`. Mutations that
  reded correctly: the 'no AI' chip made unconditional →
  `a full-enrichment source must not claim no AI: expected <span …> to be null`; the listing-page
  verdict swapped for the render-tier one → `assert 'render_tier' == 'listing_page'` **and**
  `assert 'render_tier' != 'render_tier'` (the anti-collapse test); the editable-field allowlist
  made silent → `Failed: DID NOT RAISE KeyError`; `previewable` declared uniformly true →
  `assert {'watched-dir': True} != {'watched-dir': False}`; save-time `validate_spec` removed →
  `assert 201 == 400` on both the bad-URL and the sensitive-path cases; the WordPress title left
  escaped → `assert 'Don&#8217;t …' == 'Don’t …'`; `next_poll_at` dropped from the soft-failure path
  → `a failing source that will be retried must say so`; the never-polled chip reverted to the
  stored default → `Unable to find an element with the text: Not polled yet`; the listing-page URL
  field removed → `Unable to find an accessible element with the role "textbox" and name
  /Listing-page URL for Product changelog/`. Also fixed a flake I authored: the next-check assertion
  used an exact 20-minute offset, which `relFuture`'s floor renders as 19m or 20m depending on
  whether a millisecond elapsed (1 red in 3 runs) — moved mid-bucket, then 5 consecutive green runs.
  **Gates:** `make lint` clean (black 1684 files, isort, flake8, mypy **869** source files);
  `tests/test_knowledge_sources_api.py` = **38 passed**; `test_web_source.py` = **47**;
  `test_source_engine.py` + `test_feed_source.py` + `test_dir_source.py` + the API suite = **146**;
  repo-wide rails `test_api_manifest_drift.py` + `test_inert_surface_baseline.py` +
  `test_portability.py` + `test_durability_inventory.py` + `test_config_baseline.py` +
  `test_resilience_degraded_lint.py` + `test_roadmap_dag_derived.py` = **124 passed**. Web gate (this
  atom changes `web/`, so it is mandatory): `npm run typecheck` clean; the **FULL** `npm test` =
  **2625 passed / 264 files / 0 failed**; `npm run build` exit 0. The full suite caught two real
  things a path-scoped run would have skipped — `accentChip` (an alpha primary tint under primary
  ink measures 3.64–4.20:1 in light, below AA; adopted the shared `design/accent` pair, and note the
  rule scans COMMENTS too, so the first fix tripped it by quoting the utility names) and
  `toggleDisabledReason`'s pinned census, which the test's own words ask to be a deliberate PR line:
  14 → 15, my site added to the enumerated in-flight class (it gates on `busy`, so a
  `disabledReason` there would be a regression) and the `disabledReason` count held at 4. No config
  field, no new enum/trigger-kind/SDK export, so `test_config_baseline` needed no regeneration and
  the inert-surface counts are unchanged. `src/personalclaw/reference/{index,routes}.md` regenerated
  for the four new routes (docstring first lines reworded so the generated summaries read as
  sentences), and `docs/design/consistency-audit.json` moved 467 → 470 files scanned with
  `driftHits` unchanged at 7.
  **As-a-user validation — what was actually driven, on `PERSONALCLAW_HOME=$PWD/.dev-home`, port
  10099 (`:10000` was squatted by another instance).** Onboarded, then from `#/knowledge` pressed
  the new `Sources` control → `#/knowledge/sources`. Created all three kinds **from the frontend**:
  a **web page** (`https://github.blog/changelog/` — preview returned **20 items via
  `wordpress_api` in 2 requests**, then saved; the engine polled it within 30s and wrote 20 items
  that appear in `GET /api/knowledge/items` with `provider: watched-page` and the right
  `source_id`), a **RAW feed** (`hn_algolia` preset, enrichment Raw — **20 items**, and the row
  carries the `no AI` chip while the enriched page row does not), and a **watched folder**
  (`.dev-home/watched-notes` with two markdown files; a first attempt at `~/.ssh` was refused by
  `dir_source`'s own guard with "path is a sensitive location and cannot be watched", surfaced as a
  `role="alert"` field error). Drove **both remediations on real URLs**: `https://example.com/`
  polled to `degraded` + the listing-page strip + a URL field, and `https://excalidraw.com/` polled
  to `needs render tier` + the render-tier strip + the knob — the two rendered side by side with
  different chips, different messages and different controls. Pressed **"Allow the render tier"**
  (button vanished, advice remained, toast announced); pressed **"Point it here"** with a new URL
  (spec updated, toast confirmed); **paused** a source (chip appeared, "next in" dropped, switch
  relabelled to Resume). Also drove the create-time preview against a homepage (listing-page
  guidance) and a JS shell ("Allow the render tier and retry"). **A11y measured, not assumed:**
  on the populated list **16 of 16** controls focusable with **0** unnamed and **0** nested
  interactive, the one `aria-disabled` control still reachable with its reason; at **390px** the
  create page had 0 overflowing controls, 0 sub-24px targets, 0 header overlap and no horizontal
  scroll. Every status change goes through `notify()` into the `Toaster`'s live region. Console was
  clean apart from the browser's own log of an intentional 400 (the sensitive-path refusal) and a
  burst of `ERR_CONNECTION_REFUSED` from reloading during a gateway restart; `gateway.log` showed
  only the expected `no model provider resolves for use case 'background'` from ingestion
  enrichment, which is this dev home having no model bound, not this surface.
  **OBSERVED, not changed (WS-3's decision, stated so it is not lost):** after both fixes landed
  the two rows re-polled to `ok` with zero new items and no escalations — and for the JS shell that
  is a **304**. The cursor still carries the ETag from the first poll, so `web_source` returns
  "not modified" (no items, no error) and the engine records `HEALTH_OK`, which OVERWRITES the
  `needs render tier` verdict even though the page still needs the tier. That is WS-3's
  conditional-GET contract working as specified (its own falsification list includes "a 304 treated
  as a detection failure" as a mutation that SHOULD red), and the health column is a per-poll rollup
  by design, so nothing here was changed. But it means a JS-heavy source can oscillate between
  `needs render tier` and `ok` depending on whether the server answers 304, and whichever atom next
  owns health should decide deliberately whether a 304 may clear a diagnosis it did not re-test.
  The other row's `0 new` is correct and is WS-4 working: repointed at the GitHub changelog it saw
  URLs an existing source had already ingested, so the sightings became attributions on those items
  rather than duplicates.
  **What I could NOT verify:** the render tier actually SUCCEEDING (this environment has no
  `personalclaw[js-render]`, so the allowed-and-installed path is unit-tested only); dir
  create/modify/delete propagation past the first seeding pass (WS-5 seeds only on pass one and its
  own suite covers the debounce); and light theme, which was not swept.
  **CHANGELOG: yes.** WS-3 and WS-4 both withheld an entry because nobody could reach the feature.
  This atom is what makes watched sources reachable, so the entry covers the whole capability —
  web/feed/folder sources, the paste-URL preview, the raw no-AI mode and the health/remediation
  surface — not just the UI.
- [WS-6] DONE: **§5 fetch-and-slice — sniff, sha256 cache, cascaded detection, purpose-cut slices,
  deterministic references.** `knowledge/slicing.py` is the whole primitive and contains **zero model
  calls**; thresholds live in ONE `# ══ THRESHOLDS ══` block per §5's paperloom drift lesson, and each
  is falsified by monkeypatching it and re-measuring the OUTCOME, so a constant the code does not
  actually read cannot pass.
  **Layout reading stays in `readers.py` so the cascade stays pure.** `_read_pdf` answers "what does
  this say"; the new `read_pdf_structure` answers "how is it laid out" (per-page text, per-line max
  glyph size, bookmark titles) from the SAME guarded `pdfplumber` import — the brief said not to add a
  second PDF path and that is the right call for a stronger reason than duplication: a second guarded
  import is a second place for the PDF path to be present-or-absent, and callers would then have to
  know which one degraded. Consequence: `slicing.py` never imports pdfplumber and `slice_structure` is
  a pure function of a plain dataclass, so the cascade is tested with no PDF at all as well as
  end-to-end on a real generated one. Outline entries carry TITLES, not destinations — resolving a PDF
  destination goes through named-destination indirection real papers get wrong, whereas a title can
  simply be LOCATED in the extracted text, which is the offset the caller needs anyway.
  **Two ordering decisions carry the detection, and both are now falsifiable.** Tiers 1+2 are unioned
  and resolved by `(offset, strategy rank, title)`, so a heading found by the outline AND the font tier
  is ONE section attributed to the outline — the document declaring its own structure beats a
  typographic inference. Tier 3 (header regex) fires only when the union is EMPTY and proposes only
  headings it can NAME: a fallback that guessed at structure would be worse than one section spanning
  the whole document, because a wrong span silently truncates what an enrichment node reads. Body size
  is the CHAR-WEIGHTED mode with ties broken to the smaller size — line-counted, a paper with many
  short headings and few long body lines elects a heading size as "body" and then finds no headings at
  all (asserted directly).
  **Determinism is asserted across PROCESSES, not just twice in one.** Two in-process runs share a
  string-hash seed, so a detector that iterated a set of titles would produce the same order twice and
  pass an in-process double-run test. The suite runs detection in three children under
  `PYTHONHASHSEED` 0/1/524287 and compares a digest, which is the only assertion that can observe
  hash-order dependence.
  **Slices are merged RANGES, not concatenations, and they require detected structure.** Range-merging
  is why a body section overlapping a kept page cannot be emitted twice (a duplicated method section is
  a doubled token bill for whoever reads the slice). `brief` is clamped into `[BRIEF_MIN_FRACTION,
  BRIEF_MAX_FRACTION]` and the floor rounds UP — a truncating floor lands below the fraction it claims
  to guarantee, which is not a floor. §5's kept-pages floor is **pre-bibliography** by construction:
  the last pages of a paper ARE its references, so a naive last-2 floor would re-import exactly what
  `body` strips. **DEVIATION (recorded):** a document with no section the cascade could NAME gets NO
  slices at all. §5 defines `meta` as "the first pages", which fired on every plain `.txt` in the
  library — where it is byte-identical to the content — so the gate is what keeps the primitive about
  papers. And `full` is §5's fourth role, retrievable via `slice_for` but never a persisted row: it
  equals the item's own `content` column, so a row would double every paper's storage to repeat
  something already stored.
  **The reference cascade is ordered by TRUST and the order is load-bearing.** arXiv id → DOI → fuzzy
  title (sliding window ≥ `TITLE_MATCH_RATIO` against titles already keyed from THIS bibliography — the
  deterministic replacement for asking a model "same paper?") → author+year proximity. A fixture entry
  carries BOTH an arXiv id and a DOI precisely so the order can fail; it must key as arXiv, the
  identifier this codebase can re-fetch via `sniff_source`. An entry satisfying none of the four tiers
  is COUNTED as unkeyed and never given an invented key: a fabricated key is worse than an admitted gap
  because KNOWLEDGE-SYNTHESIS's later linking pass would treat it as real. §5 stops at extraction — a
  stored record is `{key, tier, title, year}` with no resolved target.
  **The cache is two levels, and that is not redundancy.** Originals are content-addressed
  (`sha256-<hex>.pdf`, so two refs to identical bytes share one file) plus a `ref-<hash>.json` pointer
  recording which digest a normalized reference resolved to. Content-addressing ALONE cannot serve a
  re-ingest — computing the hash needs the bytes we are trying not to fetch — so the pointer is what
  makes the zero-network clause achievable at all. Both live in a `sources/` subdirectory of the
  existing `knowledge_files_dir()`: §5's "no new cache root" holds and the durability inventory's
  `workspace/knowledge/files` tree entry already claims it (no inventory change needed, verified). The
  cached original's extension comes from the **content** sniff (`%PDF-`), not the URL or the server's
  content-type, so a DOI resolving to a publisher HTML page is not stored as `.pdf` and handed to the
  PDF reader.
  **Wiring: `document_slice` is a graph LEAF in both DocumentGraph and BookmarkGraph.** Feeding
  `consolidate` would header-concat three derived views onto the document itself, tripling the text the
  insights/embed stages read (asserted). `NodeOutput.pool_rows` is the engine's new multi-row
  mechanism: one node is one STEP, but a step whose product is a SET of role-sized views needs rows
  named by the node (`slice:brief`, not `document_slice`), and three graph nodes would each re-run the
  same detection — giving one deterministic cascade three chances to disagree with itself.
  **Two defects found by the falsification pass, not by review.** (1) `pre_bib_pages[-0:]` is the WHOLE
  list, so `KEEP_LAST_PAGES = 0` turned the floor maximally ON instead of off; a zero keep-count now has
  to be spelled out. (2) `_consolidated_text` — the retroactive-intent LLM path — concatenates an item's
  ENTIRE extracted-content pool, so slice rows would have sent every paper through a model two or three
  times. It now skips them via `is_slice_row`, which is why that helper is public. Falsifying that fix
  reds with `assert 3 == 1` on the document's own text.
  **Falsification (8 mutations on live lines; two rounds, because the first found weak tests).** Cache
  always misses → `AssertionError: network reached for https://arxiv.org/pdf/2103.00020 — the sha256
  cache did not serve it` plus `assert 'unreachable' == 'done'` through the pipeline. `PERSISTED_SLICES
  = ()` → `assert ('slice:brief' in ['bookmark_scrape'])`. DOI checked before arXiv → `assert 'doi' ==
  'arxiv'`. Kept-pages floor removed → the abstract vanishes from `body`. Bibliography never located →
  `AssertionError: references must be stripped from body`. Slicer wired into `consolidate` → the extra
  `Edge(from_node='document_slice', to_node='consolidate')`. Pool consumer stops excluding slices →
  `assert 3 == 1`.
  **TWO MUTATIONS REDED NOTHING on the first round, and both were real gaps, not noise.** (a) Removing
  the candidate `.sort()` left every test green: both fixtures had only ONE tier contributing, in
  document order already, so the sort was a no-op on them and nothing exercised the property it exists
  for. Added `test_the_unioned_tiers_are_ordered_by_offset_not_by_tier` — an outline naming two sections
  with a font-only heading BETWEEN them, so concatenation order ≠ document order — which now reds with
  `At index 1 diff: 'Conclusion' != '2 Method'`. (b) The pipeline re-ingest test passed with the cache
  disabled, and the reason is a behavioural fact worth recording: `BookmarkScrapeNode`'s pre-existing
  "user content wins" short-circuit fires on the text the FIRST ingest stored, so a regenerate never
  reaches the fetch at all and the cache was not what made it network-free. Re-scoped into two honest
  tests — `test_saving_the_same_paper_twice_opens_no_socket_the_second_time` (a SECOND item, same URL,
  empty content → genuinely served from the cache, and it reds under the mutation) and
  `test_re_ingesting_one_fetched_paper_reuses_its_stored_text_and_re_slices_it`, which documents that
  the regenerate path re-slices FLATTENED text (no font tier; the header tier carries detection) and
  still rebuilds — replaces, not appends — its slices.
  **Corrections to the briefing recon, stated plainly because it can be wrong.** (1) `pdfplumber` is
  NOT optional here — it is a declared CORE dependency (`pyproject.toml`) and
  `test_knowledge.py::test_pdf_reader_dependency_present` asserts it must be present, so the brief's
  "your tests must not depend on it being installed" is inverted for this repo; `reportlab` is core too
  (owner ruling, `test_documents.py`), which is what allows real generated-PDF fixtures instead of
  stubs. The design still keeps the cascade pdfplumber-free, for the independent reason above. (2) The
  brief said "WS-9 shipped the Sources UI"; `WS-9` is still `todo` and WS-4's own log records that
  `store.create_source` has zero non-test callers. The CHANGELOG entry is warranted anyway, via
  surfaces that DO exist: uploading a PDF to the Knowledge Library, and saving an arXiv/DOI/`.pdf` URL
  as a bookmark — which previously ran the HTML scraper over PDF bytes and stored the noise.
  **Gates:** `make lint` clean (black/isort/flake8, mypy 872 files); `tests/test_knowledge_slicing.py`
  = 56; the new-surface rails (`test_inert_surface_baseline`, `test_portability`,
  `test_durability_inventory`, `test_config_baseline`, `test_resilience_degraded_lint`,
  `test_agent_reference`, `test_roadmap_dag_derived`) = 123 passed with the inert baseline NOT
  regenerated (no new config key, enum member, trigger kind, editable-config entry or SDK export — the
  slice vocabulary is module string constants for exactly that reason); affected suites
  (`test_knowledge_pipeline`, `test_knowledge`, `test_documents`, `test_knowledge_typed_items`,
  `test_feed_source`, `test_dir_source`, `test_web_source`, `test_source_engine`) = 430 passed. Full
  suite **20473 passed, 30 skipped, 12 xfailed, 0 failed**. No config field added, so no
  `test_config_baseline` regeneration. No `web/` change → no FE gate.

- [WS-8] DONE: **§7.1 connector-pack app kind + §7.2 source-recipe directory.**
  A connector pack is an ordinary app with a `sources[]` manifest block (`apps/manifest.py`:
  `PackSourceEntry` + `AppManifest.sources`) and a **three-line** `provider.py` that calls
  `sdk.knowledge.connector_pack_provider(__file__, config)`. So the class that polls is CORE's
  (`knowledge_providers/connector_pack.py`) and a pack cannot substitute its own fetch even by
  accident — it never writes one. The poll resolves `spec.pack_source` → a declared entry, validates
  the user's `args`, renders the manifest's `fetchSpec` (`{{args.x}}` percent-encoded into the URL —
  a repo name with a slash must not become an extra path segment; `{{secret:KEY}}` from the
  credential store into **headers only**), performs ONE `net.fetch` under the engine-supplied
  `SOURCE` policy, then hands the body to the script on stdin and reads `SourceItem` JSON lines back.
  Conditional GET rides the shared `conditional_get` cursor, so the pack kind is the third consumer
  of that one implementation rather than a fourth copy of a cursor shape.
  **The briefing's central premise was measurably WRONG, and correcting it is this atom's substance.**
  §7.1 says the script is "sandboxed via `sandbox.wrap_argv` like `schedule_script.py`". Measured:
  `wrap_argv` is a **filesystem** control and no egress control at all — its Seatbelt profile is
  literally `(allow default)` plus deny-**read** rules, its Linux launcher unshares only
  `CLONE_NEWUSER`/`CLONE_NEWNS` (never `CLONE_NEWNET`), and on this machine `detect_backend()` returns
  `none` (macOS 26 refuses `sandbox_apply` for third-party callers), so it is not applied at all and
  logs "No OS-level sandbox available". Building the no-socket property on it would have left the
  atom's whole point resting on nothing. `wrap_argv` is still used (for the credential-path half it
  really does cover), but the **live rail is `knowledge_providers/pack_parse.py`'s in-process fence**,
  installed before the pack script runs. It is THREE mechanisms plus a verification, and the division
  of labour between them is MEASURED rather than assumed: under `python -I` exactly three denied names
  are already in `sys.modules` (`os`, `os.path`, `posix`) and no others. (a) A `sys.meta_path[0]`
  finder refuses `DENIED_MODULES` — sufficient on its own for every name that is NOT pre-imported, so
  it is what stops `socket`, `ssl`, `ctypes`, `subprocess`, `urllib.request` and `importlib` down every
  route including `importlib.import_module`. (b) The three pre-imported names are EVICTED from
  `sys.modules`, without which `import os` is served from the startup cache and never reaches a finder
  at all — the finder alone would leave the spawn-a-child route wide open. (c) The process-spawning
  callables on those same live module objects are NEUTERED, because eviction removes the *name* and
  not the *object*: `object.__subclasses__()` finds `os._wrap_close`, whose `__init__.__globals__` IS
  the os module dict, so `os.system` stays reachable with no import whatsoever. The child parses one
  body and is discarded, so wrecking its copy of the stdlib is free. `os`/`posix` are neutered
  SELECTIVELY (the exec/spawn family only, not `os.stat`), because blanket-neutering broke `linecache`
  and made an ordinary parser bug die inside our own error handler and surface as "no terminator" —
  measured, then fixed. (d) After the script returns the harness re-verifies (a) and (b) and reports
  `fence: tampered` otherwise, which discards the batch. A FOURTH mechanism was built and then
  deleted — see the falsification record below; it reded nothing and the measurement above is why.
  **`DENIED_MODULES` is a denylist DELIBERATELY, and an allowlist was built first and rejected on
  evidence.** An allowlist of importable modules breaks legitimate parsers for reasons an author
  cannot predict (the stdlib's own lazy imports: `csv`→`_csv`, `random`→`os`, `re`→`re._compiler`),
  and a fence that fails on correct code gets removed by the next person. The denylist is closed *for
  this property* rather than being a general jail: every in-process network path in CPython bottoms
  out at `_socket` (which `socket`/`ssl`/`http`/`urllib`/`asyncio`/every third-party client is built
  on), `_ctypes` (raw libc), or a spawned child (`os`/`subprocess`/`_posixsubprocess`/
  `multiprocessing`). It is interpolated into the harness source, so the frozenset is the ONE live
  definition — there is no hand-copied second list inside the harness string to drift from.
  **Fail-closed output is structural, not a check.** The harness emits a **nonce-tagged terminator**
  only after the script returns; the nonce arrives in a config file the harness unlinks on read (the
  `schedule_script.py` idiom), so a script cannot forge it, and the parent REQUIRES that line. That
  one mechanism makes truncation, a timeout kill, an `os._exit` and a crash all indistinguishable
  from each other and all fail closed. Garbage, a torn line, an over-cap batch and a wrong-shaped row
  yield ZERO items plus a typed `ParseFailure.code` — deliberately never a subset, because a
  half-ingested feed is indistinguishable from a feed that shrank — and the **cursor does not
  advance**, so a refused batch is re-offered rather than skipped past. Bounds reuse core's own
  ceilings (`spawn_shim_argv(PROFILE_TOOL)`, `build_child_env`, wall clock, input cap, output cap)
  rather than a bare `subprocess.run`; the child also runs `python -I` so no inherited `PYTHONPATH` or
  `sitecustomize.py` can pre-import anything before the fence exists.
  **Manifest schema decisions, each with a reason a reviewer can check.** `fetchSpec.method` is
  `GET`/`HEAD` only — a pack that could declare a POST would be an unattended write to somebody
  else's service on a timer, authorized once at install; a pack needing a write graduates to a full
  `KnowledgeSourceProvider` where the code is reviewable. `argsSchema` is flat and scalar because an
  arg's only job is substitution into a URL and there is no substitution of a nested object into a
  URL — so this is the CORRECT grammar here, not a smaller one, and it is why WS-3's
  `_validate_against` was deliberately NOT lifted out of `web_source.py` (refactoring a tuned module
  to share a walker this schema does not need would have been churn). A secret in `fetchSpec.url` is
  refused (a URL reaches the egress audit row, the remote access log and any redirect's `Referer`),
  and an `Authorization`/`Cookie`/`X-Api-Key` header carrying a literal is refused because a manifest
  ships to a Store — the same rule `packs/connectors.py` states as "schema-banned from carrying a
  value-bearing field", and the header list has ONE definition asserted equal across the two modules.
  Two CROSS-FIELD rules make the kind coherent rather than merely well-formed: `sources[]` without a
  `knowledge`/`source` provider is refused (declared scripts nothing can drive — the inert-surface
  shape), and `sources[]` without `permissions.network` is refused. Precisely stated, because the
  shipped consent surface is precise about it: `network` is DISCLOSURE and not containment
  (`installConsent.tsx` says so in as many words, and app-platform.md §permissions documents
  why there is no per-app egress chokepoint), but without the declaration that card reads
  "Network access: not declared" for an app whose entire purpose is scheduled outbound
  fetching — disclosure that reads the wrong way is worse than none.
  **DEVIATION (recorded): no new `PROVIDER_TYPES` entry, and the #47 rule is not in play.** A
  connector pack is not a new provider type — it rides `knowledge` (already present since WS-1) with
  `capabilities: ["source"]`. `sources[]` is a manifest BLOCK, not a type, so
  `test_manifest_types_match_handlers` is untouched and stays green. The §Plug-in Map's
  `ALLOWED_HOOK_PROVIDERS` note needed nothing either: it applies IF a pack ever ships an `action`
  provider, and this atom ships none.
  **DEVIATION (recorded): `fetchSpec`/`argsSchema` are camelCase**, not §7.1's `fetch_spec`/
  `args_schema`. The manifest's own convention is camelCase for multiword keys
  (`settingsSchema`, `displayName`, `minPersonalClawVersion`); matching the plan's snake_case would
  have made this the one block spelled differently from every other.
  **RECON, INCLUDING ONE THING THAT CHANGED UNDER ME MID-SESSION.** (1) At the commit this work was
  cut from, `WS-9` was `todo`, `SourceCreatePage.tsx` did not exist, and `store.create_source` had
  zero non-test callers exactly as `WS-3` and `WS-4` recorded — so "bundled recipes surface in the
  create flow" was first built as the seam a create flow reads (`GET /api/knowledge/source-recipes`,
  `?url=` for the "already covered?" answer). **Then `WS-9` landed on `origin/main` (`ff6f78f6`)
  while this atom was in flight**, which is exactly the stale-checkout trap: computing readiness from
  a worktree instead of `origin/main` had me about to ship a route with a note saying its UI was
  still to come, when the UI was already on `main`. Caught by a `git diff origin/main` that showed
  `SourceCreatePage.tsx` as a DELETION. Rebased `--onto origin/main` and integrated for real: the
  create screen now opens with a paste-a-URL lookup that offers matching recipes and seeds the
  form's own fields from the resolved spec, so what saves is what the user reviewed. (2)
  `packs/connectors.py` is NOT a half-present connector concept — it is AGENT-PACKS §3.3's
  MCP-connector catalog (credentials, `mcp.json` servers, substitution) and shares nothing with a
  knowledge connector pack but the word; it was read before designing and correctly left alone. (3)
  `knowledge/connectors/` (`BaseConnector`, `WebUrlConnector`) is the bookmark-scrape fetch leg the
  §9 disposition table marks ABSORBED by web-source; also unrelated.
  **Falsification: 17 mutations on live lines. Fourteen reded; THREE reded nothing, and all three
  were FIXED rather than noted — one of them by deleting a mechanism.**
  *(a) Removing the `builtins.__import__` wrapper reded nothing.* Investigated instead of accepted,
  and the reason was measurable: under `python -I` exactly THREE denied names are pre-imported
  (`os`, `os.path`, `posix`) and no others, so once those are evicted every denied import already
  reaches the meta-path finder — the wrapper was a second path to the same refusal. It was
  **DELETED** (a dual path this project does not keep, and an untested layer is decoration rather
  than defence), and `test_each_fence_mechanism_is_named_in_the_harness_and_none_is_redundant` now
  pins the pre-imported set, so a future Python that pre-imported `socket` reds instead of silently
  moving `import socket` out of the finder's jurisdiction. The three surviving mechanisms are each
  individually falsifiable: removing the finder reds 7 tests; removing the eviction loop reds 2;
  removing the neutering reds the gadget test with `DID NOT RAISE ParseFailure`.
  *(b) Making the terminator optional AND (c) removing the `sys.modules["__main__"]` swap both reded
  nothing* — and they turned out to be the same gap: NO test reached the `INCOMPLETE` branch at all
  (the timeout path raises before the terminator is ever parsed, and the "partial line" fixture's
  `SystemExit` is caught by the harness, which then emits a terminator normally), and nothing reached
  the harness's own globals. Finding the actual hole took a probe: `rfind` alone does not defeat
  forgery, because a script that **closes stdout after writing a forged terminator** leaves its own as
  the last one — so the nonce is what defeats it, and `sys.modules["__main__"]` is exactly how a script
  would read the nonce. `test_a_script_cannot_forge_the_terminator_and_claim_an_intact_fence` now does
  precisely that (read nonce → pop the fence → print a row → forge `{"fence": "intact"}` → close
  stdout) and reds on BOTH mutations, the swap one with the outcome message `a forged terminator got a
  batch accepted`. One test, three mechanisms given teeth.
  **A fourth mutation reded on the WRONG line and the test was rewritten.** Dropping poll-time
  `entry.validate()` reded `test_a_manifest_that_became_invalid_after_install_stops_polling`, but with
  `assert 'fetchSpec.method' in "source 'releases' declares method 'POST'"` — i.e. `render_fetch`'s
  OWN method check had caught it and the poll-time schema pass was never the thing under test. The
  fixture now mutates the installed manifest to an inline-literal `Authorization` header, which is
  the case only the schema catches; the mutation then reds with
  `assert [SourceItem(...)] == []` — a poll that fetched and ingested with a committed credential.
  **The other thirteen reded correctly, with the exact message:** `DENIED_MODULES = frozenset()` →
  `assert '_socket' in frozenset()` plus two `DID NOT RAISE ParseFailure` (this one is kept IN-TREE as
  `test_the_socket_proof_is_not_vacuous`, which asserts the real loopback listener DOES accept a
  connection once the fence is empty, so the proof cannot rot silently); `_rows_from` tolerating a bad
  line → `DID NOT RAISE`; advancing the cursor on a parse failure →
  `assert '{"etag": "e"}' == '{"etag": "old"}'`; the renderer's URL-secret guard → `DID NOT RAISE
  PackConfigError`; `_default_secret` returning `""` → `DID NOT RAISE`; `normalize_row`'s
  http(s)-scheme check → `assert 2 == 0` (two items ingested, one of them a `javascript:` link); the
  manifest's inline-credential-header rule → an empty error list; `resolve_spec` filling a missing
  capture group with a blank → `DID NOT RAISE KeyError`; a recipe pattern matching everything → 8
  tests including `test_an_uncovered_url_matches_nothing_rather_than_guessing`; and `validate_recipe`
  dropping the capture-group rail → `assert False`.
  **Two gaps the mutations exposed and closed, worth stating separately** because both were shipped
  code nothing measured: `_default_secret` (the resolver that actually runs in production — every
  other credential test injected a fake) and `normalize_row`'s url-scheme clause. Both now have their
  own case.
  **A FOURTH mutation reded nothing, and it caught a rail that only LOOKS like one.** The full suite
  reded once on `test_spawn_ceiling_audit.py::test_every_spawn_site_is_classified` — a genuine repo
  rail catching the new `subprocess.run`, classified in `_CEILING_WRAPPED` with the ceiling it
  carries. Then, deleting `spawn_shim_argv(wrapped, PROFILE_TOOL)` from `run_parse_script` left that
  audit **and every other test green**: its map asserts a site is *described* as ceiling-wrapped, not
  that it *is*. That is the declared-but-inert shape one layer above the code, and it is
  pre-existing for all fifteen mapped sites rather than something this atom introduced — so rather
  than rewrite a shared audit, this atom's own claim was made falsifiable:
  `test_the_spawn_really_carries_the_bounds_its_audit_entry_claims` reads the AST of
  `run_parse_script` and asserts the four bounds are actually applied there. Removing the ceiling now
  reds with `the tool resource ceiling is not applied`; swapping `build_child_env` for
  `dict(os.environ)` reds with `the child inherits an unfiltered environment`.
  **Gates:** `make lint` clean (black 1690 files, isort, flake8, mypy **874** source files);
  `tests/test_connector_pack.py` = **80 passed** (79 + the spawn-bounds AST case); the rails `test_app_manifest.py` +
  `test_provider_registry.py` + `test_entity_seam_handlers.py` + `test_apps_import_boundary.py` +
  `test_action_provider_chokepoints.py` + `test_inert_surface_baseline.py` + `test_portability.py` +
  `test_durability_inventory.py` + `test_api_manifest_drift.py` + `test_agent_reference.py` +
  `test_roadmap_dag_derived.py` = **219 passed, 1 skipped**; full backend suite **20608 passed, 30
  skipped, 12 xfailed, 0 failed** on the rebased tree. An earlier run had exactly one red —
  `test_spawn_ceiling_audit.py::test_every_spawn_site_is_classified` on the new `subprocess.run` —
  which was a real rail doing its job, not a flake. `reference/routes.md` + `index.md` regenerated in the same commit
  (687 → 688 agent-callable routes, on top of WS-9's four) — the byte-compare rail.
  **Frontend gate (this atom touches `web/`, so it is the FULL repo-wide run, never a path-scoped
  subset):** `npm run typecheck --workspace web` clean, `npm test --workspace web` = **2692 passed
  across 271 files**, `npm run build --workspace web` clean. That full run is what caught the
  integration's one real defect: three repo-wide design ratchets
  (`disabledReason.test.tsx` ×2, `disabledReasonTriage.test.ts`) reded on the new `Check` button
  being disabled with no `disabledReason`, i.e. a keyboard user tabbing past an action that could
  not say what was missing. A path-scoped vitest run would have shipped it. `docs/design/
  consistency-audit.json` is regenerated because the reporter writes it as a side effect of that
  run; `driftHits` (7) and `filesWithDrift` (6) are unchanged, so the integration added no drift.
  **`test_inert_surface_baseline` reded once and was FIXED, not regenerated.** The new SDK export
  `connector_pack_provider` counted as inert (`src/personalclaw/sdk/knowledge.py: 3 -> 4`) because the
  census scans for `from personalclaw.sdk… import <name>` and the test suite was importing the factory
  from the core module. The fix is the reader the census was asking for AND the more correct import:
  the tests now reach the factory through the SDK facade, which is the exact line a pack's own
  `provider.py` writes. The committed baseline is untouched.
  **`pyproject.toml` gains `knowledge/sources/recipes/*.json` in `package-data`:** without that line
  the wheel ships an empty recipe directory and every pasted URL silently looks uncovered — a product
  regression with no error anywhere, which is why the shipped-count assertion is load-bearing rather
  than cosmetic. No `web/` change, so no FE gate. No new config field, enum, trigger kind or
  `_EDITABLE_CONFIG` entry.
  **CHANGELOG: yes** — and this is the first WS atom that earns one. `WS-2`/`WS-3`/`WS-4`/`WS-5` all
  declined because `store.create_source` had no non-test caller, and that is still true for creating a
  source. But this atom ships two things a user reaches today: a connector pack installs and enables
  through the ordinary app path and polls on the engine's schedule with no Sources UI in existence,
  and `GET /api/knowledge/source-recipes` answers. The entry says exactly that and does not promise
  the paste-URL create-flow UI, which is `WS-9`'s.

- **DONE — `WS-7` streams: `SourceItemIngested`/`SourcePollCompleted`/`SourceQueryMatched` + saved
  queries + digest handoff (§6.1, §6.2, §6.4, §8, §11 step 5's events half).** Three new modules under
  `knowledge/`: `source_streams.py` (the events + the interim spool), `source_queries.py` (saved
  queries + the trigger bridge) and `source_digest.py` (the §6.2 digest). `source_engine.py` gains two
  emit seams and one query-evaluation call on the real poll path.
  **INTERIM SPOOL, not a bus — measured, not assumed.** `git grep 'event_bus\|EventBus'` over
  `src/personalclaw/` returns ZERO hits, so AUTOMATION-SUBSTRATE's bus does not exist. The atom's own
  dep note sanctions the spool until it lands, so `SourceEventSpool` is an append-only JSONL log plus
  an absolute-`seq` cursor and nothing else: no subscriber registry, no dispatch, no delivery
  semantics. That is the smallest thing the bus can later drain, and building bus machinery here would
  have minted a second mechanism for the substrate to delete. It trims at 5000 records keeping 2500,
  with `seq` written INTO each record so a consumer's cursor survives a trim.
  **`SourcePollCompleted` fires on EVERY exit of `poll_source`** (not-enrolled, provider raised, soft
  error, success) — the same reasoning that made `next_poll_at` unconditional in WS-3: a poll event
  visible only on success makes a source that STOPPED producing indistinguishable from one producing
  nothing, which is the only question a stream consumer asks.
  🔴 **`budget_spent` reads 0 for every shipped provider, and that is deliberate.** §6.1 specifies
  `{source_id, new_count, escalations, budget_spent}`, but `SourcePollResult` carries no request count
  — only `SourcePreview` does (`knowledge_providers/base.py:163`), while `web_source.poll` builds a
  `_Budget` counter (`web_source.py:1314`) it never returns. The field is read duck-typed
  (`getattr(result, "requests_used", 0)`) so adding it is a one-line provider-side change; it was NOT
  filled with a derived number, because a fabricated request count makes the egress audit surface lie.
  **Follow-up worth an atom row: add `requests_used` to `SourcePollResult` and populate it at
  `web_source.poll`'s five return sites.**
  🔴 **§6.4's literal trigger shape does not exist and was NOT built.** The plan says a Trigger
  subscribes with `{source: SourceQueryMatched, pattern: {query_id}}`. The SHIPPED matcher's `source`
  is one of three enum values (`event_triggers.EVENT_SOURCES` = memory/inbox/app) and its pattern kinds
  are a closed set, so the literal shape would require a FOURTH event source — a second matcher for one
  producer, which the substrate's own round-2 amendment forbids ("no new trigger kind, no second
  matcher"). DEVIATION: the match fires through `trigger_sources.registry.emit`, the existing single
  app-source ingestion point, which namespaces it to `app:watched-sources:SourceQueryMatched`, fences
  the text at origin and reaches `event_triggers`. The query id rides `meta.query_id` — exactly where
  the inbox bridge puts `sender`/`address`. A user subscribes with an ordinary `AppEvent` trigger whose
  `event_glob` matches that name; `test_a_subscribed_trigger_fires_END_TO_END` drives every link.
  The `app:` prefix on a core-contributed source is a naming wart worth an owner call later; it is not
  worth a fourth vocabulary now.
  **Only `SourceQueryMatched` is declared to triggers.** `SourceItemIngested`/`SourcePollCompleted`
  stay on the spool: declaring an event in the browsable vocabulary that never reaches the bus is the
  "declared kind without a runtime" defect, and bridging a per-item event to triggers is a firehose
  this atom was not asked to open.
  **Matching reads the STRUCTURAL row, never the event payload.** The payload's title is fenced, and
  §6.1 says payload content never participates in pattern matching. A matcher that stripped a fence to
  look inside it would be a fence-break with extra steps, so saved queries read title/url/content as
  the provider emitted them and the digest re-resolves each item from the store.
  **The digest is a callable, NOT a bundled workflow template.** There is no bundled morning-digest
  template on `main` (no `templates/` dir under `workflows/`, no "morning" template anywhere), and
  inventing a template format for one consumer would have put `fence_untrusted` inside a
  user-editable prompt string — a security control a template author could delete. A clock trigger
  calls `run_morning_digest`. Synthesis is `one_shot_completion(use_case="background")`, the item is
  ONE `note`/`digest` row, and the notification goes through `DashboardState.notify` →
  `notification_allowed()` with the gate NOT re-implemented.
  🔴 **The digest notification uses `notification_kinds.INFO`, not a dedicated kind.** A
  `source_digest` kind belongs in `notification_kinds.py`'s registry (plus its inventory ratchets),
  which is outside this atom's file fence; `INFO` is registered, ranks correctly and is configurable
  today. **Follow-up: a dedicated kind alongside the Sources UI (`WS-9`/`WS-10`).**
  **SC#8 is asserted adversarially, not structurally.** A real injection payload rides scraped
  CONTENT through the real poll path into the real prompt; the guard asserts it appears exactly once
  and only inside `<untrusted_content source=source:… source_type=watched_source
  transformation_path=digest>`, that the instruction giving the fence meaning precedes every block,
  and that a payload carrying the CLOSE marker cannot end the fence early (exactly one close marker
  survives). Containment is asserted too: the run's only writes are one note and one notification, so
  a model that obeyed the injection could change the digest's prose and nothing else.
  **Falsified twice.** (i) `_emit_ingested`'s event name mutated to `SourceItemIngestedX` (grepped
  back) → 5 reds in `test_watched_sources_streams.py` including the two count assertions; restored by
  `cp` from `/tmp`. (ii) `fence_item` short-circuited to return the raw body (grepped back) → both
  injection guards red (`substring not found` on the fence attributes, and the payload outside the
  span); restored by `cp`. Every guard carries a vacuity assertion: the deduped second poll emits
  ZERO further ingested events, the two `new_count` values DIFFER, two of three items fail the saved
  query, `mute_all` suppresses the digest notification, and the exploding-LLM patch is proven to fire.
  **A frozen test clock was a measured false green.** `_due_delay` compares against the store's
  wall-clock `last_poll_at`, so a clock at `t=1_000_000` leaves every polled source permanently "not
  due" and every later `tick()` a silent no-op that reads as "no second event emitted". The clock is
  real-time based and advances past the SOURCE ROW's own `poll_interval_secs` (3600 from
  `create_source`, which wins over the config default) — a 60s nudge was not enough.
  No `web/` change, so no FE gate. No new config field, enum, trigger kind or `_EDITABLE_CONFIG`
  entry. Three new state files under `<home>/sources/`: `events.jsonl`, `saved_queries.json`,
  `digest_cursor.json` — all resolved per call through `config_dir()`, never import-bound, and every
  test asserts the redirect lands under `tmp_path`.
  **CHANGELOG: no.** Nothing here has a user-reachable entry point yet: saved queries have no UI
  (`WS-9`/`WS-10` own the Sources section) and `run_morning_digest` has no bundled clock trigger
  pointing at it. The events + fence are the substrate the next atoms consume. **This atom is
  therefore PARTIAL in one respect worth recording: the digest is invocable and fully tested, but
  nothing in the shipped product calls it yet** — a bundled clock trigger (or a template that
  references it) is the missing user-reachable half and belongs with the Sources UI.
  🔴 **Two gate findings worth keeping, both fixed rather than worked around.**
  (1) `scripts/gate_report.py`'s `structural-duplication` gate caught `source_streams.py`
  re-deriving a durable write (`durable-write:_maybe_trim`, count 0 → 1): the trim was a local
  `mkstemp` + `os.replace`. Replaced with the canonical `personalclaw.atomic_write.atomic_write`;
  the gate is back to 6/6 PASS with 0 failures. A local copy of a crash-safety guarantee is exactly
  the second implementation that later drifts from the first.
  (2) **`monkeypatch.setattr("personalclaw.config.loader.config_dir", …)` is not undoable when it
  is live during a consumer's FIRST import.** `providers/entity_routes.py:22` does `from
  personalclaw.config.loader import config_dir`, so the consumer keeps the LAMBDA and monkeypatch's
  undo — which restores only the loader module's attribute — cannot reach the copy. Under xdist this
  made the `mute_all` vacuity test read the PREVIOUS test's home in the same worker
  (env=`…/test_mute_all…/home`, actual=`…/test_digest_makes_ONE_item…/home`), so `mute_all` was
  never seen and the notification delivered — a red that pointed at the digest bypassing the gate
  when the gate was fine. Both new test files now use `PERSONALCLAW_HOME` alone as the lever
  (`config_dir()` reads it per call and caches nothing, so it CANNOT be baked in), the digest file
  additionally re-points `entity_routes.config_dir` at the real live function to undo any bake-in a
  sibling suite performed, and both fixtures ASSERT the redirect binds. The `mute_all` test also
  asserts its precondition (`notification_allowed(INFO) is False`) before running the digest, so an
  isolation leak now fails at the precondition instead of masquerading as a security-control bypass.

- **[2026-08-25][`WS-7`] CORRECTION — this atom was briefly flipped `done` and that was WRONG.** A
  roadmap tick read the `**DONE — \`WS-7\` streams: …**` opener of the entry above, verified the four
  `done_when` deliverables against `origin/main` (`fc597af4`), found them all present, and flipped the
  status. It did **not** read to the end of the same entry, where the bold ruling sits ~80 lines below
  the opener: *"This atom is therefore PARTIAL in one respect worth recording: the digest is invocable
  and fully tested, but **nothing in the shipped product calls it yet**"*. The flip was reverted before
  merging; `dag.json` stays `todo` and now carries a `blocked_reason` quoting that ruling.
  **The failure mode is worth more than the fix.** Every deliverable *existed*, so a symbol-and-path
  check passed cleanly. What was missing was a **caller** — `run_morning_digest` has no bundled clock
  trigger pointing at it — which is precisely the inert-control shape this repo's own census doctrine
  says to test for: *"a census that asks only 'is there a caller?' is one level too shallow — ask
  'would deleting the caller be caught?'"*. Here the answer is that deleting the caller would be caught
  by nothing, because there is no caller. **An entry's opener is not its verdict; the last ruling in an
  entry is.**
  🔴 **`tools/audit_landed_atoms.py` had this atom right and its REASON wrong, which is why the reason
  matters.** It bucketed `WS-7` as LANDED-BUT-GATED — correct — but justified it as *"the log names an
  owner call / BLOCKED"*, matching the unrelated sentence *"a naming wart worth an owner call later; it
  is not worth a fourth vocabulary now"*. A correct verdict resting on a wrong reason invites exactly
  the override that happened here. That reason is fixed separately (the deferral filter demotes the
  `later` clause), moving `WS-7` to `PARTIAL_OR_UNMET` — *"the log names an unmet clause"* — which is
  the true one. Its LANDED-AND-CLEAN sibling error on `MRT-5` is fixed in the same change.
