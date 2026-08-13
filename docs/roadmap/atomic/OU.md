# ONBOARDING-UX — atomic plans

**Source plan:** [`ONBOARDING-UX`](../plans/ONBOARDING-UX.md)  
**Code:** `OU`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `OU-1` | ✅ | Extend onboarding state backend (step/provider/first_success fields + POST write path) | — | additive fields (step, provider_chosen/essentials, first_success) persist to entity_settings/onboarding.json, survive mid-flow reload, old clients tolerant-read; POST /api/onboarding/state does a partial merge (NOT the config PATCH allowlist, per §2.1 entity-state rule) |
| `OU-2` | ✅ | Essential-apps in-flow onboarding step (model required; search/speech/channel opt-in) | `OU-1` | fresh dev home (PERSONALCLAW_FIRST_PARTY_APPS_DIR fixture): model+search installable and the model bindable entirely in-flow (install -> key -> Test -> chat binding, reusing the 3 existing APIs); skipping all but model still reaches first-success; per-app install consent preserved; no auto-install anywhere |
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

**Status:** done

Session 1 T1.1; Contracts C1 (onboarding state: extends GET /api/onboarding, handler api_onboarding; new POST /api/onboarding/state)

**Done when:** additive fields (step, provider_chosen/essentials, first_success) persist to entity_settings/onboarding.json, survive mid-flow reload, old clients tolerant-read; POST /api/onboarding/state does a partial merge (NOT the config PATCH allowlist, per §2.1 entity-state rule)

**Shipped:** new `personalclaw/onboarding.py` store over `entity_settings/onboarding.json` —
`{step, essentials: {model, search, speech, channel}, first_success: {knowledge, trigger, loop}}`.
`load_onboarding_state()` sanitizes per field and never raises (missing file, corrupt JSON,
non-object JSON, absent fields, wrong types, retired step value → that field's default, siblings
kept); `merge_onboarding_state()` merges partially at BOTH levels and rejects unknown/mistyped
keys with `ValueError`. `GET /api/onboarding` returns the three new fields alongside the existing
readiness triple (additive — an old client reading only `needs_model`/`has_*` is unaffected);
`POST /api/onboarding/state` is the write path, deliberately not the `_EDITABLE_CONFIG` PATCH
allowlist. 35 tests in `tests/test_onboarding_state.py`, including a rail that every declared step
is writable and one asserting nothing onboarding-shaped reached `AppConfig`/`_EDITABLE_CONFIG`.

**Deviations from C1** (both recorded in the module docstring and the plan's Execution log): the
middle step id is `essentials`, not `provider` — the 2026-07-26 amendment (ruling a) re-scoped that
step and renamed the field, so the step id now agrees with the field it fills; and `name`/`completed`
are NOT stored, because C1's "existing" annotation was inaccurate against code (the GET never
returned them; the name lives in server identity and `onboarded` is derived from it being non-empty).

**Remainder — the fields' real-path writers are downstream atoms, by the DAG:** `OU-2` writes
`step` + `essentials` from the essential-apps step, `OU-3` writes `first_success` as each "try one"
card completes, `OU-4` reads `step` to resume. No `web/` change shipped here (`OnboardingState` in
`web/src/lib/api.ts` gains the fields when OU-2 consumes them), so nothing was added to the
frontend that no component reads.

### `OU-2` — Essential-apps in-flow onboarding step (model required; search/speech/channel opt-in)

**Status:** done

Session 1 T1.2, re-scoped to T1.2r by the 2026-07-26 Amendment (ruling a); Design 'Guided first run'; catalog-driven via apps/catalog.py first-party source

**Done when:** fresh dev home (PERSONALCLAW_FIRST_PARTY_APPS_DIR fixture): model+search installable and the model bindable entirely in-flow (install -> key -> Test -> chat binding, reusing the 3 existing APIs); skipping all but model still reaches first-success; per-app install consent preserved; no auto-install anywhere

