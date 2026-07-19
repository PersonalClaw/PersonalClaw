# Research Learnings Library

Source-agnostic distillation of a 95-source competitive-research corpus, created 2026-07-13. Each topic file compresses everything the corpus taught about one domain into two sections: **Principles** (the durable truths, each backed by convergent evidence across independent systems) and **Mechanisms** (implementation-ready designs with concrete parameters, schemas, and thresholds).

Provenance: distilled from 95 per-source research files (5 batches, 2026-07-12/13). The originals have been retired — all durable content lives here; all roadmap-actionable items have been folded into the 26 plans under `docs/roadmap/plans/`.

## How to use this library

- **Feed a topic file directly to a plan author or implementation session.** Each file is self-contained and sized to fit comfortably in context (~150–215 lines). Cross-references between topics are relative links.
- **Treat Mechanisms sections as implementation-ready.** They carry exact schemas, state machines, thresholds, and formulas (e.g. compaction ladders, decay math, gate predicates) that can be adopted verbatim and adapted to PersonalClaw's stack.
- **Treat Principles as design review criteria.** When a plan contradicts a Principle, the plan needs an explicit argument for why — the Principles are convergent findings across multiple independent systems, not single-source opinions.

## Topics

| Topic | What it covers |
|---|---|
| [agent-harness-engineering](agent-harness-engineering.md) | The harness as the lever over the model: agent loop anatomy, context compaction ladder, tool-output offloading, the ratchet, feedforward/feedback control taxonomy. |
| [automation-and-triggers](automation-and-triggers.md) | Always-on background work: scheduler-dispatches-never-executes, the full trigger taxonomy, crash-safe firing, boot sweeps, unattended-vs-interactive policy regimes, pull-over-push. |
| [ecosystem-and-interop](ecosystem-and-interop.md) | Distribution and interop: packaging as the product, one canonical source with generated projections, self-describing manifests, two-tool retrieval surfaces, supply-chain trust ladders, protocol adapters (MCP/ACP/A2A/OpenAI-compat) as edges not architecture. |
| [knowledge-pipelines](knowledge-pipelines.md) | Compile-at-ingest knowledge over query-time RAG: claim schemas with quotes and epistemic stance, deterministic identity and idempotent writes, cost-layered extraction, per-item LLM-call budgets. |
| [local-models-and-inference](local-models-and-inference.md) | Model operations as the differentiator: capability flags over failure-time discovery, sidecar process isolation, declarative catalogs, download UX engineering, hardware tiering/quant ladders, backend benchmarking with persisted winners. |
| [memory-architectures](memory-architectures.md) | Typed/tiered memory: injected-memory authority doctrine, relevance-ranks/strength-prunes, extract→decide formation, decay/heat/promotion math, deterministic write-time graphs, graph-walk recall. |
| [multi-agent-orchestration](multi-agent-orchestration.md) | Coordination: structured state over dialogue, typed handoff schemas, debate as a compilable macro, orthogonal-lens role panels, two-tier model economics, workers-know-their-judge. |
| [planning-and-decomposition](planning-and-decomposition.md) | The task/workflow/agent taxonomy and 4-question checklist, complexity triage before the planner, tiered template matching, spec fast lanes (never waterfall), graduated evidence-gated autonomy. |
| [product-surfaces-and-ux](product-surfaces-and-ux.md) | Agent-product UX: generative UI (fault-tolerant streaming spec parsers, registry-constrained rendering, merge-by-name patching), dashboards, decision-ready approval briefs, evidence/proof sections, refresh-on-view. |
| [security-and-guardrails](security-and-guardrails.md) | Enforcement over request: chokepoint-gated capabilities, untrusted-content fencing and role-token stripping, capability sets frozen at creation, egress policy tiers, budgets and circuit breakers as security controls. |
| [self-improvement-loops](self-improvement-loops.md) | Learning flywheels: the ratchet doctrine, traces as the universal substrate, statistical acceptance gates (median-of-N, held-out, monotonic ratchets), propose-don't-write, failure attribution before repair. |
| [skills-and-prompt-craft](skills-and-prompt-craft.md) | Skill authoring: SKILL.md anatomy and three-tier progressive disclosure, trigger-shaped descriptions, the invocation-axis taxonomy, a skill-prose failure-mode lint rulebook, token-budget economics. |
| [verification-and-judging](verification-and-judging.md) | Judging: self-grading is structurally broken, a loop's floor is its evaluator, act-capable skeptical judges executing ground truth, externalized completion, typed verdict contracts, hidden validation. |
| [workflow-engine-design](workflow-engine-design.md) | Graph/DAG engines: node taxonomies, declarative specs compiled to immutable versions, event-sourced state law, joins/retries/failure envelopes, rewind/fork/replay, mid-flight mutation, the agentic escape hatch. |

