"""Per-session active-job tracker (PLATFORM-RESILIENCE §6.2).

A small formalization of what is otherwise an internal semaphore: a map from
session key → the ``ActiveJob`` currently running on it. This is **bookkeeping over
existing state, not a scheduler** — it records what a turn's origin is (webui / a
channel / cron / loop / subagent) so the cancel-and-replace decision (§6.3) can tell
an interactive turn a user is watching apart from unattended work that must never be
cancelled out from under itself.

Consumers: the cancel-and-replace eligibility guard, a channel typing/busy signal,
the Doctor ("3 sessions mid-turn"), and the FE queue indicator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Origin classes. Only ``webui`` and ``channel:*`` are INTERACTIVE (a human is
# waiting on the stream); the rest are unattended and are never cancel-and-replace
# targets — a user message landing on a busy loop/cron/subagent session queues.
_INTERACTIVE = ("webui", "channel:")


@dataclass(frozen=True)
class ActiveJob:
    """The turn currently running on a session key."""

    job_id: str
    origin: str  # webui | channel:<name> | cron | loop | subagent | heartbeat | other
    started_at: float

    @property
    def interactive(self) -> bool:
        return self.origin == "webui" or self.origin.startswith("channel:")


def classify_origin(session_key: str) -> str:
    """Map a session key to its origin class, using the platform's verified key
    conventions (see PLATFORM-RESILIENCE §6.3):

    * ``loop-<id>`` / ``loop-plan-…``          → ``loop``
    * ``cron:<job>``                           → ``cron``
    * ``subagent:<id>``                        → ``subagent``
    * ``_bg`` (heartbeat background)           → ``heartbeat``
    * ``dashboard:<session>`` or a bare webui  → ``webui``
    * anything else                            → ``other`` (treated non-interactive)

    A channel transport turn is tagged ``channel:<name>`` by the caller (the inbound
    path knows the transport), not derivable from the key here.
    """
    key = session_key or ""
    if key == "_bg":
        return "heartbeat"
    if key.startswith("loop-"):
        return "loop"
    if key.startswith("cron:"):
        return "cron"
    if key.startswith("subagent:"):
        return "subagent"
    if key.startswith("dashboard:") or key == "dashboard":
        return "webui"
    # A dashboard session is often keyed by a bare name (no prefix); the dashboard
    # runner is the webui channel, so an unprefixed interactive session is webui.
    return "webui"


class ActiveJobTracker:
    """Session key → the ``ActiveJob`` running on it. Registered at turn start,
    cleared at turn end. Purely in-memory (one gateway); never persisted."""

    def __init__(self) -> None:
        self._jobs: dict[str, ActiveJob] = {}
        # Per-session timestamp of the last cancel-and-replace, for the debounce
        # guard (§6.3.5) — a burst of rapid messages produces ONE cancel.
        self._last_cancel_at: dict[str, float] = {}

    # ── turn lifecycle ────────────────────────────────────────────────────
    def register(self, session_key: str, *, origin: Optional[str] = None, now: float) -> ActiveJob:
        """Record a turn starting on ``session_key``. ``origin`` overrides the
        key-derived classification (the channel inbound path passes ``channel:<name>``)."""
        job = ActiveJob(
            job_id=f"{session_key}@{now:.3f}",
            origin=origin or classify_origin(session_key),
            started_at=now,
        )
        self._jobs[session_key] = job
        return job

    def clear(self, session_key: str) -> None:
        """Record a turn ending (idempotent)."""
        self._jobs.pop(session_key, None)

    def get(self, session_key: str) -> Optional[ActiveJob]:
        return self._jobs.get(session_key)

    def active(self) -> list[ActiveJob]:
        """Every in-flight job (the Doctor's 'N sessions mid-turn')."""
        return list(self._jobs.values())

    def interactive_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.interactive)

    # ── cancel-and-replace debounce (§6.3.5) ──────────────────────────────
    def within_debounce(self, session_key: str, min_interval_secs: float, *, now: float) -> bool:
        """True if a cancel-and-replace fired on this session within
        ``min_interval_secs`` — the caller should coalesce (queue) instead of firing
        a second cancel."""
        last = self._last_cancel_at.get(session_key)
        return last is not None and (now - last) < max(0.0, min_interval_secs)

    def mark_cancel(self, session_key: str, *, now: float) -> None:
        self._last_cancel_at[session_key] = now


def is_cancellable_origin(origin: str) -> bool:
    """Whether a turn of this origin may be cancel-and-replaced — interactive turns
    only. Loop/cron/subagent/heartbeat work is NEVER cancelled by a user message
    (§6.3.1); it queues regardless of policy."""
    return origin == "webui" or origin.startswith("channel:")


# Process-global tracker (one gateway). The chat runner registers/clears turns on
# it; the mid-turn handler reads it for the cancel-and-replace decision.
_TRACKER: Optional[ActiveJobTracker] = None


def get_tracker() -> ActiveJobTracker:
    global _TRACKER
    if _TRACKER is None:
        _TRACKER = ActiveJobTracker()
    return _TRACKER


def reset_tracker() -> None:
    """Test isolation — drop the process-global tracker."""
    global _TRACKER
    _TRACKER = None
