"""An installed pack can be uninstalled, and uninstalling never removes a copy you edited.

Settings → Packs listed each installed pack with Finish setup, Deploy roster, Add triggers and
Check for update — and no way to remove it. ``dashboard/handlers/packs.py`` registered no uninstall
route and ``packs/`` had no removal function at all, so a pack you tried once stayed on the machine
for good: its skills in every agent's skill list, its agent definitions, its staged automations.

Uninstall is the §1 update rule run to its end (``packs/uninstall.py``). The install ledger stamped
a digest of every component as it landed; a component whose bytes still match is the pack's copy and
goes, and one whose bytes changed is yours now and stays, named, with the reason. An agent or an
automation you DEPLOYED from the pack is a live row in your own stores, which you may have changed
since, so a pack with one still live is refused, naming each and where to remove it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import manager
from personalclaw.packs import bundled as pack_bundled

PACK = "personal-cfo"
TRIGGER_ID = "pack-personal-cfo-spending-digest"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(root))
    return root


def _gateway() -> web.Application:
    from personalclaw.dashboard.handlers.packs import register_pack_routes

    app = web.Application()
    register_pack_routes(app)
    return app


def _skip_connectors() -> dict[str, dict[str, str]]:
    source = pack_bundled.get_bundled(PACK)
    assert source is not None
    declared = json.loads((source.source / "connectors.json").read_text(encoding="utf-8"))
    return {str(row["name"]): {"mode": "skip"} for row in declared}


async def _install(client: TestClient) -> None:
    resp = await client.post(
        f"/api/packs/bundled/{PACK}/install", json={"connector_choices": _skip_connectors()}
    )
    assert resp.status == 200, await resp.text()


def _ledger(home: Path) -> dict[str, Any]:
    return json.loads((home / "packs" / "installed.json").read_text(encoding="utf-8"))


def _component_paths(home: Path) -> dict[str, Path]:
    locks = _ledger(home)[PACK]["component_locks"]
    return {ref: home / lock["path"] for ref, lock in locks.items()}


async def _uninstall(client: TestClient, confirm: bool) -> tuple[int, dict[str, Any]]:
    resp = await client.post(f"/api/packs/{PACK}/uninstall", json={"confirm": confirm})
    return resp.status, await resp.json()


@pytest.mark.asyncio
async def test_the_dry_run_names_everything_and_writes_nothing(home) -> None:
    async with TestClient(TestServer(_gateway())) as client:
        await _install(client)
        paths = _component_paths(home)
        status, body = await _uninstall(client, confirm=False)

    assert status == 200, body
    plan = body["uninstall"]
    assert plan["applied"] is False
    assert sorted(plan["removed"]) == sorted(paths), "every component the pack installed"
    assert plan["kept"] == [] and plan["missing"] == [] and plan["in_use"] == []
    # Vacuity: the pack really did put all seven components and its setup skill here.
    assert len(paths) == 8
    assert all(p.exists() for p in paths.values()), "a dry run is a read"
    assert PACK in _ledger(home)


@pytest.mark.asyncio
async def test_confirming_removes_the_pack_and_everything_it_installed(home) -> None:
    async with TestClient(TestServer(_gateway())) as client:
        await _install(client)
        paths = _component_paths(home)
        status, body = await _uninstall(client, confirm=True)
        listed = await (await client.get("/api/packs/installed")).json()
        store = await (await client.get("/api/packs/bundled")).json()

    assert status == 200, body
    assert body["uninstall"]["applied"] is True
    assert [p for p in paths.values() if p.exists()] == [], "no component is left behind"
    assert not (home / "packs" / "staged" / PACK).exists(), "nor its staging"
    assert listed["packs"] == []
    assert PACK in {p["name"] for p in store["packs"]}, "the store still offers it"
    # A definition's own directory goes with its only file; the roots every definition lives in
    # stay.
    assert not (home / "agents" / "cfo").exists()
    assert not (home / "workflows" / "defs" / "cfo-monthly-review").exists()
    for root in ("skills", "agents", "prompts", "workflows/defs"):
        assert (home / root).is_dir(), root


@pytest.mark.asyncio
async def test_a_component_you_edited_stays_and_says_why(home) -> None:
    async with TestClient(TestServer(_gateway())) as client:
        await _install(client)
        edited = home / "skills" / "cfo-budget-review" / "SKILL.md"
        edited.write_text(edited.read_text(encoding="utf-8") + "\nMy own rule.\n", encoding="utf-8")
        status, body = await _uninstall(client, confirm=True)

    assert status == 200, body
    assert body["uninstall"]["kept"] == [
        {
            "ref": "skill:cfo-budget-review",
            "reason": "you edited it after it was installed, so it stays",
        }
    ]
    assert "skill:cfo-budget-review" not in body["uninstall"]["removed"]
    assert edited.read_text(encoding="utf-8").endswith("My own rule.\n"), "your copy is untouched"
    assert not (home / "skills" / "cfo-statement-fetch").exists(), "its unedited sibling is gone"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recorded",
    [
        "../outside.txt",  # out of the home altogether
        "skills/cfo-budget-review/../../outside.txt",  # the same, through a component's own path
        "prompts/other-thing.yaml",  # the right root, but not the component the ref names
    ],
)
async def test_a_ledger_path_that_is_not_where_the_component_installs_is_never_deleted(
    home, tmp_path, recorded
) -> None:
    """The ledger is a file under the home; a path in it must not become a way to delete an
    arbitrary file. A matching digest does not make a wrong location right."""
    from personalclaw.packs.update import component_digest

    async with TestClient(TestServer(_gateway())) as client:
        await _install(client)
        victim = (home / recorded).resolve()
        victim.parent.mkdir(parents=True, exist_ok=True)
        victim.write_text("not the pack's", encoding="utf-8")
        ledger = _ledger(home)
        ledger[PACK]["component_locks"]["prompt:cfo-spending-digest"] = {
            "source": "pack:personal-cfo@1.0.0",
            "computedHash": component_digest(victim),
            "path": recorded,
        }
        (home / "packs" / "installed.json").write_text(json.dumps(ledger), encoding="utf-8")
        status, body = await _uninstall(client, confirm=True)

    assert status == 200, body
    assert victim.read_text(encoding="utf-8") == "not the pack's"
    kept = {k["ref"]: k["reason"] for k in body["uninstall"]["kept"]}
    assert "outside where packs install it" in kept["prompt:cfo-spending-digest"]


@pytest.mark.asyncio
async def test_a_symlinked_component_is_never_followed(home, tmp_path) -> None:
    from personalclaw.packs.update import component_digest

    async with TestClient(TestServer(_gateway())) as client:
        await _install(client)
        prompt = home / "prompts" / "cfo-spending-digest.yaml"
        victim = tmp_path / "victim.yaml"
        victim.write_bytes(prompt.read_bytes())
        prompt.unlink()
        prompt.symlink_to(victim)
        assert component_digest(prompt) == component_digest(victim), "the digest alone would pass"
        status, body = await _uninstall(client, confirm=True)

    assert status == 200, body
    assert victim.exists() and prompt.is_symlink()
    assert "prompt:cfo-spending-digest" in {k["ref"] for k in body["uninstall"]["kept"]}


@pytest.mark.asyncio
async def test_a_pack_still_in_use_is_refused_naming_what_uses_it(home) -> None:
    from personalclaw.config.loader import AppConfig
    from personalclaw.triggers.store import TriggerStore

    async with TestClient(TestServer(_gateway())) as client:
        await _install(client)
        assert (await client.post(f"/api/packs/{PACK}/roster/deploy", json={})).status == 200
        assert (await client.post(f"/api/packs/{PACK}/triggers/deploy", json={})).status == 200
        paths = _component_paths(home)

        dry_status, dry = await _uninstall(client, confirm=False)
        status, body = await _uninstall(client, confirm=True)
        assert all(p.exists() for p in paths.values()), "a refusal removes nothing"
        assert PACK in _ledger(home)

        # Remove what came from it, the way a user would — and then it goes.
        cfg = AppConfig.load()
        del cfg.agents["cfo"]
        cfg.save()
        assert TriggerStore(home).delete(TRIGGER_ID)
        after_status, after = await _uninstall(client, confirm=True)

    trigger_name = next(u["name"] for u in dry["uninstall"]["in_use"] if u["kind"] == "automation")
    assert dry_status == 200
    assert dry["uninstall"]["in_use"] == [
        {"kind": "agent", "id": "cfo", "name": "cfo"},
        {"kind": "automation", "id": TRIGGER_ID, "name": trigger_name},
    ]
    assert status == 409, body
    assert body["error"]["code"] == "pack_in_use"
    assert body["error"]["message"] == (
        f"personal-cfo is still in use — the agent cfo and the automation “{trigger_name}” came "
        "from it. Remove them in Agents and Automations, then uninstall."
    )
    assert after_status == 200 and after["uninstall"]["applied"] is True, after


@pytest.mark.asyncio
async def test_uninstalling_a_pack_that_is_not_installed_says_so(home) -> None:
    async with TestClient(TestServer(_gateway())) as client:
        resp = await client.post("/api/packs/nope/uninstall", json={"confirm": True})
        body = await resp.json()
    assert resp.status == 404
    assert body["error"]["code"] == "pack_not_installed"


# ── only the owner ─────────────────────────────────────────────────────────────────


APP = "probe-packs-app"


def _install_app(root: Path) -> None:
    appdir = root / "apps" / APP
    appdir.mkdir(parents=True, exist_ok=True)
    (appdir / "app.json").write_text(
        json.dumps(
            {
                "name": APP,
                "version": "1.0.0",
                "displayName": APP,
                "description": "x",
                "permissions": {"api": ["/api/packs"]},
            }
        ),
        encoding="utf-8",
    )
    (appdir / "installed.json").write_text(
        json.dumps({"name": APP, "version": "1.0.0", "enabled": True}), encoding="utf-8"
    )


@contextmanager
def _scoped_home(root: Path) -> Iterator[Path]:
    with (
        patch("personalclaw.config.loader.config_dir", return_value=root),
        patch.object(manager, "config_dir", return_value=root),
    ):
        yield root


@pytest.mark.asyncio
async def test_an_app_cannot_uninstall_your_packs(tmp_path) -> None:
    from personalclaw.dashboard.server import app_permission_middleware

    @web.middleware
    async def identity(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        request["app"] = APP
        return await handler(request)

    async def reached(request: web.Request) -> web.Response:
        return web.json_response({"reached": True})

    app = web.Application(middlewares=[identity, app_permission_middleware])
    app.router.add_route("POST", "/api/packs/{name}/uninstall", reached)
    fake = MagicMock()
    with _scoped_home(tmp_path) as root, patch("personalclaw.sel.sel", return_value=fake):
        _install_app(root)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(f"/api/packs/{PACK}/uninstall", json={"confirm": True})
    assert resp.status == 403
    [row] = [
        c
        for c in fake.log_api_access.call_args_list
        if c.kwargs.get("caller") == f"app:{APP}" and c.kwargs.get("outcome") == "denied"
    ]
    assert "owner-only" in row.kwargs["error"]
