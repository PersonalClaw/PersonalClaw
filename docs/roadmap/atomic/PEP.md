# PRODUCT-EXPERIENCE-PARITY — atomic plans

**Source plan:** [`PRODUCT-EXPERIENCE-PARITY`](../plans/PRODUCT-EXPERIENCE-PARITY.md)  
**Code:** `PEP`  
**Source status:** todo

Product-experience improvements to PersonalClaw's own surfaces: progressive-disclosure empty states that seed (never replace) the expert create flows, an always-open App Store category/source rail with polished cards, onboarding import from other local agent tools, artifact folders, local artifact deploy served through PersonalClaw's own gateway, artifacts as an indexed knowledge source, an always-on-conventions viewer, and a first-party product-app suite. Every simplification is progressive disclosure with full power one click away, and artifact deploy is strictly local through the PersonalClaw gateway with no cloud provisioner.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `PEP-1` | ⬜ | PresetEmptyState primitive + Triggers/Schedule preset on-ramp | — | On a fresh dev home the Triggers empty state shows preset cards; clicking e.g. 'Morning briefing' opens the create flow pre-filled to a working schedule trigger; the expert blank-create path still works unchanged; keyboard/focus a11y verified. |
| `PEP-2` | ⬜ | Cross-surface preset empty-state sweep | `PEP-1` | No list surface presents a bare form with no on-ramp; each empty surface deep-links into its existing create flow; expert paths unchanged; validation recorded with screenshots. |
| `PEP-3` | ⬜ | App Store persistent category/source rail + card polish | `EXT:APP-PLATFORM-EVOLUTION:quality-manifest-block` | Wide viewport shows the rail persistently and narrow falls back to the dropdown; selecting a category/source filters the grid and survives reload via the URL; cards render art-forward with and without hero art; rail is keyboard-navigable with aria-pressed category buttons. |
| `PEP-4` | ✅ | Onboarding import engine (scanners + writers) | — | A fixture ~/.claude yields instruction+mcp+skills items with secrets counted-and-skipped and re-scan idempotent; importing the fixture creates the memories, MCP entries, and skills/imported/claude_code/*, and a conflicting item reports 'conflict' rather than silently overwriting. |
| `PEP-5` | ⬜ | Onboarding import step UI | `PEP-4`, `EXT:ONBOARDING-UX:step-stack-primitive` | Fresh home with a fixture source shows the step; import completes without any secret appearing; re-entry shows already-imported items as 'existing'; skip path works; validation recorded. |
| `PEP-6` | ⬜ | Artifact folders | — | Folders CRUD; filing is metadata-only (no updated_at bump); renaming a folder leaves artifact records untouched; deleting a folder falls its members back to unfiled; membership persists across reload; nested folders validated. |
| `PEP-7` | ⬜ | Artifacts as an indexed knowledge source | — | Saving a markdown artifact makes it searchable in Knowledge without appearing in the Knowledge list; editing refreshes and deleting removes it from the index; enabling on a home with existing artifacts backfills exactly once and reboot doesn't re-run; a credential in an artifact is redacted before indexing; config round-trips. |
| `PEP-8` | ⬜ | Local static artifact deploy (webapp kind + serve route) | `PEP-6` | An html widget artifact renders at /artifacts/serve/<slug>/ and can be opened and interacted with in-app; a traversal attempt is refused; the served page cannot call /api (CSP fence validated explicitly); teardown removes the route. |
| `PEP-9` | ⬜ | React artifact build path | `PEP-8`, `EXT:EXECUTION-ISOLATION:resource-limited-build-spawn` | A small React artifact builds and serves as static files through the deploy route and is interactable in-app; a build failure is legible, not a hang. |
| `PEP-10` | ⬜ | Always-on conventions viewer + first domain-craft skills | — | The viewer matches what a session actually receives (spot-checked against an assembled prompt) and editing a project instruction round-trips safely; the three new skills load and surface when relevant, validated in a real session. |
| `PEP-11` | ⬜ | First-party product-app suite program | `EXT:ECOSYSTEM-TOOLING:exemplar-scaffold` | Each app ships as its own validated PR, is listed in the Store, and is recorded as a platform exemplar; the suite is delivered app-by-app in leverage order with reuse (docs ride document-handling, spec builder rides the workflow engine, meetings extends minutes) rather than rebuilt backends. |

## Atom scopes

### `PEP-1` — PresetEmptyState primitive + Triggers/Schedule preset on-ramp

**Status:** todo

Build a reusable PresetEmptyState + PresetCard primitive (icon, title, cadence/summary line, description, onPick(prefill)) in the shared UI with keyboard and focus-visible a11y. Apply it to the Triggers/Schedule surface: a data-driven preset catalog (cadence derived from the locale-format seam, not frozen en-US copy), empty-state cards that deep-link into the existing TriggerCreatePage/ScheduleForm with a prefill payload, and grouping of the lifecycle-event combobox (live events first, dormant ones collapsed under 'advanced'). Presets only seed the existing form; the blank expert create path is left unchanged.

**Done when:** On a fresh dev home the Triggers empty state shows preset cards; clicking e.g. 'Morning briefing' opens the create flow pre-filled to a working schedule trigger; the expert blank-create path still works unchanged; keyboard/focus a11y verified.

### `PEP-2` — Cross-surface preset empty-state sweep

**Status:** todo

Reuse the PresetEmptyState primitive across the remaining list surfaces: Workflows and Tasks (preset source = the bundled workflow/task templates surfaced as cards, no new copy that drifts from the templates), plus lighter-touch example cards on the Knowledge and Agents/Tools/Skills empty states. Validate as a newcomer by walking every list surface's empty state and confirming each offers a guided on-ramp while the expert blank-create path still works unchanged.

**Done when:** No list surface presents a bare form with no on-ramp; each empty surface deep-links into its existing create flow; expert paths unchanged; validation recorded with screenshots.

### `PEP-3` — App Store persistent category/source rail + card polish

**Status:** todo

Add a StoreSideRail with a CATEGORIES block (canonical categories derived from installed+catalog tags, live counts, select-to-filter, 'All' resets) and a SOURCES block (Built-in badge + each registered source with app count + Add-source into the existing sources flow), reusing the existing filter state; the current dropdown FilterMenu/SourcesPopover become the narrow-screen fallback. The rail is always open on wide screens and collapses on narrow, with category/source selection deep-linked in the URL (hash-router). Polish app cards to an art-forward shape: hero-image column with a deterministic gradient+icon fallback, name / 2-line-clamp description / category / action; render the quality/permission badge from the app-platform quality manifest block rather than inventing a second badge. No hardcoded colors (token-lint passes).

**Done when:** Wide viewport shows the rail persistently and narrow falls back to the dropdown; selecting a category/source filters the grid and survives reload via the URL; cards render art-forward with and without hero art; rail is keyboard-navigable with aria-pressed category buttons.

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

**Status:** todo

Add an ArtifactFolderStore (flat JSON, opaque 12-char-hex id, parent_id nesting, order, icon) mirroring the existing chat-folder store, plus an Artifact.folder_id field (tolerant-loaded, default '' = library root) and a set_folder metadata-only mutation that does not bump updated_at. Support list/query by folder with the present-vs-absent distinction (None=all, ''=unfiled, id=that folder) and routes (GET /api/artifacts?folder=<id>). Artifacts library UI: folder tree in the side rail, drag-to-file, create/rename/delete, and an unfiled bucket.

**Done when:** Folders CRUD; filing is metadata-only (no updated_at bump); renaming a folder leaves artifact records untouched; deleting a folder falls its members back to unfiled; membership persists across reload; nested folders validated.

### `PEP-7` — Artifacts as an indexed knowledge source

**Status:** todo

Extend the existing knowledge source framework so content-bearing artifacts are mirrored into the Knowledge Library without being listed as knowledge items. Add one aggregate artifact:// source row (source_type 'artifact') with per-artifact item grouping (an artifact's items replaced on edit / removed on delete without touching the rest) and a knowledge.auto_ingest_artifacts config (default on) fully round-tripped (dataclass+_meta, load, to_dict, write path). Emit change events from the artifact store and add a single in-process change-listener -> ingest/replace on upsert, remove on delete, routing artifacts through the existing FileReader path via a kind->extension map (html->prose extraction, md/text/json->text; widget/svg excluded) with redaction on the way in. First-enable backfill tied to source-row creation (idempotent). Artifacts surface only in search results with a provenance badge, never as knowledge items.

**Done when:** Saving a markdown artifact makes it searchable in Knowledge without appearing in the Knowledge list; editing refreshes and deleting removes it from the index; enabling on a home with existing artifacts backfills exactly once and reboot doesn't re-run; a credential in an artifact is redacted before indexing; config round-trips.

### `PEP-8` — Local static artifact deploy (webapp kind + serve route)

**Status:** todo

Add a webapp artifact kind (a multi-file artifact whose entry is index.html) with multi-file storage and deploy metadata (entry point, optional build command, stable slug), reusing the filed-set grouping from artifact folders. Add a gateway static-serve route GET /artifacts/serve/{slug}/{path:.*} that serves the artifact's files behind session auth and a path-traversal guard, with a strict CSP that fences the served page like a widget (no ambient access to the gateway /api). Artifacts UI: a Deploy/Open action opening the artifact at its stable in-gateway URL (new tab or embedded pane), a deployed-app listing with URL, and teardown that removes the route. Local-only: public exposure is explicitly out of scope (deferred to authenticated-exposure work); no cloud provisioner is built.

**Done when:** An html widget artifact renders at /artifacts/serve/<slug>/ and can be opened and interacted with in-app; a traversal attempt is refused; the served page cannot call /api (CSP fence validated explicitly); teardown removes the route.

### `PEP-9` — React artifact build path

**Status:** todo

Add a React build path for webapp artifacts: build once in a sandboxed, resource-limited spawn and store the emitted static bundle as the artifact's files (build-once-serve-static; no per-artifact dev server), served through the PEP-8 static route. A build failure surfaces a legible WHAT/WHY/FIX error rather than a hang. This is the unbounded-spawn hazard, so it must ride the shared resource-ceiling build-spawn profile and must not begin before that profile exists.

**Done when:** A small React artifact builds and serves as static files through the deploy route and is interactable in-app; a build failure is legible, not a hang.

### `PEP-10` — Always-on conventions viewer + first domain-craft skills

**Status:** todo

Add an 'Always-on' viewer to the Capabilities area that lists, with provenance (global vs project), every always:true skill and project-instruction doc currently injected into sessions, with inline read/edit; reuse the skills/instruction security discipline (symlink-leaf rejection, atomic write preserving mode bits, trust-base containment). Do not introduce a parallel always-on 'steering' store - the always:true-skills + project-instructions layer is the single always-on mechanism; this adds only the missing legibility surface. Author the first domain-craft bundled skills: web-verify/preview, document-authoring, and research-campaign, each with the frontmatter contract and a worked example.

**Done when:** The viewer matches what a session actually receives (spot-checked against an assembled prompt) and editing a project instruction round-trips safely; the three new skills load and surface when relevant, validated in a real session.

### `PEP-11` — First-party product-app suite program

**Status:** todo

Build a first-party product-app suite in PersonalClawApps as a phased program, one independently-shippable PR per app, in build-order by leverage: Code Review (each changed file in its own isolated subagent, weighted by blast radius, findings kept locally) -> Research Lab (multi-cycle unattended research: question -> sub-question tree -> agents -> synthesis) -> Design Critique (screenshot/flow/URL heuristic + a11y review via the vision + headless-render path) -> Docs/Slides (app fronts over the shipped document-handling seam, not new backends) -> Notes (git-backed markdown notebook editor, scoped as an editor not a second knowledge store) -> Issue Radar (GitHub/GitLab issue triage with suggested labels + local per-issue notes) -> Spec Builder (app front over the workflow engine, not a parallel planner) -> Ops (on-call responder; gated on autonomy guardrails + confirm-gated fixes; largest, later) -> Companion (opt-in desktop companion surface). Extend the existing minutes app rather than rebuilding a meetings app. Each app is built to the app-creation contract (app.json, SDK-only imports, minimum permissions, test_provider.py/test_server.py, README, LICENSE) and validated by adding it as a local Store source and driving it in the real UI. Each app finishes and ships before the next; do not batch.

**Done when:** Each app ships as its own validated PR, is listed in the Store, and is recorded as a platform exemplar; the suite is delivered app-by-app in leverage order with reuse (docs ride document-handling, spec builder rides the workflow engine, meetings extends minutes) rather than rebuilt backends.

