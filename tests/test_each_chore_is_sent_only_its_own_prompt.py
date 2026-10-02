"""Each background chore is sent only its own prompt, whatever chat or chore came before it.

Measured on a running install: every chore of every chat (a chat's title and tags, its organize
proposal, its follow-up chips, its memory consolidation) was sent on ONE long-lived background
conversation, kept by the session manager for the whole day. A local model's requests grew from 3
to 94 messages, each carrying every earlier chore's prompt and answer. A consolidation of one chat
was handed other chats' titles, organize questions, follow-ups and consolidations as if they were
its conversation, and it stored a name it read in another chat as the user's own. A chat's text
sent to a chore stayed in that conversation for every later chore of every chat to read.

The behaviour now: a chore is one fresh call on the Background chain, sent exactly its own prompt.
Nothing of an earlier chore reaches it, whichever chat that chore was made for.

These tests drive the real chores, the real chain walk, the real guard and the gateway's own session
manager over a scripted model that keeps a conversation the way a model provider's client does; no
real model is called.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

import personalclaw.agents.native.runtime as runtime_mod
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.routing import rates as rates_mod

ENTRY, REF = "relay", "relay:swift"
#: A model on this machine an Incognito chat can run on; no chain names it.
HERE, HERE_REF = "here", "here:tiny"

#: Each chore's prompt, by how it starts (the bundled prompts), and what the model answers it.
TITLE = "Generate a short title"
ORGANIZE = "Classify one chat into an existing organization scheme"
FOLLOW_UPS = "You are suggesting follow-up messages"
CONSOLIDATION = "You are a memory consolidation agent"

#: Two chats, each with words no other chat has.
WALK_KEY = "chat-1-1790000001"
WALK = (
    "Plan a walk to the old lighthouse at Point Avery on Saturday.",
    "Leave at nine: the causeway is dry from ten until one.",
)
WALK_WORDS = ("lighthouse", "Point Avery", "causeway")
BREAD_KEY = "chat-2-1790000002"
BREAD = (
    "My sourdough starter smells of nail varnish after its feed.",
    "Feed it twice a day for three days and keep it warmer.",
)
BREAD_WORDS = ("sourdough", "nail varnish")


class _World:
    """What the models are sent: every request, as the messages it carried, in order, and the
    entry of the model each went to."""

    def __init__(self) -> None:
        self.requests: list[list[dict[str, str]]] = []
        self.served: list[str] = []

    def answer(self, prompt: str) -> str:
        if prompt.startswith(TITLE):
            return ("Lighthouse walk plan" if "lighthouse" in prompt else "Sourdough rescue") + (
                "\nTAGS: none"
            )
        if prompt.startswith(ORGANIZE):
            return "NONE"
        if prompt.startswith(FOLLOW_UPS):
            return '["Check the tide table", "Pack a flask"]'
        if prompt.startswith(CONSOLIDATION):
            return '{"history_entry": "A plan was made."}'
        raise AssertionError(f"the scripted model was sent a prompt it does not know: {prompt!r}")

    def asked(self, head: str) -> list[list[dict[str, str]]]:
        """The requests whose newest message is a prompt starting with *head*."""
        return [r for r in self.requests if r and r[-1]["content"].startswith(head)]


class _Model:
    """A model as a provider's client holds one: a turn sent with ``stream`` carries this
    instance's whole conversation, a chat-completions client's ``messages``, and ``complete`` is
    sent the messages it is handed. So a call made on an instance an earlier call used is sent
    that call too, whichever way it is made."""

    supports_tools = False

    def __init__(self, world: _World, entry: str) -> None:
        self.world = world
        self.entry = entry
        self._history: list[dict[str, str]] = []

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def complete(self, messages: list[dict], **_kw: Any):
        sent = [{"role": str(m["role"]), "content": str(m["content"])} for m in messages]
        self.world.requests.append(sent)
        self.world.served.append(self.entry)
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=self.world.answer(sent[-1]["content"]))
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=900, output_tokens=40)

    async def stream(self, message: str):
        self._history.append({"role": "user", "content": message})
        said = ""
        async for event in self.complete(self._history):
            if event.kind == EVENT_TEXT_CHUNK:
                said += event.text
            yield event
        self._history.append({"role": "assistant", "content": said})


def _capability() -> ProviderCapability:
    return ProviderCapability(
        type="cloud-fake",
        capabilities=frozenset({Capability.CHAT}),
        supports_streaming=True,
        supports_tools=False,
        supports_embeddings=False,
        supports_vision=False,
        max_context_tokens=32_768,
    )


@pytest.fixture
def world(tmp_path, monkeypatch) -> Iterator[_World]:
    """The Background chain is one cloud model, and a model on this machine is set up beside it;
    every instance built of either records into one world."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime_mod, "_INFERENCE_RETRY_BACKOFF_SECS", 0.0)
    rates_mod._overlay_cache = None
    w = _World()
    registry = ProviderRegistry()
    registry.register_type(
        _capability(), lambda *, entry, session_key=None, **_kw: _Model(w, entry.name)
    )
    registry.register_entry(ProviderEntry(name=ENTRY, type="cloud-fake", model="swift"))
    registry.register_entry(ProviderEntry(name=HERE, type="cloud-fake", model="tiny"))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_active_models",
        lambda: {"chat": [REF], "background": [REF]},
    )
    yield w
    rates_mod._overlay_cache = None


