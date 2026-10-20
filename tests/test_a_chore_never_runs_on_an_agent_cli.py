"""A chore never runs on an agent CLI, and no helper call approves a tool nobody was asked about.

A chore is model work nobody typed: a chat's title and its tags (one call), its follow-up chips,
the new-chat suggestions. Each asks a model for a text answer, and an agent CLI is a whole agent
with tools of its own.

Measured before this change, on a home whose default agent is an agent CLI and which had no model
chosen: each of those chores started the agent CLI, and the best-of-n and evaluation judges were
the CLI too. The one-shot call every chore is made through built the first registered provider when
nothing was bound, and on such a home that is the agent CLI's entry: the record shows the CLI
started for a title, for follow-ups and for the suggestions. The calls the CLI asked about were
refused, but a helper call that named no policy of its own approved a tool request with nobody
asked.

Now:

* A chore runs on the model chosen for the Background use case (its chain in Settings → Models,
  else a configured model that names one of its own), never on an agent CLI. With none chosen it
  is skipped: no CLI starts, nothing is approved, and each surface says why, in the sentence
  ``chores.needs_a_model`` composes, naming where the model is chosen.
* With a model chosen, each chore runs on it as before.
* A helper call's tool requests are refused unless its caller passes a policy of its own
  (``stream_and_collect``'s default), and each refusal is audited with its reason.

Driven through the real chore paths, the real one-shot call and the real provider registry, against
``scripted_acp_agent.py`` over stdio for the CLI and an in-process model for the bound case.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from chat_test_helpers import _make_state

from personalclaw import suggestions
from personalclaw.dashboard import chat_followups, chat_title
from personalclaw.llm.base import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    LLMEvent,
)
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry, set_default_registry

AGENT = Path(__file__).with_name("scripted_acp_agent.py")
CLI = "pcfixture-cli"
DEFAULT_AGENT = "Pcfixture"
MODEL_ENTRY = "pcfixture-model"
MODEL_REF = f"{MODEL_ENTRY}:pcfixture-small"

#: What each surface says while no model is chosen for its chore.
SUGGESTIONS_NEED = "Suggestions need a model: choose one in Settings → Models."
FOLLOWUPS_NEED = "Follow-ups need a model: choose one in Settings → Models."
TITLES_NEED = "Titles and tags need a model: choose one in Settings → Models."

#: What the bound model answers each chore with.
TITLE_ANSWER = "Reading a file in Python\nTAGS: python"
FOLLOWUPS_ANSWER = '["Show a code example", "How do I handle errors?"]'
SUGGESTIONS_ANSWER = '["Review my Python notes", "Plan the next step"]'


def _wire(record: Path, kind: str) -> list[dict]:
    if not record.exists():
        return []
    rows = [json.loads(line) for line in record.read_text().splitlines() if line.strip()]
    return [r for r in rows if r["kind"] == kind]


@pytest.fixture
def registry() -> ProviderRegistry:
    """A provider registry of this test's own, holding the agent-CLI type as every home does (the
    shared one is put back after the test)."""
    from personalclaw.llm.acp_agent import ACP_AGENT_CAPABILITY, _factory

    fresh = ProviderRegistry()
    fresh.register_type(ACP_AGENT_CAPABILITY, _factory)
    set_default_registry(fresh)
    return fresh


def _model_type(registry: ProviderRegistry, name: str, provider: Any, *, tools: bool) -> None:
    """A configured model instance *name* (its row in config.json, and its entry, which names no
    model of its own) whose every build is *provider*."""
    from personalclaw.config.transactions import mutate_config

    mutate_config(
        lambda doc: doc.setdefault("providers", []).append(
            {"name": name, "type": name, "model": ""}
        )
    )
    registry.register_type(
        ProviderCapability(
            type=name,
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=tools,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=8192,
        ),
        lambda **_kw: provider,
    )
    registry.register_entry(ProviderEntry(name=name, type=name, model=""))


@pytest.fixture
def cli_home(registry, tmp_path, monkeypatch) -> Path:
    """A home whose default agent is an agent CLI and which has no model chosen: the scripted CLI
    registered the way its app registers it (the only runtime there is), an agent on it made the
    default agent, and nothing in Settings → Models. Returns the CLI's wire record."""
    from personalclaw.acp_bundles._register import register_acp_cli_entry
    from personalclaw.config.loader import AgentProfile, AppConfig

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    record = tmp_path / "cli-wire.jsonl"
    entry = register_acp_cli_entry(
        cli=CLI,
        dialect="default",
        command=[sys.executable, str(AGENT), "deny-ends", str(record), "spec"],
    )
    assert entry is not None
    assert [e.name for e in registry.list_entries()] == [f"acp:{CLI}"]
    cfg = AppConfig.load()
    cfg.agent.provider = f"acp:{CLI}"
    cfg.agents[DEFAULT_AGENT] = AgentProfile(provider=f"acp:{CLI}")
    cfg.default_agent = DEFAULT_AGENT
    cfg.save()
    again = AppConfig.load()
    assert again.agents[again.default_agent].provider == f"acp:{CLI}"
    return record


