"""A provider key typed in Settings lives in the credential store; config.json holds a reference.

Measured on the real image (uid 10001): the key entered in Settings → Providers → Add instance
landed as ``options.api_key`` in ``/data/config.json`` at mode 0644 — world-readable, captured by
every snapshot and every export. ``api_provider_create`` / ``api_provider_update`` wrote the
request's ``options`` into the document verbatim.

These tests drive the real handlers against the per-test home and read the bytes on disk, because
"the handler returned 200" is not the claim — "no key text reaches the file" is. The "still
authenticates" legs go through the real registry entry and the real catalog probe; only the far
end of the HTTP call is a stand-in, and it answers 200 for exactly one ``Authorization`` header.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import uuid

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.config import loader as config_loader
from personalclaw.dashboard.handlers import providers as H
from personalclaw.llm.branded_specs import BrandedProviderSpec
from personalclaw.llm.registry import get_default_registry
from personalclaw.net.client import FetchResponse
from personalclaw.sdk.provider_helpers import register_branded_app

KEY = "sk-fixture-5f0c1d9e-never-plaintext"
ROTATED = "sk-fixture-rotated-8a7b6c5d"

FIXTURE_TYPE = "fixture-keystore-openai"
FIXTURE_BASE = "https://fixture-keystore.invalid/v1"


@pytest.fixture(autouse=True)
def _home(monkeypatch):
    """The conftest-guarded tmp home, a registered fixture provider type, no OS keychain."""
    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    monkeypatch.setattr(H, "_refresh_media_registries", lambda: None)
    register_branded_app(
        BrandedProviderSpec(type=FIXTURE_TYPE, protocol="openai", default_base_url=FIXTURE_BASE)
    )
    previous = os.umask(0o022)  # the common default — what made 0644 the measured mode
    try:
        yield config_loader.config_dir()
    finally:
        os.umask(previous)


@pytest.fixture
def name():
    """A per-test instance name: the LLM registry is process-global and idempotent by name."""
    n = f"fx-{uuid.uuid4().hex[:8]}"
    yield n
    get_default_registry().unregister_entry(n)


@pytest.fixture
def endpoint_accepting(monkeypatch):
    """Make the catalog probe's far end accept exactly one bearer token."""

    def install(token: str) -> list[str]:
        seen: list[str] = []

        async def fake_fetch(url, *, policy=None, method="GET", headers=None, **_kw):
            auth = (headers or {}).get("Authorization", "")
            seen.append(auth)
            if auth == f"Bearer {token}":
                body = json.dumps({"object": "list", "data": [{"id": "fixture-model"}]})
                return FetchResponse(url=url, status=200, body=body.encode())
            return FetchResponse(url=url, status=401, body=b'{"error":"bad key"}')

        monkeypatch.setattr("personalclaw.sdk.net.fetch", fake_fetch)
        return seen

    return install


async def _coro(v):
    return v


def _req(method: str, path: str, body: dict | None = None, match_info: dict | None = None):
    req = make_mocked_request(method, path, match_info=match_info or {})
    req.json = lambda: _coro(body or {})
    return req


def _run(coro):
    return asyncio.run(coro)


def _create(name: str, options: dict) -> None:
    body = {"name": name, "type": FIXTURE_TYPE, "model": "", "options": options}
    resp = _run(H.api_provider_create(_req("POST", "/api/model-providers", body)))
    assert resp.status == 200, resp.body


def _update(name: str, options: dict) -> None:
    resp = _run(
        H.api_provider_update(
            _req("PUT", f"/api/model-providers/{name}", {"options": options}, {"name": name})
        )
    )
    assert resp.status == 200, resp.body


def _test_connection(name: str) -> dict:
    resp = _run(
        H.api_provider_test(_req("POST", f"/api/model-providers/{name}/test", None, {"name": name}))
    )
    return json.loads(resp.body)


def _config_text() -> str:
    return config_loader.config_path().read_text(encoding="utf-8")


def _stored_options(name: str) -> dict:
    data = json.loads(_config_text())
    return next(p for p in data["providers"] if p["name"] == name).get("options", {})


def _home_hits(home, needle: str) -> list[str]:
    """Every file under the home whose bytes contain ``needle`` — the brief's grep, as a test."""
    hits = []
    for path in sorted(home.rglob("*")):
        if path.is_file() and not path.is_symlink() and needle.encode() in path.read_bytes():
            hits.append(str(path.relative_to(home)))
    return hits