@pytest.fixture
def state(tmp_path, world):
    """The dashboard's state over the gateway's own session manager and provider factory."""
    from personalclaw.config.loader import AppConfig
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.providers.provider_bridge import create_provider_factory
    from personalclaw.session import SessionManager

    sessions = SessionManager(AppConfig(), provider_factory=create_provider_factory("chat"))
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    # A vocabulary the chats' titles match nothing in, so organizing them asks the model.
    state._folders = [{"id": "f-garden", "name": "Garden"}]
    state._tags = [{"id": "t-errands", "name": "Errands"}]
    state.broadcast_ws = lambda *_a, **_k: None
    return state


def _chat(state, key: str, exchange: tuple[str, str], memory_mode: str = "persistent"):
    from personalclaw.dashboard.state import _ChatSession

    session = _ChatSession(key, memory_mode=memory_mode)
    session.title = key
    session.messages = [
        {"role": "user", "content": exchange[0]},
        {"role": "assistant", "content": exchange[1]},
    ]
    state._sessions[key] = session
    return session


def _consolidator(tmp_path, key: str, exchange: tuple[str, str]):
    """The consolidator over a log holding *exchange* as the chat *key*'s unconsolidated turns,
    with no memory yet, so its prompt holds nothing but that chat."""
    from personalclaw.history import ConversationLog, HistoryConsolidator
    from personalclaw.memory import MemoryStore

    log = ConversationLog(base_dir=tmp_path / "sessions")
    log.init()
    log.append(key, "user", exchange[0])
    log.append(key, "assistant", exchange[1])
    memory = MemoryStore(workspace=tmp_path / "memory")
    memory.init()
    return HistoryConsolidator(log=log, memory=memory)


async def _title(state, session) -> None:
    from personalclaw.dashboard.chat_title import _maybe_auto_title

    await _maybe_auto_title(state, session)


async def _organize(state, session) -> None:
    from personalclaw.session_organize import propose_for_session

    await propose_for_session(state, session)


async def _follow_ups(state, session) -> None:
    from personalclaw.dashboard.chat_followups import _maybe_followups

    await _maybe_followups(state, session)


CHORES = {"title": (_title, TITLE), "organize": (_organize, ORGANIZE)}
CHORES["follow-ups"] = (_follow_ups, FOLLOW_UPS)


def _alone(request: list[dict[str, str]], head: str) -> None:
    """*request* is one message: the user's prompt that starts with *head*, and nothing else."""
    assert [m["role"] for m in request] == ["user"], (
        f"a {head!r} chore was sent {len(request)} messages, its own prompt among other "
        f"chores' prompts and answers: {[m['content'][:60] for m in request]}"
    )
    assert request[0]["content"].startswith(head), request[0]["content"][:120]


def _none_of(request: list[dict[str, str]], earlier: list[list[dict[str, str]]]) -> None:
    """No *earlier* chore's prompt is in *request*, folded into its one message or beside it.

    What an earlier chore answered may be in it rightly: an organize question names the title
    the chat's title chore gave it. What keeps an earlier exchange out is that the request is one
    message (:func:`_alone`)."""
    text = "\n".join(m["content"] for m in request)
    for before in earlier:
        head = before[-1]["content"].split("\n", 1)[0]
        assert head not in text, f"an earlier chore's prompt ({head!r}) reached this one"


def _mentions(text: str, words: tuple[str, ...]) -> list[str]:
    """The *words* in *text*, whatever their case."""
    return [word for word in words if word.lower() in text.lower()]


