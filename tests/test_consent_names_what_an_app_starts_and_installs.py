"""Install consent says which programs an app starts, what it installs, and where it writes.

An agent app (Claude Code, Codex, Kiro CLI, Gemini CLI) gets three things done that its review
never named. Core starts the agent's own CLI from this machine, and that CLI runs signed in as
its own account, with its own settings and, for some, its own auto-approve rules. Core also
npm-installs the ACP adapter the app asks for into the PersonalClaw folder when the app is
switched on. And the app may keep files outside its own folder (Claude Code's own config). The
dialog still said "This is everything <app> gets".

The manifest now declares each of them: ``launches`` (the program, what it is for, and what of
the owner's it runs with, with the setting that decides it), ``dependencies.npmPackages`` and
``writes``. The one disclosure projection carries them to the install review, the Store card and
an update's review, so an update that adds one asks again. And core does neither for an app that
did not declare it: the npm install and the CLI's registration are refused, with the reason on
the provider's card.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, catalog, manager
from personalclaw.apps.disclosure import describe
from personalclaw.apps.manifest import AppManifest
from personalclaw.providers import loader

PROGRAM = {
    "program": "example-cli",
    "why": "It does the work of each chat; the app talks to it over ACP.",
    "inherits": ["sign-in", "settings", "auto-approve-rules"],
}
WRITE = {"path": "example-cli-config", "why": "The CLI's own config while isolation is on."}
NPM = "@example/example-acp"

SETTINGS = {
    "type": "object",
    "properties": {
        "isolated": {
            "type": "boolean",
            "default": True,
            "x-meta": {"label": "Isolated settings", "help": "On: the CLI starts empty."},
        }
    },
}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    from personalclaw import inbox as _inbox
    from personalclaw.providers import entity_routes as _er

    for module in (cfg, manager, catalog, _er, _inbox):
        monkeypatch.setattr(module, "config_dir", lambda: tmp_path)
    native = tmp_path / "native"
    native.mkdir()
    monkeypatch.setattr(loader, "BUNDLED_DIR", native)
    monkeypatch.setenv("PERSONALCLAW_FIRST_PARTY_APPS_DIR", str(tmp_path / "no-first-party"))
    monkeypatch.setattr(catalog, "_DEFAULT_GIT_SOURCES", ())
    catalog._git_scan_cache.clear()
    catalog._registry_cache.clear()
    yield tmp_path
    catalog._git_scan_cache.clear()
    catalog._registry_cache.clear()


def _manifest(**extra) -> dict:
    return {
        "name": "example-agent",
        "version": "1.0.0",
        "displayName": "Example Agent",
        "description": "runs an agent CLI",
        **extra,
    }


def _agent_provider(settings: dict | None = None) -> dict:
    return {
        "type": "agent",
        "implementation": "provider:create_provider",
        **({"settingsSchema": settings} if settings else {}),
    }


def _bundle(root: Path, manifest: dict, provider_py: str = "") -> Path:
    d = root / manifest["name"]
    d.mkdir(parents=True)
    (d / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    if provider_py:
        (d / "provider.py").write_text(provider_py, encoding="utf-8")
    return d


# ── the manifest ─────────────────────────────────────────────────────────────────────────────


def test_the_declarations_round_trip_through_the_manifest():
    raw = _manifest(
        provider=_agent_provider(SETTINGS),
        launches=[{**PROGRAM, "inheritsWhile": {"setting": "isolated", "value": False}}],
        writes=[WRITE],
        dependencies={"npmPackages": [NPM]},
    )
    m = AppManifest.from_dict(raw)

    assert m.validate() == []
    d = m.to_dict()
    assert d["launches"] == raw["launches"]
    assert d["writes"] == [WRITE]
    assert d["dependencies"]["npmPackages"] == [NPM]
    assert AppManifest.from_dict(d).to_dict() == d
    assert not {"launches", "writes"} & set(m.extra), "a declaration must not land in extra"
    plain = AppManifest.from_dict(_manifest()).to_dict()
    assert "launches" not in plain and "writes" not in plain and "dependencies" not in plain


@pytest.mark.parametrize(
    ("over", "says"),
    [
        ({"launches": [{"why": "it works"}]}, "missing 'program'"),
        ({"launches": [{"program": "x"}]}, "missing 'why'"),
        ({"launches": [{**PROGRAM, "program": "/usr/bin/x"}]}, "a program's name"),
        ({"launches": [{**PROGRAM, "inherits": ["keys"]}]}, "inherits"),
        ({"launches": [PROGRAM, PROGRAM]}, "more than once"),
        ({"launches": [{**PROGRAM, "program": f"p{i}"} for i in range(11)]}, "at most 10"),
        (
            {"launches": [{**PROGRAM, "inheritsWhile": {"setting": "nope", "value": False}}]},
            "boolean setting",
        ),
        (
            {"launches": [{**PROGRAM, "inherits": [], "inheritsWhile": {"setting": "isolated"}}]},
            "inherits nothing",
        ),
        ({"writes": [{"path": "/etc/x", "why": "w"}]}, "inside your home"),
        ({"writes": [{"path": "a/../../x", "why": "w"}]}, "'..'"),
        ({"writes": [{"path": "cfg"}]}, "missing 'why'"),
        ({"dependencies": {"npmPackages": ["Not A Package"]}}, "npm package name"),
        ({"dependencies": {"npmPackages": [NPM, NPM]}}, "more than once"),
    ],
)
def test_a_declaration_the_consent_cannot_show_is_an_install_error(over, says):
    errors = AppManifest.from_dict(_manifest(provider=_agent_provider(SETTINGS), **over)).validate()
    assert any(says in e for e in errors), errors


# ── the install review, the card and an update ──────────────────────────────────────────────


def test_the_install_review_names_the_program_it_starts_and_the_package_it_installs(tmp_path):
    """The review the consent dialog shows is the server's reading of the staged bytes."""
    src = _bundle(
        tmp_path / "src",
        _manifest(
            provider=_agent_provider(SETTINGS),
            launches=[{**PROGRAM, "inheritsWhile": {"setting": "isolated", "value": False}}],
            writes=[WRITE],
            dependencies={"npmPackages": [NPM]},
        ),
    )

    review = app_manager.preview(src)

    assert review.needs_consent, review.error
    assert review.disclosure["launches"] == [
        {
            "program": "example-cli",
            "why": PROGRAM["why"],
            "inherits": ["sign-in", "settings", "auto-approve-rules"],
            # The setting as the owner sees it on the app's Configure page, and where it starts.
            "inheritsWhile": {
                "setting": "isolated",
                "label": "Isolated settings",
                "value": False,
                "default": True,
            },
            # Not an npx entry, and it names no host.
            "npmPackage": "",
            "hosts": [],
        }
    ]
    assert review.disclosure["npmPackages"] == [NPM]
    assert review.disclosure["writes"] == [WRITE]
    says = review.disclosure["runsAsYou"]
    assert "the program it starts" in says and "the npm package it installs" in says, says


