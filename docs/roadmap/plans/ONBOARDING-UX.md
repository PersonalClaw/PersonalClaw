# ONBOARDING-UX

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/OU.md`](../atomic/OU.md) as 11 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Onboarding UX — Guided First Run + Progressive Disclosure

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner: "onboarding UX that guides user through the app, features, configuration of their first agent provider or model provider, and gets them started")
**Created:** 2026-07-18
**Wave:** 1 (S1-2) + 2 (S3-4)
**Depends on:** nothing hard. Coordinates with DISCOVERABILITY-LAUNCH (demo capture reuses the tour click-path), DISTRIBUTION (the install path determines what the first screen may promise), AMBIENT-SURFACES (Home tiles are the tour terminus), INBOX-NOTIFICATIONS-UNIFICATION (badge semantics the nav shows).
**Scope:** guided first run to a first success in under 5 minutes, progressive disclosure over the 20+-surface nav, approval-brief polish, and stranger validation. **Soul guardrail:** guidance, never gates — every step skippable, the full sidebar one toggle away, zero features locked behind the tour, and **no tour telemetry** (zero-telemetry stance; learn from usability sessions, not tracking). Build on the existing machinery — `Onboarding.tsx` + `onboarding/StepStack.tsx`, `NavRail.tsx`, `ApprovalCard.tsx`, `PlanningWalkthrough.tsx` — extend, don't reinvent (protocol rule: nearest-analog style).

---

> 📎 **A high-value onboarding step lives in [PRODUCT-EXPERIENCE-PARITY](PRODUCT-EXPERIENCE-PARITY.md) §3 (#68)** — added 2026-08-05: **import from other local agent tools** (Claude Code, Codex first — detect, review, import instructions/memories/MCP/skills). This plan has no import row; #68 owns it but the *step* belongs in this plan's onboarding flow (it extends the StepStack in C-series). Coordinate placement when #68 §3 executes: the import step slots after the provider step, before first-success. #68 §1's `PresetEmptyState` also rides this plan's T2.1 Starter/Everything progressive disclosure — same family, land compatibly.

## Context (code recon, 2026-07-18)

Onboarding exists as `web/src/app/Onboarding.tsx` + `onboarding/StepStack.tsx` (step-stack machinery) + `identity.tsx`, backed by `GET /api/onboarding` first-run state — today it covers name capture and points at provider setup (per getting-started: "asks for your name and walks you to provider setup"). The nav is `ui/NavRail.tsx`; shell primitives (TopBar/ListScaffold/SidePanel/HeaderActions) and `CommandPalette.tsx` exist; `useApprovalToasts.ts` + `pages/chat/ApprovalCard.tsx` are the approval surfaces; `PlanningWalkthrough.tsx` is an existing walkthrough-pattern precedent. CLI has `personalclaw setup` (wizard) and `doctor` (verification). Gap: no in-flow provider install/bind (a Settings triple-hop: Apps→install, Providers→key, Models→bind), no first-success moment, no disclosure model over the nav, empty states vary in helpfulness.

## Design

- **Guided first run (StepStack extension):** name (exists) → **provider step in-flow**: curated provider-app cards (Anthropic/OpenAI/OpenAI-compatible/Ollama/Bedrock — driven by the Store catalog, not hardcoded vendors: the list is "model-provider-typed apps," rendered from the catalog so core stays vendor-blind) → inline install (existing Store install API) → key entry + Test (existing provider settings API) → chat binding (existing bindings API) → **first-success step**: seeded "try one" cards (summarize a URL into Knowledge then ask about it; set a reminder trigger; run a small goal loop) that execute for real → done screen (points at Inbox, the bounciness slider, and "unlock everything" toggle). Skippable at every step; resume state in the existing onboarding state endpoint (extend, additive).
- **Progressive disclosure (NavRail):** two rail sections — **Starter** (Chat, Inbox, Apps, Settings) and **Everything** (collapsed group, one click to expand permanently). A surface auto-pins from Everything→visible when first visited (deep links + CommandPalette always work and auto-pin — URL doctrine untouched). Pin state = frontend-persisted user pref (existing appearance/prefs storage pattern); an "expert mode" toggle (Settings → Appearance) shows all permanently. Default for **existing** installs: expert mode ON (no rug-pull); fresh installs: starter mode.
- **Empty states as on-ramps:** shared `EmptyState` primitive (icon, one-paragraph concept, one seeded action button) applied to the major pages (Loops, Workflows, Knowledge, Memory, Skills, Tasks, Triggers) — copy voice per PRODUCT.md ("companion, not console").
- **Approval brief (ApprovalCard):** decision-ready layout — what (tool + args summary), why (one line of plan/goal context when the runner provides it), blast radius chips (writes files / network / shell / reads-only — derived from tool metadata + command screening classification), and scoped remember-this-choice (this session / always for this tool / no). Same content model flows to channel `request_approval` renderers (plan 40 apps consume the structured brief). **Copy-sensitive surface:** wording changes reviewed against the security-docs voice; the brief must never *advocate* approval.
- **Stranger validation:** 3 sessions, think-aloud, fresh install → first success; facilitator script + consent note; findings triaged fix-now (in-session budget) vs issue-filed.

## Contracts & Interfaces (conventions per [AGENTS.md](../../../AGENTS.md))

### C1 — Onboarding state (extends existing `GET /api/onboarding`, handler `api_onboarding`, registered `server.py:371`)

Additive fields only (tolerant reads; old clients ignore them). State persisted in `entity_settings/onboarding.json` (§2.4):
```jsonc
{
  "name": "…", "completed": false,           // existing
  "step": "name|provider|first_success|done", // NEW: resume point
  "provider_chosen": "anthropic-models",       // NEW
  "first_success": {"knowledge": false, "trigger": false, "loop": false}  // NEW: which "try one" cards completed
}
```
Write path: a dedicated `POST /api/onboarding/state` (partial merge) — not the config PATCH allowlist (this is entity state, §2.1 rule-of-thumb).

### C2 — Approval brief data model (EXTENDS existing `ApprovalSegment`, `web/src/pages/chat/chatTypes.ts:30` — do NOT invent a new type)

The type already carries `{id, tool, input?, purpose?, risk?: 'safe'|'caution'|'destructive', resolved?}`. This plan:
- adds `blastRadius?: { writes: bool; network: bool; shell: bool; readOnly: bool }` (derived frontend-side by `approvalMeta.ts` from tool name + the existing `risk` + command-screening classification — **read-only consumption**, no security-logic change; E4 if a change tempts);
- adds `rememberScope?: 'session'|'tool_always'|'no'` to the resolve action (persists via the existing approval-preference path).
- Backend: `purpose`/`risk` already flow via `m.meta` (verified `chatTypes.ts:262`); the plan ensures the runner populates `purpose` (one line of plan/goal context) where available — additive meta, no new event channel.

### C3 — `EmptyState` primitive (`web/src/ui/EmptyState.tsx`, new shared component)
Props: `{ icon: ReactNode; title: string; body: string; action?: { label: string; onClick: () => void } }`. Applied to the 7 listed pages. Copy voice per `web/PRODUCT.md`.

### C4 — NavRail sections (extends `web/src/ui/NavRail.tsx`)
Pin state persisted in the existing appearance/prefs store (locate — same store as the bounciness slider): `{ navMode: 'starter'|'expert', pinned: string[] }`. Fresh installs → `starter`; upgrades (onboarding-completed-before-this-version marker) → `expert`. Deep-link/CommandPalette visit auto-adds to `pinned` (URL doctrine intact).

### Integration points
- **Calls:** existing Store install API, provider settings + Test API, model-bindings API (in-flow provider step); knowledge-ingest / trigger-create / loop-create APIs (first-success cards); command-screening classification (blast-radius, read-only).
- **Called by:** first-run flow; `personalclaw setup` prints a pointer to it.
- **Consumed by:** DISCOVERABILITY (36) reuses the tour click-path for the demo capture; 40/44 approval renderers consume the same brief model (C2) over `ChannelDelivery.request_approval`.
- **Storage:** `entity_settings/onboarding.json`; frontend prefs store (nav/pins).

## Task breakdown (executor-ready — run under the roadmap session discipline in [AGENTS.md](../../../AGENTS.md))

### Session 1 — Guided first run

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | Extend onboarding state (backend): additive fields for step progress + chosen provider + first-success completion; round-trip contract respected | onboarding state handler (locate via `GET /api/onboarding` registration), config/entity storage per existing pattern | state survives reload mid-flow; old clients unaffected (tolerant reads) |
| T1.2 | Provider step: catalog-driven provider-app cards (filter: model-provider type), inline install → key entry + Test → chat binding, reusing the three existing APIs; failure paths inline (bad key shows the Test error, retry in place) | `web/src/app/onboarding/` new step components on StepStack | fresh dev home: Anthropic-compatible fixture provider installable + bindable entirely in-flow; no Settings navigation required |
| T1.3 | First-success step: three "try one" cards executing real flows (knowledge URL ingest + question; reminder trigger creation; small seeded loop) with live progress + a visible result each | onboarding step components (+ tiny seeded content) | each card reaches its visible outcome on a fresh home in <2 min |
| T1.4 | Done screen + resume behavior + skip affordances on every step; CLI parity note: `personalclaw setup` prints the dashboard-flow pointer when a browser is available (wizard itself unchanged this session) | onboarding components, `cli_setup.py` (one pointer line) | skip at any step lands in a working dashboard; re-entering onboarding resumes correctly |
| V1 | Validation: fresh home → full flow to first success, timed (<5 min target); mid-flow reload; full-skip path; existing-home upgrade shows NO onboarding | — | timings + all paths recorded in Execution log |

### Session 2 — Progressive disclosure

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | NavRail sections (Starter/Everything) + pin model + auto-pin-on-visit + persisted prefs; expert-mode toggle in Appearance; existing installs default expert ON (detect: onboarding-completed-before-this-version marker) | `web/src/ui/NavRail.tsx`, prefs storage, Appearance panel | fresh home shows starter rail; visiting Loops via CommandPalette pins it; toggle shows all; upgrade fixture keeps full rail |
| T2.2 | `EmptyState` primitive + rollout to the seven listed pages (copy drafted per PRODUCT.md voice; one seeded action each) | `web/src/ui/EmptyState.tsx`, seven page components | each empty page explains itself + offers one working action; visual check across both themes |
| T2.3 | URL-doctrine test extension: deep link to an unpinned surface renders it AND pins it (regression-proof the disclosure model) | frontend test suite | test red if a deep link ever 404s/blanks under starter mode |
| V2 | Validation: keyboard-only pass (focus-visible on rail interactions), reduced-motion pass, mobile viewport sanity (`useIsMobile` paths) | — | WCAG checks hold |

### Session 3 — Approval brief polish (Wave 2)

| ID | Task | Files | Done when |
|---|---|---|---|
| T3.1 | Blast-radius derivation: map tool metadata + command-screening classification to chips (writes/network/shell/read-only); surface in a small pure function with tests (no security-logic changes — read-only consumption of existing classifications; E4 if any gap tempts a change) | new `web/src/pages/chat/approvalMeta.ts` + backend field pass-through if needed (locate tool-result meta) | chip derivation unit-tested against representative tools (bash, web_fetch, memory write, read-only) |
| T3.2 | ApprovalCard redesign: what/why/blast-radius/scoped-remember layout per Design; toast variant (`useApprovalToasts`) gets the compact form | `web/src/pages/chat/ApprovalCard.tsx`, `useApprovalToasts.ts` | card renders all four zones; remember-scope choices persist to the existing approval-preference path; copy review against security voice recorded |
| T3.3 | Structured brief over the seam: ensure the same fields flow through `ChannelDelivery.request_approval` payloads (additive meta; slack app renders what it can today; plan 40 apps consume fully) | approval payload builder site, apps repo slack renderer (minimal) | channel approval shows tool + blast-radius line; dashboard remains the rich surface |
| V3 | Validation: drive risky + benign approvals as a user; screenshot the card for README (feeds plan 36 T3.2 asset list) | — | screenshots produced; behavior verified |

### Session 4 — Stranger validation (Wave 2)

| ID | Task | Files | Done when |
|---|---|---|---|
| T4.1 | Facilitator kit: script (tasks: install via the real one-liner, reach first success, find and approve a tool call, tell us what "Loops" means from the UI alone), consent note, observation sheet | `docs/maintainers/usability-kit.md` | kit self-contained; dry-run on yourself recorded |
| T4.2 | Run 3 sessions (owner task 1), transcribe findings into: fix-now list (≤1 day total, executed within this session) + filed issues (labeled ux-finding) | Execution log + issues | 3 sessions run; fix-now list empty by session close; issues filed |
| V4 | Validation: re-run the first-success timing after fixes; compare against S1 baseline | — | delta recorded |

## Owner tasks (real world)

1. **Recruit 3 strangers** (technical-adjacent but never seen the product — colleagues, community members) and host the sessions (~45 min each, screen-share or in person). The kit (T4.1) makes it turnkey.
2. **Copy review** (30 min): first-run copy + empty-state paragraphs + approval-brief wording — your product voice, your sign-off (approval wording is a security surface).
3. Decide the **starter set** if you disagree with Chat/Inbox/Apps/Settings (e.g., swap Inbox for Tasks) — before T2.1.

## Risks & open questions

- **First-success cards depend on a bound provider** — the flow order guarantees it; the cards must degrade gracefully if the binding Test passed but the first real call fails (show the error, offer Settings deep-link) — covered in T1.3's failure paths.
- **Open:** should `personalclaw setup` (CLI) gain full parity with the new flow? Deferred — dashboard is the canonical onboarding; CLI wizard remains credentials-first (a DISCOVERY note if V1 shows CLI-first users hitting friction).

## Amendment (2026-07-26 — gap analysis round 2, owner decisions)

**The three-surface split + apps-first (owner decisions).** Code recon (2026-07-26): `web/src/app/Onboarding.tsx` is a three-step StepStack (`name → model → ready`; the model step is a fix-or-skip readiness check over `GET /api/onboarding`, which reports `needs_model/has_model_provider/has_chat_binding`); `onboarded` is DERIVED from a non-empty server name (`identity.tsx`). A Discover surface already exists twice: the dashboard `widgets/Discover.tsx` spotlight + the full `pages/discover/DiscoverPage.tsx` hub (server-side hand-authored catalog, deep-link + dismiss, propose-don't-write). First-party apps reach the Store via `apps/catalog.py`: the workspace `apps/` dev source (`_first_party_source`) OR the shipped default git source `_DEFAULT_GIT_SOURCES = ("https://github.com/PersonalClaw/PersonalClawApps.git",)` — a Store-listing default, never auto-installing; install stays a per-app consented act. So the "essential apps" step is catalog-driven listing + consented installs, exactly the mechanism S1 T1.2 already planned — this amendment widens WHICH apps and names the split.

Owner rulings, mapped onto the existing sessions honestly:

- **(a) Guided onboarding flow = first-run setup** — Session 1 as designed (extend the existing `Onboarding.tsx` step stack), with one re-scope of T1.2: the provider step becomes an **essential-apps step**, and it is the flow's FIRST act after name. It walks the user through installing, from the first-party source (dev dir or the default git Store source — name the mechanism in the step's empty/error states), the apps that make the platform work end-to-end: a **model provider** (required rail — the existing key-entry + Test + chat-binding sub-flow), a **search** app, a **speech** app (STT/TTS), and optionally a **channel** — each an opt-in card with the Store's install-consent surface, model-provider required, the rest skippable. Only then does the flow teach anything (first-success step unchanged). C1's `provider_chosen` generalizes to `essentials: {model: str|null, search: bool, speech: bool, channel: str|null}` (additive).
- **(b) Guided walkthrough = a replayable product tour** — a NEW deliverable (the current plan has no tour; `PlanningWalkthrough.tsx` is the pattern donor): a spotlight-overlay tour component over the real UI (anchored steps: rail → chat → inbox → approvals → settings), launched from the onboarding done-screen, **re-launchable from the Discover page** ("Replay the tour" card). Guidance-never-gates: Esc exits anywhere, no step blocks, zero telemetry.
- **(c) Discover = the progressive-disclosure arm, formally.** The existing Discover widget + hub ABSORB that role: Session 2's starter/expert NavRail + auto-pin model stays, and Discover is named as where undisclosed surfaces get discovered (tips deep-link → visit auto-pins, T2.1's existing mechanic — no new machinery, a role declaration + the tour entry card).

### Session placement

(a) re-scopes S1 T1.2 (no count change there); (b)+(c) form a new **Session 5** after S2 (needs the disclosure model to point at; count 4 → 5). S3/S4 untouched.

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.2r | Essential-apps step (re-scope of T1.2): catalog-driven cards for model/search/speech/channel types from the first-party source; model = required rail (install → key → Test → bind, per original T1.2); others opt-in; per-app install consent preserved | `web/src/app/onboarding/` step components (existing Store/provider/binding APIs only) | fresh dev home (`PERSONALCLAW_FIRST_PARTY_APPS_DIR` fixture): model+search installable entirely in-flow; skipping everything but model still reaches first-success; no auto-install anywhere |
| T5.1 | Replayable tour component (spotlight steps over the real UI, PlanningWalkthrough-pattern), launched from the done-screen; Esc-anywhere; reduced-motion honored | `web/src/app/onboarding/ProductTour.tsx`, done-screen, anchor ids on toured surfaces | tour runs post-onboarding end-to-end; exiting mid-tour leaves a fully working app; zero requests logged for tour progress (no telemetry) |
| T5.2 | Discover absorbs progressive disclosure formally: "Replay the tour" card + copy pass naming Discover as the disclosure arm beside the S2 auto-pin model | `web/src/pages/discover/DiscoverPage.tsx`, discover catalog (server-side), docs | tour re-launchable from Discover; a dismissed-everything user still finds the tour; S2 auto-pin behavior unchanged |

## Execution log

- [2026-08-13][OU-1] **DONE (S1 T1.1 / C1).** New `src/personalclaw/onboarding.py` holds the
  first-run progress state in `entity_settings/onboarding.json`:
  `{step, essentials: {model, search, speech, channel}, first_success: {knowledge, trigger, loop}}`.
  `load_onboarding_state()` sanitizes field by field and never raises — a missing file, corrupt
  JSON, non-object JSON, an older client's store with none of these fields, a wrong-typed value,
  or a retired `step` value each fall back to that field's default while its siblings survive.
  `merge_onboarding_state()` merges partially at BOTH levels (a patch naming only
  `first_success.knowledge` leaves `trigger`/`loop` alone) and rejects an unknown or mistyped key
  with `ValueError`, following bug #22's lesson that a lenient write path leaks garbage back out
  through every read. `GET /api/onboarding` (`handlers_system.api_onboarding`) now returns the
  three fields alongside the existing `needs_model`/`has_model_provider`/`has_chat_binding`
  triple — purely additive, so a client reading only the triple is unaffected — and
  `POST /api/onboarding/state` is the write path, deliberately NOT the `_EDITABLE_CONFIG` PATCH
  allowlist (§2.1). 35 tests in `tests/test_onboarding_state.py`. Both central rails were
  falsified: starting the merge from defaults instead of the stored state reds the four
  partial-merge tests; returning the raw disk dict instead of the sanitized projection reds the
  four tolerant-read tests.
- [2026-08-13][OU-1] **DEVIATION (C1 field/step naming).** C1 spelled the middle step `provider`
  and the field `provider_chosen`; the 2026-07-26 amendment (ruling a) re-scoped that step to
  essential-apps and generalized the field to `essentials`. The amendment renamed the field but
  said nothing about the step enum, so the step id is now `essentials` too — a step id named
  `provider` for a step whose UI installs essential apps is exactly the drift a later coherence
  pass would file. `STEPS = ("name", "essentials", "first_success", "done")`. No dual name shipped.
- [2026-08-13][OU-1] **DISCOVERY (C1 premise correction).** C1 annotates `name` and `completed` as
  *existing* fields of this state. They are not, and never were: `api_onboarding` only ever
  returned the readiness triple, the name lives in server identity, and `onboarded` is derived
  from that name being non-empty (`web/src/app/identity.tsx`, as the amendment's own recon says).
  Storing either here would create a second source of truth for a derived value, so neither is in
  the schema. Sessions 1 and 4 should read "resume point + essentials + first-success" as the whole
  of this store.
- [2026-08-13][OU-1] **DISCOVERY (no new inventory entry needed).** A new `entity_settings/*.json`
  store joins snapshot and durability coverage for free: `durability/inventory.py` declares
  `entity_settings` as one `KIND_JSON_ENTITY_DIR` entry and snapshot capture is inventory-derived,
  so `CORE_FILES`, `append_dedup` and `audit_home()` needed no change (`test_snapshot.py`,
  `test_durability_inventory.py`, `test_portability.py`, `test_resilience_doctor.py` all green
  unchanged) — the same free ride `feedback.json`, `channel_trust.json` and `legibility.json` take.
  Later atoms adding onboarding-adjacent stores can rely on this too.
- [2026-08-13][OU-1] **No CHANGELOG entry, deliberately.** The change is class B by the letter
  (it persists something new under the home) but nothing a user can perceive changed: the flow
  does not resume yet, because the writers are OU-2/OU-3 and the resume read is OU-4. The in-app
  Updates panel renders `CHANGELOG.md`, so an entry here would send a user looking for a resume
  behaviour that does not exist. The entry lands with the atom that makes the state observable.
  The API addition is discoverable now through the regenerated `reference/routes.md` and
  `docs/reference/api-overview.md`, which is the surface its actual audience reads.
- [2026-08-13][OU-2] **DONE (S1 T1.2r / Amendment ruling a).** `web/src/app/onboarding/EssentialsStep.tsx`
  is the flow's new step 2 (`name → essentials → ready`), and it is the first place a fresh install
  can become a working agent without a detour through Settings. Four lanes render from
  `GET /api/apps/catalog`: **model** (required), **search**, **speech**, **channel**. The model lane
  carries the full rail over three EXISTING endpoints — `POST /api/apps` (install) →
  `POST /api/model-providers` + `POST /api/model-providers/{name}/test` (the key, then a real Test) →
  `PUT /api/models/active/chat` (the binding) — with the provider's own `settingsSchema` rendered by
  the Settings panel's `SchemaField`, so onboarding grew no second key-entry idiom. This atom is what
  finally WRITES the state OU-1 shipped: the shell persists `step`
  (`essentials` → `first_success` → `done`) and each lane patches only its own `essentials` field, so
  OU-1's both-level partial merge is exercised for real rather than asserted in isolation. The old
  fix-or-skip `ModelStep` is deleted, not bypassed — its two states (chat already resolves; a provider
  exists but nothing is bound) are the model lane's opening phases, so there is no dual path.
- [2026-08-13][OU-2] **Central rails, both falsified.** (1) *No auto-install anywhere*: installing the
  first model candidate from an effect reds 12 tests in `essentialsStep.test.tsx`; restored, green.
  (2) *Per-app consent preserved*: making the card's Review button install directly — the quieter
  consent path the done_when forbids — reds 12 tests including the three that assert the Store's
  disclosure copy verbatim; restored, green. Consent is not a paraphrase: `PermissionList`,
  `CronConsentList` and `ConsentModal` are the Store's own components, so a scanner WARNING raised
  during onboarding shows the same findings and demands the same explicit "Install anyway".
- [2026-08-13][OU-2] **DEVIATION (the "chat binding API" is not the prompt-bindings API).** The plan's
  Integration points say "model-bindings API", and the atom brief pointed at
  `PUT /api/prompts/bindings` (`api_prompt_bindings_save`). That endpoint binds a PROMPT to a use
  case. The chat-model binding is `PUT /api/models/active/{use_case}` (`api.setActiveModel`), which is
  what `active_model_refs("chat")` — and therefore `needs_model` / `can_resolve_use_case("chat")` —
  actually reads, and what the pre-existing model step already used. Binding through the prompt
  endpoint would have left `needs_model` true forever: a step that reports success while the readiness
  probe still says "no model" is the shape this repo calls a live reader of an unwritten key. Verified
  live: `active_models.json` ends at `{"chat": ["ollama:gemma4:12b"]}` and the GET flips to
  `needs_model: false, has_chat_binding: true`.
- [2026-08-13][OU-2] **DEVIATION (model is a required RAIL, not a wall).** The amendment says
  "model-provider required, the rest skippable"; the Design says "Skippable at every step". Both hold:
  `Continue` is unavailable until the model lane resolves (with a `disabledReason`), and a quiet
  "Set up later" link still leaves the step. OU-4's full-skip path depends on that escape existing.
- [2026-08-13][OU-2] **DISCOVERY (a provider key is stored in `config.json`, not the credential
  store).** An earlier draft of the key-entry copy read "they go to the credential store, never a
  config file". Driving the step against a real gateway falsified it: `POST /api/model-providers`
  writes the whole `options` object — `sensitive` fields included — into `config.json`, and no
  `credentials.json` is created. Confirmed on an isolated home with a throwaway key:
  `providers[0].options.api_key` sat there in plaintext. This is a pre-existing property of the
  endpoint the Store and Settings already use, so re-routing it is a security change well outside an
  onboarding atom (and would belong with SECURITY-HARDENING's keychain slice); what OU-2 fixed is the
  copy, which now describes only what happens ("fill in its settings, then test the connection").
  A first run must not make a storage promise the backend does not keep.
- [2026-08-13][OU-2] **DISCOVERY (`providerType` cannot separate a chat model from a speech model).**
  `CatalogEntry` exposed `providerType` and author-controlled `tags`, but not the provider's declared
  capabilities — and faster-whisper (`stt`), piper-tts (`tts`), sentence-transformers (`embedding`)
  and fal-image (`image_gen`) are ALL `providerType: "model"`. A `providerType`-only model lane would
  have offered a transcription app as the required chat provider, installed it, and then found nothing
  to bind. Fixed additively: `CatalogEntry.providerCapabilities`, populated from
  `provider.capabilities` at every construction site. Live against the first-party fixture the model
  lane lists 15 chat-capable apps and Whisper/Piper sit under Speech.
- [2026-08-13][OU-2] **DISCOVERY (an app's own Test may not validate its key).** Reusing the existing
  Test endpoint means its strength is the provider app's, not this step's: `anthropic-models` accepted
  an obviously bogus key and advanced to binding, because its test does not call the API. The failure
  path was therefore driven against a provider whose test does make a real call — Ollama pointed at a
  dead port — and it behaves as T1.2 requires: the provider's real error
  (`Cannot connect to host 127.0.0.1:9 …`) renders inline in a `role="alert"`, the form keeps its
  values, and correcting the endpoint in place succeeds. Worth knowing before OU-3: a passing Test is
  not a guarantee a real call will work, which is exactly why OU-3's cards carry a failure path.
- [2026-08-13][OU-2] **DISCOVERY (the install-consent surface needed its own module).** Importing the
  consent components from `AppsSection` made the bundler report
  `INEFFECTIVE_DYNAMIC_IMPORT: AppsSection.tsx is dynamically imported by App.tsx but also statically
  imported by EssentialsStep.tsx` — the whole Store page would have joined the first-load bundle, on
  the first-run path of all places. `ScanReport`/`ConsentModal`/`ClientInstallCommand`/
  `PermissionList`/`CronConsentList` moved to `web/src/pages/apps/installConsent.tsx`, imported by
  both the Store and onboarding; `permissionConsent.test.tsx` follows them. One definition, no
  re-export shim, and the warning is gone.
- [2026-08-13][OU-2] **V1 (partial — the essentials leg).** Fresh isolated home
  (`PERSONALCLAW_HOME=/private/tmp/ou2-live/home`, `PERSONALCLAW_FIRST_PARTY_APPS_DIR` → the
  `PersonalClawApps` clone, `AUTH_MODE=none` so the bind is loopback-only): name → all four lanes
  listed correctly → Review discloses OpenRouter's permissions with the Store's exact wording
  (enforced-permissions list, the app-messaging deny-by-default caption, the network advisory row) →
  Install Ollama → schema form seeded from the manifest → Save and test hits the LIVE local Ollama →
  bind `gemma4:12b` → lane reads "Ready" → Continue → "Start using PersonalClaw" → working dashboard
  greeting the entered name. Zero console errors. `POST /api/apps` fired exactly once, only after the
  Install click — the network log for the whole pre-install phase contains no install request at all.
  `entity_settings/onboarding.json` finished at
  `{"step": "done", "essentials": {"model": "ollama-models", "search": false, "speech": false, "channel": null}}`,
  which is also the done_when's "skipping all but model" path: three lanes untouched, first success
  reached. The real `~/.personalclaw` was never written (no `entity_settings/onboarding.json` there).
  The load-FAILURE branch (dead catalog → `LoadError` alert + retry, not four empty lanes) is covered
  by unit test with a cold `sessionStorage`, not driven live — killing a live catalog endpoint
  mid-flow was not worth a fixture for it. The rest of V1 (mid-flow reload resume, existing-home
  upgrade shows NO onboarding, <5 min end-to-end timing) belongs to OU-4, which owns resume.
- [2026-08-13][OU-7] **DONE (S3 T3.1 / C2).** New `web/src/pages/chat/approvalMeta.ts`:
  `deriveBlastRadius({tool, risk?, readOnlyCommand?}) → BlastRadius | undefined`, with
  `BlastRadius` exactly C2's `{writes, network, shell, readOnly}`. Pure, total, zero runtime
  imports. The design question the atom actually turns on is what to do when an input is
  missing, and the answer is that every boolean is a POSITIVE claim — `false` means "not
  established", never "verified absent" — and that when NOTHING is established the function
  returns `undefined` instead of an all-false object. An all-false object is the trap: rendered
  as chips it reads "no writes, no network, no shell, not read-only", a confident all-clear
  derived from zero evidence, and it is worst on the surface least able to check (the phone).
  `blastRadius?` being optional in C2 is already the unknown channel, so absence needs no new
  field. `readOnly` is claimed only on positive evidence — screening verdict, then EFFECTIVE-safe
  `risk`, then a `_READ_VERB_HINTS` name — and never survives an established write, so the
  function can only ever under-claim safety. Name evidence mirrors `task_modes.py`'s own hint
  tuples and its destructive→read-verb→mutating precedence, so the frontend and the backend agree
  on what a name means rather than inventing a second vocabulary.
  `RISK_ESTABLISHES_READ_ONLY` is a total `Record<ApprovalRisk, boolean>`: a new risk level is a
  type error, and there is no `default:` branch anywhere in the module. 23 tests cover the four
  representative tools from `done_when` on BOTH wire paths, the foreign-risk-value case
  (`ChatPage.tsx:911` casts the wire string into the union unvalidated), and a source-level rail
  that keeps the module a pure leaf nothing can gate on. Central rail falsified: dropping `bash`
  from `SHELL_HINTS` reds 6 of 23; restoring it returns 23/23.
- [2026-08-13][OU-7] **DEVIATION (C2's third input has no supplier).** C2 names
  "command-screening classification" as an input. It exists — `is_read_only_bash()`
  (`task_modes.py:88`) runs per interactive approval and its verdict is stored as
  `perm_meta["is_read_only"]` (`chat_runner.py:2593`) — but it is NOT on the `approval` WS
  payload, so no frontend caller can supply it. Resolved as an optional `readOnlyCommand?:
  boolean` parameter carrying that verdict's exact shape, documented as having no caller today,
  with the pass-through left to OU-8/OU-9. Deliberately NOT resolved by re-deriving read-only-ness
  client-side: deciding whether a command is read-only is security logic with an owner, and
  C2 says E4 if a gap tempts a change. The module never inspects a command string, and a test
  asserts it (`never re-implements the command screening it consumes`).
- [2026-08-13][OU-7] **DISCOVERY (`perm_meta["is_read_only"]` is a live writer of an unread
  key).** Tracing that input turned up a dead control: `chat_runner.py:2593` computes and
  persists `is_read_only` "for context-aware buttons", and NOTHING reads it — not the backend,
  not the frontend (`grep -rn is_read_only web/src/` is empty), not the rehydration path.
  It is written on every interactive bash approval and has never been consumed. Left as found,
  since fixing it is neither this atom's file nor its scope, but OU-8 should consume it rather
  than add a second screening input, and whoever audits the inert-surface baseline should know
  the key is a candidate.
- [2026-08-13][OU-7] **DISCOVERY (`memory_remember` infers as SAFE backend-side).**
  `_MUTATING_NAME_HINTS` (`task_modes.py:153`) has no `remember` token and `remember` is not a
  read verb, so `infer_risk_from_name("memory_remember")` falls through to `'safe'` — for a tool
  that durably persists a lesson. Reachable in practice for dict-defined MCP tools that ship no
  explicit `risk_level`. NOT fixed here: adding a token to live risk inference is exactly the
  security-logic change C2 forbids (E4). The frontend hint list carries `remember` so the chip is
  honest, the divergence is documented at the constant, and the conservative "an established
  write never claims read-only" guard makes the two consistent where it matters. Worth a separate
  atom on the owning plan.
- [2026-08-13][OU-7] **DISCOVERY (premise correction: the FE suite baseline).** The task brief
  cited a 222-file / 2211-test vitest baseline. Measured on this tree at `origin/main`
  (`811aaee4`) with the two new files moved aside, the real baseline is **220 files / 2179 tests /
  0 failed**; with them it is 221 / 2202 (+1 file, +23 tests). The cited numbers are from an older
  revision. Also corrected: `PendingApproval` is at `web/src/lib/api.ts:1661` and `ToolItem` at
  `:1008`, not the 1595/942 in the brief — the field-level findings themselves all held.
- [2026-08-13][OU-7] **DISCOVERY (the two risk vocabularies DO agree).**
  `ApprovalSegment['risk']` (`chatTypes.ts:49`) and `ToolItem.risk_level` (`api.ts:1008`) are
  byte-identical unions (`'safe' | 'caution' | 'destructive'`) and match the backend `RiskLevel`
  values, so there was no vocabulary to reconcile. `ApprovalRisk` is aliased from
  `ApprovalSegment['risk']` rather than re-declared, so the pair cannot drift apart later.
- [2026-08-13][OU-7] **No CHANGELOG entry, deliberately.** Nothing a user can perceive changed:
  the atom ships the derivation half with no call site, by design — OU-8 is the renderer. The
  in-app Updates panel renders `CHANGELOG.md`, so an entry here would advertise blast-radius
  chips that no surface draws yet. The entry belongs to OU-8.
- [2026-08-13][OU-8] **DONE (S3 T3.2 + V3 / C2).** `ApprovalCard` is now a four-zone decision
  brief and `useApprovalToasts` carries the compact form of the same brief.
  **WHAT** — tool + arguments (unchanged, `ui/ApprovalPrompt`'s truncated mono line).
  **WHY** — the runner's `purpose`, and nothing at all when it supplied none (no filler line).
  **WHAT IT CAN TOUCH** — a named `<ul>` of ESTABLISHED blast-radius facets from OU-7's
  `deriveBlastRadius`, rendered through a new shared vocabulary in `approvalMeta.ts`
  (`establishedFacets` / `blastRadiusLine` / `BLAST_RADIUS_FACET_ORDER`, labels in a total
  `Record<keyof BlastRadius, …>`). Positives only: `undefined` and an all-false radius both
  render NO zone, because four "no" chips would be a confident all-clear from zero evidence.
  **HOW FAR THE ANSWER REACHES** — a `Segmented` (`size="sm"`) remember-scope strip plus the
  promise it makes, in visible text, above the verbs.
  Actions collapse from four scope-encoding buttons to **Allow / Deny**, with the scope carried
  in Allow's accessible name. `ui/ApprovalPrompt` gained ONE optional `scope` slot (+ doc/anatomy
  update); the companion passes nothing and its 13 tests pass untouched. Toast: new pure
  `app/approvalToast.ts` (`approvalToastMessage`) — one line, no verbs, no scope, drawing its
  words from the same vocabulary so the card and the nudge cannot drift.
  **Tests:** `ApprovalCard.test.tsx` (16), `approvalToast.test.ts` (5), +6 in
  `approvalMeta.test.ts`; `approvalOutcome.test.tsx`'s four-label assertion re-pointed at the new
  action row. FE suite 223 files / 2229 tests / 0 failed (was 221 / 2202).
- [2026-08-13][OU-8] **DEVIATION (C2's `tool_always` remember-scope has no honest home — SHIPPED
  WITHOUT IT).** C2 names three scopes (`session` / `tool_always` / `no`) persisting "via the
  existing approval-preference path". Two of the three map cleanly onto actions
  `api_chat_session_approve` already implements — `no` → `approved`, `session` → `trust` — and the
  card also keeps the pre-existing agent-wide grant (`trust_agent`). **Nothing in this codebase
  remembers an approval decision per TOOL.** `trust`, `trust_agent` and `yolo` are all "every
  tool" at a widening blast radius (`chat_handlers.py:2105-2160`), and `set_approval_policy`
  (`session.py:1579`) is keyed by session, not by tool. The one per-tool matcher that exists,
  `config.hooks.auto_approve_tools` (consumed live at `hooks.py:394` via
  `chat_runner.py:2284`), has **no write path** and is pinned into a `HookManager` at gateway
  construction (`gateway.py:662`), so a config write would keep asking until a restart — and its
  patterns are `fnmatch` over the tool TITLE, which for an ACP agent is a command string
  ("Running: ls -la"), so a literal tool name would match nothing or too much depending on the
  provider. Building that write path + live mutation is a change to the approval gate, which C2
  forbids (E4). Labelling `trust` "always allow this tool" was the alternative and is a
  security-relevant lie about what a click did, so the option is **absent**, the reasoning is
  recorded at `REMEMBER_SCOPES`, and a test asserts no label implies per-tool memory.
  **Remainder for a future atom:** a genuinely per-tool grant needs a persisted per-tool policy
  the runtime reads live — a security-surface change with its own scope, not a label.
- [2026-08-13][OU-8] **DEVIATION (`readOnlyCommand` pass-through NOT wired — it would add
  nothing on this path).** OU-7 left `perm_meta["is_read_only"]` unbroadcast and named the
  pass-through OU-8/OU-9's. Measured: on the chat path it is redundant. `resolve_effective_risk`
  reaches `'safe'` THROUGH read-only-ness (`task_modes.py:245`), the `approval` frame already
  carries that effective risk, and `RISK_ESTABLISHES_READ_ONLY.safe` is `true` — so a read-only
  bash call already derives `readOnly` today, as the live drive shows (`Safe` → chips "Runs a
  command" + "Reads only"). Putting the flag on the wire would change no rendered chip here; its
  value is the companion path, which has no `risk` at all, and that payload is OU-9's. Left for
  OU-9 with the reason, rather than touching `src/` for a no-op.
- [2026-08-13][OU-8] **DEVIATION (no `primary` tone on Allow).** `ui/ApprovalPrompt` documents
  `primary` as "the least-privilege default", which the old four-button row satisfied. With one
  Allow whose breadth depends on the scope, a solid primary fill would read as "this is the
  action to take" — advocacy, on the one surface whose job is to make someone weigh a decision.
  Allow is neutral, Deny keeps its tinted danger edge, and a test asserts Allow's inline
  background is not `--color-primary`. The companion's Allow still passes `primary`; that is
  named in the prompt's doc as worth revisiting with it, not changed here.
- [2026-08-13][OU-8] **V3 (validated as a user, real gateway, isolated home).**
  `PERSONALCLAW_HOME=/private/tmp/ou8-live/home`, gateway on `:10088` from THIS worktree
  (`PYTHONPATH` + a fresh `static/dist` symlink so the served SPA is this build). No hosted-model
  credentials were available in the validation environment, so the
  provider is the real `openai-compatible` app installed from the first-party source and pointed
  at a local OpenAI-compatible stub (`/private/tmp/ou8-live/stub_openai.py`, streaming path,
  returns one deterministic tool call per turn). Everything downstream of the model is the real
  system: real `approval` WS frame, real card, real
  `POST /api/chat/sessions/{s}/approve`, real resolution.
  Drove three cards and both answers: **risky** `bash` (`rm -rf …`) → `Destructive` + one chip
  "Runs a command"; **benign** read-only `bash` (`ls -la`) → `Safe` + "Runs a command" +
  "Reads only", Allowed once → persisted `resolved: "approved"`, the tool ran, the turn finished;
  a **denial** → persisted `resolved: "rejected"`; and a **`write_file`** call → `Caution` +
  "Writes files", with the scope switched to "This chat" → Allow's name became "Allow write_file —
  this chat: Every tool in this chat runs without asking, until you change it back." and the
  session's mode came back `trust`. Screenshots (README/asset-list feed, plan 36 T3.2):
  `ou8-risky-card-zoom.png`, `ou8-benign-card-zoom.png`, `ou8-writes-card-zoom.png`,
  `ou8-benign-allow-settled.png` — in `/private/tmp/ou8-live/shots/` and copied to the workspace
  root beside the other UX-loop shots. They are NOT placed in `README.md`: the README has no
  screenshot section today and that section is DISCOVERABILITY-LAUNCH's to design.
- [2026-08-13][OU-8] **Falsified the central rail.** Made `BlastRadiusChips` enumerate all four
  facets with negative labels when nothing is established (the exact defect OU-7's `undefined`
  exists to prevent): 4 tests RED, including "renders NO facet zone at all when the inputs
  establish nothing". Restored → 16/16 green. Also flipped Allow to `tone: 'primary'`: the
  no-advocacy rail "does not make Allow the visual primary" went RED alone; restored → green.
- [2026-08-13][OU-8] **DISCOVERY (the approval record is memory-only, so a restart erases it).**
  `_save_session_to_history` deliberately DROPS role `permission` when writing a session's JSONL
  (`chat_persistence.py:603`), so an approval row — pending or settled — lives only in the
  in-memory `session.messages`. Verified live in the other direction: the resolution IS stamped
  in memory (`GET /api/chat/sessions/<key>` came back with `resolved: "approved"` on the allowed
  card and `"rejected"` on the denied one, and only the two cards I deliberately left unanswered
  read `null`), and the settled outcome line renders from that. But nothing survives a gateway
  restart: the transcript that is the permanent record of a security decision loses the decision.
  Not touched — backend lifecycle, not this atom's file — and the same family as #541. Worth its
  own atom on the owning plan. (`approvalOutcome.test.tsx`'s history-hydration parity tests keep
  the FE side honest for whenever the rows do start persisting.)
- [2026-08-16][OU-3] **DONE (S1 T1.3).** A fourth step (`name → essentials → try → ready`) with three
  cards that EXECUTE, not pre-fill: knowledge = `POST /api/knowledge/items` → `GET
  /api/knowledge/search-for-context` (shows the returned passage, its match type and token cost);
  reminder = `POST /api/triggers` with the `notify` action → `POST /api/triggers/{id}/run` → `GET
  /api/notifications` (shows the notification that actually landed + next fire time); loop = `POST
  /api/loops` (`general`, `max_cycles: 1`) → `PATCH /api/loops/{id}` `{action:'start'}` (shows the
  status the START response reported). Flows live in `web/src/app/onboarding/tryOneFlows.ts`, apart
  from `TryOneStep.tsx`'s chrome. `first_success` finally has its writer — OU-1's last remainder —
  and each card patches only its own key. One backend-facing line: `KnowledgeContextCard.content` in
  `web/src/lib/api.ts` (the endpoint always sent the passage; the interface omitted it). Not built on
  PEP-1's `PresetCard`: it is deliberately ONE tab stop with no interactive children, and these cards
  grow a run button, then an outcome link, then a deep-link and a retry.
- [2026-08-16][OU-3] **V (driven as a user, scripted Playwright, real gateway, fresh home).**
  `PERSONALCLAW_HOME=/private/tmp/ou3-wt/.dev-home`, gateway `:10088` from THIS worktree
  (`PYTHONPATH` + its own `static/dist` symlink). Drove the flow with the essentials step
  **SKIPPED — no model provider configured at all**, which is the strongest available proof the
  cards need no paid inference: a card that secretly required a completion would have failed
  outright. **Click → visible outcome: knowledge 0.94s / 1.05s, reminder 1.01s / 1.14s, loop
  1.22s / 1.38s** across two runs — the budget is 2 min. Backend really changed:
  `first_success: {knowledge:true, trigger:true, loop:true}`, `knowledge items=1`, a real
  `clock:daily-check-in` trigger (`At 09:00 AM`, `next_fire_at 2026-08-16T09:00:00+00:00`), a real
  loop `status=running cycles=1`, and a real `Your daily check-in` notification. Recap read
  "First success: 3 of 3 tried"; the ordinary finish still lands on `#/dashboard`. Zero `pageerror`,
  zero console errors. Shots in `/private/tmp/ou3-live/shots/`.
- [2026-08-16][OU-3] **V (the failure path, driven for real).** A provider refusal was injected at
  the HTTP boundary with Playwright `route` — the exact envelope a 401 reaches the SPA as
  (`{"error": "Incorrect API key provided: sk-abc***xyz. …"}`, status 401) — while the SAME origin
  answered a healthy `200` to `GET /api/knowledge/stats` in the same page load, so the failure is
  the real CALL and not the box. Everything downstream is the real system. Measured: the sentence
  renders **verbatim**; the copy names the passing-Test situation; "Try again" is offered in place;
  siblings stay usable; the deep-link is present, and clicking it landed on
  **`#/settings/providers`** with headings `["Providers","Agent providers",…]` — the real panel, not
  the Settings bento home an unknown sub-segment silently renders. The non-provider variant
  (`POST /api/loops` → 400 `Task is too short`) routed to **`#/settings/doctor`** and did NOT claim
  the provider had refused.
- [2026-08-16][OU-3] **BUG FOUND BY THE LIVE DRIVE — the deep-link mechanism shipped green and
  broken.** `App.tsx`'s guard holds a non-onboarded user on `#/onboarding`, so the exit destination
  is handed to the guard (`onboarding/exitTo.ts`) rather than raced against it. v1 cleared the
  destination on read; every unit test passed and it landed on `#/dashboard` on every real click.
  Cause: **the guard effect is re-entrant.** `navigate` sets `location.hash`; `route` only updates
  when the browser's async `hashchange` fires, so any App re-render in that window runs the effect
  again with `onboarded === true` and a stale `route === 'onboarding'`. Run one read the destination
  and navigated correctly; run two read `''`, took `|| 'dashboard'`, and overwrote the hash — the
  guard silently undid itself. Fixed by making the read idempotent (`peekOnboardingExit`) and
  clearing on a later branch once the route has provably left onboarding. The tests could not see it
  because they asserted read-and-clear, which was the WRONG contract; the rail now pins idempotence
  (N reads resolve identically) and forbids the consuming spelling in the guard.
- [2026-08-16][OU-3] **Falsified, five mutations — one reded NOTHING and was a real defect.**
  (1) knowledge card returns its outcome without calling either endpoint (the navigate-don't-execute
  shape) → **4 RED**, incl. `expected "spy" to be called at least once` and `Unable to find an
  element with the text: /Everything PersonalClaw knows…/`. (2) Settings deep-link removed from the
  failure branch → **7 RED**, incl. `Unable to find role="button" and name "Open model provider
  settings"` on all three cards. (3) loop's `status !== 'running'` guard dropped (create-only) →
  **1 RED**, `Unable to find role="alert"`. (4) knowledge card echoes the REQUEST body as "the
  passage" instead of the retrieved one → **RED NOTHING.** The fixture's response `content` was a
  PREFIX of `KNOWLEDGE_SEED.content`, so the assertion could not tell "read off the response" from
  "echoed the request" — and that vacuity hid a real defect, because the retriever answers from the
  whole corpus and the matched passage is frequently NOT the note just written, so an echoing card
  would show text that did not answer the question and label it "the passage". Fixture rewritten to
  share no phrase with the seed (`RETRIEVED_ONLY`) plus a negative assertion that the SENT note is
  not shown as the answer; the mutation now reds. (5) guard stops reading the exit destination →
  **3 RED**.
- [2026-08-16][OU-3] **Visual defect fixed at source.** The card's run button and the Settings
  deep-link were `variant="secondary"`, which is `bg-surface-high` — the same token as the card they
  sit on — so both rendered as plain text with no control chrome (visible in the first capture).
  Re-toned to `variant="tonal"` (the kit's primary-tinted chip CTA), which is visible on that
  surface and is not the step's primary; `Continue` keeps that. No baseline was raised: no raw
  `<button>`, no raw form control and no hand-rolled spinner was added, so
  `primitiveAdoption.baseline.json` is untouched.
- [2026-08-16][OU-3] **DISCOVERY — three model-dependent knowledge/loop paths fail OPEN and
  silently, so no card could have been built on them.** Measured on a home with no provider:
  `POST /api/knowledge/items`' ingest node-graph logs `ProviderWorker: request failed: No provider
  entries registered` and still reports `processing_status: "done"` with `insights: {}`;
  `POST /api/knowledge/items/{id}/generate-intelligence` answers **200** with every `node_phase`
  `"done"` and `insights` empty; `POST /api/loops/classify` answers **200** in 0.1s with
  `classified: false` and no error. Each is a live reader of a model that quietly reports success
  when the model is absent — a card built on any of them could never tell the user its call failed,
  which is why all three flows use paths whose failure is REPORTED. Not touched: backend behaviour
  on other plans' surfaces, and each is worth its own atom on the owning plan.
- [2026-08-16][OU-3] **Note on the regenerated `docs/design/consistency-audit.json`.** Checked
  rather than assumed, per the generated-baseline rule: `origin/main` re-derives
  `filesScanned: 476` while the COMMITTED value said `470`, so six of the nine-file delta is
  pre-existing staleness on main, and this atom contributes exactly **+3** — its three non-test
  modules (`tryOneFlows.ts`, `TryOneStep.tsx`, `exitTo.ts`); the scanner skips `*.test.*`.
  `driftHits: 7` and `filesWithDrift: 6` are unchanged in all three states, so the new files add no
  drift. Regenerated in this commit rather than left dirty.