def test_add_instance_leaves_no_key_text_in_config_json(name):
    _create(name, {"api_key": KEY, "endpoint": FIXTURE_BASE})

    assert KEY not in _config_text(), "the typed key reached config.json in plaintext"
    stored = _stored_options(name)
    assert stored["endpoint"] == FIXTURE_BASE, "non-secret options still live in config.json"
    assert stored["api_key"].startswith("{{secret:"), stored


def test_config_json_is_written_0600(name):
    _create(name, {"api_key": KEY})

    mode = stat.S_IMODE(config_loader.config_path().stat().st_mode)
    assert mode == 0o600, f"config.json written at {oct(mode)}; it can hold a secret"


def test_the_only_file_holding_the_key_is_the_credential_store(_home, name):
    _create(name, {"api_key": KEY})

    assert _home_hits(_home, KEY) == [".env"]


def test_the_stored_key_authenticates(name, endpoint_accepting):
    seen = endpoint_accepting(KEY)
    _create(name, {"api_key": KEY, "endpoint": FIXTURE_BASE})

    result = _test_connection(name)
    assert result["ok"] is True, result
    assert seen == [f"Bearer {KEY}"]


def test_a_new_instance_naming_a_vault_key_authenticates_at_once(
    name, endpoint_accepting, monkeypatch
):
    """Add-instance registered its in-memory entry from the REQUEST's options, so a Secrets-panel
    reference typed into the key field reached the provider as literal text: Test connection sent
    ``Bearer {{secret:…}}`` and failed until a restart replayed config.json. The entry is now
    built from the stored record, resolved against the provider's own credentials."""
    from personalclaw.config.credentials import save_credential
    from personalclaw.config.secret_refs import make_ref

    vault_name, vault_value = "FIXTURE_VAULT_NEW_INSTANCE_KEY", "sk-fixture-vault-2b3c4d5e"
    # A named (vault) credential is mirrored into the process environment by design; this makes
    # the teardown take it back out, so no later test inherits it.
    monkeypatch.setenv(vault_name, "")
    save_credential(vault_name, vault_value)
    seen = endpoint_accepting(vault_value)
    _create(name, {"api_key": make_ref(vault_name), "endpoint": FIXTURE_BASE})

    assert get_default_registry().get_entry(name).options["api_key"] == vault_value
    result = _test_connection(name)
    assert result["ok"] is True, result
    assert seen == [f"Bearer {vault_value}"]


def test_a_new_instance_is_the_entry_a_restart_would_register(name):
    """One path from a stored record to an entry: what Add-instance registers is what the boot
    sync registers from the same file — here, a pasted key the store trims."""
    from personalclaw.llm.registry import sync_entries_from_config

    _create(name, {"api_key": f"  {KEY}\n", "endpoint": FIXTURE_BASE})
    created = get_default_registry().get_entry(name)
    get_default_registry().unregister_entry(name)
    sync_entries_from_config()

    assert get_default_registry().get_entry(name) == created
    assert created.options["api_key"] == KEY


def test_rotate_clear_and_omit_all_go_through_the_store(_home, name, endpoint_accepting):
    """#3554's three wire meanings, preserved: a value rotates, ``null`` clears, absence keeps."""
    _create(name, {"api_key": KEY, "endpoint": FIXTURE_BASE})

    _update(name, {"api_key": ROTATED})
    seen = endpoint_accepting(ROTATED)
    assert _test_connection(name)["ok"] is True
    assert seen == [f"Bearer {ROTATED}"]
    assert KEY not in _config_text() and ROTATED not in _config_text()
    assert _home_hits(_home, KEY) == [], "a rotated-away key must not linger anywhere"

    _update(name, {"endpoint": FIXTURE_BASE})  # omitted → unchanged
    assert _home_hits(_home, ROTATED) == [".env"]

    _update(name, {"api_key": None})  # explicit null → cleared, from the store too
    assert "api_key" not in _stored_options(name)
    assert _home_hits(_home, ROTATED) == []


def test_delete_removes_the_stored_key(_home, name):
    _create(name, {"api_key": KEY})

    resp = _run(
        H.api_provider_delete(_req("DELETE", f"/api/model-providers/{name}", None, {"name": name}))
    )
    assert resp.status == 200, resp.body
    assert _home_hits(_home, KEY) == [], "deleting the provider left its key in the store"


