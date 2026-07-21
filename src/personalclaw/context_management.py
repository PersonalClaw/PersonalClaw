"""Context management for sub-agent results and session workspaces.

Enforces size limits on disk files, memory buffers, and session history
to prevent unbounded growth during multi-agent orchestration.

All limits are centralized here so they can be tuned in one place.
"""

import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

# ── Limits ──────────────────────────────────────────────────────────

# Per sub-agent result file: truncate after this many bytes.
RESULT_FILE_MAX_BYTES = 512_000  # 500 KB

# In-memory streaming_text buffer per sub-agent (for Activity Viewer).
STREAMING_TEXT_MAX_CHARS = 50_000  # ~50 KB

# Words to include in the completion notification summary.
# The LLM uses this to decide whether to read the full file.
# 50 words is enough for simple status; 200 words gives enough for planning.
RESULT_SUMMARY_WORDS = 200

# Session workspace: max total bytes across all result files.
SESSION_MAX_BYTES = 5_000_000  # 5 MB

# History JSONL: max entries kept.
HISTORY_MAX_ENTRIES = 500

# Session workspace: max age before cleanup (seconds).
SESSION_MAX_AGE_SECS = 86400 * 7  # 7 days

# Max completed sub-agents retained in SubagentManager._agents dict.
MAX_RETAINED_AGENTS = 50

# ── Orchestration guards ────────────────────────────────────────────

# Max consecutive failures on the same sub-task before forcing user escalation.
MAX_TASK_FAILURES = 3
MAX_STAGE_ROUNDS = 3
MAX_STAGE_ESCALATIONS = 2  # after 2 escalations (= 9 rounds), force-fail


class OrchestrationTracker:
    """Track failures and rounds per orchestrated session.

    Enforces hard limits that the LLM prompt cannot override.
    """

    def __init__(self, stage_timeout_seconds: int = 1800) -> None:
        self._task_failures: dict[str, int] = {}  # task_key → failure count
        self._stage_rounds: dict[int, int] = {}  # stage_num → round count
        self._stage_escalations: dict[int, int] = {}  # stage_num → escalation count
        self._stage_results: dict[int, str] = {}  # stage_num → result file path
        self.stopped: bool = False
        self._stage_timeout: int = stage_timeout_seconds
        self._stage_start: float = 0.0  # set when stage begins

    def stop(self) -> None:
        """User requested stop after escalation."""
        self.stopped = True

    @property
    def has_escalated(self) -> bool:
        """True if any task hit failure limit or any stage hit round limit."""
        return any(v >= MAX_TASK_FAILURES for v in self._task_failures.values()) or any(
            v >= MAX_STAGE_ROUNDS for v in self._stage_rounds.values()
        )

    def reset_after_guidance(self) -> None:
        """Reset round counters after user provides guidance. Increments escalation count."""
        for stage, rounds in self._stage_rounds.items():
            if rounds >= MAX_STAGE_ROUNDS:
                self._stage_escalations[stage] = self._stage_escalations.get(stage, 0) + 1
                self._stage_rounds[stage] = 0
        # Also reset task failures so user guidance gets a fresh start
        self._task_failures.clear()
        self._stage_start = 0.0  # reset timeout clock for next stage

    def is_force_failed(self, stage: int) -> bool:
        """True if stage has exhausted all escalations (2 escalations = 9 rounds)."""
        return self._stage_escalations.get(stage, 0) >= MAX_STAGE_ESCALATIONS

    def record_failure(self, task_key: str) -> bool:
        """Record a failure. Returns True if limit reached (must escalate)."""
        self._task_failures[task_key] = self._task_failures.get(task_key, 0) + 1
        return self._task_failures[task_key] >= MAX_TASK_FAILURES

    def record_success(self, task_key: str) -> None:
        """Reset failure count for a task."""
        self._task_failures.pop(task_key, None)

    def failure_count(self, task_key: str) -> int:
        return self._task_failures.get(task_key, 0)

    def record_round(self, stage: int) -> bool:
        """Record a spawn round for a stage. Returns True if limit reached."""
        self._stage_rounds[stage] = self._stage_rounds.get(stage, 0) + 1
        if self._stage_rounds[stage] == 1 or not self._stage_start:
            self._stage_start = time.monotonic()
        return self._stage_rounds[stage] >= MAX_STAGE_ROUNDS

    def is_stage_timed_out(self) -> bool:
        """True if current stage has exceeded the timeout."""
        if not self._stage_start or not self._stage_timeout:
            return False
        return (time.monotonic() - self._stage_start) > self._stage_timeout

    @property
    def timeout_human(self) -> str:
        """Human-friendly timeout string, e.g. '30m' or '1m30s'."""
        s = self._stage_timeout
        if s >= 60:
            m, rem = divmod(s, 60)
            return f"{m}m{rem}s" if rem else f"{m}m"
        return f"{s}s"

    def round_count(self, stage: int) -> int:
        return self._stage_rounds.get(stage, 0)

    @property
    def current_stage(self) -> int:
        return max(self._stage_rounds.keys(), default=1)

    # ── Python-controlled stage loop helpers ──

    def record_stage_result(self, stage_num: int, result_path: str) -> None:
        """Record that *stage_num* (1-based) completed with result at *result_path*."""
        self._stage_results[stage_num] = result_path

    def status_summary(self, current: int, total: int, titles: list[str]) -> str:
        """Build a compact plan status block.

        *current* is 0-based index of the stage about to execute.
        """
        lines: list[str] = []
        for i in range(total):
            t = titles[i] if i < len(titles) else ""
            label = f"Stage {i + 1}: {t}" if t else f"Stage {i + 1}"
            if i < current:
                lines.append(f"  ✅ {label} — completed")
            elif i == current:
                lines.append(f"  ▶️ {label} — execute now")
            else:
                lines.append(f"  ⬜ {label} — pending")
        return "\n".join(lines)


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


