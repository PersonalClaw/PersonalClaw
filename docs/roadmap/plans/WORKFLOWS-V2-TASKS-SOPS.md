# WORKFLOWS-V2-TASKS-SOPS

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/WF2TAS.md`](../atomic/WF2TAS.md) as 11 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Tasks & SOPs as Workflow Primitives

**Status:** DONE — sessions 55-61k shipped (PRs #195-#215, on `main`): the task projection with its
engine call site (`controller.py` → `should_materialize` → `plan_materialization`), verified-done,
`ConfirmationRequest` + the `/confirm` verb, surfacing core + channels, the pool, the DagView
composition, and the config four points plus the fifth (`workflows/settings.py` resolvers, so the
knobs are not inert). **Every module has a live caller** — verified by AST import audit 2026-08-04,
the cleanest plan in Pillar A. One DEVIATION: `match_threshold` deliberately not re-added (it would
have been an inert control). Status corrected 2026-08-04. (rev 2 — research-integrated 2026-07-12)

---

## Research Integration (2026-07-12)

Approved recommendation IDs folded into this revision (mechanism-level, not appended):

- **TASK-R1** → §1 State Projection (typed block/status taxonomy + card previews)
- **TASK-R2** → §1 Verified Done (done_criterion, evidence, engine-owned transitions, diagnostics sweep, clean-exit template, granularity lint)
- **TASK-R3** → §2 Surfacing Discipline (opt-in enum, trigger phrases, negative triggers, trigger CI, reachability doctor, registry-first doctrine)
- **TASK-R4** → §2 Surfacing Metadata & Injection Contract (summary/when_to_use, preconditions, freedom_level, digest tier, one-source-two-wrappers, overlays, portable markdown + git-sync provider)
- **TASK-R5** → §1 Projection Enforcement (API-rejected writes, actor matrix, fingerprint dedup, fan-out caps, idempotent recompute)
- **TASK-R6** → §4 ConfirmationRequest (one durable typed record, atomic single-use resolution, require_hitl)
- **TASK-R7** → §2 Hand-Off Edges + §8 Seed Template Library
- **TASK-R8** → §2 Cadence Surfacing Channel (cadence_days, overdue escalation via create-task)
- **TASK-R9** → §4 Guardrails & Postconditions (Stop rules, non_negotiable_rules, posture, regression-appendix loop — propose-only)
- **TASK-R10** → §5 Frontier/Next Projections, Evented Unblock, TTL'd Leases
- **TASK-R11** → §2 Parameter Pre-Fill Contract + Requirements Preflight
- **TASK-R12** → §1 Task Body Contract + §6 Context Bundles
- **TASK-R13** → §4 Per-Stage Mute, Observed Tool Profiles, Step-Scoped Approval Memory
- **TASK-R14** → §2 Composition-Direction Lint (checklist ⊃ SOP, acyclic refs)
- **TASK-R15** → §7 Surfacing UX (composer chips, validated deep-links)
- **TASK-R16** → §2 Blueprint Sessions (third surfacing mode)
- **TASK-R17** → §1/§5 Cascade-Fail Propagation
- **TASK-R18** → §2 Layered Scope Resolution & Shadowing (per-stage overlays)
- **TASK-R19** → §2 Workspace-Fingerprint Surfacing Channel (template packs)

**Recon corrections applied in this revision** (verified against code 2026-07-12):
1. The old SOP surfacing threshold is **0.62** (`workflows/surfacing.py::DEFAULT_MATCH_THRESHOLD`, config `workflows.match_threshold`, `WorkflowsConfig` loader.py:1052) with a **0.7** keyword-overlap fallback gate — NOT 0.55 (0.55 is the *skills* surfacing threshold in `skills/surfacing.py`). All surfacing numbers below use the real workflow values.
2. `create-task` is NOT a new action provider — it is one of the **8 existing core-native ActionProviders** (`action_providers/registry.py::_ensure_default_providers_registered`) and is already in `ALLOWED_HOOK_PROVIDERS` (`validation.py:555`). §6 reuses it.
3. Tasks persist as **per-entity JSON files** (`~/.personalclaw/tasks/t-<8hex>.json`, `NativeTaskProvider`, atomic_write) — there is no SQLite and no cross-file transaction. Materialization idempotency below is designed for rename-atomic per-file semantics, not transactions.
4. `WorkflowScope` already has FOUR tiers (`GLOBAL | WORKSPACE | AGENT | SESSION`, workflows/models.py) with an up-only promotion ladder (`workflows/registry.py::promote_workflow`). The plan's earlier three-value `DefScope` is corrected to preserve all four (session-scoped defs get end-of-run cleanup like `delete_session_workflows` today).
5. The FE DagView node-level **Approve/Deny (`onApprove`/`onDeny` + `awaiting` state) is a declared, UNWIRED extension point** (`web/src/pages/tasks/DagView.tsx`) — §4's ConfirmationRequest is the missing backend seam and §7 wires it.
6. The Task model **already has** `exit_criteria` with `can_mark_complete` requiring all criteria complete (tasks/models.py:224) — `done_criterion` (R2) extends this existing seam rather than inventing a parallel one.
7. Old-surfacing is called per turn from sync `build_message` via thread-pool bridges (context.py:1233-1252) with a swallow-all → None contract; new surfacing must preserve never-break-a-turn. `force_workflow_ids` (goal-loop confirmed SOPs) must keep injecting each cycle during coexistence.

---

## Overview

**Tasks** become the human-facing persistence view of workflow execution — leaf nodes auto-materialize as Task entities whose lifecycle is engine-driven, engine-verified, and evidence-bearing. **SOPs** become single-sequence workflow templates with embedding-based auto-surfacing preserved — but surfacing gains trigger discipline, two additional non-semantic channels (cadence, workspace fingerprint), and a visible, toggleable UX. Checklists are workflow defs with approval-gated stages backed by ONE durable ConfirmationRequest record. The existing Task UI (board, DAG, list, cards) works unchanged; standalone manual tasks remain fully independent.

---

## 1. Tasks and Workflow Nodes: The Persistence View Model

### Design Decision: Hybrid Materialization

Leaf executable nodes (`stage`, `action`, `gate`) in a running workflow **auto-materialize Task entities** whose status is a read projection of the node instance state. Container nodes (`sequence`, `parallel`, `foreach`, `loop`, `subworkflow`) and zero-token nodes (`transform`, `wait`) do NOT create tasks.

### State Projection (typed taxonomy — R1)

| Node Instance State | Task Status | `blocked_kind` | Policy |
|---|---|---|---|
| `pending` | `open` | — | — |
| `ready` | `open` | — | — |
| `running` | `in_progress` | — | — |
| `waiting` (gate / needs input) | `blocked` | `needs_input` | Routed to the needs-input inbox (§4 ConfirmationRequest); >24h lingering → re-notify via `state.notify()` and flip `blocked_kind` to `escalated` |
| `failed` (classified transient) | `blocked` | `transient` | Auto-retries per node retry policy; never lands in the needs-input inbox |
| `failed` (classified capability) | `blocked` | `capability` | Routes to a setup/requirements surface naming the missing binary/credential/provider (§2 requirements preflight supplies the finding); a stage whose provider became unavailable at dispatch time projects here too (R11 amendment) |
| runner lost (reaper-detected) | `blocked` | `disconnected` | Wired to the same detection class as `SubagentManager._reaper_loop` — a lost worker never silently stays `in_progress` |
| upstream node failed/cancelled | `blocked` | `upstream_failed` | Cascade, see below (R17) |
| `done` (criterion pending) | `in_progress` | — | "done (claimed)" — see Verified Done |
| `done` (criterion passed) | `done` | — | Engine-flipped, irreversible |
| `skipped` (incl. muted stages) | `skipped` | — | NEW TaskStatus value — deliberate skip is NOT `cancelled`; activity feeds must not lie about aborts |
| `cancelled` | `cancelled` | — | Sticky |

`blocked_kind` is a field on the Task (`blocked_kind: str = ""`), not a status explosion — `TaskStatus` (tasks/models.py:19) gains exactly one new member, `SKIPPED`. FE `taskMeta` STATUSES (board columns) adds the mapping; per-surface **label mapping is configuration** — FE board / Slack / future trackers map display labels, never fork the canonical state set (R12).

Each managed task carries a secret-free `preview` string (≤80 chars from the node's last checkpoint/tool label, passed through `security.redact()`) and a cheap progress scalar, rendered on board cards (R1).

**State flow is one-way (engine → task).** The workflow engine owns the node lifecycle; the Task entity reflects it. Manual task status edits from the UI trigger the corresponding workflow mutation (`workflow_skip`, `workflow_rewind`) through a thin adapter — the Task is never the source of truth for an engine-driven task.

### Verified Done (R2)

The board stops trusting worker self-report — the roadmap's judge-ground-truth-independence principle (`loop/judge.py::_observe_ground_truth` precedent) applied at task grain:

- **`done_criterion`**: when a leaf node materializes a Task, its verify clause (`verify_command`, `schema`, or `expression`) is copied into the Task. This extends the existing `exit_criteria` seam (`Task.can_mark_complete` already refuses completion with open criteria — tasks/models.py:224); a `done_criterion` is an exit criterion whose checker is the ENGINE, not the user. Minimal acceptance-schema format (metaharness shape): two check types — `file_phrase {path, required_phrases, weight}` and `command {command, expect_exit_code, weight}` — with a scored objective (hit_weight/total_weight) and per-check results, directly serializable into the task JSON. Cheaper than authoring test suites per task.
- **Pass-state gating**: a stage completing without its criterion passing projects as `blocked`, not `done`. The ENGINE (never the worker) executes verification (via the same `loop/gates.py::run_verify_command` tristate machinery — 180s timeout, `audit_bash_command` screen, exit-127→None) and flips the state, **irreversibly**.
- **Completion record**: managed tasks record `attempts` (node retries) and `evidence` (artifact id / command-output ref) at completion; the default completion record projected into the Task body is the 5-part report `{files changed, behavior, tests, commands+results, risks/follow-ups}`.
- **Stuck-work diagnostics sweep**: a periodic pass flags `in_progress` without node heartbeat >20min, `open`-and-ready unclaimed >1h, and done-without-evidence — surfaced as a Tasks-board strip and as Run Ledger findings (WORKFLOWS-V2 §5). The sweep also auto-releases expired leases (§5).
- **Clean-exit checklist** (build passes / tests pass / progress recorded / no stale artifacts / startup works) ships as a bundled checklist template (§8 item 8).
- **Granularity lint** on the SOP migration utility (§2): warn when a converted step isn't one-session-completable or lacks a verification.

### Projection Enforcement (R5)

The one-way invariant becomes enforceable, not aspirational:

- **API rejection**: the Task write façade (`tasks/registry.py` write path + the dashboard task handlers) rejects direct status writes on `workflow_binding.managed=True` tasks; the thin adapter (status edit → `workflow_skip`/`workflow_rewind`) is the only mutation path. Read-only task providers already set `readonly=True` and are skipped by write façades — this adds the managed-binding guard on the same chokepoint.
- **Three-actor transition matrix** (CORE precedent, R5 batch-5): allowed transitions are per-ACTOR (engine / user / agent), covering standalone tasks too. The agent (via `task_update` tool) may only move tasks to propose states (`blocked(needs_input)`, review); claim states (`in_progress` by engine dispatch, `done`) stay engine- or user-owned. This closes the hole where an agent tool call self-marks its own task done — the same worker-self-report hole R2 closes for nodes.
- **Unmapped manual statuses**: manual statuses with NO workflow-mutation mapping (blocked-for-external-reason, review columns) **pause engine projection** on that task until the user returns it to an automated state — enumerated explicitly, not undefined.
- **Idempotent timing**: `started_at` uses COALESCE(started_at, now) so node retries never rewrite start timing; `cancelled` is sticky.
- **Content-fingerprint dedup** (critical given per-file JSON storage with no transactions): auto-materialized tasks carry `sha1(source_ref or title+body)[:16]` + source kind; rewind/fork/retrigger re-materializations **merge/refresh** the existing task (journaled to the Run Ledger as `intake_refresh`) instead of duplicating. Projection is a pure idempotent recompute from run state — full-rebuild is the normal path, making drift impossible by construction.
- **Fan-out caps**: a `foreach` body materializes at most ~20 child tasks before collapsing to one parent task with a progress counter; auto-generated task trees respect a depth cap (default 3, max 5). Configurable via `workflows.max_materialized_per_foreach` (§9 config wiring).
- **Concurrent body edits**: task bodies use structured sections `{plan, outcome, log}` with per-section merge semantics so concurrent agent/user edits don't clobber each other (CORE precedent).

### Cascade-Fail Propagation (R17)

When a managed task's upstream node fails or is cancelled, the engine cascades `blocked(kind=upstream_failed, reason="Node X failed: {cause}")` to ALL dependent materialized Tasks in the same run whose frontier is now unreachable — using the binding-dependency graph (not just tree children). Dependents stay blocked until the upstream is retried/rewound/skipped, then return to `open`. Rapid cascade events (parallel fan-in failure) are debounced into ONE notification through `state.notify()` (dashboard/state.py:1023, gated by `notification_allowed`) rather than N alerts. Without this, dependent tasks sit misleadingly `open` after their prerequisite died — the board lies about what is workable.

### Task Body Contract (R12)

Materialization is not title-only mapping:

- Each materialized leaf task is a **vertical, independently-verifiable slice sized to ONE fresh context window** — matching the engine's fresh-session-retry behavior (a fresh subagent session must be able to execute it).
- Body format: behavior-first "what to build" + acceptance-criteria checkboxes (the `done_criterion` checks render here) + blocked-by. File paths / code snippets are prohibited in bodies (they go stale) **except** decision-rich artifacts: schemas, state machines, type shapes.
- The granularity lint (R2) enforces sizing at SOP-migration time.

### Standalone vs Managed Tasks

- **Managed tasks** (`workflow_binding.managed = True`): status driven by the engine. Created automatically when a leaf node goes ready.
- **Standalone tasks** (no binding or `managed = False`): fully user-driven. Created via `task_create` tool or UI, not tied to any workflow.
- **Produced tasks** (`managed = False`, has binding for provenance): created by `action{provider: create-task}` nodes as workflow OUTPUT. The workflow creates them but does not track their completion.

### New Field: `WorkflowTaskBinding`

```python
@dataclass
class WorkflowTaskBinding:
    run_id: str           # the WorkflowRun that owns this task
    node_id: str          # the specific node instance
    node_path: str        # dot-path in the spec tree
    managed: bool = True  # True = engine-driven; False = standalone production
    fingerprint: str = "" # sha1(source_ref or title+body)[:16] — dedup key (R5)
