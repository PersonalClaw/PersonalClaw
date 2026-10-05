"""A chat's requests in the Inbox are that chat's own.

The Inbox holds what reached you, and some of it is a chat's work waiting on you: the approval its
agent or one of its subagents asks for, the note one of its calls left when nobody could answer, and
what one of its own runs waits on. A batch's request names each of its tasks in that chat's words.
``inbox_list`` handed every caller every open item, so any chat's agent asked for a briefing read a
Temporary or an Incognito chat's waiting requests, through the built-in agent's tool and through the
tool route a scheduled script, an app and an agent CLI's tool server reach; and the Morning triage
digest sent the first words of each to its model and kept them in its run's record, a run of yours
that every agent's workflow tools read.

Now an agent reads, of the Inbox, its own chat's items and those about no chat (a message from your
mail, what a run of yours waits on). Another chat's item is not there for it: not listed, and not
counted. Morning triage is no chat's work and reads only what is about no chat. You read every item,
on your Inbox page and in a tool you run from your own pages.

Each read is made the way its caller makes it, against the gateway's real sign-in on a live server:
the built-in agent's tool in the gateway (its chat's session bound, as its runtime binds it), and
the tool route with the internal credential the tool server of an agent CLI and a scheduled script
present, and with an app's token. The tool server an agent CLI runs serves no Inbox tool of its
own, so the route is its door to the Inbox.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import auto_denials, mcp_core
from personalclaw.dashboard import session_store, token_auth
from personalclaw.dashboard.server import INTERNAL_ROUTES, MIXED_INTERNAL_ROUTES
from personalclaw.inbox import InboxItem, InboxState, InboxStore, ItemKind
from personalclaw.inbox_providers import native_source
from personalclaw.subagent import SubagentInfo
from personalclaw.workflows import attention, batch_start, ownership, store
from personalclaw.workflows.models import OriginKind, RunOrigin, RunStatus, WorkflowRun

SECRET = "the-internal-credential-for-these-tests"
PORT = 10000
APP = "field-notes"

#: The chat whose agent is asked for a briefing, an ordinary chat, a Temporary one, an Incognito
#: one, and an ordinary chat with nothing of its own waiting.
B, O, T, I, Z = (f"chat-{n}-17000001{n:02d}" for n in range(1, 6))
KEY = {name: f"dashboard:{name}" for name in (B, O, T, I, Z)}
MODE = {B: "persistent", O: "persistent", T: "temporary", I: "incognito", Z: "persistent"}
#: A subagent the Temporary chat started, still at work.
T_HELPER = "a1b2c3d4"

#: What each waiting item says, in the words of the chat it is for, or about no chat.
SAYS = {
    "t_batch": "Gifts for the lantern festival",
    "t_helper": "bakeries open late near the lantern festival",
    "t_run": "Which evening suits the lantern festival?",
    "i_call": "the clinic letter",
    "o_batch": "Gifts for the book club",
    "b_call": "the allotment rota",
    "your_run": "Which day suits the team offsite?",
    "mail": "The plumber can come on Friday.",
}
#: What each caller reads: its own chat's, and what is about no chat.
NO_CHAT = {"your_run", "mail"}
READS = {
    KEY[B]: {"b_call"} | NO_CHAT,
    KEY[O]: {"o_batch"} | NO_CHAT,
    KEY[T]: {"t_batch", "t_helper", "t_run"} | NO_CHAT,
    KEY[I]: {"i_call"} | NO_CHAT,
    f"subagent:{T_HELPER}": {"t_batch", "t_helper", "t_run"} | NO_CHAT,
    "cron:morning-briefing": NO_CHAT,
    f"app:{APP}": NO_CHAT,
}
_COUNT = re.compile(r"^(\d+) open items? in the Inbox")


def _batch_ask(chat: str, about: str) -> tuple[str, str]:
    """``(purpose, said)`` of a batch of two read-only tasks *chat*'s agent started about *about*,
    as the batch's ask words them (`batch_start`)."""
    tasks = [
        batch_start.Task(
            path=f"root.children[{n}]",
            node_id=f"leaf-{n}",
            label=label,
            agent="",
            changes=(),
            writes=(),
        )
        for n, label in enumerate((f"Gifts for {about}", f"A cake for {about}"))
    ]
    return batch_start._ask_text(tasks, folder="", workspace={}, work=None)


