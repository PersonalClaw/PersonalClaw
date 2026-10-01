"""A model whose breaker is open is a model that failed, and the next model of its chain answers.

Measured with the local background model taken down: its breaker opened after five failed calls,
and from then on every background chore (a new chat's title, two idle chats' consolidation, the
suggestions) failed with the open breaker while the cloud model after it in the chain, which had
just answered one of them, was never asked again. The background session keeps the model it was
built on, and its turn moved on from an error or a timeout but not from an open breaker. Nothing
tried the failed chores again either: the chat kept its key for a title, and the consolidations
never ran.

These tests drive the real background runtime, the real guard and the real chain walk over fake
models; no real model is called.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import personalclaw.agents.native.runtime as runtime_mod
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.routing import rates as rates_mod

HERE, HERE_REF = "here", "here:tiny"
RELAY, RELAY_REF = "relay", "relay:swift"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime_mod, "_INFERENCE_RETRY_BACKOFF_SECS", 0.0)
    rates_mod._overlay_cache = None
    yield tmp_path
    rates_mod._overlay_cache = None


class _World:
    """What each entry answers (``answers``), which entries are down (``down``), and every call
    that reached a model, in order (``asked``)."""

    def __init__(self) -> None:
        self.answers: dict[str, str] = {}
        self.down: set[str] = set()
        self.asked: list[str] = []


class _Model:
    supports_tools = False

    def __init__(self, name: str, world: _World) -> None:
        self.name = name
        self.world = world

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **_kw: Any):
        self.world.asked.append(self.name)
        if self.name in self.world.down:
            raise httpx.ConnectError("All connection attempts failed")
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=self.world.answers[self.name])
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=900, output_tokens=40)

    async def stream(self, message: str):
        async for event in self.complete([{"role": "user", "content": message}]):
            yield event


def _capability(type_: str) -> ProviderCapability:
    return ProviderCapability(
        type=type_,
        capabilities=frozenset({Capability.CHAT}),
        supports_streaming=True,
        supports_tools=False,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=32_768,
    )


@pytest.fixture
def world(monkeypatch) -> _World:
    """The Background chain: a model on this machine, then a cloud model after it."""
    w = _World()
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **_kw: Any):
        return _Model(entry.name, w)

    registry.register_type(_capability("local-fake"), _factory)
    registry.register_type(_capability("cloud-fake"), _factory)
    registry.register_entry(ProviderEntry(name=HERE, type="local-fake", model="tiny"))
    registry.register_entry(ProviderEntry(name=RELAY, type="cloud-fake", model="swift"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_active_models",
        lambda: {"chat": [RELAY_REF], "background": [HERE_REF, RELAY_REF]},
    )
    return w


def _open_breaker(name: str) -> None:
    from personalclaw.guardrails.breaker import get_breaker

    breaker = get_breaker(name)
    while not breaker.is_open():
        breaker.record_failure()


class _BackgroundSessions:
    """The session manager's background session: ONE runtime, built on first use and kept, as the
    gateway keeps it, so a breaker that opens later finds it built on the first model."""

    def __init__(self) -> None:
        self.runtime: Any = None

    async def get_or_create(self, key: str, agent: str | None = None, **_kw: Any):
        from personalclaw.agents.defaults import LITE_AGENT_NAME
        from personalclaw.providers import provider_bridge

        if self.runtime is None:
            self.runtime = provider_bridge._build_native_runtime(
                use_case="chat",
                session_key=key,
                agent=agent or LITE_AGENT_NAME,
                model_override=None,
                cwd=None,
                model_axis="background",
            )
            await self.runtime.start()
        return self.runtime, False, True

    def release(self, key: str) -> None:
        return None

    async def recycle_background(self) -> None:
        return None


async def _built_on_the_first_model(world: _World) -> _BackgroundSessions:
    sessions = _BackgroundSessions()
    runtime, _new, _resumed = await sessions.get_or_create("_bg")
    assert runtime.served_model_ref == HERE_REF, "built while the first model answered"
    return sessions


def _chat(key: str = "dashboard:chat-6") -> SimpleNamespace:
    return SimpleNamespace(
        key=key,
        title=key,
        _titled=False,
        blocks_reads=False,
        is_restricted=False,
        tags=["kept"],
        _dirty=False,
        messages=[
            {"role": "user", "content": "A packing list for a weekend hike"},
            {"role": "assistant", "content": "Boots, a rain jacket, water."},
        ],
    )


def _state(sessions: _BackgroundSessions, *chats: SimpleNamespace) -> SimpleNamespace:
    titles: list[tuple[str, str]] = []
    return SimpleNamespace(
        sessions=sessions,
        _sessions={c.key: c for c in chats},
        conversation_log=None,
        titles=titles,
        push_session_title=lambda key, title: titles.append((key, title)),
    )


# ── one rule: an open breaker moves the chain on, in every walk ─────────────────────────────────


def test_a_one_shot_call_whose_first_models_breaker_is_open_is_answered_by_the_next(world):
    """The one-shot chain walk already moved on from an open breaker; pinned beside the rest."""
    from personalclaw.llm_helpers import one_shot_completion

    world.answers.update(relay="Hello.")
    _open_breaker(HERE)

    answer = asyncio.run(one_shot_completion("Say hello.", use_case="background"))

    assert answer == "Hello."
    assert world.asked == [RELAY], "the paused model was never sent the call"


def test_a_chats_title_with_the_first_models_breaker_open_comes_from_the_next(world, caplog):
    """🔴 The measured run: "Auto-title failed … circuit breaker … OPEN", the next model unasked."""
    from personalclaw.dashboard.chat_title import _stream_background_prompt
    from personalclaw.session import chore_usage

    async def scenario() -> str:
        sessions = await _built_on_the_first_model(world)
        _open_breaker(HERE)
        return await _stream_background_prompt(
            _state(sessions), "Title this chat.", usage=chore_usage("dashboard:chat-6")
        )

    world.answers.update(relay="Hike packing list")
    with caplog.at_level(logging.WARNING, logger="personalclaw.llm_helpers"):
        text = asyncio.run(scenario())

    assert text == "Hike packing list"
    assert world.asked == [RELAY]
    said = [r.getMessage() for r in caplog.records if r.name == "personalclaw.llm_helpers"]
    assert said == [
        f"Background chat chore: Ran on {RELAY_REF} instead of {HERE_REF}: it failed before it "
        f"replied (calls to '{HERE}' are paused after it failed repeatedly)."
    ], said


def test_a_consolidation_with_the_first_models_breaker_open_comes_from_the_next(world, tmp_path):
    """🔴 The measured run: "LLM consolidation call failed: circuit breaker … OPEN", twice."""
    from personalclaw.history import ConversationLog, HistoryConsolidator

    async def scenario():
        sessions = await _built_on_the_first_model(world)
        _open_breaker(HERE)
        consolidator = HistoryConsolidator(
            ConversationLog(tmp_path / "log"), memory=None, sessions=sessions
        )
        return await consolidator._call_llm("Consolidate this chat.", "dashboard:chat-3")

    world.answers.update(relay='{"history_entry": "Packed for the hike."}')
    assert asyncio.run(scenario()) == {"history_entry": "Packed for the hike."}
    assert world.asked == [RELAY]


def test_suggestions_with_the_first_models_breaker_open_come_from_the_next(world, monkeypatch):
    """🔴 The measured run: "Suggestions generation failed", a traceback each time."""
    from personalclaw import suggestions

    monkeypatch.setattr(
        suggestions, "_build_context", lambda _state: "## Recent chats\n" + "x" * 80
    )
    monkeypatch.setattr(
        "personalclaw.prompt_providers.runtime.render_use_case_prompt", lambda *_a, **_k: "Suggest."
    )

    async def scenario() -> list[str]:
        sessions = await _built_on_the_first_model(world)
        _open_breaker(HERE)
        return await suggestions.generate_suggestions(_state(sessions))

    world.answers.update(relay='["Plan the route", "Check the weather"]')
    assert asyncio.run(scenario()) == ["Plan the route", "Check the weather"]
    assert world.asked == [RELAY]


def test_follow_up_chips_with_the_first_models_breaker_open_come_from_the_next(world):
    from personalclaw.dashboard.chat_followups import _generate_followups

    async def scenario() -> list[str]:
        sessions = await _built_on_the_first_model(world)
        _open_breaker(HERE)
        return await _generate_followups(_state(sessions), _chat())

    world.answers.update(relay='["Add a map?"]')
    assert asyncio.run(scenario()) == ["Add a map?"]
    assert world.asked == [RELAY]


def test_a_chore_that_stops_reading_at_its_last_event_leaves_the_next_chore_its_fallback(world):
    """🔴 Found driving this: a chat's title fell back to the next model, then its follow-ups on
    the same background session failed on the model that was down. The title stopped reading at
    its last event, so its turn was closed later, when the generator was collected, and that late
    cleanup took the leave to fall back the follow-ups had just been given."""
    from personalclaw.llm.base import EVENT_COMPLETE as COMPLETE
    from personalclaw.llm_helpers import any_answer, let_fail_over

    world.down.add(HERE)
    world.answers.update(relay="An answer.")

    async def scenario() -> list[str]:
        sessions = await _built_on_the_first_model(world)
        runtime = sessions.runtime
        let_fail_over(runtime, any_answer)
        first = runtime.stream("Title this chat.")
        async for event in first:
            if event.kind == COMPLETE:
                break
        let_fail_over(runtime, any_answer)
        await first.aclose()  # the title's turn is closed only now, as the collector would
        texts = []
        async for event in runtime.stream("Suggest follow-ups."):
            if event.kind == EVENT_TEXT_CHUNK:
                texts.append(event.text)
        return texts

    assert asyncio.run(scenario()) == ["An answer."]
    assert world.asked == [HERE, HERE, RELAY, HERE, HERE, RELAY], "each chore asks its own first"


# ── the same rule for the other two failures a chore reads ──────────────────────────────────────


def test_a_consolidation_answered_without_json_comes_from_the_next_model(world, tmp_path):
    from personalclaw.history import ConversationLog, HistoryConsolidator

    async def scenario():
        sessions = await _built_on_the_first_model(world)
        consolidator = HistoryConsolidator(
            ConversationLog(tmp_path / "log"), memory=None, sessions=sessions
        )
        return await consolidator._call_llm("Consolidate this chat.", "dashboard:chat-3")

    world.answers.update(
        here="The chat was about packing for a hike.",
        relay='{"history_entry": "Packed for the hike."}',
    )
    assert asyncio.run(scenario()) == {"history_entry": "Packed for the hike."}
    assert world.asked == [HERE, RELAY], "the model that missed is not asked twice"


def test_a_title_answered_with_nothing_comes_from_the_next_model(world):
    from personalclaw.dashboard.chat_title import _maybe_auto_title

    chat = _chat()

    async def scenario() -> None:
        sessions = await _built_on_the_first_model(world)
        await _maybe_auto_title(_state(sessions, chat), chat)

    world.answers.update(here="", relay="Hike packing list")
    asyncio.run(scenario())

    assert chat.title == "Hike packing list"
    assert world.asked == [HERE, RELAY]


def test_with_no_next_model_a_chore_reads_the_answer_it_got(world, monkeypatch, tmp_path):
    """A check hands a missed answer on only when another model is there to answer: the last
    model's answer reaches the chore, which reads it as it always did."""
    from personalclaw.history import ConversationLog, HistoryConsolidator

    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_active_models",
        lambda: {"chat": [RELAY_REF], "background": [HERE_REF]},
    )

    async def scenario():
        sessions = await _built_on_the_first_model(world)
        consolidator = HistoryConsolidator(
            ConversationLog(tmp_path / "log"), memory=None, sessions=sessions
        )
        return await consolidator._call_llm("Consolidate this chat.", "dashboard:chat-3")

    world.answers.update(here="The chat was about packing for a hike.")
    assert asyncio.run(scenario()) is None
    assert world.asked == [HERE]