```

Added to the Task model as `workflow_binding: WorkflowTaskBinding | None`, alongside the new projection fields (`blocked_kind`, `preview`, `progress`, `attempts`, `evidence`, `done_criterion`). All land in tasks/models.py with the same list-coercion discipline `Task.__post_init__` already applies.

### Materialization Flow

When the engine transitions a leaf node from `pending` → `ready` (or directly to `running`):

1. Check if a Task already exists for this `(run_id, node_id)` pair OR matching `fingerprint` (idempotency on resume/rewind — per-file JSON storage means dedup-by-lookup, not transactions).
2. If not: `registry.create_task(title=node.label, task_list_id=run_task_list_id, workflow_binding=binding, done_criterion=<copied verify clause>, ...)` through the tasks provider façade — so non-native task providers keep working.
3. The task inherits `project_id` from the WorkflowRun's context.

When a node claims completion: engine runs the criterion → on pass, `registry.update_task(task_id, status="done", evidence=...)`; on fail, `blocked(kind=transient|capability)` per classification.

Every engine mutation of a materialized Task emits a state-change signal so the FE board updates without polling drift — **adapted to PClaw's real live-channel architecture** (recon: fe-surfaces): dashboard WS envelopes are refetch SIGNALS, not payloads, so the engine emits a `tasks`-kind refresh hint via `push_refresh()`/WS which `DashboardLive` debounce-refetches, plus `TaskCreated`/`TaskCompleted` lifecycle events on the hook event bus enabling trigger-based automation (R10 amendment, wired through the existing `hooks.HOOK_EVENTS` seam).

### TaskList per WorkflowRun

Each WorkflowRun auto-provisions one TaskList (named `"{workflow_name} run #{run_id[:6]}"`). All materialized tasks land in this list. The existing Tasks UI scoped by project shows them naturally. TaskLists support **shared handles**: workflow runs AND ad-hoc sessions can attach to the same list by ID (R10 amendment) — the same pattern `loop/tasks_link.py` already uses for loop-backed lists.

### Per-Node Opt-Out

Nodes can disable materialization: `materialize_task: false` on the node spec. Useful for internal/helper stages (transforms, intermediate judges) that would clutter the task board.

---

## 2. SOPs Evolve into Workflow Templates

### Migration Path

Old SOPs (`Workflow` entity with `WorkflowStep` list — workflows/models.py) become `WorkflowDef` instances with a flat `sequence` root whose children are `stage` nodes (one per former step).

```python
def migrate_sop_to_def(old: Workflow) -> WorkflowDef:
    stages = []
    for step in old.steps:
        if step.is_ref():
            stages.append(Node(kind="subworkflow", ref=step.ref, label=f"@{step.ref}"))
        else:
            stages.append(Node(
                kind="stage", id=step.id, label=step.title,
                prompt=step.instruction or step.title,
                tools_posture="minimal", max_turns=1,
            ))
    return WorkflowDef(
        name=old.name, description=old.description,
        source="migrated", tags=old.tags + ["sop", "migrated"],
        root=Node(kind="sequence", children=stages),
        match_text=old.match_text,
        match_embedding=old.match_embedding,
        surface_mode="passive",  # R3: migrated SOPs keep surfacing, passively
    )
```

The migration utility runs three lints (all warn-not-block):
- **Granularity lint** (R2): flag steps that aren't one-session-completable or lack any verification.
- **Description lint** (R4): reject step-summarizing `summary`/`when_to_use` text — descriptions answer WHEN, never WHAT/HOW (praxis body-skipping failure: if the surfaced card describes the steps, the agent acts from the card and never loads the SOP).
- **Composition-direction lint** (R14): see below.

### Surfacing: Three Channels, One Discipline

Surfacing is no longer a single boolean. Three channels feed one candidate set:

**Channel 1 — Semantic match (per-turn).** Same algorithm as the old `workflows/surfacing.py`: cosine vs cached `match_embedding` with the REAL operative thresholds — `DEFAULT_MATCH_THRESHOLD=0.62` (config `workflows.match_threshold`), keyword word-overlap fallback gate `0.7`, tie-epsilon 0.05 preferring narrower scope. Runs inside sync `build_message` via the existing thread-pool bridge with the never-break-a-turn contract (swallow-all → None). Quoted/pasted content is **fenced out of the embedding-match input** (R3) — the `fence_untrusted` markers (security.py:668) delimit what the matcher may see.

**Channel 2 — Cadence/recency (R8).** WorkflowDef gains optional `cadence_days` + a derived last-completed timestamp (from the def's most recent successful run — run history the old feature never had). Overdue defs surface with a freshness gradient and auto-sort to the top of the templates list. Per-def escalation mode: **Manual** (surface only) vs **Auto** — Auto materializes ONE standalone Task per day while overdue (throttled: once daily while the condition persists, never per evaluation tick), via the existing `create-task` action provider (§6). Materialized tasks carry an explicit bidirectional link block `{linked_def, run_id?, completed, completed_at}` so the def reflects downstream completion. In the semantic scorer, defs with an in-flight or recently-abandoned WorkflowRun get a boost so unfinished checklists resurface as resume nudges. This channel exists because per-turn semantic match structurally cannot express "it's been 40 days since the backup checklist ran."

**Channel 3 — Workspace fingerprint (R19).** Defs (or named groups — "packs") carry optional `fingerprint` predicates: weighted file-glob patterns (`pyproject.toml` + `tests/` → python-project pack; `.github/workflows` → ci pack). When a session's working directory or project changes, a cheap scan scores confidence per pack; above threshold, PClaw proposes enabling that pack's SOP set as ONE grouped, dismissible suggestion — **propose-don't-enable**, user confirms; dismissal remembered per project. Pure file-pattern matching, zero LLM cost, runs only on project/directory attach — never per turn. Packs also solve R7's cold-start: bundled seed SOPs arrive as fingerprint-gated packs instead of polluting every project's candidate set.

### Surfacing Discipline (R3)

- `auto_surface: bool` is replaced by `surface_mode: "passive" | "suggest" | "off"` — default `passive` for migrated SOPs, **`off` for new defs**. Explicit `/workflow <name>` invocation always works regardless. (OpenSquilla shipped auto-trigger-by-default and retreated to manual-first after pasted content kept firing workflows — we don't re-ship their mistake.)
- `match_text` must be **2-5 natural trigger phrases** (not prose), with a save-time collision check against existing defs.
- **Negative triggers**: the matcher never emits an execution suggestion for planning-only requests, when the user already named a specific workflow, or from pasted/quoted content. Implementation reuses the negative-trigger veto pattern skills surfacing already ships (`skills/surfacing.py`).
- **Registry-first doctrine** (R3 amendment): the agent prompt mandates querying for matching SOPs/templates BEFORE composing ad-hoc plans, with ambient surfacing as the fallback; a soft runtime nudge fires after N tool calls without referencing the pinned SOP (enforcement lighter than tool denial).
- **Trigger-accuracy CI**: each bundled template ships fixtures (positive / explicit-invocation / pasted-history-negative / neighbor-domain-negative prompts) run as template CI.
- **Reachability doctor**: a doctor-style maintenance check verifies every active def is reachable via `match_text` or explicit index (gbrain's audit found 63 silently unreachable skills on first run — the mirror failure of over-firing).

### Surfacing Metadata & Injection Contract (R4)

New fields on WorkflowDef, splitting matching from display:

```python
match_text: str = ""                 # 2-5 trigger phrases (R3)
match_embedding: list[float] = field(default_factory=list)
embedding_model: str = ""
summary: str = ""                    # ≤180 chars, answers WHEN (lint-enforced)
when_to_use: str = ""                # ≤400 chars, never summarizes steps
preconditions: list[dict] = ...      # declaratively checkable file/entity/config predicates gating suggest mode
freedom_level: str = "medium"        # high|medium|low — how literally stages are followed; feeds gate strictness
lifecycle: str = "one-shot"          # one-shot|session|until-deactivated — passive-guidance persistence
revisit_window_days: int = 0         # + last_reviewed — stale-SOP detection
scope: WorkflowScope = GLOBAL        # global | workspace | agent | session (ALL FOUR existing tiers preserved)
scope_ref: str = ""
surface_mode: str = "off"            # passive | suggest | off (R3)
cadence_days: int = 0                # R8
fingerprint: list[dict] = ...        # R19 pack predicates
agent_digest: str = ""               # compressed quick-reference tier (below)
hands_off_to: list[dict] = ...       # R7: [{target_def, condition, context_fields}]
requirements: list[str] = ...        # R11: binaries/credentials/providers, aggregated from action providers
```

Injection contract:
- **One source, two wrappers**: both passive and suggest modes render from the SAME WorkflowDef with an appended mode delta — never a forked copy. A drift check during the coexistence period asserts old-surfacing text and template-surfacing text can't diverge for a migrated def.
- **Digest tier**: `agent_digest` (quick reference + numbered do/don't rules) is what passive mode injects — **verbatim, between server-side BEGIN/END fence markers, never model-paraphrased**. Suggest/execution mode gets the full doc. This resolves the context-cost tension: full SOPs are too expensive for ambient injection.
- Injected output is explicitly labeled guidance (feedforward) vs execution suggestion (feedback).
- **Learned-content overlays** (R4 batch-5, OpenJarvis): flywheel-accepted optimizations for a def live in a separate fault-tolerant sidecar overlay file (`few-shot` exemplar pairs + description overrides) injected at prompt time, NEVER mutating the base def — revert = delete file; a corrupt overlay can't break def loading. This extends one-source-two-wrappers to learned content and keeps the propose-don't-write learning boundary.
- **Anti-hallucination execution steps** (OpenJarvis): checklist execution suggestions use strict-JSON action arrays with exact-ID rules — the agent must copy node/task IDs verbatim from presented lines, with seen-ID dedup, so a suggestion can never act on invented step IDs.
- **Portability**: defs export/import as standalone front-mattered markdown files — the natural evolution of the existing `~/.personalclaw/workflows/<name>/WORKFLOW.md` + embedding-sidecar layout. A **git-synced def library** ships as a read-only workflow provider app (LocalAGI pattern): `type: workflow` manifest, registers via the WorkflowTypeHandler into `workflows/registry.py` with `readonly=True`, syncing a skills/-style directory from a git repo, with list/read/search exposed as tools. Version control stays external to PClaw; the provider seam is the existing one.
- **Per-def graduation** (R4 amendment, OpenWork): passive→suggest promotion is per-def, not global — a def earns execution-suggestion mode individually, enabling incremental trust building.

### Parameter Pre-Fill + Requirements Preflight (R11)

The execution-suggestion injection (`workflow_start(name, inputs={...})`) adopts a schema-driven extraction contract: returns `{extracted, missing, follow_up, all_filled}`; **only user messages count as truth** (fenced content excluded); latest value wins; follow_up asks required-first and never re-asks a declined optional; the result is re-validated against the def's input schema rather than trusting the LLM's `all_filled`.

`requirements` preflight: a def whose requirements are unmet **fails at suggestion time naming the missing item** instead of surfacing a suggestion that dies mid-run. The preflight uses the real availability seam that already exists: provider bundles may export module-level `availability() -> (bool, reason)` (providers/loader.py), and the Leon-style three-state model applies — *installed* (registered) / *enabled* (not disabled by owner) / *available* (required settings configured) — with the failure message deep-linking the settings path to fix it. Availability is re-checked **per-STEP at dispatch time**, not only at suggestion time: a stage whose provider became unavailable mid-run projects as `blocked(kind=capability)` per §1's taxonomy with the missing-settings finding attached.

### Layered Scope Resolution & Shadowing (R18)

The scope field gets actual resolution semantics (today the old registry has a promotion ladder but no shadowing rules):

- **Resolution order**: session > agent > workspace/project > global > bundled templates, by name — narrower shadows wider (consistent with the existing tie-epsilon narrower-scope preference in surfacing).
- **Visible shadowing**: shadowed defs remain VISIBLE with an explicit `effective | shadowed | disabled` state in the templates list — never silently hidden — with an "adopt" affordance promoting a bundled/shadowed def into an editable scope.
- **Per-stage override overlays**: a narrower-scope def may declare `overrides: {base_def, stage_id: replacement-or-disable}` patches against a wider-scope def instead of forking the whole sequence — a project can swap one stage of the global deploy-procedure while inheriting upstream improvements to the rest. Overlay application is validated at save time (referenced stage ids must exist in the base) and renders as a diff in the def detail view. This is what keeps a personal SOP library DRY when migration produces dozens of near-duplicate defs.

### Composition-Direction Lint (R14)

Save-time lint on WorkflowDef refs enforcing tier direction: checklist-grade defs (multi-stage sequences with gates) may `subworkflow`-ref SOP-grade defs (flat sequences); SOP-grade defs may reference skills/memory in prompts but may NOT ref checklist-grade defs. All subworkflow ref chains are validated acyclic at save — extending the existing `workflows/composition.py::validate_refs` (dangling-ref + cycle DFS) machinery, complementing the engine's runtime `__wf_depth`. `grade: checklist | sop` is derived **structurally** (has gates + multi-stage vs flat sequence), so the lint needs no manual classification.

Two library-scaling amendments: **routing SOPs** — thin top-level defs that only classify/route to nested sub-SOPs, with nested entries EXCLUDED from the surfacing catalog (explicit-load-only), keeping prompt cost flat as the library grows; and a `requires` field force-loading referenced sub-procedures at digest tier when the parent surfaces, so composite procedures load as a unit.

### Blueprint Sessions (R16 — the third surfacing mode)

Between passive text injection and full `workflow_start` sits a zero-engine mode: an SOP/checklist can materialize a **pre-seeded template conversation** into chat (`{id, title, messages: [{role, text}], openOnFirstLoad}` + a hydration record `{templateId, sessionId, hydratedAt}`). The conversation carries checklist steps as numbered assistant messages with structured prompts — a guided interactive session with zero engine overhead, written as a normal `sessions/<safe_key>.jsonl` ConversationLog. Rehydration is replace-not-merge (defensive, idempotent). The same WorkflowDef serves all three modes (passive injection / blueprint session / full run), selected by a mode field or complexity heuristic. This is the cheapest possible "walk me through this" for guidance-grade SOPs that don't need gates or status projection.

### Hand-Off Edges (R7)

`hands_off_to: [{target_def, condition, context_fields}]` on WorkflowDef: a completing SOP run **suggests** the follow-on SOP with context carried over. Codified edges shipped with the seed library: incident→bugfix, bugfix→feature, review→fix (review→fix only on explicit user request). Template-to-template transitions become declared graph edges instead of improvisation.

### Dual Mode (Passive Guidance vs Execution Suggestion)

The structural heuristic stands, now interacting with `surface_mode`:

**Lightweight defs** (all stages `max_turns ≤ 1`, no `schema`):
→ Passive guidance: inject the `agent_digest` verbatim between fence markers (preserves old SOP behavior at digest cost).

**Substantial defs** (any stage `max_turns > 1` or `schema`):
→ Execution suggestion (only if `surface_mode="suggest"` AND preconditions pass AND requirements preflight passes): `[SUGGESTED WORKFLOW — call workflow_start(name="{name}", inputs={pre-filled per R11})]`. The agent decides whether to execute formally or proceed manually.

### The "Learning" Path (Agent Captures Procedures)

| Old Path | New Path |
|---|---|
| `workflow_create` (SOP with steps) | `workflow_author` with `save: true` (creates a WorkflowDef, `surface_mode="off"` until the user opts in) |
| Consolidation extracts `new_skill` proposal | Consolidation extracts `new_workflow_def` **proposal** (propose-don't-write: lands in a `.proposals/` queue mirroring `skills/proposals.py` — fenced source excerpts, user accepts/rejects) |
| `workflow_promote` (session → global) | Same up-only ladder on WorkflowDef `scope` (registry `promote_workflow` semantics preserved) |
| `workflow_run` (returns steps as text) | `workflow_start` (actually EXECUTES) |

### Coexistence Period

During transition, both the old surfacing (`workflows/surfacing.py`) and new template surfacing run in parallel for one release cycle:
- Old SOPs still surface via their existing mechanism (not immediately archived); `force_workflow_ids` (goal-loop confirmed SOPs) keeps injecting each cycle.
- New template surfacing runs alongside, preferring templates when both match; the one-source-two-wrappers drift check (R4) asserts migrated defs render identical step text through both paths.
- After one release: old SOPs archived to `_legacy_sops/`, migration offered.

---

## 3. Skills vs Workflow Templates (Boundary)

| | Skills | Workflow Templates |
|---|---|---|
| Purpose | Passive procedural knowledge (always-on context) | Executable procedures (engine runs them) |
| Format | Free-form markdown (SKILL.md) | Structured node graph (workflow.json / front-mattered markdown export) |
| Lifecycle | Ages, curated, decays if unused | Versioned, immutable per run; `revisit_window_days` staleness flag |
| Invocation | Injected into system prompt every turn | Explicitly started, auto-suggested (opt-in), cadence-due, or fingerprint-proposed |
| Learning | Consolidation creates skill proposals | Consolidation creates workflow-def proposals (propose-don't-write) |
| Execution | Agent reads and follows (guidance) | Engine schedules nodes (automation) |
| Surfacing threshold | 0.55 semantic (`skills/surfacing.py`) | 0.62 semantic + 0.7 keyword (`workflows.match_threshold`) — separate knobs, do not conflate |

**They coexist.** A skill might say "for deploys, use the deploy-procedure workflow template" — guidance that points to execution. Note this boundary is about *procedures*; neither side touches the knowledge store (knowledge.db = the user's documents/files/notes) — SOP/task learning artifacts belong to memory/skills subsystems and the LEARNING-FLYWHEEL plan.

---

## 4. Checklists as Workflow Defs

A recurring checklist is a `WorkflowDef` whose structure is `sequence[gate, stage, gate, stage, ...]` — approval gates for manual verification, stages for automated work.

### Example: Deploy Procedure

```yaml
name: deploy-procedure
description: "Production deployment checklist"
guardrails:                      # R9 — reserved section, injected verbatim, never summarized
  preconditions: ["staging environment reachable"]
  stop:                          # what the executor must NEVER do; routes to inbox instead
    - "Never deploy with failing integration tests — route to needs-input"
    - "Never modify production data outside the deploy script"
  non_negotiable_rules:          # standing pre-action gates, active every turn while in force
    - "Read the current prod version before advising on rollback"
  posture: "terse status updates; no speculative fixes mid-deploy"
