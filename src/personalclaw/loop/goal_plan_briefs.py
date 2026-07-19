"""Pure brief + parser helpers for the GOAL kind's stepwise planning walkthrough — the
fixed intent → sub-goals → quorum → execution_plan step set, each step's brief, and the
artifact JSON parser. No store/orchestration deps (those live in loop.plan_walkthrough);
just the deterministic, unit-testable pieces the goal Walkthrough delegate wraps. Lives
in the unified loop package so the goal kind doesn't reach into legacy loops/ (cutover
Slice 2e). Legacy loops.plan_walkthrough re-exports these until it's deleted."""

from __future__ import annotations

from personalclaw.planning.session import PlanStep

# Sentinels the planner writes (one per pass); distinct so a stale design file is never
# mistaken for a step artifact.
STEPS_SENTINEL = "plan_steps.json"
ARTIFACT_SENTINEL = "step_artifact.json"

# The canonical Goal-Loop walkthrough steps. Unlike Code (whose steps are fully
# dynamic), a goal's planning shape is stable — intent → decomposition → quorum →
# phased plan — so the design pass is fixed here (the planner still tailors each step's
# CONTENT to the goal). The last step is execution_plan (the executable one).
GOAL_STEP_GUIDE: tuple[tuple[str, str], ...] = (
    ("intent", "the true intent + definition of done (concrete success criteria)"),
    ("sub_goals", "the ordered decomposition into distinct, non-overlapping sub-goals"),
    ("quorum", "the agent roles/personas (+ orchestration) the goal needs"),
    ("execution_plan", "the role-phased cycle plan — ordered phases, each with role/target/min_cycles/exit"),
)


def default_steps() -> list[dict]:
    """The fixed ordered step list seeded for every goal walkthrough."""
    titles = {
        "intent": "Intent & definition of done",
        "sub_goals": "Sub-goals",
        "quorum": "Agent quorum",
        "execution_plan": "Execution plan",
    }
    return [{"kind": k, "title": titles[k], "objective": desc} for k, desc in GOAL_STEP_GUIDE]


def _approved_block(approved: list[PlanStep]) -> str:
    """The '  - [kind] title: summary' lines for the approved artifacts so far ("" when
    none) — the dynamic block the step brief drops under its APPROVED header."""
    return "\n".join(
        f"  - [{a.kind}] {a.title}: {_summarize_artifact(a.artifact)}" for a in approved
    )


def _comments_block(step: PlanStep) -> str:
    """The '  - text' lines for the user's re-draft comments on this step ("" when none)."""
    out: list[str] = []
    for c in step.comments:
        text = str(c.get("text", "")).strip() if isinstance(c, dict) else str(c).strip()
        if text:
            out.append(f"  - {text}")
    return "\n".join(out)


def build_step_brief(goal: str, step: PlanStep, *, approved: list[PlanStep] | None = None) -> str:
    """Produce the brief for ONE goal-planning step — carries the goal, the prior
    approved artifacts (so each step builds on the last), and any user comments on this
    step (re-draft feedback)."""
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    approved = approved or []
    rendered = render_use_case_prompt(
        "goal_step_brief",
        {
            "goal": goal.strip(),
            "step_title": step.title,
            "step_kind": step.kind,
            "objective": step.objective.strip(),
            "approved_block": _approved_block(approved),
            "comments_block": _comments_block(step),
            "artifact_sentinel": ARTIFACT_SENTINEL,
            "artifact_contract": _artifact_contract(step.kind),
        },
    )
    if rendered is not None:
        return rendered
    # Prompt store unavailable — fall back to the shipped brief (identical text).
    lines = [
        f"GOAL TO PLAN:\n{goal.strip()}",
        "",
        f"CURRENT PLANNING STEP: {step.title}  (kind: {step.kind})",
    ]
    if step.objective.strip():
        lines.append(f"Objective: {step.objective.strip()}")
    if approved:
        lines += ["", "APPROVED ARTIFACTS SO FAR (build on these — stay consistent):"]
        for a in approved:
            lines.append(f"  - [{a.kind}] {a.title}: {_summarize_artifact(a.artifact)}")
    if step.comments:
        lines += ["", "THE USER COMMENTED ON YOUR LAST DRAFT — address every point:"]
        for c in step.comments:
            text = str(c.get("text", "")).strip() if isinstance(c, dict) else str(c).strip()
            if text:
                lines.append(f"  - {text}")
    lines += [
        "",
        "Investigate context as needed (the goal may point at internal docs/tickets "
        "reachable via MCP, or the web). Then PRODUCE THIS STEP'S ARTIFACT as JSON "
        f"written to `{ARTIFACT_SENTINEL}` in your current directory.",
        "",
        _artifact_contract(step.kind),
        "",
        "Ground every claim concretely — no filler. This is a single pass for THIS "
        "step: once the file is written, you are DONE (the user reviews it next).",
    ]
    return "\n".join(lines)


def _artifact_contract(kind: str) -> str:
    """The expected artifact JSON for a goal-planning step kind. Each is a JSON object
    with a ``markdown`` human view plus the structured field(s) that project into the
    loop spec."""
    if kind == "intent":
        return (
            'For `intent`, write: {"markdown":"<intent + DoD as readable text>", '
            '"success_criteria":"<one concrete, checkable definition of done>"}'
        )
    if kind == "sub_goals":
        return (
            'For `sub_goals`, write: {"markdown":"<the decomposition explained>", '
            '"sub_goals":["<distinct sub-goal>", ...]}  (ordered, non-overlapping)'
        )
    if kind == "quorum":
        return (
            'For `quorum`, write: {"markdown":"<who runs this + why>", '
            '"roster":[{"role":"<role>","persona":"<persona/agent>","role_hint":"<what they do>"}, ...]}'
            "  (smallest quorum that credibly achieves the goal; one member = solo)"
        )
    if kind == "execution_plan":
        return (
            'For `execution_plan`, write: {"markdown":"<the phased plan explained>", '
            '"execution_plan":[{"role":"<role from the quorum>","target":"<this phase\'s '
            'target>","min_cycles":<int>,"phase_exit":"<signal to advance>"}, ...]}'
            "  (ordered phases the orchestrator runs in turn)"
        )
    return f'For `{kind}`, write: {{"markdown":"<the artifact>", "key_points":["...", ...]}}'


def _summarize_artifact(artifact: dict) -> str:
    if not isinstance(artifact, dict):
        return ""
    md = str(artifact.get("markdown", "")).strip()
    if md:
        first = md.splitlines()[0].strip()
        return (first[:160] + "…") if len(first) > 160 else first
    return ""


def parse_artifact_sentinel(text: str) -> dict | None:
    """Parse a step's artifact JSON (tolerates code-fenced / prose-wrapped). Ensures
    ``markdown`` is a string when present; keeps all other fields as authored."""
    from personalclaw.loop.code_classify import _parse_obj  # shared JSON-from-prose helper

    data = _parse_obj(text or "")
    if not isinstance(data, dict):
        return None
    if "markdown" in data and not isinstance(data["markdown"], str):
        data["markdown"] = str(data["markdown"])
    return data
