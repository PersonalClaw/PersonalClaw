"""Onboarding progress state (ONBOARDING-UX C1 / OU-1).

Three properties carry this contract, and each has a rail here:

1. **Tolerant reads.** A store written by a client that had none of these fields — or one
   carrying a wrong-typed value — must still load, and the GET must still answer. A
   first-run signal that 500s on a stale file is worse than no signal.
2. **Partial merge at both levels.** Writing one field must not clear the others, nested
   fields included. Without that, every onboarding step would have to read the whole
   document and echo it back, and any two steps racing would lose progress.
3. **Entity state, not config.** The write path is ``POST /api/onboarding/state`` and the
   bytes land in ``entity_settings/onboarding.json`` — never ``config.json``, never the
   ``_EDITABLE_CONFIG`` PATCH allowlist (§2.1).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw import onboarding as ob
from personalclaw.dashboard import handlers_system as hs


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch, tmp_path):
    """Point the whole config home at a tmp dir so the real home is never touched.

    ``PERSONALCLAW_HOME`` rather than a ``config_dir`` patch: the store resolves its path
    through ``entity_routes._entity_settings_path`` -> ``config_dir()`` on every call, and
    the env var is the one lever every such caller honours.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    return tmp_path


def _store_path(home):
    return home / "entity_settings" / "onboarding.json"


def _write_raw(home, payload):
    p = _store_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")


def _req(body):
    r = MagicMock()
    r.json = AsyncMock(return_value=body)
    return r


async def _json(resp):
    return json.loads(resp.body.decode())


# ── 1. defaults + round trip ─────────────────────────────────────────────────


def test_fresh_home_starts_at_the_first_step(_isolate_home):
    assert not _store_path(_isolate_home).exists()
    state = ob.load_onboarding_state()
    assert state == {
        "step": "name",
        "essentials": {"model": None, "search": False, "speech": False, "channel": None},
        "first_success": {"knowledge": False, "trigger": False, "loop": False},
    }


def test_merge_persists_to_entity_settings_not_config(_isolate_home):
    ob.merge_onboarding_state({"step": "essentials", "essentials": {"model": "acme-models"}})

    # The bytes are where §2.1 says they belong…
    on_disk = json.loads(_store_path(_isolate_home).read_text(encoding="utf-8"))
    assert on_disk["step"] == "essentials"
    assert on_disk["essentials"]["model"] == "acme-models"
    # …and NOT in config.json, whose PATCH allowlist this deliberately bypasses.
    cfg = _isolate_home / "config.json"
    assert not cfg.exists() or "onboarding" not in cfg.read_text(encoding="utf-8")


def test_state_survives_a_reload(_isolate_home):
    """A mid-flow reload re-reads from disk — no in-process cache carries the answer."""
    ob.merge_onboarding_state({"step": "first_success", "first_success": {"knowledge": True}})
    # Simulate the reload by dropping every cached module-level value there could be:
    # the store keeps none, so a plain re-read must already agree with disk.
    again = ob.load_onboarding_state()
    assert again["step"] == "first_success"
    assert again["first_success"]["knowledge"] is True


# ── 2. partial merge at both levels ──────────────────────────────────────────


def test_top_level_merge_is_partial(_isolate_home):
    """Write A, POST only B, assert A survives."""
    ob.merge_onboarding_state({"essentials": {"model": "acme-models", "search": True}})
    after = ob.merge_onboarding_state({"step": "first_success"})
    assert after["step"] == "first_success"
    assert after["essentials"] == {
        "model": "acme-models",
        "search": True,
        "speech": False,
        "channel": None,
    }


def test_nested_merge_is_partial(_isolate_home):
    """A patch naming one card must not clear the other two."""
    ob.merge_onboarding_state({"first_success": {"knowledge": True, "loop": True}})
    after = ob.merge_onboarding_state({"first_success": {"trigger": True}})
    assert after["first_success"] == {"knowledge": True, "trigger": True, "loop": True}


