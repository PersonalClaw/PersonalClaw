"""A rebind or clear of Embedding in Settings → Models reaches every memory store at its next use.

#3719 made a rebind reach every session already running. Embedding had the same defect in its own
holders: each memory store kept the embedding function it was handed when it was built. The main
store got one at boot, or when a dashboard read or the re-index first wired one; a per-directory
store got the one bound when that directory was first opened, and kept it for the process's life.
So a rebind reached new stores only, and clearing the binding reached none: the main store went on
embedding every memory, and every search, with the model it was bound to before.

A store now embeds with the model bound at each call. Rebinding raises a second problem, which
the width checks alone could not catch: a store holds vectors the previous model wrote. At a
different width they cannot be compared at all, and at the SAME width they compare and score
numbers that mean nothing. So every vector now records the model that wrote it, and only vectors
of the model bound now are compared. The rest are stale: search reads them by keyword, the stats
count them, and the re-index Settings → Models starts after a rebind re-embeds them in every
memory store, not only the main one.

Driven with a recording embedding provider: every ``embed`` call records the model it was asked
for and the instance it was built from, and each model writes its own vector space.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import types
from pathlib import Path
from typing import Any

import pytest

ENTRY = "rec"
A, B, C = "emb-a", "emb-b", "emb-c"  # A and C share a width and not a space; B is wider

OSPREY = "The osprey nests by the lake"
KESTREL = "A kestrel hovers over the field"


def _vector(model: str, text: str) -> list[float]:
    """Each model's own space. A counts osprey then kestrel; C (same width) counts them the other
    way round, so an A vector scored against a C query ranks the WRONG memory first; B is 3 wide."""
    osprey = 1.0 + 3.0 * text.lower().count("osprey")
    kestrel = 1.0 + 3.0 * text.lower().count("kestrel")
    if model == A:
        return [osprey, kestrel]
    if model == C:
        return [kestrel, osprey]
    return [osprey, kestrel, 1.0]


class _Recorded:
    """A built embedding provider: records the model and instance of every call it serves."""

    def __init__(self, model: str, endpoint: str, calls: list[tuple[str, str, str]]) -> None:
        self._model = model
        self._endpoint = endpoint
        self._calls = calls

    async def start(self) -> None:
        return None

    async def embed(self, inputs: list[str]) -> list[list[float]]:
        out = []
        for text in inputs:
            self._calls.append((self._model, self._endpoint, text))
            out.append(_vector(self._model, text))
        return out


@pytest.fixture
def recorded(monkeypatch) -> list[tuple[str, str, str]]:
    """One configured instance of a recording provider type, in a registry of its own.

    ``config.json`` names it, so its refs survive the prune ``load_active_models`` applies. The
    per-directory memory stores start empty. Returns every ``(model, endpoint, text)`` embedded.
    """
    from personalclaw import context as context_mod
    from personalclaw.config.loader import config_path
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.llm.registry import ProviderEntry, ProviderRegistry, set_default_registry

    monkeypatch.setattr(context_mod, "_memory_stores", {})
    calls: list[tuple[str, str, str]] = []
    registry = ProviderRegistry()

    def _factory(*, entry: ProviderEntry, session_key: str | None = None, **kwargs: Any):
        endpoint = str((entry.options or {}).get("endpoint") or "")
        return _Recorded(str(kwargs.get("embedding_model") or ""), endpoint, calls)

    registry.register_type(
        ProviderCapability(
            type=ENTRY,
            capabilities=frozenset({Capability.EMBEDDING}),
            supports_streaming=False,
            supports_tools=False,
            supports_embeddings=True,
            supports_vision=False,
            max_context_tokens=8192,
        ),
        _factory,
    )
    registry.register_entry(
        ProviderEntry(name=ENTRY, type=ENTRY, model="", options={"endpoint": "http://one"})
    )
    set_default_registry(registry)  # conftest restores the singleton afterwards
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"providers": [{"name": ENTRY, "type": ENTRY, "model": ""}]}))
    return calls


def _bind(model: str | None) -> None:
    """Bind Embedding the way Settings → Models' PUT does — or clear it (``None``)."""
    from personalclaw.providers.use_cases import load_active_models, save_active_models

    active = load_active_models()
    active["embedding"] = [f"{ENTRY}:{model}"] if model else []
    save_active_models(active)