class _Model:
    """An in-process model: it answers each chore by what its prompt asks for, and records every
    prompt it was sent."""

    def __init__(self, sent: list[str]) -> None:
        self.sent = sent

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, message: str):
        self.sent.append(message)
        if "Rules for these follow-ups" in message:
            text = FOLLOWUPS_ANSWER
        elif "Rules for these suggestions" in message:
            text = SUGGESTIONS_ANSWER
        else:
            text = TITLE_ANSWER
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=text)
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)


@pytest.fixture
def model_sent(cli_home, registry) -> list[str]:
    """The same home with a model chosen for the Background use case: every prompt it is sent."""
    from personalclaw.providers.use_cases import save_active_models

    sent: list[str] = []
    _model_type(registry, MODEL_ENTRY, _Model(sent), tools=False)
    save_active_models({"background": [MODEL_REF]})
    return sent


def _chat(state) -> Any:
    session = state.get_or_create_session("chore-chat")
    session.append("user", "How do I read a file in Python?", "msg msg-u", broadcast=False)
    session.append("assistant", "Use open() with a context manager.", "msg msg-a", broadcast=False)
    session.drain()
    return session


def _events(state, monkeypatch) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    monkeypatch.setattr(state, "broadcast_ws", lambda kind, data=None: events.append((kind, data)))
    return events


@pytest.fixture
def suggestions_state(monkeypatch):
    """A stand-in for the gateway's state with enough context to earn the suggestions a call."""
    monkeypatch.setattr(
        suggestions,
        "_build_context",
        lambda _state: "## User Preferences\nI work mostly in Python and keep short notes.",
    )
    return SimpleNamespace(
        conversation_log=None, _background_tasks=set(), push_refresh=lambda *kinds: None
    )


async def _suggestions_read(state) -> dict:
    """What the new-chat page reads, once the generation it starts has landed."""
    request = SimpleNamespace(app={"state": state}, query={"force": "1"})
    await suggestions.api_suggestions(request)
    await suggestions.get_suggestions_cache(state)._task
    response = await suggestions.api_suggestions(SimpleNamespace(app={"state": state}, query={}))
    return json.loads(response.body)


# ── no model chosen: nothing starts the CLI, nothing is approved, each surface says why ─────────


@pytest.mark.asyncio
async def test_a_title_and_its_tags_start_no_agent_cli(cli_home, tmp_path, monkeypatch):
    """🔴 Red before: the title chore started the CLI, its request to run a command was answered
    "allow" with nobody asked, and the chat was titled with what the CLI said after running it."""
    state = _make_state(tmp_path)
    session = _chat(state)

    await chat_title._maybe_auto_title(state, session)

    assert _wire(cli_home, "spawn") == [], "a chore started the agent CLI"
    assert _wire(cli_home, "permission_answer") == [], "a chore's tool request was answered"
    assert not session._titled and not session.tags


