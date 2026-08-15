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
| `WF2LOO-12` | ✅ (#PENDING) | Make `judge_contract`'s enforcement claim honest, and rail it against drift | `WF2LOO-1`, `WF2LOO-6` | `judge_contract.py`'s docstring stops asserting live enforcement ("a PASS without cited proof is invalid ... rejected by the contract") and instead records, measured, that its six enforcement entry points have ZERO production callers, names both live judge paths that supersede it (the one-word `GateKind.JUDGE` gate read by `verify.parse_verdict`; the 6 templates' contract-shaped `judge` STAGE whose output nothing reads), and states what wiring would take. No function is deleted and no judge gate is made stricter: `engine.py` and `verify.py` stay byte-identical. One rail (`test_the_unwired_enforcement_claim_matches_the_live_path`) fails in both directions — enforcement gaining a caller while the docstring still says it has none, and the live gate leaving its one-word prompt while the docstring still gives that as the reason — proven able to fail by three probes (marker removed; injected `validate_verdict` caller; gate prompt switched to JSON) each reverted by a targeted edit, plus two vacuity floors. |
| `WF2LOO-13` | ✅ (#PENDING) | Wire the judge contract into the live judge path (blast radius measured by `WF2LOO-12`) | `WF2LOO-12`, `WF2LOO-7` | The live judge path speaks the contract: `GateKind.JUDGE` asks for the contract JSON instead of one bare word, `validate_verdict` decides the gate (so a PASS without `proof`/`evidence_refs` is rejected rather than accepted), `meets_ratchet` compares the 14 declared `runtime_hints.judge` rubric criteria against their `target_score` for the first time, `hints_from_dict` becomes the parser of the declarations 6 templates already carry, and the 6 judge STAGES' already-contract-shaped output is validated and bound by the templates that produce it. The two verdict vocabularies (`judge_contract.Verdict` PASS/REJECT/REPLAN/ESCALATE/NEEDS_INPUT vs `verify.Verdict` PASS/RETRY/ESCALATE/REJECT) are reconciled to one, not bridged. Measured blast radius: 7 judge gates in 7 bundled templates change prompt + parse; 6 templates x 14 rubric criteria become live thresholds; every judge answer grows from one token to a JSON object on a gate that runs every loop iteration (cost + latency + a new PROTOCOL failure mode on unparseable JSON). Ship behind a measurement of how many real judge responses would satisfy the contract BEFORE it gets teeth — enforcing it against today's population is an outage, which is why `WF2LOO-12` deliberately did not. |
| `WF2LOO-14` | ✅ (#PENDING) | Make `until_dry` read the `progress_field` its templates declare | `WF2LOO-3` | `controller._advance_loop` decides dryness from the loop's declared `progress_field` instead of from the whole output of whichever node happened to finish the iteration: `_progress_reading` states one exhaustive per-type rule (`None`/`False`/`0`/blank/empty ⇒ dry; anything else ⇒ progress; an unmapped type ⇒ *unreadable*, never swallowed), and `_progress_value` finds the field anywhere in the iteration's body — necessary because both declaring templates emit it from the FIRST stage of a sequence body and end the iteration on a judge stage whose schema has no such key, so a last-leaf-only read would have shipped inert. A loop declaring no field keeps the whole-output rule byte-for-byte, and a declared-but-absent or unreadable field falls back to it rather than counting as dryness (a missing key must not truncate a user's run). `streak` semantics untouched. Blast radius is exactly two shipped templates: `goal-pursuit-open-ended` (`new_findings_count`) and `general-project` (`meaningful_progress`) now end after two zero-progress cycles instead of running to their 12/6 iteration cap; both bodies DO emit their declared field, so no template needed fixing. Driven through a real controller (ends on two zero-progress iterations; does NOT end while progress is reported; a progress cycle resets the streak; an absent field runs to the cap), plus a rail over every bundled template that a declared field is emitted by a body schema AND named in that node's prompt — proven able to fail by two probes (call site reverted to `_is_dry`, one template's field renamed) each reverted by a targeted edit. |
| `WF2LOO-15` | ✅ | judge_actors: separate the enforced invariant from the authored one, and rail the claim | `WF2LOO-12` | judge_actors' docstring distinguishes the ENFORCED invariant (judge isolation — plan_judge_session/validate_judge_model called from engine.dispatch_gate) from the AUTHORED-but-unwired one (the worker-transition rule: check_transition/resolve_transition have no production caller because node transitions carry no actor — controller.py's actor belongs to the mutation queue), and names blind_provenance/assemble_judge_evidence as unwired for the same reason; tests/test_workflows_judge_actors_claims.py pins the claim to the call graph in BOTH directions and is proven to fail when the marker is removed AND when a caller is wired while the marker stays; WF2LOO-13's scope names all four functions so the unwired judge surface has ONE owner |
| `WF2LOO-16` | ⬜ | Reconcile the THIRD verdict vocabulary (loop/judge.CycleVerdict) into the contract | `WF2LOO-13` | `judge_contract.Verdict` absorbs what only `CycleVerdict` carried — `marginal_value` and `regressed` become contract fields with the same 0-5 clamp and the same asymmetric adjudication rule (`loop/judge.adjudicate`: a `done` survives only if the skeptic also says done; a `regressed` survives if EITHER judge flags it) — and `CycleVerdict` is DELETED, not bridged. The loop judge's ground-truth observation (`_observe_ground_truth`: runs the verify command, reads the named deliverable across workspace plus fallback dirs, injects it labelled authoritative) survives unchanged as the loop-side evidence source feeding the contract's `evidence_refs`. Population measured before enforcement, per this program's rule: how many real loop verdicts would satisfy `validate_verdict` is counted BEFORE the proof precondition applies to a loop cycle. Blast radius named: every loop kind that writes a verdict, plus `watchdog._publish_cycle_verdict` and the cockpit's ROI rail / verdict panel which read `marginal_value`/`quality_score`/`regressed` off the persisted shape. |
| `WF2LOO-17` | ✅ | Give the loop judge a model binding independent of the worker it grades | — | A `loops.judge_use_case` config field is wired through all four config points (dataclass + `_meta`, `load()`, `to_dict()`, the `_EDITABLE_CONFIG` PATCH allowlist) defaulting to `reasoning`, and `assess_cycle` / `assess_cycle_skeptic` / `gates.judge_verdict` resolve it instead of `"loops"`. The docstring's claim becomes true rather than being softened. A test asserts the judge's resolved binding differs from the worker's whenever the two use cases resolve to different entries, and a second asserts the degraded path is unchanged (a judge whose provider cannot start still returns None — defer, never a false complete — and still logs WARNING so the degradation stays diagnosable). Cheap by construction: no new mechanism, one field and three call sites. |
| `WF2LOO-18` | ✅ | Give the loops engine a worker-independent progress signal | — | The loops watchdog gains a progress signal the worker cannot author: byte-identical-output detection over the cycle's finding content (the same rule `resilience.check_breaker` already applies, reused rather than reimplemented) and repeated-call fingerprinting where a cycle records tool calls. The self-reported `new_findings_count` is KEPT as a cheap first signal — it is genuinely informative when honest — but it can no longer be the only one, and its absence stops reading as progress. `_STAGNATION_WINDOW` becomes a `LoopsConfig` field wired through all four config points. Verified by a driven loop whose worker reports a nonzero count every cycle while emitting identical content: it now stalls, and the test is proven able to fail by reverting the detector. |

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


### `WF2LOO-12` — Make `judge_contract`'s enforcement claim honest, and rail it against drift

**Status:** ✅ done (#PENDING)

An HONESTY atom, not a feature atom: it changes what the module SAYS to match what the code
DOES, and it deliberately builds none of the missing wiring. No function is deleted; no judge
gate becomes stricter.

**Done when:** `judge_contract.py`'s docstring stops asserting live enforcement and records the
measurement instead — six enforcement entry points with zero production callers, both live judge
paths named, and what wiring would take; `engine.py` and `verify.py` are byte-identical, so a
user's judge gate behaves exactly as before; one rail fails in both drift directions and is
proven able to fail by three probes reverted with targeted edits; the wiring work is recorded as
`WF2LOO-13` with its measured blast radius.

**Design**

`WF2LOO-1` shipped `judge_contract.py` and its "Done when" claims *"engine tests prove ...
PASS-without-proof invalid"*. The module opens with the same claim as a statement of fact:

> **A PASS without cited proof is invalid.** Not "discouraged": the verdict is rejected by the
> contract.

**The live judge gate has never run that contract.** Measured on this tree:

| Fact | Evidence |
|---|---|
| the live gate demands ONE WORD | `engine.py` judge branch: `f"{prompt}\n\nRespond with EXACTLY ONE word, one of: PASS, RETRY, ESCALATE, REJECT. No other text."` |
| the live parse keeps only the word | `verify.py:63` `parse_verdict` — returns a `verify.Verdict`, no `scores`, no `evidence_refs` |
| a DIFFERENT enum | `verify.Verdict` is PASS/RETRY/ESCALATE/REJECT; this module's is PASS/REJECT/REPLAN/ESCALATE/NEEDS_INPUT |
| enforcement has no caller | `validate_verdict`, `meets_ratchet`, `compute_overall`, `detect_forbidden_modes`, `aggregate_samples`, `hints_from_dict` → 0 production callers (only `Isolation` / `FallbackCheck`, the TYPES, are imported by `judge_actors.py:26` / `judge_pretier.py:246`) |
| blast radius of enforcing it | 7 judge gates in 7 bundled templates; 6 templates declare `runtime_hints.judge.rubric` (14 criteria) |

So enforcing the headline against the shipped population would invalidate **every** judge PASS in
all 7 templates. This program's rule is explicit — giving a never-run control teeth before the
population satisfies it is an outage, not a gate — so the enforcement stays unwired and the
DOCSTRING is what changes.

**The measurement also found a second live judge path, and it is the interesting one.** Six
templates' `judge` STAGE (`code-project`, `design-project`, `diagnose-run`, `general-project`,
`goal-pursuit-open-ended`, `goal-pursuit-verifiable`) declares `config.schema` =
`{reasoning, verdict, scores, marginal_value, evidence_refs, proof, cannot_judge}` and its prompt
spells out THIS module's five-verdict vocabulary. `dispatch_infer` genuinely parses that JSON
(`want_json = bool(cfg.get("schema"))`). A contract-shaped judge response is therefore produced
on every loop iteration — and then **discarded**: `dispatch_infer` returns DONE for any parseable
JSON whatever the verdict says, `validate_verdict` never sees it, and **no shipped template binds
`nodes.judge.output.*`** (measured: zero references in all six).

That closes the "is there a live structured path to enforce on?" question in the only way that
settles the atom. There is one, but enforcing there cannot change any outcome, because nothing
consumes the verdict — so any enforcement added there today ships INERT by construction; and
making it decide the node instead would fail work that passes today. Both are barred, which makes
the docs-honesty option forced rather than preferred.

**What the rubric declarations actually reach.** `runtime_hints.judge.rubric` (6 templates, 14
criteria) is parsed only by `hints_from_dict`, which has no production caller. The criteria DO
reach the model, but as PROSE: `WF2LOO-3`'s log claims "a test asserting every declared criterion
reaches a judge", and that test —
`test_workflows_loop_templates.py::test_every_rubric_criterion_appears_in_a_judge_prompt` — asserts
the criterion STRING is a substring of some judge node's `prompt`. Text inlining, not scoring. No
`target_score` is ever compared against a returned score, because `meets_ratchet` has no caller.
The claim is literally true and much weaker than it reads.

**Implementation plan**

1. Measure before writing: census the bundled templates for judge gates (7 / 7 templates), for
   `runtime_hints.judge.rubric` (6 templates, 14 criteria), and for `nodes.judge.output.*`
   bindings (zero). Trace one rubric criterion end to end to establish it arrives as prose only.
2. Answer the structured-path question explicitly rather than assuming the gate is the only judge:
   check `judge_pretier`, `judge_actors`, the ladder gate, `judge_calibration`, `loop/judge.py`
   and the `schema` → `want_json` stage path. (`loop/judge.py` is a third, live, structured judge
   with its OWN vocabulary — `CycleVerdict{done, marginal_value, quality_score, regressed}` — and
   no proof field, so the contract is not expressible there either.)
3. Rewrite the module docstring: keep every authored rule, but say up front that enforcement has
   no caller, and add a measured "what the live judges actually do" section naming both paths.
4. Add the two-directional rail with vacuity floors (entry points must still be defined; the
   judge branch must still be found before concluding anything from the gate's text).
5. Prove the rail fails: remove the marker; inject a `validate_verdict` production caller; switch
   the gate prompt to JSON. Revert each with a targeted edit and confirm `engine.py` / `verify.py`
   are byte-identical to `HEAD`.
6. Record the wiring work as `WF2LOO-13` with the measured blast radius so it is a scoped decision
   rather than a surprise.

### `WF2LOO-13` — Wire the judge contract into the live judge path (blast radius measured by `WF2LOO-12`)

**Status:** done (PR #PENDING)

**As landed.** The population was measured before the control got teeth, and two of
`WF2LOO-12`'s numbers moved: the rubric is **13** criteria, not 14, and exactly **1** of the 7
judge gates sits in a template that declares a rubric at all — so on 6 of 7 the ratchet has
nothing to compare and is a no-op by construction. That is the first of three rules that keep
enforcement off the live templates' throat; the other two are that the prompt is GENERATED from
the same `JudgeHints` the validation reads (`judge_instruction` names the exact score keys
`meets_ratchet` will look up), and that `score_for` matches a criterion exact → normalized →
uniquely-contained, so a judge that restated a key still scores. `verify.Verdict` was DELETED and
`judge_contract.Verdict` survives with `RETRY` merged in; `engine._aggregate_gate_verdicts` went
with it, its two rules absorbed into `aggregate_samples`. Unparseable JSON is a NAMED
`FailureClass.PROTOCOL` with the raw text on `judge_evidence`, never a silent pass. The judge
STAGE seam (`engine.apply_judge_contract`, opt-in `judge_contract: true`) validates and BINDS but
does not fail the node — those prompts say "reporting real issues is the normal outcome", so
failing on a REJECT would convert normal operation into a failed run. DEVIATION: 4 of 6 templates
gained a `{{nodes.judge.output.*}}` / `{{last.output.*}}` binding; `design-project` and
`diagnose-run` have no node after their terminal judge, so their binding is the validated run
output rather than an expression. Full record in `dag.json`'s evidence field and the plan's
execution log.

Judge Design: Typed Verdict Contract (R2/R2c), Runtime Hints rubric convergence contract (R3);
the wiring half of what `WF2LOO-1` authored and `WF2LOO-12` measured as unwired.

**Done when:** the live judge path speaks the contract — `GateKind.JUDGE` asks for the contract
JSON instead of one bare word, `validate_verdict` decides the gate, `meets_ratchet` compares the
14 declared rubric criteria against their `target_score` for the first time, `hints_from_dict`
becomes the parser of declarations 6 templates already carry, and the 6 judge STAGES' already
contract-shaped output is validated and bound by the templates that produce it. The two verdict
vocabularies are reconciled to ONE, not bridged.

**Design**

The cheap half is already done by accident: 6 templates' judge stages emit exactly the contract
shape, so the *prompt engineering* for a structured judge exists and works. The expensive half is
the gate (7 gates, 7 templates) and the consequences of turning declarations into thresholds.

Measured blast radius (from `WF2LOO-12`):

* **7 judge gates across 7 bundled templates** (`gap-healing`, `goal-pursuit-open-ended`,
  `knowledge-lint`, `knowledge-synthesis`, `publish-article`, `rich-ingest`, `thesis-tracker`)
  change both prompt and parse.
* **6 templates x 14 rubric criteria** become live thresholds for the first time; a criterion the
  judge does not score is a `not scored` shortfall under `Ratchet.STRICT`, i.e. a REJECT.
* **Cost:** every judge answer grows from ~1 token to a JSON object, on a gate that runs every
  loop iteration; plus a new PROTOCOL failure mode when the JSON does not parse (today an
  unparseable word already fails the gate, so the shape of the failure is not new — its frequency
  is).
* **Vocabulary:** `verify.Verdict` (PASS/RETRY/ESCALATE/REJECT) and `judge_contract.Verdict`
  (PASS/REJECT/REPLAN/ESCALATE/NEEDS_INPUT) must converge. `engine.py` currently restates the
  sampling rule precisely to avoid feeding one vocabulary's values to the other's aggregator.

**Implementation plan**

1. Measure the population BEFORE giving the control teeth: run the 7 judge gates with the
   contract prompt and count how many real responses would satisfy `validate_verdict`
   (proof/evidence_refs present, every rubric criterion scored). Enforcement lands only after
   that number is known — this is the step whose absence made `WF2LOO-12` a docs atom.
2. Reconcile the two verdict enums to one closed set, deleting the loser (clean break).
3. Move the gate to the contract prompt + `validate_verdict`, keeping the pre-tier, sampling and
   cross-model isolation seams `WF2LOO-6`/`WF2LOO-11` already built.
4. Validate and bind the 6 judge STAGES' output so a stage verdict stops being discarded.
5. Retire `WF2LOO-12`'s rail in the same change: the docstring stops saying "enforcement is NOT
   wired", which is exactly the direction that rail is built to catch.

### `WF2LOO-14` — Make `until_dry` read the `progress_field` its templates declare

**Status:** ✅ done (#PENDING)

An inert-control atom over `WF2LOO-3`'s templates: `until_dry` termination measured something
other than what the templates declare, so the mode's own exit could not fire for either template
that asked for it.

**Done when:** dryness is decided from the loop's declared `progress_field` with one exhaustive
per-type rule; loops declaring none keep today's whole-output rule byte-for-byte; an absent or
unreadable field falls back rather than ending a loop; `streak` is untouched; a driven controller
test proves the loop ENDS on two zero-progress iterations and does NOT end while progress is
reported; a rail over every bundled template proves a declared field can actually be emitted by
its body.

**Design**

`controller._is_dry(output)` — *"Did an iteration surface anything new? Feeds `until_dry`
termination"* — returned True only when the output was `None` or `len(output) == 0`. It never
looked at `progress_field`. Two shipped templates declare one, both `mode: until_dry, streak: 2`:

| Template | field | body shape | cap |
|---|---|---|---|
| `goal-pursuit-open-ended` | `new_findings_count` (integer) | `sequence[cycle, judge]` | 12 |
| `general-project` | `meaningful_progress` (boolean) | `sequence[work, judge]` | 6 |

Two independent reasons the exit could never fire for them:

1. **The field was never read.** A structured cycle returning `{"new_findings_count": 0, "notes":
   "nothing new"}` is a non-empty dict, so it counted as progress.
2. **The output measured was the wrong node's.** `_advance_loop` reads
   `self._outputs[item.node.id]` for the leaf that COMPLETED the iteration — in both templates the
   `judge` stage, whose schema (`reasoning`, `verdict`, `scores`, …) has no progress key and which
   returns a populated object every time. Even under the whole-output rule that output can never be
   empty, so `tick.loop_should_continue`'s `dry_streak < need` never stopped being true and the run
   burned every iteration up to its cap, paying for a model call each time to learn nothing.

The third symptom was a doc claim: `template_lint.py`'s TANGLED-loop comment asserted
*"`progress_field` merely names which field it reads"* — describing behavior that did not exist.

**The dryness rule for a declared field**, one sentence: *a declared progress field is dry when its
value is that field's own expression of "nothing" — null, false, zero, blank, or empty.* Per type,
exhaustively, in `_progress_reading`:

| value | reading | why |
|---|---|---|
| `None` | dry | the body answered "nothing" |
| `False` / `True` | dry / progress | a boolean field IS the answer (checked before `int`: `bool` is an `int`) |
| `0` / `0.0` | dry | the shipped `new_findings_count: 0` case |
| non-zero number | progress | including a NEGATIVE: a nonsense count is not evidence that nothing happened |
| `""` / whitespace | dry | a blank statement of what is new says nothing is new |
| non-blank `str` | progress | |
| empty / non-empty `bytes` | dry / progress | |
| empty / non-empty `list`,`tuple`,`set`,`frozenset`,`dict` | dry / progress | nothing collected |
| any other type | **unreadable** | no rule exists, so it refuses to call it dry — not swallowed by a default branch |

**Absence and unreadability fall back to the whole-output rule, deliberately.** Treating a missing
key as dryness would end a user's run after `streak` iterations because the body forgot to emit a
field — silently truncating real work. One extra iteration is the cheaper and more visible mistake.
The same direction covers an oversize output whose inline preview is a `result_omitted` stub.

**Blast radius: exactly the two templates above.** Both bodies genuinely emit their declared field
(schema + an explicit prompt instruction, verified), so neither template was wrong at its own end
and no template needed fixing. `goal-pursuit-open-ended`: two cycles reporting
`new_findings_count: 0` now end the loop instead of running to 12. `general-project`: two cycles
reporting `meaningful_progress: false` end it instead of running to 6. The other two `until_dry`
loops (`audit-sweep`, `deep-research`) declare no field and are byte-for-byte unchanged, as is
every other loop mode. `_is_dry` has exactly ONE caller, so no non-loop behavior could change.

**Implementation plan**

1. Census `_is_dry`'s callers (one: `_advance_loop`) and every `progress_field` declaration in
   `src/` (two bundled templates, one lint comment) before touching the signature.
2. Keep `_is_dry` as the whole-output rule, unchanged, for loops that declare no field.
3. Add `_progress_reading` (the exhaustive per-type table above, with `unreadable` as a real third
   answer) and `_progress_value` (`(found?, value)`, so a present `None` stays distinguishable from
   absence).
4. Scan the loop BODY for the field, restricted to nodes whose instance for THIS iteration
   succeeded — `self._outputs` is keyed by node id and holds the previous iteration's value for a
   node that did not run, and reading that would report last iteration's progress as this one's.
   Last match in document order wins.
5. Drive it through a real controller on a body shaped like the shipped templates (field emitted by
   the first stage, iteration ended by a judge stage): ends at 2 iterations on zeros; runs to the
   cap while progress is reported; a productive cycle resets the streak; an absent field runs to
   the cap. The judge's fake output carries a counter so `check_breaker`'s `identical_output` rule
   cannot end the loop first and make the test prove nothing.
6. Rail the template end over EVERY bundled template (not a hand-maintained list): a declared
   `progress_field` must appear in some body node's `schema` AND be named in that node's prompt —
   a key the prompt never asks for comes back invented or missing.
7. Prove both halves can fail: revert the call site to `_is_dry` (the two ending tests go red);
   rename one template's declared field (the rail goes red). Revert each with a targeted edit.
8. Correct `template_lint.py`'s comment so it describes the behavior that now exists.

### `WF2LOO-15` — judge_actors: separate the enforced invariant from the authored one

**Status:** done

**Design.** `judge_actors` opened with *"Two invariants the engine ENFORCES rather than suggests"*, and only one of
them ran. Measured live references outside the module: `plan_judge_session` 3, `validate_judge_model` 4 — the judge
**isolation** rule is genuinely enforced from `engine.dispatch_gate`, and `engine.py:1296`'s own comment records that
this seam was itself dead until S146 wired it. Against that: `check_transition` 0, `resolve_transition` 0,
`blind_provenance` 0, `assemble_judge_evidence` 0.

The reason the transition rule was never wired is structural, not an oversight: **the state machine has no actor at a
node transition.** `controller.py`'s `actor` parameter (line 1210) belongs to the *mutation* queue, so there is
nothing to pass to `check_transition` at the point a node's state changes. Introducing one is real work, and it is
`WF2LOO-13`'s — this atom refuses to fake it, corrects the claim, and consolidates ownership so the unwired judge
surface has a single owner instead of three half-findings across two modules.

Same for the blinding pair: the live judge gate sends a one-word prompt and parses the word, so there is no
message-list evidence for `assemble_judge_evidence` to blind. Authored for the richer judge path, not for this one.

**Implementation plan.**
1. Rewrite the module docstring into three bullets — ENFORCED (with the file:line seam), AUTHORED-NOT-ENFORCED (with
   the reason and the owning atom), and the blinding pair — and say the sections below specify a rule rather than
   describe what runs.
2. `tests/test_workflows_judge_actors_claims.py`: a vacuity floor (every symbol still defined), the enforced half must
   keep a live caller, and the authored half must agree with the docstring **in both directions**.
3. Prove both failure directions with probes, reverted by targeted edits.
4. Extend `WF2LOO-13`'s scope to name all four functions.

### `WF2LOO-16` — Reconcile the THIRD verdict vocabulary (loop/judge.CycleVerdict) into the contract

**Status:** todo

`WF2LOO-13` reconciled TWO vocabularies (`verify.Verdict` deleted, `judge_contract.Verdict` survived with RETRY merged in). `WF2LOO-12`'s own design already named the third and left it: "loop/judge.py is a third, live, structured judge with its OWN vocabulary — CycleVerdict{done, marginal_value, quality_score, regressed} — and no proof field, so the contract is not expressible there either." That is a recorded gap with no owner. The two dialects are not merely different spellings: the loop side has `marginal_value` (0-5) and `regressed`, which are the product's ONLY diminishing-returns signals, and the contract side has the proof precondition, the rubric ratchet and the actor matrix, which the loop side lacks. Each is missing what the other has, so reconciling is additive in both directions rather than a rename.

**Done when:** `judge_contract.Verdict` absorbs what only `CycleVerdict` carried — `marginal_value` and `regressed` become contract fields with the same 0-5 clamp and the same asymmetric adjudication rule (`loop/judge.adjudicate`: a `done` survives only if the skeptic also says done; a `regressed` survives if EITHER judge flags it) — and `CycleVerdict` is DELETED, not bridged. The loop judge's ground-truth observation (`_observe_ground_truth`: runs the verify command, reads the named deliverable across workspace plus fallback dirs, injects it labelled authoritative) survives unchanged as the loop-side evidence source feeding the contract's `evidence_refs`. Population measured before enforcement, per this program's rule: how many real loop verdicts would satisfy `validate_verdict` is counted BEFORE the proof precondition applies to a loop cycle. Blast radius named: every loop kind that writes a verdict, plus `watchdog._publish_cycle_verdict` and the cockpit's ROI rail / verdict panel which read `marginal_value`/`quality_score`/`regressed` off the persisted shape.

### `WF2LOO-17` — Give the loop judge a model binding independent of the worker it grades

**Status:** done

`loop/judge.assess_cycle`'s docstring states an independence property the code does not implement: "in production it resolves the 'reasoning' use-case (a stronger third-party check than the worker's model)". The code returns `resolve_provider_for_use_case("loops")` (`judge.py:218`), and `providers/provider_bridge.py:343` documents `"loops"` as the LOOP WORKER's use case. Same for the skeptic (`judge.py:286`) and for `gates.judge_verdict` (`gates.py:134`). `LoopsConfig` has no judge model field, so there is nothing to bind even if a caller wanted independence. The judge is therefore independent in session and prompt but runs on the same model binding as the work it grades — the "correlated reviewer mistakes" failure mode named in the review-loop literature, and the opposite of the vendor guidance to reserve the strongest model for judgment.

**Done when:** A `loops.judge_use_case` config field is wired through all four config points (dataclass + `_meta`, `load()`, `to_dict()`, the `_EDITABLE_CONFIG` PATCH allowlist) defaulting to `reasoning`, and `assess_cycle` / `assess_cycle_skeptic` / `gates.judge_verdict` resolve it instead of `"loops"`. The docstring's claim becomes true rather than being softened. A test asserts the judge's resolved binding differs from the worker's whenever the two use cases resolve to different entries, and a second asserts the degraded path is unchanged (a judge whose provider cannot start still returns None — defer, never a false complete — and still logs WARNING so the degradation stays diagnosable). Cheap by construction: no new mechanism, one field and three call sites.

**As landed.** The docstring's claim was made TRUE, not softened. `loops.judge_use_case`
(default `reasoning`) is wired through all four config points, and all three judge call
sites — `assess_cycle`, `assess_cycle_skeptic`, `gates.judge_verdict` — resolve it via one
`loop/judge.judge_use_case()` helper instead of the worker's `"loops"`. The normalizer is
fail-SAFE rather than fail-open: an unknown axis (or an unreadable config) collapses to
`reasoning`, never to `loops`, because the failure being closed is precisely "the judge
silently grades on the worker's binding". Two stale claims elsewhere were corrected in the
same change: `gates.judge_verdict`'s docstring ("loop judgments ride the loops chain") and
`providers/use_cases.py`'s vocabulary comment (`loops — loop worker sessions + loop
gates/judges`), which had documented the defect as the design.

`tests/test_use_case_chains.py::test_loop_judges_resolve_loops_axis` was a source-text
ratchet PINNING the defect — it asserted the judges resolved `"loops"` and that
`"reasoning"` was absent. It is inverted, not deleted, under the same class name. No
frontend control: no `LoopsConfig` field has one (there is no Loops settings panel and no
schema-driven config UI), so a lone control for the fourth field would be a new surface,
not this atom's "cheap by construction" scope; the PATCH allowlist entry is the write path
and `docs/reference/CONFIG-REFERENCE.md`'s `loops.*` table carries the field, which is
exactly where that file says a knob with no dashboard control belongs.

### `WF2LOO-18` — Give the loops engine a worker-independent progress signal

**Status:** done

`loop/watchdog.check_stagnation` is the loops engine's only progress detector and it reads a field the WORKER writes: `all(int(f.get("new_findings_count", 1) or 0) == 0 for f in recent)`. `agents/defaults.py:89` instructs the agent to emit `new_findings_count`, and `loop/kinds/{goal,research}.py` prompt for it. The default when the key is absent is `1` — "progressing". So a worker reporting any nonzero count is immune to stagnation detection forever, and a worker that omits the field is immune by default. The workflows side has a worker-INDEPENDENT backstop (`resilience.check_breaker`'s byte-identical output hashing, plus `loop_middleware`'s call and fix fingerprints); loops has only `_MAX_CONSECUTIVE_ERRORS = 2` on turn FAILURES and a wall-clock unresponsive deadline, so a confidently-looping worker producing fresh junk trips nothing. `_STAGNATION_WINDOW = 5` is also a module constant while the middleware's equivalent window is configurable.

**Done when:** The loops watchdog gains a progress signal the worker cannot author: byte-identical-output detection over the cycle's finding content (the same rule `resilience.check_breaker` already applies, reused rather than reimplemented) and repeated-call fingerprinting where a cycle records tool calls. The self-reported `new_findings_count` is KEPT as a cheap first signal — it is genuinely informative when honest — but it can no longer be the only one, and its absence stops reading as progress. `_STAGNATION_WINDOW` becomes a `LoopsConfig` field wired through all four config points. Verified by a driven loop whose worker reports a nonzero count every cycle while emitting identical content: it now stalls, and the test is proven able to fail by reverting the detector.

**As landed.** `check_stagnation` now returns a REASON string (empty while progressing) and
evaluates three signals over the last `loops.stagnation_window` findings, only the last of
which the worker authors: (1) byte-identical cycle content, with `cycle`/`new_findings_count`
and the timestamp keys excluded from the hash so a worker cannot buy immunity by
incrementing a counter beside unchanged output; (2) an identical set of recorded
calls/targets (`tool_calls`/`calls`/`commands`/`sources_checked`/`files_touched`), which
catches the worker that re-words its report every cycle while re-reading the same pages —
the case content hashing misses; (3) the self-reported count, KEPT because it is the
cheapest signal and honest workers do report zero, but consulted LAST and no longer able to
veto (1) or (2). Its absence no longer reads as progress: the old `f.get(...,  1)` default
made silence mean "progressing", so a window whose speaking cycles all reported zero now
stalls even if the rest said nothing.

**Reuse, not re-derivation (the atom's constraint).** Both observed signals delegate to the
workflow engine's existing rules — values are recorded into `resilience.BreakerState` and
the verdict comes from `resilience.check_breaker` (`identical_output`), and call
normalization uses `loop_middleware.call_fingerprint`. `check_breaker` reads its thresholds
off a node's `config`, so the loops caller hands it a config-carrying `Node`; a loops CYCLE
is exactly the iteration unit the breaker was written for. The imports are lazy because
`workflows` already reaches into `loop` (`controller` → `loop.gates`), so a module-level
import back would close the cycle at import time. `tests/test_loop_stagnation_signal.py::
TestReusesTheEngineRules` spies on both engine functions, so a later inline re-implementation
on the loops side goes red rather than silently drifting from the workflows rule.

**The vacuity trap that shaped signal (2).** `design`/`general`/`sdlc` findings record no
calls at all, so their fingerprint lists are empty — and an all-empty window is trivially
"identical". Firing on that would have stalled every loop of those kinds at cycle N. The
signal is therefore silent unless EVERY cycle in the window recorded something, and a rail
drives a call-less kind past the window to prove it stays RUNNING. The same reasoning
anchors signal (3): only kinds whose prompts ask for `new_findings_count` (goal/research)
can produce an explicit zero, so absence alone can never establish a stall.

`_STAGNATION_WINDOW` is gone; `loops.stagnation_window` (default 5, clamped [2, 50], fail-safe
to 5) is wired through all four config points and read per poll, so a change applies without a
restart. The floor of 2 is structural — the two observed signals compare findings BETWEEN
cycles, so a window of 1 could only compare a cycle with itself. No frontend control, for the
reason `WF2LOO-17` records: no `LoopsConfig` field has one, and the `loops.*` table in
`docs/reference/CONFIG-REFERENCE.md` is where a knob without a dashboard control belongs.