def _edit_instance(endpoint: str) -> None:
    """An edit in Settings → Providers: ``PUT /api/model-providers/{name}`` replaces the entry."""
    from personalclaw.llm.registry import get_default_registry

    registry = get_default_registry()
    edited = dataclasses.replace(registry.get_entry(ENTRY), options={"endpoint": endpoint})
    registry.unregister_entry(ENTRY)
    registry.register_entry(edited)


def _models(calls: list[tuple[str, str, str]]) -> list[str]:
    return [model for model, _endpoint, _text in calls]


def _main_state(home: Path):
    """The dashboard's view of the gateway's main memory: its context builder over ``memory.db``,
    built the way ``GatewayOrchestrator._init_services`` builds it."""
    from personalclaw.context import ContextBuilder
    from personalclaw.memory import MemoryStore
    from personalclaw.vector_memory import VectorMemoryStore

    memory = MemoryStore()
    memory.init()
    store = VectorMemoryStore(db_path=home / "memory.db")
    store.init()
    memory.vector_store = store
    return types.SimpleNamespace(
        context_builder=ContextBuilder(memory=memory), knowledge_store=None
    )


def _main_service(state):
    """The memory service the dashboard's memory API writes and searches through."""
    from personalclaw.dashboard.handlers._shared import _get_memory
    from personalclaw.memory_service import service_for

    return service_for(_get_memory(state))


def _texts(results: list[dict]) -> list[str]:
    return [r["text"] for r in results]


# ── a rebind reaches every store ──────────────────────────────────────────────────────────


def test_a_directory_store_embeds_with_the_model_bound_now(recorded, tmp_path):
    """🔴 Red on main: the per-directory store kept the model bound when the directory was first
    opened, so after the rebind its search still embedded the query with that model."""
    from personalclaw.context import ContextBuilder
    from personalclaw.memory_service import service_for

    _bind(A)
    store = ContextBuilder.get_memory_for(str(tmp_path / "project"))
    service_for(store).write_episodic(OSPREY, source="user_explicit")

    _bind(B)
    service_for(ContextBuilder.get_memory_for(str(tmp_path / "project"))).search_episodic(
        query_text="osprey"
    )

    assert _models(recorded) == [A, B], "the search embeds with the model bound now"


def test_a_directory_opened_before_any_binding_follows_the_first_one(recorded, tmp_path):
    """🔴 Red on main: a directory first opened with no embedding bound stayed text-only for the
    process's life — binding one later reached nothing already open."""
    from personalclaw.context import ContextBuilder
    from personalclaw.memory_service import service_for

    ContextBuilder.get_memory_for(str(tmp_path / "project"))
    _bind(A)
    store = ContextBuilder.get_memory_for(str(tmp_path / "project"))
    service_for(store).write_episodic(OSPREY, source="user_explicit")

    assert _models(recorded) == [A]
    assert store.vector_store is not None
    assert store.vector_store.memory_stats()["embedded_count"] == 1


def test_a_directory_keeps_its_memories_searchable_after_a_clear(recorded, tmp_path, monkeypatch):
    """🔴 Red on main: a directory opened with no embedding model bound stayed text-only, so once
    the binding was cleared, a restart left every memory its store held out of every search."""
    from personalclaw import context as context_mod
    from personalclaw.context import ContextBuilder
    from personalclaw.memory_service import service_for

    _bind(A)
    store = ContextBuilder.get_memory_for(str(tmp_path / "project"))
    service_for(store).write_episodic(OSPREY, source="user_explicit")

    _bind(None)
    monkeypatch.setattr(context_mod, "_memory_stores", {})  # the gateway coming back up
    reopened = ContextBuilder.get_memory_for(str(tmp_path / "project"))
    found = service_for(reopened).search_episodic(query_text="osprey")

    assert _texts(found) == [OSPREY], "read by keyword"
    assert _models(recorded) == [A], "and nothing embeds"


