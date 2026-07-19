"""The planning session — an ordered, gated walkthrough of planner-designed steps.

A target's *planning phase* is no longer a single classify-then-review. The planner
agent walks a **dynamic, ordered list of planning steps** it designs for the target
at hand (Code: problem framing, requirements, design, decomposition, …; Goal Loop:
sub-goal, persona, phase-cycle definition). Each step **produces an artifact**, then
**blocks on a user gate**: the user approves it (advance) or comments (re-run the
step with the feedback). The final approved step projects into execution.

This module is the pure, feature-agnostic data model + state machine for that
walkthrough — shared by the Code feature and Goal Loop. Each feature persists a
session as a sidecar (e.g. ``plan_session.json``) next to its draft and supplies
its own step briefs/parsers. The step ``kind`` set is intentionally OPEN — the
planner chooses the steps that fit the target — so this model validates *shape*,
never a fixed taxonomy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class StepStatus(str, Enum):
    """Lifecycle of a single planning step.

    ``pending`` → the planner hasn't started it. ``running`` → the planner is
    investigating + drafting its artifact. ``awaiting_review`` → the artifact is
    ready and the UI gate is open (the user approves or comments).
    ``approved`` → the user accepted the artifact; the walkthrough may advance.
    A comment moves an ``awaiting_review`` step back to ``running`` (re-draft).
    """

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"


@dataclass
class PlanComment:
    """One piece of user feedback on a step's artifact, with the cycle it was
    applied at (so the UI can thread comment → re-draft → new artifact)."""

    text: str
    at: float = 0.0


@dataclass
class PlanStep:
    """One step in the SDLC planning walkthrough.

    ``kind`` is a planner-chosen slug (e.g. ``problem_framing``, ``requirements``,
    ``design``, ``decomposition``) — open set. ``artifact`` is the step's produced
    content, a free-form dict whose shape depends on ``kind`` (the FE renders it
    per-kind). ``comments`` accumulate across re-drafts.
    """

    id: str
    kind: str
    title: str
    objective: str = ""
    status: str = StepStatus.PENDING.value
    artifact: dict = field(default_factory=dict)
    comments: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PlanStep":
        return cls(
            id=str(data.get("id", "")),
            kind=str(data.get("kind", "")),
            title=str(data.get("title", "")),
            objective=str(data.get("objective", "")),
            status=_coerce_status(data.get("status")),
            artifact=dict(data.get("artifact") or {}),
            comments=[
                {"text": str(c.get("text", "")), "at": float(c.get("at", 0.0) or 0.0)}
                for c in (data.get("comments") or [])
                if isinstance(c, dict) and str(c.get("text", "")).strip()
            ],
        )


def _coerce_status(raw: Any) -> str:
    try:
        return StepStatus(str(raw)).value
    except ValueError:
        return StepStatus.PENDING.value


@dataclass
class PlanSession:
    """The whole planning walkthrough for one code project.

    ``steps`` is the ordered list the planner designed. The session is complete
    when every step is ``approved`` — at which point the final decomposition step's
    artifact projects into execution. ``steps`` may grow as the planner refines
    (it can append steps it discovers are needed), but earlier approved steps are
    immutable except via a fresh comment.
    """

    project_id: str
    steps: list[PlanStep] = field(default_factory=list)
    created_at: float = 0.0
    # Set when a DESIGN pass ran but produced no usable step list (planner timed out
    # / emitted unparseable output). Persisting this — rather than leaving NO session
    # — makes "planning ran and failed" distinguishable from "planning never started",
    # so the walkthrough surfaces the failure + an explicit Retry instead of silently
    # re-spawning a fresh investigation on every poll/remount. Cleared on a real retry.
    design_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "created_at": self.created_at,
            "steps": [s.to_dict() for s in self.steps],
            "design_error": self.design_error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlanSession":
        # Tolerate a non-dict payload (a corrupt/partially-written session file that's
        # valid JSON but not an object) — return an empty session rather than raising
        # AttributeError on .get(), which would otherwise propagate out of the reader
        # and break the entire planning view + resume reaper. Shared by Code + Goal Loop.
        if not isinstance(data, dict):
            return cls(project_id="", created_at=0.0, steps=[])
        return cls(
            project_id=str(data.get("project_id", "")),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            steps=[PlanStep.from_dict(s) for s in (data.get("steps") or []) if isinstance(s, dict)],
            design_error=str(data.get("design_error", "") or ""),
        )


# ── pure state-machine helpers (no persistence) ──

def current_step(session: PlanSession) -> PlanStep | None:
    """The step the walkthrough is currently ON — the first step not yet approved.

    Returns None when every step is approved (planning is complete) or there are
    no steps. This is the single source of truth for "where are we" — the running
    or awaiting_review step, or the next pending one.
    """
    for step in session.steps:
        if step.status != StepStatus.APPROVED.value:
            return step
    return None


def is_complete(session: PlanSession) -> bool:
    """True iff there is at least one step and ALL steps are approved."""
    return bool(session.steps) and all(
        s.status == StepStatus.APPROVED.value for s in session.steps
    )


def approve_step(session: PlanSession, step_id: str) -> bool:
    """Mark a step approved (the user accepted its artifact). Only an
    ``awaiting_review`` step can be approved. Returns True if it transitioned.
    """
    for step in session.steps:
        if step.id == step_id:
            if step.status == StepStatus.AWAITING_REVIEW.value:
                step.status = StepStatus.APPROVED.value
                return True
            return False
    return False


def comment_step(session: PlanSession, step_id: str, text: str, *, at: float = 0.0) -> bool:
    """Attach a user comment to a step and send it back for a re-draft. Only an
    ``awaiting_review`` step accepts a comment (you comment on a produced artifact).
    Moves the step ``awaiting_review`` → ``running``. Returns True if applied.
    """
    text = (text or "").strip()
    if not text:
        return False
    for step in session.steps:
        if step.id == step_id:
            if step.status == StepStatus.AWAITING_REVIEW.value:
                step.comments.append({"text": text, "at": at})
                step.status = StepStatus.RUNNING.value
                return True
            return False
    return False


def mark_running(session: PlanSession, step_id: str) -> bool:
    """The planner picked up a step (pending → running). Returns True if applied."""
    for step in session.steps:
        if step.id == step_id and step.status == StepStatus.PENDING.value:
            step.status = StepStatus.RUNNING.value
            return True
    return False


def mark_pending(session: PlanSession, step_id: str) -> bool:
    """Revert a RUNNING step to pending (the planner pass produced no usable
    artifact — timeout / garbled output). Keeps the step's state honest ("not yet
    produced") + cleanly retryable, instead of stranding it as RUNNING with nothing.
    Only acts on a RUNNING step (never reopens an approved/awaiting one). Returns
    True if applied."""
    for step in session.steps:
        if step.id == step_id and step.status == StepStatus.RUNNING.value:
            step.status = StepStatus.PENDING.value
            return True
    return False


def submit_artifact(session: PlanSession, step_id: str, artifact: dict) -> bool:
    """The planner finished a step's artifact (running → awaiting_review). Replaces
    the step's artifact and opens its review gate. Returns True if applied."""
    for step in session.steps:
        if step.id == step_id and step.status == StepStatus.RUNNING.value:
            step.artifact = dict(artifact or {})
            step.status = StepStatus.AWAITING_REVIEW.value
            return True
    return False


def edit_artifact(session: PlanSession, step_id: str, markdown: str) -> bool:
    """The user directly edits an artifact's human-facing ``markdown`` body while
    it's awaiting review — finalizing it themselves instead of round-tripping the
    planner (the vision's "work with the user to finalize them"). Only the markdown
    is user-editable; the structured fields (the projection source) stay as the
    planner authored them, so a hand-edit can't desync the executable plan. The step
    stays ``awaiting_review`` (the user still approves). Returns True if applied.
    """
    for step in session.steps:
        if step.id == step_id:
            if step.status == StepStatus.AWAITING_REVIEW.value:
                step.artifact = {**(step.artifact or {}), "markdown": str(markdown)}
                return True
            return False
    return False
