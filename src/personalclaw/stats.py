"""Runtime statistics — thread-safe counters for sessions, subagents, tokens and turns.

Every counter here MUST have a writer on a real runtime path. A counter nothing increments
reports a confident `0` forever, which reads as "this never happened" rather than "this is not
measured" — the more misleading of the two failure modes, because it is indistinguishable from a
genuinely quiet system. Seven such counters (messages_received/success/failed, tool_approvals/
denials/auto_approved, timeouts) were removed rather than kept as aspirational fields; if the
message and tool-approval paths later need counting, add the counter WITH its call site.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)


class Stats:
    """Singleton collecting runtime counters with lock-guarded increments."""

    _instance: "Stats | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "Stats":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_counters()
        return cls._instance

    def _init_counters(self) -> None:
        self._mu = threading.Lock()
        self._start_time = time.monotonic()
        self._c: dict[str, int] = {
            "sessions_created": 0,
            "sessions_cleaned": 0,
            "subagents_spawned": 0,
            "subagents_completed": 0,
            "subagents_failed": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "total_turns": 0,
            "total_duration_ms": 0,
        }
        self._cost_usd: float = 0.0

    # -- mutators --

    def inc(self, key: str, n: int = 1) -> None:
        """Increment counter *key* by *n*."""
        with self._mu:
            self._c[key] = self._c.get(key, 0) + n

    def inc_input_tokens(self, n: int) -> None:
        self.inc("input_tokens", n)

    def inc_output_tokens(self, n: int) -> None:
        self.inc("output_tokens", n)

    def inc_cache_creation_tokens(self, n: int) -> None:
        self.inc("cache_creation_tokens", n)

    def inc_cache_read_tokens(self, n: int) -> None:
        self.inc("cache_read_tokens", n)

    def inc_cost_usd(self, amount: float) -> None:
        with self._mu:
            self._cost_usd += amount

    def inc_turns(self, n: int) -> None:
        self.inc("total_turns", n)

    def inc_duration_ms(self, n: int) -> None:
        self.inc("total_duration_ms", n)

    def get_cost_usd(self) -> float:
        with self._mu:
            return self._cost_usd

    def inc_session_created(self) -> None:
        self.inc("sessions_created")

    def inc_session_cleaned(self) -> None:
        self.inc("sessions_cleaned")

    def inc_subagent_spawned(self) -> None:
        self.inc("subagents_spawned")

    def inc_subagent_completed(self) -> None:
        self.inc("subagents_completed")

    def inc_subagent_failed(self) -> None:
        self.inc("subagents_failed")

    # -- queries --

    def uptime_str(self) -> str:
        """Human-readable uptime."""
        secs = round(time.monotonic() - self._start_time)
        h, rem = divmod(secs, 3600)
        d, h = divmod(h, 24)
        m, _s = divmod(rem, 60)
        parts: list[str] = []
        if d:
            parts.append(f"{d}d")
        parts.append(f"{h}h")
        parts.append(f"{m}m")
        return " ".join(parts)

    def snapshot(self) -> dict[str, int]:
        """Return a copy of all counters."""
        with self._mu:
            return dict(self._c)

    def summary(self) -> str:
        """One-line summary of the counters that are actually measured.

        Reports only written counters. The previous version led with
        ``msgs 0 (ok 0 / fail 0) · tools approved 0 denied 0 auto 0 · timeouts 0`` on every
        install — six writerless counters presented as measurements, which made a busy gateway
        look idle. ``daily_report()`` was worse and is gone: it derived a health verdict
        (🟢 healthy / 🟡 degraded / 🔴 critical) from ``messages_success / messages_received``,
        so it could only ever emit "🔇 no messages". It had no caller.
        """
        s = self.snapshot()
        return (
            f"uptime {self.uptime_str()} · "
            f"sessions {s['sessions_created']}/{s['sessions_cleaned']} · "
            f"subagents {s['subagents_spawned']} spawned, "
            f"{s['subagents_completed']} completed, {s['subagents_failed']} failed · "
            f"turns {s['total_turns']} · "
            f"tokens {s['input_tokens']} in / {s['output_tokens']} out"
        )

    def reset(self) -> None:
        """Zero all counters and restart uptime clock."""
        with self._mu:
            for k in self._c:
                self._c[k] = 0
            self._start_time = time.monotonic()


def cache_hit_pct(
    *,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    input_tokens: int,
) -> float | None:
    """Share of one turn's PROMPT tokens that were served from cache, or ``None``.

    Deliberately a module-level function, not a :class:`Stats` method: it derives a
    per-turn ratio from three numbers the caller already holds. Putting it on the
    singleton would invite a second, turn-scoped store beside the process-lifetime
    counters at ``stats.py:43-44`` (``cache_creation_tokens`` / ``cache_read_tokens``),
    and those counters stay the only tally.

    THE DENOMINATOR — ``input_tokens + cache_read_tokens + cache_creation_tokens``.
    The three buckets on ``LLMEvent`` are DISJOINT: ``input_tokens`` EXCLUDES the
    cached tokens, so they must be added back to recover the turn's whole prompt.
    Evidence:

    * ``llm/anthropic.py:529-531`` (and its twin at ``:715-717``) assigns
      ``input_tokens`` verbatim from ``usage.input_tokens``, while the cache counts
      come from the SDK's separate ``cache_creation_input_tokens`` /
      ``cache_read_input_tokens`` fields via ``_read_cache_usage``
      (``llm/anthropic.py:84-98``). No arithmetic ever relates the three.
    * ``pricing.py:106-113`` bills them additively — ``input * in_rate + cache_read *
      cache_read_rate + cache_creation * cache_write_rate``. If ``input_tokens``
      already contained the cached tokens, the shipped cost model would double-bill
      every cached turn.
    * ``usage_ledger.py:197-200`` (``_fold``) sums the three into three SEPARATE
      aggregate keys, side by side. A subset relation would make that fold
      double-count on every cached turn, so the persisted ledger's own arithmetic
      only balances if the buckets are disjoint. Cited over PCS-7's own
      ``pricing.py:166-168``, which adds the same three but is this module's
      counterpart — evidence for a premise must not be the code the premise
      justifies.

    Returns ``None`` when the denominator is 0: no prompt tokens is NO MEASUREMENT,
    not ``0%``. Same honesty rule as ``context_pct`` on the turn-complete line — see
    ``dashboard/chat_runner.py:635-636``, whose ``if context_pct is not None`` guard
    exists because a defaulted ``0`` printed ``context 0%`` for providers that
    reported nothing, a number the backend never supplied. A measured 0 (prompt
    tokens present, none of them cached) is a real answer and returns ``0.0``.
    """
    read = cache_read_tokens or 0
    prompt_tokens = read + (cache_creation_tokens or 0) + (input_tokens or 0)
    if prompt_tokens <= 0:
        return None
    return (read / prompt_tokens) * 100.0