def test_clearing_the_binding_reaches_the_main_store(recorded, tmp_path):
    """🔴 Red on main: the dashboard wired the main store with the model bound at its first read,
    and clearing Embedding left that function in place — every later memory and search embedded
    with the model the user had just unbound."""
    from personalclaw.config.loader import config_dir

    state = _main_state(config_dir())
    _bind(A)
    _main_service(state).write_episodic(OSPREY, source="user_explicit")

    _bind(None)
    service = _main_service(state)
    service.write_episodic(KESTREL, source="user_explicit")
    found = service.search_episodic(query_text="kestrel")

    assert _models(recorded) == [A], "nothing embeds once no embedding model is bound"
    assert _texts(found) == [KESTREL], "search reads memory by keyword instead"
    assert not service.can_vector_search


def test_an_edit_of_the_bound_instance_reaches_the_main_store(recorded, tmp_path):
    """🔴 Red on main: the store kept the provider built from the instance as it was, so a new
    endpoint saved in Settings → Providers reached no memory write."""
    from personalclaw.config.loader import config_dir

    state = _main_state(config_dir())
    _bind(A)
    _main_service(state).write_episodic(OSPREY, source="user_explicit")

    _edit_instance("http://two")
    _main_service(state).write_episodic(KESTREL, source="user_explicit")

    assert [(m, e) for m, e, _t in recorded] == [(A, "http://one"), (A, "http://two")]


def test_the_knowledge_embedder_follows_an_edit_of_the_bound_instance(recorded):
    """🔴 Red on main: ``get_knowledge_embedder`` was keyed on the binding alone, so an endpoint
    changed in Settings → Providers kept knowledge embedding against the old one."""
    from personalclaw.knowledge import get_knowledge_embedder

    _bind(A)
    get_knowledge_embedder().embed(OSPREY)
    _edit_instance("http://two")
    get_knowledge_embedder().embed(KESTREL)

    # Each build also probes the model for its width; only the two texts are the calls here.
    assert [e for _m, e, t in recorded if t in (OSPREY, KESTREL)] == ["http://one", "http://two"]


# ── a rebind never mixes models: stale vectors are never compared, and are counted ──────────


def test_a_rebind_to_another_width_never_mixes_and_the_store_says_so(recorded, tmp_path):
    """🔴 Red on main: the main store went on embedding with the old model after the rebind (the
    query named ``emb-a``), and nothing counted the vectors the new model cannot read."""
    from personalclaw.config.loader import config_dir

    state = _main_state(config_dir())
    _bind(A)
    service = _main_service(state)
    service.write_episodic(OSPREY, source="user_explicit")
    service.write_episodic(KESTREL, source="user_explicit")

    _bind(B)
    service = _main_service(state)
    found = service.search_episodic(query_text="osprey")
    service.write_episodic("A merlin hunts at dawn", source="user_explicit")

    assert _models(recorded) == [A, A, B, B], "the query and the new memory embed with emb-b"
    assert _texts(found) == [OSPREY], "the stale vectors are read by keyword"
    stats = state.context_builder.memory.vector_store.memory_stats()
    assert (stats["embedded_count"], stats["embedded_stale"]) == (1, 2)
    index = state.context_builder.memory.vector_store.index_state()
    assert index["dim"] == 3 and len(index["ids"]) == 1, "the index holds one model's vectors"


def test_a_same_width_swap_never_scores_one_models_vectors_against_anothers(recorded, tmp_path):
    """🔴 Red on main: after a restart on the new model, the kestrel memory ranked first for
    "osprey" — ``emb-c`` and ``emb-a`` share a width, so the old vectors passed every width guard
    and scored a query from another space."""
    from personalclaw.config.loader import config_dir

    _bind(A)
    service = _main_service(_main_state(config_dir()))
    service.write_episodic(OSPREY, source="user_explicit")
    service.write_episodic(KESTREL, source="user_explicit")

    _bind(C)
    restarted = _main_service(_main_state(config_dir()))  # the gateway coming back up
    found = restarted.search_episodic(query_text="osprey")

    assert _texts(found)[:1] == [OSPREY]
    assert not any("cosine_sim" in r for r in found), "no vector of emb-a is scored"