def test_the_key_is_not_exported_into_the_process_environment(name):
    """Only a reference resolves it: the gateway's children must not inherit a provider key."""
    _create(name, {"api_key": KEY})

    assert KEY not in os.environ.values()


def test_provider_list_reports_the_stored_secret_by_name_only(name):
    _create(name, {"api_key": KEY})

    resp = _run(H.api_providers_list(_req("GET", "/api/model-providers")))
    assert KEY not in resp.body.decode()
    row = next(p for p in json.loads(resp.body)["providers"] if p["name"] == name)
    assert row["stored_secrets"] == ["api_key"]


def test_a_key_typed_before_this_change_moves_to_the_store_and_still_authenticates(
    _home, name, endpoint_accepting
):
    """The one-time move at gateway boot. A key the user already typed must keep working."""
    doc = {
        "providers": [
            {
                "name": name,
                "type": FIXTURE_TYPE,
                "model": "",
                "options": {"api_key": KEY, "endpoint": FIXTURE_BASE},
            }
        ]
    }
    config_loader.config_path().write_text(json.dumps(doc), encoding="utf-8")
    (_home / "config.json.bak").write_text(json.dumps(doc), encoding="utf-8")

    from personalclaw.config.secret_refs import migrate_plaintext_secrets
    from personalclaw.llm.registry import sync_entries_from_config

    migrate_plaintext_secrets()

    assert _home_hits(_home, KEY) == [".env"], "the move left a plaintext copy behind"
    assert stat.S_IMODE(config_loader.config_path().stat().st_mode) == 0o600

    sync_entries_from_config()
    seen = endpoint_accepting(KEY)
    assert _test_connection(name)["ok"] is True
    assert seen == [f"Bearer {KEY}"]

    before = _config_text()
    migrate_plaintext_secrets()
    assert _config_text() == before, "the move is not idempotent"


def test_a_provider_cannot_be_saved_naming_an_apps_key(name):
    """``config.json`` is core's, and core holds every key no app holds — not an app's. A
    reference to an app's token typed into Settings → Providers was accepted on create and on
    update, and core then resolved it. It is refused with what to do, nothing is written, and a
    Secrets-panel reference still saves: core holds the vault."""
    from personalclaw.apps import manager
    from personalclaw.config.credentials import save_credential
    from personalclaw.config.secret_refs import make_ref, ref_key
    from personalclaw.providers.settings import ProviderSettings
    from personalclaw.sel import sel

    token = "xoxb-app-owned-3c4d5e6f-fixture"
    ProviderSettings.save("fixture-token-holder", {"bot_token": token})
    settings = manager.app_dir("fixture-token-holder") / "data" / "config.json"
    app_ref = json.loads(settings.read_text(encoding="utf-8"))["bot_token"]
    app_key = ref_key(app_ref) or ""
    assert app_key.startswith("PCSECRET_APP_"), app_ref

    body = {"name": name, "type": FIXTURE_TYPE, "model": "", "options": {"api_key": app_ref}}
    created = _run(H.api_provider_create(_req("POST", "/api/model-providers", body)))
    assert created.status == 400, created.body
    refusal = json.loads(created.body)["error"]
    assert app_ref in refusal and "belongs to a different owner" in refusal, refusal
    assert "Type the key itself — not a reference — into api_key instead" in refusal, refusal
    assert token not in created.body.decode()
    assert not config_loader.config_path().exists() or name not in _config_text()

    save_credential("FIXTURE_VAULT_PROVIDER_KEY", KEY)
    _create(name, {"api_key": make_ref("FIXTURE_VAULT_PROVIDER_KEY"), "endpoint": FIXTURE_BASE})
    before = _config_text()
    updated = _run(
        H.api_provider_update(
            _req(
                "PUT",
                f"/api/model-providers/{name}",
                {"options": {"api_key": app_ref}},
                {"name": name},
            )
        )
    )
    assert updated.status == 400, updated.body
    assert "belongs to a different owner" in json.loads(updated.body)["error"]
    assert _config_text() == before, "a refused update was written"
    rows = [e for e in sel().recent(200) if e.get("resources") == f"secret:{app_key}"]
    assert len(rows) == 2, rows
    assert all(r["caller_identity"] == "core" and r["outcome"] == "denied" for r in rows)
    assert token not in json.dumps(sel().recent(500)), "a refused value reached the security log"
