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
| `WF2LEA-6` | ⬜ | Step 6: template refiner AGENT + version store + Versions/Ledger FE tabs + skill sidecar overlays | `WF2LEA-4`, `EXT:WORKFLOWS-V2:template-versioning`, `EXT:AUTOMATION-SUBSTRATE:run-workflow-trigger` | Refiner ships as a trigger-fired run-workflow template with a propose_*-only tool set (consuming S73/S79 decision functions + fenced_evidence); monotonic template version store with re-pin/rollback; runs pin executed version; Versions (typed-op diff) + Run Ledger FE tabs + maturity badge + Refine-now button; accepted skill proposals apply as sidecar overlays (revert = delete one file, install_guarded locks intact) |
| `WF2LEA-7` | ✅ (#1080) | Step 7: ad-hoc→template detector call sites + tier-migration detector + template_save_from_session | `WF2LEA-1`, `EXT:WORKFLOWS-V2:run-intents` | detectors.py wired at its call sites: fifth run_skill_ladder_review branch, per-spec embedding + registry-miss logging, intent inversion, positive-path trace mining, grill SaveFn; every negative decision writes a skipped(reason) ledger event; R17 tier_migration proposals from ledger statistics; template_save_from_session tool files a draft proposal (module no longer has zero importers) |
| `WF2LEA-8` | ⬜ | Step 8: self-model observer call site + user.selfmodel.* store + ambient slot producer | `WF2LEA-2` | An observer records (route, tools, outcome, reaction) into the staging log after significant turns; live user.selfmodel.* entries read/written via MemoryService within the caps; reinforced habits file lesson_batch proposals (never self-installed); the compact snapshot is produced into the §2.4 allocator's self_model slot |
| `WF2LEA-9` | ⬜ | Step 9: polish tier — multi-gate heat promotion, memory-heat kernel migration, observability panel completion, per-tool approval identity, intent-adaptive/ablation sweeps | `WF2LEA-2`, `WF2LEA-5` | heat-earned promotion uses the multi-gate (usage+recency+diversity); memory_record.heat migrated onto the one kernel; observability panel shows health composite (0-100), judge-calibration MAE buckets (R10d), attribution verdict history (R16), per-op LLM cost aggregates (R19e); per-tool approval identity added and fed to procedural priors (or dropped with DEVIATION); intent-adaptive weight profiles + ablation-delta sweep |
| `WF2LEA-10` | ⬜ | Amendment E1.1 skill resource tier + E1.2 SKILL.md conformance check | `WF2LEA-1`, `EXT:OWNER-RULING:skill-md-conformance` | Optional resources: frontmatter; skill_invoke returns body + an L0 resource catalog (never contents); new skill_resource(skill, path) resolves ONLY declared paths, rejects traversal, size-capped truncate-notice, usage-recorded, reads-never-executes; a standard-conformant third-party SKILL.md imports unmodified through the existing scanner rail (DANGEROUS floor still non-overridable, tested); §2.4 tiering NOT reimplemented in skills/ |
| `WF2LEA-11` | ✅ | Amendment E1.3: retroactive completed-run/conversation → skill proposal (verify-then-build, no second queue) | `WF2LEA-7` | After auditing skill_remember + proposals.py + §3.2 coverage, ONLY the missing retroactive path is added: a successful run/conversation can be promoted to a skill PROPOSAL feeding §3.2's existing queue; agent may propose unprompted but never writes; a rejected promotion is remembered in decision memory and does not re-surface |
| `WF2LEA-12` | ⬜ | Amendment E1.4: project_context_review → typed proposals into the §2.2 queue | `WF2LEA-1` | project_context_review reads a conversation/run and emits project_instruction/project_file/project_skill proposals with per-item rationale into the §2.2 queue; nothing written until accepted; accepting applies exactly the accepted items; the decision is recorded so a second review does not re-propose a rejected item; prompt-triggered only |

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

**Status:** todo

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

