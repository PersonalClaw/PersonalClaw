"""A ``{{secret:…}}`` reference resolves only against its own owner's credentials.

Every settings record belongs to core or to one app, and the credential store keeps each
record's secrets under that owner (``config.secret_refs``). A reference, though, is text in a
settings file — which an app can write — and ``secret_refs.resolve`` read whatever key it named.
So a reference in one app's settings to another app's bot token, to a provider's API key or to a
Secrets-panel credential handed that value to the first app. Measured on ``main`` by each test
below: ``ProviderSettings.load`` returned the other owner's token, a multi-instance record was
handed out holding it, an app's MCP server was spawned with it, and a save that named it was
accepted.

Now a record resolves only keys its own owner holds; a reference to any other is refused where
it is used (the consumer is told which key, and that it belongs to a different owner; the
security log records the app and the key name — never a value), and refused where it is saved.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.apps import manager
from personalclaw.config.credentials import get_credential, save_credential
from personalclaw.config.secret_refs import make_ref, ref_key
from personalclaw.providers.settings import ProviderSettings

APP = "fixture-reader"
OTHER = "fixture-holder"
VAULT_KEY = "FIXTURE_VAULT_TOKEN"

OWN_TOKEN = "xoxb-own-5e4d3c2b-fixture"
OTHER_TOKEN = "xoxb-other-9a8b7c6d-never-reaches-another-app"
VAULT_VALUE = "ghp-vault-1f2e3d4c-never-reaches-an-app"


@pytest.fixture(autouse=True)
def home(monkeypatch):
    """The suite's isolated home, with the OS keychain out of the picture: every value here
    lives in that home's ``.env``."""
    from personalclaw.config import loader

    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    return loader.config_dir()


def _stored_settings(app: str) -> dict:
    """An app's settings file as it is on disk: its secrets are references."""
    return json.loads((manager.app_dir(app) / "data" / "config.json").read_text(encoding="utf-8"))


def _other_apps_key() -> str:
    """Store a token under OTHER, the way its own settings form does; return the key."""
    ProviderSettings.save(OTHER, {"bot_token": OTHER_TOKEN})
    key = ref_key(_stored_settings(OTHER)["bot_token"])
    assert key and key.startswith("PCSECRET_APP_"), key
    return key


def _write_settings(app: str, settings: dict) -> None:
    """Write an app's settings file directly — what the app itself, or a hand edit, can do."""
    path = manager.app_dir(app) / "data" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings), encoding="utf-8")


def _denials(key: str) -> list[dict]:
    from personalclaw.sel import sel

    return [
        e
        for e in sel().recent(200)
        if e.get("resources") == f"secret:{key}" and e.get("outcome") == "denied"
    ]


def _all_audit_text() -> str:
    from personalclaw.sel import sel

    return json.dumps(sel().recent(500))


# ── resolve: the same owner resolves, another owner's key does not ─────────────────────────


def test_a_reference_resolves_a_key_its_own_owner_holds():
    ProviderSettings.save(APP, {"bot_token": OWN_TOKEN, "channel": "general"})

    assert ProviderSettings.load(APP) == {"bot_token": OWN_TOKEN, "channel": "general"}


def test_another_apps_key_does_not_resolve_in_an_apps_settings():
    key = _other_apps_key()
    _write_settings(APP, {"bot_token": make_ref(key), "channel": "general"})

    with pytest.raises(ValueError) as refused:
        ProviderSettings.load(APP)

    message = str(refused.value)
    assert make_ref(key) in message, "the consumer is not told which key was named"
    assert "belongs to a different owner" in message
    assert OTHER_TOKEN not in message
    [row] = _denials(key)
    assert row["caller_identity"] == f"app:{APP}", row
    assert row["operation"] == "secrets.resolve"
    assert OTHER_TOKEN not in _all_audit_text(), "a refused value reached the security log"


def test_a_secrets_panel_credential_does_not_resolve_in_an_apps_settings():
    """The vault is core's. An app that needs a key has it stored under the app."""
    save_credential(VAULT_KEY, VAULT_VALUE)
    _write_settings(APP, {"api_key": make_ref(VAULT_KEY)})

    with pytest.raises(ValueError, match="belongs to a different owner"):
        ProviderSettings.load(APP)
    assert _denials(VAULT_KEY), "the refusal left no security-log row"