inputs:
  service_name: {type: string, required: true}
  version: {type: string, required: true}
requirements: ["deploy", "health-check"]   # R11 preflight — suggestion fails naming the missing binary
root:
  kind: sequence
  children:
    - kind: gate
      id: pre-check
      label: "Verify staging is green"
      gate_kind: approval
      prompt: "Confirm staging for {{inputs.service_name}} v{{inputs.version}} passes health checks"

    - kind: stage
      id: run-tests
      label: "Run integration tests"
      prompt: "Execute integration tests for {{inputs.service_name}}"
      schema: {passed: boolean, failures: integer}
      on_error: pause_run

    - kind: gate
      id: approve-deploy
      label: "Get deployment approval"
      gate_kind: approval

    - kind: action
      id: deploy
      label: "Deploy to production"
      provider: bash
      config: {command: "deploy {{inputs.service_name}} {{inputs.version}}"}

    - kind: gate
      id: verify-health
      label: "Verify production health"
      gate_kind: verify_command
      config: {command: "health-check {{inputs.service_name}}", timeout_secs: 300}
postconditions:                  # R9 — end-state assertions checked by the ENGINE after the final stage
  - {kind: verify_command, command: "health-check {{inputs.service_name}}"}
```

### ConfirmationRequest: ONE Durable Typed Record (R6)

The plan's flagship gate use previously never said what an approval IS as data. Checklist sign-offs, workflow approval gates, needs-input questions, and destructive-action confirmations unify into one persisted entity:

```python
@dataclass
class ConfirmationRequest:
    id: str
    run_id: str
    gate_id: str
    type: str            # approval | needs_input | destructive_confirm
    risk_category: str
    title: str
    payload_preview: str # secret-free, redact()-passed
    requested_at: str
    ttl_seconds: int
    status: str          # pending | resolved | expired
    resolved_by: str
    resolution_note: str
```

- Resolve-by-id is async; the run pauses on the record and **auto-resumes on resolution without re-executing completed stages**; expired records follow a configured auto-reject-or-hold policy.
- **Atomic single-use resolution**: the answer/approval is consumed in the same operation that resumes the run (atomic_write of the record with a resolved-status compare-and-check under `single_flight` — concurrency.py:59 — since per-file JSON has no transactions), so double-clicking Resume can never replay a clarification into downstream steps.
- The needs-input queue supports approve/reject/**skip**/quit where skip leaves the item pending for the next pass; question payloads follow the needs-info template ("established so far" + specific actionable questions, never "more info please").
- Any `stage` node accepts `require_hitl: true` — approval as a property of the step — so authors gate a stage without structurally inserting a gate node.
- Every resolution is SEL-audited (`sel.log_tool_invocation`) like other security-relevant decisions.
- One entity means ONE inbox surface (dashboard ActionCenter / "Needs you") and ONE autonomy policy instead of three bespoke flows — and it is exactly the backend seam the FE DagView's unwired `onApprove`/`onDeny` extension point has been waiting for (§7).

### Guardrails & Postconditions (R9)

Reserved def sections, as in the example above:

- **`guardrails`** — preconditions, stop-and-ask conditions, a Stop section (never-do boundary + what routes to the inbox instead), `non_negotiable_rules` (standing pre-action gates active every turn while the SOP is in force), and `posture` (style constraints). Injected **verbatim** into every execution suggestion and each stage's worker context — never summarized. The Stop boundary and standing rules are the non-inferable parts of a procedure; the gate vocabulary has mid-run checks but had no never-do boundary.
- **`postconditions`** — the "MERGED, never CLOSED" shape: end-state assertions checked by the engine after the final stage, distinct from per-stage `verify_command` gates.
- **Regression-appendix loop** (propose-only, per the platform's learning tenet): when a run under an SOP fails and is later corrected, the flywheel **proposes** appending the corrective check to that SOP's postconditions — SOP as accumulating test plan; the user accepts via the proposals queue, never auto-written.
- **Gate pre-condition checklists** (amendment): approval gates may carry criterion/threshold/evidence tables the gate cannot open until each checkbox is verified with evidence; per-checklist-item **provenance blocks** (origin incident, date-added, source-to-verify-against) record WHY items exist — lessons materialized as executable checks that grow monotonically.

### Per-Stage Mute, Tool Profiles, Approval Memory (R13)

- Stages carry `enabled: bool` — muted = skipped-but-visible; projects as `skipped` per §1 (not removed), letting users trim a checklist instance (e.g., mute the staging gate for a hotfix) without structural edits.
- The engine accumulates `step_tool_usage` per `(def, node_id)` from real runs — which tools each stage actually used — surfaced in the def detail view and consulted by execution-suggestion mode.
- **Step-scoped approval memory**: remembered tool approvals resolve step-level first, then def-level; sensitive-pattern decisions (anything matching `security.py` deny patterns) are **never** remembered. The third run of deploy-procedure shouldn't re-prompt for the same test command.
- **Declared execution-kind labels** (amendment, Paperloom): steps may declare `execution_kind: deterministic-tool | llm | ask-user` + parallelism markers — telling dual-mode surfacing what's safe to auto-execute vs surface as guidance, and giving plan-review UX a badge per step kind.

### Repeatable Pattern

The old "reset task list" pattern maps to: **start a new WorkflowRun from the same WorkflowDef.** Each run is a fresh instance with its own history. Past completions are preserved as past runs (queryable, comparable — and the substrate for §2's cadence channel).

### Gate Modes (Flexibility Spectrum)

| Gate Kind | Behavior | Use Case |
|---|---|---|
| `approval` | Run pauses on a ConfirmationRequest; user resolves | Manual verification |
| `verify_command` | Engine runs command; exit 0 = pass (tristate — exit-127/can't-run → None, held not failed) | Automated checks |
| `expression` | Binding evaluates truthy | Data-dependent gates |
| `event` | External trigger (webhook — via the existing `apps/webhook-action` provider seam) | Cross-system coordination |

A purely manual checklist uses all `approval` gates. A fully automated pipeline uses `verify_command`. Most real procedures mix both — the mix the old SOP feature could NOT express.

---

## 5. Blocking Dependencies → Node DAG Structure

| Task Dependency Pattern | Workflow DAG Encoding |
|---|---|
| Linear chain (A → B → C) | `sequence[A, B, C]` |
| Fan-in (A,B both block C) | `parallel[A, B]` then C in parent sequence |
| Fan-out (A blocks B and C) | A in sequence, then `parallel[B, C]` |
| Complex DAG | `parallel` with intra-block `needs: [sibling_ids]` |
| Cross-workflow dependency | `gate{kind: event, filter: {run_id, node_id}}` |
| Manual block (external reason) | `gate{kind: approval, prompt: "Blocked: {reason}"}` — pauses projection per §1 |

For workflow-bound tasks, the engine's `frontier()` function IS the dependency resolution logic. The existing `DependencyAnalysis` (tasks/reconcile.py:216 — completion %, critical path, bottlenecks, cycle-tolerant DFS) maps to workflow metrics computed from the node DAG.

### Frontier/Next Projections, Evented Unblock, Leases (R10)

PClaw already runs real concurrent co-tenant sessions and batch `subagent_run` children sharing a task pool — the pool needs concurrency semantics:

- **Evented auto-unblock for standalone tasks**: `status=done` on a task emits an event that unblocks dependents with `requires`/BLOCKS edges (today only workflow-bound tasks get this via `frontier()`). Failure of a blocker cascades `blocked(kind=dependency_failed)` with the blocker's failure reason (R17), completion emits the unblock event.
- **Two cheap projections over ALL tasks**: `frontier` (all currently unblocked tasks ranked by priority+urgency) and `next` (single top task scored by deps+priority+recency), exposed to both UI (the existing `api.readyTasks()` dashboard slice generalizes into these) and agent tools — so "what should I work on" stops being reimplemented ad hoc per surface.
- **TTL'd lease claims**: an executing session/subagent takes an exclusive TTL'd lease (≤1h, renew/release) on a task before working it. Acquire is compare-and-swap — implemented as an fcntl-locked read-modify-write on the task's JSON file (the `single_flight`/flock pattern, since per-entity JSON files have no transactions). The board shows claims; expired leases auto-release via the R2 diagnostics sweep. Without CAS leases, engine-projected tasks WILL be double-executed by concurrent sessions.
- **Acyclicity at write time** for standalone `blocked_by` edges (amendment, AionUI's shipped A-blocks-B/B-blocks-A deadlock): the FE editor guard (`pages/tasks/dag.ts::wouldCycle`) already exists client-side; the server-side write path adds the authoritative check via the existing cycle-tolerant `analyze()`.
- **Task lifecycle events** (`TaskCreated`/`TaskCompleted`) on the hook event bus (`hooks.HOOK_EVENTS`) enable trigger-based automation; FE board updates ride the WS refetch-signal pattern (§1 Materialization Flow).

---

## 6. The `create-task` Action Node (reuses the EXISTING provider)

**Recon correction:** `create-task` already exists as a core-native `ActionProvider` (`action_providers/`, ABC at base.py:50, registered idempotently by `_ensure_default_providers_registered()`, already present in `ALLOWED_HOOK_PROVIDERS` — validation.py:555). The workflow engine's `action` node dispatches through the **same action-provider registry** the hooks/schedule/trigger dispatch sites use (hooks.py:494, gateway.py:701, event_triggers.py:214) — no new provider, no allowlist change. What this plan adds is the workflow-side config surface and the R12 content contract.

For workflows that PRODUCE tasks as output (audits filing findings, sprint planning creating stories):

```yaml
- kind: foreach
  items: "{{nodes.verify.output.confirmed_findings}}"
  body:
    kind: action
    id: file-task
    label: "Create task: {{item.summary}}"
    provider: create-task
    config:
      title: "Fix: {{item.summary}}"
      description: "{{item.evidence}}"
      priority: "{{item.severity}}"
      labels: ["audit", "auto-filed"]
```

These tasks have `workflow_binding.managed = False` — standalone entities the user works manually. The §1 fan-out cap applies (≤~20 per foreach before parent-with-counter collapse). The `ActionResult.outcome` vocabulary (`""|skip|done|launched`) is honored: `launched` stays honest started≠succeeded.

### Context Bundles for Externally-Sourced Tasks (R12)

When `create-task` materializes from an external source (inbox item, issue-like entity), it injects a structured user-editable context bundle `{source_id, title, url, description, status}` and reuses source-side naming conventions for the task/run name. Bundle text originating outside PClaw is fenced (`fence_untrusted`) before it reaches any prompt. The R12 body contract (§1) applies: behavior-first, acceptance checkboxes, no stale file paths.

---

## 7. FE Surfaces

### Tasks board/DAG (existing 4 view modes: list | cards | board | dag)

- `TaskStatus.SKIPPED` + `blocked_kind` badge + `preview`/progress render on board cards; column/label mapping extends `taskMeta` STATUSES (per-surface label mapping is configuration, never a state fork — R12).
- **DagView Approve/Deny gets wired** (R6): the declared-but-unwired `onApprove`/`onDeny` + `awaiting` node state in `web/src/pages/tasks/DagView.tsx` binds to ConfirmationRequest resolve endpoints — gate nodes render `awaiting` (already pulse-styled), approve/deny resolves the record and the run auto-resumes.
- **Stuck-work strip** (R2): the diagnostics sweep's findings render as a dismissible strip above the board.
- Board liveness: engine task mutations arrive as WS refetch signals into `DashboardLive`'s existing debounced refetch (never payload-carrying dashboard events — recon invariant).

### Surfacing UX (R15)

- Surfaced defs render as **count-badged composer chips**, not invisible prompt injection: passive mode = an "SOP: <name> — ON" toggle chip with hover preview of the pinned digest; suggest mode = the same chip plus a run affordance. The user can see and switch off what the matcher injected — the difference between surfacing that builds trust and surfacing that gets globally disabled after one bad match.
- Suggestion cards deep-link into the planner as `#/workflows/new?template=X&param=Y` (hash-router grammar), with params validated against per-template input-schema allowlists on mount, then state→URL sync (reject URL-injected garbage). Query-param conventions follow the shell rules: refinements `{replace:true}`, destinations push.
- Checklist editing: drag-reorder with checked-locks-drag and two-stage destructive reveal (matching the existing armed-delete pattern).
- Templates list: freshness gradient + overdue-first sort (R8), `effective | shadowed | disabled` scope states with "adopt" (R18), pack proposals as one grouped dismissible suggestion (R19).
- New run-lifecycle events consumed by cockpit/card surfaces MUST be added to the FE stream union (the `RUN_LIFECYCLE` gotcha — EventSource silently drops unregistered event types); the workflow-run stream registers its task/gate events (`task_materialized`, `confirmation_pending`, `confirmation_resolved`, `task_verified`, `cascade_blocked`) in the WORKFLOWS-V2 widget's equivalent union.

---

## 8. Changes to WORKFLOWS-V2.md

