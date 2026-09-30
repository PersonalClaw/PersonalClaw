"""A trigger's run is told what started it: which file arrived, which message came, what was sent.

A file trigger's run got the sentence that created the automation and nothing else ("When a new PDF
lands in ~/Documents/Home/Kitchen, summarise it into …"), so its agent read it as a request to set
the automation up, found it already there, and reported that. Every event kind had the same gap:
an inbox run was not told which message, a web watch's which items, a webhook's what it delivered.

The store dispatches now compose what happened (`triggers.fire_facts.describe`) from the event as
it arrived, before its payload is fenced, and hand it to the action (`ActionContext.fire_facts`);
each action that starts an agent puts it in the agent's task, and the files the fire is about go
with it as files the run may read (`ActionContext.fire_files`).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from personalclaw.action_providers import invoke_agent_provider, run_prompt_provider
from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.invoke_agent_provider import InvokeAgentActionProvider
from personalclaw.action_providers.run_prompt_provider import RunPromptActionProvider
from personalclaw.triggers import fire_facts
from personalclaw.triggers.file_watch import Delta, fire_payload
from personalclaw.triggers.models import Trigger

FACTS = "[WHAT STARTED THIS RUN]\nA file it watches changed.\n[END WHAT STARTED THIS RUN]"
INSTRUCTION = (
    "When a new PDF lands in ~/Documents/Home/Kitchen, summarise it into "
    "~/Notes/Garden/Home/kitchen-reno.md."
)


def _trigger(kind: str, *, provider: str = "run-prompt", config: dict | None = None) -> Trigger:
    return Trigger(
        id=f"{kind}:kitchen-pdf-summarizer",
        name="Kitchen PDF Summarizer",
        kind=kind,
        workflow={"provider": provider, "config": config or {"message": INSTRUCTION}},
    )


def _facts(trigger: Trigger, payload: dict) -> fire_facts.FireFacts:
    return asyncio.run(fire_facts.describe(trigger, payload))


def _fence_holding(text: str, needle: str) -> str:
    """The one untrusted-content span in *text* that holds *needle*."""
    for part in text.split("<untrusted_content")[1:]:
        body = part.split("</untrusted_content>", 1)[0]
        if needle in body:
            return body
    raise AssertionError(f"{needle!r} is not inside any fence:\n{text}")


# ── what each kind of fire says ──────────────────────────────────────────────────────────────


def test_a_file_fire_names_the_file_that_arrived_its_name_and_what_happened_to_it():
    arrived = "/home/user/Documents/Home/Kitchen/revised-quote.pdf"
    facts = _facts(_trigger("file"), fire_payload(Delta(added=[arrived]), trigger_id="file:k"))

    assert facts.text.startswith(fire_facts.FACTS_OPEN)
    assert facts.text.endswith(fire_facts.FACTS_CLOSE)
    assert "Added (a new file):" in facts.text
    fence = _fence_holding(facts.text, arrived)
    assert f"path: {arrived}" in fence and "name: revised-quote.pdf" in fence
    assert "already exists: do what its instruction says" in facts.text
    assert facts.files == (arrived,)


def test_a_changed_and_a_removed_file_are_named_and_only_the_one_still_there_may_be_read():
    kept, gone = "/home/user/notes/a.md", "/home/user/notes/b.md"
    facts = _facts(_trigger("file"), fire_payload(Delta(modified=[kept], removed=[gone])))
    assert "2 files it watches changed:" in facts.text
    assert "Changed:" in facts.text and "Removed:" in facts.text
    assert _fence_holding(facts.text, gone)
    assert facts.files == (kept,)


def test_a_web_watchs_run_is_told_the_page_and_its_new_items():
    payload = {"kind": "web_watch", "url": "https://news.example.com", "new_items": ["Item one"]}
    facts = _facts(_trigger("web_watch"), payload)
    assert _fence_holding(facts.text, "page: https://news.example.com")
    assert _fence_holding(facts.text, "Item one")


def test_a_webhooks_run_is_told_what_it_delivered():
    from personalclaw.security import fence_untrusted

    body = fence_untrusted('{"build": "green"}', source="webhook")
    facts = _facts(_trigger("webhook"), {"trigger_id": "webhook:k", "body": body})
    assert "A webhook delivered this:" in facts.text and body in facts.text


def test_a_schedule_or_a_run_by_hand_adds_nothing():
    assert _facts(_trigger("clock"), {"trigger_id": "clock:k"}) == fire_facts.FireFacts()
    assert _facts(_trigger("file"), {"trigger_id": "file:k", "manual": True}).text == ""


def test_an_inbox_messages_run_is_told_who_wrote_what_it_says_and_what_came_with_it(tmp_path):
    from test_doc_parser import _pdf

    from personalclaw.attachments import Attachment
    from personalclaw.event_triggers import BusEvent
    from personalclaw.event_triggers import fire_payload as event_payload
    from personalclaw.inbox import InboxState, InboxStore
    from personalclaw.inbox_providers.base import IncomingMessage
    from personalclaw.inbox_service import InboxService

    store = InboxStore()
    service = InboxService(state=InboxState(tmp_path / "state.json"), store=store)
    mail = IncomingMessage(
        id="<m@build.example.com>",
        channel_id="owner@example.com",
        channel_name="owner@example.com",
        text="The revised quote is attached.",
        sender_id="builder@build.example.com",
        sender_name="Dana Builder",
        timestamp=1790726807.0,
        files=[Attachment("quote.pdf", "application/pdf", _pdf("Subtotal 31080"))],
    )
    service._ingest([mail], source=SimpleNamespace(source_name="mail"))
    [item_id] = store.items
    event = BusEvent(
        source="inbox",
        event_type="message_received",
        key=item_id,
        value=mail.text,
        now=1790726808.0,
        meta={
            "sender": mail.sender_id,
            "sender_name": mail.sender_name,
            "address": "owner@example.com",
        },
    )
    payload, _ = event_payload("event:contractor-replied", event)
    with patch("personalclaw.inbox_service._live_inbox_service", return_value=service):
        facts = _facts(_trigger("event"), payload)

    assert "A message arrived in the Inbox." in facts.text
    assert _fence_holding(facts.text, "from: Dana Builder <builder@build.example.com>")
    assert _fence_holding(facts.text, "The revised quote is attached.")
    assert "It came with 1 attachment:" in facts.text
    fence = _fence_holding(facts.text, "Subtotal 31080")
    assert "name: quote.pdf" in fence


# ── the actions that start an agent put it in the task ───────────────────────────────────────


def _spawned(monkeypatch, module, provider, config: dict, ctx: ActionContext) -> dict:
    seen: dict = {}

    def _spawn(**kw):
        seen.update(kw)
        return SimpleNamespace(id="c0ffee01", done=False, error="")

    monkeypatch.setattr(
        module,
        "get_action_services",
        lambda: SimpleNamespace(subagents=SimpleNamespace(spawn=_spawn)),
    )
    result = asyncio.run(provider.execute(config, ctx))
    assert result.success, result.error
    return seen


def test_run_prompt_hands_its_agent_the_instruction_then_what_started_it(monkeypatch, tmp_path):
    ctx = ActionContext(
        event="file.changed",
        trigger_id="file:k",
        fire_facts=FACTS,
        fire_files=("/home/user/Documents/Home/Kitchen/revised-quote.pdf",),
    )
    seen = _spawned(
        monkeypatch, run_prompt_provider, RunPromptActionProvider(), {"message": INSTRUCTION}, ctx
    )
    task = seen["task"]
    assert task.index(INSTRUCTION) < task.index("[WHAT STARTED THIS RUN]")
    assert seen["may_read"] == ctx.fire_files


def test_invoke_agent_hands_its_agent_the_task_then_what_started_it(monkeypatch):
    ctx = ActionContext(
        event="file.changed",
        trigger_id="file:k",
        fire_facts=FACTS,
        fire_files=("/home/user/in/a.pdf",),
    )
    seen = _spawned(
        monkeypatch,
        invoke_agent_provider,
        InvokeAgentActionProvider(),
        {"task_template": "Summarise the new file."},
        ctx,
    )
    assert seen["task"].startswith("Summarise the new file.\n\n[WHAT STARTED THIS RUN]")
    assert seen["may_read"] == ("/home/user/in/a.pdf",)


# ── end to end: the gateway's file fire, into a real subagent manager ────────────────────────


@pytest.mark.asyncio
async def test_a_file_fire_starts_an_agent_told_the_file_and_able_to_read_it(tmp_path, monkeypatch):
    from test_one_allow_covers_a_triggers_agent import _manager

    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.triggers import grants
    from personalclaw.triggers.store import TriggerStore

    arrived = tmp_path / "Kitchen" / "revised-quote.pdf"
    arrived.parent.mkdir()
    arrived.write_bytes(b"%PDF-1.4\n")
    trigger = _trigger("file")
    trigger.spec = {"paths": [str(arrived.parent / "**")]}
    grants.give(trigger)
    TriggerStore().upsert(trigger)

    manager = _manager(AsyncMock(return_value=True))
    monkeypatch.setattr(
        run_prompt_provider, "get_action_services", lambda: SimpleNamespace(subagents=manager)
    )
    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = SimpleNamespace(
        notify=lambda *a, **k: None, push_refresh=lambda *a: None, broadcast_ws=lambda *a, **k: None
    )
    with patch("personalclaw.subagent.SubagentManager._run", new=AsyncMock()):
        await orch._fire_store_trigger(
            trigger, fire_payload(Delta(added=[str(arrived)]), trigger_id=trigger.id)
        )
        [info] = list(manager._agents.values())
        await asyncio.wait_for(manager._tasks[info.id], timeout=10)

    assert INSTRUCTION in info._raw_task
    fence = _fence_holding(info._raw_task, str(arrived))
    assert "name: revised-quote.pdf" in fence
    assert info.may_read == (str(arrived),)
