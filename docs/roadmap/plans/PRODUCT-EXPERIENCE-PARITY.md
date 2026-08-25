# PRODUCT-EXPERIENCE-PARITY

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/PEP.md`](../atomic/PEP.md) as 11 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Product Experience Parity — Experience Simplification, Artifacts-as-Apps, Onboarding Import & the First-Party App Suite

**Status:** DESIGNED — created 2026-08-05. Every claim was checked against the current
PersonalClaw tree. This is the **product/UX** companion to
[PLATFORM-HARDENING-FLOORS](PLATFORM-HARDENING-FLOORS.md), which covers the enforcement/trust
findings.

**Created:** 2026-08-05
**Wave:** mixed — §1 (UX simplification) and §2 (App Store UI) are early legibility multipliers;
the rest are product surfaces (Wave 2/3). See the per-section wave tags.
**Depends on:** nothing hard for §1–§6. §7 (app suite) coordinates with ECOSYSTEM-TOOLING;
§9 re-homes to CHANNEL-EXPANSION; §10 to DISTRIBUTION; §11 to the new DESKTOP-COMPUTER-USE plan.

**Scope:** the owner's twelve-item inspiration list, triaged into what PersonalClaw
should build, re-home, or has already covered. **Soul guardrail:** adopt *interaction patterns and
capability seams*, not vendor posture or cloud dependence. Two owner-specific deltas, held
throughout: (a) **artifact deploy is LOCAL-first** — served through PersonalClaw's own gateway/
domain, rather than a cloud-VM/CDN provisioner that stands up a whole EC2/CloudFront path in the
user's own AWS account (the owner explicitly wants a local server the user reaches through the
PersonalClaw UI); (b) every new surface must **not degrade the power** — simplification is
progressive disclosure, never removal.

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
| 8 | Skills we're missing + the always-on-conventions concept | **NEW** (small — mostly an assessment; the always-on layer is largely already covered) | §8 |
| 9 | Telegram/Discord/WeChat/Teams/Webex/WeCom + Slack improvements | **RE-HOME** → CHANNEL-EXPANSION (amendment: the shared TurnDriver + the added channels) | §9 |
| 10 | Build/distribution/update mechanisms | **RE-HOME** → DISTRIBUTION follow-on (reconcile with PLATFORM-HARDENING-FLOORS §7.2, which already deferred release channels) | §10 |
| 11 | Computer use implementation | **NEW PLAN** → [DESKTOP-COMPUTER-USE](DESKTOP-COMPUTER-USE.md) (#69) — BROWSE-AUTOMATION is browser-only; nothing owns desktop GUI automation | §11 |
| — | (always-on conventions, assessed) | **MOSTLY COVERED** — `always: true` skills + `project_context.py` instructions already are the always-on layer; §8 adds only the missing *viewer/legibility* surface | §8 |

**Change class.** §4/§5/§6 change persisted shapes (artifact folder membership, a new artifact
kind, a new knowledge source type) — class-B, executed as clean breaks under the pre-1.0 banner. §1/
§2/§3/§7/§8 are class-R.

---

## 1. Progressive-disclosure UX — preset-first empty states (Wave 1)

### 1.1 The gap, precisely

The target pattern: a schedule surface opens on **four preset cards** — e.g. a dependency guardian,
a nightly build watch — each showing an icon, a title, a human cadence (`Weekly · Mondays
6:00am`), and a one-line description. Clicking one opens the standard create flow **pre-filled**; the
user reviews and saves like any other job. The create form itself is a single segmented control —
`interval | weekly | cron` — that reveals only the fields for the chosen mode. A newcomer never sees
a cron expression unless they pick "cron".

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

The target arrangement uses a **persistent category rail**: two always-visible blocks — CATEGORIES
(canonical categories with per-category counts; select to filter) and SOURCES (trust provenance —
Built-in badge + each external registry with its app count + an Add-source action). App cards are
art-forward: a ~16:9 hero image column (with a gradient+icon fallback), then name / two-line
description / category / action button.

PersonalClaw's Store (`AppsSection.tsx`, 1455 LOC) puts category/type/tag filtering behind a
`FilterMenu` **dropdown** and sources behind a `SourcesPopover` — both hidden until opened. Cards
group under `SourceDivider` headings. The owner wants exactly that arrangement: **categories +
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

The onboarding-import design scans the machine for other local agent tools, resolving each via
env-var-then-default roots (`CLAUDE_CONFIG_DIR`→`~/.claude`, `CODEX_HOME`→`~/.codex`, …), and imports
across **seven categories**: `instructions` (CLAUDE.md/AGENTS.md), `memories`, `workspaces`,
`mcp_servers`, `skills`, `schedules`, `settings`. It is a real switching-cost reducer: a user
arriving from another local agent tool keeps their MCP servers, skills, instruction files, and
memories.

PersonalClaw has **no equivalent** (`grep` for import-from-other-tools returns nothing), and
ONBOARDING-UX has no import row. This is a genuine gap and a strong first-run moment.

### 3.2 Design (the shape, our boundaries)

- **A scanner per source** (`onboarding/import/sources/*.py`), each a pure function
  `scan(root) -> ScanResult` with no store/session dependency (a `_Scan`-style dataclass) —
  so it's unit-testable against a fixture home. Start with the two highest-value sources:
  **Claude Code** (`~/.claude` — `CLAUDE.md`, `.mcp.json`, `settings.json`, `skills/`) and **Codex**
  (`~/.codex` — `AGENTS.md`, config). Others are additive later (broader source coverage is not
  the v1 bar).
- **Category → PersonalClaw destination map:** instructions → project/agent instruction docs;
  memories → the memory store (`memory_service`); mcp_servers → MCP config; skills → `skills/imported/
  <source>/`; settings → a **reviewed** merge (never silently overwrite). schedules → triggers.
- **Consent + review, never silent.** A four-value writer vocabulary (imported / existing / conflict
  / rejected) surfaced in an onboarding step; the user picks what to import per category. A
  `_WriteOutcome` model is the reference.
- **Security floors reuse ours:** every imported file passes `is_sensitive_path` refusal +
  `redact_credentials`/`redact_exfiltration_urls`; imported skills go through the same install-scan
  as Store skills. Secrets in a scanned config are **counted and skipped**, never imported.
- **Fingerprint-idempotent:** re-running import doesn't double-import (SHA over
  `source\0category\0key`, an `_Item.fingerprint`).

### 3.3 Tasks

| ID | Task | Files | Done when |
|---|---|---|---|
| SP3.1 | Scanner framework: `ScanResult`/`ImportItem`/`WriteOutcome` types + a source registry; the Claude Code + Codex scanners (pure, fixture-tested); env-var-then-default root resolution | `src/personalclaw/onboarding/import/` (new pkg), tests | a fixture `~/.claude` yields instructions+mcp+skills items; secrets are counted+skipped; re-scan is idempotent |
| SP3.2 | Writers per category → PersonalClaw destinations, with the four-value outcome vocabulary; imported skills routed through the install-scan; settings merge is review-gated (never clobber) | `onboarding/import/writers.py`, memory/MCP/skills/triggers seams | importing the fixture creates the memories, MCP entries, and `skills/imported/claude_code/*`; a conflicting item reports `conflict`, not silent overwrite |
| SP3.3 | Onboarding step UI: "We found Claude Code / Codex on this machine — import?" with per-category checkboxes, counts, and a review of conflicts; skippable; idempotent on re-entry | `web/src/app/onboarding/` (extends ONBOARDING-UX's StepStack), `GET/POST /api/onboarding/import` | fresh home with a fixture source shows the step; import completes; secrets never appear; re-entry shows already-imported as `existing` |
| V3 | Validation: seed a fake `~/.claude` under the dev home, run onboarding, import, confirm memories/MCP/skills landed and a planted secret did not; confirm skip path and re-entry idempotence | — | holds |

**Scope discipline:** v1 = Claude Code + Codex. Additional sources are additive rows later;
do not block the step on broader source parity. Record the deferral in the Execution log.

---

## 4. Artifact folders (Wave 2)

### 4.1 Design (the rename-safe model)

The artifact-folder model uses an **opaque `folder_id`, never a path** — a flat JSON store
(`ArtifactFolderStore`, `{id, name, order, parent_id, icon}`) with nesting via `parent_id`, and
membership lives on the artifact record (`Artifact.folder_id`). Renaming a folder never rewrites
artifact records; filing is a metadata-only mutation that does **not** bump `updated_at`. This
mirrors PersonalClaw's own chat-folder subsystem, so the pattern is already house-native.

PersonalClaw's artifact store (`artifacts/models.py`, `native.py`) has **no folder concept**.

### 4.2 Tasks

| ID | Task | Files | Done when |
|---|---|---|---|
| SP4.1 | `ArtifactFolderStore` (flat JSON, opaque 12-char-hex id, `parent_id` nesting, `order`, `icon`) mirroring the chat-folder store; `Artifact.folder_id` field (tolerant-loaded, default `""` = library root); `set_folder` as a metadata-only mutation | `src/personalclaw/artifacts/models.py`, `native.py`, new folder store | folders CRUD; filing an artifact is metadata-only (no `updated_at` bump); renaming a folder leaves artifact records untouched |
| SP4.2 | List/query by folder (present-vs-absent distinction via a `folder` param: `None`=all, `""`=unfiled, id=that folder); routes | `artifacts/handlers.py`, `dashboard/handlers` | `GET /api/artifacts?folder=<id>` scopes correctly; unfiled vs all distinguished |
| SP4.3 | Artifacts library UI: folder tree in the side rail, drag-to-file, create/rename/delete, unfiled bucket | `web/src/pages/artifacts/*` | a user creates a folder, drags artifacts in, renames it; artifacts persist membership across reload |
| V4 | Validation: create nested folders, file artifacts, rename a folder (confirm no artifact churn), delete a folder (confirm members fall back to unfiled) | — | holds |

---

## 5. Artifact deploy — LOCAL html/react served through PersonalClaw (Wave 2/3)

### 5.1 The design decision — LOCAL-first deploy

Rather than a bring-your-own-AWS provisioner (a cloud-VM/CDN path that stands up S3 + CloudFront in
the user's account with its own IAM setup), the owner wants something simpler and on-soul: **a local
server, exposed through the PersonalClaw UI/domain**, so a user can build and interact with their own
html/react widgets/apps *inside* PersonalClaw — no cloud account, no liability. This is a better fit
for the local-first tenet, and PersonalClaw already has every seam it needs:

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

- **New artifact kind `webapp`**: a multi-file artifact whose entry is `index.html` (static) or a
  built React bundle. Carries deploy metadata (entry point, build command if any, a stable slug →
  URL).
- **A gateway static-serve route** `GET /artifacts/serve/{slug}/{path:.*}` that serves the artifact's
  files from artifact storage, behind the same session auth + a path-traversal guard as app UI
  serving, with a **strict CSP** (this is user/agent-generated content — fence it like a widget:
  no access to the gateway API except through an explicitly-granted, scoped channel).
- **React build path:** reuse the existing frontend build tooling in a sandboxed, resource-limited
  spawn (this is exactly where PLATFORM-HARDENING-FLOORS §1's `build` ceiling profile applies) → emit
  a static bundle stored as the artifact's files. No dev server per artifact; build-once-serve-static.
- **The UI:** a "Deploy / Open" action on a `webapp` artifact opens it in a new tab / embedded pane
  at its stable URL; the artifacts library shows deployed apps with their URL.
- **Explicitly out of scope (owner boundary):** the AWS/public path. If a user wants public exposure
  that rides EXTERNAL-ACCESS's authenticated-exposure work, not a bespoke cloud provisioner. Record
  this so a future session doesn't rebuild a cloud-VM/CDN provisioner path.

### 5.3 Tasks

| ID | Task | Files | Done when |
|---|---|---|---|
| SP5.1 | `webapp` artifact kind + multi-file storage (depends on §4's folder/bundle grouping — a webapp is a filed set); deploy metadata (entry, build cmd, slug) | `artifacts/models.py`, `native.py` | a multi-file html artifact can be created and stored with an entry point |
| SP5.2 | Gateway static-serve route with session auth + traversal guard + strict CSP fencing; stable per-slug URL | `dashboard/handlers/artifacts.py` (or new), `dashboard/server.py` | an html artifact renders at `/artifacts/serve/<slug>/`; a traversal attempt is refused; the served page cannot reach the gateway API |
| SP5.3 | React build path: sandboxed, resource-limited build spawn (uses PLATFORM-HARDENING-FLOORS `build` ceiling profile) → static bundle stored as the artifact; failure surfaces a WHAT/WHY/FIX error | build seam, `artifacts/native.py` | a small React artifact builds and serves as static files; a build failure is legible, not a hang |
| SP5.4 | Artifacts UI: Deploy/Open action, deployed-app listing with URL, teardown; embedded-pane option | `web/src/pages/artifacts/*` | a user deploys an html widget and opens it in-app; teardown removes the route |
| V5 | Validation: build a small html widget and a small React app as artifacts, deploy both, interact through the PersonalClaw UI, confirm CSP fencing (the page cannot call `/api`), tear down | — | holds |

**Ordering:** §5 depends on §4 (a webapp is a multi-file/filed artifact) and on PLATFORM-HARDENING-FLOORS §1
(the `build` ceiling profile — a React build is exactly the unbounded-spawn hazard that plan fixes).
Do not start §5.3 before that profile exists.

---

## 6. Artifacts as a knowledge source — indexed, not listed (Wave 2)

### 6.1 The mechanism, precisely

The mechanism to build mirrors content-bearing artifacts into the Knowledge Library **without listing
them as knowledge items**, via four precise design choices:

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

A mature first-party product-app suite spans ~11 substantial apps. PersonalClaw's product-app roster
is thin (`minutes`, `growth`, `meta-muse-spark`, `skills-sh` — the rest of PersonalClawApps is model/
search/tool providers). The owner wants a first-party suite designed around common product use cases.
**Reconcile with what exists** — `minutes` overlaps the Meetings use case; don't rebuild it, extend it.

This section is a **program, not a single session** — each app is its own PersonalClawApps effort,
built against the SDK, validated in the real UI, and listed in the Store. It coordinates with
ECOSYSTEM-TOOLING (the scaffold + exemplars) — these apps ARE the exemplars that prove the platform.

### 7.2 The suite (design intent per app — build order by leverage)

| App | Use case | Notes |
|---|---|---|
| **Code Review** | Deep-review a GitHub PR; each changed file in its own isolated subagent, weighted by blast radius; findings kept locally | Highest leverage — dogfoods subagents + the SDK. Uses `git`/`gh`. |
| **Research Lab** | Multi-cycle autonomous research campaigns that keep working unattended (question → sub-question tree → agents → synthesis) | Dogfoods triggers/autonudge + subagents; a flagship "walk away" demo. |
| **Design Critique** | Pre-colleague design review from a screenshot / flow / Figma / URL; heuristic + a11y findings | Small, high-signal; uses the vision path + headless render. |
| **Docs/Slides** | Generate real `.pptx` and LaTeX/markdown documents from a brief | PersonalClaw already has doc-writer deps (DOCUMENT-HANDLING-TOOLS shipped) — these are app fronts over that seam, not new backends. |
| **Notes** | A git-backed markdown notebook (versioned, portable) | Overlaps Knowledge; scope as a *notebook editor* app, not a second knowledge store. |
| **Issue Radar** | GitHub/GitLab issue triage with AI-suggested labels + per-issue investigation notes kept locally | Pairs with Code Review; open-source-maintainer value. |
| **Ops** | On-call first responder: watch alarms/pages, claim, investigate, propose fixes | Largest; heavily gated (needs AUTONOMY-GUARDRAILS + confirm-gated fixes). Later. |
| **Spec Builder** | Idea → Requirements → Design → Tasks, then hand to an autonomous run | Overlaps WORKFLOWS-V2 planning; scope as an app front over the engine, not a parallel planner. |
| **Companion** | An optional desktop companion surface (watchlist, reminders, day-planning) | Charm/retention; strictly opt-in; coordinate with COMPANION-APPS/DESKTOP. |

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

## 8. Skills we're missing + the always-on-conventions assessment (Wave 2, small)

### 8.1 The always-on layer is already ~90% covered — add only the viewer

An always-on convention layer = always-on convention `.md` files at two scopes (global +
per-project) injected into every session — distinct from **skills**, which are on-demand. The
conceptual split is: *always-on conventions = always-on rules; skills = loaded-when-relevant
workflows.*

PersonalClaw **already has both halves of the always-on layer**: skills carry an `always: true`
frontmatter flag (`skills/loader.py:get_always_skills`) and `project_context.py` injects
project-scoped instructions/overview into every session in that project. So the *mechanism* exists —
what's missing is only the **legibility surface** an always-on viewer provides: a place that
shows "which always-on conventions are in effect right now," with provenance (global vs project),
editable. The viewer's security discipline is the reference: symlink-leaf rejection, atomic write
preserving mode bits, containment on the trust base.

**Recommendation:** do NOT import a parallel always-on-conventions concept — it would be a second
always-on mechanism competing with `always: true` skills + project instructions (violates *one path
per concern*). Instead add an **"Always-on" viewer** to the Capabilities area that lists, with
provenance, every always-on skill and project-instruction doc in effect, read/edit inline. Small,
high-legibility.

### 8.2 Skill authoring targets (the gap)

Mature agent skill libraries cluster around **doing** (frontend-design-workflow, image-authoring,
slide/document authoring, web-browse/preview/verify, simulator-preview, meetings, computer-use).
PersonalClaw's bundled skills cluster around **platform discipline** (memory-discipline,
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
| SP8.1 | "Always-on" viewer in Capabilities: lists every `always: true` skill + project-instruction doc in effect, with provenance (global/project) and inline read/edit; reuses the skills security discipline (containment, atomic write, no credential redaction on the editor round-trip but redacted metadata) | `web/src/pages/.../CapabilitiesPage`, a read/edit handler | the page shows what's always injected now; editing a project instruction round-trips safely |
| SP8.2 | Author the first domain-craft bundled skills (web-verify/preview + document-authoring + research-campaign), each with the frontmatter contract and an example; the rest ship with their partner apps in §7 | `src/personalclaw/skills/bundled/*` | the three skills load, surface when relevant, and are validated in a real session |
| V8 | Validation: confirm the always-on viewer matches what a session actually receives (spot-check against an assembled prompt); confirm the new skills surface | — | holds |

---

## 9. Channels — RE-HOME to CHANNEL-EXPANSION (amendment)

**No new plan.** CHANNEL-EXPANSION already owns Telegram (S2-3), Discord (S4-5), and email, with a
proven vendor-blind transport seam and a `channel_conformance.py` kit. Two design inputs go **into
that plan** as an amendment:

1. **The shared TurnDriver.** A channel-neutral `messaging/driver.py` (`TurnDriver`) consumes the
   provider event stream and emits abstract `OutputEvent`s to a per-channel `Renderer`, owning
   **redaction + the tool-approval ladder once** so every channel inherits them. Each channel is then
   a uniform 7-file shape (`client/commands/gateway/renderer/transport/…`).
   PersonalClaw's `ChannelTransportProvider` is comparable but does not centralize the turn concerns —
   adopting a shared driver means Telegram/Discord/etc. don't each re-implement approval + redaction.
   **Recommendation for CHANNEL-EXPANSION:** land the shared driver as S1's trust-seam companion
   before the per-channel work, so every channel is thin.
2. **Channel breadth + Slack polish.** Additional channels (Teams/Webex/WeCom/WeChat) are ~1.3–1.5k
   LOC each on the shared driver — cheap once the driver exists. A mature Slack channel app carries
   `enterprise.py`, `channel_resolver.py`, `interactions.py`, `blocks.py`, `retry.py` — worth
   designing into our slack-channel app for enterprise-workspace handling and interaction/retry
   robustness.
   **These are added channels + a Slack hardening pass, appended to CHANNEL-EXPANSION's Wave 2**, not
   a new plan. Community-tier rules (WhatsApp/Signal) are unchanged.

Recorded as an amendment in CHANNEL-EXPANSION; sequencing is a nudge (Telegram first = the mobile
story), consistent with PLATFORM-HARDENING-FLOORS §7.1.

---

## 10. Distribution — RE-HOME to DISTRIBUTION (reconcile with PLATFORM-HARDENING-FLOORS §7.2)

**No new plan; DISTRIBUTION is DONE with a follow-on.** PLATFORM-HARDENING-FLOORS §7.2 already
**deferred** release channels (stable/insider/nightly) with a named gate — this section only adds the
specifics the owner asked to study:

- **Signed channel feed** (a channel-feed installer pattern): resolves a channel feed, verifies its
  RSA-SHA256 signature against an installer-pinned public key, records the channel in the data home,
  and pins wheels by `SHA256SUMS` — **no unsigned fallback**. PersonalClaw already has OIDC
  build-provenance attestation (stronger than a hash file); the missing piece is the **channel**
  concept + `_installer.py` channel awareness + a signed feed.
- **Desktop auto-update** (a sign-and-notarize workflow + electron-updater on a universal
  signed/notarized DMG so grants stay sticky across updates): DISTRIBUTION T4.3 already delegates
  desktop to electron-updater (plan 45); the **notarized-DMG-so-grants-persist** detail is worth
  pinning in DESKTOP-CAPABILITIES.
- **Per-arch vs universal artifact** decision (one universal zip + auto-update onto native arm64) —
  a concrete data point for DESKTOP-CAPABILITIES.

**Action:** append a "signed channel feed" row to DISTRIBUTION's deferred S5 (convenience channels)
and note the notarized-DMG detail in DESKTOP-CAPABILITIES. No work is owned here.

---

## 11. Computer use — NEW PLAN (DESKTOP-COMPUTER-USE, #69)

BROWSE-AUTOMATION is **browser-only**; nothing owns desktop GUI automation. The capability: drive the
operator's **native desktop apps** via the accessibility tree (element-index `AXPress`, not
screenshot-and-coordinate) with a keystone out-of-band enable the agent can't flip. This is a
distinct, large capability → its own plan: [DESKTOP-COMPUTER-USE](DESKTOP-COMPUTER-USE.md).
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
| 8 | §5 artifact local deploy | M–L | **After §4 + PLATFORM-HARDENING-FLOORS §1's `build` ceiling profile** |
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

- [2026-08-05][plan] DISCOVERY: the always-on layer is ~90% already covered — `skills/loader.py:get_always_skills`
  (`always: true` frontmatter) + `project_context.py` injection ARE the always-on layer. Importing a
  parallel always-on-conventions concept would be a second always-on mechanism (violates one path per
  concern). §8 adds only the missing legibility surface (an always-on viewer), not a new store.
- [2026-08-05][plan] DISCOVERY: no plan owns desktop GUI automation — BROWSE-AUTOMATION is browser-only,
  verified by grep for accessibility/AXPress/desktop-automation across all plans (zero hits). Routed to a
  new plan DESKTOP-COMPUTER-USE (#69) rather than stretching BROWSE-AUTOMATION off its browser soul.
- [2026-08-05][plan] DISCOVERY: onboarding-import has no owner (ONBOARDING-UX has no import row); artifact
  folders/deploy/knowledge-ingest have no owner (ARTIFACTS-EVOLUTION owns iterate/diff/references, not
  these). All routed here as new sections. Channels (CHANNEL-EXPANSION) and distribution (DISTRIBUTION,
  DONE) DO have owners → re-homed as amendments, not duplicated. No code changed by this plan.
- [2026-08-15][PEP-1] DONE: SP1.1 + SP1.2. `web/src/ui/PresetEmptyState.tsx` — `PresetCard` (icon,
  title, cadence/summary, description, `onPick(prefill)`) over **`TileButton`**, so the card inherits
  the kit's chrome + `focus-visible` ring instead of hand-rolling one, and `PresetEmptyState`
  (headline, hint, 1/2-column grid, `footer` slot for the blank path); `prefill` is a type parameter
  the primitive never reads. `pages/triggers/triggerPresets.ts` — four presets built by ONE `preset()`
  factory so the id, the title and the cadence are declared once and used twice (id = catalog key AND
  `?preset=`; title = card heading AND trigger name; cadence = summary line AND saved cron), and the
  cadence label goes through the LOCALE seam: measured `en-US` "Every day · 8:00 AM" vs `de-DE`
  "Every day · 8:00", Monday/Montag/月曜日. The seed rides the URL
  (`#/triggers/new?kind=schedule&preset=<id>`) like `kind`/`pattern`, so a seeded flow is deep-linkable
  and reload-safe; name+cadence seed as lazy `useState` initializers, the ACTION in an effect (its
  config defaults come from the provider's fetched `settingsSchema`), `seedActionConfig` then the
  preset's values so `notify`'s `kind: 'info'` default survives. **Gate:** `make lint` clean (black
  1683 files, isort, flake8, mypy 869 files) · `tsc --noEmit` clean · full `npm test --workspace web`
  **267 files / 2648 tests green** (token-lint caught an inline `maxWidth: '640px'` — fixed at source
  to a `max-w-[640px]` class, not allowlisted) · `npm run build` clean, 89 ui-docs components.
  **42 new tests across 5 files** (2612 → 2654; 267 → 268 files). **Driven on a fresh dev home at :10021:** cards are tab stops 28-31,
  focus ring measured as a 5-layer box-shadow (inset 2px, `:focus-visible` matching), Enter activates;
  the seeded form arrived with `Morning briefing` / `0 8 * * *` / the task prompt filled; Create saved
  `schedule:clock:morning-briefing` with `next_run` set and `POST /api/triggers/<id>/run` returned
  `{"ok": true, "result": "ran"}`, likewise `Standup reminder` (`45 9 * * 1-5`, `notify`);
  `#/triggers/new` bare still opens empty with Create `aria-disabled="true"`; 1 column at 420px, 2 at
  1440px; zero console errors, zero failed requests. **Falsified** by mutation: `onPick` handing back
  `undefined` reds 4 (`expected "spy" to be called with arguments: [ { cron: '0 8 * * *', … } ]`);
  dropping the preset values from the action seed reds 4 (`expected '' to contain 'morning briefing'`);
  the list dropping the preset id reds 1 (`"triggers/new" ≠ "triggers/new?kind=schedule&preset=…"`);
  removing `TileButton`'s ring classes reds 1; making the card a `<div>` reds 8 (`Unable to find an
  accessible element with the role "button"`); `findTriggerPreset` falling back to the first preset
  reds 5 blank-path assertions; a nested `<button>` inside the card reds 3.
- [2026-08-15][PEP-1] DEVIATION: the lifecycle-combobox half ships as live-first ORDERING + a group
  heading, not a collapsible "advanced" group — `Combobox` has no collapsible group and adding one
  would be a new mechanism on a shared primitive. It also ships CONDITIONALLY, and that is the bigger
  deviation: §1.1's "~15 events, 7 of which warn 'never fires'" is **stale**. Measured on a current
  build, `GET /api/triggers/variables` returns **15 lifecycle events, 0 dormant**. An unconditional
  heading would label all fifteen "Live events" and separate nothing, so `lifecycleEventOptions`
  (`triggerMeta.ts`) emits no group while nothing is dormant and both groups the moment one is. Both
  branches asserted, so the rail is not vacuous even though its dormant half matches nothing live.
- [2026-08-15][PEP-1] DISCOVERY: a fresh dev home is **not** trigger-empty, so §1.3's V1 premise needs
  care in `PEP-2`. `action_providers/digest_provider.reconcile_digest_cron` registers
  `system:notification-digest` at every boot, and the Triggers list shows it — measured
  `GET /api/triggers` on a first-boot home returns exactly that one row. A newcomer's first visit is
  therefore one machine-named system row with NO on-ramp (the empty state is gated on
  `counts.all === 0`), which is worse than the empty case this atom fixes. Not changed here on purpose:
  gating on "no USER triggers" would render a row and an empty state at once. Wants an owner call on
  whether system-created triggers belong in the user-facing list.
- [2026-08-16][PEP-7] DONE: SP6.1 + SP6.2 + SP6.3 + V6. `artifacts/changes.py` is the observer seam
  (§6.1 choice 2): a two-word vocabulary — `upsert`/`delete` — emitted by `NativeArtifactProvider`
  from OUTSIDE its lock, so one subscription covers every writer (HTTP, MCP, chat tools) and no save
  serializes behind another's indexing. Artifacts deliberately do NOT emit the library's three-word
  `created`/`modified`/`deleted` vocabulary: create-vs-modify needs to know whether the MIRROR exists,
  which is knowledge-side state, and a wrong guess either duplicates a row or drops an edit.
  `knowledge/artifact_ingest.py` joins the WatchedSource mechanism rather than paralleling it — ONE
  `sources` row (`provider='artifacts'`, `kind='artifact'`, `spec.uri='artifact://'`), per-artifact
  identity as the store's own `(source_id, guid=slug)` pair (so `find_source_item` makes "replace this
  artifact's mirror, touch nothing else" a single-row lookup), and `ingest_queue.enqueue` as the ONLY
  writer. `readers.html_to_prose` is `_read_html`'s conversion half lifted out, so an `html` artifact
  and an uploaded `.html` reduce to the same prose; dispatch is by KIND because every text artifact is
  stored as `current.html` whatever it is. **Gate:** `make lint` clean (black 1719, isort, flake8,
  mypy 887 files) · `tests/test_artifact_knowledge_source.py` 31 passed · durability-inventory +
  portability + config-roundtrip + config-baseline 129 passed · `npm run typecheck` + full
  `npm test --workspace web` + `npm run build` green. `config-baseline.json` regenerated.
- [2026-08-16][PEP-7] DEVIATION: **an artifact delete FORGETS the sighting; it does not archive.**
  WS-5's rule for a watched directory is archive-never-delete, because that file may be back tomorrow
  and the library row is the last remaining copy. An artifact mirror is the opposite kind of upstream:
  we own it, and once it is deleted through the app nothing can revive it, so an archived mirror is a
  permanently unrevivable orphan. New store primitive `forget_source_item(source_id, guid)` does the
  item cascade AND the `source_seen` row in ONE transaction. The second half is not tidiness —
  leaving the seen row makes `create_typed_item`'s novelty gate refuse an artifact re-created under
  the same slug **forever and silently**, which `test_a_recreated_slug_indexes_again` now pins.
  Removal also ignores the master switch on purpose: turning indexing off must not turn deletion off,
  or a user who disables the mirror after deleting an artifact keeps a searchable copy of it.
- [2026-08-16][PEP-7] DEVIATION: the mirror's source row is created with `enrichment='raw'`, which
  §6 does not specify. The mirror is automatic and default-ON, so `full` would spend one model call
  per artifact the first time a gateway starts on an existing home — a cost the user never asked for
  and cannot attribute. `raw` routes every mirror through the LLM-free `FeedItemGraph`
  (`pipeline/graphs.graph_for` overrides the type map for `raw`), and the Sources UI already reads
  that field back as a "no AI" chip, so the guarantee is legible rather than implicit.
- [2026-08-16][PEP-7] DEVIATION: §6.2's SP6.1 asks for a "4-point wired" config; it ships **5-point**
  (dataclass + `_meta`, `load()`, `to_dict()`, the `_EDITABLE_CONFIG` PATCH allowlist, AND a
  `ToggleRow` in Settings → Sources) plus `config-baseline.json`. A user-facing switch with no
  control is a knob only a file edit can reach. `start()` also subscribes UNCONDITIONALLY and reads
  the switch per event: subscribing only when it was on at boot would make turning it on a live
  setting that quietly needs a restart.
- [2026-08-16][PEP-7] DISCOVERY: the Sources row is written for a POLLER, and an event-driven source
  rendered through it is actively wrong — measured "No provider · never polled · every 1h", where the
  first is a **danger** chip asserting that a working mechanism is broken. `_serialize_source` now
  ships `event_driven` and the row drops every poll-shaped verdict, states "indexed as artifacts
  change · turn off in Settings → Sources", and **renders no pause toggle**: the row's `enabled`
  column is not what the mirror reads, so that switch would have saved successfully and moved nothing.
  `enrolled` stays honestly `false` — nothing IS enrolled to poll it, and faking it would hide a
  genuinely orphaned row of some future kind. Second half of the same class: `resolveType` fell
  through its mime/url cascade to `note`, so an artifact search hit rendered a StickyNote labelled
  "Note". `ARTIFACT_TYPE` is its own meta kept OUT of `TYPES` (the create picker's catalog, which must
  not offer a type the backend refuses to author), and the hit carries an "Open artifact" link —
  a mirror is a search surface, so a hit that could only show extracted text would be a dead end.
- [2026-08-16][PEP-7] DISCOVERY: §6.1 says items are "grouped per-artifact", plural. One item per
  artifact is what shipped: `store.py`'s model is "one item = one logical document" and chunking is an
  embedding-pipeline detail, so N items per artifact would be a second chunking layer above the one
  that already exists. The grouping the plan wants — an artifact's mirror replaced on edit / removed
  on delete without touching the rest — is exactly what `(source_id, guid)` already provides.
- [2026-08-16][PEP-7] DISCOVERY: `INDEXABLE_KINDS` is a closed ALLOWLIST rather than §6.1's
  "widget/svg excluded" denylist. A denylist silently indexes every artifact kind added later —
  including a binary one, whose text body on disk is the raw-URL *reference*, so the library would
  index the string `/api/artifacts/<slug>/raw`. `react`/`infographic` are excluded for the same reason
  `widget` is: their bodies are program text, and indexing it makes every search for a variable name
  outrank the user's own notes. Left out deliberately: `get_stats()['items']` still counts mirrors —
  it is a store-wide `COUNT(*)` that already counts archived rows the list hides, so artifacts inherit
  its existing meaning instead of introducing a new inconsistency.
- [2026-08-18][PEP-2] DONE: §1's SP1.3 + SP1.4 + V1, executed as a CENSUS first. Extracted every
  `<EmptyState>`/`<PresetEmptyState>` element under `web/src/pages/**` — **57 sites in 30 files** —
  reduced each file to the branch a user meets when the collection is GENUINELY empty, and pinned the
  result as a four-verdict table in `pages/emptyStateRollout.test.tsx` (`on-ramp` / `produced` /
  `derived` / `degenerate`), whose vacuity floor derives the population FROM THE TREE so a new list
  surface cannot ship without a verdict. **Three surfaces failed the clause** and were fixed:
  `#/workflows` Runs (the DEFAULT tab, so a newcomer's first view of Workflows — its one CTA went to
  the definitions LIST, twenty-odd machine names), `#/knowledge?view=intents` (named the "New intent"
  control in prose and left the user to find it in the top bar), and `#/artifacts` (hint named the
  Files page with no way to get there). Everything else already had one or legitimately has nothing to
  create, with the reason recorded per row. **Gate:** `npm run typecheck` clean · full
  `npm test --workspace web` **396 files / 4029 tests passed** · `npm run build` clean ·
  `make lint` clean (black 1771 unchanged, isort, flake8, mypy 909 files).
- [2026-08-18][PEP-2] DEVIATION: **Workflows' preset cards are built from the LOADED definitions, and
  the surface §1.3 names ("Workflows … empty state") is not the one that was daunting.** The
  Definitions tab is never empty (22 bundled templates ship), so its empty branch is unreachable and a
  template-sourced grid there would be empty by construction. The RUNS tab is the default and IS empty
  on a fresh home, so that is where the grid went. `pages/workflows/workflowPresets.ts` maps the five
  `KIND_TO_TEMPLATE` kinds to cards, resolving the template through the SHARED `templateForKind` (never
  a second table) and reading `summary` from `def.name` and `description` from `def.description` — two
  of the card's three lines are the template's own data, which is how §1.3's "no new copy that drifts
  from the templates" is honoured; only the kind LABEL is authored. A kind whose template is absent is
  dropped, so `presets.length === 0` is reachable and the pre-PEP-2 `EmptyState` survives as that
  branch. Showing the machine name as the accent line is deliberate: it teaches the vocabulary
  `templateSuggest`'s doc says the picker hides.
- [2026-08-18][PEP-2] DEVIATION: **Tasks got nothing, on purpose.** §1.3 pairs it with Workflows
  ("reuse bundled templates as the preset source"), but there is no task-template catalog to source
  from — `src/personalclaw/tasks/` ships models/handlers/registry and no templates — and authoring card
  copy would be exactly the drift the same sentence forbids. `#/tasks` already satisfies the clause
  ("No tasks" → New task → the existing `onCreate`). Recorded as a census row rather than left implicit.
- [2026-08-18][PEP-2] DISCOVERY: **the honesty precondition bit again, on the surface being changed.**
  `IntentsView`'s loader carried `.catch(() => setIntents([]))` — the harsher swallow — so a failed
  `GET /api/knowledge/intents` rendered "No intents yet", and adding a create CTA would have turned a
  silent lie into an actionable one ("make your first" to a user who has some). Fixed in the same
  change: the rejection is captured and `<LoadError what="intents">` is an EARLY RETURN ahead of both
  the skeleton and the list. Filed per-site rather than as an `ui/loadErrorState.test.tsx` ADOPTERS row
  because that rail's no-swallow check is FILE-scoped and this 1000-line page carries several
  deliberate decoration-read fallbacks it would flag — the same reason `settingsListHonesty.test.ts`
  and `sessionLoadHonesty.test.ts` exist. Two coordinated rails moved with it:
  `listDestinationLoadError`'s `what=` vacuity floor (5 → 6) and `loadingNounPairing` (the skeleton
  needed the sibling noun, `what="intents"`).
- [2026-08-18][PEP-2] DISCOVERY: `blankIntent()` found a THIRD copy of the blank-intent literal. The
  header control and the new empty-state CTA were the two the atom set out to unify; the rail's
  "the blank shape has one definition" assertion then failed at 2, exposing the `?intent=__new__`
  deep-link restore (`KnowledgeListPage.tsx:126`) building the same object inline. All three now share
  one definition.
- [2026-08-18][PEP-2] DISCOVERY (not fixed — concurrent work): the **Automations/triggers** surfaces
  were being changed in parallel and were treated as off-limits. Two findings for a follow-up:
  (a) PEP-1's own log already records that a fresh home is NOT trigger-empty —
  `reconcile_digest_cron` registers `system:notification-digest` at every boot, so a newcomer's first
  `#/triggers` visit is ONE machine-named system row and NO empty state at all, because the preset grid
  is gated on `counts.all === 0`. That is strictly worse than the case PEP-1 fixed and it makes the
  flagship preset grid unreachable on a real fresh install. (b) `triggers/WeekGridView`'s "No fires this
  week" has no on-ramp. Both are named in the census's off-limits set so the sweep is honestly short
  rather than silently so.
- [2026-08-18][PEP-2] DEVIATION: `ui/disabledReasonCensus.test.ts` is line-pinned to
  `KnowledgeListPage.tsx`, so its one row shifted 859 → 893 with this edit. A baseline bump, not a
  primitive change — the excused control is byte-identical and its `disabled={!o.item_id}` shape check
  still passes.
- [2026-08-18][PEP-2] V1 validation, driven in a real browser against an ISOLATED home
  (`PYTHONPATH` pointed at the worktree — the venv's `personalclaw` is an editable install of the MAIN
  repo, so the first pass silently measured main's SPA and reported 0 preset cards + the OLD single
  CTA; that near-miss is why every probe below carries a route-specific mounted-ness floor on top of
  the `nav[data-tour="rail"]` onboarding floor). Measured by DOM, not by Playwright's `text=` engine,
  which matched an ANCESTOR and counted a HEADER button as the empty state's own — a fake finding in
  both directions. **EMPTY:** `#/workflows` five cards, each accessible-named `<kind label> — <template
  name>` (`Work on code — code-project`, …), footer "Browse all definitions", old single CTA gone, zero
  alerts; `#/knowledge?view=intents` heading "No intents yet" + exactly ONE body "New intent" (4 header
  duplicates are `HeaderActions`' responsive set); `#/artifacts` heading "No artifacts" + one body
  "Browse files". **The on-ramp reaches the EXISTING flow:** clicking "Plan a project" opened the dialog
  titled **"Run general-project"** carrying the template's own kickoff example and its declared inputs
  (`Task *`, `Exit condition`) — that is `start(name)`'s `promptForm`, no new surface, no route change;
  "Browse files" landed on `#/files`; the footer landed on `#/workflows?tab=defs` with definition rows.
  **NON-EMPTY (expert path unchanged):** with one intent, "No intents yet" and its CTA are absent and
  the row renders; with one artifact, "No artifacts" and "Browse files" are both absent; with one run,
  zero preset cards, zero footer, zero empty headline, the run row present. The Workflows non-empty
  case could NOT be produced from the backend — `POST /api/workflows/runs` refuses with
  `preflight_failed: no model resolves for the 'orchestration' use case` on a credential-less home — so
  it was driven against the real bundle with the runs LIST stubbed, and separately in jsdom with a
  mounted-ness floor. Stated plainly rather than implied.
- [2026-08-18][PEP-2] Falsification, each mutating the LIVE line and restoring from a file copy:
  dropping the Artifacts `action` reds 2 (`Unable to find … name /Browse files/` + `Artifacts claims an
  on-ramp but offers no action`); opening the emptiness gate (`filteredRuns.length === 0` → `>= 0`) reds
  the NON-EMPTY test while the EMPTY one stays GREEN — the discrimination proof; hardcoding
  `summary: def.name` reds 3 including `EMPTY: … Work on code — code-project`; removing the Knowledge
  CTA reds the shared-seed count (1 ≠ 2); restoring the intents swallow reds the capture assertion;
  deleting one census row reds `every empty-state file needs a PEP-2 verdict` with
  `['pages/tools/ToolsPage.tsx']`. **One came back GREEN and the anchor was the fault:** neutralising
  the non-empty test's mounted-ness floor under the mutated gate passed everything, because that
  `await` is also the SYNC POINT — every `queryBy…` ran before the load resolved. Re-probed with a sync
  point the mutated build satisfies and the card-absence assertions reddened as they should; the
  finding is now a 🪤 comment on the floor so nobody deletes it as decoration.
- [2026-08-18][PEP-2] Taste call left open: five kinds lay out 2+2+1 in `PresetEmptyState`'s shared
  two-column grid (Triggers ships four, a clean 2×2), so the fifth card sits alone. Dropping a kind to
  even the grid would mean a user whose intent is "design" sees no card, and widening the grid to three
  columns is a change to a primitive Triggers depends on. Left as-is; wants an owner eye.
- [2026-08-18][PEP-10] DONE: always-on conventions viewer (`legibility/always_on.py`, three
  `/api/legibility/always-on*` routes, an `AlwaysOnConventions` section inside `#/settings/legibility`)
  plus the three domain-craft bundled skills the plan names — `web-verify`, `document-authoring`,
  `research-campaign`. The viewer does NOT re-derive the always-on set: the global tier is parsed out of
  `SkillsLoader.get_context(agent=…)` (the exact string a session receives) and the project tier comes
  from `project_context.inlined_context_files` (the repo's own CONTENT-based answer to "what did the
  project block actually inline") plus the same readers `_project_context_preamble` calls. No parallel
  always-on store was added, per the 2026-08-05 DISCOVERY above.
- [2026-08-18][PEP-10] DISCOVERY: `ContextBuilder.build_session_context` — the single composer every
  session runs through (`context.build_message` calls it; gateway/context_engine/subagent all call
  `build_message`) — is NOT a pure read. It routes the skill block through the budgeted ambient
  allocator, which calls `_record_ambient_measurements` and persists a budget-utilization sample plus a
  cadence-gated ablation sweep. So a GET viewer must not assemble a prompt: a read-only legibility
  surface that mutated learning telemetry on every page open would be a defect. The runtime reads the
  producers; `tests/test_legibility_always_on.py` closes the gap by asserting every viewer item is a
  substring of a really-assembled `build_session_context()` output, with a vacuity floor.
- [2026-08-18][PEP-10] DISCOVERY: ZERO bundled skills ship `always: true` — measured across all
  fifteen bundled skills. The always-on skill tier is therefore EMPTY on a fresh home, which made the
  divergence rail vacuous by default (empty viewer vs empty prompt passes forever). Every rail in the
  new test file plants content and asserts it was non-trivial before comparing, and the viewer names
  its own mechanism ("always: true in a skill's SKILL.md frontmatter") instead of rendering blank.
- [2026-08-18][PEP-10] DISCOVERY: `project_context.write_overview` swallows its own `OSError` and
  reports failure as a bare `False`; because the write is atomic the previous text survives intact. A
  caller that ignored that `False` would render "Saved" over a silently discarded edit, so
  `write_instruction` raises `InstructionWriteError` and the PUT answers 4xx/5xx with a reason — never
  `ok: true`. The frontend keeps the draft on screen on failure, since it is the user's only copy.
- [2026-08-18][PEP-10] DEVIATION: SP8.1 says the viewer goes in the Capabilities area; it shipped as a
  section inside `#/settings/legibility` instead (owner-fenced to `web/src/pages/settings/`). It is
  searchable from the Settings home tile via added "always-on conventions" search text. Named
  `AlwaysOnConventions.tsx`, not `*Panel.tsx`, because `panelHeadingLevel.test.tsx` correctly treats
  every `*Panel.tsx` as a sub-route page requiring a `<PanelHeader>` h1 — this is a section, not a page.

- [2026-08-25][PEP-3] **DONE** — persistent Store category/source rail + one card anatomy (PR #2065).
  `web/src/pages/apps/StoreSideRail.tsx` (CATEGORIES + SOURCES, each block led by its reset entry with
  live counts) renders from `AppsSection.tsx:614` inside `WorkbenchLayout`'s `isStore` branch — the
  surface `app/App.tsx:151` routes `case 'apps'` to. New URL dimension `?ssrc=`, keyed on
  `sourceGroup().key` so the rail and the existing source dividers agree. `appArt.ts` supplies the
  deterministic token gradient; the card's four shapes collapsed to one anatomy, so a hero-less card no
  longer reads as a failed image load. 17 tests in `storeRail.test.tsx`, all driving `AppsSection`
  rather than the rail directly.
  All four `done_when` clauses MET, each with a vacuity half: URL survival proven by rebuilding a
  router from the serialized search string alone; viewport split asserted **outside** the rail's own
  subtree (matching inside it produced a false failure first); both hero paths pinned via
  `data-art`; `aria-pressed` read off the accessibility tree, not class names.
  Falsification (re-run at integration): `{false && !isMobile && (` at `AppsSection.tsx:614` →
  10 failed / 7 passed of 17.
- [2026-08-25][PEP-3] **DEVIATION** — categories derive from `storeUniverse`, not the scope prose's
  "installed+catalog tags". The Store excludes installed apps, so an installed-derived category would
  advertise a filter whose grid comes back empty. No `done_when` clause is affected. **Owner: ratify
  the scope-prose correction or say categories should span installed apps too.**
- [2026-08-25][PEP-3] **DEVIATION** — the arrow-key cursor was dropped. `ui/popupItemRoles.test.tsx`
  flagged a cursor over a mapped list with no container role, and declaring `role="listbox"` would
  force `aria-selected`, removing the `aria-pressed` this atom's clause explicitly names. Tab +
  Enter/Space is the whole keyboard model.
- [2026-08-25][PEP-3] **DEVIATION** — the rail row was extracted to `ui/FilterRow.tsx` (+ its
  `.doc.ts`, required by `uiDocs.drift`) and `ui/FilterMenu.tsx` now renders it, output unchanged.
  Forced by `design/primitiveAdoption`: the row was a copy of FilterMenu's private `Row`, so
  extraction removed a duplicate instead of buying ratchet slack.
