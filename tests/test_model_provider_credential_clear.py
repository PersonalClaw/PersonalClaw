"""#3554 — emptying a stored credential field must clear it, not survive the PATCH.

``PUT /api/model-providers/{name}`` used to merge ``options`` with a plain
``dict.update()``. That can only ADD or OVERWRITE a key PRESENT in the incoming
``options``; it has no way to express "remove this key". The frontend (onboarding's
``ConfigureProvider``, ``EssentialsStep.tsx``) only ever sent non-empty values, so an
emptied ``api_key`` field was simply absent from the PATCH and the old key survived
forever — the field's own help text ("leave empty to fall back to the environment
variable") was true on the first save and false on every one after.

The fix gives the wire an explicit "clear this field" signal — a JSON ``null`` — that a
plain omitted key still does not mean: omitted still means "leave whatever is stored
alone" (an untouched field must not be wiped just because this call touched a sibling
field), and only an explicit ``null`` deletes. This file pins both directions on the
handler that owns the merge, plus the symmetric rule for ``POST`` (create): a ``null`` in
a fresh ``options`` payload stores nothing for that key rather than a literal JSON
``null`` on disk.
"""

from __future__ import annotations

import asyncio
import json

from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers import providers as H

_KEY = "sk-fixture-not-a-real-credential"


async def _coro(v):
    return v


def _req(method: str, path: str, body: dict, match_info: dict | None = None):
    req = make_mocked_request(method, path, match_info=match_info or {})
    req.json = lambda: _coro(body)
    return req


def _run(coro):
    return asyncio.run(coro)


def _seed_config(cfg, providers: list[dict]) -> None:
    cfg.write_text(json.dumps({"providers": providers}))


def _stored_options(cfg, name: str) -> dict:
    """The entry's options as a provider reads them. On disk a secret option is a reference into
    the credential store (``config.secret_refs``); the value is what the merge semantics are
    about, so that is what these assertions read."""
    from personalclaw.config.secret_refs import resolve

    data = json.loads(cfg.read_text())
    entry = next(p for p in data["providers"] if p["name"] == name)
    return resolve(entry.get("options", {}))


def test_update_with_explicit_null_clears_a_previously_stored_key(tmp_path, monkeypatch):
    """The fix. Fails before it (a bare `.update()` leaves `api_key` untouched)."""
    cfg = tmp_path / "config.json"
    _seed_config(
        cfg,
        [
            {
                "name": "anthropic",
                "type": "anthropic",
                "model": "",
                "options": {"api_key": _KEY, "default_model": "claude-sonnet-5"},
            }
        ],
    )
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg)
    monkeypatch.setattr(H, "_refresh_media_registries", lambda: None)

    req = _req(
        "PUT",
        "/api/model-providers/anthropic",
        {"options": {"api_key": None}},
        {"name": "anthropic"},
    )
    resp = _run(H.api_provider_update(req))
    assert resp.status == 200, resp.body

    options = _stored_options(cfg, "anthropic")
    assert "api_key" not in options, "an explicit null must delete the key, not store it"
    assert options["default_model"] == "claude-sonnet-5", "an untouched sibling field must survive"


def test_update_omitting_a_key_still_leaves_it_untouched(tmp_path, monkeypatch):
    """The side the fix must NOT change: absence still means "leave it alone".

    Making every absent key mean "clear it" would be the much-worse bug the issue
    explicitly warns against — a re-entry that only edits `default_model` must not wipe
    a working `api_key` it never mentioned.
    """
    cfg = tmp_path / "config.json"
    _seed_config(
        cfg,
        [
            {
                "name": "anthropic",
                "type": "anthropic",
                "model": "",
                "options": {"api_key": _KEY, "default_model": "claude-sonnet-5"},
            }
        ],
    )
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg)
    monkeypatch.setattr(H, "_refresh_media_registries", lambda: None)

    req = _req(
        "PUT",
        "/api/model-providers/anthropic",
        {"options": {"default_model": "claude-opus-4-8"}},
        {"name": "anthropic"},
    )
    resp = _run(H.api_provider_update(req))
    assert resp.status == 200, resp.body

    options = _stored_options(cfg, "anthropic")
    assert options["api_key"] == _KEY, "omitting a key must not clear it"
    assert options["default_model"] == "claude-opus-4-8"


def test_update_with_a_real_value_still_overwrites(tmp_path, monkeypatch):
    """A genuine rotation must still work — masking/clearing must not make a key sticky."""
    cfg = tmp_path / "config.json"
    _seed_config(
        cfg, [{"name": "anthropic", "type": "anthropic", "model": "", "options": {"api_key": _KEY}}]
    )
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg)
    monkeypatch.setattr(H, "_refresh_media_registries", lambda: None)

    req = _req(
        "PUT",
        "/api/model-providers/anthropic",
        {"options": {"api_key": "sk-fixture-rotated"}},
        {"name": "anthropic"},
    )
    resp = _run(H.api_provider_update(req))
    assert resp.status == 200, resp.body
    assert _stored_options(cfg, "anthropic")["api_key"] == "sk-fixture-rotated"


def test_create_strips_null_options_rather_than_storing_a_literal_null(tmp_path, monkeypatch):
    """On a first save there is nothing yet to clear — `null` there must mean "store
    nothing for it", never a literal JSON `null` on disk (which would read as configured
    but unusable, rather than as genuinely unset)."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"providers": []}))
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg)
    monkeypatch.setattr(H, "_refresh_media_registries", lambda: None)

    from personalclaw.llm.capabilities import Capability, ProviderCapability
    from personalclaw.llm.registry import get_default_registry

    reg = get_default_registry()
    if "anthropic" not in reg._capabilities:  # noqa: SLF001
        reg.register_type(
            ProviderCapability(
                type="anthropic",
                capabilities=frozenset({Capability.CHAT}),
                supports_streaming=True,
                supports_tools=True,
                supports_embeddings=False,
                supports_vision=True,
                max_context_tokens=0,
            ),
            lambda **kw: None,
        )

    req = _req(
        "POST",
        "/api/model-providers",
        {
            "name": "anthropic",
            "type": "anthropic",
            "model": "",
            "options": {"api_key": None, "default_model": "claude-sonnet-5"},
        },
    )
    resp = _run(H.api_provider_create(req))
    assert resp.status == 200, resp.body

    options = _stored_options(cfg, "anthropic")
    assert "api_key" not in options, "a null in a fresh create must not be persisted at all"
    assert options["default_model"] == "claude-sonnet-5"
