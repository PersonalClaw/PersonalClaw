"""A chat turn whose model fails before it says anything is answered by the next model in its chain,
and says so; and a provider's refusal names the real fix.

Measured on ``main`` before any of this was written:

* A chat turn whose model failed before any output was retried ONCE on that same model, and then
  the turn died with its error, even with a second model configured after it in Settings →
  Models. ``run_over_use_case_chain`` walks the chain for background calls only
  (``llm_helpers.py``: "The INTERACTIVE chat/code_tools stream is deliberately NOT a consumer"),
  and the native loop had nothing of its own.
* A Bedrock ``AccessDeniedException`` read as a bad API key whenever its text held ``permission``
  or its account id held ``401`` (``"401" in text``), and as the raw botocore dump otherwise.
  Neither is the fix: the credentials are fine, they are not allowed to use that model.
* A model app's own refusal, the SDK's ``ProviderResolutionError`` with the sentence only that app
  can write, was rewritten by the same substring map as soon as it said "permission".

Every provider here is a fake behind the real registry, the real ``_build_native_runtime`` and the
real native loop: only the models are scripted.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import personalclaw.agents.native.runtime as runtime_mod
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry

pytestmark = pytest.mark.asyncio

#: The event a fallback's sentence arrives in (``llm.events.EVENT_MODEL_SUBSTITUTION``), spelled out
#: so this file collects on a tree that does not have it yet.
SUBSTITUTION = "model_substitution"

DOWN, DOWN_REF = "down-oai", "down-oai:down-1"
UP, UP_REF = "up-oai", "up-oai:up-1"
ALSO_DOWN, ALSO_DOWN_REF = "also-down", "also-down:down-2"
#: A provider whose wire carries images, serving a model whose id says it reads them.
EYE, EYE_REF = "eye-oai", "eye-oai:vision-1"
IMAGE = "data:image/png;base64,AAAA"

OVERLOADED = "Error code: 529 - {'type': 'error', 'error': {'type': 'overloaded_error'}}"
OVERLOADED_CLAUSE = "the model provider is rate-limiting or overloaded right now"
REFUSED = "connection refused by the model host"


class _Scripted:
    """A provider for one registry entry: answers with who it is, or fails as told to."""

    supports_tools = False

    def __init__(self, entry: str, model: str, world: "_World") -> None:
        self.entry = entry
        self.model = model
        self.world = world
        self.served_ref = f"{entry}:{model}"

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **kw: Any):
        images = [
            part["image_url"]["url"]
            for m in messages
            if m.get("role") == "user" and isinstance(m.get("content"), list)
            for part in m["content"]
            if part.get("type") == "image_url"
        ]
        self.world.calls.append((self.entry, str(kw.get("model") or self.model), images))
        failure = self.world.failures.get(self.entry)
        if isinstance(failure, tuple):
            text, exc = failure
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=text)
            raise exc
        if failure is not None:
            raise failure
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"answered by {self.entry}:{kw.get('model')}")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)


class _World:
    def __init__(self) -> None:
        self.failures: dict[str, Any] = {}
        self.calls: list[tuple[str, str, list[str]]] = []
        self.active: dict[str, list[str]] = {}


def _capability(type_: str = "scripted", *, vision: bool = False) -> ProviderCapability:
    return ProviderCapability(
        type=type_,
        capabilities=frozenset({Capability.CHAT}),
        supports_streaming=True,
        supports_tools=False,
        supports_embeddings=False,
        supports_vision=vision,
        max_context_tokens=8192,
    )


@pytest.fixture
def world(monkeypatch) -> _World:
    w = _World()
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **kwargs: Any):
        return _Scripted(entry.name, str(kwargs.get("model") or entry.model), w)

    registry.register_type(_capability(), _factory)
    registry.register_type(_capability("scripted-vision", vision=True), _factory)
    registry.register_entry(ProviderEntry(name=DOWN, type="scripted", model="down-1"))
    registry.register_entry(ProviderEntry(name=UP, type="scripted", model="up-1"))
    registry.register_entry(ProviderEntry(name=ALSO_DOWN, type="scripted", model="down-2"))
    registry.register_entry(ProviderEntry(name=EYE, type="scripted-vision", model="vision-1"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr("personalclaw.providers.use_cases.load_active_models", lambda: w.active)
    monkeypatch.setattr(runtime_mod, "_INFERENCE_RETRY_BACKOFF_SECS", 0.0)
    return w


async def _runtime(*, agent: str | None = None, announce: bool = True):
    from personalclaw.providers import provider_bridge

    rt = provider_bridge._build_native_runtime(
        use_case="chat",
        session_key="dashboard:fails-over",
        agent=agent,
        model_override=None,
        cwd=None,
    )
    await rt.start()
    rt.set_approval_policy("auto")
    announce_failover = getattr(rt, "announce_failover", None)
    if announce and callable(announce_failover):
        announce_failover()
    return rt


async def _turn(rt, message: str = "Name a colour.") -> list[LLMEvent]:
    return [ev async for ev in rt.stream(message)]


def _agent(name: str, model: str) -> None:
    from personalclaw.config.loader import AgentProfile, AppConfig

    cfg = AppConfig.load()
    cfg.agents[name] = AgentProfile(model=model)
    cfg.save()


# ── a chat turn falls back down its chain, and says so ──


async def test_a_turn_whose_model_fails_before_it_replies_is_answered_by_the_next_one(world):
    """🔴 Red on main: the turn was retried once on the same model, then died with its error."""
    world.active["chat"] = [DOWN_REF, UP_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    rt = await _runtime()
    try:
        events = await _turn(rt)
    except Exception as exc:  # noqa: BLE001 — the defect IS the raise
        pytest.fail(f"the turn failed instead of falling back to {UP_REF}: {exc!r}")

    kinds = [ev.kind for ev in events]
    assert kinds == [SUBSTITUTION, EVENT_TEXT_CHUNK, EVENT_COMPLETE], kinds
    assert events[0].text == (
        f"Ran on {UP_REF} instead of {DOWN_REF}: it failed before it replied "
        f"({OVERLOADED_CLAUSE})."
    ), "said before the reply, naming both models and why"
    assert events[1].text == f"answered by {UP_REF}"
    assert [c[0] for c in world.calls] == [DOWN, DOWN, UP], "one retry, then the next model"


async def test_a_turn_resolved_past_an_open_breaker_sends_that_entry_its_own_model(world):
    """🔴 Red on main, found driving this: with the head's breaker open the runtime was built on
    the next entry's provider and sent it the HEAD's model id, while the line said it ran on the
    next entry's model."""
    from personalclaw.guardrails.breaker import get_breaker

    world.active["chat"] = [DOWN_REF, UP_REF]
    breaker = get_breaker(DOWN)
    while not breaker.is_open():
        breaker.record_failure()
    rt = await _runtime()
    assert rt.served_model_ref == UP_REF
    events = await _turn(rt)
    assert world.calls[0][:2] == (UP, "up-1"), world.calls
    assert f"answered by {UP_REF}" in [ev.text for ev in events]