1. **New fields on WorkflowDef:** `match_text`, `match_embedding`, `embedding_model`, `summary`, `when_to_use`, `preconditions`, `freedom_level`, `lifecycle`, `revisit_window_days`/`last_reviewed`, `scope` (all FOUR tiers), `scope_ref`, `surface_mode`, `cadence_days`, `fingerprint`, `agent_digest`, `hands_off_to`, `requirements`, `guardrails`, `postconditions`, `overrides` (Section 1, Data Model).
2. **New fields on WorkflowRun:** `task_list_id`, `project_id` (Section 1, Data Model).
3. **New fields on leaf node specs:** `materialize_task: bool` (default true for stage/gate, false for action), `enabled: bool`, `require_hitl: bool`, `execution_kind` (Section 1).
4. **New entity:** `ConfirmationRequest` (Section 1 Data Model + Section 2 Engine pause/resume + Section 5 events).
5. **Action node dispatch** through the existing `action_providers` registry; `create-task` reused as-is (Section 2 Engine) — NOT a new provider.
6. **New chat tool:** `workflow_from_sop` — convert NL steps into a workflow def (proposal-queued, `surface_mode="off"`). Registered the way `mcp_workflows.py` tools are today: a tool module in `mcp_core._TOOL_MODULES` via `tool_providers/registry.py`.
7. **New Section 4.5:** Template surfacing — three channels (semantic 0.62/0.7, cadence, fingerprint), surface_mode discipline, digest injection contract, requirements preflight, reachability doctor.
8. **Add to Slice 6 templates:** `checklist`, `sop-guided`, `audit-and-file` (steipete per-commit QA triage table as its concrete flavor: non-skip rows materialize Tasks via create-task, skip rows write only a ledger line with rationale — R7), `clean-exit` (R2), plus 2-3 franklioxygen MIT-licensed seed SOPs (bug-fix + code-review at minimum, each opening with its triage-classification stage), shipped as fingerprint-gated packs (R19).
9. **Migration Phase 1.5:** Legacy SOP one-time conversion on first boot post-deletion, with the three migration lints.
10. **Run Ledger additions:** `intake_refresh` (dedup merges), stuck-work findings, confirmation resolutions, cascade events.
11. **Engine events:** `task_materialized`, `task_verified`, `confirmation_pending/resolved`, `cascade_blocked` added to the run event stream (and to the FE stream union).

---

## 9. Provider & Config Integration Map

Where each new piece plugs into the pluggable-provider architecture (nothing bypasses it):

| New piece | Plugs in via |
|---|---|
| WorkflowDef storage/CRUD | Existing `workflows/registry.py` provider registry + `WorkflowProvider` ABC; native provider keeps the markdown+sidecar layout. Apps contribute defs via manifest `provider: {type: "workflow"}` → WorkflowTypeHandler (providers/registry.py) |
| Git-synced def library (R4) | A first-party workflow provider app (`type: workflow`, `readonly=True`); registered by the app loader on enable, deregistered on disable |
| `create-task` action nodes | EXISTING core-native ActionProvider; already in `ALLOWED_HOOK_PROVIDERS` (validation.py:555). Any future NEW action provider a template needs must be added to that frozenset or hook/workflow validation rejects it |
| `event` gates (webhooks) | Existing `apps/webhook-action` action app seam |
| Task materialization writes | `tasks/registry.py` provider façade (`register_provider`; readonly providers skipped) — non-native task providers keep working |
| Chat tools (`workflow_start`, `workflow_author`, `workflow_from_sop`, frontier/next) | Tool-provider category: a tool module listed in `mcp_core._TOOL_MODULES` via `tool_providers/registry.py` (the `mcp_workflows.py` pattern, including name→id fallback; ids never shown to the LLM) |
| Def proposals (learning path) | Proposals-queue pattern mirroring `skills/proposals.py` (fenced excerpts, pending cap, accept/reject) — propose-don't-write |
| Notifications (escalation, cascade, overdue) | `state.notify()` (dashboard/state.py:1023) gated by `notification_allowed()` — never a parallel channel |
| Requirements preflight | providers/loader.py `availability()` hook + provider registries' enabled state |
| Config fields | `WorkflowsConfig` (loader.py:1052) gains `surface_mode_default`, `max_materialized_per_foreach`, `confirmation_ttl_secs`, `lease_ttl_secs` — each wired through the FOUR points: (a) dataclass field with `_meta(label, help)`, (b) `AppConfig.load()` field-by-field mapping, (c) `to_dict()`, (d) PATCH `_EDITABLE_CONFIG` + FE if runtime-editable. `workflows.match_threshold` + `workflows.enabled` already exist |
| Audit | ConfirmationRequest resolutions + verified-done flips SEL-logged (`sel.log_tool_invocation`) |

---

## 10. Risks

1. **Per-file JSON, no transactions** — materialization dedup and lease CAS rely on flock + atomic_write discipline, not transactions. Mitigation: fingerprint-lookup-before-create, `single_flight` around lease acquire, projection-as-idempotent-recompute (R5) so any race resolves on the next rebuild.
2. **Surfacing regression risk** — three channels could over-fire. Mitigation: `off`-by-default for new defs, negative triggers, once-daily cadence throttle, fingerprint runs only on directory attach, trigger-accuracy CI per bundled template, visible chips (R15) so a bad match is one toggle away from silenced.
3. **Coexistence drift** — old + new surfacing running in parallel. Mitigation: one-source-two-wrappers drift check; migrated defs prefer template path when both match.
4. **Snapshot/portability gap** (recon: persistence-security gotcha 10): neither snapshot nor export covers `tasks/` or `workflows/` today — new run/task/confirmation state is NOT backed up. Flag to the DURABILITY-SYNC plan; do not claim full-state backup.
5. **Status-taxonomy churn on FE** — new `SKIPPED` status + blocked kinds touch board columns, filters, and taskMeta in one release. Mitigation: label mapping is configuration; unknown kinds degrade to plain `blocked`.
6. **Verification cost** — engine-run criteria add command executions per task completion. Mitigation: criteria are optional (absent = old claimed-done behavior for standalone tasks); verify commands run through the existing bounded/audited `run_verify_command`.

---

## Implementation Effort

- **7 sessions** (after Workflows v2 Slices 0-4) — was 3; the approved high-priority set (R1/R2/R3/R4/R5/R6) adds ~1 session, the medium/low set adds ~3 more.
- **Session 1 — Projection core:** `workflow_binding` + new Task fields (`blocked_kind`, `preview`, `done_criterion`, `evidence`, `attempts`, `fingerprint`) in tasks/models.py; `TaskStatus.SKIPPED`; auto-materialization with fingerprint dedup + fan-out caps; typed state projection table (R1, R5-core, R12 body contract).
- **Session 2 — Verified done + enforcement:** engine-owned criterion execution (reuse `run_verify_command`), pass-state gating, completion records; three-actor transition matrix + managed-write rejection on the task façade; cascade-fail propagation + debounced notify; stuck-work diagnostics sweep (R2, R5-rest, R17).
- **Session 3 — ConfirmationRequest + gates:** the unified durable record with atomic single-use resolution + auto-resume; `require_hitl`; needs-input queue semantics; DagView Approve/Deny wiring; guardrails/postconditions def sections + verbatim injection; per-stage mute + step-scoped approval memory + tool profiles (R6, R9, R13; FE half of §7 board work).
- **Session 4 — Surfacing core:** `surface_mode` enum + trigger-phrase `match_text` + collision check + negative triggers (port the 0.62/0.7 algorithm from `workflows/surfacing.py`, preserving the never-break-a-turn bridge); metadata split (summary/when_to_use/digest) + lints; one-source-two-wrappers injection contract + overlays; dual-mode injection; SOP migration utility with all three lints; coexistence drift check (R3, R4, R14).
- **Session 5 — Surfacing channels + resolution:** cadence channel + overdue escalation via create-task; fingerprint channel + packs; layered scope resolution with visible shadowing + per-stage overlays; parameter pre-fill contract + requirements preflight (availability three-state, per-step recheck); reachability doctor + trigger-accuracy CI harness (R8, R19, R18, R11).
- **Session 6 — Pool + templates:** frontier/next projections + evented unblock + TTL'd leases + write-time acyclicity; task lifecycle events on the hook bus; seed template library (franklioxygen imports, audit-and-file triage flavor, clean-exit checklist) + hand-off edges; blueprint sessions (R10, R7, R16).
- **Session 7 — UX + validation:** composer chips + validated deep-links + checklist edit UX (R15); config four-point wiring; end-to-end validation as-a-user: run a checklist workflow → tasks appear in board → gate pauses on a ConfirmationRequest → resolve in DagView → run auto-resumes → verified-done flips with evidence → cadence + fingerprint proposals fire correctly → surfacing chips toggle.

## Success Criteria

