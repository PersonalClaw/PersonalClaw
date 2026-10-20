"""A Temporary or Incognito chat's batch is that chat's own, and nothing of it outlives the chat.

A batch is two or more tasks a chat's agent starts at once (``subagent_run``). It was compiled
into a workflow definition and saved in your workflow library under a machine name, holding the
chat's tasks in its own words, and every other chat's agent listed it and read it
(``workflow_list_defs``, ``workflow_get_def``), could start it again and delete it, through the
built-in agent's tools and through the tool server an agent CLI runs. A Temporary chat's runs were
deleted once it ended and the definition was not, so its tasks outlived it. The batch's run was
read by its id by any chat's agent too, its tasks and their reports included, and the context of
every chat's turn listed it.

Now a batch keeps no definition: its run is started from the tasks it was compiled from and holds
them, as every run holds the spec it runs. A batch's run, and every run a Temporary or Incognito
chat starts, is that chat's own: another chat's agent lists none of it and reads it as no run at
all, in the words an id that never existed reads in. Once the chat ends (a Temporary chat's
session, an Incognito chat's deletion), nothing of its batch is left. An ordinary chat's batch
still works for that chat, and you see every chat's on your Workflows page.

Driven as each caller drives it, against the real gateway with its workflow supervisor started: the
built-in agent's tools in the gateway (a native agent's turn whose model calls the real tool), the
real ``personalclaw mcp-core`` process an agent CLI runs, and the gateway's own routes.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import aiohttp
import pytest
import pytest_asyncio
from test_a_private_chat_controls_only_its_own_runs import (
    CHAT_MODEL,
    _internal,
    _NativeAgent,
    _owner_token,
    _url,
)
from test_an_agent_clis_workflow_tools_reach_the_gateway import (  # noqa: F401 - a fixture
    TWO_STEPS,
    _body,
    _tool_server,
    _until,
    gateway,
)

from personalclaw import session_restrictions
from personalclaw.cancellation import cancel_and_wait
from personalclaw.dashboard.chat_forget import delete_chats, forget_temporary_chat
from personalclaw.subagent import SubagentManager
from personalclaw.workflows import batch_start
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import native_defs, ownership, store
from personalclaw.workflows.models import OriginKind, RunOrigin, RunStatus, WorkflowRun

#: An ordinary chat whose agent asks about the batches of the others, and the chats that start one.
OTHER = "dashboard:chat-batch-other"
ORDINARY = "dashboard:chat-batch-ordinary"
TEMPORARY = "dashboard:chat-batch-temporary"
INCOGNITO = "dashboard:chat-batch-incognito"
MODE = {OTHER: "persistent", ORDINARY: "persistent", TEMPORARY: "temporary", INCOGNITO: "incognito"}

#: What the person asked the batch's tasks about, in the chat's own words.
WORDS = "the lantern festival"
TASKS = [
    {
        "task": f"List gift ideas for {WORDS}",
        "title": f"Gifts for {WORDS}",
        "objective": f"find three gifts that suit {WORDS}",
        "output_format": "a numbered list, one line each",
        "boundary": "read only: change no file and send nothing",
    },
    {
        "task": f"Find a bakery that can make a cake for {WORDS}",
        "title": f"A cake for {WORDS}",
        "objective": f"name one bakery that can bake for {WORDS}",
        "output_format": "the bakery's name and one sentence",
        "boundary": "read only: change no file and send nothing",
    },
]

#: A run id and a batch's name that never existed: what another chat's batch reads like.
NEVER_RUN = "0badc0de"
NEVER_BATCH = "subagent-batch-1000000000000-000000"


class _TrustedSessions(MagicMock):
    """The gateway's sessions, every chat's Trust on: a batch that only reads starts unasked, as
    its chat's subagents do (`SubagentManager._start_grant`)."""

    def get_approval_policy(self, key: str) -> str:
        return "auto"


