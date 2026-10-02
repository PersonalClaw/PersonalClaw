"""A time limit that ends an agent's wait for its owner names the limit and the real wait.

An agent's time limit counts its whole run. When the limit ran out while one of its calls waited
for the owner's answer, the agent's error read "Timed out after 30 minutes while waiting for you to
answer bash [turn 2/0 | last tool: bash | elapsed: 2100s]": thirty minutes of waiting blamed on
her, though she had answered its first ask at once and the ask that was open had waited about
twelve, and three counters of the agent's own bookkeeping in a sentence a person reads.

So the error names the limit (what ran out), the call and how long THAT ask had been open (what
she could have done), and nothing else; the counters go to the log.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw.subagent import (
    SubagentManager,
    ended_a_wait_for_the_owner,
    time_limit_stop,
)

#: Words that are the agent's own bookkeeping, never a person's sentence.
COUNTERS = re.compile(r"\[turn|\bturn \d+/\d+|elapsed:|last tool:")


def test_the_sentence_names_the_limit_and_how_long_the_open_ask_waited() -> None:
    """The run's case: a 30-minute limit, and an ask that had been open 11 minutes 42 seconds."""
    asked_at = 1000.0
    error = time_limit_stop(1800, ("bash", asked_at), asked_at + 702)
    assert "time limit of 30 minutes" in error, error
    assert "waiting 12 minutes for your answer" in error, error
    assert "bash" in error, error
    assert not COUNTERS.search(error), error
    assert ended_a_wait_for_the_owner(error)


def test_a_limit_that_ended_work_says_nothing_of_waiting() -> None:
    error = time_limit_stop(1800, None, 5000.0)
    assert error == "Its time limit of 30 minutes ran out", error
    assert not ended_a_wait_for_the_owner(error)


def test_a_cancel_or_another_failure_is_not_a_wait_the_limit_ended() -> None:
    assert not ended_a_wait_for_the_owner("cancelled")
    assert not ended_a_wait_for_the_owner("Reaped after 1843s (exceeded 1800s deadline)")
    assert not ended_a_wait_for_the_owner("")


# ── the manager: the ask's own opening time reaches the sentence ─────────────
#
# Deterministic on purpose. A wall-clock race (work a second, then ask) measures the host's load as
# much as the code: on a loaded machine the limit can fire before the second ask is even made.


def _manager(limit: int = 1800) -> SubagentManager:
    sessions = MagicMock()
    sessions.reset = AsyncMock()
    sessions.release = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    return SubagentManager(sessions=sessions, ctx_builder=None, default_timeout=limit)


def test_an_ask_records_the_call_and_the_moment_it_opened() -> None:
    from personalclaw.subagent import SubagentInfo

    manager = _manager()
    info = SubagentInfo(id="clock003", task="look it up")
    with patch("personalclaw.subagent.time.monotonic", return_value=500.0):
        with manager._waiting_for_owner(info, "bash"):
            assert manager._asking_owner[info.id] == ("bash", 500.0)
    assert info.id not in manager._asking_owner, "an answered ask is not left open"


@pytest.mark.asyncio
async def test_the_wait_named_is_the_open_asks_not_the_whole_run() -> None:
    """🔴 Before: the limit's whole run read as her wait ("while waiting for you to answer bash"),
    with the agent's counters beside it. The agent ran 1,843 seconds; the open ask had waited
    702 of them."""
    import time

    from personalclaw.subagent import SubagentInfo

    manager = _manager()
    info = SubagentInfo(id="clock004", task="look it up")
    info.turns, info.last_tool = 2, "bash"
    manager._agents[info.id] = info
    manager._running_count = 1
    manager._asking_owner[info.id] = ("bash", time.monotonic() - 702)
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        await manager._force_reap(info.id, info, 1843)
    assert "waiting 12 minutes for your answer" in info.error, info.error
    assert not COUNTERS.search(info.error), info.error
    assert ended_a_wait_for_the_owner(info.error)


@pytest.mark.asyncio
async def test_a_reap_that_ended_work_names_no_wait_and_no_counters() -> None:
    from personalclaw.subagent import SubagentInfo

    manager = _manager()
    info = SubagentInfo(id="clock005", task="look it up")
    info.turns, info.last_tool = 7, "grep"
    manager._agents[info.id] = info
    manager._running_count = 1
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        await manager._force_reap(info.id, info, 1843)
    assert info.error == "Reaped after 1843s (exceeded 1800s deadline)", info.error
    assert not ended_a_wait_for_the_owner(info.error)
