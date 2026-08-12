# WORKFLOWS-V2-LOOPS-EVOLUTION

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/WF2LOO.md`](../atomic/WF2LOO.md) as 13 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Loop Kinds Evolution — Loops as Workflow Templates

**Status:** IN PROGRESS — sessions 29-33 shipped (PRs #168-#172, on `main`): judge contract,
pre-tier, actors, loop middleware, calibration, aliases, and 5 of 8 templates.
🔴 **BUT THE RUN-PATH INTEGRATION IS INERT** — `workflows/{loop_middleware,judge_calibration,
judge_pretier,judge_actors}.py` have **ZERO production importers** (AST audit 2026-08-04); the
cluster is self-referential, nothing emits `judge_verdict`/`judge_divergence`, and the steering queue
is stored but never consumed. The plan's own log admits this four times. Criteria 2/3/8/9/11 are
unmet until one wiring session lands. The legacy `src/personalclaw/loop/` remains the live engine.
Status corrected 2026-08-04 by code audit. (rev 2 — research-integrated 2026-07-12)

---

## Research Integration (2026-07-12)

Fifteen approved recommendations folded in (IDs only; section where each landed):

- **LOOP-R1** — Judge Design: Maker/Checker Teeth (judge stages become act-capable, isolated, adversarial; the headline fix)
- **LOOP-R2** — Judge Design: Typed Verdict Contract (+ Migration Checklist row)
- **LOOP-R3** — Runtime Hints: rubric convergence contract; Migration Checklist: judge-verdict ledger + hardening loop
- **LOOP-R4** — Engine Behaviors: no-progress circuit breaker + escalation ladder (+ Migration Checklist row)
- **LOOP-R5** — Per-Kind Templates §4: code-project structural gates (initializer, WIP=1, repro-before-edit, baseline, dual verify+guard)
- **LOOP-R6** — Migration Path Phases 3-4: retirement acceptance criteria (nodding-loop detector, five-moves audit, provenance tags)
- **LOOP-R7** — Migration Checklist: fresh-session retry = structured handoff + worker lifecycle protocol + executor capability flags
- **LOOP-R8** — Judge Design: deterministic verification tier before and beneath every LLM judge
- **LOOP-R9** — Runtime Hints: orthogonal split + depth/freedom/model-tier/reasoning-budget axes + judge-tier invariant
- **LOOP-R10** — Migration Path: coexistence aliases, template metadata hygiene, cockpit live-follow key equivalence
- **LOOP-R11** — Per-Kind Templates §§2/4/5: design divergence, test triage, TDD doctrine, diff-edit refine
- **LOOP-R12** — Per-Kind Templates §6 (new): bundled `diagnose` template (4-layer failure taxonomy over the Run Ledger)
- **LOOP-R13** — Migration Checklist: reactive compaction with one-shot guard + proactive topic-segmented compression
- **LOOP-R14** — Engine Behaviors: mid-run human steering channel (interrupt queue, judge-comment triage, per-project judge-prompt overrides)
- **LOOP-R15** — Per-Kind Templates §1 (monitor variant): bounded loop self-scheduling via automation-substrate triggers

---

## Overview

All five loop kinds (goal, research, general, code, design) evolve into **bundled workflow templates** that compose the standard Workflows v2 node types. This replaces the need for a separate loop-kind-strategy engine with the universal workflow execution platform while preserving the domain intelligence that makes each kind valuable.

**Key decision:** Loops remain operational as-is during the transition period. The workflow templates are the NEW way to express the same patterns; existing loops are NOT forcibly migrated. Over time, users create workflow-based projects instead of loops, and the old loop engine becomes legacy.

**Soul check:** everything here stays personal-scale — one user, local files, local SQLite, no fleet machinery. Self-scheduling is bounded by construction; learning artifacts are *proposed*, never silently written.

---

## Reality Baseline (recon-verified 2026-07-12)

The plan is grounded on what actually exists, not what earlier drafts assumed:

- **The loop engine is already pluggable.** `loop/kinds/__init__.py` defines a `LoopKindStrategy` Protocol + registry — a new kind is one module + `register()`, no engine edits. What templates buy is not "escaping a monolith" but *user-composable graphs*: today's kinds are closed Python strategies; templates are open, editable node trees.
- **`tick.evaluate` is NOT the watchdog's brain.** The pure decision core (`loop/tick.py`) is consumed ONLY by the sdlc kind (`kinds/sdlc.py:_tick_decide`). Goal/general kinds use judge/verify signals directly. Any "port tick to the engine" claim scopes to the sdlc→code-project mapping only.
- **The real loop judge is already ground-truth-injecting and adversarial.** `loop/judge.py` runs `assess_cycle` + `assess_cycle_skeptic` and `adjudicate()` requires TWO yeses for done (one suffices for regressed); `_observe_ground_truth` re-runs the verify command and reads deliverable files into the judge prompt; judge sessions get NO write tools (permission requests rejected). The bundled templates must MATCH this bar, not water it down (see Judge Design below — rev 1 shipped `tools_posture: none` judges, contradicting its own checklist).
- **Judge calibration has real numbers.** `loop/granularity.py` maps the dial to (threshold T, window N): quick 3.0/1, balanced 2.0/2, exhaustive 1.0/3 on the 0-5 `marginal_value` scale; calibrated bands need ≥4 samples; `loop/instrument.py` ships a `probe_judge` canary (strong-vs-null separation ≥1.5) and a `judge_blind` NEEDS_INPUT halt. Rev 1's example values (`marginal_threshold: 0.3, streak_for_done: 5`) matched neither scale — corrected below.
- **Worker trust is three-fold**: `session._trust=True` + `sessions.set_approval_policy(key, "auto")` + `session._unattended` (plus `acp_mode="bypassPermissions"` for unattended ACP workers). Miss one and runs stall on approvals (loop/manager.py:start). Template runs must set all three the same way.
- **The behaviors at risk in migration live in `gateway._fire` + the watchdog**, not in plan structure: deliverable-forcing re-prompt with fresh-ACP-session retry (`_MAX_CYCLE_REPROMPTS`), autonudge as the cycle ticker, trust-TTL expiry, stagnation windows, `reap_orphaned_loops` boot recovery. The Migration Checklist below names every one.
- **No workflow execution engine exists today.** `workflows/` is definitions + surfacing + checklist injection (`workflow_run` returns text; the agent executes). Everything below assumes WORKFLOWS-V2 Slices 0-2 deliver the run engine first.

### Where the new pieces plug into the provider architecture

Provider fidelity is non-negotiable; every new piece uses an existing seam:

| New piece | Real seam |
|---|---|
| The 8 bundled templates | Bundled workflow definitions synced by `NativeWorkflowProvider` from `workflows/bundled/` (workflows/native.py) — same path existing SOP starters use. Third parties can ship template packs as `type: workflow` provider apps (→ `workflows/registry.py` via the workflow `_TypeHandler`) |
| `set_onetime_task` / `set_recurring_task` tools (R15) | New tool module registered in `mcp_core._TOOL_MODULES` + `tool_providers/registry.py` (same pattern as `mcp_workflows.py`); the triggers themselves are AUTOMATION-SUBSTRATE entities |
| Judge isolation sessions (R1) | `SubagentManager.spawn()` with the internal-secret `approval_mode="auto"` path; judge model resolution via the **`reasoning`** use-case axis (`llm_helpers.one_shot_completion`) — NOT chat/code_tools, which return the NativeAgentRuntime |
| Judge-verdict ledger events (R3) | Run Ledger events (WORKFLOWS-V2 data model). Consumed by LEARNING-FLYWHEEL — this is MEMORY-side (harness mechanics), never written to the knowledge store |
| New SSE event types (steering, breaker-trip, verdict, divergence) | Per-run SSE registry + the FE `RUN_LIFECYCLE` union in `web/src/pages/loops/useRunStream.ts` — EventSource DROPS unregistered event types; every new event MUST be added there |
| Any new config keys (e.g. `workflows.judge_samples_default`, `workflows.self_schedule_max_outstanding`) | The FOUR wiring points: dataclass `_meta`, `AppConfig.load` mapping, `to_dict`, PATCH `_EDITABLE_CONFIG` |
| Any new action provider (none currently planned; R15 uses tools, not hooks) | Would additionally require `ALLOWED_HOOK_PROVIDERS` (validation.py:555) |

---

## Architecture: Templates + Runtime Behavior Layer

### The Problem the Critique Identified

A naive "just make it a template" approach loses the done-ness intelligence that lives in the loop engine today — judge calibration, stagnation heuristics, goal-type-specific exit criteria, and the adaptive cycle-to-cycle nudge prompts. Templates are static structure; loops have runtime intelligence.

### The Solution: Workflow-Level `runtime_hints`

WorkflowDefs gain an optional `runtime_hints` dict (opaque to the engine core, consumed by the `stage` prompt-renderer and a small set of engine-enforced invariants at execution time).

**(R9) The hints split into two ORTHOGONAL groups** — semantic judge calibration vs structural execution shape — so one blob doesn't tangle the axes across 5 kinds:

```python
@dataclass
class WorkflowDef:
    # ... existing fields ...
    runtime_hints: dict = field(default_factory=dict)
```

```yaml
runtime_hints:
  judge:                      # SEMANTIC: what "done/good" means (R1, R2, R3, R8)
    rubric:                   # (R3) machine-checkable convergence contract —
      - {criterion: "...", target_score: 2}   #   fixed 0-2 dimensions, evidence citation per
      - {criterion: "...", target_score: 1}   #   score, hard per-criterion thresholds
    ratchet: strict           # strict | relaxed — any shortfall fails the stage; no averaging
    stop_condition: {consecutive_clean: 2}    # (R3) double-clean rule: exit only after N
                                              # consecutive INDEPENDENT clean judge passes
    marginal_threshold: 2.0   # 0-5 scale, seeded from the REAL granularity dial
                              # (quick 3.0/1, balanced 2.0/2, exhaustive 1.0/3)
    forbidden_success_modes:  # (R1) explicit denylist of degenerate passes the judge must
      - "test deleted or skipped"             # verify did NOT occur; worker tampering with
      - "gate/validation config modified"     # validation config = automatic failure
      - "output stubbed or hardcoded"
    proof_command: "..."      # (R1) exact command the judge independently re-runs; actual
                              # output recorded into the Run Ledger event
    validator_script: "..."   # (R1) judge prefers executing the deliverable's declared
                              # validator over re-reading worker prose
    hidden_validation_commands: ["..."]       # (R1) rendered ONLY into judge prompts, never
                                              # the worker's; fixtures stored where the worker
                                              # has no read path (not just a separate prompt)
    ground_truth_sources: [node_ids]          # (R1) which node outputs are authoritative
                                              # MEASUREMENTS vs worker synthesis — "the LLM is
                                              # a formatter over measured data, never the
                                              # measurement"
    judge_isolation: fresh    # fresh | cross_model (R1) — never the session that produced the
                              # work; provenance-blinded (strip which retry produced the
                              # output); cross_model = different model FAMILY (same-family
                              # judges are a calibration failure mode one knob deeper)
    judge_samples: 3          # (R2) N-sample median aggregation, default 3 for terminal
                              # accept/reject gates (single-run LLM-judge acceptance was
                              # indistinguishable from noise)
    fallback_check: command_exit_code         # (R8) artifact_exists | command_exit_code |
                                              # diff_nonempty — deterministic fallback AND
                                              # standing cross-check; a judge PASS that
                                              # contradicts it auto-escalates
    freedom_level: high       # high | medium | low (R9) — low-freedom templates judged on
                              # per-step compliance; high-freedom on outcome rubric (per-step
                              # judging mis-measures a research loop)

  execution:                  # STRUCTURAL: how the run behaves (R4, R7, R9, R13)
    depth: balanced           # fast | balanced | production (R9) — prunes gates per rigor;
                              # fast mode overriding a high-risk signal requires informed-
                              # consent confirmation
    reasoning_budget: none    # none | N-tokens | unlimited (R9) — mapped per-backend
                              # (Anthropic thinking budgets hosted; logit-processor local)
    escalation:               # (R4) numeric strategy ladder, engine-consumed on breaker trip
      attempt_cap: 3
      ladder: [classified_retry, fresh_session, model_switch, restart_from_scratch, surface]
    failure_mutations:        # (R4) failure_mode → corrective instruction for targeted
      test_timeout: "..."     # classified retries
    breaker:                  # (R4 amendment-5) template-level breaker parameterization —
      fingerprint_window: 3   # engine default applies everywhere; templates may tighten
      no_progress_stop: 5     # (proven values: N=5 consecutive non-improving,
      hypothesis_abandon_after: 3            #  K=3 same-fix failures)
    executor_caps:            # (R7) per-executor capability record so the engine branches
      supports_resume: true   # correctly across PersonalClaw's 3 ACP dialects —
      supports_lifecycle_hooks: false        # fresh-session retry ONLY where resume is
      prompt_delivery: acp    # unsupported
    compaction_prompt: "..."  # (R13) domain-specific compaction guidance, e.g. "keep
                              # decisions and verified state, drop file-level diffs"
    strategy_axes:            # (R9 amendment-5) typed enum vocabulary for monitor/research
      memory_extraction: ...  # templates (memory_extraction, observation_compression,
      observation_compression: ...           # retrieval_strategy, task_decomposition) —
      retrieval_strategy: ... # replaces freeform prompt variation with tested parameters
      task_decomposition: ...
