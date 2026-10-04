"""Text from outside reaches a model only after the injection screen has read it, fenced as data
with its source, whatever door it comes through.

Each door here hands a model words nobody vetted: what a workflow run said it produced to the
automation that runs after it, an Inbox message's sender to the automation it fires, a watched
page's items, a pasted prompt card, the context an agent saved for a callback, a scheduled run's
result opened as a chat, a group channel's recent messages, the post that started a thread, and a
line someone else sent in a conversation's history. Every one now goes through one door
(``outside_text.admit``), and each test below drives a door the way the product does:

* the ordinary texts of the injection screen's labelled table
  (``tests/test_the_injection_screen_passes_ordinary_text_and_refuses_a_take_over.py``: code,
  tables, pipelines, logs, release notes, a task assistant's replies) all pass, and each arrives
  inside a fence that names where it came from;
* a text the screen refuses, from the same table, never reaches the model, and what stands in
  for it names the pattern class and never the words.

Measured on the integration branch before this change: what a workflow run said it produced
reached the next automation's agent outside any fence; an Inbox sender's name went unscreened, so
a fire ran on one the screen refuses; a watched page's item, an event's value and a pasted card
that quote the fence's own marker reached the model with their words outside any fence; a
callback's saved context, a scheduled run's result, a channel's history and a thread's first post
reached a model unscreened and unfenced; and another person's line in a history was fenced but
never screened.
"""

from __future__ import annotations

import asyncio
import json
import types
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from test_the_injection_screen_passes_ordinary_text_and_refuses_a_take_over import (
    ORDINARY,
    REFUSED,
    TAKE_OVER,
)

from personalclaw.action_providers.base import ActionContext
from personalclaw.security import fence_untrusted, outside_fences
from personalclaw.triggers import grants
from personalclaw.triggers.models import Trigger

#: The take-overs the screen refuses outright, from its own table.
REFUSED_TEXTS = {name: text for name, (tier, text) in TAKE_OVER.items() if tier == REFUSED}
_ORDERS = TAKE_OVER["tells the model to ignore what it was told"][1]

#: A page that documents the fence, and so quotes its marker.
_QUOTES_THE_MARKER = "Text from outside reaches the model wrapped in <untrusted_content> markers."


def _spans(text: str) -> list[tuple[str, str]]:
    """Every fence in *text*: its open marker's attributes and the words inside it."""
    out = []
    for part in text.split("<untrusted_content")[1:]:
        attrs, _, rest = part.partition(">")
        out.append((attrs, rest.split("</untrusted_content>", 1)[0]))
    return out


def assert_fenced(handed: str, text: str, *, source: str, what: str = "") -> None:
    """*text* reached the model inside a fence that names *source*, and none of it outside one."""
    assert any(
        f"source={source}" in attrs and text in body for attrs, body in _spans(handed)
    ), f"{what}: not inside a fence from {source}:\n{handed}"
    for line in text.splitlines():
        if len(line.strip()) > 3:
            assert line not in outside_fences(handed), f"{what}: {line!r} outside a fence"


def assert_withheld(handed: str, text: str, *, what: str = "") -> None:
    """None of *text* reached the model, and what stands in for it says the screen refused it."""
    for line in text.splitlines():
        if len(line.strip()) > 3:
            assert line not in handed, f"{what}: {line!r} reached the model"
    assert "the injection screen refused it" in handed, f"{what}: nothing says why:\n{handed}"


# ── a stored trigger's fire: what started the run ─────────────────────────────────────────────


class _Action:
    """A trigger's action that records what it is handed."""

    def __init__(self) -> None:
        self.handed: list[ActionContext] = []

    async def execute(self, config, ctx, timeout=30):
        self.handed.append(ctx)
        return types.SimpleNamespace(success=True, summary="done", stdout="", outcome="")


@pytest.fixture
def fire(monkeypatch):
    """Fire a trigger through the dispatch every fire runs through, with an action that records
    what it is handed; what it was handed, or None when its action did not run."""
    import personalclaw.action_providers as AP
    from personalclaw.gateway import GatewayOrchestrator

    async def _fire(trigger: Trigger, payload: dict[str, Any]) -> ActionContext | None:
        action = _Action()
        monkeypatch.setattr(AP, "get_action_provider", lambda name: action)
        await object.__new__(GatewayOrchestrator)._fire_store_trigger(trigger, payload)
        return action.handed[0] if action.handed else None

    return _fire


def _trigger(kind: str) -> Trigger:
    trigger = Trigger(
        id=f"{kind}:door",
        name="Tell me what came in",
        kind=kind,
        workflow={"inline": {"provider": "notify", "config": {"title_template": "New"}}},
    )
    grants.give(trigger)
    return trigger


