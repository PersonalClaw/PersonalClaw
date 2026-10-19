"""An installed app cannot read or drive your first-run setup.

**The holes, measured on origin/main 1321e6180 (#3670) with the fixture app below.**
``/api/onboarding`` was in no ``SECURITY_ROUTE_FAMILIES`` root, so an app that declared it
reached five of its eight routes. Bringing your setup over and the one-click bind were already
owner-only subtrees.

1. ``GET /api/onboarding`` answered the app with ``chat_model_refs``, the model your chats are
   bound to. ``GET /api/onboarding/model-check`` names the same binding (``bound``).
2. ``POST /api/onboarding/local-model/scan`` ran your opt-in LAN sweep for the app and handed it
   what the sweep found, and ``GET /api/onboarding/local-model`` probed this machine for a model
   server.
3. ``POST /api/onboarding/state`` let the app move your setup's progress, which decides what the
   wizard shows you next.

Same harness as ``test_apps_cannot_change_your_models.py`` (#3681): the real token middleware and
the real ``app_permission_middleware``, a fixture app you installed through preview → consent →
install, and the token you minted for it, sent both ways an app sends one. The route matrix
answers with a stand-in at each real template. The leak cases run the real handlers, with the two
network probes replaced by recorders, so no case here scans anything.

The app is installed once for the module (``shared_app_rows.SharedApp``), and each route and each
leak case is a row on that install: the home is put back to what the install left before every
row and checked by digest, every session is signed out, and the fenced registries are read
around it. One leak case stays its own test, on an app installed for it alone.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from shared_app_rows import Row, SharedApp, planted_rows
from table_rows import row
from test_apps_cannot_change_your_models import _gateway, _Gateway

# Imported before any test patches `config_dir`: a module first imported under a patch keeps the
# mock bound.
from personalclaw import local_model_detect
from personalclaw.apps import manager
from personalclaw.dashboard import handlers_system
from personalclaw.dashboard.handlers import local_model, model_check

pytestmark = pytest.mark.security


APP = "probe-onboarding"
FAMILY = "/api/onboarding"
BIND = "/api/onboarding/local-model/bind"
#: Your setup: a model provider, and your chats bound to its model.
YOURS = "Yours"
YOUR_REF = "Yours:vendor-chat-1"

#: Every route in the family, each with what a hostile call would send. Railed below against the
#: route census, so a route added tomorrow has to be listed here.
ONBOARDING_ROUTES: list[tuple[str, str, Any]] = [
    ("GET", "/api/onboarding", None),
    ("POST", "/api/onboarding/state", {"step": "done"}),
    ("GET", "/api/onboarding/model-check", None),
    ("GET", "/api/onboarding/local-model", None),
    ("POST", "/api/onboarding/local-model/scan", None),
    ("POST", BIND, {"endpoint": "http://127.0.0.1:11434", "bind_chat": True}),
    ("GET", "/api/onboarding/import", None),
    ("POST", "/api/onboarding/import", {"items": []}),
    ("GET", "/api/onboarding/import/job", None),
    ("DELETE", "/api/onboarding/import/job", None),
    ("GET", "/api/onboarding/import/stream", None),
]


def _bundle(root: Path) -> Path:
    """A fixture app that declares your first-run setup's routes, and ships nothing."""
    d = root / "bundles" / APP
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": APP,
        "version": "1.0.0",
        "displayName": "Probe Onboarding",
        "description": "A fixture app that declares the routes of your first-run setup.",
        "permissions": {"api": [FAMILY]},
    }
    (d / "app.json").write_text(json.dumps(manifest, indent=1))
    return d


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_sessions() -> Iterator[None]:
    from personalclaw.dashboard.token_auth import revoke_all_sessions

    revoke_all_sessions()
    yield
    revoke_all_sessions()


@pytest.fixture
def sel_rows() -> Iterator[MagicMock]:
    """Every row the permission middleware writes (it imports ``sel`` at the refusal)."""
    fake = MagicMock()
    with patch("personalclaw.sel.sel", return_value=fake):
        yield fake.log_api_access


@pytest.fixture
def probes(monkeypatch) -> dict[str, list[str]]:
    """The two probes that look for a model server, as recorders: nothing here touches your
    machine's ports or your network, and each case can say whether a probe ran."""
    ran: dict[str, list[str]] = {"this machine": [], "your network": []}

    def on_this_machine(*_args: Any, **_kwargs: Any) -> None:
        ran["this machine"].append("probed")

    def on_your_network(*_args: Any, **_kwargs: Any) -> list:
        ran["your network"].append("swept")
        return []

    monkeypatch.setattr(local_model_detect, "detect_localhost", on_this_machine)
    monkeypatch.setattr(local_model_detect, "scan_local_network", on_your_network)
    return ran


def _denials(rows: MagicMock, path: str) -> list:
    return [
        c
        for c in rows.call_args_list
        if c.kwargs.get("caller") == f"app:{APP}"
        and c.kwargs.get("outcome") == "denied"
        and c.kwargs.get("source") == "app_permissions"
        and c.kwargs.get("resources") == path
    ]


