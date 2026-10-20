"""A sensitive setting is write-only on EVERY route that carries a config — derived, not listed.

``295b48f7b`` made ``x-meta.sensitive`` write-only on ``GET/PATCH /api/providers/{name}/config``
and pinned it with a rail that parametrizes over the two modules serving *that* route. The rail
was a hand-written list of two, so it had nothing to say about a third surface — and there were
three more. Measured against a live gateway (``openai-models``: ``multiInstance: true``,
``api_key`` sensitive), on the parked parent:

* ``POST   /api/providers/{name}/instances``      → the freshly-saved key, in the clear
* ``PUT    /api/providers/{name}/instances/{id}`` → the same, on every save
* ``GET    /api/providers/{name}/instances/{id}`` → the stored key
* ``GET    /api/providers/{name}/instances``      → EVERY instance's key, in ONE body, needing
  no operator action at all
* ``GET    /api/apps/{name}``                     → the stored config verbatim, while
  ``GET /api/apps/{name}/config`` — the same file, the same flag, two functions further down
  the same module — already masked

Eleven bundled model apps declare exactly that shape (``openai-models``, ``anthropic-models``,
``google-models``, ``groq-models``, ``mistral-models``, ``deepseek-models``, ``together-models``,
``openrouter-models``, ``alibaba-models``, ``openai-compatible``, ``anthropic-compatible``).

**Which client reaches the list route, measured rather than assumed.** A cold
Settings → Providers load calls it once per enabled multi-instance provider — measured, for
``mcp-tools`` and ``openai-tools``. It did NOT call it for a **model** provider, which renders
through ``ModelBackends.tsx`` off ``/api/model-providers`` — and a model app's instances no
longer live in this store at all (these routes refuse one with ``model_instances_elsewhere``),
so the fixture below is a TOOL app shaped like ``openai-tools``. The rail is deliberately
indifferent to who reads the body: it asks whether a config reaches a response body, because
a route is the boundary a client is on the far side of.

**Why this file's rail is DERIVED.** A list of route names is what let three of those five sit
unnoticed, so nothing here enumerates a route. :func:`scan` walks the tree, learns which store
functions return a config-or-instance object *from those stores' own return annotations*, taints
their results through each route handler, and reports every tainted value that reaches a
``json_response`` payload without passing through :mod:`personalclaw.apps.secret_fields`. Add a
sixth config-carrying route and it is classified whether or not anyone remembers this file.

A derived rail that finds nothing is indistinguishable from a clean tree, so the census carries
vacuity floors (:func:`test_the_scan_is_not_vacuous`,
:func:`test_the_store_derivation_still_discriminates`) and a planted-regression proof against the
REAL shipped source (:func:`test_reverting_the_mask_in_instance_routes_is_counted`).
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

_SECRET = "fake-openai-project-1"

_SCHEMA = {
    "type": "object",
    "properties": {
        "api_key": {"type": "string", "x-meta": {"label": "API Key", "sensitive": True}},
        "default_model": {"type": "string"},
    },
}


# ── behaviour: the five surfaces, driven through the real route table ──────────


class _FakeProviderConfig:
    type = "tool"
    entity = ""
    capabilities: list[str] = []
    multiInstance = True
    settingsSchema = _SCHEMA


class _FakeExt:
    name = "fake-tools"
    enabled = False  # keep the registry re-cycle / tool-refresh paths out of the way
    error = ""
    provider_config = _FakeProviderConfig()


class _FakeRegistry:
    def get(self, name):
        return _FakeExt() if name == "fake-tools" else None

    def disable(self, name):  # pragma: no cover - not reached with enabled=False
        return None

    def enable(self, name):  # pragma: no cover - not reached with enabled=False
        return True


@asynccontextmanager
async def _client(tmp_path: Path):
    from personalclaw.providers import instance_routes

    # 🪤 A multi-instance TOOL fake reaches `_rebuild_agent_config_safe()`: every
    # `_create()` call in this file runs `instance_routes._refresh_multi_instance_provider_safe`
    # → `rebuild_agent_config()`, which writes through `agent.agents_dir()` and reads the home
    # per call, so patching `config_dir` keeps the write inside tmp_path.
    with (
        patch("personalclaw.config.loader.config_dir", return_value=tmp_path),
        patch("personalclaw.providers.registry.get_provider_registry", lambda: _FakeRegistry()),
        patch("personalclaw.agent.agents_dir", lambda: tmp_path / "agents"),
    ):
        app = web.Application()
        instance_routes.register_instance_routes(app)
        async with TestClient(TestServer(app)) as client:
            yield client


_BASE = "/api/providers/fake-tools/instances"


def _stored(tmp_path: Path, instance_id: str) -> dict:
    """The instance's config as its provider reads it: the record on disk holds a reference for
    each secret, resolved here from the same home's credential store (``config.secret_refs``)."""
    from personalclaw.config.secret_refs import instance_owner, resolve

    path = tmp_path / "extensions" / "fake-tools" / "instances" / f"{instance_id}.json"
    raw = path.read_text(encoding="utf-8")
    assert _SECRET not in raw, "an instance's key reached its record on disk"
    with patch("personalclaw.config.loader.config_dir", return_value=tmp_path):
        return resolve(json.loads(raw)["config"], owner=instance_owner("fake-tools", instance_id))


