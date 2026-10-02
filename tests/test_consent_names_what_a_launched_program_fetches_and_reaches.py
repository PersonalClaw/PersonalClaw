"""Install consent says what a program an app starts downloads and runs, and where it goes.

A marketplace app searched by running ``npx -y skills find <query>`` from its own code. npx fetches
the newest ``skills`` package from the npm registry and runs it as the owner, on every search, and
the search reaches the marketplace's own site. Its install review named none of it: "Permissions
the gateway enforces: None", a network row naming no host, and a provider module. Only the
security scan's warning about running a program said anything ran at all.

A ``launches`` entry now carries what the review needs for that: ``npmPackage``, the package an
``npx`` entry downloads and runs (and an ``npx`` entry must name one), and ``hosts``, the hosts the
program reaches. A program the owner chooses (a runbook action they wrote) is ``*``, and names no
program core may start for the app.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, catalog, manager
from personalclaw.apps.disclosure import describe
from personalclaw.apps.manifest import AppManifest
from personalclaw.providers import loader

NPX = {
    "program": "npx",
    "why": "Without an API key, each search runs the marketplace's own command line.",
    "inherits": ["sign-in", "settings"],
    "npmPackage": "example-finder",
    "hosts": ["registry.npmjs.org", "finder.example.com"],
}
GIT = {
    "program": "git",
    "why": "It clones a listing's repository into a temporary folder to read it.",
    "inherits": ["settings"],
    "hosts": ["git.example.org"],
}
YOURS = {
    "program": "*",
    "why": "Applying a fix runs the command an action in your own runbook declares.",
    "inherits": ["sign-in", "settings"],
}

SETTINGS = {
    "type": "object",
    "properties": {
        "allow_apply": {
            "type": "boolean",
            "default": False,
            "x-meta": {"label": "Allow gated remediation", "help": "Off: it only proposes."},
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
        "name": "example-finder",
        "version": "1.0.0",
        "displayName": "Example Finder",
        "description": "finds listings",
        **extra,
    }


def _provider(settings: dict | None = None) -> dict:
    return {
        "type": "skills",
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


def test_the_package_npx_runs_its_hosts_and_a_program_you_name_round_trip():
    raw = _manifest(
        provider=_provider(SETTINGS),
        launches=[
            NPX,
            GIT,
            {**YOURS, "inheritsWhile": {"setting": "allow_apply", "value": True}},
        ],
    )
    m = AppManifest.from_dict(raw)

    assert m.validate() == []
    d = m.to_dict()
    assert d["launches"] == raw["launches"]
    assert AppManifest.from_dict(d).to_dict() == d
    # Nothing new is written for an entry that declares neither.
    plain = AppManifest.from_dict(_manifest(launches=[{"program": "ffmpeg", "why": "w"}]))
    assert plain.to_dict()["launches"] == [{"program": "ffmpeg", "why": "w"}]


@pytest.mark.parametrize(
    ("entry", "says"),
    [
        # A package is what npx downloads and runs; no other program does that.
        ({**GIT, "npmPackage": "example-finder"}, "'program' must be 'npx'"),
        ({**YOURS, "npmPackage": "example-finder"}, "'program' must be 'npx'"),
        # An npx entry that does not say what it runs hides the one thing that matters.
        ({key: v for key, v in NPX.items() if key != "npmPackage"}, "name that package"),
        ({**NPX, "npmPackage": "Example Finder"}, "npm package name"),
        ({**NPX, "npmPackage": "example-finder@latest"}, "npm package name"),
        ({**GIT, "hosts": ["https://git.example.org"]}, "is not a host name"),
        ({**GIT, "hosts": ["git.example.org:443"]}, "is not a host name"),
        ({**GIT, "hosts": ["git.example.org/repo"]}, "is not a host name"),
        ({**GIT, "hosts": ["Git.Example.org"]}, "is not a host name"),
        ({**GIT, "hosts": ["git.example.org", "git.example.org"]}, "a host more than once"),
        ({**GIT, "hosts": [f"h{i}.example.org" for i in range(11)]}, "at most 10"),
    ],
)
def test_a_launch_the_consent_cannot_show_is_an_install_error(entry, says):
    errors = AppManifest.from_dict(_manifest(provider=_provider(), launches=[entry])).validate()
    assert any(says in e for e in errors), errors


def test_a_program_you_name_is_the_one_word_star_declared_once():
    """``*`` alone says the owner chooses the program. It is not a pattern: ``*x`` is no name."""
    assert AppManifest.from_dict(_manifest(launches=[YOURS])).validate() == []
    starred = AppManifest.from_dict(_manifest(launches=[{**YOURS, "program": "*x"}])).validate()
    assert any("a program's name" in e for e in starred), starred
    twice = AppManifest.from_dict(_manifest(launches=[YOURS, YOURS])).validate()
    assert any("'*' more than once" in e for e in twice), twice


# ── the install review, the card and an update ──────────────────────────────────────────────


def test_the_install_review_names_the_package_npx_runs_and_the_hosts(tmp_path):
    """The review the consent dialog shows is the server's reading of the staged bytes."""
    src = _bundle(tmp_path / "src", _manifest(provider=_provider(), launches=[NPX, GIT]))

    review = app_manager.preview(src)

    assert review.needs_consent, review.error
    npx, git = review.disclosure["launches"]
    assert npx["npmPackage"] == "example-finder"
    assert npx["hosts"] == ["registry.npmjs.org", "finder.example.com"]
    assert git["npmPackage"] == "" and git["hosts"] == ["git.example.org"]
    # The package npx fetches runs as you too, and the lead sentence says so.
    says = review.disclosure["runsAsYou"]
    assert says.startswith(
        "Its provider module, the 2 programs it starts and the npm package npx fetches for it "
        "run as you on this machine."
    ), says
    # It is not something core installs for the app.
    assert review.disclosure["npmPackages"] == []
    assert "the npm package it installs" not in says, says


