"""The audit row of an unattended refusal names the answers the agent CLI offered.

Every decision on an agent CLI's call is audited with what the agent offered for it beside the
answer sent (``turn_endings.offered``), so which refusal a Deny could send, and whether one let the
agent go on, is on the record. An unattended run's refusal is one of those decisions, made with
nobody to ask, and its row says it the same way.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw import security
from personalclaw.dashboard import chat_refusals

_OFFERED = [
    {"id": "allow_once", "label": "Yes, proceed", "kind": "allow_once"},
    {"id": "cancel", "label": "No, and tell me what to do differently", "kind": "reject_once"},
]


def _event(options: list[dict[str, str]]) -> SimpleNamespace:
    return SimpleNamespace(
        title="Run command",
        tool_kind="execute",
        request_id="7",
        tool_call_id="c7",
        tool_input='{"command": "git push"}',
        options=options,
    )


async def _refused(monkeypatch, options: list[dict[str, str]]) -> dict:
    audit = MagicMock()
    monkeypatch.setattr(chat_refusals, "sel", lambda: audit)
    monkeypatch.setattr(chat_refusals.auto_denials, "note_unattended", MagicMock())
    refuse = AsyncMock()
    await chat_refusals.refuse_unattended(
        MagicMock(),
        MagicMock(key="s1"),
        "dashboard:s1",
        _event(options),
        refuse,
        agent="Codex",
        said="refused: no one to approve",
        reason="unattended",
        decided_by="unattended",
        why="the run is unattended: no one to approve",
        kind=security.DENY_KIND_POLICY,
    )
    refuse.assert_awaited_once()
    (row,) = [c.kwargs for c in audit.log_tool_invocation.call_args_list]
    return row


@pytest.mark.asyncio
async def test_an_unattended_refusal_names_every_answer_the_agent_offered(monkeypatch):
    row = await _refused(monkeypatch, _OFFERED)
    assert row["outcome"] == "denied"
    assert row["metadata"]["decided_by"] == "unattended"
    assert row["metadata"]["offered"] == [
        {"id": "allow_once", "kind": "allow_once", "name": "Yes, proceed"},
        {"id": "cancel", "kind": "reject_once", "name": "No, and tell me what to do differently"},
    ]


@pytest.mark.asyncio
async def test_a_runtime_that_offers_no_answers_has_none_on_its_row(monkeypatch):
    """PersonalClaw's own runtime offers no answers for a call, and its row claims none."""
    row = await _refused(monkeypatch, [])
    assert "offered" not in row["metadata"]
    assert row["metadata"]["reason"] == "unattended"
