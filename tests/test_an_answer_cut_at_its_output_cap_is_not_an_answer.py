"""An answer its model stopped at its output cap is not an answer, and nothing reads it as one.

Measured on the local-first chain (Background on a model on this machine with the 4,096-token
Background output limit, a cloud model after it): two of a day's eleven memory consolidations
stopped at that cap. The cut JSON each wrote still held one whole object from inside it (a fact),
the chore's JSON check took that object as the answer, and the consolidation kept nothing from it
while the chat was marked consolidated. The next model of the chain was never asked, nothing was
owed, and nothing said so: what those chats said was never kept.

A one-shot call now fails on a capped answer as on any other failed call, so the chain's next model
is asked; a consolidation keeps only an answer that holds a key it asked for; and one no model could
finish is owed, tried again later, and said in a notice. These tests drive the real chores, the real
guard and the real chain walk over scripted models; no real model is called.
"""

from __future__ import annotations

import asyncio
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
#: The Background output limit a chore is held to (``background.max_output_tokens``).
CAP = 4_096
KEY = "dashboard:chat-10"
TITLE = "Asking Aiko to co-present"

#: What the local model wrote before the cap: the start of a consolidation, whose own object never
#: closed, and inside it a whole lesson object.
CUT_CONSOLIDATION = (
    '{"history_entry": "Noor asked what she meant to ask Aiko about the talk.", '
    '"lessons": [{"rule": "Spell the co-maintainer as Aiko", "negative": "Ico", '
    '"category": "knowledge"}], "preferences_update": "# User Preferences\\n\\n- Talk notes are'
)
KEPT_ENTRY = "Noor meant to ask Aiko whether she wants to co-present the Postgres part."
WHOLE_CONSOLIDATION = f'{{"history_entry": "{KEPT_ENTRY}", "lessons": []}}'


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime_mod, "_INFERENCE_RETRY_BACKOFF_SECS", 0.0)
    rates_mod._overlay_cache = None
    yield tmp_path
    rates_mod._overlay_cache = None


def cut(text: str) -> tuple[str, str]:
    """An answer its model stopped at the output cap, as Ollama ends one (``done_reason``)."""
    return text, "length"


def whole(text: str) -> tuple[str, str]:
    """An answer its model finished."""
    return text, "stop"


class _World:
    """Each entry's answers, one per call (the last repeats), and every call that reached a model,
    in order (``asked``). An entry in ``down`` refuses the connection."""

    def __init__(self) -> None:
        self.script: dict[str, list[tuple[str, str]]] = {}
        self.down: set[str] = set()
        self.asked: list[str] = []

    def answer(self, name: str) -> tuple[str, str]:
        turns = self.script[name]
        return turns.pop(0) if len(turns) > 1 else turns[0]


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
        text, stop = self.world.answer(self.name)
        if text:
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=text)
        yield LLMEvent(
            kind=EVENT_COMPLETE,
            stop_reason=stop,
            input_tokens=1_600,
            output_tokens=CAP if stop == "length" else 60,
        )

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


def _chain(monkeypatch, *background: str) -> None:
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_active_models",
        lambda: {"chat": [RELAY_REF], "background": list(background)},
    )


