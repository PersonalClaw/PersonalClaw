"""An app's settings in two parts: the account every capability shares, and each capability's own
(#3842).

A test app with two capabilities reads one shared token plus each its own option; a capability's
own settings sit in their own file under their own secret owner, and go with the capability. The
move that carries a capability's settings out of the shared file runs once, survives a stop half
way, and leaves an app that declares no account settings exactly as it was.

The released core (v0.3.0) keeps one settings file, and this change must not cost a home that goes
back to it anything: it still reads every app's settings and resolves every stored secret, and it
refuses an app that declares account settings rather than running it without its capabilities'.
``_v030_load`` is v0.3.0's read, frozen here as released, so a later change to this core's code
cannot move what it proves.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps import manager
from personalclaw.apps.manifest import AppManifest
from personalclaw.config import loader as config_loader
from personalclaw.config import secret_refs
from personalclaw.config.credentials import credential_names, get_credential
from personalclaw.providers import registry as registry_module
from personalclaw.providers.capability_settings_move import move_capability_settings
from personalclaw.providers.settings import ProviderSettings, load_stored, load_stored_capability

pytestmark = pytest.mark.security

APP = "duo-mail"
PLAIN = "plain-app"
TOKEN = "fixture-account-token-7c6b5a49"
SEARCH_KEY = "fixture-search-key-0f1e2d3c"
PLAIN_KEY = "fixture-plain-key-5a6b7c8d"

ACCOUNT = {
    "type": "object",
    "properties": {
        "token": {"type": "string", "x-meta": {"sensitive": True}},
        "server": {"type": "string"},
    },
}
INBOX = {
    "type": "object",
    "properties": {"folder": {"type": "string"}, "purpose": {"type": "string"}},
}
SEARCH = {
    "type": "object",
    "properties": {
        "max_results": {"type": "integer"},
        "search_key": {"type": "string", "x-meta": {"sensitive": True}},
    },
}


def _manifest(name: str = APP, **fields: Any) -> dict[str, Any]:
    return {
        "name": name,
        "version": "1.0.0",
        "displayName": "Duo Mail",
        "description": "Reads and searches your mail.",
        "requiresCoreFeatures": ["account-settings"],
        "settings": ACCOUNT,
        "providers": [
            {
                "type": "inbox",
                "implementation": "provider:inbox",
                "id": "inbox",
                "label": "Read your mail",
                "settingsSchema": INBOX,
            },
            {
                "type": "search",
                "implementation": "provider:search",
                "id": "search",
                "label": "Search your mail",
                "settingsSchema": SEARCH,
            },
        ],
    } | fields


def _install(raw: dict[str, Any]) -> AppManifest:
    folder = manager.app_dir(raw["name"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "app.json").write_text(json.dumps(raw), encoding="utf-8")
    manager._write_installed(
        raw["name"], manager.InstalledApp(name=raw["name"], version=raw["version"])
    )
    return AppManifest.from_dict(raw)


@pytest.fixture
def home() -> Path:
    registry_module.reset_provider_registry()
    yield config_loader.config_dir()
    registry_module.reset_provider_registry()


def _hits(root: Path, needle: str) -> list[str]:
    return [
        str(p.relative_to(root))
        for p in sorted(root.rglob("*.json"))
        if p.is_file() and needle.encode() in p.read_bytes()
    ]


def _files(app: str) -> dict[str, str]:
    data = manager.app_dir(app) / "data"
    return {
        str(p.relative_to(data)): p.read_text(encoding="utf-8")
        for p in sorted(data.rglob("*.json"))
    }


# ── two parts ────────────────────────────────────────────────────────────────────────────────


def test_two_capabilities_read_the_shared_token_and_each_its_own_option(home) -> None:
    _install(_manifest())
    ProviderSettings.update_account(APP, {"token": TOKEN, "server": "imap.example.invalid"})
    ProviderSettings.update_capability(APP, "inbox", {"folder": "INBOX"})
    ProviderSettings.update_capability(APP, "search", {"max_results": 20})

    shared = {"token": TOKEN, "server": "imap.example.invalid"}
    assert ProviderSettings.load(APP, "inbox") == shared | {"folder": "INBOX"}
    assert ProviderSettings.load(APP, "search") == shared | {"max_results": 20}
    assert ProviderSettings.load_account(APP) == shared
    assert ProviderSettings.load(APP) == shared | {
        "folder": "INBOX",
        "max_results": 20,
    }, "with no capability, the one dict every app read before"
    assert load_stored(APP).keys() == {"token", "server"}, "the account's file holds the account"
    assert load_stored_capability(APP, "inbox") == {"folder": "INBOX"}


def test_an_app_with_no_account_settings_keeps_one_file(home) -> None:
    _install({"name": PLAIN, "version": "1.0.0", "displayName": "Plain", "description": "x"})
    ProviderSettings.update(PLAIN, {"api_key": PLAIN_KEY, "region": "eu"})
    assert ProviderSettings.load(PLAIN) == {"api_key": PLAIN_KEY, "region": "eu"}
    assert ProviderSettings.load(PLAIN, "search") == ProviderSettings.load(PLAIN)
    assert not (manager.app_dir(PLAIN) / "data" / "capabilities").exists()


def test_a_whole_write_of_an_app_s_settings_lands_each_in_its_part(home) -> None:
    _install(_manifest())
    ProviderSettings.update(APP, {"token": TOKEN, "folder": "Archive", "max_results": 5})
    assert load_stored(APP).keys() == {"token"}
    assert load_stored_capability(APP, "inbox") == {"folder": "Archive"}
    assert load_stored_capability(APP, "search") == {"max_results": 5}


def test_a_whole_save_clears_a_capability_whose_settings_it_leaves_out(home) -> None:
    _install(_manifest())
    ProviderSettings.save(APP, {"token": TOKEN, "max_results": 5, "search_key": SEARCH_KEY})
    capability = secret_refs.capability_owner(APP, "search").prefix
    assert secret_refs.count_owned([capability]) == 1

    ProviderSettings.save(APP, {"token": TOKEN, "folder": "INBOX"})
    assert load_stored_capability(APP, "search") == {}, "a whole write replaces every part"
    assert secret_refs.count_owned([capability]) == 0, "and the left-out secret goes with it"
    assert load_stored_capability(APP, "inbox") == {"folder": "INBOX"}
    assert ProviderSettings.load_account(APP) == {"token": TOKEN}


@pytest.mark.parametrize("capability", ["../x", "a/b", ".", "", "../../other-app/data/config"])
def test_a_string_that_is_not_a_capability_id_reads_and_writes_nothing(home, capability) -> None:
    _install(_manifest())
    _install(_manifest("other-app"))
    ProviderSettings.update_account("other-app", {"server": "kept.example.invalid"})
    before = {app: _files(app) for app in (APP, "other-app")}
    calls = [
        lambda: ProviderSettings.load(APP, capability),
        lambda: ProviderSettings.capability_path(APP, capability),
        lambda: load_stored_capability(APP, capability),
        lambda: ProviderSettings.save_capability(APP, capability, {"server": "x"}),
        lambda: ProviderSettings.update_capability(APP, capability, {"server": "x"}),
        lambda: ProviderSettings.remove_capability(APP, capability),
    ]
    for call in calls:
        with pytest.raises(ValueError, match="is not a capability id"):
            call()
    assert {app: _files(app) for app in (APP, "other-app")} == before


# ── the manifest ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("change", "named"),
    [
        (
            lambda m: m["providers"][1]["settingsSchema"]["properties"].update(
                server={"type": "string"}
            ),
            "providers[1].settingsSchema.properties.server shadows the account setting "
            "settings.properties.server",
        ),
        (
            lambda m: m["providers"][1]["settingsSchema"]["properties"].update(
                folder={"type": "string"}
            ),
            "providers[1].settingsSchema.properties.folder is declared by "
            "providers[0].settingsSchema too",
        ),
        (
            lambda m: m.update(requiresCoreFeatures=[]),
            "must list 'account-settings' in requiresCoreFeatures",
        ),
        (
            lambda m: m.update(setup={"configSchema": ACCOUNT}),
            "settings replaces setup.configSchema",
        ),
        (lambda m: m.update(settings="token"), "settings must be a JSON schema object"),
    ],
    ids=[
        "shadows-an-account-setting",
        "two-capabilities-declare-it",
        "no-core-feature",
        "both-schemas",
        "not-a-schema",
    ],
)
def test_each_wrong_account_settings_declaration_is_refused(change, named) -> None:
    raw = json.loads(json.dumps(_manifest()))
    change(raw)
    errors = AppManifest.from_dict(raw).validate()
    assert any(named in e for e in errors), errors


def test_the_fixture_is_valid(home) -> None:
    assert AppManifest.from_dict(_manifest()).validate() == []


# ── secrets ──────────────────────────────────────────────────────────────────────────────────


def test_a_capability_s_secret_is_its_own_and_goes_with_it(home) -> None:
    _install(_manifest())
    ProviderSettings.update_account(APP, {"token": TOKEN})
    ProviderSettings.update_capability(APP, "search", {"search_key": SEARCH_KEY})
    capability = secret_refs.capability_owner(APP, "search").prefix
    account = secret_refs.account_owner(APP).prefix

    assert _hits(home, SEARCH_KEY) == [] and _hits(home, TOKEN) == []
    assert secret_refs.count_owned([capability]) == 1
    assert ProviderSettings.load(APP, "search")["search_key"] == SEARCH_KEY
    assert ProviderSettings.load(APP, "inbox").get("search_key") is None

    assert ProviderSettings.remove_capability(APP, "search") == 1
    assert secret_refs.count_owned([capability]) == 0
    assert ProviderSettings.load_account(APP) == {"token": TOKEN}, "the account's keys stay"
    assert [k for k in credential_names() if k.startswith(account)] != []
    assert load_stored_capability(APP, "search") == {}


def test_a_reference_to_another_app_s_key_is_refused(home) -> None:
    _install(_manifest())
    other = secret_refs.app_owner("other-app").key("token")
    with pytest.raises(secret_refs.ForeignSecretReference):
        ProviderSettings.update_capability(
            APP, "search", {"search_key": secret_refs.make_ref(other)}
        )


# ── applying a save ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_capability_save_rebuilds_that_one_and_an_account_save_every_one(
    home, monkeypatch
) -> None:
    from personalclaw.dashboard.handlers.apps import register_app_routes as apps_routes

    manifest = _install(_manifest())
    registry = registry_module.get_provider_registry()
    registry.register(manifest)
    for record in registry.list_for_app(APP):
        record.enabled = True
    rebuilt: list[tuple[str, str | None]] = []
    monkeypatch.setattr(registry, "rebuild", lambda name, cap=None: rebuilt.append((name, cap)))

    @web.middleware
    async def owner(request, handler):
        request["user"] = "owner"
        request["app"] = ""
        return await handler(request)

    gw = web.Application(middlewares=[owner])
    apps_routes(gw)
    async with TestClient(TestServer(gw)) as client:
        url = f"/api/apps/{APP}/capabilities/inbox/config"
        read = await (await client.get(url)).json()
        assert read["schema"] == INBOX and read["config"] == {}
        put = await client.put(
            url, json={"folder": "INBOX"}, headers={"If-Match": read["revision"]}
        )
        assert put.status == 200, await put.text()
        assert rebuilt == [(APP, "inbox")], "a capability's save rebuilds that capability alone"

        account = await (await client.get(f"/api/apps/{APP}/config")).json()
        assert account["schema"] == ACCOUNT
        put = await client.put(
            f"/api/apps/{APP}/config",
            json={"token": TOKEN},
            headers={"If-Match": account["revision"]},
        )
        assert put.status == 200, await put.text()
        assert rebuilt[-1] == (APP, None), "an account save rebuilds every capability that is on"

        stale = await client.put(url, json={"folder": "Spam"}, headers={"If-Match": "old"})
        assert stale.status == 409
        unknown = await client.get(f"/api/apps/{APP}/capabilities/calendar/config")
        assert unknown.status == 404, "a capability the app does not declare"
        ghost = await client.get("/api/apps/gone-app/capabilities/inbox/config")
        assert ghost.status == 404, "an app that is not installed"
        _install(
            {
                "name": PLAIN,
                "version": "1.0.0",
                "displayName": "Plain",
                "description": "x",
                "providers": [
                    {"type": "inbox", "implementation": "p:make", "id": "inbox", "label": "Mail"}
                ],
            }
        )
        one_file = await client.get(f"/api/apps/{PLAIN}/capabilities/inbox/config")
        assert one_file.status == 409
        assert (
            f"/api/apps/{PLAIN}/config" in (await one_file.json())["error"]
        ), "an app with no account settings keeps one file, and says where it is"
    assert ProviderSettings.load(APP, "inbox") == {"token": TOKEN, "folder": "INBOX"}


def test_each_capability_is_built_from_its_own_settings(home) -> None:
    manifest = _install(_manifest())
    ProviderSettings.update_account(APP, {"token": TOKEN})
    ProviderSettings.update_capability(APP, "inbox", {"folder": "INBOX"})
    ProviderSettings.update_capability(APP, "search", {"max_results": 7})
    built: dict[str, dict[str, Any]] = {}

    class _Handler:
        def create(self, ext):
            built[ext.capability] = registry_module._settings_of(ext)
            return object()

        def register(self, ext, instance):
            return None

        def deregister(self, ext, instance):
            return None

    registry = registry_module.ProviderRegistry()
    registry.register_type_handler("inbox", _Handler())  # type: ignore[arg-type]
    registry.register_type_handler("search", _Handler())  # type: ignore[arg-type]
    registry.register(manifest, enabled=True)
    assert built == {
        "inbox": {"token": TOKEN, "folder": "INBOX"},
        "search": {"token": TOKEN, "max_results": 7},
    }


@pytest.mark.asyncio
async def test_a_folder_whose_manifest_names_another_app_has_no_capability_settings(home) -> None:
    """The routes name every file by the installed manifest's own name and declared id, never by
    what the request typed, so a folder holding another app's manifest is no app here."""
    from personalclaw.dashboard.handlers.apps import register_app_routes as apps_routes

    _install(_manifest())
    stray = manager.app_dir("stray-folder")
    stray.mkdir(parents=True)
    (stray / "app.json").write_text(json.dumps(_manifest()), encoding="utf-8")

    @web.middleware
    async def owner(request, handler):
        request["user"] = "owner"
        request["app"] = ""
        return await handler(request)

    gw = web.Application(middlewares=[owner])
    apps_routes(gw)
    async with TestClient(TestServer(gw)) as client:
        assert (await client.get(f"/api/apps/{APP}/capabilities/search/config")).status == 200
        stray_read = await client.get("/api/apps/stray-folder/capabilities/search/config")
        assert stray_read.status == 404, await stray_read.text()
        put = await client.put(
            "/api/apps/stray-folder/capabilities/search/config",
            json={"max_results": 1},
            headers={"If-Match": '"x"'},
        )
        assert put.status == 404
    assert not (stray / "data").exists(), "nothing was written under the stray folder"


