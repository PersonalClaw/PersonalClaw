"""A helper's reply goes back into a channel thread only when its work was asked for there.

A helper (a subagent) hands its report back to the session that started it, and the reply its
session writes to that report was posted into the session's channel thread whatever started the
helper: work the owner started from the dashboard, an internal turn's, another thread's. In a
channel or a group everyone in the conversation read it, though they asked for none of that work.

Now the reply goes into the thread only when the turn that started the helper came from that
thread's own message (``SubagentInfo.started_on``, matched on its channel and thread), whoever
sent it, or when the thread is the owner's own DM with PersonalClaw. Started anywhere else, or
from a start nothing records, it goes to the owner's DM, and stays in PersonalClaw (the Inbox)
when no channel reaches her.

Driven as the gateway ends a helper's work (``_subagent_done``, through ``_init_subagents``) for
a session a channel app runs itself, a thread keyed by its own ts as Slack's is, with the channel
connected and knowing its own ids.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_a_chat_linked_to_a_channel_continues_there import (  # noqa: F401 — fixture
    SLACK_DM,
    chats,
)

from personalclaw.subagent import SubagentInfo
from personalclaw.turn_source import DASHBOARD_SOURCE, arrived_on

pytestmark = pytest.mark.security

#: The shared channel the session's thread is in, and the thread (its session's key).
SHARED = "C0SHAREDCH"
THREAD = "1712793600.000300"
#: Another thread on the same channel.
ELSEWHERE = "1712793600.000900"
COLLEAGUE = "U0JONASCOL"
#: What the session's model wrote in reply to the helper's report.
REPLY = "The landlord's number is 555-0142; I added it to the lease notes."


def _helper(started_on: dict[str, str]) -> SubagentInfo:
    """A helper the thread's session started, finished, its start recorded as *started_on*."""
    return SubagentInfo(
        id="h1e2l3p4",
        task="Find the landlord's number in the lease",
        parent_session_key=THREAD,
        done=True,
        result="Found it: 555-0142.",
        started_on=dict(started_on),
    )


@pytest.fixture
def slack(chats) -> MagicMock:  # noqa: F811 — the fixture imported above
    """Slack connected, the owner's DM there opening as :data:`SLACK_DM`."""
    delivery = chats("slack")["slack"]
    delivery.deliver_subagent_reply = AsyncMock()
    return delivery


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The gateway's own end of a helper's work, for a session on the channel conversation
    :data:`SHARED`, whose model answers the report with :data:`REPLY`."""
    from test_gateway import _make_orchestrator, _mock_sessions

    orch = _make_orchestrator()
    orch.sessions = _mock_sessions()
    orch.sessions.get_channel = MagicMock(return_value=SHARED)
    orch.sessions.get_agent = MagicMock(return_value="")
    orch.ctx_builder = MagicMock()
    orch.dashboard_state = None
    with (
        patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
        patch("personalclaw.gateway.SubagentManager") as manager_type,
    ):
        manager_type.return_value = MagicMock(
            running=[],
            running_agents_for=MagicMock(return_value=[]),
            get=MagicMock(return_value=None),
        )
        orch._init_subagents()
    orch.ctx_builder = None  # the report is handed to the session as it is
    monkeypatch.setattr(
        "personalclaw.gateway.stream_and_collect", AsyncMock(return_value=REPLY), raising=True
    )
    return orch


def _ended(gateway: Any, helper: SubagentInfo, *, channel: str = SHARED) -> None:
    """End *helper*'s work, its session on the conversation *channel*, as the gateway does."""
    gateway.sessions.get_channel = MagicMock(return_value=channel)

    async def _run() -> None:
        await gateway._announce_subagents([helper])
        await asyncio.gather(*list(gateway._background_tasks))

    asyncio.run(_run())


def _replied_in(slack: MagicMock) -> list[str]:
    """Each conversation the reply was posted in."""
    return [c.args[0] for c in slack.deliver_subagent_reply.await_args_list if c.args[1] == REPLY]


def test_a_helper_its_thread_started_answers_in_the_thread(gateway, slack):
    """The control: a colleague asked for the work in the thread, so its reply is posted there."""
    _ended(gateway, _helper(arrived_on(THREAD, COLLEAGUE, "slack")))
    assert _replied_in(slack) == [SHARED]


def test_a_helper_started_from_the_dashboard_answers_in_her_dm(gateway, slack):
    """🔴 Red before: the owner's own work from the dashboard was posted into the shared thread."""
    _ended(gateway, _helper(dict(DASHBOARD_SOURCE)))
    assert _replied_in(slack) == [SLACK_DM]


