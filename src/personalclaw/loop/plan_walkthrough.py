"""The unified stepwise planning walkthrough — kind-agnostic orchestration.

Both the goal and code kinds plan their work as a **gated, stepwise walkthrough**
over a :class:`personalclaw.planning.session.PlanSession`: the planner agent
produces one step's artifact, the loop BLOCKS on the user's review gate (approve /
comment / edit), and only then advances. The difference is per-kind and lives in a
:class:`Walkthrough` delegate the strategy supplies:

  - **fixed** (goal) — a stable ordered step list (intent → sub-goals → quorum →
    execution_plan); no design pass.
  - **dynamic** (code) — a first *design pass* in which the planner investigates
    the target and decides the ordered step list itself, then a step pass per step.

This module owns the SHARED state machine (seed → design → step → gate → finalize)
on the unified store/runner; the delegate owns the kind-specific briefs, parsers,
and the projection of approved artifacts into the loop spec. A kind without a
walkthrough (general/design today) returns ``None`` from ``Loop`` strategy's
``walkthrough()`` and the plan routes 404 for it.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from personalclaw.loop import files as loop_files
from personalclaw.loop import store
from personalclaw.loop.loop import PRELAUNCH_STATUSES, LoopStatus
from personalclaw.planning import session as PS
from personalclaw.planning.session import PlanSession, PlanStep

if TYPE_CHECKING:
    from personalclaw.planning.runner import PlannerPass

logger = logging.getLogger(__name__)

# The files the planner writes, one per pass, in the loop's own folder: it works in the
# bound workspace, and its briefs name the absolute path, so none of them lands in the
# user's tree. Distinct names so a stale design-pass file is never mistaken for a step
# artifact.
STEPS_SENTINEL = "plan_steps.json"
ARTIFACT_SENTINEL = "step_artifact.json"


@runtime_checkable
class Walkthrough(Protocol):
    """The kind-specific pieces of a planning walkthrough. Pure (no store/runner
    coupling) so each kind's planning is unit-testable; the orchestrator below wires
    it to the shared state machine + planner runner."""

    #: The planner agent that drives this kind's walkthrough.
    planner_agent: str

    #: ``"fixed"`` (stable step list, no design pass) or ``"dynamic"`` (planner
    #: designs the step list first).
    step_mode: str

    def default_steps(self) -> list[dict]:
        """FIXED mode only — the stable ordered step list ({kind,title,objective})."""
        ...

    def build_design_brief(
        self,
        task: str,
        workspace_dir: str,
        design_inputs: list[dict] | None = None,
        *,
        out_dir: str,
    ) -> str:
        """DYNAMIC mode only — the pass-1 brief: investigate + design the step list,
        written to ``plan_steps.json`` in ``out_dir`` (the loop's own folder).
        ``design_inputs`` (design kind only) are the user's multi-modal reference
        inputs to work through; other kinds ignore it."""
        ...

    def parse_steps_sentinel(self, raw: str) -> tuple[str, list[dict]] | None:
        """DYNAMIC mode only — parse pass-1 output → ``(summary, [step dicts])``."""
        ...

    def build_step_brief(
        self,
        task: str,
        step: PlanStep,
        *,
        approved: list[PlanStep],
        workspace_dir: str,
        out_dir: str,
    ) -> str:
        """The pass-2 brief: produce ONE step's artifact, given the approved prior
        artifacts + the kind's artifact contract, written to ``step_artifact.json`` in
        ``out_dir`` (the loop's own folder)."""
        ...

    def parse_artifact_sentinel(self, raw: str) -> dict | None:
        """Parse a step's artifact JSON (tolerant of code-fenced / prose-wrapped)."""
        ...

    def project_to_spec(self, session: PlanSession) -> dict:
        """Project the APPROVED artifacts into the UNIFIED loop spec fields
        (``plan`` / ``roster`` / ``execution`` / ``success_criteria`` / ``summary`` /
        ``kind_config``) — the same fields the kind's classify produced, so launch is
        identical whether the user classified or walked the plan."""
        ...


def _walkthrough_for(loop_id: str):
    """The walkthrough delegate for a loop's kind, or None if the kind has no
    stepwise planning walkthrough. Resolves the strategy (kinds are loaded lazily)."""
    from personalclaw.loop import kinds

    kinds.ensure_loaded()
    loop = store.get(loop_id)
    if loop is None:
        return None, None
    strat = kinds.get_or_none(loop.kind)
    factory = getattr(strat, "walkthrough", None) if strat else None
    if factory is None:
        return loop, None
    try:
        return loop, factory()
    except Exception:
        logger.debug("walkthrough() for kind %s failed", getattr(loop, "kind", "?"), exc_info=True)
        return loop, None


#: What every loop planner's session key starts with (:func:`planner_session_key`).
PLANNER_SESSION_PREFIX = "loop-plan-"


def planner_session_key(loop_id: str) -> str:
    """Hidden planner session key for a loop (distinct from the worker's)."""
    return f"{PLANNER_SESSION_PREFIX}{loop_id}"


def _still_planning(loop_id: str) -> bool:
    """Whether loop *loop_id* still wants its planner: it exists, and it has neither launched nor
    ended. No pass starts for one that has — a Stop or a delete landing between a pass and its
    retry used to have the retry arm the planner again, for a loop nobody would see it plan."""
    loop = store.get(loop_id)
    return loop is not None and LoopStatus(loop.status) in PRELAUNCH_STATUSES


def seed_steps(session: PlanSession, steps: list[dict]) -> PlanSession:
    """Populate a session's ordered steps with stable ids (``step-0``, …)."""
    session.steps = [
        PlanStep(
            id=f"step-{i}",
            kind=str(s.get("kind", "step")),
            title=str(s.get("title", "")) or f"Step {i + 1}",
            objective=str(s.get("objective", "")),
        )
        for i, s in enumerate(steps)
    ]
    return session


# ── orchestration: spawn the planner per pass, gate, persist ──


def _loop_folder(loop) -> str:
    """The loop's own folder: where the planner's files go, and the only place they are read."""
    return str(loop_files.loop_dir(loop.id) or "")


async def _run_pass(
    state, svc, loop, wt: Walkthrough, *, brief: str, sentinel: str, timeout_secs: int | None = None
) -> PlannerPass:
    """One planner pass via the shared runner, resolving the loop's primitives."""
    from personalclaw.planning import runner

    files_dir = _loop_folder(loop)
    return await runner.run_planner_pass(
        state,
        svc,
        session_key=planner_session_key(loop.id),
        agent_name=wt.planner_agent,
        workspace_dir=loop.workspace_dir or "",
        files_dir=files_dir,
        sentinel=sentinel,
        brief=brief,
        app="loops",
        model=getattr(loop, "model", ""),
        provider=getattr(loop, "provider", ""),
        provider_agent=getattr(loop, "provider_agent", ""),
        reasoning_effort=getattr(loop, "reasoning_effort", ""),
        stop_sentinel_name=loop_files.STOP_SENTINEL,
        timeout_secs=timeout_secs,
        # Both walkthrough files, whichever pass is active: a step pass
        # (step_artifact.json) routinely has the planner re-create the decomposition
        # file (plan_steps.json) while it works.
        extra_sentinels=(STEPS_SENTINEL, ARTIFACT_SENTINEL),
    )


def _read_problem(text: str, *, needs: str) -> tuple[str, str]:
    """What is wrong with a file the planner wrote that its kind could not use, in words the
    planner and its owner can both act on: where its JSON breaks (and the file's own words
    there), or what it lacks. ``needs`` names what a parseable object was missing (the kind's
    parser said no to it). Returns ``(problem, the words where it breaks or "")``."""
    start = text.find("{")
    if start < 0:
        return "it holds no JSON object", ""
    try:
        value, _end = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError as e:
        line = (text.splitlines() or [""])[e.lineno - 1] if e.lineno > 0 else ""
        where = f"it is not valid JSON: {e.msg} at line {e.lineno}, column {e.colno}"
        return where, _excerpt(line, e.colno)
    if not isinstance(value, dict):
        return "it holds JSON, but not one object", ""
    return f"it is valid JSON, but {needs}", ""


def _excerpt(line: str, col: int) -> str:
    """The words of *line* around 1-based column *col*: where a JSON error is, cut at word
    boundaries so it reads as the file's own text, with an ellipsis where it was cut."""
    at = col - 1
    lo, hi = max(0, at - 40), min(len(line), at + 20)
    if lo > 0 and (space := line.find(" ", lo, at)) >= 0:
        lo = space + 1
    if hi < len(line) and (space := line.rfind(" ", at + 1, hi)) > at:
        hi = space
    text = " ".join(line[lo:hi].split())
    return ("\u2026" if lo > 0 else "") + text + ("\u2026" if hi < len(line) else "")


def _no_draft_reason(result: PlannerPass, sentinel: str, *, needs: str) -> str:
    """Why a planner pass produced nothing usable, as the sentence its owner reads."""
    from personalclaw.planning import runner

    if result.ended == runner.WROTE:
        problem, near = _read_problem(result.text, needs=needs)
        # The file's own words go last and unquoted: a quote mark around text full of them
        # (and turned into a plain one on its way to a model) only blurs where it ends.
        return f"The planner wrote {sentinel}, but {problem}." + (
            f" Near there it reads: {near}" if near else ""
        )
    if result.ended == runner.TIMED_OUT:
        return (
            f"The planner ran out of time ({result.limit_secs / 60:g} minutes) before it "
            f"wrote {sentinel}."
        )
    if result.ended == runner.STOPPED:
        return f"The planner ended its turn without writing {sentinel}."
    return f"The planner pass failed before it wrote {sentinel}; the gateway log says why."


def _correction(result: PlannerPass, path: str, reason: str) -> str:
    """The line the retry adds to the brief: what was wrong with the last attempt, and the one
    thing to do about it. A planner that wrote the file is told what is wrong with what it wrote,
    never that it did not write it."""
    from personalclaw.planning import runner

    head = f"\n\n# CRITICAL — your previous attempt produced nothing usable. {reason}\n"
    if result.ended == runner.WROTE:
        return head + (
            f"Write the whole file `{path}` again with write_file, as ONE valid JSON object. "
            'Inside a JSON string, write a double quote as \\" and a line break as \\n. Keep '
            "the artifact's content as it was; only make it valid JSON."
        )
    if result.ended == runner.TIMED_OUT:
        return head + (
            "Do not start the investigation over: use what you have already found, and write "
            f"the artifact to `{path}` with write_file now."
        )
    return head + (
        f"You must persist the artifact by CALLING the write_file tool with the path `{path}` "
        "(that exact path: the loop's own folder, not the workspace). Do NOT paste the JSON, a "
        "diff, or a code block into your reply — only an actual write_file call creates the "
        "file we read. Re-emit the artifact now via write_file."
    )


#: What a step-list file lacked when it parsed as an object but the kind refused it.
_STEPS_NEEDS = 'it has no non-empty "steps" list'
#: Any JSON object is a step artifact, so a parsed one is never refused for its shape.
_ARTIFACT_NEEDS = "it is not a step artifact"


async def run_design_pass(state, svc, loop_id: str) -> PlanSession | None:
    """DYNAMIC pass-1 — investigate + design the ordered step list, seed + persist
    the session. Returns the seeded session, or None (records design_error so a
    re-entry surfaces an explicit Retry instead of silently re-spawning). Never raises."""
    import time as _time

    loop, wt = _walkthrough_for(loop_id)
    if loop is None or wt is None or not _still_planning(loop_id):
        return None
    try:
        store.update_status(loop_id, LoopStatus.PLANNING)
    except Exception:
        pass
    # Pass the loop's multi-modal design inputs (kind_config.design_inputs) so the
    # design delegate can instruct the planner to work through each (URL/image/…).
    # Goal/code delegates ignore the kwarg (fixed "" / task+workspace only).
    design_inputs = (
        (loop.kind_config or {}).get("design_inputs")
        if isinstance(loop.kind_config, dict)
        else None
    )
    result = await _run_pass(
        state,
        svc,
        loop,
        wt,
        brief=wt.build_design_brief(
            loop.task,
            loop.workspace_dir or "",
            design_inputs=design_inputs,
            out_dir=_loop_folder(loop),
        ),
        sentinel=STEPS_SENTINEL,
    )
    parsed = wt.parse_steps_sentinel(result.text)
    if not _still_planning(loop_id):
        return None  # stopped or deleted while the planner worked: nothing of it is kept
    if parsed is None:
        reason = _no_draft_reason(result, STEPS_SENTINEL, needs=_STEPS_NEEDS)
        logger.info("planning %s: the design pass produced no step list: %s", loop_id, reason)
        session = loop_files.read_plan_session(loop_id) or PlanSession(
            project_id=loop_id, created_at=_time.time()
        )
        session.design_error = f"{reason} Retry planning, or edit the task to be more concrete."
        loop_files.write_plan_session(session)
        return None
    _summary, steps = parsed
    session = loop_files.read_plan_session(loop_id) or PlanSession(
        project_id=loop_id, created_at=_time.time()
    )
    seed_steps(session, steps)
    session.design_error = ""
    loop_files.write_plan_session(session)
    return session


async def run_step_pass(state, svc, loop_id: str, step_id: str) -> PlanStep | None:
    """Produce the artifact for ONE step (current, or a re-draft after a comment).
    Marks the step running, runs the planner, opens the review gate, persists. Returns
    the updated step, or None if nothing usable was produced. Never raises."""
    loop, wt = _walkthrough_for(loop_id)
    session = loop_files.read_plan_session(loop_id)
    if loop is None or wt is None or session is None or not _still_planning(loop_id):
        return None
    step = next((s for s in session.steps if s.id == step_id), None)
    if step is None:
        return None
    # Why the step's last pass failed, when it did: the owner's Retry re-runs it, and the planner
    # is told what went wrong rather than handed the same brief that produced nothing.
    last_failure = step.error
    if step.status == PS.StepStatus.PENDING.value:
        PS.mark_running(session, step_id)
        loop_files.write_plan_session(session)

    approved = [s for s in session.steps if s.status == PS.StepStatus.APPROVED.value]
    artifact_path = os.path.join(_loop_folder(loop), ARTIFACT_SENTINEL)
    base_brief = wt.build_step_brief(
        loop.task,
        step,
        approved=approved,
        workspace_dir=loop.workspace_dir or "",
        out_dir=_loop_folder(loop),
    )
    if last_failure:
        base_brief += (
            f"\n\n# Your previous attempt at this step failed. {last_failure}\n"
            f"Write this step's artifact to `{artifact_path}` with write_file, as one valid "
            "JSON object."
        )
    result = await _run_pass(state, svc, loop, wt, sentinel=ARTIFACT_SENTINEL, brief=base_brief)
    artifact = wt.parse_artifact_sentinel(result.text)
    if not _still_planning(loop_id):
        return None  # stopped or deleted while the planner worked: no retry, nothing written
    if artifact is None:
        # Nothing usable came back: the planner narrated the artifact instead of writing it,
        # wrote a file that is not valid JSON, or ran out of time. Retry the pass ONCE, telling
        # it which — a planner that wrote the file and is told it never did writes the same
        # broken text again (observed: three identical writes, an unescaped quote each time).
        reason = _no_draft_reason(result, ARTIFACT_SENTINEL, needs=_ARTIFACT_NEEDS)
        logger.info(
            "planning %s %s: no usable artifact, retrying once: %s", loop_id, step_id, reason
        )
        result = await _run_pass(
            state,
            svc,
            loop,
            wt,
            sentinel=ARTIFACT_SENTINEL,
            brief=base_brief + _correction(result, artifact_path, reason),
        )
        artifact = wt.parse_artifact_sentinel(result.text)
        if not _still_planning(loop_id):
            return None
    if artifact is None:
        # Still nothing after the retry — revert RUNNING → PENDING so its state stays honest
        # and an explicit retry cleanly re-runs it, with the reason on the step for the page.
        reason = _no_draft_reason(result, ARTIFACT_SENTINEL, needs=_ARTIFACT_NEEDS)
        logger.warning(
            "planning %s %s: no usable artifact after a retry: %s", loop_id, step_id, reason
        )
        session = loop_files.read_plan_session(loop_id) or session
        if PS.fail_step(session, step_id, reason):
            loop_files.write_plan_session(session)
        return None
    session = loop_files.read_plan_session(loop_id) or session
    step = next((s for s in session.steps if s.id == step_id), step)
    if step.status != PS.StepStatus.RUNNING.value:
        step.status = PS.StepStatus.RUNNING.value  # ensure submit precondition
    PS.submit_artifact(session, step_id, artifact)
    loop_files.write_plan_session(session)
    return next((s for s in session.steps if s.id == step_id), None)


async def finalize_plan(loop_id: str) -> bool:
    """Project the approved artifacts into the loop spec + flip the draft to REVIEW
    (ready to launch). Called once every step is approved. Returns True on success.

    After projecting the spec, runs the walkthrough's optional ``on_finalize`` hook —
    where a kind materializes side effects from its approved plan (the goal kind turns
    its approved sub-goals into linked Tasks, the modern replacement for the dropped
    ``/decompose`` endpoint). The hook runs BEFORE the REVIEW flip so the launchable
    draft already carries its Task links. Never raises out of the hook."""
    loop, wt = _walkthrough_for(loop_id)
    session = loop_files.read_plan_session(loop_id)
    if loop is None or wt is None or session is None:
        return False
    spec = wt.project_to_spec(session)
    if spec:
        store.update_spec(loop_id, spec)
    hook = getattr(wt, "on_finalize", None)
    if hook is not None:
        try:
            await hook(loop_id)
        except Exception:
            logger.warning("walkthrough on_finalize failed for %s", loop_id, exc_info=True)
    try:
        store.update_status(loop_id, LoopStatus.REVIEW)
    except Exception:
        return False
    return True


def mark_design_error(loop_id: str, message: str = "") -> None:
    """Record a recoverable design failure (a backstop if the fire-and-forget advance
    task throws). Idempotent + never raises — creates a session if none exists yet."""
    import time as _time

    try:
        session = loop_files.read_plan_session(loop_id) or PlanSession(
            project_id=loop_id, created_at=_time.time()
        )
        session.design_error = message or (
            "Planning hit an unexpected error. Retry planning, or edit the task to be "
            "more concrete."
        )
        loop_files.write_plan_session(session)
    except Exception:
        logger.debug("mark_design_error failed for %s", loop_id, exc_info=True)


def clear_design_error(loop_id: str) -> None:
    """Clear a recorded design failure so the NEXT advance re-runs the design pass.
    Called only on an EXPLICIT user retry (FE 'Retry planning'), never on a passive
    poll — which is what makes the repeated-pass guard hold."""
    session = loop_files.read_plan_session(loop_id)
    if session is not None and session.design_error:
        session.design_error = ""
        loop_files.write_plan_session(session)


async def advance_plan(state, svc, loop_id: str) -> str:
    """Drive the walkthrough forward by exactly ONE planner pass, then stop at the
    next gate. Returns ``designed`` | ``produced`` | ``gated`` | ``finalized`` |
    ``failed``. Never raises.

    FIXED kinds skip the design pass — a missing session seeds the stable step list.
    DYNAMIC kinds run the design pass when the session has no steps; a recorded
    design_error (with no steps) stays ``failed`` until an explicit retry clears it.
    """
    import time as _time

    try:
        loop, wt = _walkthrough_for(loop_id)
        if loop is None or wt is None:
            return "failed"
        session = loop_files.read_plan_session(loop_id)
        if wt.step_mode == "dynamic":
            # A persisted design failure (no usable steps) must NOT silently re-run —
            # that's the repeated-pass bug. Stay failed until an explicit retry.
            if session is not None and session.design_error and not session.steps:
                return "failed"
            if session is None or not session.steps:
                try:
                    store.update_status(loop_id, LoopStatus.PLANNING)
                except Exception:
                    pass
                session = await run_design_pass(state, svc, loop_id)
                if session is None:
                    return "failed"
                # fall through to produce the first step's artifact this same call
        else:  # fixed
            if session is None:
                try:
                    store.update_status(loop_id, LoopStatus.PLANNING)
                except Exception:
                    pass
                session = PS.PlanSession(project_id=loop_id, created_at=_time.time())
                seed_steps(session, wt.default_steps())
                loop_files.write_plan_session(session)
        if PS.is_complete(session):
            return "finalized" if await finalize_plan(loop_id) else "failed"
        current = PS.current_step(session)
        if current is None:
            return "finalized" if await finalize_plan(loop_id) else "failed"
        if current.status == PS.StepStatus.AWAITING_REVIEW.value:
            return "gated"  # waiting on the user — nothing to do
        step = await run_step_pass(state, svc, loop_id, current.id)
        return "produced" if step is not None else "failed"
    except Exception:
        logger.warning("advance_plan failed for loop %s", loop_id, exc_info=True)
        return "failed"
