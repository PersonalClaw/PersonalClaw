"""A rebind in Settings → Models reaches the next call of everything already running.

Measured on a scratch-home gateway (a recording fake Ollama; chat bound to one model at start,
rebound to another with the same PUT Settings → Models sends): after the rebind, 6 of 8 calls still
named the old model — the chat title, both rounds of follow-ups, the home suggestions, the memory
consolidation, all through the one persistent background session, and the second turn of a chat
opened before the rebind that has no model of its own. A new chat and a one-shot call followed.

The cause: ``SessionManager`` caches one runtime per session and a native runtime fixes its model
when it is built, and the chores ran on one background session built when the gateway started. A
chore is a call of its own now (``chores.run_chore``), which resolves its model as it is made. A
runtime records what its model was resolved from (``provider_bridge.ResolutionBasis``: the chains
it read, and the provider entry it serves from), and a session whose basis moved is rebuilt at its
next acquire — the way an edited agent's already was.

Two more holders of an old resolution are covered here: the knowledge handlers' embedder, built
once at boot, and a restored chat whose saved model no longer fits, which was pinned to the first
provider's model and so outranked every later rebind.
"""

from __future__ import annotations

import json
import types
from typing import Any

import pytest

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

ENTRY = "rec"
A, B, C = "model-a", "model-b", "model-c"


class _Recorded:
    """A model provider that records the model every call it serves names."""

    supports_tools = False

    def __init__(self, model: str, embedding_model: str, calls: list[tuple[str, str]]) -> None:
        self.model = model
        self.embedding_model = embedding_model
        self._calls = calls

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def is_alive(self) -> bool:
        return True

    def context_usage_pct(self) -> float | None:
        return None

    async def complete(self, messages: list[dict], *, model: str | None = None, **_kw: Any):
        self._calls.append(("chat", str(model or self.model)))
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="Fake Chat Title")
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=3, output_tokens=2)

    async def stream(self, message: str):
        async for event in self.complete([{"role": "user", "content": message}]):
            yield event

    async def embed(self, inputs: list[str]) -> list[list[float]]:
        self._calls.append(("embed", self.embedding_model))
        return [[0.6, 0.8] for _ in inputs]


@pytest.fixture
def recorded() -> list[tuple[str, str]]:
    """One configured instance of a recording provider type, in a registry of its own.

    ``config.json`` names it too, so its refs survive the prune ``load_active_models`` applies.
    Returns every ``(kind, model)`` a call named, in order.
    """
    from personalclaw.config.loader import config_path
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.llm.registry import ProviderEntry, ProviderRegistry, set_default_registry

    calls: list[tuple[str, str]] = []
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **kwargs: Any):
        model = str(kwargs.get("model") or entry.model)
        return _Recorded(model, str(kwargs.get("embedding_model") or ""), calls)

    registry.register_type(
        ProviderCapability(
            type=ENTRY,
            capabilities=frozenset({Capability.CHAT, Capability.EMBEDDING}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=True,
            supports_vision=False,
            max_context_tokens=8192,
        ),
        _factory,
    )
    registry.register_entry(ProviderEntry(name=ENTRY, type=ENTRY, model=""))
    set_default_registry(registry)  # conftest restores the singleton afterwards
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"providers": [{"name": ENTRY, "type": ENTRY, "model": ""}]}))
    return calls


def _bind(**chains: list[str]) -> None:
    """Write Settings → Models the way its PUT does: the whole store, one chain per use case."""
    from personalclaw.providers.use_cases import save_active_models

    save_active_models(
        {use_case: [f"{ENTRY}:{m}" for m in models] for use_case, models in chains.items()}
    )


def _sessions():
    """The gateway's session manager over the real resolution seam (no warm pool)."""
    from unittest.mock import MagicMock

    from personalclaw.providers.provider_bridge import create_provider_factory
    from personalclaw.session import SessionManager

    cfg = MagicMock()
    cfg.default_agent = ""
    cfg.model = "auto"
    cfg.session.pool_size = 0
    cfg.session.pool_agent = ""
    cfg.session.pool_ttl_secs = 0
    cfg.session.timeout_secs = 3600
    return SessionManager(cfg, provider_factory=create_provider_factory("chat"))