def _after_a_run(trigger: Trigger, summary: str) -> dict[str, Any]:
    return {
        "trigger_id": trigger.id,
        "source_run_id": "run-7",
        "source_workflow": "weekly-digest",
        "run_status": "completed",
        "summary": summary,
    }


def _blocked_rows(trigger: Trigger) -> list[dict]:
    from personalclaw.config.loader import config_dir
    from personalclaw.schedule_history import ScheduleRunStore

    rows, _ = asyncio.run(ScheduleRunStore(config_dir()).list_for_job(trigger.id, 0, 10))
    return [row for row in rows if row.get("status") == "blocked_injection"]


@pytest.mark.asyncio
async def test_what_a_workflow_run_produced_reaches_the_next_automation_fenced(fire):
    """🔴 Red on integration: the run's own words went into the next agent's task unfenced."""
    trigger = _trigger("run_completed")
    for name, text in ORDINARY.items():
        handed = await fire(trigger, _after_a_run(trigger, text))
        assert handed is not None, f"{name}: an ordinary result did not run the next automation"
        assert_fenced(handed.fire_facts, text, source=f"trigger:{trigger.id}", what=name)


def test_a_workflow_runs_result_the_screen_refuses_runs_nothing_after_it(fire):
    trigger = _trigger("run_completed")
    for name, text in REFUSED_TEXTS.items():
        assert asyncio.run(fire(trigger, _after_a_run(trigger, text))) is None, name
    assert len(_blocked_rows(trigger)) == len(REFUSED_TEXTS)


def _inbox_message(trigger: Trigger, sender_name: str) -> dict[str, Any]:
    return {
        "trigger_id": trigger.id,
        "source": "inbox",
        "event_type": "message_received",
        "key": "msg-41",
        "value": fence_untrusted("See you at noon.", source="inbox-message"),
        "meta": {"sender": "U0FRIEND", "sender_name": sender_name, "address": "C0TEAM"},
    }


@pytest.mark.asyncio
async def test_an_inbox_message_s_sender_reaches_its_automation_screened_and_fenced(fire):
    """🔴 Red on integration: the sender's name was fenced and never screened, so a fire whose
    sender the screen refuses ran its action."""
    trigger = _trigger("event")
    handed = await fire(trigger, _inbox_message(trigger, "Rosa Delgado"))
    assert handed is not None
    assert_fenced(
        handed.fire_facts, "from: Rosa Delgado <U0FRIEND>", source=f"trigger:{trigger.id}"
    )
    for name, text in REFUSED_TEXTS.items():
        assert await fire(trigger, _inbox_message(trigger, text)) is None, name


@pytest.mark.asyncio
async def test_a_watched_page_s_item_reaches_the_run_fenced_even_when_it_quotes_the_marker(fire):
    """🔴 Red on integration: an item that merely held the fence's marker was taken for one fenced
    already, and its words reached the run's agent outside any fence."""
    trigger = _trigger("web_watch")
    for name, text in {**ORDINARY, "a page about the fence": _QUOTES_THE_MARKER}.items():
        payload = {"trigger_id": trigger.id, "url": "https://example.com/feed", "new_items": [text]}
        handed = await fire(trigger, payload)
        assert handed is not None, name
        for words in text.split("<untrusted_content>"):
            if words.strip():
                assert_fenced(handed.fire_facts, words, source=f"trigger:{trigger.id}", what=name)


# ── an event's value, fenced where the event is matched ───────────────────────────────────────


def test_an_event_s_value_that_quotes_the_marker_is_fenced_where_it_arrives():
    """🔴 Red on integration: a value holding the marker was kept as though fenced, so its words
    went on raw, with a stray close marker after them."""
    from personalclaw.event_triggers import BusEvent, fire_payload
    from personalclaw.outside_text import is_whole_fence

    event = BusEvent(
        source="memory", event_type="create", key="notes.fence", value=_QUOTES_THE_MARKER, now=1.0
    )
    payload, context = fire_payload("event:t", event)
    assert is_whole_fence(payload["value"]) and "&lt;untrusted_content&gt;" in payload["value"]
    assert is_whole_fence(context) and "notes.fence: Text from outside" in context


def test_a_value_of_several_fences_is_cut_with_its_last_fence_closed():
    """🔴 Red on integration: the cut kept the first fence's close, so the second was left open
    and everything after it would read as fenced."""
    from personalclaw.event_triggers import BusEvent, fire_payload
    from personalclaw.outside_text import ends_inside_a_fence

    value = (
        fence_untrusted("Bulbs", source="shop") + "\n" + fence_untrusted("x" * 400, source="shop")
    )
    event = BusEvent(source="app", event_type="app:shop:new", key="k", value=value, now=1.0)
    _payload, context = fire_payload("event:t", event)
    assert not ends_inside_a_fence(context), context