def test_a_stale_memory_stays_findable_once_the_new_model_has_written_one(recorded, tmp_path):
    """Once one memory holds the new model's vector, search compares that model's vectors, and
    only those. The memories the re-index has not reached yet are read by keyword beside them;
    without that, each of them dropped out of every search from the first re-embed on, and a
    store the re-index never finished stayed that way."""
    from personalclaw.config.loader import config_dir

    state = _main_state(config_dir())
    _bind(A)
    service = _main_service(state)
    service.write_episodic(OSPREY, source="user_explicit")
    service.write_episodic(KESTREL, source="user_explicit")

    _bind(B)
    service = _main_service(state)
    service.write_episodic("An osprey dives at dawn", source="user_explicit")
    found = {r["text"]: r for r in service.search_episodic(query_text="osprey")}

    assert OSPREY in found, "the memory holding an emb-a vector is still found"
    assert set(found) == {"An osprey dives at dawn", OSPREY}, "the kestrel matches neither arm"
    assert "cosine_sim" in found["An osprey dives at dawn"], "the emb-b vector is compared"
    assert "cosine_sim" not in found[OSPREY], "the emb-a one is read by keyword, never scored"


def test_a_memory_the_reindex_re_embeds_is_found_before_the_job_ends(recorded, tmp_path):
    """The index holds the new model's vectors from the first search after the rebind on, and the
    re-index writes the rest to the database only. So each memory it re-embedded after that search
    was in no index and no longer read by keyword (its vector is the new model's) — missing from
    every search until the job ended."""
    from personalclaw.config.loader import config_dir

    state = _main_state(config_dir())
    _bind(A)
    service = _main_service(state)
    service.write_episodic(OSPREY, source="user_explicit")
    service.write_episodic(KESTREL, source="user_explicit")

    _bind(B)
    seen: dict[int, dict[str, dict]] = {}

    def _search_meanwhile(done: int, _total: int) -> None:
        found = _main_service(state).search_episodic(query_text="kestrel")
        seen[done] = {r["text"]: r for r in found}

    state.context_builder.memory.vector_store.reembed_stale(on_progress=_search_meanwhile)

    assert "cosine_sim" not in seen[1][KESTREL], "not re-embedded yet: read by keyword"
    assert KESTREL in seen[2], "re-embedded: found by the next search"
    assert "cosine_sim" in seen[2][KESTREL], "and compared, with the model that re-embedded it"


def test_a_recall_over_stale_memories_says_what_it_read_by_keyword(recorded, tmp_path):
    """The recall disclosure described the capability alone: with a model bound it said "Ranked
    by semantic similarity" just after a rebind, when every memory was read by keyword, and on
    through the re-index, while the memories it had not reached yet were."""
    from personalclaw.config.loader import config_dir
    from personalclaw.dashboard.handlers.memory import _ranking_payload

    state = _main_state(config_dir())
    _bind(A)
    service = _main_service(state)
    service.write_episodic(OSPREY, source="user_explicit")
    service.write_episodic(KESTREL, source="user_explicit")
    before = _ranking_payload(service)

    _bind(B)
    service = _main_service(state)
    rebound = _ranking_payload(service)
    service.write_episodic("An osprey dives at dawn", source="user_explicit")
    partial = _ranking_payload(service)

    assert (before["mode"], before["stale"]) == ("semantic", 0)
    assert (rebound["mode"], rebound["degraded"]) == ("keyword", True)
    assert rebound["summary"].startswith(
        "Keyword-ranked only: every stored vector is another embedding model's"
    )
    assert rebound["summary"].endswith(
        " The re-index in Settings → Models re-embeds the 2 memories."
    )
    assert (partial["mode"], partial["stale"], partial["degraded"]) == ("semantic", 2, True)
    assert partial["summary"] == before["summary"] + (
        " 2 memories embedded by another embedding model are read by keyword until the re-index "
        "in Settings → Models re-embeds them."
    )


