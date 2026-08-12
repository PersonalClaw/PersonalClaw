# WORKFLOWS-V2-LOOPS-EVOLUTION — atomic plans

**Source plan:** [`WORKFLOWS-V2-LOOPS-EVOLUTION`](../plans/WORKFLOWS-V2-LOOPS-EVOLUTION.md)  
**Code:** `WF2LOO`  
**Source status:** in_progress



Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `WF2LOO-1` | ✅ (##167) | Judge contract + runtime_hints + engine-enforced primitives | `EXT:WORKFLOWS-V2:run engine + WorkflowDef (Slices 0-2)` | workflows/judge_contract.py (closed verdict enum, rubric ratchet, engine-computed overall, N-sample median), judge_pretier.py (free rule tier + tristate fallback_check), judge_actors.py (worker-cannot-reach-done actor transition, isolation, provenance blinding, narration-excluding evidence assembly) ship; runtime_hints added opaque to WorkflowDef via hints_from_dict; engine tests prove worker cannot transition to done, PASS-without-proof invalid, PASS-contradicts-deterministic escalates (PR #167). |
| `WF2LOO-2` | ✅ | Engine loop-node middleware decision layer (breaker + escalation + interrupt queue) | `WF2LOO-1` | workflows/loop_middleware.py provides 7-way failure classification, tool-argument fingerprinting, Continue->Nudge->Escalate->Halt ladder with per-class entry rungs and recoverable-class headroom, structured never-silence brief, and atomic interrupt queue; 4 new Run Ledger kinds registered in the FE RUN_LIFECYCLE union with a bidirectional drift test. Built on resilience.check_breaker (not replacing it). NOTE: pure decision layer, not yet wired into the RunController tick (that is WF2LOO-7). |
| `WF2LOO-3` | ✅ | Author the bundled loop templates (5 of 8) | `WF2LOO-1` | Five bundled templates ship with runtime_hints + judge contract wired: goal-pursuit-open-ended, goal-pursuit-verifiable, general-project, design-project, diagnose-run; convention suite extended 6->11 templates; 132 structural integration tests green. Premise-mismatch corrections recorded: {{defaults.runtime_hints.*}} binding, gate_kind, and until-config forms do not exist in the engine, so rubrics are inlined into judge prompts with a test asserting every declared criterion reaches a judge. |
| `WF2LOO-4` | ✅ | Calibration + acceptance-instrumentation modules + template lint | `WF2LOO-1` | workflows/judge_calibration.py (verdict ledger incl. status=discard, typed divergence records, nodding-loop detector, free stuck detection, judge canary sharing loop/instrument threshold, dangerous-direction-first hardening exemplars) + template_lint.py (six anti-pattern rules + five-moves audit; KNOWN_ANTI_PATTERNS empty and a test keeps it empty) ship. NOTE: not wired into the run path — nothing emits judge_verdict/judge_divergence yet (that is WF2LOO-7). |
| `WF2LOO-5` | ✅ | FE coexistence scaffolding: legacy aliases, steering endpoints, cockpit key-equivalence helper | `WF2LOO-2`, `WF2LOO-3` | workflows/loop_aliases.py (one-way read-time legacy-kind aliases, each asserted against a shipped template) + cockpit stream-key equivalence helper mirrored in web/src/pages/workflows/containerKey.ts with a backend<->FE drift test; /steer and /steering run endpoints stored; alias manifest surfaced via /api/workflows/manifest. NOTE: steering queue is stored/surfaced but not consumed; picker widget + cockpit keysEquivalent usage not mounted (that is WF2LOO-7/WF2LOO-8). |
| `WF2LOO-6` | ✅ | Close inert judge/gate controls at the dispatcher: pre-tier + judge_samples + cross_model lint | `WF2LOO-1`, `WF2LOO-3` | dispatch_gate runs _judge_pretier_screen before the model call (opt-in via an evidence binding; existence gate defaults off) short-circuiting rule-solvable rejects with failure_class (S144); judge_samples aggregated with the documented median rule over the gate's own verify.Verdict enum, tokens summed over samples, floored to 1 / clamped at MAX_JUDGE_SAMPLES=5 (S145); WFL_UNENFORCEABLE_ISOLATION lint warns when a template declares cross_model with no enforcement seam + a test asserts no shipped template makes the claim (S146). Each verified load-bearing by removal; gate green. |
| `WF2LOO-7` | ⬜ | Wire the decision layers into the RunController tick (steering + calibration emission + R6a) | `WF2LOO-2`, `WF2LOO-4`, `WF2LOO-5` | The RunController tick consumes the stored steering/interrupt queue atomically at each iteration boundary and triggers plan re-evaluation (journaled steering event); the tick emits judge_verdict (with evidence chain, discard status) and judge_divergence Run Ledger events; the nodding-loop detector blocks a 100%-pass template from becoming its kind's default (R6a); loop_middleware breaker de-duplicated against the shipped resilience.check_breaker (clean break, no dual path). Verified: judges reject at least once with evidence over parity runs (criterion 3); an interrupt re-ranks a running plan (criterion 8, engine side). |
| `WF2LOO-8` | ⬜ | FE surfaces + as-a-user coexistence validation | `WF2LOO-7` | Template-picker widget ('Start from template') suggests code-project for coding intents and deep-research for research intents and resolves legacy loop-kind names via aliases (criterion 11); cockpit live-follow switched to keysEquivalent so template-run SSE events are not dropped (R10c); interrupt-queue + judge-comment-triage + per-project judge-prompt-override UI wired; an accepted judge comment reaches the worker session (criterion 8, UI side); mid-flight edit surfaces the re-validate warning (criterion 9); end-to-end as-a-user validation of the bundled templates passes. |
| `WF2LOO-9` | ⬜ | goal-pursuit-monitor template + self-schedule tool module + bounds config (R15) | `WF2LOO-3`, `EXT:AUTOMATION-SUBSTRATE:trigger entities + resume-targets (set_onetime_task/set_recurring_task, AUTO-R11)` | goal-pursuit-monitor ships as a parked-run-plus-self-created-clock-trigger template; a new set_onetime_task/set_recurring_task tool module is registered via mcp_core._TOOL_MODULES + tool_providers/registry.py; workflows.self_schedule_max_outstanding wired through all four config points (dataclass _meta, load(), to_dict(), _EDITABLE_CONFIG PATCH) with mandatory per-trigger TTL + provenance tag + autonomy-mode gating. Verified: a monitor run parks between checks via a self-created trigger visible in the cockpit with provenance+TTL and survives a gateway restart (criterion 7). BLOCKED until the substrate trigger entities/resume-targets exist — do not ship a template whose central mechanism silently no-ops. |
| `WF2LOO-10` | ✅ | code-project template (product decision + build) | `WF2LOO-3` | The product decision is made (replace the shipped code-implementation template vs. add code-project beside it) and the chosen template ships with the R5 structural gates: gated initializer (4-condition checklist), WIP=1 engine-enforced single_active_feature, reproduction-before-edit with inverted success_when for bug-flavored runs, baseline capture, and dual verify+guard gate. Verified: a 'build a feature' run passes the init-gate, holds WIP=1, and classifies a seeded regression vs a pre-existing failure via the baseline diff (criterion 6). |
| `WF2LOO-11` | ⬜ | cross_model judge isolation enforcement | `WF2LOO-1`, `WF2LOO-6` | dispatch_gate receives the producing stage's model and the completion seam accepts a model (not just a use_case) so judge_actors.plan_judge_session/validate_judge_model can demand a different model FAMILY; a template declaring isolation: cross_model provably runs a different family judge; the WFL_UNENFORCEABLE_ISOLATION lint (from WF2LOO-6) is retired now that the claim is enforceable. Unblocks the cross_model declarations in deep-research and code-project. |

## Atom scopes

### `WF2LOO-1` — Judge contract + runtime_hints + engine-enforced primitives

**Status:** done (PR ##167)

Judge Design: Maker/Checker With Teeth (R1/R2/R8); Architecture: Templates + Runtime Behavior Layer (runtime_hints two-group split, engine-enforced invariants); Changes to WORKFLOWS-V2.md #2-#5; Implementation Effort Session 1

**Done when:** workflows/judge_contract.py (closed verdict enum, rubric ratchet, engine-computed overall, N-sample median), judge_pretier.py (free rule tier + tristate fallback_check), judge_actors.py (worker-cannot-reach-done actor transition, isolation, provenance blinding, narration-excluding evidence assembly) ship; runtime_hints added opaque to WorkflowDef via hints_from_dict; engine tests prove worker cannot transition to done, PASS-without-proof invalid, PASS-contradicts-deterministic escalates (PR #167).

### `WF2LOO-2` — Engine loop-node middleware decision layer (breaker + escalation + interrupt queue)

**Status:** done

Engine Behaviors: no-progress circuit breaker + escalation ladder (R4), fresh-session/worker-lifecycle protocol (R7), context-overflow recovery (R13), interrupt queue (R14); Implementation Effort Session 2

**Done when:** workflows/loop_middleware.py provides 7-way failure classification, tool-argument fingerprinting, Continue->Nudge->Escalate->Halt ladder with per-class entry rungs and recoverable-class headroom, structured never-silence brief, and atomic interrupt queue; 4 new Run Ledger kinds registered in the FE RUN_LIFECYCLE union with a bidirectional drift test. Built on resilience.check_breaker (not replacing it). NOTE: pure decision layer, not yet wired into the RunController tick (that is WF2LOO-7).

### `WF2LOO-3` — Author the bundled loop templates (5 of 8)

**Status:** done

Per-Kind Template Designs (goal-pursuit, general-project, design-project, diagnose); Implementation Effort Session 3

**Done when:** Five bundled templates ship with runtime_hints + judge contract wired: goal-pursuit-open-ended, goal-pursuit-verifiable, general-project, design-project, diagnose-run; convention suite extended 6->11 templates; 132 structural integration tests green. Premise-mismatch corrections recorded: {{defaults.runtime_hints.*}} binding, gate_kind, and until-config forms do not exist in the engine, so rubrics are inlined into judge prompts with a test asserting every declared criterion reaches a judge.

### `WF2LOO-4` — Calibration + acceptance-instrumentation modules + template lint

**Status:** done

Judge Design: Calibration becomes measurable (R3); Migration Path: Retirement acceptance criteria (R6), Phase 2 template metadata hygiene (R10b); Implementation Effort Session 4

**Done when:** workflows/judge_calibration.py (verdict ledger incl. status=discard, typed divergence records, nodding-loop detector, free stuck detection, judge canary sharing loop/instrument threshold, dangerous-direction-first hardening exemplars) + template_lint.py (six anti-pattern rules + five-moves audit; KNOWN_ANTI_PATTERNS empty and a test keeps it empty) ship. NOTE: not wired into the run path — nothing emits judge_verdict/judge_divergence yet (that is WF2LOO-7).

### `WF2LOO-5` — FE coexistence scaffolding: legacy aliases, steering endpoints, cockpit key-equivalence helper

**Status:** done

Migration Path Phase 1 read-time aliases (R10a), Phase 2 cockpit live-follow key equivalence (R10c); Mid-run human steering channel (R14); Implementation Effort Session 5

**Done when:** workflows/loop_aliases.py (one-way read-time legacy-kind aliases, each asserted against a shipped template) + cockpit stream-key equivalence helper mirrored in web/src/pages/workflows/containerKey.ts with a backend<->FE drift test; /steer and /steering run endpoints stored; alias manifest surfaced via /api/workflows/manifest. NOTE: steering queue is stored/surfaced but not consumed; picker widget + cockpit keysEquivalent usage not mounted (that is WF2LOO-7/WF2LOO-8).

### `WF2LOO-6` — Close inert judge/gate controls at the dispatcher: pre-tier + judge_samples + cross_model lint

**Status:** done

Judge Design: Deterministic tier before/beneath every judge (R8), Typed verdict contract judge_samples median (R2c), judge isolation contract (R1); Execution log S144/S145/S146

**Done when:** dispatch_gate runs _judge_pretier_screen before the model call (opt-in via an evidence binding; existence gate defaults off) short-circuiting rule-solvable rejects with failure_class (S144); judge_samples aggregated with the documented median rule over the gate's own verify.Verdict enum, tokens summed over samples, floored to 1 / clamped at MAX_JUDGE_SAMPLES=5 (S145); WFL_UNENFORCEABLE_ISOLATION lint warns when a template declares cross_model with no enforcement seam + a test asserts no shipped template makes the claim (S146). Each verified load-bearing by removal; gate green.

### `WF2LOO-7` — Wire the decision layers into the RunController tick (steering + calibration emission + R6a)

**Status:** todo

Implementation Effort Session 5 back half; Engine Behaviors: interrupt queue consumption (R14), Calibration becomes measurable (R3 emission), Retirement acceptance criteria default-promotion (R6a); Success Criteria 3, 8

**Done when:** The RunController tick consumes the stored steering/interrupt queue atomically at each iteration boundary and triggers plan re-evaluation (journaled steering event); the tick emits judge_verdict (with evidence chain, discard status) and judge_divergence Run Ledger events; the nodding-loop detector blocks a 100%-pass template from becoming its kind's default (R6a); loop_middleware breaker de-duplicated against the shipped resilience.check_breaker (clean break, no dual path). Verified: judges reject at least once with evidence over parity runs (criterion 3); an interrupt re-ranks a running plan (criterion 8, engine side).

### `WF2LOO-8` — FE surfaces + as-a-user coexistence validation

**Status:** todo

Migration Path Phase 2 (R10c cockpit), Mid-run human steering channel (R14 comment-triage + judge-prompt override UI); Implementation Effort Session 5; Success Criteria 8, 9, 11

**Done when:** Template-picker widget ('Start from template') suggests code-project for coding intents and deep-research for research intents and resolves legacy loop-kind names via aliases (criterion 11); cockpit live-follow switched to keysEquivalent so template-run SSE events are not dropped (R10c); interrupt-queue + judge-comment-triage + per-project judge-prompt-override UI wired; an accepted judge comment reaches the worker session (criterion 8, UI side); mid-flight edit surfaces the re-validate warning (criterion 9); end-to-end as-a-user validation of the bundled templates passes.

### `WF2LOO-9` — goal-pursuit-monitor template + self-schedule tool module + bounds config (R15)

**Status:** todo

Per-Kind Template Designs 1: Goal Loop monitor variant (R15); Changes to WORKFLOWS-V2.md #9; Success Criteria 7

**Done when:** goal-pursuit-monitor ships as a parked-run-plus-self-created-clock-trigger template; a new set_onetime_task/set_recurring_task tool module is registered via mcp_core._TOOL_MODULES + tool_providers/registry.py; workflows.self_schedule_max_outstanding wired through all four config points (dataclass _meta, load(), to_dict(), _EDITABLE_CONFIG PATCH) with mandatory per-trigger TTL + provenance tag + autonomy-mode gating. Verified: a monitor run parks between checks via a self-created trigger visible in the cockpit with provenance+TTL and survives a gateway restart (criterion 7). BLOCKED until the substrate trigger entities/resume-targets exist — do not ship a template whose central mechanism silently no-ops.

### `WF2LOO-10` — code-project template (product decision + build)

**Status:** done (PR PENDING)

Per-Kind Template Designs 4: Code/SDLC Loop -> code-project (R5); Success Criteria 6

**Done when:** The product decision is made (replace the shipped code-implementation template vs. add code-project beside it) and the chosen template ships with the R5 structural gates: gated initializer (4-condition checklist), WIP=1 engine-enforced single_active_feature, reproduction-before-edit with inverted success_when for bug-flavored runs, baseline capture, and dual verify+guard gate. Verified: a 'build a feature' run passes the init-gate, holds WIP=1, and classifies a seeded regression vs a pre-existing failure via the baseline diff (criterion 6).

### `WF2LOO-11` — cross_model judge isolation enforcement

**Status:** todo

Judge Design: judge stage contract Isolated+provenance-blinded (R1); Engine-enforced invariant #1 (judge-tier isolation); Execution log S146 remaining

**Done when:** dispatch_gate receives the producing stage's model and the completion seam accepts a model (not just a use_case) so judge_actors.plan_judge_session/validate_judge_model can demand a different model FAMILY; a template declaring isolation: cross_model provably runs a different family judge; the WFL_UNENFORCEABLE_ISOLATION lint (from WF2LOO-6) is retired now that the claim is enforceable. Unblocks the cross_model declarations in deep-research and code-project.