def cap_result_file(path: Path) -> bool:
    """Truncate a result file if it exceeds RESULT_FILE_MAX_BYTES.

    Keeps the first 20% and last 80% of the budget to preserve
    the beginning (task context) and end (final output).
    Returns True if truncation occurred.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size <= RESULT_FILE_MAX_BYTES:
        return False

    head_budget = RESULT_FILE_MAX_BYTES // 5  # 20%
    tail_budget = RESULT_FILE_MAX_BYTES - head_budget - 100  # 80% minus marker

    content = path.read_text(encoding="utf-8", errors="replace")
    head = content[:head_budget]
    tail = content[-tail_budget:]
    marker = f"\n\n[...truncated {size - RESULT_FILE_MAX_BYTES:,} bytes...]\n\n"

    atomic_write(path, head + marker + tail)
    logger.info("Truncated %s from %d to %d bytes", path.name, size, RESULT_FILE_MAX_BYTES)
    return True


def cap_streaming_text(text: str) -> str:
    """Truncate in-memory streaming_text if it exceeds the limit.

    Keeps the last STREAMING_TEXT_MAX_CHARS characters (most recent output).
    """
    if len(text) <= STREAMING_TEXT_MAX_CHARS:
        return text
    return "…(truncated)\n" + text[-STREAMING_TEXT_MAX_CHARS + 20 :]


def cap_history(entries: list[dict]) -> list[dict]:
    """Keep only the last HISTORY_MAX_ENTRIES from a history list."""
    if len(entries) <= HISTORY_MAX_ENTRIES:
        return entries
    return entries[-HISTORY_MAX_ENTRIES:]


def check_session_budget(session_dir: Path) -> bool:
    """Check if a session workspace exceeds its total size budget.

    Returns True if over budget. Caller should stop writing new results.
    """
    total = sum(f.stat().st_size for f in session_dir.glob("agent-*.md") if f.is_file())
    return total > SESSION_MAX_BYTES


def evict_completed_agents(agents: dict, max_retained: int = MAX_RETAINED_AGENTS) -> int:
    """Remove oldest completed sub-agents from the agents dict.

    Returns number of evicted entries.
    """
    completed = [(k, v) for k, v in agents.items() if v.done]
    if len(completed) <= max_retained:
        return 0
    completed.sort(key=lambda x: x[1].started)
    to_evict = len(completed) - max_retained
    for k, _ in completed[:to_evict]:
        del agents[k]
    logger.info("Evicted %d completed sub-agents (kept %d)", to_evict, max_retained)
    return to_evict


def cleanup_stale_sessions() -> int:
    """Remove session workspace directories older than SESSION_MAX_AGE_SECS.

    Returns number of cleaned up sessions.
    """
    sessions_dir = config_dir() / "sessions"
    if not sessions_dir.exists():
        return 0
    now = time.time()
    cleaned = 0
    for d in sessions_dir.iterdir():
        if not d.is_dir():
            continue
        try:
            files = list(d.iterdir())
            mtime = max((f.stat().st_mtime for f in files), default=d.stat().st_mtime)
            if now - mtime > SESSION_MAX_AGE_SECS:
                shutil.rmtree(d, ignore_errors=True)
                cleaned += 1
        except OSError:
            continue
    if cleaned:
        logger.info("Cleaned up %d stale session workspaces", cleaned)
    return cleaned