def test_with_no_dm_its_reply_stays_in_personalclaw(gateway, slack, monkeypatch):
    """🔴 Red before: with no way to reach her, the reply went into the shared thread. Now nothing
    is posted on the channel, and the reply waits in the Inbox."""
    inboxed: list[str] = []
    monkeypatch.setattr(
        "personalclaw.inbox_providers.native_source.post_to_inbox",
        lambda message, **_kw: inboxed.append(message) or MagicMock(),
    )
    slack.open_dm = AsyncMock(return_value="")
    _ended(gateway, _helper(dict(DASHBOARD_SOURCE)))
    assert _replied_in(slack) == []
    assert any(REPLY in item for item in inboxed), inboxed


@pytest.mark.parametrize(
    "started_on",
    [
        pytest.param({}, id="nothing-recorded"),
        pytest.param({"source_thread": THREAD}, id="no-channel"),
        pytest.param({"source_channel": "slack"}, id="no-thread"),
        pytest.param(arrived_on(THREAD, COLLEAGUE, "teams"), id="another-channel"),
        pytest.param(arrived_on(ELSEWHERE, COLLEAGUE, "slack"), id="another-thread"),
    ],
)
def test_a_start_that_is_not_the_threads_is_not_posted_there(gateway, slack, started_on):
    """🔴 Red before: a start that cannot be read, or that names another channel or thread, was
    posted into the thread. An unreadable origin counts as not the thread."""
    _ended(gateway, _helper(started_on))
    assert SHARED not in _replied_in(slack)
    assert _replied_in(slack) == [SLACK_DM]


def test_in_her_own_dm_the_reply_is_posted_in_its_thread(gateway, slack):
    """Her own DM with PersonalClaw is hers alone: the reply to work she started from the
    dashboard is posted in the session's thread there."""
    _ended(gateway, _helper(dict(DASHBOARD_SOURCE)), channel=SLACK_DM)
    assert _replied_in(slack) == [SLACK_DM]
    assert slack.deliver_subagent_reply.await_args.args[2] == THREAD


def test_a_batch_answers_in_the_thread_only_when_every_helper_started_there(gateway, slack):
    """One reply answers a batch: it goes into the thread only when every helper was started
    from the thread's own message, else to her DM."""
    here = _helper(arrived_on(THREAD, COLLEAGUE, "slack"))
    other = _helper(dict(DASHBOARD_SOURCE))
    other.id = "o2t3h4e5"

    async def _run() -> None:
        await gateway._announce_subagents([here, other])
        await asyncio.gather(*list(gateway._background_tasks))

    asyncio.run(_run())
    assert _replied_in(slack) == [SLACK_DM]


# ── where the start is recorded ───────────────────────────────────────────────────────────────


def test_a_spawn_records_where_the_turn_that_started_it_came_from():
    """The helper keeps the source of the message whose turn started it, as that turn names it
    (``memory_writes.started_on``), and a restart's record keeps it too."""
    from personalclaw import memory_writes, subagent_waiting

    source = arrived_on(THREAD, COLLEAGUE, "slack")
    with memory_writes.derived_from(THREAD):
        memory_writes.started_on(source)
        assert memory_writes.origin_for(THREAD) == source
    assert memory_writes.origin_for(THREAD) == {}, "the turn's mark ends with it"

    helper = _helper(source)
    request = subagent_waiting._request_of(helper)
    kept = subagent_waiting.info_of(
        {"id": helper.id, "task": helper.task, "parent_session": THREAD, "request": request}
    )
    assert kept is not None and kept.started_on == source


def test_a_spawn_in_a_turn_records_its_origin(monkeypatch):
    """A spawn made while the turn runs records the turn's origin on the helper."""
    from personalclaw import memory_writes
    from personalclaw.subagent import SubagentManager

    source = arrived_on(THREAD, COLLEAGUE, "slack")
    manager = SubagentManager(sessions=MagicMock(), ctx_builder=MagicMock())
    monkeypatch.setattr(manager, "_enter", lambda info: info)
    monkeypatch.setattr(
        "personalclaw.subagent.admission", lambda _task, _key, agent, *_a: ("", "", agent)
    )
    with memory_writes.derived_from(THREAD):
        memory_writes.started_on(source)
        info = manager.spawn("Find the landlord's number", parent_session_key=THREAD)
    assert info is not None and info.started_on == source


def test_a_reports_row_carries_where_its_helpers_started():
    """The row a report starts its chat's turn with says where every helper's start came from,
    and says nothing when they differ."""
    from personalclaw.subagent_endings import report_meta
    from personalclaw.turn_source import ASKED_FOR_BY, STARTED_ON

    here = arrived_on(THREAD, COLLEAGUE, "slack")
    assert report_meta({}, [_helper(here)]) == {STARTED_ON: here}
    assert report_meta({}, [_helper(here), _helper(dict(DASHBOARD_SOURCE))]) is None
    assert report_meta(here, [_helper({})]) == {ASKED_FOR_BY: here}
