# Plan: Universal Project Planning via Workflows v2

**Status:** PROPOSED (rev 2 — research-integrated 2026-07-12)
**Created:** 2026-07-11
**Revised:** 2026-07-12 (18 approved research recommendations folded in; recon-corrected against code)
**Depends on:** WORKFLOWS-V2.md (Slices 0-3), WORKFLOWS-V2-LOOPS-EVOLUTION.md (template library)
**Scope:** Any type of project across all walks of life

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
