"""A named agent's own instructions reach its model wherever it runs, read in one place.

Measured on the real assembly: an agent a chat's spawn, a workflow step, an automation or an app's
agent run named was handed the Background prompt and none of its own instructions; a webhook on an
agent defined in ``config.json`` was handed the safety rules alone, because the assembler read
agents' prompts only from the agent files; a room member was handed its role line alone; the
heartbeat on a default agent with instructions of its own was handed the Background prompt;
``personalclaw chat`` on such an agent was handed the safety rules alone; an agent made from a
marketplace definition had its instructions written to a file nothing read, and a definition's
test was handed them cut to 2,000 characters; and a chat or a loop on an agent ``config.json`` does
not hold could be handed the default agent's instructions and voice in place of its own.

Every one of them now reads the agent's instructions and voice where the others do
(``agents.instructions.agent_instructions``), and the platform's safety rules follow them, once.
Each test drives the path's real assembly into a real ``NativeAgentRuntime`` and reads what its
scripted model was handed. The fixture agent's instructions carry a sentence no prompt of the
product's carries, and its voice a line of its own, so each is counted where it lands. Which
paths exist at all is the census's business (``test_agent_safety_rules_census.py``).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from test_every_agent_is_handed_the_safety_rules import (
    _builder,
    _carries_the_rules_once,
    _Chat,
    _Model,
    _runtime,
    _Sessions,
    _text,
    _where,
)

from personalclaw.config import loader as config_loader

AGENT = "map-keeper"
#: A sentence no prompt of the product's carries: where it lands is where the instructions did.
SENTENCE = "Mark every place you name with its grid square, as in [C4]."
INSTRUCTIONS = f"You keep the neighbourhood map for the residents' association.\n\n{SENTENCE}"
VOICE = "Dry and exact, like a surveyor's field notebook."
ASKED = "where should the new bench by the pond go"
#: The opening of the Background prompt, which an agent's own instructions replace.
BACKGROUND = "You are running in a BACKGROUND context"
#: The sub-agent framing an unnamed spawn is handed, and a named one is not.
HELPER = "You are a focused sub-agent"


def _install(*, default: str = "PersonalClaw", agents: dict[str, dict] | None = None) -> None:
    """An install with the default native agent (no instructions of its own), the fixture agent
    in ``config.json``, and *agents*, the default agent being *default*."""
    data = {
        "agent": {"bot_name": "Aide"},
        "default_agent": default,
        "agents": {
            "PersonalClaw": {"provider": "native", "system_prompt": "", "source": "builtin"},
            AGENT: {"provider": "native", "system_prompt": INSTRUCTIONS, "voice": VOICE},
            **(agents or {}),
        },
    }
    config_loader.config_dir()  # where the file is: finding it makes nothing
    config_loader.config_path().write_text(json.dumps(data), encoding="utf-8")


def _agent_file(name: str, fields: dict) -> None:
    """An agent that exists only as its file under ``<home>/agents``."""
    from personalclaw.agent import agents_dir

    agents_dir().mkdir(parents=True, exist_ok=True)
    (agents_dir() / f"{name}.json").write_text(json.dumps({"name": name, **fields}), "utf-8")


def _runs_on_the_instructions(request: list[dict]) -> str:
    """*request* carries the fixture agent's voice and instructions, once each, and the platform's
    safety rules after them, once, and nothing of the Background prompt. Answers its text."""
    text = _carries_the_rules_once(request)
    assert text.count(SENTENCE) == 1, f"the instructions appear {text.count(SENTENCE)} times"
    assert text.count(VOICE) == 1, f"the voice appears {text.count(VOICE)} times"
    assert text.index(VOICE) < text.index(SENTENCE) < _where(text), "voice, instructions, rules"
    assert BACKGROUND not in text, "the Background prompt rode along"
    return text


# ── an agent no chat runs ────────────────────────────────────────────────────────────────────


def _spawned(tmp_path: Path, *, parent_agent: str = "", **spawn) -> list[dict]:
    """The first request a spawned agent's model is handed: the real manager, assembler and
    runtime. *parent_agent* is the agent the session that spawned it runs."""
    from personalclaw.subagent import SubagentManager

    sessions = _Sessions(tmp_path)
    manager_sessions = sessions.manager()
    manager_sessions.get_agent = MagicMock(return_value=parent_agent)
    manager = SubagentManager(
        sessions=manager_sessions, ctx_builder=_builder(tmp_path), is_yolo=lambda: True
    )

    async def go() -> None:
        with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
            info = manager.spawn(ASKED, **spawn)
            assert info is not None and not info.error, getattr(info, "error", "")
            await manager._tasks[info.id]
            await manager.flush_deliveries()

    asyncio.run(go())
    (model,) = sessions.models.values()
    return model.requests[0]


@pytest.mark.parametrize(
    "spawn",
    [
        pytest.param({"agent": AGENT}, id="a-chats-spawn"),
        pytest.param(
            {
                "agent": AGENT,
                "parent_session_key": "workflow:run-1:survey",
                "parent_run": "workflow:run-1",
                "approval_mode": "auto",
                "silent": True,
            },
            id="a-workflow-stage",
        ),
        pytest.param(
            {
                "agent": AGENT,
                "parent_session_key": "app:map-notes",
                "app": "map-notes",
                "silent": True,
            },
            id="an-apps-agent-run",
        ),
        pytest.param(
            {"agent": AGENT, "trigger_id": "trg-map", "title": "Pond survey"},
            id="an-automations-agent",
        ),
    ],
)
def test_a_spawn_that_names_an_agent_runs_on_its_instructions(tmp_path, spawn):
    _install()
    sent = _runs_on_the_instructions(_spawned(tmp_path, **spawn))
    assert ASKED in sent and HELPER not in sent


@pytest.mark.parametrize(
    ("default", "parent_agent"),
    [
        pytest.param("PersonalClaw", AGENT, id="started-by-an-agents-chat"),
        pytest.param(AGENT, "", id="the-default-agent-has-instructions"),
    ],
)
def test_an_unnamed_spawn_stays_a_focused_helper(tmp_path, default, parent_agent):
    """A spawn that names no agent is a helper on the runtime of whatever started it, framed as a
    sub-agent on the Background prompt, and handed no agent's own instructions: handed its
    parent's, a goal loop worker's helper would be told to run the loop's cycle protocol itself,
    and handed the default agent's, a helper working for another agent would be told it is the
    default one."""
    _install(default=default)
    sent = _carries_the_rules_once(_spawned(tmp_path, parent_agent=parent_agent))
    assert BACKGROUND in sent and HELPER in sent
    assert SENTENCE not in sent and VOICE not in sent


@pytest.mark.asyncio
async def test_a_webhook_on_an_agent_defined_in_config_runs_on_its_instructions(tmp_path):
    from personalclaw.dashboard.handlers.hooks import _run_hook_inner

    _install()
    sessions = _Sessions(tmp_path)
    manager = sessions.manager()
    manager.record_success = MagicMock()
    state = SimpleNamespace(sessions=manager, context_builder=_builder(tmp_path))
    await _run_hook_inner(state, "hook:pond-survey", ASKED, AGENT)
    _runs_on_the_instructions(sessions.models[AGENT].requests[0])


@pytest.mark.asyncio
async def test_a_webhook_on_an_agent_defined_by_its_file_runs_on_its_file(tmp_path):
    """The agent files are a store an agent can live in too: an inline prompt, and one the file
    points at."""
    from personalclaw.dashboard.handlers.hooks import _run_hook_inner

    _install()
    prompt_file = tmp_path / "surveyor.md"
    prompt_file.write_text(INSTRUCTIONS, encoding="utf-8")
    _agent_file("surveyor", {"prompt": f"file://{prompt_file}", "voice": VOICE})
    sessions = _Sessions(tmp_path)
    manager = sessions.manager()
    manager.record_success = MagicMock()
    state = SimpleNamespace(sessions=manager, context_builder=_builder(tmp_path))
    await _run_hook_inner(state, "hook:pond-survey", ASKED, "surveyor")
    _runs_on_the_instructions(sessions.models["surveyor"].requests[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("own", [True, False], ids=["its-own-instructions", "none-of-its-own"])
async def test_the_heartbeat_runs_on_the_default_agents_instructions(tmp_path, own):
    """A heartbeat task runs as the default agent: on its own instructions when it has them, as a
    chat on it does, and on the Background prompt when it has none."""
    from test_gateway import _make_orchestrator

    _install(default=AGENT if own else "PersonalClaw")
    sessions = _Sessions(tmp_path)
    orch = _make_orchestrator()
    orch.sessions = sessions.manager()
    orch.ctx_builder = _builder(tmp_path)
    orch.consolidator = MagicMock()
    orch.dashboard_state = None
    orch._deliver_result = AsyncMock()
    await orch._run_heartbeat_task("check the map for the new bench", "")
    (model,) = sessions.models.values()
    if own:
        _runs_on_the_instructions(model.requests[0])
    else:
        sent = _carries_the_rules_once(model.requests[0])
        assert BACKGROUND in sent and SENTENCE not in sent


# ── a chat, a loop ───────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("app", ["", "loop"], ids=["a-chat", "a-loop-worker"])
async def test_an_agent_defined_by_its_file_runs_on_its_own_not_the_default_agents(tmp_path, app):
    """An agent ``config.json`` does not hold was handed the DEFAULT agent's instructions and voice
    (the runtime bindings answer for such a name with the default agent's profile), in place of the
    instructions its own file holds."""
    _install(
        default="owner-agent", agents={"owner-agent": {"system_prompt": "You are Ada's aide."}}
    )
    _agent_file("surveyor", {"system_prompt": INSTRUCTIONS, "voice": VOICE})
    chat = _Chat(tmp_path)
    await chat.send(chat.session("chat-surveyor", agent="surveyor", app=app))
    sent = _runs_on_the_instructions(chat.requests("surveyor")[0])
    assert "Ada's aide" not in sent, "the default agent's instructions ran instead"


@pytest.mark.asyncio
async def test_a_loop_worker_on_an_agent_runs_on_its_instructions(tmp_path):
    _install()
    chat = _Chat(tmp_path)
    await chat.send(chat.session("loop-pond", agent=AGENT, app="loop"))
    _runs_on_the_instructions(chat.requests(AGENT)[0])


@pytest.mark.asyncio
async def test_an_agent_made_from_a_marketplace_definition_runs_on_its_instructions(tmp_path):
    """A definition made through its route and activated is one of your agents, its instructions
    and voice included: the route dropped the voice, and activating wrote the instructions to a
    file beside the definition that nothing read."""
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers.agent_marketplace import (
        api_agent_marketplace_activate,
        api_agent_marketplace_create,
    )

    _install()
    create = make_mocked_request("POST", "/api/agent-marketplace/agents", app=web.Application())

    async def _definition() -> dict:
        return {"name": "pond-guide", "system_prompt": INSTRUCTIONS, "voice": VOICE}

    create.json = _definition  # type: ignore[method-assign]
    created = await api_agent_marketplace_create(create)
    assert created.status == 201, created.text
    request = make_mocked_request(
        "POST",
        "/api/agent-marketplace/agents/pond-guide/activate",
        match_info={"name": "pond-guide"},
        app=web.Application(),
    )
    response = await api_agent_marketplace_activate(request)
    assert response.status == 200, response.text

    chat = _Chat(tmp_path)
    await chat.send(chat.session("chat-pond-guide", agent="pond-guide"))
    _runs_on_the_instructions(chat.requests("pond-guide")[0])


@pytest.mark.asyncio
async def test_a_marketplace_definitions_test_is_handed_its_whole_instructions(tmp_path):
    """A definition's one-turn test is handed what an agent activated from it runs on: its
    instructions whole and its voice. They were cut to 2,000 characters, the voice left out."""
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.agents.marketplace import AgentDefinition, get_default_agent_registry
    from personalclaw.dashboard.handlers.agent_marketplace import api_agent_marketplace_test

    _install()
    long = "Survey notes are filed by street, oldest first.\n" * 60 + INSTRUCTIONS
    assert len(long) > 2_000
    get_default_agent_registry().get("local").create(
        AgentDefinition(name="pond-guide", system_prompt=long, voice=VOICE)
    )
    sessions = _Sessions(tmp_path)
    app = web.Application()
    app["state"] = SimpleNamespace(sessions=sessions.manager())
    request = make_mocked_request(
        "POST",
        "/api/agent-marketplace/agents/pond-guide/test",
        match_info={"name": "pond-guide"},
        app=app,
    )
    response = await api_agent_marketplace_test(request)
    assert response.status == 200, response.text
    (model,) = sessions.models.values()
    sent = _text(model.requests[0])
    assert sent.count(SENTENCE) == 1 and sent.count(VOICE) == 1
    assert sent.index(VOICE) < sent.index(SENTENCE)


# ── a room member ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_room_member_opens_with_its_agents_instructions(tmp_path, monkeypatch):
    from personalclaw.config.loader import AgentProfile, AppConfig
    from personalclaw.rooms import store, turn

    cfg = AppConfig()
    cfg.rooms.enabled = True
    cfg.agents = {AGENT: AgentProfile(system_prompt=INSTRUCTIONS, voice=VOICE)}
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, *a, **k: cfg))
    room = store.create_room("Where does the new bench go?")
    store.add_member(room.id, AGENT, role_blurb="the map keeper")
    store.append_message(room.id, role="user", content="Start with the pond.", speaker="")
    sessions = _Sessions(tmp_path)

    await turn.run_member_turn(sessions, room.id, AGENT)
    first = _runs_on_the_instructions(sessions.models[AGENT].requests[0])
    assert first.index(SENTENCE) < first.index(f'You are "{AGENT}", a member of the room')
    assert _where(first) < first.index("<untrusted_content"), "the rules ahead of the fence"

    store.append_message(room.id, role="user", content="Now the playground.", speaker="")
    await turn.run_member_turn(sessions, room.id, AGENT)
    later = _text(sessions.models[AGENT].requests[-1])
    assert "Now the playground." in later
    assert later.count(SENTENCE) == 1, "the session keeps them; a slice does not repeat them"


# ── the terminal chat ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_terminal_chat_runs_on_the_default_agents_instructions(tmp_path):
    from personalclaw import cli_chat

    _install(default=AGENT)
    model = _Model()
    runtime = await _runtime(AGENT, model, tmp_path)
    factory = MagicMock(return_value=runtime)
    with patch.object(config_loader.AppConfig, "create_provider_factory", return_value=factory):
        await cli_chat._chat("where should the bench go", None)
    assert factory.call_args.kwargs["agent"] == AGENT, "the runtime is the default agent's"
    _runs_on_the_instructions(model.requests[0])
