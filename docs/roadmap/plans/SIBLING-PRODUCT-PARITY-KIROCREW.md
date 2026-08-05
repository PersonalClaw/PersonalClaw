# Plan: Sibling Product Parity (KiroCrew) — Experience Simplification, Artifacts-as-Apps, Onboarding Import & the First-Party App Suite

**Status:** DESIGNED — created 2026-08-05 from a code-verified study of Amazon's open-sourced
KiroCrew (a direct architectural sibling — see [[personalclaw-kirocrew-sibling]]). Every claim was
checked against **both** trees. This is the **product/UX** companion to
[SIBLING-HARDENING-KIROCREW](SIBLING-HARDENING-KIROCREW.md), which covered the enforcement/trust
findings from the same study.

**Created:** 2026-08-05
**Wave:** mixed — §1 (UX simplification) and §2 (App Store UI) are early legibility multipliers;
the rest are product surfaces (Wave 2/3). See the per-section wave tags.
**Depends on:** nothing hard for §1–§6. §7 (app suite) coordinates with ECOSYSTEM-TOOLING;
§9 re-homes to CHANNEL-EXPANSION; §10 to DISTRIBUTION; §11 to the new DESKTOP-COMPUTER-USE plan.

**Scope:** the owner's twelve-item inspiration list from KiroCrew, triaged into what PersonalClaw
should build, re-home, or has already covered. **Soul guardrail:** adopt *interaction patterns and
capability seams*, not vendor posture or cloud dependence. Two owner-specific deltas from KiroCrew,
held throughout: (a) **artifact deploy is LOCAL-first** — served through PersonalClaw's own gateway/
domain, not a bring-your-own-AWS provisioner (their `deploy/` is a whole EC2/CloudFront path; the
owner explicitly wants a local server the user reaches through the PersonalClaw UI); (b) every new
surface must **not degrade the power** — simplification is progressive disclosure, never removal.

---

## 0. Ownership map — read before touching any section

Twelve items studied. Several already have owners; this plan re-homes those rather than duplicating
(*one path per concern*). A row marked **re-home** means the task lives in the named plan and this
document only records the design input.

