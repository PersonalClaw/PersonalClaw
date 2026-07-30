# Plan: Multi-Tenant Entity Readiness — The Harness as a Good Citizen of Shared Stores

**Status:** IN PROGRESS — Session 1 (username identity + author attribution) shipped 2026-07-28;
Session 2 (mine-vs-everyone filters) shipped 2026-07-28; Session 3 (contributor provenance
+ owner-weighted ranking) shipped 2026-07-30 — §2.4 satisfied by inspection. Sessions 4-5
remain gated on AUTOMATION-SUBSTRATE.
Rescoped 2026-07-14 — harness-side scope
**Created:** 2026-07-14
**Wave:** 0+3 — Sessions 1-3 (owner identity + per-entity multi-user tolerance) have no dependencies and can start today; Sessions 4-5 (trigger-store provider seam + proof-of-concept trigger-provider app) gate on AUTOMATION-SUBSTRATE steps 1-3 (`triggers.json` + one `TriggerService` — build one seam, not four).
**Depends on:** AUTOMATION-SUBSTRATE steps 1-3 (Sessions 4-5 only). Sessions 1-3 depend on nothing.
**Research:** `docs/roadmap/research/multi-tenancy-entity-audit.md` (code audit: seam inventory, tenancy-readiness matrix, harness-side readiness gaps) + `docs/roadmap/research/team-shared-harness-research.md` (verified ecosystem findings on shared stores).
**Scope:** Make PersonalClaw's entities behave correctly when a pluggable provider is **multi-tenant in nature** — a task provider whose tasks aren't all assigned to this harness's owner, a trigger store containing triggers created by other people, a memory provider returning memories contributed by others. The harness recognizes two or more usernames circulating in the records it handles and treats each sensibly per entity. Sharing semantics, permissions, and coordination live at the shared-store / application design level — the harness only needs to behave correctly as one client among many.

---

## Research Integration

The research corpus (`team-shared-harness-research.md`) documents how the ecosystem's shared stores and multi-tenant services work; the harness deliberately implements only the **CLIENT side** of those patterns — it consumes shared stores well, it does not build them. The one pattern adopted directly: provenance on foreign-attributed content is **metadata, not instructions** — labeled in recall, fenced in prompts, never authoritative.

---

## Overview

Exactly one entity has a full storage-provider seam today — tasks (`tasks/provider.py TaskProvider` + `tasks/registry.py` routing; an external provider app works with zero core edits). Memory has a provider contract (`memory_providers/base.py`). Triggers get their seam after AUTOMATION-SUBSTRATE unifies four stores into `triggers.json` behind one `TriggerService`. What none of these seams have is **multi-user tolerance**: no record carries a "who," so a provider returning records attributed to other people would have them silently treated as the owner's own — foreign tasks counted as the owner's work, foreign triggers armed and fired on this machine, foreign memories ranked as if the owner wrote them.

This plan closes that gap harness-side:

1. **A minimal owner identity.** First-boot onboarding asks for a **username** alongside the existing display name (`dashboard.user_name`). The username is the harness owner's identity string — an opaque, stable string used for attribution. (Eventually this can be provisioned by an SSO or enterprise login when tailored to a team; that is a provisioning change, not a schema change.)
2. **Optional attribution fields** on entity records — author/assignee/contributor strings that default to the owner's username. No identity class hierarchy, no delegation model, no id minting: records may carry a username that isn't mine, and the harness handles that sensibly.
3. **Per-entity tolerance semantics** (§2): tasks display and filter by assignee, triggers arm only the owner's, memory recall labels contributors and weights the owner's memories higher.
4. **The trigger-store provider seam** (§3) so an app can be a trigger source, plus **one proof-of-concept app** (§4) that registers triggers which autonomously fire a workflow, an automation, a prompt, and an action — validating the integration path end-to-end.

**Soul guardrail:** PersonalClaw stays personal — one human, one home, plain local files. Every attribution field is optional and defaults to the owner; with no multi-tenant providers installed there is zero behavior change (a migration test proves a pre-plan store round-trips with only the new defaults). Execution never leaves the machine: whatever a store holds, this harness fires only what its owner created, under all local policy (capability allowlists, budgets, kill switch).

