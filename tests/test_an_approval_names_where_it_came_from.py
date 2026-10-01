"""A pending approval says where it came from, and every surface names it by those words.

A loop's worker asks on the chat path, so its approval carried no source of its own, and a channel's
prompt for it read "[chat] Approve bash?" although no chat had asked: the registry handed the
channel ``source or "chat"``. Workflow steps and triggers came to a channel as "[subagent]". The
dashboard's cards each guessed the source from the session key, or showed none.

The registry's entry now carries ``source_label``, the work that asked in a few words (the chat,
the loop, the workflow's step, the trigger, the MCP server), and that label is what a channel's
prompt is tagged with, what the Inbox row keeps, and what the dashboard's card says it is from.

Only the channel is a fake. The registry, the loop store, the run store and the rules file are
real, in a scratch home.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import channel_delivery
from personalclaw import notification_kinds as nk
from personalclaw import notification_rules as rules
from personalclaw.approval_answer import YOU
from personalclaw.approval_source import approval_source_label
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.config.loader import CRED_OWNER_ID, AppConfig
from personalclaw.llm_helpers import LLMEvent

PROVIDER = "achat"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        er, "_entity_settings_path", lambda entity: tmp_path / "entity_settings" / f"{entity}.json"
    )
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    for key in (CRED_OWNER_ID, owner_id_credential(PROVIDER)):
        monkeypatch.delenv(key, raising=False)
    yield tmp_path
    os.environ.pop(owner_id_credential(PROVIDER), None)
    channel_delivery.register(None, provider=PROVIDER)


class _Channel:
    """A channel's outbound half: what each prompt was tagged with, and the texts it was sent."""

    def __init__(self) -> None:
        self.sources: list[str] = []
        self.prompts: list[Any] = []
        self.sent: list[str] = []

    async def open_dm(self, user_id: str) -> str:
        return f"dm-{user_id}"

    async def deliver_text(self, channel: str, text: str, thread_ts: str = "", **_kw) -> str:
        self.sent.append(text)
        return "1"

    async def request_approval(
        self, event, *, source, parent_session_key="", sessions=None, on_prompted=None
    ):  # noqa: E301 - the protocol's shape
        self.sources.append(source)
        pending = SimpleNamespace(future=asyncio.get_running_loop().create_future())
        self.prompts.append(pending)
        if on_prompted:
            on_prompted(pending)
        return (await pending.future) == "approved"


def _connect(handle: Any) -> Any:
    channel_delivery.register(handle, provider=PROVIDER)
    save_credential(owner_id_credential(PROVIDER), "owner-1")
    return handle


def _state(tmp_path):
    """The registry, with approvals sent to the owner's channel (the Channel DM target)."""
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog

    state = DashboardState(
        sessions=MagicMock(count=0),
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
    )
    state.broadcast_ws = MagicMock()
    registered = nk.kind_for_legacy(nk.APPROVAL)
    assert rules.ensure_target(registered.source, registered.kind, "channel_dm")
    return state


async def _until(check, what: str) -> None:
    for _ in range(300):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never: {what}")


async def _ask_in(state, chat, request_id: str = "r-1") -> asyncio.Future:
    """What a chat's runner does for a call that needs approval (``chat_runner``): park its
    future, then publish it. A loop's worker asks exactly this way."""
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    chat._approval_futures[request_id] = future
    await state.hold_session_approval(
        chat,
        request_id,
        tool="write_file",
        tool_input='{"path": "README.md", "content": "## Storage"}',
        tool_purpose="rewrite the storage section",
        agent="personalclaw-coder",
        risk="caution",
        is_read_only=False,
        blast_radius=None,
        grant_agent="",
    )
    return future


def _loop(name: str):
    from personalclaw.loop import store
    from personalclaw.loop.loop import Loop

    return store.create(Loop(id="", name=name, kind="code", task="update the README"))


# ── a loop's worker ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_loop_workers_approval_is_tagged_with_its_loop_on_the_channel(tmp_path):
    channel = _connect(_Channel())
    state = _state(tmp_path)
    loop = _loop("Fix the README")
    worker = state.get_or_create_session(name=f"loop-{loop.id}")

    waiting = await _ask_in(state, worker)
    await _until(lambda: channel.sources, "the channel was asked")

    assert channel.sources == ["loop “Fix the README”"], "a loop's ask was called a chat"
    entry = next(iter(state._pending_approvals.values()))
    assert entry["source_label"] == "loop “Fix the README”"
    channel.prompts[0].future.set_result("approved")
    assert await asyncio.wait_for(waiting, timeout=5) == "approved"


@pytest.mark.asyncio
async def test_a_task_worker_of_the_loop_is_tagged_with_the_same_loop(tmp_path):
    channel = _connect(_Channel())
    state = _state(tmp_path)
    loop = _loop("Fix the README")
    worker = state.get_or_create_session(name=f"loop-{loop.id}-t-0a1b2c3d")

    waiting = await _ask_in(state, worker)
    await _until(lambda: channel.sources, "the channel was asked")
    assert channel.sources == ["loop “Fix the README”"]
    channel.prompts[0].future.set_result("rejected")
    assert await asyncio.wait_for(waiting, timeout=5) == "rejected"