# ── a chore no model answered is not lost ──────────────────────────────────────────────────────


async def _settles(done: Callable[[], bool]) -> None:
    """Give the heartbeat's pass over owed chores, which it does not wait for, time to run."""
    for _ in range(200):
        if done():
            return
        await asyncio.sleep(0.01)


def test_a_title_no_model_answered_is_asked_for_again_once_one_does(world):
    """🔴 The measured run: the chat kept its key for a title and nothing asked again."""
    from personalclaw.dashboard.chat_title import _maybe_auto_title
    from personalclaw.heartbeat import HeartbeatService

    chat = _chat()

    async def scenario(state) -> None:
        await _maybe_auto_title(state, chat)
        assert chat.title == chat.key, "no model answered: no title yet"
        world.down.clear()
        await HeartbeatService(consolidator=None)._beat()
        await _settles(lambda: chat.title != chat.key)

    world.down.update({HERE, RELAY})
    world.answers.update(here="Hike packing list")
    state = _state(_BackgroundSessions(), chat)
    asyncio.run(scenario(state))

    assert chat.title == "Hike packing list"
    assert state.titles == [(chat.key, "Hike packing list")]


def test_a_consolidation_no_model_answered_runs_once_one_does_and_seals_after(
    world, tmp_path, monkeypatch
):
    """🔴 The measured run: an idle chat's expiry consolidation failed, its offset stayed 0, and
    the session was sealed anyway. Now it is consolidated once a model answers, then sealed."""
    from personalclaw.heartbeat import HeartbeatService
    from personalclaw.history import ConversationLog, HistoryConsolidator
    from personalclaw.memory import MemoryStore
    from personalclaw.memory_service import MemoryService

    key = "dashboard:chat-3"
    log = ConversationLog(base_dir=tmp_path / "sessions")
    log.init()
    for role, text in (("user", "Pack for the hike"), ("assistant", "Boots and a rain jacket.")):
        log.append(key, role, text)
    memory = MemoryStore(workspace=tmp_path / "memory")
    memory.init()
    sealed: list[int] = []
    monkeypatch.setattr(
        MemoryService,
        "seal_session",
        lambda _self, k: sealed.append(log.unconsolidated_count(k)) or 0,
    )
    consolidator = HistoryConsolidator(log=log, memory=memory, sessions=_BackgroundSessions())

    async def scenario() -> None:
        await consolidator.consolidate_session(key)
        assert log.unconsolidated_count(key) == 2, "no model answered: nothing consolidated yet"
        assert sealed == [], "and the session waits for its consolidation to be sealed"
        world.down.clear()
        await HeartbeatService(consolidator=consolidator)._beat()
        await _settles(lambda: bool(sealed))

    world.down.update({HERE, RELAY})
    world.answers.update(here='{"history_entry": "Packed for the hike."}')
    asyncio.run(scenario())

    assert log.unconsolidated_count(key) == 0
    assert sealed == [0], "sealed once, after its messages were consolidated"