```

These hints flow into stage prompts via bindings (`{{defaults.runtime_hints.judge.rubric}}`) and `transform` expressions — **except** for the small set the engine ENFORCES rather than merely renders (marked below). The engine stays generic; the intelligence lives in the template's prompt engineering, binding wiring, and a handful of engine invariants.

**Engine-enforced invariants (not prompt suggestions):**

1. **(R9c) Judge-tier isolation:** judge/gate nodes never run on the same tier + session that produced the work under judgment. Enforced at node dispatch, using `SubagentManager` for fresh sessions.
2. **(R1 amendment-5) Actor-based state transitions:** the worker actor may transition a run/node only to `waiting`/`review`, NEVER to `done`. The terminal `done` transition is reserved for judge/gate actors — self-approval is impossible by construction, not by prompt doctrine.
3. **(R1 amendment-5) Proof-attachment precondition:** a judge PASS without a cited proof artifact (proof_command output, validator result, or deterministic-check record in the Run Ledger event) is itself invalid — completion records carry proof, always.
4. **(R5f) Expression-definable node success:** success conditions are expressions, not hardcoded exit-0 — reproduction workflows have inverted semantics (`fail_reproduced` IS success).
5. **(R5b) WIP=1 for code templates:** `single_active_feature` is a template rule the engine refuses to violate.

**Per-node `model_tier`:** `deterministic | cheap | standard | judgment` (R9 + amendment-5). `judgment` resolves via the `reasoning` use-case axis (active_models.json), same as today's `eval.LLMJudge`. `deterministic` (below `cheap`) skips LLM planning entirely for schema-validated simple steps — mostly satisfiable via existing `transform`/`action`/`gate` node kinds; template lint flags LLM stages doing deterministic work. **Mode-aware prompt framing** (interactive vs background preamble; background enforces structured output) lets one template serve blocking-chat and trigger-fired modes.

---

## Judge Design: Maker/Checker With Teeth (LOOP-R1, R2, R3, R8)

**This section is the headline fix.** Rev 1 contained an internal contradiction: the Migration Checklist claimed judge ground-truth independence was "PRESERVED BY CONSTRUCTION" while every shipped judge stage carried `tools_posture: none` — no bundled judge could even read the artifact it was supposed to verify. Meanwhile the REAL loop judge (`loop/judge.py`) already re-runs verify commands, reads deliverables (`_observe_ground_truth`), runs a skeptic second opinion, and requires TWO yeses to adjudicate done. The templates must meet or exceed that bar.

### The judge stage contract (every bundled judge)

1. **`tools_posture: verify`** (new posture; see Changes to WORKFLOWS-V2.md): read artifacts + run declared commands; NO write tools — same restriction today's judge sessions have (permission requests rejected, judge.py:354).
2. **Isolated + provenance-blinded** (R1): spawned via `SubagentManager`, never the worker session; which retry/iteration produced the output is stripped. `judge_isolation: cross_model` uses a different model FAMILY as the anti-rubber-stamping option.
3. **Independent proof** (R1): the judge re-runs `proof_command` (actual output → Run Ledger event) and prefers executing `validator_script` over re-reading worker prose. `hidden_validation_commands` render only into the judge prompt; validation fixtures live where the worker has no read path.
4. **Anti-injection evidence assembly** (R1 amendment, R13d): judge evidence transcripts structurally EXCLUDE worker narration blocks — only user/spec messages + tool calls + tool outputs. The worker cannot influence the judge through prose, including prose that survives compaction.
5. **Worker pre-announcement** (R1 amendment): worker prompts pre-announce the judge and its evidence demands, so first-pass quality rises mechanically.
6. **Prompt doctrine** (R1, R3 amendment): skeptical persona; "assume broken until proven otherwise"; explicit anti-leniency ("do not talk yourself into approving"); default-to-finding-3-5-issues; zero-issues-is-a-red-flag; scope-anchored (no luxury requirements beyond spec); anti-fence-sitting on graded scales; expected 2-3 revision cycles as healthy.

### Typed verdict contract (R2)

Judge/gate schemas use a CLOSED verdict enum so loop nodes route on data, not prose:

```yaml
schema:
  reasoning: string        # bounded free text BEFORE the verdict — constraints without
                           # reasoning room measurably hurt accuracy
  verdict: enum [PASS, REJECT, REPLAN, ESCALATE, NEEDS_INPUT]
  scores: {criterion: 0-2, ...}     # per-rubric-dimension, evidence citation each
  evidence_refs: [string]  # node/field/artifact citations
  cannot_judge: string     # typed escape hatch — refusal is parseable, not a parse failure
```

- **REPLAN** produces ONLY the remaining steps given the critique, preserving the frozen-region invariant with a monotonic global index — mid-flight replan gets a typed shape instead of ad-hoc mutation.
- **`judge_samples: N` median aggregation** (default 3) on terminal accept/reject gates. This also carries forward today's dual-judge adjudication (`assess` + `assess_cycle_skeptic`, TWO yeses): a terminal PASS requires the sampled median to pass AND no forbidden-success-mode hit.
- **Constrained decoding** / provider-native json-schema where the judge model supports it; fallback = parse-with-retry-that-re-presents-the-schema.
- **Derivable fields are engine-computed** (R3 amendment): overall score is recomputed server-side from dimension scores with fixed weights; the model's own overall survives only as metadata; derivable fields are null in the judge output schema so engine-computable values cannot drift.

### Deterministic tier before and beneath every judge (R8)

- **A free rule tier runs BEFORE any LLM judge call**: regex/heuristic failure patterns (tool errors, verbal give-ups), mechanical validation (schema/length/forbidden-content — most failures catchable in ~0.3ms), structural pre-checks (referenced files exist, links resolve), and a cheap existence/non-emptiness gate (artifacts/commits produced > 0) that must pass before the expensive judge invocation. Anything rule-solvable never reaches the probabilistic model. Loop judges run every cycle — this is the single biggest token saver in the plan.
- **Every judge/gate node declares `fallback_check`** (artifact_exists | command_exit_code | diff_nonempty): graceful degradation when the judge model is unavailable AND a standing cross-check when it isn't — a judge PASS contradicting the deterministic check auto-escalates.
- **Verification ladder ordering codified**: `verify_command` > artifact/visual check > LLM-judge-as-tiebreaker. Dual scoring (deterministic + LLM, averaged; judge-null falls back to deterministic) with strictly-greater score as the improvement-acceptance criterion for refine loops.
- This is the template-world descendant of `loop/gates.py:run_verify_command` (tristate True/False/None, `audit_bash_command` screen, exit-127→None) — the gate implementation carries over as-is.

### Calibration becomes measurable (R3)

- The rubric contract (in `runtime_hints.judge`, above) replaces rev 1's ad-hoc `judge_calibration: {marginal_value_threshold, streak_for_done}` — whose example values didn't even match the real dial's scale.
- **Free stuck detection** the engine evaluates off journal data: byte-identical scores across N cycles, or N consecutive failed cycles, auto-pauses the run. Zero LLM calls.
- **Every judge verdict persists as a first-class Run Ledger event**, citing the artifact/observation chain inspected; discarded/reverted iterations are recorded (`status=discard`), not just kept ones. This is the (verdict, ground-truth, override) dataset LEARNING-FLYWHEEL needs — free-text verdicts cannot be calibrated or diffed. (Memory-side data: harness mechanics, never the knowledge store.)
- **Judge-hardening loop** (Migration Checklist row): log judge reasoning; emit a `judge_divergence` event whenever the user overrides a verdict; patch the judge prompt at divergence points with few-shot scored exemplars. Expect 3-5 tuning rounds; criteria wording reviewed for steering side-effects. The existing `probe_judge` canary (strong-vs-null separation ≥1.5, `loop/instrument.py`) becomes the template-save-time calibration probe; an uncalibrated judge maps to the same `judge_blind` halt behavior the watchdog has today.

---

## Per-Kind Template Designs

All judge stages below follow the Judge Design contract; YAML shows the goal-pursuit judge in full and elides repetition elsewhere.

### 1. Goal Loop → `goal-pursuit` Template Family

Three variants by goal type: `goal-pursuit-verifiable`, `goal-pursuit-open-ended`, `goal-pursuit-monitor`.

**Structure (open-ended variant):**

```yaml
name: goal-pursuit-open-ended
description: "Pursue an open-ended goal with adaptive depth control"
inputs:
  task: {type: string, required: true, help: "What you want to achieve"}
  granularity: {type: string, default: "balanced", help: "quick|balanced|exhaustive"}
  success_criteria: {type: string, default: ""}
  scope: {type: array, default: [], help: "Directories/files to focus on"}
defaults:
  model: null  # inherits session model
  effort: medium
runtime_hints:
  judge:
    marginal_threshold: 2.0        # balanced dial: 2.0 threshold / window 2 (granularity.py)
    stop_condition: {consecutive_clean: 2}
    freedom_level: high            # outcome rubric, not per-step compliance
    judge_isolation: fresh
    fallback_check: artifact_exists
    forbidden_success_modes: ["deliverable stubbed", "success_criteria reworded to fit output"]
  execution:
    depth: balanced
    escalation: {attempt_cap: 3, ladder: [classified_retry, fresh_session, surface]}
root:
  kind: sequence
  children:
    - id: intake
      kind: stage
      label: "Understand and decompose"
      prompt: |
        Analyze this goal and produce sub-goals and an execution plan.
        Goal: {{inputs.task}}
        Success criteria: {{inputs.success_criteria}}
        Scope: {{inputs.scope}}
        NOTE: your work will be judged by an independent verifier that re-runs
        commands and reads artifacts directly. Cite evidence for every claim.
      schema: {sub_goals: [string], execution_plan: [{phase: string, objective: string}]}

    - id: work
      kind: loop
      mode:
        until_dry: {streak: 2, progress_field: "new_findings_count"}  # balanced window=2
      body:
        kind: sequence
        children:
          - id: cycle
            kind: stage
            label: "Work cycle"
            prompt: |
              Goal: {{inputs.task}}
              Plan: {{nodes.intake.output.execution_plan}}
              Previous findings: {{nodes.work.output | slice(-3) | json}}
              Do ONE meaningful step. Report what you found/did WITH evidence
              (file paths, command output). An independent judge will verify.
            schema:
              summary: string
              key_insight: string
              new_findings_count: integer
              evidence: string
            effort: medium
            tools_posture: full

          - id: judge
            kind: stage
            label: "Verify progress (independent)"
            prompt: |
              You are a skeptical verifier. Assume the cycle produced nothing
              until proven otherwise. Do not talk yourself into approving.
              Goal: {{inputs.task}}
              Rubric: {{defaults.runtime_hints.judge.rubric}}
              Forbidden passes: {{defaults.runtime_hints.judge.forbidden_success_modes}}
              Read the actual artifacts under {{inputs.scope}}. Never trust the
              worker's summary — verify the evidence it cites.
            schema:
              reasoning: string
              verdict: enum [PASS, REJECT, REPLAN, ESCALATE, NEEDS_INPUT]
              marginal_value: number   # 0-5, same scale as loop/judge.py CycleVerdict
              evidence_refs: [string]
              cannot_judge: string
            model_tier: judgment       # reasoning-axis model
            effort: low
            tools_posture: verify      # read artifacts + run declared commands; NO writes
            isolation: fresh           # engine-enforced: never the worker's session

    - id: deliverable
      kind: stage
      label: "Synthesize results"
      prompt: |
        Synthesize all findings from the goal pursuit into a clear deliverable.
        Goal: {{inputs.task}}
        All cycle outputs: {{nodes.work.output | flatten}}
      schema: {report: string, key_findings: [string], recommendations: [string]}

    - id: accept
      kind: gate
      label: "Terminal acceptance"
      gate_kind: judge
      config: {judge_samples: 3}       # median of 3 on terminal accept/reject (R2c)
