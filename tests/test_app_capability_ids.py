"""Each provider an app contributes has an id and a label, and the registry keeps it by them.

An app could already list several providers, but they had no identity: the registry stored the
first under the app's name and hung the rest off it, so one could not be listed, switched on or off
or rebuilt without the others, and two of one type in one app collided. Now each provider is a
capability, ``<app>/<id>``, with a label a person reads (#3841).

Old manifests keep working (compatibility first, 2026-10-28): the released v0.3.0 installs
first-party apps from apps ``main``, so the singular ``provider`` is read as the first entry, and a
provider without an ``id`` gets one derived from its type. An app is held off only where that
cannot name each capability apart.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from personalclaw.apps.manifest import AppManifest
from personalclaw.providers.registry import ProviderRegistry


def _manifest(**fields: Any) -> AppManifest:
    base: dict[str, Any] = {
        "name": "demo",
        "version": "1.0.0",
        "displayName": "Demo",
        "description": "A demo app.",
    }
    base.update(fields)
    return AppManifest.from_dict(base)


def _p(type_: str, impl: str = "provider:create", **extra: Any) -> dict[str, Any]:
    return {"type": type_, "implementation": impl, **extra}


# ── ids and labels in the manifest ───────────────────────────────────────────


def test_a_provider_s_id_label_and_description_are_read_and_written_back() -> None:
    raw = _p("channel", id="channel", label="Chat with your agent", description="In Slack.")
    raw["renamedFrom"] = ["chat"]
    m = _manifest(providers=[raw], retiredCapabilities=["old-inbox"])
    (cfg,) = m.all_providers()
    assert (cfg.id, cfg.label, cfg.description, cfg.renamedFrom) == (
        "channel",
        "Chat with your agent",
        "In Slack.",
        ["chat"],
    )
    assert m.retiredCapabilities == ["old-inbox"]
    again = m.to_dict()
    assert again["providers"][0]["id"] == "channel"
    assert again["providers"][0]["renamedFrom"] == ["chat"]
    assert again["retiredCapabilities"] == ["old-inbox"]
    assert AppManifest.from_dict(again).to_dict() == again


def test_a_manifest_without_ids_round_trips_without_growing_any() -> None:
    raw = {
        "name": "demo",
        "version": "1.0.0",
        "displayName": "Demo",
        "description": "A demo app.",
        "provider": _p("channel"),
        "providers": [_p("trigger_source", "provider:create_source")],
    }
    assert AppManifest.from_dict(raw).to_dict() == raw


def test_an_old_manifest_s_providers_get_ids_from_their_types() -> None:
    m = _manifest(
        provider=_p("channel"),
        providers=[_p("trigger_source", "provider:source"), _p("inbox", "provider:inbox")],
    )
    assert m.capability_ids() == ["channel", "triggers", "inbox"]
    assert m.validate() == []


@pytest.mark.parametrize(
    ("type_", "derived"),
    [
        ("tool", "tools"),
        ("trigger", "automations"),
        ("vector_store", "vector-store"),
        ("notification", "notifications"),
        ("model", "model"),
        ("duty_gate", "duty-gate"),
    ],
)
def test_each_type_derives_the_id_the_first_party_apps_use(type_: str, derived: str) -> None:
    assert _manifest(provider=_p(type_)).capability_ids() == [derived]


def test_a_declared_id_wins_over_the_derived_one() -> None:
    m = _manifest(provider=_p("model", id="chat", label="Chat models"))
    assert m.capability_ids() == ["chat"]


def test_two_providers_of_one_type_without_ids_cannot_be_told_apart() -> None:
    m = _manifest(providers=[_p("channel", "a:create"), _p("channel", "b:create")])
    with pytest.raises(ValueError, match="channel"):
        m.capability_ids()
    assert any("give each an id" in e for e in m.validate())


def test_two_providers_of_one_type_with_ids_are_two_capabilities() -> None:
    m = _manifest(
        providers=[
            _p("model", "a:create", id="chat", label="Chat models"),
            _p("model", "b:create", id="embeddings", label="Text embeddings"),
        ]
    )
    assert m.capability_ids() == ["chat", "embeddings"]
    assert m.validate() == []


@pytest.mark.parametrize(
    ("providers", "fields", "named"),
    [
        ([_p("channel", id="Chat_Channel", label="Chat")], {}, "providers[0].id"),
        (
            [_p("channel", id="chat", label="Chat"), _p("inbox", id="chat", label="Mail")],
            {},
            "providers[1].id",
        ),
        (
            [_p("channel", label="Chat"), _p("inbox", id="channel", label="Mail")],
            {},
            "providers[1].id",
        ),
        ([_p("channel", id="chat", label="chat")], {}, "providers[0].label"),
        ([_p("channel", id="chat", label="Channel")], {}, "providers[0].label"),
        (
            [_p("channel", id="chat", label="Chat"), _p("inbox", id="mail", label="Chat")],
            {},
            "providers[1].label",
        ),
        (
            [
                _p("channel", id="chat", label="Chat"),
                _p("inbox", id="mail", label="Mail", renamedFrom=["chat"]),
            ],
            {},
            "providers[1].renamedFrom",
        ),
        (
            [_p("channel", id="chat", label="Chat")],
            {"retiredCapabilities": ["chat"]},
            "providers[0].id",
        ),
        (
            [_p("channel", id="chat", label="Chat", renamedFrom=["old"])],
            {"retiredCapabilities": ["old"]},
            "providers[0].renamedFrom",
        ),
        ([_p("channel", id="chat", label="Chat")], {"retiredCapabilities": ["Old"]}, "retiredCap"),
        (
            [
                _p("trigger_source", "a:src", id="dm", label="Direct messages", capabilities=["m"]),
                _p("trigger_source", "b:src", id="all", label="All messages", capabilities=["m"]),
            ],
            {},
            "providers[1].capabilities",
        ),
        (
            [_p("model", id="chat", label="Chat models", renamedFrom="model")],
            {},
            'providers[0].renamedFrom must be a list of ids, got "model"',
        ),
        (
            [],
            {"provider": _p("model", id="chat", label="Chat models", renamedFrom="model")},
            "provider.renamedFrom must be a list of ids",
        ),
        (
            [_p("channel", id="chat", label="Chat")],
            {"retiredCapabilities": "old-inbox"},
            'retiredCapabilities must be a list of ids, got "old-inbox"',
        ),
        (
            [
                _p("inbox", id="mail-inbox", label="Read your mail", renamedFrom=["inbox"]),
                _p("search", id="mail-search", label="Search your mail", renamedFrom=["inbox"]),
            ],
            {},
            "providers[1].renamedFrom names 'inbox', also renamed by providers[0]",
        ),
        (
            [
                _p("model", "a:create", id="chat", label="Chat models", capabilities=["chat"]),
                _p("model", "b:create", id="fast", label="Fast chat", capabilities=["chat"]),
            ],
            {},
            "providers[1].capabilities: the use 'chat' is also providers[0]'s",
        ),
    ],
    ids=[
        "id-not-kebab",
        "id-repeated",
        "id-repeats-a-derived-one",
        "label-is-its-id",
        "label-is-a-type-name",
        "label-repeated",
        "renamed-from-a-declared-id",
        "id-was-retired",
        "renamed-from-a-retired-id",
        "retired-id-not-kebab",
        "two-sources-one-event",
        "renamed-from-not-a-list",
        "singular-renamed-from-not-a-list",
        "retired-not-a-list",
        "one-old-id-renamed-twice",
        "two-models-one-use",
    ],
)
def test_each_wrong_id_or_label_is_refused_naming_the_field(
    providers: list[dict], fields: dict, named: str
) -> None:
    errors = _manifest(**({"providers": providers} if providers else {}), **fields).validate()
    assert any(named in e for e in errors), errors


def test_every_native_bundle_declares_providers_with_ids_and_labels() -> None:
    from personalclaw.providers.loader import BUNDLED_DIR

    manifests = sorted(BUNDLED_DIR.glob("*/app.json"))
    assert len(manifests) >= 30, "the bundled apps were not found"
    for path in manifests:
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "provider" not in raw, f"{path.parent.name} still declares the singular provider"
        assert raw.get("providers"), f"{path.parent.name} declares no provider"
        for entry in raw["providers"]:
            assert entry.get("id") and entry.get("label"), f"{path.parent.name}: {entry}"
        assert AppManifest.from_dict(raw).validate() == [], path.parent.name
        for entry in raw["providers"]:
            # A model's own id says what it serves; `model` is only what an old manifest derives.
            assert entry["id"] != "model", f"{path.parent.name}: name what it serves"


def test_the_core_names_the_feature_apps_declare_for_ids() -> None:
    from personalclaw.apps.core_features import CAPABILITY_IDS, CORE_FEATURES

    assert CAPABILITY_IDS == "capability-ids"
    assert CAPABILITY_IDS in CORE_FEATURES


# ── one record per capability in the registry ────────────────────────────────


class _Handler:
    """A type handler that builds a plain object per capability and records what it did."""

    def __init__(self, log: list[str]) -> None:
        self.log = log

    def create(self, ext):
        return {"key": ext.key}

    def register(self, ext, instance) -> None:
        self.log.append(f"+{ext.key}")

    def deregister(self, ext, instance) -> None:
        self.log.append(f"-{ext.key}")


def _registry(log: list[str]) -> ProviderRegistry:
    reg = ProviderRegistry()
    for type_ in ("model", "trigger_source", "channel"):
        reg.register_type_handler(type_, _Handler(log))  # type: ignore[arg-type]
    return reg


def _three() -> AppManifest:
    return _manifest(
        providers=[
            _p("model", "a:create", id="chat", label="Chat models"),
            _p("model", "b:create", id="embeddings", label="Text embeddings"),
            _p("trigger_source", "c:create", id="triggers", label="Run automations on its events"),
        ]
    )


def test_an_app_with_three_capabilities_registers_three_records_by_key() -> None:
    log: list[str] = []
    reg = _registry(log)
    reg.register(_three())
    records = reg.list_for_app("demo")
    assert [r.key for r in records] == ["demo/chat", "demo/embeddings", "demo/triggers"]
    assert [r.capability for r in records] == ["chat", "embeddings", "triggers"]
    assert reg.get("demo", "embeddings") is records[1]
    assert reg.get("demo") is records[0], "an app with no capability named is its first"
    assert [r.key for r in reg.list_extensions()] == [r.key for r in records]


def test_one_capability_is_switched_on_and_off_without_the_others() -> None:
    log: list[str] = []
    reg = _registry(log)
    reg.register(_three())
    assert reg.enable("demo") is True
    assert [r.enabled for r in reg.list_for_app("demo")] == [True, True, True]
    reg.disable("demo", "embeddings")
    assert [(r.capability, r.enabled) for r in reg.list_for_app("demo")] == [
        ("chat", True),
        ("embeddings", False),
        ("triggers", True),
    ]
    assert log[-1] == "-demo/embeddings"
    assert reg.enable("demo", "embeddings") is True
    assert log[-1] == "+demo/embeddings"
    reg.rebuild("demo", "triggers")
    assert log[-2:] == ["-demo/triggers", "+demo/triggers"]
    reg.disable("demo", "chat")
    del log[:]
    reg.rebuild("demo")
    assert log == [
        "-demo/embeddings",
        "-demo/triggers",
        "+demo/embeddings",
        "+demo/triggers",
    ], "an app whose first capability is off still rebuilds the ones running, all off first"


def test_deregistering_an_app_forgets_every_capability() -> None:
    log: list[str] = []
    reg = _registry(log)
    reg.register(_three(), enabled=True)
    assert reg.deregister("demo") is True
    assert reg.list_for_app("demo") == []
    assert reg.get("demo") is None
    assert sorted(x for x in log if x.startswith("-")) == [
        "-demo/chat",
        "-demo/embeddings",
        "-demo/triggers",
    ]


def test_registering_an_app_again_replaces_its_records() -> None:
    reg = _registry([])
    reg.register(_three())
    reg.register(_manifest(providers=[_p("channel", id="channel", label="Chat with your agent")]))
    assert [r.key for r in reg.list_for_app("demo")] == ["demo/channel"]


def test_an_old_manifest_registers_under_its_derived_ids() -> None:
    reg = _registry([])
    reg.register(_manifest(provider=_p("channel"), providers=[_p("trigger_source", "x:source")]))
    assert [(r.key, r.provider_config.type) for r in reg.list_for_app("demo")] == [
        ("demo/channel", "channel"),
        ("demo/triggers", "trigger_source"),
    ], "each id is kept with the provider it names"


def test_an_app_whose_capabilities_cannot_be_told_apart_registers_nothing() -> None:
    reg = _registry([])
    reg.register(_manifest(providers=[_p("channel", "a:create"), _p("channel", "b:create")]))
    assert reg.list_for_app("demo") == []
    reg.register(_three())
    reg.register(_manifest(providers=[_p("channel", "a:create"), _p("channel", "b:create")]))
    assert reg.list_for_app("demo") == [], "an update to such a manifest keeps no earlier record"


def test_a_flat_registry_keeps_the_app_name_unless_the_app_repeats_the_type() -> None:
    reg = _registry([])
    reg.register(_three())
    reg.register(
        AppManifest.from_dict(
            {
                "name": "solo",
                "version": "1.0.0",
                "displayName": "Solo",
                "description": "One channel.",
                "provider": _p("channel"),
            }
        )
    )
    reg.register(
        AppManifest.from_dict(
            {
                "name": "twin",
                "version": "1.0.0",
                "displayName": "Twin",
                "description": "Two sync transports.",
                "providers": [
                    _p("sync", "a:create", id="git", label="Sync through a git remote"),
                    _p("sync", "b:create", id="folder", label="Sync through a shared folder"),
                ],
            }
        )
    )
    reg.register(
        AppManifest.from_dict(
            {
                "name": "pair",
                "version": "1.0.0",
                "displayName": "Pair",
                "description": "Two trigger sources.",
                "providers": [
                    _p(
                        "trigger_source",
                        "a:src",
                        id="dm",
                        label="Direct messages",
                        capabilities=["dm"],
                    ),
                    _p(
                        "trigger_source",
                        "b:src",
                        id="all",
                        label="All messages",
                        capabilities=["all"],
                    ),
                ],
            }
        )
    )
    names = {r.key: r.registry_name for r in reg.list_extensions()}
    assert names["solo/channel"] == "solo", "a name settings already use stays as it was"
    assert names["twin/git"] == "twin/git" and names["twin/folder"] == "twin/folder"
    # A model's name is the provider half of its bindings, and a trigger source's the namespace
    # of its events: both keep the app's, however many the app declares.
    assert names["demo/chat"] == names["demo/embeddings"] == names["demo/triggers"] == "demo"
    assert names["pair/dm"] == names["pair/all"] == "pair"


class _LocalModel:
    """Enough of the local-model contract for the registry to treat it as one."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.display_name = name

    def list_models(self):
        return []

    def download_model(self, model_id):
        return None

    def delete_model(self, model_id):
        return None