def test_lesson_dedup_never_compares_two_models_vectors(recorded, tmp_path):
    """🔴 Red on main: after a restart on ``emb-c``, the first lesson's stored ``emb-a`` vector
    matched the second lesson's ``emb-c`` vector exactly, so the lesson about the osprey was
    superseded by one about a different bird."""
    from personalclaw.config.loader import config_dir

    _bind(A)
    _main_service(_main_state(config_dir())).write_lesson("Check the osprey feeder daily")

    _bind(C)
    restarted = _main_service(_main_state(config_dir()))  # the gateway coming back up
    written = restarted.write_lesson("Refill the kestrel perch weekly")

    lessons = [str(r.get("value_json")) for r in restarted.get_lessons()]
    assert written, "the new lesson is written"
    assert any("osprey" in v for v in lessons), "and the other lesson is not superseded by it"
    assert any("kestrel" in v for v in lessons)


# ── the re-index Settings → Models starts re-embeds every memory store ─────────────────────


def _reindex(state) -> dict[str, Any]:
    """``POST /api/models/embedding/reindex`` as the Models panel sends it after a save, run to
    the job's end."""
    from personalclaw.dashboard.embedding_reindex import ReindexRegistry
    from personalclaw.dashboard.handlers.embedding_reindex import api_reindex_start

    jobs = ReindexRegistry()
    state.embedding_reindex = lambda: jobs

    async def _run() -> dict[str, Any]:
        request = types.SimpleNamespace(app={"state": state})
        response = await api_reindex_start(request)
        assert response.status == 202, response.body
        job_id = json.loads(response.body)["id"]
        for _ in range(500):
            job = jobs.get(job_id)
            if job is not None and job.status != "running":
                return job.to_dict()
            await asyncio.sleep(0.01)
        raise AssertionError("the re-index did not finish")

    return asyncio.run(_run())


def test_the_reindex_re_embeds_every_memory_store(recorded, tmp_path):
    """🔴 Red on main: the re-index re-embedded the main store only — the directory's memory kept
    its ``emb-a`` vector and went on embedding with ``emb-a``."""
    from personalclaw.config.loader import config_dir
    from personalclaw.context import ContextBuilder
    from personalclaw.memory_service import service_for

    state = _main_state(config_dir())
    _bind(A)
    _main_service(state).write_episodic(OSPREY, source="user_explicit")
    directory = ContextBuilder.get_memory_for(str(tmp_path / "project"))
    service_for(directory).write_episodic(KESTREL, source="user_explicit")

    _bind(B)
    job = _reindex(state)
    recorded.clear()
    found = service_for(ContextBuilder.get_memory_for(str(tmp_path / "project"))).search_episodic(
        query_text="kestrel"
    )

    assert job["status"] == "done", job
    assert job["memory"] == 2, "both stores' memories were re-embedded"
    assert _models(recorded) == [B]
    assert _texts(found) == [KESTREL] and "cosine_sim" in found[0], "found by meaning again"
    for store in (state.context_builder.memory.vector_store, directory.vector_store):
        assert store.memory_stats()["embedded_stale"] == 0


