"""A turn's usage is priced by the model that answered it, and a room member whose model fails
before it replies is answered by the next model in its chain, the way a chat turn is (#3687).

Every provider here is a fake behind the real registry, the real ``_build_native_runtime``, the
real native loop, the real ``run_chat`` and the real ``run_member_turn``: only the models are
scripted. The two models are priced very differently on purpose (``claude-opus-4.8`` and
``gpt-4o``), so a row priced by the wrong one cannot pass by accident.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import personalclaw.agents.native.runtime as runtime_mod
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry

pytestmark = pytest.mark.asyncio

DOWN, DOWN_REF = "down-oai", "down-oai:claude-opus-4.8"
UP, UP_REF = "up-oai", "up-oai:gpt-4o"
ALSO_DOWN, ALSO_DOWN_REF = "also-down", "also-down:gpt-4o-mini"

TOKENS_IN, TOKENS_OUT = 100_000, 10_000
OVERLOADED = "Error code: 529 - {'type': 'error', 'error': {'type': 'overloaded_error'}}"
OVERLOADED_CLAUSE = "the model provider is rate-limiting or overloaded right now"
REFUSED = "connection refused by the model host"


def _price(model: str) -> float:
    """What the shipped table's row for *model* bills the call's tokens at."""
    from personalclaw.pricing import price_row

    row = price_row(model)
    assert row is not None, f"premise: the shipped table prices {model}"
    return round((TOKENS_IN * row.fields["in"] + TOKENS_OUT * row.fields["out"]) / 1e6, 6)


class _Scripted:
    """A provider for one registry entry: answers with who it is, or fails as told to. It uses
    tools, as a subagent's model must: one that can't is refused before it runs."""

    supports_tools = True

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
        self.world.calls.append(self.entry)
        failure = self.world.failures.get(self.entry)
        if failure is not None:
            raise failure
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=f"answered by {self.entry}:{kw.get('model')}")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=TOKENS_IN, output_tokens=TOKENS_OUT)


class _World:
    def __init__(self) -> None:
        self.failures: dict[str, BaseException] = {}
        self.calls: list[str] = []
        self.active: dict[str, list[str]] = {}


@pytest.fixture
def world(monkeypatch) -> _World:
    w = _World()
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **kwargs: Any):
        return _Scripted(entry.name, str(kwargs.get("model") or entry.model), w)

    registry.register_type(
        ProviderCapability(
            type="scripted",
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=200_000,
        ),
        _factory,
    )
    for name, ref in ((DOWN, DOWN_REF), (UP, UP_REF), (ALSO_DOWN, ALSO_DOWN_REF)):
        registry.register_entry(ProviderEntry(name=name, type="scripted", model=ref.split(":")[1]))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr("personalclaw.providers.use_cases.load_active_models", lambda: w.active)
    monkeypatch.setattr(runtime_mod, "_INFERENCE_RETRY_BACKOFF_SECS", 0.0)
    return w


async def _runtime(*, session_key: str, agent: str | None = None, pick: str = ""):
    from personalclaw.providers import provider_bridge

    rt = provider_bridge._build_native_runtime(
        use_case="chat",
        session_key=session_key,
        agent=agent,
        model_override=pick or None,
        cwd=None,
    )
    await rt.start()
    return rt


async def _chat_turn(rt, tmp_path, *, pick: str = ""):
    """One real ``run_chat`` turn on ``rt``, for a chat whose own pick is ``pick``."""
    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import DashboardState, _ChatSession
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult

    rt.set_approval_policy("auto")
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
    session = _ChatSession("chat-priced", model=pick)
    session._trust = True
    session.append("user", "Name a colour.", "msg msg-u")
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "Name a colour.")
    (reply,) = [m for m in session.messages if m.get("role") == "assistant"]
    return reply


def _usage_by_model() -> list[tuple[str, float, bool]]:
    """What Settings → Usage lists: one row per model, with its cost and whether it is priced."""
    from personalclaw.usage_ledger import rollup

    return [(r["model"], r["cost_usd"], r["priced"]) for r in rollup(group_by="model")]


# ── the usage row follows the model that answered ──


@pytest.mark.parametrize(
    "pick",
    ["claude-opus-4.8", DOWN_REF],
    ids=["the pick as a bare id", "the pick as the composer writes it"],
)
async def test_a_chat_turn_that_fell_back_is_priced_by_the_model_that_answered(
    world, tmp_path, pick
):
    """🔴 Red on main: the row was written for the chat's pick. A bare pick priced the turn at
    Opus rates ($2.25) though gpt-4o answered it for $0.35, and the composer's ref form priced
    nothing, because that ref is no price-table key."""
    world.active["chat"] = [DOWN_REF, UP_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    rt = await _runtime(session_key="dashboard:chat-priced", pick=pick)

    reply = await _chat_turn(rt, tmp_path, pick=pick)

    assert reply["content"] == f"answered by {UP_REF}", "the next model answered"
    assert _usage_by_model() == [("gpt-4o", _price("gpt-4o"), True)]
    telemetry = reply["meta"]["turn_telemetry"]
    assert (telemetry["model"], telemetry["cost_usd"], telemetry["priced"]) == (
        "gpt-4o",
        _price("gpt-4o"),
        True,
    )
    assert f"${_price('gpt-4o'):.4f}" in telemetry["line"], telemetry["line"]


async def test_a_chat_on_auto_is_priced_by_the_model_that_served_it(world, tmp_path):
    """🔴 Red on main: a chat left on Auto has no pick, and the row was written for the pick, so
    every one of its turns recorded no model and read "unpriced"."""
    world.active["chat"] = [UP_REF]
    rt = await _runtime(session_key="dashboard:chat-priced")

    reply = await _chat_turn(rt, tmp_path)

    assert reply["content"] == f"answered by {UP_REF}"
    assert _usage_by_model() == [("gpt-4o", _price("gpt-4o"), True)]
    assert reply["meta"]["turn_telemetry"]["model"] == "gpt-4o"


async def test_a_subagent_with_no_model_of_its_own_is_priced_by_the_model_that_ran_it(world):
    """🔴 Red on main: a spawn that named no model was priced by the model it named, none, so its
    row read "unpriced" and its run's budget was charged nothing for a gpt-4o turn."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.subagent import SubagentManager

    world.active["chat"] = [UP_REF]
    rt = await _runtime(session_key="subagent:priced")
    sessions = _mock_sessions()
    sessions.get_or_create = AsyncMock(return_value=(rt, True, False))
    manager = SubagentManager(
        sessions=sessions, ctx_builder=_mock_ctx_builder(), is_yolo=lambda: True
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        info = manager.spawn("Name a colour.", parent_session_key="dashboard:parent")
        assert info is not None and not info.model, "a spawn with no model of its own"
        await manager._tasks[info.id]

    assert info.cost_usd == _price("gpt-4o")
    assert _usage_by_model() == [("gpt-4o", _price("gpt-4o"), True)]


#: A rate the owner sets for the model that answers, far from the shipped table's gpt-4o row.
_OWNER_RATE = {"in_per_mtok": 1.0, "out_per_mtok": 2.0}
_OWNER_COST = round((TOKENS_IN * 1.0 + TOKENS_OUT * 2.0) / 1_000_000, 6)


def _owner_prices(ref: str) -> None:
    from personalclaw.routing import rates

    rates._overlay_cache = None
    rates.save_overlay({ref: dict(_OWNER_RATE)})


async def test_a_chat_turn_is_priced_at_the_rate_the_owner_set(world, tmp_path):
    """🔴 Red before: the chat priced its turn from the shipped table alone, so the rate the owner
    set in Settings → Usage → Model prices reached neither the turn's cost line nor its usage
    row."""
    world.active["chat"] = [UP_REF]
    _owner_prices(UP_REF)
    assert _OWNER_COST != _price("gpt-4o"), "premise: the two rates differ"
    rt = await _runtime(session_key="dashboard:chat-priced")

    reply = await _chat_turn(rt, tmp_path)

    assert _usage_by_model() == [("gpt-4o", _OWNER_COST, True)]
    assert reply["meta"]["turn_telemetry"]["cost_usd"] == _OWNER_COST


async def test_a_subagent_is_priced_at_the_rate_the_owner_set(world):
    """🔴 Red before: a child's cost, which its fan-out's run budget is charged, came from the
    shipped table whatever rate the owner set."""
    from test_subagent import _mock_ctx_builder, _mock_sessions

    from personalclaw.subagent import SubagentManager

    world.active["chat"] = [UP_REF]
    _owner_prices(UP_REF)
    rt = await _runtime(session_key="subagent:priced")
    sessions = _mock_sessions()
    sessions.get_or_create = AsyncMock(return_value=(rt, True, False))
    manager = SubagentManager(
        sessions=sessions, ctx_builder=_mock_ctx_builder(), is_yolo=lambda: True
    )
    with patch("personalclaw.subagent.Stats"), patch("personalclaw.subagent.sel"):
        info = manager.spawn("Name a colour.", parent_session_key="dashboard:parent")
        assert info is not None
        await manager._tasks[info.id]

    assert info.cost_usd == _OWNER_COST
    assert _usage_by_model() == [("gpt-4o", _OWNER_COST, True)]


async def test_every_write_site_records_the_model_the_event_names():
    """🔴 Red on main: the ledger's one seam wrote whatever model its caller passed, so a
    heartbeat or a cron fire on the native loop, whose caller reads no model, read "unpriced"."""
    from types import SimpleNamespace

    from personalclaw.usage_ledger import record_from_event

    done = SimpleNamespace(
        input_tokens=TOKENS_IN, output_tokens=TOKENS_OUT, cost_usd=0.0, served_model_ref=UP_REF
    )
    record_from_event(done, source="background", model="")

    assert _usage_by_model() == [("gpt-4o", _price("gpt-4o"), True)]


async def test_the_terminal_event_names_the_model_that_answered(world):
    """The native loop says which model answered on the event that carries the turn's usage: a
    turn that fell back names the fallback, and the next turn, back on its own model, names that."""
    world.active["chat"] = [DOWN_REF, UP_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    rt = await _runtime(session_key="dashboard:named")

    rt.announce_failover()
    (done,) = [ev async for ev in rt.stream("Name a colour.") if ev.kind == EVENT_COMPLETE]
    assert done.served_model_ref == UP_REF

    world.failures.clear()
    (done,) = [ev async for ev in rt.stream("And another?") if ev.kind == EVENT_COMPLETE]
    assert done.served_model_ref == DOWN_REF


# ── a room member falls back down its chain, and the room says so ──


@pytest.fixture
def room_config(monkeypatch):
    """Rooms on, and one agent, ``critic``, pinned to the model that is about to fail."""
    from personalclaw.config.loader import AgentProfile, AppConfig

    cfg = AppConfig()
    cfg.rooms.enabled = True
    cfg.agents = {"critic": AgentProfile(model=DOWN_REF)}
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls, *a, **k: cfg))
    return cfg