# ── what the log says ──────────────────────────────────────────────────────────────────────────


def test_a_suggestions_refresh_no_model_answered_is_one_warning_line(world, monkeypatch, caplog):
    """🔴 Measured: a ~60-line traceback at WARNING on every refresh. Now one line, with the
    traceback at debug."""
    from personalclaw import suggestions

    monkeypatch.setattr(
        suggestions, "_build_context", lambda _state: "## Recent chats\n" + "x" * 80
    )
    monkeypatch.setattr(
        "personalclaw.prompt_providers.runtime.render_use_case_prompt", lambda *_a, **_k: "Suggest."
    )
    world.down.update({HERE, RELAY})
    cache = suggestions.SuggestionsCache()

    with caplog.at_level(logging.DEBUG, logger="personalclaw.suggestions"):
        asyncio.run(suggestions.refresh_suggestions(_state(_BackgroundSessions()), cache))

    mine = [r for r in caplog.records if r.name == "personalclaw.suggestions"]
    warnings = [r for r in mine if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert warnings[0].exc_info is None, "no traceback at WARNING"
    assert warnings[0].getMessage() == (
        f"Suggestions generation failed: no model of its chain answered: {HERE_REF} failed before "
        f"it replied (couldn't connect to the model provider), and so did {RELAY_REF} (couldn't "
        "connect to the model provider)"
    )
    assert any(r.levelno == logging.DEBUG and r.exc_info for r in mine), "the traceback is at debug"


# ── when an owed chore is tried again ──────────────────────────────────────────────────────────


def test_an_owed_chore_waits_longer_after_each_failed_try_and_a_recovery_makes_it_due(world):
    from personalclaw import owed_chores
    from personalclaw.guardrails.breaker import get_breaker
    from personalclaw.llm_helpers import one_shot_completion

    tries: list[int] = []

    async def retry() -> bool:
        tries.append(len(tries))
        raise httpx.ConnectError("All connection attempts failed")

    owed_chores.owe("title:dashboard:chat-6", "the title of dashboard:chat-6", retry)
    asyncio.run(owed_chores.try_due())
    asyncio.run(owed_chores.try_due())
    assert tries == [0], "tried on the next heartbeat, then it waits"

    # The first model's breaker had opened and its pause is over: its next call is the probe,
    # and it answers.
    breaker = get_breaker(HERE, recovery_secs=0.0)
    while breaker.consecutive_failures < breaker.threshold:
        breaker.record_failure()
    world.answers.update(here="Hello.")
    asyncio.run(one_shot_completion("Say hello.", model=HERE_REF, use_case="background"))

    asyncio.run(owed_chores.try_due())
    assert tries == [0, 1], "a provider answering again makes every owed chore due"
    assert owed_chores.owed() == ["title:dashboard:chat-6"], "and one that fails again stays owed"


def test_an_owed_chore_that_fails_for_another_reason_is_said_and_settled(caplog):
    from personalclaw import owed_chores

    async def retry() -> bool:
        raise KeyError("messages")

    owed_chores.owe("consolidation:dashboard:chat-3", "the consolidation of chat-3", retry)
    with caplog.at_level(logging.WARNING, logger="personalclaw.owed_chores"):
        asyncio.run(owed_chores.try_due())

    assert owed_chores.owed() == []
    (said,) = [r for r in caplog.records if r.name == "personalclaw.owed_chores"]
    assert said.getMessage() == "Owed: gave up on the consolidation of chat-3: it failed again"
    assert said.exc_info is not None, "a defect keeps its traceback"


# ── every chore of the background session lets its turn move on ────────────────────────────────


@functools.cache
def _chore_sites() -> dict[str, set[str]]:
    """(file, function) of every function that takes the background session and streams it,
    and whether it lets the turn move on: ``let_fail_over(…)`` or ``on_substitution=``."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
    found: dict[str, set[str]] = {"moves_on": set(), "stays": set()}
    for path in sorted(root.rglob("*.py")):
        text = path.read_text("utf-8")
        if "BACKGROUND_KEY" not in text or "get_or_create" not in text:
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
            called = {
                c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", "")
                for c in calls
            }
            if "BACKGROUND_KEY" not in names or "get_or_create" not in called:
                continue
            streams = called & {"stream", "stream_and_collect", "stream_and_collect_json"}
            if not streams:
                continue
            moves_on = "let_fail_over" in called or any(
                k.arg == "on_substitution" for c in calls for k in c.keywords
            )
            site = f"{path.relative_to(root)}:{node.name}"
            found["moves_on" if moves_on else "stays"].add(site)
    return found


def test_every_chore_on_the_background_session_lets_its_turn_move_on():
    sites = _chore_sites()
    assert not sites["stays"], (
        "these stream the background session without letting its turn move on to the next model "
        "of the chain, so a first model that fails or is paused fails the chore: call "
        f"`llm_helpers.let_fail_over(client, check)` before streaming: {sorted(sites['stays'])}"
    )


def test_the_census_finds_the_chores_it_exists_for():
    """The floor: a scan that found nothing would pass for free."""
    found = _chore_sites()["moves_on"]
    for known in (
        "dashboard/chat_title.py:_stream_background_prompt",
        "suggestions.py:generate_suggestions",
        "dashboard/chat_followups.py:_generate_followups",
        "dashboard/chat_folders.py:_generate_folder_icon",
        "history.py:_call_llm",
        "context.py:compress_thread_history",
    ):
        assert known in found, (known, sorted(found))
