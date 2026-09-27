"""Nothing core hands a channel carries a key: core masks it once, where every channel is reached.

A channel app sends what it is handed to a service outside this machine: Slack, Telegram, Discord,
a mail server. Measured on main, the masking was split between core's callers and the apps, each
covering some paths:

* the Slack and Telegram apps' ``deliver_notification`` sent the title and the text as they were;
* Telegram's ``deliver_rich`` sent its fallback text as it was, and Discord's its components;
* every channel's ``deliver_cron_result`` put the automation's name in its header as it was, and
  the email app put a notification's title in the subject as it was;
* core's own callers masked on some paths (a heartbeat's result, in ``_deliver_result``) and not
  others: a subagent's reply, an approval's title and input, and a send-message action's title
  were handed to the channel as they were, safe only while every app masked them again.

Now the registry holds every channel's handle behind the mask (``channel_delivery.MaskedDelivery``),
so every door core has to a channel (``delivery_for``, ``owner_reachable``, ``approval_delivery``,
``reach_owner``, ``deliver_to_owner``, and the gateway's and the dashboard's views of them) hands it
text masked with ``security.redact_for_display``, and the apps need no copy of their own.

Each test plants a key and asserts that what the channel was handed does not carry it. The channel
is a recorder standing where a channel app's delivery handle stands: an app renders what it is
handed and posts it, so a key in a recorded call is a key on its way off the machine.
"""

from __future__ import annotations

import inspect
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import channel_delivery
from personalclaw.channel_delivery import ChannelDelivery
from personalclaw.config.credentials import owner_id_credential
from personalclaw.config.loader import AppConfig
from personalclaw.llm.events import AgentEvent

SECRET = "sk-ant-api03-" + ("A" * 20) + ("B" * 20) + ("C" * 15)
MASK = "[REDACTED: credential]"
OWNER = "4242"
DM = f"dm-{OWNER}"


