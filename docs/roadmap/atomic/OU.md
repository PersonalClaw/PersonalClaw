# ONBOARDING-UX — atomic plans

**Source plan:** [`ONBOARDING-UX`](../plans/ONBOARDING-UX.md)  
**Code:** `OU`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `OU-1` | ✅ | Extend onboarding state backend (step/provider/first_success fields + POST write path) | — | additive fields (step, provider_chosen/essentials, first_success) persist to entity_settings/onboarding.json, survive mid-flow reload, old clients tolerant-read; POST /api/onboarding/state does a partial merge (NOT the config PATCH allowlist, per §2.1 entity-state rule) |
| `OU-2` | ⬜ | Essential-apps in-flow onboarding step (model required; search/speech/channel opt-in) | `OU-1` | fresh dev home (PERSONALCLAW_FIRST_PARTY_APPS_DIR fixture): model+search installable and the model bindable entirely in-flow (install -> key -> Test -> chat binding, reusing the 3 existing APIs); skipping all but model still reaches first-success; per-app install consent preserved; no auto-install anywhere |
| `OU-3` | ⬜ | First-success 'try one' cards (knowledge ingest+ask, reminder trigger, seeded loop) | `OU-1`, `OU-2` | each of the three cards executes a real flow and reaches its visible outcome on a fresh home in <2 min; failure path shows the error and offers a Settings deep-link when a real call fails despite a passing Test |
| `OU-4` | ⬜ | Onboarding done screen + resume + per-step skip + CLI setup pointer | `OU-1`, `OU-2`, `OU-3` | skip at any step lands in a working dashboard; re-entering onboarding resumes at the persisted step; personalclaw setup prints the dashboard-flow pointer when a browser is available (wizard unchanged); V1 recorded in Execution log (full flow <5 min, mid-flow reload, full-skip path, existing-home upgrade shows NO onboarding) |
| `OU-5` | ⬜ | NavRail progressive disclosure (starter/expert sections, auto-pin-on-visit, expert toggle) + URL-doctrine regression test | — | fresh home shows the starter rail; visiting an Everything surface via deep link/CommandPalette renders AND auto-pins it (test red if a deep link 404s/blanks under starter mode); expert-mode toggle in Appearance shows all permanently; upgrade fixture (onboarding-completed-before-this-version marker) defaults expert ON; keyboard-only, reduced-motion, and mobile-viewport passes hold |
| `OU-6` | ⬜ | EmptyState primitive + rollout to the 7 listed pages | — | web/src/ui/EmptyState.tsx exists and is applied to Loops, Workflows, Knowledge, Memory, Skills, Tasks, Triggers with one seeded working action each; copy in PRODUCT.md voice; visual check across both themes |
| `OU-7` | ✅ | Blast-radius derivation (approvalMeta.ts pure function) — C2 read-only consumption | — | web/src/pages/chat/approvalMeta.ts maps tool name + existing risk + command-screening classification to writes/network/shell/readOnly chips; unit-tested against representative tools (bash, web_fetch, memory write, read-only); NO security-logic change (E4 if any gap tempts one) |
| `OU-8` | ✅ | ApprovalCard redesign (what/why/blast-radius/scoped-remember) + toast compact variant | `OU-7` | ApprovalCard renders all four zones; useApprovalToasts gets the compact form; remember-scope (session/tool_always/no) persists via the existing approval-preference path; brief never advocates approval; risky+benign approvals driven as a user and README screenshots produced (feeds DISCOVERABILITY-LAUNCH asset list) |
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

**Status:** done

Session 3 T3.1 (Wave 2); Contracts C2 (approval brief data model: blastRadius/rememberScope added to existing ApprovalSegment)

**Done when:** web/src/pages/chat/approvalMeta.ts maps tool name + existing risk + command-screening classification to writes/network/shell/readOnly chips; unit-tested against representative tools (bash, web_fetch, memory write, read-only); NO security-logic change (E4 if any gap tempts one)

**Shipped:** new `web/src/pages/chat/approvalMeta.ts` —
`deriveBlastRadius({tool, risk?, readOnlyCommand?}) → BlastRadius | undefined`, where
`BlastRadius` is C2's shape verbatim (`{writes, network, shell, readOnly}`). Pure, total, no
runtime imports. Every boolean is a POSITIVE claim: `false` means "not established", and when
NOTHING is established the function returns `undefined` rather than four false negatives that
would render as a confident all-clear. `readOnly` is claimed only on positive evidence
(screening verdict → EFFECTIVE-safe risk → a `_READ_VERB_HINTS` name) and never alongside an
established write. Name evidence mirrors `task_modes.py`'s own hint vocabulary and verb
precedence, so the two agree on what a name means; `RISK_ESTABLISHES_READ_ONLY` is a total
`Record<ApprovalRisk, boolean>`, so a new risk level breaks typecheck instead of falling
through a `default:`. 23 tests in `approvalMeta.test.ts` cover the four named representative
tools on both wire paths, plus a source-level rail keeping the module a pure leaf nothing can
gate on.