def test_nested_merge_can_clear_one_field_explicitly(_isolate_home):
    """Partial means absent-is-untouched, not absent-is-false — an explicit false wins."""
    ob.merge_onboarding_state({"first_success": {"knowledge": True, "trigger": True}})
    after = ob.merge_onboarding_state({"first_success": {"knowledge": False}})
    assert after["first_success"] == {"knowledge": False, "trigger": True, "loop": False}


def test_essentials_model_can_be_nulled(_isolate_home):
    ob.merge_onboarding_state({"essentials": {"model": "acme-models"}})
    after = ob.merge_onboarding_state({"essentials": {"model": None}})
    assert after["essentials"]["model"] is None


# ── 3. tolerant reads ────────────────────────────────────────────────────────


def test_old_client_store_missing_every_new_field_still_loads(_isolate_home):
    """The shape an older client would have left behind: no step/essentials/first_success."""
    _write_raw(_isolate_home, {"some_older_key": "whatever"})
    state = ob.load_onboarding_state()
    assert state == ob.default_state()


def test_wrong_typed_fields_do_not_raise_and_fall_back_per_field(_isolate_home):
    _write_raw(
        _isolate_home,
        {
            "step": 7,  # not a string
            "essentials": "nope",  # not an object
            "first_success": {"knowledge": "yes", "loop": True},  # one bad, one good
        },
    )
    state = ob.load_onboarding_state()
    assert state["step"] == "name"
    assert state["essentials"] == ob.default_state()["essentials"]
    # The bad sibling does not cost us the good one — per-field fallback.
    assert state["first_success"] == {"knowledge": False, "trigger": False, "loop": True}


def test_out_of_domain_step_on_disk_falls_back(_isolate_home):
    _write_raw(_isolate_home, {"step": "some-step-we-retired"})
    assert ob.load_onboarding_state()["step"] == "name"


def test_corrupt_json_and_non_object_json_both_load(_isolate_home):
    p = _store_path(_isolate_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json at all", encoding="utf-8")
    assert ob.load_onboarding_state() == ob.default_state()
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert ob.load_onboarding_state() == ob.default_state()


def test_unknown_on_disk_keys_are_not_leaked_back_out(_isolate_home):
    """Bug #22's lesson: garbage must not ride a read back out to every client."""
    _write_raw(_isolate_home, {"step": "done", "totally_bogus_key_xyz": "junk"})
    assert "totally_bogus_key_xyz" not in ob.load_onboarding_state()


# ── 4. strict writes ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "patch",
    [
        {"totally_bogus_key_xyz": 1},
        {"step": "not-a-step"},
        {"step": 3},
        {"essentials": {"bogus": True}},
        {"essentials": {"search": "yes"}},
        {"essentials": {"model": 42}},
        {"essentials": []},
        {"first_success": {"knowledge": "true"}},
        {"first_success": {"bogus_card": True}},
        ["not", "an", "object"],
    ],
)
def test_bad_patches_are_rejected(_isolate_home, patch):
    with pytest.raises(ValueError):
        ob.merge_onboarding_state(patch)


def test_a_rejected_patch_writes_nothing(_isolate_home):
    ob.merge_onboarding_state({"step": "essentials"})
    with pytest.raises(ValueError):
        ob.merge_onboarding_state({"step": "bogus"})
    assert ob.load_onboarding_state()["step"] == "essentials"


def test_every_declared_step_is_writable(_isolate_home):
    """No declared step is unreachable — a step nobody can write is an inert enum."""
    for step in ob.STEPS:
        assert ob.merge_onboarding_state({"step": step})["step"] == step