@pytest.mark.asyncio
async def test_a_chats_own_approval_is_still_tagged_with_its_chat(tmp_path):
    channel = _connect(_Channel())
    state = _state(tmp_path)
    chat = state.get_or_create_session(name="chat-7")
    chat.title = "Trip planning"

    waiting = await _ask_in(state, chat)
    await _until(lambda: channel.sources, "the channel was asked")
    assert channel.sources == ["chat “Trip planning”"]
    channel.prompts[0].future.set_result("approved")
    assert await asyncio.wait_for(waiting, timeout=5) == "approved"


# ── the registry's entry and its Inbox row ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_workflow_steps_approval_names_the_workflow_and_the_step(tmp_path):
    from personalclaw.inbox import InboxStore
    from personalclaw.workflows import store as run_store
    from personalclaw.workflows.models import WorkflowRun

    run = run_store.create(WorkflowRun(id="", workflow_name="deep-research"))
    state = _state(tmp_path)
    inbox = InboxStore(tmp_path / "inbox_items.json")
    state._inbox_svc = SimpleNamespace(inbox=inbox)

    waiter = asyncio.ensure_future(
        state.request_approval(
            "spawn:1",
            "subagent",
            "bash",
            tool_input={"command": "pwd; ls"},
            session=f"workflow:{run.id}:sweep",
        )
    )
    await _until(lambda: "spawn:1" in state._pending_approvals, "the approval was listed")
    entry = state._pending_approvals["spawn:1"]
    assert entry["source_label"] == "workflow “deep-research” · step “sweep”"

    # Its Inbox row keeps the words, for a card shown once the approval has left the list.
    rows = [i for i in inbox.items.values() if i.refs.get("approval") == "spawn:1"]
    assert [r.refs.get("source_label") for r in rows] == [entry["source_label"]]

    state.resolve_approval("spawn:1", False, by=YOU)
    await asyncio.wait_for(waiter, timeout=5)


@pytest.mark.asyncio
async def test_an_approval_no_channel_can_prompt_for_names_its_source_in_the_link(tmp_path):
    """The message with the link to answer it, sent where no channel has buttons, says where the
    approval came from too."""

    class _NoButtons(_Channel):
        request_approval = None  # type: ignore[assignment]

    channel = _connect(_NoButtons())
    state = _state(tmp_path)
    loop = _loop("Fix the README")
    worker = state.get_or_create_session(name=f"loop-{loop.id}")

    waiting = await _ask_in(state, worker)
    await _until(lambda: channel.sent, "the owner was told")
    assert "write_file, from loop “Fix the README”" in channel.sent[0], channel.sent[0]
    worker._approval_futures["r-1"].set_result("rejected")
    await asyncio.wait_for(waiting, timeout=5)


# ── the gateway's subagent relay ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_workflow_steps_subagent_is_tagged_with_its_step_not_as_a_subagent(tmp_path):
    """The gateway asks a channel for a background call before the registry lists it, and tags
    the prompt with the words the entry will carry."""
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.workflows import store as run_store
    from personalclaw.workflows.models import WorkflowRun

    run = run_store.create(WorkflowRun(id="", workflow_name="deep-research"))
    handle = _connect(MagicMock())
    handle.request_approval = AsyncMock(return_value=True)
    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg)
    orch.sessions = MagicMock()
    ds = MagicMock()
    ds.is_yolo_active.return_value = False
    ds._sessions = {}
    ds.channel_provider_for.return_value = ""
    ds.request_approval = AsyncMock(return_value=False)
    orch.dashboard_state = ds
    event = LLMEvent(kind="permission_request", request_id="spawn:1:call", title="bash")

    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        approve = orch._interactive_approval(
            "subagent", session_resolver=lambda _rid: f"workflow:{run.id}:sweep"
        )
        assert bool(await approve(event, "")) is True

    assert handle.request_approval.call_args.kwargs["source"] == (
        "workflow “deep-research” · step “sweep”"
    )


# ── the words, origin by origin ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("asked", "label"),
    [
        ({"source": "", "session": "chat-7", "title": "Trip planning"}, "chat “Trip planning”"),
        ({"source": "", "session": "chat-7"}, "chat"),
        (
            {"source": "subagent", "session": "chat-7", "title": "Trip planning"},
            "subagent of chat “Trip planning”",
        ),
        ({"source": "subagent", "session": ""}, "subagent"),
        (
            {"source": "subagent", "session": "", "trigger": "t1", "trigger_name": "Friday digest"},
            "trigger “Friday digest”",
        ),
        ({"source": "subagent", "session": "", "trigger": "t1"}, "trigger"),
        ({"source": "mcp:deepwiki", "session": ""}, "MCP server “deepwiki”"),
        ({"source": "subagent", "session": "workflow:gone1234:sweep"}, "workflow · step “sweep”"),
        ({"source": "something-else", "session": ""}, "background task"),
    ],
)
def test_each_origin_is_named_by_the_work_that_asked(asked, label):
    assert approval_source_label(**asked) == label


def test_a_loops_planner_is_named_by_the_loop():
    from personalclaw.loop.plan_walkthrough import planner_session_key

    loop = _loop("Fix the README")
    assert (
        approval_source_label(source="", session=planner_session_key(loop.id))
        == "loop “Fix the README”"
    )


def test_a_subagent_of_a_loops_worker_is_named_by_the_loop():
    loop = _loop("Nightly digest")
    assert (
        approval_source_label(source="subagent", session=f"loop-{loop.id}")
        == "subagent of loop “Nightly digest”"
    )
