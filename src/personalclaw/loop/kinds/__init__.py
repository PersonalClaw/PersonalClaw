"""Per-kind loop strategies — where subject-matter expertise lives.

The unified engine (store/manager/watchdog) is kind-agnostic. Everything that
varies by :class:`personalclaw.loop.loop.LoopKind` — how a task is classified,
how the problem is phased, which capabilities load, how the worker brief is
framed, which planning-walkthrough config is used — is supplied by a
:class:`LoopKindStrategy` registered here.

A new kind = a new strategy module + one ``register()`` call. No engine edits, no
entity columns. This is the seam that lets all kinds share loop + project features
while keeping rich, type-specific behavior.

**The SUPERVISOR is no longer part of that seam**. How done-ness
is gated, whether a budget stop is genuine, and whether the stall signal applies
are DECLARED in
:data:`personalclaw.workflows.supervisor_policy.KIND_CONVERGENCE` and evaluated by
the one kind-agnostic evaluator in :mod:`personalclaw.loop.supervisor`. A kind may
not supply a convergence mechanism in Python — the closed
:data:`~personalclaw.workflows.supervisor_policy.DONE_SIGNALS` vocabulary is the
whole contract. What remains here is intake, worker framing and projection keys.
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
    kinds (goal/general) don't need it; their declared done-signal suffices.
    """

    svc: Any  # the AutoNudgeService (worker loops)
    state: Any  # the dashboard state (sessions, notify)
    publish: Callable[[str, str, Any], None]  # publish(loop_id, event, data) → per-loop SSE
    complete: Callable[[str, str], Awaitable[None]]  # complete(loop_id, reason) — terminal


