"""Tests for personalclaw.stats module.

Every counter in ``Stats`` must have a writer on a real runtime path. A counter nothing
increments reports a confident ``0`` forever, which reads as "this never happened" rather than
"this is not measured" — indistinguishable from a genuinely quiet system, and therefore the more
misleading of the two failure modes.

This file previously drove the counters through helpers (``inc_message_received``,
``inc_tool_approval``, ...) that NO production code ever called, and asserted the resulting
snapshot. That proved the increment mechanism worked while never asking whether anything
increments it — so seven writerless counters stayed green for as long as they existed, and
``daily_report`` had four tests despite having no caller at all. ``test_every_counter_has_a_writer``
below is the assertion that was missing.
"""

import ast
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from personalclaw.stats import Stats

_SRC = Path(__file__).resolve().parent.parent / "src" / "personalclaw"

# The counters, and the runtime path that writes each. Grouped so a failure names the subsystem.
_WRITERS = {
    "sessions_created": "inc_session_created",
    "sessions_cleaned": "inc_session_cleaned",
    "subagents_spawned": "inc_subagent_spawned",
    "subagents_completed": "inc_subagent_completed",
    "subagents_failed": "inc_subagent_failed",
    "input_tokens": "inc_input_tokens",
    "output_tokens": "inc_output_tokens",
    "cache_creation_tokens": "inc_cache_creation_tokens",
    "cache_read_tokens": "inc_cache_read_tokens",
    "total_turns": "inc_turns",
    "total_duration_ms": "inc_duration_ms",
}


def _called_methods() -> set[str]:
    """Every ``.method(...)`` name invoked anywhere in src/personalclaw, excluding stats.py itself.

    Parsed rather than grepped: a grep for the method name also matches its own ``def`` line and
    any mention in a comment, which is how a writerless counter can look wired.
    """
    names: set[str] = set()
    for path in _SRC.rglob("*.py"):
        if path.name == "stats.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


class TestStatsWriters(unittest.TestCase):
    """The contract that keeps a dead counter from shipping as a measurement."""

    def test_every_counter_has_a_writer(self) -> None:
        called = _called_methods()
        orphans = {counter: helper for counter, helper in _WRITERS.items() if helper not in called}
        assert not orphans, (
            f"Counter(s) whose incrementer is never called on any runtime path: {orphans}. "
            "A counter with no writer renders a confident 0 forever. Either wire the call site "
            "or delete the counter — do not ship it as an aspirational field."
        )

    def test_every_counter_is_declared_in_the_writer_map(self) -> None:
        # The other direction: a NEW counter added to Stats must declare its writer here, so it
        # cannot be introduced writerless. This is what would have caught the original seven.
        assert set(Stats().snapshot().keys()) == set(_WRITERS), (
            "Stats counters and the _WRITERS map disagree. A new counter must be added to "
            "_WRITERS with the method that increments it on a real path."
        )

    def test_no_writerless_helpers_remain_on_the_class(self) -> None:
        # A leftover `inc_*` helper for a deleted counter would KeyError-free its way into
        # _c via inc()'s .get() default, silently resurrecting the field.
        # `inc_cost_usd` is deliberately exempt: it accumulates a float in `_cost_usd`, outside
        # the `_c` counter dict, so it is not a snapshot key. It IS written (chat_runner).
        helpers = {n for n in dir(Stats) if n.startswith("inc_")} - {"inc", "inc_cost_usd"}
        assert helpers == set(_WRITERS.values()), (
            f"Unexpected inc_* helper(s): {helpers - set(_WRITERS.values())}. "
            "inc() creates missing keys on demand, so a stray helper re-adds a deleted counter."
        )


