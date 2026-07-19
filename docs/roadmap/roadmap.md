# PersonalClaw Roadmap

Active and planned feature work. Each entry links to a detailed plan document in `plans/`.

**Last updated:** 2026-07-19 · **rev 10** — 52 plans / 6 pillars / ~285 sessions.

- **rev 10** — Pillar F "Product Depth & Craft" added (48 App-Platform-Evolution, 49 Knowledge-Library, 50 Session-Management, 51 Design-System-Consistency, 52 Fluid-Motion), from the owner's product-depth ask.
- **rev 9** — pre-launch alignment: 17 plans (31-47) added from the pre-launch investigation & owner review under new Pillar E (Launch, Reach & Ecosystem) + additions to B/C/D; PUBLICATION amended (repos under the `PersonalClaw` GitHub org, personalclaw.dev primary domain, force-push/SOURCE_REV retired → feature-branches-to-`main`); EXTERNAL-ACCESS §3 read-only MCP extracted to plan 41. Deepened plans carry Contracts & Interfaces + executor task tables; see the three companion docs below.
- **rev 4-8** (earlier) — ACP Agent Parity (4), TEAM-SHARED-ENTITIES (5), Multi-Tenant rescope (6), grok-build learnings folded into CONTEXT-ECONOMY/EXTERNAL-ACCESS/PLATFORM-RESILIENCE (7), HARNESS-CRAFT (8).

---

## Program Structure

52 plans across 6 conceptual pillars. Plans interleave via execution waves; the engine carries embedded acceptance criteria from downstream plans. The research corpus lives at `docs/research/learnings/` (14 source-agnostic topic files, ~320 mechanisms) — feed a topic file to any implementation session for mechanism-level grounding.

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
| 1 | Workflows v2 — Composable Execution Platform | [WORKFLOWS-V2](plans/WORKFLOWS-V2.md) | ~31 | 1 |
| 2 | Loop Evolution — Loops as Workflow Templates | [LOOPS-EVOLUTION](plans/WORKFLOWS-V2-LOOPS-EVOLUTION.md) | ~5 | 2A |
| 3 | Universal Project Planning + Planner Collapse | [UNIVERSAL-PLANNING](plans/WORKFLOWS-V2-UNIVERSAL-PLANNING.md) | ~6 | 2A |
| 4 | Tasks & SOPs as Workflow Primitives | [TASKS-SOPS](plans/WORKFLOWS-V2-TASKS-SOPS.md) | ~7 | 2D |
| 5 | Knowledge Artifact Synthesis Nodes | [KNOWLEDGE-SYNTHESIS](plans/WORKFLOWS-V2-KNOWLEDGE-SYNTHESIS.md) | ~6 | 2B |
| 6 | Work-Container Hierarchy — Project as Sole Umbrella | [WORK-CONTAINERS](plans/WORKFLOWS-V2-WORK-CONTAINERS.md) | ~9 | 2C |
| 7 | One Automation Substrate — Triggers Fire Workflows | [AUTOMATION-SUBSTRATE](plans/WORKFLOWS-V2-AUTOMATION-SUBSTRATE.md) | ~9 | 3 |
| 8 | Learning Flywheel — One Lifecycle for Learned Artifacts | [LEARNING-FLYWHEEL](plans/WORKFLOWS-V2-LEARNING-FLYWHEEL.md) | ~11 | 0+3 |
| 30 | Harness Craft — Fast Worktrees + Best-of-N + Check-Work | [HARNESS-CRAFT](plans/HARNESS-CRAFT.md) | ~3 | 2/3 |

### Pillar B — Safety, Resilience & Operations

Cross-cutting floors everything else depends on.