@pytest.mark.parametrize(
    ("earlier", "later"),
    [("organize", "title"), ("title", "organize"), ("title", "follow-ups")],
    ids=["a title after an organize", "an organize after a title", "follow-ups after a title"],
)
def test_a_chats_chore_is_sent_only_its_own_prompt(world, state, earlier, later):
    """🔴 The measured run: the second chore's request carried the first chore's prompt and
    answer ahead of its own."""
    chat = _chat(state, WALK_KEY, WALK)
    first, first_head = CHORES[earlier]
    then, then_head = CHORES[later]

    async def scenario() -> None:
        await first(state, chat)
        await then(state, chat)

    asyncio.run(scenario())

    (before,) = world.asked(first_head)
    (request,) = world.asked(then_head)
    _alone(before, first_head)
    _alone(request, then_head)
    _none_of(request, [before])
    assert _mentions(request[0]["content"], WALK_WORDS), "it is this chat's chore"


def test_a_consolidation_after_a_chats_other_chores_is_sent_only_its_own_prompt(
    world, state, tmp_path
):
    """🔴 The measured run: a consolidation read the chat's title, organize and follow-up
    exchanges as if they were the conversation."""
    chat = _chat(state, WALK_KEY, WALK)
    consolidator = _consolidator(tmp_path, f"dashboard:{WALK_KEY}", WALK)

    async def scenario() -> None:
        await _title(state, chat)
        await _organize(state, chat)
        await _follow_ups(state, chat)
        await consolidator.consolidate_now(f"dashboard:{WALK_KEY}")

    asyncio.run(scenario())

    earlier = world.asked(TITLE) + world.asked(ORGANIZE) + world.asked(FOLLOW_UPS)
    assert len(earlier) == 3, [r[-1]["content"][:40] for r in world.requests]
    (request,) = world.asked(CONSOLIDATION)
    _alone(request, CONSOLIDATION)
    _none_of(request, earlier)
    assert _mentions(request[0]["content"], WALK_WORDS) == list(WALK_WORDS)


def test_a_consolidation_holds_nothing_of_another_chats_chores(world, state, tmp_path):
    """🔴 The measured harm: one chat's consolidation opened with another chat's words, and stored
    a name it read there as the user's own. Two chats' chores run back to back: the second chat's
    consolidation is sent its own conversation and nothing of the first chat."""
    walk = _chat(state, WALK_KEY, WALK)
    consolidator = _consolidator(tmp_path, f"dashboard:{BREAD_KEY}", BREAD)

    async def scenario() -> None:
        await _title(state, walk)
        await _organize(state, walk)
        await _follow_ups(state, walk)
        await consolidator.consolidate_now(f"dashboard:{BREAD_KEY}")

    asyncio.run(scenario())

    (request,) = world.asked(CONSOLIDATION)
    _alone(request, CONSOLIDATION)
    sent = request[0]["content"]
    assert _mentions(sent, BREAD_WORDS) == list(BREAD_WORDS), "the second chat's consolidation"
    leaked = _mentions(sent, WALK_WORDS)
    assert not leaked, f"the first chat's words reached the second chat's consolidation: {leaked}"
    _none_of(request, world.asked(TITLE) + world.asked(ORGANIZE) + world.asked(FOLLOW_UPS))


def test_every_chore_of_two_chats_is_sent_one_message(world, state, tmp_path):
    """However many chores ran before, each request is one message: the requests no longer grow
    with the day."""
    walk = _chat(state, WALK_KEY, WALK)
    bread = _chat(state, BREAD_KEY, BREAD)
    consolidators = {
        WALK_KEY: _consolidator(tmp_path / "walk", f"dashboard:{WALK_KEY}", WALK),
        BREAD_KEY: _consolidator(tmp_path / "bread", f"dashboard:{BREAD_KEY}", BREAD),
    }

    async def scenario() -> None:
        for chat in (walk, bread, walk, bread):
            for chore, _head in CHORES.values():
                chat._titled = False
                await chore(state, chat)
        for key, consolidator in consolidators.items():
            await consolidator.consolidate_now(f"dashboard:{key}")

    asyncio.run(scenario())

    assert len(world.requests) == 14, [r[-1]["content"][:40] for r in world.requests]
    assert [len(r) for r in world.requests] == [1] * 14, [len(r) for r in world.requests]


def test_a_channels_chores_through_the_sdk_are_each_sent_only_their_own_prompt(world):
    """A channel app asks its own chores (a thread's title) through the channel SDK: two threads'
    titles back to back are two calls of one message each, and each is its thread's spend."""
    from personalclaw.sdk.channel import Attribution, chore_usage, run_chore
    from personalclaw.usage_ledger import _iter_rows

    threads = {"C1:1790000001.000100": WALK[0], "C1:1790000002.000200": BREAD[0]}

    async def scenario() -> None:
        for thread, said in threads.items():
            await run_chore(f"{TITLE} for this thread.\n\nUser: {said}", usage=chore_usage(thread))

    asyncio.run(scenario())

    walk, bread = world.asked(TITLE)
    _alone(walk, TITLE)
    _alone(bread, TITLE)
    assert _mentions(walk[0]["content"], WALK_WORDS) and not _mentions(
        walk[0]["content"], BREAD_WORDS
    )
    assert _mentions(bread[0]["content"], BREAD_WORDS) and not _mentions(
        bread[0]["content"], WALK_WORDS
    )
    assert isinstance(chore_usage(""), Attribution)
    rows = [(row["source"], row["session_key"]) for row in _iter_rows()]
    assert rows == [("background", thread) for thread in threads], rows


