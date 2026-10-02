"""A channel's approval prompt shows what will run, as the dashboard's card shows it.

The dashboard's approval card shows the tool, its arguments, the runner's purpose, the risk and
what the call can touch. A channel was handed the tool's name and the raw event: Telegram and
Discord printed the name alone ("Approve: bash?"), so people approved a command they could not
see, and each channel that did show more masked the arguments with its own copy of the passes.

The brief core hands every channel now carries the call: ``tool``, ``input`` and ``purpose``,
masked exactly as the dashboard's pending approval masks them, the ``summary`` line, and the
``reach`` line for a command whose host is on no allowed list. Both
askers stamp it (the gateway's, and the registry's Channel DM target), and
``personalclaw.sdk.channel.approval_brief_for`` is the one read a channel makes, composing a brief
for an approval its own turn raised.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import channel_delivery
from personalclaw import notification_kinds as nk
from personalclaw import notification_rules as rules
from personalclaw.approval_answer import YOU
from personalclaw.approval_brief import APPROVAL_BRIEF_META_KEY
from personalclaw.config.credentials import owner_id_credential, save_credential
from personalclaw.config.loader import CRED_OWNER_ID, AppConfig
from personalclaw.llm_helpers import LLMEvent
from personalclaw.security import redact_field
from personalclaw.task_modes import tool_input_to_str

PROVIDER = "achat"
#: A key the credential mask knows; it must never reach a channel.
SECRET = "sk-ant-api03-" + "Q" * 40
#: The arguments of a native-loop call: a dict, as the native runtime hands it over.
CALL = {
    "command": f"curl -H 'x-api-key: {SECRET}' https://api.example.test/v1/models",
    "timeout": 30,
}
PURPOSE = f"check which models the key {SECRET} can reach"


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
    """A channel's outbound half: the events it was asked to prompt with."""

    def __init__(self) -> None:
        self.events: list[Any] = []
        self.prompts: list[Any] = []

    async def open_dm(self, user_id: str) -> str:
        return f"dm-{user_id}"

    async def deliver_text(self, channel: str, text: str, thread_ts: str = "", **_kw) -> str:
        return "1"

    async def request_approval(
        self, event, *, source, parent_session_key="", sessions=None, on_prompted=None
    ):  # noqa: E301 - the protocol's shape
        self.events.append(event)
        pending = SimpleNamespace(future=asyncio.get_running_loop().create_future())
        self.prompts.append(pending)
        if on_prompted:
            on_prompted(pending)
        return (await pending.future) == "approved"


def _connect() -> _Channel:
    handle = _Channel()
    channel_delivery.register(handle, provider=PROVIDER)
    save_credential(owner_id_credential(PROVIDER), "owner-1")
    return handle


def _state(tmp_path):
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
    for _ in range(200):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never: {what}")


def _brief_of(event: Any) -> dict:
    brief = event.tool_meta.get(APPROVAL_BRIEF_META_KEY)
    assert isinstance(brief, dict), f"the channel was handed no brief: {event.tool_meta!r}"
    return brief


# ── the registry's Channel DM target ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_channel_is_handed_the_call_the_dashboard_card_shows(tmp_path):
    """The channel's brief holds the entry's own strings: the ones the dashboard's card shows."""
    channel = _connect()
    state = _state(tmp_path)

    waiter = asyncio.ensure_future(
        state.request_approval(
            "ap-1", "cron:nightly", "execute_bash", tool_input=CALL, tool_purpose=PURPOSE
        )
    )
    await _until(lambda: channel.events, "the channel was asked")
    entry = state._pending_approvals["ap-1"]
    brief = _brief_of(channel.events[0])

    assert brief["tool"] == entry["tool"] == "execute_bash"
    assert brief["input"] == entry["tool_input"], "the channel shows what the card shows"
    assert "curl -H" in brief["input"] and "https://api.example.test/v1/models" in brief["input"]
    assert brief["purpose"] == entry["tool_purpose"]
    assert brief["purpose"].startswith("check which models the key")
    # The command decides the risk the entry carries, and the channel says what the card shows:
    # it reaches the network, and the screen cannot vouch for it.
    assert entry["risk"] == "unchecked"
    assert entry["blast_radius"] == {
        "writes": False,
        "network": True,
        "shell": True,
        "saysReadOnly": False,
        "readOnly": False,
    }
    assert brief["summary"] == "Can: runs a command, uses the network · Risk: Not checked"
    # Its host is on no allowed list, which the card says on a line of its own under its chips,
    # and so does the channel, in the card's words.
    assert brief["reach"] == entry["reach"]
    assert brief["reach"].startswith("It reaches api.example.test, which is not on Allowed hosts")
    for text in (brief["tool"], brief["input"], brief["purpose"], brief["summary"], brief["reach"]):
        assert SECRET not in text, "a key reached the channel"

    state.resolve_approval("ap-1", False, by=YOU)
    await asyncio.wait_for(waiter, timeout=5)