| # | Feature | Plan | Sessions | Wave |
|---|---|---|---|---|
| 9 | Autonomy Guardrails — Budgets, Denylist, Kill Switch | [AUTONOMY-GUARDRAILS](plans/AUTONOMY-GUARDRAILS.md) | ~4 | 0 |
| 10 | Platform Resilience — Doctor, Degraded Mode, Mid-Turn | [PLATFORM-RESILIENCE](plans/PLATFORM-RESILIENCE.md) | ~5 | 0/1+3 |
| 11 | Self-Verification — Spec Harness + Event Replay + QA Companion | [SELF-VERIFICATION](plans/SELF-VERIFICATION.md) | ~6 | 0/1+2 |
| 12 | Context Economy — Compression + Tool-Groups + Codebase Graph | [CONTEXT-ECONOMY](plans/CONTEXT-ECONOMY.md) | ~6 | 0/1 |
| 13 | Execution Isolation — Sandbox + BYO Runners + Secrets Vault | [EXECUTION-ISOLATION](plans/EXECUTION-ISOLATION.md) | ~7 | 2 |
| 28 | ACP Agent Parity — One Provider, the Whole Platform | [ACP-AGENT-PARITY](plans/ACP-AGENT-PARITY.md) | ~9 | 0 |
| 31 | Lifecycle Doctrine & API Stability — Post-PoC Change Discipline | [LIFECYCLE-DOCTRINE](plans/LIFECYCLE-DOCTRINE.md) | ~3 | 0 (first) |
| 32 | Provider-Boundary Completion — Retire the Slack Residue | [PROVIDER-BOUNDARY-COMPLETION](plans/PROVIDER-BOUNDARY-COMPLETION.md) | ~2 | 0 |
| 47 | Security Hardening — Keychain, Signed Manifests, Gate Fuzzing, SEL Surface | [SECURITY-HARDENING](plans/SECURITY-HARDENING.md) | ~4 | 4 |

### Pillar C — Intelligence & Memory

How the system learns, remembers, and builds knowledge.

| # | Feature | Plan | Sessions | Wave |
|---|---|---|---|---|
| 14 | Memory Graph + Vault — Linked Recall + Editable Mirror | [MEMORY-GRAPH-AND-VAULT](plans/MEMORY-GRAPH-AND-VAULT.md) | ~5 | 0 |
| 15 | Watched Sources — URL/Feed/Dir → Knowledge Ingestion | [WATCHED-SOURCES](plans/WATCHED-SOURCES.md) | ~5 | 2E+3 |
| 16 | Evaluation Substrate — Template Studies + Trust Ladder | [EVALUATION-SUBSTRATE](plans/EVALUATION-SUBSTRATE.md) | ~5 | 3/4 |
| 17 | Model Routing Telemetry — Learned Local-vs-Cloud | [MODEL-ROUTING-TELEMETRY](plans/MODEL-ROUTING-TELEMETRY.md) | ~3 | 3 |
| 46 | Learning Visibility — Make the Flywheel Felt | [LEARNING-VISIBILITY](plans/LEARNING-VISIBILITY.md) | ~4 | 1+2+3 |

### Pillar D — Product Surfaces & Ecosystem

What the user sees and how the platform interoperates.

