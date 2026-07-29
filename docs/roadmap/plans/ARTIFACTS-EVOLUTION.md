# Plan: Artifacts Evolution — From a Files Tab to a First-Class Creative Library

**Status:** IN PROGRESS — S1a/S1b/S2 shipped; **S3 T3.1 + T3.4 + T3.3 shipped 2026-07-29** (resolver + @-references + version compare); T3.2 (iterate panel) blocked on a ChatEmbed host bridge / artifact WS event — see the S3b log. Created 2026-07-26 (roadmap rev 13; owner ask: sibling-platform gap analysis round 2)
**Created:** 2026-07-26
**Wave:** 2 (S1: the split + store hardening; S2: the library surface) + 3 (S3: iterate-with-agent + diffs + chat references)
**Depends on:** nothing hard for S1-2 (builds on the shipped `artifacts/` package, WidgetFrame/ReactWidgetFrame, and the Files page). S3 has a hard dep on **INVESTIGATE-ANYWHERE (60)** — the iterate panel consumes its primitive (registers an `artifact` resolver against plan 60's registry; reuse, don't duplicate). Coordinates with DESIGN-SYSTEM-CONSISTENCY (51, shipped — the library grid uses the primitives/tokens), FLUID-MOTION (52 — grid/preview motion inherits its tiers when it lands), WORKFLOWS-V2 program (loop/cron provenance already flows in via `source`; no engine coupling), APP-PLATFORM-EVOLUTION (48 — apps read artifacts only via existing SDK/API surfaces).
**Scope:** owner decision: artifacts were coupled to the Files page for **navigational similarity only** — split them into their OWN surface and evolve toward what the sibling ecosystem proved: stable identity, version history + revert, a library with live previews, and "iterate with agent." Code recon (below) shows the *store* is already first-class — the real gaps are the surface and the iterate loop. **S1 — the split + store hardening:** artifacts get their own top-level route + nav entry (`#/artifacts`), the Files page keeps raw workspace files only, deep-links migrate, and the `artifact_save` tool gains server-backed list-before-save dedup (a similar-artifact hint, not just skill prose). **S2 — the library surface:** a grid with LIVE sandboxed preview thumbnails (WidgetFrame/ReactWidgetFrame scaled down, theme-token-injected, lazily mounted), search/filter/collections, and the version timeline + revert promoted into the library detail. **S3 (Wave 3) — iterate with agent:** a side chat panel beside an open artifact seeded with its content via plan 60's primitive, version-to-version diff, and `@`-artifact references in chat that resolve to the library. **Soul guardrails:** (1) **one artifact store** — the existing `artifacts/` package IS the entity; this plan never forks a second store or a parallel "library" model; (2) **previews are sandboxed exactly like chat widgets** — same `sandbox="allow-scripts"` isolation, same srcdoc/theme contract, scaled not re-implemented; (3) **versions are immutable, identity is the slug** — nothing here may change slug derivation (`widgetSlug.ts`'s FNV scheme is load-bearing: changing it orphans every saved chat widget). Class **B** where it adds persisted metadata (the `collection` field on `meta.json`) — pre-LIFECYCLE-DOCTRINE, so it lands as a **plain clean break under the pre-1.0 banner** (tolerant reads, no gate/migration; CHANGELOG entry + snapshot advice in release notes).

---

## Context (code recon, 2026-07-26 — be honest about what exists vs missing)