async def _until(condition: Any, *, within: float = 10.0) -> None:
    deadline = time.monotonic() + within
    while not condition():
        assert time.monotonic() < deadline, "the condition never held"
        await asyncio.sleep(0.02)


@pytest_asyncio.fixture
async def gateway(tmp_path, monkeypatch):
    """The gateway's sign-in, its tool route and its Inbox page on a live server, with each chat's
    requests waiting in the Inbox as the gateway raises them, beside a message from your mail and a
    run of yours waiting on you."""
    import personalclaw.config.loader as loader
    from personalclaw.agents.native.builtin_tools import create_inbox_tools_provider
    from personalclaw.dashboard.handlers.tools import api_tool_invoke
    from personalclaw.dashboard.handlers_inbox import api_inbox_list
    from personalclaw.dashboard.memory_write_gate import memory_write_middleware
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.subagent import SubagentManager

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(session_store, "config_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path / "subagents"
    )
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir()
    monkeypatch.delenv("PERSONALCLAW_BYPASS_LOCAL_NETWORKS", raising=False)
    monkeypatch.delenv("PERSONALCLAW_SESSION_KEY", raising=False)
    token_auth.use_ephemeral_secret(b"a-chats-requests-in-the-inbox-are-its-own")
    token_auth.revoke_all_sessions()

    # The tools an agent's turn is offered include the Inbox's, as the bundled app registers them.
    inbox_tools = create_inbox_tools_provider()
    monkeypatch.setattr(
        "personalclaw.tool_providers.registry.list_providers", lambda: [inbox_tools]
    )
    # An app that declared `inbox_list`, installed with your consent.
    from personalclaw.apps import app_manager

    source = tmp_path / "src" / APP
    source.mkdir(parents=True)
    manifest = {"name": APP, "version": "1.0.0", "displayName": "Field Notes", "description": "x"}
    manifest["permissions"] = {"mcpTools": ["inbox_list"]}
    (source / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert app_manager.install(source, confirm=True).ok

    manager = SubagentManager(sessions=MagicMock(), ctx_builder=None)
    manager._agents[T_HELPER] = SubagentInfo(
        id=T_HELPER, task="Find a bakery open late", parent_session_key=KEY[T]
    )
    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0, subagents=manager)
    state.push_sessions_update = MagicMock()
    for name, mode in MODE.items():
        state.get_or_create_session(name, memory_mode=mode)
    # The running Inbox service's store, which every row is raised into (`inbox.live_store`).
    state._inbox_svc = SimpleNamespace(inbox=InboxStore(), state=InboxState(), sorter=None)
    state._inbox_svc.inbox.load()
    native_source.set_dashboard_state(state)

    rows = await _raise_every_request(state)

    app = web.Application(
        middlewares=[
            token_auth.token_auth_middleware(
                port=PORT,
                internal_routes=INTERNAL_ROUTES,
                mixed_internal_routes=MIXED_INTERNAL_ROUTES,
                internal_secret=SECRET,
            ),
            memory_write_middleware(),
        ]
    )
    app["state"] = state
    app.router.add_post("/api/tools/invoke", api_tool_invoke)
    app.router.add_get("/api/inbox", api_inbox_list)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield _Gateway(client, state, inbox_tools, rows)
    finally:
        for task in rows.asking:
            task.cancel()
        await asyncio.gather(*rows.asking, return_exceptions=True)
        await client.close()
        native_source.set_dashboard_state(None)
        token_auth.revoke_all_sessions()
        token_auth.use_persistent_secret()