def test_every_step_of_the_flow_has_a_resume_point():
    """The stored vocabulary covers all five UI steps, plus the terminal ``done``.

    The first version named only three of them, on the reading that a point means "the next step
    you have not finished" — which left the import step and the recap with no id. Driven on a fresh
    home that cost real progress: stopped on the import step the file still said ``name``, so a
    reload restarted at the beginning and threw away the typed name; stopped on the recap it said
    ``first_success``, so a reload walked the user BACK a step. The field is the high-water mark
    now, so every step the user can stand on needs somewhere to be recorded.

    ``first_success`` is the ``try`` step's stored spelling — the frontend owns that one mapping in
    ``web/src/app/onboarding/steps.ts``, whose ``stepMachine.test.ts`` round-trips it against this
    tuple from the other side.
    """
    assert ob.STEPS == ("name", "import", "essentials", "first_success", "ready", "done")


def test_the_three_older_values_still_load(_isolate_home):
    """Extending the domain is compatible: nothing an older client wrote became unreadable."""
    for step in ("name", "essentials", "first_success", "done"):
        _write_raw(_isolate_home, {"step": step})
        assert ob.load_onboarding_state()["step"] == step


# ── 5. the HTTP surface ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_carries_progress_beside_the_readiness_triple(_isolate_home):
    ob.merge_onboarding_state({"step": "first_success", "essentials": {"speech": True}})
    data = await _json(await hs.api_onboarding(_req({})))
    # The pre-existing contract an old client reads is untouched…
    for key in ("needs_model", "has_model_provider", "has_chat_binding"):
        assert key in data
    # …and the new fields ride alongside it.
    assert data["step"] == "first_success"
    assert data["essentials"]["speech"] is True
    assert data["first_success"] == {"knowledge": False, "trigger": False, "loop": False}


@pytest.mark.asyncio
async def test_get_carries_the_bound_chat_model_not_only_a_flag(_isolate_home, monkeypatch):
    """#3528 — the GET must say WHICH chat model is bound, not merely that one is.

    The flow persists ``essentials.model``, which is the **app** that provides the model
    (``ollama-models``). With only a boolean to go on, a re-entered first run had nothing
    else to render under the words "Chat model" and told the user their model was that app
    name. The refs are what ``active_models.json`` holds, so the route reports them — and
    ``has_chat_binding`` is derived from the same read, so the two cannot disagree.
    """
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.active_model_refs",
        lambda use_case: ["Local Ollama:qwen2.5vl:7b"] if use_case == "chat" else [],
    )
    ob.merge_onboarding_state({"essentials": {"model": "ollama-models"}})
    data = await _json(await hs.api_onboarding(_req({})))
    assert data["chat_model_refs"] == ["Local Ollama:qwen2.5vl:7b"]
    assert data["has_chat_binding"] is True
    # The app name is still recorded — as the app it is, under the field that means that.
    assert data["essentials"]["model"] == "ollama-models"


@pytest.mark.asyncio
async def test_get_reports_no_bound_chat_model_when_nothing_is_bound(_isolate_home, monkeypatch):
    """An empty chain is the honest answer, and the flag agrees with it.

    This is the case where resolution comes from the implicit "first capable configured
    provider" rule, so there is no model the user chose — and a surface that named one
    would be inventing a choice.
    """
    monkeypatch.setattr("personalclaw.providers.use_cases.active_model_refs", lambda use_case: [])
    data = await _json(await hs.api_onboarding(_req({})))
    assert data["chat_model_refs"] == []
    assert data["has_chat_binding"] is False