async def test_the_next_turn_starts_on_the_chosen_model_again(world):
    world.active["chat"] = [DOWN_REF, UP_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    rt = await _runtime()
    await _turn(rt)
    assert rt.served_model_ref == DOWN_REF, "the fallback answered one turn, not the session"

    world.failures.clear()
    getattr(rt, "announce_failover", lambda: None)()
    events = await _turn(rt, "And another?")
    assert [ev.kind for ev in events] == [EVENT_TEXT_CHUNK, EVENT_COMPLETE]
    assert events[0].text == f"answered by {DOWN_REF}"


async def test_an_agent_s_pin_that_fails_falls_back_to_the_chat_chain_and_says_whose_it_was(world):
    """🔴 Red on main: the pinned model's failure ended the turn."""
    world.active["chat"] = [UP_REF, DOWN_REF]  # the pin is one of the chat models set up
    _agent("Researcher", DOWN_REF)
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    rt = await _runtime(agent="Researcher")
    assert rt.served_model_ref == DOWN_REF
    try:
        events = await _turn(rt)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"the turn failed instead of falling back: {exc!r}")
    assert events[0].kind == SUBSTITUTION
    assert events[0].text == (
        f"Ran on {UP_REF} instead of Researcher's model {DOWN_REF}: it failed before it replied "
        f"({OVERLOADED_CLAUSE})."
    )


