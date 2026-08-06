# ONBOARDING-UX — atomic plans

**Source plan:** [`ONBOARDING-UX`](../plans/ONBOARDING-UX.md)  
**Code:** `OU`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `OU-1` | ⬜ | Extend onboarding state backend (step/provider/first_success fields + POST write path) | — | additive fields (step, provider_chosen/essentials, first_success) persist to entity_settings/onboarding.json, survive mid-flow reload, old clients tolerant-read; POST /api/onboarding/state does a partial merge (NOT the config PATCH allowlist, per §2.1 entity-state rule) |
| `OU-2` | ⬜ | Essential-apps in-flow onboarding step (model required; search/speech/channel opt-in) | `OU-1` | fresh dev home (PERSONALCLAW_FIRST_PARTY_APPS_DIR fixture): model+search installable and the model bindable entirely in-flow (install -> key -> Test -> chat binding, reusing the 3 existing APIs); skipping all but model still reaches first-success; per-app install consent preserved; no auto-install anywhere |
| `OU-3` | ⬜ | First-success 'try one' cards (knowledge ingest+ask, reminder trigger, seeded loop) | `OU-1`, `OU-2` | each of the three cards executes a real flow and reaches its visible outcome on a fresh home in <2 min; failure path shows the error and offers a Settings deep-link when a real call fails despite a passing Test |
| `OU-4` | ⬜ | Onboarding done screen + resume + per-step skip + CLI setup pointer | `OU-1`, `OU-2`, `OU-3` | skip at any step lands in a working dashboard; re-entering onboarding resumes at the persisted step; personalclaw setup prints the dashboard-flow pointer when a browser is available (wizard unchanged); V1 recorded in Execution log (full flow <5 min, mid-flow reload, full-skip path, existing-home upgrade shows NO onboarding) |
| `OU-5` | ⬜ | NavRail progressive disclosure (starter/expert sections, auto-pin-on-visit, expert toggle) + URL-doctrine regression test | — | fresh home shows the starter rail; visiting an Everything surface via deep link/CommandPalette renders AND auto-pins it (test red if a deep link 404s/blanks under starter mode); expert-mode toggle in Appearance shows all permanently; upgrade fixture (onboarding-completed-before-this-version marker) defaults expert ON; keyboard-only, reduced-motion, and mobile-viewport passes hold |
| `OU-6` | ⬜ | EmptyState primitive + rollout to the 7 listed pages | — | web/src/ui/EmptyState.tsx exists and is applied to Loops, Workflows, Knowledge, Memory, Skills, Tasks, Triggers with one seeded working action each; copy in PRODUCT.md voice; visual check across both themes |
| `OU-7` | ⬜ | Blast-radius derivation (approvalMeta.ts pure function) — C2 read-only consumption | — | web/src/pages/chat/approvalMeta.ts maps tool name + existing risk + command-screening classification to writes/network/shell/readOnly chips; unit-tested against representative tools (bash, web_fetch, memory write, read-only); NO security-logic change (E4 if any gap tempts one) |
| `OU-8` | ⬜ | ApprovalCard redesign (what/why/blast-radius/scoped-remember) + toast compact variant | `OU-7` | ApprovalCard renders all four zones; useApprovalToasts gets the compact form; remember-scope (session/tool_always/no) persists via the existing approval-preference path; brief never advocates approval; risky+benign approvals driven as a user and README screenshots produced (feeds DISCOVERABILITY-LAUNCH asset list) |
| `OU-9` | ⬜ | Structured approval brief over the ChannelDelivery.request_approval seam | `OU-7`, `OU-8`, `EXT:CHANNEL-EXPANSION:ChannelDelivery.request_approval + apps-repo slack renderer consume the structured brief` | the same brief fields (tool + blast-radius line) flow through ChannelDelivery.request_approval payloads as additive meta; apps-repo slack renderer minimally updated to show what it can today; dashboard remains the rich surface |
| `OU-10` | ⬜ | Replayable product tour component + Discover 'Replay the tour' card | `OU-4`, `OU-5` | web/src/app/onboarding/ProductTour.tsx spotlight tour (rail -> chat -> inbox -> approvals -> settings) runs post-onboarding end-to-end, launched from the done-screen; Esc exits anywhere leaving a fully working app; reduced-motion honored; zero requests logged for tour progress (no telemetry); re-launchable from DiscoverPage 'Replay the tour' card; S2 auto-pin behavior unchanged |
| `OU-11` | ⬜ | Stranger-validation facilitator kit + 3 think-aloud sessions | `OU-4`, `OU-8`, `OU-10` | docs/maintainers/usability-kit.md self-contained (facilitator script, consent note, observation sheet; dry-run on self recorded); 3 sessions run (OWNER recruits/hosts strangers); fix-now list (<=1 day) empty by session close; issues filed labeled ux-finding; first-success timing delta vs S1 baseline recorded |

## Atom scopes

### `OU-1` — Extend onboarding state backend (step/provider/first_success fields + POST write path)

**Status:** todo

Session 1 T1.1; Contracts C1 (onboarding state: extends GET /api/onboarding, handler api_onboarding; new POST /api/onboarding/state)

**Done when:** additive fields (step, provider_chosen/essentials, first_success) persist to entity_settings/onboarding.json, survive mid-flow reload, old clients tolerant-read; POST /api/onboarding/state does a partial merge (NOT the config PATCH allowlist, per §2.1 entity-state rule)

### `OU-2` — Essential-apps in-flow onboarding step (model required; search/speech/channel opt-in)