def test_the_reindex_of_a_small_store_reports_each_memory(recorded, tmp_path, monkeypatch):
    """🔴 Red on main: the job published progress every 25th memory only, so the re-index of a
    store holding fewer sat at "reindexing memory (0/N)" in Settings → Models until it ended,
    however long the model took over each."""
    from personalclaw.config.loader import config_dir
    from personalclaw.dashboard.embedding_reindex import ReindexRegistry

    frames: list[tuple[str, int, int]] = []
    publish = ReindexRegistry._publish

    def _recording(self, job, event: str) -> None:
        frames.append((job.phase, job.done, job.total))
        publish(self, job, event)

    monkeypatch.setattr(ReindexRegistry, "_publish", _recording)
    state = _main_state(config_dir())
    _bind(A)
    _main_service(state).write_episodic(OSPREY, source="user_explicit")
    _main_service(state).write_episodic(KESTREL, source="user_explicit")

    _bind(B)
    _reindex(state)

    memory = [(done, total) for phase, done, total in frames if phase == "reindexing memory"]
    assert memory == [(0, 2), (1, 2), (2, 2)]


def test_the_gateway_start_re_embeds_what_an_update_or_a_stop_left(recorded, tmp_path):
    """🔴 Red on main: the re-index the gateway resumes at its start looked at knowledge only. The
    memory a stopped re-index left half done stayed read by keyword until the model was rebound,
    and so would every memory vector after the update that started recording each vector's
    model, since none names one yet."""
    from personalclaw.config.loader import config_dir
    from personalclaw.dashboard.embedding_reindex import ReindexRegistry
    from personalclaw.dashboard.handlers.embedding_reindex import resume_interrupted_reindex

    state = _main_state(config_dir())
    _bind(A)
    service = _main_service(state)
    service.write_episodic(OSPREY, source="user_explicit")
    service.write_episodic(KESTREL, source="user_explicit")
    store = state.context_builder.memory.vector_store
    store.db.execute(
        "UPDATE episodic_memories SET embedding_model = NULL"
    )  # as the update finds them
    store.db.commit()
    jobs = ReindexRegistry()
    state.embedding_reindex = lambda: jobs

    async def _start() -> dict[str, Any]:
        job = resume_interrupted_reindex({"state": state})
        assert job is not None, "a re-index starts"
        for _ in range(500):
            if job.status != "running":
                return job.to_dict()
            await asyncio.sleep(0.01)
        raise AssertionError("the re-index did not finish")

    assert store.memory_stats()["embedded_stale"] == 2
    done = asyncio.run(_start())

    assert (done["status"], done["memory"], done["knowledge"]) == ("done", 2, 0)
    assert store.memory_stats()["embedded_stale"] == 0
    re_embedded = [(m, t) for m, _e, t in recorded if t in (OSPREY, KESTREL)][2:]
    assert re_embedded == [(A, OSPREY), (A, KESTREL)], "with the model bound now"


def test_the_gateway_start_leaves_whole_stores_alone(recorded, tmp_path):
    """Nothing to re-embed, nothing started: the check runs at every start."""
    from personalclaw.config.loader import config_dir
    from personalclaw.dashboard.embedding_reindex import ReindexRegistry
    from personalclaw.dashboard.handlers.embedding_reindex import resume_interrupted_reindex

    state = _main_state(config_dir())
    _bind(A)
    _main_service(state).write_episodic(OSPREY, source="user_explicit")
    jobs = ReindexRegistry()
    state.embedding_reindex = lambda: jobs

    assert resume_interrupted_reindex({"state": state}) is None
    assert jobs.list() == []


def test_a_directory_is_read_whatever_characters_its_home_path_holds(tmp_path):
    """Whether a directory holds memory is read from its database, opened read-only by URI. A raw
    ``file:`` URI read a "%", "?" or "#" in the home's path as URI syntax, so that directory read
    as holding nothing: the re-index skipped it, and a clear left its memories unreachable."""
    import sqlite3

    from personalclaw.context import _holds_memory

    db = tmp_path / "50% off?#1" / "memory_index.db"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE episodic_memories (id TEXT); CREATE TABLE semantic_memory (key TEXT);"
        "INSERT INTO episodic_memories VALUES ('m');"
    )
    conn.commit()
    conn.close()

    assert _holds_memory(db) is True
    assert _holds_memory(db.with_name("absent.db")) is False
    assert not db.with_name("absent.db").exists(), "and a read never creates one"


# ── every memory is found while the model catches up with it ──────────────────────────────


