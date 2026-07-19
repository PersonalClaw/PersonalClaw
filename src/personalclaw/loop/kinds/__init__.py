"""Per-kind loop strategies — where subject-matter expertise lives.

The unified engine (store/manager/watchdog) is kind-agnostic. Everything that
varies by :class:`personalclaw.loop.loop.LoopKind` — how a task is classified,
how the problem is phased, how done-ness is gated, which capabilities load, how
the worker brief is framed, which planning-walkthrough config is used — is
supplied by a :class:`LoopKindStrategy` registered here.

A new kind = a new strategy module + one ``register()`` call. No engine edits, no
entity columns. This is the seam that lets all kinds share loop + project features
while keeping rich, type-specific behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from personalclaw.loop.loop import Loop


@dataclass
class CycleContext:
    """The watchdog capabilities a kind's per-cycle orchestration hook may use,
    handed in so the strategy never imports the watchdog (no cycle, testable).

    A kind whose done-ness is more than a point-in-time signal — code advances
    SDLC stages + provisions tasks each cycle, design advances design steps — uses
    ``on_new_cycle`` (below) to run that orchestration and report completion. Most
    kinds (goal/general) don't need it; their ``is_done_signal`` suffices.
    """

    svc: Any                                  # the AutoNudgeService (worker loops)
    state: Any                                # the dashboard state (sessions, notify)
    publish: Callable[[str, str, Any], None]  # publish(loop_id, event, data) → per-loop SSE
    complete: Callable[[str, str], Awaitable[None]]  # complete(loop_id, reason) — terminal


@runtime_checkable
class LoopKindStrategy(Protocol):
    """The behavior contract for one loop kind. The engine calls these; the
    strategy supplies the subject-matter expertise.

    Methods are deliberately small + pure where possible so each kind is unit-
    testable in isolation and the engine never branches on ``loop.kind``.
    """

    #: The LoopKind value this strategy serves (e.g. ``"goal"``).
    kind: str

    #: Human label for the kind slider (e.g. "Goal", "Code").
    label: str

    #: One-line description shown under the kind in the Loop composer.
    description: str

    #: Whether this kind needs a bound workspace directory to run (code/design:
    #: typically yes for brownfield; goal/general: optional).
    wants_workspace: bool

    #: The default worker agent name when the loop doesn't pin one (code → the
    #: coder agent; goal/general/design → the loop-worker agent).
    default_agent: str

    #: Whether this kind is TASK-DRIVEN — provisions a backing Tasks Project +
    #: per-phase TaskLists (+ seeds the planner's tasks) at launch. code/design: yes.
    #: goal/general: no (sub-goals become Tasks only via an explicit user decompose —
    #: auto-provisioning would spawn an unwanted Project + empty lists). Absent ⇒ False
    #: (the manager reads it via getattr, so a kind need not declare it).
    provisions_tasks: bool

    def default_kind_config(self) -> dict:
        """The initial ``kind_config`` for a freshly-created loop of this kind
        (goal_type/granularity, entry_stage, design targets, …)."""
        ...

    def phase_key(self, phase: dict) -> str:
        """The stable key for a plan phase — what ``phase_status`` /
        ``task_list_ids`` are keyed by. Goal/general: title; code: stage-or-title;
        design: step id. Must match how the kind's planner emits phases."""
        ...

    async def is_done_signal(self, loop: Loop, findings: list[dict]) -> bool | None:
        """The kind's done-ness read for the CURRENT state, produced by something
        other than the worker (verify command, judge verdict, all-phases-gated).
        ``True`` = complete, ``False`` = keep going, ``None`` = can't tell (defer).
        The watchdog owns the lifecycle decision; this only supplies the signal.

        Async: the supervisor's own checks (running a verify command, a judge
        subagent pass) are I/O. The watchdog awaits it each cycle."""
        ...

    async def classify(self, task: str, ask, *, skills: list | None = None,
                       workflows: list | None = None, agents: list | None = None) -> dict:
        """The intake brain — analyze ``task`` and return a NORMALIZED classification
        the composer/Plan-Review consumes + the create body can fold into a Loop:
        ``{title, summary, classified, intake_rigor, execution, roster, strategy_id,
        clarifying_questions, suggested_skill_ids, suggested_workflow_ids,
        marketplace_suggestions, plan: [...], kind_config: {...}}``. ``ask`` is the
        one-shot LLM callable; the catalogs are the installed capabilities the planner
        may rank/bind. Each kind wraps its own classifier (goal type / SDLC stage /
        design steps); never raises — returns safe defaults flagged classified=False."""
        ...

    def build_brief(self, loop: Loop, context_dir: str = "") -> str:
        """The full ``brief.md`` body the worker reads each cycle — the durable
        spec (task, plan, workspace, DoD, capabilities) in the kind's framing.
        PURE: returns text; the manager owns the file write + dir resolution and
        passes the already-resolved project ``context_dir`` (or "") so the strategy
        stays free of store/projects coupling and is unit-testable."""
        ...

    def deliverable_name(self, loop: Loop) -> str:
        """OPTIONAL — the filename of the on-disk document deliverable this loop's
        worker maintains (goal open_ended → REPORT.md, monitor → MONITOR_LOG.md),
        or "" if the kind has no document output (verifiable/code: the code/check IS
        the output). On completion the watchdog surfaces it as a file-backed artifact
        in the cockpit Outputs panel."""
        ...

    def launch_blocker(self, loop: Loop) -> str | None:
        """OPTIONAL — a launch-time re-validation: a user-facing reason this loop
        cannot ``start`` yet (e.g. a brownfield code loop with no bound workspace),
        or None to allow. The engine enforces it generically on a fresh start (not
        resume), so the kind-specific precondition stays in the strategy."""
        ...

    def walkthrough(self):
        """OPTIONAL — the kind's stepwise planning walkthrough delegate (a
        ``loop.plan_walkthrough.Walkthrough``), or absent/None if the kind has no
        gated planning walkthrough (general/design today). Goal returns a fixed-step
        delegate; code returns a dynamic-design-pass one. The plan-* routes 404 when
        a kind has no walkthrough."""
        ...

    def cycle_nudge(self, loop: Loop, loop_dir: str) -> str:
        """The per-cycle trigger message the manager fires at the worker. The
        methodology lives in the agent system prompt / skill; this restates the
        hard, non-negotiable per-cycle contract (read status/brief/guidance → do
        ONE step → MUST write a finding) in the kind's own framing (goal sub-goals,
        code stage directive, …). ``loop_dir`` is the loop's file dir (where
        status.json / brief.md / findings/ live), path-qualified so a brownfield
        worker whose cwd is the bound workspace still finds them."""
        ...


