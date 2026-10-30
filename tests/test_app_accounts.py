"""An app's accounts, and a capability's own settings across a rename and a retirement (#3842).

An app that declares account ``settings`` has a first account, kept where its settings always were,
and may add more. Each further account is a full copy of the account settings and of each
capability's own; a capability reads the account it runs under and nothing of another's, every
secret is its account's, and removing an account deletes exactly its keys. Clearing an account
deletes only its own fields' keys, never a capability's nested under its prefix.

An update that renames a capability carries its settings and secrets to the new id in every
account, once, and a stop at any of the move's four steps is finished by the next run. One the
app declares retired has its settings deleted in every account; one dropped without saying so
keeps them, since it may come back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from test_account_and_capability_settings import (
    APP,
    SEARCH_KEY,
    TOKEN,
    _install,
    _manifest,
    _v030_prefixes,
)

from personalclaw.apps import capability_lifecycle, manager
from personalclaw.apps.manifest import AppManifest
from personalclaw.config import loader as config_loader
from personalclaw.config import secret_refs
from personalclaw.config.credentials import credential_names, get_credential
from personalclaw.providers import capability_settings_move as move
from personalclaw.providers import registry as registry_module
from personalclaw.providers.settings import (
    NoSuchAccount,
    ProviderSettings,
    load_stored_capability,
)

pytestmark = pytest.mark.security


@pytest.fixture
def home() -> Path:
    registry_module.reset_provider_registry()
    yield config_loader.config_dir()
    registry_module.reset_provider_registry()


WORK_TOKEN = "fixture-work-token-3b2a1908"
WORK_KEY = "fixture-work-search-key-6d5c4b3a"
LATER_KEY = "fixture-later-search-key-2e4f6a8c"


def _two_accounts() -> str:
    _install(_manifest())
    ProviderSettings.update_account(APP, {"token": TOKEN, "server": "home.example.invalid"})
    ProviderSettings.update_capability(APP, "search", {"search_key": SEARCH_KEY, "max_results": 5})
    work = ProviderSettings.add_account(APP, "Work", "work")
    ProviderSettings.update_account(APP, {"token": WORK_TOKEN}, "work")
    ProviderSettings.update_capability(APP, "search", {"search_key": WORK_KEY}, "work")
    return work


def _owners(work: str = "work") -> dict[str, secret_refs.SecretOwner]:
    return {
        "first": secret_refs.account_owner(APP),
        "first/search": secret_refs.capability_owner(APP, "search"),
        "work": secret_refs.account_owner(APP, work),
        "work/search": secret_refs.capability_owner(APP, "search", work),
    }


def _counts(owners: dict[str, secret_refs.SecretOwner]) -> dict[str, int]:
    """Each owner's own keys: an account's fields, never the capabilities nested under it."""
    return {name: secret_refs.count_direct(owner) for name, owner in owners.items()}


def _held(value: str) -> int:
    """How many stored secrets hold *value*."""
    return sum(1 for name in credential_names() if get_credential(name) == value)


def _in_a_file(home, value: str) -> list[str]:
    return [
        str(p.relative_to(home))
        for p in sorted(home.rglob("*.json"))
        if p.is_file() and value.encode() in p.read_bytes()
    ]


# ── two accounts ─────────────────────────────────────────────────────────────────────────────


def test_each_capability_reads_its_own_account_and_nothing_of_the_other(home) -> None:
    work = _two_accounts()
    assert ProviderSettings.load(APP, "search") == {
        "token": TOKEN,
        "server": "home.example.invalid",
        "search_key": SEARCH_KEY,
        "max_results": 5,
    }
    assert ProviderSettings.load(APP, "search", work) == {
        "token": WORK_TOKEN,
        "search_key": WORK_KEY,
    }, "nothing is inherited from the first account"
    assert ProviderSettings.load(APP, "search", "default") == ProviderSettings.load(APP, "search")
    assert [a["id"] for a in ProviderSettings.list_accounts(APP)] == ["default", work]
    assert _counts(_owners()) == {"first": 1, "first/search": 1, "work": 1, "work/search": 1}
    for value in (TOKEN, SEARCH_KEY, WORK_TOKEN, WORK_KEY):
        assert _held(value) == 1 and _in_a_file(home, value) == []


def test_an_account_resolves_none_of_the_others_keys(home) -> None:
    _two_accounts()
    first_token = secret_refs.account_owner(APP).key("token")
    stored = secret_refs.make_ref(first_token)
    record = manager.app_dir(APP).parent.parent / "extensions" / APP / "instances" / "work.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    data["account"]["token"] = stored
    record.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(secret_refs.ForeignSecretReference):
        ProviderSettings.load_account(APP, "work")


def test_removing_an_account_deletes_exactly_its_keys(home) -> None:
    work = _two_accounts()
    before = _counts(_owners())
    assert ProviderSettings.remove_account(APP, work) == 2
    assert secret_refs.count_owned([secret_refs.account_owner(APP, work).prefix]) == 0
    after = _counts(_owners())
    assert after == before | {"work": 0, "work/search": 0}, "every other owner keeps its keys"
    assert [a["id"] for a in ProviderSettings.list_accounts(APP)] == ["default"]
    with pytest.raises(NoSuchAccount):
        ProviderSettings.load_account(APP, work)


def test_clearing_the_first_account_leaves_every_nested_key(home) -> None:
    _two_accounts()
    before = _counts(_owners())
    assert ProviderSettings.clear_account(APP) == 1
    assert _counts(_owners()) == before | {"first": 0}
    assert ProviderSettings.load(APP, "search")["search_key"] == SEARCH_KEY
    assert ProviderSettings.load(APP, "search", "work")["search_key"] == WORK_KEY
    assert ProviderSettings.load_account(APP) == {}


def test_a_whole_save_of_the_first_account_that_drops_a_field_leaves_every_nested_key(home):
    _two_accounts()
    before = _counts(_owners())
    ProviderSettings.save_account(APP, {"server": "home.example.invalid"})
    assert _counts(_owners()) == before | {"first": 0}
    assert ProviderSettings.load(APP, "search", "work")["token"] == WORK_TOKEN


def _point_at(path: Path, part: tuple[str, ...], field: str, key: str) -> None:
    """Have the stored settings at *path* (under *part*) reference *key* in *field*, as a record
    written by hand or by an older build may."""
    data = json.loads(path.read_text(encoding="utf-8"))
    target = data
    for name in part:
        target = target[name]
    target[field] = secret_refs.make_ref(key)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_a_dropped_reference_to_a_capability_s_key_never_deletes_it(home) -> None:
    work = _two_accounts()
    record = manager.app_dir(APP).parent.parent / "extensions" / APP / "instances" / f"{work}.json"
    first_search = secret_refs.capability_owner(APP, "search").key("search_key")
    work_search = secret_refs.capability_owner(APP, "search", work).key("search_key")
    _point_at(ProviderSettings.config_path(APP), (), "legacy_key", first_search)
    _point_at(record, ("account",), "legacy_key", work_search)
    before = _counts(_owners())

    ProviderSettings.save_account(APP, {"server": "home.example.invalid"})
    ProviderSettings.clear_account(APP, work)

    assert _counts(_owners()) == before | {"first": 0, "work": 0}
    assert ProviderSettings.load(APP, "search")["search_key"] == SEARCH_KEY
    assert ProviderSettings.load(APP, "search", work)["search_key"] == WORK_KEY
    assert _held(SEARCH_KEY) == 1 and _held(WORK_KEY) == 1


def test_clearing_a_further_account_leaves_its_capabilities(home) -> None:
    work = _two_accounts()
    before = _counts(_owners())
    assert ProviderSettings.clear_account(APP, work) == 1
    assert _counts(_owners()) == before | {"work": 0}
    assert ProviderSettings.load(APP, "search", work) == {"search_key": WORK_KEY}


@pytest.mark.parametrize("account", ["default", "Work", "../x", "a/b", "", "-x"])
def test_an_id_that_is_no_further_account_is_refused(home, account) -> None:
    _install(_manifest())
    with pytest.raises(ValueError):
        ProviderSettings.add_account(APP, "x", account)
    with pytest.raises(ValueError):
        ProviderSettings.remove_account(APP, account)
    assert ProviderSettings.list_accounts(APP) == [{"id": "default", "label": ""}]


def test_the_first_account_s_id_never_names_a_record(home) -> None:
    """``default`` names the first account, kept where its settings always were: no record, so
    a ``default.json`` in the instance store is never read or written as an account."""
    from personalclaw.providers import accounts

    _install(_manifest())
    with pytest.raises(ValueError, match="first account"):
        accounts.record_path(APP, "default")
    with pytest.raises(ValueError, match="first account"):
        accounts.checked_account("default")


def test_an_account_the_app_does_not_have_is_refused(home) -> None:
    _install(_manifest())
    for call in (
        lambda: ProviderSettings.load(APP, "search", "ghost"),
        lambda: ProviderSettings.update_account(APP, {"token": "x"}, "ghost"),
        lambda: ProviderSettings.update_capability(APP, "search", {"max_results": 1}, "ghost"),
        lambda: ProviderSettings.remove_account(APP, "ghost"),
    ):
        with pytest.raises(NoSuchAccount):
            call()
    assert not (manager.app_dir(APP).parent.parent / "extensions" / APP).exists()


def test_an_account_added_twice_is_refused(home) -> None:
    _install(_manifest())
    ProviderSettings.add_account(APP, "Work", "work")
    with pytest.raises(ValueError, match="already has an account"):
        ProviderSettings.add_account(APP, "Work again", "work")


def test_account_settings_beside_a_multi_instance_provider_are_refused() -> None:
    raw = json.loads(json.dumps(_manifest()))
    raw["providers"][1]["multiInstance"] = True
    errors = AppManifest.from_dict(raw).validate()
    assert any("providers[1].multiInstance" in e for e in errors), errors


def test_a_further_account_s_keys_are_ones_v030_holds_as_the_app_s(home) -> None:
    work = _two_accounts()
    for owner in (
        secret_refs.account_owner(APP, work),
        secret_refs.capability_owner(APP, "search", work),
    ):
        assert owner.prefix.startswith(_v030_prefixes(APP))


# ── a rename ─────────────────────────────────────────────────────────────────────────────────


def _renamed_files() -> dict[str, Any]:
    return {
        "first/old": load_stored_capability(APP, "search"),
        "first/new": load_stored_capability(APP, "finder"),
        "work/old": load_stored_capability(APP, "search", "work"),
        "work/new": load_stored_capability(APP, "finder", "work"),
    }


def _assert_moved(home) -> None:
    assert ProviderSettings.load(APP, "finder")["search_key"] == SEARCH_KEY
    assert ProviderSettings.load(APP, "finder", "work")["search_key"] == WORK_KEY
    files = _renamed_files()
    assert files["first/old"] == {} and files["work/old"] == {}
    for account in (None, "work"):
        assert (
            secret_refs.count_owned([secret_refs.capability_owner(APP, "search", account).prefix])
            == 0
        )
    for value in (SEARCH_KEY, WORK_KEY):
        assert _held(value) == 1, f"{value} is held once"
        assert _in_a_file(home, value) == []


def test_a_rename_carries_each_account_s_settings_once(home) -> None:
    _two_accounts()
    move.move_on_rename(APP, "search", "finder")
    _assert_moved(home)
    snapshot = (_renamed_files(), sorted(credential_names()))
    move.move_on_rename(APP, "search", "finder")
    assert (_renamed_files(), sorted(credential_names())) == snapshot, "a second run moves nothing"


class _Stop(Exception):
    pass


def _stop_after(monkeypatch, target: Any, name: str, *, times: int = 1) -> None:
    real = getattr(target, name)
    calls = {"n": 0}

    def once(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        calls["n"] += 1
        if calls["n"] == times:
            raise _Stop(name)
        return result

    monkeypatch.setattr(target, name, once)


@pytest.mark.parametrize(
    "step",
    ["store-new", "write-new", "delete-old", "purge-old"],
)
def test_a_rename_stopped_after_each_step_is_finished_by_the_next_run(home, monkeypatch, step):
    from personalclaw.providers import settings as settings_module

    _two_accounts()
    target, name = {
        "store-new": (secret_refs, "store"),
        "write-new": (settings_module, "_write"),
        "delete-old": (Path, "unlink"),
        "purge-old": (secret_refs, "purge"),
    }[step]
    with monkeypatch.context() as stop:
        _stop_after(stop, target, name)
        with pytest.raises(_Stop):
            move.move_on_rename(APP, "search", "finder")
    move.move_on_rename(APP, "search", "finder")
    _assert_moved(home)


def test_both_files_there_keeps_the_new_one_and_finishes_the_old(home) -> None:
    _two_accounts()
    ProviderSettings.update_capability(APP, "finder", {"max_results": 9})
    move.move_on_rename(APP, "search", "finder")
    assert load_stored_capability(APP, "finder") == {"max_results": 9}, "the new file is kept"
    assert load_stored_capability(APP, "search") == {}
    assert secret_refs.count_owned([secret_refs.capability_owner(APP, "search").prefix]) == 0
    assert _held(SEARCH_KEY) == 0, "the old file's secret went with it"


def _renaming_update(version: str = "1.1.0") -> AppManifest:
    raw = json.loads(json.dumps(_manifest()))
    raw["version"] = version
    raw["providers"][1]["id"] = "finder"
    raw["providers"][1]["renamedFrom"] = ["search"]
    return AppManifest.from_dict(raw)


class _Killed(BaseException):
    """A stop no handler catches, as when the process is killed."""


def _kill_after(monkeypatch, target: Any, name: str, when: Any) -> None:
    real = getattr(target, name)

    def then_die(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        if when(*args, **kwargs):
            raise _Killed(name)
        return result

    monkeypatch.setattr(target, name, then_die)


def _prefix(capability: str, account: str | None) -> str:
    return secret_refs.capability_owner(APP, capability, account).prefix


def _killing_steps() -> dict[tuple[str, str], tuple[Any, str, Any]]:
    """Each step of the move in each account, and the call that ends it."""
    from personalclaw.providers import settings as settings_module

    steps: dict[tuple[str, str], tuple[Any, str, Any]] = {}
    for account in (None, "work"):
        shown = account or "first"
        steps[(shown, "store-new")] = (
            secret_refs,
            "store",
            lambda *a, account=account, **k: k["owner"].prefix == _prefix("finder", account),
        )
        steps[(shown, "purge-old")] = (
            secret_refs,
            "purge",
            lambda prefixes, account=account: list(prefixes) == [_prefix("search", account)],
        )
    steps[("first", "write-new")] = (
        settings_module,
        "_write",
        lambda path, *a: path.name == "finder.json",
    )
    steps[("first", "delete-old")] = (
        Path,
        "unlink",
        lambda path, *a, **k: path.name == "search.json",
    )
    # A further account's record takes the new settings and drops the old in one write.
    steps[("work", "write-new")] = (
        settings_module,
        "_write_record",
        lambda app, account, record: account == "work",
    )
    return steps


@pytest.mark.parametrize("account,step", sorted(_killing_steps()))
def test_a_rename_killed_after_each_step_is_finished_by_the_next_start(
    home, monkeypatch, account, step
) -> None:
    from personalclaw.apps import app_manager, app_runtime

    _two_accounts()
    with monkeypatch.context() as kill:
        _kill_after(kill, *_killing_steps()[(account, step)])
        with pytest.raises(_Killed):
            app_manager._record_update(
                APP, AppManifest.from_dict(_manifest()), _renaming_update(), "community"
            )
    move_on_disk = manager._read_installed(APP).capabilityMoves["search"]
    assert move_on_disk["pending"] == [
        capability_lifecycle.mover_name(move.move_on_rename)
    ], "the move is on disk, pending, before its mover runs"

    app_runtime._retry_pending_moves()

    assert manager._read_installed(APP).capabilityMoves == {
        "search": {"to": "finder", "in": "1.1.0"}
    }
    _assert_moved(home)


def test_an_update_s_failed_rename_stays_pending_until_the_next_start(home, monkeypatch) -> None:
    _two_accounts()
    meta = manager.InstalledApp(name=APP, version="1.0.0")
    with monkeypatch.context() as stop:
        _stop_after(stop, secret_refs, "purge")
        capability_lifecycle.after_update(
            meta, AppManifest.from_dict(_manifest()), _renaming_update()
        )
    pending = meta.capabilityMoves["search"]["pending"]
    assert capability_lifecycle.mover_name(move.move_on_rename) in pending
    assert capability_lifecycle.retry_pending_moves(meta) is True
    assert "pending" not in meta.capabilityMoves["search"]
    _assert_moved(home)


# ── a retirement ─────────────────────────────────────────────────────────────────────────────


def _dropping_update(version: str, *, declared: bool) -> AppManifest:
    raw = json.loads(json.dumps(_manifest()))
    raw["version"] = version
    raw["providers"] = raw["providers"][:1]
    if declared:
        raw["retiredCapabilities"] = ["search"]
    return AppManifest.from_dict(raw)


def test_a_declared_retirement_deletes_the_capability_in_every_account(home) -> None:
    _two_accounts()
    meta = manager.InstalledApp(name=APP, version="1.0.0")
    capability_lifecycle.after_update(
        meta, AppManifest.from_dict(_manifest()), _dropping_update("1.1.0", declared=True)
    )
    for account in (None, "work"):
        owner = secret_refs.capability_owner(APP, "search", account)
        assert secret_refs.count_owned([owner.prefix]) == 0
        assert load_stored_capability(APP, "search", account) == {}
    assert ProviderSettings.load_account(APP, "work") == {"token": WORK_TOKEN}


def test_an_undeclared_drop_keeps_the_settings_for_when_it_comes_back(home) -> None:
    _two_accounts()
    meta = manager.InstalledApp(name=APP, version="1.0.0")
    first = AppManifest.from_dict(_manifest())
    dropped = _dropping_update("1.1.0", declared=False)
    capability_lifecycle.after_update(meta, first, dropped)
    assert meta.retiredCapabilities["search"]["declared"] is False
    back = json.loads(json.dumps(_manifest()))
    back["version"] = "1.2.0"
    capability_lifecycle.after_update(meta, dropped, AppManifest.from_dict(back))
    assert ProviderSettings.load(APP, "search")["search_key"] == SEARCH_KEY
    assert ProviderSettings.load(APP, "search", "work")["search_key"] == WORK_KEY


# ── the routes ───────────────────────────────────────────────────────────────────────────────


def _gateway() -> web.Application:
    from personalclaw.dashboard.handlers.apps import register_app_routes

    @web.middleware
    async def owner(request, handler):
        request["user"] = "owner"
        request["app"] = ""
        return await handler(request)

    gw = web.Application(middlewares=[owner])
    register_app_routes(gw)
    return gw


@pytest.mark.asyncio
async def test_the_account_routes_add_read_write_and_remove_an_account(home) -> None:
    _install(_manifest())
    base = f"/api/apps/{APP}/accounts"
    async with TestClient(TestServer(_gateway())) as client:
        added = await client.post(base, json={"label": "Work"})
        assert added.status == 201, await added.text()
        work = (await added.json())["id"]
        listed = await (await client.get(base)).json()
        assert listed["accounts"] == [{"id": "default", "label": ""}, {"id": work, "label": "Work"}]

        url = f"{base}/{work}/config"
        read = await (await client.get(url)).json()
        unguarded = await client.put(url, json={"token": WORK_TOKEN})
        assert unguarded.status == 428, "a write names the copy it replaces"
        put = await client.put(
            url, json={"token": WORK_TOKEN}, headers={"If-Match": f'"{read["revision"]}"'}
        )
        assert put.status == 200, await put.text()
        assert WORK_TOKEN not in await put.text(), "the credential is masked on the way out"
        assert ProviderSettings.load_account(APP, work) == {"token": WORK_TOKEN}

        cap = f"{base}/{work}/capabilities/search/config"
        read = await (await client.get(cap)).json()
        put = await client.put(
            cap, json={"search_key": WORK_KEY}, headers={"If-Match": f'"{read["revision"]}"'}
        )
        assert put.status == 200, await put.text()
        assert ProviderSettings.load(APP, "search", work)["search_key"] == WORK_KEY

        gone = await client.delete(f"{base}/{work}")
        assert gone.status == 200 and (await gone.json())["secretsRemoved"] == 2
        assert (await client.get(url)).status == 404


@pytest.mark.asyncio
async def test_the_account_routes_say_why_not(home) -> None:
    _install(_manifest())
    _install({"name": "plain-app", "version": "1.0.0", "displayName": "Plain", "description": "x"})
    base = f"/api/apps/{APP}/accounts"
    async with TestClient(TestServer(_gateway())) as client:
        assert (await client.get(f"{base}/ghost/config")).status == 404
        assert (await client.get(f"{base}/Not-An-Id/config")).status == 404
        assert (await client.get(f"{base}/default/capabilities/ghost/config")).status == 404
        assert (await client.delete(f"{base}/default")).status == 409
        assert (await client.get("/api/apps/plain-app/accounts")).status == 409
        assert (await client.get("/api/apps/no-such-app/accounts")).status == 404


def test_the_account_routes_hold_an_app_to_its_own() -> None:
    from personalclaw.apps.permissions import ROUTE_AUTHZ

    for route in (
        "GET /api/apps/{name}/accounts",
        "POST /api/apps/{name}/accounts",
        "DELETE /api/apps/{name}/accounts/{account}",
        "GET /api/apps/{name}/accounts/{account}/config",
        "PUT /api/apps/{name}/accounts/{account}/config",
        "GET /api/apps/{name}/accounts/{account}/capabilities/{capability}/config",
        "PUT /api/apps/{name}/accounts/{account}/capabilities/{capability}/config",
    ):
        assert ROUTE_AUTHZ[route].owns is not None, route


_STRAY = "/api/apps/stray-folder/accounts"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,url",
    [
        ("GET", _STRAY),
        ("POST", _STRAY),
        ("DELETE", f"{_STRAY}/work"),
        ("GET", f"{_STRAY}/work/config"),
        ("GET", f"{_STRAY}/default/config"),
        ("PUT", f"{_STRAY}/default/config"),
        ("GET", f"{_STRAY}/default/capabilities/search/config"),
        ("PUT", f"{_STRAY}/default/capabilities/search/config"),
    ],
)
async def test_a_folder_whose_manifest_names_another_app_has_no_accounts(home, method, url) -> None:
    """Each account route names every file by what the gateway knows (the installed manifest's
    own name, an account record that exists, a declared capability id), never by what the
    request typed, so a folder holding another app's manifest is no app here, even with an
    account record of its own beside it."""
    _install(_manifest())
    ProviderSettings.add_account(APP, "Work", "work")
    stray = manager.app_dir("stray-folder")
    stray.mkdir(parents=True)
    (stray / "app.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    instances = manager.app_dir(APP).parent.parent / "extensions" / "stray-folder" / "instances"
    instances.mkdir(parents=True)
    planted = instances / "work.json"
    planted.write_text(json.dumps({"kind": "account", "display_name": "Stray"}), encoding="utf-8")
    before = planted.read_bytes()
    async with TestClient(TestServer(_gateway())) as client:
        answer = await client.request(
            method, url, json={"label": "x", "max_results": 1}, headers={"If-Match": '"x"'}
        )
        assert answer.status == 404, await answer.text()
    assert [a["id"] for a in ProviderSettings.list_accounts(APP)] == ["default", "work"]
    assert sorted(p.name for p in instances.iterdir()) == ["work.json"], "no account was added"
    assert planted.read_bytes() == before, "the stray record is as it was"
    assert not (stray / "data").exists(), "nothing was written under the stray folder"


# ── the review's additions ───────────────────────────────────────────────────────────────────


def _instances() -> Path:
    return manager.app_dir(APP).parent.parent / "extensions" / APP / "instances"


def test_a_rename_copies_no_key_another_account_holds(home, caplog) -> None:
    """A further account's record that references the first account's key fails that account's
    move, as every read of it does: nothing is copied, its move stays pending, and the other
    accounts move."""
    _two_accounts()
    later = ProviderSettings.add_account(APP, "Later", "zz-later")
    ProviderSettings.update_capability(APP, "search", {"search_key": LATER_KEY}, later)
    record = _instances() / "work.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    data["capabilities"]["search"]["search_key"] = secret_refs.make_ref(
        secret_refs.account_owner(APP).key("token")
    )
    record.write_text(json.dumps(data), encoding="utf-8")
    meta = manager.InstalledApp(name=APP, version="1.0.0")
    with caplog.at_level("WARNING", logger="personalclaw.providers.capability_settings_move"):
        capability_lifecycle.after_update(
            meta, AppManifest.from_dict(_manifest()), _renaming_update()
        )
    pending = meta.capabilityMoves["search"]["pending"]
    assert capability_lifecycle.mover_name(move.move_on_rename) in pending
    assert "moving search to finder in account work failed" in caplog.text
    assert ProviderSettings.load(APP, "finder")["search_key"] == SEARCH_KEY, "the first moved"
    assert ProviderSettings.load(APP, "finder", later)["search_key"] == LATER_KEY, "so did zz-later"
    held = json.loads(record.read_text(encoding="utf-8"))["capabilities"]
    assert "search" in held and "finder" not in held, "work's move waits"
    assert _held(TOKEN) == 1, "the first account's token is never copied"


def test_an_id_whose_file_is_no_account_is_never_written_over(home) -> None:
    _install(_manifest())
    _instances().mkdir(parents=True, exist_ok=True)
    plain = _instances() / "x.json"
    plain.write_text(json.dumps({"id": "x", "options": {"region": "eu"}}), encoding="utf-8")
    before = plain.read_bytes()
    with pytest.raises(ValueError, match="no account"):
        ProviderSettings.add_account(APP, "X", "x")
    assert plain.read_bytes() == before
    assert [a["id"] for a in ProviderSettings.list_accounts(APP)] == ["default"]


@pytest.mark.asyncio
async def test_each_row_of_the_account_routes_names_the_account(home) -> None:
    from personalclaw.sel import sel

    _install(_manifest())
    base = f"/api/apps/{APP}/accounts"
    async with TestClient(TestServer(_gateway())) as client:
        work = (await (await client.post(base, json={"label": "Work"})).json())["id"]
        for url, body in (
            (f"{base}/{work}/config", {"token": WORK_TOKEN}),
            (f"{base}/{work}/capabilities/search/config", {"search_key": WORK_KEY}),
        ):
            read = await (await client.get(url)).json()
            put = await client.put(url, json=body, headers={"If-Match": f'"{read["revision"]}"'})
            assert put.status == 200, await put.text()
        assert (await client.delete(f"{base}/{work}")).status == 200
    rows = [
        (r["operation"], r["resources"])
        for r in reversed(sel().recent(50))
        if str(r.get("operation", "")).startswith("apps.")
    ]
    assert rows == [
        ("apps.account_add", f"{APP} account={work}"),
        ("apps.config", f"{APP} account={work}"),
        ("apps.config", f"{APP} account={work} capability=search"),
        ("apps.account_remove", f"{APP} account={work} secrets_removed=2"),
    ]


def test_an_account_saved_without_a_label_lists_with_an_empty_one(home) -> None:
    work = _two_accounts()
    record = _instances() / f"{work}.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    del data["display_name"]
    record.write_text(json.dumps(data), encoding="utf-8")
    assert ProviderSettings.list_accounts(APP)[1] == {"id": work, "label": ""}


def test_an_account_is_never_read_through_a_linked_folder(home, tmp_path) -> None:
    from personalclaw.providers.accounts import read_record

    work = _two_accounts()
    elsewhere = tmp_path / "elsewhere"
    _instances().rename(elsewhere)
    _instances().symlink_to(elsewhere, target_is_directory=True)
    assert read_record(APP, work) is None