| # | Owner's item | Disposition | Where |
|---|---|---|---|
| 1 | Schedule UX cleaner + cross-surface simplification | **NEW** | §1 |
| 2 | App Store layout: right-rail categories+sources, always-open, card polish | **NEW** (+ coordinates APP-PLATFORM-EVOLUTION quality badges) | §2 |
| 3 | Onboarding import (detect + import other local agent tools) | **NEW** — no plan owns it; ONBOARDING-UX has no import row | §3 |
| 4 | Artifact folders | **NEW** (extends the artifact store) | §4 |
| 5 | Artifact deploy — **local** html/react served through PersonalClaw | **NEW** (extends artifact store + app-backend hosting) | §5 |
| 6 | Artifacts as a knowledge source (indexed, not listed) | **NEW** (extends KNOWLEDGE-LIBRARY's source framework) | §6 |
| 7 | First-party app suite (Code-Review, Design-Critique, Research-Lab, PPTX, Papyrus, Notes, Ops, Issue-Radar, Meetings, Companion, Spec) | **NEW** (coordinates ECOSYSTEM-TOOLING exemplars; several partially exist in PersonalClawApps) | §7 |
| 8 | Skills we're missing + the steering concept | **NEW** (small — mostly an assessment; steering is largely already covered) | §8 |
| 9 | Telegram/Discord/WeChat/Teams/Webex/WeCom + Slack improvements | **RE-HOME** → CHANNEL-EXPANSION (amendment: the shared TurnDriver + the added channels) | §9 |
| 10 | Build/distribution/update mechanisms | **RE-HOME** → DISTRIBUTION follow-on (reconcile with SIBLING-HARDENING §7.2, which already deferred release channels) | §10 |
| 11 | Computer use implementation | **NEW PLAN** → [DESKTOP-COMPUTER-USE](DESKTOP-COMPUTER-USE.md) (#69) — BROWSE-AUTOMATION is browser-only; nothing owns desktop GUI automation | §11 |
| — | (steering, assessed) | **MOSTLY COVERED** — `always: true` skills + `project_context.py` instructions already are the always-on layer; §8 adds only the missing *viewer/legibility* surface | §8 |

**Change class.** §4/§5/§6 change persisted shapes (artifact folder membership, a new artifact
kind, a new knowledge source type) — class-B, executed as clean breaks under the pre-1.0 banner. §1/
§2/§3/§7/§8 are class-R.

---

## 1. Progressive-disclosure UX — preset-first empty states (Wave 1)

### 1.1 What KiroCrew does better, precisely

Their `SchedulePage` opens on **four preset cards** (`schedulePresets.tsx` — Dependency Guardian,
Nightly Build Watch, etc.), each showing an icon, a title, a human cadence (`Weekly · Mondays
6:00am`), and a one-line description. Clicking one opens the standard create flow **pre-filled**; the
user reviews and saves like any other job. The create form itself (`JobForm.tsx`, 330 LOC) is a
single `SegmentedControl` — `interval | weekly | cron` — that reveals only the fields for the chosen
mode. A newcomer never sees a cron expression unless they pick "cron".

PersonalClaw's `TriggerCreatePage.tsx` is well-built but **front-loads the full model**: on open it
shows Name, a `Segmented` of trigger *kinds*, then for a lifecycle trigger a combobox of ~15 events
(7 of which warn "never fires") plus a glob matcher, then the full Action config. There is **no
preset/empty-state on-ramp** — a first-timer meets the whole ontology at once. That is the "daunting
at first glance" the owner named.

### 1.2 The pattern (reusable, not schedule-specific)

A shared `PresetEmptyState` primitive: when a list surface is empty (or via a persistent "Start from
a template" affordance), show N preset cards that deep-link into the existing create flow with a
prefill payload. **The create form is unchanged** — presets only seed it. This is the same shape as
ONBOARDING-UX's `EmptyState` primitive (C3) and rides the NavRail Starter/Everything progressive
disclosure already designed in ONBOARDING-UX T2.1 — so it composes, it doesn't compete.

**Where it applies** (the cross-surface sweep the owner asked for — ranked by how daunting the empty
surface is today, from a UI audit):

1. **Triggers/Schedule** — the flagship. 4–6 presets (morning briefing, weekly digest, nightly
   check, on-file-change watcher). The lifecycle-event combobox gets grouped (live events first,
   dormant ones collapsed under "advanced").
2. **Workflows** — the create surface is expert-dense; presets = the bundled workflow templates
   surfaced as cards on the empty state.
3. **Tasks** — a "from a template" row (bounded project spec starters).
4. **Knowledge** — empty state offers "add a folder / paste a URL / connect a source" as cards
   rather than a bare form.
5. **Agents / Tools / Skills** — empty states point at the Store/create with one example each.

### 1.3 Tasks

| ID | Task | Files | Done when |
|---|---|---|---|
| SP1.1 | `PresetEmptyState` primitive + `PresetCard` (icon, title, cadence/summary line, description) in the shared UI; a `Prefill` type per consuming surface | `web/src/ui/PresetEmptyState.tsx`, `web/src/ui/` | renders N cards; clicking one calls an `onPick(prefill)`; a11y (keyboard, focus-visible) per the design-system rails |
| SP1.2 | Triggers/Schedule: preset catalog (data, not hardcoded copy — derive cadence from the locale-format seam so it doesn't freeze en-US), empty-state cards, prefill wired into `TriggerCreatePage`/`ScheduleForm`; group the lifecycle combobox (live vs dormant) | `web/src/pages/triggers/*`, `web/src/pages/schedule/*`, a preset catalog module | fresh dev home: Triggers empty state shows presets; clicking "Morning briefing" opens the create flow pre-filled to a working schedule trigger; expert path (blank create) unchanged |
| SP1.3 | Apply the pattern to Workflows + Tasks empty states (reuse bundled templates as the preset source — do not author new copy that drifts from the templates) | `web/src/pages/workflows/*`, `web/src/pages/tasks/*` | each empty surface offers template cards that deep-link into the existing create flow |
| SP1.4 | Knowledge + Agents/Tools/Skills empty states get preset/example cards (lighter touch — one or two each) | those pages | no empty surface presents a bare form with no on-ramp |
| V1 | Validation as a newcomer: fresh dev home, walk every list surface's empty state; confirm each offers a guided on-ramp AND the expert blank-create path still works unchanged; screenshot each | — | recorded in Execution log; no surface is "bare form only" |

**Guardrail:** presets seed the *existing* form and never replace it. The full power (raw cron, all
lifecycle events, every action provider) stays one segmented-control click away. This is the
owner's "without degrading the power" constraint made literal.

---

## 2. App Store UI — persistent right rail + card polish (Wave 1)

### 2.1 The gap

KiroCrew's Discover uses a **persistent `CategoryRail`**: two always-visible blocks — CATEGORIES
(canonical categories with per-category counts; select to filter) and SOURCES (trust provenance —
Built-in badge + each external registry with its app count + an Add-source action). Their app cards
(`FeatureCard`) are art-forward: a ~16:9 hero image column (with a gradient+icon fallback), then
name / two-line description / category / action button.

PersonalClaw's Store (`AppsSection.tsx`, 1455 LOC) puts category/type/tag filtering behind a
`FilterMenu` **dropdown** and sources behind a `SourcesPopover` — both hidden until opened. Cards
group under `SourceDivider` headings. The owner wants exactly KiroCrew's arrangement: **categories +
source management moved into a right-side rail, always open on wide screens, with the card look
polished.**

### 2.2 Tasks

| ID | Task | Files | Done when |
|---|---|---|---|
| SP2.1 | `StoreSideRail` component: CATEGORIES block (derived from installed+catalog tags → canonical categories with live counts; select filters; "All" resets) + SOURCES block (Built-in badge + each registered source with app count + Add-source → existing sources flow). Reuses the existing filter state; the dropdown `FilterMenu` becomes the narrow-screen fallback | `web/src/pages/apps/AppsSection.tsx`, new `web/src/pages/apps/StoreSideRail.tsx` | rail lists categories with counts + sources; selecting a category filters the grid; adding a source works from the rail |
| SP2.2 | Responsive: rail is **always open on wide screens** (≥ a breakpoint), collapses to the dropdown/popover on narrow. State deep-linked in the URL like the rest of the app (hash-router doctrine) | same + layout | wide viewport shows the rail persistently; narrow falls back; category selection survives reload (URL) |
| SP2.3 | App card polish to the art-forward shape: hero-image column with gradient+icon fallback, name / 2-line clamp description / category / action; honest quality/permission badges (coordinate with APP-PLATFORM-EVOLUTION S2's `quality` block rather than inventing a second badge) | `web/src/pages/apps/` card component(s), design tokens | cards render art-forward; no hardcoded colors (token-lint passes); fallback gradient deterministic per app name |
| V2 | Validation: Store on wide + narrow; category/source filtering from the rail; card rendering with and without hero art; a11y (rail is keyboard-navigable, `aria-pressed` on category buttons) | — | holds; screenshots recorded |

**Coordination:** the quality-badge half is APP-PLATFORM-EVOLUTION S2's `quality` manifest block —
this plan renders it, that plan defines and CI-verifies it. Do not duplicate the badge logic.

---

## 3. Onboarding import — detect & import from other local agent tools (Wave 1/2)

### 3.1 What it is

KiroCrew ships a **4,155-line `onboarding_import.py`** that scans the machine for five sibling tools
(`codex`, `claude_code`, `meshclaw`, `openclaw`, `hermes`), resolving each via env-var-then-default
roots (`CLAUDE_CONFIG_DIR`→`~/.claude`, `CODEX_HOME`→`~/.codex`, …), and imports across **seven
categories**: `instructions` (CLAUDE.md/AGENTS.md), `memories`, `workspaces`, `mcp_servers`,
`skills`, `schedules`, `settings`. It is a real switching-cost reducer: a user arriving from Claude
Code keeps their MCP servers, skills, instruction files, and memories.

PersonalClaw has **no equivalent** (`grep` for import-from-other-tools returns nothing), and
ONBOARDING-UX has no import row. This is a genuine gap and a strong first-run moment.

### 3.2 Design (adopt their shape, our boundaries)

- **A scanner per source** (`onboarding/import/sources/*.py`), each a pure function
  `scan(root) -> ScanResult` with no store/session dependency, exactly like their `_Scan` dataclass —
  so it's unit-testable against a fixture home. Start with the two highest-value sources:
  **Claude Code** (`~/.claude` — `CLAUDE.md`, `.mcp.json`, `settings.json`, `skills/`) and **Codex**
  (`~/.codex` — `AGENTS.md`, config). Others are additive later (their five-source breadth is not
  the v1 bar).
- **Category → PersonalClaw destination map:** instructions → project/agent instruction docs;
  memories → the memory store (`memory_service`); mcp_servers → MCP config; skills → `skills/imported/
  <source>/`; settings → a **reviewed** merge (never silently overwrite). schedules → triggers.
- **Consent + review, never silent.** A four-value writer vocabulary (imported / existing / conflict
  / rejected) surfaced in an onboarding step; the user picks what to import per category. Their
  `_WriteOutcome` model is the reference.
- **Security floors reuse ours:** every imported file passes `is_sensitive_path` refusal +
  `redact_credentials`/`redact_exfiltration_urls`; imported skills go through the same install-scan
  as Store skills. Secrets in a scanned config are **counted and skipped**, never imported.
- **Fingerprint-idempotent:** re-running import doesn't double-import (SHA over
  `source\0category\0key`, their `_Item.fingerprint`).

### 3.3 Tasks

| ID | Task | Files | Done when |
|---|---|---|---|
| SP3.1 | Scanner framework: `ScanResult`/`ImportItem`/`WriteOutcome` types + a source registry; the Claude Code + Codex scanners (pure, fixture-tested); env-var-then-default root resolution | `src/personalclaw/onboarding/import/` (new pkg), tests | a fixture `~/.claude` yields instructions+mcp+skills items; secrets are counted+skipped; re-scan is idempotent |
| SP3.2 | Writers per category → PersonalClaw destinations, with the four-value outcome vocabulary; imported skills routed through the install-scan; settings merge is review-gated (never clobber) | `onboarding/import/writers.py`, memory/MCP/skills/triggers seams | importing the fixture creates the memories, MCP entries, and `skills/imported/claude_code/*`; a conflicting item reports `conflict`, not silent overwrite |
| SP3.3 | Onboarding step UI: "We found Claude Code / Codex on this machine — import?" with per-category checkboxes, counts, and a review of conflicts; skippable; idempotent on re-entry | `web/src/app/onboarding/` (extends ONBOARDING-UX's StepStack), `GET/POST /api/onboarding/import` | fresh home with a fixture source shows the step; import completes; secrets never appear; re-entry shows already-imported as `existing` |
| V3 | Validation: seed a fake `~/.claude` under the dev home, run onboarding, import, confirm memories/MCP/skills landed and a planted secret did not; confirm skip path and re-entry idempotence | — | holds |

**Scope discipline:** v1 = Claude Code + Codex. The other three sources are additive rows later;
do not block the step on five-source parity. Record the deferral in the Execution log.

---

## 4. Artifact folders (Wave 2)

### 4.1 Design (their rename-safe model, adopted)

KiroCrew's artifact folders use an **opaque `folder_id`, never a path** — a flat JSON store
(`ArtifactFolderStore`, `{id, name, order, parent_id, icon}`) with nesting via `parent_id`, and
membership lives on the artifact record (`Artifact.folder_id`). Renaming a folder never rewrites
artifact records; filing is a metadata-only mutation that does **not** bump `updated_at`. This
mirrors PersonalClaw's own chat-folder subsystem, so the pattern is already house-native.

PersonalClaw's artifact store (`artifacts/models.py`, `native.py`) has **no folder concept**.

### 4.2 Tasks

| ID | Task | Files | Done when |
|---|---|---|---|
| SP4.1 | `ArtifactFolderStore` (flat JSON, opaque 12-char-hex id, `parent_id` nesting, `order`, `icon`) mirroring the chat-folder store; `Artifact.folder_id` field (tolerant-loaded, default `""` = library root); `set_folder` as a metadata-only mutation | `src/personalclaw/artifacts/models.py`, `native.py`, new folder store | folders CRUD; filing an artifact is metadata-only (no `updated_at` bump); renaming a folder leaves artifact records untouched |
| SP4.2 | List/query by folder (present-vs-absent distinction like their `folder` param: `None`=all, `""`=unfiled, id=that folder); routes | `artifacts/handlers.py`, `dashboard/handlers` | `GET /api/artifacts?folder=<id>` scopes correctly; unfiled vs all distinguished |
| SP4.3 | Artifacts library UI: folder tree in the side rail, drag-to-file, create/rename/delete, unfiled bucket | `web/src/pages/artifacts/*` | a user creates a folder, drags artifacts in, renames it; artifacts persist membership across reload |
| V4 | Validation: create nested folders, file artifacts, rename a folder (confirm no artifact churn), delete a folder (confirm members fall back to unfiled) | — | holds |

---

## 5. Artifact deploy — LOCAL html/react served through PersonalClaw (Wave 2/3)

### 5.1 The owner's delta from KiroCrew

KiroCrew's deploy is **bring-your-own-AWS** (S3 + CloudFront in the user's account, `deploy/` engine,
IAM setup). The owner wants something simpler and on-soul: **a local server, exposed through the
PersonalClaw UI/domain**, so a user can build and interact with their own html/react widgets/apps
*inside* PersonalClaw — no cloud account, no liability. This is a better fit for the local-first
tenet, and PersonalClaw already has every seam it needs:

- **App-backend hosting** (`apps/backend_runtime.py`) already spawns loopback subprocesses reached
  through the gateway proxy (`api_app_proxy`, `/apps/{name}/api/{tail}`) with a path-traversal guard
  and per-app scoped tokens — the exact hosting substrate.
- **Sandboxed iframe preview** already exists for artifacts (`ArtifactCard.tsx`,
  `sandbox="allow-scripts"` srcdoc) — the render surface.
- **Headless render** (`web/render.py`) for verification/screenshots.

So a "deployed artifact" is: a multi-file artifact (needs §4 folders / a bundle kind) served either
as static files through a gateway route, or — for a React app — built once and served as a static
bundle, addressable at a stable in-gateway URL the user opens from the artifacts UI.

### 5.2 Design

- **New artifact kind `webapp`** (their name, adopted): a multi-file artifact whose entry is
  `index.html` (static) or a built React bundle. Carries deploy metadata (entry point, build command
  if any, a stable slug → URL).
- **A gateway static-serve route** `GET /artifacts/serve/{slug}/{path:.*}` that serves the artifact's
  files from artifact storage, behind the same session auth + a path-traversal guard as app UI
  serving, with a **strict CSP** (this is user/agent-generated content — fence it like a widget:
  no access to the gateway API except through an explicitly-granted, scoped channel).
- **React build path:** reuse the existing frontend build tooling in a sandboxed, resource-limited
  spawn (this is exactly where SIBLING-HARDENING §1's `build` ceiling profile applies) → emit a
  static bundle stored as the artifact's files. No dev server per artifact; build-once-serve-static.
- **The UI:** a "Deploy / Open" action on a `webapp` artifact opens it in a new tab / embedded pane
  at its stable URL; the artifacts library shows deployed apps with their URL.
- **Explicitly out of scope (owner boundary):** the AWS/public path. If a user wants public exposure
  that rides EXTERNAL-ACCESS's authenticated-exposure work, not a bespoke cloud provisioner. Record
  this so a future session doesn't rebuild KiroCrew's EC2 path.

### 5.3 Tasks

| ID | Task | Files | Done when |
|---|---|---|---|
| SP5.1 | `webapp` artifact kind + multi-file storage (depends on §4's folder/bundle grouping — a webapp is a filed set); deploy metadata (entry, build cmd, slug) | `artifacts/models.py`, `native.py` | a multi-file html artifact can be created and stored with an entry point |
| SP5.2 | Gateway static-serve route with session auth + traversal guard + strict CSP fencing; stable per-slug URL | `dashboard/handlers/artifacts.py` (or new), `dashboard/server.py` | an html artifact renders at `/artifacts/serve/<slug>/`; a traversal attempt is refused; the served page cannot reach the gateway API |
| SP5.3 | React build path: sandboxed, resource-limited build spawn (uses SIBLING-HARDENING `build` ceiling profile) → static bundle stored as the artifact; failure surfaces a WHAT/WHY/FIX error | build seam, `artifacts/native.py` | a small React artifact builds and serves as static files; a build failure is legible, not a hang |
| SP5.4 | Artifacts UI: Deploy/Open action, deployed-app listing with URL, teardown; embedded-pane option | `web/src/pages/artifacts/*` | a user deploys an html widget and opens it in-app; teardown removes the route |
| V5 | Validation: build a small html widget and a small React app as artifacts, deploy both, interact through the PersonalClaw UI, confirm CSP fencing (the page cannot call `/api`), tear down | — | holds |

**Ordering:** §5 depends on §4 (a webapp is a multi-file/filed artifact) and on SIBLING-HARDENING §1
(the `build` ceiling profile — a React build is exactly the unbounded-spawn hazard that plan fixes).
Do not start §5.3 before that profile exists.

---

## 6. Artifacts as a knowledge source — indexed, not listed (Wave 2)

### 6.1 The exact mechanism to port

KiroCrew's `knowledge/artifact_ingest.py` (550 LOC) mirrors content-bearing artifacts into the
Knowledge Library **without listing them as knowledge items**, via four precise design choices:

1. **One aggregate "Artifacts" source row** (`source_type="artifact"`, uri `artifact://`) — appears
   in the Sources UI like a folder source; items grouped per-artifact so one artifact's items are
   replaced on edit / removed on delete without touching the rest.
2. **Event-driven, no polling** — a single in-process change-listener on the artifact store (the
   gateway is the only writer) fires ingest on `upsert`, removal on `delete`.
3. **First-enable backfill tied to source-row creation** — the row's existence is the idempotency
   marker; the one-time backfill runs when the row is first created, never again.
4. **One ingestion path** — artifacts go through the same `FileReader`/extractor path as folders/
   uploads via a `kind → extension` map (html→prose extraction, md/text/json→text; widget/svg
   excluded), not a parallel raw-text path. Content redacted before crossing into the store.

PersonalClaw has the seams: a knowledge source framework (`knowledge/pipeline/`, `connectors/`) and
a `KnowledgeStore` — but **no artifact change-listener and no artifact source type**.

### 6.2 Tasks

| ID | Task | Files | Done when |
|---|---|---|---|
| SP6.1 | Aggregate `artifact://` source row (source_type `artifact`) + per-artifact item grouping in the knowledge store; a `knowledge.auto_ingest_artifacts` config (default on) 4-point wired | `knowledge/store.py`, `knowledge/pipeline/*`, `config/loader.py` | the source appears in Sources UI; config round-trips |
| SP6.2 | In-process change-listener on the artifact store → ingest/replace on upsert, remove on delete; kind→extension map through the existing `FileReader` path; redaction on the way in; widget/svg excluded | `artifacts/native.py` (emit change events), `knowledge/artifact_ingest.py` (new), `knowledge/readers.py` | saving a markdown artifact makes it searchable in Knowledge **without** appearing in the Knowledge list; deleting it removes it from the index |
| SP6.3 | First-enable backfill tied to row creation (idempotent); artifacts do **not** show as knowledge *items* (they're source-grouped, surfaced only in search results with an artifact provenance badge) | `knowledge/artifact_ingest.py` | enabling on a home with existing artifacts backfills once; reboot doesn't re-run; artifacts are searchable but not listed as items |
| V6 | Validation: create/edit/delete artifacts, confirm they become/refresh/leave knowledge search results without polluting the Knowledge list; confirm a credential in an artifact is redacted before indexing | — | holds |

---

## 7. First-party app suite (Wave 2/3 — phased, one app per effort)

### 7.1 The gap and the reconciliation

KiroCrew ships ~11 substantial first-party product apps. PersonalClaw's product-app roster is thin
(`minutes`, `growth`, `meta-muse-spark`, `skills-sh` — the rest of PersonalClawApps is model/search/
tool providers). The owner wants an equivalent first-party suite designed around the same use cases.
**Reconcile with what exists** — `minutes` overlaps KiroCrew's Meetings; don't rebuild it, extend it.

This section is a **program, not a single session** — each app is its own PersonalClawApps effort,
built against the SDK, validated in the real UI, and listed in the Store. It coordinates with
ECOSYSTEM-TOOLING (the scaffold + exemplars) — these apps ARE the exemplars that prove the platform.

### 7.2 The suite (design intent per app — build order by leverage)

| App | KiroCrew analog | Use case | Notes |
|---|---|---|---|
| **Code Review** | Code Review Sage | Deep-review a GitHub PR; each changed file in its own isolated subagent, weighted by blast radius; findings kept locally | Highest leverage — dogfoods subagents + the SDK. Uses `git`/`gh`. |
| **Research Lab** | Research Lab (auto_research) | Multi-cycle autonomous research campaigns that keep working unattended (question → sub-question tree → agents → synthesis) | Dogfoods triggers/autonudge + subagents; a flagship "walk away" demo. |
| **Design Critique** | Design Critique | Pre-colleague design review from a screenshot / flow / Figma / URL; heuristic + a11y findings | Small, high-signal; uses the vision path + headless render. |
| **Docs/Slides** | PPTX Maker + Papyrus | Generate real `.pptx` and LaTeX/markdown documents from a brief | PersonalClaw already has doc-writer deps (DOCUMENT-HANDLING-TOOLS shipped) — these are app fronts over that seam, not new backends. |
| **Notes** | md_notebook | A git-backed markdown notebook (versioned, portable) | Overlaps Knowledge; scope as a *notebook editor* app, not a second knowledge store. |
| **Issue Radar** | Issue Radar | GitHub/GitLab issue triage with AI-suggested labels + per-issue investigation notes kept locally | Pairs with Code Review; open-source-maintainer value. |
| **Ops** | Ops Mission Control | On-call first responder: watch alarms/pages, claim, investigate, propose fixes | Largest; heavily gated (needs AUTONOMY-GUARDRAILS + confirm-gated fixes). Later. |
| **Spec Builder** | Spec Builder | Idea → Requirements → Design → Tasks, then hand to an autonomous run | Overlaps WORKFLOWS-V2 planning; scope as an app front over the engine, not a parallel planner. |
| **Companion** | Mochi | An optional desktop companion surface (watchlist, reminders, day-planning) | Charm/retention; strictly opt-in; coordinate with COMPANION-APPS/DESKTOP. |

### 7.3 Approach (not a task table — a program note)

- **Each app is its own PR in PersonalClawApps**, built to the app-creation contract (`app.json`,
  SDK-only imports, minimum permissions, `test_provider.py`/`test_server.py`, README, LICENSE),
  validated by adding it as a local Store source and driving it in the real UI.
- **Build order = leverage:** Code Review → Research Lab → Design Critique → Docs/Slides → Notes →
  Issue Radar → Spec Builder → Ops → Companion. Each is independently shippable; do not batch.
- **Reuse, don't rebuild:** Docs/Slides ride DOCUMENT-HANDLING-TOOLS; Spec Builder rides WORKFLOWS-V2;
  Notes coordinates with Knowledge; Meetings already exists as `minutes` (extend, don't duplicate).
- **These are the ECOSYSTEM-TOOLING exemplars.** That plan's scaffold generates the skeleton; this
  program fills them in. Record each shipped app in ECOSYSTEM-TOOLING's exemplar list.

---

## 8. Skills we're missing + the steering assessment (Wave 2, small)

### 8.1 Steering is already ~90% covered — add only the viewer

KiroCrew's **steering** = always-on convention `.md` files at two scopes (`~/.kiro/steering/**`
global, `<project>/.kiro/steering/**` workspace) injected into every session — distinct from
**skills**, which are on-demand. The conceptual split is: *steering = always-on rules; skills =
loaded-when-relevant workflows.*

PersonalClaw **already has both halves of the always-on layer**: skills carry an `always: true`
frontmatter flag (`skills/loader.py:get_always_skills`) and `project_context.py` injects
project-scoped instructions/overview into every session in that project. So the *mechanism* exists —
what's missing is only the **legibility surface** KiroCrew's Steering Viewer provides: a place that
shows "which always-on conventions are in effect right now," with provenance (global vs project),
editable. Their `steering-viewer.md` is a strong reference for the security discipline (symlink-leaf
rejection, atomic write preserving mode bits, containment on the trust base).

**Recommendation:** do NOT import a parallel "steering" concept — it would be a second always-on
mechanism competing with `always: true` skills + project instructions (violates *one path per
concern*). Instead add a **"Always-on" viewer** to the Capabilities area that lists, with provenance,
every always-on skill and project-instruction doc in effect, read/edit inline. Small, high-legibility.

### 8.2 Skill authoring targets (the gap)

KiroCrew's builtin skills cluster around **doing** (frontend-design-workflow, image-authoring,
pptx-maker, papyrus-writing, web-browse/preview/verify, ios-simulator-preview, meetings, computer-
use). PersonalClaw's bundled skills cluster around **platform discipline** (memory-discipline,
knowledge-grounding, delegation, task-and-project, pclaw-api/features, grill). The gap is **domain
craft skills** — reusable workflows for common user work, not platform mechanics.

Missing skill areas worth authoring (each a bundled skill or an app-contributed one):

- **Frontend/design workflow** (build-a-UI, critique-a-UI) — pairs with the Design Critique app.
- **Document authoring** (slides, papers, structured docs) — pairs with Docs/Slides.
- **Web verification / preview** (drive a built webapp, verify it renders/works) — pairs with §5.
- **Research campaign** (the grill→decompose→investigate→synthesize loop as a reusable skill) —
  pairs with Research Lab.
- **Data/spreadsheet** and **image authoring** workflows.

### 8.3 Tasks

| ID | Task | Files | Done when |
|---|---|---|---|
| SP8.1 | "Always-on" viewer in Capabilities: lists every `always: true` skill + project-instruction doc in effect, with provenance (global/project) and inline read/edit; reuses the skills/steering security discipline (containment, atomic write, no credential redaction on the editor round-trip but redacted metadata) | `web/src/pages/.../CapabilitiesPage`, a read/edit handler | the page shows what's always injected now; editing a project instruction round-trips safely |
| SP8.2 | Author the first domain-craft bundled skills (web-verify/preview + document-authoring + research-campaign), each with the frontmatter contract and an example; the rest ship with their partner apps in §7 | `src/personalclaw/skills/bundled/*` | the three skills load, surface when relevant, and are validated in a real session |
| V8 | Validation: confirm the always-on viewer matches what a session actually receives (spot-check against an assembled prompt); confirm the new skills surface | — | holds |

---

## 9. Channels — RE-HOME to CHANNEL-EXPANSION (amendment)

**No new plan.** CHANNEL-EXPANSION already owns Telegram (S2-3), Discord (S4-5), and email, with a
proven vendor-blind transport seam and a `channel_conformance.py` kit. Two design inputs from the
study go **into that plan** as an amendment:

1. **The shared TurnDriver.** KiroCrew factors a channel-neutral `messaging/driver.py` (`TurnDriver`)
   that consumes the provider event stream and emits abstract `OutputEvent`s to a per-channel
   `Renderer`, owning **redaction + the tool-approval ladder once** so every channel inherits them.
   Each channel is then a uniform 7-file shape (`client/commands/gateway/renderer/transport/…`).
   PersonalClaw's `ChannelTransportProvider` is comparable but does not centralize the turn concerns —
   adopting a shared driver means Telegram/Discord/etc. don't each re-implement approval + redaction.
   **Recommendation for CHANNEL-EXPANSION:** land the shared driver as S1's trust-seam companion
   before the per-channel work, so every channel is thin.
2. **Channel breadth + Slack polish.** Their Teams/Webex/WeCom/WeChat are ~1.3–1.5k LOC each on the
   shared driver — cheap once the driver exists. Their Slack app (21k LOC) has `enterprise.py`,
   `channel_resolver.py`, `interactions.py`, `blocks.py`, `retry.py` — worth a read against our
   slack-channel app for enterprise-workspace handling and interaction/retry robustness.
   **These are added channels + a Slack hardening pass, appended to CHANNEL-EXPANSION's Wave 2**, not
   a new plan. Community-tier rules (WhatsApp/Signal) are unchanged.

Recorded as an amendment in CHANNEL-EXPANSION; sequencing is a nudge (Telegram first = the mobile
story), consistent with SIBLING-HARDENING §7.1.

---

## 10. Distribution — RE-HOME to DISTRIBUTION (reconcile with SIBLING-HARDENING §7.2)

**No new plan; DISTRIBUTION is DONE with a follow-on.** SIBLING-HARDENING §7.2 already **deferred**
release channels (stable/insider/nightly) with a named gate — this section only adds the specifics
the owner asked to study:

- **Signed channel feed** (their `cli.sh`): resolves a channel feed, verifies its RSA-SHA256
  signature against an installer-pinned public key, records the channel in the data home, and pins
  wheels by `SHA256SUMS` — **no unsigned fallback**. PersonalClaw already has OIDC build-provenance
  attestation (stronger than a hash file); the missing piece is the **channel** concept +
  `_installer.py` channel awareness + a signed feed.
- **Desktop auto-update** (their `sign-and-notarize.yml` + electron-updater on a universal
  signed/notarized DMG so grants stay sticky across updates): DISTRIBUTION T4.3 already delegates
  desktop to electron-updater (plan 45); the study confirms the **notarized-DMG-so-grants-persist**
  detail is worth pinning in DESKTOP-CAPABILITIES.
- **Per-arch vs universal artifact** decision (they chose one universal zip + auto-update onto native
  arm64) — a concrete data point for DESKTOP-CAPABILITIES.

**Action:** append a "signed channel feed" row to DISTRIBUTION's deferred S5 (convenience channels)
and note the notarized-DMG detail in DESKTOP-CAPABILITIES. No work is owned here.

---

## 11. Computer use — NEW PLAN (DESKTOP-COMPUTER-USE, #69)

BROWSE-AUTOMATION is **browser-only**; nothing owns desktop GUI automation. KiroCrew's `computer_use/`
(13k LOC) drives the operator's **native desktop apps** via the accessibility tree (element-index
`AXPress`, not screenshot-and-coordinate) with a keystone out-of-band enable the agent can't flip.
This is a distinct, large capability → its own plan: [DESKTOP-COMPUTER-USE](DESKTOP-COMPUTER-USE.md).
Design there; this section only records the routing.

---

## 12. Execution order (this plan's sections + the two re-homes)

By atomic completability — each row finishes to a clean state without waiting on the engine queue.

| # | Work | Size | Why here |
|---|---|---|---|
| 1 | §1 preset empty states (SP1.1–1.2 for Triggers/Schedule first) | S–M | The owner's flagship ask; cheap; every newcomer hits it |
| 2 | §2 App Store right rail + card polish | M | Owner-requested, self-contained, coordinates APP-PLATFORM-EVOLUTION badges |
| 3 | §6 artifacts → knowledge auto-ingest | S–M | Small, high-value, seams already exist; independent |
| 4 | §4 artifact folders | M | Prerequisite for §5; house-native pattern |
| 5 | §3 onboarding import (Claude Code + Codex) | M–L | Strong first-run moment; large but independent |
| 6 | §8 always-on viewer + first domain skills | S | Cheap legibility + fills the craft-skill gap |
| 7 | §1 (SP1.3–1.4) cross-surface sweep | S | After the pattern proves out on Triggers |
| 8 | §5 artifact local deploy | M–L | **After §4 + SIBLING-HARDENING §1's `build` ceiling profile** |
| 9 | §7 app suite (Code Review → Research Lab → …) | XL, phased | Each app its own effort; ongoing; these are the ECOSYSTEM-TOOLING exemplars |
| — | §9 channels | — | RE-HOME → CHANNEL-EXPANSION amendment |
| — | §10 distribution | — | RE-HOME → DISTRIBUTION S5 follow-on |
| — | §11 computer use | — | NEW → DESKTOP-COMPUTER-USE (#69) |

Rows 1–3 are roughly two sessions and deliver the most visible "less daunting, more capable" wins.

---

## Owner tasks (real world)

- **Confirm the app-suite order/scope** in §7 — which apps matter most for your users, and whether
  Ops (the largest, gated on Guardrails) is worth its size vs. the lighter apps.
- **Confirm the local-deploy boundary** in §5: local-server-through-PersonalClaw only, with public
  exposure deferred to EXTERNAL-ACCESS (not a bespoke cloud provisioner). Stated as an assumption;
  say if you want a cloud path too.

## Risks & open questions

- **§5 CSP fencing is the sharp edge.** A deployed React app is agent/user-generated code served from
  our origin. It must be fenced like a widget — no ambient access to `/api`. Get this wrong and a
  deployed artifact becomes an XSS foothold against the gateway. Validate the fence explicitly (V5).
- **§7 is a program, not a plan-sized deliverable.** Nine apps is a lot; the risk is starting many
  and finishing none. The build-order-by-leverage + one-PR-per-app discipline is the mitigation —
  each app is independently shippable and validated before the next.
- **§3 secret handling.** Scanning other tools' configs means touching files that hold API keys.
  The count-and-skip + refusal-on-sensitive-path floor is load-bearing; a leaked key imported into
  our store would be a real defect. Test with a planted secret (SP3.1 done-when).
- **§1 must not become removal.** The temptation with "simpler" is to hide power. The guardrail
  (presets seed the unchanged expert form) is the whole point — a V-task that confirms the expert
  path still works is mandatory, not optional.

## Execution log

<!-- Append only: - [YYYY-MM-DD][T<id>] DEVIATION|DISCOVERY|DONE|BLOCKED: <one line> -->

- [2026-08-05][plan] DISCOVERY: steering is ~90% already covered — `skills/loader.py:get_always_skills`
  (`always: true` frontmatter) + `project_context.py` injection ARE the always-on layer KiroCrew calls
  steering. Importing a parallel "steering" concept would be a second always-on mechanism (violates one
  path per concern). §8 adds only the missing legibility surface (an always-on viewer), not a new store.
- [2026-08-05][plan] DISCOVERY: no plan owns desktop GUI automation — BROWSE-AUTOMATION is browser-only,
  verified by grep for accessibility/AXPress/desktop-automation across all plans (zero hits). Routed to a
  new plan DESKTOP-COMPUTER-USE (#69) rather than stretching BROWSE-AUTOMATION off its browser soul.
- [2026-08-05][plan] DISCOVERY: onboarding-import has no owner (ONBOARDING-UX has no import row); artifact
  folders/deploy/knowledge-ingest have no owner (ARTIFACTS-EVOLUTION owns iterate/diff/references, not
  these). All routed here as new sections. Channels (CHANNEL-EXPANSION) and distribution (DISTRIBUTION,
  DONE) DO have owners → re-homed as amendments, not duplicated. No code changed by this plan.