**Already built (far more than the "evolve toward" list assumed — the store IS the entity):**
- **A real artifact entity with stable identity, versions, revert, events, provenance.** `src/personalclaw/artifacts/` — `models.py` (246 lines): `Artifact` dataclass with `slug` (URL-safe, regex-validated, the on-disk dir name — path-traversal-proofed), monotonic `version`, `kind` ∈ {widget, html, react, markdown, svg, json, text, infographic, document, image}, `source` ∈ {chat, cron, subagent, manual, import} (provenance!), `tags`, `project_id`, `events: list[ArtifactEvent]` (created/edited/iterated/referenced/reverted, each carrying `by` + `session_id` — the chat deep-link), caps (50 versions FIFO-pruned, 1MiB text / 16MiB binary bodies). `provider.py`: `ArtifactProvider` ABC (list/get/create/update/revert/create_binary/update_binary/raw_bytes/find_by_source_path) behind `registry.py`; `native.py` (752 lines): on-disk under `config_dir()/artifacts/<slug>/` (`meta.json` + `current.*` + `versions/vN.*`), coarse-locked, atomic writes; **server-side revert** (kind-agnostic, restores vN as a NEW current version — never round-trips content through the client).
- **A full REST surface.** `handlers.py`: GET/POST `/api/artifacts`, GET/PATCH/DELETE `/{slug}`, `/{slug}/raw` (binary, per-version Content-Type from the on-disk extension), `/{slug}/regenerate` (image re-gen), `/{slug}/versions[/{version}]`, `/{slug}/events` (GET + POST record). Create already **dedups by `source_path`** (`handlers.py:113-116` — re-saving a file-backed artifact bumps the existing one via `find_by_source_path`).
- **Agent tools.** `mcp_artifacts.py`: `artifact_save/get/update/list/versions/delete` + image/video generate, dispatched against the native provider, project-scoped (`_current_project_id`), SEL-audited. **Dedup today is skill-prose-only:** the bundled `artifacts` skill (`skills/bundled/artifacts/SKILL.md`) instructs "Always `artifact_list` before `artifact_save`" — but `artifact_save` itself (`mcp_artifacts.py:346`) creates unconditionally; a name collision silently mints `-2` slugs via `_unique_slug` (`native.py:440-444`). That's the list-before-save gap.
- **Chat-side identity + save.** `web/src/ui/widget/widgetSlug.ts` — deterministic slug from `(messageTs, widgetIndex)` (two 32-bit FNV-1a passes; the comment marks the scheme as change-forbidden); `WidgetFrame.tsx:95-120` — save-as-artifact bookmark that reconciles across refresh, plus the C32 living-view bridge (a saved widget names its slug so the agent can `artifact_update` it in place). `widgetSrcdoc.ts` — theme-token injection (NE `--color-*` tokens + short aliases read live from the host, sanitized) into the sandboxed srcdoc; `ReactWidgetFrame.tsx` — the same for `kind:react` (blob-iframe, `sandbox="allow-scripts"`).
- **The Files-page viewer.** `web/src/pages/files/FilesSection.tsx` — Artifacts is ONE TAB in the Files top-bar (comment: "Unified Files + Artifacts page"); deep-link `#/files/<slug>` opens it. `artifacts/ArtifactList.tsx` (85 lines — a filter rail: text + kind chips) and `ArtifactViewer.tsx` (274 lines — render + edit/snapshot + **version picker + revert UI already working** + events timeline + download). `api.ts` has the full typed client (`artifacts`, `artifact`, `updateArtifact`, `artifactVersions`, `artifactVersion`, …).
- **Design cockpit precedent for live grids:** `pages/loops/DesignCockpitPage.tsx:598-625` — CanvasView renders multiple `kind:react` artifacts live via `ReactWidgetFrame`, ordered, tag-scoped (`loop:{id}`) — proof the "many live sandboxed previews on one page" pattern performs.