@pytest.mark.asyncio
async def test_a_background_shell_write_is_shown_as_the_write_it_is(tmp_path):
    """A shell call that declares nothing still has its command read: the entry carries the
    command's risk and radius, so the card and the channel both say it writes a file."""
    channel = _connect()
    state = _state(tmp_path)
    write = {"command": "cd ~ && printf 'hello\\n' > Documents/note.txt"}

    waiter = asyncio.ensure_future(
        state.request_approval("ap-2", "cron:nightly", "execute_bash", tool_input=write)
    )
    await _until(lambda: channel.events, "the channel was asked")
    entry = state._pending_approvals["ap-2"]
    assert entry["risk"] == "caution"
    assert entry["is_read_only"] is False
    assert entry["blast_radius"]["writes"] is True
    assert _brief_of(channel.events[0])["summary"] == "Can: writes files · Risk: Caution"

    state.resolve_approval("ap-2", False, by=YOU)
    await asyncio.wait_for(waiter, timeout=5)


@pytest.mark.asyncio
async def test_a_background_call_that_declares_nothing_and_runs_no_command_names_no_risk(
    tmp_path,
):
    """Nothing declared and no command read: no risk anybody established, so the entry says
    none rather than one minted from the tool's name."""
    _connect()
    state = _state(tmp_path)
    waiter = asyncio.ensure_future(
        state.request_approval("ap-3", "cron:nightly", "frobnicate_xyzzy", tool_input={"x": 1})
    )
    await _until(lambda: "ap-3" in state._pending_approvals, "the approval was registered")
    assert state._pending_approvals["ap-3"]["risk"] == ""
    state.resolve_approval("ap-3", False, by=YOU)
    await asyncio.wait_for(waiter, timeout=5)


# ── the gateway's subagent approval ────────────────────────────────────────────────────────


def _orchestrator():
    from personalclaw.gateway import GatewayOrchestrator

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
    return orch


@pytest.mark.asyncio
async def test_a_subagent_approval_hands_the_channel_its_masked_arguments(tmp_path):
    handle = MagicMock()
    handle.request_approval = AsyncMock(return_value=True)
    channel_delivery.register(handle, provider=PROVIDER)
    save_credential(owner_id_credential(PROVIDER), "owner-1")
    event = LLMEvent(
        kind="permission_request",
        request_id="req-1",
        title="execute_bash",
        tool_kind="execute",
        risk_level="destructive",
        tool_input=dict(CALL),
        tool_purpose=PURPOSE,
    )

    with patch("personalclaw.trust_mode.is_yolo_active", return_value=False):
        approve = _orchestrator()._interactive_approval("subagent")
        assert bool(await approve(event, "")) is True

    brief = _brief_of(handle.request_approval.call_args.args[0])
    # The dashboard's entry for the same call holds redact_field(tool_input_to_str(raw)).
    assert brief["input"] == redact_field(tool_input_to_str(CALL))
    assert '"command": "curl -H' in brief["input"] and '"timeout": 30' in brief["input"]
    assert brief["purpose"] == redact_field(PURPOSE)
    assert brief["summary"] == "Can: runs a command · Risk: Destructive"
    for text in (brief["input"], brief["purpose"]):
        assert SECRET not in text, "a key reached the channel"
    assert event.tool_input == CALL, "the call itself is untouched"


# ── the one read a channel makes ───────────────────────────────────────────────────────────


def test_a_channel_reads_the_brief_core_stamped():
    from personalclaw.channel_delivery import ONE_CALL_ANSWERS
    from personalclaw.sdk.channel import approval_brief_for

    answers = [a.as_dict() for a in ONE_CALL_ANSWERS]
    stamped = {
        "tool": "t",
        "input": "i",
        "purpose": "p",
        "risk": "safe",
        "summary": "s",
        "answers": answers,
    }
    event = SimpleNamespace(title="other", tool_meta={APPROVAL_BRIEF_META_KEY: stamped})
    assert approval_brief_for(event) is stamped

    # A stamped brief that offers nothing a prompt can press is composed again from the event.
    for offers in (None, [], [{"key": "approved"}]):
        partial = {**stamped, "answers": offers}
        event = SimpleNamespace(title="other", tool_meta={APPROVAL_BRIEF_META_KEY: partial})
        assert approval_brief_for(event)["answers"] == answers


