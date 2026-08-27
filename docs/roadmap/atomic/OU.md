# ONBOARDING-UX — atomic plans

**Source plan:** [`ONBOARDING-UX`](../plans/ONBOARDING-UX.md)  
**Code:** `OU`  
**Source status:** proposed



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `OU-1` | ✅ | Extend onboarding state backend (step/provider/first_success fields + POST write path) | — | additive fields (step, provider_chosen/essentials, first_success) persist to entity_settings/onboarding.json, survive mid-flow reload, old clients tolerant-read; POST /api/onboarding/state does a partial merge (NOT the config PATCH allowlist, per §2.1 entity-state rule) |
| `OU-2` | ✅ | Essential-apps in-flow onboarding step (model required; search/speech/channel opt-in) | `OU-1` | fresh dev home (PERSONALCLAW_FIRST_PARTY_APPS_DIR fixture): model+search installable and the model bindable entirely in-flow (install -> key -> Test -> chat binding, reusing the 3 existing APIs); skipping all but model still reaches first-success; per-app install consent preserved; no auto-install anywhere |
| `OU-3` | ✅ | First-success 'try one' cards (knowledge ingest+ask, reminder trigger, seeded loop) | `OU-1`, `OU-2` | each of the three cards executes a real flow and reaches its visible outcome on a fresh home in <2 min; failure path shows the error and offers a Settings deep-link when a real call fails despite a passing Test |
| `OU-4` | ✅ | Onboarding done screen + resume + per-step skip + CLI setup pointer | `OU-1`, `OU-2`, `OU-3` | skip at any step lands in a working dashboard; re-entering onboarding resumes at the persisted step; personalclaw setup prints the dashboard-flow pointer when a browser is available (wizard unchanged); V1 recorded in Execution log (full flow <5 min, mid-flow reload, full-skip path, existing-home upgrade shows NO onboarding) |
| `OU-5` | ✅ | NavRail progressive disclosure (starter/expert sections, auto-pin-on-visit, expert toggle) + URL-doctrine regression test | — | fresh home shows the starter rail; visiting an Everything surface via deep link/CommandPalette renders AND auto-pins it (test red if a deep link 404s/blanks under starter mode); expert-mode toggle in Appearance shows all permanently; upgrade fixture (onboarding-completed-before-this-version marker) defaults expert ON; keyboard-only, reduced-motion, and mobile-viewport passes hold |
| `OU-6` | ✅ | EmptyState primitive + rollout to the 7 listed pages | — | web/src/ui/EmptyState.tsx exists and is applied to Loops, Workflows, Knowledge, Memory, Skills, Tasks, Triggers with one seeded working action each; copy in PRODUCT.md voice; visual check across both themes |
| `OU-7` | ✅ | Blast-radius derivation (approvalMeta.ts pure function) — C2 read-only consumption | — | web/src/pages/chat/approvalMeta.ts maps tool name + existing risk + command-screening classification to writes/network/shell/readOnly chips; unit-tested against representative tools (bash, web_fetch, memory write, read-only); NO security-logic change (E4 if any gap tempts one) |
| `OU-8` | ✅ | ApprovalCard redesign (what/why/blast-radius/scoped-remember) + toast compact variant | `OU-7` | ApprovalCard renders all four zones; useApprovalToasts gets the compact form; remember-scope (session/tool_always/no) persists via the existing approval-preference path; brief never advocates approval; risky+benign approvals driven as a user and README screenshots produced (feeds DISCOVERABILITY-LAUNCH asset list) |
| `OU-9` | 🟡 | Structured approval brief over the ChannelDelivery.request_approval seam | `OU-7`, `OU-8`, `EXT:CHANNEL-EXPANSION:ChannelDelivery.request_approval + apps-repo slack renderer consume the structured brief` | the same brief fields (tool + blast-radius line) flow through ChannelDelivery.request_approval payloads as additive meta; apps-repo slack renderer minimally updated to show what it can today; dashboard remains the rich surface |
| `OU-10` | ✅ | Replayable product tour component + Discover 'Replay the tour' card | `OU-4`, `OU-5` | web/src/app/onboarding/ProductTour.tsx spotlight tour (rail -> chat -> inbox -> approvals -> settings) runs post-onboarding end-to-end, launched from the done-screen; Esc exits anywhere leaving a fully working app; reduced-motion honored; zero requests logged for tour progress (no telemetry); re-launchable from DiscoverPage 'Replay the tour' card; S2 auto-pin behavior unchanged |
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

