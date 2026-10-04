"""An Incognito or Temporary chat's work leaves no work behind it that lasts after the chat.

Such a chat's words stay with it: only the model it runs on sees them, and its work keeps nothing.
Its agent could still make a loop or project, start one, make an automation or a scheduled task,
change one, and register a callback. Each of those is kept after the chat and runs later as a
session of its own, on its own model, that keeps what it does, and nothing in the loop engine or the
trigger store read the chat's mode. So a loop made from an Incognito chat carried the chat's words
into a durable loop run as an ordinary session on the Loops model, and an automation made from a
Temporary chat kept its words in the trigger store, to be run by another model later.

Each is refused now at the one place every door to it reaches (``lasting_work``), before anything
is written, with a sentence that says why and to do it from an ordinary chat. Driven as each door
is driven: a native agent's turn in the gateway whose scripted model calls the real tool, the real
``mcp-core`` process an agent CLI runs, the gateway's tool route that process can call with the
internal credential, and the loop routes, each beside an ordinary chat that still does the work.
"""

from __future__ import annotations

import ast
import inspect
import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
from test_a_private_chat_controls_only_its_own_runs import _Scripted
from test_an_agent_clis_workflow_tools_reach_the_gateway import (  # noqa: F401 - a fixture
    CLI_MODEL,
    TEMPORARY_KEY,
    _tool_server,
    gateway,
)

from personalclaw import lasting_work, mcp_automation, memory_writes, session_restrictions
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tools import InProcessMcpToolProvider
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.dashboard.handlers.loop_routes import _build_loop_from_body
from personalclaw.llm.events import EVENT_PERMISSION_REQUEST, EVENT_TOOL_RESULT
from personalclaw.loop import files as loop_files
from personalclaw.loop import manager as loop_manager
from personalclaw.loop import store as loop_store
from personalclaw.triggers import tools as trigger_tools
from personalclaw.triggers.store import TriggerStore

#: An Incognito chat and an ordinary one, live in the gateway, and the model each turn runs on.
INCOGNITO_KEY = "dashboard:chat-lasting-incognito"
ORDINARY_KEY = "dashboard:chat-lasting-ordinary"
CHAT_MODEL = "local:scripted-chat-model"

#: What the person said in the chat, which the agent would hand to what it makes.
WORDS = "Plan the surprise party for my sister at the lake house in May"

#: The owner's own automation, made on the Triggers page.
WATERING = "Water the plants on the balcony"


class _SteadySvc:
    """The loop scheduler, noting each worker it is asked to arm and driving none."""

    def __init__(self) -> None:
        self.added: list[str] = []

    async def add(self, **kw: Any) -> None:
        self.added.append(str(kw.get("session_name", "")))

    async def update(self, *a: Any, **kw: Any) -> None:
        return None

    def get_by_session(self, key: str) -> None:
        return None

    def list_all(self) -> list[Any]:
        return []


@pytest.fixture
def svc(monkeypatch: pytest.MonkeyPatch) -> _SteadySvc:
    scheduler = _SteadySvc()
    monkeypatch.setattr("personalclaw.triggers.nudge.get_instance", lambda: scheduler)
    return scheduler


@pytest.fixture
def chats(gateway) -> Iterator[None]:  # noqa: F811 - the imported fixture
    """The Incognito chat, the ordinary chat and the agent CLI's Temporary chat, live in the
    gateway as each is while its agent's turn runs, each on the model its turn named."""
    for key, mode in ((INCOGNITO_KEY, "incognito"), (ORDINARY_KEY, "persistent")):
        gateway.state.get_or_create_session(key.removeprefix("dashboard:"), memory_mode=mode)
    gateway.state.get_or_create_session(
        TEMPORARY_KEY.removeprefix("dashboard:"), memory_mode="temporary"
    )
    session_restrictions.mark_own_model(INCOGNITO_KEY, CHAT_MODEL)
    session_restrictions.mark_own_model(TEMPORARY_KEY, CLI_MODEL)
    yield
    session_restrictions.clear(INCOGNITO_KEY)