def test_an_unconditional_inheritance_says_so():
    d = describe(AppManifest.from_dict(_manifest(launches=[PROGRAM])))
    assert d["launches"][0]["inheritsWhile"] is None
    assert d["npmPackages"] == [] and d["writes"] == []


def test_the_store_card_carries_what_the_app_starts_and_installs(tmp_path):
    root = tmp_path / "local-src"
    _bundle(root, _manifest(launches=[PROGRAM], dependencies={"npmPackages": [NPM]}))
    catalog.add_local_source(str(root))

    [card] = [c for c in catalog._scan_local_sources() if c.name == "example-agent"]

    assert [p["program"] for p in card.launches] == ["example-cli"]
    assert card.npmPackages == [NPM]
    assert card.consentKnown


@pytest.mark.parametrize(
    "adds",
    [
        {"launches": [PROGRAM]},
        {"dependencies": {"npmPackages": [NPM]}},
        {"writes": [WRITE]},
    ],
    ids=["a program", "an npm package", "a place it writes"],
)
def test_an_update_that_adds_one_asks_first(tmp_path, adds):
    """On main each went into ``extra`` (or was dropped), the review said nothing changed, and
    the update committed on a bare request — so a new version could start a CLI, or have core
    npm-install a package, that no review had named."""
    v1 = _bundle(tmp_path / "v1", _manifest())
    assert app_manager.install(v1, confirm=True).ok
    v2 = _bundle(tmp_path / "v2", _manifest(version="1.1.0", **adds))

    res = app_manager.update(v2)

    assert res.needs_consent and not res.ok, res.error
    assert res.consent, "the review is bound to the new bytes"
    assert manager._read_installed("example-agent").version == "1.0.0"


def test_an_update_that_widens_what_a_program_inherits_asks_first(tmp_path):
    v1 = _bundle(tmp_path / "v1", _manifest(launches=[{**PROGRAM, "inherits": ["sign-in"]}]))
    assert app_manager.install(v1, confirm=True).ok
    v2 = _bundle(tmp_path / "v2", _manifest(version="1.1.0", launches=[PROGRAM]))

    res = app_manager.update(v2)

    assert res.needs_consent and not res.ok, res.error
    assert res.previous["launches"][0]["inherits"] == ["sign-in"]
    assert res.disclosure["launches"][0]["inherits"] == PROGRAM["inherits"]