class Channel:
    """A channel app's delivery handle that records every call core makes on it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def open_dm(self, user_id: str) -> str:
        return f"dm-{user_id}"

    def list_reply_channels(self) -> list[dict]:
        return []

    def is_tracked_channel(self, channel_id: str) -> bool:
        return True

    def build_thread_link(self, channel: str, ts: str) -> str:
        return ""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        async def send(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            return True if name == "request_approval" else "1700000000.000100"

        return send

    def handed(self) -> str:
        """Everything it was handed, as one string to search."""
        return repr(self.calls)


@pytest.fixture(autouse=True)
def _no_channel_left_behind():
    """The registry is process-level: a handle one test leaves would answer the next one."""
    channel_delivery.register(None)
    yield
    channel_delivery.register(None)


@pytest.fixture
def telegram(monkeypatch) -> Channel:
    """A connected channel that knows the owner."""
    channel = Channel()
    channel_delivery.register(channel, provider="telegram")
    monkeypatch.setenv(owner_id_credential("telegram"), OWNER)
    return channel


def _gateway() -> Any:
    from personalclaw.gateway import GatewayOrchestrator

    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        return GatewayOrchestrator(cfg)


# ── the four texts the channel apps sent as they were ──────────────────────────────────────


@pytest.mark.asyncio
async def test_a_run_result_and_its_title_reach_the_owner_masked(telegram):
    """``deliver_to_owner`` is the door every owner notification goes through: a heartbeat's or a
    hook's result, a subagent's reply, a file, a send-message action."""
    outcome = await channel_delivery.deliver_to_owner(
        lambda d, dm: d.deliver_notification(dm, f"Nightly {SECRET}", f"all green, {SECRET}"),
        title=f"Nightly {SECRET}",
        text=f"all green, {SECRET}",
    )

    assert outcome.delivered
    assert telegram.calls == [
        ("deliver_notification", (DM, f"Nightly {MASK}", f"all green, {MASK}"), {})
    ]


@pytest.mark.asyncio
async def test_a_rich_message_and_its_fallback_are_masked(telegram):
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"deploy key {SECRET}"}},
        {"type": "actions", "elements": [{"type": "button", "action_id": "ack", "value": "j-1"}]},
    ]

    await channel_delivery.reach_owner(
        lambda d, dm: d.deliver_rich(dm, blocks, f"deploy key {SECRET}", thread_ts="1.5")
    )

    [(name, (dm, payload, fallback), kwargs)] = telegram.calls
    assert SECRET not in telegram.handed()
    assert payload[0]["text"]["text"] == fallback == f"deploy key {MASK}"
    assert (name, dm, payload[1], kwargs) == ("deliver_rich", DM, blocks[1], {"thread_ts": "1.5"})
    assert blocks[0]["text"]["text"] == f"deploy key {SECRET}", "the caller's payload was changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("by_keyword", [False, True], ids=["positional", "keyword"])
async def test_an_automations_name_in_a_cron_result_is_masked(telegram, by_keyword):
    given = {
        "channel": "C1",
        "job_name": f"backup {SECRET}",
        "job_id": "job-1",
        "text": f"done {SECRET}",
        "thread_ts": "1.5",
    }
    handle = channel_delivery.delivery_for("telegram")
    assert handle is not None
    if by_keyword:
        await handle.deliver_cron_result(**given)
    else:
        await handle.deliver_cron_result(*given.values())

    [(name, args, kwargs)] = telegram.calls
    assert dict(zip(given, args), **kwargs) == {
        **given,
        "job_name": f"backup {MASK}",
        "text": f"done {MASK}",
    }


# ── core's callers that handed a channel text as it was ────────────────────────────────────


def _gateway_with_subagents() -> tuple[Any, Any]:
    """A gateway whose subagent manager is a stand-in, and the completion callback it was given."""
    orch = _gateway()
    orch.sessions = MagicMock()
    orch.sessions.get_or_create = AsyncMock(return_value=(MagicMock(), True, False))
    orch.sessions.recycle_background = AsyncMock()
    orch.sessions.get_channel = MagicMock(return_value=None)
    orch.ctx_builder = MagicMock()
    orch.ctx_builder.build_message = MagicMock(return_value=("msg", None))
    orch.dashboard_state = MagicMock(_sessions={}, _background_tasks=set())
    orch.dashboard_state.get_session = MagicMock(return_value=None)
    orch.dashboard_state.is_yolo_active.return_value = False
    with (
        patch("personalclaw.trust_mode.is_yolo_active", return_value=False),
        patch("personalclaw.gateway.SubagentManager") as manager,
    ):
        manager.return_value = MagicMock(
            running=[], running_agents_for=MagicMock(return_value=[]), get=MagicMock()
        )
        manager.return_value.get.return_value = None
        orch._init_subagents()
    return orch, manager.call_args.kwargs["on_done"]


@pytest.mark.asyncio
@pytest.mark.parametrize("thread", ["C123", None], ids=["its-thread", "the-owners-dm"])
async def test_a_subagents_reply_reaches_the_channel_masked(telegram, thread):
    """A subagent started from a channel conversation: its synthesized reply goes back to that
    thread, or to the owner's DM when the conversation has none."""
    orch, on_done = _gateway_with_subagents()
    orch.sessions.get_channel = MagicMock(return_value=thread)
    info = MagicMock(
        id="agent-1",
        parent_session_key="C123:1234.567890",
        error=None,
        result="raw result",
        result_path="",
        task="look it up",
        agent="",
        silent=False,
        elapsed=3.0,
        started=time.monotonic() - 3.0,
    )

    with patch(
        "personalclaw.gateway.stream_and_collect",
        new_callable=AsyncMock,
        return_value=f"Here is the key: {SECRET}",
    ):
        await on_done([info])

    [(_, args, _)] = [call for call in telegram.calls if call[0] == "deliver_subagent_reply"]
    assert args[:2] == (thread or DM, f"Here is the key: {MASK}")
    assert SECRET not in telegram.handed()