def test_an_app_has_one_local_model_capability() -> None:
    from personalclaw.local_models.registry import get_provider, unregister_provider
    from personalclaw.providers.registry import ModelTypeHandler

    handler = ModelTypeHandler()
    reg = ProviderRegistry()
    reg.register_type_handler("model", handler)
    reg.register(
        _manifest(
            name="localish",
            providers=[
                _p("model", "a:create", id="chat", label="Local chat", capabilities=["stt"]),
                _p("model", "b:create", id="voices", label="Local voices", capabilities=["tts"]),
            ],
        )
    )
    first, second = reg.list_for_app("localish")
    try:
        handler.register(first, _LocalModel("one"))
        with pytest.raises(ValueError, match="one local-model capability"):
            handler.register(second, _LocalModel("two"))
        assert get_provider("localish").name == "one"
        handler.deregister(second, _LocalModel("two"))
        assert get_provider("localish") is not None, "only its owner takes the entry away"
        handler.deregister(first, _LocalModel("one"))
        assert get_provider("localish") is None
    finally:
        unregister_provider("localish")


# ── readers that show an app by its first provider ───────────────────────────


def test_a_native_app_s_store_card_shows_its_first_provider() -> None:
    from personalclaw.apps import catalog
    from personalclaw.providers.loader import BUNDLED_DIR

    cards = {e.name: e for e in catalog.available_bundled()}
    assert cards, "no native app was listed"
    for path in sorted(BUNDLED_DIR.glob("*/app.json")):
        first = AppManifest.from_dict(json.loads(path.read_text(encoding="utf-8"))).providers[0]
        card = cards[path.parent.name]
        assert card.isProvider, card.name
        assert (card.providerType, card.providerCapabilities) == (
            first.type,
            list(first.capabilities),
        ), card.name


