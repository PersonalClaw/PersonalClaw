"""Pure brief + parser helpers for the RESEARCH kind's stepwise planning walkthrough.

Research planning is a deep-research PLAN, not a goal decomposition: intent →
subtopics (the living research plan) → output format & manner → execution plan. The
generic brief assembly + artifact JSON parser are shared with the goal walkthrough
(``goal_plan_briefs``); only the step set + per-step artifact contract differ here, so
a research loop's Plan Review reads as research planning rather than goal sub-goals.
"""

from __future__ import annotations

from personalclaw.planning.session import PlanStep
from personalclaw.loop import goal_plan_briefs as _gpb

# Reuse the goal walkthrough's sentinels (same plumbing, different step semantics).
STEPS_SENTINEL = _gpb.STEPS_SENTINEL
ARTIFACT_SENTINEL = _gpb.ARTIFACT_SENTINEL

# The canonical Research-Loop walkthrough steps. Stable shape (the planner tailors each
# step's CONTENT to the request). ``subtopics`` is the deep-research plan; ``output``
# pins the report template + manner the user asked for; ``execution_plan`` is executable.
RESEARCH_STEP_GUIDE: tuple[tuple[str, str], ...] = (
    ("intent", "the true research question + what 'done' means (the report the user actually wants)"),
    ("subtopics", "the initial deep-research plan — the ordered subtopics/questions to investigate (this list evolves as the loop learns)"),
    ("output", "the OUTPUT contract — the report's template/structure AND the manner (tone, format, depth, audience) the user requested"),
    ("execution_plan", "the role-phased research cycle plan — ordered phases, each with role/target/min_cycles/exit"),
)


def default_steps() -> list[dict]:
    """The fixed ordered step list seeded for every research walkthrough."""
    titles = {
        "intent": "Research question & definition of done",
        "subtopics": "Subtopics (research plan)",
        "output": "Output format & manner",
        "execution_plan": "Execution plan",
    }
    return [{"kind": k, "title": titles[k], "objective": desc} for k, desc in RESEARCH_STEP_GUIDE]


def build_step_brief(task: str, step: PlanStep, *, approved: list[PlanStep] | None = None) -> str:
    """Brief for ONE research-planning step. Delegates the generic assembly (task, prior
    approved artifacts, user comments) to the goal helper, then appends the
    research-specific artifact contract for this step kind."""
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    base = _gpb.build_step_brief(task, step, approved=approved)
    # The goal helper embeds the GOAL artifact contract; for the research-only step kinds
    # (subtopics/output) it falls back to a generic key_points contract, so append the
    # precise research contract to steer the artifact JSON. (intent/execution_plan reuse
    # the goal contracts verbatim — identical shape.) The contract lives in the prompt
    # system (bundled ``task-research-step-brief``); it renders to "" for the kinds that
    # need no extra.
    extra = render_use_case_prompt("research_step_brief", {"step_kind": step.kind})
    if extra is None:  # prompt store unavailable — fall back to the shipped contract
        extra = _research_artifact_contract(step.kind)
    return f"{base}\n\n{extra}" if extra else base


def _research_artifact_contract(kind: str) -> str:
    if kind == "subtopics":
        return (
            "RESEARCH ARTIFACT CONTRACT — for `subtopics`, write: "
            '{"markdown":"<the research plan explained>", '
            '"subtopics":["<subtopic / question to investigate>", ...]}  '
            "(ordered by signal; this is the STARTING plan — the loop adds/prunes as it learns)"
        )
    if kind == "output":
        return (
            "RESEARCH ARTIFACT CONTRACT — for `output`, write: "
            '{"markdown":"<the output contract explained>", '
            '"output_template":"<the report structure/sections, or a named template>", '
            '"output_manner":"<tone, format, depth, audience — how the user wants it written>"}'
        )
    return ""  # intent / execution_plan reuse the goal contract embedded by build_step_brief


def parse_artifact_sentinel(raw: str):
    return _gpb.parse_artifact_sentinel(raw)
