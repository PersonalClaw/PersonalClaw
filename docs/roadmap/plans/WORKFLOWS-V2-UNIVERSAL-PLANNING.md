# WORKFLOWS-V2-UNIVERSAL-PLANNING

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/WF2UNI.md`](../atomic/WF2UNI.md) as 12 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Universal Project Planning via Workflows v2

**Status:** DONE (backend) — sessions 40-45 shipped (PRs #178-#184, on `main`): `intent`, `matcher`,
`grounding`, `patterns`, `generation`, `contracts`, `revision`, `rigor`, `grill_protocol`,
`autonomy`, `preflight` — all reached live from `mcp_workflows.workflow_plan`, with the CI
routing-fixture gate.
**REMAINING:** `workflows/template_pipeline.py` and `workflows/eval_specs.py` have no production
importer; the `suggest_template` chat tool is unregistered; the QuestionSlider/streaming review
render, `revise{step_ref, comment}` as a resume verb, `source_session_id`, and criterion 9's
watched-scratchpad path are unbuilt. Status corrected 2026-08-04 by code audit.
(rev 2 — research-integrated 2026-07-12)

---

## Research Integration (2026-07-12)

Approved recommendation IDs folded into this revision (mechanism-level, not appended):

- **UP-R1** — Grounded from-scratch generation (grounding bundle, pattern-pick + slot-fill, schema-constrained output, repair-not-regenerate, MCP tool catalog) → *Architecture: From-Scratch Generation*
- **UP-R2** — Tiered deterministic-first template matcher, matching metadata, negatives, lighter_path, presets, router tie-breaker policy → *Architecture: Tiered Template Matcher* + *Template Structure*
- **UP-R3** — Per-stage done-means contracts, preflight, stopping-condition triple, planner altitude rule → *Stage Contracts*
- **UP-R4** — Risk-signal registry, autonomy floors, HITL/AFK typing, confirmation matrix, spend annotation, announce-block + combined commitment control → *Approval as an Autonomy Mode-Switch*
- **UP-R5** — Structured rigor:deep grill (recommended answers, facts-vs-decisions, stress-test, Step-0 schema, prohibitions) → *Mid-Planning Interrogation*
- **UP-R6** — Earned autonomy (report-only first runs, plan_mode, frame-only, read-only planning, audited auto-decisions, mid-run demotion, remembered-last-choice) → *Earned Autonomy*
- **UP-R7** — Typed merge-by-id revision patches over TTL'd draft sketches, streaming multi-view render, NO_UPDATE sentinel, plan-as-markdown-artifact → *Plan Revision Gate*
- **UP-R8** — Derived parameter schemas + extraction contract → *Template Parameterization*
- **UP-R9** — Template-creation pipeline (session mining, discover-then-freeze, suggest_template, entity scrubbing) → *User-Created Templates*
- **UP-R10** — rigor:fast + Specify (anti-waterfall), revise-spec-from-artifact → *The Rigor Axis*
- **UP-R11** — Triage gate as canonical first node, escalate-and-reclassify, impact-triage skip runs → *Triage-First Convention*
- **UP-R12** — Cheap heuristic intent classifier for rigor routing, hybrid composition, ranked alternatives → *Architecture: Intent Classifier*
- **UP-R13** — Planner eval harness (routing fixtures in CI + grounding A/B + per-template eval specs) → *Planner Eval Harness*
- **UP-R14** — Entity + topic grounding preamble → *Architecture: Grounding Preamble*
- **UP-R15** — Pattern-instantiation as a third planning mode (`kind: pattern`) → *Pattern Documents*
- **UP-R16** — Blocking vs non-blocking decision typing, Open Decisions on run summary → *Approval as an Autonomy Mode-Switch*
- **UP-R17** — Repo-context reverse pass for brownfield planning → *Architecture: Brownfield Context Pass*
- **UP-R18** — Watched scratchpad intake → *Planner Entry Surfaces*

Recon corrections applied in this revision (verified against code 2026-07-12):

- The old surfacing system's operative numbers are `workflows.match_threshold = 0.62` (config, `config/loader.py:1052`) + keyword word-overlap fallback `0.7` (`workflows/surfacing.py`); 0.55 appears only as a comment referencing vector_memory's short-text threshold and was never a skills/workflows surfacing threshold. The previous revision's 0.5/0.8 decision-flow cliffs are replaced wholesale by the tiered matcher (UP-R2).
- `context_management.py` chat plan-mode is dead code end-to-end: `OrchestrationTracker` is never constructed, and — correcting rev 1 — `extract_plan_metadata`/`rephrase_plan` are ALSO dead (their `dashboard/chat_title.py` wrappers are re-exported by `dashboard/chat.py:110-115` with `# noqa: F401` and have zero call sites). The whole plan-mode half deletes; only the subagent context-budget half (`cap_result_file`, `evict_completed_agents`, `cap_streaming_text`) stays.
- `capableModels` is a FRONTEND function (`web/src/pages/settings/ModelsPanel.tsx:43`), not a backend symbol — the planner's provider capability matrix (UP-R1 amendment) is sourced from the backend registries instead (`llm/capabilities.py`, `llm/catalog.py:infer_capabilities`, `local_models/registry.py`).
- `grill()`'s real signature is `grill(goal, shape="flat"|"tree", ask: AskFn, recall: RecallFn|None, save: SaveFn|None, assess=True)` (`grill.py`); `SaveFn = Callable[[str], None]` persists settled decisions as lessons. UP-R5's lookups are split per the memory/knowledge boundary (below).
- `run_planner_pass` (`planning/runner.py:60`) currently spawns the planner session with `trust=True` + ACP `bypassPermissions` — the opposite of read-only. UP-R6's plan-phase read-only is adapted to the REAL trust plumbing (session flags + tool stripping, the `_unattended` pattern), not a fictional env-var plan mode.
- No workflow execution engine exists today (`workflows/` is definitions + surfacing + checklist injection only; no run entity, no `run_from` op). Every engine-touching mechanism below is specified as a change to WORKFLOWS-V2's deliverables, not to existing code.

---

## Overview

Project planning evolves from "classifier picks a loop kind → kind generates bespoke phases" to a **universal planner that generates a workflow graph spec from any natural-language intent**. Whether the user says "build me a REST API", "plan my trip to Japan", "research investment strategies for retirement", or "organize the nursery renovation" — the same mechanism produces an appropriate, editable workflow spec that the Workflows v2 engine executes.

The five loop kinds' classification logic collapses into template-aware LLM planning. Domain expertise comes from the template library (bundled + user-created), not hard-coded kind strategies.

Rev 2 hardens every mechanism-level weak point the research surfaced: matching becomes deterministic-first and explainable; the unknown-domain path becomes grounded, schema-constrained, and repairable; stages carry machine-checkable done-contracts; the autonomy switch gains a risk model, floors, and history; and the rigor axis runs in both directions (fast AND deep). The soul is unchanged: personal-scale, single-user, local files, propose-don't-write.

---

## Architecture: Template-Aware LLM Planner

### The `workflow_plan` Tool (Enhanced)

The existing `workflow_plan` chat tool (from WORKFLOWS-V2.md Section 4) becomes the universal planning entry point. When the user says "help me plan X", the agent calls `workflow_plan` which now runs a five-step pipeline:

1. **Intent classification** (no-LLM heuristic) — routes rigor and stakes (UP-R12).
2. **Grounding preamble** — entity resolution + topic extraction (UP-R14), plus the brownfield context pass when a project directory is in scope (UP-R17).
3. **Tiered template matching** — deterministic-first, embedding as tie-breaker (UP-R2).
4. **Spec production** — template parameterization, pattern instantiation (UP-R15), or grounded from-scratch generation (UP-R1).
5. **Validation + review** — stage-contract lint (UP-R3), risk/autonomy annotation (UP-R4/R6), streaming multi-view review (UP-R7).

`workflow_author` (spec-in authoring) remains a separate tool — the two contracts must not be merged.

### Intent Classifier (UP-R12)

Before any matching, a **no-LLM keyword-heuristic classifier** produces a `(complexity, uncertainty, stakes, time_pressure)` tuple with its own confidence:

- Grounded in the 4-question decision checklist (ambiguity, value-vs-spend, capability reliability, cost-of-error). If the intent pre-maps to a decision tree, the planner emits a fixed sequence/branch spec with infer nodes rather than an agentic loop shape.
- **Rigor routing:** complex + high-uncertainty auto-escalates to `rigor: deep`; critical-stakes biases toward approval-gated autonomy. This answers "when does grill auto-trigger?" with one mechanism.
- The tuple is **recorded on the run** as the bucketing key LEARNING-FLYWHEEL uses for outcome learning; the classifier may later learn from corrections at runtime (adaptive-classifier pattern), but ships as pure keyword heuristics — zero token cost, offline-safe.
- Deployment bar: **≥85% routing accuracy on the fixture suite** (measured via the eval harness below).

### Grounding Preamble (UP-R14)

When the intent names a specific external entity (person, company, place, product), the planner emits a **deterministic identity-resolution action step as the first node**: one cached lookup injected into run state with a guard instruction ("use exactly this resolved identity; do not substitute unless a tool result explicitly disproves it"). The resolved identity propagates via binding to all downstream stages. Lookup failure falls back to entity-name-only context with a `degraded` flag — never a mid-graph network call. Entity-heavy domains (financial analysis, research) additionally get a "do not pattern-match narrative to an unresolved name" prohibition in worker prompts.

The preamble is two-part: **(a)** entity resolution as above; **(b)** a lightweight topic-extraction pass (one small-model call) whose output topics form the retrieval queries feeding the grill's facts-vs-decisions lookup — "understand first, then check what I already know", so retrieval is formed from a fresh reading of the goal rather than raw intent text.

**Memory vs knowledge boundary (binding, per user directive):** topic-driven lookups query TWO separate subsystems and never conflate them — **memory** (the harness's model of the user: facts/facets/episodic/lessons in `memory.db`, via `MemoryService` recall / the injected `RecallFn`) and **knowledge** (the user's personal items: documents, files, photos, notes in `knowledge.db`, via `HybridRetriever` / the `knowledge_search` tool). `knowledge_*` names in this plan always mean the knowledge store; anything the planner LEARNS (settled decisions, lessons) is written to the memory subsystem and governed by the LEARNING-FLYWHEEL plan.

### Brownfield Context Pass (UP-R17)

When `workflow_plan` targets an existing directory or project (detected from intent or explicit path), it builds a cheap context bundle BEFORE matching and generation: depth-filtered file tree (max 2 levels, common ignores) + README/docs head (capped 8k chars) + project metadata (package.json / pyproject.toml / Makefile presence). One synthesis call produces a one-paragraph "what this project is and intends" summary, **cached per `(project_id, tree-hash)`** (tree-hash change re-synthesizes; 7d TTL). This feeds the planning prompt as `CODEBASE_CONTEXT` so generated stages assume the right language, test framework, and directory conventions instead of generic scaffolding.

### Tiered Template Matcher (UP-R2)