def _approval_gateway() -> Any:
    """The gateway's approval harness (as in ``test_approval_brief``), with the channel left to
    the registry."""
    from personalclaw.gateway import GatewayOrchestrator

    gateway = GatewayOrchestrator.__new__(GatewayOrchestrator)
    gateway.sessions = MagicMock()
    gateway.sessions.get_pid = MagicMock(return_value=None)
    gateway.sessions.get_channel = MagicMock(return_value=None)
    gateway.sessions.get_thread = MagicMock(return_value=None)
    gateway.dashboard_state = MagicMock(_sessions={})
    gateway.dashboard_state.is_yolo_active.return_value = False
    gateway.dashboard_state.request_approval = AsyncMock(return_value=True)
    gateway._owner_id = OWNER
    gateway._cfg = MagicMock()
    gateway._cfg.agent.max_subagents = 4
    gateway._approval_mode = None
    return gateway


@pytest.mark.asyncio
async def test_an_approval_asked_on_a_channel_shows_the_owner_no_key(telegram):
    event = AgentEvent(
        kind="permission_request",
        request_id="req-1",
        title=f"deploy with {SECRET}",
        tool_purpose=f"push the build, authenticating with {SECRET}",
        tool_input={"command": f"deploy --token {SECRET}", "retries": 2},
    )

    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        decision = await _approval_gateway()._interactive_approval("subagent")(
            event, "1775113012.860459"
        )

    assert decision.approved is True
    [(name, (asked,), kwargs)] = telegram.calls
    assert name == "request_approval" and SECRET not in telegram.handed()
    assert (asked.request_id, asked.title) == ("req-1", f"deploy with {MASK}")
    assert asked.tool_input == {"command": f"deploy --token {MASK}", "retries": 2}
    assert kwargs["source"] == "subagent"
    assert SECRET in event.title, "the gateway's own request was changed"


@pytest.mark.asyncio
async def test_a_send_message_actions_title_reaches_the_owner_masked(telegram):
    """The action masked its text and not its title."""
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.send_message_provider import SendMessageActionProvider
    from personalclaw.dashboard.state import DashboardState

    state = DashboardState.__new__(DashboardState)
    with patch(
        "personalclaw.action_providers.send_message_provider.get_action_services",
        return_value=MagicMock(state=state),
    ):
        result = await SendMessageActionProvider().execute(
            {"title": f"Deploy {SECRET}", "text_template": "finished"},
            ActionContext(event="Stop", context=""),
        )

    assert result.success, result.error
    assert telegram.calls == [("deliver_text", (DM, f"*Deploy {MASK}*\nfinished"), {})]


@pytest.mark.asyncio
async def test_a_heartbeats_result_is_masked_once(telegram):
    """The heartbeat's path masked its own text already; masking again at the door leaves one
    mask, not a mask of a mask."""
    orch = _gateway()
    orch.dashboard_state = None

    await orch._deliver_result(f"Heartbeat {SECRET}", "task", f"result {SECRET}", "channel")

    assert telegram.calls == [
        ("deliver_notification", (DM, f"Heartbeat {MASK}", f"result {MASK}"), {})
    ]


# ── every door ─────────────────────────────────────────────────────────────────────────────


def _door(name: str) -> Any:
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.gateway import GatewayOrchestrator

    state = DashboardState.__new__(DashboardState)
    gateway = GatewayOrchestrator.__new__(GatewayOrchestrator)
    return {
        "delivery_for": lambda: channel_delivery.delivery_for("telegram"),
        "owner_reachable": channel_delivery.owner_reachable,
        # `(provider, delivery)`: the provider names who answers there.
        "approval_delivery": lambda: channel_delivery.approval_delivery()[1],
        "the dashboard's delivery_for": lambda: state.delivery_for("telegram"),
        "the dashboard's channel_delivery": lambda: state.channel_delivery,
        "the gateway's _channel_delivery": lambda: gateway._channel_delivery,
    }[name]()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "door",
    [
        "delivery_for",
        "owner_reachable",
        "approval_delivery",
        "the dashboard's delivery_for",
        "the dashboard's channel_delivery",
        "the gateway's _channel_delivery",
    ],
)
async def test_every_door_to_a_channel_hands_it_masked_text(telegram, door):
    await _door(door).deliver_text("C1", f"key {SECRET}", thread_ts="1.5")

    assert telegram.calls == [("deliver_text", ("C1", f"key {MASK}"), {"thread_ts": "1.5"})]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "writer",
    ["register", "register_channel_delivery", "the gateway's setter", "the dashboard's setter"],
)
async def test_every_way_a_channel_is_registered_puts_it_behind_the_mask(writer):
    """The four ways a transport hands core its handle, all of which the shipped apps use."""
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.gateway import GatewayOrchestrator

    channel = Channel()
    if writer == "register":
        channel_delivery.register(channel, provider="discord")
    elif writer == "register_channel_delivery":
        GatewayOrchestrator.__new__(GatewayOrchestrator).register_channel_delivery(
            channel, "discord"
        )
    elif writer == "the gateway's setter":
        GatewayOrchestrator.__new__(GatewayOrchestrator)._channel_delivery = channel
    else:
        DashboardState.__new__(DashboardState).channel_delivery = channel

    handle = channel_delivery.owner_reachable()
    assert handle is not None
    await handle.deliver_text("C1", f"key {SECRET}")
    assert channel.calls == [("deliver_text", ("C1", f"key {MASK}"), {})]