#: The plan-row fields a phase's stable key is read from, in priority order — ONE
#: vocabulary shared by every kind and by the frontend.
#:
#: ``stage`` is the phase id every planner emits (``code_plan_briefs`` and
#: ``design_plan_briefs`` both ask their model for ``{"stage", "title", …}``, and
#: ``LoopPhase.stage`` is the field the wire type declares). ``title`` is the fallback for
#: a genuinely stage-less row — goal sub-goals, general phases, and a titled-but-stageless
#: code row.
#:
#: This tuple exists so the field NAMES are stated once instead of being re-spelled at each
#: reader. Design used to spell the same id ``step``, which no reader outside the design
#: kind knew: the writer keyed ``phase_status`` by ``step`` and the frontend's fold keyed it
#: by ``stage``, so every design stage rendered ``todo`` forever under a header counter that
#: read ``3/5 stages`` (issue 494). ``web/src/pages/loops/loopPhases.ts`` carries the
#: frontend's copy of this list and ``tests/test_loop_phase_key_one_owner.py`` asserts the
#: two agree, so renaming a field on either side reds a test instead of a screen.
PHASE_KEY_FIELDS: tuple[str, ...] = ("stage", "title")


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
    @property
    def provisions_tasks(self) -> bool:
        return False

    #: Whether this kind's engine actually ADVANCES ``phase_status`` — i.e. whether a
    #: plan phase ever reaches ``done`` for a loop of this kind. Only a kind with an
    #: ``on_new_cycle`` hook that calls ``store.set_phase_status`` may declare True
    #: (today: code + design). Absent ⇒ False (read via getattr, so a kind need not
    #: declare it), and ``tests/test_loop_kinds.py`` asserts each declaration against
    #: the kind module's real ``set_phase_status`` call sites.
    #:
    #: This exists because a plan[] is NOT a progress tracker. Every kind may carry a
    #: descriptive ``plan`` — research plans five objectives, goal lists sub-goals — but
    #: only these two maintain per-phase done-state. The frontend used to decide with
    #: ``kind !== 'goal'``, so a COMPLETED 20-cycle research run rendered "0/5 stages"
    #: (#448): the reader's phased set was three kinds wider than the writer set, and
    #: nothing tied the two enumerations together. The declaration travels to the FE on
    #: the redacted loop view as ``phase_tracked``, so there is one source of truth.
    @property
    def tracks_phases(self) -> bool:
        return False

    def default_kind_config(self) -> dict:
        """The initial ``kind_config`` for a freshly-created loop of this kind
        (goal_type/granularity, entry_stage, design targets, …)."""
        ...

    def phase_key(self, phase: dict) -> str:
        """The stable key for a plan phase — what ``phase_status`` / ``task_list_ids`` are
        keyed by, and what the frontend looks a phase's state up by.

        ONE implementation for every kind: the first non-empty :data:`PHASE_KEY_FIELDS`
        value. A kind MUST NOT override this. Four hand-written copies used to say three
        different things (goal/general ``title``, code ``stage``-or-title, design
        ``step``-or-title) and nothing checked that any reader agreed — which is how
        design's writer and the frontend's reader ended up keyed on different words. A
        kind that needs a different phase id changes its planner to emit ``stage``, not
        this function; ``tests/test_loop_phase_key_one_owner.py`` reds on a new override.
        """
        for field in PHASE_KEY_FIELDS:
            value = str(phase.get(field, "") or "").strip()
            if value:
                return value
        return ""

    async def classify(
        self,
        task: str,
        ask,
        *,
        skills: list | None = None,
        workflows: list | None = None,
        agents: list | None = None,
    ) -> dict:
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
        return ""

    def launch_blocker(self, loop: Loop) -> str | None:
        """OPTIONAL — a launch-time re-validation: a user-facing reason this loop
        cannot ``start`` yet (e.g. a brownfield code loop with no bound workspace),
        or None to allow. The engine enforces it generically on a fresh start (not
        resume), so the kind-specific precondition stays in the strategy."""
        return None

    def walkthrough(self):
        """OPTIONAL — the kind's stepwise planning walkthrough delegate (a
        ``loop.plan_walkthrough.Walkthrough``), or absent/None if the kind has no
        gated planning walkthrough (general/design today). Goal returns a fixed-step
        delegate; code returns a dynamic-design-pass one. The plan-* routes 404 when
        a kind has no walkthrough."""
        return None

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
    (the watchdog skips the declared done-signal path); returning ``None`` (no
    hook) means "this kind has no per-cycle orchestration — fall through to the
    policy's declared signal + budget". Never raises into the poll loop — errors → None."""
    hook = getattr(strategy, "on_new_cycle", None)
    if hook is None:
        return None
    try:
        return bool(await hook(loop, findings, ctx))
    except Exception:  # pragma: no cover - defensive; a kind bug must not wedge the poll
        import logging

        logging.getLogger(__name__).warning(
            "loop kind %s on_new_cycle errored", getattr(strategy, "kind", "?"), exc_info=True
        )
        return False


async def run_schedule_hook(strategy, loop: Loop, ctx: CycleContext) -> bool:
    """Run a kind's optional per-poll scheduler, if it defines one.

    A kind that runs work of its own beside the loop's worker (code: parallel task workers in
    worktrees) implements ``async schedule(loop, ctx) -> bool``; the watchdog asks it on every
    poll of a running loop, whether or not a finding landed, and it returns True iff the loop
    paused for its owner. A kind without one is left alone. Never raises into the poll loop."""
    hook = getattr(strategy, "schedule", None)
    if hook is None:
        return False
    try:
        return bool(await hook(loop, ctx))
    except Exception:  # pragma: no cover - defensive; a kind bug must not wedge the poll
        import logging

        logging.getLogger(__name__).warning(
            "loop kind %s schedule errored", getattr(strategy, "kind", "?"), exc_info=True
        )
        return False