def test_a_provider_declared_only_in_the_list_still_drives_the_settings_form() -> None:
    from personalclaw.dashboard.handlers.apps import (
        _configured_per_instance,
        _effective_config_schema,
    )

    schema = {"type": "object", "properties": {"token": {"type": "string"}}}
    one = _manifest(providers=[_p("channel", id="channel", label="Chat", settingsSchema=schema)])
    assert _effective_config_schema(one) == schema
    assert _configured_per_instance(one) is None
    many = _manifest(
        providers=[
            _p("model", id="chat", label="Chat models", settingsSchema=schema, multiInstance=True)
        ]
    )
    assert _effective_config_schema(many) == {}
    assert "keeps its settings on each instance" in (_configured_per_instance(many) or "")


def test_the_availability_probe_checks_every_capability_of_an_app(monkeypatch) -> None:
    from personalclaw.apps import app_python, app_runtime
    from personalclaw.providers import availability_probe

    monkeypatch.setattr(app_python, "activate", lambda: None)
    monkeypatch.setattr(app_runtime, "installed", lambda: [(_three(), True)])
    found, missing = availability_probe._records(["demo", "gone"])
    assert [r.key for r in found] == ["demo/chat", "demo/embeddings", "demo/triggers"]
    assert missing == ["gone"]