1. Running a workflow produces tasks visible in the existing Tasks board/list/DAG/cards views, with typed blocked kinds, previews, and progress on cards.
2. Task status updates automatically as workflow nodes complete — and `done` means **engine-verified** done: a stage claiming completion without its criterion passing projects as blocked, with evidence recorded on pass.
3. Direct status writes on managed tasks are rejected by the API; agent tool calls cannot self-mark tasks done (actor matrix); a rewound run refreshes existing tasks (fingerprint dedup) instead of duplicating.
4. A migrated SOP (old "deploy-checklist" → workflow template) executes with approval gates backed by durable ConfirmationRequests; resolving one in the Tasks DAG (Approve/Deny now wired) atomically consumes it and auto-resumes the run; double-resolve is impossible.
5. Lightweight SOPs still auto-surface as passive guidance at the real thresholds (0.62 semantic / 0.7 keyword) — digest-tier, fenced, verbatim; new defs default `surface_mode="off"`; pasted content never fires a suggestion; every active def passes the reachability doctor.
6. An overdue `cadence_days` def surfaces with a freshness gradient and (in Auto mode) materializes at most one standalone task per day; opening a Python repo proposes the python pack once, dismissibly.
7. `create-task` action nodes (existing provider, no allowlist change) produce standalone tasks that outlive the workflow run, capped per foreach, with fenced context bundles for external sources.
8. Two concurrent sessions cannot double-execute the same task (lease CAS); upstream failure cascades visible `blocked(upstream_failed)` to dependents with one debounced notification.
9. A workspace def shadows the global def of the same name visibly (`shadowed` state shown, adopt affordance works); a per-stage overlay swaps one stage while inheriting the rest.
10. Standalone manual tasks (not workflow-bound) remain fully user-driven, unaffected — except they gain evented unblock, frontier/next, and optional leases.
11. All new config fields round-trip through the four wiring points (schema metadata test green); all new engine events reach the FE (stream-union check); SOP-learning artifacts flow only through the proposals queue (propose-don't-write) and never touch knowledge.db.

---

## Execution log

### 2026-08-02 — session 55 (projection core) DONE — PR #195

`tasks/models.py` gains `WorkflowTaskBinding`, `TaskStatus.SKIPPED` and six projection fields;
`tasks/native.py` names them on create; `workflows/materialize.py` (new) owns the state→status table,
fingerprint dedup, fan-out caps and the write-rejection rule. 92 tests.

- **DISCOVERY — the projection table was missing FIVE of the engine's fourteen `InstanceState`
  members**, and every one fell through to `OPEN`. So a tripped circuit breaker (`escalated`), a
  scope violation, a protocol-violation block, a discarded instance and a `no_change` inherit all read
  on the board as ordinary work still to do. Found by enumerating `InstanceState` and diffing against
  the table rather than by reading my own code. The gaps were filled from the engine's OWN
  classification — `SUCCESS_STATES` contains `no_change` (so it projects DONE), `TERMINAL_STATES`
  contains the other four — and two tests now assert exhaustiveness plus agreement with those sets, so
  a fifteenth state cannot land on a silent default.

- **DISCOVERY — a new model field is DROPPED unless the native provider names it.**
  `NativeTaskProvider.create_task` builds its `Task` field-by-field, so the binding round-tripped
  correctly through `to_dict`/`from_dict` and still arrived EMPTY from `create_task`. Measured against
  a real task store on disk. `update_task` was fine (generic setattr path) — the asymmetry is exactly
  the kind that makes a field look wired.

- **DISCOVERY — `Task.from_dict` dropped `workflow_binding` while `asdict` serialized it.** A
  materialized task therefore read back as STANDALONE after one reload — which is precisely the state
  in which a user's manual status write would be ACCEPTED, silently un-managing engine-owned work. Both
  directions now use the binding's own serializer, and a round-trip equality test covers every new
  field.

- **DISCOVERY — `skipped` silently coerced to `OPEN`.** Measured before adding the enum member: a task
  written with `status: "skipped"` read back as work still to do, on the board the user plans from.
  Tolerance is still right for a genuinely unknown value (OPEN keeps the work visible), and a test pins
  both behaviours so the tolerance cannot quietly re-absorb a real status.

- **`TaskStatus` gained exactly ONE member.** The WHY of a block lives in `blocked_kind`, because a
  status per reason is a state fork every surface re-implements — and the surface that forgets is the
  one showing a stale column. An unrecognized kind degrades to a plain `blocked` badge, which is why
  passing an unknown failure class through as "" is safe and normalizing it into a wrong kind is not.

- **Three binding configurations, not two.** `managed=True` (engine drives status), `managed=False`
  WITH a binding (a task the workflow PRODUCED — provenance recorded, completion untracked), and no
  binding (standalone). Collapsing the middle into unmanaged loses the provenance; collapsing it into
  managed makes the engine responsible for work it only suggested. `from_dict` honors an explicit
  `managed: false` rather than defaulting it to True.

- **Dedup runs on TWO keys.** `(run_id, node_id)` catches the same node re-materializing; the
  FINGERPRINT catches the same work under a different node id, which is what a rewind-then-replan
  produces. Checking only the first duplicates the work on the board; only the second collides two
  genuinely different nodes whose titles match. A `source_ref` beats title+body because it survives a
  re-worded label.

- **The write façade REFUSES, it does not merge**, and the refusal names the alternative
  (`workflow_skip`/`workflow_rewind`). Two writers on one status field produce a board that disagrees
  with the run it shows, and the user believes the board. Protection covers every engine-owned field,
  not just status — a user edit to `evidence` would be a human asserting the machine's finding. But a
  managed task stays writable for `assignee`/`labels`/notes: the engine owns the projection, not the
  whole task.

- **A capped fan-out SAYS how much it is not showing.** Twenty is a readable column; two hundred is a
  column nobody opens. A cap of zero still materializes one, because an empty board for a running
  fan-out is a board that lies by omission. The parent counter names blocked separately from
  incomplete, since "18 of 200" and "18 of 200, 3 blocked" call for different actions.

- **NOT DONE:** the engine call site — `plan_materialization` decides what should exist and the caller
  performs the `registry.create_task`, because wiring it into the controller's ready-transition needs
  the run→TaskList provisioning (also this session's scope on paper) and that in turn needs the
  `tasks_link.py` shared-handle pattern; doing half of it would leave a materializer nothing calls.
  The `push_refresh()`/WS refresh hint and the `TaskCreated`/`TaskCompleted` hook events are surface
  wiring on the same seam. FE `taskMeta` STATUSES mapping for the new `SKIPPED` column and the
  `blocked_kind` badge are FE work. The granularity lint (R2) belongs with SOP migration in session 58.

### 2026-08-02 — session 56 (verified done + enforcement) DONE

`workflows/verified_done.py` (new): engine-owned criterion execution over the existing
`loop/gates` tristate, pass-state gating, the three-actor transition matrix, the weighted acceptance
schema, cascade-fail over the binding graph, the stuck-work sweep and idempotent timing. 70 tests.

- **The tristate was VERIFIED by running the real machinery**, not assumed. `run_verify_command`
  returns `None` for a missing binary AND for a command the safety screen refuses (`rm -rf /` →
  `None`, logged as "refusing to run"). Both matter: reading `None` as a pass makes a broken check
  indistinguishable from a passing one, and the broken one is silent. `UNRUNNABLE` also wins over
  `FAILED` when both are present — "one check failed and one could not run" is a criterion nobody has
  evaluated, and calling it a failure sends the user after the wrong problem.

- **DISCOVERY — gating on `Verdict.passed` alone blocked every CRITERION-FREE task.** An empty verdict
  is `None` (nothing was evaluated), so a task with no `done_criterion` — which is most tasks —
  projected as permanently BLOCKED. Caught by checking against the EXISTING seam rather than my own
  reasoning: `Task.can_mark_complete` already documents that "a task with no exit criteria is freely
  completable". Two seams disagreeing about the same question would make completability depend on
  which one ran. A test now pins the agreement.

- **A worker does not judge its own work, at BOTH levels.** The node-level version is the engine
  running the criterion; the task-level version is the actor matrix refusing `AGENT → done`. The
  agent's allowed set is `{blocked}` with kinds `{needs_input, capability}` — it may PROPOSE and may
  not CLAIM. It specifically may not file its own failure as `transient`, because that is requesting
  its own retry. The refusal names the alternative, since a refusal that does not say what to do reads
  as the feature being broken.

- **A USER may not SKIP a task.** A skip is a routing decision the run makes, so a user who wants work
  skipped asks the run (`workflow_skip`) and the board and the run agree afterwards. The engine may
  record any outcome — it observed the work, and restricting it would mean an engine that saw a
  failure could not record one.

- **Every check must pass, and the score is for the REPORT.** An acceptance criterion with a failed
  check has not been met; 0.8 is not "mostly done", it is one unmet requirement. The weighting exists
  because the author said which checks matter, and a zero weight is treated as 1 rather than as
  "ignore" — a check that ran and failed is information, and silently dropping it would let an author
  disable a check by typo. A malformed check is DROPPED and reported, never treated as passing.

- **The cascade follows the BINDING graph.** Driven on a graph with a later sibling reading the failed
  node's output — the shape a tree walk misses, and the one where an unblocked task is most
  misleading. Transitive (a cascaded block cascades onward), terminates on a dependency cycle
  (verified with an alarm), leaves unrelated work alone, and notifies ONCE: a parallel fan-in failure
  produces N events in milliseconds, and N alerts for one cause is how a user mutes the channel that
  was about to tell them something important. Clearing covers exactly the set the block covered — a
  dependent left blocked after its prerequisite recovered is the same lie in the other direction.

- **The sweep REPORTS rather than fixes.** Auto-resolving a stall would hide the condition that caused
  it and the same stall would recur with nothing recorded. It is tolerant of unparseable timestamps
  for the same reason a diagnostics pass exists at all: raising on one bad row would stop it reporting
  every other stall. A task with NO timestamps is not flagged — absence is not staleness, and flagging
  it would put every freshly-created task on the strip.

- **`cancelled` is sticky and `started_at` is written once.** Projection is an idempotent REBUILD —
  the normal path — so without stickiness every rebuild would resurrect work someone deliberately
  stopped. And a retry that rewrote `started_at` would make a task running for an hour look
  thirty-seconds old, which is exactly the field the heartbeat sweep reads to decide whether work has
  stalled.

- **`done_without_evidence` takes `Any`, deliberately.** The sweep reads status off a provider-supplied
  object, and a provider handing back a raw string must get a truthful answer rather than a type
  error; the identity comparison makes a non-status value read as "not done", which is the safe
  direction.

- **NOT DONE:** the engine call sites — criterion execution is decided here and performed by the
  caller (one implementation of the tristate and one safety screen, both already in `loop/gates`), and
  wiring it into the controller's completion path needs the S55 materialization call site that is
  itself still pending. The `intake_refresh` Run Ledger event, the debounced `state.notify()` call and
  the Tasks-board stuck-work strip are surface wiring on this contract. The clean-exit checklist
  template belongs with the bundled-template work in §8; the granularity lint belongs with SOP
  migration in session 58.

### 2026-08-02 — session 57 (ConfirmationRequest + gates) DONE

`workflows/confirmation.py` (new): the one durable typed record, per-type expiry policy, the four-verb
resolution vocabulary, `require_hitl`, per-stage mute, tool profiles and the DagView approve/deny card.
Two SHIPPED defects fixed in `human_input.py` and `security.py`. 58 tests.

- **DISCOVERY (shipped bug, in a correctness-critical path) — single-use resolution did not hold.**
  `consume_continuation` documented "`unlink` is the atomic primitive — two racing resumes both read
  the file, but only one `unlink` succeeds". Measured with 8 threads racing one token: MULTIPLE callers
  received the payload in **36 of 40 trials**. Two reasons, both fatal to the claim — every caller had
  already READ the file before unlinking, and `unlink` does not reliably raise `FileNotFoundError` for
  the losers on this filesystem. That is the exact double-approval replay the single-use rule exists to
  prevent: two resumes carrying one clarification into downstream steps. Replaced with `os.rename` as
  the claim primitive, which decides the winner BEFORE anything is read — measured 0 of 40 — and
  leaves the claimed record on disk under a `.claimed` suffix so a resolution that crashes mid-resume
  is recoverable and auditable rather than silently gone. All 49 existing continuation tests still
  pass.

- **DISCOVERY (shipped gap in a security control) — `security.redact` missed three real credential
  shapes.** Found while checking that the ConfirmationRequest preview was actually safe:
  `sk-live-ABCDEFGH1234567890` survived (the `sk-[A-Za-z0-9]{32,}` pattern cannot match a key with
  hyphens or underscores in its body), and there was **no generic assignment form and no bearer form at
  all** — so `api_key=<anything>` and `Authorization: Bearer <jwt>` both passed through untouched.
  Widened with a hyphen-tolerant provider pattern, a NAME-keyed assignment pattern (so an unknown
  provider's key format does not have to be guessed) and a bearer pattern. Checked in BOTH directions:
  every previously-covered shape still redacts, and ordinary prose ("the API key rotation policy",
  "we discussed passwords", "bearer of bad news") is untouched. 283 security tests pass.

- **DISCOVERY — `str(None)` is `"None"`, which is not empty.** An absent payload previewed as the
  literal word "None" in an inbox row — a value a user reads as content the run produced. Caught by
  the test asserting an empty payload previews as empty.

- **The preview is redacted at CONSTRUCTION, and fails closed.** Redacting at render time means every
  surface has to remember; if `redact` is unavailable the preview is withheld, because a preview is the
  field most likely to carry a fetched credential and an unredacted one is worse than none.

- **Expiry is per-TYPE, declared rather than defaulted.** A destructive confirmation AUTO-REJECTS on
  expiry — the action does not happen, which is the recoverable direction, and auto-approving because
  nobody looked would be the worst behaviour this module could have. An approval or needs-input HOLDS:
  the user being slow does not make the work unnecessary. A single global default would have to be
  wrong for one of them. `ttl: 0` means never rather than instantly, because an author writing it means
  "wait for me".

- **Four resolution verbs, and SKIP is not REJECT.** Skip leaves the item pending for the next pass; a
  queue without it forces the user to answer in the order the engine happened to ask. Reject resolves
  AND resumes, down the declined path — leaving it pending would strand a run whose answer was given.
  An unknown verb is REFUSED rather than treated as a reject: a typo silently declining work the user
  meant to allow is a failure they cannot diagnose.

- **A destructive confirmation cannot be MUTED.** "Stop asking me about deletions" is a request to
  remove the last check before an unrecoverable action — the one setting that cannot be undone by
  changing it back.

- **Tool profiles reuse S48's `Capability` vocabulary.** Two least-privilege vocabularies would
  disagree about a tool and the looser one would win. An unknown profile name is refused rather than
  defaulted: loose would silently over-grant, strict would silently break a stage that needs to write
  and the author would debug the wrong thing.

- **`require_hitl` must be the boolean `True`.** A truthy string is an author mistake, and treating
  `"false"` as a gate would surprise them in the direction of extra prompts they cannot explain.

- **NOT DONE:** persisting the record itself — it rides the existing continuation store by design (one
  claim primitive, one directory, one audit trail), and the writer belongs with the controller's
  gate-entry path, which is the same seam S55/S56 are waiting on. The FE DagView wiring of
  `onApprove`/`onDeny` is the declared extension point this session supplies the backend for (§7 wires
  it). The needs-info question template and the `guardrails`/`postconditions` def sections (R9) are
  session-58 SOP work.

### S58 — Surfacing core (`workflows/surfacing.py`, 80 tests) — DONE

Three defects measured, all of them "present and inert":

- **The prose detector vetoed a legitimate trigger.** A substring scan for common connectives
  flagged `ship the release` — a phrase an author would reasonably register — so a def could be
  linted into unusability. Fixed by matching SUBORDINATING phrases only, plus a hard
  `MAX_TRIGGER_WORDS = 6` word cap: the property that makes a trigger bad is that it is a
  sentence, and a word count measures that directly where a connective scan guesses at it.

- **`!`-prefix negative triggers were parsed here AND in `skills/surfacing`.** Two parsers for one
  author-facing syntax drift, and the drift shows as a veto that fires on the skill path and not
  the workflow path (or the reverse) for the same `match_text`. `trigger_phrases` is now the one
  splitter and `_keyword_score`'s convention is the one it implements.

- **`render_suggest` could have forked from `render_passive`.** The plan asks for a coexistence
  period, which is exactly the window in which two renders diverge unnoticed — both look
  plausible and nobody diffs them. `drift()` asserts the suggest render CONTAINS the passive
  render verbatim (not "resembles"), so a fork fails a test rather than shipping.

Decisions worth recording:

- **`SUGGEST` is required for a suggestion, not merely "not off".** A passive def surfaces
  guidance and proposes running nothing; collapsing the two modes would let a def that was
  migrated for guidance start proposing execution.

- **An empty digest renders as `""`, not as an empty labelled block.** A block titled "Standing
  guidance" with nothing under it reads as the system having nothing to say, which is worse than
  saying nothing. The suggestion path still renders, because a suggestion's value is the CALL.

- **`veto_reasons` returns ALL reasons.** A def vetoed for three reasons has an author who should
  see three; returning the first would send them to fix one and be surprised again.

- **A migrated SOP lands in PASSIVE unless it declared `auto_surface`.** Surfacing is preserved by
  the migration; execution suggestion is not something a migration grants. `findings` is separate
  from `metadata` so a silent normalization is auditable — the SOP is a document the user wrote.

- **`unreachable()` is the reachability floor.** A def in a surfacing mode with no positive trigger
  can never surface and its author has no way to notice; S59 owns the full doctor.

- **NOT DONE:** the cadence and fingerprint channels, layered scope resolution, parameter pre-fill,
  and the reachability doctor surface are all session-59 scope, as the queue splits them.

### S59 — Surfacing channels + resolution (`workflows/surfacing_channels.py`, 113 tests) — DONE

Probed the seams live before writing a line. Three findings, one of which changed the design:

- **The `create-task` hook silently drops unknown keys.** Measured: an action config carrying
  `linked_def` and `workflow_binding` returned `success=True` and created a task whose
  `workflow_binding` was `None`, with neither value in the persisted JSON —
  `CreateTaskActionProvider.execute` renders `title_template`/`body_template` and passes through
  only `priority`/`project`/`assignee`/`due`/`labels`. The plan's "materialized tasks carry an
  explicit bidirectional link block" would therefore have been a control that runs, reports
  success and enforces nothing. **DEVIATION:** the link block lives in the cadence ledger (which
  has to hold the throttle timestamp anyway, so one record holds both directions), and
  `escalation_action` emits ONLY keys the provider actually reads — pinned by a test that fails if
  a future key is added blind.

- **`WorkflowDef` has no `scope`, `cadence_days`, `fingerprint` or `overrides` field yet**
  (measured against `dataclasses.fields`). So this session's records are standalone and def-side
  wiring belongs to the session that adds the fields. Asserting against a field that does not
  exist is how a test passes while the feature is absent.

- **A pre-existing suite-wide test-isolation leak, root-caused and fixed.** `test_workflows_api`'s
  preflight-422 test failed in the full xdist run at 13715→13826 tests purely because the
  distribution shifted. Bisected to THREE files that register provider entries into the
  process-global `get_default_registry()` singleton and never remove them
  (`test_can_resolve_use_case`, `test_provider_resolution_unify`, `test_provider_create_bedrock`).
  A leaked CHAT-capable entry makes `preflight`'s `can_resolve_use_case` probe succeed, so the run
  STARTED (202) instead of being refused (422) — a workflow ran because an unrelated file left a
  model provider behind. Confirmed pre-existing by reproducing the two-file pairing on a clean
  tree. Fixed with ONE snapshot-and-restore autouse fixture in `tests/conftest.py` alongside the
  other process-global guards; a name-list fixture was tried first and missed `MyCloud2`, which is
  why the shipped version snapshots. Registered TYPES are left alone (idempotent, and a type with
  no entry resolves nothing).

Decisions worth recording:

- **NEVER_RUN is its own freshness band.** A checklist authored yesterday has not failed to run;
  reporting it as maximally stale on day one trains a user to ignore the column. It surfaces
  overdue-first but does NOT auto-materialize — an authored-and-never-run def is a draft, and a
  "you are overdue" task for something never started reads as the system malfunctioning.
- **Lateness sorts PROPORTIONALLY.** Absolute lateness parks every long-cadence def permanently at
  the top of the list.
- **Last-completed is DERIVED from `store.list_runs`,** never cached on the def: a cached stamp and
  the run table disagree the first time a run is deleted. A broken store degrades to "never run"
  rather than raising — the channel runs inside a turn.
- **Escalation throttles per DAY, not per tick,** as the plan states; a tick-rate throttle puts one
  task on the board per scheduler pass.
- **A pack with no predicates scores 0.0, not 1.0.** A pack matching everything would propose
  itself in every directory — the over-firing failure this channel exists to avoid. The scan is
  bounded (`MAX_SCAN_FILES`) and skips vendor dirs: a fingerprint inside `node_modules` describes a
  dependency, not this project.
- **`propose_packs` carries `enabled_anything=False` as a FIELD.** Propose-don't-enable as
  something a test can check, not a docstring claim.
- **A DISABLED def neither shadows nor wins.** Calling it shadowed would tell the user something
  else is winning when nothing is. An unknown scope sorts widest, so a scope this build cannot read
  never shadows a def the user explicitly wrote.
- **Availability is probed in order (installed → enabled → available)** and a probe that RAISES
  reads as UNAVAILABLE. An availability hook is code from a removable bundle; treating its crash as
  a pass surfaces a suggestion that dies at dispatch — the exact failure preflight prevents.
- **`all_filled` is re-derived from the schema,** never trusted from the extractor, and
  `suggestion_inputs` never emits a placeholder: a placeholder passes the engine's presence check
  and then executes a step against a made-up value.
- **The doctor accepts ANY channel** (trigger, cadence, pack, index). Checking only `match_text`
  would report every cadence-only def as broken, which trains a user to ignore the doctor. `off` is
  not a finding — it is a deliberate choice and explicit invocation always works.

- **NOT DONE:** the def-side fields (`cadence_days`, `scope`, `fingerprint`, `overrides`) and the
  templates-list UI (freshness gradient, scope chips, grouped pack proposal) — the plan assigns the
  surfaces to §7/session 7, and the fields land with the def-model session. The semantic channel
  (channel 1) is untouched by design: it is the existing mechanism.

### S60 — Pool + hand-offs + blueprints (`workflows/pool.py`, 78 tests) — DONE

Probed the seams first. Three findings, one of them a defect in this session's own first draft:

- **`TaskComplete` is a declared hook event that NOTHING fires.** It is in `hooks.HOOK_EVENTS`,
  allowlisted in `validation.py::ALLOWED_HOOK_EVENTS`, and rendered by the hook UI with
  `_LIFECYCLE_BASE_VARS` — so a user can configure "when a task finishes" and get nothing.
  `validation.py` admits it in a comment: "the rest are reserved for future firing sites and
  currently never trigger". A repo-wide search for `fire(` finds exactly one call site
  (`fire_tool_hooks`). This session supplies the payload builder (shaped to `ScriptHookStore.fire`'s
  real signature, asserted by test) and the EDGE-trigger rule that make it fireable.
  **DEVIATION:** the plan's `TaskCreated` is not a shipped event name. Adding one here would create
  a vocabulary the hook UI does not render, so task-creation events wait until the event is declared
  where users can see it.

- **Acyclicity was ALREADY server-authoritative.** `tasks/native.py` calls
  `reconcile.would_create_cycle` on both create and update. The plan's "the server-side write path
  adds the authoritative check" was already satisfied, so `plan_edges` delegates rather than
  shipping a second DFS — asserted by a spy test, because two cycle checkers means the looser one
  lets AionUI's A-blocks-B/B-blocks-A deadlock through. A missing checker reports
  "cycle check unavailable" rather than "no cycle": fail-closed, or a broken import silently
  disables the guard.

- **My own priority scale was INVENTED.** The first draft weighted `urgent | high | medium | low`.
  The shipped `TaskPriority` is `critical | high | medium | low | trivial` — there is no `urgent`.
  So `critical`, the most important rung in the product, would have fallen through to the default
  weight and ranked BELOW `high`. Caught by a test asserting the weight table's keys equal the enum
  values; that test is the one worth keeping.

Decisions worth recording:

- **The frontier EXCLUDES leased work by default.** A list that shows what another session is
  actively holding invites exactly the double-execution the leases prevent. `include_leased` exists
  for the board, which displays claims rather than picking work.
- **`next` is `frontier`'s head by construction,** so the list and the pick cannot disagree — the
  point of having one projection instead of a per-surface re-derivation.
- **A task blocking others outranks an equal that blocks nothing.** That is the whole value of a
  dependency-aware pool; ties then break on recency and id, because an unstable "next task" makes
  an agent thrash between two equals.
- **An EXPIRED lease is takeable but NOT renewable.** Between expiry and renewal another session
  may already hold it; extending silently would produce two holders who both think they won. A
  takeover resets `renewals` — carrying the dead holder's count forward makes a stuck task look
  actively worked. A same-holder re-acquire is a renewal, so a restarted session is not locked out
  of its own task.
- **One of two prerequisites completing does NOT unblock.** The bug a naive "completion unblocks
  dependents" rule ships with: work becomes visible before its other prerequisite is done. A FAILED
  blocker cascades regardless of siblings and carries the blocker's REASON, because the dependent's
  card should say why. A cascade burst coalesces into ONE notification.
- **Completion firing is EDGE-triggered.** §1 makes idempotent projection recompute the normal
  path, so a level-triggered fire would emit a hook per rebuild.
- **A hand-off SUGGESTS and carries only ALLOWLISTED fields.** Auto-starting a successor spends a
  second run's budget on a decision the user did not make; passing the whole outcome would carry a
  previous run's credentials and artifacts into new inputs, and a hand-off is exactly the seam
  where nobody would look. `review → fix` requires an explicit user request, per the plan.
- **A GATED def can never be a blueprint.** A blueprint has no engine, so there is nothing to
  pause, and rendering a gate as a numbered message shows the user an approval that approves
  nothing. Hydration is replace-not-merge and re-hydrating the same blueprint into the same session
  is a no-op — the defensive case is a client retrying the open, and a merge would print step 1
  twice.

- **NOT DONE:** the seed template library's actual def files (`checklist`, `sop-guided`,
  `audit-and-file`, `clean-exit`, the franklioxygen imports) — the hand-off EDGES that bind them
  ship here, but the defs themselves are content that belongs with the def-model fields, and
  authoring defs against a `WorkflowDef` that still lacks `surface_mode`/`cadence_days`/`scope`
  would mean writing them twice. The lease WRITE path (flocked read-modify-write on the task JSON)
  and the `TaskComplete` emission call site are single-line wirings into `tasks/native.py` that
  belong with §7's wiring session; the decision rules they implement are complete and tested here.

### S61 — Def-side surfacing fields + the def→record adapter (33 tests) — DONE (RE-SCOPED)

**RE-SCOPE, recorded as a deviation.** The queue names session 61 "UX + validation: composer chips,
validated deep-links, checklist edit UX, config four-point wiring, end-to-end as-a-user sweep".
Measured before starting: **every mechanism S55-S60 built is unreachable from an authored def.**
`WorkflowDef`/`DefMetadata` had no `surface_mode`, `cadence_days`, `escalation`, `packs`,
`hands_off_to` or `guided` field (checked against `dataclasses.fields`), and no core module imports
`materialize`, `verified_done`, `confirmation`, `pool` or `surfacing_channels` — they are pure
decision modules with no callers. A composer chip for a def that cannot declare
`surface_mode`, or a templates list sorting by a `cadence_days` that does not exist, would be UI
over nothing; the end-to-end as-a-user sweep the session ends with cannot pass either. So this
session took the actual blocker — the def-side fields plus ONE adapter per record type — and the FE
surfaces + the sweep move to the next session, which will have something to drive.

Findings:

- **The fields went on `DefMetadata`, typed, not in `extra`.** That block's own comment records why:
  `from_dict` drops what it does not name, and annotating all 18 bundled templates with `keywords`
  once left the matcher reading 0 of 18 while running on description overlap at 0.02-0.22
  confidence. A field in an open dict is a field the reader treats as absent.

- **Coercion goes in the SAFE direction, per field.** An unknown `surface_mode` reads as `off` (a
  typo must not start surfacing); an unknown `escalation` reads as `manual` (materializes nothing);
  a negative `cadence_days` clamps to 0 rather than being kept, because a negative cadence makes
  every comparison read as overdue and a fat-fingered `-7` would nag forever; a non-numeric cadence
  is 0 rather than a load failure, since def metadata is hand-authored YAML; `guided` must be the
  boolean `True`, matching §4's `require_hitl` rule.

- **A test that passed for the WRONG reason, caught by measuring.** `Node.cases` is a **dict**
  keyed by label, not a list. The first version of the branch fixture built a list, `models.walk`
  raised `AttributeError`, and `route_from_def`'s except-branch swallowed it and returned RUN — so
  the assertion "a gate buried in branch cases routes to RUN" passed while proving nothing about
  traversal. Fixed the fixture; the assertion now exercises the real walk.

- **`route()`'s `off` short-circuit was WRONG, and S60's test had pinned the wrong behaviour.**
  Returning PASSIVE for an `off` def BEFORE the structural check reported a **gated** def as
  passive — which tells a caller it may be injected as text and silently drops the gate. Structure
  now decides first: `route` answers "what IS this def", and whether it may surface is
  `surfacing.veto_reasons`, a separate question with a separate answer. What `off` still governs is
  BLUEPRINT, because materializing a guided conversation for a def the user switched off would put
  it on screen anyway. The S60 test was updated to the corrected rule with the measurement recorded.

- **One adapter per record type, not per call site.** `meta_from_def`, `cadence_from_def`,
  `handoffs_from_def`, `doctor_entry`, `route_from_def`. Two readers of the same fields drift, and
  the drift shows as a def that surfaces through one path and not the other for identical metadata —
  S58's `drift()` check applied to the fields themselves. The adapter deliberately does NOT
  re-implement tolerance (`from_dict` already coerced), and `doctor_entry` is built centrally
  because a surface assembling that dict itself would forget `packs` and report every pack-gated def
  as unreachable. `cadence_from_def` takes the run facts as PARAMETERS: a channel that queried per
  def would issue one query per template on every list render.

- **NOT DONE (moves to the next session):** composer chips, validated deep-links, checklist edit UX,
  the templates-list freshness/scope/pack rendering, config four-point wiring, the FE stream-union
  registrations, and the end-to-end as-a-user sweep. Also still unwired: the lease write path, the
  `TaskComplete` emission call site, and the confirmation resolve endpoints — all single call sites
  into existing files, now unblocked because the def can finally declare what they read.

### S61b — The wiring that makes surfacing reachable (23 tests) — DONE (PARTIAL: backend only)

S55-S61 built decision modules and gave the def somewhere to declare its surfacing. **Nothing called
any of it.** Three gaps measured, each of which left a shipped mechanism unreachable:

- **`author_def` had NO `metadata` parameter.** Every `DefMetadata` field — including the
  `surface_mode`, `cadence_days` and `packs` the channels read — could be loaded from disk and never
  SET through the API. A field with a read path and no write path is a field only a hand-edited file
  can use, which is exactly what the config round-trip contract exists to prevent. Added, and the
  write goes through `DefMetadata.from_dict(...).to_dict()` so the tolerant per-field coercion
  applies to the WRITE as well: coercing on read alone would store `surface_mode: vibes` while every
  surface displayed `off`, and nobody could explain the gap.

- **`list_defs` drops `metadata` entirely.** Its projection is name/description/source/version/
  tags/provider, so a templates list built on it cannot render a freshness gradient, a surfacing
  toggle or a pack chip no matter what a def declares. Added `list_defs_surfacing` +
  `GET /api/workflows/surfacing` as a SECOND route rather than widening the first: the thin list is
  on the planner picker's hot path, and making every caller pay a per-def run-history lookup to
  render a name is a cost nobody asked for. Cadence facts are batched, not per-def.

- **`TaskComplete` was never fired.** Now emitted from `NativeTaskProvider.update_task`,
  edge-triggered via `pool.should_fire_completion`, with the payload built by
  `pool.lifecycle_payload` so the shape and the rule live together. Verified by measurement: fires
  once on a real completion, does NOT re-fire on an idempotent re-save, and fires again after a
  genuine reopen. The fire is an OBSERVER — it swallows everything, because a user's broken hook
  script must not turn a successful `PUT /api/tasks/{id}` into a 500 when the task is already
  written.

Landmines re-confirmed while probing (all already in the engine-landmines memory, all cost a cycle
each anyway): action args go under `config.with` (not `args`); `registry.update_task`'s second
positional is `provider_name` as a KEYWORD (a positional `"native"` silently became the task id and
returned None); a `sequence` with no children fails validation, so probe specs need a real child.

- **Route ordering:** `/api/workflows/surfacing` is registered BEFORE `/api/workflows/{name}`, with a
  test asserting the index order. aiohttp matches in registration order, so the reverse would make a
  GET for it look for a definition named "surfacing" — the same hazard the function's own docstring
  records for `/runs`.

- **A test-isolation lesson, measured twice now.** Three of this session's tests asserted on
  `defs[0]` or on an empty list and passed in isolation while failing in the xdist mix: the def
  registry is process-global and a full run has bundled providers registered. They now scope rows to
  their own provider. Same class as the provider-registry leak S59 fixed — a global registry means a
  test's view is never just its own writes.

- **NOT DONE (the FE half):** composer chips, validated deep-links, checklist edit UX, the
  templates-list rendering of freshness/scope/packs, config four-point wiring, the FE stream-union
  registrations, and the end-to-end as-a-user sweep. The backend they consume is now real and
  driveable (`GET /api/workflows/surfacing` returns rows + doctor findings; metadata is writable
  through `POST /api/workflows`), so the FE session is unblocked. The lease write path and the
  confirmation resolve endpoints remain unwired.

### S61c — The FE surfacing surfaces, validated as a user (34 FE tests) — DONE (PARTIAL)

The frontend half of §7's surfacing UX, driven against a live gateway rather than asserted.

- **`surfacingMeta.ts` mirrors `workflowMeta.ts`'s discipline:** one place decides the tone and label
  for a freshness band or surfacing mode, because three components each choosing their own colour is
  how "overdue" looks urgent in one place and calm in another. Every helper is pure and reads the
  BACKEND's computed state — `overdue` comes from the response, never recomputed from
  `cadence_days` + `last_completed_at`, because the thresholds (`DUE_SOON_AT`, `STALE_MULTIPLE`)
  have an owner and a second comparison would drift the first time one moved.

- **An `off` def gets NO composer chip.** The chip exists to show the user what the matcher injected
  and let them switch it off; a def that injects nothing has nothing to show, so a chip would be an
  affordance with no referent. Only `suggest` gets the run affordance — a passive def proposes
  running nothing, which is the whole reason the two modes are separate.

- **Deep-link params are an ALLOWLIST against the template's declared inputs**, and the rejected keys
  are REPORTED. A URL is not a trust boundary: a shared or hand-edited link can carry anything, and a
  denylist would silently pass whatever it had not been taught about. Reporting rejections is what
  lets a stale card explain itself — one generated before an input was renamed should say which
  parameter no longer exists rather than quietly starting the run without it. An empty value is
  dropped rather than pre-filled: `''` makes a required input LOOK answered while the engine still
  refuses, so the user sees a filled form and an inexplicable rejection. `missingRequired` re-derives
  from the schema for the same reason the backend re-derives `all_filled`.

- **The list degrades rather than blanking.** A failed surfacing read leaves the plain def list
  intact — the templates are still startable without their freshness column. Surfacing rides
  ALONGSIDE the thin list rather than replacing it, because that list is the picker's hot path.

- **Freshness renders ONLY for a def that declares a cadence** (a band for an untracked def implies a
  schedule it does not have), while the surfacing MODE renders always, including `off`: "this never
  surfaces" is the fact a user most often wants to check, and hiding it makes an off def
  indistinguishable from one whose chip they simply had not seen. A doctor finding WINS the subtitle
  over the description — "no channel can reach this def" is more urgent, and showing both truncates
  the part that matters.

**Validated as a user** (live gateway on an isolated dev home, `AUTH_MODE=none`): authored three defs
over HTTP — one with full surfacing metadata, one unreachable, one with a typo'd mode — then read
them back through `GET /api/workflows/surfacing` and the def detail. Confirmed: the typo'd
`surface_mode: vibes` + `cadence_days: -9` persisted as `off`/`0` (coercion on the WRITE);
`ghost-sop` was the only doctor finding; the overdue def sorted first among 29 templates; the
metadata round-tripped intact. Then drove the Definitions tab in a browser: attention-first order,
the finding as ghost-sop's subtitle, "Every 7 days · files a task when overdue", the "Never run"
band, the Guidance/Off mode chips and the `ci` pack chip all render — with **zero console
messages**.

**DISCOVERY (stale plan premise, E1-class — recorded, not blocking).** The plan's recon correction #4
states "`WorkflowScope` already has FOUR tiers (`GLOBAL | WORKSPACE | AGENT | SESSION`,
workflows/models.py) with an up-only promotion ladder (`workflows/registry.py::promote_workflow`)".
Measured: there is no `WorkflowScope` in `workflows/models.py`, no `scope` field on `WorkflowDef`, and
no `workflows/registry.py` module at all (ImportError). S59's `resolve_scopes`/`SCOPE_ORDER` therefore
own the ladder outright rather than preserving an existing one, and the promotion ladder S45's
`template_pipeline.SCOPE_LADDER` describes is the only one that exists. The plan's §2 R18 text should
be corrected by the owner; nothing here was built against the absent API.

- **NOT DONE:** checklist drag-reorder edit UX (needs the checklist def shape, which is S62+ content),
  config four-point wiring, the FE stream-union registrations for `task_materialized`/
  `confirmation_pending`/`confirmation_resolved`/`task_verified`/`cascade_blocked` (those events are
  not emitted by the engine yet — registering a union member for an event nothing sends would be the
  same present-and-inert control this program keeps finding), the lease write path, and the
  confirmation resolve endpoints.

### S61d — The lease write path + the confirmation resolve endpoint (34 py + 5 FE tests) — DONE

S60 built the lease DECISION rules as pure functions; S57 built the confirmation verbs. Neither had
durability or a caller. Both now do.

**The lease write path survives real process contention — measured, not assumed.** Eight separate
PROCESSES racing one task through `claim_task`, 12 trials: **0 multi-winner**. That is the property
the whole mechanism exists for, and it is the same class of claim S57 measured failing 36 of 40 times
in its read-then-`unlink` form. The read-modify-write is wrapped in `single_flight` (the established
flock primitive) because per-entity JSON files have no transactions, so "read the lease, decide,
write the lease" is otherwise a race between the read and the write. A LOSER of the lock is told the
task is held rather than proceeding — single-flight means don't double-run, and a caller ignoring the
miss would be performing exactly the double-claim the lock prevents.

Design decisions:

- **The lease is a SIDECAR file, not a field on `Task`.** Three reasons in order of cost: a
  once-a-minute renewal written into the entity file rewrites the task every time and races
  concurrent edits to unrelated fields; `Task` is the SHARED model across every provider, so a
  native-only concurrency concept on it makes every provider's task carry a field only one can
  honour; and a sidecar can be deleted to force-release without touching user data.
- **A corrupt or holderless lease file reads as UNCLAIMED.** Degrading to unclaimed risks a brief
  double-claim; degrading to claimed would strand the task permanently with no holder to release it.
  The contention resolves; the strand does not.
- **A task id is sanitized before it becomes a filename.** It arrives from an HTTP path and a
  provider id is not a trust boundary — verified: `../../evil` lands as `.._.._evil.json` inside the
  directory.
- **The sweep deletes unparseable files too.** They already read as "no lease" to every reader, so
  removing them is cleanup rather than a decision, and leaving them means the directory grows forever.

**`resolve_confirmation` rides `resume_run`, and converts the verb to the approval BOOLEAN.** The
engine's gate resolution reads a boolean, so passing `"reject"` through would make a rejection truthy
— the single worst mistranslation available in this path, and pinned by a test. It rides the one
resume path because the claim primitive lives with the token, and a second resolve path would be a
second chance to double-approve. `skip`/`quit` deliberately do NOT touch the run or consume the
token: they are decisions about the QUEUE, not answers to the gate, and burning a single-use claim on
a non-answer would strand the gate forever. An unknown verb is refused, not read as a reject.

`POST /api/workflows/runs/{run_id}/confirm` is guarded by the SAME operation as `resume` (this IS a
resume with a verb vocabulary on top — a separate permission would let a caller who may not answer a
gate answer it through the other door), audits the verb, and does NOT accept `channel` from the body,
for the same reason `api_run_resume` does not: `channel` marks a remote reply the engine owner-binds,
and forwarding it from an untrusted body would let a caller claim to be a channel.

FE: `confirmWorkflowRun` plus `tokenForNode`/`canResolveNode` — the `(run_id, node_id)` join between
the DagView's node ids and the continuation list's resume tokens. An empty token is NOT sent as a
wildcard: the backend reads a missing token as "the newest pending gate", which is right for a chat
user saying "approve it" and wrong for a click on a specific node. An EXPIRED continuation is not
answerable either, so both verbs go false together — a node still offering Approve after its gate was
answered is how a user double-approves.

**Validated over HTTP** against a live gateway (isolated dev home): a typo'd verb is a 400
(`bad_request`), `skip` returns `resumed=false, still_pending=true` without touching the run even for
an unknown run id, and `approve` reaches the run and reports the real `not_found`.

- **NOT DONE:** the DagView component itself is in `pages/tasks/`, and wiring its `onApprove`/`onDeny`
  props needs the run-detail view to render a DAG at all (it currently does not — `WorkflowRunDetail`
  has no `DagView` usage). That is a view-composition change, not a seam gap: the backend, the client
  method and the node→token join all exist and are tested. Also still open: checklist drag-reorder
  edit UX, config four-point wiring, and the FE stream unions (blocked — the engine emits none of
  `task_materialized`/`confirmation_pending`/`confirmation_resolved`/`task_verified`/
  `cascade_blocked`).

### S61e — The task-projection events, both channels (20 py + 1 FE test) — DONE (UNBLOCKS §7)

S61c recorded the FE stream unions as BLOCKED: the engine emitted none of `task_materialized`,
`confirmation_pending`, `confirmation_resolved`, `task_verified` or `cascade_blocked`, and registering
a union member for an event nothing sends would be the same present-and-inert control this program
keeps finding. **That block is now cleared** — the events exist on both channels.

**Two channels, ONE vocabulary.** The five names are ledger kinds (`journal.LEDGER_KINDS`, so a
downstream refiner and the existing drift test see them) AND SSE events published through
`RunController._publish`, differing only by the `workflow_` prefix. A test asserts the prefix relation
mechanically, because a consumer folding the live stream and one reconstructing from history would
otherwise need two vocabularies for one fact and the second always drifts.

**Measured: `_publish` has NO server-side allowlist.** It accepts any event name, so the FE's
`WORKFLOW_LIFECYCLE` array is the only thing standing between an emitted event and a frontend that
never receives it — EventSource silently drops an unregistered type, with no error anywhere. Two tests
now guard that in both directions: a backend test asserts every emitted kind appears in the FE array
(by reading the file), and the pre-existing `workflowMeta.test.ts` drift guard asserts the reverse
plus "no extras". A second test pins the no-allowlist fact, so if one is ever added THAT test fails
and the coupling becomes belt-and-braces rather than the only guard.

Per-event decisions, each of which is a real distinction a reader needs:

- **`task_materialized` carries `refreshed`.** §1 makes idempotent recompute the NORMAL path, so a
  reader counting materializations over-counts the run's output on every rewind without it.
- **`confirmation_pending` is recorded when the WAIT STARTS**, not only when answered. A run that sat
  unanswered for a week and one answered instantly are indistinguishable from the resolution alone —
  and the wait is the number a user cares about.
- **`confirmation_resolved` carries BOTH the verb and the boolean.** The boolean is what the engine
  acted on; the verb is what the user chose. Recording only the boolean would leave an audit unable to
  tell a reject from an expiry auto-reject — the exact distinction §4's per-type expiry policy exists
  to create. An unattributed resolution records `unknown` rather than empty.
- **`task_verified` names the CRITERION.** "Verification failed" without naming what was checked is a
  finding a user cannot act on, and the criterion is the def author's own text.
- **`cascade_blocked` is ONE event carrying every blocked id.** N events for one upstream failure
  would make the run look like it failed N times, and §1 already debounces the notification — two
  collapse points would disagree.

**The durable record does not depend on a live observer.** The emitters write the ledger first, then
publish; a broken observer (verified with a raising publish fn) leaves the history intact, and a run
with no observer at all (a CLI run, a replay) still produces its history.

- **NOT DONE:** the DagView-in-run-detail composition (`WorkflowRunDetail` renders no DAG today — a
  view-composition change, not a seam gap), checklist drag-reorder edit UX, config four-point wiring,
  and the engine CALL SITES that will invoke these five emitters during a real run (the materialize/
  verified_done/confirmation modules own those decisions and are still uncalled from `tick`).

### S61f — The engine CALL SITE for task projection (14 tests, real runs) — DONE

S55 built `materialize` as pure decision functions; S61e gave the events a channel. Nothing invoked
either during a real run — a grep for `materialize.` outside its own module found **zero** hits, so
every rule in it was reachable only from a unit test. `RunController` now projects where a node
settles, and the tests drive REAL runs to completion rather than calling the hook: a call site that is
never reached is precisely the defect being fixed, and only an executed run proves it fires.

**Two measured API mismatches, both of which would have projected NOTHING — silently.**

1. `should_materialize`/`plan_materialization` read the node key **`id`**, not `node_id`. `node_id` is
   the name the *binding* uses, and passing it would have failed the has-an-id refusal for every
   node.
2. `plan.create` holds **`TaskSpec`** objects, not dicts. `entry.get("task_id")` would have raised
   inside the hook's own `except` — which swallows by design — so the projection would have failed
   invisibly on every single node while the run reported success.

Both are the same hazard this program keeps hitting: a plausible-looking integration against an
unread contract. Both are now pinned by tests that assert the SOURCE passes the right keys, because a
behavioural test alone would pass again the next time someone "fixes" the key name.

Verified against real executed runs (bash actions, providers registered — note that `bash` is
registered on demand, and without `_ensure_default_providers_registered()` the probe run fails with
"unknown action provider" and never reaches the success path at all):

- both leaf nodes of a two-step sequence project, with distinct fingerprints, on BOTH channels;
- a `parallel` container does NOT project while its children do — a board row for a container is a
  row nobody can act on, and the container's work IS its children;
- an explicit `materialize_task: false` is honoured (the author's declaration outranks the kind
  heuristic, which is why `should_materialize` checks it first);
- a FAILED node does not project — the hook sits inside the `SUCCESS_STATES` branch, pinned
  structurally by a test that reads the source, so a future edit moving it out shows up here rather
  than as board rows for work that did not happen;
- re-projecting a settled node reports a REFRESH, not a second create.

**The hook never breaks the run.** It swallows everything: the node has already succeeded and its
output is already journaled, so turning a board-row problem into a run failure would lose real work
over a presentation concern. Verified by monkeypatching `should_materialize` to raise — the run still
completes and `step_completed` is still journaled.

**The dedup set lives on the controller** (`self._projected`, declared in `__init__`) because the
controller is the single writer for its own run. A per-node read of the per-entity JSON store would be
one file scan per settled node; a restart re-reads via the projection rebuild, which §1 makes the
normal path anyway.

- **NOT DONE:** the actual Task WRITE (the hook emits the event and records the binding; the write
  through the task provider is the next step, and needs the actor-matrix guard from S56 so an
  engine-owned write is distinguishable from a user edit), the `verified_done`/`confirmation` call
  sites, DagView-in-run-detail composition, checklist edit UX, and config four-point wiring.

### S61g — The Task WRITE, and three defects found by measuring (18 tests) — DONE

S61f wired the call site but wrote nothing. This session makes the Task real — **the first point in
the program where running a workflow puts a row on the user's board.** The write is the ENGINE actor
in §1's three-actor matrix, and the asymmetry is the design: the engine sets a managed task's status
directly, and `materialize.reject_write` refuses that same write from anyone else while naming the
alternative (`workflow_skip`/`workflow_rewind`). Verified both directions on a real run — the engine's
write lands with `managed=True`, a user's `status` write is refused, and a `notes` write still goes
through (refusing everything would make a projected task read-only, and a user who cannot leave a
note on their own board row would rightly call that broken).

**Three defects, each found by measuring rather than reading.**

1. **`run_to_completion` dropped the board row entirely.** The write is scheduled on the loop from the
   SYNC settle path, and completion returned with it still pending — so a caller that awaited the run
   and closed its loop got `status=complete`, no `task_materialized` in the ledger, and no task. The
   user-visible half of running a workflow, lost silently. Fixed by draining in the completion path;
   the regression test deliberately does NOT drain manually, because if a caller has to know to
   drain, every caller that does not is broken.

2. **`asyncio.wait_for(gather(...))` CANCELS what it waits for.** The bounded drain's obvious spelling
   silently killed the very writes it was waiting on — and a cancelled write may already have created
   the task, which loses the id without undoing the row. Found because the test asserted the write
   was still running afterwards and it was not. Fixed with `asyncio.shield`.

3. **A slow test made an unrelated test flaky.** The hung-store drain test routed through
   `run_to_completion` and so paid the default 10s bound — the slowest test in the suite by two orders
   of magnitude. On a shared xdist worker it pushed `test_terminal_handler`'s real-PTY test past ITS
   120s timeout. Rewritten to exercise `drain_projection_writes` directly: 10.03s → 0.05s.

**Plus a pre-existing suite-wide leak, root-caused and fixed.** `test_workflows_grill_protocol.py`
calls `register_bundled_provider()` (18 bundled templates) into the process-global `workflows.defs`
registry and never removes it, so `test_workflows_api`'s `test_listing_is_empty_with_no_providers` saw
18 instead of 0 and `test_save_then_list_then_get` saw 19 instead of 1. Reproduced identically on a
clean tree — this session's +18 tests only changed which worker the two files shared. Fixed with a
snapshot-and-restore autouse fixture in `conftest.py`, beside the two other process-global guards this
program has now needed (provider registry, def registry). That is three of the same shape; the pattern
is that every module-level registry in this codebase needs one.

- **NOT DONE:** the `verified_done` and `confirmation` call sites (the modules own their decisions and
  are still uncalled from a run), DagView-in-run-detail composition, checklist edit UX, config
  four-point wiring. Also worth an owner note: `test_terminal_handler`'s real-PTY test intermittently
  errors in teardown (`OSError: Bad file descriptor`, ~1 run in 3) — pre-existing fragility this
  session's timing changes made visible, not caused.

### S61h — The verification call site, and the tristate it must not collapse (17 tests) — DONE

`verified_done` (S56) had no caller: a projected node's `done_criterion` was never executed by a run.
Wired into the controller beside the projection write — scheduled, not inline, because a criterion is
a shell command (`pytest -q` is the canonical authoring shape) and running it in the sync settle path
would block the whole tick on someone else's test suite. Tracked in the same in-flight set as the
writes, so `run_to_completion`'s drain covers it.

**The defect this session found in its own first draft.** The evaluator returns a TRISTATE
(`True` / `False` / `None` = could not run) and the emitter wrote `bool(passed)`. `bool(None)` is
`False`, so a criterion whose binary was missing reported **"your check failed"** — sending the user
to debug their code when the problem is their environment. §1 projects those two to DIFFERENT blocked
kinds (`needs_input` vs `capability`) precisely because they need different fixes, so collapsing them
at the emitter threw away the distinction the taxonomy exists to create. The event now carries
`passed` AND `unrunnable`, and a test asserts all three outcomes are distinguishable — measured on
real runs: `true` → (True, False), `false` → (False, False), a missing binary → (False, True).

**A second, smaller one:** the "has a criterion" guard used truthiness, so `"   "` passed it, parsed
to zero checks, and reported UNRUNNABLE — a scary "could not verify" for a field its author had
effectively left blank. The guard now asks the PARSER, which is the component that already knows what
counts as empty.

Decisions:

- **A node with no criterion emits NOTHING.** `Task.can_mark_complete`'s rule is that a task with no
  exit criteria is freely completable; emitting `passed=True` for a node nobody wrote a check for
  would manufacture evidence that does not exist.
- **An unparseable criterion is UNRUNNABLE, not failed.** The author wrote something the engine could
  not read, which is a different problem from the work being wrong.
- **An unreadable file check is UNRUNNABLE too** — `evaluate_file_phrase`'s own rule: the phrase may
  well be there in a file this process cannot see, and "the phrase is missing" would be a claim about
  content nobody read. The reader returns `None`, never `""`.
- **A broken verifier does not fail the run.** The node succeeded and its output is journaled; a
  broken criterion must not retroactively fail work that completed.

**Owner note — a pre-existing flake, measured not caused.**
`test_terminal_handler.py::TestTerminalWsIntegration::test_rest_create_list_delete` swings between
0.25s and ~11s **on clean `origin/main`**, in isolation, with `setup=0.01s` and `call=0.01s` — the
time is entirely outside the test body (interpreter/loop teardown around `aiohttp`'s `TestServer` and
`asyncio` subprocess machinery). Under `/usr/bin/time` it is a steady 0.58s, so it is environmental
scheduling, not code. On a shared xdist worker the spike can exceed the suite's 120s timeout and red
an unrelated job. It flaked CI on this stack twice. Worth an owner decision: either give that one test
a longer `@pytest.mark.timeout`, or drop its real-`TestServer` round trip for a direct handler call
like the rest of the file uses.

### S61i — The confirmation-gate emission, and a real fd bug behind a "flaky" test (18 tests) — DONE

S57 built the `ConfirmationRequest` record and its verbs; S61e gave the events a channel. Neither was
emitted by a running gate, so a run could park on an approval and its own history would show
`workflow_needs_input` and nothing typed — "how long did this gate wait" and "who answered it" had no
answer in the ledger.

**Placement is the design.** The PENDING half rides `_ensure_continuation`, which already dedups on
`(path, epoch)` — the watchdog polls a waiting run repeatedly, and a second emission site would have
had to re-derive that idempotency and would have got it wrong. Measured: a re-poll leaves the count at
1. The RESOLVED half fires AFTER the claim is won and the epoch checked; emitting earlier would log an
approval for a race the caller LOST, and the audit would show two people approving one gate. Measured:
a lost race emits nothing, and the ledger holds exactly one resolution.

**The id is derived from `(run, gate, epoch)` via the shipped `confirmation.request_id`, never from
the resume token.** A token is single-use and rotates per poll, so a token-derived id would give the
two halves different values and they would never pair up — a defect that stays invisible until someone
asks how long a gate waited. Verified on real runs: both halves carry the same `cr-…` id, and the
epoch is in the key so a rewind asks a new question.

**Gate classification reads the AUTHOR's declaration** (`risk_category`, then `kind`), not the prompt
text. §4 gives a destructive confirmation a different expiry policy (auto-reject) and forbids muting
it, so misclassifying a deletion as a plain approval would make it auto-APPROVE on timeout — the worst
behaviour available in this module. An unknown risk word falls back to APPROVAL, whose policy is HOLD.

**A REAL bug behind the "environmental" flake.** `test_terminal_handler.py`'s `_make_session` fixture
hardcoded `master_fd=99`, and `_kill_session` does `os.close(sess.master_fd)` for any fd >= 0. Fd 99 is
not the test's fd — it is whatever the process happens to have there. In a bare interpreter it is
closed (hence the intermittent `OSError: Bad file descriptor`); under xdist with aiohttp, coverage and
a live `TestServer`, a process can hold fd 99, and then the test **closes someone else's socket**. That
is the mechanism behind `test_rest_create_list_delete` swinging 0.25s → ~11s and blocking CI twice on
this program. The fixture now defaults to `-1` (the value `_kill_session` itself writes back after
closing, so the guard is exercised rather than bypassed) and offers `_owned_fd()` — one end of a pipe —
for a test that genuinely needs a closable fd. Three consecutive full suites are clean at 14083 passed;
the earlier owner note in #210 is superseded by this finding, and it was NOT environmental.

- **NOT DONE:** DagView-in-run-detail composition (`WorkflowRunDetail` still renders no DAG — a view
  composition change), checklist drag-reorder edit UX, config four-point wiring.

### S61j — The DagView composition, and two defects only a live run exposed (22 FE tests) — DONE

`DagView`'s `onApprove`/`onDeny` had been a declared-but-unwired extension point since it was written,
and `WorkflowRunDetail` rendered no graph at all. Both are now real: a `List | Graph` toggle, a pure
`runDag.ts` layout module, and the gate verbs bound to `POST /runs/{id}/confirm` (S61d).

**Two defects that unit tests could not have found — both needed a real projected run.**

1. **The projection contains NO container rows.** A live gated run projected `root.children[0]`,
   `root.children[1].children[0..1]` and `root.children[2]` — no `root`, no `root.children[1]`. The
   first layout derived column depth by counting PLACED ancestors, so every node had none, landed in
   column 0, and the graph drew one column and zero edges. Depth now comes from the path's own
   nesting (counting `[`, which appears once per container level in every shape the engine emits),
   normalized so the shallowest row starts at the left edge. Edges fall back to the nearest path
   PREFIX among placed nodes, so a nested leaf still links to the step containing it.
2. **The gate overlay was clipped.** `DagView` draws Approve/Deny in a `foreignObject` at `y + h`, so
   a height computed from the node boxes alone rendered the buttons INTO the SVG and below its edge —
   invisible, which looks exactly like the seam still being unwired. The layout now reserves
   `GATE_OVERLAY_H` when any node is awaiting.

A third finding, from `buildTree`: its `TreeRow.depth` counts `.children[...]` SEGMENTS and reports 0
for both `root` and `root.children[0]` — correct for the list's indentation model (a top-level step is
not indented under the root container) and wrong for columns, where it would draw a parent on top of
its own child. The module comment records all three so a later "simplification" cannot reintroduce any
of them.

