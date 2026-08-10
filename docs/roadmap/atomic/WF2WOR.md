# WORKFLOWS-V2-WORK-CONTAINERS — atomic plans

**Source plan:** [`WORKFLOWS-V2-WORK-CONTAINERS`](../plans/WORKFLOWS-V2-WORK-CONTAINERS.md)  
**Code:** `WF2WOR`  
**Source status:** in_progress

Project-as-sole-umbrella work-container hierarchy: all 9 decision layers shipped (S46-54, PRs #185-193) but their call sites, the subagent fan-out hardening (C-amendment), and the entire React FE remain unbuilt.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `WF2WOR-1` | 🟡 (##185) | Project umbrella + Work board: /work endpoint, lease files, hub FE | — | Success Criterion 1: GET /api/projects/{id}/work is registered (register_unified_loop_routes pattern) over containers.py's projection with per-section isolation; the hub Work tab shows runs+legacy loops+tasks in one state-grouped board (needs-input pinned, queued/suspended/claimed), truthful across a gateway kill; claim leases ride the concurrency.single_flight flock; overview auto-revises on run completion via the controller completion hook |
| `WF2WOR-2` | 🟡 (##190) | Cross-project needs-input inbox FE + reply-resume routing | `WF2WOR-1`, `EXT:WORKFLOWS-V2:workflow_resume answer grammar for modify-and-approve`, `EXT:SELF-VERIFICATION:replay harness gates needs-input/approval journal-event reification` | Success Criterion 4: the needs-input inbox surfaces a gated run, an attention-state loop, and a blocked task as decision-ready NeedsInputItem cards; a reply resumes the exact blocked node via resume_token; modify-and-approve (permission_suggestions + updated_input) works; count pills, digest-batching, staleness re-notify and the Open Decisions lane render |
| `WF2WOR-3` | 🟡 (##186) | Artifact publishing: media self-containment, file drop/outbox, cockpit diffs | — | Success Criterion 3: a publish:{artifact} output appears in the existing Artifacts UI versioned only on material change with a change_note and typed lineage deep links; local files copied into the version dir with content-hash names; per-run file drop (approval-gated multipart) + outbox listing route work; cockpit renders structured version diffs + multi-view tabs on the contentTypes.ts registry |
| `WF2WOR-4` | ✅ | Run workspace + worktree execution wiring + code-run cockpit | `WF2WOR-1` | Success Criterion 7: plan_provisioning/pending_setup/plan_teardown are wired into the controller run-start path and executed (subprocess setup/teardown); PID-liveness lock files + preserved_workspace_path land on the run record; workspace_default_mode/workspace_teardown_on_expiry config defaults wire through all four points; a code-kind run provisions a worktree with preserve_patterns + idempotent setup, survives resume, tears down before deletion, and the cockpit diff panel + Apply Locally/Checkout Branch verbs work end-to-end |
| `WF2WOR-5` | 🟡 (##187) | Batch subagent_run compile-cutover + tool-handler posture seam + agent roster | `WF2WOR-4`, `WF2WOR-8`, `WF2WOR-9` | Success Criterion 2: mcp_subagents.subagent_run(tasks=[...]) routes through compile_batch (N>=2) producing a live widget that survives a gateway restart with individually-retryable branches; the __wf_depth tool-handler seam enforces capability classes, orchestration-tool denial and the secret-filtered leaf env; each branch runs in an isolated workspace, returns schema-validated typed output, and holds a lease (no double-execution); agent-roster slug-keyed catalog projection over config agents + drift check ship |
| `WF2WOR-6` | ✅ (##947) | Session-ownership run-start wiring + incognito enforcement at run start | `WF2WOR-1` | Success Criterion 5: at run start the controller performs the session_restrictions mark and the JSONL memory_mode write, and mirrors a completion summary into the launching session; a blocking run launched from an incognito session writes nothing to knowledge/learning stores, verified durable after a gateway restart |
| `WF2WOR-7` | 🟡 (##192) | Run cockpit + introspection/RunStats FE + live-adoption plumbing | `WF2WOR-1`, `WF2WOR-3` | Success Criteria 6 & 8: from the hub + cockpit alone an evaluator answers all nine introspection questions (node tree, journal timeline, attempt ledger, RunStats cost/latency strip, template p50/p95 cards, said-no fake-check badge, Proof section); the session-key equivalence helper adopts in-flight runs live; run-id-keyed streaming + useRunStream event-union additions land; live touched-items feed + PinnedArtifacts widget render |
| `WF2WOR-8` | ✅ | Fan-out subagent-path defect fixes (C1: injection wall, queue, agent-validate, control, budget) | `EXT:COST-AND-TOKEN-OBSERVABILITY:per-child cost ledger (T1.3)`, `EXT:AUTONOMY-GUARDRAILS:SpendMeter run scope` | 8 near-simultaneous sub-agent completions deliver without loss and without resetting the parent session; queued spawns carry the full parameter set and get real cancellable ids; _validate_agent returns a typed error (no silent downgrade); a run-scoped concurrency lane + one-click 'kill this fan-out' + record_failure breaker work; the run-scoped budget re-checks mid-flight and stops a fan-out that would exceed it |
| `WF2WOR-9` | ✅ (#961) | Fan-out leaf contract + capability enforcement + measurement harness (C2 + VC) | `WF2WOR-8` | C2.1: a leaf without an explicit objective/output-format/boundary fails compilation and off-format output is caught; C2.2: capability research\|mutating is enforced (two mutating leaves never run concurrently), leaves are homogeneous-by-default with optional per-leaf model pinning and no persona field; C2.3: the token-matched measurement harness reports a sub-5-point delta as inconclusive; VC: an 8-wide fan-out on real work passes and the verdict is logged |
| `WF2WOR-10` | 🟡 (##193) | Project export/import archive I/O + snapshot/portability registration + CLI/REST/FE | — | Success Criterion 9: archive I/O writes the manifest ZIP and extracts to a unique tmp with janitor cleanup and extraction-time path-safety; optional client-side AES-GCM encryption works; a 'projects' component is registered in snapshot.VALID_COMPONENTS and portability; exporting then importing on a clean home yields brief/overview/ledgers/templates/artifact-metadata/run-digests intact (sha256-verified) with zero secrets in the ZIP; CLI/REST surface + FE export button work |
| `WF2WOR-11` | ⬜ | Project-scoped memory locality + knowledge project tagging | `WF2WOR-6` | project-owned sessions run with cwd = project context_dir so their memory lands in the project partition; recall searches the partition first then global with cross-partition hits explicitly source-labeled and fenced (ordering-only, never admission); run-written knowledge items carry project_id + run_id metadata and a sharing_policy:private\|shared filter, and the project Artifacts/Knowledge views filter on it |
| `WF2WOR-12` | ⬜ | Container workspace mode + snapshot-anchored fork (deferred, opt-in) | `WF2WOR-4` | mode:container joins the workspace enum with a typed environment manifest (image XOR build, user, mounts, capabilities) on Docker/containerd or Apple Virtualization (no hard Docker dependency); container snapshots between stages anchor fork-from-checkpoint to workspace state; strictly opt-in with in_place/worktree staying default and no remote/cloud deploy modes |

## Atom scopes

### `WF2WOR-1` — Project umbrella + Work board: /work endpoint, lease files, hub FE

**Status:** in_progress (PR ##185)

§1 (Project extensions), §5.2 (truthful run lifecycle), §6.1 (Work tab + local-first rendering), Migration Order steps 1 & 4

**Done when:** Success Criterion 1: GET /api/projects/{id}/work is registered (register_unified_loop_routes pattern) over containers.py's projection with per-section isolation; the hub Work tab shows runs+legacy loops+tasks in one state-grouped board (needs-input pinned, queued/suspended/claimed), truthful across a gateway kill; claim leases ride the concurrency.single_flight flock; overview auto-revises on run completion via the controller completion hook

### `WF2WOR-2` — Cross-project needs-input inbox FE + reply-resume routing

**Status:** in_progress (PR ##190)

§6.1 (needs-input inbox / decision queue, R1), Migration Order step 4

**Done when:** Success Criterion 4: the needs-input inbox surfaces a gated run, an attention-state loop, and a blocked task as decision-ready NeedsInputItem cards; a reply resumes the exact blocked node via resume_token; modify-and-approve (permission_suggestions + updated_input) works; count pills, digest-batching, staleness re-notify and the Open Decisions lane render

### `WF2WOR-3` — Artifact publishing: media self-containment, file drop/outbox, cockpit diffs

**Status:** in_progress (PR ##186)

§2 (Artifacts — existing entity; R4/R5/R10/R17), Migration Order steps 3 & 8

**Done when:** Success Criterion 3: a publish:{artifact} output appears in the existing Artifacts UI versioned only on material change with a change_note and typed lineage deep links; local files copied into the version dir with content-hash names; per-run file drop (approval-gated multipart) + outbox listing route work; cockpit renders structured version diffs + multi-view tabs on the contentTypes.ts registry

### `WF2WOR-4` — Run workspace + worktree execution wiring + code-run cockpit

**Status:** done

§4.1 (workspace-provisioning block), §4.2 (folder contracts), Migration Order steps 2 & 5

`workflows/provisioning.py` (new) is the PERFORMER for S49's plan and S52's decisions, and
`controller._provision_workspace` (called from `_prepare`) is its caller — before it, a spec's
`workspace:` block was parsed nowhere in `src/`, so every run ran in place no matter what its
template declared. A FATAL declaration (unknown mode, greedy preserve pattern) now REFUSES the run
through `_finish`; provisioning runs preserve → setup in that order through the existing injected
`teardown_runner` seam; setup is marker-guarded and content-addressed so a resume skips a done step
and re-runs an EDITED one; a setup failure is recorded and never blocks the run. The PID-liveness
lock rides `concurrency.lock_path` OUTSIDE the workspace (fail-fast on a live holder it can NAME,
self-healing on a stale pid). `run.extra["worktree_path"]` is written for every isolated mode — it
had a live reader (`watchdog._substrate_for`) and zero writers — and `preserved_workspace_path`
lands from `WorktreeState`. `service.teardown_workspace` is the ONE performer both deletion paths
go through (`delete_run` and `watchdog.prune_runs`, both now async), gated by the new
`workflows.workspace_teardown_on_expiry`. `_substrate_for` now consults
`worktrees.substrate_for(inspect_worktree(...))`, which is what S52 built it for. The cockpit ships
`GET /api/workflows/runs/{id}/workspace` + `WorkspacePanel.tsx`: changed files (machinery excluded)
and both reintegration verbs as READABLE COMMANDS — offered, never performed, with conflicts named
on the offer via a non-mutating `merge-tree --write-tree` probe.

**DISCOVERY (measured, changed the design):** a plain `git add -A` in the worktree committed the
preserved `.env` AND `.pclaw-setup/` into the run branch, so the durable record of a run's work
would carry the user's local credentials into git history and both verbs would then offer to apply
them; the exclusion runs at the ADD via git pathspecs, because a review filter cannot un-commit a
secret. **DISCOVERY (pre-existing, fixed):** `worktrees.inspect_worktree("", ...)` reported ALIVE —
`Path("")` is `.` — so an unprovisioned run would have named the gateway's own cwd as its live
workspace and the boot sweep would suspend a run with nothing to resume into. **DEVIATION:** a
spec with no `workspace:` block provisions NOTHING (a workspace is a declaration, not a default);
defaulting every run into a scratch dir made every stale RUNNING run look isolated to the boot
sweep, which `test_an_adopted_run_resumes_without_re_running_finished_work` caught. **NOT DONE:**
container mode (§4.4) stays owner-deferred to `WF2WOR-12` and degrades to an isolated scratch dir
with the reason recorded; ff-only refresh for named workspaces is unbuilt (the lock and the name
keying that it needs are in place).

**Done when:** Success Criterion 7: plan_provisioning/pending_setup/plan_teardown are wired into the controller run-start path and executed (subprocess setup/teardown); PID-liveness lock files + preserved_workspace_path land on the run record; workspace_default_mode/workspace_teardown_on_expiry config defaults wire through all four points; a code-kind run provisions a worktree with preserve_patterns + idempotent setup, survives resume, tears down before deletion, and the cockpit diff panel + Apply Locally/Checkout Branch verbs work end-to-end

### `WF2WOR-5` — Batch subagent_run compile-cutover + tool-handler posture seam + agent roster

**Status:** in_progress (PR ##187)

§3 (Subagent tools vs stages; batch-compile hardening R2/R16), Migration Order step 6

**Done when:** Success Criterion 2: mcp_subagents.subagent_run(tasks=[...]) routes through compile_batch (N>=2) producing a live widget that survives a gateway restart with individually-retryable branches; the __wf_depth tool-handler seam enforces capability classes, orchestration-tool denial and the secret-filtered leaf env; each branch runs in an isolated workspace, returns schema-validated typed output, and holds a lease (no double-execution); agent-roster slug-keyed catalog projection over config agents + drift check ship

### `WF2WOR-6` — Session-ownership run-start wiring + incognito enforcement at run start

**Status:** done (PR ##947)

§5.1 (session ownership + incognito inheritance), Migration Order step 7

**Done when:** Success Criterion 5: at run start the controller performs the session_restrictions mark and the JSONL memory_mode write, and mirrors a completion summary into the launching session; a blocking run launched from an incognito session writes nothing to knowledge/learning stores, verified durable after a gateway restart

### `WF2WOR-7` — Run cockpit + introspection/RunStats FE + live-adoption plumbing

**Status:** in_progress (PR ##192)

§6.2 (run cockpit), §6.3 (live adoption + streaming honesty R7 FE), §6.4 (introspection checklist), §6.5 (compact affordances)

**Done when:** Success Criteria 6 & 8: from the hub + cockpit alone an evaluator answers all nine introspection questions (node tree, journal timeline, attempt ledger, RunStats cost/latency strip, template p50/p95 cards, said-no fake-check badge, Proof section); the session-key equivalence helper adopts in-flight runs live; run-id-keyed streaming + useRunStream event-union additions land; live touched-items feed + PinnedArtifacts widget render

### `WF2WOR-8` — Fan-out subagent-path defect fixes (C1: injection wall, queue, agent-validate, control, budget)

**Status:** done (merged)

Amendment §'Audit findings that must be fixed before any width increase' + Amendment task table C1.1-C1.5

**Done when:** 8 near-simultaneous sub-agent completions deliver without loss and without resetting the parent session; queued spawns carry the full parameter set and get real cancellable ids; _validate_agent returns a typed error (no silent downgrade); a run-scoped concurrency lane + one-click 'kill this fan-out' + record_failure breaker work; the run-scoped budget re-checks mid-flight and stops a fan-out that would exceed it

### `WF2WOR-9` — Fan-out leaf contract + capability enforcement + measurement harness (C2 + VC)

**Status:** done (PR #961)

Amendment §'Contract changes to §3' (a-e) + §'The synthesis this plan adopts' + Amendment task table C2.1-C2.3, VC

`batch_compile.LeafTask` carries three REQUIRED, default-less contract fields — `objective`,
`output_format`, `boundary` — and `contract_lint`/`boundary_lint` refuse an under-specified or
self-contradictory leaf at compile; all three ride into the leaf's own prompt beside the verbatim
`output_schema`, because the engine's existing `output_contract` rejects off-format output before any
binding resolves and a format the worker never saw would fail every attempt. `boundary` is the dual of
`writes` (negative declaration for the worker vs positive declaration for the compiler), and only their
contradiction — a write inside its own boundary — is an error, compared on path-shaped tokens so prose
boundaries cannot cry wolf. Amendment (c) is enforced by a `needs` chain over the `mutating` leaves,
measured against the real `tick.frontier`: research leaves still launch together, one mutator advances
per tick, and a FAILED mutator hands the lane on (so serialization never became a second way for one bad
leaf to sink a batch). Heterogeneity is by MODEL only — `model_ref`, named around the
`mutations._FIELD_ALIASES` `model`→`model_tier` collision — and `forbidden_declarations()` asserts no
persona-shaped field exists, making amendment (a) a standing check rather than a comment. C2.3 ships
`harness/fanout_measure.py` + `python -m harness fanout-measure` + the documented procedure, reporting
`inconclusive` for a sub-5-point delta (and for a delta under the arms' own within-arm spread),
`not_token_matched`, and `insufficient_trials` — exit 0 for every honest verdict. **DISCOVERY:**
`Capability.MUTATING` was a declared-but-inert enum member until this atom (nothing in `src/` branched on
it); the chain is its first reader, and `inert-surface-baseline.json` shrank 156→155 in the same commit.
**VC partially proved:** all 8 leaves terminal, no two mutators concurrent on any tick, one FAILED leaf
still derives DONE. Per-child cost + one-click kill (C1.4/C1.5), parent-context preservation (C1.1) and
the live end-to-end 8-wide `subagent_run` drive plus its real-work harness verdict are deferred to
**WF2WOR-5**, which owns the production call site and depends on this atom.

**Done when:** C2.1: a leaf without an explicit objective/output-format/boundary fails compilation and off-format output is caught; C2.2: capability research|mutating is enforced (two mutating leaves never run concurrently), leaves are homogeneous-by-default with optional per-leaf model pinning and no persona field; C2.3: the token-matched measurement harness reports a sub-5-point delta as inconclusive; VC: an 8-wide fan-out on real work passes and the verdict is logged

### `WF2WOR-10` — Project export/import archive I/O + snapshot/portability registration + CLI/REST/FE

**Status:** in_progress (PR ##193)

§1.7 (export/import contract R15), Migration Order step 8

**Done when:** Success Criterion 9: archive I/O writes the manifest ZIP and extracts to a unique tmp with janitor cleanup and extraction-time path-safety; optional client-side AES-GCM encryption works; a 'projects' component is registered in snapshot.VALID_COMPONENTS and portability; exporting then importing on a clean home yields brief/overview/ledgers/templates/artifact-metadata/run-digests intact (sha256-verified) with zero secrets in the ZIP; CLI/REST surface + FE export button work

### `WF2WOR-11` — Project-scoped memory locality + knowledge project tagging

**Status:** todo

§1.6 (memory locality + knowledge tagging R14, adapted to memory_dir_for_cwd + global knowledge seam), Migration Order step 7

**Done when:** project-owned sessions run with cwd = project context_dir so their memory lands in the project partition; recall searches the partition first then global with cross-partition hits explicitly source-labeled and fenced (ordering-only, never admission); run-written knowledge items carry project_id + run_id metadata and a sharing_policy:private|shared filter, and the project Artifacts/Knowledge views filter on it

### `WF2WOR-12` — Container workspace mode + snapshot-anchored fork (deferred, opt-in)

**Status:** todo

§4.4 (optional container mode R20), Migration Order step 9 (explicitly deferred to last)

**Done when:** mode:container joins the workspace enum with a typed environment manifest (image XOR build, user, mounts, capabilities) on Docker/containerd or Apple Virtualization (no hard Docker dependency); container snapshots between stages anchor fork-from-checkpoint to workspace state; strictly opt-in with in_place/worktree staying default and no remote/cloud deploy modes