def test_a_handler_driven_with_a_stand_in_record_keeps_its_name() -> None:
    """A first-party app's own tests drive core's handlers with a stand-in that carries only
    ``name`` (``local-image-gen``'s model test) or with ``None`` (the channel apps' trigger-source
    fixtures), and run against every core."""
    from personalclaw.providers.registry import DutyGateTypeHandler, ModelTypeHandler
    from personalclaw.triggers.calendar import _DUTY_GATES

    class _Stand:
        name = "stand-in-app"

        class provider_config:  # noqa: N801
            capabilities = ["stt"]
            multiInstance = False

    class _Gate:
        async def on_duty(self, now, ctx):
            return None

    handler = DutyGateTypeHandler()
    handler.register(_Stand(), _Gate())
    try:
        assert "stand-in-app" in _DUTY_GATES
    finally:
        handler.deregister(_Stand(), _Gate())
    assert "stand-in-app" not in _DUTY_GATES
    handler.register(None, type("_Named", (_Gate,), {"name": "named-gate"})())
    handler.deregister(None, type("_Named", (_Gate,), {"name": "named-gate"})())
    assert "named-gate" not in _DUTY_GATES

    from personalclaw.local_models.registry import get_provider, unregister_provider

    model = ModelTypeHandler()
    try:
        model.register(_Stand(), _LocalModel("stand"))
        assert get_provider("stand-in-app") is not None
        model.deregister(_Stand(), _LocalModel("stand"))
        assert get_provider("stand-in-app") is None
    finally:
        unregister_provider("stand-in-app")