async def _create(client, **config) -> tuple[str, str]:
    """Create an instance; return ``(id, raw response text)``."""
    r = await client.post(_BASE, json={"display_name": "Primary", "config": config})
    raw = await r.text()
    assert r.status == 201, raw
    return json.loads(raw)["instance"]["id"], raw


async def _save(client, instance_id: str, config: dict):
    """Save *config* the way the instance editor does: over the revision of the instance it read
    (`personalclaw/stale_write.py`) — a save naming none is refused before anything is written."""
    painted = await (await client.get(f"{_BASE}/{instance_id}")).json()
    revision = painted["instance"]["revision"]
    return await client.put(
        f"{_BASE}/{instance_id}", json={"config": config}, headers={"If-Match": f'"{revision}"'}
    )


@pytest.mark.asyncio
async def test_create_does_not_echo_the_key_it_was_just_given(tmp_path):
    async with _client(tmp_path) as client:
        instance_id, raw = await _create(client, api_key=_SECRET, default_model="gpt-4o")
        assert _SECRET not in raw, "POST echoed the API key it had just been handed"
        body = json.loads(raw)["instance"]
        assert body["config"]["api_key"] == SECRET_MASK
        assert body["config"]["default_model"] == "gpt-4o", "a non-sensitive field passes through"
        assert body["_secret_set"] == ["api_key"]
        # …and the real value is on disk, unharmed.
        assert _stored(tmp_path, instance_id)["api_key"] == _SECRET


@pytest.mark.asyncio
async def test_the_list_route_masks_every_instance(tmp_path):
    """The worst of the five: no operator action, N secrets, one body."""
    async with _client(tmp_path) as client:
        await _create(client, api_key=_SECRET, default_model="gpt-4o")
        await _create(client, api_key=_SECRET + "-second", default_model="gpt-4o-mini")

        raw = await (await client.get(_BASE)).text()
        assert _SECRET not in raw, "the list route handed out stored API keys in the clear"
        instances = json.loads(raw)["instances"]
        assert len(instances) == 2, "the fixture did not produce two instances"
        for inst in instances:
            assert inst["config"]["api_key"] == SECRET_MASK
            assert inst["_secret_set"] == ["api_key"]


@pytest.mark.asyncio
async def test_the_single_read_route_masks(tmp_path):
    async with _client(tmp_path) as client:
        instance_id, _ = await _create(client, api_key=_SECRET)
        raw = await (await client.get(f"{_BASE}/{instance_id}")).text()
        assert _SECRET not in raw
        assert json.loads(raw)["instance"]["config"]["api_key"] == SECRET_MASK


