"""Shared planning substrate — the stepwise, gated walkthrough used by BOTH the
Code feature and Goal Loop.

A planning *session* is an ordered list of *steps*; each step produces an
*artifact* the user approves or comments on (a comment sends it back for a
re-draft) before the walkthrough advances. The step ``kind`` set is open — each
feature's planner designs the steps that fit its target (Code: SDLC stages; Goal
Loop: sub-goal / persona / phase-cycle definition).

This package holds the feature-agnostic pieces: the :mod:`session` data model +
state machine. Feature-specific briefs, parsers, and persistence layer on top
(``code.plan_walkthrough`` / ``code.store``; the Goal Loop equivalents).
"""

from personalclaw.planning.session import (
    PlanComment,
    PlanSession,
    PlanStep,
    StepStatus,
    approve_step,
    comment_step,
    current_step,
    edit_artifact,
    is_complete,
    mark_running,
    submit_artifact,
)

__all__ = [
    "PlanComment",
    "PlanSession",
    "PlanStep",
    "StepStatus",
    "approve_step",
    "comment_step",
    "current_step",
    "edit_artifact",
    "is_complete",
    "mark_running",
    "submit_artifact",
]