# ── names for a repeated type, where they are read ───────────────────────────


def test_a_repeated_type_s_names_are_read_where_each_registry_is_read(monkeypatch) -> None:
    """An app that declares a flat-named type twice keeps each under ``<app>/<id>`` (the
    ``registry_name``) when its instance names none. Four registries take that name: duty gates,
    trigger stores, memory providers and vector stores. None of them puts it in a URL path, a file
    or folder name, or a config key; each is read by a lookup or a stored value, and each finds
    both records here. (The others register under the instance's own name.)"""
    import asyncio
    from datetime import datetime, timezone

    from personalclaw.context import ContextBuilder
    from personalclaw.providers import registry as registry_module
    from personalclaw.triggers import calendar
    from personalclaw.triggers.registry import provider_rows_by_store
    from personalclaw.vector_stores import registry as vector_stores

    monkeypatch.setattr(
        registry_module.VectorStoreTypeHandler, "_backfill_corpus", staticmethod(lambda: None)
    )
    handlers = {
        "duty_gate": registry_module.DutyGateTypeHandler(),
        "trigger": registry_module.TriggerTypeHandler(),
        "memory": registry_module.MemoryTypeHandler(),
        "vector_store": registry_module.VectorStoreTypeHandler(),
    }

    class _Gate:
        async def on_duty(self, now, ctx):
            return calendar.DutyVerdict(on_duty=False, reason="off")

    class _Store:
        def load(self):
            return []

    class _Memory:
        pass

    class _Vectors:
        pass

    made = {"duty_gate": _Gate, "trigger": _Store, "memory": _Memory, "vector_store": _Vectors}
    reg = ProviderRegistry()
    providers = []
    for type_ in handlers:
        for n in ("a", "b"):
            word = type_.replace("_", "-")
            providers.append(_p(type_, f"{n}:make", id=f"{word}-{n}", label=f"{type_} {n}"))
    reg.register(_manifest(name="dup", providers=providers))
    live = [(rec, made[rec.provider_config.type]()) for rec in reg.list_for_app("dup")]
    for rec, instance in live:
        handlers[rec.provider_config.type].register(rec, instance)
    try:
        names = {rec.key: rec.registry_name for rec, _ in live}
        assert names["dup/duty-gate-a"] == "dup/duty-gate-a", names
        moment = datetime(2026, 10, 29, 9, tzinfo=timezone.utc)
        decision = asyncio.run(
            calendar.evaluate_duty({"duty_gate": {"provider": "dup/duty-gate-b"}}, moment)
        )
        assert decision.outcome == calendar.GateOutcome.OFF_DUTY.value, decision
        stores = {name for name, _store, _rows in provider_rows_by_store()}
        assert {"dup/trigger-a", "dup/trigger-b"} <= stores
        memory = {rec.registry_name: inst for rec, inst in live if isinstance(inst, _Memory)}
        assert ContextBuilder.get_memory_for(memory_store="dup/memory-a") is memory["dup/memory-a"]
        assert {"dup/vector-store-a", "dup/vector-store-b"} <= set(vector_stores._providers)
    finally:
        for rec, instance in live:
            handlers[rec.provider_config.type].deregister(rec, instance)
    assert "dup/duty-gate-b" not in calendar.duty_gate_names()
    assert not {"dup/vector-store-a", "dup/vector-store-b"} & set(vector_stores._providers)


