"""A chat that works in a folder of its own runs its turns on Settings → Models → Code & tools.

Settings → Models offers Code & tools for "Native agent turns that lean on tool use and code work",
and no chat turn ever ran on it: every turn that was not a loop's took the Chat binding
(``chat_runner.model_axis_for``). So with Code & tools on one provider, to keep her work code
there, and Chat on another for her own chats, a turn that ran the tests in her repository went to
Chat's provider, and nothing said so.

Which binding a turn runs on is decided before its first request, from what the chat already
holds: the folder it works in. A chat working in a folder of its own (the working directory set
for it, its project's folder or its agent's) is the chat whose memory is that folder's
(``memory_locality.is_local_partition``), and its turns resolve on Code & tools: that chain when it
binds a model, Chat's while it binds none. A chat in the workspace, where every chat starts, stays
on Chat. A model picked for the chat still comes first, and each turn names the model that
answered it and where that model was chosen from.

Driven through the real turn engine (``run_chat``), the gateway's session manager over the real
resolution seam and the real native loop; only the two models are scripted. Their names are
invented.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

#: The provider bound for Code & tools (her work account) and the one bound for Chat (a plan).
WORK, WORK_MODEL = "work-models", "deep-coder"
PLAN, PLAN_MODEL = "plan-models", "flat-chat"
WORK_REF, PLAN_REF = f"{WORK}:{WORK_MODEL}", f"{PLAN}:{PLAN_MODEL}"

ASK = "Run the tests in this repository and tell me how many pass."
ANSWER = "12 passed."


class _Model:
    """A model the resolution seam builds for one entry: it answers, and records that it was.

    An agent's loop asks it through ``complete``, which is what ``asked`` records. A chore after
    the turn (its title, its follow-ups) asks it for one answer through ``stream``, which is the
    Background binding's to decide and is not counted here."""

    supports_tools = True

    def __init__(self, entry: str, model: str, asked: list[str]) -> None:
        self.entry = entry
        self.model = model
        self.asked = asked
        self.served_ref = f"{entry}:{model}"

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **_kw: Any):
        self.asked.append(self.entry)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=ANSWER)
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=40, output_tokens=6)

    async def stream(self, message: str):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Test run")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=4, output_tokens=2)


@pytest.fixture
def asked() -> list[str]:
    """Settings → Providers: the two providers, each a scripted model. Returns every entry asked,
    in order."""
    from personalclaw.config.loader import config_path
    from personalclaw.guardrails.breaker import reset_breakers
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.llm.registry import ProviderEntry, ProviderRegistry, set_default_registry

    calls: list[str] = []
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **kwargs: Any):
        return _Model(entry.name, str(kwargs.get("model") or entry.model), calls)

    registry.register_type(
        ProviderCapability(
            type="scripted",
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=True,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=32_768,
        ),
        _factory,
    )
    for name, model in ((WORK, WORK_MODEL), (PLAN, PLAN_MODEL)):
        registry.register_entry(ProviderEntry(name=name, type="scripted", model=model))
    set_default_registry(registry)  # conftest restores the singleton afterwards
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"providers": [{"name": n, "type": "scripted"} for n in (WORK, PLAN)]})
    )
    reset_breakers()
    yield calls
    reset_breakers()


def _bind(**chains: list[str]) -> None:
    """Settings → Models, written the way its PUT writes it: one chain per use case."""
    from personalclaw.providers.use_cases import save_active_models

    save_active_models(chains)


@pytest_asyncio.fixture
async def gateway(tmp_path):
    """The gateway's session manager over the real resolution seam, and the dashboard state a
    chat's turn runs in."""
    from personalclaw.config import AppConfig
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult
    from personalclaw.providers.provider_bridge import create_provider_factory
    from personalclaw.session import SessionManager

    sessions = SessionManager(AppConfig(), provider_factory=create_provider_factory("chat"))
    state = DashboardState(
        sessions=sessions,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path / "sessions"),
    )
    builder = MagicMock()
    builder.hooks.on_tool_call.return_value = ToolHookResult.allow()
    builder.build_message.side_effect = lambda message, *_a, **_k: (message, None)
    state.context_builder = builder
    state._hook_store = MagicMock(fire_for_ids=AsyncMock(return_value=[]))
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    try:
        yield state
    finally:
        await sessions.close_all()


