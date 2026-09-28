"""An agent a trigger starts is named by its trigger, or by the instruction it was given.

A trigger's `run-prompt` or `invoke-agent` action spawns an agent whose task opens with the
unattended-run framing, and the run had no name of its own: the background-agents list named it by
its task (the framing), and its completion arrived as "Subagent `<id>` completed" with the reminder
it carried at the end of the body. The run now carries a title — the trigger's name, else the first
line of the instruction — which the list, the completion notice and a restart's notice lead with.
The notice itself is `tests/test_gateway.py::TestSubagentDone`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.subagent import SubagentInfo, SubagentManager
from personalclaw.triggers import store as trigger_store


class TestRunTitle:
    def test_the_triggers_name_wins(self) -> None:
        with patch.object(trigger_store, "trigger_name", return_value="Call the dentist"):
            assert (
                trigger_store.run_title("clock:dentist", "Remind me to call") == "Call the dentist"
            )

    def test_an_unnamed_trigger_lends_its_instructions_first_line(self) -> None:
        with patch.object(trigger_store, "trigger_name", return_value=""):
            assert (
                trigger_store.run_title("clock:x", "\n  Remind me to call the dentist\nat 5pm")
                == "Remind me to call the dentist"
            )

    def test_a_long_instruction_is_cut_and_says_so(self) -> None:
        with patch.object(trigger_store, "trigger_name", return_value=""):
            title = trigger_store.run_title("clock:x", "word " * 40)
        assert len(title) == trigger_store.RUN_TITLE_CHARS
        assert title.endswith("…")

    def test_nothing_to_name_it_by_is_no_title(self) -> None:
        with patch.object(trigger_store, "trigger_name", return_value=""):
            assert trigger_store.run_title("", "") == ""


class _Spawns:
    """The action services' subagent manager, recording what each spawn was asked. Each spawn
    starts, as the manager's does: it returns the agent it scheduled."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def spawn(self, **kwargs: Any) -> SubagentInfo:
        self.calls.append(kwargs)
        return SubagentInfo(id=f"run{len(self.calls):05d}", task=kwargs.get("task", ""))


def _services(spawns: _Spawns) -> Any:
    services = MagicMock()
    services.subagents = spawns
    return services


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "config"),
    [
        ("run_prompt_provider", {"message": "Remind me to call the dentist"}),
        ("invoke_agent_provider", {"task_template": "Remind me to call the dentist"}),
    ],
    ids=["run-prompt", "invoke-agent"],
)
async def test_a_trigger_action_names_the_agent_it_starts(provider: str, config: dict) -> None:
    """🔴 Red before: neither action passed a title, so the agent was named by its framed task."""
    import importlib

    module = importlib.import_module(f"personalclaw.action_providers.{provider}")
    spawns = _Spawns()
    ctx = ActionContext(event="trigger.fired", payload={}, trigger_id="clock:call-the-dentist")
    with (
        patch.object(module, "get_action_services", return_value=_services(spawns)),
        patch.object(trigger_store, "trigger_name", return_value="Call the dentist"),
    ):
        result = await module.create_provider().execute(dict(config), ctx)

    assert result.success, result.error
    (sent,) = spawns.calls
    assert sent["title"] == "Call the dentist"
    assert sent["trigger_id"] == "clock:call-the-dentist"


@pytest.mark.asyncio
async def test_the_background_agents_list_carries_each_runs_title() -> None:
    """🔴 Red before: `/api/spawn` carried only the task, so the list could only name the run by
    its framing."""
    import json

    from personalclaw.dashboard.handlers.messaging import api_spawn_list

    subagents = MagicMock()
    subagents.all_agents = [
        SubagentInfo(id="rem00001", task="framed", title="Call the dentist"),
        SubagentInfo(id="plain001", task="Summarize the thread"),
    ]
    request = MagicMock()
    request.app = {"state": MagicMock(subagents=subagents)}

    body = json.loads((await api_spawn_list(request)).body)
    titles = {row["id"]: row["title"] for row in body["agents"]}
    assert titles == {"rem00001": "Call the dentist", "plain001": ""}


def test_the_spawned_run_carries_its_title_and_lists_it() -> None:
    """The manager keeps the title on the run (redacted like its task) and the running list, which
    the chat's live subagent status reads, carries it."""
    manager = SubagentManager(sessions=MagicMock(), ctx_builder=MagicMock())
    info = SubagentInfo(id="rem00001", task="framed", title="Call the dentist")
    manager._agents[info.id] = info
    (row,) = manager.running_agents_for("")
    assert row["title"] == "Call the dentist"