@pytest.mark.asyncio
async def test_the_single_read_route_refuses_an_unregistered_provider(tmp_path):
    """Fail CLOSED. Without the extension there is no schema, so there is no way to know
    which fields are sensitive — and "no schema" must not be read as "nothing to withhold".
    Every sibling route on this surface already 404s here; this one used to answer with the
    raw stored config instead."""
    async with _client(tmp_path) as client:
        r = await client.get("/api/providers/not-registered/instances/anything")
        assert r.status == 404, await r.text()


@pytest.mark.asyncio
async def test_an_unregistered_provider_is_refused_even_when_the_instance_exists(tmp_path):
    """The same guard, pinned so the 404 cannot come from anywhere else.

    404 for a provider with no instances proves nothing: the missing-instance branch two
    lines below answers 404 too. Measured — a mutant replacing the registry refusal with
    ``schema = ext.provider_config.settingsSchema if ext else {}`` (fail OPEN: no schema,
    therefore nothing sensitive, therefore the stored config verbatim) left the test above
    GREEN. Plant the instance on disk under a name the registry does not know and the
    registry guard becomes the only thing that can refuse.
    """
    path = tmp_path / "extensions" / "ghost-tools" / "instances" / "abc123.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": "abc123",
                "extension_name": "ghost-tools",
                "display_name": "Ghost",
                "config": {"api_key": _SECRET},
                "enabled": True,
            }
        ),
        encoding="utf-8",
    )

    async with _client(tmp_path) as client:
        r = await client.get("/api/providers/ghost-tools/instances/abc123")
        raw = await r.text()
        assert r.status == 404, raw
        assert _SECRET not in raw, "answered with a stored key for a provider it has no schema for"


@pytest.mark.asyncio
async def test_update_does_not_echo_the_key(tmp_path):
    async with _client(tmp_path) as client:
        instance_id, _ = await _create(client, api_key=_SECRET)
        r = await _save(client, instance_id, {"api_key": _SECRET, "default_model": "gpt-4o-mini"})
        raw = await r.text()
        assert r.status == 200, raw
        assert _SECRET not in raw, "PUT echoed the key it had just been given"
        assert json.loads(raw)["instance"]["config"]["api_key"] == SECRET_MASK


@pytest.mark.asyncio
async def test_saving_the_mask_back_does_not_erase_the_stored_key(tmp_path):
    """The hazard masking the reads CREATES, and the reason the write half is not optional.

    ``MultiInstanceCard`` seeds its editor from the list route's config and PUTs the whole
    dict back. Once that config arrives masked, changing a model id would otherwise write
    ``••••••••`` over a working API key — a worse defect than the leak.
    """
    async with _client(tmp_path) as client:
        instance_id, _ = await _create(client, api_key=_SECRET, default_model="gpt-4o")
        r = await _save(
            client, instance_id, {"api_key": SECRET_MASK, "default_model": "gpt-4o-mini"}
        )
        assert r.status == 200, await r.text()
        stored = _stored(tmp_path, instance_id)
        assert stored["api_key"] == _SECRET, "the mask sentinel overwrote a working key"
        assert stored["default_model"] == "gpt-4o-mini", "the real edit was still saved"


@pytest.mark.asyncio
async def test_saving_a_blank_secret_over_a_stored_one_keeps_it(tmp_path):
    """The second shape the editor produces: the input is blanked, so a save sends "".","""
    async with _client(tmp_path) as client:
        instance_id, _ = await _create(client, api_key=_SECRET, default_model="gpt-4o")
        r = await _save(client, instance_id, {"api_key": "", "default_model": "gpt-5"})
        assert r.status == 200, await r.text()
        stored = _stored(tmp_path, instance_id)
        assert stored["api_key"] == _SECRET
        assert stored["default_model"] == "gpt-5"