async def test_when_every_model_fails_the_error_names_each_one(world):
    """🔴 Red on main: the error named only the first model's failure, after one retry of it."""
    from personalclaw.llm_helpers import humanize_provider_error

    world.active["chat"] = [DOWN_REF, ALSO_DOWN_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    world.failures[ALSO_DOWN] = RuntimeError(REFUSED)
    rt = await _runtime()
    with pytest.raises(Exception) as failed:
        await _turn(rt)
    assert humanize_provider_error(failed.value) == (
        f"None of this chat's models answered: {DOWN_REF} failed before it replied "
        f"({OVERLOADED_CLAUSE}), and so did {ALSO_DOWN_REF} ({REFUSED}). Try again in a "
        "moment, or check them in Settings → Models."
    )
    assert [c[0] for c in world.calls] == [DOWN, DOWN, ALSO_DOWN]


async def test_a_turn_that_already_showed_output_is_not_moved_to_another_model(world):
    """A retry after visible text re-streams what the user read; so would a fallback."""
    world.active["chat"] = [DOWN_REF, UP_REF]
    world.failures[DOWN] = ("Blu", RuntimeError(OVERLOADED))
    rt = await _runtime()
    with pytest.raises(RuntimeError):
        await _turn(rt)
    assert [c[0] for c in world.calls] == [DOWN]


async def test_a_caller_that_does_not_show_the_fallback_gets_the_failure(world):
    """A background stream never shows the line, so for it a fallback's reply would read as the
    chosen model's: it keeps the failure."""
    world.active["chat"] = [DOWN_REF, UP_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    rt = await _runtime(announce=False)
    with pytest.raises(RuntimeError):
        await _turn(rt)
    assert UP not in [c[0] for c in world.calls]


async def test_a_turn_with_an_image_falls_back_only_to_a_model_that_takes_images(world):
    """🔴 Red on main: the turn died with its model's error. A model that reads no images is passed
    over, since the image would reach it as nothing it could read."""
    from personalclaw.providers import image_input

    image_input.clear_cache()
    world.active["chat"] = [DOWN_REF, UP_REF, EYE_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    rt = await _runtime()
    assert rt.stage_image_part(IMAGE)
    try:
        events = await _turn(rt)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"the turn failed instead of falling back: {exc!r}")
    assert [c[0] for c in world.calls] == [DOWN, DOWN, EYE], world.calls
    assert all(c[2] == [IMAGE] for c in world.calls), "every attempt carried the image"
    assert events[0].text == (
        f"Ran on {EYE_REF} instead of {DOWN_REF}: it failed before it replied "
        f"({OVERLOADED_CLAUSE})."
    )


# ── the chat says it live, and on the reply ──


async def test_a_chat_turn_that_fell_back_says_so_live_and_on_the_reply(world, tmp_path):
    """🔴 Red on main: the turn ended in an error bubble."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import DashboardState, _ChatSession
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult

    world.active["chat"] = [DOWN_REF, UP_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    rt = await _runtime(announce=False)  # the chat runner announces for itself

    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock(return_value=(rt, True, False))
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
    session = _ChatSession("chat-fails-over")
    session._trust = True
    session.append("user", "Name a colour.", "msg msg-u")
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "Name a colour.")

    expected = (
        f"Ran on {UP_REF} instead of {DOWN_REF}: it failed before it replied ({OVERLOADED_CLAUSE})."
    )
    lines = [
        call.args[1].get("text")
        for call in state.broadcast_ws.call_args_list
        if call.args
        and call.args[0] == "activity_event"
        and call.args[1].get("kind") == "model_substitution"
    ]
    assert lines == [expected]
    errors = [m for m in session.messages if m.get("role") == "error"]
    assert errors == [], errors
    (reply,) = [m for m in session.messages if m.get("role") == "assistant"]
    assert reply["content"] == f"answered by {UP_REF}"
    assert reply["meta"]["model_substitution"] == expected


# ── a provider's refusal names the real fix ──

_ACCESS = (
    "The model provider refused this request: the credentials it was sent aren't allowed to use "
    "this model. Give them access to it with the provider, or pick a different model."
)


@pytest.mark.parametrize(
    "raw",
    [
        # An IAM policy without the action. The account id holds "401".
        "An error occurred (AccessDeniedException) when calling the ConverseStream operation: "
        "User: arn:aws:sts::340140123401:assumed-role/Dev/alice is not authorized to perform: "
        "bedrock:InvokeModelWithResponseStream on resource: "
        "arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-pro-v1:0",
        # Model access never granted on the account.
        "An error occurred (AccessDeniedException) when calling the ConverseStream operation: "
        "You don't have access to the model with the specified model ID.",
        # A provider that says permission outright.
        "permission_error: Your API key does not have permission to use the specified resource.",
        "Error code: 403 - Forbidden",
    ],
)
async def test_a_permission_error_names_access_to_the_model_not_the_api_key(raw):
    """🔴 Red on main: these read as a bad API key, or as the raw dump."""
    from personalclaw.llm_helpers import humanize_provider_error

    assert humanize_provider_error(RuntimeError(raw)) == _ACCESS


@pytest.mark.parametrize(
    "raw",
    [
        "request 7a91-1234291 failed upstream",  # a 429 inside an id
        "account 123401 is suspended pending review",  # a 401 inside an id
        "Your message is 1,429 tokens over the limit",  # a figure
    ],
)
async def test_a_status_code_inside_a_longer_number_is_not_that_status(raw):
    """🔴 Red on main: each was read as a rate limit or a bad key."""
    from personalclaw.llm_helpers import humanize_provider_error

    assert humanize_provider_error(RuntimeError(raw)) == raw


async def test_a_status_code_standing_alone_still_reads_as_one():
    from personalclaw.llm_helpers import humanize_provider_error

    assert humanize_provider_error(RuntimeError("HTTP 429 Too Many")).startswith(
        "The model provider is rate-limiting"
    )
    assert "API key" in humanize_provider_error(RuntimeError("Error code: 401"))


async def test_a_model_app_s_own_refusal_is_shown_as_it_wrote_it():
    """🔴 Red on main: the app's sentence said "permission", so the map replaced it with the API
    key line, and the fix only the app knew was gone."""
    from personalclaw.llm_helpers import humanize_provider_error
    from personalclaw.sdk.model import ProviderResolutionError

    sentence = (
        "Your AWS credentials don't have permission to run amazon.nova-pro-v1:0: add "
        "bedrock:InvokeModelWithResponseStream to their IAM policy, or pick a different model."
    )
    assert humanize_provider_error(ProviderResolutionError(sentence)) == sentence


async def test_a_failure_clause_reads_inside_a_sentence():
    from personalclaw.llm_helpers import failure_clause

    assert failure_clause(RuntimeError(OVERLOADED)) == OVERLOADED_CLAUSE
    assert failure_clause(RuntimeError("HTTP 500")) == "HTTP 500", "not lowered: not a sentence"
    assert failure_clause(asyncio.TimeoutError()) == (
        "the model provider did not answer in time, so the request timed out"
    )