async def _raise_every_request(state: Any) -> SimpleNamespace:
    """Each chat's requests, raised as the gateway raises them, and the two items about no chat.
    Returns each item's Inbox id by what it says, and the asks still waiting."""
    asking: list[asyncio.Task[Any]] = []
    loop = asyncio.get_running_loop()

    def _waits(approval_id: str) -> bool:
        return approval_id in state._pending_approvals

    # A batch the Temporary chat's agent and one the ordinary chat's agent started, each waiting
    # for your Allow: asked through the approval registry under the chat's name, as its start is.
    for chat, about, name in ((T, "the lantern festival", "t"), (O, "the book club", "o")):
        purpose, said = _batch_ask(chat, about)
        ask = batch_start.ask_id(f"subagent-batch-1700000100000-{name * 6}")
        asking.append(
            asyncio.create_task(
                state.request_approval(
                    ask,
                    "subagent",
                    "subagent_run",
                    tool_input=said,
                    tool_purpose=purpose,
                    session=chat,
                    risk_level="caution",
                    annotations={"readOnlyHint": True},
                )
            )
        )
        await _until(lambda ask=ask: _waits(ask))
    # The Incognito chat's and the asking chat's own calls, each waiting on the chat's card.
    for chat, words in ((I, SAYS["i_call"]), (B, SAYS["b_call"])):
        session = state.get_or_create_session(chat, memory_mode=MODE[chat])
        session._approval_futures["call-1"] = loop.create_future()
        await state.hold_session_approval(
            session,
            "call-1",
            tool="read_file",
            tool_input=json.dumps({"path": f"notes/{words}.md"}),
            tool_purpose=f"Read {words}",
            agent="personalclaw",
            risk="caution",
            is_read_only=True,
            blast_radius=None,
            grant_agent="personalclaw",
        )
    # A call the Temporary chat's subagent made that nobody could be asked about.
    auto_denials.note_unattended(
        state,
        session_key=f"subagent:{T_HELPER}",
        tool="web_search",
        tool_input=json.dumps({"query": SAYS["t_helper"]}),
    )
    # A run the Temporary chat started, and one of yours, each waiting on you at a gate.
    for session, mode, prompt, name in (
        (KEY[T], ownership.MemoryMode.TEMPORARY, SAYS["t_run"], "festival-plan"),
        ("", None, SAYS["your_run"], "offsite-plan"),
    ):
        run = store.create(
            WorkflowRun(
                id="",
                workflow_name=name,
                status=RunStatus.NEEDS_INPUT,
                origin=RunOrigin(
                    kind=OriginKind.CHAT if session else OriginKind.API, session_key=session
                ),
                extra=ownership.stamp_run_mode({}, mode) if mode else {},
            )
        )
        attention.raise_gate_item(
            state,
            run_id=run.id,
            workflow=name,
            node_id="pick",
            instance_path="root.children[0]",
            epoch=0,
            resume_token=f"resume-{name}",
            ask={"prompt": prompt},
        )
    # A message from your mail.
    inbox = state._inbox_svc.inbox
    inbox.add(
        InboxItem(
            id=f"mail_5ea1ab1e_{time.time():.6f}",
            channel="inbox",
            channel_name="mail",
            thread_ts=None,
            message=SAYS["mail"],
            sender_id="sam@example.com",
            sender_name="Sam",
            source="mail-inbox",
            item_kind=ItemKind.EMAIL.value,
            created_at=time.time(),
        )
    )
    inbox.flush()
    ids = {}
    for label, says in SAYS.items():
        [item_id] = [i.id for i in inbox.open_items() if says in i.message]
        ids[label] = item_id
    return SimpleNamespace(ids=ids, asking=asking)