async def _detail(state, session) -> dict:
    """The chat as its page reads it, on opening it and after each turn."""
    from personalclaw.dashboard.chat_handlers import api_chat_session_detail

    request = SimpleNamespace(app={"state": state}, match_info={"session": session.key}, query={})
    return json.loads((await api_chat_session_detail(request)).body)


@pytest.mark.asyncio
async def test_the_title_surface_says_why(cli_home, tmp_path):
    """The chat's header reads why it has no title, and asked for one (Regenerate title) the chat
    keeps the name it has and the answer says what titles and tags are waiting on, and where."""
    state = _make_state(tmp_path)
    session = _chat(state)
    request = SimpleNamespace(app={"state": state}, match_info={"session": session.key})

    assert (await _detail(state, session))["title_needs_model"] == TITLES_NEED
    response = await chat_title.api_chat_session_generate_title(request)

    body = json.loads(response.body)
    assert body["needs_model"] == TITLES_NEED
    assert not session._titled
    assert _wire(cli_home, "spawn") == []


@pytest.mark.asyncio
async def test_follow_ups_start_no_agent_cli_and_say_why(cli_home, tmp_path, monkeypatch):
    """🔴 Red before: the follow-up chore started the CLI and approved its request, twice (the call
    asks once more when an answer is not a list). Now the chips' place says why there are none."""
    state = _make_state(tmp_path)
    session = _chat(state)
    events = _events(state, monkeypatch)

    await chat_followups._maybe_followups(state, session)

    assert _wire(cli_home, "spawn") == [], "a chore started the agent CLI"
    assert _wire(cli_home, "permission_answer") == []
    said = [d for kind, d in events if kind == "chat_followups"]
    assert said == [{"session": session.key, "items": [], "needs_model": FOLLOWUPS_NEED}]


@pytest.mark.asyncio
async def test_suggestions_start_no_agent_cli_and_say_why(cli_home, suggestions_state):
    """🔴 Red before: generating the suggestions started the CLI and approved its request. Now the
    new-chat page keeps the standard list and says why it is not one of her own."""
    body = await _suggestions_read(suggestions_state)

    assert _wire(cli_home, "spawn") == [], "a chore started the agent CLI"
    assert _wire(cli_home, "permission_answer") == []
    assert body["suggestions"] == suggestions._FALLBACK_SUGGESTIONS
    assert body["needs_model"] == SUGGESTIONS_NEED


# ── a model chosen: each chore runs on it, as before ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_with_a_model_chosen_the_title_and_tags_run_on_it(
    cli_home, model_sent, tmp_path, monkeypatch
):
    state = _make_state(tmp_path)
    session = _chat(state)
    assert (await _detail(state, session))["title_needs_model"] == "", "a model is chosen"

    await chat_title._maybe_auto_title(state, session)

    assert session._titled and session.title == "Reading a file in Python"
    assert [t["name"] for t in state._tags if t["id"] in session.tags] == ["python"]
    assert len(model_sent) == 1 and "TAGS:" in model_sent[0]
    assert _wire(cli_home, "spawn") == []


@pytest.mark.asyncio
async def test_with_a_model_chosen_the_title_surface_says_nothing_is_missing(
    cli_home, model_sent, tmp_path
):
    state = _make_state(tmp_path)
    session = _chat(state)
    request = SimpleNamespace(app={"state": state}, match_info={"session": session.key})

    body = json.loads((await chat_title.api_chat_session_generate_title(request)).body)

    assert body == {"ok": True, "title": "Reading a file in Python"}