class _Agent:
    """A chat's native agent, with the loop tools, the automation tools and the core tools, and the
    turn its chat runs it in (``memory_writes.runs_as_its_session``, ``answered_by``)."""

    def __init__(self, key: str, mode: str, cwd: Any) -> None:
        self.key, self.mode = key, mode
        self.model = _Scripted()
        self.runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
            model_provider=self.model,
            tool_providers=[
                NativeBuiltinToolProvider(cwd, session_key=key, categories={"projects"}),
                InProcessMcpToolProvider(
                    module="personalclaw.mcp_automation", provider_name="personalclaw-automation"
                ),
                InProcessMcpToolProvider(),
            ],
            session_key=key,
        )
        self.runtime.set_task_mode("agent")

    async def calls(self, tool: str, arguments: dict[str, Any]) -> SimpleNamespace:
        """One turn in which the agent calls *tool*; its owner allows the call when asked. Returns
        whether it succeeded, what the model was answered, and how many times anyone was asked."""
        self.model.next(tool, arguments)
        results: list[str] = []
        asked = 0
        with memory_writes.derived_from(self.key, memory_mode=self.mode):
            memory_writes.answered_by(CHAT_MODEL)
            async for event in self.runtime.stream("go"):
                if event.kind == EVENT_PERMISSION_REQUEST:
                    asked += 1
                    await self.runtime.approve_tool(event.request_id)
                elif event.kind == EVENT_TOOL_RESULT:
                    results.append(str(event.tool_output))
        assert len(results) == 1, results
        return SimpleNamespace(ok=not results[0].startswith("Error"), text=results[0], asked=asked)


async def _agent(key: str, mode: str, cwd: Any) -> _Agent:
    agent = _Agent(key, mode, cwd)
    await agent.runtime.start()
    return agent


def _loop_folders() -> list[str]:
    root = loop_files.loops_root()
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []


def _owners_draft() -> str:
    """A loop you made on the Loops page and have not started."""
    made = loop_store.create(
        _build_loop_from_body({"kind": "goal", "task": "Compare the three quotes for the roof"})
    )
    return made.id


def _owners_automation(gw: SimpleNamespace) -> str:
    """An automation you made on the Triggers page."""
    made = trigger_tools.create(
        TriggerStore(base_dir=gw.home),
        name="balcony plants",
        when="in 3 hours",
        message=WATERING,
        created_by="user",
    )
    assert made.ok, made.text
    return str(made.data["trigger"]["id"])


def _stored(gw: SimpleNamespace) -> str:
    """Everything the trigger store and the callback store keep, as text."""
    files = (gw.home / "triggers.json", gw.home / "webhook_callbacks.json")
    return "\n".join(f.read_text(encoding="utf-8") for f in files if f.exists())


def _says_why(text: str, mode: str, what: str) -> None:
    assert (
        f"This chat is {mode}, so nothing from it is sent to any model but the one it runs on"
        in (text)
    ), (text)
    assert what in text, text
    assert "from an ordinary chat" in text, text


# ── a native agent, in the gateway ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_incognito_chats_agent_creates_no_loop_and_says_why(
    gateway, chats, svc, tmp_path  # noqa: F811 - the imported fixture
):
    """🔴 Before: a Goal loop draft whose task was the chat's words, after you were asked to allow
    the call; its worker would run as an ordinary session on the Loops model."""
    agent = await _agent(INCOGNITO_KEY, "incognito", tmp_path)

    made = await agent.calls("project_run_create", {"kind": "goal", "task": WORDS})

    assert not made.ok
    _says_why(made.text, "Incognito", "a loop or project is kept after the chat")
    assert "none was created" in made.text, made.text
    assert made.asked == 0
    assert loop_store.list_all() == []
    assert _loop_folders() == []