# ── a pasted prompt card ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def converter(monkeypatch):
    """The model that converts a card: records the prompt it is handed."""
    seen: list[str] = []

    async def _model(prompt, **_kwargs):
        seen.append(prompt)
        return json.dumps({"target": ""})

    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", _model)
    return seen


def test_an_ordinary_prompt_card_reaches_the_converter_fenced(converter):
    """🔴 Red on integration for a card that quotes the fence's marker: it was taken for one
    fenced already, and reached the converter outside any fence."""
    from personalclaw.packs import prompt_cards

    for name, text in {**ORDINARY, "a page about the fence": _QUOTES_THE_MARKER}.items():
        asyncio.run(prompt_cards.convert_card(text))
        for words in text.split("<untrusted_content>"):
            if words.strip():
                assert_fenced(converter[-1], words, source="pasted prompt card", what=name)


def test_a_prompt_card_the_screen_refuses_never_reaches_the_converter(converter):
    """🔴 Red on integration: the card was fenced and handed to the model whatever it held."""
    from personalclaw.packs import prompt_cards

    for name, text in REFUSED_TEXTS.items():
        with pytest.raises(prompt_cards.PromptCardError) as refused:
            asyncio.run(prompt_cards.convert_card(text))
        assert "the injection screen refused the card" in str(refused.value), name
        assert text not in str(refused.value)
    assert converter == []


# ── the context an agent saved for a callback ─────────────────────────────────────────────────


def _call_back(session_key: str, monkeypatch) -> tuple[web.Response, list[tuple]]:
    """An outside program on this machine calling a callback back with the webhook token; the
    answer, and the turns it started."""
    from personalclaw.dashboard.handlers import hooks as hooks_mod
    from personalclaw.inbound import caps

    caps.reset_for_tests()
    turns: list[tuple] = []

    async def _turn(*args):
        turns.append(args)
        hooks_mod._hook_semaphore.release()

    monkeypatch.setattr(hooks_mod, "_run_hook_agent", _turn)
    monkeypatch.setattr(hooks_mod, "_hook_token_refusal", lambda _request: "")
    app = web.Application()
    app["state"] = types.SimpleNamespace(_background_tasks=set())
    req = make_mocked_request("POST", "/api/hooks/agent", app=app).clone(remote="127.0.0.1")

    async def _json():
        return {"message": "CI passed", "sessionKey": session_key, "deliver": False}

    req.json = _json  # type: ignore[assignment]

    async def _go():
        resp = await hooks_mod.api_hooks_agent(req)
        for task in list(app["state"]._background_tasks):
            await task
        return resp

    return asyncio.run(_go()), turns


def test_a_callback_s_saved_context_reaches_its_turn_fenced(monkeypatch):
    """🔴 Red on integration: the context was masked, and handed to the turn unfenced."""
    from personalclaw import webhook_callbacks

    for name, text in ORDINARY.items():
        callback = webhook_callbacks.register("deploy:prod", text)
        assert_fenced(
            webhook_callbacks.restored_context(callback).text,
            text,
            source="callback:deploy:prod",
            what=name,
        )
    webhook_callbacks.allow(callback)
    answer, ((*_args, restored),) = _call_back("hook:deploy:prod", monkeypatch)
    assert answer.status == 200
    assert_fenced(restored, text, source="callback:deploy:prod")


def test_a_callback_whose_saved_context_the_screen_refuses_starts_no_turn(monkeypatch):
    """🔴 Red on integration: the turn started from the saved context whatever it held."""
    from personalclaw import webhook_callbacks

    callback = webhook_callbacks.register("deploy:prod", f"Merge when green. {_ORDERS}")
    webhook_callbacks.allow(callback)
    answer, turns = _call_back("hook:deploy:prod", monkeypatch)
    assert answer.status == 409 and turns == []
    error = json.loads(answer.body)["error"]
    assert error["code"] == "callback_context_refused"
    assert "override" in error["message"] and _ORDERS not in error["message"]


# ── a scheduled run's result, opened as a chat ────────────────────────────────────────────────