@pytest_asyncio.fixture
async def chats(gateway, monkeypatch):  # noqa: F811 - the imported fixture
    """The real gateway with your workflow library and its chats' transcripts on disk, as the
    gateway keeps them, its subagent manager, every chat live in it, and the supervisor's own poll
    stopped, so each poll a test makes is the only one."""
    from personalclaw.history import ConversationLog

    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case", lambda _use_case: True
    )
    defs_mod.register_provider(native_defs.NativeWorkflowDefProvider())
    gateway.state.conversation_log = ConversationLog()
    gateway.state.conversation_log.init()
    gateway.state.subagents = SubagentManager(sessions=_TrustedSessions(), ctx_builder=None)
    for key, mode in MODE.items():
        gateway.state.get_or_create_session(key.removeprefix("dashboard:"), memory_mode=mode)
        session_restrictions.mark_own_model(key, CHAT_MODEL)
    await cancel_and_wait([gateway.supervisor._task], what="the supervisor's own poll")
    try:
        yield gateway
    finally:
        for key in MODE:
            session_restrictions.clear(key)


async def _batch(gw: SimpleNamespace, key: str) -> SimpleNamespace:
    """A batch the chat *key*'s agent starts through the tool server an agent CLI runs."""
    [(ok, text)] = await _tool_server(gw, ("subagent_run", {"tasks": TASKS}), key=key)
    assert ok, text
    run_id = str(json.loads(text.splitlines()[0])["run_id"])
    run = store.get(run_id)
    assert run is not None and run.origin.kind is OriginKind.SUBAGENT_TOOL, text
    assert run.origin.session_key == key
    spec = store.read_spec(run_id) or {}
    step = str(spec["root"]["children"][0]["id"])
    return SimpleNamespace(run_id=run_id, name=run.workflow_name, step=step)


def _native(key: str) -> _NativeAgent:
    return _NativeAgent(key, MODE[key])


def _calls(batch: SimpleNamespace, *, run_id: str = "", name: str = "") -> list[tuple[str, dict]]:
    """Each workflow tool call that names the batch, by its definition's name or its run's id."""
    run_id = run_id or batch.run_id
    name = name or batch.name
    return [
        ("workflow_get_def", {"name": name}),
        ("workflow_start", {"name": name}),
        ("workflow_delete_def", {"name": name}),
        ("workflow_status", {"run_id": run_id}),
        ("workflow_output", {"run_id": run_id, "node_id": batch.step}),
        ("workflow_fork", {"run_id": run_id}),
        ("workflow_cancel", {"run_id": run_id}),
    ]


async def _answers(path: str, gw: SimpleNamespace, key: str, calls: list) -> list[str]:
    """What *key*'s agent is answered for *calls* on *path*."""
    if path == "tool_server":
        return [text for _ok, text in await _tool_server(gw, *calls, key=key)]
    agent = _native(key)
    await agent.runtime.start()
    return [(await agent.calls(name, args))[1] for name, args in calls]


async def _route_answers(
    gw: SimpleNamespace, key: str, batch: SimpleNamespace, *, run_id: str = "", name: str = ""
) -> list[tuple[int, Any]]:
    """What the gateway's routes answer *key*'s agent for the batch, by name and by run id."""
    run_id = run_id or batch.run_id
    name = name or batch.name
    asked = [
        ("GET", f"/api/workflows/{name}"),
        ("DELETE", f"/api/workflows/{name}"),
        ("GET", f"/api/workflows/runs/{run_id}"),
        ("GET", f"/api/workflows/runs/{run_id}/outputs/{batch.step}"),
        ("GET", f"/api/workflows/runs/{run_id}/observe?duration_ms=100"),
        ("POST", f"/api/workflows/runs/{run_id}/fork"),
        ("POST", f"/api/workflows/runs/{run_id}/cancel"),
    ]
    return [await _internal(gw, method, path, work=key, body={}) for method, path in asked]


def _unnamed(answer: Any, *names: str) -> str:
    """*answer* with each of *names* written as one placeholder, so two answers that differ only by
    the name or id they echo compare equal."""
    text = json.dumps(answer) if not isinstance(answer, str) else answer
    for name in names:
        text = text.replace(name, "<named>")
    return text


async def _listed(path: str, gw: SimpleNamespace, key: str) -> str:
    """Everything *key*'s agent is told when it lists the workflows."""
    if path == "route":
        status, body = await _internal(gw, "GET", "/api/workflows", work=key)
        assert status == 200, body
        return json.dumps(body)
    [answer] = await _answers(path, gw, key, [("workflow_list_defs", {})])
    return answer