# ── the contract of the mask ───────────────────────────────────────────────────────────────

#: The protocol's methods that hand a channel no text: each reads, opens a conversation with an
#: id, or ends a stream.
READS = {
    "open_dm",
    "resolve_user_name",
    "resolve_user_profile",
    "channel_info",
    "list_reply_channels",
    "is_tracked_channel",
    "build_thread_link",
    "stop_stream",
}

#: The text each sending method hands a channel.
TEXT = {
    "deliver_text": {"text"},
    "deliver_rich": {"payload", "fallback_text"},
    "deliver_cron_result": {"job_name", "text"},
    "deliver_notification": {"title", "text"},
    "deliver_chat_mirror": {"text"},
    "deliver_subagent_reply": {"text"},
    "upload_attachment": {"title", "initial_comment"},
    "start_stream": {"initial_text"},
    "append_stream_task": {"title"},
}


def test_every_protocol_method_is_masked_or_hands_no_text():
    """A method added to the protocol must be one or the other, so a new way to hand a channel
    text cannot pass the mask by being forgotten."""
    protocol = {name for name, member in vars(ChannelDelivery).items() if callable(member)} - {
        name for name in vars(ChannelDelivery) if name.startswith("_")
    }
    masked = {name for name in vars(channel_delivery.MaskedDelivery) if not name.startswith("_")}

    assert masked == set(TEXT) | {"request_approval"}
    assert protocol == masked | READS
    assert not masked & READS


@pytest.mark.asyncio
@pytest.mark.parametrize("by_keyword", [False, True], ids=["positional", "keyword"])
@pytest.mark.parametrize("method", sorted(TEXT))
async def test_each_text_is_masked_and_everything_else_arrives_as_given(
    telegram, method, by_keyword
):
    params = [p for p in inspect.signature(getattr(ChannelDelivery, method)).parameters.values()][
        1:
    ]
    given = {
        p.name: f"{p.name} {SECRET}" if p.name in TEXT[method] else f"{p.name}-1" for p in params
    }
    positional = [p.name for p in params if p.kind == p.POSITIONAL_OR_KEYWORD and not by_keyword]
    handle = channel_delivery.delivery_for("telegram")
    assert handle is not None

    await getattr(handle, method)(
        *(given[name] for name in positional),
        **{name: value for name, value in given.items() if name not in positional},
    )

    [(name, args, kwargs)] = telegram.calls
    assert name == method
    assert dict(zip(positional, args), **kwargs) == {
        name: f"{name} {MASK}" if name in TEXT[method] else value for name, value in given.items()
    }