async def _installed(gw: _Gateway, home: Path) -> str:
    """Your install of the fixture app, and the token you mint for it."""
    await gw.install(_bundle(home))
    return str((await gw.ok("POST", f"/api/apps/{APP}/token"))["token"])


def _stand_ins(reached: list[tuple[str, str]]) -> list[tuple[str, str, Any]]:
    """A stand-in for every handler in :data:`ONBOARDING_ROUTES`, recording who reached it."""

    async def stand_in(request: web.Request) -> web.Response:
        reached.append((request.get("app", ""), request.method))
        return web.json_response({"reached": True})

    return [(method, template, stand_in) for method, template, _body in ONBOARDING_ROUTES]


def _real_routes() -> list[tuple[str, str, Any]]:
    """The real handlers of the five routes that were open, at the templates the gateway
    registers them under (railed against the census below)."""
    return [
        ("GET", "/api/onboarding", handlers_system.api_onboarding),
        ("POST", "/api/onboarding/state", handlers_system.api_onboarding_state),
        ("GET", "/api/onboarding/model-check", model_check.api_onboarding_model_check),
        ("GET", "/api/onboarding/local-model", local_model.api_local_model_detect),
        ("POST", "/api/onboarding/local-model/scan", local_model.api_local_model_scan),
    ]


def _your_setup() -> None:
    """A model provider in ``config.json`` and your chats bound to its model, as Settings leaves
    them. The binding survives a read only while its provider is configured."""
    from personalclaw.config.loader import config_path
    from personalclaw.providers.use_cases import save_active_models

    path = config_path()
    document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    document["providers"] = [
        {
            "name": YOURS,
            "type": "vendor-models",
            "model": "vendor-chat-1",
            "options": {"endpoint": "https://yours.example/v1"},
        }
    ]
    path.write_text(json.dumps(document), encoding="utf-8")
    save_active_models({"chat": [YOUR_REF]})


#: The fixture app, installed once for the module.
SHARED = SharedApp(APP, _bundle, _gateway)


# ── 1. Every route in the family is yours ───────────────────────────────────────────────────────


def _route_rows(reached: list[tuple[str, str]]) -> list[tuple[str, object]]:
    """A row per route of :data:`ONBOARDING_ROUTES`, and one for ``HEAD``: the app refused both
    ways it sends its token, each refusal an SEL row naming it, and only your request reaching the
    stand-in."""

    def refused(method: str, template: str, body: Any):
        async def case(r: Row) -> None:
            reached.clear()
            as_sdk = await r.gw.call(method, template, body, app_token=r.token)
            as_backend = await r.gw.call(method, template, body, app_token=r.token, as_backend=True)
            yours = await r.gw.call(method, template, body)
            for status, text in (as_sdk, as_backend):
                assert status == 403, text
                assert "owner-only capability, not grantable to an app" in text, text
            assert len(r.denials(template)) == 2, "each refusal leaves an SEL row naming the app"
            assert yours[0] == 200, yours
            assert reached == [("", method)], "only your request reached the handler"

        return case

    async def head_is_the_same_read(r: Row) -> None:
        reached.clear()
        status, _text = await r.gw.call("HEAD", FAMILY, app_token=r.token)
        yours, _ = await r.gw.call("HEAD", FAMILY)
        assert status == 403
        assert r.denials(FAMILY)
        assert yours == 200 and reached == [("", "HEAD")]

    rows = [(f"{m} {t}", refused(m, t, b)) for m, t, b in ONBOARDING_ROUTES]
    return [*rows, (f"HEAD {FAMILY}", head_is_the_same_read)]


@pytest.mark.asyncio
async def test_an_app_is_refused_every_route_and_you_are_not(
    subtests, tmp_path_factory, monkeypatch, probes
) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    rows = _route_rows(reached)
    assert len(rows) == len(ONBOARDING_ROUTES) + 1, "a route was left out of the table"
    await SHARED.run(subtests, rows, _stand_ins(reached), label="route")


@pytest.mark.asyncio
async def test_a_row_that_leaves_state_behind_fails_the_next_row(
    tmp_path_factory, monkeypatch, probes
) -> None:
    """The table's isolation, shown rather than assumed: a row that leaves the home, the SEL rows
    or a fenced registry changed is caught, by name, in the row after it."""
    await SHARED.home(tmp_path_factory, monkeypatch)
    reached: list[tuple[str, str]] = []
    probe = _route_rows(reached)[0]
    failed = await planted_rows(SHARED, _stand_ins(reached), probe)
    name = probe[0]
    # Reset as every row is, a grant left in the home and an SEL row reach nothing after them.
    assert failed["home"]["reset"] == {} and failed["sel"]["reset"] == {}, failed
    # Without the reset, the row after them fails, by name, on the check that guards it.
    assert "a home other than the installed app's" in failed["home"]["no reset"][name]
    assert "planted-grant.json" in failed["home"]["no reset"][name]
    assert "Security Event Log rows" in failed["sel"]["no reset"][name]
    # A fenced registry is caught in the row that changed it, and again in the row after.
    assert set(failed["fenced"]["reset"]) == {"planted fenced", name}, failed["fenced"]
    assert all("left registries" in why for why in failed["fenced"]["reset"].values())