| # | Feature | Plan | Sessions | Wave |
|---|---|---|---|---|
| 18 | Local Model Manager v2 — Sidecar Isolation + DX | [LOCAL-MODEL-MANAGER-V2](plans/LOCAL-MODEL-MANAGER-V2.md) | ~5 | 0 |
| 19 | Platform Legibility — Manifest, SKILL.md, Error Envelopes | [PLATFORM-LEGIBILITY](plans/PLATFORM-LEGIBILITY.md) | ~5 | 0 |
| 20 | Ambient Surfaces — Composable Home + Menu Bar | [AMBIENT-SURFACES](plans/AMBIENT-SURFACES.md) | ~6 | 2F+3 |
| 21 | Proactive Assistant — Triage + Decision Journal | [PROACTIVE-ASSISTANT](plans/PROACTIVE-ASSISTANT.md) | ~5 | 4 |
| 22 | Multimodal I/O — Voice Profiles + Screen Context | [MULTIMODAL-IO](plans/MULTIMODAL-IO.md) | ~5 | 2/3 |
| 23 | Browse Automation — Web-Interaction Action Provider | [BROWSE-AUTOMATION](plans/BROWSE-AUTOMATION.md) | ~4 | 2 |
| 24 | External Access — Inbound API + Capture Proxy + Headless CLI | [EXTERNAL-ACCESS](plans/EXTERNAL-ACCESS.md) | ~7 | 3 |
| 25 | Agent Packs & Portable Bundles | [AGENT-PACKS](plans/AGENT-PACKS.md) | ~6 | 4 |
| 26 | Durability & Multi-Machine Sync | [DURABILITY-AND-SYNC](plans/DURABILITY-AND-SYNC.md) | ~5 | 0+3 |
| 27 | Publication — GitHub Release (core + apps repos) | [PUBLICATION](plans/PUBLICATION.md) | ~2 | 0 |
| 29 | Multi-Tenant Entity Readiness — Harness as a Good Citizen of Shared Stores | [TEAM-SHARED-ENTITIES](plans/TEAM-SHARED-ENTITIES.md) | ~5 | 0+3 |
| 40 | Channel Expansion — Sender Trust + Telegram/Discord/Email | [CHANNEL-EXPANSION](plans/CHANNEL-EXPANSION.md) | ~8 | 1+2 |
| 41 | MCP Read-Only Inbound — Curated Query Surface (extracted from 24) | [MCP-READONLY-INBOUND](plans/MCP-READONLY-INBOUND.md) | ~2 | 0/1 |
| 42 | Inbox/Notifications Unification — One Attention Store + Rules | [INBOX-NOTIFICATIONS-UNIFICATION](plans/INBOX-NOTIFICATIONS-UNIFICATION.md) | ~5 | 1+2 |
| 43 | Onboarding UX — Guided First Run + Progressive Disclosure | [ONBOARDING-UX](plans/ONBOARDING-UX.md) | ~4 | 1+2 |
| 44 | Mobile Companion — Monitor + Approve From the Phone | [MOBILE-COMPANION](plans/MOBILE-COMPANION.md) | ~6 | 2+3 |
| 45 | Desktop Capabilities — Electron as the OS-Capability Surface | [DESKTOP-CAPABILITIES](plans/DESKTOP-CAPABILITIES.md) | ~4 | 2/3 |

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

**Total estimated effort:** ~285 sessions across 52 plans.

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
- **OSS Operations (plan 37: stated model, hygiene set, DCO, AGENTS.md, community surface, continuity)**
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
- Inbox/Notifications Unification (Sessions 1-3: kind registry + rules engine, inbox as the attention store, settings unification) — first full LIFECYCLE-DOCTRINE exercise
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
- Mobile Companion (Sessions 1-3: remote-access story, PWA companion view, web push)
- Desktop Capabilities (Sessions 1-3: rebuild + signing, capability bridge, live audio)
- Ecosystem Tooling (Sessions 1-2: scaffold + template, registry data tier)
- Platform Reach Track B (Windows rungs 1-2: containers + WSL2; rung-3 audit)
- Learning Visibility (Session 3: refinement arm surfaced)
- **Design-System Consistency (full, ~3: audit → token/primitive hardening → a11y/parity + CI ratchet — run early so later surfaces inherit a clean baseline)**
- **Knowledge Library (Sessions 1-2: collections, curation + taxonomy + bulk)**
- **Session Management (Sessions 1-2: cross-session search, smart organization + bulk + auto-archive)**
- **App Platform Evolution (Sessions 1-2: background/event capabilities, quality bar + native evolution)**

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