@pytest.mark.asyncio
async def test_with_a_model_chosen_follow_ups_run_on_it(
    cli_home, model_sent, tmp_path, monkeypatch
):
    state = _make_state(tmp_path)
    session = _chat(state)
    events = _events(state, monkeypatch)

    await chat_followups._maybe_followups(state, session)

    said = [d for kind, d in events if kind == "chat_followups"]
    assert said == [
        {"session": session.key, "items": ["Show a code example", "How do I handle errors?"]}
    ]
    assert _wire(cli_home, "spawn") == []


@pytest.mark.asyncio
async def test_with_a_model_chosen_suggestions_run_on_it(cli_home, model_sent, suggestions_state):
    body = await _suggestions_read(suggestions_state)

    assert body["suggestions"] == ["Review my Python notes", "Plan the next step"]
    assert body["needs_model"] == ""
    assert _wire(cli_home, "spawn") == []


# ── a judge asks for a text answer too ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_slate_is_judged_by_no_agent_cli(cli_home):
    """🔴 Red before: with nothing bound, best-of-n's judge resolved the reasoning use case to the
    agent CLI's entry and started it to grade each sample. Now the slate is left unjudged."""
    from personalclaw.sampling import _judge_candidates

    judged = await _judge_candidates("Name a colour.", "", [{"idx": 0, "text": "blue"}])

    assert judged == []
    assert _wire(cli_home, "spawn") == []


@pytest.mark.asyncio
async def test_an_eval_candidate_is_judged_by_no_agent_cli(cli_home):
    from personalclaw.evals.candidate_score import _judge_factory
    from personalclaw.providers.provider_bridge import ProviderResolutionError

    judge = _judge_factory(None)
    with pytest.raises(ProviderResolutionError):
        await judge.start()
    assert _wire(cli_home, "spawn") == []


# ── a helper call is let use no tool unless its caller says so ────────────────────────────────


class _AsksForATool:
    """A provider whose turn asks to run a command before it answers."""

    provider_id = MODEL_ENTRY

    def __init__(self) -> None:
        self.approved: list[object] = []
        self.rejected: list[object] = []

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def approve_tool(self, request_id) -> None:
        self.approved.append(request_id)

    async def reject_tool(self, request_id) -> None:
        self.rejected.append(request_id)

    async def stream(self, message: str):
        yield LLMEvent(
            kind=EVENT_PERMISSION_REQUEST,
            request_id="req-1",
            tool_call_id="call-1",
            title="Run command",
            tool_kind="execute",
            tool_input={"command": "git status"},
        )
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="blue")
        yield LLMEvent(kind=EVENT_COMPLETE)


def _refusals() -> list[dict]:
    from personalclaw.config.loader import config_dir

    path = config_dir() / "security_events.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return [
        r
        for r in rows
        if r.get("event_type") == "tool_invocation" and r.get("operation") == "Run command"
    ]


@pytest.mark.asyncio
async def test_a_tool_asked_for_under_the_default_policy_is_refused_and_audited():
    """🔴 Red before: the default policy approved the request with nobody asked."""
    from personalclaw.llm_helpers import stream_and_collect

    provider = _AsksForATool()

    text = await stream_and_collect(provider, "Name a colour.")

    assert text == "blue", "the answer still comes back"
    assert provider.approved == [] and provider.rejected == ["req-1"]
    (row,) = _refusals()
    assert row["outcome"] == "rejected"
    assert row["metadata"]["reason"] == "reject_all_policy"


@pytest.mark.asyncio
async def test_a_one_shot_call_on_a_model_that_asks_for_a_tool_is_refused(registry):
    """The one-shot call every chore is made through passes no policy, so it is the default's."""
    from personalclaw.llm_helpers import one_shot_completion
    from personalclaw.providers.use_cases import save_active_models

    provider = _AsksForATool()
    _model_type(registry, MODEL_ENTRY, provider, tools=True)
    save_active_models({"background": [MODEL_REF]})

    assert await one_shot_completion("Name a colour.", use_case="background") == "blue"
    assert provider.approved == [] and provider.rejected == ["req-1"]
