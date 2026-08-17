"""Context management for sub-agent results and session workspaces.

Enforces size limits on disk files, memory buffers, and session history
to prevent unbounded growth during multi-agent orchestration.

All limits are centralized here so they can be tuned in one place.

The chat plan-mode text surface that used to share this file is gone. Its plan-memory
journal half was deleted when the Learning Flywheel's RUN_END cadence absorbed run
outcomes into the Run Ledger and proposal pipeline (LEARNING-FLYWHEEL §3.3); the
remaining plan-format parser/rephraser half was deleted by UNIVERSAL-PLANNING, whose
planner owns plan production end to end (see that plan's "Planning Surfaces Collapsed
by This Plan"). What is left here is only the sub-agent context-budget half.
"""

import logging
import shutil
import time
from pathlib import Path

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