# ── a chat that keeps nothing ─────────────────────────────────────────────────────────────────


def _refusal(mode: str) -> str:
    return (
        f"This chat is {mode.capitalize()}, so nothing from it is sent to any model but the one "
        f"it runs on: {REF} was not asked."
    )


@pytest.mark.parametrize("mode", ["incognito", "temporary"])
def test_a_chore_made_for_a_chat_that_keeps_nothing_reaches_no_model(world, state, mode):
    """🔴 Red before the helper kept the rule: an Incognito or Temporary chat's chore asked by a
    caller that did not check the chat first was sent to the Background model. Outside the chat's
    own turn no model of the chat's is known, so the chore is refused before anything is sent;
    the same chore of a chat that keeps memory is answered (the positive control)."""
    from personalclaw import memory_writes
    from personalclaw.dashboard.chat_title import chat_chore

    restricted = _chat(state, WALK_KEY, WALK, memory_mode=mode)
    with pytest.raises(memory_writes.OtherModelRefused) as refused:
        asyncio.run(chat_chore(restricted, f"{TITLE} for this chat.\n\nUser: {WALK[0]}"))
    assert world.requests == [], "a chat that keeps nothing was sent to a background model"
    assert str(refused.value) == _refusal(mode)

    kept = _chat(state, BREAD_KEY, BREAD)
    asyncio.run(chat_chore(kept, f"{TITLE} for this chat.\n\nUser: {BREAD[0]}"))
    assert world.served == [ENTRY]


def test_a_channel_threads_chore_that_keeps_nothing_reaches_no_model(world):
    """🔴 Red before: a channel app's chore for a thread the channel marked Temporary was sent to
    the Background model. The helper reads every record of the thread's mode, the channel's mark
    among them, whatever the app asked first."""
    from personalclaw import memory_writes, session_restrictions
    from personalclaw.sdk.channel import chore_usage, run_chore

    thread = "C1:1790000003.000300"
    session_restrictions.mark_temporary(thread)
    try:
        with pytest.raises(memory_writes.OtherModelRefused):
            asyncio.run(run_chore(f"{TITLE} for this thread.", usage=chore_usage(thread)))
    finally:
        session_restrictions.clear(thread)
    assert world.requests == []


def test_a_chore_in_a_restricted_chats_own_turn_runs_on_the_chats_own_model(world):
    """In the chat's own turn, once the turn has named the model it runs on, a chore of it is that
    turn's work: it runs on the chat's own model, as every one-shot call there does, and the
    Background model is sent nothing. A chore the turn makes for another chat is refused."""
    from personalclaw import memory_writes
    from personalclaw.sdk.channel import chore_usage, run_chore

    key = f"dashboard:{WALK_KEY}"

    async def turn(for_chat: str) -> str:
        with memory_writes.derived_from(key, WALK_KEY, memory_mode="incognito"):
            memory_writes.answered_by(HERE_REF)
            return await run_chore(f"{TITLE} for this chat.", usage=chore_usage(for_chat))

    assert asyncio.run(turn(key)).startswith("Sourdough rescue")
    assert world.served == [HERE]

    with pytest.raises(memory_writes.OtherModelRefused) as refused:
        asyncio.run(turn(f"dashboard:{BREAD_KEY}"))
    assert world.served == [HERE]
    assert str(refused.value) == _refusal("incognito")


def test_a_chore_in_a_restricted_chats_work_before_its_turn_named_a_model_is_refused(world):
    """Work of an Incognito chat whose turn has not named its model yet knows no model it may use:
    a chore made in it, for whichever chat, is refused before anything is sent."""
    from personalclaw import memory_writes
    from personalclaw.sdk.channel import chore_usage, run_chore

    async def work() -> str:
        with memory_writes.derived_from(f"dashboard:{WALK_KEY}", memory_mode="incognito"):
            return await run_chore(f"{TITLE} for the home page.", usage=chore_usage())

    with pytest.raises(memory_writes.OtherModelRefused) as refused:
        asyncio.run(work())
    assert world.requests == []
    assert str(refused.value) == _refusal("incognito")
