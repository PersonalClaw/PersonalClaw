"""Unified Loop data model — the one autonomous primitive.

PersonalClaw has a single autonomous engine: the **Loop**. Every loop shares the
same spine (understand → break into phases → plan with persona/skills/workflows →
execute each phase until its goal is met) and the same machinery (nudge + a
deterministic watchdog + a file-based worker↔supervisor split). What differs per
loop is **subject-matter expertise**, supplied by a per-:data:`kind` strategy
(see :mod:`personalclaw.loop.kinds`): how it classifies the task, phases the
problem, gates done-ness, loads capabilities, and frames the worker brief.

Kinds: ``general`` (Claude-Code-``/loop``-style generic iteration), ``goal``
(verifiable/open-ended/monitor research+action), ``code`` (SDLC stage-gated work
in a workspace), ``design`` (design-system creation).

The entity holds the genuinely-shared lifecycle/timing/identity fields at the top
level (what the store/manager/watchdog touch generically) plus a ``kind_config``
dict the kind strategy owns (goal_type+granularity; entry_stage+stage_plan;
design tokens; …). Kind-specific *behavior* lives in the strategy, never here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from personalclaw.workflows.models import LifecyclePhase


class LoopKind(str, Enum):
    """The subject-matter axis of a loop. Picks the strategy that supplies the
    type-specific classify/phase/gate/capability/brief behavior."""

    GENERAL = "general"  # generic iterative goal in a chat session (nudge + watchdog)
    GOAL = "goal"  # open-ended / verifiable / monitor research + action
    CODE = "code"  # SDLC stage-gated work in a workspace (mini-IDE cockpit)
    DESIGN = "design"  # design-system creation (live canvas, tokens, components)
    RESEARCH = "research"  # deep iterative web research → synthesized report (evolving subtopics)


KINDS: frozenset[str] = frozenset(k.value for k in LoopKind)


class LoopStatus(str, Enum):
    """Lifecycle states — the UNION of the former GoalLoop + CodeProject enums, so
    one machine serves every kind. ``stagnant`` (goal's no-new-findings) and
    ``blocked`` (code's repeatedly-failing stage gate) are both supervisor-set
    attention states; a kind uses whichever its watchdog logic raises.

    Transitions are enforced in :mod:`personalclaw.loop.store`. Which members are terminal is
    not spelled out here: it is DERIVED from :data:`LOOP_PHASES` below, so the enum names the
    states and the phase map names what they mean (`PP-16`, "one status vocabulary").
    """

    INTAKE = "intake"  # submitted, classifier running
    PLANNING = "planning"  # decomposing / scoping / stage-planning
    REVIEW = "review"  # plan ready, awaiting the user's launch
    READY = "ready"  # created, not yet started
    RUNNING = "running"  # worker armed, agent working
    PAUSED = "paused"  # deactivated by the user
    STAGNANT = "stagnant"  # supervisor saw no new findings for N cycles (goal-ish)
    BLOCKED = "blocked"  # a gate failed repeatedly / stuck (code-ish)
    NEEDS_INPUT = "needs_input"  # attended-mode clarification, or trust expired
    COMPLETE = "complete"  # done-ness met or budget exhausted
    FAILED = "failed"  # worker unresponsive / unrecoverable
    STOPPED = "stopped"  # user stopped it


#: Every :class:`LoopStatus` member's :class:`LifecyclePhase` — the ONE declaration the four
#: membership sets below are derived from, rather than four literals maintained beside the enum
#: and drifting apart (`PP-16`, "one status vocabulary"). Exhaustive in both directions by rail.
#:
#: ``FAILED`` is ``ENDED``, not ``ATTENTION``, and that is the assignment the whole model turns
#: on. A failed loop HAS stopped producing — ``update_status`` stamps its ``completed_at``
#: alongside ``COMPLETE`` and ``STOPPED`` — and it is still a ``resume`` source. Those two facts
#: only look contradictory while "ended" and "terminal" are the same word; split apart, ``FAILED``
#: is simply the one ended state this noun can leave (:data:`RESUMABLE_ENDED_STATUSES`).
LOOP_PHASES: dict[LoopStatus, LifecyclePhase] = {
    LoopStatus.INTAKE: LifecyclePhase.PRELAUNCH,
    LoopStatus.PLANNING: LifecyclePhase.PRELAUNCH,
    LoopStatus.REVIEW: LifecyclePhase.PRELAUNCH,
    LoopStatus.READY: LifecyclePhase.PRELAUNCH,
    LoopStatus.RUNNING: LifecyclePhase.ACTIVE,
    LoopStatus.PAUSED: LifecyclePhase.ATTENTION,
    LoopStatus.STAGNANT: LifecyclePhase.ATTENTION,
    LoopStatus.BLOCKED: LifecyclePhase.ATTENTION,
    LoopStatus.NEEDS_INPUT: LifecyclePhase.ATTENTION,
    LoopStatus.COMPLETE: LifecyclePhase.ENDED,
    LoopStatus.FAILED: LifecyclePhase.ENDED,
    LoopStatus.STOPPED: LifecyclePhase.ENDED,
}

#: Loop states that have stopped producing, so an end timestamp belongs on arrival. This is the
#: set ``store.update_status`` stamps ``completed_at`` for; it was an anonymous inline tuple that
#: happened to disagree with ``TERMINAL_STATUSES`` about ``FAILED`` with nothing naming why.
ENDED_STATUSES: frozenset[LoopStatus] = frozenset(
    status for status, phase in LOOP_PHASES.items() if phase is LifecyclePhase.ENDED
)

#: Ended loop states a loop may still LEAVE — the one place ``failed`` stops meaning what it
#: means for a run, stated rather than implied. A loop is a CAMPAIGN of attempts, so a dead
#: worker ends the attempt and not the campaign: ``ACTION_SOURCE_STATES["resume"]`` accepts
#: ``FAILED`` and re-arms the same loop. A run is ONE attempt, so its counterpart
#: (`workflows.models:RESUMABLE_ENDED_RUN_STATUSES`) is empty and its ``failed`` IS terminal.
#: Moving ``FAILED`` out of here is therefore not a tidy-up — it withdraws resumability from
#: every failed loop, and `tests/test_lifecycle_phase_vocabulary.py` fails when it is tried.
RESUMABLE_ENDED_STATUSES: frozenset[LoopStatus] = frozenset({LoopStatus.FAILED})

#: Terminal = ended and not resumable. Nothing may transition OUT of these (``store``'s guard).
TERMINAL_STATUSES: frozenset[LoopStatus] = ENDED_STATUSES - RESUMABLE_ENDED_STATUSES

#: States that count as "an active loop" — used for list filters + active-count badges. Derived
#: as ``ACTIVE | ATTENTION``, which is *why* ``FAILED`` is absent: a failed loop is resumable but
#: has no armed worker, so it must not inflate the badge. That used to be a hand-written comment
#: on a hand-written literal; it is now a consequence of ``FAILED`` being ``ENDED``.
ACTIVE_STATUSES: frozenset[LoopStatus] = frozenset(
    status
    for status, phase in LOOP_PHASES.items()
    if phase in (LifecyclePhase.ACTIVE, LifecyclePhase.ATTENTION)
)

#: Pre-launch states whose spec is still editable (no worker has run yet). Mirrors the former
#: code store's editable-spec gate; the union covers every kind's intake.
PRELAUNCH_STATUSES: frozenset[LoopStatus] = frozenset(
    status for status, phase in LOOP_PHASES.items() if phase is LifecyclePhase.PRELAUNCH
)

# Which action a status can be the SOURCE of (the lifecycle transition guard the
# HTTP action handler enforces). resume is allowed from any attention/paused state;
# stop from anything not already terminal; pause only while running.
#: What ``stop`` may be called FROM. A superset of :data:`ACTIVE_STATUSES` by exactly the two
#: pre-launch states, and deliberately its own name rather than a widened ``ACTIVE_STATUSES``:
#: that set also drives "active loop" list filters and badge counts, where a loop still in intake
#: is not yet active and must not be counted as one.
#:
#: `PP-16`: the union of the four action rows omitted ``INTAKE`` and ``PLANNING``, so a loop whose
#: classifier or planner died had **no available action at all** and ``DELETE`` was its only exit —
#: which discards the record instead of terminating it. ``stop`` is the right home because
#: ``STOPPED`` is terminal and needs no worker to reach: ``manager.stop`` tears down whatever is
#: armed (``_teardown`` no-ops when nothing is) and ``store.update_status`` refuses only
#: transitions OUT of a terminal state, so both pre-launch states could always reach it — the guard
#: was the only thing in the way.
STOPPABLE_STATUSES: frozenset[LoopStatus] = ACTIVE_STATUSES | frozenset(
    {LoopStatus.INTAKE, LoopStatus.PLANNING}
)

ACTION_SOURCE_STATES: dict[str, frozenset[LoopStatus]] = {
    "start": frozenset({LoopStatus.READY, LoopStatus.REVIEW}),
    "pause": frozenset({LoopStatus.RUNNING}),
    "resume": frozenset(
        {
            LoopStatus.PAUSED,
            LoopStatus.STAGNANT,
            LoopStatus.BLOCKED,
            LoopStatus.NEEDS_INPUT,
            LoopStatus.FAILED,
        }
    ),
    "stop": STOPPABLE_STATUSES,
}


@dataclass
class Loop:
    """One autonomous loop of any kind.

    Top-level fields are the SHARED spine (identity, the worker binding, lifecycle,
    timing, project scope, capabilities). ``kind_config`` carries everything
    type-specific that only the kind strategy interprets — so the store/manager/
    watchdog stay kind-agnostic and a new kind adds a strategy, not entity columns.
    """

    id: str
    name: str
    kind: str  # LoopKind value
    task: str  # the user's free-text goal/task

    # A loop ALWAYS belongs to a project (its context + workspace scope). The
    # composer/Projects surface resolves or auto-creates one at intake.
    project_id: str = ""

    summary: str = ""  # one-line planner restatement
    intake_rigor: str = "auto"  # auto | thorough | grill | minimal

    # ── the phased execution plan (shared shape across kinds) ──
    # Each phase: {phase/stage, title, objective, exit_criteria: [str], deliverable,
    # task_list_name, agent_name, skill_ids, workflow_ids, tasks}. A kind names its
    # phases its own way (goal sub-goals, code SDLC stages, design steps) but the
    # store/watchdog treat the plan + per-phase status map uniformly.
    plan: list[dict] = field(default_factory=list)
    phase_status: dict = field(default_factory=dict)  # {phase_key: pending|active|done}

    # ── the worker binding (how the agent runs) ──
    execution: str = "solo"  # solo | multi_agent
    agent: str = ""  # worker agent (default applied at launch)
    model: str = ""  # optional per-loop model override
    provider: str = ""
    provider_agent: str = ""
    reasoning_effort: str = ""
    roster: list[dict] = field(default_factory=list)  # multi-agent personas
    strategy_id: str = "orchestrator"  # orchestration method
    strategy_config: dict = field(default_factory=dict)
    skill_ids: list[str] = field(default_factory=list)  # always-on baseline capabilities
    workflow_ids: list[str] = field(default_factory=list)

    # ── workspace + run controls ──
    workspace_dir: str = ""  # validated abs dir; "" = use project context dir
    # Scratch-workspace lifecycle (auto-campaign-scratch-workspace): when True, the
    # loop's own dir (config_dir()/loop/<id>/) is treated as disposable scratch and
    # is torn down automatically once the loop reaches a terminal state — UNLESS the
    # user graduates it first (saves the deliverable as a permanent artifact). Off by
    # default: a completed loop's findings/report persist (never auto-delete a user's
    # work without opt-in). External workspace_dir bindings are NEVER auto-torn-down.
    auto_teardown_on_complete: bool = False
    attended: bool = False
    autopilot: bool = True  # system drives phases vs user queues (code-ish)
    max_cycles: int = 30  # 0 = uncapped
    idle_secs: int = 120
    success_criteria: str | None = None  # overall definition of done

    # ── kind-specific config (owned + interpreted by the kind strategy) ──
    # goal: {goal_type, granularity, sub_goals, deliverables, scope, rubric,
    #        ratchet_mode, verify_command}; code: {entry_stage, project_kind,
    #        verify_command, test_command, queued_task_ids}; design: {tokens,
    #        targets, exports}; general: {verify_command?}.
    kind_config: dict = field(default_factory=dict)

    # ── lifecycle + timing (shared) ──
    status: str = LoopStatus.READY.value
    created_at: float = 0.0
    started_at: float | None = None  # start of the CURRENT running stretch (reset each resume)
    completed_at: float | None = None
    elapsed_seconds: float = 0.0  # banked running time from PRIOR stretches (excludes pauses)
    # NO `total_cycles` here. It was a stored copy of the ledger's `step_completed` count, and
    # PP-16 seam 4a retired it: ask `store.cycles_completed(id)` (or `len(get_findings(id))` when
    # you already hold the projection). The redacted views still PUBLISH `total_cycles`, derived —
    # the API shape is unchanged, the second source of truth is gone.
    error_message: str | None = None

    # ── integration links (shared) ──
    tasks_project_id: str = ""  # backing Tasks Project id
    task_list_ids: dict = field(default_factory=dict)  # {phase_key: task_list_id}
    linked_task_ids: list[str] = field(default_factory=list)  # decomposed Tasks (flat)
    session_key: str = ""  # the worker session (loop-<id>)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Loop":
        """Build a Loop from a dict, ignoring unknown keys (forward-compatible reads)."""
        allowed = {f for f in cls.__dataclass_fields__}  # noqa: C416
        return cls(**{k: v for k, v in data.items() if k in allowed})


def finding_content(finding: dict, *, limit: int = 6000) -> str:
    """The cycle's reported content for scoring — the SINGLE canonical extraction the
    done-ness judge + ratchet both use, so they always evaluate the same text.
    Precedence: evidence → summary → content → note (richest first). A divergent
    precedence here caused false-regression pauses (ratchet scored a terse summary while
    the judge scored rich evidence from the same finding)."""
    text = (
        finding.get("evidence")
        or finding.get("summary")
        or finding.get("content")
        or finding.get("note")
        or ""
    )
    return str(text)[:limit]


def effective_dir(loop: "Loop") -> str:
    """The directory a loop's work actually lives in — the SINGLE resolver every
    ground-truth check uses so the supervisor reads the same place the worker writes.

    Mirrors the worker session's own cwd resolution, in the same precedence:
      1. ``workspace_dir`` — an explicitly bound codebase (brownfield code, or any loop
         pointed at a real directory);
      2. a GREENFIELD code loop's own ``loop_dir`` — a code loop with NO bound workspace
         operates FROM its files dir: the brief tells the worker "(none — operate from the
         project files dir)" and the cycle_nudge qualifies every path with the loop dir, so
         a greenfield deliverable (e.g. slugify.py) lands there, NOT the shared workspace
         root. Only the CODE kind is directed this way — goal/general keep only engine files
         (brief/findings/FINDINGS.md) in the loop dir and write their deliverable to the
         project/workspace, so this tier is code-only to avoid mis-pointing them. Missing
         this tier hard-failed the code deliverable gate forever — the supervisor looked in
         workspace_root() while the worker wrote to the loop dir, so `_resolve_deliverable`
         returned (True, None) → "stage held" on a genuinely-complete stage (observed live:
         greenfield loop 07d5a0d0, 15/15 tests green, stage held across cycles);
      3. the containing project's shared context dir (``workspace_dir == ""`` means "use
         the project context dir" per the Loop model);
      4. ``workspace_root()`` — the default session workspace, where a loop with NO bound
         workspace AND no project actually runs (the common open-ended-goal case; observed
         live: goal 0fef190e wrote its deliverable to the workspace root).

    Always resolves to a real dir in practice (tier 4 never empty), so a ground-truth read
    has somewhere to look rather than silently no-opping on an empty path."""
    ws = (loop.workspace_dir or "").strip()
    if ws:
        return ws
    # Greenfield code (no bound workspace) operates from its own loop dir — mirror that so
    # the supervisor's deliverable gate reads where the worker actually wrote.
    if loop.kind == "code":
        try:
            from personalclaw.loop import store

            d = store.loop_dir(loop.id)
            if d is not None and d.is_dir():
                return str(d)
        except Exception:
            pass
    if loop.project_id:
        try:
            from personalclaw import projects as projects_svc

            ctx = (projects_svc.context_dir(loop.project_id) or "").strip()
            if ctx:
                return ctx
        except Exception:
            pass
    try:
        from personalclaw.config.loader import workspace_root

        return str(workspace_root())
    except Exception:
        return ""