@pytest.fixture
def repo(tmp_path) -> Path:
    """Her repository: a folder of its own, outside the workspace."""
    folder = tmp_path / "src" / "recipe-box"
    folder.mkdir(parents=True)
    return folder


def _chat(state, *, folder: str, pick: str = ""):
    """A chat as ``POST /api/chat/sessions`` makes one, working in *folder*."""
    chat = state.get_or_create_session(workspace_dir=folder, model=pick)
    chat._trust = True
    return chat


def _in_the_workspace() -> str:
    """Where a chat with no folder of its own works: the workspace every chat starts in."""
    from personalclaw.config.loader import default_workspace_dir

    folder = default_workspace_dir()
    assert folder, "premise: the test home has a workspace"
    return folder


async def _turn(state, chat, words: str = ASK) -> dict:
    """One real turn of *chat*; returns the reply."""
    from personalclaw.dashboard.chat_runner import run_chat

    chat.append("user", words, "msg msg-u")
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, chat, words)
    return [m for m in chat.messages if m.get("role") == "assistant"][-1]


def _providers_billed() -> list[str]:
    """The provider each chat turn's usage row names, in order (``usage/turns.jsonl``)."""
    from personalclaw.usage_ledger import _iter_rows

    return [row["provider"] for row in _iter_rows() if row.get("source") == "chat"]


# ── which binding a turn runs on ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_turn_in_a_chat_working_in_its_own_folder_runs_on_code_and_tools(
    asked, gateway, repo
):
    """🔴 Red before: the turn ran on the Chat binding, its usage row named the Chat provider,
    and Code & tools was never asked."""
    _bind(chat=[PLAN_REF], code_tools=[WORK_REF])
    chat = _chat(gateway, folder=str(repo))

    reply = await _turn(gateway, chat)

    assert reply["content"] == ANSWER
    assert asked == [WORK], "the turn did not run on the Code & tools binding"
    assert _providers_billed() == [WORK], "the usage row names another provider"


@pytest.mark.asyncio
async def test_with_code_and_tools_empty_that_turn_runs_on_chat(asked, gateway, repo):
    """Code & tools binds nothing: the turn takes the Chat chain, as the Models page says."""
    _bind(chat=[PLAN_REF])
    chat = _chat(gateway, folder=str(repo))

    await _turn(gateway, chat)

    assert asked == [PLAN]
    assert _providers_billed() == [PLAN]


@pytest.mark.asyncio
async def test_a_chat_in_the_workspace_still_runs_on_chat(asked, gateway):
    """Vacuity control for the first test: the same bindings, a chat with no folder of its own."""
    _bind(chat=[PLAN_REF], code_tools=[WORK_REF])
    chat = _chat(gateway, folder=_in_the_workspace())

    await _turn(gateway, chat)

    assert asked == [PLAN]
    assert _providers_billed() == [PLAN]


@pytest.mark.asyncio
async def test_a_model_picked_for_the_chat_still_wins(asked, gateway, repo):
    """The composer's pick sits above every chain: it serves the folder chat's turn."""
    _bind(chat=[PLAN_REF], code_tools=[WORK_REF])
    chat = _chat(gateway, folder=str(repo), pick=PLAN_REF)

    await _turn(gateway, chat)

    assert asked == [PLAN]
    assert _providers_billed() == [PLAN]


@pytest.mark.asyncio
async def test_a_chat_given_a_folder_runs_its_next_turn_on_code_and_tools(asked, gateway, repo):
    """🔴 Red without the rebuild: the runtime the chat's first turn built was resolved on the
    Chat chain and was reused for the turn after she set its working directory."""
    _bind(chat=[PLAN_REF], code_tools=[WORK_REF])
    chat = _chat(gateway, folder=_in_the_workspace())
    await _turn(gateway, chat)

    chat.workspace_dir = str(repo)  # what POST /api/chat/sessions/{session}/workspace-dir sets
    await _turn(gateway, chat, "And again, please.")

    assert asked == [PLAN, WORK]


# ── the turn says what it ran on ────────────────────────────────────────────────────────────


def _stats_frames(state) -> list[dict]:
    return [
        c.args[1]
        for c in state.broadcast_ws.call_args_list
        if c.args and c.args[0] == "activity_event" and c.args[1].get("kind") == "stats"
    ]