---

## 1. Owner Identity: a Username at First Boot (Session 1)

- **Onboarding** asks for a username alongside the display name (the existing `dashboard.user_name` "Operator Name" step in `web/src/app/Onboarding.tsx`). New config field `dashboard.username`: lowercase-ish opaque string, suggested from the display name, editable in Settings → Account (`AccountPanel.tsx`).
- **Config wiring** through all four points (the established checklist): `_meta(label, help)` on the field, `AppConfig.load()` explicit mapping, `to_dict()`, `_EDITABLE_CONFIG` allowlist + FE `api.ts` + panel.
- **Semantics:** the username is an attribution string, not a credential. Changing it is a rename that affects future writes only — existing records keep the string they were written with. Empty username (pre-existing installs that never re-onboard) degrades to today's behavior: writes carry no attribution and everything is treated as the owner's.
- **Attribution fields, everywhere cheap, defaulting to the owner** — all additive, plain strings:
  - `Task.author` / `Task.assignee` + `TaskComment.author` finally set (today an empty free string nobody fills).
  - Memory writes stamped with the owner's username (contributor field on the record).
  - Where schemas are still forming (AUTOMATION-SUBSTRATE's `triggers.json` rows, WORKFLOWS-V2 ledger/journal rows), **reserve optional author/contributor string fields** at design time — cheap now, breaking later. That is the entire footprint in other plans.
- **Future note (one line):** when tailored to a team, the username can be provisioned by SSO/enterprise login instead of first-boot entry.

---

## 2. Per-Entity Multi-User Tolerance Semantics (Sessions 2-3)

What "handles it sensibly" means, per entity:

### 2.1 Tasks (Session 2)

A multi-tenant `TaskProvider` may return tasks assigned to other users. The harness:

- **Displays assignee** on task rows/detail (username chip; owner's own tasks show no chip or "me").
- **Filters "mine vs everyone"** in task views (default: everyone visible, mine emphasized).
- **Never treats foreign tasks as the owner's work items:** Home widgets, ready-task counts, "next task" pickers, and any agent work-selection count and select **only tasks assigned to (or authored by, when unassigned) the owner**. A fixture multi-tenant provider in the test suite proves the counters.
- Writes through the registry stamp `author` (and default `assignee`) with the owner's username.

### 2.2 Triggers (Session 4, with the seam)

A shared trigger store may contain triggers created by others. The harness:

- **Arms and fires ONLY the owner's triggers** — the filter is `author == owner username` at arm time, enforced structurally in `TriggerService` (a foreign row cannot tick, not "is skipped").
- **Foreign triggers are visible and inert:** rendered read-only in the Automations surface (author chip, no enable/edit/delete), purely informational.
- Owner's triggers from a provider fire under all normal local policy — capability allowlists, budgets, kill switch. Provider-sourced display content (names, descriptions) entering prompts rides the existing `fence_untrusted` mechanism; nothing new.

### 2.3 Memory (Session 3)

A shared memory provider may return memories contributed by others. The harness:

- **Recall carries contributor provenance:** hits from other contributors are labeled (the same labeled-provenance rendering used for cross-project recall — labels are metadata, not instructions) and fenced on the way into prompts.
- **Ranking weights the owner's own memories higher:** at equal relevance, owner-contributed memories order above foreign-contributed ones; locality affects ordering only, never admission.
- **Writes attribute the owner's username** so anything this harness contributes to a shared store carries provenance for everyone else.

### 2.4 Knowledge (near-free rider, inside Session 3)

Where federated knowledge search hits already render a source label, a provider-supplied contributor rides that label unchanged — string passthrough, no new machinery. Nothing further.

---

## 3. The Trigger-Store Provider Seam (Session 4, core — gated)

Gated on AUTOMATION-SUBSTRATE steps 1-3 (`triggers.json` + one `TriggerService`), per the audit's difficulty ordering — otherwise we'd build four seams and throw three away.

- Extract a **`TriggerStore` interface** from the unified service's persistence (list/get/upsert/delete + change-notification); native impl wraps `triggers.json` with all its preserved conventions (fcntl, mtime-sync, atomic_write).
- New provider type **`trigger`** enters `PROVIDER_TYPES` (apps/manifest.py) **and** a `TriggerTypeHandler` in providers/registry.py **in the same commit** — `test_manifest_types_match_handlers` guards the known bug class.
- A trigger provider contributes **trigger rows, never execution** — the local `TriggerService` does all firing, and only for rows whose `author` is the owner (§2.2).
- SDK: `sdk/triggers.py` re-exports the store/provider contracts so third-party trigger sources are buildable like any provider app.

---

## 4. Proof-of-Concept Trigger-Provider App (Session 5, app)

One simple app (apps repo or third-party-apps/) that validates the integration path end-to-end — deliberately not a product:

- Registers as a `trigger` provider and contributes trigger rows to the harness.
- Its owner-authored triggers **autonomously fire** each of: a workflow, an automation, a prompt, and an action — proving an app-registered trigger can drive every action kind on the harness under local policy.
- Ships fixture rows under a second username ("alice") to prove the visible-but-inert rendering and the structural cannot-arm filter.
- Install is manifest-only (zero core edits) — the same bar the tasks seam already meets.

---

## Provider & Config Plug-in Map

- **Providers:** tasks use the existing `TaskTypeHandler` (zero core edits — the proof the pattern works); memory uses the existing `memory` type; triggers get the NEW type `trigger` + handler in the same commit (§3).
- **SDK:** `sdk/triggers.py` (TriggerStore/provider contracts) re-exported for third-party apps, `SDK_VERSION` conventions as with `sdk/sync.py`.
- **Config:** `dashboard.username` wired through the four points (§1). Anything provider-specific (backend URL, credentials) lives in per-app `ProviderSettings` + the credential store — NOT core config.
- **Untrusted content:** foreign-attributed content enters prompts only through the existing `fence_untrusted` with a provenance source; no new screening machinery.
- **Egress/secrets:** provider apps follow the standing app-platform rules (`net.fetch` under the CONNECTOR profile, `save_credential`); nothing new here.

---

## Implementation Effort

**~5 sessions** (4 core, 1 app).

| # | Side | Session | After |
|---|---|---|---|
| 1 | core | Username at first boot (onboarding + AccountPanel + four-point config wiring) + attribution fields defaulting to owner (`Task.author`/`assignee`, `TaskComment.author`, memory-write contributor) + reserve optional author/contributor strings in still-forming schemas (triggers.json, v2 ledger rows) + invisible-single-user migration test | nothing (Wave 0) |
| 2 | core | Task multi-user tolerance: assignee display, mine-vs-everyone filter, MINE-only Home widgets/ready-task counts/work-selection, fixture multi-tenant provider in tests | Session 1 |
| 3 | core | Memory contributor provenance: labeled + fenced recall, owner-weighted ranking, attributed writes; knowledge federated-hit contributor label passthrough (near-free) | Session 1 |
| 4 | core | `TriggerStore` seam + provider type `trigger` (+handler, same commit) + owner-filter at arm time + foreign-trigger read-only rendering | AUTOMATION-SUBSTRATE steps 1-3 (Wave 3) |
| 5 | app | Proof-of-concept trigger-provider app: owner triggers autonomously fire a workflow / automation / prompt / action; second-username fixtures prove inert display + cannot-arm | Session 4 |

Sessions 1-3 are Wave 0 (no dependencies, ship value alone); Sessions 4-5 are Wave 3, after the substrate's trigger unification.

---

## Risks

| Risk | Mitigation |
|---|---|
| Attribution fields ripple into stores and break single-user installs | All fields optional + defaulting to the owner's username; migration round-trip test; Session 1 gated on zero behavior change in the full regression suite |
| A foreign trigger fires on this harness | Owner-filter enforced structurally in `TriggerService` at arm time (a foreign row cannot tick); adversarial test with a provider whose rows are majority-foreign |
| Foreign tasks pollute the owner's work signals | MINE-only counters/pickers proven against the fixture multi-tenant provider (Home widgets, ready counts, agent work-selection) |
| A foreign memory steers prompts | Provenance labels are metadata + `fence_untrusted` on the way in; ranking down-weights, never admits-by-authority; adversarial "ignore prior instructions" test |
| Username rename orphans attribution | Username is an opaque stable string; rename affects future writes only, old records keep their string — documented, tested |
| Trigger seam built before the substrate unifies (four seams instead of one) | Session 4 hard-gated on AUTOMATION-SUBSTRATE steps 1-3 |

---

## Success Criteria

1. **Invisible single-user:** with no multi-tenant providers installed, the full regression suite and a byte-level store round-trip show zero behavior change after Sessions 1-3 — every new field silently defaults to the owner's username.
2. **Identity lands:** first boot asks for a username beside the display name; it persists in `config.json`, is editable in Settings → Account, and stamps subsequent task/comment/memory writes.
3. **Task tolerance:** a fixture `TaskProvider` returning tasks assigned to "alice" and "bob" renders assignees correctly, the mine-vs-everyone filter works, and Home widgets/ready-task counts/agent work-selection count only the owner's tasks.
4. **Trigger tolerance:** a trigger provider containing another username's triggers shows them read-only and structurally cannot arm them (test at the `TriggerService` seam, not the UI); the owner's triggers from the same provider arm and fire normally.
5. **PoC end-to-end:** the proof-of-concept app installs manifest-only with zero core edits, registers triggers on the harness, and each owner-authored trigger autonomously fires its workflow/automation/prompt/action under local policy.
6. **Memory tolerance:** recall over a shared-provider fixture returns foreign memories labeled with their contributor and fenced (a hit whose text says "ignore prior instructions" steers nothing); at equal relevance the owner's memories rank first; writes carry the owner's username.

---

## Execution log

- [2026-07-28][S1] DONE (§1 — owner identity + attribution): new `identity.py` + `dashboard.username` + task/comment attribution. (a) **The slug rule (OWNER RULING, 2026-07-28: strict slug).** `slugify_username()`: NFKD-fold accents → lowercase → `[a-z0-9_-]` (everything else → `-`) → collapse repeats → trim separators → cap 32 chars → re-trim. `"Keyur Golani"` → `keyur-golani`, `"José"` → `jose`, `"a@b.com"` → `a-b-com`. Strict on purpose: this string lands in JSON records (and later shard filenames + sync payloads) effectively forever, so a permissive rule would bake trailing spaces/emoji/full email addresses into records nobody can rename. Unusable input yields `""` — never a fabricated `user-1`. Normalization runs at BOTH the write boundary and on config LOAD, so a hand-edited `config.json` can't smuggle a non-canonical handle into records. (b) **Config 4-point wiring** — `DashboardConfig.username` + `_meta`, `AppConfig.load()` (via `_slug_username`), `to_dict` (asdict), and the PUT handler write+read. (c) **Attribution** — `Task.author` (NEW field; see the correction below) stamped from `current_username()` at create, explicit author wins; `TaskComment.author` now prefers the handle over the historical `"user"` placeholder (which remains the fallback so existing readers see no change). (d) **FE** — Settings → Account gained a Username field (placeholder suggests the slug of the display name; shows the SERVER's canonical value after save, so typing `"  Typed  MESSY Name!!  "` visibly becomes `typed-messy-name`), using the shared `Button` primitive. (e) **Semantics, tested:** a rename affects FUTURE writes only — rewriting history to match a new handle would falsify the very record attribution exists to preserve; empty handle = no attribution = today's behavior; `current_username()` never raises (attribution decorates a write and must not be why one fails). 31 tests in `test_identity.py`.
- [2026-07-28][S1] PREMISE CORRECTION (E1, confirmed): the plan states at §1 that "`Task.author` / `Task.assignee` + `TaskComment.author` **finally set** (today an empty free string nobody fills)" — true for `assignee` and `TaskComment.author`, but **`Task.author` did not exist** (`tasks/models.py`: `assignee` only). Added additively with a `""` default per AGENTS.md ("stay additive: defaults on new fields, tolerant reads"), so pre-attribution task JSON loads cleanly with `author == ""` (regression-tested).
- [2026-07-28][S1] TWO BUGS FOUND BY TESTING/VALIDATION, both fixed: (1) **`Task.from_dict` builds the dataclass field-by-field**, so `author` was silently DROPPED on every read — the round-trip test caught it (a task created with an author read back as `""`); added to the constructor call. (2) **The `PUT /api/dashboard/config` handler carries its OWN field allowlist**, separate from the dataclass — so after all four config wiring points were correct, the endpoint still 400'd `Unknown fields: {'username'}`. Only driving the real endpoint caught this; both now have regression tests (the second asserts the allowlist by source-grep, since a unit test on the dataclass can't see it).
- [2026-07-28][S1] SCOPE TRIMS (logged, not silent): (a) the plan's "**reserve optional author/contributor string fields** in AUTOMATION-SUBSTRATE's `triggers.json` rows and WORKFLOWS-V2 ledger/journal rows" is **unstartable** — neither store exists (`triggers.json` appears nowhere in `src/`; there is no v2 ledger). The plan itself calls that "the entire footprint in other plans", so it is dropped rather than faked; those plans should add the field when they create those rows. (b) The **memory-write contributor stamp** is deferred to MEMORY-GRAPH-AND-VAULT S1, which is already extending `vector_memory.py`'s `_MIGRATIONS` ladder to v7 (owner-approved) — adding a `contributor` column here would mean two separate schema touches on the same table in the same sprint. Doing it there is one migration instead of two.
- [2026-07-28][S1] Gate: `make lint` green (519 files) · `make test` **8345 passed (50.6s)** · web typecheck + 251 vitest + build green. The primitive-adoption ratchet tripped on my Save button and was fixed by ADOPTING `Button` (the two older Save buttons in that panel remain hand-rolled — pre-existing, not touched), never a baseline bump. NOTE: one intermediate suite run showed 157s + a `TestOnDoneTimeout` failure; that was contention from a concurrent web build, confirmed by isolating the test (8.2s, 6 passed) and re-running the suite alone (50.6s green) — same false alarm pattern as the CE-S5 lesson. Validated as-a-user on :10025: messy handle normalized + persisted to config.json, non-string rejected 400, task author + comment author stamped, rename kept the old record's author while the new one got the new handle, Settings → Account round-trips through the real endpoint, zero console errors. **Remaining: S2** (task mine-vs-everyone filters + MINE-only counters), **S3** (memory contributor provenance + owner-weighted ranking — inherits the deferred stamp above); S4-S5 gate on AUTOMATION-SUBSTRATE.