```

**Verifiable variant** adds a `gate{kind: verify_command}` after the work loop (the descendant of goal-kind `run_verify_command` + the multi-sub-goal judge):

```yaml
    - id: verify
      kind: gate
      label: "Verify success"
      gate_kind: verify_command
      config:
        command: "{{inputs.verify_command}}"
        cwd: "{{inputs.scope[0]}}"
      on_timeout: pause_run
```

**Monitor variant (R15)** — no longer a degenerate `counted{n: max_cycles}` busy-loop. Monitoring becomes a **parked run + self-created clock trigger**:

- The run gains tool actions `set_onetime_task(when, payload)` and `set_recurring_task(schedule, payload)` — a new tool module (registered via `mcp_core._TOOL_MODULES` / `tool_providers/registry.py`, same pattern as `mcp_workflows.py`) that creates triggers in the AUTOMATION-SUBSTRATE which re-enter the SAME run (the write-side counterpart of AUTO-R11's resume-targets) or spawn a linked follow-up run carrying prior findings as context.
- A monitor cycle that finds nothing new schedules its next check ("check back in 2h") and the run parks instead of burning cycles. Survives restarts via the substrate's persistence — replacing what monitor loops get today from autonudge + `reap_orphaned_loops`.
- **Safety bounds are first-class**: max outstanding self-created triggers per run (default 3, config `workflows.self_schedule_max_outstanding` — four wiring points); mandatory TTL on every self-created trigger; provenance tag (creating run id) listed in the run cockpit; creation obeys the run's autonomy mode (unattended runs self-schedule within bounds; per-stage runs surface it as an approval). Bounded by construction — personal-scale, not self-replicating job machinery.
- The monitor variant parameterizes its observation behavior via `runtime_hints.execution.strategy_axes` (typed enums for memory_extraction / observation_compression / retrieval_strategy / task_decomposition) and may tighten the breaker's fingerprint window (R4 amendment-5).
- Preserves the goal-kind monitor semantics: never self-completes; `budget_stop_genuine` carries over as a hint the terminal gate reads.

### 2. Research Loop → `deep-research` Template

```yaml
name: deep-research
description: "Multi-source research with adversarial verification and synthesis"
inputs:
  question: {type: string, required: true}
  depth: {type: string, default: "standard", help: "shallow|standard|deep|exhaustive"}
  sources: {type: array, default: [], help: "Seed URLs or topics to explore"}
runtime_hints:
  judge:
    freedom_level: high          # outcome rubric — per-step judging mis-measures research
    judge_isolation: cross_model # claims verified by a different model family
  execution:
    compaction_prompt: "Keep verified claims + source URLs + open questions; drop raw page text."
root:
  kind: sequence
  children:
    - id: scope
      kind: stage
      label: "Scope the research"
      prompt: "Break this research question into sub-questions and identify search strategies: {{inputs.question}}"
      schema: {sub_questions: [string], search_strategies: [string], verification_criteria: [string]}

    - id: gather
      kind: loop
      mode:
        until_dry: {streak: 3, progress_field: "new_sources_found"}
      body:
        kind: sequence
        children:
          - id: search
            kind: parallel
            join: all
            max_concurrency: 3
            children:
              - id: web-search
                kind: stage
                prompt: "Search for: {{nodes.scope.output.sub_questions[iter % sub_questions.length]}}"
                schema: {sources: [{url: string, title: string, relevance: string}], new_sources_found: integer}
              - id: deep-read
                kind: stage
                prompt: "Deep-read the most promising unread source and extract claims"
                schema: {claims: [{claim: string, source: string, confidence: string}], new_sources_found: integer}

          - id: verify
            kind: foreach
            items: "{{nodes.search.output | flatten_claims | filter('confidence','medium')}}"
            pipeline: true
            max_concurrency: 2
            body:
              kind: stage
              label: "Verify: {{item.claim}}"
              prompt: "Adversarially verify this claim. Try to REFUTE it: {{item.claim}} (source: {{item.source}})"
              schema: {verdict: enum [confirmed, refuted, unverifiable], evidence: string, confidence: string}
              tools_posture: verify
              isolation: fresh

    - id: synthesize
      kind: stage
      label: "Synthesize research report"
      prompt: |
        Synthesize a comprehensive research report from verified findings.
        Question: {{inputs.question}}
        Verified claims: {{nodes.gather.output | flatten | filter('verdict','confirmed')}}
      schema: {report: string, confidence_level: string, gaps: [string]}
      effort: high
```

Simple multi-step extraction inside the gather loop chains on lightweight continuation signals (R9 amendment-5, heartbeat pattern); the full judge evaluation is reserved for phase boundaries — per-step judging over-taxes low-stakes chained steps.

### 3. General Loop → `general-project` Template

The "general" kind is the catch-all. **(R1.6 fix)** Rev 1's work stage self-reported `done: boolean` with no judge — violating the platform invariant that no agent certifies its own work (the loop engine's oldest rule). The default is now safe: a judge stage closes every iteration. A template author may opt out ONLY via an explicit `self_judged: true` flag, which the template lint surfaces as a warning badge.

```yaml
name: general-project
description: "General-purpose iterative project — works for any domain"
inputs:
  task: {type: string, required: true}
  max_cycles: {type: integer, default: 20}
  exit_condition: {type: string, default: "Task is complete to satisfaction"}
runtime_hints:
  judge: {freedom_level: medium, fallback_check: artifact_exists}
root:
  kind: loop
  mode:
    until_dry: {streak: 2, progress_field: "meaningful_progress"}
  body:
    kind: sequence
    children:
      - id: work
        kind: stage
        prompt: |
          Task: {{inputs.task}}
          Exit when: {{inputs.exit_condition}}
          Previous work: {{last.output.summary | default('None yet')}}
          Do the next meaningful step. Cite evidence; an independent judge verifies.
        schema: {summary: string, meaningful_progress: boolean, evidence: string}
        tools_posture: full
      - id: judge
        kind: stage
        label: "Verify (independent)"
        # standard judge contract: typed verdict, tools_posture: verify, isolation: fresh
        schema: {reasoning: string, verdict: enum [PASS, REJECT, REPLAN, ESCALATE, NEEDS_INPUT], cannot_judge: string}
        model_tier: judgment
        tools_posture: verify
        isolation: fresh
```

### 4. Code/SDLC Loop → `code-project` Template

**(R5) Restructured around the strongest quantitative evidence in the research** (+31% feature completion from an initializer stage; +37% from WIP=1) plus four independently-converged structural gates. This template is the descendant of the sdlc kind's stage machine — and the ONLY place `tick.evaluate` semantics port to (recon: tick is consumed solely by `kinds/sdlc.py:_tick_decide`; it was never the watchdog's brain).

```yaml
name: code-project
description: "Software development project with SDLC stages and gated progression"
inputs:
  task: {type: string, required: true}
  cwd: {type: string, required: true, help: "Working directory"}
  verify_command: {type: string, default: "", help: "Metric command that validates the deliverable"}
  guard_command: {type: string, default: "", help: "Regression command (must not get worse)"}
  bug_flavored: {type: boolean, default: false, help: "Requires reproduction before edits"}
runtime_hints:
  judge:
    proof_command: "{{inputs.verify_command}}"
    forbidden_success_modes: ["test deleted/skipped", "assertion weakened", "verify_command edited"]
    fallback_check: command_exit_code
  execution:
    single_active_feature: true      # (R5b) WIP=1 — engine-enforced, refuses to violate
    verify: {command: "{{inputs.verify_command}}", direction: pass}   # (R5e) dual-gate:
    guard: {command: "{{inputs.guard_command}}"}                      # metric + regression
    worktree_isolation: true         # (R5g) deliverable gate diffs the worktree as ground
                                     # truth (descends from loop/worktree.py)
root:
  kind: sequence
  children:
    - id: init
      kind: stage
      label: "Initialize (gated)"      # (R5a) +31% feature completion
      prompt: |
        Establish: a runnable environment, ONE verified passing test, a task
        breakdown with acceptance criteria, and a baseline commit for: {{inputs.task}}
      schema: {can_start: boolean, can_test: boolean, can_see_progress: boolean,
               can_pick_next: boolean, breakdown: [{item: string, acceptance: string}]}
      cwd: "{{inputs.cwd}}"
      tools_posture: full

    - id: init-gate
      kind: gate
      gate_kind: expression            # 4-condition checklist blocks implement until true
      config: {expr: "nodes.init.output.can_start && nodes.init.output.can_test && nodes.init.output.can_see_progress && nodes.init.output.can_pick_next"}

    - id: baseline
      kind: stage                      # (R5d) baseline capture BEFORE the first mutating
      label: "Capture baseline"        # node; later gates diff against this to classify
      prompt: "Run {{inputs.verify_command}} and {{inputs.guard_command}}; record exact output."
      schema: {verify_output: string, guard_output: string}
      cwd: "{{inputs.cwd}}"

    - id: repro
      kind: stage                      # (R5c) reproduction-before-edit for bug-flavored runs
      label: "Reproduce (bug runs only)"
      when: "{{inputs.bug_flavored}}"
      prompt: |
        NO edits until a failing test/command/documented repro exists. The only escape:
        the failure depends on production data/infra that cannot be simulated locally
        (effort alone does not qualify). The repro artifact IS the validation artifact.
      schema: {fail_reproduced: boolean, repro_command: string, infeasible_reason: string}
      success_when: "output.fail_reproduced || output.infeasible_reason != ''"   # (R5f) inverted semantics

    - id: implement
      kind: foreach
      items: "{{nodes.init.output.breakdown}}"
      pipeline: true                   # WIP=1: strictly sequential features
      body:
        kind: sequence
        children:
          - id: code
            kind: stage
            label: "Implement: {{item.item}}"
            prompt: |
              Implement: {{item.item}}. Acceptance: {{item.acceptance}}
              Pre-agreed test seams: {{nodes.init.output.breakdown}}
              Doctrine: expected values need an independent source of truth (no
              tautological tests); replace tests, don't layer them.   # (R11b)
            cwd: "{{inputs.cwd}}"
            tools_posture: full
          - id: feature-gate
            kind: gate
            gate_kind: verify_command
            config: {command: "{{inputs.verify_command | default('echo ok')}}", cwd: "{{inputs.cwd}}"}
            on_timeout: skip

    - id: test
      kind: stage
      label: "Run tests"
      prompt: "Run the test suite; report failures verbatim."
      cwd: "{{inputs.cwd}}"
      schema: {passed: boolean, failures: [string]}

    - id: triage
      kind: stage                      # (R11b) classify BEFORE retrying: flake vs
      label: "Triage failures"         # regression vs infra — routes the retry arm
      when: "{{!nodes.test.output.passed}}"
      schema: {classification: enum [flake, regression, infra], per_failure: [{failure: string, class: string}]}
      model_tier: cheap
      retry_route: {flake: rerun, regression: classified_retry, infra: surface}

    - id: verify
      kind: gate
      label: "Final verification (dual)"
      gate_kind: verify_command
      config:
        command: "{{inputs.verify_command}}"
        guard: "{{inputs.guard_command}}"     # regression vs pre-existing classified by
        baseline: "{{nodes.baseline.output}}" # diffing against the journaled baseline
        cwd: "{{inputs.cwd}}"

    - id: review
      kind: stage
      label: "Code review (independent judge)"
      # standard judge contract; rubric includes: "the interface is the test surface",
      # the deletion test, and the two self-tests as scored rubric lines   # (R11b)
      model_tier: judgment
      tools_posture: verify
      isolation: cross_model
```

### 5. Design Loop → `design-project` Template

**(R11a)** Rev 1's structure had no divergence phase and a dead `done: boolean` field inside a `counted` loop. Fixed:

```yaml
name: design-project
description: "Design exploration with divergence, iteration and convergence"
inputs:
  brief: {type: string, required: true}
  constraints: {type: array, default: []}
  max_iterations: {type: integer, default: 5}