**Remaining (owned by `OU-8`/`OU-9`, not by this atom):** no call site yet — OU-8 renders the
facets, OU-9 carries them over `ChannelDelivery.request_approval`. The `readOnlyCommand`
parameter has no supplier: the classification exists (`is_read_only_bash`, run per approval and
stored as `perm_meta["is_read_only"]` at `chat_runner.py:2593`) but is on no payload and read by
nobody, so putting it on the wire is OU-8/OU-9's pass-through. The companion path's missing
`risk` is likewise OU-9's additive-meta work; until then verbless reads (`grep`, `glob`,
`repo_map`) correctly derive nothing there rather than being guessed.

### `OU-8` — ApprovalCard redesign (what/why/blast-radius/scoped-remember) + toast compact variant

**Status:** done

Session 3 T3.2 + V3 (Wave 2); Design 'Approval brief'; copy-sensitive surface reviewed against security voice

**Done when:** ApprovalCard renders all four zones; useApprovalToasts gets the compact form; remember-scope (session/tool_always/no) persists via the existing approval-preference path; brief never advocates approval; risky+benign approvals driven as a user and README screenshots produced (feeds DISCOVERABILITY-LAUNCH asset list)

**Shipped:** `ApprovalCard` is a four-zone decision brief — **WHAT** (tool + arguments),
**WHY** (the runner's `purpose`, and nothing at all when it gave none), **WHAT IT CAN TOUCH**
(a named list of ESTABLISHED blast-radius facets from OU-7's `deriveBlastRadius`), **HOW FAR THE
ANSWER REACHES** (a `Segmented` remember-scope strip plus the promise it makes, in visible text).
The facet WORDS moved into `approvalMeta.ts` beside the derivation (`establishedFacets`,
`blastRadiusLine`, `BLAST_RADIUS_FACET_ORDER`, labels in a total
`Record<keyof BlastRadius, …>`), so the card, the toast and OU-9's channel brief cannot drift into
three vocabularies. Positives only: `undefined` **and** an all-false radius render NO zone —
four "no" chips would be a confident all-clear from zero evidence. The verb row collapses to
**Allow / Deny** with the scope carried in Allow's accessible name; neither verb is the visual
primary, because a primary-filled Allow on a permission prompt is advocacy.
`ui/ApprovalPrompt` gained ONE optional `scope` slot (doc + anatomy updated); the phone companion
passes nothing and its 13 tests pass untouched. The toast compact form is a new pure
`web/src/app/approvalToast.ts` — one line, no verbs, no scope, same vocabulary.
27 new tests (16 + 5 + 6) and `approvalOutcome.test.tsx`'s label assertion re-pointed at the new
action row; FE suite 223 files / 2229 tests / 0 failed.

**Deviation — C2's `tool_always` scope is NOT shipped:** nothing in this codebase remembers an
approval per TOOL. `trust` / `trust_agent` / `yolo` are all "every tool" at a widening blast
radius, `set_approval_policy` is keyed by session, and the one per-tool matcher
(`config.hooks.auto_approve_tools`, live at `hooks.py:394`) has no write path and is pinned into a
`HookManager` at gateway construction, so writing it would keep asking until a restart. The two
honest scopes ship (`no` → `approved`, `session` → `trust`) alongside the pre-existing agent-wide
grant, and a test asserts no label implies per-tool memory. Widening `trust` into "always for this
tool" would be a security-relevant lie about what the click did.

**Validated live** on an isolated home (`/private/tmp/ou8-live/home`, gateway `:10088` serving
this worktree's build) with the real `openai-compatible` app pointed at a local stub, because no
model credentials exist in that sandbox: risky `bash` (`Destructive` → "Runs a command"), benign
read-only `bash` (`Safe` → "Runs a command" + "Reads only") Allowed once → persisted `approved`,
a denial → persisted `rejected`, and a `write_file` (`Caution` → "Writes files") Allowed with the
scope switched to "This chat" → session mode `trust`. Screenshots produced for the plan-36 asset
list; they are not placed in `README.md`, which has no screenshot section yet (that section is
DISCOVERABILITY-LAUNCH's to design).

**Remaining (other atoms):** the `readOnlyCommand` pass-through and the companion path's missing
`risk` stay with `OU-9` — on the chat path the flag is redundant, since EFFECTIVE-`safe` is
already derived FROM read-only-ness, so wiring it would change no rendered chip. A genuinely
per-tool grant needs a persisted per-tool policy the runtime reads live: a security-surface atom,
not a label.

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

