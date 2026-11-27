"""The open pages hear when the models PersonalClaw can use change, whoever changed them.

Measured before this was written: the header's model chip ("Set up a model", "Choose a model",
"N degraded") read the gateway on its own 20 s poll, which stretches to 80 s and 200 s as the page
goes idle, and nothing on the gateway said when a binding or a provider changed. So the chip kept
"Set up a model" for up to 20 s after a model was ready, and the "is ready" notice, which its read
evaluates, waited as long.

Now a change to a binding (``use_cases.save_active_models``: Settings › Models, a provider
connected, the local model a download binds) or to a provider instance (created, updated, deleted)
is announced (``providers.model_changes``), and the dashboard tells every open page with a
``refresh`` frame naming ``models``, which the chip reads again on. Its poll stays as the fallback.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from personalclaw.providers import instances, model_changes, use_cases


@pytest.fixture
def told():
    heard: list[str] = []

    def listener() -> None:
        heard.append("changed")

    model_changes.subscribe(listener)
    model_changes.subscribe(listener)  # idempotent: told once, not twice
    try:
        yield heard
    finally:
        model_changes.unsubscribe(listener)


def test_a_binding_saved_is_announced(told):
    """🔴 Red before: nothing was told."""
    use_cases.save_active_models({"chat": ["ollama:llama3"]})
    assert told == ["changed"]


def test_a_provider_instance_created_updated_and_deleted_is_announced(told):
    """🔴 Red before: nothing was told."""
    inst = instances.create_instance("fixture-multi", "Work", {"endpoint": "https://x.invalid"})
    instances.update_instance("fixture-multi", inst.id, config={"endpoint": "https://y.invalid"})
    assert instances.delete_instance("fixture-multi", inst.id)
    assert told == ["changed", "changed", "changed"]


def test_a_listener_that_fails_neither_undoes_the_save_nor_silences_the_rest():
    heard: list[str] = []

    def broken() -> None:
        raise RuntimeError("a listener's own fault")

    def after() -> None:
        heard.append("after")

    model_changes.subscribe(broken)
    model_changes.subscribe(after)
    try:
        use_cases.save_active_models({"chat": ["ollama:llama3"]})
    finally:
        model_changes.unsubscribe(broken)
        model_changes.unsubscribe(after)
    assert json.loads(use_cases.active_models_path().read_text())["chat"] == ["ollama:llama3"]
    assert heard == ["after"]


def _relay(app: web.Application):
    """The dashboard's own start and stop hooks for the relay, run alone: the rest of the
    gateway's hooks start real subsystems."""
    from personalclaw.dashboard.lifecycle_hooks import register_lifecycle_hooks

    register_lifecycle_hooks(app)
    start = next(h for h in app.on_startup if h.__name__ == "_model_changes_relay_startup")
    stop = next(h for h in app.on_cleanup if h.__name__ == "_model_changes_relay_shutdown")
    return start, stop


@pytest.mark.asyncio
async def test_the_gateway_tells_the_open_pages_until_it_stops():
    """🔴 Red before: no relay, so no page was told."""
    app = web.Application()
    app["state"] = MagicMock()
    start, stop = _relay(app)
    await start(app)
    try:
        use_cases.save_active_models({"chat": ["ollama:llama3"]})
        app["state"].push_refresh.assert_called_once_with("models")
    finally:
        await stop(app)
    app["state"].push_refresh.reset_mock()
    use_cases.save_active_models({"chat": []})
    app["state"].push_refresh.assert_not_called()


def test_a_provider_added_edited_and_removed_in_settings_is_announced(told, monkeypatch):
    """🔴 Red before (driven, dogfood): Settings › Models adds, edits and removes a provider through
    these routes, which wrote config.json and told nobody, so the other open pages waited for their
    poll. Each is announced once its write has landed, with no binding to change."""
    import asyncio
    import uuid

    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import providers as H
    from personalclaw.llm.branded_specs import BrandedProviderSpec
    from personalclaw.llm.registry import get_default_registry
    from personalclaw.sdk.provider_helpers import register_branded_app

    monkeypatch.setattr(H, "_refresh_media_registries", lambda: None)
    kind = "fixture-chip-openai"
    register_branded_app(
        BrandedProviderSpec(
            type=kind, protocol="openai", default_base_url="https://fixture-chip.invalid/v1"
        )
    )
    name = f"fx-{uuid.uuid4().hex[:8]}"

    async def _body(v):
        return v

    def call(handler, method: str, path: str, body: dict | None = None) -> None:
        req = make_mocked_request(
            method, path, match_info={} if method == "POST" else {"name": name}
        )
        req.json = lambda: _body(body or {})
        before = len(told)
        resp = asyncio.run(handler(req))
        assert resp.status == 200, resp.body
        assert len(told) > before, f"{method} {path} told nobody"

    try:
        created = {"name": name, "type": kind, "model": "", "options": {}}
        call(H.api_provider_create, "POST", "/api/model-providers", created)
        edited = {"options": {"endpoint": "https://fixture-chip.invalid/v2"}}
        call(H.api_provider_update, "PUT", f"/api/model-providers/{name}", edited)
        call(H.api_provider_delete, "DELETE", f"/api/model-providers/{name}")
    finally:
        get_default_registry().unregister_entry(name)