runtime_hints:
  execution:
    diff_edit_baseline: true    # (R11c) carry (base_input, base_output) from the last
                                # judge-approved iteration; instruct minimal-diff
                                # regeneration instead of full regeneration
root:
  kind: sequence
  children:
    - id: diverge
      kind: stage                      # design-it-twice as the core loop: role-storm
      label: "Divergent role-storming"  # perspectives BEFORE committing to two options
      prompt: "Generate 4-6 radically different framings of this brief from distinct design perspectives: {{inputs.brief}}"
      schema: {framings: [{lens: string, sketch: string}]}

    - id: explore
      kind: parallel
      join: all
      children:
        - id: option-a
          kind: stage
          prompt: "Generate design option A (bold/unconventional), staying WITHIN this lens: {{nodes.diverge.output.framings[0].lens}}. Brief: {{inputs.brief}}"
          schema: {design: string, rationale: string, trade_offs: [string]}
        - id: option-b
          kind: stage
          prompt: "Generate design option B (pragmatic/safe), staying WITHIN this lens: {{nodes.diverge.output.framings[1].lens}}. Brief: {{inputs.brief}}"
          schema: {design: string, rationale: string, trade_offs: [string]}

    - id: evaluate
      kind: stage
      label: "Compare and select"
      prompt: |
        Evaluate design options against constraints: {{inputs.constraints}}
        Option A: {{nodes.option-a.output.design}}
        Option B: {{nodes.option-b.output.design}}
      schema: {winner: string, synthesis: string, remaining_issues: [string]}
      isolation: fresh                 # evaluator is not either generator

    - id: refine
      kind: loop
      mode:
        until: "{{last.output.remaining_issues | length == 0}}"   # (R11a) real exit
        max_iterations: "{{inputs.max_iterations}}"               # condition; counted cap
      body:
        kind: stage
        prompt: |
          Refine the selected design with a MINIMAL DIFF against the last approved
          iteration (baseline: {{run.diff_baseline}}).
          Remaining issues: {{last.output.remaining_issues | default(nodes.evaluate.output.remaining_issues)}}
        schema: {refined: string, remaining_issues: [string]}