def _opened_as_a_chat(result: str) -> str:
    """Open a schedule's last result as a chat, then hand a fresh runtime that chat's history;
    the history line the result became."""
    from unittest.mock import MagicMock

    from personalclaw.dashboard.chat_persistence import prior_turns_transcript
    from personalclaw.dashboard.schedule_inject import inject_schedule_result_to_session
    from personalclaw.dashboard.state import DashboardState, _ChatSession
    from personalclaw.schedule import ScheduleJob, make_agent_action
    from personalclaw.turn_source import turn_line

    state = DashboardState(sessions=MagicMock(count=0), start_time=0.0)
    session = _ChatSession(key="cron-nightly")
    state.get_or_create_session = lambda name=None, agent="", **kw: session  # type: ignore
    state.push_sessions_update = MagicMock()  # type: ignore[method-assign]
    state.conversation_log = None
    job = ScheduleJob(id="nightly", name="Nightly", action=make_agent_action(message="Sum up."))
    inject_schedule_result_to_session(state, job, result, history=[])
    ((line, row),) = [(turn_line(m), m) for m in prior_turns_transcript(session, "")]
    assert row["role"] == "assistant"
    return line


def test_a_scheduled_run_s_result_reaches_the_chat_s_model_fenced():
    """🔴 Red on integration: it was handed back as the assistant's own words, unfenced."""
    for name, text in ORDINARY.items():
        assert_fenced(_opened_as_a_chat(text), text, source="trigger:nightly", what=name)


def test_a_scheduled_run_s_result_the_screen_refuses_never_reaches_the_chat_s_model():
    """🔴 Red on integration: the result went into the history whatever it held."""
    for name, text in REFUSED_TEXTS.items():
        assert_withheld(_opened_as_a_chat(text), text, what=name)


# ── a group channel's recent messages, and the post that started a thread ─────────────────────


def _channel(*lines: tuple[str, str], thread: str | None = None) -> str:
    from personalclaw.channel_history import ChannelHistory

    history = ChannelHistory()
    history.set_user_name("U0ALICE", "Alice")
    history.set_user_name("U0BOB", "Bob")
    for user, text in lines:
        history.push("C0TEAM", user, text, thread_ts=thread)
    return history.context_for("C0TEAM", thread_ts=thread)


@pytest.mark.parametrize("thread", [None, "1700000000.1"])
def test_each_participant_s_messages_reach_the_model_fenced_with_their_source(thread):
    """🔴 Red on integration: every message went into the prompt as a plain line."""
    for name, text in ORDINARY.items():
        said = _channel(("U0ALICE", "Is the deploy done?"), ("U0BOB", text), thread=thread)
        assert_fenced(said, "Alice", source="channel:C0TEAM:U0ALICE", what=name)
        assert_fenced(said, text, source="channel:C0TEAM:U0BOB", what=name)
        assert "Bob (" in said and "Is the deploy done?" in said


def test_a_participant_s_message_the_screen_refuses_never_reaches_the_model():
    """🔴 Red on integration: every message went into the prompt whatever it held."""
    for name, text in REFUSED_TEXTS.items():
        said = _channel(("U0ALICE", "Is the deploy done?"), ("U0BOB", text))
        assert_withheld(said, text, what=name)
        assert "U0BOB" in said and "Is the deploy done?" in said


def _thread_context(parent: str) -> str:
    from personalclaw.context import ContextBuilder

    message, _ = ContextBuilder().build_message(
        "what did I miss?",
        is_new_session=False,
        channel_id="C0TEAM",
        thread_ts="1700000000.1",
        thread_parent_text=parent,
    )
    return message


def test_the_post_that_started_a_thread_reaches_the_model_fenced():
    """🔴 Red on integration: the post went into the prompt as plain text."""
    for name, text in ORDINARY.items():
        assert_fenced(
            _thread_context(text), text, source="channel:C0TEAM:thread:1700000000.1", what=name
        )


def test_a_thread_s_first_post_the_screen_refuses_never_reaches_the_model():
    """🔴 Red on integration: the post went into the prompt whatever it held."""
    for name, text in REFUSED_TEXTS.items():
        assert_withheld(_thread_context(text), text, what=name)


# ── a line someone else sent, in a conversation's history ─────────────────────────────────────


def _theirs(text: str) -> str:
    from personalclaw.turn_source import turn_line

    row = {
        "role": "user",
        "content": text,
        "source_thread": "C0TEAM:1700000000.1",
        "source_user": "U0FRIEND",
        "source_channel": "slack",
    }
    return turn_line(row)


def test_a_line_someone_else_sent_reaches_the_model_fenced_as_theirs():
    for name, text in ORDINARY.items():
        assert_fenced(_theirs(text), text, source="channel:slack:U0FRIEND", what=name)


def test_a_line_someone_else_sent_that_the_screen_refuses_never_reaches_the_model():
    """🔴 Red on integration: it was fenced, and never screened."""
    for name, text in REFUSED_TEXTS.items():
        line = _theirs(text)
        assert_withheld(line, text, what=name)
        assert line.startswith("SENT BY SOMEONE OTHER THAN THE USER"), line