async def _owners(gw: SimpleNamespace, path: str) -> Any:
    """What your own Workflows page reads at *path*."""
    token = await _owner_token(gw)
    async with aiohttp.ClientSession() as http:
        resp = await http.get(_url(gw, path), headers={"Authorization": f"Bearer {token}"})
        assert resp.status == 200, await resp.text()
        return await resp.json()


def _anything_left(home: Path, batch: SimpleNamespace) -> list[str]:
    """What the home still holds of the batch: its definition, its versions, its run, its record,
    and any file of the workflow store that names it or its tasks."""
    workflows = store.workflows_dir()
    left = [
        str(p)
        for p in (
            native_defs.defs_root() / batch.name,
            workflows / "versions" / batch.name,
            workflows / "batches" / f"{batch.name}.json",
            store.run_dir(batch.run_id),
        )
        if p.exists()
    ]
    if store.get(batch.run_id) is not None:
        left.append(f"the run {batch.run_id}")
    for path in workflows.rglob("*"):
        if path.is_file() and path.suffix in (".json", ".jsonl", ".txt", ".md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if WORDS in text or batch.name in text:
                left.append(str(path))
    return left


async def _ended(run_id: str) -> None:
    """Until the batch's run has ended: its tasks have no subagent manager here, so each fails."""
    await _until(
        lambda: (store.get(run_id) or SimpleNamespace(is_terminal=True)).is_terminal, within=30
    )


async def _polled_until_gone(gw: SimpleNamespace, batch: SimpleNamespace) -> list[str]:
    """The supervisor's poll, made by hand until the batch's run is gone or ten polls have passed:
    a live run is stopped on one poll and removed on a later one."""
    for _ in range(10):
        await gw.supervisor._poll_once()
        if store.get(batch.run_id) is None:
            break
        await _ended(batch.run_id)
    return _anything_left(gw.home, batch)


# ── another chat's agent reads none of a private chat's batch ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [TEMPORARY, INCOGNITO])
@pytest.mark.parametrize("path", ["native", "tool_server"])
async def test_another_chats_agent_finds_nothing_of_a_private_chats_batch(chats, private, path):
    """🔴 Red on integration: the ordinary chat's agent was listed the private chat's batch, read
    its tasks in the chat's own words, and could start it again and delete it."""
    batch = await _batch(chats, private)

    listed = await _listed(path, chats, OTHER)
    assert batch.name not in listed and WORDS not in listed, listed

    theirs = await _answers(path, chats, OTHER, _calls(batch))
    never = await _answers(path, chats, OTHER, _calls(batch, run_id=NEVER_RUN, name=NEVER_BATCH))
    for asked, answer, none in zip(_calls(batch), theirs, never, strict=True):
        assert WORDS not in answer, (asked, answer)
        assert _unnamed(answer, batch.run_id, batch.name) == _unnamed(
            none, NEVER_RUN, NEVER_BATCH
        ), (asked, answer, none)
    run = store.get(batch.run_id)
    assert run is not None and run.status is not RunStatus.CANCELLED, "another chat stopped it"
    assert [
        r for r in store.list_runs(limit=50)[0] if r.id != batch.run_id
    ] == [], "another chat started or forked it"

    # The chat that started it reads its batch, its tasks in its own words.
    [mine] = await _answers(path, chats, private, [("workflow_status", {"run_id": batch.run_id})])
    assert WORDS in mine and batch.run_id in mine, mine


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [TEMPORARY, INCOGNITO])
async def test_the_routes_answer_another_chats_agent_as_for_a_batch_that_never_existed(
    chats, private
):
    """🔴 Red on integration: the routes the tool server an agent CLI runs calls answered the
    ordinary chat's agent with the private chat's batch definition and its run."""
    batch = await _batch(chats, private)
    theirs = await _route_answers(chats, OTHER, batch)
    never = await _route_answers(chats, OTHER, batch, run_id=NEVER_RUN, name=NEVER_BATCH)
    for (status, body), (none_status, none_body) in zip(theirs, never, strict=True):
        assert status == none_status == 404, (body, none_body)
        assert WORDS not in json.dumps(body), body
        assert _unnamed(body, batch.run_id, batch.name) == _unnamed(
            none_body, NEVER_RUN, NEVER_BATCH
        ), (body, none_body)
    # Its own chat reads its run there.
    status, body = await _internal(
        chats, "GET", f"/api/workflows/runs/{batch.run_id}", work=private
    )
    assert status == 200 and body["run_id"] == batch.run_id, body


