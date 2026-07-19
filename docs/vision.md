# PersonalClaw — Vision

The north star. When code and this document disagree, reconcile — either the code
drifted, or the vision needs a deliberate update.

For the as-built architecture see `docs/architecture/`; for the plan index see
`docs/roadmap/roadmap.md`.

---

## Product vision

PersonalClaw is an **agentic operating system for one person**: a platform where
agents accomplish the user's work using a rich, user-assembled set of capabilities.
It is local-first, provider-agnostic, and MIT-licensed.

**Primary experiences:**
1. **Goal Loops** — the user gives a target; the agent classifies, plans, and loops
   autonomously — chipping away one cycle at a time, observable, pausable, resumable.
2. **Agentic chat** — conversational sessions where the agent acts with full tool access.
3. **Automation** — triggers, schedules, and workflows that fire without the user present.

**Delivery surfaces** (same core everywhere): local server, Docker compose, desktop app.

---

## Architecture tenets

1. **Provider-agnostic core.** Core owns *capability logic*; provider-specific logic
   lives in its own app bundle (native / first-party / third-party). Never hardcode a
   vendor path into core. The boundary is the app manifest + the typed provider contract.

2. **Clean break, always.** No backward-compatibility shims, no dual implementations,
   no dead code carried forward. When a design evolves, the old path is deleted in the
   same commit. Code is the authority; stale docs are bugs.

3. **Local-first, optionally connected.** All data lives under one `~/.personalclaw`
   home by default. Remote backends are opt-in via provider apps — the system degrades
   gracefully to local-only and never requires network for core operation.

4. **One path per concern.** Each piece of logic, each event transport, each storage
   access has exactly one implementation path. Dual paths are drift waiting to happen.

5. **Autonomy bounded by deterministic guardrails.** Safety is enforced by failure
   breakers, expiring trust, approval timeouts, deny-lists, and single-flight locks —
   never by the model's self-judgment.

6. **As-built is the spec.** The architecture documents describe the code's current
   reality; the roadmap plans describe intended changes. No document is authoritative
   over what the code actually does.

---

## Tech design principles

- **Entities are either standalone or pluggable.** Standalone entities (sessions,
  loops, artifacts, workflows) are core-owned: their storage, lifecycle, and semantics
  are not abstractable to a third party. Pluggable entities (tasks, memory, knowledge,
  search, models, channels, tools, skills, prompts, inbox sources, triggers) have a
  typed provider contract an app can implement. Before seaming a new entity, decide
  which kind it is.

- **Apps are the extension mechanism.** The app platform (quarantine → scan-gate →
  install → backend subprocess with scoped token) is how all provider-specific logic
  ships. An app can host a remote backend, serve a UI, register MCP tools, declare
  crons, and contribute to any pluggable entity — all permission-gated.

- **The context engine is a pluggable assembly substrate.** What goes into the model
  is assembled via four hooks (ingest / assemble / compact / after_turn); new behaviors
  slot in as engines, never by hacking the assembly path.

- **One learning lifecycle.** The system improves via a single capture path (memory +
  skills + workflows), not scattered heuristics. User frustration is a signal;
  environment-dependent failures are never learned.

- **Realtime: right transport per concern.** Always-on dashboard state rides one
  multiplexed WebSocket; page-scoped feeds get per-resource SSE. No event is
  delivered redundantly over two channels.

---

## Future evolution vision

### Multi-tenant entity readiness

PersonalClaw stays personal but evolves to be a **good citizen of shared stores**.
When a team hosts a shared task tracker, trigger repository, or memory space — and
a PersonalClaw app integrates with it — the harness handles the multi-user world
gracefully:

- **Identity:** the harness knows its owner's username (first-boot onboarding;
  eventually SSO/enterprise login). Entity records carry optional contributor
  attribution defaulting to the owner.
- **Tasks:** a multi-tenant task provider may return tasks assigned to others —
  the harness displays them, filters "mine vs everyone" in views, and counts only
  the owner's items in Home widgets.
- **Triggers:** a shared trigger store may contain triggers created by others —
  the harness arms and fires only the owner's; others are visible but inert.
- **Memory:** a shared memory provider may return memories from others — recall
  carries contributor provenance (labeled), and ranking weights the owner's own
  contributions higher.
- **Boundary:** sharing semantics, permissions, claim/lease mechanics, and
  coordination protocols belong to the shared store / application design —
  not to this harness.

### ACP agent backends

The native execution loop has deep harness integration. ACP-provided agent backends
(claude code, codex, kiro cli) are supported through the ACP seam — the chat runner
is provider-neutral, but in-loop machinery (MCP tool delivery, approval gates, loop
guards, learning, resume) has parity gaps being closed (see plan 28). The end state:
a user who binds one ACP provider can use the entire platform end-to-end.

### The broader ecosystem picture

PersonalClaw's plug-and-play architecture enables a unique gap: **shared-data
harnesses**. A team where each member runs their own PersonalClaw instance can plug
team-shared backends for triggers, tasks, knowledge, and memory — gaining shared
work visibility without giving up personal autonomy. The harness is never the
multi-tenant system; it is one well-behaved participant in one.

---

## How to use this document

- **Before adding a provider seam**, confirm the entity is pluggable (see above).
- **Before a refactor**, verify the relevant tenet. If unsure, the architecture
  docs + roadmap research corpus have the grounded details.
- **Before deviating**, update this document first — silence ≠ consent.
