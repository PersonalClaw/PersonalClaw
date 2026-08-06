# PersonalClaw Roadmap

Active and planned feature work. Each entry links to a detailed plan document in `plans/`.

**Last updated:** 2026-08-05 · **rev 16** — 69 plans / 6 pillars / ~360 sessions.

## Proposing roadmap changes

This roadmap is **maintainer-owned** — which is a statement about process, not about
openness. Plans encode sequencing decisions and cross-plan contracts (see
[INTEGRATION-ARCHITECTURE](plans/INTEGRATION-ARCHITECTURE.md)), so a plan edited in
isolation tends to break a seam another plan depends on. One person holding the
dependency graph is what keeps that coherent.

**So: propose, don't PR.** Open a thread in
[Discussions → Ideas](https://github.com/PersonalClaw/PersonalClaw/discussions/categories/ideas)
describing the capability and why it matters. The maintainer files or amends the plan,
and you are credited in it. **Pull requests against `docs/roadmap/` are declined on
principle** — not because the idea is unwelcome, but because the plan is the wrong
artifact for a newcomer to write cold.

What *is* directly contributable: implementing an existing plan task (read
[EXECUTION-PROTOCOL](plans/EXECUTION-PROTOCOL.md) first — it is mandatory for roadmap
work), fixing a
[good-first-issue](https://github.com/PersonalClaw/PersonalClaw/issues?q=is%3Aopen+label%3Agood-first-issue),
or reporting that a plan's stated premise no longer matches the code (that is an E1
escalation and genuinely valuable — several plans have been re-scoped because of one).

- **rev 16** — **product/UX** pass (2026-08-05). Companion to rev 15's hardening pass. **Two new plans: 68 Product-Experience-Parity** (Pillar F) and **69 Desktop-Computer-Use** (Pillar B). Plan 68 is a triage-and-route plan: (§1) **preset-first empty states** — a preset-first schedule surface opens on four prefilled preset cards + a mode-segmented form that hides cron unless chosen, where our `TriggerCreatePage` front-loads the full ontology (~15 events, 7 dormant); a reusable `PresetEmptyState` primitive applied to Triggers/Schedule → Workflows → Tasks → Knowledge, **presets seed the unchanged expert form so power is never removed**. (§2) **App Store right rail** — a persistent `CategoryRail` (categories+counts and sources+provenance always visible) vs our dropdown `FilterMenu`+popover; move both into an always-open-on-wide right rail + art-forward card polish (renders APP-PLATFORM-EVOLUTION's `quality` badges, doesn't duplicate them). (§3) **Onboarding import** — there is no onboarding-import path today; ONBOARDING-UX has no import row → new scanner framework, Claude Code + Codex first, consent+review+redaction, secrets counted-and-skipped. (§4) **Artifact folders** — an opaque-`folder_id`-never-a-path model (rename-safe), we have none. (§5) **Artifact LOCAL deploy** — owner delta from a BYO-AWS `deploy/` model: a `webapp` artifact served through our own gateway route with strict CSP fencing (app-backend hosting + sandboxed-iframe seams already exist); public exposure deferred to EXTERNAL-ACCESS, **not** a cloud provisioner. (§6) **Artifacts as a knowledge source** — port `knowledge/artifact_ingest.py`'s exact mechanism: one aggregate `artifact://` source row, in-process change-listener (no polling), backfill tied to row-creation, one `FileReader` path; searchable but **not listed as knowledge items**. (§7) **First-party app suite** — ~11 comparable apps vs our ~4; a phased program (Code-Review → Research-Lab → Design-Critique → Docs/Slides → Notes → Issue-Radar → Spec-Builder → Ops → Companion), one PR each, these ARE the ECOSYSTEM-TOOLING exemplars, reusing DOCUMENT-HANDLING-TOOLS/WORKFLOWS-V2/minutes rather than rebuilding. (§8) **steering assessed as ~90% already covered** — `always: true` skills + `project_context.py` ARE our always-on layer; add only a legibility *viewer*, not a parallel concept; plus the missing domain-craft skills. **Re-homed, not duplicated:** channels → CHANNEL-EXPANSION amendment (a shared `TurnDriver` centralizes redaction+approval once; Teams/Webex/WeCom/WeChat are cheap on it; a 21k-LOC Slack app's enterprise/interaction/retry modules are a hardening read); distribution → DISTRIBUTION S5 follow-on (signed channel feed — reconciles with rev 15 §7.2's channel deferral; we already have OIDC provenance). **Plan 69 Desktop-Computer-Use** is genuinely new capability — BROWSE-AUTOMATION is browser-only (grep-verified, zero desktop-automation hits across 67 plans); adopts an accessibility-tree / element-index `AXPress` approach (safer + more reliable than screenshot-and-coordinate; pointer never moves by accident) with the keystone-out-of-band-enable safety floor, hard-gated on AUTONOMY-GUARDRAILS + the rev-15 ceilinged-spawn.
- **rev 15** — hardening pass (2026-08-04). **One new plan: 67 Platform-Hardening-Floors** (Pillar B) — deliberately a *re-homing* plan: nine of twelve studied items already had owners, so it corrects and completes them rather than duplicating. **Two premise corrections it exists to record:** (a) EXECUTION-ISOLATION's `EI-A1` specifies `preexec_fn` for resource ceilings and justifies it as "fork-safe because every seam spawns from the single-threaded-at-fork asyncio path" — **false**: core has 67 thread-creation sites, `apps/backend_runtime.py:288` respawns backends from a daemon thread, and `action_providers/bash_provider.py:216` spawns on the event loop thread, so executing that row as written would introduce a gateway-wedge hazard (a child wedged pre-`exec` blocks `Popen._execute_child` in an uninterruptible `os.read` on the loop thread and keeps duplicates of `gateway.lock` + the listening socket); the fix is post-`exec` delivery via a shim plus a four-profile split (`tool`/`session_host`/`build`/`none` — `session_host` exists because a uniform NOFILE cap broke ACP sessions) and a `preexec_fn` AST tripwire. (b) The app-platform trust boundary is **one-directional**: the outbound half is real (`app-platform.md:76` strips the owner credential, injects an app-scoped token), but inbound is unauthenticated — `apps/backend_runtime.py:251` binds loopback with no signature check, so any local process bypasses the proxy, session auth, and `app_permission_middleware`. Also newly owned: a generated+committed `config-baseline.json` (drift, which `test_config_roundtrip.py` cannot see) and an `inert-surface-baseline.json` shrink-only counter aimed at the recurring declared-but-unread defect class; an offline **fake-model E2E harness** that is precisely the "seeded authenticated per-route CI harness" DESIGN-SYSTEM-CONSISTENCY recorded as its deferred a11y tail; a `docs-lint` gate; and the two long-standing xdist flakes' **root causes** (the subagent one is a security-audit gap — a swallowed `except` around a SEL write). Design input re-homed to AUTONOMY-GUARDRAILS `S5.2`: `SafetyProfile` (already recorded there as having zero non-test callers) gains the two-level **ceiling ∩ profile** model with archetype dispatch, plus their path-matcher rule lifted verbatim as a test (never `normpath` a *pattern* — `/a/**/../b` → `/a/b` silently drops the `**` and widens an allow). **Excluded by ruling, recorded so they are not mistaken for unbuilt ideas:** a default-on telemetry beacon (the zero-tracking posture is the better promise), a cloud launcher, a gateway-federation hub (already a permanent veto), and i18n catalogs. Telegram needs **no plan change** — CHANNEL-EXPANSION `S2-3` already specs it correctly; the seven-channel breadth is a sequencing argument only. Release channels **deferred, not dropped** (we already have OIDC build-provenance, stronger than a `SHA256SUMS` file; the gap is only the nightly-channel concept).
- **rev 14** — capability gap analysis (researched 2026-07-28/29: four research tracks + two owner-supplied deep-research reports + a code-verified capability audit), owner-triaged item by item. **Four new plans:** **63 Prompt-Cache-Substrate** (Pillar D — verified finding: `model_pricing.json` prices `cache_read`/`cache_write` for 26 models and `LLMEvent` accumulates both, but **zero `cache_control` markers exist in core or any of 40 apps**; one provider-agnostic middleware seam + the one deliberate prefix-stability fix — `context.py:773`'s minute-precision `[CURRENT DATE]` sits at position 2 of every assembled prefix, the canonical cache killer), **64 Cost-and-Token-Observability** (Pillar D — `Stats.get_cost_usd()` has zero consumers, `Stats` is in-memory and lost on restart, `SystemAgentStats.input_tokens` is typed in the FE and rendered by nothing, and subagents discard the completion event's numbers at `subagent.py:1684`; a durable per-turn ledger + three surfaces, observation-only with `SpendMeter` keeping all enforcement), **65 Document-Handling-Tools** (Pillar D — the most visible capability gap: `python-docx`/`openpyxl`/`python-pptx`/`pdfplumber` are all already core deps used **read-only**; one writer seam + docx/xlsx/pptx/pdf/csv generation into the existing artifact store), **66 Email-Inbox-and-Triggers** (Pillar D — no mail anywhere and data-event triggers are memory-only with `vector_memory` as sole emitter; generalize the event vocabulary, add a mail inbox-provider app, then a prompt-bound-address mechanism, allowlist-first and fail-closed). **Seven dated amendments** (`## Amendment (2026-07-29 …)` blocks): BROWSE-AUTOMATION (the local-browser advantage — an extension approach reaches where our gateway already runs; a second execution target behind per-task grants, with the Amazon-v-Perplexity framing constraint written in), WORKFLOWS-V2-WORK-CONTAINERS (**owner-commissioned topology evidence review** — homogeneous-by-default fan-out, heterogeneity by MODEL not persona, writes single-threaded, no item-count threshold, plus seven blocking audit findings incl. the parent-session injection wall that already bites at 8), EXECUTION-ISOLATION (three app-side confinement compounders + a docs-only correction to land first), WORKFLOWS-V2-LEARNING-FLYWHEEL (skill resource tier — we already have 2 of 3 disclosure levels; agentic/retroactive skill authoring; approval-gated project-context write-back), CHAT-CRAFT (Branch — reopening S1's explicit non-goal deliberately, over the shipped `chat_fork.py`; plus the plan gate promoted from loop-only to chat), MCP-READONLY-INBOUND (already stateless by design and thus safe from the `2026-07-28` stateless-spec change, but `PROTOCOL_VERSION` is three revisions stale), KNOWLEDGE-LIBRARY (indexing depth — **no chunking**, one vector per item over `content[:1000]`, and a brute-force full-table cosine scan under otherwise-strong RRF+graph+cliff-cut machinery). **Owner rulings recorded:** replay links, persistent always-on compute, distribute-into-peer-products, the two skill-safety mechanisms, and per-vendor model residency all DISCARDED as not fitting the self-hosted personal model; sharing/publishing deferred far-horizon behind a user-declared exposed domain; the two audit bugs filed as issues #94/#95 rather than planned. Owner corrections to the input research were recorded at analysis time (acquisition-status, benchmark-provenance, and fork-lineage caveats) and are not load-bearing on the plans.
- **rev 13** — gap-analysis round 2 (owner Q&A + five approved mechanism designs). Five new plans: **58 Feedback-Signal** (Pillar C — 👍/👎 on AI judgments; 👍 silent-positive-only; capture store + per-producer accuracy + deterministic retire-thresholds; interpretive arm forward-hooked to Learning-Flywheel), **59 Model-Use-Cases-V2** (Pillar D — chat sub-vocabulary grows to background/orchestration/loops + code_tools/reasoning finally exposed; ordered per-use-case fallback chains, breaker-aware; composer override chains on top: override → chain default → fallbacks), **60 Investigate-Anywhere** (Pillar F — one fenced chat-with-context primitive + ~13-surface adoption sweep), **61 Artifacts-Evolution** (Pillar F — artifacts split from Files into a first-class library; store already first-class per recon — surface/dedup/iterate/diffs are the real gaps), **62 Agent-Rooms** (Pillar A — PROPOSED stub, owner-deferred until WORKFLOWS-V2 slices + ACP-AGENT-PARITY; council workflow template named as the cheap precursor). Ten round-2 amendments (`## Amendment (2026-07-26 — gap analysis round 2 …)` blocks): AUTONOMY-GUARDRAILS (earned-autonomy rung ladder per action type, ~4→~6), BROWSE-AUTOMATION (a11y-outline compression + live mirror + auth handoff + scheduled actuator, ~4→~5), EVALUATION-SUBSTRATE (lab/gate/field three-loop structure, ~5→~6), AMBIENT-SURFACES (dashboard-as-views w/ Overview + Mission Control presets-first + modern agent-world seam, ~6→~8), INBOX-NOTIFICATIONS-UNIFICATION (typed Proposal payload + apply contract + app emission, 6→7), ONBOARDING-UX (three-surface split; onboarding walks essential first-party apps FIRST, 4→5), COMPANION-APPS (multi-gateway pairing/switching = the sanctioned multi-instance story; hub explicitly vetoed), CHANNEL-EXPANSION (vendor-completeness pattern, Slack exemplar), AUTOMATION-SUBSTRATE (app-contributed trigger sources committed), CHAT-CRAFT (optimizer polish row). Owner rulings recorded: no multi-instance hub ever (remote access = Remote-User-Auth; separation = Companion-Apps switching); presets-first on a composition-ready view registry; 👍 silent-positive-only; composer model override chains rather than failing hard. **Execution-order pass folded in:** the rev-12/13 batch entries in the Wave 2/3 lists carry an explicit in-wave pickup order — contract-owners before consumers (Model-UC-v2 and Feedback-Signal first; Investigate-Anywhere before Artifacts S3; Inbox-Unify's proposals contract before Guardrails' promotion offers), ChatPage-heavy work serialized (Chat-Craft → Agent-Routing → Artifacts' chat touches) so no two plans churn ChatPage concurrently, and Personality-Themes after Fluid-Motion (it rides those dials).
- **rev 12** — platform gap analysis (~75 apps surveyed 2026-07-26), owner-greenlit batch. Three new plans: **55 Chat-Craft** (Pillar F — seven proven chat-surface mechanics: true rewind, queue interrupt-now, find-in-conversation, quote-reply, follow-up chips, screen-snip, smooth streaming), **56 Agent-Routing** (Pillar D — suggest-first specialist routing chip; deterministic+embedding classification, LLM never in the hot path; silent auto-routing deferred to a future earned-autonomy mechanism), **57 Personality-Themes** (Pillar F — themes that carry behavior: name/logo/sound/flourishes, strictly additive, a11y never weakened). Thirteen dated amendments (each plan carries an `## Amendment (2026-07-26 …)` block): WORKFLOWS-V2 (cached-rerun legibility + per-node inspection, 31→32), PLATFORM-RESILIENCE (`steer` mid-turn policy, 5→6), EXECUTION-ISOLATION (ceiling-everything + spawn-audit test, 7→8), AUTOMATION-SUBSTRATE (calendar-aware scheduling: quiet windows + duty gate + week grid, 9→10), INBOX-NOTIFICATIONS-UNIFICATION (second-opinion verify gate, 5→6), SECURITY-HARDENING (tamper-resistant baseline denylist), LEARNING-VISIBILITY (periodic identity report), MODEL-ROUTING-TELEMETRY (the usage story over the guardrails audit), EXTERNAL-ACCESS (OpenAI-compatible doorway promoted to an early sub-slice), AMBIENT-SURFACES (in-chat widget round-trip formalized — bridge already exists), APP-PLATFORM-EVOLUTION (app update badges, consented cross-app reads, Fix-with-AI), MOBILE-COMPANION (approve-from-phone reordered to milestone one), DURABILITY-AND-SYNC (merge-restore gaps closed — merge mode already exists).
- **rev 11** — two cross-cutting infrastructure plans added from the owner's remote-access ask: **53 Remote-User-Auth** (Pillar B — human login that mints the existing session token for internet-exposed self-hosting, on a durable-session foundation; the prerequisite for remote companion clients) and **54 Companion-Apps** (Pillar D — the connectivity contract native clients use to reach a local-or-remote gateway: discovery, unified pairing, endpoint switching, auth; MOBILE-COMPANION + DESKTOP-CAPABILITIES consume it). Note verified in the same pass: the desktop shell is **already Electron** — there is no Tauri anywhere, so no migration plan was created; the mic/audio gap is DESKTOP-CAPABILITIES S3 (unbuilt), not a re-platform. v0.1.2 shipped Design-System-Consistency, Platform-Legibility, Autonomy-Guardrails, and Platform-Resilience.
- **rev 10** — Pillar F "Product Depth & Craft" added (48 App-Platform-Evolution, 49 Knowledge-Library, 50 Session-Management, 51 Design-System-Consistency, 52 Fluid-Motion), from the owner's product-depth ask.
- **rev 9** — pre-launch alignment: 17 plans (31-47) added from the pre-launch investigation & owner review under new Pillar E (Launch, Reach & Ecosystem) + additions to B/C/D; PUBLICATION amended (repos under the `PersonalClaw` GitHub org, personalclaw.dev primary domain, force-push/SOURCE_REV retired → feature-branches-to-`main`); EXTERNAL-ACCESS §3 read-only MCP extracted to plan 41. Deepened plans carry Contracts & Interfaces + executor task tables; see the three companion docs below.
- **rev 4-8** (earlier) — ACP Agent Parity (4), TEAM-SHARED-ENTITIES (5), Multi-Tenant rescope (6), grok-build learnings folded into CONTEXT-ECONOMY/EXTERNAL-ACCESS/PLATFORM-RESILIENCE (7), HARNESS-CRAFT (8).

---

## Program Structure

62 plans across 6 conceptual pillars. Plans interleave via execution waves; the engine carries embedded acceptance criteria from downstream plans. The research corpus lives at `docs/research/learnings/` (14 source-agnostic topic files, ~320 mechanisms) — feed a topic file to any implementation session for mechanism-level grounding.

**Every implementation session executes under [plans/EXECUTION-PROTOCOL.md](plans/EXECUTION-PROTOCOL.md)** — the standing ground rules (scope discipline, definition of done, validation-as-a-user, deviation ledger, escalation triggers) that let plan tasks be delegated to any session, including smaller models, without eroding standards. Deepened plans carry executor-ready task tables (ID / task / files / done-when); a session that can't tell what "done" means for a task treats that as a defect in the task, not license to improvise.

**Three cross-cutting companion docs:**
- [plans/EXECUTION-PROTOCOL.md](plans/EXECUTION-PROTOCOL.md) — the standing ground rules every session runs under (above).
- [plans/INTEGRATION-ARCHITECTURE.md](plans/INTEGRATION-ARCHITECTURE.md) — **how the rev-9 plans fit together**: the build-order/data-flow map, the shared-seam inventory (each contract defined once, referenced everywhere), the mechanical conventions (config wiring, error envelopes, SEL events, storage, fail-open-vs-closed, sdk exports), the verified existing primitives, and the three "landmine" convergence points. Read this before any single plan. Each deepened plan carries a `Contracts & Interfaces` section (exact dataclasses, signatures, JSON schemas, wire contracts) + an `Integration points` list (calls / called-by / storage / gates) so a session — including a smaller model — never invents a shape another plan also touches.
- [plans/REV9-ALIGNMENT-AND-OWNER-TASKS.md](plans/REV9-ALIGNMENT-AND-OWNER-TASKS.md) — binds the original plans 1-30 to the protocol, annotates their rev-9 alignment deltas, and holds the single consolidated **owner-tasks index across all 47 plans** (accounts, spend decisions, credentials, hardware, validations you must drive, copy sign-offs).

**Alignment principles (rev 9, owner-set):**

1. **Clean architecture and implementation state outrank early feature availability.** Applied as: never pull a feature forward onto an unclean seam — the seam work lands first (e.g. sender trust before channels, the kind registry before digest, the lifecycle doctrine before any migration-bearing plan).
2. **The engine program keeps its position.** WORKFLOWS-V2 remains the Wave-1 architecture investment; the launch/reach plans (Pillar E) run in Waves 0-2 alongside it rather than displacing it.
3. **The clean-break doctrine graduates.** Post-publication, migration-bearing changes follow the LIFECYCLE-DOCTRINE lifecycle (gate → dual-path → migrate → cleanup); plan 31 lands first in Wave 0 because it shapes how every other plan's changes land.
4. **Zero telemetry is a feature.** No adoption instrumentation is added anywhere in this program; public signals only (stars, PyPI downloads, GitHub traffic).

---

## Plans by Pillar

### Pillar A — Execution Engine + Convergence

The v2 workflow engine and the systems it subsumes.

| # | Feature | Plan | Sessions | Wave |
|---|---|---|---|---|
| 1 | Workflows v2 — Composable Execution Platform | [WORKFLOWS-V2](plans/WORKFLOWS-V2.md) | ~32 | 1 |
| 2 | Loop Evolution — Loops as Workflow Templates | [LOOPS-EVOLUTION](plans/WORKFLOWS-V2-LOOPS-EVOLUTION.md) | ~5 | 2A |
| 3 | Universal Project Planning + Planner Collapse | [UNIVERSAL-PLANNING](plans/WORKFLOWS-V2-UNIVERSAL-PLANNING.md) | ~6 | 2A |
| 4 | Tasks & SOPs as Workflow Primitives | [TASKS-SOPS](plans/WORKFLOWS-V2-TASKS-SOPS.md) | ~7 | 2D |
| 5 | Knowledge Artifact Synthesis Nodes | [KNOWLEDGE-SYNTHESIS](plans/WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS.md) | ~6 | 2B |
| 6 | Work-Container Hierarchy — Project as Sole Umbrella | [WORK-CONTAINERS](plans/WORKFLOWS-V2-WORK-CONTAINERS.md) | ~9 | 2C |
| 7 | One Automation Substrate — Triggers Fire Workflows | [AUTOMATION-SUBSTRATE](plans/WORKFLOWS-V2-AUTOMATION-SUBSTRATE.md) | ~10 | 3 |
| 8 | Learning Flywheel — One Lifecycle for Learned Artifacts | [LEARNING-FLYWHEEL](plans/WORKFLOWS-V2-LEARNING-FLYWHEEL.md) | ~11 | 0+3 |
| 30 | Harness Craft — Fast Worktrees + Best-of-N + Check-Work | [HARNESS-CRAFT](plans/HARNESS-CRAFT.md) | ~3 | 2/3 |
| 62 | Agent Rooms — Persistent Multi-Agent Deliberation (PROPOSED, deferred) | [AGENT-ROOMS](plans/AGENT-ROOMS.md) | ~6 | 4 |

### Pillar B — Safety, Resilience & Operations

Cross-cutting floors everything else depends on.

| # | Feature | Plan | Sessions | Wave |
|---|---|---|---|---|
| 9 | Autonomy Guardrails — Budgets, Denylist, Kill Switch (+ Earned Autonomy) | [AUTONOMY-GUARDRAILS](plans/AUTONOMY-GUARDRAILS.md) | ~6 | 0+3 |
| 10 | Platform Resilience — Doctor, Degraded Mode, Mid-Turn | [PLATFORM-RESILIENCE](plans/PLATFORM-RESILIENCE.md) | ~6 | 0/1+3 |
| 11 | Self-Verification — Spec Harness + Event Replay + QA Companion | [SELF-VERIFICATION](plans/SELF-VERIFICATION.md) | ~6 | 0/1+2 |
| 12 | Context Economy — Compression + Tool-Groups + Codebase Graph | [CONTEXT-ECONOMY](plans/CONTEXT-ECONOMY.md) | ~6 | 0/1 |
| 13 | Execution Isolation — Sandbox + BYO Runners + Secrets Vault | [EXECUTION-ISOLATION](plans/EXECUTION-ISOLATION.md) | ~8 | 2 |
| 28 | ACP Agent Parity — One Provider, the Whole Platform | [ACP-AGENT-PARITY](plans/ACP-AGENT-PARITY.md) | ~9 | 0 |
| 31 | Lifecycle Doctrine & API Stability — Post-PoC Change Discipline | [LIFECYCLE-DOCTRINE](plans/LIFECYCLE-DOCTRINE.md) | ~3 | 0 (first) |
| 32 | Provider-Boundary Completion — Retire the Slack Residue | [PROVIDER-BOUNDARY-COMPLETION](plans/PROVIDER-BOUNDARY-COMPLETION.md) | ~2 | 0 |
| 47 | Security Hardening — Keychain, Signed Manifests, Gate Fuzzing, SEL Surface | [SECURITY-HARDENING](plans/SECURITY-HARDENING.md) | ~4 | 4 |
| 53 | Remote User Authentication — Log In From the Internet Without Being Home | [REMOTE-USER-AUTH](plans/REMOTE-USER-AUTH.md) | ~4 | 1 |
| 67 | Platform Hardening Floors — Enforcement Floors, Trust Seams & Gate Ergonomics | [PLATFORM-HARDENING-FLOORS](plans/PLATFORM-HARDENING-FLOORS.md) | ~5 | 0/1 |
| 69 | Desktop Computer Use — Native GUI Automation via the Accessibility Tree | [DESKTOP-COMPUTER-USE](plans/DESKTOP-COMPUTER-USE.md) | ~4 | 3 |

### Pillar C — Intelligence & Memory

How the system learns, remembers, and builds knowledge.

| # | Feature | Plan | Sessions | Wave |
|---|---|---|---|---|
| 14 | Memory Graph + Vault — Linked Recall + Editable Mirror | [MEMORY-GRAPH-AND-VAULT](plans/MEMORY-GRAPH-AND-VAULT.md) | ~5 | 0 |
| 15 | Watched Sources — URL/Feed/Dir → Knowledge Ingestion | [WATCHED-SOURCES](plans/WATCHED-SOURCES.md) | ~5 | 2E+3 |
| 16 | Evaluation Substrate — Template Studies + Trust Ladder | [EVALUATION-SUBSTRATE](plans/EVALUATION-SUBSTRATE.md) | ~6 | 3/4 |
| 58 | Feedback Signal — The Thumbs That Actually Teach | [FEEDBACK-SIGNAL](plans/FEEDBACK-SIGNAL.md) | ~3 | 2 |
| 17 | Model Routing Telemetry — Learned Local-vs-Cloud | [MODEL-ROUTING-TELEMETRY](plans/MODEL-ROUTING-TELEMETRY.md) | ~3 | 3 |
| 46 | Learning Visibility — Make the Flywheel Felt | [LEARNING-VISIBILITY](plans/LEARNING-VISIBILITY.md) | ~4 | 1+2+3 |

### Pillar D — Product Surfaces & Ecosystem

What the user sees and how the platform interoperates.

| # | Feature | Plan | Sessions | Wave |
|---|---|---|---|---|
| 18 | Local Model Manager v2 — Sidecar Isolation + DX | [LOCAL-MODEL-MANAGER-V2](plans/LOCAL-MODEL-MANAGER-V2.md) | ~5 | 0 |
| 19 | Platform Legibility — Manifest, SKILL.md, Error Envelopes | [PLATFORM-LEGIBILITY](plans/PLATFORM-LEGIBILITY.md) | ~5 | 0 |
| 20 | Ambient Surfaces — Composable Home + Menu Bar + Views | [AMBIENT-SURFACES](plans/AMBIENT-SURFACES.md) | ~8 | 2F+3 |
| 21 | Proactive Assistant — Triage + Decision Journal | [PROACTIVE-ASSISTANT](plans/PROACTIVE-ASSISTANT.md) | ~5 | 4 |
| 22 | Multimodal I/O — Voice Profiles + Screen Context | [MULTIMODAL-IO](plans/MULTIMODAL-IO.md) | ~5 | 2/3 |
| 23 | Browse Automation — Web-Interaction Action Provider | [BROWSE-AUTOMATION](plans/BROWSE-AUTOMATION.md) | ~5 | 2 |
| 24 | External Access — Inbound API + Capture Proxy + Headless CLI | [EXTERNAL-ACCESS](plans/EXTERNAL-ACCESS.md) | ~7 | 3 |
| 25 | Agent Packs & Portable Bundles | [AGENT-PACKS](plans/AGENT-PACKS.md) | ~6 | 4 |
| 26 | Durability & Multi-Machine Sync | [DURABILITY-AND-SYNC](plans/DURABILITY-AND-SYNC.md) | ~5 | 0+3 |
| 27 | Publication — GitHub Release (core + apps repos) | [PUBLICATION](plans/PUBLICATION.md) | ~2 | 0 |
| 29 | Multi-Tenant Entity Readiness — Harness as a Good Citizen of Shared Stores | [TEAM-SHARED-ENTITIES](plans/TEAM-SHARED-ENTITIES.md) | ~5 | 0+3 |
| 40 | Channel Expansion — Sender Trust + Telegram/Discord/Email | [CHANNEL-EXPANSION](plans/CHANNEL-EXPANSION.md) | ~8 | 1+2 |
| 41 | MCP Read-Only Inbound — Curated Query Surface (extracted from 24) | [MCP-READONLY-INBOUND](plans/MCP-READONLY-INBOUND.md) | ~2 | 0/1 |
| 42 | Inbox/Notifications Unification — One Attention Store + Rules | [INBOX-NOTIFICATIONS-UNIFICATION](plans/INBOX-NOTIFICATIONS-UNIFICATION.md) | ~7 | 1+2 |
| 43 | Onboarding UX — Guided First Run + Progressive Disclosure | [ONBOARDING-UX](plans/ONBOARDING-UX.md) | ~5 | 1+2 |
| 44 | Mobile Companion — Monitor + Approve From the Phone | [MOBILE-COMPANION](plans/MOBILE-COMPANION.md) | ~6 | 2+3 |
| 45 | Desktop Capabilities — Electron as the OS-Capability Surface | [DESKTOP-CAPABILITIES](plans/DESKTOP-CAPABILITIES.md) | ~4 | 2/3 |
| 54 | Companion Apps — Native Clients Over a Local or Remote Gateway | [COMPANION-APPS](plans/COMPANION-APPS.md) | ~4 | 2 |
| 56 | Agent Routing — Suggest-First Specialist Routing | [AGENT-ROUTING](plans/AGENT-ROUTING.md) | ~2 | 2 |
| 59 | Model Use-Cases v2 — Sovereign Vocabulary + Fallback Chains | [MODEL-USE-CASES-V2](plans/MODEL-USE-CASES-V2.md) | ~3 | 2 |
| 63 | Prompt-Cache Substrate — One Middleware Seam That Makes Every Turn Cheaper | [PROMPT-CACHE-SUBSTRATE](plans/PROMPT-CACHE-SUBSTRATE.md) | ~2 | 2 |
| 64 | Cost & Token Observability — Answer "What Did This Cost Me?" | [COST-AND-TOKEN-OBSERVABILITY](plans/COST-AND-TOKEN-OBSERVABILITY.md) | ~2 | 2 |
| 65 | Document Handling Tools — Produce the Formats a Person Actually Sends | [DOCUMENT-HANDLING-TOOLS](plans/DOCUMENT-HANDLING-TOOLS.md) | ~2 | 2 |
| 66 | Email Inbox & Triggers — Mail as a Source, Any Source as a Trigger | [EMAIL-INBOX-AND-TRIGGERS](plans/EMAIL-INBOX-AND-TRIGGERS.md) | ~3 | 2 |

### Pillar E — Launch, Reach & Ecosystem (new in rev 9)

The open-source offering around the product: distribution, verification, discoverability, contribution, and platform reach.

| # | Feature | Plan | Sessions | Wave |
|---|---|---|---|---|
| 33 | CI & Release Engineering — Verifiable Quality + Release Pipeline | [CI-RELEASE-ENGINEERING](plans/CI-RELEASE-ENGINEERING.md) | ~4 | 0 |
| 34 | Distribution & Packaging — One Command to a Talking Agent | [DISTRIBUTION](plans/DISTRIBUTION.md) | ~5 | 0 |
| 35 | Security Legibility — SECURITY.md + Public Threat Model | [SECURITY-LEGIBILITY](plans/SECURITY-LEGIBILITY.md) | ~2 | 0 |
| 36 | Discoverability & Launch — Org, personalclaw.dev, Docs Site, Launch Assets | [DISCOVERABILITY-LAUNCH](plans/DISCOVERABILITY-LAUNCH.md) | ~5 | 0+1 |
| 37 | OSS Operations — Contribution Model, Hygiene, Governance | [OSS-OPERATIONS](plans/OSS-OPERATIONS.md) | ~3 | 0 |
| 38 | Ecosystem Tooling — Scaffold, Registry, Exemplars, Bounties | [ECOSYSTEM-TOOLING](plans/ECOSYSTEM-TOOLING.md) | ~4 | 2+3 |
| 39 | Platform Reach — Reliable ARM + the Windows Ladder | [PLATFORM-REACH](plans/PLATFORM-REACH.md) | ~5 | 1+2 |

### Pillar F — Product Depth & Craft (new in rev 10)

The product getting richer and more polished: platform/app evolution, knowledge-library and session management, and UI/UX consistency + motion craft.

| # | Feature | Plan | Sessions | Wave |
|---|---|---|---|---|
| 48 | App Platform Evolution — Richer Capabilities, Better Apps | [APP-PLATFORM-EVOLUTION](plans/APP-PLATFORM-EVOLUTION.md) | ~4 | 2+3 |
| 49 | Knowledge Library — Collections, Curation, Reading | [KNOWLEDGE-LIBRARY](plans/KNOWLEDGE-LIBRARY.md) | ~4 | 2+3 |
| 50 | Session Management — Search, Organize, Lifecycle | [SESSION-MANAGEMENT](plans/SESSION-MANAGEMENT.md) | ~4 | 2+3 |
| 51 | Design-System Consistency — One Coherent Surface | [DESIGN-SYSTEM-CONSISTENCY](plans/DESIGN-SYSTEM-CONSISTENCY.md) | ~3 | 2 |
| 52 | Fluid Motion — Liquid Morphing & Motion Physics | [FLUID-MOTION](plans/FLUID-MOTION.md) | ~3 | 3 |
| 55 | Chat Craft — Seven Proven Chat-Surface Mechanics | [CHAT-CRAFT](plans/CHAT-CRAFT.md) | ~4 | 2+3 |
| 57 | Personality Themes — Themes That Carry Behavior | [PERSONALITY-THEMES](plans/PERSONALITY-THEMES.md) | ~2 | 3 |
| 60 | Investigate Anywhere — One Chat-With-Context Primitive | [INVESTIGATE-ANYWHERE](plans/INVESTIGATE-ANYWHERE.md) | ~2 | 2 |
| 61 | Artifacts Evolution — First-Class Creative Library | [ARTIFACTS-EVOLUTION](plans/ARTIFACTS-EVOLUTION.md) | ~3 | 2+3 |
| 68 | Product Experience Parity — UX Simplification, Artifacts-as-Apps, Onboarding Import & the App Suite | [PRODUCT-EXPERIENCE-PARITY](plans/PRODUCT-EXPERIENCE-PARITY.md) | ~12 | 1+2+3 |

**Total estimated effort:** ~339 sessions across 66 plans.

---

## Execution Waves

**Wave 0 — Front-runners (no v2 dependency; launch-gating set added in rev 9):**
- **Lifecycle Doctrine (plan 31 — lands FIRST: its doctrine + stability tiers shape how every other plan's changes land)**
- **Publication (plan 27, amended — release under the `PersonalClaw` org; all engineering prerequisites complete):**
  create org + migrate repos, push, tag v0.1.0, post-publication verification against the live remote
- **CI & Release Engineering (plan 33: red-test triage → green main, PR/merge workflows, release pipeline, supply chain)**
- **Distribution (plan 34: wheels with prebuilt web assets, PyPI/uvx, images, bootstrap, self-update generalization)**
- **Security Legibility (plan 35: SECURITY.md, public threat model, honest-limitations)**
- **Discoverability & Launch (plan 36 S1-3: claim org/domain, website + docs site + llms.txt, launch assets)**
- **OSS Operations (plan 37: stated model, hygiene set, DCO, AGENTS.md, GitHub Discussions; the chat server + continuity floor were owner-descoped 2026-07-31 — handled separately)**
- **Provider-Boundary Completion (plan 32: slack-sdk dep out, doctor/setup extraction, logger-root seam)**
- **MCP Read-Only Inbound (plan 41 S1: substrate + mount; S2 lands Wave 0/1)**
- Autonomy Guardrails (full)
- Local Model Manager v2 (full)
- Platform Legibility (Sessions 1-3: manifest, SKILL.md, error envelopes)
- Platform Resilience (Sessions 1-3: doctor probes, degraded contracts, mid-turn handling)
- Context Economy (Sessions 1-4: compression extensions, tool groups, background compaction)
- Memory Graph + Vault (Sessions 1-3: backlinks table, alias graph, vault projection)
- Durability & Sync (Sessions 1-3: snapshot coverage gap, manifest, scheduled backups)
- Self-Verification (Sessions 1-2: spec harness, event-trace replay infrastructure)
- Learning Flywheel (steps 1-4: LearningGate, capture hygiene, proposal queue, lesson migration)
- Universal Planning dead-code deletion (verified dead `context_management.py` plan-mode)
- ACP Agent Parity (full: 3 per-provider validation sweeps → severity-ordered parity fixes)
- Multi-Tenant Entity Readiness (Sessions 1-3)

**Wave 1 — The engine + first reach track (engine unchanged; reach runs alongside):**
- WORKFLOWS-V2 Slices 0-5 (data model + frontier scheduler, engine-owned completion, effect ledger, mutations + checkpoints/fork, chat tools + HTTP/FE + live widget; Self-Verification replay gates the journal format)
- Channel Expansion (Sessions 1-3: sender-trust core seam, then Telegram)
- **Remote User Authentication (plan 53: durable session foundation → owner credential + CLI/deploy bootstrap → login front door → public-exposure hardening — the prerequisite for remote companion clients)**
- Inbox/Notifications Unification (Sessions 1-3: kind registry + rules engine, inbox as the attention store, settings unification) — executed as a maintainer clean break under the pre-1.0 banner (the earlier "first full LIFECYCLE-DOCTRINE exercise" framing described a methodology, not a dependency; corrected 2026-07-30)
- Learning Visibility (Sessions 1-2: end-to-end visible slice, "What I learned" surfaces)
- Onboarding UX (Sessions 1-2: guided first run, progressive disclosure)
- Platform Reach Track A (ARM: arm64 CI + SQLite-fallback verification, multi-arch release-blocking)
- Discoverability & Launch (Sessions 4-5: comparison pages, listings program, research-library publication path)
- MCP Read-Only Inbound (Session 2 if not landed in Wave 0)

**Wave 2 — Convergence (parallel tracks once Slices 0-2 land):**
- Track A: Loops Evolution → Universal Planning
- Track B: Knowledge Synthesis
- Track C: v2 Slices 6-8 interleaved with Work-Containers
- Track D: Tasks & SOPs
- Track E: Watched Sources (Sessions 1-5)
- Track F: Ambient Surfaces (Sessions 1-3)
- Execution Isolation; Browse Automation; Self-Verification Session 3; Multimodal I/O (Sessions 1-2); Platform Legibility (Sessions 4-5); Harness Craft
- Channel Expansion (Sessions 4-8: Discord, email, channel-author ramp)
- Inbox/Notifications Unification (Sessions 4-5: proposal-surface fold-in, digest + cleanup)
- Onboarding UX (Sessions 3-4: approval-brief polish, stranger validation)
- **Companion Apps (plan 54: connectivity contract + Devices registry, LAN discovery, endpoint switching, native-wrapper coordination — MOBILE-COMPANION + DESKTOP-CAPABILITIES consume it; after Remote-User-Auth S1)**
- Mobile Companion (Sessions 1-3: remote-access story, PWA companion view, web push)
- Desktop Capabilities (Sessions 1-3: rebuild + signing, capability bridge, live audio)
- Ecosystem Tooling (Sessions 1-2: scaffold + template, registry data tier)
- Platform Reach Track B (Windows rungs 1-2: containers + WSL2; rung-3 audit)
- Learning Visibility (Session 3: refinement arm surfaced)
- **Design-System Consistency (full, ~3: audit → token/primitive hardening → a11y/parity + CI ratchet — run early so later surfaces inherit a clean baseline)**
- **Knowledge Library (Sessions 1-2: collections, curation + taxonomy + bulk)**
- **Session Management (Sessions 1-2: cross-session search, smart organization + bulk + auto-archive)**
- **App Platform Evolution (Sessions 1-2: background/event capabilities, quality bar + native evolution)**
- *The rev-12/13 batch below is listed in deliberate in-wave pickup order — contract-owners and floor multipliers first, then the ChatPage-heavy work serialized (Chat-Craft → Agent-Routing → Artifacts' chat touches) so no two of them churn ChatPage concurrently:*
- **Model Use-Cases v2 (full, ~3: vocabulary + chain resolver, consumer wiring, Settings chains UI — rev 13; floor multiplier: background/loop chores stop burning the flagship chat model, and Model-Routing-Telemetry later learns inside this vocabulary)**
- **Feedback Signal (full, ~3: capture store + SDK, thumbs on core surfaces, deterministic thresholds — rev 13; contract-owner: its records gate Autonomy-Guardrails' earned-autonomy ladder and Evaluation-Substrate's field loop)**
- **Investigate Anywhere (full, ~2: fenced primitive + SDK, adoption sweep — rev 13; contract-owner: Artifacts-Evolution S3 consumes its resolver registry)**
- **Chat Craft (Sessions 1-3: rewind + queue manners, find + quote, chips + smooth streaming — rev 12; the heaviest ChatPage churn of the batch — land it before the other ChatPage touches)**
- **Agent Routing (full, ~2: routing metadata + classifier, suggestion chip + suppression — rev 12; after Feedback-Signal so its routing-pair double-write lands live, and after Chat-Craft's ChatPage churn)**
- **Artifacts Evolution (Sessions 1-2: entity split + store hardening, library surface — rev 13; S3 unblocks the moment Investigate-Anywhere lands)**
- Platform Resilience Session 6 (`steer` mid-turn policy — rev 12 amendment; after the capability-gate recon; independent — slots anywhere in the batch)

**Wave 3 — Substrate unification + intelligence:**
- Automation Substrate (steps 1-9); Learning Flywheel (steps 5-9); Watched Sources (Sessions 6-9); Ambient Surfaces (Sessions 4-6); Memory Graph + Vault (Sessions 4-5); Durability & Sync (Sessions 4-5); Model Routing Telemetry; Platform Resilience Session 4; Multimodal I/O (Sessions 3-5); Evaluation Substrate (Sessions 1-2); Multi-Tenant Entity Readiness (Sessions 4-5)
- External Access (inbound API, capture proxy, A2A — §3 already served by plan 41; inherits its substrate)
- Mobile Companion (Sessions 4-6: wrapper tier, pairing, store packaging)
- Desktop Capabilities (Session 4: presence + additional platforms, gated on Platform Reach)
- Ecosystem Tooling (Sessions 3-4: exemplar apps, bounty program, registry surface)
- Learning Visibility (Session 4: the public benchmark, with Evaluation Substrate)
- **Knowledge Library (Session 3: reading experience, dedup/merge, library home)**
- **Session Management (Session 3: lifecycle, templates, export/share)**
- **App Platform Evolution (Sessions 3-4: app-to-app messaging, richer UI contribution)**
- **Fluid Motion (full, ~3: physics system → liquid morphing primitives → route transitions + budget proof — on the consistency baseline)**
- *Rev-12/13 Wave-3 additions in dependency order — the proposals contract first (three later items emit through it), then its consumers:*
- Inbox/Notifications Unification (Sessions 6-7: second-opinion verify gate — rev 12; proposals contract + app emission — rev 13; **take these early in the wave** — Feedback-Signal retire proposals, Guardrails promotion offers, and app-update notifications all upgrade from `notify()` to the typed contract the moment S7 lands)
- Autonomy Guardrails (Sessions 5-6: earned-autonomy rung ladder — rev 13; after Feedback-Signal S1, ideally after Inbox-Unify S7 so promotion proposals ride the typed contract from birth)
- Ambient Surfaces (Sessions 7-8: dashboard-as-views presets + modern agent-world seam — rev 13; Mission Control after Inbox-Unification S1-2)
- **Chat Craft (Session 4: screen-snip + polish/validation — rev 12; independent tail)**
- **Artifacts Evolution (Session 3: iterate-with-agent + diffs + chat references — rev 13; needs Investigate-Anywhere's resolver registry; sequence its ChatPage touches after Chat-Craft S4)**
- **Personality Themes (full, ~2: personality registry + two first-party proofs — rev 12; after Fluid Motion — it rides those dials)**

**Wave 4 — Capstone + retirements:**
- Loop engine retirement (Loops Evolution Phase 4); Autonudge absorption; v2 Slices 9-11
- Proactive Assistant (triage pipeline + decision journal — its ambient digest slice already landed via plan 42)
- Evaluation Substrate (Sessions 3-6); Agent Packs & Portable Bundles
- **Security Hardening (plan 47: keychain, signed manifests, gate fuzzing, SEL surface, external review)**
- Agent Rooms (plan 62 — PROPOSED; un-defer only after WORKFLOWS-V2 core slices + ACP-AGENT-PARITY land; council workflow template front-runs it as a LOOPS-EVOLUTION library candidate)

```
Wave 0: DOCTRINE→ PUBLICATION  CI/RELEASE  DISTRIBUTION  SEC-LEGIBILITY  DISCOVER(1-3)  OSS-OPS  BOUNDARY-DONE  MCP-RO(1)
        GUARDRAILS  LMM-V2  LEGIBILITY(1-3)  RESILIENCE(1-3)  CONTEXT-ECON  MEM-GRAPH(1-3)  DURABILITY(1-3)  SELF-VERIF(1-2)  LEARNING(1-4)  ACP-PARITY  MT-READY(1-3)
                    │
Wave 1: ════ WORKFLOWS-V2 Slices 0-5 ════  ∥  CHANNELS(1-3)  REMOTE-AUTH  INBOX-UNIFY(1-3)  LEARN-VIS(1-2)  ONBOARD(1-2)  ARM  DISCOVER(4-5)  MCP-RO(2)
                    │
Wave 2: LOOPS→PLAN  KNOWLEDGE  v2-6-8⇄CONTAINERS  TASKS  WATCHED(1-5)  AMBIENT(1-3)  ISOLATION  BROWSE  QA  MULTIMODAL(1-2)  LEGIBILITY(4-5)
        CHANNELS(4-8)  INBOX-UNIFY(4-5)  ONBOARD(3-4)  COMPANION-APPS  MOBILE(1-3)  DESKTOP(1-3)  ECOSYSTEM(1-2)  WINDOWS(1-2)  LEARN-VIS(3)
        MODEL-UC-V2 → FEEDBACK-SIGNAL → INVESTIGATE → CHAT-CRAFT(1-3) → AGENT-ROUTING → ARTIFACTS(1-2)  ∥  RESILIENCE(6:steer)
                    │
Wave 3: AUTOMATION(1-10)  LEARNING(5-9)  WATCHED(6-9)  AMBIENT(4-6)  MEM-GRAPH(4-5)  DURABILITY(4-5)  EXT-ACCESS  MODEL-ROUTING-TEL  RESILIENCE(4)  MULTIMODAL(3-5)  EVAL(1-2)  MT-READY(4-5)
        MOBILE(4-6)  DESKTOP(4)  ECOSYSTEM(3-4)  LEARN-VIS(4)
        INBOX-UNIFY(6-7:verify+proposals) → GUARDRAILS(5-6:earned-autonomy)  AMBIENT(7-8:views+worlds)
        CHAT-CRAFT(4) → ARTIFACTS(3)  FLUID-MOTION → PERSONALITY-THEMES
                    │
Wave 4: LOOPS-Ph4→AUTO(final)  v2-9-11  PROACTIVE  EVAL(3-6)  AGENT-PACKS  SEC-HARDENING  AGENT-ROOMS(deferred)
```

---

## Boundary Note: Memory vs Knowledge

- **Knowledge** = the user's personal items — documents, files, photos, notes, ingested URLs; `knowledge.db`; future providers (Google Drive, Google Photos) plug in via `KnowledgeProvider` ABC. Plans: KNOWLEDGE-SYNTHESIS, WATCHED-SOURCES, PROACTIVE-ASSISTANT (decision journal items).
- **Memory** = the harness's internal mechanics — facts/facets/episodic/procedural/lessons about the user; `memory.db` (cwd-partitioned); the learning lifecycle belongs here. Plans: LEARNING-FLYWHEEL, MEMORY-GRAPH-AND-VAULT, PROACTIVE-ASSISTANT (approval patterns).

## Boundary Note: Inbox vs Notifications (rev 9)

- **Inbox** = THE durable attention store: externally-arriving items AND standing agent requests (proposals, needs-input), typed by (source, kind), with lifecycle and dedup. Plan 42 unifies the surfaces (skills proposals, flywheel proposal queue → inbox kinds).
- **Notifications** = the ephemeral, per-(source, kind)-configurable delivery layer over inbox items and transient events — `DashboardState.notify()` stays the single choke point; rules decide never/badge/immediate/digest and targets (dashboard, channel DM, push). Unread counts derive from inbox lifecycle only.

---

## Research Library

The 95-source research corpus has been distilled into a source-agnostic learnings library at `docs/research/learnings/` (14 topic files, ~320 mechanisms). Per-source originals have been retired — all durable content lives in the topic files; all roadmap-actionable items are folded into the plans above. See `docs/research/learnings/README.md` for the index and highest-conviction cross-corpus findings. Public republication of curated topics on personalclaw.dev is planned (DISCOVERABILITY-LAUNCH Session 5).

Targeted research docs live at `docs/roadmap/research/`:

- `multi-tenancy-entity-audit.md` (2026-07-14) — code-evidence audit of every provider seam and entity store: tenancy-readiness matrix and the harness-side readiness gaps (username identity, contributor attribution, owner-filter on trigger arming, provenance-weighted recall). Grounds plan 29.
- `team-shared-harness-research.md` (2026-07-14) — adversarially-verified internet research on 2025-2026 shared/multi-tenant agentic harnesses (11 unanimous findings). Ecosystem context for plan 29 — the harness implements only the client side of these patterns.
- `acp-agent-parity-audit.md` — native loop vs claude-code/codex/kiro-cli capability matrix. Grounds plan 28.
- The 2026-07-18 pre-launch investigation & owner alignment review grounds plans 31-47 (rev 9): offering gap analysis (distribution, channels, contribution model, CI, discoverability, platform reach), the inbox/notifications boundary investigation (plan 42), and the provider-boundary residue verification (plan 32).