def test_core_settings_resolve_the_vault_but_not_an_apps_key():
    """A provider instance in config.json is core's: a Secrets-panel credential typed into it
    by hand still resolves, and an app's token does not — the record is left out."""
    from personalclaw.config.secret_refs import resolve_provider_records

    save_credential(VAULT_KEY, VAULT_VALUE)
    key = _other_apps_key()
    records = [
        {"name": "vaulted", "type": "openai", "options": {"api_key": make_ref(VAULT_KEY)}},
        {"name": "borrowing", "type": "openai", "options": {"api_key": make_ref(key)}},
    ]

    resolved = {r["name"]: r["options"] for r in resolve_provider_records(records)}

    assert resolved == {"vaulted": {"api_key": VAULT_VALUE}}
    [row] = _denials(key)
    assert row["caller_identity"] == "core"


def test_the_owner_is_required_and_never_defaults_to_core():
    """A defaulted owner would read as core, and core holds every key no app holds."""
    from personalclaw.config.secret_refs import SecretOwner, resolve

    with pytest.raises(TypeError):
        resolve({"api_key": make_ref(VAULT_KEY)})
    with pytest.raises(TypeError):
        SecretOwner("PCSECRET_FIXTURE__")


def test_an_instance_is_handed_out_as_stored_and_resolves_only_its_apps_keys(home):
    from personalclaw.providers import instances

    key = _other_apps_key()
    record = home / "extensions" / APP / "instances" / "i1.json"
    record.parent.mkdir(parents=True)
    record.write_text(
        json.dumps({"id": "i1", "extension_name": APP, "config": {"api_key": make_ref(key)}}),
        encoding="utf-8",
    )

    inst = instances.get_instance(APP, "i1")
    assert OTHER_TOKEN not in json.dumps(inst.to_dict()), "an instance was handed out resolved"
    with pytest.raises(ValueError, match="belongs to a different owner"):
        instances.resolved_config(inst)


def test_an_instance_record_cannot_enrol_itself_under_another_apps_keys(home):
    """The owning app is the directory's, never the record's own ``extension_name`` claim."""
    from personalclaw.providers import instances

    key = _other_apps_key()
    record = home / "extensions" / APP / "instances" / "i1.json"
    record.parent.mkdir(parents=True)
    record.write_text(
        json.dumps({"id": "i1", "extension_name": OTHER, "config": {"api_key": make_ref(key)}}),
        encoding="utf-8",
    )

    inst = instances.get_instance(APP, "i1")
    assert OTHER_TOKEN not in json.dumps(inst.to_dict()), "the record was handed out resolved"
    with pytest.raises(ValueError, match="belongs to a different owner"):
        instances.resolved_config(inst)


def test_the_webhook_does_not_authenticate_with_an_apps_token(home):
    """Core settings do not resolve an app's key either: ``hooks.webhook_token`` naming another
    owner's credential reads as "no token configured", and every request is refused."""
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers.hooks import _verify_hook_token

    key = _other_apps_key()
    (home / "config.json").write_text(
        json.dumps({"hooks": {"webhook_token": make_ref(key)}}), encoding="utf-8"
    )
    request = make_mocked_request(
        "POST", "/api/hooks/agent", headers={"Authorization": f"Bearer {OTHER_TOKEN}"}
    )

    assert _verify_hook_token(request) is False
    [row] = _denials(key)
    assert row["caller_identity"] == "core"


@pytest.mark.asyncio
async def test_the_providers_list_masks_a_reference_whatever_its_field_is_called(home, monkeypatch):
    """Settings → Providers lists each instance from the registry, which holds the VALUES, so a
    Secrets-panel credential typed into a field no schema calls secret was listed in the clear.
    The list masks every reference in the stored form."""
    from personalclaw.apps.secret_fields import SECRET_MASK
    from personalclaw.dashboard.handlers import providers as handler
    from personalclaw.llm import registry as llm_registry
    from personalclaw.llm.capabilities import Capability
    from personalclaw.llm.registry import ProviderEntry

    save_credential(VAULT_KEY, VAULT_VALUE)
    stored = {"endpoint": make_ref(VAULT_KEY), "region": "eu"}
    (home / "config.json").write_text(
        json.dumps({"providers": [{"name": "mine", "type": "openai", "options": stored}]}),
        encoding="utf-8",
    )
    entry = ProviderEntry(
        name="mine",
        type="openai",
        model="m",
        options={"endpoint": VAULT_VALUE, "region": "eu"},
        declared_capabilities=frozenset({Capability.CHAT}),
    )

    class _Registry:
        def list_entries(self):
            return [entry]

        def capability_of(self, _type):
            raise LookupError("no capability descriptor")

        def build_catalog(self, _entry):
            return None

    monkeypatch.setattr(llm_registry, "get_default_registry", lambda: _Registry())
    resp = await handler.api_providers_list(object())

    assert VAULT_VALUE not in resp.text, "the providers list handed out a resolved reference"
    [listed] = json.loads(resp.text)["providers"]
    assert listed["options"] == {"endpoint": SECRET_MASK, "region": "eu"}
    assert listed["secret_set"] == ["endpoint"]