```

### 6. `diagnose` Template (new, LOOP-R12)

A bundled template (or skill) that operates over the Run Ledger and failure-signature records of a completed/failed loop-template run:

- **Method**: start from the failure signature → walk the execution trace chronologically → diff manifests/traces against the last successful run of the same template → classify into one of FOUR layers: **routing** (wrong template/profile selected), **execution** (tool errors/permission blocks), **verification** (output exists but doesn't match criteria), **governance** (halted by safety/budget check).
- **Rules**: evidence before intuition; localize before explaining; compare, don't guess; never summarize without citing node/field/artifact.
- **Output**: a structured diagnosis record `{layer, failing_node, root_cause_hypothesis, evidence_chain, recommended_action}` journaled as a Run Ledger event and surfaceable in the cockpit/needs-input inbox.
- Doubles as the standard post-mortem procedure for LEARNING-FLYWHEEL's failure-mining stage — structured failure records the flywheel can cluster without human labeling. (Run Ledger / memory side; not knowledge.)

---

## Engine Behaviors (loop-node middleware)

These are engine-level, not template-level — they replace what loops get today from `gateway._fire`, the watchdog, and the subagent reaper.

### No-progress circuit breaker + escalation ladder (LOOP-R4)

1. **A deterministic, LLM-free circuit breaker** as loop-node middleware, evaluated BEFORE each iteration off journal data: tool-argument fingerprinting; trip at 3 consecutive identical failures; extended headroom for recoverable classes (timeouts, rate limits); Continue → Nudge (inject corrective steering) → Halt ladder. Judges only see runs the breaker could not unstick. Costs zero LLM calls until it trips. Templates may declare/parameterize the breaker per-loop-node (`runtime_hints.execution.breaker` — e.g. a stricter fingerprint window for monitor variants) while the engine default applies everywhere.
2. **On trip, switch STRATEGY** per the numeric ladder in `runtime_hints.execution.escalation`: `[classified_retry, fresh_session, model_switch, restart_from_scratch, surface]`. Classified retry uses `failure_mutations` (failure_mode → corrective instruction) for targeted re-prompts. Failure classes route to distinct arms: malformed-output → resume-retry with feedback (cheap); wrong-work → fresh-session retry (expensive); rate-limit/429 → wait-and-retry with NO fresh session and no escalation. Restart-from-scratch (discard workdir, re-run from spec) is a distinct rung from fresh-session retry. Success resets counters. Proven abandonment values: `no_progress_stop: 5` consecutive non-improving, `hypothesis_abandon_after: 3` same-fix failures.
3. **"Never silence" exits**: a stalled leaf must exit via `needs_input {question, options}` or `incomplete {root_cause}` — the final escalation routes to a needs-input gate carrying a structured brief `{goal, attempts with verbatim error signatures, where stuck, recommendation, exact choices}`, never a raw transcript. (This upgrades the watchdog's NEEDS_INPUT/`stagnant` behaviors with structure.)

### Fresh-session retry + worker lifecycle protocol (LOOP-R7)

Rev 1's checklist row said only `fresh_session: true`; the mechanics every studied system converged on:

- **Structured handoff, clear-don't-compact**: the retry session resumes from on-disk artifacts + a 5-field ledger summary (`task_overview / current_state / important_discoveries / next_steps / context_to_preserve`), with prior-attempt error-signature digests injected so retries don't repeat a failed path. Assembly rule: immediate attempt inline; older attempts as pointers with a token cap.
- **Repair vs restart distinction**: repair keeps workspace progress (ground-truth check, e.g. commits-since-base), injects failure stage + bounded prior summary + "smallest patch" instruction, severity-tagged gap injection as the repair payload; attempt budget = max(execution_attempts, repair_rounds+1). Restart resets. Rewind-over-correct: prefer restarting from the last good checkpoint with a distilled learnings note rather than correction-polluted context.
- **Complete-or-block protocol**: a session terminating without an explicit signal is a protocol violation → node auto-transitions to `blocked`, never silently hung.
- **Engine-level liveness carried over from the watchdog/reaper**: heartbeats, stale-claim reclamation, long_running/stalled/stuck/lost classification, and verification of the NEW session's identity so a retry can't attach to a stale predecessor. (Descends from `LoopWatchdog` in-memory liveness + `SubagentManager._reaper_loop`/`_reconcile_orphans` — restart recovery re-arms, it does not resume counters; same design here.)
- **Atomic clarification consumption**: user clarifications threaded into a retried step are consumed atomically (single-use rows deleted in the resume transaction) so double-resume can't replay them.
- **Executor capability flags** (`runtime_hints.execution.executor_caps`: `supports_resume`, `supports_lifecycle_hooks`, `prompt_delivery`) so the engine branches correctly across PersonalClaw's 3 ACP dialects — fresh-session retry only where resume is unsupported. (This generalizes today's fresh-ACP-retry heuristic: some ACP agents no-op repeat prompts, which is exactly why `gateway._fire` grows a fresh session.)
- **State-continuity default at iteration boundaries** (amendment-5): auto-persist-only-if-agent-didn't — the engine detects whether the worker explicitly stored its state at iteration end and only then auto-persists a summary, guaranteeing continuity across fresh sessions without double-writing or clobbering the agent's own richer checkpoint.
- **Trust plumbing parity**: retried/fresh worker sessions get the same three-fold trust setup loops get today (`_trust` + approval policy "auto" + `_unattended`, plus `acp_mode="bypassPermissions"` when unattended) — miss one and the run wedges on an option prompt.

### Context-overflow recovery within loop iterations (LOOP-R13)

Loops currently get compaction from the gateway; workflow-template loops must not crash on `prompt_too_long` or thrash:

- **Reactive**: on a context_length_exceeded error mid-iteration, attempt ONE forced compaction (micro-compact structural cleanup first, then LLM summarization if still over) and retry the turn; a `hasAttemptedReactiveCompact` flag prevents infinite compact-retry loops. After 3 consecutive compaction failures, disable auto-compact for that node execution.
- **Proactive** (amendment-5): topic-segmented history compression with attention ratios — current topic at high fidelity (~65% of budget), historical topics compressed to request/response pairs, oldest tier bulk-summarized; run asynchronously between iterations so the reactive path becomes the rare fallback. Topic segmentation gives `compaction_prompt` a structural unit (compact per-topic, not per-transcript), preserving typed carryover buckets (files touched, verified work, spawned children) that survive any context reset.
- **Judge boundary invariant**: judge/gate evidence transcripts assembled post-compaction exclude worker narration blocks — the worker cannot influence the judge through prose that survives compaction.

### Mid-run human steering channel (LOOP-R14)

Today the only ways to course-correct a running loop are cancel+restart (loses cycle context) or full mid-flight graph mutation (heavyweight). Three cheaper paths:

1. **Interrupt queue on loop nodes**: the user injects new instructions mid-flight; queued items are consumed atomically at the next iteration boundary and trigger a plan re-evaluation step (re-rank remaining sub-goals against the new instruction) before the next work cycle; journaled as a `steering` Run Ledger event. (This generalizes the loop `nudges.json` + guidance-file mechanism into a first-class engine feature.)
2. **Judge-comment triage**: judge/review stage output gains a line/location-anchored comment format; each comment is individually accept/rejectable by the user; the ACCEPTED subset is dispatched back to the SAME worker session as follow-up instructions — closing the loop that judge independence opened.
3. **Per-project judge-prompt overrides**: a file in the project/workspace beats the template's bundled judge prompt, resolved at render time and recorded in the ledger so verdicts are attributable to the prompt version that produced them.

New SSE events (`steering`, `breaker_trip`, `judge_divergence`, `comment_triage`, `self_scheduled`) MUST be added to the FE `RUN_LIFECYCLE` union (`web/src/pages/loops/useRunStream.ts`) — EventSource silently drops unregistered event types.

---

## What Is Gained

| Dimension | Old Loop Engine | Workflow Template |
|---|---|---|
| Composability | Closed strategies; can't insert custom stages between classify/execute | Open graph; user inserts/removes/reorders nodes freely |
| Visibility | Per-kind cockpit with pre-defined layout | Universal widget showing the exact node tree + per-node status |
| Editability | Limited (pause, change granularity, add sub-goals, guidance files) | Full mid-flight mutation + the cheap interrupt-queue path (R14) |
| Reuse | Each kind is a registered Python strategy (pluggable, but code-only) | Templates compose from shared primitives; custom templates are data |
| Domain support | 5 bundled kinds (a 6th is one module + register(), but still code) | Unlimited templates; unknown domains generate from scratch |
| Multi-model | Single model per loop | Per-node model_tier (deterministic/cheap/standard/judgment) |
| Judge accountability | Verdicts in per-loop files; calibration via probe canary | Typed verdict ledger + divergence events + hardening loop (R3) |

## What Must Be Preserved (Domain Intelligence)

| Loop Intelligence | Where It Lives in Template |
|---|---|
| Judge calibration (granularity dial → threshold/window) | `runtime_hints.judge.rubric` + `marginal_threshold` seeded from the real dial values (quick 3.0/1, balanced 2.0/2, exhaustive 1.0/3) |
| Dual-judge adjudication (assess + skeptic, TWO yeses for done) | `judge_samples: 3` median on terminal gates + forbidden-success-mode screen (R2c) |
| Judge ground-truth observation (`_observe_ground_truth`) | `tools_posture: verify` + `proof_command`/`validator_script` (R1) — the judge re-runs and re-reads, never trusts prose |
| Judge canary + judge_blind halt (`instrument.py`) | Template-save-time calibration probe + uncalibrated-judge halt (R3e) |
| Stagnation detection (streak counting, window N) | `loop{mode: until_dry{streak: N}}` — native node capability, N from the dial |
| Goal-type-specific exit | Template variant selection (verifiable/open/monitor) |
| Adaptive nudge prompts | Stage prompt references `{{last.output}}` and adapts |
| Classification | Preserved as the `workflow_plan` tool's template-matching step |
| Plan generation | Preserved as the planning meta-workflow (see UNIVERSAL-PROJECT-PLANNING.md) |
| Worker/supervisor dir symmetry (`effective_dir`) | Engine invariant: the judge/gate reads where the worker writes; code templates pin `cwd` + worktree explicitly |

---

## Migration Path

### Phase 1: Coexistence (immediate)
- Loops keep running on their existing engine.
- New workflow templates are available as an ALTERNATIVE way to start projects.
- Users who create a "goal" from chat can choose: old loop or new workflow (the system offers both until the old is deprecated).
- **(R10a)** Legacy loop-kind identifiers and loop chat-tool names resolve to their workflow templates via read-time aliases in the live enum — zero migration code for years-old references; aliases are deleted only at the Phase-4 endgame.

### Phase 2: Feature Parity (after Slices 0-5 + this plan)
- All 5 template variants pass the same validation scenarios old loops handle.
- The "Projects" nav section shows BOTH active loops and active workflow runs.
- Dashboard ActiveWork widget aggregates both.
- **(R10c) Cockpit live-follow key equivalence** (checklist row): loop cockpit live-follow today keys per-loop SSE on `loop:<id>`; template-run sessions stream under derived run-scoped keys, and the cockpit/chat surface must treat them as the base container via key equivalence — strict-equality matching drops events (a proven FE regression class).
- **(R10b) Template metadata hygiene**: bundled templates pre-define only graph structure + judge wiring, never autonomy/model/retention policy (those stay per-run inputs); `<category>-<layout>-<style>` naming taxonomy is a rule; each template carries a typed doc block (WHEN-trigger description, keywords, stage anatomy, judge do/don'ts) validated at save time with a recorded validation signature — post-migration user edits surface a re-validate warning instead of silently breaking embedded judge calibration.

### Phase 3: Deprecation (6-12 months post-v2)
- New project creation defaults to workflows; loop creation moves to "Legacy" section.
- Existing running loops continue to completion.
- No forced migration of historical data.

**(R6) Retirement acceptance criteria — a template may not become the default replacement for its loop kind until:**

1. **Its judges actually reject sometimes.** Run Ledger evidence required; a gate with a 100% pass rate over N runs is statistical proof of a fake check — it blocks retirement and surfaces as a warning badge on the template (the nodding-loop detector).
2. **The five-moves audit passes.** Each of the 5 templates must show where each move — discovery / handoff / verification / persistence / scheduling — lives in its graph, or justify its absence. The five anti-patterns (nodding / amnesiac / manual / blind / tangled) become named template-lint rules run in CI.
3. **Every ported engine behavior carries provenance tags** (see checklist table): the failure mode it mitigates + the model era it was added — "every component encodes an assumption about what the model can't do" — enabling methodical ONE-AT-A-TIME retirement later (radical one-shot simplification is a documented failure; one-at-a-time worked).

### Phase 4: Retirement Endgame (explicit — "loops drain naturally" is not a plan)

Loops can be paused or long-lived indefinitely (goal loops have no expiry), so retirement of `loop/manager.py`, `loop/watchdog.py`, autonudge-as-loop-ticker, and the reserved loop agents (`RESERVED_AGENT_NAMES`, agents/defaults.py) cannot wait for the last loop to finish on its own. The endgame:

1. At Phase 3 + 90 days: paused/stagnant loops older than 30 days get a one-time migration offer (their plan + findings compiled into an equivalent workflow run via the template mapping, findings carried as prior context).
2. Any still-active loop past the deadline: auto-converted on next resume (same compilation), or archived-with-data if conversion fails validation.
3. Only then are the engine modules deleted. Historical loop rows stay readable (read-only archive view) — the DATA never migrates forcibly, only the EXECUTION.
4. **(R6 amendment) "Gate writes, never reads"**: at retirement, block new loop CREATION only; keep read/cancel/cockpit forever — retirement is non-destructive by construction.
5. **(R6 amendment) Proof artifacts for retirement**: each migrated engine behavior gets a claim-action-assertion-validated proof artifact (screenshot/ledger record) attached to the Run Ledger — the retirement acceptance criteria get a working evidence format.

### Migration Checklist — Behaviors That Must Not Silently Vanish

These loop-engine behaviors are baked into `gateway._fire`, the watchdog, and the subagent reaper — NOT into plan structure. Each row carries **(R6c) provenance tags**: the failure mode it mitigates + the model era it was added.

| Behavior | New Home | Mitigates / Era |
|---|---|---|
| Deliverable-forcing re-prompt (worker claims done but deliverable missing → re-prompt; `_MAX_CYCLE_REPROMPTS`) | Template-level: terminal `gate{verify_command\|expression}` + `retry{amend_prompt}` on the deliverable stage; actor-transition rule makes worker-declared done impossible anyway | Worker done-claims without artifacts / 2025 |
| Fresh-ACP-retry heuristic (hung ACP session → retry on a fresh one) | Engine-level: stage `retry` gains `fresh_session: true` + the full R7 protocol (structured 5-field handoff, repair-vs-restart, complete-or-block, liveness classification, executor_caps gating across the 3 ACP dialects, atomic clarification consumption, auto-persist-only-if-agent-didn't) | ACP agents no-op repeat prompts / 2025 |
| Loop `workflow_ids` force-injection (per-phase SOP guidance, context.py force-include) | Template stage prompts reference the migrated SOP-template content directly (TASKS-SOPS plan) | Workers forgetting standing procedure / 2025 |
| Autonudge idle-tick driving loop cycles | The v2 engine is tick-driven natively (engine scheduler + event ticks); autonudge returns to its original chat-idle job (AUTOMATION-SUBSTRATE absorbs it as `kind:idle`). Note: the loop engine's PURE tick core (`loop/tick.py`) ports only into code-project stage semantics — it was only ever the sdlc kind's brain | Sessions idling instead of continuing / 2025 |
| Judge ground-truth independence (`_observe_ground_truth` re-runs commands / reads artifacts) | **(R1)** No longer claimed "by construction" — enforced: `tools_posture: verify`, `proof_command`, `validator_script`, `hidden_validation_commands`, `forbidden_success_modes`, `ground_truth_sources`, judge isolation via SubagentManager, actor-based done-transition, proof-attachment precondition | Verifier theater + reward hacking / 2026 |
| Dual-judge adjudication (skeptic second opinion, TWO yeses) | **(R2)** `judge_samples: N` median (default 3 on terminal gates) + typed verdict enum {PASS, REJECT, REPLAN, ESCALATE, NEEDS_INPUT}, reasoning-before-verdict, constrained decoding, `cannot_judge` escape hatch | Single-judge noise + leniency drift / 2026 |
| Judge calibration (granularity dial, probe canary, judge_blind halt) | **(R3)** Rubric convergence contract + `stop_condition{consecutive_clean}` + free stuck detection off journal data + verdict ledger events (incl. `status=discard`) + judge-hardening loop (divergence events → few-shot exemplar patches) | Uncalibrated judges passing everything / 2025-26 |
| Stagnation/error windows + trust-TTL (watchdog `_MAX_CONSECUTIVE_ERRORS`, `_MAX_TURN_SECS`, trust expiry → NEEDS_INPUT) | **(R4)** Engine circuit breaker (fingerprinting, trip at 3 identical failures, recoverable-class headroom) + escalation ladder + never-silence structured briefs; trust-TTL carries over as a run-level policy | Infinite fix loops (the #1 failure mode) / 2025 |
| Gateway auto-compaction within long turns | **(R13)** Reactive one-shot compaction + circuit breaker + proactive topic-segmented compression + typed carryover buckets + judge-boundary narration exclusion | Context overflow mid-iteration / 2025 |
| Guidance files + nudges.json mid-flight steering | **(R14)** Interrupt queue consumed at iteration boundaries + plan re-evaluation + judge-comment triage + per-project judge-prompt overrides | Cancel+restart as the only correction path / 2026 |
| Deterministic gates (`gates.py` tristate verify, audit screen) | **(R8)** Free rule tier before every judge + `fallback_check` beneath every judge + PASS-contradicts-deterministic auto-escalation + verification ladder ordering | Burning judge tokens on rule-solvable checks / 2025 |
| Boot recovery (`reap_orphaned_loops`, subagent `_reconcile_orphans`) | Engine run-reconciliation at startup: RUNNING runs re-arm, orphaned worker sessions tombstoned, self-created triggers (R15) survive via substrate persistence | Gateway restarts stranding runs / 2025 |

---

## Changes to WORKFLOWS-V2.md

1. **Binding syntax addition:** `{{last.output}}` inside a `loop` body refers to the previous iteration's output (already implied but must be explicit in the spec).
2. **`runtime_hints` field on WorkflowDef** — opaque dict with the two-group split (`judge` / `execution`), passed to stage prompt rendering via `{{defaults.runtime_hints.*}}`; the engine-enforced subset (judge-tier isolation, actor transitions, proof-attachment, WIP=1, expression success) documented as invariants, not hints.
3. **`tools_posture: verify`** — a third posture between `none` and `full`: read tools + declared-command execution, no writes (R1).
4. **Node fields**: `isolation: fresh | cross_model` on stages; `model_tier: deterministic | cheap | standard | judgment`; `success_when` expression; `when` conditional; `retry_route` classified-retry mapping.
5. **Gate kinds**: `gate{kind: judge}` with `judge_samples`; dual verify+guard config on `verify_command` gates; `fallback_check` on all judge/gate nodes.
6. **Loop-node middleware seam**: circuit breaker + escalation ladder + interrupt queue are engine features of the `loop` node (R4, R14).
7. **Run Ledger event types**: `judge_verdict` (with evidence chain + discard status), `judge_divergence`, `steering`, `breaker_trip`, `comment_triage`, `diagnosis`, `self_scheduled` — and the corresponding FE `RUN_LIFECYCLE` union additions.
8. **Add 8 loop-derived templates to Slice 6** (goal-pursuit-verifiable, goal-pursuit-open-ended, goal-pursuit-monitor, deep-research, code-project, design-project, general-project, diagnose).
9. **Tool surface**: `set_onetime_task` / `set_recurring_task` tool module (R15), registered via `mcp_core._TOOL_MODULES` + `tool_providers/registry.py`; trigger entities + resume-target wiring specified in AUTOMATION-SUBSTRATE.

---

## Risks

- **Judge-hardening is iterative by nature** — expect 3-5 tuning rounds per template before divergence events quiet down; budget it, don't treat first-pass judge prompts as final (R3).
- **`tools_posture: verify` is a new privilege tier** — it must reuse the existing judge-session restriction pattern (no write tools, permission requests rejected) and the `audit_bash_command` screen on `proof_command` execution, or judges become an escalation path.
- **Judge sessions are subagent spawns** — they ride `SubagentManager` caps (`_MAX_CONCURRENT`/auto-sizing, memory gate, cwd validation). A run with per-cycle judges + parallel foreach could starve the pool; judge spawns should use the internal-secret `approval_mode="auto"` path and count against run-level, not global, concurrency where possible.
- **Self-scheduling (R15) is the one genuinely new power** — the bounds (count cap, TTL, provenance, autonomy gating) are load-bearing; ship them in the same slice as the tools, never after.
- **Alias layer (R10a) can mask drift** — aliases resolve names only; behavior parity is proven by the R6 acceptance gates, not by the alias resolving.

---

## Implementation Effort

- **5 sessions** (after Workflows v2 Slices 0-2 are complete; was 3 — the judge contract, engine middleware, and acceptance instrumentation absorb the added scope)
- **Session 1 — Judge contract + runtime_hints spec**: the two-group hints schema; `tools_posture: verify`; typed verdict enum + constrained decoding + parse-retry; judge isolation via SubagentManager + actor-transition + proof-attachment invariants; deterministic pre-tier + `fallback_check` (R1, R2, R8, R9). Engine unit tests: a worker cannot transition to done; a PASS without proof is invalid; PASS-contradicts-deterministic escalates.
- **Session 2 — Engine loop-node middleware**: circuit breaker + fingerprinting + escalation ladder + failure-class routing; fresh-session/lifecycle protocol + executor_caps; reactive/proactive compaction; interrupt queue + boundary consumption (R4, R7, R13, R14). New Run Ledger event types + FE `RUN_LIFECYCLE` additions.
- **Session 3 — Author the 8 template YAML specs** (R5 code-project restructure, R11 design/test/TDD content, R12 diagnose, R15 monitor self-scheduling incl. the trigger tools module + bounds config through the four wiring points) + integration tests that run each through the engine; verify `until_dry` + `progress_field` and judge-stage structured output reliability.
- **Session 4 — Calibration + acceptance instrumentation**: rubric contract + server-side score recomputation; verdict ledger + divergence events + hardening loop; template lint (five-moves audit, anti-pattern rules, self_judged warning, LLM-stage-doing-deterministic-work); nodding-loop detector + warning badge; save-time validation signature (R3, R6, R10b).
- **Session 5 — FE + coexistence**: template picker + "Start from template"; cockpit live-follow key equivalence (R10c); interrupt-queue + comment-triage UI; legacy alias layer (R10a); end-to-end validation of all 8 templates as-a-user.

## Success Criteria

1. Each of the 5 loop kinds' core scenarios can be expressed as a workflow template and runs to completion.
2. **No bundled judge is blind or captive**: every judge stage runs `tools_posture: verify` in an isolated session, re-runs its `proof_command`, and its PASS events carry cited proof artifacts (spot-check the Run Ledger).
3. **Judges reject sometimes**: over the parity-validation runs, every template's judge/gate shows at least one REJECT/REPLAN with evidence — a 100% pass rate blocks the template from becoming its kind's default (R6a).
4. The circuit breaker trips on a synthetic 3-identical-failure run without any LLM call, and escalation walks the ladder to a structured needs-input brief (R4).
5. A "research a topic" workflow produces findings of equivalent quality to the old Research Loop, with claims verified by a cross-model judge.
6. A "build a feature" workflow passes the init-gate, holds WIP=1, and classifies a seeded regression vs a pre-existing failure via the baseline diff (R5).
7. The monitor variant parks between checks via a self-created trigger (visible in the cockpit with provenance + TTL) instead of burning counted cycles, and survives a gateway restart (R15).
8. Mid-flight steering works: an interrupt queued during a running research workflow is consumed at the next iteration boundary and visibly re-ranks the plan; an accepted judge comment reaches the worker session (R14).
9. Mid-flight editing works (user pauses a running research workflow, edits the synthesis prompt, resumes; edited template surfaces the re-validate warning).
10. The five-moves audit + anti-pattern lint pass in CI for all 8 bundled templates (R6b).
11. The template picker correctly suggests `code-project` for coding intents and `deep-research` for research intents; legacy loop-kind names resolve via aliases (R10a).

---

## Execution log

- **2026-08-01 — DONE — Judge contract + runtime_hints + enforcement invariants (session 29 of the WF2 queue).**
  Branch `feature-wf2-loops-judge`, PR #167. `workflows/judge_contract.py` (closed verdict enum
  PASS/REJECT/REPLAN/ESCALATE/NEEDS_INPUT, rubric ratchet with strict-no-averaging,
  engine-computed overall, forbidden-mode denylist, N-sample median aggregation),
  `judge_pretier.py` (the free rule tier that runs before any judge model call — never issues a
  PASS — plus the tristate `fallback_check`), `judge_actors.py` (the actor-transition invariant:
  a worker may never reach `done`; judge isolation incl. cross-family refusal; provenance
  blinding; narration-excluding evidence assembly). `runtime_hints` added to `WorkflowDef`
  (opaque to the core, parsed leniently to the STRICT default by `hints_from_dict`). 11379 tests.

- **DISCOVERY — the forbidden-mode denylist shipped inert in its first form.** Requiring every
  long word of a mode phrase to be present missed the phrasing a judge actually produces
  ("test deleted or skipped" lists alternatives). Two-of-N stemmed-signal matching + two-signal
  default phrasings; verified 6/6 real admissions caught, 4/4 innocent clean.

- **DEVIATION — `loop/judge.py` is not unified.** LOOPS-EVOLUTION converges loops onto v2 in its
  own slices; unifying the existing loop judge pre-emptively would be wasted motion. This session
  builds the template-world contract that must MEET OR EXCEED the loop judge's bar (which it does:
  isolation, independent proof re-run, dual/median adjudication, verify-command tier).

- **NOT DONE (queue split):** the engine loop-node middleware (breaker, fingerprinting, escalation
  ladder, failure-class routing, fresh-session protocol, interrupt queue) is session 30; the 8
  template specs are session 31. This session is the enforcement primitives they consume.

- **Real-model validated** through the live dev gateway (Bedrock, claude-sonnet-5): a real verdict
  came back correctly shaped and REJECTED thin evidence unprompted; the contract then caught both
  a rubber-stamp (same zero scores flipped to PASS) and a deterministic contradiction (PASS vs
  failed check → escalate).

- **2026-08-01 — CODE DONE (push blocked) — Engine loop-node middleware (session 30 of the WF2 queue).**
  Branch `feature-wf2-loops-middleware`. `workflows/loop_middleware.py`: 7-way failure
  classification, tool-argument fingerprinting, the Continue→Nudge→Escalate→Halt ladder with
  per-class entry rungs, recoverable-class headroom (no rung burned on a 429), the structured
  never-silence brief, and the atomic interrupt queue. 4 new ledger kinds registered in the FE
  `RUN_LIFECYCLE` union with a bidirectional drift test. 11442 tests, full FE gate green.

- **DISCOVERY — two dead-configuration bugs in my own first cut, both caught by measuring the walk.**
  (1) Every non-classified-retry rung mapped to HALT, making the ladder's middle unreachable — fixed
  with a distinct ESCALATE action. (2) `attempt_cap` applied as a ladder-POSITION cap made
  `restart_from_scratch` unreachable under the plan's own values — fixed by counting attempts within
  a rung. A rung that can never be selected reads as a working feature; only walking the whole ladder
  exposes it.

- **DEVIATION — built on top of `resilience.check_breaker`, not replacing it.** The Slice-2c breaker
  already catches four stalls; this adds only the tiers needing more than counters.

- **NOT DONE — the middleware is a pure decision layer, not yet wired into the `RunController` tick.**
  That wiring (plus the R7 fresh-session lifecycle protocol and R13 reactive compaction, which needs
  the absent summarizer seam) is the live-engine integration those consume. Validated in isolation on
  a full simulated stall sequence.

- **2026-08-01 — CODE DONE (push blocked) — The loop-kind templates (session 31 of the WF2 queue).**
  Branch `feature-wf2-loops-templates`. Five new bundled templates with `runtime_hints` and the
  judge contract wired in: `goal-pursuit-open-ended`, `goal-pursuit-verifiable`, `general-project`,
  `design-project`, `diagnose-run`. 132 structural integration tests; the pre-existing bundled
  convention suite extended 6 → 11 templates. Verified through the live API and in a built wheel.

- **DEVIATION — five templates, not eight.** `deep-research` (the research-loop descendant) already
  ships from Slice 9a. `goal-pursuit-monitor` is NOT BUILDABLE: its design rests on
  `set_onetime_task` / `set_recurring_task`, which belong to AUTOMATION-SUBSTRATE and do not exist
  anywhere in the tree — shipping it would mean a template whose central mechanism silently
  no-ops. `code-project` is deferred as a product decision: it overlaps the shipped
  `code-implementation`, and choosing between replacing that or adding a second beside it is not a
  mechanical port.

- **PREMISE MISMATCH — the plan's YAML uses three spec forms the engine does not have.**
  `{{defaults.runtime_hints.*}}` is not a valid binding root (only inputs/nodes/item/iter/last);
  a gate's kind is `config.kind`, not `gate_kind`; an `until` loop needs `config.condition`, not
  `config.until`. Rubrics are inlined into judge prompts instead, with a test asserting every
  declared criterion actually reaches a judge.

- **DISCOVERY — my own `design-project` shipped without a judge,** exiting its refinement loop on a
  self-reported `issues_resolved` — precisely the "no agent certifies its own work" rule this plan
  names as the platform's oldest. Caught by the structural test suite written in the same session.

- **DISCOVERY — `continue_on_error` is not an engine key.** I invented it; the bundled action-arg
  guard caught it. The real form is `allow_failure` inside `with`.

- **2026-08-01 — CODE DONE (push blocked) — Calibration + acceptance instrumentation (session 32).**
  Branch `feature-wf2-loops-calibration`. `workflows/judge_calibration.py`: the verdict ledger
  (discarded iterations included), divergence records with a typed direction, the nodding-loop
  detector with a generous minimum sample, free stuck detection, the judge canary sharing
  `loop/instrument`'s threshold, and hardening-loop exemplars ordered dangerous-direction-first.
  Six anti-pattern lint rules + the five-moves audit in `template_lint.py`. 11697 tests.

- **DISCOVERY — five false positives from my own first draft of the anti-pattern rules,** every one
  on the shipped library and none a real template defect: `{{nodes.}}` is cross-iteration state;
  `refuted: boolean` routes as well as `verdict`; `infer` nodes have no tools by definition;
  `streak` alone is a valid until_dry exit and a bound `max_iterations` is a valid cap; and
  verifiers exist under the names `verify_refute`, `completeness_critic` and `round_gaps`. All
  fixed at the rule; `KNOWN_ANTI_PATTERNS` is empty and a test keeps it that way.

- **DISCOVERY — I nearly recorded one of those as a genuine defect,** complete with a reasoned
  exemption, and the self-check test written alongside it is what disproved it. The plausible
  write-up was the risk, not the rule.

- **NOT DONE — the calibration is not wired into the run path.** Nothing emits `judge_verdict` /
  `judge_divergence` yet; the kinds are registered and the FE union knows them (session 30), but the
  emit points sit in the controller tick and the human-override UI, which is session 33. The
  save-time `probe_judge` hook is deferred deliberately: a model call on the save path is a latency
  decision, not a detail.

- **2026-08-01 — CODE DONE (push blocked) — FE + coexistence (session 33).**
  Branch `feature-wf2-loops-fe`. `workflows/loop_aliases.py` (read-time legacy-kind aliases, one-way,
  every alias asserted against a shipped template) + cockpit stream-key equivalence (the R10c silent
  event-drop fix), mirrored in `web/src/pages/workflows/containerKey.ts` with a backend↔FE drift
  test. R14 steering endpoints (`/steer`, `/steering`) queued on the run and consumed at the
  iteration boundary. Alias manifest surfaced through `/api/workflows/manifest`. 11750 tests, full FE
  gate green.

- **DISCOVERY — a test of mine leaked a run into the REAL home.** The fixture patched the loader's
  `config_dir` but `store.py` binds it at module level, so `store.save()` wrote to
  `~/.personalclaw/workflows/runs.db`; the leak surfaced three files away as a memory-smoke failure
  because a live run prepends `[ACTIVE WORKFLOWS]` to every built message. Root-caused, the stray row
  removed, the fixture fixed to patch `store.config_dir` directly.

- **DEVIATION — the run-path wiring is not done, and that is the honest back half of this session.**
  The steering queue is stored and surfaced but the tick loop does not consume it; the template
  picker's data (aliases + manifest) exists but the widget does not; `keysEquivalent` exists but the
  cockpit still compares raw keys. All three sit above the same controller-tick seam that sessions 30
  and 32 also stopped at, and wiring the three decision layers into the live run in one coherent
  change is better than three half-integrations. The as-a-user validation of all 8 templates is
  bounded by the same gap.

### S144 — the free rule tier runs before the LLM judge (criterion 2)

**DONE.** LOOPS-EVOLUTION criterion 2 requires that "no bundled judge is blind or captive", and §judge
is explicit that **a free rule tier runs BEFORE any LLM judge call** — mechanical length, failure-
pattern regex, structural pre-checks, existence — so "anything rule-solvable never reaches the
probabilistic model. Loop judges run every cycle — this is the single biggest token saver in the plan."

`judge_pretier.run_pretier` implements exactly that tier and **shipped in session 30 with no caller**
(AST-verified: zero production importers outside its own module + tests — this was the finding the
2026-08-04 header audit recorded). So the saver did not exist: measured against the live `GateKind.JUDGE`
dispatcher, an empty artifact, a whitespace artifact and a `TODO: implement` stub all reached the
reasoning-tier judge and each spent a completion on output there was provably nothing to judge.

**Wired at the gate, opt-in via an `evidence` binding.** `dispatch_gate` now runs
`_judge_pretier_screen(cfg)` before the model call; a rule-solvable rejection short-circuits to a
REJECT `NodeResult` carrying the pre-tier's `failure_class` (`empty_output` / `stubbed_output` /
`worker_gave_up` / `tool_error`) so the escalation ladder routes on WHY it failed rather than
re-deriving it. Adopted in five bundled templates whose deliverable node is unambiguous:
goal-pursuit-open-ended (`accept`), knowledge-lint (`detail-preserved`), knowledge-synthesis
(`grounded`), publish-article (`accuracy-held`), thesis-tracker (`falsifiable`).

**DEVIATION — additive, not the whole cluster.** The plan's back half is "wire the three decision
layers into the live run in one coherent session" (`loop_middleware`, `judge_calibration`, and this
pre-tier). This session took ONLY the pre-tier, deliberately: `loop_middleware`'s breaker is
**redundant with the shipped `resilience.check_breaker`** the RunController already consults every
iteration (criterion 4's LLM-free 3-identical trip is met), so wiring `check_middleware` wholesale
would be a dual path — a clean-break violation, not a fix. The pre-tier is the one layer that is both
inert AND non-redundant, and it is atomically completable without touching the controller. Calibration
(`judge_verdict`/`judge_divergence` emission) and the middleware/steering consumption remain the
genuine back half; they are recorded here as the next Loops-Evolution slice, not silently folded in.

**Two guards that keep it additive, each measured:**

* **No `evidence` key → no screen.** Every judge shipped before this binds no evidence; the mechanical
  length check would otherwise see empty text and reject a working gate as "under 20 chars — nothing
  to judge". Driven: a no-evidence judge reaches the model exactly as before (1 call, DONE).
* **The existence gate defaults OFF** unless the template supplies an `evidence_*` count. A text-only
  judge must not be failed for producing zero commits; it engages only when the author asked for it.

Verified load-bearing by removing the screen call and confirming the rejection tests go red. Gate:
`make lint` (black+isort+flake8+mypy, 691 files) green; `pytest -n 4 --dist worksteal` **16082 passed,
29 skipped, 13 xfailed**. No `web/` change. 8 new tests in `tests/test_workflows_engine.py`.

- **REMAINING in LOOPS-EVOLUTION:** the middleware/steering run-path consumption (criteria 3/8) and
  calibration emission + assessment (`judge_verdict`/`judge_divergence`, criterion feeding R6a) — the
  honest back half this session did not take, plus the 3 templates deferred at session 33 and the
  `code-project` product decision.

### S145 — `judge_samples` was declared and read by nothing (criterion 2 / R6a)

**DONE.** `goal-pursuit-open-ended`'s terminal `accept` gate carries `judge_samples: 3`, and its own
prompt explains why to the model: *"three independent samples of you are being asked — a single
judgement on a terminal accept was measured to be indistinguishable from noise."* The gate read the
key **not at all**. Measured before writing a line:

```
judge_samples=3, model returns PASS, REJECT, REJECT
  -> model_calls=1  state=done  output={'verdict': 'PASS'}