async def run_cycle_hook(strategy, loop: Loop, findings: list, ctx: CycleContext) -> bool | None:
    """Run a kind's optional per-cycle orchestration hook, if it defines one.

    A kind with multi-cycle orchestration (code: advance the SDLC stage, run the
    stage gate, provision/queue tasks; design: advance the design step) implements
    ``async on_new_cycle(loop, findings, ctx) -> bool`` — return True iff the loop
    COMPLETED this cycle. The watchdog calls this on each new finding BEFORE its
    own budget/stall checks: a hook that returns a bool owns the cycle's done-ness
    (the watchdog skips its generic is_done_signal path); returning ``None`` (no
    hook) means "this kind has no per-cycle orchestration — fall through to the
    generic signal + budget". Never raises into the poll loop — errors → None."""
    hook = getattr(strategy, "on_new_cycle", None)
    if hook is None:
        return None
    try:
        return bool(await hook(loop, findings, ctx))
    except Exception:  # pragma: no cover - defensive; a kind bug must not wedge the poll
        import logging
        logging.getLogger(__name__).warning(
            "loop kind %s on_new_cycle errored", getattr(strategy, "kind", "?"), exc_info=True)
        return False


_REGISTRY: dict[str, LoopKindStrategy] = {}


def register(strategy: LoopKindStrategy) -> None:
    """Register a kind strategy (idempotent — re-register overwrites, so a reload
    in tests/dev is safe)."""
    _REGISTRY[strategy.kind] = strategy


def get(kind: str) -> LoopKindStrategy:
    """The strategy for ``kind``. Raises KeyError if the kind isn't registered —
    an unknown kind is a programmer error (the entity validates kind on create)."""
    return _REGISTRY[kind]


def get_or_none(kind: str) -> LoopKindStrategy | None:
    return _REGISTRY.get(kind)


def registered_kinds() -> list[str]:
    """Kinds with a registered strategy, in registration order."""
    return list(_REGISTRY)


def ensure_loaded() -> None:
    """Import the bundled kind strategies so they self-register. Idempotent; called
    by the engine before it dispatches. Kept lazy to avoid import cycles (a kind
    module may import engine helpers)."""
    from personalclaw.loop.kinds import (  # noqa: F401
        design as _design,
        general as _general,
        goal as _goal,
        research as _research,
        sdlc as _code,
    )


# A late-binding hook so a bundled/extension kind can register without the engine
# importing it directly (mirrors the provider-registry self-registration pattern).
_DEFERRED: list[Callable[[], None]] = []