**Actually missing (the honest gap list this plan closes):**
1. **No standalone surface.** Artifacts live behind a Files tab; no top-level nav entry, no `#/artifacts` route (the skill's own prose "each lives at `/artifacts/<slug>`" is aspirational — the real deep-link is `#/files/<slug>`). No grid, no live thumbnails (the rail is text rows), no collections (tags exist but no curated grouping), no cross-cutting library view of images+widgets+docs together.
2. **No enforced dedup on save.** Skill prose only; the tool happily creates `sales-dashboard-2`.
3. **No iterate-with-agent.** ArtifactViewer's edit is a raw content textarea; "iterate" means going back to a chat and naming the slug. No side-by-side artifact+chat, no seeded context.
4. **No version diff.** The viewer shows one version at a time; no A/B comparison.
5. **No chat references.** The composer's `@` menu reaches knowledge items (`chat_runner.py:718::_inject_knowledge_content`) but not artifacts.

**Sibling precedent — KiroClaw's live-pointer model:** file-backed artifacts share an on-disk pointer with the file viewer; a deliberate Snapshot action + a `live_dirty` drift flag distinguish "the file moved on" from "the saved version." **Verdict: ALREADY ADOPTED — preserve it.** PersonalClaw independently has the identical mechanism: `source_path` (the workspace file is the live source of truth, `native.py:157-210` read/write-through), `live_dirty` computed per-read (`models.py` — live content differs from the latest snapshot; never persisted), the explicit Snapshot button (`ArtifactViewer.tsx:86-90`), and the drift dot in the rail (`ArtifactList.tsx` warning dot). This plan keeps the model exactly as-is and carries it into the library UI (drift badge on file-backed cards); no adopt-or-reject decision remains open.

## Design

- **S1 — the entity split + store hardening.** (a) **Route split:** `#/artifacts` becomes a top-level route + nav entry ("Artifacts", `FileCode` icon); `FilesSection` drops the `ARTIFACTS_TAB` and reverts to raw file roots only (Workspace/Home/Outbox); old `#/files/<slug>` deep-links redirect to `#/artifacts/<slug>` (clean break: the redirect is a one-line hash rewrite kept because external references — artifact events' `session_id` chat links, the skill prose — point at slugs, not because we keep dual paths; the Files-tab code is deleted in the same change). Skill/docs prose updates to the real route. (b) **List-before-save dedup, enforced server-side:** `artifact_save` without an explicit `slug` first checks for likely duplicates (same normalized name → same base slug; same kind + high name similarity) and, on a hit, returns a **structured refusal-with-hint** ("artifact `sales-dashboard` (v4, updated 2h ago) looks like this; pass `slug` to update it, or `force:true` to create anew") instead of silently minting `-2`. Explicit `slug` and `force` keep full power; the REST create path gains the same check behind a `?force=` escape. The skill prose stays (it teaches the flow) but the tool no longer depends on the model's obedience. (c) **`collection` field** on `Artifact` (single string, "" = none — the curated grouping the grid needs; tags remain free-form labels): tolerant reads, wired through models/provider/handlers/tools/api.ts.
- **S2 — the library surface.** `web/src/pages/artifacts/` — a **grid of live preview cards**: each card mounts the real renderer (WidgetFrame srcdoc pipeline for widget/html/svg/infographic/document; ReactWidgetFrame for react; markdown/text/json as styled excerpts; `<img>` off `/raw` for images) inside a fixed-aspect, `pointer-events:none`, CSS-scaled container — theme-token-injected exactly like chat (reuse `widgetSrcdoc.ts`/`readThemeVars`, zero new sandbox code). Previews mount **lazily** (IntersectionObserver; placeholder = kind icon tile) and cap concurrent live iframes (~12, LRU-unmount off-screen) so a 200-artifact library stays smooth — the DesignCockpit CanvasView proves the per-frame cost. Toolbar: text search (name/slug/description/tags — the existing client-side filter promoted), kind chips (as today), source filter (chat/cron/subagent/manual/import — surfacing provenance), collection picker + assign, sort (updated/created/name). **Detail view** = today's ArtifactViewer relocated (render + edit + snapshot + version timeline + revert + events + download), now full-page with the version rail always visible. Nothing about versions/revert is rebuilt — it ships today and moves house.
- **S3 (Wave 3) — iterate with agent.** (a) **The iterate panel:** an "Iterate with agent" action on the detail view opens a side chat panel (split view: artifact left, chat right) — implemented as plan 60's primitive: an `artifact` resolver registered against INVESTIGATE-ANYWHERE's registry (envelope = name/kind/version + current content, fenced; suggested task mode **`agent`** here, not `ask` — iteration means the agent may call `artifact_update` on THIS slug; the opening prompt names the slug so the C32 living-view bridge refreshes the preview in place as versions land). The panel is `ChatEmbed` (appSdk) pointed at the created session — no new chat surface. The preview re-renders on each `artifact_update` (poll the version, or the existing events feed). (b) **Version diff:** a Compare mode in the version rail — two-version picker → unified text diff for text kinds (client-side, no new endpoint: both bodies come from `/versions/{n}`), side-by-side rendered previews for visual kinds, before/after for images. (c) **Chat references:** the composer's `@` menu gains an Artifacts section; selection stamps `meta.artifacts: [slug]` and a `_inject_artifact_content` (beside `_inject_knowledge_content`, same fenced-labelled-block pattern, records a `referenced` event) resolves current content at turn time — chat mentions resolve to the library, live.
- **What this is NOT:** not a second store (the `artifacts/` package is untouched in shape); not a publishing/sharing system (SESSION-MANAGEMENT S3's share-as-artifact and any public publishing stay out of scope); not a widget-runtime change (srcdoc/sandbox/theme contracts are consumed, never modified).

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

### C1 — Model addition (`artifacts/models.py`; clean break, tolerant reads)
```python
# NEW Artifact field (persisted in meta.json; old meta without it reads as ""):
collection: str = ""   # single curated grouping; "" = uncollected. Tags stay free-form.
```
Wired through `to_dict`/`from_dict`, provider `list(collection=...)` filter, PATCH allowlist in `handlers.py::api_artifact_update`, `api.ts` `Artifact` interface. Collections are emergent (the set = distinct non-empty values — no separate collections store).

### C2 — Save dedup (provider + tool + REST; §2.2 error envelope)
```python
# artifacts/native.py (provider-level, so every caller gets it):
def find_similar(self, *, name: str, kind: str) -> Artifact | None: ...
  # exact base-slug match first (slugify(name) == existing slug), then
  # same-kind normalized-name equality; deterministic, no LLM.

# mcp_artifacts.py artifact_save, when no explicit slug and not force:
#   similar found → return structured hint text (slug, version, updated_at,
#   "pass slug=... to update, or force=true to create") — NO create. SEL outcome="deduped".
# handlers.py api_artifacts_create: same check; 409 {"error":{"code":"similar_artifact_exists",
#   "message":..., }} + hint payload, bypass via ?force=1 or explicit slug.
```
`source_path` dedup (`handlers.py:113`) is untouched — it already updates-in-place.

### C3 — Routes + library surface (frontend)
```
#/artifacts                    → ArtifactsSection (grid; query: ?q=&kind=&source=&collection=&sort=)
#/artifacts/<slug>             → detail (viewer + versions + events); ?v=N deep-links a version
#/files/<slug>  (legacy)       → hash-rewrite redirect to #/artifacts/<slug>
```
New components: `web/src/pages/artifacts/ArtifactsSection.tsx`, `ArtifactGrid.tsx`, `ArtifactCard.tsx` (lazy live preview: srcdoc via the existing `widgetSrcdoc.ts` builders, `sandbox="allow-scripts"`, CSS `transform: scale()`, `pointer-events:none`, IntersectionObserver mount + LRU cap), relocated `ArtifactViewer.tsx`. `pages/files/artifacts/` is deleted (clean break).

### C4 — Iterate + references (S3; consumes plan 60's registry — never a second launcher)
```python
# artifacts side (registered at boot, per plan 60 C1):
register_investigate_resolver("artifact", _resolve_artifact)
#   → InvestigateContext(kind="artifact", id=slug, snapshot=current content (capped),
#       suggested_task_mode="agent", back_link=f"#/artifacts/{slug}",
#       opening_prompt=f"Iterate on artifact `{slug}` (use artifact_update)...")
# chat side:
#   composer @-menu → meta.artifacts: [slug, ...]
def _inject_artifact_content(state, session, message) -> str: ...
#   (chat_runner.py, beside _inject_knowledge_content; fenced labelled block;
#    records a 'referenced' ArtifactEvent with session_id)
```

### Integration points
- **Calls:** the `artifacts/` package (provider/registry/handlers — extended, not forked), `widgetSrcdoc.ts`/`readThemeVars`/`ReactWidgetFrame` (preview reuse), plan 60's `register_investigate_resolver` + `ChatEmbed` (S3), `fence_untrusted` via the injection pattern (S3c), SEL (`artifact_*` audit already wired; dedup adds `outcome="deduped"`).
- **Called by:** chat WidgetFrame save/living-view (unchanged — same slugs, same API), DesignCockpit canvas (unchanged — tag-scoped list), Projects linked view (`/api/projects/{id}/linked` artifacts — unchanged), the nav shell (new route).
- **Storage owned:** `config_dir()/artifacts/` (existing; +`collection` in meta.json). No new stores.
- **Deliberately NOT touched:** `widgetSlug.ts` slug derivation (load-bearing), version snapshot format (`versions/vN.*`), the srcdoc/sandbox contract, `MAX_VERSIONS`/body caps, attention-path contracts.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — Entity split + store hardening

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | `collection` field: model + provider list filter + PATCH allowlist + tools (`artifact_update` collection arg, `artifact_list` filter) + api.ts type; tolerant read test (old meta.json loads with `""`) | `src/personalclaw/artifacts/models.py`, `native.py`, `provider.py`, `handlers.py`, `mcp_artifacts.py`, `web/src/lib/api.ts` | round-trips through REST + tool; pre-existing artifact dirs load clean |
| T1.2 | Server-backed dedup: `find_similar` on the provider + the `artifact_save` refusal-with-hint (+ `force`) + REST 409 `similar_artifact_exists` (+ `?force=1`); `source_path` path untouched; SEL `outcome="deduped"` | `native.py`, `mcp_artifacts.py`, `handlers.py`, tests | saving "Sales Dashboard" twice without slug/force yields the hint (tool) / 409 (REST), and NO `-2` slug exists on disk |
| T1.3 | Route split: `#/artifacts[/<slug>]` top-level route + nav entry; `FilesSection` drops the artifacts tab (delete `pages/files/artifacts/` after relocation); `#/files/<slug>` hash-redirect; skill/docs prose updated to the real route | `web/src/app/App.tsx`, nav shell, `web/src/pages/files/FilesSection.tsx`, `web/src/pages/artifacts/` (new home for the relocated viewer), `skills/bundled/artifacts/SKILL.md` | Files shows only file roots; an old `#/files/<slug>` link lands on `#/artifacts/<slug>`; viewer works at its new home byte-equivalent in behavior |
| V1 | Validation as a user: save widgets from chat (dedup hint exercised), navigate the new route, revert a version, confirm chat living-view still refreshes a saved widget; full local gate | — | holds |

### Session 2 — The library surface

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | `ArtifactCard` live preview: srcdoc-per-kind (widget/html/svg/infographic/document via `widgetSrcdoc`, react via ReactWidgetFrame's builder, markdown/text/json excerpt, image via `/raw`), scaled + inert + lazy (IntersectionObserver) + LRU iframe cap (~12) | `web/src/pages/artifacts/ArtifactCard.tsx`, `ArtifactGrid.tsx` | a mixed-kind library renders live, theme-correct previews; off-screen cards hold placeholders; theme switch re-renders visible cards |
| T2.2 | Grid toolbar: search + kind/source chips + collection picker/assign + sort; URL-query-backed (shareable filter state) | `ArtifactsSection.tsx` | filter state round-trips the URL; assigning a collection from the card menu persists |
| T2.3 | Detail view polish: full-page viewer with always-visible version rail + events timeline + `?v=N` deep-link; drift badge (`live_dirty`) on file-backed cards + detail | `pages/artifacts/` detail components | version deep-link opens the immutable snapshot; the drift badge matches the Files-era behavior |
| T2.4 | Perf proof: 200-artifact seeded fixture — grid scroll stays smooth (measure; no more than the iframe cap live), list endpoint stays content-free (verify `list` omits `content` end-to-end) | fixture + a perf note in the PR | measured, no jank; no content in list payloads |
| V2 | Validation as a user: browse/search/collect a real library incl. images + react widgets; token-lint/theme pass; web typecheck/test/build | — | holds |

### Session 3 — Iterate with agent + diff + references (Wave 3; needs plan 60 landed)

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | `artifact` investigate resolver (envelope per C4: content capped + fenced downstream, task mode `agent`, back-link, slug-naming opening prompt) registered against plan 60's registry | `artifacts/` boot registration, tests | "Iterate with agent" produces a session whose first turn carries the fenced artifact content |
| T3.2 | The split-view iterate panel: detail view + `ChatEmbed` side panel on the investigate-created session; preview refreshes when `artifact_update` lands a new version (events/version poll) | `pages/artifacts/` detail, using `appSdk.ChatEmbed` | ask the agent to change the widget → new version appears in the rail AND the preview updates without reload |
| T3.3 | Version diff: Compare mode — unified text diff (text kinds, client-side over two `/versions/{n}` bodies), side-by-side previews (visual kinds), before/after (image) | version rail components | pick v3 vs v7 → correct diff per kind |
| T3.4 | Chat `@`-artifact references: composer menu section + `meta.artifacts` + `_inject_artifact_content` (fenced, labelled, `referenced` event recorded) | `web/src/pages/ChatPage.tsx` composer, `dashboard/chat_runner.py`, tests | `@sales-dashboard` in chat grounds the reply in the current version; the artifact's events show `referenced` with the session id |
| V3 | Validation as a user: full loop — create in chat → find in library → iterate in the panel → diff versions → revert → reference from a new chat; full local gate | — | holds |

## Owner tasks (real world)
1. **Rule on the dedup default** — server-refuses-with-hint (proposed) vs hint-but-create. The refusal changes agent behavior on every save; dogfood it a week and confirm it doesn't annoy legitimate "another dashboard, same name" flows (the `force` escape is the pressure valve).
2. **Confirm S3's `agent` task mode** for the iterate panel (it deliberately diverges from plan 60's `ask` default — iteration requires writes to that one slug). If this feels too open, the fallback is `ask` + a one-click escalate.
3. **Name the nav position** — Artifacts as a top-level rail entry displaces nothing, but the rail is finite; confirm placement (proposed: beside Files).

## Risks & open questions
- **Live-preview iframe cost:** dozens of sandboxed iframes can hurt. Mitigated by lazy mount + LRU cap + `pointer-events:none` (no interactivity cost) — T2.4 measures against a 200-artifact fixture before ship; the fallback is static-thumbnail-until-hover, decided by measurement, not taste.
- **Dedup false positives:** deterministic name/slug matching only (no LLM, no fuzzy embeddings) keeps it predictable; the hint always includes the exact escape (`slug`/`force`). A false positive costs one retry, a silent duplicate costs a polluted library — the asymmetry favors refusing.
- **Route-split fallout:** anything hardcoding `#/files/<slug>` (chat event deep-links are built FE-side; grep all of `web/src` + docs) must move in the same change — the redirect is a safety net for *persisted* references (old sessions' rendered links), not an excuse to leave callers on the old route.
- **S3 sequencing:** if plan 60 slips, S3 must NOT hand-roll a seeded-chat variant (that's exactly the duplication the owner consolidated); S3 waits — record the stop point in the execution log per the split-plan convention.
- **Open:** whether collections should be orderable/nested — deferred; flat single-collection ships first (tags already cover cross-cutting), DISCOVERY-file if real use demands hierarchy.





## Execution log

- [2026-07-27][S1a] DONE (T1.1 + T1.2, backend): the store-hardening half of S1,
  shipped as an atomic backend sub-scope. **T1.1 collection field:** `Artifact.collection`
  (model + `to_dict`/`from_dict` tolerant read — pre-collection meta.json loads as ""),
  threaded through `NativeArtifactProvider.create`/`update` (metadata-only, no version
  bump) + `list(collection=...)` filter, the abstract `ArtifactProvider` signatures,
  REST create/update/list handlers (+ `_serialize` redaction), the `artifact_save`/
  `artifact_update`/`artifact_list` MCP tools + their schemas, and the FE `Artifact` type.
  **T1.2 server-backed dedup:** `provider.find_similar(name, kind)` (by derived slug,
  newest-first, read-only never-raise); `artifact_save` refuses a name-collision with a
  hint (update existing slug / `force=true`); `POST /api/artifacts` returns
  `409 similar_artifact_exists {similar:{slug,name,kind}}` (bypass `?force=1`), SEL
  `outcome="deduped"`. The existing source_path dedup path is untouched (a file-backed
  re-save still bumps, never 409s on name). Tests: `test_artifacts_collection_dedup.py`
  (11 — model round-trip + tolerant read, provider create/update/list-filter + reload,
  find_similar, REST 409+force, collection PATCH round-trip, source_path-still-bumps).
  Gate: `make lint` green, `make test` green, web typecheck green, reference regenerated
  (tools.md — new tool args). Class-B `collection` field = plain clean break (banner).
- [2026-07-27][S1b] DEFERRED (T1.3, route split): moving artifacts to their own
  `#/artifacts` top-level route + dropping the Files artifacts tab + `#/files/<slug>`
  redirect is a 470-line refactor of `FilesSection.tsx` (31 artifact-branch points; files
  + artifacts share the tab strip, header, content-search, explorer state, and the
  file-side drift badge consumes `artifactPaths`). Extracting it cleanly without
  regressing BOTH surfaces needs a focused pass — deliberately NOT rushed under
  unsupervised overnight execution (atomic-completability: ship the finished backend half,
  do the risky FE relocation with care). S1b is the next Artifacts session; S2 (library
  surface) builds on the split, so S1b precedes it.
- [2026-07-27][S1b] DONE (T1.3, route split): artifacts are their own top-level surface.
  `web/src/pages/artifacts/` is the new home — `ArtifactList.tsx` + `ArtifactViewer.tsx`
  relocated verbatim via `git mv` (imports repointed; `artifactKindMeta`/`relTime` stay in
  `files/fileMeta` — they have 10+ cross-page consumers, moving them is churn not cleanup)
  plus a new `ArtifactsSection.tsx` host: viewer fills width, list is the right-docked
  hidable SidePanel (the Files-era layout carried over), deep-link `#/artifacts/<slug>`,
  selection writes the slug to the URL (replace). App.tsx: `artifacts` NAV entry (FileCode,
  Platform group) + lazy route; e2e `routes.ts` gains the route (needsData). FilesSection:
  the ARTIFACTS_TAB, its render branch, and all `isArtifacts` state are DELETED (clean
  break — files-only now); a legacy `#/files/<slug>` deep-link redirects (replace) to
  `#/artifacts/<slug>` — kept for persisted references in old transcripts/events, not a
  dual path; a stale `files-tab=artifacts` localStorage value self-heals to the first root.
  "Save as artifact" navigates to the new page; the file-tree drift badge keeps its
  `artifactPaths` feed. Cross-page emitters updated: ProjectsSection + LoopsSection now
  navigate to `artifacts/<slug>`. The bundled artifacts SKILL.md prose already said
  `/artifacts/<slug>` (was aspirational; now true — no edit needed). Validated as a user
  on :10020 (Playwright): `#/artifacts` renders the nav entry + list + live widget render;
  `#/artifacts/<slug>` deep-link opens the artifact; legacy `#/files/<slug>` lands on
  `#/artifacts/<slug>` (hash verified rewritten) with the viewer working; `#/files` shows
  root tabs only (no Artifacts tab) with the explorer intact; ZERO console errors on all
  routes. Gate: web typecheck + 251 vitest + build green, `make lint` green, full backend
  suite green. Remaining: S2 (library surface — grid/previews/collections) builds on this.
- [2026-07-28][S2] DONE (T2.1-T2.4, the library surface): the `#/artifacts` route is now a
  real library. **T2.1 ArtifactCard + ArtifactGrid:** live srcdoc-per-kind previews —
  widget/html/infographic/document/svg via `buildSrcdoc`, react via `buildReactSrcdoc`
  (the EXACT chat-widget sandbox: `sandbox="allow-scripts"`, theme tokens injected, mode-
  aware), scaled 0.4x (rendered 2.5x-size then transformed), INERT (pointer-events-none +
  tabIndex -1 — the tile is one click target), LAZY (per-card IntersectionObserver,
  200px rootMargin), and LRU-CAPPED at 12 live iframes (module-level slot registry;
  evicted cards demote to kind-pill placeholders). markdown/text/json render a cheap
  600-char excerpt (no iframe); images use `/raw` with `loading="lazy"`. The lazy detail
  fetch also carries `live_dirty` → the "source changed" drift badge on file-backed cards.
  NEW `ui/TileButton` primitive (+doc) — the block-level clickable card the kit lacked
  (the raw <button> card tripped the primitive-adoption ratchet; fixed by adoption, not a
  baseline bump). DISCOVERY: `test_rendering_registry_parity.py` forbids hand-rolled
  IFRAME_KINDS/EDITABLE_KINDS Sets (the registry owns capability dispatch) — the card's
  iframe-kind check is now DERIVED from the registry (`isSandboxed(resolveContentType(...))`
  plus document/svg, which the full page renders in-DOM sanitized for the comment layer but
  the mini card sandboxes — no comment layer at card scale, one isolation path); `binary`
  from the registry replaces the image kind check. **T2.2 toolbar:** search + kind Segmented + source/collection Popover
  menus + Recent/Name/Kind sort — all URL-query-backed (?q/?kind/?src/?col/?sort, replace)
  so filter state is shareable; collections derive from the library; a header "Set
  collection" control on the detail assigns via PATCH (promptInput). **T2.3 detail
  polish:** `?v=N` deep-links a historical snapshot (viewer gains initialVersion/
  onVersionChange/defaultDetailsOpen; reload(keepVersion) honors the pin on first load
  only — save/snapshot/revert still return to current); version picks write ?v; the
  details rail opens automatically on a pinned load. **T2.4 perf proof:** 204-artifact
  seeded library (200 text fixtures + 4 mixed) — Playwright scroll through the full grid
  measured **283 frames, 0 long frames (>50ms), exactly 3 live iframes** at rest (≤ the
  12 cap); `GET /api/artifacts` verified content-free end-to-end. Validated as a user on
  :10021: grid renders live theme-correct widget preview; kind filter → ?kind=widget URL;
  card click → detail; v2 snapshot then `?v=1` deep-link shows the read-only historical
  banner + details rail open; collection filter → ?col=Dashboards showing exactly the 2
  collected cards; react artifact renders in the Babel frame; ZERO console errors
  throughout. Gate: web typecheck + 251 vitest + build green, `make lint` green, full
  backend suite green. **Remaining: S3 (Wave 3 — iterate-with-agent via the investigate
  registry, version diffs, @-artifact chat references).**

- 2026-07-29 — **DONE (S3, T3.1 + T3.4).** The iterate *entry point* and @-artifact chat
  references. T3.2 (split-view panel) and T3.3 (version compare) are the next slice —
  this one is the backend contract plus the chat surface, which is the atomically
  completable half.
  - **T3.1 resolver.** `_resolve_artifact` in `investigate.py`, registered as `artifact`.
    Snapshot carries kind/version/description/tags, the version list, and the CURRENT
    body; a file-backed artifact names its live source (the agent must edit THAT, or the
    next read reverts its work); a binary artifact contributes a raw-URL reference, never
    bytes.
  - **OWNER RULING APPLIED (2026-07-29): `suggested_task_mode="agent"`.** I had
    recommended `build` as least-privilege; the owner rejected that — *"The iteration may
    need the agent to search internet or read knowledge files or run commands or
    investigate project, etc. So only minimum permission mode doesn't really seem to be
    prudent choice here."* This is the ONLY resolver that suggests `agent`, and a test
    pins `InvestigateContext.suggested_task_mode`'s default at `ask` so the exception
    can't become the platform default by drift.
  - **DEVIATION (file placement):** plan C4 says "artifacts side (registered at boot)",
    but S1's own logged deviation put every resolver in `investigate.py`. Followed the
    shipped convention rather than the plan text — one registry, one file.
  - **T3.4 references.** `_inject_artifact_content` in `chat_runner.py` beside
    `_inject_knowledge_content`; composer `+`-menu row + `ArtifactContextPicker` +
    `meta.artifacts`. Grounds in the CURRENT version (referencing an artifact means
    "what it is now"), redacts credentials on the way in, and records a `referenced`
    event via the EXISTING `record_impression` — which is idempotent per session, so a
    long conversation about one artifact leaves one impression, not a per-turn flood.
  - **BUG FOUND IN OWN WORK:** the injection resolved the provider unguarded, so a
    broken artifact store would have raised into the turn. Now caught and degraded. The
    test that found it asserts the injection returns the message unchanged when the
    provider raises.
  - **TEST-ISOLATION BUG FOUND (worth recording — it will bite the next artifact test).**
    `artifacts/registry._providers` is a MODULE-LEVEL cache and
    `NativeArtifactProvider.__init__` resolves `config_dir()` EAGERLY. Patching
    `registry.get_provider` is therefore not enough: under xdist, a provider constructed
    by an earlier test in the same worker wins, and one test read another's artifact. Fix:
    `monkeypatch.setitem(registry._providers, "native", ...)` with an EXPLICIT
    `root=tmp_path`. Confirmed stable across 3 consecutive full-file runs.
  - **Design-system:** the ratchet rejected two raw `<button>`s in the new picker; rows
    now use the existing `MenuRow` primitive and chips use `Button`.
  - **Validated as a user** on an isolated dev home: `POST /api/investigate`
    `{kind: "artifact"}` created a real session staged with `agent` mode, the
    `#/artifacts/<slug>` back-link, and a slug+tool-naming opening prompt; after a
    version bump the resolver staged **v2 and not the stale v1**; the injection grounded
    in v2 and the artifact's timeline gained exactly one `referenced` event with the
    session id; the composer's "Reference an artifact" row opened a picker listing
    `widget · v2 · sales-dashboard` and selecting it produced a chip. 0 console errors,
    0 gateway tracebacks.
  - **Gates:** `make lint` clean (mypy 538 files) · backend **8859 passed** (20 new) ·
    web **283 passed** + typecheck + build.
  - Pre-existing/unrelated: `test_cron.py`'s spring-forward test (core issue #85).

- [2026-07-29][S3b] DONE (T3.3 — version compare) + the dead-code sweep S2 left behind.
  **T3.3 version diff.** New `ArtifactCompare.tsx`, opened from a "Compare versions"
  action beside the version picker and REPLACING the body rather than sitting beside it
  (the question "what changed between these two?" wants the full width; a diff stacked
  under a live preview leaves neither readable). Defaults to the two most recent
  versions, since "what changed in the last iteration?" is the question the surface
  exists for. Offered only when there are ≥2 versions — a disabled control on a
  one-version artifact just raises the question of why it's disabled.

  **How each kind renders is decided by the CONTENT-TYPE REGISTRY, not a local kind
  list** (the rule the viewer already follows, so a newly registered kind behaves
  sensibly here without editing this file): `binary` → the two versions side by side,
  because a pixel diff of a rendered image is noise and before/after is what a person
  wants; everything else → a real text diff. Visual kinds (widget/html/svg) ARE text
  underneath and their source is what changed; comparing two sandboxed iframes would
  be prettier and far less useful.

  **Reused the Monaco `DiffEditor` ELEMENT from `pages/code/DiffView.tsx`, not the
  component** — that one fetches its own git content and is file/git-specific. Copied
  its options verbatim, including **`ignoreTrimWhitespace: false`**: the default `true`
  silently hides whitespace-only changes, and an agent re-rendering a widget often
  re-indents it, so "nothing changed" would be a lie. Monaco is locally bundled
  (`monacoSetup.ts`), never a CDN.

  **PREMISE CORRECTION (E1-lite):** both T3.2 and T3.3 refer to "the version rail".
  There is no rail — the version picker is a `Segmented`/dropdown inside the
  collapsible bottom **Details** panel (`ArtifactViewer.tsx:225-240`). Built the
  Compare affordance there rather than inventing a rail, which would have been a third
  piece of unlisted work.

  **API GOTCHA, worth remembering:** `GET /versions/{n}` returns the artifact's
  **CURRENT** version number, not the requested one (`native.py:376-399` swaps only
  `content`). Every label in this component is built from the REQUESTED number; reading
  it off the payload would mislabel every diff pane.

  **TWO BUGS FOUND BY DRIVING IT IN A REAL BROWSER** — neither reachable from unit
  tests, and the first invisible to typecheck, lint and vitest alike:
  1. **The diff rendered as a blank panel.** I used `flex min-h-0 flex-1` on the root,
     but the host is a plain block wrapper, not a flex column, so `flex-1` resolved to
     zero height and Monaco (which needs a sized container) drew a one-pixel strip.
     `h-full` fixes it — which is exactly what `DiffView` uses, and now the comment says
     why.
  2. **Monaco threw on every version switch:** `TextModel got disposed before
     DiffEditorWidget model got reset`. My loading state cleared `bodies` to `null`,
     which swapped the mounted diff for a spinner and tore the editor down while its
     text models were still attached. Now the previous diff stays on screen until the
     next pair arrives — no teardown, and it reads better (content updates in place
     instead of flashing empty). Verified with five rapid switches: zero console errors.

  **Dead-code sweep:** deleted `pages/artifacts/ArtifactList.tsx` (85 lines, zero
  importers). S2's card grid orphaned it and left it behind — a clean-break violation,
  and it was a functional duplicate one generation behind on both design system and
  features. Ratcheted `primitiveAdoption.baseline.json` **278 → 277** in the same commit
  per the ratchet's own rule (the file carried 2 raw buttons; Compare's swap button adds
  1), and regenerated `consistency-audit.json`.

  **RATCHET LANDMINE (bit me, second time in this repo's history):** the
  primitive-adoption scanner is a **regex over source TEXT and counts matches inside
  COMMENTS**. My docstring explaining that the picker avoids a raw dropdown named the
  element in angle brackets — and tripped the ratchet at 150 > 149. The prose now
  deliberately avoids naming it, with a note saying why. Also: the version picker uses
  the shared `Segmented` primitive (`size="sm"`, `collapse="menu"`) rather than a
  bespoke dropdown, so a 40-version artifact collapses to one pill instead of
  overflowing the toolbar.

  Validated as a user on an isolated dev home (port 10733, never the owner's :10000):
  seeded a 3-version widget, opened Compare, confirmed v2↔v3 renders with per-token
  highlighting on the changed digits, switched to v1↔v3 (2 lines vs 3, added line
  visible), exercised the swap (selections AND pane contents both flipped), and
  confirmed the "same version on both sides" hint appears when they match. Gate:
  `make lint` green · `make test` **8888 passed** · web typecheck + 283 vitest + build
  green.

  **NOT done: T3.2** (the split-view iterate panel). It is blocked on a real gap rather
  than effort: `ChatEmbed` is a bare iframe in a separate document with **no
  postMessage bridge**, and there is **no `artifact_*` WS event at all**, so the host
  page cannot learn from the embed that `artifact_update` landed a new version. The
  cheapest correct trigger is a host-side `useChatSocket` filtered on `tool_call` where
  `tool === 'artifact_update'`; adding a new WS event would be shared-contract scope
  creep (INTEGRATION-ARCHITECTURE territory). Recorded as the stop point.