def test_the_capability_settings_routes_hold_an_app_to_its_own() -> None:
    from personalclaw.apps.permissions import ROUTE_AUTHZ, AppMay, OwnedTarget

    for verb in ("GET", "PUT"):
        row = ROUTE_AUTHZ[f"{verb} /api/apps/{{name}}/capabilities/{{capability}}/config"]
        assert isinstance(row, AppMay) and OwnedTarget("name", app=True) in row.owns


# ── the move ─────────────────────────────────────────────────────────────────────────────────


def _before_the_move(home: Path) -> None:
    """Two installed apps as an earlier core left them: one flat settings file each, secrets in
    the credential store under the app's own owner. One declares account settings now."""
    for raw, values in (
        (
            _manifest(),
            {
                "token": TOKEN,
                "server": "imap.example.invalid",
                "folder": "INBOX",
                "purpose": "File receipts under Receipts.",
                "max_results": 20,
                "search_key": SEARCH_KEY,
            },
        ),
        (
            {"name": PLAIN, "version": "1.0.0", "displayName": "Plain", "description": "x"},
            {"api_key": PLAIN_KEY, "region": "eu"},
        ),
    ):
        _install(raw)
        stored = secret_refs.store(
            values,
            owner=secret_refs.app_owner(raw["name"]),
            declared={"token", "search_key"},
        )
        path = ProviderSettings.config_path(raw["name"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stored), encoding="utf-8")


