"""An agent CLI's turn ends on its answer, not on a silence — and a late answer stays in its turn.

Driven through the real ACP client against ``scripted_acp_agent.py`` ``slow-answer``: its first
turn runs a step and then thinks in silence before it answers; later turns answer at once.

Red before the fix: a turn that went quiet for the silence window after a step (90 s; shrunk
here) was ended as "ACP prompt timed out" while its prompt was still pending, and the answer
that arrived afterwards was read by the NEXT turn as that turn's own.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from scripted_acp_agent import LATE_ANSWER, NEXT_ANSWER, SLOW_SECONDS

from personalclaw.acp.client import AcpClient
from personalclaw.acp.dialect import CodexDialect
from personalclaw.acp.errors import AcpTimeoutError
from personalclaw.acp.types import EVENT_COMPLETE, EVENT_TEXT_CHUNK

AGENT = Path(__file__).with_name("scripted_acp_agent.py")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.acp.client._DRAIN_DURATION", 0.05)
    # Where a silence timer exists, make it far shorter than the agent's think, so a turn that
    # ends on silence ends here. The fixed code has none to shrink.
    monkeypatch.setattr("personalclaw.acp.session._STALE_TURN_TIMEOUT", 0.3, raising=False)
    record = tmp_path / "wire.jsonl"
    made = AcpClient(
        command=[sys.executable, str(AGENT), "slow-answer", str(record)],
        work_dir=tmp_path / "work",
        dialect=CodexDialect(),
    )
    made.record = record  # type: ignore[attr-defined]
    return made


def _received(record: Path, method: str) -> list[dict]:
    rows = [json.loads(line) for line in record.read_text().splitlines() if line]
    return [r for r in rows if r["kind"] == "received" and r["method"] == method]


@pytest.mark.asyncio
async def test_a_model_thinking_after_its_last_step_is_still_answering(client):
    try:
        events = [e async for e in client.stream_events("review the log")]
    finally:
        await client.shutdown()
    assert [e.text for e in events if e.kind == EVENT_TEXT_CHUNK] == [LATE_ANSWER]
    assert events[-1].kind == EVENT_COMPLETE and events[-1].stop_reason == "end_turn"


@pytest.mark.asyncio
async def test_an_answer_that_arrives_after_its_turn_ended_never_lands_in_the_next(client):
    """The turn's own deadline is shorter than the agent's think: it ends as a timeout, the agent
    is told to stop, and its late answer is dropped before the next turn starts."""
    try:
        with pytest.raises(AcpTimeoutError):
            async for _event in client.stream_events("review the log", timeout=SLOW_SECONDS / 3):
                pass
        second = [e async for e in client.stream_events("and the second question?")]
    finally:
        await client.shutdown()
    assert [e.text for e in second if e.kind == EVENT_TEXT_CHUNK] == [NEXT_ANSWER]
    assert second[-1].kind == EVENT_COMPLETE
    assert _received(client.record, "session/cancel"), "the agent was never told to stop"