@pytest.mark.parametrize(
    ("bound", "folder", "pick", "model", "chosen"),
    [
        ({"code_tools": [WORK_REF]}, "repo", "", WORK_MODEL, "from your Code & tools chain"),
        ({}, "repo", "", PLAN_MODEL, "from your Chat chain"),
        ({"code_tools": [WORK_REF]}, "workspace", "", PLAN_MODEL, "from your Chat chain"),
        ({"code_tools": [WORK_REF]}, "repo", PLAN_REF, PLAN_MODEL, "picked for this chat"),
    ],
    ids=["code and tools", "code and tools empty", "no folder of its own", "a pick"],
)
@pytest.mark.asyncio
async def test_each_turn_names_the_model_that_answered_and_where_it_was_chosen(
    asked, gateway, repo, bound, folder, pick, model, chosen
):
    """🔴 Red before: nothing on the turn said which binding it ran on, so a binding that was
    passed over read exactly like one that served."""
    _bind(chat=[PLAN_REF], **bound)
    chat = _chat(gateway, folder=str(repo) if folder == "repo" else _in_the_workspace(), pick=pick)

    reply = await _turn(gateway, chat)

    telemetry = reply["meta"]["turn_telemetry"]
    entry = WORK if model == WORK_MODEL else PLAN
    assert (telemetry["model"], telemetry["provider"], telemetry["chosen"]) == (
        model,
        entry,
        chosen,
    )
    assert f" · {model}, {chosen} · " in telemetry["line"], telemetry["line"]
    (live,) = _stats_frames(gateway)
    assert (live["model"], live["chosen"], live["text"]) == (model, chosen, telemetry["line"])


@pytest.mark.asyncio
async def test_the_chat_says_which_chain_its_next_turn_takes(asked, gateway, repo):
    """What the model pill's Auto says before a turn: the chain a turn on Auto resolves on."""
    from personalclaw.dashboard.chat_runner import auto_chain_name

    _bind(chat=[PLAN_REF], code_tools=[WORK_REF])
    in_folder = _chat(gateway, folder=str(repo))
    in_workspace = _chat(gateway, folder=_in_the_workspace())
    assert (auto_chain_name(in_folder), auto_chain_name(in_workspace)) == (
        "Code & tools",
        "Chat",
    )

    _bind(chat=[PLAN_REF])
    assert auto_chain_name(in_folder) == "Chat", "an empty Code & tools uses the Chat chain"


@pytest.mark.asyncio
async def test_the_composer_reads_the_chain_from_the_chat_and_from_setting_its_folder(
    asked, gateway, repo
):
    """The two answers the model pill's Auto is drawn from: the chat's detail, and what setting or
    clearing its working directory answers."""
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from personalclaw.dashboard.chat import (
        api_chat_session_detail,
        api_chat_session_workspace_dir,
    )

    _bind(chat=[PLAN_REF], code_tools=[WORK_REF])
    chat = _chat(gateway, folder=_in_the_workspace())
    app = web.Application()
    app["state"] = gateway
    app.router.add_get("/api/chat/sessions/{session}", api_chat_session_detail)
    app.router.add_post(
        "/api/chat/sessions/{session}/workspace-dir", api_chat_session_workspace_dir
    )

    async with TestClient(TestServer(app)) as client:
        before = await (await client.get(f"/api/chat/sessions/{chat.key}")).json()
        with patch("personalclaw.dashboard.chat_handlers._save_recent_project"):
            moved = await client.post(
                f"/api/chat/sessions/{chat.key}/workspace-dir", json={"workspace_dir": str(repo)}
            )
            moved_body = await moved.json()
            cleared = await client.post(
                f"/api/chat/sessions/{chat.key}/workspace-dir", json={"workspace_dir": ""}
            )
            cleared_body = await cleared.json()

    assert before["auto_chain"] == "Chat"
    assert (moved.status, moved_body["auto_chain"]) == (200, "Code & tools")
    assert (cleared.status, cleared_body["auto_chain"]) == (200, "Chat")


