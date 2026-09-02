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
| `WF2UNI-7` | ✅ | Wire the S45 template pipeline into production: source_session_id mining, discover-then-freeze candidate templates, suggest_template registration, eval_specs importer | `WF2UNI-6`, `EXT:WORKFLOWS-V2-LEARNING-FLYWHEEL:judge/grading for eval-spec graded_checks` | workflow_plan accepts source_session_id and resolves a session id to sessions/<sid>.jsonl (via session_map.py) feeding mine_session; discover-then-freeze persists LLM-generated specs as SESSION-scoped candidate templates loaded back through the tiered matcher; suggest_template is registered as a local-only chat tool with a persisted anti-nag NudgeState; eval_specs is imported by a live surface so per-template benchmarks are produced (grading deferred to LEARNING-FLYWHEEL). |
| `WF2UNI-8` | ✅ | revise{step_ref, comment} as a workflow_resume answer verb (span-scoped re-plan re-invoking the planner on one node) | `WF2UNI-4`, `EXT:WORKFLOWS-V2:workflow_resume answer-grammar / engine resume+run-from surface` | workflow_resume's answer grammar accepts revise{step_ref, comment} and routes to revision.py's merge-by-id patch scoped to exactly one node (comment_step's awaiting_review->running semantics); the approved prose artifact on disk matches what runs; carried-forward-three-times item retired. |
| `WF2UNI-9` | ✅ | Watched-scratchpad intake (Success Criterion 9): periodic scan proposes plans into the needs-input inbox, never auto-executed, with source backlink + dedup | `WF2UNI-1`, `EXT:AUTOMATION-SUBSTRATE:file-watch/interval trigger + run-workflow action`, `EXT:INBOX-NOTIFICATIONS-UNIFICATION:InboxService/emit_attention_item proposal surface` | a file-watch/interval automation trigger scans the configured planning.scratchpad_path, runs each actionable line through the triage gate + intent classifier, and lands a PROPOSED plan via InboxService with a backlink to the source line; dedup by content-hash + seen-line tracking; checked/struck lines ignored; planning.scratchpad_path wired through the 5-point config contract. |
| `WF2UNI-10` | ✅ | Frontend review surface: QuestionSlider/ask() stepper widget + streaming multi-view render + new SSE events into RUN_LIFECYCLE + small-model naming call | `WF2UNI-4`, `WF2UNI-6` | the deep-rigor Round renders as a QuestionSlider stepper (typed kinds, one-at-a-time, custom-answer escape hatch, single Submit); plan review streams progressively (buffer-append re-parse, shimmer on in-flight steps) across proposal cards + read-only graph + JSON; new plan-streaming/revision/confirmation/demotion SSE events are added to the RUN_LIFECYCLE union in web/src/pages/loops/useRunStream.ts; the {title,description,per-step labels} naming call is wired with deterministic fallbacks. |
| `WF2UNI-11` | ✅ | Populate the grounding inputs: brownfield context pass (UP-R17), entity/topic preamble (UP-R14), and wire T4/T5 embedding tie-break to a live embedder/model | `WF2UNI-1`, `WF2UNI-2` | generation.py's codebase_context is populated by a depth-filtered tree + README head + project-metadata synthesis cached per (project_id, tree-hash) with 7d TTL; workflow_plan emits an entity-resolution first node (deterministic lookup + degraded fallback) plus topic extraction feeding the grill; matcher T4 (cached match_embedding, workflows.match_threshold 0.62 tie-breaker) and T5 (summarize-then-rematch re-entering the deterministic scorer) are wired to a live embedder/model in workflow_plan. |
| `WF2UNI-12` | ✅ | Retire the collapsed planning surfaces: delete legacy chat plan-mode (plan_memory), planning/ module + loop plan-walkthrough, and loop classifiers | `WF2UNI-1`, `WF2UNI-6`, `EXT:LOOPS-EVOLUTION:loop drain / retirement before planning-module deletion` | plan_memory.py and its live call sites (history.py, dashboard/chat_title.py) are removed now that this plan replaces the format (keeping only the live subagent context-budget half); planning/ (runner.py, session.py), loop/plan_walkthrough.py, loop/*_plan_briefs.py, and loop/classify.py + loop/code_classify.py are deleted as loops drain, with their example intents already seeding the eval fixtures. |
| `WF2UNI-14` | ⬜ | Retire legacy planning modules after loop drain (split from WF2UNI-12 per ruling) | `WF2UNI-12` | planning/ (runner.py, session.py), loop/plan_walkthrough.py, loop/*_plan_briefs.py, and loop/classify.py + loop/code_classify.py are deleted as loops drain, with their example intents already seeding the eval fixtures. BLOCKED on the WORKFLOWS-V2-LOOPS-EVOLUTION Phase-4 loop drain. |
| `WF2UNI-13` | ✅ | Give the unattended-interrupt taxonomy a producer and a reader, delete the member that had no signal, and ratchet both enums | `WF2UNI-5` | `should_interrupt` has a live production caller (`unattended_interrupts`, emitted by `_autonomy_surface` as the plan preview's `unattended_interrupts` key); `UNINFERABLE` is produced from the `credentials_or_payment` registry signal now carried by name on `ConfirmationRequest.signals`; `CONFLICTING` is DELETED (no signal a `ConfirmationRequest` carries says "requirements contradict") and both "only three interrupts" claims are corrected; `should_interrupt` names every `ConfirmationType` member and RAISES on an unmapped one; an AST ratchet fails when any `Interrupt` member has no producer; `enum:Interrupt.UNINFERABLE` and `enum:Interrupt.CONFLICTING` both leave `inert-surface-baseline.json`; enforcement is UNCHANGED and the relaxation this atom refused is recorded as an owner decision |

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

**Status:** done

User-Created Templates (UP-R9); Planner Eval Harness (UP-R13.3); Changes to WORKFLOWS-V2.md items 1 & 3; Execution log S45 NOT DONE

**Done when:** workflow_plan accepts source_session_id and resolves a session id to sessions/<sid>.jsonl (via session_map.py) feeding mine_session; discover-then-freeze persists LLM-generated specs as SESSION-scoped candidate templates loaded back through the tiered matcher; suggest_template is registered as a local-only chat tool with a persisted anti-nag NudgeState; eval_specs is imported by a live surface so per-template benchmarks are produced (grading deferred to LEARNING-FLYWHEEL).

### `WF2UNI-8` — revise{step_ref, comment} as a workflow_resume answer verb (span-scoped re-plan re-invoking the planner on one node)

**Status:** done

Plan Revision Gate (UP-R7); Changes to WORKFLOWS-V2.md item 4; Execution log carried-forward 3x (S43/S44/S45 NOT DONE)

**Done when:** workflow_resume's answer grammar accepts revise{step_ref, comment} and routes to revision.py's merge-by-id patch scoped to exactly one node (comment_step's awaiting_review->running semantics); the approved prose artifact on disk matches what runs; carried-forward-three-times item retired.

### `WF2UNI-9` — Watched-scratchpad intake (Success Criterion 9): periodic scan proposes plans into the needs-input inbox, never auto-executed, with source backlink + dedup

**Status:** done

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

### `WF2UNI-13` — Give the unattended-interrupt taxonomy a producer and a reader, delete the member that had no signal, and ratchet both enums

**Status:** done

Architecture: the confirmation matrix + the three interrupts (shipped by `WF2UNI-5`, which this atom corrects); `autonomy.py`'s "Three interrupts, and only three" module claim

**Done when:** `should_interrupt` has a live production caller (`unattended_interrupts`, emitted by `_autonomy_surface` as the plan preview's `unattended_interrupts` key); `UNINFERABLE` is produced from the `credentials_or_payment` registry signal now carried by name on `ConfirmationRequest.signals`; `CONFLICTING` is DELETED and both "only three interrupts" claims are corrected; `should_interrupt` names every `ConfirmationType` member and RAISES on an unmapped one; an AST ratchet fails when any `Interrupt` member has no producer; `enum:Interrupt.UNINFERABLE` and `enum:Interrupt.CONFLICTING` both leave `inert-surface-baseline.json`; enforcement is UNCHANGED and the relaxation this atom refused is recorded as an owner decision

**Design**

`Interrupt`'s docstring called its members "the only three things that stop an unattended run", and
**nothing in production consulted any of them.** `should_interrupt` was the only producer, it had no
caller outside tests, and it could only ever return `IRREVERSIBLE` — `UNINFERABLE` and `CONFLICTING`
were produced nowhere and `inert-surface-baseline.json` listed both. So the taxonomy documented three
stops of which two could not happen and one never ran.

**The measurement came first, because wiring a guardrail may only ADD stops.** What stops an
unattended run today, at the only seam that enforces anything:

| Gate's declared `risk` | `gate_policy.decide` verdict for a trigger-origin run |
|---|---|
| `safe` | `AUTO_APPROVED` — proceeds |
| `caution` | `AUTO_APPROVED` — proceeds |
| `destructive` | `ASK` — stops |
| absent / unparseable | `gate_risk` defaults to `DESTRUCTIVE` ⇒ `ASK` — stops |

So today's answer is neither "stop for everything" nor "stop for nothing": **an unattended run stops
for a DESTRUCTIVE-risk gate node and proceeds through everything else.** That decision is
`gate_policy.decide`, called once from `controller._apply`'s `WAITING` branch, on the engine's
`RiskLevel` × `OriginKind` vocabulary.

**The two layers never meet, and that is deliberate.** `autonomy.py` speaks `Mode` ×
`ConfirmationType`; the engine speaks `RiskLevel` × `OriginKind`. `autonomy.Mode` never reaches the
run path at all — `run.mode` is `blocking | background`, an unrelated axis. The whole autonomy
compilation chain (`scan_risk` → `type_attention` → `compile_require_hitl` → `build_confirmations`)
terminates in `_autonomy_surface`'s plan-preview payload; `require_hitl`, described in-module as "the
ONE uniform engine target", has no engine reader either. `_autonomy_surface`'s own docstring already
states the asymmetry: "the ENGINE's own gate policy still governs what actually runs, so a failure
here loses advice, never enforcement."

**So this atom wires the taxonomy where it belongs — the advisory chain — and does NOT give it
teeth.** The reasoning, in the failure direction:

* *As a replacement for `gate_policy.decide`:* a **severe relaxation, refused.** `gate_risk` defaults
  an undeclared gate to DESTRUCTIVE (⇒ ASK), while `_classify_node` types a `gate` node as
  `READ`/`SAFE` (its `kind` matches none of action/transform/stage/infer), so `should_interrupt`
  would return `False` and **every unclassified gate in an unattended run would newly
  auto-approve.** That is precisely the failure `gate_risk`'s deny-by-default exists to prevent.
* *As an additive veto layered after `gate_policy` (approve → ask only, never the reverse):* safe in
  direction but **inert by construction** — gate nodes classify as `READ`, so the veto never fires. A
  live reader of a signal nothing writes is the worst available shape.
* *At the action-dispatch seam, stopping unattended runs before outward actions:* the one genuine
  behavioural delta in the taxonomy (`should_interrupt` stops for `OUTWARD` at CAUTION; `gate_policy`
  auto-approves CAUTION). Additive, and real. **But it is an owner decision, not an implementer's** —
  it contradicts this module's own rule that autonomy machinery must not grow a second enforcement
  path, and it changes unattended semantics for every user template that posts or notifies on a
  schedule: those runs would newly park for an approval nobody is watching for. Zero bundled
  templates use an outward provider, so the shipped library measures clean, but user templates are
  not measurable from here. Recorded as an E4 owner decision instead of taken.

**What ships is the counterfactual the offer surface was missing.** Every risk signal caps autonomy
at `per_stage`, so `offer_autonomy` routinely *recommends* `per_stage` while still *offering*
`unattended` — and `build_confirmations` is computed at the recommended mode. The preview therefore
listed the stops for a mode the user might not pick and said nothing about the one being offered.
`unattended_interrupts` answers it per confirmation: which of these stops survive at unattended, and
which become journaled assumptions. That is the informed consent the rest of the module insists on,
and being advisory it cannot relax anything — it reports on confirmations the plan already raises and
changes no verdict.

**`UNINFERABLE` gets an honest producer; `CONFLICTING` is deleted.**

* `UNINFERABLE` ("a credential or a product decision nobody can guess") — the registry already has
  `credentials_or_payment`, which *is* "a credential". The obstacle was that `_classify_node`
  collapses every DESTRUCTIVE-level signal into the same `(DESTRUCTIVE, DESTRUCTIVE)` pair, so the
  request reaching `should_interrupt` no longer knew which signal fired. Fixed by carrying the
  registry signal names on `ConfirmationRequest.signals` — a canonical registry value threaded
  through, not a text scan of the question. **Label-only and provably non-relaxing:**
  `credentials_or_payment` is DESTRUCTIVE-level, so such a request already stopped on risk alone;
  checking the signal first changes which interrupt is reported, never whether the run stops. Worth
  the distinction anyway — "this cannot be undone" tells a user to review the blast radius, "nobody
  can guess this value" tells them to supply it, and reporting the second as irreversible sends them
  looking for a blast radius that is not the problem.
* `CONFLICTING` ("requirements that contradict each other") — **deleted.** Nothing a
  `ConfirmationRequest` carries expresses it, and manufacturing one by scanning question text is a
  heuristic, not a signal. The one real contradiction this module detects — a template
  `autonomy_floor` above the risk ceiling, in `offer_autonomy` — is resolved at PLAN time by letting
  the floor win and recording it in `capped_by`, so it never reaches a run to stop it and would be
  the wrong semantics for an interrupt. Both "only three" claims (the module docstring and the enum
  docstring) are corrected to two rather than left describing a stop that cannot happen.

**Two ratchets, because this atom's whole subject is a declared-but-unproducible member.**
`should_interrupt` is exhaustive over `ConfirmationType` with a raising tail — the dangerous default
there is `return False`, so a new type that fell through the old tail would have been waved through
an unattended run. And an AST read asserts every `Interrupt` member is named in `should_interrupt`,
which is the ratchet that would have caught this finding at birth.

**Implementation plan**

1. `autonomy.py`: add `ConfirmationRequest.signals: tuple[str, ...]`, surfaced in `to_dict()`;
   populate it in `build_confirmations` from the node's `RiskHit`s.
2. `autonomy.py`: delete `Interrupt.CONFLICTING`; correct the module docstring's "Three interrupts,
   and only three" and the enum docstring; state the advise-vs-enforce split in the module docstring
   pointing at `gate_policy` and `compile_require_hitl`.
3. `autonomy.py`: in `should_interrupt`, add the `UNINFERABLE` branch keyed on
   `credentials_or_payment` (before the risk check — label-only), give `DESTRUCTIVE`/`OUTWARD`/
   `SPEND`/`READ`/`WRITE` each an explicit named branch, and replace the permissive tail with a
   raising one.
4. `autonomy.py`: add `unattended_interrupts(confirmations)` running the taxonomy at
   `Mode.UNATTENDED` over the confirmations the plan already raises.
5. `mcp_workflows.py`: emit `unattended_interrupts` from `_autonomy_surface`.
6. Tests: drive the producer from a spec through `build_confirmations` (not a hand-built request),
   assert the payload through `_autonomy_surface`, pin the non-relaxation with a
   signals-stripped comparison, and add the two ratchets plus a proof each can fail.
7. Regenerate `inert-surface-baseline.json` with `scripts/generate_inert_surface_baseline.py`.