def test_a_channels_own_turn_gets_a_brief_composed_and_masked():
    """Slack runs its own turns, so their approvals reach it with no brief on them."""
    from personalclaw.sdk.channel import approval_brief_for

    event = LLMEvent(
        kind="permission_request",
        request_id="r",
        title="web_fetch",
        tool_input=json.dumps({"url": f"https://example.test/?key={SECRET}"}),
        tool_purpose="read the page",
    )
    brief = approval_brief_for(event)
    assert brief is not None
    assert brief["tool"] == "web_fetch"
    assert brief["input"] == redact_field(event.tool_input)
    assert SECRET not in brief["input"]
    assert brief["purpose"] == "read the page"
    assert brief["summary"].startswith("Can: uses the network")
    assert event.tool_meta == {}, "reading composes; it stamps nothing"


def test_a_stamped_brief_missing_a_part_is_composed_again_from_the_event():
    """A brief that could not show the whole call is not the one a prompt is made from."""
    from personalclaw.sdk.channel import approval_brief_for

    event = LLMEvent(
        kind="permission_request",
        request_id="r",
        title="write_file",
        tool_input={"path": "/tmp/notes.md"},
        tool_meta={APPROVAL_BRIEF_META_KEY: {"tool": "write_file", "risk": "caution"}},
    )
    brief = approval_brief_for(event)
    assert brief is not None and brief["input"] == '{"path": "/tmp/notes.md"}'


def test_an_event_with_no_tool_has_no_brief():
    from personalclaw.sdk.channel import approval_brief_for

    assert approval_brief_for(SimpleNamespace(title="", tool_meta={})) is None


# ── the summary line ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("tool", "risk", "line"),
    [
        ("write_file", "caution", "Can: writes files · Risk: Caution"),
        ("bash", "safe", "Can: runs a command · Reads only · Risk: Safe"),
        ("memory_search", "safe", "Reads only · Risk: Safe"),
        ("frobnicate_xyzzy", "caution", "Risk: Caution"),
        ("frobnicate_xyzzy", "", ""),
        ("frobnicate_xyzzy", "unheard-of", ""),
    ],
)
def test_the_summary_says_what_is_established_and_the_risk(tool, risk, line):
    from personalclaw.approval_brief import derive_blast_radius, summary_line

    assert summary_line(derive_blast_radius(tool, risk=risk), risk) == line


def test_a_false_facet_never_becomes_an_all_clear():
    """Only ESTABLISHED facets are named; a ``False`` is never rendered as a negative."""
    from personalclaw.approval_brief import summary_line

    radius = {
        "writes": False,
        "network": True,
        "shell": False,
        "saysReadOnly": False,
        "readOnly": False,
    }
    line = summary_line(radius, "caution")
    assert line == "Can: uses the network · Risk: Caution"
    for absent in ("writes files", "runs a command", "reads only", "no network", "no shell"):
        assert absent not in line.lower()


def test_a_facet_added_later_reads_as_a_consequence_never_a_reassurance():
    """The frame excludes the one read claim rather than listing the consequences."""
    from personalclaw.approval_brief import FACET_COPY, summary_line

    copy = dict(FACET_COPY)
    FACET_COPY["sendsOnYourBehalf"] = {"label": "Sends on your behalf", "detail": ""}
    try:
        from personalclaw import approval_brief as mod

        order = mod.BLAST_RADIUS_FACET_ORDER
        mod.BLAST_RADIUS_FACET_ORDER = (*order, "sendsOnYourBehalf")
        try:
            line = summary_line({"sendsOnYourBehalf": True}, "destructive")
        finally:
            mod.BLAST_RADIUS_FACET_ORDER = order
    finally:
        FACET_COPY.clear()
        FACET_COPY.update(copy)
    assert line == "Can: sends on your behalf · Risk: Destructive"


def test_the_risk_words_are_the_dashboard_cards():
    """``RISK_META`` in the card's source, parsed: a channel's "Risk: Caution" is its chip."""
    from personalclaw.approval_brief import RISK_LABELS

    source = (
        Path(__file__).resolve().parents[1] / "web/src/pages/chat/ApprovalCard.tsx"
    ).read_text(encoding="utf-8")
    block = re.search(r"const RISK_META = \{(.*?)\} as const", source, re.S)
    assert block, "the card's RISK_META moved"
    card = dict(re.findall(r"(\w+): \{ label: '([^']+)'", block.group(1)))
    assert card == RISK_LABELS
