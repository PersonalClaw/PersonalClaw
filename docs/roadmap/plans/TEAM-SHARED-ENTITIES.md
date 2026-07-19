# Plan: Multi-Tenant Entity Readiness — The Harness as a Good Citizen of Shared Stores

**Status:** PROPOSED (rescoped 2026-07-14 — harness-side scope)
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