**Status:** done

Session 1 T1.3; Design 'Guided first run' first-success step; Risks (cards degrade gracefully if a bound provider's real call fails)

**Done when:** each of the three cards executes a real flow and reaches its visible outcome on a fresh home in <2 min; failure path shows the error and offers a Settings deep-link when a real call fails despite a passing Test

**DONE.** A fourth step — `try` — sits between essentials and the recap (`name → essentials → try →
ready`), holding three cards that each RUN a real endpoint chain and then render facts read back out
of the real responses. `web/src/app/onboarding/tryOneFlows.ts` holds the flows apart from the chrome
so what each card executes is checkable in one screen; `TryOneStep.tsx` is the step. `first_success`
finally has its writer (OU-1's last remainder) — each card patches only its own key.

- **knowledge** — `POST /api/knowledge/items` (real note) → `GET /api/knowledge/search-for-context`
  (real retrieval). Shows the PASSAGE the retrieval returned, its match type and token cost, and a
  link to the note. A note that saves but does not come back is a **failure**, not a success.
- **reminder** — `POST /api/triggers` (`notify` action, `cron: 0 9 * * *`) →
  `POST /api/triggers/{id}/run` → `GET /api/notifications`. Shows the notification text that actually
  landed plus the next fire time. `TriggerRunResult.ok === false` on a 200 is treated as a failure —
  `ok` is whether the ACTION ran (#395), and a silent no-op must not read as a green tick.
- **loop** — `POST /api/loops` (`kind: general`, `max_cycles: 1`) → `PATCH /api/loops/{id}`
  `{action: 'start'}`. Shows the status the START response reported. A loop left in `ready` is a
  failure: `POST /api/loops` answers 201 with `status: "ready"`, so a create-only card would tick
  green for a loop that never ran.

**No paid inference on any of the three, by construction — and measured.** Each path was picked
because its outcome is real and observable without a completion call: the retriever builds
`HybridRetriever(store, embedder=None)` when no embedder is configured, `notify` only calls
`state.notify(...)`, and `manager.start` writes `status: running` before any agent work and merely
arms a timer. Validated by driving the whole flow with the essentials step **skipped**, i.e. with no
model provider at all — a card that secretly needed a completion would have failed outright. The
loop is capped at one cycle so the work it later does is bounded rather than an open spend.

**The failure path (half the atom).** One shared branch: the gateway's own sentence VERBATIM in an
`InlineError`, plus a Settings deep-link chosen by `isProviderFailure(message, status)` — a
provider-shaped refusal (401/402/403, or the provider vocabulary) goes to `settings/providers` and
says *"The provider passed its test and then refused this call"*; anything else goes to
`settings/doctor` and does not blame the credential. Siblings stay usable and the card retries in
place. Two classifier bugs were found by its own rails: `\b` does not split snake_case, so every
machine-readable code (`insufficient_quota`, `invalid_api_key`) fell through while the prose matched;
and `new Error('')` stringifies to the literal word `Error`, which would have been shown to a user as
the gateway's explanation.

**The deep-link needed a new mechanism, and its first version shipped green and broken.**
`App.tsx`'s guard pulls every route back to `#/onboarding` while `onboarded` is false, so a link out
of the flow cannot navigate — it is bounced, and navigating after committing the name races the same
guard. `web/src/app/onboarding/exitTo.ts` hands the destination to the guard instead. v1 cleared on
read and every unit test passed; driven live it landed on `#/dashboard` every time, because **the
guard effect is re-entrant**: `navigate` sets `location.hash` and `route` only catches up on the
browser's async `hashchange`, so the exit branch ran twice and the second run read `''` and took the
default. `peekOnboardingExit` is now idempotent and `clearOnboardingExit` fires on a later branch,
once the route has provably left onboarding. The regression rail asserts N reads resolve identically.

**Deviations.** (1) The "ask" is retrieval, not a model-generated sentence — there is no synchronous
knowledge-answer endpoint (`POST /api/chat` streams a whole session), and retrieval is both the real
answer path and the part that must work before any model can answer. (2) Leaving the try step writes
NO new resume point: `STEPS` has no id between `first_success` and `done`, so OU-3's step *is*
`first_success`, and a user who reloads on the recap correctly resumes at the step they have not
finished. (3) `stepProgressAnnounced`'s "all three rows read from TITLES" count is now DERIVED from
`ORDER` — a frozen count turns "every row" into "exactly N rows" and would red for a compliant
fourth step; deriving it strengthens the claim rather than weakening the rail.

**Not built on PEP-1's `PresetCard`** (read first, as instructed): that primitive is deliberately ONE
tab stop whose whole body is a `TileButton` with no interactive children, because a button inside a
button is `nested-interactive`. These cards grow controls after they run — run, then an outcome link,
then possibly a Settings deep-link and a retry — so they cannot be a single click target, and
`PresetEmptyState` hardcodes `PresetCard` in its grid. Chrome composed from the kit instead
(`Button`, `TextLink`, `InlineError`), which is the part that would otherwise drift.

**Backend:** one line — `KnowledgeContextCard.content` added to `web/src/lib/api.ts`.
`search-for-context` has always sent the matched passage (it is the text the composer injects) but it
was absent from the interface, so the one thing a retrieval card exists to show was untypeable. No
endpoint was added for onboarding.

**Confirms PEP-1's finding:** a fresh home is not empty — `system:notification-digest` and a
"Backup restore drill passed" notification are both present at first boot. The reminder card matches
its notification by TITLE rather than by "the store was empty", so it is correct on a real home.

### `OU-4` — Onboarding done screen + resume + per-step skip + CLI setup pointer

**Status:** done

Session 1 T1.4 + V1; Design done screen (points at Inbox, bounciness slider, unlock-everything toggle); cli_setup.py one pointer line

**Done when:** skip at any step lands in a working dashboard; re-entering onboarding resumes at the persisted step; personalclaw setup prints the dashboard-flow pointer when a browser is available (wizard unchanged); V1 recorded in Execution log (full flow <5 min, mid-flow reload, full-skip path, existing-home upgrade shows NO onboarding)

**DONE.** The atom that finally READS what OU-1/OU-2/OU-3 wrote. `step` had a writer and no
reader, which reads exactly like a working resume until someone reloads: every reload restarted
at the essentials step and silently offered to redo work the home had already recorded.

- **Resume** — one `GET /api/onboarding` on mount now carries both halves (live readiness for
  the essentials step, the persisted resume point for the shell). `resumeTarget()` maps
  `essentials → essentials` and `first_success → try`; `name` and `done` are deliberately NOT
  targets. The transition write records the step it LANDS on, so resuming can never walk the
  stored point backwards (a `{step: essentials}` write on the way into `first_success` would
  have decayed the resume one step per reload).
- **Per-step skip** — one always-visible escape under the stack (`step !== 'ready'`, where
  "Start using" is the door). It runs the same `finish()` as completing: terminal step recorded,
  rail marker written, identity committed — committing identity is what releases `App.tsx`'s
  route guard, so this is what makes the landing a WORKING dashboard rather than a stuck flow.
  It also makes `finish()`'s `|| 'Operator'` fallback reachable for the first time (it was dead
  code, since `commitName` refuses an empty name), so that literal became
  `identity.DEFAULT_USER_NAME` shared with the Settings → Account field that also falls back to
  it — and the link SAYS the name it will use rather than renaming someone silently.
- **Done screen** — recap plus the Design's three pointers, each handing over the real control:
  the Inbox (a `TextLink` through `exitTo.ts`, because the route guard bounces a plain link),
  the Settings → Design **Bounciness** dial (`ScalarControl` on the `--bounciness` token, not a
  lookalike bound to the same variable), and **Show every surface** (OU-5's one nav-disclosure
  setting). The switch states intent and `finish()` performs the single write, so the C4 marker
  and the user's choice cannot disagree and an abandoned flow leaves no record — absence is how
  the shell tells an upgrade from a fresh install.
- **CLI pointer** — `_print_dashboard_pointer()` after both of `_setup`'s completion lines,
  gated on the new `env.browser_available()`. That predicate is the gateway's own auto-open
  heuristic, EXTRACTED rather than copied: `gateway.py` no longer derives it from
  `SSH_CONNECTION`/`DISPLAY` inline, and a rail asserts those names are gone from that file.
  The wizard itself is unchanged, which answers the plan's open question ("full parity?") with
  "no — the dashboard owns onboarding".

**Deviations.** (1) A resumed flow still asks for the NAME first, then jumps to the persisted
step. The name is not part of this state by OU-1's ruling (identity lives on the server and
`onboarded` is derived from it), and it is committed only at the end — so the alternatives were
fabricating a name for someone who typed one before the reload, or keeping a second copy of the
name in a device-local draft. Re-typing one field is the honest cost; everything the earlier
visit actually DID is what resume restores. (2) The resumed essentials summary is the app name
from `essentials.model` rather than the bound model label a live run shows, and it is checked
against `needs_model` so a home whose provider was removed since does not keep promising a
model. (3) A resumed try-one visit restores the COUNT, not the cards: only the flags survive a
reload, not the outcomes the cards rendered, so `leaveTryOne` floors the recap at the persisted
count instead of telling a user who succeeded before the reload that nothing was tried.

### `OU-5` — NavRail progressive disclosure (starter/expert sections, auto-pin-on-visit, expert toggle) + URL-doctrine regression test

**Status:** done

Session 2 T2.1 + T2.3 + V2; Contracts C4 (NavRail sections, prefs store); Design 'Progressive disclosure'

**Done when:** fresh home shows the starter rail; visiting an Everything surface via deep link/CommandPalette renders AND auto-pins it (test red if a deep link 404s/blanks under starter mode); expert-mode toggle in Appearance shows all permanently; upgrade fixture (onboarding-completed-before-this-version marker) defaults expert ON; keyboard-only, reduced-motion, and mobile-viewport passes hold

**DONE.** New `web/src/app/navDisclosure.ts` owns the whole model — `{mode: 'starter'|'expert',
pinned: string[]}` in `localStorage['nav-disclosure']`, one `isDisclosed(id, mode, pinned)` rule,
one `undisclosedCount()`, and a `useNavDisclosure()` hook that reads SYNCHRONOUSLY on mount (no
probe, so no flash of the wrong rail). `ui/NavRail` gained ONE optional `disclosure`
prop — `{expanded, moreCount, onToggle}` — and renders an "Everything +N" / "Show fewer" row at
the end of scroll order with `aria-expanded` and the count in its accessible name; the rail is
handed already-FILTERED items, so which surfaces are starter and how pins persist stay in one
place. `App.tsx` filters the rail, computes the count, and carries the auto-pin effect;
`DesignPanel` (`#/settings/design`, the Appearance panel) gained a **Navigation** section whose
`Show every surface` switch is the same one setting, not a second mechanism.

**The clause with teeth.** Disclosure governs the RAIL ONLY. `rendered` is untouched by it and
the CommandPalette is still built from the full `NAV`, so a deep link, a palette "Go to", a
Discover tip and an in-app link all render a hidden surface — and reaching one PINS it, so the
rail grows with use instead of asking to be configured. `navDisclosure.test.tsx` drives the real
shell for this (19 tests): `#/tools` under starter mode is asserted on the page's own `h1`, not on
a route string, because a blank page and a rendered page both "navigate".

**The upgrade marker is the record's ABSENCE.** C4 asks for an
"onboarding-completed-before-this-version marker"; it needs no new field. `Onboarding`'s `finish()`
writes `mode: 'starter'` — the one act only a fresh install performs, since it is what commits
identity and flips `onboarded` — so a stored record means "onboarded under this version" and no
record means "onboarded before it", resolving to `expert`. That is also the safe failure
direction: an absent or unreadable preference shows every surface rather than hiding surfaces
someone has been using for months. Pins are never cleared by `finish()`, so "Restart onboarding"
resets the mode without taking a surface away.

**Falsified, seven mutations, and two of them reded nothing at first** — both were real gaps in
the tests, now closed: dropping `Onboarding`'s marker was invisible (no test drove the fresh-install
write; two now live in `onboardingProgress.test.tsx`), and removing the `moreCount > 0` guard reded
only a source rail (a behavioural "renders NO control once nothing is left to reveal" test now
covers it, doubling as a ratchet on a new rail destination). The five that reded correctly:
routing `rendered` through `isDisclosed` → 3 RED (`Unable to find … heading "Tools"`); auto-pin as a
no-op → 3 RED; filtering the palette → 1 RED; defaulting an absent record to `starter` → 4 RED;
dropping `aria-expanded` → 3 RED.

