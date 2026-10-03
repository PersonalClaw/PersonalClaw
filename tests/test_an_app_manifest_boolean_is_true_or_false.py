"""An ``app.json`` boolean is the JSON ``true`` or ``false``, and no other value installs.

``bool("false")`` is True, and the manifest read its booleans with ``bool()``: a text ``"false"``
read as true. ``"native": "false"`` declared a native app, ``"storage": "false"`` put the grant on
the install-consent screen and granted it, ``"agentCallable": "false"`` handed the route to the
agent, ``"multiInstance": "false"`` made a provider configured per instance, and a cron entry's
``"silent": "false"`` silenced it. Three compared with ``True`` instead, so a text ``"true"`` there
was ignored (``memory``, a route's ``readOnly``, a launch condition's ``value``), and the quality
badges read ``"tested": "false"`` as a claim.

Parsing now reads each one as the word it spells and anything else as the field's safe value
(``safety_flags.strict_bool``; ``yes_or_no`` for a quality axis, where spelling neither claims
nothing), because a Store listing parses every manifest it shows and must not break on one. An
install, an update and their review are where the author can fix it, so ``validate()`` refuses
every value that is not ``true`` or ``false`` and names the field.
"""

from __future__ import annotations

import dataclasses
import json
import typing
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager
from personalclaw.apps.disclosure import describe
from personalclaw.apps.manifest import PERMISSION_KEYS, AppManifest, Permissions, permission_key

_BASE = {
    "name": "flag-app",
    "version": "1.0.0",
    "displayName": "Flag App",
    "description": "declares one boolean",
}
_JOBS = {"cron": True, "agent": "text"}
_JOB = {"name": "daily", "every": 3600, "message": "summarise the day"}
_ROUTE = {"op": "list_items", "method": "GET", "path": "/items"}
_PROGRAM = {"program": "example-cli", "why": "It runs each chat.", "inherits": ["sign-in"]}
_AGENT = {
    "type": "agent",
    "implementation": "provider:create_provider",
    "settingsSchema": {"type": "object", "properties": {"isolated": {"type": "boolean"}}},
}
_SEARCH = {"type": "search", "implementation": "provider:create_provider"}

#: Every boolean an ``app.json`` declares: how a manifest declares it, how the parsed manifest
#: reads it back, what the refusal names it, and what a value spelling neither reads as.
PLACES: dict[tuple[str, str], tuple[typing.Callable, typing.Callable, str, object]] = {
    ("AppManifest", "native"): (lambda v: {"native": v}, lambda m: m.native, "native", False),
    **{
        ("Permissions", key): (
            (
                lambda v, key=key: {
                    "permissions": {key: v, **({"agent": "text"} if key == "cron" else {})}
                }
            ),
            (lambda m, key=key: getattr(m.permissions, key)),
            f"permissions.{key}",
            False,
        )
        for key in ("storage", "network", "memory", "cron", "storageShared", "backgroundTasks")
    },
    ("CronEntry", "silent"): (
        lambda v: {"permissions": _JOBS, "crons": [{**_JOB, "silent": v}]},
        lambda m: m.crons[0].silent,
        "silent",
        False,
    ),
    ("CronEntry", "persistent_session"): (
        lambda v: {"permissions": _JOBS, "crons": [{**_JOB, "persistent_session": v}]},
        lambda m: m.crons[0].persistent_session,
        "persistent_session",
        True,
    ),
    ("RouteEntry", "agentCallable"): (
        lambda v: {"backend": {"routes": [{**_ROUTE, "agentCallable": v}]}},
        lambda m: m.backend.routes[0].agentCallable,
        "agentCallable",
        False,
    ),
    ("RouteEntry", "readOnly"): (
        lambda v: {"backend": {"routes": [{**_ROUTE, "readOnly": v}]}},
        lambda m: m.backend.routes[0].readOnly,
        "readOnly",
        False,
    ),
    ("ProviderConfig", "multiInstance"): (
        lambda v: {"provider": {**_SEARCH, "multiInstance": v}},
        lambda m: m.provider.multiInstance,
        "multiInstance",
        False,
    ),
    ("SettingCondition", "value"): (
        lambda v: {
            "provider": _AGENT,
            "launches": [{**_PROGRAM, "inheritsWhile": {"setting": "isolated", "value": v}}],
        },
        lambda m: m.launches[0].inheritsWhile.value,
        "inheritsWhile.value",
        False,
    ),
    ("QualityDeclaration", "tested"): (
        lambda v: {"quality": {"tested": v}},
        lambda m: m.quality.tested,
        "quality.tested",
        None,
    ),
    ("QualityDeclaration", "a11y"): (
        lambda v: {"quality": {"a11y": v}},
        lambda m: m.quality.a11y,
        "quality.a11y",
        None,
    ),
}

_IDS = [f"{cls}.{name}" for cls, name in PLACES]


def _parsed(place: tuple[str, str], value: object) -> AppManifest:
    build = PLACES[place][0]
    return AppManifest.from_dict({**_BASE, **build(value)})


def _read(place: tuple[str, str], value: object) -> object:
    return PLACES[place][1](_parsed(place, value))


