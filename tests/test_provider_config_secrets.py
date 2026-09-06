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


def _stored(tmp_path: Path) -> dict:
    path = tmp_path / "apps" / "fake-channel" / "data" / "config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


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
        # …and the real value is still on disk, unharmed.
        assert _stored(tmp_path)["bot_token"] == _SECRET


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
    ["personalclaw.providers.routes", "personalclaw.dashboard.handlers.apps"],
)
def test_both_config_routes_use_the_shared_policy(module):
    """Derived companion to the rail above: both handlers must reference the shared helpers.

    Parametrized over the two modules that serve a config route, so neither can drop back to
    a local implementation while the sentinel test still passes.
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