async def _title() -> str:
    """One chat-title chore, through the real path: a call of its own on the Background chain."""
    from personalclaw.chores import chore_usage
    from personalclaw.dashboard.chat_title import _generate_title_via_provider

    return await _generate_title_via_provider(
        [{"role": "user", "content": "hi"}], usage=chore_usage()
    )


async def _turn(sessions, key: str, **kwargs: Any) -> object:
    """One turn on the chat ``key``; returns the runtime that served it."""
    client, _is_new, _resumed = await sessions.get_or_create(key, **kwargs)
    try:
        async for _event in client.stream("Hello"):
            pass
    finally:
        sessions.release(key)
    return client


def _chat_models(calls: list[tuple[str, str]]) -> list[str]:
    return [model for kind, model in calls if kind == "chat"]


# ── the chores follow a rebind ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_rebind_reaches_the_background_chores(recorded):
    """🔴 Red when the chores shared one background session: it was built for the model chat was
    bound to first, and every chore after the rebind went on naming it."""
    _bind(chat=[A])
    assert await _title() == "Fake Chat Title"

    _bind(chat=[B])
    await _title()
    await _title()

    assert _chat_models(recorded) == [A, B, B]


@pytest.mark.asyncio
async def test_binding_the_background_axis_itself_reaches_the_background_chores(recorded):
    """🔴 Red when the chores shared one background session: binding a cheap model to Background
    left them on the chat model."""
    _bind(chat=[A])
    await _title()

    _bind(chat=[A], background=[C])
    await _title()

    assert _chat_models(recorded) == [A, C]


# ── an open chat follows a rebind, and keeps a model of its own ───────────────────────────────


@pytest.mark.asyncio
async def test_an_open_chat_with_no_model_of_its_own_follows_a_rebind(recorded):
    """🔴 Red on main: the chat's second turn, after the rebind, went to the old model."""
    _bind(chat=[A])
    sessions = _sessions()
    before = await _turn(sessions, "dashboard:open")

    _bind(chat=[B])
    after = await _turn(sessions, "dashboard:open")

    assert _chat_models(recorded) == [A, B]
    assert after is not before, "the runtime was rebuilt, at the chat's next turn"


@pytest.mark.asyncio
async def test_a_chat_that_picked_its_own_model_keeps_it_across_a_rebind(recorded):
    """The rebind rebuilds the runtime, and the rebuilt one still serves the chat's own pick."""
    _bind(chat=[A, B])
    sessions = _sessions()
    await _turn(sessions, "dashboard:picked", model=f"{ENTRY}:{B}")

    _bind(chat=[C, B])
    await _turn(sessions, "dashboard:picked", model=f"{ENTRY}:{B}")

    assert _chat_models(recorded) == [B, B]


@pytest.mark.asyncio
async def test_a_turn_running_when_chat_is_rebound_finishes_on_the_model_it_started_with(recorded):
    _bind(chat=[A])
    sessions = _sessions()
    running, _new, _ = await sessions.get_or_create("dashboard:busy")  # the turn holds its permit

    _bind(chat=[B])
    async for _event in running.stream("Hello"):
        pass
    sessions.release("dashboard:busy")  # the turn ends
    await _turn(sessions, "dashboard:busy")

    assert _chat_models(recorded) == [A, B]


@pytest.mark.asyncio
async def test_a_binding_no_session_reads_leaves_open_sessions_alone(recorded):
    """Vacuity control: rebinding embedding does not rebuild a chat runtime."""
    _bind(chat=[A])
    sessions = _sessions()
    before = await _turn(sessions, "dashboard:steady")

    _bind(chat=[A], embedding=["emb-x"])
    after = await _turn(sessions, "dashboard:steady")

    assert after is before


# ── an edit of the instance a session serves from reaches it ──────────────────────────────────


