"""Whether anybody watches a loop's work is the loop's Mode, under whichever key a check reads it.

A loop's sessions (its stage worker, a worker per task, its planner) run as dashboard chats, so the
native runtime binds a worker's tools to the key a chat's provider layer names its work by,
``dashboard:loop-<id>``. Every check that asks whether anybody is watching read that key as a
chat's: an Unattended loop's worker could stop or restart PersonalClaw, resolved the safety profile
of a watched chat, acted at the rung a watched chat gets, and a subagent it started reported under
the rules for a chat someone is in. Its bare key read the other way, as unattended whatever its
loop's Mode said.

Each test drives the keys the product mints for a loop's sessions through a check that judges
them. An Unattended loop's are judged unattended in both forms. The control: an Attended loop's stay
watched, so its worker still puts its questions to its owner and the check of an unverified agent
CLI does not refuse it. A key whose loop cannot be read is judged unattended, and no loop's session
passes for the person a room asks or for a chat the Inbox can ask to try again.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from personalclaw import mcp_core
from personalclaw.acp.client import AcpClient
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.agents.runners import UnverifiedAdapterError
from personalclaw.config import loader
from personalclaw.constants import dashboard_session_key
from personalclaw.gateway import announce_profile
from personalclaw.guardrails import autonomy as au
from personalclaw.guardrails import rungs as rg
from personalclaw.guardrails.denylist import check_command
from personalclaw.guardrails.policy import is_unattended_session, profile_for_session
from personalclaw.loop import manager as loop_manager
from personalclaw.loop import store as loop_store
from personalclaw.loop.loop import Loop
from personalclaw.loop.plan_walkthrough import planner_session_key
from personalclaw.session import SessionManager

#: The sentence the self-stop check refuses a command that would stop PersonalClaw with.
STOPS_PERSONALCLAW = "it would stop the PersonalClaw gateway that is executing it"

#: A task a loop's scheduler gives a worker of its own.
TASK = "t-2b9c41fe"

#: An agent CLI no catalog row names, so its adapter cannot be verified.
UNVERIFIED_CLI = "acp:not-a-cataloged-runner"


def _loop(*, attended: bool) -> Loop:
    """A loop started in the Mode *attended* names, as the loop page creates one."""
    return loop_store.create(
        Loop(id="", name="Release notes", kind="goal", task="draft them", attended=attended)
    )


def _sessions_of(loop: Loop) -> list[str]:
    """Every key a session of *loop* runs under: its stage worker's, a task worker's and its
    planner's, each bare (the chat's own name) and as the runtime binds its tools to it."""
    bare = [
        loop_manager.session_key(loop.id),
        loop_manager.task_session_key(loop.id, TASK),
        planner_session_key(loop.id),
    ]
    return [form for key in bare for form in (key, dashboard_session_key(key))]


def _worker(loop: Loop) -> str:
    """The key the runtime binds a loop's stage worker's tools to (``chat_runner.run_chat``)."""
    return dashboard_session_key(loop_manager.session_key(loop.id))


def _verified_adapters_required() -> None:
    """Settings → Agents: an unattended run starts only on an agent CLI whose adapter verifies."""
    path = loader.config_dir() / "config.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data.setdefault("agent", {})["unattended_requires_verified_adapter"] = True
    path.write_text(json.dumps(data))


def _refused_by_its_bash_tool(key: str, workspace: Path, command: str):
    """What the agent's bash tool refuses *command* with inside the dispatch of a native runtime
    whose session is *key*, the scope that binds its tools to it (``None`` when it would run).
    Nothing is run."""
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="loop-worker", provider="native", model="any"),
        model_provider=None,  # type: ignore[arg-type]
        cwd=workspace,
        session_key=key,
        unattended=True,
    )
    tools = NativeBuiltinToolProvider(workspace)
    with runtime._dispatch_scope():
        assert mcp_core.get_current_session_key() == key
        return tools._bash_refusal(command, [], command)


# ── an Unattended loop's work is work nobody watches ───────────────────────────────────────


def test_no_session_of_an_unattended_loop_can_stop_personalclaw():
    loop = _loop(attended=False)

    for key in _sessions_of(loop):
        held = check_command("personalclaw stop", session_key=key)
        assert held.blocked is True, key
        assert STOPS_PERSONALCLAW in held.refusal(), (key, held.refusal())


def test_an_unattended_loops_worker_is_refused_it_by_its_own_bash_tool(tmp_path):
    refused = _refused_by_its_bash_tool(
        _worker(_loop(attended=False)), tmp_path, "personalclaw stop"
    )

    assert refused is not None and STOPS_PERSONALCLAW in refused.error, refused


def test_an_unattended_loops_work_resolves_the_profile_of_work_nobody_watches():
    loop = _loop(attended=False)

    for key in _sessions_of(loop):
        assert is_unattended_session(key) is True, key
        assert profile_for_session(key).name == "headless", key


def test_an_action_an_unattended_loop_fires_is_held_to_the_ladders_ceiling():
    """An action that can be undone runs with its undo kept, never silently, as any other run
    nobody watches does."""
    rg.ensure_core_action_types()

    route = rg.route_provider_action("create-task", session_key=_worker(_loop(attended=False)))

    assert route.rung == au.RUNG_AUTO_WITH_UNDO, route
    assert route.route == rg.ROUTE_EXECUTE_WITH_UNDO


def test_a_subagent_an_unattended_loop_started_reports_under_the_rules_for_work_nobody_watches():
    profile = announce_profile(_worker(_loop(attended=False)), None)

    assert profile is not None and profile.name == "headless", profile


def test_an_unattended_loops_key_alone_is_refused_an_unverified_agent_cli():
    _verified_adapters_required()

    with pytest.raises(UnverifiedAdapterError):
        SessionManager._guard_unattended_runner(
            _worker(_loop(attended=False)), None, {"provider_kind": UNVERIFIED_CLI}
        )


def test_an_unattended_loops_agent_cli_is_not_told_its_owner_answers(tmp_path):
    client = AcpClient(work_dir=tmp_path, session_key=_worker(_loop(attended=False)))

    assert client._owner_answers() is False


# ── the control: an Attended loop's work is watched ────────────────────────────────────────


def test_an_attended_loops_work_is_watched():
    rg.ensure_core_action_types()
    loop = _loop(attended=True)

    for key in _sessions_of(loop):
        assert is_unattended_session(key) is False, key
        assert profile_for_session(key).name == "interactive", key
        assert check_command("personalclaw stop", session_key=key).blocked is False, key
        assert rg.route_provider_action("create-task", session_key=key).rung == au.RUNG_AUTONOMOUS


def test_an_attended_loops_worker_still_asks_its_owner(tmp_path):
    client = AcpClient(work_dir=tmp_path, session_key=_worker(_loop(attended=True)))

    assert client._owner_answers() is True


def test_an_attended_loops_worker_is_not_refused_an_unverified_agent_cli():
    _verified_adapters_required()

    SessionManager._guard_unattended_runner(
        _worker(_loop(attended=True)), None, {"provider_kind": UNVERIFIED_CLI, "unattended": False}
    )


# ── a key whose loop cannot be read ───────────────────────────────────────────────────────


def test_a_session_of_a_loop_that_does_not_exist_is_judged_unattended():
    key = dashboard_session_key(loop_manager.session_key("0badf00d"))

    assert is_unattended_session(key) is True
    assert check_command("personalclaw stop", session_key=key).blocked is True


def test_a_session_of_a_loop_whose_mode_cannot_be_read_is_judged_unattended(monkeypatch):
    loop = _loop(attended=True)

    def unreadable(_loop_id):
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(loop_store, "get", unreadable)

    for key in _sessions_of(loop):
        assert is_unattended_session(key) is True, key
        assert check_command("personalclaw stop", session_key=key).blocked is True, key


# ── a loop's session is neither the person a room asks nor a chat ────────────────────────────


def test_no_session_of_a_loop_passes_for_the_person_a_room_asks():
    from personalclaw.rooms.posture import agent_shaped_identity

    for attended in (True, False):
        for key in _sessions_of(_loop(attended=attended)):
            assert agent_shaped_identity(key) != "", key


def test_an_attended_loops_denied_call_offers_no_chat_to_ask_again():
    """What a loop's session asks is answered on the loop's page, so a call its approval window
    ran out on is not offered to a chat to ask again, whichever its Mode."""
    from personalclaw.auto_denials import answerable_chat

    for key in _sessions_of(_loop(attended=True)):
        assert answerable_chat(key) == "", key