class _Gateway:
    """The reads a caller makes of the Inbox, by the path it makes them on."""

    def __init__(self, client: TestClient, state: Any, inbox_tools: Any, rows: Any) -> None:
        self.client = client
        self.state = state
        self.inbox_tools = inbox_tools
        self.ids: dict[str, str] = rows.ids

    async def native(self, work: str, **args: Any) -> str:
        """The built-in agent's `inbox_list` in the gateway, for the work *work* names: the runtime
        binds that session for the call (`mcp_core.set_current_session_key`)."""
        token = mcp_core.set_current_session_key(work)
        try:
            result = await self.inbox_tools.invoke("inbox_list", args)
        finally:
            mcp_core.reset_current_session_key(token)
        assert result.success, result.error
        return str(result.output)

    async def route(self, work: str, **args: Any) -> str:
        """`inbox_list` through the gateway's tool route: with the internal credential naming
        *work* (the tool server an agent CLI runs, a scheduled script), or with the app's own token
        when *work* is the app's, as its backend presents it."""
        headers: dict[str, str] = {}
        params: dict[str, str] = {}
        if work.startswith("app:"):
            params["token"] = token_auth.generate_token("owner", ttl_seconds=300, app=APP)
        else:
            headers = {"X-Internal-Secret": SECRET, "X-Session-Key": work}
        resp = await self.client.post(
            "/api/tools/invoke",
            json={"tool": "inbox_list", "arguments": args},
            headers=headers,
            params=params,
        )
        body = await resp.json()
        assert resp.status == 200 and body.get("ok"), body
        return str(body["output"])

    async def read(self, path: str, work: str, **args: Any) -> str:
        return await (self.native if path == "native" else self.route)(work, **args)

    def shown(self, said: str) -> set[str]:
        """Which of the waiting items *said* shows, by what each says."""
        return {label for label, says in SAYS.items() if says in said}

    async def owner(self, method: str, path: str, body: Any = None) -> Any:
        """A call of yours, signed in, from your own pages."""
        owner = token_auth.generate_token("owner", ttl_seconds=300)
        resp = await self.client.request(
            method,
            path,
            json=body,
            headers={"Authorization": f"Bearer {owner}", "X-Session-Key": "dashboard:ui"},
        )
        assert resp.status == 200, await resp.text()
        return await resp.json()


PATHS = ["native", "route"]


# ── another chat's requests are not there for a chat's agent ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATHS)
async def test_a_chats_agent_reads_its_own_requests_and_none_of_another_chats(gateway, path):
    """🔴 Red on integration: the asking chat's agent was listed all eight open items, the
    Temporary chat's batch with each task in its words, the Incognito chat's call, the ordinary
    chat's batch, the Temporary chat's subagent's denied call and its run's question."""
    said = await gateway.read(path, KEY[B])

    assert gateway.shown(said) == READS[KEY[B]], said
    for theirs in ("t_batch", "t_helper", "t_run", "i_call", "o_batch"):
        assert gateway.ids[theirs] not in said, said
    for words in ("lantern festival", "clinic letter", "book club"):
        assert words not in said, said
    # Not counted either: the count is of what it reads.
    match = _COUNT.match(said)
    assert match and int(match.group(1)) == len(READS[KEY[B]]), said


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATHS)
async def test_each_chat_reads_its_own_requests_and_what_is_about_no_chat(gateway, path):
    """🔴 Red on integration: every chat read every chat's requests."""
    for chat in (KEY[T], KEY[I], KEY[O]):
        said = await gateway.read(path, chat)
        assert gateway.shown(said) == READS[chat], f"{chat} on {path}: {said}"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATHS)
async def test_a_subagents_own_call_reads_its_chats_requests(gateway, path):
    """A subagent works for the chat that started it, so it reads that chat's requests and no
    other chat's. 🔴 Red on integration: it read every chat's."""
    said = await gateway.read(path, f"subagent:{T_HELPER}")
    assert gateway.shown(said) == READS[f"subagent:{T_HELPER}"], said