def test_the_consent_digest_moves_with_the_declarations(tmp_path):
    first = app_manager.preview(_bundle(tmp_path / "a", _manifest(launches=[PROGRAM])))
    second = app_manager.preview(
        _bundle(tmp_path / "b", _manifest(launches=[{**PROGRAM, "inherits": ["sign-in"]}]))
    )
    assert first.consent and second.consent and first.consent != second.consent


# ── core starts only the programs an app declares ───────────────────────────────────────────

_REGISTERS = """\
from personalclaw.sdk.acp import register_acp_cli_entry


def create_provider(config=None):
    register_acp_cli_entry(
        cli="example-cli",
        dialect="default",
        command={command!r},
        extension="example-agent",
        requires_executable={engine!r},
    )
    return None
"""


@pytest.fixture
def acp_registry():
    from personalclaw.acp_bundles._register import unregister_acp_cli_entry
    from personalclaw.llm.registry import get_default_registry

    unregister_acp_cli_entry("example-cli")
    yield get_default_registry()
    unregister_acp_cli_entry("example-cli")


def _install_registering(tmp_path, manifest: dict, *, command: list[str], engine=None):
    src = _bundle(
        tmp_path / "src",
        manifest,
        _REGISTERS.format(command=command, engine=engine),
    )
    res = app_manager.install(src, confirm=True)
    assert res.ok, res.error
    from personalclaw.providers.registry import get_provider_registry

    rec = get_provider_registry().get("example-agent")
    assert rec is not None
    return rec


# A program that cannot resolve: registering never starts it, and nothing here may.
_CLI = ["/nonexistent/pc-fixture-cli", "acp"]


def test_an_agent_cli_the_manifest_declares_is_registered(tmp_path, acp_registry):
    rec = _install_registering(
        tmp_path, _manifest(provider=_agent_provider(), launches=[PROGRAM]), command=_CLI
    )
    assert rec.error == ""
    assert acp_registry._entries.get("acp:example-cli") is not None


def test_an_agent_cli_the_manifest_does_not_declare_is_not_started(tmp_path, acp_registry):
    rec = _install_registering(tmp_path, _manifest(provider=_agent_provider()), command=_CLI)

    assert acp_registry._entries.get("acp:example-cli") is None, "core would start it"
    assert "launches" in rec.error and "install review never named" in rec.error, rec.error


@pytest.mark.parametrize("engine", ["example-cli", "other-cli"])
def test_the_engine_an_adapter_hands_its_turns_to_must_be_declared(tmp_path, acp_registry, engine):
    """An adapter that delegates each turn to a separate CLI (``requires_executable``) starts
    that CLI: it is the program the review has to have named."""
    rec = _install_registering(
        tmp_path,
        _manifest(provider=_agent_provider(), launches=[PROGRAM]),
        command=["/nonexistent/pc-fixture-adapter"],
        engine={"label": engine, "env_var": "", "path": ""},
    )

    if engine == "example-cli":
        assert rec.error == "" and acp_registry._entries.get("acp:example-cli") is not None
    else:
        assert acp_registry._entries.get("acp:example-cli") is None
        assert "other-cli" in rec.error, rec.error


@pytest.mark.parametrize("declared", [True, False], ids=["declared", "not declared"])
def test_an_adapter_fetched_with_npx_must_be_a_declared_npm_package(
    tmp_path, acp_registry, declared
):
    deps = {"dependencies": {"npmPackages": [NPM]}} if declared else {}
    rec = _install_registering(
        tmp_path,
        _manifest(provider=_agent_provider(), launches=[PROGRAM], **deps),
        command=["/nonexistent/npx", "-y", NPM],
    )

    if declared:
        assert rec.error == "" and acp_registry._entries.get("acp:example-cli") is not None
    else:
        assert acp_registry._entries.get("acp:example-cli") is None
        assert NPM in rec.error and "npmPackages" in rec.error, rec.error


def test_core_registering_its_own_entry_is_not_an_app_declaration(acp_registry):
    """Only an app's code is held to its manifest: a registration core (or a test) makes is not
    an app's, so it has no review to be held to."""
    from personalclaw.acp_bundles._register import register_acp_cli_entry

    assert register_acp_cli_entry(cli="example-cli", dialect="default", command=_CLI) is not None
