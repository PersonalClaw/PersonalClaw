"""One control plane for embeddings, and `migrated` means migrated (issue 522).

Six `/api/memory/*` routes formed a SECOND control plane for embeddings with zero frontend callers
and zero `api.ts` wrappers -- measured across `web/src`, the separate PersonalClawApps repo, and
core: only their own definitions, their registrations, one test, and two docs referenced them.

Every capability they offered already exists on the reachable, FE-wired plane:

    activate-model     -> PUT /api/models/active/embedding      (ModelsPanel)
    delete-model       -> DELETE /api/models/local/{prov}/{mod}  (LocalModelManager)
    embedding-models   -> GET /api/models/local/{prov}/search + the downloads plane
    enable/disable     -> binding an embedding model in Settings > Models + startEmbeddingReindex

So this is a redundant surface, not a missing feature -- and it carried a real defect. The enable
handler ran::

    data.setdefault("memory", {})["migrated"] = True

`migrated` gates markdown memory (`history.py`: markdown reads/writes are skipped when true), and
the reachable path never set it. So one user-visible intent left two different persisted states.

The decision the issue asked for is settled by the code rather than by taste: `api_memory_migrate`
sets `migrated` only when a migration actually produced entries ("Auto-set migrated=true if
migration produced entries"). `migrated` therefore belongs to MIGRATION, and enabling embeddings
migrates nothing -- so the coupling was simply wrong and is NOT reproduced on the reachable path.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from gateway_routes_shared import handler_for
from one_embedding_control_plane_shared import REPO, RETIRED_PATHS

from personalclaw.dashboard import handlers
from personalclaw.dashboard.handlers import memory as mem_mod

#: The six retired handler names, and the route paths they were registered under.
RETIRED_HANDLERS = (
    "api_memory_embedding_status",
    "api_memory_enable_embeddings",
    "api_memory_disable_embeddings",
    "api_memory_embedding_models",
    "api_memory_delete_model",
    "api_memory_activate_model",
)


def test_no_retired_handler_survives_on_the_barrel():
    """Import-level, not a source scan: registration is `handlers.<name>`, so a leftover
    registration would raise AttributeError while the dashboard boots."""
    for name in RETIRED_HANDLERS:
        assert not hasattr(handlers, name), f"{name} is still exported"
        assert not hasattr(mem_mod, name), f"{name} still exists in the memory module"


def test_no_retired_route_is_registered():
    """The gateway serves none of the retired paths under any method, while the facets route
    they sat beside still resolves, so the table asked is the real one."""
    assert handler_for("GET", "/api/memory/facets") is mem_mod.api_memory_facets
    for path in RETIRED_PATHS:
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            assert handler_for(method, path) is None, f"{method} {path} is still served"


def test_the_dead_setup_state_machine_is_gone():
    """`setup_step`/`setup_error`/`can_retry` were written in three places and read only by the
    orphaned status endpoint, so no consumer could ever observe a non-idle value -- including three
    carefully-worded diagnostics and a "Click Enable to retry" string for a button nothing rendered.
    """
    assert not hasattr(mem_mod, "_embedding_setup_status")
    for name, value in vars(mem_mod).items():
        if isinstance(value, dict):
            assert not {"setup_step", "can_retry"} & set(value), f"{name} still holds the machine"


# ── the survivor: `migrated` still has exactly one owner ────────────────────────────────────


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """A `config.json` of the test's own, as the config transaction finds it; returns a reader
    of `memory.migrated` (``None`` while unset)."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"memory": {}}), encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: path)
    return lambda: json.loads(path.read_text(encoding="utf-8")).get("memory", {}).get("migrated")


class _LegacyStore:
    """The global memory, whose markdown migration produces *counts*; it records how many
    migrations ran at once."""

    def __init__(self, counts: dict[str, int], *, hold: float = 0.0) -> None:
        self.counts = counts
        self.hold = hold
        self.running = 0
        self.most_at_once = 0
        self._lock = threading.Lock()

    def migrate_from_markdown(self) -> dict[str, int]:
        with self._lock:
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
        time.sleep(self.hold)
        with self._lock:
            self.running -= 1
        return dict(self.counts)