# ── two model capabilities, one use ──────────────────────────────────────────


def test_an_app_with_two_models_serving_one_use_is_not_installed(tmp_path, monkeypatch) -> None:
    """Both would be filed under the app's name for that use: the second would replace the first,
    switching either off would take the other's away, and a ``provider:model`` binding could not
    say which it meant. The install says so, naming the field, and nothing lands."""
    import personalclaw.config.loader as loader
    from personalclaw.apps import app_manager, manager

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    src = tmp_path / "src" / "twin-chat"
    src.mkdir(parents=True)
    raw = {
        "name": "twin-chat",
        "version": "1.0.0",
        "displayName": "Twin Chat",
        "description": "Two chat models.",
        "providers": [
            _p("model", "provider:a", id="chat", label="Chat models", capabilities=["chat"]),
            _p("model", "provider:b", id="fast", label="Fast chat", capabilities=["chat"]),
        ],
    }
    (src / "app.json").write_text(json.dumps(raw), encoding="utf-8")
    (src / "provider.py").write_text("def a(c):\n    return None\n\n\ndef b(c):\n    return None\n")
    res = app_manager.install(src, confirm=True)
    assert not res.ok
    assert "providers[1].capabilities: the use 'chat'" in res.error, res.error
    assert manager._read_installed("twin-chat") is None


