"""A model someone NAMED is served by that model, or the platform says it was not.

Measured on ``main`` (f47ffcb82) before any of this was written:

* An agent pinned to a model that does not exist ran on the chat binding. The only trace was a
  ``logger.info`` in ``provider_bridge._reconcile_agent_model``. The chat, the room and the Agents
  page all went on showing the model the user chose.
* An agent pinned to an available model of the SECOND provider in the chat chain was sent to the
  FIRST provider. The runtime resolved its inference provider from the chain and only borrowed the
  pin's model id, so ``served_model_ref`` named a model the provider never offered.
* A workflow step whose binding could not be built (a model app whose update failed) reported
  success. ``one_shot_completion`` swallowed the refusal and built the first registered provider
  instead, so best-of-n's calls went to ``fake-oai:fake-model-1``.
* A pinned model (the cross-model judge's) whose provider could not serve was replaced by the head
  of the chain, which is the very model the isolation control had excluded.
* A fallback the user DID configure (a second chain entry) served a call without anything saying
  so. The call recorded the model that answered and nothing about the one that was asked for.

The rule these tests pin: a named model either serves, or the refusal names it; a substitute
serves only where the user configured one (the chain) or where an agent's pin cannot run (the chat
binding), and then the call, the step, the reply and the room all say "ran on X instead of Y".
"""

from __future__ import annotations

from typing import Any

import pytest

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.providers.provider_bridge import ProviderResolutionError

LIVE = "fake-oai"
LIVE_MODEL = "fake-model-1"
LIVE_REF = f"{LIVE}:{LIVE_MODEL}"
OTHER = "other-oai"
OTHER_REF = f"{OTHER}:other-1"
#: An entry whose app failed to load: config names it, and nothing registers its type.
BROKEN = "broken-app"
BROKEN_REF = f"{BROKEN}:model-x"


class _Answers:
    """A provider that answers with the entry and model it was built for, so a test can see who
    served without trusting any record the code under test wrote."""

    served_ref = ""

    def __init__(self, entry: str, model: str) -> None:
        self.entry = entry
        self.model = model

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def stream(self, message: str):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"answered by {self.entry}:{self.model}")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)

    async def complete(self, messages: list[dict], **_kw: Any):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"answered by {self.entry}:{self.model}")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)


def _capability(type_: str) -> ProviderCapability:
    return ProviderCapability(
        type=type_,
        capabilities=frozenset({Capability.CHAT}),
        supports_streaming=True,
        supports_tools=False,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=8192,
    )