@pytest.mark.asyncio
async def test_an_attachment_is_judged_against_the_model_the_chats_next_turn_runs_on(
    asked, gateway, repo, monkeypatch
):
    """Before a chat's runtime exists, whether an attached image goes as pixels or as its text is
    read for the model its turn would run on: the Code & tools model for a folder chat, which the
    chip used to read off the Chat binding instead."""
    from personalclaw.dashboard.chat_runner import session_image_input
    from personalclaw.providers import image_input

    seen: list[str] = []

    async def _image_input(ref: str):
        seen.append(ref)
        return SimpleNamespace(accepted=False, reason="")

    monkeypatch.setattr(image_input, "image_input", _image_input)
    _bind(chat=[PLAN_REF], code_tools=[WORK_REF])

    await session_image_input(gateway, _chat(gateway, folder=str(repo)))
    await session_image_input(gateway, _chat(gateway, folder=_in_the_workspace()))

    assert seen == [WORK_REF, PLAN_REF]


# ── a side question beside the chat ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_side_question_beside_a_folder_chat_is_answered_on_code_and_tools(
    asked, gateway, repo
):
    """🔴 Red before: the side question read the chat's conversation, her code included, and was
    answered on the Chat binding while the chat's own turns ran on Code & tools."""
    from personalclaw.dashboard.side import _run_side_turn
    from personalclaw.dashboard.side_state import SideState

    _bind(chat=[PLAN_REF], code_tools=[WORK_REF])
    chat = _chat(gateway, folder=str(repo))
    await _turn(gateway, chat)
    chat._side = side = SideState(open=True)
    side.last_run_id = "run-1"

    with patch("personalclaw.dashboard.side.sel", MagicMock()):
        await _run_side_turn(gateway, chat.key, chat, side, "Which file failed last?", "run-1")

    assert asked == [WORK, WORK], "the side question went to another model than the chat's"


@pytest.mark.asyncio
async def test_the_side_question_of_a_chat_in_the_workspace_stays_on_chat(asked, gateway):
    from personalclaw.dashboard.side import _run_side_turn
    from personalclaw.dashboard.side_state import SideState

    _bind(chat=[PLAN_REF], code_tools=[WORK_REF])
    chat = _chat(gateway, folder=_in_the_workspace())
    chat._side = side = SideState(open=True)
    side.last_run_id = "run-1"

    with patch("personalclaw.dashboard.side.sel", MagicMock()):
        await _run_side_turn(gateway, chat.key, chat, side, "What did I ask?", "run-1")

    assert asked == [PLAN]


# ── the decision itself ─────────────────────────────────────────────────────────────────────


def test_the_axis_is_the_folder_the_chat_works_in(repo):
    """``model_axis_for`` answers from what the chat holds before its turn: a loop's work takes
    Loops whatever its folder, a chat in a folder of its own Code & tools, any other chat Chat."""
    from personalclaw.dashboard.chat_runner import model_axis_for

    workspace = _in_the_workspace()
    assert model_axis_for(SimpleNamespace(_app="", workspace_dir=str(repo))) == "code_tools"
    assert model_axis_for(SimpleNamespace(_app="", workspace_dir=workspace)) == ""
    assert model_axis_for(SimpleNamespace(_app="", workspace_dir="")) == ""
    assert model_axis_for(SimpleNamespace(_app="loop", workspace_dir=str(repo))) == "loops"


# ── every use case is named the way the Models page names it ────────────────────────────────


def test_every_use_case_has_the_name_the_models_page_gives_it():
    """A server-composed sentence names a use case by ``USE_CASE_NAMES``, and the Models page by
    its own table (``ModelsPanel``'s ``USE_CASE_META``): the two read the same for every one. A
    sentence used to call Code & tools "Code tools", and Speech-to-text "Stt"."""
    import re

    from personalclaw.providers.use_cases import USE_CASE_NAMES, VALID_USE_CASES

    panel = Path(__file__).resolve().parents[1] / "web/src/pages/settings/ModelsPanel.tsx"
    labels = dict(re.findall(r"^\s+(\w+): \{ label: '([^']+)'", panel.read_text(), re.M))
    assert set(labels) == set(VALID_USE_CASES), "vacuity: the Models page table was not read whole"
    assert USE_CASE_NAMES == labels


def test_a_fix_names_code_and_tools_as_the_models_page_does(asked):
    """What a removed provider's fix says a use case falls back to, in the page's own words."""
    from personalclaw.resilience.fixes import _left_empty

    _bind(chat=[PLAN_REF], code_tools=[WORK_REF])

    assert _left_empty("code_tools", [WORK_REF]) == "Code & tools then uses your Chat models"
    assert _left_empty("stt", []) == "Speech-to-text then has no model until you choose one"