```

**A 1-of-3 PASS accepted the run.** The majority verdict the template paid for never happened, and the
template's own stated reason for sampling — that one judgement is noise — described the behaviour it
was actually getting. `judge_contract.aggregate_samples` ships the exact median rule (any escalation
wins; majority pass; else majority reject) and was **also uncalled**, so both halves existed and
nothing connected them. This is the same declared-but-unread shape as S144's pre-tier, one config key
over.

**The rule is shared; the TYPE deliberately is not.** `aggregate_samples` types on
`judge_contract.Verdict` (PASS/REJECT/REPLAN/ESCALATE/NEEDS_INPUT) while this gate speaks
`verify.Verdict` (PASS/RETRY/ESCALATE/REJECT) — measured, they differ in three members. Importing the
aggregator and feeding it gate verdicts would be **exactly** the cross-vocabulary defect S130 found in
the fail-mode classifier, where a set of cap keys was compared against gate names and every gate read
"closed". So `_aggregate_gate_verdicts` restates the documented rule over the gate's own enum, and the
docstring names the reason so a future reader does not "simplify" it back into a shared import.

The split rule needed a decision the contract does not cover: this gate has a RETRY the contract
lacks. A RETRY/REJECT split resolves to **REJECT**, because a REJECT stops and asks while a RETRY
spins — the safe reading of a split is the one that does not loop.

**DISCOVERY — my own first draft under-counted tokens 3×.** With sampling wired, the result still
reported `_estimate_tokens` for the LAST sample: a 3-sample gate read 22 tokens where it had spent
~66. That silently un-binds the loop breaker's `max_tokens` and the run cost cap **exactly** on the
path sampling makes most expensive. A meter that reads low on the expensive path is worse than no
meter, because the cap stops binding without ever saying so. Now summed over every sample, asserted
(`three.tokens == one.tokens * 3`).

Two guards keep it additive: an absent or invalid `judge_samples` floors to **1** (pre-S145 behaviour
— a gate that never asked for sampling must not start paying for it), and the count clamps at
`MAX_JUDGE_SAMPLES = 5` so an author typo (`judge_samples: 30`) cannot quietly cost 30× on a gate that
runs every loop iteration. An unparseable sample fails the whole gate where it stands rather than
deciding from the 2 that parsed — a terminal accept decided from 2 of 3 is a quieter version of the
single-sample bug this session fixes.

Verified load-bearing: forcing `samples = 1` turns 7 of the 14 new tests red. Gate: `make lint`
(black+isort+flake8+mypy, 691 files) green; `pytest -n 4 --dist worksteal` **16096 passed, 29 skipped,
13 xfailed**. No `web/` change.

- **STILL REMAINING in LOOPS-EVOLUTION** (unchanged by this session): the middleware/steering
  run-path consumption (`loop_middleware` is redundant with the shipped `resilience.check_breaker`, so
  it needs a de-duplication decision, not a wiring), calibration emission + assessment (dead at BOTH
  ends — nothing emits `judge_verdict` and R6a's default-promotion path does not exist), the 3
  templates deferred at session 33, and the `code-project` product decision.

### S146 — `isolation: cross_model` is unenforceable, and now it says so (R1)

**DONE.** `judge_contract.Isolation` offers two levels, and the audit found they are **not equally
real**:

* **`fresh` is satisfied BY CONSTRUCTION.** A judge gate runs through `one_shot_completion`, which
  builds a temporary instance rather than reusing the worker's session — so the worker's reasoning
  trace is not in its context. Nothing to enforce, nothing to warn about.
* **`cross_model` has NO SEAM.** Measured: `dispatch_gate` receives `(node, ctx, now, verify,
  completion, tiers, mode)` and is **never told the producing stage's model**, and
  `one_shot_completion` accepts a `use_case`, not a model — so there is no argument through which a
  different model FAMILY could be demanded. `judge_actors.plan_judge_session` and
  `validate_judge_model` implement the family comparison in full and have **no caller**.

So a template asking for the strongest independence claim in the contract would silently receive the
weaker one — the anti-rubber-stamping option, reading as satisfied.

**Caught BEFORE it shipped, which is the useful part.** No bundled template declares `cross_model`
today (all judges say `isolation: fresh` — verified against every shipped `workflow.json`). But the
plan specifies `judge_isolation: cross_model` twice for templates that are still unbuilt: the
deep-research template ("claims verified by a different model family") and `code-project`. Without
this rule, the first of those to land would have shipped the claim quietly meaning fresh-session only.

**DEVIATION — the honest fix here is a LINT, not enforcement.** Cross-family selection needs the
worker's model threaded into the gate and a completion seam that accepts a model rather than a
use-case; both are real plumbing this session did not have. Inventing a cross-family choice through a
seam with no model argument would produce exactly the inert control this program keeps finding — a
check that runs, reports independence, and cannot deliver it. So `WFL_UNENFORCEABLE_ISOLATION` makes
the gap loud at authoring time and `plan_judge_session` stays ready for the day the plumbing lands.

**A WARNING, not an error**, for two reasons: the declaration is aspirational rather than wrong, and
the shipped library is held to `clean` — an error would block a template for asking for something
better than it can get. A companion test asserts no shipped template makes the claim, so the two
directions are pinned together: `test_every_judge_is_isolated_and_read_only` asserts the positive
(`fresh`), and `test_no_shipped_template_declares_an_unenforceable_isolation` asserts the negative.

**A correction I made mid-session, worth recording.** My first probe reported that
`goal-pursuit-open-ended` declared `cross_model`. It does not — it declares `fresh`; I had read a
stale value from an enum dump rather than the template. Re-measuring against the real JSON (and
against `origin/main`) is what turned this from "a shipped template is lying" into "a future template
would have". The rule is the same either way; the queue row would have been false.

Verified load-bearing: removing the rule turns the flagging test red. Gate: `make lint`
(black+isort+flake8+mypy, 691 files) green; `pytest -n 4 --dist worksteal` **16105 passed, 29 skipped,
13 xfailed**. No `web/` change.

- **STILL REMAINING in LOOPS-EVOLUTION:** cross-model isolation ENFORCEMENT (needs the worker model
  threaded into `dispatch_gate` + a model-accepting completion seam — a real plumbing session, and the
  reason this one shipped a lint); the middleware/steering run-path consumption (`loop_middleware` is
  redundant with the shipped `resilience.check_breaker`, so it needs a de-duplication decision);
  calibration emission + assessment (dead at BOTH ends); the 3 templates deferred at session 33; and
  the `code-project` product decision.

- **2026-08-11 — DONE — `code-project`: the product decision + the R5 structural gates (WF2LOO-10).**
  Branch `feature-wf2loo10-code-project`, PR PENDING. §4's template ships, criterion 6 is proven, and
  the last deferral in the list above is closed.

  **THE PRODUCT DECISION: EVOLVE `code-implementation` INTO `code-project` — replace, do not add.**
  Three reasons, in order of weight. (1) The clean-break tenet forbids dual overlapping paths: two
  near-duplicate code templates would make the template picker arbitrate between synonyms, and
  criterion 11 expects the picker to suggest `code-project` for a coding intent. (2) The alias layer
  already existed for exactly this — `loop_aliases.KIND_TO_TEMPLATE` mapped `code` →
  `code-implementation` with a comment naming this very deferral; repointing it at `code-project`
  keeps every legacy reference (a stored `kind: code`, a saved cron, a months-old transcript)
  resolving at READ time with **zero migration code**, and the alias stays one-way as that module's
  docstring demands. (3) The pre-1.0 banner permits the break, so no gate/migration machinery was
  needed (Lifecycle-Doctrine remains owner-deferred and blocked nothing). `code-implementation/` was
  deleted in the same change; every reference was swept (the two Python alias tables, the FE mirror
  `containerKey.KIND_TO_TEMPLATE` + `templateSuggest`, two bundled `when_not_to_use` lines, and six
  test files including the bundled inventory set — regenerated by hand, never weakened).

  **What each R5 gate compiles to in the engine's real vocabulary:**
  - **R5a gated initializer** → `init` (stage, `tools_posture: full`, schema carrying the four
    booleans + the breakdown) followed by `init_gate`, a `gate` of `config.kind: expression` whose
    `expr` is the four-term conjunction. Not `gate_kind`, and the four conditions are read from
    `nodes.init.output.*`.
  - **R5b WIP=1** → `runtime_hints.execution.single_active_feature: true`, enforced in TWO places
    (below), not declared in the template's `foreach`.
  - **R5c/R5f reproduction-before-edit with inverted semantics** → an unconditional `repro` stage
    carrying `config.success_when:
    "inputs.bug_flavored != true || output.fail_reproduced || output.infeasible_reason != ''"`. The
    non-bug escape reads the USER's input, deliberately: an arm keyed on a model-reported "this is
    not a bug fix" would be self-certification with extra steps.
  - **R5d baseline capture** → one `action`/`bash` node that runs both commands and `printf`s a JSON
    object of real booleans, so it ALWAYS exits 0. That shape is load-bearing: `controller._apply`
    does not publish a FAILED node's output to `_outputs`, so a baseline that exited non-zero — the
    exact case the classification exists for — would leave every downstream
    `{{nodes.baseline.output…}}` unresolvable. The commands travel as `payload` env vars, not spliced
    into the shell, so a quoted command survives and cannot become code.
  - **R5e dual verify+guard** → the final `verify` gate's `config.verify` block gained `guard` and
    `guard_baseline`; `engine._dual_guard` runs the guard only after the metric passes and classifies
    the result against the baseline.

  **The two engine mechanisms, with their live writer and live reader:**
  - `success_when` — WRITER: the shipped `repro` node. READER: `controller._check_success_when`,
    called from `_execute` next to the write-scope check. It can only NARROW success (a node that
    already failed stays failed), it binds the node's own output as a new `output.*` root on
    `BindingContext`, and an unevaluable predicate FAILS rather than passing. Written brace-free
    because `resolve_config` resolves braced bindings *before* the node runs, when `output` does not
    exist yet — `engine._condition_keys` now holds conditions back from that resolution for the same
    reason on a gate's `expr`.
  - `single_active_feature` — WRITER: `runtime_hints.execution` in the template. READERS: (a)
    `tick.frontier(single_active_feature=…)` forces every fan-out's cap to 1, overriding whatever a
    `foreach` declared, and names the items it refused in the new `Frontier.wip_held`; (b)
    `controller._journal_wip_holds` writes each held item once as a `DECISION` record, so the refusal
    is readable from the ledger; (c) `validator._validate_wip_invariant` refuses a spec declaring
    WIP=1 beside `max_concurrency > 1` (`WF_WIP_CONTRADICTION`) — the engine refuses rather than
    silently clamping, so a template cannot read one way and run another. `execution_hints.py` parses
    ONLY this field: `depth`/`escalation`/`breaker` still have no reader anywhere, and typing them
    into a dataclass would make them look enforced.

  **How criterion 6's regression-vs-pre-existing proof works.** `_dual_guard` maps a closed
  `GuardOutcome` (`skipped|clean|pre_existing|regression|undetermined`, mapped exhaustively with no
  default arm): the guard command's failure is a REGRESSION only if `guard_baseline` says it was
  PASSING before the change, in which case the gate FAILS; if it was already failing it is
  PRE_EXISTING and the gate passes DEGRADED with a reason; an unrunnable guard is UNDETERMINED and
  never a pass. The test seeds both by really running the shipped baseline node in a `tmp_path` — a
  green guard (`true`) and a red one (`false`) — then dispatches the shipped gate with a fake verifier
  that fails the guard both times, and asserts the two runs reach OPPOSITE verdicts from identical
  commands. `test_workflows_code_project.py` drives the SHIPPED spec's own nodes through the real
  dispatchers and the real `frontier` (39 tests); `test_workflows_conditions.py` covers the dialect
  (39 tests).

  **DISCOVERY — the expression gate could never reject, in two shipped templates.** It resolved its
  `expr` with `bindings.resolve` (which INTERPOLATES a template into a string) and then asked
  `_truthy` about the result, so `audit-sweep`'s `{{inputs.fix}} == true` became the string
  `"false == true"` — non-empty, therefore truthy, therefore pass. `produce-and-audit`'s
  `verdict == 'pass'` was the same shape. The new `workflows/conditions.py` PARSES the comparison and
  is the one dialect the gate, `until` loops and `success_when` all use; a test pins the old reading
  as broken so it cannot come back. This is why the four-condition checklist is a real gate rather
  than a decorative one.

  **DISCOVERY — `EngineServices.verify` was never wired in production.** `gateway.py` built
  `EngineServices(...)` with no `verify=`, and `dispatch_gate` fails INTERNAL "no verifier wired for
  this gate" without one — so every `verify_command` gate in every shipped template (this one's
  deliverable gate, `goal-pursuit-verifiable`'s final gate) could not run outside a test. Fixed by
  `verify.run_verify_block`, which delegates to `loop.gates.run_verify_command` rather than spawning
  its own subprocess: that runner already owns the security screen (`audit_bash_command`), the
  `PROFILE_TOOL` rlimits, the bounded timeout, and the same tristate where a missing binary is
  "cannot tell" rather than "failed". In scope because the atom's dual gate would otherwise have
  shipped inert.

  **DEVIATION — `isolation: fresh`, not `cross_model`, on the judge.** WF2LOO-11 made the claim
  enforceable, but it fails CLOSED when a second model family cannot be obtained, and NO shipped
  template declares it yet. Making the default coding template the first would break its final judge
  on every single-provider install. The declaration belongs to a session that can drive a real
  two-family run.

  **DEVIATION — no `when:` / `retry_route:` / per-node `cwd:`.** None are engine fields (§4's YAML
  uses all three). `when` would need a `branch`, and a branch keyed on a boolean would need the case
  spelled `"True"` (`_select_case` keys by `str(value)`) — so the bug-flavored arm became the
  `success_when` predicate instead, which also keeps `{{nodes.repro.output}}` resolvable downstream
  (a SKIPPED branch arm publishes no output, and `| default(…)` does not rescue an unresolvable node
  reference). `retry_route` is dropped. `cwd` lives on the `verify` block and in the baseline's env,
  because the bash provider takes no `cwd` and a stage's cwd is the run's.

  **DEVIATION — the baseline records pass/fail + exit codes, not "exact output".** Shell-escaping
  arbitrary command output into JSON is not robust, and the classification only needs the booleans;
  the `test` stage is what captures failure text verbatim, with full tools.

  **PREMISE MISMATCH (fourth and fifth, adding to session 33's three).** (4) The binding dialect has
  NO boolean operators — `&&`/`||`/`!`/`==` did not exist anywhere, so §4's `expr` and `success_when`
  could not have been evaluated by any existing evaluator; `conditions.py` adds them as a closed
  grammar over the existing leaf resolver rather than a second dialect. (5) `bindings._WHOLE_RE`
  reads a value that both starts and ends with `{{`/`}}` as ONE whole reference, so a two-term
  `{{a}} && {{b}}` resolved as a path literally named `a}} && {{b` — which is why conditions are now
  held back from `resolve_config`.

  Gate: `make lint` (black+isort+flake8+mypy, 802 files) green; `pytest -k "workflow or template or
  loop or bundled"` green; `web`: `npm run typecheck` clean, FULL `npm test` **144 files / 1368
  tests** green, `npm run build` clean. `docs/architecture/workflows.md`'s module table gained the two
  new modules in the same commit (its bidirectional drift test demands it).

- **2026-08-12 — DONE — `WF2LOO-12`: the judge contract's enforcement claim is now honest, and
  railed against drift.** Branch `feature-judge-contract-claims`. `judge_contract.py` opened by
  stating *"**A PASS without cited proof is invalid.** Not 'discouraged': the verdict is rejected by
  the contract"* as a description of the running engine. **It never has been.**

  **Measured.** The live judge gate (`engine.dispatch_gate`, `GateKind.JUDGE`) sends
  `f"{prompt}\n\nRespond with EXACTLY ONE word, one of: PASS, RETRY, ESCALATE, REJECT. No other
  text."` and reads the answer with `verify.parse_verdict` (`verify.py:63`), which extracts the
  verdict WORD only — over a DIFFERENT four-member enum (`verify.Verdict` carries RETRY where the
  contract carries REPLAN/NEEDS_INPUT). A bare word cannot carry proof, scores or a rubric result,
  so the contract is not merely unused there: it is inexpressible. And nothing calls it —
  `validate_verdict`, `meets_ratchet`, `compute_overall`, `detect_forbidden_modes`,
  `aggregate_samples` and `hints_from_dict` have **zero** production callers. The only things that
  leave the module are TYPES (`judge_actors.py:26` imports `Isolation`; `judge_pretier.py:246`
  imports `FallbackCheck`), plus two comments (`models.py:712`, `engine.py:1510-1511`) recording
  that `engine.py` deliberately RESTATES the sampling rule rather than importing it. Population:
  **7 judge gates in 7 bundled templates**, so enforcing the headline would invalidate every judge
  PASS in all of them — an outage, not a gate.

  **DISCOVERY — a live structured judge path DOES exist, and it already emits the contract shape.**
  The `judge` STAGE in 6 bundled templates (`code-project`, `design-project`, `diagnose-run`,
  `general-project`, `goal-pursuit-open-ended`, `goal-pursuit-verifiable`) declares
  `config.schema` = `{reasoning, verdict, scores, marginal_value, evidence_refs, proof,
  cannot_judge}`, and its prompt spells out this module's own five-verdict vocabulary verbatim.
  `dispatch_infer` really parses it — `want_json = bool(cfg.get("schema"))` (`engine.py:409`). So a
  `validate_verdict`-shaped response is produced on every loop iteration, and then **discarded**:
  `dispatch_infer` returns DONE for any parseable JSON whatever the verdict says, and **zero**
  shipped templates bind `nodes.judge.output.*`. That is what settled the option. Enforcing on that
  path could not change any outcome (nothing consumes the verdict) so it would ship INERT; making
  it decide the node instead would fail work that passes today. Both are barred by this program's
  rules, so **option A (docs honesty) is forced, not preferred.** A third live structured judge,
  `loop/judge.py`, speaks yet another vocabulary (`CycleVerdict{done, done_reason, marginal_value,
  quality_score, regressed}`) with no proof field, so the contract is not expressible there either.

  **DISCOVERY — what the rubric declarations actually reach.** 6 templates declare
  `runtime_hints.judge.rubric` (14 criteria total). `hints_from_dict` is the only parser and has no
  caller, so as DATA the rubric is inert. As PROSE it does arrive: `WF2LOO-3`'s log claims "a test
  asserting every declared criterion reaches a judge", and that test is
  `test_workflows_loop_templates.py::test_every_rubric_criterion_appears_in_a_judge_prompt` — it
  asserts the criterion STRING is a substring of some judge node's `prompt`. Text inlining, not
  scoring. No `target_score` is ever compared against a returned score, because `meets_ratchet` has
  no caller. The claim is literally true and much weaker than it reads.

  **What shipped.** The module docstring now says enforcement has no caller, and carries a measured
  "what the live judges actually do" section naming both live paths, the rubric's prose-only
  reachability, and the wiring atom. **No function was deleted** (they are authored contract), and
  **nothing became stricter**: `engine.py` and `verify.py` are byte-identical to `HEAD`, so a
  user's judge gate does exactly what it did before this change — one-word prompt, `parse_verdict`,
  same verdict→state mapping. No `web/` change.

  **The rail.** `test_workflows_judge_contract.py::test_the_unwired_enforcement_claim_matches_the_live_path`
  fails in BOTH drift directions: (a) any of the six entry points gains a production caller while
  the docstring still says enforcement is unwired; (b) the live gate stops demanding one bare word
  while the docstring still gives that as the reason for not wiring. Two vacuity floors keep it from
  matching nothing — every entry point must still be `def`ined in `judge_contract.py`, and
  `GateKind.JUDGE` must be found inside `dispatch_gate` before any conclusion is drawn from what its
  text lacks. **Proven able to fail by three probes, each reverted with a targeted edit:** deleting
  the `Enforcement is NOT wired` marker (1 failed), injecting a `validate_verdict` caller into
  `verify.py` (1 failed, naming the caller), and switching the gate prompt to JSON (1 failed). It
  deliberately does NOT assert enforcement stays unwired — wiring it is `WF2LOO-13`.

  **`WF2LOO-13` recorded (todo) with the measured blast radius** so the wiring is a scoped decision
  rather than a surprise: 7 judge gates in 7 templates change prompt + parse; 6 templates × 14
  rubric criteria become live thresholds for the first time (an unscored criterion is a
  `not scored` shortfall — a REJECT — under `Ratchet.STRICT`); every judge answer grows from ~1
  token to a JSON object on a gate that runs every loop iteration, with a more frequent PROTOCOL
  failure mode on unparseable JSON; and the two verdict enums must converge to one closed set
  rather than be bridged. Its first step is measuring how many real judge responses would satisfy
  the contract BEFORE it gets teeth — which is why it depends on `WF2LOO-7` (the tick that actually
  emits `judge_verdict` ledger events; `judge_calibration.VerdictRecord` is the reader).

  **DEVIATION — the rail lives in `tests/test_workflows_judge_contract.py`, not in
  `tests/test_inert_surface_baseline.py`.** `PHF-13` already pins `validate_verdict` and
  `hints_from_dict` as caller-free there, and that rail belongs to the census. This one is about
  the DOCSTRING's honesty versus the live path, which is WF2LOO's contract, so it sits with the
  module's own tests and the generated inert-surface baseline is untouched (no regeneration needed).

  Gate: `make lint` green; `pytest -k "judge or verdict or gate or rubric"` green; the roadmap
  derived block regenerated with `tools/regen_dag_derived.py` (614 atoms, 847 edges; the 2 cycles
  and 1 unresolved ext-ref are pre-existing and unrelated). The source plan's decomposition count
  moved 11 → 13.