@pytest.mark.asyncio
async def test_an_edit_of_the_instance_a_session_serves_from_reaches_its_next_call(recorded):
    """🔴 Red on main: with nothing bound, a session kept the instance it was built from, so a
    model changed on that instance in Settings → Providers reached none of its turns."""
    import dataclasses

    from personalclaw.llm.registry import get_default_registry

    registry = get_default_registry()
    configured = dataclasses.replace(registry.get_entry(ENTRY), model=A)
    registry.unregister_entry(ENTRY)
    registry.register_entry(configured)
    _bind()
    sessions = _sessions()
    before = await _turn(sessions, "dashboard:edited")

    # The edit, as ``PUT /api/model-providers/{name}`` makes it: the entry is replaced.
    edited = dataclasses.replace(registry.get_entry(ENTRY), model=B)
    registry.unregister_entry(ENTRY)
    registry.register_entry(edited)
    after = await _turn(sessions, "dashboard:edited")

    assert _chat_models(recorded) == [A, B]
    assert after is not before, "the runtime was rebuilt, at the chat's next turn"


@pytest.mark.asyncio
async def test_an_edit_of_the_instance_a_chore_serves_from_reaches_the_next_chore(recorded):
    """A chore resolves its model as it is made, so an edit of the instance reaches the next."""
    import dataclasses

    from personalclaw.llm.registry import get_default_registry

    registry = get_default_registry()
    configured = dataclasses.replace(registry.get_entry(ENTRY), model=A)
    registry.unregister_entry(ENTRY)
    registry.register_entry(configured)
    _bind()
    await _title()

    edited = dataclasses.replace(registry.get_entry(ENTRY), model=B)
    registry.unregister_entry(ENTRY)
    registry.register_entry(edited)
    await _title()

    assert _chat_models(recorded) == [A, B]


# ── the knowledge handlers embed with the embedding model bound now ───────────────────────────


@pytest.mark.asyncio
async def test_knowledge_search_embeds_with_the_embedding_model_bound_now(recorded, tmp_path):
    """🔴 Red on main: the knowledge handlers kept the embedder built the first time, so after an
    embedding rebind (and its re-index) search went on embedding queries with the old model."""
    from personalclaw.dashboard.handlers.knowledge import list_items
    from personalclaw.knowledge.store import KnowledgeStore

    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    app: dict[str, Any] = {"state": types.SimpleNamespace(knowledge_store=store)}

    class _Req:
        def __init__(self) -> None:
            self.query = {"q": "osprey"}
            self.app = app  # one app across both searches, as the gateway has

    _bind(embedding=["emb-a"])
    await list_items(_Req())
    first = {model for kind, model in recorded if kind == "embed"}
    recorded.clear()

    _bind(embedding=["emb-b"])
    await list_items(_Req())
    second = {model for kind, model in recorded if kind == "embed"}

    assert first == {"emb-a"}
    assert second == {"emb-b"}


# ── a restored chat whose saved model no longer fits follows its binding ──────────────────────


def test_a_restored_chat_whose_model_no_longer_fits_follows_its_binding(tmp_path, monkeypatch):
    """🔴 Red on main: the chat was pinned to the first provider's own model, a choice nobody
    made, which then outranked every rebind for the life of the chat."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from personalclaw.config.loader import config_path
    from personalclaw.dashboard.chat import _rehydrate_session_from_history
    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"providers": [{"name": ENTRY, "type": ENTRY, "model": A}]}))
    meta = {"_type": "metadata", "created_at": "2026-09-26T10:00:00", "last_consolidated": 0}
    meta.update({"title": "Restored", "model": "claude-opus-4.7"})
    turn = {"role": "user", "content": "hi", "ts": "2026-09-26T10:00:01"}
    (tmp_path / "dashboard_restored.jsonl").write_text(
        json.dumps(meta) + "\n" + json.dumps(turn) + "\n", encoding="utf-8"
    )
    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.remove = AsyncMock()
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    with patch(
        "personalclaw.dashboard.chat_persistence._model_matches_provider", return_value=False
    ):
        session = _rehydrate_session_from_history(state, "restored")

    assert session is not None
    assert session.title == "Restored", "the chat really was restored from its file"
    assert (
        session.model == ""
    ), "no model of its own: Settings → Models decides, rebind after rebind"