# ── store: a save that names another owner's key is refused, before anything is stored ──


def test_a_save_naming_another_owners_key_is_refused_and_stores_nothing(home):
    key = _other_apps_key()
    fresh = "xoxb-fresh-0a1b2c3d-typed-alongside"

    with pytest.raises(ValueError) as refused:
        ProviderSettings.save(APP, {"bot_token": make_ref(key), "app_token": fresh})

    message = str(refused.value)
    assert "belongs to a different owner" in message
    assert "another app's credential" in message, message
    assert "type the key itself — not a reference — into bot_token" in message, message
    assert not (manager.app_dir(APP) / "data" / "config.json").exists(), "the refused save wrote"
    assert fresh not in (home / ".env").read_text(), "a value was stored by a refused save"


def test_an_apps_own_reference_survives_a_save_untouched():
    """The masked form sends a field's reference back unchanged; that is the app's own key."""
    ProviderSettings.save(APP, {"bot_token": OWN_TOKEN})
    stored = _stored_settings(APP)

    ProviderSettings.save(APP, {**stored, "channel": "ops"})

    assert _stored_settings(APP)["bot_token"] == stored["bot_token"]
    assert ProviderSettings.load(APP) == {"bot_token": OWN_TOKEN, "channel": "ops"}


@pytest.mark.asyncio
async def test_saving_the_fix_retries_a_provider_its_settings_had_failed(monkeypatch):
    """The refusal says: store the key under the app. Doing that must make the app work at once.
    Its provider's enable FAILED on the foreign reference, and a save that only rebuilt enabled
    providers left it failed — still showing the old error — until a restart. A provider the
    owner switched off (no error) and a disabled app are left alone."""
    from personalclaw.providers import routes

    class _Ext:
        def __init__(self, *, enabled: bool, error: str):
            self.enabled, self.error = enabled, error

    class _Registry:
        def __init__(self, ext: _Ext):
            self.ext, self.enabled_calls = ext, []

        def get(self, _name):
            return self.ext

        def enable(self, name):
            self.enabled_calls.append(name)
            self.ext.enabled, self.ext.error = True, ""
            return True

    async def saved(ext: _Ext, denial: str = "") -> list[str]:
        registry = _Registry(ext)
        monkeypatch.setattr(routes, "get_provider_registry", lambda: registry)
        monkeypatch.setattr("personalclaw.apps.permissions.app_lifecycle_denial", lambda _n: denial)
        await routes.apply_saved_settings(APP)
        return registry.enabled_calls

    assert await saved(_Ext(enabled=False, error="API Key refers to …")) == [APP]
    assert await saved(_Ext(enabled=False, error="")) == [], "a switched-off provider was enabled"
    assert await saved(_Ext(enabled=False, error="…"), denial="app is disabled") == []


# ── MCP: an app's server resolves only its app's keys ──────────────────────────────────────