def _state() -> dict[str, Any]:
    names = sorted(k for k in credential_names() if k.startswith("PCSECRET_APP_"))
    return {
        "files": {app: _files(app) for app in (APP, PLAIN)},
        "credentials": {k: get_credential(k) for k in names},
    }


def test_the_move_carries_each_capability_s_settings_out_once(home) -> None:
    _before_the_move(home)
    plain_before = _files(PLAIN)

    moved = move_capability_settings()
    assert moved == {APP: ["folder", "max_results", "purpose", "search_key"]}
    assert load_stored(APP).keys() == {"token", "server"}
    assert load_stored_capability(APP, "inbox").keys() == {"folder", "purpose"}
    search = load_stored_capability(APP, "search")
    assert search["max_results"] == 20
    assert secret_refs.ref_key(search["search_key"]).startswith(
        secret_refs.capability_owner(APP, "search").prefix
    ), "the moved secret is re-stored under its capability's owner"
    assert ProviderSettings.load(APP, "search")["search_key"] == SEARCH_KEY
    assert ProviderSettings.load(APP, "inbox")["token"] == TOKEN
    assert _files(PLAIN) == plain_before, "an app with no account settings is left as it was"

    for secret in (TOKEN, SEARCH_KEY, PLAIN_KEY):
        assert _hits(home, secret) == [], f"a secret value reached a settings file: {secret}"
    held = list(_state()["credentials"].values())
    assert held.count(SEARCH_KEY) == 1, "the moved secret is held once, not twice"

    after = _state()
    assert move_capability_settings() == {}, "a second run finds nothing to move"
    assert _state() == after, "and writes nothing"