class _MemberSessions:
    """The one ``SessionManager`` surface a room turn uses, handing out real native runtimes."""

    def __init__(self) -> None:
        self.runtimes: dict[str, Any] = {}
        self.released: list[str] = []

    async def get_or_create(self, key: str, agent: str | None = None, **kwargs: Any):
        if key in self.runtimes:
            return self.runtimes[key], False, False
        self.runtimes[key] = await _runtime(session_key=key, agent=agent)
        return self.runtimes[key], True, False

    def release(self, key: str) -> None:
        self.released.append(key)


def _transcript(room_id: str) -> list[tuple[str, str, str]]:
    from personalclaw.rooms.store import read_messages

    return [(m["role"], m.get("speaker", ""), m["content"]) for m in read_messages(room_id)]


async def test_a_room_member_whose_model_fails_is_answered_by_the_next_and_the_room_says_so(
    world, room_config
):
    """🔴 Red on main: the member's turn ended in its model's error, with gpt-4o configured after
    it. The room now says which model answered, in the member's slot and before the reply."""
    from personalclaw.rooms import store, turn

    world.active["chat"] = [UP_REF, DOWN_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    room = store.create_room("Failover")
    store.add_member(room.id, "critic")

    try:
        reply = await turn.run_member_turn(_MemberSessions(), room.id, "critic")
    except Exception as exc:  # noqa: BLE001 — the defect IS the raise
        pytest.fail(f"the member's turn failed instead of falling back to {UP_REF}: {exc!r}")

    assert reply == f"answered by {UP_REF}"
    assert _transcript(room.id) == [
        (
            store.ROOM_NOTE_ROLE,
            "critic",
            f"Ran on {UP_REF} instead of critic's model {DOWN_REF}: it failed before it replied "
            f"({OVERLOADED_CLAUSE}).",
        ),
        ("assistant", "critic", f"answered by {UP_REF}"),
    ]
    assert world.calls == [DOWN, DOWN, UP], "one retry, then the next model"


async def test_when_every_model_a_member_can_run_on_fails_the_room_names_each_one(
    world, room_config
):
    """🔴 Red on main: the room said only the first model's error. The sentence it says now is a
    room's: a member's models are its agent's, changed on the Agents page, not "this chat's"."""
    from personalclaw.rooms import store, turn

    world.active["chat"] = [ALSO_DOWN_REF, DOWN_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    world.failures[ALSO_DOWN] = RuntimeError(REFUSED)
    room = store.create_room("Nobody answers")
    store.add_member(room.id, "critic")

    with pytest.raises(Exception) as failed:
        await turn.run_member_turn(_MemberSessions(), room.id, "critic")
    turn.note_failed_turn(room.id, "critic", failed.value)

    assert _transcript(room.id) == [
        (
            store.ROOM_NOTE_ROLE,
            "critic",
            f"critic could not take its turn. None of critic's models answered: {DOWN_REF} failed "
            f"before it replied ({OVERLOADED_CLAUSE}), and so did {ALSO_DOWN_REF} ({REFUSED}). "
            "Try again in a moment, or give the critic agent a different model on the Agents "
            "page.",
        )
    ]


async def test_a_caller_that_cannot_show_the_line_still_keeps_its_failure(world):
    """The control: ``stream_and_collect`` without a place to show the line (a cron fire, a
    heartbeat) does not fall back, since the reply would read as the chosen model's."""
    from personalclaw.llm_helpers import stream_and_collect

    world.active["chat"] = [DOWN_REF, UP_REF]
    world.failures[DOWN] = RuntimeError(OVERLOADED)
    rt = await _runtime(session_key="dashboard:keeps-its-failure")
    assert rt.failover is not None, "the runtime could fall back; only the caller decides"

    with pytest.raises(RuntimeError):
        await stream_and_collect(rt, "Name a colour.")
    assert UP not in world.calls
