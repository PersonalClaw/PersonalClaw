"""#3410 — a multiInstance MODEL app owns ONE local-model registry entry, over ALL of it.

The defect had two halves and the second is the data-loss-shaped one:

* **register** keyed the local-model registry by the APP name for every provider in the
  list it was handed. A ``multiInstance`` app's ``create()`` returns one provider per
  enabled instance, so N instances wrote the same key in list order and the LAST one
  silently won — and since ``list_instances`` orders by a random hex id, *which* instance
  owned the key flipped when an instance was added or removed. A healthy Ollama read
  "not available on this machine" because a sibling registered after it.
* **deregister** called ``unregister_provider(ext.name)`` once per provider in the list,
  so tearing down ONE instance of two EMPTIED the app's entry and the surviving,
  still-enabled instance became unreachable on the download, health and binding surfaces.

The fix is not a longer key. The registry key is a path segment on the
``/api/models/local/{provider}/…`` routes and the provider half of every
``provider:model`` binding ref, and ``use_cases.split_ref`` splits a ref on the FIRST
colon — so an ``app:instance`` key cannot round-trip through a ref, and
``_prune_removed_providers`` (which keeps a ref only when its first-colon prefix names a
known provider) would have **deleted every existing binding** for that app. The app keeps
one identity and that identity now answers for all of its instances, so there is no
"which instance won" question left to answer nondeterministically.

These drive the REAL ``ModelTypeHandler.register`` / ``.deregister`` against the REAL
``local_models.registry`` — no gateway, because the collapse was in the registration.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.local_models import registry as lm_registry
from personalclaw.local_models.multi_instance import MultiInstanceLocalProvider
from personalclaw.providers.registry import ModelTypeHandler, RegisteredProvider


class _SharedNameProvider:
    """An instance provider whose ``name``/``display_name`` are READ-ONLY properties.

    This is the shape that made the defect invisible: ``create()`` tries to tag each
    instance with ``f"{ext.name}:{inst.id}"`` behind ``if not hasattr(provider, "name")``,
    and the real Ollama app declares ``name`` as a property — so ``hasattr`` is True, the
    tag never applies, and every instance answers the same string. Core's own
    ``instance_id``/``instance_label`` stamps are what carry identity instead.
    """

    def __init__(self, endpoint: str, *, healthy: bool, models: list[str] | None = None) -> None:
        self.endpoint = endpoint
        self._healthy = healthy
        self._models = list(models or [])
        self.deleted: list[str] = []
        self.downloaded_calls: list[str] = []
        self.searchable = True

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def display_name(self) -> str:
        return "Ollama"

    async def is_available(self) -> bool:
        return self._healthy

    async def availability_detail(self) -> tuple[bool, str]:
        return (True, "ready") if self._healthy else (False, "not available on this machine")

    async def list_models(self):
        from personalclaw.local_models.provider import LocalModel

        return [LocalModel(name=m, downloaded=True) for m in self._models]

    async def search_models(self, query: str):
        from personalclaw.local_models.provider import LocalModel

        return [LocalModel(name=f"{query}-remote", downloaded=False)]

    async def download_model(self, model_name: str) -> bool:
        self.downloaded_calls.append(model_name)
        self._models.append(model_name)
        return True

    async def delete_model(self, model_name: str) -> bool:
        self.deleted.append(model_name)
        return True


class _Cfg:
    type = "model"
    multiInstance = True
    capabilities = ["chat", "embedding"]
    implementation = "provider:create"
    settingsSchema: dict = {}


class _SingletonCfg(_Cfg):
    multiInstance = False


APP = "ollama-models"
# The two ids the filed probe measured, ascending — `list_instances` sorts filenames, so
# this IS the order the handler receives them in.
HEALTHY_ID = "242ede4dc209"
DEAD_ID = "92e5d998b13d"


def _ext(cfg: object = None) -> RegisteredProvider:
    return RegisteredProvider(name=APP, manifest=None, provider_config=cfg or _Cfg(), enabled=True)


def _instance(endpoint: str, iid: str, label: str, *, healthy: bool, models=None):
    """A provider tagged the way ``ModelTypeHandler.create`` tags one."""
    provider = _SharedNameProvider(endpoint, healthy=healthy, models=models)
    provider.instance_id = iid
    provider.instance_label = label
    return provider


@pytest.fixture(autouse=True)
def clean_registry():
    """Snapshot/restore the module-global local-model registry (no autouse guard exists)."""
    providers = dict(lm_registry._providers)
    caps = dict(lm_registry._capabilities)
    yield
    lm_registry._providers.clear()
    lm_registry._providers.update(providers)
    lm_registry._capabilities.clear()
    lm_registry._capabilities.update(caps)


@pytest.fixture
def two_instances():
    healthy = _instance("http://127.0.0.1:11434", HEALTHY_ID, "laptop", healthy=True, models=["a"])
    dead = _instance("http://127.0.0.1:1", DEAD_ID, "deadbox", healthy=False, models=[])
    return healthy, dead


# ── Half 1: the collapse ─────────────────────────────────────────────────────


def test_both_enabled_instances_are_reachable_under_the_one_app_key(two_instances):
    healthy, dead = two_instances
    ModelTypeHandler().register(_ext(), [healthy, dead])

    assert list(lm_registry._providers) == [APP], "the app keeps ONE key, not one per instance"
    entry = lm_registry.get_provider(APP)
    assert isinstance(entry, MultiInstanceLocalProvider)
    # Neither instance was overwritten: both are members of the app's identity.
    assert entry.instance_ids == (HEALTHY_ID, DEAD_ID)
    assert {p for _i, _l, p in entry.instances} == {healthy, dead}


@pytest.mark.parametrize("order", ["healthy_first", "dead_first"])
def test_a_dead_sibling_no_longer_makes_a_healthy_instance_read_unavailable(two_instances, order):
    """The filed symptom, in BOTH list orders — the answer must not depend on the order.

    Measured on the unfixed code: ``[healthy, dead]`` answered ``ok: false`` ("not
    available on this machine") and ``[dead, healthy]`` answered ``ok: true``, from the
    same two endpoints.
    """
    healthy, dead = two_instances
    providers = [healthy, dead] if order == "healthy_first" else [dead, healthy]
    ModelTypeHandler().register(_ext(), providers)
    entry = lm_registry.get_provider(APP)

    assert asyncio.run(entry.is_available()) is True
    ok, message = asyncio.run(entry.availability_detail())
    assert ok is True
    # And the multiplicity is now VISIBLE — the missing surface the issue names.
    assert "1 of 2 instances ready" in message
    assert "laptop: ready" in message
    assert "deadbox: not available on this machine" in message


def test_list_order_does_not_change_any_app_level_answer(two_instances):
    """Same two instances, opposite list order, identical answers on every surface."""
    healthy, dead = two_instances
    answers = []
    for providers in ([healthy, dead], [dead, healthy]):
        lm_registry._providers.clear()
        lm_registry._capabilities.clear()
        ModelTypeHandler().register(_ext(), providers)
        entry = lm_registry.get_provider(APP)
        answers.append(
            (
                asyncio.run(entry.is_available()),
                asyncio.run(entry.availability_detail())[0],
                sorted(m.name for m in asyncio.run(entry.list_models())),
                sorted(m.name for m in asyncio.run(entry.search_models("q"))),
                sorted(entry.instance_ids),
            )
        )
    assert answers[0] == answers[1], answers


def test_the_same_scenario_twice_resolves_identically(two_instances):
    """Determinism, stated as a property: two runs of one scenario, byte-identical.

    Nothing here may read dict iteration order, a clock, or a random id — so a repeat of
    the same registration must produce the same keys, the same member order, the same
    availability verdict and the same catalog.
    """
    healthy, dead = two_instances
    runs = []
    for _ in range(2):
        lm_registry._providers.clear()
        lm_registry._capabilities.clear()
        ModelTypeHandler().register(_ext(), [healthy, dead])
        entry = lm_registry.get_provider(APP)
        runs.append(
            {
                "keys": sorted(lm_registry._providers),
                "caps": lm_registry.capabilities_for(APP),
                "members": entry.instance_ids,
                "display": entry.display_name,
                "available": asyncio.run(entry.is_available()),
                "detail": asyncio.run(entry.availability_detail()),
                "models": [m.name for m in asyncio.run(entry.list_models())],
            }
        )
    assert runs[0] == runs[1], runs


def test_health_search_and_binding_all_describe_the_same_instance_set(two_instances):
    """The three surfaces that used to disagree now answer from one member list."""
    from personalclaw.providers import use_cases as uc

    healthy, dead = two_instances
    ModelTypeHandler().register(_ext(), [healthy, dead])
    entry = lm_registry.get_provider(APP)

    # health
    assert asyncio.run(entry.availability_detail())[0] is True
    # search — the union, so a model offered by either endpoint is findable
    assert [m.name for m in asyncio.run(entry.search_models("llama"))] == ["llama-remote"]
    # binding eligibility reads `_key_for` over `list_providers()`
    assert lm_registry._key_for(entry) == APP
    assert APP in uc._dynamic_media_provider_names()


# ── Half 2: the mirror — a teardown of ONE instance must not empty the entry ──


def test_deregistering_one_of_two_instances_leaves_the_other_registered(two_instances):
    """The data-loss half. On the unfixed code this left the registry EMPTY."""
    healthy, dead = two_instances
    handler = ModelTypeHandler()
    handler.register(_ext(), [healthy, dead])

    handler.deregister(_ext(), [dead])

    assert list(lm_registry._providers) == [APP], "the surviving instance must stay reachable"
    entry = lm_registry.get_provider(APP)
    assert entry.instance_ids == (HEALTHY_ID,)
    assert asyncio.run(entry.is_available()) is True
    # The capability set survives the rewrite, so binding eligibility is unchanged.
    assert lm_registry.capabilities_for(APP) == ["chat", "embedding"]


def test_deregistering_the_healthy_one_leaves_the_dead_one_registered(two_instances):
    """Symmetry: which instance is torn down is not a special case."""
    healthy, dead = two_instances
    handler = ModelTypeHandler()
    handler.register(_ext(), [healthy, dead])

    handler.deregister(_ext(), [healthy])

    assert lm_registry.get_provider(APP) is not None
    assert lm_registry.get_provider(APP).instance_ids == (DEAD_ID,)
    assert asyncio.run(lm_registry.get_provider(APP).is_available()) is False


def test_deregistering_the_last_instance_removes_the_app(two_instances):
    """The ONLY case in which the app leaves the registry."""
    healthy, dead = two_instances
    handler = ModelTypeHandler()
    handler.register(_ext(), [healthy, dead])

    handler.deregister(_ext(), [healthy, dead])

    assert lm_registry.get_provider(APP) is None
    assert APP not in lm_registry._providers


def test_a_full_disable_enable_cycle_rebuilds_from_the_surviving_instance(two_instances):
    """What `_refresh_multi_instance_provider_safe` actually does on an instance delete."""
    healthy, dead = two_instances
    handler = ModelTypeHandler()
    handler.register(_ext(), [healthy, dead])

    handler.deregister(_ext(), [healthy, dead])  # disable(name)
    handler.register(_ext(), [healthy])  # enable(name) → create() re-reads disk

    entry = lm_registry.get_provider(APP)
    assert entry.instance_ids == (HEALTHY_ID,)
    assert entry.display_name == "Ollama", "a single instance keeps the plain label"


# ── The aggregate's own semantics ─────────────────────────────────────────────


def test_the_catalog_is_the_union_and_downloaded_means_present_everywhere():
    a = _instance("http://a", "aaa", "a", healthy=True, models=["shared", "only-a"])
    b = _instance("http://b", "bbb", "b", healthy=True, models=["shared"])
    ModelTypeHandler().register(_ext(), [a, b])
    models = {m.name: m for m in asyncio.run(lm_registry.get_provider(APP).list_models())}

    assert sorted(models) == ["only-a", "shared"]
    assert models["shared"].downloaded is True, "on both instances"
    assert models["only-a"].downloaded is False, "on one of two — still offer the download"


def test_a_write_fans_out_to_every_instance_rather_than_picking_one():
    """Picking one instance to pull onto is the arbitrary choice this class removes."""
    a = _instance("http://a", "aaa", "a", healthy=True)
    b = _instance("http://b", "bbb", "b", healthy=True)
    entry_ext = _ext()
    ModelTypeHandler().register(entry_ext, [a, b])
    entry = lm_registry.get_provider(APP)

    assert asyncio.run(entry.download_model("qwen3:8b")) is True
    assert a.downloaded_calls == ["qwen3:8b"] and b.downloaded_calls == ["qwen3:8b"]
    assert asyncio.run(entry.delete_model("qwen3:8b")) is True
    assert a.deleted == ["qwen3:8b"] and b.deleted == ["qwen3:8b"]


def test_a_refusing_instance_raises_instead_of_reporting_a_partial_success():
    """A ``LiveWriteDisabled``-shaped refusal must reach the caller, not become ``False``."""

    class _Refuses(_SharedNameProvider):
        async def delete_model(self, model_name: str) -> bool:
            raise RuntimeError("live writes disabled")

    a = _instance("http://a", "aaa", "a", healthy=True)
    b = _Refuses("http://b", healthy=True)
    b.instance_id, b.instance_label = "bbb", "b"
    ModelTypeHandler().register(_ext(), [a, b])

    with pytest.raises(RuntimeError, match="live writes disabled"):
        asyncio.run(lm_registry.get_provider(APP).delete_model("x"))


def test_an_unreadable_instance_catalog_does_not_blank_the_card():
    class _Raises(_SharedNameProvider):
        async def list_models(self):
            raise OSError("connection refused")

    a = _instance("http://a", "aaa", "a", healthy=True, models=["kept"])
    b = _Raises("http://b", healthy=False)
    b.instance_id, b.instance_label = "bbb", "b"
    ModelTypeHandler().register(_ext(), [a, b])

    models = asyncio.run(lm_registry.get_provider(APP).list_models())
    assert [m.name for m in models] == ["kept"]
    # The instance that could not answer gets no vote on `downloaded`.
    assert models[0].downloaded is True


def test_an_untagged_provider_pair_still_gets_distinct_members():
    """``register`` called directly with untagged providers must not re-collapse."""
    a = _SharedNameProvider("http://a", healthy=True)
    b = _SharedNameProvider("http://b", healthy=False)
    ModelTypeHandler().register(_ext(), [a, b])
    entry = lm_registry.get_provider(APP)
    assert len(entry.instance_ids) == 2
    assert len(set(entry.instance_ids)) == 2, "identity falls back to the object, not the name"


def test_a_singleton_model_app_is_registered_directly_not_wrapped():
    """Only ``multiInstance`` means "these are instances" — a singleton is unchanged."""
    only = _SharedNameProvider("http://one", healthy=True)
    ModelTypeHandler().register(_ext(_SingletonCfg()), only)
    assert lm_registry.get_provider(APP) is only

    ModelTypeHandler().deregister(_ext(_SingletonCfg()), only)
    assert lm_registry.get_provider(APP) is None