@pytest.mark.asyncio
async def test_chat_is_bundled_floor_is_true_only_when_a_floor_is_all_there_is(_isolate_home):
    """OU-14 — the honesty signal, both directions plus the mixed case.

    Three states, three answers, because the whole value of the flag is that it distinguishes
    them: nothing capable at all (the OU-12 setup state), a FLOOR and nothing else (the
    bundled weight is about to answer, and the chat surface must say so), and a floor beside a
    real provider (the user bound something — the floor loses resolution, so the warning must
    go away). A flag that fired on the third case would put a "you're on a toy model" banner on
    every properly-configured home.
    """
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.llm.registry import ProviderEntry, get_default_registry

    registry = get_default_registry()

    def _cap(type_: str) -> ProviderCapability:
        return ProviderCapability(
            type=type_,
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=0,
        )

    for name in ("ou14-floor-type", "ou14-real-type"):
        try:
            registry.register_type(_cap(name), lambda **_kw: object())
        except Exception:  # noqa: BLE001 — already registered by a sibling test
            pass
    try:
        assert (await _json(await hs.api_onboarding(_req({}))))["chat_is_bundled_floor"] is False

        registry.register_entry(
            ProviderEntry(
                name="ou14-floor",
                type="ou14-floor-type",
                model="tiny",
                declared_capabilities=frozenset({Capability.CHAT}),
                floor=True,
            )
        )
        data = await _json(await hs.api_onboarding(_req({})))
        assert data["chat_is_bundled_floor"] is True
        assert data["needs_model"] is False, "a floor resolves chat, so the nudge must be gone"

        registry.register_entry(
            ProviderEntry(
                name="ou14-real",
                type="ou14-real-type",
                model="big",
                declared_capabilities=frozenset({Capability.CHAT}),
            )
        )
        data = await _json(await hs.api_onboarding(_req({})))
        assert data["chat_is_bundled_floor"] is False, (
            "a real provider is configured, so the floor no longer answers and the banner must "
            "not be shown"
        )
    finally:
        registry.unregister_entry("ou14-floor")
        registry.unregister_entry("ou14-real")


@pytest.mark.asyncio
async def test_chat_download_offer_names_the_size_and_retires_once_it_is_downloaded(
    _isolate_home, monkeypatch
):
    """OU-14 — the escape hatch from the setup state, and the number that makes it honest.

    The weight is not in the wheel (owner decision 2026-09-24), so on a fresh install the true
    answer is neither "you have a model" nor "go configure a provider": it is "there is a
    one-time download and it is this big". This asserts the payload carries the SIZE — a
    download offer with no number is minutes of silent progress on a slow connection — and that
    it disappears once the model is on disk, because an offer to download something you already
    have is a dead control.

    Registered through core's generic local-model registry under TWO provider names, neither of
    which has anything to do with any app in this tree, and the route must name whichever one
    answered: the route must not know which app answers, or the provider boundary has a hole in
    it. Two names rather than one because the real bundled app is registered under one name too,
    so a single case cannot tell "reports the registry's answer" from "reports that one app".

    The registry's CONTENTS are this test's own input, cleared first, and that is load-bearing
    rather than tidiness. `local_models.registry` is process-global and its writer is a GATEWAY
    BOOT path (`ModelTypeHandler._register_local`) that never unregisters, so any test booting a
    dashboard enrols every native model app for the rest of the worker — and the route walks the
    registry in registration order. Asserting "the registry answers with MY provider" while the
    rest of the suite decides what else is in it is not an assertion about the route: it read
    `bundled-chat` on CI shard 4 of #3441 and looked exactly like an app name leaking into a core
    payload. (The leak itself is undone by `conftest._restore_local_model_registry`; owning the
    input is what makes the assertion here measure the route.)
    """
    from personalclaw.local_models import registry as lm_registry
    from personalclaw.local_models.provider import LocalModel, LocalModelProvider

    class _Fake(LocalModelProvider):
        def __init__(self, provider_name: str) -> None:
            self._provider_name = provider_name
            self.present = False

        @property
        def name(self) -> str:
            return self._provider_name

        @property
        def display_name(self) -> str:
            return "OU-14 fake"

        async def is_available(self) -> bool:
            return True

        async def list_models(self) -> list[LocalModel]:
            return [
                LocalModel(
                    name="tiny-chat",
                    size_mb=138.125,
                    description="a small chat model",
                    downloaded=self.present,
                    capabilities=["chat"],
                    license="Apache-2.0",
                ),
                # A second model with a DIFFERENT capability, so the probe is shown to filter
                # by capability rather than offering the first row it finds.
                LocalModel(name="not-chat", size_mb=9, downloaded=False, capabilities=["stt"]),
            ]

        async def download_model(self, model_name: str) -> bool:
            self.present = True
            return True

        async def delete_model(self, model_name: str) -> bool:
            self.present = False
            return True

    monkeypatch.setattr(lm_registry, "_providers", {})
    monkeypatch.setattr(lm_registry, "_capabilities", {})

    # The control for everything below, and the contract for a client with no offer: with no
    # local-model provider registered at all the field is present and null, not absent — a client
    # distinguishing "no offer" from "old server" reads the key, not its absence.
    bare = await _json(await hs.api_onboarding(_req({})))
    assert "chat_download_offer" in bare
    assert bare["chat_download_offer"] is None

    for provider_name in ("ou14-fake-local", "ou14-unrelated-fake"):
        fake = _Fake(provider_name)
        lm_registry.register_provider(fake, capabilities=["chat"])
        try:
            data = await _json(await hs.api_onboarding(_req({})))
            offer = data["chat_download_offer"]
            assert offer is not None, "nothing is bound and a model is downloadable — offer it"
            assert offer["provider"] == provider_name
            assert offer["model"] == "tiny-chat"
            assert offer["bytes"] == int(138.125 * 1024 * 1024)
            assert offer["licence"] == "Apache-2.0"

            await fake.download_model("tiny-chat")
            after = await _json(await hs.api_onboarding(_req({})))
            assert (
                after["chat_download_offer"] is None
            ), "a downloaded model must not still be offered as a download"
        finally:
            lm_registry.unregister_provider(provider_name)