@pytest.mark.asyncio
@pytest.mark.parametrize("work", ["cron:morning-briefing", f"app:{APP}"])
async def test_a_briefing_and_an_app_read_only_what_is_about_no_chat(gateway, work):
    """A scheduled briefing (a scheduled script's call through the tool route) and an app's own
    tool call are no chat's work: they read what is about no chat. 🔴 Red on integration: each was
    handed every chat's requests."""
    said = await gateway.route(work)
    assert gateway.shown(said) == NO_CHAT, said


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PATHS)
async def test_nothing_says_another_chats_request_is_there(gateway, path):
    """A chat with nothing of its own waiting, asking only for agent requests, reads what an empty
    Inbox reads: no count, no title. 🔴 Red on integration: four open items, the other chats'."""
    said = await gateway.read(path, KEY[Z], kind="agent_request")

    gateway.state._inbox_svc.inbox.items.clear()
    empty = await gateway.read(path, KEY[Z], kind="agent_request")
    assert said == empty, said
    assert not _COUNT.match(said) and not gateway.shown(said), said


# ── an item whose chat cannot be told is yours alone ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_item_whose_chat_cannot_be_told_is_yours_alone(gateway, monkeypatch):
    """An item naming its session in a form that names nobody, and one about a run whose record
    cannot be read, are read by you and by no agent. 🔴 Red on integration: every agent read both."""
    from personalclaw.inbox import emit_attention_item

    state = gateway.state
    garbled = emit_attention_item(
        state,
        source="system",
        kind="agent_request",
        item_kind=ItemKind.AGENT_REQUEST.value,
        title="Approval needed: read_file",
        body="Read the seed catalogue.",
        refs={"session": [KEY[T]]},
    )
    unreadable = emit_attention_item(
        state,
        source="workflow",
        kind="needs_input",
        item_kind=ItemKind.NEEDS_INPUT.value,
        title="Waiting for you",
        body="Which seeds to order?",
        refs={"workflow": "5eed5eed"},
    )
    real_get = store.get

    def _get(run_id: str) -> Any:
        if run_id == "5eed5eed":
            raise OSError("the run's record cannot be read")
        return real_get(run_id)

    monkeypatch.setattr(store, "get", _get)

    for path in PATHS:
        said = await gateway.read(path, KEY[B])
        assert garbled not in said and unreadable not in said, said
        assert "seed" not in said, said
    yours = {row["id"] for row in await gateway.owner("GET", "/api/inbox")}
    assert {garbled, unreadable} <= yours


# ── you read every item ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_you_read_every_request_on_your_inbox_page_and_in_a_tool_you_run(gateway):
    """Your Inbox page lists every chat's requests, and a tool you run from your own pages reads
    them all. (A guard: it holds before and after.)"""
    listed = await gateway.owner("GET", "/api/inbox")
    assert set(gateway.ids.values()) <= {row["id"] for row in listed}

    tried = await gateway.owner("POST", "/api/tools/invoke", {"tool": "inbox_list"})
    assert tried["ok"], tried
    assert gateway.shown(tried["output"]) == set(SAYS), tried["output"]


# ── the Morning triage digest is no chat's work ────────────────────────────────────────────────


def test_morning_triage_collects_only_what_is_about_no_chat(gateway):
    """The digest sends what it collects to its model and keeps it in its run's record, a run of
    yours that every agent's workflow tools read: it collects no chat's request, and a carried
    proposal about one drops out.
    🔴 Red on integration: it collected every chat's requests, a Temporary chat's batch included."""
    from personalclaw.proactive.collect import collect_all, current_item
    from personalclaw.proactive.manifest import SOURCE_INBOX

    state = gateway.state
    inbox = state._inbox_svc.inbox
    collected = collect_all(inbox_store=inbox, state=state, include_runs=False)

    assert {c.source_id for c in collected if c.source == SOURCE_INBOX} == {
        gateway.ids[label] for label in NO_CHAT
    }
    assert not any("lantern festival" in c.title for c in collected), collected
    for theirs in ("t_batch", "t_run", "i_call", "b_call"):
        assert (
            current_item(SOURCE_INBOX, gateway.ids[theirs], inbox_store=inbox, state=state) is None
        )
    assert current_item(SOURCE_INBOX, gateway.ids["mail"], inbox_store=inbox, state=state)