@pytest.mark.asyncio
async def test_an_incognito_chats_agent_starts_no_loop(
    gateway, chats, svc, tmp_path  # noqa: F811 - the imported fixture
):
    """🔴 Before: your draft started from the Incognito chat's turn, its worker armed."""
    draft = _owners_draft()
    agent = await _agent(INCOGNITO_KEY, "incognito", tmp_path)

    started = await agent.calls("project_run_start", {"project_id": draft})

    assert not started.ok
    _says_why(started.text, "Incognito", "it was left as it is")
    assert started.asked == 0
    assert loop_store.get(draft).status == "ready"
    assert not (loop_files.safe_loop_dir(draft) / "brief.md").exists()
    assert svc.added == []
    assert f"loop-{draft}" not in gateway.state._sessions


@pytest.mark.asyncio
async def test_an_ordinary_chats_agent_creates_and_starts_a_loop(
    gateway, chats, svc, tmp_path  # noqa: F811 - the imported fixture
):
    agent = await _agent(ORDINARY_KEY, "persistent", tmp_path)

    made = await agent.calls("project_run_create", {"kind": "goal", "task": WORDS})
    [loop] = loop_store.list_all()
    started = await agent.calls("project_run_start", {"project_id": loop.id})

    assert made.ok and "Created Goal Loop draft" in made.text, made.text
    assert loop.task == WORDS
    assert started.ok and "it's now running" in started.text, started.text
    assert loop_store.get(loop.id).status == "running"
    assert svc.added == [f"loop-{loop.id}"]


@pytest.mark.asyncio
async def test_an_incognito_chats_agent_makes_no_automation_and_registers_no_callback(
    gateway, chats, tmp_path  # noqa: F811 - the imported fixture
):
    """🔴 Before: the one-time task and the callback were saved with the chat's words, and your
    automation's name was replaced by them."""
    yours = _owners_automation(gateway)
    kept = _stored(gateway)
    agent = await _agent(INCOGNITO_KEY, "incognito", tmp_path)

    answers = [
        await agent.calls(
            "automation_create", {"name": "party", "when": "in 2 hours", "message": WORDS}
        ),
        await agent.calls(
            "set_onetime_task", {"name": "party", "when": "in 2 hours", "message": WORDS}
        ),
        await agent.calls("automation_update", {"id": yours, "patch": json.dumps({"name": WORDS})}),
        await agent.calls("hook_register", {"hook_id": "party:rsvp", "context_summary": WORDS}),
    ]

    for answer, what in zip(
        answers,
        (
            "none was created",
            "none was created",
            "it was left as it is. Change it",
            "none was registered",
        ),
        strict=True,
    ):
        assert not answer.ok
        _says_why(answer.text, "Incognito", what)
        assert answer.asked == 0
    assert _stored(gateway) == kept
    assert WORDS not in _stored(gateway)


@pytest.mark.asyncio
async def test_an_ordinary_chats_agent_makes_an_automation_and_registers_a_callback(
    gateway, chats, tmp_path  # noqa: F811 - the imported fixture
):
    agent = await _agent(ORDINARY_KEY, "persistent", tmp_path)

    made = await agent.calls(
        "automation_create", {"name": "party", "when": "in 2 hours", "message": WORDS}
    )
    registered = await agent.calls(
        "hook_register", {"hook_id": "party:rsvp", "context_summary": WORDS}
    )

    assert made.ok and "Created automation 'party'" in made.text, made.text
    assert registered.ok and "Hook registered: party:rsvp" in registered.text, registered.text
    assert _stored(gateway).count(WORDS) == 2


# ── an agent CLI's tool server, and the gateway's routes it can reach ────────────────────────────


