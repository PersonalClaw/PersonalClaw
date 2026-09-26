"""``/api/providers/{name}/config`` must hold the same write-only secret policy as
``/api/apps/{name}/config`` — one file, one ``x-meta.sensitive`` flag, one policy.

It did not. ``tests/test_app_api.py::test_sensitive_config_field_is_write_only`` pinned the
rule on the Apps route (#43) while the Providers route — the one the Settings → Providers
schema form actually calls — returned the stored secret verbatim on GET and echoed it back
on PATCH. For the bundled ``slack-channel`` app that meant its Bot Token and App Token in
every panel-open response, in the form's React state, and revealable on screen through the
field's show/hide toggle.

Found while measuring #952/#953 against a real gateway: the very PATCH used to save Slack
tokens through the dashboard replied with them in cleartext.

The last test is the rail: the policy is asserted to live in exactly ONE module, because
two implementations of one rule are how the routes diverged in the first place.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.apps.secret_fields import SECRET_MASK

_SECRET = "xoxb-NOT-A-REAL-TOKEN-just-a-fixture"

_SCHEMA = {
    "type": "object",
    "properties": {
        "bot_token": {"type": "string", "x-meta": {"label": "Bot Token", "sensitive": True}},
        "app_token": {"type": "string", "x-meta": {"label": "App Token", "sensitive": True}},
        "command": {"type": "string"},
    },
}


class _FakeProviderConfig:
    type = "channel"
    entity = ""
    capabilities: list[str] = []
    multiInstance = False
    settingsSchema = _SCHEMA


class _FakeExt:
    name = "fake-channel"
    enabled = False  # keep PATCH off the registry re-cycle path
    error = ""
    provider_config = _FakeProviderConfig()


class _FakeRegistry:
    def get(self, name):
        return _FakeExt() if name == "fake-channel" else None


@asynccontextmanager
async def _client(tmp_path: Path):
    from personalclaw.apps import manager
    from personalclaw.providers import routes as provider_routes

    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
        patch.object(provider_routes, "get_provider_registry", lambda: _FakeRegistry()),
    ):
        app = web.Application()
        provider_routes.register_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client


def _config_file(tmp_path: Path) -> Path:
    return tmp_path / "apps" / "fake-channel" / "data" / "config.json"


def _stored(tmp_path: Path) -> dict:
    """The settings as the app reads them: the file holds a reference for each secret, and the
    value is resolved from the credential store (``config.secret_refs``)."""
    from personalclaw.config.secret_refs import app_owner, resolve

    path = _config_file(tmp_path)
    if not path.is_file():
        return {}
    return resolve(json.loads(path.read_text(encoding="utf-8")), owner=app_owner("fake-channel"))


@pytest.mark.asyncio
async def test_get_config_masks_sensitive_fields(tmp_path):
    """A configured token never leaves the backend through this route."""
    async with _client(tmp_path) as client:
        r = await client.patch(
            "/api/providers/fake-channel/config",
            json={"bot_token": _SECRET, "app_token": "xapp-1-fixture", "command": "pclaw"},
        )
        assert r.status == 200, await r.text()

        raw = await (await client.get("/api/providers/fake-channel/config")).text()
        assert _SECRET not in raw, "GET handed the stored bot token back in the clear"
        body = json.loads(raw)
        assert body["config"]["bot_token"] == SECRET_MASK
        assert body["config"]["app_token"] == SECRET_MASK
        assert body["config"]["command"] == "pclaw", "a non-sensitive field must pass through"
        assert body["_secret_set"] == ["app_token", "bot_token"]
        # …and the real value is still stored, unharmed — in the credential store, never in
        # the app's own config file.
        assert _stored(tmp_path)["bot_token"] == _SECRET
        assert _SECRET not in _config_file(tmp_path).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_patch_response_does_not_echo_the_saved_secret(tmp_path):
    async with _client(tmp_path) as client:
        raw = await (
            await client.patch("/api/providers/fake-channel/config", json={"bot_token": _SECRET})
        ).text()
        assert _SECRET not in raw, "PATCH echoed the token it had just been given"
        assert json.loads(raw)["config"]["bot_token"] == SECRET_MASK


@pytest.mark.asyncio
async def test_patching_the_mask_back_preserves_the_stored_secret(tmp_path):
    """The other half of masking: the form round-trips whatever GET gave it.

    Without this, masking the GET would delete a working token the first time an operator
    saved an unrelated field on the same form — a worse bug than the one being fixed.
    """
    async with _client(tmp_path) as client:
        await client.patch("/api/providers/fake-channel/config", json={"bot_token": _SECRET})
        r = await client.patch(
            "/api/providers/fake-channel/config",
            json={"bot_token": SECRET_MASK, "command": "renamed"},
        )
        assert r.status == 200, await r.text()
        assert _stored(tmp_path)["bot_token"] == _SECRET
        assert _stored(tmp_path)["command"] == "renamed"


@pytest.mark.asyncio
async def test_an_empty_sensitive_field_over_a_stored_value_preserves_it(tmp_path):
    """The second shape a round-tripped masked form produces (field cleared by the widget)."""
    async with _client(tmp_path) as client:
        await client.patch("/api/providers/fake-channel/config", json={"bot_token": _SECRET})
        await client.patch("/api/providers/fake-channel/config", json={"bot_token": ""})
        assert _stored(tmp_path)["bot_token"] == _SECRET


@pytest.mark.asyncio
async def test_a_real_new_value_still_overwrites(tmp_path):
    """Masking must not make a token unchangeable."""
    async with _client(tmp_path) as client:
        await client.patch("/api/providers/fake-channel/config", json={"bot_token": _SECRET})
        await client.patch(
            "/api/providers/fake-channel/config", json={"bot_token": "xoxb-ROTATED-fixture"}
        )
        assert _stored(tmp_path)["bot_token"] == "xoxb-ROTATED-fixture"


@pytest.mark.asyncio
async def test_a_patch_naming_another_owners_key_is_refused(tmp_path, monkeypatch):
    """This form writes the same file as the app's Configure page, so the same refusal: a
    reference to a credential another owner holds is a 400 that says what to do, and the
    stored settings are untouched. On main it was saved, and the app then resolved it."""
    from personalclaw.config.credentials import save_credential
    from personalclaw.config.secret_refs import make_ref

    monkeypatch.setattr("personalclaw.config.credentials._usable_keyring", lambda: None)
    async with _client(tmp_path) as client:
        await client.patch("/api/providers/fake-channel/config", json={"bot_token": _SECRET})
        before = _config_file(tmp_path).read_text(encoding="utf-8")
        save_credential("VAULT_FIXTURE_KEY", "ghp-vault-value-never-an-apps")

        r = await client.patch(
            "/api/providers/fake-channel/config",
            json={"bot_token": make_ref("VAULT_FIXTURE_KEY"), "command": "renamed"},
        )

        text = await r.text()
        assert r.status == 400, text
        message = json.loads(text)["error"]
        assert "{{secret:VAULT_FIXTURE_KEY}}" in message
        assert "belongs to a different owner" in message
        assert "a credential in Settings → Secrets" in message
        assert "type the key itself — not a reference — into" in message
        assert "ghp-vault-value" not in text
        assert _config_file(tmp_path).read_text(encoding="utf-8") == before
        assert _stored(tmp_path)["bot_token"] == _SECRET


def test_the_masking_policy_has_exactly_one_implementation():
    """The rail. Every config route must resolve the mask sentinel to the same module.

    The divergence existed because the Apps handler owned a private ``_SECRET_MASK`` +
    ``_mask_secret_config`` while the Providers handler owned nothing. Keyed on the sentinel
    VALUE rather than a symbol name, because a second copy is what forks the policy — and a
    copy is exactly as likely to be spelled inline as named. (``doctor.py`` has an unrelated
    ``_SECRET_MASK`` naming an unresolved-secret placeholder for workflow previews; it is a
    different concept with a different value, which is why the value is the test.)
    """
    src = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
    carriers = sorted(
        str(p.relative_to(src))
        for p in src.rglob("*.py")
        if SECRET_MASK in p.read_text(encoding="utf-8")
    )
    assert carriers == ["apps/secret_fields.py"], (
        f"the sensitive-field mask sentinel appears in more than one module: {carriers}. "
        "One rule, one owner — a second copy is how /api/apps and /api/providers came to "
        "disagree about whether a token is write-only."
    )


@pytest.mark.parametrize(
    "module",
    [
        "personalclaw.providers.routes",
        "personalclaw.dashboard.handlers.apps",
        "personalclaw.cli_config",
    ],
)
def test_both_config_routes_use_the_shared_policy(module):
    """Derived companion to the rail above: every config read path must use the shared helpers.

    Parametrized over the modules that serve a config read, so none can drop back to a local
    implementation while the sentinel test still passes.

    ``cli_config`` is the THIRD, added for #3125. The two HTTP cases above were the whole scope
    for a while, and that is exactly how the CLI came to print provider API keys and the legacy
    Slack tokens in the clear: this rail could not see a read path that serves no route. It is
    the same rule — one file, one ``x-meta.sensitive`` flag, one policy — and now the same rail.
    """
    import importlib
    import inspect as _inspect

    src = _inspect.getsource(importlib.import_module(module))
    assert "secret_fields" in src, f"{module} does not use apps.secret_fields"
    assert "mask_secrets" in src, f"{module} does not mask sensitive fields on read"
    assert "preserve_unchanged_secrets" in src, (
        f"{module} masks on read without preserving on write — the first save of an "
        "unrelated field would erase a stored credential."
    )


# ── the schema-LESS half of the same policy (#3125) ──────────────────────────────────────────
#
# `config.json`'s credential-bearing blocks are the ones core does not model, so no
# `settingsSchema` describes them: `providers` is a raw list of instance records and the legacy
# `slack` block is not a provider extension at all. Sensitivity is derived from the field name
# there, and these are that derivation's floors.


@pytest.mark.parametrize(
    "name",
    [
        "api_key",
        "apiKey",
        "bot_token",
        "app_token",
        "client_secret",
        "password",
        "passphrase",
        "credentials",
        "access_key_id",
        "private_key",
    ],
)
def test_the_derived_policy_recognises_a_credential_name(name):
    from personalclaw.apps.secret_fields import is_credential_field_name

    assert is_credential_field_name(name), f"{name} holds a credential and would be printed"


@pytest.mark.parametrize(
    "name",
    [
        # 🔴 DISCRIMINATION FLOOR. A rule that said yes to these would mask the config's own
        # budget ceilings and cache identifiers — masking an integer while protecting nothing,
        # and corrupting a round-tripped document on the way.
        "max_tokens_per_day",
        "context_budget_tokens",
        "session_brief_max_tokens",
        "semantic_keys",
        "cache_key",
        "sort_key",
        "key_prefix",
        "log_level",
        "keywords",
    ],
)
def test_the_derived_policy_does_not_claim_an_ordinary_field(name):
    from personalclaw.apps.secret_fields import is_credential_field_name

    assert not is_credential_field_name(name)


def test_the_derived_policy_leaves_the_modelled_config_alone():
    """VACUITY'S OPPOSITE — over-reach. Measured against the real `AppConfig`, not a fixture.

    The mask runs over the WHOLE merged document rather than an enumerated block list, so it has
    to be shown not to touch the ~340 modelled fields. If a future config field is both
    credential-named and a non-empty string by default, this fails and the choice becomes
    explicit rather than a surprise in someone's `config get`.
    """
    from personalclaw.apps.secret_fields import mask_secrets_in_document
    from personalclaw.config.loader import AppConfig

    _, masked = mask_secrets_in_document(AppConfig().to_dict())
    assert masked == []


def test_a_credential_named_container_masks_every_string_inside_it():
    """`credentials: {"github": "…"}` is a real shape and no inner name is a tell.

    Over-masking is the safe direction on a read path: `--reveal` exists, and the write side
    restores by PATH, so an over-masked field still round-trips losslessly.
    """
    from personalclaw.apps.secret_fields import mask_secrets_in_document

    masked, paths = mask_secrets_in_document({"credentials": {"github": "ghp-fixture"}})
    assert masked["credentials"]["github"] == SECRET_MASK
    assert paths == ["credentials.github"]


def test_the_document_walk_masks_nothing_it_was_not_asked_to():
    from personalclaw.apps.secret_fields import mask_secrets_in_document

    doc = {"agent": {"log_level": "INFO", "max_tokens_per_day": 100000}, "use_cases": {"chat": "x"}}
    masked, paths = mask_secrets_in_document(doc)
    assert paths == []
    assert masked == doc


def test_masking_does_not_mutate_the_document_it_was_given():
    """The CLI prints the masked copy and writes from the original; sharing one dict would make
    the display path a write path."""
    from personalclaw.apps.secret_fields import mask_secrets_in_document

    doc = {"slack": {"bot_token": _SECRET}}
    mask_secrets_in_document(doc)
    assert doc["slack"]["bot_token"] == _SECRET


def test_a_masked_document_restores_byte_for_byte():
    from personalclaw.apps.secret_fields import (
        mask_secrets_in_document,
        preserve_unchanged_secrets_in_document,
    )

    stored = {
        "providers": [
            {"name": "a", "options": {"api_key": "sk-a-fixture"}},
            {"name": "b", "api_key": "sk-b-fixture"},
        ],
        "slack": {"bot_token": _SECRET, "command": "pclaw"},
    }
    masked, _ = mask_secrets_in_document(stored)
    restored, unresolved = preserve_unchanged_secrets_in_document(masked, stored)
    assert unresolved == []
    assert restored == stored


def test_a_reordered_provider_list_restores_by_NAME_not_by_index():
    """🔴 The failure this ordering rule prevents: writing one instance's key onto another.

    An index-paired restore over a reordered list is silent and wrong, which is worse than the
    refusal an unpairable element gets.
    """
    from personalclaw.apps.secret_fields import preserve_unchanged_secrets_in_document

    stored = {
        "providers": [
            {"name": "a", "api_key": "sk-a-fixture"},
            {"name": "b", "api_key": "sk-b-fixture"},
        ]
    }
    incoming = {
        "providers": [
            {"name": "b", "api_key": SECRET_MASK},
            {"name": "a", "api_key": SECRET_MASK},
        ]
    }
    restored, unresolved = preserve_unchanged_secrets_in_document(incoming, stored)
    assert unresolved == []
    assert restored["providers"][0]["api_key"] == "sk-b-fixture"
    assert restored["providers"][1]["api_key"] == "sk-a-fixture"


def test_an_unpairable_masked_element_is_reported_rather_than_guessed():
    from personalclaw.apps.secret_fields import preserve_unchanged_secrets_in_document

    stored = {"providers": [{"name": "a", "api_key": "sk-a-fixture"}]}
    incoming = {"providers": [{"name": "renamed", "api_key": SECRET_MASK}]}
    _, unresolved = preserve_unchanged_secrets_in_document(incoming, stored)
    assert unresolved == ["providers[0].api_key"]


def test_a_real_new_credential_in_a_document_still_overwrites():
    """Masking must not make a credential unchangeable through `config set --file` either."""
    from personalclaw.apps.secret_fields import preserve_unchanged_secrets_in_document

    stored = {"slack": {"bot_token": _SECRET}}
    incoming = {"slack": {"bot_token": "xoxb-ROTATED-fixture"}}
    restored, unresolved = preserve_unchanged_secrets_in_document(incoming, stored)
    assert unresolved == []
    assert restored["slack"]["bot_token"] == "xoxb-ROTATED-fixture"


def test_mask_bearing_paths_finds_a_mask_anywhere_and_nothing_otherwise():
    """Floor for the fail-closed check: it must both fire and be quiet."""
    from personalclaw.apps.secret_fields import mask_bearing_paths

    assert mask_bearing_paths({"a": {"b": [{"c": SECRET_MASK}]}}) == ["a.b[0].c"]
    assert mask_bearing_paths({"a": {"b": "plain"}, "n": 1, "t": True}) == []