Decisions:

- **The LIST stays the default.** It carries failure text, remediation and per-item labels a 168px
  node box cannot, and it is what a user reads when something broke. The graph answers a different
  question — where in the shape am I — so it is a mode, not a replacement.
- **The state map is lossy BY DESIGN.** `degraded` and `no_change` are in the engine's
  `SUCCESS_STATES`, so painting them as errors would tell the user work failed when it did not;
  `scope_violation`/`escalated` are errors; anything unrecognized is `todo`, the state that claims
  least, so an older frontend cannot paint a new engine state as a failure.
- **Answerability asks the CONTINUATION list, not the state.** `waiting` also covers a `wait` node
  parked on the clock, and offering Approve on one would ask the user to answer something nobody
  asked them. An expired continuation is not answerable either — the token is gone, and a button that
  always fails teaches the user the UI lies.
- **The verbs are withheld on a terminal run** for the same reason.
- **The graph reuses the continuations this view already fetches** — no second request.

**Validated as a user** (live gateway, isolated dev home): authored a def with a nested `parallel` and
a gated step, ran it, and drove the browser. The graph renders two columns with the parallel's children
to the right, the awaiting gate carries the warn ring, and **clicking Approve on the node resolved the
gate and the run resumed** (`needs_input` → `running`). Zero console errors.