def _concrete(base: type, name: str) -> Any:
    """An instance of *base* with every abstract member stubbed, named *name*."""
    stubs: dict[str, Any] = {}
    for member in base.__abstractmethods__:  # type: ignore[attr-defined]
        if member in ("name", "display_name"):
            stubs[member] = property(lambda self: name)
        else:
            stubs[member] = lambda self, *a, **k: None
    return type(f"_{base.__name__}", (base,), stubs)()


def test_two_model_capabilities_serving_different_uses_stay_apart() -> None:
    """Allowed: each is filed under its own use, so switching one off leaves the other running."""
    from personalclaw.providers.registry import ModelTypeHandler
    from personalclaw.stt import registry as stt
    from personalclaw.stt.provider import SttProvider
    from personalclaw.tts import registry as tts
    from personalclaw.tts.provider import TtsProvider

    m = _manifest(
        name="voice",
        providers=[
            _p("model", "a:create", id="listen", label="Speech to text", capabilities=["stt"]),
            _p("model", "b:create", id="speak", label="Text to speech", capabilities=["tts"]),
        ],
    )
    assert m.validate() == []
    reg = ProviderRegistry()
    handler = ModelTypeHandler()
    reg.register_type_handler("model", handler)
    reg.register(m)
    listen, speak = reg.list_for_app("voice")
    heard, said = _concrete(SttProvider, "voice"), _concrete(TtsProvider, "voice")
    handler.register(listen, heard)
    handler.register(speak, said)
    try:
        handler.deregister(listen, heard)
        assert stt.get_provider("voice") is None
        assert tts.get_provider("voice") is said, "switching one off took the other's away"
    finally:
        stt.unregister_provider("voice")
        tts.unregister_provider("voice")


def test_a_local_model_owner_goes_with_its_entry() -> None:
    """The owner is kept beside the entry it guards, so whatever takes the entry away (an app's
    removal, a reset) takes the owner with it, and a later capability of that app is not refused
    by one that is gone."""
    from personalclaw.local_models import registry as local

    local.register_provider(_LocalModel("old"), name="lm-app", owner="chat")
    try:
        with pytest.raises(ValueError, match="one local-model capability"):
            local.register_provider(_LocalModel("new"), name="lm-app", owner="voices")
        local.unregister_provider("lm-app")
        assert "lm-app" not in local._owners
        local.register_provider(_LocalModel("new"), name="lm-app", owner="voices")
        assert local.get_provider("lm-app").name == "new"
    finally:
        local.unregister_provider("lm-app")
    assert "lm-app" not in local._owners
