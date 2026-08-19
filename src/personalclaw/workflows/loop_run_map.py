"""Every `Loop` field's home on a `WorkflowRun` + `SupervisorPolicy` (PP-16, step 1).

`loop_aliases` answers the NOUN question at the type level — a loop *kind* resolves to a bundled
template. This module answers it at the FIELD level, which is the half that decides whether the
noun change is a migration or a silent feature deletion. The plan's own first implementation step
says it plainly: *"Map every field of the `Loop` row onto `WorkflowRun` + `SupervisorPolicy`, and
name the ones with no home BEFORE writing code — an unmapped field is a feature about to be dropped
silently."* This is that map, in the same idiom `supervisor_policy.POLICY_KNOB_MAP` established for
AG-13's fourteen knobs: one row per field, the destination as a resolvable path, and a test that
resolves every one so a rename on either side reds rather than rots.

**Deliberately inert, and it says so.** Nothing reads this at runtime, and it constructs no run.
It is a DECLARATION, railed in both drift directions (`tests/test_pp16_loop_field_map.py`) — the
honesty marker this program established in `WF2LOO-12` and reused for `PP-14`. The wiring owner is
PP-16's later sessions; this exists so those sessions inherit a measured map instead of re-deriving
it per field, and so the six fields with NO home are an owner decision taken in daylight rather
than a discovery made after `loop/store.py` is gone.

**What the map is measured against, not guessed from.** Each `RUN_INPUT` row names a parameter a
BUNDLED template actually declares (the rail re-reads the shipped `workflow.json` files, so a
template that renames its input reds this), each `RUN`/`POLICY`/`DEF`/`INTENT` row names a real
dataclass field (the rail resolves the dotted path), and each `NONE` row carries the measurement
that established it has no home.

**Three findings this map surfaced, recorded here because they are the expensive half of PP-16:**

1. **A run has no user-facing title.** The runs list labels a row ``{workflow_name} — run {id}``
   (`web/src/pages/workflows/WorkflowsListPage.tsx:372`). `Loop.name` — shown on every loop
   surface, and user-editable via `store.rename` — has nowhere to live. `WorkflowRun.extra` is a
   tolerant-reader spillover dict, not a declared field, so parking a first-class user-visible
   string there is a shape decision, not a migration detail.
2. **The status vocabularies are not a superset relationship.** `LoopStatus` has twelve members and
   `RunStatus` eight, and each has members the other cannot express — see `STATUS_VOCABULARY_DELTA`.
   "One status vocabulary" therefore costs a decision per orphan, not a rename.
3. **`WorkflowRun.task_list_id` is declared and inert.** It has no writer and no reader outside
   `models.py`; and it is singular, where a loop keeps one TaskList PER PHASE
   (`Loop.task_list_ids: {phase_key: task_list_id}`). So the projection to tasks is one field short
   of the loop's shape before any code moves.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Destination kinds. `RUN`/`POLICY`/`DEF`/`INTENT` name a dotted path the rail RESOLVES against a
#: real dataclass; `RUN_INPUT` names a bundled template's declared input parameter; `NODE_CONFIG`
#: names a per-node `config` key (untyped by design — `Node` keeps kind-specific fields there);
#: `PROJECTION` means the value is DERIVED from run state rather than stored (the direction
#: `materialize.py` already proves); `NONE` means no home exists yet.
RUN = "run"
POLICY = "policy"
DEF = "def"
INTENT = "intent"
RUN_INPUT = "run_input"
NODE_CONFIG = "node_config"
PROJECTION = "projection"
NONE = "none"

#: Every legal `dest_kind`, so a typo is a failure rather than an unchecked row.
DEST_KINDS: frozenset[str] = frozenset(
    {RUN, POLICY, DEF, INTENT, RUN_INPUT, NODE_CONFIG, PROJECTION, NONE}
)

#: The kinds whose `dest` is a dotted path the rail resolves against a real dataclass.
DIRECT_PATH_KINDS: frozenset[str] = frozenset({RUN, POLICY, DEF, INTENT})


@dataclass(frozen=True)
class FieldHome:
    """One `Loop` field and where it lives once a loop IS a run."""

    field: str  #: the `loop.loop:Loop` field name
    dest_kind: str  #: one of the destination constants above
    dest: str  #: the dotted path / input name / config key — "" for `PROJECTION` and `NONE`
    note: str  #: the measurement that established this row, or the consequence if `NONE`


#: The load-bearing artifact: one row per `Loop` dataclass field, exhaustive in both directions
#: (the rail fails on a field with no row AND on a row naming no field).
LOOP_FIELD_MAP: tuple[FieldHome, ...] = (
    # ── identity ──
    FieldHome(
        "id",
        RUN,
        "WorkflowRun.id",
        "Same shape (a hex id), and the ledger already keys on it either way — `loop.journal` and "
        "`workflows.journal` are both `personalclaw.ledger` writers (PP-5).",
    ),
    FieldHome(
        "name",
        NONE,
        "",
        "NO HOME. A run has no user-facing title — the runs list labels a row "
        "`{workflow_name} — run {id}` (WorkflowsListPage.tsx:372). A loop's name is user-set "
        "(`store.rename`) and shown on every loop surface. Owner decision: declare a `title` on "
        "WorkflowRun, or accept `extra['name']` (a spillover dict, not a declared field).",
    ),
    FieldHome(
        "kind",
        DEF,
        "WorkflowDef.name",
        "The kind IS the template: `loop_aliases.KIND_TO_TEMPLATE` already resolves all five at "
        "read time, so this row needs no new machinery.",
    ),
    FieldHome(
        "task",
        RUN_INPUT,
        "task",
        "Declared by general-project, goal-pursuit-open-ended, goal-pursuit-verifiable and "
        "code-project. design-project calls it `brief` and deep-research calls it `question`, so "
        "the noun change needs a per-template input name, not one constant.",
    ),
    FieldHome(
        "project_id",
        RUN,
        "WorkflowRun.project_id",
        "Same meaning (the owning Project) — but see `tasks_project_id`: a loop keeps TWO project "
        "links where a run has one.",
    ),
    FieldHome(
        "summary",
        RUN,
        "WorkflowRun.intent",
        "The planner's one-line restatement is exactly what an intent string displays; `task` goes "
        "to the template input because that is what the worker reads.",
    ),
    FieldHome(
        "intake_rigor",
        INTENT,
        "Intent.rigor",
        "`workflows.intent:Intent.rigor` is the same axis with a DIFFERENT vocabulary: "
        "auto|thorough|grill|minimal vs `Rigor` trivial|fast|standard|deep. A value-level "
        "translation, and `auto` (route it) has no `Rigor` member — it is the absence of a "
        "decision, which is what `route_rigor` produces.",
    ),
    # ── the phased plan ──
    FieldHome(
        "plan",
        PROJECTION,
        "",
        "The graph IS the plan: `WorkflowDef.root`'s nodes replace the phase list. Nothing to "
        "carry — this is the field whose disappearance is the point of the atom.",
    ),
    FieldHome(
        "phase_status",
        PROJECTION,
        "",
        "Derived from `NodeInstance` state, the way `materialize.plan_materialization` already "
        "projects node state onto task status.",
    ),
    # ── the worker binding ──
    FieldHome(
        "execution",
        NODE_CONFIG,
        "agent",
        "solo|multi_agent becomes a graph SHAPE (one stage vs a fan-out of agent nodes) plus the "
        "per-node `agent` slug `roster.py` resolves; there is no run-level execution mode.",
    ),
    FieldHome(
        "agent",
        NODE_CONFIG,
        "agent",
        "Per-node, by SLUG (`roster.py` is the projection over `config.agents`). Exactly one "
        "bundled template declares `agent` today, so most templates inherit the default.",
    ),
    FieldHome(
        "model",
        NODE_CONFIG,
        "model_tier",
        "FIDELITY LOSS, recorded: a loop pins a concrete model id; a node picks a TIER "
        "(reasoning|standard|fast — 53 uses across the bundled templates). A per-loop model "
        "override has no exact destination.",
    ),
    FieldHome(
        "provider",
        NODE_CONFIG,
        "provider",
        "Declared per node — measured in the shipped code-project spec, so this is the one worker "
        "binding field that ports without a decision.",
    ),
    FieldHome(
        "provider_agent",
        NONE,
        "",
        "NO HOME. No node config key and no run field names the provider's own agent binding "
        "(the ACP agent within a provider). Nothing in the bundled templates declares it.",
    ),
    FieldHome(
        "reasoning_effort",
        DEF,
        "WorkflowDef.defaults.effort",
        "Per-def rather than per-loop; `RunDefaults.effort` is the same axis, so a per-LOOP "
        "override becomes a per-template default unless a node config key is added.",
    ),
    FieldHome(
        "roster",
        NODE_CONFIG,
        "agent",
        "FIDELITY LOSS, recorded: a loop's roster is N personas on ONE work unit; a graph gives "
        "one agent per node. The personas survive as nodes, the LIST does not survive as a field.",
    ),
    FieldHome(
        "strategy_id",
        NONE,
        "",
        "NO HOME. The orchestration method (`orchestrator` and friends) is a run-level choice a "
        "graph expresses structurally; nothing declares it, so each strategy must be shown to be "
        "expressible as a shape before this field can be dropped.",
    ),
    FieldHome(
        "strategy_config",
        NONE,
        "",
        "NO HOME. The payload of `strategy_id`, and homeless for the same reason: the shape that "
        "replaces an orchestration method has to be named before the config can be dropped.",
    ),
    FieldHome(
        "skill_ids",
        NODE_CONFIG,
        "tools_posture",
        "Always-on capabilities become per-node posture/tool grants (`tools_posture` is the key "
        "the bundled templates declare); the run-level always-on LIST has no field.",
    ),
    FieldHome(
        "workflow_ids",
        PROJECTION,
        "",
        "A loop referencing workflows becomes a subworkflow node — `WorkflowRun.parent_run_id` / "
        "`spawned_by_node_id` are the run-side link, so the id list is structural, not stored.",
    ),
    # ── workspace + run controls ──
    FieldHome(
        "workspace_dir",
        RUN_INPUT,
        "cwd",
        "code-project declares `cwd`; `loop.loop:effective_dir`'s four-tier fallback has no "
        "run-side equivalent and is the behaviour to port, not the field.",
    ),
    FieldHome(
        "auto_teardown_on_complete",
        NONE,
        "",
        "NO HOME. Scratch-workspace teardown on terminal is a loop lifecycle flag; the run side "
        "owns isolation per node (`config['isolation']`, `worktrees.py`) with no run-level "
        "teardown opt-in.",
    ),
    FieldHome(
        "attended",
        POLICY,
        "SupervisorPolicy.hitl_posture",
        "Already mapped by `supervisor_policy.POLICY_KNOB_MAP` (knob 11) — one of the three knobs "
        "that collapse onto this field.",
    ),
    FieldHome(
        "autopilot",
        POLICY,
        "SupervisorPolicy.autonomy.approval",
        "system-drives-phases vs user-queues is an approval posture, which is what the "
        "`SafetyProfile` half of the policy (AG-13 knob 4/14) already expresses.",
    ),
    FieldHome(
        "max_cycles",
        POLICY,
        "SupervisorPolicy.budget_max_cycles",
        "Already mapped by `POLICY_KNOB_MAP` (knob 12), same `0 = uncapped` semantics. "
        "deep-research additionally exposes it as its `rounds` input.",
    ),
    FieldHome(
        "idle_secs",
        POLICY,
        "SupervisorPolicy.idle_secs",
        "Already mapped by `POLICY_KNOB_MAP` (knob 13) — the idle-stall cutoff for one cycle.",
    ),
    FieldHome(
        "success_criteria",
        POLICY,
        "SupervisorPolicy.rubric",
        "The machine-checkable form of the same statement; goal-pursuit-open-ended also declares "
        "`success_criteria` as a template input, which is the human-authored half.",
    ),
    FieldHome(
        "kind_config",
        NODE_CONFIG,
        "config",
        "The umbrella row: every kind-specific key becomes either a template INPUT (measured: "
        "`verify_command`/`guard_command`/`scope`/`constraints`/`exit_condition`/`rounds`) or a "
        "node config key. No single destination — one decision per key, per template.",
    ),
    # ── lifecycle + timing ──
    FieldHome(
        "status",
        RUN,
        "WorkflowRun.status",
        "NOT a rename: see `STATUS_VOCABULARY_DELTA`. Five LoopStatus members have no RunStatus "
        "equivalent and three RunStatus members have no LoopStatus one.",
    ),
    FieldHome(
        "created_at",
        RUN,
        "WorkflowRun.created_at",
        "TYPE CHANGE, recorded: loop epoch float → run ISO-8601 string. Every reader of the "
        "numeric form (list sort, cockpit age) converts.",
    ),
    FieldHome(
        "started_at",
        RUN,
        "WorkflowRun.started_at",
        "Same semantics (the start of the CURRENT running stretch, reset each resume); same "
        "epoch-float to ISO-string type change.",
    ),
    FieldHome(
        "completed_at",
        RUN,
        "WorkflowRun.completed_at",
        "Same semantics (set once on reaching a terminal state); same epoch-float to ISO-string "
        "type change.",
    ),
    FieldHome(
        "elapsed_seconds",
        RUN,
        "WorkflowRun.elapsed_seconds",
        "Same field, same units (banked running time from prior stretches, excluding pauses) — "
        "the one timing field that needs no conversion.",
    ),
    FieldHome(
        "total_cycles",
        PROJECTION,
        "",
        "A run counts tokens and agents, never cycles. The count is the ledger's `step_completed` "
        "events (PP-5 made the loop the second producer), which is strictly better: it survives a "
        "restart and the flywheel already reads it.",
    ),
    FieldHome(
        "error_message",
        RUN,
        "WorkflowRun.error_message",
        "Same field, same meaning — and it is load-bearing for display: a `complete` loop carrying "
        "one is the synthetic `ended_early` status every loop surface shows.",
    ),
    # ── integration links ──
    FieldHome(
        "tasks_project_id",
        NONE,
        "",
        "NO HOME. A loop keeps TWO project links — its own Project (`project_id`) and the backing "
        "Tasks Project — and a run has only one `project_id`. Either they unify (a decision about "
        "whether a run's project and its task project are the same thing) or a field is needed.",
    ),
    FieldHome(
        "task_list_ids",
        RUN,
        "WorkflowRun.task_list_id",
        "SHAPE MISMATCH, recorded: singular on the run, `{phase_key: id}` on the loop — and "
        "`WorkflowRun.task_list_id` has no writer or reader outside `models.py`, so the "
        "destination is declared but inert.",
    ),
    FieldHome(
        "linked_task_ids",
        PROJECTION,
        "",
        "`materialize.py`'s `WorkflowTaskBinding` already projects run state onto tasks — the "
        "id list is the projection's output, not a stored field.",
    ),
    FieldHome(
        "session_key",
        PROJECTION,
        "",
        "A loop binds ONE worker session (`loop-<id>`); the engine opens a session per node "
        "(`SessionMode`), so this is derived per node rather than stored per work unit.",
    ),
)

#: The status members neither vocabulary can express, measured against `LoopStatus` / `RunStatus`.
#: This is the concrete cost of the atom's "one status vocabulary" clause: each orphan is a
#: decision (map it, or drop the state), not a rename.
STATUS_VOCABULARY_DELTA: dict[str, tuple[str, ...]] = {
    # LoopStatus members with no RunStatus equivalent. `intake`/`planning`/`review`/`ready` are the
    # PRELAUNCH quartet a run collapses into `draft`; `stagnant` and `blocked` are the two
    # supervisor-set attention states a run has no member for (`needs_input` means a HUMAN was
    # asked, which is a different fact); `stopped` is a user stop, which `cancelled` covers.
    "loop_only": ("intake", "planning", "review", "ready", "stagnant", "blocked", "stopped"),
    # RunStatus members with no LoopStatus equivalent.
    "run_only": ("draft", "cancelled", "escalated"),
}