def _offer_fixtures(monkeypatch):
    """A fixed-catalog provider with an undownloaded chat model, and a SEARCHABLE one — whose
    ``list_models`` stands in for asking a server what it has pulled — registered first, so a
    probe that walked every catalog would reach it before the offer."""
    from personalclaw.local_models import registry as lm_registry
    from personalclaw.local_models.provider import LocalModel, LocalModelProvider

    class _Fixed(LocalModelProvider):
        name = "ou14-fixed"  # type: ignore[assignment]
        display_name = "fixed catalog"  # type: ignore[assignment]

        async def is_available(self) -> bool:
            return True

        async def list_models(self) -> list[LocalModel]:
            return [
                LocalModel(
                    name="tiny-chat",
                    size_mb=10,
                    downloaded=False,
                    capabilities=["chat"],
                    license="Apache-2.0",
                )
            ]

        async def download_model(self, model_name: str) -> bool:
            return True

        async def delete_model(self, model_name: str) -> bool:
            return True

    class _Server(_Fixed):
        searchable = True
        calls = 0

        async def list_models(self) -> list[LocalModel]:
            type(self).calls += 1
            return [LocalModel(name="pulled", size_mb=1, downloaded=True, capabilities=["chat"])]

    monkeypatch.setattr(lm_registry, "_providers", {})
    monkeypatch.setattr(lm_registry, "_capabilities", {})
    lm_registry.register_provider(_Server(), capabilities=["chat"], name="ou14-server")
    lm_registry.register_provider(_Fixed(), capabilities=["chat"], name="ou14-fixed")
    return _Server


@pytest.mark.asyncio
async def test_the_download_is_offered_when_a_provider_reads_as_set_up(_isolate_home, monkeypatch):
    """The offer is an option, not a readiness claim, so it does not wait for ``needs_model``.

    Measured on a real image: save Ollama at an address nothing listens on and ``needs_model``
    turns false, because the readiness probe makes no network call and a configured provider
    reads as set up to it. The offer was computed only while ``needs_model`` was true, so
    onboarding's "Pick a different provider" had no no-account download on exactly the home
    whose provider was down.
    """
    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.llm.registry import ProviderEntry, get_default_registry

    _offer_fixtures(monkeypatch)
    registry = get_default_registry()
    try:
        registry.register_type(
            ProviderCapability(
                type="ou14-configured-type",
                capabilities=frozenset({Capability.CHAT}),
                supports_streaming=True,
                supports_tools=False,
                supports_embeddings=False,
                supports_vision=False,
                max_context_tokens=0,
            ),
            lambda **_kw: object(),
        )
    except Exception:  # noqa: BLE001 — already registered by a sibling test
        pass
    registry.register_entry(
        ProviderEntry(
            name="ou14-configured",
            type="ou14-configured-type",
            model="m",
            declared_capabilities=frozenset({Capability.CHAT}),
        )
    )
    try:
        data = await _json(await hs.api_onboarding(_req({})))
        assert data["needs_model"] is False, "precondition: a configured provider reads as set up"
        offer = data["chat_download_offer"]
        assert offer is not None, "the small model is not on disk, so it is on offer"
        assert offer["provider"] == "ou14-fixed"
    finally:
        registry.unregister_entry("ou14-configured")


