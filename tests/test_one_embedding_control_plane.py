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

import inspect
from pathlib import Path

from personalclaw.dashboard import handlers, server
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
RETIRED_PATHS = (
    "/api/memory/embedding-status",
    "/api/memory/enable-embeddings",
    "/api/memory/disable-embeddings",
    "/api/memory/embedding-models",
    "/api/memory/delete-model",
    "/api/memory/activate-model",
)

REPO = Path(__file__).resolve().parents[1]


def test_no_retired_handler_survives_on_the_barrel():
    """Import-level, not a source scan: registration is `handlers.<name>`, so a leftover
    registration would raise AttributeError while the dashboard boots."""
    for name in RETIRED_HANDLERS:
        assert not hasattr(handlers, name), f"{name} is still exported"
        assert not hasattr(mem_mod, name), f"{name} still exists in the memory module"


def test_no_retired_route_is_registered():
    src = inspect.getsource(server)
    for path in RETIRED_PATHS:
        assert path not in src, f"{path} is still registered"


def test_the_dead_setup_state_machine_is_gone():
    """`setup_step`/`setup_error`/`can_retry` were written in three places and read only by the
    orphaned status endpoint, so no consumer could ever observe a non-idle value -- including three
    carefully-worded diagnostics and a "Click Enable to retry" string for a button nothing rendered.
    """
    assert not hasattr(mem_mod, "_embedding_setup_status")
    src = inspect.getsource(mem_mod)
    assert "setup_step" not in src
    assert "can_retry" not in src


# ── the survivor: `migrated` still has exactly one owner ────────────────────────────────────


def test_set_migrated_survives_and_the_migrate_endpoint_still_calls_it():
    """🪤 `_set_migrated` sat INSIDE the deleted block. Cutting the block wholesale would have
    taken it with it and broken the real migration endpoint -- the likeliest way this surgery
    goes wrong, so it is asserted rather than assumed."""
    assert hasattr(mem_mod, "_set_migrated")
    assert "_set_migrated(True)" in inspect.getsource(mem_mod.api_memory_migrate)


def test_the_migrate_lock_survives_too():
    """🪤 The SECOND survivor, and the one I actually cut: three module-level symbols shared the
    deleted range -- the dead status dict, a faiss-install lock used only by the retired handlers,
    and `_migrate_lock`, which `api_memory_migrate` still needs. mypy caught it (`Name
    "_migrate_lock" is not defined`), which is the real guard; this pins it so a future tidy-up of
    this region cannot repeat it silently."""
    assert hasattr(mem_mod, "_migrate_lock")
    assert "_migrate_lock" in inspect.getsource(mem_mod.api_memory_migrate)
    # And the lock that WAS only the retired plane's is gone.
    assert not hasattr(mem_mod, "_faiss_install_lock")


def test_migration_is_the_only_thing_that_sets_migrated():
    """The correctness claim. Enabling embeddings must not assert that a migration happened."""
    src = inspect.getsource(mem_mod)
    setters = [ln.strip() for ln in src.splitlines() if "_set_migrated(True)" in ln]
    assert len(setters) == 1, f"more than one writer of migrated: {setters}"

    # And the coupling was not merely moved to the reachable re-index path.
    reindex = (REPO / "src/personalclaw/dashboard/embedding_reindex.py").read_text(encoding="utf-8")
    assert "migrated" not in reindex, "the reachable path must not claim a migration either"


def test_migrate_still_gates_on_actually_having_migrated_something():
    """Vacuity guard for the rule above: `migrated` is set only when entries were produced, which
    is what makes "migration owns this field" true rather than a slogan."""
    src = inspect.getsource(mem_mod.api_memory_migrate)
    assert 'counts.get("semantic", 0) > 0' in src and 'counts.get("episodic", 0) > 0' in src


# ── the capability claim is verified, not assumed ───────────────────────────────────────────


def test_every_retired_capability_still_has_a_reachable_home():
    """Deleting a plane is only safe if its capabilities survive elsewhere. Each of these is
    registered by the model-downloads/registry handlers and wrapped in `api.ts`."""
    downloads = (REPO / "src/personalclaw/dashboard/handlers/model_downloads.py").read_text(
        encoding="utf-8"
    )
    assert '"/api/models/local/{provider}/{model}"' in downloads, "local-model delete"
    assert '"/api/models/local/{provider}/search"' in downloads, "local-model search"

    api_ts = (REPO / "web/src/lib/api.ts").read_text(encoding="utf-8")
    for wrapper in ("/api/models/local/", "/api/models/downloads", "/api/models/active/"):
        assert wrapper in api_ts, f"{wrapper} has no frontend wrapper"


def test_no_frontend_reference_to_the_retired_plane_remains():
    """The census that justified the deletion, kept executable so it cannot rot."""
    web = REPO / "web/src"
    hits = [
        f"{path.relative_to(REPO)}:{token}"
        for path in web.rglob("*.ts*")
        for token in RETIRED_PATHS
        if token in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert hits == [], f"a frontend caller appeared: {hits}"