def attendedness_lines(loop: Loop, *, subject: str = "task") -> list[str]:
    """The worker brief's words for the loop's Mode: who answers its calls, and whether it may ask.

    One wording for every kind, and the one the loop's own posture enforces (``loop.posture``):
    an Attended worker's calls are answered by the user, an Unattended worker's run on the loop's
    standing grant, and a Mode that cannot be read is told what its sessions then do: ask.
    *subject* names what the loop works on ("task", "goal") in the question rule."""
    from personalclaw.loop import posture

    if posture.of(loop).asks:
        return [
            "",
            "**Attended:** the user is watching this loop. Your tool calls ask for their "
            "approval the way a chat's do (a shell command, a file edit), and one they decline "
            "comes back refused: do not retry it; take another way, or ask. If the "
            f"{subject} is genuinely ambiguous in a way that would change your direction, you "
            'MAY write {"question", "why"} to questions.json in the loop\'s own folder and end '
            "the turn: the loop pauses for the user. Keep the bar high; otherwise proceed on a "
            "best-reasoned assumption and record it.",
        ]
    return [
        "",
        "**Unattended:** nobody is watching, so your calls run without asking, inside the "
        "user's safety rules. Do NOT pause to ask the user: investigate ambiguities yourself, "
        "pick the best-reasoned answer, record the assumption in your finding, and proceed. "
        "Never write questions.json in this mode.",
    ]


def workspace_rules_lines() -> list[str]:
    """The worker brief's words on how it treats its owner's repository and machine: commits are
    made as git is configured to commit there (the loop merges them under that name too), and
    scratch work goes in a temporary folder of the owner's, not in a shared one or the checkout."""
    return [
        "",
        "**Commits:** commit as git is configured here. Never set a name or email for a commit "
        "yourself (no `-c user.name`, `--author` or `GIT_AUTHOR_*`): if git says it has none, "
        "stop and say so — the loop asks its owner for one.",
        "",
        "**Scratch work** (a throwaway copy, a test run's output) goes in a folder `mktemp -d` "
        "makes, which is your temporary folder; remove it when you are done. Never write under "
        "/tmp by name, and never leave scratch files in the checkout.",
    ]


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
    from personalclaw.loop.kinds import design as _design  # noqa: F401
    from personalclaw.loop.kinds import general as _general
    from personalclaw.loop.kinds import goal as _goal
    from personalclaw.loop.kinds import research as _research
    from personalclaw.loop.kinds import sdlc as _code


@dataclass(frozen=True)
class WorkerTurn:
    """What a loop's worker is given on its upcoming turn, by its loop's kind: the loop's own
    skills and workflows (``turn_capabilities``: the active phase's or stage's, with the always-on
    baseline), and the directive put ahead of the turn's nudge (``turn_directive``)."""

    skill_ids: tuple[str, ...] = ()
    workflow_ids: tuple[str, ...] = ()
    directive: str = ""


def worker_turn(session_key: str) -> WorkerTurn:
    """What the worker whose session is *session_key* (``loop-<id>``, or ``loop-<id>-<task>`` for
    one of its parallel task workers) is given on its upcoming turn. Nothing for a session that is
    no loop's worker, a loop that is gone, or a kind with no strategy registered.

    The one reading of it: the chat runner loads the skills and workflows on the turn and puts the
    directive ahead of its nudge, and the skill tools let the turn use those skills, which are the
    loop's own whatever its agent's skill list holds (``agents.skill_list``).
    """
    from personalclaw.loop import store
    from personalclaw.loop.manager import worker_ids

    loop_id, _task_id = worker_ids(session_key or "")
    loop = store.get(loop_id) if loop_id else None
    if loop is None:
        return WorkerTurn()
    ensure_loaded()
    strategy = get_or_none(loop.kind)
    if strategy is None:
        return WorkerTurn()
    capabilities = getattr(strategy, "turn_capabilities", None)
    skills, workflows = capabilities(loop) if capabilities is not None else ([], [])
    directive = getattr(strategy, "turn_directive", None)
    return WorkerTurn(
        skill_ids=tuple(skills),
        workflow_ids=tuple(workflows),
        directive=(directive(loop) if directive is not None else "") or "",
    )


# A late-binding hook so a bundled/extension kind can register without the engine
# importing it directly (mirrors the provider-registry self-registration pattern).
_DEFERRED: list[Callable[[], None]] = []