def _boolean_fields() -> set[tuple[str, str]]:
    """``(class, field)`` for every boolean the manifest's dataclasses declare, found from their
    types, so a boolean added tomorrow is in question here without anyone listing it."""
    seen: set[type] = set()
    found: set[tuple[str, str]] = set()

    def walk(cls: type) -> None:
        if cls in seen:
            return
        seen.add(cls)
        hints = typing.get_type_hints(cls)
        for f in dataclasses.fields(cls):
            if not f.init:
                continue  # a record parsing keeps about the raw dict, not something declared
            if cls is Permissions and permission_key(f) not in PERMISSION_KEYS:
                continue  # Permissions' own bookkeeping: no app.json key
            kind = hints[f.name]
            members = typing.get_args(kind)
            if kind is bool or set(members) == {bool, type(None)}:
                found.add((cls.__name__, f.name))
                continue
            for inner in (kind, *members, *(a for m in members for a in typing.get_args(m))):
                if dataclasses.is_dataclass(inner):
                    walk(inner)

    walk(AppManifest)
    return found


def test_every_boolean_an_app_json_declares_is_in_question_here():
    found = _boolean_fields()
    assert len(found) >= 15, f"the type walk found too little to be measuring: {sorted(found)}"
    assert found == set(PLACES), (
        f"  not covered: {sorted(found - set(PLACES))}\n"
        f"  no longer a boolean: {sorted(set(PLACES) - found)}"
    )


@pytest.mark.parametrize("place", list(PLACES), ids=_IDS)
@pytest.mark.parametrize("value", [True, False])
def test_a_real_boolean_installs_and_reads_as_itself(place, value):
    manifest = _parsed(place, value)

    assert manifest.validate() == []
    assert PLACES[place][1](manifest) is value


@pytest.mark.parametrize("place", list(PLACES), ids=_IDS)
def test_text_reads_as_the_word_it_spells(place):
    assert _read(place, "false") is False
    assert _read(place, " Off ") is False
    assert _read(place, "true") is True
    assert _read(place, "yes") is True


@pytest.mark.parametrize("place", list(PLACES), ids=_IDS)
@pytest.mark.parametrize("value", ["maybe", ["true"], {"on": True}], ids=["word", "list", "object"])
def test_a_value_that_spells_neither_reads_as_the_fields_safe_value(place, value):
    assert _read(place, value) is PLACES[place][3]


@pytest.mark.parametrize("place", list(PLACES), ids=_IDS)
@pytest.mark.parametrize(
    "value",
    ["false", "true", "", 0, 1, None, "maybe", ["true"], {"on": True}],
    ids=["text-false", "text-true", "blank", "zero", "one", "null", "word", "list", "object"],
)
def test_an_install_refuses_every_value_that_is_not_true_or_false_and_names_the_field(place, value):
    manifest = _parsed(place, value)  # parsing never raises: the Store still lists the app

    named = [e for e in manifest.validate() if PLACES[place][2] in e]

    assert len(named) == 1, f"expected one refusal naming {PLACES[place][2]!r}: {named}"


def test_memory_keeps_its_own_words_for_the_tier_vocabulary_it_replaced():
    manifest = AppManifest.from_dict({**_BASE, "permissions": {"memory": "app-scoped"}})

    (error,) = manifest.validate()

    assert error.startswith("permissions.memory must be a boolean"), error
    assert "tier vocabulary was removed" in error and "'app-scoped'" in error, error
    assert manifest.permissions.memory is False


# ── what the owner sees ─────────────────────────────────────────────────────────────────────


def test_a_grant_its_author_turned_off_in_text_is_neither_granted_nor_shown():
    off = AppManifest.from_dict({**_BASE, "permissions": {"storage": "false"}})
    on = AppManifest.from_dict({**_BASE, "permissions": {"storage": True}})

    assert off.permissions.storage is False
    assert "storage" not in describe(off)["permissions"], "the consent screen shows a grant"
    assert describe(on)["permissions"]["storage"] is True, "a real grant left the consent screen"


def test_native_written_as_text_false_is_not_a_native_app():
    manifest = AppManifest.from_dict({**_BASE, "native": "false"})

    assert manifest.native is False
    assert "native" not in manifest.to_dict()


@pytest.fixture
def _home(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


def _source(root: Path, **extra) -> Path:
    d = root / "source" / _BASE["name"]
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps({**_BASE, **extra}), encoding="utf-8")
    return d


def test_an_install_of_a_text_boolean_is_refused_before_anything_is_written(_home):
    src = _source(_home, permissions={"storage": "false"})

    review = app_manager.preview(src)
    result = app_manager.install(src, confirm=True)

    assert review.scan is None and "permissions.storage" in review.error, review.error
    assert not result.ok and "permissions.storage" in result.error, result.error
    assert not manager.app_dir(_BASE["name"]).exists()
    assert manager._read_installed(_BASE["name"]) is None


def test_an_app_declaring_real_booleans_installs(_home):
    src = _source(_home, permissions={"storage": True, "network": False})

    result = app_manager.install(src, confirm=True)

    assert result.ok, result.error
    assert manager._read_installed(_BASE["name"]) is not None
