"""An agent CLI that reports its context through ACP's usage update is measured.

ACP's own report of the context in use is a ``session/update`` whose ``sessionUpdate`` is
``usage_update``: ``used`` tokens of a window of ``size``. Core read only a vendor metadata frame's
``contextUsagePercentage``, so a CLI that reports only the standard way (claude-code's adapter)
was never measured: its chat's context ring stayed empty, no "context at N%" line was logged, and
the context threshold never applied to it (``test_an_agent_cli_keeps_its_session_at_the_context_
threshold.py`` drives that on a scripted CLI). ``translate.extract_usage_pct`` reads it now, and a
turn keeps whichever of the two frames came last.
"""

from __future__ import annotations

import logging

import pytest
from test_context_pct_honesty import _metadata, _mk_session, _run_turn

from personalclaw.acp import translate
from personalclaw.acp.types import METHOD_SESSION_UPDATE, JsonRpcMessage


def _usage(session_id: str, **update: object) -> JsonRpcMessage:
    return JsonRpcMessage(
        method=METHOD_SESSION_UPDATE,
        params={"sessionId": session_id, "update": {"sessionUpdate": "usage_update", **update}},
    )


class TestTheUsageUpdate:
    def test_it_reads_as_the_share_of_the_window_in_use(self):
        assert translate.extract_usage_pct(_usage("A", used=45_000, size=200_000)) == 22.5
        assert translate.extract_usage_pct(_usage("A", used=0, size=200_000)) == 0.0

    @pytest.mark.parametrize(
        "update",
        [
            {"used": 45_000},  # no window
            {"used": 45_000, "size": 0},
            {"size": 200_000},  # nothing in use said
            {"used": "45000", "size": 200_000},  # not numbers
            {"used": True, "size": 200_000},
            {"used": -1, "size": 200_000},
        ],
    )
    def test_a_frame_that_does_not_say_both_numbers_is_not_a_reading(self, update):
        assert translate.extract_usage_pct(_usage("A", **update)) is None

    def test_another_update_is_not_a_reading(self):
        chunk = JsonRpcMessage(
            method=METHOD_SESSION_UPDATE,
            params={"update": {"sessionUpdate": "agent_message_chunk", "used": 1, "size": 2}},
        )
        assert translate.extract_usage_pct(chunk) is None

    def test_a_count_just_past_the_window_is_the_window_full(self):
        assert translate.extract_usage_pct(_usage("A", used=200_500, size=200_000)) == 100.0

    def test_a_count_well_past_the_window_is_said_once_and_not_read(self, caplog, monkeypatch):
        monkeypatch.setattr(translate, "_overfull_usage_said", False)
        with caplog.at_level(logging.WARNING, logger="personalclaw.acp.translate"):
            assert translate.extract_usage_pct(_usage("A", used=300_000, size=200_000)) is None
            assert translate.extract_usage_pct(_usage("A", used=400_000, size=200_000)) is None
        said = [r for r in caplog.records if "window: not read" in r.getMessage()]
        assert len(said) == 1


class TestATurnKeepsIt:
    @pytest.mark.asyncio
    async def test_a_turn_that_sends_it_is_measured(self):
        """🔴 Red before: the update was passed over, so the turn reported None."""
        s, q, futs = _mk_session()
        await _run_turn(s, q, futs, [_usage("A", used=45_000, size=200_000)])
        assert s.context_usage_pct() == 22.5

    @pytest.mark.asyncio
    async def test_whichever_frame_came_last_is_the_reading(self):
        s, q, futs = _mk_session()
        await _run_turn(s, q, futs, [_metadata("A", 61.5), _usage("A", used=45_000, size=200_000)])
        assert s.context_usage_pct() == 22.5
        await _run_turn(s, q, futs, [_usage("A", used=45_000, size=200_000), _metadata("A", 61.5)])
        assert s.context_usage_pct() == 61.5

    @pytest.mark.asyncio
    async def test_a_frame_that_says_nothing_keeps_the_last_reading(self):
        s, q, futs = _mk_session()
        await _run_turn(s, q, futs, [_usage("A", used=45_000, size=200_000)])
        await _run_turn(s, q, futs, [_usage("A", used=45_000)])
        assert s.context_usage_pct() == 22.5