def _reset() -> None:
    from personalclaw.config.credentials import delete_credential

    for name in list(credential_names()):
        if name.startswith("PCSECRET_APP_"):
            delete_credential(name)
    for app in (APP, PLAIN):
        for path in sorted((manager.app_dir(app) / "data").rglob("*.json"), reverse=True):
            path.unlink()


@pytest.mark.parametrize(
    "stop_at",
    ["_save", "_save_capability"],
    ids=["before-the-shared-file", "at-a-capability-s-file"],
)
def test_a_move_stopped_half_way_is_finished_by_the_next_run(home, monkeypatch, stop_at) -> None:
    _before_the_move(home)
    move_capability_settings()
    whole = _state()
    _reset()
    _before_the_move(home)

    real = getattr(ProviderSettings, stop_at)

    def stop(*args):
        raise OSError("the gateway stopped here")

    # Stopped either after every capability's file is written (each secret stored, then its
    # reference) and before the shared file is rewritten, or at the first capability's file.
    # Either way the shared file still holds the settings and the account's copies of their
    # secrets, so nothing is lost.
    monkeypatch.setattr(ProviderSettings, stop_at, staticmethod(stop))
    assert move_capability_settings() == {}, "a stopped app reports nothing moved"
    assert load_stored(APP).keys() >= {"folder", "search_key"}, "the shared file is untouched"
    assert ProviderSettings.load(APP, "search")["search_key"] == SEARCH_KEY

    monkeypatch.setattr(ProviderSettings, stop_at, staticmethod(real))
    assert move_capability_settings() == {APP: ["folder", "max_results", "purpose", "search_key"]}
    assert _state() == whole, "the next run ends where an unstopped run does"
    held = list(_state()["credentials"].values())
    assert held.count(SEARCH_KEY) == 1 and held.count(TOKEN) == 1, "no secret is held twice"
    for secret in (TOKEN, SEARCH_KEY):
        assert _hits(home, secret) == [], "and none is a value in a settings file"