def test_a_program_you_name_is_shown_as_one(tmp_path):
    m = AppManifest.from_dict(
        _manifest(
            provider=_provider(SETTINGS),
            launches=[{**YOURS, "inheritsWhile": {"setting": "allow_apply", "value": True}}],
        )
    )
    assert m.validate() == []

    d = describe(m)

    assert d["launches"] == [
        {
            "program": "*",
            "why": YOURS["why"],
            "inherits": ["sign-in", "settings"],
            # The setting that gates it, as the owner sees it on the app's Configure page.
            "inheritsWhile": {
                "setting": "allow_apply",
                "label": "Allow gated remediation",
                "value": True,
                "default": False,
            },
            "npmPackage": "",
            "hosts": [],
        }
    ]
    # However many programs your runbooks name, so never "the program it starts".
    assert d["runsAsYou"].startswith(
        "Its provider module and the programs you name for it run as you on this machine."
    ), d["runsAsYou"]


def test_the_store_card_carries_the_package_and_the_hosts(tmp_path):
    root = tmp_path / "local-src"
    _bundle(root, _manifest(launches=[NPX]))
    catalog.add_local_source(str(root))

    [card] = [c for c in catalog._scan_local_sources() if c.name == "example-finder"]

    assert card.launches[0]["npmPackage"] == "example-finder"
    assert card.launches[0]["hosts"] == NPX["hosts"]


@pytest.mark.parametrize(
    "widened",
    [
        {**GIT, "hosts": [*GIT["hosts"], "mirror.example.net"]},
        {**NPX, "npmPackage": "example-finder-next"},
    ],
    ids=["a host", "another package"],
)
def test_an_update_that_reaches_a_new_host_or_runs_another_package_asks_first(tmp_path, widened):
    before = GIT if widened["program"] == "git" else NPX
    v1 = _bundle(tmp_path / "v1", _manifest(launches=[before]))
    assert app_manager.install(v1, confirm=True).ok
    v2 = _bundle(tmp_path / "v2", _manifest(version="1.1.0", launches=[widened]))

    res = app_manager.update(v2)

    assert res.needs_consent and not res.ok, res.error
    assert manager._read_installed("example-finder").version == "1.0.0"


# ── a program you name lets core start nothing ──────────────────────────────────────────────

_REGISTERS = """\
from personalclaw.sdk.acp import register_acp_cli_entry


def create_provider(config=None):
    register_acp_cli_entry(
        cli="example-cli",
        dialect="default",
        command=["/nonexistent/pc-fixture-cli", "acp"],
        extension="example-finder",
    )
    return None
"""


def test_a_program_you_name_does_not_let_core_start_an_agent_cli(tmp_path):
    """``*`` says the owner chooses the program; it names none, so it is no declaration of the
    agent CLI an app asks core to start for every chat."""
    from personalclaw.acp_bundles._register import unregister_acp_cli_entry
    from personalclaw.llm.registry import get_default_registry
    from personalclaw.providers.registry import get_provider_registry

    unregister_acp_cli_entry("example-cli")
    try:
        src = _bundle(
            tmp_path / "src",
            _manifest(provider={**_provider(), "type": "agent"}, launches=[YOURS]),
            _REGISTERS,
        )
        assert app_manager.install(src, confirm=True).ok
        rec = get_provider_registry().get("example-finder")

        assert get_default_registry()._entries.get("acp:example-cli") is None, "core started it"
        assert rec is not None and "lists no program under launches" in rec.error, rec
    finally:
        unregister_acp_cli_entry("example-cli")