@pytest.mark.asyncio
async def test_a_temporary_agent_clis_tools_make_no_automation_and_register_no_callback(
    gateway, chats  # noqa: F811 - the imported fixture
):
    """🔴 Before: the real tool server saved the one-time task and the callback with the chat's
    words, and replaced your automation's name with them."""
    yours = _owners_automation(gateway)
    kept = _stored(gateway)

    answers = await _tool_server(
        gateway,
        ("automation_create", {"name": "party", "when": "in 2 hours", "message": WORDS}),
        ("set_onetime_task", {"name": "party", "when": "in 2 hours", "message": WORDS}),
        ("automation_update", {"id": yours, "patch": json.dumps({"name": WORDS})}),
        ("hook_register", {"hook_id": "party:rsvp", "context_summary": WORDS}),
        key=TEMPORARY_KEY,
    )

    for ok, text in answers:
        assert not ok, text
        assert text.startswith(f"Error [{lasting_work.CODE}]: This chat is Temporary"), text
        assert "from an ordinary chat" in text, text
    assert _stored(gateway) == kept


async def _internal(gw: SimpleNamespace, path: str, body: dict[str, Any]) -> tuple[int, Any]:
    """A call an agent CLI's process makes with the internal credential, naming its chat."""
    secret = (gw.home / ".local_secret").read_text(encoding="utf-8").strip()
    async with aiohttp.ClientSession() as http:
        resp = await http.post(
            f"http://127.0.0.1:{gw.port}{path}",
            json=body,
            headers={"X-Internal-Secret": secret, "X-Session-Key": TEMPORARY_KEY},
        )
        return resp.status, await resp.json(content_type=None)


@pytest.mark.asyncio
async def test_a_temporary_agent_clis_call_through_the_gateway_creates_and_starts_no_loop(
    gateway, chats, svc  # noqa: F811 - the imported fixture
):
    """The tool route runs a tool as the work of the chat the call names, so the loop tools an
    agent CLI has no server of its own for are held to the chat there too."""
    draft = _owners_draft()

    made = await _internal(
        gateway,
        "/api/tools/invoke",
        {"tool": "project_run_create", "arguments": {"kind": "goal", "task": WORDS}},
    )
    started = await _internal(
        gateway,
        "/api/tools/invoke",
        {"tool": "project_run_start", "arguments": {"project_id": draft}},
    )

    for status, answer in (made, started):
        assert status == 200 and answer["ok"] is False, answer
        assert answer["error"].startswith("This chat is Temporary"), answer
        assert answer["not_run"] == "refused_by_tool", answer
    assert [loop.id for loop in loop_store.list_all()] == [draft]
    assert loop_store.get(draft).status == "ready"
    assert svc.added == []


async def _signed_in(gw: SimpleNamespace, method: str, path: str, *, work: str, body: Any = None):
    """A request with your sign-in, naming *work* as the session it is made for."""
    secret = (gw.home / ".local_secret").read_text(encoding="utf-8").strip()
    async with aiohttp.ClientSession() as http:
        token = await http.get(
            f"http://127.0.0.1:{gw.port}/api/token/local", headers={"X-Local-Secret": secret}
        )
        bearer = (await token.json())["token"]
        resp = await http.request(
            method,
            f"http://127.0.0.1:{gw.port}{path}",
            json=body,
            headers={"Authorization": f"Bearer {bearer}", "X-Session-Key": work},
        )
        return resp.status, await resp.json(content_type=None)