@pytest.mark.asyncio
async def test_your_workflows_page_lists_your_definitions_and_each_chats_batch_marked_as_its(chats):
    """Your library is every definition you can act on, and no batch is one of them. 🔴 Red on
    integration: each batch was a definition in your library under a machine name."""
    temporary = await _batch(chats, TEMPORARY)
    ordinary = await _batch(chats, ORDINARY)

    defs = {d["name"] for d in (await _owners(chats, "/api/workflows"))["defs"]}
    assert TWO_STEPS["name"] in defs
    assert temporary.name not in defs and ordinary.name not in defs, defs

    rows = {r["id"]: r for r in (await _owners(chats, "/api/workflows/runs"))["runs"]}
    assert rows[temporary.run_id]["origin"]["session_key"] == TEMPORARY
    assert rows[temporary.run_id]["memory_mode"] == "temporary"
    assert rows[ordinary.run_id]["origin"]["session_key"] == ORDINARY
    # You read each one whole.
    for batch in (temporary, ordinary):
        body = await _owners(chats, f"/api/workflows/runs/{batch.run_id}")
        assert body["run_id"] == batch.run_id


# ── nothing of it outlives the chat ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nothing_of_a_temporary_chats_batch_outlives_the_chat(chats):
    """🔴 Red on integration: its run was deleted when the chat ended, and its definition, with the
    chat's tasks in its own words, stayed in your library, with every version of it."""
    batch = await _batch(chats, TEMPORARY)
    await _ended(batch.run_id)
    await chats.supervisor._poll_once()
    assert store.get(batch.run_id) is not None, "kept while its chat runs"

    session = chats.state._sessions[TEMPORARY.removeprefix("dashboard:")]
    forget_temporary_chat(chats.state, session, why="it was evicted as inactive")

    assert await _polled_until_gone(chats, batch) == []


@pytest.mark.asyncio
async def test_nothing_of_an_incognito_chats_batch_outlives_its_deletion(chats):
    """An Incognito chat's batch is kept as its transcript is, until the chat is deleted. 🔴 Red on
    integration: its run and its definition stayed after the chat was deleted."""
    from personalclaw.dashboard.chat_utils import persisted_history_key

    batch = await _batch(chats, INCOGNITO)
    await _ended(batch.run_id)
    await chats.supervisor._poll_once()
    assert store.get(batch.run_id) is not None, "kept while its chat is kept"

    # The gateway lets the chat go (an eviction, a restart) and keeps its transcript, as it keeps
    # an Incognito chat's: its batch is kept with it.
    name = INCOGNITO.removeprefix("dashboard:")
    log = chats.state.conversation_log
    log.append(persisted_history_key(log, name), "user", f"Plan {WORDS}")
    chats.state._sessions.pop(name)
    await chats.supervisor._poll_once()
    assert store.get(batch.run_id) is not None, "kept while its transcript is"

    done = await delete_chats(chats.state, [name], by="you")
    assert done.deleted, done

    assert await _polled_until_gone(chats, batch) == []


@pytest.fixture
def asking(chats):
    """The gateway's own subagent manager and start relay, as the gateway wires them, with no
    chat's Trust on: a batch that only reads asks you once, in your Inbox, and waits."""
    from unittest.mock import patch

    from test_gateway import _make_orchestrator
    from test_subagent import _mock_ctx_builder, _mock_sessions

    sessions = _mock_sessions()
    sessions.get_approval_policy = lambda key: ""
    orch = _make_orchestrator()
    orch.sessions = sessions
    orch.ctx_builder = _mock_ctx_builder()
    orch.dashboard_state = chats.state
    with patch("personalclaw.subagent.SubagentManager.start_reaper"):
        orch._init_subagents()
    chats.state.subagents = orch.subagent_mgr
    return chats


