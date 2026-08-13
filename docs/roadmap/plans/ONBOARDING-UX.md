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
