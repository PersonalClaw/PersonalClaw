"""A note about background agent work that failed names what failed and why, and says it once.

An agent nobody named (one a chat's agent started, or one started through ``/api/spawn``) ended as
"Subagent `<id>` failed", its body the event its parent's model is handed: "[Subagent completion
event]", the id again, the task cut at 100 characters, and only then the reason. And the same
failure told again each time it happened: a task its chat started again and again, failing the same
way, put one more identical note in the bell every time. A trigger whose owner asked it to
"Collapse repeat failures" kept quiet about a repeat on its own route, and the agent's plain note
then went out in its place.

Now every note is named by its run (its title, else the first line of its task), its body is what
happened, and a failure already told is not told again within the hour: the window an automation's
"Collapse repeat failures" re-alerts on.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from test_gateway import _make_orchestrator, _mock_dashboard_state, _mock_sessions

from personalclaw import notification_kinds
from personalclaw.subagent import SubagentInfo
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore

TRIGGER_ID = "clock:nightly-digest"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def _on_done():
    """The gateway's real subagent completion callback, over a mock dashboard state."""
    orch = _make_orchestrator()
    orch.sessions = _mock_sessions()
    orch.ctx_builder = MagicMock()
    orch.ctx_builder.hooks = MagicMock()
    orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
    orch.dashboard_state = _mock_dashboard_state()
    with (
        patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
        patch("personalclaw.gateway.SubagentManager") as manager,
    ):
        manager.return_value = MagicMock(running=[], get=MagicMock(return_value=None))
        orch._init_subagents()
    return orch, manager.call_args[1]["on_done"]


def _agent(agent_id: str = "5f3a9c21", *, error: str = "", **over) -> SubagentInfo:
    fields = {
        "id": agent_id,
        "task": "Summarize the overnight build logs\nList each failing job with its first error.",
        "done": True,
        "result": "" if error else "All four jobs passed.",
        "error": error,
        "parent_session_key": "",
        **over,
    }
    return SubagentInfo(**fields)


def _notes(orch) -> list[tuple[str, str, str]]:
    """Every note that went out, as (kind, title, body)."""
    out = []
    for call in orch.dashboard_state.notify.call_args_list:
        args, kwargs = call
        out.append(
            (
                kwargs.get("kind", args[0] if args else ""),
                kwargs.get("title", args[1] if len(args) > 1 else ""),
                kwargs.get("body", args[2] if len(args) > 2 else ""),
            )
        )
    return out


@pytest.mark.asyncio
async def test_a_failed_run_nobody_named_says_what_failed_and_why(home):
    orch, on_done = _on_done()

    await on_done([_agent(error="the model provider answered 503")])

    ((kind, title, body),) = _notes(orch)
    assert kind == notification_kinds.SUBAGENT
    assert title == "Summarize the overnight build logs — failed"
    assert body == "Error: the model provider answered 503"


@pytest.mark.asyncio
async def test_a_finished_run_nobody_named_is_named_by_its_task(home):
    orch, on_done = _on_done()

    await on_done([_agent()])

    ((_kind, title, body),) = _notes(orch)
    assert title == "Summarize the overnight build logs"
    assert body == "All four jobs passed."


@pytest.mark.asyncio
async def test_the_same_failure_again_is_not_told_again(home):
    orch, on_done = _on_done()

    for agent_id in ("5f3a9c21", "0a1b2c3d", "7e8f9a0b"):
        await on_done([_agent(agent_id, error="the model provider answered 503")])

    assert [title for _k, title, _b in _notes(orch)] == [
        "Summarize the overnight build logs — failed"
    ]


@pytest.mark.asyncio
async def test_a_chat_hears_every_failure_and_the_bell_one_note(home):
    """A chat's agent that starts the same task again after each failure: every failure reaches the
    chat, whose agent is the one deciding what to do next, and one note reaches the bell."""
    from unittest.mock import AsyncMock

    orch, on_done = _on_done()
    session = MagicMock(running=False, task=None, key="kitchen", mode="")
    session._owed_subagent_endings = []
    orch.dashboard_state.get_session = MagicMock(return_value=session)

    with patch("personalclaw.gateway.run_chat", new_callable=AsyncMock) as chat_turn:
        for agent_id in ("5f3a9c21", "0a1b2c3d", "7e8f9a0b"):
            await on_done(
                [
                    _agent(
                        agent_id,
                        error="the model provider answered 503",
                        parent_session_key="dashboard:kitchen",
                    )
                ]
            )

    assert chat_turn.call_count == 3, "a failure the chat was not told of"
    assert [title for _k, title, _b in _notes(orch)] == [
        "Summarize the overnight build logs — failed"
    ]