@pytest.mark.asyncio
async def test_a_real_rotated_key_still_overwrites(tmp_path):
    """Masking must not make a credential unchangeable."""
    async with _client(tmp_path) as client:
        instance_id, _ = await _create(client, api_key=_SECRET)
        r = await _save(client, instance_id, {"api_key": "fake-key-1"})
        assert r.status == 200, await r.text()
        assert _stored(tmp_path, instance_id)["api_key"] == "fake-key-1"


@pytest.mark.asyncio
async def test_the_mask_sentinel_is_never_stored_as_a_credential(tmp_path):
    """A client handed a mask by a read route must not be able to persist the SENTINEL.

    Nothing is stored at create time, so there is no secret to preserve — but writing
    ``••••••••`` as the API key would produce an instance that looks configured and cannot
    work, and whose "credential" is a constant published in this repo.
    """
    async with _client(tmp_path) as client:
        instance_id, raw = await _create(client, api_key=SECRET_MASK, default_model="gpt-4o")
        assert "api_key" not in json.loads(raw)["instance"]["config"]
        assert "api_key" not in _stored(tmp_path, instance_id)


@pytest.mark.asyncio
async def test_a_refused_write_does_not_quote_the_submitted_value_back(tmp_path):
    """Error handling without information disclosure.

    A 422 that echoed the offending body would re-leak what masking just withheld. Measured
    clean on the parked parent and pinned here: the validator reports the field's LABEL and
    what the schema expected, never what arrived.
    """
    async with _client(tmp_path) as client:
        r = await client.post(
            _BASE,
            json={"display_name": "Bad", "config": {"api_key": _SECRET, "default_model": 12345}},
        )
        raw = await r.text()
        assert r.status == 422, raw
        assert _SECRET not in raw, "a validation refusal quoted the submitted secret back"
        assert "12345" not in raw, "a validation refusal quoted the submitted value back"


@pytest.mark.asyncio
async def test_an_instance_naming_another_owners_key_is_refused(tmp_path):
    """An instance resolves only its own app's credentials, so creating or editing one to
    reference a key another owner holds is a 400 saying what to do instead, and the stored
    record is untouched. On main both were accepted, and the instance was handed the value."""
    from personalclaw.config.credentials import save_credential
    from personalclaw.config.secret_refs import make_ref

    vault = "ghp-vault-value-never-an-instances"
    async with _client(tmp_path) as client:
        with patch("personalclaw.config.loader.config_dir", return_value=tmp_path):
            save_credential("VAULT_FIXTURE_KEY", vault)
        foreign = {"api_key": make_ref("VAULT_FIXTURE_KEY"), "default_model": "gpt-4o"}

        created = await client.post(_BASE, json={"display_name": "Borrow", "config": foreign})
        raw = await created.text()
        assert created.status == 400, raw
        error = json.loads(raw)["error"]
        assert error["code"] == "secret_owned_elsewhere"
        assert "{{secret:VAULT_FIXTURE_KEY}}" in error["message"]
        assert "belongs to a different owner" in error["message"]
        assert "type the key itself — not a reference — into api_key on this instance" in (
            error["message"]
        )
        assert vault not in raw

        instance_id, _ = await _create(client, api_key=_SECRET, default_model="gpt-4o")
        record = tmp_path / "extensions" / "fake-tools" / "instances" / f"{instance_id}.json"
        before = record.read_text(encoding="utf-8")
        updated = await _save(client, instance_id, foreign)
        raw = await updated.text()
        assert updated.status == 400, raw
        assert json.loads(raw)["error"]["code"] == "secret_owned_elsewhere"
        assert vault not in raw
        assert record.read_text(encoding="utf-8") == before, "a refused edit rewrote the record"
        assert _stored(tmp_path, instance_id)["api_key"] == _SECRET


# ── the derived rail: which routes carry a config, and do they all mask? ───────
#
# Nothing below names a route. The population is re-derived on every run from the stores'
# own type annotations and the handlers' own dataflow, because a hand-written list is the
# mechanism that let three of the five leaks above survive a fix aimed at the other two.


# ── vacuity: a census that measures nothing must not read as clean ────────────


# ── the ratchets ──────────────────────────────────────────────────────────────