@pytest.mark.asyncio
async def test_an_approval_keeps_what_routes_the_answer(telegram):
    """Masked: what the owner is shown. Kept: the request's id, its options and its kind."""
    event = AgentEvent(
        kind="permission_request",
        request_id="req-9",
        title=f"call {SECRET}",
        text=f"why {SECRET}",
        tool_purpose=f"because {SECRET}",
        tool_input=f"--key {SECRET}",
        tool_input_obj={"key": SECRET},
        tool_meta={"brief": {"tool": f"call {SECRET}", "risk": "high"}},
        options=[{"optionId": "allow", "name": "Allow"}],
    )
    asking = channel_delivery.approval_delivery()
    assert asking is not None
    provider, handle = asking
    assert provider == "telegram"

    assert await handle.request_approval(event, source="chat") is True

    [(_, (asked,), kwargs)] = telegram.calls
    assert SECRET not in repr(asked)
    assert (asked.kind, asked.request_id, asked.options) == (
        "permission_request",
        "req-9",
        [{"optionId": "allow", "name": "Allow"}],
    )
    assert asked.tool_meta == {"brief": {"tool": f"call {MASK}", "risk": "high"}}
    assert kwargs == {"source": "chat"}


@pytest.mark.asyncio
async def test_the_dashboards_ask_on_a_channel_is_masked_too(telegram):
    """The dashboard asks a channel with a namespace, not the gateway's event."""
    ask = SimpleNamespace(request_id="a1b2", title=f"t {SECRET}", tool_input=f"i {SECRET}")
    handle = channel_delivery.delivery_for("telegram")
    assert handle is not None

    await handle.request_approval(ask, source="chat")

    [(_, (asked,), _)] = telegram.calls
    assert vars(asked) == {"request_id": "a1b2", "title": f"t {MASK}", "tool_input": f"i {MASK}"}
    assert ask.title == f"t {SECRET}", "the dashboard's own request was changed"


@pytest.mark.asyncio
async def test_a_request_with_nothing_to_mask_is_handed_as_it_is(telegram):
    event = AgentEvent(kind="permission_request", request_id="req-2", title="read_file")
    handle = channel_delivery.delivery_for("telegram")
    assert handle is not None

    await handle.request_approval(event, source="chat")

    assert telegram.calls[0][1][0] is event


@pytest.mark.asyncio
async def test_reads_and_a_channels_own_methods_reach_it_unchanged(telegram):
    class Discord:
        async def open_dm(self, user_id: str) -> str:
            return f"dm-{user_id}"

        def list_reply_channels(self) -> list[dict]:
            return [{"id": "C1", "name": "general"}]

        def invite_url(self) -> str:
            return "https://discord.example/invite"

    channel_delivery.register(Discord(), provider="discord")
    handle = channel_delivery.delivery_for("discord")
    assert handle is not None

    assert await handle.open_dm("99") == "dm-99"
    assert handle.list_reply_channels() == [{"id": "C1", "name": "general"}]
    assert handle.invite_url() == "https://discord.example/invite"


def test_a_method_the_channel_lacks_still_reads_as_absent(monkeypatch):
    """Core asks ``getattr(delivery, "request_approval", None)`` whether a channel can prompt: a
    channel that cannot must still be passed over."""

    class Mail:
        async def open_dm(self, user_id: str) -> str:
            return user_id

    channel_delivery.register(Mail(), provider="email")
    monkeypatch.setenv(owner_id_credential("email"), "noor@example.com")
    handle = channel_delivery.delivery_for("email")

    assert getattr(handle, "request_approval", None) is None
    assert not hasattr(handle, "deliver_rich")
    assert channel_delivery.approval_delivery() is None


@pytest.mark.asyncio
async def test_a_method_set_on_the_handle_is_still_called_through_the_mask(telegram):
    """Nothing set on the handle core holds can stand in for the mask."""
    handle = channel_delivery.delivery_for("telegram")
    assert handle is not None
    sent = AsyncMock(return_value="1.0")

    handle.deliver_text = sent  # type: ignore[method-assign]
    await handle.deliver_text("C1", f"key {SECRET}")

    sent.assert_awaited_once_with("C1", f"key {MASK}")


@pytest.mark.asyncio
async def test_registering_the_handle_core_gave_out_does_not_mask_it_twice(telegram):
    handle = channel_delivery.delivery_for("telegram")
    channel_delivery.register(handle, provider="telegram")

    again = channel_delivery.delivery_for("telegram")
    assert again is not None and again.inner is telegram
    await again.deliver_text("C1", f"key {SECRET}")
    assert telegram.calls == [("deliver_text", ("C1", f"key {MASK}"), {})]