@pytest.mark.asyncio
async def test_a_new_reason_is_told(home):
    orch, on_done = _on_done()

    await on_done([_agent(error="the model provider answered 503")])
    await on_done([_agent("0a1b2c3d", error="the model provider refused the key")])

    assert [body for _k, _t, body in _notes(orch)] == [
        "Error: the model provider answered 503",
        "Error: the model provider refused the key",
    ]


@pytest.mark.asyncio
async def test_a_failure_still_happening_an_hour_on_is_told_again(home):
    from personalclaw.triggers import delivery

    orch, on_done = _on_done()
    clock = [1_000_000.0]
    with patch("personalclaw.subagent_notes.time.time", side_effect=lambda: clock[0]):
        await on_done([_agent(error="the model provider answered 503")])
        clock[0] += delivery.FAILURE_REMINDER_SECS - 1
        await on_done([_agent("0a1b2c3d", error="the model provider answered 503")])
        clock[0] += 2
        await on_done([_agent("7e8f9a0b", error="the model provider answered 503")])

    assert len(_notes(orch)) == 2, "still broken an hour on: told again, as the trigger's is"


@pytest.mark.asyncio
async def test_a_failure_that_ended_with_success_is_told_again_next_time(home):
    """A success between two failures: the second failure is news, not a repeat."""
    orch, on_done = _on_done()

    await on_done([_agent(error="the model provider answered 503")])
    await on_done([_agent("0a1b2c3d")])
    await on_done([_agent("7e8f9a0b", error="the model provider answered 503")])

    assert [title for _k, title, _b in _notes(orch)] == [
        "Summarize the overnight build logs — failed",
        "Summarize the overnight build logs",
        "Summarize the overnight build logs — failed",
    ]


@pytest.mark.asyncio
async def test_a_batch_names_each_run_and_how_it_ended(home):
    orch, on_done = _on_done()

    await on_done(
        [
            _agent(error="the model provider answered 503"),
            _agent("0a1b2c3d", task="Check the release notes for typos"),
        ]
    )

    ((_kind, title, body),) = _notes(orch)
    assert title == "2 agents finished — 1 failed"
    assert body == (
        "Summarize the overnight build logs — failed\nError: the model provider answered 503"
        "\n\nCheck the release notes for typos\nAll four jobs passed."
    )


def _store_trigger(home, **over) -> None:
    trigger = Trigger(
        id=TRIGGER_ID,
        name="Nightly digest",
        kind="clock",
        spec={"kind": "interval", "interval_secs": 600},
        capabilities={"providers": ["run-prompt"]},
        workflow={"inline": {"provider": "run-prompt", "config": {"message": "Digest."}}},
        delivery="inbox",
        **over,
    )
    TriggerStore(base_dir=home).upsert(trigger)


@pytest.mark.asyncio
async def test_collapse_repeat_failures_is_kept_for_an_agent_run(home):
    """🔴 The trigger's route kept quiet about the repeat, and the plain note said it instead."""
    _store_trigger(home, failure_policy={"dedupe_hash": True})
    orch, on_done = _on_done()

    for agent_id in ("5f3a9c21", "0a1b2c3d", "7e8f9a0b"):
        run = _agent(agent_id, error="the model provider answered 503", trigger_id=TRIGGER_ID)
        run.title = "Nightly digest"
        await on_done([run])

    assert [(kind, title) for kind, title, _b in _notes(orch)] == [
        (notification_kinds.CRON_FAILED, "Nightly digest failed")
    ]


@pytest.mark.asyncio
async def test_without_collapse_every_failed_fire_is_still_told(home):
    """The control: an automation whose owner did not ask to collapse repeats is told each time,
    on its own route, and only there."""
    _store_trigger(home)
    orch, on_done = _on_done()

    for agent_id in ("5f3a9c21", "0a1b2c3d"):
        run = _agent(agent_id, error="the model provider answered 503", trigger_id=TRIGGER_ID)
        run.title = "Nightly digest"
        await on_done([run])

    assert [(kind, title) for kind, title, _b in _notes(orch)] == [
        (notification_kinds.CRON_FAILED, "Nightly digest failed"),
        (notification_kinds.CRON_FAILED, "Nightly digest failed"),
    ]