## Highest-conviction findings across the corpus

The strongest convergent findings — each appears independently in multiple topic files and multiple source systems:

- **The harness, not the model, is the lever.** A frozen frontier model improved 0.560→0.780 on pure harness edits; a team went 20%→~100% task success with no model change. Agent failures are legible configuration problems; the discipline is maximizing today's models.
- **Deterministic first, LLM last — everywhere.** Anything deterministic logic can solve never goes to a probabilistic model. The cheap-predicate → deterministic-code → LLM ordering is the single most-converged economic shape in the corpus (triggers, knowledge extraction, memory linking, template matching, verification, triage).
- **If a step is mandatory, codify it in rails, never in prompt text.** Per-step reliability compounds multiplicatively ("march of nines"); hard-coded gates, schemas, and state machines interleave with LLM steps in every production-grade system.
- **Self-report is structurally untrustworthy; completion and judgment must be external.** "A model is its own output's best defense attorney." Workers request state transitions; the engine verifies and flips them (measured: real completion 37.5%→87.5%). Judges must be independent, skepticism-calibrated, and *act* (execute, screenshot, re-run) rather than read.
- **Enforcement beats request.** Prompt guardrails are soft guidance; real security lives at execution chokepoints (egress function, install gate, tool policy, path gate). An advisory permission table nobody consults is worse than nothing.
- **Untrusted content is data, never instructions.** Everything crossing the owner's trust boundary (webhooks, fetched pages, retrieved memory, other agents' messages) gets fenced, provenance-labeled, and role-token-stripped; payloads may bind arguments to pre-declared action sets but never introduce new actions.
- **The ratchet doctrine.** Add a constraint only after a real observed failure, with provenance to that failure and a retirement path. Corollary: the right harness cannot be downloaded as a framework — it is shaped by your failure history.
- **Trust is graduated and evidence-gated, never binary.** Autonomy ladders (report-only → assisted → unattended), supply-chain tiers (builtin → official → trusted → community), and permission ladders all instantiate the same shape: start narrow, promote on measured evidence, demote on confidence drops.
- **Every non-action is a recorded, typed outcome.** Silent skips, silent fallbacks, and fail-open empty returns are the #1 defect class across the corpus; skipped fires, blocked payloads, no-op captures, and degraded paths all get reason strings and ledger records.
- **Structured, typed state is durable; prose and dialogue are ephemeral.** Typed handoffs, closed verdict enums, claim schemas with verbatim quotes, and event-sourced folds (replaying the journal reconstructs state exactly) beat free-form NL at every agent boundary.
- **One canonical source, N generated projections.** Never fork copies — generate every surface (docs, manifests, rule files, prompts) from one authoritative artifact with CI drift checks; self-description generated from the real registry cannot lie.
- **Budgets are security controls.** "Denial-of-wallet" is a named threat class; spend caps, circuit breakers, iteration/stagnation breakers, and graceful budget landings are load-bearing safety machinery, not cost hygiene.
