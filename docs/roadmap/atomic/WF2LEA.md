# WORKFLOWS-V2-LEARNING-FLYWHEEL — atomic plans

**Source plan:** [`WORKFLOWS-V2-LEARNING-FLYWHEEL`](../plans/WORKFLOWS-V2-LEARNING-FLYWHEEL.md)  
**Code:** `WF2LEA`  
**Source status:** in_progress

Mostly-shipped 11-session flywheel program: 2 done atoms catalogue the landed lifecycle machine + decision spokes (PRs #163-166, #227-236); 10 todo atoms cover inert-control wiring (criterion 9 accountability, cadence coverage), the two v2-coupled spokes (run-end capture, refiner agent), step 2 lesson reroute, self-model producer, polish tier, and amendment tasks E1.1-E1.4.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `WF2LEA-1` | ✅ (##163,#164,#165,#166) | Shared lifecycle machine: Capture (gate/hygiene/staging), Propose (queue+decision memory), Curate (usage/decay/curator), Inject (slot allocator) | — | One LearningGate computes eligibility once per event; capture_hygiene fences untrusted; append-only staging log with FLUSH_* outcomes; learning/proposals.py has six kinds + fingerprints + rejected-exemplar store + 4-verdict resolve cascade + change manifests + per-run quota + SEL-audited accept; usage store + one decay kernel (3 profiles) + hardened curator wired to history.py consolidation tick; ranked slot allocator with per-entity profiles, sacrificial-slot truncation, authority preamble — all green (shipped) |
| `WF2LEA-2` | ✅ (##227-#236,#239) | Measure floor + decision spokes + Proposal Inbox + observability panel + Learning HTTP surface (criteria 1/4/5) | `WF2LEA-1` | surfacing_events per-arm precision + Beta-Binomial trust reportable; self-model/refiner/detector/attribution DECISION functions pure+tested; Proposal Inbox view + human-only accept gate (require_human, no trust param); staging week() panel; five /api/learning routes live; adversarial refiner-path fencing test passes; one-budget ambient render through allocator — criteria 1,4,5 closed (shipped) |
| `WF2LEA-3` | ⬜ | Step 2: reroute /api/lessons consumers onto memory.db, then delete lessons.jsonl/LessonStore | `WF2LEA-1` | The three /api/lessons consumers (mcp_memory tools, dashboard backing in handlers/schedule.py, no-embedder write fallback in context.py) read/write memory.db lesson.* identically; residual JSONL imported; embedder-less writes verified; lessons.jsonl + LessonStore deleted from gateway.py/cli_server.py/cli_commands.py/context.py; criterion 6 regression test green |
| `WF2LEA-4` | ✅ | Step 5: run-end capture spoke + route SESSION_END/RUN_END cadences through the gate (criterion 10 remainder) | `WF2LEA-1`, `EXT:WORKFLOWS-V2:run-ledger-slices-0-3` | WorkflowRun terminal state routes through LearningGate; step_failed emits write_lesson(source=workflow_run) via proposal queue + record_procedural; R8 failure-mode keying + capsules; R18 pending_outcome journaled at decision-time and resolved on curator tick; plan_memory absorbed into Run Ledger and deleted (removed from portability export); assert_gate_covers_cadences() reports zero gaps; criterion 10 regression test green |
| `WF2LEA-5` | ✅ | Criterion 9: wire accountability.attribute on the curator tick — Run-Ledger deltas → verdicts → auto-filed HARMFUL reverts | `WF2LEA-1`, `WF2LEA-4` | A production caller on the curator tick computes fixed[]/regressed[] deltas from Run Ledger outcomes after N post-acceptance runs, invokes accountability.attribute(), records the EFFECTIVE..HARMFUL verdict on the accepted change, and a HARMFUL verdict has auto-filed a revert proposal through the queue; verified end-to-end (module no longer has zero importers) |
| `WF2LEA-6` | ✅ | Step 6: template refiner AGENT + version store + Versions/Ledger FE tabs + skill sidecar overlays | `WF2LEA-4`, `EXT:WORKFLOWS-V2:template-versioning`, `EXT:AUTOMATION-SUBSTRATE:run-workflow-trigger` | Refiner ships as a trigger-fired run-workflow template with a propose_*-only tool set (consuming S73/S79 decision functions + fenced_evidence); monotonic template version store with re-pin/rollback; runs pin executed version; Versions (typed-op diff) + Run Ledger FE tabs + maturity badge + Refine-now button; accepted skill proposals apply as sidecar overlays (revert = delete one file, install_guarded locks intact) |
| `WF2LEA-7` | ✅ (#1080) | Step 7: ad-hoc→template detector call sites + tier-migration detector + template_save_from_session | `WF2LEA-1`, `EXT:WORKFLOWS-V2:run-intents` | detectors.py wired at its call sites: fifth run_skill_ladder_review branch, per-spec embedding + registry-miss logging, intent inversion, positive-path trace mining, grill SaveFn; every negative decision writes a skipped(reason) ledger event; R17 tier_migration proposals from ledger statistics; template_save_from_session tool files a draft proposal (module no longer has zero importers) |
| `WF2LEA-8` | ⬜ | Step 8: self-model observer call site + user.selfmodel.* store + ambient slot producer | `WF2LEA-2` | An observer records (route, tools, outcome, reaction) into the staging log after significant turns; live user.selfmodel.* entries read/written via MemoryService within the caps; reinforced habits file lesson_batch proposals (never self-installed); the compact snapshot is produced into the §2.4 allocator's self_model slot |
| `WF2LEA-9` | ✅ | Step 9: polish tier — multi-gate heat promotion, memory-heat kernel migration, observability panel completion, per-tool approval identity, intent-adaptive/ablation sweeps | `WF2LEA-2`, `WF2LEA-5` | heat-earned promotion uses the multi-gate (usage+recency+diversity); memory_record.heat migrated onto the one kernel; observability panel shows health composite (0-100), judge-calibration MAE buckets (R10d), attribution verdict history (R16), per-op LLM cost aggregates (R19e); per-tool approval identity added and fed to procedural priors (or dropped with DEVIATION); intent-adaptive weight profiles + ablation-delta sweep |
| `WF2LEA-10` | ✅ | Amendment E1.1 skill resource tier + E1.2 SKILL.md conformance check | `WF2LEA-1`, `EXT:OWNER-RULING:skill-md-conformance` | Optional resources: frontmatter; skill_invoke returns body + an L0 resource catalog (never contents); new skill_resource(skill, path) resolves ONLY declared paths, rejects traversal, size-capped truncate-notice, usage-recorded, reads-never-executes; a standard-conformant third-party SKILL.md imports unmodified through the existing scanner rail (DANGEROUS floor still non-overridable, tested); §2.4 tiering NOT reimplemented in skills/ |
| `WF2LEA-11` | ✅ | Amendment E1.3: retroactive completed-run/conversation → skill proposal (verify-then-build, no second queue) | `WF2LEA-7` | After auditing skill_remember + proposals.py + §3.2 coverage, ONLY the missing retroactive path is added: a successful run/conversation can be promoted to a skill PROPOSAL feeding §3.2's existing queue; agent may propose unprompted but never writes; a rejected promotion is remembered in decision memory and does not re-surface |
| `WF2LEA-12` | ⬜ | Amendment E1.4: project_context_review → typed proposals into the §2.2 queue | `WF2LEA-1` | project_context_review reads a conversation/run and emits project_instruction/project_file/project_skill proposals with per-item rationale into the §2.2 queue; nothing written until accepted; accepting applies exactly the accepted items; the decision is recorded so a second review does not re-propose a rejected item; prompt-triggered only |
| `WF2LEA-13` | ✅ | Close the procedural-memory loop: a live reader for `procedural_priors` + the `denied`/`corrected` outcome contract | `WF2LEA-9` | procedural_priors() has a production reader — MemoryService.procedural_block() → context.build_session_context() → learning.ambient as a named block mapped onto the EXISTING `lesson` kind (no sixth kind, no sixth slot), competing inside the one `learning.context_budget_tokens` budget; a raw `→ failed`/`→ denied` row is NEVER surfaced (synthesis input only) so the block cannot become a tool-call log, and the environment-failure guardrail applies on the read side too; `denied` gains a live writer (the native runtime labels every classify_denial observation as denied, not failed) feeding synthesize_failures' pre-existing `→ denied` cluster read; `corrected` is REMOVED from the contract (no attributable seam) and PROCEDURAL_OUTCOMES is a closed set record_procedural enforces; heat may rank priors but the eviction verdict never does; the whole chain (capture → heat promotion → priors → rendered block in the real session context) is driven in one test, and a stored lesson is provably never crowded out by a prior |
| `WF2LEA-14` | ✅ | Carry lesson SCOPE through the /api/lessons write path and honor it on read (`MemoryScope.WORKSPACE` stops being inert) | `WF2LEA-3` | `POST /api/lessons {scope:'workspace', workspace:'<abs cwd>'}` persists `scope='workspace'` + `scope_ref=realpath(cwd)` on the `lesson.*` row, threaded handler → `memory_service.write_lesson` → `vector_memory.write_lesson`; the endpoint REFUSES rather than downgrades (missing workspace / non-absolute workspace / unknown-or-unwritable scope → 400, nothing written), and every `MemoryScope` member is mapped with no default branch; the read side splits into an INVENTORY read (`get_lessons`, unchanged — list/count/delete still see every scope) and a fail-closed VISIBILITY read (`lessons_visible_in` / `get_lessons_context`) that yields global-only unless the caller declares a working directory, wired at the ONE read path that has one (`context.build_session_context(cwd)`); dedup is bucket-scoped and a workspace key is `lesson.ws.<hash(ref+rule)>` so a narrower write can never supersede or re-scope a global lesson; existing (all-global) lessons render byte-identically; `enum:MemoryScope.WORKSPACE` leaves `inert-surface-baseline.json` (140 → 139) |

## Atom scopes

### `WF2LEA-1` — Shared lifecycle machine: Capture (gate/hygiene/staging), Propose (queue+decision memory), Curate (usage/decay/curator), Inject (slot allocator)

**Status:** done (PR ##163,#164,#165,#166)

Migration steps 1,3,4a,4b (§2.1 Capture, §2.2 Propose, §2.3 Curate, §2.4 Inject); learning/ package: gate.py, hygiene.py, staging.py, proposals.py, usage.py, decay.py, curator.py, surfacing.py

**Done when:** One LearningGate computes eligibility once per event; capture_hygiene fences untrusted; append-only staging log with FLUSH_* outcomes; learning/proposals.py has six kinds + fingerprints + rejected-exemplar store + 4-verdict resolve cascade + change manifests + per-run quota + SEL-audited accept; usage store + one decay kernel (3 profiles) + hardened curator wired to history.py consolidation tick; ranked slot allocator with per-entity profiles, sacrificial-slot truncation, authority preamble — all green (shipped)

### `WF2LEA-2` — Measure floor + decision spokes + Proposal Inbox + observability panel + Learning HTTP surface (criteria 1/4/5)

**Status:** done (PR ##227-#236,#239)

§2.5 Measure, §2.6 self-model decisions, §3.1 refiner acceptance discipline, §3.2/§3.3 detector+failure decisions, §3.1 attribution decisions, §5/§6 inbox+panel; sessions S71-S80 (measure.py, self_model.py, refiner.py, detectors.py, accountability.py decisions, inbox.py, /api/learning routes, ambient.py)

**Done when:** surfacing_events per-arm precision + Beta-Binomial trust reportable; self-model/refiner/detector/attribution DECISION functions pure+tested; Proposal Inbox view + human-only accept gate (require_human, no trust param); staging week() panel; five /api/learning routes live; adversarial refiner-path fencing test passes; one-budget ambient render through allocator — criteria 1,4,5 closed (shipped)

### `WF2LEA-3` — Step 2: reroute /api/lessons consumers onto memory.db, then delete lessons.jsonl/LessonStore

**Status:** todo

§7 step 2, §4 Disposition (lessons.jsonl LessonStore = REROUTE CONSUMERS), success criterion 6

**Done when:** The three /api/lessons consumers (mcp_memory tools, dashboard backing in handlers/schedule.py, no-embedder write fallback in context.py) read/write memory.db lesson.* identically; residual JSONL imported; embedder-less writes verified; lessons.jsonl + LessonStore deleted from gateway.py/cli_server.py/cli_commands.py/context.py; criterion 6 regression test green

### `WF2LEA-4` — Step 5: run-end capture spoke + route SESSION_END/RUN_END cadences through the gate (criterion 10 remainder)

**Status:** done (PR #938)

§2.1 run-end cadence, §3.3 typed/checkable/outcome-grounded lessons (R8 capsules, R18 pending-outcome resolver), §7 step 5, success criterion 10; plan-memory absorption + deletion (+ portability list)

**Done when:** WorkflowRun terminal state routes through LearningGate; step_failed emits write_lesson(source=workflow_run) via proposal queue + record_procedural; R8 failure-mode keying + capsules; R18 pending_outcome journaled at decision-time and resolved on curator tick; plan_memory absorbed into Run Ledger and deleted (removed from portability export); assert_gate_covers_cadences() reports zero gaps; criterion 10 regression test green

### `WF2LEA-5` — Criterion 9: wire accountability.attribute on the curator tick — Run-Ledger deltas → verdicts → auto-filed HARMFUL reverts

**Status:** done

§3.1 change-manifest attribution (predict-then-verify, 5-way verdicts, HARMFUL auto-revert), Status line 'accountability.py has ZERO production importers', success criterion 9

**Done when:** A production caller on the curator tick computes fixed[]/regressed[] deltas from Run Ledger outcomes after N post-acceptance runs, invokes accountability.attribute(), records the EFFECTIVE..HARMFUL verdict on the accepted change, and a HARMFUL verdict has auto-filed a revert proposal through the queue; verified end-to-end (module no longer has zero importers)

### `WF2LEA-6` — Step 6: template refiner AGENT + version store + Versions/Ledger FE tabs + skill sidecar overlays

**Status:** done

§3.1 refiner substrate/trust/tiers/shape (R3), §3.1 accept→new version, §6 template-detail Versions/Ledger tabs + maturity badge, §7 step 6

**Done when:** Refiner ships as a trigger-fired run-workflow template with a propose_*-only tool set (consuming S73/S79 decision functions + fenced_evidence); monotonic template version store with re-pin/rollback; runs pin executed version; Versions (typed-op diff) + Run Ledger FE tabs + maturity badge + Refine-now button; accepted skill proposals apply as sidecar overlays (revert = delete one file, install_guarded locks intact)

### `WF2LEA-7` — Step 7: ad-hoc→template detector call sites + tier-migration detector + template_save_from_session

**Status:** done (PR #1080) — all 7 clauses closed; R17 tier_migration BLOCKED on absent inputs (see the plan Execution log)

§3.2 gate chain signal sources (R13), §3.5 tier-migration (R17), §5 template_save_from_session; Status line 'detectors.py has ZERO production importers'

**Done when:** detectors.py wired at its call sites: fifth run_skill_ladder_review branch, per-spec embedding + registry-miss logging, intent inversion, positive-path trace mining, grill SaveFn; every negative decision writes a skipped(reason) ledger event; R17 tier_migration proposals from ledger statistics; template_save_from_session tool files a draft proposal (module no longer has zero importers)

### `WF2LEA-8` — Step 8: self-model observer call site + user.selfmodel.* store + ambient slot producer

**Status:** todo

§2.6 self-model (R21), §7 step 8; S72/S80 'NOT DONE': observer call site + memory read/write of live entries + ambient self_model producer

**Done when:** An observer records (route, tools, outcome, reaction) into the staging log after significant turns; live user.selfmodel.* entries read/written via MemoryService within the caps; reinforced habits file lesson_batch proposals (never self-installed); the compact snapshot is produced into the §2.4 allocator's self_model slot

### `WF2LEA-9` — Step 9: polish tier — multi-gate heat promotion, memory-heat kernel migration, observability panel completion, per-tool approval identity, intent-adaptive/ablation sweeps

**Status:** todo

§7 step 9, §2.3 heat-earned promotion + decay kernel (R6f), §6 flywheel observability panel completion (R14b), §4 Disposition 'stats approve/deny counters → procedural priors'

**Done when:** heat-earned promotion uses the multi-gate (usage+recency+diversity); memory_record.heat migrated onto the one kernel; observability panel shows health composite (0-100), judge-calibration MAE buckets (R10d), attribution verdict history (R16), per-op LLM cost aggregates (R19e); per-tool approval identity added and fed to procedural priors (or dropped with DEVIATION); intent-adaptive weight profiles + ablation-delta sweep

### `WF2LEA-10` — Amendment E1.1 skill resource tier + E1.2 SKILL.md conformance check

**Status:** todo

Amendment (a) skill resource tier + (Owner task 1) format conformance; task rows E1.1, E1.2, VE

**Done when:** Optional resources: frontmatter; skill_invoke returns body + an L0 resource catalog (never contents); new skill_resource(skill, path) resolves ONLY declared paths, rejects traversal, size-capped truncate-notice, usage-recorded, reads-never-executes; a standard-conformant third-party SKILL.md imports unmodified through the existing scanner rail (DANGEROUS floor still non-overridable, tested); §2.4 tiering NOT reimplemented in skills/

### `WF2LEA-11` — Amendment E1.3: retroactive completed-run/conversation → skill proposal (verify-then-build, no second queue)

**Status:** todo

Amendment (b) agent-authored + retroactively-promoted skills; task row E1.3

**Done when:** After auditing skill_remember + proposals.py + §3.2 coverage, ONLY the missing retroactive path is added: a successful run/conversation can be promoted to a skill PROPOSAL feeding §3.2's existing queue; agent may propose unprompted but never writes; a rejected promotion is remembered in decision memory and does not re-surface

### `WF2LEA-12` — Amendment E1.4: project_context_review → typed proposals into the §2.2 queue

**Status:** todo

Amendment (c) self-updating project context with an approval gate; task row E1.4

**Done when:** project_context_review reads a conversation/run and emits project_instruction/project_file/project_skill proposals with per-item rationale into the §2.2 queue; nothing written until accepted; accepting applies exactly the accepted items; the decision is recorded so a second review does not re-propose a rejected item; prompt-triggered only


### `WF2LEA-13` — Close the procedural-memory loop: a live reader for `procedural_priors` + the `denied`/`corrected` outcome contract

**Status:** ✅ done (#PENDING)

Picks up exactly what `WF2LEA-9`'s Part 4 left on the table and named as its own atom
("`procedural_priors()` is an inert reader, and `record_procedural`'s declared `denied` outcome is
an enum member no writer produces — closing both together is a coherent piece of work; half-wiring
either here was the alternative and is worse"). Created 2026-08-11.

**Done when:** `procedural_priors()` has a production reader — `MemoryService.procedural_block()`,
produced in `context.build_session_context` and rendered by `learning.ambient` as a named block
mapped onto the **existing** `lesson` kind, so it competes inside the one
`learning.context_budget_tokens` budget rather than becoming a sixth independent block; a raw
`→ failed` / `→ denied` row is NEVER surfaced (it is `synthesize_failures` INPUT, and the collapsed
prior is the durable form), so the block cannot degrade into a tool-call log; the
environment-failure guardrail applies on the READ side as well as the write side; `denied` gains a
live writer — the native runtime labels a refused call from `security.classify_denial`'s own
recogniser instead of calling it `failed` — feeding the `→ denied` cluster read
`synthesize_failures` already performed; `corrected` is REMOVED from the contract rather than given
a synthetic writer, and `PROCEDURAL_OUTCOMES` becomes a closed set `record_procedural` enforces;
heat may rank priors while the decay kernel's eviction verdict never does; and the WHOLE chain
(capture → heat promotion → priors → the block inside a real `build_session_context`) is driven in
one test, with a proof that a stored lesson is never crowded out by a prior.

#### Design

**The finding: two live writers, no reader, and two members nobody writes.**
`MemoryService.record_procedural` is called from `after_turn_review.record_procedural_outcomes`
(the dashboard turn path, via `chat_runner`) and from `learning/run_end.py` on a workflow's
terminal failures. `MemoryService.procedural_priors()` — whose own docstring says "for recall-gated
injection", naming a consumer that did not exist — had **zero** production callers. So the system
paid to capture how-to-work priors on every significant turn and used none of them. Its docstring
also declared `Outcome ∈ {success, denied, corrected, failed}` while only `success` and `failed`
were ever written.

**The reader joins the EXISTING allocator pool.** `ambient.SLOT_KINDS` maps each named block onto
the allocator's existing kind vocabulary, and its own note says why: "a sixth kind would need a
sixth slot, which is how 'one budget' becomes six again". A how-to-work prior IS a learned lesson —
the difference is only who taught it — so `procedural` maps onto `lesson`. Rejected: a sixth kind
(the warning above), and the `memory` family (it would have dodged the crowd-out question by
ranking priors below the skills index, which is not the same as answering it).

**Sharing the lesson kind has a cost, and it is paid explicitly.** `lesson` is the one kind exempt
from the diversification cap, and `lessons` is a non-sacrificial slot, so the block enters as ONE
all-or-nothing candidate at score 0.8 (a stored lesson is 1.0) rather than one candidate per prior.
Two rails follow from that: `render`'s "nothing may crowd out a lesson" retry now discriminates by
KEY (`lesson:*`) instead of by kind — measured, a check on kind alone read a surviving prior block
as "a lesson survived", so a 60-token budget that dropped the user's only correction printed
"[Learned corrections — ALWAYS follow these]" over machine-observed priors — and the block carries
an explicit `[End of how-to-work priors]` footer, because the allocator renders a whole slot as ONE
chunk and a prior list outranking the second lesson would otherwise leave that lesson sitting under
this block's header.

**What may be surfaced is a narrow question, and the answer is not "the records".** `success` priors
and `failure_synthesis` rows only. A raw `→ failed` or `→ denied` row is synthesis INPUT: below the
cluster threshold one failure is not evidence, and above it `synthesize_failures` publishes the one
prior that replaces the N scattered rows — printing both would defeat the anti-noise mechanism and
contradict it in the same breath. The outcome vocabulary is mapped exhaustively with no default
branch, and `is_environment_failure_claim` is applied to the prior text, because `record_procedural`
accepts a `detail` that lands in that text and a promoted world-condition would be durable guidance
telling the agent a working tool does not work.

**`denied`: WIRED — it had a live reader all along.** `synthesize_failures` clusters on
`"→ failed" in text or "→ denied" in text`. `denied` was therefore the worst inert shape available:
a live reader of a value no writer produced. The seam is the native runtime's outcome record, which
derived `failed` from `result_str.startswith("Error:")` — and every one of its five denial paths
(hard deny-list, task-mode gate, PreToolUse hook, the user's reject, the unattended auto-decline)
returns an observation authored by `security.classify_denial`. So that function gained the
recogniser (`is_denial_observation`, keyed on a table of its own four wording fragments, beside the
code that writes them) and the runtime asks it rather than re-authoring the strings. This is a
correctness fix, not bookkeeping: labelling the user's refusal `failed` is what let failure
synthesis publish "this tool is unreliable — prefer an alternative" about a tool that works fine
and is merely not allowed here. The breaker still sees `failed` — a denial IS a reason to stop
repeating the call.

**`corrected`: REMOVED — there is no attributable seam.** `after_turn_review` does detect
corrections, but its `correction` flag is this turn's user message read as a reaction to the
**previous** turn's work (`chat_runner` says so where it feeds the self-model observer), and nothing
carries the previous turn's tool set forward. A writer here would blame whichever tools happened to
run this turn, and nothing anywhere reads `→ corrected`. A wrong prior is worse than a missing one,
and the correction itself is already captured — as a lesson, in the block that ranks above this one.
So the member is deleted from the contract instead of documented and unwritten.

**Rank is heat, and the doctrine holds.** `learning/decay.py` bars *strength* (the bare recency
curve) from surfacing rank and permits heat, which weights its usage term above its recency term so
recency can break a tie but never create one. The eviction VERDICT is not consulted at all.

#### Implementation plan

1. **Confirm the premise before writing anything**: grep `procedural_priors` for production callers,
   grep the two writers, and check what actually promotes a memory record to GLOBAL. (It is
   `promote_by_heat` on the history consolidation tick, plus `synthesize_failures` writing global
   rows directly — the curator's `TIER_MIGRATION` proposal path promotes learned-library *entities*
   and the accept installer has no branch for that kind, so accepting one moves no record's scope.)
2. **Close the vocabulary**: `PROCEDURAL_OUTCOMES` in `memory_service`, enforced by
   `record_procedural` (raise, not silently store) and by `record_procedural_outcomes` at the drain
   (drop + log, since a turn must not fail over a label); delete `corrected` from the contract.
3. **Give `denied` its writer**: `security.is_denial_observation` beside `classify_denial`, and the
   runtime's accumulator becomes `(tool, outcome)` — updating both consumers of the drain
   (`record_procedural_outcomes`, and the self-model observer's `succeeded`, which keeps its exact
   previous verdict).
4. **Add the reader**: the surfaceability filter inside `procedural_priors` (one definition, so no
   second caller can bypass it) plus `procedural_block()` with its header/footer.
5. **Wire the block** through `context.build_session_context` → `_render_ambient` → `ambient.render`
   / `sources_for` (its own source key, so the ablation sweep and per-arm report attribute it
   correctly), and fix the two rails sharing the lesson kind breaks (`_kept_a_lesson`,
   `_is_lesson_block`).
6. **Drive the whole chain in one test**, capture through the live writer and promotion through the
   live promoter — plus the non-vacuity case that proves the key-based crowd-out rail matters, the
   exhaustive-vocabulary rail, and the runtime denial labelling driven through a real
   `NativeAgentRuntime`.
7. **Gate**: `make lint`; `-k "procedural or ambient or memory or learning or after_turn"`; the
   ratchets (`test_inert_surface_baseline`, `test_agent_reference`, `test_docs_lint_baseline`,
   `test_config_roundtrip`); the full suite once, with the real-home rail's verdict reported.

**Scope guard — what this atom is NOT.** It does not add a config knob (the block rides the budget
that already exists), does not touch the breaker's failure accounting, does not build the
`TIER_MIGRATION` installer branch (a separate, human-approval-shaped question), and does not widen
`task_shape` beyond the coarse tool identity M5d chose — a finer shape is a capture change with its
own noise budget, and this atom is about the read side.

### `WF2LEA-14` — Carry lesson SCOPE through the `/api/lessons` write path and honor it on read

**Status:** ✅ done (#PENDING)

Created 2026-08-12 from a traced defect, not from a task row. `WF2LEA-3` owns the three
`/api/lessons` consumers (the `mcp_memory` tools, the dashboard backing in
`handlers/schedule.py`, the no-embedder write path) and `WF2LEA-13` owns how a lesson competes
for the ambient budget on the way into a prompt — so the plan that owns the lesson write path
end to end owns closing it.

**Done when:** a `POST /api/lessons {scope:"workspace", workspace:"<absolute cwd>"}` persists
`scope='workspace'` + `scope_ref=realpath(cwd)` and reads back that way from a fresh store; the
lesson is invisible to another working directory and to a reader with no working directory —
including through the real `build_session_context` — while a global lesson still reaches
everyone; the endpoint refuses (400) rather than downgrading on a missing workspace, a
non-absolute workspace, or an unknown/unwritable scope; every `MemoryScope` member is mapped
with no default branch; and `enum:MemoryScope.WORKSPACE` leaves the inert-surface baseline as a
result of being consumed, not referenced.

#### Design

**The finding: an advertised, validated, enforced parameter that three layers then dropped.**
`mcp_memory.py:41-48` advertises `scope: "global" | "workspace"` with `workspace` described as
*"required when scope='workspace'"*, and it enforces exactly that — line 106-110 returns
`"Error: workspace name is required when scope='workspace'"` — before POSTing
`{"rule","category","scope"[,"workspace"]}` to `/api/lessons`. `api_lessons_create` then read
`rule`, `category` and `negative` and **never** `scope`/`workspace`, calling
`svc.write_lesson(rule, category, negative)`. Neither `memory_service.write_lesson` nor
`vector_memory.write_lesson` had a scope parameter at all, so the row landed at the
`MemoryRecord` default — global. The symptom was already catalogued:
`enum:MemoryScope.WORKSPACE` sat in `inert-surface-baseline.json`. A caller that carefully asked
for a workspace-scoped lesson silently got a global one and was told `{"ok": true}`.

**Refuse, never downgrade.** The endpoint does not trust the MCP tool's validation — the tool is
one client of an HTTP route anyone can call, and for a while it was the only thing enforcing a
contract the server ignored. `resolve_lesson_scope` maps a declared `(scope, workspace)` pair
onto stored axes and raises for anything it cannot honor exactly: a missing workspace, a
**non-absolute** workspace (a bare `alpha` would `realpath` against the *gateway's* cwd and land
under a ref nothing can ever match — silent invisibility is as dishonest as a silent downgrade),
an unknown string, and `session`/`agent`, which are real `MemoryScope` members with no lesson
write path. Absent or blank means the documented default; `""` and `" "` cannot mean different
things because no caller can see the difference. Each member is tested explicitly, so a fifth
member added later reds rather than quietly becoming global.

**The read side, measured before deciding.** There is no ambient "current workspace" in
`memory_service.py` or `context.py`. What exists is stronger and already load-bearing: memory is
partitioned by working directory (`config.loader.memory_dir_for_cwd`), and the
`workspace-identity` prompt block tells the agent in as many words *"You are operating in
workspace (working directory): …"*, then documents `scope=workspace` as *"only visible in this
working directory"* — a promise the code did not keep. So a workspace **is** a working directory,
the ref is its `realpath`, and option (a) applies at exactly one read path:
`context.build_session_context(cwd=…)` already has the cwd and is the path that assembles the
prompt. That partitioning is COARSE — the gateway registers its one store under both the no-cwd
`_default` key and the running-workspace key — so several working directories share one
memory.db in production, and the scope filter is what separates them.

**What a workspace lesson is visible to:** a session whose cwd `realpath`-matches its
`scope_ref` (its injected lessons block, via `build_session_context`); the lesson inventory —
`GET /api/lessons` with no filter, the MemoryPanel list, the count badge, the CLI listing, and
`delete_lesson` (a lesson you cannot see is a lesson you cannot delete); and `GET
/api/lessons?workspace=<abs path>` for its own workspace.
**What it is invisible to:** any session in a different working directory; any recall path with
no workspace identity — the dashboard grill-tree recall (`loop_routes`), the debug context
preview, and `build_session_context()` with no cwd; and `GET /api/lessons?workspace=` for
another directory. Matching is EXACT: no basename, prefix, or case-insensitive comparison, because
two unrelated checkouts are routinely both named `web` and a fuzzy match would leak one project's
private rule into another. A ref that matches nothing shows no lesson — the safe direction to
fail.

**Inventory and visibility are separate reads, deliberately.** `get_lessons()` keeps its exact
behavior (every scope) because the management surfaces and the delete path need it;
`lessons_visible_in(workspace)` is the new, fail-closed read that decides what may enter a
prompt, and `get_lessons_context` is built on it. Folding both into one defaulted parameter was
rejected: whichever default was chosen, one of the two callers would be silently wrong — a
workspace lesson that cannot be deleted, or one that leaks into every recall.

**A narrower write must never mutate a wider record.** Two mechanisms enforce it. The key: a
workspace lesson is `lesson.ws.<md5(ref + rule)>`, so writing text that already exists globally
is a new row rather than an UPSERT that re-scopes the global one (the same silent-rescope defect,
one layer down). The dedup pass: it iterates `_lessons_in_bucket(scope, scope_ref)` — that
workspace's own lessons for a workspace write, global-only for a global write — because the
substring, topic-overlap and cosine branches all *supersede the loser*, and an unscoped pass
would let a project-local rule soft-delete a lesson every other workspace still reads. A global
write therefore behaves byte-identically to before.

**Additive, no migration.** `scope` was added by migration v6 with `DEFAULT 'global'`, so every
existing lesson is already global and stays visible everywhere; every read `COALESCE`s a NULL to
`'global'` so a hand-restored row cannot vanish. The extra axis UPDATE only fires for a
non-global write, mirroring `_apply_axes`'s own "skip for plain global/durable" rule.

#### Implementation plan

1. `memory_service.py`: module-level `normalize_workspace_ref` (realpath, exact, "" for
   relative/empty) and `resolve_lesson_scope` (exhaustive over `MemoryScope`, raises with a
   caller-facing message, never widens).
2. `vector_memory.write_lesson`: `scope`/`scope_ref` keywords; workspace key derivation;
   bucket-scoped dedup; one axis UPDATE after `set_semantic` for non-global writes.
3. `vector_memory`: `_lesson_rows` (one SELECT), `get_lessons` (inventory, behavior unchanged),
   `lessons_visible_in` (fail-closed visibility), `_lessons_in_bucket` (dedup population),
   `get_lessons_context(workspace)`.
4. `memory_service`: thread the axes through `write_lesson` (normalizing by the same function the
   read side uses), add `lessons_visible_in`, make `lessons_context(workspace)` normalize and
   default closed.
5. `handlers/schedule.py`: resolve-or-400 on create; `?workspace=` filter on list; each listed row
   carries its own `scope`/`workspace`.
6. `context.py`: pass the session `cwd` into `lessons_context` — the one read path that has one.
7. `mcp_memory.py`: the advertised `workspace` field now says *absolute working-directory path,
   copied from the WORKSPACE IDENTITY block*, matching what the server enforces; `memory_list`
   labels a workspace lesson with its directory so the inventory cannot read as universal rules.
8. `tests/test_lesson_scope.py`: the round trip through the real handler + a fresh store, the
   two-cwd/one-store injection proof, the three refusals, the exhaustive-enum rail, and the
   no-collision / no-supersede rails.
9. Regenerate `inert-surface-baseline.json` (it legitimately shrinks).

**Scope guard — what this atom is NOT.** It does not add a scope CHOOSER to the dashboard
MemoryPanel: the Studio has no working-directory identity to offer, so a picker would have to
invent one, and manual dashboard entry stays global (correct — a global lesson is what the
surface can honestly promise). The list response now carries `scope`/`workspace` so a future FE
badge is a display change over data that already arrives. It does not give `session`/`agent`
scopes a lesson write path, does not touch `MemoryTier`, and does not add promotion (a workspace
lesson never widens to global on its own — that is the M5+ heat-gated promotion axis, and it
stays out of the write path this atom fixes).