- **NOT DONE:** checklist drag-reorder edit UX, config four-point wiring.

### S61k — The config four-point wiring + checklist edit UX (49 tests) — DONE

Two halves, and both turned out to hinge on something the plan does not mention.

**The config four points — plus the fifth nobody writes down.** §8 names four `WorkflowsConfig`
fields and four wiring points (dataclass + `_meta`, `load()` mapping, `to_dict()`, PATCH allowlist).
All four are wired. But `materialize`, `confirmation` and `pool` each carry their own module constant,
so every field could have been set, persisted, echoed by `to_dict`, rendered in Settings and
**completely ignored** — the present-and-inert control this program keeps finding. So there is a fifth
point: `workflows/settings.py`, one resolver per knob, module constant as the fallback, and the call
sites bound to it. Three tests read the SOURCE of `plan_materialization`/`build_request`/`claim_task`
to assert they resolve from config, because a behavioural test alone would pass again the next time
someone "simplified" it back to the constant.

**DEVIATION — `match_threshold` was NOT re-added.** §8's recon (correction #1) states it exists at
`workflows.match_threshold`. Measured: it does not. `WorkflowsConfig`'s own docstring records why — it
was DELETED with the old SOP feature under the namespace-reuse clean break, and a repo-wide grep finds
the name only in that docstring. The new semantic channel is session-59 scope and its threshold is not
user-tunable yet, so adding the knob now would ship exactly the inert control the rest of this session
exists to prevent. Recorded rather than silently satisfied.