async def _put_embedding(state, model: str) -> None:
    """Bind Embedding through ``PUT /api/models/active/embedding``, as any caller does (Settings →
    Models, onboarding, a script), and let what the binding starts in the background finish."""
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import model_registry as mr

    app = web.Application()
    app["state"] = state
    read = await mr.api_models_active(make_mocked_request("GET", "/api/models/active"))
    revision = json.loads(read.text)["revisions"]["embedding"]
    req = make_mocked_request(
        "PUT", "/api/models/active/embedding", headers={"If-Match": f'"{revision}"'}, app=app
    )
    req.match_info["use_case"] = "embedding"

    async def _json():
        return {"models": [f"{ENTRY}:{model}"]}

    req.json = _json  # type: ignore[method-assign]
    assert (await mr.api_models_active_set(req)).status == 200
    for task in list(getattr(mr, "_BINDING_REINDEXES", ())):
        await task
    for _ in range(500):
        running = state.embedding_reindex().active()
        if running is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the re-index did not finish")


def test_a_memory_written_while_nothing_was_bound_joins_semantic_search_on_the_bind(
    recorded, tmp_path
):
    """🔴 Red on main: binding a model embedded nothing until someone started a re-index, so a
    memory written while none was bound was never compared by meaning."""
    from personalclaw.config.loader import config_dir
    from personalclaw.dashboard.embedding_reindex import ReindexRegistry

    state = _main_state(config_dir())
    jobs = ReindexRegistry()
    state.embedding_reindex = lambda: jobs
    _main_service(state).write_episodic(OSPREY, source="user_explicit")  # nothing bound

    asyncio.run(_put_embedding(state, A))
    found = _main_service(state).search_episodic(query_text="osprey")

    assert _texts(found) == [OSPREY]
    assert "cosine_sim" in found[0], "compared by meaning, with the model the binding named"
    assert (A, OSPREY) in [(model, text) for model, _endpoint, text in recorded]


def test_a_memory_no_model_embedded_is_read_by_keyword_beside_the_vector_results(
    recorded, tmp_path
):
    """🔴 Red on main: once one memory held a vector, a search compared vectors, and a memory
    that had none was in neither arm: it dropped out of every search until something embedded
    it."""
    from personalclaw.config.loader import config_dir

    state = _main_state(config_dir())
    _main_service(state).write_episodic(OSPREY, source="user_explicit")  # nothing bound
    _bind(A)  # bound without the re-index a binding through the PUT starts
    _main_service(state).write_episodic("An osprey dives at dawn", source="user_explicit")

    found = {r["text"]: r for r in _main_service(state).search_episodic(query_text="osprey")}

    assert set(found) == {OSPREY, "An osprey dives at dawn"}
    assert "cosine_sim" in found["An osprey dives at dawn"]
    assert "cosine_sim" not in found[OSPREY], "read by keyword: nothing compared it by meaning"


def test_the_gateway_start_embeds_the_memories_no_model_embedded(recorded, tmp_path):
    """🔴 Red on main: the start counted the vectors of another model only, so a memory written
    while nothing was bound stayed unembedded however often the gateway restarted."""
    from personalclaw.config.loader import config_dir
    from personalclaw.dashboard.embedding_reindex import ReindexRegistry
    from personalclaw.dashboard.handlers.embedding_reindex import resume_interrupted_reindex

    state = _main_state(config_dir())
    _main_service(state).write_episodic(OSPREY, source="user_explicit")  # nothing bound
    _bind(A)
    jobs = ReindexRegistry()
    state.embedding_reindex = lambda: jobs

    async def _start() -> dict[str, Any]:
        job = resume_interrupted_reindex({"state": state})
        assert job is not None, "a re-index starts"
        for _ in range(500):
            if job.status != "running":
                return job.to_dict()
            await asyncio.sleep(0.01)
        raise AssertionError("the re-index did not finish")

    done = asyncio.run(_start())

    assert (done["status"], done["memory"]) == ("done", 1)
    assert state.context_builder.memory.vector_store.memory_stats()["unembedded"] == 0