def test_a_value_the_capability_already_holds_is_kept(home) -> None:
    _before_the_move(home)
    ProviderSettings.update_capability(APP, "inbox", {"folder": "Archive"})
    move_capability_settings()
    assert load_stored_capability(APP, "inbox")["folder"] == "Archive"
    assert "folder" not in load_stored(APP), "the shared file's stale copy goes"


def test_the_owner_s_instruction_is_still_hers_after_the_move(home) -> None:
    from personalclaw.apps import instruction_settings

    raw = _manifest()
    raw["providers"][0]["settingsSchema"] = {
        "type": "object",
        "properties": {
            "folder": {"type": "string"},
            "purpose": {"type": "string", "x-meta": {"instruction": True}},
        },
    }
    _install(raw)
    ProviderSettings.update_account(
        APP, {"token": TOKEN, "purpose": "File receipts under Receipts."}
    )
    assert instruction_settings.holds(APP, "File receipts under Receipts.")
    assert move_capability_settings() == {APP: ["purpose"]}
    assert "purpose" in load_stored_capability(APP, "inbox")
    assert instruction_settings.holds(
        APP, "File receipts under Receipts."
    ), "the instruction is read from its capability's file after the move"


# ── a home that goes back to v0.3.0 ──────────────────────────────────────────────────────────

