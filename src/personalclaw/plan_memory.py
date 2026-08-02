"""Plan format + plan-memory — the legacy chat plan-mode surface.

Split out of ``context_management`` because the two concerns that module held had
nothing to do with each other: one bounds resource growth during multi-agent
orchestration (result files, history, session budgets), the other parses a plan
format and keeps a plan-memory journal on disk. They shared a file, not a subject.

Isolating this half is preparation for its removal. UNIVERSAL-PLANNING replaces the
format, and the Learning Flywheel's run-end cadence absorbs the plan-memory journal
into the proposal pipeline — at which point this becomes a file to delete rather
than a region to carve out of a live module. What remains here is exactly what the
current callers still use (``chat_title``, ``history`` consolidation), so the seam
is drawn where the deletion will be.

Everything plan-shaped moved together, ``rephrase_plan`` included — it reads as a
generic text utility but its whole job is restating *this* plan format, so leaving
it behind would have split one concern across two files for no gain.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

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


# ── Plan memory ─────────────────────────────────────────────────────

# Plan memory lives in a GLOBAL directory (not per-session) so it can be
# queried across all sessions. Each plan run gets a unique entry with the
# session_id as a tag. A consolidation mechanism can summarize common learnings
# into a "plan_lessons.md" file that is always injected during planning.
#
# Future: similarity search via embeddings to find top-5 related plans.
# For now, we keep the last N plans and a consolidated summary.

_PLAN_MEMORY_DIR = "plan_memory"
_PLAN_MEMORY_FILE = "plan_memory.jsonl"
_PLAN_LESSONS_FILE = "plan_lessons.md"  # consolidated lessons, always injected


def _plan_memory_dir() -> Path:
    """Global plan memory directory."""
    d = config_dir() / _PLAN_MEMORY_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def plan_memory_path() -> Path:
    """Path to global plan memory JSONL."""
    return _plan_memory_dir() / _PLAN_MEMORY_FILE


def plan_lessons_path() -> Path:
    """Path to consolidated plan lessons (always injected during planning)."""
    return _plan_memory_dir() / _PLAN_LESSONS_FILE


_plan_lessons_cache: tuple[float, str] = (0.0, "")
_PLAN_LESSONS_TTL = 30.0  # seconds


def load_plan_lessons() -> str:
    """Load consolidated plan lessons. Returns empty string if none.

    Cached for 30s to avoid repeated file reads within the same session.
    """
    global _plan_lessons_cache
    now = time.time()
    if now - _plan_lessons_cache[0] < _PLAN_LESSONS_TTL:
        return _plan_lessons_cache[1]
    path = plan_lessons_path()
    if not path.exists():
        _plan_lessons_cache = (now, "")
        return ""
    result = path.read_text(encoding="utf-8").strip()
    _plan_lessons_cache = (now, result)
    return result


def build_plan_consolidation_prompt() -> str:
    """Build the LLM prompt for plan lesson consolidation.

    Returns empty string when no plan events exist (caller should skip LLM call).
    Called by the consolidation cycle's HistoryConsolidator.
    """
    path = plan_memory_path()
    if not path.exists():
        return ""
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not events:
        return ""

    event_lines: list[str] = []
    for e in events[-100:]:
        etype = e.get("type", "")
        sid = e.get("session_id", "?")[:8]
        if etype == "plan_created":
            event_lines.append(f"[{sid}] Plan created: {e.get('task_description', '')[:100]}")
        elif etype == "task_failed":
            event_lines.append(
                f"[{sid}] Failed: {e.get('task', '')[:60]} — {e.get('error', '')[:60]}"
            )
        elif etype == "user_guidance":
            event_lines.append(
                f"[{sid}] User said: Q: {e.get('question', '')[:60]} A: {e.get('answer', '')[:80]}"
            )
        elif etype == "plan_completed":
            status = "succeeded" if e.get("success") else "failed"
            event_lines.append(f"[{sid}] Plan {status}: {e.get('summary', '')[:80]}")
        elif etype == "format_miss":
            event_lines.append(
                f"[{sid}] Format miss ({e.get('pattern', '')}): {e.get('snippet', '')[:80]}"
            )
        elif etype == "format_invalid":
            event_lines.append(f"[{sid}] Format invalid: {e.get('issues', [])}")

    if not event_lines:
        return ""

    existing = load_plan_lessons()
    # The consolidation instruction lives in the prompt system (bundled
    # ``task-plan-consolidation``), rendered with the current lessons + events.
    from personalclaw.prompt_providers.runtime import render_use_case_prompt

    return (
        render_use_case_prompt(
            "plan_consolidation",
            {
                "existing": existing or "(empty — first consolidation)",
                "event_lines": "\n".join(event_lines),
            },
        )
        or ""
    )


_MAX_PLAN_LESSONS_LINES = 80  # hard cap on saved plan lessons


def save_plan_lessons(text: str) -> None:
    """Write consolidated plan lessons to disk."""
    global _plan_lessons_cache
    if text and len(text) > 20:
        lines = text.splitlines(keepends=True)
        if len(lines) > _MAX_PLAN_LESSONS_LINES:
            lines = lines[:_MAX_PLAN_LESSONS_LINES]
            text = "".join(lines).rstrip() + "\n"
        atomic_write(plan_lessons_path(), text)
        _plan_lessons_cache = (time.time(), text.strip())
        logger.info("Saved plan lessons (%d chars, %d lines)", len(text), len(lines))


_PLAN_MEMORY_MAX_LINES = 500


def append_plan_event(session_id: str, event: dict[str, Any]) -> None:
    """Append a plan event to the global plan memory JSONL."""
    event = {**event, "session_id": session_id, "ts": time.time()}
    path = plan_memory_path()
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
    # Rotate: keep last N lines to prevent unbounded growth.
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > _PLAN_MEMORY_MAX_LINES:
            atomic_write(path, "\n".join(lines[-_PLAN_MEMORY_MAX_LINES:]) + "\n")
    except Exception:
        pass


def load_plan_memory(session_id: str | None = None) -> list[dict[str, Any]]:
    """Load plan events, optionally filtered by session_id."""
    path = plan_memory_path()
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if session_id is None or e.get("session_id") == session_id:
            events.append(e)
    return events


def summarize_plan_memory_for_context(session_id: str) -> str:
    """Build a context string with plan lessons + session events."""
    lessons = load_plan_lessons()
    events = load_plan_memory(session_id)
    if not lessons and not events:
        return ""
    parts: list[str] = []
    if lessons:
        parts.append(f"## Plan lessons from past sessions\n{lessons}")
    if events:
        lines = []
        for e in events:
            etype = e.get("type", "")
            if etype == "task_failed":
                lines.append(f"- ❌ {e.get('task', '?')} failed: {e.get('error', '?')}")
            elif etype == "user_guidance":
                lines.append(f"- 💬 Q: {e.get('question', '?')} → A: {e.get('answer', '?')}")
            elif etype == "plan_completed":
                status = "✅" if e.get("success") else "❌"
                lines.append(f"- {status} Plan completed: {e.get('summary', '')}")
            elif etype == "plan_created":
                lines.append(f"- 📋 Plan created with stages: {e.get('stages', [])}")
        if lines:
            parts.append("## This session's events\n" + "\n".join(lines))
    return "\n\n".join(parts)


def build_stage_context(
    session_id: str,
    approved_plan: str,
    completed_stages: list[dict[str, Any]],
) -> str:
    """Build context for the LLM when executing a stage."""
    lessons = load_plan_lessons()
    parts: list[str] = []
    if lessons:
        parts.append(f"## Plan lessons\n{lessons}")
    parts.append(f"## Approved plan\n{approved_plan}")
    if completed_stages:
        lines = [
            f"- Stage {s['stage']}: {s['status']} — {s.get('summary', '')}"
            for s in completed_stages
        ]
        parts.append("## Completed stages\n" + "\n".join(lines))
    return "\n\n".join(parts)