@pytest.mark.asyncio
async def test_a_request_made_for_a_temporary_chat_creates_starts_and_steers_no_loop(
    gateway, chats, svc  # noqa: F811 - the imported fixture
):
    """The loop routes reach the same places: a request made for the Temporary chat is answered
    403 ``restricted_session`` in the refusal's words, and the same request of yours is made."""
    draft = _owners_draft()
    running = _owners_draft()
    await loop_manager.start(gateway.state, svc, running)
    armed = list(svc.added)

    created = await _signed_in(
        gateway, "POST", "/api/loops", work=TEMPORARY_KEY, body={"kind": "goal", "task": WORDS}
    )
    started = await _signed_in(
        gateway, "PATCH", f"/api/loops/{draft}", work=TEMPORARY_KEY, body={"action": "start"}
    )
    steered = await _signed_in(
        gateway, "POST", f"/api/loops/{running}/nudge", work=TEMPORARY_KEY, body={"text": WORDS}
    )
    yours = await _signed_in(
        gateway, "POST", "/api/loops", work="dashboard:ui", body={"kind": "goal", "task": WORDS}
    )

    for (status, answer), what in zip(
        (created, started, steered),
        ("none was created", "it was left as it is", "nothing was sent to it"),
        strict=True,
    ):
        assert status == 403, answer
        assert answer["error"]["code"] == lasting_work.CODE, answer
        assert answer["error"]["message"].startswith("This chat is Temporary"), answer
        assert what in answer["error"]["message"], answer
    assert loop_store.get(draft).status == "ready"
    assert svc.added == armed
    assert loop_files.read_guidance(running) == ""
    assert loop_files.get_nudges(running) == []
    assert yours[0] == 201, yours
    assert sorted(loop.task for loop in loop_store.list_all()).count(WORDS) == 1


# ── the answers each door relies on ─────────────────────────────────────────────────────────────


def test_work_whose_chat_cannot_be_read_creates_no_loop_and_says_so(tmp_path, monkeypatch):
    """A chat whose memory setting nothing can read is held to the same rule, saying so; work that
    derives from no chat is not held to it."""
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    key = "dashboard:chat-lasting-unreadable"
    session_restrictions.mark_unreadable(key)
    try:
        with memory_writes.derived_from(key):
            why = lasting_work.refusal(lasting_work.LOOP, lasting_work.CREATE)
            with pytest.raises(lasting_work.Refused):
                _owners_draft()
    finally:
        session_restrictions.clear(key)

    assert why.startswith("This chat's memory setting cannot be read"), why
    assert lasting_work.refusal(lasting_work.LOOP, lasting_work.CREATE) == ""
    assert loop_store.list_all() == []
    assert _owners_draft()


def test_a_subagent_of_an_incognito_chat_creates_no_loop(tmp_path, monkeypatch):
    """A subagent works for its chat (``memory_writes.hand_on`` marks it with the chat's mode), so
    a request it makes is held to the chat's rule."""
    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    child = "subagent:lasting-helper"
    session_restrictions.mark_incognito(child)
    try:
        with memory_writes.as_work_of(child):
            with pytest.raises(lasting_work.Refused, match="This chat is Incognito"):
                _owners_draft()
    finally:
        session_restrictions.clear(child)
    assert loop_store.list_all() == []


def test_every_automation_tool_that_makes_or_changes_one_says_so_before_it_is_allowed():
    """The automation tools that reach the trigger store's make or change are the ones whose
    preflight asks the rule, so a new one cannot be added without it."""
    tree = ast.parse(inspect.getsource(mcp_automation._call_tool_inner))
    reaching: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        named = node.test.comparators[0]
        if not isinstance(named, ast.Constant):
            continue
        for call in (n for stmt in node.body for n in ast.walk(stmt)):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                if (
                    call.func.attr in ("create", "update")
                    and getattr(call.func.value, "id", "") == "T"
                ):
                    reaching[named.value] = (
                        lasting_work.CREATE if call.func.attr == "create" else lasting_work.CHANGE
                    )
    assert reaching == mcp_automation._LASTING_ACTS


def _answering(cid: str, body: dict[str, Any]) -> Any:
    """The request that answers the question loop *cid* waits on."""
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    app = web.Application()
    app["state"] = SimpleNamespace()
    request = make_mocked_request(
        "POST", f"/api/loops/{cid}/answer", match_info={"id": cid}, app=app
    )

    async def _json() -> dict[str, Any]:
        return body

    request.json = _json  # type: ignore[method-assign]
    return request


def _waiting_on(question: dict[str, Any]) -> str:
    """A code loop of yours, waiting on *question*."""
    from personalclaw.loop.loop import LoopStatus

    made = loop_store.create(
        _build_loop_from_body(
            {"kind": "code", "task": "Tidy the error handling in the billing code"}
        )
    )
    loop_store.update_status(made.id, LoopStatus.NEEDS_INPUT)
    loop_files.write_question(made.id, "Your call", **question)
    return made.id