@pytest.fixture
def active(monkeypatch) -> dict[str, list[str]]:
    """Two live providers and one whose app failed to load; returns the Settings → Models map."""
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **kwargs: Any):
        return _Answers(entry.name, str(kwargs.get("model") or entry.model))

    registry.register_type(_capability("fakeoai"), _factory)
    registry.register_entry(ProviderEntry(name=LIVE, type="fakeoai", model=LIVE_MODEL))
    registry.register_entry(ProviderEntry(name=OTHER, type="fakeoai", model="other-1"))
    registry.register_entry(ProviderEntry(name=BROKEN, type="brokenapp", model="model-x"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    bound: dict[str, list[str]] = {}
    monkeypatch.setattr("personalclaw.providers.use_cases.load_active_models", lambda: bound)
    return bound


def _agent(name: str, model: str) -> None:
    from personalclaw.config.loader import AgentProfile, AppConfig

    cfg = AppConfig.load()
    cfg.agents[name] = AgentProfile(model=model)
    cfg.save()


def _native(agent: str | None = None, model_override: str | None = None):
    from personalclaw.providers import provider_bridge

    return provider_bridge._build_native_runtime(
        use_case="chat",
        session_key="dashboard:named-model",
        agent=agent,
        model_override=model_override,
        cwd=None,
    )


# ── a workflow step: the binding it asked for, or a refusal that names it ──


@pytest.mark.asyncio
async def test_a_step_on_a_broken_binding_fails_naming_the_model_it_asked_for(active):
    """🔴 Red on main: the refusal was swallowed and the first registered provider answered, so
    the step reported success on a model nobody chose."""
    from personalclaw.llm_helpers import one_shot_completion

    active["reasoning"] = [BROKEN_REF]
    with pytest.raises(ProviderResolutionError) as refused:
        await one_shot_completion("Name a colour.", use_case="reasoning")
    assert BROKEN_REF in str(refused.value)


@pytest.mark.asyncio
async def test_best_of_n_on_a_broken_binding_samples_nothing_and_names_the_model(active):
    """🔴 Red on main, the case the model-calls lane measured: every sample was answered by the
    first registered provider and the slate picked a winner, so the run read "complete" on a model
    nobody chose. Now no sample runs, and the failure the step shows names the model and the fix."""
    from personalclaw.guardrails.calls import capture_model_calls
    from personalclaw.sampling import best_of_n

    active["background"] = [BROKEN_REF]
    with capture_model_calls() as log:
        result = await best_of_n("Name a colour.", 2)
    assert result["winner"] is None, result
    assert log.calls == [], "no call may go out on a model nobody chose"
    assert BROKEN_REF in result["note"], "the step's failure names the model it asked for"
    assert result["failure"]["class"] == "user", "a retry cannot build a model whose app is gone"
    assert "install an app that provides 'brokenapp'" in result["failure"]["fix"]


@pytest.mark.asyncio
async def test_a_pinned_model_that_cannot_serve_is_refused_not_replaced_by_the_chain(active):
    """🔴 Red on main: the pin fell through to the chain, so the cross-model judge ran on the head
    of the chain, which is exactly the model it was isolated from."""
    from personalclaw.llm_helpers import one_shot_completion

    active["reasoning"] = [LIVE_REF]
    with pytest.raises(ProviderResolutionError) as refused:
        await one_shot_completion("Judge this.", use_case="reasoning", model=BROKEN_REF)
    assert BROKEN_REF in str(refused.value)


@pytest.mark.asyncio
async def test_nothing_bound_runs_on_the_configured_model_that_names_its_own(active):
    """Nothing is bound, so nothing was asked for: the bridge's one rule picks the first configured
    instance that names a model of its own, and that model answers."""
    from personalclaw.llm_helpers import one_shot_completion

    assert await one_shot_completion("Hello.", use_case="reasoning") == (f"answered by {LIVE_REF}")


@pytest.mark.asyncio
async def test_a_fallback_the_user_configured_is_named_on_the_call_it_served(active):
    """🔴 Red on main: the second chain entry answered and nothing recorded that the first was
    asked for. The call now carries the substitution, in the words the step and Introspect show."""
    from personalclaw.guardrails.calls import capture_model_calls
    from personalclaw.llm_helpers import one_shot_completion

    active["reasoning"] = [BROKEN_REF, LIVE_REF]
    with capture_model_calls() as log:
        text = await one_shot_completion("Name a colour.", use_case="reasoning")
    assert text == f"answered by {LIVE_REF}"
    assert [c.provider for c in log.calls] == [LIVE]
    (sentence,) = log.substitutions
    assert sentence.startswith(f"ran on {LIVE_REF} instead of {BROKEN_REF}: ")
    assert "brokenapp" in sentence, "the reason names what is missing"


@pytest.mark.asyncio
async def test_a_chain_whose_head_serves_records_no_substitution(active):
    """The vacuity control for the one above: nothing was replaced, so nothing is said."""
    from personalclaw.guardrails.calls import capture_model_calls
    from personalclaw.llm_helpers import one_shot_completion

    active["reasoning"] = [LIVE_REF, OTHER_REF]
    with capture_model_calls() as log:
        await one_shot_completion("Name a colour.", use_case="reasoning")
    assert [c.provider for c in log.calls] == [LIVE]
    assert log.substitutions == []


# ── an agent: its pin is kept, and where it cannot run the platform says so ──


def test_an_agent_pinned_to_a_missing_model_says_which_model_it_ran_on(active):
    """🔴 Red on main: the pin was dropped to "" with a log line and the chat binding answered."""
    from personalclaw.config.loader import AppConfig

    active["chat"] = [LIVE_REF]
    _agent("Researcher", "fake-oai:no-such-model")
    runtime = _native(agent="Researcher")
    note = runtime.model_substitution
    assert note is not None
    assert note.requested == "fake-oai:no-such-model"
    assert note.served == LIVE_REF
    assert runtime.served_model_ref == LIVE_REF
    assert note.sentence() == (
        "ran on fake-oai:fake-model-1 instead of Researcher's model fake-oai:no-such-model: "
        "it is not one of the chat models set up in Settings → Models. Pick another model for "
        "Researcher on the Agents page, or add it in Settings → Models"
    )
    # The user's choice is KEPT: nothing rewrote the pin.
    assert AppConfig.load().agents["Researcher"].model == "fake-oai:no-such-model"


def test_an_agent_pinned_to_an_available_model_runs_on_that_model_and_says_nothing(active):
    """🔴 Red on main: the pin named the SECOND chain provider, and the runtime was built on the
    FIRST, sending it a model id it does not offer."""
    active["chat"] = [LIVE_REF, OTHER_REF]
    _agent("Researcher", OTHER_REF)
    runtime = _native(agent="Researcher")
    assert runtime.served_model_ref == OTHER_REF
    assert runtime.model_substitution is None


def test_a_chat_s_own_model_that_is_gone_is_said_too(active):
    """The per-chat pick outranks the agent's pin, so it is held to the same rule."""
    active["chat"] = [LIVE_REF]
    runtime = _native(model_override=BROKEN_REF)
    note = runtime.model_substitution
    assert note is not None and note.requested == BROKEN_REF and note.served == LIVE_REF
    assert "this chat" in note.sentence()


def test_an_agent_with_no_pin_runs_on_the_chat_binding_and_says_nothing(active):
    active["chat"] = [LIVE_REF]
    _agent("Plain", "")
    runtime = _native(agent="Plain")
    assert runtime.served_model_ref == LIVE_REF
    assert runtime.model_substitution is None


# ── a workflow step, end to end: the run, its ledger row, its node row and Introspect ──


def _isolated_runs(home, monkeypatch):
    from personalclaw.workflows import store

    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(store, "config_dir", lambda: home)


async def _run_one_infer(home, monkeypatch) -> dict[str, Any]:
    """One `infer` step on the reasoning axis, driven by the real engine and the real
    `one_shot_completion` — only the providers are fakes."""
    from personalclaw.ledger.reader import read_journal
    from personalclaw.workflows import run_cockpit, service, store
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.models import WorkflowRun

    spec = {
        "name": "named-model",
        "root": {
            "kind": "infer",
            "id": "ask",
            "config": {"prompt": "Name a colour.", "model_tier": "reasoning"},
        },
    }
    _isolated_runs(home, monkeypatch)
    run = store.create(WorkflowRun(id="", workflow_name="named-model"))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(publish=lambda e, p: None))
    status = await controller.run_to_completion(timeout=60)
    journal = read_journal(store, run.id)
    return {
        "status": status,
        "completed": [r for r in journal if r.get("kind") == "step_completed"],
        "failed": [r for r in journal if r.get("kind") == "step_failed"],
        "rows": list(service._nodes_of(run.id)),
        "introspect": run_cockpit.introspect(run.id),
    }


@pytest.mark.asyncio
async def test_a_workflow_step_on_a_broken_binding_fails_and_names_the_model(
    active, tmp_path, monkeypatch
):
    """🔴 Red on main: the step completed, answered by the first registered provider."""
    from personalclaw.workflows.models import RunStatus

    active["reasoning"] = [BROKEN_REF]
    out = await _run_one_infer(tmp_path / "home", monkeypatch)
    assert out["status"] == RunStatus.FAILED, out
    assert out["completed"] == []
    assert BROKEN_REF in out["failed"][-1]["error"]


@pytest.mark.asyncio
async def test_a_workflow_step_a_configured_fallback_served_says_so_everywhere(
    active, tmp_path, monkeypatch
):
    """🔴 Red on main: the step completed on the second chain entry and its row, the run view and
    Introspect named only that model."""
    from personalclaw.workflows.models import RunStatus

    active["reasoning"] = [BROKEN_REF, LIVE_REF]
    out = await _run_one_infer(tmp_path / "home", monkeypatch)
    assert out["status"] == RunStatus.COMPLETE, out
    (row,) = out["completed"]
    assert row["model"] == LIVE_MODEL and row["provider"] == LIVE
    (sentence,) = row["model_substituted"]
    assert sentence.startswith(f"ran on {LIVE_REF} instead of {BROKEN_REF}: ")
    (node,) = [r for r in out["rows"] if r["node_id"] == "ask"]
    assert node["model_substituted"] == [sentence]
    assert out["introspect"]["stats"]["substitutions"] == [sentence]


@pytest.mark.asyncio
async def test_a_workflow_step_on_its_own_model_writes_no_substitution(
    active, tmp_path, monkeypatch
):
    """The row a step that got the model it asked for writes is the row it always wrote."""
    active["reasoning"] = [LIVE_REF, OTHER_REF]
    out = await _run_one_infer(tmp_path / "home", monkeypatch)
    (row,) = out["completed"]
    assert "model_substituted" not in row
    assert all("model_substituted" not in r for r in out["rows"])
    assert out["introspect"]["stats"]["substitutions"] == []


# ── a chat turn: said before the reply, and stamped on it so a reload says it too ──


def _substitution():
    from personalclaw.llm.base import ModelSubstitution

    return ModelSubstitution(
        requested="fake-oai:no-such-model",
        served=LIVE_REF,
        why="it is not one of the chat models set up in Settings → Models",
        fix="pick another model for Researcher on the Agents page, or add it in Settings → Models",
        who="Researcher's model",
    )


async def _chat_turn(tmp_path, *, substitution):
    from unittest.mock import AsyncMock, MagicMock, patch

    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import DashboardState, _ChatSession
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult

    async def _events():
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Blue.")
        yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    client = AsyncMock()
    client.provider_id = "native"
    client.model_substitution = substitution
    client.stream = MagicMock(side_effect=lambda *a, **kw: _events())
    # Synchronous reads the turn makes of its runtime: an AsyncMock would hand back coroutines.
    client.context_usage_pct = MagicMock(return_value=None)
    client.supports_native_commands = False
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock(return_value=(client, True, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    builder = MagicMock()
    builder.hooks.on_tool_call.return_value = ToolHookResult.allow()
    builder.build_message.return_value = ("Name a colour.", None)
    state.context_builder = builder
    hook_store = MagicMock()
    hook_store.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hook_store
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    session = _ChatSession("chat-named-model")
    session._trust = True
    session.append("user", "Name a colour.", "msg msg-u")
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "Name a colour.")
    lines = [
        call.args[1].get("text")
        for call in state.broadcast_ws.call_args_list
        if call.args
        and call.args[0] == "activity_event"
        and call.args[1].get("kind") == "model_substitution"
    ]
    replies = [m for m in session.messages if m.get("role") == "assistant"]
    return lines, replies


@pytest.mark.asyncio
async def test_a_chat_turn_on_a_substitute_says_so_before_the_reply_and_on_it(tmp_path):
    """🔴 Red on main: the reply streamed with nothing saying it came from another model."""
    lines, replies = await _chat_turn(tmp_path, substitution=_substitution())
    expected = (
        "Ran on fake-oai:fake-model-1 instead of Researcher's model fake-oai:no-such-model: it "
        "is not one of the chat models set up in Settings → Models. Pick another model for "
        "Researcher on the Agents page, or add it in Settings → Models."
    )
    assert lines == [expected]
    assert replies[-1]["meta"]["model_substitution"] == expected


@pytest.mark.asyncio
async def test_a_chat_turn_on_the_chosen_model_says_nothing(tmp_path):
    lines, replies = await _chat_turn(tmp_path, substitution=None)
    assert lines == []
    assert "model_substitution" not in (replies[-1].get("meta") or {})


# ── the Agents page: the pin is kept and shown as unavailable, with the fix ──


def test_the_agents_page_marks_a_pin_that_cannot_run_and_keeps_it(active):
    """🔴 Red on main: the list showed the pin like any other, and the editor's picker showed
    "Auto" for a value it did not offer, while every turn ran on the chat binding."""
    import asyncio
    import json

    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers.agents import api_personalclaw_agents

    active["chat"] = [LIVE_REF]
    _agent("Researcher", "fake-oai:no-such-model")
    _agent("Writer", LIVE_REF)
    body = json.loads(
        asyncio.run(api_personalclaw_agents(make_mocked_request("GET", "/api/agents"))).body
    )
    rows = {a["name"]: a for a in body["agents"]}
    assert rows["Researcher"]["model"] == "fake-oai:no-such-model"
    assert rows["Researcher"]["model_unavailable"] == {
        "why": "it is not one of the chat models set up in Settings → Models",
        "fix": "add it in Settings → Models",
    }
    assert rows["Writer"]["model_unavailable"] is None


# ── an agent edit reaches the chats and rooms already open ──


class _Runtime:
    """What the session manager caches per key: records the model it was built with."""

    def __init__(self, model: str) -> None:
        self.model = model
        self.shut_down = False

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        self.shut_down = True

    def is_alive(self) -> bool:
        return True


def _sessions():
    from unittest.mock import MagicMock

    from personalclaw.config.loader import AppConfig
    from personalclaw.session import SessionManager

    cfg = MagicMock()
    cfg.default_agent = ""
    cfg.model = "auto"
    cfg.session.pool_size = 0
    cfg.session.pool_agent = ""
    cfg.session.pool_ttl_secs = 0

    def factory(key, agent=None, model_override=None, **_kw):
        return _Runtime(AppConfig.load().agents[agent].model)

    return SessionManager(cfg, provider_factory=factory)


async def _edit(sessions, name: str, body: dict) -> int:
    import types

    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers.agents import api_personalclaw_agent_update

    app = web.Application()
    app["state"] = types.SimpleNamespace(sessions=sessions, push_refresh=lambda *k: None)
    request = make_mocked_request("PUT", f"/api/agents/{name}", match_info={"name": name}, app=app)

    async def _json():
        return body

    request.json = _json  # type: ignore[assignment]
    return (await api_personalclaw_agent_update(request)).status


@pytest.mark.asyncio
async def test_an_agent_edit_takes_effect_in_its_open_room_on_the_next_turn(active):
    """🔴 Red on main: the room kept the runtime its first turn built, so after the edit it went on
    answering with the old model while the members panel showed the new one."""
    _agent("Researcher", LIVE_REF)
    sessions = _sessions()
    key = "room:r1:Researcher"
    before, _new, _ = await sessions.get_or_create(key, agent="Researcher")
    sessions.release(key)

    assert await _edit(sessions, "Researcher", {"model": OTHER_REF}) == 200

    after, is_new, _ = await sessions.get_or_create(key, agent="Researcher")
    sessions.release(key)
    assert after is not before
    assert after.model == OTHER_REF
    assert is_new, "the rebuilt runtime is a new session, so a chat re-sends its context"
    assert before.shut_down


@pytest.mark.asyncio
async def test_a_turn_running_when_the_agent_is_edited_finishes_on_what_it_started_with(active):
    _agent("Researcher", LIVE_REF)
    sessions = _sessions()
    key = "dashboard:chat-1"
    running, _new, _ = await sessions.get_or_create(key, agent="Researcher")  # permit held

    assert await _edit(sessions, "Researcher", {"model": OTHER_REF}) == 200
    assert not running.shut_down, "nothing is torn down under a turn"

    sessions.release(key)  # the turn ends
    nxt, _is_new, _ = await sessions.get_or_create(key, agent="Researcher")
    sessions.release(key)
    assert nxt is not running and nxt.model == OTHER_REF


@pytest.mark.asyncio
async def test_an_edit_of_one_agent_leaves_another_agent_s_sessions_alone(active):
    _agent("Researcher", LIVE_REF)
    _agent("Writer", LIVE_REF)
    sessions = _sessions()
    kept, _new, _ = await sessions.get_or_create("room:r1:Writer", agent="Writer")
    sessions.release("room:r1:Writer")

    assert await _edit(sessions, "Researcher", {"model": OTHER_REF}) == 200

    again, _is_new, _ = await sessions.get_or_create("room:r1:Writer", agent="Writer")
    sessions.release("room:r1:Writer")
    assert again is kept


def test_a_room_s_members_panel_marks_a_pin_that_cannot_run(active):
    """🔴 Red on main: the panel named the member's pin while its turns ran on the chat model."""
    import types

    from personalclaw.dashboard.handlers.rooms import _member_bindings

    active["chat"] = [LIVE_REF]
    _agent("Researcher", "fake-oai:no-such-model")
    _agent("Writer", LIVE_REF)
    room = types.SimpleNamespace(
        members=[types.SimpleNamespace(name="Researcher"), types.SimpleNamespace(name="Writer")]
    )
    rows = {b["name"]: b for b in _member_bindings(room)}
    assert rows["Researcher"]["model"] == "fake-oai:no-such-model"
    assert rows["Researcher"]["model_unavailable"]["why"] == (
        "it is not one of the chat models set up in Settings → Models"
    )
    assert rows["Writer"]["model_unavailable"] is None