**Wave 4 — Capstone + retirements:**
- Loop engine retirement (Loops Evolution Phase 4); Autonudge absorption; v2 Slices 9-11
- Proactive Assistant (triage pipeline + decision journal — its ambient digest slice already landed via plan 42)
- Evaluation Substrate (Sessions 3-5); Agent Packs & Portable Bundles
- **Security Hardening (plan 47: keychain, signed manifests, gate fuzzing, SEL surface, external review)**

```
Wave 0: DOCTRINE→ PUBLICATION  CI/RELEASE  DISTRIBUTION  SEC-LEGIBILITY  DISCOVER(1-3)  OSS-OPS  BOUNDARY-DONE  MCP-RO(1)
        GUARDRAILS  LMM-V2  LEGIBILITY(1-3)  RESILIENCE(1-3)  CONTEXT-ECON  MEM-GRAPH(1-3)  DURABILITY(1-3)  SELF-VERIF(1-2)  LEARNING(1-4)  ACP-PARITY  MT-READY(1-3)
                    │
Wave 1: ════ WORKFLOWS-V2 Slices 0-5 ════  ∥  CHANNELS(1-3)  INBOX-UNIFY(1-3)  LEARN-VIS(1-2)  ONBOARD(1-2)  ARM  DISCOVER(4-5)  MCP-RO(2)
                    │
Wave 2: LOOPS→PLAN  KNOWLEDGE  v2-6-8⇄CONTAINERS  TASKS  WATCHED(1-5)  AMBIENT(1-3)  ISOLATION  BROWSE  QA  MULTIMODAL(1-2)  LEGIBILITY(4-5)
        CHANNELS(4-8)  INBOX-UNIFY(4-5)  ONBOARD(3-4)  MOBILE(1-3)  DESKTOP(1-3)  ECOSYSTEM(1-2)  WINDOWS(1-2)  LEARN-VIS(3)
                    │
Wave 3: AUTOMATION(1-9)  LEARNING(5-9)  WATCHED(6-9)  AMBIENT(4-6)  MEM-GRAPH(4-5)  DURABILITY(4-5)  EXT-ACCESS  ROUTING  RESILIENCE(4)  MULTIMODAL(3-5)  EVAL(1-2)  MT-READY(4-5)
        MOBILE(4-6)  DESKTOP(4)  ECOSYSTEM(3-4)  LEARN-VIS(4)
                    │
Wave 4: LOOPS-Ph4→AUTO(final)  v2-9-11  PROACTIVE  EVAL(3-5)  AGENT-PACKS  SEC-HARDENING
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

The 95-source competitive-research corpus has been distilled into a source-agnostic learnings library at `docs/research/learnings/` (14 topic files, ~320 mechanisms). Per-source originals have been retired — all durable content lives in the topic files; all roadmap-actionable items are folded into the plans above. See `docs/research/learnings/README.md` for the index and highest-conviction cross-corpus findings. Public republication of curated topics on personalclaw.dev is planned (DISCOVERABILITY-LAUNCH Session 5).

Targeted research docs live at `docs/roadmap/research/`:

- `multi-tenancy-entity-audit.md` (2026-07-14) — code-evidence audit of every provider seam and entity store: tenancy-readiness matrix and the harness-side readiness gaps (username identity, contributor attribution, owner-filter on trigger arming, provenance-weighted recall). Grounds plan 29.
- `team-shared-harness-research.md` (2026-07-14) — adversarially-verified internet research on 2025-2026 shared/multi-tenant agentic harnesses (11 unanimous findings). Ecosystem context for plan 29 — the harness implements only the client side of these patterns.
- `acp-agent-parity-audit.md` — native loop vs claude-code/codex/kiro-cli capability matrix. Grounds plan 28.
- The 2026-07-18 pre-launch investigation & owner alignment review grounds plans 31-47 (rev 9): competitive/offering gap analysis (distribution, channels, contribution model, CI, discoverability, platform reach), the inbox/notifications boundary investigation (plan 42), and the provider-boundary residue verification (plan 32).
