"""A chat whose working directory she changes works in the new folder from its next turn on.

Measured on a running gateway: she switched a chat's Working directory from one folder to another
and the toast said the new folder was set, while the chat's live runtime kept its tools in the old
one: the shell started there, and a relative read or write landed there, until the runtime was
rebuilt for some other reason. A runtime fixes its folder when it is built (the native loop's tool
context, an agent CLI's process and session), and the working-directory door wrote the chat's
folder and left the runtime it had in place. Moving into or out of a folder of its own happened to
rebuild it (the turn's model then resolves on another chain); a move between two folders did not.

The working directory is now changed the way every other choice of what a chat runs on is
(``running_turn.rebind``): between turns its runtime is let go at once and the next turn's is built
in the new folder; during a turn, that turn ends as stopped and her message is answered again in
the new folder (``test_moving_a_chat_mid_turn_answers_her_message_there``). Clearing it puts the
chat back in the workspace a new chat starts in.

Driven through the real turn engine (``run_chat``), the route, the gateway's session manager over
the real resolution seam, and the real native loop and its file and shell tools; only the model
is scripted. Its name, and the folders, are invented.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, EVENT_TOOL_CALL, LLMEvent

ENTRY, MODEL = "work-models", "deep-coder"
REF = f"{ENTRY}:{MODEL}"

ASK = "Read the notes here, write your reply next to them and tell me where you are."
REPLY = "Read the notes."

#: What a coding turn does in the folder it works in, one call per model round: it reads a file
#: there by a relative path, writes one beside it, and asks the shell where it is.
CALLS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("read_file", {"path": "notes.txt"}),
    ("write_file", {"path": "reply.txt", "content": REPLY}),
    ("bash", {"command": "pwd"}),
)


class _Worker:
    """The model the chat's runtime is served by: each turn it makes :data:`CALLS` in order, then
    answers, and keeps what each call returned (``turns``, one list per turn). Each runtime's
    model names its calls apart, so a call asked again on a new runtime is a new call."""

    supports_tools = True
    built = 0

    def __init__(self, entry: str, model: str, turns: list[list[str]]) -> None:
        self.entry = entry
        self.model = model
        self.served_ref = f"{entry}:{model}"
        self.turns = turns
        _Worker.built += 1
        self.serial = _Worker.built

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **_kw: Any):
        asked_at = max(i for i, m in enumerate(messages) if m.get("role") == "user")
        results = [str(m["content"]) for m in messages[asked_at:] if m.get("role") == "tool"]
        if len(results) < len(CALLS):
            name, args = CALLS[len(results)]
            yield LLMEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"call-{self.serial}-{len(results) + 1}",
                title=name,
                tool_input=json.dumps(args),
            )
            yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=40, output_tokens=6)
            return
        self.turns.append(results)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Done.")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=40, output_tokens=2)

    async def stream(self, message: str):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Notes")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=4, output_tokens=2)


@pytest.fixture
def turns() -> list[list[str]]:
    """Settings → Providers holds one scripted provider, bound for Chat and for Code & tools, so a
    chat's turn runs on it whichever folder it works in. Returns what each turn's calls returned."""
    from personalclaw.config.loader import config_path
    from personalclaw.guardrails.breaker import reset_breakers
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.llm.registry import ProviderEntry, ProviderRegistry, set_default_registry
    from personalclaw.providers.use_cases import save_active_models

    seen: list[list[str]] = []
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **kwargs: Any):
        return _Worker(entry.name, str(kwargs.get("model") or entry.model), seen)

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
    registry.register_entry(ProviderEntry(name=ENTRY, type="scripted", model=MODEL))
    set_default_registry(registry)  # conftest restores the singleton afterwards
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"providers": [{"name": ENTRY, "type": "scripted"}]}))
    save_active_models({"chat": [REF], "code_tools": [REF]})
    reset_breakers()
    yield seen
    reset_breakers()


@pytest_asyncio.fixture
async def gateway(tmp_path):
    """The gateway's session manager over the real resolution seam, the dashboard state a chat's
    turn runs in, and the working-directory route as the composer calls it."""
    from personalclaw.config import AppConfig
    from personalclaw.dashboard.chat import api_chat_session_workspace_dir
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
    app = web.Application()
    app["state"] = state
    app.router.add_post(
        "/api/chat/sessions/{session}/workspace-dir", api_chat_session_workspace_dir
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    state.http = client
    try:
        yield state
    finally:
        await client.close()
        await sessions.close_all()


def _workspace() -> Path:
    """The workspace every chat starts in."""
    from personalclaw.config.loader import default_workspace_dir

    folder = default_workspace_dir()
    assert folder, "premise: the test home has a workspace"
    return Path(folder)


@pytest.fixture
def folders(tmp_path) -> dict[str, Path]:
    """Two folders of hers and the workspace, each holding notes that say which one it is."""
    places = {
        "recipe-box": tmp_path / "src" / "recipe-box",
        "garden-log": tmp_path / "src" / "garden-log",
        "workspace": _workspace(),
    }
    for name, folder in places.items():
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "notes.txt").write_text(f"These are the {name} notes.\n", encoding="utf-8")
    return places


def _chat(state, folder: Path):
    """A chat as ``POST /api/chat/sessions`` makes one, working in *folder*, on Trust."""
    chat = state.get_or_create_session(workspace_dir=str(folder))
    chat._trust = True
    return chat


async def _turn(state, chat, words: str = ASK) -> None:
    from personalclaw.dashboard.chat_runner import run_chat

    chat.append("user", words, "msg msg-u")
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, chat, words)


async def _set_folder(state, chat, folder: str) -> dict:
    """What the composer's Working directory prompt sends (``""`` clears it)."""
    with patch("personalclaw.dashboard.chat_handlers._save_recent_project"):
        resp = await state.http.post(
            f"/api/chat/sessions/{chat.key}/workspace-dir", json={"workspace_dir": folder}
        )
    assert resp.status == 200, await resp.text()
    return await resp.json()


