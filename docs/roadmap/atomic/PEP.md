# PRODUCT-EXPERIENCE-PARITY — atomic plans

**Source plan:** [`PRODUCT-EXPERIENCE-PARITY`](../plans/PRODUCT-EXPERIENCE-PARITY.md)  
**Code:** `PEP`  
**Source status:** todo

Product-experience improvements to PersonalClaw's own surfaces: progressive-disclosure empty states that seed (never replace) the expert create flows, an always-open App Store category/source rail with polished cards, onboarding import from other local agent tools, artifact folders, local artifact deploy served through PersonalClaw's own gateway, artifacts as an indexed knowledge source, an always-on-conventions viewer, and a first-party product-app suite. Every simplification is progressive disclosure with full power one click away, and artifact deploy is strictly local through the PersonalClaw gateway with no cloud provisioner.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `PEP-1` | ✅ | PresetEmptyState primitive + Triggers/Schedule preset on-ramp | — | On a fresh dev home the Triggers empty state shows preset cards; clicking e.g. 'Morning briefing' opens the create flow pre-filled to a working schedule trigger; the expert blank-create path still works unchanged; keyboard/focus a11y verified. |
| `PEP-2` | ✅ | Cross-surface preset empty-state sweep | `PEP-1` | No list surface presents a bare form with no on-ramp; each empty surface deep-links into its existing create flow; expert paths unchanged; validation recorded with screenshots. |
| `PEP-3` | ✅ | App Store persistent category/source rail + card polish | `EXT:APP-PLATFORM-EVOLUTION:quality-manifest-block` | Wide viewport shows the rail persistently and narrow falls back to the dropdown; selecting a category/source filters the grid and survives reload via the URL; cards render art-forward with and without hero art; rail is keyboard-navigable with aria-pressed category buttons. |
| `PEP-4` | ✅ | Onboarding import engine (scanners + writers) | — | A fixture ~/.claude yields instruction+mcp+skills items with secrets counted-and-skipped and re-scan idempotent; importing the fixture creates the memories, MCP entries, and skills/imported/claude_code/*, and a conflicting item reports 'conflict' rather than silently overwriting. |
| `PEP-5` | ⬜ | Onboarding import step UI | `PEP-4`, `EXT:ONBOARDING-UX:step-stack-primitive` | Fresh home with a fixture source shows the step; import completes without any secret appearing; re-entry shows already-imported items as 'existing'; skip path works; validation recorded. |
| `PEP-6` | ✅ | Artifact folders | — | Folders CRUD; filing is metadata-only (no updated_at bump); renaming a folder leaves artifact records untouched; deleting a folder falls its members back to unfiled; membership persists across reload; nested folders validated. |
| `PEP-7` | ✅ | Artifacts as an indexed knowledge source | — | Saving a markdown artifact makes it searchable in Knowledge without appearing in the Knowledge list; editing refreshes and deleting removes it from the index; enabling on a home with existing artifacts backfills exactly once and reboot doesn't re-run; a credential in an artifact is redacted before indexing; config round-trips. |
| `PEP-8` | ✅ | Local static artifact deploy (webapp kind + serve route) | `PEP-6` | An html widget artifact renders at /artifacts/serve/<slug>/ and can be opened and interacted with in-app; a traversal attempt is refused; the served page cannot call /api (CSP fence validated explicitly); teardown removes the route. |
| `PEP-9` | ⬜ | React artifact build path | `PEP-8`, `EXT:EXECUTION-ISOLATION:resource-limited-build-spawn` | A small React artifact builds and serves as static files through the deploy route and is interactable in-app; a build failure is legible, not a hang. |
| `PEP-10` | ✅ | Always-on conventions viewer + first domain-craft skills | — | The viewer matches what a session actually receives (spot-checked against an assembled prompt) and editing a project instruction round-trips safely; the three new skills load and surface when relevant, validated in a real session. |
| `PEP-11` | ⬜ | First-party product-app suite program | `EXT:ECOSYSTEM-TOOLING:exemplar-scaffold` | Each app ships as its own validated PR, is listed in the Store, and is recorded as a platform exemplar; the suite is delivered app-by-app in leverage order with reuse (docs ride document-handling, spec builder rides the workflow engine, meetings extends minutes) rather than rebuilt backends. |

## Atom scopes

### `PEP-1` — PresetEmptyState primitive + Triggers/Schedule preset on-ramp

**Status:** done

Build a reusable PresetEmptyState + PresetCard primitive (icon, title, cadence/summary line, description, onPick(prefill)) in the shared UI with keyboard and focus-visible a11y. Apply it to the Triggers/Schedule surface: a data-driven preset catalog (cadence derived from the locale-format seam, not frozen en-US copy), empty-state cards that deep-link into the existing TriggerCreatePage/ScheduleForm with a prefill payload, and grouping of the lifecycle-event combobox (live events first, dormant ones collapsed under 'advanced'). Presets only seed the existing form; the blank expert create path is left unchanged.

**Done when:** On a fresh dev home the Triggers empty state shows preset cards; clicking e.g. 'Morning briefing' opens the create flow pre-filled to a working schedule trigger; the expert blank-create path still works unchanged; keyboard/focus a11y verified.

**DONE.** `web/src/ui/PresetEmptyState.tsx` is the primitive: `PresetCard` (icon, title,
cadence/summary line, description, `onPick(prefill)`) composing **`TileButton`** for its chrome and
button semantics — so a preset card inherits the kit's card look and its `focus-visible` ring rather
than growing a second one — plus `PresetEmptyState` (headline, hint, responsive 1/2-column grid, and
a `footer` slot for the expert blank path). `prefill` is a type parameter the primitive never reads,
which is how each surface keeps its own `Prefill` shape. `PresetEmptyState.doc.ts` documents both.

The Triggers catalog is `pages/triggers/triggerPresets.ts`: four presets (Morning briefing, Weekly
digest, Nightly check, Standup reminder) built by one `preset()` factory so **the id, the title and
the cadence are each declared once and used twice** — the id as the catalog key AND the `?preset=`
payload, the title as the card heading AND the trigger's name, the cadence as the card's summary
line AND the saved cron. A card can therefore never advertise a cadence the saved trigger does not
have. Cadence is a structured `Cadence` union and its label goes through the **locale seam**
(`toLocaleTimeString`/`toLocaleDateString`, no explicit locale) rather than frozen copy: measured
`en-US` "Every day · 8:00 AM" vs `de-DE` "Every day · 8:00" (no meridiem), and Monday/Montag/月曜日.

The seed rides in the URL — `#/triggers/new?kind=schedule&preset=<id>` — like `kind`/`pattern`
already do, so a seeded flow is deep-linkable, back/forward-safe and survives a reload.
`TriggerCreatePage` seeds the name and the cadence as lazy `useState` initializers and the ACTION in
an effect, because the action's config defaults come from the provider's FETCHED `settingsSchema`;
`seedActionConfig` first, then the preset's values, so `notify`'s `kind: 'info'` default survives the
merge. `findTriggerPreset('')` and an unknown id both return `null`, which is exactly what leaves the
expert blank path byte-for-byte what it was.

**Driven on a fresh dev home (port 10021, its own `.dev-home`), not just tested:** the empty state
renders all four cards; they are tab stops **28–31** with a real focus ring (a **5-layer** box-shadow,
inset 2px, `:focus-visible` matching) and **Enter activates**; the seeded form arrived with name
`Morning briefing`, cron `0 8 * * *` and the task prompt filled, plus a line saying it was filled in
from a preset; Create saved `schedule:clock:morning-briefing` with **`next_run` set**, and
`POST /api/triggers/<id>/run` returned `{"ok": true, "result": "ran"}` — as did the second preset
(`Standup reminder`, `45 9 * * 1-5`, `notify`, whose action needs no model). `#/triggers/new` with no
preset still opens empty with Create `aria-disabled="true"` and the action picker unset. One column at
420px, two at 1440px. Zero console errors, zero failed requests.

**Two DEVIATIONS, both measured rather than chosen.** (1) The scope asks for the lifecycle combobox's
dormant events "collapsed under 'advanced'". `Combobox` has no collapsible group, and building one
would be a new mechanism on a shared primitive; live-first ordering plus a heading that names the dead
half is how the kit expresses it (`lifecycleEventOptions` in `triggerMeta.ts`). (2) More importantly,
the plan's premise for that half is **stale**: it describes "~15 events, 7 of which warn 'never
fires'", and `GET /api/triggers/variables` on a current build returns **15 events, 0 dormant**. An
unconditional heading would label all fifteen "Live events" and separate nothing, so the headings are
emitted only when something IS dormant — the picker is unchanged today and both groups appear the
moment one appears. Both branches are asserted in `lifecycleEventOptions.test.ts`, so the rail is not
vacuous even though its dormant half currently matches nothing live.

**DISCOVERY for `PEP-2`:** a fresh dev home is **not** trigger-empty. `reconcile_digest_cron`
registers `system:notification-digest` at boot, so a newcomer's first Triggers visit shows one
machine-named system row and — because the empty state is gated on `counts.all === 0` — no on-ramp at
all. That is arguably worse than the empty case this atom fixes. Left alone deliberately: gating the
empty state on "no USER triggers" would render a list row and an empty state simultaneously, which is
incoherent. It wants an owner call on whether system-created triggers belong in that list at all.


### `PEP-2` — Cross-surface preset empty-state sweep

**Status:** done

Reuse the PresetEmptyState primitive across the remaining list surfaces: Workflows and Tasks (preset source = the bundled workflow/task templates surfaced as cards, no new copy that drifts from the templates), plus lighter-touch example cards on the Knowledge and Agents/Tools/Skills empty states. Validate as a newcomer by walking every list surface's empty state and confirming each offers a guided on-ramp while the expert blank-create path still works unchanged.

**Done when:** No list surface presents a bare form with no on-ramp; each empty surface deep-links into its existing create flow; expert paths unchanged; validation recorded with screenshots.

### `PEP-3` — App Store persistent category/source rail + card polish

**Status:** done

Add a StoreSideRail with a CATEGORIES block (canonical categories derived from installed+catalog tags, live counts, select-to-filter, 'All' resets) and a SOURCES block (Built-in badge + each registered source with app count + Add-source into the existing sources flow), reusing the existing filter state; the current dropdown FilterMenu/SourcesPopover become the narrow-screen fallback. The rail is always open on wide screens and collapses on narrow, with category/source selection deep-linked in the URL (hash-router). Polish app cards to an art-forward shape: hero-image column with a deterministic gradient+icon fallback, name / 2-line-clamp description / category / action; render the quality/permission badge from the app-platform quality manifest block rather than inventing a second badge. No hardcoded colors (token-lint passes).

**Done when:** Wide viewport shows the rail persistently and narrow falls back to the dropdown; selecting a category/source filters the grid and survives reload via the URL; cards render art-forward with and without hero art; rail is keyboard-navigable with aria-pressed category buttons.

**DONE — all four `done_when` clauses MET.** `web/src/pages/apps/StoreSideRail.tsx` is the rail
(CATEGORIES + SOURCES, each block led by its reset entry with live counts), rendered by
`AppsSection.tsx` inside the `isStore` branch of `WorkbenchLayout`'s children — i.e. by
`#/apps?view=store`, the destination `app/App.tsx` routes `case 'apps'` to. The source dimension is a
new URL param `?ssrc=`, keyed on `sourceGroup().key` so the rail, the existing source dividers and the
grid cannot disagree about what "this source" means; the category dimension REUSES the existing
`?stag=`. `storeCategories`/`storeSources` is ONE derivation feeding both the rail and the dropdown,
and its counts are taken over `storeUniverse` — the exact set the grid can render.

**Card anatomy is now singular.** Every card is banner-topped: the app's own `heroUrl` when it declares
one, otherwise the deterministic gradient from `appArt.ts` (FNV-1a over the app NAME → an angle plus a
guaranteed-distinct pair of scheme tokens, composed with `color-mix`, so it is stable across reloads and
correct in all twelve schemes with zero literal colors). The banner carries `data-art="hero" |
"generated"` so a test can tell the two paths apart without parsing a background string. The icon tile
became unconditional as well, since `AppIcon` already resolves an absent/legacy icon to the Blocks
glyph — that deletes the old four-shape card (hero+icon / hero-only / icon-only / neither), where the
hero-less card read as an image that had failed to load.

**17 tests in `web/src/pages/apps/storeRail.test.tsx`, every one driving `AppsSection`** — nothing
mounts the rail directly, because a rail with its own green isolation suite is the inert-control shape.
Falsified three ways: disabling the render line (`{false && isMobile &&`) turned **10 of 17** red;
deleting the `ssrc` grid filter turned the re-mounted source test red on the CARD CENSUS
(`['Ledger','Notes','Timer']` vs `['Notes','Timer']`); hardcoding `aria-pressed={false}` turned 4 red,
including the accessibility-tree query, which proves that query reads the tree and not a class.
URL-survival is proven by READING THE URL BACK from a `URLSearchParams`-backed fake router and
RE-MOUNTING a fresh component from only that string — not by asserting `setQuery` was called, which a
page that writes the URL and then reads its own `useState` would also pass.

**Three DEVIATIONS, each forced by a global ratchet or a measured premise.**
1. **No arrow-key cursor on the rail.** The first version had ArrowUp/Down/Home/End roving;
   `ui/popupItemRoles.test.tsx` failed it — a cursor over a mapped list of buttons with no container
   role. The two escapes were to declare `role="listbox"` (which forces `aria-selected` on options and
   would have DROPPED the `aria-pressed` this atom's `done_when` names) or to drop the cursor. Dropped
   the cursor: an arrow-driven list is a different APG pattern implying a roving tabindex and ONE tab
   stop, and bolting arrows onto independently-tabbable toggle buttons is neither pattern. Keyboard
   navigability is now Tab + Enter/Space over native buttons, driven end-to-end by a test that tabs
   through all seven entries in DOM order.
2. **The row became a shared primitive, `ui/FilterRow.tsx`** (+ its required `FilterRow.doc.ts`).
   `design/primitiveAdoption` counts a page-level raw button as new bespoke chrome and may only
   shrink, and the rail row was a deliberate copy of `ui/FilterMenu`'s private `Row` anyway. Extracting
   it — and pointing FilterMenu at it — makes "one control at two viewport widths" structural instead
   of a resemblance someone has to maintain, and keeps the ratchet at its measured 265 rather than
   asking it for slack. `FilterMenu`'s rendered output is unchanged (`pressed` is opt-in; the dropdown
   leaves it undefined because its trailing check already announces the choice).
3. **Categories are derived from `storeUniverse`, not "installed+catalog"** as the scope sentence says.
   The Store deliberately EXCLUDES installed apps (they live in the Library, a 2026-07-05 decision
   recorded in `AppsSection.tsx`), so a category counted over installed apps would advertise a filter
   whose grid comes back empty — a count maintained beside a table it does not describe. Labels are
   humanised by `categoryLabel()` (`dev-tools` → `Dev tools`) because a raw author-controlled slug in a
   rail heading reads as a leaked identifier; the KEY stays the raw tag, so the URL is unchanged.

The scope's "render the quality/permission badge from the quality manifest block rather than inventing a
second badge" was already satisfied before this atom — `QualityBadges` (APE-4) is on the card and was
left exactly as it was.

**Gates:** `make lint` clean (black 2065 files, isort, flake8, mypy 1014 files) · `npm run typecheck:web`
clean · `npm run test:web` **488 files / 5197 tests, all green** (the full suite, not path-scoped — three
of the four findings above came ONLY from global ratchets: `popupItemRoles`, `primitiveAdoption`,
`uiDocs.drift`) · `npm run build` clean · `scripts/gate_report.py` **6/6 PASS** · probe sweep 16 total,
0 diff-introduced.

**Full `make test`: 26656 passed, 1 failed — PRE-EXISTING and NOT this diff.**
`tests/test_lv5_refinement_arm.py::test_v3_arc_flawed_skill_stumble_refine_approve_rerun` asserts the
literal `"## Refinement v1 (2026-08-25, from a correction)"`, while `skills/overlays.py:206` derives that
stamp from a UTC `created_at`. Measured on this machine: local date 2026-08-25, UTC date 2026-08-26 — so
the hardcoded LOCAL authoring date and the UTC-derived stamp disagree for the ~7 hours a day after UTC
rolls over. Reproduced in isolation against a diff containing ZERO python files. Landed by `c5d4762c`
(LV-5 S3); left alone because that area belongs to another agent this session.

### `PEP-4` — Onboarding import engine (scanners + writers)

**Status:** done

New onboarding/import package. Define ScanResult/ImportItem/WriteOutcome types and a source registry, then implement pure, fixture-testable scanners for the two highest-value sources: Claude Code (~/.claude: instruction docs, .mcp.json, settings.json, skills/) and Codex (~/.codex: AGENTS.md, config), with env-var-then-default root resolution (additional sources are additive later, not a v1 bar). Implement per-category writers to PersonalClaw destinations (instructions -> instruction docs, memories -> memory store, mcp_servers -> MCP config, skills -> skills/imported/<source>/ via the install-scan, schedules -> triggers, settings -> a review-gated merge that never clobbers) with a four-value outcome vocabulary (imported/existing/conflict/rejected). Enforce security floors on every imported file: is_sensitive_path refusal + credential/exfiltration-URL redaction, secrets counted-and-skipped and never imported; fingerprint-idempotent (SHA over source\0category\0key) so re-import never duplicates.

**Done when:** A fixture ~/.claude yields instruction+mcp+skills items with secrets counted-and-skipped and re-scan idempotent; importing the fixture creates the memories, MCP entries, and skills/imported/claude_code/*, and a conflicting item reports 'conflict' rather than silently overwriting.

**DONE.** `src/personalclaw/onboarding_import/` (the package name the plan writes as
`onboarding/import/` — `import` is a keyword and `onboarding.py` is a module, so the
package is `onboarding_import`): `model.py` (ScanResult/ImportItem/WriteOutcome/WriteResult +
the `sha256(source\0category\0key)` fingerprint), `floors.py` (the three security floors),
`sources/claude_code.py` + `sources/codex.py` (pure, env-var-then-default roots),
`registry.py`, `writers.py` (per-category destinations, exhaustive dispatch), `engine.py`
(scan → select → import). 19 tests in `tests/test_onboarding_import.py`, including a
planted secret asserted absent from every scan output AND from every byte written under the
home, count-based idempotence, and a no-clobber conflict test per collidable destination.
Two DEVIATIONS: (1) `schedules` and `workspaces` are NOT declared — neither v1 source emits
them, and a declared category with no producer is a dead kind; (2) `instructions` land in the
memory store (there is no separate instruction-doc store in the code) and `settings` stage to
a review queue under `onboarding/staged/`, never into live config. Onboarding step UI +
`/api/onboarding/import` remain `PEP-5`.

### `PEP-5` — Onboarding import step UI

**Status:** todo

Add an onboarding step that surfaces detected sources ('We found Claude Code / Codex on this machine - import?') with per-category checkboxes, counts, and a conflict review; skippable and idempotent on re-entry (already-imported items shown as 'existing'). Wire GET/POST /api/onboarding/import. Validate against a fake source seeded under the dev home: run onboarding, import, confirm memories/MCP/skills landed and a planted secret did not, and confirm the skip path and re-entry idempotence.

**Done when:** Fresh home with a fixture source shows the step; import completes without any secret appearing; re-entry shows already-imported items as 'existing'; skip path works; validation recorded.

### `PEP-6` — Artifact folders

**Status:** done

Add an ArtifactFolderStore (flat JSON, opaque 12-char-hex id, parent_id nesting, order, icon) mirroring the existing chat-folder store, plus an Artifact.folder_id field (tolerant-loaded, default '' = library root) and a set_folder metadata-only mutation that does not bump updated_at. Support list/query by folder with the present-vs-absent distinction (None=all, ''=unfiled, id=that folder) and routes (GET /api/artifacts?folder=<id>). Artifacts library UI: folder tree in the side rail, drag-to-file, create/rename/delete, and an unfiled bucket.

**Done when:** Folders CRUD; filing is metadata-only (no updated_at bump); renaming a folder leaves artifact records untouched; deleting a folder falls its members back to unfiled; membership persists across reload; nested folders validated.

**DONE.** `artifacts/folders.py` owns the tree: `ArtifactFolder` + `ArtifactFolderStore` (flat JSON at `<home>/artifacts/folders.json`, inside the existing `artifacts` durability entry; 12-char-hex ids; `parent_id`/`order`/`icon`), with `delete_folder()` as the ONE deletion door so a folder can never be dropped while members still point at it. `Artifact.folder_id` is tolerant-loaded (`""` = unfiled) and `NativeArtifactProvider.set_folder()` writes it WITHOUT touching `updated_at`, `version`, the body or the event log — filing is organization, and a bump would reorder every recency-sorted view. `list(folder=...)` is present-vs-absent (`None` all / `""` unfiled / id that folder), exposed as `GET /api/artifacts?folder=`; folder CRUD is `GET|POST /api/artifacts/folders` + `PATCH|DELETE /api/artifacts/folders/{id}`, and filing is its own `PATCH /api/artifacts/{slug}/folder` route (the generic PATCH goes through `update`, which bumps `updated_at`). Nesting refuses a missing parent, a self-parent and a descendant-parent before writing anything. 24 tests in `tests/test_artifact_folders.py`. **Deferred:** the artifacts-library side-rail folder tree with drag-to-file — the scope's UI sentence is a new frontend surface (today's library is a toolbar-driven grid with no rail) and is not part of this atom's `done_when`; the API is complete and ready for it.

### `PEP-7` — Artifacts as an indexed knowledge source

**Status:** done

Extend the existing knowledge source framework so content-bearing artifacts are mirrored into the Knowledge Library without being listed as knowledge items. Add one aggregate artifact:// source row (source_type 'artifact') with per-artifact item grouping (an artifact's items replaced on edit / removed on delete without touching the rest) and a knowledge.auto_ingest_artifacts config (default on) fully round-tripped (dataclass+_meta, load, to_dict, write path). Emit change events from the artifact store and add a single in-process change-listener -> ingest/replace on upsert, remove on delete, routing artifacts through the existing FileReader path via a kind->extension map (html->prose extraction, md/text/json->text; widget/svg excluded) with redaction on the way in. First-enable backfill tied to source-row creation (idempotent). Artifacts surface only in search results with a provenance badge, never as knowledge items.

**Done when:** Saving a markdown artifact makes it searchable in Knowledge without appearing in the Knowledge list; editing refreshes and deleting removes it from the index; enabling on a home with existing artifacts backfills exactly once and reboot doesn't re-run; a credential in an artifact is redacted before indexing; config round-trips.

**DONE.** `artifacts/changes.py` is the observer seam — a two-word vocabulary (`upsert`/`delete`)
emitted by `NativeArtifactProvider` from OUTSIDE its lock, so every writer (HTTP handler, MCP tool,
chat tool) is covered by one subscription and no writer serializes behind someone else's indexing.
Artifacts deliberately do NOT emit the knowledge library's three-word `created`/`modified`/`deleted`
vocabulary: choosing between create and modify needs to know whether the MIRROR exists, which is
knowledge-side state, and a wrong guess either duplicates a row or drops an edit. `set_folder` emits
nothing — filing is organization (PEP-6's no-bump contract), so dragging ten artifacts costs zero
re-indexing.

`knowledge/artifact_ingest.py` joins the WatchedSource mechanism rather than paralleling it: ONE
`sources` row (`provider='artifacts'`, `kind='artifact'`, `spec.uri='artifact://'`), per-artifact
identity as the store's own `(source_id, guid=slug)` pair so `find_source_item` makes "replace this
artifact's mirror, touch nothing else" a single-row lookup, and `ingest_queue.enqueue` as the only
writer — never a hand-written `items_fts` row, which is the failure that looks perfectly present in
`items` and is invisible to every search. HTML rides the shared reader conversion, extracted from
`FileReader._read_html` into `readers.html_to_prose` (one primitive, so an `html` artifact and an
uploaded `.html` reduce to the same prose); dispatch is by KIND, not by path, because every text
artifact is stored on disk as `current.html` whatever it is. `INDEXABLE_KINDS` is a closed
allowlist (html/markdown/text/json/csv/document) — the fail-closed direction, so a kind added later
is not silently indexed, and `widget`/`react`/`svg`/`infographic` are excluded because indexing
program text makes every search for a variable name outrank the user's notes.

**Four decisions worth naming.** (1) **Enrichment is `raw`, not `full`** — the mirror is automatic
and default-on, so `full` would spend one model call per artifact the first time a gateway starts on
an existing home; `raw` routes every mirror through the LLM-free `FeedItemGraph`, and the Sources UI
already reads that field back as a "no AI" chip. (2) **A delete FORGETS the sighting** where a
watched directory archives it — new store primitive `forget_source_item(source_id, guid)`, one
transaction over the item cascade AND its `source_seen` row. Archiving would leave an orphan nothing
can revive (we own this upstream), and leaving the seen row would make `create_typed_item`'s novelty
gate refuse a re-created slug **forever, silently** — asserted end-to-end by
`test_a_recreated_slug_indexes_again`. Removal deliberately ignores the master switch: disabling the
mirror must not disable deletion. (3) **Idempotence is a content hash**
(`file_metadata['artifact_sha']` over title + indexed text), not a timestamp, so a re-run backfill
and a no-op PATCH write nothing and enqueue nothing; the title is in the hash because a rename must
refresh what a search result shows. (4) **Not listed** is one predicate on one column: the mirror's
`item_type='artifact'` sits OUTSIDE the twelve authorable knowledge types, so the create API can
never author one and `list_items`' no-query branch excludes it while the search branch does not —
an explicit `?type=artifact` still answers, because a filter that silently returns nothing is worse
than one that does.

The switch is `knowledge.auto_ingest_artifacts` (default on), wired through all five points
(dataclass + `_meta`, `load()`, `to_dict()` via `asdict`, the `_EDITABLE_CONFIG` PATCH allowlist, and
a **ToggleRow in Settings → Sources**) plus `config-baseline.json`. It is read PER EVENT, and
`start()` subscribes unconditionally — subscribing only when it was on at boot would make turning it
on a setting that quietly needs a restart. With it off no source row exists, so a later turn-on is
still a FIRST enable and still backfills exactly once; the row's existence IS the idempotency marker,
so there is no second "did the backfill run" flag to fall out of step with it.

**Two UI honesty fixes were mandatory, not polish.** The Sources row describes a POLLER, so the new
row read *"No provider · never polled · every 1h"* — a DANGER chip telling the user a working
mechanism is broken. `_serialize_source` now ships `event_driven`, and the row suppresses the
poll-shaped verdicts, states *"indexed as artifacts change · turn off in Settings → Sources"*, and
**renders no pause toggle at all**: the row's `enabled` column is not what the mirror reads, so that
switch would have saved successfully and changed nothing. `enrolled` stays honestly `false` (nothing
IS enrolled to poll it) rather than being faked, which would hide a genuinely orphaned row of some
future kind. Second: `resolveType` fell through its cascade to `note`, so an artifact search hit
rendered a StickyNote labelled "Note"; `ARTIFACT_TYPE` is its own meta kept OUT of `TYPES` (the
create picker's catalog), the redundant lowercase `artifacts` provider pill is suppressed where the
type label already says "Artifact", and the hit carries an **"Open artifact"** link — a mirror is a
search surface, and a hit that could only ever show extracted text would be a dead end.

31 backend tests (`tests/test_artifact_knowledge_source.py`) + 9 frontend
(`web/src/pages/knowledge/artifactMirror.test.tsx`). Every claim is an outcome: "searchable" is
asserted by SEARCHING, "not listed" against the real `list_items` handler, "backfills once" by
driving the startup path TWICE and comparing counts *and* `updated_at`, redaction from both
directions (the secret is unfindable by search AND absent from every stored column). Two vacuity
floors: every allowlisted kind is exercised, and the "No provider" chip is asserted to still appear
for a genuinely orphaned poller. **Left out deliberately:** `get_stats()['items']` still counts
mirrors — it is a store-wide `COUNT(*)` that already counts archived rows the list hides, so
artifacts inherit its existing meaning rather than introducing a new inconsistency.

### `PEP-8` — Local static artifact deploy (webapp kind + serve route)

**Status:** done

Add a webapp artifact kind (a multi-file artifact whose entry is index.html) with multi-file storage and deploy metadata (entry point, optional build command, stable slug), reusing the filed-set grouping from artifact folders. Add a gateway static-serve route GET /artifacts/serve/{slug}/{path:.*} that serves the artifact's files behind session auth and a path-traversal guard, with a strict CSP that fences the served page like a widget (no ambient access to the gateway /api). Artifacts UI: a Deploy/Open action opening the artifact at its stable in-gateway URL (new tab or embedded pane), a deployed-app listing with URL, and teardown that removes the route. Local-only: public exposure is explicitly out of scope (deferred to authenticated-exposure work); no cloud provisioner is built.

**Done when:** An html widget artifact renders at /artifacts/serve/<slug>/ and can be opened and interacted with in-app; a traversal attempt is refused; the served page cannot call /api (CSP fence validated explicitly); teardown removes the route.

**DONE.** `artifacts/deploy.py` owns the deploy registry (`<home>/artifacts/deployments.json`),
the containment spine and the CSP constant; `artifacts/handlers.py` serves
`GET /artifacts/serve/{slug}/{path:.*}` (registered with the artifact routes, so no
second registration site) plus `POST`/`DELETE /api/artifacts/{slug}/deploy` and
`GET /api/artifacts/deployed`. Containment is **asserted on resolved paths**, never
string-matched: marker rejection is only a first gate — with it neutered every traversal
test still passes (measured), and only removing the resolve-and-contain assertion plus the
symlink refusal lets a traversal through (8 reds). The fence is a response **header**
(`connect-src 'none'` + `default-src`/`form-action`/`base-uri`/`object-src 'none'`,
`frame-ancestors 'self'`), asserted by directive VALUE so weakening one reds the suite.
Teardown is registry removal — aiohttp freezes its router, so "removes the route" means the
handler serves nothing for an undeployed slug; deleting an artifact tears its deployment
down too. UI: `ArtifactDeploy` (Deploy / Preview pane / Open / Tear down) in the viewer and
`DeployedAppsMenu` (the deployed-app listing with URLs) in the library toolbar.
**Deferred, deliberately:** the multi-file *webapp kind* itself (`ALLOWED_KINDS` is
unchanged) and its build-command metadata — extra files are already served from
`<slug>/webapp/`, and the kind + build path is what `PEP-9` adds on top of this route.

### `PEP-9` — React artifact build path

**Status:** todo

Add a React build path for webapp artifacts: build once in a sandboxed, resource-limited spawn and store the emitted static bundle as the artifact's files (build-once-serve-static; no per-artifact dev server), served through the PEP-8 static route. A build failure surfaces a legible WHAT/WHY/FIX error rather than a hang. This is the unbounded-spawn hazard, so it must ride the shared resource-ceiling build-spawn profile and must not begin before that profile exists.

**Done when:** A small React artifact builds and serves as static files through the deploy route and is interactable in-app; a build failure is legible, not a hang.

### `PEP-10` — Always-on conventions viewer + first domain-craft skills

**Status:** done

Add an 'Always-on' viewer to the Capabilities area that lists, with provenance (global vs project), every always:true skill and project-instruction doc currently injected into sessions, with inline read/edit; reuse the skills/instruction security discipline (symlink-leaf rejection, atomic write preserving mode bits, trust-base containment). Do not introduce a parallel always-on 'steering' store - the always:true-skills + project-instructions layer is the single always-on mechanism; this adds only the missing legibility surface. Author the first domain-craft bundled skills: web-verify/preview, document-authoring, and research-campaign, each with the frontmatter contract and a worked example.

**Done when:** The viewer matches what a session actually receives (spot-checked against an assembled prompt) and editing a project instruction round-trips safely; the three new skills load and surface when relevant, validated in a real session.

### `PEP-11` — First-party product-app suite program

**Status:** todo

Build a first-party product-app suite in PersonalClawApps as a phased program, one independently-shippable PR per app, in build-order by leverage: Code Review (each changed file in its own isolated subagent, weighted by blast radius, findings kept locally) -> Research Lab (multi-cycle unattended research: question -> sub-question tree -> agents -> synthesis) -> Design Critique (screenshot/flow/URL heuristic + a11y review via the vision + headless-render path) -> Docs/Slides (app fronts over the shipped document-handling seam, not new backends) -> Notes (git-backed markdown notebook editor, scoped as an editor not a second knowledge store) -> Issue Radar (GitHub/GitLab issue triage with suggested labels + local per-issue notes) -> Spec Builder (app front over the workflow engine, not a parallel planner) -> Ops (on-call responder; gated on autonomy guardrails + confirm-gated fixes; largest, later) -> Companion (opt-in desktop companion surface). Extend the existing minutes app rather than rebuilding a meetings app. Each app is built to the app-creation contract (app.json, SDK-only imports, minimum permissions, test_provider.py/test_server.py, README, LICENSE) and validated by adding it as a local Store source and driving it in the real UI. Each app finishes and ships before the next; do not batch.

**Done when:** Each app ships as its own validated PR, is listed in the Store, and is recorded as a platform exemplar; the suite is delivered app-by-app in leverage order with reuse (docs ride document-handling, spec builder rides the workflow engine, meetings extends minutes) rather than rebuilt backends.