@pytest.mark.asyncio
async def test_an_answer_made_for_a_temporary_chat_resumes_no_loop_and_changes_nothing(
    tmp_path, monkeypatch
):
    """A loop waiting on a merge or a conflict resumes once it is answered, so a request made for a
    Temporary chat is refused before its answer is written: the question stays, nothing is merged,
    redone or dropped. 🔴 Before: the answer was taken and the loop resumed from the chat's request.
    Your own answer is taken and resumes it."""
    from personalclaw.dashboard.handlers import loop_routes as routes
    from personalclaw.loop import conflicts, kinds

    monkeypatch.setattr("personalclaw.loop.files.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.triggers.nudge.get_instance", lambda: _SteadySvc())
    monkeypatch.setattr("personalclaw.loop.worktree.branch_tip", lambda ws, task: "c0ffee1")
    answered: list[str] = []
    resumed: list[str] = []

    async def _resume(state: Any, svc: Any, loop_id: str) -> None:
        resumed.append(loop_id)

    async def _choose(svc: Any, loop: Any, task_id: str, tip: str, choice: str) -> Any:
        answered.append(f"{choice}:{loop.id}")
        return conflicts.Outcome()

    kinds.ensure_loaded()
    monkeypatch.setattr(
        kinds.get_or_none("code"),
        "approve_merge",
        lambda cid, tips: answered.append(f"merge:{cid}"),
    )
    monkeypatch.setattr(conflicts, "choose", _choose)
    monkeypatch.setattr(loop_manager, "start", _resume)
    merging = _waiting_on({"merge": {"tasks": [{"task_id": "t1", "title": "Fix"}], "into": "main"}})
    conflicted = _waiting_on({"conflict": {"task_id": "t1", "title": "Fix"}})
    merge = {"tips": {"t1": "c0ffee1"}, "confirm": True}
    drop = {"choice": "drop", "task_id": "t1", "tip": "c0ffee1", "confirm": True}

    with memory_writes.derived_from(TEMPORARY_KEY, memory_mode="temporary"):
        with pytest.raises(lasting_work.Refused, match="it was left as it is"):
            await routes.api_loop_merge(_answering(merging, merge))
        with pytest.raises(lasting_work.Refused, match="it was left as it is"):
            await routes.api_loop_conflict(_answering(conflicted, drop))

    assert answered == [] and resumed == []
    assert loop_files.pending_question(merging) is not None
    assert loop_files.pending_question(conflicted) is not None
    assert (await routes.api_loop_merge(_answering(merging, merge))).status == 200
    assert (await routes.api_loop_conflict(_answering(conflicted, drop))).status == 200
    assert answered == [f"merge:{merging}", f"drop:{conflicted}"]
    assert resumed == [merging, conflicted]


def test_a_lifecycle_trigger_is_neither_made_nor_changed_for_an_incognito_chat(tmp_path):
    """A lifecycle trigger runs later, whenever its event comes, as work of its own."""
    from personalclaw.hooks import ScriptHookStore

    store = ScriptHookStore(config_dir=tmp_path)
    yours = store.create(
        {
            "name": "after each prompt",
            "provider_config": {"command": "/nonexistent/pc-fixture-hook"},
        }
    )
    kept = (tmp_path / "hooks.json").read_text(encoding="utf-8")

    with memory_writes.derived_from(INCOGNITO_KEY, memory_mode="incognito"):
        with pytest.raises(lasting_work.Refused, match="none was created"):
            store.create({"name": WORDS, "provider_config": {"command": "/nonexistent/pc-x"}})
        with pytest.raises(lasting_work.Refused, match="it was left as it is"):
            store.update(yours.id, {"name": WORDS})

    assert (tmp_path / "hooks.json").read_text(encoding="utf-8") == kept
    assert store.update(yours.id, {"name": "after every prompt"}) is not None
