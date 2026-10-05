"""Shared goal-scoping seam — the grill pipeline.

A vendor-neutral scoping pipeline reused by goal loops (flat sub-goals), Projects
(question tree), and the chat ``grill`` skill. It replaces the blind
one-shot "suggest sub-goals" with a **memory-checked** decomposition:

    assess_goal → check_memory → decompose(shape) → save_decisions

- **assess_goal** — is the goal clear enough to decompose, or does it need a
  clarifying pass? Returns an ambiguity read + any clarifying questions.
- **check_memory** — pull relevant prior decisions/lessons so the decomposition
  doesn't re-ask what the user already settled (the key upgrade over the one-shot).
- **decompose(shape)** — produce the scoped breakdown: ``flat`` = a list of
  sub-goals (goal loops); ``tree`` = phases of questions (projects).
- **save_decisions** — persist the decomposition's settled decisions as lessons so
  the next grill benefits (closes the loop with the lesson store).

Pure orchestration: the LLM call + memory recall + lesson write are injected as
callables, so this module has no dashboard/provider coupling and is unit-testable.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

Shape = Literal["flat", "tree"]

# Callables injected by the caller (keeps grill vendor-/surface-neutral).
AskFn = Callable[[str], Awaitable[str]]  # prompt → raw LLM text
RecallFn = Callable[[str], Awaitable[str]]  # query → relevant prior context (or "")
SaveFn = Callable[[str], None]  # a settled decision → persist (lesson)


@dataclass
class GrillResult:
    """Outcome of a grill pass."""

    shape: Shape
    sub_goals: list[str] = field(default_factory=list)  # flat shape
    phases: list[dict] = field(
        default_factory=list
    )  # tree shape: [{title, description, steps:[{title,prompt}]}]
    clarifying_questions: list[str] = field(default_factory=list)
    memory_hits: int = 0
    ambiguous: bool = False


def _flat_prompt(goal: str, prior: str) -> str:
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    rendered = render_use_case_prompt("grill_flat", {"goal": goal, "prior": prior})
    if rendered is not None:
        return rendered
    mem = (
        f"\n\nRELEVANT PRIOR CONTEXT (do not re-ask what's already settled here):\n{prior}"
        if prior
        else ""
    )
    return (
        "Decompose this goal into concise sub-goals that make pursuing it thorough — "
        "each a DISTINCT angle (technical feasibility, trade-offs, alternatives, risks, "
        "cost, prior art, success criteria). Reason from first principles: fundamental, "
        "non-overlapping angles, not restatements. Output ONLY a JSON array of sub-goal "
        f"strings (no prose), at most 20.{mem}\n\nGoal: {goal}"
    )


def _tree_prompt(goal: str, prior: str) -> str:
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    rendered = render_use_case_prompt("grill_tree", {"goal": goal, "prior": prior})
    if rendered is not None:
        return rendered
    mem = (
        f"\n\nRELEVANT PRIOR CONTEXT (skip questions already answered here):\n{prior}"
        if prior
        else ""
    )
    return (
        "You are planning work. Read the goal and produce an adaptive plan as PHASES of "
        "clarifying QUESTIONS that, once answered, give enough detail to break the work "
        "into concrete tasks. Detect the kind of work and tailor the phases (software, "
        "event, research, routine — adapt; no fixed template). 2-4 phases, 2-5 questions "
        f"each.{mem}\n\nGOAL:\n{goal}\n\nRespond with ONLY a JSON object, no prose:\n"
        '{"phases": [{"title": "...", "description": "...", "steps": [{"title": "short label", "prompt": "the question"}]}]}'  # noqa: E501
    )


def _assess_prompt(goal: str) -> str:
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    rendered = render_use_case_prompt("grill_assess", {"goal": goal})
    if rendered is not None:
        return rendered
    # Plain concat (NOT str.format) — the example JSON contains literal braces.
    return (
        "Assess whether this goal is clear enough to decompose into a concrete plan, or "
        "whether it's ambiguous and needs clarifying questions first. Respond with ONLY a "
        'JSON object: {"ambiguous": true|false, "questions": ["clarifying question", …]} '
        "(empty questions list when clear).\n\nGoal: " + goal
    )


async def assess_goal(goal: str, ask: AskFn) -> tuple[bool, list[str]]:
    """Return ``(ambiguous, clarifying_questions)``. Never raises."""
    from personalclaw.llm_helpers import expecting, parse_llm_json

    try:
        # Each grill answer has a shape, and one without it is that model failing the call: the
        # next model of the chain is asked inside the call (`expecting`).
        with expecting(_object_problem):
            raw = await ask(_assess_prompt(goal))
        data = parse_llm_json(raw)
    except Exception:
        return False, []
    if not isinstance(data, dict):
        return False, []
    qs = [
        str(q).strip() for q in data.get("questions", []) if isinstance(q, str) and str(q).strip()
    ]
    return bool(data.get("ambiguous")) and bool(qs), qs[:8]


async def check_memory(goal: str, recall: RecallFn | None) -> str:
    """Pull relevant prior context for the goal (or "" — never raises)."""
    if recall is None:
        return ""
    try:
        return (await recall(goal)) or ""
    except Exception:
        logger.debug("grill memory check failed", exc_info=True)
        return ""


async def grill(
    goal: str,
    *,
    shape: Shape = "flat",
    ask: AskFn,
    recall: RecallFn | None = None,
    save: SaveFn | None = None,
    assess: bool = True,
) -> GrillResult:
    """Run the full scoping pipeline. Memory-checked, shape-aware, best-effort.

    *assess* gates the clarifying-question pass (goal loops may skip it for speed).
    A flat result fills ``sub_goals``; a tree result fills ``phases``. ``save`` (if
    given) persists each settled sub-goal/phase title as a decision lesson.
    """
    result = GrillResult(shape=shape)

    if assess:
        ambiguous, qs = await assess_goal(goal, ask)
        result.ambiguous = ambiguous
        result.clarifying_questions = qs

    prior = await check_memory(goal, recall)
    result.memory_hits = 1 if prior else 0
    from personalclaw.llm_helpers import expecting, parse_llm_json, parse_llm_json_list

    try:
        if shape == "flat":
            with expecting(_list_problem):
                raw = await ask(_flat_prompt(goal, prior))
            items = parse_llm_json_list(raw)
            result.sub_goals = [
                str(s).strip() for s in (items or []) if isinstance(s, str) and str(s).strip()
            ][:20]
        else:
            with expecting(_phases_problem):
                raw = await ask(_tree_prompt(goal, prior))
            data = parse_llm_json(raw)
            phases = data.get("phases") if data is not None else None
            result.phases = _normalize_phases(phases or [])
    except Exception:
        logger.debug("grill decompose failed", exc_info=True)

    if save:
        for decision in _decisions(result):
            try:
                save(decision)
            except Exception:
                logger.debug("grill save_decision failed", exc_info=True)

    return result


def _decisions(result: GrillResult) -> list[str]:
    if result.shape == "flat":
        return list(result.sub_goals)
    return [ph.get("title", "") for ph in result.phases if ph.get("title")]


def _normalize_phases(phases: list) -> list[dict]:
    out: list[dict] = []
    for ph in phases:
        if not isinstance(ph, dict):
            continue
        steps = [
            {"title": str(s.get("title", "")).strip(), "prompt": str(s.get("prompt", "")).strip()}
            for s in ph.get("steps", [])
            if isinstance(s, dict) and str(s.get("prompt", "")).strip()
        ]
        out.append(
            {
                "title": str(ph.get("title", "")).strip(),
                "description": str(ph.get("description", "")).strip(),
                "steps": steps,
            }
        )
    return out


# Each check reads the answer the way its parser above does (``llm_helpers.parse_llm_json`` /
# ``parse_llm_json_list``), so an answer the chain accepts is one the scoping pass can use.


def _object_problem(raw: str) -> str:
    from personalclaw.llm_helpers import parse_llm_json

    return "" if parse_llm_json(raw) is not None else "no JSON object"


def _list_problem(raw: str) -> str:
    from personalclaw.llm_helpers import parse_llm_json_list

    return "" if parse_llm_json_list(raw) is not None else "no JSON array"


def _phases_problem(raw: str) -> str:
    from personalclaw.llm_helpers import parse_llm_json

    data = parse_llm_json(raw)
    return "" if data is not None and isinstance(data.get("phases"), list) else "no 'phases' array"