def test_recall_ranks_a_keyword_hit_among_the_semantic_ones_by_score(recorded, tmp_path):
    """🔴 Red on main: a memory read by keyword carried no score, so Recall's ranking put every
    keyword hit after every semantic one: mid re-index, the exact match came last."""
    from personalclaw.config.loader import config_dir

    state = _main_state(config_dir())
    _bind(A)
    _main_service(state).write_episodic(OSPREY, source="user_explicit")
    _bind(B)
    _main_service(state).write_episodic(
        KESTREL, source="user_explicit"
    )  # re-embedded, weakly alike

    ranked = _main_service(state).rank_episodic(query_text="osprey")

    assert _texts(ranked) == [OSPREY, KESTREL], [(r["text"], r.get("score")) for r in ranked]
    assert "cosine_sim" not in ranked[0] and ranked[0]["keyword_match"] == 1.0
    assert ranked[0]["score"] > ranked[1]["score"]


def test_the_reindex_re_embeds_only_the_knowledge_the_model_has_not(recorded, tmp_path):
    """🔴 Red on main: the knowledge half of the re-index cleared and re-embedded every item,
    the ones the bound model had embedded already included."""
    from personalclaw.config.loader import config_dir
    from personalclaw.knowledge.embedder import create_embedder_from_config
    from personalclaw.knowledge.pipeline.runner import _embed
    from personalclaw.knowledge.store import KnowledgeStore

    state = _main_state(config_dir())
    state.knowledge_store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    _bind(A)
    ks = state.knowledge_store
    embedded = ks.create_typed_item(item_type="note", title="Osprey nesting", content="The osprey")
    assert _embed(ks, embedded, create_embedder_from_config({})) == "done"  # the ingest path
    ks.create_typed_item(item_type="note", title="Kestrel hover", content="A kestrel")  # none
    recorded.clear()

    job = _reindex(state)

    assert (job["status"], job["knowledge"]) == ("done", 1), job
    assert [text for _m, _e, text in recorded if "Osprey nesting" in text] == []


def test_a_rebuild_on_another_thread_never_pairs_its_ids_with_the_index_a_search_read(
    tmp_path, monkeypatch
):
    """🔴 Red on main: a rebuild assigned the width, the ids and the index one after another, and
    a search read the index and then the ids. A rebuild on the re-index's thread between the two
    paired the new ids with the old index, so the search named another memory for a row (with
    the first one's score), or read past the end of the new ids."""
    import threading

    faiss = pytest.importorskip("faiss")
    from personalclaw.vector_memory import VectorMemoryStore

    real = faiss.IndexFlatIP
    armed: list[Any] = []

    class _Index:
        """A FAISS index that runs what is armed just before it answers a search."""

        def __init__(self, dim: int) -> None:
            self._inner = real(dim)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        def search(self, x: Any, k: int) -> Any:
            while armed:
                armed.pop()()
            return self._inner.search(x, k)

    monkeypatch.setattr(faiss, "IndexFlatIP", _Index)
    store = VectorMemoryStore(db_path=tmp_path / "memory.db")
    store.embed_fn = lambda text: [1.0, 0.0] if "osprey" in text.lower() else [0.0, 1.0]
    store.init()
    assert store.write_episodic(OSPREY, source="user_explicit")
    assert store.write_episodic(KESTREL, source="user_explicit")
    osprey = next(r["id"] for r in store.get_episodic_list() if r["text"] == OSPREY)

    def _rebuild_on_the_reindex_thread() -> None:
        store.delete_episodic(osprey)  # a memory goes, and the index is rebuilt without it
        worker = threading.Thread(target=store.rebuild_faiss_index)
        worker.start()
        worker.join()

    armed.append(_rebuild_on_the_reindex_thread)
    found = store.search_episodic(query_embedding=[1.0, 0.0], mmr=False)

    assert [(r["text"], r["cosine_sim"]) for r in found] == [(KESTREL, 0.0)], found
    store.close()