**Status:** todo

Session 1 T1.2, re-scoped to T1.2r by the 2026-07-26 Amendment (ruling a); Design 'Guided first run'; catalog-driven via apps/catalog.py first-party source

**Done when:** fresh dev home (PERSONALCLAW_FIRST_PARTY_APPS_DIR fixture): model+search installable and the model bindable entirely in-flow (install -> key -> Test -> chat binding, reusing the 3 existing APIs); skipping all but model still reaches first-success; per-app install consent preserved; no auto-install anywhere

### `OU-3` — First-success 'try one' cards (knowledge ingest+ask, reminder trigger, seeded loop)

**Status:** todo

Session 1 T1.3; Design 'Guided first run' first-success step; Risks (cards degrade gracefully if a bound provider's real call fails)

**Done when:** each of the three cards executes a real flow and reaches its visible outcome on a fresh home in <2 min; failure path shows the error and offers a Settings deep-link when a real call fails despite a passing Test

### `OU-4` — Onboarding done screen + resume + per-step skip + CLI setup pointer

**Status:** todo

Session 1 T1.4 + V1; Design done screen (points at Inbox, bounciness slider, unlock-everything toggle); cli_setup.py one pointer line

**Done when:** skip at any step lands in a working dashboard; re-entering onboarding resumes at the persisted step; personalclaw setup prints the dashboard-flow pointer when a browser is available (wizard unchanged); V1 recorded in Execution log (full flow <5 min, mid-flow reload, full-skip path, existing-home upgrade shows NO onboarding)

### `OU-5` — NavRail progressive disclosure (starter/expert sections, auto-pin-on-visit, expert toggle) + URL-doctrine regression test

**Status:** todo

Session 2 T2.1 + T2.3 + V2; Contracts C4 (NavRail sections, prefs store); Design 'Progressive disclosure'

**Done when:** fresh home shows the starter rail; visiting an Everything surface via deep link/CommandPalette renders AND auto-pins it (test red if a deep link 404s/blanks under starter mode); expert-mode toggle in Appearance shows all permanently; upgrade fixture (onboarding-completed-before-this-version marker) defaults expert ON; keyboard-only, reduced-motion, and mobile-viewport passes hold

### `OU-6` — EmptyState primitive + rollout to the 7 listed pages

**Status:** todo

Session 2 T2.2; Contracts C3 (EmptyState props); Design 'Empty states as on-ramps'

**Done when:** web/src/ui/EmptyState.tsx exists and is applied to Loops, Workflows, Knowledge, Memory, Skills, Tasks, Triggers with one seeded working action each; copy in PRODUCT.md voice; visual check across both themes

### `OU-7` — Blast-radius derivation (approvalMeta.ts pure function) — C2 read-only consumption

**Status:** todo

Session 3 T3.1 (Wave 2); Contracts C2 (approval brief data model: blastRadius/rememberScope added to existing ApprovalSegment)

**Done when:** web/src/pages/chat/approvalMeta.ts maps tool name + existing risk + command-screening classification to writes/network/shell/readOnly chips; unit-tested against representative tools (bash, web_fetch, memory write, read-only); NO security-logic change (E4 if any gap tempts one)

### `OU-8` — ApprovalCard redesign (what/why/blast-radius/scoped-remember) + toast compact variant

**Status:** todo

Session 3 T3.2 + V3 (Wave 2); Design 'Approval brief'; copy-sensitive surface reviewed against security voice

**Done when:** ApprovalCard renders all four zones; useApprovalToasts gets the compact form; remember-scope (session/tool_always/no) persists via the existing approval-preference path; brief never advocates approval; risky+benign approvals driven as a user and README screenshots produced (feeds DISCOVERABILITY-LAUNCH asset list)

### `OU-9` — Structured approval brief over the ChannelDelivery.request_approval seam

**Status:** todo

Session 3 T3.3 (Wave 2); Contracts C2; INTEGRATION-ARCHITECTURE §3.5 (existing ChannelDelivery); apps-repo slack renderer (minimal)

**Done when:** the same brief fields (tool + blast-radius line) flow through ChannelDelivery.request_approval payloads as additive meta; apps-repo slack renderer minimally updated to show what it can today; dashboard remains the rich surface

### `OU-10` — Replayable product tour component + Discover 'Replay the tour' card

**Status:** todo

2026-07-26 Amendment Session 5 T5.1 + T5.2 (rulings b + c); PlanningWalkthrough.tsx is the pattern donor; Discover named as the progressive-disclosure arm

**Done when:** web/src/app/onboarding/ProductTour.tsx spotlight tour (rail -> chat -> inbox -> approvals -> settings) runs post-onboarding end-to-end, launched from the done-screen; Esc exits anywhere leaving a fully working app; reduced-motion honored; zero requests logged for tour progress (no telemetry); re-launchable from DiscoverPage 'Replay the tour' card; S2 auto-pin behavior unchanged

### `OU-11` — Stranger-validation facilitator kit + 3 think-aloud sessions

**Status:** todo

Session 4 T4.1 + T4.2 + V4 (Wave 2); Design 'Stranger validation'; Owner task 1 (recruit + host 3 strangers)

**Done when:** docs/maintainers/usability-kit.md self-contained (facilitator script, consent note, observation sheet; dry-run on self recorded); 3 sessions run (OWNER recruits/hosts strangers); fix-now list (<=1 day) empty by session close; issues filed labeled ux-finding; first-success timing delta vs S1 baseline recorded