def _mcp_json(home, servers: dict) -> None:
    (home / "mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_an_apps_mcp_server_is_not_started_with_another_owners_key(home):
    """``{app}:{server}`` is an app's server (``apps.mcp_bridge``). Its env named a key the app
    does not hold, and the native client spawned it holding that value."""
    from personalclaw.mcp_client import _personalclaw_mcp_specs

    key = _other_apps_key()
    save_credential(VAULT_KEY, VAULT_VALUE)
    _mcp_json(
        home,
        {
            f"{APP}:tools": {"command": "true", "env": {"TOKEN": make_ref(key)}},
            f"{APP}:search": {"command": "true", "env": {"TOKEN": make_ref(VAULT_KEY)}},
            "mine": {"command": "true", "env": {"TOKEN": make_ref(VAULT_KEY)}},
        },
    )

    specs = _personalclaw_mcp_specs()

    assert f"{APP}:tools" not in specs and f"{APP}:search" not in specs
    assert specs["mine"]["env"] == {"TOKEN": VAULT_VALUE}, "a core server lost the vault"
    assert OTHER_TOKEN not in json.dumps(specs) and _denials(key)


def test_an_apps_mcp_server_still_resolves_its_own_stored_value(home):
    from personalclaw.config.secret_refs import store_mcp_spec
    from personalclaw.mcp_client import _personalclaw_mcp_specs

    stored = store_mcp_spec(f"{APP}:tools", {"env": {"TOKEN": OWN_TOKEN}}, strict=True)
    _mcp_json(home, {f"{APP}:tools": {"command": "true", **stored}})

    assert _personalclaw_mcp_specs()[f"{APP}:tools"]["env"] == {"TOKEN": OWN_TOKEN}


def test_the_mcp_probe_reports_the_refusal_as_the_servers_error():
    """The Tools page's status for the server: the whole sentence, and nothing spawned."""
    import asyncio

    from personalclaw.mcp_discovery import McpServerInfo, probe_server

    key = _other_apps_key()
    server = McpServerInfo(
        name=f"{APP}:tools", command="definitely-not-a-command", env={"TOKEN": make_ref(key)}
    )

    probed = asyncio.run(probe_server(server))

    assert probed.status == "error"
    assert make_ref(key) in probed.error and "belongs to a different owner" in probed.error


def test_the_mcp_form_refuses_a_reference_to_another_owners_key(home):
    """STRICT is a value typed now: refused with what to do, and nothing stored."""
    from personalclaw.config.secret_refs import store_mcp_spec

    key = _other_apps_key()
    before = (home / ".env").read_text()

    with pytest.raises(ValueError, match="belongs to a different owner"):
        store_mcp_spec(
            f"{APP}:tools", {"env": {"TOKEN": make_ref(key), "OTHER": "typed"}}, strict=True
        )
    assert (home / ".env").read_text() == before


def _mcp_detail(method: str, name: str, body: dict | None = None):
    import asyncio

    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import mcp as mcp_mod

    req = make_mocked_request(method, f"/api/mcp/servers/{name}", match_info={"name": name})

    async def _json():
        return body

    req.json = _json
    return asyncio.run(mcp_mod.api_mcp_server_detail(req))


def test_the_mcp_edit_form_does_not_turn_another_owners_key_into_plaintext(home, monkeypatch):
    """Keeping a stored variable and marking it plain moves its value into ``mcp.json``. For a
    reference to another owner's key, that wrote the other owner's token in plaintext into core's
    file. It is refused, and the file is left as it was."""
    monkeypatch.setattr("personalclaw.agent._USER_DIR", home)
    (home / "agents").mkdir(parents=True, exist_ok=True)
    (home / "agents" / "personalclaw.json").write_text(
        json.dumps({"mcpServers": {}, "tools": [], "allowedTools": []}), encoding="utf-8"
    )
    key = _other_apps_key()
    _mcp_json(home, {"mine": {"command": "echo", "env": {"TOKEN": make_ref(key)}}})
    before = (home / "mcp.json").read_text(encoding="utf-8")

    resp = _mcp_detail(
        "PUT", "mine", {"command": "echo", "env": {}, "plainEnv": ["TOKEN"], "keepEnv": ["TOKEN"]}
    )

    assert resp.status == 400, resp.text
    assert json.loads(resp.text)["error"]["code"] == "secret_owned_elsewhere"
    assert (home / "mcp.json").read_text(encoding="utf-8") == before
    assert OTHER_TOKEN not in resp.text


def test_config_get_reveal_does_not_reveal_another_owners_key():
    """``config.json`` is core's. ``--reveal`` shows core's own stored values, and leaves a
    reference to an app's key as the reference — else the documented ``--reveal`` → edit →
    ``config set --file`` round trip would copy that app's token into core's settings."""
    from personalclaw.config.secret_refs import reveal_stored_values, store_provider_options

    key = _other_apps_key()
    own = store_provider_options("mine", "openai", {"api_key": "sk-core-own-4c5d6e7f"})
    doc = {
        "providers": [
            {"name": "mine", "type": "openai", "options": own},
            {"name": "borrowing", "type": "openai", "options": {"api_key": make_ref(key)}},
        ]
    }

    revealed, missing = reveal_stored_values(doc)

    assert revealed["providers"][0]["options"]["api_key"] == "sk-core-own-4c5d6e7f"
    assert revealed["providers"][1]["options"]["api_key"] == make_ref(key)
    assert OTHER_TOKEN not in json.dumps(revealed) and missing == []


def test_the_mcp_document_writer_keeps_a_foreign_reference_rather_than_drop_the_server(home):
    """The chokepoint every mcp.json write goes through must never lose a server, or the rest
    of its secrets, over one value: the reference stays, and is refused at start."""
    from personalclaw.config.secret_refs import write_mcp_document

    key = _other_apps_key()
    doc = {"mcpServers": {f"{APP}:tools": {"command": "true", "env": {"TOKEN": make_ref(key)}}}}

    write_mcp_document(home / "mcp.json", doc)

    written = json.loads((home / "mcp.json").read_text())["mcpServers"][f"{APP}:tools"]
    assert written["env"] == {"TOKEN": make_ref(key)}
    assert get_credential(key) == OTHER_TOKEN, "the other owner's key was touched"
