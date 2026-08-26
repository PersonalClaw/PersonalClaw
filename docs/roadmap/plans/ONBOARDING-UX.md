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
  `save_session_to_history` deliberately DROPS role `permission` when writing a session's JSONL
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
- [2026-08-16][OU-5] **DONE (S2 T2.1 + T2.3 + V2 / C4).** New `web/src/app/navDisclosure.ts` is the
  whole model in one leaf module: `{mode: 'starter'|'expert', pinned: string[]}` in
  `localStorage['nav-disclosure']`, one `isDisclosed(id, mode, pinned)` rule, one
  `undisclosedCount()`, and a `useNavDisclosure()` hook that reads SYNCHRONOUSLY on mount — no
  probe, therefore no flash of the wrong rail on any load. `ui/NavRail` gained ONE optional
  `disclosure` prop (`{expanded, moreCount, onToggle}`) and renders an "Everything +N" /
  "Show fewer" row at the end of scroll order; it is handed already-FILTERED `items`, so which
  surfaces are starter and how pins persist never live half in the rail. `App.tsx` filters the
  rail, counts what is held back and carries the auto-pin effect; `DesignPanel` (`#/settings/design`
  — the panel whose own search keywords are "design theme appearance …") gained a **Navigation**
  section whose `Show every surface` switch drives the same one setting, not a second mechanism.
  Five starter rows out of 18 static destinations, so 13 are held back on a fresh install.
- [2026-08-16][OU-5] **The clause with teeth, and how it is held.** Disclosure hides rail ROWS and
  nothing else: `rendered` is untouched by it and the CommandPalette is still built from the full
  `NAV`, so a deep link, a palette "Go to", a Discover tip and an in-app link all render a hidden
  surface — and reaching one PINS it, so the rail grows with use instead of asking to be
  configured. `web/src/app/navDisclosure.test.tsx` drives the REAL shell for this (19 tests,
  `<App/>` under its `main.tsx` provider stack) and asserts `#/tools` on the page's own `h1`, not on
  a route string, because a blank page and a rendered page both "navigate".
- [2026-08-16][OU-5] **The upgrade marker is the record's ABSENCE — no new field.** C4 asks for an
  "onboarding-completed-before-this-version marker". `Onboarding`'s `finish()` writes
  `mode: 'starter'`, and that is the ONE act only a fresh install performs (it is what commits
  identity and flips `onboarded`), so a stored record means "onboarded under this version" and no
  record means "onboarded before it" → `expert`. It is also the safe failure direction: an absent or
  unreadable preference shows everything rather than hiding surfaces someone has used for months.
  `finish()` never clears `pinned`, so "Restart onboarding" (Settings → Account) resets the MODE
  without taking away a surface already reached.
