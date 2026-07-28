"""PUT /api/models/active/{use_case} must never succeed-silently (#48).

Two ways the endpoint answered ``ok: true`` while the caller's binding had NOT taken:

1. A body that never mentioned ``models`` — e.g. a caller guessing
   ``{"providers": [...]}`` — was read as ``models: []``, which UNSET the use-case's
   binding and still returned ok. The caller saw success while its binding was wiped.
2. On a config with no ``providers`` key at all (a fresh install), the unknown-provider
   guard was skipped entirely, so a ref naming a provider that does not exist was
   stored unchallenged — a dead binding, accepted as valid.

The endpoint's deliberate NON-check is also pinned here: the model ID is not validated
against the discovered catalog, because a real provider that is slow to enumerate its
models must not have valid refs rejected.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web

from personalclaw.dashboard.handlers import model_registry as mr
from personalclaw.providers import use_cases as uc


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Isolate config.json + active_models.json under tmp_path."""
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(mr, "_sel_log", lambda *a, **k: None)
    return tmp_path


def _config(store, **payload) -> None:
    (store / "config.json").write_text(json.dumps(payload), encoding="utf-8")


async def _put(body: object, use_case: str = "chat") -> tuple[int, dict]:
    """Drive the real handler with a mocked request."""
    from aiohttp.test_utils import make_mocked_request

    req = make_mocked_request("PUT", f"/api/models/active/{use_case}")
    req.match_info["use_case"] = use_case

    async def _json():
        return body

    req.json = _json  # type: ignore[method-assign]
    resp: web.Response = await mr.api_models_active_set(req)
    return resp.status, json.loads(resp.text or "{}")


# ── 1. an omitted key is not an empty chain ───────────────────────────────────


@pytest.mark.asyncio
async def test_wrong_body_key_is_rejected_not_treated_as_clear(store):
    """The #48 repro: `{"providers": [...]}` used to wipe the binding and report ok."""
    _config(store, providers=[{"name": "Ollama"}])
    status, first = await _put({"models": ["Ollama:gemma4:12b"]})
    assert status == 200 and first["models"] == ["Ollama:gemma4:12b"]

    status, body = await _put({"providers": ["Ollama:gemma4:12b"]})
    assert status == 400, "a body without 'models' must not succeed"
    assert body["error"]["code"] == "models_required"
    # The error names what WAS sent, so a scripted caller can see its own mistake.
    assert body["error"]["received_keys"] == ["providers"]

    # And crucially: the existing binding survived the rejected call.
    status, after = await _put({"models": ["Ollama:gemma4:12b"]})
    assert after["models"] == ["Ollama:gemma4:12b"]


@pytest.mark.asyncio
async def test_explicit_empty_list_still_clears(store):
    """Clearing must remain possible — it just has to be explicit."""
    _config(store, providers=[{"name": "Ollama"}])
    await _put({"models": ["Ollama:gemma4:12b"]})
    status, body = await _put({"models": []})
    assert status == 200
    assert body["models"] == []


@pytest.mark.asyncio
async def test_empty_body_object_is_rejected(store):
    _config(store, providers=[{"name": "Ollama"}])
    status, body = await _put({})
    assert status == 400
    assert body["error"]["code"] == "models_required"


# ── 2. the unknown-provider guard must not be skipped on a fresh config ───────


def test_missing_providers_key_reads_as_none_configured_not_unknown(store):
    """A config with no `providers` key means "no providers configured", NOT "I can't
    tell". Returning None conflated the two, and every caller treats None as
    skip-validation — so on a fresh home a dead ref was accepted."""
    _config(store, dashboard={})
    known = uc._known_provider_names()
    assert known is not None, "a readable config must not read as unreadable"
    assert "native" in known  # the bundled set is the honest answer here


def test_unreadable_config_still_returns_none(store):
    """The distinction that must be preserved: a genuinely unreadable config skips
    validation rather than rejecting refs that are probably fine."""
    (store / "config.json").write_text("{ not json", encoding="utf-8")
    assert uc._known_provider_names() is None


def test_configured_provider_is_known(store):
    _config(store, providers=[{"name": "Ollama", "type": "ollama"}])
    known = uc._known_provider_names()
    assert known is not None and "Ollama" in known


@pytest.mark.asyncio
async def test_unknown_provider_rejected_on_a_fresh_config(store):
    """Before the fix this returned 200 and STORED the dead ref."""
    _config(store, dashboard={})
    status, body = await _put({"models": ["Nonexistent:foo"]})
    assert status == 400
    assert "Nonexistent" in body["error"]


# ── 3. the deliberate non-check, pinned so nobody "tightens" it ───────────────


@pytest.mark.asyncio
async def test_model_id_is_not_validated_against_the_catalog(store):
    """A real provider that is installed but slow to enumerate models must not have
    its valid refs rejected, so only the provider PREFIX is checked. This test exists
    to make that a decision rather than an oversight."""
    _config(store, providers=[{"name": "Ollama", "type": "ollama"}])
    status, body = await _put({"models": ["Ollama:a-model-not-yet-enumerated"]})
    assert status == 200
    assert body["models"] == ["Ollama:a-model-not-yet-enumerated"]