def _worked_in(results: list[str], folders: dict[str, Path]) -> set[str]:
    """Which of the folders a turn's read and shell answered from."""
    read, _wrote, shell = results
    return {
        name
        for name, folder in folders.items()
        if f"the {name} notes" in read or os.path.realpath(folder) in shell
    }


#: Each switch the prompt makes: the folder the chat worked in, and what she sets ("" clears it,
#: which puts it back in the workspace).
SWITCHES = [
    ("folder to folder", "recipe-box", "garden-log"),
    ("folder to workspace", "recipe-box", ""),
    ("workspace to folder", "workspace", "garden-log"),
]


@pytest.mark.parametrize(("_name", "start", "to"), SWITCHES, ids=[s[0] for s in SWITCHES])
@pytest.mark.asyncio
async def test_after_a_switch_the_next_turn_reads_writes_and_runs_in_the_new_folder(
    turns, gateway, folders, _name, start, to
):
    """🔴 Red before, folder to folder: the chat's second turn read the first folder's notes, wrote
    its reply there again and its shell started there, while the toast said the second folder was
    set. The other two switches are the controls: they moved the next turn already."""
    landed = to or "workspace"
    chat = _chat(gateway, folders[start])
    await _turn(gateway, chat)

    answer = await _set_folder(gateway, chat, str(folders[to]) if to else "")
    await _turn(gateway, chat, "Again, please.")

    first, second = turns
    assert _worked_in(first, folders) == {start}
    assert _worked_in(second, folders) == {landed}, second
    assert (folders[landed] / "reply.txt").read_text(encoding="utf-8") == REPLY
    assert answer["workspace_dir"] == os.path.realpath(folders[landed])
    assert chat.workspace_dir == os.path.realpath(folders[landed])
    assert answer["moved"] is False, "no turn was running to move"


@pytest.mark.asyncio
async def test_the_folder_it_left_gets_nothing_more_from_the_chat(turns, gateway, folders):
    """What the second turn wrote is in the new folder only: the reply the first one wrote in the
    old folder is the only one there."""
    chat = _chat(gateway, folders["recipe-box"])
    await _turn(gateway, chat)
    (folders["recipe-box"] / "reply.txt").unlink()

    await _set_folder(gateway, chat, str(folders["garden-log"]))
    await _turn(gateway, chat, "Again, please.")

    assert not (folders["recipe-box"] / "reply.txt").exists()
    assert (folders["garden-log"] / "reply.txt").exists()


