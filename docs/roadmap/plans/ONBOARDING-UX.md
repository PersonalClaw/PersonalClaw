# Plan: Onboarding UX — Guided First Run + Progressive Disclosure

**Status:** DESIGNED — deepened 2026-07-18 with code recon (initial PROPOSED 2026-07-18; owner: "onboarding UX that guides user through the app, features, configuration of their first agent provider or model provider, and gets them started")
**Created:** 2026-07-18
**Wave:** 1 (S1-2) + 2 (S3-4)
**Depends on:** nothing hard. Coordinates with DISCOVERABILITY-LAUNCH (demo capture reuses the tour click-path), DISTRIBUTION (the install path determines what the first screen may promise), AMBIENT-SURFACES (Home tiles are the tour terminus), INBOX-NOTIFICATIONS-UNIFICATION (badge semantics the nav shows).
**Scope:** guided first run to a first success in under 5 minutes, progressive disclosure over the 20+-surface nav, approval-brief polish, and stranger validation. **Soul guardrail:** guidance, never gates — every step skippable, the full sidebar one toggle away, zero features locked behind the tour, and **no tour telemetry** (zero-telemetry stance; learn from usability sessions, not tracking). Build on the existing machinery — `Onboarding.tsx` + `onboarding/StepStack.tsx`, `NavRail.tsx`, `ApprovalCard.tsx`, `PlanningWalkthrough.tsx` — extend, don't reinvent (protocol rule: nearest-analog style).

---

## Context (code recon, 2026-07-18)

Onboarding exists as `web/src/app/Onboarding.tsx` + `onboarding/StepStack.tsx` (step-stack machinery) + `identity.tsx`, backed by `GET /api/onboarding` first-run state — today it covers name capture and points at provider setup (per getting-started: "asks for your name and walks you to provider setup"). The nav is `ui/NavRail.tsx`; shell primitives (TopBar/ListScaffold/SidePanel/HeaderActions) and `CommandPalette.tsx` exist; `useApprovalToasts.ts` + `pages/chat/ApprovalCard.tsx` are the approval surfaces; `PlanningWalkthrough.tsx` is an existing walkthrough-pattern precedent. CLI has `personalclaw setup` (wizard) and `doctor` (verification). Gap: no in-flow provider install/bind (a Settings triple-hop: Apps→install, Providers→key, Models→bind), no first-success moment, no disclosure model over the nav, empty states vary in helpfulness.

## Design

- **Guided first run (StepStack extension):** name (exists) → **provider step in-flow**: curated provider-app cards (Anthropic/OpenAI/OpenAI-compatible/Ollama/Bedrock — driven by the Store catalog, not hardcoded vendors: the list is "model-provider-typed apps," rendered from the catalog so core stays vendor-blind) → inline install (existing Store install API) → key entry + Test (existing provider settings API) → chat binding (existing bindings API) → **first-success step**: seeded "try one" cards (summarize a URL into Knowledge then ask about it; set a reminder trigger; run a small goal loop) that execute for real → done screen (points at Inbox, the bounciness slider, and "unlock everything" toggle). Skippable at every step; resume state in the existing onboarding state endpoint (extend, additive).
- **Progressive disclosure (NavRail):** two rail sections — **Starter** (Chat, Inbox, Apps, Settings) and **Everything** (collapsed group, one click to expand permanently). A surface auto-pins from Everything→visible when first visited (deep links + CommandPalette always work and auto-pin — URL doctrine untouched). Pin state = frontend-persisted user pref (existing appearance/prefs storage pattern); an "expert mode" toggle (Settings → Appearance) shows all permanently. Default for **existing** installs: expert mode ON (no rug-pull); fresh installs: starter mode.
- **Empty states as on-ramps:** shared `EmptyState` primitive (icon, one-paragraph concept, one seeded action button) applied to the major pages (Loops, Workflows, Knowledge, Memory, Skills, Tasks, Triggers) — copy voice per PRODUCT.md ("companion, not console").
- **Approval brief (ApprovalCard):** decision-ready layout — what (tool + args summary), why (one line of plan/goal context when the runner provides it), blast radius chips (writes files / network / shell / reads-only — derived from tool metadata + command screening classification), and scoped remember-this-choice (this session / always for this tool / no). Same content model flows to channel `request_approval` renderers (plan 40 apps consume the structured brief). **Copy-sensitive surface:** wording changes reviewed against the security-docs voice; the brief must never *advocate* approval.
- **Stranger validation:** 3 sessions, think-aloud, fresh install → first success; facilitator script + consent note; findings triaged fix-now (in-session budget) vs issue-filed.

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

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

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

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