Design calls, each with the failure it avoids:

- **The resolvers are deliberately UNCACHED.** All four are in the live-editable PATCH set; a cached
  read would keep applying the old number until the gateway restarted, which is the difference
  between live-editable and restart-required.
- **An explicit argument still beats config.** The config is the DEFAULT — a template declaring its
  own gate lifetime must not be silently rewritten by a global preference.
- **The PATCH bounds restate what the code enforces** (`lease_ttl_secs` max = `MAX_LEASE_SECS`,
  `max_materialized_per_foreach` min = 1). A stored value the runtime silently clamps is worse than a
  rejection, because the user reads the stored one. `confirmation_ttl_secs` min is 0, because
  `expires_at` reads `<= 0` as "never expires" and refusing 0 would make an intent the record
  supports unreachable through the API.
- **An unreadable `config.json` degrades to the shipped constants.** A malformed file must not stop a
  run from materializing its tasks.

**The checklist UX, and a cosmetic-fix trap.** Drag-reorder already shipped; the plan's two rules did
not. Two-stage destructive reveal now matches the shipped armed-delete pattern (arm, confirm,
4s timeout) rather than inventing a second one — a checklist row is text the user typed and there is
nothing to undo it with.

Checked-locks-drag was the interesting one: **styling the grip as disabled is cosmetic.**
`Reorderable` wraps every item in a `Reorder.Item`, which makes the whole ROW draggable, so a
"locked" row still picked up and reordered. The lock had to move into the primitive — a locked item
now renders as a plain `div`, outside the reorder group — which is what the new `canDrag` prop does.
A completed step's position is the record of what happened in what order, which is the one thing a
checklist is FOR.

**Two guards earned their keep, and one false positive worth knowing.** The design-system
primitive-adoption ratchet caught a raw button (278 > 277) — fixed by using the shared `Button`, not
by raising the baseline. Then it failed AGAIN at the same count: the scanner counts the literal
string, and my *comment* contained the tag name. The comment is now worded around it, with a note
saying why. Separately, `test_config_roundtrip` correctly rejected the new enum field (its generic
rule appends `-x`); the fix is a `_SPECIAL` entry declaring a real member, exactly as
`dashboard.stream_reveal` already does.

**Validated as a user** against a live gateway: `GET /api/config/personalclaw` serves all four fields;
PATCH accepts `lease_ttl_secs=120` and `surface_mode_default=passive`; refuses `99999` ("must be
between 30 and 3600"), `vibes` ("must be one of ['off','passive','suggest']") and `cap=0`; accepts
`ttl=0`. The resolvers then read the patched values with **no restart**.

### 2026-08-12 — WF2TAS-12 (retire the guidance-persistence `Lifecycle`) DONE — PR PENDING

`workflows/surfacing.py` loses the `Lifecycle` enum, the `SurfacingMeta.lifecycle` field and its two
round-trip keys. `test_workflows_def_surfacing_fields.py` gains the adapter-completeness rail plus a
proof it can fail. `inert-surface-baseline.json` regenerated on the shrink.

- **THE FINDING.** S58 shipped `lifecycle` (`one_shot` | `session` | `until_deactivated`) with a
  docstring warning that `until_deactivated` "is the one that needs care: guidance that outlives its
  relevance is guidance the user stops reading". Nothing branched on it. A def could declare
  `until_deactivated`, it round-tripped, it linted, and the guidance behaved identically to
  `one_shot`. `inert-surface-baseline.json` already listed `enum:Lifecycle.UNTIL_DEACTIVATED`.

- **THE MEASUREMENT — the field was inert at BOTH ends of its seam, not just the reader.** The live
  consumer of def surfacing state is `GET /api/workflows/surfacing` → `service.list_defs_surfacing`
  → `surfacing_channels.{cadence_from_def,freshness,overdue,sort_key,doctor_entry,doctor,handoffs_from_def}`.
  It reads `DefMetadata`, and `DefMetadata` has **no** `lifecycle` field — so `author_def` could not
  write one and `meta_from_def` (the ONE conversion point, `surfacing_channels.py:1069`) could not
  carry one. On the reader side the whole passive channel is absent: `render_passive` is called only
  from `render_suggest`/`drift` inside its own module, `agent_digest` — the text passive mode injects
  — is read nowhere in `src/` outside `models.py`/`meta_from_def`, and `meta_from_def` has no
  production caller at all. Two independent corroborations already in-tree: `FS.md` records that this
  module's `may_suggest`/`veto_reasons` are "themselves inert (zero runtime callers)", and
  `test_learning_ambient.py::test_a_template_suggestion_would_fit_the_budget_when_a_producer_exists`
  calls the passive/suggest renders "the two unbuilt producers" in its own docstring. **Today's
  behaviour for a def that has surfaced once: it never surfaced, because nothing surfaces passively.**

- **Why this is a gap and not merely an unbuilt layer.** Sibling policy fields differ measurably:
  `cadence_days` has 5 consumers outside `surfacing.py` and `surface_mode` has 2 — both reach live
  code because both are on `DefMetadata`. `lifecycle` had neither end. (`revisit_window_days`,
  `freedom_level`, `preconditions`, `scope_ref` are in the same `SurfacingMeta`-only position — see
  the DISCOVERY below.)

- **PARKED-vs-DELETED, decided: deleted.** With no per-def surfaced-state anywhere in the system, no
  wiring could make the three members differ observably, and inventing a store to make an enum true
  is explicitly not the job. The remaining choice was keep-with-a-note or clean break. A note that
  the field is "declarative-only pending a consumer" is a prose TODO, which the tenets put in a plan
  file rather than in code — and a field that only looks configurable is worse than a missing one,
  because it teaches an author to declare it and wonder which behaviour changed. The rebuild recipe
  (passive-channel caller → per-def surfaced-state → `DefMetadata` field + adapter mapping) is
  recorded in `docs/roadmap/atomic/WF2TAS.md` under `WF2TAS-12`.

- **Guidance did not get more aggressive — the check that mattered.** `one_shot` was the default, so
  the deletion's risk was silently promoting some def to "surface every match". It cannot: there is
  no passive surfacing path to be more aggressive on, and no matching/veto behaviour changed
  (`veto_reasons`, `may_suggest`, `surface_mode`, `MIN_TRIGGERS`/`MAX_TRIGGERS` untouched). **A def
  that declared nothing before behaves identically after; a def that declared
  `lifecycle: until_deactivated` on disk still loads, with the key ignored** — `from_dict` names what
  it reads, so a dropped field is not a crash on a def somebody already wrote.

- **THE RATCHET.** Two layers, neither of them new machinery. (1) The generic one already exists and
  is the right owner: if `Lifecycle` is re-added with an unreferenced member, the inert-surface
  census counts it, `surfacing.py`'s counter RISES and `test_inert_surface_baseline` reds — writing a
  second bespoke "do not re-add this" guard would duplicate a ratchet the repo already runs. (2) The
  new rail closes the adjacent gap the measurement exposed: `meta_from_def` must name every field
  `SurfacingMeta` and `DefMetadata` share (7 today). Read from the SOURCE via AST, because a field
  the adapter forgot arrives at its dataclass DEFAULT — a legal value, so a behavioural assertion
  cannot tell "carried" from "silently dropped"; only the call site can. Vacuity-guarded at 7 (a rail
  over an empty intersection passes forever), with `test_the_conversion_rail_can_FAIL` proving teeth
  against a doctored adapter that drops `cadence_days` — the quiet half of the defect class, since a
  def would simply never appear overdue and nothing would raise.

- **DISCOVERY — `lifecycle` was not alone in the `SurfacingMeta`-only position.**
  `freedom_level`, `preconditions`, `revisit_window_days`, `scope`, `scope_ref` are also on
  `SurfacingMeta` with no `DefMetadata` twin; the owner's measurement put `revisit_window_days` and
  `freedom_level` at 0 consumers each. They are NOT touched here (this atom's scope is `lifecycle`),
  and they do not show in the inert census because the enum heuristic clears a member whose name is
  accessed as an attribute anywhere in `src/` — `FreedomLevel.MEDIUM` is referenced in
  `surfacing.py` itself. Whoever builds the passive channel should decide all five at once rather
  than one per atom; the adapter rail added here will fire the moment any of them gains a
  `DefMetadata` twin without a mapping.

- **DEVIATION — none.** No gate, no migration: the deleted field had no writer, so there is no
  persisted state to migrate (clean break under the pre-1.0 banner, as the owner's lifecycle
  deferral directs). No user-visible surface changed, so no CHANGELOG entry: the field was
  unreachable from the API, the UI and the CLI alike — `web/src` contains no reference to it.