The single embedding step (and the previous revision's hard-coded 0.5/0.8 cliffs) is replaced by a **tiered `match_template()` in `defs.py`**:

- **T1 — inverted keyword index** over a new template `keywords[]` field (deterministic, offline, auditable).
- **T2 — metadata scoring**: tags, name, description, scenario, and embedded example OUTPUTS (user intents resemble desired outputs more than descriptions).
- **T3 — intent-shape pre-classification** constraining candidates by category (uses the UP-R12 tuple).
- **T4 — embedding as tie-breaker** — the same cosine machinery as the old surfacing system (`workflows/surfacing.py`: cached `match_embedding`, config `workflows.match_threshold` default 0.62, keyword fallback gate 0.7), demoted from sole decider to tie-breaker.
- **T5 — LLM summarize-then-rematch** that RE-ENTERS the deterministic scorer — never an LLM-emitted template id. LLM-supplied names are fuzzy-resolved with a warning.

**Failure-path contract:** when the cheap-LLM tier is used it must return typed JSON `{primary, confidence, suggested_alternates}`; on parse failure, API failure, or missing model, the router **degrades to the deterministic keyword tiers with a fixed priority tiebreak** — template matching never hard-fails offline or on a flaky model.

**Every decision attaches a human-readable reason string** rendered in plan review (score_reasons-style one-line rationale). Rejected near-matches with `when_not_to_use` negatives are explained ("not for X — use Y").

**Router tie-breaker policy (adopted verbatim):** at most ONE clarifying question, and only if template choice materially changes actions; on low-risk ambiguity pick the likeliest and state the assumption in the plan; an explicitly user-named template wins unless clearly unsafe, with overrides explained.

**Hybrid composition + ranked alternatives (UP-R12):** if top templates score within 0.15, or the tuple is complex + high-uncertainty, compose 2-3 templates as subworkflows at penalized confidence instead of forcing an arbitrary winner. Plan review always shows ranked alternatives with dimension-level trade-off strings. Matcher confidence is assembled (candidate gap + classifier confidence + raw score) and clamped below 0.95.

**Lighter paths:** templates declare `lighter_path` — trivial intents route to a direct chat answer or a single `subagent_run` instead of a full run. One rung above that, templates may ship **presets** — named starter parameterizations (morning-digest / scheduled-monitor style) the matcher offers as one-click instantiations before any LLM generation.

**Schema-native surfacing:** bundled templates are additionally surfaced as typed enum options (per-template description + parameter schema) directly in `workflow_plan`'s tool JSON schema, so the calling LLM can match mechanically from schema alone. A continue-vs-spawn policy table (reuse existing session vs start fresh, keyed on overlap) makes orchestration decisions auditable at review.

### Planning Decision Flow (revised)

```
User intent arrives
    │
    ▼
Heuristic classifier → (complexity, uncertainty, stakes, time_pressure) + rigor route
    │
    ▼
Grounding preamble (entity resolve + topic extract) ── brownfield? → CODEBASE_CONTEXT
    │
    ▼
Tiered matcher T1→T5 (deterministic-first, reason string attached)
    │
    ├── Template match → preset offer → parameterize (extraction contract)
    │
    ├── Pattern document match (kind: pattern) → collaborative slot-fill dialogue
    │
    ├── Near-tie / high-uncertainty → hybrid composition (2-3 templates as subworkflows)
    │
    └── No match → grounded from-scratch generation
        (pattern-pick + slot-fill first; freeform whole-graph only as explicit fallback)
```

### From-Scratch Generation (UP-R1) — the hardened unknown-domain path

The "emit a WorkflowDef JSON" branch is the plan's single most failure-prone step and is restructured on seven mechanisms (measured basis: grounding + strict validation took first-try-valid from 0/5 to 4/5 and silent spec misses from 3 to 0, at flat wall time and +58% tokens):

1. **Grounded planner.** The planner is handed a bundled offline reference: the node taxonomy + exact action-provider signatures, orient-then-drill (index first, then only relevant provider docs). The reference bundle is **regenerated from the live registries**, not hand-written: node types from the engine's node registry, action signatures from `action_providers/registry.py`, and — per the batch-5 amendment — the user's **MCP-registered tools as first-class options** (tool name, signature, server identity, read from the mcp-tools instance store repointed onto `~/.personalclaw/mcp.json`), so a generated spec can emit action nodes targeting MCP tools with exact signatures instead of hallucinating them.
2. **Pattern-pick + slot-fill.** The DOMAIN PATTERNS prose list is promoted to a **registry of proven graph shapes** (staged-with-gates, convergent-research, fan-out-synthesis, iterative-refinement, sequential-procedure, creative-exploration, debate-macro: parallel analyst fan-out + adversarial debate + judge). The planner first classifies the intent into a shape and slot-fills it; freeform whole-graph generation is an explicit fallback, never the default.
3. **Schema-constrained emission.** When the bound model supports structured output, emit under the full workflow-spec JSON schema, typed `oneOf[WorkflowSpec, {cannot_plan: reason}]` — the planner can honestly decline instead of emitting garbage. Model support is read from the backend capability registries (`llm/capabilities.py`, `catalog.infer_capabilities`) — NOT the FE-only `capableModels`.
4. **Generated planning prompt.** The Planning Stage Prompt is generated from the node-taxonomy / action-provider / template registries instead of the rev-1 hand-written YAML block, with a numbered "hard requirements, not suggestions" constraint block placed ABOVE the intent text. The provider capability matrix (model features: structured-output support, speaker_labels, instruct-vs-thinking) feeds in as machine-readable context for parameterization.
5. **Repair, not regenerate.** Invalid specs re-prompt with a failure-mode-specific correction note, up to N repair retries before presenting. Spec validation uses shape assertions (must_match_any acceptance sets, forbidden constructs per section), not brittle exact-match.
6. **Mechanical pre-output self-check** — unique ids, gates have approvers, foreach has a binding, terminal node exists — plus a language-lock rule. Output conventions hardened: always emit the plan tag even when empty (clean exit); plan only unblocked items with an explicit blocked-by rubric; deterministic child-run naming for idempotent replanning; validate plan-referenced entities before presenting.
7. **Optional sandbox dry run.** From-scratch specs may be gated on a dry-run pass before first approval (rides the engine's dry-run posture; see UP-R6 report-only below).

### Triage-First Convention (UP-R11)

A planner convention encoded in the pattern registry and bundled templates: **generated/bundled multi-stage plans open with a triage stage** — a 2-3 tier classification whose output selects the entry subgraph and skips declared stage ranges (Small-tier work routes to a self-contained lighter prompt; binding-friendly via `{{nodes.triage.output.tier}}`).

- **Sizing axis:** the planner scales stage count and roster to classified task scale — Micro = 1-3 stages single-agent; Sprint skips discovery stages; Full = complete pipeline. Big bundled templates ship as machine-readable roster + prose doc pairs with staged activation groups (always / later-phase / as-needed) so they deploy without activating everything at once.
- **Escalate-and-reclassify** is a NAMED typed plan mutation: mid-run discovery of higher risk/ambiguity splices the previously-skipped stages ahead of the frontier (tier upgrade) instead of abandoning the run. The engine's frozen-region invariant already permits this; the plan names it so tooling and review render it.
- **Impact-triage skip runs:** for stimulus-driven plans (commit/event/file triggers via AUTOMATION-SUBSTRATE), step zero classifies the stimulus by user impact and may emit a skipped plan with a one-line rationale recorded as a **ledger-only run** — the concrete decision rule AUTOMATION-SUBSTRATE's two-weight run records need.

### Stage Contracts (UP-R3)

`workflow_plan`'s output schema gains a **per-stage sprint contract** — the reviewable artifact:

- **scope** — what this stage does
- **done_means** — machine-checkable verification: an expression, a `verify_command`, or an artifact check
- **exclusions** — "out of scope this phase" + regression risks

Per-stage approval approves the CONTRACT; `revise{step_ref, comment}` edits it; the stage's judge/gate cites exactly it — preserving the engine's ground-truth invariant (no agent certifies its own work; supervisors observe where workers write, the `effective_dir` symmetry lesson).

**Validation rules:**
- A stage lacking executable verification is flagged; a workflow lacking a machine-checkable stopping condition is rejected — **goal / verification / stopping-condition is the minimal triple**.
- Steps with no derivable check are marked "unverifiable — needs approval gate or human check" in review.
- The planner emits a **preflight step** (credentials, network, tool/binary availability — aggregated one-hop from referenced action providers) before work stages, killing the plan-approved-run-dies-at-step-1 failure class.
- **Planner altitude rule** encoded in the prompt: bold in scope, product/step level; constrain DELIVERABLES, not implementation — granular technical detail cascades errors downstream.
- For validation-type plans, `rigor: deep` adds a scenario-rigor checklist (environment fidelity, fault injection, lifecycle arcs, resource-growth assertions).

---

## Template Library Design

### Bundled Templates (shipped with the platform)

| Template | Domain | Key Pattern |
|---|---|---|
| `goal-pursuit-*` | Any convergent goal | `loop{until_dry}` + judge cycle |
| `deep-research` | Research & analysis | Multi-source search + verify + synthesize |
| `code-project` | Software development | Staged SDLC with gates |
| `design-project` | Creative/design work | Parallel exploration + evaluate + refine |
| `general-project` | Catch-all | Simple work loop with judgment |
| `checklist` | Recurring procedures | Sequence of approval-gated stages |
| `audit-sweep` | Quality/security audits | Find + dedup + verify + file |
| `trip-planning` | Travel | Research + budget + book + checklist |
| `financial-analysis` | Finance/investment | Gather data + analyze + model + recommend |
| `content-campaign` | Marketing/social media | Strategy + create + schedule + monitor |

All bundled multi-stage templates adopt the triage-first convention (UP-R11) and per-stage contracts (UP-R3).

### Template Structure (extended)

Each template has:

- `name`, `description` — identity
- `keywords[]` — the T1 inverted-index field (deterministic matching)
- `match_text` — phrases for the T4 embedding tie-breaker (semantic matching)
- `when_to_use` — trigger-only guidance, **lint-enforced against step-summaries** (no restating the steps)
- `when_not_to_use` — negatives with redirects ("not for X — use Y"); rejections explained in review
- `lighter_path` — where trivial intents route instead (chat answer / single `subagent_run`)
- `presets[]` — named starter parameterizations offered as one-click instantiations
- `examples[]` — example intents AND example OUTPUTS (metadata-scoring fodder)
- `codebase_markers` — brownfield applicability signals (file/framework markers)
- `inputs` — parameterization schema (**derived**, see Parameterization below)
- `root` — the node tree (the actual workflow structure)
- `runtime_hints` — domain intelligence that flows into prompts
- `prerequisites` — must-have inputs the grill resolves before launch
- `prohibitions` — frozen never-do boundaries injected into every stage's worker context
- `output_sections` — expected deliverable shape
- `autonomy_floor` — the minimum supervision level neither planner nor user can silently lower (UP-R4)
- `tags`, and `{id, label, icon}` picker metadata
- `kind: template | pattern` — see Pattern Documents below

### Template Parameterization (UP-R8)

Hand-maintained `inputs` blocks stop being the source of truth:

- **`resolve_unfilled_inputs()` in `defs.py`** computes each template's parameter schema as "node inputs neither bound nor defaulted". This derived schema IS the plan-review/launch form — spec edits can never drift from the form.
- Each template's parameter contract renders into the parameterize prompt as a commented type string via `template_types(template_id)`.
- The parameterize step follows the **extraction contract**: return `{extracted, missing, follow_up, all_filled}`; only user messages count as truth; latest value wins; never re-ask a declined optional; re-validate LLM output against the schema instead of trusting `all_filled`; extraction failure marks all required fields missing with `extraction_failed`.
- Planner-auto-generated values are flagged `*_auto: true` so review highlights unvetted fields; required-but-missing inputs are ASKED, never assumed (prerequisites semantics); the planner may consult a cheap machine-capability snapshot when filling model/concurrency parameters.

### Pattern Documents (UP-R15) — the third planning mode

Alongside template-match and from-scratch generation: **pattern instantiation**. The user provides (pastes, references from the knowledge store, or selects from a bundled set) an abstract pattern document — a prose description of a workflow shape with named phases and decision points but no concrete parameterization. `workflow_plan` enters a short collaborative dialogue (which modular parts apply, which conventions to follow, supervision level), then emits a concrete parameterized WorkflowDef and stores the chosen supervision/approval level as the pattern's default.

- Pattern documents are a recognized artifact kind in the template catalog (`kind: pattern` vs `kind: template`), discoverable via the same tiered matcher.
- The plan-review-with-revise-comments UX already fits the fill dialogue.
- The chosen supervision level persists so repeated use of the same pattern skips re-negotiation.
- A pattern referenced "from knowledge" is a knowledge ITEM (the user's document in `knowledge.db`) — the catalog stores the extracted pattern def; the knowledge item remains the user's source document.

### User-Created Templates (UP-R9 — the creation pipeline)

Beyond `workflow_save_as_template` (completed runs), the two highest-volume template sources stop being thrown away:

1. **Chat-session mining** — `workflow_plan` accepts `source_session_id` and mines the session transcript (`sessions/<sid>.jsonl`, resolved via `session_map.py`): observed tools, approval decisions as priors, a pre-validated permission signature — so "we just did this in chat" becomes a parameterized template one-shot.
2. **Discover-then-freeze** — every LLM-generated spec for an unknown domain persists as a **session-scoped candidate template**; subsequent similar intents load it via the tiered matcher instead of re-generating, preventing plan drift across runs. (This rides the existing workflow scope ladder — session → agent → workspace → global, `workflows/registry.py:promote_workflow` — candidates start at SESSION scope and promote up.)
3. **`suggest_template` nudge** — a local-only tool highlighting a "save as template" affordance when the agent spots a recurring-task shape, with anti-nag rules.
4. **Entity scrubbing** — generalizing a concrete run into a template scrubs entities into `{placeholder}` slots using a shared non-entity token allowlist (single point of truth between scorer and templater), so real entities become parameters while domain acronyms survive.

---

## Classification Evolution

### Old System (5 kinds)
```
"Build a REST API" → CODE
"Research market trends" → RESEARCH
"Organize my garage" → GENERAL
"Design a logo" → DESIGN
"Track my weight loss" → GOAL
```

### New System (tiered matching + grounded planning)
```
"Build a REST API"            → T1 keyword hit: code-project — reason: keywords[api,build,rest]
"Research market trends"      → T2 metadata: deep-research — reason: output-example similarity
"Organize my garage"          → no match → pattern-pick: sequential-procedure shape, slot-filled
"Design a logo"               → T1: design-project (preset: single-asset)
"Track my weight loss"        → T2: goal-pursuit-monitor — reason: monitor-shaped intent
"Fix this typo in my README"  → lighter_path: direct chat answer (no run instantiated)
"Plan a baby shower"          → no match → sequential-procedure + approval gates, from scratch
"Analyze my portfolio"        → financial-analysis, ranked alternative: deep-research (gap 0.11 → hybrid offered)
```

The classification question ("what kind is this?") is replaced by "which template/pattern fits, at which tier, for which reason?" — every routing decision carries its reason string, and if nothing fits, the planner slot-fills a proven graph shape before resorting to freeform generation. `loop/classify.py` + `loop/code_classify.py` (pure intake classifiers, injected `AskFn`) are absorbed by this pipeline.

---

## Plan Review UX

### The Flow

1. User states intent ("plan our family vacation to Japan")
2. Agent calls `workflow_plan` → the five-step pipeline runs (streaming; see below)
3. **In chat:** the review opens with the **announce-block header** (UP-R4):
   ```
   Detected:  trip-planning (T1 keyword: trip, vacation + destination entity resolved)
   Risk:      payments touched at stage 4 (booking) — risk registry hit
   Autonomy:  offered up to per-stage (floor: booking stages HITL)
   Cost:      ~14 model calls, 2 live-web stages (est. from fan-out topology)
   Pipeline:  1. Research destinations → 2. Budget [contract: totals ≤ input budget]
              → 3. GATE approve budget → 4. Book (HITL) → 5. Itinerary → 6. Checklist
   ```
   …followed by ranked alternatives with trade-off strings when matching was close.
4. User can: start (choosing autonomy/executor/environment — one combined commitment control), revise per-step, or open the graph editor.
5. **In UI:** synchronized views — plain-English per-step proposal cards + read-only graph canvas + JSON spec (JSON authoritative) — **streaming progressively while the planner runs** (buffer-append lenient re-parse, shimmer on in-flight steps). Planner-inferred parameters are marked derived-from-user-words vs inferred, surfaced as "inferred — confirm?" chips. One streamed small-model naming call returns `{title, description, per-step labels}` with deterministic fallbacks; revisions relabel only changed steps.

### The Rigor Axis (UP-R10 + UP-R5) — both directions

| Mode | Behavior |
|---|---|
| `rigor: fast` | Explicit "10-minute inferior spec, start now": skips interrogation, starts immediately, **auto-schedules a spec-refinement gate after the first stage output** — refinement happens against a built artifact instead of up-front guessing |
| **Specify** | One-click single-intent rewrite: an aux model fleshes a rough intent into a runnable one-stage spec |
| `rigor: standard` | Default: grounding preamble + matcher + contracts, no interrogation |
| `rigor: deep` | The structured grill (below); auto-triggered by the intent classifier (complex + high-uncertainty) or by any risk-registry hit |

Plan revision gains **revise-spec-from-artifact**: run output + user reaction feed back into the spec, and each fixed defect can append to the plan's acceptance criteria (append-only ratchet). Spec-driven planning must not become a new waterfall — the fast end exists precisely for exploratory tasks where starting early is cheapest.

### Mid-Planning Interrogation (`rigor: deep` — the structured grill, UP-R5)

The free-form clarification gate is replaced by a spec'd protocol, absorbing the existing `grill.py` pipeline (assess_goal → check_memory → decompose → save_decisions) as the planner's deep-rigor machinery:

1. **Every question ships WITH the planner's recommended answer** — what makes deep grilling fast, not tedious.
2. **Facts-vs-decisions split** — discoverable facts are LOOKED UP, not asked: codebase facts via the brownfield context pass; user-item facts via the **knowledge store** (`HybridRetriever` / `knowledge_search`); harness-known facts via **memory recall** (the `RecallFn` injected into `grill()`, backed by `MemoryService`). Only genuine decisions are asked. (Two subsystems, never conflated — see the boundary note above.)
3. **Adaptive pacing** — ≥3 independent load-bearing decisions → one batched structured round (≤8 typed question objects, `choice[]` with 2-5 options + mandatory "Other", inline-reply parsing); dependent questions fall back to one-per-turn. In the UI, questions render as a QuestionSlider/`ask()` stepper widget: 1-5 questions of typed kinds (text-options, slider, freeform), one-at-a-time with gated forward navigation and a per-question custom-answer escape hatch; single Submit returns a typed answer record that directly parameterizes the template.
4. **Stress-test phase** — after scoping, 2-3 adversarial scenario probes generated from the user's stated constraints; stated-vs-revealed contradictions feed back into the plan BEFORE spec emission.
5. **Step-0 output schema** — confirmed requirements / inferred assumptions / open questions, with "never treat a guess as a requirement"; open questions are blockers, rendered in review.
6. **Boundary capture** — every round includes a Stop/never-do question whose answers persist as a frozen `prohibitions` block injected into every stage's worker context; templates gain `prerequisites` + `prohibitions` + `output_sections` blocks (see Template Structure).
7. **Persistence** — all Q+A pairs persist to the run/project decision log, and settled decisions save as lessons via the existing `SaveFn` seam (`grill.py`: `SaveFn = Callable[[str], None]` → `write_lesson` → `lesson.*` rows in **memory.db** — memory subsystem, LEARNING-FLYWHEEL's domain), so no question is asked twice.
8. **Shared-understanding confirmation** gates spec emission.
9. For evaluation-bearing or unfamiliar-domain intents, the planner emits a **domain_spec artifact** (success metrics, held-out checks, leakage risks, budget) as a reviewable step BEFORE generating the workflow spec — pre-registration as a scaffolded artifact whose checks bind into gate nodes.

The conversational `grill` chat skill stays untouched (it's a chat style, not an engine).

### Plan Revision Gate (UP-R7 — mechanism for the walkthrough's best interaction)

The loop plan-walkthrough's one genuinely superior interaction — per-step threaded comments driving targeted re-drafts (`planning/session.py`: `comment_step` flips `awaiting_review → running` on exactly one step) — is preserved and finally given a real mechanism:

1. **Typed merge-by-id patches.** Revision runs in editMode: the LLM emits ONLY changed steps merged by node id (same-id replaces, new adds, absent preserved) — untouched steps' parameterization can never drift (~60 vs ~400 tokens). A **NO_UPDATE sentinel fast-path** applies between nodes: the revising LLM emits either the literal sentinel (no parse, no cost) or a typed mutation set — never a free rewrite. Reviewer diffs adopt insertion-only semantics where sensible: revision adds attributed "Phase N.5" steps rather than rewriting originals, giving revise-comments clean merge semantics and provenance. Vertical-slice phasing (every phase crosses all affected layers) is encoded as a planner rule.
2. **TTL'd draft sketches.** Drafts are ephemeral sketches (sketch_create/sketch_promote): auto-GC'd if never approved; approval atomically promotes to a WorkflowRun/template. Revisions stage into the draft with If-Match optimistic concurrency; committed epochs only read the committed spec (frozen-region safe).
3. **Plan-as-artifact.** The plan's prose view is ALSO persisted as a markdown artifact in a known per-project location (via the artifacts registry's native FS provider, `~/.personalclaw/artifacts/` — versioned, evented), with `revise{step_ref, comment}` renderable as line-anchored comments on it — revisions become diffable, the approve-what-runs guarantee is inspectable as a file, and abandoned drafts are recoverable from disk (the sketch TTL/GC story applies to the artifact too).
4. **Mid-flight template switches** carry prior node outputs into the new template's entry node instead of restarting.

`workflow_resume`'s answer grammar gains `revise{step_ref, comment}`, which re-invokes the planner scoped to that ONE step — span-scoped editing, the Canvas contract applied to plans.

### Approval as an Autonomy Mode-Switch (UP-R4 + UP-R16)

Approving a plan is not a boolean. The approval gate offers **run unattended / per-stage approval / first stage only / frame-only (see Earned Autonomy) / keep planning / edit spec** — but the offer is now governed by a risk model:

1. **Risk-signal registry.** ONE canonical registry file (destructive ops, external writes, credentials/payments, schedule creation), cited by-reference from planner and templates. Any hit forces `rigor: deep`, caps offered autonomy, and a conflicting user-requested unattended mode surfaces exactly ONE informed-consent question — never silent honor, never silent upgrade.
2. **Autonomy floors.** Templates declare an `autonomy_floor` neither planner nor user can silently lower.
3. **HITL/AFK node typing.** Plan nodes are typed HITL vs AFK at plan time; autonomy modes DERIVE from node attention types (unattended runs still stop at HITL-typed nodes), compiling to a `require_hitl` flag on stage nodes as the single uniform engine target.
4. **Confirmation policy matrix** — `(ConfirmationType × RiskLevel × mode)`: unattended auto-approves everything except `is_destructive`; per-stage auto-approves read-only stages. One typed async resolve-by-id `ConfirmationRequest` entity carries all of it.
5. **Spend annotation.** Each step annotated `spend: none|cached|live`; plan review shows an estimated call/cost figure from fan-out topology, plus **per-template historical p50/p95 token/cost from the Run Ledger** before approval.
6. **Announce-block header** (shown in The Flow) ends in **one combined commitment control**: autonomy mode, executor/model choice (the planning agent explicitly MAY differ from the implementing agent), and execution environment (local / worktree / sandbox) stamped together on the run at approval time — three choices, one gate.
7. **Interrupt taxonomy for unattended mode:** exactly three conditions interrupt — irreversible/high-risk actions; uninferable credentials/product decisions; conflicting requirements. All other ambiguity proceeds with a journaled assumption.
8. **Cost-of-error drives defaults:** steps with `verify_command` gates default toward unattended; high-stakes + hard-to-verify steps default to per_stage + read-only tools posture.
9. **Permission pre-approval:** plan approval pre-approves the permissions the plan implies — plan-referenced tools become session allow-rules, removing redundant re-prompts mid-run.
10. **Blocking vs non-blocking decisions (UP-R16).** The planner types every decision node: **blocking needs-input** (pauses the run, enters the needs-input inbox) vs **non-blocking open-decision** (never pauses; lands as a structured "Open Decisions" section on the completed run's summary, answerable retroactively). Auto-classification rule: decisions whose output feeds a downstream binding are blocking; ambiguity that doesn't change the execution path is non-blocking; genuine forks and destructive-action approvals are always blocking. Non-blocking decisions answered post-run can trigger a scoped re-run from the affected node via the v2 engine's rewind/run-from op. Plan review marks each decision node's severity.

The chosen autonomy level is stamped on the run at commitment time and drives the engine's gate-injection behavior — subject to the trust plumbing invariant: a worker session needs ALL THREE of `session._trust=True` + `set_approval_policy(key, "auto")` + `session._unattended` (and ACP `bypassPermissions` when unattended), or runs stall on approvals (recon gotcha; `loop/manager.py:start` is the reference implementation).

### Earned Autonomy (UP-R6)

Per-run autonomy converts into **per-template earned trust**:

1. **Report-only first runs.** The first run of any NEW template defaults to report-only: side-effecting actions are PROPOSED into run state, not executed (the propose-don't-write soul, applied to execution). Promotion toward unattended is SUGGESTED only after N verified successes per template. Earned-trust state gains a **remembered-last-choice tier**: the approval dialog defaults to what the user chose last time for this template/executor pair instead of resetting each run.
2. **`plan_mode: fixed | dynamic | rolling`.** Template-matched plans are `fixed` (graph frozen by default; mid-flight mutation needs explicit unlock); scratch-generated plans are `dynamic`; long-horizon plans are `rolling` with re-plan checkpoints. A "one-time answer or a view that stays fresh?" persistent-view intent check plus the stable-goal heuristic gates recurring persistence (hands off to AUTOMATION-SUBSTRATE for the recurring case).
3. **Frame-only mode.** A fourth autonomy mode: analysis nodes run autonomously but every decision-type gate presents framed options and hard-stops for the human choice — "frame decisions, never make them." The autonomy ceiling users want for high-stakes personal domains (finances).
4. **Plan-phase read-only — the planner layer's OWN guarantee.** The planning phase runs read-only, enforced by the planner layer for EVERY executor (native and ACP), deferring to native plan modes only as an optimization, never a dependency. Concretely: the planner session is built with a read-only tools posture (the same session-flag + tool-stripping seam `_unattended` uses today, inverted to strip WRITE tools) — replacing `run_planner_pass`'s current `trust=True` + `bypassPermissions` posture, which is the opposite of read-only.
5. **Audited auto-decisions.** Unattended mode encodes "auto-decide replaces judgment, not analysis": options + choice + rationale recorded per auto-decided step in the Run Ledger; plan-lint flags analysis compression. Approval interactions use the richer request spec (what / why / what-could-go-wrong / if-approved / if-denied; answers: approve / deny / modify / defer / always-allow-narrow) as durable resume tokens; every decision logged for LEARNING-FLYWHEEL.
6. **Mid-run demotion.** Autonomy is demotable mid-run: an unattended run drops to per-stage approval when a gate/judge confidence score falls below threshold — graceful degradation to human-in-the-loop rather than autonomy fixed for the whole run at approval time.

---

## Planner Entry Surfaces

Three ways intents reach the planner:

1. **Chat intent** — the primary path (`workflow_plan` tool call), as above.
2. **Stimulus triggers** — commit/event/file triggers via AUTOMATION-SUBSTRATE open with the impact-triage step (UP-R11) and may emit ledger-only skip runs.
3. **Watched scratchpad (UP-R18)** — a watched local "daily page"/scratchpad file (or designated inbox note) that a periodic scan converts from unstructured text (unchecked todos, jotted intents) into candidate planning inputs. Each detected actionable line runs through the triage gate + the intent classifier exactly like a chat intent, but lands as a **PROPOSED plan/task in the needs-input inbox — never auto-executed** — with a backlink to the source line. Dedup by content-hash + seen-line tracking (a line never proposes twice); checked/struck lines are ignored. The scan is a plain automation trigger (file-watch or interval) riding AUTOMATION-SUBSTRATE with no new machinery; proposals surface through the existing `InboxService`. The barrier drops from "create a task" to "write it down" — ambient capture inside the propose-don't-write guardrail, local files only.

---

## Domain-Specific Examples

### Trip Planning (no bundled template — grounded generation)

Note the rev-2 additions visible in the spec: the entity/preamble node, per-stage `done_means` contracts, HITL typing on the booking stage, and spend annotations.

```yaml
name: japan-trip-2027
plan_mode: dynamic          # scratch-generated
inputs:
  destination: "Japan"      # entity-resolved in preamble
  dates: "March 15-28, 2027"
  travelers: 2
  budget: "$8000"
root:
  kind: sequence
  children:
    - id: preflight
      kind: action
      label: "Preflight: web search reachable, calendar tool available"

    - id: ground
      kind: action
      label: "Resolve destination entity + extract topics"
      spend: cached

    - id: research
      kind: parallel
      join: all
      children:
        - kind: stage
          label: "Research destinations"
          prompt: "Research top destinations in Japan for March (cherry blossom season)"
          schema: {destinations: [{city: string, highlights: [string], days_recommended: int}]}
          done_means: "≥5 destinations with days_recommended summing to trip length ±3"
          spend: live
        - kind: stage
          label: "Research logistics"
          prompt: "Research Japan travel logistics: JR pass, SIM cards, money, etiquette"
          schema: {logistics: [{topic: string, recommendation: string, cost: string}]}
          done_means: "covers transit, connectivity, money, etiquette"
          spend: live

    - id: budget
      kind: stage
      label: "Create budget breakdown"
      prompt: "Create detailed budget for {{inputs.travelers}} travelers, {{inputs.budget}} total"
      schema: {categories: [{name: string, amount: number}], buffer: number}
      done_means: "sum(categories.amount) + buffer == inputs.budget"
      exclusions: "no bookings this phase"

    - id: approve-plan
      kind: gate
      gate_kind: approval
      decision: blocking      # output feeds booking bindings
      prompt: "Review destinations and budget. Approve to proceed with bookings?"

    - id: book
      kind: parallel
      join: all
      require_hitl: true      # payments — risk-registry hit; unattended still stops here
      children:
        - kind: stage
          label: "Find flights"
          prompt: "Search for flights to Japan for {{inputs.dates}}"
          schema: {options: [{airline: string, price: number, duration: string}]}
          spend: live
        - kind: stage
          label: "Find accommodation"
          prompt: "Find hotels/ryokans for the itinerary"
          schema: {options: [{city: string, name: string, price_per_night: number}]}
          spend: live

    - id: itinerary
      kind: stage
      label: "Create day-by-day itinerary"
      prompt: "Create complete itinerary combining destinations, logistics, bookings"
      schema: {days: [{date: string, city: string, activities: [string], notes: string}]}
      done_means: "one entry per trip day; every booked item appears"

    - id: checklist
      kind: stage
      label: "Pre-trip checklist"
      prompt: "Create a pre-trip preparation checklist (visa, packing, reservations)"
      schema: {items: [{task: string, deadline: string, done: boolean}]}
```

### Financial Analysis (bundled template)

Unchanged in structure from rev 1 (gather-foreach → three-scenario parallel → synthesize), with rev-2 template metadata added: `keywords: [portfolio, invest, refinance, analyze, financial]`, `when_not_to_use: "not for bookkeeping/tax filing — use checklist"`, `autonomy_floor: frame-only` (financial decisions are framed, never made — UP-R6), `prohibitions` seeded by the grill (e.g. "never assume a 60/40 fallback allocation"), and an entity-grounding preamble node when a specific ticker/company/asset is named (UP-R14).

```yaml
name: financial-analysis
description: "Analyze financial data, model scenarios, and produce recommendations"
kind: template
keywords: [portfolio, invest, refinance, analyze, financial, mortgage]
when_to_use: "user wants analysis + recommendation over financial data or a money decision"
when_not_to_use: "not for bookkeeping or tax filing — use checklist"
autonomy_floor: frame-only
inputs:            # derived via resolve_unfilled_inputs(); shown here for readability
  topic: {type: string, required: true, help: "What to analyze"}
  data_sources: {type: array, default: [], help: "URLs or file paths to financial data"}
  risk_tolerance: {type: string, default: "moderate"}
root:
  kind: sequence
  children:
    - id: gather
      kind: foreach
      items: "{{inputs.data_sources}}"
      max_concurrency: 3
      body:
        kind: stage
        label: "Analyze: {{item}}"
        prompt: "Extract and analyze financial data from {{item}}"
        schema: {metrics: object, trends: [string], risks: [string]}

    - id: model
      kind: parallel
      join: all
      children:
        - kind: stage
          label: "Bull case"
          prompt: "Model optimistic scenario given: {{nodes.gather.output | json}}"
          schema: {projection: object, assumptions: [string], probability: number}
        - kind: stage
          label: "Base case"
          prompt: "Model base/expected scenario"
          schema: {projection: object, assumptions: [string], probability: number}
        - kind: stage
          label: "Bear case"
          prompt: "Model pessimistic scenario"
          schema: {projection: object, assumptions: [string], probability: number}

    - id: recommend
      kind: gate
      gate_kind: decision      # frame-only: options framed, human chooses
      decision: blocking
      prompt: |
        Given risk tolerance: {{inputs.risk_tolerance}}
        Scenarios: {{nodes.model.output | json}}
        Frame 2-4 concrete options with rationale, risk, and trade-offs. Do NOT choose.
      effort: high
```

---

## Planner Eval Harness (UP-R13)

Session 1's "test on 10 diverse intents" becomes a permanent quality gate:

1. **Routing fixtures as CI contract tests.** An evals file of `{intent_text, expected_template, expected_rigor}` fixtures asserted STRUCTURALLY in CI against the template registry — template ids exist, rigor enum valid, deterministic tiers (T1-T3) map each fixture exactly, **no LLM at test time**. Seeded from this plan's own Classification Evolution examples and Success Criteria. This catches silent drift between template metadata, deterministic matching tiers, and documented routing — the exact failure mode that made the old loop classifiers untrustworthy.
2. **Grounding A/B as `workflow_plan`'s acceptance test.** ~5 representative planning tasks run with/without the grounding bundle, scored on **first-try-valid rate** (cheap, automatic) and **silent spec misses** (LLM/judge-graded) as SEPARATE metrics — separating validation failures from silent misses is what makes the eval actionable.
3. **Per-template eval specs.** Each template artifact mechanically derives a template-specific benchmark (representative fixture intents + expected parameterization + acceptance checks) via pure functions — one declarative artifact compiles into BOTH the runnable plan AND its eval-suite config, giving LEARNING-FLYWHEEL's template-diff proposals something concrete to gate on. Routing fixtures alone don't cover per-template plan quality.
4. The intent classifier's **≥85% routing-accuracy target** on the fixture suite is the deployment bar (UP-R12).

Near-zero maintenance cost — appropriate for a single-user project.

---

## Planning Surfaces Collapsed by This Plan

This plan is also the retirement path for PClaw's three parallel planning mechanisms (identified in the 2026-07-11 orchestration sweep, re-verified by recon 2026-07-12):

| Surface | Fate |
|---|---|
| Legacy chat plan-mode (`context_management.py`: OrchestrationTracker, looks_like_plan, validate_plan_format, PLAN_TEMPLATE, append_plan_event, plan_memory) | **DELETE — the whole plan-mode half.** Tracker never constructed (`dashboard/state.py:351` sets `_orch_tracker = None`, nothing else touches it); *correction vs rev 1:* `extract_plan_metadata`/`rephrase_plan` are ALSO dead — their `chat_title.py` wrappers are re-exported (`dashboard/chat.py:110-115`, `# noqa: F401`) with zero call sites. Keep ONLY the live subagent context-budget half (`cap_result_file`, `evict_completed_agents`, `cap_streaming_text`, size constants). Can be deleted TODAY, independent of everything else. |
| `planning/` module (runner.py, session.py) + `loop/plan_walkthrough.py` + `loop/*_plan_briefs.py` | **ABSORB → delete with loops** — `run_planner_pass` ≡ a `stage` node with `schema` (structured-output replaces the fragile `plan_steps.json`/`step_artifact.json` sentinel-file polling); the `PlanStep` pending→running→awaiting_review→approved gate flow ≡ `gate{approval}` + the UP-R7 revision mechanism (`comment_step`'s awaiting_review→running re-draft IS `revise{step_ref, comment}`); `edit_artifact` ≡ `workflow_edit` ops. The sentinel-cleanup invariant (sentinels land in the user's repo when workspace-bound; must clear pre+post) dies with the sentinels. Deleted when LOOPS-EVOLUTION drains. |
| `grill.py` engine | **ABSORB** into the planner's `rigor: deep` protocol (UP-R5): `AskFn` → the typed question rounds; `RecallFn` → the facts-vs-decisions memory lookup; `SaveFn` (`Callable[[str], None]` → lessons) → decision-log + lesson persistence, tree-shaped decompositions saved as session-scoped candidate workflow defs (UP-R9's discover-then-freeze). The conversational `grill` chat *skill* stays untouched. |
| Loop classifiers (`loop/classify.py`, `code_classify.py`) | **ABSORB** into the intent classifier + tiered matcher (this plan's core). Their fixture-worthy example intents seed the eval harness. |

---

## Provider & Config Integration

Where each new piece plugs into the pluggable-provider architecture (nothing here invents a parallel mechanism):

| Piece | Seam |
|---|---|
| Templates / patterns / candidate templates | Workflow defs under the existing **workflow provider** family (`workflows/registry.py` `_providers`, native provider's markdown-first store, scope ladder session→agent→workspace→global). Apps can ship template packs by contributing a `type: workflow` provider (manifest `provider.type` → WorkflowTypeHandler → `workflows/registry`). |
| `workflow_plan` / `workflow_save_as_template` / `suggest_template` tools | The existing **tool-provider category** route: `mcp_workflows.py` module (already in `mcp_core._TOOL_MODULES` via `tool_providers/registry.py:71-99`) gains the new tools — no new registration mechanism. |
| Planner LLM calls (matching T5, generation, naming, grill) | Resolved via `providers/use_cases.py` — generation on the **`planning` chat sub-category**, judge/verification on **`reasoning`**, naming on **`background`**; all through `one_shot_completion` (which maps to plain ModelProviders, never the native runtime). No direct provider imports. |
| Grounding bundle's action catalog | Regenerated from `action_providers/registry.py` (the 9 allowlisted providers) + the MCP instance store (`~/.personalclaw/mcp.json` via `providers/mcp_instances.py`). **Invariant:** any NEW action provider this program ships must be added to `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`) or hook create/update rejects it. (This plan ships none; the scratchpad scan uses existing triggers + `run-workflow`.) |
| Provider capability matrix | Backend registries only: `llm/capabilities.py`, `llm/catalog.py:infer_capabilities`, `local_models/registry.py`. (`capableModels` is FE-only — recon correction.) |
| Risk-signal registry | One bundled data file in core (like `loop/sdlc_meta.py`'s ladders), cited by-reference; NOT a provider — it is the validation layer's input. |
| Watched scratchpad scan | A plain AUTOMATION-SUBSTRATE trigger (file-watch/interval) whose action is the existing `run-workflow`/`run-prompt` action providers; proposals land via `InboxService` (the native push source). No new provider type. |
| Plan prose artifact | The **artifacts registry** (`artifacts/registry.py`, native FS provider) — named, versioned, evented. Distinct from knowledge items (user documents) and from ingestion-pipeline "orphaned artifacts" (derived media files — a known naming collision to keep straight). |
| Run Ledger fields (`autonomy`, `plan_mode`, classifier tuple, auto-decision log, earned-trust counters, remembered-last-choice) | Columns/JSON fields on the v2 `WorkflowRun` entity + Run Ledger (WORKFLOWS-V2.md data model) — engine-owned, not config. |
| New config keys (`planning.default_rigor`, `planning.report_only_first_runs`, `planning.scratchpad_path`, `workflows.match_threshold` reuse) | The **FOUR wiring points**, every time: dataclass field with `_meta(label, help)` + `AppConfig.load` field mapping + `to_dict` serialization + PATCH `_EDITABLE_CONFIG` allowlist (the config-flag-two-maps→four-maps gotcha). |
| New SSE events (plan streaming, revision, confirmation requests, autonomy demotion) | Added to the FE `RUN_LIFECYCLE` union in `web/src/pages/loops/useRunStream.ts` — EventSource DROPS unregistered event types (recon gotcha 9). |
| ConfirmationRequest entity | Engine-owned typed entity resolved by id over the existing dashboard WS/needs-input inbox surfaces — same channel loop `needs_input` uses today. |

---

## Changes to WORKFLOWS-V2.md

1. **`workflow_plan` tool (planner contract):** v2 Slice 3 ships `workflow_plan(goal, rigor?, template?)` template-UNAWARE (LLM-only generation); this plan upgrades it to the five-step pipeline (classifier → preamble → tiered matcher → production → validated review) and adds `source_session_id` (UP-R9) and pattern mode (UP-R15). `workflow_author` stays separate.
2. **Tiered matching in the engine:** `defs.py` gains `match_template(intent, catalog) -> (candidates ranked, reason strings)` implementing T1-T5; the T4 tier reuses the old surfacing system's embedding+keyword machinery with its REAL parameters (`workflows.match_threshold` 0.62 default + keyword 0.7) as a tie-breaker, not a decider. `defs.py` also gains `resolve_unfilled_inputs()` + `template_types()` (UP-R8).
3. **New chat tools:** `workflow_save_as_template` (+ entity scrubbing), `suggest_template` (local-only nudge).
4. **`workflow_resume` answer grammar:** `revise{step_ref, comment}` (typed merge-by-id patch semantics, NO_UPDATE sentinel), plus the richer approval request spec (approve/deny/modify/defer/always-allow-narrow).
5. **Run fields:** `WorkflowRun.autonomy: unattended|per_stage|first_only|frame_only|report_only`, `plan_mode: fixed|dynamic|rolling`, `require_hitl` on stage nodes, `decision: blocking|open` on gate nodes, executor/environment stamp, classifier tuple, spend annotations, auto-decision log entries in the Run Ledger.
6. **Engine ops:** escalate-and-reclassify as a named typed mutation (frontier splice of skipped stages); scoped run-from for post-run open-decision answers; mid-run autonomy demotion hook off gate/judge confidence.
7. **Draft sketches:** TTL'd plan drafts with If-Match concurrency + atomic promote (UP-R7), prose view mirrored to an artifact.
8. **Validation layer:** stage-contract lint (minimal triple, unverifiable-step flags), shape assertions, risk-registry citation, preflight synthesis.

---

## Implementation Effort

- **6 sessions** (after Workflows v2 Slices 0-3 + Loop Evolution templates); was 3 pre-integration — the added scope is mechanism depth on the same surfaces, so sessions stay cohesive.
- **Session 1 — Matching + classification:** intent classifier (tuple + rigor routing); tiered `match_template()` T1-T5 with reason strings, negatives, lighter_path, presets, failure-path degradation; template metadata extensions; routing-fixture eval file in CI (UP-R13.1). *Also: delete dead chat plan-mode (`context_management.py` split — the whole plan-mode half incl. the dead chat_title wrappers) — zero dependencies, front-runs everything.*
- **Session 2 — Grounded generation:** grounding bundle regeneration from live registries (node taxonomy, action providers, MCP tools, capability matrix); pattern-shape registry + slot-fill; schema-constrained `oneOf` emission; repair-not-regenerate; self-check; grounding A/B harness (UP-R13.2); brownfield context pass; entity/topic preamble.
- **Session 3 — Contracts + parameterization:** per-stage done-means contracts + validation lint + preflight synthesis + altitude rule; `resolve_unfilled_inputs()` + extraction contract; triage-first convention + escalate-and-reclassify op; blocking/open decision typing.
- **Session 4 — Review + revision:** streaming multi-view review (announce-block, ranked alternatives, inferred-chips, naming call); typed merge-by-id revision + NO_UPDATE sentinel + TTL'd sketches + plan-as-artifact; `revise{step_ref, comment}`; new SSE events into `RUN_LIFECYCLE`.
- **Session 5 — Autonomy + risk:** risk-signal registry; autonomy floors; HITL/AFK typing → `require_hitl`; confirmation matrix + ConfirmationRequest entity; combined commitment control; earned trust (report-only first runs, promotion counters, remembered-last-choice); plan_mode; frame-only; planner read-only posture (replacing `run_planner_pass`'s trust=True); audited auto-decisions + mid-run demotion.
- **Session 6 — Grill + entry surfaces + template pipeline:** structured `rigor: deep` protocol (question rounds w/ recommended answers, facts-vs-decisions split across memory AND knowledge lookups, stress-test, Step-0 schema, prohibitions, SaveFn persistence, QuestionSlider widget); rigor:fast + Specify + revise-spec-from-artifact; session mining + discover-then-freeze + suggest_template + entity scrubbing; watched scratchpad intake; per-template eval specs (UP-R13.3); unknown-domain validation sweep.

## Dependencies

- Template library (from LOOPS-EVOLUTION plan) must exist for matching to work
- `workflow_plan` tool v1 (Slice 3) must be operational
- Binding expression resolver (Slice 0) for template parameterization + `resolve_unfilled_inputs()`
- Run Ledger (WORKFLOWS-V2 acceptance criteria) for spend history, auto-decision audit, and earned-trust counters
- AUTOMATION-SUBSTRATE triggers for the scratchpad scan and stimulus-driven entry (entry surfaces 2-3 degrade gracefully without it)
- LEARNING-FLYWHEEL consumes (not blocks): classifier tuples, decision logs, per-template eval specs
- Dead-code deletion (chat plan-mode) has NO dependencies — can front-run everything

## Risks

- **Planner over-machinery for a single user.** Mitigation: rigor:fast + Specify + lighter_path + presets keep the cheap paths cheapest; every heavyweight mechanism (grill, contracts, risk gates) is entered only by classifier/risk escalation, never by default.
- **Deterministic tiers drift from template metadata.** Mitigation: the CI routing fixtures assert tier-exact mappings with no LLM (UP-R13.1); lint enforces `when_to_use` against step-summaries.
- **Autonomy machinery contradicts the engine's trust plumbing.** Mitigation: everything compiles down to the ONE uniform target (`require_hitl` + the three-fold session trust flags); the confirmation matrix is evaluated engine-side, not per-executor.
- **Grill lookups blur the memory/knowledge boundary.** Mitigation: the boundary is stated normatively in this plan (Grounding Preamble); facts-vs-decisions lookup code takes two explicitly separate callables (memory recall, knowledge search) — never a merged "context fetch".
- **Plan-as-artifact vs knowledge-item confusion.** Mitigation: prose plans go to the artifacts registry only; knowledge.db is never written by the planner.

## Success Criteria

1. "Build me a REST API with auth" → T1-matches `code-project`, parameterizes via the extraction contract with zero drift from the derived schema, runs to completion; every stage carries a done-means contract the gate cites.
2. "Plan our family trip to Japan in March" → grounded generation produces a first-try-valid spec (schema-constrained, self-checked) with entity preamble, preflight, and HITL-typed booking stages; runs successfully.
3. "Help me analyze whether to refinance my mortgage" → financial-analysis at `autonomy_floor: frame-only`: analysis runs autonomously, the decision is framed with options and hard-stops for the user.
4. "I want to organize a garage sale" → pattern-pick slot-fills a sequential-procedure shape (inventory, pricing, advertising, day-of checklist) despite no template; the spec validates first try or repairs within N retries.
5. "Fix this typo" → lighter_path answers in chat; no run is instantiated.
6. User can revise the generated plan per-step before starting ("add a stage for checking weather forecasts") via `revise{step_ref, comment}` — only the touched step re-drafts; the approved prose artifact on disk matches exactly what runs.
7. First run of a newly saved template executes report-only; after N verified successes the approval dialog suggests promotion, and remembers the user's last choice for that template.
8. The CI routing-fixture suite passes with ≥85% deterministic-tier accuracy and zero LLM calls; the grounding A/B shows first-try-valid ≥4/5 on the representative task set.
9. A jotted line in the watched scratchpad appears as a PROPOSED plan in the needs-input inbox with a source backlink — and is never auto-executed.

## Execution log

- **2026-08-02 — CODE DONE (push blocked) — Matching + classification (session 40 of the WF2 queue).**
  Branch `feature-wf2-planning-match`. `workflows/intent.py` (no-LLM 4-dimension classifier + rigor
  routing), `workflows/matcher.py` (T1-T5), typed match metadata on `DefMetadata`, all 18 bundled
  templates annotated, `tests/fixtures/planner_routing.json` as the CI gate, both wired into
  `workflow_plan`. 12251 tests.

- **The accuracy number was earned, not asserted.** Fixtures were written FIRST from how a user
  actually types, then measured: **68% on first contact** against the plan's 85% bar. One root cause —
  a request with NO signal collapsed to TRIVIAL, so "add a retry to the ingest queue" took the
  cheapest path. Absence of evidence is not evidence of simplicity. Four measure-fix rounds reached
  100% on fixtures, 4/4 shapes, and 13/13 against the REAL bundled library.

- **DISCOVERY — nine classifier defects**, each a measured miss: TRIVIAL was unreachable (required an
  explicit certainty word that nothing emits); time pressure was checked after stakes, so an outage
  routed DEEP; `quick` read as urgency when it is a size; `delete`/`drop` sat in both the stakes and
  irreversible lists and HIGH wins ties, making the `scratch` de-escalator unreachable; a signal-less
  MEDIUM both blocked the cheap paths and read as not-complex; high stakes short-circuited to FAST so
  a changelog got less planning than a retry; breadth was not a signal, so a codebase-wide rename read
  as a one-liner; a domain noun counted as a unit of scale; and the uncertainty list lacked the bare
  "why".

- **DISCOVERY — seven matcher defects.** `DefMetadata` is a CLOSED dataclass whose `from_dict` drops
  unnamed keys, so annotating 18 templates left the matcher reading 0/18 while still reporting
  matches — the match surface is now typed fields. `TemplateProfile.from_def` called `.get()` on that
  dataclass and silently got nothing. Confidence saturated at the ceiling for every clean match (a
  number that never varies carries no information). The T3 shape penalty could not unseat a keyword
  hit, so a monitor intent matched a review template. Hard-excluding every candidate crashed.
  Literal-phrase keyword matching missed "why did that run fail" against `"why did it fail"`. And the
  matcher read templates from DISK while the plan tool resolved them through registered providers —
  outside a booted gateway the router proposed a name the loader could not find and turned a working
  scaffold into an error.

- **DEVIATION — the "delete dead chat plan-mode" task is not actionable as written.** That split
  already happened: the plan-mode half is `plan_memory.py`, whose own docstring says it becomes
  deletable once this plan replaces the format. It is NOT dead — `history.py` (2 sites) and
  `dashboard/chat_title.py` import it live, with test coverage. Deleting it now would break three
  live surfaces to front-run a replacement that does not exist until session 41.
  **↳ SUPERSEDED 2026-08-12: `src/personalclaw/plan_memory.py` no longer exists** and nothing
  imports it (0 hits across `src/`), so this half of `WF2UNI-12`'s `done_when` is already
  satisfied and its `scope` note ("plan_memory still imported live") is stale. The REST of that
  atom is unchanged and still not startable: `planning/runner.py`, `planning/session.py`,
  `loop/plan_walkthrough.py`, `loop/classify.py`, `loop/code_classify.py` and four
  `loop/*_plan_briefs.py` all still exist with live importers, which is exactly what the atom's
  `EXT:LOOPS-EVOLUTION:loop drain / retirement` dependency is about.

- **Validated end to end** in the real runtime: audit intent → `audit-sweep` @0.79 (T3), "why did
  that run fail" → `diagnose-run` @0.59 (T1), synthesis intent → `knowledge-synthesis` @0.80, each
  returning the template's expanded tree plus steering examples. A monitor-shaped intent correctly
  reports that no template serves it (session 39 deferred `market-monitor` for want of `net.fetch`)
  and falls back to a scaffold rather than forcing a wrong shape.

- **NOT DONE:** T4 (embedding tie-break) and T5 (summarize-then-rematch) are built and unit-tested
  but not wired to a live embedder or model in `workflow_plan` — both need plumbing that lands with
  session 41's grounding work. Hybrid composition returns the names to compose without building the
  subworkflow spec; `presets`/`lighter_path` are surfaced but not yet acted on.

- **2026-08-02 — DONE (#179) — Grounded generation (session 41 of the WF2 queue).**
  Branch `feature-wf2-planning-grounding`. `workflows/grounding.py` (the bundle, three signature
  discovery tiers, orient-then-drill), `workflows/patterns.py` (seven proven shapes with slots,
  `when_not`, and a deterministic pick), `workflows/generation.py` (generated prompt, mechanical
  self-check, repair-not-regenerate, the decline path, `oneOf` emission schema), all wired into
  `workflow_plan`. 12320 tests (+69), lint clean at 624 files.

- **DISCOVERY — provider argument shapes are only partly discoverable, and the first two tiers
  covered 7 of 16.** `MCP_CORE_SCHEMAS` has typed fields for the tool-backed providers and some
  providers document `action_config` in a docstring, but NINE had neither — including `bash`,
  `create-task` and `run-workflow`, the ones a generated plan reaches for most. A bundle naming a
  provider it cannot describe calling is the ungrounded failure with extra steps. Added a third
  tier that scrapes what the provider's own code reads, taking coverage to 15/16; the one remainder
  (`notification-digest`) genuinely reads no config. The scraper's first pattern also missed the
  `(action_config or {}).get(...)` idiom and reported `run-workflow` as taking NO arguments — a
  pattern miss that produces a confident "takes no arguments" is worse than one producing silence,
  so requiredness is never inferred from a scan and the source tier is labelled.

- **DISCOVERY — three of my own "proven" shapes had no stopping condition.** The A/B harness caught
  it: `convergent-research`, `fan-out-synthesis` and `creative-exploration` ended on a synthesis or
  selection stage, which passes the engine's structural validator and fails the plan's minimal
  goal/verification/stopping triple. Grounded first-try-valid measured **3/5** against the plan's
  ≥4 bar until each gained a gate. A skeleton that teaches the planner to omit the thing the hard
  requirements demand is not a proven shape.

- **DEVIATION — `capabilities_for` does not exist.** The real accessor is
  `get_default_registry().capability_of(type)`, and an unbootstrapped process legitimately cannot
  answer — which must read as UNKNOWN. The wrong call silently produced `structured_output: False`,
  which would have sent every plan down the prose-with-repair path even on a model that handles
  schemas.

- **DEVIATION — MCP tools are surfaced as SERVERS, not tool names.** `mcp_client.list_tools` is
  async and requires live connections; enumerating tools on the planning hot path would block on a
  user's remote endpoint. The bundle names the configured servers and tells the planner to discover
  tool names rather than guess them. A missing `mcp.json` is "none configured" — distinct from
  "unreadable", which is stated.

- **A/B harness (UP-R13.2)** ships as the acceptance test, scored on first-try-valid: ungrounded
  specs must score 0/5 and grounded ≥4/5, with validation failures and silent misses asserted as
  SEPARATE modes. It runs without a model — it measures the mechanism this session builds; a
  model-scored end-to-end A/B belongs with the eval substrate that owns scoring.

- **Validated in the real runtime:** a monitor intent no template serves falls to grounded
  generation with the `iterative-refinement` shape and a 4753-char grounded prompt, and that
  skeleton — slot-filled — passes BOTH the self-check and the engine validator clean.

- **NOT DONE:** the brownfield context pass (UP-R17) is a `codebase_context` parameter the prompt
  accepts and nothing yet populates — building the depth-filtered tree + README head + `(project_id,
  tree-hash)` cache is its own scope and reads the filesystem, which the rest of this session does
  not. The entity/topic grounding preamble (UP-R14) likewise: `brief` threads through from session
  38's Session Brief, but entity resolution needs a lookup provider that does not exist. Repair
  execution is built (`repair_prompt`, `MAX_REPAIR_ATTEMPTS`) but not driven — the loop that calls a
  model, self-checks, and re-prompts needs the model plumbing that lands with session 43's review
  cycle.

- **2026-08-02 — DONE (#181) — Contracts + parameterization (session 42 of the WF2 queue).**
  Branch `feature-wf2-planning-contracts`. `workflows/contracts.py`: `resolve_unfilled_inputs()` +
  `template_types()` + the extraction contract (UP-R8), per-stage done-means contracts with their
  lint (UP-R3), and blocking-vs-open decision typing (UP-R16). Wired into `workflow_plan`'s template
  path as the review surface. 12417 tests (+97), lint clean at 625 files.

- **DISCOVERY — THREE of eighteen shipped templates declared an input nothing read.** The derived
  schema found them immediately, which is the whole point of deriving it. `knowledge-lint` offered
  `apply` while its node hardcoded `false` — a user could set it, see no effect, and get no error;
  now wired through. `design-project` and `general-project` offered loop caps nothing consulted.

- **DEVIATION — the phantom loop-cap inputs were DELETED, not wired.** Wiring them to the loops'
  `max_iterations` looked like the obvious fix and broke a real invariant:
  `test_every_loop_has_a_real_exit_and_a_hard_cap` requires the cap to be a statically verifiable
  literal, and a binding makes it a string at spec time. A user-supplied cap can also be a value
  that never fires — so offering it would be offering to weaken a safety invariant. An input nothing
  reads is a control that lies; the honest fix is removing it.

- **DISCOVERY — the contract lint found SIX templates ending on a write with nothing establishing
  the work was right.** Five are this program's own and now carry a machine check:
  `knowledge-synthesis` (is the synthesis grounded in its sources?), `thesis-tracker` (is the thesis
  still falsifiable?), `rich-ingest` (did the lenses stay inside the transcript?), `gap-healing` (are
  the drafts supported by the excerpts?), and `knowledge-lint` (did the merge keep every distinct
  detail — checked per cluster, where the loss would happen). `publish-article` had only a human
  APPROVAL: nobody verified the revision addressed the accuracy findings before it was stored as
  reference, so a judge now runs before the gate. **Recorded not fixed:** `design-review`,
  `diagnose-run` and `project-planning` (Slice 9a) have the same gap — retrofitting templates this
  session did not author, blind, is how an unvalidated gate lands.

- **The lint was TOO STRICT twice, and both exemptions are measured.** A stage whose output a
  verified stage consumes is checked THROUGH it (the reviewer's findings are what the judge reads),
  so demanding a gate per stage would turn a three-stage plan into a six-node ceremony. And an
  ALL-DETERMINISTIC plan is exempt entirely — `knowledge-health` is every-node-zero-token, so its
  output already IS the check, and paying a model to form an opinion about arithmetic is the kind of
  finding that gets a rule suppressed wholesale, taking the real findings with it.

- **Decision typing is mechanical, with two safe-direction overrides.** A gate whose output feeds a
  downstream binding is blocking; ambiguity that changes no execution path lands as an Open Decision
  on the finished summary. Destructive-risk and approval gates are ALWAYS blocking whatever the
  bindings say — auto-proceeding past "may I delete this?" because nothing consumed the answer is
  the one classification error with an unrecoverable cost.

- **NOT DONE:** the triage-first convention (UP-R11) and `escalate-and-reclassify` as a named
  mutation — the convention is encodable in the pattern registry, but the mutation is an engine op
  and belongs with the review/revision cycle in session 43. The preflight step the planner should
  emit (aggregating requirements one hop from referenced providers) needs the provider-requirement
  data the grounding bundle does not yet carry.

- **2026-08-02 — DONE (#182) — Review + revision (session 43 of the WF2 queue).**
  Branch `feature-wf2-planning-review`. `workflows/revision.py`: typed merge-by-id patches with the
  `NO_UPDATE` sentinel, TTL'd draft sketches, the announce-block review surface, a structural cost
  estimate, plan-as-markdown, and inferred-vs-stated chips. Wired into both `workflow_plan` paths.
  12467 tests (+50), lint clean at 626 files.

- **The load-bearing property: an untouched stage CANNOT change.** The merge walks the original tree
  and substitutes; it never rebuilds. "Absent means preserved" is therefore structural rather than a
  promise — there is no code path that writes an untouched node.

- **Every refusal is deliberate.** A `replace` naming a node that does not exist is REJECTED rather
  than silently converted to an `add` (inventing a stage the user never asked for); a duplicate `add`
  is rejected with "use replace"; an `add` naming a missing anchor is rejected rather than appended,
  because a silent relocation is the kind nobody reviews; and a `replace` cannot RENAME its node,
  since that would break every binding pointing at it when the user asked to change the stage rather
  than re-address it.

- **DISCOVERY — the announce block and the contract lint disagreed about the same plan.** The header
  reported "Unchecked: review-accuracy, review-clarity, store, record-decision" on `publish-article`
  while the lint deliberately exempts all four (two feed a verified stage, two are zero-token). Two
  views of one plan disagreeing is worse than either alone: the user believes the scarier one, and
  the lint they might have trusted looks wrong. The header now applies the same exemptions.

- **DISCOVERY — the EXPIRED sketch reason was one-shot.** Dropping a stale sketch on read is right (a
  sweep needs a clock nobody owns), but without a tombstone the second attempt on the same id
  reported "unknown sketch" — losing the distinction that tells a user their revision was reasonable
  and the draft merely aged out. A bounded tombstone set keeps the honest answer available.

- **The cost estimate returns COUNTS, never a price.** A dollar figure derived from a node count is a
  confident number built on an unknown per-call cost, and a user who sees "$0.42" believes it. It
  multiplies through fan-out (a stage inside a foreach is not one call) and NAMES unbounded loops
  separately rather than folding them in — an unbounded loop makes the number a floor, and
  presenting a floor as an estimate understates the one case that runs away.

- **NOT DONE:** the streaming render (progressive buffer-append re-parse, shimmer on in-flight steps)
  is a frontend concern with no backend seam in this session — the plan tool returns a complete
  surface, and streaming it needs the SSE events the plan lists for `RUN_LIFECYCLE`, which belong
  with a UI slice. The one small-model naming call (`{title, description, per-step labels}`) is
  likewise unwired: `plan_markdown` uses deterministic labels from each node's own config, which is
  the stated fallback. `revise{step_ref, comment}` exists as the patch grammar but is not yet an
  answer verb on `workflow_resume` — that is an engine-surface change belonging with the autonomy
  work in session 44.

### 2026-08-02 — session 44 (autonomy + risk) DONE

`workflows/autonomy.py` (new): the risk-signal registry, autonomy floors and offers, HITL/AFK
attention typing compiled to the engine's own `require_hitl`, the confirmation matrix, the three
interrupts, and earned trust. `_autonomy_surface` ships it from `_plan` alongside the review surface
from session 43. 82 tests in `tests/test_workflows_autonomy.py`.

- **The registry reuses the engine's `RiskLevel` rather than declaring a second vocabulary.** A
  private gradient would drift from the one the approval UI already renders, and two gradients
  disagreeing about the same node is how a user learns to ignore both. Every signal states a
  CONSEQUENCE, not just a name: an informed-consent question built from `destructive_op hit` is not
  informed, while "this can delete data that cannot be recovered" is a decision a person can make.

- **DISCOVERY — three measured false positives, all found by scanning the SHIPPED library rather
  than fixtures.** (1) A bare `\btruncate\b` matched `| truncate(1500)` and flagged THREE templates
  as destructive for shortening a string in a prompt. (2) A bare `\bcredential\b` fired on
  `audit-sweep`'s finder, whose entire job is to look FOR credential-handling problems — reading
  about a risk is not taking one. (3) Generic production patterns fired on "write a report about our
  production architecture". All three are now action-shaped (verb + target, or write-verb +
  preposition), and each is pinned by a regression test. A scanner that fires on a template doing
  its job gets suppressed wholesale, and the real findings go with it.

- **DISCOVERY — the provider check `continue`d past the pattern scan.** A `run-script` node whose
  argument said `drop table users` was reported only as "uses the `run-script` provider" — the same
  verdict on far worse evidence. Capability and content are now both recorded, because a reviewer
  triaging by evidence needs to see which one fired.

- **The scan reads action ARGUMENTS, not just prompts.** A `bash` node's danger is entirely in its
  command; a scan that read only prompts would miss every action node's actual payload. A command
  assembled from a binding cannot be scanned at plan time at all, which is why `bash` is dangerous
  by capability whatever its literal argument says.

- **Silent honor and silent refusal are both failures.** Honoring `unattended` on a plan that deletes
  production is the obvious one; quietly downgrading it is the one that makes a user distrust the
  control and stop reading it. A request above the ceiling produces exactly ONE consent question
  naming the consequence. Live-measured: `code-implementation` (uses `bash`) is capped at per-stage
  with `unattended` absent from the offer, while `publish-article` earns unattended and still stops
  at its approval gate — `compile_require_hitl(..., Mode.UNATTENDED)["approve"]` is `True`, because
  an approval gate exists to pause for a person and no mode retires it.

- **A single failure RESETS earned trust rather than averaging it away.** Twenty clean runs and one
  failure earns nothing: a template that broke once is one whose next run deserves eyes, and
  averaging is how earned trust becomes a rubber stamp. A first run is report-only for the same
  reason — the cost is one extra approval, and the alternative is discovering the behaviour by having
  it happen. The commitment stamps mode, executor and environment TOGETHER, since
  unattended-in-a-worktree and unattended-on-the-real-filesystem are different grants.

- **An author's explicit `require_hitl` is never downgraded.** The author knows something the scanner
  does not, and a scanner that overrode them would make the declaration useless. Containers are not
  typed at all: a sequence is a scheduling policy, and putting a stop on something that does no work
  is a pause a user cannot act on.

- **NOT DONE:** permission pre-approval as durable session allow-rules needs the approval store's
  scope model (an autonomy-mode grant is not a tool grant, and conflating them would widen a tool
  approval into a mode approval); per-template p50/p95 spend needs Run Ledger aggregation that the
  Evaluation-Substrate plan owns; mid-run demotion (dropping from unattended to per-stage when a
  risk appears at runtime rather than plan time) needs a controller seam this session does not touch
  — `should_interrupt` is the plan-time half of it. `revise{step_ref, comment}` as an answer verb on
  `workflow_resume` carried forward from session 43 remains unwired for the same reason: it is an
  engine-surface change, and the seam belongs with the entry surfaces in session 45.

### 2026-08-02 — session 45 (grill + entry surfaces + template pipeline) DONE — Universal Planning CLOSED

Four new modules, all pure: `grill_protocol.py` (the structured `rigor: deep` protocol),
`rigor.py` (the cheap end of the axis), `template_pipeline.py` (mining, discover-then-freeze,
the nudge, entity scrubbing), `eval_specs.py` (per-template benchmarks derived from the artifact).
`_plan` gains a `rigor_note`, the fast-path refinement gate, and a `_grill_surface`. 286 new tests.

- **The existing `grill.py` was NOT replaced.** It is the vendor-neutral `assess → recall →
  decompose → save` machinery and the plan's ABSORB note reads as a rewrite; treating it as one
  would have deleted a seam the goal loops and the chat skill both use. The protocol sits on top:
  recommendation-bearing questions, the channel split, pacing, probes, Step-0, prohibitions.

- **Facts are looked up, not asked — mechanically.** A question is routed to a lookup channel or it
  is asked, never both. The three channels stay separate callables (memory recall, knowledge search,
  the brownfield pass) because a merged "context fetch" would make it impossible to say which
  subsystem answered, and the plan states that boundary normatively.

- **DISCOVERY — an arbitrary asymmetry in the lookup router.** The hand-listed codebase phrases had
  `which file` and `what module` but not `which module`, so a natural phrasing fell through to ASK.
  An asymmetric router is worse than a narrow one: it works often enough that nobody notices the
  half that does not. Replaced with an interrogative × noun cross-product, which removes the class.

- **A deferral is never a confirmed requirement.** "You decide" / "no preference" / "idk" fold into
  ASSUMPTIONS with the recommendation attached, and an unanswered load-bearing question with no
  recommendation is an OPEN QUESTION that blocks emission. Three lists, so a guess can never be
  presented as something the user said.

- **Prohibitions are frozen and reach every worker.** The block is injected into each model-bearing
  node rather than the root — a worker reads its own config, so a root-level prohibition is one
  nobody sees. Zero-token nodes are skipped. The boundary answer splits on newlines and semicolons
  but NOT commas: "don't touch prod, staging, or CI" is one boundary, and shredding it into three
  partial ones produces three weaker rules.

- **DISCOVERY — the fast path was inert from the only surface that can request it.** `workflow_plan`'s
  published rigor vocabulary is `minimal`/`standard`/`deep`, and `is_fast` matched only the literal
  `fast`. A caller asking for `minimal` got the standard path plus a note saying so. The same bug in
  `rigor_note` printed "Fast path (rigor=standard …)", a note contradicting its own headline. Both
  now share one `_FAST_WORDS` alias set.

- **DISCOVERY — the risk half of the deep-rigor trigger was present and inert.** The plan makes ANY
  risk-registry hit force `rigor: deep` and `deep_triggered` implemented it, but nothing was feeding
  it hits — so a destructive plan the classifier happened to call `standard` went ungrilled.
  `_grill_surface` now runs session 44's `scan_risk` over the proposed tree. Verified live: a `bash`
  node running `rm -rf /var/data` triggers the grill on an intent classified STANDARD.

- **The refinement gate lands after the first OUTPUT, not at the end.** The whole premise of the fast
  path is refining against something built; a gate at the end refines nothing. A bare single-node
  root (the shape the fast path produces most often) is wrapped into a sequence rather than skipped —
  refusing there would make the mechanism inert exactly where it matters. Scheduling is idempotent.

- **The acceptance ratchet is append-only and deduplicated by exact text.** A criteria list that can
  shrink is one where a later revision silently drops the check an earlier failure earned. Two
  SIMILAR criteria are both kept: collapsing them would be the planner deciding they are the same.
  `revise_from_artifact` deliberately does NOT edit nodes — that stays with session 43's merge-by-id,
  because a second edit path is a second chance to silently rewrite a stage nobody complained about.

- **A denial is a decision, in mining.** A tool the user refused in the session never enters the
  mined permission signature, even if a later call succeeded. A miner counting only successes would
  re-request it, which reads as the system overriding the user rather than forgetting.

- **Entity scrubbing is deliberately conservative.** Sentence-initial capitals are skipped (measured
  as the single largest source of junk slots — otherwise every prompt's first word becomes a
  parameter), `NON_ENTITY_TOKENS` is the single point of truth so a domain acronym cannot be
  parameterized in one path and not another, and one entity across two stages binds ONE input.

- **DISCOVERY — two defects in the derived eval, both found by running it on the real library.**
  (1) A hand-rolled tree walk over guessed key names (`branches`/`then`/`otherwise`) found **4 of 13**
  nodes in `deep-research` and 2 of 8 in `audit-sweep`: the engine's branch children live under
  `cases`/`default_case`, so every branch subtree was silently skipped. Replaced with the engine's
  own `walk` over a typed `Node`. (2) Testing for `stage` alone reported `deep-research` — which
  makes five `infer` model calls — as "all nodes are deterministic — the outputs are facts, not
  opinions", filing it as needing no judge. A confident false claim about the exact property the
  eval exists to check. Now uses the engine's `LLM_KINDS`. After both fixes only `knowledge-health`
  is genuinely judge-free, where before three templates were.

- **The eval names what it cannot grade.** `graded_checks` says what a judge would have to assess and
  leaves the judging to LEARNING-FLYWHEEL; `graded_note` explains an empty list, because an empty
  list with no explanation reads as "nothing to grade", which is a claim. The suite reports the
  free/graded split so a passing CI run cannot be read as "every template was evaluated".

- **NOT DONE:** the QuestionSlider widget, the streaming render, and the `suggest_template` chat tool
  registration are frontend/tool-surface work — `Round.to_ask()` returns the engine's own typed `Ask`
  precisely so no planner-specific renderer is needed, and the nudge's anti-nag state has no
  persistence seam in this session (it takes a `NudgeState` the caller owns). `source_session_id` on
  `workflow_plan` is unwired: `mine_session` takes parsed records so the rules are testable, but
  resolving a session id to a transcript path belongs with the tool-schema change. The grounding A/B
  harness (UP-R13.2) needs the judge LEARNING-FLYWHEEL owns. `revise{step_ref, comment}` as a
  `workflow_resume` answer verb remains carried forward — twice now — because it is an engine-surface
  change and every planning session has correctly declined to reach into the resume grammar.
  **CLOSED 2026-08-10 by WF2UNI-8** — see the entry below.

### 2026-08-10 — WF2UNI-8 (the `revise` answer verb) DONE — carried-forward item RETIRED

`revise{step_ref, comment}` is now a real answer verb on `workflow_resume`, and the item carried
forward through sessions 43, 44 and 45 is closed. What made it carryable that long was correct
caution: it IS an engine-surface change, so it belongs in `controller.resume`, not in a planning
session. It lands there now.

- **Validated where the token still exists.** `_parse_revise` runs alongside `Ask.validate_answer`,
  BEFORE `consume_continuation` — the ordering that method's own docstring justifies. Every
  refusal (`WF_REVISE_UNKNOWN_STEP`, `WF_REVISE_AMBIGUOUS_STEP`, `WF_REVISE_NO_STEP_REF`,
  `WF_REVISE_NO_COMMENT`, `WF_REVISE_NOT_APPLICABLE`, `WF_REVISE_REJECTED`) leaves the reviewer
  able to answer, because a rejected revision means they are still deciding.

- **Exactly one node, enforced by a COUNT.** `resolve_step_ref` refuses a ref matching two nodes.
  `_ids_in` returns a set, so the duplicate that makes a ref unpatchable is precisely the fact a
  set discards — hence `_count_id`. An ambiguous ref would patch whichever copy the walk reached
  first and leave the other running the text the reviewer rejected: a half-applied revision, worse
  than a refused one because the run carries on looking revised.

- **The comment reaches the PROMPT, not just a note.** `comment_patch` emits a `replace`, not an
  `annotate`. An annotation lives where only a reviewer reads it, so the step would re-run on the
  text the reviewer just objected to — the mechanism would exist and change nothing. A GATE's
  prompt is deliberately exempt: that string is the QUESTION put to a human, and appending "be
  terser" to "Ship it?" would corrupt the ask being answered.

- **awaiting_review → running, in the engine's own state names.** Mirroring
  `planning.session.comment_step`: the gate instance goes back to PENDING at the current epoch
  rather than DONE, so it re-asks against the revised step. Nothing is marked approved, no
  `always_allow` is remembered, and `gate_resolved` is NOT written — a revise folded into that
  event would make `introspection.gate_stats` count a wording request as a said-no. It gets its
  own `journal.GATE_REVISED` kind instead.

- **One writer, so "what runs is what was recorded" is structural.** The revision is routed through
  `_commit_mutation` as an `UPDATE_NODE` op carrying `raw={"op": "revise", ...}`. That is the single
  writer of `spec.json` + `spec_history/` + `user_edited_mid_flight`, so the executing spec, the
  persisted spec and the audit record are one document rather than three that agree by convention.
  A test asserts `store.read_spec(run) == controller.spec` after a revise, and that the recorded
  `spec_hash` hashes the file on disk. It also gives the refiner the hand-fix signal for free.

- **DEVIATION — "the approved PROSE artifact on disk matches what runs" is satisfied against the
  spec, not a prose file, because no prose-artifact writer exists.** `revision.plan_markdown`
  (`workflows/revision.py:587`) has exactly one caller, `mcp_workflows.py:1018`, which returns it
  as a `plan_markdown` STRING in the `workflow_plan` response body. Nothing writes it to disk: the
  only per-run files are `spec.json`, `state.json`, `spec_history/`, outputs/artifacts and the
  journal (`workflows/store.py:319-510`), and no path under `artifacts/` receives a plan. The
  plan-as-artifact clause at line 338 of this plan is therefore still NOT DONE and remains open
  work. Inventing an artifact-writing surface to satisfy the wording would have shipped a new
  persistence seam under cover of an answer-verb atom. The clause's real content — the approved
  document and the executed document cannot diverge — is enforced above on `spec.json`, which is
  the document that actually runs.

- **DISCOVERY — two workflow tools advertised an argument nothing validated.**
  `WORKFLOW_RESUME_SCHEMA` declared no `answer` FieldSpec (despite a comment saying `answer` is
  "deliberately UNTYPED") and `WORKFLOW_PLAN_SCHEMA` none for `project_id`, while both are
  advertised in `_list_tools()` and read by their handlers. `validate_tool_args`
  (`validation.py:248-259`) REJECTS an unknown field, so a schema-validated call carrying either
  would fail outright. LATENT, not live: `_validate_args` looks up `MCP_CORE_SCHEMAS` only
  (`mcp_core.py:615`), and `_aggregated_call_tool` (`mcp_core.py:1217`) routes a `workflow_*` call
  straight to `mcp_workflows._call_tool` with no validation hop, so `MCP_WORKFLOW_SCHEMAS` has no
  non-test consumer today. Both FieldSpecs are added anyway — this atom adds a third shape to that
  schema, and the drift becomes a live rejection the moment the surface is wired.
  `test_every_advertised_field_is_a_validated_field` is the ratchet that would have caught both;
  it asserts advertised ⊆ validated, and deliberately not the reverse (a validated-but-unadvertised
  field is an internal argument, not a bug).
- **VERIFY PASS (independent re-run, same day).** Gate re-run from scratch rather than trusting the report: lint clean (1484 files), `mypy src/personalclaw harness` clean on 782 files, and 170 passed across `test_workflows_gate_revise` + `test_workflows_tools` + `test_workflows_revision` + the two full-suite-only ratchets (`test_agent_reference`, `test_inert_surface_baseline`). The eight assertions that carry the atom's correctness were run individually and named: five `TestARejectedReviseKeepsTheToken` cases (unknown step_ref, missing comment, missing step_ref, stale epoch, unknown token refused before any spec write) and three `TestWhatRunsMatchesWhatWasRecorded` cases (spec on disk equals the spec the engine runs, recorded ops hash the spec that landed, a revised step does not serve its cached output). Also read the code rather than the summary: all five `WF_REVISE_*` refusal returns in `_resume_revise` precede its `consume_continuation` call, so a rejected revise provably cannot destroy the token.
  **The environment trap this atom nearly hid.** The venv is editable-installed against the MAIN tree, so a bare `pytest` inside the worktree imports `/…/PersonalClaw/src` and silently tests `main` instead of the branch. Every run here was prefixed `PYTHONPATH=/private/tmp/uni8-wt/src`, and the proof recorded before believing any number: `personalclaw.workflows.controller.__file__` resolving under the worktree and `hasattr(controller, "_parse_revise")` returning True. The same trap produced a false green twice earlier today in the apps repo; a green from the wrong tree is worse than a red, because nothing about it looks wrong.
  **DEVIATION (upheld on review) — the done-when's "approved prose artifact on disk" has no writer.** `revision.plan_markdown` (revision.py:587) has exactly one caller, `mcp_workflows.py:1018`, which returns it as a STRING in the `workflow_plan` response body; per-run files are only `spec.json`, `state.json`, `spec_history/`, outputs and the journal. Rather than invent an artifact-writing surface to satisfy the wording, the clause's real content is enforced structurally against `spec.json`: the revision commits through `_commit_mutation`, the single writer of spec + history + `user_edited_mid_flight`, and the two tests above assert the on-disk spec equals both what the engine walks and what the history hash records. Plan line 338's plan-as-artifact clause remains NOT DONE and is the honest residual.
  **LATENT, NOT LIVE — stated precisely because the distinction matters.** `MCP_WORKFLOW_SCHEMAS` has no non-test consumer: `_aggregated_call_tool` (mcp_core.py:1217) routes a `workflow_*` call straight to `mcp_workflows._call_tool`, `_validate_args` (mcp_core.py:615) reads `MCP_CORE_SCHEMAS` only, and `InProcessMcpToolProvider` wraps the raw `_call_tool` too. So the two field mismatches were not a live outage, and calling them one would have been the more dramatic and less true framing. They are fixed regardless — this atom adds a third field to that schema — and `test_every_advertised_field_is_a_validated_field` now asserts advertised ⊆ validated across all 19 tools, the check that would have caught `workflow_resume.answer` and `workflow_plan.project_id` when they drifted. The reverse direction stays deliberately unasserted: a validated-but-unadvertised field is an internal argument, not a defect.
- **FOLLOW-UP (same PR) — the new event was published to nobody.** CI's `test_transport_doctrine::test_workflow_engine_sse_events_are_all_registered_in_the_frontend` caught `workflow_gate_revised` publishing with no frontend listener. That ratchet exists because **EventSource silently DROPS event types with no registered listener**, so an unlisted publish is a live update that never arrives and no other test would have shown it — the backend-truth/frontend-silence shape. Fixed in all THREE places the contract spans, since fixing one would leave it half-wired: the `WORKFLOW_LIFECYCLE` union in `useWorkflowStream.ts` (what actually subscribes), the mirror list in `workflowMeta.test.ts` (the FE-side parity assertion), and a real `case` arm in `workflowFold.ts`. The module docstring's publisher inventory was updated too — it is the map a reader trusts, and a stale one is how the next event goes missing.
  **The fold arm is deliberately NOT a copy of `workflow_gate_resolved`.** Resolved marks the node `done`; a revise patches the step and RE-RUNS it, so it marks `running` — showing a finished step the engine is still executing would be a lie in the run view. Attention is cleared in both, because the ask was answered either way.
  **A bug in my own first draft, caught by reading the helper instead of pattern-matching:** I keyed the patch on `{...env, node_id: env.step_ref}`, but `patchNode` keys on `instance_path` (workflowFold.ts:242) and ignores `node_id` entirely — so the override was inert decoration that would have silently patched nothing. Corrected to pass `env` unchanged, with a comment recording that `step_ref` is informational for a reader, not the key.
  **Gate after the fix:** the failing ratchet plus `test_workflows_projection_events` — 29 passed; FULL `npm run test --workspace web` 1112 tests / 111 files; `tsc --noEmit` exit 0. (The worktree had no `node_modules`, so the first web run exited 127 and `npx tsc` resolved to an unrelated binary — `npm ci` first, then re-run. An exit 127 read as a pass is exactly the false green this session has already been bitten by twice.)
- [2026-08-11][WF2UNI-9 / Success Criterion 9] DONE: a plan a user jots into a notes file now becomes reviewable work. `planning/scratchpad.py` parses the configured scratchpad, triages each line, and raises an inbox PROPOSAL with a backlink to the source line. It rides the EXISTING `_file_watch_poll_loop` rather than adding a third poll task — same "a local file changed" clock at the same cadence, and its own fingerprint check makes an unchanged file one `stat`.
  **Not an inert path — the call chain is real:** `_file_watch_task` → `_file_watch_poll_loop` (gateway.py:1465) → `_scan_scratchpad` (:1501, :1507). Verified by following the chain, not by the file existing.
  **DEVIATION — the done-when names two components; only one exists.** The intent classifier is real and used as written: `workflows/intent.py:269 classify()`, deterministic and LLM-free, so a jotted line routes exactly like a typed one. There is NO `triage_gate` anywhere — the only "triage" token in `triggers/` is `models.py:114 Outcome.SKIPPED_TRIAGE`, which has no writer at all. Rather than invent one, `triage_line` plays the gate: a structural verdict, plus the SHIPPED pre-LLM injection screen (`triggers/screen.py:265 screen()`, reused not re-implemented), plus an actionability test. Declining is first-class and carries a named reason.
  **Deliberately NOT a store trigger.** A scratchpad line never starts a run, so there is no action to dispatch and nothing for the capability fence to guard: it raises a proposal and stops. Incident mode still suspends the scan, which is right — proposing work is unattended background activity even though it executes nothing.
  **Dedup is TWO tiers, because one leaks.** Tier 1 is a persisted content-hash seen-set (`<config_dir>/planning/scratchpad-seen.json`, atomic write; a sidecar rather than `entity_settings/` because it is high-churn growing state, not an allowlisted PUT document). Tier 2 is `emit_attention_item`'s `dedup_key`, which only matches PENDING/SEEN rows (`inbox.py:473`) — so a DISMISSED proposal would return on the next scan without tier 1. Both are asserted: `test_a_dismissed_proposal_does_not_come_back` proves the open-row lookup returns None for a dismissed row AND that the seen-set holds the line down anyway; `test_a_restart_does_not_resurrect_a_proposal` forces a fresh fingerprint so the seen-set — not the unchanged-file short-circuit — is what stays quiet. Content hashing means re-indenting or reordering the file proposes nothing.
  **The crux filter, tested across its real spellings.** Checked and struck lines are DONE or ABANDONED, so proposing them is the most user-visible wrong behaviour. Verified green over five checked markers (`- [x]`, `- [X]`, `* [x]`, `1. [x]`, `- [✓]`) and three struck forms (`- ~~x~~`, bare `~~x~~`, `- [ ] ~~x~~`).
  **Backlink:** `<abs_path>:<line_no>`, 1-based, in `refs` as `scratchpad_path`/`scratchpad_line`/`scratchpad_hash`/`backlink` and in the row body. `test_the_backlink_resolves_to_the_right_line` reads the file back at that index rather than trusting the string.
  **DISCOVERY — a new config field has a FIFTH gate the contract does not mention.** `config-baseline.json` + `test_config_baseline` red unless regenerated via `scripts/generate_config_baseline.py`. That is neither in CONTRIBUTING's 5-point contract nor in the usual full-suite ratchet list; a sanctioned grow (one leaf added), regenerated in the same commit. Worth adding to the contract's own wording.
  **DISCOVERY (own error, caught by a test):** the first `configured_path()` called a nonexistent `load_config()`. There is no such symbol — it is `AppConfig.load()`, a zero-arg classmethod. Uncaught, this would have shipped as a silently-always-off feature: the scan would raise, the loop would swallow it, and no proposal would ever appear.
  **Config, 5 points + the baseline:** `PlanningConfig` dataclass + `_meta`, `AppConfig.load()` (`planning_data` block + constructor), `to_dict()`, `_EDITABLE_CONFIG["planning.scratchpad_path"]` with a `pathguard.canonicalize` write-boundary sanitizer, and a Settings → Watched sources control. Default `""` reads no files at all, so the feature is off until a user opts in.
  **Gate (independent re-run, judged by EXIT CODE not tails):** black/isort/flake8 all 0; `mypy src/personalclaw harness` clean on 787 files; 84 passed across the new suite + `test_config_roundtrip` + `test_config_baseline` + the three full-suite-only ratchets; FULL `npm run test --workspace web` 1170 tests / 120 files with `node_modules` verified present first (a missing install exits 127, which reads as a pass from a tail); `tsc --noEmit` 0 — and tsc caught a mock-arity error vitest did not. PYTHONPATH proved via `scratchpad.__file__` resolving under the worktree. The web run's timestamp-only churn in `docs/design/consistency-audit.json` was reverted rather than committed; `driftHits` stays 7 and `filesScanned` correctly stays 432, since this atom adds a control to an existing panel rather than a new component.
- [2026-08-11][WF2UNI-7] DONE — all four clauses wired. S45 built a whole session→template pipeline and nothing ever called it: `workflows/template_pipeline.py` had ZERO production importers (its only external mention was a *comment* in `surfacing_channels.py:545`), and `workflows/eval_specs.py` had zero. Both are now reachable from a live surface, so the module `_plan`'s own docstring was holding a seat for — "the template-aware planner is UNIVERSAL-PLANNING's, and inventing a half-planner here would have to be deleted when the real one lands" — is the planner that landed.
  **Clause 1 — `source_session_id` → `mine_session`.** New `session_map.transcript_path(sid)` + `read_transcript(sid)`, both resolving the home PER CALL rather than reusing the import-time `_SESSIONS_DIR` binding — that binding would have handed any test (or a lazily-booted gateway) a path under the REAL home, which is the patch-config_dir-misses-import-bound-stores trap. `_plan` → `_mine_source_session()` → `read_transcript()` → `mine_session()`. **What proves the pipeline is wired rather than merely present:** the test writes an actual `sessions/<sid>.jsonl` and asserts the tool reports what the FILE said — `permission_signature == ["github_list_issues"]` with `denied == ["shell_exec"]`, derived from an `approval: deny` record inside it. A hand-built record list would have passed while the production path never resolved a session at all.
  **Clause 2 — `eval_specs` imported by a live surface.** `_eval_surface()` in `mcp_workflows.py`, called from `_plan_from_template`, so every template plan carries an `eval_spec`. Grading stays out per the done-when's own deferral, and that is ASSERTED rather than assumed: a test pins that the surface carries `graded_checks` names and NO `passed`/`score`/`verdict`/`grade` key. An un-gradeable spec is the correct output here, not a gap.
  **Clause 3 — `suggest_template` + persisted `NudgeState`.** Lands in `mcp_core.py`, not `mcp_workflows`: it does not fit the hard `workflow_` prefix and is a conversation-level affordance rather than a workflow entity op. New `workflows/template_store.py` persists per-shape state plus the nudge clock. **No-re-nag proof:** decline → discard every in-memory object → re-enter through the tool → "declined for this shape". Decline is per-shape not global; cooldown fires; the clock survives a restart.
  **Clause 4 — discover-then-freeze.** `workflow_author` with `save=false` that VALIDATED now parameterizes and freezes a SESSION-scoped candidate instead of discarding it; `_library_profiles()` loads candidates into the tiered matcher and `_plan_from_template` can plan from one. Bundled names win collisions, an invalid spec freezes nothing, session scope does not leak while promoted scope does.
  **Every registration point, in one commit** — the miss that red'd WF2LEA-7 an hour earlier: `MCP_WORKFLOW_SCHEMAS` (`source_session_id` + `_SESSION_ID_RE`), `MCP_CORE_SCHEMAS` (`SUGGEST_TEMPLATE_SCHEMA`), `TOOL_META` ×2, `_RESIDUAL_CORE_TOOLS` with a justification comment, both tools' `inputSchema`, and `reference/{index,tools}.md` regenerated. The workflows surface is still **19 tools** — no count assertion and no prefix rule was weakened to make anything fit.
  **`goal` is no longer schema-required on `workflow_plan`** (both the `FieldSpec` and the tool's `"required": []`), because a mining-only call must be representable. The handler still returns `WF_PLAN_GOAL_REQUIRED` when goal AND session are both absent, and `test_a_goal_is_required` passes unmodified — the contract is intact, only its enforcement point moved from the schema to the handler.
  **DEVIATION — the nudge cooldown clock counts considerations, not turns.** `should_nudge(state, *, turn=...)` wants a turn index and there is none a stdio MCP tool can read: `mcp_core._resolve_session_key()` yields a session KEY, not a count, and nothing in that module exposes one. Rather than invent a symbol or re-count the transcript on every call, `template_store.bump_turn()` advances once per `suggest_template` consideration, so `NUDGE_COOLDOWN` means "that many further shape observations". That preserves the guarantee the rule exists for — a decline is not re-offered until real activity has passed — and it survives a restart, which an in-memory turn index would not. Documented at the function.
  **Gate (independent re-run, by EXIT CODE):** black/isort/flake8 all 0; `mypy src/personalclaw harness` clean on 787 files; **252 passed / 1 xfailed** across the whole tool-surface ratchet FAMILY plus the new 34-test suite (`workflows_tools`, `native_tool_categories`, `api_manifest_drift`, `inbound_mcp`, `agent_reference`, `inert_surface_baseline`, `docs_lint_baseline`). The inert-surface baseline needed no regeneration. PYTHONPATH proved via `template_store.__file__` resolving under the worktree.
  **The 28→64 async failures were contention, settled by measurement not assertion.** `test_workflows_{context,fork,effects}` reported failures that GREW when re-run — the tell for load, not for a defect — and none of those files reference a symbol this atom touches. Re-run serially (`-n0`) on a verified-clear field: **106 passed in 12m44s**. Judging that cluster from a parallel run is how a healthy branch gets called broken.
- [2026-08-12][WF2UNI-13] DONE — the unattended-interrupt taxonomy has a producer and a reader; the member with no signal is deleted; **the one change that would have relaxed a guardrail was refused and is recorded below as an owner decision.** New atom, authored under this plan rather than as a `WV` id because `WF2UNI-5` explicitly owns `workflows/autonomy.py` ("the confirmation matrix + three interrupts") and `WV.md` contains no autonomy scope at all.
  **FINDING.** `Interrupt`'s docstring called its members "the only three things that stop an unattended run" and **nothing in production consulted any of them.** `should_interrupt` (autonomy.py:663) was the only producer, had no caller outside tests, and could only ever return `IRREVERSIBLE`; `UNINFERABLE` and `CONFLICTING` were produced nowhere and `inert-surface-baseline.json` listed both. Three documented stops: two impossible, one never run.
  **MEASUREMENT FIRST, because wiring a guardrail may only ADD stops.** The only seam that enforces anything is `gate_policy.decide`, called once from `controller._apply`'s `WAITING` branch (controller.py:2317). For a trigger-origin (`SCHEDULE`/`EVENT`/`HOOK`/`IDLE`), non-blocking run: `risk: safe` → `AUTO_APPROVED` (proceeds); `caution` → `AUTO_APPROVED` (proceeds); `destructive` → `ASK` (stops); absent or unparseable → `gate_risk` defaults to `DESTRUCTIVE` → `ASK` (stops). **So today is neither "stop for everything" nor "stop for nothing": an unattended run stops for a DESTRUCTIVE-risk GATE NODE and proceeds through everything else.**
  **The two layers never meet, and that is deliberate.** `autonomy.py` speaks `Mode` × `ConfirmationType`; the engine speaks `RiskLevel` × `OriginKind`. `autonomy.Mode` never reaches the run path at all — `run.mode` is `blocking | background`, an unrelated axis (models.py:846). The whole autonomy chain (`scan_risk` → `type_attention` → `compile_require_hitl` → `build_confirmations`) terminates in `_autonomy_surface`'s plan-preview payload, and `require_hitl` — described in-module as "the ONE uniform engine target" — has **no engine reader either**: only mcp_workflows.py:1120 writes it, and `confirmation.requires_hitl` (itself test-only) reads the shape. `_autonomy_surface`'s own docstring already states the asymmetry: "the ENGINE's own gate policy still governs what actually runs, so a failure here loses advice, never enforcement."
  **WHAT I DELIBERATELY DID NOT WIRE, in the failure direction.** (1) `should_interrupt` as a **replacement** for `gate_policy.decide` is a **severe relaxation** — `gate_risk` defaults an undeclared gate to `DESTRUCTIVE` (⇒ ASK) while `_classify_node` types a `gate` node as `READ`/`SAFE` (its `kind` matches none of action/transform/stage/infer), so `should_interrupt` returns `False` and **every unclassified gate in an unattended run would newly auto-approve** — exactly what `gate_risk`'s deny-by-default exists to prevent. Not shipped. (2) As an **additive veto** layered after `gate_policy` (approve→ask only, never the reverse): safe in direction but **inert by construction**, since gate nodes classify as `READ` so the veto never fires — a live reader of a signal nothing writes. Not shipped. (3) At the **action-dispatch seam** — see the owner decision below.
  **E4 OWNER DECISION RECORDED, NOT TAKEN — giving the `OUTWARD` stop teeth.** The taxonomy's one genuine behavioural delta is that `should_interrupt` STOPS for `OUTWARD` at CAUTION while `gate_policy` auto-approves CAUTION. Enforcing it (stopping an unattended run before an outward action) is **additive and real** — it only ever adds a stop. I did not ship it, for two reasons that are the owner's call and not an implementer's: it would create the **second enforcement path this module's own docstring forbids** ("autonomy machinery that grew its own enforcement path would contradict the engine's trust plumbing, so the compilation target is single by design"), and it **changes unattended semantics for every user template that posts or notifies on a schedule** — those runs would newly park for an approval nobody is watching for, which is an availability regression wearing a safety costume. **Population measured:** ZERO of 21 bundled templates use an outward provider (`send-message`/`notify`/`webhook`/`call-app-route` appear only in `bundled/shared/safety-tiers.md` prose), so the shipped library is clean; user-authored templates are not measurable from here. Decision wanted: enforce `OUTWARD` → stop for unattended runs, and if so, through `require_hitl` (giving that target its first engine reader) or at the action seam?
  **WHAT SHIPPED — the counterfactual the offer surface was missing.** Every risk signal caps autonomy at `per_stage`, so `offer_autonomy` routinely RECOMMENDS `per_stage` while still OFFERING `unattended`, and `build_confirmations` is computed at the recommended mode. The preview therefore listed the stops for a mode the user might not pick and said nothing about the one being offered. `unattended_interrupts(confirmations)` answers it per confirmation — which stops survive at unattended, which become journaled assumptions — and `_autonomy_surface` emits it as `unattended_interrupts`. Advisory, so it **cannot** relax anything: it reports on confirmations the plan already raises and changes no verdict.
  **PER-MEMBER — `UNINFERABLE` KEPT, with a producer.** The registry already carries `credentials_or_payment`, which *is* "a credential". The obstacle was that `_classify_node` collapses every DESTRUCTIVE-level signal into the same `(DESTRUCTIVE, DESTRUCTIVE)` pair, so the request reaching `should_interrupt` no longer knew which signal fired. Fixed by carrying registry signal NAMES on `ConfirmationRequest.signals` — a canonical registry value threaded through, **not** a text scan of the question. **Label-only and provably non-relaxing:** `credentials_or_payment` is DESTRUCTIVE-level, so such a request already stopped on risk alone; checking the signal first changes WHICH interrupt is reported, never WHETHER the run stops. Pinned by `test_uninferable_relabels_a_stop_and_never_relaxes_one`, which strips `signals` and asserts both verdicts stop. Worth carrying anyway: "this cannot be undone" tells a user to review the blast radius, "nobody can guess this value" tells them to supply it.
  **PER-MEMBER — `CONFLICTING` DELETED.** Nothing a `ConfirmationRequest` carries expresses "requirements that contradict each other", and manufacturing one by scanning question text is a heuristic, not a signal. The one real contradiction this module detects — a template `autonomy_floor` above the risk ceiling (`offer_autonomy`, autonomy.py:404) — is resolved at PLAN time by letting the floor win and recording it in `capped_by`, so it never reaches a run to stop it and would be the wrong semantics for an interrupt. Both "only three" claims (module docstring + enum docstring) corrected to two rather than left describing a stop that cannot happen. Safe as a clean break: `Interrupt` was never serialized anywhere, having had no producer, so no on-disk value can exist.
  **RATCHETS — two, because this atom's whole subject is a declared-but-unproducible member.** `should_interrupt` is exhaustive over `ConfirmationType` with a RAISING tail (the dangerous default there is `return False`, so a new type falling through the old permissive tail would have been waved through an unattended run), and an AST read asserts every `Interrupt` member is NAMED in `should_interrupt` — the ratchet that would have caught this finding at birth. **Proven to fail:** temporarily adding `Interrupt.RATCHET_PROOF` reds `test_every_interrupt_member_is_produced` and `test_only_two_interrupts_exist` (2 failed); reverted by a targeted edit, never `git checkout --`. The raising tail is asserted directly by `test_an_unhandled_confirmation_type_raises_rather_than_proceeding`.
  **Baseline + gate.** `inert-surface-baseline.json` regenerated with `scripts/generate_inert_surface_baseline.py`: **148 → 146** total, enum 21 → 19, the whole `src/personalclaw/workflows/autonomy.py` block removed. `make lint` clean (black/isort/flake8 0, mypy clean on 804 files); 329 passed / 2 skipped across `-k "autonomy or confirmation or unattended or guardrail"`; 35 passed across the baseline/agent-reference/docs-lint/config-roundtrip ratchets. No `web/` change — nothing in the frontend reads the plan-preview autonomy payload.
- **2026-08-17 — `WF2UNI-12` PARTIAL: clause 1 shipped, clause 2 BLOCKED on an unmet precondition (atom stays `todo`).**
  **Premise correction first.** `plan_memory.py` was **not** already deleted — it was **renamed**.
  `git log --diff-filter=A -- src/personalclaw/plan_format.py` and `--diff-filter=D -- src/personalclaw/plan_memory.py`
  both return `cc44e24f` (`WF2LEA-4`, #938), which deleted only the *journal* half and kept the *format* half alive
  as `plan_format.py`, still imported by `dashboard/chat_title.py`. The clause was unmet; the mechanism had survived
  under a new name. (Corroborated by this repo's own
  `WORKFLOWS-V2-LEARNING-FLYWHEEL.md:1243` — "`plan_memory.py` -> renamed to `plan_format.py` keeping only the
  formatting helpers".) A filename grep is not a mechanism census.
  **Clause 1 — MET, as a real clean break.** Deleted `src/personalclaw/plan_format.py` (169 lines, 8 public names)
  and `tests/test_plan_format.py`. Its only src importer, `dashboard/chat_title.py`, lost
  `_extract_and_redact_plan_metadata` and `_rephrase_plan_lite`, which had **zero call sites** — they existed only to
  be re-exported from `dashboard/chat.py` behind `# noqa: F401`, so nothing was routed anywhere instead because there
  was no caller to route. `looks_like_plan`, `validate_plan_format`, `ensure_go_all_option`, `strip_plan_markers` and
  `PLAN_TEMPLATE` had no src consumer at all. `history.py` was already clean.
  **Clause 2 — BLOCKED (E3/E6). The clause is conditioned on "as loops drain"; loops have not drained.** Measured
  against a live isolated gateway on :10411, not inferred: `POST /api/loops/classify` returns **200** with the full
  composer dict (`intake_rigor`, `roster`, `strategy_id`, `clarifying_questions`, `success_criteria`, `kind_config`,
  ...), and `/plan-session` + `/plan/start` return **400 — registered and reached, not 404**. Routes come from
  `register_unified_loop_routes` (`loop_routes.py:1032`, called at `dashboard/server.py:1138`); the frontend reaches
  them via `App.tsx:124` -> `LoopsSection` -> `LoopPlanningView` (`LoopsSection.tsx:77`) plus `CodePlanningView`,
  `CodePlanReview`, `PlanningArtifactDoc` and six `api.ts` methods. The nine modules named for deletion
  (`loop/classify.py`, `loop/code_classify.py`, `loop/plan_walkthrough.py`, `loop/*_plan_briefs.py`) are what back
  those routes. The designated replacement, `workflows/intent.py:classify(text) -> Intent`, is a deterministic
  keyword classifier returning level/rigor: it **cannot** produce that dict, and
  `git grep workflows.intent -- src/personalclaw/loop src/personalclaw/dashboard` returns **zero** consumers.
  **Owner ruling: deleting them today removes a reachable user-facing feature with no replacement — that is a
  regression, not a clean break.** The pre-1.0 clean-break doctrine permits deleting a *replaced* mechanism; it does
  not license deleting an *unreplaced* one. Clause 2 waits on the loop-drain precondition it already names. No gate,
  no shim, and no dual path was built — there is nothing to build until the replacement exists.
  **Premise correction 2:** `gateway.py` is **not** affected. Its only `planning` import is
  `personalclaw.planning.scratchpad` (:1723), which this atom keeps. The deletion-blocked importers are
  `loop_routes.py`, `loop/store.py`, `loop/manager.py`, `loop/kinds/{__init__,goal,sdlc,design,research}.py` and
  `agents/native/sdlc_tools.py:160`.
  **Gate (re-run independently, not trusted from the report):** `make lint` EXIT=0, mypy clean on 901 source files,
  **419 passed** across `test_context_management`, `test_dashboard_chat`, `test_agent_reference`, `test_portability`,
  `test_durability_inventory`, `test_inert_surface_baseline`, `test_docs_lint_baseline`. No baseline moved (the
  deletion removed no route, provider or declared surface). A **runtime import sweep** of the eight affected modules
  passed 8/8 — the necessary rail here, because of the mypy finding below. `web/` untouched.
- **2026-08-17 — DISCOVERY (`WF2UNI-12`): `make lint`'s mypy leg is blind to a stranded import of a deleted
  first-party module.** With a deliberate `from personalclaw.plan_format import looks_like_plan` re-added to the live
  `chat_title.py`, `mypy src/personalclaw harness` still reported `Success: no issues found in 901 source files`
  while `python -c "import personalclaw.dashboard.chat_title"` raised `ModuleNotFoundError`. Cause:
  `ignore_missing_imports = true` (`pyproject.toml:289`). **For any deletion atom, mypy is not the rail that catches a
  stranded import — only a runtime import or pytest collection is.** Two further mutations reddened nothing and are
  recorded as leads, not passes: `_parse_title`'s `title.upper() == "SKIP"` branch and its `redact_credentials` call
  are both **uncovered**, and `test_fork_redacts_credentials_in_llm_generated_title` passes via the fork path, so its
  name overstates what it pins. Pre-existing on `main`, outside this atom's fence.
- **2026-08-17 — DISCOVERY (`WF2UNI-12`, row-1 residue NOT closed).** The same task row also names
  `OrchestrationTracker`, still in `context_management.py` with `MAX_TASK_FAILURES`/`MAX_STAGE_ROUNDS`/
  `MAX_STAGE_ESCALATIONS`. It is provably inert: `state.py:370` only ever assigns `_orch_tracker = None`, and
  **`_auto_run = True` appears nowhere in `src` or `web`**, so `chat_handlers.py:259`'s stop-detection block and the
  `auto_run_stopped` SEL emission at `:1045` can never fire. Blast radius if scheduled: 5 src files
  (`context_management.py`, `dashboard/{chat,chat_handlers,chat_title,state}.py`) and 2 test files, ~31 references
  each; `auto_run_stopped` has exactly one emission site and no registry or ratchet entry. Left because it reaches
  into the live chat send path, which was outside this atom's file fence.