### 2026-07-28 — Session 2 (§2.1 tasks: mine vs everyone) — DONE

`Task.belongs_to(username)` + an owner-only default on `ready_tasks` + a `mine`
filter on the list endpoint + attribution in the shared task meta line.

**The load-bearing guarantee, and where it lives.** §2.1 requires that Home widgets,
ready counts, "next task" pickers, and agent work-selection count and select only the
owner's tasks. All of those already funnel through `registry.ready_tasks` — the ready
endpoint and the agent's `task_ready` tool are its only callers — so the filter went
THERE with `mine_only=True` as the default, rather than at each surface. A surface
that forgets to filter is the failure mode; with the default inverted, a surface has
to explicitly ask for everyone's work to see it.

**Readiness is still computed over the FULL task set, then filtered.** A task of mine
blocked by a colleague's unfinished prerequisite is genuinely not startable, and
filtering before reconciliation would report it as ready. Test-locked
(`test_readiness_is_computed_over_the_full_set`).

**Ownership semantics** (`belongs_to`): assignee decides when set — who DOES it beats
who wrote it, in both directions — and an unassigned task falls back to its author,
because "I wrote it and nobody picked it up" is still my work. Two deliberate
"everything is mine" cases: an **unattributed** task (no author, no assignee) belongs
to the owner, because every task written before attribution existed looks like that
and treating them as foreign would empty the counters on upgrade; and with **no
username configured** every task is the owner's, which is both today's behavior and
the honest answer — with no identity there is nobody else for a task to belong to.