- [2026-08-16][OU-5] **DEVIATION (`dashboard` joins the Design's starter set).** The Design names
  "Chat, Inbox, Apps, Settings". `dashboard` (Home) is the app's LANDING route
  (`useHashRoute('dashboard')`), and a rail that omits the page it opens on is a defect rather than a
  decision — the very first thing a fresh user would see is a rail with no row for where they are.
  Owner task 3 ("decide the starter set … before T2.1") has no recorded ruling, so the Design's four
  plus Home is what shipped; swapping the set is a one-line change to `STARTER_NAV_IDS`.
- [2026-08-16][OU-5] **DEVIATION (section headers are dropped on the starter rail).** Measured on the
  live starter rail before the fix: five rows under three headings, with `PLATFORM` sitting over
  Inbox alone and `APPS` over Store alone. A heading per item groups nothing, and the starter rail is
  one curated group by construction (essentials + what you have opened) — which is also what the
  Design means by "two rail sections". Expert mode keeps Platform / Capabilities / Apps untouched.
- [2026-08-16][OU-5] **DEVIATION (contributed-app tiles are exempt from disclosure).** An `app/<name>`
  tile only appears in the rail because the user opted it in from the app's detail panel ("Show in
  navigation", persisted in `nav-apps`). Hiding it would silently undo a choice made by hand, so
  `isDisclosed` returns true for `app/*` in both modes. The Store tile itself is in the starter set.
- [2026-08-16][OU-5] **DEVIATION (auto-pin fires in starter mode only).** In expert mode nothing is
  hidden, so a visit reveals nothing — and pinning every surface browsed while expanded would
  silently empty out the difference "Show fewer" restores, turning the toggle into a one-way door.
  The consequence, accepted: surfaces visited while expert is on are not carried into a later
  starter rail.
- [2026-08-16][OU-5] **Falsified — seven mutations, and TWO reded nothing at first.** Both were real
  gaps in the tests rather than vacuous mutations, and both are now closed. (1) Removing
  `setNavMode('starter')` from `Onboarding.finish()` reded **nothing**: no test drove the
  fresh-install write, so the starter rail could have shipped to nobody. Two tests now live in
  `onboardingProgress.test.tsx` (the file that already owns what finishing writes), and the mutation
  now reds both. (2) Removing the `moreCount > 0` guard reded only a source-level rail — an
  "Everything +0" control would have shipped. A behavioural test ("renders NO control once nothing is
  left to reveal", every non-starter id pinned) now covers it and doubles as a ratchet: a new rail
  destination must be classified starter or listed there. The five that reded correctly: routing
  `rendered` through `isDisclosed` → 3 RED (`Unable to find … heading "Tools"`); auto-pin as a no-op →
  3 RED (`expected [] to include 'tools'`); filtering the palette by disclosure → 1 RED
  (`Unable to find role="option"`); defaulting an absent record to `starter` → 4 RED; dropping
  `aria-expanded` → 3 RED.
- [2026-08-16][OU-5] **V2 (driven as a user, scripted Playwright, real gateway, isolated home).**
  Fresh `PERSONALCLAW_HOME=/private/tmp/ou5-wt/.dev-home-ou5`, gateway on `:10055` from this worktree
  (`PYTHONPATH` + its own `static/dist` symlink so the served SPA is this build), `AUTH_MODE=none`
  (loopback-only). Fresh home landed on onboarding (`h1: "Welcome to PersonalClaw"`, no
  `nav-disclosure` record); name → essentials → "Set up later" → "Start using PersonalClaw" wrote
  `{"mode":"starter","pinned":[]}` and the rail came up **Home · Chat · Inbox · Store ·
  "Everything +13" · Settings** with no section headers. Deep link `#/tools` → `h1: "Tools"`, 2292 DOM
  nodes, rail grew a **Tools** row, store `{"mode":"starter","pinned":["tools"]}`; reload → both
  survived. ⌘K → typed "Learning" → Enter → `h1: "Learning"` and `pinned:["tools","learning"]` — the
  palette reaches what the rail hides and the visit pins it. The rail control focused by keyboard
  measured `outline: solid 2px rgb(154,155,156)`, `boxShadow` layers **0** (the app's ring is
  `:focus-visible { outline }`, exactly as briefed); Enter expanded it to all 18 destinations with
  the headers back and the name flipped to "Show fewer, hide 11 surfaces"; reload kept `expert`.
  `#/settings/design` → the **Navigation** switch read `aria-checked="true"`; clicking it returned
  the rail to starter **plus the two earned pins**. Upgrade fixture (`localStorage.removeItem` →
  reload): full 18-row rail, and the store stayed `null` — an expert install writes nothing until the
  user acts. Mobile 390×844: drawer opened from "Expand sidebar", the control present and named, and
  Enter on it expanded the rail. Collapsed 64px rail: icon only, `aria-label` intact, visible text
  empty (the `title` carries it). **Zero console errors and zero pageerrors across the whole run.**
- [2026-08-16][OU-5] **DISCOVERY (the rail has 18 destinations, not 19).** Counted from `NAV`: the
  expanded rail renders 19 buttons because one of them is the disclosure control. Worth knowing for
  OU-10's tour, which anchors on the rail.
- [2026-08-16][OU-5] **Known limit, deliberate.** The preference is per-DEVICE, which `identity.tsx`
  already states as the house rule ("Per-device prefs (theme, width, nav state) stay in localStorage;
  identity does not"). So a SECOND browser opened against an already-onboarded install has no record
  and shows the full rail. That fails open — it never hides a surface from someone using it — and
  matches how theme, rail width and rail collapse already behave. Moving it server-side would mean a
  probe on every boot and either a flash of the wrong rail or a new boot gate, for a preference the
  rest of the family keeps local.
- [2026-08-16][OU-4] **DONE — the flow's done screen, its resume, one door out, and a CLI pointer.**
  Shipped in `web/src/app/Onboarding.tsx` (+ `identity.tsx`, `pages/settings/AccountPanel.tsx`,
  `src/personalclaw/env.py`, `gateway.py`, `cli_setup.py`). Details of what each part does and why
  live in [`../atomic/OU.md`](../atomic/OU.md) `OU-4`; this entry records the findings and V1.
- [2026-08-16][OU-4] **The reader was the whole point.** `step` had three writers (OU-1 stored it,
  OU-2/OU-3 wrote it) and no reader, which reads exactly like a working resume until someone
  reloads. Every mid-flow reload restarted at the essentials step and offered to redo installs the
  home had already recorded. The fix is small; the shape of the bug is the lesson — a stored key
  whose reader is a later atom is indistinguishable from a working feature until the reader lands.
- [2026-08-16][OU-4] **DISCOVERY — the "skip" the atom asks for made DEAD CODE live.**
  `finish()` has always ended `setName(savedName || 'Operator')`, and that fallback was
  unreachable: `commitName` refuses an empty name and no row can be jumped forward, so `savedName`
  was always non-empty at finish. A full-skip path is precisely what reaches it. `'Operator'` was
  also a second literal — `AccountPanel`'s empty-name save falls back to the same word — so it
  became `identity.DEFAULT_USER_NAME`, used by both. The skip link NAMES the default it will use
  ("Skip setup — start as Operator, rename yourself in Settings"): a visible default beats a
  silent rename.
- [2026-08-16][OU-4] **DEVIATION (resume asks for the name first).** `done_when` says "resumes at
  the persisted step". A resumed flow lands on the name step, and committing it jumps to the
  persisted step. The name is deliberately NOT in this state (OU-1's ruling: identity lives on the
  server, `onboarded` is derived from it being non-empty) and it is committed only at the end, so
  the alternatives were to fabricate a name for someone who typed one before the reload, or to keep
  a second copy of the name in a device-local draft. Re-typing one field is the honest cost of
  OU-1's ruling; nothing the earlier visit actually DID is redone.
- [2026-08-16][OU-4] **DEVIATION (`done` is not a resume target).** Only `essentials` and
  `first_success` resume. A stored `done` means a previous run finished — "Restart onboarding" in
  Settings → Account, or a `finish()` whose identity write never landed — and dropping such a user
  on the recap would skip the very steps they asked to run again. It needs no extra write path and
  leaves `AccountPanel`'s restart untouched.
- [2026-08-16][OU-4] **DEVIATION (a resumed try-one visit restores the count, not the cards).**
  Only the `first_success` FLAGS survive a reload, not the outcomes the cards rendered, so the
  cards start idle and `leaveTryOne` floors the recap at the persisted count. Measured live: card
  run → reload → recap read "First success: 1 of 3 tried" rather than "Nothing tried yet".
- [2026-08-16][OU-4] **The done screen hands over controls rather than describing them.** The
  Bounciness dial is `ScalarControl` on the `--bounciness` token — the same object
  `#/settings/design` renders, so there is no parallel dial to keep in step — and "Show every
  surface" is OU-5's one setting, with the same accessible name the Appearance switch uses. The
  switch only states intent; `finish()` performs the single `setNavMode` write, so C4's
  fresh-install marker and the user's choice cannot disagree and an abandoned flow leaves NO
  record (absence is how the shell tells an upgrade from a fresh install). Measured: flipping it
  wrote nothing to `localStorage` until "Start using".
- [2026-08-16][OU-4] **The CLI predicate was extracted, not copied.** `personalclaw setup` needs
  the same "is a browser reachable" answer the gateway's auto-open branch had inlined
  (`SSH_CONNECTION`/`SSH_CLIENT` + `DISPLAY`/`WAYLAND_DISPLAY` + a darwin exemption). It moved to
  `env.browser_available()`; `gateway.py` calls it, and a rail asserts those env names no longer
  appear in `gateway.py` at all. Deliberately NOT merged with `origin.py`/`cli_doctor`'s
  "is this host remote" checks — a remote host WITH a display can open a browser. The wizard is
  otherwise unchanged, which answers this plan's open question with "the dashboard owns onboarding".
- [2026-08-16][OU-4] **V1 (driven as a user, real gateway from this worktree, isolated home).**
  `PERSONALCLAW_HOME=/private/tmp/ou4-home`, `AUTH_MODE=none` (loopback-only), port `:10044`, its
  own `static/dist` symlink so the served SPA is this build. Three of the four legs verified, one
  partially:
  · **mid-flow reload → resumes at the persisted step — VERIFIED, twice.** Name → Continue wrote
    `step: "essentials"` (identity still `""`, so the flow still gates); reload → step 1; name →
    Continue → **Step 2**. Then "Set up later" wrote `step: "first_success"`; reload → name →
    Continue → **"Step 3 of 4: Try one"** with the essentials step NOT re-run, and the stored step
    still `first_success` (never walked back).
  · **full-skip path → working dashboard — VERIFIED.** Fresh home, fresh browser context, one
    click on "Skip setup — start as Operator…" from step 1 → `#/dashboard` rendered
    ("Good afternoon, Operator"), `user_name: "Operator"`, `step: "done"`, rail
    **Home · Chat · Inbox · Store · "Everything +13" · Settings**, store `{"mode":"starter","pinned":[]}`.
  · **existing-home upgrade shows NO onboarding — VERIFIED.** Home with a name, `onboarding.json`
    deleted (a pre-OU-1 home) and `localStorage` cleared: deep-linking `#/onboarding` bounced to
    `#/dashboard`, no onboarding UI, and the rail showed all 18 destinations + "Show fewer" with
    the disclosure record still `null` — the upgrade default.
  · **full flow to first success under 5 min — PARTIAL.** Name → essentials → all three try-one
    cards → done screen measured **9.3 s** of driven interaction (all three cards green:
    `{"knowledge":true,"trigger":true,"loop":true}`, recap "3 of 3 tried"). The provider segment
    (install → key → Test → bind) was NOT exercised: it needs a real credential, which this
    session did not have, so the essentials step was skipped every run. The <5 min claim therefore
    stands only for the flow around that segment.
  · **The three done-screen pointers, driven:** "Open the Inbox instead" left the flow and landed
    on a rendered `#/inbox` (`h1: "Inbox 0 pending · 0 total"`) with `step: "done"` recorded; the
    unlock switch ON at finish produced `{"mode":"expert"}` and the full 19-row rail; the
    Bounciness slider is the real `ScalarControl` (`min 0 / max 1`, its own "Reset Bounciness").
    Keyboard: all four new controls are in the tab order and the focused switch measured
    `outline: 2px solid` (the app's `:focus-visible` ring). **Zero console errors across the run.**
- [2026-08-16][OU-4] **Harness artifact worth recording (not a defect).** Re-pointing the SAME
  browser context at a wiped home showed `#/dashboard` and greeted the OLD name while the server
  returned `user_name: ""` — a stale service-worker/HTTP cache from the previous home, in a
  profile that had been driven through onboarding minutes earlier. In a genuinely fresh isolated
  context the same URL landed on onboarding. Wipe the context (or use a new one) between home
  resets, or the guard looks broken when it is not.
- [2026-08-16][OU-4] **Falsified, three mutations, all red.** (1) `resumeTarget` returning `null`
  for `first_success` → **4 RED** in `onboardingProgress.test.tsx`
  (`Unable to find role="button" and name "stub-tried"`;
  `AssertionError: expected [ 'essentials' ] to deeply equal [ 'first_success' ]`). (2) `finish()`
  writing `setNavMode('starter')` unconditionally, ignoring the done screen's switch → **1 RED**
  in `doneScreen.test.tsx` (`AssertionError: expected 'starter' to be 'expert'`). (3) Dropping the
  `_print_dashboard_pointer()` call after the wizard's full-run completion line → **1 RED** in
  `test_onboarding_setup_pointer.py` (`assert 1 == 2` on
  `len(pointer_calls) == len(done_prints)`) — the AST rail catches a pointer that exists but is
  never reached on one of the two paths.
- [2026-08-16][OU-4] **Note for OU-10 (now on the ready frontier).** The done screen is where the
  tour launches from, and it already carries three controls plus the finish button; the tour entry
  belongs alongside them, not instead of one. `exitTo.ts` is the seam for anything that has to
  leave the flow — a plain link cannot, because the route guard bounces it.
- [2026-08-16][OU-10] **DONE (S5 T5.1 + T5.2 / rulings b + c).** The replayable product tour, in
  two pieces plus five anchors. `web/src/ui/SpotlightTour.tsx` (+ `.doc.ts`) is the presentation
  primitive: portal, four dim bands around a ring on one element, a step card, focus trap, Escape,
  reduced motion. `web/src/app/onboarding/ProductTour.tsx` owns the five stops
  (rail → chat → inbox → approvals → settings), their copy, and the navigation between them;
  `web/src/app/onboarding/tourLaunch.ts` is the launch seam. Anchors: `data-tour="rail"` on
  `ui/NavRail`'s `<nav>`, `"chat"` on `ChatPage`'s composer stage, `"inbox"` on `InboxPage`'s queue
  column, `"approvals"` on `DashboardPage`'s "Needs you" band, `"settings"` on `SettingsHome`'s
  search. Entry points: a "Take the quick tour" button beside "Start using PersonalClaw" on the
  done screen, and a non-dismissible "Replay the tour" card at the top of `DiscoverPage`.
- [2026-08-16][OU-10] **The launch seam exists for the same reason `exitTo.ts` does.** The done
  screen's tour button runs `finish()`, and `finish()` is what commits identity and flips
  `onboarded` — which makes `App` render the shell INSTEAD of the flow. The button's own component
  is gone before a tour could mount, so it cannot render one; it can only leave a request behind.
  `tourLaunch.ts` is a pending flag plus an in-tab event: the done screen sets the flag before the
  shell exists and the shell CONSUMES it on mount, while Discover sets it inside an already-mounted
  shell and a live listener consumes it. Consuming is what stops a request replaying on the next
  thing that mounts. Module state, not storage — see the telemetry note below.
- [2026-08-16][OU-10] **Zero telemetry is structural here, not a promise.** The tour has no
  progress field, no "seen it" flag and no request on any step: the stop index is React state and
  the launch request is module state. It is REPLAYABLE rather than resumable, which is what lets it
  have no memory at all. Held by two rails — a recorder over the whole `api` surface asserting no
  tour-shaped call during a full walk, and a source rail asserting `ProductTour.tsx`,
  `tourLaunch.ts` and `SpotlightTour.tsx` import no gateway client and name neither
  `localStorage` nor `sessionStorage`. Confirmed live too: 0 POSTs across a five-stop walk in the
  browser's network panel (only the shell's own GET polls).
- [2026-08-16][OU-10] **DEVIATION (the overlay lives in `ui/`, not in `ProductTour.tsx`).** The
  atom names one file. Two exist, because `primitiveAdoption.baseline.json` holds `rawDialog` at
  **0** outside `web/src/ui/` — a page-level `role="dialog"` reds the ratchet by construction. So
  the modal overlay is a `ui/` primitive (with the `.doc.ts` that `uiDocs.drift.test.ts` demands)
  and `ProductTour.tsx` is the app-specific half: stops, copy, routes, launch. That is also the
  better layering, and `dialogFocusContract.test.tsx`'s aria-modal census now names it as the
  fourth entry — with a trap, which is the whole point of that census.
- [2026-08-16][OU-10] **DEVIATION (the tour NAVIGATES; "S2 auto-pin unchanged" is what makes that
  safe).** T5.1 asks for "anchor ids on toured surfaces", so the tour goes to each surface rather
  than describing it from the dashboard. Every route it visits — `chat`, `inbox`, `dashboard`,
  `settings` — is in `STARTER_NAV_IDS`, so `isDisclosed` is already true and OU-5's auto-pin effect
  returns before it writes: a full walk leaves the disclosure record byte-identical. Asserted as an
  outcome (`readNavDisclosure()` unchanged after the walk) with a vacuity guard beside it (a deep
  link to `#/tools` still pins). Measured live as well: after a browser walk the record was
  `{"mode":"starter","pinned":["discover"]}` — and `discover` is there because the SESSION opened
  `#/discover` by URL, not because of the tour.
- [2026-08-16][OU-10] **DEVIATION (the Discover entry is not a catalog tip).** T5.2 lists the
  server-side discover catalog in its Files column. A catalog tip carries a dismiss, and dismissing
  the tour would delete the product's only replay entry — which contradicts the same task's "a
  dismissed-everything user still finds the tour". So the card is client-side, non-dismissible, and
  rendered OUTSIDE every branch of the page: it survives Discover being switched off, every tip
  being dismissed, and a failed `/api/legibility/discover`. Three tests, one per state.
- [2026-08-16][OU-10] **DISCOVERY (the "pattern donor" donates a pattern, not a mechanism).**
  `ui/PlanningWalkthrough.tsx` is a full-page split view, not an overlay: it contributes the
  stepwise idiom, the expressiveness-scaled step choreography and the host-owns-the-index shape.
  The portal + focus-trap machinery came from `ui/Modal.tsx` and `useFocusTrap`, which is where the
  repo's overlay contract actually lives.
- [2026-08-16][OU-10] **Two placement defects found by DRIVING it, invisible to jsdom** (which has
  no layout, so every rect is 0×0 and the overlay takes its unanchored path). (1) The card was
  placed under-else-over-else-clamp-to-top, so on the rail stop — an anchor as tall as the viewport
  — it landed ON the rail and covered the wordmark plus four of the six rows it was describing.
  `cardPosition` now falls through to BESIDE the box. (2) The ring is the anchor's box plus padding,
  and the inbox queue column is full-bleed, so its right-hand stroke landed off-screen: a
  three-sided ring reading as an unfinished box. `ringFor` clamps to the viewport, which also keeps
  the four dim bands non-negative.
- [2026-08-16][OU-10] **Focus, and the one honest limit.** The card is a `role="dialog"`
  `aria-modal="true"` container with `useFocusTrap`, and it re-takes focus on `[index, anchorEl]`
  rather than on `index` alone — because the host navigates when the stop changes, so the new
  surface mounts afterwards and `SettingsHome` autofocuses its search. Verified live: on the
  settings stop the dialog held focus with the search field mounted. On exit, focus returns to the
  launching control when that control survived (measured: back on Discover's "Start the tour" with
  `outline: rgb(255,107,91) solid 2px`). When the tour navigated AWAY from the launch surface the
  trigger is detached, `useFocusTrap` correctly declines to focus a dead node, and focus lands at
  the document start — which is exactly what every other route change in this shell does. Recorded
  as a limit rather than papered over with focus management the rest of the app does not have.
- [2026-08-16][OU-10] **V5 (driven as a user, real gateway from this worktree, isolated home).**
  `PERSONALCLAW_HOME=/private/tmp/ou10-home`, `AUTH_MODE=none` (loopback only), port `:10066`, its
  own `static/dist` symlink so the served SPA was this build. Legs driven:
  · **done-screen launch → tour on a working shell — VERIFIED.** Fresh home, full flow
    (name → "Set up later" → "Skip this") → "Take the quick tour" → landed on `#/dashboard` with
    the rail rendered AND the tour on step 1, named
    `PersonalClaw tour — step 1 of 5: The sidebar is the whole app`.
  · **all five stops — VERIFIED, screenshot each.** rail (`#/discover`, no navigation) → chat
    (`#/chat`, composer ringed + halo) → inbox (`#/inbox`) → approvals (`#/dashboard`, the
    "Needs you" band) → settings (`#/settings`, the search). Every stop resolved its anchor.
  · **Escape mid-tour → a working app — VERIFIED.** Escape on the settings stop closed the overlay,
    handed the route back to `#/discover` (where the tour was launched), and the rail still
    navigated: a click on Inbox rendered `#/inbox`.
  · **Discover replay — VERIFIED**, including that the card sits above the tips and that ending the
    tour from step 1 returns focus to its button.
  · **Zero console errors** across the whole run; the gateway log carried only the expected
    "no model provider resolves for use case 'background'" warnings (the essentials step was
    skipped, so this home has no provider).
  · **NOT driven:** reduced motion (verified by test only — no way to toggle the OS preference from
    this harness), mobile width, and the degraded stop (an anchor that never mounts), which is
    covered by unit test. The mobile nav drawer parks off-screen, so the rail stop would fall back
    to a centred card there; the tour is a desktop-shell feature this session did not widen.
- [2026-08-16][OU-10] **Falsified, four mutations, all red.** (1) Escape handler emptied → **5 RED**
  across three files, including the repo's own rail: `SpotlightTour.test.tsx` +
  `productTour.test.tsx` gave `AssertionError: expected <div role="dialog" …(8)>…(2)</div> to be
  null` (×4), and `escapeDismissContract.test.tsx` gave `AssertionError: A scrim is a MOUSE
  dismissal; without an Escape handler a keyboard user cannot close the overlay:`. (2) The halo's
  `!reduce &&` guard replaced with `true &&` → **2 RED** in
  `SpotlightTour.reducedMotion.test.tsx`: `expected <div data-tour-halo="true" …(2)></div> to be
  null` and `the static outline ring must still be drawn: expected 2 to be 1`; the motion-allowed
  half stayed green, so the pair is non-vacuous in both directions. (3) A
  `api.saveOnboardingState({step:'done'})` added to the step effect → **2 RED**:
  `expected [ 'saveOnboardingState', …(16) ] to deeply equal []` and
  `app/onboarding/ProductTour.tsx must not import the gateway client: expected true to be false`.
  (4) The inbox stop's route changed to the held-back `tools` surface → **2 RED**, the auto-pin one
  being `expected { Object (mode, pinned) } to deeply equal { mode: 'starter', pinned: [] }`.
- [2026-08-16][OU-10] **Gate.** `npm run typecheck --workspace web` clean;
  `npm test --workspace web` **303 files / 3128 tests passed** (the full suite, so the repo-wide
  design and a11y ratchets are included); `npm run build --workspace web` clean (92 components in
  `ui-docs.json`). No Python touched. `docs/design/consistency-audit.json` regenerates on every web
  run and was restored, not committed.
- [2026-08-16][OU-6] **DONE — but the atom's premise was wrong, and correcting it is the decision.**
  The task line reads "web/src/ui/EmptyState.tsx exists", which reads as a greenfield primitive. It is
  not one: `EmptyState` predates this atom, exported from the LIST KIT (`ui/ListScaffold.tsx`) beside
  `LoadError` and `ListSkeleton`, and already answers ~30 call sites across 15+ pages. Shipping a second
  component at `ui/EmptyState.tsx` would have been a **dual path** (two components, one condition), and
  it would have contradicted an existing rail that pins the co-location deliberately —
  `loadErrorState.test.tsx` › "the primitive is exported from the list kit, beside EmptyState":
  *"Co-located on purpose: the two are alternative answers to the same condition, and a surface reaching
  for one should see the other."* **Ruling: keep one primitive, where the repo already put it.** The
  filename clause is recorded as a DEVIATION in `atomic/OU.md`; the PRODUCT clause is what shipped.
  Rejected alternatives: (a) extract `EmptyState` into its own file and migrate ~30 call sites — a pure
  churn diff that breaks the co-location rail to satisfy a path string; (b) fold `PresetEmptyState` into
  `EmptyState` — they answer *different* conditions and each says so in its doc ("Distinct from
  EmptyState (ListScaffold), which states a fact and offers one CTA"), so merging them would lose the
  preset-first on-ramp, not remove a duplicate.
- [2026-08-16][OU-6] **The rollout gap was TWO surfaces, not seven.** Audited all seven before touching
  anything. Already routed through a shared primitive with an action: Knowledge ("Add knowledge"),
  Skills ("Browse skills"), Tasks ("New task"), Triggers (`PresetEmptyState`, whose cards *are* the
  actions). Real gaps: **Memory** — not a page at all but `#/settings/memory`, a multi-tab panel, with
  **zero** empty-state adoption (four hand-rolled centered `<p>`s, no action); and **Workflows** — using
  the primitive but with **no action on either** empty state. Fixed both. Memory's audit log also
  conflated two facts in one sentence ("No matching events." was shown to a user whose memory had
  recorded nothing), now split into "Nothing recorded yet" vs "No matching events".
- [2026-08-16][OU-6] 🔴 **DISCOVERY — `#/loops` told a load failure it was an empty list.** Found while
  auditing the seven, and it is the honesty precondition for shipping an empty state at all: an empty
  state that renders when the data merely FAILED to load is a confident wrong answer, not an empty
  state. `LoopsListPage.tsx:63` carried `.catch(() => [] as GoalLoop[])` inside its `useCachedData`
  fetcher — the harsher variant, so `error` was permanently null and no caller *could* have read it. A
  failed `GET /api/loops` therefore rendered "No loops yet — Describe a task and let an agent classify,
  plan, and pursue it autonomously" plus the Start-a-loop CTA. Fixed both halves (drop the swallow, test
  the error branch first on the same `loops === undefined` condition — dropping the swallow alone would
  have pinned the page on its skeleton forever). It joined the existing family rail's ADOPTERS list
  rather than growing a second rail. **Driven:** `page.route` 500 on `/api/loops` with a cold load →
  `role="alert"` "Couldn't load your loops | HTTP 500 | Retry", `saysNoLoopsYet: false`,
  `offersStartALoop: false`; un-route + click Retry → alert clears and the honest "No loops yet" + CTA
  returns.
- [2026-08-16][OU-6] **Two craft calls, both recorded in code.** (1) Memory's entity-graph and
  daily-digest empty states take NO action, because the control each would point at ("Rebuild links",
  "Build / refresh") is rendered a few lines above and visible at the same time — a second button with
  the same accessible name in the same region hands assistive tech two indistinguishable controls, so
  the hint NAMES the adjacent control instead of cloning it. (2) Every action is on the
  genuinely-empty branch only; a narrowed-to-nothing list gets the fact and no CTA, per the canonical
  shape `emptyStateNoMatch.test.tsx` already documents. Loops' facet no-match branch is the one
  exception that *gains* an action, and deliberately: "View all loops" resets the filter rather than
  creating anything, and the branch is reachable only when loops exist.
- [2026-08-16][OU-6] **Falsified, three mutations, all red, all restored.** (1) Re-introduced Loops'
  `.catch(() => [] as GoalLoop[])` → **2 RED** in `loadErrorState.test.tsx`:
  `pages/loops/LoopsListPage.tsx swallows a fetch rejection: expected [ 'loops:62' ] to deeply equal []`
  and `a swallow here makes every other consumer of the key unable to see the failure: expected [ Array(1) ] to deeply equal []`.
  (2) Stripped both Workflows actions → **1 RED** in `emptyStateRollout.test.tsx`:
  `the empty state on Workflows must offer a next step, not just state a fact: expected 0 to be greater than 0`.
  (3) Created `web/src/ui/EmptyState.tsx` (the dual path this atom could most easily have minted) →
  **1 RED**: `EmptyState lives in the list kit (ui/ListScaffold.tsx); a second file beside it would be
  two components for one condition: expected true to be false`. Each mutation was asserted to have
  applied before the run, so none of the three could read as a green by silently failing to match.
- [2026-08-16][OU-6] **Two rail bugs found by the rails rejecting CORRECT code — fixed the rail, not the
  surface.** (1) The action matcher was `action={{ … onClick`, i.e. the object-literal shape only, and it
  failed `#/skills`, whose `action={!q ? { … } : undefined}` is the *conditional* shape and the better
  one (it encodes "no CTA while filtered" in the prop itself). Replaced with a brace-matcher over the
  whole `action={…}` expression, which admits both and cannot be padded. (2) A first draft asserted a
  `focus-visible:ring-*` utility on the CTA and failed — correctly: this app's keyboard ring is a GLOBAL
  rule in `design/tokens.css` (`:focus-visible { outline: 2px solid var(--color-primary) }`), so no
  control carries one, jsdom paints nothing, and the observable property is a control opting OUT with
  `outline-none` and not replacing it. Both are the "when a rail rejects a new adopter, check the rail
  before the adopter" lesson that file already teaches.
- [2026-08-16][OU-6] **One pre-existing census floor moved, in the intended direction.**
  `loadingNounPairing.test.ts` went red: `skeletons gated on the state a results noun counts: expected 4
  to be greater than or equal to 5`. Causal, not incidental — `resPaired` is defined as
  `!errNoun && resultsNoun`, so every surface that adopts `LoadError` moves a skeleton out of that
  bucket and into `errPaired`. Loops did exactly that. Measured: all=64, err=34 (was 33), res=4 (was 5).
  Lowered the floor to 4 with the reason in place; it is a vacuity guard, not a target, and the shape it
  guards is the one the LoadError rollout is deliberately draining.
- [2026-08-16][OU-6] **Validated as a user** on an isolated home (`/private/tmp/ou6-home`, port 10088,
  fresh → Skip setup). Rendered with genuinely empty state: `#/loops/history` "No loops yet" + Start a
  loop · `#/workflows` "No workflow runs yet" + Browse definitions (clicked → `?tab=defs`, the working
  action) · `#/knowledge` "Knowledge base is empty" + Add knowledge · `#/tasks` "No tasks" + New task ·
  `#/settings/memory?tab=audit` "Nothing recorded yet" (the new split) · memory studio narrowed to
  `Facts 0` → "No matching memories". Loops' facet branch driven by serving one terminal loop:
  h2 "No active loops right now", hint "You have 1 loop — just none in this view.", escape "View all
  loops" (clicked → `?filter=all`, row appears) and **no** create CTA. Both themes via the app's own
  toggle: dark `body #0f0f0f / h2 rgb(227,227,227) / CTA rgb(255,107,91)` → light
  `body rgb(240,244,248) / h2 rgb(31,31,31) / CTA rgb(200,69,46)`, all tokens flipping. The one layout
  risk I flagged (a page-scale primitive in Memory's 19rem explorer) measured clean: 290px wide in a
  302px pane, 163px tall in 485px, no overflow, no clipping. **Not driven:** Skills and Triggers —
  their genuinely-empty branches are unreachable on a fresh home (native skills and
  `system:notification-digest` ship pre-installed/enabled); both were already correct and are untouched.
- [2026-08-16][OU-6] ⚠️ **Probe trap worth recording.** Three separate "the fix does not work" readings
  were all harness artifacts, never code. `useCachedData` caches at module scope, so a hash-only
  navigation (`location.hash = …`, and `page.goto` to the same document with a different hash) does
  **not** reload and the stale `[]` keeps satisfying `loops.length === 0`. Worse, killing the gateway and
  reloading to force a cold failure served **no assets at all** (`ERR_FAILED` on every chunk — the SW did
  not cover the navigation), leaving an unmounted app that reads exactly like a clean empty state. Only
  `page.route` interception installed BEFORE boot, plus a real `page.reload()`, plus a mounted-ness
  floor (`main.querySelectorAll('*').length`) produced a trustworthy measurement. Add the mount floor to
  any empty-state probe: an empty page and an unmounted page are indistinguishable by text alone.
- [2026-08-25][OU-11] **PARTIAL (S4 T4.1 + the dry-run only). Atom stays `todo`.** New
  [`docs/maintainers/usability-kit.md`](../../maintainers/usability-kit.md) — self-contained in the
  literal sense the done_when asks for: a facilitator who has never opened this repo can run a
  session from that one file, and it says so and then earns it. Eight sections: what you are running,
  before-the-session prerequisites, the consent note (read verbatim), the facilitator script, the
  observation sheet, after-the-session triage, the dry-run record, and the kit's own limits. Four
  parts against the done_when's list — **facilitator script** (four tasks written as goals, plus a
  fixed may-say list and a never-say list, so the script cannot lead the witness), **consent note**
  (what is recorded, what is not, stop at any time, where the notes go, plus a throwaway-credential
  request — no invented retention policy, because the project has none), **observation sheet**
  (hesitation log / expected-vs-happened / their exact words / dead ends, an eleven-row vocabulary
  table, an unblock log, five closing questions), and the **dry-run on self**, recorded below.
- [2026-08-25][OU-11] **The S1 baseline exists but is not a human baseline — the V4 clause cannot be
  satisfied by comparison against it.** The only recorded figure is in this log's OU-4 entry of
  2026-08-16: **9.3 s** of *driven* (scripted-browser) interaction for name → essentials → three
  try-one cards → done, and that same entry marks the leg **PARTIAL** because "the provider segment
  (install → key → Test → bind) was NOT exercised: it needs a real credential." OU-3's entry of the
  same date adds per-card click→outcome latencies (0.94–1.38 s) against a 2 min budget. So the
  number is a machine floor for the click path with the most expensive human segment excluded —
  comparing a stranger's wall clock against 9.3 s would manufacture a two-orders-of-magnitude
  "regression" with no defect behind it. **Ruling recorded in the kit §6:** session 1 *establishes*
  the human baseline; the delta V4 asks for is measurable only from the post-fix re-run onward. If
  the owner wants a delta against S1 specifically, the missing ingredient is a *human* S1 run, not a
  new instrument.
- [2026-08-25][OU-11] **Dry-run on self, driven for real (the deliverable, not a formality).** Fresh
  isolated home (`PERSONALCLAW_HOME=$PWD/.dev-home-ou11`), `AUTH_MODE=none` (loopback-only), gateway
  on `:10411` from this worktree, SPA built from this tree and asserted serving before anything was
  observed (2.6 MB bundle, HTTP 200) — a missing `dist` would have made every observation vacuous.
  Isolated browser context, per OU-4's stale-cache warning. Gateway killed by PID. Backend confirmed
  `first_success: {knowledge:true, trigger:true, loop:true}`. **Zero console errors, zero warnings.**
  **The dry-run's job was to find the kit's bugs before a stranger did, and it found four:**
  · **Task 4 named a label the product does not use.** T4.1 specifies the task as "tell us what
    *Loops* means from the UI alone." Measured: "Loops" appears in **neither** the starter rail
    (`Home · Chat · Inbox · Store · Everything+13 · Settings`) **nor** the expanded 18-item rail, and
    `#/projects` — the only rail item that mentions loops at all, and only in its a11y description
    ("Projects, 1 active loop") — renders the substring "loop" **zero** times. The task as written
    would have sent a participant hunting for a word that does not exist and a facilitator would
    have logged a finding the kit invented. Rewritten as a three-step probe that takes the
    participant's word first and reveals ours last.
  · **Task 3 had an unstated, hard prerequisite.** Approving a tool call needs a bound model. With no
    provider the dashboard reports "11 degraded — 11 surface(s) running without a model" and zero
    approvals are reachable, so task 3 does not run at all. Now a decision in the kit's §2 with three
    named options, one of which explicitly records task 3 as not run.
  · **The kit assumed a stranger can satisfy the required provider step.** Often they cannot: the
    four cards shown by default (Alibaba Model Studio, Amazon Bedrock, Anthropic,
    Anthropic-Compatible) all need a paid key or an AWS account, and the keyless option (Ollama) is
    **12th of 16**, behind "Show all 16 model provider apps". Now the session's headline logistical
    risk.
  · **My own timing harness produced a number I threw away.** Card 1 measured 29 s because the
    polling loop's deadline dominated the result; re-measured tightly, cards 2 and 3 were **112 ms**
    and **235 ms** click→outcome. Hence the kit's instruction to split hands-on from machine time and
    to trust the participant's declaration over any instrument. Wall clock for the whole dry-run was
    **4m 04s**, essentially all of it operator deliberation — a lower bound on the operator's path,
    not a target, and stated as such.
- [2026-08-25][OU-11] **DISCOVERY (three product defects, reported not fixed — each is a different
  atom's work).** (1) The **"Set a reminder" try-one card contradicts itself in one view**: "Cadence:
  At 09:00 AM" beside "Next time: 8/26/2026, 2:00:00 AM". `GET /api/triggers` shows
  `schedule:clock:daily-check-in` with `cron_expr "0 9 * * *"`, `timezone: null`, `next_run_ts` =
  `2026-08-26T09:00:00Z` = 02:00 local (PDT). The cron is evaluated in UTC while the label reads as a
  local wall-clock time, so the first reminder a new user creates fires at their UTC offset — and the
  card shows them the discrepancy. `schedule:system:notification-digest` has the identical shape
  (`0 8 * * *`, `timezone: null`). (2) The **Projects rail item advertises "1 active loop" but
  `#/projects` lists no loops** — only the two built-in projects (Personal, Repeatable, "0 lists");
  the running loop is reachable only from the dashboard, and opening it lands on **`#/chat/loop-…`**,
  a chat session. One concept, four namings across four surfaces. (3) The try-one step opens with
  **two consecutive sentences that both say "runs for real"** — redundant copy in the flow's
  highest-attention position.
- [2026-08-25][OU-11] **OWNER-GATED — the clauses this session did NOT satisfy, named precisely.**
  (a) *"3 sessions run (OWNER recruits/hosts strangers)"* — not run; recruiting and hosting three
  people who have never seen the product is Owner task 1 and cannot be simulated. No results section
  was written, deliberately: an invented findings table would be worse than an empty one.
  (b) *"fix-now list (<=1 day) empty by session close"* — vacuous until (a); there is no session
  close. The kit records the budget as ≤1 day **across all three sessions**, not per session, because
  the clause reads the other way at a glance.
  (c) *"issues filed labeled `ux-finding`"* — none filed (agents do not file on the owner's behalf).
  The label itself may not exist: `.github/` carries `ISSUE_TEMPLATE/{bug,feature}.yml` and no
  tracked label definition, so labels live in the GitHub UI. **What I would file, verbatim, if
  authorized:** (i) *"Onboarding: the required model-provider step has no keyless option in view"* —
  4 default cards all need a paid key or AWS; Ollama is 12th of 16 behind "Show all 16". (ii) *"A
  reminder created in onboarding fires at the user's UTC offset, and the card says both times"* —
  the timezone defect above; this one is arguably a plain bug, not a `ux-finding`. (iii) *"The rail
  badges Projects with '1 active loop'; the Projects page shows none, and the loop opens as a chat"*
  — the naming/IA split. (iv) *"'Loops' is a product concept with no navigation label"* — the finding
  that came out of fixing the kit's own task 4. (v) *"Try-one step: two adjacent sentences both say
  'runs for real'"* — copy, fix-now sized.
  (d) *"first-success timing delta vs S1 baseline recorded"* — no delta, and the reason is the
  baseline ruling above, not a missed measurement.
- [2026-08-25][OU-11] **Falsified (prose has no unit test, so the checkable claim is that every path
  the kit cites resolves).** Repointed the kit's `../guides/getting-started.md` link at
  `../guides/getting-started-FALSIFY.md`, confirmed the mutation with plain `grep` (an untracked
  working-tree edit is invisible to `git grep`) and confirmed the target absent. `gate_report.py` →
  **docs-lint FAIL, 1 failure**, naming it exactly:
  `docs/maintainers/usability-kit.md: docs-lint findings rose 0 -> 1; new finding(s):
  ['dead_link:docs/guides/getting-started-FALSIFY.md']`. Restored from a pristine copy at a literal
  path, verified byte-identical by `shasum -a 256` (`42f6e5b1…be32` both sides) — **the same
  invocation then returned all 6 gates PASS** and `git status --porcelain` was empty, so the restore
  was byte-identical rather than merely re-green. Both directions, one gate. Note for later citations
  here: `find_dead_links` runs on text with inline code spans blanked, so a **backticked** path is
  invisible to that gate while a markdown link is enforced — the kit uses markdown links for exactly
  this reason.
