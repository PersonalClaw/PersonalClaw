"""Plan format — the chat plan-mode text surface (parse, validate, restate).

Once this module also carried a plan-memory JSONL journal and its LLM-consolidated
``plan_lessons.md``. The Learning Flywheel's RUN_END cadence absorbed that silo into
the Run Ledger + proposal pipeline (LEARNING-FLYWHEEL §3.3): a run's outcomes now
journal to ``events.jsonl`` and surface as human-gated lesson proposals, rather than
appending to a separate global file that only planning re-read. The journal half was
deleted with that cadence; what remains is the plan *format* — the only thing the live
callers (``chat_title``) ever used.

``rephrase_plan`` lives here too: it reads as a generic text utility but its whole job
is restating *this* plan format, so it belongs with the parser, not apart from it.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Plan format validation ──────────────────────────────────────────

_PLAN_HEADER_RE = re.compile(r"📋\s*Plan for:", re.IGNORECASE)
_STAGE_RE = re.compile(r"^Stage\s+(\d+)\s*:", re.MULTILINE | re.IGNORECASE)
_STAGE_TITLE_RE = re.compile(r"^Stage\s+(\d+)\s*:\s*(.*)", re.MULTILINE | re.IGNORECASE)
_PLAN_GOAL_RE = re.compile(r"📋\s*Plan for:\s*\"?(.+?)\"?\s*$", re.MULTILINE | re.IGNORECASE)
_OPTION_RE = re.compile(r"\[OPTION:\s*Go\s*\|.*Cancel\s*\]")


def extract_plan_metadata(text: str) -> tuple[list[str], str, list[list[str]]]:
    """Extract stage titles, goal, and descriptions from plan text.

    Returns (titles, goal, descriptions) where titles[i] is Stage i+1's title
    and descriptions[i] is a list of bullet-point tasks for that stage.
    """
    pairs = _STAGE_TITLE_RE.findall(text)
    max_stage = max((int(n) for n, _ in pairs), default=0)
    titles = [""] * max_stage
    for num_str, title in pairs:
        idx = int(num_str) - 1
        if 0 <= idx < max_stage:
            titles[idx] = title.strip()
    goal_m = _PLAN_GOAL_RE.search(text)
    goal = goal_m.group(1).strip() if goal_m else ""
    # Extract bullet points under each stage heading
    descriptions: list[list[str]] = [[] for _ in range(max_stage)]
    lines = text.splitlines()
    current_stage = -1
    for line in lines:
        m = _STAGE_TITLE_RE.match(line)
        if m:
            current_stage = int(m.group(1)) - 1
            continue
        stripped = line.strip()
        if current_stage >= 0 and current_stage < max_stage and stripped.startswith("- "):
            descriptions[current_stage].append(stripped)
        elif stripped and not stripped.startswith("-") and current_stage >= 0:
            # Non-bullet, non-empty line ends bullet collection for this stage
            current_stage = -1
    return titles, goal, descriptions


PLAN_TEMPLATE = """\
📋 Plan for: "<task description>"

Stage 1: <Title>
  - <task>
  - <task>

Stage 2: <Title>
  - <task>

Stage N: Verification
  - <verification task>

[OPTION: Go | Go All | Cancel]"""


# Loose pre-filter: catches plan-like text cheaply. False positives are
# handled by rephrase_plan(might_not_be_plan=True) which asks the LLM.
_PLAN_LIKE_RE = re.compile(
    r"(?:^|\n)\s*(?:Phase|Step|Stage|Part)\s+\d+\s*[:\-—]" r"|(?:^|\n)\s*\d+\.\s+\*\*[A-Z]",
    re.IGNORECASE,
)


def looks_like_plan(text: str) -> bool:
    """Cheap heuristic: does the text look like it might be a plan?

    Intentionally loose — false positives are caught downstream by the
    LLM-based rephrase which can reject non-plans.
    """
    return len(_PLAN_LIKE_RE.findall(text)) >= 2


_GO_ALL_RE = re.compile(r"\[OPTION:\s*Go\s*\|\s*Cancel\s*\]")


def ensure_go_all_option(text: str) -> str:
    """Patch [OPTION: Go | Cancel] → [OPTION: Go | Go All | Cancel]."""
    return _GO_ALL_RE.sub("[OPTION: Go | Go All | Cancel]", text)


def validate_plan_format(text: str) -> tuple[bool, bool, list[str]]:
    """Check if text contains a plan and whether it follows the expected format.

    Returns (has_plan, valid, issues).
    """
    if not _PLAN_HEADER_RE.search(text):
        return False, False, []
    issues: list[str] = []
    stages = _STAGE_RE.findall(text)
    if not stages:
        issues.append("No 'Stage N:' lines found")
    else:
        nums = [int(s) for s in stages]
        if nums != list(range(1, len(nums) + 1)):
            issues.append(f"Stages not sequential: {nums}")
    if not _OPTION_RE.search(text):
        issues.append("Missing [OPTION: Go | Go All | Cancel] footer")
    return True, len(issues) == 0, issues


async def rephrase_plan(
    text: str, issues: list[str], client: Any, *, might_not_be_plan: bool = False
) -> str | None:
    """Ask the LLM to reformat a malformed plan. Returns fixed text or None.

    When *might_not_be_plan* is True, the LLM is instructed to return the
    input unchanged (prefixed with ``NOT_A_PLAN:``) if it is not an
    execution plan.
    """
    from personalclaw.llm_helpers import stream_and_collect
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    # The reformat instruction (both the plain and the is-this-even-a-plan variant)
    # lives in the prompt system (bundled ``task-plan-rephrase``).
    prompt = render_use_case_prompt(
        "plan_rephrase",
        {
            "plan_template": PLAN_TEMPLATE,
            "issues": ", ".join(issues),
            "text": text,
            "might_not_be_plan": might_not_be_plan,
        },
    )
    if not prompt:
        logger.warning("Plan rephrase prompt unresolved — skipping")
        return None
    try:
        result = await stream_and_collect(client, prompt)
        if not result:
            return None
        if might_not_be_plan and result.strip().startswith("NOT_A_PLAN"):
            return None
        return result
    except Exception:
        logger.warning("Plan rephrase failed", exc_info=True)
        return None


def strip_plan_markers(text: str) -> str:
    """Remove plan structure markers, leaving content as plain text."""
    text = _PLAN_HEADER_RE.sub("", text)
    text = _STAGE_RE.sub("", text)
    text = _OPTION_RE.sub("", text)
    return text.strip()