**Surfaces.** `GET /api/tasks?mine=1` resolves the owner server-side rather than
taking a name from the client, and means "assigned to me, or authored by me and
unassigned" — which the pre-existing `assignee=` filter cannot express. `total` is
recomputed for the filtered set (otherwise the UI reads "1 of 2" over a one-row list),
and every response now carries `owner` so the frontend can label rows. `GET
/api/tasks/ready?everyone=1` is the opt-in shared-board view.

**FE.** The mine/everyone control is a `FilterMenu` section that **only appears when
someone else's work is actually present** — on a single-user install it could only
ever be a no-op, and a control that does nothing is worse than no control. Attribution
was added to `MetaLine`, which the list row and the card both render, so one edit
covers both views; the list row previously showed no assignee at all, which is a real
gap on a shared board. It renders only when someone is named, since "@you" on every
row of a solo install is noise.

Tests: `tests/test_task_ownership.py`, 16 cases. Validated as a user on an isolated
dev home with a mixed board (mine / Dana's / unassigned): writes stamped the author
automatically, `mine=1` returned 2 of 3, and `/api/tasks/ready` excluded Dana's task
while `?everyone=1` returned all three. Full suite 8696 passed; lint clean; web
typecheck + 268 vitest green. **Remaining: Session 3** (§2.3 memory contributor
provenance + owner-weighted ranking, §2.4 knowledge label passthrough) and Sessions
4-5 (the trigger-store provider seam, gated).

### 2026-07-30 — Session 3 (§2.3 memory contributor provenance + owner-weighted ranking) — DONE

Migration **v9** adds `contributor` to both record tables, stamped at the single INSERT of
each; recall labels foreign records and fences the label as metadata; ranking prefers the
owner's own memories in ORDER only. §2.4 is satisfied by inspection (below).

**The deferred stamp had fallen through TWICE and this session owns it.** S1's log deferred
the memory contributor stamp to MEMORY-GRAPH-AND-VAULT S1 ("one migration instead of two");
that plan's S1 log then re-deferred it to its own §4.2 (Session 3, unbuilt) as "dead schema
nothing reads". Both deferrals were individually reasonable and the net effect was a
capability owned by nobody. Verified before starting: no `contributor` column existed on
either table. It lands here, where there is finally a reader for it.

**One stamp site, not nine.** `_write_semantic`'s upsert is the ONLY statement that creates
a `semantic_memory` row, and `write_episodic`'s INSERT the only one for episodes — so both
stamps cover every typed writer (`put`, `write_lesson`, facets, consolidation, promotion,
migration, import) plus the HTTP endpoint, and no future writer can forget. Verified by
driving `write_lesson` and the PUT endpoint, not by reading call sites.

**A caller cannot spoof a contributor.** `PUT /api/memory/semantic` reads four named keys
and silently drops the rest, so a `contributor` in the body never reaches the store; the
server stamps `current_username()`. Confirmed live — posting `"contributor":"dana"` stored
`keyur-golani`. The keyword-only override on `set_semantic` exists for internal callers
(import) only.

**`contributor` is NOT in the ON CONFLICT update.** Editing a shared record is not authoring
it. Reassigning on every touch would make the column mean "last writer" while claiming to
mean "contributor" — worse than no column. Test-locked: the owner edits Dana's record, the
value changes, the attribution does not.

**Ordering only, never admission — and where that boundary physically lives.** The owner
bonus is applied in the SORT KEY, deliberately not added to `score`, because `score > 0` is
the admission gate: a provenance bonus ahead of it could lift a zero-relevance record into
the result set, letting locality decide what the model SEES. Three tests pin the boundary
from both sides — owner wins a tie, a zero-relevance owner record is still excluded, and a
stronger foreign match still beats a weaker owner one. `_OWNER_RANK_BONUS = 0.05` sits under
one keyword-overlap step (0.1 after normalization), following the heat boost's
bounded-nudge precedent rather than the graph arm's deliberately-admitting one.
Also verified live that a *better-matching* foreign record correctly outranks the owner's.

**A REAL BUG I INTRODUCED AND THE SUITE CAUGHT — restore from any older snapshot silently
imported NOTHING.** `_MERGE_ALLOWED_TABLES` names columns explicitly; adding `contributor`
unconditionally meant a pre-v9 snapshot raised `no such column`, the handler logged and
SKIPPED the table, and the restore printed "Semantic Memory imported: 0" while looking like
it succeeded. Losing all memory on restore is far worse than losing a provenance column, so
the column is now opportunistic via `_both_have()` (checks BOTH databases). Verified both
directions: v9→v9 imports 6 semantic + 1 episodic with contributors intact; pre-v9→v9
imports cleanly with an empty contributor.

**Two other explicit allowlists that would have silently dropped provenance:** the vault
frontmatter emitter (`_FM_ORDER`) and the snapshot merge column lists. Both are now covered
by tests that assert the allowlist itself, since a unit test on the column can't see them.

**Three episodic READ paths omitted the column** (vector search, list, FTS search), so the
stamp landed but never reached recall. Found by asserting on `recall_with_provenance`'s
output rather than on the stored row — the stamp test passed while the feature didn't work.

**Fence budget:** the "(from …) is metadata" clause is added ONLY when a label is actually
present. The fence is paid on every injected turn, and `test_respects_cap` correctly caught
the unconditional version inflating the header by 315 chars. Content capping was never
wrong (476 ≤ 500) — the fence was.

**§2.4 (knowledge label passthrough) — SATISFIED BY INSPECTION, no code.** The section reads
"*Where* federated knowledge search hits already render a source label, a provider-supplied
contributor rides that label unchanged — string passthrough, no new machinery. Nothing
further." That condition is already met: `provider` is an opaque string set at ingest
(`knowledge/store.py:239`), defaulted on read (`store.py:963`), carried verbatim onto search
hits (`knowledge/retrieval.py:154`) and context cards (`handlers/knowledge.py:1186`), and
rendered as a pill (`KnowledgeDetail.tsx:367`, `KnowledgeListPage.tsx:645`).
`KnowledgeItem.metadata` is a second open carrier. A provider expressing a contributor works
today. Making it a *separately rendered token* would be new machinery, which §2.4 explicitly
forecloses — so nothing was built, deliberately.

**Upgrade safety:** existing rows are NOT back-stamped (no backfill) — a pre-column record
has genuinely unknown authorship, and inventing the current owner would falsify exactly what
the column exists to preserve, the same rule `identity.py` states for renames. An
unattributed record counts as the owner's for ranking, and with no username configured the
bonus is uniform, so a single-user install orders exactly as it does today (test-locked).

**Import preserves foreign provenance.** A record carrying a contributor keeps it; stamping
the importer would relabel a colleague's memory as the importer's own. Verified live through
`POST /api/memory/import`.

**Product surface:** `GET /api/memory/semantic` gains `contributor` (automatic — it
pass-throughs the row) plus a server-resolved `is_mine` flag, so the "is this mine?" rule
lives in one place instead of being re-derived in the client; the Memory Studio inspector
shows `Contributor: you | <handle>`.

**Validated as a user** on an isolated dev home with a mixed store (owner + an imported
"dana"): server stamping; spoof attempt stored the real owner; import preserved `dana`;
recall labeled the foreign record and episode with the metadata-not-instructions fence;
symmetric-key tie ordered the owner first while a better-matching foreign record correctly
won; snapshot→restore(replace) preserved both contributors; the inspector rendered
`Contributor: dana` and `Contributor: you`. **0 console errors, 0 gateway tracebacks.** (The
owner's real gateway on :10000 was left untouched; the CLI's gateway-running guard is global,
so the merge paths were exercised by calling `_merge_memory` directly.)

**Gates:** `make lint` clean (mypy 551 files) · backend **9118 passed** · web **283 passed**
+ typecheck + build. Tests: `tests/test_memory_contributor.py`, 38 cases.

**Remaining:** Sessions 4-5 (the trigger-store provider seam + a proof-of-concept trigger
provider app), still gated on AUTOMATION-SUBSTRATE steps 1-3 — `triggers.json` and
`TriggerService` do not exist yet.