# ── 2. What a refusal keeps from an app ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_it_does_not_learn_which_model_your_chats_run_on(home, sel_rows, probes) -> None:
    """The family's end-to-end refusal on an app installed for this test alone."""
    async with _gateway(_real_routes()) as gw:
        token = await _installed(gw, home)
        _your_setup()
        status_read = await gw.call("GET", FAMILY, app_token=token)
        model_check_read = await gw.call("GET", "/api/onboarding/model-check", app_token=token)
        yours = await gw.ok("GET", FAMILY)
    assert yours["chat_model_refs"] == [YOUR_REF], "you still read your own setup"
    for status, text in (status_read, model_check_read):
        assert YOUR_REF not in text, text
        assert status == 403, text
    assert _denials(sel_rows, FAMILY)
    assert _denials(sel_rows, "/api/onboarding/model-check")


async def _cannot_look_for_model_servers(r: Row, probes: dict[str, list[str]]) -> None:
    sweep = await r.gw.call("POST", "/api/onboarding/local-model/scan", app_token=r.token)
    probe = await r.gw.call("GET", "/api/onboarding/local-model", app_token=r.token)
    for_the_app = {where: list(ran) for where, ran in probes.items()}
    await r.gw.ok("POST", "/api/onboarding/local-model/scan")
    await r.gw.ok("GET", "/api/onboarding/local-model")
    assert for_the_app == {"this machine": [], "your network": []}, "nothing looked for the app"
    assert probes == {"this machine": ["probed"], "your network": ["swept"]}, "you still can"
    assert [sweep[0], probe[0]] == [403, 403], (sweep, probe)
    assert r.denials("/api/onboarding/local-model/scan")
    assert r.denials("/api/onboarding/local-model")


async def _cannot_move_your_setups_progress(r: Row, probes: dict[str, list[str]]) -> None:
    from personalclaw.onboarding import load_onboarding_state

    await r.gw.ok("POST", "/api/onboarding/state", {"step": "import"})
    moved = await r.gw.call(
        "POST",
        "/api/onboarding/state",
        {"step": "done", "first_success": {"knowledge": True}},
        app_token=r.token,
    )
    state = load_onboarding_state()
    assert state["step"] == "import", "your setup resumes where you left it"
    assert state["first_success"]["knowledge"] is False
    assert moved[0] == 403, moved
    assert r.denials("/api/onboarding/state")


#: What a refusal keeps from an app, each run through the real handlers on the shared install.
_LEAKS = [
    ("it cannot look for model servers on your machine or network", _cannot_look_for_model_servers),
    ("it cannot move your setup's progress", _cannot_move_your_setups_progress),
]


@pytest.mark.asyncio
async def test_what_an_app_no_longer_gets(subtests, tmp_path_factory, monkeypatch, probes) -> None:
    await SHARED.home(tmp_path_factory, monkeypatch)

    def with_probes(case):
        async def run(r: Row) -> None:
            for ran in probes.values():
                ran.clear()
            await case(r, probes)

        return run

    await SHARED.run(subtests, [(n, with_probes(c)) for n, c in _LEAKS], _real_routes())


# ── 3. The route table declares every route in the family ──────────────────────────────────────


def _census() -> set[str]:
    from personalclaw.manifest_reference import _routes_from_ast

    return {
        f"{r['method']} {r['path']}"
        for r in _routes_from_ast()
        if r["path"] == FAMILY or r["path"].startswith(FAMILY + "/")
    }


def test_the_route_table_declares_your_setup(subtests) -> None:
    from personalclaw.apps.permissions import (
        READ_DECLARED_FAMILIES,
        ROUTE_AUTHZ,
        SECURITY_ROUTE_FAMILIES,
        OwnerOnly,
        owner_only_api_reason,
    )
    from personalclaw.dashboard import handlers

    census = _census()
    with row(subtests, check="the family is declared, reads included"):
        assert FAMILY in SECURITY_ROUTE_FAMILIES
        assert FAMILY in READ_DECLARED_FAMILIES, "an app reads none of your setup"
    with row(subtests, check="every route in the family is driven here"):
        assert len(census) >= 8, f"only {len(census)} onboarding routes — vacuous"
        driven = {f"{m} {t}" for m, t, _body in ONBOARDING_ROUTES}
        assert census == driven, census ^ driven
        real = {f"{m} {t}" for m, t, _handler in _real_routes()}
        assert real <= census, "a real handler at a template nothing registers"
        # The gateway registers these two through the `handlers` package.
        assert handlers.api_onboarding is handlers_system.api_onboarding
        assert handlers.api_onboarding_state is handlers_system.api_onboarding_state
    with row(subtests, check="no app is granted any of it"):
        # No shipped app calls these routes. One that needs a read gets an `AppMay` row saying
        # why it is safe, and a case here.
        granted = sorted(
            key
            for key in census
            if not owner_only_api_reason(key.split(" ", 1)[1])
            and not isinstance(ROUTE_AUTHZ.get(key), OwnerOnly)
        )
        assert not granted, granted