@pytest.fixture
def world(monkeypatch) -> _World:
    """The plan's local-first Background chain: a model on this machine, then a cloud model."""
    w = _World()
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **_kw: Any):
        return _Model(entry.name, w)

    registry.register_type(_capability("local-fake"), _factory)
    registry.register_type(_capability("cloud-fake"), _factory)
    registry.register_entry(ProviderEntry(name=HERE, type="local-fake", model="tiny"))
    registry.register_entry(ProviderEntry(name=RELAY, type="cloud-fake", model="swift"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    _chain(monkeypatch, HERE_REF, RELAY_REF)
    return w


@pytest.fixture
def notices(monkeypatch) -> list[tuple[str, str, str]]:
    """Every notice the gateway's dashboard was given, as ``(kind, title, body)``."""
    said: list[tuple[str, str, str]] = []
    state = SimpleNamespace(
        notify=lambda kind, title, body, **_kw: said.append((kind, title, body))
    )
    monkeypatch.setattr(
        "personalclaw.inbox_providers.native_source.get_dashboard_state", lambda: state
    )
    return said


@pytest.fixture
def home(tmp_path, monkeypatch) -> SimpleNamespace:
    """One chat with an exchange to consolidate, the memory it keeps, and its seals."""
    from personalclaw.history import ConversationLog, HistoryConsolidator
    from personalclaw.memory import MemoryStore
    from personalclaw.memory_service import MemoryService

    log = ConversationLog(base_dir=tmp_path / "sessions")
    log.init()
    log.append(KEY, "user", "What did I say I'd ask Aiko?")
    log.append(KEY, "assistant", "Whether she wants to co-present the Postgres part of the talk.")
    log.set_title(KEY, TITLE)
    memory = MemoryStore(workspace=tmp_path / "memory")
    memory.init()
    sealed: list[int] = []
    monkeypatch.setattr(
        MemoryService,
        "seal_session",
        lambda _self, k: sealed.append(log.unconsolidated_count(k)) or 0,
    )
    return SimpleNamespace(
        log=log,
        memory=memory,
        sealed=sealed,
        consolidator=HistoryConsolidator(log=log, memory=memory),
    )


async def _settles(done: Callable[[], bool]) -> None:
    """Give the heartbeat's pass over owed chores, which it does not wait for, time to run."""
    for _ in range(200):
        if done():
            return
        await asyncio.sleep(0.01)


# ── the one rule, where every chore is asked ───────────────────────────────────────────────────


def test_an_answer_cut_at_its_output_cap_is_asked_of_the_next_model(world, caplog):
    """🔴 Before: the cut answer was the call's answer, and the next model was never asked."""
    from personalclaw.llm_helpers import one_shot_completion

    world.script.update(
        here=[cut("The release checklist: tag, build, then")],
        relay=[whole("The release checklist: tag, build, publish.")],
    )
    with caplog.at_level(logging.WARNING, logger="personalclaw.llm_helpers"):
        answer = asyncio.run(one_shot_completion("List the release steps.", use_case="background"))

    assert answer == "The release checklist: tag, build, publish."
    assert world.asked == [HERE, RELAY]
    said = [r.getMessage() for r in caplog.records if r.name == "personalclaw.llm_helpers"]
    assert said == [
        f"one_shot chain advance: background entry 0 ({HERE_REF}) failed (OutOfOutputRoom) — "
        "trying next"
    ], said


def test_the_call_log_records_the_cut_answer_no_one_shot_call_used_as_failed(world):
    """🔴 Before: ``output_cap``, passed, for a call whose answer nothing used, so the model read as
    healthy. A turn of the native loop still records a cut reply that wrote text as passed: the
    chat shows that reply, marked cut."""
    from personalclaw.guardrails import audit
    from personalclaw.llm_helpers import one_shot_completion

    world.script.update(
        here=[cut("The release checklist: tag, build, then")],
        relay=[whole("The release checklist: tag, build, publish.")],
    )
    asyncio.run(one_shot_completion("List the release steps.", use_case="background"))

    rows = [
        (r["model"], r["failure_mode"], r["passed"], r["tokens_out"]) for r in audit.read_recent()
    ]
    assert rows == [("tiny", "output_cap", False, CAP), ("swift", "none", True, 60)], rows


def test_with_no_model_left_a_cut_answer_fails_the_call_and_names_the_cap(world, monkeypatch):
    """The one model is not asked again: the same request meets the same cap."""
    from personalclaw.guardrails.failure import OutOfOutputRoom
    from personalclaw.llm_helpers import one_shot_completion

    _chain(monkeypatch, HERE_REF)
    world.script.update(here=[cut("The release checklist: tag, build, then")])

    with pytest.raises(OutOfOutputRoom) as raised:
        asyncio.run(one_shot_completion("List the release steps.", use_case="background"))

    assert str(raised.value) == (
        f"{HERE_REF} ran out of output room before it finished its answer (4,096 tokens)"
    )
    assert world.asked == [HERE]


def test_a_chain_every_model_of_which_ran_out_of_room_says_what_each_did(world):
    """A model that spent its cap before it wrote a word is named for the cap too."""
    from personalclaw.llm_helpers import one_shot_completion, why_no_model_answered

    world.script.update(here=[cut("")], relay=[cut('{"history_entry": "Noor asked')])

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(one_shot_completion("Consolidate chat-10.", use_case="background"))

    assert why_no_model_answered(raised.value) == (
        f"{HERE_REF} failed before it replied (it ran out of output room before it answered "
        f"(4,096 tokens)), and so did {RELAY_REF} (it ran out of output room before it finished "
        "its answer (4,096 tokens))"
    )


def test_a_model_out_of_room_is_owed_as_one_that_did_not_answer():
    """A later try can get past it: the next answer may fit, and a raised limit gives it room. A
    chain one of whose models answered in the wrong shape still is not owed."""
    from personalclaw.guardrails.failure import OutOfOutputRoom, OutputContractError
    from personalclaw.llm_helpers import ChainExhausted
    from personalclaw.owed_chores import no_model_answered

    capped = OutOfOutputRoom(HERE_REF, output_cap=CAP, wrote=True)
    down = httpx.ConnectError("All connection attempts failed")
    misread = OutputContractError("dict", "Sure!", why="no JSON object")

    assert no_model_answered(capped)
    assert no_model_answered(
        ChainExhausted("every model failed", [(HERE_REF, capped), (RELAY_REF, down)])
    )
    assert not no_model_answered(
        ChainExhausted("every model failed", [(HERE_REF, capped), (RELAY_REF, misread)])
    )


# ── a consolidation ────────────────────────────────────────────────────────────────────────────


def test_a_consolidation_cut_at_its_output_cap_is_kept_from_the_next_model(world, home, notices):
    """🔴 The measured run: the cut answer's lesson object was read as the answer, nothing was
    kept, the chat read consolidated, and the cloud model after the local one was never asked."""
    world.script.update(here=[cut(CUT_CONSOLIDATION)], relay=[whole(WHOLE_CONSOLIDATION)])

    asyncio.run(home.consolidator.consolidate_session(KEY))

    assert world.asked == [HERE, RELAY]
    assert KEPT_ENTRY in home.memory.read_history()
    assert home.log.unconsolidated_count(KEY) == 0
    assert home.sealed == [0], "sealed once, after its messages were consolidated"
    assert notices == [], "a consolidation the next model finished asks nothing of her"


def test_a_consolidation_no_model_could_finish_is_owed_said_and_kept_once_one_does(
    world, home, notices, monkeypatch
):
    """🔴 Before: marked consolidated with nothing kept, owed nowhere, said nowhere. Now its
    messages stay to consolidate, she is told why and what gives it room, and it is tried again
    on the heartbeat: once a model finishes, the chat is consolidated and then sealed."""
    from personalclaw import owed_chores
    from personalclaw.heartbeat import HeartbeatService

    _chain(monkeypatch, HERE_REF)
    world.script.update(here=[cut(CUT_CONSOLIDATION), whole(WHOLE_CONSOLIDATION)])

    async def scenario() -> None:
        await home.consolidator.consolidate_session(KEY)
        assert home.log.unconsolidated_count(KEY) == 2, "nothing is marked consolidated"
        assert home.memory.read_history() == "", "and nothing from the cut answer is kept"
        assert owed_chores.owed() == [f"consolidation:{KEY}"]
        assert home.sealed == [], "the session waits for its consolidation to be sealed"
        await HeartbeatService(consolidator=home.consolidator)._beat()
        await _settles(lambda: bool(home.sealed))

    asyncio.run(scenario())

    assert world.asked == [HERE, HERE], "asked once, then again on the heartbeat"
    assert KEPT_ENTRY in home.memory.read_history()
    assert home.log.unconsolidated_count(KEY) == 0
    assert home.sealed == [0]
    assert owed_chores.owed() == []
    assert notices == [
        (
            "warning",
            "Memory from a chat isn't saved yet",
            f"PersonalClaw couldn't save what it learned from “{TITLE}” to memory: {HERE_REF} "
            "ran out of output room before it finished its answer (4,096 tokens). It tries again "
            "later. To give it room, raise the Background output limit in Settings → Models.",
        )
    ], "said once, when it was first owed"


def test_a_notice_for_a_chain_says_no_other_model_finished_it(world, home, notices):
    world.script.update(here=[cut(CUT_CONSOLIDATION)])
    world.down.add(RELAY)

    asyncio.run(home.consolidator.consolidate_session(KEY))

    ((_kind, _title, body),) = notices
    assert body.startswith(
        f"PersonalClaw couldn't save what it learned from “{TITLE}” to memory: {HERE_REF} ran out "
        "of output room before it finished its answer (4,096 tokens), and no other Background "
        "model finished it. It tries again later."
    ), body
    assert home.log.unconsolidated_count(KEY) == 2


def test_a_model_whose_own_limit_is_the_smaller_is_named_as_the_one_to_raise(
    world, home, notices, monkeypatch
):
    """Raising the Background output limit gives no room to a model whose own limit is lower."""
    from personalclaw.config.loader import BackgroundConfig

    monkeypatch.setattr(
        "personalclaw.config.loader.background_limits",
        lambda: BackgroundConfig(max_output_tokens=8_192),
    )
    _chain(monkeypatch, HERE_REF)
    world.script.update(here=[cut(CUT_CONSOLIDATION)])

    asyncio.run(home.consolidator.consolidate_session(KEY))

    ((_kind, _title, body),) = notices
    assert body.endswith(
        "To give it room, raise that model's own output limit where its provider's settings have "
        "one, or add another model to Background in Settings → Models."
    ), body


def test_an_answer_holding_no_key_the_consolidation_asked_for_is_not_its_answer(
    world, home, notices
):
    """🔴 A whole answer whose own object does not parse (a quote left unescaped) still holds the
    whole lesson object inside it, which was read as the answer: nothing kept, chat marked."""
    unparsed = (
        '{"history_entry": "Noor asked about "the talk" and Aiko.", "lessons": [{"rule": '
        '"Spell the co-maintainer as Aiko", "negative": "Ico", "category": "knowledge"}]}'
    )
    world.script.update(here=[whole(unparsed)], relay=[whole(WHOLE_CONSOLIDATION)])

    asyncio.run(home.consolidator.consolidate_session(KEY))

    assert world.asked == [HERE, RELAY]
    assert KEPT_ENTRY in home.memory.read_history()
    assert home.log.unconsolidated_count(KEY) == 0


def test_a_whole_answer_that_found_nothing_to_keep_still_consolidates_the_chat(
    world, home, notices
):
    """The keys, empty: a finished answer that kept nothing is a pass, owed nothing and said
    nowhere."""
    from personalclaw import owed_chores

    world.script.update(here=[whole('{"history_entry": "", "lessons": []}')])

    asyncio.run(home.consolidator.consolidate_session(KEY))

    assert world.asked == [HERE]
    assert home.memory.read_history() == ""
    assert home.log.unconsolidated_count(KEY) == 0
    assert home.sealed == [0]
    assert owed_chores.owed() == []
    assert notices == []


def test_consolidate_now_that_no_model_could_finish_is_owed_and_said(
    world, home, notices, monkeypatch
):
    """🔴 Memory → Settings → Consolidate now answered "started" and the pass that followed kept
    nothing and marked the chat; it is owed and said as an idle chat's is."""
    from personalclaw import owed_chores
    from personalclaw.dashboard.handlers import memory as memory_handlers

    monkeypatch.setattr(memory_handlers, "_is_restricted_session", lambda _state, _req: False)
    _chain(monkeypatch, HERE_REF)
    world.script.update(here=[cut(CUT_CONSOLIDATION)])

    async def _body() -> dict:
        return {"key": KEY}

    request = SimpleNamespace(app={"state": SimpleNamespace(consolidator=home.consolidator)})
    request.json = _body

    async def scenario() -> int:
        response = await memory_handlers.api_memory_consolidate(request)
        await asyncio.gather(*list(home.consolidator._tasks), return_exceptions=True)
        return response.status

    assert asyncio.run(scenario()) == 200
    assert home.log.unconsolidated_count(KEY) == 2
    assert owed_chores.owed() == [f"consolidation:{KEY}"]
    assert [title for _kind, title, _body in notices] == ["Memory from a chat isn't saved yet"]


# ── the other chores ───────────────────────────────────────────────────────────────────────────


def _chat(key: str = "dashboard:chat-6") -> SimpleNamespace:
    return SimpleNamespace(
        key=key,
        title=key,
        _titled=False,
        blocks_reads=False,
        is_restricted=False,
        memory_mode="persistent",
        tags=["kept"],
        _dirty=False,
        messages=[
            {"role": "user", "content": "A packing list for a weekend hike"},
            {"role": "assistant", "content": "Boots, a rain jacket, water."},
        ],
    )


def _state(*chats: SimpleNamespace) -> SimpleNamespace:
    titles: list[tuple[str, str]] = []
    return SimpleNamespace(
        _sessions={c.key: c for c in chats},
        conversation_log=None,
        titles=titles,
        push_session_title=lambda key, title: titles.append((key, title)),
    )


def test_a_title_cut_at_its_output_cap_comes_from_the_next_model(world):
    """🔴 Before: the chat was titled with the words the cap cut."""
    from personalclaw.dashboard.chat_title import _maybe_auto_title

    chat = _chat()
    world.script.update(here=[cut("Hike packing li")], relay=[whole("Hike packing list")])

    asyncio.run(_maybe_auto_title(_state(chat), chat))

    assert chat.title == "Hike packing list"
    assert world.asked == [HERE, RELAY]


def test_follow_ups_cut_at_the_output_cap_come_from_the_next_model(world):
    """🔴 Before: a whole list ahead of the cut was read as the answer."""
    from personalclaw.dashboard.chat_followups import _generate_followups

    world.script.update(
        here=[cut('["Add a map?"]\n\nThese suggest the next steps for the')],
        relay=[whole('["Pack water?"]')],
    )

    assert asyncio.run(_generate_followups(_chat())) == ["Pack water?"]
    assert world.asked == [HERE, RELAY]


def test_a_thread_compression_cut_at_the_output_cap_comes_from_the_next_model(world):
    """🔴 Before: the cut summary was handed to the next prompt as the thread's history."""
    from personalclaw.context import compress_thread_history

    turns = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"Step {i}: " + "x" * 2_000}
        for i in range(30)
    ]
    world.script.update(
        here=[cut("They planned the hike route and")],
        relay=[whole("They planned the hike route and packed for rain.")],
    )

    compressed = asyncio.run(compress_thread_history(turns, "dashboard:chat-6", "the route"))

    assert compressed is not None
    assert "They planned the hike route and packed for rain." in compressed
    assert world.asked == [HERE, RELAY]


def test_a_prose_summary_cut_at_the_output_cap_comes_from_the_next_model(world):
    """🔴 Before: the background compressor stored the cut summary as the summary."""
    from personalclaw.tool_providers.prose_compress import compress_prose

    world.script.update(
        here=[cut("The deploy failed at the migration step because")],
        relay=[whole("The deploy failed at the migration step: a lock timeout.")],
    )

    summary = asyncio.run(compress_prose("deploy log line\n" * 400))

    assert summary == "The deploy failed at the migration step: a lock timeout."
    assert world.asked == [HERE, RELAY]