@pytest.mark.asyncio
async def test_setting_the_folder_it_already_works_in_keeps_its_runtime(turns, gateway, folders):
    """The prompt opens on the chat's own folder: Set with nothing changed is no change, so the
    chat keeps the runtime it has (an agent CLI keeps its process and conversation)."""
    chat = _chat(gateway, folders["recipe-box"])
    await _turn(gateway, chat)
    runtime = gateway.sessions.get_provider(f"dashboard:{chat.key}")

    answer = await _set_folder(gateway, chat, str(folders["recipe-box"]))

    assert gateway.sessions.get_provider(f"dashboard:{chat.key}") is runtime
    assert answer["moved"] is False
    assert answer["workspace_dir"] == os.path.realpath(folders["recipe-box"])


@pytest.mark.asyncio
async def test_every_open_page_is_told_where_the_chat_works_now(turns, gateway, folders):
    """The other tabs of the chat learn the folder, and the chain its next turn on Auto takes,
    from the same frame that names what it runs on."""
    chat = _chat(gateway, folders["workspace"])

    await _set_folder(gateway, chat, str(folders["garden-log"]))

    (binding,) = [
        c.args[1] for c in gateway.broadcast_ws.call_args_list if c.args[0] == "session_binding"
    ]
    assert binding["workspace_dir"] == os.path.realpath(folders["garden-log"])
    assert binding["auto_chain"] == "Code & tools"


@pytest.mark.asyncio
async def test_a_switch_lands_in_her_saved_chat(turns, gateway, folders):
    """Written into the chat's saved record at once, so a restart before her next message does not
    put the chat back in the folder it left."""
    chat = _chat(gateway, folders["recipe-box"])
    await _turn(gateway, chat)

    await _set_folder(gateway, chat, str(folders["garden-log"]))

    from personalclaw.dashboard.chat_utils import persisted_history_key

    log = gateway.conversation_log
    meta = log.get_metadata(persisted_history_key(log, chat.key))
    assert meta["workspace_dir"] == os.path.realpath(folders["garden-log"])


@pytest.mark.asyncio
async def test_a_turn_running_when_she_switches_is_answered_again_in_the_new_folder(
    turns, gateway, folders
):
    """🔴 Red before: the turn went on in the old folder, its write landed there once she allowed
    it, and the chat said nothing about the switch.

    The chat is not on Trust here, so the turn's write waits for her answer: that is the turn
    running when she switches. It ends as stopped, its approval answered cancelled, and her
    message is answered again in the new folder, where each call that asks is allowed."""
    from personalclaw.dashboard.chat_runner import run_chat

    chat = _chat(gateway, folders["recipe-box"])
    chat._trust = False

    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        assert chat.enqueue_or_run_prompt(ASK, run_chat, gateway)
        in_the_old_folder = await _waiting_on(chat)

        answer = await _set_folder(gateway, chat, str(folders["garden-log"]))
        await _allow_each_call_until_answered(gateway, chat, never=in_the_old_folder)

    assert answer["moved"] is True
    assert answer["workspace_dir"] == os.path.realpath(folders["garden-log"])
    assert not (folders["recipe-box"] / "reply.txt").exists(), "the old folder was written"
    assert (folders["garden-log"] / "reply.txt").read_text(encoding="utf-8") == REPLY
    (worked,) = turns
    assert _worked_in(worked, folders) == {"garden-log"}
    notices = [m["content"] for m in chat.messages if m.get("role") == "notice"]
    garden = os.path.realpath(folders["garden-log"])
    assert notices == [f"Moved to PersonalClaw in {garden} — it is answering your message."]
    assert chat.workspace_dir == garden


def _pending(chat) -> list[str]:
    return [aid for aid, fut in chat._approval_futures.items() if not fut.done()]


async def _waiting_on(chat) -> str:
    """The id of the approval the chat's turn waits on, once it asks."""
    for _ in range(1500):
        if pending := _pending(chat):
            return pending[0]
        await asyncio.sleep(0.01)
    raise AssertionError(f"the turn never asked: {chat.messages}")


async def _allow_each_call_until_answered(state, chat, *, never: str) -> None:
    """Allow each call the chat's turn asks about until the chat is idle again; the call *never*
    names (the old folder's) is never allowed."""
    from personalclaw.approval_answer import YOU

    for _ in range(3000):
        for aid in _pending(chat):
            assert aid != never, "the turn in the old folder is still waiting for her answer"
            state.decide_session_approval(chat, aid, "approved", by=YOU)
        task = chat.task
        if (task is None or task.done()) and not chat._queue and not _pending(chat):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the chat never answered: {chat.messages}")
