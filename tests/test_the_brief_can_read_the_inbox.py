"""An agent can read what is waiting in the Inbox, and only read it.

The "Morning briefing" preset asks its agent for "what is waiting in my inbox", and the brief it
starts runs read-only (an automation's agent is granted reads). No agent tool read the Inbox: the
whole inbox group was ``post_to_inbox``, a write the read grant refuses. So the brief had nothing
to call, said nothing about an Inbox holding an open "Needs you" item, and an earlier run of it
said "No messages in the inbox (currently empty or not accessible via local files)".

``inbox_list`` lists the open items (pending or seen), newest first: what each is, who or what
raised it, when, and its text. It declares itself a read, so the read grant admits it; it changes
nothing, so reading an item does not mark it seen; and each item's text reaches the model fenced
as untrusted data, since much of the Inbox is other people's words.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import personalclaw.agents.native.builtin_tools as BT
import personalclaw.inbox_providers.native_source as ns
from personalclaw.guardrails.policy import TOOL_READ, SafetyProfile, declared_tool_grant_denial
from personalclaw.inbox import InboxItem, InboxState, InboxStore, ItemKind, ItemStatus
from personalclaw.tool_providers.base import RiskLevel


def _run(coro):
    return asyncio.run(coro)


def _item(item_id: str, message: str, *, created_at: float, **over) -> InboxItem:
    fields = dict(
        id=item_id,
        channel="C1",
        channel_name="family",
        thread_ts=None,
        message=message,
        sender_id="u1",
        sender_name="Dana",
        status=ItemStatus.PENDING.value,
        created_at=created_at,
    )
    fields.update(over)
    return InboxItem(**fields)


@pytest.fixture
def inbox(tmp_path, monkeypatch) -> InboxStore:
    store = InboxStore(path=tmp_path / "inbox.json")
    store.load()
    for item in (
        _item("m_old", "Can you sign the field trip form?", created_at=100.0),
        _item(
            "needs_input_e0",
            "Loop stopped at its budget. It used its budget of 1 cycle.",
            created_at=300.0,
            channel_name="loop",
            sender_name="loop",
            item_kind=ItemKind.NEEDS_INPUT.value,
            status=ItemStatus.SEEN.value,
        ),
        _item("m_mid", "Dentist moved to Oct 13 at 4pm", created_at=200.0),
        _item("m_done", "Thanks, all sorted", created_at=400.0, status=ItemStatus.HANDLED.value),
        _item("m_gone", "Newsletter", created_at=500.0, status=ItemStatus.DISMISSED.value),
    ):
        store.add(item)
    store.flush()
    st = MagicMock()
    st._inbox_svc = None
    st._inbox_store = store
    st._inbox_state = InboxState(path=tmp_path / "inbox_state.json")
    monkeypatch.setattr(ns, "_dashboard_state", st)
    return store


def _tool(provider):
    return next(t for t in _run(provider.list_tools()) if t.name == "inbox_list")


def test_the_inbox_tools_include_a_read() -> None:
    provider = BT.create_inbox_tools_provider()
    names = {t.name for t in _run(provider.list_tools())}
    assert names == {"post_to_inbox", "inbox_list"}
    tool = _tool(provider)
    assert tool.risk_level is RiskLevel.SAFE
    assert tool.requires_approval is False


def test_a_read_only_run_may_call_it_and_still_may_not_post() -> None:
    research = SafetyProfile(name="spawn_research", tool_grants=TOOL_READ)
    tools = {t.name: t for t in _run(BT.create_inbox_tools_provider().list_tools())}
    assert declared_tool_grant_denial(research, "inbox_list", tools["inbox_list"].risk_level) == ""
    # The control: the same grant refuses the inbox's write, so the line above is not vacuous.
    assert declared_tool_grant_denial(research, "post_to_inbox", tools["post_to_inbox"].risk_level)


def test_it_lists_what_is_open_newest_first(inbox: InboxStore) -> None:
    result = _run(BT.create_inbox_tools_provider().invoke("inbox_list", {}))
    assert result.success, result.error
    out = result.output
    assert "3 open items in the Inbox" in out
    order = [
        out.index(text) for text in ("Loop stopped at its budget", "Dentist moved", "field trip")
    ]
    assert order == sorted(order), out
    # Answered and dismissed rows are not waiting.
    assert "Thanks, all sorted" not in out and "Newsletter" not in out
    # What each item is and who raised it.
    assert "needs input" in out and "From: loop" in out and "From: Dana in family" in out
    assert "needs_input_e0" in out


def test_each_items_text_is_fenced_as_untrusted(inbox: InboxStore) -> None:
    out = _run(BT.create_inbox_tools_provider().invoke("inbox_list", {})).output
    fences = re.findall(r"<untrusted_content[^>]*>(.*?)</untrusted_content>", out, re.DOTALL)
    assert len(fences) == 3
    assert any("Dentist moved to Oct 13 at 4pm" in body for body in fences)


def test_reading_changes_nothing(inbox: InboxStore, tmp_path: Path) -> None:
    before = (tmp_path / "inbox.json").read_bytes()
    statuses = {i.id: i.status for i in inbox.items.values()}
    _run(BT.create_inbox_tools_provider().invoke("inbox_list", {}))
    assert {i.id: i.status for i in inbox.items.values()} == statuses
    assert (tmp_path / "inbox.json").read_bytes() == before


def test_kind_and_limit_narrow_it(inbox: InboxStore) -> None:
    provider = BT.create_inbox_tools_provider()
    only = _run(provider.invoke("inbox_list", {"kind": "needs_input"})).output
    assert "1 open item in the Inbox" in only and "Dentist" not in only
    newest = _run(provider.invoke("inbox_list", {"limit": 1})).output
    assert "Loop stopped at its budget" in newest and "Dentist" not in newest
    assert "1 of 3 open items" in newest


def test_an_empty_inbox_says_so(tmp_path, monkeypatch) -> None:
    store = InboxStore(path=tmp_path / "inbox.json")
    store.load()
    st = MagicMock()
    st._inbox_svc = None
    st._inbox_store = store
    monkeypatch.setattr(ns, "_dashboard_state", st)
    result = _run(BT.create_inbox_tools_provider().invoke("inbox_list", {}))
    assert result.success and result.output == "Nothing is waiting in the Inbox."


def test_an_unreachable_inbox_is_a_failure_not_an_empty_one(monkeypatch) -> None:
    monkeypatch.setattr(ns, "_dashboard_state", None)
    result = _run(BT.create_inbox_tools_provider().invoke("inbox_list", {}))
    assert not result.success
    assert "Nothing is waiting" not in (result.output or "")


def test_the_morning_briefing_preset_asks_for_what_a_tool_can_read() -> None:
    """The preset's words name the Inbox, and the Inbox now has a read a brief may call."""
    presets = (
        Path(__file__).resolve().parents[1]
        / "web"
        / "src"
        / "pages"
        / "triggers"
        / "triggerPresets.ts"
    ).read_text(encoding="utf-8")
    briefing = presets[presets.index("id: 'morning-briefing'") :][:800]
    assert "inbox" in briefing.lower()
    assert "inbox_list" in {t.name for t in _run(BT.create_inbox_tools_provider().list_tools())}