class TestStats(unittest.TestCase):

    def setUp(self) -> None:
        Stats().reset()

    # -- singleton --

    def test_singleton(self) -> None:
        assert Stats() is Stats()

    # -- counters --

    def test_increment_and_read(self) -> None:
        s = Stats()
        s.inc_session_created()
        s.inc_session_created()
        s.inc_subagent_spawned()
        s.inc_turns(3)
        snap = s.snapshot()
        assert snap["sessions_created"] == 2
        assert snap["subagents_spawned"] == 1
        assert snap["total_turns"] == 3
        assert snap["sessions_cleaned"] == 0

    def test_token_counters_accumulate_by_amount(self) -> None:
        # Token counters take an n, unlike the lifecycle counters — a regression here would
        # undercount every turn to 1.
        s = Stats()
        s.inc_input_tokens(1200)
        s.inc_output_tokens(340)
        s.inc_cache_read_tokens(900)
        s.inc_cache_creation_tokens(50)
        s.inc_duration_ms(4500)
        snap = s.snapshot()
        assert snap["input_tokens"] == 1200
        assert snap["output_tokens"] == 340
        assert snap["cache_read_tokens"] == 900
        assert snap["cache_creation_tokens"] == 50
        assert snap["total_duration_ms"] == 4500

    # -- summary --

    def test_summary_reports_only_measured_counters(self) -> None:
        s = Stats()
        s.inc_session_created()
        s.inc_subagent_spawned()
        s.inc_turns(2)
        s.inc_input_tokens(10)
        text = s.summary()
        assert "uptime" in text
        assert "sessions 1/0" in text
        assert "subagents 1 spawned" in text
        assert "turns 2" in text
        assert "tokens 10 in" in text

    def test_summary_makes_no_claim_about_messages_or_tool_approvals(self) -> None:
        # The old summary led with `msgs 0 (ok 0 / fail 0) · tools approved 0 denied 0 auto 0 ·
        # timeouts 0` on EVERY install — six writerless counters presented as measurements, which
        # made a busy gateway look idle. Nothing unmeasured may reappear in this string.
        # Matched on the old string's exact PHRASES, not bare words: "failed" legitimately appears
        # now as `subagents N spawned, N completed, N failed`, which IS measured. A substring check
        # for "fail" would forbid a real counter — the assertion has to name what it forbids.
        text = Stats().summary()
        for absent in ("msgs ", "(ok ", "/ fail ", "approved ", "denied ", "auto ", "timeouts "):
            assert absent not in text, f"summary() reports unmeasured {absent!r}"

    def test_daily_report_is_gone(self) -> None:
        # It derived a health verdict (🟢 healthy / 🟡 degraded / 🔴 critical) from
        # messages_success / messages_received, both writerless — so it could only ever emit
        # "🔇 no messages". It had no caller; four tests covered it anyway.
        assert not hasattr(Stats, "daily_report")

    # -- reset --

    def test_reset(self) -> None:
        s = Stats()
        s.inc_session_created()
        s.inc_input_tokens(50)
        s.reset()
        snap = s.snapshot()
        assert all(v == 0 for v in snap.values())

    # -- uptime --

    def test_uptime_str(self) -> None:
        s = Stats()
        with patch("personalclaw.stats.time") as mock_time:
            mock_time.monotonic.return_value = s._start_time + 3661
            assert s.uptime_str() == "1h 1m"

    def test_uptime_str_with_days(self) -> None:
        s = Stats()
        with patch("personalclaw.stats.time") as mock_time:
            mock_time.monotonic.return_value = s._start_time + 3 * 86400 + 14 * 3600 + 22 * 60
            assert s.uptime_str() == "3d 14h 22m"

    # -- snapshot keys --

    def test_snapshot_keys(self) -> None:
        expected = {
            "sessions_created",
            "sessions_cleaned",
            "subagents_spawned",
            "subagents_completed",
            "subagents_failed",
            "input_tokens",
            "output_tokens",
            "cache_creation_tokens",
            "cache_read_tokens",
            "total_turns",
            "total_duration_ms",
        }
        assert set(Stats().snapshot().keys()) == expected

    # -- thread safety --

    def test_thread_safety(self) -> None:
        s = Stats()
        barrier = threading.Barrier(10)

        def worker() -> None:
            barrier.wait()
            for _ in range(100):
                s.inc_session_created()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert s.snapshot()["sessions_created"] == 1000


if __name__ == "__main__":
    unittest.main()