#: v0.3.0's core features, as released (``apps/core_features.py`` at v0.3.0).
_V030_FEATURES = frozenset(
    {
        "adaptive-thinking",
        "approval-answers",
        "chat-trust",
        "closing-streams",
        "cut-off-answers",
        "digest-replies",
        "guarded-clients",
        "guarded-download",
        "links-name-their-channel",
        "message-instructions",
        "messages-run-once",
        "model-efforts",
        "paired-owner",
        "pre-tool-hooks",
        "running-list",
        "tool-call-screen",
        "turns-name-their-channel",
        "turns-name-who-asked",
        "waiting-list",
    }
)
_REF = re.compile(r"^\{\{secret:([A-Za-z0-9_]+)\}\}$")


def _v030_prefixes(app: str) -> tuple[str, str]:
    """v0.3.0's ``secret_refs.app_owned_prefixes(app)``, as released: the app's settings and its
    instances, each ``<segment>_<digest>__``."""
    seg = re.sub(r"[^A-Za-z0-9]+", "_", app).strip("_").upper()[:32].rstrip("_") or "X"
    part = f"{seg}_{hashlib.sha256(app.encode()).hexdigest()[:8].upper()}"
    return f"PCSECRET_APP_{part}__", f"PCSECRET_INSTANCE_{part}__"


def _v030_resolve(app: str, values: dict[str, Any]) -> dict[str, Any]:
    """v0.3.0's resolution of a settings record the app owns: a reference resolves when its key
    is under one of the app's prefixes, to the value the credential store holds for it."""
    out = dict(values)
    for name, value in values.items():
        found = _REF.match(value) if isinstance(value, str) else None
        if found is None:
            continue
        key = found.group(1)
        assert key.startswith(_v030_prefixes(app)), f"v0.3.0 refuses {app}'s {name}: {key}"
        out[name] = get_credential(key)
    return out


def _v030_load(app: str) -> dict[str, Any]:
    """v0.3.0's ``ProviderSettings.load(app)``, as released: the one flat ``data/config.json``,
    resolved for the app."""
    path = manager.app_dir(app) / "data" / "config.json"
    return _v030_resolve(app, json.loads(path.read_text(encoding="utf-8")))


def test_a_home_that_goes_back_to_v030_keeps_every_setting_and_secret(home) -> None:
    _before_the_move(home)
    move_capability_settings()

    assert _v030_load(PLAIN) == {"api_key": PLAIN_KEY, "region": "eu"}
    assert _v030_load(APP) == {"token": TOKEN, "server": "imap.example.invalid"}
    for app in (APP, PLAIN):
        for rel, text in _files(app).items():
            for name, value in _v030_resolve(app, json.loads(text)).items():
                assert not (isinstance(value, str) and _REF.match(value)), (app, rel, name)
                assert value != "", f"v0.3.0 resolves {app}'s {rel} {name} to nothing"


def test_v030_refuses_an_app_that_declares_account_settings(monkeypatch) -> None:
    from personalclaw.apps import core_version

    monkeypatch.setattr(core_version, "core_has", lambda feature: feature in _V030_FEATURES)
    verdict = AppManifest.from_dict(_manifest()).core_compatibility()
    assert not verdict.admits and "account-settings" in verdict.missing