**DEVIATION (`dashboard` is in the starter set).** The Design names "Chat, Inbox, Apps, Settings".
`dashboard` (Home) is added because it is the app's LANDING route (`useHashRoute('dashboard')`) — a
rail that omits the page it opens on is a defect, not a decision. Five starter rows; 13 of the 18
static destinations held back.

**DEVIATION (section headers are dropped on the starter rail).** Measured live before the fix: five
rows under three headings, with `PLATFORM` over Inbox alone and `APPS` over Store alone. A heading
per item groups nothing, and the starter rail is one curated group by construction. Expert keeps
Platform / Capabilities / Apps untouched.

**DEVIATION (`app/*` tiles are exempt).** A contributed app's tile is already an explicit per-app
pin ("Show in navigation", persisted in `nav-apps`), so disclosure has nothing to reveal for it and
hiding it would silently undo a choice made by hand.

**DISCOVERY (the rail has 18 destinations, not 19).** Counted from `NAV`: the expanded rail renders
19 buttons because one of them is the disclosure control itself. Worth knowing for any later atom
that quotes a rail count.

**Known limit, deliberate.** The preference is per-DEVICE, which `identity.tsx` already states as
the house rule ("Per-device prefs (theme, width, nav state) stay in localStorage; identity does
not"). So a SECOND browser opened against an already-onboarded install has no record and shows the
full rail. That fails open — it never hides a surface from someone who has been using it — and
matches how theme, rail width and rail collapse already behave.

### `OU-6` — EmptyState primitive + rollout to the 7 listed pages

**Status:** done

Session 2 T2.2; Contracts C3 (EmptyState props); Design 'Empty states as on-ramps'

**Done when:** web/src/ui/EmptyState.tsx exists and is applied to Loops, Workflows, Knowledge, Memory, Skills, Tasks, Triggers with one seeded working action each; copy in PRODUCT.md voice; visual check across both themes

**DEVIATION on the filename clause — `web/src/ui/EmptyState.tsx` deliberately does NOT exist.**
`EmptyState` predates this atom, exported from the list kit (`ui/ListScaffold.tsx`) beside `LoadError`
and `ListSkeleton`, and already answers ~30 call sites. A second component beside it would be a dual
path, and `loadErrorState.test.tsx` already pins the co-location *on purpose* ("alternative answers to
the same condition, and a surface reaching for one should see the other"). So the atom shipped its
PRODUCT clause — all seven surfaces explain themselves and offer one working action — and left the
primitive where the repo already put it. `pages/emptyStateRollout.test.tsx` holds both halves,
including an assertion that `ui/EmptyState.tsx` stays absent.

**Shipped:** the rollout gap was two surfaces, not seven. Five (Knowledge, Skills, Tasks, Triggers via
`PresetEmptyState`, and Workflows' no-match branches) already routed through a shared primitive.
· **Memory** (`settings/MemoryPanel.tsx`) had no empty state at all — four hand-rolled centered `<p>`s;
now `EmptyState` at all four, with "Add a fact" wired to the studio's own add control, and the audit
log split into "Nothing recorded yet" vs "No matching events" (one sentence used to serve both, telling
a user with an untouched memory that their filter was the problem).
· **Workflows** had two actionless empty states; both now carry a working action ("Start from template",
"Browse definitions") on the genuinely-empty branch only.
· **Loops** conflated a failed load with an empty one (`.catch(() => [] as GoalLoop[])` → a 500 rendered
"No loops yet — Start a loop"); it joined `loadErrorState.test.tsx`'s ADOPTERS, and its facet no-match
branch moved from a hand-rolled `<p>` to the shared primitive.

Two surfaces' genuinely-empty branches are unreachable on a fresh home and were not driven: **Skills**
(native skills ship pre-installed) and **Triggers** (`system:notification-digest` ships enabled). Both
were already correct and are untouched by this atom.

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

**Status:** done

2026-07-26 Amendment Session 5 T5.1 + T5.2 (rulings b + c); PlanningWalkthrough.tsx is the pattern donor; Discover named as the progressive-disclosure arm

**Done when:** web/src/app/onboarding/ProductTour.tsx spotlight tour (rail -> chat -> inbox -> approvals -> settings) runs post-onboarding end-to-end, launched from the done-screen; Esc exits anywhere leaving a fully working app; reduced-motion honored; zero requests logged for tour progress (no telemetry); re-launchable from DiscoverPage 'Replay the tour' card; S2 auto-pin behavior unchanged

### `OU-11` — Stranger-validation facilitator kit + 3 think-aloud sessions

**Status:** todo

Session 4 T4.1 + T4.2 + V4 (Wave 2); Design 'Stranger validation'; Owner task 1 (recruit + host 3 strangers)

**Done when:** docs/maintainers/usability-kit.md self-contained (facilitator script, consent note, observation sheet; dry-run on self recorded); 3 sessions run (OWNER recruits/hosts strangers); fix-now list (<=1 day) empty by session close; issues filed labeled ux-finding; first-success timing delta vs S1 baseline recorded