def _migrate(store: _LegacyStore, monkeypatch) -> web.Response:
    monkeypatch.setattr(mem_mod, "_global_provider", lambda _state: store)
    app = web.Application()
    app["state"] = MagicMock()  # no session headers: not a restricted session
    return mem_mod.api_memory_migrate(make_mocked_request("POST", "/api/memory/migrate", app=app))


def test_set_migrated_survives_and_the_migrate_endpoint_still_calls_it(config_file, monkeypatch):
    """🪤 `_set_migrated` sat INSIDE the deleted block. Cutting the block wholesale would have
    taken it with it and broken the real migration endpoint -- the likeliest way this surgery
    goes wrong, so a migration that produced entries is driven and its record read back."""
    resp = asyncio.run(_migrate(_LegacyStore({"semantic": 3, "episodic": 0}), monkeypatch))
    assert resp.status == 200
    assert config_file() is True, "a migration that produced entries was not recorded"


def test_the_migrate_lock_survives_too(config_file, monkeypatch):
    """🪤 The SECOND survivor, and the one I actually cut: three module-level symbols shared the
    deleted range -- the dead status dict, a faiss-install lock used only by the retired handlers,
    and `_migrate_lock`, which `api_memory_migrate` still needs. mypy caught it (`Name
    "_migrate_lock" is not defined`), which is the real guard. Two migrations asked for at once
    run one after the other."""
    store = _LegacyStore({"semantic": 0, "episodic": 0}, hold=0.2)

    async def both() -> list[web.Response]:
        return list(
            await asyncio.gather(_migrate(store, monkeypatch), _migrate(store, monkeypatch))
        )

    assert [r.status for r in asyncio.run(both())] == [200, 200]
    assert store.most_at_once == 1, "two migrations ran over one store at once"
    # And the lock that WAS only the retired plane's is gone.
    assert not hasattr(mem_mod, "_faiss_install_lock")


def test_migration_is_the_only_thing_that_sets_migrated(config_file, monkeypatch):
    """The correctness claim. Enabling embeddings must not assert that a migration happened: a
    re-index, the reachable path a new embedding model takes, runs to its end and leaves
    `migrated` unset, and writes it through no other door."""
    from personalclaw.dashboard.embedding_reindex import ReindexRegistry

    writes: list[bool] = []
    real = mem_mod._set_migrated

    async def spy(value: bool) -> None:
        writes.append(value)
        await real(value)

    monkeypatch.setattr(mem_mod, "_set_migrated", spy)

    async def reindex() -> str:
        jobs = ReindexRegistry()
        job, error = jobs.start(
            model="probe", knowledge_store=None, memory_store=None, embedder=None
        )
        assert error is None and job is not None
        for _ in range(500):
            if job.status != "running":
                return job.status
            await asyncio.sleep(0.01)
        raise AssertionError("the re-index did not finish")

    assert asyncio.run(reindex()) == "done"
    assert writes == [] and config_file() is None, "the re-index claimed a migration"


def test_migrate_still_gates_on_actually_having_migrated_something(config_file, monkeypatch):
    """Vacuity guard for the rule above: `migrated` is set only when entries were produced, which
    is what makes "migration owns this field" true rather than a slogan. Episodic entries alone
    count too."""
    asyncio.run(_migrate(_LegacyStore({"semantic": 0, "episodic": 0}), monkeypatch))
    assert config_file() is None, "a migration that produced nothing was recorded"
    asyncio.run(_migrate(_LegacyStore({"semantic": 0, "episodic": 2}), monkeypatch))
    assert config_file() is True, "episodic entries alone were not recorded"


# ── the capability claim is verified, not assumed ───────────────────────────────────────────


def test_every_retired_capability_still_has_a_reachable_home():
    """Deleting a plane is only safe if its capabilities survive elsewhere. Each of these is
    served by the model-downloads handlers and wrapped in `api.ts`."""
    from personalclaw.dashboard.handlers import model_downloads as downloads

    delete = handler_for("DELETE", "/api/models/local/ollama/llama3")
    assert delete is downloads.api_local_model_delete, "local-model delete"
    search = handler_for("GET", "/api/models/local/ollama/search")
    assert search is downloads.api_local_model_search, "local-model search"

    api_ts = (REPO / "web/src/lib/api.ts").read_text(encoding="utf-8")
    for wrapper in ("/api/models/local/", "/api/models/downloads", "/api/models/active/"):
        assert wrapper in api_ts, f"{wrapper} has no frontend wrapper"