@pytest.mark.asyncio
async def test_a_batch_waiting_for_its_ask_ends_with_its_temporary_chat(asking):
    """🔴 Red on integration: a batch whose chat ended while it waited for your Allow kept its ask
    in your Inbox and its record, with its tasks, on disk, where an Allow still started it."""
    [(ok, text)] = await _tool_server(asking, ("subagent_run", {"tasks": TASKS}), key=TEMPORARY)
    assert ok and '"awaiting_approval"' in text, text
    head = json.loads(text.splitlines()[0])
    name, ask = str(head["batch"]), str(head["approval"])
    await _until(lambda: ask in asking.state._pending_approvals)
    assert batch_start.read_record(name) is not None, "recorded while it waits"

    session = asking.state._sessions[TEMPORARY.removeprefix("dashboard:")]
    forget_temporary_chat(asking.state, session, why="it was evicted as inactive")
    await asking.supervisor._poll_once()

    assert ask not in asking.state._pending_approvals, "its ask still waits for you"
    assert batch_start.read_record(name) is None
    assert store.list_runs(workflow_name=name, limit=1)[0] == [], "it started after its chat ended"


# ── an ordinary chat's batch still works, for that chat ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["native", "tool_server"])
async def test_an_ordinary_chats_batch_is_that_chats(chats, path):
    """Its chat's agent reads its batch's run, steps and reports. 🔴 Red on integration: another
    ordinary chat's agent read it too, its tasks in the first chat's words."""
    batch = await _batch(chats, ORDINARY)
    [mine, step] = await _answers(
        path,
        chats,
        ORDINARY,
        [
            ("workflow_status", {"run_id": batch.run_id}),
            ("workflow_output", {"run_id": batch.run_id, "node_id": batch.step}),
        ],
    )
    assert WORDS in mine and batch.run_id in mine, mine
    assert "WF_RUN_NOT_FOUND" not in step, step

    [theirs] = await _answers(path, chats, OTHER, [("workflow_status", {"run_id": batch.run_id})])
    [none] = await _answers(path, chats, OTHER, [("workflow_status", {"run_id": NEVER_RUN})])
    assert _unnamed(theirs, batch.run_id) == _unnamed(none, NEVER_RUN), theirs


# ── what every chat's turn is told is running ──────────────────────────────────────────────────


def test_a_turns_context_lists_only_the_runs_its_chat_reads(tmp_path, monkeypatch):
    """🔴 Red on integration: every chat's turn was told of the private chat's runs and its batch,
    by name, id and what each waited on."""
    from personalclaw.context import ContextBuilder
    from personalclaw.memory import MemoryStore
    from personalclaw.skills import SkillsLoader

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)

    def _running(name: str, session: str, kind: OriginKind, mode: str = "") -> str:
        extra = ownership.stamp_run_mode({}, ownership.MemoryMode(mode)) if mode else {}
        run = store.create(
            WorkflowRun(
                id="",
                workflow_name=name,
                status=RunStatus.NEEDS_INPUT,
                origin=RunOrigin(kind=kind, session_key=session),
                attention={"prompt": f"Which day suits {WORDS}?"} if mode else {},
                extra=extra,
            )
        )
        return run.id

    temporary_batch = _running("subagent-batch-1", TEMPORARY, OriginKind.SUBAGENT_TOOL, "temporary")
    incognito_run = _running("two-steps", INCOGNITO, OriginKind.CHAT, "incognito")
    ordinary_batch = _running("subagent-batch-2", ORDINARY, OriginKind.SUBAGENT_TOOL)
    yours = _running("two-steps", "", OriginKind.API)
    builder = ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )

    told, _ = builder.build_message("What is running?", is_new_session=True, session_key=OTHER)
    assert yours in told
    for theirs in (temporary_batch, incognito_run, ordinary_batch):
        assert theirs not in told, told
    assert WORDS not in told and "subagent-batch" not in told, told

    own, _ = builder.build_message("What is running?", is_new_session=True, session_key=ORDINARY)
    assert ordinary_batch in own and yours in own
    assert temporary_batch not in own and incognito_run not in own, own