@pytest.mark.asyncio
async def test_the_offer_reads_no_searchable_catalog(_isolate_home, monkeypatch):
    """No network call on this read. A ``searchable`` provider's ``list_models`` asks its server
    what it already pulled (the manager-backed Ollama adapter), and by the ``LocalModelProvider``
    contract it returns only models already present, so it can never hold an offer."""
    server = _offer_fixtures(monkeypatch)
    data = await _json(await hs.api_onboarding(_req({})))
    assert data["chat_download_offer"] is not None
    assert data["chat_download_offer"]["provider"] == "ou14-fixed"
    assert server.calls == 0, "the searchable catalog was read"


@pytest.mark.asyncio
async def test_get_still_answers_over_a_corrupt_store(_isolate_home):
    p = _store_path(_isolate_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("]]] broken", encoding="utf-8")
    data = await _json(await hs.api_onboarding(_req({})))
    assert data["needs_model"] is True  # readiness still computed
    assert data["step"] == "name"


@pytest.mark.asyncio
async def test_post_round_trips_through_the_get(_isolate_home):
    resp = await hs.api_onboarding_state(_req({"step": "done", "first_success": {"loop": True}}))
    assert resp.status == 200
    posted = await _json(resp)
    assert posted["ok"] is True
    assert posted["state"]["step"] == "done"
    got = await _json(await hs.api_onboarding(_req({})))
    assert got["step"] == "done"
    assert got["first_success"]["loop"] is True


@pytest.mark.asyncio
async def test_post_partial_merge_over_http(_isolate_home):
    await hs.api_onboarding_state(_req({"essentials": {"model": "acme-models"}}))
    resp = await hs.api_onboarding_state(_req({"first_success": {"knowledge": True}}))
    state = (await _json(resp))["state"]
    assert state["essentials"]["model"] == "acme-models"
    assert state["first_success"]["knowledge"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [{"nope": 1}, {"step": "bogus"}, {"essentials": {"search": "yes"}}, "a string"]
)
async def test_post_rejects_bad_bodies_with_400(_isolate_home, body):
    resp = await hs.api_onboarding_state(_req(body))
    assert resp.status == 400
    assert "error" in await _json(resp)


@pytest.mark.asyncio
async def test_post_rejects_unparseable_json_with_400(_isolate_home):
    r = MagicMock()
    r.json = AsyncMock(side_effect=ValueError("boom"))
    resp = await hs.api_onboarding_state(r)
    assert resp.status == 400


def test_post_route_is_registered():
    """The handler must be reachable — a store with no route is inert."""
    import ast
    from pathlib import Path

    import personalclaw.dashboard.server as srv

    tree = ast.parse(Path(srv.__file__).read_text(encoding="utf-8"))
    posts = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_post"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert "/api/onboarding/state" in posts


def test_onboarding_is_not_wired_into_config(_isolate_home):
    """§2.1: this is entity state. It must not reach the config allowlist or dataclass."""
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    assert not hasattr(cfg, "onboarding_step")
    assert not hasattr(cfg, "first_success")
    from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

    assert not any(k.startswith("onboarding") or k == "first_success" for k in _EDITABLE_CONFIG)
