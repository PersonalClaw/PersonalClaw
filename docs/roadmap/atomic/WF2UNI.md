# WORKFLOWS-V2-UNIVERSAL-PLANNING — atomic plans

**Source plan:** [`WORKFLOWS-V2-UNIVERSAL-PLANNING`](../plans/WORKFLOWS-V2-UNIVERSAL-PLANNING.md)  
**Code:** `WF2UNI`  
**Source status:** in_progress

Backend DONE across 6 shipped sessions (#178-#184); 6 open atoms remain along wiring/surface/retirement seams (inert template pipeline+eval_specs, unregistered suggest_template, no revise resume verb, unbuilt scratchpad + FE streaming/QuestionSlider, unpopulated grounding inputs, LOOPS-gated retirement).

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `WF2UNI-1` | ✅ (##178) | Matching + classification: no-LLM intent classifier + tiered match_template T1-T5 + routing-fixture CI gate | — | workflows/intent.py (4-dimension tuple + rigor routing) and workflows/matcher.py (T1-T5, reason strings, negatives, presets, failure-path degradation) are live from workflow_plan; tests/fixtures/planner_routing.json asserted structurally in CI at 100% deterministic-tier accuracy with zero LLM calls; 18 bundled templates annotated with typed match metadata on DefMetadata. |
| `WF2UNI-2` | ✅ (##179) | Grounded from-scratch generation: bundle from live registries + pattern-shape registry + schema-constrained oneOf emission + self-check/repair | `WF2UNI-1` | workflows/grounding.py (three signature-discovery tiers, orient-then-drill, MCP servers named), workflows/patterns.py (seven proven shapes each with a stopping condition), workflows/generation.py (generated prompt, mechanical self-check, oneOf/cannot_plan decline path, repair_prompt) are wired into workflow_plan; the grounding A/B harness scores ungrounded 0/5 and grounded >=4/5 with validation-failures and silent-misses as separate modes. |
| `WF2UNI-3` | ✅ (##181) | Stage contracts + parameterization: resolve_unfilled_inputs/template_types + extraction contract + done-means lint + preflight + decision typing | `WF2UNI-2` | workflows/contracts.py provides resolve_unfilled_inputs(), template_types(), the extraction contract, per-stage done-means contracts with the minimal-triple lint (with the two measured exemptions), and mechanical blocking-vs-open decision typing; workflows/preflight.py emits the preflight step; all wired into workflow_plan's template path as the review surface. |
| `WF2UNI-4` | ✅ (##182) | Review + revision: typed merge-by-id patches + NO_UPDATE sentinel + TTL'd draft sketches + announce-block surface + plan-as-markdown | `WF2UNI-3` | workflows/revision.py implements merge-by-id patch semantics (absent-preserved is structural; replace/add refusals deliberate), the NO_UPDATE sentinel, TTL'd sketches with a tombstone set, the announce-block review header applying the same lint exemptions, and a counts-only cost estimate; wired into both workflow_plan paths. |
| `WF2UNI-5` | ✅ | Autonomy + risk: risk-signal registry + autonomy floors/offers + HITL/AFK typing -> require_hitl + confirmation matrix + earned trust | `WF2UNI-4` | workflows/autonomy.py provides the action-shaped risk-signal registry (reusing engine RiskLevel, each signal stating a consequence, false-positives pinned by regression tests), autonomy floors/offers, HITL/AFK typing compiled to require_hitl, the confirmation matrix + three interrupts, and earned-trust (report-only first runs, reset-on-failure, combined commitment stamp); _autonomy_surface ships from _plan. |
| `WF2UNI-6` | ✅ | Grill + rigor axis + template-pipeline & eval-spec modules (built, pure): structured deep-rigor protocol, fast-path/Specify, mining/scrubbing, per-template evals | `WF2UNI-5` | workflows/grill_protocol.py (recommendation-bearing question rounds, facts-vs-decisions channel split, stress-test, Step-0, frozen prohibitions, SaveFn persistence), workflows/rigor.py (fast-path alias set, refinement-after-first-output gate, append-only acceptance ratchet), workflows/template_pipeline.py (mining, discover-then-freeze, suggest_template nudge, entity scrubbing) and workflows/eval_specs.py exist and are unit-tested; _plan gains rigor_note + fast-path gate + _grill_surface (running scan_risk over the tree). |
| `WF2UNI-7` | ⬜ | Wire the S45 template pipeline into production: source_session_id mining, discover-then-freeze candidate templates, suggest_template registration, eval_specs importer | `WF2UNI-6`, `EXT:WORKFLOWS-V2-LEARNING-FLYWHEEL:judge/grading for eval-spec graded_checks` | workflow_plan accepts source_session_id and resolves a session id to sessions/<sid>.jsonl (via session_map.py) feeding mine_session; discover-then-freeze persists LLM-generated specs as SESSION-scoped candidate templates loaded back through the tiered matcher; suggest_template is registered as a local-only chat tool with a persisted anti-nag NudgeState; eval_specs is imported by a live surface so per-template benchmarks are produced (grading deferred to LEARNING-FLYWHEEL). |
| `WF2UNI-8` | ⬜ | revise{step_ref, comment} as a workflow_resume answer verb (span-scoped re-plan re-invoking the planner on one node) | `WF2UNI-4`, `EXT:WORKFLOWS-V2:workflow_resume answer-grammar / engine resume+run-from surface` | workflow_resume's answer grammar accepts revise{step_ref, comment} and routes to revision.py's merge-by-id patch scoped to exactly one node (comment_step's awaiting_review->running semantics); the approved prose artifact on disk matches what runs; carried-forward-three-times item retired. |
| `WF2UNI-9` | ⬜ | Watched-scratchpad intake (Success Criterion 9): periodic scan proposes plans into the needs-input inbox, never auto-executed, with source backlink + dedup | `WF2UNI-1`, `EXT:AUTOMATION-SUBSTRATE:file-watch/interval trigger + run-workflow action`, `EXT:INBOX-NOTIFICATIONS-UNIFICATION:InboxService/emit_attention_item proposal surface` | a file-watch/interval automation trigger scans the configured planning.scratchpad_path, runs each actionable line through the triage gate + intent classifier, and lands a PROPOSED plan via InboxService with a backlink to the source line; dedup by content-hash + seen-line tracking; checked/struck lines ignored; planning.scratchpad_path wired through the 5-point config contract. |
| `WF2UNI-10` | ⬜ | Frontend review surface: QuestionSlider/ask() stepper widget + streaming multi-view render + new SSE events into RUN_LIFECYCLE + small-model naming call | `WF2UNI-4`, `WF2UNI-6` | the deep-rigor Round renders as a QuestionSlider stepper (typed kinds, one-at-a-time, custom-answer escape hatch, single Submit); plan review streams progressively (buffer-append re-parse, shimmer on in-flight steps) across proposal cards + read-only graph + JSON; new plan-streaming/revision/confirmation/demotion SSE events are added to the RUN_LIFECYCLE union in web/src/pages/loops/useRunStream.ts; the {title,description,per-step labels} naming call is wired with deterministic fallbacks. |
| `WF2UNI-11` | ⬜ | Populate the grounding inputs: brownfield context pass (UP-R17), entity/topic preamble (UP-R14), and wire T4/T5 embedding tie-break to a live embedder/model | `WF2UNI-1`, `WF2UNI-2` | generation.py's codebase_context is populated by a depth-filtered tree + README head + project-metadata synthesis cached per (project_id, tree-hash) with 7d TTL; workflow_plan emits an entity-resolution first node (deterministic lookup + degraded fallback) plus topic extraction feeding the grill; matcher T4 (cached match_embedding, workflows.match_threshold 0.62 tie-breaker) and T5 (summarize-then-rematch re-entering the deterministic scorer) are wired to a live embedder/model in workflow_plan. |
| `WF2UNI-12` | ⬜ | Retire the collapsed planning surfaces: delete legacy chat plan-mode (plan_memory), planning/ module + loop plan-walkthrough, and loop classifiers | `WF2UNI-1`, `WF2UNI-6`, `EXT:LOOPS-EVOLUTION:loop drain / retirement before planning-module deletion` | plan_memory.py and its live call sites (history.py, dashboard/chat_title.py) are removed now that this plan replaces the format (keeping only the live subagent context-budget half); planning/ (runner.py, session.py), loop/plan_walkthrough.py, loop/*_plan_briefs.py, and loop/classify.py + loop/code_classify.py are deleted as loops drain, with their example intents already seeding the eval fixtures. |

## Atom scopes

### `WF2UNI-1` — Matching + classification: no-LLM intent classifier + tiered match_template T1-T5 + routing-fixture CI gate

**Status:** done (PR ##178)

Implementation Effort Session 1; Architecture: Intent Classifier (UP-R12); Tiered Template Matcher (UP-R2); Planner Eval Harness (UP-R13.1)

**Done when:** workflows/intent.py (4-dimension tuple + rigor routing) and workflows/matcher.py (T1-T5, reason strings, negatives, presets, failure-path degradation) are live from workflow_plan; tests/fixtures/planner_routing.json asserted structurally in CI at 100% deterministic-tier accuracy with zero LLM calls; 18 bundled templates annotated with typed match metadata on DefMetadata.

### `WF2UNI-2` — Grounded from-scratch generation: bundle from live registries + pattern-shape registry + schema-constrained oneOf emission + self-check/repair

**Status:** done (PR ##179)

Implementation Effort Session 2; Architecture: From-Scratch Generation (UP-R1); Pattern Documents (UP-R15); Planner Eval Harness (UP-R13.2)

**Done when:** workflows/grounding.py (three signature-discovery tiers, orient-then-drill, MCP servers named), workflows/patterns.py (seven proven shapes each with a stopping condition), workflows/generation.py (generated prompt, mechanical self-check, oneOf/cannot_plan decline path, repair_prompt) are wired into workflow_plan; the grounding A/B harness scores ungrounded 0/5 and grounded >=4/5 with validation-failures and silent-misses as separate modes.

### `WF2UNI-3` — Stage contracts + parameterization: resolve_unfilled_inputs/template_types + extraction contract + done-means lint + preflight + decision typing

**Status:** done (PR ##181)

Implementation Effort Session 3; Stage Contracts (UP-R3); Template Parameterization (UP-R8); Approval as an Autonomy Mode-Switch (UP-R16 blocking/open); From-Scratch Generation (preflight)

**Done when:** workflows/contracts.py provides resolve_unfilled_inputs(), template_types(), the extraction contract, per-stage done-means contracts with the minimal-triple lint (with the two measured exemptions), and mechanical blocking-vs-open decision typing; workflows/preflight.py emits the preflight step; all wired into workflow_plan's template path as the review surface.

### `WF2UNI-4` — Review + revision: typed merge-by-id patches + NO_UPDATE sentinel + TTL'd draft sketches + announce-block surface + plan-as-markdown

**Status:** done (PR ##182)

Implementation Effort Session 4; Plan Revision Gate (UP-R7); Plan Review UX (announce-block, ranked alternatives, inferred-chips, cost estimate)

**Done when:** workflows/revision.py implements merge-by-id patch semantics (absent-preserved is structural; replace/add refusals deliberate), the NO_UPDATE sentinel, TTL'd sketches with a tombstone set, the announce-block review header applying the same lint exemptions, and a counts-only cost estimate; wired into both workflow_plan paths.

### `WF2UNI-5` — Autonomy + risk: risk-signal registry + autonomy floors/offers + HITL/AFK typing -> require_hitl + confirmation matrix + earned trust

**Status:** done

Implementation Effort Session 5; Approval as an Autonomy Mode-Switch (UP-R4/UP-R16); Earned Autonomy (UP-R6)

**Done when:** workflows/autonomy.py provides the action-shaped risk-signal registry (reusing engine RiskLevel, each signal stating a consequence, false-positives pinned by regression tests), autonomy floors/offers, HITL/AFK typing compiled to require_hitl, the confirmation matrix + three interrupts, and earned-trust (report-only first runs, reset-on-failure, combined commitment stamp); _autonomy_surface ships from _plan.

### `WF2UNI-6` — Grill + rigor axis + template-pipeline & eval-spec modules (built, pure): structured deep-rigor protocol, fast-path/Specify, mining/scrubbing, per-template evals

**Status:** done

Implementation Effort Session 6; Mid-Planning Interrogation (UP-R5); The Rigor Axis (UP-R10); User-Created Templates (UP-R9); Planner Eval Harness (UP-R13.3)

**Done when:** workflows/grill_protocol.py (recommendation-bearing question rounds, facts-vs-decisions channel split, stress-test, Step-0, frozen prohibitions, SaveFn persistence), workflows/rigor.py (fast-path alias set, refinement-after-first-output gate, append-only acceptance ratchet), workflows/template_pipeline.py (mining, discover-then-freeze, suggest_template nudge, entity scrubbing) and workflows/eval_specs.py exist and are unit-tested; _plan gains rigor_note + fast-path gate + _grill_surface (running scan_risk over the tree).

### `WF2UNI-7` — Wire the S45 template pipeline into production: source_session_id mining, discover-then-freeze candidate templates, suggest_template registration, eval_specs importer

**Status:** todo

User-Created Templates (UP-R9); Planner Eval Harness (UP-R13.3); Changes to WORKFLOWS-V2.md items 1 & 3; Execution log S45 NOT DONE

**Done when:** workflow_plan accepts source_session_id and resolves a session id to sessions/<sid>.jsonl (via session_map.py) feeding mine_session; discover-then-freeze persists LLM-generated specs as SESSION-scoped candidate templates loaded back through the tiered matcher; suggest_template is registered as a local-only chat tool with a persisted anti-nag NudgeState; eval_specs is imported by a live surface so per-template benchmarks are produced (grading deferred to LEARNING-FLYWHEEL).

### `WF2UNI-8` — revise{step_ref, comment} as a workflow_resume answer verb (span-scoped re-plan re-invoking the planner on one node)

**Status:** todo

Plan Revision Gate (UP-R7); Changes to WORKFLOWS-V2.md item 4; Execution log carried-forward 3x (S43/S44/S45 NOT DONE)

**Done when:** workflow_resume's answer grammar accepts revise{step_ref, comment} and routes to revision.py's merge-by-id patch scoped to exactly one node (comment_step's awaiting_review->running semantics); the approved prose artifact on disk matches what runs; carried-forward-three-times item retired.

### `WF2UNI-9` — Watched-scratchpad intake (Success Criterion 9): periodic scan proposes plans into the needs-input inbox, never auto-executed, with source backlink + dedup

**Status:** todo

Planner Entry Surfaces (UP-R18); Provider & Config Integration (watched scratchpad scan); Success Criteria 9

**Done when:** a file-watch/interval automation trigger scans the configured planning.scratchpad_path, runs each actionable line through the triage gate + intent classifier, and lands a PROPOSED plan via InboxService with a backlink to the source line; dedup by content-hash + seen-line tracking; checked/struck lines ignored; planning.scratchpad_path wired through the 5-point config contract.

### `WF2UNI-10` — Frontend review surface: QuestionSlider/ask() stepper widget + streaming multi-view render + new SSE events into RUN_LIFECYCLE + small-model naming call

**Status:** todo

Plan Review UX (streaming synchronized views); Mid-Planning Interrogation (QuestionSlider widget); Provider & Config Integration (new SSE events); Execution log S43/S45 NOT DONE

**Done when:** the deep-rigor Round renders as a QuestionSlider stepper (typed kinds, one-at-a-time, custom-answer escape hatch, single Submit); plan review streams progressively (buffer-append re-parse, shimmer on in-flight steps) across proposal cards + read-only graph + JSON; new plan-streaming/revision/confirmation/demotion SSE events are added to the RUN_LIFECYCLE union in web/src/pages/loops/useRunStream.ts; the {title,description,per-step labels} naming call is wired with deterministic fallbacks.

### `WF2UNI-11` — Populate the grounding inputs: brownfield context pass (UP-R17), entity/topic preamble (UP-R14), and wire T4/T5 embedding tie-break to a live embedder/model

**Status:** todo

Architecture: Grounding Preamble (UP-R14); Brownfield Context Pass (UP-R17); Tiered Template Matcher (T4/T5); Execution log S40 & S41 NOT DONE; Success Criteria 2

**Done when:** generation.py's codebase_context is populated by a depth-filtered tree + README head + project-metadata synthesis cached per (project_id, tree-hash) with 7d TTL; workflow_plan emits an entity-resolution first node (deterministic lookup + degraded fallback) plus topic extraction feeding the grill; matcher T4 (cached match_embedding, workflows.match_threshold 0.62 tie-breaker) and T5 (summarize-then-rematch re-entering the deterministic scorer) are wired to a live embedder/model in workflow_plan.

### `WF2UNI-12` — Retire the collapsed planning surfaces: delete legacy chat plan-mode (plan_memory), planning/ module + loop plan-walkthrough, and loop classifiers

**Status:** todo

Planning Surfaces Collapsed by This Plan; Execution log S40 DEVIATION (plan_memory still imported live)

**Done when:** plan_memory.py and its live call sites (history.py, dashboard/chat_title.py) are removed now that this plan replaces the format (keeping only the live subagent context-budget half); planning/ (runner.py, session.py), loop/plan_walkthrough.py, loop/*_plan_briefs.py, and loop/classify.py + loop/code_classify.py are deleted as loops drain, with their example intents already seeding the eval fixtures.