**Shipped:** `web/src/app/onboarding/EssentialsStep.tsx` replaces the old `model` readiness step as
step 2 of the stack (`name → essentials → ready`); the fix-or-skip `ModelStep` is DELETED, its two
states folded into the model lane. Four lanes — model (required), search, speech, channel — are
rendered from `GET /api/apps/catalog`, each card disclosing the Store's own
`PermissionList`/`CronConsentList` before offering Install. The model lane completes in-flow over
three EXISTING endpoints: `POST /api/apps` → `POST /api/model-providers` (+ `/test`) →
`PUT /api/models/active/chat`. `step` and `essentials` are finally written (OU-1's remainder), each
lane patching only its own field. The install-consent components moved to a new
`web/src/pages/apps/installConsent.tsx` so onboarding and the Store share ONE consent surface
without the onboarding flow statically importing the lazily-loaded Store page. 32 frontend tests
(`essentialsStep.test.tsx`, `onboardingProgress.test.tsx`) + 2 backend
(`test_app_catalog.py`).

**Backend, minimal and additive:** `CatalogEntry.providerCapabilities` (from
`provider.capabilities`). `providerType` alone cannot separate a chat model from a speech model —
faster-whisper (`stt`) and piper-tts (`tts`) are both `providerType: "model"` — so without it the
model lane would offer a transcription app as a chat provider and dead-end at binding. Verified
live: the model lane lists 15 chat apps, and Whisper/Piper appear under Speech.

**Deviations:** (1) the plan's "chat binding API" is `PUT /api/models/active/{use_case}`, NOT
`PUT /api/prompts/bindings` (which binds a PROMPT to a use case); the active-model path is what
`needs_model`/`can_resolve_use_case('chat')` actually reads. (2) The model lane is required for
`Continue` but a quiet "Set up later" escape remains, per the Design's "skippable at every step" —
required to CONSIDER, not a wall. (3) `ScanReport`'s `text-negative`/`text-positive` were corrected
to `text-danger`/`text-ok` because relocating that code would otherwise have GROWN the
inert-utility allowlist, which the rail forbids.

**Remainder — none for this atom.** `first_success` is still unwritten (OU-3 owns it) and nothing
reads `step` to resume yet (OU-4), exactly as the DAG orders it.

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

Session 3 T3.3 (Wave 2); Contracts C2; existing ChannelDelivery seam; apps-repo slack renderer (minimal)

**Done when:** the same brief fields (tool + blast-radius line) flow through ChannelDelivery.request_approval payloads as additive meta; apps-repo slack renderer minimally updated to show what it can today; dashboard remains the rich surface

### `OU-10` — Replayable product tour component + Discover 'Replay the tour' card

**Status:** todo

2026-07-26 Amendment Session 5 T5.1 + T5.2 (rulings b + c); PlanningWalkthrough.tsx is the pattern donor; Discover named as the progressive-disclosure arm

**Done when:** web/src/app/onboarding/ProductTour.tsx spotlight tour (rail -> chat -> inbox -> approvals -> settings) runs post-onboarding end-to-end, launched from the done-screen; Esc exits anywhere leaving a fully working app; reduced-motion honored; zero requests logged for tour progress (no telemetry); re-launchable from DiscoverPage 'Replay the tour' card; S2 auto-pin behavior unchanged

### `OU-11` — Stranger-validation facilitator kit + 3 think-aloud sessions

**Status:** todo

Session 4 T4.1 + T4.2 + V4 (Wave 2); Design 'Stranger validation'; Owner task 1 (recruit + host 3 strangers)

**Done when:** docs/maintainers/usability-kit.md self-contained (facilitator script, consent note, observation sheet; dry-run on self recorded); 3 sessions run (OWNER recruits/hosts strangers); fix-now list (<=1 day) empty by session close; issues filed labeled ux-finding; first-success timing delta vs S1 baseline recorded

