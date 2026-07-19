# Plan: Tasks & SOPs as Workflow Primitives

**Status:** PROPOSED (rev 2 — research-integrated 2026-07-12)  
**Created:** 2026-07-11  
**Revised:** 2026-07-12 — 19 approved research recommendations folded in; recon-corrected against code  
**Depends on:** WORKFLOWS-V2.md (Slices 0-4)  
**Scope:** Task tracking integration, SOP migration, checklist patterns, surfacing discipline, approval/needs-input records

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
